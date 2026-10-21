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
2. **Four skills become available in every project** (the first two with `--profile starter`):
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
  `skills/<name>/`, a small record of what it installed (`.claude-global-standards.json`), and with
  `--hooks`, `hooks/`.
- **Two profiles.** `--profile full` (the default) installs everything. `--profile starter` installs
  the core rules and the two setup skills (`project-setup`, `adopt-project-standards`) and does not
  install the rule-making skills or their section of `CLAUDE.md`; it is the gentler start for someone
  new to Claude Code. The choice is remembered, and `--profile full --apply` moves up later. Moving
  from full down to starter never deletes the two extra skills; the dry run names them.
- **Keeps your own `~/.claude/CLAUDE.md`.** If you already have one that the installer did not write,
  your text is kept (only trailing blank lines are trimmed) and the toolkit's rules are added after
  it, between two marker comments. Later installs change only what is between the markers. The dry
  run shows this as MERGE (first time) or BLOCK (later). A `CLAUDE.md` that is not UTF-8, or whose
  markers are broken, is refused with an error and left unchanged.
- **Never overwrites a file you edited.** The record lists what the installer wrote. A file you have
  changed since, `CLAUDE.md` included, is left alone and the new version is written beside it as
  `<name>.new` for you to compare (the dry run says KEEP). To take the new version, copy the `.new`
  file over yours. If the record is lost, the installer recognises its own earlier installs from the
  clone's git history (so the clone must be a git clone, not a zip), and works out the profile from
  which skills are installed.
- **Describes your machine, not the author's.** The Environment section of `CLAUDE.md` is generated
  for your Python command, its path and version, and your console's encoding.
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
- **Dry run by default.** Without `--apply` nothing is written; it lists each file as ADD, CHANGE,
  MERGE, BLOCK or KEEP.

## What it does not do

- It does not install Claude Code, Python or git.
- It does not change any project. Project files are only written when you run `project-setup` or
  `adopt-project-standards` inside a project, and both show their plan before writing.
- It does not turn on blocking. Hook enforcement stays off until you add `--enforce=` yourself.
- It does not send anything anywhere. No telemetry; hit logs stay in `~/.claude/hooks/`.

**Some content is still the author's own** and is worth reading before you install:
- The rules quote the dates the author made them. In your install they read "author's rule,
  2026-..." so they are not mistaken for decisions you made (`--attribution user` keeps the
  original wording). A few working-habit notes are Windows-specific (PowerShell 5.1, Git Bash).
- `project-setup` ships its backup script, changelog format and metrics script in
  `skills/project-setup/bundled/`, so those work everywhere. Two parts need sources of your own: a
  ticket system and the C++ pack's longer wording and hooks. Point at them in
  `~/.claude/project-setup.sources.json`, e.g.
  `{"ticket_system": "C:\\path\\to\\ticket-system", "cpp_standards": "...", "cpp_hooks": "..."}`.
  Without that file the skill says those parts are unavailable and skips them. It never invents a
  replacement.

## Requirements

Claude Code, Python 3.10 or newer, and git. Developed and used daily on Windows 11 (Git Bash and
PowerShell). The installer's self-test covers macOS and Linux home folders, but the toolkit has not
been run on a real Mac or Linux machine yet.

## Install

