"""
test_release_notes_content_described.py
=========================================
Regression gate for the content-described-notes policy: the release-notes
generator (``get_item_title()`` in
``pgai_agent_kanban.cm.release_notes_generator``, the generator
``team/scripts/lib/cm/release_notes.sh`` runs at Step 11a) must
render each bundled BUG-NNNN or PRIORITY-NNNN item by SUBSTANCE — sourced
from the item's own Symptom (bugs) or Title/Description (priorities) text —
and must never emit the bare internal ledger ID as the rendered title. A
bug's Fix section is a proposed remedy written before the work, so it is
never a release-notes source.

Fidelity approach: identical to test_release_notes_bundled_item_title.py —
the shipped generator module is imported and its CLI entry point,
``main()``, is called with the argv cm_release_generate_notes passes, so a
regression in the shipped generator fails these tests directly. There is no
parallel implementation to drift out of sync.

Covers:
  1. A bug item with both ``## Symptom`` and ``## Fix`` renders a
     "Fixed: <symptom>" bullet built from its Symptom; no text unique to the
     Fix section appears anywhere in the notes, and neither does its own
     BUG-NNNN ID.
  2. A bug item with only a ``## Symptom`` section (no Fix) renders the same
     "Fixed: <symptom>" bullet, still with zero occurrences of its own ID.
  3. A bug item with no Symptom text falls back to a generic, ID-free
     sentence rather than the file stem (which would itself be the bare
     ledger ID for a bug file) -- including a Fix-only bug, whose Fix text
     never reaches the notes.
  4. A priority item with a ``## Title`` section renders that title as the
     bullet, with zero occurrences of its own PRIORITY-NNNN ID.
  5. A priority item with only ``## Description`` (no Title) falls back to
     the Description text.
  6. Defense-in-depth: a bug's own Symptom prose incidentally mentioning a
     DIFFERENT internal bug or task ID inline is scrubbed from the rendered
     bullet — the generator does not merely avoid quoting its own item's ID,
     it never lets any internal ledger ID through.
  7. A public KI-N.N.N.N disclosure ID mentioned in Symptom prose passes
     through UNCHANGED — the scrub in (6) is scoped to internal
     BUG-/PRIORITY-/task IDs only, never the public disclosure namespace.
  8. The generated release-notes file, taken as a whole, is clean under the
     real ``lint_hygiene.py`` provenance class with no carve-out — proving
     the generator complies "by construction" rather than merely by this
     test's own narrower assertions.
  9. A source line that already carries a list marker renders as exactly
     one ``- `` bullet, on the bundled-item path and the commit-subject
     fallback path alike, and a bug's Files Involved suggestions are never
     used as its substance.
 10. A requirements-document path in a bug's prose never reaches the notes.
 11. Sentence extraction does not end a sentence at a common abbreviation
     ('e.g.', 'i.e.', 'etc.') or at a '.' inside a backtick code span, so
     a Symptom whose lead sentence carries '(e.g. ...)' renders one bullet
     with balanced parentheses and balanced backticks.
 12. Symptom sections shaped like real filed reports -- a lead sentence
     ending in ':' that introduces a fenced log excerpt or a bulleted list
     of failing tests -- render one single-line bullet each: no
     triple-backtick, no text from inside the fence or the list, no
     trailing ':', and zero internal IDs.

All temp paths use pytest's tmp_path fixture (redirected to the framework
temp root by conftest.py) or the framework temp root directly for the
lint-scan case, which must live outside any 'tests/'-segment path (see
lint_hygiene.py's own module docstring and its _CODE_EXCLUDE_SEGMENTS for
why). No bare /tmp paths. No live kanban root is touched.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

from pgai_agent_kanban.cm.release_notes_generator import main as release_notes_main

# A hyphen preceded by whitespace or the start of the string and followed
# by an alphanumeric character is the exact shape a stranded task-ID slug
# fragment takes (e.g. "-verify-2-14-1") when the scrub's closing boundary
# stops at the identifier's numeric head instead of consuming the whole
# kebab-case slug. A clean scrub leaves none of these behind.
_STRANDED_HYPHEN_FRAGMENT_RE = re.compile(r"(?:^|\s)-[A-Za-z0-9]")

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_THIS_FILE = pathlib.Path(__file__).resolve()
# team/tests/unit/test_release_notes_content_described.py
# three levels up: unit/ -> tests/ -> team/
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_hygiene.py"

_SCRATCH_SUBDIR = "release_notes_content_described_scratch"


def _generate_notes(
    tmp_path: pathlib.Path,
    *,
    bundle_file: str,
    active_rc: str = "v9.9.9",
) -> str:
    """Run the shipped generator's CLI entry point and return the generated
    release notes file's text (same argv contract cm_release_generate_notes
    passes)."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)

    argv = [
        bundle_file,
        str(repo_root),
        active_rc,
        "FUNCTIONAL",
        "2026-08-08",
        "test-project",
        "none",
        "rc/" + active_rc,
        "unknown-tester-task",
    ]
    rc = release_notes_main(argv)
    assert rc == 0, f"release-notes generator returned {rc}"

    out_file = repo_root / "release-notes" / f"{active_rc}.md"
    assert out_file.exists(), f"Expected release notes file not written: {out_file}"
    return out_file.read_text(encoding="utf-8")


