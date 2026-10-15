"""
test_deep_verify_freshness_check.py
=====================================
End-to-end integration tests for team/scripts/deep-verify-freshness-check.sh
-- the pre-release promotion gate that refuses to promote a version unless
the project's deep-verify ledger holds a fresh green record for it.

These tests invoke the real script via subprocess against a temporary
kanban root (PGAI_AGENT_KANBAN_ROOT_PATH redirected to tmp_path, which
pytest_configure in team/tests/conftest.py already redirects under
$PGAI_AGENT_KANBAN_TEMP_DIR/tests). No test runs team/scripts/deep-verify.sh
or a real container build: each fixture writes its ledger record directly
as markdown text in the same append-only shape
pgai_agent_kanban.deep_verify.ledger.format_record() produces, and the
clock is injected via PGAI_DEEP_VERIFY_FRESHNESS_NOW (documented in the
script's own header) rather than depending on the wall-clock date of the
test run.

Coverage:
  - A green record for the target version, within the freshness window:
    exits 0 and prints the record's date, tree state, and verdict.
  - No record at all: exits non-zero, names the never-run case, and the
    remedy is the exact runnable command that produces a record.
  - A failed-to-build record and a could-not-attempt record refuse with
    two DIFFERENT messages; the could-not-attempt one says plainly that
    nothing was learned about whether the images build.
  - A green record outside the freshness policy (wrong version and/or too
    old) refuses, stating both the policy and the record's actual
    mismatch/age.
  - A malformed/unparseable ledger file refuses rather than passing, and
    does not crash.
  - Every refusal carries the dual-channel contract (error/message/remedy).
  - The check writes nothing: the ledger, release-state.md, and the task
    tree are byte-identical before and after every case above, and no
    HALT file is created.
  - --project unregistered refuses dual-channel.
  - --help prints usage and exits 0.

Test names describe behaviour under test, never bug/task IDs (team/roles/TESTER.md,
Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's own location.
# test file:  team/tests/integration/test_deep_verify_freshness_check.py
# team/:      .../team
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_FRESHNESS_CHECK_SCRIPT = _SCRIPTS_DIR / "deep-verify-freshness-check.sh"

_NOW = "2026-09-03T12:00:00+00:00"
_TARGET_VERSION = "v2.9.12"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_kanban_root(tmp_path: pathlib.Path, *, register_project: str | None = "demo") -> pathlib.Path:
    root = tmp_path / "kanban_root"
    (root / "projects").mkdir(parents=True, exist_ok=True)
    if register_project:
        (root / "projects.cfg").write_text(
            f"[project:{register_project}]\npriority = 1\n", encoding="utf-8"
        )
    else:
        (root / "projects.cfg").write_text("", encoding="utf-8")
    return root


def _ledger_path(kanban_root: pathlib.Path, project: str) -> pathlib.Path:
    return kanban_root / "projects" / project / "deep-verify-ledger.md"


def _release_state_path(kanban_root: pathlib.Path, project: str) -> pathlib.Path:
    return kanban_root / "projects" / project / "release-state.md"


def _write_ledger(
    kanban_root: pathlib.Path,
    project: str,
    *,
    timestamp: str,
    version: str,
    commit: str = "e240ee1",
    verdict: str = "built",
    detail: str = "docker/claude-code: built; podman/claude-code: built",
) -> pathlib.Path:
    """Write a single-record ledger directly, in the same shape
    pgai_agent_kanban.deep_verify.ledger.format_record()/append_record()
    produce -- a stub, rather than invoking team/scripts/deep-verify.sh
    (an operator-only container-tier harness) to build one."""
    ledger = _ledger_path(kanban_root, project)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        f"# Deep-Verify Ledger — {project}\n\n"
        "Append-only. One record per deep-verify run.\n\n"
        f"## Deep-Verify Run: {timestamp} — {project} — {version}\n"
        f"- Commit: {commit}\n"
        f"- Verdict: {verdict}\n"
        f"- Detail: {detail}\n",
        encoding="utf-8",
    )
    return ledger


def _write_malformed_ledger(kanban_root: pathlib.Path, project: str) -> pathlib.Path:
    ledger = _ledger_path(kanban_root, project)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("this is not a deep-verify ledger at all\njust prose\n", encoding="utf-8")
    return ledger


def _snapshot(kanban_root: pathlib.Path, project: str) -> dict:
    """Capture the write-nothing surface this gate must never touch."""
    ledger = _ledger_path(kanban_root, project)
    release_state = _release_state_path(kanban_root, project)
    return {
        "ledger": ledger.read_bytes() if ledger.exists() else None,
        "ledger_exists": ledger.exists(),
        "release_state": release_state.read_bytes() if release_state.exists() else None,
        "release_state_exists": release_state.exists(),
        "halt_exists": (kanban_root / "HALT").exists(),
        "project_halt_exists": (kanban_root / "projects" / project / "HALT").exists(),
    }


def _run_freshness_check(
    kanban_root: pathlib.Path,
    project: str | None,
    *,
    version: str | None = _TARGET_VERSION,
    now: str = _NOW,
    extra_args: list | None = None,
    window_days: str | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_DEEP_VERIFY_FRESHNESS_NOW"] = now
    if window_days is not None:
        env["PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS"] = window_days
    else:
        env.pop("PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS", None)

    args = ["bash", str(_FRESHNESS_CHECK_SCRIPT)]
    if project is not None:
        args += ["--project", project]
    if version is not None:
        args += ["--version", version]
    if extra_args:
        args += extra_args

    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_help_prints_usage_and_exits_zero(tmp_path: pathlib.Path) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    result = _run_freshness_check(kanban_root, None, version=None, extra_args=["--help"])
    assert result.returncode == 0
    assert "Usage: deep-verify-freshness-check.sh" in result.stdout


# ---------------------------------------------------------------------------
# Pass case: fresh green record for the target version
# ---------------------------------------------------------------------------


def test_fresh_green_record_for_target_version_exits_zero_and_prints_date_tree_state_and_verdict(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    _write_ledger(
        kanban_root,
        "demo",
        timestamp="2026-09-01T03:15:00+00:00",
        version=_TARGET_VERSION,
        commit="abc1234",
    )
    before = _snapshot(kanban_root, "demo")

    result = _run_freshness_check(kanban_root, "demo")

    assert result.returncode == 0, result.stderr
    assert "2026-09-01T03:15:00+00:00" in result.stdout  # date
    assert "abc1234" in result.stdout  # tree state (commit)
    assert _TARGET_VERSION in result.stdout  # tree state (version)
    assert "built" in result.stdout  # verdict
    assert _snapshot(kanban_root, "demo") == before


# ---------------------------------------------------------------------------
# Never-run case
# ---------------------------------------------------------------------------


def test_no_record_at_all_refuses_naming_never_run_case_with_runnable_remedy(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    (kanban_root / "projects" / "demo").mkdir(parents=True, exist_ok=True)
    before = _snapshot(kanban_root, "demo")

    result = _run_freshness_check(kanban_root, "demo")

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr
    assert "freshness_gate.no_record" in result.stderr
    assert "team/scripts/deep-verify.sh --project demo" in result.stderr
    assert _snapshot(kanban_root, "demo") == before


# ---------------------------------------------------------------------------
# Non-green verdicts -- two distinct refusals
# ---------------------------------------------------------------------------


def test_failed_to_build_record_refuses_naming_a_tree_defect(tmp_path: pathlib.Path) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    _write_ledger(
        kanban_root,
        "demo",
        timestamp="2026-09-01T03:15:00+00:00",
        version=_TARGET_VERSION,
        verdict="failed-to-build",
        detail="docker/claude-code: failed at step: RUN npm ci",
    )
    before = _snapshot(kanban_root, "demo")

    result = _run_freshness_check(kanban_root, "demo")

    assert result.returncode != 0
    assert "freshness_gate.failed_to_build" in result.stderr
    assert "RUN npm ci" in result.stderr
    assert _snapshot(kanban_root, "demo") == before


def test_could_not_attempt_record_refuses_saying_nothing_was_learned(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    _write_ledger(
        kanban_root,
        "demo",
        timestamp="2026-09-01T03:15:00+00:00",
        version=_TARGET_VERSION,
        verdict="could-not-attempt",
        detail="no container engine on the verifying host",
    )
    before = _snapshot(kanban_root, "demo")

    result = _run_freshness_check(kanban_root, "demo")

    assert result.returncode != 0
    assert "freshness_gate.could_not_attempt" in result.stderr
    assert "nothing was learned about whether the images build" in result.stderr
    assert _snapshot(kanban_root, "demo") == before


def test_failed_to_build_and_could_not_attempt_carry_different_messages(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root_a = _make_kanban_root(tmp_path / "a")
    _write_ledger(
        kanban_root_a, "demo", timestamp="2026-09-01T03:15:00+00:00",
        version=_TARGET_VERSION, verdict="failed-to-build",
    )
    result_a = _run_freshness_check(kanban_root_a, "demo")

    kanban_root_b = _make_kanban_root(tmp_path / "b")
    _write_ledger(
        kanban_root_b, "demo", timestamp="2026-09-01T03:15:00+00:00",
        version=_TARGET_VERSION, verdict="could-not-attempt",
    )
    result_b = _run_freshness_check(kanban_root_b, "demo")

    assert result_a.returncode != 0
    assert result_b.returncode != 0
    assert result_a.stderr != result_b.stderr


# ---------------------------------------------------------------------------
# Stale: green but outside freshness policy
# ---------------------------------------------------------------------------


def test_green_record_for_a_different_version_refuses_stating_policy_and_mismatch(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    _write_ledger(
        kanban_root,
        "demo",
        timestamp="2026-09-03T11:00:00+00:00",  # 1 hour old -- age is not the problem
        version="v2.9.11",
    )
    before = _snapshot(kanban_root, "demo")

    result = _run_freshness_check(kanban_root, "demo")

    assert result.returncode != 0
    assert "freshness_gate.stale_record" in result.stderr
    assert "v2.9.11" in result.stderr
    assert _TARGET_VERSION in result.stderr
    assert _snapshot(kanban_root, "demo") == before


def test_green_record_older_than_the_freshness_window_refuses_stating_the_age(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    _write_ledger(
        kanban_root,
        "demo",
        timestamp="2026-08-01T00:00:00+00:00",  # far older than 7 days before _NOW
        version=_TARGET_VERSION,
    )
    before = _snapshot(kanban_root, "demo")

    result = _run_freshness_check(kanban_root, "demo")

    assert result.returncode != 0
    assert "freshness_gate.stale_record" in result.stderr
    assert "7-day" in result.stderr
    assert _snapshot(kanban_root, "demo") == before


def test_freshness_window_override_widens_what_counts_as_fresh(tmp_path: pathlib.Path) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    _write_ledger(
        kanban_root,
        "demo",
        timestamp="2026-08-25T00:00:00+00:00",  # 9 days before _NOW
        version=_TARGET_VERSION,
    )

    default_window = _run_freshness_check(kanban_root, "demo")
    assert default_window.returncode != 0

    widened_window = _run_freshness_check(kanban_root, "demo", window_days="14")
    assert widened_window.returncode == 0, widened_window.stderr


# ---------------------------------------------------------------------------
# Malformed ledger
# ---------------------------------------------------------------------------


def test_malformed_ledger_refuses_instead_of_passing_and_does_not_crash(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    _write_malformed_ledger(kanban_root, "demo")
    before = _snapshot(kanban_root, "demo")

    result = _run_freshness_check(kanban_root, "demo")

    assert result.returncode != 0
    assert "freshness_gate.ledger_unreadable" in result.stderr
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr
    # No Python traceback -- a handled refusal, not a crash.
    assert "Traceback" not in result.stderr
    assert _snapshot(kanban_root, "demo") == before


# ---------------------------------------------------------------------------
# Unregistered project
# ---------------------------------------------------------------------------


def test_unregistered_project_refuses_dual_channel(tmp_path: pathlib.Path) -> None:
    kanban_root = _make_kanban_root(tmp_path, register_project=None)

    result = _run_freshness_check(kanban_root, "nonexistent-project")

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr
    assert "freshness_gate.unregistered_project" in result.stderr


# ---------------------------------------------------------------------------
# Usage-class refusals
# ---------------------------------------------------------------------------


def test_missing_project_flag_refuses(tmp_path: pathlib.Path) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    result = _run_freshness_check(kanban_root, None)
    assert result.returncode != 0
    assert "--project is required" in result.stderr


def test_missing_version_flag_refuses(tmp_path: pathlib.Path) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    result = _run_freshness_check(kanban_root, "demo", version=None)
    assert result.returncode != 0
    assert "--version is required" in result.stderr


def test_malformed_version_flag_refuses(tmp_path: pathlib.Path) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    result = _run_freshness_check(kanban_root, "demo", version="not-a-version")
    assert result.returncode != 0
    assert "not well-formed semver" in result.stderr
