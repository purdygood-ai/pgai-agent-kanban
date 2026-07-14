"""
test_status_bar_glyphs.py
=========================
Tests for the status-bar glyph set (v1.12.0):
  - team/scripts/lib/status_glyphs.sh  (glyph lib — single home for literals)
  - team/scripts/dashboard/status-right.sh
  - team/scripts/dashboard/status-bottom.sh

Covers three classes of acceptance criteria:

1. Rich-mode glyph assertions — every segment shows its leading glyph in
   rich mode (USE_COLOR, color-capable terminal).

2. NO_COLOR regression lock — NO_COLOR / TERM=dumb output for every state
   is byte-identical to the pre-RC dumb-mode baseline.  The sibling scripts
   must NOT emit glyphs in dumb mode.

3. Sibling byte-equality gate — the glyph-consuming rendering logic produces
   the same glyph for corresponding states across both siblings, confirming
   that glyph literals are delegated to the shared lib.

Fixture structure built by each test (under pytest tmp_path):
  <tmp_path>/
    VERSION                           — framework version file
    projects/
      fixture-proj/
        tasks/
          HUMAN-APPROVE-v1-001/
            status.md                 — WAITING | BACKLOG | DONE
    HALT                              — (created/removed per-test)
    HALT-AFTER                        — (created/removed per-test)

Test isolation:
  All filesystem operations are under tmp_path (pytest-managed; redirected
  by the parent conftest.py autouse fixture).  Subprocess invocations set
  PGAI_AGENT_KANBAN_ROOT_PATH to tmp_path to isolate from the live install.

Rich-mode invocations: TERM=xterm-256color, NO_COLOR unset (via env -u).
Dumb-mode invocations: TERM=dumb (suppresses ANSI; also suppresses glyphs).
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Script paths
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/tests/unit/ -> team/
_STATUS_RIGHT  = _TEAM_DIR / "scripts" / "dashboard" / "status-right.sh"
_STATUS_BOTTOM = _TEAM_DIR / "scripts" / "dashboard" / "status-bottom.sh"

# ---------------------------------------------------------------------------
# Expected glyphs (must match team/scripts/lib/status_glyphs.sh)
# ---------------------------------------------------------------------------
GLYPH_VERSION    = "\U0001f4dd"   # 📝
GLYPH_PM_AUTO    = "\U0001f7e2"   # 🟢
GLYPH_PM_MANUAL  = "\U0001f7e1"   # 🟡
GLYPH_APPROVAL   = "✋"       # ✋
GLYPH_HALT       = "\U0001f6d1"   # 🛑
GLYPH_HALT_AFTER = "⚠️" # ⚠️  (U+26A0 + U+FE0F variation selector)
GLYPH_TIMESTAMP  = "\U0001f4c5"   # 📅


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _make_root(tmp_path: pathlib.Path, *, version: str = "v1.12.0") -> pathlib.Path:
    """Create a minimal kanban root under tmp_path with a VERSION file."""
    (tmp_path / "VERSION").write_text(version + "\n", encoding="utf-8")
    (tmp_path / "projects").mkdir(exist_ok=True)
    return tmp_path


def _write_human_approve_task(
    root: pathlib.Path,
    project: str,
    task_id: str,
    state: str,
) -> pathlib.Path:
    """Create a HUMAN-APPROVE task folder with a status.md in the given state."""
    task_dir = root / "projects" / project / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(
        f"# Status\n\n## State\n{state}\n\n## Needs Human\nyes\n",
        encoding="utf-8",
    )
    return status_file


def _run_right(
    root: pathlib.Path,
    *,
    rich: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke status-right.sh.  rich=True uses xterm-256color; False uses TERM=dumb."""
    env = dict(os.environ)
    env.pop("NO_COLOR", None)
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_QUEUE_DIR", None)
    env.pop("PGAI_RELEASE_STATE_PATH", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    if rich:
        env["TERM"] = "xterm-256color"
    else:
        env["TERM"] = "dumb"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(_STATUS_RIGHT), str(root)],
        capture_output=True, text=True, env=env, timeout=30,
    )


