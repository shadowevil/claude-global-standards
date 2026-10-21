# claude-global-standards

A machine-wide setup for [Claude Code](https://claude.com/claude-code): global instructions, skills
and hooks that make every Claude session and agent on a machine work the same disciplined way, plus
skills that set up new projects and bring existing projects in line.

It grew out of a real multi-agent game project and is shaped by measurement, not taste. Analysing
about 4,600 session transcripts showed where agent time and tokens actually go:
- **Navigation** was 37% of turns and 67% of what got pulled into context.
- **Large single-file docs** were 14% of everything agents read.
- **Hook denials, heredoc quoting failures and PowerShell quirks** cost about 5% of tool calls.

The rules here target those costs directly.

## What's in it

| Path | Installs to | What it does |
|---|---|---|
| `global/CLAUDE.md` | `~/.claude/CLAUDE.md` | Loaded into every session and agent: safety rails (write scope, literal delete paths, git), working habits, dispatching rules, the lean-index docs rule |
| `global/docs/documentation_standards.md` | `~/.claude/docs/` | How to structure docs as lean index files plus sidecar folders, so a lookup is one small index read plus one targeted read |
| `skills/project-setup/` | `~/.claude/skills/` | Interactive setup of a NEW project: CLAUDE.md, agent team briefs, team workflow, tickets, changelogs, backups, metrics, language packs |
| `skills/adopt-project-standards/` | `~/.claude/skills/` | Brings an EXISTING project in line: measures its transcripts, classifies every local rule against the global standards, migrates in reviewed waves |
| `skills/rule-hook-agent/` | `~/.claude/skills/` | When you state a new rule, records it and checks whether a hook can enforce it |
| `skills/pattern-skill-agent/` | `~/.claude/skills/` | When the same work has been done three times, turns it into a skill |
| `hooks/` | `~/.claude/hooks/` | The global write guard: W2 literal delete paths, W3 writes outside the project, W5 no system-temp writes (plus log-only rules); the S1 size flag for a project `.claude` over 1 GB; the M1 model reminder for an Agent call that sets no model (log-only) |
| `install.py` | - | The installer: a dry run by default, backs up before replacing, never deletes, never touches `settings.json` |

## Quick start

Requirements: Claude Code, Python 3.10+, git. Developed on Windows 11 with Git Bash and PowerShell.

```bash
git clone https://github.com/shadowevil/claude-global-standards.git
```

```bash
cd claude-global-standards
```

```bash
python install.py
```

```bash
python install.py --apply
```

The first `install.py` is a dry run listing every file it would add or change. `--apply` installs
the global instructions, docs and skills, backing up anything it replaces under
`~/.claude/staging/install-backups/<timestamp>/`.

**Hooks are opt-in, in two steps:**
1. `python install.py --hooks --apply` copies the hook code.
2. Wire it yourself by merging `hooks/settings_snippet.json` into the `hooks.PreToolUse` list of
   `~/.claude/settings.json`. The installer never edits `settings.json`. A global hook affects every
   session, so turning it on is a deliberate step.

The snippet ships log-only: `--log-only` with no `--enforce=` records every hit and denies nothing.
Read `~/.claude/hooks/write_guard_hits.jsonl` (or run `hooks/hits_summary.py`) for a week, then add
`--enforce=W2,W3,W5` (or a subset) to the command in `settings.json` to let those rules deny. Keep
`--log-only` on the line either way: an `--enforce=` list then names the only rules that may deny.

**S1, the `.claude` size flag (`hooks/size_guard.py`, flags from day one).** Rule (user, 2026-10-20):
"if the agents local .claude directory gets bigger than 1gb, it needs to prune any temporary files
and address the bloat", to be checked "whenever that directory is greped or used". It is partly
hookable: a hook can measure the folder and tell the agent, but it can't judge what is temporary. So S1
only ever flags. It adds `additionalContext`, never denies and never deletes (user ruling,
2026-10-20: "a flag to the agent/PM to prune it. Not necessarily blocking"). "Local" means the
project's `.claude\` (`CLAUDE_PROJECT_DIR`, else `cwd`), never `~/.claude`.
- Events: SessionStart and UserPromptSubmit reach the main session. PreToolUse on
  Read/Grep/Glob/Write/Edit/MultiEdit/NotebookEdit/Bash/PowerShell fires only when the target is
  inside `<project>\.claude`, and reaches whoever made the call, main session or subagent. Subagents
  get a "report it, prune only if your brief allows writes" wording.
- Message: the size, the top 5 subfolders, prune `.claude\scratch\` first with literal paths (the
  step is left out when scratch is empty), and leave worktrees, metrics, docs, design, backups and
  tickets to the user.
- Cache: one state file beside the script (`size_guard_state.jsonl`, never inside a project).
  The folder is re-measured at most every 10 min, or every 2 min while it is over the limit or after
  a walk the 3 s cap cut short. `--limit-mb=`, `--ttl-minutes=`, `--over-ttl-minutes=` and
  `--budget-seconds=` override these.
- How often it flags, per project and recipient (the main session, or each subagent): at most once
  per 10-min window while pruning scratch alone could get the folder under the limit. When even
  deleting all of scratch could not, it flags once per session (user ruling, 2026-10-20). In both
  cases a SessionStart after `/clear` or a compaction flags the main session again, since the earlier
  flag has left its context. A hook process that sees a parallel process already flagged stays silent.
- Wiring: merge `hooks/size_guard_settings_snippet.json` (three events) into `settings.json`. It ships
  flagging from day one, with no log-only week (user ruling, 2026-10-20). Add `--log-only` to a
  command to record rows without showing the flag, or set `CLAUDE_SIZE_GUARD=log`. Rows go to
  `size_guard_hits.jsonl`. Self-test: `python -I -S -B hooks/size_guard.py --self-test`.
- Measured 2026-10-20: a full walk takes 30-70 ms warm and up to 660 ms cold (6,388 files). The hook
  costs about 70-95 ms per matched call when it exits at the pre-filter, about 120-190 ms when it
  checks. Folders over 1 GB: Helbreath 2.6 GB (all `.backups`), entityframeworkcpp 2.0 GB (all
  `worktrees`), hb-npc-wow-maker 4.2 GB (all `scratch`).

**M1, the Agent model reminder (`hooks/model_guard.py`, LOG-ONLY).** Rule (user, 2026-10-21): pick the
cheapest model that does the job well (Haiku for clear, fully specified actions including edits and
bulk edits, Sonnet the default, Opus for design, security-sensitive code, hard debugging and
attack-testing; a reviewer is never a lower tier than the author on security-sensitive code), and
state the model and why in the brief. Partly hookable: whether the tier FITS the task is a judgment no
hook can make, but whether the dispatcher picked one at all is visible. A PreToolUse hook on the
`Agent` tool receives `tool_input.prompt`, `description`, `subagent_type` and `model` (Claude Code hooks
reference). M1 fires when nothing picks a tier: no `model` on the call, no agent definition (project
`.claude/agents`, a parent of the cwd, or `~/.claude/agents`) with a `model:` other than `inherit`, and
no `CLAUDE_CODE_SUBAGENT_MODEL`. The subagent then runs on the main session's model (the documented
order: call `model`, definition frontmatter, that variable, main model), and a "Model: Haiku" line in
the brief does not change that. It stays silent for an unknown or plugin agent type it cannot verify, and for `claude-code-guide` (Haiku)
and `statusline-setup` (Sonnet), which carry their own models. A project or user definition that reuses
a built-in name (say `Explore` with `model: haiku`) overrides the built-in and is honoured; a file with
no `---` header is not treated as a definition.
- Advisory only: no `permissionDecision`, no deny. Default is log-only (rows to
  `model_guard_hits.jsonl`, nothing printed); add `--advise` to the command to print the reminder as
  `additionalContext`. `CLAUDE_MODEL_GUARD=log` forces log-only.
- Not built, and why: check (ii), "haiku on a prompt that says hooks/delete/security" or "opus on a
  lookup". Replayed over 1,247 Agent calls (2026-09-28 to 2026-10-21): 4 of 18 Haiku calls matched and
  all 4 were false positives (a constant named `attack_impact_fraction`, "delete the six colour
  constants", an NPC folder called `guard`, "hooks" in a version note); the Opus sample was legitimate
  reviews and a self-justified inventory. The tier is a judgment, so the reminder text carries the
  tiers instead.
- Measured replay of M1 over those calls: 48 fires (3.8%), 5 of 5 hand-checked hits real (general-purpose, Explore and
  Plan calls with no model set, which inherit the main session's). Measured 2026-10-21; the fire rate
  of the live hook depends on how the dispatcher behaves once the rule is in its context.
- Wiring: `hooks/model_guard_settings_snippet.json` (one PreToolUse entry, matcher `Agent`). Self-test:
  `python -I -S -B hooks/model_guard.py --self-test`.

**Strict git (optional):** `python install.py --strict-git --apply` appends
`global/CLAUDE.git-strict.md` ("never commit on the default branch") to the installed `CLAUDE.md`.
Pass the flag on every later install too, or the dry run reports `CLAUDE.md` as changed.

## Adapt it to your machine first

This repo is one person's live setup, and some lines name that machine:
- `global/CLAUDE.md`: the Python interpreter path, the `C:\Users\<name>\.claude` path, and the console
  code page (cp1252, hence the ASCII rule).
- `hooks/settings_snippet.json` and the usage line in `hooks/write_guard.py`'s docstring: the
  absolute path to `write_guard.py`. `hooks/size_guard_settings_snippet.json`: the path to
  `size_guard.py`. `hooks/model_guard_settings_snippet.json`: the path to `model_guard.py`.
- `skills/adopt-project-standards/SKILL.md` Step 1: the absolute path to `transcript_metrics.py`.
- The hook self-test fixtures in `hooks/rules/` (`w2_literal_delete_paths.py`, `w3_outside_root.py`,
  `w5_temp_dir.py`) use `C:\Users\ShadowEvil\...` sample paths. Only the self-test reads them, so
  they can stay, but update them if you want the samples to match your machine.
- `skills/project-setup/SKILL.md` and `packs/cpp-house-style.md`: the "canonical sources" table
  points at the author's own projects (a ticket system, a backup script, a metrics script, C++ hooks).
  If those paths don't exist on your machine, the skill says so and skips that component; it never
  invents a replacement.

Search the repo for `ShadowEvil` and replace those paths with yours before installing.

## How changes flow

Edit in this repo, never in `~/.claude` directly:
1. **Change** the file here.
2. **Review:** every skill or hook change gets a separate, read-only review by another agent before it
   is installed. `global/CLAUDE.md` changes need the owner's approval.
3. **Commit and push.**
4. **Install:** `python install.py`, then `python install.py --apply` (add `--hooks` for hook code).

## Set it up with an AI

Paste this into a Claude Code session opened in your clone of this repo:

```text
Set up the global Claude Code standards from this repository on my machine.

1. Read README.md, CLAUDE.md, install.py and global/CLAUDE.md in full.
2. Search the repo for machine-specific values (user name "ShadowEvil", absolute paths, the Python
   path, the console code page). List each one with my machine's equivalent, and ask me before
   changing anything. Make the changes in this repo, not in ~/.claude.
3. Run `python install.py` (dry run) and show me what it would add or change, including anything it
   would replace in my existing ~/.claude/CLAUDE.md. If I already have a global CLAUDE.md, propose a
   merge rather than overwriting my rules.
4. After I approve, run `python install.py --apply`.
5. Hooks: explain what each global rule (W2, W3, W5) blocks, and that S1 only flags a project
   `.claude` folder over 1 GB and never blocks. Recommend starting W2, W3 and W5 log-only; S1's
   snippet flags from day one. Install the hook code with `python install.py --hooks --apply` and
   merge hooks/settings_snippet.json and hooks/size_guard_settings_snippet.json into
   ~/.claude/settings.json only after I approve both steps.
6. Verify: confirm ~/.claude/CLAUDE.md matches the repo copy, list the installed skills, and if hooks
   were installed, run `python -I -S -B "<home>/.claude/hooks/write_guard.py" --self-test` (and the
   same for `size_guard.py`) with
   <home> spelled out as an absolute path (PowerShell does not expand `~` for python).
7. Tell me how to use the two main skills: "set up this project's workflow" (project-setup) in a new
   project, and "adopt the standards" (adopt-project-standards) in an existing one.

Do not write outside this repository and ~/.claude, do not use heredocs to write files, and do not
delete anything without asking me first.
```

## Using it day to day

- **New project:** open a Claude Code session in the project folder and say "set up this project's
  workflow". It detects the project, interviews you with clickable questions, shows a plan, and
  generates the files.
- **Existing project:** say "adopt the standards". It measures the project's past sessions, shows a
  table of what duplicates, narrows, conflicts with or is missing from the standards, and migrates in
  waves you approve. A re-measure afterwards shows whether it helped.
- **New rule:** state it ("from now on, never ..."). `rule-hook-agent` records it and checks whether a
  hook can enforce it.
