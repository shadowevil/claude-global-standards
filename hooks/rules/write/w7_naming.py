"""W7: a new file or folder whose name breaks the naming rule (user rule, 2026-11-23, global
~/.claude/CLAUDE.md, "Session and scratch files", bullet "File and folder names": "Code files and
their folders follow their language's convention (C++ and Python: `snake_case`). Markdown docs and
their sidecar folders follow the docs standard: lowercase, hyphenated. Names a tool requires stay as
they are (`CMakeLists.txt`, `README.md`, `CLAUDE.md`, `Makefile`, `__init__.py`), and so do
third-party trees. New files follow this now. Renaming existing files is a planned, dry-run and
reviewed job, never a side effect of other work.")

ALWAYS_LOG_ONLY, like W4: never denies, never prints, never in an --enforce list.

What W7 judges: a CREATION inside the session's project root (CLAUDE_PROJECT_DIR; no cwd
fallback - no root, no judgement) whose own name (the last path segment) breaks its convention:

    kind "code"     a file whose extension maps to a code convention (CONVENTIONS) and whose stem
                    does not match it: HMAC.cpp, NewDialog.h, my-tool.py
    kind "doc"      a Markdown file whose stem is not lowercase-hyphenated: My_Doc.md, my_doc.md
    kind "doc-dir"  a folder created beside a same-named .md (a docs sidecar) that is not
                    lowercase-hyphenated
    kind "dir"      a new folder whose PARENT holds code of exactly one listed convention (its
                    language is unknown at creation, so the parent's code decides) and whose name
                    has upper case, a space or a dot: NewModule, v1.2. Hyphen-vs-underscore is not
                    judged for folders; a parent with C#/Java/JS/... code, or none, is not judged.

Never judged: an existing target (overwrite, `mkdir -p` of an existing folder; renaming existing
names is the planned migration's job), Edit (never creates), a MOVE that keeps its basename (same
name, new path is not a new name; a case-only rename `mv a.cpp A.cpp` IS judged), file types with
no convention in the table (.txt .json .png ...), tool-required names (TOOL_NAMES), dotfiles,
backups (BACKUP_MARKERS: `.bak`, `.prev.`, `.orig.`, `.old.` in the name), anything outside the
root, `.claude/scratch/**` (temporary), third-party trees (any path segment in THIRD_PARTY_SEGMENTS,
plus project-relative prefixes listed in an optional <root>/.claude/naming-exempt.txt), UNC and
//host targets (never stat'ed), and targets W3's parser cannot resolve statically.

Targets come from W3's extractor (rules/write/w3_outside_root.find_write_targets_cwd), post-processed
here: delete verbs are dropped, cp/mv/install/ln/copy/move to a folder become DEST/basename(SRC),
Rename-Item/ren/rni put -NewName beside the source, New-Item -Name is joined to -Path, and W7 adds
`git mv` and `[IO.Directory]::CreateDirectory(...)`, which W3 does not extract.

Cost caps (review fix, 2026-11-23): at most MAX_TARGETS targets or BUDGET_SECONDS of wall clock per
call (then one `budget-stop` row), each folder listed once per call and only its first LISTDIR_MAX
entries, and naming-exempt.txt read only up to EXEMPT_MAX_BYTES / EXEMPT_MAX_LINES.
"""
import os
import re
import time

from hooklib import paths
from hooklib import shell
from hooklib.output import excerpt
from rules.write import w3_outside_root as w3

RULE_ID = "W7"
ALWAYS_LOG_ONLY = True
MAX_ROWS = 50  # per call; one command cannot flood the hit log (review fix 2026-11-23)

# One table: a convention and the extensions that follow it. Add a language by adding a line (and,
# for a new convention name, its pattern in PATTERNS).
CONVENTIONS = {
    "snake_case": (".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx", ".h++", ".inl",
                   ".ipp", ".tpp", ".py", ".pyw", ".pyi"),
    "kebab-case": (".md", ".markdown"),
}
PATTERNS = {
    "snake_case": re.compile(r"^[a-z0-9_]+$"),
    "kebab-case": re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
}
EXT_CONVENTION = {ext: conv for conv, exts in CONVENTIONS.items() for ext in exts}
KIND_FOR = {"snake_case": "code", "kebab-case": "doc"}
DOC_CONVENTION = "kebab-case"

# Names a tool or framework requires, compared case-insensitively on the whole name.
TOOL_NAMES = {"cmakelists.txt", "makefile", "gnumakefile", "dockerfile", "__init__.py",
              "__main__.py", "readme.md", "claude.md", "changelog.md", "license.md", "licence.md",
              "contributing.md", "code_of_conduct.md", "security.md", "authors.md", "notice.md",
              "agents.md", "skill.md", "memory.md", "rules.md"}

# Generic third-party segments, at any depth, case-insensitive. "dependencies" is on the list by
# decision (coordinator, 2026-11-23): it exempts real vendored trees, at the cost of false
# negatives where a Dependencies/ folder also holds first-party code (see RULES.md).
THIRD_PARTY_SEGMENTS = {"vendor", "third_party", "third-party", "external", "extern",
                        "node_modules", "_deps", "dependencies", ".git"}
EXEMPT_FILE = os.path.join(".claude", "naming-exempt.txt")
# a backup or previous copy keeps its original's name (cross-project replay, 2026-11-23: RESUME.prev.md)
BACKUP_MARKERS = (".bak", ".prev.", ".orig.", ".old.")

DOTNET_MKDIR = re.compile(r"\[(?:System\.)?IO\.Directory\]::CreateDirectory\(\s*(['\"])(.+?)\1",
                          re.I)
