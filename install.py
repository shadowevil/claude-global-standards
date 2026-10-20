"""Install this repo's global Claude Code setup into ~/.claude.

    python install.py                  dry run: what would be added or changed (writes nothing)
    python install.py --apply          back up what will be replaced, then copy
    python install.py --only skills    limit to one part: global | skills | hooks (repeatable)
    python install.py --hooks          also install hook code (hooks are skipped unless asked)
    python install.py --strict-git     append global/CLAUDE.git-strict.md to the installed CLAUDE.md

Mapping:
    global/CLAUDE.md   -> ~/.claude/CLAUDE.md        (+ CLAUDE.git-strict.md with --strict-git)
    global/docs/...    -> ~/.claude/docs/...
    skills/<name>/...  -> ~/.claude/skills/<name>/...
    hooks/...          -> ~/.claude/hooks/...        (only with --hooks)

Rules:
- Bytes are copied exactly (line endings included); a file that differs only in line endings is
  reported as such.
- Nothing in ~/.claude is ever deleted. Files there that this repo does not have are listed and left.
- Every replaced file is first copied to ~/.claude/staging/install-backups/<timestamp>/<same path>.
- settings.json is never touched: wiring a hook stays a manual step the user approves.
- Output is ASCII only.
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="ascii", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parent
TARGET = Path.home() / ".claude"
SKIP_PARTS = {"__pycache__", ".git"}
SKIP_SUFFIXES = {".pyc", ".jsonl", ".log"}


def wanted(p):
    return not (set(p.parts) & SKIP_PARTS) and p.suffix not in SKIP_SUFFIXES and p.is_file()


def claude_md(strict_git):
    """The bytes the installed CLAUDE.md should hold: the base file, plus the strict-git block."""
    data = (REPO / "global" / "CLAUDE.md").read_bytes()
    if strict_git:
        extra = (REPO / "global" / "CLAUDE.git-strict.md").read_bytes().replace(b"\r\n", b"\n")
        if b"\r\n" in data:
            extra = extra.replace(b"\n", b"\r\n")
        nl = b"\r\n" if b"\r\n" in data else b"\n"
        if not data.endswith(nl):
            data += nl
        data += extra
    return data


def plan(parts, strict_git=False):
    """Yield (part, source bytes, destination) for every file this repo installs."""
    if "global" in parts:
        yield "global", claude_md(strict_git), TARGET / "CLAUDE.md"
        docs = REPO / "global" / "docs"
        if docs.is_dir():
            for src in sorted(docs.rglob("*")):
                if wanted(src):
                    yield "global", src.read_bytes(), TARGET / "docs" / src.relative_to(docs)
    for part, sub in (("skills", "skills"), ("hooks", "hooks")):
        if part not in parts:
            continue
        root = REPO / sub
        if not root.is_dir():
            continue
        for src in sorted(root.rglob("*")):
            if wanted(src):
                yield part, src.read_bytes(), TARGET / sub / src.relative_to(root)


def classify(a, dst):
    if not dst.exists():
        return "ADD"
    b = dst.read_bytes()
    if a == b:
        return "same"
    if a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n"):
        return "CHANGE (line endings only)"
    return "CHANGE"


def extras(parts):
    """Files under the installed skill/hook folders that this repo does not carry."""
    out = []
    if "global" in parts and (TARGET / "docs").is_dir():
        for f in (TARGET / "docs").rglob("*"):
            if wanted(f) and not (REPO / "global" / "docs" / f.relative_to(TARGET / "docs")).exists():
                out.append(f)
    if "skills" in parts:
        for d in sorted((REPO / "skills").iterdir()):
            inst = TARGET / "skills" / d.name
            if inst.is_dir():
                for f in inst.rglob("*"):
                    if wanted(f) and not (d / f.relative_to(inst)).exists():
                        out.append(f)
    if "hooks" in parts and (TARGET / "hooks").is_dir():
        for f in (TARGET / "hooks").rglob("*"):
            if wanted(f) and not (REPO / "hooks" / f.relative_to(TARGET / "hooks")).exists():
                out.append(f)
    return out


def main():
    ap = argparse.ArgumentParser(description="Install the global Claude Code setup into ~/.claude.")
    ap.add_argument("--apply", action="store_true", help="copy (default is a dry run)")
    ap.add_argument("--only", action="append", choices=["global", "skills", "hooks"])
    ap.add_argument("--hooks", action="store_true", help="include hook code")
    ap.add_argument("--strict-git", action="store_true",
                    help="append global/CLAUDE.git-strict.md (never commit on the default branch)")
    a = ap.parse_args()

    parts = set(a.only or ["global", "skills", "hooks"])
    if "hooks" in parts and not a.hooks:
        parts.discard("hooks")
        print("hooks: skipped (pass --hooks to include hook code; wiring in settings.json stays manual)")

    rows = [(part, data, dst, classify(data, dst)) for part, data, dst in plan(parts, a.strict_git)]
    changes = [r for r in rows if r[3] != "same"]
    for part, data, dst, state in rows:
        if state != "same":
            print("%-28s %s" % (state, dst))
    print("%d file(s) to add or change, %d already identical" % (len(changes), len(rows) - len(changes)))
    for f in extras(parts):
        print("extra (left alone): %s" % f)

    if not a.apply:
        print("dry run - nothing written. Re-run with --apply to install.")
        return 0
    if not changes:
        print("nothing to do.")
        return 0

    backup = TARGET / "staging" / "install-backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    for part, data, dst, state in changes:
        if dst.exists():
            keep = backup / dst.relative_to(TARGET)
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, keep)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        if dst.read_bytes() != data:
            print("FAILED to verify %s" % dst)
            return 1
    print("installed %d file(s); replaced files backed up under %s" % (len(changes), backup))
    return 0


if __name__ == "__main__":
    sys.exit(main())
