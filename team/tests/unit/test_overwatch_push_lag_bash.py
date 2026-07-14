"""
test_overwatch_push_lag_bash.py
================================
Behavioral unit tests for check-push-lag.sh and the prefix-aware branch
reasoning in check-stale-active-rc.sh / check-orphan-rc-branches.sh.

Fixtures exercised:
  (A) push_to_remote=false + local-ahead state:
      No push invoked. Action log contains a 'staged-by-design' entry.
  (B) push_to_remote=true + local-ahead state (dry-run):
      Push path entered. Action log contains 'dry-run-push-lag-detected'.
  (C) prefix_branch=ai_ + orphan branch named ai_rc/v1.0.0:
      check-orphan-rc-branches detects the orphan with the prefixed pattern.
  (D) prefix_branch=ai_ + stale rc with ai_rc/v1.0.0 absent, ai_v1.0.0 tag present:
      check-stale-active-rc detects stale state correctly.

All tests run against synthetic environments and never touch the live kanban root.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths to the check modules.
# When pytest is run from the team/ directory (the standard invocation via
# run-unit-tests.sh), paths are relative to team/.  All shell_harness.py
# invocations run bash from the process cwd, which is team/ in that case.
# ---------------------------------------------------------------------------
_CHECK_PUSH_LAG = "scripts/lib/overwatch-checks/check-push-lag.sh"
_CHECK_ORPHAN_RC = "scripts/lib/overwatch-checks/check-orphan-rc-branches.sh"
_CHECK_STALE_RC = "scripts/lib/overwatch-checks/check-stale-active-rc.sh"
_PROTOCOL_SH = "scripts/lib/overwatch_protocol.sh"


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


def _make_git_repo(repo: pathlib.Path) -> None:
    """Initialise a git repo with an initial commit."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")


def _make_commit(repo: pathlib.Path, msg: str = "bump") -> str:
    """Add a file and commit; return the commit SHA."""
    (repo / f"{msg}.txt").write_text(f"{msg}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _make_tag(repo: pathlib.Path, tag: str) -> None:
    """Create a lightweight tag on HEAD."""
    _git(repo, "tag", tag)


def _make_branch(repo: pathlib.Path, branch: str) -> None:
    """Create a local branch pointing at HEAD (no-op if already exists)."""
    try:
        _git(repo, "branch", branch)
    except subprocess.CalledProcessError:
        pass


# ---------------------------------------------------------------------------
# Kanban fixture builder
# ---------------------------------------------------------------------------

def _build_kanban(
    kanban_root: pathlib.Path,
    project_name: str,
    dev_tree: pathlib.Path,
    push_to_remote: str = "true",
    branch_prefix: str = "",
) -> pathlib.Path:
    """
    Build a minimal synthetic kanban root with one registered project.

    Returns the project directory path.
    """
    proj_dir = kanban_root / "projects" / project_name
    (proj_dir / "overwatch" / "backups").mkdir(parents=True)
    (proj_dir / "overwatch" / "actions.log").write_text("", encoding="utf-8")
    (proj_dir / "logs" / "overwatch").mkdir(parents=True)

    # Minimal project.cfg
    cfg_lines = [
        "[project]\n",
        f"project_name={project_name}\n",
        f"dev_tree_path={dev_tree}\n",
        f"push_to_remote={push_to_remote}\n",
    ]
    if branch_prefix:
        cfg_lines.append(f"branch_prefix={branch_prefix}\n")
    (proj_dir / "project.cfg").write_text("".join(cfg_lines), encoding="utf-8")

    # Minimal release-state.md
    (proj_dir / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n", encoding="utf-8"
    )

    return proj_dir


def _action_log(proj_dir: pathlib.Path) -> str:
    """Read the overwatch action log for the project."""
    log_path = proj_dir / "overwatch" / "actions.log"
    if log_path.exists():
        return log_path.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# (A) push_to_remote=false + local-ahead state → staged-by-design, no push
# ---------------------------------------------------------------------------


def test_push_lag_local_mode_staged_by_design(tmp_path: pathlib.Path) -> None:
    """
    Fixture (A): push_to_remote=false + local main ahead of origin/main.

    Expected: no git push is invoked; action log contains 'staged-by-design'.
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    # Build the dev tree: origin repo + clone with one local-ahead commit.
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare")

    _make_git_repo(dev_tree)
    # Point dev_tree at origin as remote and push once so origin/main exists.
    _git(dev_tree, "remote", "add", "origin", str(origin))
    _git(dev_tree, "push", "origin", "HEAD:main")
    _git(dev_tree, "branch", "-M", "main")

    # Add a local-only commit so local main is ahead of origin/main.
    _make_commit(dev_tree, "local-only")

    project_name = "test-project"
    proj_dir = _build_kanban(
        kanban_root,
        project_name,
        dev_tree,
        push_to_remote="false",
        branch_prefix="",
    )

    # Source check-push-lag and invoke it with --dry-run to avoid needing
    # a live overwatch_protocol (which requires the state dir to be set up).
    # In the local mode path, the check exits before reaching the push code
    # regardless of --dry-run, but we include --dry-run as a safety net.
    script = textwrap.dedent(f"""\
        set -euo pipefail

        # Stub overwatch_log_action to append to the project's action log.
        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            echo "${{name}}\t${{action}}\t${{reason}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_halt_first_fix() {{ return 0; }}

        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"

        source {_CHECK_PUSH_LAG}
        overwatch_check_push_lag
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-push-lag should exit 0 in local mode\nstderr={result.stderr}"
    )

    log = _action_log(proj_dir)
    assert "staged-by-design" in log, (
        f"Expected 'staged-by-design' in action log, got:\n{log}\nstderr={result.stderr}"
    )
    # Verify no git push was attempted (origin should still be behind).
    local_sha = _git(dev_tree, "rev-parse", "main")
    origin_sha = _git(dev_tree, "rev-parse", "origin/main")
    assert local_sha != origin_sha, (
        "origin/main should not have been updated (push_to_remote=false)"
    )


