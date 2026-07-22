"""
test_describe_tag_filter.py
============================
Behavioral tests for BUG-0074 and BUG-0078: git describe --tags must always
carry a prefix-aware --match pattern at every call site in team/scripts/.

WHAT IS TESTED
--------------
1. **Bare-prefix alias-tag fixture** (BUG-0074 regression) — a repo with an
   annotated vX.Y.Z tag and a NEWER annotated 'latest' tag on the same commit:
   the version_stamp path must resolve to vX.Y.Z, not 'latest'.  The divergence
   check must also report clean when compared against a VERSION file containing
   vX.Y.Z.

2. **Prefixed fixture** (BUG-0078) — a repo whose release tag carries a branch
   prefix (e.g. ai_v1.23.0), with a BARE v-ancestor (v1.0.0) and a NEWER
   'latest' alias on HEAD: the version_stamp path must resolve to the ai_v tag,
   not the bare v-ancestor or the 'latest' alias.  The divergence check must
   also report clean when VERSION matches the prefixed tag.

3. **Call-site matrix** — both prefix shapes (empty and "ai_") are exercised for
   all four describe call sites (version_stamp.sh, check-version-divergence.sh,
   cm/finalize-release.sh both-describes path, and upgrade.sh advisory path via
   git_describe_tag_pattern).  This encodes the "omitted-dimension" lesson from
   BUG-0074's fixture gap.

4. **Grep-zero gate** — zero unfiltered 'git describe --tags' calls exist in
   team/scripts/ shell files.  An unfiltered call is 'describe --tags' NOT
   immediately followed by --match.  Comment lines are excluded from the scan
   (this gate targets executable code, not documentation).

NAMING CONVENTION
-----------------
Function names describe the behavior under test, not the bug ID.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

from tests.unit.shell_harness import run_bash


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LIB_DIR = _SCRIPTS_DIR / "lib"
_VERSION_STAMP_SH = _LIB_DIR / "version_stamp.sh"
_CVD_SH = _LIB_DIR / "overwatch-checks" / "check-version-divergence.sh"
_FINALIZE_SH = _SCRIPTS_DIR / "cm" / "finalize-release.sh"


# ---------------------------------------------------------------------------
# Git fixture helpers
# ---------------------------------------------------------------------------


def _make_git_repo(tmp_path: pathlib.Path, subdir: str = "repo") -> pathlib.Path:
    """Create a minimal git repo with one initial commit.  Returns the repo root."""
    repo = tmp_path / subdir
    repo.mkdir(parents=True, exist_ok=True)

    def _git(*args: str) -> str:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return r.stdout.strip()

    _git("init", "-b", "main")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test Fixture")
    return repo


def _git_cmd(repo: pathlib.Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _build_alias_tag_repo(
    tmp_path: pathlib.Path,
    release_version: str = "v1.23.0",
) -> pathlib.Path:
    """Build a repo with an annotated release tag and a NEWER annotated 'latest' tag
    on the same commit.

    Bare-prefix fixture (BUG-0074 regression scenario): release tags have the form
    vX.Y.Z with no branch prefix.  A fresh clone where 'latest' was created after
    the release tag causes unfiltered 'git describe --tags' to pick 'latest'
    instead of the release tag.

    Returns the repo path.
    """
    repo = _make_git_repo(tmp_path, "alias_tag_repo")

    (repo / "f.txt").write_text("content\n", encoding="utf-8")
    _git_cmd(repo, "add", "f.txt")
    _git_cmd(repo, "commit", "-m", "Release commit")

    # Annotated release tag (created first).
    _git_cmd(repo, "tag", "-a", release_version, "-m", f"Release {release_version}")

    # Annotated 'latest' tag created after the release tag on the same commit.
    # git describe --tags breaks same-commit ties by tagger date; 'latest' wins
    # without --match.
    _git_cmd(repo, "tag", "-a", "latest", "-m", "Alias tag pointing to latest release")

    return repo


def _build_prefixed_alias_tag_repo(
    tmp_path: pathlib.Path,
    prefix: str = "ai_",
    prefixed_version: str = "ai_v1.23.0",
    bare_ancestor_version: str = "v1.0.0",
) -> pathlib.Path:
    """Build a repo with a bare v-ancestor, a newer prefixed release tag, and a
    NEWER annotated 'latest' tag on HEAD.

    Prefixed fixture (BUG-0078 scenario): installs that use branch_prefix=ai_ tag
    releases as ai_vX.Y.Z.  Without a prefix-aware --match pattern, git describe
    walks past the ai_v tag to the older bare v-ancestor (or to 'latest').

    Commit sequence (oldest → newest):
      1. Initial commit — bare ancestor tag (v1.0.0)
      2. Release commit — prefixed release tag (ai_v1.23.0)
      3. 'latest' alias tag on the same commit as the release tag

    Returns the repo path.
    """
    repo = _make_git_repo(tmp_path, "prefixed_tag_repo")

    # Commit 1: bare v-ancestor (e.g. v1.0.0)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git_cmd(repo, "add", "base.txt")
    _git_cmd(repo, "commit", "-m", "Initial release")
    _git_cmd(repo, "tag", "-a", bare_ancestor_version, "-m", f"Base release {bare_ancestor_version}")

    # Commit 2: release commit that carries the prefixed tag
    (repo / "f.txt").write_text("content\n", encoding="utf-8")
    _git_cmd(repo, "add", "f.txt")
    _git_cmd(repo, "commit", "-m", "Prefixed release commit")

    # Annotated prefixed release tag (created first on this commit)
    _git_cmd(repo, "tag", "-a", prefixed_version, "-m", f"Release {prefixed_version}")

    # Annotated 'latest' alias created after the release tag on the same commit
    _git_cmd(repo, "tag", "-a", "latest", "-m", "Alias tag pointing to latest release")

    return repo


# ---------------------------------------------------------------------------
# Test 1a: version_stamp resolves to the release tag when 'latest' is also present
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
def test_version_stamp_resolves_release_tag_not_alias(tmp_path: pathlib.Path) -> None:
    """version_stamp writes the numeric release tag when a newer 'latest' alias exists.

    Reproduces the BUG-0074 failure scenario:
    - Repo has annotated v1.23.0 and a NEWER annotated 'latest' on the same commit.
    - Unfiltered 'git describe --tags' would resolve to 'latest'.
    - With --match 'v[0-9]*', it must resolve to 'v1.23.0'.

    Assertions:
    - VERSION contains exactly the release version tag (not 'latest').
    - VERSION does not contain 'latest'.
    """
    release_version = "v1.23.0"
    repo = _build_alias_tag_repo(tmp_path, release_version)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    result = run_bash(
        tmp_path,
        f"source {_VERSION_STAMP_SH!s} && stamp_version_files '{repo!s}' '{kanban_root!s}'",
    )
    assert result.returncode == 0, (
        f"stamp_version_files exited non-zero: {result.stderr}"
    )

    version_file = kanban_root / "VERSION"
    assert version_file.exists(), "VERSION was not written"
    stamped = version_file.read_text(encoding="utf-8").strip()

    assert stamped == release_version, (
        f"version_stamp must resolve to the numeric release tag, not the alias tag.\n"
        f"Expected: {release_version!r}\n"
        f"Got: {stamped!r}\n"
        f"If 'latest' was returned, --match 'v[0-9]*' is missing from git describe."
    )
    assert "latest" not in stamped, (
        f"VERSION must not contain 'latest'; got: {stamped!r}"
    )


# ---------------------------------------------------------------------------
# Test 1b: divergence check is clean when VERSION matches the release tag
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CVD_SH.exists(),
    reason=f"check-version-divergence.sh not found at {_CVD_SH}",
)
def test_divergence_check_clean_when_version_matches_release_tag(
    tmp_path: pathlib.Path,
) -> None:
    """Divergence check reports clean when VERSION matches the numeric release tag.

    Scenario: repo has annotated v1.23.0 and a NEWER annotated 'latest' on
    the same commit.  The installed VERSION file contains 'v1.23.0'.

    Without --match on git describe in the divergence check, the describe
    resolves to 'latest', causing a false divergence (installed='v1.23.0'
    vs. dev='latest').

    With --match 'v[0-9]*', the describe resolves to 'v1.23.0' and the check
    must NOT log a divergence.

    This test is structural: it verifies the divergence-check output text does
    NOT contain the "DIVERGENCE DETECTED" phrase when the scenario should be clean.
    """
    release_version = "v1.23.0"
    repo = _build_alias_tag_repo(tmp_path, release_version)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    # Write a VERSION file reflecting the installed version.
    (kanban_root / "VERSION").write_text(f"{release_version}\n", encoding="utf-8")

    # Minimal projects.cfg so the check can resolve the project name.
    (kanban_root / "projects.cfg").write_text(
        "[project:test-proj]\nname = test-proj\n",
        encoding="utf-8",
    )

    result = run_bash(
        tmp_path,
        "\n".join([
            f"export KANBAN_ROOT='{kanban_root!s}'",
            f"export PGAI_DEV_TREE_PATH='{repo!s}'",
            "export OVERWATCH_PROJECT='test-proj'",
            # Stub out overwatch_log_action so the check doesn't need the full protocol.
            "overwatch_log_action() { return 0; }",
            f"source '{_CVD_SH!s}'",
            "overwatch_check_version_divergence",
        ]),
    )

    combined = result.stdout + result.stderr
    assert "DIVERGENCE DETECTED" not in combined, (
        f"Divergence check must NOT report divergence when VERSION matches "
        f"the release tag and only an alias tag is newer.\n"
        f"This indicates --match 'v[0-9]*' is missing from git describe in the check.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Test 1c: prefixed fixture — version_stamp resolves prefixed tag, not bare ancestor
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
def test_version_stamp_resolves_prefixed_tag_not_bare_ancestor(tmp_path: pathlib.Path) -> None:
    """version_stamp resolves the prefixed release tag, not a bare v-ancestor or 'latest'.

    Prefixed fixture (BUG-0078 scenario):
    - Repo has a bare v1.0.0 ancestor, a newer annotated ai_v1.23.0 on a later
      commit, and a NEWER annotated 'latest' on the same commit as ai_v1.23.0.
    - Without a prefix-aware --match pattern, describe walks back to v1.0.0
      (bare v-ancestor) because 'ai_v[0-9]*' does not match 'v[0-9]*'.
    - With --match 'ai_v[0-9]*' (from git_describe_tag_pattern("ai_")),
      describe must resolve to ai_v1.23.0.

    Assertions:
    - VERSION contains the prefixed release tag (ai_v1.23.0).
    - VERSION does not contain 'latest'.
    - VERSION does not contain the bare ancestor version (v1.0.0).
    """
    prefix = "ai_"
    prefixed_version = "ai_v1.23.0"
    bare_ancestor = "v1.0.0"

    repo = _build_prefixed_alias_tag_repo(tmp_path, prefix, prefixed_version, bare_ancestor)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    result = run_bash(
        tmp_path,
        "\n".join([
            f"source {_VERSION_STAMP_SH!s}",
            f"stamp_version_files '{repo!s}' '{kanban_root!s}' '' '{prefix}'",
        ]),
    )
    assert result.returncode == 0, (
        f"stamp_version_files exited non-zero: {result.stderr}"
    )

    version_file = kanban_root / "VERSION"
    assert version_file.exists(), "VERSION was not written"
    stamped = version_file.read_text(encoding="utf-8").strip()

    assert stamped == prefixed_version, (
        f"version_stamp must resolve to the prefixed release tag.\n"
        f"Expected: {prefixed_version!r}\n"
        f"Got: {stamped!r}\n"
        f"If '{bare_ancestor}' was returned, --match is not using the prefix.\n"
        f"If 'latest' was returned, --match filter is ineffective."
    )
    assert "latest" not in stamped, (
        f"VERSION must not contain 'latest'; got: {stamped!r}"
    )
    assert stamped != bare_ancestor, (
        f"VERSION must not be the bare ancestor tag {bare_ancestor!r}; "
        f"the prefix-aware pattern must match the prefixed tag."
    )


# ---------------------------------------------------------------------------
# Test 1d: prefixed fixture — divergence check is clean for prefixed install
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CVD_SH.exists(),
    reason=f"check-version-divergence.sh not found at {_CVD_SH}",
)
def test_divergence_check_clean_when_version_matches_prefixed_tag(
    tmp_path: pathlib.Path,
) -> None:
    """Divergence check reports clean when VERSION matches the prefixed release tag.

    Prefixed fixture (BUG-0078 scenario):
    - Repo has v1.0.0 bare ancestor, ai_v1.23.0 prefixed tag, and newer 'latest'.
    - The installed VERSION file contains 'ai_v1.23.0'.
    - Without a prefix-aware --match, the check resolves to v1.0.0 or 'latest',
      causing a false divergence.
    - With --match 'ai_v[0-9]*', the check resolves to ai_v1.23.0 and must NOT
      log a divergence.

    This test stubs project.cfg with branch_prefix=ai_ so the check reads the
    prefix and constructs the correct --match pattern via git_describe_tag_pattern.
    """
    prefix = "ai_"
    prefixed_version = "ai_v1.23.0"

    repo = _build_prefixed_alias_tag_repo(tmp_path, prefix, prefixed_version)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    # Write VERSION with the prefixed release version.
    (kanban_root / "VERSION").write_text(f"{prefixed_version}\n", encoding="utf-8")

    # Minimal projects.cfg so the check resolves the project name.
    (kanban_root / "projects.cfg").write_text(
        "[project:test-proj]\nname = test-proj\n",
        encoding="utf-8",
    )

    # Minimal project.cfg with branch_prefix so the divergence check uses the
    # correct --match pattern.
    proj_root = kanban_root / "projects" / "test-proj"
    proj_root.mkdir(parents=True)
    (proj_root / "project.cfg").write_text(
        f"[project]\nbranch_prefix = {prefix}\ndev_tree_path = {repo!s}\n",
        encoding="utf-8",
    )

    result = run_bash(
        tmp_path,
        "\n".join([
            f"export KANBAN_ROOT='{kanban_root!s}'",
            f"export PGAI_DEV_TREE_PATH='{repo!s}'",
            "export OVERWATCH_PROJECT='test-proj'",
            "overwatch_log_action() { return 0; }",
            f"source '{_CVD_SH!s}'",
            "overwatch_check_version_divergence",
        ]),
    )

    combined = result.stdout + result.stderr
    assert "DIVERGENCE DETECTED" not in combined, (
        f"Divergence check must NOT report divergence when VERSION matches "
        f"the prefixed release tag and only a bare v-ancestor and alias tag exist.\n"
        f"This indicates the prefix-aware --match pattern is not being used.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Test 1e: git_describe_tag_pattern helper — unit test for pattern construction
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
@pytest.mark.parametrize("prefix,expected_pattern", [
    ("",     "v[0-9]*"),
    ("ai_",  "ai_v[0-9]*"),
    ("team-", "team-v[0-9]*"),
])
def test_git_describe_tag_pattern_constructs_correct_pattern(
    tmp_path: pathlib.Path,
    prefix: str,
    expected_pattern: str,
) -> None:
    """git_describe_tag_pattern returns the correct --match pattern for each prefix.

    Empty prefix collapses to 'v[0-9]*' (BUG-0074 latest-exclusion preserved).
    Non-empty prefix produces '<prefix>v[0-9]*'.
    """
    result = run_bash(
        tmp_path,
        "\n".join([
            f"source {_VERSION_STAMP_SH!s}",
            f"git_describe_tag_pattern '{prefix}'",
        ]),
    )
    assert result.returncode == 0, (
        f"git_describe_tag_pattern exited non-zero: {result.stderr}"
    )
    assert result.stdout.strip() == expected_pattern, (
        f"git_describe_tag_pattern('{prefix}') must return '{expected_pattern}'; "
        f"got: {result.stdout.strip()!r}"
    )


# ---------------------------------------------------------------------------
# Test 1f: call-site matrix — both prefix shapes for version_stamp.sh
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
@pytest.mark.parametrize("prefix,tag,bare_ancestor,expected", [
    # Bare-prefix shape: existing BUG-0074 scenario
    ("", "v1.23.0", None, "v1.23.0"),
    # Prefixed shape: BUG-0078 scenario
    ("ai_", "ai_v1.23.0", "v1.0.0", "ai_v1.23.0"),
])
def test_version_stamp_prefix_shape_matrix(
    tmp_path: pathlib.Path,
    prefix: str,
    tag: str,
    bare_ancestor: str | None,
    expected: str,
) -> None:
    """version_stamp resolves the correct tag for both bare and prefixed shapes.

    Matrix:
    - prefix=""    → bare v-tag repo (BUG-0074 regression)
    - prefix="ai_" → prefixed ai_v-tag repo with bare v-ancestor (BUG-0078)

    Both cases also include a newer 'latest' alias tag on HEAD.
    """
    if bare_ancestor is not None:
        repo = _build_prefixed_alias_tag_repo(tmp_path, prefix, tag, bare_ancestor)
    else:
        repo = _build_alias_tag_repo(tmp_path, tag)

    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir(exist_ok=True)

    result = run_bash(
        tmp_path,
        "\n".join([
            f"source {_VERSION_STAMP_SH!s}",
            f"stamp_version_files '{repo!s}' '{kanban_root!s}' '' '{prefix}'",
        ]),
    )
    assert result.returncode == 0, (
        f"stamp_version_files exited non-zero (prefix={prefix!r}): {result.stderr}"
    )

    version_file = kanban_root / "VERSION"
    assert version_file.exists(), "VERSION was not written"
    stamped = version_file.read_text(encoding="utf-8").strip()

    assert stamped == expected, (
        f"stamp_version_files with prefix={prefix!r}: "
        f"expected {expected!r}, got {stamped!r}"
    )
    assert "latest" not in stamped, f"VERSION must not contain 'latest'; got: {stamped!r}"


# ---------------------------------------------------------------------------
# Test 1g: call-site matrix — both prefix shapes for check-version-divergence.sh
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CVD_SH.exists(),
    reason=f"check-version-divergence.sh not found at {_CVD_SH}",
)
@pytest.mark.parametrize("prefix,installed_version,tag,bare_ancestor", [
    # Bare-prefix shape: BUG-0074 regression
    ("", "v1.23.0", "v1.23.0", None),
    # Prefixed shape: BUG-0078 scenario
    ("ai_", "ai_v1.23.0", "ai_v1.23.0", "v1.0.0"),
])
def test_divergence_check_prefix_shape_matrix(
    tmp_path: pathlib.Path,
    prefix: str,
    installed_version: str,
    tag: str,
    bare_ancestor: str | None,
) -> None:
    """Divergence check reports clean for both bare and prefixed tag shapes.

    Matrix:
    - prefix=""    → bare v-tag repo; VERSION=v1.23.0; no false divergence.
    - prefix="ai_" → prefixed ai_v-tag repo with bare v-ancestor; VERSION=ai_v1.23.0;
                     no false divergence (ai_v1.23.0 matches ai_v[0-9]*, not v1.0.0).

    Both cases also include a newer 'latest' alias on HEAD.
    """
    if bare_ancestor is not None:
        repo = _build_prefixed_alias_tag_repo(tmp_path, prefix, tag, bare_ancestor)
    else:
        repo = _build_alias_tag_repo(tmp_path, tag)

    suffix = prefix.rstrip("_") or "bare"
    kanban_root = tmp_path / f"kanban_root_{suffix}"
    kanban_root.mkdir(parents=True, exist_ok=True)

    (kanban_root / "VERSION").write_text(f"{installed_version}\n", encoding="utf-8")
    (kanban_root / "projects.cfg").write_text(
        "[project:test-proj]\nname = test-proj\n",
        encoding="utf-8",
    )

    proj_root = kanban_root / "projects" / "test-proj"
    proj_root.mkdir(parents=True, exist_ok=True)
    (proj_root / "project.cfg").write_text(
        f"[project]\nbranch_prefix = {prefix}\ndev_tree_path = {repo!s}\n",
        encoding="utf-8",
    )

    result = run_bash(
        tmp_path,
        "\n".join([
            f"export KANBAN_ROOT='{kanban_root!s}'",
            f"export PGAI_DEV_TREE_PATH='{repo!s}'",
            "export OVERWATCH_PROJECT='test-proj'",
            "overwatch_log_action() { return 0; }",
            f"source '{_CVD_SH!s}'",
            "overwatch_check_version_divergence",
        ]),
    )

    combined = result.stdout + result.stderr
    assert "DIVERGENCE DETECTED" not in combined, (
        f"Divergence check must NOT report divergence for prefix={prefix!r} "
        f"when VERSION={installed_version!r} matches the {tag!r} tag.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Test 1h: call-site matrix — git_describe_tag_pattern for upgrade.sh advisory path
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
@pytest.mark.parametrize("prefix,tag,bare_ancestor", [
    # Bare-prefix shape: BUG-0074 regression
    ("", "v1.23.0", None),
    # Prefixed shape: BUG-0078 scenario
    ("ai_", "ai_v1.23.0", "v1.0.0"),
])
def test_describe_pattern_used_in_advisory_path(
    tmp_path: pathlib.Path,
    prefix: str,
    tag: str,
    bare_ancestor: str | None,
) -> None:
    """git_describe_tag_pattern produces a pattern that resolves the correct tag
    in the upgrade.sh advisory describe call.

    This exercises the advisory code path: construct the match pattern via
    git_describe_tag_pattern, then run git describe with that pattern and verify
    the result matches the expected tag (not 'latest' and not a bare ancestor).
    """
    if bare_ancestor is not None:
        repo = _build_prefixed_alias_tag_repo(tmp_path, prefix, tag, bare_ancestor)
    else:
        repo = _build_alias_tag_repo(tmp_path, tag)

    result = run_bash(
        tmp_path,
        "\n".join([
            f"source {_VERSION_STAMP_SH!s}",
            f"_pat=\"$(git_describe_tag_pattern '{prefix}')\"",
            f"git -C '{repo!s}' describe --tags --match \"$_pat\" 2>/dev/null || true",
        ]),
    )
    assert result.returncode == 0, (
        f"Advisory describe failed (prefix={prefix!r}): {result.stderr}"
    )
    described = result.stdout.strip()
    # The described value starts with the tag (may have -N-gSHA suffix past the tag).
    assert described.startswith(tag), (
        f"Advisory describe with prefix={prefix!r} must start with {tag!r}; "
        f"got: {described!r}. "
        f"If 'latest' or '{bare_ancestor}' was returned, the pattern is wrong."
    )


# ---------------------------------------------------------------------------
# Test 1i: call-site matrix — finalize-release.sh describe path (pattern correctness)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
@pytest.mark.parametrize("prefix,tag,bare_ancestor", [
    # Bare-prefix shape: BUG-0074 regression
    ("", "v1.23.0", None),
    # Prefixed shape: BUG-0078 scenario
    ("ai_", "ai_v1.23.0", "v1.0.0"),
])
def test_finalize_release_describe_pattern_resolves_correct_tag(
    tmp_path: pathlib.Path,
    prefix: str,
    tag: str,
    bare_ancestor: str | None,
) -> None:
    """The describe pattern used by finalize-release.sh resolves the project's own tag.

    finalize-release.sh calls git_describe_tag_pattern to build the --match
    pattern before running git describe.  This test exercises that same logic
    (source version_stamp.sh → call git_describe_tag_pattern → run describe)
    and verifies the correct tag is resolved for both prefix shapes.
    """
    if bare_ancestor is not None:
        repo = _build_prefixed_alias_tag_repo(tmp_path, prefix, tag, bare_ancestor)
    else:
        repo = _build_alias_tag_repo(tmp_path, tag)

    result = run_bash(
        tmp_path,
        "\n".join([
            f"source {_VERSION_STAMP_SH!s}",
            f"_pat=\"$(git_describe_tag_pattern '{prefix}')\"",
            # Simulate the finalize-release.sh logic: describe with --abbrev=0
            f"git -C '{repo!s}' describe --tags --match \"$_pat\" --abbrev=0 HEAD 2>/dev/null || true",
        ]),
    )
    assert result.returncode == 0, (
        f"Finalize-release describe failed (prefix={prefix!r}): {result.stderr}"
    )
    resolved = result.stdout.strip()
    assert resolved == tag, (
        f"finalize-release describe with prefix={prefix!r} must resolve to {tag!r}; "
        f"got: {resolved!r}. "
        f"If '{bare_ancestor}' was returned, the pattern is prefix-blind."
    )


# ---------------------------------------------------------------------------
# Test 2: grep-zero gate — no unfiltered describe --tags in team/scripts/
# ---------------------------------------------------------------------------


def _repo_root() -> pathlib.Path:
    """Return the repository root via git."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return pathlib.Path(result.stdout.strip())


