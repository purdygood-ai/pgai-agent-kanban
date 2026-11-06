"""
test_dispatch_ledger_module.py
==============================
The per-task dispatch ledger's write and latest read, which
overwatch_protocol.sh's dispatch_record_append and dispatch_record_latest
call through pp_run_ops: ``pgai_agent_kanban.lib.dispatch_ledger``.

Covered behaviour:

  * a record stores digit fields as integers and empty or non-digit
    offset_end / exit_code as null, with a non-digit offset_start read as
    0, and a UTC ``...Z`` timestamp;
  * an append adds one sorted-key JSON line and keeps the earlier lines;
  * the latest read returns the newest usable record, skipping blank
    lines, torn JSON, non-object JSON and records with no batch_log;
  * negative or non-integer offsets read as ``0`` / open (``""``);
  * a ledger with no usable record reads as None, and a missing ledger
    raises so the caller can tell it apart;
  * the command line prints nothing on append, one tab-separated line on
    latest, exits 1 silently for a missing or empty ledger, and names the
    ledger on stderr when it cannot be written.

Every ledger is written under pytest's tmp_path. Subprocesses run with an
environment built from an explicit set and a throwaway HOME.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import dispatch_ledger
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MODULE = "pgai_agent_kanban.lib.dispatch_ledger"


def _run_module(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    return subprocess.run(
        [sys.executable, "-m", _MODULE, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


# ---------------------------------------------------------------------------
# Record and append
# ---------------------------------------------------------------------------


def test_digit_fields_are_stored_as_integers() -> None:
    now = datetime.datetime(2026, 1, 2, 3, 4, 5, 678, tzinfo=datetime.UTC)
    record = dispatch_ledger.dispatch_record(
        "returned",
        "coder",
        "claude",
        "/logs/coder-batch.log",
        "120",
        "300",
        "1",
        now=now,
    )
    assert record == {
        "recorded_at": "2026-01-02T03:04:05Z",
        "event": "returned",
        "agent": "coder",
        "provider": "claude",
        "batch_log": "/logs/coder-batch.log",
        "offset_start": 120,
        "offset_end": 300,
        "exit_code": 1,
    }


@pytest.mark.parametrize("raw", ["", "  ", "abc", "-1", "1.5", "12x"])
def test_non_digit_fields_are_stored_as_null(raw: str) -> None:
    record = dispatch_ledger.dispatch_record("dispatched", "coder", "claude", "/b.log", raw, raw, raw)
    assert record["offset_end"] is None
    assert record["exit_code"] is None
    assert record["offset_start"] == 0, "an unmeasured start offset is recorded as 0"


def test_surrounding_whitespace_around_digits_is_tolerated() -> None:
    assert dispatch_ledger.as_int(" 7 ") == 7
    assert dispatch_ledger.as_int(None) is None


def test_append_writes_sorted_json_lines_and_keeps_earlier_ones(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    first = dispatch_ledger.dispatch_record("dispatched", "coder", "claude", "/b.log", "5")
    second = dispatch_ledger.dispatch_record("returned", "coder", "claude", "/b.log", "5", "9", "0")

    dispatch_ledger.append_dispatch_record(str(ledger), first)
    dispatch_ledger.append_dispatch_record(str(ledger), second)

    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert [json.loads(ln) for ln in lines] == [first, second]
    assert all(ln == json.dumps(json.loads(ln), sort_keys=True) for ln in lines)


# ---------------------------------------------------------------------------
# Latest read
# ---------------------------------------------------------------------------


def test_the_newest_record_wins(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    for record in (
        {"batch_log": "/old.log", "offset_start": 1, "offset_end": 2},
        {"batch_log": "/new.log", "offset_start": 30, "offset_end": 45},
    ):
        dispatch_ledger.append_dispatch_record(str(ledger), record)
    assert dispatch_ledger.latest_dispatch_span(str(ledger)) == ("/new.log", "30", "45")


def test_blank_and_malformed_lines_are_skipped(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    ledger.write_text(
        "\n".join(
            [
                json.dumps({"batch_log": "/good.log", "offset_start": 10, "offset_end": None}),
                "",
                "   ",
                '{"batch_log": "/torn.log", "offset_st',
                "[1, 2]",
                json.dumps({"batch_log": "", "offset_start": 99}),
                json.dumps({"event": "returned"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert dispatch_ledger.latest_dispatch_span(str(ledger)) == ("/good.log", "10", "")


def test_negative_or_non_integer_offsets_read_as_start_and_open(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    ledger.write_text(
        json.dumps({"batch_log": "/b.log", "offset_start": -4, "offset_end": "9"}) + "\n",
        encoding="utf-8",
    )
    assert dispatch_ledger.latest_dispatch_span(str(ledger)) == ("/b.log", "0", "")


def test_ledger_with_no_usable_record_reads_none(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    ledger.write_text("\nnot json\n", encoding="utf-8")
    assert dispatch_ledger.latest_dispatch_span(str(ledger)) is None


def test_missing_ledger_raises(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FileNotFoundError):
        dispatch_ledger.latest_dispatch_span(str(tmp_path / "missing.jsonl"))


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def test_append_and_latest_commands_round_trip(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    opened = _run_module(
        tmp_path,
        "append",
        "--",
        str(ledger),
        "dispatched",
        "coder",
        "claude",
        "/b.log",
        "17",
        "",
        "",
    )
    assert (opened.returncode, opened.stdout, opened.stderr) == (0, "", "")
    latest = _run_module(tmp_path, "latest", "--", str(ledger))
    assert (latest.returncode, latest.stdout, latest.stderr) == (0, "/b.log\t17\t\n", "")

    closed = _run_module(
        tmp_path,
        "append",
        "--",
        str(ledger),
        "returned",
        "coder",
        "claude",
        "/b.log",
        "17",
        "40",
        "2",
    )
    assert closed.returncode == 0, closed.stderr
    latest = _run_module(tmp_path, "latest", "--", str(ledger))
    assert latest.stdout == "/b.log\t17\t40\n"
    assert [json.loads(ln)["event"] for ln in ledger.read_text(encoding="utf-8").splitlines()] == [
        "dispatched",
        "returned",
    ]


def test_latest_command_exits_one_silently_without_a_usable_record(tmp_path: pathlib.Path) -> None:
    missing = _run_module(tmp_path, "latest", "--", str(tmp_path / "missing.jsonl"))
    assert (missing.returncode, missing.stdout, missing.stderr) == (1, "", "")
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    result = _run_module(tmp_path, "latest", "--", str(empty))
    assert (result.returncode, result.stdout, result.stderr) == (1, "", "")


def test_append_command_names_the_ledger_it_cannot_write(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "no-such-dir" / "dispatch.jsonl"
    result = _run_module(
        tmp_path,
        "append",
        "--",
        str(ledger),
        "dispatched",
        "coder",
        "claude",
        "/b.log",
        "0",
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith(f"dispatch_record_append: cannot write {ledger}: "), result.stderr
    assert "Traceback" not in result.stderr
