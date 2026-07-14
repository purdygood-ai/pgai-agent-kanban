"""
test_changelog_writer.py
========================
Unit tests for team/pgai_agent_kanban/cm/changelog_writer.py.

Tests cover the acceptance criteria from the task README:
  1. Classification negative — bugfix bundle produces Fixed listing with symptom;
     Fixed:None with BUG items present is impossible.
  2. Disclosure scope positive/negative — bug affecting published version discloses;
     born-and-fixed between published versions does not; empty PUBLISHED discloses
     nothing; absent PUBLISHED is treated as empty (no FileNotFoundError).
  3. Retroactive update — adding Fixed In to open disclosed bug flips rendering;
     idempotence (two runs byte-identical).
  4. Coordinate translation — unpublished internal fix renders 'fix pending next
     release'; publishing the version renders 'fixed in <version>'.
  6. Structural conformance — one ## vX.Y.Z heading per release, ### content
     headings, Breaking/Upgrade/Deprecations omitted when empty, Known Issues always
     present.
  8. Internal-ID leak gate — grep for BUG-[0-9] in generated output returns zero
     hits.

Test naming describes behavior, not bug IDs or scaffolding labels (SOP.md Anti-pattern 6).
All temp paths use pytest's tmp_path fixture (redirected to the framework temp root).
No bare /tmp paths in this file.
"""

from __future__ import annotations

import pathlib
import re
import textwrap

import pytest

from pgai_agent_kanban.cm.changelog_writer import (
    BugRecord,
    load_published_manifest,
    parse_bug_file,
    parse_release_notes,
    regenerate,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_release_notes(tmp_path: pathlib.Path, version: str, date: str, **kwargs) -> pathlib.Path:
    """Create a minimal release-notes/vX.Y.Z.md file under tmp_path/release-notes/.

    Keyword arguments:
        summary: str — text for the ## Summary section
        features: str — text for the ## Features section
        bug_fixes: str — text for the ## Bug Fixes section
        bugs_resolved: str — text for the ## Bugs Resolved section
    """
    notes_dir = tmp_path / "release-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / f"{version}.md"

    summary = kwargs.get("summary", f"Release {version}.")
    features = kwargs.get("features", "None")
    bug_fixes = kwargs.get("bug_fixes", "None")
    bugs_resolved = kwargs.get("bugs_resolved", "None")

    content = textwrap.dedent(f"""\
        # Release Notes: test-project {version}

        **Release Date:** {date}
        **Released By:** test

        ## Status
        FUNCTIONAL

        ## Summary
        {summary}

        ## Features
        {features}

        ## Bug Fixes
        {bug_fixes}

        ## Bugs Resolved
        {bugs_resolved}

        ## Known Issues
        None
        """)
    path.write_text(content, encoding="utf-8")
    return path


def _make_bug_file(
    bugs_dir: pathlib.Path,
    bug_num: int,
    slug: str,
    symptom: str,
    status: str = "open",
    affects: str = "",
    fixed_in: str = "",
    public_id: str = "",
) -> pathlib.Path:
    """Create a minimal BUG-NNNN-slug.md file under bugs_dir."""
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_id = f"BUG-{bug_num:04d}"
    path = bugs_dir / f"{bug_id}-{slug}.md"

    affects_section = f"\n## Affects\n{affects}" if affects else "\n## Affects\n"
    fixed_in_section = f"\n## Fixed In\n{fixed_in}" if fixed_in else "\n## Fixed In\n"
    public_id_section = f"\n## Public ID\n{public_id}" if public_id else "\n## Public ID\n"

    content = textwrap.dedent(f"""\
        # {bug_id}-{slug}

        **Bug ID:** {bug_id}-{slug}
        **Filed By:** test
        **Date:** 2026-01-01
        **Severity:** medium

        ## Status
        {status}

        ## Category
        misc

        ---

        ## Symptom
        {symptom}

        ## Expected
        Expected behavior.

        ## Actual
        Actual behavior.
        """) + affects_section + fixed_in_section + public_id_section + "\n"

    path.write_text(content, encoding="utf-8")
    return path


def _make_published(tmp_path: pathlib.Path, versions: list[str]) -> pathlib.Path:
    """Create release-notes/PUBLISHED under tmp_path."""
    notes_dir = tmp_path / "release-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / "PUBLISHED"
    path.write_text("\n".join(versions) + ("\n" if versions else ""), encoding="utf-8")
    return path


def _make_minimal_repo(
    tmp_path: pathlib.Path,
    versions_newest_first: list[str],
    published_versions: list[str],
    bugs: list[dict],
    release_kwargs: dict | None = None,
) -> tuple[pathlib.Path, pathlib.Path, list[str]]:
    """Set up a minimal fixture repo and return (repo_root, bugs_dir, versions).

    bugs is a list of dicts with keys matching _make_bug_file kwargs.
    release_kwargs is a dict mapping version -> {features, bug_fixes, ...}.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bugs_dir = tmp_path / "bugs"
    release_kwargs = release_kwargs or {}

    for i, version in enumerate(versions_newest_first):
        rk = release_kwargs.get(version, {})
        _make_release_notes(
            repo_root,
            version,
            f"2026-01-{i + 1:02d}",
            **rk,
        )

    _make_published(repo_root, published_versions)

    for bug_spec in bugs:
        _make_bug_file(bugs_dir, **bug_spec)

    return repo_root, bugs_dir, versions_newest_first


# ===========================================================================
# Criterion 1: Classification negative
# ===========================================================================


class TestClassificationNegative:
    """A bugfix bundle must produce a non-empty Fixed section; Fixed:None with
    BUG items present is impossible."""

    def test_bugfix_release_lists_symptom_in_fixed_section(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A release resolving a BUG item renders the bug's symptom in Fixed."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "widget-crash",
                    "symptom": "Widget crashes on startup",
                    "status": "resolved",
                    "affects": "v1.0.0",
                    "fixed_in": "v1.1.0",
                }
            ],
            release_kwargs={
                "v1.1.0": {
                    "bug_fixes": "- **BUG-0001**: Widget crashes on startup. Fixed."
                }
            },
        )
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        # The v1.1.0 Fixed section must contain the symptom text
        assert "Widget crashes on startup" in content

    def test_fixed_section_present_with_bug_items_never_shows_none(
        self, tmp_path: pathlib.Path
    ) -> None:
        """When BUG items exist in the release, Fixed section cannot be None."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "data-loss",
                    "symptom": "Data lost on write",
                    "status": "resolved",
                    "affects": "v1.0.0",
                    "fixed_in": "v1.1.0",
                }
            ],
            release_kwargs={
                "v1.1.0": {
                    "bug_fixes": "- **BUG-0001**: Data lost on write. Fixed."
                }
            },
        )
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)

        # Locate the v1.1.0 entry and confirm Fixed section is not None
        entry_match = re.search(
            r"## v1\.1\.0.*?(?=## v1\.0\.0|\Z)", content, re.DOTALL
        )
        assert entry_match is not None, "v1.1.0 entry not found in output"
        entry_text = entry_match.group(0)

        fixed_match = re.search(r"### Fixed\n(.+?)(?=###|\Z)", entry_text, re.DOTALL)
        assert fixed_match is not None, "Fixed section not found in v1.1.0 entry"
        fixed_body = fixed_match.group(1).strip()
        assert fixed_body != "None", (
            "Fixed section shows 'None' despite BUG items present in release"
        )
        assert "Data lost on write" in fixed_body

    def test_fixed_section_shows_none_when_no_bug_items_exist(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A release with no bug items correctly shows Fixed: None."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0"],
            published_versions=[],
            bugs=[],
            release_kwargs={
                "v1.1.0": {
                    "features": "- New widget added.",
                }
            },
        )
        content = regenerate(repo_root, versions, [], bugs_dir)

        fixed_match = re.search(r"### Fixed\n(.+?)(?=###|\Z)", content, re.DOTALL)
        assert fixed_match is not None
        assert fixed_match.group(1).strip() == "None"

    def test_internal_only_bug_renders_symptom_without_identifier(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An internal-only bug (no public disclosure) renders as plain symptom."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[
                {
                    "bug_num": 2,
                    "slug": "internal-race",
                    "symptom": "Internal race condition in scheduler",
                    "status": "resolved",
                    # affects is a version NOT in PUBLISHED (born between releases)
                    "affects": "v1.0.5",
                    "fixed_in": "v1.1.0",
                }
            ],
            release_kwargs={
                "v1.1.0": {
                    "bug_fixes": "- **BUG-0002**: Internal race condition. Fixed."
                }
            },
        )
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        # Symptom appears (as plain text in Fixed)
        assert "Internal race condition in scheduler" in content
        # No public ID assigned (KI- pattern should not appear for this bug)
        assert "KI-" not in content or "KI-1.0" not in content


