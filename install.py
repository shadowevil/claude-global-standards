"""Install this repo's global Claude Code setup into ~/.claude.

    python install.py                  dry run: what would be added or changed (writes nothing)
    python install.py --apply          back up what will be replaced, then copy
    python install.py --only skills    limit to one part: global | skills | hooks (repeatable)
    python install.py --hooks          also install hook code (hooks are skipped unless asked)
    python install.py --strict-git     append global/CLAUDE.git-strict.md to the installed CLAUDE.md
    python install.py --wire-hooks     show the settings.json entries the hooks need (add --apply to write)
    python install.py --python CMD     the Python command the hooks run with (default: detected)
    python install.py --self-test      run the installer's own tests (writes nothing)

Mapping:
    global/CLAUDE.md   -> ~/.claude/CLAUDE.md        (+ CLAUDE.git-strict.md with --strict-git)
    global/docs/...    -> ~/.claude/docs/...
    skills/<name>/...  -> ~/.claude/skills/<name>/...
    hooks/...          -> ~/.claude/hooks/...        (only with --hooks)

Placeholders (2026-10-21, so the repo installs for anyone): text files may carry
    {{CLAUDE_HOME}}      the installing user's ~/.claude, native separators. On a non-Windows
                         machine the backslashes in the path right after it become '/'.
    {{CLAUDE_HOME_FWD}}  the same folder with forward slashes (for hook commands in JSON)
    {{PYTHON}}           the Python command (`python` / `python3`, or a quoted full path)
They are filled at install time; in .json files the values are JSON-escaped. Everything else in a
file is copied byte for byte, so on the owner's machine the installed files are unchanged.

Rules:
- Line endings are kept exactly; a file that differs only in line endings is reported as such.
- Nothing in ~/.claude is ever deleted. Files there that this repo does not have are listed and left.
- Every replaced file is first copied to ~/.claude/staging/install-backups/<timestamp>/<same path>.
- settings.json is only touched by --wire-hooks --apply: it backs the file up, ADDS the hook entries
  from hooks/*settings_snippet.json that are not wired yet (matched by script name, so an entry you
  already have keeps your flags), and never removes or edits anything else.
- Output is ASCII only.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="ascii", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parent
TARGET = Path.home() / ".claude"
SKIP_PARTS = {"__pycache__", ".git"}
SKIP_SUFFIXES = {".pyc", ".jsonl", ".log"}
TOKENS = ("{{CLAUDE_HOME}}", "{{CLAUDE_HOME_FWD}}", "{{PYTHON}}")
# {{CLAUDE_HOME}} plus the backslash path that follows it (stops at whitespace, quotes, backticks, ')')
HOME_PATH_RE = re.compile(r"\{\{CLAUDE_HOME\}\}((?:\\[^\s\"'`)\\]+)*\\?)")


class InstallError(Exception):
    pass


def wanted(p):
    return not (set(p.parts) & SKIP_PARTS) and p.suffix not in SKIP_SUFFIXES and p.is_file()


# ------------------------------------------------------------------ placeholders

def detect_python():
    """The command the hooks should run Python with: a bare name when it resolves to a real
    interpreter, else the running interpreter's full path (quoted when it has a space)."""
    names = ("python", "python3") if os.name == "nt" else ("python3", "python")
    for name in names:
        found = shutil.which(name)
        if found and "windowsapps" not in found.lower() and python_ok(found):  # the Store stub is not Python
            return name
    if sys.version_info < (3, 10):
        raise InstallError("no Python 3.10+ found for the hooks; pass --python <command>")
    exe = sys.executable.replace("\\", "/")
    return '"%s"' % exe if " " in exe else exe


