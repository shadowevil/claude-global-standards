"""W3: every write - and every delete, which is a write to the filesystem too - stays inside the
session's project root, ~/.claude, or (2026-09-28 audit fix) THIS session's own Claude Code
scratchpad (<tempdir>\\claude\\<project-slug>\\<session_id>\\scratchpad\\...) - the per-session temp
directory Claude Code's own system prompt tells every session to use, and which accounted for 67%
of this hook's hits across a real cross-project replay before the fix (see hooklib.paths.
session_scratchpad_regex). Ported from the Helbreath project hook's W3 section (hb_hooks.py),
generalized with no project-specific text anywhere: the deny message names no repo, no doc and no
path that is not the caller's own.

A delete's target is judged here rather than in a separate rules/delete/ module: the only question
THIS module asks about a delete is WHERE it lands (inside the allowed roots), which is exactly what
it already computes for every other write destination. Whether a delete's path syntax is itself
safe (fully qualified, no wildcards in a directory segment, ...) is judged separately, by
rules/delete/w2_literal_delete_paths.py - both modules call hooklib.shell.delete_verb_targets()
for the raw target list rather than each parsing it themselves.

Two shapes:
  evaluate_tool(event)  - a Write/Edit/MultiEdit/NotebookEdit event -> [(raw_target, resolved_bad_path), ...]
  evaluate_shell(event) - a Bash/PowerShell event -> [(RULE_ID, detail), ...]
decide(event, hits) turns either result into a PreToolUse deny, honouring hooklib.output.enforcing().

A target that cannot be judged statically ($variable, a glob before the last segment, a relative
path after a cd that cannot be followed) is left unjudged and produces no row. 2026-10-20 (user
approved): the old "W3-unresolved" row for such targets was removed - ~70% of all hit-log rows
(7,112 measured), never denied, carrying no verdict anyone acted on.
"""
import os
import re

from hooklib import paths
from hooklib import shell
from hooklib.output import excerpt

RULE_ID = "W3"

MSG = {
    RULE_ID: "Write outside the allowed roots. Rewrite the target as a path under the session's "
             "project root, or under ~/.claude/ - the one location this hook allows outside the "
             "project root.",
}

WRITE_VERBS_ALL_POS = {"mkdir", "touch"}
# "copy"/"move"/"ren" added here for cmd.exe's own builtins (positional-only, no -Destination
# concept) - a segment actually typed in PowerShell never reaches this set at all, since
# verb_write_targets checks PS_WRITE_CMDLETS (which has its OWN, flag-aware "copy"/"move"/"ren"
# aliases below) FIRST whenever `powershell` is True (2026-09-28 audit fix).
WRITE_VERBS_LAST_POS = {"cp", "mv", "install", "ln", "copy", "move", "ren"}
PS_WRITE_CMDLETS = {
    # pos_greedy: whether every positional from pos_index to the end of the arg list belongs to
    # the destination (True), or only the single token AT pos_index (False). Set-Content/
    # Add-Content's positional after the path is -Value (arbitrary content, not a second
    # destination) and Out-File's is -Encoding-like - collecting "idx to end" for those three
    # treats a content string that happens to look like a path as an extra write target (2026-09-28
    # review: `Set-Content notes.txt 'C:\...\other-repo\notes.txt'` gave a false-positive hit).
    # Accepted narrower coverage: a positional comma-array AT the destination slot itself
    # (`Set-Content 'a','b' -Value v`) is read as only its first element for these three - the
    # named -Path/-FilePath form just above still gets full array coverage via the "named" branch.
    "new-item": {"dest_flags": ("-path",), "pos_index": 0, "pos_greedy": True},
    "set-content": {"dest_flags": ("-path", "-filepath"), "pos_index": 0, "pos_greedy": False},
    "add-content": {"dest_flags": ("-path", "-filepath"), "pos_index": 0, "pos_greedy": False},
    "out-file": {"dest_flags": ("-filepath", "-path"), "pos_index": 0, "pos_greedy": False},
    "copy-item": {"dest_flags": ("-destination",), "pos_index": 1, "pos_greedy": True},
    "move-item": {"dest_flags": ("-destination",), "pos_index": 1, "pos_greedy": True},
    "rename-item": {"dest_flags": ("-newname",), "pos_index": 1, "pos_greedy": True},
    "expand-archive": {"dest_flags": ("-destinationpath",), "pos_index": 1, "pos_greedy": True},
    # 2026-09-28 PowerShell write-surface audit (reviewer finding: Tee-Object, the CMDLET name, not
    # its "tee" alias, was missing entirely - `... | Tee-Object -FilePath 'C:\outside\evil.txt'`
    # gave 0 W3 hits and could forge W4's witness store the same way). Added while auditing the
    # rest of the surface - see PS_ALIASES below for the alias names routed to these same specs,
    # and w3_outside_root's module-level report comment near PS_WRITE_CMDLETS's end for the cmdlets
    # deliberately left out and why.
    "tee-object": {"dest_flags": ("-filepath", "-literalpath"), "pos_index": 0, "pos_greedy": True},
    "export-csv": {"dest_flags": ("-path", "-literalpath"), "pos_index": 0, "pos_greedy": True},
    "export-clixml": {"dest_flags": ("-path", "-literalpath"), "pos_index": 0, "pos_greedy": True},
    "compress-archive": {"dest_flags": ("-destinationpath",), "pos_index": 1, "pos_greedy": True},
    "start-transcript": {"dest_flags": ("-path",), "pos_index": 0, "pos_greedy": True},
}
# Real PowerShell aliases/functions for several of the cmdlets above - each shares its canonical
# cmdlet's EXACT spec (same dict object, not a copy) since they take the same flags positionally.
# "tee" maps to Tee-Object here (single -FilePath, no array) - NOT the same shape as a genuine
# Bash/cmd `tee`, which can duplicate output to several files; that stays a separate, simpler
# branch in verb_write_targets, reached only when `powershell` is False.
PS_ALIASES = {
    "ni": "new-item", "mkdir": "new-item", "md": "new-item",
    "sc": "set-content",
    "ac": "add-content",
    "cpi": "copy-item", "copy": "copy-item",
    "mi": "move-item", "move": "move-item",
    "ren": "rename-item", "rni": "rename-item",
    "tee": "tee-object",
}
for _alias, _canonical in PS_ALIASES.items():
    PS_WRITE_CMDLETS[_alias] = PS_WRITE_CMDLETS[_canonical]
del _alias, _canonical
# PowerShell switch parameters (take no value) on the cmdlets above. Any OTHER "-Flag" is assumed
# to consume the next token as ITS value (the common case, e.g. `-ItemType Directory`) so that
# value is never mistaken for a positional destination.
PS_SWITCH_FLAGS = {"-force", "-recurse", "-whatif", "-confirm", "-verbose", "-passthru",
                    "-noclobber", "-append", "-nonewline", "-directory", "-file"}

