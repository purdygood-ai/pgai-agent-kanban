"""
test_release_summary_origin_actions.py
======================================
The release finalizer's closing summary reports the origin and branch
actions that actually ran, not the ones a fully remote-backed release would
have run.

Each test drives the real ``cm_release_finalize`` from
``team/scripts/lib/cm/release_finalize.sh`` end to end over a throwaway git
repository whose ``origin`` is a bare repository under pytest's ``tmp_path``
(inside the framework temp root, so a fixture push stays local). Only the
finalizer's collaborators that live in other stage files are stubbed: the
hook-step callbacks, ``git_step``, ``cm_halt``, the campaign/HALT-AFTER spend
helpers and the project-path lookups. The ship-posture stage is sourced as
shipped, so its Auto-Ship Event append runs with no event recorded, as it
does on every release that shipped without the posture waving a stop
through. Every git step, the release-state rewrite and the summary itself
run as shipped.

What is pinned here:
  - ``push_to_remote=false``: no summary line claims a push to, or a deletion
    from, origin; the summary says the release is local only; and origin is
    in fact untouched (its RC branch survives, the tag never reaches it).
  - ``push_to_remote=true`` with a stub remote: the summary says the tag was
    pushed to origin and the RC branch deleted from origin, and both are
    true of the stub remote afterwards.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest
from tests.fixtures.binary_guard import require_binary
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_RELEASE_FINALIZE_SH = _TEAM_DIR / "scripts" / "lib" / "cm" / "release_finalize.sh"
_RELEASE_ORIGIN_SUMMARY_SH = _TEAM_DIR / "scripts" / "lib" / "cm" / "release_origin_summary.sh"
_RELEASE_SHIP_POSTURE_SH = _TEAM_DIR / "scripts" / "lib" / "cm" / "release_ship_posture.sh"

_VERSION = "v1.2.0"
_RC_BRANCH = f"rc/{_VERSION}"
_MAIN = "main"
_PROJECT = "demo"


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Every test here builds and inspects a real git repository."""
    require_binary("git", label="release-summary-origin-actions")


