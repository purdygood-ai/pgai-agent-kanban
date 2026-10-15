"""
test_wake_dispatch_ledger.py
============================
Behavioral tests for the per-task dispatch ledger the wake writes around
each provider invocation (wd_invoke_task, scripts/lib/wake_driver_invoke.sh)
and OVERWATCH's transient-error check reads (scripts/lib/overwatch-checks/
check-transient-api-error.sh).

The ledger block is extracted VERBATIM from wake_driver_invoke.sh between
its DISPATCH-LEDGER-BEGIN / DISPATCH-LEDGER-END sentinel comments (the same
technique test_wake_telemetry_safeguard_retry.py uses for the watchdog
block it encloses) and run with a stub provider, so the assertions hold
against the production writes rather than a re-implementation.

Fixtures exercised:
  (A) One dispatch whose provider output carries a 529 → the ledger holds a
      "dispatched" line naming the batch log and the byte offset where the
      task's output begins, then a "returned" line closing the span with the
      provider exit code; the check's span extraction over that ledger sees
      the 529 and nothing written before the dispatch.
  (B) A second dispatch of the same task into a newer batch log → the
      latest record names the newer log (the earlier lines are kept).
  (C) A ledger write failure is narrated as a WARNING and the provider is
      still invoked — the ledger never gates a dispatch.

All tests run against synthetic directories and never touch the live kanban root.
"""

from __future__ import annotations

import json
import pathlib

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_INVOKE_LIB = _TEAM_DIR / "scripts" / "lib" / "wake_driver_invoke.sh"
_PROTOCOL_LIB = _TEAM_DIR / "scripts" / "lib" / "overwatch_protocol.sh"
_CHECK = _TEAM_DIR / "scripts" / "lib" / "overwatch-checks" / "check-transient-api-error.sh"

_LEDGER_BEGIN = "# DISPATCH-LEDGER-BEGIN"
_LEDGER_END = "# DISPATCH-LEDGER-END"


def _extract_ledger_block() -> str:
    """Return the verbatim source between the ledger sentinels."""
    source = _INVOKE_LIB.read_text(encoding="utf-8")
    begin = source.index(_LEDGER_BEGIN)
    end = source.index(_LEDGER_END)
    assert begin < end, "sentinels out of order in wake_driver_invoke.sh"
    return source[begin:end]


def _harness(
    tmp_path: pathlib.Path,
    *,
    provider_output: str,
    provider_exit: int,
    task_dir: pathlib.Path,
    log_file: pathlib.Path,
    extra: str = "",
) -> str:
    """Build a bash harness that runs the extracted ledger block once.

    MAX_TASK_SECONDS=0 takes the watchdog's direct-invocation path so the
    stub provider runs in this shell; its output is appended to LOG_FILE the
    way the real provider's stdout/stderr is.
    """
    block = _extract_ledger_block()
    return f"""
set -uo pipefail
export PGAI_AGENT_KANBAN_TEMP_DIR={str(tmp_path / "kanban_tmp")!r}
mkdir -p "$PGAI_AGENT_KANBAN_TEMP_DIR"
source {str(_PROTOCOL_LIB)!r}
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" | tee -a "$LOG_FILE" >&2; }}

LOG_FILE={str(log_file)!r}
AGENT="coder"
ACTIVE_PROVIDER="claude"
task_id="TASK-LEDGER-TEST"
task_dir={str(task_dir)!r}
task_log_dir={str(task_dir / "logs")!r}
MAX_TASK_SECONDS=0
KILL_GRACE_SECONDS=1
agent_exit=""
{extra}

_wd_run_provider_with_telemetry_safeguard_retry() {{
  printf '%s\\n' {provider_output!r} >> "$LOG_FILE"
  PROVIDER_AGENT_EXIT_CODE={provider_exit}
}}

_run_ledger_block() {{
{block}
}}
_run_ledger_block

echo "AGENT_EXIT=${{agent_exit}}"
"""


