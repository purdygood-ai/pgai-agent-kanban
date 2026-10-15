"""
test_defer_card_glyph_placement.py
====================================
The BUGS column's deferred-card glyph renders as a SUFFIX after the bug id
(id, one space, then the glyph), never before it, so every card's id
starts at the same column whether or not that particular card carries a
glyph -- a prefix shifts the id itself and breaks the id column's left
alignment on a board mixing deferred and undeferred cards.

Exercises the real single-project rendering path
(team/scripts/dashboard/column-render.sh input <bugs_dir> ...) end to end,
not a reimplementation of the glyph decision -- the single shared glyph
site is team/pgai_agent_kanban/dashboard/defer_card_glyph.py
(defer_card_glyph_suffix), which both column_render_cells.sh (this test's
single-project path) and column_render_data_multi.sh (the --all-projects
path) import; this file proves the single-project renderer's own output,
not the module in isolation.

Design notes:
  - Uses two_project_root (tmp_path-backed). No bare /tmp paths.
  - Test names describe the behaviour under test, not the ticket that
    motivated them (docs/test-authoring-guidelines.md, Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Path constants (resolve from this file's own location)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_COLUMN_RENDER = _TEAM_DIR / "scripts" / "dashboard" / "column-render.sh"

# team/scripts/lib/status_glyphs.sh's literal for a deferred bug -- this
# test asserts PLACEMENT (suffix, not prefix), not the glyph's own value,
# but needs the real character to find the rendered line.
_GLYPH_DEFERRED = "✋"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# Shared helpers (local to this module -- each integration test file owns
# its own helper shapes rather than importing another module's private
# functions, matching test_defer_icon_truth.py's stated convention)
# ---------------------------------------------------------------------------


def _set_defer_posture(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    deferred_bugs: bool,
    deferred_bug_level: str,
) -> None:
    cfg_path = kanban_root / "projects" / project_name / "project.cfg"
    lines = [
        "",
        "[release]",
        f"deferred_bugs = {'true' if deferred_bugs else 'false'}",
        f"deferred_bug_level = {deferred_bug_level}",
    ]
    with cfg_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _add_bug_file(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_id: str,
    slug: str,
    severity: str,
) -> pathlib.Path:
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_file = bugs_dir / f"{bug_id}-{slug}.md"
    bug_file.write_text(
        f"# {bug_id}: {slug}\n\n"
        f"**Severity:** {severity}\n\n"
        "## Status\nopen\n\n"
        "## Summary\nTest bug file.\n",
        encoding="utf-8",
    )
    return bug_file


def _run_column_render(
    kanban_root: pathlib.Path, bugs_dir: pathlib.Path, *, no_color: bool
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # PGAI_AGENT_KANBAN_ROOT_PATH points at team/ so the embedded Python
    # subprocess can import pgai_agent_kanban (the dev tree carries the
    # package); the synthetic kanban DATA root travels separately via
    # --kanban-root, matching test_dashboard_status_glyph_config.py's
    # established pattern for this same script.
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(_TEAM_DIR)
    for var in ("PGAI_TASKS_DIR", "PGAI_QUEUE_DIR", "PGAI_RELEASE_STATE_PATH"):
        env.pop(var, None)
    env["TERM"] = "dumb" if no_color else "xterm-256color"
    if no_color:
        env["NO_COLOR"] = "1"
    else:
        env.pop("NO_COLOR", None)
    return subprocess.run(
        [
            "bash",
            str(_COLUMN_RENDER),
            "input",
            str(bugs_dir),
            "none",
            "10",
            "40",
            "--label",
            "BUGS",
            "--kanban-root",
            str(kanban_root),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(kanban_root),
        timeout=30,
    )


def _card_lines(stdout: str) -> list[str]:
    """Non-header, non-blank output lines with ANSI codes stripped."""
    return [
        _strip_ansi(ln)
        for ln in stdout.splitlines()
        if ln.strip() and not ln.startswith("===")
    ]


# ---------------------------------------------------------------------------
# Mixed deferred/undeferred board: suffix placement, single space, alignment
# ---------------------------------------------------------------------------


class TestDeferredCardGlyphIsATrailingSuffix:
    def test_mixed_board_renders_ids_left_aligned_with_glyph_as_a_trailing_suffix(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _set_defer_posture(
            root, "project_a", deferred_bugs=True, deferred_bug_level="low"
        )
        # Low severity at level "low" is held back (deferred); high severity
        # is kept -- the same severity/level comparison discovery itself
        # applies (team/scripts/lib/discovery_defer.sh).
        _add_bug_file(root, "project_a", "BUG-0001", "low-a", "low")
        _add_bug_file(root, "project_a", "BUG-0002", "high-a", "high")

        bugs_dir = root / "projects" / "project_a" / "bugs"
        result = _run_column_render(root, bugs_dir, no_color=False)
        assert result.returncode == 0, result.stderr

        lines = _card_lines(result.stdout)
        deferred_line = next((ln for ln in lines if ln.startswith("BUG-0001")), None)
        kept_line = next((ln for ln in lines if ln.startswith("BUG-0002")), None)
        assert deferred_line is not None, f"stdout: {result.stdout!r}"
        assert kept_line is not None, f"stdout: {result.stdout!r}"

        # Suffix position: id first, exactly one space, then the glyph --
        # never the glyph before the id.
        assert deferred_line == f"BUG-0001 {_GLYPH_DEFERRED}", deferred_line
        assert "  " not in deferred_line, (
            f"expected no double spaces between id and glyph: {deferred_line!r}"
        )

        # An undeferred (kept) card carries no glyph and no trailing space.
        assert kept_line == "BUG-0002", kept_line

        # Ids stay left-aligned in one column whether or not a glyph
        # follows: both cards' ids start at the same offset (0).
        assert deferred_line.index("BUG-0001") == kept_line.index("BUG-0002") == 0

    def test_no_color_mode_renders_the_id_alone_with_no_glyph_at_all(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _set_defer_posture(
            root, "project_a", deferred_bugs=True, deferred_bug_level="low"
        )
        _add_bug_file(root, "project_a", "BUG-0001", "low-a", "low")

        bugs_dir = root / "projects" / "project_a" / "bugs"
        result = _run_column_render(root, bugs_dir, no_color=True)
        assert result.returncode == 0, result.stderr

        lines = _card_lines(result.stdout)
        deferred_line = next((ln for ln in lines if ln.startswith("BUG-0001")), None)
        assert deferred_line is not None, f"stdout: {result.stdout!r}"
        assert deferred_line == "BUG-0001", deferred_line
        assert _GLYPH_DEFERRED not in deferred_line
