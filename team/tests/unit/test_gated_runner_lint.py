"""
test_gated_runner_lint.py
==========================
Behavioral tests for team/scripts/lib/gated_runner_lint.sh — the shared
warn-file-continue wrapper that implements the CONVERT class of
docs/blocking-site-census.md: a quality-class finding must warn
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
     must not perturb (roles/TESTER.md "Test Authoring Guidelines" -- no bare-/tmp
     writes from tests); this suite instead exercises a synthetic stand-in
     function with the identical zero/non-zero contract, invoked through
     gated_runner_run_quality_lint exactly as run-unit-tests.sh and
     run-integration-tests.sh now do it.
     -> test_post_suite_cleanliness_style_check_warns_files_and_continues

Also covered: a non-empty finding detail
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
_ERROR_CONTRACT_LIB = "scripts/lib/error_contract.sh"


def _source(snippet: str) -> str:
    """Return a bash snippet that sources gated_runner_lint.sh then runs *snippet*."""
    return f"set -uo pipefail\nsource {_LIB}\n{snippet}"


def _source_with_error_contract(snippet: str) -> str:
    """Same as _source, but also sources error_contract.sh first -- required
    by gated_runner_assert_findings_claim, which calls pgai_emit_error_contract
    (both real gated runners source error_contract.sh before gated_runner_lint.sh
    for exactly this reason)."""
    return f"set -uo pipefail\nsource {_ERROR_CONTRACT_LIB}\nsource {_LIB}\n{snippet}"


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
    exercised from a unit test (roles/TESTER.md "Test Authoring Guidelines" bans
    bare-/tmp writes from tests); this fixture uses a synthetic stand-in
    function with the identical contract (0 = clean, 1 = residue found,
    diagnostic printed to stderr) invoked through the exact wrapper call
    shape now used in both runner scripts, proving the conversion's actual
    code path rather than re-testing the underlying scanner.
    """
    # The /tmp/pgai_orphan_test_residue below is diagnostic sample text this
    # synthetic stand-in echoes to stderr -- it names no real path and is
    # never created, opened, or deleted; it only proves the wrapper surfaces
    # the underlying check's diagnostic line unchanged.
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


# ---------------------------------------------------------------------------
# gated_runner_record_finding
# ---------------------------------------------------------------------------


