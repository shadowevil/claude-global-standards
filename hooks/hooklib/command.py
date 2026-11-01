"""Command: a small, forgiving parser for the shell command line of a Bash or PowerShell tool call.

It is not a shell. It splits a command into SEGMENTS (simple commands) at ; && || | & and newlines,
outside quotes, drops heredoc and here-string bodies, resolves each segment's PROGRAM past env
assignments, wrappers (timeout, time, nice, env, call, start, Start-Process) and the PowerShell call
operator, and looks inside `cmd /c "..."`, `bash -c "..."` and `powershell -Command "..."`. Rules
(rules/execute/) read `command.segments`. It never raises on odd input: an unclosed quote ends at the
end of the text, and a command over MAX_CHARS is not parsed (`truncated`)."""

import re

MAX_CHARS = 200000
MAX_DEPTH = 3
MAX_SEGMENTS = 5000

_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(?P<q>[\"']?)(?P<d>[A-Za-z_][\w-]*)(?P=q)")
_HERESTRING_OPEN = re.compile(r"@([\"'])\s*$")
_KEYWORDS = frozenset(("do", "then", "else", "elif", "if", "while", "until", "!", "{", "}", "time", "exec", "command", "nohup", "sudo", "call", "builtin", "noglob"))
_LOOP_OPEN = frozenset(("for", "while", "until", "foreach"))
_EXT = (".exe", ".cmd", ".bat", ".ps1", ".com")
_SHELL_C = {"bash": ("-c",), "sh": ("-c",), "zsh": ("-c",), "dash": ("-c",),
    "powershell": ("-c", "-command"), "pwsh": ("-c", "-command")}


class Segment(object):
    """One simple command. program is the resolved lowercase base name ("" if none); args are the
    tokens after it; env maps leading NAME=value assignments; sep_before / sep_after are the
    separators that joined it to its neighbours ("", ";", "&&", "||", "|", "&", "\\n")."""

    __slots__ = ("text", "tokens", "program", "args", "env", "sep_before", "sep_after", "heredoc", "in_loop")

    def __init__(self, tokens, sep_before, in_loop):
        self.tokens = tokens
        self.text = " ".join(tokens)
        self.program = ""
        self.args = []
        self.env = {}
        self.sep_before = sep_before
        self.sep_after = ""
        self.heredoc = any(t.startswith("<<") and not t.startswith("<<<") for t in tokens)
        self.in_loop = in_loop
        self._resolve()

    def _resolve(self):
        toks = self.tokens
        start_process = False
        i = 0
        n = len(toks)
        while i < n:
            t = toks[i]
            low = t.lower()
            if low in _KEYWORDS:
                i += 1
            elif _ASSIGN.match(t):
                name, _eq, value = t.partition("=")
                self.env[name] = value
                i += 1
            elif low == "timeout":
                i += 1
                while i < n and toks[i].startswith("-"):
                    i += 1
                i += 1  # the duration
            elif low in ("nice", "stdbuf", "env"):
                i += 1
                while i < n and (toks[i].startswith("-") or _ASSIGN.match(toks[i])):
                    if _ASSIGN.match(toks[i]):
                        name, _eq, value = toks[i].partition("=")
                        self.env[name] = value
                    i += 1
            elif low in ("start", "start-process"):
                start_process = low == "start-process"
                i += 1
                while i < n and (toks[i].startswith("-") or toks[i].startswith("/") and len(toks[i]) <= 8):
                    opt = toks[i].lower()
                    if opt == "-filepath":
                        i += 1
                        break
                    i += 2 if opt in _START_VALUE_OPTIONS else 1
            elif t.startswith("$") and not t.startswith("$(") and i == 0 and _ps_assignment_rest(toks) is not None:
                toks = _ps_assignment_rest(toks)  # `$p = Start-Process ...`, `$out = & tool ...`
                n = len(toks)
                i = 0
            else:
                break
        if i >= n:
            return
        self.program = _base_name(toks[i])
        args = _without_redirects(toks[i + 1:])
        self.args = _start_process_arguments(args) if start_process else args


_START_VALUE_OPTIONS = frozenset(("-workingdirectory", "-redirectstandardoutput", "-redirectstandarderror",
    "-redirectstandardinput", "-verb", "-windowstyle", "-credential", "-environment"))
_ARGUMENT_LIST = frozenset(("-argumentlist", "-args", "-arg"))


def _ps_assignment_rest(toks):
    """The tokens after a PowerShell `$var =` / `$var +=` / `$var=value` prefix when what follows is a command
    (Start-Process, or a program with arguments); None otherwise. A lone value (`$msb = 'C:\\x\\MSBuild.exe'`)
    is a string, not a call, and the tokenizer has dropped its quotes, so a single token is never a command."""
    first = toks[0]
    rest = None
    if len(toks) > 1 and toks[1] in ("=", "+="):
        rest = toks[2:]
    else:
        eq = first.find("=")
        if 1 < eq:
            head = first[eq + 1:]
            rest = ([head] if head else []) + toks[1:]
    if rest and (len(rest) >= 2 or rest[0].lower() in ("start-process", "start")):
        return rest
    return None


def _start_process_arguments(args):
    """Start-Process takes its program's arguments as one string (-ArgumentList "a.sln /m:2", or the second
    positional); split it so the rules see the program's real arguments."""
    out = []
    k = 0
    n = len(args)
    taken = False
    while k < n:
        a = args[k]
        if a.lower() in _ARGUMENT_LIST and k + 1 < n:
            out.extend(x for x in re.split(r"[\s,]+", args[k + 1].strip()) if x)
            taken = True
            k += 2
        elif not taken and k == 0 and not a.startswith("-"):
            out.extend(x for x in re.split(r"[\s,]+", a.strip()) if x)
            taken = True
            k += 1
        else:
            out.append(a)
            k += 1
    return out


_REDIRECT = re.compile(r"^(?:\d*>>?|\d*<|&>>?|\d*>&)$")  # a bare operator: its target is the next token
_REDIRECT_WITH_TARGET = re.compile(r"^(?:\d*>>?|\d*<|&>>?|\d*>&)\S")  # >file  2>&1  <<HEREDOC  <in.txt


def _without_redirects(args):
    """Arguments minus redirections (`> out`, `2>&1`, `< in`, `<<HEREDOC`), which are not the program's arguments."""
    out = []
    skip = False
    for a in args:
        if skip:
            skip = False
        elif _REDIRECT.match(a):
            skip = True
        elif not _REDIRECT_WITH_TARGET.match(a):
            out.append(a)
    return out


def _base_name(token):
    name = token.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower().strip("\"'")
    for ext in _EXT:
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


class Command(object):
    """raw text, segments, heredoc_count (bodies dropped), truncated (too long to parse)."""

    def __init__(self, raw):
        self.raw = raw if isinstance(raw, str) else ""
        self.segments = []
        self.heredoc_count = 0
        self.truncated = False


def parse(text):
    cmd = Command(text)
    if len(cmd.raw) > MAX_CHARS:
        cmd.truncated = True
        return cmd
    body, cmd.heredoc_count = _strip_heredocs(cmd.raw)
    cmd.segments = _segments(body, 0)
    return cmd


def _strip_heredocs(text):
    """Replace each heredoc / here-string opener by a `<<HEREDOC` / `@HERESTRING` token and drop its body."""
    lines = text.split("\n")
    out = []
    count = 0
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        i += 1
        m = _HEREDOC.search(line)
        if m:
            delim = m.group("d")
            line = line[:m.start()] + " <<HEREDOC " + line[m.end():]
            while i < n and lines[i].strip() != delim and lines[i].strip("\t") != delim:
                i += 1
            i += 1  # the terminator line (or past the end when unclosed)
            count += 1
        else:
            h = _HERESTRING_OPEN.search(line)
            while h:  # a closing line can carry another opener:  '@, @'
                quote = h.group(1)
                line = line[:h.start()] + " @HERESTRING"
                while i < n and not lines[i].startswith(quote + "@"):
                    i += 1
                if i < n:
                    line += " " + lines[i][2:]  # what follows the closing '@ (a pipe, a call, the next argument)
                i += 1
                count += 1
                h = _HERESTRING_OPEN.search(line)
        out.append(line)
    return "\n".join(out), count


def _segments(text, depth):
    segs = []
    tokens = []
    cur = []
    has_cur = False
    quote = ""
    sep_before = ""
    loop_depth = 0
    i = 0
    n = len(text)

    def end_token():
        nonlocal cur, has_cur
        if has_cur:
            tokens.append("".join(cur))
        cur = []
        has_cur = False

    def end_segment(sep):
        nonlocal tokens, sep_before, loop_depth
        end_token()
        if tokens:
            first = tokens[0].lower()
            in_loop = loop_depth > 0
            if first in _LOOP_OPEN:
                loop_depth += 1
            seg = Segment(tokens, sep_before, in_loop)
            seg.sep_after = sep
            if first == "done":
                loop_depth = max(0, loop_depth - 1)
            segs.extend(_expand(seg, depth))
            sep_before = sep
        elif sep:
            sep_before = sep
        tokens = []

    while i < n and len(segs) < MAX_SEGMENTS:
        c = text[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < n and text[i + 1] == '"':
                cur.append('"')
                i += 2
                continue
            if c == "`" and quote == '"' and i + 1 < n:
                cur.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
            else:
                cur.append(c)
            i += 1
            continue
        if c in "'\"":
            quote = c
            has_cur = True
            i += 1
        elif c in " \t\r":
            end_token()
            i += 1
        elif c == "\n":
            end_segment("\n")
            i += 1
        elif (c == "\\" or c == "`") and text[i + 1:i + 2] in ("\n", "\r"):
            end_token()
            i += 2 if text[i + 1] == "\n" else 3  # a line continuation joins the lines
        elif c == "#" and not has_cur:
            j = text.find("\n", i)
            i = n if j < 0 else j  # a comment runs to the end of the line
        elif c == ";":
            end_segment(";")
            i += 1
        elif c == "|":
            if text[i + 1:i + 2] == "|":
                end_segment("||")
                i += 2
            else:
                end_segment("|")
                i += 1
        elif c == "&":
            nxt = text[i + 1:i + 2]
            prev = text[i - 1] if i > 0 else ""
            if nxt == "&":
                end_segment("&&")
                i += 2
            elif nxt == ">" or prev in ("<", ">"):
                cur.append(c)  # 2>&1, &>file
                has_cur = True
                i += 1
            elif not has_cur and (not tokens or tokens[-1].endswith("=")) and nxt in (" ", "\t", "\"", "'", "$"):
                i += 1  # the PowerShell call operator, at the start of a command or after `=`: $r = & 'C:\x\tool.exe' args
            else:
                end_segment("&")
                i += 1
        elif c == "$" and text[i + 1:i + 2] == "(":
            end_token()
            end_segment("")
            i += 2
        elif c in "()":
            end_segment("")
            i += 1
        elif c in "{}" and not has_cur and text[i + 1:i + 2] in (" ", "\t", "\n", ""):
            end_segment("")
            i += 1
        else:
            cur.append(c)
            has_cur = True
            i += 1
    end_segment("")
    return segs


def _expand(seg, depth):
    """A shell wrapper (cmd /c, bash -c, powershell -Command) is replaced by the segments of the command it runs."""
    if depth >= MAX_DEPTH or not seg.program:
        return [seg]
    inner = None
    if seg.program == "cmd":
        low = [a.lower() for a in seg.args]
        for flag in ("/c", "/k"):
            if flag in low:
                rest = seg.args[low.index(flag) + 1:]
                inner = rest[0] if len(rest) == 1 else " ".join(rest)
                break
    elif seg.program in _SHELL_C:
        flags = _SHELL_C[seg.program]
        for k, a in enumerate(seg.args):
            if a.lower() in flags and k + 1 < len(seg.args):
                inner = seg.args[k + 1]
                break
    if inner is None:
        return [seg]
    inner_body, _count = _strip_heredocs(inner)
    subs = _segments(inner_body, depth + 1)
    if not subs:
        return [seg]
    if seg.program == "cmd":
        for sub in subs:  # in cmd.exe a single & runs the next command after this one, it is not a background job
            sub.sep_after = ";" if sub.sep_after == "&" else sub.sep_after
            sub.sep_before = ";" if sub.sep_before == "&" else sub.sep_before
    subs[0].sep_before = seg.sep_before
    subs[-1].sep_after = seg.sep_after
    return subs
