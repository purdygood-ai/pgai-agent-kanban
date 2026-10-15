"""
test_changelog_writer_real_data.py
===================================
Fixture-data acceptance test for the CHANGELOG writer (criterion 5 and
criterion 9 from the v1.17.0 release-summary-ledger-projection requirements).

This test runs the writer against the ACTUAL release-notes/ tree and a
COMMITTED bug-ledger fixture (team/tests/fixtures/bug_ledger/) — never the
operator's live kanban bugs directory — and asserts:
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

The fixture bug ledger is committed to the tree, so it is always present —
the suite's verdict no longer depends on what the operator's live kanban
bug ledger happens to contain on any given machine (reading the live
ledger and hardcoding the presence of a specific resolved-bug file used to
fail on any machine whose ledger had recycled that file after
resolution).  Each fixture record's disclosure sentence is
copied byte-for-byte from the published render at commit c2d8056 — see
``test_fixture_disclosure_text_matches_published_render`` below, which is
the in-test verification that the fixture never drifts from what was
actually published.

Design notes:
  - The repo root is captured at MODULE IMPORT TIME (module-level constant)
    to escape the autouse _block_live_kanban_writes fixture that redirects
    PGAI_AGENT_KANBAN_ROOT_PATH inside each test — the fixture bug ledger
    does not depend on that env var at all, but release-notes/ resolution
    does depend on the repo root being resolved before any redirect.
  - The test does NOT write to any live kanban file.  It regenerates into a
    tmp_path scratch area, leaving the live dev tree untouched.
  - The test is self-contained: it reads real files but produces no persistent
    side effects.  Running it twice produces the same outcome.
  - Test naming describes behavior under test (roles/TESTER.md Anti-pattern 6 compliance).
  - All temp paths use pytest's tmp_path (redirected by conftest to the framework
    temp root via PYTEST_DEBUG_TEMPROOT).
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess

import pytest

from pgai_agent_kanban.cm.changelog_writer import (
    load_published_manifest,
    parse_bug_file,
    regenerate,
    _earliest_published_gte,
    _parse_version,
)
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="changelog-writer-real-data")

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

# Committed bug-ledger fixture — never the operator's live kanban bugs
# directory. Holds one record per published KI-1.0.0.x
# identifier, recovered verbatim from the c2d8056 CHANGELOG.md render.
_FIXTURE_BUGS_DIR = _REPO_ROOT / "team" / "tests" / "fixtures" / "bug_ledger"

# The commit whose CHANGELOG.md render is the source of truth for each
# fixture record's disclosure text (the last commit that still carried the
# KI-1.0.0.x disclosures before OVERWATCH's regeneration at 92dae1b dropped
# them from the checked-in artifact, once the bug files those disclosures
# were sourced from were recycled after resolution).
_DISCLOSURE_SOURCE_COMMIT = "c2d8056"


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
    if not _FIXTURE_BUGS_DIR.exists():
        return False, (
            f"Committed bug-ledger fixture not found: {_FIXTURE_BUGS_DIR} "
            "— team/tests/fixtures/bug_ledger/ is missing from this checkout."
        )
    return True, ""


# ===========================================================================
# Real-data acceptance tests (criteria 5 and 9)
# ===========================================================================


class TestRealDataChangelog:
    """Fixture-data acceptance tests for the CHANGELOG writer.

    All assertions are derived from the requirements at:
      requirements/v1.17.0-release-summary-ledger-projection.md

    The test class skips as a whole when release-notes/PUBLISHED is absent
    (pre-publication state), rather than failing — that is a checkout state,
    not a code defect.  The bug-ledger fixture itself is committed to the
    tree, so it is always present.
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
        content = regenerate(
            _REPO_ROOT, versions, published, _FIXTURE_BUGS_DIR,
            published_changelog=None,  # the fixture ledger is rendered without the
            # live artifact: its published IDs belong to the live ledger, not the fixture
        )
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
            r"^## v1\.0\.0(?:\s|$).*?(?=\n## v|\Z)", content, re.DOTALL | re.MULTILINE
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

        The underlying defect (KI-1.0.0.1) was fixed internally in v1.16.2.
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
            r"^## v1\.0\.0(?:\s|$).*?(?=\n## v|\Z)", content, re.DOTALL | re.MULTILINE
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

        bug_file_candidates = list(_FIXTURE_BUGS_DIR.glob("BUG-0023-*.md"))
        assert len(bug_file_candidates) == 1, (
            f"Expected exactly one matching bug file in {_FIXTURE_BUGS_DIR}, "
            f"found {len(bug_file_candidates)}: {bug_file_candidates}"
        )

        rec = parse_bug_file(bug_file_candidates[0])

        assert rec.public_id == "KI-1.0.0.1", (
            f"## Public ID expected 'KI-1.0.0.1', got '{rec.public_id}'"
        )
        assert rec.affects == "v1.0.0", (
            f"## Affects expected 'v1.0.0', got '{rec.affects}'"
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

        content1 = regenerate(
            _REPO_ROOT, versions, published, _FIXTURE_BUGS_DIR,
            published_changelog=None,  # the fixture ledger is rendered without the
            # live artifact: its published IDs belong to the live ledger, not the fixture
        )
        content2 = regenerate(
            _REPO_ROOT, versions, published, _FIXTURE_BUGS_DIR,
            published_changelog=None,  # the fixture ledger is rendered without the
            # live artifact: its published IDs belong to the live ledger, not the fixture
        )

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

        A changelog_writer that parses a prose ## Affects value ('(internal —
        plugin era; no KI)') as version (0, 0, 0) silently stamps the seven
        internal bugs carrying one (B43-B48, B51) with sequential KI-1.0.0.N
        identifiers.  This test guards that shape:
        prose-affects bugs must not appear in the Known Issues section even if
        they have pre-existing KI- Public IDs.

        The check is targeted at the Known Issues section of the v1.0.0
        entry specifically, because that is the section that is synthesised
        from the disclosable-bugs projection.  Bugs appearing in ### Fixed
        sections may still carry KI- IDs (they are resolved bugs that happened
        to have been stamped while still open and appeared in release notes).
        """
        self._ensure_data_available()

        content, _ = self._regenerate_into_scratch(tmp_path)

        # Extract the v1.0.0 entry's Known Issues section.
        entry_match = re.search(
            r"^## v1\.0\.0(?:\s|$).*?(?=\n## v|\Z)", content, re.DOTALL | re.MULTILINE
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


# ===========================================================================
# Fixture-provenance guard: never author, paraphrase, or invent disclosure
# text for a fixture record
# ===========================================================================


class TestFixtureDisclosureProvenance:
    """Every fixture record's disclosure sentence traces verbatim to c2d8056.

    The recovery rule is explicit: the five bug files behind
    KI-1.0.0.1..5 must be recovered from the published CHANGELOG.md render
    at commit c2d8056, copied byte-for-byte — never authored or paraphrased.
    This test is the mechanical proof that each fixture record's parsed
    ## Symptom text (the sentence the writer renders into the KI line, before
    the writer's own affects/fixed-in suffix is appended) is byte-identical
    to the disclosure sentence in ``git show c2d8056:CHANGELOG.md``.

    A future edit that alters a fixture record's disclosure text without
    re-verifying it against c2d8056 fails this test loudly, by design.
    """

    def test_fixture_disclosure_text_matches_published_render(self) -> None:
        """Each fixture record's Symptom is byte-identical to its c2d8056 KI line.

        Recovers the five ``KI-1.0.0.x — <disclosure>; affects ...`` lines
        from ``git show c2d8056:CHANGELOG.md``, isolates the disclosure
        sentence (the text between the em-dash and the trailing
        ``; affects`` clause), and compares it against
        ``parse_bug_file(...).symptom`` for the fixture record carrying the
        matching ## Public ID.

        The trailing ``; affects vX · fixed in vY`` clause is deliberately
        excluded from the comparison: it is re-derived by the writer at
        regeneration time from the current release-notes/PUBLISHED
        manifest, not stored verbatim, so it is not part of what "the
        fixture must not invent" governs.
        """
        result = subprocess.run(
            ["git", "show", f"{_DISCLOSURE_SOURCE_COMMIT}:CHANGELOG.md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"git show {_DISCLOSURE_SOURCE_COMMIT}:CHANGELOG.md failed: "
            f"{result.stderr!r}"
        )

        published_symptoms: dict[str, set[str]] = {}
        for line in result.stdout.splitlines():
            if not line.startswith("KI-1.0.0."):
                continue
            public_id, _, rest = line.partition(" — ")
            idx = rest.rfind("; affects ")
            assert idx != -1, (
                f"Published KI line has no '; affects' clause to split on: {line!r}"
            )
            published_symptoms.setdefault(public_id, set()).add(rest[:idx])

        assert len(published_symptoms) == 5, (
            f"Expected 5 distinct KI-1.0.0.x identifiers in "
            f"{_DISCLOSURE_SOURCE_COMMIT}:CHANGELOG.md, found "
            f"{len(published_symptoms)}: {sorted(published_symptoms)}"
        )

        fixture_files = sorted(_FIXTURE_BUGS_DIR.glob("BUG-*.md"))
        assert len(fixture_files) == 5, (
            f"Expected 5 fixture records under {_FIXTURE_BUGS_DIR}, found "
            f"{len(fixture_files)}: {[f.name for f in fixture_files]}"
        )

        seen_public_ids: set[str] = set()
        for path in fixture_files:
            rec = parse_bug_file(path)
            assert rec.public_id, f"{path.name} has no ## Public ID set"
            seen_public_ids.add(rec.public_id)
            expected = published_symptoms.get(rec.public_id)
            assert expected is not None, (
                f"{path.name} carries Public ID {rec.public_id!r}, which does "
                f"not appear in {_DISCLOSURE_SOURCE_COMMIT}:CHANGELOG.md "
                f"(known published IDs: {sorted(published_symptoms)})"
            )
            assert rec.symptom in expected, (
                f"{path.name} ## Symptom does not byte-match the published "
                f"{_DISCLOSURE_SOURCE_COMMIT} disclosure for {rec.public_id}.\n"
                f"  fixture symptom: {rec.symptom!r}\n"
                f"  published symptom(s): {expected!r}\n"
                "Fixture disclosure text must be copied byte-for-byte from "
                "the published render, never authored or paraphrased."
            )

        assert seen_public_ids == set(published_symptoms), (
            f"Fixture Public IDs {sorted(seen_public_ids)} do not exactly "
            f"cover the published set {sorted(published_symptoms)} — one "
            "record per KI-1.0.0.x identifier is required."
        )


# ===========================================================================
# Writer-render disclosure guard
#
# TestFixtureDisclosureProvenance above proves the fixture RECORDS were
# copied verbatim from c2d8056. It compares each record's raw ## Symptom
# field directly against the published text — the writer's rendering
# pipeline is never invoked. This class closes the remaining gap: it proves
# changelog_writer's own RENDER of those records reproduces the same
# published text, so a future change to the rendering pipeline (a
# truncation, an escaping change, a reordering) that corrupts the disclosure
# on its way to output is caught even though the source record itself is
# untouched.
#
# Both sides of every comparison here come from inside the tree: the actual
# side is changelog_writer's own render of the committed fixture ledger
# (team/tests/fixtures/bug_ledger/); the expected side is the committed
# team/tests/fixtures/published_ki_1_0_0_disclosures.json data file — never
# the live-regenerated CHANGELOG.md, which cm-release Step 11b and the
# OVERWATCH drift check are free to regenerate from whatever the operator's
# real bug ledger currently holds.
# ===========================================================================

# team/tests/fixtures/published_ki_1_0_0_disclosures.json holds one entry
# per published KI-1.0.0.x identifier: the disclosure sentence recovered
# verbatim from `git show c2d8056:CHANGELOG.md` (the last commit whose
# committed CHANGELOG.md still carried these lines, before OVERWATCH's
# drift regeneration at 92dae1b dropped them). It is committed test data,
# not an inline paraphrase -- authoring or paraphrasing disclosure text is
# the failure mode this fixture design exists to prevent -- and reading it
# does not require git history to be reachable at test time.
_PUBLISHED_KI_DISCLOSURES_PATH = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "published_ki_1_0_0_disclosures.json"
)

# A rendered Known-Issues line, e.g.
# "KI-1.0.0.1 — `create.sh` fails ...; affects v1.0.0 · fixed in v1.23.0".
_RENDERED_KI_LINE_RE = re.compile(r"^(KI-1\.0\.0\.\d+) — (.*)$")


def _load_published_ki_disclosures() -> dict[str, str]:
    """Return {public_id: published disclosure sentence} from committed test data."""
    return json.loads(_PUBLISHED_KI_DISCLOSURES_PATH.read_text(encoding="utf-8"))


def _extract_ki_lines(content: str) -> dict[str, str]:
    """Return {public_id: full rendered line} for every KI-1.0.0.x line in content."""
    lines: dict[str, str] = {}
    for line in content.splitlines():
        match = _RENDERED_KI_LINE_RE.match(line)
        if match:
            lines[match.group(1)] = line
    return lines


def _rendered_disclosure_sentence(ki_line: str) -> str:
    """Return the disclosure-sentence portion of one rendered KI line.

    A rendered line has the shape ``KI-A.B.C.n — <sentence>; affects vX
    ...``. Splits on the last ``"; affects "`` occurrence (a disclosure
    sentence may itself contain semicolons, as KI-1.0.0.4's does), matching
    the same extraction rule ``TestFixtureDisclosureProvenance`` above uses
    against the raw published CHANGELOG line.
    """
    _, _, rest = ki_line.partition(" — ")
    idx = rest.rfind("; affects ")
    assert idx != -1, f"Rendered KI line has no '; affects' clause: {ki_line!r}"
    return rest[:idx]


def _assert_render_matches_published(content: str, published: dict[str, str]) -> None:
    """Assert every KI-1.0.0.x line the render emits matches its published text.

    changelog_writer normalizes a single trailing sentence-ending period off
    the symptom before appending the "; affects" clause (see the "Normalize
    a trailing sentence-ending period" comment in
    pgai_agent_kanban/cm/changelog_writer.py) — a documented transform
    applied uniformly to every rendered symptom, not a source-data defect.
    The published sentence (itself a complete sentence, e.g. "...one
    pane).") is normalized the identical way before comparison, so this
    assertion holds the writer to its own documented behaviour rather than
    demanding the transform never happened.

    Not every published identifier is expected to re-render: a bug whose
    ``## Fixed In`` equals its ``## Affects`` (a same-version fix, e.g.
    KI-1.0.0.5) is correctly excluded from the rendered Known
    Issues section by design — it never persisted as a disclosed issue in
    the version it affects. Absence alone is therefore not a divergence;
    only a rendered line whose text disagrees with the published record is.

    Args:
        content:   Full regenerated CHANGELOG content (or any substring
                   containing the KI-1.0.0.x lines) to check.
        published: {public_id: published disclosure sentence}, as loaded
                   from the committed test-data file.

    Raises:
        AssertionError naming the offending public ID, the rendered line,
        and both texts, when a rendered disclosure diverges from its
        published record.
    """
    rendered = _extract_ki_lines(content)
    for public_id, published_sentence in published.items():
        if public_id not in rendered:
            continue
        expected_sentence = published_sentence.rstrip()
        if expected_sentence.endswith("."):
            expected_sentence = expected_sentence[:-1]
        actual_sentence = _rendered_disclosure_sentence(rendered[public_id])
        assert actual_sentence == expected_sentence, (
            f"{public_id} rendered disclosure diverges from the published "
            f"c2d8056 text.\n"
            f"  rendered line: {rendered[public_id]!r}\n"
            f"  rendered sentence:  {actual_sentence!r}\n"
            f"  published sentence: {expected_sentence!r}\n"
            "changelog_writer's render of the fixture ledger no longer "
            "reproduces what was actually published under this identifier."
        )


def _regenerate_fixture_ledger_changelog(tmp_path: pathlib.Path) -> str:
    """Regenerate full CHANGELOG content from the committed fixture ledger.

    Mirrors ``TestRealDataChangelog._regenerate_into_scratch`` — never
    writes to the live repo root, always scratch under tmp_path.
    """
    notes_dir = _REPO_ROOT / "release-notes"
    found = sorted(
        notes_dir.glob("v*.md"), key=lambda p: _parse_version(p.stem), reverse=True
    )
    versions = [p.stem for p in found]
    published = load_published_manifest(_REPO_ROOT)
    content = regenerate(
        _REPO_ROOT, versions, published, _FIXTURE_BUGS_DIR,
        published_changelog=None,  # the fixture ledger is rendered without the
        # live artifact: its published IDs belong to the live ledger, not the fixture
    )
    (tmp_path / "CHANGELOG.md").write_text(content, encoding="utf-8")
    return content


class TestWriterRenderReproducesPublishedDisclosures:
    """Guard: the WRITER'S RENDER of the fixture ledger byte-matches c2d8056.

    Complements ``TestFixtureDisclosureProvenance`` (which proves the
    fixture *records* are verbatim) by proving the *rendering pipeline*
    reproduces the same text, implemented against the c2d8056 published
    text held as committed test data. Comparing against the
    live-regenerated CHANGELOG.md instead would recouple this suite's
    verdict to whatever the operator's live bug ledger currently holds --
    the same live-environment coupling the committed-fixture design
    throughout this module exists to avoid.
    """

    def _ensure_data_available(self) -> None:
        ok, reason = _real_data_available()
        if not ok:
            pytest.skip(reason)

    def test_render_reproduces_published_ki_1_0_0_disclosures(
        self, tmp_path: pathlib.Path
    ) -> None:
        """changelog_writer's render of the fixture ledger byte-matches c2d8056.

        Regenerates CHANGELOG content from the committed fixture ledger into
        a tmp_path scratch file and asserts every KI-1.0.0.x line the render
        actually emits carries the disclosure sentence published under that
        identifier at c2d8056 — the committed
        team/tests/fixtures/published_ki_1_0_0_disclosures.json data, never
        the live-regenerated CHANGELOG.md.
        """
        self._ensure_data_available()
        published = _load_published_ki_disclosures()
        content = _regenerate_fixture_ledger_changelog(tmp_path)

        rendered = _extract_ki_lines(content)
        assert rendered, (
            "changelog_writer's render of the fixture ledger produced no "
            "KI-1.0.0.x lines at all — regeneration is broken or the "
            "fixture ledger is empty."
        )
        assert set(rendered).issubset(set(published)), (
            "Render produced KI identifiers absent from the published "
            f"record: {sorted(set(rendered) - set(published))}"
        )

        _assert_render_matches_published(content, published)

    def test_guard_fails_and_names_the_mutated_line_when_a_disclosure_diverges(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Mutating one KI line in a scratch fixture render fails the guard, naming it.

        Proves the guard actually fires (a gate that cannot fail is not a
        gate). Takes a real fixture-ledger render, mutates the text of
        exactly one KI-1.0.0.x line in a scratch copy of the rendered
        content, and asserts ``_assert_render_matches_published`` raises —
        with the mutated public ID named in the failure message.
        """
        self._ensure_data_available()
        published = _load_published_ki_disclosures()
        content = _regenerate_fixture_ledger_changelog(tmp_path)

        rendered = _extract_ki_lines(content)
        assert rendered, "No KI-1.0.0.x lines rendered — cannot exercise the mutation."
        target_id = sorted(rendered)[0]
        original_line = rendered[target_id]
        # Mutate inside the disclosure SENTENCE (before "; affects"), not the
        # trailing affects/fixed-in clause — that clause is re-derived from
        # the PUBLISHED manifest at regeneration time and is deliberately
        # excluded from the comparison, so a mutation after it would not
        # exercise the guard at all.
        assert "; affects " in original_line, (
            f"Rendered line has no '; affects' clause to mutate around: "
            f"{original_line!r}"
        )
        mutated_line = original_line.replace(
            "; affects ", " MUTATED-FOR-TEST; affects ", 1
        )

        # Rebuild content line-by-line rather than str.replace() on the whole
        # document: the SAME disclosure sentence also appears verbatim as a
        # "- KI-1.0.0.n — ..." bullet under the release that fixed it (the
        # "### Fixed" carried-forward mention), so a plain substring replace
        # could silently mutate that unrelated bullet instead of the actual
        # Known-Issues line this test targets. Matching the exact KI-line
        # regex per source line guarantees the right line is mutated.
        content_lines = content.splitlines(keepends=True)
        mutated_count = 0
        for i, raw_line in enumerate(content_lines):
            if raw_line.rstrip("\n") == original_line:
                content_lines[i] = raw_line.replace(original_line, mutated_line, 1)
                mutated_count += 1
        assert mutated_count == 1, (
            f"Expected exactly one exact-match line for {target_id!r} to "
            f"mutate, found {mutated_count}."
        )
        mutated_content = "".join(content_lines)
        assert mutated_content != content, "Mutation did not change the rendered content."

        with pytest.raises(AssertionError) as exc_info:
            _assert_render_matches_published(mutated_content, published)

        assert target_id in str(exc_info.value), (
            f"Guard failure message does not name the mutated identifier "
            f"{target_id!r}.\nMessage: {exc_info.value}"
        )
