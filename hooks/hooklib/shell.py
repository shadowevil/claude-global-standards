"""Shell-command segmentation and tokenizing, without a real shell. Ported verbatim from the
origin project hook (split_top_level, shell_tokens, mask_quotes) - nothing here
is project-specific.
"""
import re
import shlex

ENV_ASSIGN = re.compile(r"^\s*(?:[A-Za-z_]\w*=\S*\s+)+")
REDIRECT_TOKEN = re.compile(r"^\d*(>>?|<)")  # 2>nul, 2>&1, >>out.log, <in.txt, ... - never a target
HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")


def split_top_level(cmd):
    """[segment, ...] - see split_top_level_seps, which also reports each segment's separators."""
    return [seg for seg, _before, _after in split_top_level_seps(cmd)]


def split_top_level_seps(cmd):
    """[(segment, sep_before, sep_after), ...] - sep is the separator text (';', '&&', '||', '|',
    '&', newline, '{', '}') or None at either end of the command. Empty segments are dropped and
    their separators folded into the neighbour's. Otherwise identical to the original splitter:
    cmd split into segments at an unquoted ; && || | & { } or a bare newline (a doubled ;/&/|
    counts as one boundary). `{`/`}` are split too so a write/delete nested in a PowerShell
    `if (...) { ... }`/`foreach (...) { ... }`/scriptblock, or a bash `{ ...; }` group or function
    body, is not hidden - this is a flat cut, not real brace-depth parsing, so it can over-split
    (e.g. a literal `{`/`}` inside an unquoted argument), which only ever means an argument gets
    checked as its own segment, never that a real write goes unseen. A segment that opens a
    heredoc (`<<EOF`, `<<-'EOF'`, ...) suspends splitting - the body up to and including its
    terminator line is swallowed whole, not scanned as commands, so text inside one (a file's
    contents, an embedded script, ...) is never mistaken for a typed write."""
    segments = []
    seps = [None]
    buf = []
    i = 0
    n = len(cmd)
    quote = None
    heredoc_end = None
    while i < n:
        if heredoc_end is not None:
            eol = cmd.find("\n", i)
            line = cmd[i:eol if eol >= 0 else n]
            if line.strip() == heredoc_end:
                heredoc_end = None
            i = (eol + 1) if eol >= 0 else n
            continue
        c = cmd[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"' and i + 1 < n:
                buf.append(cmd[i + 1])
                i += 1
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c in (";", "&", "|", "\n", "{", "}"):
            j = i + 1
            if c != "\n" and j < n and cmd[j] == c:
                j += 1
            seg = "".join(buf)
            segments.append(seg)
            seps.append(cmd[i:j])
            m = HEREDOC_START.search(seg)
            if m:
                heredoc_end = m.group(2)
            buf = []
            i = j
            continue
        buf.append(c)
        i += 1
    segments.append("".join(buf))
    seps.append(None)
    out = []
    pending = None
    for k, seg in enumerate(segments):
        before = seps[k] if pending is None else pending
        if not seg.strip():
            pending = before if before is not None else seps[k + 1]
            continue
        pending = None
        out.append([seg, before, seps[k + 1]])
    for k in range(len(out) - 1):
        out[k][2] = out[k][2] if out[k][2] is not None else out[k + 1][1]
    return [tuple(x) for x in out]


def expand_ps_commas(s):
    """s with every UNQUOTED comma - and an @( ... ) array-literal wrapper around them, if present -
    replaced by a space, so a PowerShell comma-separated argument list ('a','b' / "a", "b" / a,b /
    @('a','b')) tokenizes into one element per token. Without this, shlex glues the next element's
    leading comma onto it (live false positive, 2026-09-28: `Remove-Item -LiteralPath
    'C:\\a\\x.png','C:\\a\\y.png'` read its second path as `,'C:\\a\\y.png'`, comma still attached,
    and flagged it as a relative path). A comma INSIDE a quoted argument is left alone - only
    quote-tracking (the same state machine as mask_quotes/split_top_level) decides "unquoted"."""
    out = []
    quote = None
    at_depth = 0
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if quote:
            out.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "@" and i + 1 < n and s[i + 1] == "(":
            at_depth += 1
            out.append("  ")
            i += 2
            continue
        if c == ")" and at_depth > 0:
            at_depth -= 1
            out.append(" ")
            i += 1
            continue
        if c == ",":
            out.append(" ")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def shell_tokens(segment, powershell=False):
    """segment's words with quotes stripped, redirections (2>nul, >>out.log, ...) dropped, or None
    if it does not tokenize (unbalanced quotes - fails open: the segment is skipped rather than
    guessed at). `powershell` gates expand_ps_commas: a Bash filename may legitimately contain a
    literal comma (`rm x,y` is ONE target named "x,y"), so comma-splitting only runs for a segment
    that is actually PowerShell - callers pass this from the event's own tool_name (bug found in
    review, 2026-09-28: `rm C:\\...\\x,y` was misread as two targets and `y` flagged as relative)."""
    s = ENV_ASSIGN.sub("", segment, count=1).strip()
    s = re.sub(r"^&\s*", "", s)  # a leading PowerShell call operator
    if powershell:
        s = expand_ps_commas(s)
    try:
        toks = shlex.split(s, posix=False)  # posix=False: never eats backslashes in C:\... paths
    except ValueError:
        return None
    out = []
    for t in toks:
        if REDIRECT_TOKEN.match(t):
            continue
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
            t = t[1:-1]
        out.append(t)
    return out


def mask_quotes(s):
    """s with every quoted character (including the quote marks) replaced by 'x', same length -
    so a regex can be run against it to find only UNQUOTED matches, then re-sliced out of the
    original s by span (masking never changes any character's position)."""
    out = list(s)
    quote = None
    for i, c in enumerate(s):
        if quote:
            if c == quote:
                quote = None
            else:
                out[i] = "x"
            continue
        if c in ("'", '"'):
            quote = c
            out[i] = "x"
    return "".join(out)


# ------------------------------------------------------------------ delete-verb target extraction
#
# Shared between rules/write (a delete is a write to the filesystem too - W3 judges WHERE it lands)
# and rules/delete (W2 judges HOW it is written - literal, fully-qualified). Neither module
# duplicates this; both call delete_verb_targets().

DELETE_VERBS = {"rm", "rmdir", "remove-item", "ri", "del", "erase", "rd"}
# PowerShell/cmd flags that take a following value which is not itself a delete target. NOT every
# Remove-Item flag is here - only ones whose value must be consumed and discarded; a flag whose
# value IS the target list (e.g. -LiteralPath) must stay off this set so its value reaches targets.
VALUE_FLAGS = {"-erroraction", "-filter", "-include", "-exclude", "-credential", "-stream"}
CMD_FLAG = re.compile(r"^/[A-Za-z](?::\S*)?$")  # a cmd.exe-style /s /q /f switch, not a posix path
# a PowerShell `-Flag:value` colon-attached form (`-LiteralPath:'C:\x'`) - shlex/expand_ps_commas
# glue the flag NAME and its FIRST array element into one token, so this has to be split back apart
# before the usual "starts with -" flag test ever sees it, or the whole thing (flag AND value) gets
# misread as an opaque flag and the value is silently lost (security fix, 2026-09-28 review).
COLON_FLAG_VALUE = re.compile(r"^(-[a-z]+):(.+)$", re.I)
PS_BOOL_LITERAL = re.compile(r"^\$(true|false)$", re.I)  # `-Force:$true` - a switch, never a target
VAR_OR_SUBST = re.compile(r"\$\{|\$env:|\$\(|`|\$[A-Za-z_]", re.I)
# recursive flags: a single-dash cluster built only from r/f/v/i and containing r (-r, -rf, -fr, -Rf...);
# a PowerShell -Recurse or an accepted abbreviation (-Rec, -Recu, ...); --recursive; cmd.exe /s
BASH_RECURSIVE = re.compile(r"^-[rfvi]*r[rfvi]*$", re.I)
PS_RECURSIVE = re.compile(r"^-rec(u(r(s(e)?)?)?)?$", re.I)
LONG_RECURSIVE = re.compile(r"^--recursive$", re.I)
CMD_RECURSIVE = re.compile(r"^/s$", re.I)


def delete_verb_targets(seg, powershell=False):
    """(verb, targets, is_recursive) for an rm/rmdir/Remove-Item/del/erase/rd segment - targets is
    the RAW list (possibly empty for a pipe-fed/no-arg delete), unfiltered by any rule's own
    legality check, so every rule that cares about a delete target sees every one of them. None if
    this segment is not a delete verb at all (git rm is exempt - callers handle that, along with
    `find -delete`/`find -exec rm`, neither of which has one static path for this to return).
    `powershell` is passed straight through to shell_tokens - see its docstring; it also gates the
    colon-form fix below (bash has no `-flag:value` syntax).

    Security fix, 2026-09-28 review: `-LiteralPath:'C:\\a','C:\\b'` (colon, not space, before the
    first value) used to be swallowed whole as an opaque flag token - `t.startswith("-")` matched
    the ENTIRE `-LiteralPath:C:\\a` string, so it went straight into `flags` and its value never
    reached `targets` at all, regardless of array position; only a LATER comma-split element (a
    separate token with no leading `-`) ever made it into `targets`. `Remove-Item
    -LiteralPath:'C:\\x\\evil.txt','<in-root>\\a'` therefore gave 0 hits - the dangerous first
    element was discarded, not merely misjudged. Fixed by splitting a colon-attached flag+value
    apart FIRST: a value-consuming flag (VALUE_FLAGS) or a boolean literal (`-Force:$true`) is
    still discarded, but any other colon value is a real target, and - matching the space form -
    every comma-split element that follows it, up to the next flag, is one too."""
    toks = shell_tokens(seg, powershell)
    if not toks or toks[0].lower() not in DELETE_VERBS:
        return None
    flags = []
    targets = []
    i = 1
    while i < len(toks):
        t = toks[i]
        tl = t.lower()
        if tl == "--":
            i += 1
            continue
        m = COLON_FLAG_VALUE.match(t) if powershell else None
        if m:
            base_flag = m.group(1)
            value = m.group(2)
            # unlike a standalone token, shell_tokens' own whole-token quote strip never runs on
            # this value - it is still glued to "-flag:" when shlex sees it, so t[0] is '-', not a
            # quote. Strip a matching pair here, the same rule shell_tokens applies to every other
            # token (bug found alongside the fix above: an unstripped leading quote made
            # `-LiteralPath:'C:\a'` fail WIN_ABS's "is this absolute" check and misreport as
            # "a relative path", even once the value was no longer being discarded outright).
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            flags.append(base_flag)
            if base_flag.lower() in VALUE_FLAGS or PS_BOOL_LITERAL.match(value):
                i += 1
                continue
            targets.append(value)
            j = i + 1
            while j < len(toks) and not toks[j].startswith("-") and not CMD_FLAG.match(toks[j]):
                targets.append(toks[j])
                j += 1
            i = j
            continue
        if t.startswith("-") or CMD_FLAG.match(t):
            flags.append(t)
            i += 2 if tl in VALUE_FLAGS else 1
            continue
        targets.append(t)
        i += 1
    is_recursive = any(BASH_RECURSIVE.match(f) or PS_RECURSIVE.match(f) or LONG_RECURSIVE.match(f)
                        or CMD_RECURSIVE.match(f) for f in flags)
    return toks[0], targets, is_recursive


# ------------------------------------------------------------------ working-directory tracking
#
# 2026-10-17 (FP5, the largest W3 false-positive class): `cd sub && echo x > ../out.log` resolved
# `../out.log` against the event's cwd, not `sub`, so an in-root write looked like it climbed out.
# cwd_per_segment() follows cd / pushd / popd / Set-Location / chdir through one command line.

CD_VERBS = {"cd", "chdir", "pushd", "set-location", "sl", "push-location"}
# a cd behind one of these runs conditionally or in another scope - its effect is not static
CD_PREFIX_WORDS = {"then", "do", "else", "elif", "builtin", "command", "env", "time", "!", "{",
                   "if", "while", "until", "exec", "nohup", "sudo"}
ASSIGNMENT_WORD = re.compile(r"^[A-Za-z_]\w*=")
# separators that make a cd conditional or run it in a subshell/background job
UNTRACKABLE_SEPS = {"||", "|", "&", "{", "}"}
COND_OPEN = {"if", "while", "until", "for", "case", "select"}
COND_CLOSE = {"fi", "done", "esac"}
DRIVE_RELATIVE = re.compile(r"^[A-Za-z]:(?![\\/])")
PROVIDER_PATH = re.compile(r"^[A-Za-z][\w.-]+::?")  # HKLM:\, Env:, Cert:, Registry::...
POPD_VERBS = {"popd", "pop-location"}
CD_PATH_FLAGS = {"-path", "-literalpath"}
UNRESOLVED_CWD = object()  # sentinel: a cd happened whose argument cannot be resolved statically
DYNAMIC_ARG = re.compile(r"\$|`|%[A-Za-z_][\w()]*%")


def _cd_argument(toks):
    """The directory argument of a cd-like token list, '' for a bare cd, or None when it cannot be
    picked out (several positionals, an unknown flag form)."""
    args = []
    i = 1
    while i < len(toks):
        t = toks[i]
        tl = t.lower()
        if tl in ("--", "-p", "-l", "/d"):
            i += 1
            continue
        if tl in CD_PATH_FLAGS:
            i += 1
            continue
        m = re.match(r"^(-path|-literalpath):(.+)$", t, re.I)
        if m:
            args.append(m.group(2))
            i += 1
            continue
        if t.startswith("-") and t != "-":
            return None
        args.append(t)
        i += 1
    if not args:
        return ""
    if len(args) > 1:
        return None
    a = args[0]
    if len(a) >= 2 and a[0] == a[-1] and a[0] in ("'", '"'):
        a = a[1:-1]
    return a


def _cd_context(seg):
    """'cd' for a plain leading cd/pushd/popd, 'hidden' for one behind a prefix word or a NAME=value
    assignment (or opened by a paren), None when the segment does not change directory."""
    words = seg.strip().split()
    if not words:
        return None
    if words[0].startswith("("):
        stripped = words[0].lstrip("(")
        if stripped.lower() in CD_VERBS or stripped.lower() in POPD_VERBS or \
                (not stripped and len(words) > 1 and words[1].lower() in CD_VERBS | POPD_VERBS):
            return "hidden"
    k = 0
    while k < len(words) and (words[k].lower() in CD_PREFIX_WORDS or ASSIGNMENT_WORD.match(words[k])):
        k += 1
    if k < len(words) and (words[k].lower() in CD_VERBS or words[k].lower() in POPD_VERBS):
        return "hidden" if k > 0 else "cd"
    return None


def cwd_per_segment(segments, start_cwd, home, to_windows, powershell=False, seps=None):
    """[effective_cwd, ...], one per segment: the directory a relative target in that segment
    resolves against. None means "the event's own cwd" (nothing changed it); UNRESOLVED_CWD means a
    cd ran whose argument is not static (a $variable, `cd -`, ...), so a relative target after it
    cannot be judged. `to_windows` maps an MSYS path (hooklib.paths.msys_to_windows - passed in so
    this module stays free of path policy). Never raises; any surprise yields UNRESOLVED_CWD."""
    import os
    out = []
    cur = None
    stack = []
    cond_depth = 0
    paren_depth = 0
    for idx, seg in enumerate(segments):
        out.append(cur)
        masked = mask_quotes(seg)
        ctx = _cd_context(seg)
        first = seg.strip().split()[0].lower() if seg.strip() else ""
        before, after = (seps[idx] if seps else (None, None))
        untrackable = (cond_depth > 0 or paren_depth > 0 or ctx == "hidden"
                       or before in UNTRACKABLE_SEPS or after in UNTRACKABLE_SEPS)
        if first in COND_OPEN:
            cond_depth += 1
        elif first in COND_CLOSE and cond_depth > 0:
            cond_depth -= 1
        paren_depth = max(0, paren_depth + masked.count("(") - masked.count(")"))
        if ctx is None:
            continue
        if untrackable:
            cur = UNRESOLVED_CWD
            continue
        try:
            toks = shell_tokens(seg.strip().lstrip("("), powershell)
        except Exception:
            toks = None
        if not toks:
            continue
        verb = toks[0].lower()
        if verb in POPD_VERBS:
            cur = stack.pop() if stack else UNRESOLVED_CWD
            continue
        if verb not in CD_VERBS:
            continue
        if verb in ("pushd", "push-location"):
            stack.append(cur)
        arg = _cd_argument(toks)
        base = start_cwd if cur is None else cur
        if arg is None or arg == "-" or base is UNRESOLVED_CWD or DYNAMIC_ARG.search(arg) \
                or DRIVE_RELATIVE.match(arg) or PROVIDER_PATH.match(arg):
            cur = UNRESOLVED_CWD
            continue
        if arg == "" or arg == "~" or arg.startswith("~/") or arg.startswith("~\\"):
            if not home:
                cur = UNRESOLVED_CWD
                continue
            arg = home + arg[1:] if arg else home
        elif arg.startswith("~"):
            cur = UNRESOLVED_CWD  # ~user
            continue
        p = to_windows(arg)
        if not re.match(r"^[A-Za-z]:\\", p) and not p.startswith("\\\\"):
            if not base:
                cur = UNRESOLVED_CWD
                continue
            p = os.path.join(to_windows(base), p)
        cur = os.path.normpath(p)
    return out