# 2026-09-28 PowerShell write-surface audit (reviewer-requested, after the Tee-Object gap): what
# the reviewer's named list resolves to, so a future audit does not have to re-derive this.
#
# ALREADY COVERED, no change needed:
#   - `New-Item -ItemType Directory` - just a flag on New-Item, already in PS_WRITE_CMDLETS; the
#     unrecognized-flag branch in ps_write_targets consumes "-ItemType"'s own value already.
#   - `Set-Content -Stream <name>` - a flag selecting an NTFS alternate data stream of an already-
#     -Path-checked file, not a second path; does not change what gets root-checked.
#   - cmd.exe `type f > out` / `echo x > out` - a real redirect regardless of the verb in front of
#     it; find_redirects() already catches these from the segment's `>`/`>>` syntax alone.
#
# ADDED this pass: Tee-Object (the priority fix) plus Export-Csv, Export-Clixml, Compress-Archive
# -DestinationPath, Start-Transcript -Path (all above), and the alias set in PS_ALIASES (ni/mkdir/
# md, sc, ac, cpi/copy, mi/move, ren/rni, tee) - these used to fall through to the CRUDER "last
# positional is the destination" bash-style branches (or, for bash `cp`/`mv`, never reached
# PS_WRITE_CMDLETS's flag-aware parsing at all when used as a PowerShell alias with a NAMED
# -Destination), which does not know a `-Destination`/`-NewName` flag's value is not a second
# plain positional target.
#
# LEFT OUT, deliberately, with why:
#   - `[IO.File]::WriteAllText(...)` and any other [Type]::Method(...) .NET call (File.Copy,
#     File.AppendAllText, Directory.CreateDirectory, New-Object IO.StreamWriter, Add-Type,
#     Invoke-Expression, ...) - unbounded surface for a static, line-based parser; this is
#     effectively arbitrary code execution, not a fixed cmdlet grammar. Same stance as an
#     unresolvable $variable target: a known gap, never judged statically.
#   - `cmd /c copy ...` / `cmd.exe /c copy ...` as a WRAPPING sub-command - this hook does not
#     unwrap and re-parse a nested shell invocation's own command line (a general limitation, not
#     specific to copy/move/ren); `copy`/`move`/`ren` typed directly (no `cmd /c` wrapper) ARE
#     covered, in both PS_WRITE_CMDLETS (PowerShell) and WRITE_VERBS_LAST_POS (bash/cmd.exe).
REDIRECT_RE = re.compile(r"(\d*)(>>|>)\s*('[^']*'|\"[^\"]*\"|\S+)")
NULL_TARGETS = {"/dev/null", "nul", "$null"}
VAR_OR_SUBST = shell.VAR_OR_SUBST  # shared with rules/delete - both need "is this target a $var?"
# 2026-10-17 false-positive fixes (19-day replay, ~44% of W3 rows were FP):
# FP1 - a /dev/ pseudo-file (stdout, stderr, tty, fd/N, tcp/host/port) is never a file write.
PSEUDO_FILE_PREFIX = "/dev/"
# FP2 - parser junk: a pipe or newline inside the token, a leading # or (, or a bare root token
# left over from a glob/brace split - skipped. Unbalanced parens, a leftover quote, or a glob
# (? *) cannot be judged statically - left unjudged, no row. Balanced parens ("Program Files
# (x86)") are a real path and are judged.
JUNK_CHARS = re.compile(r"[|\n]")
JUNK_EXACT = {"/", "\\", "/*"}
GLOB_CHARS = re.compile(r"[?*\[]")
# a literal backslash-n (or -t/-r) not followed by a name character is an escape sequence from
# quoted source text, not a path: `\n#include`, `out\n`. C:\new\x stays a path.
ESCAPE_SEQUENCE = re.compile(r"\\[ntr](?![\w.-])")
# a bash brace expansion ({a,b} / {1..3}) - kept whole as one word for W3's parse (the shared
# splitter cuts at { and }), then judged by the folder before it, or unresolved when it has none
BRACE_WORD = re.compile(r"\{[^{}\s'\"]*(?:,|\.\.)[^{}\s'\"]*\}")
VAR_BRACE = re.compile(r"\$\{[A-Za-z_]\w*\}")  # ${NAME}: one word, not a group
BRACE_OPEN, BRACE_CLOSE = "\x01", "\x02"
# FP3 - any sign of expansion still in the token: $ (incl. $$, $1, $?), a backtick, or a cmd
# %VAR%. Wider than VAR_OR_SUBST on purpose (that one is shared with the enforced W2): resolving
# the literal remainder of `$D/src` judged it as C:/src.
DYNAMIC_TARGET = re.compile(r"\$|`|%[A-Za-z_][\w()]*%")

# ------------------------------------------------------------------ tool-call side


def w3_tool_targets(tool, ti):
    if tool == "NotebookEdit":
        p = ti.get("notebook_path")
        return [p] if p else []
    if tool in ("Write", "Edit", "MultiEdit"):
        p = ti.get("file_path")
        return [p] if p else []
    return []


def evaluate_tool(event):
    """[(raw_target, resolved_bad_path), ...] for a Write/Edit/MultiEdit/NotebookEdit event."""
    tool = event.get("tool_name") or ""
    out = []
    for t in w3_tool_targets(tool, event.get("tool_input") or {}):
        bad = paths.violation(t, event)
        if bad is not None:
            out.append((t, bad))
    return out


# ------------------------------------------------------------------ shell side

# delete verbs count as writes for W3's purposes (see module docstring). delete_verb_targets is
# shared parsing (hooklib.shell) - not redefined here.


def find_redirects(seg):
    """[(op, target), ...] for every real, UNQUOTED file redirect in `seg` - `2>&1` fd dups, null
    targets (/dev/null, NUL, $null), and a `>` that only appears inside a quoted string are all
    excluded."""
    out = []
    masked = shell.mask_quotes(seg)
    for m in REDIRECT_RE.finditer(masked):
        op = (m.group(1) or "") + m.group(2)
        raw = seg[m.start(3):m.end(3)]  # the REAL text at this span, not the masked placeholder
        if re.match(r"^&\d+$", raw):
            continue
        quoted = len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"')
        t = raw[1:-1] if quoted else raw.rstrip(")]};,")  # an unquoted target's trailing shell
        # punctuation - `2>/dev/null)` closing a subshell - is never part of the actual filename
        if t.lower() in NULL_TARGETS:
            continue
        out.append((op, t))
    return out


def ps_write_targets(toks):
    """[dest_token, ...] for a PowerShell write cmdlet invocation - possibly several: a dest flag's
    value is PowerShell array-typed (`-Path 'a','b'`), so once shell_tokens has comma-split it into
    consecutive tokens, EVERY one up to the next flag belongs to it, not just the first (security
    fix, 2026-09-28 review: returning only the first silently dropped later array elements from
    W3's own root check - `New-Item -Path '<in-root>\\a','C:\\x\\b'` gave 0 hits). Named
    -Path/-Destination/... wins; else the cmdlet's own positional destination slot (see
    PS_WRITE_CMDLETS).

    Second security fix, 2026-09-28 re-review: the COLON form (`-Path:'a','b'`, no space before the
    first value) only ever added that one `m.group(2)` value and never ran the "collect every
    following non-flag token" loop the space form uses - so `New-Item -Path:'<in-root>\\a',
    'C:\\x\\evil.txt'` still gave 0 hits even after the first fix above (the LATER array element
    was silently dropped again, just one token later than before). Fixed by running the SAME
    collect-loop after a colon match too, seeded with its captured value first.

    Third security fix, 2026-09-28 re-review (hotfix2 review, reviewer-verified): `-Path:@(...)`,
    an @()-wrapped array attached directly to the colon, evades BOTH fixes above. expand_ps_commas
    turns `@(` into spaces before tokenizing (see its docstring), so the token that actually reaches
    here is a BARE `-Path:` with nothing after the colon - it matches neither the colon-value regex
    (needs a non-empty value) nor a plain `-path` (it still has the trailing colon), so it fell into
    the "unrecognized flag, consume and discard its own value" branch below, silently eating the
    first array element regardless of position. Fixed by also recognizing a bare trailing-colon
    token and mapping it to the SAME flag name a colon-with-value or a space form would - once
    `base_flag` reads "-path" either way, the existing dest_flags branch and its collect-loop
    (already fixed above) handle it identically to every other form.

    Fourth security fix (hotfix2's final review, pre-existing, identical in the live hook and every
    staged tree): a POSITIONAL comma-array - no flag at all, e.g. `New-Item 'C:\\a','C:\\x\\evil'`
    or `Copy-Item x 'C:\\a','C:\\x\\evil'` - checked only `positionals[idx]`, the single element AT
    the destination's positional slot, never the rest of the array sitting right after it in the
    SAME `positionals` list (every comma-split element that is not itself a flag value already
    lands in `positionals`, in order - the array was there all along, just never read past its
    first entry). Fixed by returning every positional from `idx` to the end, not just one - but
    ONLY for cmdlets where nothing else ever follows the destination positionally.

    Fifth security fix, and a correction of the fourth (2026-09-28 re-review, false positive):
    Set-Content and Add-Content DO place something after their destination positional - `-Value`,
    an arbitrary content string, not a second destination - and Out-File's is an encoding name.
    The fourth fix's blanket "idx to end" wrongly treated that content as extra destinations, e.g.
    `Set-Content notes.txt 'C:\\...\\other-repo\\notes.txt'` (a content string that happens to look
    like a path) fired a hit with nothing at all live. Fixed with the `pos_greedy` flag on
    PS_WRITE_CMDLETS: greedy (idx-to-end) only for cmdlets with no positional after the
    destination; the other three read just the single token at `idx`, matching pre-fourth-fix
    behaviour for them (see the dict's own comment for the accepted narrower coverage)."""
    spec = PS_WRITE_CMDLETS.get(toks[0].lower())
    if spec is None:
        return []
    named = []
    positionals = []
    i = 1
    while i < len(toks):
        t = toks[i]
        tl = t.lower()
        m = re.match(r"^(-[a-z]+):(.+)$", t, re.I)
        bare_colon = re.match(r"^(-[a-z]+):$", t, re.I)
        base_flag = (m.group(1).lower() if m else
                     (bare_colon.group(1).lower() if bare_colon else
                      (tl if tl.startswith("-") else None)))
        if base_flag:
            if base_flag in spec["dest_flags"]:
                if m:
                    value = m.group(2)
                    # same quote-strip as hooklib.shell.delete_verb_targets: this value is still
                    # glued to "-flag:" when shlex sees it, so shell_tokens' own whole-token strip
                    # never runs on it. Currently masked downstream by paths.resolve()'s own
                    # strip('"').strip("'"), but fixed here too so this function is correct on its
                    # own, not merely lucky given its one current caller.
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                        value = value[1:-1]
                    named.append(value)
                j = i + 1
                while j < len(toks) and not toks[j].startswith("-"):
                    named.append(toks[j])
                    j += 1
                i = j - 1
            elif not m and base_flag not in PS_SWITCH_FLAGS and i + 1 < len(toks) \
                    and not toks[i + 1].startswith("-"):
                i += 1  # an unrecognized flag's own value - skip it too, not a positional
            i += 1
            continue
        positionals.append(t)
        i += 1
    if named:
        return named
    idx = spec["pos_index"]
    if idx >= len(positionals):
        return []
    if spec.get("pos_greedy", True):
        return positionals[idx:]
    return [positionals[idx]]


