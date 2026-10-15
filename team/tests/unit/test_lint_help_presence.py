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

 12. **Remedy block (the gates-prescribe-remedies pattern)**: forcing a
     missing-``--help`` failure prints the full three-part remedy block
     required by docs/coding-standards.md ("Gates prescribe, not just
     diagnose") — cause(s) naming the offending script, a numbered fix block
     of verbatim-runnable commands with no angle-bracket placeholders ending
     with the re-lint check, and the one-line enforced rule.

 13. **Remedy commands clear the gate**: the exact numbered commands printed
     in the remedy block are parsed out of the captured output and executed,
     verbatim, through a real shell — the appended handler actually responds
     to ``--help`` (proving the remedy is a genuine implementation, not a
     pattern-match exploit), and the lint re-run against the fixture exits 0.

All tests use pytest's ``tmp_path`` and importlib for isolation — no bare /tmp
paths, no live kanban state mutations.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
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


# _write_shell_script is suffix-agnostic (it just writes text to a path); the
# alias below documents intent at .py call sites without duplicating logic.
_write_python_script = _write_shell_script


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
# Test 5b: .py entry points are in scope — argparse-based script passes
# ---------------------------------------------------------------------------


def test_python_argparse_script_passes(tmp_path: Path) -> None:
    """A .py entry point built on argparse.ArgumentParser() is recognized as
    help-aware — argparse adds a working -h/--help flag by default.

    This is the positive proof that widening the scan set to .py files does
    not turn every Python entry point into a false-positive offender: the
    common case (argparse with default add_help=True) must pass.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    script_content = """\
#!/usr/bin/env python3
\"\"\"Synthetic Python entry point using argparse's default --help.\"\"\"
import argparse


def main():
    parser = argparse.ArgumentParser(description="synthetic test tool")
    parser.parse_args()
    print("running")


if __name__ == "__main__":
    main()
"""
    _write_python_script(scripts_dir, "test_argparse_entry_point.py", script_content)
    exempt_path = _write_exempt_list(tmp_path, [])

    result = _lint.main([
        "--scripts-dir", str(scripts_dir),
        "--exempt-list", str(exempt_path),
    ])

    assert result == 0, (
        "A .py entry point built on argparse.ArgumentParser() (default "
        "add_help=True) must be recognized as help-aware.  The pattern-6 "
        "argparse-constructor detection is broken."
    )


# ---------------------------------------------------------------------------
# Test 5c: .py entry points are in scope — no-help script is flagged
# ---------------------------------------------------------------------------


def test_python_script_without_help_triggers_violation(tmp_path: Path) -> None:
    """A .py entry point with no argparse and no --help handling is flagged.

    This is the negative proof for the widened scan set: a Python script
    that never checks --help/-h — the exact shape pseudocron.py had before
    its argument parser was added — must fail the gate, not be silently
    skipped for having the wrong extension.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    script_content = """\
#!/usr/bin/env python3
\"\"\"Synthetic Python entry point with no argument parsing at all.\"\"\"
import sys


def main():
    print("doing real work", file=sys.stderr)


if __name__ == "__main__":
    main()
"""
    _write_python_script(scripts_dir, "test_no_help_entry_point.py", script_content)
    exempt_path = _write_exempt_list(tmp_path, [])

    result = _lint.main([
        "--scripts-dir", str(scripts_dir),
        "--exempt-list", str(exempt_path),
    ])

    assert result == 1, (
        "A .py entry point with no argparse and no --help handling must be "
        "flagged as an offender now that the scan set covers .py files.  "
        "If this returns 0, the widened scan is not actually checking "
        "Python entry points."
    )


# ---------------------------------------------------------------------------
# Test 5d: .py entry point with add_help=False and no manual handler fails
# ---------------------------------------------------------------------------


def test_python_argparse_add_help_false_without_manual_handler_fails(
    tmp_path: Path,
) -> None:
    """A .py script that disables argparse's automatic help (add_help=False)
    and adds no manual --help/-h handling is flagged.

    argparse.ArgumentParser(add_help=False) opts out of the free -h/--help
    flag; a script that does this without adding its own handler has no
    working help path, so pattern 6 must not give it credit just for
    calling the constructor.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    script_content = """\
#!/usr/bin/env python3
\"\"\"Synthetic Python entry point with help explicitly disabled.\"\"\"
import argparse


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project")
    parser.parse_args()


if __name__ == "__main__":
    main()
"""
    _write_python_script(scripts_dir, "test_no_manual_help_entry_point.py", script_content)
    exempt_path = _write_exempt_list(tmp_path, [])

    result = _lint.main([
        "--scripts-dir", str(scripts_dir),
        "--exempt-list", str(exempt_path),
    ])

    assert result == 1, (
        "A .py entry point that sets add_help=False and adds no manual "
        "--help/-h handler must be flagged.  Pattern 6 must not credit a "
        "parser constructed this way."
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

    # Deliberately exercises the real team/scripts/ tree (not a fixture):
    # this is the proof that the current codebase satisfies the lint, not
    # an isolated behavior a fixture could stand in for.
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
# Test 8b: real-tree .py inclusion — pseudocron.py is actually scanned
# ---------------------------------------------------------------------------


def test_real_tree_scan_includes_pseudocron_py() -> None:
    """pseudocron.py is a checked (non-exempt) script in the real-tree scan.

    Regression guard for the gap this lint used to have: before the scan
    set widened to .py files, pseudocron.py was invisible to this lint no
    matter what its --help behavior was. This proves the widened scan
    actually walks it — a script naming any list of .py files without
    including this one would silently keep the gap open. Uses --verbose so
    the scanned-file names are on stdout to check against.
    """
    import contextlib
    import io

    real_exempt_list = _SCRIPTS_DIR / "help_presence_exempt.txt"

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = _lint.main([
            "--scripts-dir", str(_SCRIPTS_DIR),
            "--exempt-list", str(real_exempt_list),
            "--verbose",
        ])

    assert result == 0, (
        "Real-tree scan (including pseudocron.py) must produce zero "
        "offenders; pseudocron.py must implement --help/-h."
    )
    assert "scanning: pseudocron.py" in buf.getvalue(), (
        "pseudocron.py did not appear in the --verbose scan log — the "
        "widened scan set is not actually including .py entry points.\n"
        f"stdout: {buf.getvalue()!r}"
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
    # Deliberately exercises the real team/scripts/cm/ tree (not a
    # fixture): this is a live regression guard, not an isolated behavior a
    # fixture could stand in for.
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


# ---------------------------------------------------------------------------
# Remedy-block tests (the gates-prescribe-remedies pattern)
# ---------------------------------------------------------------------------

# A numbered fix-command line, e.g. "  1. echo '...' >> \"...\"".
_NUMBERED_COMMAND_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$", re.MULTILINE)

# An unresolved angle-bracket placeholder such as "<name>" or "<repo-root>".
# The house placeholder law (docs/coding-standards.md) forbids these in the
# printed fix commands — every site-specific value must already be derived
# and substituted by the time the remedy block is printed.
_ANGLE_BRACKET_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][\w -]*>")


class TestHelpPresenceRemedyBlock:
    """The remedy block: cause(s), numbered verbatim commands, re-lint, rule.

    Exercises the gates-prescribe-remedies pattern's second lint target end
    to end: force a missing-``--help`` failure, assert the remedy block
    contains every required part, assert the printed commands carry no
    angle-bracket placeholders, and then actually execute the printed
    commands verbatim to prove they clear the gate — including proving the
    appended handler genuinely responds to ``--help``, not merely satisfies
    the lint's textual pattern match.
    """

    def _write_no_help_fixture(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """Return (scripts_dir, exempt_list_path, script_path) for a synthetic
        scripts directory containing exactly one no-help script."""
        scripts_dir = tmp_path / "remedy_fixture"
        scripts_dir.mkdir()
        script = _write_shell_script(
            scripts_dir,
            "no_help_target.sh",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'echo "running with args: $*"\n',
        )
        exempt_path = _write_exempt_list(tmp_path, [])
        return scripts_dir, exempt_path, script

    def test_remedy_block_contains_all_required_parts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Forcing a missing-``--help`` failure prints the full three-part
        remedy block.

        Asserts, per docs/coding-standards.md "Gates prescribe, not just
        diagnose":
          1. Cause(s) with a concrete location (the offending script path).
          2. A numbered fix block of at least two verbatim-runnable
             commands, ending with the re-lint verification command.
          3. The one-line enforced rule.
          4. No angle-bracket placeholders anywhere in the output.
        """
        scripts_dir, exempt_path, script = self._write_no_help_fixture(tmp_path)

        result = _lint.main([
            "--scripts-dir", str(scripts_dir),
            "--exempt-list", str(exempt_path),
        ])
        assert result == 1, "Forced no-help fixture did not fail the gate."

        out = capsys.readouterr().out

        assert "Cause(s):" in out, (
            f"Remedy block missing the 'Cause(s):' section.\nstdout: {out!r}"
        )
        assert str(script) in out, (
            f"Remedy block does not name the actual offending script path.\nstdout: {out!r}"
        )

        assert "To fix:" in out, (
            f"Remedy block missing the 'To fix:' numbered fix block.\nstdout: {out!r}"
        )
        commands = _NUMBERED_COMMAND_RE.findall(out)
        assert len(commands) >= 2, (
            "Remedy block's numbered fix block has fewer than 2 commands "
            "(expected at least: append handler, then re-lint).\n"
            f"Parsed commands: {commands!r}\nstdout: {out!r}"
        )
        last_command_text = commands[-1][1]
        assert "lint_help_presence.py" in last_command_text, (
            "Last numbered command is not the re-lint verification command.\n"
            f"Last command: {last_command_text!r}\nstdout: {out!r}"
        )

        assert (
            "must implement a --help/-h handler" in out
        ), (
            f"Remedy block missing the one-line enforced rule.\nstdout: {out!r}"
        )

        placeholder_hits = _ANGLE_BRACKET_PLACEHOLDER_RE.findall(out)
        assert placeholder_hits == [], (
            "Remedy block contains unresolved angle-bracket placeholder(s), "
            "violating the house placeholder law.\n"
            f"Placeholders found: {placeholder_hits!r}\nstdout: {out!r}"
        )

    def test_remedy_block_commands_clear_the_gate(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Executing the remedy block's printed commands verbatim clears the
        gate — the motivating end-to-end proof: a stranger reading only
        the gate's failure output, with no other guidance, can clear it.

        The test parses the exact numbered command strings out of the
        captured stdout (not a hand-reconstructed equivalent) and runs them
        through a real shell. It then invokes the now-patched script with
        --help directly to prove the appended handler is a genuine, working
        implementation — not merely a string that satisfies the lint's regex.
        """
        scripts_dir, exempt_path, script = self._write_no_help_fixture(tmp_path)

        result = _lint.main([
            "--scripts-dir", str(scripts_dir),
            "--exempt-list", str(exempt_path),
        ])
        assert result == 1, "Forced no-help fixture did not fail the gate."

        out = capsys.readouterr().out
        commands = _NUMBERED_COMMAND_RE.findall(out)
        assert len(commands) >= 2, (
            f"Expected at least 2 numbered commands in the remedy block.\nstdout: {out!r}"
        )

        for _, command_text in commands:
            proc = subprocess.run(
                command_text,
                shell=True,
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, (
                f"Printed remedy command exited non-zero: {command_text!r}\n"
                f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
            )

        # The last command IS the re-lint verification command and already
        # asserted returncode == 0 above. Confirm independently via a direct
        # main() call for belt-and-braces.
        recheck = _lint.main([
            "--scripts-dir", str(scripts_dir),
            "--exempt-list", str(exempt_path),
        ])
        assert recheck == 0, (
            "lint_help_presence still fails against the fixture directory "
            "after running every printed remedy command verbatim."
        )

        # The appended handler must be a genuine, working --help responder,
        # not merely text that satisfies the lint's textual pattern match.
        help_proc = subprocess.run(
            ["bash", str(script), "--help"],
            capture_output=True,
            text=True,
        )
        assert help_proc.returncode == 0, (
            f"Patched script exited non-zero on --help: {help_proc.stderr!r}"
        )
        assert "Usage:" in help_proc.stdout, (
            f"Patched script did not print a usage line on --help.\n"
            f"stdout: {help_proc.stdout!r}"
        )


# ---------------------------------------------------------------------------
# Python-offender remedy — same end-to-end proof, for a .py target
# ---------------------------------------------------------------------------


class TestHelpPresenceRemedyBlockPython:
    """The remedy block for a .py offender: same three-part contract, but the
    fix command must be `_build_py_fix_command`'s rewrite, not the shell
    case-branch append (which would be invalid syntax dropped into a .py
    file, and would never even run past a `sys.exit()`-calling __main__
    guard placed ahead of it).
    """

    def _write_no_help_python_fixture(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """Return (scripts_dir, exempt_list_path, script_path) for a synthetic
        scripts directory containing exactly one no-help Python entry point
        whose __main__ guard calls sys.exit() — the shape that proves an
        end-of-file append would never run."""
        scripts_dir = tmp_path / "remedy_fixture_py"
        scripts_dir.mkdir()
        script = _write_python_script(
            scripts_dir,
            "no_help_target.py",
            "#!/usr/bin/env python3\n"
            '"""Synthetic offender for the Python remedy path."""\n'
            "import sys\n"
            "\n"
            "\n"
            "def main():\n"
            '    print("doing real work")\n'
            "    return 0\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    sys.exit(main())\n",
        )
        exempt_path = _write_exempt_list(tmp_path, [])
        return scripts_dir, exempt_path, script

    def test_python_remedy_command_is_single_line_and_clears_the_gate(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The printed fix command for a .py offender is exactly one line
        (a multi-line command would break the numbered-command parsing every
        other remedy-block consumer relies on), carries no angle-bracket
        placeholder, and — when executed verbatim — produces a genuine,
        working --help responder without disturbing the script's normal
        (no-argument) behavior.
        """
        scripts_dir, exempt_path, script = self._write_no_help_python_fixture(tmp_path)

        result = _lint.main([
            "--scripts-dir", str(scripts_dir),
            "--exempt-list", str(exempt_path),
        ])
        assert result == 1, "Forced no-help Python fixture did not fail the gate."

        out = capsys.readouterr().out
        commands = _NUMBERED_COMMAND_RE.findall(out)
        assert len(commands) >= 2, (
            f"Expected at least 2 numbered commands in the remedy block.\nstdout: {out!r}"
        )

        placeholder_hits = _ANGLE_BRACKET_PLACEHOLDER_RE.findall(out)
        assert placeholder_hits == [], (
            "Python remedy command contains unresolved angle-bracket "
            f"placeholder(s).\nPlaceholders found: {placeholder_hits!r}\nstdout: {out!r}"
        )

        fix_command_text = commands[0][1]
        assert fix_command_text.count("\n") == 0, (
            "The .py offender's fix command must be a single line (a "
            "multi-line command breaks the '<n>. <command>' numbered-list "
            f"parsing every remedy-block consumer relies on).\nGot: {fix_command_text!r}"
        )

        for _, command_text in commands:
            proc = subprocess.run(
                command_text,
                shell=True,
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, (
                f"Printed Python remedy command exited non-zero: {command_text!r}\n"
                f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
            )

        recheck = _lint.main([
            "--scripts-dir", str(scripts_dir),
            "--exempt-list", str(exempt_path),
        ])
        assert recheck == 0, (
            "lint_help_presence still fails against the Python fixture "
            "directory after running every printed remedy command verbatim."
        )

        # The patched script must genuinely respond to --help ...
        help_proc = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
        )
        assert help_proc.returncode == 0, (
            f"Patched .py script exited non-zero on --help: {help_proc.stderr!r}"
        )
        assert "Usage:" in help_proc.stdout, (
            f"Patched .py script did not print a usage line on --help.\n"
            f"stdout: {help_proc.stdout!r}"
        )

        # ... while its original no-argument behavior is undisturbed.
        plain_proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
        )
        assert plain_proc.returncode == 0, (
            f"Patched .py script exited non-zero with no arguments: "
            f"{plain_proc.stderr!r}"
        )
        assert "doing real work" in plain_proc.stdout, (
            "Patched .py script's original no-argument behavior changed.\n"
            f"stdout: {plain_proc.stdout!r}"
        )


# ---------------------------------------------------------------------------
# Help-quality checks: undocumented flags and fixed sed help ranges.
# Quality class — each finding is narrated; neither changes the exit status.
# ---------------------------------------------------------------------------

_SECRET_FLAG_SCRIPT = """\
#!/usr/bin/env bash
# fixture_secret_flag.sh
# Usage:
#   fixture_secret_flag.sh [--verbose] [--help]
#
# Options:
#   --verbose  Print more.
#   --help     Print this usage and exit 0.

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) echo usage; exit 0 ;;
    --verbose) shift ;;
    --secret) shift ;;
    *) exit 1 ;;
  esac
done
"""

_FIXED_RANGE_SCRIPT_TEMPLATE = """\
#!/usr/bin/env bash
# fixture_fixed_range.sh
# Usage: fixture_fixed_range.sh [--help]
case "${{1:-}}" in
  --help|-h) sed -n '2,3p' {operand} | sed 's/^# \\{{0,1\\}}//'; exit 0 ;;
esac
"""


def _quality_findings(scripts_dir: Path) -> list:
    """Return the help-quality findings for every script under *scripts_dir*."""
    findings, _checked = _lint._scan_help_quality(scripts_dir, verbose=False)
    return findings


def test_quality_scan_reports_an_undocumented_case_arm_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A script whose case arm parses --secret, never named in its help, is
    reported by name; the documented --verbose is not; the lint still
    exits 0 because the check is quality class."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    _write_shell_script(scripts_dir, "fixture_secret_flag.sh", _SECRET_FLAG_SCRIPT)
    exempt_path = _write_exempt_list(tmp_path, [])

    result = _lint.main(["--scripts-dir", str(scripts_dir), "--exempt-list", str(exempt_path)])
    output = capsys.readouterr().out

    assert result == 0, f"quality findings must not change the exit status; got {result}\n{output}"
    finding_lines = [line for line in output.splitlines() if "[undocumented-flag]" in line]
    assert len(finding_lines) == 1, f"expected one undocumented-flag finding:\n{output}"
    assert "fixture_secret_flag.sh" in finding_lines[0]
    assert "--secret" in finding_lines[0]
    assert "--verbose" not in finding_lines[0], finding_lines[0]


@pytest.mark.parametrize("operand", ['"$0"', '"${BASH_SOURCE[0]}"', "$0"])
def test_quality_scan_reports_a_fixed_sed_help_range(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], operand: str
) -> None:
    """A script printing its help through a fixed sed -n 'A,Bp' range on its
    own file is reported with the line number; the lint still exits 0."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    _write_shell_script(
        scripts_dir,
        "fixture_fixed_range.sh",
        _FIXED_RANGE_SCRIPT_TEMPLATE.format(operand=operand),
    )
    exempt_path = _write_exempt_list(tmp_path, [])

    result = _lint.main(["--scripts-dir", str(scripts_dir), "--exempt-list", str(exempt_path)])
    output = capsys.readouterr().out

    assert result == 0, f"quality findings must not change the exit status; got {result}\n{output}"
    finding_lines = [line for line in output.splitlines() if "[fixed-sed-help-range]" in line]
    assert len(finding_lines) == 1, f"expected one fixed-sed-help-range finding:\n{output}"
    assert "fixture_fixed_range.sh:5" in finding_lines[0], finding_lines[0]
    assert "header_help_print" in finding_lines[0], "the finding must name the replacement helper"


def test_quality_scan_reads_every_parsed_flag_form(tmp_path: Path) -> None:
    """argparse_has, --value-flags lists and OPERATOR_VALID_FLAGS arrays all
    count as parsed flags; each undocumented one is reported, and a script
    rendering help from OPERATOR_VALID_FLAGS documents those by construction."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    _write_shell_script(
        scripts_dir,
        "fixture_forms.sh",
        "#!/usr/bin/env bash\n"
        "# fixture_forms.sh [--help]\n"
        "\n"
        'argparse_parse --value-flags "kanban-root backup" -- "$@"\n'
        "if argparse_has help; then echo usage; exit 0; fi\n"
        "if argparse_has dry-run; then :; fi\n"
        "OPERATOR_VALID_FLAGS=(project limit)\n",
    )
    _write_shell_script(
        scripts_dir,
        "fixture_rendered.sh",
        "#!/usr/bin/env bash\n"
        "# fixture_rendered.sh\n"
        "\n"
        "OPERATOR_VALID_FLAGS=(project limit help)\n"
        "if argparse_has help; then operator_args_render_help_for_flags; exit 0; fi\n",
    )

    findings = {f.label: f.detail for f in _quality_findings(scripts_dir)}

    assert "fixture_rendered.sh" not in findings, findings
    detail = findings.get("fixture_forms.sh", "")
    for flag in ("--kanban-root", "--backup", "--dry-run", "--project", "--limit"):
        assert flag in detail, f"{flag} not reported: {detail!r}"
    assert "--help" not in detail.split(" but ")[0], detail