def _git(repo: pathlib.Path, *args: str) -> str:
    """Run git with -C *repo* and return stripped stdout; raise on failure."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _build_repo_with_origin(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A dev repo at a post-squash state and a bare origin holding main and the RC branch.

    main carries the release commit with VERSION already written (the state
    the finalizer starts from) one commit ahead of origin's main; the RC
    branch exists locally and on origin.
    Returns (repo, origin).
    """
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(origin)], capture_output=True, check=True)
    subprocess.run(
        ["git", "init", f"--initial-branch={_MAIN}", str(repo)],
        capture_output=True, check=True,
    )
    _git(repo, "config", "--local", "user.email", "test@pgai.local")
    _git(repo, "config", "--local", "user.name", "PGAI Test")
    (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial commit")
    _git(repo, "branch", _RC_BRANCH)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", _MAIN, _RC_BRANCH)
    (repo / "VERSION").write_text(_VERSION.lstrip("v") + "\n", encoding="utf-8")
    _git(repo, "add", "VERSION")
    _git(repo, "commit", "-m", "Release commit")
    return repo, origin


def _build_kanban_root(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A kanban root laid out like the install, with an idle-able release-state.md.

    ``scripts/`` and the ``pgai_agent_kanban`` package are linked from this
    tree so the finalizer's own helpers (write_rc_state.py, pp_run_ops)
    resolve. Returns (kanban_root, release_state_path).
    """
    root = tmp_path / "kanban"
    project_root = root / "projects" / _PROJECT
    (project_root / "bugs").mkdir(parents=True)
    (root / "scripts").symlink_to(_TEAM_DIR / "scripts", target_is_directory=True)
    (root / "pgai_agent_kanban").symlink_to(
        _TEAM_DIR / "pgai_agent_kanban", target_is_directory=True
    )
    release_state = project_root / "release-state.md"
    release_state.write_text(
        "# Release State\n\n"
        f"## Active RC\n{_VERSION}\n\n"
        "## RC Opened At\n2026-01-01T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-fixture\n",
        encoding="utf-8",
    )
    return root, release_state


def _run_finalizer(tmp_path: pathlib.Path, push_to_remote: str):
    """Run cm_release_finalize over a fresh fixture; return (result, repo, origin)."""
    repo, origin = _build_repo_with_origin(tmp_path)
    root, release_state = _build_kanban_root(tmp_path)
    project_root = root / "projects" / _PROJECT

    script = f"""
set -euo pipefail
unset PYTHONPATH
export KANBAN_ROOT={root}
source {root / 'scripts' / 'lib' / 'pp_run_ops.sh'}
source {_RELEASE_ORIGIN_SUMMARY_SH}
source {_RELEASE_SHIP_POSTURE_SH}
source {_RELEASE_FINALIZE_SH}

# Collaborators that live in other release stage files.
cm_halt() {{ echo "HALT: $1" >&2; exit 1; }}
git_step() {{ shift; "$@" || {{ echo "git_step failed: $*" >&2; exit 1; }}; }}
_fixture_pre_tag_hook() {{ :; }}
_fixture_post_tag_hook() {{ :; }}
cm_release_spend_campaign_file() {{ :; }}
cm_release_spend_halt_after_sentinel() {{ :; }}
pp_prefix_tag() {{ echo "$2"; }}
pp_strip_prefix_from_tag() {{ echo "${{2#v}}"; }}
pp_project_root() {{ echo {project_root}; }}
pp_git_remote_name() {{ echo origin; }}

PROJECT_NAME={_PROJECT}
REPO_ROOT={repo}
MAIN_BRANCH={_MAIN}
RC_BRANCH={_RC_BRANCH}
ACTIVE_RC={_VERSION}
RELEASE_STATE={release_state}
PROJECT_HOOKS_DIR={root / 'hooks'}
_CM_PROJECT_ROOT={project_root}
_CM_PUSH_TO_REMOTE={push_to_remote}
_CM_AUTORESOLVE_LOG=""
_CLEAN_RELEASE_VERSION={_VERSION.lstrip('v')}

cm_release_finalize _fixture_pre_tag_hook _fixture_post_tag_hook
"""
    result = run_bash(tmp_path, script, cwd=tmp_path, timeout=120)
    return result, repo, origin


def _summary_block(stdout: str) -> str:
    """The closing summary: everything from the 'Summary of what was done' line on."""
    marker = "Summary of what was done:"
    assert marker in stdout, f"closing summary missing from output:\n{stdout}"
    return stdout[stdout.index(marker):]


def _origin_has_ref(origin: pathlib.Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(origin), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True, text=True,
    )
    return result.returncode == 0


class TestLocalOnlyReleaseSummary:
    """push_to_remote=false: the summary claims nothing about origin it did not do."""

    def test_summary_claims_no_origin_push_or_delete_and_says_local_only(
        self, tmp_path: pathlib.Path
    ) -> None:
        result, repo, origin = _run_finalizer(tmp_path, "false")
        output = result.stdout + result.stderr
        assert result.returncode == 0, output

        summary = _summary_block(result.stdout)
        assert "pushed to origin" not in summary, summary
        assert "deleted from origin" not in summary, summary
        assert "local only" in summary, summary
        assert f"Tag {_VERSION} created locally only" in summary, summary
        assert f"{_RC_BRANCH} deleted locally; origin not touched" in summary, summary

    def test_origin_is_untouched_by_a_local_only_release(
        self, tmp_path: pathlib.Path
    ) -> None:
        result, repo, origin = _run_finalizer(tmp_path, "false")
        assert result.returncode == 0, result.stdout + result.stderr

        assert _origin_has_ref(origin, f"refs/heads/{_RC_BRANCH}"), (
            "a local-only release must leave the RC branch on origin"
        )
        assert not _origin_has_ref(origin, f"refs/tags/{_VERSION}"), (
            "a local-only release must not push the tag to origin"
        )
        assert _git(repo, "tag", "--list", _VERSION) == _VERSION


class TestRemoteBackedReleaseSummary:
    """push_to_remote=true with a stub remote: the summary reports the pushes that ran."""

    def test_summary_reports_tag_pushed_and_rc_deleted_from_origin(
        self, tmp_path: pathlib.Path
    ) -> None:
        result, repo, origin = _run_finalizer(tmp_path, "true")
        output = result.stdout + result.stderr
        assert result.returncode == 0, output

        summary = _summary_block(result.stdout)
        assert f"Tag {_VERSION} created locally and pushed to origin" in summary, summary
        assert f"{_RC_BRANCH} deleted from origin; deleted locally" in summary, summary
        assert f"{_MAIN} PUSHED, tags PUSHED" in summary, summary
        assert "local only" not in summary, summary

        assert _origin_has_ref(origin, f"refs/tags/{_VERSION}"), (
            "the summary says the tag reached origin, so the stub remote must hold it"
        )
        assert not _origin_has_ref(origin, f"refs/heads/{_RC_BRANCH}"), (
            "the summary says the RC branch was deleted from origin, so the stub remote must not hold it"
        )
