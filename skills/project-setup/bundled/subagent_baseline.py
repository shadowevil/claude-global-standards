"""subagent_baseline.py - orientation and friction baseline over this project's subagent transcripts.

User brief 2026-10-20 (warm-start dispatch, phases 1 and 5): re-run unchanged to compare before / after.

    python .claude/metrics/subagent_baseline.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--json out.json]

Source: ~/.claude/projects/<project-slug>/*/subagents/agent-*.jsonl (+ .meta.json for agentType).
Definitions:
  turn     one distinct assistant message.id
  edit     a tool_use named Edit, MultiEdit, Write or NotebookEdit
  segment  a dispatch or a resume: the run of rows after each user row that carries a prompt (text, not a
           tool_result); a SendMessage resume therefore starts a new segment
  intent   read-only for agent types review / Explore / Plan; otherwise edit-capable (the transcripts do not
           record the brief's INTENT line before 2026-10-20)
  mandated a Read of .claude/docs/** or CLAUDE.md (reading the project rules require)
  denial   a failed tool_result naming a hook rule id (W2:, W3:, ...), grouped by rule
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Claude Code's folder name for this project under ~/.claude/projects: the absolute project path with
# every character that is not a letter or digit replaced by "-". Empty = derive it from where this
# script sits (<project>/.claude/metrics/subagent_baseline.py).
PROJECT_SLUG = ""
EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
READ_ONLY_TYPES = {"review", "Explore", "Plan"}
MANDATED = re.compile(r"[\\/]\.claude[\\/]docs[\\/]|CLAUDE\.md$", re.I)
# Hook rule ids only (a compiler's D8040: is not one). The global write guard's denials are W ids;
# add your project's own denying hook ids here.
RULE_ID = re.compile(r"\b((?:W\d)[a-z]?):")
HEREDOC = re.compile(r"<<-?\s*'?\"?[A-Z_]+")
P75 = 0.75


def parse_day(text):
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc) if text else None


def stamp(row):
    raw = row.get("timestamp")
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def is_prompt(row):
    if row.get("type") != "user":
        return False
    content = row.get("message", {}).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and any(block.get("type") == "text" for block in content)


def tool_uses(row):
    content = row.get("message", {}).get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if block.get("type") == "tool_use"]


def tool_errors(row):
    content = row.get("message", {}).get("content")
    if not isinstance(content, list):
        return []
    errors = []
    for block in content:
        if block.get("type") == "tool_result" and block.get("is_error"):
            body = block.get("content")
            if isinstance(body, list):
                body = " ".join(part.get("text", "") for part in body if isinstance(part, dict))
            errors.append(str(body or ""))
    return errors


def quantiles(values):
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {"n": len(ordered), "mean": round(statistics.fmean(ordered), 1), "median": statistics.median(ordered),
            "p75": ordered[min(len(ordered) - 1, int(len(ordered) * P75))]}


def segments_of(rows):
    current = []
    for row in rows:
        if is_prompt(row) and current:
            yield current
            current = []
        current.append(row)
    if current:
        yield current


def measure(segment):
    turns = []
    first_edit_turn = None
    mandated = 0
    for row in segment:
        if row.get("type") != "assistant":
            continue
        message_id = row.get("message", {}).get("id")
        if message_id and message_id not in turns:
            turns.append(message_id)
        for use in tool_uses(row):
            if use.get("name") in EDIT_TOOLS and first_edit_turn is None:
                first_edit_turn = len(turns) - 1
            if use.get("name") == "Read" and MANDATED.search(str(use.get("input", {}).get("file_path", ""))):
                mandated += 1
    return len(turns), first_edit_turn, mandated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--json")
    args = parser.parse_args()
    since, until = parse_day(args.since), parse_day(args.until)
    slug = PROJECT_SLUG or re.sub(r"[^A-Za-z0-9]", "-", str(Path(__file__).resolve().parents[2]))
    root = Path.home() / ".claude" / "projects" / slug
    if not root.is_dir():
        print("note: no transcript folder %s (set PROJECT_SLUG if Claude Code named it differently)" % root,
              file=sys.stderr)

    by_type = defaultdict(lambda: {"segments": 0, "edited": 0, "pre_edit": [], "turns": [], "readonly_turns": [],
                                   "pre_edit_turns": 0, "all_turns": 0, "mandated": 0})
    tools = Counter()
    failures = Counter()
    denials = Counter()
    transcripts = 0
    for path in sorted(root.glob("*/subagents/agent-*.jsonl")):
        meta_path = path.with_suffix(".meta.json")
        agent_type = "unknown"
        if meta_path.exists():
            try:
                agent_type = json.loads(meta_path.read_text(encoding="utf-8")).get("agentType", "unknown")
            except (OSError, ValueError):
                pass
        rows = []
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        when = next((stamp(row) for row in rows if stamp(row)), None)
        if when is None or (since and when < since) or (until and when >= until):
            continue
        transcripts += 1
        for row in rows:
            for use in tool_uses(row):
                name = use.get("name", "?")
                tools[name] += 1
                command = str(use.get("input", {}).get("command", ""))
                if name in ("Bash", "PowerShell") and HEREDOC.search(command):
                    tools["(heredoc)"] += 1
                if name == "PowerShell" and "Select-String" in command:
                    tools["(Select-String)"] += 1
            for error in tool_errors(row):
                failures["all"] += 1
                rules = set(RULE_ID.findall(error))
                for rule in rules:
                    denials[rule] += 1
                if not rules:
                    failures["other"] += 1
        for segment in segments_of(rows):
            total, first_edit, mandated = measure(segment)
            if total == 0:
                continue
            stats = by_type[agent_type]
            stats["segments"] += 1
            stats["all_turns"] += total
            stats["mandated"] += mandated
            stats["turns"].append(total)
            if first_edit is not None:
                stats["edited"] += 1
                stats["pre_edit"].append(first_edit)
                stats["pre_edit_turns"] += first_edit
            elif agent_type in READ_ONLY_TYPES:
                stats["readonly_turns"].append(total)

    report = {"window": {"since": args.since, "until": args.until}, "transcripts": transcripts, "by_type": {},
              "tools": dict(tools.most_common()), "failures": dict(failures), "denials_by_rule": dict(denials)}
    for agent_type, stats in sorted(by_type.items()):
        report["by_type"][agent_type] = {
            "intent": "read-only" if agent_type in READ_ONLY_TYPES else "edit-capable",
            "segments": stats["segments"],
            "edited_pct": round(100.0 * stats["edited"] / stats["segments"], 1) if stats["segments"] else 0.0,
            "turns_before_first_edit": quantiles(stats["pre_edit"]),
            "pre_edit_share_pct": round(100.0 * stats["pre_edit_turns"] / stats["all_turns"], 1)
            if stats["all_turns"] else 0.0,
            "turns_per_segment": quantiles(stats["turns"]),
            "mandated_reads": stats["mandated"],
        }
    text = json.dumps(report, indent=2)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