def test_record_finding_appends_a_hand_authored_quality_finding(tmp_path: pathlib.Path) -> None:
    """A caller with no command output to summarize (a fatal pre-flight
    gate noting it aborted the run) can still add a well-shaped finding."""
    script = _source(
        """
gated_runner_record_finding "runner_incomplete" "aborted before pytest ran"
echo "FINDINGS_COUNT=${#GATED_RUNNER_FINDINGS[@]}"
echo "FINDING_JSON=${GATED_RUNNER_FINDINGS[0]}"
"""
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert "FINDINGS_COUNT=1" in result.stdout
    finding_line = next(
        line for line in result.stdout.splitlines() if line.startswith("FINDING_JSON=")
    )
    finding = json.loads(finding_line.removeprefix("FINDING_JSON="))
    assert finding == {
        "lint": "runner_incomplete",
        "severity": "quality",
        "summary": "aborted before pytest ran",
    }


# ---------------------------------------------------------------------------
# gated_runner_claim_findings_file / gated_runner_assert_findings_claim --
# the "clear or claim" mechanism and its consumer-side refusal.
# ---------------------------------------------------------------------------


def test_claim_writes_empty_findings_document_and_a_matching_sidecar_token(
    tmp_path: pathlib.Path,
) -> None:
    """Claiming a path resets it to an empty, THIS-run findings document
    (proving the "clear" half of clear-then-gate) and records the same
    token in the sidecar it echoes -- the token round-trips."""
    out_path = tmp_path / "findings.json"
    script = _source(
        f"""
echo "PRE-EXISTING CONTENT" > "{out_path}"
TOKEN="$(gated_runner_claim_findings_file "{out_path}" "test-runner.sh")"
echo "TOKEN=$TOKEN"
"""
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    token_line = next(line for line in result.stdout.splitlines() if line.startswith("TOKEN="))
    token = token_line.removeprefix("TOKEN=")
    assert token, "the claim must echo a non-empty run-identity token"

    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["findings"] == [], (
        "claiming must overwrite whatever was on disk with a fresh, empty "
        "findings document -- this is the 'clear' half of clear-then-gate"
    )
    assert doc["runner"] == "test-runner.sh"

    sidecar = pathlib.Path(f"{out_path}.runid")
    assert sidecar.read_text(encoding="utf-8") == token, (
        "the sidecar '<path>.runid' file must hold exactly the token the "
        "claim echoed -- gated_runner_assert_findings_claim reads it back "
        "to verify no other invocation reclaimed the path"
    )


def test_assert_findings_claim_succeeds_when_uncontested(tmp_path: pathlib.Path) -> None:
    """The common case: nothing else touched the path between claim and
    assert, so the token still matches and the assert is silent (returns 0,
    prints nothing)."""
    out_path = tmp_path / "findings.json"
    script = _source_with_error_contract(
        f"""
TOKEN="$(gated_runner_claim_findings_file "{out_path}" "test-runner.sh")"
gated_runner_assert_findings_claim "{out_path}" "$TOKEN"
echo "ASSERT_RETURNED=$?"
"""
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert "ASSERT_RETURNED=0" in result.stdout
    assert "error:   gated_runner.stale_findings_claim" not in result.stderr, (
        "an uncontested claim must not print the stale-claim refusal"
    )


def test_assert_findings_claim_refuses_with_dual_channel_contract_when_reclaimed(
    tmp_path: pathlib.Path,
) -> None:
    """The consumer-side refusal: when the sidecar token
    has moved (simulating a concurrent invocation of a gated runner
    reclaiming the same host-wide path), the assert refuses rather than
    letting the caller trust content that may belong to a different run --
    and the refusal carries the full dual-channel contract (error key,
    message, remedy) plus a census anchor comment at the refusal site."""
    out_path = tmp_path / "findings.json"
    script = _source_with_error_contract(
        f"""
TOKEN="$(gated_runner_claim_findings_file "{out_path}" "test-runner.sh")"
echo "a-different-invocations-token" > "{out_path}.runid"
gated_runner_assert_findings_claim "{out_path}" "$TOKEN"
echo "ASSERT_RETURNED=$?"
"""
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, "the wrapping script must still complete (assert never exits)"
    assert "ASSERT_RETURNED=1" in result.stdout, (
        "a moved token must make the assert return 1 so the caller can "
        "skip trusting the file (e.g. skip handing it to the bug filer)"
    )
    assert "error:   gated_runner.stale_findings_claim" in result.stderr, (
        "the refusal's machine key must be stable and grep-able"
    )
    assert "no longer carries this invocation's run-identity claim" in result.stderr, (
        "the refusal's plain-language message must explain what happened"
    )
    assert "Re-run the gated runner that owns" in result.stderr, (
        "the refusal's remedy must be runnable, naming the concrete next step"
    )
    assert str(out_path) in result.stderr, "the remedy must name the actual file at issue"


def test_assert_findings_claim_refuses_when_sidecar_absent(tmp_path: pathlib.Path) -> None:
    """A findings file with no sidecar at all (never claimed by this
    mechanism -- e.g. a stale file written before any claim) is refused exactly
    like a mismatched token, never silently trusted."""
    out_path = tmp_path / "findings.json"
    out_path.write_text('{"schema_version": 1, "findings": []}\n', encoding="utf-8")
    script = _source_with_error_contract(
        f'gated_runner_assert_findings_claim "{out_path}" "some-token"\n'
        'echo "ASSERT_RETURNED=$?"\n'
    )
    result = run_bash(tmp_path, script)
    assert "ASSERT_RETURNED=1" in result.stdout
    assert "error:   gated_runner.stale_findings_claim" in result.stderr
    assert "<absent>" in result.stderr, (
        "the message must say the sidecar was absent, not just print an "
        "empty token"
    )


def test_reclaiming_after_a_refused_assert_succeeds(tmp_path: pathlib.Path) -> None:
    """The refusal's own remedy, demonstrated: re-running the claim step
    produces a fresh token that the sidecar now agrees with, so a
    subsequent assert against the NEW token succeeds."""
    out_path = tmp_path / "findings.json"
    script = _source_with_error_contract(
        f"""
TOKEN="$(gated_runner_claim_findings_file "{out_path}" "test-runner.sh")"
echo "a-different-invocations-token" > "{out_path}.runid"
gated_runner_assert_findings_claim "{out_path}" "$TOKEN" 2>/dev/null
echo "FIRST_ASSERT_RETURNED=$?"

NEW_TOKEN="$(gated_runner_claim_findings_file "{out_path}" "test-runner.sh")"
gated_runner_assert_findings_claim "{out_path}" "$NEW_TOKEN"
echo "SECOND_ASSERT_RETURNED=$?"
"""
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert "FIRST_ASSERT_RETURNED=1" in result.stdout
    assert "SECOND_ASSERT_RETURNED=0" in result.stdout, (
        "re-claiming (the refusal's own remedy) must make the very next "
        "assert against the freshly-claimed token succeed"
    )
