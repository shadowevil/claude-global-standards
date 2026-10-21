# Global user instructions

These apply to every project on this machine (loaded for all sessions, in addition to any
per-project CLAUDE.md and project memory).

## Environment

- **Python is invoked as bare `python` on this machine, never `python3`.** Bare `python` is the
  real interpreter (`%LOCALAPPDATA%\Programs\Python\Python313\python.exe`, currently 3.13.x).
  `python3` resolves to a non-functional Windows Store alias (`%LOCALAPPDATA%\Microsoft\WindowsApps\
  python3.exe`) that just prints the "Python was not found" store prompt. This holds in both
  PowerShell and Git Bash. Always call `python ...` for any Python script or one-off.

- **Default to ASCII.** The console here is cp1252, so anything written to stdout or stderr must be
  ASCII: no arrows, em dashes, ellipses, curly quotes or box drawing. A single such character
  raises `UnicodeEncodeError` and takes the whole script down, which has already broken a deploy
  script mid-run. Use `->`, `-`, `...`, `'` instead.

  This applies to **printed output**: `print()`, log lines, CLI banners, error messages. It is not a
  ban on UTF-8 in files. Source, Markdown and CSS may contain whatever they need, provided they are
  read and written with an explicit `encoding="utf-8"`.

  When output genuinely has to carry non-ASCII (dumping a file, echoing user data), reconfigure the
  stream first and keep going if that fails:

  ```python
  try:
      sys.stdout.reconfigure(encoding="utf-8", errors="replace")
  except Exception:
      pass
  ```

  For a throwaway one-liner, `python -X utf8 -c ...` does the same job.

## Safety (binding in every project)

- **Write access is the session's own project directory and `{{CLAUDE_HOME}}`, with their
  subdirectories - nothing else; everything outside them is read-only** (user ruling, 2026-10-20). No
  sibling worktrees, parent folders or other repos: work on another folder from a session opened there.
  Give every write target as an absolute path; never `../` chains. Enforced by the global write guard (W3).
- **Deletes name literal absolute paths** (user rule, 2026-09-28). Never a relative path, `$variable`,
  `${...}`, `$env:`, `~`, `$(...)`, a pipe, or a loop over a variable. Several literal paths in one
  command are fine. A wildcard only in the final file-name segment, never with `-r`/`-rf`/`-Recurse`
  and never on `rmdir`. Verbose but safe. Enforced by the global write guard (W2).
- **Git** (user ruling, 2026-10-20: global): local commits are allowed; **push and PRs only when the user
  asks**; **never pull, `reset --hard`, force, or rewrite history**. Branching is loose and per project
  (user, 2026-10-20): prefer a feature or patch branch; each project's CLAUDE.md sets its own convention.
- **Never kill a process you did not start.** Stop by PID, never by image name; a running copy may be
  the user testing.
- **Work aimed at another repository is not automatically ours.** Report faults in tools you consume;
  do not go and change them.

## Working habits (measured friction, 2026-10-20)

- **Write scripts with the Write tool** into the scratch folder, then run them. No heredocs
  (`cat > f <<EOF`, `python - <<EOF`) and no PowerShell here-strings; they caused most quoting failures.
- **Never `sed -i` (or rewrite through `>`) a tracked file from Git Bash:** it converts CRLF to LF and
  turns every line into a diff, hiding the real edit from review. Edit with the Edit tool or a
  bytes-preserving script; verify with `diff --strip-trailing-cr`. (Measured, 2026-10-20.)
- **Search code with the Grep tool** (or `grep`/`rg`), not PowerShell `Select-String`.
- **CLAUDE.md files and an agent's own brief are already in context.** Never Read or grep them.
- **PowerShell 5.1:** a trailing `Get-Process ... -ErrorAction SilentlyContinue` that matches nothing
  makes the whole command exit 1. Check processes in a separate call.
- **Evidence over assertion.** Report checks as `verified` / `failed` / `unverified (and why)` and never
  round `unverified` up; every number says whether it was measured, derived or is unverified.

## When dispatching agents (any project; user ruling, 2026-10-20)

- **Only the main session dispatches.** Agents never spawn agents.
- **Briefs are self-contained:** intent (edit / read-only / propose-then-edit), goal, where (each location
  tagged verified / located / guess), constraints, done-when, report shape - and the model chosen and
  why. Never orient in the main session's context; if the location is unknown, say where to start.
