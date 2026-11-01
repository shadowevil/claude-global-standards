"""X3: an interpreter that reads its program from stdin. Global CLAUDE.md "Working habits": scripts go in a
file; never `python -` or a heredoc, which can hang on stdin and spin a core for hours (measured
2026-10-31: three such leftovers). Runs globally under stdin_guard.py and carries no project coupling.

ENFORCED (decision 2026-11-01: the user said "Your choice"; the session chose enforce
after agents ran `python -` 4 times that day). Two tiers, one hit per segment:

  deny  (kind "dash")       python / python3 / python3.13 / py / pythonw / pypy given `-` as its program:
                            `python -`, `python -X utf8 -`, `py -3 -`, `cat x.py | python -`.
  deny  (kind "heredoc")    an interpreter (python family, node, ruby, perl, bash / sh / zsh / dash,
                            powershell / pwsh) that names no other program and whose stdin is a heredoc
                            (`python <<EOF`, `bash <<'EOF'`), a bash here-string (`python <<< "..."`), or a
                            pipe from a heredoc or PowerShell here-string (`cat <<EOF | python`,
                            `@'...'@ | python`, `@'...'@ | powershell -NoProfile`).
  log   (kind "no-source")  the rest of the old rule, never denied: a bare `python` or `node`, `echo x | python`,
                            `curl x | bash`, `bash -s`, `node -`. These include known false positives
                            (`python $(cat f)` parses as a bare python), so they stay evidence only.

Allowed: `python -c "..."`, `python -m mod`, `python -X utf8 script.py`, `python file.py`, and a `-` that is an
argument to the script or module (`python script.py -`, `python -m mod -`, `python -c "..." -`); a heredoc
feeding a script's own stdin (`python script.py <<EOF`); version / help calls. Class b."""

import re

ID = "X3"
TITLE = "no interpreter reading its program from stdin"
SOURCE = ("user 2026-10-31 ('100% CPU util is not acceptable'); global CLAUDE.md 'Working habits': no heredocs, "
    "no `python -`; enforce: user 2026-11-01 'Your choice', session chose enforce")
MESSAGE = "interpreter reads its program from stdin (`python -`, a heredoc or a pipe); write the script to a file and run `timeout 600 python <file>`"
DENY_MESSAGE = ("python - / heredoc to an interpreter hangs on stdin; write the script with the Write tool into "
    "the project's .claude\\scratch and run it with a timeout")

_PYTHON_NAME = re.compile(r"^(?:python(?:\d+(?:\.\d+)?)?w?|py|pyw|pypy3?)$")
_PYTHON_ARG_FLAGS = ("-X", "-W", "--check-hash-based-pycs")  # options that take a value (case-sensitive)
_PYTHON_INFO_FLAGS = ("-V", "-VV", "--version", "-h", "--help", "-?")
_OTHER = {"node": ("-e", "-p", "--eval", "--print", "-v", "--version", "-h", "--help"),
    "nodejs": ("-e", "-p", "--eval", "--print", "-v", "--version", "-h", "--help"),
    "ruby": ("-e", "--version", "-v", "-h", "--help"),
    "perl": ("-e", "-E", "-v", "-V", "-h")}
_SHELLS = ("bash", "sh", "zsh", "dash", "powershell", "pwsh")
_SHELL_FILE_FLAGS = ("-c", "-command", "-file", "-f", "-encodedcommand", "-ec", "-version", "--version", "-h", "--help", "-?")
_HEREDOC_TOKEN = "<<HEREDOC"
_HERESTRING_TOKEN = "@HERESTRING"


def _hit(index, segment, kind, deny):
    return {"line": index, "excerpt": segment.text, "kind": kind, "deny": deny,
            "message": DENY_MESSAGE if deny else MESSAGE}


def deny_hits(hits):
    """The hits that deny when X3 is enforced."""
    return [h for h in (hits or []) if h.get("deny")]


