"""Global PreToolUse hook: every write - and every delete, which is a write to the filesystem too -
stays inside the calling session's project root or ~/.claude (W3); and every delete, wherever it
lands, is expressed as a literal, fully-qualified path (W2). Install as a PreToolUse hook for
Write|Edit|MultiEdit|NotebookEdit|Bash|PowerShell in ~/.claude/settings.json; see
settings_snippet.json alongside this file for the exact entry.

This is a thin entry point only: it reads stdin, classifies the call into the action(s) it
performs, runs each action's rule modules, logs every hit, and merges every rule module's verdict
into one deny (a single delete can fail both W2 and W3 - PreToolUse returns one decision, so their
reasons are joined, never one silently overwriting the other). Nothing here is project-specific -
it carries no path, doc name or rule text belonging to any one repo.

    hooklib/             paths.py (root + allow-list), shell.py (segmentation/tokenizing, incl. the
                         shared delete_verb_targets used by both rule modules below), output.py
                         (deny JSON, ASCII hit log with its size cap, verdict merging)
    rules/write/          w3_outside_root.py - WHERE a write (or delete) may land
    rules/delete/          w2_literal_delete_paths.py - HOW a delete's target must be expressed

Enforcement: user decision, 2026-09-28 - the global package installs with `--log-only` on its own
settings.json command line for about a week, so the user can decide real enforcement from the hit
numbers that week produces (hits are still recorded; nothing is denied while log-only, except a
rule the command line lists in `--enforce=` - see the per-rule paragraph below).
CLAUDE_WRITE_GUARD=log also still works, for a manual probe or a non-Windows install. Without
either, this hook denies by default.

Per-rule enforcement (user decision, 2026-09-30: "yes, stage step 1" - turn blocking on for W2
only): `--enforce=<id>[,<id>...]` on the same command line lists the rules that may DENY while the
hook otherwise runs `--log-only`. (settings_snippet.json is the distribution template and ships plain
`--log-only`; each machine's live settings.json sets its own `--enforce=` list.) Installed command
line once approved:

    python -I -S "C:/Users/ShadowEvil/.claude/hooks/write_guard.py" --log-only --enforce=W2

  * `--log-only --enforce=W2`: every rule still logs; only W2 hits can deny. W3, W4 and W5 stay
    log-only. `--enforce=W2,W3` would add W3; the flag is repeatable, ids are
    case-insensitive, the flag name is as tolerant as --log-only's.
  * No `--enforce` at all: behaviour is exactly what it was before (plain `--log-only` denies
    nothing; no flags at all denies everything enforceable, W2 and W3 reasons joined).
  * An `--enforce` list, even an empty or invalid one, is authoritative whether or not `--log-only`
    is also passed: only the listed enforceable rules may deny (2026-09-30 review fix - `--enforce=W2`
    alone used to enforce every rule). `--enforce` without `--log-only` is recorded in `.config` as
    `enforce-without-log-only`, since the intended line carries both.
  * W4 (ALWAYS_LOG_ONLY) is never enforceable, even if listed. An unknown id (the retired
    W3-unresolved included, 2026-10-20), a bare `--enforce`, a misspelled flag or a stray word is
    ignored, never a crash, and is written to `<hits path>.config` (size-capped) so a typo in
    settings.json is visible.
  * A deny carries ONLY the reasons of enforced rules: a delete failing both W2 and W3 under
    `--enforce=W2` denies with W2's reason alone. Every hit is still logged as before, and each log
    row now has `"denied": true|false`.
  * CLAUDE_WRITE_GUARD=log keeps its meaning and is the kill switch: nothing denies, `--enforce`
    included. Fail open is unchanged: any exception exits 0 without denying.

W2 precision as of 2026-09-30 (replay of the real hit log, 2026-09-28 17:03 to 2026-09-30 11:07, 498
W2 rows = 145 tool calls, each row mapped back to its real command from the session transcripts): a
seeded random sample of 30 rows hand-labelled 29 true positives, 1 false positive (96.7%). Over all
498 rows the old parser had 19 false rows (96.2%), 5 of the 145 calls were false-only (96.6% per
call). Two parser classes were found and FIXED in this version (rules/delete/w2_literal_delete_paths.py,
Bash only, with self-test cases): a quoted directory glued to an unquoted glob (`rm "C:/d/"*.pyc`
read as a relative `*.pyc`; 4 calls) and a backslash-newline continuation read as a target `\\`
(and its continuation lines never seen). With both fixed: 141 calls would deny, 1 of them a known
false positive (a `$` inside single quotes, which is literal: `'C:\\...\\data$d.db'`), 99.3% per call.
Everything else W2 flags is a genuine delete of a `$VAR`/relative/wildcard-directory target, or a
`find ... -delete/-exec rm`, exactly what the rule is written to stop.

    python -I -S write_guard.py --self-test
    python -I -S write_guard.py --log-only              (log-only, from the settings.json command)
    python -I -S write_guard.py --log-only --enforce=W2  (log-only, except W2 may deny)
    python -I -S write_guard.py --hits-path=<file>       (redirect the hit log - self-test/review)
"""
import json
import os
import sys
import time

# python -I leaves the script's own directory off sys.path - put it back so hooklib/rules import
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from hooklib import output  # noqa: E402
from rules.write import w3_outside_root  # noqa: E402
from rules.write import w4_review_stamp  # noqa: E402
from rules.write import w5_temp_dir  # noqa: E402
from rules.delete import w2_literal_delete_paths  # noqa: E402

# which action(s) a tool call performs; a Bash/PowerShell command can itself contain several
# actions (e.g. `cp a b; rm c`) - that finer classification happens inside each rule module, which
# walks the command's own segments. A future rules/read/ or rules/execute/ package would get its
# own action key and its own line in RULE_MODULES.
ACTIONS_FOR_TOOL = {
    "Write": ("write",), "Edit": ("write",), "MultiEdit": ("write",), "NotebookEdit": ("write",),
    "Bash": ("write", "delete"), "PowerShell": ("write", "delete"),
}
TOOL_EVENT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SHELL_EVENT_TOOLS = ("Bash", "PowerShell")
RULE_MODULES = {"write": (w3_outside_root, w4_review_stamp, w5_temp_dir),
                "delete": (w2_literal_delete_paths,)}


