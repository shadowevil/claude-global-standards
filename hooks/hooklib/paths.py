"""Root resolution, Windows path normalization and the allow-list, for any PreToolUse hook event.

Nothing here names a specific project. The root is always read from the event/environment:
CLAUDE_PROJECT_DIR if the hook host sets it, else the event's own `cwd`. This is what lets the
same file sit under any project's .claude/hooks/ OR under ~/.claude/hooks/ as a global hook
without edits - ported verbatim from the origin project hook's W3 section, so a future change
to one is a change to port to the other, not a divergent rewrite.
"""
import ctypes
import os
import re
from functools import lru_cache

# tempfile is NOT imported here at module level - see msys_to_windows/temp_roots,
# both of which import it lazily, only on the (uncommon) path that actually needs it. Neither is
# pathlib imported here - see home_dir(). Both cost real time on EVERY cold hook invocation
# (measured, 2026-09-28 perf review: ~90ms of a ~118ms median call was import overhead), and every
# PreToolUse call pays for a fresh interpreter with no warm import cache, so an unconditional
# module-level import here is not "paid once" - it is paid on every single tool call, in every
# project, whether or not this hook ever needs it.
MSYS_DRIVE = re.compile(r"^/([A-Za-z])(/.*)?$")
TMP_PATH = re.compile(r"^/tmp(/.*)?$", re.I)
SHORT83_SEG = re.compile(r"^[^\\/]{1,8}~\d[^\\/]*$")
# A plausible ABSOLUTE Windows path shape: drive-letter (C:\...) or UNC (\\server\share...). Used
# only to sanity-check os.path.realpath()'s OWN output (see resolve_real_prefix below) - never to
# validate a raw, not-yet-resolved target, which may legitimately be relative at that point.
PLAUSIBLE_ABS_WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:\\|\\\\)")


def project_root(event):
    """The session's project root: CLAUDE_PROJECT_DIR from the hook environment, else the hook
    input's own `cwd`. Never hard-coded to any one project. Claude Code itself sets
    CLAUDE_PROJECT_DIR for every hook invocation, so the `cwd` fallback is normally only exercised
    offline (replay, a hand-built event) - it is kept because nothing here may assume a live host.

    Short-name-expanded (2026-09-28 fix): resolve()/violation() always expand a TARGET's short
    (8.3) name segments (SHADOW~1 -> ShadowEvil) before comparing it against a root, but this
    function never expanded the ROOT side of that same comparison - a root reported to the hook in
    8.3 form (CLAUDE_PROJECT_DIR or `cwd` from a shell that resolves it that way) would then never
    prefix-match its own, correctly-expanded targets, so every write under it looked like it fell
    OUTSIDE the allowed root. Both W3 and W4 call this, so both inherited the gap."""
    root = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ""
    if not root:
        return None
    return resolve_real_prefix(expand_short_names(os.path.normpath(root)))


