"""backup.py - pre-change backup system for Claude's modifications (change sets are the rollback
for a change until it builds, the user confirms it works, and it is committed).

    python backup.py save    --label <name> --files <paths...> [--new-files <paths...>]
    python backup.py restore --set <set-name>
    python backup.py list
    python backup.py purge   --set <set-name | all>

Sets live in <repo>\\.claude\\.backups\\<timestamp>-<label>\\ with repo-relative paths preserved
(moved from <repo>\\.backups\\ in the 2026-09-28 layout change; gitignored either way).
manifest.txt lines: "REL|<path>" = pre-change copy saved, "NEW|<path>" = created file
(restore deletes it). The on-disk layout is identical to the retired backup.ps1, so old sets restore.

Stray paths after --files (e.g. "--files a b c") are backed up too; "a,b" is split on commas.
"""

import argparse
import os
import re
import shutil
import stat
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKUP_ROOT = REPO_ROOT / ".claude" / ".backups"
MANIFEST_NAME = "manifest.txt"
STAMP_FORMAT = "%Y%m%d-%H%M%S"
SEPARATOR = "|"
# A label is one plain path segment: no separators, not "." or "..", and no trailing "." (Windows strips it,
# so the printed set name would not match the folder).
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_-]([A-Za-z0-9._-]*[A-Za-z0-9_-])?$")
SET_PATTERN = re.compile(r"^\d{8}-\d{6}-[A-Za-z0-9_-]([A-Za-z0-9._-]*[A-Za-z0-9_-])?$")
PURGE_ALL = "all"
# Manifest kinds restore acts on; other kinds (db_snapshot's MYSQL lines) belong to other tools and are skipped.
FILE_KINDS = ("REL", "NEW")
# A NEW entry is deleted on restore, so it may never name git's store or the backup sets themselves.
NEW_FORBIDDEN = (".git", os.path.join(".claude", ".backups"))


class BackupError(Exception):
	pass


def backup_root() -> Path:
	"""BACKUP_ROOT, refused when it, or any folder above it (.claude), is a link or junction.

	Non-strict resolve() follows every existing link on the way, so this holds before .backups exists too."""
	if BACKUP_ROOT.is_symlink() or BACKUP_ROOT.resolve() != BACKUP_ROOT.absolute():
		raise BackupError(f"{BACKUP_ROOT} is or sits under a link; refusing to use it")
	inside_repo(BACKUP_ROOT)
	return BACKUP_ROOT


def inside_repo(path: Path) -> Path:
	"""Resolve path and require it strictly inside the repo; a sibling like <repo>-copy does not pass."""
	full = path.resolve()
	prefix = str(REPO_ROOT).lower() + os.sep
	if not str(full).lower().startswith(prefix):
		raise BackupError(f"outside repo: {path}")
	return full


def set_dir_for(set_name: str) -> Path:
	"""The directory of a named set, which must be a direct child of BACKUP_ROOT."""
	if not set_name or not SET_PATTERN.fullmatch(set_name):
		raise BackupError(f"not a backup set name: {set_name!r}")
	root = backup_root()
	set_dir = (root / set_name).resolve()
	if set_dir.parent != root.resolve():
		raise BackupError(f"set outside {BACKUP_ROOT}: {set_name}")
	return set_dir


def split_commas(values):
	out = []
	for value in values:
		out.extend(part for part in value.split(",") if part)
	return out


def relative_path(path: str) -> str:
	full = Path(path)
	if not full.exists():
		raise BackupError(f"Cannot find path '{path}' because it does not exist.")
	return str(inside_repo(full))[len(str(REPO_ROOT)) + 1:]


def read_manifest(set_dir: Path):
	path = set_dir / MANIFEST_NAME
	if not path.exists():
		return []
	try:
		return path.read_text(encoding="utf-8-sig").splitlines()
	except UnicodeDecodeError as error:
		raise BackupError(f"manifest is not UTF-8: {path}") from error


def new_file_rel(file: str) -> str:
	# a new file may not exist yet; it is still resolved and must sit inside the repo
	rel = str(inside_repo(Path(file)))[len(str(REPO_ROOT)) + 1:]
	for forbidden in NEW_FORBIDDEN:
		if rel.lower() == forbidden.lower() or rel.lower().startswith(forbidden.lower() + os.sep):
			raise BackupError(f"a new file may not be under {forbidden}: {file}")
	return rel


