# Global hook rules

What each hook in this folder checks, what it does when the check fires, and how to turn it on.
Every hook here is a plain Python script (standard library only) that Claude Code runs before a tool
call or at a session event. A hook never runs unless `~/.claude/settings.json` has an entry for it:
`python install.py --only hooks --hooks --apply` copies the code and
`python install.py --only hooks --wire-hooks --apply` adds those entries (see the README).

Contents: [Shared behaviour](#shared-behaviour) - [W2, W3, W5: the write guard](#w2-w3-w5-the-write-guard)
- [S1: the .claude size flag](#s1-the-claude-size-flag) - [M1: the Agent model reminder](#m1-the-agent-model-reminder)

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
