"""
test_overwatch_changelog_drift_bash.py
=======================================
Behavioral unit tests for check-changelog-drift.sh.

Integration fixture coverage (simulates the drift-recurrence scenario):

  (A) Fresh bug filing on ai_main after release → drift detected, CHANGELOG.md
      regenerated and committed, freshness lint exits 0 byte-exact.
  (B) No drift on main branch → no commit made, check exits 0.
  (C) Active RC in progress → drift reported, skip narrated, no commit.
  (D) HEAD on non-main branch (e.g. ai_rc/v1.23.10) → check skips.
  (E) --dry-run mode with drift → drift detected, no commit made.
  (F) Source is side-effect-free (sourcing check script does not call functions).
  (G) The project's repo lock held by another holder → drift reported, skip
      narrated naming the holder, no commit, CHANGELOG.md untouched.
  (H) An RC opened between detection and the lock → no commit.
  (I) The commit happens while the repo lock is held with OVERWATCH's
      provenance (holder, pid, op, task).
  (J) An unrelated file already staged in the dev tree stays staged and out
      of the repair commit, which carries CHANGELOG.md alone.
  (K) HEAD leaves the main branch between detection and the lock → no commit.
  (L) The CHANGELOG.md backup fails → the repair aborts, CHANGELOG.md is not
      overwritten, and stderr and the action log say why.

All tests run against synthetic environments and never touch the live kanban
root.  The dev tree used in each fixture is a minimal git repo whose
CHANGELOG.md and bug ledger can be controlled by the test.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

from tests.unit.shell_harness import run_bash
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="overwatch-changelog-drift-bash")

# ---------------------------------------------------------------------------
# Paths to the check module and related scripts.
# When pytest is run from team/ (the standard invocation via run-unit-tests.sh),
# paths are relative to team/.
# ---------------------------------------------------------------------------
_CHECK_DRIFT = "scripts/lib/overwatch-checks/check-changelog-drift.sh"
_PROTOCOL_SH = "scripts/lib/overwatch_protocol.sh"
_FRESHNESS_PY = "scripts/lint_changelog_freshness.py"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(repo: pathlib.Path, *args: str) -> str:
    """Run a git command inside repo; return stripped stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _make_git_repo(repo: pathlib.Path, branch: str = "ai_main") -> None:
    """Initialise a minimal git repo on the given branch with an initial commit."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    # Rename to the desired branch name.
    _git(repo, "branch", "-M", branch)


def _git_head_sha(repo: pathlib.Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------

def _build_kanban(
    kanban_root: pathlib.Path,
    project_name: str,
    dev_tree: pathlib.Path,
    branch_prefix: str = "ai_",
    active_rc: str = "none",
) -> pathlib.Path:
    """Build a minimal synthetic kanban root with one registered project.

    Returns the project directory path.
    """
    proj_dir = kanban_root / "projects" / project_name
    (proj_dir / "overwatch" / "backups").mkdir(parents=True)
    (proj_dir / "overwatch" / "actions.log").write_text("", encoding="utf-8")
    (proj_dir / "logs" / "overwatch").mkdir(parents=True)

    # Minimal project.cfg
    (proj_dir / "project.cfg").write_text(
        "[project]\n"
        f"project_name={project_name}\n"
        f"dev_tree_path={dev_tree}\n"
        f"branch_prefix={branch_prefix}\n",
        encoding="utf-8",
    )

    # Minimal release-state.md
    (proj_dir / "release-state.md").write_text(
        f"# Release State\n\n## Active RC\n{active_rc}\n",
        encoding="utf-8",
    )

    return proj_dir


def _action_log(proj_dir: pathlib.Path) -> str:
    """Read the overwatch action log for the project."""
    log_path = proj_dir / "overwatch" / "actions.log"
    if log_path.exists():
        return log_path.read_text(encoding="utf-8")
    return ""


def _write_minimal_changelog(path: pathlib.Path) -> None:
    """Write a minimal but valid CHANGELOG.md header."""
    path.write_text(
        "# Changelog\n\n"
        "<!-- Auto-generated by changelog_writer.py. Do not edit manually. -->\n"
        "<!-- Known Issues covers defects that existed in PUBLISHED releases only. -->\n"
        "<!-- Internal-only defects are tracked in the bug ledger but not disclosed. -->\n"
        "\n---\n",
        encoding="utf-8",
    )


def _write_stale_changelog(path: pathlib.Path) -> None:
    """Write a CHANGELOG.md that the writer would not produce (stale content)."""
    path.write_text(
        "# Changelog\n\n"
        "<!-- STALE: this content is intentionally wrong for testing drift detection -->\n"
        "\n---\n",
        encoding="utf-8",
    )


def _build_minimal_dev_tree(
    dev_tree: pathlib.Path,
    bugs_dir: pathlib.Path,
    changelog_text: str | None = None,
) -> None:
    """Populate a git repo with the minimal structure changelog_writer needs.

    Creates:
      - release-notes/PUBLISHED (empty — no published releases)
      - release-notes/v0.1.0.md (minimal release notes entry)
      - CHANGELOG.md
      - team/ directory with enough structure for changelog_writer import
    """
    # Release notes
    notes_dir = dev_tree / "release-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "PUBLISHED").write_text("", encoding="utf-8")
    (notes_dir / "v0.1.0.md").write_text(
        "# Release v0.1.0\n\n## Summary\nInitial release.\n\n## Implemented\n- Initial feature.\n",
        encoding="utf-8",
    )

    # CHANGELOG.md
    if changelog_text is not None:
        (dev_tree / "CHANGELOG.md").write_text(changelog_text, encoding="utf-8")

    # Symlink or stub the team/ package so changelog_writer imports work.
    # We point team/ at the real team directory resolved from this test file.
    # The real changelog_writer.py is at team/pgai_agent_kanban/cm/changelog_writer.py.
    import os
    real_team = pathlib.Path(__file__).parent.parent.parent  # team/
    team_link = dev_tree / "team"
    if not team_link.exists():
        os.symlink(str(real_team), str(team_link))


def _regenerate_changelog_for_tree(
    dev_tree: pathlib.Path, bugs_dir: pathlib.Path
) -> str:
    """Run changelog_writer.py for the given dev tree and return the output."""
    import os
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONPATH"] = str(dev_tree / "team")
    result = subprocess.run(
        [
            "python3", "-m", "pgai_agent_kanban.cm.changelog_writer",
            str(dev_tree), str(bugs_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"changelog_writer failed:\n{result.stderr}"
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Test (A): drift detected → CHANGELOG.md regenerated and committed
# ---------------------------------------------------------------------------


def test_drift_detected_regenerates_and_commits(tmp_path: pathlib.Path) -> None:
    """
    Fixture (A): ai_main has a stale CHANGELOG.md.

    Scenario: a release shipped with CHANGELOG.md at content C1.  A new bug
    file is added to the bug ledger afterward (simulating a post-release bug
    filing).  changelog_writer would now produce C2 ≠ C1.  The OVERWATCH check
    must detect the drift, regenerate CHANGELOG.md, and commit it.

    After the check runs:
      - A new commit exists on ai_main beyond the pre-run HEAD.
      - CHANGELOG.md content matches the fresh writer output (byte-exact).
      - The action log contains "changelog-drift-fixed".
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    main_branch = "ai_main"
    project_name = "test-project"

    # Set up the dev tree git repo on ai_main.
    _make_git_repo(dev_tree, branch=main_branch)

    # Build minimal dev-tree structure with an intentionally stale CHANGELOG.
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)

    _build_minimal_dev_tree(dev_tree, bugs_dir, changelog_text="# STALE\n")

    # Add a bug file to the kanban bugs dir (simulates post-release bug filing).
    (bugs_dir / "BUG-9999-test-drift.md").write_text(
        "# BUG-9999-test-drift\n\n"
        "## Status\nopen\n\n"
        "## Symptom\nTest drift scenario.\n",
        encoding="utf-8",
    )

    # Stage and commit the initial state (stale CHANGELOG on ai_main).
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "initial state with stale CHANGELOG")

    sha_before = _git_head_sha(dev_tree)

    proj_dir = _build_kanban(
        kanban_root,
        project_name,
        dev_tree,
        branch_prefix="ai_",
    )

    # Run the check.  overwatch_protocol.sh is sourced first, as the sweep
    # runner does, so the repair runs through overwatch_halt_first_fix and
    # the stubs below replace its logging helpers.
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_PROTOCOL_SH}

        # Stub overwatch_log_action to append to the project's action log.
        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            printf '%s\\t%s\\t%s\\n' "${{name}}" "${{action}}" "${{reason}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_backup_file() {{ return 0; }}

        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tmp_path}/pgai_tmp"
        export PGAI_AGENT_KANBAN_ROOT_PATH="{kanban_root}"

        source {_CHECK_DRIFT}
        overwatch_check_changelog_drift
    """)

    result = run_bash(tmp_path, script, timeout=60)
    assert result.returncode == 0, (
        f"check-changelog-drift should exit 0\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    # A new commit should exist.
    sha_after = _git_head_sha(dev_tree)
    assert sha_after != sha_before, (
        "Expected a new commit after drift fix, but HEAD did not advance\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    # CHANGELOG.md should now be byte-identical to a fresh regeneration.
    fresh = _regenerate_changelog_for_tree(dev_tree, bugs_dir)
    actual = (dev_tree / "CHANGELOG.md").read_text(encoding="utf-8")
    assert actual == fresh, (
        "CHANGELOG.md after fix should be byte-identical to fresh writer output"
    )

    # Action log should record the fix.
    log = _action_log(proj_dir)
    assert "changelog-drift-fixed" in log, (
        f"Expected 'changelog-drift-fixed' in action log, got:\n{log}"
    )


# ---------------------------------------------------------------------------
# Test (B): no drift → no commit
# ---------------------------------------------------------------------------


def test_no_drift_no_commit(tmp_path: pathlib.Path) -> None:
    """
    Fixture (B): ai_main has a fresh CHANGELOG.md.

    When CHANGELOG.md matches the writer output, no commit should be made and
    the action log should record "changelog-drift-none".
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    main_branch = "ai_main"
    project_name = "test-project"

    _make_git_repo(dev_tree, branch=main_branch)

    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)

    _build_minimal_dev_tree(dev_tree, bugs_dir, changelog_text=None)

    # Generate the fresh CHANGELOG.md and install it so there is no drift.
    fresh = _regenerate_changelog_for_tree(dev_tree, bugs_dir)
    (dev_tree / "CHANGELOG.md").write_text(fresh, encoding="utf-8")

    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "initial state with fresh CHANGELOG")

    sha_before = _git_head_sha(dev_tree)

    proj_dir = _build_kanban(
        kanban_root, project_name, dev_tree, branch_prefix="ai_"
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail

        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            printf '%s\\t%s\\t%s\\n' "${{name}}" "${{action}}" "${{reason}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_backup_file() {{ return 0; }}

        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tmp_path}/pgai_tmp"
        export PGAI_AGENT_KANBAN_ROOT_PATH="{kanban_root}"

        source {_CHECK_DRIFT}
        overwatch_check_changelog_drift
    """)

    result = run_bash(tmp_path, script, timeout=60)
    assert result.returncode == 0, (
        f"check-changelog-drift should exit 0 with no drift\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    sha_after = _git_head_sha(dev_tree)
    assert sha_after == sha_before, (
        "No commit should be made when CHANGELOG.md is fresh"
    )

    log = _action_log(proj_dir)
    assert "changelog-drift-none" in log, (
        f"Expected 'changelog-drift-none' in action log, got:\n{log}"
    )


# ---------------------------------------------------------------------------
# Test (C): active RC → skip without committing
# ---------------------------------------------------------------------------


def test_active_rc_skips(tmp_path: pathlib.Path) -> None:
    """
    Fixture (C): an active RC is present.

    The check must skip without committing when the release pipeline is active,
    so it does not interfere with the in-flight RC.
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    main_branch = "ai_main"
    project_name = "test-project"

    _make_git_repo(dev_tree, branch=main_branch)

    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)

    _build_minimal_dev_tree(dev_tree, bugs_dir, changelog_text="# STALE\n")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "stale changelog")

    sha_before = _git_head_sha(dev_tree)

    proj_dir = _build_kanban(
        kanban_root, project_name, dev_tree, branch_prefix="ai_",
        active_rc="v1.23.10",
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail

        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            printf '%s\\t%s\\t%s\\n' "${{name}}" "${{action}}" "${{reason}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_backup_file() {{ return 0; }}

        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tmp_path}/pgai_tmp"
        export PGAI_AGENT_KANBAN_ROOT_PATH="{kanban_root}"

        source {_CHECK_DRIFT}
        overwatch_check_changelog_drift
    """)

    result = run_bash(tmp_path, script, timeout=30)
    assert result.returncode == 0, (
        f"check-changelog-drift should exit 0 when active RC present\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    sha_after = _git_head_sha(dev_tree)
    assert sha_after == sha_before, (
        "No commit should be made when an active RC is present"
    )
    assert (dev_tree / "CHANGELOG.md").read_text(encoding="utf-8") == "# STALE\n", (
        "CHANGELOG.md must be left untouched while an RC is active"
    )
    assert "drift detected" in result.stderr
    assert "active RC 'v1.23.10' in progress; nothing was changed" in result.stderr
    assert "changelog-drift-skipped-active-rc" in _action_log(proj_dir)


# ---------------------------------------------------------------------------
# Test (D): HEAD on non-main branch → skip
# ---------------------------------------------------------------------------


def test_non_main_branch_skips(tmp_path: pathlib.Path) -> None:
    """
    Fixture (D): dev tree HEAD is on ai_rc/v1.23.10 (not ai_main).

    The check must skip without committing to avoid corrupting an RC branch.
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    project_name = "test-project"

    _make_git_repo(dev_tree, branch="ai_main")

    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)

    _build_minimal_dev_tree(dev_tree, bugs_dir, changelog_text="# STALE\n")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "stale changelog")

    # Check out a non-main branch.
    _git(dev_tree, "checkout", "-b", "ai_rc/v1.23.10")

    sha_before = _git_head_sha(dev_tree)

    proj_dir = _build_kanban(
        kanban_root, project_name, dev_tree, branch_prefix="ai_"
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail

        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            printf '%s\\t%s\\t%s\\n' "${{name}}" "${{action}}" "${{reason}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_backup_file() {{ return 0; }}

        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tmp_path}/pgai_tmp"
        export PGAI_AGENT_KANBAN_ROOT_PATH="{kanban_root}"

        source {_CHECK_DRIFT}
        overwatch_check_changelog_drift
    """)

    result = run_bash(tmp_path, script, timeout=30)
    assert result.returncode == 0, (
        f"check-changelog-drift should exit 0 on non-main branch\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    sha_after = _git_head_sha(dev_tree)
    assert sha_after == sha_before, (
        "No commit should be made when HEAD is not on the main branch"
    )


# ---------------------------------------------------------------------------
# Test (E): --dry-run mode → drift detected but no commit
# ---------------------------------------------------------------------------


def test_dry_run_detects_drift_without_commit(tmp_path: pathlib.Path) -> None:
    """
    Fixture (E): drift is present, --dry-run flag is set.

    The check must detect drift, log "changelog-drift-dry-run", and NOT commit.
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    main_branch = "ai_main"
    project_name = "test-project"

    _make_git_repo(dev_tree, branch=main_branch)

    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)

    _build_minimal_dev_tree(dev_tree, bugs_dir, changelog_text="# STALE\n")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "stale changelog")

    sha_before = _git_head_sha(dev_tree)

    proj_dir = _build_kanban(
        kanban_root, project_name, dev_tree, branch_prefix="ai_"
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail

        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            printf '%s\\t%s\\t%s\\n' "${{name}}" "${{action}}" "${{reason}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_backup_file() {{ return 0; }}

        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tmp_path}/pgai_tmp"
        export PGAI_AGENT_KANBAN_ROOT_PATH="{kanban_root}"

        source {_CHECK_DRIFT}
        overwatch_check_changelog_drift --dry-run
    """)

    result = run_bash(tmp_path, script, timeout=30)
    assert result.returncode == 0, (
        f"check-changelog-drift --dry-run should exit 0\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    sha_after = _git_head_sha(dev_tree)
    assert sha_after == sha_before, (
        "No commit should be made in --dry-run mode"
    )

    log = _action_log(proj_dir)
    assert "changelog-drift-dry-run" in log, (
        f"Expected 'changelog-drift-dry-run' in action log, got:\n{log}"
    )


# ---------------------------------------------------------------------------
# Test (F): source is side-effect-free
# ---------------------------------------------------------------------------


def test_source_is_side_effect_free(tmp_path: pathlib.Path) -> None:
    """
    Fixture (F): sourcing check-changelog-drift.sh must not execute any commands.

    After sourcing, 'overwatch_check_changelog_drift' must be defined as a
    function.  No git operations or file modifications should occur on source.
    """
    script = textwrap.dedent(f"""\
        set -euo pipefail
        # Provide required env stubs so the source does not fail on lookups.
        export KANBAN_ROOT="{tmp_path}"
        export OVERWATCH_PROJECT="test-project"
        source {_CHECK_DRIFT}
        # Verify the function is defined.
        declare -f overwatch_check_changelog_drift >/dev/null || {{
            echo "overwatch_check_changelog_drift not defined after source" >&2
            exit 1
        }}
        echo "ok: function defined after source"
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"Sourcing check-changelog-drift.sh should define the function without errors\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "ok: function defined after source" in result.stdout


