"""Global hook: X3, a shell command must not make an interpreter read its program from stdin
(user rule, 2026-10-31, "100% CPU util is not acceptable"; global CLAUDE.md "Working habits": no
heredocs, no `python -`). `python -`, `python <<EOF`, `cat x | python`, a bare `python`, the same for
node / ruby / perl, and a shell or PowerShell fed by a heredoc, here-string, pipe or `-s` can hang on
stdin and spin a core for hours (measured 2026-10-31: three such leftovers). Write the script to a
file and run `timeout 600 python <file>` instead.

LOG-ONLY and advisory: it never denies unless `--enforce=X3` is on its command line (not shipped).
Wire it on ONE event, for the shell tools:

    PreToolUse   (matcher Bash|PowerShell)

See stdin_guard_settings_snippet.json. A separate entry point from write_guard.py on purpose:
write_guard's semantics are deny-or-log for writes and deletes; this hook judges only what an
interpreter is told to read. Nothing here belongs to one project: the root is the session's own
CLAUDE_PROJECT_DIR and the log lives in that project's .claude/.

    hooklib/command.py           parses a Bash / PowerShell command line into segments (new file)
    rules/execute/x3_interpreter_stdin.py   the rule, its message and its self-test cases

    python -I -S stdin_guard.py                 (shipped: log-only)
    python -I -S stdin_guard.py --self-test
    python -I -S stdin_guard.py --time=N        (ms per in-process call)
  flags: --enforce=X3   the only id allowed to deny (not shipped)
         --root=PATH    override CLAUDE_PROJECT_DIR
         --log=PATH     hit log; must lie inside <root>/.claude/hooks/log/ or it is ignored
  CLAUDE_STDIN_GUARD=log in the environment forces log-only even with --enforce (a kill switch).

Root: CLAUDE_PROJECT_DIR (or --root). Unset or not a directory: the hook judges nothing and writes
nothing - never a log in an arbitrary cwd. The hit log is <root>/.claude/hooks/log/stdin_guard_hits.jsonl
and is written only when <root>/.claude already exists (the hook never creates a .claude in a repo);
it is rotated at 5 MB. Fail open: any exception exits 0 and prints nothing. Output is ASCII only."""
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # python -I leaves the script's own directory off sys.path

try:
    from hooklib import command  # noqa: E402
    from rules.execute import x3_interpreter_stdin as x3  # noqa: E402
except Exception:  # fail open: a broken install must never block a tool call
    command = None
    x3 = None

LOG_NAME = "stdin_guard_hits.jsonl"
LOG_SUBDIR = (".claude", "hooks", "log")
LOG_CAP_BYTES = 5 * 1024 * 1024
EXCERPT_MAX = 160
TOOLS = ("Bash", "PowerShell")
SELFTEST_ROOT = os.path.join(HERE, "stdin_guard_selftest_root")
STATE_READ_CAP = 16 * 1024 * 1024


def ascii_only(text):
    try:
        return str(text).encode("ascii", "replace").decode("ascii")
    except Exception:
        return "?"


def excerpt(text):
    text = ascii_only(text).replace("\t", "    ").strip()
    return text if len(text) <= EXCERPT_MAX else text[:EXCERPT_MAX - 3] + "..."


def cli_value(argv, name, default=None):
    for arg in argv:
        if arg.startswith(name + "="):
            return arg[len(name) + 1:]
    return default


def enforce_ids(argv):
    if os.environ.get("CLAUDE_STDIN_GUARD", "").lower() == "log":
        return set()
    raw = cli_value(argv, "--enforce", "") or ""
    return set(x.strip().upper() for x in raw.split(",") if x.strip())


def resolve_root(argv):
    root = cli_value(argv, "--root") or os.environ.get("CLAUDE_PROJECT_DIR") or ""
    root = root.strip().strip('"').replace("\\", "/")
    return root if root and os.path.isdir(root) else None


def log_path(root, argv):
    """The hit log, or None when <root>/.claude does not exist. --log must lie inside <root>/.claude/hooks/log/."""
    if not os.path.isdir(os.path.join(root, ".claude")):
        return None
    default = os.path.join(root, *LOG_SUBDIR, LOG_NAME)
    override = cli_value(argv, "--log")
    if override:
        try:
            allowed = os.path.realpath(os.path.join(root, *LOG_SUBDIR))
            if os.path.realpath(override).lower().startswith(allowed.lower() + os.sep):
                return override
        except Exception:
            pass
    return default


