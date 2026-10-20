"""W5: no writes into the system temp folder (user rule, 2026-10-18: "global block with a message
stating to keep files local to the project inside the local .claude and subfolders").

A write target that resolves into a system temp directory - /tmp/**, %TEMP% / %TMP% / $TMPDIR
(resolved), <home>\\AppData\\Local\\Temp\\** including the Claude harness scratchpad under
Temp\\claude\\ - is reported as W5 and denied when W5 is enforced. Reads are never judged: only the
write targets W3's parser extracts (redirects, tee, cp/mv/mkdir/touch, PowerShell write cmdlets,
deletes, Write/Edit/MultiEdit/NotebookEdit) are looked at.

A target inside an allowed root (the project root, ~/.claude, a user-granted additional directory)
is local by definition and never W5, even when that root itself sits under Temp.

W5 SUPERSEDES W3: write_guard drops a W3 row for any target W5 reported in the same event, so a temp
write is one W5 row, not a W5 row plus a W3 row.
"""
import re

from hooklib import paths
from hooklib import shell
from hooklib.output import excerpt
from rules.write import w3_outside_root as w3

RULE_ID = "W5"
SUPERSEDES = ("W3",)

MSG = ("W5: writes to the system temp folder are blocked. Keep files local to the project, inside "
       "its .claude/ folder and subfolders (user rule, 2026-10-18).")

# a target that starts with a temp-folder variable: $TEMP/x, ${TMP}/x, $TMPDIR, $env:TEMP\x, %TMP%\x
TEMP_VAR_PREFIX = re.compile(
    r"^(?:\$\{?(?:TEMP|TMP|TMPDIR)\}?|\$env:(?:TEMP|TMP|TMPDIR)|%(?:TEMP|TMP|TMPDIR)%)(?:[\\/]|$)",
    re.IGNORECASE)


def temp_target(raw, event, cwd=None):
    """The resolved temp path if `raw` (a literal, statically resolvable target) lands in a system
    temp directory outside every allowed root, else None."""
    target = paths.resolve(raw, event, cwd)
    if not paths.is_temp(target):
        return None
    if any(paths.is_inside(r, target) for r in paths.allowed_roots(event)):
        return None
    return target


def evaluate_tool(event):
    """[(raw_target, resolved_temp_path), ...] for a Write/Edit/MultiEdit/NotebookEdit event."""
    out = []
    for t in w3.w3_tool_targets(event.get("tool_name") or "", event.get("tool_input") or {}):
        hit = temp_target(t, event)
        if hit is not None:
            out.append((t, hit))
    return out