def _make_bundle(tmp_path: pathlib.Path, item_paths: list[pathlib.Path]) -> pathlib.Path:
    """Write a minimal bundle file with a '## Bundled Items' section."""
    bundle_path = tmp_path / "bundle.md"
    lines = ["# Bundle", "", "## Bundled Items"]
    for item_path in item_paths:
        lines.append(f"- fixture item (`{item_path}`)")
    bundle_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return bundle_path


# ---------------------------------------------------------------------------
# Bug substance extraction
# ---------------------------------------------------------------------------


class TestBugSubstanceExtraction:
    def test_symptom_renders_as_bullet_and_fix_text_never_appears(
        self, tmp_path: pathlib.Path
    ) -> None:
        bug = tmp_path / "BUG-0042-race-condition-retry-write.md"
        bug.write_text(
            "# BUG-0042-race-condition-retry-write\n\n"
            "## Status\nopen\n\n"
            "## Symptom\n"
            "Two agents wrote the retry record file at the same time and "
            "corrupted it.\n\n"
            "## Fix\n"
            "Serialize writes to the retry record with a file lock so only "
            "one agent writes at a time.\n",
            encoding="utf-8",
        )
        bundle = _make_bundle(tmp_path, [bug])

        notes = _generate_notes(tmp_path, bundle_file=str(bundle))

        assert _bullet_lines(notes, "Bug Fixes") == [
            "- Fixed: Two agents wrote the retry record file at the same "
            "time and corrupted it."
        ]
        assert "Serialize writes" not in notes
        assert "file lock" not in notes
        assert "BUG-0042" not in notes

    def test_fix_only_bug_renders_generic_fallback_not_its_fix_text(
        self, tmp_path: pathlib.Path
    ) -> None:
        bug = tmp_path / "BUG-0045-fix-only-report.md"
        bug.write_text(
            "# BUG-0045-fix-only-report\n\n"
            "## Status\nopen\n\n"
            "## Fix\n"
            "For the implementer to choose; options, not a prescription:\n"
            "- (a) Hand the lock to the archive step.\n"
            "- (b) Schedule the archive outside the wake chain.\n",
            encoding="utf-8",
        )
        bundle = _make_bundle(tmp_path, [bug])

        notes = _generate_notes(tmp_path, bundle_file=str(bundle))

        assert _bullet_lines(notes, "Bug Fixes") == [
            "- Fixed an internal defect (no Symptom/Fix text recorded in the "
            "bug report)."
        ]
        for fix_fragment in ("implementer to choose", "options", "archive step", "wake chain"):
            assert fix_fragment not in notes
        assert "BUG-0045" not in notes

    def test_symptom_only_falls_back_to_fixed_prefix_with_no_bare_id(
        self, tmp_path: pathlib.Path
    ) -> None:
        bug = tmp_path / "BUG-0043-duplicate-pm-tickets.md"
        bug.write_text(
            "# BUG-0043-duplicate-pm-tickets\n\n"
            "## Status\nopen\n\n"
            "## Symptom\n"
            "PM decomposed the same requirements document twice, producing "
            "duplicate tickets.\n",
            encoding="utf-8",
        )
        bundle = _make_bundle(tmp_path, [bug])

        notes = _generate_notes(tmp_path, bundle_file=str(bundle))

        assert (
            "- Fixed: PM decomposed the same requirements document twice, "
            "producing duplicate tickets." in notes
        )
        assert "BUG-0043" not in notes

    def test_no_symptom_or_fix_falls_back_to_generic_id_free_sentence(
        self, tmp_path: pathlib.Path
    ) -> None:
        bug = tmp_path / "BUG-0044-empty-report.md"
        bug.write_text(
            "# BUG-0044-empty-report\n\n## Status\nopen\n",
            encoding="utf-8",
        )
        bundle = _make_bundle(tmp_path, [bug])

        notes = _generate_notes(tmp_path, bundle_file=str(bundle))

        assert "BUG-0044" not in notes
        assert "- BUG-0044-empty-report" not in notes
        assert "- Fixed an internal defect" in notes


