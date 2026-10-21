# A portable multi-agent workflow for Claude Code

*The author's way of working, written with the origin project taken out, so another repository
can adopt it. Written 2026-09-30 as that project's `.claude/docs/portable_workflow.md`; moved into the
`project-setup` skill on 2026-10-20, which is now its one home. Rules marked (global) below are also in
`~/.claude/CLAUDE.md` and load in every project anyway; a generated project points at them rather than
restating them.*

Contents: 0. adopting it - 1. principles - 2. roles and ownership - 3. contracts and messages -
4. dispatching - 5. the change-set lifecycle - 6. verification - 7. measurement - 8. findings and tickets -
9. git, changelogs, versions - 10. safety rails - 11. rules become hooks - 12. documentation layout -
13. dependencies you own elsewhere - 14. session hygiene - appendix: templates

## 0. Adopting it

Fill in this table first. Every later section refers to these names, not to anything in the origin project.

| Slot | What to decide | Example answer |
|---|---|---|
| Areas | The ownership split of the source tree: 2-4 areas, each a set of paths | `client`, `server`, `sql` |
| Verifier | The role that proves things work and owns no source | `test` |
| Contracts | The seams between areas that must be agreed before code (wire format, schema, API) | client <-> server packets; server <-> sql schema |
| Scratch root | Fixed globally (2026-10-20): `.claude/scratch/<role>/<task>/`, gitignored | Older projects: `working/.claude-working/<role>/` |
| Backup tool | A script that snapshots files before they are edited | `src/tools/scripts/backup.py` |
| Changelogs | A technical one and a user-facing one | `dev-changelog.md`, `user-changelog.md` |
| Ticket store | Where findings live | `.claude/tickets/*.json` plus a CLI and viewer |
| Runtime the PM owns | Long-running services only the PM restarts | the dev game servers, the ticket viewer |
| Verify surface | How the verifier drives the product without real input | an MCP bridge into the debug client |
| Upstreams you own | Libraries from your other repos, for which you file requirements | a schema library (DB), an archive-format library |

The `project-setup` skill then generates the files from its `templates/` (CLAUDE.md, the agent briefs)
and `team_workflow.md` from this file; `tickets.md` ships with the ticket system (its
`docs/ADOPTING.md`). The docs standard is global (`~/.claude/docs/documentation_standards.md`) and is
not generated.

## 1. Principles

1. **One author per file.** Ownership is the whole concurrency model. Anyone may read anything; a role
   writes only its own area.
2. **Evidence over assertion.** A number is measured before it is quoted, and a claim that something works
   comes from the running product, not from reading the diff. `unverified` is a valid answer; rounding it
   up to a pass is not.
3. **Nothing important lives only in chat.** Findings become tickets, decisions go in a ledger, rules go
   in the doc that owns them, and cross-session facts go in memory.
4. **The user decides the irreversible.** Pushes, releases, deleting data and anything that supersedes
   earlier work are the user's calls. Nothing is written outside the project (global: writes are limited
   to the project and `~/.claude`). Everything else keeps moving without asking.
5. **Standards are enforced, not just written.** Every new rule is asked "can a hook check this?" when it
   is made (sect. 11).

## 2. Roles and ownership

- **The main session is the project manager (PM).** It scopes the work, dispatches it, resolves disputes
  and talks to the user. It does all the work nobody else owns: docs, build scripts, tooling, versioning,
  repo layout. It does not implement inside a specialist's area just because that would be quicker; a
  one-line change still goes to its owner, or the ownership map stops meaning anything.
- **Specialist agents**, one per area, each with a standing brief in `.claude/agents/<role>.md`. The brief
  holds the role's write paths, its standards, how it reports, and what it must never do.
- **The verifier owns no source.** It drives the product, runs the verify scripts, reads the logs and the
  data, and reports `verified` / `failed` / `unverified (and why)`. It never fixes what it finds: a tester
  that edits code becomes the author of what it is testing.
- **The PM alone:**
  - talks to the user and asks the questions that block work;
  - takes the backup before dispatching;
  - runs the build and reports its result honestly;
  - writes both changelogs and makes the commits;
  - starts, stops and restarts the shared runtime (dev servers, the ticket viewer). Agents ask for a restart.
