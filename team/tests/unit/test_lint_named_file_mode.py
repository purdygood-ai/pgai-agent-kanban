"""
test_lint_named_file_mode.py
============================
Named-file mode (``--files PATH [PATH ...]``) for the two per-file
test-authoring lints:

  - team/scripts/lint_test_anti_patterns.py
  - team/scripts/lint_skip_bug_gate.sh (and its Python surface,
    team/scripts/lib/skip_bug_citation_scan.py)

Named-file mode checks exactly the files it is given: no tests-directory
walk, and (for the anti-pattern lint) no runtime-script scan. Each case
below proves one side of that contract by running the real script as a
subprocess:

  - a named file carrying a finding is reported, naming file and line;
  - a sibling file carrying the same finding, but NOT named, is never
    reported (proof the directory is not walked);
  - a named clean file exits 0 with no finding;
  - malformed requests (no paths, a missing path, --files combined with a
    directory option) are usage errors, exit 2.

Every fixture is generated under pytest's ``tmp_path`` at test time, never
committed under team/tests/, so the whole-tree guards (for example
test_lint_golden_env_coupling.py::test_real_tree_has_zero_ap7_findings)
keep reporting zero findings for the real tree. For the same reason the
anti-pattern fixture text below is assembled from fragments: no single
source line of THIS file matches the AP7 detector, so this file stays
clean under the whole-tree scan while the generated fixture does not.

The bug identifiers used as test data are synthetic placeholders that
exist only inside these fixtures and the tmp_path bugs/ directory built
for them.
"""

from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path

from tests.unit.shell_harness import run_bash

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_AP_LINT = _SCRIPTS_DIR / "lint_test_anti_patterns.py"
_SKIP_GATE = _SCRIPTS_DIR / "lint_skip_bug_gate.sh"
_SCAN_MODULE_PATH = _SCRIPTS_DIR / "lib" / "skip_bug_citation_scan.py"


