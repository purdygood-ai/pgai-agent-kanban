"""
test_deep_verify_scheduled_dispatch.py
========================================
End-to-end integration tests for team/scripts/deep-verify.sh --scheduled --
the DEEP-VERIFY tier's weekly, off-hours dispatch mode.

These tests invoke the real script via subprocess against a temporary
kanban root (PGAI_AGENT_KANBAN_ROOT_PATH redirected to tmp_path). No test
invokes a real container build: the cold lane is stubbed via the
PGAI_DEEP_VERIFY_COLD_LANE_CMD seam deep-verify.sh's own header documents.
The clock is injected via PGAI_DEEP_VERIFY_NOW (also documented in the
script's own header) rather than depending on the wall-clock date of the
test run.

Coverage:
  - A fixture project configured deep_verify = weekly whose ledger records
    a run eight days ago: the scheduled invocation runs the lane and
    writes a new record.
  - The same fixture with a record from two days ago: the scheduled
    invocation exits 0, invokes no lane, writes no new record, and logs
    one line naming the project and when it is next due.
  - A fixture project configured deep_verify = off never runs the lane on
    the scheduled path regardless of ledger age, and a fixture with an
    unrecognized value refuses dual-channel and runs nothing.
  - A fixture project that has never been deep-verified is treated as due
    on its first scheduled invocation.
  - --scheduled with no --project fans out over every active registered
    project from a single invocation.

Test names describe behaviour under test, never bug/task IDs (team/roles/TESTER.md,
Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from tests.fixtures.scrubbed_subprocess_env import scrub_agent_scoped_env

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's own location.
# test file:  team/tests/integration/test_deep_verify_scheduled_dispatch.py
# team/:      .../team
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_DEEP_VERIFY_SCRIPT = _SCRIPTS_DIR / "deep-verify.sh"

_STUB_GREEN = """\
#!/usr/bin/env bash
echo "[cold-build] [docker] [claude-code] Image built successfully: tag:test"
echo "Cold-build lane PASSED"
exit 0
"""

# A fixed "now" that always falls inside the shipped default off-hours
# window (03:00-03:59 UTC) -- every test below injects this (or an offset
# of it) so no assertion depends on the wall-clock date the suite runs on.
_NOW_IN_WINDOW = "2026-09-10T03:15:00+00:00"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_kanban_root(tmp_path: pathlib.Path, *, projects: dict) -> pathlib.Path:
    """projects: {name: {"deep_verify": "weekly"|"off"|None|<raw>, "priority": int}}"""
    root = tmp_path / "kanban_root"
    (root / "projects").mkdir(parents=True, exist_ok=True)

    cfg_lines = []
    for name, opts in projects.items():
        proj_dir = root / "projects" / name
        proj_dir.mkdir(parents=True, exist_ok=True)
        cfg_lines.append(f"[project:{name}]")
        cfg_lines.append(f"priority = {opts.get('priority', 1)}")
        cfg_lines.append("")

        deep_verify = opts.get("deep_verify")
        if deep_verify is not None:
            (proj_dir / "project.cfg").write_text(
                f"[project]\nproject_name = {name}\n\n[release]\ndeep_verify = {deep_verify}\n",
                encoding="utf-8",
            )

        ledger_last_run = opts.get("ledger_last_run")
        if ledger_last_run is not None:
            (proj_dir / "deep-verify-ledger.md").write_text(
                f"# Deep-Verify Ledger — {name}\n\n"
                f"## Deep-Verify Run: {ledger_last_run} — {name} — v1.0.0\n"
                "- Commit: abc1234\n"
                "- Verdict: built\n"
                "- Detail: docker/claude-code: built\n",
                encoding="utf-8",
            )

    (root / "projects.cfg").write_text("\n".join(cfg_lines), encoding="utf-8")
    return root


def _write_stub(tmp_path: pathlib.Path) -> pathlib.Path:
    stub = tmp_path / "stub_green.sh"
    stub.write_text(_STUB_GREEN, encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _run_scheduled(
    kanban_root: pathlib.Path,
    stub_lane: pathlib.Path,
    *,
    project: str | None = None,
    now: str = _NOW_IN_WINDOW,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_DEEP_VERIFY_COLD_LANE_CMD"] = str(stub_lane)
    # The stubbed lane stands in for the container tier, which runs only
    # with operator authorization and never inside an agent wake.
    scrub_agent_scoped_env(env)
    env["PGAI_CONTAINER_TIER"] = "1"
    env["PGAI_DEEP_VERIFY_NOW"] = now
    env.pop("PGAI_PROJECT_NAME", None)

    # An operator's scheduled entry names the images it builds; without a
    # selection a due run lists the images and runs nothing.
    args = ["bash", str(_DEEP_VERIFY_SCRIPT), "--scheduled", "--image", "all"]
    if project is not None:
        args += ["--project", project]

    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)


def _ledger_path(kanban_root: pathlib.Path, project: str) -> pathlib.Path:
    return kanban_root / "projects" / project / "deep-verify-ledger.md"


def _record_count(kanban_root: pathlib.Path, project: str) -> int:
    ledger = _ledger_path(kanban_root, project)
    if not ledger.exists():
        return 0
    return ledger.read_text(encoding="utf-8").count("## Deep-Verify Run:")


# ---------------------------------------------------------------------------
# Due: ledger record eight days old
# ---------------------------------------------------------------------------


def test_ledger_eight_days_old_is_due_and_writes_a_new_record(
    tmp_path: pathlib.Path,
) -> None:
    root = _make_kanban_root(
        tmp_path,
        projects={
            "demo": {
                "deep_verify": "weekly",
                "ledger_last_run": "2026-09-02T03:15:00+00:00",  # 8 days before _NOW_IN_WINDOW
            }
        },
    )
    stub = _write_stub(tmp_path)

    assert _record_count(root, "demo") == 1
    result = _run_scheduled(root, stub, project="demo")

    assert result.returncode == 0, result.stderr
    assert "running now" in result.stdout
    assert _record_count(root, "demo") == 2
    ledger_text = _ledger_path(root, "demo").read_text(encoding="utf-8")
    assert "- Verdict: built" in ledger_text


# ---------------------------------------------------------------------------
# Not due: ledger record two days old
# ---------------------------------------------------------------------------


def test_ledger_two_days_old_is_not_due_and_costs_nothing(
    tmp_path: pathlib.Path,
) -> None:
    root = _make_kanban_root(
        tmp_path,
        projects={
            "demo": {
                "deep_verify": "weekly",
                "ledger_last_run": "2026-09-08T03:15:00+00:00",  # 2 days before _NOW_IN_WINDOW
            }
        },
    )
    stub = _write_stub(tmp_path)

    result = _run_scheduled(root, stub, project="demo")

    assert result.returncode == 0, result.stderr
    assert _record_count(root, "demo") == 1  # unchanged -- no new record
    # Exactly one narration line naming the project and when it next comes due.
    narration_lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(narration_lines) == 1
    assert "demo" in narration_lines[0]
    assert "next due" in narration_lines[0]


# ---------------------------------------------------------------------------
# Never run before: due on first scheduled invocation
# ---------------------------------------------------------------------------


def test_never_verified_project_is_due_on_first_scheduled_invocation(
    tmp_path: pathlib.Path,
) -> None:
    root = _make_kanban_root(tmp_path, projects={"demo": {"deep_verify": "weekly"}})
    stub = _write_stub(tmp_path)

    assert not _ledger_path(root, "demo").exists()
    result = _run_scheduled(root, stub, project="demo")

    assert result.returncode == 0, result.stderr
    assert "never deep-verified before" in result.stdout
    assert _record_count(root, "demo") == 1


# ---------------------------------------------------------------------------
# Legacy boolean aliases: 'true' reaches the due-evaluation path exactly
# like 'weekly', 'false' skips exactly like 'off' -- no
# deep_verify.invalid_schedule_mode refusal for either.
# ---------------------------------------------------------------------------


def test_true_alias_reaches_due_evaluation_like_weekly(tmp_path: pathlib.Path) -> None:
    root = _make_kanban_root(tmp_path, projects={"demo": {"deep_verify": "true"}})
    stub = _write_stub(tmp_path)

    assert not _ledger_path(root, "demo").exists()
    result = _run_scheduled(root, stub, project="demo")

    assert result.returncode == 0, result.stderr
    assert "deep_verify.invalid_schedule_mode" not in result.stderr
    assert "never deep-verified before" in result.stdout
    assert _record_count(root, "demo") == 1


def test_false_alias_skips_the_lane_like_off(tmp_path: pathlib.Path) -> None:
    root = _make_kanban_root(
        tmp_path,
        projects={
            "demo": {
                "deep_verify": "false",
                "ledger_last_run": "2020-01-01T00:00:00+00:00",  # ancient
            }
        },
    )
    stub = _write_stub(tmp_path)

    result = _run_scheduled(root, stub, project="demo")

    assert result.returncode == 0, result.stderr
    assert "deep_verify.invalid_schedule_mode" not in result.stderr
    assert "deep_verify=off" in result.stdout
    assert _record_count(root, "demo") == 1  # unchanged


# ---------------------------------------------------------------------------
# deep_verify = off: never runs regardless of ledger age
# ---------------------------------------------------------------------------


def test_off_mode_never_runs_regardless_of_stale_ledger(tmp_path: pathlib.Path) -> None:
    root = _make_kanban_root(
        tmp_path,
        projects={
            "demo": {
                "deep_verify": "off",
                "ledger_last_run": "2020-01-01T00:00:00+00:00",  # ancient
            }
        },
    )
    stub = _write_stub(tmp_path)

    result = _run_scheduled(root, stub, project="demo")

    assert result.returncode == 0, result.stderr
    assert "deep_verify=off" in result.stdout
    assert _record_count(root, "demo") == 1  # unchanged


# ---------------------------------------------------------------------------
# Unrecognized deep_verify value: dual-channel refusal, runs nothing
# ---------------------------------------------------------------------------


def test_unrecognized_deep_verify_value_refuses_dual_channel_and_runs_nothing(
    tmp_path: pathlib.Path,
) -> None:
    root = _make_kanban_root(
        tmp_path,
        projects={
            "demo": {
                "deep_verify": "sometimes",
                "ledger_last_run": "2020-01-01T00:00:00+00:00",
            }
        },
    )
    stub = _write_stub(tmp_path)

    result = _run_scheduled(root, stub, project="demo")

    assert result.returncode == 1
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr
    assert "sometimes" in result.stderr
    assert _record_count(root, "demo") == 1  # unchanged -- no new record written


# ---------------------------------------------------------------------------
# Fan-out: --scheduled with no --project covers every active project
# ---------------------------------------------------------------------------


def test_fanout_with_no_project_evaluates_every_active_project(
    tmp_path: pathlib.Path,
) -> None:
    root = _make_kanban_root(
        tmp_path,
        projects={
            "alpha": {"deep_verify": "weekly"},  # never run -> due
            "beta": {"deep_verify": "off", "ledger_last_run": "2020-01-01T00:00:00+00:00"},
        },
    )
    stub = _write_stub(tmp_path)

    result = _run_scheduled(root, stub, project=None)

    assert result.returncode == 0, result.stderr
    assert _record_count(root, "alpha") == 1  # never run before -> ran, one new record
    assert _record_count(root, "beta") == 1  # pre-seeded record, off -> unchanged (no new record)
    assert "alpha" in result.stdout
    assert "beta" in result.stdout


def test_fanout_continues_past_one_projects_invalid_config(
    tmp_path: pathlib.Path,
) -> None:
    """A bad config in one project must not hide the fan-out's other projects --
    exit code reflects the failure, but every project is still evaluated."""
    root = _make_kanban_root(
        tmp_path,
        projects={
            "good": {"deep_verify": "weekly"},  # never run -> due
            "bad": {"deep_verify": "not-a-real-mode"},
        },
    )
    stub = _write_stub(tmp_path)

    result = _run_scheduled(root, stub, project=None)

    assert result.returncode == 1  # non-zero: at least one project failed
    assert _record_count(root, "good") == 1  # still ran despite bad's refusal
