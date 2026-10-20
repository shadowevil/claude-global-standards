"""S1: a project's own .claude/ folder stays under 1 GB (user rule, 2026-10-20: "if the agents local
.claude directory gets bigger than 1gb, it needs to prune any temporary files and address the
bloat"; and "Can we enforce that whenever that directory is greped or used?").

"Local .claude" is the session's PROJECT folder's .claude/ (CLAUDE_PROJECT_DIR, else the event's
cwd), never ~/.claude: a session opened in the home folder itself is skipped.

Class (c), partly checkable. A hook can DETECT the size and FLAG it; it cannot judge which files are
temporary, so it never deletes anything. User ruling, 2026-10-20: "It mostly needs to be a flag to
the agent/PM to prune it. Not necessarily blocking." - there is no deny path and no enforce mode.
Who is flagged, and by which event:
  * the main session (the PM): SessionStart (startup/resume/clear/compact) and UserPromptSubmit -
    neither needs a .claude path - plus its own PreToolUse calls that touch .claude;
  * a subagent: PreToolUse calls it makes that touch .claude (the event carries `agent_id`). It is
    told to report the flag and to prune only if its brief allows writes. SubagentStart is
    deliberately not used: it would flag every subagent, read-only reviewers included.
A PreToolUse call "touches .claude" when its target is inside <root>\\.claude - Read/Write/Edit/
MultiEdit file_path, NotebookEdit notebook_path, Grep path (or a glob naming .claude), Glob
path/pattern, and any Bash/PowerShell word that resolves inside it. A Grep/Glob with no path that
merely sweeps the project is not counted.
The flag is hookSpecificOutput.additionalContext: the size, the top 5 subfolders, what to prune
first (.claude\\scratch\\) and what to leave to the user.

Speed and frequency: the size comes from a cache (state file beside the hook, see size_guard.py),
re-measured at most once per TTL_SECONDS (the cache window) per project, or OVER_TTL_SECONDS while
over so a prune clears the flag quickly. A walk has a time budget (WALK_BUDGET_SECONDS); a cut-short
walk reports "at least" and is re-measured on the short cadence even when under the limit.
A recipient is the main session or one subagent. While pruning scratch alone could bring the folder
under the limit, the flag fires at most once per cache window (NUDGE_SECONDS = TTL_SECONDS) per
(project, recipient). When even deleting all of scratch could not (user ruling A, 2026-10-20), it
fires ONCE per session per recipient. Either way a SessionStart with source clear or compact flags
the main session again at once, since the earlier flag left its context.
"""
import os
import re

RULE_ID = "S1"
LIMIT_BYTES = 1024 * 1024 * 1024          # 1 GB; --limit-mb= overrides
TTL_SECONDS = 10 * 60                      # re-measure an under-limit folder at most every 10 min
OVER_TTL_SECONDS = 2 * 60                  # ... and an over-limit one every 2 min (prune feedback)
NUDGE_SECONDS = TTL_SECONDS                # flag at most once per cache window per recipient
MAX_RECIPIENTS = 256                       # nudge stamps kept per project (oldest dropped first)
WALK_BUDGET_SECONDS = 3.0                  # stop walking after this and report "at least"
TOP_N = 5
MAX_ROOTS = 64                             # state entries kept (oldest measurement dropped first)
NUDGE_KEEP_SECONDS = 24 * 3600             # per-session nudge stamps older than this are dropped
SOURCE_QUOTE = ('"if the agents local .claude directory gets bigger than 1gb, it needs to prune '
                'any temporary files and address the bloat"')

# top-level .claude children: what the message may call temporary vs. what is the user's call
TEMPORARY = ("scratch",)
KEEP = ("worktrees", "metrics", "docs", "design", "designs", "tickets", "backups", ".backups",
        "backup", ".backup")

TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Grep", "Glob", "Bash", "PowerShell")
FILE_KEYS = {"Read": "file_path", "Write": "file_path", "Edit": "file_path",
             "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}

# a shell word that mentions .claude as a whole segment name (not .claude-old, .claudex)
CLAUDE_WORD = re.compile(r"""[^\s"'`;|&<>()=,]*\.claude(?![\w.-])[^\s"'`;|&<>()=,]*""", re.I)
PROJECT_VAR = re.compile(r"\$\{CLAUDE_PROJECT_DIR\}|\$env:CLAUDE_PROJECT_DIR|\$CLAUDE_PROJECT_DIR"
                         r"|%CLAUDE_PROJECT_DIR%", re.I)


# ------------------------------------------------------------------ which calls reach the check

def candidate_paths(event):
    """Raw path strings this call names, cheap and import-free. A PreToolUse call reaches the size
    check only if one of these mentions `.claude` (the entry point's pre-filter) AND resolves inside
    <root>\\.claude (touched_targets)."""
    tool = event.get("tool_name") or ""
    ti = event.get("tool_input") or {}
    if not isinstance(ti, dict):
        return []
    out = []
    if tool in FILE_KEYS:
        v = ti.get(FILE_KEYS[tool])
        if isinstance(v, str) and v:
            out.append(v)
    elif tool == "Grep":
        base = ti.get("path") if isinstance(ti.get("path"), str) else ""
        if base:
            out.append(base)
        g = ti.get("glob")
        if isinstance(g, str) and ".claude" in g.lower():
            out.append(_join(base or event.get("cwd") or "", _glob_head(g)))
    elif tool == "Glob":
        base = ti.get("path") if isinstance(ti.get("path"), str) else ""
        if base:
            out.append(base)
        p = ti.get("pattern")
        if isinstance(p, str) and ".claude" in p.lower():
            out.append(_join(base or event.get("cwd") or "", _glob_head(p)))
    elif tool in ("Bash", "PowerShell"):
        cmd = ti.get("command")
        if isinstance(cmd, str) and ".claude" in cmd.lower():
            out.extend(m.group(0) for m in CLAUDE_WORD.finditer(cmd))
    return out


def _glob_head(pattern):
    """A glob pattern with any leading `**/` segments dropped (`**/.claude/x` -> `.claude/x`)."""
    p = pattern.strip().replace("\\", "/")
    while p.startswith("**/"):
        p = p[3:]
    return p


def _join(base, rel):
    if not base or re.match(r"^(?:[A-Za-z]:[\\/]|/|\\\\)", rel):
        return rel
    return base.rstrip("\\/") + "\\" + rel


MAIN_EVENTS = ("SessionStart", "UserPromptSubmit")  # reach the main session; need no .claude path


def prefilter(event):
    """True when this event might touch a .claude folder at all - checked before hooklib.paths
    (and its ctypes import) is loaded. SessionStart and UserPromptSubmit always pass."""
    if (event.get("hook_event_name") or "") in MAIN_EVENTS:
        return True
    if (event.get("tool_name") or "") not in TOOLS:
        return False
    return any(".claude" in p.lower() for p in candidate_paths(event))


def claude_dir_for(event):
    """(root, <root>\\.claude) for this event, or (None, None) when there is no root or the
    project root is the home folder (its .claude is ~/.claude, which this rule does not police)."""
    from hooklib import paths
    root = paths.project_root(event)
    if not root:
        return None, None
    cdir = os.path.join(paths.msys_to_windows(root), ".claude")
    home = paths.home_dir()
    if home and os.path.normcase(os.path.normpath(cdir)) == \
            os.path.normcase(os.path.normpath(os.path.join(home, ".claude"))):
        return None, None
    return root, cdir


def touched_targets(event, root, cdir):
    """Every raw candidate path that resolves inside (or to) <root>\\.claude."""
    from hooklib import paths
    hits = []
    for raw in candidate_paths(event):
        r = PROJECT_VAR.sub(lambda _m: root, raw)  # a function's return is used literally
        r = r.strip().strip("\"'")
        if r.startswith("~"):
            continue  # ~/.claude is the home folder's, never the project's
        try:
            target = paths.resolve(r, event)
        except Exception:
            continue
        if paths.is_inside(cdir, target):
            hits.append(raw)
    return hits


# ------------------------------------------------------------------ measuring

def classify(name):
    n = name.lower()
    if n in TEMPORARY:
        return "temporary"
    if n in KEEP or "backup" in n:
        return "keep"
    return "other"


def measure(cdir, budget=WALK_BUDGET_SECONDS, scandir=os.scandir, clock=None, check_every=256):
    """{"bytes", "files", "errors", "ms", "partial", "top": [[name, bytes, kind], ...], "scratch"}
    for one .claude folder. Never follows a symlink or junction (no double count, no cycle). Fails
    open: an unreadable folder or entry counts as an error and adds 0 bytes. Read-only."""
    import time
    clock = clock or time.perf_counter
    t0 = clock()
    sizes = {}
    files = 0
    errors = 0
    partial = False
    loose = 0
    stack = []
    try:
        with scandir(cdir) as it:
            for e in it:
                try:
                    if e.is_symlink() or _is_junction(e):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        sizes[e.name] = 0
                        stack.append((e.name, e.path))
                    else:
                        loose += e.stat(follow_symlinks=False).st_size
                        files += 1
                except OSError:
                    errors += 1
    except OSError:
        errors += 1
    seen = 0
    while stack and not partial:
        top, d = stack.pop()
        try:
            with scandir(d) as it:
                for e in it:
                    seen += 1
                    if seen % check_every == 0 and clock() - t0 > budget:
                        partial = True
                        break
                    try:
                        if e.is_symlink() or _is_junction(e):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append((top, e.path))
                        else:
                            sizes[top] += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
    total = loose + sum(sizes.values())
    ranked = sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    top = [[n, b, classify(n)] for n, b in ranked[:TOP_N]]
    if loose:
        top.append(["(loose files)", loose, "other"])
        top = sorted(top, key=lambda t: -t[1])[:TOP_N]
    scratch = sum(b for n, b in sizes.items() if n.lower() in TEMPORARY)
    return {"bytes": total, "files": files, "errors": errors, "partial": partial,
            "ms": int((clock() - t0) * 1000), "top": top, "scratch": scratch}


def _is_junction(entry):
    f = getattr(entry, "is_junction", None)  # Python 3.12+
    try:
        return bool(f()) if f else False
    except OSError:
        return False


# ------------------------------------------------------------------ the message

def mb(n):
    return "%.1f MB" % (n / 1048576.0)


KIND_LABEL = {"temporary": "temporary", "keep": "keep - user's call", "other": "not classified - user's call"}


MESSAGE_CAP = 9500  # below Claude Code's 10,000-char additionalContext cap (review P5)


def unprunable(entry, limit):
    """True when deleting every byte of scratch would still leave the folder over the limit."""
    return entry.get("bytes", 0) - entry.get("scratch", 0) > limit


def message(cdir, entry, limit, nudge_seconds, subagent=False):
    """The additionalContext text for an over-limit folder, at most MESSAGE_CAP characters. Paths
    and folder names are kept exactly as they are on disk (review P1: a non-ASCII project path must
    stay a usable literal path); the entry point's json.dumps(ensure_ascii=True) escapes them, so
    stdout is still ASCII. `subagent` selects the wording for an agent that is not in charge of the
    work: report the flag, prune only if its brief allows writes."""
    import time
    at_least = "at least " if entry.get("partial") else ""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.get("measured", 0)))
    biggest = "; ".join("%s %s [%s]" % (n, mb(b), KIND_LABEL.get(k, k))
                        for n, b, k in entry.get("top") or [])
    scratch = entry.get("scratch", 0)
    after = entry.get("bytes", 0) - scratch
    parts = [
        "S1 flag (.claude size): %s is %s%s, over the %s limit (user rule, 2026-10-20: %s)."
        % (cdir, at_least, mb(entry.get("bytes", 0)), mb(limit), SOURCE_QUOTE),
        "Measured %s, %d files. Biggest: %s." % (when, entry.get("files", 0), biggest or "-"),
    ]
    if subagent:
        parts.append("You are a subagent: do not stop your task for this. Put this flag in your "
                     "final report so the main session can prune; prune yourself only if your "
                     "brief allows writes there, and then only as below.")
    steps = []
    if scratch > 0:
        steps.append("Prune temporary files first: %s\\scratch\\ (%s) - finished task folders and "
                     "stale outputs only, never a live task's files; delete each by its literal "
                     "absolute path, never a wildcard directory or a variable." % (cdir, mb(scratch)))
    steps.append("Do NOT delete worktrees, metrics, docs, design, backups or tickets, or anything "
                 "not clearly temporary: report them with their sizes and leave the decision to "
                 "the user.")
    if after > limit:
        steps.append("%s it would be %s, still over: tell the user which folders above hold the "
                     "bloat; that part is the user's call."
                     % ("Even with all of scratch gone" if scratch > 0 else "There is no scratch to "
                        "prune, so", mb(after)))
    else:
        steps.append("Pruning scratch is enough to get back under the limit.")
    parts += ["%d) %s" % (i + 1, s) for i, s in enumerate(steps)]
    if after > limit:
        parts.append("(This flag never blocks. Pruning scratch cannot fix this, so it is shown once "
                     "per session, and again after /clear or a compaction.)")
    else:
        parts.append("(This flag never blocks; it repeats at most every %d min, and again after "
                     "/clear or a compaction.)" % (nudge_seconds // 60))
    text = " ".join(parts)
    if len(text) > MESSAGE_CAP:
        text = text[:MESSAGE_CAP - 15] + " ...[truncated]"
    return text


def _ascii(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v \
        and v not in (float("inf"), float("-inf"))


def valid_entry(e):
    """True when a cached root entry has the shape evaluate() relies on (review P2): a dict with
    numeric measured and bytes, optional numeric files/scratch/errors/ms, `top` a list of
    [str, number, str] triples and `nudged` a dict of str -> number. A bad entry is dropped on
    load (and ignored by evaluate), so the folder is simply re-measured."""
    if not isinstance(e, dict) or not _num(e.get("measured")) or not _num(e.get("bytes")):
        return False
    for k in ("files", "scratch", "errors", "ms"):
        if k in e and not _num(e[k]):
            return False
    top = e.get("top", [])
    if not isinstance(top, list) or not all(
            isinstance(t, list) and len(t) == 3 and isinstance(t[0], str) and _num(t[1])
            and isinstance(t[2], str) for t in top):
        return False
    nudged = e.get("nudged", {})
    if not isinstance(nudged, dict) or not all(
            isinstance(k, str) and _num(v) for k, v in nudged.items()):
        return False
    return True


def clean_state(data):
    """`data` as a usable state dict: a fresh one unless it is {"roots": {str: entry}}, with every
    invalid entry dropped."""
    if not isinstance(data, dict) or not isinstance(data.get("roots"), dict):
        return {"version": 1, "roots": {}}
    data["roots"] = {k: v for k, v in data["roots"].items() if isinstance(k, str) and valid_entry(v)}
    return data


# ------------------------------------------------------------------ the decision (cache + nudge)

def recipient(event):
    """Who would read the flag: one subagent (`agent_id`, present only inside a subagent) or the
    main session (keyed by session_id)."""
    aid = event.get("agent_id")
    if aid:
        return "agent:" + str(aid)
    return "main:" + str(event.get("session_id") or "")


def evaluate(event, cfg, state, now, measure_fn=measure):
    """(context_text_or_None, log_rows, state_changed). `cfg` keys: limit, ttl, over_ttl,
    nudge_seconds, emit (bool: False under --log-only, the flag is only logged), budget. `state` is
    the loaded cache dict ({"roots": {...}}), mutated in place. Never raises for a bad folder:
    measure_fn errors fail open (no flag, no state change). Never returns a deny."""
    rows = []
    event_name = event.get("hook_event_name") or "PreToolUse"
    root, cdir = claude_dir_for(event)
    if not cdir:
        return None, rows, False
    targets = []
    if event_name not in MAIN_EVENTS:
        targets = touched_targets(event, root, cdir)
        if not targets:
            return None, rows, False
    roots = state.setdefault("roots", {})
    if not isinstance(roots, dict):
        roots = state["roots"] = {}
    key = state_key(cdir)
    entry = roots.get(key)
    if entry is not None and not valid_entry(entry):
        entry = None  # a malformed cache entry is re-measured, never a crash (review P2)
    changed = False
    limit = cfg["limit"]
    if not _fresh(entry, now, cfg):
        try:
            m = measure_fn(cdir, cfg.get("budget", WALK_BUDGET_SECONDS))
        except Exception:
            return None, rows, False  # fail open
        old_nudged = (entry or {}).get("nudged") or {}
        entry = dict(m)
        entry["measured"] = now
        entry["nudged"] = {s: t for s, t in old_nudged.items() if now - t < NUDGE_KEEP_SECONDS}
        roots[key] = entry
        _cap_roots(roots)
        changed = True
        rows.append(_row("measure", event, event_name, cdir, entry, limit, targets, emitted=False))
    if entry.get("bytes", 0) <= limit:
        return None, rows, changed
    who = recipient(event)
    nudged = entry.setdefault("nudged", {})
    last = nudged.get(who)
    # clear/compact drop the earlier flag from the main session's context: flag again at once
    reset = event_name == "SessionStart" and event.get("source") in ("clear", "compact")
    if unprunable(entry, limit):
        # user ruling A, 2026-10-20: bloat scratch cannot fix is flagged once per session (and
        # again after clear/compact) - for the main session and for each subagent alike
        due = reset or last is None
    else:
        due = reset or last is None or now - last >= cfg["nudge_seconds"]
    if not due:
        return None, rows, changed
    nudged[who] = now
    if len(nudged) > MAX_RECIPIENTS:
        for k in sorted(nudged, key=nudged.get)[:len(nudged) - MAX_RECIPIENTS]:
            nudged.pop(k, None)
    text = message(cdir, entry, limit, cfg["nudge_seconds"], subagent=who.startswith("agent:"))
    row = _row("flag", event, event_name, cdir, entry, limit, targets, emitted=cfg["emit"], who=who)
    row["prev"] = last
    row["unprunable"] = unprunable(entry, limit)
    rows.append(row)
    return (text if cfg["emit"] else None), rows, True


def state_key(cdir):
    return os.path.normcase(os.path.normpath(cdir))


def flag_taken(rows, disk_state):
    """True when, since this process loaded the state, another hook process already flagged the
    same recipient for the same folder (review P4: concurrent events, e.g. parallel tool calls,
    each saw no stamp and each flagged). `disk_state` is the state re-read just before emitting."""
    for r in rows:
        if r.get("kind") != "flag":
            continue
        try:
            stamp = disk_state["roots"][state_key(r["root"])]["nudged"].get(r["who"])
        except (KeyError, TypeError, AttributeError):
            return False
        if _num(stamp) and (r.get("prev") is None or stamp > r["prev"]):
            return True
    return False


def _fresh(entry, now, cfg):
    if not entry or "measured" not in entry:
        return False
    age = now - entry["measured"]
    if age < 0:
        return False  # clock went backwards: re-measure
    # over the limit, or a budget-capped (partial) walk: the short cadence (review P7)
    short = entry.get("bytes", 0) > cfg["limit"] or entry.get("partial")
    return age < (cfg["over_ttl"] if short else cfg["ttl"])


def _cap_roots(roots):
    if len(roots) <= MAX_ROOTS:
        return
    for k in sorted(roots, key=lambda k: roots[k].get("measured", 0))[:len(roots) - MAX_ROOTS]:
        roots.pop(k, None)


def _row(kind, event, event_name, cdir, entry, limit, targets, emitted, who=None):
    from hooklib.output import excerpt
    return {"rule": RULE_ID, "kind": kind, "event": event_name, "tool": event.get("tool_name"),
            "who": who or recipient(event),
            "root": cdir, "cwd": event.get("cwd"), "bytes": entry.get("bytes"),
            "files": entry.get("files"), "errors": entry.get("errors"), "partial": entry.get("partial"),
            "ms": entry.get("ms"), "over": entry.get("bytes", 0) > limit, "limit": limit,
            "target": excerpt(targets[0]) if targets else None, "emitted": bool(emitted),
            "session": event.get("session_id")}


def output_for(event_name, text):
    """The hook JSON carrying `text` as additionalContext - never a permissionDecision."""
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}


# ------------------------------------------------------------------ self-test

FAKE_ROOT = "C:\\S1SelfTest\\proj"
FAKE_CDIR = FAKE_ROOT + "\\.claude"
GB = 1024 * 1024 * 1024

# (tool, tool_input, must reach the size check) - all against FAKE_ROOT as cwd and project dir
TOUCH_CASES = [
    ("Read", {"file_path": FAKE_CDIR + "\\tickets\\T1.md"}, True),
    ("Read", {"file_path": "C:/S1SelfTest/proj/.claude/docs/x.md"}, True),
    ("Write", {"file_path": FAKE_CDIR + "\\scratch\\a.txt"}, True),
    ("Edit", {"file_path": ".claude\\docs\\a.md"}, True),                       # relative to cwd
    ("NotebookEdit", {"notebook_path": FAKE_CDIR + "\\n.ipynb"}, True),
    ("Grep", {"pattern": "x", "path": FAKE_CDIR}, True),
    ("Grep", {"pattern": "x", "path": FAKE_CDIR + "\\scratch"}, True),
    ("Grep", {"pattern": "x", "glob": ".claude/**/*.md"}, True),
    ("Glob", {"pattern": ".claude/**/*.md"}, True),
    ("Glob", {"pattern": "**/.claude/tickets/*.md"}, True),
    ("Glob", {"pattern": "*.md", "path": FAKE_CDIR + "\\docs"}, True),
    ("Bash", {"command": "ls .claude/scratch"}, True),
    ("Bash", {"command": "du -sh /c/S1SelfTest/proj/.claude"}, True),
    ("Bash", {"command": "grep -r foo \"C:\\S1SelfTest\\proj\\.claude\\tickets\""}, True),
    ("Bash", {"command": "cd x && cat $CLAUDE_PROJECT_DIR/.claude/a.md"}, True),
    ("PowerShell", {"command": "Get-ChildItem $env:CLAUDE_PROJECT_DIR\\.claude -Recurse"}, True),
    ("PowerShell", {"command": "Get-Content -Path .claude\\docs\\x.md"}, True),
    ("Read", {"file_path": FAKE_ROOT + "\\src\\main.cpp"}, False),             # outside .claude
    ("Read", {"file_path": FAKE_ROOT + "\\.claude-old\\x.md"}, False),        # sibling name
    ("Read", {"file_path": FAKE_ROOT + "\\foo.claude\\x.md"}, False),
    ("Read", {"file_path": "C:\\Users\\Someone\\.claude\\CLAUDE.md"}, False),  # a home .claude
    ("Read", {"file_path": "C:\\Other\\proj\\.claude\\x.md"}, False),          # another project's
    ("Grep", {"pattern": ".claude"}, False),                                     # no path: a sweep
    ("Grep", {"pattern": "x", "path": FAKE_ROOT}, False),
    ("Glob", {"pattern": "**/*.md"}, False),
    ("Bash", {"command": "cat ~/.claude/CLAUDE.md"}, False),
    ("Bash", {"command": "echo .claude-backup"}, False),
    ("Bash", {"command": "git status"}, False),
    ("Bash", {"command": "ls src/x.claude"}, False),
    ("WebFetch", {"url": "https://x/.claude/y"}, False),                         # not a matched tool
]


CASES_RUN = [0]  # checks run by the last run_self_test(); the entry point adds it to its total


def run_self_test():
    """Returns a list of failure strings. Uses a fake root that does not exist on disk, a fake
    measure function and in-memory state: never builds test data, never writes a file."""
    failures = []
    CASES_RUN[0] = 0
    saved = os.environ.pop("CLAUDE_PROJECT_DIR", None)
    try:
        _self_test(failures)
    finally:
        if saved is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = saved
    return failures


def _ev(tool, ti, **kw):
    ev = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti, "cwd": FAKE_ROOT,
          "session_id": "s1-selftest", "transcript_path": "C:\\Users\\x\\.claude\\projects\\p\\t.jsonl"}
    ev.update(kw)
    return ev


def _cfg(emit=True, limit=GB):
    return {"limit": limit, "ttl": TTL_SECONDS, "over_ttl": OVER_TTL_SECONDS,
            "nudge_seconds": NUDGE_SECONDS, "emit": emit, "budget": WALK_BUDGET_SECONDS}


def _fake(bytes_, calls):
    def fn(cdir, budget):
        calls.append(cdir)
        return {"bytes": bytes_, "files": 10, "errors": 0, "partial": False, "ms": 1,
                "top": [[".backups", bytes_ - 300, "keep"], ["scratch", 200, "temporary"],
                        ["tickets", 100, "keep"]], "scratch": 200}
    return fn


def _self_test(failures):
    def check(ok, msg):
        CASES_RUN[0] += 1
        if not ok:
            failures.append("S1: " + msg)

    os.environ["CLAUDE_PROJECT_DIR"] = FAKE_ROOT
    for tool, ti, want in TOUCH_CASES:
        ev = _ev(tool, ti)
        root, cdir = claude_dir_for(ev)
        got = bool(prefilter(ev) and cdir and touched_targets(ev, root, cdir))
        check(got == want, "%s %r must %sreach the size check" % (tool, ti, "" if want else "not "))
    # transcript_path always contains .claude: the pre-filter must look at tool_input only
    check(not prefilter(_ev("Read", {"file_path": "C:\\S1SelfTest\\proj\\a.txt"})),
          "prefilter passed on transcript_path alone")
    check(prefilter({"hook_event_name": "SessionStart"}), "prefilter must pass every SessionStart")
    check(not prefilter({"tool_name": "Bash", "tool_input": "not a dict"}), "prefilter on a bad tool_input")

    T = 1000000.0
    over, under = 2 * GB, GB // 2
    pover = GB + 100  # over, but deleting its 200 B of scratch brings it under: prunable
    ev_in = _ev("Read", {"file_path": FAKE_CDIR + "\\tickets\\T1.md"})
    ev_out = _ev("Read", {"file_path": FAKE_ROOT + "\\src\\x.cpp"})

    # must fire: over the limit, a path under .claude
    calls, st = [], {}
    text, rows, ch = evaluate(ev_in, _cfg(), st, T, _fake(over, calls))
    check(bool(text) and "S1" in text and "scratch" in text and ch, "over + .claude path must nudge")
    check(len(calls) == 1, "a cold cache must measure once, measured %d" % len(calls))
    check([r["kind"] for r in rows] == ["measure", "flag"], "rows must be measure+flag, got %r"
          % [r["kind"] for r in rows])
    check("2048.0 MB" in (text or "") and "1024.0 MB" in (text or ""), "message lacks the size or limit")
    check("Do NOT delete worktrees" in (text or "") and "user's call" in (text or ""),
          "message must leave non-temporary folders to the user")
    check("still over" in (text or ""), "scratch-is-not-enough line missing when scratch cannot fix it")
    try:
        (text or "").encode("ascii")
        check(len(text or "") < 10000, "message must stay under the 10,000-char cap")
    except UnicodeEncodeError:
        check(False, "message is not ASCII")
    out = output_for("PreToolUse", text)
    hso = out["hookSpecificOutput"]
    check(hso.get("additionalContext") == text and "permissionDecision" not in hso,
          "output must be additionalContext only, never a permissionDecision")
    # cache fresh: same call again, no re-measure and (within NUDGE_SECONDS) no second nudge
    text2, rows2, _ = evaluate(ev_in, _cfg(), st, T + 30, _fake(over, calls))
    check(text2 is None and len(calls) == 1 and rows2 == [],
          "fresh cache within the nudge interval must neither measure nor nudge")
    # over-TTL passed: re-measure, still within nudge interval: no nudge
    text3, rows3, _ = evaluate(ev_in, _cfg(), st, T + OVER_TTL_SECONDS + 1, _fake(over, calls))
    check(text3 is None and len(calls) == 2 and [r["kind"] for r in rows3] == ["measure"],
          "a stale over-limit cache must re-measure after OVER_TTL_SECONDS but not re-nudge")
    # ruling A: `over` is unprunable (2 GB, 200 B of scratch) - no repeat after the window, ever
    text4, _r, _ = evaluate(ev_in, _cfg(), st, T + NUDGE_SECONDS + 1, _fake(over, calls))
    check(text4 is None, "ruling A: unprunable bloat must not re-flag after one window")
    check(evaluate(ev_in, _cfg(), st, T + 20 * NUDGE_SECONDS, _fake(over, calls))[0] is None,
          "ruling A: unprunable bloat must not re-flag the same session hours later")
    check("once per session" in (text or "") and "every 10 min" not in (text or ""),
          "ruling A: the unprunable flag must say it is shown once per session")
    # prunable over-limit (scratch alone fixes it): repeats once per cache window
    pc, pst = [], {}
    tp = evaluate(ev_in, _cfg(), pst, T, _fake(pover, pc))[0]
    check(bool(tp) and "Pruning scratch is enough" in tp and "every 10 min" in tp,
          "a prunable over-limit folder must flag with the 10-min wording")
    check(evaluate(ev_in, _cfg(), pst, T + NUDGE_SECONDS - 1, _fake(pover, pc))[0] is None,
          "prunable: no second flag inside one window")
    check(bool(evaluate(ev_in, _cfg(), pst, T + NUDGE_SECONDS + 1, _fake(pover, pc))[0]),
          "prunable: the flag must repeat after one cache window")
    # a different session gets its own first nudge
    text5, _r, _ = evaluate(_ev("Read", ev_in["tool_input"], session_id="other"), _cfg(), st,
                            T + NUDGE_SECONDS + 2, _fake(over, calls))
    check(bool(text5), "a second session must get its own first nudge")
    # SessionStart: startup nudges a new session; compact re-nudges the same one at once
    ss = {"hook_event_name": "SessionStart", "source": "startup", "cwd": FAKE_ROOT, "session_id": "s-new"}
    check(bool(evaluate(ss, _cfg(), st, T + NUDGE_SECONDS + 3, _fake(over, calls))[0]),
          "SessionStart over the limit must nudge")
    ss_c = dict(ss, source="compact")
    check(bool(evaluate(ss_c, _cfg(), st, T + NUDGE_SECONDS + 4, _fake(over, calls))[0]),
          "SessionStart after compact must re-nudge")
    ss_r = dict(ss, source="resume")
    check(evaluate(ss_r, _cfg(), st, T + NUDGE_SECONDS + 5, _fake(over, calls))[0] is None,
          "SessionStart resume within the interval must not re-nudge")

    # routing (user ruling 2026-10-20): the main session via UserPromptSubmit, a subagent via its
    # own PreToolUse; each is its own recipient, and neither suppresses the other
    calls, st = [], {}
    ups = {"hook_event_name": "UserPromptSubmit", "prompt": "go on", "cwd": FAKE_ROOT,
           "session_id": "pm", "transcript_path": "C:\\Users\\x\\.claude\\projects\\p\\t.jsonl"}
    t_main = evaluate(ups, _cfg(), st, T, _fake(pover, calls))[0]
    check(bool(t_main) and "You are a subagent" not in t_main,
          "UserPromptSubmit over the limit must flag the main session (main wording)")
    check(prefilter(ups), "prefilter must pass every UserPromptSubmit")
    check(evaluate(ups, _cfg(), st, T + 60, _fake(pover, calls))[0] is None,
          "UserPromptSubmit within the cache window must not flag again")
    ev_sub = _ev("Grep", {"pattern": "x", "path": FAKE_CDIR}, session_id="pm", agent_id="a1",
                 agent_type="Explore")
    t_sub = evaluate(ev_sub, _cfg(), st, T + 61, _fake(pover, calls))[0]
    check(bool(t_sub) and "You are a subagent" in t_sub and "final report" in t_sub,
          "a subagent touching .claude must get its own flag, with the subagent wording")
    check(evaluate(ev_sub, _cfg(), st, T + 62, _fake(pover, calls))[0] is None,
          "the same subagent must not be flagged twice in one window")
    ev_sub2 = dict(ev_sub, agent_id="a2")
    check(bool(evaluate(ev_sub2, _cfg(), st, T + 63, _fake(pover, calls))[0]),
          "a second subagent must get its own flag")
    ev_main_tool = _ev("Read", {"file_path": FAKE_CDIR + "\\x.md"}, session_id="pm")
    check(evaluate(ev_main_tool, _cfg(), st, T + 64, _fake(pover, calls))[0] is None,
          "the main session's own PreToolUse shares the main recipient: no second flag")
    check(bool(evaluate(ups, _cfg(), st, T + NUDGE_SECONDS + 1, _fake(pover, calls))[0]),
          "the main session must be flagged again after one cache window")
    check(len(calls) <= 2, "one project must be measured at most once per window (+1 over-TTL), got %d"
          % len(calls))
    check(recipient({"session_id": "s"}) == "main:s" and recipient({"agent_id": "z"}) == "agent:z",
          "recipient keys")
    # a subagent touching a path outside .claude is never flagged, even when over
    check(evaluate(_ev("Read", {"file_path": FAKE_ROOT + "\\a.cpp"}, agent_id="a3"), _cfg(), st,
                   T + 65, _fake(over, calls))[0] is None, "a subagent outside .claude must not be flagged")

    # ruling A routing: unprunable -> once per session for the main session AND each subagent;
    # SessionStart clear/compact re-flags the main session, resume does not
    ac, ast = [], {}
    ups_a = dict(ups, session_id="pmA")
    check(bool(evaluate(ups_a, _cfg(), ast, T, _fake(over, ac))[0]), "ruling A: first main flag")
    for dt in (NUDGE_SECONDS + 1, 5 * NUDGE_SECONDS):
        check(evaluate(ups_a, _cfg(), ast, T + dt, _fake(over, ac))[0] is None,
              "ruling A: main session must not re-flag at +%ds" % dt)
    ss_a = {"hook_event_name": "SessionStart", "cwd": FAKE_ROOT, "session_id": "pmA"}
    check(evaluate(dict(ss_a, source="resume"), _cfg(), ast, T + 6 * NUDGE_SECONDS,
                   _fake(over, ac))[0] is None, "ruling A: resume must not re-flag")
    for src in ("clear", "compact"):
        check(bool(evaluate(dict(ss_a, source=src), _cfg(), ast, T + 7 * NUDGE_SECONDS,
                            _fake(over, ac))[0]), "ruling A: SessionStart %s must re-flag" % src)
    check(bool(evaluate(dict(ss_a, source="startup", session_id="pmB"), _cfg(), ast,
                        T + 7 * NUDGE_SECONDS, _fake(over, ac))[0]), "ruling A: a new session flags")
    ev_sa = _ev("Grep", {"pattern": "x", "path": FAKE_CDIR}, session_id="pmA", agent_id="subA")
    check(bool(evaluate(ev_sa, _cfg(), ast, T + 8 * NUDGE_SECONDS, _fake(over, ac))[0]),
          "ruling A: a subagent's first touch flags it")
    check(evaluate(ev_sa, _cfg(), ast, T + 10 * NUDGE_SECONDS, _fake(over, ac))[0] is None,
          "ruling A: the same subagent must not re-flag after windows pass")

    # P1: a non-ASCII project path stays exact in the text; the JSON is still ASCII
    upath = "C:\\Pr\u00f8j\u00e9ct\\.claude"
    ut = message(upath, {"bytes": 2 * GB, "scratch": 5, "files": 1, "measured": T,
                         "top": [["scr\u00e4tch-old", 5, "other"]]}, GB, NUDGE_SECONDS)
    check(upath + "\\scratch\\" in ut and "scr\u00e4tch-old" in ut and "?" not in ut,
          "P1: a non-ASCII path must stay a usable literal path in the flag")
    import json as _json
    dumped = _json.dumps(output_for("PreToolUse", ut), ensure_ascii=True)
    check(dumped.isascii() and _json.loads(dumped)["hookSpecificOutput"]["additionalContext"] == ut,
          "P1: json.dumps(ensure_ascii=True) must carry the exact path in ASCII")
    # P5: the text is capped below the 10,000-char limit
    big = {"bytes": 2 * GB, "scratch": 5, "files": 1, "measured": T,
           "top": [["x" * 4000, 1, "other"]] * 5}
    bt = message(FAKE_CDIR, big, GB, NUDGE_SECONDS, subagent=True)
    check(len(bt) <= MESSAGE_CAP < 10000 and bt.endswith("[truncated]"), "P5: cap at MESSAGE_CAP")
    # scratch == 0: no "prune scratch first" step; it says there is nothing to prune
    zt = message(FAKE_CDIR, {"bytes": 2 * GB, "scratch": 0, "files": 1, "measured": T,
                             "top": [["worktrees", 2 * GB, "keep"]]}, GB, NUDGE_SECONDS)
    check("Prune temporary files first" not in zt and "There is no scratch to prune" in zt
          and zt.count("1) ") == 1 and "3) " not in zt, "scratch==0 wording: %r" % zt[-300:])
    # P2: malformed cache entries are rejected and re-measured, never a crash
    good = {"bytes": 5, "measured": T, "nudged": {"main:x": T}, "top": [["a", 1, "keep"]]}
    check(valid_entry(good), "P2: a well-formed entry is valid")
    bads = [5, "x", None, [], dict(good, measured="abc"), dict(good, bytes="x"),
            dict(good, bytes=True), dict(good, nudged=[]), dict(good, nudged={"main:x": "t"}),
            dict(good, top=5), dict(good, top=[[1]]), dict(good, top=[["a", "b", "c"]]),
            dict(good, files="7"), dict(good, measured=float("nan"))]
    for bad in bads:
        check(not valid_entry(bad), "P2: %r must be invalid" % (bad,))
        bc = []
        st_bad = {"roots": {state_key(FAKE_CDIR): bad}}
        try:
            r = evaluate(ev_in, _cfg(), st_bad, T, _fake(over, bc))
            ok = bool(r[0]) and len(bc) == 1 and valid_entry(st_bad["roots"][state_key(FAKE_CDIR)])
        except Exception as e:
            ok = False
            bc.append(e)
        check(ok, "P2: evaluate over a bad entry %r must re-measure and flag, not raise (%r)" % (bad, bc))
    cs = clean_state({"roots": {"a": good, "b": 5, 7: good, "c": dict(good, top=5)}})
    check(list(cs["roots"]) == ["a"], "P2: clean_state keeps only valid entries, got %r" % list(cs["roots"]))
    check(clean_state({"roots": 5}) == {"version": 1, "roots": {}} and clean_state([1])["roots"] == {},
          "P2: a wrongly typed state loads fresh")
    check(evaluate(ev_in, _cfg(), {"roots": 5}, T, _fake(over, []))[0] is not None,
          "P2: a non-dict roots must not raise")
    # P7: a partial walk under the limit is re-measured on the short cadence
    part_c, part_st = [], {}

    def partial_fake(cdir, budget):
        part_c.append(cdir)
        return {"bytes": under, "files": 1, "errors": 0, "partial": True, "ms": 3000, "top": [],
                "scratch": 0}
    evaluate(ev_in, _cfg(), part_st, T, partial_fake)
    evaluate(ev_in, _cfg(), part_st, T + OVER_TTL_SECONDS + 1, partial_fake)
    check(len(part_c) == 2, "P7: a partial under-limit walk must re-measure after OVER_TTL_SECONDS")
    # P4: a concurrent process's newer stamp suppresses this process's duplicate flag
    d_st = {}
    _t, d_rows, _c = evaluate(ev_in, _cfg(), d_st, T, _fake(over, []))
    disk = {"roots": {state_key(FAKE_CDIR): {"nudged": {recipient(ev_in): T - 1}}}}
    check(flag_taken(d_rows, disk), "P4: another process's stamp must mark the flag as taken")
    check(not flag_taken(d_rows, {"roots": {}}), "P4: no stamp on disk: not taken")
    p_st = {"roots": {state_key(FAKE_CDIR): {"bytes": pover, "scratch": 200, "files": 1,
                                             "measured": T, "top": [], "nudged": {recipient(ev_in): T}}}}
    _t, r_rows, _c = evaluate(ev_in, _cfg(), p_st, T + NUDGE_SECONDS + 1, _fake(pover, []))
    check(any(r["kind"] == "flag" and r["prev"] == T for r in r_rows), "P4 setup: prev must be T")
    own = {"roots": {state_key(FAKE_CDIR): {"nudged": {recipient(ev_in): T}}}}
    check(not flag_taken(r_rows, own), "P4: our own earlier stamp (== prev) must not suppress")

    # must not fire: under the limit
    calls, st = [], {}
    text, rows, _ = evaluate(ev_in, _cfg(), st, T, _fake(under, calls))
    check(text is None and [r["kind"] for r in rows] == ["measure"], "under the limit must not nudge")
    evaluate(ev_in, _cfg(), st, T + TTL_SECONDS - 1, _fake(under, calls))
    check(len(calls) == 1, "an under-limit cache must stay fresh for TTL_SECONDS")
    evaluate(ev_in, _cfg(), st, T + TTL_SECONDS + 1, _fake(under, calls))
    check(len(calls) == 2, "an under-limit cache must re-measure after TTL_SECONDS")
    # must not fire: a path outside .claude (and must not even measure)
    calls, st = [], {}
    text, rows, ch = evaluate(ev_out, _cfg(), st, T, _fake(over, calls))
    check(text is None and not calls and not rows and not ch, "a path outside .claude must not measure")
    # log-only: over, but nothing emitted; the would-be nudge is still logged
    calls, st = [], {}
    text, rows, _ = evaluate(ev_in, _cfg(emit=False), st, T, _fake(over, calls))
    check(text is None and any(r["kind"] == "flag" and r["emitted"] is False for r in rows),
          "log-only must emit nothing but log the nudge with emitted:false")
    # unreadable folder: measure raising fails open; a real walk over an unreadable dir counts 0
    def boom(cdir, budget):
        raise PermissionError("selftest")
    text, rows, ch = evaluate(ev_in, _cfg(), {}, T, boom)
    check(text is None and not rows and not ch, "a raising measure must fail open")

    def denied(path):
        raise PermissionError(path)
    m = measure(FAKE_CDIR, scandir=denied)
    check(m["bytes"] == 0 and m["errors"] == 1, "an unreadable .claude must measure 0 bytes, 1 error")
    check(evaluate(ev_in, _cfg(), {}, T, lambda c, b: measure(c, b, scandir=denied))[0] is None,
          "an unreadable .claude must not nudge")
    m = measure(FAKE_CDIR)  # does not exist on disk
    check(m["bytes"] == 0 and m["errors"] == 1, "a missing .claude must measure 0 bytes")
    # home folder as project root: ~/.claude is not policed
    from hooklib import paths
    home = paths.home_dir()
    if home:
        os.environ["CLAUDE_PROJECT_DIR"] = home
        ev_home = _ev("Read", {"file_path": os.path.join(home, ".claude", "CLAUDE.md")}, cwd=home)
        check(evaluate(ev_home, _cfg(), {}, T, _fake(over, []))[0] is None,
              "a session rooted at home must not police ~/.claude")
        os.environ["CLAUDE_PROJECT_DIR"] = FAKE_ROOT
    # a real, read-only walk of this hook's own rules/ folder with a tiny threshold
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    m = measure(here)
    check(m["bytes"] > 0 and m["files"] > 0 and m["top"] and not m["partial"],
          "a real walk of rules/ must find bytes and a top list, got %r" % ({k: m[k] for k in
                                                                              ("bytes", "files", "partial")},))
    check(m["bytes"] > 1, "tiny threshold: the real walk must be over a 1-byte limit")
    # the time budget: a clock that jumps past the budget makes the walk partial
    ticks = iter([0.0] + [100.0] * 100000)
    m = measure(here, budget=0.5, clock=lambda: next(ticks), check_every=1)
    check(m["partial"], "a walk past its budget must be marked partial")
    # partial walk -> "at least" wording
    entry = {"bytes": over, "files": 1, "partial": True, "top": [], "scratch": 0, "measured": T}
    check("at least" in message(FAKE_CDIR, entry, GB, NUDGE_SECONDS), "partial walk must say 'at least'")
    # scratch alone enough -> no 'still over'
    entry = {"bytes": GB + 10, "files": 1, "top": [["scratch", 100, "temporary"]], "scratch": 100,
             "measured": T}
    t = message(FAKE_CDIR, entry, GB, NUDGE_SECONDS)
    check("Pruning scratch is enough" in t and "still over" not in t, "scratch-is-enough wording")
    # classify
    for name, want in (("scratch", "temporary"), ("Scratch", "temporary"), ("worktrees", "keep"),
                       ("metrics", "keep"), ("docs", "keep"), ("design", "keep"), (".backups", "keep"),
                       ("tickets", "keep"), ("db-backups", "keep"), ("hooks", "other"), ("evidence", "other")):
        check(classify(name) == want, "classify(%r) must be %r" % (name, want))
    # state cap
    roots = {"k%d" % i: {"measured": i} for i in range(MAX_ROOTS + 5)}
    _cap_roots(roots)
    check(len(roots) == MAX_ROOTS and "k0" not in roots and "k%d" % (MAX_ROOTS + 4) in roots,
          "state must keep the newest MAX_ROOTS roots")

