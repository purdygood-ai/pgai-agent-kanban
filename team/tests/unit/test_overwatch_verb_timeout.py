"""
test_overwatch_verb_timeout.py
==============================
Behavioral tests for the time limit OVERWATCH's resolution stage puts on
every operator verb script it runs (pgai_agent_kanban.overwatch.
resolution_stage): cancel-rc.sh, unwind-rc.sh and cm/release.sh, each
bounded by kanban.cfg ``[overwatch] verb_timeout_seconds``.

  - A verb script that sleeps past the limit comes back as a failed verb
    whose output ends with the reason code ``overwatch.verb-timeout``,
    within a bounded time, for each of the three runners -- and its whole
    process group, a background child included, is gone afterwards.
  - Driven through the Lane A playbook, a timed-out unwind-rc.sh is the
    drill's ordinary failed-step path: cancel-rc.sh is never started, the
    bundle document and ship-policy HALT stay, the errors name the timeout
    and the steps not run, and the stage goes on to write its action-log
    summary line.
  - The limit is read from kanban.cfg: absent resolves to the default; a
    value that is not a positive whole number resolves to the default with
    one warning.

Design:
  - Every test builds a throwaway kanban root under pytest's tmp_path (which
    the suite conftest anchors under $PGAI_AGENT_KANBAN_TEMP_DIR), points
    PGAI_AGENT_KANBAN_ROOT_PATH and KANBAN_ROOT at it, and clears
    PGAI_TASKS_DIR and PGAI_PROJECT_ROOT so no path resolves to a live
    install.
  - The verb scripts are real stub scripts inside that root, run for real.
    The sleeping stub records its own pid and its background child's pid so
    the test can confirm both are gone. The limit is 1 second; the stubs
    sleep at most 30 seconds and die on SIGTERM.
"""

from __future__ import annotations

import pathlib
import time

import pytest

from pgai_agent_kanban.lib.bounded_run import KILL_GRACE_SECONDS
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import resolution_stage
from pgai_agent_kanban.overwatch.fix_rewrite import REWRITE_BLOCK_HEADING
from pgai_agent_kanban.overwatch.resolution_matrix import LANE_A, LaneClassification

_PROJECT = "demo"
_VERSION = "v9.9.1"
_BUNDLE_KEY = f"{_VERSION}-bugfix-bundle-20990101"
_BLAMED_BUG = "BUG-0001-blamed-fix"

# Bound on one timed-out run: the 1s limit plus the SIGTERM grace.
_BOUND_SECONDS = 1 + KILL_GRACE_SECONDS

_SLEEPING_STUB = """#!/usr/bin/env bash
echo "started $*"
sleep 30 &
echo "$!" > "{pid_dir}/child.pid"
echo "$$" > "{pid_dir}/leader.pid"
sleep 30
"""

