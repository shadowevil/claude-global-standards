# documentation standards (read before adding or restructuring a doc)

*Machine-wide standard, installed to `~/.claude/docs/` from claude-global-standards. Generalized
2026-10-20 from the Helbreath project's standard, where it was first written (2026-09-03). A project
keeps only its own additions (examples, project-specific folders) in a local doc that points here.*

Why: a monolith makes every narrow question pay for the whole file. Measured 2026-10-20 over 1,482
Helbreath subagents: doc reads were 14% of all tool output pulled into context, mostly from a few
single-file docs.

## 1. Size only prompts a review; it never decides

A content-bearing doc over ~10 KB gets **assessed**. Ask, and write the verdict as one italic line under
the doc's title so nobody re-litigates it:

1. What does a reader come here for - one fact, or the whole picture?
2. Do the sections stand alone, or presuppose each other?
3. Is there shared context every section needs? (It belongs in the index; a fat index is fine.)
4. From the index alone, would a reader know which part they want?
5. How often is it consulted? Structure earns its keep on files read every session.

Check the verdict against usage when you can: many searches into one file mean readers come for one
section, whatever the doc claims about being read whole (the `adopt-project-standards` skill's
`transcript_metrics.py` measures this).

**Cohesion beats every size target.** Worst failures first: fragmenting a cohesive topic; an index too
thin to decide from; a section so large reading it is most of the old cost. A modest saving from a clean
split beats a large one from a fragmented split.

## 2. The pattern: a lean index file plus a sidecar folder

`topic.md` becomes the index; `topic/` holds the sections (lowercase, hyphenated). The index is lean:
one line per entry - the path, what it covers, WHEN to read it. A lookup is one small index read plus
one targeted read (or a grep) of the path it names, never a whole-file read:

```markdown
# environment (index)
- `environment/shells.md` - Bash vs PowerShell quirks; read before running any command.
- `environment/build.md` - build and deploy commands; read before building.
- `environment/databases.md` - safe read-only database access; read before any query.
```

The metric is **index + ONE target versus the old monolith** per lookup. If an index outgrows
usefulness, add a level of the same pattern; never shorten entries to hit a number.

## 3. Index entries

- **Decidable without opening the target:** topic, status with date where it applies, one line of scope.
  If an entry needs 500 bytes to be decidable, it gets 500 bytes.
- **Every pointer says WHEN to read the target**, not merely that it exists.
- **An index points; it never restates.** Every fact lives in exactly one place.

## 4. Binding rules are never demoted to a pointer

Anything whose violation causes damage stays stated in full in a CLAUDE.md (the project's, or the
global one), the only files loaded automatically. Test: if a session never opened the linked file,
could it do damage? If yes, the rule stays in CLAUDE.md.

## 5. Splitting obliges you to repair every reference

Grep for every moved path AND moved heading across the source, `.claude/` and scripts. Fix "above",
"below", "this file". **Preserve section numbering** so citations like "sect. 4" still resolve; where a
file's path is cited, keep it as the index.

## 6. When NOT to split

Sections that constantly cross-reference; one continuous argument; sidecars that would be a few hundred
bytes each. Give such files a Contents block instead.

## 7. Plans live in folders by how far the work got

`.claude/design/plans/`: the root holds designed-but-unstarted plans and anything in flight; `partial/`
partly built; `completed/` shipped. **Sort by what the code shows, never by the status line in the
file** - those go stale the moment a change lands. Move a plan when its state changes.

## 8. Citing code from a doc that cannot be edited later

Cite by function or symbol name, or as `file:line (as of <date>)`. A bare line number in a doc that will
not be maintained points at the wrong code within weeks.
