"""Install this repo's global Claude Code setup into ~/.claude.

    python install.py                  dry run: what would be added or changed (writes nothing)
    python install.py --apply          back up what will be replaced, then copy
    python install.py --only skills    limit to one part: global | skills | hooks (repeatable)
    python install.py --hooks          also install hook code (hooks are skipped unless asked)
    python install.py --strict-git     append global/CLAUDE.git-strict.md to the installed CLAUDE.md
    python install.py --wire-hooks     show the settings.json entries the hooks need (add --apply to write)
    python install.py --python CMD     the Python command the hooks run with (default: detected)
    python install.py --profile P      starter | full (default: the last install's, else full)
    python install.py --attribution A  author | user: how dated rulings read (default: author for a
                                       new install, user where an earlier install wrote CLAUDE.md)
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

Profiles, machine facts, merge-not-overwrite (Part B, 2026-10-21):
- global/CLAUDE.md marks full-only sections with "<!-- profile: full -->" and the Environment bullets
  with "<!-- environment: begin/end -->". The markers are never installed; the Environment text is
  generated for this machine (the repo text IS the author's machine, so it comes back unchanged there).
- ~/.claude/.claude-global-standards.json records the profile, the wording and each installed file's
  sha256. A file that no longer matches its record (or, before the record existed, any committed
  version of it) was edited by you: it is KEPT and the new version written beside it as <name>.new.
- A CLAUDE.md that is yours gets the rules in a marked block after your text (MERGE); later installs
  rewrite only that block (BLOCK).

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


# ------------------------------------------------------------------ profiles, machine facts, wording

PROFILE_MARK = "<!-- profile: full -->"
ENV_BEGIN, ENV_END = "<!-- environment: begin -->", "<!-- environment: end -->"
BLOCK_BEGIN = "<!-- claude-global-standards: begin (installer-managed; edit the clone, not this block) -->"
BLOCK_END = "<!-- claude-global-standards: end -->"
FULL_ONLY_SKILLS = {"rule-hook-agent", "pattern-skill-agent"}
OWNER_PY_PATH = "%LOCALAPPDATA%\\Programs\\Python\\Python313\\python.exe"
# dated attributions of the author's own rulings: "(user rule, 2026-...", "The user, 2026-...", ...
ATTRIB_RE = re.compile(r"(?<![A-Za-z'])([Uu])ser( rule| ruling| standard| decision)?(?=, \d{4}-\d{2}-\d{2})")


def machine_facts(python_cmd):
    """What the Environment section says about this machine: os, Python command/path/version,
    whether `python3` is the Windows Store stub, and the encoding piped output gets."""
    import locale
    facts = {"os": os.name, "cmd": python_cmd, "path": python_cmd.strip('"'), "ver": "%d.%d" % sys.version_info[:2],
             "stub3": False, "enc": (locale.getpreferredencoding(False) or "utf-8").lower()}
    try:
        p = subprocess.run([python_cmd.strip('"'), "-c", "import sys; print(sys.executable); print('%d.%d' % sys.version_info[:2])"],
                           capture_output=True, text=True, timeout=20)
        lines = [l.strip() for l in p.stdout.splitlines() if l.strip()]  # a path may contain spaces
        if p.returncode == 0 and len(lines) >= 2:
            facts["path"], facts["ver"] = lines[0], lines[1]
    except (OSError, subprocess.SubprocessError):
        pass
    if os.name == "nt":
        found = shutil.which("python3") or ""
        facts["stub3"] = "windowsapps" in found.lower()
        local = os.environ.get("LOCALAPPDATA", "")
        if local and facts["path"].lower().startswith(local.lower() + "\\"):
            facts["path"] = "%LOCALAPPDATA%" + facts["path"][len(local):]
    else:
        home = str(Path.home())
        if facts["path"].startswith(home + "/"):
            facts["path"] = "~" + facts["path"][len(home):]
    if facts["enc"].replace("-", "") in ("utf8", "cp65001"):
        facts["enc"] = "utf-8"
    return facts


def environment_block(repo_block, facts, nl):
    """The Environment bullets for this machine. The repo's text is the author's machine (bare
    `python`, `python3` the Store stub, a cp1252 console); on such a machine it comes back unchanged."""
    py_text, sep, ascii_text = repo_block.partition(nl + nl + "- **Default to ASCII.**")
    ascii_text = "- **Default to ASCII.**" + ascii_text
    cmd = facts["cmd"]
    if facts["os"] == "nt" and cmd == "python" and facts["stub3"]:
        py = py_text.replace(OWNER_PY_PATH, facts["path"]).replace("currently 3.13.x", "currently %s.x" % facts["ver"])
    elif cmd.startswith('"') or "/" in cmd or "\\" in cmd:
        py = nl.join(["- **Python is invoked by its full path on this machine:** `%s` (currently %s.x). Use"
                      % (facts["path"], facts["ver"]), "  that path for any Python script or one-off."])
    else:
        py = nl.join(["- **Python is invoked as `%s` on this machine** (`%s`, currently %s.x). Always call"
                      % (cmd, facts["path"], facts["ver"]), "  `%s ...` for any Python script or one-off." % cmd])
    first, sep2, rest = ascii_text.partition(nl + nl)
    if facts["enc"] == "utf-8":
        first = nl.join([
            "- **Default to ASCII.** The console here is UTF-8, but scripts also run in consoles and logs that",
            "  are not, so keep anything written to stdout or stderr ASCII: no arrows, em dashes, ellipses, curly",
            "  quotes or box drawing. On a cp1252 console a single such character raises `UnicodeEncodeError`",
            "  and takes the whole script down. Use `->`, `-`, `...`, `'` instead."])
    else:
        first = first.replace("The console here is cp1252,", "The console here is %s," % facts["enc"])
    return py + nl + nl + first + sep2 + rest


def shape_claude_md(text, profile, facts):
    """Apply the profile and the machine facts to global/CLAUDE.md's text, and strip the markers."""
    nl = "\r\n" if "\r\n" in text else "\n"
    if ENV_BEGIN in text and ENV_END in text:
        head, _, tail = text.partition(ENV_BEGIN + nl)
        block, _, tail = tail.partition(nl + ENV_END)
        text = head + (environment_block(block, facts, nl) if facts else block) + tail
    out, skipping, own_heading = [], False, False
    for line in text.split(nl):
        if line == PROFILE_MARK:  # the marked section runs to the next "## " heading
            skipping, own_heading = profile != "full", True
            continue
        if line.startswith("## "):
            if own_heading:
                own_heading = False
            elif skipping:
                skipping = False
        if not skipping:
            out.append(line)
    return nl.join(out)


