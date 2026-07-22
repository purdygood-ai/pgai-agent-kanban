"""
test_changelog_writer_real_data.py
===================================
Real-data acceptance test for the CHANGELOG writer (criterion 5 and criterion 9
from the v1.17.0 release-summary-ledger-projection requirements).

This test runs the writer against the ACTUAL release-notes/ and bugs/ trees
(not synthetic fixtures) and asserts:
  (a) Criterion 5a — the regenerated v1.0.0 entry carries KI-1.0.0.1 with the
      tmux symptom line and the correct affects/fixed-in disclosure per the
      PUBLISHED manifest state at implementation time.
  (b) Criterion 9 — the entry count in the regenerated file equals the count of
      release-notes/vX.Y.Z.md files (release-entry preservation).
  (c) Criterion 5c — an earlier defect's file carries ## Public ID: KI-1.0.0.1.
  (d) Criterion 5d / 8 — grep -E 'BUG-[0-9]' of the final file returns zero hits.

Skipped when:
  - release-notes/PUBLISHED is absent from the repo root (pre-publication state;
    the writer would raise FileNotFoundError — not a test environment error).
  - The live kanban bugs directory does not exist at the path derived from
    PGAI_AGENT_KANBAN_ROOT_PATH (captured at import time, before conftest's
    autouse monkeypatching redirects that env var to a temp path).

Design notes:
  - The real kanban root and project bugs directory are captured at MODULE IMPORT
    TIME (module-level constants) to escape the autouse _block_live_kanban_writes
    fixture that redirects PGAI_AGENT_KANBAN_ROOT_PATH inside each test.
  - The test does NOT write to any live kanban file.  It regenerates into a
    tmp_path scratch area, leaving the live dev tree untouched.
  - The test is self-contained: it reads real files but produces no persistent
    side effects.  Running it twice produces the same outcome.
  - Test naming describes behavior under test (SOP.md Anti-pattern 6 compliance).
  - All temp paths use pytest's tmp_path (redirected by conftest to the framework
    temp root via PYTEST_DEBUG_TEMPROOT).
"""

from __future__ import annotations

import os as _os
import pathlib
import re
import shutil

import pytest

from pgai_agent_kanban.cm.changelog_writer import (
    load_published_manifest,
    parse_bug_file,
    regenerate,
    _earliest_published_gte,
    _parse_version,
)

# ---------------------------------------------------------------------------
# Module-level path resolution — captured before conftest monkeypatching
# ---------------------------------------------------------------------------

# The repo root is the directory four levels above this file:
#   team/tests/unit/test_changelog_writer_real_data.py
#            └── unit/
#        └── tests/
#    └── team/
# └── <repo-root>/
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.parent

# The live kanban root is resolved from PGAI_AGENT_KANBAN_ROOT_PATH at
# import time, before conftest's autouse fixture redirects the env var.
_LIVE_KANBAN_ROOT = pathlib.Path(
    _os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")
    or str(pathlib.Path.home() / "pgai_agent_kanban")
)

# Project-scoped bugs directory under the live kanban root.
_REAL_BUGS_DIR = _LIVE_KANBAN_ROOT / "projects" / "pgai-agent-kanban" / "bugs"


def _skip_if_missing(reason: str) -> None:
    """Unconditionally call pytest.skip with the provided reason."""
    pytest.skip(reason)


def _real_data_available() -> tuple[bool, str]:
    """Return (True, '') when real data prerequisites are met; (False, reason) otherwise."""
    if not (_REPO_ROOT / "release-notes" / "PUBLISHED").exists():
        return False, (
            f"release-notes/PUBLISHED not found under repo root {_REPO_ROOT} "
            "— pre-publication state; no Known Issues to assert."
        )
    if not _REAL_BUGS_DIR.exists():
        return False, (
            f"Live kanban bugs directory not found: {_REAL_BUGS_DIR} "
            "— live install not present in this environment."
        )
    return True, ""


