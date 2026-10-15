"""
test_finalize_release_github_release_gate.py
============================================
Behavioural tests for whether cm/finalize-release.sh creates a GitHub
release, depending on the project's ``[project] push_to_remote`` setting.

Contract under test: a GitHub release publishes the version off-host, so it
follows the same push_to_remote gate as the branch and tag pushes.

  - ``push_to_remote = false``: gh is never asked to create a release (even
    when it is installed and authenticated), the script narrates
    ``[push_to_remote=false] skipping GitHub release`` and the summary reports
    the release as skipped for that reason.
  - ``push_to_remote = true``: with an authenticated gh, exactly one
    ``gh release create`` call is made for the version, as before.

Design:
  - gh is a PATH stub that records every argv it receives to a log file and
    exits 0 for every subcommand, so ``gh auth status`` reads as
    authenticated. The real gh CLI is never reached: the stub directory is
    first on PATH and the environment is rebuilt with ``build_scrubbed_env``.
  - Every path lives under pytest's ``tmp_path``: a throwaway kanban root, a
    local dev repository on ``main`` carrying the release tag and its
    committed release notes, and a bare ``origin`` repository. The
    push_to_remote=true case pushes into that bare repository only, which is
    a destination inside the framework temp root.
  - The subprocess runs in its own process group, killed whole on timeout.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys

import pytest
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_FINALIZE = _TEAM_DIR / "scripts" / "cm" / "finalize-release.sh"

_PROJECT = "gh-release-gate-fixture"
_VERSION = "v0.1.0"
_TIMEOUT_SECONDS = 60
_SKIP_NARRATION = "[push_to_remote=false] skipping GitHub release"

# A gh stand-in: append one tab-separated argv line per invocation to the log
# named by GH_STUB_LOG, then succeed. Exiting 0 for `auth status` is what makes
# the script treat gh as installed and authenticated.
_GH_STUB = """#!/usr/bin/env bash
( IFS=$'\\t'; printf '%s\\n' "$*" ) >> "${GH_STUB_LOG:?GH_STUB_LOG must be set}"
exit 0
"""


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip the module when git is absent; every test builds a git repo."""
    require_binary("git", label="finalize-release-github-release-gate")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def _build_repos(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A dev repo on ``main`` tagged _VERSION, with a bare ``origin`` beside it."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(origin)],
        capture_output=True, text=True, check=True,
    )
    repo = tmp_path / "dev_repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "--local", "user.email", "test@pgai.local")
    _git(repo, "config", "--local", "user.name", "PGAI Test")
    (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
    notes = repo / "release-notes"
    notes.mkdir()
    (notes / f"{_VERSION}.md").write_text(f"# {_VERSION}\n\nFixture notes.\n", encoding="utf-8")
    _git(repo, "add", "README.md", f"release-notes/{_VERSION}.md")
    _git(repo, "commit", "-m", "Initial commit")
    _git(repo, "tag", _VERSION)
    _git(repo, "remote", "add", "origin", str(origin))
    return repo, origin


def _build_kanban_root(
    tmp_path: pathlib.Path, repo: pathlib.Path, push_to_remote: str
) -> pathlib.Path:
    """A throwaway kanban root with one project using *push_to_remote*."""
    root = tmp_path / "kanban_root"
    proj = root / "projects" / _PROJECT
    (proj / "tasks" / "queues").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "tmp").mkdir()
    (root / "kanban.cfg").write_text("[paths]\n", encoding="utf-8")
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8"
    )
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {_PROJECT}\n"
        f"dev_tree_path = {repo}\n"
        "workflow_type = release\n"
        f"push_to_remote = {push_to_remote}\n",
        encoding="utf-8",
    )
    return root


def _install_gh_stub(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Write the gh stub into its own bin dir; return (bin_dir, argv_log)."""
    bin_dir = tmp_path / "stub_bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(_GH_STUB, encoding="utf-8")
    gh.chmod(0o755)
    log = tmp_path / "gh_calls.log"
    log.touch()
    return bin_dir, log


# ---------------------------------------------------------------------------
# Running the entry point
# ---------------------------------------------------------------------------


def _run_finalize(tmp_path: pathlib.Path, push_to_remote: str):
    """Run finalize-release.sh against a fresh sandbox; return its result and gh calls."""
    repo, origin = _build_repos(tmp_path)
    root = _build_kanban_root(tmp_path, repo, push_to_remote)
    stub_bin, gh_log = _install_gh_stub(tmp_path)
    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir()
    (tmp_path / "home").mkdir()
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        # The gh stub first, then the interpreter running this suite so any
        # python3 the script reaches has the version the tree is written for.
        extra_path_dirs=[stub_bin, pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "GH_STUB_LOG": str(gh_log),
        },
    )
    argv = ["bash", str(_FINALIZE), "--project", _PROJECT, "--yes", _VERSION]
    proc = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=str(foreign_cwd), start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    calls = [line.split("\t") for line in gh_log.read_text(encoding="utf-8").splitlines()]
    return proc.returncode, stdout, stderr, calls, origin


def _release_create_calls(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if c[:2] == ["release", "create"]]


def _diagnostic(rc: int, stdout: str, stderr: str, calls: list[list[str]]) -> str:
    return (
        f"exit={rc}\ngh calls={calls!r}\n"
        f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_local_only_project_creates_no_github_release(tmp_path: pathlib.Path) -> None:
    """push_to_remote=false: an authenticated gh is never asked to create a release."""
    rc, stdout, stderr, calls, origin = _run_finalize(tmp_path, "false")
    diag = _diagnostic(rc, stdout, stderr, calls)

    assert rc == 0, f"finalize-release should complete locally.\n{diag}"
    assert _release_create_calls(calls) == [], (
        f"A local-only project must not create a GitHub release.\n{diag}"
    )
    assert _SKIP_NARRATION in stdout, (
        f"The skipped GitHub release step must be narrated with {_SKIP_NARRATION!r}.\n{diag}"
    )
    assert "GitHub release: skipped (push_to_remote=false)" in stdout, (
        f"The summary must report the GitHub release as skipped by push policy.\n{diag}"
    )
    assert _git(origin, "for-each-ref").strip() == "", (
        f"A local-only project must leave origin untouched.\n{diag}"
    )


def test_pushing_project_creates_exactly_one_github_release(tmp_path: pathlib.Path) -> None:
    """push_to_remote=true: an authenticated gh gets one release create for the version."""
    rc, stdout, stderr, calls, origin = _run_finalize(tmp_path, "true")
    diag = _diagnostic(rc, stdout, stderr, calls)

    assert rc == 0, f"finalize-release should complete.\n{diag}"
    creates = _release_create_calls(calls)
    assert len(creates) == 1, (
        f"A pushing project must create exactly one GitHub release.\n{diag}"
    )
    assert creates[0][2] == _VERSION, (
        f"The GitHub release must be created for {_VERSION}.\n{diag}"
    )
    assert _SKIP_NARRATION not in stdout, (
        f"A pushing project must not narrate the GitHub release as skipped.\n{diag}"
    )
    assert "GitHub release: created" in stdout, (
        f"The summary must report the GitHub release as created.\n{diag}"
    )
    assert _git(origin, "tag", "--list", _VERSION).strip() == _VERSION, (
        f"The release tag must reach the fixture origin.\n{diag}"
    )
