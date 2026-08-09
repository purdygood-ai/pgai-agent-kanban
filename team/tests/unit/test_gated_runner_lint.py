"""
test_gated_runner_lint.py
==========================
Behavioral tests for team/scripts/lib/gated_runner_lint.sh — the shared
warn-file-continue wrapper that implements the CONVERT class of
docs/blocking-site-census.md (v2.3.6): a quality-class finding must warn
(dual-channel: printed in full to the console, then filed into the
GATED_RUNNER_FINDINGS accumulator / findings-file), and MUST NOT abort the
calling gated runner regardless of the wrapped command's own exit code.

WHY THIS FILE EXISTS
---------------------
team/tests/unit/test_check_breakage_class.py's own module docstring already
references "test_gated_runner_lint.py" as the sibling suite covering the
non-fatal half of the breakage/quality split -- that file never existed
before the CONVERT-class conversion this file belongs to. Converting every
CONVERT-class census site to warn+file+continue closes that gap: this file
is both the regression test for the wrapper's pre-existing behavior AND the
fixture proof required by the v2.3.6 requirements ("Fixture: a previously-fatal
CONVERT-class site now warns, files, and continues (one representative per
converted family)").

FAMILIES COVERED
-----------------
Every CONVERT row in docs/blocking-site-census.md funnels through one of a
small number of underlying mechanisms ("families").  Rather than one
fixture per row (23 rows), this suite proves one representative per
mechanism, per the census Notes ("if a family is large the fixture uses one
representative per family"):

  1. Coarse pass/fail lint wrapper (rows 31-43, 45) -- a lint script/command
     that returns a bare exit code.  Representative: a synthetic failing
     command run through gated_runner_run_quality_lint.
     -> test_failing_command_is_recorded_and_wrapper_continues,
        test_passing_command_produces_no_finding

  2. Dashboard network-git gate (row 39) -- previously a standalone script
     with NO call site in either gated runner (verified by grep across the
     tree before this task; see docs/blocking-site-census.md row 39 and
     this task's status.md).  This task wires it into both runners' quality
     lint fleets using the exact same wrapper mechanism as family 1, so the
     representative fixture for family 1 already proves this family's
     behavior; this suite additionally proves the specific wiring shape
     (python3 scripts/lint_dashboard_network_git_gate.py invoked through
     the wrapper) against a synthetic violation.
     -> test_dashboard_network_gate_violation_warns_files_and_continues

  3. Post-suite cleanliness assertions (rows 52-53) -- previously a hard
     `exit 1` in run-unit-tests.sh / run-integration-tests.sh when residue
     or a leaked listener was detected after a passing pytest run.  This
     task converts both call sites to the same wrapper.  The real
     pgai_tmp_cleanliness_check / pgai_listener_cleanliness_check functions
     scan actual bare /tmp and real /proc listener state, which a unit test
     must not perturb (SOP.md "Test Authoring Guidelines" -- no bare-/tmp
     writes from tests); this suite instead exercises a synthetic stand-in
     function with the identical zero/non-zero contract, invoked through
     gated_runner_run_quality_lint exactly as run-unit-tests.sh and
     run-integration-tests.sh now do it.
     -> test_post_suite_cleanliness_style_check_warns_files_and_continues

Also covered: the finding-detail regression this task fixed
(_gated_runner_build_finding previously always produced an empty "detail"
field because its heredoc-fed `python3 -` invocation consumed stdin for the
script source, leaving nothing for the script's own sys.stdin.read() to
read the piped detail text) and the findings-file schema.
"""

from __future__ import annotations

import json
import pathlib

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/gated_runner_lint.sh"


def _source(snippet: str) -> str:
    """Return a bash snippet that sources gated_runner_lint.sh then runs *snippet*."""
    return f"set -uo pipefail\nsource {_LIB}\n{snippet}"


# ---------------------------------------------------------------------------
# Family 1: coarse pass/fail lint wrapper (rows 31-43, 45)
# ---------------------------------------------------------------------------