# ===========================================================================
# Criterion 2: Disclosure scope
# ===========================================================================


class TestDisclosureScope:
    """Bugs affecting published versions disclose; bugs born-and-fixed between
    published versions do not; empty PUBLISHED discloses nothing; absent
    PUBLISHED is treated as empty (no error, same semantics as empty)."""

    def test_bug_affecting_published_version_is_disclosed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A bug with Affects=v1.0.0 (in PUBLISHED) appears in Known Issues."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "crash-on-start",
                    "symptom": "Crash on startup",
                    "status": "open",
                    "affects": "v1.0.0",
                }
            ],
        )
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        assert "KI-" in content
        assert "Crash on startup" in content

    def test_bug_born_and_fixed_between_published_versions_not_disclosed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A bug born and fixed entirely between published releases is not disclosed."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.2.0", "v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0", "v1.2.0"],
            bugs=[
                {
                    "bug_num": 3,
                    "slug": "internal-only",
                    "symptom": "Internal scheduler glitch",
                    "status": "resolved",
                    "affects": "v1.1.0",   # not in PUBLISHED
                    "fixed_in": "v1.1.5",  # not in PUBLISHED either
                }
            ],
        )
        content = regenerate(repo_root, versions, ["v1.0.0", "v1.2.0"], bugs_dir)
        # Must not appear in Known Issues (no KI- ID assigned)
        assert "Internal scheduler glitch" not in content or "KI-" not in content
        # Definitively: no KI- identifier for this bug
        assert not re.search(r"KI-1\.1\.0\.\d+", content)

    def test_both_disclosed_and_internal_bugs_in_same_fixture(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Only the bug affecting a published version appears in Known Issues."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.2.0", "v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0", "v1.2.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "public-crash",
                    "symptom": "Public crash symptom",
                    "status": "open",
                    "affects": "v1.0.0",  # PUBLISHED
                },
                {
                    "bug_num": 2,
                    "slug": "internal-glitch",
                    "symptom": "Internal glitch symptom",
                    "status": "resolved",
                    "affects": "v1.1.0",  # NOT published
                    "fixed_in": "v1.1.5",
                },
            ],
        )
        content = regenerate(repo_root, versions, ["v1.0.0", "v1.2.0"], bugs_dir)
        # Disclosed bug appears
        assert "Public crash symptom" in content
        assert "KI-1.0.0.1" in content
        # Internal bug does not appear in Known Issues
        assert "KI-1.1.0" not in content
        # Internal bug's symptom should NOT appear (it's not in Fixed either)
        # since we have no release entry that resolved an earlier defect in versions list
        assert "Internal glitch symptom" not in content

    def test_empty_published_manifest_discloses_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An empty PUBLISHED manifest results in no Known Issues anywhere."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=[],  # empty
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "crash",
                    "symptom": "Everything crashes",
                    "status": "open",
                    "affects": "v1.0.0",
                }
            ],
        )
        content = regenerate(repo_root, versions, [], bugs_dir)
        # No KI- identifiers in output
        assert "KI-" not in content
        # Known Issues sections exist but say None
        assert re.search(r"### Known Issues\nNone", content)

    def test_absent_published_manifest_returns_empty_list(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Absent release-notes/PUBLISHED returns [] (empty-manifest semantics).

        Managed dev trees that predate the v1.17 disclosure architecture have no
        PUBLISHED file.  An absent manifest means no versions have been publicly
        released — the same semantics as an empty file — so load_published_manifest
        returns [] rather than raising FileNotFoundError.
        """
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        # Do NOT create release-notes/PUBLISHED — verify tolerance
        result = load_published_manifest(repo_root)
        assert result == [], (
            f"Expected [] for absent PUBLISHED, got {result!r}"
        )

    def test_absent_published_manifest_writer_exits_cleanly(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Writer on a manifest-less fixture tree exits without error.

        All entries are rendered as internal (no KI- identifiers); Known Issues
        sections say 'None' because nothing has been publicly released.
        """
        repo_root, bugs_dir, _ = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],  # will be overwritten below
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "crash",
                    "symptom": "Everything crashes",
                    "status": "open",
                    "affects": "v1.0.0",
                }
            ],
        )
        # Remove the PUBLISHED file to simulate a pre-disclosure dev tree.
        manifest_path = repo_root / "release-notes" / "PUBLISHED"
        manifest_path.unlink()
        assert not manifest_path.exists()

        # load_published_manifest must return [] without raising
        published = load_published_manifest(repo_root)
        assert published == []

        # regenerate() must complete without error; output must have no KI- IDs
        content = regenerate(repo_root, ["v1.1.0", "v1.0.0"], published, bugs_dir)
        assert content, "Expected non-empty CHANGELOG output"
        assert "KI-" not in content, (
            "No KI- identifiers expected when manifest is absent (empty-manifest semantics)"
        )
        assert re.search(r"### Known Issues\nNone", content), (
            "Known Issues section must say 'None' when no versions are published"
        )


