"""W2: a delete's target must be a literal, fully-qualified path - never a relative path, a
$variable/substitution, a %VAR%, a leading ~ home-dir reference, or a delete fed by a pipe or with
no path at all. A wildcard is fine only in the final file-name segment of an otherwise literal
path, and never together with a recursive flag or on rmdir/rd (either could sweep a whole
directory). Ported from the origin project hook's audited W2 section - same
checks, same fixes (a LEADING ~ only, never a substring match against an 8.3 short name like
SHADOW~1; brace/heredoc/quote-aware segmentation; the PowerShell comma-array-argument hotfix) -
generalized with no project-specific text: the deny message names no repo, no doc.

Two Bash-only tokenizing fixes, 2026-09-30 (found replaying the real hit log before W2 was enforced -
see write_guard.py's docstring): a quoted-then-unquoted word such as `"C:/dir/"*.pyc` is one word
(glue_adjacent_quotes), and a backslash-newline continuation joins its lines (join_continuations).

`git rm` (history, not an arbitrary filesystem delete) and any Remove-* cmdlet other than
Remove-Item/ri (Remove-ItemProperty, Remove-Variable, ...) are exempt - the second exemption falls
out naturally from DELETE_VERBS (hooklib.shell) never listing them, not a special case here.

    evaluate_shell(event) - a Bash/PowerShell event -> [("W2", (segment, verb, target, reason)), ...]
    decide(event, tool_hits, shell_hits, enforce) - turns hits into a PreToolUse deny

Raw target extraction (delete_verb_targets) lives in hooklib.shell, shared with rules/write (a
delete is a write to the filesystem too, and W3 judges where it lands) - not duplicated here.
"""
import re

from hooklib import paths
from hooklib import shell
from hooklib.output import excerpt

RULE_ID = "W2"

MSG = {
    RULE_ID: "Delete with a non-static-resolvable target. Rewrite the target as a literal "
             "absolute path (C:\\... , \\\\server\\... , or /c/...) naming every directory - "
             "never a relative path, $variable, ${...}, $env:, %VAR%, ~, $(...) or backtick "
             "substitution, a delete fed by a pipe, or a delete with no path at all. A wildcard "
             "(* ? [...]) is fine only in the final file-name segment of an otherwise literal "
             "path, and never together with a recursive flag (-r, -rf, -Recurse, /s) or on "
             "rmdir/rd, which always resolve to directories.",
}

RMDIR_VERBS = {"rmdir", "rd"}
TILDE_HOME = re.compile(r"^~[A-Za-z0-9_.\-]*([\\/].*)?$")  # a LEADING ~ only, not e.g. SHADOW~1
PERCENT_VAR = re.compile(r"%[A-Za-z_]\w*%")
WILDCARD = re.compile(r"[*?\[\]]")
WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")


def bad_delete_target(verb, is_recursive, path):
    """Why `path` is not a safe, fully-qualified delete target for this `verb`, or None if it is
    fine."""
    if not path:
        return "an empty target"
    if shell.VAR_OR_SUBST.search(path):
        return "a variable or command substitution"
    if PERCENT_VAR.search(path):
        return "a %VAR% reference"
    if TILDE_HOME.match(path):
        return "a ~ home-dir reference"
    if WILDCARD.search(path):
        if verb in RMDIR_VERBS:
            return "a wildcard on rmdir/rd (always resolves to directories)"
        if is_recursive:
            return "a wildcard combined with a recursive flag (could sweep directories)"
        sep = max(path.rfind("\\"), path.rfind("/"))
        dir_part = path[:sep + 1] if sep >= 0 else path
        if WILDCARD.search(dir_part):
            return "a wildcard in a directory segment"
        # else: confined to the final file-name segment, non-recursive, not rmdir/rd - allowed
    if WIN_ABS.match(path) or path.startswith("\\\\") or path.startswith("//") or path.startswith("/"):
        return None
    return "a relative path"


