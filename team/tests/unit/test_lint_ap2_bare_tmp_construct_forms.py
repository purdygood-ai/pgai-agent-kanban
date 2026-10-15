"""
test_lint_ap2_bare_tmp_construct_forms.py
==========================================
Tests for the Anti-pattern 2 (environment-coupled fixtures) checks in
team/scripts/lint_test_anti_patterns.py, covering two additions:

  1. String-prefix coverage — Path(), open(), os.makedirs(), and a bare
     variable assignment all still flag a bare-/tmp literal when the string
     carries a Python prefix (f, r, b, u, or a two-letter combination such
     as rb/fr).  Before this coverage, a bare-/tmp literal escaped detection
     entirely as soon as it was written as an f-string — a scan gap that let
     an f-string-wrapped bare-/tmp Path() call slip past the linter unnoticed.

  2. The bare-/tmp subject-matter whitelist — a narrow, filename-scoped
     allowance (as opposed to a per-instance opt-out comment) for files that
     behaviorally test a real bare-/tmp scan and cannot route the path they
     plant through PGAI_AGENT_KANBAN_TEMP_DIR without testing nothing. The
     allowance exempts ONLY the Path()/open() constructor checks for the
     named file; every other AP2 pattern still applies to it.

Test coverage:

  1. An f-string-wrapped bare-/tmp Path() call is flagged (gap closed).
  2. An f-string-wrapped bare-/tmp open() call is flagged (gap closed).
  3. An f-string-wrapped bare-/tmp os.makedirs() call is flagged (gap closed).
  4. A bare variable assignment using an f-string is flagged.
  5. The plain (non-prefixed) string forms are still flagged — the
     string-prefix support is additive, not a replacement.
  6. A file on the bare-/tmp subject-matter whitelist is NOT flagged for its
     Path() constructor call...
  7. ...but the SAME whitelisted file IS still flagged for an unrelated AP2
     pattern (a bare variable assignment) — proving the allowance is narrow,
     not a blanket exemption for the whole file.
  8. The real, shipped test_wake_bracket_litter_check.py produces zero AP2
     findings — regression proof that the file's one remaining bare-/tmp
     construct lives inside the allowed helper and nowhere else.

Every synthetic bad-pattern line below is assembled via string concatenation
(mirroring team/tests/fixtures/fixture_hardcoded_tmp_and_mkdtemp.py's own
convention) so that THIS file's own source text never contains a
contiguous, scannable copy of the pattern it is proving the linter catches.
Without that, this file would itself trip the very lint it tests when the
real CI run scans team/tests/.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

# ---------------------------------------------------------------------------
# Load lint_test_anti_patterns.py as a module (mirrors sibling
# test_lint_dashboard_hardcode_colors.py's import-by-path pattern).
# Path: team/tests/unit/ -> team/ (two levels up)
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_test_anti_patterns.py"


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

# Bare-/tmp prefix, assembled via concatenation — see module docstring for why.
_TMP = "/" + "tmp" + "/"


def _bad_line(template: str) -> str:
    """Fill {tmp} in *template* with the bare-/tmp prefix and append a newline.

    Keeping the substitution here (rather than an f-string literal at each
    call site) is what keeps this file's own source free of a contiguous,
    scannable "/tmp/" following a Path(/open(/makedirs(/assignment token.
    """
    return template.replace("{tmp}", _TMP) + "\n"


# ---------------------------------------------------------------------------
# Cases 1-4 — string-prefix coverage (the f-string scan gap)
# ---------------------------------------------------------------------------


def test_f_string_path_constructor_is_flagged() -> None:
    lines = [_bad_line('probe = pathlib.Path(f"{tmp}{name}")')]
    findings = _lint._check_ap2_py(Path("synthetic_caller.py"), lines)
    assert len(findings) == 1, findings
    assert "[AP2]" in findings[0]
    assert "Path() constructor" in findings[0]


def test_f_string_open_call_is_flagged() -> None:
    lines = [_bad_line('handle = open(f"{tmp}{name}", "w")')]
    findings = _lint._check_ap2_py(Path("synthetic_caller.py"), lines)
    assert len(findings) == 1, findings
    assert "open() call" in findings[0]


def test_f_string_makedirs_is_flagged() -> None:
    lines = [_bad_line('os.makedirs(f"{tmp}{name}")')]
    findings = _lint._check_ap2_py(Path("synthetic_caller.py"), lines)
    assert len(findings) == 1, findings
    assert "os.makedirs/os.mkdir" in findings[0]


def test_f_string_var_assign_is_flagged() -> None:
    lines = [_bad_line('target = f"{tmp}{name}"')]
    findings = _lint._check_ap2_py(Path("synthetic_caller.py"), lines)
    assert len(findings) == 1, findings
    assert "assigned to variable" in findings[0]


# ---------------------------------------------------------------------------
# Case 5 — plain (non-prefixed) string forms remain flagged
# ---------------------------------------------------------------------------


def test_plain_string_forms_still_flagged() -> None:
    lines = [
        _bad_line('a = pathlib.Path("{tmp}plain")'),
        _bad_line('b = open("{tmp}plain2", "w")'),
        _bad_line('os.makedirs("{tmp}plain3")'),
        _bad_line('c = "{tmp}plain4"'),
    ]
    findings = _lint._check_ap2_py(Path("synthetic_caller.py"), lines)
    assert len(findings) == 4, findings


# ---------------------------------------------------------------------------
# Cases 6/7 — the bare-/tmp subject-matter whitelist is narrow
# ---------------------------------------------------------------------------


def test_bare_tmp_subject_matter_whitelist_exempts_path_constructor_only() -> None:
    """A whitelisted filename's Path()/open() bare-/tmp calls are exempt."""
    lines = [
        _bad_line('probe = pathlib.Path(f"{tmp}{prefix}-{suffix}")'),
        _bad_line('handle = open("{tmp}other", "w")'),
    ]
    findings = _lint._check_ap2_py(
        Path("test_wake_bracket_litter_check.py"), lines
    )
    assert findings == [], findings


def test_bare_tmp_subject_matter_whitelist_still_flags_other_ap2_patterns() -> None:
    """The SAME whitelisted filename is still flagged for a non-constructor
    AP2 pattern — proving the allowance is scoped to Path()/open(), not the
    whole file.
    """
    lines = [_bad_line('leftover = "{tmp}not_routed_through_the_helper"')]
    findings = _lint._check_ap2_py(
        Path("test_wake_bracket_litter_check.py"), lines
    )
    assert len(findings) == 1, findings
    assert "assigned to variable" in findings[0]


# ---------------------------------------------------------------------------
# Case 8 — real-tree regression: the shipped litter-check module is clean
# ---------------------------------------------------------------------------


def test_real_litter_check_module_has_zero_ap2_findings() -> None:
    """The shipped test_wake_bracket_litter_check.py produces zero AP2
    findings: its one bare-/tmp constructor lives inside the allowed
    helper (covered by the whitelist above), and no other bare-/tmp
    construct exists anywhere else in the file.
    """
    real_file = (
        _TEAM_DIR / "tests" / "unit" / "test_wake_bracket_litter_check.py"
    )
    lines = real_file.read_text(encoding="utf-8").splitlines(keepends=True)
    findings = _lint._check_ap2_py(real_file, lines)
    assert findings == [], (
        "Unexpected AP2 findings in the shipped litter-check test module:\n"
        + "\n".join(findings)
    )