# ---------------------------------------------------------------------------
# Priority substance extraction
# ---------------------------------------------------------------------------


class TestPrioritySubstanceExtraction:
    def test_title_section_renders_as_bullet_with_no_bare_id(
        self, tmp_path: pathlib.Path
    ) -> None:
        priority = tmp_path / "PRIORITY-0007-add-dashboard-filter.md"
        priority.write_text(
            "# PRIORITY-0007-add-dashboard-filter\n\n"
            "## Status\nopen\n\n"
            "## Title\n"
            "Add a status filter to the operator dashboard.\n\n"
            "## Description\n"
            "Operators currently must scroll to find tasks in a given state.\n",
            encoding="utf-8",
        )
        bundle = _make_bundle(tmp_path, [priority])

        notes = _generate_notes(tmp_path, bundle_file=str(bundle))

        assert "- Add a status filter to the operator dashboard." in notes
        assert "PRIORITY-0007" not in notes

    def test_description_only_falls_back_when_title_absent(
        self, tmp_path: pathlib.Path
    ) -> None:
        priority = tmp_path / "PRIORITY-0008-improve-error-copy.md"
        priority.write_text(
            "# PRIORITY-0008-improve-error-copy\n\n"
            "## Status\nopen\n\n"
            "## Description\n"
            "Rewrite the gate refusal message so a stranger understands it.\n",
            encoding="utf-8",
        )
        bundle = _make_bundle(tmp_path, [priority])

        notes = _generate_notes(tmp_path, bundle_file=str(bundle))

        assert (
            "- Rewrite the gate refusal message so a stranger understands it."
            in notes
        )
        assert "PRIORITY-0008" not in notes


# ---------------------------------------------------------------------------
# Defense-in-depth: inline mentions of OTHER internal IDs get scrubbed;
# public KI IDs pass through unchanged.
# ---------------------------------------------------------------------------


class TestInternalIdScrubbingAndPublicIdPassthrough:
    def test_inline_mention_of_a_different_bug_id_is_scrubbed(
        self, tmp_path: pathlib.Path
    ) -> None:
        bug = tmp_path / "BUG-0050-regression-of-earlier-fix.md"
        bug.write_text(
            "# BUG-0050-regression-of-earlier-fix\n\n"
            "## Status\nopen\n\n"
            "## Symptom\n"
            "The queue guard went missing during the BUG-0011 cleanup.\n",
            encoding="utf-8",
        )
        bundle = _make_bundle(tmp_path, [bug])

        notes = _generate_notes(tmp_path, bundle_file=str(bundle))

        assert "BUG-0050" not in notes
        assert "BUG-0011" not in notes
        # The removed identifier is replaced with the scrub's neutral
        # stand-in text ("another item") rather than deleted -- here the
        # identifier sat in an adjectival position, directly modifying the
        # following noun, so the substitution reads a little less smoothly
        # than the identifier-as-noun-phrase cases elsewhere in this suite,
        # but it still visibly stands in for the removed token instead of
        # trailing off mid-clause.
        assert (
            "- Fixed: The queue guard went missing during the another item cleanup."
            in notes
        )

    def test_inline_public_ki_id_passes_through_unchanged(
        self, tmp_path: pathlib.Path
    ) -> None:
        bug = tmp_path / "BUG-0051-followup-to-disclosed-issue.md"
        bug.write_text(
            "# BUG-0051-followup-to-disclosed-issue\n\n"
            "## Status\nopen\n\n"
            "## Symptom\n"
            "An edge case from public disclosure KI-1.2.3.4 still reproduced.\n",
            encoding="utf-8",
        )
        bundle = _make_bundle(tmp_path, [bug])

        notes = _generate_notes(tmp_path, bundle_file=str(bundle))

        assert "BUG-0051" not in notes
        assert (
            "- Fixed: An edge case from public disclosure KI-1.2.3.4 still reproduced."
            in notes
        )


