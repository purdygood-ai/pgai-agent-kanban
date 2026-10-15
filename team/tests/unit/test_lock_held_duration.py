"""
test_lock_held_duration.py
==========================
Behavioral unit tests for the lock's recorded acquisition time and every
surface that reports how long a lock has been held
(team/scripts/lib/lock_provenance.sh, team/scripts/why.sh --details,
team/scripts/release-locks.sh).

Acceptance criteria exercised:
  (1) pgai_lock_acquire writes acquired_at, parseable as ISO-8601 UTC, in
      the same record as holder/pid/op/task; pgai_lock_set_task (what the
      wake chain calls as it dispatches a task) changes task= from none to
      the task id and keeps every other field; release removes the record.
  (2) pgai_lock_set_task never writes to a lock this process does not hold
      at its path -- the other holder's record is left byte-for-byte.
  (3) why.sh --details renders a lock seeded as acquired 5 minutes ago as
      "held: 5m (since <acquired_at>) by <holder> pid <pid> — chain, no task
      yet" with held verdict "live", and one seeded past the stale floor
      as "long (Nm past floor)".
  (4) A legacy record (no acquired_at) and a torn one both render
      "acquired_at not recorded (pre-upgrade lock)" with no invented
      duration and no verdict, and no mtime-derived age line appears.
  (5) release-locks.sh's listing shows the same held line and verdict.
  (6) The orphan-holder narration and the overdue-holder narration
      cite the held time from acquired_at, not the file's mtime.

The wake chain's own dispatch-time update is exercised end to end in
tests/integration/test_chain_lock_held_through_agent.py, which reads the
live chain's lock while its stubbed subagent runs.

All paths live under pytest's tmp_path; the live kanban root is never read
or written.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import subprocess
import textwrap
import time

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_LOCK_PROVENANCE_SH = _TEAM_DIR / "scripts" / "lib" / "lock_provenance.sh"
_WHY_SH = _TEAM_DIR / "scripts" / "why.sh"
_RELEASE_LOCKS_SH = _TEAM_DIR / "scripts" / "release-locks.sh"

# Pinned chain budget so the stale floor is known exactly, whatever the
# calling environment exports: 300 + 0 + 10 + the fixed 120s margin = 430s,
# above the default wake-periods product (3 x 120s = 360s), so the floor is
# the effective threshold.
_FLOOR_ENV = {
    "MAX_RUNTIME_SECONDS": "300",
    "MAX_TASK_SECONDS": "0",
    "KILL_GRACE_SECONDS": "10",
}
_FLOOR_SECONDS = 430

_ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"


def _iso_ago(seconds: int) -> str:
    """ISO-8601 UTC timestamp *seconds* before now, in the writer's form."""
    then = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    return then.strftime(_ISO_UTC)


