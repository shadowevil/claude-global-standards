"""Global hook: M1, an Agent call must pick its model (user rule 2026-10-21, "Pick the cheapest model
that does the job well"; see rules/model/m1_model_not_set.py for the rule, its message and its
self-test cases).

LOG-ONLY and advisory. It never denies and never blocks: by default it appends a row to
model_guard_hits.jsonl and prints nothing. With `--advise` it also prints
hookSpecificOutput.additionalContext (the reminder to the dispatcher). Wire it on ONE event:

    PreToolUse   (matcher Agent)   -> the session that is about to dispatch

See model_guard_settings_snippet.json. This is a separate entry point from write_guard.py on purpose:
write_guard's matcher is the write/shell tools and its semantics are deny-or-log; this hook only ever
advises and reads the Agent tool's input (`prompt`, `description`, `subagent_type`, `model`).

    python -I -S model_guard.py                  (shipped: log-only)
    python -I -S model_guard.py --advise         (also print the additionalContext reminder)
    python -I -S model_guard.py --self-test
  flags: --hits-path=<file>
  CLAUDE_MODEL_GUARD=log in the environment forces log-only even with --advise (a kill switch).
Fail open: any exception exits 0 and prints nothing.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # python -I leaves the script's own directory off sys.path

try:
    from rules.model import m1_model_not_set as m1  # noqa: E402  (imports only os and re)
except Exception:  # a broken or missing rules/ must exit 0 silently
    m1 = None

HITS_PATH = os.path.join(HERE, "model_guard_hits.jsonl")
SELFTEST_HITS = os.path.join(HERE, "model_guard_hits.selftest.jsonl")
SNIPPET = os.path.join(HERE, "model_guard_settings_snippet.json")
ENV_KILL = "CLAUDE_MODEL_GUARD"
KNOWN_FLAGS = ("advise", "self-test", "hits-path")


def _ascii(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def parse_flags(argv):
    """(cfg, issues). A bad or unknown flag is recorded in `issues` and ignored, never a crash."""
    cfg = {"advise": False, "hits-path": HITS_PATH}
    issues = []
    for a in argv:
        if not a.startswith("--"):
            issues.append("stray-argument:%s" % _ascii(a)[:40])
            continue
        name, sep, value = a[2:].partition("=")
        name = name.lower().replace("_", "-")
        if name not in KNOWN_FLAGS:
            issues.append("unrecognized-flag:%s" % _ascii(a)[:40])
        elif name == "advise":
            cfg["advise"] = (not sep) or value.strip().lower() in ("1", "true", "yes")
        elif name == "hits-path":
            if value:
                cfg["hits-path"] = value
            else:
                issues.append("bad-value:%s" % _ascii(a)[:40])
    if os.environ.get(ENV_KILL, "").strip().lower() == "log":
        cfg["advise"] = False
    return cfg, issues


def process(event, argv, lookup=None):
    """(printed_json_or_None, rows, issues) for one event under this command line."""
    cfg, issues = parse_flags(argv)
    if not isinstance(event, dict):
        return None, [], issues
    found = m1.evaluate(event, lookup)
    rows, out = [], None
    for f in found:
        text = m1.message_for(f)
        ti = event.get("tool_input") or {}
        rows.append({"rule": m1.RULE_ID, "kind": f["kind"], "tool": "Agent", "cwd": event.get("cwd") or "",
                     "target": f["subagent_type"], "session": event.get("session_id") or "",
                     "sample": [" ".join(str(ti.get("description") or "").split())[:80]],
                     "words": f["words"], "brief_names": f["brief_names"],
                     "emitted": bool(cfg["advise"]), "msg": text[:160]})
    if rows and cfg["advise"]:
        out = m1.output_for(m1.message_for(found[0]))
    return out, rows, issues


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if m1 is None:
            raise RuntimeError("rules/ did not import")
        # stdin is UTF-8 (Claude Code's encoding); under -I the text stream would use the console code page
        event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        out, rows, issues = process(event, argv)
        if rows or issues:
            from hooklib import output
            cfg, _i = parse_flags(argv)
            output.log_hits(rows, hits_path=cfg["hits-path"])
            output.record_config_issues(issues, argv, hits_path=cfg["hits-path"])
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=True))
            sys.stdout.flush()
    except Exception:
        pass  # fail open: a broken hook must never block, print or fail a call
    sys.exit(0)


# ------------------------------------------------------------------ self-test

def self_test():
    import subprocess

    if m1 is None:
        print("FAIL: rules/model/m1_model_not_set.py did not import")
        print("self-test: 1 case(s), 1 failure(s)")
        return 1
    failures = list(m1.run_self_test())
    n = m1.CASES_RUN[0]

    def check(ok, msg):
        nonlocal n
        n += 1
        if not ok:
            failures.append("model_guard: " + msg)

    saved_kill = os.environ.pop(ENV_KILL, None)
    saved_env = os.environ.pop(m1.ENV_MODEL, None)
    fixture = os.path.join(HERE, "model_guard_fixture.selftest.d")
    made = []
    try:
        # ---- flags
        cfg, iss = parse_flags([])
        check(cfg["advise"] is False and cfg["hits-path"] == HITS_PATH and not iss, "no flags: log-only")
        for argv in (["--advise"], ["--Advise"], ["--advise=1"], ["--advise=yes"]):
            check(parse_flags(argv)[0]["advise"] is True, "%r must advise" % argv)
        check(parse_flags(["--advise=no"])[0]["advise"] is False, "--advise=no must stay log-only")
        _c, iss = parse_flags(["--enforce=M1", "M1", "--hits-path="])
        check(iss == ["unrecognized-flag:--enforce=M1", "stray-argument:M1", "bad-value:--hits-path="],
              "no --enforce (M1 never denies), a stray word and an empty path are recorded, got %r" % iss)
        check(parse_flags(["--hits-path=x.jsonl"])[0]["hits-path"] == "x.jsonl", "hits path override")
        os.environ[ENV_KILL] = "log"
        check(parse_flags(["--advise"])[0]["advise"] is False, "CLAUDE_MODEL_GUARD=log must force log-only")
        os.environ.pop(ENV_KILL, None)

        # ---- the definition lookup against real files (fixture folder beside this script, removed after)
        agents = os.path.join(fixture, ".claude", "agents")
        os.makedirs(os.path.join(agents, "sub"), exist_ok=True)
        files = {os.path.join(agents, "cheap.md"): "---\nname: cheap\nmodel: haiku\n---\nbody\n",
                 os.path.join(agents, "plain.md"): "---\nname: plain\ndescription: x\n---\nbody\n",
                 os.path.join(agents, "sub", "renamed.md"): "---\nname: nested\nmodel: inherit\n---\n",
                 os.path.join(agents, "Plan.md"): "---\nname: Plan\nmodel: haiku\n---\nbody\n",
                 os.path.join(agents, "bom.md"): "﻿---\nname: bom\nmodel: haiku\n---\nbody\n",
                 os.path.join(agents, "nohdr.md"): "You are an agent.\nmodel: haiku\n",
                 os.path.join(agents, "Explore.md"): "You are Explore. No frontmatter here.\nmodel: haiku\n"}
        for p, t in files.items():
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(t)
            made.append(p)
        dirs = [agents]
        check(m1.find_definition_model("cheap", dirs) == (True, "haiku"), "lookup: cheap -> haiku")
        check(m1.find_definition_model("plain", dirs) == (True, ""), "lookup: plain has no model")
        check(m1.find_definition_model("nested", dirs) == (True, "inherit"), "lookup: matched by name:, nested")
        check(m1.find_definition_model("PLAIN", dirs) == (True, ""), "lookup: case-insensitive")
        check(m1.find_definition_model("nope", dirs) == (False, ""), "lookup: unknown type")
        check(m1.find_definition_model("cheap", [os.path.join(fixture, "missing")]) == (False, ""),
              "lookup: missing folder")
        base = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": fixture, "session_id": "t",
                "tool_input": {"prompt": "p", "description": "d", "subagent_type": "cheap"}}
        check(m1.agent_dirs(base, home="")[0] == os.path.normpath(agents), "agent_dirs: cwd/.claude/agents first")
        check(m1.find_definition_model("bom", dirs) == (True, "haiku"), "lookup: BOM-prefixed header is read")
        check(m1.find_definition_model("nohdr", dirs) == (False, ""), "lookup: a file with no header is not a definition")

        def with_type(t):
            return dict(base, tool_input=dict(base["tool_input"], subagent_type=t))

        check(m1.evaluate(with_type("bom")) == [], "real lookup: a BOM-prefixed definition with a model stays silent")
        check(m1.evaluate(with_type("nohdr")) == [], "real lookup: a headerless file is no definition, unknown type is silent")
        check(m1.evaluate(with_type("Plan")) == [], "real lookup: a definition reusing the name Plan overrides the built-in")
        check(len(m1.evaluate(with_type("Explore"))) == 1,
              "real lookup: a headerless Explore.md is no override, the built-in Explore still fires")
        check(m1.evaluate(with_type("claude-code-guide")) == [] and m1.evaluate(with_type("statusline-setup")) == [],
              "real lookup: claude-code-guide and statusline-setup are silent")
        check(m1.evaluate(base) == [], "real lookup: a definition with a model stays silent")
        check(len(m1.evaluate(dict(base, tool_input=dict(base["tool_input"], subagent_type="plain")))) == 1,
              "real lookup: a definition with no model fires")
        check(m1.evaluate(dict(base, tool_input=dict(base["tool_input"], subagent_type="nope"))) == [],
              "real lookup: an unknown type stays silent")

        # ---- real subprocess runs (hits redirected to the self-test file)
        with open(SELFTEST_HITS, "w", encoding="ascii"):
            pass  # truncate the self-test hit log (never deleted)
        env = {k: v for k, v in os.environ.items() if k not in (ENV_KILL, m1.ENV_MODEL, "CLAUDE_PROJECT_DIR")}
        script = os.path.join(HERE, "model_guard.py")
        hp = "--hits-path=" + SELFTEST_HITS

        def run(ev, extra=(), raw=None):
            p = subprocess.run([sys.executable, "-I", "-S", "-B", script, hp] + list(extra),
                               input=raw if raw is not None else json.dumps(ev), capture_output=True,
                               text=True, env=env, timeout=30)
            try:
                return p.returncode, json.loads(p.stdout) if p.stdout.strip() else None, p.stdout, p.stderr
            except ValueError:
                return p.returncode, "unparseable", p.stdout, p.stderr

        fire = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": fixture, "session_id": "sf",
                "tool_input": {"prompt": "Look up where X is.", "description": "find X",
                               "subagent_type": "Explore"}}
        rc, out, raw, err = run(fire)
        check(rc == 0 and out is None and err == "", "subprocess: default is log-only, prints nothing, got %r" % raw[:100])
        rc, out, raw, err = run(fire, ["--advise"])
        hso = (out or {}).get("hookSpecificOutput", {}) if isinstance(out, dict) else {}
        check(rc == 0 and hso.get("hookEventName") == "PreToolUse" and "M1:" in hso.get("additionalContext", "")
              and "permissionDecision" not in hso and raw.isascii(),
              "subprocess: --advise must print additionalContext only (ASCII), got %r" % raw[:120])
        for label, ev in (("model set", dict(fire, tool_input=dict(fire["tool_input"], model="haiku"))),
                          ("custom type with a model", dict(base)),
                          ("another tool", dict(fire, tool_name="Read")),
                          ("non-object event", None)):
            rc, out, raw, err = run(ev, ["--advise"], raw="[1]" if ev is None else None)
            check(rc == 0 and out is None, "subprocess: %s must print nothing" % label)
        rc, out, raw, err = run(None, ["--advise"], raw="{not json")
        check(rc == 0 and out is None and err == "", "subprocess: garbage stdin must fail open")
        rc, out, raw, err = run(None, ["--advise"], raw="")
        check(rc == 0 and out is None, "subprocess: empty stdin must fail open")
        # a UTF-8 prompt with non-ASCII text must not crash or leak non-ASCII to stdout
        u = dict(fire, tool_input=dict(fire["tool_input"], prompt="Prüfe das — bitte", description="ä"))
        pu = subprocess.run([sys.executable, "-I", "-S", "-B", script, hp, "--advise"],
                            input=json.dumps(u, ensure_ascii=False).encode("utf-8"),
                            capture_output=True, env=env, timeout=30)
        check(pu.returncode == 0 and pu.stdout.isascii() and b"M1:" in pu.stdout, "subprocess: UTF-8 event, ASCII output")
        try:
            with open(SELFTEST_HITS, encoding="ascii") as fh:
                logged = [json.loads(ln) for ln in fh.read().split("\n") if ln]
        except (OSError, ValueError):
            logged = []
        check(len(logged) == 3 and all(r.get("rule") == "M1" and r.get("kind") == "unset" for r in logged),
              "subprocess: exactly the 3 firing runs are logged as M1, got %d row(s)" % len(logged))
        check([r.get("emitted") for r in logged] == [False, True, True], "hit log: emitted flag per run")

        # P-style: a broken rules/ import exits 0 silently
        code = ("import sys; sys.modules['rules.model.m1_model_not_set'] = None; sys.argv = %r; "
                "g = {'__name__': '__main__', '__file__': %r}; "
                "exec(compile(open(%r, encoding='utf-8').read(), %r, 'exec'), g)"
                % ([script, hp, "--advise"], script, script, script))
        p6 = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], input=json.dumps(fire),
                            capture_output=True, text=True, env=env, timeout=30)
        check(p6.returncode == 0 and p6.stdout == "" and p6.stderr == "",
              "a failed rules import must exit 0 silently, got rc=%r out=%r err=%r"
              % (p6.returncode, p6.stdout[:80], p6.stderr[-160:]))

        # ---- the settings snippet: Agent only, log-only (no --advise), this script
        try:
            import shlex
            with open(SNIPPET, encoding="utf-8") as fh:
                snip = json.load(fh)
            hooks = snip.get("hooks", snip)
            check(set(hooks) == {"PreToolUse"}, "snippet wires exactly PreToolUse, got %r" % sorted(hooks))
            grp = hooks["PreToolUse"][0]
            check(grp["matcher"] == "Agent", "snippet matcher must be exactly Agent, got %r" % grp["matcher"])
            for h in grp["hooks"]:
                toks = shlex.split(h["command"], posix=True)
                idx = next((i for i, t in enumerate(toks)
                            if t.replace("\\", "/").lower().endswith("model_guard.py")), -1)
                cfg, iss = parse_flags(toks[idx + 1:] if idx >= 0 else [])
                check(idx >= 0 and cfg["advise"] is False and not iss and 0 < h.get("timeout", 0) <= 10,
                      "snippet command must run model_guard.py log-only (no --advise): %r" % h["command"])
        except Exception as e:
            check(False, "could not load/parse the snippet: %r" % (e,))
    finally:
        for p in made:  # remove only the fixture files this run created, then its folders
            try:
                os.remove(p)
            except OSError:
                pass
        for d in (os.path.join(fixture, ".claude", "agents", "sub"), os.path.join(fixture, ".claude", "agents"),
                  os.path.join(fixture, ".claude"), fixture):
            try:
                os.rmdir(d)
            except OSError:
                pass
        if saved_kill is not None:
            os.environ[ENV_KILL] = saved_kill
        if saved_env is not None:
            os.environ[m1.ENV_MODEL] = saved_env

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
