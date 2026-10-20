---
name: adopt-project-standards
description: Run by the main session (never by a dispatched agent) when an EXISTING project with its own Claude setup (CLAUDE.md, agent briefs, docs, hooks) should adopt the machine's standards - "adopt the standards", "bring this project up to the standards", "align this project with the global rules", "migrate this project to the workflow", "reduce our local docs against global", "audit this project's setup", "what's missing from this project's workflow". Measures the project's transcripts, classifies every local rule against ~/.claude/CLAUDE.md and the project-setup templates, and migrates in reviewed waves ranked by measured savings. Brand-new projects use project-setup instead.
---

# adopt-project-standards

The user, 2026-10-20: "We should also have another skill which is adopt-project-standards ... that way
existing setups can adopt it." `project-setup` bootstraps a new project; this skill brings an existing
one in line without losing what makes it work.

## Binding

- **Main session only, and changes only in this session's project** (writes are limited to it). For
  another project, run Steps 1-2 read-only and write nothing in that project: hand the baseline and
  the table to that project's own session, or save them under this project's
  `.claude/scratch/<role or session>/<task>/`. In a project with a PM workflow, the PM session runs
  this skill itself.
- **The project's own CLAUDE.md governs the mechanics:** backups before edits, changelogs, tickets,
  branches, freezes it has declared (e.g. a ruling against hook work). Follow it; if it conflicts with
  a step here, ask.
- **No rule is lost.** A local rule is removed only when `~/.claude/CLAUDE.md`, or a doc the project
  keeps, states it equally or more strictly. Conflicts go to the user and are never resolved silently.
  A local rule that looks machine-wide is proposed to the user as a global candidate, never moved.
- **Never overwrite or delete without the user's OK**, given per wave on a shown diff.
- **Hooks and skills go through the review phase** (global rule), settings.json wiring included; hooks
  start log-only.
- **One home for the standards:** the global rules are `~/.claude/CLAUDE.md`; the templates, workflow and
  language packs are in `~/.claude/skills/project-setup/` (`references/workflow.md`, `templates/`,
  `packs/`). Read them there; do not copy them into this skill.

## Step 1 - Baseline

```
python -B "C:\Users\ShadowEvil\.claude\skills\adopt-project-standards\tools\transcript_metrics.py" --project "<absolute project path>"
```

Optional `--since YYYY-MM-DD` limits it to recent sessions. Save the output where it survives until
Step 5: `.claude/metrics/adoption-baseline-<date>.txt` (tracked, not scratch). In report-only mode for
another project, never save it there (see Binding). Summarize the top findings in at most 8 lines: orientation
median/p75, the docs share and the most expensive doc files, hook denials by rule, the worst tool error
rates, and the habit counts. With no transcripts yet, say so and continue without numbers.

## Step 2 - Inventory and classify (read-only)

Read `~/.claude/CLAUDE.md` in full. Then read the project's CLAUDE.md, `.claude/agents/*.md`, the
index files under `.claude/docs/` (use indexes and grep rather than reading every sidecar),
`.claude/hooks/` rule docs if present, `.claude/skills/` names and `.gitignore`. Produce one table:

| Item (file, section) | Global or template equivalent | Verdict | Proposed change | Wave |
|---|---|---|---|---|

Verdicts:
- **duplicate** - same rule, no extra local meaning: remove it, or reduce it to a one-line pointer where
  readers need to know it applies there.
- **narrower locally** - keep only the project-specific part (e.g. branch naming, a stricter review
  rule, tool-specific traps); strip the generic half the global file carries.
- **conflict** - local and global disagree: the user decides.
- **global candidate** - local but machine-wide in nature: propose it to the user.
- **missing** - a template component the project lacks (tickets, changelogs, backups, metrics,
  language pack). Offer it; a component the project has declined is not a gap. The docs standard is
  global and is never missing.

Also check, and add rows for what fails:
- **CLAUDE.md:** its size (it loads every session), binding rules stated in full, a read-when index.
- **Agent briefs:** write paths, `model:` and `tools:` set deliberately, the hooks block, the report shape.
- **Docs:** single-file docs with several standalone topics (many greps into one file means readers
  want one section); files over ~10 KB with no italic assessment line; index entries without a path or
  a WHEN.
- **Hooks:** which are log-only and which enforced, and whether each has a recorded review.
- **Layout:** scratch outside `.claude/scratch/` (the global layout, 2026-10-20) is a row; so is a
  `.gitignore` that does not cover `.claude/scratch/` or the components the project chose.

## Step 3 - Plan waves, ranked by measured savings

Default order, re-ranked by the Step 1 numbers:
1. **CLAUDE.md against the global file.** It loads every session, so each removed duplicate pays on
   every turn of every agent.
2. **Agent briefs:** model and tools set deliberately; the "Rules enforced by hooks" block kept (until a
   re-measure shows the global statement is enough); restated global habits removed.
3. **Docs to lean indexes:** start with the files that pull in the most characters and draw the most
   searches (Step 1 B). Follow `~/.claude/docs/documentation_standards.md`; a project's own docs
   standard shrinks to its project-specific additions plus a pointer there (a duplicate row in
   Step 2). Record each verdict in the doc's italic line,
   keep cited section numbers resolvable, and repair every reference.
4. **Friction from Step 1 C/D:** a rule that agents keep violating gets stated where they read it
   (their brief); tool errors become tool tickets for the tool's owner, not hooks.
5. **Optional components** the user opts into, each done exactly as `project-setup` Step 4 describes
   (tickets through the ticket system's ADOPTING.md; backups with the depth check; language packs per
   their pack file).

Present the table and the waves, then ask which waves to run (multiSelect, at most 4 options per
question: with five waves, ask waves 1-3 in one question and 4-5 in a second).

## Step 4 - Apply a wave

- The project's rules first: backup set, a ticket if the project uses tickets, a changelog bullet after.
- Show the diff, apply on OK, then grep for every moved path and heading and repair references.
- Verify: every pointer resolves; `git status --short` shows only the planned files.
- Report per wave: files changed and bytes removed (CLAUDE.md separately).

## Step 5 - Re-measure

After enough new work (about 50 dispatches, or a week of normal use), re-run Step 1 with
`--since <adoption date>` and compare against the baseline like-for-like: docs share and per-read
characters for the files that were split, hook denials per rule, heredoc and PowerShell-search counts,
orientation median and p75. Report the deltas with their sample sizes. A change that did not help is
reported as such.

## Report

Waves applied; bytes removed (CLAUDE.md first); conflicts and global candidates waiting on the user;
the baseline file's path; when to re-measure.