# ===========================================================================
# Real-data acceptance tests (criteria 5 and 9)
# ===========================================================================


class TestRealDataChangelog:
    """Real-data acceptance tests for the CHANGELOG writer.

    All assertions are derived from the requirements at:
      requirements/v1.17.0-release-summary-ledger-projection.md

    The test class skips as a whole when the live environment is not
    present, rather than failing — a missing live install is not a code
    defect.
    """

    def _ensure_data_available(self) -> None:
        ok, reason = _real_data_available()
        if not ok:
            pytest.skip(reason)

    def _regenerate_into_scratch(self, scratch_dir: pathlib.Path) -> tuple[str, pathlib.Path]:
        """Run the writer against real release-notes and bugs, writing to scratch_dir.

        Returns (content, changelog_path).
        """
        # Discover release versions from the real release-notes dir, newest first.
        notes_dir = _REPO_ROOT / "release-notes"
        found = sorted(
            notes_dir.glob("v*.md"),
            key=lambda p: _parse_version(p.stem),
            reverse=True,
        )
        versions = [p.stem for p in found]

        # Load the PUBLISHED manifest from the real repo root.
        published = load_published_manifest(_REPO_ROOT)

        # Regenerate into scratch — never write to the live repo root.
        scratch_changelog = scratch_dir / "CHANGELOG.md"
        content = regenerate(_REPO_ROOT, versions, published, _REAL_BUGS_DIR)
        scratch_changelog.write_text(content, encoding="utf-8")
        return content, scratch_changelog

    def test_release_entry_count_matches_release_notes_file_count(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Criterion 9: entry count in regenerated CHANGELOG equals release-notes file count.

        The writer must produce exactly one ## vX.Y.Z heading for every
        release-notes/vX.Y.Z.md file present in the repo — no release is
        dropped or duplicated in the migration.
        """
        self._ensure_data_available()

        notes_dir = _REPO_ROOT / "release-notes"
        release_note_files = list(notes_dir.glob("v*.md"))
        expected_count = len(release_note_files)

        assert expected_count > 0, (
            f"No release-notes/v*.md files found under {notes_dir}"
        )

        content, _ = self._regenerate_into_scratch(tmp_path)
        headings = re.findall(r"^## (v\d+\.\d+\.\d+)", content, re.MULTILINE)

        assert len(headings) == expected_count, (
            f"CHANGELOG has {len(headings)} entries but "
            f"release-notes/ has {expected_count} files. "
            f"Missing or extra versions: "
            f"{set(headings).symmetric_difference({p.stem for p in release_note_files})}"
        )

    def test_v1_0_0_entry_carries_ki_1_0_0_1_with_tmux_symptom(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Criterion 5a: v1.0.0 entry carries KI-1.0.0.1 with the tmux symptom.

        The Known Issues section under v1.0.0 must disclose the tmux 3.4
        dashboard failure using the public ID KI-1.0.0.1 and include the
        symptom line ('size missing').
        """
        self._ensure_data_available()

        content, _ = self._regenerate_into_scratch(tmp_path)

        # Extract the v1.0.0 entry
        entry_match = re.search(
            r"## v1\.0\.0.*?(?=\n## v|\Z)", content, re.DOTALL
        )
        assert entry_match is not None, "v1.0.0 entry not found in regenerated CHANGELOG"
        entry_text = entry_match.group(0)

        # KI-1.0.0.1 must appear in the v1.0.0 entry
        assert "KI-1.0.0.1" in entry_text, (
            "KI-1.0.0.1 not found in v1.0.0 entry of regenerated CHANGELOG"
        )

        # The tmux symptom must appear (the bug is about 'size missing' error)
        assert "size missing" in entry_text, (
            "tmux 'size missing' symptom not found in v1.0.0 Known Issues"
        )

    def test_v1_0_0_entry_carries_correct_affects_fixed_in_disclosure(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Criterion 5a: v1.0.0 entry shows correct affects/fixed-in per PUBLISHED state.

        The underlying defect (BUG-0023 / KI-1.0.0.1) was fixed internally in v1.16.2.
        The writer's rendering rule: if the earliest PUBLISHED version >= v1.16.2 exists,
        the KI line reads 'fixed in <that-version>'; otherwise it reads
        'fix pending next release'.  This test reflects that behavioral contract
        for whatever PUBLISHED manifest is checked into the repo at test time.
        """
        self._ensure_data_available()

        published = load_published_manifest(_REPO_ROOT)

        content, _ = self._regenerate_into_scratch(tmp_path)

        # Extract the v1.0.0 entry
        entry_match = re.search(
            r"## v1\.0\.0.*?(?=\n## v|\Z)", content, re.DOTALL
        )
        assert entry_match is not None, "v1.0.0 entry not found in regenerated CHANGELOG"
        entry_text = entry_match.group(0)

        # Find the KI-1.0.0.1 line in the entry
        ki_line_match = re.search(r"KI-1\.0\.0\.1.*", entry_text)
        assert ki_line_match is not None, "KI-1.0.0.1 line not found in v1.0.0 entry"
        ki_line = ki_line_match.group(0)

        # The line must contain 'affects v1.0.0'
        assert "affects v1.0.0" in ki_line, (
            f"Expected 'affects v1.0.0' in KI line but got: {ki_line!r}"
        )

        # Rendering depends on PUBLISHED state — mirrors the writer's
        # _earliest_published_gte rule anchored to the internal fix version v1.16.2:
        #   earliest PUBLISHED >= v1.16.2 exists  → 'fixed in <that-version>'
        #   no PUBLISHED version >= v1.16.2        → 'fix pending next release' / 'open'
        earliest_fixed = _earliest_published_gte("v1.16.2", published)
        if earliest_fixed:
            assert f"fixed in {earliest_fixed}" in ki_line, (
                f"Earliest PUBLISHED version >= v1.16.2 is {earliest_fixed!r}; "
                f"expected 'fixed in {earliest_fixed}' in KI line: {ki_line!r}"
            )
        else:
            assert "fix pending next release" in ki_line or "open" in ki_line, (
                f"No PUBLISHED version >= v1.16.2; expected 'fix pending next release' "
                f"or 'open' in KI line: {ki_line!r}"
            )

    def test_bug_0023_file_carries_public_id_ki_1_0_0_1(self) -> None:
        """Criterion 5c: an earlier defect's file carries ## Public ID: KI-1.0.0.1.

        The bug ledger entry for the tmux 3.4 dashboard failure must have
        the public identifier KI-1.0.0.1 committed to its ## Public ID field.
        """
        self._ensure_data_available()

        bug_file_candidates = list(_REAL_BUGS_DIR.glob("BUG-0023-*.md"))
        assert len(bug_file_candidates) == 1, (
            f"Expected exactly one BUG-0023-*.md in {_REAL_BUGS_DIR}, "
            f"found {len(bug_file_candidates)}: {bug_file_candidates}"
        )

        rec = parse_bug_file(bug_file_candidates[0])

        assert rec.public_id == "KI-1.0.0.1", (
            f"BUG-0023 ## Public ID expected 'KI-1.0.0.1', got '{rec.public_id}'"
        )
        assert rec.affects == "v1.0.0", (
            f"BUG-0023 ## Affects expected 'v1.0.0', got '{rec.affects}'"
        )

    def test_no_internal_bug_identifiers_in_generated_changelog(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Criterion 5d / 8: grep -E 'BUG-[0-9]' returns zero hits on generated output.

        Internal BUG-NNNN identifiers must never appear in the generated
        CHANGELOG. This test is the real-data proof of the unit-level
        leak-gate in TestInternalIdLeakGate.
        """
        self._ensure_data_available()

        content, _ = self._regenerate_into_scratch(tmp_path)

        matches = re.findall(r"BUG-[0-9]", content)
        assert matches == [], (
            f"Internal BUG-NNNN identifiers found in real-data CHANGELOG output: {matches}"
        )

    def test_regeneration_is_idempotent_on_real_data(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Two consecutive regenerations against real data produce byte-identical output.

        Idempotence is a hard requirement because cm-release.sh regenerates
        CHANGELOG.md on every release. A nondeterministic writer would make
        every release diff noisy even when no bugs or release notes changed.
        """
        self._ensure_data_available()

        scratch1 = tmp_path / "run1"
        scratch1.mkdir()
        scratch2 = tmp_path / "run2"
        scratch2.mkdir()

        notes_dir = _REPO_ROOT / "release-notes"
        found = sorted(
            notes_dir.glob("v*.md"),
            key=lambda p: _parse_version(p.stem),
            reverse=True,
        )
        versions = [p.stem for p in found]
        published = load_published_manifest(_REPO_ROOT)

        content1 = regenerate(_REPO_ROOT, versions, published, _REAL_BUGS_DIR)
        content2 = regenerate(_REPO_ROOT, versions, published, _REAL_BUGS_DIR)

        assert content1 == content2, (
            "Two consecutive regenerations against real data produced different output. "
            "The writer is not idempotent — check for non-deterministic ID assignment "
            "or file-order sensitivity."
        )

    def test_real_ledger_ki_known_issues_contains_no_prose_affects_entries(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Criterion an earlier defect: the Known Issues section for v1.0.0 contains no
        entries that came from prose-affects internal bugs.

        The pre-fix changelog_writer silently stamped seven internal bugs
        (B43-B48, B51) with sequential KI-1.0.0.N identifiers because their
        prose ## Affects values ('(internal — plugin era; no KI)') parsed as
        version (0, 0, 0).  This test verifies the fix prevents recurrence:
        prose-affects bugs must not appear in the Known Issues section even if
        they have pre-existing KI- Public IDs (from before the fix).

        The check is targeted at the Known Issues section of the v1.0.0
        entry specifically, because that is the section that is synthesised
        from the disclosable-bugs projection.  Bugs appearing in ### Fixed
        sections may still carry KI- IDs (they are resolved bugs that happened
        to have been stamped before the fix and appeared in release notes).
        """
        self._ensure_data_available()

        content, _ = self._regenerate_into_scratch(tmp_path)

        # Extract the v1.0.0 entry's Known Issues section.
        entry_match = re.search(
            r"## v1\.0\.0.*?(?=\n## v|\Z)", content, re.DOTALL
        )
        assert entry_match is not None, "v1.0.0 entry not found in regenerated CHANGELOG"
        entry_text = entry_match.group(0)

        ki_section_match = re.search(
            r"### Known Issues\n(.*?)(?=###|\Z)", entry_text, re.DOTALL
        )
        assert ki_section_match is not None, "Known Issues section not found in v1.0.0 entry"
        ki_body = ki_section_match.group(1).strip()

        # Each KI line contains 'affects <version>' — prose entries would
        # show 'affects (internal — ...; no KI)' or similar non-version text.
        # A valid KI entry must have 'affects v<digits>'.
        ki_lines = [ln.strip() for ln in ki_body.splitlines() if ln.strip() and ln.strip() != "None"]
        for line in ki_lines:
            # Locate the 'affects <value>' clause in the KI line.
            affects_match = re.search(r"affects\s+(.+?)\s+·", line)
            if not affects_match:
                # No affects clause — this is unusual but not a prose-affects failure.
                continue
            affects_value = affects_match.group(1).strip()
            # The affects value must look like a version string, not prose.
            assert re.match(r"^v?\d+\.\d+", affects_value), (
                f"Known Issues section contains a KI line with a non-version "
                f"affects value {affects_value!r} — prose-affects bug minted into KI section:\n"
                f"  {line!r}\n"
                "Check for prose-affects internal bugs (B41-B51) that should "
                "not appear in Known Issues."
            )