def write_rows(rows, path):
    if not rows or not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) >= LOG_CAP_BYTES:
                old = path + ".1"
                if os.path.exists(old):
                    os.remove(old)
                os.replace(path, old)
        except OSError:
            pass
        with open(path, "a", encoding="ascii", errors="replace", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    except Exception:
        pass


def deny_verdict(reasons):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": "\n".join(ascii_only(r) for r in reasons)}}


def process(event, argv, root=None):
    """One hook call. Returns the verdict dict (None = allow)."""
    t0 = time.perf_counter()
    if command is None or x3 is None or not isinstance(event, dict):
        return None
    if root is None:
        root = resolve_root(argv)
    if root is None:
        return None
    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input")
    if tool not in TOOLS or not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return None
    cmd = command.parse(tool_input["command"])
    if cmd.truncated:
        return None
    hits = x3.check(cmd) or []
    if not hits:
        return None
    ms = (time.perf_counter() - t0) * 1000.0
    mode = "enforce" if x3.ID in enforce_ids(argv) else "log"
    session = str(event.get("session_id") or "")[:12]
    rows = [{"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "rule": x3.ID, "mode": mode, "tool": tool,
             "line": int(h.get("line") or 0), "excerpt": excerpt(h.get("excerpt") or ""),
             "msg": ascii_only(h.get("message") or ""), "session": ascii_only(session), "ms": round(ms, 2)}
            for h in hits[:50]]
    write_rows(rows, log_path(root, argv))
    if mode == "enforce":
        return deny_verdict(["%s %s" % (x3.ID, h.get("message") or x3.TITLE) for h in hits[:1]])
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in argv:
        return self_test()
    if cli_value(argv, "--time"):
        return timing(argv)
    try:
        text = sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
        event = json.loads(text) if text.strip() else {}
        verdict = process(event, argv)
        if verdict:
            sys.stdout.write(json.dumps(verdict, ensure_ascii=True) + "\n")
            sys.stdout.flush()
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------- self-test

def say(line):
    try:
        sys.stdout.write(ascii_only(line) + "\n")
    except Exception:
        pass


def file_state(path):
    """(exists, size, sha256) of a file; (False, 0, '') when it is absent."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(STATE_READ_CAP)
        return (True, len(data), hashlib.sha256(data).hexdigest())
    except OSError:
        return (False, 0, "")


def read_rows(path):
    try:
        with open(path, "r", encoding="ascii") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except Exception:
        return []


def make_root():
    os.makedirs(os.path.join(SELFTEST_ROOT, *LOG_SUBDIR), exist_ok=True)
    return SELFTEST_ROOT


def remove_root():
    """Remove the self-test scratch root next to this script; it refuses any other path."""
    if os.path.basename(SELFTEST_ROOT) == "stdin_guard_selftest_root":
        shutil.rmtree(SELFTEST_ROOT, ignore_errors=True)


def run_cases(mod):
    passed = 0
    failed = 0
    notes = []
    for case in getattr(mod, "CASES", []):
        try:
            hits = mod.check(command.parse(case["command"]))
            expect = case["expect"]
            ok = len(hits) >= 1 if expect is True else (len(hits) == 0 if expect is False else len(hits) == int(expect))
        except Exception as exc:
            ok = False
            hits = []
            notes.append("  %s %s: raised %r" % (mod.ID, case.get("name"), exc))
        if ok:
            passed += 1
        else:
            failed += 1
            notes.append("  %s %s: expected %r, got %d hit(s)" % (mod.ID, case.get("name"), case["expect"], len(hits)))
    return passed, failed, notes


def self_test():
    if command is None or x3 is None:
        say("IMPORT FAIL hooklib.command or rules.execute.x3_interpreter_stdin")
        return 1
    env_root = os.environ.get("CLAUDE_PROJECT_DIR") or ""
    real_log = os.path.join(env_root, *LOG_SUBDIR, LOG_NAME) if env_root else ""
    real_before = file_state(real_log) if real_log else None
    passed, failed, notes = run_cases(x3)
    say("%-4s %-48s %2d pass %2d fail" % (x3.ID, x3.TITLE[:48], passed, failed))
    for note in notes:
        say(note)
    root = make_root()
    log_arg = "--log=" + os.path.join(root, *LOG_SUBDIR, LOG_NAME)
    own_log = os.path.join(root, *LOG_SUBDIR, LOG_NAME)

    def bash(text, tool="Bash"):
        return {"session_id": "selftest", "tool_name": tool, "tool_input": {"command": text}}

    def rows():
        return read_rows(own_log)

    def reset():
        try:
            os.remove(own_log)
        except OSError:
            pass

    checks = []
    reset()
    verdict = process(bash("python - <<'EOF'\nprint(1)\nEOF"), [log_arg], root=root)
    checks.append(("python - heredoc is logged, never denied", verdict is None and [r["rule"] for r in rows()] == ["X3"]))
    reset()
    process(bash("timeout 600 python tools/run.py --workers 4"), [log_arg], root=root)
    checks.append(("a plain python script logs nothing", rows() == []))
    reset()
    process(bash("python -", tool="PowerShell"), [log_arg], root=root)
    checks.append(("PowerShell is judged too", len(rows()) == 1))
    reset()
    process({"tool_name": "Write", "tool_input": {"command": "python -"}}, [log_arg], root=root)
    process({"tool_name": "Read", "tool_input": {"file_path": "x"}}, [log_arg], root=root)
    checks.append(("other tools are not judged", rows() == []))
    reset()
    verdict = process(bash("python -"), ["--enforce=X3", log_arg], root=root)
    ok = verdict is not None and "X3" in verdict["hookSpecificOutput"]["permissionDecisionReason"]
    saved = os.environ.get("CLAUDE_STDIN_GUARD")
    os.environ["CLAUDE_STDIN_GUARD"] = "log"
    try:
        killed = process(bash("python -"), ["--enforce=X3", log_arg], root=root) is None
    finally:
        if saved is None:
            os.environ.pop("CLAUDE_STDIN_GUARD", None)
        else:
            os.environ["CLAUDE_STDIN_GUARD"] = saved
    checks.append(("--enforce=X3 denies and the kill switch stops it", ok and killed))
    reset()
    stray = os.path.join(root, ".claude", "hooks", "..", "stray_stdin.jsonl")
    process(bash("python -"), ["--log=" + stray], root=root)
    checks.append(("--log outside the log dir is ignored", not os.path.exists(stray) and len(rows()) == 1))
    reset()
    bare = os.path.join(HERE, "stdin_guard_selftest_bare")
    os.makedirs(bare, exist_ok=True)
    try:
        process(bash("python -"), [], root=bare)
        no_dot_claude = not os.path.exists(os.path.join(bare, ".claude"))
    finally:
        shutil.rmtree(bare, ignore_errors=True)
    checks.append(("no .claude in the root: nothing is created", no_dot_claude))
    saved_env = os.environ.pop("CLAUDE_PROJECT_DIR", None)
    try:
        unset_ok = process(bash("python -"), ["--enforce=X3"]) is None
    finally:
        if saved_env is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = saved_env
    checks.append(("no CLAUDE_PROJECT_DIR fails open", unset_ok))
    garbage_ok = True
    for g in (None, "x", {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": "x"}, {"tool_name": "Bash", "tool_input": {"command": 5}}):
        try:
            garbage_ok = garbage_ok and process(g, [log_arg], root=root) is None
        except Exception:
            garbage_ok = False
    checks.append(("malformed events fail open", garbage_ok))
    worst = 0.0
    for text in ["python - " * 8000, "python - <<EOF\n" + "x\n" * 100000, "a & " * 20000, "echo '" * 30000, "$(" * 20000,
                 "cmd /c \"" * 5000, "(" * 50000, "a | " * 40000, "@'\n" * 20000, "x" * 190000]:
        t0 = time.perf_counter()
        try:
            process(bash(text), [log_arg], root=root)
        except Exception:
            garbage_ok = False
        worst = max(worst, (time.perf_counter() - t0) * 1000.0)
    checks.append(("adversarial inputs are fast (worst %.0f ms)" % worst, worst < 1500 and garbage_ok))
    remove_root()
    if real_log:
        checks.append(("the session's real hit log is byte-identical before and after", file_state(real_log) == real_before))
    checks.append(("the self-test scratch root is gone", not os.path.exists(SELFTEST_ROOT)))
    for name, ok in checks:
        say("E2E  %s: %s" % (name, "pass" if ok else "FAIL"))
        passed += int(bool(ok))
        failed += int(not ok)
    say("self-test: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


def timing(argv):
    n = int(cli_value(argv, "--time", "50") or 50)
    root = make_root()
    log_arg = "--log=" + os.path.join(root, *LOG_SUBDIR, LOG_NAME)
    event = {"tool_name": "Bash", "tool_input": {"command": "cd tools && python - <<EOF\nx\nEOF"}}
    t0 = time.perf_counter()
    for _ in range(n):
        process(event, [log_arg], root=root)
    per = (time.perf_counter() - t0) * 1000.0 / max(n, 1)
    remove_root()
    say("in-process: %.2f ms per call over %d calls (excludes interpreter start-up)" % (per, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
