# Global hook rules

What each hook in this folder checks, what it does when the check fires, and how to turn it on.
Every hook here is a plain Python script (standard library only) that Claude Code runs before a tool
call or at a session event. A hook never runs unless `~/.claude/settings.json` has an entry for it:
`python install.py --only hooks --hooks --apply` copies the code and
`python install.py --only hooks --wire-hooks --apply` adds those entries (see the README).

Contents: [Shared behaviour](#shared-behaviour) - [W2, W3, W5: the write guard](#w2-w3-w5-the-write-guard)
- [S1: the .claude size flag](#s1-the-claude-size-flag) - [M1: the Agent model reminder](#m1-the-agent-model-reminder) - [X3: no interpreter reading stdin](#x3-no-interpreter-reading-its-program-from-stdin) - [W7: file and folder names](#w7-file-and-folder-names)

## Shared behaviour

- **Fail open.** A hook that crashes, gets malformed input or cannot read a file lets the tool call
  through and prints nothing. A broken hook never blocks your work.
- **ASCII output** only, and nothing on stdout unless the hook has something to say.
- **Hit logs** are `*_hits.jsonl` files beside each script in `~/.claude/hooks/`, rotated at 5 MB. They
  are machine-local: git ignores them and `install.py` never copies them.
- **Self-tests:** every entry point takes `--self-test`, e.g.
  `python -I -S -B "<your home>/.claude/hooks/write_guard.py" --self-test`. Spell the home folder out:
  PowerShell does not expand `~` for Python.

## W2, W3, W5: the write guard

`write_guard.py` runs before Write, Edit, MultiEdit, NotebookEdit, Bash and PowerShell calls.

| Rule | Fires when | Why |
|---|---|---|
| W0 | a Bash/PowerShell command is too large to check inside the hook's 10 s timeout: over 131,072 characters, 1,000 top-level parts, 1,000 write/delete targets, 4,096 path separators across those targets, or 64 in any one target | past the timeout Claude Code lets the call through unchecked; none of 296,012 real commands measured on this machine comes near a cap |
| W2 | a delete names its target with a relative path, a variable, `~`, a command substitution, a pipe, or a wildcard anywhere but the last path segment | an agent deleting the wrong folder is the costliest mistake there is; a literal absolute path can be checked before it runs |
| W3 | a write lands outside the session's project folder (`CLAUDE_PROJECT_DIR`) and `~/.claude` | keeps each session inside its own project; everything else is read-only |
| W5 | a write lands in a system temp folder (`/tmp`, `%TEMP%`, `AppData\Local\Temp`) | keeps scratch work inside the project, where it can be found and cleaned up |
| W4 | (log-only, never denies) a hook or skill file is written without a review stamp | evidence for the review-phase rule |
| W7 | (log-only, never denies) a new file or folder name breaks the naming rule | evidence for the file and folder naming rule; see [W7](#w7-file-and-folder-names) |

**Log-only vs enforcing.** The shipped command runs `--log-only`: every hit is logged, nothing is
denied. To let rules deny, add `--enforce=W2,W3,W5` (or a subset) to that command in `settings.json`
and keep `--log-only` on the line; the `--enforce=` list then names the only rules that may deny.
Recommended: run log-only for about a week, read the hit log (`hooks/hits_summary.py` summarizes it),
then enforce. An unknown id in `--enforce=` is ignored and recorded, never a crash.
`CLAUDE_WRITE_GUARD=log` in the environment is the kill switch: nothing denies.

**W0 is different** (rules/execute/w0_too_large.py; user decision 2026-10-31: "Go with the size cap,
deny oversized commands"). It runs before every other rule and denies in every mode, including
`--log-only` and any `--enforce` list; it is not an `--enforce` id. It logs one `too-large` row (the
sizes, the caps and a 60-character head, never the full command). Under the kill switch it logs with
`denied:false` and does not deny: the switch means nothing denies, and whoever sets it accepts that an
oversized call may time the hook out unchecked. A denied call is fixed by splitting the command, or by
writing a script file with the Write tool inside the project's `.claude/` folder and running that.
Write/Edit/MultiEdit/NotebookEdit are not capped.

**Known gaps, low priority** (user decision, 2026-10-31: fix only if they show up in real use; none of
~296,000 real commands came near them, every slow case was built on purpose, and the guard is a
safety net against mistakes, not a defence against a hostile agent):
- `hooklib/paths.py` `resolve_real_prefix` resolves UNC and `//host` paths, which is a network
  round-trip: `touch //10.255.255.1/share/f` took about 21 s, past the 10 s hook timeout, so the call
  goes through unchecked. Fix if needed: never resolve UNC, `//host` or `\\?\UNC\` paths.
- W4 reads and hashes a copy's source file once per guarded destination (11.7 MB to 999
  destinations took 17 s). Fix if needed: hash each source once per call, with a byte budget.

## S1: the .claude size flag

`size_guard.py`. Rule (user, 2026-10-20): "if the agents local .claude directory gets bigger than
1gb, it needs to prune any temporary files and address the bloat", checked "whenever that directory
is greped or used". A hook can measure the folder and tell the agent, but it can't judge what is
temporary, so S1 only ever flags: it adds `additionalContext` to the conversation, never denies and
never deletes (user ruling, 2026-10-20: "a flag to the agent/PM to prune it. Not necessarily
blocking"). "Local" means the project's `.claude\` (`CLAUDE_PROJECT_DIR`, else `cwd`), never `~/.claude`.

- **Events:** SessionStart and UserPromptSubmit reach the main session. PreToolUse on
  Read/Grep/Glob/Write/Edit/MultiEdit/NotebookEdit/Bash/PowerShell fires only when the call's target is
  inside `<project>\.claude`, and reaches whoever made the call (main session or subagent). Subagents
  are told to report it and prune only if their brief allows writes.
- **The message:** the size, the 5 biggest subfolders, "prune `.claude\scratch\` first, by literal
  paths" (left out when scratch is empty), and "leave worktrees, metrics, docs, design, backups and
  tickets to the user".
- **Cache:** one state file beside the script (`size_guard_state.jsonl`, never inside a project). The
  folder is re-measured at most every 10 min, or every 2 min while it is over the limit or after a walk
  the 3 s cap cut short. `--limit-mb=`, `--ttl-minutes=`, `--over-ttl-minutes=` and `--budget-seconds=`
  override these.
- **How often:** per project and recipient, at most once per 10-min window while pruning scratch
  alone could bring the folder under the limit; once per session when even deleting all of scratch
  could not (user ruling, 2026-10-20). A SessionStart after `/clear` or a compaction flags the main
  session again, since the earlier flag has left its context.
- **On by default** (user ruling, 2026-10-20: no log-only week). `--log-only` on the command, or
  `CLAUDE_SIZE_GUARD=log`, records rows without showing the flag. Rows go to `size_guard_hits.jsonl`.
- **Measured 2026-10-20:** a full walk takes 30-70 ms warm, up to 660 ms cold (6,388 files). The hook
  costs about 70-95 ms per matched call when its pre-filter exits early, 120-190 ms when it measures.

## M1: the Agent model reminder

`model_guard.py`, PreToolUse on the `Agent` tool. Rule (user, 2026-10-21): pick the cheapest model
that does the job well and state the model and why in the brief (the tiers are in the global
`CLAUDE.md`). Whether a tier FITS a task is a judgment no hook can make, but whether the dispatcher
picked one at all is visible. M1 fires when nothing picks a model:
- no `model` on the Agent call,
- no agent definition (project `.claude/agents`, a parent of the cwd, or `~/.claude/agents`) with a
  `model:` other than `inherit`,
- and no `CLAUDE_CODE_SUBAGENT_MODEL`.

The subagent then runs on the main session's model (Claude Code's documented order: the call's
`model`, the definition's frontmatter, that variable, the main model). A "Model: Haiku" line in the
brief does not change that, so M1 flags it. M1 stays silent for an agent type it cannot verify (unknown
or from a plugin) and for `claude-code-guide` and `statusline-setup`, which carry their own models. A
project or user definition that reuses a built-in name (say `Explore` with `model: haiku`) overrides
the built-in and is honoured; a `.md` file with no `---` header is not a definition.

- **Advisory only:** no `permissionDecision`, never denies. The default is log-only (rows to
  `model_guard_hits.jsonl`, nothing shown). Add `--advise` to the command to show the reminder as
  `additionalContext`. `CLAUDE_MODEL_GUARD=log` forces log-only.
- **Not built:** a tier-mismatch check ("haiku on a prompt about hooks/delete/security", "opus on a
  lookup"). Replayed over 1,247 real Agent calls, 4 of the 18 Haiku calls matched and all 4 were false
  positives.
- **Measured replay (2026-10-21):** 48 fires in 1,247 Agent calls (3.8%), 5 of 5 hand-checked hits
  real: general-purpose, Explore and Plan calls with no model, which inherit the main session's.

## X3: no interpreter reading its program from stdin

`stdin_guard.py`, PreToolUse on Bash and PowerShell. Rule (user, 2026-10-31: "100% CPU util is not
acceptable"; global `CLAUDE.md` Working habits: no heredocs; `python -` has the same stdin hang). An interpreter told to
read its program from stdin can hang there and spin a core for hours (2026-10-31: three leftover
`python -` processes did).

**Enforced** (decision 2026-11-01, asked whether to enforce, the user said "Your
choice"; the session chose enforce because agents kept running `python -`). The shipped
command carries `--enforce=X3`. X3 has two tiers:

| Tier | Fires on | Effect |
|---|---|---|
| deny (`dash`) | `-` as the program of python / python3 / python3.13 / py / pythonw / pypy: `python -`, `python -X utf8 -`, `py -3 -`, `cat x.py \| python -` | denied |
| deny (`heredoc`) | an interpreter (python family, node, ruby, perl, bash / sh / zsh / dash, powershell / pwsh) that names no other program and reads a heredoc, a bash `<<<` here-string, or a pipe from a heredoc or PowerShell here-string: `python <<EOF`, `bash <<'EOF'`, `cat <<EOF \| python`, `@'...'@ \| python` | denied |
| log (`no-source`) | the rest: a bare `python` or `node`, `echo x \| python`, `curl x \| bash`, `bash -s`, `node -` | logged, let through |

Passes: `python -c`, `python -m`, `python -X utf8 script.py`, `python file.py`, a `-` that belongs to the
script or module (`python script.py -`, `python -m json.tool -`), a heredoc feeding a script's stdin
(`python script.py <<EOF`), version and help calls. The deny message: "python - / heredoc to an
interpreter hangs on stdin; write the script with the Write tool into the project's .claude\scratch and
run it with a timeout".

- **Switches:** without `--enforce=X3` on the command nothing denies; `CLAUDE_STDIN_GUARD=log` in the
  environment forces log-only (the kill switch). A parse or rule error fails open.
- **Hit log:** `<project>/.claude/hooks/log/stdin_guard_hits.jsonl`, written only when that project's
  `.claude` exists; nothing is logged without `CLAUDE_PROJECT_DIR`. Rows carry `kind` and `denied`.
- **Measured before enforcing (2026-11-01, one project):** live log 31 hits (2026-11-01 04:14-12:00 UTC),
  29 true positives, 2 false positives (`timeout 600 python $(basename $f)`, which parses as a bare
  `python`). Replay over all 7,446 shell calls in the project's transcripts (2026-10-31 and 11-01, 174
  files): 81 hits; the enforced rule denies 79, every one a hand-checked `python ... -` (58 with a heredoc,
  21 without); the 2 false positives fall in the log tier and are not denied. False denies: 0.
- **Cost:** about 70-100 ms per shell call, almost all interpreter start-up (2026-10-31); the check
  itself 0.62 ms per call before the change, 0.65 ms after (in-process, 500 calls, 2026-11-01).
- **Known misses:** `powershell -Command -` (the parser expands `-Command` and drops the `-`), and a
  here-string held in a variable and piped later (`$s | python`, log tier only), and wrappers the
  parser does not look through (`xargs python -`, `uv run python -`). **Known false positives**, log
  tier only: flag-only node calls such as `node --test`, `py -0p` / `py --list`, and `python $(cat f)`.

## W7: file and folder names

`write_guard.py` -> `rules/write/w7_naming.py`, under the existing write_guard wiring (no new
settings entry). **ALWAYS_LOG_ONLY**, like W4: never denies, never prints, and is not an `--enforce`
id (listing it is recorded as a config issue and ignored).

**Rule** (user, 2026-11-23; `~/.claude/CLAUDE.md`, "Session and scratch files", "File and folder
names"): code files and their folders follow their language's convention (C++ and Python
`snake_case`); Markdown docs and their sidecar folders are lowercase-hyphenated (the docs standard);
names a tool requires stay as they are; third-party trees are exempt; new files follow this now, and
renaming existing files is a planned, reviewed job. It replaced a project-only snake_case rule the
same day. The user also ruled that day that the tools-location rule (an earlier W6 draft) is NOT a
hook: "it should be up to the project to define it - but global should have a recommendation".

**Class: (c) partly checkable.** A name is checkable when a call creates it. Not checkable: the
planned rename of existing names, names a tool generates on its own (build output, backups, a
script writing files), and a folder's language at the moment it is created.

| Kind | Fires when a call CREATES, inside the project root (`CLAUDE_PROJECT_DIR`; no cwd fallback) |
|---|---|
| `code` | a file whose extension maps to a code convention and whose stem breaks it: `HMAC.cpp`, `NewDialog.h`, `my-tool.py` |
| `doc` | a Markdown file whose stem is not lowercase-hyphenated: `My_Doc.md`, `my_doc.md` |
| `doc-dir` | a folder beside a same-named `.md` (a docs sidecar) that is not lowercase-hyphenated |
| `dir` | a new folder whose PARENT already holds code of exactly one listed convention, with upper case, a space or a dot in its name: `Sources/NewModule`, `Sources/v1.2`. A folder's language is unknown at creation, so the parent's code decides; a parent with no code, mixed conventions, or code of an unlisted language (C#, Java, JS...: PascalCase folders are right there) is not judged, and hyphen-vs-underscore is never judged for folders |

**One extension table** (`CONVENTIONS` in the module): `snake_case` for `.c .cc .cpp .cxx .c++ .h
.hh .hpp .hxx .h++ .inl .ipp .tpp .py .pyw .pyi`; `kebab-case` for `.md .markdown`. Add a language by
adding its extensions (and a pattern for a new convention). Every other type (`.txt .json .png .cs
.js ...`) has no convention and is never judged; an "upper-case letters" warning kind for them was
considered and left out, because a log kind with no rule behind it only adds noise.

**Never judged:** an existing target (overwrite, `mkdir -p` of an existing folder), Edit, a MOVE that
keeps its basename (`mv x/Foo.cpp src/`, `git mv`, `Move-Item`: same name, new path is not a new
name), tool-required names (`CMakeLists.txt`, `Makefile`, `GNUmakefile`, `Dockerfile`,
`__init__.py`, `__main__.py`, `README.md`, `CLAUDE.md`, `CHANGELOG.md`, `LICENSE.md`, `LICENCE.md`,
`CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `AUTHORS.md`, `NOTICE.md`, `AGENTS.md`,
`SKILL.md`, `MEMORY.md`, `RULES.md`; case-insensitive), dotfiles, backups (`.bak`, `.prev.`,
`.orig.` or `.old.` in the name), `<root>/.claude/scratch/**`, anything outside the root, UNC and
`//host` targets (never stat'ed), and targets W3's parser cannot resolve statically (`$VAR`,
`$(...)`, backticks, `%VAR%`, globs).

**Case-only renames ARE judged** (2026-11-23 review fix): `mv a.cpp A.cpp`, `git mv`, `Move-Item`,
`Rename-Item`, a folder too. The target "exists" only as the source itself, so the new name is
compared with the source's on-disk name, case-sensitively, not by whether the path exists. A
copy (`cp a.cpp A.cpp`) is not judged: on Windows it overwrites `a.cpp` and keeps that name.

**Third-party trees:** any path segment named `vendor`, `third_party`, `third-party`, `external`,
`extern`, `node_modules`, `_deps`, `Dependencies` or `.git` (case-insensitive, any depth), plus
project-relative prefixes in an optional `<project>/.claude/naming-exempt.txt` (one per line, `#`
comments, `/` or `\`). Only the file's first 64 KiB and 1,000 lines are read, and a line cut by the
byte cap is dropped; the rest is ignored (any session can write the file, and an unbounded one was
a way to time the hook out). **Tradeoff of exempting `Dependencies`:** the name is common for vendored
code, but projects also keep first-party code under it. In the project this rule was drafted in,
`Sources/Dependencies/` holds `SFML`, `SQLite` and `Shared/ASIO` next to first-party
`Shared/Util`, `Client` and `Server`, and the exemption hides real hits there (two new `HMAC.cpp`
/ `HMAC.h` files were measured as hidden). It is a false negative, not a false positive, and W7 is
log-only, so the generic list keeps it. A project that wants its first-party `Dependencies/` code
judged can name the real vendored trees in `naming-exempt.txt` instead, once W7 supports an
override list (not built).

**Targets:** W3's extractor (`find_write_targets_cwd`), post-processed: delete verbs dropped;
`cp`/`mv`/`install`/`ln`/`copy`/`move`/`Copy-Item`/`Move-Item` into a folder become
`DEST/basename(SRC)`; `Rename-Item`/`ren`/`rni` put `-NewName` beside the source; `New-Item -Name`
joins `-Path`; `New-Item -ItemType Directory`, `mkdir` and `md` are folders. W7 adds `git mv` and
`[IO.Directory]::CreateDirectory(...)`, which W3 does not extract. Never judged: `git clone` and
`git worktree add` destinations (repository checkouts, not language folders), URL-like targets, and
PowerShell targets computed by a subexpression (`(Join-Path $x 'Foo')`). Backups keep their
original's name: `.bak`, `.prev.`, `.orig.` and `.old.` in a name exempt it.
Names are cleaned the way Windows creates them: an NTFS stream suffix (`x.cpp:Zone.Identifier`) is
dropped and trailing dots and spaces are stripped. One row per target per call (case-insensitive).

**Rows:** no `msg` (the message is here), one excerpt per segment, at most 50 rows per call plus
one `truncated` row, plus one `budget-stop` row when the cost cap below stopped W7 early.

**Fail open, per module** (2026-11-23 review fix). The guarantee: a fault in W7 can cost W7 its own
rows for that call, never another rule's deny. Three layers:
- `write_guard.py` imports W7 inside a `try ... except BaseException` (a `SystemExit` at import is
  caught too) and drops W7 when its `RULE_ID` is not a non-empty string or it is not
  `ALWAYS_LOG_ONLY`; W2/W3/W4/W5 then load and run as before.
- `run_detailed` and `process` give every rule module its own try (`BaseException`) around its
  evaluation, its `log_rows` and its `decide`. A module that raises contributes no hits, rows or
  verdict for that call; every other module still logs and denies. A `decide` returning anything
  but a deny-shaped dict counts as no verdict, and an `ALWAYS_LOG_ONLY` module's verdict (W4, W7)
  is dropped whatever it returns.
- W7's own `evaluate_tool`, `evaluate_shell` and `log_rows` catch their own exceptions and return
  `[]`; `decide` always returns None.

- A module raising `SystemExit` is now absorbed by its own try; live's `main()` catches only
  `Exception`, so the same `SystemExit` there exited 2 (reviewer-measured, 2026-11-23).
- An `ALWAYS_LOG_ONLY` module never supersedes: `drop_superseded` skips it, so a log-only module
  declaring `SUPERSEDES` cannot remove another rule's hits and turn its deny into an allow.

Not covered: a W7 that hangs. That is what the cost cap is for; past the 10 s hook timeout Claude
Code lets the call through.

**Cost** (2026-11-23 review fix). W7 re-runs W3's target extractor (`find_write_targets_cwd`)
itself, walks the segments once more for `git mv` / `CreateDirectory`, and per target adds a
`resolve()` (a `realpath`), one `exists` check, and, for a new folder, a listing of its parent.
The caps:
- at most 200 targets judged, and none after 0.5 s of wall clock since W7 started, whichever comes
  first; then one `budget-stop` row;
- each parent folder listed once per call, and only its first 500 entries (`os.scandir`, stopped
  there);
- the exempt file read only up to its caps (above).

Measured, 2026-11-23: subprocess through the entry point, live command line
`--log-only --enforce=W2,W3,W5`, min of 3 runs; "live" is the installed write_guard without W7.

| Case | Staged before the fix | Staged now | Live |
|---|---|---|---|
| 999 `mkdir`s in a 20,000-entry folder + 1 W3 violation | 11.78 s | 4.34 s | 4.28 s |
| same, 999 `mkdir` segments + 1 W3 violation | 11.64 s | 4.42 s | 4.31 s |
| 10 MB `naming-exempt.txt`, 999-file `touch` + 1 W3 violation | 14.73 s | 4.36 s | 4.38 s |
| 60 MB `naming-exempt.txt`, 999-file `touch` + 1 W3 violation | 63.11 s | 4.36 s | 4.47 s |
| 60 MB `naming-exempt.txt`, one Write | 0.58 s | 0.25 s | 0.24 s |
| `git mv` x999 segments + 1 W3 violation | - | 0.41 s | 0.31 s |
| `[IO.Directory]::CreateDirectory` x999 (PowerShell) + 1 W3 violation | - | 0.37 s | 0.30 s |
| `cp` of 998 files into a folder + 1 W3 violation | - | 0.32 s | 0.26 s |

The "before" column is 1 run; "-" was not measured before. The W3 violation denied in every row.
Staged minus live is -0.11 s to +0.11 s on the first five rows, which is run-to-run noise, and
+0.06 to +0.10 s on the last three. Those three exercise W7's second pass (`git mv`,
`CreateDirectory`) and its copy-into-folder path under the cap. The 62-deep `New-Item` shape is
denied by W0 before any rule runs: 0.22 s staged, 0.20 s live.

In-process, W7's own share of the first four commands was 0.039-0.050 s, down from 6.47 s for the
first. That was profiled on the code before its last refactor (one cache removed, the exempt read
moved into a helper); it was not re-profiled after.

What remains is the existing rules. W5 alone took 2.9-6.5 s in-process on these 999-target commands
across profile runs. That it costs the same in live is derived, not profiled: its module bytes are
identical. Across three runs of the same commands, live took 3.65-5.23 s and staged 3.74-5.52 s. On
the busiest run, staged was 5.01 s and 5.52 s against live's 4.83 s and 5.23 s. The margin under
the 10 s timeout is set by W5's pre-existing cost, not W7's.

**Message:** "W7: a new file or folder name breaks the naming rule: code follows its language's
convention (C++ and Python snake_case), Markdown docs and their sidecar folders are
lowercase-hyphenated; tool-required names and third-party trees are exempt (user rule, 2026-11-23).
Log-only."

**Known misses:** only the last segment is judged (`mkdir -p a/NewDir/b`, or a Write into a new
folder, creates that folder unflagged); targets past the cost cap (200 per call); a folder whose
parent has more than 500 entries is judged by its first 500 only; nested shells (`bash -c`, `powershell -Command`, `cmd /c`),
`curl -o`, and files a script writes itself; what W3's extractor does not see, W7 does not either.

**Measurements** (2026-11-23):

- **Self-test:** the whole `write_guard.py --self-test` suite, 854 cases, 0 failures (measured
  2026-11-23 after the review fixes; 828 before them, 718 before W7). The review fixes, each
  reverted in a throwaway copy, fail it: no case-only rename skip -> 6 failures, no target cap -> 3,
  no per-folder convention cache -> 1, no scan stop -> 1, an unbounded exempt read -> 1, no exempt
  line cap -> 1, no budget-stop row -> 1, the guard keeping an ALWAYS_LOG_ONLY verdict -> 1, the
  guard without per-module isolation of rows -> 4, of evaluation -> 4, of decide -> 4. Subprocess mutants of W7 (`log_rows` or `decide`
  raising or calling `SystemExit`, in the body or replacing the public function; `decide` returning
  a deny or garbage; `evaluate_shell` raising; a raise or `SystemExit` at import; `RULE_ID = 7`;
  `ALWAYS_LOG_ONLY = False`): the W2 deny on `rm -rf build` survives in all 13, with no W7 reason.
  With the guard's isolation removed, the same faults lose it (controls).
  Earlier mutations, before the review fixes: Mutations, each measured: W7 never fires -> 31 failures; every name accepted -> 31;
  no same-basename skip -> 3; every folder judged as C++ -> 4; no PowerShell subexpression guard
  -> 1; no third-party segments -> 5; `.json`/`.txt` given a convention -> 2; W7's never-raise
  wrapper removed -> 2 entry-point failures (W3 loses its deny). The repository-checkout guard is
  defence in depth: without it, checkouts fall through as extension-less files and still never fire.
- **Entry point (measured):** a W7 hit through `main()` under `--log-only`,
  `--log-only --enforce=W2,W3,W5,W7` and `--enforce=W7` prints nothing and logs `denied:false`;
  `--enforce=W7` is recorded as `not-enforceable:W7`. With W7's import forced to fail, write_guard
  loads W3/W4/W5 and W3 still denies.
- **Cross-project replay** (`replay_w7.py`, stage only, not installed; glob
  `~/.claude/projects/*/*.jsonl`, top-level session transcripts, no subagents; seeded order): all
  1,571 of 1,571 files, 4,947 MB, 260 projects, 116,130 events (11,997 Write, 89,859 Bash, 14,274
  PowerShell) in 89 s; 0 errors; W7 costs 0.40 ms mean, 33.8 ms max per event in-process.
  **Would fire live: 450** (0.39% of events; derived: 451 measured, minus 1 removed by the backup
  fix): about 423 `doc`, 27 `code`, 0 `dir`, in 24 of 260 projects. Demoted, not counted: 56 `>>`
  appends, 55 targets an earlier call had touched, 23 Writes that updated an existing file.
- **Doc hits by shape** (424 before the backup fix, measured): 226 underscore names
  (`cache_ledger.md`), 109 ID-prefixed records (`D-231-an-agent-can-be-duplicated.md`), 81 all-caps
  names (`REPORT.md`, `BUILD_PROMPT.md`, `COMMIT_STANDARDS.md`), 8 other (version dots such as
  `ts-1.2.0-body.md`, variants such as `SKILL.reviewed.md`).
- **Hand-checked sample** (seeded, 20 of the 451; final rule): 18 match the rule as written, 2 do
  not. By kind: 18 doc, 2 code. Spread over 9 projects. The 2 false: `RESUME.prev.md`, a backup copy (fixed: `.prev`/`.orig`/`.old`
  now count as backups), and `ItemMarks.h` in a forked third-party tree (`wowmodelviewer-develop/`,
  14 firing hits in all), which only a project `naming-exempt.txt` line can exempt. Of the 18, 9 are
  shapes the user may want to allow: 5 ID-prefixed decision records and 4 all-caps meta docs; the
  rule as written flags both.
- **All 27 `code` hits hand-checked:** 11 match the rule (new PascalCase C++ in the user's own
  projects, e.g. `GameConfigJson.h`, `MySqlLogSink.cpp`, the PAKLibC tests, and `open_1.0.5.py`);
  16 are the forked third-party tree above (14 firing after demotion).
- **Earlier rounds, fixed before the numbers above:** the first replay crashed on 1,455 of 116,127
  events (`shell_tokens` returns None on unbalanced quotes) - live, each crash would have dropped the
  enforced W2/W3/W5 verdict for that call, because `run_detailed` did not wrap each module then (it
  does now, see Fail open); W7 now never raises, and a regression test proves W3 keeps its deny. Its seeded sample had 7 false
  positives of 20: `git worktree add` / `git clone` destinations (3; one where W3 reads
  `--branch prod`'s value as the clone URL), a C# project's PascalCase folder, a backup folder, a
  `(Join-Path $fx 'MAPDATA')` target taken literally, and the forked tree. Fixed: repository
  checkouts and URL-like targets are never judged, computed PowerShell paths are skipped, and a
  folder is judged only by the code convention already in its parent and only for upper case,
  spaces or dots.
- **Cross-project replay numbers** (the 450 hits, 0.40 ms mean) were measured BEFORE the review
  fixes (cost cap, case-only renames) and have not been re-run; how often real traffic hits the
  200-target cap is unmeasured.
- **Ordinary calls** (second review, 2026-11-23, reviewer-measured): over 318 subprocess events
  through the entry point, a mean of 292 ms live against 289 ms staged, so W7 adds no measurable
  cost. **Not measured:** subagent transcripts were not replayed.
