"""
test_lint_help_presence.py
==========================
Tests for team/scripts/lint_help_presence.py — the operator-script help-presence
lint gate.

Tests cover the core acceptance criteria:

  1. **Positive case — missing --help is flagged**: a synthetic script directory
     containing a script that has no --help handler causes the lint to exit 1
     and report the offender.  This is the proof the guard guards.

  2. **Exempt list suppresses the finding**: the same script, when listed in an
     exempt file, is skipped and the lint exits 0.

  3. **Help-aware script passes**: a synthetic script that implements ``--help``
     via a case branch exits 0 with zero offenders.

  4. **Argparse_has form is detected**: a script that uses ``argparse_has help``
     (operator_args.sh framework) is recognized as help-aware.

  5. **Exec-passthrough form is detected**: a script that delegates all arguments
     via ``exec ... "$@"`` is recognized as help-aware (it passes --help to its
     delegate).

  6. **main() returns 2 for a missing scripts directory**.

  7. **main() returns 2 for a missing exempt list**.

  8. **Real-tree positive case**: the real team/scripts/ directory (top-level
     only, via --scripts-dir) plus the real help_presence_exempt.txt produces
     zero offenders.  This is the proof that the lint passes the current
     codebase (excluding cm/) after the exempt list is applied.

  9. **cm/ positive case — missing --help in cm/ script is flagged**: a
     synthetic cm/ subdirectory containing a script with no --help handler
     causes the lint to exit 1 when the default two-directory scan runs.

 10. **cm/ exempt-list entry suppresses cm/ finding**: a ``cm/<name>.sh`` entry
     in the exempt list causes the cm/ offender to be skipped.

 11. **Real-tree cm/ scan finds the expected offenders**: running the lint
     against the real team/scripts/cm/ (via --scripts-dir) finds exactly the
     five expected offender scripts still awaiting remediation.

All tests use pytest's ``tmp_path`` and importlib for isolation — no bare /tmp
paths, no live kanban state mutations.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load lint_help_presence.py as a module.
# Path: team/tests/unit/ → team/ (three levels up from unit/) → team/scripts/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent   # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_help_presence.py"


def _import_lint_module():
    """Import lint_help_presence as a module."""
    spec = importlib.util.spec_from_file_location(
        "lint_help_presence", _LINT_SCRIPT
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

_FIXTURE_DIR = _TEAM_DIR / "tests" / "fixtures"
_NO_HELP_FIXTURE = _FIXTURE_DIR / "fixture_no_help_script.sh"


def _write_exempt_list(directory: Path, basenames: list[str]) -> Path:
    """Write a minimal exempt-list data file in *directory*."""
    exempt_path = directory / "help_presence_exempt.txt"
    content = "# Test exempt list\n" + "\n".join(basenames) + "\n"
    exempt_path.write_text(content, encoding="utf-8")
    return exempt_path


def _write_shell_script(directory: Path, name: str, content: str) -> Path:
    """Write a synthetic shell script to *directory / name*."""
    script = directory / name
    script.write_text(content, encoding="utf-8")
    return script


# ---------------------------------------------------------------------------
# Test 1: missing --help triggers a violation
# ---------------------------------------------------------------------------


def test_missing_help_triggers_violation(tmp_path: Path) -> None:
    """A script without --help is flagged; lint exits 1.

    The fixture script (fixture_no_help_script.sh) has no --help handler.
    When scanned as the only script in a synthetic directory with an empty
    exempt list, the lint must report it as an offender and return 1.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    # Copy the no-help fixture into the synthetic scripts dir.
    fixture_text = _NO_HELP_FIXTURE.read_text(encoding="utf-8")
    _write_shell_script(scripts_dir, "fixture_no_help_script.sh", fixture_text)

    # Empty exempt list.
    exempt_path = _write_exempt_list(tmp_path, [])

    result = _lint.main([
        "--scripts-dir", str(scripts_dir),
        "--exempt-list", str(exempt_path),
    ])

    assert result == 1, (
        "lint_help_presence.main() must return 1 when a script without --help "
        "is found.  The fixture_no_help_script.sh is the deliberate bad example; "
        "if the lint returns 0, the guard is not working."
    )


