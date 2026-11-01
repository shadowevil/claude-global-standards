"""X3: an interpreter that reads its program from stdin. Global CLAUDE.md "Working habits": scripts go in a
file; never `python -` or a heredoc, which can hang on stdin and spin a core for hours (measured
2026-10-31: three such leftovers). Runs globally under stdin_guard.py and carries no project coupling.

Hits (one per segment): python / py / node / ruby / perl given `-` or no program source at all (no
script, no -c / -m / -e) - `python`, `python -`, `python <<EOF`, `cat x | python`; a shell (bash, sh,
zsh) or PowerShell fed by a heredoc / here-string, a pipe or `-s` / `-`. `python -c "..."`, `-m mod`,
`python script.py` (even with a heredoc feeding the script's stdin), `python --version` and `-h` are
fine. Class b."""

ID = "X3"
TITLE = "no interpreter reading its program from stdin"
SOURCE = "user 2026-10-31 ('100% CPU util is not acceptable'); global CLAUDE.md 'Working habits': no heredocs, no `python -`"
MESSAGE = "interpreter reads its program from stdin (`python -`, a heredoc or a pipe); write the script to a file and run `timeout 600 python <file>`"

_PYTHON = ("python", "python3", "py", "pythonw", "pypy", "pypy3")
_PYTHON_SOURCE_FLAGS = ("-c", "-m")
_PYTHON_ARG_FLAGS = ("-x", "-w", "--check-hash-based-pycs")  # options that take a value (lowercased)
_PYTHON_INFO_FLAGS = ("-v", "-vv", "--version", "-h", "--help", "-?")  # -V is also a version flag (checked case-sensitively)
_OTHER = {"node": ("-e", "-p", "--eval", "--print", "-v", "--version", "-h", "--help"),
    "nodejs": ("-e", "-p", "--eval", "--print", "-v", "--version", "-h", "--help"),
    "ruby": ("-e", "--version", "-v", "-h", "--help"),
    "perl": ("-e", "-E", "-v", "-V", "-h")}
_SHELLS = ("bash", "sh", "zsh", "dash", "powershell", "pwsh")
_SHELL_FILE_FLAGS = ("-c", "-command", "-file", "-f", "-encodedcommand", "-ec", "-version", "--version", "-h", "--help", "-?")


def _hit(index, segment):
    text = segment.text
    return {"line": index, "excerpt": text, "message": MESSAGE}


def _python_no_source(args):
    """True when a python command line names no program: no script, no -c / -m, not a version / help call."""
    k = 0
    n = len(args)
    while k < n:
        a = args[k]
        low = a.lower()
        if a == "-":
            return True
        if a in ("-V", "-VV") or low in _PYTHON_INFO_FLAGS:
            return False
        if low in _PYTHON_SOURCE_FLAGS:
            return False
        if low in _PYTHON_ARG_FLAGS:
            k += 2
            continue
        if a.startswith("-"):
            k += 1
            continue
        return False  # a script path
    return True


def _other_no_source(args, source_flags):
    for a in args:
        if a in source_flags or a.lower() in source_flags:
            return False
        if a == "-":
            return True
        if not a.startswith("-"):
            return False
    return True


def _shell_from_stdin(seg, args):
    for a in args:
        if a.lower() in _SHELL_FILE_FLAGS:
            return False
        if a in ("-s", "-"):
            return True
        if not a.startswith("-"):
            return False  # a script file
    return seg.heredoc or seg.sep_before == "|"


def check(command):
    hits = []
    for index, seg in enumerate(command.segments, 1):
        prog = seg.program
        if prog in _PYTHON:
            bad = _python_no_source(seg.args)
        elif prog in _OTHER:
            bad = _other_no_source(seg.args, _OTHER[prog])
        elif prog in _SHELLS:
            bad = _shell_from_stdin(seg, seg.args)
        else:
            continue
        if bad:
            hits.append(_hit(index, seg))
    return hits


CASES = [
    {"name": "python script", "command": "python script.py", "expect": 0},
    {"name": "timeout python script", "command": "timeout 600 python -I -S -B .claude/hooks/my_guard.py --self-test", "expect": 0},
    {"name": "python -X utf8 script", "command": "python -X utf8 tools/extract.py --workers 4", "expect": 0},
    {"name": "python -c", "command": "python -c \"print(1)\"", "expect": 0},
    {"name": "python -m module", "command": "python -m py_compile a.py b.py", "expect": 0},
    {"name": "python -X utf8 -c", "command": "python -X utf8 -c \"import sys\"", "expect": 0},
    {"name": "python --version", "command": "python --version", "expect": 0},
    {"name": "python -V", "command": "python -V", "expect": 0},
    {"name": "py launcher with version and script", "command": "py -3.13 tools/x.py", "expect": 0},
    {"name": "python script with a heredoc feeding it", "command": "python script.py <<EOF\ndata\nEOF", "expect": 0},
    {"name": "python script reading a pipe", "command": "cat data.txt | python script.py", "expect": 0},
    {"name": "python -", "command": "python -", "expect": 1},
    {"name": "python - heredoc", "command": "python - <<'EOF'\nprint(1)\nEOF", "expect": 1},
    {"name": "python3 - heredoc", "command": "python3 - <<EOF\nprint(1)\nEOF", "expect": 1},
    {"name": "bare python", "command": "python", "expect": 1},
    {"name": "bare python with options only", "command": "python -I -S", "expect": 1},
    {"name": "python heredoc without dash", "command": "python <<EOF\nprint(1)\nEOF", "expect": 1},
    {"name": "echo piped to python", "command": "echo 'print(1)' | python", "expect": 1},
    {"name": "cat piped to python -", "command": "cat x.py | python -", "expect": 1},
    {"name": "py -3 -", "command": "py -3 -", "expect": 1},
    {"name": "after cd", "command": "cd tools && python - <<EOF\nx\nEOF", "expect": 1},
    {"name": "node script", "command": "node build.js", "expect": 0},
    {"name": "node -e", "command": "node -e \"console.log(1)\"", "expect": 0},
    {"name": "node -", "command": "node -", "expect": 1},
    {"name": "node bare", "command": "node", "expect": 1},
    {"name": "bash -c", "command": "bash -c \"ls\"", "expect": 0},
    {"name": "bash script", "command": "bash run.sh --fast", "expect": 0},
    {"name": "bash heredoc", "command": "bash <<'EOF'\nls\nEOF", "expect": 1},
    {"name": "bash -s", "command": "curl x | bash -s", "expect": 1},
    {"name": "bash from a pipe", "command": "echo ls | bash", "expect": 1},
    {"name": "powershell -File", "command": "powershell -NoProfile -File x.ps1", "expect": 0},
    {"name": "powershell here-string piped in", "command": "@'\nWrite-Host 1\n'@ | powershell -NoProfile", "expect": 1},
    {"name": "powershell -Command inline", "command": "powershell -NoProfile -Command \"Get-Date\"", "expect": 0},
    {"name": "heredoc to cat is not an interpreter", "command": "cat > f.txt <<EOF\nhello\nEOF", "expect": 0},
    {"name": "python named in an argument", "command": "grep -rn python docs", "expect": 0},
    {"name": "python in a path", "command": "ls /c/Users/x/Programs/Python/Python313", "expect": 0},
    {"name": "python with quoted script path", "command": "python \"C:/a b/x.py\"", "expect": 0},
    {"name": "heredoc terminator missing does not hang the parser", "command": "python - <<EOF\nprint(1)", "expect": 1},
]