# ---------------------------------------------------------------------------
# The task-ID alternative's closing boundary previously stopped at the
# identifier's numeric head, leaving a real task ID's kebab-case slug
# stranded in the rendered sentence (e.g. "-verify-2-14-1"). release_notes.sh
# and release_known_issue.sh both import the fix from the single shared
# definition in internal_id_scrub.py (see that module's own
# _INTERNAL_ID_RE comment) rather than each carrying its own copy;
# release_known_issue.sh's own call sites are covered separately by
# test_cm_known_issue_carry_forward_bash.py. This class proves the scrub
# reaches this file's caller too, for all six kanban agent prefixes.
# ---------------------------------------------------------------------------


class TestTaskIdSlugScrubbing:
    @pytest.mark.parametrize(
        "prefix", ["PM", "CODER", "WRITER", "CM", "TESTER", "PO"]
    )
    def test_task_id_and_its_kebab_slug_are_both_scrubbed(
        self, tmp_path: pathlib.Path, prefix: str
    ) -> None:
        task_id = f"{prefix}-20260909-045-verify-2-14-1"
        bug = tmp_path / "BUG-0070-task-id-in-symptom-text.md"
        bug.write_text(
            "# BUG-0070-task-id-in-symptom-text\n\n"
            "## Status\nopen\n\n"
            "## Symptom\n"
            f"The guard went missing after the earlier attempt (Source Task: {task_id}).\n",
            encoding="utf-8",
        )
        bundle = _make_bundle(tmp_path, [bug])

        notes = _generate_notes(tmp_path, bundle_file=str(bundle))

        assert "BUG-0070" not in notes
        assert task_id not in notes
        assert prefix + "-20260909" not in notes
        # Positive assertion on the surviving text: no hyphen-leading
        # fragment of the identifier's slug survives, and the removed
        # identifier is replaced with the scrub's neutral stand-in text
        # ("another item"), so the sentence around it still reads as
        # ordinary English instead of trailing off after "Source Task: ".
        assert not _STRANDED_HYPHEN_FRAGMENT_RE.search(notes), notes
        assert "-verify-2-14-1" not in notes
        assert (
            "- Fixed: The guard went missing after the earlier attempt "
            "(Source Task: another item)."
        ) in notes


# ---------------------------------------------------------------------------
# End-to-end: the generated file passes the real provenance lint, no
# carve-out, proving by-construction compliance rather than a narrower
# hand-picked assertion set above.
# ---------------------------------------------------------------------------


def _pgai_temp_root() -> pathlib.Path:
    """Return $PGAI_AGENT_KANBAN_TEMP_DIR/<_SCRATCH_SUBDIR>, creating it if needed.

    Falls back to the documented resolver default only when the env var is
    unset, matching the fallback convention used by temp.sh's
    pgai_temp_dir() (see the literal itself, below, for the exact value).
    """
    # anti-pattern-allowlist: 2 (justification: mirrors temp.sh's own documented
    # hard-coded fallback default; this test intentionally exercises that exact
    # resolver contract rather than routing through it)
    root = pathlib.Path(
        os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR") or "/tmp/pgai_kanban_tmp"
    )
    scratch_root = root / _SCRATCH_SUBDIR
    scratch_root.mkdir(parents=True, exist_ok=True)
    return scratch_root