COPY_VERBS = {"cp", "install", "ln", "copy"}
MOVE_VERBS = {"mv", "move"}
PS_COPY_VERBS = {"copy-item", "cpi", "copy"}
PS_MOVE_VERBS = {"move-item", "mi", "move"}
RENAME_VERBS = {"rename-item", "rni", "ren"}
MKDIR_VERBS = {"mkdir", "md"}
# Repository checkouts are not language folders: never judged (cross-project replay, 2026-11-23:
# worktree and clone destinations were 3 of 7 false positives in the hand-checked sample).
REPO_VERBS = {"git clone", "git worktree add"}
PS_PATH_FLAGS = ("-path", "-literalpath", "-lp", "-pspath")
URL_LIKE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:(?://|\\\\|/)|^[^\\/]*@[^\\/]*:")  # https://, git@host:
# a PowerShell target computed by a subexpression - (Join-Path $x 'Foo'), $(...), [IO.Path]::...:
# the literal token W3 hands over is not the path that gets created
PS_SUBEXPR = re.compile(r"\(\s*(?:Join-Path|Resolve-Path|Split-Path|Get-|\$|\[)", re.I)
# code files of a language with NO convention in CONVENTIONS: a parent folder holding them is not
# judged (C#, Java, ... use PascalCase folders, which are correct there)
OTHER_CODE_EXTS = {".cs", ".csproj", ".vb", ".fs", ".java", ".kt", ".kts", ".scala", ".go", ".rs",
                   ".swift", ".m", ".mm", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".rb",
                   ".php", ".lua", ".dart", ".sln"}
LISTDIR_MAX = 500
FOLDER_UNAMBIGUOUS_BAD = re.compile(r"[A-Z\s.]")
# Cost caps (review fix, 2026-11-23: a 999-folder mkdir in a 20,000-entry folder listed that folder
# 999 times, 13.7 s against the 10 s hook timeout; a timeout lets a W3-violating write in the same
# command through). Per call W7 judges at most MAX_TARGETS targets and stops once BUDGET_SECONDS of
# wall clock have passed since it started, whichever comes first; a stop is logged as one row of
# kind BUDGET_KIND. The exempt file is read up to EXEMPT_MAX_BYTES / EXEMPT_MAX_LINES; the rest is
# ignored (a 10 MB file took 13.4 s, a 60 MB file over 60 s, and any session can write it).
MAX_TARGETS = 200
BUDGET_SECONDS = 0.5
BUDGET_KIND = "budget-stop"
EXEMPT_MAX_BYTES = 64 * 1024
EXEMPT_MAX_LINES = 1000


def _listdir(p):
    """At most LISTDIR_MAX names from folder p, stopping the scan there (never lists it whole)."""
    out = []
    try:
        with os.scandir(p) as it:
            for entry in it:
                out.append(entry.name)
                if len(out) >= LISTDIR_MAX:
                    break
    except OSError:
        pass
    return out


class Fs(object):
    """The filesystem questions W7 asks, injectable (self-test, replay)."""

    def __init__(self, exists=None, isdir=None, listdir=None):
        self.exists = exists or os.path.exists
        self.isdir = isdir or os.path.isdir
        self.listdir = listdir or _listdir


LIVE_FS = Fs()


class _CallFs(object):
    """One call's view of an Fs: each folder's code convention is computed at most once
    (`conventions`, filled by parent_code_convention), so each folder is listed at most once (the
    only listing W7 makes is for that convention)."""

    def __init__(self, fs):
        self.exists = fs.exists
        self.isdir = fs.isdir
        self.listdir = fs.listdir
        self.conventions = {}


def _call_fs(fs):
    fs = fs or LIVE_FS
    return fs if isinstance(fs, _CallFs) else _CallFs(fs)


class Budget(object):
    """How much more W7 may judge in this call: `take()` is True while fewer than `max_targets`
    targets have been taken and the clock has not passed `seconds` since the budget was made.
    Once it says no it keeps saying no (`stopped`). `clock` is injectable (self-test)."""

    def __init__(self, max_targets=None, seconds=None, clock=None):
        self.clock = clock or time.monotonic
        self.deadline = self.clock() + (BUDGET_SECONDS if seconds is None else seconds)
        self.left = MAX_TARGETS if max_targets is None else max_targets
        self.taken = 0
        self.stopped = False

    def take(self):
        if self.stopped or self.left <= 0 or self.clock() > self.deadline:
            self.stopped = True
            return False
        self.left -= 1
        self.taken += 1
        return True

    def out(self):
        """True once the budget is spent, without taking anything."""
        if not self.stopped and (self.left <= 0 or self.clock() > self.deadline):
            self.stopped = True
        return self.stopped


# ------------------------------------------------------------------ names and places

def project_root():
    """CLAUDE_PROJECT_DIR, normalized; None without it (no cwd fallback: a session started below
    the repo root would otherwise judge against the wrong folder)."""
    env = os.environ.get("CLAUDE_PROJECT_DIR") or ""
    if not env:
        return None
    try:
        return paths.expand_short_names(os.path.normpath(paths.msys_to_windows(env)))
    except Exception:
        return os.path.normpath(env)


def is_network(p):
    return bool(p) and (p.startswith("\\\\") or p.startswith("//"))


def clean_name(name):
    """The name Windows actually creates: an NTFS stream suffix (`x.cpp:Zone.Identifier`) dropped,
    trailing dots and spaces stripped."""
    if ":" in name:
        name = name.split(":", 1)[0]
    return name.rstrip(". ")


def rel_parts(root, target):
    if not root or not target or is_network(target):
        return None
    r = os.path.normcase(os.path.normpath(root)).rstrip("\\/")
    t = os.path.normcase(os.path.normpath(target))
    if t != r and not t.startswith(r + os.sep):
        return None
    rest = os.path.normpath(target)[len(r):].strip("\\/")
    return [p for p in re.split(r"[\\/]+", rest) if p]


def _read_head(path, n):
    """At most the first n bytes of the file at path (never the whole file); b"" on any error."""
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except (OSError, ValueError):
        return b""


def load_exempt_prefixes(root, _cache={}):
    """Project-relative prefixes from <root>/.claude/naming-exempt.txt (one per line, # comments),
    lowercased with '/' separators, as a frozenset. Missing file: none. Read once per root per
    process, and only its first EXEMPT_MAX_BYTES / EXEMPT_MAX_LINES: the rest is ignored, and a
    line cut by the byte cap is dropped (a cut prefix would exempt more than the line says)."""
    if not root:
        return frozenset()
    if root in _cache:
        return _cache[root]
    data = _read_head(os.path.join(root, EXEMPT_FILE), EXEMPT_MAX_BYTES + 1)
    if len(data) > EXEMPT_MAX_BYTES:
        data = data[:EXEMPT_MAX_BYTES]
        data = data[:data.rfind(b"\n") + 1]  # no newline at all: nothing kept
    out = set()
    # utf-8-sig: a BOM (Notepad, PowerShell 5.1 Out-File) must not swallow the first line
    for line in data.decode("utf-8-sig", "replace").splitlines()[:EXEMPT_MAX_LINES]:
        line = line.split("#", 1)[0].strip().replace("\\", "/").strip("/")
        if line:
            out.add(line.lower())
    _cache[root] = frozenset(out)
    return _cache[root]


def _exempt(low_parts, prefixes):
    """True when the path (lowercased parts) or one of its ancestors is a listed prefix: one set
    lookup per path depth, never one comparison per listed line."""
    acc = ""
    for p in low_parts:
        acc = acc + "/" + p if acc else p
        if acc in prefixes:
            return True
    return False


def _local_inside(p, root):
    return bool(p) and not is_network(p) and rel_parts(root, p) is not None


def parent_code_convention(parent, root, fs):
    """The code convention a new folder in `parent` should follow: the one convention of the code
    files already there, or None (no code files, code of a language with no convention here, or
    code of more than one convention). A folder's own language is unknown when it is created, so
    its parent's code decides (cross-project replay, 2026-11-23: judging every folder flagged C#
    PascalCase folders and backup folders)."""
    if not _local_inside(parent, root):
        return None
    cache = getattr(fs, "conventions", None)
    key = os.path.normcase(parent)
    if cache is not None and key in cache:
        return cache[key]
    conv = _listing_convention(fs.listdir(parent))
    if cache is not None:
        cache[key] = conv
    return conv


def _listing_convention(names):
    convs = set()
    for entry in names:
        ext = os.path.splitext(entry)[1].lower()
        if ext in OTHER_CODE_EXTS:
            return None
        conv = EXT_CONVENTION.get(ext)
        if conv and KIND_FOR.get(conv) == "code":
            convs.add(conv)
    return convs.pop() if len(convs) == 1 else None


def classify(target, root, what, fs, exempt=None):
    """'code' | 'doc' | 'doc-dir' | 'dir' | None for a resolved target the call would create."""
    parts = rel_parts(root, target)
    if not parts:
        return None
    low = [p.lower() for p in parts]
    if low[0] == ".claude" and len(low) > 1 and low[1] == "scratch":
        return None
    if any(p in THIRD_PARTY_SEGMENTS for p in low):
        return None
    prefixes = load_exempt_prefixes(root) if exempt is None else exempt
    if prefixes and _exempt(low, prefixes):
        return None
    name = clean_name(parts[-1])
    lname = name.lower()
    if not name or name.startswith(".") or lname in TOOL_NAMES \
            or any(m in lname for m in BACKUP_MARKERS):
        return None
    if what == "dir":
        parent = os.path.dirname(target)
        sidecar = os.path.join(parent, name + ".md")
        if _local_inside(sidecar, root) and fs.exists(sidecar):
            return None if PATTERNS[DOC_CONVENTION].match(name) else "doc-dir"
        conv = parent_code_convention(parent, root, fs)
        if conv is None or PATTERNS[conv].match(name):
            return None
        # hyphen-vs-underscore is not judged for folders: a new folder in a code tree often holds
        # data or staging output, not code (cross-project replay, 2026-11-23: all 6 such hits were
        # working/test folders). Upper case, spaces and dots are wrong in every listed convention.
        if not FOLDER_UNAMBIGUOUS_BAD.search(name):
            return None
        return "dir"
    stem, ext = os.path.splitext(name)
    conv = EXT_CONVENTION.get(ext.lower())
    if conv is None or PATTERNS[conv].match(stem):
        return None
    return KIND_FOR[conv]


# ------------------------------------------------------------------ target extraction

def _basename(raw):
    return re.split(r"[\\/]+", raw.rstrip("\\/"))[-1]


def _parent(raw):
    parts = re.split(r"[\\/]+", raw.rstrip("\\/"))
    return "/".join(parts[:-1]) if len(parts) > 1 else ""


def _join(folder, name):
    return (folder.rstrip("\\/") + "/" + name) if folder else name


def _ps_named(toks, flags):
    """The value of the first of `flags` (exact or abbreviated, `-Flag v` or `-Flag:v`), else None."""
    for i, t in enumerate(toks):
        m = re.match(r"^(-[A-Za-z]+)(?::(.*))?$", t)
        if not m:
            continue
        f = m.group(1).lower()
        if f in flags or (len(f) >= 3 and any(x.startswith(f) for x in flags)):
            if m.group(2):
                return m.group(2).strip("'\"")
            if i + 1 < len(toks) and not toks[i + 1].startswith("-"):
                return toks[i + 1]
    return None


def _ps_positionals(toks):
    out, i = [], 1
    while i < len(toks):
        t = toks[i]
        if t.startswith("-") and len(t) > 1:
            base = t.split(":", 1)[0].lower()
            if ":" not in t and base not in w3.PS_SWITCH_FLAGS and i + 1 < len(toks) \
                    and not toks[i + 1].startswith("-"):
                i += 1
            i += 1
            continue
        out.append(t)
        i += 1
    return out


def _posix_positionals(toks):
    return [t for t in toks[1:] if not t.startswith("-") and not shell.CMD_FLAG.match(t)]


class _Ctx(object):
    def __init__(self, event, root, fs, seg_cwd, budget=None):
        self.event, self.root, self.fs, self.seg_cwd = event, root, fs, seg_cwd
        self.budget = budget or Budget()

    def resolve(self, raw):
        """Absolute target or None: UNC never resolved, dynamic or unresolvable-relative skipped."""
        if not raw or URL_LIKE.match(raw.strip().strip("'\"")):
            return None
        kind, judged = w3.classify_target(raw)
        if kind != "judge" or judged != raw.strip().strip("'\""):
            return None  # skip, unresolved, or a glob (judged as its folder): no single name
        if is_network(judged):
            return None
        if self.seg_cwd is shell.UNRESOLVED_CWD and w3.is_relative(judged):
            return None
        try:
            return paths.resolve(judged, self.event,
                                 self.seg_cwd if isinstance(self.seg_cwd, str) else None)
        except Exception:
            return None

    def isdir(self, raw):
        r = self.resolve(raw)
        return bool(r) and _local_inside(r, self.root) and bool(self.fs.isdir(r))


def _same_place(ctx, a, b):
    """True when raw paths a and b resolve to the same path, case-insensitively (a case-only
    rename: `mv a.cpp A.cpp`, where the target 'exists' only as the source itself)."""
    ra, rb = ctx.resolve(a), ctx.resolve(b)
    return bool(ra) and bool(rb) and os.path.normcase(ra) == os.path.normcase(rb)


def _copy_like(ctx, srcs, dest, move):
    """[(raw, what, src)] a copy/move/link of srcs to dest creates; same-basename moves dropped;
    `src` is the source of a move (None for a copy), for the case-only rename check. At most the
    budget's remaining targets are looked at."""
    if not srcs or not dest:
        return []
    srcs = srcs[:ctx.budget.left + 1]
    # a case-only rename of a folder (`mv src/foo src/Foo`) is a rename, not a move INTO it
    same = move and len(srcs) == 1 and _same_place(ctx, srcs[0], dest)
    into = not same and (dest.endswith(("/", "\\")) or dest in (".", "..") or len(srcs) > 1
                         or ctx.isdir(dest))
    out = []
    for s in srcs:
        if ctx.budget.out():
            break
        base = _basename(s)
        if not base:
            continue
        raw = _join(dest, base) if into else dest
        if move and _basename(raw) == base:
            continue
        out.append((raw, "dir" if ctx.isdir(s) else "file", s if move else None))
    return out


def _segment_items(ctx, seg, verb, raw, powershell):
    """[(raw, what, src)] for one W3 (seg, verb, raw) row; src as in _copy_like."""
    v = (verb or "").lower()
    if v in shell.DELETE_VERBS or v in REPO_VERBS:
        return []
    if powershell and PS_SUBEXPR.search(seg):
        return []
    toks = shell.shell_tokens(seg, powershell) or []  # None on unbalanced quotes
    if powershell and v in PS_COPY_VERBS | PS_MOVE_VERBS:
        src = _ps_named(toks, PS_PATH_FLAGS)
        if src is None:
            pos = _ps_positionals(toks)
            src = pos[0] if pos else None
        return _copy_like(ctx, [src] if src else [], raw, v in PS_MOVE_VERBS)
    if v in RENAME_VERBS and (powershell or v == "ren"):
        if powershell:
            path = _ps_named(toks, PS_PATH_FLAGS)
            if path is None:
                pos = _ps_positionals(toks)
                path = pos[0] if pos else None
        else:
            pos = _posix_positionals(toks)
            path = pos[0] if len(pos) >= 2 else None
        if not path:
            return []
        new = raw if re.search(r"[\\/]", raw) else _join(_parent(path), raw)
        if _basename(new) == _basename(path):
            return []
        return [(new, "dir" if ctx.isdir(path) else "file", path)]
    if v in COPY_VERBS | MOVE_VERBS:
        pos = _posix_positionals(toks)
        return _copy_like(ctx, pos[:-1], raw, v in MOVE_VERBS)
    if v in MKDIR_VERBS:
        return [(raw, "dir", None)]
    if v in ("new-item", "ni"):
        itemtype = _ps_named(toks, ("-itemtype", "-type"))
        name = _ps_named(toks, ("-name",))
        target = _join(raw, name) if name else raw
        is_dir = bool(itemtype) and itemtype.lower().startswith("d")
        return [(target, "dir" if is_dir else "file", None)]
    return [(raw, "file", None)]


def _extra_items(seg, powershell):
    """[(raw, what)] for forms W3 does not extract: git mv and [IO.Directory]::CreateDirectory."""
    out = [(m.group(2), "dir") for m in DOTNET_MKDIR.finditer(seg)]
    toks = shell.shell_tokens(seg, powershell) or []  # None on unbalanced quotes (replay crash fix)
    if len(toks) >= 4 and toks[0].lower() == "git" and toks[1].lower() == "mv":
        pos = [t for t in toks[2:] if not t.startswith("-")]
        if len(pos) >= 2:
            out.append(("__git_mv__", (pos[:-1], pos[-1])))
    return out


def _case_rename_target(ctx, item_raw, resolved, src):
    """For a move or rename whose target is its own source under another case (`mv a.cpp A.cpp`):
    the target with the name AS TYPED (resolve() re-cases an existing path to its on-disk name, so
    the resolved name would be the old one). None for anything else."""
    if not src or not _same_place(ctx, src, item_raw):
        return None
    typed = clean_name(_basename(item_raw))
    if not typed or typed == os.path.basename(ctx.resolve(src) or ""):
        return None
    return os.path.join(os.path.dirname(resolved), typed)


def shell_targets(event, root, fs, budget=None):
    """[(seg, verb, raw, resolved, what, case_rename)] for every creation W7 may judge in a shell
    call, at most the budget's targets. case_rename is True for a case-only rename, whose
    `resolved` then carries the typed name and whose existence check is skipped."""
    tool = event.get("tool_name") or ""
    cmd = (event.get("tool_input") or {}).get("command") or ""
    if tool not in ("Bash", "PowerShell") or not cmd:
        return []
    budget = budget or Budget()
    powershell = tool == "PowerShell"
    out = []

    def add(ctx, seg, verb, item_raw, what, src):
        if not budget.take():
            return False
        r = ctx.resolve(item_raw)
        if r:
            cr = _case_rename_target(ctx, item_raw, r, src)
            out.append((seg, verb, item_raw, cr or r, what, cr is not None))
        return True

    rows = w3.find_write_targets_cwd(cmd, powershell, event.get("cwd"), brace_words=True)
    for seg, verb, raw, seg_cwd in rows:
        if budget.out():
            return out
        seg, raw = w3.unmask_brace_words(seg), w3.unmask_brace_words(raw)
        ctx = _Ctx(event, root, fs, seg_cwd, budget)
        for item_raw, what, src in _segment_items(ctx, seg, verb, w3.reglue(seg, raw), powershell):
            if not add(ctx, seg, verb, item_raw, what, src):
                return out
    # W3 drops segments it extracts nothing from, so walk every segment for the extra forms
    triples = shell.split_top_level_seps(w3.mask_brace_words(cmd))
    segs = [w3.unmask_brace_words(t[0]) for t in triples]
    cwds = shell.cwd_per_segment([t[0] for t in triples], event.get("cwd"), paths.home_dir(),
                                 paths.msys_to_windows, powershell, [(t[1], t[2]) for t in triples])
    for seg, seg_cwd in zip(segs, cwds):
        if budget.out():
            return out
        ctx = _Ctx(event, root, fs, seg_cwd, budget)
        for raw, what in _extra_items(seg, powershell):
            if raw == "__git_mv__":
                srcs, dest = what
                for item_raw, w, src in _copy_like(ctx, srcs, dest, move=True):
                    if not add(ctx, seg.strip(), "git mv", item_raw, w, src):
                        return out
                continue
            if not add(ctx, seg.strip(), "CreateDirectory", raw, what, None):
                return out
    return out


# ------------------------------------------------------------------ rule interface

def evaluate_tool(event, fs=None, root=None):
    """[(raw, resolved, kind)] for a Write creating a badly named file. NEVER raises (cross-project
    replay, 2026-11-23: 1,455 crashes in 116,127 events before this guard, each of which dropped
    the enforced W2/W3/W5 verdict for its call). write_guard now also isolates every module, so
    this is the first of two layers."""
    try:
        return _evaluate_tool(event, fs, root)
    except Exception:
        return []


def evaluate_shell(event, fs=None, root=None):
    """[(RULE_ID, (segment, verb, raw, resolved, kind))], one per target (case-insensitive).
    NEVER raises (see evaluate_tool)."""
    try:
        return _evaluate_shell(event, fs, root)
    except Exception:
        return []


def _evaluate_tool(event, fs=None, root=None):
    fs = _call_fs(fs)
    if (event.get("tool_name") or "") != "Write":
        return []
    raw = (event.get("tool_input") or {}).get("file_path") or ""
    root = root or project_root()
    if not raw or not root or is_network(raw):
        return []
    try:
        target = paths.resolve(raw, event)
    except Exception:
        return []
    kind = classify(target, root, "file", fs)
    if kind is None or fs.exists(target):
        return []
    return [(raw, target, kind)]


def _evaluate_shell(event, fs=None, root=None, budget=None):
    fs = _call_fs(fs)
    root = root or project_root()
    if not root:
        return []
    budget = budget or Budget()
    hits, seen = [], set()
    for seg, verb, raw, target, what, case_rename in shell_targets(event, root, fs, budget):
        if budget.clock() > budget.deadline:
            budget.stopped = True
            break
        key = os.path.normcase(target)
        if key in seen:
            continue
        kind = classify(target, root, what, fs)
        if kind is None or (not case_rename and fs.exists(target)):
            continue
        seen.add(key)
        hits.append((RULE_ID, (seg, verb, raw, target, kind)))
    if budget.stopped:
        # one marker in the ordinary hit shape: W7 stopped judging this call early
        hits.append((RULE_ID, ("", "", "", "", BUDGET_KIND)))
    return hits


def log_rows(event, tool_hits, shell_hits, ms):
    """W7's rows, or [] if building them fails (never raises: review fix 2026-11-23)."""
    try:
        return _log_rows(event, tool_hits, shell_hits, ms)
    except Exception:
        return []


def _log_rows(event, tool_hits, shell_hits, ms):
    """Rows without `msg` (the message lives in RULES.md); one excerpt per segment; at most
    MAX_ROWS rows per call plus one `truncated` row; a budget stop is one more row of kind
    BUDGET_KIND, kept whatever the truncation."""
    base = {"rule": RULE_ID, "tool": event.get("tool_name"), "root": project_root(),
            "cwd": event.get("cwd"), "session": event.get("session_id"), "ms": ms}
    rows, seg_excerpts, tail = [], {}, []
    for raw, target, kind in tool_hits:
        rows.append(dict(base, kind=kind, target=raw, resolved=target, verb="Write",
                         sample=[excerpt("Write %s" % raw)]))
    for _rid, (seg, verb, raw, target, kind) in shell_hits:
        if kind == BUDGET_KIND:
            tail = [dict(base, kind=BUDGET_KIND, max_targets=MAX_TARGETS,
                         budget_seconds=BUDGET_SECONDS)]
            continue
        if seg not in seg_excerpts:
            seg_excerpts[seg] = excerpt(seg)
        rows.append(dict(base, kind=kind, target=raw, resolved=target, verb=verb,
                         sample=[seg_excerpts[seg]]))
    if len(rows) > MAX_ROWS:
        dropped = len(rows) - MAX_ROWS
        rows = rows[:MAX_ROWS] + [dict(base, kind="truncated", truncated=dropped)]
    return rows + tail


def decide(event, tool_hits, shell_hits, enforce):
    """Always None: W7 is ALWAYS_LOG_ONLY (and write_guard drops any verdict such a module
    returns)."""
    return None


MSG = ("W7: a new file or folder name breaks the naming rule: code follows its language's "
       "convention (C++ and Python snake_case), Markdown docs and their sidecar folders are "
       "lowercase-hyphenated; tool-required names and third-party trees are exempt (user rule, "
       "2026-11-23). Log-only.")


# ------------------------------------------------------------------ self-test

ROOT = "C:\\proj\\w7demo"
FWD = ROOT.replace("\\", "/")


def _j(*parts):
    return "\\".join((ROOT,) + parts)


EXISTING = {_j("Sources", "Client", "Game.cpp"), _j("Sources", "Client", "Old.h"),
            _j(".claude", "docs", "environment.md"), _j(".claude", "docs", "My_Topic.md"),
            _j(".claude", "docs", "my_topic.md"), _j("Sources", "x.cpp")}
DIRS = {ROOT, _j("Sources"), _j("Sources", "Client"), _j("Sources", "Server"), _j(".claude"),
        _j(".claude", "docs"), _j(".claude", "scratch"), _j("Binaries")}


LISTINGS = {  # what each existing folder holds, for the parent-convention check
    _j("Sources"): ["x.cpp", "main.cpp", "Client", "Server"],
    _j("Sources", "Server"): ["server.cpp", "server.h"],
    _j("Sources", "Client"): ["Game.cpp", "Old.h"],
    ROOT: ["README.md", "Sources", "Binaries", ".claude"],
    _j("csapp", "Core"): ["Thing.cs", "Core.csproj"],
    _j("mixed"): ["a.cpp", "b.js"],
    _j(".claude", "docs"): ["environment.md", "My_Topic.md", "my_topic.md"],
}


def _fake_fs():
    ex = {os.path.normcase(p) for p in EXISTING}
    dirs = {os.path.normcase(p) for p in DIRS} | {os.path.normcase(p) for p in LISTINGS}
    listing = {os.path.normcase(k): v for k, v in LISTINGS.items()}
    return Fs(exists=lambda p: os.path.normcase(p) in ex or os.path.normcase(p) in dirs,
              isdir=lambda p: os.path.normcase(p) in dirs,
              listdir=lambda p: listing.get(os.path.normcase(p), []))


# (tool, tool_input, expected kind or None)
CASES = [
    # --- code (C++/Python snake_case)
    ("Write", {"file_path": _j("Sources", "Client", "NewDialog.cpp")}, "code"),
    ("Write", {"file_path": _j("Sources", "Util", "HMAC.h")}, "code"),
    ("Write", {"file_path": _j("tools", "my-tool.py")}, "code"),
    ("Write", {"file_path": _j("Sources", "Bad.cpp.")}, "code"),               # trailing dot
    ("Write", {"file_path": _j("Sources", "Bad2.cpp:stream")}, "code"),        # NTFS stream
    ("Write", {"file_path": _j("Sources", "good.cpp:Zone.Identifier")}, None),
    ("Write", {"file_path": _j("Sources", "Client", "new_dialog.cpp")}, None),
    ("Write", {"file_path": _j("Sources", "123.cpp")}, None),                 # digits only
    ("Write", {"file_path": _j("Sources", "_impl.cpp")}, None),               # leading underscore
    ("Write", {"file_path": _j("Sources", "x.CPP")}, None),                   # extension case ignored
    ("Write", {"file_path": _j("pkg", "__init__.py")}, None),
    # --- docs (lowercase-hyphenated)
    ("Write", {"file_path": _j(".claude", "docs", "My_Doc.md")}, "doc"),
    ("Write", {"file_path": _j(".claude", "docs", "my_doc.md")}, "doc"),
    ("Write", {"file_path": _j(".claude", "docs", "my-doc.md")}, None),
    ("Write", {"file_path": _j(".claude", "design", "plans", "sdl-versioning-and-sql-source.md")}, None),
    ("Write", {"file_path": _j("README.md")}, None),
    ("Write", {"file_path": _j("CLAUDE.md")}, None),
    ("Write", {"file_path": _j(".claude", "skills", "foo", "SKILL.md")}, None),
    ("Write", {"file_path": _j("Sources", "CMakeLists.txt")}, None),
    ("Write", {"file_path": _j("Makefile")}, None),
    # --- other file types: no convention
    ("Write", {"file_path": _j("Binaries", "Config.json")}, None),
    ("Write", {"file_path": _j("Binaries", "Logo.PNG")}, None),
    ("Write", {"file_path": _j(".claude", "metrics", "adoption-baseline-2026-11-23.txt")}, None),
    # --- exemptions and non-creations
    ("Write", {"file_path": _j("Sources", "Client", "Game.cpp")}, None),      # exists
    ("Edit", {"file_path": _j("Sources", "Client", "NewDialog.cpp")}, None),
    ("Write", {"file_path": _j("Sources", "Dependencies", "SFML", "Graphics.hpp")}, None),
    ("Write", {"file_path": _j("Sources", "vendor", "Lib.h")}, None),
    ("Write", {"file_path": _j("third-party", "X.h")}, None),
    ("Write", {"file_path": _j("build", "_deps", "Foo.cpp")}, None),
    ("Write", {"file_path": _j("web", "node_modules", "x", "Y.md")}, None),
    ("Write", {"file_path": _j("Sources", "Libs", "Imgui", "ImGui.h")}, None),  # naming-exempt.txt
    ("Write", {"file_path": _j(".claude", "scratch", "t", "Bad.py")}, None),
    ("Write", {"file_path": _j("Sources", "Game.cpp.bak_0219c632")}, None),
    ("Write", {"file_path": _j("pm", "RESUME.prev.md")}, None),
    ("Write", {"file_path": _j("Sources", "Old_Name.orig.cpp")}, None),
    ("Write", {"file_path": _j("Sources", "Prevalent.cpp")}, "code"),        # "prev" inside a name
    ("Write", {"file_path": _j(".clang-format")}, None),
    ("Write", {"file_path": "C:\\other\\BadName.cpp"}, None),                 # outside the root
    ("Write", {"file_path": ROOT + "2\\BadName.cpp"}, None),                   # sibling <root>2
    ("Write", {"file_path": "\\\\server\\share\\BadName.cpp"}, None),          # UNC
    # --- shell: creations
    ("Bash", {"command": "echo x > Sources/Bad.h"}, "code"),
    ("Bash", {"command": "touch " + FWD + "/Sources/Server/Foo.h"}, "code"),
    ("Bash", {"command": "cd Sources/Client && cat > BadName.h <<'EOF'\n#pragma once\nEOF"}, "code"),
    ("Bash", {"command": "cp a.cpp Sources/Client/Copy.cpp"}, "code"),
    ("Bash", {"command": "cp Templates/Base.cpp Sources/Client/"}, "code"),   # into a folder
    ("Bash", {"command": "mv a.cpp Sources/Client/BadName2.cpp"}, "code"),    # a move to a new name
    ("Bash", {"command": "mv .claude/scratch/x/BadName.cpp Sources/"}, None),  # same basename
    ("Bash", {"command": "git mv Sources/x.cpp Sources/X2.cpp"}, "code"),
    ("Bash", {"command": "git mv Sources/Old.cpp Sources/Client/"}, None),     # same basename
    # folders: judged by the convention of the code already in the parent
    ("Bash", {"command": "mkdir Sources/NewModule"}, "dir"),                   # parent holds C++
    ("Bash", {"command": "mkdir Sources/v1.2"}, "dir"),                        # dotted folder
    ("Bash", {"command": "mkdir Sources/net-code"}, None),                     # hyphen: not judged
    ("Bash", {"command": "mkdir \"Sources/New Folder\""}, "dir"),              # space
    ("Bash", {"command": "mkdir Sources/net_code"}, None),
    ("Bash", {"command": "mkdir -p Sources/net/PacketCodec"}, None),           # new parent: unknown
    ("Bash", {"command": "mkdir v1.2"}, None),                                 # root holds no code
    ("Bash", {"command": "mkdir csapp/Core/Backups"}, None),                   # C#: PascalCase is right
    ("Bash", {"command": "mkdir mixed/NewThing"}, None),                       # mixed languages
    # repository checkouts, URLs and computed PowerShell paths are never judged
    ("Bash", {"command": "git worktree add ../Review-Lane_3 85a84b3"}, None),
    ("Bash", {"command": "git worktree add Sources/Review-Lane 85a84b3"}, None),
    ("Bash", {"command": "git clone --branch prod https://github.com/x/Y-Repo.git Sources/Dest"}, None),
    ("Bash", {"command": "cp a.cpp https://example.invalid/Y.cpp"}, None),
    ("PowerShell", {"command": "New-Item -ItemType File -Path (Join-Path $fx 'Bad9.cpp')"}, None),
    ("PowerShell", {"command": "New-Item -ItemType Directory -Force (Join-Path $fx 'MAPDATA')"}, None),
    ("Bash", {"command": "echo \"unterminated > Sources/Bad10.cpp"}, None),     # tokenizer gives None
    ("Bash", {"command": "mkdir .claude/docs/My_Topic"}, "doc-dir"),           # sidecar of My_Topic.md
    ("Bash", {"command": "mkdir .claude/docs/my_topic"}, "doc-dir"),           # sidecar of my_topic.md
    ("Bash", {"command": "mkdir .claude/docs/environment"}, None),             # sidecar, hyphen ok
    ("Bash", {"command": "touch BadA.cpp BADA.cpp BadA.cpp"}, "code"),         # de-dup, case-insensitive
    ("PowerShell", {"command": "New-Item -ItemType Directory -Path Sources\\Server\\Handlers"}, "dir"),
    ("PowerShell", {"command": "New-Item -Path Sources\\Client -Name UiPanel.cpp -ItemType File"}, "code"),
    ("PowerShell", {"command": "Set-Content -Path Sources\\Client\\Hud.cpp -Value 1"}, "code"),
    ("PowerShell", {"command": "Rename-Item -Path Sources\\x.cpp -NewName Bad3.cpp"}, "code"),
    ("PowerShell", {"command": "ren Sources\\x.cpp Bad4.cpp"}, "code"),
    ("PowerShell", {"command": "Rename-Item Sources\\x.cpp x.cpp"}, None),     # same name
    ("PowerShell", {"command": "Move-Item -Path a\\Bad5.cpp -Destination Sources\\Client\\"}, None),
    ("PowerShell", {"command": "Copy-Item a\\Bad6.cpp -Destination Sources\\Client\\"}, "code"),
    ("PowerShell", {"command": "[IO.Directory]::CreateDirectory('Sources\\NewDir')"}, "dir"),
    # case-only renames: the target "exists" only as the source itself (review fix 2026-11-23)
    ("Bash", {"command": "mv Sources/x.cpp Sources/X.cpp"}, "code"),
    ("Bash", {"command": "git mv Sources/x.cpp Sources/X.cpp"}, "code"),
    ("PowerShell", {"command": "Rename-Item -Path Sources\\x.cpp -NewName X.cpp"}, "code"),
    ("PowerShell", {"command": "Move-Item -Path Sources\\x.cpp -Destination Sources\\X.cpp"}, "code"),
    ("Bash", {"command": "mv Sources/Server Sources/SERVER"}, "dir"),          # a folder, case only
    ("Bash", {"command": "mv Sources/Client Sources/client"}, None),           # to a good name
    ("Bash", {"command": "cp Sources/x.cpp Sources/X.cpp"}, None),             # a copy overwrites x.cpp
    # --- shell: not creations / not judgeable
    ("Bash", {"command": "mkdir -p Sources/Client"}, None),                    # exists
    ("Bash", {"command": "cat Sources/Client/Game.cpp"}, None),
    ("Bash", {"command": "rm Sources/BadName.cpp"}, None),                     # a delete
    ("Bash", {"command": "echo x > Sources/Client/Old.h"}, None),             # exists
    ("Bash", {"command": "cd Sources/Client && touch new_file.cpp"}, None),
    ("Bash", {"command": "mkdir $D/NewDir"}, None),                           # $VAR
    ("Bash", {"command": "echo x > $(pwd)/Bad.cpp"}, None),                   # $(
    ("Bash", {"command": "echo x > `pwd`/Bad.cpp"}, None),                    # backtick
    ("Bash", {"command": "copy a.cpp %CD%\\Bad.cpp"}, None),                  # %VAR%
    ("Bash", {"command": "touch Sources/*.cpp Sources/Ba?.cpp"}, None),       # globs
    ("Bash", {"command": "cp a.cpp //server/share/BadName.cpp"}, None),       # UNC
    ("Bash", {"command": "python x.py 2>&1 > /dev/null"}, None),
    ("Bash", {"command": "cat <<'EOF' > Sources/notes.txt\nmkdir BadDir\nEOF"}, None),  # heredoc body
]
N_CASES = len(CASES) + 13 + 13  # +13: the cost-cap, exempt-bound, BOM and real-file checks
EXEMPT = ("sources/libs/imgui",)
SELFTEST_DIR = ".selftest_tmp"


def selftest_tmp():
    """A fresh folder under <hook root>/.selftest_tmp/ (never the system temp folder: global rule),
    for self-test files. The caller removes it with remove_selftest_tmp()."""
    import tempfile
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        SELFTEST_DIR)
    os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(prefix="w7_", dir=base)


