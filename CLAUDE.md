# claude-global-standards

The source of truth for this machine's machine-wide Claude Code setup: the global instructions, the
global skills and the global hooks. Changes are made here, reviewed, committed, and then installed into
`~/.claude` with `install.py`. Nothing in `~/.claude` is edited by hand.

## Rules for this repo

- **Commit and push freely** (user, 2026-10-20: "Commit and push as needed. No need to confirm with me.
  That is your space"). Work in `feature/<name>` or `patch/<name>` branches, merged to `main`.
- **Skill and hook changes still go through the review phase** (global rule): stage the change here,
  have a separate read-only agent review it, and only then commit and install it. A hotfix is no
  exception.
- **Global hooks need the user's approval to install** (global rule). The installer never wires hooks
  into `settings.json`; that stays a manual, approved step.
- **Changes to `global/CLAUDE.md` need the user's approval** before they are installed, and never on
  another session's request alone.
- **Install with `python install.py`** (dry run: shows every file it would add or change) and
  `python install.py --apply` (backs up what it replaces, then copies). Never copy into `~/.claude` by hand.
- **Nothing machine-local is committed:** no hit logs, `__pycache__`, staging folders or transcripts.
- Edit files with the Edit tool or a bytes-preserving script; never `sed -i` or `>` on a tracked file
  from Git Bash (it rewrites CRLF to LF).

## Layout

- `global/CLAUDE.md` - installs to `~/.claude/CLAUDE.md`.
- `global/CLAUDE.git-strict.md` - appended to the installed `CLAUDE.md` only with `install.py --strict-git`.
- `global/docs/` - machine-wide docs (the documentation standard), install to `~/.claude/docs/`.
- `skills/<name>/` - each installs to `~/.claude/skills/<name>/`.
- `hooks/` - installs to `~/.claude/hooks/` (code only; wiring is manual, see README).
- `install.py` - the installer. `README.md` - for anyone adopting this.