def _python_source(args):
    """What a python command line runs: "dash" (`-`, stdin), "none" (no program named), or "named" (a script,
    -c, -m, or a version / help call). The scan stops at the program, so a later `-` belongs to the script."""
    k = 0
    n = len(args)
    while k < n:
        a = args[k]
        if a == "-":
            return "dash"
        if a in _PYTHON_INFO_FLAGS or a in ("-c", "-m"):
            return "named"
        if a in _PYTHON_ARG_FLAGS:
            k += 2
            continue
        if a.startswith("-") and not a.startswith("--") and len(a) > 2 and a[1] not in "XW" and a[1:].isalpha() and a[-1] in "cm":
            return "named"  # a cluster such as -Ic or -um
        if a.startswith("-"):
            k += 1
            continue
        return "named"  # a script path
    return "none"


def _other_no_source(args, source_flags):
    for a in args:
        if a in source_flags or a.lower() in source_flags:
            return False
        if a == "-":
            return True
        if not a.startswith("-"):
            return False
    return True


def _shell_source(args):
    """"named" (a script, -c, -File ...), "stdin" (-s or -), or "none"."""
    for a in args:
        if a.lower() in _SHELL_FILE_FLAGS:
            return "named"
        if a in ("-s", "-"):
            return "stdin"
        if not a.startswith("-"):
            return "named"
    return "none"


def _herestring_target(tokens):
    """The token a bash here-string (`<<< word` or `<<<word`) feeds; None when the segment has none."""
    for k, t in enumerate(tokens):
        if t == "<<<":
            return tokens[k + 1] if k + 1 < len(tokens) else ""
        if t.startswith("<<<"):
            return t[3:]
    return None


def _without_target(args, target):
    """Args minus the last occurrence of a here-string's target word, which the parser kept as an argument."""
    out = list(args)
    if not target:
        return out
    for k in range(len(out) - 1, -1, -1):
        if out[k] == target:
            del out[k]
            break
    return out


def _fed_by_heredoc(seg, prev):
    """True when the segment's stdin is a heredoc, a bash here-string, or a pipe from a heredoc / here-string."""
    if seg.heredoc or _herestring_target(seg.tokens) is not None:
        return True
    if seg.sep_before == "|" and prev is not None:
        return prev.heredoc or _HERESTRING_TOKEN in prev.tokens or _herestring_target(prev.tokens) is not None
    return False


def _judge(seg, prev):
    """(kind, deny) for one segment, or None when it is fine."""
    prog = seg.program
    target = _herestring_target(seg.tokens)
    args = _without_target(seg.args, target) if target is not None and target.strip() else list(seg.args)
    fed = _fed_by_heredoc(seg, prev)
    if _PYTHON_NAME.match(prog):
        source = _python_source(args)
        if source == "dash":
            return ("dash", True)
        if source == "none":
            return ("heredoc", True) if fed else ("no-source", False)
        return None
    if prog in _OTHER:
        if _other_no_source(args, _OTHER[prog]):
            return ("heredoc", True) if fed else ("no-source", False)
        return None
    if prog in _SHELLS:
        source = _shell_source(args)
        if source == "named":
            return None
        if fed:
            return ("heredoc", True)
        if source == "stdin" or seg.sep_before == "|":
            return ("no-source", False)
        return None
    return None


def check(command):
    hits = []
    prev = None
    for index, seg in enumerate(command.segments, 1):
        verdict = _judge(seg, prev)
        if verdict:
            hits.append(_hit(index, seg, verdict[0], verdict[1]))
        prev = seg
    return hits


