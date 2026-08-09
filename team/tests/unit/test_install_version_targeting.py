"""
test_install_version_targeting.py
===================================
Gated fixtures for install.sh's version-targeted fresh-install behavior
(requirements/v2.2.0-version-targeted-install-upgrade.md, install-side
acceptance criteria 2, 3, 4).

WHAT IS TESTED
--------------
1. **Version-targeted laydown.** ``install.sh vA`` on a clean target lays
   down vA's tree (VERSION file proves it) even though newer tags (vB, vC)
   also exist in the source repo and the source repo's checked-out branch
   points at the newest commit.

2. **Direction-free fresh installs.** A second fresh install targeting an
   OLDER version than a prior install, on a different root, succeeds
   regardless of that prior newer install — fresh installs carry no global
   direction state (that rule belongs to upgrade.sh only).

3. **Nonexistent-tag fail-loud.** Requesting a tag that does not exist
   fails loud: it names the requested version, lists the tags that do
   exist, exits non-zero, and leaves no partial laydown (the target root is
   never created).

4. **Source-cleanliness.** The source repo's checked-out branch and
   ``git worktree list`` output are byte-identical before and after both a
   successful version-targeted install and a failed (nonexistent-tag) one
   — the operator's checkout is never touched, on either path.

SANDBOX DESIGN
--------------
install.sh resolves the requested version against git tags in the
directory it is invoked from (``SCRIPT_DIR``, derived from its own
``BASH_SOURCE``). To exercise the real resolution / materialize / laydown
code path end-to-end, each test builds a standalone, throwaway git repo
that is a full copy of this dev tree (so install.sh and every library it
sources — version_resolve.sh, version_stamp.sh, argparse.sh, the
subagents manifest, team/workflows/, etc. — are real, unmodified content),
then adds three commits on top, each changing ONLY the committed VERSION
file, tagged (ascending) so the fixture has tags "vA < vB < vC" per the
requirements doc's acceptance language. Every install.sh invocation in
this file runs against one of these throwaway repos, never against the
real dev tree this test suite lives in.

Both PGAI_AGENT_KANBAN_ROOT_PATH (the install target) and HOME (so
--add-claude-agents/--add-codex-agents are never implicitly triggered,
and any provider-CLI probing lands in a throwaway HOME) are redirected
under tmp_path for every run. ``--no-system-cron`` keeps the OS crontab
untouched (on top of the suite's autouse crontab sandbox in conftest.py).
No network access is required — the fixture repo is fully local.

NAMING CONVENTION
-----------------
Function names describe the behavior under test.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths to the real scripts under test
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_REAL_INSTALL_SH = _REPO_ROOT / "install.sh"

_TIMEOUT = 120

# Ascending fixture tags standing in for the requirements doc's "vA < vB < vC".
# Namespaced away from this project's own real release tags (v0.x/v1.x/v2.x)
# so there is no risk of colliding with a genuine semver tag if fixture
# construction is ever pointed at a non-throwaway repo by mistake.
_TAG_A = "v100.0.0"
_TAG_B = "v100.1.0"
_TAG_C = "v100.2.0"
_ASCENDING_TAGS = [_TAG_A, _TAG_B, _TAG_C]

# Directories excluded when copying this dev tree into a fixture repo — the
# fixture builds its OWN .git history from scratch (see _build_tagged_source_repo).
_FIXTURE_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache"
)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _run_git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _git_state_snapshot(repo: pathlib.Path) -> tuple[str, str]:
    """Return (checked-out branch, worktree list) for *repo*.

    Used to prove source-cleanliness: these two values must be
    byte-identical before and after any install.sh invocation against
    *repo*, whether the invocation succeeds or fails.
    """
    branch = _run_git(repo, "symbolic-ref", "--short", "HEAD").stdout
    worktrees = _run_git(repo, "worktree", "list", "--porcelain").stdout
    return branch, worktrees


# ---------------------------------------------------------------------------
# Fixture repo builder
# ---------------------------------------------------------------------------


def _build_tagged_source_repo(
    tmp_path: pathlib.Path,
    subdir: str,
    tags: list[str] = _ASCENDING_TAGS,
) -> pathlib.Path:
    """Build a throwaway git repo that is a full copy of this dev tree, with
    one commit per tag in *tags* (ascending order), each commit's only
    change being the committed VERSION file set to that tag's value.

    install.sh (and every library it sources at fresh-install time) is real,
    unmodified content copied from this checkout — this is the same "full,
    real copy" approach test_subagent_deploy_dir_creation.py and
    test_overwatch_wake_driven_fixtures.py already use for install.sh /
    wake-script fixtures — so a version-targeted install run against this
    repo exercises the real resolution/materialize/laydown code path, not a
    synthetic stand-in for it.

    The repo's final checked-out branch is "main", pointing at the LAST
    (newest) tag's commit — this is what lets the "checked-out branch is
    untouched" assertions be meaningful: the branch is not already sitting
    at the tag being requested.
    """
    repo = tmp_path / subdir
    shutil.copytree(_REPO_ROOT, repo, ignore=_FIXTURE_COPY_IGNORE)

    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "fixture@example.com")
    _run_git(repo, "config", "user.name", "Fixture Repo")

    for tag in tags:
        (repo / "VERSION").write_text(f"{tag}\n", encoding="utf-8")
        _run_git(repo, "add", "-A")
        _run_git(repo, "commit", "-m", f"Set VERSION to {tag}")
        _run_git(repo, "tag", tag)

    return repo


def _run_install(
    source_repo: pathlib.Path,
    *,
    kanban_root: pathlib.Path,
    home: pathlib.Path,
    version_arg: str | None = None,
) -> subprocess.CompletedProcess:
    """Run the real install.sh out of *source_repo* against a throwaway root.

    Uses --force (non-interactive) and --no-system-cron (never touches the
    host crontab; the suite's autouse crontab sandbox also protects this,
    but --no-system-cron additionally means pseudocron/crontab installation
    narration never appears in output this test might otherwise have to
    account for). Neither --add-claude-agents nor --add-codex-agents is
    passed, so nothing is written under HOME beyond what install.sh itself
    creates for its own bookkeeping.
    """
    home.mkdir(parents=True, exist_ok=True)

    args = ["bash", str(source_repo / "install.sh")]
    if version_arg is not None:
        args.append(version_arg)
    args += ["--force", "--no-system-cron"]

    env = dict(os.environ)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "HOME": str(home),
        }
    )

    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
    )


def _combined_output(result: subprocess.CompletedProcess) -> str:
    return f"{result.stdout}\n{result.stderr}"


def _installed_version(kanban_root: pathlib.Path) -> str:
    return (kanban_root / "VERSION").read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# Acceptance 1 (install-side AC2, first half): version-targeted laydown
# lands the requested tag while newer tags exist.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_install_with_version_argument_lands_requested_tag_while_newer_tags_exist(
    tmp_path: pathlib.Path,
) -> None:
    """``install.sh vA`` lays down vA's VERSION even though vB and vC exist
    and the source repo's checked-out branch sits at vC's commit."""
    repo = _build_tagged_source_repo(tmp_path, "source_repo")
    kanban_root = tmp_path / "kanban_root"
    home = tmp_path / "home"

    result = _run_install(
        repo, kanban_root=kanban_root, home=home, version_arg=_TAG_A
    )
    output = _combined_output(result)

    assert result.returncode == 0, f"install.sh {_TAG_A} failed:\n{output}"
    assert f"resolved to tag '{_TAG_A}'" in output, output

    installed = _installed_version(kanban_root)
    assert installed == _TAG_A, (
        f"Installed VERSION must equal the requested tag {_TAG_A!r}, "
        f"proving the older tag's tree was laid down even though newer "
        f"tags ({_TAG_B}, {_TAG_C}) exist in the source repo.\n"
        f"Got: {installed!r}"
    )

    # Sanity: the newer tags still exist in the source repo (this test does
    # not delete or hide them — the resolution simply chose the requested one).
    existing_tags = _run_git(repo, "tag", "-l").stdout.split()
    assert _TAG_B in existing_tags and _TAG_C in existing_tags, existing_tags


# ---------------------------------------------------------------------------
# Acceptance 2 (install-side AC2, second half): direction-free fresh
# installs — an older-version install on a separate root succeeds
# regardless of a prior newer install elsewhere (no global direction state).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_fresh_install_targeting_older_version_on_separate_root_succeeds_after_newer_install_elsewhere(
    tmp_path: pathlib.Path,
) -> None:
    """A fresh install targeting the newest tag, followed by a second fresh
    install (different root) targeting the OLDEST tag, both succeed — a
    fresh install has no direction to enforce, unlike upgrade.sh."""
    repo = _build_tagged_source_repo(tmp_path, "source_repo")

    newer_root = tmp_path / "kanban_root_newer"
    newer_home = tmp_path / "home_newer"
    newer_result = _run_install(
        repo, kanban_root=newer_root, home=newer_home, version_arg=_TAG_C
    )
    assert newer_result.returncode == 0, (
        f"install.sh {_TAG_C} failed:\n{_combined_output(newer_result)}"
    )
    assert _installed_version(newer_root) == _TAG_C

    older_root = tmp_path / "kanban_root_older"
    older_home = tmp_path / "home_older"
    older_result = _run_install(
        repo, kanban_root=older_root, home=older_home, version_arg=_TAG_A
    )
    assert older_result.returncode == 0, (
        f"A fresh install targeting an OLDER version than a prior install "
        f"elsewhere must still succeed (fresh installs carry no global "
        f"direction state).\n{_combined_output(older_result)}"
    )
    assert _installed_version(older_root) == _TAG_A, (
        f"Second fresh install's VERSION must equal the older requested "
        f"tag {_TAG_A!r} — a fresh install on a new root has no memory of "
        f"the prior {_TAG_C!r} install on {newer_root}.\n"
        f"Got: {_installed_version(older_root)!r}"
    )

    # Both roots retain their own independently-requested version.
    assert _installed_version(newer_root) == _TAG_C, (
        "The first (newer) install's root must be unaffected by the "
        "second (older) install on a different root."
    )


# ---------------------------------------------------------------------------
# Acceptance 3 (install-side AC3): nonexistent-tag request fails loud,
# names the request, lists the tags that DO exist, exits non-zero, and
# leaves no partial laydown.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_nonexistent_tag_request_fails_loud_lists_available_tags_and_leaves_no_partial_laydown(
    tmp_path: pathlib.Path,
) -> None:
    """Requesting a tag that does not exist in the source repo fails loud,
    naming the request and listing the tags that DO exist, exits non-zero,
    and never creates the target install root (no partial laydown)."""
    repo = _build_tagged_source_repo(tmp_path, "source_repo")
    kanban_root = tmp_path / "kanban_root_missing"
    home = tmp_path / "home"
    bogus_version = "v999.999.999"

    result = _run_install(
        repo, kanban_root=kanban_root, home=home, version_arg=bogus_version
    )
    output = _combined_output(result)

    assert result.returncode != 0, (
        f"install.sh must exit non-zero for a nonexistent tag request.\n{output}"
    )
    assert bogus_version in output, (
        f"Failure output must name the requested version {bogus_version!r}.\n{output}"
    )
    for real_tag in _ASCENDING_TAGS:
        assert real_tag in output, (
            f"Failure output must list tags that DO exist in the source "
            f"repo (expected {real_tag!r} among them).\n{output}"
        )

    assert not kanban_root.exists(), (
        f"No partial laydown: {kanban_root} must not be created when "
        f"version resolution fails before any install content is copied.\n"
        f"Output was:\n{output}"
    )


# ---------------------------------------------------------------------------
# Acceptance 4 (install-side AC4): source-cleanliness — the source repo's
# checked-out branch and worktree list are byte-identical before/after both
# a success and a failure run.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_source_repo_branch_and_worktree_list_unchanged_across_success_and_failure_installs(
    tmp_path: pathlib.Path,
) -> None:
    """The source repo's checked-out branch and `git worktree list` output
    are byte-identical before any install.sh run, after a successful
    version-targeted install, and after a failed (nonexistent-tag) one —
    proving the operator's checkout is never touched on either path."""
    repo = _build_tagged_source_repo(tmp_path, "source_repo")

    baseline = _git_state_snapshot(repo)

    success_result = _run_install(
        repo,
        kanban_root=tmp_path / "kanban_root_success",
        home=tmp_path / "home_success",
        version_arg=_TAG_A,
    )
    assert success_result.returncode == 0, (
        f"Setup: the success-path install must itself succeed.\n"
        f"{_combined_output(success_result)}"
    )
    after_success = _git_state_snapshot(repo)
    assert after_success == baseline, (
        f"Source repo's checked-out branch / worktree list changed after a "
        f"SUCCESSFUL version-targeted install.\n"
        f"Before: {baseline}\nAfter:  {after_success}"
    )

    failure_result = _run_install(
        repo,
        kanban_root=tmp_path / "kanban_root_failure",
        home=tmp_path / "home_failure",
        version_arg="v999.999.999",
    )
    assert failure_result.returncode != 0, (
        f"Setup: the failure-path install must itself fail.\n"
        f"{_combined_output(failure_result)}"
    )
    after_failure = _git_state_snapshot(repo)
    assert after_failure == baseline, (
        f"Source repo's checked-out branch / worktree list changed after a "
        f"FAILED (nonexistent-tag) version-targeted install.\n"
        f"Before: {baseline}\nAfter:  {after_failure}"
    )

    # No leftover worktree metadata after either run.
    assert "worktree " in after_failure[1] and after_failure[1].count("worktree ") == 1, (
        f"Exactly one worktree (the source repo's own) must be registered "
        f"after both runs — no leaked detached worktrees from either the "
        f"success or the failure path.\ngit worktree list --porcelain:\n"
        f"{after_failure[1]}"
    )
