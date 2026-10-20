"""W4: a write into `.claude/hooks/**` or `.claude/skills/**` (project or ~/.claude), or a
settings(.local).json edit that changes the `hooks` key, needs a REVIEW STAMP for its resulting
content - a sha256 recorded by some OTHER agent_id than the one making the write. See
REVIEW_STAMP.md (design note) for the full picture; this is the short version.

ALWAYS LOG-ONLY (see ALWAYS_LOG_ONLY below) - a user decision, 2026-09-28: build it, stage it, but
never let it deny anything until the user says otherwise, regardless of --log-only/CLAUDE_WRITE_GUARD.

Two stores, both under a GENERIC (non-project) `~/.claude/hooks/`:
  review_stamps.jsonl          - the reviewer's own write: {sha256, reviewer_agent_id, session_id,
                                  ts, verdict}. Self-reported - could lie.
  review_stamps_witness.jsonl  - written by THIS hook (via hooklib.output.log_hits, the same
                                  hardened append used for every other hit log here) whenever it
                                  sees a write TO review_stamps.jsonl: {sha256, verdict, agent_id,
                                  session_id} using the REAL agent_id/session_id off the incoming
                                  event, never the stamp content's own self-reported field. CHECK
                                  trusts a witness record over the stamp's self-report whenever one
                                  exists for that hash.

Two shapes, matching every other rule module here:
  evaluate_tool(event)  - Write/Edit/MultiEdit/NotebookEdit -> [hit, ...]
  evaluate_shell(event) - Bash/PowerShell (Copy-Item/cp, Move-Item/mv only - see REVIEW_STAMP.md's
                          gaps for redirects) -> [hit, ...]
Both accept an optional `base_dir=` (tests only) overriding paths.home_dir() for where the two
stores and the guarded `.claude/hooks|skills` trees resolve, so self-test never touches the real
~/.claude.
"""
import hashlib
import json
import os

from hooklib import output
from hooklib import paths
from hooklib import shell
from hooklib.output import excerpt
from rules.write import w3_outside_root as w3

RULE_ID = "W4"
MSG = {RULE_ID: "A write into .claude/hooks or .claude/skills (or a settings.json hooks-key "
                "change) has no review stamp from a DIFFERENT agent for its resulting content. "
                "Log-only for now (2026-09-28 user decision) - nothing is denied."}

# Hard-coded, not read from --log-only/CLAUDE_WRITE_GUARD: this rule NEVER denies until the user
# decides otherwise, independent of whatever the rest of the hook is doing that week.
ALWAYS_LOG_ONLY = True

GUARDED_SUBDIRS = ("hooks", "skills")
SETTINGS_FILENAMES = ("settings.json", "settings.local.json")
STAMP_FILENAME = "review_stamps.jsonl"
WITNESS_FILENAME = "review_stamps_witness.jsonl"
COPY_VERBS = {"cp", "copy-item", "mv", "move-item"}
PASS_VERDICT = "pass"

try:
    HITS_LOG_BASENAME = os.path.basename(output.HITS_PATH) if output.HITS_PATH else ""
except Exception:
    HITS_LOG_BASENAME = ""

# ------------------------------------------------------------------ stores


def resolve_stamp_paths(base_dir=None):
    """(stamp_path, witness_path, hits_log_path) under `<base_dir or paths.home_dir()>/.claude/
    hooks/` - a generic, non-project-specific location so the same files answer for every project.
    `hits_log_path` mirrors output.HITS_PATH's own basename (not its home - see HITS_LOG_BASENAME)
    so a self-test using a fake `base_dir` still gets a consistent, matching path to exempt."""
    base = base_dir if base_dir is not None else paths.home_dir()
    if not base:
        return None, None, None
    hooks_dir = os.path.join(base, ".claude", "hooks")
    return (os.path.join(hooks_dir, STAMP_FILENAME), os.path.join(hooks_dir, WITNESS_FILENAME),
            os.path.join(hooks_dir, HITS_LOG_BASENAME) if HITS_LOG_BASENAME else None)


def is_store_path(resolved, store_path):
    """True if `resolved` IS `store_path` exactly (full resolved path, never a basename match - a
    same-named file elsewhere, e.g. a project's OWN .claude/hooks/review_stamps.jsonl when the
    real store is under ~/.claude, must never be silently exempted - 2026-09-28 review)."""
    if resolved is None or not store_path:
        return False
    return os.path.normcase(resolved) == os.path.normcase(os.path.normpath(store_path))


