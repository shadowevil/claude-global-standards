# Install profiles and merge-not-overwrite (design, 2026-10-21; approved and built 2026-10-21)

**As built:** starter = every CLAUDE.md section except "Hooks and skills" (the only section marked
full-only), the docs standard (project-setup points at it), and the project-setup and
adopt-project-standards skills; full = everything. B3's "first manifest run" recognises an earlier
install by matching the file against every committed version of it in the clone (git log), not
only the current one; without git an existing file counts as the user's.

**Owner's decisions (2026-10-21):** build it with default `full`; reword the dated quotes at install
time for other users ("author's rule, date"; the repo text and the owner's install stay as is);
Part D bundles the backup tool, changelog format and metrics baseline into
`skills/project-setup/bundled/`. No origin-project name appears in this repo (owner, 2026-10-21).

Part B of "Make claude-global-standards portable". Part A (placeholders, `--wire-hooks`) made the
installer use the installing user's paths. This note covers what Part A leaves: a first-time user
gets the owner's whole setup, the owner's machine described as theirs, and their own global
`CLAUDE.md` replaced. Nothing here is built until the owner approves it.

## Problems, measured against the current repo

1. **`install.py --apply` replaces `~/.claude/CLAUDE.md`.** It backs the old file up, but a user's
   own global rules silently stop applying. (verified: `plan()` writes the filled repo copy over it.)
2. **`global/CLAUDE.md` describes the owner's machine as fact.** "Python is invoked as bare `python`
   on this machine", the `Python313` path, "The console here is cp1252". On a Mac or Linux box, or a
   Windows box with UTF-8 consoles, an agent is told something false. (verified: lines 8-14.)
3. **Everything installs at once.** Four skills, the dispatch rules and the doc standard arrive
   together; for a first-time Claude Code user that is a lot of rules to learn on day one.
4. **Hand edits in `~/.claude` are lost on the next install** (backed up, not kept). The repo rule is
   "nothing in `~/.claude` is edited by hand", but a new user will not know it.

## Proposal

### B1. Profiles: `--profile starter|full`

| | starter | full |
|---|---|---|
| `CLAUDE.md` sections | Safety, Working habits, Session and scratch files, Evidence | all (adds dispatching, model tiers, lean-index docs, hooks-and-skills review rules) |
| docs | - | `documentation_standards.md` |
| skills | `project-setup`, `adopt-project-standards` | all four |
| hooks | never (needs `--hooks`) | never (needs `--hooks`) |

- Sections are chosen by marker comments in `global/CLAUDE.md`
  (`<!-- profile: full -->` before a section), so there stays one source file, no copy to drift.
- **Default `full`**, so the owner's install stays byte-identical; the README and the setup prompt
  recommend `starter` to a first-time user and say how to move up (`--profile full --apply`).
- The chosen profile is remembered in the manifest (B3), so a later plain `install.py` keeps it.

### B2. Machine facts are generated, not copied

The Environment section becomes `{{ENVIRONMENT}}`, filled by the installer from what it detects:
- the Python command it found (`python` or `python3`) and, on Windows, whether `python3` is the
  WindowsApps stub;
- the console encoding (`locale.getpreferredencoding()`, and on Windows the active code page); the
  ASCII-output rule is written as binding only when that is not UTF-8, otherwise as a recommendation;
- the OS and shells (PowerShell 5.1 notes only on Windows).

The owner's machine produces exactly today's text, so the owner's file does not change.

### B3. Merge, never overwrite

- **A manifest** `~/.claude/.claude-global-standards.json` records the sha256 of every file the
  installer wrote, plus the profile and options.
- **A file still matching its manifest hash** is unmodified: replace it (after the usual backup).
- **A file that differs** was edited by hand: do not replace it. Write the new version beside it as
  `<name>.new`, report `KEPT (edited)` with a one-line diff summary, and leave the merge to the user.
- **First install over an existing `~/.claude/CLAUDE.md`** (no manifest): keep the user's file and
  add the toolkit's rules inside a marked block at the end:
  `<!-- claude-global-standards: begin -->` ... `<!-- claude-global-standards: end -->`. Later
  installs replace only the text between the markers. The dry run shows exactly this.
- **The owner:** the first run with B3 finds no manifest and an installed `CLAUDE.md` equal to the
  filled repo copy; it treats that as installed-by-us and writes the manifest only (no file change).

### B5. Rule attribution reworded for other users

In the installed copy, "user rule" / "user ruling" in a dated quote becomes "author's rule" /
"author's ruling" (an exact phrase list, `.md` files only, tested). The manifest records the
wording as `attribution: user|author`. When the first manifest-writing run finds an installed
`CLAUDE.md` equal to the repo copy with the original wording (the owner), it records `user` and
nothing changes; every fresh install records `author`. `--attribution=` overrides it.

### B4. Hooks stay off by default

Already true (`--hooks` and `--wire-hooks` are both opt-in). No change, stated here so it is decided.

## Not proposed

- An uninstaller: the backups and the manifest make one easy later; not needed for a first user.
- Editing the owner's dated rulings ("user ruling, 2026-10-20") out of the text. They show where a rule
  came from. Open question below.

## Open questions for the owner

1. Default profile `full` (owner unchanged, newcomers told to pick `starter`), or no default
   (the installer asks)?
2. Is the starter section list right?
3. Should the dated quotes read "(author's rule, date)" in an installed copy, so a new user does not
   think they made those rulings?

## Test plan (when built)

Extend `install.py --self-test` and the scratch fake-home E2E: starter and full in a fresh home; an
existing user `CLAUDE.md` gets the marked block and keeps its text; a second install edits only the
block; a hand-edited skill file is KEPT with a `.new`; the owner's real home dry run reports 0 changes.
