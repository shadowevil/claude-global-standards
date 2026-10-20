"""Global hook: flags a project's .claude/ folder once it passes 1 GB (rule S1, user rule 2026-10-20;
see rules/size/s1_claude_dir_size.py for the rule, its message and its self-test cases).

It only ever FLAGS (user ruling, 2026-10-20: "It mostly needs to be a flag to the agent/PM to prune
it. Not necessarily blocking."): it prints hookSpecificOutput.additionalContext and never a
permissionDecision, never deletes, never blocks. Wire it for three events (see
size_guard_settings_snippet.json):

    SessionStart       (matcher startup|resume|clear|compact)  -> the main session, at start
    UserPromptSubmit   (no matcher)                            -> the main session, each prompt
    PreToolUse         (Read|Grep|Glob|Write|Edit|MultiEdit|NotebookEdit|Bash|PowerShell)
                                                               -> whoever calls: main or subagent

This is a thin entry point: it reads stdin, runs a cheap pre-filter (a PreToolUse call whose
tool_input never mentions `.claude` exits before hooklib.paths and its ctypes import load), then
the rule module, then saves the cache, logs, and prints the flag. Nothing here is project-specific:
the root is CLAUDE_PROJECT_DIR, else the event's cwd.

Cache: ONE small state file beside this script, `size_guard_state.jsonl` (one JSON line keyed by
project .claude path) - not inside each project's .claude/, so the hook never creates a file in a
project tree (which would be untracked noise there and part of the very folder it measures). The
.jsonl name keeps it gitignored here and skipped by install.py. Written atomically (temp file in
the same folder, then os.replace); concurrent sessions are last-writer-wins, which at worst costs
one extra measurement.

    python -I -S size_guard.py                         (shipped: flag from day one - user ruling B,
                                                        2026-10-20 - print additionalContext when over)
    python -I -S size_guard.py --log-only              (option: log rows, print nothing)
    python -I -S size_guard.py --self-test
  flags: --limit-mb=<n> (default 1024), --ttl-minutes=<n> (10), --over-ttl-minutes=<n> (2),
         --budget-seconds=<n> (3), --state-path=<file>, --hits-path=<file>
  CLAUDE_SIZE_GUARD=log in the environment also means log-only (a kill switch).
Fail open: any exception exits 0 and prints nothing.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # python -I leaves the script's own directory off sys.path

try:
    from rules.size import s1_claude_dir_size as s1  # noqa: E402  (imports only os and re)
except Exception:  # a broken or missing rules/ must exit 0 silently (review P6)
    s1 = None

STATE_PATH = os.path.join(HERE, "size_guard_state.jsonl")
HITS_PATH = os.path.join(HERE, "size_guard_hits.jsonl")
SELFTEST_STATE = os.path.join(HERE, "size_guard_state.selftest.jsonl")
SELFTEST_HITS = os.path.join(HERE, "size_guard_hits.selftest.jsonl")
ENV_KILL = "CLAUDE_SIZE_GUARD"
SNIPPET = os.path.join(HERE, "size_guard_settings_snippet.json")

NUMERIC_FLAGS = {"limit-mb": ("limit", 1024 * 1024), "ttl-minutes": ("ttl", 60),
                 "over-ttl-minutes": ("over_ttl", 60), "budget-seconds": ("budget", 1)}
PATH_FLAGS = ("state-path", "hits-path")
KNOWN_FLAGS = ("log-only", "self-test") + tuple(NUMERIC_FLAGS) + PATH_FLAGS


def _ascii(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def parse_flags(argv):
    """(cfg, paths, issues). Flag names are tolerant like write_guard's (case, '_' or '-'). A bad
    or unknown flag is recorded in `issues` and ignored, never a crash. There is no --enforce:
    S1 never denies, so the only switch is --log-only (flag vs. log only)."""
    cfg = {"limit": s1.LIMIT_BYTES, "ttl": s1.TTL_SECONDS, "over_ttl": s1.OVER_TTL_SECONDS,
           "nudge_seconds": s1.NUDGE_SECONDS, "budget": s1.WALK_BUDGET_SECONDS, "emit": True}
    paths = {"state-path": STATE_PATH, "hits-path": HITS_PATH}
    issues = []
    for a in argv:
        if not a.startswith("--"):
            issues.append("stray-argument:%s" % _ascii(a)[:40])
            continue
        name, sep, value = a[2:].partition("=")
        name = name.lower().replace("_", "-")
        if name not in KNOWN_FLAGS:
            issues.append("unrecognized-flag:%s" % _ascii(a)[:40])
        elif name == "log-only":
            if not sep or value.strip().lower() in ("1", "true", "yes"):
                cfg["emit"] = False
        elif name in NUMERIC_FLAGS:
            key, scale = NUMERIC_FLAGS[name]
            try:
                n = float(value)
                if not (n > 0) or n != n or n == float("inf"):
                    raise ValueError
                cfg[key] = n * scale if key == "budget" else int(n * scale)
            except ValueError:
                issues.append("bad-value:%s" % _ascii(a)[:40])
        elif name in PATH_FLAGS:
            if value:
                paths[name] = value
            else:
                issues.append("bad-value:%s" % _ascii(a)[:40])
    cfg["nudge_seconds"] = cfg["ttl"]  # once per cache window (user ruling 2026-10-20)
    if os.environ.get(ENV_KILL, "").strip().lower() == "log":
        cfg["emit"] = False
    return cfg, paths, issues


def load_state(path):
    """The cache dict, or a fresh one when the file is missing, unreadable or malformed. Every
    entry is validated (review P2): valid JSON of the wrong shape is dropped, so that folder is
    re-measured instead of raising in evaluate() on every call forever."""
    try:
        with open(path, encoding="ascii") as fh:
            data = json.loads(fh.readline() or "{}")
        return s1.clean_state(data)
    except Exception:
        return {"version": 1, "roots": {}}


def save_state(path, state):
    """One ASCII JSON line, written to a sibling temp file and moved over the state file. If the
    move fails, the hook removes its OWN temp file (review P4) - the only file it ever removes."""
    tmp = "%s.%d.tmp.jsonl" % (path[:-6] if path.endswith(".jsonl") else path, os.getpid())
    try:
        with open(tmp, "w", encoding="ascii", newline="\n") as fh:
            fh.write(json.dumps(state, ensure_ascii=True, separators=(",", ":")) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass


def process(event, argv, now=None):
    """(printed_json_or_None, rows, issues) for one event under this command line."""
    cfg, paths, issues = parse_flags(argv)
    if not isinstance(event, dict) or not s1.prefilter(event):
        return None, [], issues
    state = load_state(paths["state-path"])
    text, rows, changed = s1.evaluate(event, cfg, state, time.time() if now is None else now)
    if any(r.get("kind") == "flag" for r in rows) and \
            s1.flag_taken(rows, load_state(paths["state-path"])):
        # review P4: a concurrent hook process flagged this recipient since we loaded the state;
        # keep its stamp (do not save over it) and stay silent
        for r in rows:
            if r.get("kind") == "flag":
                r["emitted"] = False
                r["duplicate"] = True
        return None, rows, issues
    if changed:
        save_state(paths["state-path"], state)
    out = None
    if text:
        out = s1.output_for(event.get("hook_event_name") or "PreToolUse", text)
    return out, rows, issues


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if s1 is None:
            raise RuntimeError("rules/ did not import")  # fail open, silently (review P6)
        # review P3: under -I the text-mode stdin decodes as the console code page (cp1252);
        # Claude Code sends UTF-8, so read bytes and decode them as UTF-8
        event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        out, rows, issues = process(event, argv)
        if rows or issues:
            from hooklib import output
            _cfg, paths, _i = parse_flags(argv)
            output.log_hits(rows, hits_path=paths["hits-path"])
            output.record_config_issues(issues, argv, hits_path=paths["hits-path"])
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=True))
            sys.stdout.flush()
    except Exception:
        pass  # fail open: a broken hook must never block, print or fail a call
    sys.exit(0)


# ------------------------------------------------------------------ self-test

def self_test():
    import subprocess

    if s1 is None:
        print("FAIL: rules/size/s1_claude_dir_size.py did not import")
        print("self-test: 1 case(s), 1 failure(s)")
        return 1
    failures = list(s1.run_self_test())
    n = s1.CASES_RUN[0]

    def check(ok, msg):
        nonlocal n
        n += 1
        if not ok:
            failures.append("size_guard: " + msg)

    # ---- flags
    saved_kill = os.environ.pop(ENV_KILL, None)
    try:
        cfg, p, iss = parse_flags([])
        check(cfg["emit"] and cfg["limit"] == 1024 ** 3 and cfg["nudge_seconds"] == cfg["ttl"] and not iss,
              "no flags: flag mode, 1 GB, nudge = cache window")
        for argv in (["--log-only"], ["--Log_Only"], ["--log-only=1"], ["--log-only=yes"]):
            check(parse_flags(argv)[0]["emit"] is False, "%r must be log-only" % argv)
        check(parse_flags(["--log-only=no"])[0]["emit"] is True, "--log-only=no must flag")
        cfg, _p, iss = parse_flags(["--limit-mb=0.5", "--ttl-minutes=5", "--budget-seconds=1.5"])
        check(cfg["limit"] == 512 * 1024 and cfg["ttl"] == 300 and cfg["nudge_seconds"] == 300
              and cfg["budget"] == 1.5 and not iss, "numeric overrides, got %r" % cfg)
        for bad in ("--limit-mb=abc", "--limit-mb=0", "--limit-mb=-1", "--ttl-minutes=", "--limit-mb=nan"):
            cfg, _p, iss = parse_flags([bad])
            check(cfg["limit"] == s1.LIMIT_BYTES and cfg["ttl"] == s1.TTL_SECONDS
                  and iss and iss[0].startswith("bad-value"), "%r must be ignored and recorded" % bad)
        _c, _p, iss = parse_flags(["--enforce=S1", "S1"])
        check(iss == ["unrecognized-flag:--enforce=S1", "stray-argument:S1"],
              "--enforce is not a flag of this hook (it never denies), got %r" % iss)
        _c, p, _i = parse_flags(["--state-path=x.jsonl", "--hits-path=y.jsonl"])
        check(p == {"state-path": "x.jsonl", "hits-path": "y.jsonl"}, "path overrides")
        os.environ[ENV_KILL] = "log"
        check(parse_flags([])[0]["emit"] is False, "CLAUDE_SIZE_GUARD=log must mean log-only")
        os.environ.pop(ENV_KILL, None)
    finally:
        if saved_kill is not None:
            os.environ[ENV_KILL] = saved_kill

    # ---- state file round trip and fail-open on a corrupt file (beside this script, overwritten)
    st = {"version": 1, "roots": {"k": {"bytes": 5, "measured": 1.0, "nudged": {}}}}
    save_state(SELFTEST_STATE, st)
    check(load_state(SELFTEST_STATE) == st, "state round trip")
    with open(SELFTEST_STATE, "w", encoding="ascii") as fh:
        fh.write("{not json\n")
    check(load_state(SELFTEST_STATE) == {"version": 1, "roots": {}}, "a corrupt state must load fresh")
    check(load_state(os.path.join(HERE, "no_such_dir", "s.jsonl"))["roots"] == {}, "a missing state")
    # P2: valid JSON of the wrong shape loads with the bad entries dropped, never a raise
    good = {"bytes": 5, "measured": 1.0, "nudged": {}, "top": [["a", 1, "keep"]]}
    for bad_entry in (5, {"bytes": "x", "measured": 1.0}, {"bytes": 5, "measured": "abc"},
                      dict(good, nudged=[]), dict(good, nudged={"main:s": "x"}), dict(good, top=5),
                      dict(good, top=[[1]])):
        with open(SELFTEST_STATE, "w", encoding="ascii") as fh:
            fh.write(json.dumps({"version": 1, "roots": {"bad": bad_entry, "ok": good}}) + "\n")
        check(load_state(SELFTEST_STATE)["roots"] == {"ok": good},
              "P2: entry %r must be dropped on load, the good one kept" % (bad_entry,))
    for bad_state in ('{"roots": 5}', '[1, 2]', '"x"', '{"roots": {"k": [1]}}'):
        with open(SELFTEST_STATE, "w", encoding="ascii") as fh:
            fh.write(bad_state + "\n")
        check(load_state(SELFTEST_STATE)["roots"] == {}, "P2: state %s must load fresh" % bad_state)
    # P4: when the final move fails, the hook's own temp file is removed (target is a directory)
    blocked = os.path.join(HERE, "rules")
    save_state(blocked, {"version": 1, "roots": {}})
    check(not os.path.exists("%s.%d.tmp.jsonl" % (blocked, os.getpid())) and os.path.isdir(blocked),
          "P4: a failed os.replace must leave no temp file behind (and the target untouched)")

    # ---- real subprocess runs against a seeded, fresh, over-limit cache (no walk, no test data)
    key = os.path.normcase(os.path.normpath(s1.FAKE_CDIR))

    def seed(bytes_):
        save_state(SELFTEST_STATE, {"version": 1, "roots": {key: {
            "bytes": bytes_, "files": 3, "errors": 0, "partial": False, "ms": 1, "scratch": 100,
            "top": [["scratch", 100, "temporary"]], "measured": time.time(), "nudged": {}}}})

    with open(SELFTEST_HITS, "w", encoding="ascii"):
        pass  # truncate the self-test hit log (never deleted)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", ENV_KILL)}
    script = os.path.join(HERE, "size_guard.py")
    base = ["--state-path=" + SELFTEST_STATE, "--hits-path=" + SELFTEST_HITS]

    def run(ev, extra=(), raw=None):
        p = subprocess.run([sys.executable, "-I", "-S", "-B", script] + base + list(extra),
                           input=raw if raw is not None else json.dumps(ev), capture_output=True,
                           text=True, env=env, timeout=30)
        try:
            return p.returncode, json.loads(p.stdout) if p.stdout.strip() else None, p.stdout
        except ValueError:
            return p.returncode, "unparseable", p.stdout

    ev_in = {"hook_event_name": "PreToolUse", "tool_name": "Read", "cwd": s1.FAKE_ROOT,
             "session_id": "st", "tool_input": {"file_path": s1.FAKE_CDIR + "\\docs\\a.md"}}
    seed(2 * 1024 ** 3)
    rc, out, raw = run(ev_in)
    hso = (out or {}).get("hookSpecificOutput", {}) if isinstance(out, dict) else {}
    check(rc == 0 and hso.get("hookEventName") == "PreToolUse" and "S1 flag" in hso.get("additionalContext", "")
          and "permissionDecision" not in hso, "subprocess: over + .claude path must print the flag, got %r"
          % raw[:160])
    check(raw.isascii(), "subprocess: stdout must be ASCII")
    rc, out, raw = run(ev_in)
    check(rc == 0 and out is None, "subprocess: second call in the window must print nothing")
    seed(2 * 1024 ** 3)
    rc, out, raw = run(ev_in, ["--log-only"])
    check(rc == 0 and out is None, "subprocess: --log-only must print nothing")
    seed(2 * 1024 ** 3)
    rc, out, raw = run(dict(ev_in, tool_input={"file_path": s1.FAKE_ROOT + "\\src\\a.cpp"}))
    check(rc == 0 and out is None, "subprocess: a path outside .claude must print nothing")
    seed(1024)
    rc, out, raw = run(ev_in)
    check(rc == 0 and out is None, "subprocess: under the limit must print nothing")
    for name, ev in (("UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "prompt": "x",
                                           "cwd": s1.FAKE_ROOT, "session_id": "st2"}),
                     ("SessionStart", {"hook_event_name": "SessionStart", "source": "startup",
                                       "cwd": s1.FAKE_ROOT, "session_id": "st3"})):
        seed(2 * 1024 ** 3)
        rc, out, raw = run(ev)
        hso = (out or {}).get("hookSpecificOutput", {}) if isinstance(out, dict) else {}
        check(rc == 0 and hso.get("hookEventName") == name and hso.get("additionalContext"),
              "subprocess: %s over the limit must flag the main session, got %r" % (name, raw[:120]))
    seed(2 * 1024 ** 3)
    rc, out, raw = run(dict(ev_in, agent_id="sub1", agent_type="Explore"))
    ctx = ((out or {}).get("hookSpecificOutput", {}) if isinstance(out, dict) else {}).get("additionalContext", "")
    check(rc == 0 and "You are a subagent" in ctx, "subprocess: a subagent's call must get the subagent flag")
    rc, out, raw = run(None, raw="{not json")
    check(rc == 0 and out is None, "subprocess: garbage stdin must fail open")
    rc, out, raw = run(None, raw="[1, 2]")
    check(rc == 0 and out is None, "subprocess: a non-object event must fail open")

    # P3: stdin is UTF-8 (Claude Code's encoding), not the console code page; a non-ASCII project
    # root must be read exactly, keyed exactly, and come back exact (JSON-escaped) in the flag
    uroot = "C:\\S1Self\u00e9T\u00f8st\\proj"
    ucdir = uroot + "\\.claude"
    save_state(SELFTEST_STATE, {"version": 1, "roots": {os.path.normcase(os.path.normpath(ucdir)): {
        "bytes": 2 * 1024 ** 3, "files": 3, "errors": 0, "partial": False, "ms": 1, "scratch": 100,
        "top": [["scratch", 100, "temporary"]], "measured": time.time(), "nudged": {}}}})
    ev_u = {"hook_event_name": "PreToolUse", "tool_name": "Read", "cwd": uroot, "session_id": "su",
            "tool_input": {"file_path": ucdir + "\\docs\\n\u00f6te.md"}}
    pu = subprocess.run([sys.executable, "-I", "-S", "-B", script] + base,
                        input=json.dumps(ev_u, ensure_ascii=False).encode("utf-8"),
                        capture_output=True, env=env, timeout=30)
    try:
        uctx = json.loads(pu.stdout.decode("ascii"))["hookSpecificOutput"]["additionalContext"]
    except Exception:
        uctx = ""
    check(pu.returncode == 0 and (ucdir + "\\scratch\\") in uctx and pu.stdout.isascii(),
          "P3: a UTF-8 event with a non-ASCII root must flag with the exact path, got %r" % pu.stdout[:160])

    # P6: a broken or missing rules/ package exits 0 and prints nothing
    seed(2 * 1024 ** 3)
    code = ("import sys; sys.modules['rules.size.s1_claude_dir_size'] = None; sys.argv = %r; "
            "g = {'__name__': '__main__', '__file__': %r}; "
            "exec(compile(open(%r, encoding='utf-8').read(), %r, 'exec'), g)"
            % ([script] + base, script, script, script))
    p6 = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], input=json.dumps(ev_in),
                        capture_output=True, text=True, env=env, timeout=30)
    check(p6.returncode == 0 and p6.stdout == "" and p6.stderr == "",
          "P6: a failed rules import must exit 0 silently, got rc=%r out=%r err=%r"
          % (p6.returncode, p6.stdout[:80], p6.stderr[-160:]))
    rc, out, raw = run(ev_in)  # control: the same seeded event flags once rules/ imports
    check(rc == 0 and isinstance(out, dict), "P6 control: the seeded event must flag normally")
    try:
        with open(SELFTEST_HITS, encoding="ascii") as fh:
            logged = [json.loads(ln) for ln in fh.read().split("\n") if ln]
    except (OSError, ValueError):
        logged = []
    check(any(r.get("kind") == "flag" and r.get("emitted") is True for r in logged) and
          any(r.get("kind") == "flag" and r.get("emitted") is False for r in logged),
          "subprocess: hit log must carry emitted:true and emitted:false flag rows")
    check(all(r.get("rule") == "S1" for r in logged) and logged, "subprocess: every row is S1")

    # ---- the settings snippet: every command is this script in flag mode, the three events wired
    try:
        with open(SNIPPET, encoding="utf-8") as fh:
            snip = json.load(fh)
        hooks = snip.get("hooks", snip)
        check(set(hooks) == {"PreToolUse", "SessionStart", "UserPromptSubmit"},
              "snippet must wire exactly PreToolUse, SessionStart, UserPromptSubmit, got %r" % sorted(hooks))
        pre = hooks["PreToolUse"][0]
        check(set(pre["matcher"].split("|")) == set(s1.TOOLS), "PreToolUse matcher must be %r" % (s1.TOOLS,))
        check(set(hooks["SessionStart"][0]["matcher"].split("|")) == {"startup", "resume", "clear", "compact"},
              "SessionStart matcher")
        check("matcher" not in hooks["UserPromptSubmit"][0], "UserPromptSubmit takes no matcher")
        for ev_name, groups in hooks.items():
            for g in groups:
                for h in g["hooks"]:
                    cmd = h["command"]
                    import shlex
                    toks = shlex.split(cmd, posix=True)
                    idx = next((i for i, t in enumerate(toks)
                                if t.replace("\\", "/").lower().endswith("size_guard.py")), -1)
                    kill = os.environ.pop(ENV_KILL, None)
                    try:
                        cfg, _p, iss = parse_flags(toks[idx + 1:] if idx >= 0 else [])
                    finally:
                        if kill is not None:
                            os.environ[ENV_KILL] = kill
                    # user ruling B, 2026-10-20: the shipped snippet flags from day one
                    check(idx >= 0 and cfg["emit"] is True and not iss and h.get("timeout", 0) >= 5,
                          "ruling B: %s snippet command must run size_guard.py in flag mode "
                          "(no --log-only): %r" % (ev_name, cmd))
    except Exception as e:
        check(False, "could not load/parse the snippet: %r" % (e,))

    if failures:
        for f in failures:
            print("FAIL: " + _ascii(f))
        print("self-test: %d case(s), %d failure(s)" % (n, len(failures)))
        return 1
    print("self-test: %d case(s), 0 failure(s)" % n)
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    main()
