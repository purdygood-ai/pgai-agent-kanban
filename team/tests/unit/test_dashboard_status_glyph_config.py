"""
test_dashboard_status_glyph_config.py
======================================
Unit tests for PRIORITY-0007 — dashboard_status_glyph kanban.cfg key.

Covers three acceptance behaviors:

1. Default render (absent key):
   When kanban.cfg has no dashboard_status_glyph key, column-render.sh
   renders the "■" glyph and the output is byte-identical to output
   produced when the key is explicitly set to "■".

2. ASCII override alignment:
   When kanban.cfg sets dashboard_status_glyph to an ASCII character
   (e.g. '#'), column-render.sh renders the configured character instead
   of "■", and column widths stay aligned (TAG_VIS_LEN is the same because
   both "■" and a single ASCII char are 1 logical character each).

3. Multi-character rejection:
   When kanban.cfg sets dashboard_status_glyph to a multi-character value,
   load_config in config_loader.sh exits non-zero with a message that names
   the key dashboard_status_glyph.

Fixture structure built under pytest tmp_path:
  <tmp_path>/
    projects.cfg                      — one project registered
    projects/
      glyph-test/
        tasks/
          queues/
            coder_backlog.md          — one task entry for the coder queue
          CODER-20260721-001-test/
            status.md                 — state WORKING

Tests invoke bash scripts via subprocess, matching the pattern in
test_status_bar_glyphs.py.  TERM=xterm-256color enables color output so the
glyph character is present in the tagged column line.

PGAI_AGENT_KANBAN_ROOT_PATH is set to the team/ directory of the dev tree so
that the Python subprocess inside column-render.sh can resolve the
pgai_agent_kanban package.  The kanban DATA root (projects.cfg, queue files,
kanban.cfg) is always the test's tmp_path, passed via --kanban-root.  The
live kanban install is never written to.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Script paths (relative to this file: team/tests/unit/ → team/)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_COLUMN_RENDER = _TEAM_DIR / "scripts" / "dashboard" / "column-render.sh"
_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"

# The team/ directory contains the pgai_agent_kanban Python package.
# column-render.sh inserts PGAI_AGENT_KANBAN_ROOT_PATH into sys.path so the
# Python inline section can import the package.  Using _TEAM_DIR here gives
# the subprocess access to the package without touching the live kanban root.
_PYTHON_PKG_ROOT = str(_TEAM_DIR)

# Default dashboard status glyph (U+25A0 BLACK SQUARE)
_DEFAULT_GLYPH = "■"  # ■


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_glyph_test_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build a minimal multi-project kanban root under tmp_path.

    Creates one project (glyph-test) with a single coder queue entry so that
    column-render.sh --all-projects renders a tagged row containing the glyph.
    Returns tmp_path (the kanban root).
    """
    # Create the root directory and register one project in projects.cfg
    tmp_path.mkdir(parents=True, exist_ok=True)
    projects_cfg = tmp_path / "projects.cfg"
    projects_cfg.write_text(
        "[project:glyph-test]\n"
        "priority=1\n"
        "enabled=true\n"
        "dashboard_color=#378ADD\n",
        encoding="utf-8",
    )

    # Create the project's task structure
    task_dir = tmp_path / "projects" / "glyph-test" / "tasks" / "CODER-20260721-001-test"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "status.md").write_text(
        "# Status\n\n## State\nWORKING\n",
        encoding="utf-8",
    )

    # Create the coder queue backlog with one entry in QUEUE_LINE_RE format
    queue_dir = tmp_path / "projects" / "glyph-test" / "tasks" / "queues"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "coder_backlog.md").write_text(
        "# CODER Backlog\n\n"
        "- [A] CODER-20260721-001-test\n",
        encoding="utf-8",
    )

    return tmp_path


