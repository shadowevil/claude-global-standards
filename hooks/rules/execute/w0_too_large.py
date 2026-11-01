"""W0: a Bash/PowerShell command too large for the write guard to check within its time limit is
denied (user decision, WB-0055, 2026-10-31: "Go with the size cap, deny oversized commands").

Why: the hook runs with `timeout: 10` in settings.json, and Claude Code lets a call through UNCHECKED
once a hook times out. The rules' cost grows with the command (WB-0009 made tokenizing linear, but
every write/delete target is still resolved against the filesystem by W3, W4 and W5), so a big
enough command would bypass every rule. W0 runs FIRST, in write_guard.py,
before any rule module parses the command, and denies in every mode - `--log-only` and any
`--enforce=` list included - because under those modes a timeout would let through a call that the
enforced rules would otherwise have judged. Only the CLAUDE_WRITE_GUARD=log kill switch stops it
denying (the row is still logged), since that switch's meaning is "nothing denies".

Caps, checked cheapest first; each is the largest value still ALLOWED (over = strictly more):

    MAX_CHARS              characters in the command             len(), free
    MAX_PARTS              top-level parts of the brace-masked   one linear pass (W3's own split)
                           command
    MAX_TARGETS            write/delete targets                  a parse without path resolution, in
                                                                 both views the rules use (unmasked:
                                                                 W2/W4; brace-masked: W3/W5), larger
                                                                 count wins; cached for the rules
    MAX_SEPARATORS         / and \\ across all those targets     a count over the parsed targets
    MAX_TARGET_SEPARATORS  / and \\ in any one target            the same pass

Why separators: os.path.realpath on a path that does not exist steps back one level at a time,
each step a failed filesystem call whose cost grows with the path's length, and W3, W4 and W5 each
resolve every target. hooklib.paths.resolve_real_prefix shares the work between them through an
lru_cache of 128, so a command with more than 128 targets pays it three times. Cost therefore
grows with levels x path length per target, which neither the character nor the target cap bounds
(review probes, WB-0055: 10 targets of 6,400 levels in 128 KiB took 16-22 s; 3 targets of 1,363
long levels plus 997 cheap ones, under the total-separator cap, took 9.1 s). The total cap bounds
the step count; the per-target cap bounds steps x length, since that is at most
MAX_TARGET_SEPARATORS x MAX_CHARS.

Evidence (WB-0055, 2026-10-31; 296,012 unique real Bash/PowerShell commands from every session
transcript on this machine, measured): max 39,847 characters (p99.99 17,711), max 202 parts
(p99.99 72), max 183 targets (p99.99 48), max 1,818 separators in a command (p99.99 599), max 28
in one target (p99.99 16) - no real command is over any cap. The caps sit 3.3x / 5x / 5.5x /
2.25x / 2.3x above the largest real command. The separator cap is the binding one for absolute
paths: at about 14 levels per in-project path it allows about 290 such targets per command.
Worst just-under-cap case measured (subprocess, live argv, min of 3, 2026-10-31): 2.7 s
(PowerShell, 62 targets of 62 levels x 31-char names plus 900 cheap targets); the 1,000-target
cases 1.1-2.3 s depending on machine load. Over-cap denies take about 0.3 s.

Remaining gap (WB-0055, not fixed here): W4 reads and hashes a copy's SOURCE file once per guarded
destination (w4_review_stamp.py, the COPY_VERBS branch). Copy-Item of an 11.7 MB file to 999
destinations under .claude\\hooks took 17.0 s while under every cap, and a single cp of a multi-GB
file needs no repetition. Suggested fix, in W4: hash each source once per call, plus a budget on
total bytes hashed (over it: a W0-style deny).

Write/Edit/MultiEdit/NotebookEdit are NOT capped: the rules judge only their file path (W3, W5) and,
for a guarded .claude/hooks or skills path, hash the resulting content once (W4) - linear and cheap
at any size, so a large file write cannot time the hook out.

The hit row carries the measured sizes and a short head of the command, never the command in full.
"""
from hooklib import paths
from hooklib import shell
from hooklib.output import excerpt
from rules.write import w3_outside_root as w3

