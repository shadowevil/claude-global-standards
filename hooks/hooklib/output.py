"""The PreToolUse deny JSON, ASCII-safe printing, and the hit log with its size cap/rotation.

Enforcement default: this hook denies by default (unlike the project copy it was ported from,
which stays log-only until the PM opts an id in). User decision, 2026-09-28: the global package
installs LOG-ONLY for about a week so the user can decide enforcement from the real hit numbers.
Two ways to select log-only, checked in this order:
  1. `--log-only` on the hook command's OWN command line (write_guard.py's entry point passes this
     through as `force_log_only`) - the reliable way on Windows, where a `VAR=x cmd` prefix on the
     hook command in settings.json does not reliably reach the child process's environment.
  2. CLAUDE_WRITE_GUARD=log in the environment - kept working for compatibility / non-Windows use,
     and for anyone scripting a one-off probe without editing settings.json.
Anything else - both absent, or CLAUDE_WRITE_GUARD set to any other value - means enforce.

Per-rule enforcement (user decision, 2026-09-30): write_guard.py also takes `--enforce=W2[,W3]` on
the same command line. `enforcing()` below stays the GLOBAL switch; the per-rule list is layered on
top of it in write_guard.py (see its docstring), and CLAUDE_WRITE_GUARD=log (env_log_only()) stays a
kill switch for the whole hook, the list included. record_config_issues() notes a bad flag or id.
"""
import json
import os
import time

LOG_MAX_BYTES = 5 * 1024 * 1024
EXCERPT_MAX = 160
# not pathlib.Path.home(): the `pathlib` import alone measured as a meaningful share of hot-path
# latency (2026-09-28 perf review, ~118ms median per cold hook call with tempfile+pathlib imported
# unconditionally) for a value the environment already provides - see hooklib.paths.home_dir(),
# whose same os.environ lookup this mirrors rather than importing across modules for two lines.
_HOME = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
HITS_PATH = os.path.join(_HOME, ".claude", "hooks", "write_guard_hits.jsonl") if _HOME else ""
# env var override for the hits file - self-test and a reviewer probing the hook manually (piping a
# crafted PreToolUse event to main() via stdin, not --self-test) must never write the real file. A
# reviewer doing exactly that polluted it on 2026-09-28; the PM had to delete it. write_guard.py's
# main() also accepts a `--hits-path=<path>` command-line flag, handled the same way.
HITS_PATH_ENV = "CLAUDE_WRITE_GUARD_HITS_PATH"


def env_log_only():
    """True when CLAUDE_WRITE_GUARD=log is set in the environment (case-insensitive). Kept as its
    own function so write_guard.py can treat it as a kill switch that also overrides a per-rule
    `--enforce=` list (user-visible meaning of CLAUDE_WRITE_GUARD=log: nothing denies)."""
    return os.environ.get("CLAUDE_WRITE_GUARD", "").strip().lower() == "log"


def enforcing(force_log_only=False):
    """Whether a hit should be able to deny the call. False (log-only) when EITHER
    `force_log_only` is True (the caller read `--log-only` off its own command line) OR
    CLAUDE_WRITE_GUARD=log is set in the environment. Everything else means enforce.
    This is the GLOBAL switch; write_guard.py layers the per-rule `--enforce=` list on top of it
    (a rule listed there can deny even while this returns False, unless the env kill switch
    CLAUDE_WRITE_GUARD=log is set)."""
    if force_log_only:
        return False
    return not env_log_only()


def resolve_hits_path(override=None):
    """The hits file to use: an explicit `override` (e.g. a `--hits-path=` argument) wins, then
    CLAUDE_WRITE_GUARD_HITS_PATH from the environment, then the real default HITS_PATH."""
    if override:
        return override
    return os.environ.get(HITS_PATH_ENV) or HITS_PATH


def excerpt(s):
    s = " ".join(s.split())
    return s[:EXCERPT_MAX]