def modules_for(tool):
    """Every rule module this tool call's action(s) select, in order, each at most once."""
    seen = []
    for action in ACTIONS_FOR_TOOL.get(tool, ()):
        for mod in RULE_MODULES.get(action, ()):
            if mod not in seen:
                seen.append(mod)
    return seen


def run(event):
    """(rows, per_mod) - per_mod is [(mod, tool_hits, shell_hits), ...], kept PER MODULE rather
    than merged into one shared tool_hits/shell_hits list (2026-09-28 fix, found while wiring in a
    second "write" module, W4): each module's decide()/log_rows() only knows how to unpack ITS OWN
    hit shape - W3's are (raw, bad) tuples, W4's are dicts. An earlier version of this function
    merged every module's hits into one list and handed that SAME merged list to EVERY module's
    decide() - an event that trips both would crash W3's decide() unpacking a W4 dict as a 2-tuple,
    silently swallowed by main()'s fail-open `except Exception: pass`, which could silently
    suppress a genuine W3 deny for that call. Each module now only ever sees its own hits."""
    rows = []
    per_mod = []
    for mod, th, sh, mod_rows in run_detailed(event):
        rows.extend(mod_rows)
        per_mod.append((mod, th, sh))
    return rows, per_mod


def run_detailed(event):
    """[(mod, tool_hits, shell_hits, mod_rows), ...] - the same evaluation run() does, but with each
    module's own log rows kept next to its hits, so process() can mark a row `denied` from ITS
    module's verdict (2026-09-30, per-rule enforcement) instead of guessing afterwards."""
    tool = event.get("tool_name") or ""
    started = time.time()
    hits = []
    for mod in modules_for(tool):
        evaluate_tool = getattr(mod, "evaluate_tool", None)
        evaluate_shell = getattr(mod, "evaluate_shell", None)
        th = evaluate_tool(event) if evaluate_tool and tool in TOOL_EVENT_TOOLS else []
        sh = evaluate_shell(event) if evaluate_shell and tool in SHELL_EVENT_TOOLS else []
        hits.append([mod, th, sh, int((time.time() - started) * 1000)])
    drop_superseded(hits)
    return [(mod, th, sh, mod.log_rows(event, th, sh, ms)) for mod, th, sh, ms in hits]


def _hit_target(item):
    """The raw target of one hit, whatever the module's shape: (raw, bad) for a tool hit,
    (rule_id, (seg, verb, raw, ...)) for a shell hit; None for any other shape (W4's dicts)."""
    try:
        if isinstance(item[1], tuple):
            return item[1][2]
        return item[0]
    except (TypeError, IndexError, KeyError):
        return None


def drop_superseded(hits):
    """In place: a module declaring SUPERSEDES = ("W3",) owns its targets - the superseded
    module's hits on the SAME raw target are removed, so one write is
    one row (W5 over W3 for a temp write, 2026-10-18)."""
    for mod, th, sh, _ms in hits:
        owned_ids = getattr(mod, "SUPERSEDES", ())
        if not owned_ids or not (th or sh):
            continue
        owned = {_hit_target(x) for x in th} | {_hit_target(x) for x in sh}
        owned.discard(None)
        for entry in hits:
            if getattr(entry[0], "RULE_ID", None) in owned_ids:
                entry[1] = [x for x in entry[1] if _hit_target(x) not in owned]
                entry[2] = [x for x in entry[2] if _hit_target(x) not in owned]


# values that turn `--flag=value` ON; anything else (0, false, no, a typo, ...) is treated as OFF,
# not as an error - the fail-safe direction for an OFF/unrecognized value is enforce, same as the
# flag's plain absence.
TRUE_VALUES = {"1", "true", "yes"}


def cli_flag(argv, name):
    """True if a TOLERANT form of `--name` is present in argv, either as a bare switch or as
    `--name=<value>` with value in TRUE_VALUES (case-insensitive). Tolerant means: the flag's own
    name is matched case-insensitively with '_' and '-' interchangeable (`--log_only`,
    `--Log-Only`, `--LOG-ONLY` all match `--log-only`) - real spellings a hand-typed or generated
    settings.json command line might reasonably use. Anything NOT recognized - a different name
    entirely, an unrecognized value, or the flag's plain absence - resolves to False, the FAIL-SAFE
    direction (enforce): a misspelled or malformed flag must never silently downgrade a live install
    to log-only everywhere (review finding, 2026-09-28: 'the flag fails toward ENFORCE')."""
    target = name.lower().replace("_", "-")
    for a in argv:
        if not a.startswith("--"):
            continue
        body = a[2:]
        flag, sep, value = body.partition("=")
        norm_flag = flag.lower().replace("_", "-")
        if norm_flag != target:
            continue
        if not sep:
            return True
        return value.strip().lower() in TRUE_VALUES
    return False


def cli_value(argv, name):
    """The value of `--name=value` in argv, or None - argv is the hook's OWN command-line
    arguments (from settings.json), never the PreToolUse event, which always arrives on stdin."""
    prefix = "--" + name + "="
    for a in argv:
        if a.startswith(prefix):
            return a[len(prefix):]
    return None


# ------------------------------------------------------------------ per-rule enforcement (--enforce=)
#
# `--enforce=W2[,W3]` (repeatable) lists the rules that may DENY even while the hook runs
# `--log-only`. Ids are matched case-insensitively. Only a rule module that can actually deny is
# enforceable: W4 (ALWAYS_LOG_ONLY) is accepted on the command line but ignored, and so is an
# unknown id - each is recorded in `<hits path>.config` (output.record_config_issues), never raised.
# W3-unresolved (row removed 2026-10-20) is no longer recognized: an old settings line naming it
# takes the unknown-id path - recorded, never a crash, never a deny.


def enforceable_ids():
    """The rule ids a `--enforce=` list may turn on: every registered rule module's RULE_ID except
    a module marked ALWAYS_LOG_ONLY (W4)."""
    ids = set()
    for mods in RULE_MODULES.values():
        for mod in mods:
            if not getattr(mod, "ALWAYS_LOG_ONLY", False):
                ids.add(mod.RULE_ID.upper())
    return ids


def known_ids():
    """Every id that is at least recognized (enforceable or deliberately not)."""
    ids = set()
    for mods in RULE_MODULES.values():
        for mod in mods:
            ids.add(mod.RULE_ID.upper())
    return ids


