"""
test_verify_rc_state_release_check.py
======================================
Integration tests for team/scripts/verify-rc-state.sh's release-state.md
check (check A), converted to call the shared schema-driven validator in
team/pgai_agent_kanban/cm/read_state_field.py instead of its own reader and
format checks.

Coverage:
  - A well-formed release-state.md produces the same ok/warn/error shape and
    exit code the check always has: every field OK, exit 0.
  - A malformed field (an uppercase 'NONE' where the schema accepts the
    literal 'none' or a version) produces an ERROR line, and that line's
    accepted-forms text is the exact string the shared Python validator
    module owns -- proof the shell script has no second copy of the
    accepted-values decision to drift out of sync with.

Design notes:
  - All tests use tmp_path for scratch; no bare /tmp paths.
  - verify-rc-state.sh is invoked via subprocess against a temporary kanban
    root and a temporary local git repo -- never the live install.
  - Fixture construction mirrors the pattern in test_rc_lifecycle.py's
    _build_kanban_root_for_rc, trimmed to what a read-only checker needs
    (no push/pull, no bare remote).
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.cm.read_state_field import FieldKind, _ACCEPTED_FORMS
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="verify-rc-state-release-check")

# ---------------------------------------------------------------------------
# Path helpers — resolve from this file's own location.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_VERIFY_SCRIPT = _TEAM_DIR / "scripts" / "verify-rc-state.sh"


def _init_local_repo(repo_path: pathlib.Path) -> None:
    """Initialise a minimal local git repo with one commit -- no remote."""
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path, check=True,
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_path, check=True)


def _build_kanban_root(
    parent: pathlib.Path, project_name: str, repo_path: pathlib.Path
) -> pathlib.Path:
    """Build a minimal kanban root that verify-rc-state.sh can run against."""
    root = parent / "kanban_root"
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\ndescription=verify test\nenabled=true\n",
        encoding="utf-8",
    )
    proj = root / "projects" / project_name
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = {repo_path}\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        "git_remote_name = origin\n"
        "workflow_type = release\n"
        "is_self_build = false\n"
        "push_to_remote = false\n"
        "\n"
        "[versioning]\n"
        "max_patch = 99\n"
        "max_minor = 9\n"
        "max_major = 0\n",
        encoding="utf-8",
    )
    return root


def _write_release_state(project_root: pathlib.Path, body: str) -> None:
    (project_root / "release-state.md").write_text(body, encoding="utf-8")


def _run_verify_rc_state(
    kanban_root: pathlib.Path, project_name: str
) -> subprocess.CompletedProcess:
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    return subprocess.run(
        ["bash", str(_VERIFY_SCRIPT), project_name, "--verbose"],
        capture_output=True, text=True, env=base_env, timeout=60,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_check_a_passes_every_field_for_well_formed_fresh_project(
    tmp_path: pathlib.Path,
) -> None:
    """A never-shipped project's release-state.md (all-'none' populated
    fields, 'Last Released' absent) produces an OK line for every schema
    field in check A, and the overall run exits 0 -- the verifier's exit
    status and output shape for a healthy project are unchanged."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    kanban_root = _build_kanban_root(tmp_path, "verifytest", repo)
    _write_release_state(
        kanban_root / "projects" / "verifytest",
        "# Release State\n\n"
        "## Active RC\nnone\n\n"
        "## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n",
    )

    result = _run_verify_rc_state(kanban_root, "verifytest")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK]    Active RC is 'none' (no RC in flight)" in result.stdout
    assert "[OK]    RC Opened At timestamp valid: none" in result.stdout
    assert "[OK]    RC Opened By Task is valid: none" in result.stdout
    assert "[OK]    Last Released is 'none' (no prior release)" in result.stdout
    assert "0 errors" in result.stdout


def test_check_a_error_message_shares_accepted_forms_text_with_shared_validator(
    tmp_path: pathlib.Path,
) -> None:
    """When Active RC holds 'NONE' instead of the schema's literal 'none',
    check A's ERROR line names the exact accepted-forms text the shared
    Python validator module owns (_ACCEPTED_FORMS[FieldKind.VERSION_OR_NONE])
    -- proof the shell script echoes the validator's own decision rather
    than carrying a second, independently-maintained copy of it."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    kanban_root = _build_kanban_root(tmp_path, "verifytest", repo)
    _write_release_state(
        kanban_root / "projects" / "verifytest",
        "# Release State\n\n"
        "## Active RC\nNONE\n\n"
        "## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n",
    )

    result = _run_verify_rc_state(kanban_root, "verifytest")

    assert result.returncode == 1, result.stdout + result.stderr
    accepted_forms = _ACCEPTED_FORMS[FieldKind.VERSION_OR_NONE]
    matching_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith("[ERROR]") and "Active RC" in line
    ]
    assert matching_lines, result.stdout
    assert accepted_forms in matching_lines[0]
    assert "'NONE'" in matching_lines[0]
