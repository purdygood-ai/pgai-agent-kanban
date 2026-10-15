"""
test_lint_dashboard_hardcode_colors.py
=======================================
Tests for team/scripts/lint_dashboard_hardcode_colors.py — the gate that
fails when a raw ANSI escape sequence literal or raw tmux ``#[fg=...]``
markup literal appears anywhere under ``team/scripts/dashboard/`` or
``team/scripts/lib/dashboard/`` instead of a lookup through the
single-source palette (ansi_code() / dashboard_semantic_color() /
dashboard_semantic_tmux_fg() / pgai_agent_kanban.lib.palette).

Test coverage:

  1. Real-tree green — the shipped tree under both scanned roots produces
     zero violations (proof the current codebase is single-source
     clean).
  2. A raw ANSI escape literal is flagged.
  3. A raw tmux #[fg=...] markup literal is flagged.
  4. A dynamically-built truecolor escape (f-string interpolation) is
     NOT flagged — it is legitimate hex-driven, already-single-sourced
     project-tag color construction, not a hardcoded named color.
  5. Comment-only mention is exempt — a line whose first non-whitespace
     character is '#' does not trigger a violation even though it names
     the literal pattern.
  6. A file under a fixtures/ path segment is exempt.
  7. A file under a golden/ path segment is exempt.
  8. A test_*.py file is exempt.
  9. Nested lib/ subdirectory under team/scripts/dashboard/ is scanned
     (recursive scope), and team/scripts/lib/dashboard/ is scanned too
     (the second scanned root).
  10. main() returns 0 on a clean synthetic tree.
  11. main() returns 1 when a violation is present.
  12. main() returns 2 when --team-dir does not exist.
  13. main() returns 2 when neither scanned root exists.
  14. --help exits 0 (argparse integration smoke test).
  15. Violation message is actionable — names the file, line, and kind.
  16. Remedy block names the shared primitives to call instead of a
      literal, and ends with the re-lint verification command.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load lint_dashboard_hardcode_colors.py as a module (mirrors sibling
# test_lint_dashboard_network_git_gate.py's import-by-path pattern).
# Path: team/tests/unit/ -> team/ (two levels up)
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_dashboard_hardcode_colors.py"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location(
        "lint_dashboard_hardcode_colors", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def _make_team_dir(tmp_path: Path) -> Path:
    """Create a minimal synthetic team/ layout with both scanned roots."""
    team_dir = tmp_path / "team"
    (team_dir / "scripts" / "dashboard" / "lib").mkdir(parents=True, exist_ok=True)
    (team_dir / "scripts" / "lib" / "dashboard").mkdir(parents=True, exist_ok=True)
    return team_dir


# ---------------------------------------------------------------------------
# Case 1 — Real tree is clean
# ---------------------------------------------------------------------------


def test_real_dashboard_trees_have_zero_violations() -> None:
    """Both shipped scanned roots produce zero violations.

    If this test fails, a raw ANSI escape or tmux markup literal was
    reintroduced somewhere under team/scripts/dashboard/ or
    team/scripts/lib/dashboard/ — exactly the regression class this gate
    exists to catch.
    """
    # Deliberately exercises the real tree (not a fixture): this is a live
    # regression guard, not an isolated behavior a fixture could stand in for.
    violations = _lint.scan_dashboard_dirs(_TEAM_DIR, verbose=False)
    assert violations == [], (
        "Unexpected violations on the real dashboard trees:\n"
        + "\n".join(f"  {v.path}:{v.line} ({v.kind}) {v.snippet}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Case 2/3 — Scratch positives: each forbidden literal shape is flagged
# ---------------------------------------------------------------------------


def test_raw_ansi_escape_literal_is_flagged(tmp_path: Path) -> None:
    team_dir = _make_team_dir(tmp_path)
    _write(
        team_dir / "scripts" / "dashboard" / "fake.sh",
        """
        #!/usr/bin/env bash
        printf '\\033[0;31mred text\\033[0m\\n'
        """,
    )
    violations = _lint.scan_dashboard_dirs(team_dir, verbose=False)
    assert len(violations) == 1
    assert violations[0].kind == "ansi_escape"
    assert violations[0].line == 3


def test_raw_tmux_fg_markup_literal_is_flagged(tmp_path: Path) -> None:
    team_dir = _make_team_dir(tmp_path)
    _write(
        team_dir / "scripts" / "lib" / "dashboard" / "fake_frame.sh",
        """
        HALT_DISPLAY="#[fg=red]HALT#[default]"
        """,
    )
    violations = _lint.scan_dashboard_dirs(team_dir, verbose=False)
    assert len(violations) == 1
    assert violations[0].kind == "tmux_fg"


# ---------------------------------------------------------------------------
# Case 4 — Dynamically-built truecolor escape is not flagged
# ---------------------------------------------------------------------------


def test_fstring_truecolor_escape_is_not_flagged(tmp_path: Path) -> None:
    """A hex-driven truecolor build (interpolated) is legitimate, not a hardcode.

    Mirrors the real project_tag_color() helper in column_render_frame.sh:
    the interpolation braces break the digits/semicolons-only pattern this
    lint requires between "[" and "m", so this never matches.
    """
    team_dir = _make_team_dir(tmp_path)
    _write(
        team_dir / "scripts" / "lib" / "dashboard" / "fake_truecolor.py",
        """
        def project_tag_color(r, g, b):
            return f"\\033[38;2;{r};{g};{b}m"
        """,
    )
    violations = _lint.scan_dashboard_dirs(team_dir, verbose=False)
    assert violations == []


# ---------------------------------------------------------------------------
# Case 5 — Comment-only mention is exempt
# ---------------------------------------------------------------------------


def test_comment_only_mention_is_exempt(tmp_path: Path) -> None:
    team_dir = _make_team_dir(tmp_path)
    _write(
        team_dir / "scripts" / "dashboard" / "commented.sh",
        """
        # HALT is rendered with the tmux markup #[fg=red] ... #[default]
        # or the raw escape \\033[0;31m in ANSI mode.
        echo "no literal in real code here"
        """,
    )
    violations = _lint.scan_dashboard_dirs(team_dir, verbose=False)
    assert violations == []


# ---------------------------------------------------------------------------
# Case 6/7/8 — Fixture, golden, and test_*.py exemptions
# ---------------------------------------------------------------------------


def test_fixtures_path_segment_is_exempt(tmp_path: Path) -> None:
    team_dir = _make_team_dir(tmp_path)
    _write(
        team_dir / "scripts" / "dashboard" / "fixtures" / "golden_transcript.sh",
        """
        printf '\\033[0;31mred\\033[0m'
        """,
    )
    violations = _lint.scan_dashboard_dirs(team_dir, verbose=False)
    assert violations == []


def test_golden_path_segment_is_exempt(tmp_path: Path) -> None:
    team_dir = _make_team_dir(tmp_path)
    _write(
        team_dir / "scripts" / "lib" / "dashboard" / "golden" / "expected.sh",
        """
        printf '\\033[0;32mgreen\\033[0m'
        """,
    )
    violations = _lint.scan_dashboard_dirs(team_dir, verbose=False)
    assert violations == []


def test_test_prefixed_python_file_is_exempt(tmp_path: Path) -> None:
    team_dir = _make_team_dir(tmp_path)
    _write(
        team_dir / "scripts" / "dashboard" / "test_something.py",
        """
        EXPECTED = "\\033[0;33myellow\\033[0m"
        """,
    )
    violations = _lint.scan_dashboard_dirs(team_dir, verbose=False)
    assert violations == []


# ---------------------------------------------------------------------------
# Case 9 — Recursive scope: both scanned roots, including nested lib/
# ---------------------------------------------------------------------------


def test_nested_lib_subdirectory_is_scanned(tmp_path: Path) -> None:
    team_dir = _make_team_dir(tmp_path)
    _write(
        team_dir / "scripts" / "dashboard" / "lib" / "nested.sh",
        """
        printf '\\033[0;31mred\\033[0m'
        """,
    )
    violations = _lint.scan_dashboard_dirs(team_dir, verbose=False)
    assert len(violations) == 1
    assert "lib" in violations[0].path.parts


def test_second_scanned_root_is_scanned(tmp_path: Path) -> None:
    team_dir = _make_team_dir(tmp_path)
    _write(
        team_dir / "scripts" / "lib" / "dashboard" / "column_render_cells.sh",
        """
        printf '\\033[0;31mred\\033[0m'
        """,
    )
    violations = _lint.scan_dashboard_dirs(team_dir, verbose=False)
    assert len(violations) == 1


# ---------------------------------------------------------------------------
# Case 10/11 — main() return codes
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_clean_synthetic_tree(tmp_path: Path) -> None:
    team_dir = _make_team_dir(tmp_path)
    _write(team_dir / "scripts" / "dashboard" / "clean.sh", "echo hi\n")
    assert _lint.main(["--team-dir", str(team_dir)]) == 0


def test_main_returns_one_when_violation_present(tmp_path: Path, capsys) -> None:
    team_dir = _make_team_dir(tmp_path)
    _write(
        team_dir / "scripts" / "dashboard" / "dirty.sh",
        """
        printf '\\033[0;31mred\\033[0m'
        """,
    )
    rc = _lint.main(["--team-dir", str(team_dir)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.out


# ---------------------------------------------------------------------------
# Case 12/13 — main() usage errors
# ---------------------------------------------------------------------------


def test_main_returns_two_when_team_dir_does_not_exist(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert _lint.main(["--team-dir", str(missing)]) == 2


def test_main_returns_two_when_neither_scanned_root_exists(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    team_dir.mkdir(parents=True, exist_ok=True)
    assert _lint.main(["--team-dir", str(team_dir)]) == 2


# ---------------------------------------------------------------------------
# Case 14 — --help smoke test
# ---------------------------------------------------------------------------


def test_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert "dashboard" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Case 15/16 — Actionable violation message and remedy block
# ---------------------------------------------------------------------------


def test_violation_message_and_remedy_are_actionable(tmp_path: Path, capsys) -> None:
    team_dir = _make_team_dir(tmp_path)
    target = _write(
        team_dir / "scripts" / "dashboard" / "needs_fix.sh",
        """
        printf '\\033[0;33myellow\\033[0m'
        """,
    )
    rc = _lint.main(["--team-dir", str(team_dir)])
    assert rc == 1
    out = capsys.readouterr().out

    # Names the offending file and line.
    assert str(target) in out
    assert ":2:" in out or ":3:" in out  # line 2 (shebang-less) or 3

    # Remedy names the shared primitives, not a mechanical rewrite.
    assert "ansi_code" in out
    assert "dashboard_semantic_color" in out
    assert "resolve_semantic_color" in out

    # Remedy ends with a runnable re-lint verification command.
    assert "lint_dashboard_hardcode_colors.py" in out
    assert "--team-dir" in out