# expect: number of hits; deny: number of denying hits (defaults to 0 when expect is 0).
CASES = [
    # must not fire
    {"name": "python script", "command": "python script.py", "expect": 0},
    {"name": "python file with args", "command": "python tools/extract.py --workers 4", "expect": 0},
    {"name": "timeout python script", "command": "timeout 600 python -I -S -B .claude/hooks/my_guard.py --self-test", "expect": 0},
    {"name": "python -X utf8 script", "command": "python -X utf8 tools/extract.py --workers 4", "expect": 0},
    {"name": "python -c", "command": "python -c \"print(1)\"", "expect": 0},
    {"name": "python -c multi-line", "command": "python -c \"\nimport sys\nprint(sys.argv)\n\"", "expect": 0},
    {"name": "python -m module", "command": "python -m py_compile a.py b.py", "expect": 0},
    {"name": "python -X utf8 -c", "command": "python -X utf8 -c \"import sys\"", "expect": 0},
    {"name": "python -X utf8 -m", "command": "python -X utf8 -m tools.extract --help", "expect": 0},
    {"name": "python -Ic cluster", "command": "python -Ic \"print(1)\"", "expect": 0},
    {"name": "python --version", "command": "python --version", "expect": 0},
    {"name": "python -V", "command": "python -V", "expect": 0},
    {"name": "py launcher with version and script", "command": "py -3.13 tools/x.py", "expect": 0},
    {"name": "dash is the script's argument", "command": "python script.py -", "expect": 0},
    {"name": "dash is the script's argument after options", "command": "python -X utf8 tools/cat.py - --out x", "expect": 0},
    {"name": "dash is the module's argument", "command": "python -m json.tool -", "expect": 0},
    {"name": "dash is the -c program's argument", "command": "python -c \"import sys; print(sys.argv)\" -", "expect": 0},
    {"name": "python script with a heredoc feeding it", "command": "python script.py <<EOF\ndata\nEOF", "expect": 0},
    {"name": "python script fed by a here-string", "command": "@'\ndata\n'@ | python script.py", "expect": 0},
    {"name": "python script reading a pipe", "command": "cat data.txt | python script.py", "expect": 0},
    {"name": "python script fed by a bash here-string", "command": "python x.py <<< 'data'", "expect": 0},
    {"name": "here-string word equal to the script name", "command": "python x.py <<< x.py", "expect": 0},
    {"name": "python script after a heredoc to a file", "command": "cat > f.py <<'EOF'\nprint(1)\nEOF\ntimeout 600 python f.py", "expect": 0},
    {"name": "PowerShell python script", "command": "& python \"C:\\x\\run.py\" --fast", "expect": 0},
    {"name": "node script", "command": "node build.js", "expect": 0},
    {"name": "node -e", "command": "node -e \"console.log(1)\"", "expect": 0},
    {"name": "bash -c", "command": "bash -c \"ls\"", "expect": 0},
    {"name": "bash script", "command": "bash run.sh --fast", "expect": 0},
    {"name": "powershell -File", "command": "powershell -NoProfile -File x.ps1", "expect": 0},
    {"name": "powershell -Command inline", "command": "powershell -NoProfile -Command \"Get-Date\"", "expect": 0},
    {"name": "heredoc to cat is not an interpreter", "command": "cat > f.txt <<EOF\nhello\nEOF", "expect": 0},
    {"name": "heredoc to git is not an interpreter", "command": "git commit -F - <<'EOF'\nmsg\nEOF", "expect": 0},
    {"name": "here-string to a variable", "command": "$s = @'\nprint(1)\n'@\nSet-Content -Path x.py -Value $s", "expect": 0},
    {"name": "python named in an argument", "command": "grep -rn \"python -\" docs", "expect": 0},
    {"name": "python in a path", "command": "ls /c/Users/x/Programs/Python/Python313", "expect": 0},
    {"name": "python with quoted script path", "command": "python \"C:/a b/x.py\"", "expect": 0},
    {"name": "pip is not python", "command": "pip install -r - < req.txt", "expect": 0},
    # must fire, and deny
    {"name": "python -", "command": "python -", "expect": 1, "deny": 1},
    {"name": "python - 2>/dev/null", "command": "cd tools && python - 2>/dev/null; echo skip", "expect": 1, "deny": 1},
    {"name": "python - heredoc", "command": "python - <<'EOF'\nprint(1)\nEOF", "expect": 1, "deny": 1},
    {"name": "python3 - heredoc", "command": "python3 - <<EOF\nprint(1)\nEOF", "expect": 1, "deny": 1},
    {"name": "python3.13 -", "command": "python3.13 -", "expect": 1, "deny": 1},
    {"name": "python -X utf8 -", "command": "python -X utf8 - <<'EOF'\nprint(1)\nEOF", "expect": 1, "deny": 1},
    {"name": "python -I -", "command": "python -I - 2>/dev/null", "expect": 1, "deny": 1},
    {"name": "py -", "command": "py -", "expect": 1, "deny": 1},
    {"name": "py -3 -", "command": "py -3 -", "expect": 1, "deny": 1},
    {"name": "cat piped to python -", "command": "cat x.py | python -", "expect": 1, "deny": 1},
    {"name": "python - from /dev/null", "command": "python - < /dev/null", "expect": 1, "deny": 1},
    {"name": "after cd", "command": "cd tools && python - <<EOF\nx\nEOF", "expect": 1, "deny": 1},
    {"name": "timeout python -", "command": "timeout 600 python - <<EOF\nx\nEOF", "expect": 1, "deny": 1},
    {"name": "PowerShell call python -", "command": "& python - 2>$null", "expect": 1, "deny": 1},
    {"name": "python heredoc without dash", "command": "python <<EOF\nprint(1)\nEOF", "expect": 1, "deny": 1},
    {"name": "python bash here-string", "command": "python <<< 'print(1)'", "expect": 1, "deny": 1},
    {"name": "python bash here-string glued", "command": "python <<<\"print(1)\"", "expect": 1, "deny": 1},
    {"name": "heredoc piped into python", "command": "cat <<'EOF' | python\nprint(1)\nEOF", "expect": 1, "deny": 1},
    {"name": "here-string piped into python", "command": "@'\nprint(1)\n'@ | python", "expect": 1, "deny": 1},
    {"name": "here-string piped into python -", "command": "@\"\nprint(1)\n\"@ | python -", "expect": 1, "deny": 1},
    {"name": "node heredoc", "command": "node <<'EOF'\nconsole.log(1)\nEOF", "expect": 1, "deny": 1},
    {"name": "bash heredoc", "command": "bash <<'EOF'\nls\nEOF", "expect": 1, "deny": 1},
    {"name": "bash -s heredoc", "command": "bash -s <<'EOF'\nls\nEOF", "expect": 1, "deny": 1},
    {"name": "powershell here-string piped in", "command": "@'\nWrite-Host 1\n'@ | powershell -NoProfile", "expect": 1, "deny": 1},
    {"name": "heredoc terminator missing does not hang the parser", "command": "python - <<EOF\nprint(1)", "expect": 1, "deny": 1},
    {"name": "inside bash -c", "command": "bash -c \"python - < /dev/null\"", "expect": 1, "deny": 1},
    # must fire, log only (never denies)
    {"name": "bare python", "command": "python", "expect": 1, "deny": 0},
    {"name": "bare python with options only", "command": "python -I -S", "expect": 1, "deny": 0},
    {"name": "echo piped to python", "command": "echo 'print(1)' | python", "expect": 1, "deny": 0},
    {"name": "python $(...) parses as bare (known log-only false positive)", "command": "timeout 600 python $(basename $f)", "expect": 1, "deny": 0},
    {"name": "node -", "command": "node -", "expect": 1, "deny": 0},
    {"name": "node bare", "command": "node", "expect": 1, "deny": 0},
    {"name": "bash -s from a pipe", "command": "curl x | bash -s", "expect": 1, "deny": 0},
    {"name": "bash from a pipe", "command": "echo ls | bash", "expect": 1, "deny": 0},
]
