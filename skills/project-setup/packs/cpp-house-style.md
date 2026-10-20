# Language pack: C++ house style

Offered when Step 1 detects C++ (`.h .hpp .cpp .cc .cxx .inl`). The user's C++ standards as Helbreath
practises them, minus everything game- or engine-specific. User ruling, 2026-10-20: a language pack,
never global, never applied to non-C++ projects.

## 1. Rules, in four groups

Ask one multiSelect question with the four groups as options (all recommended). Adopted groups go into
the project's CLAUDE.md "code" section, one line per rule. The hook ids are the Helbreath modules that
check each group (sect. 2); rules with no id are written-only.

| Group (option) | Rules | Hook modules (`rules/write/cpp/`) |
|---|---|---|
| **Naming and formatting** | ASCII only in source (code, comments, literals). snake_case for every identifier (exceptions: template params, `SCREAMING_CASE` macros); members `m_`; files lowercase snake_case. Tabs, Allman braces, the true path on its own line (never `if (x) return;`). Indentation means scope (namespace bodies indented). Comments are short. | `c01_scope_indent` (C1), `c02_short_comments` (C2), `c21_true_path_line` (C21) |
| **Values and initialization** | No magic numbers or values: every literal is a named `constexpr` stating WHY, never `#define` constants, placed where they belong. Every variable initialized where declared. | `c03_constant_placement` (C3), `c11_initialized` (C11), `c12_magic_numbers` (C12, noisy) |
| **Structure** | `#pragma once`; includes at the top only; class state in the header; no `using namespace` in headers; anonymous namespaces used very sparingly; no pass-through functions, duplicated lambdas or large switch cases. *(MSBuild only)* every sub-family gets its own Visual Studio sub-filter. | `c04_types_in_headers` (C4), `c05_pragma_and_includes` (C5), `c07_using_namespace` (C7), `c08_anonymous_namespace` (C8, advisory), `c10_switch` (C10) |
| **Types, ownership, errors** | Modern types only: `std::string`/`string_view`, `std::array`/`vector`, fixed-width integers; no C strings, raw arrays or anonymous C-style structs. Ownership is `std::unique_ptr` + `std::make_unique` only, never `new`/`delete`/`shared_ptr`. Exceptions for fatal/corrupt state, `bool` for recoverable; no `goto`, no integer error codes, no exceptions in hot paths. One logging facility for all output. | `c15_c_strings` (C15), `c16_anonymous_structs` (C16), `c17_raw_arrays` (C17), `c18_fixed_width_integers` (C18) |

Mechanism and examples go in `.claude/docs/coding_standards.md` (index + sidecars). Canonical wording
to adapt: `C:\Users\ShadowEvil\source\Helbreath-SDL\Helbreath\.claude\docs\coding_standards.md` and its
`coding_standards/` sidecars (code-structure, formatting-and-naming, project-organization). Strip
Helbreath-only material (UI kit, lang.json, `.hba`, wire data, `DEF_CLAUDE_TESTING`, window resolution).
Each rule's exact check is described in Helbreath's `.claude/hooks/RULES.md` under the section number
matching its id (C1 -> sect. 1, C21 -> sect. 21).

## 2. Hooks (optional; LOG-ONLY; every step is under the review phase)

Ask separately whether to port the hooks. **Everything below is a hook write: stage it, have a separate
read-only agent review the staged files AND the settings.json diff together, and install only after the
review passes and the user approves.** W2/W3 (deletes, write scope) are enforced by the global write
guard, so they are not ported.

Canonical source: `C:\Users\ShadowEvil\source\Helbreath-SDL\Helbreath\.claude\hooks\`. If its layout
differs from what is described here, stop and report; never reconstruct hooks from memory.

1. **Stage** under `.claude/scratch/pm/hooks-cpp/` (never straight into `.claude/hooks/`):
   - `std_guard.py`, with its rule imports cut to `rules.write.cpp` only (it also imports Helbreath-only
     a1, f1, lk1, pt1, wk1, p1, p2, s1 and w3; none are copied).
   - `hooklib/`: `__init__.py`, `output.py`, `policy.py`, `cpp_source.py`, `runner.py`, `fixtures.py`.
     The C rules import only `cpp_source`, `fixtures`, `output` and `policy`; `paths.py`, `waiver.py`,
     `shell.py`, `nested.py`, `lang_keys.py` and `py_source.py` are not needed.
   - `hooklib/selftest.py` as a **rewrite**, not a copy: Helbreath's imports every Helbreath rule and
     `waiver`, runs a W3 deny test and asserts `ENFORCE <= {W2, W3}`. The new one collects the kept C
     modules plus `output.run_self_test` only, and asserts `ENFORCE` is empty.
   - `rules/__init__.py`, `rules/write/__init__.py`, and `rules/write/cpp/` with only the modules of the
     adopted groups, its `__init__.py` import list trimmed to match.
2. **Adapt:**
   - The C++ scope (Helbreath: `src/{client,server,shared,loginserver,tools,schema}/`) becomes this
     project's source folders; the exclusions (`external/`, `reference/`, `working/`, `build/`) become
     its vendored, scratch and build folders.
   - **`rules/write/cpp/__init__.py` `run_self_test` hard-codes Helbreath paths** (`C:/r/Helbreath/src/schema/...`,
     `external/`, `reference/`); rewrite those cases for the new scope.
   - The same `run_self_test` depends on specific ids: the breadcrumb case checks `C18s` and the
     deny-path case enforces `C18s` and checks `C17`. If the Types group is not adopted, rewrite those
     cases to use an id from a kept module.
   - Every `tempfile.TemporaryDirectory()` gets `dir=` pointing inside `.claude/scratch/` (the
     system temp folder is off limits globally).
   - `policy.ENFORCE` = the empty set (log-only). The hit log lands under the project's `.claude/`,
     next to the hook (`.claude/hooks/std_guard_hits.jsonl`), and `.gitignore` covers it. No Helbreath
     name, path or rule id remains in any message.
3. **Prove:** `python -I -S -B <staged>/std_guard.py --self-test` passes with 0 failures (`-B` keeps
   `__pycache__` out of the staged tree). If the project has past transcripts, replay them and report
   hits plus a false-positive sample per rule.
4. **Review**, with the staged settings.json diff wiring
   `python -I -S "$CLAUDE_PROJECT_DIR/.claude/hooks/std_guard.py"` on `Write|Edit|MultiEdit|NotebookEdit`
   (10 s timeout) included in what is reviewed. **Then install** the files and the wiring together.
5. **Enforcement is the user's call**, later, once a rule has run log-only on real traffic with only
   true positives.
