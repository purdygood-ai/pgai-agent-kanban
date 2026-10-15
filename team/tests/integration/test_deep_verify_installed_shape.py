"""
test_deep_verify_installed_shape.py
=====================================
Integration coverage for the installed-kanban-root tree-state resolution: a
deep-verify run invoked from an *installed* kanban root -- team/scripts/
deep-verify.sh copied to $KANBAN_ROOT/scripts/ with no repository checkout
above it, exactly the shape the shipped weekly cron entry (team/scripts/
cron-suggested.template.txt) runs against -- must record the version and
commit of the tree the cold lane actually built, never the literal string
"unknown" when a resolvable dev tree exists.

A naive resolution that derives the tree purely from the script's own
directory (team/scripts/../..) gets this right in a checkout (../.. lands on
the repo root) but wrong from an install (../.. lands on the *parent* of the
kanban root, which has neither a VERSION file nor a git repository) -- both
fallbacks miss and the ledger records "unknown" for both fields, a record
the pre-release freshness gate (deep-verify-freshness-check.sh) then refuses
forever, because "unknown" can never equal the version being promoted.

This file builds an install-shaped sandbox -- a scripts/ directory (plus the
pgai_agent_kanban/ package and config/ it depends on) with no repository
above it -- points PGAI_DEV_TREE_PATH at a separate, real dev-tree checkout
the fixture builds for itself (a fresh git repo carrying a VERSION file),
and asserts the ledger record names that tree by both version and commit.
It then feeds that exact record to deep-verify-freshness-check.sh, run from
the same install-shaped root, and asserts the gate accepts it -- proving the
remedy an unknown record would otherwise make unreachable (re-running the
same installed verb producing another unknown record) is now reachable.

No test invokes a real container build -- the cold lane is stubbed via the
PGAI_DEEP_VERIFY_COLD_LANE_CMD seam, the same idiom test_deep_verify_run.py
uses. tmp_path is redirected under $PGAI_AGENT_KANBAN_TEMP_DIR by
team/tests/conftest.py's pytest_configure, so every fixture path this file
creates already satisfies the framework's temp-file hygiene rule. Nothing
here reads or writes the live install, any real project's deep-verify
ledger, or any real RC -- the "dev tree" this file resolves the record
against is a synthetic git repo built fresh under tmp_path, never the
checkout the test suite itself runs from.

Test names describe behaviour under test, never bug/task IDs (team/roles/TESTER.md,
Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import scrub_agent_scoped_env


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="deep-verify-installed-shape")

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's own location.
# test file:  team/tests/integration/test_deep_verify_installed_shape.py
# team/:      .../team
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent

_STUB_GREEN = """\
#!/usr/bin/env bash
echo "[cold-build] [docker] [claude-code] Image built successfully: tag:test"
echo "Cold-build lane PASSED"
exit 0
"""


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _build_install_shaped_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build a kanban root shaped like install.sh's output: a scripts/
    directory (plus the pgai_agent_kanban/ package and config/ it needs)
    with NO repository checkout above it -- the exact layout the shipped
    weekly cron entry runs deep-verify.sh against."""
    root = tmp_path / "kanban_installed"
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    shutil.copytree(_TEAM_DIR / "pgai_agent_kanban", root / "pgai_agent_kanban")
    if (_TEAM_DIR / "config").is_dir():
        shutil.copytree(_TEAM_DIR / "config", root / "config")
    (root / "projects").mkdir(parents=True, exist_ok=True)
    return root


