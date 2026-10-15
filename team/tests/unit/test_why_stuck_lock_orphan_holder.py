"""
test_why_stuck_lock_orphan_holder.py
====================================
A STUCK-LOCK verdict's remedy is a ``release-locks.sh --release`` command.
release-locks.sh refuses a lock whose recorded holder is dead while a process
the record does not name still holds its flock (an orphan holder,
``release_locks.orphan_holder``) unless ``--force`` is given, so the remedy
must carry ``--force`` in that case -- and only then, for a dead holder.

Covered:
  - pgai_agent_kanban.ops.locks.flock_held reads a held flock as held, a free
    one as free, and a missing file as not held, without keeping any lock;
  - why.sh --project: a dead-pid record whose flock another process still
    holds gets a STUCK-LOCK remedy with ``--force`` and a cause naming the
    orphan;
  - the same dead-pid record with a free flock keeps the remedy without
    ``--force``.

The orphan is a child process of this test that opens the lock file and
holds an exclusive flock on it until the test closes its stdin. why.sh runs
against a sandbox kanban root under tmp_path (the project README's
live-install sandbox: every inherited root name set or cleared together).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from contextlib import contextmanager

from pgai_agent_kanban.ops import locks as ops_locks

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_WHY_SH = _TEAM_DIR / "scripts" / "why.sh"

_STUCK_LOCK_EXIT_CODE = 6
_NOW = 2_000_000_000
# Past the wake-periods stale threshold (3 x 120s) with the chain-budget
# floor collapsed by _env(), as test_why_stuck_lock_verdict.py does.
_STALE_MTIME = _NOW - (3 * 120 + 60)
_DEAD_PID = "999999999"

_HOLDER_CODE = (
    "import fcntl, sys\n"
    "f = open(sys.argv[1], 'rb')\n"
    "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
    "print('ready', flush=True)\n"
    "sys.stdin.read()\n"
)


@contextmanager
def _flock_holder(path: pathlib.Path):
    """A child process holding an exclusive flock on *path* until exit."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_CODE, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "ready"
        yield proc
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _sandbox(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "root"
    (root / "tmp").mkdir(parents=True)
    (root / "projects.cfg").write_text("[project:demo]\npriority=1\nenabled=true\n", encoding="utf-8")
    task = root / "projects" / "demo" / "tasks" / "CODER-20260910-001-demo"
    task.mkdir(parents=True)
    (task / "status.md").write_text("## State\nBACKLOG\n\n## Summary\nfixture task\n", encoding="utf-8")
    return root


def _write_dead_holder_lock(root: pathlib.Path) -> pathlib.Path:
    lock = root / "locks" / "repo-wake-demo.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        f"holder=coder\npid={_DEAD_PID}\nop=wake-chain:demo\ntask=CODER-20260910-001-demo\n",
        encoding="utf-8",
    )
    os.utime(lock, (_STALE_MTIME, _STALE_MTIME))
    return lock


def _env(root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME"):
        env.pop(name, None)
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(root / "tmp")
    env["MAX_RUNTIME_SECONDS"] = "0"
    env["MAX_TASK_SECONDS"] = "0"
    env["KILL_GRACE_SECONDS"] = "0"
    env["VERDICT_NOW_EPOCH"] = str(_NOW)
    return env


def _why(root: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_WHY_SH), "--project", "demo"],
        capture_output=True,
        text=True,
        env=_env(root),
        timeout=30,
    )


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------


def test_flock_held_reads_held_free_and_missing(tmp_path: pathlib.Path) -> None:
    lock = tmp_path / "probe.lock"
    lock.write_text("", encoding="utf-8")

    assert ops_locks.flock_held(lock) is False
    with _flock_holder(lock):
        assert ops_locks.flock_held(lock) is True
        assert ops_locks.flock_held(str(lock)) is True
    assert ops_locks.flock_held(lock) is False
    assert ops_locks.flock_held(tmp_path / "absent.lock") is False


# ---------------------------------------------------------------------------
# The STUCK-LOCK remedy
# ---------------------------------------------------------------------------


def test_dead_holder_with_orphan_flock_gets_force_in_remedy(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path)
    lock = _write_dead_holder_lock(root)

    with _flock_holder(lock):
        result = _why(root)

    assert result.returncode == _STUCK_LOCK_EXIT_CODE, result.stdout + result.stderr
    line = result.stdout.strip()
    assert line.startswith("demo: STUCK-LOCK"), line
    remedy = line.split("remedy:", 1)[1]
    assert "team/scripts/release-locks.sh --release repo-wake-demo --force" in remedy, remedy
    assert "still holds its flock" in line.split("remedy:", 1)[0], line


def test_dead_holder_with_free_flock_keeps_remedy_without_force(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path)
    _write_dead_holder_lock(root)

    result = _why(root)

    assert result.returncode == _STUCK_LOCK_EXIT_CODE, result.stdout + result.stderr
    line = result.stdout.strip()
    assert "team/scripts/release-locks.sh --release repo-wake-demo" in line
    assert "--force" not in line
    assert "still holds its flock" not in line