def home_dir():
    """The user's home directory via the environment (USERPROFILE on Windows, HOME elsewhere) -
    not pathlib.Path.home(), which does the equivalent lookup itself but the `pathlib` import alone
    measured as a meaningful share of hot-path latency (2026-09-28 perf review) for a value the
    environment already hands out for free. Empty string if neither is set.

    Short-name-expanded (2026-09-28 fix, same gap as project_root() above): USERPROFILE itself can
    be reported in 8.3 form - temp_roots() has to expand tempfile.gettempdir()
    for exactly this reason, but this function's own result fed allowed_roots()/W4's guarded-path
    checks unexpanded. expand_short_names is a safe no-op on a non-Windows-drive path (POSIX HOME),
    so this costs nothing there."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if not home:
        return ""
    return resolve_real_prefix(expand_short_names(os.path.normpath(home)))


def msys_to_windows(p):
    """An MSYS/Git-Bash path as `C:\\Users\\...`: `/c/Users/...` drive-letter form, `/tmp/...`
    mapped to the REAL temp directory (tempfile.gettempdir(), not a literal `C:\\tmp` - Git Bash's
    `/tmp` is a symlink/mount to %TEMP%, so reporting anything else would name a path that was
    never actually the write's target); any other path just gets its slashes normalized to
    backslashes. `tempfile` is imported lazily, right here, only for the actual `/tmp/...` case -
    every other call (the overwhelming majority) never pays for it."""
    if not p:
        return p
    p = p.strip()
    m = MSYS_DRIVE.match(p)
    if m:
        rest = (m.group(2) or "").lstrip("/")
        return m.group(1).upper() + ":\\" + rest.replace("/", "\\")
    m = TMP_PATH.match(p)
    if m:
        import tempfile
        rest = (m.group(1) or "").lstrip("/").replace("/", "\\")
        base = tempfile.gettempdir().rstrip("\\")
        return base + ("\\" + rest if rest else "")
    return p.replace("/", "\\")


def expand_short_names(path):
    """path with any existing-ancestor Windows 8.3 short-name segment (SHADOW~1, ...) expanded to
    its long form via the OS. Only queries the filesystem when a segment looks 8.3-shaped, and only
    down to the first ancestor that does not exist - nothing not yet on disk can have a short-name
    alias, so a freshly-typed new file/dir tail is left exactly as written. Fails open per segment:
    any OS error just keeps that segment as typed."""
    if not re.match(r"^[A-Za-z]:\\", path):
        return path
    parts = [p for p in path[3:].split("\\") if p]
    if not any(SHORT83_SEG.match(p) for p in parts):
        return path
    drive = path[:3]
    resolved = drive
    out = []
    existing = True
    for part in parts:
        candidate = resolved + part
        if existing and os.path.exists(candidate):
            try:
                buf = ctypes.create_unicode_buffer(4096)
                n = ctypes.windll.kernel32.GetLongPathNameW(candidate, buf, 4096)
                if n and n < 4096:
                    candidate = buf.value
                    part = candidate.rsplit("\\", 1)[-1]
            except Exception:
                pass
            resolved = candidate + "\\"
        else:
            existing = False
            resolved = candidate + "\\"
        out.append(part)
    return drive + "\\".join(out)


@lru_cache(maxsize=128)
def resolve_real_prefix(path):
    """`path` with any NTFS junction or symlink reparse point along its EXISTING prefix resolved to
    its real target, via os.path.realpath. 2026-09-28 review: `mklink /J` (an NTFS junction, no
    admin rights needed) makes a write through a path that LOOKS unguarded land inside a guarded
    tree, or vice versa - the reparse point's own name proves nothing about where a write actually
    lands, so both W3 and W4 inherited the gap.

    realpath is used rather than a hand-rolled ancestor walk: probed empirically (2026-09-28) that
    Windows' realpath (Python 3.8+, via GetFinalPathNameByHandle) already resolves a real junction
    correctly AND leaves a not-yet-created tail segment (the new file/dir itself) appended,
    unresolved, after the resolved existing prefix - exactly the "existing prefix only" behaviour
    this needed, with no extra ancestor-walking code of our own to get wrong. It is also a safe
    no-op on an ordinary path with no reparse point (returns it unchanged, not `\\\\?\\`-prefixed or
    re-cased) and on a path with no real ancestor at all (a fake drive, or this file's own
    synthetic self-test roots) - it returns the input unchanged rather than raising. Measured
    (2026-09-28): ~0.03-0.06ms per call even for a nonexistent path, so applying it to both a root
    and a target costs a fraction of a millisecond against this hook's multi-second budget.

    Still wrapped in try/except and fails open (returns `path` unchanged) on any error - a real
    filesystem probe going wrong here must never turn a write into a false verdict either way.

    Result-shape guard (2026-09-28 hotfix): a garbage "target" mis-extracted by the shell parser
    (e.g. a nested-quote nightmare inside a `python -c` heredoc, misread as a redirect - see
    hooklib/shell.py's mask_quotes docstring for the exact transcript) does NOT make realpath raise;
    it can hand back a nonsense string that no longer even LOOKS like an absolute Windows path,
    which then fails every prefix check against a real root and gets treated as "outside" instead of
    the harmless non-path text it actually was. realpath's own output is trusted only when it is
    still drive-letter (`C:\\...`) or UNC (`\\\\server\\share...`) shaped; anything else falls back
    to the pre-realpath value, exactly as if this function were never called.

    Memoized (2026-09-28 latency hotfix, lru_cache(maxsize=128)): project_root(), home_dir() and
    resolve() each call this independently, so a single Write/Edit PreToolUse event was measured
    calling it 9 times over just 3 distinct strings (the project root, the home dir, and the
    target). Safe here specifically because every hook invocation is a fresh, short-lived
    interpreter process - the cache never outlives one event, so there is no staleness risk from a
    junction/symlink changing mid-process."""
    if not path:
        return path
    try:
        resolved = os.path.realpath(path)
    except Exception:
        return path
    if PLAUSIBLE_ABS_WINDOWS_PATH.match(resolved):
        return resolved
    return path


def allowed_roots(event):
    """[project root, home()/.claude, every permissions.additionalDirectories entry] - the LITERAL
    directories a write may land under (see side_notes_regex below for
    the pattern-shaped allowances). Any may be missing; callers only need at least one to check
    against. The ~/.claude entry is what lets Claude Code's own config/memory/skills be written
    from any project session - it is deliberately the WHOLE home-relative .claude tree, not a
    narrower slice, because this hook has no way to know which part of it (memory, a skill, a hook
    it is itself about to replace) a given session legitimately needs to touch.

    additionalDirectories (2026-10-17, FP4): directories the user granted Claude Code in the
    USER-level ~/.claude/settings.json only - a project-level settings file lives inside the root,
    so a session could grant itself anything by writing one. Over-broad entries are refused (see
    settings_additional_dirs). The event's cwd is deliberately NOT added."""
    roots = []
    root = project_root(event)
    if root:
        roots.append(os.path.normpath(msys_to_windows(root)))
    home = home_dir()
    if home:
        roots.append(os.path.normpath(os.path.join(home, ".claude")))
    roots.extend(settings_additional_dirs(home))
    return roots


# an additionalDirectories entry must name a real subtree: a drive root, a bare UNC share, a
# \\?\ device path, home or an ancestor of it would allow nearly every write on the machine
MIN_COMPONENTS_BELOW_DRIVE = 2
RAW_TOO_BROAD = re.compile(r"^(?:~|[\\/]+|[\\/]*[A-Za-z]:?[\\/]*|[\\/]{2}[A-Za-z][\\/]*)$")


def grant_is_acceptable(raw, resolved, home):
    """False for an additionalDirectories entry that is too broad to honour."""
    if RAW_TOO_BROAD.match(raw.strip()):
        return False
    if raw.strip().startswith(("\\\\?\\", "//?/", "\\\\.\\", "//./")):
        return False
    if not re.match(r"^[A-Za-z]:\\", resolved):
        return False  # UNC shares and anything not drive-anchored: refused outright
    parts = [p for p in resolved[3:].split("\\") if p]
    if len(parts) < MIN_COMPONENTS_BELOW_DRIVE:
        return False
    if home:
        h = os.path.normcase(home.rstrip("\\")) + "\\"
        r = os.path.normcase(resolved.rstrip("\\")) + "\\"
        if h.startswith(r):
            return False  # home itself or one of its ancestors
    return True


@lru_cache(maxsize=8)
def settings_additional_dirs(home):
    """Every acceptable permissions.additionalDirectories entry from the USER-level
    ~/.claude/settings.json, as normalized absolute Windows paths (`~` expands to home). Fails
    open: a missing or malformed file contributes nothing."""
    import json  # lazy - only paid when a write is actually being judged
    out = []
    if not home:
        return ()
    for f in (os.path.join(home, ".claude", "settings.json"),):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            dirs = (data.get("permissions") or {}).get("additionalDirectories") or []
        except Exception:
            continue
        if not isinstance(dirs, list):
            continue
        for d in dirs:
            if not isinstance(d, str) or not d.strip():
                continue
            raw = d.strip()
            d = raw
            if d.startswith("~/") or d.startswith("~\\"):
                d = home + d[1:]
            if d.startswith("//") and len(d) > 3 and d[2].isalpha() and d[3] == "/":
                d = d[1:]  # the settings docs' //c/path form for an absolute Windows path
            d = msys_to_windows(d)
            if not re.match(r"^[A-Za-z]:\\", d):
                continue  # relative, UNC or device: never honoured
            d = resolve_real_prefix(expand_short_names(os.path.normpath(d)))
            if grant_is_acceptable(raw, d, home):
                out.append(d)
    return tuple(out)


def side_notes_regex():
    """A compiled regex for the Claude desktop app's own side-session notes,
    %LOCALAPPDATA%/Claude-Data/<profile>/side-session-notes/... (2026-10-17, FP6) - the app
    writes these itself on the user's behalf. None when no local-appdata directory is known."""
    base = os.environ.get("LOCALAPPDATA") or ""
    if not base:
        home = home_dir()
        if not home:
            return None
        base = os.path.join(home, "AppData", "Local")
    base = expand_short_names(os.path.normpath(base)).rstrip("\\")
    try:
        return re.compile(r"^" + re.escape(base) + r"\\Claude-Data\\[^\\]+\\side-session-notes(\\.*)?$",
                          re.IGNORECASE)
    except re.error:
        return None


def temp_roots():
    """The system temp directories, resolved: tempfile.gettempdir() (which Git Bash's /tmp maps
    to), %TEMP%, %TMP%, $TMPDIR, and <local appdata>/Temp. Any candidate that is too broad to be a
    temp folder (a drive root, home or an ancestor of it) is ignored, the same test an
    additionalDirectories grant must pass. Fails open per candidate."""
    home = home_dir()
    cands = []
    try:
        import tempfile  # lazy - see the module docstring note by the imports
        cands.append(tempfile.gettempdir())
    except Exception:
        pass
    for var in ("TEMP", "TMP", "TMPDIR"):
        if os.environ.get(var):
            cands.append(os.environ[var])
    local = os.environ.get("LOCALAPPDATA") or (os.path.join(home, "AppData", "Local") if home else "")
    if local:
        cands.append(os.path.join(local, "Temp"))
    out = []
    for c in cands:
        try:
            d = msys_to_windows(c.strip())
            if not re.match(r"^[A-Za-z]:\\", d):
                continue
            d = resolve_real_prefix(expand_short_names(os.path.normpath(d)))
            if grant_is_acceptable(d, d, home) and d.lower() not in (x.lower() for x in out):
                out.append(d)
        except Exception:
            continue
    return out


def is_temp(target):
    """True when an already-resolved target lies in (or is) a system temp directory."""
    return any(is_inside(t, target) for t in temp_roots())


def is_inside(root, target):
    root_n = os.path.normcase(root.rstrip("\\")) + "\\"
    target_n = os.path.normcase(target.rstrip("\\")) + "\\"
    return target_n.startswith(root_n)


def resolve(raw_target, event, cwd=None):
    """raw_target resolved to an absolute Windows path: MSYS-mapped, joined against the hook
    input's cwd if relative (also MSYS-mapped), `..` collapsed lexically by os.path.normpath (never
    touching the filesystem), short names expanded, then any junction/symlink reparse point along
    its EXISTING prefix resolved to its real target (resolve_real_prefix, 2026-09-28 fix). `cwd`, when
    given, replaces the event's cwd as the base for a relative target (a tracked `cd`, 2026-10-17)."""
    target = msys_to_windows(raw_target.strip().strip('"').strip("'"))
    if not re.match(r"^[A-Za-z]:\\", target) and not target.startswith("\\\\"):
        base = msys_to_windows(cwd if cwd is not None else (event.get("cwd") or ""))
        target = os.path.normpath(os.path.join(base, target)) if base else os.path.normpath(target)
    else:
        target = os.path.normpath(target)
    return resolve_real_prefix(expand_short_names(target))


def violation(raw_target, event, cwd=None):
    """The resolved path if `raw_target` (as typed) falls outside every allowed root, else None.
    The harness scratchpad is no longer allowed (user rule, 2026-10-18: temp writes are W5's to
    block); a temp target that W5 reports is de-duplicated out of W3 by write_guard. Reads the filesystem (stat calls only, via
    expand_short_names/resolve_real_prefix - never a write) to resolve short names and any
    junction/symlink reparse point; never touches the network."""
    if not raw_target:
        return None
    roots = allowed_roots(event)
    if not roots:
        return None
    target = resolve(raw_target, event, cwd)
    if any(is_inside(r, target) or os.path.normcase(target) == os.path.normcase(r) for r in roots):
        return None
    notes_re = side_notes_regex()
    if notes_re is not None and notes_re.match(target):
        return None
    return target