# CORRECTION 1, same review pass: the first version of this fix assumed os.open(path, os.O_APPEND)
# + os.write() gives Windows-atomic append (one WriteFile() call, serialized against every other
# FILE_APPEND_DATA writer) the way real POSIX O_APPEND does on Linux. Measured, empirically, that
# this is FALSE for Python's os.open()/os.write() on Windows: a 30-process x 60-row concurrent
# stress test still produced corrupt lines AND silently LOST rows (1511 of 1800 expected lines, 5
# of those corrupt) - the CRT-level O_APPEND here does not carry the same cross-process atomicity
# guarantee as a raw Win32 FILE_APPEND_DATA handle, or Python is not obtaining one. Fixed with real
# mutual exclusion instead: a lock on a fixed 1-byte sentinel region at offset 0 - every writer
# locks the SAME region regardless of where its own idea of end-of-file is, so exactly one process
# holds it at a time; the position of the actual data write is always a fresh SEEK_END taken AFTER
# the lock is held, never trusted from before it.
#
# CORRECTION 2, final review: that fix used a BLOCKING lock (msvcrt.LK_LOCK) with 5 outer retries.
# Under 60-process contention the reviewer measured a max per-call time of 8.16s against the 10s
# PreToolUse hook timeout - a theoretical worst case near 50s. Claude Code kills a hook that times
# out and lets the tool call proceed anyway, so a blocking wait here does not fail safe: logging
# silently drops AND, once this is ever enforced, a genuine violation would silently be ALLOWED
# because the hook never got to answer. Fixed by replacing the blocking wait with a NON-BLOCKING
# poll under a small fixed time budget (LOCK_BUDGET_SECONDS): try the lock, and if another writer
# holds it, sleep LOCK_POLL_SECONDS and try again, never blocking on the OS lock call itself. If
# the budget runs out, the batch is dropped (not written) and the drop is recorded best-effort, in
# ONE unlocked, unretried line, to `<hits path>.dropped` - deliberately not routed through the same
# locked path that just failed, so recording a drop can never itself block or compound the failure.
CHUNK_MAX_BYTES = 4000
_LOCK_BYTES = 1  # the sentinel region every writer contends for, at a fixed offset (0)
LOCK_POLL_SECONDS = 0.01   # ~10ms between non-blocking lock attempts
LOCK_BUDGET_SECONDS = 0.3  # ~300ms total before giving up and dropping the batch
DROPPED_SUFFIX = ".dropped"


def _try_lock_nonblocking(fh):
    """True if the sentinel region was acquired WITHOUT blocking; False if another writer
    currently holds it (never waits - a busy lock is reported immediately, not waited out) or the
    platform's locking primitive is unavailable (fails open: no serialization, but the caller still
    gets to write rather than being blocked indefinitely by a lock call it cannot make)."""
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, _LOCK_BYTES)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False
    except Exception:
        return True  # no usable lock primitive - proceed unlocked rather than never proceed at all


def _unlock(fh):
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, _LOCK_BYTES)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass  # never let unlocking fail loudly - see the log_hits() finally that calls this


def _record_drop(hits_path, n_rows):
    """Best-effort, ONE unlocked append to `<hits_path>.dropped` - deliberately not routed through
    the locked write path (that path is exactly what just failed to acquire within budget), and
    never retried, so recording a drop can itself never block a tool call."""
    try:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        line = json.dumps({"ts": ts, "dropped_rows": n_rows,
                            "reason": "lock not acquired within %.2fs" % LOCK_BUDGET_SECONDS},
                           ensure_ascii=True) + "\n"
        with open(hits_path + DROPPED_SUFFIX, "a", encoding="ascii", newline="\n") as fh:
            fh.write(line)
    except Exception:
        pass


CONFIG_SUFFIX = ".config"
CONFIG_MAX_BYTES = 64 * 1024  # the config-issue sidecar stops growing past this - never unbounded


def record_config_issues(issues, raw_args, hits_path=None):
    """Best-effort, ONE unlocked append to `<hits path>.config` naming every problem found in the
    hook's own command-line flags (an unknown or non-enforceable id in `--enforce=`, an unrecognized
    flag). Never raises, never blocks, never retried, and stops writing once the file passes
    CONFIG_MAX_BYTES, so a misconfigured settings.json cannot fill the disk. A config problem is
    never a reason to deny or to crash: the hook keeps running with whatever it could parse."""
    if not issues:
        return
    try:
        path = resolve_hits_path(hits_path) + CONFIG_SUFFIX
        try:
            if os.path.getsize(path) > CONFIG_MAX_BYTES:
                return
        except OSError:
            pass
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        line = json.dumps({"ts": ts, "issues": list(issues), "args": [str(a)[:80] for a in raw_args][:12]},
                          ensure_ascii=True) + "\n"
        with open(path, "a", encoding="ascii", newline="\n") as fh:
            fh.write(line)
    except Exception:
        pass