def is_hits_log_path(resolved, hits_log_path):
    """True for the hit log itself or one of its generated rotations (.1, .dropped) - by FULL path,
    not basename. These sit physically inside a guarded ~/.claude/hooks tree but are the hook's own
    bookkeeping, not a write this rule judges."""
    if resolved is None or not hits_log_path:
        return False
    resolved_n = os.path.normcase(resolved)
    return any(resolved_n == os.path.normcase(hits_log_path + suf)
               for suf in ("", ".1", output.DROPPED_SUFFIX))


def _load_jsonl(path):
    """[{...}, ...], skipping any line that is not valid JSON - a malformed line should not happen,
    but a reader here should never fail the whole read over one (same policy as hits_summary.py)."""
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _latest_matching(records, sha256):
    match = None
    for r in records:
        if r.get("sha256") == sha256:
            match = r  # keep the LAST (most recent) matching record
    return match


def is_validly_reviewed(sha256, writer_agent_id, witness_path):
    """True only if a WITNESS record - the hook's OWN hook-observed corroboration, never the
    stamp's self-reported `reviewer_agent_id` - shows a verdict=="pass" review from an agent_id
    other than the writer's own.

    Second security fix (2026-09-28 re-review, false clearance): the first version fell back to
    trusting the stamp's own self-reported field whenever NO witness existed for that hash. That
    fallback let one agent clear ANY hit in one extra step, with zero hits: forge a stamp line
    claiming `reviewer_agent_id: "someone-else"` via any route this hook does not witness (a
    redirect into the stamp store, or - before this same fix - the witness store directly), then
    make the real write. A self-report alone proves nothing about who actually wrote it, so an
    UNWITNESSED stamp now counts as unreviewed, same as no stamp at all - it costs nothing during
    the log-only bootstrap and closes the hole (see REVIEW_STAMP.md's KNOWN GAPS for what remains:
    two cooperating sessions, where one writes the stamp and a DIFFERENT one - which the witness
    correctly records - writes the guarded file, still looks like a legitimate review)."""
    w = _latest_matching(_load_jsonl(witness_path), sha256)
    if w is None or w.get("verdict") != PASS_VERDICT:
        return False
    return (w.get("agent_id") or None) != (writer_agent_id or None)


def capture_witness(event, resulting_bytes, stamp_path, witness_path):
    """Appends a witness row for every NEW stamp line `resulting_bytes` adds (the caller has
    already confirmed this write's target IS the stamp store, via is_store_path), using the REAL
    agent_id/session_id off `event` - never the stamp content's own field. This is the one place
    any rule module here writes rather than only reads, and it is the ONLY route by which a stamp
    is ever witnessed: writing the stamp store any other way (a shell redirect, or writing the
    witness store directly) is itself a hit - see evaluate_tool/evaluate_shell."""
    if resulting_bytes is None:
        return
    existing = set()
    try:
        with open(stamp_path, encoding="utf-8") as fh:
            existing = set(ln.strip() for ln in fh if ln.strip())
    except OSError:
        pass
    new_rows = []
    for line in resulting_bytes.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line in existing:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        sha = rec.get("sha256")
        if not sha:
            continue
        new_rows.append({"sha256": sha, "verdict": rec.get("verdict"),
                          "agent_id": event.get("agent_id"), "session_id": event.get("session_id")})
    if new_rows:
        output.log_hits(new_rows, hits_path=witness_path)


# ------------------------------------------------------------------ guarded-path detection


def is_guarded_path(resolved, root, home):
    """True if `resolved` falls under <root|home>/.claude/hooks or .claude/skills, excluding any
    __pycache__ segment. The stamp/witness/hits-log stores are NOT filtered out here (a 2026-09-28
    fix - see is_store_path/is_hits_log_path) - they used to be exempted by BASENAME right here,
    which meant a same-named decoy file (or the real store reached via a route this function never
    checked, like a shell redirect) silently bypassed review entirely. Callers now check the real
    stores' FULL resolved paths explicitly, before falling through to this generic guarded check."""
    if resolved is None:
        return False
    if "__pycache__" in os.path.normcase(resolved).split(os.sep):
        return False
    for base in (root, home):
        if not base:
            continue
        claude = os.path.normpath(os.path.join(base, ".claude"))
        for name in GUARDED_SUBDIRS:
            guarded = os.path.normpath(os.path.join(claude, name))
            if paths.is_inside(guarded, resolved) or os.path.normcase(resolved) == os.path.normcase(guarded):
                return True
    return False


def _settings_dir_match(resolved, root, home):
    base_name = os.path.basename(resolved).lower()
    if base_name not in SETTINGS_FILENAMES:
        return False
    parent = os.path.normcase(os.path.dirname(resolved))
    for base in (root, home):
        if not base:
            continue
        claude = os.path.normcase(os.path.normpath(os.path.join(base, ".claude")))
        if parent == claude:
            return True
    return False