# ---------------------------------------------------------------------------
# Test (G): bash -n syntax check
# ---------------------------------------------------------------------------


def test_bash_n_passes(tmp_path: pathlib.Path) -> None:
    """Fixture (G): bash -n must succeed on check-changelog-drift.sh."""
    import subprocess as _sp

    result = _sp.run(
        ["bash", "-n", _CHECK_DRIFT],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n failed for {_CHECK_DRIFT}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Tests (G)-(I): the repair runs only under the project's repo lock
# ---------------------------------------------------------------------------


def _stale_fixture(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, str]:
    """A dev tree on ai_main with a stale CHANGELOG.md and no active RC.

    Returns (kanban_root, dev_tree, proj_dir, sha_before).
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"
    project_name = "test-project"
    _make_git_repo(dev_tree, branch="ai_main")
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    _build_minimal_dev_tree(dev_tree, bugs_dir, changelog_text="# STALE\n")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "stale changelog")
    proj_dir = _build_kanban(kanban_root, project_name, dev_tree, branch_prefix="ai_")
    return kanban_root, dev_tree, proj_dir, _git_head_sha(dev_tree)


def _repair_script(tmp_path: pathlib.Path, kanban_root: pathlib.Path, proj_dir: pathlib.Path, extra: str = "") -> str:
    """The sweep-shaped invocation: protocol sourced, logging stubbed, check run.

    *extra* is bash run after the check is sourced and before it is called.
    """
    return textwrap.dedent(f"""\
        set -euo pipefail
        source {_PROTOCOL_SH}
        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            printf '%s\\t%s\\t%s\\n' "${{name}}" "${{action}}" "${{reason}}" \\
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_backup_file() {{ return 0; }}

        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="test-project"
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tmp_path}/pgai_tmp"
        export PGAI_AGENT_KANBAN_ROOT_PATH="{kanban_root}"

        source {_CHECK_DRIFT}
    """) + extra + "\noverwatch_check_changelog_drift\n"


def test_repo_lock_held_by_another_holder_skips_and_narrates(tmp_path: pathlib.Path) -> None:
    """Fixture (G): a CM release holds the project's repo lock.

    The check still detects and reports the drift, narrates the skip naming
    the holder, makes no commit and leaves CHANGELOG.md as it was.
    """
    from pgai_agent_kanban.ops.locks import acquire_lock, repo_lock_path

    kanban_root, dev_tree, proj_dir, sha_before = _stale_fixture(tmp_path)
    held = acquire_lock(
        repo_lock_path(kanban_root, "test-project"),
        holder="cm",
        pid=__import__("os").getpid(),
        op="release-squash",
        task="CM-20990101-001-release-v9-9-9",
    )
    try:
        result = run_bash(tmp_path, _repair_script(tmp_path, kanban_root, proj_dir), timeout=60)
    finally:
        held.release()

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _git_head_sha(dev_tree) == sha_before, (
        f"a commit was made while another holder had the repo lock\nstderr={result.stderr}"
    )
    assert (dev_tree / "CHANGELOG.md").read_text(encoding="utf-8") == "# STALE\n"
    assert _git(dev_tree, "status", "--porcelain") == ""
    assert "drift detected" in result.stderr
    assert "left unrepaired" in result.stderr
    assert "holder=cm" in result.stderr and "op=release-squash" in result.stderr, result.stderr
    assert "changelog-drift-skipped-lock-held" in _action_log(proj_dir)
    assert not (proj_dir / "HALT").exists(), "the skipped repair left a project HALT behind"


def test_rc_opened_before_lock_is_taken_makes_no_commit(tmp_path: pathlib.Path) -> None:
    """Fixture (H): Active RC flips from none to an RC while the lock is being taken.

    The repair re-reads Active RC under the lock and changes nothing.
    """
    kanban_root, dev_tree, proj_dir, sha_before = _stale_fixture(tmp_path)
    release_state = proj_dir / "release-state.md"
    # The libraries overwatch_halt_first_fix would otherwise source from its
    # own directory are loaded first, as the sweep runner loads them: the
    # renamed copy below no longer knows where it was defined.
    extra = textwrap.dedent(f"""\
        source scripts/lib/project_paths.sh
        source scripts/lib/lock_provenance.sh
        eval "$(declare -f overwatch_halt_first_fix | sed '1s/overwatch_halt_first_fix/_real_halt_first_fix/')"
        overwatch_halt_first_fix() {{
            printf '# Release State\\n\\n## Active RC\\nv9.9.9\\n' > "{release_state}"
            _real_halt_first_fix "$@"
        }}
    """)
    result = run_bash(tmp_path, _repair_script(tmp_path, kanban_root, proj_dir, extra), timeout=60)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _git_head_sha(dev_tree) == sha_before
    assert (dev_tree / "CHANGELOG.md").read_text(encoding="utf-8") == "# STALE\n"
    assert "active RC 'v9.9.9' opened before the repo lock was taken" in result.stderr
    assert "changelog-drift-skipped-active-rc" in _action_log(proj_dir)


def test_commit_happens_under_the_repo_lock_with_provenance(tmp_path: pathlib.Path) -> None:
    """Fixture (I): with no lock held and no RC, exactly one CHANGELOG.md commit
    lands, made while the repo lock records OVERWATCH as its holder.
    """
    kanban_root, dev_tree, proj_dir, sha_before = _stale_fixture(tmp_path)
    capture = tmp_path / "lock-during-commit.txt"
    extra = textwrap.dedent(f"""\
        eval "$(declare -f _ccd_commit_regenerated | sed '1s/_ccd_commit_regenerated/_real_ccd_commit/')"
        _ccd_commit_regenerated() {{
            cat "$(pp_repo_lock_path test-project)" > "{capture}"
            _real_ccd_commit "$@"
        }}
    """)
    result = run_bash(tmp_path, _repair_script(tmp_path, kanban_root, proj_dir, extra), timeout=60)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    commits = _git(dev_tree, "rev-list", f"{sha_before}..HEAD").split()
    assert len(commits) == 1, f"expected exactly one new commit, got {commits}\nstderr={result.stderr}"
    assert _git(dev_tree, "show", "--name-only", "--format=", "HEAD") == "CHANGELOG.md"
    record = capture.read_text(encoding="utf-8")
    for field in ("holder=overwatch", "op=overwatch-halt-first-fix:_ccd_commit_regenerated", "task=none"):
        assert field in record, f"lock record missing {field!r}:\n{record}"
    assert "pid=" in record
    assert "changelog-drift-fixed" in _action_log(proj_dir)
    assert not (proj_dir / "HALT").exists(), "the repair left its project HALT behind"


def test_repair_commits_only_changelog_and_leaves_other_staged_files(tmp_path: pathlib.Path) -> None:
    """Fixture (J): an operator's staged file never rides along in the repair commit."""
    kanban_root, dev_tree, proj_dir, sha_before = _stale_fixture(tmp_path)
    (dev_tree / "operator-work.txt").write_text("in progress\n", encoding="utf-8")
    _git(dev_tree, "add", "operator-work.txt")

    result = run_bash(tmp_path, _repair_script(tmp_path, kanban_root, proj_dir), timeout=60)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _git(dev_tree, "rev-list", "--count", f"{sha_before}..HEAD") == "1", result.stderr
    assert _git(dev_tree, "show", "--name-only", "--format=", "HEAD") == "CHANGELOG.md"
    assert _git(dev_tree, "diff", "--cached", "--name-only") == "operator-work.txt"
    assert "changelog-drift-fixed" in _action_log(proj_dir)


