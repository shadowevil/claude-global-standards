# changelog format

Every completed change is recorded in **two** files at the repo root, written as one step each:

| File | Audience | Content |
| --- | --- | --- |
| `user-changelog.md` | users and non-coders | Generalized bug fixes, feature additions and feature changes - what someone using the product would notice |
| `dev-changelog.md` | the team | Verbose and technical, split by the area (agent) that did the work |

Both hold **exactly one living entry** covering everything done so far ON THIS BRANCH, and
**both are cleared when a NEW BRANCH is cut - never at a commit**. They are not running histories,
but they are not per-commit either: the entry accumulates across every commit on the branch, so
that when the branch is PR'd it already describes the whole effort. The summary lives in
`CLAUDE.md`. Committed history is `git log`.

## When to clear

**Clearing is a BRANCH event, not a commit event.** Cut a new working branch -> clear both files as
the first act on it, before any work lands. That is the only time either file is emptied.

A commit never clears them. The entry that produced the commit message stays in place and the next
piece of work on that branch merges into it, so the files always describe the branch, not the last
commit.

## Before writing either file

**Read the file itself** - the tree's state is irrelevant, because a clean tree right after a
commit still has a live entry that must be kept:

- **File empty** -> write a fresh entry (this is a newly cut branch).
- **File non-empty** -> **merge** the new work into the existing entry: drop each new bullet into
  its category, revise the title so it covers everything the branch has done, and revise the
  summary and date. Never stack a second titled entry below the first.

**The consequence for commit messages, stated so it is not a surprise:** the message is assembled
from `user-changelog.md`, and that entry grows over the branch - so a later commit's message
restates work already described by an earlier one. That is intended. The message describes the
branch's state at that commit, and the final one is the complete entry the PR wants. Do not trim
the entry to make one commit message tidier; the entry belongs to the branch.

The two files always describe the SAME pending change set and carry the SAME title. Write the
dev entry first (it is assembled from what the agents reported), then generalize it into the user
entry - never the reverse, or detail gets invented rather than summarized.

## `user-changelog.md`

1. First line: `## <title>` - one plain sentence describing the pending set. **No dates in the
   title.**
2. Category sections, in this fixed order, including **only** the non-empty ones:
   `**Fixed**`, `**Added**`, `**Changed**`, `**Removed**`.
3. Last line: `Summary: <one or two short sentences> (YYYY-MM-DD)`.

Rules:

- **One sentence per bullet** (two only if truly needed). Terse.
- No jargon, file paths, function names, table names or agent names.
- Describe the change a user would notice, never how it was built.
- Documentation, tooling, build and refactor work does **not** appear here unless a user would
  actually notice it. That work lives in the dev changelog's Project section.
- The summary is a plain-language recap of the whole set, not a feature-by-feature walk-through.
  If it runs past two sentences, cut it back.

## `dev-changelog.md`

1. First line: `## <title>` - the same title as `user-changelog.md`.
2. Area sections, one per area in the project's ownership split, in a fixed order, then
   `### Project` last; include **only** the non-empty ones. (Example for a web app:
   `### Database`, `### Server`, `### Client`, `### Project`.)
3. Inside each area section, category sub-groups in this fixed order, including only the
   non-empty ones: `**Fixed**`, `**Added**`, `**Changed**`, `**Removed**`.
4. Last line: `Summary: <one or two technical sentences naming the areas touched> (YYYY-MM-DD)`.

What goes in which section:

- **Each area** - the paths that area owns, and its side of any contract it shares with another
  area.
- **Project** - work owned by no area: documentation, build scripts, versioning, tooling, repo
  layout, agent definitions.

Rules:

- Technical detail is wanted here: file paths, symbol names, table and column names, migration
  numbers, config keys.
- Still **one sentence per bullet**. Verbose means precise, not rambling - no implementation
  walk-throughs.
- A cross-boundary change appears in **both** sections it touched, each from that side's point of
  view, and each naming the contract it relies on (`requires data migration 0005`, `matches the new
  repair-request fields`).
- Each agent reports its own bullets; the project manager merges them. Agents never edit this
  file directly.

## Why

The two files answer two different questions. `user-changelog.md` is the human-facing record for
someone who does not read code, and the next commit message is assembled from it.
`dev-changelog.md` is the engineering record of the same set, split so it is obvious which area
each change landed in and which contracts crossed a boundary. Keeping one living entry in each
means a commit is described by a single current pair rather than a stack of stale fragments.
