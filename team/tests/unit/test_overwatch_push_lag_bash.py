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
  (F) branch_prefix=ai_ in a repository shared with an unprefixed lane: local
      tags v9.0.0 and ai_v9.0.0, main and ai_main both ahead of the fixture
      origin. With push_to_remote = true the check pushes only ai_v9.0.0 and
      ai_main; with push_to_remote unset or false it pushes nothing.
  (G) A release whose push the remote refused (a pre-receive hook on the
      fixture origin): main ahead and the release tag missing there. While
      the remote refuses, the check records push-main-failed and
      push-tag-failed, changes nothing on the remote and returns non-zero;
      once the remote accepts, the next run pushes main and the tag.
  (H) A remote that cannot be read (its URL names a missing repository):
      main ahead and a local release tag. The check writes one
      remote-unreachable row carrying git's error, pushes nothing and
      returns 0.
  (I) The real overwatch_protocol.sh logger (no stub): a reason holding
      newlines, carriage returns or tabs -- git's multi-line push output
      from a refusing remote among them -- is written as one physical,
      six-field row, and a one-line reason is written unchanged.

All tests run against synthetic environments and never touch the live kanban root.
"""

from __future__ import annotations

import pathlib
import re
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
    require_binary("git", label="overwatch-push-lag-bash")


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
    push_to_remote: str | None = "true",
    branch_prefix: str = "",
) -> pathlib.Path:
    """
    Build a minimal synthetic kanban root with one registered project.

    The project is registered in projects.cfg: check-push-lag acts only on a
    registered project whose own project.cfg names its dev tree.

    Returns the project directory path.
    """
    kanban_root.mkdir(parents=True, exist_ok=True)
    (kanban_root / "projects.cfg").write_text(f"[project:{project_name}]\npriority = 1\n", encoding="utf-8")
    proj_dir = kanban_root / "projects" / project_name
    (proj_dir / "overwatch" / "backups").mkdir(parents=True)
    (proj_dir / "overwatch" / "actions.log").write_text("", encoding="utf-8")
    (proj_dir / "logs" / "overwatch").mkdir(parents=True)

    # Minimal project.cfg
    cfg_lines = [
        "[project]\n",
        f"project_name={project_name}\n",
        f"dev_tree_path={dev_tree}\n",
    ]
    if push_to_remote is not None:
        cfg_lines.append(f"push_to_remote={push_to_remote}\n")
    if branch_prefix:
        cfg_lines.append(f"branch_prefix={branch_prefix}\n")
    (proj_dir / "project.cfg").write_text("".join(cfg_lines), encoding="utf-8")

    # Minimal release-state.md
    (proj_dir / "release-state.md").write_text("# Release State\n\n## Active RC\nnone\n", encoding="utf-8")

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
    assert result.returncode == 0, f"check-push-lag should exit 0 in local mode\nstderr={result.stderr}"

    log = _action_log(proj_dir)
    assert "staged-by-design" in log, f"Expected 'staged-by-design' in action log, got:\n{log}\nstderr={result.stderr}"
    # Verify no git push was attempted (origin should still be behind).
    local_sha = _git(dev_tree, "rev-parse", "main")
    origin_sha = _git(dev_tree, "rev-parse", "origin/main")
    assert local_sha != origin_sha, "origin/main should not have been updated (push_to_remote=false)"


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
    assert result.returncode == 0, f"check-push-lag --dry-run should exit 0\nstderr={result.stderr}"

    log = _action_log(proj_dir)
    assert "dry-run-push-lag-detected" in log, (
        f"Expected 'dry-run-push-lag-detected' in action log, got:\n{log}" f"\nstderr={result.stderr}"
    )
    # The staged-by-design path must NOT have been taken.
    assert "staged-by-design" not in log, f"push_to_remote=true should not trigger staged-by-design; log:\n{log}"


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
    (proj_dir / "release-state.md").write_text("# Release State\n\n## Active RC\nnone\n", encoding="utf-8")

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
    assert result.returncode == 0, f"check-orphan-rc-branches --dry-run should exit 0\nstderr={result.stderr}"

    log = _action_log(proj_dir)
    assert "dry-run-orphan-rc-branch-detected" in log, (
        f"Expected 'dry-run-orphan-rc-branch-detected' in action log, got:\n{log}" f"\nstderr={result.stderr}"
    )
    # The detected branch should be ai_rc/v1.0.0 (prefixed form)
    assert "ai_rc/v1.0.0" in log, f"Expected 'ai_rc/v1.0.0' in action log target, got:\n{log}"


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
    assert result.returncode == 0, f"check-stale-active-rc --dry-run should exit 0\nstderr={result.stderr}"

    log = _action_log(proj_dir)
    assert "dry-run-stale-active-rc-detected" in log, (
        f"Expected 'dry-run-stale-active-rc-detected' in action log, got:\n{log}" f"\nstderr={result.stderr}"
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
    assert result.returncode == 0, f"check-push-lag should exit 0 when no lag\nstderr={result.stderr}"
    # No action log entry expected — local matches origin, nothing to do.
    log = _action_log(proj_dir)
    assert "staged-by-design" not in log, f"No 'staged-by-design' expected when local matches origin; log:\n{log}"


# ---------------------------------------------------------------------------
# (F) A shared repository: the ai_ lane pushes only its own main and tags
# ---------------------------------------------------------------------------


def _shared_lane_repo(tmp_path: pathlib.Path) -> tuple:
    """A dev tree shared by two lanes, both mains ahead of a fixture origin.

    Returns (dev_tree, origin, {ref: sha before the check}).
    """
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare")
    dev_tree = tmp_path / "dev"
    _make_git_repo(dev_tree)
    _git(dev_tree, "branch", "-M", "main")
    _git(dev_tree, "remote", "add", "origin", str(origin))
    _make_branch(dev_tree, "ai_main")
    _git(dev_tree, "push", "origin", "main", "ai_main")
    _make_commit(dev_tree, "bare-lane-work")
    _make_tag(dev_tree, "v9.0.0")
    _git(dev_tree, "checkout", "-q", "ai_main")
    _make_commit(dev_tree, "ai-lane-work")
    _make_tag(dev_tree, "ai_v9.0.0")
    before = {ref: _git(origin, "rev-parse", ref) for ref in ("refs/heads/main", "refs/heads/ai_main")}
    return dev_tree, origin, before


def _run_live_push_lag(tmp_path, kanban_root, project_name, proj_dir):
    script = textwrap.dedent(f"""\
        set -euo pipefail
        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            printf '%s\\t%s\\t%s\\t%s\\n' "${{name}}" "${{action}}" "${{target}}" "${{reason}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_halt_first_fix() {{ "$1"; }}
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_PUSH_LAG}
        overwatch_check_push_lag
    """)
    return run_bash(tmp_path, script)


def _origin_tags(origin: pathlib.Path) -> set:
    out = _git(origin, "tag", "--list")
    return set(out.split()) if out else set()


def test_push_lag_pushes_only_the_prefixed_lanes_main_and_release_tag(tmp_path: pathlib.Path) -> None:
    dev_tree, origin, before = _shared_lane_repo(tmp_path)
    kanban_root = tmp_path / "kanban"
    project_name = "ai-lane"
    proj_dir = _build_kanban(kanban_root, project_name, dev_tree, push_to_remote="true", branch_prefix="ai_")

    result = _run_live_push_lag(tmp_path, kanban_root, project_name, proj_dir)

    assert result.returncode == 0, result.stderr
    assert _origin_tags(origin) == {"ai_v9.0.0"}, "only the lane's own release tag is published"
    assert _git(origin, "rev-parse", "refs/heads/ai_main") == _git(dev_tree, "rev-parse", "ai_main")
    assert (
        _git(origin, "rev-parse", "refs/heads/main") == before["refs/heads/main"]
    ), "the other lane's main is never pushed"
    log = _action_log(proj_dir)
    assert "pushed-main-to-origin\tai_main" in log and "pushed-tag-to-origin\tai_v9.0.0" in log, log
    assert "v9.0.0\t" not in log.replace("ai_v9.0.0\t", "")


@pytest.mark.parametrize("push_to_remote", [None, "false"], ids=["unset", "false"])
def test_push_lag_pushes_nothing_unless_push_to_remote_is_true(tmp_path: pathlib.Path, push_to_remote) -> None:
    dev_tree, origin, before = _shared_lane_repo(tmp_path)
    kanban_root = tmp_path / "kanban"
    project_name = "ai-lane"
    proj_dir = _build_kanban(kanban_root, project_name, dev_tree, push_to_remote=push_to_remote, branch_prefix="ai_")

    result = _run_live_push_lag(tmp_path, kanban_root, project_name, proj_dir)

    assert result.returncode == 0, result.stderr
    assert _origin_tags(origin) == set()
    for ref, sha in before.items():
        assert _git(origin, "rev-parse", ref) == sha
    assert "staged-by-design" in _action_log(proj_dir)


# ---------------------------------------------------------------------------
# (G) a release push the remote refused is pushed once the remote accepts
# ---------------------------------------------------------------------------


def _release_behind_a_refusing_origin(tmp_path: pathlib.Path) -> tuple:
    """A dev tree whose main is one release commit ahead of its origin, with
    the release tag v1.0.0 local only, and an origin whose pre-receive hook
    refuses every push (ls-remote still reads it).

    Returns (dev_tree, origin, the hook's path, origin's main before the release).
    """
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare")
    dev_tree = tmp_path / "dev"
    _make_git_repo(dev_tree)
    _git(dev_tree, "branch", "-M", "main")
    _git(dev_tree, "remote", "add", "origin", str(origin))
    _git(dev_tree, "push", "origin", "main")
    origin_main_before = _git(origin, "rev-parse", "refs/heads/main")
    # The release: one commit and its tag, cut locally while the remote refuses pushes.
    _make_commit(dev_tree, "release-commit")
    _make_tag(dev_tree, "v1.0.0")
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'fixture remote: rejected' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    return dev_tree, origin, hook, origin_main_before


def test_push_lag_left_by_a_refused_release_push_is_pushed_once_the_remote_accepts(tmp_path: pathlib.Path) -> None:
    dev_tree, origin, hook, origin_main_before = _release_behind_a_refusing_origin(tmp_path)
    kanban_root = tmp_path / "kanban"
    project_name = "release-lane"
    proj_dir = _build_kanban(kanban_root, project_name, dev_tree, push_to_remote="true")

    refused = _run_live_push_lag(tmp_path, kanban_root, project_name, proj_dir)

    assert refused.returncode != 0, refused.stderr
    assert "local main is 1 commit(s) ahead of origin/main" in refused.stderr
    assert "unpushed tag: v1.0.0" in refused.stderr
    log = _action_log(proj_dir)
    assert "push-main-failed\tmain" in log and "push-tag-failed\tv1.0.0" in log, log
    assert _git(origin, "rev-parse", "refs/heads/main") == origin_main_before
    assert _origin_tags(origin) == set()

    hook.unlink()
    accepted = _run_live_push_lag(tmp_path, kanban_root, project_name, proj_dir)

    assert accepted.returncode == 0, accepted.stderr
    assert _git(origin, "rev-parse", "refs/heads/main") == _git(dev_tree, "rev-parse", "main")
    assert _origin_tags(origin) == {"v1.0.0"}
    log = _action_log(proj_dir)
    assert "pushed-main-to-origin\tmain" in log and "pushed-tag-to-origin\tv1.0.0" in log, log


# ---------------------------------------------------------------------------
# (H) an unreadable remote is one remote-unreachable row, never a push
# ---------------------------------------------------------------------------


def test_push_lag_unreadable_remote_records_one_row_and_pushes_nothing(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "missing-origin.git"
    dev_tree = tmp_path / "dev"
    _make_git_repo(dev_tree)
    _git(dev_tree, "branch", "-M", "main")
    _git(dev_tree, "remote", "add", "origin", str(missing))
    _git(dev_tree, "update-ref", "refs/remotes/origin/main", "HEAD")
    _make_commit(dev_tree, "release-commit")
    _make_tag(dev_tree, "v1.0.0")
    kanban_root = tmp_path / "kanban"
    project_name = "unreadable-remote"
    proj_dir = _build_kanban(kanban_root, project_name, dev_tree, push_to_remote="true")

    result = _run_live_push_lag(tmp_path, kanban_root, project_name, proj_dir)

    assert result.returncode == 0, result.stderr
    assert not missing.exists(), "nothing may be pushed to the unreadable remote"
    rows = [line.split("\t") for line in _action_log(proj_dir).splitlines() if line]
    assert [row[:3] for row in rows] == [["check-push-lag", "remote-unreachable", "origin"]], rows
    assert str(missing) in rows[0][3] and "main ahead=1" in rows[0][3], rows[0][3]
    assert "unpushed tag:" not in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# (I) the real logger writes one physical, six-field row per action
# ---------------------------------------------------------------------------

_ROW_START_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\t")


def _action_log_rows(proj_dir: pathlib.Path) -> list:
    """The action log split on LF only -- one entry per physical line --
    after checking that each line starts with a timestamp and has exactly
    six tab-separated fields. Read as bytes: universal newlines would turn
    a written CR into LF."""
    log = (proj_dir / "overwatch" / "actions.log").read_bytes().decode("utf-8")
    assert log.endswith("\n") and "\r" not in log, repr(log)
    lines = log[:-1].split("\n")
    for line in lines:
        assert _ROW_START_RE.match(line), f"a line that is not a record: {line!r}\nlog:\n{log}"
        assert len(line.split("\t")) == 6, f"a record without exactly six fields: {line!r}"
    return [line.split("\t") for line in lines]


def test_push_lag_refused_by_the_remote_logs_one_row_per_action_through_the_real_logger(
    tmp_path: pathlib.Path,
) -> None:
    dev_tree, _origin, _hook, _before = _release_behind_a_refusing_origin(tmp_path)
    kanban_root = tmp_path / "kanban"
    project_name = "refused-lane"
    proj_dir = _build_kanban(kanban_root, project_name, dev_tree, push_to_remote="true")
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_PROTOCOL_SH}
        overwatch_halt_first_fix() {{ "$@"; }}
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_PUSH_LAG}
        overwatch_check_push_lag
    """)

    result = run_bash(tmp_path, script)

    assert result.returncode != 0, result.stderr
    rows = _action_log_rows(proj_dir)
    assert [(row[1], row[2], row[3]) for row in rows] == [
        ("check-push-lag", "main", "push-main-failed"),
        ("check-push-lag", "v1.0.0", "push-tag-failed"),
    ], rows
    for row in rows:
        assert "[remote rejected]" in row[5], row[5]
        assert "fixture remote: rejected" in row[5], row[5]