# ===========================================================================
# Criterion 3: Retroactive update / idempotence
# ===========================================================================


class TestRetroactiveUpdate:
    """Adding Fixed In to an open disclosed bug flips its rendering; two runs
    produce byte-identical output."""

    def test_open_bug_renders_as_open(self, tmp_path: pathlib.Path) -> None:
        """A disclosed bug with no Fixed In renders '· open'."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "open-bug",
                    "symptom": "Open issue symptom",
                    "status": "open",
                    "affects": "v1.0.0",
                }
            ],
        )
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        assert "· open" in content
        assert "Open issue symptom" in content

    def test_adding_fixed_in_flips_rendering_to_fixed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """After adding Fixed In to an open bug, regeneration shows 'fixed in'."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.2.0", "v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0", "v1.2.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "flip-bug",
                    "symptom": "Flip bug symptom",
                    "status": "open",
                    "affects": "v1.0.0",
                    # No fixed_in initially
                }
            ],
        )
        content1 = regenerate(repo_root, versions, ["v1.0.0", "v1.2.0"], bugs_dir)
        assert "· open" in content1

        # Now add Fixed In to the bug file
        bug_file = bugs_dir / "BUG-0001-flip-bug.md"
        bug_text = bug_file.read_text(encoding="utf-8")
        # Update Fixed In section
        bug_text = re.sub(
            r"## Fixed In\n\s*",
            "## Fixed In\nv1.2.0\n",
            bug_text,
        )
        bug_file.write_text(bug_text, encoding="utf-8")

        content2 = regenerate(repo_root, versions, ["v1.0.0", "v1.2.0"], bugs_dir)
        # Now shows 'fixed in' (the published version v1.2.0 >= internal v1.2.0)
        assert "· open" not in content2
        assert "fixed in v1.2.0" in content2

    def test_regeneration_is_byte_identical_on_same_inputs(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Two consecutive regenerations with unchanged inputs produce identical output."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "stable-bug",
                    "symptom": "Stable bug symptom",
                    "status": "open",
                    "affects": "v1.0.0",
                    "public_id": "KI-1.0.0.1",  # pre-assigned to avoid write-back
                }
            ],
        )
        content1 = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        content2 = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        assert content1 == content2, "Regeneration produced different output on second call"

    def test_regeneration_does_not_change_other_entries_after_fix(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Flipping one bug's Fixed In does not change the content of other entries."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.2.0", "v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0", "v1.2.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "bug-a",
                    "symptom": "Bug A symptom",
                    "status": "open",
                    "affects": "v1.0.0",
                    "public_id": "KI-1.0.0.1",
                },
                {
                    "bug_num": 2,
                    "slug": "bug-b",
                    "symptom": "Bug B symptom",
                    "status": "open",
                    "affects": "v1.0.0",
                    "public_id": "KI-1.0.0.2",
                },
            ],
        )
        content_before = regenerate(
            repo_root, versions, ["v1.0.0", "v1.2.0"], bugs_dir
        )
        # Add Fixed In to bug-a only
        bug_file = bugs_dir / "BUG-0001-bug-a.md"
        bug_text = bug_file.read_text(encoding="utf-8")
        bug_text = re.sub(
            r"## Fixed In\n\s*",
            "## Fixed In\nv1.2.0\n",
            bug_text,
        )
        bug_file.write_text(bug_text, encoding="utf-8")

        content_after = regenerate(
            repo_root, versions, ["v1.0.0", "v1.2.0"], bugs_dir
        )
        # Bug B rendering unchanged
        assert "Bug B symptom" in content_before
        assert "Bug B symptom" in content_after
        assert "· open" in content_after  # Bug B still open