def save(label, files, new_files):
	if not label:
		raise BackupError("save requires --label")
	if not LABEL_PATTERN.fullmatch(label):
		raise BackupError(f"--label must be letters, digits, '.', '_' or '-': {label!r}")
	if not files and not new_files:
		raise BackupError("save requires --files and/or --new-files")
	# validate every path before anything is written
	rels = [relative_path(file) for file in files]
	for file, rel in zip(files, rels):
		if not (REPO_ROOT / rel).is_file():
			raise BackupError(f"not a file: {file}")
	# A NEW entry is deleted on restore with no copy kept, so at save time it must not exist yet: an existing
	# file (or a directory) would be lost. Restore checks NEW entries without this, since by then they exist.
	for file in new_files:
		if Path(file).exists():
			raise BackupError(f"--new-files names a path that already exists; back it up with --files instead: {file}")
	new_rels = [new_file_rel(file) for file in new_files]
	set_name = f"{time.strftime(STAMP_FORMAT)}-{label}"
	set_dir = set_dir_for(set_name)
	set_dir.parent.mkdir(parents=True, exist_ok=True)
	try:
		# never reuse a set: the failure cleanup below may only remove what this run created
		set_dir.mkdir()
	except FileExistsError as error:
		raise BackupError(f"set {set_name} already exists; run the save again") from error
	manifest = []
	try:
		for rel in rels:
			target = set_dir / rel
			target.parent.mkdir(parents=True, exist_ok=True)
			shutil.copy2(REPO_ROOT / rel, target)
			manifest.append(f"REL{SEPARATOR}{rel}")
	except OSError as error:
		# never leave a half-made set behind
		shutil.rmtree(set_dir, onerror=_force_remove)
		raise BackupError(f"save failed, set removed: {error}") from error
	for rel in new_rels:
		manifest.append(f"NEW{SEPARATOR}{rel}")
	# UTF-8 with BOM and CRLF, as PowerShell's Out-File wrote it
	with open(set_dir / MANIFEST_NAME, "w", encoding="utf-8-sig", newline="") as handle:
		handle.write("".join(line + "\r\n" for line in manifest))
	print(f"saved set: {set_name} ({len(files)} backed up, {len(new_files)} new tracked)")


def restore(set_name):
	if not set_name:
		raise BackupError("restore requires --set")
	set_dir = set_dir_for(set_name)
	if not set_dir.exists():
		raise BackupError(f"no such set: {set_name}")
	# re-check every manifest path before touching anything: a manifest is a file, not a trusted input
	entries = []
	for line in read_manifest(set_dir):
		kind, _, rel = line.partition(SEPARATOR)
		if kind not in FILE_KINDS:
			continue
		if not rel or Path(rel).is_absolute():
			raise BackupError(f"bad manifest line in {set_name}: {line!r}")
		if kind == "NEW":
			live = REPO_ROOT / new_file_rel(str(REPO_ROOT / rel))
			if live.is_dir():
				raise BackupError(f"manifest NEW entry is a directory: {rel}")
		else:
			live = inside_repo(REPO_ROOT / rel)
			source = (set_dir / rel).resolve()
			if not str(source).lower().startswith(str(set_dir).lower() + os.sep):
				raise BackupError(f"manifest copy outside its set: {rel}")
			if not source.is_file():
				raise BackupError(f"manifest copy missing from its set: {rel}")
		entries.append((kind, rel, live))
	restored = 0
	deleted = 0
	for kind, rel, live in entries:
		if kind == "REL":
			live.parent.mkdir(parents=True, exist_ok=True)
			if live.exists():
				live.chmod(live.stat().st_mode | stat.S_IWRITE)
			shutil.copy2(set_dir / rel, live)
			# A restored file keeps its old mtime, so MSBuild would skip recompiling against it.
			now = time.time()
			os.utime(live, (now, now))
			restored += 1
		elif kind == "NEW" and live.exists():
			live.chmod(live.stat().st_mode | stat.S_IWRITE)
			live.unlink()
			deleted += 1
	print(f"restored set: {set_name} ({restored} files restored, {deleted} created files removed)")


def list_sets():
	if not BACKUP_ROOT.exists():
		print("(no backup sets)")
		return
	for directory in sorted((d for d in BACKUP_ROOT.iterdir() if d.is_dir()), key=lambda d: d.name):
		try:
			print(f"{directory.name}  ({len(read_manifest(directory))} entries)")
		except BackupError:
			print(f"{directory.name}  (unreadable manifest)")


def _force_remove(function, path, _excinfo):
	os.chmod(path, stat.S_IWRITE)
	function(path)


def purge(set_name):
	if not set_name:
		raise BackupError('purge requires --set (set name or "all")')
	if set_name == PURGE_ALL:
		root = backup_root()
		if root.exists():
			shutil.rmtree(root, onerror=_force_remove)
		print("purged all backup sets")
		return
	set_dir = set_dir_for(set_name)
	if not set_dir.exists():
		raise BackupError(f"no such set: {set_name}")
	shutil.rmtree(set_dir, onerror=_force_remove)
	print(f"purged set: {set_name}")


def main() -> int:
	parser = argparse.ArgumentParser(description="Pre-change backup sets (save / restore / list / purge).")
	parser.add_argument("action", choices=["save", "restore", "list", "purge"])
	parser.add_argument("--label")
	parser.add_argument("--files", nargs="*", default=[])
	parser.add_argument("--new-files", nargs="*", default=[])
	parser.add_argument("--set")
	args, extras = parser.parse_known_args()
	stray = [e for e in extras if not e.startswith("-")]
	unknown = [e for e in extras if e.startswith("-")]
	if unknown:
		parser.error("unrecognized arguments: " + " ".join(unknown))
	files = split_commas(args.files + stray)
	new_files = split_commas(args.new_files)
	try:
		if args.action == "save":
			save(args.label, files, new_files)
		elif args.action == "restore":
			restore(args.set)
		elif args.action == "list":
			list_sets()
		else:
			purge(args.set)
	except BackupError as error:
		print(f"backup.py: {error}", file=sys.stderr)
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