KNOWN_FLAG_NAMES = ("log-only", "enforce", "hits-path", "self-test")


def _flag_name(arg):
    """The normalized flag name of one argv token (`--Log_Only=1` -> 'log-only'), or None when the
    token is not a `--flag`."""
    if not arg.startswith("--"):
        return None
    return arg[2:].partition("=")[0].lower().replace("_", "-")


def parse_enforce(argv):
    """(ids, issues): `ids` is the set of enforceable rule ids named by every `--enforce=` in argv
    (repeatable, comma-separated, case-insensitive, flag name tolerant in case and '_'/'-' like
    --log-only); `issues` is a list of ASCII strings describing anything ignored - an unknown id, a
    non-enforceable id (W4), a bare `--enforce`, an unrecognized flag or stray word,
    or `--enforce` given without `--log-only` (enforce-without-log-only).
    Never raises; with no `--enforce` at all it returns (set(), []) for a plain `--log-only` line, so
    today's command line parses exactly as before."""
    ids = set()
    issues = []
    enforceable = enforceable_ids()
    known = known_ids()
    for a in argv:
        name = _flag_name(a)
        if name is None:
            issues.append("stray-argument:%s" % _ascii(a)[:40])
            continue
        if name not in KNOWN_FLAG_NAMES:
            issues.append("unrecognized-flag:%s" % _ascii(a)[:40])
            continue
        if name != "enforce":
            continue
        _flag, sep, value = a[2:].partition("=")
        if not sep or not value.strip():
            issues.append("enforce-without-ids")
            continue
        for raw in value.split(","):
            rid = raw.strip().upper()
            if not rid:
                continue
            if rid in enforceable:
                ids.add(rid)
            elif rid in known:
                issues.append("not-enforceable:%s" % _ascii(rid)[:20])
            else:
                issues.append("unknown-id:%s" % _ascii(rid)[:20])
    if enforce_given(argv) and not cli_flag(argv, "log-only"):
        issues.append("enforce-without-log-only")
    return ids, issues


def enforce_given(argv):
    """True when argv carries any `--enforce` flag at all (bare, empty or with ids; flag name
    tolerant like --log-only's). Its presence makes the list authoritative - see module_enforce()."""
    return any(_flag_name(a) == "enforce" for a in argv)