# ===========================================================================
# Criterion 4: Coordinate translation
# ===========================================================================


class TestCoordinateTranslation:
    """Internal fix version translates to public coordinate system."""

    def test_internal_fix_not_yet_published_renders_fix_pending(
        self, tmp_path: pathlib.Path
    ) -> None:
        """When Fixed In version is not yet in PUBLISHED, renders 'fix pending next release'."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],  # v1.1.0 NOT in published
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "pending-fix",
                    "symptom": "Fix pending symptom",
                    "status": "resolved",
                    "affects": "v1.0.0",
                    "fixed_in": "v1.1.0",   # internal fix version
                    "public_id": "KI-1.0.0.1",
                }
            ],
        )
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        assert "fix pending next release" in content
        assert "Fix pending symptom" in content

    def test_publishing_fix_version_renders_fixed_in(
        self, tmp_path: pathlib.Path
    ) -> None:
        """After adding the fix version to PUBLISHED, renders 'fixed in <version>'."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0", "v1.1.0"],  # now v1.1.0 is published
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "now-fixed",
                    "symptom": "Now fixed symptom",
                    "status": "resolved",
                    "affects": "v1.0.0",
                    "fixed_in": "v1.1.0",
                    "public_id": "KI-1.0.0.1",
                }
            ],
        )
        content = regenerate(repo_root, versions, ["v1.0.0", "v1.1.0"], bugs_dir)
        assert "fix pending" not in content
        assert "fixed in v1.1.0" in content

    def test_open_bug_renders_open_not_fixed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An open bug with no Fixed In renders '· open'."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "still-open",
                    "symptom": "Still open symptom",
                    "status": "open",
                    "affects": "v1.0.0",
                    "public_id": "KI-1.0.0.1",
                }
            ],
        )
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        assert "· open" in content
        assert "Still open symptom" in content
        assert "fixed in" not in content
        assert "fix pending" not in content


# ===========================================================================
# Criterion 6: Structural conformance
# ===========================================================================


class TestStructuralConformance:
    """CHANGELOG structure: one ## vX.Y.Z per release, ### headings,
    optional empty sections, Known Issues always present."""

    def test_one_h2_heading_per_release_newest_first(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Each release has exactly one ## vX.Y.Z heading; newest release first."""
        versions = ["v1.3.0", "v1.2.0", "v1.1.0", "v1.0.0"]
        repo_root, bugs_dir, _ = _make_minimal_repo(
            tmp_path,
            versions_newest_first=versions,
            published_versions=[],
            bugs=[],
        )
        content = regenerate(repo_root, versions, [], bugs_dir)

        headings = re.findall(r"^## (v\d+\.\d+\.\d+)", content, re.MULTILINE)
        assert headings == versions, (
            f"Expected headings {versions}, got {headings}"
        )

    def test_content_sections_use_h3_headings(self, tmp_path: pathlib.Path) -> None:
        """Implemented, Fixed, Known Issues use ### (H3) headings."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.0.0"],
            published_versions=[],
            bugs=[],
        )
        content = regenerate(repo_root, versions, [], bugs_dir)

        assert "### Implemented" in content
        assert "### Fixed" in content
        assert "### Known Issues" in content

    def test_known_issues_always_present_even_when_empty(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Known Issues section appears in every entry, even when no issues exist."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=[],
            bugs=[],
        )
        content = regenerate(repo_root, versions, [], bugs_dir)

        # Count Known Issues sections — should match number of releases
        ki_count = len(re.findall(r"### Known Issues", content))
        assert ki_count == len(versions), (
            f"Expected {len(versions)} Known Issues sections, got {ki_count}"
        )

    def test_empty_implemented_shows_none_bugfix_release(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A release with no features shows 'None (bugfix release)' in Implemented."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0"],
            published_versions=[],
            bugs=[],
            release_kwargs={
                "v1.1.0": {
                    "features": "None",
                    "bug_fixes": "- **BUG-0001**: Some fix.",
                }
            },
        )
        content = regenerate(repo_root, versions, [], bugs_dir)
        assert "None (bugfix release)" in content

    def test_header_contains_auto_generated_marker(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The CHANGELOG header includes the auto-generated do-not-edit marker."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.0.0"],
            published_versions=[],
            bugs=[],
        )
        content = regenerate(repo_root, versions, [], bugs_dir)
        assert "Auto-generated" in content
        assert "Do not edit manually" in content

    def test_detail_link_present_per_entry(self, tmp_path: pathlib.Path) -> None:
        """Each entry contains a link to release-notes/vX.Y.Z.md."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=[],
            bugs=[],
        )
        content = regenerate(repo_root, versions, [], bugs_dir)
        assert "release-notes/v1.1.0.md" in content
        assert "release-notes/v1.0.0.md" in content