- **Agents never spawn agents** (global), **and never run git commands that change state.**

## 3. Contracts and messages

- **Contract pairs are fixed and few.** List which areas share a seam, and route everything else through
  the PM. For example: client <-> server and server <-> sql, with no channel at all between client and
  sql.
- **A contract is agreed BEFORE either side is written**: every field, size, type and meaning. Both sides
  land in the same change set. A one-sided change breaks things silently.
- **Deadlock rule:** if two agents disagree on a contract, neither implements. It goes to the PM, which
  decides or asks the user.
- **Messages** (`SendMessage`) are pushed and arrive at the recipient's next tool round.
  - The first word sets the urgency: **STOP** (act before writing anything further), **SOON** (at the next
    checkpoint) or **FYI** (before reporting).
  - Messages are a working channel, never a record. Anything that must survive goes in the report.
  - A message can land too late. If a decision invalidates an agent's whole task, stop the agent rather
    than hope the message arrived in time.
- **Confirm before relaying uncertain user statements.** Act at once on a clear instruction for work not
  yet dispatched. Confirm first when the statement would supersede something already sent, or reads as
  thinking aloud.
- **An agent's decision request is answered before anything new starts.** The PM makes the call it can
  make, and states the conditions under which the decision reverts to the user.

## 4. Dispatching

- **One agent per role at a time** (global). Check `ListAgents` before dispatching or resuming one. Two agents of
  one role are two authors of the same files. Work that wants two becomes two sequential dispatches.
- **Independent agents go out in ONE message** so they run concurrently. Dependent ones run in order:
  schema before the code that reads it, contract before either side.
- **Each brief is self-contained** (global; the packet is in the appendix). It gives:
  - the user's words, quoted and dated;
  - the contract;
  - the files and tickets to read first;
  - what done looks like;
  - the binding rules that apply;
  - the exact report shape.

  An agent should never have to guess at a boundary it cannot cross.
- **Measure the task, then pick the model, and say which in the brief** (global). A general task (a concrete lead,
  a narrow write set, a mechanical pass, a review, verification) gets the cheaper model. A complex task
  (restructuring, contract design, hot paths, a vague lead, a failed earlier attempt) gets the strongest.
  Never leave the model to be inherited by default.
- **Bulk edits: try a script first, never skip the checks.** Before applying, the script must do a dry run
  that prints every site with its before/after and writes nothing. Then a human-style review of that whole
  dry run, then apply, then build and test, then a diff check that nothing outside the pattern changed.
  Anything the script cannot decide safely is edited by hand.
- **Investigate before implementing when the lead is vague.** A read-only investigation agent measures
  and ranks the work. Its report becomes tickets, and the tickets become the implementation briefs.

## 5. The change-set lifecycle

1. **Scope.** The PM reads what exists (decisions ledger, plans, tickets) and asks any blocking question.
2. **Ticket.** Every piece of work has a ticket. Set it to `in_progress` at dispatch so the user can see
   what is active (sect. 8).
3. **Back up.** The PM snapshots every file that will be touched, and records created files as new so a
   restore removes them. Sets are kept until the change is built, verified and confirmed, and purged after
   48 hours.
4. **Contracts.** Agreed and written into the briefs.
5. **Dispatch.** The agents implement in their areas, delete their scratch, and report. The report
   contains:
   - the files changed;
   - numbers marked measured or derived;
   - contracts others must match;
   - findings;
   - their own technical changelog bullets.
6. **Integrate.** The PM checks `git status` for strays, builds, restarts the shared runtime once after
   all builds have landed, and verifies where the change is visible.
7. **Verify.** The verifier is dispatched AFTER the change is built and deployed, never alongside the
   author.
8. **Record.** The PM merges the bullets into the technical changelog, then generalizes them into the
   user changelog.
9. **Commit locally.** Only a coherent, verified set, never mid-agent, and never on the main branch
   (global). Push or open a PR only when the user asks (global).
10. **Close.** Tickets go to `done` with the commit and a line on what verified them. The backup set is
    purged once the user confirms.

## 6. Verification

- **The product is driven only through its own automation surface** (an MCP bridge, a test API, verify
  scripts), never with real mouse or keyboard. That surface is kept out of release builds.