def reword_attribution(text):
    """'(user rule, 2026-...' -> '(author's rule, 2026-...', and the like, for other people's installs."""
    def sub(m):
        cap, kind = m.group(1), m.group(2) or ""
        word = ("Author" if cap == "U" else "author") + ("'s" if kind else "")
        return word + kind
    return ATTRIB_RE.sub(sub, text).replace('; the user: "Haiku', '; the author: "Haiku')


# ------------------------------------------------------------------ files

def append_strict(data, strict):
    """CLAUDE.md bytes with the strict-git block appended, in the file's own line endings."""
    extra = strict.replace(b"\r\n", b"\n")
    if b"\r\n" in data:
        extra = extra.replace(b"\n", b"\r\n")
    nl = b"\r\n" if b"\r\n" in data else b"\n"
    if not data.endswith(nl):
        data += nl
    return data + extra


def claude_md(strict_git):
    """The bytes the installed CLAUDE.md should hold: the base file, plus the strict-git block."""
    data = (REPO / "global" / "CLAUDE.md").read_bytes()
    if strict_git:
        data = append_strict(data, (REPO / "global" / "CLAUDE.git-strict.md").read_bytes())
    return data


def shape(data, name, values, opts):
    """One source file's installed bytes: profile and machine facts (CLAUDE.md), wording, placeholders."""
    opts = opts or {}
    if name.lower().endswith(".md") and (name == "CLAUDE.md" or opts.get("attribution") == "author"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return fill_bytes(data, name, values)
        if name == "CLAUDE.md":
            text = shape_claude_md(text, opts.get("profile", "full"), opts.get("facts"))
        if opts.get("attribution") == "author":
            text = reword_attribution(text)
        data = text.encode("utf-8")
    return fill_bytes(data, name, values)


def plan(parts, values, strict_git=False, target=TARGET, opts=None):
    """Yield (part, installed bytes, destination) for every file this repo installs."""
    profile = (opts or {}).get("profile", "full")
    if "global" in parts:
        yield "global", shape(claude_md(strict_git), "CLAUDE.md", values, opts), target / "CLAUDE.md"
        docs = REPO / "global" / "docs"
        if docs.is_dir():
            for src in sorted(docs.rglob("*")):
                if wanted(src):
                    yield "global", shape(src.read_bytes(), src.name, values, opts), \
                        target / "docs" / src.relative_to(docs)
    for part, sub in (("skills", "skills"), ("hooks", "hooks")):
        if part not in parts:
            continue
        root = REPO / sub
        if not root.is_dir():
            continue
        for src in sorted(root.rglob("*")):
            rel = src.relative_to(root)
            if part == "skills" and profile != "full" and rel.parts[0] in FULL_ONLY_SKILLS:
                continue
            if wanted(src):
                yield part, shape(src.read_bytes(), src.name, values, opts), target / sub / rel
    # machine-local skills (gitignored, never published): local-skills/<name>/ -> ~/.claude/skills/<name>/
    local = REPO / "local-skills"
    if "skills" in parts and local.is_dir():
        for d in sorted(p for p in local.iterdir() if p.is_dir()):
            if (REPO / "skills" / d.name).exists():
                raise InstallError("local-skills/%s has the same name as a shipped skill; rename it" % d.name)
            for src in sorted(d.rglob("*")):
                if wanted(src):
                    yield "skills", shape(src.read_bytes(), src.name, values, opts), \
                        target / "skills" / src.relative_to(local)


def classify(a, dst):
    if not dst.exists():
        return "ADD"
    b = dst.read_bytes()
    if a == b:
        return "same"
    if a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n"):
        return "CHANGE (line endings only)"
    return "CHANGE"


# ------------------------------------------------------------------ manifest: never overwrite your edits

MANIFEST_NAME = ".claude-global-standards.json"


def sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


PROFILES, ATTRIBUTIONS = ("starter", "full"), ("author", "user")


def load_manifest(target=TARGET):
    """The record of the last install, or {} when it is missing or unreadable. Values the installer
    does not know are dropped (and the file is backed up before it is next rewritten)."""
    try:
        m = json.loads((target / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(m, dict):
        return {}
    files = m.get("files")
    out = {"files": {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str)}
           if isinstance(files, dict) else {}}
    if m.get("profile") in PROFILES:
        out["profile"] = m["profile"]
    if m.get("attribution") in ATTRIBUTIONS:
        out["attribution"] = m["attribution"]
    return out


def source_of(rel):
    """The repo path a destination (relative to ~/.claude, posix) is installed from."""
    if rel == "CLAUDE.md":
        return "global/CLAUDE.md"
    if rel.startswith("docs/"):
        return "global/" + rel
    return rel  # skills/... and hooks/... sit at the same path in the repo


def prior_hashes(rel, values, facts=None, _cache={}):
    """sha256 of every committed version of this file as any earlier install could have written it:
    the raw fill (installs before profiles existed) and every profile x wording x strict-git shape.
    A file matching one was put there by this installer, so replacing it loses nothing. Without git
    (a zip download) the set is empty and an existing file is treated as the user's."""
    if rel in _cache:
        return _cache[rel]
    src, out = source_of(rel), set()
    name = Path(src).name
    strict = (REPO / "global" / "CLAUDE.git-strict.md").read_bytes() if rel == "CLAUDE.md" else None
    try:
        log = subprocess.run(["git", "-C", str(REPO), "log", "--format=%H", "--", src],
                             capture_output=True, text=True, timeout=30)
        for h in log.stdout.split() if log.returncode == 0 else []:
            p = subprocess.run(["git", "-C", str(REPO), "show", "%s:%s" % (h, src)], capture_output=True, timeout=30)
            if p.returncode != 0:
                continue
            variants = [p.stdout]
            if strict is not None:
                variants.append(append_strict(p.stdout, strict))
            for raw in variants:
                out.add(sha(fill_bytes(raw, name, values)))
                for profile in PROFILES:
                    for attribution in ATTRIBUTIONS:
                        opts = {"profile": profile, "attribution": attribution, "facts": facts}
                        out.add(sha(shape(raw, name, values, opts)))
    except (OSError, subprocess.SubprocessError):
        pass
    _cache[rel] = out
    return out


def guess_attribution(values, facts=None, target=TARGET):
    """'user' when ~/.claude/CLAUDE.md is one this installer wrote with the original wording before the
    manifest existed (the author's own machine); 'author' for everyone else."""
    cur = target / "CLAUDE.md"
    if not cur.is_file():
        return "author"
    data = cur.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "author"
    if sha(data) in prior_hashes("CLAUDE.md", values, facts) and "(author's rule, " not in text:
        return "user"
    return "author"


def guess_profile(target=TARGET):
    """With no record: 'starter' when the installed setup looks like one (project-setup present, the
    full-only skills absent), else 'full'."""
    skills = target / "skills"
    if (skills / "project-setup").is_dir() and not any((skills / n).is_dir() for n in FULL_ONLY_SKILLS):
        return "starter"
    return "full"


def with_block(current, data):
    """CLAUDE.md that keeps the user's own text and carries the toolkit's rules in one marked block.
    Refuses (InstallError) a file that is not UTF-8 or whose markers are broken, rather than guess."""
    try:
        text = current.decode("utf-8")
    except UnicodeDecodeError:
        raise InstallError("~/.claude/CLAUDE.md is not UTF-8 text; convert it to UTF-8 first, or move it "
                           "aside, so the installer can keep your text without damaging it (skills alone "
                           "still install with --only skills)")
    nl = "\r\n" if "\r\n" in text else ("\n" if "\n" in text else
                                       ("\r\n" if b"\r\n" in data else "\n"))
    new = data.decode("utf-8").replace("\r\n", "\n").replace("\n", nl)
    block = BLOCK_BEGIN + nl + new.rstrip("\r\n") + nl + BLOCK_END
    nb, ne = text.count(BLOCK_BEGIN), text.count(BLOCK_END)
    if nb == 0 and ne == 0:
        return (text.rstrip("\r\n") + nl + nl + block + nl).encode("utf-8")
    if nb == 1 and ne == 1 and text.index(BLOCK_BEGIN) < text.index(BLOCK_END):
        head, _, rest = text.partition(BLOCK_BEGIN)
        _, _, tail = rest.partition(BLOCK_END)
        return (head + block + tail).encode("utf-8")
    raise InstallError("~/.claude/CLAUDE.md has broken toolkit markers (%d begin, %d end, or in the wrong "
                       "order); fix them by hand so there is one begin line before one end line, or remove "
                       "both (skills alone still install with --only skills)" % (nb, ne))


def decide(rel, data, dst, manifest, values, facts=None):
    """(state, bytes to write, path to write) for one destination. States: ADD, same, CHANGE,
    BLOCK (CLAUDE.md: only the toolkit's marked block changes), MERGE (a CLAUDE.md the installer never
    wrote: your text stays and the rules go into a marked block after it), KEEP (a file you edited
    after it was installed: it is left alone and the new version goes beside it as <name>.new)."""
    if not dst.exists():
        return "ADD", data, dst
    cur = dst.read_bytes()
    if cur == data:
        return "same", data, dst
    if rel == "CLAUDE.md" and (BLOCK_BEGIN.encode() in cur or BLOCK_END.encode() in cur):
        new = with_block(cur, data)
        return ("same" if new == cur else "BLOCK"), new, dst
    recorded = manifest.get("files", {}).get(rel)
    if recorded:
        ours, known = sha(cur) == recorded, True
    else:
        ours = sha(cur) in prior_hashes(rel, values, facts)
        known = ours
    if ours:
        return classify(data, dst), data, dst
    if rel == "CLAUDE.md" and not known:
        return "MERGE", with_block(cur, data), dst
    new_path = dst.with_name(dst.name + ".new")
    if new_path.exists() and new_path.read_bytes() == data:
        return "same", data, new_path  # already offered
    return "KEEP", data, new_path


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
        backup = TARGET / "staging" / "install-backups" / ("settings-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
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

    # profiles, machine facts, wording, the CLAUDE.md block (B, 2026-10-21)
    raw = claude_md(False).decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    owner_f = {"os": "nt", "cmd": "python", "path": OWNER_PY_PATH, "ver": "3.13", "stub3": True, "enc": "cp1252"}
    plain = nl.join(l for l in raw.split(nl) if l not in (PROFILE_MARK, ENV_BEGIN, ENV_END))
    check(shape_claude_md(raw, "full", owner_f) == plain, "the author's machine facts must reproduce the repo text")
    check(shape_claude_md(raw, "full", None) == plain, "no facts: the repo text, markers stripped")
    starter = shape_claude_md(raw, "starter", owner_f)
    check("## Hooks and skills" not in starter and "## Session and scratch files" in starter
          and "## Safety" in starter, "starter drops only the marked section")
    check(nl + nl + nl not in starter, "starter leaves no double blank line where the section was")
    linux = shape_claude_md(raw, "full", {"os": "posix", "cmd": "python3", "path": "/usr/bin/python3",
                                          "ver": "3.12", "stub3": False, "enc": "utf-8"})
    check("Python is invoked as `python3` on this machine** (`/usr/bin/python3`, currently 3.12.x)" in linux
          and "Windows Store" not in linux and "The console here is UTF-8" in linux and "cp1252," not in linux
          and "reconfigure the" in linux, "a Linux machine gets its own Environment text")
    win = shape_claude_md(raw, "full", {"os": "nt", "cmd": "python", "path": "C:\\Python312\\python.exe",
                                        "ver": "3.12", "stub3": False, "enc": "cp1250"})
    check("as `python` on this machine** (`C:\\Python312\\python.exe`" in win and "Store alias" not in win
          and "The console here is cp1250," in win, "Windows without the Store stub, another code page")
    full_path = shape_claude_md(raw, "full", {"os": "nt", "cmd": '"C:/Program Files/Py/python.exe"',
                                              "path": "C:\\Program Files\\Py\\python.exe", "ver": "3.11",
                                              "stub3": True, "enc": "cp1252"})
    check("by its full path on this machine:** `C:\\Program Files\\Py\\python.exe`" in full_path, "a full-path Python")
    for before, after in (("(user rule, 2026-09-28: x)", "(author's rule, 2026-09-28: x)"),
                          ("(user ruling, 2026-10-20).", "(author's ruling, 2026-10-20)."),
                          ("The user, 2026-10-20: a", "The author, 2026-10-20: a"),
                          ("User ruling, 2026-10-20:", "Author's ruling, 2026-10-20:"),
                          ("Rule (user, 2026-10-20)", "Rule (author, 2026-10-20)"),
                          ("(User, 2026-10-21: \"A", "(Author, 2026-10-21: \"A"),
                          ("(user standard, 2026-09-28)", "(author's standard, 2026-09-28)"),
                          ("ask the user, then", "ask the user, then"),
                          ("the user's approval (2026-09-28)", "the user's approval (2026-09-28)"),
                          ("the user rule, see", "the user rule, see")):
        check(reword_attribution(before) == after, "reword %r -> %r, got %r" % (before, after, reword_attribution(before)))
    mine = b"# my rules\r\n- be nice\r\n"
    merged = with_block(mine, b"# toolkit\r\n- rule\r\n")
    check(merged.startswith(mine.rstrip()) and BLOCK_BEGIN.encode() in merged and b"- rule" in merged
          and merged.endswith(BLOCK_END.encode() + b"\r\n"), "a merge keeps the user's text first")
    again = with_block(merged, b"# toolkit\r\n- rule two\r\n")
    check(again.startswith(mine.rstrip()) and b"- rule two" in again and b"- rule\r\n" not in again
          and again.count(BLOCK_BEGIN.encode()) == 1, "a later install replaces only the block")
    check(with_block(again, b"# toolkit\r\n- rule two\r\n") == again, "an unchanged block is a no-op")
    lf = with_block(b"# mine\n- x\n", b"# toolkit\r\n- rule\r\n")
    check(b"\r\n" not in lf and lf.startswith(b"# mine\n- x\n"), "an LF CLAUDE.md stays LF around the block")
    for broken in (b"# mine\n" + BLOCK_BEGIN.encode() + b"\nstray\n",
                   b"# mine\n" + BLOCK_END.encode() + b"\nx\n" + BLOCK_BEGIN.encode() + b"\n",
                   b"# mine\n" + (BLOCK_BEGIN + "\na\n" + BLOCK_END + "\n").encode() * 2,
                   b"# m\xfc\xdfe\n"):
        try:
            with_block(broken, b"# toolkit\n")
            check(False, "broken markers or non-UTF-8 must be refused: %r" % broken[:40])
        except InstallError:
            check(True, "")
    check(load_manifest(Path("Z:/no/such/place")) == {}, "a missing manifest is {}")
    check(source_of("docs/x.md") == "global/docs/x.md" and source_of("CLAUDE.md") == "global/CLAUDE.md"
          and source_of("skills/a/SKILL.md") == "skills/a/SKILL.md", "destination -> repo source mapping")

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
    ap.add_argument("--profile", choices=["starter", "full"],
                    help="starter: core rules + project-setup and adopt-project-standards; full: everything "
                         "(default: the last install's, else full)")
    ap.add_argument("--attribution", choices=["author", "user"],
                    help="how dated rulings read: 'author's rule' (default for a new install) or 'user rule'")
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

    manifest = load_manifest()
    facts = machine_facts(values["python"])
    profile = a.profile or manifest.get("profile") or guess_profile()
    try:
        attribution = a.attribution or manifest.get("attribution") or guess_attribution(values, facts)
        opts = {"profile": profile, "attribution": attribution, "facts": facts}
        print("profile: %s; rule attribution: %s" % (profile, attribution))
        rows = []
        for part, data, dst in plan(parts, values, a.strict_git, opts=opts):
            rel = dst.relative_to(TARGET).as_posix()
            state, out, path = decide(rel, data, dst, manifest, values, facts)
            rows.append((part, rel, out, path, state))
    except InstallError as e:
        print("error: %s" % e)
        return 2
    if profile == "starter" and "skills" in parts:
        for name in sorted(FULL_ONLY_SKILLS):
            if (TARGET / "skills" / name).is_dir():
                print("left installed (not part of starter; remove it yourself if unwanted): %s"
                      % (TARGET / "skills" / name))
    notes = {"KEEP": "  (you edited it; it is kept, the new version goes to this .new file)",
             "MERGE": "  (your own CLAUDE.md: kept, the rules are added in a marked block)",
             "BLOCK": "  (only the toolkit's marked block changes)"}
    changes = [r for r in rows if r[4] != "same"]
    for part, rel, out, path, state in changes:
        print("%-28s %s%s" % (state, path, notes.get(state, "")))
    print("%d file(s) to add or change, %d already identical" % (len(changes), len(rows) - len(changes)))
    for f in extras(parts):
        if not f.name.endswith(".new"):
            print("extra (left alone): %s" % f)

    backup = TARGET / "staging" / "install-backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    if a.apply and changes:
        for part, rel, out, path, state in changes:
            if path.exists():
                keep = backup / path.relative_to(TARGET)
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, keep)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(out)
            if path.read_bytes() != out:
                print("FAILED to verify %s" % path)
                return 1
        print("installed %d file(s); replaced files backed up under %s" % (len(changes), backup))
    elif a.apply:
        print("files: nothing to do.")
    else:
        print("dry run - nothing written. Re-run with --apply to install.")
    if a.apply:
        files = dict(manifest.get("files", {}))
        for part, rel, out, path, state in rows:
            if state != "KEEP" and path.name == Path(rel).name:
                files[rel] = sha(path.read_bytes())
        save = {"attribution": attribution, "files": files}
        # the profile is only recorded by a run that shaped both CLAUDE.md and the skill set
        if {"global", "skills"} <= parts:
            save["profile"] = profile
        elif "profile" in manifest:
            save["profile"] = manifest["profile"]
        mpath = TARGET / MANIFEST_NAME
        text = json.dumps(save, indent=2, sort_keys=True) + "\n"
        if not mpath.exists() or mpath.read_text(encoding="utf-8", errors="replace") != text:
            if mpath.exists():
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(mpath, backup / MANIFEST_NAME)
            mpath.write_text(text, encoding="utf-8")

    if a.wire_hooks:
        try:
            return wire_hooks(values, a.apply)
        except InstallError as e:
            print("error: %s" % e)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