def test_head_leaving_main_before_the_lock_makes_no_commit(tmp_path: pathlib.Path) -> None:
    """Fixture (K): a checkout lands between detection and the locked repair."""
    kanban_root, dev_tree, proj_dir, sha_before = _stale_fixture(tmp_path)
    extra = textwrap.dedent(f"""\
        source scripts/lib/project_paths.sh
        source scripts/lib/lock_provenance.sh
        eval "$(declare -f overwatch_halt_first_fix | sed '1s/overwatch_halt_first_fix/_real_halt_first_fix/')"
        overwatch_halt_first_fix() {{
            git -C "{dev_tree}" checkout -q -b ai_rc/v9.9.9
            _real_halt_first_fix "$@"
        }}
    """)
    result = run_bash(tmp_path, _repair_script(tmp_path, kanban_root, proj_dir, extra), timeout=60)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _git(dev_tree, "rev-parse", "ai_main") == sha_before
    assert _git_head_sha(dev_tree) == sha_before
    assert (dev_tree / "CHANGELOG.md").read_text(encoding="utf-8") == "# STALE\n"
    assert "HEAD moved to 'ai_rc/v9.9.9' before the repo lock was taken" in result.stderr
    assert "changelog-drift-skipped-head-moved" in _action_log(proj_dir)


def test_failed_backup_aborts_the_repair(tmp_path: pathlib.Path) -> None:
    """Fixture (L): no backup, no rewrite."""
    kanban_root, dev_tree, proj_dir, sha_before = _stale_fixture(tmp_path)
    extra = "overwatch_backup_file() { echo 'backup: disk full' >&2; return 1; }\n"
    result = run_bash(tmp_path, _repair_script(tmp_path, kanban_root, proj_dir, extra), timeout=60)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _git_head_sha(dev_tree) == sha_before
    assert (dev_tree / "CHANGELOG.md").read_text(encoding="utf-8") == "# STALE\n"
    assert _git(dev_tree, "status", "--porcelain") == ""
    assert "overwatch.changelog-drift.backup-failed" in result.stderr, result.stderr
    assert "could not be backed up" in result.stderr
    assert "changelog-drift-skipped-backup-failed" in _action_log(proj_dir)
    assert not (proj_dir / "HALT").exists()