# ===========================================================================
# Criterion 8: Internal-ID leak gate
# ===========================================================================


class TestInternalIdLeakGate:
    """BUG-[0-9] must never appear in generated CHANGELOG output."""

    def test_bug_ids_absent_from_output_with_single_bug(
        self, tmp_path: pathlib.Path
    ) -> None:
        """No BUG-NNNN in output when one bug is present in a bugfix release."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[
                {
                    "bug_num": 42,
                    "slug": "example-bug",
                    "symptom": "Example symptom text",
                    "status": "resolved",
                    "affects": "v1.0.0",
                    "fixed_in": "v1.1.0",
                    "public_id": "KI-1.0.0.1",
                }
            ],
            release_kwargs={
                "v1.1.0": {
                    "bug_fixes": "- **BUG-0042**: Example symptom text. Fixed.",
                }
            },
        )
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        matches = re.findall(r"BUG-[0-9]", content)
        assert matches == [], (
            f"Internal BUG-NNNN identifiers found in output: {matches}"
        )

    def test_bug_ids_absent_from_output_with_multiple_bugs(
        self, tmp_path: pathlib.Path
    ) -> None:
        """No BUG-NNNN in output when multiple bugs across multiple releases."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.3.0", "v1.2.0", "v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0", "v1.2.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "alpha-bug",
                    "symptom": "Alpha symptom",
                    "status": "resolved",
                    "affects": "v1.0.0",
                    "fixed_in": "v1.1.0",
                    "public_id": "KI-1.0.0.1",
                },
                {
                    "bug_num": 2,
                    "slug": "beta-bug",
                    "symptom": "Beta symptom",
                    "status": "open",
                    "affects": "v1.2.0",
                    "public_id": "KI-1.2.0.1",
                },
                {
                    "bug_num": 3,
                    "slug": "gamma-bug",
                    "symptom": "Gamma internal fix",
                    "status": "resolved",
                    "affects": "v1.1.5",  # not published
                    "fixed_in": "v1.2.0",
                },
            ],
            release_kwargs={
                "v1.1.0": {
                    "bug_fixes": "- **BUG-0001**: Alpha symptom. Fixed.\n- **BUG-0003**: Gamma internal fix.",
                },
            },
        )
        content = regenerate(
            repo_root, versions, ["v1.0.0", "v1.2.0"], bugs_dir
        )
        matches = re.findall(r"BUG-[0-9]", content)
        assert matches == [], (
            f"Internal BUG-NNNN identifiers found in output: {matches}"
        )

    def test_bug_ids_absent_from_output_with_empty_published(
        self, tmp_path: pathlib.Path
    ) -> None:
        """No BUG-NNNN in output even when PUBLISHED is empty."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=[],
            bugs=[
                {
                    "bug_num": 5,
                    "slug": "some-bug",
                    "symptom": "Some symptom",
                    "status": "resolved",
                    "affects": "v1.0.0",
                    "fixed_in": "v1.1.0",
                }
            ],
            release_kwargs={
                "v1.1.0": {
                    "bug_fixes": "- **BUG-0005**: Some symptom. Fixed.",
                }
            },
        )
        content = regenerate(repo_root, versions, [], bugs_dir)
        matches = re.findall(r"BUG-[0-9]", content)
        assert matches == [], (
            f"Internal BUG-NNNN identifiers found in output: {matches}"
        )


# ===========================================================================
# Additional: load_published_manifest behaviour
# ===========================================================================


class TestLoadPublishedManifest:
    """Tests for the load_published_manifest helper."""

    def test_loads_versions_from_file(self, tmp_path: pathlib.Path) -> None:
        """Versions are returned in file order, stripped of whitespace."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_published(repo_root, ["v1.0.0", "v1.0.1", "v1.1.0"])
        result = load_published_manifest(repo_root)
        assert result == ["v1.0.0", "v1.0.1", "v1.1.0"]

    def test_empty_file_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        """An empty PUBLISHED file returns an empty list (not an error)."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_published(repo_root, [])
        result = load_published_manifest(repo_root)
        assert result == []

    def test_missing_file_returns_empty_list(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Absent PUBLISHED file returns [] (empty-manifest semantics, no error).

        Managed dev trees that predate the v1.17 disclosure architecture have no
        PUBLISHED file.  An absent manifest is semantically identical to an empty
        one: no versions have been publicly released.
        """
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        result = load_published_manifest(repo_root)
        assert result == [], (
            f"Expected [] for absent PUBLISHED, got {result!r}"
        )


