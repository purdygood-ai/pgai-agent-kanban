"""
test_lint_abs_path_substring_assertion.py
==========================================
Tests for the Anti-pattern 8 (uncontrolled absolute-path substring
assertion) check in team/scripts/lint_test_anti_patterns.py.

The check targets a narrow literal shape -- a hardcoded string that both
starts and ends with "/" (a path-segment token like "/projects/") searched
as a substring of an absolute path via the ``in`` / ``not in`` containment
operator -- because that literal's surrounding prefix is the ambient
scratch root the suite happens to run under, not something the test
controls. Proven here in both directions:

  1. A fixture carrying an uncontrolled absolute-path substring assertion
     produces exactly one finding, naming the fixture file and the line
     where the assertion sits.
  2. A fixture that asserts on absolute paths built from a root the test
     itself computed (``.startswith(expected_prefix)``,
     ``.relative_to(root).parts``) produces zero findings.
  3. The real, shipped team/tests/ tree produces zero AP8 findings --
     regression proof that every absolute-path assertion in the tree is
     compared against a computed value, never a free-floating literal.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

# ---------------------------------------------------------------------------
# Load lint_test_anti_patterns.py as a module (mirrors sibling
# test_lint_golden_env_coupling.py's import-by-path pattern).
# Path: team/tests/unit/ -> team/ (two levels up)
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_test_anti_patterns.py"
_FIXTURES_DIR = _TEAM_DIR / "tests" / "fixtures"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location(
        "lint_test_anti_patterns", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


def _read_fixture_lines(name: str) -> tuple[Path, list[str]]:
    path = _FIXTURES_DIR / name
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return path, lines


# ---------------------------------------------------------------------------
# Case 1 — the uncontrolled substring shape fires exactly once, naming
# file and line
# ---------------------------------------------------------------------------


def test_uncontrolled_abs_path_substring_is_flagged_once() -> None:
    path, lines = _read_fixture_lines("fixture_abs_path_substring_assertion.py")
    findings = _lint._check_ap8(path, lines)

    assert len(findings) == 1, findings

    finding = findings[0]
    assert "[AP8]" in finding
    assert str(path) in finding

    # The finding must name the exact line the uncontrolled substring
    # assertion sits on, not merely somewhere in the file.
    assertion_lineno = next(
        idx + 1 for idx, line in enumerate(lines) if '"/projects/" not in' in line
    )
    assert f"{path}:{assertion_lineno}:" in finding, (
        f"expected finding to cite line {assertion_lineno}: {finding}"
    )


# ---------------------------------------------------------------------------
# Case 2 — controlled, computed-prefix assertions: silent
# ---------------------------------------------------------------------------


def test_controlled_prefix_assertion_is_silent() -> None:
    path, lines = _read_fixture_lines("fixture_abs_path_prefix_controlled.py")
    findings = _lint._check_ap8(path, lines)
    assert findings == [], findings


# ---------------------------------------------------------------------------
# Case 3 — real-tree regression: the shipped test suite is clean
# ---------------------------------------------------------------------------


def test_real_tree_has_zero_ap8_findings() -> None:
    """No file under team/tests/ (excluding fixtures/) carries the shape.

    Same regression proof test_real_tree_has_zero_ap7_findings makes for
    AP7: team/tests/unit/test_debug_log_routing.py asserts on a
    kanban_root-relative value instead of an absolute-path substring, and
    nothing else in the tree carries a literal that both opens and closes
    with "/" inside a containment assertion.
    """
    all_findings: list[str] = []
    for path in sorted(_TEAM_DIR.rglob("test_*.py")):
        if "__pycache__" in path.parts or "fixtures" in path.parts:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
        all_findings.extend(_lint._check_ap8(path, lines))

    assert all_findings == [], (
        "Unexpected AP8 findings in the shipped test tree:\n"
        + "\n".join(all_findings)
    )