def _ascii(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def module_enforce(mod, global_enforce, enforce_ids, env_log, enforce_listed=False):
    """Whether THIS rule module's decide() may deny. With no `--enforce` flag at all
    (`enforce_listed` False), `global_enforce` (no --log-only, no CLAUDE_WRITE_GUARD=log) keeps
    today's behaviour: every module may deny. Otherwise - `--log-only`, or ANY `--enforce` flag even
    without `--log-only` (2026-09-30 review fix) - a module may deny only when its rule id is in
    `enforce_ids`, and never when CLAUDE_WRITE_GUARD=log is set, which stays the kill switch for the
    whole hook, `--enforce=` included."""
    if global_enforce and not enforce_listed:
        return True
    if env_log:
        return False
    if getattr(mod, "ALWAYS_LOG_ONLY", False):
        return False
    return getattr(mod, "RULE_ID", "").upper() in enforce_ids


def process(event, argv):
    """(rows, verdict, issues) for one PreToolUse event under this command line. `rows` are every
    module's log rows, each with a `denied` flag; `verdict` is the ONE merged deny (or None) that
    carries only the reasons of rule modules allowed to enforce - a delete failing both W2 and W3
    under `--enforce=W2` denies with W2's reason alone while W3's row is still logged (denied:
    false). A decide() that raises is fail-open: no deny, rows still logged."""
    detailed = run_detailed(event)
    enforce_ids, issues = set(), []
    enforce_listed = False
    try:
        enforce_listed = enforce_given(argv)  # first: if parsing then raises, the list stays empty
        enforce_ids, issues = parse_enforce(argv)
    except Exception:
        pass  # fail open: an unparseable --enforce is no enforcement, never a crash
    verdicts = [None] * len(detailed)
    try:
        if any(th or sh for _mod, th, sh, _rows in detailed):
            global_enforce = output.enforcing(force_log_only=cli_flag(argv, "log-only"))
            env_log = output.env_log_only()
            verdicts = [mod.decide(event, th, sh,
                                   enforce=module_enforce(mod, global_enforce, enforce_ids, env_log,
                                                          enforce_listed))
                        for mod, th, sh, _rows in detailed]
    except Exception:
        verdicts = [None] * len(detailed)  # fail open
    rows = []
    for (mod, _th, _sh, mod_rows), verdict in zip(detailed, verdicts):
        rule_id = getattr(mod, "RULE_ID", None)
        for row in mod_rows:
            row = dict(row)
            # only a row carrying the module's OWN rule id can have denied
            row["denied"] = bool(verdict) and row.get("rule") == rule_id
            rows.append(row)
    return rows, output.combine(*verdicts), issues


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        event = json.loads(sys.stdin.read() or "{}")
        rows, verdict, issues = process(event, argv)
        hits_path = cli_value(argv, "hits-path")
        output.log_hits(rows, hits_path=hits_path)
        output.record_config_issues(issues, argv, hits_path=hits_path)
        output.print_verdict(verdict)
    except Exception:
        pass  # fail open: a broken hook must never block, print or fail a tool call
    sys.exit(0)


def enforce_self_test(failures):
    """Per-rule enforcement (2026-09-30): the `--enforce=` parsing, which rules may deny under
    `--log-only --enforce=W2`, verdict merging, the `denied` flag on hit rows, the env kill switch,
    fail-open, and one real subprocess run of the entry point. Appends to `failures`; returns the
    number of cases it ran (write_guard.py's self_test() adds it to its printed total)."""
    import subprocess
    import tempfile

    n = 0
    root = w3_outside_root.ROOT

    def check(ok, msg):
        nonlocal n
        n += 1
        if not ok:
            failures.append("enforce: " + msg)

    # ---- parsing: --enforce=W2, W2,W3, repeated flags, unknown id, W4 listed, no flag
    parse_cases = [
        (["--log-only", "--enforce=W2"], {"W2"}, []),
        (["--log-only", "--enforce=W2,W3"], {"W2", "W3"}, []),
        (["--log-only", "--enforce=W2", "--enforce=W3"], {"W2", "W3"}, []),       # repeated flags
        (["--log-only", "--enforce=w2"], {"W2"}, []),                               # id case-insensitive
        (["--log-only", "--Enforce=W2"], {"W2"}, []),                               # flag name tolerant
        (["--log_only", "--ENFORCE=W2"], {"W2"}, []),
        (["--log-only", "--enforce=W2, W3"], {"W2", "W3"}, []),                     # spaces tolerated
        (["--log-only", "--enforce=W9"], set(), ["unknown-id:W9"]),                 # unknown id ignored
        (["--log-only", "--enforce=W2,W9"], {"W2"}, ["unknown-id:W9"]),
        (["--log-only", "--enforce=W4"], set(), ["not-enforceable:W4"]),            # W4 never enforceable
        (["--log-only", "--enforce=W2,W4"], {"W2"}, ["not-enforceable:W4"]),
        # W3-unresolved retired 2026-10-20: an old line naming it is just an unknown id
        (["--log-only", "--enforce=W3-unresolved"], set(), ["unknown-id:W3-UNRESOLVED"]),
        (["--log-only", "--enforce=W2,W3-unresolved"], {"W2"}, ["unknown-id:W3-UNRESOLVED"]),
        (["--log-only", "--enforce"], set(), ["enforce-without-ids"]),              # bare flag
        (["--log-only", "--enforce="], set(), ["enforce-without-ids"]),
        (["--log-only"], set(), []),                                                # no flag: unchanged
        ([], set(), []),
        (["--log-only", "--hits-path=x"], set(), []),
        (["--log-only", "--enforse=W2"], set(), ["unrecognized-flag:--enforse=W2"]),  # typo surfaces
        (["--log-only", "W2"], set(), ["stray-argument:W2"]),
        # 2026-09-30 review fix: --enforce without --log-only is recorded, never an error
        (["--enforce=W2"], {"W2"}, ["enforce-without-log-only"]),
        (["--ENFORCE=w2"], {"W2"}, ["enforce-without-log-only"]),
        (["--enforce"], set(), ["enforce-without-ids", "enforce-without-log-only"]),
        (["--enforce=W9"], set(), ["unknown-id:W9", "enforce-without-log-only"]),
    ]
    for argv, want_ids, want_issues in parse_cases:
        got_ids, got_issues = parse_enforce(argv)
        check(got_ids == want_ids and got_issues == want_issues,
              "parse_enforce(%r) must be (%r, %r), got (%r, %r)"
              % (argv, sorted(want_ids), want_issues, sorted(got_ids), got_issues))
    check(enforceable_ids() == {"W2", "W3", "W5"}, "enforceable_ids() must be W2, W3 and W5 only, got %r"
          % sorted(enforceable_ids()))

    # ---- events: W2 only, W3 only, W2+W3, W4 only, an unjudged $variable target (no row at all)
    ev_w2 = {"tool_name": "Bash", "cwd": root, "tool_input": {"command": "rm -rf build"}}
    ev_w3 = {"tool_name": "Bash", "cwd": root, "tool_input": {"command": "rm -rf /c/snap-f1"}}
    ev_both = {"tool_name": "Bash", "cwd": root, "tool_input": {"command": "rm -rf ../../x"}}
    ev_w4 = {"tool_name": "Write", "cwd": root, "agent_id": "enforce-selftest",
             "tool_input": {"file_path": root + "\\.claude\\hooks\\enforce_selftest_new.py",
                            "content": "print(1)\n"}}
    ev_unres = {"tool_name": "Bash", "cwd": root, "tool_input": {"command": "echo x > $DEST/out.txt"}}

    def outcome(ev, argv):
        rows, verdict, _issues = process(ev, argv)
        out = (verdict or {}).get("hookSpecificOutput", {})
        denied = out.get("permissionDecision") == "deny"
        return denied, out.get("permissionDecisionReason", ""), {(r["rule"], r["denied"]) for r in rows}

    saved_env = os.environ.get("CLAUDE_WRITE_GUARD")
    os.environ.pop("CLAUDE_WRITE_GUARD", None)
    try:
        # setup sanity: each event trips exactly the rules the cases below assume
        _d, _r, hit_w2 = outcome(ev_w2, ["--log-only"])
        _d, _r, hit_w3 = outcome(ev_w3, ["--log-only"])
        _d, _r, hit_both = outcome(ev_both, ["--log-only"])
        _d, _r, hit_w4 = outcome(ev_w4, ["--log-only"])
        _d, _r, hit_unres = outcome(ev_unres, ["--log-only"])
        check({r for r, _ in hit_w2} == {"W2"}, "setup: ev_w2 must trip W2 only, got %r" % hit_w2)
        check({r for r, _ in hit_w3} == {"W3"}, "setup: ev_w3 must trip W3 only, got %r" % hit_w3)
        check({r for r, _ in hit_both} == {"W2", "W3"}, "setup: ev_both must trip W2 and W3, got %r" % hit_both)
        check("W4" in {r for r, _ in hit_w4}, "setup: ev_w4 must trip W4, got %r" % hit_w4)
        check(hit_unres == set(), "setup: ev_unres must log no row at all, got %r" % (hit_unres,))

        # plain --log-only (no --enforce): nothing denies, every hit still logged (unchanged)
        for name, ev in (("W2", ev_w2), ("W3", ev_w3), ("W2+W3", ev_both), ("W4", ev_w4)):
            d, _r, flags = outcome(ev, ["--log-only"])
            check(not d and flags and all(not f for _r2, f in flags),
                  "%s denied or lost its row under plain --log-only (must be unchanged)" % name)
        # no flags at all (today's default): enforce everything, W2+W3 joined, W4 still never
        d, reason, _f = outcome(ev_w2, [])
        check(d and "W2" in reason, "no flags: a W2 hit must deny (today's default)")
        d, reason, flags = outcome(ev_both, [])
        check(d and "W2" in reason and "W3" in reason and ("W2", True) in flags and ("W3", True) in flags,
              "no flags: a W2+W3 delete must deny with BOTH reasons (today's merge)")
        d, _r, flags = outcome(ev_w4, [])
        check(not d and ("W4", False) in flags, "no flags: W4 must never deny")

        # the 2026-09-30 change: --log-only --enforce=W2
        argv_w2 = ["--log-only", "--enforce=W2"]
        d, reason, flags = outcome(ev_w2, argv_w2)
        check(d and "W2" in reason and flags == {("W2", True)},
              "--enforce=W2: a W2 hit must deny and its row be denied:true, got %r" % (flags,))
        d, reason, flags = outcome(ev_w3, argv_w2)
        check(not d and flags == {("W3", False)},
              "--enforce=W2: a W3-only hit must NOT deny (row still logged, denied:false), got %r" % (flags,))
        d, reason, flags = outcome(ev_both, argv_w2)
        check(d and "W2" in reason and "W3" not in reason,
              "--enforce=W2: a W2+W3 delete must deny with W2's reason ONLY, got %r" % reason[:120])
        check(flags == {("W2", True), ("W3", False)},
              "--enforce=W2: W2+W3 rows must be W2 denied:true and W3 denied:false, got %r" % (flags,))
        for argv_w4 in (["--log-only", "--enforce=W4"], ["--log-only", "--enforce=W2,W4"],
                        ["--enforce=W4", "--log-only"]):
            d, _r, flags = outcome(ev_w4, argv_w4)
            check(not d and ("W4", False) in flags, "W4 hit must never deny (%r)" % argv_w4)
        # 2026-10-20: an unresolvable $variable target logs NO W3-unresolved row and never denies,
        # under full enforcement (no flags) or with W3 and the retired id both listed
        for argv_unres in ([], ["--log-only", "--enforce=W3,W3-unresolved"]):
            d, _r, flags = outcome(ev_unres, argv_unres)
            check(not d and flags == set(),
                  "a $variable target must log no row and never deny (%r): %r" % (argv_unres, flags))
        d, _r, flags = outcome(ev_w2, ["--log-only", "--enforce=W9"])
        check(not d and flags == {("W2", False)}, "an unknown id must enforce nothing (and not crash)")
        # both listed: W2 and W3 both enforced, reasons joined
        d, reason, flags = outcome(ev_both, ["--log-only", "--enforce=W2,W3"])
        check(d and "W2" in reason and "W3" in reason and flags == {("W2", True), ("W3", True)},
              "--enforce=W2,W3: a W2+W3 delete must deny with both reasons")
        d, reason, _f = outcome(ev_w3, ["--log-only", "--enforce=W2", "--enforce=W3"])
        check(d and "W3" in reason, "repeated --enforce flags must both apply (W3 here)")

        # 2026-09-30 review fix: an --enforce list is authoritative WITHOUT --log-only too - it used
        # to enforce every rule (a W3-only `rm -rf /c/snap-f1` denied under `--enforce=W2` alone)
        d, _r, flags = outcome(ev_w3, ["--enforce=W2"])
        check(not d and flags == {("W3", False)},
              "--enforce=W2 without --log-only: a W3-only hit must NOT deny, got %r" % (flags,))
        d, reason, flags = outcome(ev_w2, ["--enforce=W2"])
        check(d and "W2" in reason and flags == {("W2", True)},
              "--enforce=W2 without --log-only: a W2 hit must deny, got %r" % (flags,))
        d, reason, flags = outcome(ev_both, ["--enforce=W2"])
        check(d and "W2" in reason and "W3" not in reason and flags == {("W2", True), ("W3", False)},
              "--enforce=W2 without --log-only: a W2+W3 delete must deny with W2's reason ONLY")
        d, _r, flags = outcome(ev_w4, ["--enforce=W2"])
        check(not d and ("W4", False) in flags, "--enforce=W2 without --log-only: W4 must never deny")
        for bad in (["--enforce"], ["--enforce="], ["--enforce=W9"], ["--enforce=W4"]):
            d2, _r, _f = outcome(ev_w2, bad)
            d3, _r, _f = outcome(ev_w3, bad)
            check(not d2 and not d3, "an empty/invalid --enforce list without --log-only must deny nothing (%r)" % bad)
        # no --enforce at all: unchanged - no flags enforces W3 as before
        d, reason, flags = outcome(ev_w3, [])
        check(d and "W3" in reason and flags == {("W3", True)},
              "no flags at all: a W3-only hit must still deny (unchanged), got %r" % (flags,))
        os.environ["CLAUDE_WRITE_GUARD"] = "log"
        d, _r, _f = outcome(ev_w2, ["--enforce=W2"])
        check(not d, "CLAUDE_WRITE_GUARD=log must override --enforce=W2 without --log-only too")
        os.environ.pop("CLAUDE_WRITE_GUARD", None)

        # CLAUDE_WRITE_GUARD=log keeps its meaning: nothing denies, --enforce included
        os.environ["CLAUDE_WRITE_GUARD"] = "log"
        d, _r, flags = outcome(ev_w2, argv_w2)
        check(not d and flags == {("W2", False)}, "CLAUDE_WRITE_GUARD=log must override --enforce=W2")
        d, _r, flags = outcome(ev_w2, [])
        check(not d and flags == {("W2", False)}, "CLAUDE_WRITE_GUARD=log alone must stay log-only (unchanged)")
        os.environ.pop("CLAUDE_WRITE_GUARD", None)

        # fail open: a rule module whose decide() raises must not deny, print or lose its rows
        real_decide = w2_literal_delete_paths.decide
        try:
            def boom(*a, **k):
                raise RuntimeError("selftest: decide() blew up")
            w2_literal_delete_paths.decide = boom
            rows_f, verdict_f, _i = process(ev_w2, argv_w2)
            check(verdict_f is None and rows_f and all(r["denied"] is False for r in rows_f),
                  "a raising decide() must fail open (no deny) and still return the rows")
        finally:
            w2_literal_delete_paths.decide = real_decide

        # ASCII + the PreToolUse deny shape
        _rows, verdict_a, _i = process(ev_w2, argv_w2)
        shape_ok = (isinstance(verdict_a, dict) and list(verdict_a) == ["hookSpecificOutput"] and
                    verdict_a["hookSpecificOutput"].get("hookEventName") == "PreToolUse" and
                    verdict_a["hookSpecificOutput"].get("permissionDecision") == "deny")
        check(shape_ok, "deny JSON must keep the PreToolUse shape")
        try:
            json.dumps(verdict_a, ensure_ascii=True).encode("ascii")
            ascii_ok = True
        except UnicodeEncodeError:
            ascii_ok = False
        check(ascii_ok, "deny JSON must be ASCII")

        # one real subprocess run of the entry point under the real --log-only --enforce=W2 line
        with tempfile.TemporaryDirectory() as td:
            scratch = os.path.join(td, "enforce_probe_hits.jsonl")
            script = os.path.join(HERE, "write_guard.py")
            env = {k: v for k, v in os.environ.items() if k != "CLAUDE_WRITE_GUARD"}

            def run_hook(event_text, extra):
                return subprocess.run([sys.executable, "-I", "-S", script, "--log-only",
                                       "--hits-path=" + scratch] + extra,
                                      input=event_text, capture_output=True, text=True, env=env,
                                      timeout=30)

            p = run_hook(json.dumps(ev_w2), ["--enforce=W2"])
            check(p.returncode == 0, "subprocess: exit code must be 0 even when denying")
            try:
                sub = json.loads(p.stdout)
            except ValueError:
                sub = {}
            check(sub.get("hookSpecificOutput", {}).get("permissionDecision") == "deny" and
                  "W2" in sub.get("hookSpecificOutput", {}).get("permissionDecisionReason", ""),
                  "subprocess: --log-only --enforce=W2 must print a W2 deny, got %r" % p.stdout[:120])
            check(p.stdout.isascii(), "subprocess: stdout must be ASCII")
            p = run_hook(json.dumps(ev_w3), ["--enforce=W2"])
            check(p.returncode == 0 and p.stdout.strip() == "", "subprocess: a W3-only hit must print nothing")
            p = run_hook(json.dumps(ev_w2), ["--enforce=W9", "--enforse=W2"])
            check(p.returncode == 0 and p.stdout.strip() == "",
                  "subprocess: unknown id / misspelled flag must print nothing and exit 0")
            p = run_hook("{not json at all", ["--enforce=W2"])
            check(p.returncode == 0 and p.stdout.strip() == "", "subprocess: garbage stdin must fail open")
            logged = []
            try:
                with open(scratch, encoding="ascii") as fh:
                    logged = [json.loads(ln) for ln in fh.read().split("\n") if ln]
            except (OSError, ValueError):
                pass
            check(any(r.get("rule") == "W2" and r.get("denied") is True for r in logged) and
                  any(r.get("rule") == "W3" and r.get("denied") is False for r in logged),
                  "subprocess: hit log must carry W2 denied:true and W3 denied:false rows")
            try:
                with open(scratch + output.CONFIG_SUFFIX, encoding="ascii") as fh:
                    cfg = fh.read()
            except OSError:
                cfg = ""
            check("unknown-id:W9" in cfg and "unrecognized-flag:--enforse=W2" in cfg,
                  "subprocess: config problems must be recorded in <hits>.config, got %r" % cfg[:160])
            check("enforce-without-log-only" not in cfg,
                  "subprocess: a line WITH --log-only must not record enforce-without-log-only")
            # --enforce=W2 WITHOUT --log-only (2026-09-30 review fix): W3-only stays quiet, W2
            # denies, and the missing --log-only is written to <hits>.config
            p_nolog = subprocess.run([sys.executable, "-I", "-S", script, "--enforce=W2",
                                      "--hits-path=" + scratch],
                                     input=json.dumps(ev_w3), capture_output=True, text=True, env=env,
                                     timeout=30)
            check(p_nolog.returncode == 0 and p_nolog.stdout.strip() == "",
                  "subprocess: --enforce=W2 without --log-only must not deny a W3-only hit, got %r"
                  % p_nolog.stdout[:120])
            p_nolog = subprocess.run([sys.executable, "-I", "-S", script, "--enforce=W2",
                                      "--hits-path=" + scratch],
                                     input=json.dumps(ev_w2), capture_output=True, text=True, env=env,
                                     timeout=30)
            check(p_nolog.returncode == 0 and '"deny"' in p_nolog.stdout,
                  "subprocess: --enforce=W2 without --log-only must still deny a W2 hit")
            try:
                with open(scratch + output.CONFIG_SUFFIX, encoding="ascii") as fh:
                    cfg = fh.read()
            except OSError:
                cfg = ""
            check("enforce-without-log-only" in cfg,
                  "subprocess: --enforce without --log-only must be recorded in <hits>.config, got %r"
                  % cfg[-200:])
    finally:
        if saved_env is None:
            os.environ.pop("CLAUDE_WRITE_GUARD", None)
        else:
            os.environ["CLAUDE_WRITE_GUARD"] = saved_env
    return n


def self_test():
    import tempfile

    failures = []
    n_cases = 0
    for mod in (w3_outside_root, w4_review_stamp, w2_literal_delete_paths, w5_temp_dir):
        failures.extend(mod.run_self_test())
        n_cases += len(getattr(mod, "TOOL_CASES", ()))
        n_cases += len(getattr(mod, "SHELL_CASES", ()))
        n_cases += len(getattr(mod, "PS_COMMA_CASES", ()))
        n_cases += len(getattr(mod, "PS_AUDIT_CASES", ()))  # 2026-09-28 PS write-surface audit
        n_cases += len(getattr(mod, "CMD_EXE_CASES", ()))   # same audit, cmd.exe-side cases
        n_cases += len(getattr(mod, "FP_CASES", ()))        # 2026-10-17 W3 false-positive fixes
        n_cases += getattr(mod, "N_CASES", 0)  # w4_review_stamp: real-file cases, hand-counted

    # --log-only (write_guard.py's own CLI flag, 2026-09-28 - the reliable way to select log-only
    # on Windows, where a `VAR=x cmd` prefix on the settings.json command line does not reliably
    # reach the child process): a genuine hit must never deny, but log_rows must still produce rows.
    ev_hit = {"tool_name": "Write", "cwd": w3_outside_root.ROOT,
              "tool_input": {"file_path": "C:\\snap-f1\\x.md"}}
    rows, per_mod = run(ev_hit)
    w3_th, w3_sh = next(((th, sh) for mod, th, sh in per_mod if mod is w3_outside_root), ([], []))
    if not rows:
        failures.append("--log-only test setup: expected a real hit to log something")
    if output.enforcing(force_log_only=True):
        failures.append("output.enforcing(force_log_only=True) was still True")
    if output.enforcing(force_log_only=cli_flag(["--log-only"], "log-only")) is not False:
        failures.append("cli_flag did not read --log-only off argv")
    verdict_logonly = w3_outside_root.decide(ev_hit, w3_th, w3_sh,
                                              enforce=output.enforcing(force_log_only=True))
    if verdict_logonly is not None:
        failures.append("a genuine hit denied under --log-only")
    verdict_enforced = w3_outside_root.decide(ev_hit, w3_th, w3_sh,
                                               enforce=output.enforcing(force_log_only=False))
    out_enforced = (verdict_enforced or {}).get("hookSpecificOutput", {})
    if out_enforced.get("permissionDecision") != "deny":
        failures.append("the same hit did not deny once --log-only is NOT passed")
    n_cases += 5  # the --log-only block just above (2026-09-28 fix: was never counted)

    # Per-module hit isolation (2026-09-28 fix, found while wiring in W4 alongside W3): a single
    # shell command with TWO segments - one tripping W3 (outside every allowed root), the other
    # tripping W4 (an unstamped write into .claude/hooks) - must deny with W3's reason (W4 never
    # denies) and must NOT raise while doing it. The bug this guards against: an earlier run()
    # merged every module's hits into ONE shared list and fed that SAME list to every module's
    # decide() - W3's decide() unpacking a W4 dict as a (raw, bad) tuple would raise, silently
    # swallowed by main()'s fail-open catch-all, which would have silently dropped the W3 deny too.
    ev_mixed = {"tool_name": "Bash", "cwd": w3_outside_root.ROOT,
                "tool_input": {"command":
                                "cp a.txt C:\\x\\outside.txt; cp b.py " +
                                w3_outside_root.ROOT + "\\.claude\\hooks\\new.py"}}
    rows_mixed, per_mod_mixed = run(ev_mixed)
    if not any(th or sh for _m, th, sh in per_mod_mixed if _m is w3_outside_root):
        failures.append("mixed-hit test setup: expected a W3 hit on the outside-root segment")
    if not any(th or sh for _m, th, sh in per_mod_mixed if _m is w4_review_stamp):
        failures.append("mixed-hit test setup: expected a W4 hit on the .claude/hooks segment")
    try:
        enforce_mixed = output.enforcing(force_log_only=False)
        verdicts_mixed = [mod.decide(ev_mixed, th, sh, enforce=enforce_mixed)
                           for mod, th, sh in per_mod_mixed]
        combined_mixed = output.combine(*verdicts_mixed)
    except Exception as e:
        failures.append("per-module decide() raised on a mixed W3+W4 event: %r" % (e,))
    else:
        reason = (combined_mixed or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        if (combined_mixed or {}).get("hookSpecificOutput", {}).get("permissionDecision") != "deny" \
                or "W3" not in reason:
            failures.append("mixed W3+W4 event did not deny with W3's reason")
        if "W4" in reason:
            failures.append("W4 contributed to a deny reason despite ALWAYS_LOG_ONLY")
    n_cases += 5  # the mixed W3+W4 per-module isolation block just above

    # HITS_PATH override: --hits-path=<scratch file>, and CLAUDE_WRITE_GUARD_HITS_PATH, must both
    # redirect log_hits away from the real ~/.claude/hooks/write_guard_hits.jsonl - a reviewer
    # probing this hook manually polluted that real file on 2026-09-28 before this override existed.
    with tempfile.TemporaryDirectory() as td:
        scratch = os.path.join(td, "probe_hits.jsonl")
        output.log_hits(rows, hits_path=cli_value(["--hits-path=" + scratch], "hits-path"))
        if not os.path.exists(scratch):
            failures.append("--hits-path did not redirect log_hits")
        # basic correctness of the locked-write path (2026-09-28 review: a first, unlocked
        # os.O_APPEND-only version of this passed a solo self-test but LOST and CORRUPTED rows
        # under real concurrency - see _atomic_stress.py - so this also proves two sequential
        # calls append cleanly, every line still parses, and the lock never gets stuck across calls)
        output.log_hits(rows, hits_path=scratch)
        with open(scratch, encoding="ascii") as fh:
            written_lines = [ln for ln in fh.read().split("\n") if ln]
        if len(written_lines) != 2 * len(rows):
            failures.append("log_hits: two sequential calls did not produce 2x the rows (locking regression?)")
        for ln in written_lines:
            try:
                json.loads(ln)
            except ValueError:
                failures.append("log_hits wrote a line that does not parse as JSON: %r" % ln[:100])
                break
        saved_env = os.environ.get(output.HITS_PATH_ENV)
        try:
            scratch2 = os.path.join(td, "probe_hits_env.jsonl")
            os.environ[output.HITS_PATH_ENV] = scratch2
            output.log_hits(rows)  # no explicit hits_path - must fall through to the env var
            if not os.path.exists(scratch2):
                failures.append("CLAUDE_WRITE_GUARD_HITS_PATH did not redirect log_hits")
        finally:
            if saved_env is None:
                os.environ.pop(output.HITS_PATH_ENV, None)
            else:
                os.environ[output.HITS_PATH_ENV] = saved_env
    n_cases += 4  # the HITS_PATH override block just above (2026-09-28 fix: was never counted)

    # a HELD lock must lead to a drop-and-count within budget, not a block (2026-09-28 final
    # review: the earlier version used a BLOCKING lock wait, measured at up to 8.16s under real
    # contention against the 10s PreToolUse hook timeout - Claude Code kills a timed-out hook and
    # lets the call proceed anyway, so a block does not fail safe). A second, independent handle on
    # the SAME file (Windows/POSIX byte-range locks are enforced per HANDLE, not per process, so
    # this reproduces real cross-process contention without spawning one) holds the sentinel lock
    # for the whole call; log_hits() must return well within LOCK_BUDGET_SECONDS, write NOTHING to
    # the hits file, and record the drop in <path>.dropped.
    with tempfile.TemporaryDirectory() as td3:
        held_path = os.path.join(td3, "held_lock_hits.jsonl")
        open(held_path, "wb").close()
        holder = open(held_path, "r+b")
        try:
            if not output._try_lock_nonblocking(holder):
                failures.append("lock self-test setup: could not acquire the holder's own lock")
            t0 = time.time()
            output.log_hits(rows, hits_path=held_path)
            elapsed = time.time() - t0
            if elapsed > output.LOCK_BUDGET_SECONDS + 0.5:  # generous slack for scheduling jitter
                failures.append("log_hits blocked past its lock budget: %.3fs elapsed" % elapsed)
            # os.path.getsize(), not a read through a fresh handle: the sentinel byte itself is
            # still exclusively locked by `holder`, so a plain open+read of the file here would
            # itself hit that lock (confirms the lock is real) rather than answer the question
            if os.path.getsize(held_path) != 0:
                failures.append("log_hits wrote to the file despite the lock being held")
            dropped_path = held_path + output.DROPPED_SUFFIX
            if not os.path.exists(dropped_path):
                failures.append("log_hits did not record a drop when the lock could not be acquired")
            else:
                with open(dropped_path, encoding="ascii") as fh:
                    drop_line = fh.read().strip()
                try:
                    drop_obj = json.loads(drop_line)
                except ValueError:
                    failures.append("the .dropped record is not valid JSON: %r" % drop_line[:100])
                else:
                    if drop_obj.get("dropped_rows") != len(rows):
                        failures.append(".dropped record's dropped_rows does not match the batch size")
        finally:
            output._unlock(holder)
            holder.close()
    n_cases += 6  # the held-lock/drop block just above (2026-09-28 fix: was never counted)

    # tolerant --log-only spelling (2026-09-28 review): case, '_'/'-', and a "=1/true/yes" value
    # form must all resolve to log-only; anything unrecognized (a wrong value, no flag, a
    # completely different name) must resolve to enforce - the fail-safe direction.
    LOG_ONLY_SPELLINGS = [
        (["--log-only"], True), (["--log_only"], True), (["--Log-Only"], True),
        (["--LOG_ONLY"], True), (["--log-only=1"], True), (["--log-only=true"], True),
        (["--log-only=TRUE"], True), (["--log-only=yes"], True), (["--Log_Only=Yes"], True),
        (["--log-only=0"], False), (["--log-only=false"], False), (["--log-only=no"], False),
        (["--log-only=maybe"], False), ([], False), (["--logonly"], False),
        (["--hits-path=x"], False),
    ]
    for argv, want in LOG_ONLY_SPELLINGS:
        got = cli_flag(argv, "log-only")
        if got != want:
            failures.append("cli_flag(%r, 'log-only') must be %r, got %r" % (argv, want, got))
    if not any(w for _a, w in LOG_ONLY_SPELLINGS) or not any(not w for _a, w in LOG_ONLY_SPELLINGS):
        failures.append("LOG_ONLY_SPELLINGS lacks a True or False case")
    n_cases += len(LOG_ONLY_SPELLINGS) + 1  # +1 for the settings_snippet.json resolution check below

    # settings_snippet.json itself must actually resolve to log-only under this same tolerant
    # parser - a future respelling of the command there must fail THIS self-test, not silently
    # enforce everywhere once installed (review finding, 2026-09-28).
    import shlex
    try:
        with open(os.path.join(HERE, "settings_snippet.json"), encoding="utf-8") as fh:
            snippet = json.load(fh)
        cmd = snippet["hooks"][0]["command"]
        snippet_toks = shlex.split(cmd, posix=True)
        idx = next((i for i, t in enumerate(snippet_toks)
                    if t.replace("\\", "/").lower().endswith("write_guard.py")), -1)
        if idx < 0:
            failures.append("settings_snippet.json's command does not invoke write_guard.py: %r" % cmd)
        else:
            snippet_argv = snippet_toks[idx + 1:]
            if not cli_flag(snippet_argv, "log-only"):
                failures.append(
                    "settings_snippet.json's command does not resolve to --log-only: %r" % cmd)
            else:
                # The snippet is the distribution template and ships plain log-only (2026-10-20: new
                # installs start log-only); a machine's live settings.json picks its own --enforce= list.
                want_enforce = set()
                got_enforce, snippet_issues = parse_enforce(snippet_argv)
                if got_enforce != want_enforce:
                    failures.append("settings_snippet.json's command must enforce exactly %r, got %r: %r"
                                    % (sorted(want_enforce), sorted(got_enforce), cmd))
                if snippet_issues:
                    failures.append("settings_snippet.json's command has flag problems: %r" % (snippet_issues,))
    except Exception as e:
        failures.append("could not load/parse settings_snippet.json: %r" % (e,))
    n_cases += 2  # the snippet's enforce list and its flag-problem check

    n_cases += enforce_self_test(failures)

    # W5 supersedes W3 (2026-10-18): a temp target is ONE row, W5 - never also W3
    from hooklib import paths
    _tmp = paths.expand_short_names(os.path.normpath(tempfile.gettempdir()))
    _scr = _tmp + "\\claude\\slug\\5abc868e-b44c-4092-b407-b7e455c39e94\\scratchpad\\n.txt"
    _root = w3_outside_root.ROOT
    for _tool, _ti, _want in (
            ("Bash", {"command": "echo x > /tmp/x"}, ["W5"]),
            ("Bash", {"command": "echo x > $TEMP/x"}, ["W5"]),
            ("Bash", {"command": "cd /tmp && cat > x <<'EOF'\nhi\nEOF"}, ["W5"]),
            ("Write", {"file_path": _scr}, ["W5"]),
            ("Bash", {"command": "echo x > C:\\x\\evil.txt"}, ["W3"]),
            ("Bash", {"command": "echo x > /tmp/a && echo y > C:\\x\\b"}, ["W3", "W5"]),
            ("Bash", {"command": "echo x > C:\\proj\\temp\\x"}, ["W3"])):
        _rows, _pm = run({"tool_name": _tool, "cwd": _root, "tool_input": _ti})
        _got = sorted(r["rule"] for r in _rows)  # every row: no stray rule may appear either
        if _got != _want:
            failures.append("W5/W3 de-dup: %s %r gave rows %r, want %r" % (_tool, _ti, _got, _want))
        n_cases += 1

    if failures:
        for f in failures:
            print("FAIL: " + f)
        print("self-test: %d case(s), %d failure(s)" % (n_cases, len(failures)))
        return 1
    print("self-test: %d case(s), 0 failure(s)" % n_cases)
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    main()