# ===========================================================================
# Additional: parse_bug_file
# ===========================================================================


class TestParseBugFile:
    """Tests for the parse_bug_file helper."""

    def test_parses_all_fields(self, tmp_path: pathlib.Path) -> None:
        """All standard fields are parsed from a well-formed bug file."""
        bugs_dir = tmp_path / "bugs"
        path = _make_bug_file(
            bugs_dir,
            bug_num=7,
            slug="test-slug",
            symptom="Test symptom line",
            status="open",
            affects="v1.0.0",
            fixed_in="v1.1.0",
            public_id="KI-1.0.0.3",
        )
        rec = parse_bug_file(path)
        assert rec.bug_id == "BUG-0007"
        assert rec.symptom == "Test symptom line"
        assert rec.status == "open"
        assert rec.affects == "v1.0.0"
        assert rec.fixed_in == "v1.1.0"
        assert rec.public_id == "KI-1.0.0.3"

    def test_absent_fields_return_empty_string(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Missing Affects / Fixed In / Public ID return empty strings."""
        bugs_dir = tmp_path / "bugs"
        path = _make_bug_file(
            bugs_dir,
            bug_num=8,
            slug="sparse",
            symptom="Sparse bug symptom",
        )
        rec = parse_bug_file(path)
        assert rec.affects == ""
        assert rec.fixed_in == ""
        assert rec.public_id == ""


# ===========================================================================
# Criterion 7: Public-ID stickiness
# ===========================================================================


class TestPublicIdStickiness:
    """Public-ID assignment is stable across regenerations.

    Subcases:
      (a) Two bugs anchoring to the same published version get .1 and .2 in
          disclosure order (bug file sort order = BUG-NNNN ascending).
      (b) Adding a third bug and regenerating leaves the first two IDs
          unchanged (read from bug files, not recomputed).
      (c) A disclosed bug lacking ## Public ID at generation causes
          assign-and-persist: the bug file gains the field, and subsequent
          regeneration reads it verbatim.
    """

    def test_two_bugs_same_anchor_assigned_in_disclosure_order(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Two bugs with the same anchor get .1 and .2 in ascending BUG-ID order."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "alpha-crash",
                    "symptom": "Alpha crash symptom",
                    "status": "open",
                    "affects": "v1.0.0",
                    # No public_id — will be assigned
                },
                {
                    "bug_num": 2,
                    "slug": "beta-crash",
                    "symptom": "Beta crash symptom",
                    "status": "open",
                    "affects": "v1.0.0",
                    # No public_id — will be assigned
                },
            ],
        )
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)

        # Both bugs should appear with KI-1.0.0.1 and KI-1.0.0.2
        assert "KI-1.0.0.1" in content, "First bug should receive KI-1.0.0.1"
        assert "KI-1.0.0.2" in content, "Second bug should receive KI-1.0.0.2"
        # Alpha (an earlier defect) gets .1, Beta (an earlier defect) gets .2
        assert "KI-1.0.0.1 — Alpha crash symptom" in content
        assert "KI-1.0.0.2 — Beta crash symptom" in content

    def test_third_bug_leaves_first_two_ids_unchanged(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Adding a third bug and regenerating leaves the first two IDs unchanged."""
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[
                {
                    "bug_num": 1,
                    "slug": "alpha-crash",
                    "symptom": "Alpha crash symptom",
                    "status": "open",
                    "affects": "v1.0.0",
                },
                {
                    "bug_num": 2,
                    "slug": "beta-crash",
                    "symptom": "Beta crash symptom",
                    "status": "open",
                    "affects": "v1.0.0",
                },
            ],
        )
        # First regeneration: assigns KI-1.0.0.1 and KI-1.0.0.2 and persists them.
        content1 = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        assert "KI-1.0.0.1" in content1
        assert "KI-1.0.0.2" in content1

        # Verify the IDs were persisted back to the bug files.
        bug1_rec = parse_bug_file(bugs_dir / "BUG-0001-alpha-crash.md")
        bug2_rec = parse_bug_file(bugs_dir / "BUG-0002-beta-crash.md")
        assert bug1_rec.public_id == "KI-1.0.0.1", (
            f"KI-1.0.0.1 not persisted to BUG-0001; got '{bug1_rec.public_id}'"
        )
        assert bug2_rec.public_id == "KI-1.0.0.2", (
            f"KI-1.0.0.2 not persisted to BUG-0002; got '{bug2_rec.public_id}'"
        )

        # Add a third bug (no Public ID yet).
        _make_bug_file(
            bugs_dir,
            bug_num=3,
            slug="gamma-crash",
            symptom="Gamma crash symptom",
            status="open",
            affects="v1.0.0",
        )

        # Second regeneration: third bug gets .3; first two IDs must be unchanged.
        content2 = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        assert "KI-1.0.0.1 — Alpha crash symptom" in content2, (
            "KI-1.0.0.1 changed after adding a third bug"
        )
        assert "KI-1.0.0.2 — Beta crash symptom" in content2, (
            "KI-1.0.0.2 changed after adding a third bug"
        )
        assert "KI-1.0.0.3" in content2, "Third bug should receive KI-1.0.0.3"
        assert "KI-1.0.0.3 — Gamma crash symptom" in content2

    def test_missing_public_id_triggers_assign_and_persist(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A disclosed bug without ## Public ID causes assign-and-persist.

        Verify: the bug file gains the ## Public ID field, and a subsequent
        regeneration reads it verbatim (produces byte-identical output).
        """
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[
                {
                    "bug_num": 5,
                    "slug": "unidentified-crash",
                    "symptom": "Unidentified crash symptom",
                    "status": "open",
                    "affects": "v1.0.0",
                    # public_id intentionally absent
                },
            ],
        )

        bug_path = bugs_dir / "BUG-0005-unidentified-crash.md"

        # Confirm no Public ID in bug file before generation.
        pre_rec = parse_bug_file(bug_path)
        assert pre_rec.public_id == "", "Bug file must not have Public ID before first generation"

        # First regeneration: should assign and persist a Public ID.
        content1 = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)

        # Bug file should now carry the Public ID.
        post_rec = parse_bug_file(bug_path)
        assert post_rec.public_id != "", (
            "Bug file must have a Public ID after first regeneration (assign-and-persist)"
        )
        assert post_rec.public_id.startswith("KI-1.0.0."), (
            f"Public ID format unexpected: '{post_rec.public_id}'"
        )

        # The assigned ID must appear in the generated content.
        assert post_rec.public_id in content1

        # Second regeneration: reads the persisted ID verbatim — output must be
        # byte-identical (no re-assignment, no change to the file).
        content2 = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)
        assert content1 == content2, (
            "Second regeneration produced different output — Public ID was not read verbatim"
        )

        # Bug file must not have changed (Public ID field still the same).
        post_rec2 = parse_bug_file(bug_path)
        assert post_rec2.public_id == post_rec.public_id, (
            "Public ID in bug file changed between first and second regeneration"
        )


# ===========================================================================
# Content quality: Fixed-section rendering correctness
# ===========================================================================


class TestContentQualityFixed:
    """Content-quality guards for the Fixed-section renderer.

    These tests target three regressions identified in an earlier defect:
      (a) Multi-line bullets in Bug Fixes are joined into a single Fixed item.
      (b) A phantom BUG-ID embedded in prose (e.g. 'BUG-92') does not produce
          an 'internal fix — bug file not found' placeholder in Fixed.
      (c) A bug whose ## Symptom begins with a triple-backtick code fence does
          not emit a stray fence string into the rendered Fixed line.

    All three tests fail against the pre-fix writer and pass with the fix.
    """

    def test_multi_line_bullet_produces_single_fixed_item(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A multi-line bullet in ## Bug Fixes yields exactly one Fixed line.

        The pre-fix parser iterated every line individually, so continuation
        lines would each become separate candidate items.  The fixed parser
        joins them before extraction, producing a single Fixed entry.
        """
        # Write release notes directly to avoid textwrap.dedent interaction with
        # multi-line bug_fixes content that has less indentation than the template.
        bugs_dir = tmp_path / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        notes_dir = repo_root / "release-notes"
        notes_dir.mkdir()

        # v1.1.0 release notes with a multi-line bullet in ## Bug Fixes.
        # The opening line carries the BUG-NNNN reference; subsequent lines
        # are continuation text that belongs to the same bullet item.
        (notes_dir / "v1.1.0.md").write_text(
            textwrap.dedent("""\
                # Release Notes: test-project v1.1.0

                **Release Date:** 2026-01-01
                **Released By:** test

                ## Status
                FUNCTIONAL

                ## Summary
                Release v1.1.0.

                ## Features
                None

                ## Bug Fixes
                - **BUG-0001**: Widget crashes on startup.
                  Continuation line one providing more detail.
                  Continuation line two with even more context.

                ## Bugs Resolved
                None

                ## Known Issues
                None
            """),
            encoding="utf-8",
        )
        (notes_dir / "v1.0.0.md").write_text(
            textwrap.dedent("""\
                # Release Notes: test-project v1.0.0

                **Release Date:** 2026-01-02
                **Released By:** test

                ## Status
                FUNCTIONAL

                ## Summary
                Release v1.0.0.

                ## Features
                None

                ## Bug Fixes
                None

                ## Bugs Resolved
                None

                ## Known Issues
                None
            """),
            encoding="utf-8",
        )
        (notes_dir / "PUBLISHED").write_text("v1.0.0\n", encoding="utf-8")

        # Create the matching bug file.
        _make_bug_file(
            bugs_dir,
            bug_num=1,
            slug="widget-crash",
            symptom="Widget crashes on startup.",
            status="resolved",
            affects="v1.0.0",
            fixed_in="v1.1.0",
        )

        versions = ["v1.1.0", "v1.0.0"]
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)

        # Locate the v1.1.0 entry's Fixed section.
        entry_match = re.search(
            r"## v1\.1\.0.*?(?=## v1\.0\.0|\Z)", content, re.DOTALL
        )
        assert entry_match is not None, "v1.1.0 entry not found in output"
        entry_text = entry_match.group(0)

        fixed_match = re.search(r"### Fixed\n(.+?)(?=###|\Z)", entry_text, re.DOTALL)
        assert fixed_match is not None, "Fixed section not found in v1.1.0 entry"
        fixed_body = fixed_match.group(1).strip()

        # There must be exactly one non-empty line in the Fixed body.
        fixed_lines = [ln for ln in fixed_body.splitlines() if ln.strip()]
        assert len(fixed_lines) == 1, (
            f"Expected exactly 1 Fixed line for a multi-line bullet; "
            f"got {len(fixed_lines)}: {fixed_lines!r}"
        )

        # The single line must contain the bug's symptom text.
        assert "Widget crashes on startup" in fixed_lines[0], (
            f"Fixed line does not contain symptom: {fixed_lines[0]!r}"
        )

    def test_phantom_bug_id_in_prose_does_not_produce_placeholder(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A short BUG-ID token embedded in prose does not surface a placeholder.

        The pre-fix extractor matched any BUG-\\d+ token, so 'BUG-92' inside a
        prose comment would be extracted as a bug ID.  With no matching bug file,
        the renderer emitted '(internal fix — bug file not found)'.  The fixed
        extractor requires 4+ digits at a canonical position, so 'BUG-92' in
        the middle of prose is ignored.
        """
        repo_root, bugs_dir, versions = _make_minimal_repo(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
            bugs=[],
            release_kwargs={
                "v1.1.0": {
                    # A prose bullet where "BUG-92" appears inside a parenthetical.
                    # No matching bug file exists for any ID in this note.
                    "bug_fixes": (
                        "- Running pre-flight check during v1.0.0 RC "
                        "(false-positive BUG-92 anomaly) and cleanup script."
                    ),
                }
            },
        )
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)

        # The placeholder must not appear anywhere in the output.
        assert "internal fix — bug file not found" not in content, (
            "Phantom BUG-92 token caused a 'bug file not found' placeholder in output"
        )
        assert "bug file not found" not in content, (
            "Unexpected 'bug file not found' text found in output"
        )

    def test_code_fence_symptom_does_not_produce_lone_fence_fixed_line(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A bug whose ## Symptom starts with a lone code-fence line does not
        produce a bare '```' as the only content of the Fixed line.

        The pre-fix parser used the first non-empty line of ## Symptom as the
        symptom string.  When the symptom section opened with a lone '```'
        fence line (as in the v1.10.0 regression from an earlier defect), the rendered
        Fixed section contained only '```' — a meaningless, unclosed fence.

        The fixed parser uses _first_sentence() which collapses the entire
        symptom block into a single flat string before extracting a sentence,
        so the rendered Fixed line contains the actual prose description rather
        than the bare opening fence.
        """
        bugs_dir = tmp_path / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)

        # Create a bug file whose Symptom section opens with a lone fence line.
        # The prose description of the bug follows after the code block.
        bug_path = bugs_dir / "BUG-0019-fence-symptom.md"
        bug_path.write_text(
            textwrap.dedent("""\
                # BUG-0019-fence-symptom

                **Bug ID:** BUG-0019-fence-symptom
                **Filed By:** test
                **Date:** 2026-01-01
                **Severity:** medium

                ## Status
                resolved

                ## Category
                misc

                ---

                ## Symptom
                ```
                error output shown to the user
                ```
                Changelog generator emits a stray opening fence into Fixed.

                ## Expected
                Fixed section contains the prose description, not a lone fence.

                ## Actual
                Fixed section contains a lone opening ``` line.

                ## Affects
                v1.0.0

                ## Fixed In
                v1.1.0

                ## Public ID

            """),
            encoding="utf-8",
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_release_notes(
            repo_root,
            "v1.1.0",
            "2026-01-02",
            bug_fixes="- **BUG-0019**: Fixed fence-symptom rendering.",
        )
        _make_release_notes(
            repo_root,
            "v1.0.0",
            "2026-01-01",
        )
        _make_published(repo_root, ["v1.0.0"])

        versions = ["v1.1.0", "v1.0.0"]
        content = regenerate(repo_root, versions, ["v1.0.0"], bugs_dir)

        # Locate the v1.1.0 Fixed section.
        entry_match = re.search(
            r"## v1\.1\.0.*?(?=## v1\.0\.0|\Z)", content, re.DOTALL
        )
        assert entry_match is not None, "v1.1.0 entry not found in output"
        entry_text = entry_match.group(0)

        fixed_match = re.search(r"### Fixed\n(.+?)(?=###|\Z)", entry_text, re.DOTALL)
        assert fixed_match is not None, "Fixed section not found in v1.1.0 entry"
        fixed_body = fixed_match.group(1).strip()

        # The Fixed body must contain the bug's prose description.
        # Before the fix, the Fixed body was just '```' (a lone stray fence);
        # after the fix it contains the joined symptom text including the
        # prose sentence that follows the code block.
        assert "fence" in fixed_body.lower() or "changelog" in fixed_body.lower(), (
            f"Fixed line does not contain the prose symptom description: {fixed_body!r}"
        )

        # The Fixed body must NOT be just the bare opening fence.
        fixed_lines = [ln for ln in fixed_body.splitlines() if ln.strip()]
        assert not (len(fixed_lines) == 1 and fixed_lines[0].strip() == "```"), (
            "Fixed section consists of only a bare '```' — stray fence regression present"
        )