_MULTI_LINE_REASON = (
    "remote: pushes are frozen\r\n"
    "To /srv/origin.git\n"
    " ! [remote rejected] main -> main (pre-receive hook declined)\n"
    "error:\tfailed to push some refs\n"
)
_ONE_LINE_REASON = " already one line,  inner  spacing kept "


@pytest.mark.parametrize(
    ("reason", "written"),
    [
        (
            _MULTI_LINE_REASON,
            "remote: pushes are frozen To /srv/origin.git ! [remote rejected] main -> main "
            "(pre-receive hook declined) error: failed to push some refs",
        ),
        (_ONE_LINE_REASON, _ONE_LINE_REASON),
    ],
    ids=["newline-cr-tab-flattened", "one-line-unchanged"],
)
def test_overwatch_log_action_writes_one_six_field_row_whatever_the_reason_holds(
    tmp_path: pathlib.Path, reason: str, written: str
) -> None:
    proj_dir = tmp_path / "kanban" / "projects" / "demo"
    (proj_dir / "overwatch").mkdir(parents=True)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_PROTOCOL_SH}
        export KANBAN_ROOT="{tmp_path / "kanban"}"
        export OVERWATCH_PROJECT="demo"
        overwatch_log_action check-push-lag main push-main-failed none "$REASON"
    """)

    result = run_bash(tmp_path, script, extra_env={"REASON": reason})

    assert result.returncode == 0, result.stderr
    rows = _action_log_rows(proj_dir)
    assert len(rows) == 1, rows
    assert rows[0][1:5] == ["check-push-lag", "main", "push-main-failed", "none"]
    assert rows[0][5] == written
    assert rows[0][5].split() == reason.split(), "every word of every original line is kept, in order"


_BROKEN_FIELD = "first\tpart\r\nsecond  part\n"
_FLATTENED_FIELD = "first part second part"
_WRITER_ARGS = {"name": "check-push-lag", "target": "main", "action": "push-main-failed", "backup_path": "none"}


@pytest.mark.parametrize("field", list(_WRITER_ARGS))
def test_overwatch_log_action_writes_one_six_field_row_whatever_any_field_holds(
    tmp_path: pathlib.Path, field: str
) -> None:
    proj_dir = tmp_path / "kanban" / "projects" / "demo"
    (proj_dir / "overwatch").mkdir(parents=True)
    args = dict(_WRITER_ARGS, **{field: _BROKEN_FIELD})
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_PROTOCOL_SH}
        export KANBAN_ROOT="{tmp_path / "kanban"}"
        export OVERWATCH_PROJECT="demo"
        overwatch_log_action "$NAME" "$TARGET" "$ACTION" "$BACKUP_PATH" "one line"
    """)

    result = run_bash(tmp_path, script, extra_env={key.upper(): value for key, value in args.items()})

    assert result.returncode == 0, result.stderr
    rows = _action_log_rows(proj_dir)
    assert len(rows) == 1, rows
    expected = dict(args, **{field: _FLATTENED_FIELD})
    assert rows[0][1:] == [expected[key] for key in _WRITER_ARGS] + ["one line"]
