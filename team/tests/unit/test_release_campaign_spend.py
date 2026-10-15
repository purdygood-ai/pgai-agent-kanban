"""
test_release_campaign_spend.py
===============================
Unit tests for team/scripts/lib/cm/release_campaign_spend.sh — the ship-time
step that keeps the second half of a DEFER-LOW-BUGS campaign's promise:
remove the campaign file (and record why) once a shipped release is semver
>= the file's own until= target.

Acceptance criteria exercised:

  (1) A project-scoped campaign file carrying until=v2.14.0, shipping
      v2.14.0, removes the file and appends a "## Campaign Spend Event"
      audit entry to release-state.md naming the shipped version and the
      satisfied target.
  (2) Shipping v2.14.3 against the same file also removes it — the
      latching semver >= comparison, not equality.
  (3) Shipping v2.13.9 against the same file leaves it in place and prints
      a narration line naming the campaign as still in force.
  (4) After a spend, the deferral-posture resolver (defer_posture.sh)
      reports the posture resolving from a cfg tier with no until target —
      exercised end to end against the real resolver.
  (5) A root-scoped campaign file is never touched: the function only ever
      reads/removes <project_root>/DEFER-LOW-BUGS, never a
      $KANBAN_ROOT/DEFER-LOW-BUGS sitting alongside it.
  (6) A malformed campaign file (no parseable until=, or a non-semver
      until= value) is left in place and treated as a warning, never a
      crash — the function still returns 0. Same for a release-state.md
      that does not exist at audit-append time.

Tests invoke release_campaign_spend.sh via subprocess (bash -c "source ...
&& ..."), matching the pattern in test_defer_posture_resolver.py, so the
fixtures exercise the real production shell code rather than a
reimplementation of it.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import pathlib
import subprocess

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_CAMPAIGN_SPEND = _TEAM_DIR / "scripts" / "lib" / "cm" / "release_campaign_spend.sh"
_DEFER_POSTURE = _TEAM_DIR / "scripts" / "lib" / "defer_posture.sh"


def _run_bash(script: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _spend(
    project_name: str,
    shipped_version: str,
    project_root: pathlib.Path,
    release_state: pathlib.Path,
) -> subprocess.CompletedProcess:
    script = (
        f"source {str(_CAMPAIGN_SPEND)!r} && "
        f"cm_release_spend_campaign_file {project_name!r} {shipped_version!r} "
        f"{str(project_root)!r} {str(release_state)!r}"
    )
    return _run_bash(script)


def _write_campaign_file(path: pathlib.Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _write_release_state(path: pathlib.Path, last_released: str = "none") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Release State\n\n"
        "## Active RC\nnone\n\n"
        "## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n"
        f"## Last Released\n{last_released}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# (1) Exact-equality shipped version spends the file and records the audit
#     entry.
# ---------------------------------------------------------------------------


def test_shipping_the_exact_until_target_spends_the_campaign_file(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "projects" / "demo"
    campaign_file = project_root / "DEFER-LOW-BUGS"
    release_state = project_root / "release-state.md"
    _write_campaign_file(campaign_file, "until=v2.14.0\n")
    _write_release_state(release_state, last_released="v2.14.0")

    result = _spend("demo", "v2.14.0", project_root, release_state)

    assert result.returncode == 0, f"spend step failed: {result.stderr!r}"
    assert not campaign_file.exists(), "campaign file should be removed once its until target is satisfied"

    audit = release_state.read_text(encoding="utf-8")
    assert "## Campaign Spend Event" in audit
    assert "v2.14.0" in audit  # shipped version
    assert "Until target satisfied: v2.14.0" in audit


# ---------------------------------------------------------------------------
# (2) A later shipped version also spends the file — semver >=, not
#     equality.
# ---------------------------------------------------------------------------


def test_shipping_a_later_version_still_spends_the_campaign_file(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "projects" / "demo"
    campaign_file = project_root / "DEFER-LOW-BUGS"
    release_state = project_root / "release-state.md"
    _write_campaign_file(campaign_file, "until=v2.14.0\n")
    _write_release_state(release_state, last_released="v2.14.3")

    result = _spend("demo", "v2.14.3", project_root, release_state)

    assert result.returncode == 0, f"spend step failed: {result.stderr!r}"
    assert not campaign_file.exists(), "a later shipped version must also spend the campaign file (latching >=)"
    audit = release_state.read_text(encoding="utf-8")
    assert "Until target satisfied: v2.14.0" in audit


# ---------------------------------------------------------------------------
# (3) A shipped version below the target leaves the file in place and
#     narrates it.
# ---------------------------------------------------------------------------


def test_shipping_below_the_target_leaves_the_campaign_file_in_place(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "projects" / "demo"
    campaign_file = project_root / "DEFER-LOW-BUGS"
    release_state = project_root / "release-state.md"
    _write_campaign_file(campaign_file, "until=v2.14.0\n")
    _write_release_state(release_state, last_released="v2.13.9")

    result = _spend("demo", "v2.13.9", project_root, release_state)

    assert result.returncode == 0, f"spend step failed: {result.stderr!r}"
    assert campaign_file.exists(), "a release below the until target must not remove the campaign file"
    assert campaign_file.read_text(encoding="utf-8") == "until=v2.14.0\n", "file contents must be untouched"
    assert "still in force" in result.stdout
    assert "until=v2.14.0" in result.stdout
    # No audit entry should have been written for an unsatisfied target.
    assert "## Campaign Spend Event" not in release_state.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (4) After a spend, the real resolver reports the posture resolving from a
#     cfg tier with no until target — exercised end to end.
# ---------------------------------------------------------------------------


def test_resolver_falls_through_to_cfg_tiers_after_a_spend(tmp_path: pathlib.Path) -> None:
    kanban_root = tmp_path
    project_root = kanban_root / "projects" / "demo"
    campaign_file = project_root / "DEFER-LOW-BUGS"
    release_state = project_root / "release-state.md"
    (kanban_root / "kanban.cfg").write_text("[release]\n", encoding="utf-8")
    (project_root / "project.cfg").parent.mkdir(parents=True, exist_ok=True)
    (project_root / "project.cfg").write_text("[release]\n", encoding="utf-8")
    _write_campaign_file(campaign_file, "until=v2.14.0\n")
    _write_release_state(release_state, last_released="v2.14.0")

    spend_result = _spend("demo", "v2.14.0", project_root, release_state)
    assert spend_result.returncode == 0
    assert not campaign_file.exists()

    resolve_script = (
        f"source {str(_DEFER_POSTURE)!r} && resolve_defer_posture demo"
    )
    resolve_env = {"KANBAN_ROOT": str(kanban_root), "PATH": "/usr/bin:/bin"}
    resolve_result = subprocess.run(
        ["bash", "-c", resolve_script],
        capture_output=True,
        text=True,
        env=resolve_env,
        timeout=15,
    )
    assert resolve_result.returncode == 0, f"resolver failed post-spend: {resolve_result.stderr!r}"
    fields: dict[str, str] = {}
    for line in resolve_result.stdout.strip().splitlines():
        field, rest = line.split("=", 1)
        value, _tier = rest.split("|", 1)
        fields[field] = value
    assert fields["until"] == "", "until must be empty once the campaign file is spent"
    assert fields["campaign_file"] == "", "campaign_file must be empty once the campaign file is spent"


# ---------------------------------------------------------------------------
# (5) A root-scoped campaign file is never touched by a single project's
#     spend step.
# ---------------------------------------------------------------------------


def test_root_scoped_campaign_file_is_never_removed_by_a_project_release(tmp_path: pathlib.Path) -> None:
    kanban_root = tmp_path
    project_root = kanban_root / "projects" / "demo"
    root_campaign_file = kanban_root / "DEFER-LOW-BUGS"
    release_state = project_root / "release-state.md"
    _write_campaign_file(root_campaign_file, "until=v2.14.0\n")
    _write_release_state(release_state, last_released="v2.14.0")
    project_root.mkdir(parents=True, exist_ok=True)

    # The project has no campaign file of its own; only the root-scoped one exists.
    result = _spend("demo", "v2.14.0", project_root, release_state)

    assert result.returncode == 0
    assert root_campaign_file.exists(), "a project-scoped spend must never remove the root-scoped campaign file"
    assert "nothing to spend" in result.stdout


# ---------------------------------------------------------------------------
# (6) A malformed campaign file, and a missing release-state.md at
#     audit-append time, are findings — never a crash, never a non-zero
#     return.
# ---------------------------------------------------------------------------


def test_malformed_until_line_is_a_warning_not_a_failure(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "projects" / "demo"
    campaign_file = project_root / "DEFER-LOW-BUGS"
    release_state = project_root / "release-state.md"
    _write_campaign_file(campaign_file, "until=not-a-version\n")
    _write_release_state(release_state, last_released="v2.14.0")

    result = _spend("demo", "v2.14.0", project_root, release_state)

    assert result.returncode == 0, "a malformed campaign file must not fail the release"
    assert campaign_file.exists(), "a malformed campaign file is left in place, not guessed at"
    assert "WARNING" in result.stderr


def test_missing_release_state_file_does_not_fail_the_spend(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "projects" / "demo"
    campaign_file = project_root / "DEFER-LOW-BUGS"
    release_state = project_root / "release-state.md"  # deliberately never created
    _write_campaign_file(campaign_file, "until=v2.14.0\n")
    project_root.mkdir(parents=True, exist_ok=True)

    result = _spend("demo", "v2.14.0", project_root, release_state)

    assert result.returncode == 0, "a missing release-state.md must not fail an already-tagged release"
    assert not campaign_file.exists(), "the campaign file is still spent even when the audit entry cannot be written"
    assert "WARNING" in result.stderr


def test_no_campaign_file_is_a_silent_no_op(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "projects" / "demo"
    release_state = project_root / "release-state.md"
    _write_release_state(release_state, last_released="v2.14.0")

    result = _spend("demo", "v2.14.0", project_root, release_state)

    assert result.returncode == 0
    assert "nothing to spend" in result.stdout
    assert "## Campaign Spend Event" not in release_state.read_text(encoding="utf-8")