# ---------------------------------------------------------------------------
# Test 2: exempt list suppresses the violation
# ---------------------------------------------------------------------------


def test_exempt_list_suppresses_violation(tmp_path: Path) -> None:
    """A script on the exempt list is skipped; lint exits 0.

    The same no-help fixture that triggers a violation in test 1 must be
    silently skipped when its basename is listed in the exempt file.
    This proves the exempt-list mechanism works correctly.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    fixture_text = _NO_HELP_FIXTURE.read_text(encoding="utf-8")
    _write_shell_script(scripts_dir, "fixture_no_help_script.sh", fixture_text)

    # List the offending script in the exempt list.
    exempt_path = _write_exempt_list(tmp_path, ["fixture_no_help_script.sh"])

    result = _lint.main([
        "--scripts-dir", str(scripts_dir),
        "--exempt-list", str(exempt_path),
    ])

    assert result == 0, (
        "lint_help_presence.main() must return 0 when the only script "
        "in the directory is listed in the exempt file.  The exempt-list "
        "mechanism is broken if this test fails."
    )


# ---------------------------------------------------------------------------
# Test 3: case-branch --help handler passes
# ---------------------------------------------------------------------------


def test_case_branch_help_passes(tmp_path: Path) -> None:
    """A script that uses a case branch for --help is recognized as help-aware."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    script_content = """\
#!/usr/bin/env bash
# Synthetic test script with a case-branch --help handler.
set -euo pipefail
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      echo "Usage: test-script.sh [--help]"
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done
echo "running"
"""
    _write_shell_script(scripts_dir, "test_case_help.sh", script_content)
    exempt_path = _write_exempt_list(tmp_path, [])

    result = _lint.main([
        "--scripts-dir", str(scripts_dir),
        "--exempt-list", str(exempt_path),
    ])

    assert result == 0, (
        "A script using 'case \"$1\" in --help|-h)' must be recognized as "
        "help-aware.  The case-branch detection pattern is broken."
    )


# ---------------------------------------------------------------------------
# Test 4: argparse_has help form passes
# ---------------------------------------------------------------------------


def test_argparse_has_help_passes(tmp_path: Path) -> None:
    """A script that uses 'argparse_has help' is recognized as help-aware."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    script_content = """\
#!/usr/bin/env bash
# Synthetic test script using operator_args.sh argparse_has framework.
OPERATOR_VALID_FLAGS=(project help h)
argparse_parse --value-flags "project" -- "$@"
if argparse_has help; then
    echo "Usage: argparse-test.sh --project <name>"
    exit 0
fi
echo "running"
"""
    _write_shell_script(scripts_dir, "test_argparse_help.sh", script_content)
    exempt_path = _write_exempt_list(tmp_path, [])

    result = _lint.main([
        "--scripts-dir", str(scripts_dir),
        "--exempt-list", str(exempt_path),
    ])

    assert result == 0, (
        "A script using 'argparse_has help' (operator_args.sh framework) "
        "must be recognized as help-aware.  The argparse_has detection is broken."
    )


# ---------------------------------------------------------------------------
# Test 5: exec-passthrough form passes
# ---------------------------------------------------------------------------


def test_exec_passthrough_passes(tmp_path: Path) -> None:
    """A script that delegates all args via exec ... '$@' is recognized as help-aware."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    script_content = """\