# Pattern: 'describe --tags' NOT followed by '--match' anywhere in the same token sequence.
# Matches lines where --match is absent after --tags (allowing other flags between them).
# The pattern checks that '--match' does not appear anywhere after '--tags' on that line.
# We use a two-step approach: flag lines with 'describe --tags' that lack '--match'.
_UNFILTERED_DESCRIBE_PATTERN = re.compile(r"describe\s+--tags")

# Lines that start with optional whitespace then '#' are comments — excluded.
_COMMENT_LINE_PATTERN = re.compile(r"^\s*#")


def _find_unfiltered_describe_calls() -> list[str]:
    """Return '<file>:<line>:<content>' for each unfiltered 'describe --tags' call.

    Scans all .sh files under team/scripts/.  Comment lines are excluded
    (the gate targets executable code, not documentation).

    Returns an empty list when the invariant holds.
    """
    root = _repo_root()
    scripts_dir = root / "team" / "scripts"

    if not scripts_dir.is_dir():
        raise FileNotFoundError(
            f"team/scripts/ not found at {scripts_dir}; "
            "repo root resolution may be wrong."
        )

    violations: list[str] = []

    for sh_file in scripts_dir.rglob("*.sh"):
        try:
            rel_path = sh_file.relative_to(root)
        except ValueError:
            rel_path = sh_file

        rel_str = str(rel_path).replace("\\", "/")

        try:
            lines = sh_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for lineno, line in enumerate(lines, start=1):
            # Skip comment lines — gate targets executable code only.
            if _COMMENT_LINE_PATTERN.match(line):
                continue
            # A violation is a line containing 'describe --tags' that does NOT
            # also contain '--match' anywhere on the same line.
            if _UNFILTERED_DESCRIBE_PATTERN.search(line) and "--match" not in line:
                violations.append(f"{rel_str}:{lineno}:{line.rstrip()}")

    return violations