@pytest.fixture
def lint_scan_dir():
    """A self-cleaning directory outside any 'tests/'-segment path, since
    lint_hygiene.py's directory-collection path-exclusion rules
    unconditionally skip any path containing a 'tests/' segment (see
    lint_hygiene.py's own _CODE_EXCLUDE_SEGMENTS for the same concern)."""
    d = pathlib.Path(tempfile.mkdtemp(dir=str(_pgai_temp_root())))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _import_lint_module():
    spec = importlib.util.spec_from_file_location(
        "lint_hygiene", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules before exec: the module uses @dataclass, and
    # dataclasses.dataclass() resolves its own module via sys.modules while
    # processing fields -- an unregistered module makes that lookup return
    # None and crash instead of importing.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_generated_notes_pass_provenance_lint_with_no_carve_out(
    tmp_path: pathlib.Path, lint_scan_dir: pathlib.Path
) -> None:
    bug = tmp_path / "BUG-0060-two-bundled-defects.md"
    bug.write_text(
        "# BUG-0060-two-bundled-defects\n\n"
        "## Status\nopen\n\n"
        "## Symptom\n"
        "The dashboard column count drifted from the documented layout.\n\n"
        "## Fix\n"
        "Reconciled the dashboard column count with the documented layout.\n",
        encoding="utf-8",
    )
    priority = tmp_path / "PRIORITY-0009-second-bundled-item.md"
    priority.write_text(
        "# PRIORITY-0009-second-bundled-item\n\n"
        "## Status\nopen\n\n"
        "## Title\n"
        "Add a machine-readable findings file for lint output.\n",
        encoding="utf-8",
    )
    bundle = _make_bundle(tmp_path, [bug, priority])

    notes = _generate_notes(tmp_path, bundle_file=str(bundle))

    scanned_file = lint_scan_dir / "v9.9.9.md"
    scanned_file.write_text(notes, encoding="utf-8")

    lint = _import_lint_module()
    findings = lint._scan_file("provenance", scanned_file)
    assert findings == [], (
        f"lint_hygiene's provenance class found findings in generated release notes: {findings}"
    )


# ---------------------------------------------------------------------------
# One bullet per entry, and substance that describes what landed
# ---------------------------------------------------------------------------


def _bullet_lines(notes: str, heading: str) -> list[str]:
    section = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", notes, re.S | re.M)
    assert section, f"## {heading} not found in:\n{notes}"
    return [line for line in section.group(1).splitlines() if line.strip()]


def test_symptom_text_already_bulleted_renders_as_one_bullet(tmp_path: pathlib.Path) -> None:
    bug = tmp_path / "BUG-0070-split-engine-file.md"
    bug.write_text(
        "# BUG-0070-split-engine-file\n\n## Category\nmisc\n\n"
        "## Symptom\n- `team/scripts/lib/example.sh` kept its report parser inline past the size ceiling.\n",
        encoding="utf-8",
    )
    notes = _generate_notes(tmp_path, bundle_file=str(_make_bundle(tmp_path, [bug])))

    lines = _bullet_lines(notes, "Bug Fixes")
    assert lines == [
        "- Fixed: `team/scripts/lib/example.sh` kept its report parser inline past the size ceiling."
    ]
    assert not any(line.startswith("- - ") for line in notes.splitlines())


def test_files_involved_suggestions_are_not_rendered_as_the_change(tmp_path: pathlib.Path) -> None:
    bug = tmp_path / "BUG-0071-engine-file-too-long.md"
    bug.write_text(
        "# BUG-0071-engine-file-too-long\n\n## Category\nmisc\n\n"
        "## Symptom\nThe ship-policy stage exceeded the engine-size ceiling.\n\n"
        "## Files Involved\n- `team/scripts/lib/example.sh` -- split along its own seams; could move.\n",
        encoding="utf-8",
    )
    notes = _generate_notes(tmp_path, bundle_file=str(_make_bundle(tmp_path, [bug])))

    assert _bullet_lines(notes, "Bug Fixes") == [
        "- Fixed: The ship-policy stage exceeded the engine-size ceiling."
    ]
    assert "split along its own seams" not in notes


def test_commit_subject_fallback_renders_one_bullet_per_subject(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(git + ["init", "-q", "-b", "main"], check=True)
    subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "base"], check=True)
    base = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    for subject in ("- Tidy the queue parser", "Plain subject line"):
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", subject], check=True)

    rc = release_notes_main(
        ["none", str(repo), "v9.9.9", "FUNCTIONAL",
         "2026-08-08", "test-project", base, "main", "", ""]
    )
    assert rc == 0, f"release-notes generator returned {rc}"
    notes = (repo / "release-notes" / "v9.9.9.md").read_text(encoding="utf-8")

    assert sorted(_bullet_lines(notes, "Other Changes")) == ["- Plain subject line", "- Tidy the queue parser"]