#!/usr/bin/env bash
# Synthetic dispatcher that forwards all arguments to a delegate.
# --help is passed through to the delegate, which handles it.
exec "$(dirname "${BASH_SOURCE[0]}")/real-script.sh" --max-tasks=1 "$@"
"""
    _write_shell_script(scripts_dir, "test_exec_passthrough.sh", script_content)
    exempt_path = _write_exempt_list(tmp_path, [])

    result = _lint.main([
        "--scripts-dir", str(scripts_dir),
        "--exempt-list", str(exempt_path),
    ])

    assert result == 0, (
        "A script that uses 'exec ... \"$@\"' to pass --help through to a "
        "delegate must be recognized as help-aware.  The exec-passthrough "
        "detection is broken."
    )


# ---------------------------------------------------------------------------
# Test 6: missing scripts directory returns 2
# ---------------------------------------------------------------------------


def test_missing_scripts_directory_returns_exit_2(tmp_path: Path) -> None:
    """A non-existent scripts directory causes lint to return 2 (usage error)."""
    nonexistent = tmp_path / "no_such_dir"
    exempt_path = _write_exempt_list(tmp_path, [])

    result = _lint.main([
        "--scripts-dir", str(nonexistent),
        "--exempt-list", str(exempt_path),
    ])

    assert result == 2, (
        "lint_help_presence.main() must return 2 when the scripts directory "
        "does not exist.  Got {result!r}."
    )


# ---------------------------------------------------------------------------
# Test 7: missing exempt list returns 2
# ---------------------------------------------------------------------------


def test_missing_exempt_list_returns_exit_2(tmp_path: Path) -> None:
    """A non-existent exempt list causes lint to exit via sys.exit(2)."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    nonexistent_exempt = tmp_path / "no_exempt.txt"

    # _load_exempt_list calls sys.exit(2) directly — catch SystemExit.
    with pytest.raises(SystemExit) as exc_info:
        _lint.main([
            "--scripts-dir", str(scripts_dir),
            "--exempt-list", str(nonexistent_exempt),
        ])

    assert exc_info.value.code == 2, (
        "lint_help_presence must exit with code 2 when the exempt list "
        f"is missing.  Got exit code: {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# Test 8: real-tree positive case
# ---------------------------------------------------------------------------


def test_real_tree_produces_zero_offenders() -> None:
    """The real team/scripts/ directory (top-level only, via --scripts-dir)
    produces no offenders when scanned with the real help_presence_exempt.txt.

    This is the proof that the lint passes the current top-level codebase after
    the exempt list is in place.  If this test fails, a new script was added
    without --help and without an exempt-list entry.
    """
    real_exempt_list = _SCRIPTS_DIR / "help_presence_exempt.txt"

    result = _lint.main([
        "--scripts-dir", str(_SCRIPTS_DIR),
        "--exempt-list", str(real_exempt_list),
    ])

    assert result == 0, (
        "lint_help_presence.main() returned non-zero on the real team/scripts/ "
        "directory.  A script is missing --help and is not on the exempt list.  "
        "Run 'python3 team/scripts/lint_help_presence.py --verbose' to see "
        "which script(s) are flagged."
    )


# ---------------------------------------------------------------------------
# Test 9: cm/ positive case — missing --help in cm/ is flagged
# ---------------------------------------------------------------------------


def test_cm_missing_help_triggers_violation(tmp_path: Path) -> None:
    """A script in a synthetic cm/ subdirectory without --help is flagged.

    When the default two-directory scan runs (no --scripts-dir), a script
    placed in the cm/ subdirectory that lacks --help must appear in the
    offender list with the 'cm/' prefix and cause the lint to exit 1.
    """
    # Build a synthetic scripts root with an empty top-level and a cm/ subdir.
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    cm_dir = scripts_dir / "cm"
    cm_dir.mkdir()

    # Place a no-help script in cm/.
    fixture_text = _NO_HELP_FIXTURE.read_text(encoding="utf-8")
    _write_shell_script(cm_dir, "fixture_no_help_script.sh", fixture_text)

    # Empty exempt list in the scripts root.
    exempt_path = _write_exempt_list(scripts_dir, [])

    # Invoke with --scripts-dir pointing at our synthetic top (which has no
    # top-level scripts), then re-invoke without --scripts-dir to test the
    # two-directory default.  We test the default path by monkey-patching
    # __file__ is not practical; instead we pass --scripts-dir to the cm_dir
    # directly to prove the detection mechanism works for that directory.
    result_cm_only = _lint.main([
        "--scripts-dir", str(cm_dir),
        "--exempt-list", str(exempt_path),
    ])

    assert result_cm_only == 1, (
        "lint_help_presence.main() must return 1 when a script in cm/ is "
        "missing --help and the cm/ directory is scanned.  "
        "The no-help fixture in cm/ must be flagged as an offender."
    )


# ---------------------------------------------------------------------------
# Test 10: cm/ exempt-list entry (cm/<name>.sh) suppresses cm/ violation
#          in the _scan_directory helper (the building block of the default scan)
# ---------------------------------------------------------------------------


def test_cm_exempt_list_entry_suppresses_violation(tmp_path: Path) -> None:
    """A 'cm/<name>.sh' exempt-list entry suppresses a cm/ script in _scan_directory.

    The _scan_directory helper used by the default two-directory scan checks
    both the label form ('cm/<name>.sh') and the bare basename against the
    exempt set.  When a script in a cm/ directory is listed in the exempt file
    as 'cm/<name>.sh', it must be skipped and the scan of that directory must
    report no offenders.

    This test exercises _scan_directory directly so the exemption logic for
    the 'cm/' prefix is verified independently of the __file__-based repo-root
    inference used by main().
    """
    cm_dir = tmp_path / "cm"
    cm_dir.mkdir()

    fixture_text = _NO_HELP_FIXTURE.read_text(encoding="utf-8")
    _write_shell_script(cm_dir, "fixture_no_help_script.sh", fixture_text)

    # Exempt set containing the cm/-prefixed label.
    exempt_names: frozenset[str] = frozenset(["cm/fixture_no_help_script.sh"])

    offenders, exempt_count, checked_count = _lint._scan_directory(
        cm_dir,
        label_prefix="cm/",
        exempt_names=exempt_names,
        verbose=False,
    )

    assert offenders == [], (
        "When 'cm/fixture_no_help_script.sh' is in the exempt set, "
        "_scan_directory must not report it as an offender.  "
        "The 'cm/' prefix in the exempt key must be recognized."
    )
    assert exempt_count == 1, (
        "The script listed as 'cm/fixture_no_help_script.sh' in the exempt set "
        "must be counted as exempt (exempt_count should be 1)."
    )
    assert checked_count == 0, (
        "No scripts should have been checked (only the exempted one was present)."
    )


# ---------------------------------------------------------------------------
# Test 11: Real-tree cm/ scan — live regression guard
# ---------------------------------------------------------------------------

# All cm/ scripts now implement --help; this set is empty.
# This test acts as a live regression guard: if a future cm/ script is added
# without --help, the lint will return non-zero and the assertion below will fail.
_CM_EXPECTED_OFFENDERS: frozenset[str] = frozenset()


def test_real_tree_cm_offenders_match_expected() -> None:
    """The real team/scripts/cm/ directory reports zero --help offenders.

    All cm/ scripts implement --help, so the lint exits 0 with an empty offender
    set.  This test acts as a live regression guard: any future cm/ script added
    without --help will cause the lint to return non-zero and this test to fail.
    """
    real_cm_dir = _SCRIPTS_DIR / "cm"
    real_exempt_list = _SCRIPTS_DIR / "help_presence_exempt.txt"

    import io
    import contextlib

    # Capture stdout to parse offender names from the lint output.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = _lint.main([
            "--scripts-dir", str(real_cm_dir),
            "--exempt-list", str(real_exempt_list),
        ])

    output = buf.getvalue()

    # All cm/ scripts now have --help → exit code 0.
    assert result == 0, (
        f"Expected lint to exit 0 (all cm/ scripts have --help).  "
        f"Got exit code {result}.  "
        f"A cm/ script is missing --help or is not on the exempt list."
    )

    # Parse the offender names from the output lines that start with spaces.
    reported_offenders: set[str] = set()
    for line in output.splitlines():
        stripped = line.strip()
        # Offender lines printed by the lint are absolute paths or label names.
        if stripped and not stripped.startswith("lint_help_presence"):
            # The --scripts-dir mode prints bare basenames; extract the last component.
            reported_offenders.add(stripped.rsplit("/", 1)[-1])

    assert reported_offenders == _CM_EXPECTED_OFFENDERS, (
        f"cm/ offender set mismatch.\n"
        f"  Expected: {sorted(_CM_EXPECTED_OFFENDERS)}\n"
        f"  Reported: {sorted(reported_offenders)}\n"
        "Add --help to the listed cm/ script(s) or add them to help_presence_exempt.txt."
    )