def python_ok(exe):
    """True when `exe` runs and is Python 3.10 or newer (the hooks need it)."""
    try:
        p = subprocess.run([exe, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
                           capture_output=True, timeout=20)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def make_values(claude_home, python_cmd, sep=None):
    """The placeholder values for one install. `claude_home` is the target ~/.claude folder."""
    home = str(claude_home)
    bad = [c for c in ('"', "$", "`") if c in home]
    if bad:
        raise InstallError("the .claude path contains %s, which the shell running hook commands would "
                           "expand or break on: %s" % (" ".join(bad), home))
    return {"home": home, "fwd": home.replace("\\", "/"), "python": python_cmd,
            "sep": sep or os.sep}


def fill_text(text, values, json_file=False):
    """Replace the placeholders in one file's text; nothing else changes."""
    esc = (lambda s: json.dumps(s, ensure_ascii=False)[1:-1]) if json_file else (lambda s: s)
    if json_file and "{{CLAUDE_HOME}}" in text:
        raise InstallError("use {{CLAUDE_HOME_FWD}} in .json files, not {{CLAUDE_HOME}}")
    out = text.replace("{{CLAUDE_HOME_FWD}}", esc(values["fwd"]))
    out = out.replace("{{PYTHON}}", esc(values["python"]))

    def home_path(m):
        tail = m.group(1)
        if values["sep"] != "\\":
            tail = tail.replace("\\", values["sep"])
        return esc(values["home"]) + tail

    return HOME_PATH_RE.sub(home_path, out)


def fill_bytes(data, name, values):
    """Fill a file's bytes when it is UTF-8 text carrying a placeholder; return other bytes as-is."""
    if b"{{" not in data:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if not any(t in text for t in TOKENS):
        return data
    return fill_text(text, values, json_file=name.lower().endswith(".json")).encode("utf-8")


# ------------------------------------------------------------------ files

def claude_md(strict_git):
    """The bytes the installed CLAUDE.md should hold: the base file, plus the strict-git block."""
    data = (REPO / "global" / "CLAUDE.md").read_bytes()
    if strict_git:
        extra = (REPO / "global" / "CLAUDE.git-strict.md").read_bytes().replace(b"\r\n", b"\n")
        if b"\r\n" in data:
            extra = extra.replace(b"\n", b"\r\n")
        nl = b"\r\n" if b"\r\n" in data else b"\n"
        if not data.endswith(nl):
            data += nl
        data += extra
    return data


def plan(parts, values, strict_git=False, target=TARGET):
    """Yield (part, filled bytes, destination) for every file this repo installs."""
    if "global" in parts:
        yield "global", fill_bytes(claude_md(strict_git), "CLAUDE.md", values), target / "CLAUDE.md"
        docs = REPO / "global" / "docs"
        if docs.is_dir():
            for src in sorted(docs.rglob("*")):
                if wanted(src):
                    yield "global", fill_bytes(src.read_bytes(), src.name, values), \
                        target / "docs" / src.relative_to(docs)
    for part, sub in (("skills", "skills"), ("hooks", "hooks")):
        if part not in parts:
            continue
        root = REPO / sub
        if not root.is_dir():
            continue
        for src in sorted(root.rglob("*")):
            if wanted(src):
                yield part, fill_bytes(src.read_bytes(), src.name, values), \
                    target / sub / src.relative_to(root)


def classify(a, dst):
    if not dst.exists():
        return "ADD"
    b = dst.read_bytes()
    if a == b:
        return "same"
    if a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n"):
        return "CHANGE (line endings only)"
    return "CHANGE"


def extras(parts):
    """Files under the installed skill/hook folders that this repo does not carry."""
    out = []
    if "global" in parts and (TARGET / "docs").is_dir():
        for f in (TARGET / "docs").rglob("*"):
            if wanted(f) and not (REPO / "global" / "docs" / f.relative_to(TARGET / "docs")).exists():
                out.append(f)
    if "skills" in parts:
        for d in sorted((REPO / "skills").iterdir()):
            inst = TARGET / "skills" / d.name
            if inst.is_dir():
                for f in inst.rglob("*"):
                    if wanted(f) and not (d / f.relative_to(inst)).exists():
                        out.append(f)
    if "hooks" in parts and (TARGET / "hooks").is_dir():
        for f in (TARGET / "hooks").rglob("*"):
            if wanted(f) and not (REPO / "hooks" / f.relative_to(TARGET / "hooks")).exists():
                out.append(f)
    return out


# ------------------------------------------------------------------ settings.json wiring

def snippet_events(obj):
    """{event: [group, ...]} from a snippet file: either {"hooks": {...}} or one PreToolUse group."""
    if isinstance(obj.get("hooks"), dict):
        return obj["hooks"]
    if "matcher" in obj and isinstance(obj.get("hooks"), list):
        return {"PreToolUse": [obj]}
    raise InstallError("unrecognized snippet shape")


def script_of(command):
    """The lower-cased file name of the .py script a hook command runs, or None."""
    try:
        toks = shlex.split(command, posix=True)
    except ValueError:
        toks = command.split()
    for t in toks:
        if t.lower().endswith(".py"):
            return t.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return None


def load_snippets(values):
    """Every hooks/*settings_snippet.json, filled for this install: [(file name, {event: groups})]."""
    out = []
    for f in sorted((REPO / "hooks").glob("*settings_snippet.json")):
        text = fill_text(f.read_text(encoding="utf-8"), values, json_file=True)
        out.append((f.name, snippet_events(json.loads(text))))
    return out


def merge_hooks(settings, snippets):
    """Add each snippet hook whose script is not yet wired for that event. Returns (settings,
    added rows, kept rows); never removes or edits an existing entry. A settings shape it does not
    understand is refused, never repaired."""
    if not isinstance(settings, dict):
        raise InstallError("settings.json is not a JSON object; fix it by hand first")
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError('settings.json "hooks" is not an object; fix it by hand first')
    added, kept = [], []
    for _name, events in snippets:
        for event, groups in events.items():
            have = hooks.setdefault(event, [])
            if not isinstance(have, list) or not all(
                    isinstance(g, dict) and isinstance(g.get("hooks", []), list)
                    and all(isinstance(h, dict) for h in g.get("hooks", [])) for g in have):
                raise InstallError('settings.json "hooks.%s" has an unexpected shape; fix it by hand first' % event)
            wired = {script_of(str(h.get("command", ""))) for g in have for h in g.get("hooks", [])}
            for group in groups:
                scripts = {script_of(h["command"]) for h in group["hooks"]}
                if scripts & wired:
                    kept.append((event, ", ".join(sorted(s for s in scripts if s))))
                    continue
                have.append(group)
                wired |= scripts
                added.append((event, group.get("matcher", "(any)"), group["hooks"][0]["command"]))
    return settings, added, kept


def wire_hooks(values, apply):
    path = TARGET / "settings.json"
    raw = path.read_bytes() if path.exists() else b"{}"
    try:
        settings = json.loads(raw.decode("utf-8-sig") or "{}")
    except ValueError as e:
        raise InstallError("settings.json is not valid JSON, fix it first: %s" % e)
    settings, added, kept = merge_hooks(settings, load_snippets(values))
    for event, scripts in kept:
        print("already wired (left as is): %-17s %s" % (event, scripts))
    for event, matcher, command in added:
        print("%s: %-17s matcher %s -> %s" % ("wired" if apply else "would wire", event, matcher, command))
    if not added:
        print("settings.json: nothing to wire.")
        return 0
    if not apply:
        print("dry run - settings.json not written. Re-run with --wire-hooks --apply to write it.")
        return 0
    for _event, _matcher, command in added:
        script = script_of(command)
        if script and not (TARGET / "hooks" / script).exists():
            raise InstallError("hook code missing: %s - install it first with --only hooks --hooks --apply" % script)
    if path.exists():
        backup = TARGET / "staging" / "install-backups" / ("settings-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup / "settings.json")
        print("backup: %s" % (backup / "settings.json"))
    text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    if b"\r\n" in raw:
        text = text.replace("\n", "\r\n")
    tmp = path.with_name(path.name + ".install-tmp")
    try:
        tmp.write_bytes(text.encode("utf-8"))
        json.loads(tmp.read_text(encoding="utf-8"))
        os.replace(tmp, path)  # atomic: settings.json is either the old file or the new one
    except (OSError, ValueError) as e:
        raise InstallError("could not replace settings.json (is it open in another program?): %s. "
                           "settings.json is unchanged; the new version may be in %s" % (e, tmp))
    print("settings.json updated: %d hook entr%s added." % (len(added), "y" if len(added) == 1 else "ies"))
    return 0


# ------------------------------------------------------------------ self-test (in memory only)

def self_test():
    failures = []
    n = 0

    def check(ok, msg):
        nonlocal n
        n += 1
        if not ok:
            failures.append(msg)

    homes = [
        ("C:\\Users\\John Smith\\.claude", "\\"),
        ("C:\\Users\\J\u00f6rg \u0421\u0435\\.claude", "\\"),
        ("/home/alice/.claude", "/"),
        ("/Users/bob smith/.claude", "/"),
    ]
    sources = []
    for root in ("global", "skills", "hooks"):
        for f in sorted((REPO / root).rglob("*")):
            if wanted(f):
                sources.append(f)
    templated = [f for f in sources if any(t.encode() in f.read_bytes() for t in TOKENS)]
    check(len(templated) >= 6, "expected the snippets, CLAUDE.md and skills to be templated, got %d" % len(templated))

    for home, sep in homes:
        for py in ("python", '"C:/Program Files/Python313/python.exe"'):
            v = make_values(home, py, sep)
            for f in templated:
                out = fill_bytes(f.read_bytes(), f.name, v).decode("utf-8")
                check(not any(t in out for t in TOKENS), "%s: placeholder left for %r" % (f.name, home))
                if f.suffix == ".json":
                    try:
                        obj = json.loads(out)
                    except ValueError as e:
                        check(False, "%s: not JSON after fill for %r: %s" % (f.name, home, e))
                        continue
                    for event, groups in snippet_events(obj).items():
                        for g in groups:
                            for h in g["hooks"]:
                                toks = shlex.split(h["command"], posix=True)
                                script = [t for t in toks if t.endswith(".py")]
                                check(len(script) == 1 and script[0].startswith(v["fwd"] + "/hooks/"),
                                      "%s %s: script path not under %r: %r" % (f.name, event, v["fwd"], toks))
                                check(toks[0] == py.strip('"'), "%s: python command lost: %r" % (f.name, toks[:1]))
                elif sep == "/":
                    # only paths built from the home are converted; other text is the file's own
                    check(v["home"] + "\\" not in out,
                          "%s: a Windows separator follows the POSIX home" % f.name)

    # the owner's own values reproduce the old literal text exactly
    owner = make_values("C:\\Users\\ShadowEvil\\.claude", "python", "\\")
    check(fill_text("`{{CLAUDE_HOME}}\\docs\\x.md` and `{{CLAUDE_HOME}}`", owner)
          == "`C:\\Users\\ShadowEvil\\.claude\\docs\\x.md` and `C:\\Users\\ShadowEvil\\.claude`",
          "owner fill of {{CLAUDE_HOME}} must match the old literal text")
    check(fill_text('{{PYTHON}} -I -S \\"{{CLAUDE_HOME_FWD}}/hooks/a.py\\"', owner, json_file=True)
          == 'python -I -S \\"C:/Users/ShadowEvil/.claude/hooks/a.py\\"',
          "owner fill of a JSON hook command must match the old literal text")
    posix = make_values("/home/al/.claude", "python3", "/")
    check(fill_text('"{{CLAUDE_HOME}}\\skills\\a\\b.py" x\\y', posix) == '"/home/al/.claude/skills/a/b.py" x\\y',
          "only the path after {{CLAUDE_HOME}} changes separators")
    check(fill_text("{{OTHER}} {{CLAUDE_HOMEX}}", posix) == "{{OTHER}} {{CLAUDE_HOMEX}}",
          "unknown tokens must be left alone")
    check(fill_bytes(b"\xff\xfe{{PYTHON}}", "x.bin", posix) == b"\xff\xfe{{PYTHON}}", "binary is never filled")
    try:
        fill_text("{{CLAUDE_HOME}}", posix, json_file=True)
        check(False, "{{CLAUDE_HOME}} in a .json file must be refused")
    except InstallError:
        check(True, "")
    for bad in ('/home/a"b/.claude', "/home/a$b/.claude", "C:\\Users\\a`b\\.claude"):
        try:
            make_values(bad, "python3")
            check(False, "a home path the hook shell would mangle must be refused: %r" % bad)
        except InstallError:
            check(True, "")
    check(python_ok(sys.executable), "the running interpreter (3.10+) must pass python_ok")
    check(not python_ok("no-such-python-xyz"), "a missing interpreter must fail python_ok")

    # settings.json merging
    v = make_values("C:\\Users\\x\\.claude", "python", "\\")
    snips = load_snippets(v)
    names = {s for _f, ev in snips for gs in ev.values() for g in gs for s in (script_of(h["command"]) for h in g["hooks"])}
    check(names == {"write_guard.py", "size_guard.py", "model_guard.py"}, "snippet scripts: %r" % sorted(names))
    fresh, added, kept = merge_hooks({}, snips)
    check(len(added) == 5 and not kept, "an empty settings.json gets all 5 entries, got %d/%d" % (len(added), len(kept)))
    _s, again, kept2 = merge_hooks(json.loads(json.dumps(fresh)), snips)
    check(not again and len(kept2) == 5, "a second merge must add nothing (idempotent)")
    mine = {"permissions": {"allow": ["Bash(ls)"]}, "hooks": {"PreToolUse": [
        {"matcher": "Write|Edit", "hooks": [{"type": "command",
         "command": 'python -I -S "C:/Users/x/.claude/hooks/write_guard.py" --log-only --enforce=W2,W3,W5'}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-own-hook.exe"}]}]}}
    before = json.dumps(mine["hooks"]["PreToolUse"])
    merged, added3, kept3 = merge_hooks(mine, snips)
    check(json.dumps(merged["hooks"]["PreToolUse"][:2]) == before, "existing entries must stay untouched and first")
    check(any("write_guard.py" in k[1] for k in kept3), "an existing write_guard entry keeps its own flags")
    check(sum(1 for g in merged["hooks"]["PreToolUse"]
              for h in g["hooks"] if script_of(h["command"]) == "write_guard.py") == 1,
          "write_guard must not be wired twice")
    check(merged["permissions"] == {"allow": ["Bash(ls)"]}, "unrelated settings are kept")
    check(script_of('"C:/Program Files/py.exe" -I "C:/a b/hooks/Size_Guard.py"') == "size_guard.py",
          "script_of handles quoted paths with spaces")
    for odd in ([], {"hooks": None}, {"hooks": []}, {"hooks": {"PreToolUse": {}}},
                {"hooks": {"PreToolUse": ["x"]}}, {"hooks": {"PreToolUse": [{"hooks": ["cmd"]}]}}):
        try:
            merge_hooks(json.loads(json.dumps(odd)), snips)
            check(False, "an odd settings shape must be refused cleanly: %r" % (odd,))
        except InstallError:
            check(True, "")

    for f in failures:
        print("FAIL: %s" % f.encode("ascii", "replace").decode("ascii"))
    print("install self-test: %d case(s), %d failure(s)" % (n, len(failures)))
    return 1 if failures else 0


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="Install the global Claude Code setup into ~/.claude.")
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--only", action="append", choices=["global", "skills", "hooks"])
    ap.add_argument("--hooks", action="store_true", help="include hook code")
    ap.add_argument("--strict-git", action="store_true",
                    help="append global/CLAUDE.git-strict.md (never commit on the default branch)")
    ap.add_argument("--wire-hooks", action="store_true",
                    help="add the hooks' entries to ~/.claude/settings.json (dry run unless --apply)")
    ap.add_argument("--python", help="the Python command hooks run with (default: detected)")
    ap.add_argument("--self-test", action="store_true", help="run the installer's tests and exit")
    a = ap.parse_args()

    if a.self_test:
        return self_test()
    try:
        values = make_values(TARGET, a.python or detect_python())
    except InstallError as e:
        print("error: %s" % e)
        return 2
    print("installing for %s (hooks run with: %s)" % (values["home"], values["python"]))

    parts = set(a.only or ["global", "skills", "hooks"])
    if "hooks" in parts and not a.hooks:
        parts.discard("hooks")
        if parts or not a.wire_hooks:
            print("hooks: skipped (pass --hooks to include hook code; --wire-hooks adds them to settings.json)")
    if a.wire_hooks and not parts:  # a wiring-only run: no file messages
        try:
            return wire_hooks(values, a.apply)
        except InstallError as e:
            print("error: %s" % e)
            return 2

    try:
        rows = [(part, data, dst, classify(data, dst)) for part, data, dst in plan(parts, values, a.strict_git)]
    except InstallError as e:
        print("error: %s" % e)
        return 2
    changes = [r for r in rows if r[3] != "same"]
    for part, data, dst, state in rows:
        if state != "same":
            print("%-28s %s" % (state, dst))
    print("%d file(s) to add or change, %d already identical" % (len(changes), len(rows) - len(changes)))
    for f in extras(parts):
        print("extra (left alone): %s" % f)

    if a.apply and changes:
        backup = TARGET / "staging" / "install-backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
        for part, data, dst, state in changes:
            if dst.exists():
                keep = backup / dst.relative_to(TARGET)
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, keep)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            if dst.read_bytes() != data:
                print("FAILED to verify %s" % dst)
                return 1
        print("installed %d file(s); replaced files backed up under %s" % (len(changes), backup))
    elif a.apply:
        print("files: nothing to do.")
    else:
        print("dry run - nothing written. Re-run with --apply to install.")

    if a.wire_hooks:
        try:
            return wire_hooks(values, a.apply)
        except InstallError as e:
            print("error: %s" % e)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
