"""
test_why_stuck_lock_verdict.py
===============================
Behavioral tests for the STUCK-LOCK verdict (team/pgai_agent_kanban/ops/verdict.py's
_find_stuck_lock and its wiring into resolve_verdicts) and
its violation-marked rendering in team/scripts/why.sh --details' locks
section (team/scripts/lib/why_details.sh).

Acceptance criteria exercised:

  (1) Queued work plus a lock older than 3 wake periods (the shipped
      default for kanban.cfg [locks] stale_lock_reap_after_wake_periods)
      yields verdict STUCK-LOCK with the release-locks remedy printed
      inline.
  (2) Queued work plus an unprovenanced lock of any age yields STUCK-LOCK,
      and --details renders that lock with the violation marker.
  (3) Queued work with no lock, and an idle chain (no queued work) with a
      live-holder lock, both keep their current verdict (IDLE) unchanged.
  (4) No input combination produces IDLE with remedy "none needed" while a
      stale or unprovenanced lock is displayed -- a fresh, well-provenanced,
      live-holder lock alongside queued work stays IDLE with "none needed"
      (the lock is healthy, not stale), proving STUCK-LOCK does not
      over-trigger.
  (5) The release-locks remedy names --force only when the recorded holder
      pid is verifiably alive (release-locks.sh itself refuses a "held"
      release without --force; a dead-pid or unprovenanced lock does not
      need it).
  (6) The aged-out cause names the mtime age as the time since the lock
      was last refreshed -- never as held time -- and states the held time
      from the record's acquired_at beside it, or "acquired_at not
      recorded (pre-upgrade lock)" for a record without one.

Tests invoke why.sh via subprocess, exercising the real production shell
code rather than a reimplementation of it, matching the pattern in
test_why_verdict_engine.py and test_why_details_pane.py.

Naming: function names describe behavior under test, never bug IDs or task
IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import datetime
import os
import pathlib
import re
import subprocess

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WHY_SH = _TEAM_DIR / "scripts" / "why.sh"

# One wake period (team/scripts/lib/lock_provenance.sh's
# _PGAI_LOCK_WAKE_PERIOD_SECONDS) times the shipped default reap threshold
# (3 periods, kanban.cfg_example [locks] stale_lock_reap_after_wake_periods)
# -- duplicated here (not sourced), matching this file's sibling test
# files, so a change to either production constant fails this test loud
# rather than silently tracking a moving target.
_WAKE_PERIOD_SECONDS = 120
_STALE_LOCK_THRESHOLD_SECONDS = 3 * _WAKE_PERIOD_SECONDS

# The effective age threshold is max(wake-periods product,
# max_runtime_seconds + max_task_seconds + kill_grace_seconds + margin) --
# see team/scripts/lib/lock_provenance.sh's pgai_lock_stale_age_threshold_
# seconds. This suite's fixtures exercise the wake-periods pathway these
# acceptance criteria describe, so _env() pins MAX_RUNTIME_SECONDS/
# MAX_TASK_SECONDS/KILL_GRACE_SECONDS to 0 -- collapsing the chain-budget floor to just its
# fixed 120s margin, well under _STALE_LOCK_THRESHOLD_SECONDS above -- so
# the wake-periods product remains the binding threshold regardless of
# what the calling process's own environment (or kanban.cfg, absent from
# every synthetic root this suite writes) happens to carry.
_STUCK_LOCK_EXIT_CODE = 6
_IDLE_EXIT_CODE = 2


def _env(kanban_root: pathlib.Path, now_epoch: int | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["MAX_RUNTIME_SECONDS"] = "0"
    env["MAX_TASK_SECONDS"] = "0"
    env["KILL_GRACE_SECONDS"] = "0"
    if now_epoch is not None:
        env["VERDICT_NOW_EPOCH"] = str(now_epoch)
    else:
        env.pop("VERDICT_NOW_EPOCH", None)
    return env


def _run(
    args: list[str],
    kanban_root: pathlib.Path,
    now_epoch: int | None = None,
    timeout: int = 20,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_WHY_SH), *args],
        capture_output=True,
        text=True,
        env=_env(kanban_root, now_epoch),
        timeout=timeout,
    )


def _write_projects_cfg(kanban_root: pathlib.Path, projects: list[tuple[str, int, bool]]) -> None:
    lines = ["# Format: INI-style"]
    for name, priority, enabled in projects:
        lines.append(f"[project:{name}]")
        lines.append(f"priority={priority}")
        lines.append(f"enabled={'true' if enabled else 'false'}")
        lines.append("")
    (kanban_root / "projects.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_project_dir(kanban_root: pathlib.Path, name: str) -> pathlib.Path:
    project_dir = kanban_root / "projects" / name
    (project_dir / "tasks").mkdir(parents=True, exist_ok=True)
    return project_dir


def _write_task(project_dir: pathlib.Path, task_id: str, state: str) -> pathlib.Path:
    task_dir = project_dir / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(f"## State\n{state}\n\n## Summary\nfixture task\n", encoding="utf-8")
    return status_file


def _write_lock(
    kanban_root: pathlib.Path,
    filename: str,
    *,
    holder: str = "",
    pid: str = "",
    op: str = "",
    task: str = "",
    mtime_epoch: int | None = None,
    provenance: bool = True,
    acquired_at: str = "",
) -> pathlib.Path:
    """Write a lock file under locks/. provenance=False writes an empty
    (unprovenanced) file, matching what release-locks.sh's own reader
    treats as "(no provenance recorded)"."""
    locks_dir = kanban_root / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_file = locks_dir / filename
    if provenance:
        record = f"holder={holder}\npid={pid}\nop={op}\ntask={task}\n"
        if acquired_at:
            record += f"acquired_at={acquired_at}\n"
        lock_file.write_text(record, encoding="utf-8")
    else:
        lock_file.write_text("", encoding="utf-8")
    if mtime_epoch is not None:
        os.utime(lock_file, (mtime_epoch, mtime_epoch))
    return lock_file


def _section(stdout: str, header: str) -> str:
    pattern = re.compile(rf"== {re.escape(header)} ==\n(.*?)(?=\n== |\Z)", re.S)
    m = pattern.search(stdout)
    assert m, f"section '{header}' not found in output:\n{stdout}"
    return m.group(1)


# ---------------------------------------------------------------------------
# (1) Aged-out lock: STUCK-LOCK with the release-locks remedy inline
# ---------------------------------------------------------------------------


def test_queued_work_with_lock_past_stale_threshold_yields_stuck_lock_with_release_locks_remedy(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BACKLOG")

    now = 2_000_000_000
    # Dead pid (a pid this test process almost certainly never used) keeps
    # the liveness verdict "stale", not "held" -- isolates the age
    # condition from the pid-liveness condition tested separately below.
    stale_mtime = now - (_STALE_LOCK_THRESHOLD_SECONDS + 60)
    _write_lock(
        tmp_path,
        "repo-wake-demo.lock",
        holder="coder",
        pid="999999999",
        op="wake-chain:demo",
        task="CODER-20260910-001-demo",
        mtime_epoch=stale_mtime,
    )

    result = _run(["--project", "demo"], tmp_path, now_epoch=now)

    assert result.returncode == _STUCK_LOCK_EXIT_CODE, (
        f"expected the STUCK-LOCK exit code, got {result.returncode}: {result.stdout!r}"
    )
    line = result.stdout.strip()
    assert line.startswith("demo: STUCK-LOCK")
    assert "remedy:" in line
    assert "team/scripts/release-locks.sh --release repo-wake-demo" in line


def test_stuck_lock_remedy_requires_force_only_when_recorded_pid_is_alive(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BACKLOG")

    now = 2_000_000_000
    stale_mtime = now - (_STALE_LOCK_THRESHOLD_SECONDS + 60)
    # The pytest process's own pid: guaranteed alive and owned by this
    # user for the duration of the subprocess call below (a fixed pid like
    # 1/init is unreliable here -- kill -0 on a pid owned by a different
    # user reads as "not running", the same ambiguity lock_provenance.sh's
    # own header documents for its liveness check).
    _write_lock(
        tmp_path,
        "repo-wake-demo.lock",
        holder="coder",
        pid=str(os.getpid()),
        op="wake-chain:demo",
        task="CODER-20260910-001-demo",
        mtime_epoch=stale_mtime,
    )

    result = _run(["--project", "demo"], tmp_path, now_epoch=now)

    assert result.returncode == _STUCK_LOCK_EXIT_CODE
    assert "--release repo-wake-demo --force" in result.stdout


def test_stuck_lock_remedy_omits_force_when_recorded_pid_is_dead(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BACKLOG")

    now = 2_000_000_000
    stale_mtime = now - (_STALE_LOCK_THRESHOLD_SECONDS + 60)
    _write_lock(
        tmp_path,
        "repo-wake-demo.lock",
        holder="coder",
        pid="999999999",
        op="wake-chain:demo",
        task="CODER-20260910-001-demo",
        mtime_epoch=stale_mtime,
    )

    result = _run(["--project", "demo"], tmp_path, now_epoch=now)

    assert result.returncode == _STUCK_LOCK_EXIT_CODE
    line = result.stdout.strip()
    assert "--release repo-wake-demo" in line
    assert "--force" not in line


# ---------------------------------------------------------------------------
# (2) Unprovenanced lock: STUCK-LOCK + violation marker under --details
# ---------------------------------------------------------------------------


def test_queued_work_with_unprovenanced_lock_of_any_age_yields_stuck_lock(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BACKLOG")

    now = 2_000_000_000
    # Fresh (age well under the stale threshold) but no provenance at all --
    # must still trigger STUCK-LOCK: missing provenance is its own
    # independent condition, regardless of age.
    fresh_mtime = now - 5
    _write_lock(tmp_path, "repo-wake-demo.lock", provenance=False, mtime_epoch=fresh_mtime)

    result = _run(["--project", "demo"], tmp_path, now_epoch=now)

    assert result.returncode == _STUCK_LOCK_EXIT_CODE, (
        f"expected the STUCK-LOCK exit code for an unprovenanced lock regardless of age, "
        f"got {result.returncode}: {result.stdout!r}"
    )
    assert "STUCK-LOCK" in result.stdout
    assert "VIOLATION" in result.stdout


def test_details_renders_unprovenanced_lock_with_violation_marker(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BACKLOG")
    _write_lock(tmp_path, "repo-wake-demo.lock", provenance=False)

    result = _run(["--project", "demo", "--details"], tmp_path)

    locks = _section(result.stdout, "locks")
    assert "(no provenance recorded)" in locks
    assert "VIOLATION" in locks


def test_details_does_not_mark_a_well_provenanced_lock_as_a_violation(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BACKLOG")
    _write_lock(
        tmp_path,
        "repo-wake-demo.lock",
        holder="coder",
        pid=str(os.getpid()),
        op="wake-chain:demo",
        task="CODER-20260910-001-demo",
    )

    result = _run(["--project", "demo", "--details"], tmp_path)

    locks = _section(result.stdout, "locks")
    assert "VIOLATION" not in locks


# ---------------------------------------------------------------------------
# (3) Unchanged verdicts: queued work with no lock; idle chain with a
#     live-holder lock
# ---------------------------------------------------------------------------


def test_queued_work_with_no_lock_stays_idle(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BACKLOG")

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == _IDLE_EXIT_CODE, f"expected IDLE, got {result.returncode}: {result.stdout!r}"
    line = result.stdout.strip()
    assert line.startswith("demo: IDLE")
    assert "queued" in line
    assert "none needed" in line


def test_idle_chain_with_no_queued_work_and_a_live_holder_lock_stays_idle(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "DONE")  # no non-terminal work at all
    # A live, recent, well-provenanced lock -- exactly the healthy holding
    # pattern that must never be treated as stuck when nothing is queued.
    _write_lock(
        tmp_path,
        "repo-wake-demo.lock",
        holder="coder",
        pid=str(os.getpid()),
        op="wake-chain:demo",
        task="none",
    )

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == _IDLE_EXIT_CODE, f"expected IDLE, got {result.returncode}: {result.stdout!r}"
    line = result.stdout.strip()
    assert line.startswith("demo: IDLE")
    assert "no non-terminal tasks" in line


# ---------------------------------------------------------------------------
# (4) No over-trigger: a fresh, well-provenanced, live-holder lock alongside
#     queued work never displaces the healthy "none needed" IDLE
# ---------------------------------------------------------------------------


def test_queued_work_with_a_fresh_live_holder_lock_stays_idle_with_none_needed(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BACKLOG")

    now = 2_000_000_000
    fresh_mtime = now - 5  # well under the stale threshold
    _write_lock(
        tmp_path,
        "repo-wake-demo.lock",
        holder="coder",
        pid=str(os.getpid()),
        op="wake-chain:demo",
        task="CODER-20260910-001-demo",
        mtime_epoch=fresh_mtime,
    )

    result = _run(["--project", "demo"], tmp_path, now_epoch=now)

    assert result.returncode == _IDLE_EXIT_CODE, (
        f"a fresh, well-provenanced, live-holder lock must never produce STUCK-LOCK, "
        f"got {result.returncode}: {result.stdout!r}"
    )
    line = result.stdout.strip()
    assert line.startswith("demo: IDLE")
    assert "none needed" in line


def test_no_lock_for_a_different_project_never_triggers_stuck_lock(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BACKLOG")

    now = 2_000_000_000
    stale_mtime = now - (_STALE_LOCK_THRESHOLD_SECONDS + 60)
    # Stale and unprovenanced, but named for a DIFFERENT project.
    _write_lock(tmp_path, "repo-wake-other.lock", provenance=False, mtime_epoch=stale_mtime)

    result = _run(["--project", "demo"], tmp_path, now_epoch=now)

    assert result.returncode == _IDLE_EXIT_CODE, (
        f"a stuck lock belonging to a different project must not affect this one's verdict, "
        f"got {result.returncode}: {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# --details exit code matches the plain verdict for STUCK-LOCK, exactly as
# it does for every other verdict (test_why_details_pane.py's own pattern)
# ---------------------------------------------------------------------------


def test_details_exit_code_matches_plain_verdict_for_stuck_lock_project(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BACKLOG")

    now = 2_000_000_000
    stale_mtime = now - (_STALE_LOCK_THRESHOLD_SECONDS + 60)
    _write_lock(
        tmp_path,
        "repo-wake-demo.lock",
        holder="coder",
        pid="999999999",
        op="wake-chain:demo",
        task="CODER-20260910-001-demo",
        mtime_epoch=stale_mtime,
    )

    plain_result = _run(["--project", "demo"], tmp_path, now_epoch=now)
    details_result = _run(["--project", "demo", "--details"], tmp_path, now_epoch=now)

    assert plain_result.returncode == _STUCK_LOCK_EXIT_CODE
    assert details_result.returncode == plain_result.returncode == _STUCK_LOCK_EXIT_CODE
    assert details_result.stdout.splitlines()[0] == plain_result.stdout.strip()


# ---------------------------------------------------------------------------
# (6) Aged-out cause: refresh age is never called held time
# ---------------------------------------------------------------------------


def test_aged_out_cause_names_refresh_age_and_held_time_separately(tmp_path: pathlib.Path) -> None:
    now = 2_000_000_000
    stale_age = _STALE_LOCK_THRESHOLD_SECONDS + 60  # 7m 0s since last refresh
    acquired_at = datetime.datetime.fromtimestamp(
        now - 3 * 3600, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    for label, recorded, held_phrase in (
        ("recorded", acquired_at, "(held 3h 0m)"),
        ("legacy", "", "(acquired_at not recorded (pre-upgrade lock))"),
    ):
        root = tmp_path / label
        root.mkdir()
        _write_projects_cfg(root, [("demo", 1, True)])
        project_dir = _make_project_dir(root, "demo")
        _write_task(project_dir, "CODER-20260910-001-demo", "BACKLOG")
        _write_lock(
            root,
            "repo-wake-demo.lock",
            holder="coder",
            pid="999999999",
            op="wake-chain:demo",
            task="CODER-20260910-001-demo",
            mtime_epoch=now - stale_age,
            acquired_at=recorded,
        )

        result = _run(["--project", "demo"], root, now_epoch=now)

        assert result.returncode == _STUCK_LOCK_EXIT_CODE, f"{label}: {result.stdout!r}"
        line = result.stdout.strip()
        assert f"has not been refreshed for 7m 0s {held_phrase}" in line, f"{label}: {line!r}"
        assert "has been held for" not in line, f"{label}: refresh age must not read as held time: {line!r}"
