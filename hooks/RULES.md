# Global hook rules

What each hook in this folder checks, what it does when the check fires, and how to turn it on.
Every hook here is a plain Python script (standard library only) that Claude Code runs before a tool
call or at a session event. A hook never runs unless `~/.claude/settings.json` has an entry for it:
`python install.py --only hooks --hooks --apply` copies the code and
`python install.py --only hooks --wire-hooks --apply` adds those entries (see the README).

Contents: [Shared behaviour](#shared-behaviour) - [W2, W3, W5: the write guard](#w2-w3-w5-the-write-guard)
- [S1: the .claude size flag](#s1-the-claude-size-flag) - [M1: the Agent model reminder](#m1-the-agent-model-reminder) - [X3: no interpreter reading stdin](#x3-no-interpreter-reading-its-program-from-stdin)

## Shared behaviour

- **Fail open.** A hook that crashes, gets malformed input or cannot read a file lets the tool call
  through and prints nothing. A broken hook never blocks your work.
- **ASCII output** only, and nothing on stdout unless the hook has something to say.
- **Hit logs** are `*_hits.jsonl` files beside each script in `~/.claude/hooks/`, rotated at 5 MB. They
  are machine-local: git ignores them and `install.py` never copies them.
- **Self-tests:** every entry point takes `--self-test`, e.g.
  `python -I -S -B "<your home>/.claude/hooks/write_guard.py" --self-test`. Spell the home folder out:
  PowerShell does not expand `~` for Python.

## W2, W3, W5: the write guard

`write_guard.py` runs before Write, Edit, MultiEdit, NotebookEdit, Bash and PowerShell calls.

| Rule | Fires when | Why |
|---|---|---|
| W0 | a Bash/PowerShell command is too large to check inside the hook's 10 s timeout: over 131,072 characters, 1,000 top-level parts, 1,000 write/delete targets, 4,096 path separators across those targets, or 64 in any one target | past the timeout Claude Code lets the call through unchecked; none of 296,012 real commands measured on this machine comes near a cap |
| W2 | a delete names its target with a relative path, a variable, `~`, a command substitution, a pipe, or a wildcard anywhere but the last path segment | an agent deleting the wrong folder is the costliest mistake there is; a literal absolute path can be checked before it runs |
| W3 | a write lands outside the session's project folder (`CLAUDE_PROJECT_DIR`) and `~/.claude` | keeps each session inside its own project; everything else is read-only |
| W5 | a write lands in a system temp folder (`/tmp`, `%TEMP%`, `AppData\Local\Temp`) | keeps scratch work inside the project, where it can be found and cleaned up |
| W4 | (log-only, never denies) a hook or skill file is written without a review stamp | evidence for the review-phase rule |

**Log-only vs enforcing.** The shipped command runs `--log-only`: every hit is logged, nothing is
denied. To let rules deny, add `--enforce=W2,W3,W5` (or a subset) to that command in `settings.json`
and keep `--log-only` on the line; the `--enforce=` list then names the only rules that may deny.
Recommended: run log-only for about a week, read the hit log (`hooks/hits_summary.py` summarizes it),
then enforce. An unknown id in `--enforce=` is ignored and recorded, never a crash.
`CLAUDE_WRITE_GUARD=log` in the environment is the kill switch: nothing denies.

**W0 is different** (rules/execute/w0_too_large.py; user decision 2026-10-31: "Go with the size cap,
deny oversized commands"). It runs before every other rule and denies in every mode, including
`--log-only` and any `--enforce` list; it is not an `--enforce` id. It logs one `too-large` row (the
sizes, the caps and a 60-character head, never the full command). Under the kill switch it logs with
`denied:false` and does not deny: the switch means nothing denies, and whoever sets it accepts that an
oversized call may time the hook out unchecked. A denied call is fixed by splitting the command, or by
writing a script file with the Write tool inside the project's `.claude/` folder and running that.
Write/Edit/MultiEdit/NotebookEdit are not capped.

**Known gaps, low priority** (user decision, 2026-10-31: fix only if they show up in real use; none of
~296,000 real commands came near them, every slow case was built on purpose, and the guard is a
safety net against mistakes, not a defence against a hostile agent):
- `hooklib/paths.py` `resolve_real_prefix` resolves UNC and `//host` paths, which is a network
  round-trip: `touch //10.255.255.1/share/f` took about 21 s, past the 10 s hook timeout, so the call
  goes through unchecked. Fix if needed: never resolve UNC, `//host` or `\\?\UNC\` paths.
- W4 reads and hashes a copy's source file once per guarded destination (11.7 MB to 999
  destinations took 17 s). Fix if needed: hash each source once per call, with a byte budget.

## S1: the .claude size flag

`size_guard.py`. Rule (user, 2026-10-20): "if the agents local .claude directory gets bigger than
1gb, it needs to prune any temporary files and address the bloat", checked "whenever that directory
is greped or used". A hook can measure the folder and tell the agent, but it can't judge what is
temporary, so S1 only ever flags: it adds `additionalContext` to the conversation, never denies and
never deletes (user ruling, 2026-10-20: "a flag to the agent/PM to prune it. Not necessarily
blocking"). "Local" means the project's `.claude\` (`CLAUDE_PROJECT_DIR`, else `cwd`), never `~/.claude`.

- **Events:** SessionStart and UserPromptSubmit reach the main session. PreToolUse on
  Read/Grep/Glob/Write/Edit/MultiEdit/NotebookEdit/Bash/PowerShell fires only when the call's target is
  inside `<project>\.claude`, and reaches whoever made the call (main session or subagent). Subagents
  are told to report it and prune only if their brief allows writes.
- **The message:** the size, the 5 biggest subfolders, "prune `.claude\scratch\` first, by literal
  paths" (left out when scratch is empty), and "leave worktrees, metrics, docs, design, backups and
  tickets to the user".
- **Cache:** one state file beside the script (`size_guard_state.jsonl`, never inside a project). The
  folder is re-measured at most every 10 min, or every 2 min while it is over the limit or after a walk
  the 3 s cap cut short. `--limit-mb=`, `--ttl-minutes=`, `--over-ttl-minutes=` and `--budget-seconds=`
  override these.
- **How often:** per project and recipient, at most once per 10-min window while pruning scratch
  alone could bring the folder under the limit; once per session when even deleting all of scratch
  could not (user ruling, 2026-10-20). A SessionStart after `/clear` or a compaction flags the main
  session again, since the earlier flag has left its context.
- **On by default** (user ruling, 2026-10-20: no log-only week). `--log-only` on the command, or
  `CLAUDE_SIZE_GUARD=log`, records rows without showing the flag. Rows go to `size_guard_hits.jsonl`.
- **Measured 2026-10-20:** a full walk takes 30-70 ms warm, up to 660 ms cold (6,388 files). The hook
  costs about 70-95 ms per matched call when its pre-filter exits early, 120-190 ms when it measures.

## M1: the Agent model reminder

`model_guard.py`, PreToolUse on the `Agent` tool. Rule (user, 2026-10-21): pick the cheapest model
that does the job well and state the model and why in the brief (the tiers are in the global
`CLAUDE.md`). Whether a tier FITS a task is a judgment no hook can make, but whether the dispatcher
picked one at all is visible. M1 fires when nothing picks a model:
- no `model` on the Agent call,
- no agent definition (project `.claude/agents`, a parent of the cwd, or `~/.claude/agents`) with a
  `model:` other than `inherit`,
- and no `CLAUDE_CODE_SUBAGENT_MODEL`.

The subagent then runs on the main session's model (Claude Code's documented order: the call's
`model`, the definition's frontmatter, that variable, the main model). A "Model: Haiku" line in the
brief does not change that, so M1 flags it. M1 stays silent for an agent type it cannot verify (unknown
or from a plugin) and for `claude-code-guide` and `statusline-setup`, which carry their own models. A
project or user definition that reuses a built-in name (say `Explore` with `model: haiku`) overrides
the built-in and is honoured; a `.md` file with no `---` header is not a definition.

- **Advisory only:** no `permissionDecision`, never denies. The default is log-only (rows to
  `model_guard_hits.jsonl`, nothing shown). Add `--advise` to the command to show the reminder as
  `additionalContext`. `CLAUDE_MODEL_GUARD=log` forces log-only.
- **Not built:** a tier-mismatch check ("haiku on a prompt about hooks/delete/security", "opus on a
  lookup"). Replayed over 1,247 real Agent calls, 4 of the 18 Haiku calls matched and all 4 were false
  positives.
- **Measured replay (2026-10-21):** 48 fires in 1,247 Agent calls (3.8%), 5 of 5 hand-checked hits
  real: general-purpose, Explore and Plan calls with no model, which inherit the main session's.

## X3: no interpreter reading its program from stdin

`stdin_guard.py`, PreToolUse on Bash and PowerShell. Rule (user, 2026-10-31: "100% CPU util is not
acceptable"; global `CLAUDE.md` Working habits: no heredocs; `python -` has the same stdin hang). An interpreter told to
read its program from stdin can hang there and spin a core for hours (2026-10-31: three leftover
`python -` processes did).

**Enforced** (decision 2026-11-01, asked whether to enforce, the user said "Your
choice"; the session chose enforce because agents kept running `python -`). The shipped
command carries `--enforce=X3`. X3 has two tiers:

| Tier | Fires on | Effect |
|---|---|---|
| deny (`dash`) | `-` as the program of python / python3 / python3.13 / py / pythonw / pypy: `python -`, `python -X utf8 -`, `py -3 -`, `cat x.py \| python -` | denied |
| deny (`heredoc`) | an interpreter (python family, node, ruby, perl, bash / sh / zsh / dash, powershell / pwsh) that names no other program and reads a heredoc, a bash `<<<` here-string, or a pipe from a heredoc or PowerShell here-string: `python <<EOF`, `bash <<'EOF'`, `cat <<EOF \| python`, `@'...'@ \| python` | denied |
| log (`no-source`) | the rest: a bare `python` or `node`, `echo x \| python`, `curl x \| bash`, `bash -s`, `node -` | logged, let through |

Passes: `python -c`, `python -m`, `python -X utf8 script.py`, `python file.py`, a `-` that belongs to the
script or module (`python script.py -`, `python -m json.tool -`), a heredoc feeding a script's stdin
(`python script.py <<EOF`), version and help calls. The deny message: "python - / heredoc to an
interpreter hangs on stdin; write the script with the Write tool into the project's .claude\scratch and
run it with a timeout".

- **Switches:** without `--enforce=X3` on the command nothing denies; `CLAUDE_STDIN_GUARD=log` in the
  environment forces log-only (the kill switch). A parse or rule error fails open.
- **Hit log:** `<project>/.claude/hooks/log/stdin_guard_hits.jsonl`, written only when that project's
  `.claude` exists; nothing is logged without `CLAUDE_PROJECT_DIR`. Rows carry `kind` and `denied`.
- **Measured before enforcing (2026-11-01, one project):** live log 31 hits (2026-11-01 04:14-12:00 UTC),
  29 true positives, 2 false positives (`timeout 600 python $(basename $f)`, which parses as a bare
  `python`). Replay over all 7,446 shell calls in the project's transcripts (2026-10-31 and 11-01, 174
  files): 81 hits; the enforced rule denies 79, every one a hand-checked `python ... -` (58 with a heredoc,
  21 without); the 2 false positives fall in the log tier and are not denied. False denies: 0.
- **Cost:** about 70-100 ms per shell call, almost all interpreter start-up (2026-10-31); the check
  itself 0.62 ms per call before the change, 0.65 ms after (in-process, 500 calls, 2026-11-01).
- **Known misses:** `powershell -Command -` (the parser expands `-Command` and drops the `-`), and a
  here-string held in a variable and piped later (`$s | python`, log tier only), and wrappers the
  parser does not look through (`xargs python -`, `uv run python -`). **Known false positives**, log
  tier only: flag-only node calls such as `node --test`, `py -0p` / `py --list`, and `python $(cat f)`.
