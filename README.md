# claude-global-standards

A ready-made, machine-wide setup for [Claude Code](https://claude.com/claude-code). Installing it
gives every Claude Code session on your computer, and every agent those sessions start, the same set
of working rules, four skills and three optional safety hooks. It is one developer's daily setup,
published so that someone else can start from a setup that already works instead of an empty one.

It grew out of a real multi-agent game project and is shaped by measurement, not taste. Analysing
about 4,600 session transcripts showed where agent time and tokens actually go:
- **Navigation** was 37% of turns and 67% of what got pulled into context.
- **Large single-file docs** were 14% of everything agents read.
- **Hook denials, heredoc quoting failures and PowerShell quirks** cost about 5% of tool calls.

The rules here target those costs directly.

**New to Claude Code?** Start with [docs/getting-started.md](docs/getting-started.md): what you get,
how to install it, and what your first day looks like.

## What this toolkit does

Concretely, after installing it:

1. **Every session reads the same global rules.** Claude Code loads `~/.claude/CLAUDE.md` into every
   session and every agent. This repo's version sets:
   - **Safety rails:** write only inside the session's project folder and `~/.claude`; delete only by
     literal absolute paths; never pull, `reset --hard`, force-push or rewrite git history; push only
     when asked; never kill a process the session did not start.
   - **Working habits:** no heredocs, no `sed -i` on tracked files, ASCII console output, scratch
     files inside the project rather than the system temp folder.
   - **Agent dispatch:** only the main session starts agents; briefs are self-contained; every code
     change gets a separate read-only reviewer; which model (Haiku, Sonnet, Opus) fits which job.
   - **Docs:** the lean-index rule, so agents read one small index and one targeted file instead of
     a whole large doc.
2. **Four skills become available in every project:**
   - `project-setup`: say "set up this project's workflow" in a new project. It interviews you with
     clickable questions, shows a plan, then writes a project `CLAUDE.md`, agent team briefs, a team
     workflow, and optionally tickets, changelogs, backups, a metrics baseline and language packs.
   - `adopt-project-standards`: say "adopt the standards" in an existing project. It measures the
     project's past sessions, sorts every local rule into duplicates, narrows, conflicts or missing,
     and migrates in waves you approve.
   - `rule-hook-agent`: when you state a new rule ("from now on, never ..."), it records the rule and
     checks whether a hook can enforce it.
   - `pattern-skill-agent`: when the same work has been done three times, it turns it into a skill.
3. **Three hooks, only if you ask for them** (details in [hooks/RULES.md](hooks/RULES.md)):
   - **Write guard (W2, W3, W5):** checks every write and delete before it runs. It ships
     **log-only**: it records what it would have blocked and blocks nothing until you turn
     enforcement on.
   - **S1 size flag:** tells the session when a project's `.claude` folder passes 1 GB and what to
     prune first. It never blocks and never deletes.
   - **M1 model reminder:** records Agent calls that pick no model. It ships log-only and never blocks.

## What the installer changes on your machine

- **Writes only under `~/.claude`** (`%USERPROFILE%\.claude` on Windows): `CLAUDE.md`, `docs/`,
  `skills/<name>/` for the four skills, and with `--hooks`, `hooks/`.
- **Replaces your `~/.claude/CLAUDE.md` if you have one.** It backs the old file up first, but it
  does not merge. If you already have global rules, merge them into the clone's `global/CLAUDE.md`
  before installing (or ask Claude to; the setup prompt below does). That leaves local changes in
  the clone, so commit them on a branch of your own before you next update it. A merge made directly in
  `~/.claude/CLAUDE.md` is replaced again by the next install that includes the rules.
- **Backs up every file it replaces** under `~/.claude/staging/install-backups/<timestamp>/`.
- **Never deletes anything.** Files in `~/.claude` that this repo does not ship are listed as
  "extra (left alone)".
- **Fills in your paths.** The repo's files contain placeholders (`{{CLAUDE_HOME}}`,
  `{{CLAUDE_HOME_FWD}}`, `{{PYTHON}}`). The installer replaces them with your home folder and your
  Python command as it copies, so nothing in the repo needs editing first. It detects `python` or
  `python3` and checks it is 3.10 or newer (`--python` overrides it). The self-test covers home
  folders with spaces and non-ASCII characters; a home path containing `"`, `$` or a backtick is
  refused, because the shell that runs hook commands would break on it.
- **Edits `~/.claude/settings.json` only with `--wire-hooks --apply`**, and then only adds entries:
  one per hook script that is not already wired, after a backup under
  `~/.claude/staging/install-backups/settings-<timestamp>/`. It never changes or removes an entry
  you already have, but it does rewrite the file's formatting (2-space indent, no byte-order mark).
  A `settings.json` it cannot parse, or one with an unexpected shape, is refused and left alone.
- **Dry run by default.** Without `--apply` nothing is written; it lists each file as ADD or CHANGE.

## What it does not do

- It does not install Claude Code, Python or git.
- It does not change any project. Project files are only written when you run `project-setup` or
  `adopt-project-standards` inside a project, and both show their plan before writing.
- It does not turn on blocking. Hook enforcement stays off until you add `--enforce=` yourself.
- It does not send anything anywhere. No telemetry; hit logs stay in `~/.claude/hooks/`.

