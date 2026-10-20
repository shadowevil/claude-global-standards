---
name: project-setup
description: Run by the main session (never by a dispatched agent) when the user wants a project set up with their Claude workflow - "set up this project's workflow", "bootstrap the agents/tickets/docs here", "make this project like Helbreath", "set up a new project with the team workflow". An existing project with its own setup uses adopt-project-standards instead. Interviews the user with clickable questions, then generates CLAUDE.md, agent briefs, the team workflow, tickets, changelogs, backups, a metrics baseline and optional language packs inside the project, never restating the machine-wide rules in ~/.claude/CLAUDE.md.
---

# project-setup

The user, 2026-10-20: "an interactive skill which helps me setup an efficient environment like what
Helbreath SDL has". The workflow it installs is `references/workflow.md` (its one home; Helbreath's
`portable_workflow.md` was its first draft). Templates are in `templates/`, language packs in `packs/`.

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
| Ticket system | `C:\Users\ShadowEvil\source\master-ticket-site\ticket-system` - follow its `docs\ADOPTING.md` end to end (owner: the Ticket Master session) |
| Backup tool | **On hold** (2026-10-20): the canonical script can delete outside its backup folder (see Step 4, Backups). Owner: Helbreath SDL3. |
| Changelog format | `C:\Users\ShadowEvil\source\Helbreath-SDL\Helbreath\.claude\docs\changelog_format.md` |
| Metrics baseline | `C:\Users\ShadowEvil\source\Helbreath-SDL\Helbreath\.claude\metrics\subagent_baseline.py` |
| C++ pack | `packs/cpp-house-style.md` |

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
2. Tickets: full system with viewer (recommended) / none.
3. Changelogs: technical + user-facing (recommended) / technical only / none.
4. Backups before modifying: recommend yes when the repo holds untracked or binary assets, else no.
   While the backup tool is on hold (component table), say so: a yes is recorded as "backups:
   deferred" in the plan and nothing is copied.

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
1. Models: strongest for complex work and cheaper for general work (recommended) / strongest always /
   cheaper always. Each brief's `model:` comes from this.
2. Metrics baseline (recommended with a team) / none.
3. Language packs, only when a detected language has one: "C++ house style" / "none" (more options
   as packs are added).

The documentation standard is global (`~/.claude/docs/documentation_standards.md`) and is not asked.

Changelog file names default to `dev-changelog.md` and `user-changelog.md` (backups are on hold, so
no backup location is set). The ticket system's own values (name, prefix, port, CLI folder, authors) are asked
by ADOPTING.md's sect. 1 when that step runs, not here.

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
  its report to Ticket Master. Do not improvise around it.
- **Changelogs:** create both files with an empty living entry; adapt `changelog_format.md` (strip
  Helbreath names and areas).
- **Backups: ON HOLD - copy nothing** (2026-10-20). Ticket Master found that the canonical
  `backup.ps1` deletes outside its backup folder: `purge -Set ..` removed `<repo>\.claude`, and a
  `-NewFiles` path outside the repo was deleted at `restore`. Helbreath has since ported it to an
  uncommitted `src\tools\scripts\backup.py`, which has the same holes (unvalidated set name and label,
  a repo-prefix check with no trailing separator, unchecked new-file paths). The fault is with its
  owner, Helbreath SDL3. Lift this hold only after the fixed version is committed there and reviewed,
  and repoint the component table at it. Until then, record "backups: deferred" and tell the user why.
- **Metrics:** copy `subagent_baseline.py` to `.claude/metrics/` and set `PROJECT_SLUG`: the absolute
  project path with every character that is not a letter or digit replaced by `-`
  (`C:\Users\x\source\foo` -> `C--Users-x-source-foo`). If `~/.claude/projects/<slug>` exists, say so;
  if not, note that it appears after the first session there.
- **`.gitignore`:** `.claude/scratch/`, the backup sets folder, hook and viewer logs.
- **Language packs:** follow `packs/<pack>.md`.

## Step 5 - Verify

- Every path CLAUDE.md and the briefs point at exists.
- Tickets: ADOPTING.md's verify section passes.
- Backups: nothing copied while on hold.
- `git status --short` shows only the files from the plan.
- Any staged hook passes its self-test and has a review verdict before install.

## Step 6 - Report

Files created or changed; the choices; what was skipped and why; what still waits on the user (hook
installs, enforcement); first steps: the first ticket, the first dispatch using the packet.

## Existing projects

Auditing or migrating a project that already has its own setup is the `adopt-project-standards`
skill's job. It uses this skill's `references/`, `templates/` and `packs/` as its sources.
