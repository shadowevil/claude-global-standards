# bundled

Copies that `project-setup` installs into a project, so the skill works on any machine.

| File | Installed to | Notes |
|---|---|---|
| `backup.py` | `<project>\src\tools\scripts\backup.py` (or any path exactly three folders deep) | Pre-change backup sets. Bundled 2026-10-21 from the author's origin project at commit `9ae37130`, the version hardened and audited on 2026-10-20; only comments differ from it. |
| `changelog_format.md` | `<project>\.claude\docs\changelog_format.md` | The two-changelog format, with the origin's areas replaced by slots. |
| `subagent_baseline.py` | `<project>\.claude\metrics\subagent_baseline.py` | Orientation and friction baseline over the project's subagent transcripts. Derives the project's transcript folder from where it sits; counts the global hook ids (W, S, M). |

A newer version from the origin replaces a bundled file only after the same read-only review as any
skill change; `backup.py` deletes files, so its reviewer is Opus.
