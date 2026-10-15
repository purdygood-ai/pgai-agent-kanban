"""
test_lint_raw_marker_read.py
=============================
Tests for team/scripts/lint_raw_marker_read.py — the structural gate that
flags a queue-marker character being extracted from a backlog line and used
to make a decision, inside the discovery pipeline's idle/RC-in-flight
decision surface (team/scripts/lib/discovery_guards.sh,
team/scripts/lib/discovery.sh).

Test coverage:

  1. Real-tree green — the shipped discovery_guards.sh and discovery.sh
     produce zero findings (proof the rewritten gate functions no longer
     read a raw marker to decide).
  2. A synthetic raw-marker-read planted in a fixture file is detected, in
     both of its known shapes (capture-group extraction, literal/negated
     single-char test).
  3. A legitimate marker-line parse — the generic, uncaptured
     ``[^]]*`` skip form used to extract a task ID past the marker field —
     is NOT flagged.
  4. A plain-prose comment naming marker shapes (``[x]``, ``[W]``, even an
     escaped-looking ``\\[x\\]`` inside a comment) is NOT flagged — only the
     literal backslash-escaped regex-source shape trips the scan.
  5. main() return codes: 0 clean, 1 violation, 2 missing scan path.
  6. --help exits 0.
  7. Remedy block names the offending file:line and the enforced rule.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Load lint_raw_marker_read.py as a module (mirrors sibling tests' import-by-
# path pattern, e.g. test_lint_dashboard_network_git_gate.py).
# Path: team/tests/unit/ -> team/ (two levels up)
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_raw_marker_read.py"
_REAL_DISCOVERY_GUARDS = _SCRIPTS_DIR / "lib" / "discovery_guards.sh"
_REAL_DISCOVERY_SH = _SCRIPTS_DIR / "lib" / "discovery.sh"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_raw_marker_read", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


def _write_sh(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Case 1 — Real tree is clean
# ---------------------------------------------------------------------------


def test_real_discovery_decision_files_have_zero_findings() -> None:
    """The shipped discovery_guards.sh and discovery.sh produce zero findings.

    If this test fails, a raw queue-marker read was reintroduced into the
    discovery pipeline's idle/RC-in-flight decision surface — exactly the
    regression class this lint exists to catch.
    """
    violations = _lint.scan_paths([_REAL_DISCOVERY_GUARDS, _REAL_DISCOVERY_SH])
    assert violations == [], (
        "Unexpected raw-marker-read finding(s) on the real discovery decision "
        "files:\n" + "\n".join(f"  {v.path}:{v.line} ({v.kind}) {v.snippet}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Case 2 — Planted violations are detected
# ---------------------------------------------------------------------------


def test_capture_group_marker_extraction_is_flagged(tmp_path: Path) -> None:
    """A sed pattern that captures the marker's own content directly out of
    the brackets is flagged (the exact shape the prior chain-idle
    implementation used to extract the tail marker)."""
    fixture = _write_sh(
        tmp_path / "planted_capture.sh",
        r"""
        #!/usr/bin/env bash
        _tail_marker="$(echo "$_tail_line" | sed -E 's/^\s*-\s+\[([^]]*)\]\s+.*/\1/')"
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations, "Expected a finding for a capture-group marker extraction."
    assert violations[0].path == fixture
    assert "captured" in violations[0].kind


