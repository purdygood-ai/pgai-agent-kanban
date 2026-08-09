"""
test_release_notes_bundled_item_title.py
=========================================
Regression gate for BUG-0029: the release-notes bundled-item title helper
(``get_item_title()``, embedded as a Python heredoc inside
``team/scripts/cm/release.sh``) previously matched ANY '#'-prefixed line
regardless of heading level. A bug file whose first heading was ``## Status``
(no H1 above it) yielded the bogus title ``Status`` in shipped release notes.

The fix restricts the match to a true H1 (``^#\\s``, a single leading '#'
followed by whitespace) and falls back to the bundle item's filename stem
when no H1 line is present -- preserving the pre-existing no-heading-at-all
fallback behavior.

Fidelity approach: rather than hand-copying the helper's logic into the test
(which would drift silently from the production script), this module extracts
the actual ``RELNOTES_PY`` heredoc body from ``team/scripts/cm/release.sh`` at
test time and executes it as a real subprocess with the same argv contract
cm-release.sh uses. This means a regression that reintroduces the loose
'#'-prefix match in the shipped script fails these tests directly -- there is
no parallel implementation to fall out of sync.

Covers:
  1. A true H1 line (``# Title``) yields that title, stripped of the leading
     '#' and surrounding whitespace.
  2. A file whose first '#'-prefixed line is ``## Status`` (the exact BUG-0029
     shape: no H1, an H2 heading appears first) falls back to the file stem,
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
import re
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_THIS_FILE = pathlib.Path(__file__).resolve()
# team/tests/unit/test_release_notes_bundled_item_title.py
# three levels up: unit/ -> tests/ -> team/
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_RELEASE_SH = _TEAM_DIR / "scripts" / "cm" / "release.sh"

_HEREDOC_OPEN_RE = re.compile(r"<<'RELNOTES_PY'\s*$")
_HEREDOC_CLOSE_RE = re.compile(r"^RELNOTES_PY\s*$")


def _extract_relnotes_py_source() -> str:
    """Pull the literal RELNOTES_PY heredoc body out of release.sh.

    Extracting at test time (rather than embedding a hand-copied duplicate)
    ensures this test exercises the exact production code path -- a
    regression in the real script fails this test without any separate
    maintenance of a parallel copy.
    """
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
    """Execute the extracted RELNOTES_PY body with a minimal argv contract
    matching what cm-release.sh passes, and return the generated release
    notes file's text.
    """
    script_path = tmp_path / "_extracted_relnotes.py"
    script_path.write_text(_extract_relnotes_py_source(), encoding="utf-8")

    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)

    argv = [
        sys.executable,
        str(script_path),
        bundle_file,             # bundle_file_arg
        str(repo_root),          # repo_root
        active_rc,               # active_rc
        "FUNCTIONAL",            # status
        "2026-07-27",            # release_date
        "test-project",          # project_name
        "none",                  # merge_base
        "rc/" + active_rc,       # rc_branch
        "unknown-tester-task",   # tester_task_id
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
    is also the BUG-0029 regression lock (see the module docstring)."""

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

        notes = _run_relnotes_py(tmp_path, bundle_file=str(bundle))

        # A None expected_present means the case documents the file-stem
        # fallback rather than a literal H1 title.
        assert (expected_present or f"- {item.stem}") in notes
        if expected_absent is not None:
            assert expected_absent not in notes
