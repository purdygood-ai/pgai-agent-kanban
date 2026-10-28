"""
test_wake_blocked_ledger.py
============================
Unit tests for team/scripts/lib/wake_blocked_ledger.sh — the thin bash
adapter over pgai_agent_kanban.metrics.blocked_ledger that the wake path's
HALT-skip sites in wake_common.sh call.

These tests source the real library files (project_paths.sh, projects.sh,
discovery.sh, wake_blocked_ledger.sh) against a synthetic kanban root and
call pgai_stamp_halt_boundary / pgai_stamp_fleet_halt_boundary directly,
exactly as wake_common.sh's own HALT checks do -- see
docs/test-authoring-guidelines.md ("the test must invoke that script").
log() is a real capturing function (echoes a tagged line to stdout), not a
no-op stub -- the tests that claim "silent" assert the tag never appears
in stdout, and the genuine-failure test asserts it appears exactly once.

Coverage:
  - A block-start then a matching block-end for a project with an active
    RC round-trips through the real ledger file.
  - Calling "start" a second time before the matching "end" is a silent
    no-op (the ledger's own duplicate-open refusal, swallowed, producing
    no wake-log output) -- this is what makes "first cycle it appears"
    hold without a second state machine in the adapter.
  - Calling "end" with no open interval is likewise a silent no-op,
    producing no wake-log output -- what makes "first cycle it's gone"
    hold the same way. Stamping another project than the ambient one, that
    no-op is recorded in no project's refusal ledger.
  - A HALT seen while a project's Active RC is "none" is recorded under
    the ledger's no-RC version, and closed there.
  - pgai_stamp_fleet_halt_boundary fans out to every project in
    projects_cfg_list, with or without an active RC.
  - The block-start carries the cause from the sentinel-writes log (chain
    with no operator record, operator with an op=promote record; the
    fleet-scoped HALT always operator) and the HALT file's own "error:" key
    as reason_code, or the source's fixed label when it has none.
  - The block-end is class any with reason_code halt_cleared; one that
    unhalt already closed (class human) gets no second line.
  - A HALT that spans an RC opening or closing has every interval closed
    by one end attempt, the RC interval's own timestamps unchanged.
  - An end attempt for a project whose ledger never names the source makes
    no ledger CLI call at all.
  - An unwritable metrics directory never raises out of the bash function
    -- the caller (a wake cycle) keeps its own exit code -- and, unlike
    the designed no-ops above, this genuine write failure still produces
    exactly one wake-log line naming the project and the source.

All tests use tmp_path via the shared run_bash harness; no live filesystem
paths, no bare /tmp. Function names describe behaviour under test, never
bug/task IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from pgai_agent_kanban.lib.sentinel_provenance import FIELDS, log_path
from pgai_agent_kanban.metrics.blocked_ledger import NO_RC_VERSION, block_intervals
from tests.unit.shell_harness import run_bash

# log() writes a tagged line to stdout (rather than discarding, as a stub
# would) so a test can observe exactly what the wake log would have shown --
# the "silent no-op" tests below assert this tag never appears, and the
# unwritable-directory test asserts it appears exactly once. The tag keeps
# an assertion from accidentally matching unrelated stdout (there is none
# today, since the ledger CLI's own stdout is redirected to /dev/null by
# the adapter, but the tag makes that independent of that redirection).
_LOG_TAG = "WAKE-LOG:"

_LIB_SOURCE_PREAMBLE = """
set -euo pipefail
export KANBAN_ROOT="{kanban_root}"
export TEAM_ROOT="{kanban_root}"
source scripts/lib/project_paths.sh
source scripts/lib/projects.sh
source scripts/lib/discovery.sh
source scripts/lib/pp_run_ops.sh
log() {{ echo "%s $*"; }}
source scripts/lib/wake_blocked_ledger.sh
""" % _LOG_TAG


def _write_release_state(project_dir: Path, active_rc: str = "none", last_released: "str | None" = None) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    last = f"\n## Last Released\n{last_released}\n" if last_released is not None else ""
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\n"
        f"{active_rc}\n\n"
        "## RC Opened At\n"
        "2026-09-18T00:00:00+00:00\n\n"
        "## RC Opened By Task\n"
        "none\n" + last,
        encoding="utf-8",
    )


def _write_projects_cfg(kanban_root: Path, project_names: list[str]) -> None:
    lines = []
    for i, name in enumerate(project_names, start=1):
        lines.append(f"[project:{name}]\npriority={i}\n\n")
    (kanban_root / "projects.cfg").write_text("".join(lines), encoding="utf-8")


def _ledger_lines(kanban_root: Path, project: str) -> list[dict]:
    ledger_path = kanban_root / "projects" / project / "metrics" / "blocked-intervals.jsonl"
    if not ledger_path.is_file():
        return []
    text = ledger_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _make_root(tmp_path: Path, projects: dict[str, str]) -> Path:
    """Build a synthetic kanban root. *projects* maps name -> active_rc."""
    root = tmp_path / "kanban"
    for name, active_rc in projects.items():
        _write_release_state(root / "projects" / name, active_rc=active_rc)
    _write_projects_cfg(root, list(projects.keys()))
    return root


# ---------------------------------------------------------------------------
# pgai_stamp_halt_boundary -- single project
# ---------------------------------------------------------------------------


def test_start_then_end_round_trips_through_the_real_ledger(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_halt_boundary demo "halt:project" start\n'
        'pgai_stamp_halt_boundary demo "halt:project" end\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr

    records = _ledger_lines(root, "demo")
    assert [r["kind"] for r in records] == ["block-start", "block-end"]
    assert all(r["version"] == "v1.2.3" for r in records)
    assert all(r["source"] == "halt:project" for r in records)


def test_repeated_start_before_matching_end_is_a_silent_no_op(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_halt_boundary demo "halt:project" start\n'
        'pgai_stamp_halt_boundary demo "halt:project" start\n'
        'pgai_stamp_halt_boundary demo "halt:project" start\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr

    records = _ledger_lines(root, "demo")
    # Exactly one block-start recorded, no matter how many wake cycles
    # observe the HALT still present.
    assert [r["kind"] for r in records] == ["block-start"]
    # The second and third attempts found the boundary already in the
    # state they asked for (already blocked) -- the designed steady
    # state, which must produce no wake-log output at all.
    assert _LOG_TAG not in result.stdout, result.stdout


def test_end_with_no_open_interval_is_a_silent_no_op(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_halt_boundary demo "halt:project" end\n'
        'pgai_stamp_halt_boundary demo "halt:project" end\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert _ledger_lines(root, "demo") == []
    # Both attempts found no open interval to close -- also the designed
    # steady state (an ordinary wake cycle with no HALT to close out), and
    # likewise must produce no wake-log output at all.
    assert _LOG_TAG not in result.stdout, result.stdout


def test_no_op_end_for_another_project_reaches_no_refusal_ledger(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"alpha": "v2.48.1", "beta": "v0.30.8"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_halt_boundary beta "halt:project" end\n'
    )
    result = run_bash(
        tmp_path, script,
        extra_env={"PGAI_PROJECT_NAME": "alpha", "PGAI_AGENT_KANBAN_ROOT_PATH": str(root)},
    )
    assert result.returncode == 0, result.stderr
    assert _LOG_TAG not in result.stdout, result.stdout
    for project in ("alpha", "beta"):
        assert not (root / "projects" / project / "refusal-ledger.md").exists(), project


def test_active_rc_none_records_the_halt_under_the_no_rc_version(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "none"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_halt_boundary demo "halt:project" start\n'
        'pgai_stamp_halt_boundary demo "halt:project" end\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    records = _ledger_lines(root, "demo")
    assert [r["kind"] for r in records] == ["block-start", "block-end"]
    assert all(r["version"] == NO_RC_VERSION for r in records)
    assert _LOG_TAG not in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# pgai_stamp_fleet_halt_boundary -- fleet fan-out
# ---------------------------------------------------------------------------


def test_fleet_boundary_stamps_every_project_with_or_without_an_active_rc(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"has-rc": "v2.0.0", "between-rcs": "none"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_fleet_halt_boundary "halt:global" start\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr

    has_rc_records = _ledger_lines(root, "has-rc")
    assert [r["kind"] for r in has_rc_records] == ["block-start"]
    assert has_rc_records[0]["source"] == "halt:global"
    assert has_rc_records[0]["version"] == "v2.0.0"
    between_records = _ledger_lines(root, "between-rcs")
    assert [(r["kind"], r["version"]) for r in between_records] == [("block-start", NO_RC_VERSION)]


def test_fleet_boundary_pairs_start_and_end_across_two_calls(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"has-rc": "v2.0.0"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_fleet_halt_boundary "halt:global" start\n'
        'pgai_stamp_fleet_halt_boundary "halt:global" end\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    records = _ledger_lines(root, "has-rc")
    assert [r["kind"] for r in records] == ["block-start", "block-end"]


# ---------------------------------------------------------------------------
# Best-effort: an unwritable metrics directory never raises
# ---------------------------------------------------------------------------


def test_unwritable_metrics_directory_never_breaks_the_caller(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v3.0.0"})
    metrics_dir = root / "projects" / "demo" / "metrics"
    metrics_dir.mkdir(parents=True)
    metrics_dir.chmod(0o000)
    try:
        script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
            'pgai_stamp_halt_boundary demo "halt:project" start\n'
            'echo "CALLER_STILL_RUNNING"\n'
        )
        result = run_bash(tmp_path, script)
        # The adapter itself never aborts the caller: the script's own
        # exit code and its trailing echo both survive the failed stamp.
        assert result.returncode == 0, result.stderr
        assert "CALLER_STILL_RUNNING" in result.stdout
        # Unlike the designed no-op above, this is a genuine write failure
        # (an unwritable directory, not the boundary already being in the
        # requested state) -- it must still speak: exactly one wake-log
        # line, naming both the project and the source.
        log_lines = [line for line in result.stdout.splitlines() if _LOG_TAG in line]
        assert len(log_lines) == 1, result.stdout
        assert "demo" in log_lines[0]
        assert "halt:project" in log_lines[0]
    finally:
        metrics_dir.chmod(0o755)


# ---------------------------------------------------------------------------
# Cause, reason_code and class on each interval
# ---------------------------------------------------------------------------

# CM's HALT file shape (release_preflight.sh write_halt_file): every line of
# the dual-channel block is comment-prefixed.
_CM_HALT_TEXT = (
    "# HALT created by CM at 2026-10-18T10:00:00Z\n"
    "# error:   release.example-key\n"
    "# message: an example release trigger stopped the chain\n"
    "# remedy:  cat example\n"
    "# Resolution: Operator review required. See CM task status.md for full reason.\n"
)


def _sentinel_record(halt: Path, op: str, epoch: int = 1791500000) -> str:
    """One sentinel-writes log line, in the writer's own field order; its
    sha256 is the HALT's digest as it is now, as the writer records it."""
    import hashlib

    digest = hashlib.sha256(halt.read_bytes()).hexdigest() if halt.exists() else "absent"
    values = {
        "time": "2026-10-08T22:53:20+00:00", "epoch": str(epoch), "op": op, "path": str(halt),
        "sha256": digest, "entry": "python3 -m pgai_agent_kanban.ops " + op,
        "pid": "4242", "ppid": "4240", "parent": "bash halt-after.sh", "user": "op", "test": "-",
    }
    return "\t".join(f"{name}={values[name]}" for name in FIELDS) + "\n"


