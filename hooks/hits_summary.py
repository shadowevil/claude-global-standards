"""Optional summary reader for the write_guard hit log (~/.claude/hooks/write_guard_hits.jsonl by
default, or --path=<file>). Read-only: prints counts, never edits the log.

Skips a malformed (non-JSON) line rather than failing the whole read - log_hits() now writes each
batch as one or more atomic os.write() calls (see hooklib/output.py), so a corrupt line should not
happen any more, but a reader should never assume that about a log it did not just write itself
(an older log from before this fix, a line truncated by LOG_MAX_BYTES rotation mid-write, ...).

    python -I -S hits_summary.py [--path=<file>]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from hooklib.output import HITS_PATH  # noqa: E402


def cli_value(argv, name):
    prefix = "--" + name + "="
    for a in argv:
        if a.startswith(prefix):
            return a[len(prefix):]
    return None


def read_rows(path):
    """(rows, n_malformed) - every line that parses as JSON, plus a count of the ones that did not
    (skipped, never raised)."""
    rows = []
    n_bad = 0
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    n_bad += 1
    except OSError:
        pass
    return rows, n_bad


def main():
    argv = sys.argv[1:]
    path = cli_value(argv, "path") or HITS_PATH
    rows, n_bad = read_rows(path)
    print("hits file: %s" % path)
    print("rows: %d  malformed (skipped): %d" % (len(rows), n_bad))
    if not rows:
        return 0
    by_rule = {}
    by_root = {}
    by_tool = {}
    for r in rows:
        by_rule[r.get("rule")] = by_rule.get(r.get("rule"), 0) + 1
        by_root[r.get("root")] = by_root.get(r.get("root"), 0) + 1
        by_tool[r.get("tool")] = by_tool.get(r.get("tool"), 0) + 1
    print("\nby rule:")
    for k in sorted(by_rule, key=lambda k: -by_rule[k]):
        print("  %-20s %5d" % (k, by_rule[k]))
    print("\nby root (top 15):")
    for k in sorted(by_root, key=lambda k: -by_root[k])[:15]:
        print("  %-70s %5d" % (k, by_root[k]))
    print("\nby tool:")
    for k in sorted(by_tool, key=lambda k: -by_tool[k]):
        print("  %-20s %5d" % (k, by_tool[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
