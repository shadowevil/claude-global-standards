"""Measure a project's Claude Code sessions from their transcripts (read-only).

Usage:
    python -B transcript_metrics.py --project C:\\path\\to\\repo [--since 2026-10-01] [--top 12]

Reads ~/.claude/projects/<slug>/**/*.jsonl, where <slug> is the absolute project path with every
character that is not a letter or digit replaced by '-'. Writes nothing; prints ASCII only.

Every tool call and tool result is counted once, even when forked or continued sessions repeat the
same history in several transcript files. A subagent transcript copied under several session folders
(same agent-<id>.jsonl name) is read once, from its largest copy.

Reports:
  A. orientation  - subagent turns before their first edit, per segment (a segment starts at each
                    real dispatch or resume message; system-injected lines and compaction summaries
                    do not start one), median / p75 / mean
  B. docs         - reads and searches of CLAUDE.md and .claude/{docs,design,agents}/**, characters
                    pulled in, share of all tool output (approximate for shell commands)
  C. friction     - failed tool calls: hook denials by rule id, errors by tool, with rates
  D. habits       - heredoc-written scripts, PowerShell code searches, subagent re-reads of
                    CLAUDE.md and briefs, with rates
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="ascii", errors="replace")
except Exception:
    pass

EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
DOC_RE = re.compile(r"(\.claude[\\/](docs|design|agents)\b(?:[\\/][\w./\\-]*)?|CLAUDE\.md)", re.I)
SEARCH_RE = re.compile(r"\b(grep|rg|Select-String)\b")
READ_CMD_RE = re.compile(r"\b(cat|sed|head|tail|Get-Content|gc|type)\b")
HEREDOC_RE = re.compile(r"(cat|tee)\s*>\s*\S+\s*<<|python\s+-\s*<<")
PS_SEARCH_RE = re.compile(r"Select-String", re.I)
RULE_ID_RE = re.compile(r"\b([A-Z]{1,3}\d+[a-z]?(?:-\w+)?):\s")
COMPACTION_PREFIX = "This session is being continued"


def slug_for(path):
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path).resolve()))


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict))
    return ""


def norm_doc(p):
    p = p.replace("\\", "/").rstrip("/")
    k = p.lower().find(".claude/")
    if k >= 0:
        return p[k:]
    return "CLAUDE.md" if p.lower().endswith("claude.md") else p


def pct(a, b):
    return 100.0 * a / b if b else 0.0


def quantiles(values):
    if not values:
        return "n 0"
    v = sorted(values)
    p75 = v[min(len(v) - 1, int(len(v) * 0.75))]
    return "n %d  median %.0f  p75 %d  mean %.1f" % (len(v), statistics.median(v), p75, statistics.mean(v))


def starts_segment(o, content):
    """A real dispatch or resume: a user line that is not a tool result, not system-injected and
    not a compaction summary."""
    if o.get("isMeta") or o.get("isCompactSummary"):
        return False
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
    return not text_of(content).lstrip().startswith(COMPACTION_PREFIX)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", default=".", help="absolute project path (default: cwd)")
    ap.add_argument("--since", default="", help="only transcript lines on or after this UTC date (YYYY-MM-DD)")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()
    if a.since and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.since):
        print("--since must be YYYY-MM-DD")
        return 2

    root = Path.home() / ".claude" / "projects" / slug_for(a.project)
    if not root.is_dir():
        print("no transcripts at %s (they appear after the first session in that folder)" % root)
        return 0

    seen_calls, seen_results = set(), set()
    seg_pre_edit, seg_no_edit = [], 0
    doc_reads, doc_search = Counter(), Counter()
    doc_read_chars, doc_search_chars = Counter(), Counter()
    all_chars = doc_total_chars = 0
    calls = Counter()
    fails = 0
    hook_rules = Counter()
    hook_files = defaultdict(set)
    tool_errors = Counter()
    heredocs = ps_search = rereads = 0
    n_main = n_sub = 0
    uses = {}
    pending = {}  # results seen before their call: tool_use id -> (text, is_error, transcript stem)

    def count_result(tid, t, is_error, stem):
        nonlocal all_chars, doc_total_chars, fails
        seen_results.add(tid)
        all_chars += len(t)
        u = uses.get(tid)
        if u and u[0]:
            doc_total_chars += len(t)
            share = len(t) / max(len(u[2]), 1)
            for d in u[2]:
                (doc_read_chars if u[0] == "read" else doc_search_chars)[d] += share
        if is_error:
            fails += 1
            ids = set(RULE_ID_RE.findall(t)) if "hook error" in t else set()
            if ids:
                for rid in ids:
                    hook_rules[rid] += 1
                    hook_files[rid].add(stem)
            elif "hook error" not in t:
                tool_errors[u[1] if u else "?"] += 1

    files, sub_copies = [], {}
    for f in sorted(root.rglob("*.jsonl")):
        if "subagents" not in f.parts:
            files.append(f)
            continue
        best = sub_copies.get(f.stem)
        if best is None or f.stat().st_size > best.stat().st_size:
            sub_copies[f.stem] = f
    files += sorted(sub_copies.values())

    for f in files:
        is_sub = "subagents" in f.parts
        seen_msgs = set()
        seg_open, seg_edited, seg_pre = False, False, 0
        in_window = False
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue

        def close_segment():
            nonlocal seg_no_edit
            if is_sub and seg_open:
                if seg_edited:
                    seg_pre_edit.append(seg_pre)
                else:
                    seg_no_edit += 1

        with fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if a.since and str(o.get("timestamp", ""))[:10] < a.since:
                    continue
                in_window = True
                m = o.get("message") or {}
                content = m.get("content")
                typ = o.get("type")
                if typ == "user":
                    if starts_segment(o, content):
                        close_segment()
                        seg_open, seg_edited, seg_pre = True, False, 0
                        continue
                    if not isinstance(content, list):
                        continue
                    for b in content:
                        if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                            continue
                        tid = b.get("tool_use_id")
                        if tid in seen_results:
                            continue
                        t = text_of(b.get("content"))
                        if tid not in uses:
                            # its call may sit in a transcript read later: decide at the end
                            pending.setdefault(tid, (t, b.get("is_error"), f.stem))
                            continue
                        count_result(tid, t, b.get("is_error"), f.stem)
                    continue
                if typ != "assistant" or not isinstance(content, list):
                    continue
                mid = m.get("id")
                if mid not in seen_msgs:
                    seen_msgs.add(mid)
                    if seg_open and not seg_edited:
                        seg_pre += 1
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                        continue
                    n, i = b.get("name", ""), b.get("input") or {}
                    if n in EDIT_TOOLS and seg_open and not seg_edited:
                        seg_edited = True
                        seg_pre = max(seg_pre - 1, 0)
                    bid = b.get("id")
                    if bid in seen_calls:
                        continue
                    seen_calls.add(bid)
                    calls[n] += 1
                    kind, targets = "", []
                    if n == "Read":
                        fp = str(i.get("file_path", ""))
                        if DOC_RE.search(fp):
                            kind, targets = "read", [norm_doc(fp)]
                            if is_sub and re.search(r"CLAUDE\.md$|[\\/]agents[\\/]", fp, re.I):
                                rereads += 1
                    elif n == "Grep":
                        p = str(i.get("path", ""))
                        if DOC_RE.search(p):
                            kind, targets = "search", [norm_doc(p)]
                    elif n in ("Bash", "PowerShell"):
                        cmd = str(i.get("command", ""))
                        if HEREDOC_RE.search(cmd):
                            heredocs += 1
                        if n == "PowerShell" and PS_SEARCH_RE.search(cmd):
                            ps_search += 1
                        hits = [norm_doc(x.group(0)) for x in DOC_RE.finditer(cmd)]
                        if hits:
                            if SEARCH_RE.search(cmd):
                                kind, targets = "search", hits
                            elif READ_CMD_RE.search(cmd):
                                kind, targets = "read", hits
                    for t in targets:
                        (doc_reads if kind == "read" else doc_search)[t] += 1
                    uses[bid] = (kind, n, targets)
        close_segment()
        if in_window:
            n_sub += is_sub
            n_main += not is_sub

    for tid, (t, is_error, stem) in pending.items():
        if tid not in seen_results:
            count_result(tid, t, is_error, stem)

    total = sum(calls.values())
    shell = calls["Bash"] + calls["PowerShell"]
    denials = sum(hook_rules.values())
    print("project: %s%s" % (a.project, ("   since " + a.since) if a.since else ""))
    print("transcripts: %d main, %d subagent   unique tool calls: %d   failed: %d (%.1f%%)"
          % (n_main, n_sub, total, fails, pct(fails, total)))
    print()
    print("A. ORIENTATION (subagent segments: each real dispatch or resume)")
    print("   turns before first edit, segments that edited: %s" % quantiles(seg_pre_edit))
    print("   segments with no edit (read-only work, or never reached an edit): %d" % seg_no_edit)
    print()
    print("B. DOCS (approximate for shell commands)  %.1fM of %.1fM tool-output chars (%.1f%%)"
          % (doc_total_chars / 1e6, all_chars / 1e6, pct(doc_total_chars, all_chars)))
    print("   %-52s %6s %8s %6s %8s" % ("file", "reads", "per read", "search", "srch chr"))
    ranked = Counter({d: doc_read_chars[d] + doc_search_chars[d] for d in set(doc_read_chars) | set(doc_search_chars)})
    for d, _ in ranked.most_common(a.top):
        r = doc_reads[d]
        per = doc_read_chars[d] / r / 1000 if r else 0.0
        print("   %-52s %6d %7.1fk %6d %7.0fk" % (d[-52:], r, per, doc_search[d], doc_search_chars[d] / 1000))
    print()
    print("C. FRICTION")
    print("   hook denials: %d (%.2f per 100 calls)" % (denials, pct(denials, total)))
    for rid, n in hook_rules.most_common(a.top):
        print("     %-16s %5d across %d transcripts" % (rid, n, len(hook_files[rid])))
    print("   tool errors by tool (rate = errors per call of that tool):")
    for t, n in tool_errors.most_common(a.top):
        print("     %-40s %5d of %6d (%.1f%%)" % (t[-40:], n, calls[t], pct(n, calls[t])))
    print()
    print("D. HABITS")
    print("   heredoc-written scripts: %d (%.1f per 100 shell calls)" % (heredocs, pct(heredocs, shell)))
    print("   PowerShell code searches: %d (%.1f per 100 PowerShell calls); Grep tool calls: %d"
          % (ps_search, pct(ps_search, calls["PowerShell"]), calls["Grep"]))
    print("   subagent reads of CLAUDE.md / agent briefs (already in context): %d" % rereads)
    return 0


if __name__ == "__main__":
    sys.exit(main())