def join_continuations(cmd):
    """cmd with every Bash backslash-newline line continuation joined into one line (2026-09-30,
    found replaying the real hit log before enforcing W2). split_top_level cuts at every newline, so
    `rm -f "a" \\<newline> "b"` used to give the first line a bogus target `\\` (flagged as a relative
    path) and left the continuation line's targets unseen - a false positive and a bypass in one.
    A join is skipped when the next line is a heredoc terminator, so a body line that happens to end
    in a backslash can never swallow its own terminator. Bash only (PowerShell's continuation is a
    backtick, and a trailing backslash there is an ordinary path character)."""
    if "\\\n" not in cmd and "\\\r\n" not in cmd:
        return cmd
    terminators = {m.group(2) for m in shell.HEREDOC_START.finditer(cmd)}
    lines = cmd.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        body = line[:-1] if line.endswith("\r") else line
        while body.endswith("\\") and i + 1 < len(lines) and lines[i + 1].strip() not in terminators:
            i += 1
            nxt = lines[i]
            nxt = nxt[:-1] if nxt.endswith("\r") else nxt
            body = body[:-1] + " " + nxt.lstrip()
        out.append(body)
        i += 1
    return "\n".join(out)


def glue_adjacent_quotes(seg):
    """seg with every Bash word that mixes quoted and unquoted pieces - `"C:/dir/"*.pyc`,
    `"$S"/x*` - rewritten as ONE quoted word (2026-09-30, found replaying the real hit log:
    shell_tokens' shlex(posix=False) splits such a word into `"C:/dir/"` and `*.pyc`, and the bare
    `*.pyc` was then judged as a relative wildcard target - a false positive on a fully qualified
    delete, 13 hits in the first two days). A word that opens with a redirect operator is left
    alone, a word that cannot be re-quoted safely (both quote characters inside) is left alone, and
    so is the whole segment if a quote never closes. Bash only: PowerShell commas and -Flag:'v'
    forms depend on the original quoting (see hooklib.shell.expand_ps_commas)."""
    if "'" not in seg and '"' not in seg:
        return seg
    out = []
    i = 0
    n = len(seg)
    while i < n:
        if seg[i].isspace():
            out.append(seg[i])
            i += 1
            continue
        start = i
        pieces = []  # (is_quoted, text)
        buf = []
        while i < n and not seg[i].isspace():
            c = seg[i]
            if c == "\\" and i + 1 < n:
                buf.append(seg[i:i + 2])
                i += 2
                continue
            if c in ("'", '"'):
                k = i + 1
                while k < n and seg[k] != c:
                    k += 2 if (c == '"' and seg[k] == "\\" and k + 1 < n) else 1
                if k >= n:
                    return seg  # unbalanced quote - leave the whole segment as it was
                if buf:
                    pieces.append((False, "".join(buf)))
                    buf = []
                pieces.append((True, seg[i + 1:k]))
                i = k + 1
                continue
            buf.append(c)
            i += 1
        if buf:
            pieces.append((False, "".join(buf)))
        word = seg[start:i]
        if (len(pieces) >= 2 and any(q for q, _t in pieces)
                and any(t for q, t in pieces if not q)
                and not re.match(r"^\d*[<>&]", word)):
            combined = "".join(t for _q, t in pieces)
            wrap = '"' if '"' not in combined else ("'" if "'" not in combined else None)
            if wrap:
                word = wrap + combined + wrap
        out.append(word)
    return "".join(out)


def find_delete_hits(cmd, powershell=False):
    """[(segment, verb, target, reason), ...] for every unsafe delete in this command string.
    target is None for a `find -delete` / `find -exec rm`, or a delete with no explicit path
    (pipe-fed, or none given at all). `powershell` (from the event's own tool_name) gates whether a
    comma in a target is treated as a PowerShell array separator or a literal Bash filename
    character - see hooklib.shell.shell_tokens. For Bash (not PowerShell) line continuations are
    joined first and a quoted-then-unquoted word is read as one word - see join_continuations and
    glue_adjacent_quotes; the returned segment text is always the ORIGINAL one."""
    out = []
    if not powershell:
        cmd = join_continuations(cmd)
    for orig_seg in shell.split_top_level(cmd):
        seg = orig_seg if powershell else glue_adjacent_quotes(orig_seg)
        toks = shell.shell_tokens(seg, powershell)
        if not toks:
            continue
        head_l = toks[0].lower()
        if head_l == "git":
            continue  # git rm is git's own history, not an arbitrary filesystem delete by path
        if head_l == "find":
            if any(t.lower() == "-delete" for t in toks):
                out.append((orig_seg.strip(), "find", None, "a find -delete (target is not a static path)"))
            for i in range(len(toks) - 1):
                if toks[i].lower() == "-exec" and toks[i + 1].lower() == "rm":
                    out.append((orig_seg.strip(), "find", None, "a find -exec rm (target is not a static path)"))
            continue
        d = shell.delete_verb_targets(seg, powershell)
        if d is None:
            continue
        verb, targets, is_recursive = d
        if not powershell and targets and targets[-1] == "\\":
            # a trailing lone backslash with nothing after it to continue onto (join_continuations
            # already joined every real continuation): shell syntax, never a delete target
            targets = targets[:-1]
            if not targets:
                continue
        if not targets:
            out.append((orig_seg.strip(), verb, None, "no explicit path (possibly pipe-fed)"))
            continue
        for t in targets:
            why = bad_delete_target(head_l, is_recursive, t)
            if why:
                out.append((orig_seg.strip(), verb, t, why))
    return out