def test_no_unfiltered_git_describe_tags_in_scripts() -> None:
    """Zero unfiltered 'git describe --tags' calls exist in team/scripts/ shell files.

    Every 'describe --tags' invocation that feeds version stamping, divergence
    checking, or release mechanics must carry '--match' to restrict results to
    numeric release tags.  An unfiltered call allows alias tags (e.g. 'latest')
    to shadow the release tag on same-commit tie-breaking.

    This gate uses a behavior-pattern check (describe --tags NOT followed by
    --match) rather than a hard-coded file list, so future call sites cannot
    regress silently.

    Comment lines are excluded from the scan.
    """
    violations = _find_unfiltered_describe_calls()
    assert not violations, (
        f"grep-zero gate failed — {len(violations)} unfiltered 'git describe --tags' "
        f"call(s) found in team/scripts/:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nEvery 'describe --tags' call must carry '--match' to restrict "
        "results to numeric release tags (e.g. --match 'v[0-9]*').  "
        "This prevents alias tags from being picked on same-commit tie-breaking."
    )


def test_unfiltered_pattern_detects_synthetic_violation(tmp_path: pathlib.Path) -> None:
    """The unfiltered-describe pattern detects a known-bad call.

    Negative proof: confirms the scan can detect a real unfiltered call.
    """
    bad_script = tmp_path / "bad_version.sh"
    bad_script.write_text(
        "#!/usr/bin/env bash\n"
        "# This is a bad script for testing.\n"
        'ver="$(git describe --tags HEAD)"\n'
        'ver2="$(git -C /repo describe --tags 2>/dev/null || true)"\n',
        encoding="utf-8",
    )

    pattern = _UNFILTERED_DESCRIBE_PATTERN
    comment_pat = _COMMENT_LINE_PATTERN
    matching_lines = [
        line
        for line in bad_script.read_text(encoding="utf-8").splitlines()
        if not comment_pat.match(line) and pattern.search(line) and "--match" not in line
    ]

    assert len(matching_lines) >= 2, (
        f"Expected at least 2 matching lines in the synthetic bad script; "
        f"got {len(matching_lines)}: {matching_lines!r}.  "
        "The pattern may be wrong — check _UNFILTERED_DESCRIBE_PATTERN."
    )