def verb_write_targets(seg, powershell=False):
    """[(verb, target), ...] for mkdir/touch/cp/mv/install/ln/tee, git worktree add, git clone with
    an explicit destination, and the PowerShell write cmdlets (incl. aliases - PS_ALIASES). `powershell`
    gates comma-array splitting in shell_tokens (see its docstring) AND, since 2026-09-28, which
    branch several overloaded verb names resolve to: `copy`/`move`/`ren`/`tee` are real PowerShell
    aliases (Copy-Item/Move-Item/Rename-Item/Tee-Object, each flag-aware - e.g. -Destination) as
    well as separate cmd.exe builtins or a genuine Bash/coreutils tool (positional-only, and for
    `tee`, able to write several files at once) - so PS_WRITE_CMDLETS is checked FIRST whenever
    `powershell` is True, before any of these names could instead match the cruder positional-only
    branches below meant for the non-PowerShell case."""
    toks = shell.shell_tokens(seg, powershell)
    if not toks:
        return []
    head_l = toks[0].lower()
    if powershell and head_l in PS_WRITE_CMDLETS:
        return [(toks[0], t) for t in ps_write_targets(toks)]
    positionals = [t for t in toks[1:] if not t.startswith("-") and not shell.CMD_FLAG.match(t)]
    out = []
    if head_l in WRITE_VERBS_ALL_POS:
        out.extend((toks[0], p) for p in positionals)
    elif head_l in WRITE_VERBS_LAST_POS:
        if positionals:
            out.append((toks[0], positionals[-1]))
    elif head_l == "tee":
        out.extend((toks[0], p) for p in positionals)
    elif head_l == "git":
        if len(toks) >= 3 and toks[1].lower() == "worktree" and toks[2].lower() == "add":
            args = [t for t in toks[3:] if not t.startswith("-")]
            if args:
                out.append(("git worktree add", args[0]))
        elif len(toks) >= 2 and toks[1].lower() == "clone":
            args = [t for t in toks[2:] if not t.startswith("-")]
            if len(args) >= 2:
                out.append(("git clone", args[1]))
    elif head_l in PS_WRITE_CMDLETS:
        # unreachable when `powershell` is True (handled above) - kept as a defensive fallback for
        # a caller that passes powershell=False for a segment that is actually PowerShell.
        for t in ps_write_targets(toks):
            out.append((toks[0], t))
    return out


def mask_brace_words(cmd):
    """cmd with the braces of every UNQUOTED brace-expansion word swapped for placeholder bytes, so
    split_top_level keeps the word whole; unmask_brace_words puts them back."""
    masked = shell.mask_quotes(cmd)
    out = list(cmd)
    for m in BRACE_WORD.finditer(masked):
        out[m.start()] = BRACE_OPEN
        out[m.end() - 1] = BRACE_CLOSE
    for m in VAR_BRACE.finditer(masked):
        out[m.start() + 1] = BRACE_OPEN  # skip the $
        out[m.end() - 1] = BRACE_CLOSE
    return "".join(out)


def unmask_brace_words(s):
    return s.replace(BRACE_OPEN, "{").replace(BRACE_CLOSE, "}")


def find_write_targets(cmd, powershell=False):
    """[(segment, verb, raw_target), ...] - see find_write_targets_cwd; kept for W4, which needs no
    working-directory tracking (and keeps the plain, unmasked brace split)."""
    return [(seg, verb, raw) for seg, verb, raw, _cwd in find_write_targets_cwd(cmd, powershell)]


def find_write_targets_cwd(cmd, powershell=False, start_cwd=None, brace_words=False):
    """[(segment, verb, raw_target), ...] for every statically-visible write destination in `cmd`:
    redirects, tee, cp/mv/install/ln/mkdir/touch, the PowerShell write cmdlets, `git worktree add`,
    `git clone` with an explicit destination, and every delete target (a delete is a write too).
    `powershell` (from the event's own tool_name) gates comma-array splitting - see
    hooklib.shell.shell_tokens. Each row also carries the segment's effective cwd from
    hooklib.shell.cwd_per_segment (None, a path, or shell.UNRESOLVED_CWD)."""
    out = []
    if brace_words:
        cmd = mask_brace_words(cmd)
    triples = shell.split_top_level_seps(cmd)
    segments = [t[0] for t in triples]
    cwds = shell.cwd_per_segment(segments, start_cwd, paths.home_dir(), paths.msys_to_windows,
                                 powershell, [(t[1], t[2]) for t in triples])
    for seg, seg_cwd in zip(segments, cwds):
        d = shell.delete_verb_targets(seg, powershell)
        if d is not None:
            verb, targets, _is_recursive = d
            for t in targets:
                out.append((seg.strip(), verb, t, seg_cwd))
        for op, tgt in find_redirects(seg):
            out.append((seg.strip(), op, tgt, seg_cwd))
        for verb, tgt in verb_write_targets(seg, powershell):
            out.append((seg.strip(), verb, tgt, seg_cwd))
    return out


def classify_target(raw):
    """(kind, target): kind is 'skip' (a pseudo-file or parser junk - not a write at all),
    'unresolved' (cannot be judged statically), or 'judge'; target is what to judge - the token
    itself, or its folder part when a wildcard sits only in the final segment."""
    t = raw.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        t = t[1:-1]
    if not t or t.lower() in NULL_TARGETS:
        return "skip", t
    if t.startswith(PSEUDO_FILE_PREFIX) and ".." not in re.split(r"[\\/]", t):
        return "skip", t  # case-sensitive: /DEV/x is a real directory on Windows
    if DYNAMIC_TARGET.search(t):
        return "unresolved", t
    if JUNK_CHARS.search(t) or t.startswith(("#", "(")) or t in JUNK_EXACT:
        return "skip", t
    if t.count("(") != t.count(")") or "'" in t or '"' in t or ESCAPE_SEQUENCE.search(t):
        return "unresolved", t
    brace = BRACE_WORD.search(t)
    if brace:
        prefix = t[:brace.start()]
        cut = max(prefix.rfind("/"), prefix.rfind("\\"))
        if cut < 0:
            return "unresolved", t  # {a,/c/x/e}, {1..3}: no literal folder to judge
        folder = prefix[:cut] if cut > 0 else prefix[:1]
        if GLOB_CHARS.search(folder) or "{" in folder:
            return "unresolved", t
        return "judge", folder  # /c/x/{a,b}, /c/x/f{1..3}: judge the folder they land in
    if GLOB_CHARS.search(t):
        cut = max(t.rfind("/"), t.rfind("\\"))
        folder = t[:cut] if cut > 0 else (t[:1] if cut == 0 else ".")
        if GLOB_CHARS.search(folder):
            return "unresolved", t  # a wildcard before the final segment
        return "judge", folder  # wildcard only in the file name: judge the folder it lands in
    return "judge", t


def reglue(seg, raw):
    """`raw` joined back onto a quoted word it was typed against with no space (`"$D"/x`), or raw
    unchanged. shell_tokens (shared with the enforced W2, so left as is) splits such a word in two,
    and judging the tail alone read `"$D"/hb_*` as C:/hb_*. With a $variable prefix the joined
    target is left unjudged. With a LITERAL prefix (`"/c/x"/e.txt`) the quoted word is
    itself also a target and is judged on its own (`/c/x` -> W3 when outside), and the tail, when
    it reaches here, is judged joined (`/c/x/e.txt`). A one-letter tail (`"/c/x"/e`) never reaches
    here: the delete parser reads `/e` as a cmd.exe switch, so only the quoted word is judged."""
    if not raw or raw[0] not in "/\\":
        return raw
    m = re.search(r"([\"'])([^\"'\s]*)\1" + re.escape(raw) + r"(?=\s|$|[;&|)])", seg)
    if not m:
        return raw
    return m.group(2) + raw


def is_relative(raw):
    t = paths.msys_to_windows(raw.strip())
    return not re.match(r"^[A-Za-z]:\\", t) and not t.startswith("\\\\")