def _run_column_render(
    kanban_root: pathlib.Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke column-render.sh in queue --all-projects coder mode.

    Sets TERM=xterm-256color so color/glyph output is enabled.
    Sets PGAI_AGENT_KANBAN_ROOT_PATH to the team/ directory so the
    embedded Python subprocess can import pgai_agent_kanban.
    Uses --kanban-root to point at the synthetic test fixture root.
    """
    env = dict(os.environ)
    # Redirect PGAI_AGENT_KANBAN_ROOT_PATH to team/ for Python package access.
    # Data (projects.cfg, queue files, kanban.cfg) comes from --kanban-root.
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = _PYTHON_PKG_ROOT
    # Unset derivative env vars so they do not bleed from the test process.
    for var in ("PGAI_TASKS_DIR", "PGAI_QUEUE_DIR", "PGAI_RELEASE_STATE_PATH"):
        env.pop(var, None)
    # Unset DASHBOARD_STATUS_GLYPH so the script reads it from kanban.cfg.
    env.pop("DASHBOARD_STATUS_GLYPH", None)
    env["TERM"] = "xterm-256color"
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        [
            "bash",
            str(_COLUMN_RENDER),
            "queue", "none", "5", "38",
            "--label", "CODER",
            "--kanban-root", str(kanban_root),
            "--all-projects",
            "--column-type", "coder",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Test 1 — default render: absent key produces byte-identical output to "■"
# ---------------------------------------------------------------------------

def test_default_glyph_absent_key_byte_identical(tmp_path: pathlib.Path) -> None:
    """Absent dashboard_status_glyph key renders "■" byte-identical to explicit "■" cfg.

    PRIORITY-0007 acceptance criterion 1 (default byte-parity):
    When kanban.cfg has no dashboard_status_glyph key, column-render.sh must
    produce output that is byte-identical to output from a run where the key
    is explicitly set to the default "■" character.  This confirms the default
    falls back cleanly without altering any rendered output.
    """
    # Fixture A: no kanban.cfg at all (absent key)
    root_absent = _make_glyph_test_root(tmp_path / "absent")

    # Fixture B: kanban.cfg with explicit dashboard_status_glyph = ■
    root_explicit = _make_glyph_test_root(tmp_path / "explicit")
    (root_explicit / "kanban.cfg").write_text(
        "[dashboard]\n"
        f"dashboard_status_glyph = {_DEFAULT_GLYPH}\n",
        encoding="utf-8",
    )

    result_absent = _run_column_render(root_absent)
    result_explicit = _run_column_render(root_explicit)

    assert result_absent.returncode == 0, (
        f"column-render.sh failed with absent key; stderr: {result_absent.stderr!r}"
    )
    assert result_explicit.returncode == 0, (
        f"column-render.sh failed with explicit '■'; stderr: {result_explicit.stderr!r}"
    )

    # Verify the default glyph "■" appears in the absent-key output
    assert _DEFAULT_GLYPH in result_absent.stdout, (
        f"Expected default glyph {_DEFAULT_GLYPH!r} in absent-key output; "
        f"stdout: {result_absent.stdout!r}"
    )

    # Verify byte-identical output
    assert result_absent.stdout == result_explicit.stdout, (
        f"Absent-key output differs from explicit-'■' output.\n"
        f"  absent-key:  {result_absent.stdout!r}\n"
        f"  explicit-■: {result_explicit.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — ASCII override: glyph swaps, column widths stay aligned
# ---------------------------------------------------------------------------

def test_ascii_override_swaps_glyph_and_widths_remain_aligned(
    tmp_path: pathlib.Path,
) -> None:
    """ASCII override in kanban.cfg swaps glyph; column widths stay aligned.

    PRIORITY-0007 acceptance criterion 2 (ASCII override alignment):
    When kanban.cfg sets dashboard_status_glyph = # (a single ASCII char),
    column-render.sh renders '#' instead of '■' in every tagged row.  Column
    widths remain aligned because TAG_VIS_LEN = len(glyph) + 1, which is 2
    for any single-character glyph (ASCII or Unicode single-char).

    Width alignment is verified by comparing the visible-char length of the
    rendered data line (stripping ANSI escape codes) between the default-glyph
    run and the '#'-override run.  Both must be the same length.
    """
    ascii_glyph = "#"

    # Fixture A: default "■" glyph (no kanban.cfg)
    root_default = _make_glyph_test_root(tmp_path / "default")

    # Fixture B: ASCII glyph '#' via kanban.cfg
    root_ascii = _make_glyph_test_root(tmp_path / "ascii")
    (root_ascii / "kanban.cfg").write_text(
        "[dashboard]\n"
        f"dashboard_status_glyph = {ascii_glyph}\n",
        encoding="utf-8",
    )

    result_default = _run_column_render(root_default)
    result_ascii = _run_column_render(root_ascii)

    assert result_default.returncode == 0, (
        f"column-render.sh failed with default glyph; stderr: {result_default.stderr!r}"
    )
    assert result_ascii.returncode == 0, (
        f"column-render.sh failed with ASCII glyph '{ascii_glyph}'; "
        f"stderr: {result_ascii.stderr!r}"
    )

    # The ASCII glyph '#' must appear in the override output
    assert ascii_glyph in result_ascii.stdout, (
        f"Expected ASCII glyph '{ascii_glyph}' in override output; "
        f"stdout: {result_ascii.stdout!r}"
    )

    # The default glyph '■' must NOT appear in the override output
    assert _DEFAULT_GLYPH not in result_ascii.stdout, (
        f"Default glyph {_DEFAULT_GLYPH!r} must not appear in ASCII-override output; "
        f"stdout: {result_ascii.stdout!r}"
    )

    # Column width alignment: strip ANSI escape codes and compare line lengths.
    # Both single-char glyphs yield TAG_VIS_LEN = 2, so rendered lines should
    # have the same visible character count.
    import re
    _ansi_re = re.compile(r'\x1b\[[0-9;]*m')

    def _visible_lines(text: str) -> list[str]:
        """Return non-header, non-empty lines with ANSI codes stripped."""
        return [
            _ansi_re.sub("", line)
            for line in text.splitlines()
            if line and not line.startswith("===")
        ]

    default_lines = _visible_lines(result_default.stdout)
    ascii_lines = _visible_lines(result_ascii.stdout)

    assert default_lines, "Default output has no data lines to compare"
    assert ascii_lines, "ASCII-override output has no data lines to compare"
    assert len(default_lines) == len(ascii_lines), (
        f"Line count differs between default and override outputs: "
        f"default={len(default_lines)}, override={len(ascii_lines)}"
    )

    for i, (dl, al) in enumerate(zip(default_lines, ascii_lines)):
        assert len(dl) == len(al), (
            f"Line {i}: visible-char length differs between default and override outputs.\n"
            f"  default (len={len(dl)}): {dl!r}\n"
            f"  override (len={len(al)}): {al!r}\n"
            "Column widths are not aligned after glyph override."
        )


# ---------------------------------------------------------------------------
# Test 3 — multi-char rejection: config loader exits non-zero, names the key
# ---------------------------------------------------------------------------

def test_multichar_glyph_rejected_with_error_naming_key(tmp_path: pathlib.Path) -> None:
    """Multi-character dashboard_status_glyph causes config loader to exit non-zero.

    PRIORITY-0007 acceptance criterion 3 (multi-char rejection):
    When kanban.cfg sets dashboard_status_glyph to a value with more than one
    character, load_config in config_loader.sh must:
    - exit with a non-zero return code
    - write an error message to stderr that names the key 'dashboard_status_glyph'

    This test invokes load_config directly via a bash subprocess that sources
    config_loader.sh and calls load_config on a synthetic cfg file.
    """
    # Write a kanban.cfg with a two-character glyph value
    cfg_file = tmp_path / "kanban.cfg"
    cfg_file.write_text(
        "[dashboard]\n"
        "dashboard_status_glyph = ##\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = _PYTHON_PKG_ROOT
    env.pop("DASHBOARD_STATUS_GLYPH", None)

    result = subprocess.run(
        [
            "bash", "-c",
            f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r}",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )

    # Must exit non-zero
    assert result.returncode != 0, (
        f"Expected non-zero exit for multi-char glyph; got returncode={result.returncode}.\n"
        f"stderr: {result.stderr!r}"
    )

    # Error message must name the key
    assert "dashboard_status_glyph" in result.stderr, (
        f"Expected 'dashboard_status_glyph' in error message; "
        f"stderr: {result.stderr!r}"
    )