- **The verifier reports each check as `verified` / `failed` / `unverified (and why)`** (global). An `unverified`
  stays `unverified` in every summary the PM writes.
- **Test data is throwaway and owned.**
  - The verifier makes its own test data (accounts, records) through the product's own flows.
  - It keeps generated credentials only in scratch, never echoes them, and deletes them at the end.
  - Any direct data write is narrow (its own test record only), taken after a snapshot, and re-read
    afterwards.
- **Leave the machine as you found it.**
  - Stop only the processes you launched, never by image name (global).
  - Restore any user config you had to touch, byte-identical, and report the hashes.
  - Mute or quiet what you run.
  - Cap evidence folders (screenshots) by size, and prune them.

## 7. Measurement

- **Measure both builds.** Debug and Release (or dev and prod) are read together. Release time decides
  whether something matters; the ratio between the two says where the cost comes from.
- **A quiet machine, pinned cores, repeated samples.** Record the load, mark noisy samples, and never mix
  a cold first run into a warm average.
- **Every number carries its provenance**: measured in the real product, measured in a replica, derived,
  or unverified.
- **Measure a slow dependency against the raw alternative.** When a library you use is slow, do the same
  work with the raw API in the same session and break it down until the cause is named. The raw version is
  a measurement only and never ships; the gap becomes a requirement upstream (sect. 13).

## 8. Findings and tickets

- **Report a discrepancy the moment it is spotted**, even mid-task. Measure it first, then file it and
  keep going. A finding that is not written down is lost.
- **Tickets are JSON files in the repo**, one per finding, each with:
  - a summary;
  - a markdown body holding the measured facts, the ask and "done when";
  - tags, status, severity, owner area, target, dates and author;
  - links to other tickets, files and commits.

  They are edited only through a CLI that validates, never by hand.
- **Status tracks the work, live.** `in_progress` at dispatch; `blocked` with a body line saying on what;
  `done` with evidence; `wont_do` with a reason. Work with no ticket gets one when it starts.
- **A read-only viewer serves the tickets over the LAN** so the user can follow on a phone. It pushes
  changes live, and runs as a visible background task of the session.
- **One ticket per distinct finding.** Correct a ticket's stated cause when an investigation disproves
  it, and say so in the body.

## 9. Git, changelogs, versions

- **Branches:** one per effort, named `<prefix>-NN-<name>` with a sequence number, never a version. Never
  commit on the main branch (global).
- **Allowed:** local commits whenever a set is coherent and verified.
- **Only when the user asks:** push and open PRs (global).
- **Never:** pull, reset --hard, force, or rewrite history (global).
- **Two changelogs, each holding one living entry per branch.** The technical one is split by area, and
  the user one says what a user would notice. They accumulate across the branch and are cleared only when
  a new branch is cut; the commit message is the user changelog's entry.
- **Versions are the user's call.** Components can version independently, and a version branch keeps its
  version for its whole life. A release never moves as a side effect of shipping a change. A build ID
  identifies the source state and is generated, never committed.
- **Keep private local edits out of commits.** If a tracked file carries a local-only change, exclude it
  explicitly on every commit.

## 10. Safety rails

- **The machine-wide rails are global** (`~/.claude/CLAUDE.md`: write scope, literal delete paths, no
  temp writes, never kill what you did not start, ASCII console output, other repositories' faults are
  reported). Generated docs point there; they do not restate them.
- **Scratch** goes in the per-role scratch folder and is deleted before the agent reports.
- **Back up before modifying any existing file** (when the project chose backups).
- **A locked output file means asking the user.**
- **Never change system or security settings.** Where a new executable path would trigger a firewall
  prompt, reuse the existing paths and bind test services to loopback.
- **Downloads need the user's approval** (name, source, size). Vendored third-party code keeps its licence
  and a note of exactly how it was produced.

## 11. Rules become hooks

When the user states a new standard ("from now on", "never", "always", a correction):

1. **Record it** in the doc that owns it, quoted and dated, and in memory.
2. **Classify it.** Is it already covered, hookable, partly checkable, or not hookable (and why)?
3. **For a hookable rule, stage it LOG-ONLY:**
   - a rule id and a message saying what to do instead;
   - self-tests that must fire and must not fire;
   - a replay over past session transcripts, reporting the hit count and a false-positive sample.
