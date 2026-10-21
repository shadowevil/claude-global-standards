---
name: project-setup
description: Run by the main session (never by a dispatched agent) when the user wants a project set up with their Claude workflow - "set up this project's workflow", "bootstrap the agents/tickets/docs here", "make this project like my other one", "set up a new project with the team workflow". An existing project with its own setup uses adopt-project-standards instead. Interviews the user with clickable questions, then generates CLAUDE.md, agent briefs, the team workflow, tickets, changelogs, backups, a metrics baseline and optional language packs inside the project, never restating the machine-wide rules in ~/.claude/CLAUDE.md.
---

# project-setup

The user, 2026-10-20: an interactive skill that sets up an efficient environment like the one their
main project has. The workflow it installs is `references/workflow.md` (its one home). Templates are in
`templates/`, language packs in `packs/`, and the tools it copies into a project in `bundled/`.

## Binding

- **Main session only.** It talks to the user; agents never run it.
- **The target must be this session's project directory.** Writes are limited to it (global rule). If
  the user names another folder, tell them to open a session there and run this skill from it.
- **Global rules are not restated.** `~/.claude/CLAUDE.md` loads in every session and agent. In
  `references/workflow.md` every clause marked `(global)` is replaced in generated files by one pointer
  line to the global file (only the marked clause; the rest of its line stays). One exception: the short "Rules enforced by hooks" block in each agent brief,
  kept until friction data shows a CLAUDE.md statement alone is enough.
- **Never overwrite or delete.** A file that exists is shown as a diff and changed only on the user's OK.
- **Hooks and skills this setup installs go through the review phase** (global rule), settings.json
  wiring included. Hooks start LOG-ONLY; enforcement is the user's call.
- **Canonical sources are copied, never reconstructed.** If a source is missing, say so and skip it.
- **Interviewing:** AskUserQuestion, at most 4 questions per call and 2-4 options per question,
  recommended option first and marked. Free text goes through the automatic "Other". Never ask what
  detection answered or anything the global file settles.

| Component | Canonical source |
|---|---|
| Ticket system | Optional, machine-local: the `ticket_system` folder named in `{{CLAUDE_HOME}}\project-setup.sources.json` - follow its `docs\ADOPTING.md` end to end. No file or no entry: the component is not offered. |
| Backup tool | `bundled/backup.py` (the hardened, audited version; see `bundled/README.md`) |
| Changelog format | `bundled/changelog_format.md` |
| Metrics baseline | `bundled/subagent_baseline.py` |
| C++ pack | `packs/cpp-house-style.md` |

`project-setup.sources.json` is the user's own file, never shipped by the installer. It maps a
component to a folder on this machine (`{"ticket_system": "...", "cpp_standards": "...",
"cpp_hooks": "..."}`); read it if present, and treat a folder that does not exist as missing.

## Mode

- **Setup:** no `.claude/`, or one with no CLAUDE.md and no agent briefs. Steps 1-6.
- **Existing setup:** a CLAUDE.md or agent briefs already exist. Stop and use the
  `adopt-project-standards` skill, which measures, classifies and migrates in waves. If unclear, ask.

## Step 1 - Detect (read-only)

Gather, then report in at most 10 lines before asking anything:
- that the target is this session's project directory (Binding);
- git: repo or not, default branch, existing branch naming;
- languages by file count (skip `.git`, build output, vendored folders, `node_modules`), build system
  (`*.sln`/`*.slnx`/`*.vcxproj`, `CMakeLists.txt`, `package.json`, `pyproject.toml`, `Cargo.toml`) and
  the likely build and test commands;
- top-level folders and source subfolders (candidate ownership areas);
- existing `.claude/` contents, `.gitignore`, any CLAUDE.md.

## Step 2 - Interview

**Round A - shape** (4 questions):
1. Team: PM + specialists + verifier (recommended for multi-area code) / PM + specialists / solo.
2. Tickets: full system with viewer (recommended) / none. Only when the ticket system source exists;
   otherwise skip the question and report "no ticket system source on this machine".
3. Changelogs: technical + user-facing (recommended) / technical only / none.
4. Backups before modifying: recommend yes when the repo holds untracked or binary assets, else no.

**Round B - team** (skip when solo; 3 questions):
1. Areas: offer up to 4 proposals built from the detected source folders, each option naming its paths
   (e.g. "client: src/client/**"); more or different areas via Other, as `name: paths; name: paths`.
2. Contract pairs: offer the plausible pairs between the chosen areas (multiSelect) plus "none".
3. Verifier surface: test scripts / an automation bridge (MCP, test API) / the user runs it.

**Round C - names and commands** (3 questions; offer detected values as options, Other for edits):
1. Build command, and verify/test command.
2. Project one-line description.
3. Branch prefix (default `dev-NN-<name>`); the detected default branch is shown for confirmation only.

Scratch is not asked: it is fixed globally at `.claude/scratch/<role>/<task>/` (gitignored).