def remove_selftest_tmp(path):
    """Remove a folder selftest_tmp() made (and .selftest_tmp itself once empty); refuses any
    other path."""
    import shutil
    base = os.path.dirname(path)
    if os.path.basename(base) != SELFTEST_DIR or not os.path.basename(path).startswith("w7_"):
        return
    shutil.rmtree(path, ignore_errors=True)
    try:
        os.rmdir(base)
    except OSError:
        pass


def _cost_self_test(failures, fs):
    """The cost caps (review fix 2026-11-23): target and clock budget, one listing per folder,
    a scan that stops at LISTDIR_MAX, the bounded exempt file, and a real case-only rename."""
    ev = lambda cmd: {"tool_name": "Bash", "cwd": ROOT, "tool_input": {"command": cmd}}
    frozen = lambda: 0.0
    # 1-2: at most MAX_TARGETS judged, then one budget-stop marker; its row survives truncation
    many = "touch " + " ".join("Sources/N%d.cpp" % i for i in range(MAX_TARGETS + 50))
    sh = _evaluate_shell(ev(many), fs=fs, root=ROOT, budget=Budget(clock=frozen))
    kinds = [d[4] for _r, d in sh]
    if kinds.count("code") != MAX_TARGETS or kinds[-1] != BUDGET_KIND:
        failures.append("W7 budget: %d code hits, last %r (want %d, then %s)"
                        % (kinds.count("code"), kinds[-1:], MAX_TARGETS, BUDGET_KIND))
    rows = log_rows({"tool_name": "Bash"}, [], sh, 1)
    if len(rows) != MAX_ROWS + 2 or rows[-1].get("kind") != BUDGET_KIND \
            or rows[-2].get("kind") != "truncated":
        failures.append("W7 budget: rows end %r" % [r.get("kind") for r in rows[-2:]])
    # 3: the wall clock stops it too (a clock that jumps 0.2 s per reading)
    ticks = iter(range(10 ** 6))
    clock = lambda: next(ticks) * 0.2
    sh = _evaluate_shell(ev(many), fs=fs, root=ROOT, budget=Budget(clock=clock))
    if not sh or sh[-1][1][4] != BUDGET_KIND or len(sh) > 5:
        failures.append("W7 budget: the clock did not stop it (%d hits)" % len(sh))
    # 4: a small call never stops early
    sh = _evaluate_shell(ev("touch Sources/Q1.cpp"), fs=fs, root=ROOT)
    if [d[4] for _r, d in sh] != ["code"]:
        failures.append("W7 budget: a one-target call gave %r" % [d[4] for _r, d in sh])
    # 5: each parent folder is listed once per call, however many new folders it gets
    calls = []
    counting = Fs(exists=fs.exists, isdir=fs.isdir,
                  listdir=lambda p: calls.append(p) or fs.listdir(p))
    sh = evaluate_shell(ev("mkdir " + " ".join("Sources/NewMod%d" % i for i in range(40))),
                        fs=counting, root=ROOT)
    if len(sh) != 40 or len(calls) != 1:
        failures.append("W7 listing cache: %d hits, %d listings (want 40, 1)" % (len(sh), len(calls)))
    tmp = selftest_tmp()
    try:
        # 6: the real scan stops at LISTDIR_MAX
        for i in range(LISTDIR_MAX + 20):
            open(os.path.join(tmp, "f%04d.cpp" % i), "w").close()
        if len(_listdir(tmp)) != LISTDIR_MAX:
            failures.append("W7 _listdir did not stop at %d" % LISTDIR_MAX)
        # 7-9: the exempt file is read up to its caps; a line cut by the byte cap is dropped
        os.makedirs(os.path.join(tmp, ".claude"))
        exf = os.path.join(tmp, EXEMPT_FILE)
        pad = "#" + "p" * 98 + "\n"
        with open(exf, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("early/one\n" + pad * (EXEMPT_MAX_BYTES // len(pad) + 1) + "late/two\n" * 50)
        cache = load_exempt_prefixes.__defaults__[0]
        cache.pop(tmp, None)
        got = load_exempt_prefixes(tmp)
        if "early/one" not in got or "late/two" in got:
            failures.append("W7 exempt byte cap: got %r" % sorted(got)[:4])
        with open(exf, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("a/b\n" * (EXEMPT_MAX_LINES - 1) + "in/cap\n" + "past/cap\n")
        cache.pop(tmp, None)
        got = load_exempt_prefixes(tmp)
        if "in/cap" not in got or "past/cap" in got:
            failures.append("W7 exempt line cap: got %r" % sorted(got))
        with open(exf, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("x" * (EXEMPT_MAX_BYTES + 10))
        cache.pop(tmp, None)
        if load_exempt_prefixes(tmp):
            failures.append("W7 exempt byte cap: a line cut by the cap was kept")
        if len(_read_head(exf, EXEMPT_MAX_BYTES + 1)) != EXEMPT_MAX_BYTES + 1:
            failures.append("W7 exempt file: the read is not bounded to its cap")
        # a UTF-8 BOM must not swallow the first line
        with open(exf, "wb") as fh:
            fh.write(b"\xef\xbb\xbfbom/first\nsecond/line\n")
        cache.pop(tmp, None)
        got = load_exempt_prefixes(tmp)
        cache.pop(tmp, None)
        if got != frozenset(["bom/first", "second/line"]):
            failures.append("W7 exempt file: a BOM changed the first line: %r" % sorted(got))
        cache.pop(tmp, None)
        if not _exempt(["sources", "libs", "imgui", "x.h"], frozenset(["sources/libs"])) \
                or _exempt(["sources", "libsx", "a.h"], frozenset(["sources/libs"])):
            failures.append("W7 exempt prefix lookup is wrong")
        # 10-11: a real case-only rename (resolve() re-cases an existing path to its disk name)
        os.makedirs(os.path.join(tmp, "Sources"))
        open(os.path.join(tmp, "Sources", "a.cpp"), "w").close()
        real = {"tool_name": "Bash", "cwd": tmp, "tool_input": {"command": "mv Sources/a.cpp Sources/A.cpp"}}
        if [d[4] for _r, d in evaluate_shell(real, root=tmp)] != ["code"]:
            failures.append("W7 missed a real case-only rename (mv a.cpp A.cpp)")
        real["tool_input"]["command"] = "mv Sources/A.cpp Sources/a.cpp"  # no change at all
        if evaluate_shell(real, root=tmp):
            failures.append("W7 flagged a move to the name already on disk")
    finally:
        load_exempt_prefixes.__defaults__[0].pop(tmp, None)
        remove_selftest_tmp(tmp)
        if os.path.exists(tmp):
            failures.append("W7 self-test left %s behind" % tmp)


def run_self_test():
    global BUDGET_SECONDS
    failures = []
    fs = _fake_fs()
    load_exempt_prefixes.__defaults__[0][ROOT] = EXEMPT  # inject the optional file's content
    saved_budget = BUDGET_SECONDS
    BUDGET_SECONDS = 60.0  # the table cases test names, not the clock (a loaded machine is slow)
    try:
        _table_self_test(failures, fs)
        _cost_self_test(failures, fs)
    finally:
        BUDGET_SECONDS = saved_budget
    # the optional exempt file: comments, backslashes and blank lines are handled
    load_exempt_prefixes.__defaults__[0].pop(ROOT, None)
    if load_exempt_prefixes("C:\\no\\such\\root") != frozenset():
        failures.append("W7 exempt file: a missing file must give no prefixes")
    return failures


def _table_self_test(failures, fs):
    for tool, ti, want in CASES:
        ev = {"tool_name": tool, "cwd": ROOT, "tool_input": ti}
        try:
            th = evaluate_tool(ev, fs=fs, root=ROOT)
            sh = evaluate_shell(ev, fs=fs, root=ROOT)
        except Exception as e:
            failures.append("W7 raised %s on %s %r" % (type(e).__name__, tool, ti))
            continue
        kinds = [h[2] for h in th] + [d[4] for _r, d in sh]
        got = kinds[0] if kinds else None
        if got != want or len(kinds) > 1:
            failures.append("W7 must %s: %s %r (got %r)" % (
                "fire as %s" % want if want else "not fire", tool,
                excerpt(ti.get("file_path") or ti.get("command") or "")[:90], kinds))
    # the inner evaluators survive what crashed the replay (shell_tokens returning None)
    for cmd in ("echo \"unterminated > Sources/Bad.cpp",
                "$body = @'\nIt's `odd` \"text\n'@\nSet-Content -Path Sources\\Bad11.cpp -Value $body"):
        for tool in ("Bash", "PowerShell"):
            try:
                _evaluate_shell({"tool_name": tool, "cwd": ROOT, "tool_input": {"command": cmd}},
                                fs=fs, root=ROOT)
            except Exception as e:
                failures.append("W7 inner evaluator raised %s on %r" % (type(e).__name__, cmd[:40]))
    # the root is CLAUDE_PROJECT_DIR, never the cwd: a session in <root>\Sources still judges <root>
    saved = os.environ.get("CLAUDE_PROJECT_DIR")
    try:
        os.environ["CLAUDE_PROJECT_DIR"] = ROOT
        ev = {"tool_name": "Write", "cwd": _j("Sources"), "tool_input": {"file_path": _j("Bad7.cpp")}}
        if [h[2] for h in evaluate_tool(ev, fs=fs)] != ["code"]:
            failures.append("W7 did not judge against CLAUDE_PROJECT_DIR when cwd is below it")
        os.environ.pop("CLAUDE_PROJECT_DIR")
        if evaluate_tool(ev, fs=fs) or project_root() is not None:
            failures.append("W7 judged without CLAUDE_PROJECT_DIR (no cwd fallback allowed)")
    finally:
        if saved is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = saved
    # isdir/exists are never asked about a UNC path or a path outside the root
    asked = []
    spy = Fs(exists=lambda p: asked.append(p) or False, isdir=lambda p: asked.append(p) or False)
    for cmd in ("cp a.cpp //server/share/X/", "cp a.cpp \\\\server\\share\\X\\",
                "cp a.cpp C:\\elsewhere\\X\\", "mkdir //server/share/NewDir"):
        evaluate_shell({"tool_name": "Bash", "cwd": ROOT, "tool_input": {"command": cmd}},
                       fs=spy, root=ROOT)
    bad = [p for p in asked if is_network(p) or rel_parts(ROOT, p) is None]
    if bad:
        failures.append("W7 stat'ed %r (must be local and inside the root)" % bad)
    # one excerpt per segment; rows carry no msg; at most MAX_ROWS rows plus a truncated row
    sh = evaluate_shell({"tool_name": "Bash", "cwd": ROOT,
                         "tool_input": {"command": "touch Sources/P1.cpp Sources/P2.cpp"}},
                        fs=fs, root=ROOT)
    rows = log_rows({"tool_name": "Bash", "cwd": ROOT}, [], sh, 1)
    if len(rows) != 2 or rows[0]["sample"][0] is not rows[1]["sample"][0]:
        failures.append("W7 log_rows did not share one excerpt per segment")
    if any("msg" in r for r in rows):
        failures.append("W7 rows must not carry msg")
    many = " ".join("Sources/N%d.cpp" % i for i in range(120))
    sh = evaluate_shell({"tool_name": "Bash", "cwd": ROOT, "tool_input": {"command": "touch " + many}},
                        fs=fs, root=ROOT)
    rows = log_rows({"tool_name": "Bash", "cwd": ROOT}, [], sh, 1)
    if len(sh) != 120 or len(rows) != MAX_ROWS + 1 or rows[-1].get("truncated") != 120 - MAX_ROWS:
        failures.append("W7 row cap: %d hits -> %d rows" % (len(sh), len(rows)))
    # never denies, ASCII message, always log-only
    ev = {"tool_name": "Write", "cwd": ROOT, "tool_input": {"file_path": _j("Sources", "Bad8.cpp")}}
    th = evaluate_tool(ev, fs=fs, root=ROOT)
    if not th or decide(ev, th, [], enforce=True) is not None:
        failures.append("W7 denied while enforce=True, or missed the hit")
    try:
        MSG.encode("ascii")
    except UnicodeEncodeError:
        failures.append("W7 message is not ASCII")
    if not ALWAYS_LOG_ONLY:
        failures.append("W7 must be ALWAYS_LOG_ONLY")