# ---------------------------------------------------------------------------
# (B) push_to_remote=true + local-ahead state → push path entered (dry-run)
# ---------------------------------------------------------------------------


def test_push_lag_remote_mode_enters_push_path(tmp_path: pathlib.Path) -> None:
    """
    Fixture (B): push_to_remote=true + local main ahead of origin/main.

    Expected: the push path is entered; action log contains
    'dry-run-push-lag-detected' when --dry-run is passed.
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare")

    _make_git_repo(dev_tree)
    _git(dev_tree, "remote", "add", "origin", str(origin))
    _git(dev_tree, "push", "origin", "HEAD:main")
    _git(dev_tree, "branch", "-M", "main")

    # Add a local-only commit.
    _make_commit(dev_tree, "local-ahead")

    project_name = "test-remote-project"
    proj_dir = _build_kanban(
        kanban_root,
        project_name,
        dev_tree,
        push_to_remote="true",
        branch_prefix="",
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail

        # Stub overwatch_log_action to append to the project's action log.
        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            echo "${{name}}\t${{action}}\t${{reason}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_halt_first_fix() {{ return 0; }}

        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"

        source {_CHECK_PUSH_LAG}
        overwatch_check_push_lag --dry-run
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-push-lag --dry-run should exit 0\nstderr={result.stderr}"
    )

    log = _action_log(proj_dir)
    assert "dry-run-push-lag-detected" in log, (
        f"Expected 'dry-run-push-lag-detected' in action log, got:\n{log}"
        f"\nstderr={result.stderr}"
    )
    # The staged-by-design path must NOT have been taken.
    assert "staged-by-design" not in log, (
        f"push_to_remote=true should not trigger staged-by-design; log:\n{log}"
    )


# ---------------------------------------------------------------------------
# (C) branch_prefix=ai_ → check-orphan-rc-branches detects ai_rc/* branch
# ---------------------------------------------------------------------------


def test_orphan_rc_branches_prefix_aware(tmp_path: pathlib.Path) -> None:
    """
    Fixture (C): branch_prefix=ai_ + orphan branch ai_rc/v1.0.0 with tag ai_v1.0.0.

    Expected: check-orphan-rc-branches detects the orphan via the ai_rc/* pattern.
    Uses --dry-run so no actual git branch deletion is attempted.
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    _make_git_repo(dev_tree)
    # Create the orphan branch and matching prefixed tag.
    _make_branch(dev_tree, "ai_rc/v1.0.0")
    _make_tag(dev_tree, "ai_v1.0.0")

    project_name = "prefix-project"
    proj_dir = _build_kanban(
        kanban_root,
        project_name,
        dev_tree,
        push_to_remote="false",
        branch_prefix="ai_",
    )
    # release-state.md: Active RC = none (so ai_rc/v1.0.0 is not the active RC)
    (proj_dir / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n", encoding="utf-8"
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail

        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            echo "${{name}}\t${{action}}\t${{target}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_halt_first_fix() {{ return 0; }}

        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"

        source {_CHECK_ORPHAN_RC}
        overwatch_check_orphan_rc_branches --dry-run
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-orphan-rc-branches --dry-run should exit 0\nstderr={result.stderr}"
    )

    log = _action_log(proj_dir)
    assert "dry-run-orphan-rc-branch-detected" in log, (
        f"Expected 'dry-run-orphan-rc-branch-detected' in action log, got:\n{log}"
        f"\nstderr={result.stderr}"
    )
    # The detected branch should be ai_rc/v1.0.0 (prefixed form)
    assert "ai_rc/v1.0.0" in log, (
        f"Expected 'ai_rc/v1.0.0' in action log target, got:\n{log}"
    )


# ---------------------------------------------------------------------------
# (D) branch_prefix=ai_ → check-stale-active-rc detects stale state
# ---------------------------------------------------------------------------


def test_stale_active_rc_prefix_aware(tmp_path: pathlib.Path) -> None:
    """
    Fixture (D): branch_prefix=ai_ + release-state.md Active RC=v1.0.0,
    tag ai_v1.0.0 exists, branch ai_rc/v1.0.0 absent.

    Expected: check-stale-active-rc detects stale state with prefixed tag/branch.
    Uses --dry-run so no release-state.md modification is made.
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    _make_git_repo(dev_tree)
    # Tag exists (prefixed): ai_v1.0.0
    _make_tag(dev_tree, "ai_v1.0.0")
    # Branch ai_rc/v1.0.0 does NOT exist (orphan condition)

    project_name = "stale-rc-project"
    proj_dir = _build_kanban(
        kanban_root,
        project_name,
        dev_tree,
        push_to_remote="false",
        branch_prefix="ai_",
    )
    # Set Active RC in release-state.md to v1.0.0 (bare version, not prefixed)
    (proj_dir / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nv1.0.0\n\n## RC Opened At\nnone\n",
        encoding="utf-8",
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail

        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            echo "${{name}}\t${{action}}\t${{reason}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_halt_first_fix() {{ return 0; }}

        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"

        source {_CHECK_STALE_RC}
        overwatch_check_stale_active_rc --dry-run
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-stale-active-rc --dry-run should exit 0\nstderr={result.stderr}"
    )

    log = _action_log(proj_dir)
    assert "dry-run-stale-active-rc-detected" in log, (
        f"Expected 'dry-run-stale-active-rc-detected' in action log, got:\n{log}"
        f"\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# (E) push_to_remote=false + no local-ahead state → exits clean, no log entry
# ---------------------------------------------------------------------------


def test_push_lag_local_mode_no_lag_exits_clean(tmp_path: pathlib.Path) -> None:
    """
    Fixture (E): push_to_remote=false + local matches origin.

    Expected: exits 0 with no action log entry (no lag detected at all).
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare")

    _make_git_repo(dev_tree)
    _git(dev_tree, "remote", "add", "origin", str(origin))
    _git(dev_tree, "push", "origin", "HEAD:main")
    _git(dev_tree, "branch", "-M", "main")
    # No local-ahead commit — local matches origin.

    project_name = "synced-project"
    proj_dir = _build_kanban(
        kanban_root,
        project_name,
        dev_tree,
        push_to_remote="false",
        branch_prefix="",
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail

        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            echo "${{name}}\t${{action}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_halt_first_fix() {{ return 0; }}

        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"

        source {_CHECK_PUSH_LAG}
        overwatch_check_push_lag
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-push-lag should exit 0 when no lag\nstderr={result.stderr}"
    )
    # No action log entry expected — local matches origin, nothing to do.
    log = _action_log(proj_dir)
    assert "staged-by-design" not in log, (
        f"No 'staged-by-design' expected when local matches origin; log:\n{log}"
    )