def _chunk_lines(lines, max_bytes):
    """lines grouped so each group's total encoded size (with trailing newlines) stays under
    `max_bytes` - one group is one os.write() call. A single line longer than max_bytes still gets
    its own group (never split mid-line, which would corrupt the JSON)."""
    group = []
    size = 0
    for line in lines:
        b = len(line) + 1  # +1 for the newline this line will be joined with
        if group and size + b > max_bytes:
            yield group
            group = []
            size = 0
        group.append(line)
        size += b
    if group:
        yield group


def log_hits(rows, hits_path=None):
    """rows: [{"rule": ..., "tool": ..., "root": ..., "cwd": ..., "target": ..., "sample": [...],
    "session": ..., "ms": ..., "msg": ...}, ...] Appended as ASCII JSON lines under a non-blocking
    poll for a real exclusive lock (see _try_lock_nonblocking above - NOT bare O_APPEND, which
    measured as unsafe on Windows, and NOT a blocking lock wait, which measured as unsafe against
    the 10s PreToolUse hook timeout), each batch chunked to at most CHUNK_MAX_BYTES so no single
    write is unreasonably large, rotating the file to .1 once it passes LOG_MAX_BYTES. If the lock
    is not acquired within LOCK_BUDGET_SECONDS, the whole batch is DROPPED (never written) and the
    drop is recorded best-effort in `<hits_path>.dropped` - never blocked on, never retried. A
    malformed (non-JSON) line should not happen now, but any reader of this file should still skip
    one rather than fail the whole read - see hits_summary.py.
    `hits_path=None` (the normal call from main()) resolves through resolve_hits_path() - explicit
    override, then CLAUDE_WRITE_GUARD_HITS_PATH, then the real default - so self-test and a manual
    reviewer probe can redirect this without ever touching the real file by accident."""
    if not rows:
        return
    hits_path = resolve_hits_path(hits_path)
    try:
        os.makedirs(os.path.dirname(hits_path), exist_ok=True)
        try:
            if os.path.getsize(hits_path) > LOG_MAX_BYTES:
                os.replace(hits_path, hits_path + ".1")
        except OSError:
            pass
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        lines = []
        for row in rows:
            row = dict(row)
            row["ts"] = ts
            lines.append(json.dumps(row, ensure_ascii=True))
        # ensure the file exists before opening "r+b" (which, unlike "a"/"ab", never creates one
        # and gives full manual control over the file position - needed so the lock's own seek(0)
        # and the write's later seek(SEEK_END) are never second-guessed by the platform's own
        # append-mode positioning quirks)
        try:
            os.close(os.open(hits_path, os.O_CREAT | os.O_WRONLY, 0o644))
        except OSError:
            pass
        with open(hits_path, "r+b") as fh:
            acquired = False
            deadline = time.time() + LOCK_BUDGET_SECONDS
            try:
                while True:
                    if _try_lock_nonblocking(fh):
                        acquired = True
                        break
                    if time.time() >= deadline:
                        break
                    time.sleep(LOCK_POLL_SECONDS)
                if acquired:
                    fh.seek(0, os.SEEK_END)  # the TRUE end, taken only after the lock is held
                    for group in _chunk_lines(lines, CHUNK_MAX_BYTES):
                        blob = ("\n".join(group) + "\n").encode("ascii")
                        fh.write(blob)
                    fh.flush()
            finally:
                if acquired:
                    _unlock(fh)
        if not acquired:
            _record_drop(hits_path, len(rows))
    except Exception:
        pass  # a broken log must never block or fail a tool call


def deny(reasons):
    """The PreToolUse deny payload for one or more reason strings, or None if there are none.
    Same shape as the project hook's decide()/decide_shell()."""
    if not reasons:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": " | ".join(reasons)}}


def combine(*verdicts):
    """One PreToolUse deny merging every non-None verdict's reason, or None if none denied. A
    PreToolUse hook returns a single decision, so two independent rule modules denying the SAME
    call (e.g. W2's unsafe-target and W3's outside-root, on the same delete) are merged here rather
    than one silently overwriting the other."""
    reasons = [v["hookSpecificOutput"]["permissionDecisionReason"] for v in verdicts if v]
    if not reasons:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": " | ".join(reasons)}}


def print_verdict(verdict):
    if verdict is None:
        return
    import sys
    sys.stdout.write(json.dumps(verdict, ensure_ascii=True))
    sys.stdout.flush()
