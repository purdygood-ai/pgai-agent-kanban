"""
test_sentinel_leak_check_bash.py
=================================
Behavioral unit tests for team/scripts/lib/sentinel_leak_check.sh -- the
post-suite check the gated runners run to prove a test run left the live
kanban root's HALT / HALT-AFTER / DEFER-LOW-BUGS set exactly as it found it.

Every test builds its own throwaway "live root" under tmp_path and drives
the two functions through the bash harness; the prevention is exercised,
not reasoned about: a seeded leak makes the check return 1 naming the
path, a clean root returns 0, and a sentinel that vanished is reported too.
"""

from __future__ import annotations

import pathlib

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/sentinel_leak_check.sh"


def _root_with_project(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "live_root"
    (root / "projects" / "alpha").mkdir(parents=True)
    (root / "projects" / "beta").mkdir(parents=True)
    return root


def _snapshot_then_check(tmp_path: pathlib.Path, root: pathlib.Path, between: str):
    """Snapshot *root*, run the bash snippet *between*, then run the check
    against that snapshot. Returns the check's BashResult."""
    snap = tmp_path / "presuite.snap"
    return run_bash(
        tmp_path,
        f"unset PGAI_AGENT_KANBAN_ROOT_PATH KANBAN_ROOT; source {_LIB} && "
        f"pgai_sentinel_snapshot '{root}' > '{snap}' && {between}; "
        f"pgai_sentinel_leak_check '{root}' '{snap}'",
    )


def test_snapshot_lists_root_and_project_sentinels_with_a_digest(tmp_path: pathlib.Path) -> None:
    root = _root_with_project(tmp_path)
    (root / "HALT").write_text("", encoding="utf-8")
    (root / "projects" / "alpha" / "HALT-AFTER").write_text("rc:v1.2.3\n", encoding="utf-8")
    (root / "projects" / "beta" / "DEFER-LOW-BUGS").write_text("until=v9.0.0\n", encoding="utf-8")
    # A directory named HALT is not a sentinel and must not be listed.
    (root / "projects" / "beta" / "HALT").mkdir()

    result = run_bash(tmp_path, f"source {_LIB} && pgai_sentinel_snapshot '{root}'")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.rstrip("\n").split("\n")
    paths = [line.split("\t")[0] for line in lines]
    assert paths == sorted(paths)
    assert set(paths) == {
        str(root / "HALT"),
        str(root / "projects" / "alpha" / "HALT-AFTER"),
        str(root / "projects" / "beta" / "DEFER-LOW-BUGS"),
    }
    for line in lines:
        digest = line.split("\t")[1]
        assert len(digest) == 64, f"expected a sha256 digest column: {line!r}"


def test_clean_root_passes_and_says_so(tmp_path: pathlib.Path) -> None:
    root = _root_with_project(tmp_path)
    (root / "projects" / "alpha" / "HALT").write_text("", encoding="utf-8")  # pre-existing, untouched
    result = _snapshot_then_check(tmp_path, root, "true")
    assert result.returncode == 0, result.stderr
    assert "unchanged by the suite" in result.stdout


def test_a_leaked_project_halt_after_fails_the_check_naming_the_path(tmp_path: pathlib.Path) -> None:
    """The prevention, exercised: a sentinel armed on the live root during
    the suite is reported as ADDED and the check returns non-zero."""
    root = _root_with_project(tmp_path)
    leaked = root / "projects" / "alpha" / "HALT-AFTER"
    result = _snapshot_then_check(tmp_path, root, f"printf 'rc:v9.9.9\\n' > '{leaked}'")
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"[pgai-sentinel] ADDED:   {leaked}" in result.stderr
    assert "1 sentinel difference(s)" in result.stderr
    # Report-only: the leaked file is still there for the operator to inspect.
    assert leaked.is_file()


def test_a_leaked_root_halt_and_a_campaign_file_are_both_reported(tmp_path: pathlib.Path) -> None:
    root = _root_with_project(tmp_path)
    halt = root / "HALT"
    campaign = root / "projects" / "beta" / "DEFER-LOW-BUGS"
    result = _snapshot_then_check(
        tmp_path, root, f": > '{halt}' && printf 'until=v9.0.0\\n' > '{campaign}'"
    )
    assert result.returncode == 1
    assert f"ADDED:   {halt}" in result.stderr
    assert f"ADDED:   {campaign}" in result.stderr
    assert "2 sentinel difference(s)" in result.stderr


def test_a_changed_sentinel_is_reported_as_changed(tmp_path: pathlib.Path) -> None:
    root = _root_with_project(tmp_path)
    sentinel = root / "projects" / "alpha" / "HALT-AFTER"
    sentinel.write_text("rc\n", encoding="utf-8")
    result = _snapshot_then_check(tmp_path, root, f"printf 'rc:v2.0.0\\n' > '{sentinel}'")
    assert result.returncode == 1
    assert f"CHANGED: {sentinel}" in result.stderr


def test_a_removed_sentinel_is_reported_as_removed(tmp_path: pathlib.Path) -> None:
    """An unhalted lane is a live-root mutation too: a sentinel present
    before the suite and gone after is a finding, not a pass."""
    root = _root_with_project(tmp_path)
    sentinel = root / "projects" / "alpha" / "HALT"
    sentinel.write_text("", encoding="utf-8")
    result = _snapshot_then_check(tmp_path, root, f"rm -f '{sentinel}'")
    assert result.returncode == 1
    assert f"REMOVED: {sentinel}" in result.stderr


def test_without_a_snapshot_every_present_sentinel_reads_as_added(tmp_path: pathlib.Path) -> None:
    root = _root_with_project(tmp_path)
    (root / "HALT").write_text("", encoding="utf-8")
    result = run_bash(tmp_path, f"source {_LIB} && pgai_sentinel_leak_check '{root}'")
    assert result.returncode == 1
    assert f"ADDED:   {root / 'HALT'}" in result.stderr


def test_missing_root_narrates_and_passes(tmp_path: pathlib.Path) -> None:
    """No live root to guard is not a failure: the check says so and
    returns 0, so a runner with the root variables unset still completes."""
    result = run_bash(
        tmp_path,
        f"unset PGAI_AGENT_KANBAN_ROOT_PATH KANBAN_ROOT; source {_LIB} && pgai_sentinel_leak_check",
    )
    assert result.returncode == 0, result.stderr
    assert "no live kanban root to guard" in result.stderr
    absent = run_bash(tmp_path, f"source {_LIB} && pgai_sentinel_leak_check '{tmp_path / 'nope'}'")
    assert absent.returncode == 0


def test_root_falls_back_to_the_kanban_root_environment_variable(tmp_path: pathlib.Path) -> None:
    root = _root_with_project(tmp_path)
    (root / "projects" / "alpha" / "HALT").write_text("", encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"export PGAI_AGENT_KANBAN_ROOT_PATH='{root}'; source {_LIB} && pgai_sentinel_snapshot",
    )
    assert result.returncode == 0
    assert result.stdout.startswith(str(root / "projects" / "alpha" / "HALT") + "\t")


def test_functions_are_safe_under_strict_mode(tmp_path: pathlib.Path) -> None:
    """The gated runners source this lib under set -euo pipefail; neither
    function may abort the caller on an empty snapshot or an empty root."""
    root = _root_with_project(tmp_path)
    snap = tmp_path / "presuite.snap"
    result = run_bash(
        tmp_path,
        f"set -euo pipefail; source {_LIB}; pgai_sentinel_snapshot '{root}' > '{snap}'; "
        f"pgai_sentinel_leak_check '{root}' '{snap}'; echo survived",
    )
    assert result.returncode == 0, result.stderr
    assert "survived" in result.stdout