def test_quality_scan_passes_documented_flags_and_skips_lib_and_comments(
    tmp_path: Path,
) -> None:
    """Flags named in the header, a usage function or a heredoc pass; a case
    arm inside a comment is not a parsed flag; scripts under lib/ are not
    entry points and are never scanned."""
    scripts_dir = tmp_path / "scripts"
    (scripts_dir / "lib").mkdir(parents=True)
    _write_shell_script(
        scripts_dir,
        "fixture_documented.sh",
        "#!/usr/bin/env bash\n"
        "# fixture_documented.sh [--alpha] [--help]\n"
        "\n"
        "_usage() {\n"
        '  echo "  --beta   the beta flag"\n'
        "}\n"
        "cat <<EOF\n"
        "  --gamma  the gamma flag\n"
        "EOF\n"
        'case "$1" in\n'
        "  --help|-h) _usage; exit 0 ;;\n"
        "  --alpha|--beta) ;;\n"
        "  --gamma=*) ;;\n"
        "  # --commented-out) ;;\n"
        "esac\n",
    )
    _write_shell_script(
        scripts_dir / "lib",
        "helper.sh",
        "#!/usr/bin/env bash\n"
        "# helper.sh\n"
        "x() { case \"$1\" in --help) sed -n '2,3p' \"$0\" ;; --hidden) ;; esac; }\n",
    )

    assert _quality_findings(scripts_dir) == []


def test_quality_scan_is_clean_on_the_real_scripts_tree() -> None:
    """Every bash entry point under team/scripts/ names each flag it parses
    and none prints help through a fixed sed range."""
    # Deliberately the real tree, not a fixture: this proves the shipped
    # scripts satisfy both checks.
    findings = _quality_findings(_SCRIPTS_DIR)
    assert findings == [], "help-quality findings on the real tree:\n" + "\n".join(
        f"  [{f.check}] {f.label}: {f.detail}" for f in findings
    )
