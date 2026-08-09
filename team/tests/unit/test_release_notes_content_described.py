"""
test_release_notes_content_described.py
=========================================
Regression gate for the content-described-notes policy: the release-notes
generator (``get_item_title()``, embedded as a Python heredoc inside
``team/scripts/cm/release.sh``) must render each bundled BUG-NNNN or
PRIORITY-NNNN item by SUBSTANCE — sourced from the item's own
Symptom/Fix (bugs) or Title/Description (priorities) text — and must never
emit the bare internal ledger ID as the rendered title.

Fidelity approach: identical to test_release_notes_bundled_item_title.py —
the actual ``RELNOTES_PY`` heredoc body is extracted from
``team/scripts/cm/release.sh`` at test time and executed as a real
subprocess, so a regression in the shipped generator fails these tests
directly. There is no parallel implementation to drift out of sync.

Covers:
  1. A bug item with a ``## Fix`` section renders the Fix text as the bullet,
     with zero occurrences of its own BUG-NNNN ID anywhere in the notes file.
  2. A bug item with only a ``## Symptom`` section (no Fix) renders a
     "Fixed: <symptom>" bullet, still with zero occurrences of its own ID.
  3. A bug item with neither Symptom nor Fix text falls back to a generic,
     ID-free sentence rather than the file stem (which would itself be the
     bare ledger ID for a bug file).
  4. A priority item with a ``## Title`` section renders that title as the
     bullet, with zero occurrences of its own PRIORITY-NNNN ID.
  5. A priority item with only ``## Description`` (no Title) falls back to
     the Description text.
  6. Defense-in-depth: a bug's own Symptom/Fix prose incidentally mentioning
     a DIFFERENT internal bug ID inline is scrubbed from the rendered
     bullet — the generator does not merely avoid quoting its own item's ID,
     it never lets any internal ledger ID through.
  7. A public KI-N.N.N.N disclosure ID mentioned in Fix/Symptom prose passes
     through UNCHANGED — the scrub in (6) is scoped to internal
     BUG-/PRIORITY-/task IDs only, never the public disclosure namespace.
  8. The generated release-notes file, taken as a whole, is clean under the
     real ``lint_comment_provenance.py`` gate with no carve-out — proving
     the generator complies "by construction" rather than merely by this
     test's own narrower assertions.

All temp paths use pytest's tmp_path fixture (redirected to the framework
temp root by conftest.py) or the framework temp root directly for the
lint-scan case, which must live outside any 'tests/'-segment path (see
test_lint_comment_provenance.py's module docstring for why). No bare /tmp
paths. No live kanban root is touched.
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

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_THIS_FILE = pathlib.Path(__file__).resolve()
# team/tests/unit/test_release_notes_content_described.py
# three levels up: unit/ -> tests/ -> team/
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_RELEASE_SH = _TEAM_DIR / "scripts" / "cm" / "release.sh"
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_comment_provenance.py"

_HEREDOC_OPEN_RE = re.compile(r"<<'RELNOTES_PY'\s*$")
_HEREDOC_CLOSE_RE = re.compile(r"^RELNOTES_PY\s*$")

_SCRATCH_SUBDIR = "release_notes_content_described_scratch"


def _extract_relnotes_py_source() -> str:
    """Pull the literal RELNOTES_PY heredoc body out of release.sh."""
    lines = _RELEASE_SH.read_text(encoding="utf-8").splitlines()
    body_lines = []
    in_heredoc = False
    for line in lines:
        if not in_heredoc and _HEREDOC_OPEN_RE.search(line):
            in_heredoc = True
            continue
        if in_heredoc and _HEREDOC_CLOSE_RE.match(line):
            break
        if in_heredoc:
            body_lines.append(line)
    assert body_lines, (
        f"Could not locate RELNOTES_PY heredoc body in {_RELEASE_SH}. "
        "The heredoc marker may have been renamed or removed."
    )
    return "\n".join(body_lines) + "\n"


def _run_relnotes_py(
    tmp_path: pathlib.Path,
    *,
    bundle_file: str,
    active_rc: str = "v9.9.9",
) -> str:
    """Execute the extracted RELNOTES_PY body and return the generated
    release notes file's text (same argv contract cm-release.sh uses)."""
    script_path = tmp_path / "_extracted_relnotes.py"
    script_path.write_text(_extract_relnotes_py_source(), encoding="utf-8")

    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)

    argv = [
        sys.executable,
        str(script_path),
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
    result = subprocess.run(
        argv, capture_output=True, text=True, check=False, cwd=str(tmp_path)
    )
    assert result.returncode == 0, (
        f"RELNOTES_PY extraction subprocess failed (rc={result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

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
    def test_fix_section_renders_as_bullet_with_no_bare_id(
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

        notes = _run_relnotes_py(tmp_path, bundle_file=str(bundle))

        assert (
            "- Serialize writes to the retry record with a file lock so only "
            "one agent writes at a time." in notes
        )
        assert "BUG-0042" not in notes

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

        notes = _run_relnotes_py(tmp_path, bundle_file=str(bundle))

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

        notes = _run_relnotes_py(tmp_path, bundle_file=str(bundle))

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

        notes = _run_relnotes_py(tmp_path, bundle_file=str(bundle))

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

        notes = _run_relnotes_py(tmp_path, bundle_file=str(bundle))

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
            "## Fix\n"
            "Restored the guard removed during the BUG-0011 cleanup.\n",
            encoding="utf-8",
        )
        bundle = _make_bundle(tmp_path, [bug])

        notes = _run_relnotes_py(tmp_path, bundle_file=str(bundle))

        assert "BUG-0050" not in notes
        assert "BUG-0011" not in notes
        assert "Restored the guard removed during the cleanup." in notes

    def test_inline_public_ki_id_passes_through_unchanged(
        self, tmp_path: pathlib.Path
    ) -> None:
        bug = tmp_path / "BUG-0051-followup-to-disclosed-issue.md"
        bug.write_text(
            "# BUG-0051-followup-to-disclosed-issue\n\n"
            "## Status\nopen\n\n"
            "## Fix\n"
            "Closed the remaining edge case from public disclosure KI-1.2.3.4.\n",
            encoding="utf-8",
        )
        bundle = _make_bundle(tmp_path, [bug])

        notes = _run_relnotes_py(tmp_path, bundle_file=str(bundle))

        assert "BUG-0051" not in notes
        assert "KI-1.2.3.4" in notes


# ---------------------------------------------------------------------------
# End-to-end: the generated file passes the real provenance lint, no
# carve-out, proving by-construction compliance rather than a narrower
# hand-picked assertion set above.
# ---------------------------------------------------------------------------


def _pgai_temp_root() -> pathlib.Path:
    """Return $PGAI_AGENT_KANBAN_TEMP_DIR/<_SCRATCH_SUBDIR>, creating it if needed.

    Falls back to the documented resolver default only when the env var is
    unset, matching the fallback convention used by
    test_lint_comment_provenance.py's own _pgai_temp_root() (see the literal
    itself, below, for the exact value) and by temp.sh's pgai_temp_dir().
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
    lint_comment_provenance.py's path-exclusion rules unconditionally skip
    any path containing a 'tests/' segment (see
    test_lint_comment_provenance.py's module docstring for the same
    concern)."""
    d = pathlib.Path(tempfile.mkdtemp(dir=str(_pgai_temp_root())))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _import_lint_module():
    spec = importlib.util.spec_from_file_location(
        "lint_comment_provenance", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
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

    notes = _run_relnotes_py(tmp_path, bundle_file=str(bundle))

    scanned_file = lint_scan_dir / "v9.9.9.md"
    scanned_file.write_text(notes, encoding="utf-8")

    lint = _import_lint_module()
    findings = lint._scan_file(scanned_file)
    assert findings == [], (
        f"lint_comment_provenance found findings in generated release notes: {findings}"
    )
