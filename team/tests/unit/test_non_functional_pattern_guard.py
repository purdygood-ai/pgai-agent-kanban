"""
test_non_functional_pattern_guard.py
====================================
The release's Step 4c guard -- halt when the last three shipped releases
were all NON-FUNCTIONAL -- reads each release's Status the way the release
notes template writes it, counts only shipped releases, and can be passed
once the operator has acknowledged the window it halted on.

Coverage:
  - The shared reader (pgai_agent_kanban.cm.notes_status_section
    non_functional_pattern): templated notes that keep the guidance comment
    above a stamped NON-FUNCTIONAL value are detected; so are bold values;
    the active RC's own notes and any file still at PENDING-RELEASE are not
    shipped releases and never push the oldest shipped one out of the
    window; versions order by semver, not text; fewer than three shipped
    releases never detect.
  - Step 4c through the real cm_release_apply_ship_policy on a sandbox lane
    under ship_posture=matrix: templated NON-FUNCTIONAL notes plus the
    active RC's PENDING-RELEASE notes halt with the trigger-8 key and record
    the window; after the HALT is cleared a re-run over the same three
    releases does not halt; a newer NON-FUNCTIONAL release forms a new
    window that halts again.
  - The window record writes nothing under a decide-only run.

Every path is under pytest's tmp_path; the sandbox lane has no remote and a
throwaway kanban root (tests/fixtures/ship_policy_sandbox.py).
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from pgai_agent_kanban.cm.notes_status_section import non_functional_pattern
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.ship_policy_sandbox import KEY_T8, VERSION, build_lane, run_ship_policy
from tests.unit.shell_harness import run_bash

_TEMPLATED = (
    "# Release Notes: demo {version}\n\n## Status\n"
    "<!-- CM stamps this field at ship time; WRITER leaves PENDING-RELEASE here -->\n"
    "{status}\n\n## Summary\nText.\n"
)


def _write(notes_dir: pathlib.Path, version: str, status: str) -> None:
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / f"{version}.md").write_text(_TEMPLATED.format(version=version, status=status), encoding="utf-8")


def _diag(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


# ---------------------------------------------------------------------------
# The shared reader
# ---------------------------------------------------------------------------


def test_templated_notes_keeping_the_comment_line_are_detected(tmp_path):
    for version in ("v1.1.0", "v1.2.0", "v1.3.0"):
        _write(tmp_path, version, "NON-FUNCTIONAL")

    result = non_functional_pattern(tmp_path)

    assert result.detected is True, result.lines
    assert result.window == ["v1.1.0.md", "v1.2.0.md", "v1.3.0.md"]


def test_active_rc_pending_notes_do_not_push_a_shipped_release_out_of_the_window(tmp_path):
    for version in ("v1.1.0", "v1.2.0", "v1.3.0"):
        _write(tmp_path, version, "NON-FUNCTIONAL")
    _write(tmp_path, "v1.4.0", "PENDING-RELEASE")

    with_rc = non_functional_pattern(tmp_path, "v1.4.0")
    without_rc = non_functional_pattern(tmp_path)

    assert with_rc.detected is True, with_rc.lines
    assert without_rc.detected is True, "a file still at PENDING-RELEASE is not a shipped release"
    assert with_rc.window == ["v1.1.0.md", "v1.2.0.md", "v1.3.0.md"]


def test_the_active_rc_notes_are_skipped_even_when_already_stamped(tmp_path):
    for version in ("v1.1.0", "v1.2.0", "v1.3.0"):
        _write(tmp_path, version, "NON-FUNCTIONAL")
    _write(tmp_path, "v1.4.0", "FUNCTIONAL")

    assert non_functional_pattern(tmp_path, "v1.4.0").detected is True
    assert non_functional_pattern(tmp_path).detected is False


def test_bold_status_value_reads_as_its_token(tmp_path):
    for version in ("v1.1.0", "v1.2.0", "v1.3.0"):
        _write(tmp_path, version, "**NON-FUNCTIONAL**")

    assert non_functional_pattern(tmp_path).detected is True


def test_versions_order_by_semver_not_text(tmp_path):
    _write(tmp_path, "v1.9.0", "FUNCTIONAL")
    for version in ("v1.10.0", "v1.11.0", "v1.12.0"):
        _write(tmp_path, version, "NON-FUNCTIONAL")

    result = non_functional_pattern(tmp_path)

    assert result.detected is True, result.lines
    assert result.window == ["v1.10.0.md", "v1.11.0.md", "v1.12.0.md"]


def test_one_functional_release_in_the_window_is_not_the_pattern(tmp_path):
    _write(tmp_path, "v1.1.0", "NON-FUNCTIONAL")
    _write(tmp_path, "v1.2.0", "KNOWN-BUGS")
    _write(tmp_path, "v1.3.0", "NON-FUNCTIONAL")

    assert non_functional_pattern(tmp_path).detected is False


def test_fewer_than_three_shipped_releases_never_detect(tmp_path):
    _write(tmp_path, "v1.1.0", "NON-FUNCTIONAL")
    _write(tmp_path, "v1.2.0", "NON-FUNCTIONAL")
    _write(tmp_path, "v1.3.0", "PENDING-RELEASE")

    result = non_functional_pattern(tmp_path)

    assert result.detected is False
    assert result.window == []


# ---------------------------------------------------------------------------
# Step 4c through cm_release_apply_ship_policy
# ---------------------------------------------------------------------------


@pytest.fixture
def _git() -> None:
    require_binary("git", label="non-functional-pattern-guard")


def _commit_notes(lane, statuses: dict) -> None:
    notes_dir = lane.repo / "release-notes"
    for version, status in statuses.items():
        _write(notes_dir, version, status)
    for args in (("add", "-A"), ("commit", "-q", "-m", "docs: earlier release notes")):
        subprocess.run(["git", "-C", str(lane.repo), *args], check=True, capture_output=True, text=True)


def _window_file(lane) -> pathlib.Path:
    return lane.project_root / "release-state" / "non-functional-pattern-halted-window"


def test_step_4c_halts_once_per_window_and_again_on_a_new_window(tmp_path, _git):
    # The lane's RC branch carries the active RC's WRITER notes, still at
    # PENDING-RELEASE, beside three templated NON-FUNCTIONAL releases.
    lane = build_lane(tmp_path, "pass", "matrix", writer_notes=True)
    _commit_notes(lane, {"v1.0.0": "NON-FUNCTIONAL", "v1.1.0": "NON-FUNCTIONAL", "v1.2.0": "NON-FUNCTIONAL"})

    first = run_ship_policy(tmp_path, lane)

    assert first.returncode != 0, _diag(first)
    assert lane.halt_file.is_file(), _diag(first)
    assert KEY_T8 in lane.halt_file.read_text(encoding="utf-8")
    assert _window_file(lane).read_text(encoding="utf-8").strip() == "v1.0.0.md,v1.1.0.md,v1.2.0.md"

    # The operator clears the HALT; the same three releases now pass.
    lane.halt_file.unlink()
    second = run_ship_policy(tmp_path, lane)

    assert second.returncode == 0, _diag(second)
    assert not lane.halt_file.exists(), _diag(second)
    assert "Pattern already halted on this window (v1.0.0.md,v1.1.0.md,v1.2.0.md)" in second.stdout, _diag(second)

    # A newer NON-FUNCTIONAL release ships, forming a new window.
    _commit_notes(lane, {"v1.3.0": "NON-FUNCTIONAL"})
    third = run_ship_policy(tmp_path, lane)

    assert third.returncode != 0, _diag(third)
    assert lane.halt_file.is_file(), _diag(third)
    assert KEY_T8 in lane.halt_file.read_text(encoding="utf-8")
    assert _window_file(lane).read_text(encoding="utf-8").strip() == "v1.1.0.md,v1.2.0.md,v1.3.0.md"


def test_window_record_writes_nothing_under_a_decide_only_run(tmp_path):
    project_root = tmp_path / "project"
    script = (
        "source scripts/lib/pp_run_ops.sh && source scripts/lib/cm/release_ship_policy_report.sh && "
        f"_CM_PROJECT_ROOT='{project_root}' _CM_DECIDE_ONLY=true "
        "cm_record_non_functional_pattern_window v1.0.0.md,v1.1.0.md,v1.2.0.md && echo recorded-ok"
    )

    result = run_bash(tmp_path, script)

    assert "recorded-ok" in result.stdout, _diag(result)
    assert not (project_root / "release-state").exists()