**Round D - extras** (up to 3 questions):
1. Models: the global model-tier rule (recommended: Haiku for fully specified edits, Sonnet by
   default, Opus for design and security-sensitive work) / strongest always / cheaper always. Each
   brief's `model:` comes from this; with the global rule, set it per role from that role's usual work.
2. Metrics baseline (recommended with a team) / none.
3. Language packs, only when a detected language has one: "C++ house style" / "none" (more options
   as packs are added).

The documentation standard is global (`~/.claude/docs/documentation_standards.md`) and is not asked.

Changelog file names default to `dev-changelog.md` and `user-changelog.md`; the backup location is
fixed by the script (`<repo>\.claude\.backups\`). The ticket system's own values (name, prefix,
port, CLI folder, authors) are asked by ADOPTING.md's sect. 1 when that step runs, not here.

## Step 3 - Plan, then confirm once

List every file to create or modify, one line each, existing files marked "modify: diff shown". One
question: proceed / change something.

## Step 4 - Generate

Record the choices first: `.claude/design/decisions.md` (created as the decisions-ledger index) gets an
entry "Workflow adopted <date> via project-setup: <choices>".

- **CLAUDE.md** from `templates/claude-md.md`: fill the slots, delete unchosen `<IF ...>` blocks. It
  loads every session: binding rules plus the read-when index only (aim under ~10 KB).
- **Agent briefs** `.claude/agents/<role>.md` from `templates/agent-brief.md`, one per area, plus a
  read-only `review.md`, plus the verifier's brief when chosen. Review and verifier get the read-only
  `tools:` list.
- **`.claude/docs/team_workflow.md`** from `references/workflow.md` sect. 1-6 (+ 8 with tickets, 9
  tailored), this project's names in every slot, each `(global)` clause replaced by one pointer line, and the
  italic line "Adopted from the project-setup workflow on <date>". Include the appendix's dispatch brief.
- **Docs follow the global standard** `~/.claude/docs/documentation_standards.md`; no local copy is
  generated (a project adds a local doc only for its own additions, pointing at the global one).
  Create `.claude/design/plans/` with `partial/` and `completed/` (its sect. 7).
- **Every doc this setup creates is a lean index from the start** (user, 2026-10-20: "a huge cost saving
  measure because it becomes a simple index lookup and a path grep read"): CLAUDE.md's "what to read,
  and when" is the root index; any doc with several standalone topics is `topic.md` (one line per entry:
  path, scope, when to read) plus `topic/<entry>.md`, unless the standard's sect. 6 applies (then a
  Contents block).
- **Tickets:** follow ADOPTING.md exactly, including its reviewed skill install, its verify section and
  its report step. Do not improvise around it.
- **Changelogs:** create both files with an empty living entry; copy `bundled/changelog_format.md` to
  `.claude/docs/changelog_format.md` and fill its area sections with this project's areas.
- **Backups:** copy `bundled/backup.py` byte for byte; never a version from anywhere else (older
  backup scripts could delete outside their backup folder, or delete an existing file passed as
  `--new-files`). The script finds the repo root as
  `Path(__file__).resolve().parents[3]`, which is right only three folders deep: copy it to
  `<repo>\src\tools\scripts\backup.py` (or another path exactly three deep), or change that index to
  this project's depth. Then prove it before relying on it, from the project root:
  1. `python src\tools\scripts\backup.py list` runs.
  2. `save --label setup-proof --files src\tools\scripts\backup.py` prints a set name, and that set
     appears under `<repo>\.claude\.backups\` (save only copies, so nothing is left behind).
  3. `purge --set ..` is refused (exit 1, "not a backup set name") and deletes nothing.
  4. `purge --set <the set from step 2>` removes only that set.
  Gitignore `.claude/.backups/`.
- **Metrics:** copy `bundled/subagent_baseline.py` to `.claude/metrics/`. It derives the project's
  transcript folder name (the absolute project path with every character that is not a letter or
  digit replaced by `-`, `C:\Users\x\source\foo` -> `C--Users-x-source-foo`) from where it sits; set
  `PROJECT_SLUG` only if Claude Code's folder name differs. If `~/.claude/projects/<slug>` exists, say
  so; if not, note that it appears after the first session there. Add the project's own hook ids to
  its `RULE_ID` pattern when it has any.
- **`.gitignore`:** `.claude/scratch/`, the backup sets folder, hook and viewer logs.
- **Language packs:** follow `packs/<pack>.md`.

## Step 5 - Verify

- Every path CLAUDE.md and the briefs point at exists.
- Tickets (when chosen): ADOPTING.md's verify section passes.
- Backups: the four-step proof above, refusal included.
- `git status --short` shows only the files from the plan.
- Any staged hook passes its self-test and has a review verdict before install.

## Step 6 - Report

Files created or changed; the choices; what was skipped and why; what still waits on the user (hook
installs, enforcement); first steps: the first ticket, the first dispatch using the packet.

## Existing projects

Auditing or migrating a project that already has its own setup is the `adopt-project-standards`
skill's job. It uses this skill's `references/`, `templates/` and `packs/` as its sources.
