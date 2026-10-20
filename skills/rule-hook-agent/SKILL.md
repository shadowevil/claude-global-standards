---
name: rule-hook-agent
description: Run by the main session (never by a dispatched agent) whenever the user states a new standard, rule or ruling ("this is the standard", "going forward", "from now on", "never ...", "always ...", "must ..."). Records the rule where the project keeps it, then has an agent decide whether a Claude Code hook can enforce it, build it as a project hook with self-tests and a replay measurement, and say whether it could be global.
---

# rule-hook-agent

The user, 2026-09-22: "anytime I say this is a new standard or something along those lines, we should
launch that to determine if it's hook worthy." And: "Compliance is key." Made global on 2026-09-28.

A written rule is a request; a hook is enforcement. Every new standard is asked "can a hook check
this?" when it is made, not in a later sweep.

**Every hook and skill write is reviewed before it is installed** (global rule, 2026-09-28: "All hook
and skill writes need to go through a review phase") - including edits to this skill, and hotfixes.
This binds every main session that runs this skill, in any project, and every agent it dispatches -
not just this session: no one installs a hook or skill that a separate read-only reviewer has not
passed.

**The project's own rules govern the mechanics** - where rules are recorded, backups before edits,
changelogs, which model an agent gets, where scratch lives. Read its `CLAUDE.md` and follow it; this
skill says only what is the same in every project.

## When to run it

Any user message that sets or changes a standard: "this is the standard", "going forward", "from now
on", "never", "always", "must", "we do not want X ever", or a correction of how agents work ("you are
not allowed to", "stop doing"). Do not wait to be asked. Run it once per rule; one message with
several rules is one brief.

## Step 1 - record the rule (main session, before dispatching)

1. Quote it exactly, dated `(user rule, YYYY-MM-DD)`, where the project keeps rules of that kind
   (`CLAUDE.md` for a binding rule, else the doc the project's `CLAUDE.md` names). Back up and log it
   the way the project requires.
2. Save or update a memory (`feedback` or `project`) so it survives the session.

## Step 2 - dispatch the hooks agent

Agent type `claude`, background; the project's model rules, else Sonnet to add a rule to an existing
framework and Opus when there is none yet or the rule is hard to classify. One hooks agent at a time -
if one is running (`ListAgents`), SendMessage it the rule instead.

> You ARE the hooks agent: do the work below yourself. Do not load the rule-hook-agent skill and do
> not dispatch agents.
>
> The user stated a new standard on <date>: "<exact words>", recorded at <file:line>. Decide whether
> a Claude Code hook can enforce it, and if so build it.
>
> The project's framework is `.claude/hooks/` (read its `RULES.md` first). **Hooks are separate
> files, not one big Python file** (user standard, 2026-09-28): thin entry points (one per matcher),
> a shared library package split by concern (paths, shell parsing, output/logging), and ONE MODULE
> PER RULE holding its check, its message and its self-test cases; `--self-test` collects them all.
> Rule modules are **grouped by the action** the call performs ("group hooks by action such as write,
> read etc..."): `rules/write/`, `rules/delete/`, `rules/read/`, `rules/execute/`, ... - the entry
> point classifies each call into its action(s) and runs those rules. A new rule is a new module in
> its action's directory. Also: a replay script over the
> project's past transcripts (`~/.claude/projects/<project-slug>/*.jsonl`), `RULES.md` (one section per
> rule) and the wiring in `.claude/settings.json`. **If it does not exist yet, create the minimum of
> it** in that layout: stdlib-only Python, log-only by default, hits appended to a gitignored log
> inside the project.
>
> 1. Classify: (a) already covered - name the rule id; (b) hookable; (c) partly checkable - say
>    which part; (d) not hookable - say why. Say whether a build-time check or a git hook would fit
>    better.
> 2. **Scope.** ALWAYS build it as a PROJECT hook. If its effect is system-wide - it guards the
>    machine or every session rather than this codebase, like where a session may write - push back
>    in the report: "this could be a global hook because ...". Build such a rule generalized anyway
>    (root = `CLAUDE_PROJECT_DIR`, nothing about the project hard-coded), so lifting it is a copy.
> 3. For (b)/(c): add the rule in LOG-ONLY mode - a new id, its message, must-fire and must-not-fire
>    self-test cases, and a replay run with the hit count and a hand-checked false-positive sample.
>    Never put it in the enforce set.
> 4. Work on a copy of `.claude/hooks/` and `.claude/settings.json` in the project's scratch
>    directory, never on the live files and never outside the project. After every edit run
>    `python -m py_compile` and the self-test. Hooks fail open, are fast, print ASCII only and never
>    block while log-only. Touch nothing else.
> 5. Add the rule's section to `RULES.md`: the rule, its source, the class, the id, the measurements.
> 6. Report: the staged path, class, rule id, self-test result, replay numbers, the scope
>    push-back if any, and whether you think it is ready to enforce.

## Step 3 - after it reports

1. **Review phase, never skipped.** A second `claude` agent (Sonnet, read-only: it reports and edits
   nothing) audits the staged rule and tries to break it. Findings go back to the hooks agent, and a
   changed stage is reviewed again before install.
2. Install into the project: back up `.claude/hooks/` and `.claude/settings.json` as the project
   requires, copy the staged files in, re-run the self-test from the live path, and point the rule's
   text in its owning doc at the id.
3. **Rule on any "could be global" push-back yourself.** The user, 2026-09-28: "if you push back and
   say no, this isn't - you can automate that process." If you judge it project-only, the project
   install IS the whole process. Only a push-back you agree with goes to the user (item 6).
4. Tell the user in a line or two: hookable or not, the id, log-only, the enforce recommendation,
   and the scope ruling.
5. **Enforcing is the user's call** until they delegate it, per project - a delegation in one
   workspace does not carry to another. Recommend enforcement once a rule has run log-only on live
   traffic, its hits are all true positives, and an audit has passed it.
6. **Global hooks need the user's approval** (2026-09-28): "Global hooks need to be approved by me -
   but it can be suggested going forward." Suggest it with the exact script path under
   `~/.claude/hooks/` and the exact user `settings.json` diff; write nothing there until the user
   approves that specific install. A global hook hard-codes nothing about any one project.