def evaluate_shell(event, unjudged=None):
    """[(RULE_ID, (segment, verb, raw_target, resolved_bad_path)), ...] for every statically-
    resolvable target outside every allowed root. A target this parser cannot resolve gives no hit;
    when `unjudged` is a list, its raw target is appended there (the self-test reads it to prove
    such a target is left alone rather than judged)."""
    hits = []
    tool = event.get("tool_name") or ""
    if tool not in ("Bash", "PowerShell"):
        return hits
    cmd = (event.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return hits
    powershell = tool == "PowerShell"
    for seg, verb, raw, seg_cwd in find_write_targets_cwd(cmd, powershell, event.get("cwd"),
                                                          brace_words=True):
        seg, raw = unmask_brace_words(seg), unmask_brace_words(raw)
        kind, judged = classify_target(reglue(seg, raw))
        if kind == "skip":
            continue
        if kind == "unresolved" or (seg_cwd is shell.UNRESOLVED_CWD and is_relative(judged)):
            # not statically judgeable (or relative to a cd we cannot follow): no row
            if unjudged is not None:
                unjudged.append(raw)
            continue
        bad = paths.violation(judged, event, seg_cwd if isinstance(seg_cwd, str) else None)
        if bad is not None:
            hits.append((RULE_ID, (seg, verb, raw, bad)))
    return hits


# ------------------------------------------------------------------ decide / log rows


def log_rows(event, tool_hits, shell_hits, ms):
    """[{"rule", "tool", "root", "cwd", "target", "sample", "session", "ms", "msg"}, ...]. `root`
    and `cwd` (2026-09-28, user decision on the week-long log-only rollout) let the week's hits be
    grouped per project and per calling directory once the real numbers come in - in particular,
    whether a project's own writes land under a DIFFERENT root than its own `cwd` (the
    helbreath_war shared-.claude question) shows up directly as root != cwd in the log, not as
    something that has to be re-derived from `sample` text afterwards. `target` is the raw,
    unresolved string the hit was found on - the same value `sample` narrates in prose - kept as
    its own field so the log can be queried/grouped by target without re-parsing `sample`."""
    root = paths.project_root(event)
    cwd = event.get("cwd")
    rows = []
    for raw, bad in tool_hits:
        rows.append({"rule": RULE_ID, "tool": event.get("tool_name"), "root": root, "cwd": cwd,
                      "target": raw, "sample": [excerpt("%s -> %s" % (raw, bad))],
                      "session": event.get("session_id"), "ms": ms, "msg": MSG[RULE_ID]})
    for _rid, (_seg, verb, raw, bad) in shell_hits:
        sample = excerpt("%s -> %s (resolves outside allowed roots: %s)" % (verb, raw, bad))
        rows.append({"rule": RULE_ID, "tool": event.get("tool_name"), "root": root, "cwd": cwd,
                      "target": raw, "sample": [sample],
                      "session": event.get("session_id"), "ms": ms, "msg": MSG[RULE_ID]})
    return rows


def decide(event, tool_hits, shell_hits, enforce):
    """The PreToolUse deny for these hits, or None."""
    if not enforce:
        return None
    reasons = []
    for raw, bad in tool_hits:
        reasons.append("W3: `%s` -> %s - %s" % (excerpt(raw), bad, MSG[RULE_ID]))
    for _rid, detail in shell_hits:
        seg, _verb, raw, bad = detail
        reasons.append("W3: `%s` -> %s (resolves to %s, outside the allowed roots) - %s"
                        % (excerpt(seg), raw, bad, MSG[RULE_ID]))
    if not reasons:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": " | ".join(reasons)}}


# ------------------------------------------------------------------ self-test (cases live next to the rule)

# a synthetic root - proves the rule is not hard-coded to any one project (no case below needs a
# real directory on disk; every check here is lexical)
ROOT = "C:\\Users\\ShadowEvil\\test-project-root"
# the running user's real ~/.claude (always an allowed root); 2026-10-21: was hard-coded to the
# owner's home, so these cases failed for anyone else. A plain env read keeps the import cheap.
_TEST_CLAUDE = (os.environ.get("USERPROFILE") or os.environ.get("HOME") or "C:\\Users\\tester") + "\\.claude"

# (tool, tool_input, must fire)
TOOL_CASES = [
    ("Write", {"file_path": ROOT + "\\working\\x.md"}, False),
    ("Edit", {"file_path": ROOT + "\\CLAUDE.md"}, False),
    ("Write", {"file_path": "C:\\snap-f1\\x.md"}, True),
    ("Write", {"file_path": "C:\\Users\\SHADOW~1\\AppData\\Local\\Temp\\x.md"}, True),
    ("NotebookEdit", {"notebook_path": _TEST_CLAUDE + "\\projects\\foo\\memory\\x.ipynb"}, False),
    ("NotebookEdit", {"notebook_path": _TEST_CLAUDE + "\\hooks\\x.ipynb"}, False),  # whole ~/.claude
    ("MultiEdit", {"file_path": ROOT + "\\src\\shared\\x.h"}, False),
    ("Write", {"file_path": "src\\client\\x.cpp"}, False),  # relative, resolves under cwd==root
]

# (command, must fire)
SHELL_CASES = [
    ("mkdir " + ROOT + "\\working\\newdir", False),
    ("mkdir working\\newdir", False),                            # relative to cwd==root
    ("mkdir /c/SHADOW~1", True),
    ("echo x > C:\\x.txt", True),
    ("echo x > /dev/null", False),
    ("echo x 2>$null", False),
    ("echo x 2>&1", False),
    ("(echo x) 2>/dev/null)", False),                             # a subshell's trailing ) is not part of the target
    ('echo "> C:\\x" > ' + ROOT + '\\working\\safe.txt', False),
    ("cp " + ROOT + "\\working\\a C:\\tmp\\b", True),
    ("cp C:\\tmp\\a " + ROOT + "\\working\\b", False),            # INTO the root
    ("New-Item -ItemType Directory C:\\foo", True),
    ("Copy-Item x -Destination D:\\y", True),
    ("Out-File -FilePath ..\\..\\x.txt", True),                   # climbs above the root
    ("git worktree add .claude/worktrees/x", False),
    ("git worktree add ../outside", True),
    ("rm -rf /c/snap-f1", True),                                  # a delete outside the root
    ("rm -rf " + ROOT.replace("C:\\", "/c/").replace("\\", "/") + "/working/x", False),
    ("cat C:\\x", False),
    ("Get-Content C:\\x", False),
    ("Write-Host \"writing to $env:TEMP\\x.txt now\" > " + ROOT + "\\working\\log.txt",
     False),  # the redirect target is fine; the $env: text is just an echoed string, not a redirect
]

# PowerShell comma-array destinations, tested with tool_name=PowerShell (comma-splitting only ever
# runs there). A MODULE-LEVEL counted list, not an inline tuple in run_self_test() - write_guard.py's
# self_test() sums len(PS_COMMA_CASES) into its printed total the same way it already does for
# TOOL_CASES/SHELL_CASES, so a case added here is a case the printed count actually reflects (review
# finding, 2026-09-28: the original 2-case PS-array-bypass pair lived as an inline tuple and was
# invisible to that count). Covers both security fixes: the SPACE form regression pair kept for
# posterity, and the COLON form (`-Path:'a','b'`, no space before the first value) with the
# out-of-root element in first/middle/last array position, plus the must-NOT-fire controls (every
# element in-root; a `-Force:$true` boolean switch must never be mistaken for a target).
PS_COMMA_CASES = [
    # space form (first fix, 2026-09-28 review)
    ("Remove-Item -LiteralPath '" + ROOT + "\\working\\a.png','C:\\Windows\\System32\\evil.dll'", True),
    ("New-Item -Path '" + ROOT + "\\working\\a','C:\\x\\b'", True),
    # colon form (second fix, 2026-09-28 re-review) - Remove-Item (shared delete_verb_targets)
    ("Remove-Item -LiteralPath:'C:\\x\\evil.txt','" + ROOT + "\\a','" + ROOT + "\\b'", True),   # evil FIRST
    ("Remove-Item -LiteralPath:'" + ROOT + "\\a','C:\\x\\evil.txt','" + ROOT + "\\b'", True),   # evil MIDDLE
    ("Remove-Item -LiteralPath:'" + ROOT + "\\a','" + ROOT + "\\b','C:\\x\\evil.txt'", True),   # evil LAST
    ("Remove-Item -LiteralPath:'" + ROOT + "\\a','" + ROOT + "\\b','" + ROOT + "\\c'", False),  # all in-root
    ("Remove-Item -Force:$true -LiteralPath 'C:\\x\\evil.txt'", True),   # -Force:$true must not eat the real target
    # colon form - New-Item (ps_write_targets)
    ("New-Item -Path:'C:\\x\\evil.txt','" + ROOT + "\\a','" + ROOT + "\\b'", True),             # evil FIRST
    ("New-Item -Path:'" + ROOT + "\\a','C:\\x\\evil.txt','" + ROOT + "\\b'", True),             # evil MIDDLE
    ("New-Item -Path:'" + ROOT + "\\a','" + ROOT + "\\b','C:\\x\\evil.txt'", True),             # evil LAST
    ("New-Item -Path:'" + ROOT + "\\a','" + ROOT + "\\b','" + ROOT + "\\c'", False),            # all in-root
    ("New-Item -Path 'C:\\x\\evil.txt' -Force:$true", True),             # trailing -Force:$true, still caught
    # @()-wrapped colon form (third fix, 2026-09-28 hotfix2 review): expand_ps_commas turns "@("
    # into spaces BEFORE tokenizing, so `-Path:@(...)` reaches ps_write_targets as a bare `-Path:`
    # token (nothing after the colon) - neither the colon-value regex nor a plain "-path" matched
    # it, so it fell into the "unrecognized flag, discard its value" branch and silently ate the
    # first array element regardless of position.
    ("New-Item -Path:@('C:\\x\\evil.txt','" + ROOT + "\\a')", True),                  # evil FIRST, @(...)
    ("New-Item -Path:@('" + ROOT + "\\a','C:\\x\\evil.txt')", True),                  # evil LAST, @(...)
    ("New-Item -Path:@('" + ROOT + "\\a','" + ROOT + "\\b')", False),                 # all in-root, @(...)
    ("Copy-Item x -Destination:@('C:\\x\\evil.txt','" + ROOT + "\\a')", True),         # -Destination:@(...)
    ("Set-Content -FilePath:@('C:\\x\\evil.txt','" + ROOT + "\\a')", True),           # -FilePath:@(...)
    ("New-Item -Path: 'C:\\x\\evil.txt','" + ROOT + "\\a'", True),                    # colon, THEN a space
]

# Fourth fix's cases (hotfix2's final review, pre-existing bug, identical in the live hook): a
# POSITIONAL comma-array (no flag at all) - `New-Item 'a','b'`, `Copy-Item x 'a','b'` - checked
# only positionals[idx], the FIRST element at the destination's own positional slot, never the
# rest of the array sitting right after it. Generated for every pos_greedy=True PS_WRITE_CMDLETS
# entry (a pos_index=1 cmdlet gets a leading source token 'x' first, matching Copy-Item/Move-Item/
# Rename-Item/Expand-Archive's own source-then-destination shape) - evil element first/middle/last
# in a 3-element array, plus an all-in-root control, appended onto PS_COMMA_CASES so
# write_guard.py's self_test() count picks these up the same way as every other case here.
_EVIL = "C:\\x\\evil.txt"
for _cmdlet in sorted(PS_WRITE_CMDLETS):
    if not PS_WRITE_CMDLETS[_cmdlet].get("pos_greedy", True):
        continue  # Set-Content/Add-Content/Out-File: see the pos_greedy=False cases below instead
    _prefix = "x " if PS_WRITE_CMDLETS[_cmdlet]["pos_index"] == 1 else ""
    for _evil_pos in (0, 1, 2):
        _elems = [ROOT + "\\a", ROOT + "\\b", ROOT + "\\c"]
        _elems[_evil_pos] = _EVIL
        _arr = ",".join("'" + _e + "'" for _e in _elems)
        PS_COMMA_CASES.append((_cmdlet + " " + _prefix + _arr, True))
    _elems_ok = [ROOT + "\\a", ROOT + "\\b", ROOT + "\\c"]
    _arr_ok = ",".join("'" + _e + "'" for _e in _elems_ok)
    PS_COMMA_CASES.append((_cmdlet + " " + _prefix + _arr_ok, False))  # all in-root, must not fire

# Fifth fix's cases (2026-09-28 re-review, false positive in the fourth fix): Set-Content,
# Add-Content and Out-File place something other than a destination right after their positional
# path - -Value content, or an encoding name - so unlike the cmdlets above, a token there must
# NEVER be read as an extra write target even when it happens to look like an out-of-root path.
# Exact reported repro plus the coordinator's required permanent must-not-fire shapes.
PS_COMMA_CASES.append(
    ("Set-Content notes.txt 'C:\\Users\\ShadowEvil\\other-repo\\notes.txt'", False)
)
PS_COMMA_CASES.append(
    ("Set-Content " + ROOT + "\\a.txt 'C:\\x\\outside-looking.txt'", False)
)
PS_COMMA_CASES.append(
    ("Add-Content " + ROOT + "\\a.txt 'C:\\x\\outside-looking.txt'", False)
)
PS_COMMA_CASES.append(
    ("Out-File " + ROOT + "\\a.txt utf8", False)
)
# sanity: the destination slot itself is still read for these three, single value (not an array)
PS_COMMA_CASES.append(("Set-Content 'C:\\x\\evil.txt'", True))
PS_COMMA_CASES.append(("Set-Content '" + ROOT + "\\a.txt'", False))

# 2026-09-28 PowerShell write-surface audit: NAMED-flag and POSITIONAL must-fire cases, plus an
# in-root must-not-fire control, for every cmdlet/alias added this pass (the reviewer's own repro
# shape, e.g. `Tee-Object -FilePath 'C:\outside\evil.txt'`) - the generic positional-array loop
# above already covers the comma-array shape for every pos_greedy=True entry (including these), so
# this list is deliberately just the plain named/positional forms, not another array sweep.
PS_AUDIT_CASES = []
for _cmdlet in ("tee-object", "export-csv", "export-clixml", "compress-archive",
                 "start-transcript", "ni", "mkdir", "md", "sc", "ac", "cpi", "copy", "mi", "move",
                 "ren", "rni", "tee"):
    _spec = PS_WRITE_CMDLETS[_cmdlet]
    _flag = _spec["dest_flags"][0]
    _prefix = "x " if _spec["pos_index"] == 1 else ""
    PS_AUDIT_CASES.append((_cmdlet + " " + _prefix + _flag + " 'C:\\x\\evil.txt'", True))
    PS_AUDIT_CASES.append((_cmdlet + " " + _prefix + "'C:\\x\\evil.txt'", True))
    PS_AUDIT_CASES.append((_cmdlet + " " + _prefix + "'" + ROOT + "\\a.txt'", False))
del _cmdlet, _spec, _flag, _prefix
# the reviewer's own two repro lines, verbatim in shape (a witness-store-style path standing in for
# the real one, which lives under a real ~/.claude the self-test must never touch)
PS_AUDIT_CASES.append(("Tee-Object -FilePath 'C:\\outside\\evil.txt'", True))
PS_AUDIT_CASES.append(
    ("Tee-Object -FilePath '" + ROOT + "\\.claude\\hooks\\review_stamps_witness.jsonl'", False)
)
# WRITE_VERBS_LAST_POS: cmd.exe-style copy/move/ren (positional only, NOT PowerShell - proves the
# dispatch fix routes these to the bash-style branch only when `powershell` is False)
CMD_EXE_CASES = [
    ("copy a.txt C:\\x\\evil.txt", True), ("copy a.txt " + ROOT + "\\a.txt", False),
    ("move a.txt C:\\x\\evil.txt", True), ("move a.txt " + ROOT + "\\a.txt", False),
    ("ren a.txt C:\\x\\evil.txt", True), ("ren a.txt " + ROOT + "\\a.txt", False),
]

# the 8.3-root and junction fix cases at the end of run_self_test() each need a real temp dir and a
# live OS call (GetShortPathNameW / mklink), not a static tuple, so neither can live in any of the
# case lists above - counted here instead so write_guard.py's self_test() case count still reflects
# them, plus the two realpath-garbage fallback cases (2026-09-28 hotfix).
N_CASES = 5 + 17  # + the 2026-10-17 settings/side-notes real-file cases

# 2026-10-17 false-positive fixes. (tool, command, expected) - expected is "W3" (must fire),
# "unjudged" (no row, but left unjudged rather than judged fine - see evaluate_shell's `unjudged`),
# or None (judged, no row). cwd == ROOT.
_OUT = "C:\\x\\evil.txt"
FP_CASES = [
    # FP1 pseudo-files
    ("Bash", "echo x > /dev/stdout", None),
    ("Bash", "echo x 2> /dev/stderr", None),
    ("Bash", "echo x > /dev/tty", None),
    ("Bash", "echo x > /dev/fd/3", None),
    ("Bash", "echo x > /dev/tcp/127.0.0.1/80", None),
    ("Bash", "tee /dev/stderr < a.txt", None),
    ("Bash", "echo x > /devices/out.txt", "W3"),                  # only the /dev/ prefix is exempt
    ("Bash", "echo x > " + _OUT, "W3"),
    # FP2 parser junk
    ("Bash", "rm -rf /*", None),
    ("Bash", "cp a /", None),
    ("Bash", "cp a \\", None),
    ("Bash", "mkdir 'C:\\x\\a\"b'", "unjudged"),                   # leftover quote
    ("Bash", "mkdir C:\\x\\a#b", "W3"),                               # inner # is a real name
    ("Bash", "mkdir C:\\x\\a?b", "W3"),                             # final-segment glob: folder judged
    ("Bash", "rm C:\\x\\*.log", "W3"),                                # final-segment glob: folder judged
    ("Bash", "rm /c/x/v_?.out", "W3"),
    ("Bash", "rm C:\\x\\[ab].log", "W3"),
    ("PowerShell", "Remove-Item C:\\x\\*.log", "W3"),
    ("Bash", "rm " + ROOT + "\\working\\*.log", None),                 # in-root folder: fine
    ("Bash", "rm -f working/*.tmp", None),                             # relative, in-root
    ("Bash", "rm -f *.tmp", None),                                     # bare glob = the cwd
    ("Bash", "cd C:\\x && rm -f *.tmp", "W3"),                         # bare glob after cd outside
    ("Bash", "rm C:\\x*\\a.log", "unjudged"),                          # glob before the last segment
    ("Bash", "rm " + ROOT + "\\w*\\a.log", "unjudged"),
    ("Bash", "echo x > '\\n#include'", "unjudged"),                    # escape sequence, not a path
    ("Bash", "echo x > C:\\x\\out\\n", "unjudged"),
    ("Bash", "echo x > C:\\new\\x.txt", "W3"),                         # \n + name char is a path
    ("Bash", 'rm -f "$D"/hb_*', "unjudged"),                          # quoted $var prefix re-glued
    ("Bash", 'rm -f "$S/$d"/appdata/x-*.json', "unjudged"),
    ("Bash", "rm -f '$D'/x.txt", "unjudged"),
    ("Bash", 'rm -f "C:/x"/y.txt', "W3"),                             # literal quoted prefix: joined
    ("Bash", 'rm -f "' + ROOT.replace("\\", "/") + '"/y.txt', None),  # ... and in-root is fine
    ("Bash", 'rm -f "a" /c/x/evil.txt', "W3"),                        # a space: separate words
    ("Bash", 'rm -f "/c/x"/e', "W3"),                                 # literal prefix judged itself
    ("Bash", 'rm -f "' + ROOT.replace("\\", "/") + '"/e', None),
    # brace expansion: no literal folder -> unresolved; mid-path -> the folder is judged
    ("Bash", "mkdir {/c/x/a,b}", "unjudged"),
    ("Bash", "mkdir {a,/c/x/e}", "unjudged"),
    ("Bash", "mkdir -p {1..3}", "unjudged"),
    ("Bash", "touch {a,b}.txt", "unjudged"),
    ("Bash", "mkdir /c/x/{a,b}", "W3"),
    ("Bash", "touch /c/x/f{1..3}", "W3"),
    ("Bash", "mkdir C:\\x\\{a,b}", "W3"),
    ("Bash", "mkdir /c/x*/{a,b}", "unjudged"),                        # glob in the folder part
    ("Bash", "mkdir " + ROOT + "\\{a,b}", None),                       # in-root folder: fine
    ("Bash", "mkdir working/{a,b}", None),
    ("Bash", "echo '{a,b}' > " + ROOT + "\\o.txt", None),              # quoted braces untouched
    ("Bash", "{ echo x; } > C:\\x\\evil.txt", "W3"),                   # a group, not an expansion
    ("Bash", "mkdir C:\\x\\a(b", "unjudged"),                         # unbalanced paren
    ("Bash", "mkdir \"C:\\Program Files (x86)\\evil\"", "W3"),          # balanced parens: judged
    ("Bash", "mkdir \"" + ROOT + "\\dir (x86)\"", None),                 # ... and in-root is fine
    ("Bash", "mkdir 'C:\\x\\a|b'", None),                             # pipe inside: junk
    ("Bash", "mkdir #junk", None),                                     # leading #
    ("Bash", "mkdir (junk", None),                                     # leading (
    # review round 1, item 2: /dev/ is case-sensitive and may not climb
    ("Bash", "echo x > /DEV/stdout", "W3"),
    ("Bash", "echo x > /dev/../c/x/evil.txt", "W3"),
    ("Bash", "echo x > /dev/fd/../../x/evil.txt", "W3"),
    # review round 1, items 3-4: a cd whose effect is not static leaves later relative targets
    # unresolved (in bash, `cd x | cat` does not change the cwd, so ../o.txt really is outside)
    ("Bash", "cd working || true; echo x > ../o.txt", "unjudged"),
    ("Bash", "cd working | cat; echo x > ../o.txt", "unjudged"),
    ("Bash", "cd working & echo x > ../o.txt", "unjudged"),
    ("Bash", "true || cd working; echo x > ../o.txt", "unjudged"),
    ("Bash", "(cd working; echo x > ../o.txt)", "unjudged"),
    ("Bash", "( cd working; echo x > ../o.txt )", "unjudged"),
    ("Bash", "if true; then cd working; fi; echo x > ../o.txt", "unjudged"),
    ("Bash", "if cd working; then echo x > ../o.txt; fi", "unjudged"),
    ("Bash", "for d in a; do cd working; done; echo x > ../o.txt", "unjudged"),
    ("Bash", "if false; then true; else cd working; fi; echo x > ../o.txt", "unjudged"),
    ("Bash", "{ cd working; } ; echo x > ../o.txt", "unjudged"),
    ("Bash", "builtin cd working && echo x > ../o.txt", "unjudged"),
    ("Bash", "command cd working && echo x > ../o.txt", "unjudged"),
    ("Bash", "env cd working && echo x > ../o.txt", "unjudged"),
    ("Bash", "time cd working && echo x > ../o.txt", "unjudged"),
    ("Bash", "! cd working && echo x > ../o.txt", "unjudged"),
    ("Bash", "X=1 cd working && echo x > ../o.txt", "unjudged"),
    ("Bash", "cd C: && echo x > o.txt", "unjudged"),                    # drive-relative
    ("Bash", "cd D:sub && echo x > o.txt", "unjudged"),
    ("PowerShell", "Set-Location HKLM:\\Software; Out-File -FilePath o.txt", "unjudged"),
    ("PowerShell", "Set-Location Env:; Out-File -FilePath o.txt", "unjudged"),
    ("Bash", "builtin cd working && echo x > " + _OUT, "W3"),          # absolute still judged
    ("Bash", "cd working && echo x > ../o.txt && echo y > ../p.txt", None),  # plain chain tracked
    ("Bash", "mkdir /c/x/evil", "W3"),                            # a plain path is still judged
    ("Bash", "cp a /etc", "W3"),                                  # not exactly / - still judged
    # FP3 variable prefix
    ("Bash", "echo x > $D/src", "unjudged"),
    ("Bash", "echo x > ${D}/src", "unjudged"),
    ("Bash", "echo x > /tmp_err_$$", "unjudged"),
    ("Bash", "echo x > out_$1.txt", "unjudged"),
    ("Bash", "copy a %TEMP%\\x.txt", "unjudged"),
    ("Bash", "echo x > `pwd`/src", "unjudged"),
    ("Bash", "echo x > C:\\src", "W3"),                           # the same target, literal, fires
    # FP5 cd tracking
    ("Bash", "cd working && echo x > ../out.log", None),
    ("Bash", "cd working; echo x > ../out.log", None),
    ("Bash", "cd working/a/b\necho x > ../../c.txt", None),
    ("Bash", "cd \"" + ROOT + "\\working\" && mkdir ../new", None),
    ("Bash", "cd " + ROOT.replace("C:\\", "/c/").replace("\\", "/") + "/w && echo x > ../o.txt", None),
    ("Bash", "pushd working && echo x > ../o.txt && popd", None),
    ("PowerShell", "Set-Location working; Out-File -FilePath ..\\o.txt", None),
    ("PowerShell", "Set-Location -Path 'working'; Out-File -FilePath ..\\o.txt", None),
    ("Bash", "cd /d working && echo x > ..\\o.txt", None),
    ("Bash", "cd working && echo x > ../../o.txt", "W3"),          # climbs out of the root via cd
    ("Bash", "cd C:\\x && echo x > o.txt", "W3"),                  # cd outside, then relative write
    ("Bash", "cd /c/x && mkdir sub", "W3"),
    ("Bash", "pushd working && popd && echo x > ../o.txt", "W3"),  # popd restores the root cwd
    ("Bash", "cd working && echo x > " + _OUT, "W3"),               # absolute targets ignore cd
    ("Bash", "cd $D && echo x > out.txt", "unjudged"),              # unresolvable cd
    ("Bash", "cd - && echo x > out.txt", "unjudged"),
    ("Bash", "cd $D && echo x > " + ROOT + "\\ok.txt", None),       # absolute in-root after cd: fine
    ("Bash", "cd $D && echo x > " + _OUT, "W3"),                    # absolute outside after cd: fires
    ("Bash", "cd ~ && echo x > .claude/x.txt", None),               # home: ~/.claude is allowed
]


def run_self_test():
    """[failure, ...] - empty means every case behaved. Exercises evaluate_tool/evaluate_shell over
    TOOL_CASES/SHELL_CASES, a cross-project-root case (proving the root is read per session, not
    baked in), an unjudged $variable target giving no row, and the CLAUDE_WRITE_GUARD=log override."""
    import os as _os
    from hooklib.output import enforcing as _enforcing

    failures = []
    for tool, ti, want in TOOL_CASES:
        ev = {"tool_name": tool, "cwd": ROOT, "tool_input": ti}
        got = bool(evaluate_tool(ev))
        if got != want:
            failures.append("W3 (tool) must %s: %s %r" % ("fire" if want else "not fire", tool, ti))
    if not any(c[2] for c in TOOL_CASES) or not any(not c[2] for c in TOOL_CASES):
        failures.append("W3 (tool) lacks a must-fire or must-not-fire case")

    for cmd, want in SHELL_CASES:
        ev = {"tool_name": "Bash", "cwd": ROOT, "tool_input": {"command": cmd}}
        got = any(r == RULE_ID for r, _ in evaluate_shell(ev))
        if got != want:
            failures.append("W3 (shell) must %s: %r" % ("fire" if want else "not fire", cmd[:90]))
    if not any(c[1] for c in SHELL_CASES) or not any(not c[1] for c in SHELL_CASES):
        failures.append("W3 (shell) lacks a must-fire or must-not-fire case")

    # security fix, 2026-09-28 review (+ colon-form re-review, same date): a PowerShell comma-array's
    # OTHER elements - not just the one the flag itself is glued to - must not bypass W3's root
    # check, in either the space form (`-Path 'a','b'`) or the colon form (`-Path:'a','b'`). Both
    # delete_verb_targets (Remove-Item, hooklib.shell) and ps_write_targets (New-Item -Path, ...)
    # must return every element - cases now live in the counted PS_COMMA_CASES list (moved out of
    # this inline tuple, review finding: an inline case is invisible to write_guard.py's printed
    # self-test count). tool_name=PowerShell is what actually gates comma-splitting.
    for cmd, want in PS_COMMA_CASES:
        ev = {"tool_name": "PowerShell", "cwd": ROOT, "tool_input": {"command": cmd}}
        hits_bypass = evaluate_shell(ev)
        got = any(r == RULE_ID for r, _ in hits_bypass)
        if got != want:
            failures.append("W3 (PS array bypass) must %s: %r" % ("fire" if want else "not fire", cmd[:90]))
        # END TO END: the REAL ENFORCE (no override passed) is {"W2", "W3"} once installed live, so
        # a genuine bypass case must actually deny right now under the default `enforce=True` this
        # module's own decide() takes, not just register a hit
        if want:
            verdict_bypass = decide(ev, [], hits_bypass, enforce=True)
            out_bypass = (verdict_bypass or {}).get("hookSpecificOutput", {})
            if out_bypass.get("permissionDecision") != "deny" \
                    or "W3" not in out_bypass.get("permissionDecisionReason", ""):
                failures.append("W3 (PS array bypass) did not deny when enforced: %r" % cmd[:90])
    if not any(c[1] for c in PS_COMMA_CASES) or not any(not c[1] for c in PS_COMMA_CASES):
        failures.append("W3 PS_COMMA_CASES lacks a must-fire or must-not-fire case")

    # 2026-09-28 PowerShell write-surface audit: Tee-Object (the priority fix) and every other
    # cmdlet/alias added this pass - named-flag and positional forms, tool_name=PowerShell.
    for cmd, want in PS_AUDIT_CASES:
        ev = {"tool_name": "PowerShell", "cwd": ROOT, "tool_input": {"command": cmd}}
        got = any(r == RULE_ID for r, _ in evaluate_shell(ev))
        if got != want:
            failures.append("W3 (PS audit) must %s: %r" % ("fire" if want else "not fire", cmd[:90]))
    if not any(c[1] for c in PS_AUDIT_CASES) or not any(not c[1] for c in PS_AUDIT_CASES):
        failures.append("W3 PS_AUDIT_CASES lacks a must-fire or must-not-fire case")

    # the same audit's cmd.exe-side cases (tool_name=Bash, so `powershell=False` - proves the
    # dispatch fix routes plain copy/move/ren to WRITE_VERBS_LAST_POS, not PS_WRITE_CMDLETS, when
    # the segment is NOT actually PowerShell)
    for cmd, want in CMD_EXE_CASES:
        ev = {"tool_name": "Bash", "cwd": ROOT, "tool_input": {"command": cmd}}
        got = any(r == RULE_ID for r, _ in evaluate_shell(ev))
        if got != want:
            failures.append("W3 (cmd.exe) must %s: %r" % ("fire" if want else "not fire", cmd[:90]))
    if not any(c[1] for c in CMD_EXE_CASES) or not any(not c[1] for c in CMD_EXE_CASES):
        failures.append("W3 CMD_EXE_CASES lacks a must-fire or must-not-fire case")

    # 2026-10-17 false-positive fixes: each class has must-fire and must-not-fire cases
    for tool, cmd, want in FP_CASES:
        ev = {"tool_name": tool, "cwd": ROOT, "tool_input": {"command": cmd}}
        unj = []
        rules_hit = {r for r, _ in evaluate_shell(ev, unj)}
        got = RULE_ID if RULE_ID in rules_hit else ("unjudged" if unj else None)
        if rules_hit - {RULE_ID}:
            failures.append("W3 (FP fix) emitted a non-W3 row %r: %r" % (sorted(rules_hit), cmd[:90]))
        if got != want:
            failures.append("W3 (FP fix) expected %s got %s: %r" % (want, got, cmd[:90]))

    # FP4: permissions.additionalDirectories from project + user settings are allowed roots; the
    # event cwd alone is not. FP6: the Claude app's side-session-notes. Both need real files/env.
    import json as _json
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory(prefix="w3_settings_root_") as _sroot, \
            _tempfile.TemporaryDirectory(prefix="w3_settings_home_") as _shome:
        _home_parent = _os.path.dirname(_shome.rstrip("\\"))
        _user_grants = [
            "C:/granted/sub", "//d/user/granted", "~/projects/x",          # honoured
            "C:/", "/", "/c", "//c/", "C:", "C:/onlyone", "\\\\server\\share",  # refused
            "\\\\?\\C:\\dev\\path", "~", _shome, _home_parent, "rel/dir",
        ]
        _os.makedirs(_os.path.join(_sroot, ".claude"))
        _os.makedirs(_os.path.join(_shome, ".claude"))
        with open(_os.path.join(_sroot, ".claude", "settings.json"), "w", encoding="utf-8") as _f:
            _json.dump({"permissions": {"additionalDirectories": ["C:/projgrant/sub"]}}, _f)
        with open(_os.path.join(_sroot, ".claude", "settings.local.json"), "w", encoding="utf-8") as _f:
            _json.dump({"permissions": {"additionalDirectories": ["C:/projlocal/sub"]}}, _f)
        with open(_os.path.join(_shome, ".claude", "settings.json"), "w", encoding="utf-8") as _f:
            _json.dump({"permissions": {"additionalDirectories": _user_grants}}, _f)
        _saved_env = {k: _os.environ.get(k) for k in ("USERPROFILE", "HOME", "LOCALAPPDATA",
                                                       "CLAUDE_PROJECT_DIR")}
        try:
            _os.environ["USERPROFILE"] = _shome
            _os.environ.pop("CLAUDE_PROJECT_DIR", None)
            _os.environ["LOCALAPPDATA"] = "C:\\fake-local"
            paths.settings_additional_dirs.cache_clear()
            paths.resolve_real_prefix.cache_clear()
            _home_long = paths.home_dir()
            _settings_cases = [
                ("C:\\granted\\sub\\a.txt", False), ("D:\\user\\granted\\a.txt", False),
                (_home_long + "\\projects\\x\\a.txt", False),
                ("C:\\granted\\other\\a.txt", True),         # sibling of a grant
                ("C:\\anything.txt", True),                    # C:/ refused
                ("C:\\onlyone\\a.txt", True),                  # < 2 components refused
                ("\\\\server\\share\\a.txt", True),              # bare UNC share refused
                ("C:\\dev\\path\\a.txt", True),                # \\?\ form refused
                (_home_long + "\\a.txt", True),                 # home itself refused
                (_os.path.join(paths.expand_short_names(_home_parent), "a.txt"), True),  # ancestor
                ("C:\\projgrant\\sub\\a.txt", True),           # project settings.json ignored
                ("C:\\projlocal\\sub\\a.txt", True),           # project settings.local.json ignored
                ("C:\\fake-local\\Claude-Data\\prof1\\side-session-notes\\n.md", False),
                ("C:\\fake-local\\Claude-Data\\prof1\\side-session-notes", False),
                ("C:\\fake-local\\Claude-Data\\prof1\\other\\n.md", True),
                ("C:\\fake-local\\Claude-Data\\side-session-notes\\n.md", True),
            ]
            for _target, _want in _settings_cases:
                _ev = {"tool_name": "Write", "cwd": _sroot, "tool_input": {"file_path": _target}}
                if bool(evaluate_tool(_ev)) != _want:
                    failures.append("W3 (settings/notes) must %s: %s" % (
                        "fire" if _want else "not fire", _target))
            # the event cwd is never granted on its own: root comes from CLAUDE_PROJECT_DIR here
            _os.environ["CLAUDE_PROJECT_DIR"] = _sroot
            _ev_cwd = {"tool_name": "Write", "cwd": "C:\\elsewhere",
                       "tool_input": {"file_path": "C:\\elsewhere\\a.txt"}}
            if not evaluate_tool(_ev_cwd):
                failures.append("W3: the event cwd was allowed without being a root or a grant")
        finally:
            for _k, _v in _saved_env.items():
                if _v is None:
                    _os.environ.pop(_k, None)
                else:
                    _os.environ[_k] = _v
            paths.settings_additional_dirs.cache_clear()
            paths.resolve_real_prefix.cache_clear()

    # an unjudged $variable target: no hit, no log row, never denied (the W3-unresolved row was
    # removed 2026-10-20 - see the module docstring)
    ev_unres = {"tool_name": "Bash", "cwd": ROOT, "tool_input": {"command": "echo x > $DEST/out.txt"}}
    unj_unres = []
    hits_unres = evaluate_shell(ev_unres, unj_unres)
    if hits_unres or log_rows(ev_unres, [], hits_unres, 1):
        failures.append("W3: a $variable target produced a hit or a log row: %r" % (hits_unres,))
    if unj_unres != ["$DEST/out.txt"]:
        failures.append("W3: a $variable target was not left unjudged: %r" % (unj_unres,))
    if decide(ev_unres, [], hits_unres, enforce=True) is not None:
        failures.append("W3: a $variable target denied")

    # enforce-by-default: a real hit denies with no env override
    ev_hit = {"tool_name": "Write", "cwd": ROOT, "tool_input": {"file_path": "C:\\snap-f1\\x.md"}}
    hits = evaluate_tool(ev_hit)
    verdict = decide(ev_hit, hits, [], enforce=True)
    out = (verdict or {}).get("hookSpecificOutput", {})
    if out.get("permissionDecision") != "deny" or "W3" not in out.get("permissionDecisionReason", ""):
        failures.append("W3 did not deny by default on a real hit")
    else:
        import json
        try:
            json.dumps(verdict, ensure_ascii=True).encode("ascii")
        except UnicodeEncodeError:
            failures.append("deny output is not ASCII")

    # a root switch: the SAME absolute path is a violation under one project root and fine under
    # another - proves the allow-list is derived per session, not hard-coded to any one project
    navicat = "C:\\Users\\ShadowEvil\\navicat-clone"
    ev_helbreath_root = {"tool_name": "Write", "cwd": ROOT,
                          "tool_input": {"file_path": navicat + "\\src\\x.ts"}}
    if not evaluate_tool(ev_helbreath_root):
        failures.append("W3: a write into a different project's tree was not flagged when root=" + ROOT)
    ev_navicat_root = {"tool_name": "Write", "cwd": navicat,
                        "tool_input": {"file_path": navicat + "\\src\\x.ts"}}
    if evaluate_tool(ev_navicat_root):
        failures.append("W3: a write inside navicat-clone was flagged when root=navicat-clone")
    ev_navicat_root_outside = {"tool_name": "Write", "cwd": navicat,
                                "tool_input": {"file_path": ROOT + "\\src\\x.ts"}}
    if not evaluate_tool(ev_navicat_root_outside):
        failures.append("W3: a write into the OTHER project's tree was not flagged when root=" + navicat)

    # 2026-10-18: the harness scratchpad is no longer allowed - temp targets (scratchpad, tasks\,
    # bare temp files) are W5's to report (rules/write/w5_temp_dir.py carries those cases)
    import tempfile as _tempfile
    _sid = "5abc868e-b44c-4092-b407-b7e455c39e94"
    _scr = paths.expand_short_names(_os.path.normpath(_tempfile.gettempdir())) + \
        "\\claude\\slug\\" + _sid + "\\scratchpad\\notes.txt"
    if not evaluate_tool({"tool_name": "Write", "cwd": ROOT, "session_id": _sid,
                          "tool_input": {"file_path": _scr}}):
        failures.append("W3: the scratchpad is still allowed at module level (it must not be)")

    # CLAUDE_WRITE_GUARD=log: the entry point must stop denying, but keep logging (log_rows is
    # unaffected - only decide()'s caller withholds `enforce`)
    saved = _os.environ.get("CLAUDE_WRITE_GUARD")
    try:
        _os.environ.pop("CLAUDE_WRITE_GUARD", None)
        if not _enforcing():
            failures.append("enforcing() was False with CLAUDE_WRITE_GUARD unset")
        _os.environ["CLAUDE_WRITE_GUARD"] = "log"
        if _enforcing():
            failures.append("enforcing() was True with CLAUDE_WRITE_GUARD=log")
        if decide(ev_hit, hits, [], enforce=_enforcing()) is not None:
            failures.append("W3 denied under CLAUDE_WRITE_GUARD=log")
        rows = log_rows(ev_hit, hits, [], 1)
        if not rows:
            failures.append("W3 stopped logging under CLAUDE_WRITE_GUARD=log (it must not)")
        # 2026-09-28: the week-long log-only rollout needs root/cwd/target on every row, not just
        # the human-readable sample text, so the hits can be grouped/queried afterwards
        elif not all(k in rows[0] for k in ("root", "cwd", "target")):
            failures.append("W3 log_rows is missing root/cwd/target")
        elif rows[0]["root"] != ROOT or rows[0]["cwd"] != ev_hit.get("cwd") \
                or rows[0]["target"] != ev_hit["tool_input"]["file_path"]:
            failures.append("W3 log_rows root/cwd/target do not match the event")
        _os.environ["CLAUDE_WRITE_GUARD"] = "LOG"  # case-insensitive
        if _enforcing():
            failures.append("enforcing() is case-sensitive on CLAUDE_WRITE_GUARD")
    finally:
        if saved is None:
            _os.environ.pop("CLAUDE_WRITE_GUARD", None)
        else:
            _os.environ["CLAUDE_WRITE_GUARD"] = saved

    for msg in MSG.values():
        try:
            msg.encode("ascii")
        except UnicodeEncodeError:
            failures.append("a W3 message is not ASCII")
        if "helbreath" in msg.lower() or "claude.md" in msg.lower():
            failures.append("a W3 message names a project-specific doc")

    # 8.3 root fix (2026-09-28 review): paths.project_root()/home_dir() never expanded short (8.3)
    # name segments on the ROOT side of a comparison, even though resolve() always expands them on
    # the TARGET side - a root reported in 8.3 form (SHADOW~1) then never prefix-matched its own,
    # correctly-expanded targets, so an ordinary in-root write looked like it fell outside every
    # allowed root. Proven here with a REAL short name from the OS (GetShortPathNameW - the exact
    # inverse of expand_short_names' own GetLongPathNameW call), not a hand-typed guess at 8.3
    # syntax - this test is deliberately NOT pre-expanding the root itself, unlike every other case
    # in this file, since that is exactly what used to go wrong.
    import ctypes as _ctypes
    import tempfile as _tempfile

    def _short_form(path):
        try:
            buf = _ctypes.create_unicode_buffer(4096)
            n = _ctypes.windll.kernel32.GetShortPathNameW(path, buf, 4096)
            return buf.value if n and n < 4096 else None
        except Exception:
            return None

    with _tempfile.TemporaryDirectory(prefix="w3_8dot3_long_name_") as _long_root:
        _short_root = _short_form(_long_root)
        if _short_root and _short_root.lower() != _long_root.lower():
            _ev83 = {"tool_name": "Write", "cwd": _short_root,
                      "tool_input": {"file_path": _os.path.join(_long_root, "inside.txt")}}
            if evaluate_tool(_ev83):
                failures.append(
                    "W3: a write inside an 8.3-SHORT-FORM root must not fire (root not expanded?)")
        # else: short-name creation is disabled on this volume - nothing to prove either way here,
        # not treated as a failure (this machine's own %TEMP% does generate them, so it is covered)

    # junction/symlink fix (2026-09-28 review): an NTFS junction (`mklink /J`, no admin needed)
    # INSIDE the allowed root can make a write that LOOKS like it stays in-root actually escape
    # outside it - W3's own mirror of the reviewer's primary scenario (see w4_review_stamp.py's own
    # junction case, an unguarded-looking path landing in a GUARDED tree; this is the opposite
    # direction, an in-root-looking path landing OUTSIDE). A REAL junction, not a hand-typed guess,
    # proves paths.resolve()'s resolve_real_prefix step actually follows it.
    import subprocess as _subprocess

    with _tempfile.TemporaryDirectory(prefix="w3_junction_root_") as _jroot, \
            _tempfile.TemporaryDirectory(prefix="w3_junction_outside_") as _joutside:
        _junction_dir = _os.path.join(_jroot, "looks_in_root")
        _mk = _subprocess.run(["cmd", "/c", "mklink", "/J", _junction_dir, _joutside],
                               capture_output=True, text=True)
        if _mk.returncode == 0:
            _ev_j = {"tool_name": "Write", "cwd": _jroot,
                      "tool_input": {"file_path": _os.path.join(_junction_dir, "escaped.txt")}}
            if not evaluate_tool(_ev_j):
                failures.append(
                    "W3: a write through an in-root junction that escapes outside must fire (junction not resolved?)")
        # else: mklink unavailable in this environment - not provable either way, not a failure

    # garbage-realpath-shape fix (2026-09-28 hotfix): a mis-extracted shell "target" (a nested-quote
    # nightmare inside a `python -c` heredoc, misread as a redirect by find_redirects - real
    # transcript, helbreath_war session 01eefde5...) does not make os.path.realpath raise; it can
    # hand back a nonsense string that shares no prefix with any real root, turning a harmless
    # extraction into a false W3 hit. paths.resolve_real_prefix must trust realpath's own output
    # only when it is still drive-letter or UNC shaped, else fall back to the pre-realpath value.
    _real_realpath = _os.path.realpath
    paths.resolve_real_prefix.cache_clear()
    try:
        _os.path.realpath = lambda p: "enUS\": \"{} -> {}\"}'"  # the exact garbage shape observed
        _fallback_target = _os.path.join(ROOT, "still_in_root.txt")
        _got = paths.resolve_real_prefix(_fallback_target)
        if _got != _fallback_target:
            failures.append(
                "W3: resolve_real_prefix must fall back to the pre-realpath value when realpath's "
                "own output is not a plausible absolute Windows path (got %r)" % (_got,))
    finally:
        _os.path.realpath = _real_realpath
        paths.resolve_real_prefix.cache_clear()

    # must-not-fire: with realpath behaving normally, a genuinely in-root write must still not fire,
    # proving the guard above does not also swallow realpath's real, correct output.
    _ev_normal = {"tool_name": "Write", "cwd": ROOT,
                  "tool_input": {"file_path": _os.path.join(ROOT, "ordinary.txt")}}
    if evaluate_tool(_ev_normal):
        failures.append("W3: an ordinary in-root write must not fire (realpath-shape guard false positive?)")

    return failures