def _fields(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out.setdefault(key, value)
    return out


def _seed_lock(locks_dir: pathlib.Path, name: str, record: str) -> pathlib.Path:
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_file = locks_dir / name
    lock_file.write_text(record, encoding="utf-8")
    return lock_file


def _board(tmp_path: pathlib.Path, project: str = "demo") -> pathlib.Path:
    """A minimal kanban root with one registered project and no tasks."""
    root = tmp_path / "board"
    (root / "projects" / project / "tasks").mkdir(parents=True, exist_ok=True)
    (root / "projects.cfg").write_text(
        f"[project:{project}]\npriority=1\nenabled=true\n", encoding="utf-8"
    )
    return root


def _env(kanban_root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_PROJECT_ROOT", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["KANBAN_ROOT"] = str(kanban_root)
    env.update(_FLOOR_ENV)
    return env


def _why_locks(kanban_root: pathlib.Path) -> str:
    result = subprocess.run(
        ["bash", str(_WHY_SH), "--project", "demo", "--details"],
        capture_output=True, text=True, env=_env(kanban_root), timeout=30, cwd=str(_TEAM_DIR),
    )
    assert result.returncode in (0, 2, 3, 4, 5, 6), f"why.sh refused: {result.stderr!r}"
    marker = "== locks ==\n"
    assert marker in result.stdout, result.stdout
    section = result.stdout.split(marker, 1)[1]
    return section.split("\n== ", 1)[0]


# ---------------------------------------------------------------------------
# (1) acquire -> acquired_at; dispatch -> task=; release -> gone
# ---------------------------------------------------------------------------


def test_acquire_records_acquired_at_set_task_names_task_and_release_removes(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" coder 4242 "wake-chain:demo" none fd
        echo "== acquired"; cat "{lock_file}"
        pgai_lock_set_task "{lock_file}" "$fd" CODER-20261001-011-a-long-task-id
        echo "== dispatched"; cat "{lock_file}"
        pgai_lock_set_task "{lock_file}" "$fd" T-2
        echo "== redispatched"; cat "{lock_file}"
        echo "== size $(stat -c %s "{lock_file}")"
        pgai_lock_release "{lock_file}" "$fd"
        [[ -e "{lock_file}" ]] && echo "STILL_THERE" || echo "GONE"
    """)
    before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    result = run_bash(tmp_path, script)
    after = datetime.datetime.now(datetime.timezone.utc)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    acquired = _fields(result.stdout.split("== acquired\n")[1].split("== dispatched")[0])
    dispatched = _fields(result.stdout.split("== dispatched\n")[1].split("== redispatched")[0])
    redispatched = _fields(result.stdout.split("== redispatched\n")[1].split("== size")[0])

    assert acquired["task"] == "none"
    stamp = datetime.datetime.strptime(acquired["acquired_at"], _ISO_UTC).replace(
        tzinfo=datetime.timezone.utc
    )
    assert before - datetime.timedelta(seconds=1) <= stamp <= after, (
        f"acquired_at {acquired['acquired_at']!r} is not the acquisition time in UTC"
    )

    assert dispatched == {**acquired, "task": "CODER-20261001-011-a-long-task-id"}, dispatched
    # A shorter task id still leaves a complete record (padding, never a
    # truncated file) with every other field intact.
    assert redispatched == {**acquired, "task": "T-2"}, redispatched
    assert "GONE" in result.stdout, "release must remove the lock file and its record"


# ---------------------------------------------------------------------------
# (2) set_task never writes into a lock this process no longer holds
# ---------------------------------------------------------------------------


def test_set_task_leaves_another_holders_record_untouched(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    other = "holder=chain-b\npid=424242\nop=wake-chain:demo\ntask=none\nacquired_at=2026-01-01T00:00:00Z\n"
    script = textwrap.dedent(f"""\
        set -uo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" coder "$$" "wake-chain:demo" none fd
        # Another process reaps and reacquires: a new inode now sits at the path.
        rm -f "{lock_file}"
        printf '%s' '{other}' > "{lock_file}"
        pgai_lock_set_task "{lock_file}" "$fd" T-9
        echo "SET_RC=$?"
        pgai_lock_abandon "{lock_file}" "$fd"
    """)
    result = run_bash(tmp_path, script)
    assert "SET_RC=1" in result.stdout, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert lock_file.read_text(encoding="utf-8") == other


# ---------------------------------------------------------------------------
# (3) why.sh: 5m live, past-floor long
# ---------------------------------------------------------------------------


def test_why_details_renders_held_line_and_live_verdict(tmp_path: pathlib.Path) -> None:
    root = _board(tmp_path)
    pid = os.getpid()  # alive for the whole test
    acquired_at = _iso_ago(300)
    _seed_lock(
        root / "locks", "repo-wake-demo.lock",
        f"holder=coder\npid={pid}\nop=wake-chain:demo\ntask=none\nacquired_at={acquired_at}\n",
    )
    locks = _why_locks(root)
    assert (
        f"  held: 5m (since {acquired_at}) by coder pid {pid} — chain, no task yet\n" in locks
    ), locks
    assert "  held verdict: live\n" in locks, locks
    assert "age:" not in locks, f"the mtime-derived age line must be gone: {locks!r}"


def test_why_details_renders_long_verdict_with_overage_past_floor(tmp_path: pathlib.Path) -> None:
    root = _board(tmp_path)
    pid = os.getpid()
    # 10 minutes past the floor, less 30s of slack so a slow test run still
    # rounds the overage up to exactly 10 minutes.
    acquired_at = _iso_ago(_FLOOR_SECONDS + 600 - 30)
    _seed_lock(
        root / "locks", "repo-wake-demo.lock",
        f"holder=coder\npid={pid}\nop=wake-chain:demo\ntask=CODER-1\nacquired_at={acquired_at}\n",
    )
    locks = _why_locks(root)
    assert f"(since {acquired_at}) by coder pid {pid} — CODER-1\n" in locks, locks
    assert "  held verdict: long (10m past floor)\n" in locks, locks


# ---------------------------------------------------------------------------
# (4) legacy / torn acquired_at
# ---------------------------------------------------------------------------


def test_why_details_says_not_recorded_for_legacy_and_torn_records(tmp_path: pathlib.Path) -> None:
    for label, extra in (("legacy", ""), ("torn", "acquired_at=2026-10-01T04:2\n")):
        root = _board(tmp_path / label)
        _seed_lock(
            root / "locks", "repo-wake-demo.lock",
            f"holder=coder\npid={os.getpid()}\nop=wake-chain:demo\ntask=T-1\n{extra}",
        )
        locks = _why_locks(root)
        assert (
            f"  held: acquired_at not recorded (pre-upgrade lock) by coder pid {os.getpid()} — T-1\n"
            in locks
        ), f"{label}: {locks!r}"
        assert "held verdict:" not in locks, f"{label}: no duration means no verdict: {locks!r}"
        assert "age:" not in locks, f"{label}: {locks!r}"


# ---------------------------------------------------------------------------
# (5) release-locks.sh listing shows the same held line
# ---------------------------------------------------------------------------


def test_release_locks_listing_shows_held_line_and_verdict(tmp_path: pathlib.Path) -> None:
    root = _board(tmp_path)
    pid = os.getpid()
    acquired_at = _iso_ago(300)
    _seed_lock(
        root / "locks", "repo-wake-demo.lock",
        f"holder=coder\npid={pid}\nop=wake-chain:demo\ntask=T-7\nacquired_at={acquired_at}\n",
    )
    _seed_lock(root / "locks", "repo-wake-old.lock", f"holder=coder\npid={pid}\nop=o\ntask=none\n")
    env = _env(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(tmp_path / "scratch")
    result = subprocess.run(
        ["bash", str(_RELEASE_LOCKS_SH)],
        capture_output=True, text=True, env=env, timeout=30, cwd=str(_TEAM_DIR),
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert f"  held: 5m (since {acquired_at}) by coder pid {pid} — T-7\n" in result.stdout
    assert "  held verdict: live\n" in result.stdout
    assert (
        f"  held: acquired_at not recorded (pre-upgrade lock) by coder pid {pid} — chain, no task yet\n"
        in result.stdout
    ), result.stdout


# ---------------------------------------------------------------------------
# (6) reaper and overdue narrations cite acquired_at
# ---------------------------------------------------------------------------


def _wait_for(path: pathlib.Path, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while not path.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert path.exists(), f"expected {path} to appear within {timeout}s"


def test_orphan_holder_narration_cites_acquired_at(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "dead.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ready, done = tmp_path / "ready", tmp_path / "done"
    acquired_at = _iso_ago(300)
    # Flock the file for real (so the acquirer is contended) under a record
    # naming a pid that is not running.
    lock_file.write_text(
        f"holder=ghost\npid=99999999\nop=old-op\ntask=OLD\nacquired_at={acquired_at}\n",
        encoding="utf-8",
    )
    holder = subprocess.Popen(
        ["bash", "-c", textwrap.dedent(f"""\
            exec {{fd}}<"{lock_file}"
            flock -n "$fd"
            touch "{ready}"
            while [[ ! -f "{done}" ]]; do sleep 0.05; done
        """)],
        cwd=str(_TEAM_DIR),
    )
    try:
        _wait_for(ready)
        result = run_bash(tmp_path, textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            fd2=""
            pgai_lock_acquire "{lock_file}" reader "$$" op TASK-2 fd2
            echo "RC=$?"
        """))
        # The flock is held under a dead recorded pid: deferred to, never
        # reaped, and the narration cites how long the record says it was held.
        assert "RC=1" in result.stdout, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert "lock.provenance.orphan_holder" in result.stderr
        assert f"(held 5m since {acquired_at})" in result.stderr, result.stderr
        assert "(age " not in result.stderr, result.stderr
    finally:
        done.touch()
        holder.wait(timeout=10)


def test_overdue_narration_cites_acquired_at_not_mtime(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "aged.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ready, done = tmp_path / "ready", tmp_path / "done"
    holder = subprocess.Popen(
        ["bash", "-c", textwrap.dedent(f"""\
            source {_LOCK_PROVENANCE_SH}
            fd=""
            pgai_lock_acquire "{lock_file}" writer "$$" real-op TASK-AGED fd
            touch -d "-{_FLOOR_SECONDS + 5} seconds" "{lock_file}"
            touch "{ready}"
            while [[ ! -f "{done}" ]]; do sleep 0.05; done
            pgai_lock_release "{lock_file}" "$fd"
        """)],
        cwd=str(_TEAM_DIR),
    )
    try:
        _wait_for(ready)
        acquired_at = _fields(lock_file.read_text(encoding="utf-8"))["acquired_at"]
        result = run_bash(
            tmp_path,
            textwrap.dedent(f"""\
                set +e
                source {_LOCK_PROVENANCE_SH}
                fd2=""
                pgai_lock_acquire "{lock_file}" reader "$$" other-op TASK-2 fd2
                echo "RC=$?"
            """),
            extra_env={"KANBAN_ROOT": "", **_FLOOR_ENV},
        )
        assert "RC=1" in result.stdout, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert "lock.provenance.holder_overdue" in result.stderr, result.stderr
        assert f"since {acquired_at})" in result.stderr, result.stderr
        assert "last refreshed" not in result.stderr, result.stderr
    finally:
        done.touch()
        holder.wait(timeout=10)