def is_settings_hooks_change(resolved, root, home, current_bytes, resulting_bytes):
    """True if `resolved` is a settings(.local).json directly under a guarded .claude/ AND its
    `hooks` key differs between current and resulting content. A parse failure on EITHER side is
    treated as guarded too (can't prove they match) - harmless since this rule never denies."""
    if not _settings_dir_match(resolved, root, home):
        return False
    try:
        current_json = json.loads((current_bytes or b"{}").decode("utf-8", "replace"))
        resulting_json = json.loads((resulting_bytes or b"{}").decode("utf-8", "replace"))
    except ValueError:
        return True
    return current_json.get("hooks") != resulting_json.get("hooks")


# ------------------------------------------------------------------ resulting content


def _read_current_bytes(path):
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def resulting_content_tool(tool, ti, current_bytes):
    """The file's bytes AFTER this write, or None if it cannot be determined (Edit/MultiEdit with
    no readable current file). Edit/MultiEdit apply every {old_string, new_string} pair, in order,
    to the CURRENT on-disk text - so a stamp is checked against the result, not the pre-edit content
    or the diff alone."""
    if tool == "Write":
        content = ti.get("content")
        return content.encode("utf-8") if content is not None else None
    if tool in ("Edit", "MultiEdit"):
        if current_bytes is None:
            return None
        text = current_bytes.decode("utf-8", "replace")
        edits = ti.get("edits") if tool == "MultiEdit" else [ti]
        for e in edits or []:
            old, new = e.get("old_string"), e.get("new_string")
            if old is None or new is None or old == "":
                continue
            text = text.replace(old, new, 1)
        return text.encode("utf-8")
    if tool == "NotebookEdit":
        # best-effort, documented in REVIEW_STAMP.md's gaps: hashes the new cell source alone, not
        # a byte-perfect notebook JSON reconstruction - .ipynb is not expected under hooks/skills.
        src = ti.get("new_source")
        return src.encode("utf-8") if src is not None else None
    return None


def shell_copy_source(seg, powershell, verb):
    """The source argument for a positional `cp`/`mv`/`Copy-Item`/`Move-Item` in `seg`, or None."""
    toks = shell.shell_tokens(seg, powershell)
    if not toks:
        return None
    positionals = [t for t in toks[1:] if not t.startswith("-")]
    if verb.lower() in COPY_VERBS and len(positionals) >= 2:
        return positionals[0]
    return None


# ------------------------------------------------------------------ evaluate


def evaluate_tool(event, base_dir=None):
    """[hit, ...] for a Write/Edit/MultiEdit/NotebookEdit event - each hit a dict with `target`,
    `resolved`, `sha256` (None if unresolvable) and `reason`. A write TO the witness store is
    always a hit (tampering) - the hook is the only legitimate writer, via output.log_hits, never a
    tool call the hook could see and exempt. A write TO the stamp store via a TOOL call is the
    legitimate reviewer path: exempt from being a hit itself, and captures a witness (see
    capture_witness) for every new pass-verdict line."""
    tool = event.get("tool_name") or ""
    if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return []
    ti = event.get("tool_input") or {}
    target = ti.get("notebook_path") if tool == "NotebookEdit" else ti.get("file_path")
    if not target:
        return []
    resolved = paths.resolve(target, event)
    stamp_path, witness_path, hits_log_path = resolve_stamp_paths(base_dir)
    root = paths.project_root(event)
    home = base_dir if base_dir is not None else paths.home_dir()
    current_bytes = _read_current_bytes(resolved)
    resulting = resulting_content_tool(tool, ti, current_bytes)
    if is_store_path(resolved, witness_path):
        return [{"target": target, "resolved": resolved, "sha256": None,
                  "reason": "witness store written directly - tampering"}]
    if is_store_path(resolved, stamp_path):
        capture_witness(event, resulting, stamp_path, witness_path)
        return []
    if is_hits_log_path(resolved, hits_log_path):
        return []
    guarded = is_guarded_path(resolved, root, home) or \
        is_settings_hooks_change(resolved, root, home, current_bytes, resulting)
    if not guarded:
        return []  # not a guarded path at all - no hashing, no store reads (hot-path cost is zero)
    if resulting is None:
        return [{"target": target, "resolved": resolved, "sha256": None,
                  "reason": "resulting content not resolvable"}]
    sha = hashlib.sha256(resulting).hexdigest()
    if is_validly_reviewed(sha, event.get("agent_id"), witness_path):
        return []
    return [{"target": target, "resolved": resolved, "sha256": sha,
              "reason": "no review stamp from a different agent"}]


