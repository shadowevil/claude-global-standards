# <project>

<one line: what it is.>

This file is loaded automatically every session, so it carries only the rules whose violation causes
damage, plus an index of what to read and when. Everything else lives one pointer away. Machine-wide
rules (safety, git, dispatching, working habits) are in `~/.claude/CLAUDE.md` and are not repeated here.

## where things live

- `<folder>/` - <one line each; root stays clean>.
- `.claude/` - how the project is run, all tracked: `docs/` (stable working rules), `design/` (decisions
  ledger, plans), `agents/` (specialist briefs), `skills/`, `tickets/`.
- `.claude/scratch/` - scratch, gitignored, `<role>/<task>/` (global layout).

## git, backups, changelogs (binding)

- Branches: `<prefix>-NN-<name>`, cut from `<default branch>`. (Push/pull/force rules are global.)
- <IF backups> **Back up before modifying** any existing file: `<backup tool>`; sets purged after 48 h.
- <IF changelogs> **Maintain the changelogs** for every completed change: `<technical>` first, then
  `<user-facing>`; one living entry each, cleared when a new branch is cut. Format: `.claude/docs/changelog_format.md`.

## the team (binding)  <IF team>

**You are the project manager.** <N> specialists implement<, a verifier verifies>; the main session scopes,
dispatches, builds and owns everything the user sees. Briefs: `.claude/agents/{<roles>}.md`.

- `<role>` owns `<paths>`. ... (one line per role)
- Every agent may READ the whole repo; an agent WRITES only inside its own area.
- Contract pairs: <pair>, <pair> - agreed BEFORE either side is written.
- Agents delete their `.claude/scratch/<role>/<task>/` before they report.
- **PM-only:** the user conversation, <the backup set,> the build and its honest result, <the changelogs,>
  commits.

Dispatch mechanics and the change-set lifecycle: `.claude/docs/team_workflow.md`.

## code (binding)

<one line per rule whose violation causes damage; a language pack fills this in. Mechanism and examples
live in `.claude/docs/coding_standards.md`.>

## tickets  <IF tickets>

<The wiring the ticket system's ADOPTING.md sect. 9 asks for: where tickets live, the CLI, the viewer
as a background task, and that status tracks the work. Details stay in `.claude/docs/tickets.md`.>

## what to read, and when

Docs are index files plus sidecar folders: open the index, take the one section you need
(`~/.claude/docs/documentation_standards.md`, the machine-wide standard).

- `<doc>` - read <WHEN>.