def test_requirements_path_in_bug_prose_never_reaches_notes(tmp_path: pathlib.Path) -> None:
    bug = tmp_path / "BUG-0072-metrics-table.md"
    bug.write_text(
        "# BUG-0072-metrics-table\n\n## Category\nbugfix\n\n"
        "## Symptom\nThe agent-metrics table reported a value other than the one "
        "`requirements/v2.26.0-agent-metrics-v2.md` names.\n",
        encoding="utf-8",
    )
    notes = _generate_notes(tmp_path, bundle_file=str(_make_bundle(tmp_path, [bug])))

    assert "requirements/" not in notes
    assert "- Fixed: The agent-metrics table reported a value other than the one" in notes


# ---------------------------------------------------------------------------
# Sentence extraction: abbreviations and code spans never end a sentence
# ---------------------------------------------------------------------------


def _assert_balanced(bullet: str) -> None:
    assert bullet.count("(") == bullet.count(")"), bullet
    assert bullet.count("[") == bullet.count("]"), bullet
    assert bullet.count("`") % 2 == 0, bullet


class TestLeadSentenceExtraction:
    @pytest.mark.parametrize("abbreviation", ["e.g.", "i.e.", "etc."])
    def test_abbreviation_inside_parenthetical_does_not_end_the_sentence(
        self, tmp_path: pathlib.Path, abbreviation: str
    ) -> None:
        bug = tmp_path / "BUG-0080-comment-names-module.md"
        bug.write_text(
            "# BUG-0080-comment-names-module\n\n## Status\nopen\n\n"
            "## Symptom\n"
            "Two code comments name the archive module in the form the sweep "
            f"rejects ({abbreviation} `python3 -m pkg.archive`), so the sweep "
            "test fails. A second sentence never reaches the notes.\n\n"
            "## Fix\nReword both comments.\n",
            encoding="utf-8",
        )
        notes = _generate_notes(tmp_path, bundle_file=str(_make_bundle(tmp_path, [bug])))

        bullets = _bullet_lines(notes, "Bug Fixes")
        assert bullets == [
            "- Fixed: Two code comments name the archive module in the form the "
            f"sweep rejects ({abbreviation} `python3 -m pkg.archive`), so the "
            "sweep test fails."
        ]
        _assert_balanced(bullets[0])
        assert "second sentence" not in notes
        assert "Reword" not in notes

    def test_period_inside_a_code_span_does_not_end_the_sentence(
        self, tmp_path: pathlib.Path
    ) -> None:
        bug = tmp_path / "BUG-0081-dotted-code-span.md"
        bug.write_text(
            "# BUG-0081-dotted-code-span\n\n## Status\nopen\n\n"
            "## Symptom\n"
            "The parser split `Stop. Then resume` into two commands and "
            "dropped the second.\n",
            encoding="utf-8",
        )
        notes = _generate_notes(tmp_path, bundle_file=str(_make_bundle(tmp_path, [bug])))

        bullets = _bullet_lines(notes, "Bug Fixes")
        assert bullets == [
            "- Fixed: The parser split `Stop. Then resume` into two commands "
            "and dropped the second."
        ]
        _assert_balanced(bullets[0])


# ---------------------------------------------------------------------------
# Symptom shapes taken from real filed reports: a lead sentence ending in ':'
# that introduces a fenced excerpt or a bulleted list.
# ---------------------------------------------------------------------------