RULE_ID = "W0"

MAX_CHARS = 131072
MAX_PARTS = 1000
MAX_TARGETS = 1000
MAX_SEPARATORS = 4096
MAX_TARGET_SEPARATORS = 64
HEAD_CHARS = 60

SHELL_TOOLS = ("Bash", "PowerShell")

MSG = ("W0: this command is too large for the write guard to check within its time limit, so it is "
       "denied rather than let through unchecked. Split it into smaller commands, or write it to a "
       "script file with the Write tool (inside the project's .claude/ folder) and run that file.")


def _depths(targets):
    return [row[2].count("/") + row[2].count("\\") for row in targets]


def measure(cmd, powershell=False):
    """(chars, parts, targets, separators, deepest), cheapest first; a later count is None when an
    earlier cap was already exceeded, so an oversized command is never parsed further. `separators`
    is the total number of / and \\ across the write/delete targets, `deepest` the most in any one.

    Parts are counted on the brace-masked command, as W3 and W5 split it (`{a,b}0` is one word, not
    a group cut at its comma). Targets and separators are the larger of the two views the rules
    parse: unmasked (W2, W4: find_write_targets) and brace-masked (W3, W5) - a brace word is a
    target only in the masked view, so counting one view alone would let a brace-word target list
    past the target cap."""
    chars = len(cmd)
    if chars > MAX_CHARS:
        return chars, None, None, None, None
    parts = len(shell.split_top_level(w3.mask_brace_words(cmd)))
    if parts > MAX_PARTS:
        return chars, parts, None, None, None
    plain = w3.find_write_targets(cmd, powershell)
    masked = w3.find_write_targets_cwd(cmd, powershell, None, brace_words=True)
    targets = max(len(plain), len(masked))
    if targets > MAX_TARGETS:
        return chars, parts, targets, None, None
    dp, dm = _depths(plain), _depths(masked)
    return chars, parts, targets, max(sum(dp), sum(dm)), max(dp + dm + [0])


def check(event):
    """None when the call is within every cap (or is not a shell call), else
    {"chars", "parts", "targets", "separators", "deepest", "over": [text, ...], "head"} describing
    what was exceeded."""
    tool = event.get("tool_name") or ""
    if tool not in SHELL_TOOLS:
        return None
    cmd = (event.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str):
        return None
    chars, parts, targets, separators, deepest = measure(cmd, tool == "PowerShell")
    over = []
    if chars > MAX_CHARS:
        over.append("%d characters (cap %d)" % (chars, MAX_CHARS))
    if parts is not None and parts > MAX_PARTS:
        over.append("%d top-level parts (cap %d)" % (parts, MAX_PARTS))
    if targets is not None and targets > MAX_TARGETS:
        over.append("%d write/delete targets (cap %d)" % (targets, MAX_TARGETS))
    if separators is not None and separators > MAX_SEPARATORS:
        over.append("%d path separators across the write/delete targets (cap %d)"
                    % (separators, MAX_SEPARATORS))
    if deepest is not None and deepest > MAX_TARGET_SEPARATORS:
        over.append("%d path separators in one write/delete target (cap %d)"
                    % (deepest, MAX_TARGET_SEPARATORS))
    if not over:
        return None
    return {"chars": chars, "parts": parts, "targets": targets, "separators": separators,
            "deepest": deepest, "over": over, "head": excerpt(cmd[:HEAD_CHARS * 4])[:HEAD_CHARS]}