- **Pick the cheapest model that does the job well** (user rule, 2026-10-21), unless the project's
  CLAUDE.md sets its own. Haiku for clear, fully specified actions: "edit this line, in this
  document", bulk edits ("edit these lines in these documents"; the user: "Haiku, now with the 5.5
  release, can do more than just read-only work"), and mechanical lookups, counts, extracts and
  reformats. Sonnet is the default: edits that follow an existing pattern, checklist reviews against
  a clear spec, verification runs, docs. Opus for design and new frameworks, security-sensitive code
  (anything that denies, deletes or guards writes), hard debugging, ambiguous classification and
  attack-testing. A reviewer is never a lower tier than the author on security-sensitive code. An
  agent whose report shows it was out of its depth is re-run one tier up, not patched around.
- **One agent per write area at a time.** Within one task, resume the same agent with SendMessage;
  start fresh at task boundaries.
- **Code changes get a separate read-only review agent** before commit (a project may narrow this for
  trivial fixes). Reviewers and verifiers get the diff and the goal, not the author's reasoning.
- **The verifier never fixes what it finds.**

## Docs: lean index files (user ruling, 2026-10-20)

- **A doc agents read that holds several standalone topics is a lean index plus a sidecar folder.**
  `topic.md` holds one line per entry: the path, what it covers, and WHEN to read it; the content lives
  in `topic/<entry>.md`. A lookup is one small index read, then one targeted read of the path it names,
  never a whole-file read. (Measured 2026-10-20 over 1,482 subagents: doc reads were 14% of
  all tool output pulled into context, mostly from a few single-file docs.)
- The index points and never restates; binding rules stay stated in CLAUDE.md. Do not split a cohesive
  doc or one whose parts would be a few hundred bytes; give it a Contents block instead.
- The full standard is `{{CLAUDE_HOME}}\docs\documentation_standards.md` - read it before
  adding or restructuring a doc. Projects keep only their own additions locally.

  ```markdown
  # environment (index)
  - `environment/shells.md` - Bash vs PowerShell quirks; read before running any command.
  - `environment/build.md` - build and deploy commands; read before building.
  - `environment/databases.md` - safe read-only database access; read before any query.
  ```

## Hooks and skills

- **Every hook and skill write goes through a review phase** (user rule, 2026-09-28: "All hook and
  skill writes need to go through a review phase, that is a global rule"). This covers creating,
  editing or deleting any hook script, hook wiring in a `settings.json`, or `SKILL.md`, in a project
  (`.claude/hooks/`, `.claude/skills/`) or globally (`~/.claude/hooks/`, `~/.claude/skills/`), however
  small the change and whoever makes it. Stage the change first, have a separate read-only agent review
  it (it reports and edits nothing), and install only after the review passes. A hotfix is no
  exception: review it, then install. It binds every Claude session and every agent, in every project,
  and the `rule-hook-agent` / `pattern-skill-agent` skills carry it too ("It needs to be for any claude
  session when using the skill(s)").
- **Global hooks need the user's approval** (2026-09-28). Suggest one; install only when the user
  approves that install.
- **Hooks are separate files grouped by action** (2026-09-28): thin entry points, a shared library by
  concern, one module per rule under `rules/<action>/` (write, delete, read, execute, ...).

## Session and scratch files

- **All session work, scratch included, lives under the session's own project folder, in its local
  `.claude/`, in a proper file tree** (user ruling, 2026-10-20). Default layout, gitignored:
  `<project>\.claude\scratch\<role or session>\<task or ticket>\`. Durable outputs go where they
  belong instead: measurements and baselines in `.claude\metrics\`, docs in `.claude\docs\`, plans in
  `.claude\design\plans\`. Nothing is dropped loose at the project root or in a parent folder.
- **A project's `.claude\` stays under 1 GB** (user rule, 2026-10-20: "if the agents local .claude
  directory gets bigger than 1gb, it needs to prune any temporary files and address the bloat").
  Over it, prune temporary files first (scratch, stale outputs), with literal delete paths, then report
  what else is large; anything that is not clearly temporary is the user's call.
- **No writes to the system temp folders** (user rule, 2026-10-18: "global block with a message stating to keep files
  local to the project inside the local .claude and subfolders"). Never write to `/tmp`, `%TEMP%` / `%TMP%` or
  `AppData\Local\Temp` (the harness scratchpad there included). Enforced by the global write guard (W5).