# Each case: (file stem, Symptom body, expected bullet, text that must never
# reach the notes). The stems and the IDs inside the bodies are fixture
# values shaped like ledger IDs so the scrub is exercised.
_SHAPED_SYMPTOMS = [
    (
        "BUG-0901-archive-skipped-in-wake",
        "In an autonomous fleet, the release's archive step (\"Archive this "
        "project's finished items\") always reports a skip and moves nothing:\n\n"
        "```\n"
        "[archive] error:   archive.repo_lock_busy\n"
        "WARNING: archive skipped for demo; nothing moved; release continues.\n"
        "```\n\n"
        "The archive tries to take a lock its own parent chain already holds.\n",
        "- Fixed: In an autonomous fleet, the release's archive step (\"Archive "
        "this project's finished items\") always reports a skip and moves nothing.",
        ["repo_lock_busy", "nothing moved", "parent chain"],
    ),
    (
        "BUG-0902-api-artifact-stale",
        "`team/scripts/run-unit-tests.sh` fails three tests, and both gated "
        "runners record the `icd_freshness` quality lint finding:\n"
        "- `tests/unit/test_review_cmds.py::test_freshness_gate_exits_0`\n"
        "- `tests/unit/test_lint_icd_freshness.py::test_fresh_artifact_passes`\n\n"
        "`lint_icd_freshness: FAIL — docs/api/icd.json is stale.`\n",
        "- Fixed: `team/scripts/run-unit-tests.sh` fails three tests, and both "
        "gated runners record the `icd_freshness` quality lint finding.",
        ["test_freshness_gate_exits_0", "test_fresh_artifact_passes", "is stale"],
    ),
    (
        "BUG-0903-sweep-flags-comment-lines",
        "`tests/unit/test_sweep.py::test_no_bare_module_invocations` fails "
        "after the change in BUG-0899:\n\n"
        "```text\n"
        "Sweep invariant violated — 2 bare invocation(s) found (see BUG-0898):\n"
        "    team/scripts/lib/example_gate.sh:46:# `python3 -m pkg.archive`\n"
        "```\n",
        "- Fixed: `tests/unit/test_sweep.py::test_no_bare_module_invocations` "
        "fails after the change in another item.",
        ["Sweep invariant violated", "example_gate.sh", "BUG-0898"],
    ),
    (
        "BUG-0904-gate-resources-helper",
        "Two unit tests fail on the release branch and pass on the main "
        "branch (raised by CODER-20261001-012-gate-helper):\n"
        "* `test_transition_goes_through_the_writer` (`assert 'BLOCKED' == 'DONE'`)\n"
        "* `test_failed_writes_leave_the_state_alone`\n",
        "- Fixed: Two unit tests fail on the release branch and pass on the "
        "main branch (raised by another item).",
        ["test_transition_goes_through_the_writer", "'BLOCKED' == 'DONE'"],
    ),
    (
        "BUG-0905-notes-quote-proposed-fix",
        "```\n"
        "## Bug Fixes\n"
        "- Reword both comments so they name the module without the forbidden form (e.g.\n"
        "```\n\n"
        "The generated notes quote each report's proposed remedy (e.g. a menu "
        "of options) instead of the defect it fixed.\n",
        "- Fixed: The generated notes quote each report's proposed remedy "
        "(e.g. a menu of options) instead of the defect it fixed.",
        ["Reword both comments", "forbidden form"],
    ),
]


class TestShapedSymptomSections:
    @pytest.fixture
    def shaped_notes(self, tmp_path: pathlib.Path) -> str:
        bugs_dir = tmp_path / "bugs"
        bugs_dir.mkdir()
        paths = []
        for stem, symptom, _, _ in _SHAPED_SYMPTOMS:
            path = bugs_dir / f"{stem}.md"
            path.write_text(
                f"# {stem}\n\n## Status\nrunning\n\n## Category\nrelease\n\n"
                f"## Symptom\n{symptom}\n"
                "## Fix\nFor the implementer to choose; options, not a "
                "prescription: (a) proposed remedy text.\n",
                encoding="utf-8",
            )
            paths.append(path)
        return _generate_notes(tmp_path, bundle_file=str(_make_bundle(tmp_path, paths)))

    def test_each_shape_renders_its_lead_sentence_as_one_bullet(self, shaped_notes: str) -> None:
        assert _bullet_lines(shaped_notes, "Bug Fixes") == [
            expected for _, _, expected, _ in _SHAPED_SYMPTOMS
        ]

    def test_bullets_carry_no_fence_list_or_fix_text(self, shaped_notes: str) -> None:
        bullets = _bullet_lines(shaped_notes, "Bug Fixes")
        for bullet in bullets:
            assert "```" not in bullet
            assert not bullet.rstrip().endswith(":"), bullet
            _assert_balanced(bullet)
        for _, _, _, excluded in _SHAPED_SYMPTOMS:
            for fragment in excluded:
                assert fragment not in shaped_notes
        assert "proposed remedy text" not in shaped_notes
        assert "implementer to choose" not in shaped_notes

    def test_bullets_carry_zero_internal_ids(self, shaped_notes: str) -> None:
        bullets = "\n".join(_bullet_lines(shaped_notes, "Bug Fixes"))
        assert not re.search(
            r"\b(?:BUG|PRIORITY)-\d|\b(?:CODER|WRITER|TESTER|CM|PM|PO)-\d", bullets
        ), bullets
        assert not _STRANDED_HYPHEN_FRAGMENT_RE.search(bullets), bullets
