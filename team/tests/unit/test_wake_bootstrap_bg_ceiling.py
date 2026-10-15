"""
test_wake_bootstrap_bg_ceiling.py
==================================
Behavioral and structural tests for the CLI background-wait ceiling
derivation in team/scripts/lib/wake_bootstrap.sh.

The wake path derives BASH_MAX_TIMEOUT_MS (the Claude CLI's own
background-wait ceiling, verified against the installed 2.1.251 CLI's
--help/docs) from the kanban's own per-task budget — MAX_TASK_SECONDS plus
KILL_GRACE_SECONDS, converted to milliseconds — rather than pinning a
literal. See v2.14.0-provider-runtime-hardening: an un-derived CLI ceiling
(600s by default) silently truncates work the kanban's watchdog (default
5400s + 30s) was still happily waiting on.

Acceptance criteria exercised:

  1. MAX_TASK_SECONDS=5400, KILL_GRACE_SECONDS=30, no override → 5430000.
  2. MAX_TASK_SECONDS=600, KILL_GRACE_SECONDS=60 → 660000 (both terms track).
  3. BASH_MAX_TIMEOUT_MS already set (operator override via claude-env) →
     left untouched; an operator-override log line is emitted.
  4. MAX_TASK_SECONDS=0 (per-task watchdog disabled) → no ceiling derived
     from grace alone; BASH_MAX_TIMEOUT_MS stays unset.
  5. Structural: no hardcoded millisecond ceiling literal (e.g. "5430000")
     appears anywhere in the wake path.

WHY THE DERIVATION BLOCK IS EXTRACTED RATHER THAN RE-SOURCED
--------------------------------------------------------------
wake_bootstrap_init() unconditionally sources a long dependency chain
(project_paths.sh, projects.sh, task_ids.sh, discovery.sh, workflow.sh,
config_loader.sh, dev_tree.sh, pp_run_ops.sh, overwatch_protocol.sh,
error_contract.sh, env_bootstrap.sh, script_common.sh) and runs itself as a
side effect of being sourced (see the file's own header). Exercising the
derivation behaviorally does not need any of that: the block is bounded by
the BG-WAIT-CEILING-DERIVATION-BEGIN/END sentinel comments in the real file
and extracted verbatim here (the same _extract_block pattern used by
test_dispatch_prompt_temp_line.py), so the test always runs the actual
shipped arithmetic — never a hand-copied duplicate that could silently
drift from it.
"""

from __future__ import annotations

import pathlib
import textwrap

from tests.unit.shell_harness import run_bash

# Path relative to team/ (pytest cwd when run via run-unit-tests.sh)
_WAKE_BOOTSTRAP_SH = pathlib.Path("scripts/lib/wake_bootstrap.sh")
_WAKE_DRIVER_INVOKE_SH = pathlib.Path("scripts/lib/wake_driver_invoke.sh")

_BEGIN_MARKER = "BG-WAIT-CEILING-DERIVATION-BEGIN"
_END_MARKER = "BG-WAIT-CEILING-DERIVATION-END"


# ---------------------------------------------------------------------------
# Helper: extract a block of lines between two markers (inclusive of start,
# exclusive of end). Mirrors test_dispatch_prompt_temp_line.py's _extract_block.
# ---------------------------------------------------------------------------


def _extract_block(path: pathlib.Path, start_marker: str, end_marker: str) -> list[str]:
    """Return lines from start_marker (inclusive) up to but not including
    the first line that contains end_marker, after the block has begun."""
    lines = path.read_text(encoding="utf-8").splitlines()
    block: list[str] = []
    capturing = False
    for line in lines:
        if start_marker in line:
            capturing = True
        if capturing:
            if end_marker in line and block:
                break
            block.append(line)
    return block


def _derivation_block_text() -> str:
    block = _extract_block(_WAKE_BOOTSTRAP_SH, _BEGIN_MARKER, _END_MARKER)
    assert block, (
        f"{_WAKE_BOOTSTRAP_SH} does not contain a "
        f"{_BEGIN_MARKER} .. {_END_MARKER} sentinel pair. The derivation "
        "block must carry both sentinels for this test to extract it."
    )
    return "\n".join(block)