def _ledger_lines(task_dir: pathlib.Path) -> list[dict]:
    ledger = task_dir / "logs" / "dispatch.jsonl"
    assert ledger.is_file(), f"ledger not written at {ledger}"
    return [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_dispatch_writes_open_and_closed_records_the_check_can_read(
    tmp_path: pathlib.Path,
) -> None:
    """Fixture (A): one dispatch; the span excludes pre-dispatch content."""
    task_dir = tmp_path / "tasks" / "TASK-LEDGER-TEST"
    (task_dir / "logs").mkdir(parents=True)
    log_file = tmp_path / "logs" / "agents" / "coder-batch-20260101-100000.log"
    log_file.parent.mkdir(parents=True)
    preamble = "[wake] earlier task in this firing: API Error: 529 belongs to someone else\n"
    log_file.write_text(preamble, encoding="utf-8")

    harness = _harness(
        tmp_path,
        provider_output="claude: API Error: 529 overloaded_error",
        provider_exit=1,
        task_dir=task_dir,
        log_file=log_file,
    )
    result = run_bash(tmp_path, harness)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "AGENT_EXIT=1" in result.stdout, result.stdout
    assert "dispatch ledger: batch log" in result.stderr, (
        f"the open record should be narrated\nstderr={result.stderr}"
    )

    lines = _ledger_lines(task_dir)
    assert [rec["event"] for rec in lines] == ["dispatched", "returned"], lines
    opened, closed = lines
    assert opened["batch_log"] == str(log_file)
    assert opened["offset_start"] == len(preamble.encode("utf-8")), opened
    assert opened["offset_end"] is None and opened["exit_code"] is None, opened
    assert opened["agent"] == "coder" and opened["provider"] == "claude", opened
    assert closed["batch_log"] == str(log_file)
    assert closed["offset_start"] == opened["offset_start"], closed
    assert closed["offset_end"] == log_file.stat().st_size, closed
    assert closed["exit_code"] == 1, closed

    # The check's own span extraction over this ledger sees the task's 529
    # and not the preamble that predates the dispatch.
    probe = f"""
set -uo pipefail
source {str(_PROTOCOL_LIB)!r}
source {str(_CHECK)!r}
_ctae_log_tail "TASK-LEDGER-TEST" {str(tmp_path / "tasks")!r}
"""
    span = run_bash(tmp_path, probe)
    assert span.returncode == 0, span.stderr
    assert "claude: API Error: 529 overloaded_error" in span.stdout, span.stdout
    assert "belongs to someone else" not in span.stdout, (
        f"pre-dispatch content must be outside the span\nstdout={span.stdout}"
    )


def test_second_dispatch_appends_and_latest_names_newer_batch_log(
    tmp_path: pathlib.Path,
) -> None:
    """Fixture (B): re-dispatch into a newer batch log supersedes the record."""
    task_dir = tmp_path / "tasks" / "TASK-LEDGER-TEST"
    (task_dir / "logs").mkdir(parents=True)
    agents = tmp_path / "logs" / "agents"
    agents.mkdir(parents=True)
    first = agents / "coder-batch-20260101-100000.log"
    second = agents / "coder-batch-20260101-110000.log"
    first.write_text("", encoding="utf-8")
    second.write_text("", encoding="utf-8")

    for log_file, output in ((first, "first run: 529"), (second, "second run: clean")):
        result = run_bash(
            tmp_path,
            _harness(tmp_path, provider_output=output, provider_exit=0,
                     task_dir=task_dir, log_file=log_file),
        )
        assert result.returncode == 0, result.stderr

    lines = _ledger_lines(task_dir)
    assert len(lines) == 4, lines
    assert [rec["batch_log"] for rec in lines] == [str(first)] * 2 + [str(second)] * 2

    latest = run_bash(
        tmp_path,
        f"source {str(_PROTOCOL_LIB)!r}; dispatch_record_latest {str(task_dir)!r}",
    )
    assert latest.returncode == 0, latest.stderr
    assert latest.stdout.startswith(f"{second}\t"), latest.stdout


def test_ledger_write_failure_is_a_warning_and_provider_still_runs(
    tmp_path: pathlib.Path,
) -> None:
    """Fixture (C): the ledger never gates the dispatch."""
    task_dir = tmp_path / "tasks" / "TASK-LEDGER-TEST"
    (task_dir / "logs").mkdir(parents=True)
    log_file = tmp_path / "logs" / "agents" / "coder-batch-20260101-100000.log"
    log_file.parent.mkdir(parents=True)
    log_file.write_text("", encoding="utf-8")

    harness = _harness(
        tmp_path,
        provider_output="provider ran",
        provider_exit=0,
        task_dir=task_dir,
        log_file=log_file,
        extra='dispatch_record_append() { echo "stub: ledger unwritable" >&2; return 1; }',
    )
    result = run_bash(tmp_path, harness)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "WARNING" in result.stderr and "dispatch ledger write failed" in result.stderr, (
        result.stderr
    )
    assert "dispatch ledger close failed" in result.stderr, result.stderr
    assert "provider ran" in log_file.read_text(encoding="utf-8"), (
        "the provider must still have been invoked"
    )
    assert "AGENT_EXIT=0" in result.stdout, result.stdout
    assert not (task_dir / "logs" / "dispatch.jsonl").exists()
