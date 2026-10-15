"""
test_release_notes_bundled_item_title.py
=========================================
Regression gate: the release-notes bundled-item title helper
(``get_item_title()`` in ``pgai_agent_kanban.cm.release_notes_generator``,
the generator ``team/scripts/lib/cm/release_notes.sh`` runs at Step 11a)
previously matched ANY '#'-prefixed line regardless of heading level. A bug
file whose first heading was ``## Status`` (no H1 above it) yielded the
bogus title ``Status`` in shipped release notes.

The fix restricts the match to a true H1 (``^#\\s``, a single leading '#'
followed by whitespace) and falls back to the bundle item's filename stem
when no H1 line is present -- preserving the pre-existing no-heading-at-all
fallback behavior.

Fidelity approach: rather than hand-copying the helper's logic into the test
(which would drift silently from the production module), this module imports
the shipped generator and calls its CLI entry point, ``main()``, with the
same argv contract cm_release_generate_notes passes. This means a
regression that reintroduces the loose '#'-prefix match in the shipped
generator fails these tests directly -- there is no parallel implementation
to fall out of sync.

Covers:
  1. A true H1 line (``# Title``) yields that title, stripped of the leading
     '#' and surrounding whitespace.
  2. A file whose first '#'-prefixed line is ``## Status`` (the exact
     no-H1-with-an-H2-first shape) falls back to the file stem,
     not to the H2's text.
  3. A file with no '#'-prefixed line at all falls back to the file stem
     (pre-existing fallback behavior, preserved by the fix).
  4. A deeper heading (``### Detail``) with no H1 above it also falls back to
     the file stem -- the fix is heading-level-general, not just an H2 special
     case.

All temp paths use pytest's tmp_path fixture (redirected to the framework
temp root by conftest.py). No bare /tmp paths. No live kanban root is touched.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.cm.release_notes_generator import main as release_notes_main


def _generate_notes(
    tmp_path: pathlib.Path,
    *,
    bundle_file: str,
    active_rc: str = "v9.9.9",
) -> str:
    """Run the shipped generator's CLI entry point with a minimal argv
    contract matching what cm_release_generate_notes passes, and return the
    generated release notes file's text.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)

    argv = [
        bundle_file,             # bundle_file_arg
        str(repo_root),          # repo_root
        active_rc,               # active_rc
        "FUNCTIONAL",            # status
        "2026-07-27",            # release_date
        "test-project",          # project_name
        "none",                  # merge_base
        "rc/" + active_rc,       # rc_branch
        "unknown-tester-task",   # known_issue_block (unused at FUNCTIONAL)
    ]
    rc = release_notes_main(argv)
    assert rc == 0, f"release-notes generator returned {rc}"

    out_file = repo_root / "release-notes" / f"{active_rc}.md"
    assert out_file.exists(), f"Expected release notes file not written: {out_file}"
    return out_file.read_text(encoding="utf-8")


def _make_bundle(tmp_path: pathlib.Path, item_paths: list[pathlib.Path]) -> pathlib.Path:
    """Write a minimal bundle file with a '## Bundled Items' section pointing
    at the given item file paths, matching the '(`path`)' shape the real
    parser's regex expects.
    """
    bundle_path = tmp_path / "bundle.md"
    lines = ["# Bundle", "", "## Bundled Items"]
    for item_path in item_paths:
        lines.append(f"- fixture item (`{item_path}`)")
    bundle_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return bundle_path


class TestBundledItemTitleH1Restriction:
    """Table-driven: each case writes one bundle item and asserts what title
    line does (and does not) appear in the rendered release notes. Every
    original per-scenario test survives as one row in this table
    (survives-as-named-parameterization; no case dropped) — this whole file
    is also the title-fallback regression lock (see the module docstring)."""

    @pytest.mark.parametrize(
        "item_filename, item_content, expected_present, expected_absent",
        [
            pytest.param(
                "item-with-h1.md",
                "# A Real Title\n\nSome body text.\n",
                "- A Real Title",
                None,
                id="true_h1_line_yields_stripped_title",
            ),
            pytest.param(
                "bug-with-no-h1.md",
                "**Filed By:** someone\n\n## Status\n\nopen\n",
                None,  # filled in below from the item's own stem
                "- Status",
                id="h2_only_file_falls_back_to_file_stem_not_h2_text",
            ),
            pytest.param(
                "plain-item.md",
                "Just some prose with no heading line.\n",
                None,
                None,
                id="no_heading_at_all_falls_back_to_file_stem",
            ),
            pytest.param(
                "deep-heading-item.md",
                "### Detail\n\nsome detail text\n",
                None,
                "- Detail",
                id="deeper_heading_with_no_h1_falls_back_to_file_stem",
            ),
        ],
    )
    def test_title_extraction_matches_h1_restriction(
        self,
        tmp_path: pathlib.Path,
        item_filename: str,
        item_content: str,
        expected_present: str | None,
        expected_absent: str | None,
    ) -> None:
        item = tmp_path / item_filename
        item.write_text(item_content, encoding="utf-8")
        bundle = _make_bundle(tmp_path, [item])

        notes = _generate_notes(tmp_path, bundle_file=str(bundle))

        # A None expected_present means the case documents the file-stem
        # fallback rather than a literal H1 title.
        assert (expected_present or f"- {item.stem}") in notes
        if expected_absent is not None:
            assert expected_absent not in notes