def _run_derivation(
    tmp_path: pathlib.Path,
    *,
    max_task_seconds: str,
    kill_grace_seconds: str,
    preset_ceiling: str | None = None,
) -> tuple[str, str]:
    """Run the extracted derivation block under bash and return (result, log_output).

    result:      the value of BASH_MAX_TIMEOUT_MS after the block runs, or the
                  literal "<unset>" when the variable is unset/empty.
    log_output:   everything the stubbed log() function was called with.
    """
    preset_line = (
        f'BASH_MAX_TIMEOUT_MS="{preset_ceiling}"\n'
        if preset_ceiling is not None
        else "unset BASH_MAX_TIMEOUT_MS\n"
    )
    script = textwrap.dedent(
        f"""
        set -euo pipefail
        log() {{ echo "LOG: $*" >&2; }}
        MAX_TASK_SECONDS={max_task_seconds}
        KILL_GRACE_SECONDS={kill_grace_seconds}
        {preset_line}
        {_derivation_block_text()}
        echo "RESULT=${{BASH_MAX_TIMEOUT_MS:-<unset>}}"
        """
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"derivation block exited non-zero:\nstdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    value = result.stdout.strip().removeprefix("RESULT=")
    return value, result.stderr


# ---------------------------------------------------------------------------
# Criterion 1: default budget derives 5430000
# ---------------------------------------------------------------------------


def test_default_budget_derives_5430000(tmp_path: pathlib.Path) -> None:
    """MAX_TASK_SECONDS=5400 + KILL_GRACE_SECONDS=30, no override → 5430000ms."""
    value, log_output = _run_derivation(
        tmp_path, max_task_seconds="5400", kill_grace_seconds="30"
    )
    assert value == "5430000", f"expected 5430000, got {value!r}\nlog: {log_output!r}"
    assert "derived BASH_MAX_TIMEOUT_MS=5430000" in log_output, (
        f"expected a derivation log line naming the value; got: {log_output!r}"
    )


# ---------------------------------------------------------------------------
# Criterion 2: both terms track — a different budget derives a different value
# ---------------------------------------------------------------------------


def test_derivation_tracks_both_terms(tmp_path: pathlib.Path) -> None:
    """MAX_TASK_SECONDS=600 + KILL_GRACE_SECONDS=60 → 660000ms.

    Proves the arithmetic is (MAX_TASK_SECONDS + KILL_GRACE_SECONDS) * 1000,
    not a literal or a single-term derivation — changing both inputs changes
    the output in the way the formula predicts.
    """
    value, log_output = _run_derivation(
        tmp_path, max_task_seconds="600", kill_grace_seconds="60"
    )
    assert value == "660000", f"expected 660000, got {value!r}\nlog: {log_output!r}"


def test_derivation_tracks_grace_term_alone(tmp_path: pathlib.Path) -> None:
    """Changing only KILL_GRACE_SECONDS changes the derived value.

    Guards against a derivation that only reads MAX_TASK_SECONDS and ignores
    the grace term.
    """
    value_low, _ = _run_derivation(
        tmp_path, max_task_seconds="100", kill_grace_seconds="10"
    )
    value_high, _ = _run_derivation(
        tmp_path, max_task_seconds="100", kill_grace_seconds="50"
    )
    assert value_low == "110000"
    assert value_high == "150000"
    assert value_low != value_high


# ---------------------------------------------------------------------------
# Criterion 3: operator override wins and is logged
# ---------------------------------------------------------------------------


def test_operator_override_left_untouched(tmp_path: pathlib.Path) -> None:
    """A pre-set BASH_MAX_TIMEOUT_MS (as claude-env would set it) is not overwritten."""
    value, log_output = _run_derivation(
        tmp_path,
        max_task_seconds="5400",
        kill_grace_seconds="30",
        preset_ceiling="999999",
    )
    assert value == "999999", (
        f"operator override was overwritten: expected 999999, got {value!r}"
    )
    assert "operator override in force" in log_output, (
        f"expected an operator-override log line; got: {log_output!r}"
    )
    # The derivation must not have also logged a "derived" line for this run.
    assert "derived BASH_MAX_TIMEOUT_MS=" not in log_output, (
        f"derivation ran despite an operator override being present: {log_output!r}"
    )


def test_operator_override_of_empty_string_is_not_an_override(
    tmp_path: pathlib.Path,
) -> None:
    """An empty-string BASH_MAX_TIMEOUT_MS is treated as unset, not an override.

    Matches env_bootstrap.sh's own posture: `[[ -z ... ]]` treats "" the same
    as unset, so a claude-env line that exports an empty value does not
    silently suppress the derivation.
    """
    value, log_output = _run_derivation(
        tmp_path,
        max_task_seconds="5400",
        kill_grace_seconds="30",
        preset_ceiling="",
    )
    assert value == "5430000", f"expected derivation to fire, got {value!r}"
    assert "derived BASH_MAX_TIMEOUT_MS=5430000" in log_output


# ---------------------------------------------------------------------------
# Criterion 4: MAX_TASK_SECONDS=0 disables the watchdog; no ceiling derived
# ---------------------------------------------------------------------------


def test_disabled_watchdog_does_not_derive_from_grace_alone(
    tmp_path: pathlib.Path,
) -> None:
    """MAX_TASK_SECONDS=0 → BASH_MAX_TIMEOUT_MS stays unset, not 30000ms."""
    value, log_output = _run_derivation(
        tmp_path, max_task_seconds="0", kill_grace_seconds="30"
    )
    assert value == "<unset>", (
        f"expected BASH_MAX_TIMEOUT_MS to remain unset when the watchdog is "
        f"disabled, got {value!r}\nlog: {log_output!r}"
    )
    assert value != "30000", (
        "derivation must not fall back to KILL_GRACE_SECONDS alone when "
        "MAX_TASK_SECONDS=0"
    )
    assert "watchdog disabled" in log_output, (
        f"expected a disabled-watchdog log line; got: {log_output!r}"
    )


def test_disabled_watchdog_respects_existing_override(tmp_path: pathlib.Path) -> None:
    """MAX_TASK_SECONDS=0 with an operator override present still leaves it untouched."""
    value, log_output = _run_derivation(
        tmp_path,
        max_task_seconds="0",
        kill_grace_seconds="30",
        preset_ceiling="123456",
    )
    assert value == "123456"
    assert "operator override in force" in log_output


# ---------------------------------------------------------------------------
# Criterion 5: structural — no hardcoded millisecond literal in the wake path
# ---------------------------------------------------------------------------


def test_no_hardcoded_ceiling_literal_in_wake_bootstrap() -> None:
    """The wake path never hardcodes the default-derived millisecond value.

    5430000 is the value (MAX_TASK_SECONDS=5400 + KILL_GRACE_SECONDS=30) * 1000
    derives to under the shipped defaults. If this literal appears anywhere
    in wake_bootstrap.sh, the derivation has been replaced (or shadowed) by a
    pinned constant — the exact failure this test exists to catch.
    """
    content = _WAKE_BOOTSTRAP_SH.read_text(encoding="utf-8")
    assert "5430000" not in content, (
        f"{_WAKE_BOOTSTRAP_SH} contains the literal default-derived millisecond "
        "value 5430000. The ceiling must be computed from MAX_TASK_SECONDS and "
        "KILL_GRACE_SECONDS at wake time, never pinned as a literal."
    )


def test_no_hardcoded_ceiling_literal_in_watchdog() -> None:
    """The per-task watchdog file also carries no pinned millisecond ceiling."""
    content = _WAKE_DRIVER_INVOKE_SH.read_text(encoding="utf-8")
    assert "5430000" not in content, (
        f"{_WAKE_DRIVER_INVOKE_SH} contains the literal default-derived "
        "millisecond value 5430000."
    )


def test_derivation_uses_arithmetic_not_literal_assignment() -> None:
    """Structural: the export line computes the value via shell arithmetic.

    Guards against a future edit that keeps the sentinels and log lines but
    quietly swaps the `$(( ... ))` expression for a literal assignment.
    """
    block_text = _derivation_block_text()
    assert "$(( (MAX_TASK_SECONDS + KILL_GRACE_SECONDS) * 1000 ))" in block_text, (
        f"derivation block no longer computes BASH_MAX_TIMEOUT_MS via shell "
        f"arithmetic over MAX_TASK_SECONDS and KILL_GRACE_SECONDS:\n{block_text}"
    )


def test_derivation_checks_unset_or_empty_before_deriving() -> None:
    """Structural: the no-masking guard is `[[ -z ... ]]`, matching env_bootstrap.sh."""
    block_text = _derivation_block_text()
    assert 'if [[ -z "${BASH_MAX_TIMEOUT_MS:-}" ]]; then' in block_text, (
        f"derivation block does not open with the expected no-masking guard:\n{block_text}"
    )