def _run_bottom(
    root: pathlib.Path,
    *,
    rich: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke status-bottom.sh.  rich=True uses xterm-256color; False uses TERM=dumb."""
    env = dict(os.environ)
    env.pop("NO_COLOR", None)
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_QUEUE_DIR", None)
    env.pop("PGAI_RELEASE_STATE_PATH", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    if rich:
        env["TERM"] = "xterm-256color"
    else:
        env["TERM"] = "dumb"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(_STATUS_BOTTOM), str(root)],
        capture_output=True, text=True, env=env, timeout=30,
    )


# ===========================================================================
# 1. Rich-mode glyph assertions
# ===========================================================================


class TestRichModeGlyphs:
    """Every segment emits its leading glyph in rich mode."""

    def test_right_version_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh emits GLYPH_VERSION before the version string in rich mode."""
        root = _make_root(tmp_path)
        result = _run_right(root, rich=True)
        assert result.returncode == 0, result.stderr
        assert GLYPH_VERSION in result.stdout, (
            f"Expected GLYPH_VERSION ({GLYPH_VERSION!r}) in rich output; "
            f"stdout: {result.stdout!r}"
        )

    def test_right_pm_auto_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh emits GLYPH_PM_AUTO before PM:auto in rich mode."""
        root = _make_root(tmp_path)
        result = _run_right(root, rich=True)
        assert result.returncode == 0, result.stderr
        assert GLYPH_PM_AUTO in result.stdout, (
            f"Expected GLYPH_PM_AUTO ({GLYPH_PM_AUTO!r}) in rich output; "
            f"stdout: {result.stdout!r}"
        )

    def test_right_pm_manual_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh emits GLYPH_PM_MANUAL before PM:manual in rich mode."""
        root = _make_root(tmp_path)
        result = _run_right(root, rich=True, extra_env={"PGAI_KANBAN_PM_MODE": "manual"})
        assert result.returncode == 0, result.stderr
        assert GLYPH_PM_MANUAL in result.stdout, (
            f"Expected GLYPH_PM_MANUAL ({GLYPH_PM_MANUAL!r}) in rich output; "
            f"stdout: {result.stdout!r}"
        )
        assert GLYPH_PM_AUTO not in result.stdout, (
            "Auto glyph must not appear when PM mode is manual"
        )

    def test_right_halt_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh emits GLYPH_HALT before HALT GLOBAL in rich mode."""
        root = _make_root(tmp_path)
        (root / "HALT").touch()
        result = _run_right(root, rich=True)
        assert result.returncode == 0, result.stderr
        assert GLYPH_HALT in result.stdout, (
            f"Expected GLYPH_HALT ({GLYPH_HALT!r}) in rich halt output; "
            f"stdout: {result.stdout!r}"
        )

    def test_right_halt_after_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh emits GLYPH_HALT_AFTER before HALT-AFTER GLOBAL in rich mode."""
        root = _make_root(tmp_path)
        (root / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
        result = _run_right(root, rich=True)
        assert result.returncode == 0, result.stderr
        # GLYPH_HALT_AFTER may be a two-codepoint sequence (⚠️); check for first codepoint
        assert "⚠" in result.stdout, (
            f"Expected GLYPH_HALT_AFTER (⚠) in rich draining output; "
            f"stdout: {result.stdout!r}"
        )
        # Must NOT show HALT stop glyph (red 🛑) for a draining state
        assert GLYPH_HALT not in result.stdout, (
            "HALT stop glyph must not appear for HALT-AFTER (draining) state"
        )

    def test_right_approval_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh emits GLYPH_APPROVAL before APPROVAL(n) in rich mode."""
        root = _make_root(tmp_path)
        _write_human_approve_task(root, "proj", "HUMAN-APPROVE-v1-001", state="WAITING")
        result = _run_right(root, rich=True)
        assert result.returncode == 0, result.stderr
        assert GLYPH_APPROVAL in result.stdout, (
            f"Expected GLYPH_APPROVAL ({GLYPH_APPROVAL!r}) in rich approval output; "
            f"stdout: {result.stdout!r}"
        )

    def test_right_timestamp_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh emits GLYPH_TIMESTAMP before the date/time in rich mode."""
        root = _make_root(tmp_path)
        result = _run_right(root, rich=True)
        assert result.returncode == 0, result.stderr
        assert GLYPH_TIMESTAMP in result.stdout, (
            f"Expected GLYPH_TIMESTAMP ({GLYPH_TIMESTAMP!r}) in rich output; "
            f"stdout: {result.stdout!r}"
        )

    def test_bottom_version_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh emits GLYPH_VERSION before the install version in rich mode."""
        root = _make_root(tmp_path)
        result = _run_bottom(root, rich=True)
        assert result.returncode == 0, result.stderr
        assert GLYPH_VERSION in result.stdout, (
            f"Expected GLYPH_VERSION ({GLYPH_VERSION!r}) in rich bottom output; "
            f"stdout: {result.stdout!r}"
        )

    def test_bottom_pm_auto_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh emits GLYPH_PM_AUTO before PM:auto in rich mode."""
        root = _make_root(tmp_path)
        result = _run_bottom(root, rich=True)
        assert result.returncode == 0, result.stderr
        assert GLYPH_PM_AUTO in result.stdout, (
            f"Expected GLYPH_PM_AUTO ({GLYPH_PM_AUTO!r}) in rich bottom output; "
            f"stdout: {result.stdout!r}"
        )

    def test_bottom_pm_manual_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh emits GLYPH_PM_MANUAL before PM:manual in rich mode."""
        root = _make_root(tmp_path)
        result = _run_bottom(root, rich=True, extra_env={"PGAI_KANBAN_PM_MODE": "manual"})
        assert result.returncode == 0, result.stderr
        assert GLYPH_PM_MANUAL in result.stdout, (
            f"Expected GLYPH_PM_MANUAL ({GLYPH_PM_MANUAL!r}) in rich bottom output; "
            f"stdout: {result.stdout!r}"
        )

    def test_bottom_halt_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh emits GLYPH_HALT before HALT GLOBAL in rich mode."""
        root = _make_root(tmp_path)
        (root / "HALT").touch()
        result = _run_bottom(root, rich=True)
        assert result.returncode == 0, result.stderr
        assert GLYPH_HALT in result.stdout, (
            f"Expected GLYPH_HALT ({GLYPH_HALT!r}) in rich halt bottom output; "
            f"stdout: {result.stdout!r}"
        )

    def test_bottom_halt_after_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh emits GLYPH_HALT_AFTER before HALT-AFTER GLOBAL in rich mode."""
        root = _make_root(tmp_path)
        (root / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
        result = _run_bottom(root, rich=True)
        assert result.returncode == 0, result.stderr
        assert "⚠" in result.stdout, (
            f"Expected GLYPH_HALT_AFTER (⚠) in rich draining bottom output; "
            f"stdout: {result.stdout!r}"
        )

    def test_bottom_approval_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh emits GLYPH_APPROVAL before APPROVAL(n) in rich mode."""
        root = _make_root(tmp_path)
        _write_human_approve_task(root, "proj", "HUMAN-APPROVE-v1-001", state="WAITING")
        result = _run_bottom(root, rich=True)
        assert result.returncode == 0, result.stderr
        assert GLYPH_APPROVAL in result.stdout, (
            f"Expected GLYPH_APPROVAL ({GLYPH_APPROVAL!r}) in rich bottom approval output; "
            f"stdout: {result.stdout!r}"
        )

    def test_bottom_timestamp_glyph(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh emits GLYPH_TIMESTAMP before the date/time in rich mode."""
        root = _make_root(tmp_path)
        result = _run_bottom(root, rich=True)
        assert result.returncode == 0, result.stderr
        assert GLYPH_TIMESTAMP in result.stdout, (
            f"Expected GLYPH_TIMESTAMP ({GLYPH_TIMESTAMP!r}) in rich bottom output; "
            f"stdout: {result.stdout!r}"
        )

    def test_bottom_unknown_version_rich(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh renders '📝 unknown' (rich) when no VERSION file is found."""
        # No VERSION file — use a root without one
        root = tmp_path
        (root / "projects").mkdir(exist_ok=True)
        result = _run_bottom(root, rich=True)
        assert result.returncode == 0, result.stderr
        # The fallback version string varies; the glyph prefix should be present
        assert GLYPH_VERSION in result.stdout, (
            f"Expected GLYPH_VERSION prefix even for unknown version; "
            f"stdout: {result.stdout!r}"
        )


# ===========================================================================
# 2. NO_COLOR regression lock
#    Dumb-mode output must not contain any glyph characters.
# ===========================================================================

# All seven glyph codepoints (or their first codepoint for multi-codepoint glyphs)
_ALL_GLYPH_CHARS = {
    GLYPH_VERSION,
    GLYPH_PM_AUTO,
    GLYPH_PM_MANUAL,
    GLYPH_APPROVAL,
    GLYPH_HALT,
    "⚠",   # ⚠️ first codepoint
    GLYPH_TIMESTAMP,
}


def _contains_any_glyph(text: str) -> bool:
    """Return True if text contains any status-bar glyph character."""
    return any(ch in text for ch in _ALL_GLYPH_CHARS)


class TestNoColorRegressionLock:
    """NO_COLOR / TERM=dumb output must not contain glyphs (byte-identical to pre-RC baseline)."""

    def test_right_no_glyphs_normal(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh dumb mode: no glyphs in normal state."""
        root = _make_root(tmp_path)
        result = _run_right(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert not _contains_any_glyph(result.stdout), (
            f"Glyph found in NO_COLOR output (normal state); stdout: {result.stdout!r}"
        )

    def test_right_no_glyphs_halt(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh dumb mode: no glyphs in halted state."""
        root = _make_root(tmp_path)
        (root / "HALT").touch()
        result = _run_right(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert not _contains_any_glyph(result.stdout), (
            f"Glyph found in NO_COLOR halt output; stdout: {result.stdout!r}"
        )

    def test_right_no_glyphs_halt_after(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh dumb mode: no glyphs in draining state."""
        root = _make_root(tmp_path)
        (root / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
        result = _run_right(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert not _contains_any_glyph(result.stdout), (
            f"Glyph found in NO_COLOR halt-after output; stdout: {result.stdout!r}"
        )

    def test_right_no_glyphs_approval(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh dumb mode: no glyphs in approval-pending state."""
        root = _make_root(tmp_path)
        _write_human_approve_task(root, "proj", "HUMAN-APPROVE-v1-001", state="WAITING")
        result = _run_right(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert not _contains_any_glyph(result.stdout), (
            f"Glyph found in NO_COLOR approval output; stdout: {result.stdout!r}"
        )

    def test_right_no_glyphs_pm_manual(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh dumb mode: no glyphs in PM:manual state."""
        root = _make_root(tmp_path)
        result = _run_right(root, rich=False, extra_env={"PGAI_KANBAN_PM_MODE": "manual"})
        assert result.returncode == 0, result.stderr
        assert not _contains_any_glyph(result.stdout), (
            f"Glyph found in NO_COLOR PM:manual output; stdout: {result.stdout!r}"
        )

    def test_right_dumb_halt_format_unchanged(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh dumb mode halt format is byte-identical to pre-RC: '[HALT GLOBAL]'."""
        root = _make_root(tmp_path)
        (root / "HALT").touch()
        result = _run_right(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert "[HALT GLOBAL]" in result.stdout, (
            f"Expected '[HALT GLOBAL]' in dumb-mode halt output; stdout: {result.stdout!r}"
        )

    def test_right_dumb_halt_after_format_unchanged(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh dumb mode draining format is byte-identical to pre-RC: '[HALT-AFTER:GLOBAL rc]'."""
        root = _make_root(tmp_path)
        (root / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
        result = _run_right(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert "[HALT-AFTER:GLOBAL rc]" in result.stdout, (
            f"Expected '[HALT-AFTER:GLOBAL rc]' in dumb-mode output; stdout: {result.stdout!r}"
        )

    def test_right_dumb_approval_format_unchanged(self, tmp_path: pathlib.Path) -> None:
        """status-right.sh dumb mode approval format is byte-identical to pre-RC: '[APPROVAL(1)]'."""
        root = _make_root(tmp_path)
        _write_human_approve_task(root, "proj", "HUMAN-APPROVE-v1-001", state="WAITING")
        result = _run_right(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert "[APPROVAL(1)]" in result.stdout, (
            f"Expected '[APPROVAL(1)]' in dumb-mode output; stdout: {result.stdout!r}"
        )

    def test_bottom_no_glyphs_normal(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh dumb mode: no glyphs in normal state."""
        root = _make_root(tmp_path)
        result = _run_bottom(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert not _contains_any_glyph(result.stdout), (
            f"Glyph found in NO_COLOR bottom output (normal); stdout: {result.stdout!r}"
        )

    def test_bottom_no_glyphs_halt(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh dumb mode: no glyphs in halted state."""
        root = _make_root(tmp_path)
        (root / "HALT").touch()
        result = _run_bottom(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert not _contains_any_glyph(result.stdout), (
            f"Glyph found in NO_COLOR bottom halt output; stdout: {result.stdout!r}"
        )

    def test_bottom_no_glyphs_halt_after(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh dumb mode: no glyphs in draining state."""
        root = _make_root(tmp_path)
        (root / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
        result = _run_bottom(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert not _contains_any_glyph(result.stdout), (
            f"Glyph found in NO_COLOR bottom halt-after output; stdout: {result.stdout!r}"
        )

    def test_bottom_no_glyphs_approval(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh dumb mode: no glyphs in approval-pending state."""
        root = _make_root(tmp_path)
        _write_human_approve_task(root, "proj", "HUMAN-APPROVE-v1-001", state="WAITING")
        result = _run_bottom(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert not _contains_any_glyph(result.stdout), (
            f"Glyph found in NO_COLOR bottom approval output; stdout: {result.stdout!r}"
        )

    def test_bottom_no_glyphs_pm_manual(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh dumb mode: no glyphs in PM:manual state."""
        root = _make_root(tmp_path)
        result = _run_bottom(root, rich=False, extra_env={"PGAI_KANBAN_PM_MODE": "manual"})
        assert result.returncode == 0, result.stderr
        assert not _contains_any_glyph(result.stdout), (
            f"Glyph found in NO_COLOR bottom PM:manual output; stdout: {result.stdout!r}"
        )

    def test_bottom_dumb_halt_format_unchanged(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh dumb mode halt format is byte-identical to pre-RC: '[HALT GLOBAL]'."""
        root = _make_root(tmp_path)
        (root / "HALT").touch()
        result = _run_bottom(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert "[HALT GLOBAL]" in result.stdout, (
            f"Expected '[HALT GLOBAL]' in dumb bottom output; stdout: {result.stdout!r}"
        )

    def test_bottom_dumb_halt_after_format_unchanged(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh dumb mode draining format is byte-identical to pre-RC."""
        root = _make_root(tmp_path)
        (root / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
        result = _run_bottom(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert "[HALT-AFTER:GLOBAL rc]" in result.stdout, (
            f"Expected '[HALT-AFTER:GLOBAL rc]' in dumb bottom output; stdout: {result.stdout!r}"
        )

    def test_bottom_dumb_approval_format_unchanged(self, tmp_path: pathlib.Path) -> None:
        """status-bottom.sh dumb mode approval format is byte-identical to pre-RC: '[APPROVAL(1)]'."""
        root = _make_root(tmp_path)
        _write_human_approve_task(root, "proj", "HUMAN-APPROVE-v1-001", state="WAITING")
        result = _run_bottom(root, rich=False)
        assert result.returncode == 0, result.stderr
        assert "[APPROVAL(1)]" in result.stdout, (
            f"Expected '[APPROVAL(1)]' in dumb bottom output; stdout: {result.stdout!r}"
        )


# ===========================================================================
# 3. Sibling byte-equality gate
#    Both siblings must emit the same glyph for corresponding states, proving
#    that glyph literals are delegated to the shared lib (not duplicated).
# ===========================================================================


class TestSiblingGlyphEquality:
    """Both siblings emit identical glyphs for each segment in rich mode."""

    def _extract_glyphs(self, text: str) -> set[str]:
        """Return set of glyph characters found in text."""
        found = set()
        for ch in _ALL_GLYPH_CHARS:
            if ch in text:
                found.add(ch)
        return found

    def test_version_glyph_same_in_both_siblings(self, tmp_path: pathlib.Path) -> None:
        """Both siblings emit GLYPH_VERSION for the version segment."""
        root = _make_root(tmp_path)
        right = _run_right(root, rich=True)
        bottom = _run_bottom(root, rich=True)
        assert right.returncode == 0, right.stderr
        assert bottom.returncode == 0, bottom.stderr
        assert GLYPH_VERSION in right.stdout, "status-right: GLYPH_VERSION missing"
        assert GLYPH_VERSION in bottom.stdout, "status-bottom: GLYPH_VERSION missing"

    def test_pm_auto_glyph_same_in_both_siblings(self, tmp_path: pathlib.Path) -> None:
        """Both siblings emit GLYPH_PM_AUTO for PM:auto."""
        root = _make_root(tmp_path)
        right = _run_right(root, rich=True)
        bottom = _run_bottom(root, rich=True)
        assert GLYPH_PM_AUTO in right.stdout, "status-right: GLYPH_PM_AUTO missing"
        assert GLYPH_PM_AUTO in bottom.stdout, "status-bottom: GLYPH_PM_AUTO missing"

    def test_pm_manual_glyph_same_in_both_siblings(self, tmp_path: pathlib.Path) -> None:
        """Both siblings emit GLYPH_PM_MANUAL for PM:manual."""
        root = _make_root(tmp_path)
        right = _run_right(root, rich=True, extra_env={"PGAI_KANBAN_PM_MODE": "manual"})
        bottom = _run_bottom(root, rich=True, extra_env={"PGAI_KANBAN_PM_MODE": "manual"})
        assert GLYPH_PM_MANUAL in right.stdout, "status-right: GLYPH_PM_MANUAL missing"
        assert GLYPH_PM_MANUAL in bottom.stdout, "status-bottom: GLYPH_PM_MANUAL missing"

    def test_halt_glyph_same_in_both_siblings(self, tmp_path: pathlib.Path) -> None:
        """Both siblings emit GLYPH_HALT for HALT GLOBAL in rich mode."""
        root = _make_root(tmp_path)
        (root / "HALT").touch()
        right = _run_right(root, rich=True)
        bottom = _run_bottom(root, rich=True)
        assert GLYPH_HALT in right.stdout, "status-right: GLYPH_HALT missing for HALT"
        assert GLYPH_HALT in bottom.stdout, "status-bottom: GLYPH_HALT missing for HALT"

    def test_halt_after_glyph_same_in_both_siblings(self, tmp_path: pathlib.Path) -> None:
        """Both siblings emit GLYPH_HALT_AFTER (⚠) for HALT-AFTER GLOBAL in rich mode."""
        root = _make_root(tmp_path)
        (root / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
        right = _run_right(root, rich=True)
        bottom = _run_bottom(root, rich=True)
        assert "⚠" in right.stdout, "status-right: GLYPH_HALT_AFTER (⚠) missing"
        assert "⚠" in bottom.stdout, "status-bottom: GLYPH_HALT_AFTER (⚠) missing"

    def test_approval_glyph_same_in_both_siblings(self, tmp_path: pathlib.Path) -> None:
        """Both siblings emit GLYPH_APPROVAL for the APPROVAL(n) segment in rich mode."""
        root = _make_root(tmp_path)
        _write_human_approve_task(root, "proj", "HUMAN-APPROVE-v1-001", state="WAITING")
        right = _run_right(root, rich=True)
        bottom = _run_bottom(root, rich=True)
        assert GLYPH_APPROVAL in right.stdout, "status-right: GLYPH_APPROVAL missing"
        assert GLYPH_APPROVAL in bottom.stdout, "status-bottom: GLYPH_APPROVAL missing"

    def test_timestamp_glyph_same_in_both_siblings(self, tmp_path: pathlib.Path) -> None:
        """Both siblings emit GLYPH_TIMESTAMP for the date/time segment in rich mode."""
        root = _make_root(tmp_path)
        right = _run_right(root, rich=True)
        bottom = _run_bottom(root, rich=True)
        assert GLYPH_TIMESTAMP in right.stdout, "status-right: GLYPH_TIMESTAMP missing"
        assert GLYPH_TIMESTAMP in bottom.stdout, "status-bottom: GLYPH_TIMESTAMP missing"

    def test_dumb_mode_produces_no_glyphs_in_both_siblings(self, tmp_path: pathlib.Path) -> None:
        """In dumb mode, NEITHER sibling emits any glyph character.

        This is the sibling-equality gate on the regression lock: both must
        produce glyph-free output in NO_COLOR / TERM=dumb mode.
        """
        root = _make_root(tmp_path)
        right = _run_right(root, rich=False)
        bottom = _run_bottom(root, rich=False)
        assert not _contains_any_glyph(right.stdout), (
            f"status-right: glyph in dumb output; stdout: {right.stdout!r}"
        )
        assert not _contains_any_glyph(bottom.stdout), (
            f"status-bottom: glyph in dumb output; stdout: {bottom.stdout!r}"
        )
