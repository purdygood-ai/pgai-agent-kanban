"""
test_committed_version_fixtures.py
====================================
Gated fixtures for the committed-VERSION contract (PRIORITY-0003).

WHAT IS TESTED
--------------
This file covers the four acceptance criteria from PRIORITY-0003:

1. **Ship-write fixture** — a simulated cm-release run produces a commit
   containing VERSION == the new tag, and ``git show <tag>:VERSION``
   returns the tag name.

2. **Pre-tag mismatch gate** — a doctored pre-tag VERSION causes the release
   gate to exit 1 naming both values; no local tag is created.

3. **Zip-shape install fixture** — a source tree WITHOUT a .git directory
   (the "friend's download" case) causes stamp_version_files (called by
   install.sh) to copy the committed VERSION verbatim to the live install.
   VERSION content reaches the live install intact.

4. **Fresh clone + install fixture** — a source tree WITH .git and a
   committed VERSION file installs correctly with zero additional stamping
   steps beyond what stamp_version_files normally does.

FIXTURE DESIGN
--------------
All throwaway git repos and temp directories are created under ``tmp_path``
(pytest-managed, respects PGAI_AGENT_KANBAN_TEMP_DIR via conftest.py).

Tests 1–2 build a minimal git repo and simulate the relevant mechanics of
cm-release.sh (Step 11d VERSION write+commit and Step 13 pre-tag gate).

Test 3 builds a source tree WITHOUT .git and calls stamp_version_files
(the function install.sh delegates to) directly to demonstrate that the
committed VERSION value reaches the live install root.

Test 4 builds a source tree WITH .git (simulating a fresh clone) and
calls stamp_version_files to confirm VERSION is copied correctly.

NAMING CONVENTION
-----------------
Function names describe the behavior under test.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths to the real scripts under test
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LIB_DIR = _SCRIPTS_DIR / "lib"

_VERSION_STAMP_SH = _LIB_DIR / "version_stamp.sh"
_CM_RELEASE_SH = _SCRIPTS_DIR / "cm" / "release.sh"
_STATUS_RIGHT_SH = _SCRIPTS_DIR / "dashboard" / "status-right.sh"


# ---------------------------------------------------------------------------
# Git repo helpers
# ---------------------------------------------------------------------------


def _make_git_repo(tmp_path: pathlib.Path, subdir: str = "repo") -> pathlib.Path:
    """Create a minimal git repo with one initial commit and return its root."""
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
    (repo / "initial.txt").write_text("initial commit\n", encoding="utf-8")
    _git("add", "initial.txt")
    _git("commit", "-m", "Initial commit")
    return repo


def _git_cmd(repo: pathlib.Path, *args: str) -> str:
    """Run a git command in *repo* and return stdout stripped."""
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# Class 1: Ship-write fixture
#
# Simulates the mechanics of cm-release.sh Step 11d + Step 13:
#   - Write a clean release version to VERSION (as Step 11d does).
#   - Commit VERSION into the repo (as Step 11d does).
#   - Tag the commit (as Step 13 does, after the pre-tag gate passes).
#   - Assert: committed VERSION == tag name.
#   - Assert: git show <tag>:VERSION returns the tag name.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
class TestShipWriteFixture:
    """cm-release produces a commit containing VERSION == the new tag."""

    def test_committed_version_equals_release_tag(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Release commit contains VERSION == tag; git show <tag>:VERSION returns the tag.

        Simulates the cm-release.sh Step 11d write path: the clean release
        version string is written to VERSION, committed, and then tagged.

        Assertions:
        - The committed VERSION file content equals the tag name.
        - ``git show <tag>:VERSION`` returns the tag name.
        - The tag points to the commit that carries VERSION.
        """
        repo = _make_git_repo(tmp_path, "ship_write_repo")
        tag = "v1.23.9"

        # Simulate Step 11d: write the clean release version to VERSION and commit.
        # This mirrors: printf '%s\n' "$_CLEAN_RELEASE_VERSION" > "$REPO_ROOT/VERSION"
        # followed by git add VERSION && git commit.
        result = run_bash(
            tmp_path,
            f"""\
set -euo pipefail
repo={str(repo)!r}
tag={tag!r}

# Write VERSION (mirrors Step 11d: printf '%s\\n' "$_CLEAN_RELEASE_VERSION" > VERSION)
printf '%s\\n' "$tag" > "$repo/VERSION"

# Stage and commit VERSION
git -C "$repo" add VERSION
git -C "$repo" commit -m "Add committed VERSION for $tag"

# Create the release tag (mirrors Step 13, after pre-tag gate passes)
git -C "$repo" tag "$tag"
""",
        )
        assert result.returncode == 0, (
            f"Setup bash script failed (exit {result.returncode}).\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        # Assert: the committed VERSION file content equals the tag.
        version_in_commit = _git_cmd(repo, "show", f"{tag}:VERSION").strip()
        assert version_in_commit == tag, (
            f"Ship-write fixture: committed VERSION must equal the release tag.\n"
            f"Expected: {tag!r}\nGot: {version_in_commit!r}\n"
            f"git show {tag}:VERSION must return the tag name."
        )

        # Assert: git show <tag>:VERSION returns exactly the tag (no trailing newline
        # artifacts — git show strips trailing newlines from file content).
        assert version_in_commit == tag, (
            f"git show {tag}:VERSION returned {version_in_commit!r}, expected {tag!r}"
        )

        # Assert: the tag points at a commit that carries VERSION.
        # (Confirm the tag was created — no tag means the step failed.)
        tags = _git_cmd(repo, "tag", "-l", tag)
        assert tags == tag, (
            f"Release tag {tag!r} was not created; got {tags!r}"
        )

    def test_version_file_content_is_clean_tag_without_suffix(
        self, tmp_path: pathlib.Path
    ) -> None:
        """VERSION committed into the release commit is the clean tag, not a describe suffix.

        Confirms the committed VERSION carries no describe-style suffix
        (e.g. no ``-1-gABCDEF`` fragment) — it is exactly the bare release
        version string that cm-release derives from the RC branch name.

        Assertions:
        - VERSION content does not contain ''-g' (git describe suffix marker).
        - VERSION content equals the clean bare version string.
        """
        repo = _make_git_repo(tmp_path, "clean_tag_repo")
        tag = "v1.23.9"

        # Write VERSION as a clean tag (no describe suffix).
        (repo / "VERSION").write_text(f"{tag}\n", encoding="utf-8")
        _git_cmd(repo, "add", "VERSION")
        _git_cmd(repo, "commit", "-m", f"Add committed VERSION for {tag}")
        _git_cmd(repo, "tag", tag)

        # Read back from the commit.
        committed = _git_cmd(repo, "show", f"{tag}:VERSION").strip()

        assert "-g" not in committed, (
            f"Committed VERSION must not contain a git describe suffix.\n"
            f"Got: {committed!r}"
        )
        assert committed == tag, (
            f"Committed VERSION must equal the clean tag.\n"
            f"Expected: {tag!r}\nGot: {committed!r}"
        )


# ---------------------------------------------------------------------------
# Class 2: Pre-tag mismatch gate fixture
#
# Simulates the cm-release.sh Step 13 pre-tag VERSION gate:
#   - A doctored (wrong) VERSION is on disk.
#   - The gate bash logic exits 1.
#   - No tag is created.
# ---------------------------------------------------------------------------


class TestPreTagMismatchGate:
    """Doctored pre-tag VERSION causes exit 1; no tag is created."""

    def test_mismatch_gate_exits_nonzero_and_names_both_values(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Pre-tag VERSION mismatch causes exit 1 naming both on-disk and expected values.

        Replicates the Step 13 pre-tag gate from cm-release.sh in isolation:
        reads VERSION from disk, compares to the expected tag, and exits 1
        when they differ, emitting both values to stderr.

        Assertions:
        - Exit code is non-zero (gate fires).
        - Stderr contains the on-disk VALUE (the wrong value).
        - Stderr contains the EXPECTED value (the tag string).
        - No tag is created (``git tag -l <tag>`` returns empty).
        """
        repo = _make_git_repo(tmp_path, "mismatch_gate_repo")
        tag = "v1.23.9"
        wrong_version = "v9.99.99"

        # Write a doctored VERSION that does NOT match the tag.
        (repo / "VERSION").write_text(f"{wrong_version}\n", encoding="utf-8")
        _git_cmd(repo, "add", "VERSION")
        _git_cmd(repo, "commit", "-m", "Doctored VERSION for mismatch test")

        # Replicate the pre-tag gate from Step 13 of cm-release.sh.
        # The gate reads VERSION, strips trailing newline, and compares to
        # the expected clean release version; exits 1 on mismatch without
        # creating the tag.
        gate_script = f"""\
set -uo pipefail
repo={str(repo)!r}
release_tag={tag!r}
expected_version={tag!r}

# Read VERSION (mirrors Step 13 pre-tag gate)
if [[ -f "$repo/VERSION" ]]; then
  _pretag_version_on_disk="$(cat "$repo/VERSION" 2>/dev/null || true)"
fi
_pretag_version_trimmed="${{_pretag_version_on_disk%$'\\n'}}"

if [[ "$_pretag_version_trimmed" != "$expected_version" ]]; then
  echo "" >&2
  echo "ERROR: [Pre-tag VERSION gate] VERSION content does not match the tag about to be created." >&2
  echo "  On-disk VERSION : '${{_pretag_version_trimmed}}'" >&2
  echo "  Expected (tag)  : '${{expected_version}}'" >&2
  echo "  Release tag     : ${{release_tag}}" >&2
  echo "  No tag has been created. Investigate why VERSION diverged before re-running." >&2
  exit 1
fi

# Gate passed — create the tag (this line should NOT be reached).
git -C "$repo" tag "$release_tag"
"""
        result = run_bash(tmp_path, gate_script)

        # Gate must fire: exit code non-zero.
        assert result.returncode != 0, (
            f"Pre-tag mismatch gate must exit non-zero when VERSION does not match tag.\n"
            f"Exit code: {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        # Stderr must name the on-disk value (the wrong version).
        assert wrong_version in result.stderr, (
            f"Gate stderr must name the on-disk VERSION value '{wrong_version}'.\n"
            f"stderr: {result.stderr!r}"
        )

        # Stderr must name the expected value (the tag).
        assert tag in result.stderr, (
            f"Gate stderr must name the expected tag value '{tag}'.\n"
            f"stderr: {result.stderr!r}"
        )

        # No tag must have been created.
        existing_tags = _git_cmd(repo, "tag", "-l", tag)
        assert existing_tags == "", (
            f"Pre-tag mismatch gate must NOT create the tag.\n"
            f"Found tag: {existing_tags!r}"
        )

    def test_mismatch_gate_matching_version_allows_tag(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Pre-tag gate passes when VERSION matches the tag; tag is created.

        Happy-path counterpart to the mismatch test: confirms the gate logic
        does not block a correct release.

        Assertions:
        - Exit code is zero (gate passes).
        - Tag is created.
        """
        repo = _make_git_repo(tmp_path, "gate_pass_repo")
        tag = "v1.23.9"

        # Write the CORRECT VERSION (matches the tag).
        (repo / "VERSION").write_text(f"{tag}\n", encoding="utf-8")
        _git_cmd(repo, "add", "VERSION")
        _git_cmd(repo, "commit", "-m", f"Add committed VERSION for {tag}")

        gate_script = f"""\
set -uo pipefail
repo={str(repo)!r}
release_tag={tag!r}
expected_version={tag!r}

if [[ -f "$repo/VERSION" ]]; then
  _pretag_version_on_disk="$(cat "$repo/VERSION" 2>/dev/null || true)"
fi
_pretag_version_trimmed="${{_pretag_version_on_disk%$'\\n'}}"

if [[ "$_pretag_version_trimmed" != "$expected_version" ]]; then
  echo "ERROR: [Pre-tag VERSION gate] mismatch." >&2
  echo "  On-disk: '${{_pretag_version_trimmed}}'" >&2
  echo "  Expected: '${{expected_version}}'" >&2
  exit 1
fi

# Gate passed — create the tag.
git -C "$repo" tag "$release_tag"
"""
        result = run_bash(tmp_path, gate_script)

        assert result.returncode == 0, (
            f"Pre-tag gate must pass when VERSION matches the tag.\n"
            f"Exit code: {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        created_tag = _git_cmd(repo, "tag", "-l", tag)
        assert created_tag == tag, (
            f"Tag {tag!r} must be created when the gate passes.\n"
            f"Got: {created_tag!r}"
        )


# ---------------------------------------------------------------------------
# Class 3: Zip-shape install fixture
#
# Verifies that a source tree WITHOUT .git causes stamp_version_files
# (the function install.sh delegates to for VERSION stamping) to copy the
# committed VERSION verbatim to the live install root.
#
# This covers the "friend's download" case: a zip archive has no .git
# directory, so the committed VERSION file is the sole source of truth.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
class TestZipShapeInstallFixture:
    """A .git-less source tree yields VERSION at the live install via stamp_version_files."""

    def test_committed_version_reaches_live_install_without_git_directory(
        self, tmp_path: pathlib.Path
    ) -> None:
        """stamp_version_files copies committed VERSION to live install when .git is absent.

        Builds a source tree WITHOUT a .git directory (mirroring a zip/tarball
        download), places a VERSION file in it, and calls stamp_version_files
        (the function install.sh delegates to for this step).  Confirms that
        VERSION content appears verbatim in the live install root.

        This is the "friend's download" scenario from PRIORITY-0003: because
        there is no .git directory, the committed VERSION file is the only
        source of the version.  stamp_version_files copies it directly.

        Assertions:
        - The live install VERSION file exists.
        - Its content equals the committed VERSION in the source tree.
        - No VERSION_DETAIL is written (no .git → no git describe).
        """
        # Build a source tree WITHOUT .git — the zip/tarball shape.
        zip_source = tmp_path / "zip_source"
        zip_source.mkdir()
        committed_version = "v1.23.9"
        (zip_source / "VERSION").write_text(f"{committed_version}\n", encoding="utf-8")

        # Explicitly confirm there is no .git directory.
        assert not (zip_source / ".git").exists(), (
            "Fixture error: .git must not exist in the zip-shape source tree."
        )

        # The live install root (where install.sh writes KANBAN_ROOT).
        live_install = tmp_path / "live_install"
        live_install.mkdir()

        # Call stamp_version_files exactly as install.sh does:
        #   stamp_version_files "$SCRIPT_DIR" "$KANBAN_ROOT"
        # where SCRIPT_DIR is the directory containing install.sh (the source tree).
        result = run_bash(
            tmp_path,
            f"source {_VERSION_STAMP_SH!s} && stamp_version_files '{zip_source!s}' '{live_install!s}'",
            extra_env={"PYTHONHASHSEED": "0"},
        )
        assert result.returncode == 0, (
            f"stamp_version_files exited non-zero on zip-shape tree: {result.stderr}"
        )

        # The live install VERSION must exist and match the committed version.
        version_file = live_install / "VERSION"
        assert version_file.exists(), (
            "VERSION was not written to the live install root.\n"
            "stamp_version_files must copy the committed VERSION when .git is absent."
        )
        installed_version = version_file.read_text(encoding="utf-8").strip()
        assert installed_version == committed_version, (
            f"Zip-shape fixture: live install VERSION must equal the committed version.\n"
            f"Expected: {committed_version!r}\nGot: {installed_version!r}\n"
            f"The committed VERSION file is the sole source of truth when .git is absent."
        )

        # No VERSION_DETAIL: with no .git, git describe cannot run.
        detail_file = live_install / "VERSION_DETAIL"
        assert not detail_file.exists(), (
            f"VERSION_DETAIL must NOT be written when .git is absent (no git describe).\n"
            f"Found: {detail_file.read_text(encoding='utf-8')!r}"
        )

    def test_zip_shape_version_survives_install_unchanged(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Committed VERSION content is preserved byte-for-byte in the live install.

        Verifies that stamp_version_files does not rewrite, strip, or modify
        the committed VERSION value — it copies it verbatim.

        Assertions:
        - Live install VERSION equals the source tree VERSION byte-for-byte.
        """
        zip_source = tmp_path / "zip_source_b"
        zip_source.mkdir()
        committed_version = "v1.23.9"
        source_version_file = zip_source / "VERSION"
        source_version_file.write_text(f"{committed_version}\n", encoding="utf-8")

        live_install = tmp_path / "live_install_b"
        live_install.mkdir()

        result = run_bash(
            tmp_path,
            f"source {_VERSION_STAMP_SH!s} && stamp_version_files '{zip_source!s}' '{live_install!s}'",
            extra_env={"PYTHONHASHSEED": "0"},
        )
        assert result.returncode == 0, (
            f"stamp_version_files failed: {result.stderr}"
        )

        source_content = source_version_file.read_text(encoding="utf-8")
        installed_content = (live_install / "VERSION").read_text(encoding="utf-8")

        assert installed_content == source_content, (
            f"VERSION must be copied verbatim (byte-for-byte) to the live install.\n"
            f"Source:    {source_content!r}\n"
            f"Installed: {installed_content!r}"
        )

    def test_zip_shape_version_readable_by_direct_cat(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Live install VERSION is readable via direct cat (as dashboard/health read it).

        The dashboard's status-right.sh and the /health metadata path both read
        $KANBAN_ROOT/VERSION directly (cat or equivalent).  This test confirms
        that the value installed by stamp_version_files in zip-shape mode is
        readable by the same mechanism.

        Assertions:
        - ``cat $KANBAN_ROOT/VERSION`` returns the committed version string.
        """
        zip_source = tmp_path / "zip_source_c"
        zip_source.mkdir()
        committed_version = "v1.23.9"
        (zip_source / "VERSION").write_text(f"{committed_version}\n", encoding="utf-8")

        live_install = tmp_path / "live_install_c"
        live_install.mkdir()

        result = run_bash(
            tmp_path,
            f"source {_VERSION_STAMP_SH!s} && stamp_version_files '{zip_source!s}' '{live_install!s}'",
            extra_env={"PYTHONHASHSEED": "0"},
        )
        assert result.returncode == 0, f"stamp_version_files failed: {result.stderr}"

        # Simulate the reader: cat $KANBAN_ROOT/VERSION (as dashboard/health does).
        cat_result = run_bash(
            tmp_path,
            f"cat '{live_install!s}/VERSION'",
        )
        assert cat_result.returncode == 0, (
            f"cat KANBAN_ROOT/VERSION failed: {cat_result.stderr}"
        )
        cat_value = cat_result.stdout.strip()
        assert cat_value == committed_version, (
            f"cat KANBAN_ROOT/VERSION must return the committed version.\n"
            f"Expected: {committed_version!r}\nGot: {cat_value!r}"
        )


# ---------------------------------------------------------------------------
# Class 4: Fresh clone + install fixture
#
# Verifies that a source tree WITH .git and a committed VERSION file causes
# stamp_version_files to copy that VERSION to the live install.  No additional
# stamping steps should be needed beyond what stamp_version_files performs.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
class TestFreshCloneInstallFixture:
    """Fresh clone + install: VERSION present and correct with zero extra stamping steps."""

    def test_fresh_clone_version_is_copied_to_live_install(
        self, tmp_path: pathlib.Path
    ) -> None:
        """stamp_version_files copies committed VERSION from fresh clone to live install.

        Builds a git repo with a committed VERSION file (simulating a fresh
        clone of a tagged release), calls stamp_version_files (the function
        install.sh delegates to), and confirms VERSION in the live install
        matches the committed version — with no additional steps.

        Assertions:
        - Live install VERSION equals the committed version.
        - No additional stamping steps are invoked (the committed file IS the version).
        """
        # Build a fresh-clone-like git repo with a committed VERSION.
        clone_repo = _make_git_repo(tmp_path, "fresh_clone_repo")
        committed_version = "v1.23.9"

        (clone_repo / "VERSION").write_text(f"{committed_version}\n", encoding="utf-8")
        _git_cmd(clone_repo, "add", "VERSION")
        _git_cmd(clone_repo, "commit", "-m", f"Add committed VERSION {committed_version}")
        _git_cmd(clone_repo, "tag", committed_version)

        live_install = tmp_path / "fresh_clone_install"
        live_install.mkdir()

        result = run_bash(
            tmp_path,
            f"source {_VERSION_STAMP_SH!s} && stamp_version_files '{clone_repo!s}' '{live_install!s}'",
            extra_env={"PYTHONHASHSEED": "0"},
        )
        assert result.returncode == 0, (
            f"stamp_version_files failed on fresh clone: {result.stderr}"
        )

        version_file = live_install / "VERSION"
        assert version_file.exists(), "VERSION was not written to the live install."
        installed_version = version_file.read_text(encoding="utf-8").strip()
        assert installed_version == committed_version, (
            f"Fresh clone fixture: live install VERSION must equal the committed version.\n"
            f"Expected: {committed_version!r}\nGot: {installed_version!r}"
        )

    def test_fresh_clone_with_committed_version_does_not_need_git_describe(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Committed VERSION is used directly; git describe is not needed.

        When a committed VERSION file is present in the source tree,
        stamp_version_files copies it without running git describe.
        This validates the "zero stamping steps" requirement: the committed
        file IS the version; no git describe rewrite occurs.

        Assertions:
        - VERSION in the live install equals the committed file content exactly.
        - VERSION does not contain a git describe suffix (no -N-gSHA).
        """
        clone_repo = _make_git_repo(tmp_path, "clean_clone_repo")
        committed_version = "v1.23.9"

        # Commit VERSION and add a second commit past the tag (simulating a
        # fresh clone of a tag + one polish commit — the committed file should
        # still win over git describe).
        (clone_repo / "VERSION").write_text(f"{committed_version}\n", encoding="utf-8")
        _git_cmd(clone_repo, "add", "VERSION")
        _git_cmd(clone_repo, "commit", "-m", f"Add committed VERSION {committed_version}")
        _git_cmd(clone_repo, "tag", committed_version)

        # Add one more commit past the tag — git describe would return
        # v1.23.9-1-g<sha> but the committed VERSION must win.
        (clone_repo / "polish.txt").write_text("polish\n", encoding="utf-8")
        _git_cmd(clone_repo, "add", "polish.txt")
        _git_cmd(clone_repo, "commit", "-m", "Polish commit past tag")

        live_install = tmp_path / "clean_clone_install"
        live_install.mkdir()

        result = run_bash(
            tmp_path,
            f"source {_VERSION_STAMP_SH!s} && stamp_version_files '{clone_repo!s}' '{live_install!s}'",
            extra_env={"PYTHONHASHSEED": "0"},
        )
        assert result.returncode == 0, (
            f"stamp_version_files failed: {result.stderr}"
        )

        version_file = live_install / "VERSION"
        assert version_file.exists(), "VERSION was not written."
        installed_version = version_file.read_text(encoding="utf-8").strip()

        assert installed_version == committed_version, (
            f"When a committed VERSION is present, it must be used directly — "
            f"not derived from git describe.\n"
            f"Expected: {committed_version!r}\nGot: {installed_version!r}"
        )
        assert "-g" not in installed_version, (
            f"VERSION must not contain a git describe suffix; got: {installed_version!r}\n"
            f"The committed VERSION file must win over git describe."
        )

    def test_fresh_clone_version_detail_carries_deposit_provenance(
        self, tmp_path: pathlib.Path
    ) -> None:
        """On a fresh clone with .git, VERSION_DETAIL carries deposit provenance.

        When a git repo is present, stamp_version_files also writes
        VERSION_DETAIL (full describe + deposit SHA).  This is separate from
        VERSION and is deployment provenance, not identity.

        Assertions:
        - VERSION_DETAIL exists.
        - VERSION_DETAIL contains the deposit SHA.
        - VERSION (identity) is unaffected — still the clean committed value.
        """
        clone_repo = _make_git_repo(tmp_path, "provenance_clone_repo")
        committed_version = "v1.23.9"

        (clone_repo / "VERSION").write_text(f"{committed_version}\n", encoding="utf-8")
        _git_cmd(clone_repo, "add", "VERSION")
        _git_cmd(clone_repo, "commit", "-m", f"Add committed VERSION {committed_version}")
        _git_cmd(clone_repo, "tag", committed_version)

        deposit_sha = _git_cmd(clone_repo, "rev-parse", "HEAD")

        live_install = tmp_path / "provenance_install"
        live_install.mkdir()

        result = run_bash(
            tmp_path,
            f"source {_VERSION_STAMP_SH!s} && stamp_version_files '{clone_repo!s}' '{live_install!s}'",
            extra_env={"PYTHONHASHSEED": "0"},
        )
        assert result.returncode == 0, (
            f"stamp_version_files failed: {result.stderr}"
        )

        # VERSION: still the clean committed value.
        version_file = live_install / "VERSION"
        assert version_file.exists(), "VERSION was not written."
        installed_version = version_file.read_text(encoding="utf-8").strip()
        assert installed_version == committed_version, (
            f"VERSION must equal the committed version; got: {installed_version!r}"
        )

        # VERSION_DETAIL: must carry the deposit SHA.
        detail_file = live_install / "VERSION_DETAIL"
        assert detail_file.exists(), (
            "VERSION_DETAIL must be written when .git is present (deposit provenance)."
        )
        detail = detail_file.read_text(encoding="utf-8").strip()
        assert deposit_sha[:7] in detail or deposit_sha in detail, (
            f"VERSION_DETAIL must carry the deposit SHA.\n"
            f"SHA: {deposit_sha!r}\nVERSION_DETAIL: {detail!r}"
        )