def evaluate_tool(event):
    """W2 never fires on a Write/Edit/MultiEdit/NotebookEdit call - a delete is always typed
    through Bash/PowerShell."""
    return []


def evaluate_shell(event):
    """[(rule_id, detail), ...] for a Bash/PowerShell PreToolUse event."""
    hits = []
    tool = event.get("tool_name") or ""
    if tool not in ("Bash", "PowerShell"):
        return hits
    cmd = (event.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return hits
    powershell = tool == "PowerShell"
    for seg, verb, target, reason in find_delete_hits(cmd, powershell):
        hits.append((RULE_ID, (seg, verb, target, reason)))
    return hits


def log_rows(event, tool_hits, shell_hits, ms):
    """[{"rule", "tool", "root", "cwd", "target", "sample", "session", "ms", "msg"}, ...] - see
    rules/write/w3_outside_root.py's log_rows docstring for why `root`/`cwd`/`target` are their own
    fields (2026-09-28, the week-long log-only rollout: the data needs to be groupable per project
    and per target without re-parsing `sample`)."""
    root = paths.project_root(event)
    cwd = event.get("cwd")
    rows = []
    for rid, detail in shell_hits:
        if rid != RULE_ID:
            continue
        seg, verb, target, reason = detail
        sample = excerpt("%s -> %s: %s" % (verb, target or "(no path)", reason))
        rows.append({"rule": rid, "tool": event.get("tool_name"), "root": root, "cwd": cwd,
                      "target": target, "sample": [sample],
                      "session": event.get("session_id"), "ms": ms, "msg": MSG[RULE_ID]})
    return rows


MAX_LISTED = 10  # decide() lists at most this many bad targets, then "+N more"


def decide(event, tool_hits, shell_hits, enforce):
    """The PreToolUse deny for these hits, or None. Each offending target is listed once, on its
    own short line under its segment, and the fix-it guidance (MSG) follows ONCE at the end
    (2026-09-30 review fix: MSG used to be repeated per target - an 8-target `rm` gave a 4549-char
    reason). At most MAX_LISTED targets are listed, then "+N more"."""
    if not enforce:
        return None
    items = []
    seen = set()
    for rid, detail in shell_hits:
        if rid != RULE_ID:
            continue
        seg, _verb, target, reason = detail
        key = (seg, target, reason)
        if key in seen:
            continue
        seen.add(key)
        items.append((excerpt(seg), excerpt(target or "(no explicit path)"), reason))
    if not items:
        return None
    lines = []
    last_seg = None
    for seg, where, reason in items[:MAX_LISTED]:
        if seg != last_seg:
            lines.append("W2: `%s`" % seg)
            last_seg = seg
        lines.append("  -> %s: %s" % (where, reason))
    if len(items) > MAX_LISTED:
        lines.append("  +%d more" % (len(items) - MAX_LISTED))
    lines.append(MSG[RULE_ID] if len(items) == 1 else "For EACH target above: " + MSG[RULE_ID])
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": "\n".join(lines)}}


# ------------------------------------------------------------------ self-test (cases live next to the rule)

# synthetic roots - W2 does not care about any project root (it only judges a target's own syntax),
# so these exist only to give the cases a realistic absolute path; nothing here is project-specific
GFQP = "/c/Users/ShadowEvil/test-project-root"
GFQW = "C:\\Users\\ShadowEvil\\test-project-root"

# (command, must fire)
SHELL_CASES = [
    ("rm -rf build", True),                                             # relative
    ("rm -rf $HOME/tmp", True),                                         # bare $VAR
    ("rm -rf ${TMPDIR}/x", True),                                       # ${...}
    ("Remove-Item $env:TEMP\\foo", True),                               # $env:
    ("del %TEMP%\\foo.txt", True),                                      # %VAR%
    ("rm -rf ~/cache", True),                                           # ~
    ("rm -rf $(pwd)/build", True),                                      # $(...)
    ("rm -rf `pwd`/build", True),                                       # backtick
    ("Remove-Item 'C:\\Users\\Shadow*\\data\\x.db'", True),             # wildcard in a directory segment
    ("rm -f " + GFQP + "/probe*/x", True),                              # wildcard in a directory segment
    ("Get-ChildItem C:\\temp | Remove-Item", True),                     # pipe-fed, no explicit path
    ("find /tmp -name '*.bak' -delete", True),                          # find -delete
    ("find /tmp -name '*.bak' -exec rm {} \\;", True),                  # find -exec rm
    ("rm -rf " + GFQP + "/build ./stray", True),                        # multi-target, one relative
    ("Remove-Item .\\build -Recurse -Force", True),                     # relative (PowerShell)
    ("rmdir build/tmp", True),                                          # relative (bash rmdir)
    ("del build\\output.log", True),                                    # relative (PowerShell alias)
    ("rm -rf " + GFQP + "/probe/*.db", True),                           # wildcard + recursive
    ("Remove-Item -Recurse '" + GFQW + "\\probe\\*.log'", True),        # wildcard + -Recurse
    ("del /s " + GFQW + "\\probe\\*.log", True),                        # wildcard + /s
    ("rd /s " + GFQW + "\\probe\\*", True),                             # wildcard on rd (recursive too)
    ("rd " + GFQW + "\\build\\*", True),                                # wildcard on rd, not even recursive
    ("git rm -f " + GFQP + "/foo.txt", False),                          # git rm is exempt
    ("git rm --cached foo.txt", False),                                 # git rm is exempt
    ('echo "rm -rf /tmp"', False),                                      # rm only inside a quoted string
    ('grep -n "rm -rf" src/foo.cpp', False),                            # rm only inside a quoted string
    ("sed -n '/rm -rf/p' file.txt", False),                             # rm only inside a quoted string
    ("Remove-ItemProperty -Path HKCU:\\Software\\Foo -Name Bar", False),  # a different cmdlet
    ("Remove-Variable -Name tempVar", False),                           # a different cmdlet
    ("custom-rmdir-wrapper.sh /tmp/x", False),                          # rmdir is part of a longer word
    ("rm -rf " + GFQP + "/build/tmp", False),                           # fully qualified (posix)
    ("Remove-Item -Recurse -Force " + GFQW + "\\build\\tmp", False),    # fully qualified, recursive but no wildcard
    ("powershell -File src/tools/scripts/backup.ps1 purge -Set all", False),  # no delete verb typed
    ("pwsh src/tools/scripts/world_boot_verify.ps1", False),            # no delete verb typed
    ("Remove-Item '\\\\server\\share\\build' -Recurse", False),         # fully qualified UNC path
    ("rm -f " + GFQP + "/working/probe/*.db", False),                   # wildcard confined to the file name
    ("Remove-Item '" + GFQW + "\\working\\probe\\*.log'", False),       # wildcard, file name only
    ("", False),                                                        # no command
    # newline-separated commands: a following line must not be swept in as a fake target
    ('rm -rf "' + GFQP + '/working/selftest"\nls "' + GFQP + '/working"', False),
    ("rm -rf build\nls .", True),                                       # still catches the relative rm
    ('cd "' + GFQW + '" && rmdir /s /q "working\\client" 2>nul; '
     'rm -rf "working/client" 2>/dev/null', True),                      # relative targets on both halves
    # redirections are not delete targets
    ("rm -rf " + GFQP + "/build/tmp 2>/dev/null", False),
    ("rmdir /s /q " + GFQW + "\\build\\tmp 2>nul", False),
    # a heredoc body is data, never scanned as commands
    ("cat <<'EOF'\nrm -rf /tmp\nEOF", False),
    ("python - <<'EOF'\nimport os\nos.remove('x')\nEOF\nrm -rf build", True),  # but the trailing rm still fires
    # a LEADING ~ is a home reference; SHADOW~1 (an 8.3 short name) is not
    ('rm -f "/c/Users/SHADOW~1/AppData/Local/Temp/x.txt"', False),
    ("rm -rf ~", True),
    ("rm -rf ~root/data", True),
    # a delete nested in a PowerShell block or scriptblock must still be seen
    ("if ($true) { Remove-Item build\\x -Recurse -Force }", True),
    ("foreach ($f in $files) { Remove-Item $f }", True),
    ("Move-Item a b; if ((Get-ChildItem .backups -Force | Measure-Object).Count -eq 0) "
     "{ Remove-Item .backups }", True),
    ("if ($true) { Write-Host 'no delete here' }", False),              # a block with no delete stays quiet
    # bug found in review, 2026-09-28: comma-splitting must NOT run for Bash - a Bash filename may
    # legitimately contain a literal comma, and splitting on it turned one absolute target into two,
    # flagging the (fake) second half as relative. Tested with powershell=False (the default) below.
    ("rm " + GFQW + "\\x,y", False),                                    # one literal target, comma in the name
    ("rm a,b", True),                                                   # one RELATIVE literal target "a,b"
    # 2026-09-30, found replaying the real hit log before enforcing W2 - class A: a quoted literal
    # directory glued to an unquoted glob (`"dir/"*.pyc`) used to tokenize as `"dir/"` and a bogus
    # relative `*.pyc`, flagging a fully qualified delete
    ('rm "' + GFQP + '/probe/__pycache__/"*.pyc', False),
    ('rm -f "' + GFQP + '/a/"*.json* "' + GFQP + '/b/"*.json*', False),
    ('rm "' + GFQP + '/tk/"*', False),
    ("rm '" + GFQP + "/tk/'*.log", False),
    ('rm "' + GFQP + '/x/"*.log ../y', True),                           # glued glob fine, second target relative
    ('rm -rf "' + GFQP + '/probe/"*', True),                            # glued, but wildcard + recursive still fires
    ('rm -f "' + GFQP + '/a dir/"*.txt', False),                        # a space inside the quoted part stays one word
    ('rm -f "$S"/k2mut_*', True),                                       # glued variable still fires
    ('rm -rf "' + GFQP + '/build" 2>"/dev/null"', False),               # a redirect with a quoted target is not glued
    # class B: a Bash backslash-newline continuation - the lone `\` was flagged as a relative target,
    # and the continuation line's own targets were never seen at all
    ('rm -f "' + GFQP + '/a.txt" \\\n  "' + GFQP + '/b.txt"', False),
    ('rm -f "' + GFQP + '/a.txt" \\\n  build/b.txt', True),             # relative target on the continuation line
    ('rm -f "' + GFQP + '/a.txt" \\\n  "$D/b.txt"', True),              # variable on the continuation line
    ("rm -f " + GFQP + "/a.txt \\", False),                             # a trailing lone backslash is not a target
    ("cat <<'EOF'\nbody ends in a backslash \\\nEOF\nrm -rf build", True),   # heredoc terminator not swallowed
    ("cat <<'EOF'\nrm -rf x \\\nEOF\nls " + GFQP, False),               # heredoc body is still never scanned
]

# PowerShell comma-separated array of delete targets - each element checked on its own, not glued
# to its neighbour's leading/trailing comma (live false positive, 2026-09-28). Tested with
# powershell=True: comma-splitting is a PowerShell-only behaviour (see SHELL_CASES above).
PS_COMMA_CASES = [
    ("Remove-Item -LiteralPath '" + GFQW + "\\a\\x.png','" + GFQW + "\\a\\y.png'", False),  # 'a','b'
    ("Remove-Item -LiteralPath '" + GFQW + "\\a\\x.png',y.png", True),                      # 'a',b (b relative)
    ('Remove-Item -LiteralPath "' + GFQW + '\\a\\x.png", "' + GFQW + '\\a\\y.png"', False),  # "a", "b"
    ('Remove-Item -LiteralPath "' + GFQW + '\\a\\x.png", "y.png"', True),                   # "a", "b" (b relative)
    ("Remove-Item -LiteralPath " + GFQW + "\\a\\x.png," + GFQW + "\\a\\y.png", False),       # a,b unquoted
    ("Remove-Item -LiteralPath " + GFQW + "\\a\\x.png,y.png", True),                         # a,b unquoted (b relative)
    ("Remove-Item -LiteralPath @('" + GFQW + "\\a\\x.png','" + GFQW + "\\a\\y.png')", False),  # @('a','b')
    ("Remove-Item -LiteralPath @('" + GFQW + "\\a\\x.png','y.png')", True),                 # @('a','b') (b relative)
    # colon form (`-LiteralPath:'a','b'`, no space before the first value) - second security fix,
    # 2026-09-28 re-review: the colon-attached flag+value used to be swallowed whole as one opaque
    # "-flag" token (delete_verb_targets in hooklib.shell), discarding that element from `targets`
    # entirely regardless of array position - not merely misjudging it, LOSING it, so a relative (or
    # any other unsafe) target glued directly to the flag by a colon was invisible to W2 no matter
    # where in the array it sat. One relative element per case, at each array position, plus an
    # all-absolute control and a `-Force:$true` sanity check (a colon-attached boolean switch value
    # must never be mistaken for a delete target).
    ("Remove-Item -LiteralPath:'y.png','" + GFQW + "\\a','" + GFQW + "\\b'", True),   # relative FIRST
    ("Remove-Item -LiteralPath:'" + GFQW + "\\a','y.png','" + GFQW + "\\b'", True),   # relative MIDDLE
    ("Remove-Item -LiteralPath:'" + GFQW + "\\a','" + GFQW + "\\b','y.png'", True),   # relative LAST
    ("Remove-Item -LiteralPath:'" + GFQW + "\\a','" + GFQW + "\\b','" + GFQW + "\\c'", False),  # all absolute
    ("Remove-Item -Force:$true -LiteralPath '" + GFQW + "\\a'", False),  # -Force:$true not mistaken for a target
]


N_CASES = 6  # run_self_test's deny-reason cases: 1-target, 8-target size, 8-target listing, 12-target cap,
#              repeated target, ASCII (hand-counted, like w4_review_stamp's)


def run_self_test():
    """[failure, ...] - empty means every case behaved. Exercises find_delete_hits/evaluate_shell
    over SHELL_CASES, the enforce-by-default deny, and the CLAUDE_WRITE_GUARD=log override."""
    import os as _os
    from hooklib.output import enforcing as _enforcing

    failures = []
    for cmd, want in SHELL_CASES:
        got = bool(find_delete_hits(cmd))  # powershell=False (default) - these are Bash-or-unknown cases
        if got != want:
            failures.append("W2 must %s: %r" % ("fire" if want else "not fire", cmd[:90]))
    if not any(c[1] for c in SHELL_CASES) or not any(not c[1] for c in SHELL_CASES):
        failures.append("W2 lacks a must-fire or must-not-fire case")

    for cmd, want in PS_COMMA_CASES:
        got = bool(find_delete_hits(cmd, powershell=True))
        if got != want:
            failures.append("W2 (powershell=True) must %s: %r" % ("fire" if want else "not fire", cmd[:90]))
    if not any(c[1] for c in PS_COMMA_CASES) or not any(not c[1] for c in PS_COMMA_CASES):
        failures.append("W2 PS_COMMA_CASES lacks a must-fire or must-not-fire case")

    # evaluate_shell itself gates powershell off event["tool_name"], not a caller-supplied flag
    ev_bash_comma = {"tool_name": "Bash", "cwd": GFQW, "tool_input": {"command": "rm " + GFQW + "\\x,y"}}
    if any(r == RULE_ID for r, _ in evaluate_shell(ev_bash_comma)):
        failures.append("W2: evaluate_shell comma-split a literal Bash filename (tool_name=Bash)")
    ev_ps_comma = {"tool_name": "PowerShell", "cwd": GFQW,
                   "tool_input": {"command": "Remove-Item -LiteralPath '" + GFQW + "\\a\\x.png',y.png"}}
    if not any(r == RULE_ID for r, _ in evaluate_shell(ev_ps_comma)):
        failures.append("W2: evaluate_shell did not comma-split a PowerShell array (tool_name=PowerShell)")

    # evaluate_shell/decide agree with find_delete_hits
    ev = {"tool_name": "Bash", "cwd": GFQW, "tool_input": {"command": "rm -rf build"}}
    hits = evaluate_shell(ev)
    if not any(r == RULE_ID for r, _ in hits):
        failures.append("W2: evaluate_shell missed an obvious hit")
    verdict = decide(ev, [], hits, enforce=True)
    out = (verdict or {}).get("hookSpecificOutput", {})
    if out.get("permissionDecision") != "deny" or "W2" not in out.get("permissionDecisionReason", ""):
        failures.append("W2 did not deny by default on a real hit")
    else:
        import json
        try:
            json.dumps(verdict, ensure_ascii=True).encode("ascii")
        except UnicodeEncodeError:
            failures.append("deny output is not ASCII")
    if evaluate_tool({"tool_name": "Write", "tool_input": {"file_path": "x"}}):
        failures.append("W2 fired on a Write/Edit/MultiEdit/NotebookEdit call")

    # CLAUDE_WRITE_GUARD=log: stop denying, keep logging
    saved = _os.environ.get("CLAUDE_WRITE_GUARD")
    try:
        _os.environ["CLAUDE_WRITE_GUARD"] = "log"
        if _enforcing():
            failures.append("enforcing() was True with CLAUDE_WRITE_GUARD=log")
        if decide(ev, [], hits, enforce=_enforcing()) is not None:
            failures.append("W2 denied under CLAUDE_WRITE_GUARD=log")
        rows = log_rows(ev, [], hits, 1)
        if not rows:
            failures.append("W2 stopped logging under CLAUDE_WRITE_GUARD=log (it must not)")
        # 2026-09-28: the week-long log-only rollout needs root/cwd/target on every row
        elif not all(k in rows[0] for k in ("root", "cwd", "target")):
            failures.append("W2 log_rows is missing root/cwd/target")
        elif rows[0]["cwd"] != ev.get("cwd") or rows[0]["target"] != "build":
            failures.append("W2 log_rows root/cwd/target do not match the event")
    finally:
        if saved is None:
            _os.environ.pop("CLAUDE_WRITE_GUARD", None)
        else:
            _os.environ["CLAUDE_WRITE_GUARD"] = saved

    # deny-reason size (2026-09-30 review fix): each target listed once, MSG printed once, capped
    def _reason(cmd):
        e = {"tool_name": "Bash", "cwd": GFQW, "tool_input": {"command": cmd}}
        v = decide(e, [], evaluate_shell(e), enforce=True)
        return (v or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

    r1 = _reason("rm -f a")
    old_r1 = "W2: `%s` -> %s: %s - %s" % ("rm -f a", "a", "a relative path", MSG[RULE_ID])  # pre-fix format
    if not r1 or len(r1) > len(old_r1) or r1.count(MSG[RULE_ID]) != 1 or "-> a: a relative path" not in r1:
        failures.append("W2: 1-target reason must name the target once and be <= %d chars, got %d"
                        % (len(old_r1), len(r1)))
    r8 = _reason("rm -f a b c d e f g h")
    if len(r8) >= 1000 or r8.count(MSG[RULE_ID]) != 1:
        failures.append("W2: 8-target reason must carry MSG once and stay under 1000 chars, got %d" % len(r8))
    if not all(r8.count("-> %s: a relative path" % t) == 1 for t in "abcdefgh") or "EACH" not in r8:
        failures.append("W2: 8-target reason must list every target exactly once and say EACH: %r" % r8[:200])
    r12 = _reason("rm -f a b c d e f g h i j k l")
    if r12.count("  -> ") != MAX_LISTED or "+2 more" not in r12 or "-> k:" in r12:
        failures.append("W2: a 12-target reason must list %d targets then '+2 more'" % MAX_LISTED)
    rdup = _reason("rm -f a; rm -f a")
    if rdup.count("-> a: a relative path") != 1 or rdup.count(MSG[RULE_ID]) != 1:
        failures.append("W2: a target repeated in the same segment text must be listed once")
    if not (r1 + r8 + r12).isascii():
        failures.append("W2: the listed deny reason is not ASCII")

    for msg in MSG.values():
        try:
            msg.encode("ascii")
        except UnicodeEncodeError:
            failures.append("a W2 message is not ASCII")
        if "environment.md" in msg.lower():
            failures.append("a W2 message names a project-specific doc")

    return failures