def test_filtered_describe_call_is_not_flagged(tmp_path: pathlib.Path) -> None:
    """The scan does not flag 'describe --tags --match ...' calls.

    Positive proof: lines with --match are the correct form and must not
    trigger the gate.
    """
    good_script = tmp_path / "good_version.sh"
    good_script.write_text(
        "#!/usr/bin/env bash\n"
        'ver="$(git describe --tags --match \'v[0-9]*\' HEAD)"\n'
        'ver2="$(git -C /repo describe --tags --match \'v[0-9]*\' 2>/dev/null || true)"\n',
        encoding="utf-8",
    )

    pattern = _UNFILTERED_DESCRIBE_PATTERN
    comment_pat = _COMMENT_LINE_PATTERN
    matching_lines = [
        line
        for line in good_script.read_text(encoding="utf-8").splitlines()
        if not comment_pat.match(line) and pattern.search(line) and "--match" not in line
    ]

    assert not matching_lines, (
        f"Filtered 'describe --tags --match ...' must not be flagged; "
        f"got matches: {matching_lines!r}"
    )


def test_comment_lines_are_excluded_from_scan(tmp_path: pathlib.Path) -> None:
    """Comment lines containing 'describe --tags' without --match are not flagged.

    Documentation may reference the unfiltered form (e.g. in comments explaining
    why --match is needed).  The gate must not penalise comments.
    """
    comment_script = tmp_path / "comment_ok.sh"
    comment_script.write_text(
        "#!/usr/bin/env bash\n"
        "# Uses git describe --tags to get version info.\n"
        "# `git describe --tags` on main returns the clean tag.\n"
        "# Without --match, describe --tags may pick 'latest'.\n",
        encoding="utf-8",
    )

    pattern = _UNFILTERED_DESCRIBE_PATTERN
    comment_pat = _COMMENT_LINE_PATTERN
    matching_lines = [
        line
        for line in comment_script.read_text(encoding="utf-8").splitlines()
        if not comment_pat.match(line) and pattern.search(line) and "--match" not in line
    ]

    assert not matching_lines, (
        f"Comment lines must not be flagged; got: {matching_lines!r}"
    )