def test_failing_command_is_recorded_and_wrapper_continues(tmp_path: pathlib.Path) -> None:
    """A failing quality-class command does not abort the caller.

    Proves the CONVERT contract end to end: the command's own output is
    printed (user-facing channel), a non-blocking notice is printed to
    stderr (log channel), a finding is appended to GATED_RUNNER_FINDINGS
    with a non-empty detail, and the wrapper itself returns 0 so a caller
    under `set -e` keeps running -- this is the exact previously-fatal ->
    warn+file+continue transition the v2.3.6 requirements ask for.
    """
    script = _source(
        """
synthetic_failing_lint() {
  echo "synthetic_failing_lint: 1 violation(s) found" >&2
  return 1
}
gated_runner_run_quality_lint "synthetic_failing_lint" -- synthetic_failing_lint
echo "WRAPPER_RETURNED=$?"
echo "FINDINGS_COUNT=${#GATED_RUNNER_FINDINGS[@]}"
echo "FINDING_JSON=${GATED_RUNNER_FINDINGS[0]}"
"""
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"expected the wrapper script to exit 0 (continue past the failing "
        f"lint); got {result.returncode}. stderr={result.stderr!r}"
    )
    assert "synthetic_failing_lint: 1 violation(s) found" in result.stdout, (
        "the failing command's own output must be printed in full "
        "(user-facing channel)"
    )
    assert "WRAPPER_RETURNED=0" in result.stdout, (
        "gated_runner_run_quality_lint must always return 0, regardless of "
        "the wrapped command's exit code"
    )
    assert "FINDINGS_COUNT=1" in result.stdout, (
        "a failing command must append exactly one finding to "
        "GATED_RUNNER_FINDINGS"
    )
    finding_line = next(
        line for line in result.stdout.splitlines() if line.startswith("FINDING_JSON=")
    )
    finding = json.loads(finding_line.removeprefix("FINDING_JSON="))
    assert finding["lint"] == "synthetic_failing_lint"
    assert finding["severity"] == "quality"
    assert finding["exit_code"] == 1
    assert finding["detail"].strip() == "synthetic_failing_lint: 1 violation(s) found", (
        "regression guard: _gated_runner_build_finding must capture the "
        "command's real output as 'detail', not an empty string"
    )