def evaluate_shell(event, base_dir=None):
    """[hit, ...] for a Bash/PowerShell event. A shell write to EITHER store is always a hit - the
    witness store because only the hook itself may write it, the stamp store because a shell write
    (a redirect included) is never parsed for a witness, so it could never be corroborated anyway
    (see REVIEW_STAMP.md: stamps must go through Write/Edit). Copy-Item/cp and Move-Item/mv landing
    in an otherwise-guarded path are hash-checked against the SOURCE's bytes; a redirect into one
    can never be hash-matched (content is not visible in the command line), so it always fires."""
    tool = event.get("tool_name") or ""
    if tool not in ("Bash", "PowerShell"):
        return []
    cmd = (event.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return []
    powershell = tool == "PowerShell"
    stamp_path, witness_path, hits_log_path = resolve_stamp_paths(base_dir)
    root = paths.project_root(event)
    home = base_dir if base_dir is not None else paths.home_dir()
    hits = []
    for seg, verb, raw in w3.find_write_targets(cmd, powershell):
        if shell.VAR_OR_SUBST.search(raw):
            continue  # not statically resolvable - out of scope for this rule (bootstrap: skip)
        resolved = paths.resolve(raw, event)
        if is_store_path(resolved, witness_path):
            hits.append({"segment": seg, "verb": verb, "target": raw, "resolved": resolved,
                         "sha256": None, "reason": "witness store written directly - tampering"})
            continue
        if is_store_path(resolved, stamp_path):
            hits.append({"segment": seg, "verb": verb, "target": raw, "resolved": resolved,
                         "sha256": None, "reason": "stamp written by shell - not witnessable"})
            continue
        if is_hits_log_path(resolved, hits_log_path):
            continue
        if not is_guarded_path(resolved, root, home):
            continue  # zero hashing cost for every ordinary shell write
        resulting = None
        if verb.lower() in COPY_VERBS:
            src = shell_copy_source(seg, powershell, verb)
            if src:
                resulting = _read_current_bytes(paths.resolve(src, event))
        if resulting is None:
            hits.append({"segment": seg, "verb": verb, "target": raw, "resolved": resolved,
                         "sha256": None, "reason": "resulting content not resolvable"})
            continue
        sha = hashlib.sha256(resulting).hexdigest()
        if is_validly_reviewed(sha, event.get("agent_id"), witness_path):
            continue
        hits.append({"segment": seg, "verb": verb, "target": raw, "resolved": resolved,
                     "sha256": sha, "reason": "no review stamp from a different agent"})
    return hits


# ------------------------------------------------------------------ decide / log rows


def log_rows(event, tool_hits, shell_hits, ms):
    root = paths.project_root(event)
    cwd = event.get("cwd")
    rows = []
    for h in tool_hits:
        rows.append({"rule": RULE_ID, "tool": event.get("tool_name"), "root": root, "cwd": cwd,
                      "target": h["target"],
                      "sample": [excerpt("%s -> sha256=%s (%s)" % (h["target"], h["sha256"], h["reason"]))],
                      "session": event.get("session_id"), "ms": ms, "msg": MSG[RULE_ID]})
    for h in shell_hits:
        rows.append({"rule": RULE_ID, "tool": event.get("tool_name"), "root": root, "cwd": cwd,
                      "target": h["target"],
                      "sample": [excerpt("%s %s -> sha256=%s (%s)"
                                         % (h["verb"], h["target"], h["sha256"], h["reason"]))],
                      "session": event.get("session_id"), "ms": ms, "msg": MSG[RULE_ID]})
    return rows


def decide(event, tool_hits, shell_hits, enforce):
    """ALWAYS None - see ALWAYS_LOG_ONLY. `enforce` is accepted only to match every other rule
    module's decide(event, tool_hits, shell_hits, enforce) signature; it is deliberately unread."""
    del event, tool_hits, shell_hits, enforce
    if ALWAYS_LOG_ONLY:
        return None
    return None  # unreachable until a future change flips the constant above


# ------------------------------------------------------------------ self-test

def run_self_test():
    import tempfile
    import time as _time

    failures = []
    with tempfile.TemporaryDirectory() as raw_root:
        # a temp dir's path may itself resolve through a Windows 8.3 short-name segment (this
        # machine's own %TEMP% does) - normalize ONCE, up front, to the same long form
        # paths.resolve() would expand a target to, so `root` (standing in for a project root) and
        # every resolved target agree. A REAL project root never lives under %TEMP%, so this is a
        # self-test environment artifact, not something evaluate_tool/evaluate_shell need to handle
        # themselves - paths.project_root() deliberately does not expand short names on its own.
        root = paths.expand_short_names(os.path.normpath(raw_root))
        home = os.path.join(root, "home")
        os.makedirs(os.path.join(home, ".claude", "hooks"), exist_ok=True)
        os.makedirs(os.path.join(root, ".claude", "hooks"), exist_ok=True)
        stamp_path, witness_path, hits_log_path = resolve_stamp_paths(home)

        def write_stamp(sha, reviewer_agent_id, verdict=PASS_VERDICT, session_id="s-rev"):
            line = json.dumps({"sha256": sha, "reviewer_agent_id": reviewer_agent_id,
                                "session_id": session_id, "ts": "2026-09-28T00:00:00Z",
                                "verdict": verdict})
            with open(stamp_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

        def write_witness(sha, agent_id, verdict=PASS_VERDICT, session_id="s-real"):
            output.log_hits([{"sha256": sha, "verdict": verdict, "agent_id": agent_id,
                               "session_id": session_id}], hits_path=witness_path)

        # 1: guarded write, no stamp at all -> must fire
        target1 = os.path.join(root, ".claude", "hooks", "new_hook.py")
        ev1 = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
               "tool_input": {"file_path": target1, "content": "print(1)\n"}}
        hits1 = evaluate_tool(ev1, base_dir=home)
        if not hits1:
            failures.append("W4: unstamped guarded write must fire")

        # 2: same content, stamped AND WITNESSED for a DIFFERENT agent -> must not fire
        sha1 = hashlib.sha256(b"print(1)\n").hexdigest()
        write_stamp(sha1, "reviewerB")
        write_witness(sha1, "reviewerB")
        hits2 = evaluate_tool(ev1, base_dir=home)
        if hits2:
            failures.append("W4: guarded write with a witnessed different-agent stamp must not fire")

        # 2b: a stamp with NO witness at all - even claiming a different agent - must still fire.
        # This is the exact hole the 2026-09-28 re-review found: the first version of CHECK fell
        # back to trusting an unwitnessed stamp's OWN self-reported reviewer_agent_id, so one agent
        # could clear any hit in one extra step by forging a stamp line through any route the hook
        # never witnesses (see the tampering/redirect cases below) - zero hits, one self-report.
        target2b = os.path.join(root, ".claude", "hooks", "unwitnessed.py")
        ev2b = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
                "tool_input": {"file_path": target2b, "content": "print('2b')\n"}}
        write_stamp(hashlib.sha256(b"print('2b')\n").hexdigest(), "reviewerB")  # no matching witness
        if not evaluate_tool(ev2b, base_dir=home):
            failures.append("W4: an UNWITNESSED stamp (self-report only) must still fire")

        # 3: same content, stamped by the SAME agent as the writer (self-review) -> must fire
        target3 = os.path.join(root, ".claude", "hooks", "self_reviewed.py")
        ev3 = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
               "tool_input": {"file_path": target3, "content": "print(3)\n"}}
        sha3 = hashlib.sha256(b"print(3)\n").hexdigest()
        write_stamp(sha3, "writerA")
        if not evaluate_tool(ev3, base_dir=home):
            failures.append("W4: a stamp from the WRITER's own agent_id must still fire (self-review)")

        # 4a: Edit hashes the RESULT, not the pre-edit content - stamp the OLD content -> must fire
        edit_target = os.path.join(root, ".claude", "hooks", "existing.py")
        with open(edit_target, "w", encoding="utf-8", newline="") as fh:
            fh.write("value = 1\n")
        write_stamp(hashlib.sha256(b"value = 1\n").hexdigest(), "reviewerB")
        ev4 = {"tool_name": "Edit", "cwd": root, "agent_id": "writerA",
               "tool_input": {"file_path": edit_target, "old_string": "value = 1",
                               "new_string": "value = 2"}}
        if not evaluate_tool(ev4, base_dir=home):
            failures.append("W4: Edit must hash the RESULT, not the pre-edit content")

        # 4b: stamp AND witness the ACTUAL result -> must not fire
        sha_result = hashlib.sha256(b"value = 2\n").hexdigest()
        write_stamp(sha_result, "reviewerB")
        write_witness(sha_result, "reviewerB")
        if evaluate_tool(ev4, base_dir=home):
            failures.append("W4: Edit with the RESULT's hash witnessed must not fire")

        # 5a: shell copy into a guarded path, source unstamped -> must fire
        src_path = os.path.join(root, "payload.py")
        with open(src_path, "w", encoding="utf-8", newline="") as fh:
            fh.write("payload = True\n")
        cp_cmd = "cp \"" + src_path + "\" \"" + os.path.join(root, ".claude", "hooks", "dest.py") + "\""
        ev5 = {"tool_name": "Bash", "cwd": root, "agent_id": "writerA", "tool_input": {"command": cp_cmd}}
        if not evaluate_shell(ev5, base_dir=home):
            failures.append("W4: unstamped shell copy into a guarded path must fire")

        # 5b: same source, stamped AND witnessed by a different agent -> must not fire
        sha_payload = hashlib.sha256(b"payload = True\n").hexdigest()
        write_stamp(sha_payload, "reviewerB")
        write_witness(sha_payload, "reviewerB")
        if evaluate_shell(ev5, base_dir=home):
            failures.append("W4: shell copy with the SOURCE's hash witnessed must not fire")

        # 6: write outside every guarded path -> must not fire regardless of stamps
        ev6 = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
               "tool_input": {"file_path": os.path.join(root, "src", "ordinary.cpp"),
                               "content": "int x;\n"}}
        if evaluate_tool(ev6, base_dir=home):
            failures.append("W4: a write outside every guarded path must never fire")

        # 7: write TO the stamp store itself -> exempt, and captures a witness for its new line
        new_stamp_sha = hashlib.sha256(b"witness-me\n").hexdigest()
        stamp_line = json.dumps({"sha256": new_stamp_sha, "reviewer_agent_id": "reviewerZ",
                                  "session_id": "s1", "ts": "2026-09-28T00:00:00Z",
                                  "verdict": PASS_VERDICT})
        with open(stamp_path, encoding="utf-8") as fh:
            prior = fh.read()
        ev7 = {"tool_name": "Write", "cwd": root, "agent_id": "reviewerZ", "session_id": "s1",
               "tool_input": {"file_path": stamp_path, "content": prior + stamp_line + "\n"}}
        if evaluate_tool(ev7, base_dir=home):
            failures.append("W4: a write to the stamp store itself must be exempt")
        witnesses = _load_jsonl(witness_path)
        if not any(w.get("sha256") == new_stamp_sha and w.get("agent_id") == "reviewerZ"
                   for w in witnesses):
            failures.append("W4: a stamp-store write did not capture a witness with the REAL agent_id")

        # 8: witness precedence over a forged self-report - witness says the REAL author is the
        # writer itself (self-review), even though the stamp's own field lies and claims someone
        # else - CHECK must still fire (trusts the witness, not the forgeable self-report)
        forged_sha = hashlib.sha256(b"forged\n").hexdigest()
        write_stamp(forged_sha, "someone-else")  # self-reported lie
        write_witness(forged_sha, "writerA")     # real author, hook-observed: same as the writer
        ev8 = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
               "tool_input": {"file_path": os.path.join(root, ".claude", "hooks", "forged.py"),
                               "content": "forged\n"}}
        if not evaluate_tool(ev8, base_dir=home):
            failures.append("W4: a witness-contradicted self-report must still fire (anti-forgery)")

        # 9: (a) a plain Write/Edit/MultiEdit TARGETING THE WITNESS STORE ITSELF is always a hit -
        # tampering - even carrying a forged row that claims some other agent reviewed it. The
        # hook is the ONLY legitimate writer of this file (via output.log_hits, never a tool call
        # this hook could see and exempt), so any tool-observed write to it is inherently forgery.
        forged_witness_line = json.dumps({"sha256": "deadbeef", "verdict": PASS_VERDICT,
                                           "agent_id": "someone-else", "session_id": "s-fake"})
        ev9 = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
               "tool_input": {"file_path": witness_path, "content": forged_witness_line + "\n"}}
        hits9 = evaluate_tool(ev9, base_dir=home)
        if not hits9:
            failures.append("W4: a direct write to the witness store must fire (tampering)")
        elif hits9[0]["reason"] != "witness store written directly - tampering":
            failures.append("W4: a witness-store write fired with the wrong reason: %r" % hits9[0]["reason"])

        # (b) a shell redirect into either store must fire too - a redirect's content is never
        # parsed for a witness, so the stamp store can never be corroborated that way, and the
        # witness store is never legitimately shell-written at all.
        redirect_stamp_cmd = "echo hi >> \"" + stamp_path + "\""
        ev_rs = {"tool_name": "Bash", "cwd": root, "agent_id": "writerA",
                 "tool_input": {"command": redirect_stamp_cmd}}
        hits_rs = evaluate_shell(ev_rs, base_dir=home)
        if not hits_rs:
            failures.append("W4: a shell redirect into the stamp store must fire")
        elif hits_rs[0]["reason"] != "stamp written by shell - not witnessable":
            failures.append("W4: a shell-redirect-to-stamp hit fired with the wrong reason: %r"
                             % hits_rs[0]["reason"])

        redirect_witness_cmd = "echo hi >> \"" + witness_path + "\""
        ev_rw = {"tool_name": "Bash", "cwd": root, "agent_id": "writerA",
                 "tool_input": {"command": redirect_witness_cmd}}
        hits_rw = evaluate_shell(ev_rw, base_dir=home)
        if not hits_rw:
            failures.append("W4: a shell redirect into the witness store must fire")
        elif hits_rw[0]["reason"] != "witness store written directly - tampering":
            failures.append("W4: a shell-redirect-to-witness hit fired with the wrong reason: %r"
                             % hits_rw[0]["reason"])

        # the basename trick: a file named EXACTLY like a store, but NOT at the real store's path
        # (e.g. the PROJECT's own .claude/hooks/, when the real stores are under ~/.claude), must
        # never be silently exempted - it is an ordinary guarded file like any other, needing its
        # own review (2026-09-28 review: this used to be a basename-only match).
        decoy_stamp = os.path.join(root, ".claude", "hooks", STAMP_FILENAME)
        ev_decoy_stamp = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
                           "tool_input": {"file_path": decoy_stamp, "content": "not a real stamp\n"}}
        hits_decoy_stamp = evaluate_tool(ev_decoy_stamp, base_dir=home)
        if not hits_decoy_stamp:
            failures.append("W4: a same-named decoy stamp file elsewhere must not be exempt")
        elif hits_decoy_stamp[0]["reason"] != "no review stamp from a different agent":
            failures.append("W4: a decoy stamp file got a store-specific reason instead of an ordinary one: %r"
                             % hits_decoy_stamp[0]["reason"])

        decoy_witness = os.path.join(root, ".claude", "hooks", WITNESS_FILENAME)
        ev_decoy_witness = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
                             "tool_input": {"file_path": decoy_witness, "content": "not real either\n"}}
        hits_decoy_witness = evaluate_tool(ev_decoy_witness, base_dir=home)
        if not hits_decoy_witness:
            failures.append("W4: a same-named decoy witness file elsewhere must not be exempt")
        elif hits_decoy_witness[0]["reason"] != "no review stamp from a different agent":
            failures.append("W4: a decoy witness file got a store-specific reason instead of an ordinary one: %r"
                             % hits_decoy_witness[0]["reason"])

        # 9a: settings.json hooks-key change -> must fire
        settings_path = os.path.join(root, ".claude", "settings.json")
        with open(settings_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps({"hooks": {"PreToolUse": []}, "other": 1}))
        ev9a = {"tool_name": "Edit", "cwd": root, "agent_id": "writerA",
                "tool_input": {"file_path": settings_path,
                                "old_string": '"hooks": {"PreToolUse": []}',
                                "new_string": '"hooks": {"PreToolUse": ["x"]}'}}
        if not evaluate_tool(ev9a, base_dir=home):
            failures.append("W4: a settings.json hooks-key change must fire")

        # 9b: settings.json edit that leaves "hooks" untouched -> must not fire
        ev9b = {"tool_name": "Edit", "cwd": root, "agent_id": "writerA",
                "tool_input": {"file_path": settings_path, "old_string": '"other": 1',
                                "new_string": '"other": 2'}}
        if evaluate_tool(ev9b, base_dir=home):
            failures.append("W4: a settings.json edit that does not touch 'hooks' must not fire")

        # 10: __pycache__ segment is exempt even though it is physically under a guarded tree
        ev10 = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
                "tool_input": {"file_path": os.path.join(root, ".claude", "hooks", "__pycache__",
                                                          "x.pyc"), "content": "binary\n"}}
        if evaluate_tool(ev10, base_dir=home):
            failures.append("W4: a __pycache__ path must be exempt")

        # 11: the hit log's own FULL path is exempt (matched against `home`, where it really lives
        # - not `root`, and not by basename: see the decoy cases above for why that distinction
        # matters) even though it sits inside the same guarded .claude/hooks tree.
        ev11 = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
                "tool_input": {"file_path": hits_log_path or os.path.join(
                    home, ".claude", "hooks", "write_guard_hits.jsonl"), "content": "{}\n"}}
        if evaluate_tool(ev11, base_dir=home):
            failures.append("W4: the hit log's own path must be exempt")

        # 11b: a decoy with the hit log's basename, elsewhere, must NOT be exempt (same full-path
        # principle as the stamp/witness decoys above).
        decoy_hits_log = os.path.join(root, ".claude", "hooks",
                                       HITS_LOG_BASENAME or "write_guard_hits.jsonl")
        ev11b = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
                 "tool_input": {"file_path": decoy_hits_log, "content": "{}\n"}}
        if not evaluate_tool(ev11b, base_dir=home):
            failures.append("W4: a same-named decoy hit-log file elsewhere must not be exempt")

        # 12: ALWAYS_LOG_ONLY - decide() must return None regardless of `enforce`
        if decide(ev1, hits1, [], enforce=True) is not None:
            failures.append("W4: decide() denied even though ALWAYS_LOG_ONLY is set")
        if decide(ev1, hits1, [], enforce=False) is not None:
            failures.append("W4: decide() denied under enforce=False too (sanity)")

        # 12b: 8.3 root fix (2026-09-28 review, same gap as W3): paths.project_root() now expands
        # short (8.3) name segments before this rule compares a root against a target - proven with
        # a REAL short name from the OS (this machine's own %TEMP% already returns one, per
        # `raw_root` below - GetShortPathNameW just confirms/derives it), given as `cwd` and
        # deliberately NOT pre-expanded here, unlike `root` (used everywhere else in this test,
        # already expanded once up top for convenience). Before the fix this write - genuinely
        # inside a guarded .claude/hooks tree - would have looked like it fell outside project_root()
        # entirely and never even reached the guarded check, so it would wrongly NOT have fired.
        import ctypes as _ctypes

        def _short_form(path):
            try:
                buf = _ctypes.create_unicode_buffer(4096)
                n = _ctypes.windll.kernel32.GetShortPathNameW(path, buf, 4096)
                return buf.value if n and n < 4096 else None
            except Exception:
                return None

        short_root = _short_form(raw_root)
        if short_root and short_root.lower() != root.lower():
            target_83 = os.path.join(root, ".claude", "hooks", "via_short_root.py")
            ev83 = {"tool_name": "Write", "cwd": short_root, "agent_id": "writerA",
                    "tool_input": {"file_path": target_83, "content": "print('83')\n"}}
            if not evaluate_tool(ev83, base_dir=home):
                failures.append(
                    "W4: a guarded write must still fire when cwd/root is 8.3 SHORT form (root not expanded?)")
        # else: short-name creation disabled on this volume - not provable either way, not a failure

        # 12c: junction/symlink fix (2026-09-28 review): an NTFS junction (`mklink /J`, no admin
        # needed) makes a write through a path that LOOKS unguarded land inside a guarded tree - the
        # exact scenario reported. A REAL junction here, not a hand-typed guess, proves
        # paths.resolve()'s new resolve_real_prefix step actually follows it: `looks_unguarded\`
        # (lexically nowhere near .claude/hooks) is junctioned straight AT the real .claude/hooks
        # directory, so a write through it must be judged exactly as if it targeted .claude/hooks
        # directly - unstamped, it must fire.
        import subprocess as _subprocess

        junction_dir = os.path.join(root, "looks_unguarded")
        guarded_dir = os.path.join(root, ".claude", "hooks")
        _mk = _subprocess.run(["cmd", "/c", "mklink", "/J", junction_dir, guarded_dir],
                               capture_output=True, text=True)
        if _mk.returncode == 0:
            target_junction = os.path.join(junction_dir, "via_junction.py")
            ev_junction = {"tool_name": "Write", "cwd": root, "agent_id": "writerA",
                           "tool_input": {"file_path": target_junction, "content": "print('j')\n"}}
            if not evaluate_tool(ev_junction, base_dir=home):
                failures.append(
                    "W4: a write through a junction into .claude/hooks must fire (junction not resolved?)")
        # else: mklink unavailable in this environment - not provable either way, not a failure

        # 13: latency - a non-guarded write must do ZERO hashing (guarded check short-circuits
        # before hashlib.sha256 is ever called); measured below and also asserted structurally here
        # by monkeypatching hashlib.sha256 to raise if it is ever reached for an ordinary write.
        real_sha256 = hashlib.sha256
        hashlib.sha256 = lambda *a, **k: (_ for _ in ()).throw(AssertionError("hashed a non-guarded write"))
        try:
            evaluate_tool(ev6, base_dir=home)
        except AssertionError as e:
            failures.append("W4: " + str(e))
        finally:
            hashlib.sha256 = real_sha256

        n_iters = 500
        t0 = _time.time()
        for _ in range(n_iters):
            evaluate_tool(ev6, base_dir=home)
        non_guarded_ms = (_time.time() - t0) * 1000 / n_iters
        t0 = _time.time()
        for _ in range(n_iters):
            evaluate_tool(ev1, base_dir=home)
        guarded_ms = (_time.time() - t0) * 1000 / n_iters
        print("W4 latency: non-guarded %.4fms/call, guarded %.4fms/call (n=%d)"
              % (non_guarded_ms, guarded_ms, n_iters))

    return failures  # write_guard.py's self_test() prints any FAIL lines - not duplicated here


# N_CASES is derived from run_self_test's `failures.append(` call sites (the hand-kept 33 drifted from
# the real 32), and LAZILY (PEP 562): `import inspect` costs ~15-20 ms, and this module is imported on
# every hook call while the count is only read by write_guard.py's self-test.
def __getattr__(name):
    if name == "N_CASES":
        import inspect
        return inspect.getsource(run_self_test).count("failures.append(")
    raise AttributeError(name)
