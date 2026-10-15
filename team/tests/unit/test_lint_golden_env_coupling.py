"""
test_lint_golden_env_coupling.py
=================================
Tests for the Anti-pattern 7 (golden comparison + inherited subprocess
environment) check in team/scripts/lint_test_anti_patterns.py.

The pairing this check targets is narrower than either half alone: most of
the tree's inherited-environment call sites are correct as written, and
"golden" is also idiomatic shorthand for "the happy path" in test names
unrelated to any committed-fixture comparison. The check must fire only
when a file both compares against a committed golden AND builds its
subprocess environment by inheritance -- proven here in both directions:

  1. A fixture carrying the exact pairing produces exactly one finding,
     naming the fixture file and the line where the environment is built.
  2. A fixture that inherits its environment but performs no golden
     comparison produces zero findings.
  3. A fixture that compares against a golden with an explicit,
     rebuilt environment produces zero findings.
  4. The real, shipped team/tests/ tree produces zero AP7 findings --
     regression proof that the pairing does not exist anywhere real today.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

# ---------------------------------------------------------------------------
# Load lint_test_anti_patterns.py as a module (mirrors sibling
# test_lint_ap2_bare_tmp_construct_forms.py's import-by-path pattern).
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
# Case 1 — the positive pairing fires exactly once, naming file and line
# ---------------------------------------------------------------------------


def test_golden_comparison_with_inherited_env_is_flagged_once() -> None:
    path, lines = _read_fixture_lines("fixture_golden_comparison_inherited_env.py")
    findings = _lint._check_ap7(path, lines)

    assert len(findings) == 1, findings

    finding = findings[0]
    assert "[AP7]" in finding
    assert str(path) in finding

    # The finding must name the exact line the inherited-environment
    # construct sits on, not merely somewhere in the file.
    env_lineno = next(
        idx + 1 for idx, line in enumerate(lines) if "dict(os.environ)" in line
    )
    assert f"{path}:{env_lineno}:" in finding, (
        f"expected finding to cite line {env_lineno}: {finding}"
    )


# ---------------------------------------------------------------------------
# Case 2 — inherited environment alone, no golden comparison: silent
# ---------------------------------------------------------------------------


def test_inherited_env_without_golden_comparison_is_silent() -> None:
    path, lines = _read_fixture_lines(
        "fixture_inherited_env_without_golden_comparison.py"
    )
    findings = _lint._check_ap7(path, lines)
    assert findings == [], findings


# ---------------------------------------------------------------------------
# Case 3 — golden comparison with an explicit, rebuilt environment: silent
# ---------------------------------------------------------------------------


def test_golden_comparison_with_explicit_env_is_silent() -> None:
    path, lines = _read_fixture_lines("fixture_golden_comparison_explicit_env.py")
    findings = _lint._check_ap7(path, lines)
    assert findings == [], findings


# ---------------------------------------------------------------------------
# Case 4 — real-tree regression: the shipped test suite is clean
# ---------------------------------------------------------------------------


def test_real_tree_has_zero_ap7_findings() -> None:
    """No file under team/tests/ (excluding fixtures/) carries the pairing.

    This is the same regression proof
    test_real_litter_check_module_has_zero_ap2_findings makes for AP2,
    generalised to every test file rather than one named module: the two
    golden-comparison harnesses this check exists to guard were converted
    to an explicit environment builder, and nothing else in the tree
    combines a golden-comparison marker with an inherited environment.
    """
    all_findings: list[str] = []
    for path in sorted(_TEAM_DIR.rglob("test_*.py")):
        if "__pycache__" in path.parts or "fixtures" in path.parts:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
        all_findings.extend(_lint._check_ap7(path, lines))

    assert all_findings == [], (
        "Unexpected AP7 findings in the shipped test tree:\n"
        + "\n".join(all_findings)
    )