def test_passing_command_produces_no_finding(tmp_path: pathlib.Path) -> None:
    """A clean quality-class command records nothing and prints OK."""
    script = _source(
        """
gated_runner_run_quality_lint "synthetic_clean_lint" -- true
echo "WRAPPER_RETURNED=$?"
echo "FINDINGS_COUNT=${#GATED_RUNNER_FINDINGS[@]}"
"""
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert "[gated-runner] synthetic_clean_lint: OK" in result.stdout
    assert "WRAPPER_RETURNED=0" in result.stdout
    assert "FINDINGS_COUNT=0" in result.stdout


# ---------------------------------------------------------------------------
# Family 2: dashboard network-git gate (row 39) -- newly wired into the
# quality lint fleet by this task.
# ---------------------------------------------------------------------------


def test_dashboard_network_gate_violation_warns_files_and_continues(
    tmp_path: pathlib.Path,
) -> None:
    """The dashboard network-git gate, run through the wrapper, does not block.

    Before this task, lint_dashboard_network_git_gate.py had no call site in
    either gated runner (confirmed by grepping run-unit-tests.sh,
    run-integration-tests.sh, run-gated-tests.sh, and ship-rc.sh) -- a
    regression here would never have surfaced anywhere in the loop. This
    test invokes it exactly as run-unit-tests.sh / run-integration-tests.sh
    now do, against a synthetic dashboard/ directory carrying one violation,
    and proves the finding is recorded rather than the process aborting.
    """
    team_dir = tmp_path / "team"
    dashboard_dir = team_dir / "scripts" / "dashboard"
    dashboard_dir.mkdir(parents=True)
    (dashboard_dir / "git-status.sh").write_text(
        "#!/usr/bin/env bash\n"
        'git -C "$dev_tree" fetch origin --tags\n',
        encoding="utf-8",
    )

    script = _source(
        f"""
gated_runner_run_quality_lint "dashboard_network_git_gate" -- \\
  python3 scripts/lint_dashboard_network_git_gate.py --team-dir {team_dir}
echo "WRAPPER_RETURNED=$?"
echo "FINDINGS_COUNT=${{#GATED_RUNNER_FINDINGS[@]}}"
"""
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        "a real network-git violation must not abort the runner once wired "
        f"through the wrapper; got exit {result.returncode}"
    )
    assert "WRAPPER_RETURNED=0" in result.stdout
    assert "FINDINGS_COUNT=1" in result.stdout
    assert "git-status.sh" in result.stdout, (
        "the violation's file name must appear in the printed (user-facing) "
        "output"
    )


# ---------------------------------------------------------------------------
# Family 3: post-suite cleanliness assertions (rows 52-53)
# ---------------------------------------------------------------------------


def test_post_suite_cleanliness_style_check_warns_files_and_continues(
    tmp_path: pathlib.Path,
) -> None:
    """A post-suite cleanliness-style check no longer aborts the runner.

    Before this task, run-unit-tests.sh and run-integration-tests.sh each
    called pgai_tmp_cleanliness_check / pgai_listener_cleanliness_check
    directly and `exit 1`ed on a non-zero return. This task replaces that
    with the same gated_runner_run_quality_lint wrapper used by every other
    quality-class check.

    pgai_tmp_cleanliness_check itself scans real bare /tmp and must not be
    exercised from a unit test (SOP.md "Test Authoring Guidelines" bans
    bare-/tmp writes from tests); this fixture uses a synthetic stand-in
    function with the identical contract (0 = clean, 1 = residue found,
    diagnostic printed to stderr) invoked through the exact wrapper call
    shape now used in both runner scripts, proving the conversion's actual
    code path rather than re-testing the underlying scanner.
    """
    script = _source(
        """
_synthetic_bare_tmp_cleanliness_check() {
  echo "[pgai-cleanliness] FAIL: kanban suite left residue in bare /tmp." >&2
  echo "[pgai-cleanliness]   /tmp/pgai_orphan_test_residue" >&2
  return 1
}
echo "Checking for bare-/tmp residue..."
gated_runner_run_quality_lint "bare_tmp_cleanliness" -- _synthetic_bare_tmp_cleanliness_check
echo "RUNNER_STILL_RUNNING=yes"
echo "FINDINGS_COUNT=${#GATED_RUNNER_FINDINGS[@]}"
exit 0
"""
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        "the previously-fatal cleanliness `exit 1` must be gone -- a residue "
        f"finding must not abort the runner; got exit {result.returncode}"
    )
    assert "[pgai-cleanliness] FAIL" in result.stdout, (
        "the check's own diagnostic must still print in full"
    )
    assert "RUNNER_STILL_RUNNING=yes" in result.stdout, (
        "script execution must continue past the check (this line would "
        "never print under the old `exit 1` behavior)"
    )
    assert "FINDINGS_COUNT=1" in result.stdout


# ---------------------------------------------------------------------------
# gated_runner_write_findings_file schema
# ---------------------------------------------------------------------------


def test_write_findings_file_schema(tmp_path: pathlib.Path) -> None:
    """The findings file always exists and matches the documented schema."""
    out_path = tmp_path / "findings.json"
    script = _source(
        f"""
synthetic_failing_lint() {{ echo "boom" >&2; return 1; }}
gated_runner_run_quality_lint "synthetic_failing_lint" -- synthetic_failing_lint
gated_runner_write_findings_file "{out_path}" "test-runner.sh"
"""
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert out_path.is_file(), "findings file must be written unconditionally"

    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    assert doc["runner"] == "test-runner.sh"
    assert "generated_at" in doc
    assert isinstance(doc["findings"], list)
    assert len(doc["findings"]) == 1
    finding = doc["findings"][0]
    assert finding["lint"] == "synthetic_failing_lint"
    assert finding["severity"] == "quality"
    assert finding["detail"].strip() == "boom"


def test_write_findings_file_written_even_when_empty(tmp_path: pathlib.Path) -> None:
    """The findings file is written even when nothing was ever recorded."""
    out_path = tmp_path / "findings-empty.json"
    script = _source(f'gated_runner_write_findings_file "{out_path}" "test-runner.sh"')
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["findings"] == []