**Some content is still the author's own** and is worth reading before you install:
- `global/CLAUDE.md` describes the author's Windows machine in places (bare `python` rather than
  `python3`, a cp1252 console, rules quoted with the date the author made them). The rules hold on any
  machine; the descriptions may not match yours.
- `project-setup` can copy a ticket system, a backup script, a changelog format and a metrics
  script from the author's other projects. On a machine without those projects, the skill says so
  and skips that part. It never invents a replacement.

## Requirements

Claude Code, Python 3.10 or newer, and git. Developed and used daily on Windows 11 (Git Bash and
PowerShell). The installer's self-test covers macOS and Linux home folders, but the toolkit has not
been run on a real Mac or Linux machine yet.

## Install

```bash
git clone https://github.com/shadowevil/claude-global-standards.git
```

```bash
cd claude-global-standards
```

Dry run, then install the rules, docs and skills:

```bash
python install.py
```

```bash
python install.py --apply
```

Optional hooks: copy the hook code, preview the `settings.json` change, then wire them.
`--only hooks` keeps these runs from reinstalling `CLAUDE.md` and the skills:

```bash
python install.py --only hooks --hooks --apply
```

```bash
python install.py --only hooks --wire-hooks
```

```bash
python install.py --only hooks --wire-hooks --apply
```

Use `python3` instead of `python` if that is what your system calls it. Hooks take effect in Claude
Code sessions started after wiring.

**Check the hooks** with their self-tests (write the home folder out in full: PowerShell does not
expand `~` for Python):

```bash
python -I -S -B "<your home>/.claude/hooks/write_guard.py" --self-test
```

The same works for `size_guard.py` and `model_guard.py`. `python install.py --self-test` tests the
installer itself.

**Turn on enforcement** once the write guard has run log-only for a while and its hit log
(`~/.claude/hooks/write_guard_hits.jsonl`, summarized by `hooks/hits_summary.py`) shows only real
problems: add `--enforce=W2,W3,W5` (or a subset) to the write guard's command in
`~/.claude/settings.json`, keeping `--log-only` on the line.

**Strict git (optional):** `python install.py --strict-git --apply` appends
`global/CLAUDE.git-strict.md` ("never commit on the default branch") to the installed `CLAUDE.md`.
Pass the flag on every later install too, or the dry run reports `CLAUDE.md` as changed.

## Set it up with Claude

Paste this into a new Claude Code session, opened in the folder where you keep your code:

```text
Set up the claude-global-standards toolkit on my machine.

1. Clone it with git, here: `git clone https://github.com/shadowevil/claude-global-standards.git`,
   then work inside the new claude-global-standards folder. If the folder already exists, run
   `git status` there and tell me what you find instead of cloning again.
2. Read README.md, install.py, global/CLAUDE.md and hooks/RULES.md in full, then tell me in plain
   words what will be installed and what it will change.
3. Run `python install.py` (or `python3` if `python` is not found). It is a dry run. Show me every file
   it would add or change. If I already have ~/.claude/CLAUDE.md, show me what is in mine that the new
   one lacks, and propose a merge. Make the merge in the clone's global/CLAUDE.md (keep its
   {{...}} placeholders as they are), never in ~/.claude/CLAUDE.md, and re-run the dry run.
4. After I approve, run `python install.py --apply` and confirm the installed ~/.claude/CLAUDE.md
   matches what I approved.
5. Hooks: explain what the write guard (W2, W3, W5), S1 and M1 each do, that all of them start
   without blocking anything, and how enforcement is turned on later. If I want them, run
   `python install.py --only hooks --hooks --apply`, then `python install.py --only hooks --wire-hooks`
   and show me the settings.json entries it would add. Run that last command again with `--apply`
   only after I approve those entries.
6. Verify: list the installed skills, and if hooks were installed, run each hook's self-test with my
   home folder written out as an absolute path, e.g.
   `python -I -S -B "<home>/.claude/hooks/write_guard.py" --self-test`. Report each result as
   passed or failed with its numbers.
7. Tell me how to use the two main skills: "set up this project's workflow" in a new project and
   "adopt the standards" in an existing one.

Do not write outside the cloned folder and ~/.claude, do not use heredocs to write files, and do not
delete anything without asking me first.
```

## How changes flow

Edit in this repo, never in `~/.claude` directly:
1. **Change** the file here.
2. **Review:** every skill or hook change gets a separate, read-only review by another agent before it
   is installed. `global/CLAUDE.md` changes need the owner's approval.
3. **Commit and push.**
4. **Install:** `python install.py`, then `python install.py --apply` (add `--hooks` for hook code).

## Layout

| Path | Installs to |
|---|---|
| `global/CLAUDE.md` | `~/.claude/CLAUDE.md` |
| `global/CLAUDE.git-strict.md` | appended to it with `--strict-git` |
| `global/docs/` | `~/.claude/docs/` |
| `skills/<name>/` | `~/.claude/skills/<name>/` |
| `hooks/` | `~/.claude/hooks/` with `--hooks`; `*settings_snippet.json` are what `--wire-hooks` adds |
| `install.py` | not installed |