def evaluate_shell(event):
    """[(RULE_ID, (segment, verb, raw_target, resolved_or_variable)), ...] for a Bash/PowerShell
    event. A target behind a TEMP/TMP/TMPDIR variable counts even though it is not resolvable."""
    hits = []
    tool = event.get("tool_name") or ""
    if tool not in ("Bash", "PowerShell"):
        return hits
    cmd = (event.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return hits
    for seg, verb, raw, seg_cwd in w3.find_write_targets_cwd(cmd, tool == "PowerShell",
                                                             event.get("cwd"), brace_words=True):
        seg, raw = w3.unmask_brace_words(seg), w3.unmask_brace_words(raw)
        glued = w3.reglue(seg, raw)
        kind, judged = w3.classify_target(glued)
        if kind == "skip":
            continue
        if kind == "unresolved":
            if TEMP_VAR_PREFIX.match(glued.strip().replace("\"", "").replace("'", "")):
                hits.append((RULE_ID, (seg, verb, raw, glued)))
            continue
        if seg_cwd is shell.UNRESOLVED_CWD and w3.is_relative(judged):
            continue
        hit = temp_target(judged, event, seg_cwd if isinstance(seg_cwd, str) else None)
        if hit is not None:
            hits.append((RULE_ID, (seg, verb, raw, hit)))
    return hits


def log_rows(event, tool_hits, shell_hits, ms):
    root = paths.project_root(event)
    rows = []
    pairs = [(raw, bad) for raw, bad in tool_hits] + [(d[2], d[3]) for _r, d in shell_hits]
    for raw, bad in pairs:
        rows.append({"rule": RULE_ID, "tool": event.get("tool_name"), "root": root,
                     "cwd": event.get("cwd"), "target": raw,
                     "sample": [excerpt("%s -> %s (system temp folder)" % (raw, bad))],
                     "session": event.get("session_id"), "ms": ms, "msg": MSG})
    return rows


def decide(event, tool_hits, shell_hits, enforce):
    if not enforce:
        return None
    targets = [raw for raw, _bad in tool_hits] + [d[2] for _r, d in shell_hits]
    if not targets:
        return None
    reason = MSG + " Target: " + ", ".join("`%s`" % excerpt(t) for t in targets)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


# ------------------------------------------------------------------ self-test

ROOT = w3.ROOT


def _temp_long():
    import os
    import tempfile
    return paths.expand_short_names(os.path.normpath(tempfile.gettempdir())).rstrip("\\")


def _temp_short():
    import tempfile
    return tempfile.gettempdir().rstrip("\\")


SID = "5abc868e-b44c-4092-b407-b7e455c39e94"
SLUG = "C--Users-ShadowEvil-source-proj"

# (tool, tool_input or command, must fire) - temp paths are filled in at run time
SHELL_CASES = [
    ("Bash", "echo x > /tmp/x", True),
    ("Bash", "cp a.txt /tmp/b/c.txt", True),
    ("Bash", "mkdir -p /tmp/work", True),
    ("Bash", "echo x > $TEMP/x", True),
    ("Bash", "echo x > ${TMP}/x", True),
    ("Bash", "echo x > \"$TMPDIR\"/x", True),
    ("Bash", "cd /tmp && cat > x <<'EOF'\nhi\nEOF", True),
    ("Bash", "cd /tmp; echo x > sub/y.txt", True),
    ("Bash", "rm -f /tmp/v_*.out", True),
    ("Bash", "copy a.txt %TEMP%\\x.txt", True),
    ("PowerShell", "Set-Content -Path $env:TEMP\\x.txt -Value 1", True),
    ("PowerShell", "Out-File -FilePath %TMP%\\x.txt", True),
    ("Bash", "echo x > {TEMP}", False),                          # not a variable
    ("Bash", "echo x > $TEMPLATE/x", False),                     # a different variable
    ("Bash", "echo x > $D/x", False),                            # unresolved, not temp: W3's job
    ("Bash", "cat /tmp/x", False),                               # a read
    ("Bash", "grep foo /tmp/x > " + ROOT + "\\out.txt", False),  # reads temp, writes the project
    ("Bash", "cp /tmp/x " + ROOT + "\\x", False),                # copy FROM temp
    ("Bash", "echo x > C:\\proj\\temp\\x", False),               # a project dir named temp
    ("Bash", "echo x > C:\\tmp\\x", False),                      # C:\tmp is not the temp dir
    ("Bash", "echo x > " + ROOT + "\\temp\\x.txt", False),
    ("Bash", "echo x > /dev/null", False),
    ("Bash", "cd /tmp && cat x", False),                          # cd then read only
]
N_RUNTIME_CASES = 14


def run_self_test():
    failures = []

    def fire(tool, ti, cwd=ROOT, sid=None):
        ev = {"tool_name": tool, "cwd": cwd, "tool_input": ti}
        if sid:
            ev["session_id"] = sid
        return bool(evaluate_tool(ev) or evaluate_shell(ev))

    for tool, cmd, want in SHELL_CASES:
        if fire(tool, {"command": cmd}) != want:
            failures.append("W5 must %s: %r" % ("fire" if want else "not fire", cmd[:90]))

    tl, ts = _temp_long(), _temp_short()
    scratch = "\\claude\\" + SLUG + "\\" + SID + "\\scratchpad\\notes.txt"
    runtime = [
        ("Write", {"file_path": tl + "\\x.md"}, True),
        ("Write", {"file_path": ts + "\\x.md"}, True),                 # 8.3 form
        ("Write", {"file_path": tl + scratch}, True),                  # own harness scratchpad
        ("Write", {"file_path": ts + scratch}, True),
        ("Edit", {"file_path": tl + "\\claude\\other\\tasks\\y"}, True),
        ("Bash", {"command": "echo x > \"" + tl + scratch + "\""}, True),
        ("Bash", {"command": "echo x > " + ts.replace("\\", "/") + "/y.txt"}, True),
        ("NotebookEdit", {"notebook_path": tl + "\\n.ipynb"}, True),
        ("Write", {"file_path": "C:\\proj\\temp\\x"}, False),
        ("Write", {"file_path": ROOT + "\\src\\x.cpp"}, False),
        ("Write", {"file_path": tl + "x\\sibling.txt"}, False),        # Temp-something sibling
        ("Bash", {"command": "cat \"" + tl + scratch + "\""}, False),  # a read
    ]
    for tool, ti, want in runtime:
        if fire(tool, ti, sid=SID) != want:
            failures.append("W5 must %s: %s %r" % ("fire" if want else "not fire", tool, ti))
    # a project whose root itself lives under Temp: writes inside it are local, outside are not
    inner_root = tl + "\\claude\\" + SLUG + "\\" + SID + "\\scratchpad\\agentwork"
    if fire("Write", {"file_path": inner_root + "\\a.txt"}, cwd=inner_root):
        failures.append("W5 fired inside a project root that sits under Temp")
    if not fire("Write", {"file_path": tl + "\\b.txt"}, cwd=inner_root):
        failures.append("W5 missed a temp write outside a project root that sits under Temp")

    # decide: deny only when enforced, ASCII, carries the message and the target
    ev = {"tool_name": "Bash", "cwd": ROOT, "tool_input": {"command": "echo x > /tmp/x"}}
    sh = evaluate_shell(ev)
    if decide(ev, [], sh, enforce=False) is not None:
        failures.append("W5 denied while not enforced")
    v = (decide(ev, [], sh, enforce=True) or {}).get("hookSpecificOutput", {})
    reason = v.get("permissionDecisionReason", "")
    if v.get("permissionDecision") != "deny" or MSG not in reason or "/tmp/x" not in reason:
        failures.append("W5 enforced deny lacks the message or the target: %r" % reason[:120])
    try:
        (MSG + reason).encode("ascii")
    except UnicodeEncodeError:
        failures.append("W5 message is not ASCII")
    if not log_rows(ev, [], sh, 1):
        failures.append("W5 wrote no log row for a hit")
    return failures


N_CASES = N_RUNTIME_CASES + 4  # + the decide/log checks