_RECORDING_STUB = """#!/usr/bin/env bash
echo "$*" >> "{pid_dir}/{name}.calls"
echo "{name} ok"
"""


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _write_script(path: pathlib.Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def kanban_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A throwaway kanban root holding one project halted on a CM ship-policy
    HALT with an active RC and a running bundle document, a kanban.cfg whose
    verb limit is 1 second, and recording stubs for every verb script."""
    root = tmp_path / "kanban_root"
    proj = root / "projects" / _PROJECT
    for sub in ("requirements", "bugs", "tasks/queues", "release-state"):
        (proj / sub).mkdir(parents=True)
    (root / "logs").mkdir(parents=True)
    (root / "pids").mkdir()

    (root / "kanban.cfg").write_text("[overwatch]\nverb_timeout_seconds = 1\n", encoding="utf-8")
    (proj / "project.cfg").write_text(f"[project]\nproject_name = {_PROJECT}\nbranch_prefix = ai_\n", encoding="utf-8")
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{_VERSION}\n\n"
        "## RC Opened At\n2099-01-01T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-20990101-001-open-rc\n",
        encoding="utf-8",
    )
    (proj / "HALT").write_text(
        "# HALT created by CM at 2099-01-01T00:00:00Z\n" "# Reason: TESTER systemic_risk=high\n",
        encoding="utf-8",
    )
    (proj / "requirements" / f"{_BUNDLE_KEY}.md").write_text(
        f"# Requirements: {_VERSION} bug bundle\n\n"
        "## Status\nrunning\n\n"
        f"## Target Version\n{_VERSION}\n\n"
        f"## Bundled Items\n\n- {_BLAMED_BUG}.md\n",
        encoding="utf-8",
    )
    (proj / "bugs" / f"{_BLAMED_BUG}.md").write_text(
        f"# Bug: {_BLAMED_BUG}\n\n## Status\nrunning\n\n## Fix\nsample\n\n"
        f"---\n\n{REWRITE_BLOCK_HEADING}\nsample rewrite\n",
        encoding="utf-8",
    )
    for rel, name in (
        ("scripts/cm/cancel-rc.sh", "cancel-rc"),
        ("scripts/unwind-rc.sh", "unwind-rc"),
        ("scripts/cm/release.sh", "release"),
    ):
        _write_script(root / rel, _RECORDING_STUB.format(pid_dir=root / "pids", name=name))

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    return root


def _make_sleeper(root: pathlib.Path, rel: str) -> None:
    """Replace the verb script at *rel* with one that sleeps past the limit."""
    _write_script(root / rel, _SLEEPING_STUB.format(pid_dir=root / "pids"))


def _alive(pid: int) -> bool:
    """True while *pid* is a live (non-zombie) process."""
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _assert_group_gone(root: pathlib.Path) -> None:
    """The sleeping stub and the background child it started are both gone."""
    pids = {name: int((root / "pids" / f"{name}.pid").read_text()) for name in ("leader", "child")}
    deadline = time.monotonic() + 2.0
    while any(_alive(pid) for pid in pids.values()) and time.monotonic() < deadline:
        time.sleep(0.05)
    survivors = {name: pid for name, pid in pids.items() if _alive(pid)}
    assert not survivors, f"processes from the verb's group still alive: {survivors}"


# ---------------------------------------------------------------------------
# Each runner: a timed-out verb is a failed verb with its own reason code
# ---------------------------------------------------------------------------


_RUNNERS = [
    pytest.param(
        "scripts/cm/cancel-rc.sh",
        lambda root: resolution_stage._run_cm_cancel_rc(str(root), _PROJECT, _VERSION),
        id="cancel-rc",
    ),
    pytest.param(
        "scripts/unwind-rc.sh",
        lambda root: resolution_stage._run_unwind_rc(str(root), _PROJECT, _VERSION),
        id="unwind-rc",
    ),
    pytest.param(
        "scripts/cm/release.sh",
        lambda root: resolution_stage._run_cm_release_force(str(root), _PROJECT, "declared intent"),
        id="release",
    ),
]


@pytest.mark.parametrize("script_rel, run", _RUNNERS)
def test_verb_past_the_limit_fails_with_the_timeout_reason_code(
    kanban_root: pathlib.Path, script_rel: str, run
) -> None:
    _make_sleeper(kanban_root, script_rel)

    started = time.monotonic()
    ok, output = run(kanban_root)
    elapsed = time.monotonic() - started

    assert ok is False, output
    assert elapsed < _BOUND_SECONDS, f"the verb held the stage for {elapsed:.1f}s:\n{output}"
    last_line = output.rstrip("\n").splitlines()[-1]
    assert last_line.startswith(f"{resolution_stage.VERB_TIMEOUT_REASON}: "), output
    assert "did not finish within 1s" in last_line and "verb_timeout_seconds" in last_line, output
    assert output.startswith("started "), "output read before the stop must be kept:\n" + output
    _assert_group_gone(kanban_root)


def test_verb_inside_the_limit_reports_its_own_result(kanban_root: pathlib.Path) -> None:
    ok, output = resolution_stage._run_cm_cancel_rc(str(kanban_root), _PROJECT, _VERSION)

    assert ok is True, output
    assert output == "cancel-rc ok\n"
    assert resolution_stage.VERB_TIMEOUT_REASON not in output


# ---------------------------------------------------------------------------
# Through the playbook: the drill fails at the step and the stage goes on
# ---------------------------------------------------------------------------


def test_timed_out_unwind_takes_the_failed_step_path_and_the_stage_continues(
    kanban_root: pathlib.Path,
) -> None:
    _make_sleeper(kanban_root, "scripts/unwind-rc.sh")
    proj = kanban_root / "projects" / _PROJECT
    bundle_before = (proj / "requirements" / f"{_BUNDLE_KEY}.md").read_bytes()
    halt_before = (proj / "HALT").read_bytes()

    outcome = resolution_stage._run_lane_a(
        ctx=OpsContext(kanban_root=str(kanban_root)),
        kanban_root=str(kanban_root),
        project_name=_PROJECT,
        project_root=str(proj),
        version=_VERSION,
        lane_result=LaneClassification(
            lane=LANE_A, reason="regression traced to the blamed fix", regression_bug_id=_BLAMED_BUG
        ),
        family={"bundle_file": str(proj / "requirements" / f"{_BUNDLE_KEY}.md"), "family_key": None},
        ship_halt_path=str(proj / "HALT"),
        evidence_refs=[],
        timestamp="2099-01-01T00:00:00Z",
    )

    unwind_error = next(e for e in outcome.action_errors if e.startswith("unwind-rc.sh "))
    assert "FAILED" in unwind_error and resolution_stage.VERB_TIMEOUT_REASON in unwind_error, outcome
    assert any("later steps not run because the RC unwind failed" in e for e in outcome.action_errors), outcome
    assert not (kanban_root / "pids" / "cancel-rc.calls").exists(), "cancel-rc must not run after a timed-out unwind"
    assert (proj / "requirements" / f"{_BUNDLE_KEY}.md").read_bytes() == bundle_before
    assert (proj / "HALT").read_bytes() == halt_before
    summary = (proj / "overwatch" / "actions.log").read_text(encoding="utf-8").splitlines()[-1]
    assert "RC unwind" in summary, summary
    _assert_group_gone(kanban_root)


# ---------------------------------------------------------------------------
# The configured limit
# ---------------------------------------------------------------------------


def test_absent_key_resolves_to_the_default(tmp_path: pathlib.Path) -> None:
    (tmp_path / "kanban.cfg").write_text("[overwatch]\nblock_triage = true\n", encoding="utf-8")

    assert resolution_stage.resolve_verb_timeout_seconds(str(tmp_path)) == (
        resolution_stage.DEFAULT_VERB_TIMEOUT_SECONDS
    )


def test_configured_value_is_used(tmp_path: pathlib.Path) -> None:
    (tmp_path / "kanban.cfg").write_text("[overwatch]\nverb_timeout_seconds = 120\n", encoding="utf-8")

    assert resolution_stage.resolve_verb_timeout_seconds(str(tmp_path)) == 120


@pytest.mark.parametrize("raw", ["0", "-5", "10m", "soon"])
def test_unusable_value_warns_and_resolves_to_the_default(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture, raw: str
) -> None:
    (tmp_path / "kanban.cfg").write_text(f"[overwatch]\nverb_timeout_seconds = {raw}\n", encoding="utf-8")

    seconds = resolution_stage.resolve_verb_timeout_seconds(str(tmp_path))

    assert seconds == resolution_stage.DEFAULT_VERB_TIMEOUT_SECONDS
    err = capsys.readouterr().err
    assert "[overwatch] verb_timeout_seconds" in err and repr(raw) in err, err