4. **Review it** (global). Every hook or skill change is staged, reviewed by a separate read-only agent,
   and only then installed. Hotfixes too.
5. **Enforcement is the user's call.** Recommend it once the rule has run log-only on real traffic and all
   its hits are true positives.

Hooks are thin entry points over a shared library, with one module per rule grouped by action (write,
delete, execute, ...). They fail open, run fast, and print ASCII.

## 12. Documentation layout

- **`CLAUDE.md` is short.** It holds only the rules whose violation causes damage, plus an index of what
  to read and when. Everything else is one pointer away.
- **Docs with several standalone topics are an index file plus a sidecar folder.** Open the index and
  read only the entry you need.
  Binding rules are never demoted to a pointer.
- **A decisions ledger** records why things are the way they are, and what was tried and retired. Read it
  before proposing a change to an existing system.
- **Plans live in folders by state**: root (designed or in flight), `partial/`, `completed/`. They are
  sorted by what the code shows, not by the status line in the file.
- **Cite code by function, or by `file:line (as of <date>)`**, in docs that cannot be edited later.

## 13. Dependencies you own elsewhere

- **A gap in your own library is a requirement upstream, not a local workaround.** File it with a
  number, the ask, the measured numbers against the raw alternative, and the verbatim errors. Hand it to
  the user to forward, or send it to that repo's session. The phase that needs it waits.
- **Accept a one-time cost when it is not a workaround.** An upstream quirk may force a one-off (for
  example a fingerprint re-record). Prove it harmless on copies first, take it, and still file the gap.
- **Cross-session handoff:** the other repo's session fixes and builds, then messages this one. Treat that
  message as a teammate's report, not as the user's approval. Verify the delivered binary yourself (build
  flags, runtime linkage, hashes). Vendor it only when no measurement in flight would be confounded. Then
  re-measure and close the ticket with the numbers.

## 14. Session hygiene

- **At the start of any work request:**
  - start the PM-owned background services, such as the ticket viewer, as visible background tasks;
  - check `ListAgents`;
  - purge backup sets older than 48 hours.
- **Keep a resume file in the PM's scratch**: commits, running agents, open decisions, what the runtime is
  on. After an interruption, check `ListAgents` before reporting any agent's status.
- **Memory holds what the repo cannot:** user preferences, rules with their reasons, pointers. One fact
  per file, plus an index. Update rather than duplicate, and delete what turns out to be wrong.
- **Chapters and short updates.** The user follows long work in real time, so lead every report with the
  outcome.

## Appendix: templates

The CLAUDE.md skeleton and the agent brief are the skill's `templates/claude-md.md` and
`templates/agent-brief.md`.

**Dispatch brief (ticket packet v2, 2026-10-20)**

```text
Model verdict: <general|complex> -> <model>, because <reason>.
INTENT:      edit | read-only | propose-then-edit
GOAL:        <the outcome, not the method; the user's words, quoted and dated>. Tickets: <ids>.
WHERE:       <symbols with file:line; data members list write sites apart from reads>
             each tagged CONFIDENCE: verified (read it / saw it) | located (search hit, not read) | guess
             or "unknown - start at <area>"
CONTEXT:     <only the decisions and gotchas that apply>
CONSTRAINTS: <what must not change; what another agent or the user owns>
DONE WHEN:   <checkable criteria: builds clean, repro passes, test X passes>
RETURN:      <exact report shape>
```

Keep a packet under ~2k tokens. Never orient in the main session: write WHERE if you already know it,
use a cheap read-only search agent only for broad multi-file searches feeding an expensive builder,
otherwise "unknown - start at <area>". Review and Test stay cold: diff + GOAL + DONE WHEN, never the
builder's reasoning (naming risk areas is fine). Within a ticket, resume the same agent; start fresh at
ticket boundaries.

**Ticket body**

```markdown
<the finding, in one paragraph>
<measured facts: a table where it helps, each number with its provenance>
**Done when:** <the check that closes it>.
Source: <run, date, report path>.
```