def _build_dev_tree(tmp_path: pathlib.Path, version: str) -> tuple[pathlib.Path, str]:
    """Build a small, real git repository under tmp_path carrying a VERSION
    file, standing in for the operator-configured dev-tree checkout a real
    install points PGAI_DEV_TREE_PATH / kanban.cfg's [paths] dev_tree_path
    at. Returns (path, short-commit-sha)."""
    dev_tree = tmp_path / "dev_tree_checkout"
    dev_tree.mkdir(parents=True, exist_ok=True)
    (dev_tree / "VERSION").write_text(f"{version}\n", encoding="utf-8")

    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "Test Fixture"
    env["GIT_AUTHOR_EMAIL"] = "fixture@example.invalid"
    env["GIT_COMMITTER_NAME"] = "Test Fixture"
    env["GIT_COMMITTER_EMAIL"] = "fixture@example.invalid"
    subprocess.run(["git", "init", "--quiet"], cwd=str(dev_tree), env=env, check=True)
    subprocess.run(["git", "add", "VERSION"], cwd=str(dev_tree), env=env, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "seed VERSION for installed-shape fixture"],
        cwd=str(dev_tree),
        env=env,
        check=True,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(dev_tree),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return dev_tree, commit


def _write_stub_green(tmp_path: pathlib.Path) -> pathlib.Path:
    stub = tmp_path / "stub_green.sh"
    stub.write_text(_STUB_GREEN, encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _register_project(install_root: pathlib.Path, project: str) -> pathlib.Path:
    (install_root / "projects.cfg").write_text(
        f"[project:{project}]\npriority = 1\n", encoding="utf-8"
    )
    project_dir = install_root / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.cfg").write_text(
        f"[project]\nproject_name = {project}\n", encoding="utf-8"
    )
    return project_dir


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_installed_shape_run_records_the_configured_dev_tree_and_satisfies_freshness_check(
    tmp_path: pathlib.Path,
) -> None:
    install_root = _build_install_shaped_root(tmp_path)
    project = "demo-app"
    _register_project(install_root, project)

    dev_tree, expected_commit = _build_dev_tree(tmp_path, "v9.9.9")
    stub = _write_stub_green(tmp_path)

    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(install_root)
    env["PGAI_DEV_TREE_PATH"] = str(dev_tree)
    env["PGAI_DEEP_VERIFY_COLD_LANE_CMD"] = str(stub)
    # The stubbed lane stands in for the container tier, which runs only
    # with operator authorization and never inside an agent wake.
    scrub_agent_scoped_env(env)
    env["PGAI_CONTAINER_TIER"] = "1"
    env.pop("PGAI_PROJECT_NAME", None)

    # --- Run the verb the way the installed cron entry's non-scheduled
    # sibling (an on-demand operator invocation) runs it: as
    # $KANBAN_ROOT/scripts/deep-verify.sh --project <name>, from a script
    # whose own directory has no repository above it. ---
    result = subprocess.run(
        ["bash", str(install_root / "scripts" / "deep-verify.sh"), "--project", project, "--image", "all"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

    ledger_text = (install_root / "projects" / project / "deep-verify-ledger.md").read_text(
        encoding="utf-8"
    )

    # The ledger must never record the literal "unknown" sentinel when a
    # resolvable dev tree exists.
    assert "unknown" not in ledger_text

    header_match = re.search(r"## Deep-Verify Run: \S+ — demo-app — (\S+)", ledger_text)
    assert header_match, ledger_text
    recorded_version = header_match.group(1)
    assert recorded_version == "v9.9.9"

    commit_match = re.search(r"- Commit: (\S+)", ledger_text)
    assert commit_match, ledger_text
    recorded_commit = commit_match.group(1)

    # The recorded Commit must equal `git -C "$PGAI_DEV_TREE_PATH" rev-parse
    # --short HEAD` for the tree the lane was actually pointed at -- not
    # merely some tree that happens to be resolvable.
    assert recorded_commit == expected_commit
    independently_computed_commit = subprocess.run(
        ["git", "-C", str(dev_tree), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert recorded_commit == independently_computed_commit

    # --- Feed that record to the pre-release promotion gate, run from the
    # same installed-shape root, and confirm it accepts a record produced by
    # an installed invocation instead of refusing it as stale/unknown. ---
    freshness_result = subprocess.run(
        [
            "bash",
            str(install_root / "scripts" / "deep-verify-freshness-check.sh"),
            "--project",
            project,
            "--version",
            recorded_version,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert freshness_result.returncode == 0, freshness_result.stderr
    assert "OK to promote" in freshness_result.stdout
