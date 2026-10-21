# Getting started

For someone new to Claude Code. It covers what this toolkit gives you, how to install it, and what
your first day with it looks like. The [README](../README.md) has the full reference.

## What you get

Claude Code is an AI assistant that runs in your terminal or desktop app and works on the files in
the folder you open it in. Out of the box it follows none of your rules: each session decides for
itself how careful to be, where to put scratch files, and when to commit.

This toolkit gives every session on your machine the same rules, four skills and three optional
hooks.

- **Rules every session is told to follow.** They are in one file, `~/.claude/CLAUDE.md`, which
  Claude Code reads at the start of every session. They are instructions, not locks (the hooks are
  the locks). They tell Claude to:
  - write only inside the project you opened, and in `~/.claude`;
  - delete files only by their full, literal path, never through a variable or a relative path
    (a wildcard is allowed only in the file name itself);
  - never pull, force-push or rewrite git history, and push only when you ask;
  - report every check as verified, failed or unverified, never "should work";
  - have a second, read-only agent review each code change before it is committed. This costs
    extra usage, and it catches mistakes before they land.
- **Four skills.** A skill is a step-by-step procedure Claude follows when your request matches it:
  - `project-setup`: say "set up this project's workflow" in a new project. It asks you clickable
    questions, shows a plan, and writes nothing until you approve it.
  - `adopt-project-standards`: say "adopt the standards" in an existing project. It measures the
    project's past sessions, shows what it would change, and changes it in steps you approve.
  - `rule-hook-agent`: runs when you state a new rule ("from now on, never ..."). It records the
    rule in the project and checks whether a hook could enforce it.
  - `pattern-skill-agent`: runs when the same work has been done three times, and proposes turning
    it into a skill. It installs nothing without your OK.
- **Three hooks, only if you install them.** A hook is a small script Claude Code runs before an
  action. None of them blocks anything until you turn blocking on:
  - the **write guard** (rules W2, W3, W5) checks writes and deletes, and only logs at first;
  - **S1** shows the session a message when a project's `.claude` folder passes 1 GB;
  - **M1** logs agent calls that pick no model.

## What it will not do

- It does not install Claude Code, Python or git for you.
- It does not touch your projects on install. Project files are written only when you use a skill
  inside a project.
- It does not install hooks unless you ask (`--hooks`), and it does not turn blocking on.
- It does not send anything off your machine.

## Before you start

You need three things:

1. **Claude Code**, signed in. See [claude.com/claude-code](https://claude.com/claude-code).
2. **Python 3.10 or newer.** Check with `python --version`. On macOS and Linux the command is often
   `python3`; use that everywhere this guide says `python`.
3. **git.** Check with `git --version`.

The toolkit is developed and used daily on Windows 11. Its installer is tested against macOS and
Linux folder layouts, but it has not been run on a real Mac or Linux machine yet.

Some of `~/.claude/CLAUDE.md` describes the author's Windows machine (which Python command to use,
the console encoding). The rules hold anywhere; those descriptions may not match yours. Part of
`project-setup` copies tools from the author's other projects; on your machine it says those are
missing and skips them.

## Install

The easiest route is one paste. Open Claude Code in any folder and paste the prompt from the
README's [Set it up with Claude](../README.md#set-it-up-with-claude) section. Claude clones the repo
into `~/.claude/scratch/claude-global-standards`, explains what will change, asks you one question
with the whole plan in front of you, then installs and checks the result. (Claude Code may also show
its own permission prompts while it works; those are separate from that one question.)

To do it by hand, run these one at a time (in PowerShell, write `$HOME` instead of `~`):

```bash
git clone https://github.com/shadowevil/claude-global-standards.git ~/.claude/scratch/claude-global-standards
```

```bash
cd ~/.claude/scratch/claude-global-standards
```

```bash
python install.py
```

That last command is a **dry run**: it only lists what it would add or change. Save that list; it
is also what you would remove to uninstall. If it looks right, install:

```bash
python install.py --apply
```

If you already had a `~/.claude/CLAUDE.md`, the installer replaces it (the old file is saved under
`~/.claude/staging/install-backups/`). To keep your own rules, copy them into the clone's
`global/CLAUDE.md` first, then run the dry run again.

**Then start a new Claude Code session**: rules and skills load when a session starts. To check it
worked, ask the new session "which skills do you have?" and look for the four above.

Hooks are optional and can wait until you have used the toolkit for a few days. The README's
[Install](../README.md#install) section has the commands.

## Your first day

1. **Open Claude Code in a project folder.** The rules apply on their own; you do not need to
   mention them.
2. **Set the project up.** In a new project, say "set up this project's workflow". In a project
   that already has history, say "adopt the standards".
3. **Work as usual.** Ask for what you want. A few things you will notice:
   - Scratch files go in the project's `.claude/scratch/` folder, not all over your disk.
   - Claude reports checks as verified, failed or unverified.
   - Before a code change is committed, Claude has a second agent review it.
4. **State a rule when you want one.** Say "from now on, never ..." and Claude records it and checks
   whether a hook could enforce it.

## When something seems wrong

- **Claude will not write somewhere.** That is the write-scope rule. Open a Claude Code session in
  the folder you want changed instead.
- **A command was blocked.** That only happens once you have turned a hook's blocking on. The
  message explains why; [hooks/RULES.md](../hooks/RULES.md) describes each rule.
- **You want to undo the install.** Every file the installer replaced is saved under
  `~/.claude/staging/install-backups/<date-time>/`; copy back what you want. To remove the toolkit,
  delete the files from the list you saved (the README's Layout table shows where each part goes).
  If you wired hooks, restore `settings.json` from
  `~/.claude/staging/install-backups/settings-<date-time>/`.

## Updating

New versions arrive through git, in the clone at `~/.claude/scratch/claude-global-standards`
(in PowerShell, write `$HOME` instead of `~`):

```bash
git -C ~/.claude/scratch/claude-global-standards status
```

If it shows changes of your own (a merged `CLAUDE.md`, say), commit them on a branch of your own
first. Then update with `git -C ~/.claude/scratch/claude-global-standards pull`, and from inside
that folder run `python install.py` for a dry run and `--apply` once you have looked at the
changes. If you installed hooks, also run `python install.py --only hooks --hooks --apply`, and
`python install.py --only hooks --wire-hooks` to see whether a new hook needs wiring (add `--apply`
to wire it). Run the pull yourself: the rules tell Claude never to pull, so it will ask you to.
