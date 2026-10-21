---
name: pattern-skill-agent
description: Run by the main session (never by a dispatched agent) when the same medium-to-large piece of work has now been done the same way three times (a wave, a deploy, a merge, a verification pass), when the user says "we keep doing this" or "make this a skill", and when a session's work wraps up. Logs the instance in the project's candidates ledger and, once a pattern is established, has a skill drafted from the real instances, proven concise and correct, and installed only on the user's OK.
---

# pattern-skill-agent

The user, 2026-09-25: "once we have established a repeating pattern, we can create a skill for it so
it is done the same way everytime. However, we need to make sure it's concise and correct before we
implement it." Made global on 2026-09-28.

`rule-hook-agent` turns a **rule** into a hook. This turns a **procedure** into a skill. A pattern
that carries a new rule goes to `rule-hook-agent` as well.

**Every hook and skill write is reviewed before it is installed** (global rule, 2026-09-28: "All hook
and skill writes need to go through a review phase") - including edits to this skill, and hotfixes.
This binds every main session that runs this skill, in any project, and every agent it dispatches -
not just this session: no one installs a hook or skill that a separate read-only reviewer has not
passed.

**The project's own rules govern the mechanics** - backups, changelogs, agent models, where scratch
lives. Read its `CLAUDE.md` and follow it.

## What qualifies

Work of several steps, often across roles or tools, that went the same way each time and costs
something when a step is missed or reordered. Not a one-liner, not a rule (that is a hook), and not
a fixed command sequence with no judgement in it - that is a script in the project, and the skill,
if any, calls it.

## Step 1 - log the instance (every time)

Add a row to the project's ledger, `.claude/skills/CANDIDATES.md` (create it if missing), under the
pattern or a new one: date, session id, what was done, and one line on how it differed from last
time. Do it when the work finishes - the rows are the evidence.

**Established** = three logged instances that followed the same steps (two, if the user asks for the
skill). A step that varied is either a parameter of the skill or a sign the pattern is not settled.

## Step 2 - draft (one agent)

Agent type `claude`, background; the project's model rules, else the global model-tier rule:
Sonnet by default and Opus when the instances disagree or an earlier draft failed. One skills agent at a time; if one is running
(`ListAgents`), SendMessage it instead. Brief it with the ledger rows and:

> Draft `SKILL.md` for the pattern "<pattern>" in the project's scratch directory, never in the
> live skills folders and never outside the project. Evidence: <ledger rows>. Model the shape on
> `~/.claude/skills/rule-hook-agent/SKILL.md`. Do not load skills or dispatch agents.
>
> 1. **From the instances only.** Read each instance's transcript or commits. Every step must appear
>    in at least two instances; say which. Nothing invented, nothing "nice to have".
> 2. **Point, don't copy.** A rule that lives in the project's `CLAUDE.md` or its docs is linked,
>    not restated. Restated rules drift.
> 3. **Verify every name.** Each path, script, flag, tool and agent type is checked to exist. List
>    what you checked.
> 4. **Dry run.** Walk the most recent instance through the draft. Report each place the draft would
>    have done something different, and which is right.
> 5. **Concise.** The `description` says when to use it, in two sentences at most. Body under 80
>    lines. A step that is one command is one line. ASCII only.
> 6. **Scope.** Draft it as a PROJECT skill. If nothing in it is specific to this project, push back
>    in the report: "this could be a global skill because ...".
> 7. Report: the draft's path, the step-to-instance table, the names checked, the dry-run
>    differences, any rule found inside the pattern, and the scope push-back if any.

## Step 3 - prove it before installing

1. **Review phase, never skipped.** A second `claude` agent (Sonnet, read-only) reads the draft
   against the ledger rows and the project's docs: correct (no unsupported step, no contradicted
   rule, no dead name) and concise (nothing that could go). Findings go back to the drafting agent,
   and a revised draft is reviewed again before install.
2. Show the user the draft's path and three lines: what it covers, when it triggers, what it
   replaces. **Install only on the user's OK.**
3. Install as a project skill at `.claude/skills/<name>/SKILL.md`, backed up and logged as the
   project requires; mark the pattern `skill: <name>` in the ledger and save a memory pointing at it.
   Rule on any "could be global" push-back yourself: if you judge it project-only, the install above
   is the whole process; only a push-back you agree with is put to the user separately, and a global
   skill goes to `~/.claude/skills/` only on their approval of that install.

## Step 4 - first live use

The next time the pattern comes up, run the skill and log that instance too. Where the skill and what
actually had to happen differ, fix the skill, not the instance. A skill worked around twice is wrong;
take it back to Step 2.