def _start_project_halt(tmp_path: Path, root: Path, project: str = "demo"):
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        f'pgai_stamp_halt_boundary {project} "halt:project" start\n'
    )
    return run_bash(tmp_path, script)


@pytest.mark.parametrize(
    "halt_text",
    [_CM_HALT_TEXT, "error: release.example-key\n"],
    ids=["cm-comment-prefixed", "bare-key"],
)
def test_chain_project_halt_records_cause_chain_and_its_error_key(tmp_path: Path, halt_text: str) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3"})
    halt = root / "projects" / "demo" / "HALT"
    halt.write_text(halt_text, encoding="utf-8")
    # A sentinel-writes log with a record for another path only: nothing
    # names this HALT, so the chain raised it.
    other = root / "projects" / "demo" / "HALT-AFTER"
    log = log_path(root)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(_sentinel_record(other, "halt_after"), encoding="utf-8")

    result = _start_project_halt(tmp_path, root)
    assert result.returncode == 0, result.stderr

    records = _ledger_lines(root, "demo")
    assert len(records) == 1, records
    assert records[0]["kind"] == "block-start"
    assert records[0]["cause"] == "chain"
    assert records[0]["reason_code"] == "release.example-key"
    assert records[0]["version"] == "v1.2.3"
    assert _LOG_TAG not in result.stdout, result.stdout