def _import_scan_module():
    spec = importlib.util.spec_from_file_location(
        "skip_bug_citation_scan_named_files_under_test", _SCAN_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_SCAN_MODULE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_scan = _import_scan_module()


# ---------------------------------------------------------------------------
# Fixture text
# ---------------------------------------------------------------------------

# The two halves AP7 pairs: a golden-comparison marker (a "golden"-named
# identifier assigned through a pathlib join) and an inherited subprocess
# environment. Each is split across string fragments so the detector's
# regexes never match a line of this test module itself.
_GOLDEN_MARKER_LINE = "_GOLD" + "EN_DIR = _HERE / " + '"surface"\n'
_INHERITED_ENV_LINE = "    env = dict(os." + "environ)\n"

AP7_SOURCE = (
    "import os\n"
    "import pathlib\n"
    "import subprocess\n"
    "\n"
    "_HERE = pathlib.Path(__file__).parent\n"
    + _GOLDEN_MARKER_LINE
    + "\n"
    "\n"
    "def test_capture_matches_committed_surface():\n"
    + _INHERITED_ENV_LINE
    + '    subprocess.run(["true"], env=env, check=True)\n'
)
AP7_ENV_LINENO = AP7_SOURCE.splitlines().index(_INHERITED_ENV_LINE.rstrip("\n")) + 1

CLEAN_SOURCE = (
    "def test_addition_is_commutative():\n"
    "    assert 1 + 2 == 2 + 1\n"
)

PLACEHOLDER_SKIP_SOURCE = (
    "import pytest\n"
    "\n"
    "\n"
    "def test_waits_on_followup():\n"
    '    pytest.skip("BUG-SKIP-synthetic-placeholder pending")\n'
)

UNFILED_CITATION_SOURCE = (
    "import pytest\n"
    "\n"
    "\n"
    "def test_waits_on_unfiled_followup():\n"
    "    pytest.skip(\n"
    '        "see BUG-9871 for context"\n'
    "    )\n"
)

FILED_CITATION_SOURCE = (
    "import pytest\n"
    "\n"
    "\n"
    "def test_waits_on_filed_followup():\n"
    '    pytest.skip("BUG-9872 pending fix")\n'
)

SHELL_UNFILED_CITATION_SOURCE = (
    "#!/usr/bin/env bash\n"
    "# SKIP: BUG-9873 pending fix\n"
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run_ap_lint(tmp_path: Path, *args: str):
    quoted = " ".join(shlex.quote(a) for a in args)
    return run_bash(tmp_path, f"python3 {shlex.quote(str(_AP_LINT))} {quoted}")


def _run_skip_gate(tmp_path: Path, bugs_dir: Path, *args: str, cwd: Path | None = None):
    quoted = " ".join(shlex.quote(a) for a in args)
    return run_bash(
        tmp_path,
        f"bash {shlex.quote(str(_SKIP_GATE))} {quoted} "
        f"--bugs-dir {shlex.quote(str(bugs_dir))}",
        cwd=cwd,
    )


def _bugs_dir_with_filed_citation(tmp_path: Path) -> Path:
    bugs_dir = tmp_path / "bugs"
    _write(bugs_dir / "BUG-9872-synthetic-filed-followup.md", "synthetic\n")
    return bugs_dir


# ---------------------------------------------------------------------------
# lint_test_anti_patterns.py --files
# ---------------------------------------------------------------------------


def test_anti_pattern_lint_reports_ap7_in_named_file_and_ignores_unnamed_sibling(
    tmp_path: Path,
) -> None:
    tests_dir = tmp_path / "tests"
    named = _write(tests_dir / "test_named_golden.py", AP7_SOURCE)
    unnamed = _write(tests_dir / "test_unnamed_golden.py", AP7_SOURCE)
    clean = _write(tests_dir / "test_clean.py", CLEAN_SOURCE)

    result = _run_ap_lint(tmp_path, "--files", str(named), str(clean))

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{named}:{AP7_ENV_LINENO}: [AP7]" in result.stdout, result.stdout
    assert str(unnamed) not in result.stdout
    assert "lint_test_anti_patterns: 1 finding(s)" in result.stdout
    assert "scanning 2 named file(s)" in result.stdout


def test_anti_pattern_lint_named_clean_file_exits_zero_with_no_finding(
    tmp_path: Path,
) -> None:
    clean = _write(tmp_path / "tests" / "test_clean.py", CLEAN_SOURCE)

    result = _run_ap_lint(tmp_path, "--files", str(clean))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[AP" not in result.stdout
    assert "OK — 0 findings across 1 file(s) (1 test, 0 runtime)." in result.stdout


def test_anti_pattern_lint_named_mode_walks_no_tree_and_scans_no_runtime_scripts(
    tmp_path: Path,
) -> None:
    clean = _write(tmp_path / "tests" / "test_clean.py", CLEAN_SOURCE)

    result = _run_ap_lint(tmp_path, "--files", str(clean), "--verbose")

    assert result.returncode == 0, result.stdout + result.stderr
    scanned = [
        line.split("scanning: ", 1)[1]
        for line in result.stdout.splitlines()
        if "scanning: " in line
    ]
    assert scanned == [str(clean.resolve())]
    assert "scanning runtime scripts" not in result.stdout
    assert "scanning tests " not in result.stdout


def test_anti_pattern_lint_named_file_under_fixtures_dir_is_still_scanned(
    tmp_path: Path,
) -> None:
    """The whole-tree walk skips fixtures/; a file named explicitly is not."""
    named = _write(tmp_path / "tests" / "fixtures" / "golden_case.py", AP7_SOURCE)

    result = _run_ap_lint(tmp_path, "--files", str(named))

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{named}:{AP7_ENV_LINENO}: [AP7]" in result.stdout


def test_anti_pattern_lint_counts_a_file_named_twice_once(tmp_path: Path) -> None:
    named = _write(tmp_path / "tests" / "test_named_golden.py", AP7_SOURCE)

    result = _run_ap_lint(tmp_path, "--files", str(named), str(named))

    assert result.returncode == 1
    assert "lint_test_anti_patterns: 1 finding(s)" in result.stdout
    assert "scanning 1 named file(s)" in result.stdout


def test_anti_pattern_lint_missing_named_file_is_a_usage_error(tmp_path: Path) -> None:
    missing = tmp_path / "tests" / "test_does_not_exist.py"

    result = _run_ap_lint(tmp_path, "--files", str(missing))

    assert result.returncode == 2
    assert str(missing) in result.stderr


def test_anti_pattern_lint_files_with_tests_dir_is_a_usage_error(
    tmp_path: Path,
) -> None:
    clean = _write(tmp_path / "tests" / "test_clean.py", CLEAN_SOURCE)

    result = _run_ap_lint(
        tmp_path, "--files", str(clean), "--tests-dir", str(tmp_path / "tests")
    )

    assert result.returncode == 2
    assert "--files cannot be combined" in result.stderr


def test_anti_pattern_lint_whole_tree_default_still_walks_tests_dir(
    tmp_path: Path,
) -> None:
    """Without --files the directory walk is unchanged: a file the caller
    never named is found because it sits under --tests-dir."""
    tests_dir = tmp_path / "tests"
    walked = _write(tests_dir / "test_walked_golden.py", AP7_SOURCE)

    result = _run_ap_lint(tmp_path, "--tests-dir", str(tests_dir), "--scripts-dirs")

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{walked.resolve()}:{AP7_ENV_LINENO}: [AP7]" in result.stdout
    assert "scanning tests" in result.stdout


# ---------------------------------------------------------------------------
# lint_skip_bug_gate.sh --files
# ---------------------------------------------------------------------------


def test_skip_gate_reports_placeholder_in_named_file_only(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    named = _write(tests_dir / "test_named_placeholder.py", PLACEHOLDER_SKIP_SOURCE)
    unnamed = _write(tests_dir / "test_unnamed_placeholder.py", PLACEHOLDER_SKIP_SOURCE)
    bugs_dir = _bugs_dir_with_filed_citation(tmp_path)

    result = _run_skip_gate(tmp_path, bugs_dir, "--files", str(named))

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{named}:5:" in result.stderr
    assert "BUG-SKIP-synthetic-placeholder" in result.stderr
    assert str(unnamed) not in result.stderr + result.stdout
    assert "1 placeholder skip citation(s) found" in result.stderr


def test_skip_gate_reports_unfiled_citation_in_named_file(tmp_path: Path) -> None:
    named = _write(tmp_path / "tests" / "test_unfiled.py", UNFILED_CITATION_SOURCE)
    bugs_dir = _bugs_dir_with_filed_citation(tmp_path)

    result = _run_skip_gate(tmp_path, bugs_dir, "--files", str(named))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BUG-9871: no matching file found" in result.stderr


def test_skip_gate_named_file_with_filed_citation_exits_zero(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    filed = _write(tests_dir / "test_filed.py", FILED_CITATION_SOURCE)
    clean = _write(tests_dir / "test_clean.py", CLEAN_SOURCE)
    # A bad sibling that is not named must not affect the verdict.
    _write(tests_dir / "test_unnamed_unfiled.py", UNFILED_CITATION_SOURCE)
    bugs_dir = _bugs_dir_with_filed_citation(tmp_path)

    result = _run_skip_gate(tmp_path, bugs_dir, "--files", str(filed), str(clean))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK — all skip annotations are compliant." in result.stdout
    assert "BUG-9871" not in result.stdout + result.stderr


def test_skip_gate_named_shell_file_uses_line_scan(tmp_path: Path) -> None:
    named = _write(tmp_path / "tests" / "skip_case.sh", SHELL_UNFILED_CITATION_SOURCE)
    bugs_dir = _bugs_dir_with_filed_citation(tmp_path)

    result = _run_skip_gate(tmp_path, bugs_dir, "--files", str(named))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BUG-9873: no matching file found" in result.stderr


def test_skip_gate_unfiled_citation_names_each_citing_file_and_line(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    first = _write(tests_dir / "test_unfiled_first.py", UNFILED_CITATION_SOURCE)
    second = _write(tests_dir / "test_unfiled_second.py", UNFILED_CITATION_SOURCE)
    shell = _write(tests_dir / "skip_case.sh", SHELL_UNFILED_CITATION_SOURCE)
    bugs_dir = _bugs_dir_with_filed_citation(tmp_path)

    result = _run_skip_gate(
        tmp_path, bugs_dir, "--files", str(first), str(second), str(shell)
    )

    assert result.returncode == 1, result.stdout + result.stderr
    # One finding per missing ID, each followed by every skip that cites it.
    assert "2 cited BUG-NNNN ID(s) have no file" in result.stderr
    assert result.stderr.count("BUG-9871: no matching file found") == 1
    assert f"{first}:5:" in result.stderr
    assert f"{second}:5:" in result.stderr
    assert f"{shell}:2:# SKIP: BUG-9873 pending fix" in result.stderr


def test_skip_gate_counts_one_file_named_under_two_spellings_once(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "unit" / "test_probe_skip_placeholder.py", PLACEHOLDER_SKIP_SOURCE)
    _write(tmp_path / "tests" / "unit" / "test_probe_skip_unfiled.py", UNFILED_CITATION_SOURCE)
    bugs_dir = _bugs_dir_with_filed_citation(tmp_path)

    result = _run_skip_gate(
        tmp_path,
        bugs_dir,
        "--files",
        "tests/unit/test_probe_skip_placeholder.py",
        "./tests/unit/test_probe_skip_placeholder.py",
        "tests/unit/test_probe_skip_unfiled.py",
        str(tmp_path / "tests" / "unit" / "test_probe_skip_unfiled.py"),
        cwd=tmp_path,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "placeholder skip IDs in 2 named file(s)" in result.stdout
    assert "1 placeholder skip citation(s) found" in result.stderr
    assert result.stderr.count("test_probe_skip_placeholder.py:5:") == 1
    assert result.stderr.count("test_probe_skip_unfiled.py:5:") == 1
    # The first spelling given is the one scanned and printed.
    assert "tests/unit/test_probe_skip_placeholder.py:5:" in result.stderr
    assert "./tests/unit/test_probe_skip_placeholder.py" not in result.stderr


def test_skip_gate_files_without_paths_is_a_usage_error(tmp_path: Path) -> None:
    bugs_dir = _bugs_dir_with_filed_citation(tmp_path)

    result = _run_skip_gate(tmp_path, bugs_dir, "--files")

    assert result.returncode == 2
    assert "error:   lint_skip_bug_gate.files_empty" in result.stderr


def test_skip_gate_missing_named_file_is_a_usage_error(tmp_path: Path) -> None:
    missing = tmp_path / "tests" / "test_does_not_exist.py"
    bugs_dir = _bugs_dir_with_filed_citation(tmp_path)

    result = _run_skip_gate(tmp_path, bugs_dir, "--files", str(missing))

    assert result.returncode == 2
    assert "error:   lint_skip_bug_gate.files_not_found" in result.stderr
    assert str(missing) in result.stderr


def test_skip_gate_files_with_tests_dir_is_a_usage_error(tmp_path: Path) -> None:
    clean = _write(tmp_path / "tests" / "test_clean.py", CLEAN_SOURCE)
    bugs_dir = _bugs_dir_with_filed_citation(tmp_path)

    result = _run_skip_gate(
        tmp_path,
        bugs_dir,
        "--files",
        str(clean),
        "--tests-dir",
        str(tmp_path / "tests"),
    )

    assert result.returncode == 2
    assert "error:   lint_skip_bug_gate.files_with_tests_dir" in result.stderr


# ---------------------------------------------------------------------------
# skip_bug_citation_scan.py --files
# ---------------------------------------------------------------------------


def test_citation_scan_scan_files_reads_exactly_the_given_paths(
    tmp_path: Path,
) -> None:
    named = _write(tmp_path / "artifacts" / "test_named.py", UNFILED_CITATION_SOURCE)
    _write(tmp_path / "test_unnamed.py", FILED_CITATION_SOURCE)

    findings = _scan.scan_files([named], "citation")

    assert [(f.path, f.lineno) for f in findings] == [(named, 5)]
    assert "BUG-9871" in findings[0].snippet


def test_citation_scan_cli_files_mode_rejects_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "test_does_not_exist.py"

    result = run_bash(
        tmp_path,
        f"python3 {shlex.quote(str(_SCAN_MODULE_PATH))} --mode citation "
        f"--files {shlex.quote(str(missing))}",
    )

    assert result.returncode == 2
    assert str(missing) in result.stderr