The quickest route is the one-paste prompt in [Set it up with Claude](#set-it-up-with-claude). By
hand, clone into `~/.claude/scratch/` (the same place the prompt uses, so
[updating](docs/getting-started.md#updating) works the same either way). In PowerShell, write
`$HOME` instead of `~`.

```bash
git clone https://github.com/shadowevil/claude-global-standards.git ~/.claude/scratch/claude-global-standards
```

```bash
cd ~/.claude/scratch/claude-global-standards
```

Dry run, then install the rules, docs and skills (add `--profile starter` to both for the smaller
setup):

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

One paste does the whole setup. Open Claude Code in any folder and paste the prompt below. Claude
clones this repo into `~/.claude/scratch/claude-global-standards`, reads it, runs the dry runs, then
asks you **one** question with the whole plan in front of you, installs what you chose, and checks
the result. The clone is the only thing written before you answer. (Claude Code may still ask its
own permission prompts for writes outside the folder you opened; those are separate.) Keep the
clone: it is how you [update](docs/getting-started.md#updating) later.

```text
Set up the claude-global-standards toolkit on my machine, start to finish, in this session.

Ground rules for this whole task: write only inside ~/.claude (on Windows %USERPROFILE%\.claude).
Write files with your file-writing tool, never with heredocs. Delete nothing. Ask me exactly one
question (step 5); before I answer, the clone in step 1 is the only thing you write. Write my home
folder out as an absolute path with forward slashes in every command (PowerShell does not expand ~
for other programs), and quote every path. Do not cd; run the installer by its full path, which
works from any folder. <clone> below means "<home>/.claude/scratch/claude-global-standards".

1. Tools: run `git --version`, then `python --version`, `python3 --version` and, on Windows,
   `py -3 --version`. Use the first Python that reports 3.10 or newer as <py>. If git or a Python
   3.10+ is missing, stop and tell me how to install it.
2. Get the repo:
   - If "<clone>" exists, run `git -C "<clone>" status --short` and `git -C "<clone>" log -1 --oneline`
     and tell me what you found. Use it as it is; if I want a newer version, I will run
     `git -C "<clone>" pull` myself.
   - Otherwise run `git clone https://github.com/shadowevil/claude-global-standards.git "<clone>"`
     (git creates the folders) and report `git -C "<clone>" log -1 --oneline`.
3. Read "<clone>/README.md", "<clone>/docs/getting-started.md", "<clone>/install.py",
   "<clone>/global/CLAUDE.md" and "<clone>/hooks/RULES.md" in full.
4. Dry runs, nothing written: `<py> "<clone>/install.py" --profile starter`,
   `<py> "<clone>/install.py" --profile full` and
   `<py> "<clone>/install.py" --only hooks --hooks --wire-hooks`. If ~/.claude/CLAUDE.md already
   exists and the dry run says MERGE, my file is kept and the toolkit's rules are added after it:
   list any of my rules that contradict the new ones. If a plain `<py> "<clone>/install.py"` shows
   nothing to add or change and either ~/.claude/hooks does not exist or the hooks dry run also shows
   nothing, say the toolkit is already installed and current (hooks are optional), and stop.
5. One question. First show a short plan: what each profile installs (starter: the core rules and the
   project-setup and adopt-project-standards skills; full: everything); every file that would be
   replaced (each is backed up first), merged, or kept because I edited it; any contradictions with
   my own rules; one line per hook (write guard W2/W3/W5 and log-only W4: writes and deletes; S1: a
   message when a project's .claude folder passes 1 GB; M1: logs agent calls that pick no model) and
   the exact settings.json entries they would add. Then ask, with these options:
   - "Starter" (recommended if this is my first Claude Code setup)
   - "Full" (everything the author uses)
   - "Full plus hooks" (the hooks are advisory and block nothing)
   - "Change something first" (I will say what, or say stop)
6. Do what I chose: `<py> "<clone>/install.py" --profile starter --apply` or
   `<py> "<clone>/install.py" --profile full --apply`; for hooks, then
   `<py> "<clone>/install.py" --only hooks --hooks --apply` and
   `<py> "<clone>/install.py" --only hooks --wire-hooks --apply`.
7. Verify, and report each item as passed or failed with its numbers:
   - `<py> "<clone>/install.py"` shows 0 files to add or change.
   - The skills of the chosen profile exist under ~/.claude/skills/ (two for starter, four for full).
   - With hooks: `<py> "<clone>/install.py" --only hooks --hooks --wire-hooks` shows 0 files and
     every entry already wired, and `<py> -I -S -B "<home>/.claude/hooks/write_guard.py" --self-test`
     passes, and the same for size_guard.py and model_guard.py.
8. Finish by telling me: start a new Claude Code session so the rules load; say "set up this
   project's workflow" in a new project or "adopt the standards" in an existing one; and how hook
   blocking is turned on later (hooks/RULES.md).
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