def test_literal_single_char_marker_test_is_flagged(tmp_path: Path) -> None:
    """A grep pattern testing the bracket content against a literal marker
    value (e.g. matching exactly '[x]') is flagged."""
    fixture = _write_sh(
        tmp_path / "planted_literal.sh",
        r"""
        #!/usr/bin/env bash
        if echo "$line" | grep -qE '^\s*-\s+\[x\]'; then
            echo "done"
        fi
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations, "Expected a finding for a literal single-char marker test."
    assert violations[0].path == fixture
    assert "tested" in violations[0].kind


def test_negated_char_class_marker_test_is_flagged(tmp_path: Path) -> None:
    """A grep pattern testing the bracket content against a negated class
    (e.g. 'anything but x or X') is flagged — the pm-ticket-in-flight shape."""
    fixture = _write_sh(
        tmp_path / "planted_negated.sh",
        r"""
        #!/usr/bin/env bash
        if grep -qE '^\s*-\s+\[([^xX])\]\s+\S.*decompose-v[0-9]' "$pm_backlog_file"; then
            return 0
        fi
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations, "Expected a finding for a negated-char-class marker test."
    assert violations[0].path == fixture


def test_wildcard_single_char_marker_test_is_flagged(tmp_path: Path) -> None:
    """A grep pattern matching any single bracket-enclosed char (the old
    chain-idle tail-line selector, '\\[.\\]') is flagged."""
    fixture = _write_sh(
        tmp_path / "planted_wildcard.sh",
        r"""
        #!/usr/bin/env bash
        _tail_line="$(grep -E '^\s*-\s+\[.\]\s+\S' "$_backlog_file" | tail -n1)"
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations, "Expected a finding for a single-char wildcard marker test."
    assert violations[0].path == fixture


# ---------------------------------------------------------------------------
# Case 3 — Legitimate task-ID extraction is NOT flagged
# ---------------------------------------------------------------------------


def test_generic_skip_pattern_for_task_id_extraction_is_not_flagged(tmp_path: Path) -> None:
    """The generic, uncaptured '[^]]*' skip form used to extract a task ID
    past the marker field (never the marker's own content) is not a
    marker-content read and must not be flagged."""
    fixture = _write_sh(
        tmp_path / "legit_extraction.sh",
        r"""
        #!/usr/bin/env bash
        while IFS= read -r _task_id; do
            :
        done < <(grep -E '^\s*-\s+\[[^]]*\]\s+\S' "$_backlog_file" | sed -E 's/^\s*-\s+\[[^]]*\]\s+([^[:space:]]+).*/\1/')
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations == [], (
        f"Legitimate task-ID extraction pattern was incorrectly flagged: {violations}"
    )


# ---------------------------------------------------------------------------
# Case 4 — Comment-only mention is exempt
# ---------------------------------------------------------------------------


def test_comment_only_mention_is_not_flagged(tmp_path: Path) -> None:
    """Prose describing marker shapes in a comment — including an
    escaped-looking form — does not trigger a violation; only an actual
    pattern literal does."""
    fixture = _write_sh(
        tmp_path / "documents_markers.sh",
        r"""
        #!/usr/bin/env bash
        # A marker looks like [x], [ ], [W], [B], or [A]. Even an escaped
        # form written in prose, like \[x\] or \[.\] or \[([^xX])\], must
        # never trip this scan -- it is documentation, not a pattern literal.
        echo "no real pattern here"
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations == [], (
        f"Comment-only mention of marker shapes was incorrectly flagged: {violations}"
    )


# ---------------------------------------------------------------------------
# Case 5 — main() return codes
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_clean_paths(tmp_path: Path) -> None:
    fixture = _write_sh(
        tmp_path / "clean.sh",
        r"""
        #!/usr/bin/env bash
        echo "nothing to see here"
        """,
    )
    rc = _lint.main(["--paths", str(fixture)])
    assert rc == 0, f"Expected main() to return 0 on a clean file, got {rc}"


def test_main_returns_one_on_violation(tmp_path: Path) -> None:
    fixture = _write_sh(
        tmp_path / "violator.sh",
        r"""
        #!/usr/bin/env bash
        if echo "$line" | grep -qE '^\s*-\s+\[x\]'; then
            echo "done"
        fi
        """,
    )
    rc = _lint.main(["--paths", str(fixture)])
    assert rc == 1, f"Expected main() to return 1 on a violation, got {rc}"


def test_main_returns_two_on_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.sh"
    rc = _lint.main(["--paths", str(missing)])
    assert rc == 2, f"Expected main() to return 2 for a missing scan path, got {rc}"


def test_main_returns_two_on_missing_default_scope_file(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    (team_dir / "scripts" / "lib").mkdir(parents=True, exist_ok=True)
    rc = _lint.main(["--team-dir", str(team_dir)])
    assert rc == 2, (
        f"Expected main() to return 2 when the default scope files are absent, got {rc}"
    )


# ---------------------------------------------------------------------------
# Case 6 — --help exits 0
# ---------------------------------------------------------------------------


def test_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"--help exited with non-zero code {result.returncode}.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "lint_raw_marker_read" in result.stdout


# ---------------------------------------------------------------------------
# Case 7 — Remedy block names the offending location and the rule
# ---------------------------------------------------------------------------


def test_remedy_block_names_file_line_and_rule(tmp_path, capsys) -> None:
    fixture = _write_sh(
        tmp_path / "violator.sh",
        r"""
        #!/usr/bin/env bash
        if echo "$line" | grep -qE '^\s*-\s+\[x\]'; then
            echo "done"
        fi
        """,
    )

    rc = _lint.main(["--paths", str(fixture)])
    assert rc == 1

    out = capsys.readouterr().out
    assert "Cause(s):" in out
    assert str(fixture) in out
    assert "status.md" in out
    assert "Rule:" in out
