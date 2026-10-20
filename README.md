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
| `hooks/` | `~/.claude/hooks/` | The global write guard: W2 literal delete paths, W3 writes outside the project, W5 no system-temp writes (plus log-only rules) |
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

**Strict git (optional):** `python install.py --strict-git --apply` appends
`global/CLAUDE.git-strict.md` ("never commit on the default branch") to the installed `CLAUDE.md`.
Pass the flag on every later install too, or the dry run reports `CLAUDE.md` as changed.

## Adapt it to your machine first

This repo is one person's live setup, and some lines name that machine:
- `global/CLAUDE.md`: the Python interpreter path, the `C:\Users\<name>\.claude` path, and the console
  code page (cp1252, hence the ASCII rule).
- `hooks/settings_snippet.json` and the usage line in `hooks/write_guard.py`'s docstring: the
  absolute path to `write_guard.py`.
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
5. Hooks: explain what each global rule (W2, W3, W5) blocks. Recommend starting log-only. Install the
   hook code with `python install.py --hooks --apply` and merge hooks/settings_snippet.json into
   ~/.claude/settings.json only after I approve both steps.
6. Verify: confirm ~/.claude/CLAUDE.md matches the repo copy, list the installed skills, and if hooks
   were installed, run `python -I -S -B "<home>/.claude/hooks/write_guard.py" --self-test` with
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