def test_promoted_project_halt_records_cause_operator(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3"})
    halt = root / "projects" / "demo" / "HALT"
    halt.write_text(_CM_HALT_TEXT, encoding="utf-8")
    log = log_path(root)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(_sentinel_record(halt, "promote"), encoding="utf-8")

    result = _start_project_halt(tmp_path, root)
    assert result.returncode == 0, result.stderr

    records = _ledger_lines(root, "demo")
    assert [(r["kind"], r["cause"], r["reason_code"]) for r in records] == [
        ("block-start", "operator", "release.example-key")
    ]


def test_project_halt_without_an_error_key_records_the_source_label(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3"})
    (root / "projects" / "demo" / "HALT").write_text("# operator full-stop for a hand patch\n", encoding="utf-8")

    result = _start_project_halt(tmp_path, root)
    assert result.returncode == 0, result.stderr

    records = _ledger_lines(root, "demo")
    assert records[0]["reason_code"] == "halt_project"
    assert records[0]["cause"] == "chain"


def test_global_halt_records_cause_operator_for_every_project(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"alpha": "v2.0.0", "beta": "none"})
    (root / "HALT").write_text("# operator stopping every project\n", encoding="utf-8")
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_fleet_halt_boundary "halt:global" start\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr

    for project, version in (("alpha", "v2.0.0"), ("beta", NO_RC_VERSION)):
        records = _ledger_lines(root, project)
        assert [(r["kind"], r["source"], r["version"], r["cause"], r["reason_code"]) for r in records] == [
            ("block-start", "halt:global", version, "operator", "halt_global")
        ], project


def test_end_records_class_any_and_halt_cleared(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_halt_boundary demo "halt:project" start\n'
        'pgai_stamp_halt_boundary demo "halt:project" end\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr

    end = _ledger_lines(root, "demo")[-1]
    assert (end["kind"], end["class"], end["reason_code"]) == ("block-end", "any", "halt_cleared")
    (block,) = block_intervals(str(root / "projects" / "demo" / "metrics" / "blocked-intervals.jsonl"))
    assert block["class"] == "any" and block["cause"] == "chain" and block["end"] is not None


def test_end_after_unhalt_closed_the_block_adds_nothing(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3"})
    ledger = root / "projects" / "demo" / "metrics" / "blocked-intervals.jsonl"
    ledger.parent.mkdir(parents=True)
    # What unhalt leaves: the chain HALT's block closed, class human.
    ledger.write_text(
        json.dumps({"cause": "chain", "kind": "block-start", "source": "halt:project",
                    "timestamp": "2026-10-18T10:00:00+00:00", "version": "v1.2.3"}, sort_keys=True) + "\n"
        + json.dumps({"class": "human", "kind": "block-end", "reason_code": "unhalt", "source": "halt:project",
                      "timestamp": "2026-10-18T11:00:00+00:00", "version": "v1.2.3"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before = ledger.read_text(encoding="utf-8")
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_halt_boundary demo "halt:project" end\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert ledger.read_text(encoding="utf-8") == before
    assert _LOG_TAG not in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# A HALT that spans an RC opening or closing
# ---------------------------------------------------------------------------


def test_halt_spanning_an_rc_opening_closes_both_intervals(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "none"})
    project_dir = root / "projects" / "demo"
    preamble = _LIB_SOURCE_PREAMBLE.format(kanban_root=root)
    first = run_bash(tmp_path, preamble + 'pgai_stamp_halt_boundary demo "halt:project" start\n')
    assert first.returncode == 0, first.stderr
    _write_release_state(project_dir, active_rc="v1.3.0")
    second = run_bash(
        tmp_path,
        preamble
        + 'pgai_stamp_halt_boundary demo "halt:project" start\n'
        + 'pgai_stamp_halt_boundary demo "halt:project" end\n',
    )
    assert second.returncode == 0, second.stderr

    records = _ledger_lines(root, "demo")
    assert [(r["kind"], r["version"]) for r in records] == [
        ("block-start", NO_RC_VERSION),
        ("block-start", "v1.3.0"),
        ("block-end", "v1.3.0"),
        ("block-end", NO_RC_VERSION),
    ]
    # The RC's own interval opens on the first cycle the RC is seen and
    # closes on the cycle the HALT is gone, as it always has.
    assert records[1]["timestamp"] <= records[2]["timestamp"]
    blocks = block_intervals(str(project_dir / "metrics" / "blocked-intervals.jsonl"))
    assert all(b["end"] is not None for b in blocks)
    assert _LOG_TAG not in second.stdout, second.stdout


def test_halt_spanning_an_rc_closing_closes_the_released_rc_interval(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.3.0"})
    project_dir = root / "projects" / "demo"
    preamble = _LIB_SOURCE_PREAMBLE.format(kanban_root=root)
    first = run_bash(tmp_path, preamble + 'pgai_stamp_halt_boundary demo "halt:project" start\n')
    assert first.returncode == 0, first.stderr
    _write_release_state(project_dir, active_rc="none", last_released="v1.3.0")
    second = run_bash(tmp_path, preamble + 'pgai_stamp_halt_boundary demo "halt:project" end\n')
    assert second.returncode == 0, second.stderr

    records = _ledger_lines(root, "demo")
    assert [(r["kind"], r["version"]) for r in records] == [("block-start", "v1.3.0"), ("block-end", "v1.3.0")]
    assert _LOG_TAG not in second.stdout, second.stdout


def test_end_for_a_project_with_no_halt_history_makes_no_ledger_call(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3", "idle": "none"})
    ledger = root / "projects" / "demo" / "metrics" / "blocked-intervals.jsonl"
    ledger.parent.mkdir(parents=True)
    # A ledger holding only a task block: it never names halt:project.
    ledger.write_text(
        json.dumps({"kind": "block-start", "source": "task:EXAMPLE-1", "timestamp": "2026-10-18T10:00:00+00:00",
                    "version": "v1.2.3"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'eval "$(declare -f pp_run_ops | sed \'1s/^pp_run_ops/_real_pp_run_ops/\')"\n'
        'pp_run_ops() { echo "PP_RUN_OPS_CALLED $*"; _real_pp_run_ops "$@"; }\n'
        'pgai_stamp_halt_boundary demo "halt:project" end\n'
        'pgai_stamp_halt_boundary idle "halt:project" end\n'
        'pgai_stamp_fleet_halt_boundary "halt:global" end\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert "PP_RUN_OPS_CALLED" not in result.stdout, result.stdout
    assert not (root / "projects" / "idle" / "metrics").exists()
