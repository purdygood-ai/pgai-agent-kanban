"""
test_cfg_example_key_coverage.py
=================================
Unit tests for the config templates' coverage of the loader-accepted keys:

  - kanban.cfg_example's [dashboard] max_rows_per_project line states the
    same default team/scripts/lib/config_loader.sh's Key Registry declares
    (a template default that differs from the registry's is a CONFIG
    DEFAULT MISMATCH; the registry's own default is 8).
  - project.cfg_example carries a key = value line for each of the
    ten previously-undocumented project.cfg keys ([hooks] six keys,
    [project] temp_dir (commented, so it resolves empty), [training]
    reasoning_trace and training_agents,
    [versioning] max_priorities_per_run) with a value that resolves to
    exactly what team/scripts/lib/project_paths.sh's Key Registry declares
    as that key's default, so copying the template unedited changes no
    effective behaviour.

These tests read the real templates through the production INI reader
(team/scripts/lib/ini_parser.sh::read_ini), not a reimplementation of it,
matching the subprocess pattern already used by
test_config_loader_effort_resolution.py and
test_config_loader_release_posture_keys.py.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_INI_PARSER = _TEAM_DIR / "scripts" / "lib" / "ini_parser.sh"
_KANBAN_CFG_EXAMPLE = _REPO_ROOT / "kanban.cfg_example"
_PROJECT_CFG_EXAMPLE = _REPO_ROOT / "project.cfg_example"


def _read_ini(cfg_file: pathlib.Path, section: str, key: str, timeout: int = 15) -> str:
    """Run read_ini against *cfg_file* and return the resolved value.

    Invokes the real production shell function via subprocess rather than
    reimplementing INI parsing in Python, so a test failure here means the
    template genuinely does not parse the way an operator's copy would.
    """
    script = f"source {str(_INI_PARSER)!r} && read_ini {str(cfg_file)!r} {section!r} {key!r} ''"
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"read_ini failed for [{section}] {key}: {result.stderr!r}"
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# kanban.cfg_example: the CONFIG DEFAULT MISMATCH fix.
# ---------------------------------------------------------------------------


def test_kanban_cfg_example_max_rows_per_project_matches_registry_default() -> None:
    """[dashboard] max_rows_per_project must read back as 8 — the default
    team/scripts/lib/config_loader.sh's Key Registry declares — not the
    stale 13 the template previously stated."""
    value = _read_ini(_KANBAN_CFG_EXAMPLE, "dashboard", "max_rows_per_project")
    assert value == "8", (
        f"kanban.cfg_example [dashboard] max_rows_per_project resolved to {value!r}; "
        "expected '8' to match config_loader.sh's Key Registry default"
    )


@pytest.mark.parametrize(
    "section, key, expected",
    [
        pytest.param("overwatch", "block_triage", "true", id="overwatch_block_triage"),
        pytest.param("overwatch", "requeue_budget", "3", id="overwatch_requeue_budget"),
        pytest.param("wake", "block_storm_window", "3600", id="wake_block_storm_window"),
    ],
)
def test_kanban_cfg_example_documents_block_triage_keys_at_their_defaults(
    section: str, key: str, expected: str
) -> None:
    """kanban.cfg_example carries both keys as active lines set to the
    defaults the resolvers apply when the key is absent, so copying the
    template unedited changes no behaviour."""
    value = _read_ini(_KANBAN_CFG_EXAMPLE, section, key)
    assert value == expected, (
        f"kanban.cfg_example [{section}] {key} resolved to {value!r}; expected {expected!r}"
    )


@pytest.mark.parametrize(
    "key, expected",
    [
        pytest.param("lock_wait_seconds", "120", id="wake_lock_wait_seconds"),
        pytest.param("lock_starvation_firings", "3", id="wake_lock_starvation_firings"),
    ],
)
def test_kanban_cfg_example_documents_lock_wait_keys_at_their_defaults(key: str, expected: str) -> None:
    """kanban.cfg_example carries the repo-lock wait and starvation keys as
    active [wake] lines at the defaults config_loader.sh applies when the
    key is absent, each with a "Default:" line stating that value."""
    assert _read_ini(_KANBAN_CFG_EXAMPLE, "wake", key) == expected
    text = _KANBAN_CFG_EXAMPLE.read_text(encoding="utf-8")
    block = text.split(f"\n{key} = {expected}\n", 1)[0].rsplit("\n\n", 1)[-1]
    assert f"# Default: {expected}" in block, f"no '# Default: {expected}' comment above {key}"


def test_project_cfg_example_documents_block_triage_override_commented_out() -> None:
    """project.cfg_example documents the [overwatch] block_triage override
    as a commented line, so a fresh project inherits the kanban.cfg value
    until an operator opts it out."""
    assert _read_ini(_PROJECT_CFG_EXAMPLE, "overwatch", "block_triage") == ""
    lines = _PROJECT_CFG_EXAMPLE.read_text(encoding="utf-8").splitlines()
    section_lines = []
    in_overwatch = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_overwatch = stripped == "[overwatch]"
            continue
        if in_overwatch:
            section_lines.append(stripped)
    assert any(l.startswith("# block_triage =") for l in section_lines), (
        "project.cfg_example's [overwatch] section should carry a commented "
        "'# block_triage = ...' override line"
    )


def test_project_cfg_example_documents_requeue_budget_override_commented_out() -> None:
    """project.cfg_example documents the [overwatch] requeue_budget
    override as a commented line under [overwatch], stating the accepted
    form and the kanban-wide default, so a fresh project inherits the
    kanban.cfg value until an operator overrides it."""
    assert _read_ini(_PROJECT_CFG_EXAMPLE, "overwatch", "requeue_budget") == ""
    text = _PROJECT_CFG_EXAMPLE.read_text(encoding="utf-8")
    overwatch = text.split("\n[overwatch]\n", 1)[1].split("\n[", 1)[0]
    assert "\n# requeue_budget = 3\n" in overwatch, overwatch
    assert "# Accepted: a non-negative whole number." in overwatch
    assert "# Default: 3" in overwatch


# ---------------------------------------------------------------------------
# project.cfg_example: the ten previously-undocumented project.cfg keys,
# each now an active line whose value equals its own registry default (so
# a fresh install that copies the template unedited changes no behaviour).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "section, key, expected",
    [
        pytest.param("hooks", "cm_release_pre_squash_hook", "", id="hooks_pre_squash_hook"),
        pytest.param("hooks", "cm_release_pre_squash_hook_required", "false", id="hooks_pre_squash_required"),
        pytest.param("hooks", "cm_release_pre_tag_hook", "", id="hooks_pre_tag_hook"),
        pytest.param("hooks", "cm_release_pre_tag_hook_required", "false", id="hooks_pre_tag_required"),
        pytest.param("hooks", "cm_release_post_tag_hook", "", id="hooks_post_tag_hook"),
        pytest.param("hooks", "cm_release_post_tag_hook_required", "false", id="hooks_post_tag_required"),
        pytest.param("project", "temp_dir", "", id="project_temp_dir"),
        pytest.param("training", "reasoning_trace", "false", id="training_reasoning_trace"),
        pytest.param("training", "training_agents", "", id="training_training_agents"),
        pytest.param("versioning", "max_priorities_per_run", "1", id="versioning_max_priorities_per_run"),
    ],
)
def test_project_cfg_example_documents_previously_undocumented_key(
    section: str, key: str, expected: str
) -> None:
    value = _read_ini(_PROJECT_CFG_EXAMPLE, section, key)
    assert value == expected, (
        f"project.cfg_example [{section}] {key} resolved to {value!r}; expected {expected!r} "
        "to match project_paths.sh's Key Registry default"
    )