def _ascii(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def reason(info):
    return "%s Measured: %s." % (MSG, "; ".join(info["over"]))


def log_rows(event, info, ms):
    """One `too-large` row: sizes, caps and a short head - never the command text in full."""
    return [{"rule": RULE_ID, "tool": event.get("tool_name"), "root": paths.project_root(event),
             "cwd": event.get("cwd"), "target": None,
             "sample": [_ascii("too-large: %s | head: %s" % ("; ".join(info["over"]), info["head"]))],
             "chars": info["chars"], "parts": info["parts"], "targets": info["targets"],
             "separators": info["separators"], "deepest": info["deepest"],
             "caps": {"chars": MAX_CHARS, "parts": MAX_PARTS, "targets": MAX_TARGETS,
                      "separators": MAX_SEPARATORS, "deepest": MAX_TARGET_SEPARATORS},
             "session": event.get("session_id"), "ms": ms, "msg": MSG}]


def deny(info):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": _ascii(reason(info))}}


# ------------------------------------------------------------------ self-test

N_CASES = 0  # set by run_self_test(); write_guard.self_test() adds it to its printed total


def _cmd_chars(n, powershell):
    head = "Write-Output " if powershell else "echo "
    return head + "x" * (n - len(head))


def _cmd_parts(n, powershell):
    return "; ".join(("Write-Output %d" if powershell else "echo %d") % i for i in range(n))


def _cmd_targets(n, powershell):
    if powershell:
        return "Remove-Item -LiteralPath " + ",".join("'f%d'" % i for i in range(n))
    return "touch " + " ".join("f%d" % i for i in range(n))


def _cmd_deepest(n, powershell):
    """One write target carrying exactly n path separators (nothing else in the command has any)."""
    if powershell:
        return "Remove-Item -LiteralPath '" + "a\\" * n + "f'"
    return "touch " + "a/" * n + "f"


def _cmd_separators(n, powershell):
    """Exactly n path separators in all, spread over targets none deeper than the per-target cap."""
    k = -(-n // MAX_TARGET_SEPARATORS)
    base, extra = divmod(n, k)
    levels = [base + (1 if i < extra else 0) for i in range(k)]
    if powershell:
        return "Remove-Item -LiteralPath " + ",".join("'" + "a\\" * lv + "f%d'" % i
                                                      for i, lv in enumerate(levels))
    return "touch " + " ".join("a/" * lv + "f%d" % i for i, lv in enumerate(levels))


def _deep(prefix, n_targets, levels):
    return "touch " + " ".join(prefix + "a/" * levels + "f%d" % i for i in range(n_targets))


def _brace_self_test(failures, ev):
    """Brace words (WB-0055 review): counted as W3 splits them - one part per brace word, not one
    per comma - and a brace word is a target, although only the masked view sees it as one."""
    n = 0
    touch900 = "touch " + " ".join("{a,b}%d" % i for i in range(900))
    echo_many = "echo" + " {a,b}" * (MAX_PARTS + 1)
    touch_over = "touch " + " ".join("{a,b}%d" % i for i in range(MAX_TARGETS + 1))
    for tool in SHELL_TOOLS:
        ps = tool == "PowerShell"
        got = measure(touch900, ps)
        n += 2
        # PowerShell's comma-array split (W4's unmasked view) makes each {a,b}N two targets there,
        # 1800 in all, which W4 then resolves - so it is over the target cap, correctly.
        want = 1800 if ps else 900
        if got[1] != 1 or got[2] != want:
            failures.append("W0 brace: %s touch {a,b}0..899 must measure 1 part, %d targets, got %r"
                            % (tool, want, got))
        if (check(ev(tool, {"command": touch900})) is None) != (not ps):
            failures.append("W0 brace: %s touch {a,b}0..899 must be %s"
                            % (tool, "denied on targets" if ps else "allowed"))
        n += 2
        if len(shell.split_top_level(echo_many)) <= MAX_PARTS:
            failures.append("W0 brace setup: the unmasked split must see > %d parts" % MAX_PARTS)
        if check(ev(tool, {"command": echo_many})) is not None:
            failures.append("W0 brace: %s %d brace words in one echo must be allowed (1 part)"
                            % (tool, MAX_PARTS + 1))
        info = check(ev(tool, {"command": touch_over}))
        n += 1
        if info is None or not any("targets" in o for o in info["over"]):
            failures.append("W0 brace: %s %d brace-word targets must be denied on the target cap, got %r"
                            % (tool, MAX_TARGETS + 1, info and info["over"]))
    return n


def _separators_self_test(failures, ev, root):
    """Deep paths (WB-0055 review): path resolution steps back one level at a time through a path
    that does not exist, each step costing in proportion to the path's length, so cost grows with
    the levels (in total, and per target times its length), not with targets or characters."""
    n = 0
    fwd_root = root.replace("\\", "/")
    over_total = _cmd_separators(MAX_SEPARATORS + 1, False)
    long_mix = ("touch " + " ".join("C:/x/" + ("n" * 20 + "/") * 1363 + "f%d" % i for i in range(3))
                + " " + " ".join("g%d" % i for i in range(997)))
    must_fire = (  # (label, command, word the reason must carry)
        ("review probe: 10 x 6400 levels outside", _deep("C:/x/", 10, 6400), "across"),
        ("review probe: 10 x 6400 levels under the project root", _deep(fwd_root + "/", 10, 6400),
         "across"),
        ("review probe: 1000 x 60 levels", _deep("C:/x/", 1000, 60), "across"),
        ("total just over the cap, no target over the per-target cap", over_total, "across"),
        ("PowerShell comma list over the total cap", _cmd_separators(MAX_SEPARATORS + 1, True), "across"),
        ("one target one level over the per-target cap", _deep("C:/x/", 1, MAX_TARGET_SEPARATORS - 1),
         "in one"),
        ("a redirect target over the per-target cap",
         "echo x > C:/x/" + "a/" * MAX_TARGET_SEPARATORS + "f", "in one"),
        ("3 long deep targets + 997 cheap ones (9 s unchecked)", long_mix, "in one"),
    )
    for label, cmd, word in must_fire:
        tool = "PowerShell" if cmd.startswith("Remove-Item") else "Bash"
        info = check(ev(tool, {"command": cmd}))
        n += 1
        if info is None or not any(word in o for o in info["over"]):
            failures.append("W0 separators: %s must be denied naming '%s', got %r"
                            % (label, word, info and info["over"]))
    n += 1
    got = measure(over_total)
    if got[3] != MAX_SEPARATORS + 1 or got[4] > MAX_TARGET_SEPARATORS:
        failures.append("W0 separators setup: the over-total case must measure %d in all and no "
                        "target over %d, got %r" % (MAX_SEPARATORS + 1, MAX_TARGET_SEPARATORS, got))
    real_like = "cp -r " + " ".join("src/m%d/a/b/c/d/e/f/g/h/i/j/k.txt" % i for i in range(183)) + " dst/"
    must_not_fire = (
        ("at the cap, 1000 targets", "touch " + " ".join(
            "C:/x/" + "a/" * ((MAX_SEPARATORS - 2000) // 1000 + (1 if i < (MAX_SEPARATORS - 2000) % 1000 else 0))
            + "f%d" % i for i in range(1000))),
        ("183 targets of 12 levels (above the largest real command)", real_like),
        ("slashes outside any target", 'python -c "' + "/" * (MAX_SEPARATORS * 2) + '"'),
        ("slashes in a read-only argument", "cat " + "a/" * (MAX_SEPARATORS * 2) + "f"),
    )
    for label, cmd in must_not_fire:
        got = measure(cmd)
        n += 1
        if check(ev("Bash", {"command": cmd})) is not None:
            failures.append("W0 separators: %s must be allowed, measured %r" % (label, got))
    n += 1
    if measure(must_not_fire[0][1])[3] != MAX_SEPARATORS:
        failures.append("W0 separators setup: the at-cap case must measure exactly %d, got %r"
                        % (MAX_SEPARATORS, measure(must_not_fire[0][1])))
    return n


def run_self_test():
    """Just under / at / over each cap, both shells; plus non-shell tools and the row/deny shape."""
    global N_CASES
    failures = []
    n = 0
    root = w3.ROOT

    def ev(tool, ti):
        return {"tool_name": tool, "cwd": root, "session_id": "w0-selftest", "tool_input": ti}

    for tool in SHELL_TOOLS:
        ps = tool == "PowerShell"
        for dim, cap, build, idx, word in (
                ("chars", MAX_CHARS, _cmd_chars, 0, "characters"),
                ("parts", MAX_PARTS, _cmd_parts, 1, "parts"),
                ("targets", MAX_TARGETS, _cmd_targets, 2, "targets ("),
                ("separators", MAX_SEPARATORS, _cmd_separators, 3, "across"),
                ("deepest", MAX_TARGET_SEPARATORS, _cmd_deepest, 4, "in one")):
            for size in (cap - 1, cap, cap + 1):
                cmd = build(size, ps)
                got = measure(cmd, ps)
                n += 1
                if got[idx] != size:
                    failures.append("W0 setup: %s %s=%d measured as %r" % (tool, dim, size, got))
                    continue
                info = check(ev(tool, {"command": cmd}))
                n += 1
                if size <= cap and info is not None:
                    failures.append("W0: %s %s=%d (cap %d) must be allowed, got %r"
                                    % (tool, dim, size, cap, info["over"]))
                if size > cap:
                    if info is None:
                        failures.append("W0: %s %s=%d (cap %d) must be denied" % (tool, dim, size, cap))
                    elif not any(word in o for o in info["over"]):
                        failures.append("W0: %s %s=%d reason must name %s, got %r"
                                        % (tool, dim, size, dim, info["over"]))
    # an oversized command is never parsed further (parts/targets/separators skipped)
    n += 1
    if measure(_cmd_chars(MAX_CHARS + 1, False))[1:] != (None, None, None, None):
        failures.append("W0: over the char cap must skip the part/target/separator counts")
    n += _brace_self_test(failures, ev)
    n += _separators_self_test(failures, ev, root)
    # non-shell tools and malformed input are never W0, however large
    big = "x" * (MAX_CHARS * 4)
    for tool, ti in (("Write", {"file_path": root + "\\a.txt", "content": big}),
                     ("Edit", {"file_path": root + "\\a.txt", "old_string": big, "new_string": big}),
                     ("MultiEdit", {"file_path": root + "\\a.txt", "edits": [{"old_string": big}]}),
                     ("Bash", {}), ("Bash", {"command": None}), ("PowerShell", {"command": 12})):
        n += 1
        if check(ev(tool, ti)) is not None:
            failures.append("W0: %s %r must never be W0" % (tool, sorted(ti)))
    # row and deny shape: ASCII, sizes present, never the full command, reason says what to do
    secret_tail = "TAILMARKER"
    cmd = _cmd_chars(MAX_CHARS + 10, False) + " " + secret_tail
    e = ev("Bash", {"command": cmd})
    info = check(e)
    rows = log_rows(e, info, 3) if info else []
    d = deny(info) if info else {}
    import json
    blob = json.dumps(rows) + json.dumps(d)
    n += 4
    if not rows or rows[0]["rule"] != RULE_ID or rows[0]["chars"] != len(cmd):
        failures.append("W0: the row must be rule W0 carrying the measured size, got %r" % rows[:1])
    if secret_tail in blob or len(blob) > 4000:
        failures.append("W0: the row/deny must never carry the command in full")
    if not blob.isascii():
        failures.append("W0: the row/deny must be ASCII")
    r = d.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    if "Write tool" not in r or "Split" not in r or str(MAX_CHARS) not in r:
        failures.append("W0: the reason must give the size, the cap and what to do, got %r" % r[:200])
    N_CASES = n
    return failures
