"""
test_green_ledger_module.py
===========================
The two ledger-file steps team/scripts/lib/green_ledger.sh calls through
pp_run_ops: ``pgai_agent_kanban.testing.green_ledger``'s reuse verdict and
record append.

Covered behaviour:

  * the verdict is ``norecord`` when no record matches the commit, suite
    and marker selection, ``reuse`` (with the record's recorded_at) for a
    matching green run on a clean tree with the same findings hash, and
    ``mismatch`` for a matching run that exited non-zero, ran on a dirty
    tree or carries another findings hash -- always judged on the most
    recent matching record;
  * a non-blank line that is not JSON, or JSON missing a required key,
    makes the verdict ``corrupt`` naming that line; blank lines are skipped;
  * a file that cannot be read (undecodable bytes, a directory) is
    ``corrupt`` naming the exception class;
  * the append creates the ledger and its directory, records every field
    with the types the reader expects, appends rather than rewrites, moves
    a malformed ledger aside with a note, and raises on a non-integer exit
    code;
  * the command line prints ``<verdict>\\t<detail>`` and ``ok\\t<note>`` /
    ``failed\\t<reason>`` lines and exits 0.

Every ledger is written under pytest's tmp_path. Subprocesses run with an
environment built from an explicit set and a throwaway HOME.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from pgai_agent_kanban.testing import green_ledger
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MODULE = "pgai_agent_kanban.testing.green_ledger"

_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_SHA = "aaaabbbbccccddddeeeeffff00001111aaaabbbbccccddddeeeeffff00001111"
_SELECTION = "not quarantine"


def _record(**overrides: object) -> dict:
    record = {
        "recorded_at": "2026-01-01T00:00:00+00:00",
        "commit": _COMMIT,
        "suite": "unit",
        "selection": _SELECTION,
        "exit": 0,
        "findings_sha": _SHA,
        "tree_clean": True,
    }
    record.update(overrides)
    return record


def _write_ledger(path: pathlib.Path, *lines: object) -> pathlib.Path:
    text = "".join(
        (line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines
    )
    path.write_text(text, encoding="utf-8")
    return path


def _verdict(path: pathlib.Path, *, suite: str = "unit", selection: str = _SELECTION,
             sha: str = _SHA) -> tuple:
    return green_ledger.reuse_verdict(str(path), _COMMIT, suite, selection, sha)


def _run_module(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    return subprocess.run(
        [sys.executable, "-m", _MODULE, *args],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60, check=False,
    )


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def test_no_matching_record_is_norecord(tmp_path: pathlib.Path) -> None:
    ledger = _write_ledger(
        tmp_path / "ledger.jsonl",
        _record(commit="f" * 40),
        _record(suite="integration"),
        _record(selection="quarantine"),
    )
    assert _verdict(ledger) == (
        "norecord",
        f"no recorded unit run at commit {_COMMIT[:12]} with marker selection \"{_SELECTION}\"",
    )


def test_matching_green_record_is_reused_with_its_timestamp(tmp_path: pathlib.Path) -> None:
    ledger = _write_ledger(tmp_path / "ledger.jsonl", _record(recorded_at="2026-02-03T04:05:06+00:00"))
    assert _verdict(ledger) == ("reuse", "2026-02-03T04:05:06+00:00")


def test_matching_record_without_a_timestamp_reads_unknown_time(tmp_path: pathlib.Path) -> None:
    record = _record()
    del record["recorded_at"]
    ledger = _write_ledger(tmp_path / "ledger.jsonl", record)
    assert _verdict(ledger) == ("reuse", "at an unknown time")


def test_record_without_selection_matches_an_empty_selection(tmp_path: pathlib.Path) -> None:
    record = _record()
    del record["selection"]
    ledger = _write_ledger(tmp_path / "ledger.jsonl", record)
    assert _verdict(ledger, selection="")[0] == "reuse"
    assert _verdict(ledger)[0] == "norecord"


def test_matching_failed_run_is_a_mismatch(tmp_path: pathlib.Path) -> None:
    ledger = _write_ledger(tmp_path / "ledger.jsonl", _record(), _record(exit=2))
    assert _verdict(ledger) == (
        "mismatch", f"the most recent unit run at commit {_COMMIT[:12]} exited 2",
    )


def test_the_most_recent_matching_record_decides(tmp_path: pathlib.Path) -> None:
    ledger = _write_ledger(
        tmp_path / "ledger.jsonl",
        _record(exit=1),
        _record(recorded_at="2026-05-05T05:05:05+00:00"),
        _record(commit="e" * 40, exit=1),
    )
    assert _verdict(ledger) == ("reuse", "2026-05-05T05:05:05+00:00")


def test_matching_run_on_a_dirty_tree_is_a_mismatch(tmp_path: pathlib.Path) -> None:
    for tree_clean in (False, "true", None):
        ledger = _write_ledger(tmp_path / "ledger.jsonl", _record(tree_clean=tree_clean))
        assert _verdict(ledger) == (
            "mismatch",
            f"the most recent unit run at commit {_COMMIT[:12]} ran on a tree with uncommitted changes",
        ), tree_clean


def test_different_findings_hash_is_a_mismatch(tmp_path: pathlib.Path) -> None:
    ledger = _write_ledger(tmp_path / "ledger.jsonl", _record(findings_sha="9" * 64))
    assert _verdict(ledger) == (
        "mismatch",
        f"the lint findings hash differs from the recorded run at commit {_COMMIT[:12]} "
        f"({'9' * 12} recorded, {_SHA[:12]} now)",
    )


def test_malformed_lines_make_the_ledger_corrupt_and_blank_lines_are_skipped(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    _write_ledger(path, "", _record(), "   ", "{not json")
    assert _verdict(path) == ("corrupt", f"ledger {path} line 4 is not a ledger record")

    missing_key = _record()
    del missing_key["findings_sha"]
    _write_ledger(path, _record(), missing_key)
    assert _verdict(path) == ("corrupt", f"ledger {path} line 2 is not a ledger record")

    _write_ledger(path, "[1, 2, 3]")
    assert _verdict(path) == ("corrupt", f"ledger {path} line 1 is not a ledger record")

    _write_ledger(path, "", _record(), "")
    assert _verdict(path)[0] == "reuse"


def test_unreadable_ledger_is_corrupt_naming_the_error(tmp_path: pathlib.Path) -> None:
    undecodable = tmp_path / "ledger.jsonl"
    undecodable.write_bytes(b"\xff\xfe not utf-8\n")
    assert _verdict(undecodable) == (
        "corrupt", f"ledger {undecodable} is unreadable (UnicodeDecodeError)",
    )
    as_directory = tmp_path / "dir.jsonl"
    as_directory.mkdir()
    assert _verdict(as_directory) == (
        "corrupt", f"ledger {as_directory} is unreadable (IsADirectoryError)",
    )


# ---------------------------------------------------------------------------
# Append
# ---------------------------------------------------------------------------


def test_append_creates_the_ledger_and_records_every_field(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "projects" / "demo" / "green-ledger" / "v1.2.3" / "ledger.jsonl"
    now = datetime(2026, 3, 4, 5, 6, 7, 890, tzinfo=timezone.utc)

    note = green_ledger.append_record(
        str(ledger), _COMMIT, "integration", _SELECTION, "3", _SHA, True, now=now,
    )

    assert note == ""
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "recorded_at": "2026-03-04T05:06:07+00:00",
        "commit": _COMMIT,
        "suite": "integration",
        "selection": _SELECTION,
        "exit": 3,
        "findings_sha": _SHA,
        "tree_clean": True,
    }
    assert lines[0] == json.dumps(json.loads(lines[0]), sort_keys=True), "keys are written sorted"


def test_append_adds_a_line_the_verdict_reads_back(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    _write_ledger(ledger, _record(exit=1))

    note = green_ledger.append_record(str(ledger), _COMMIT, "unit", _SELECTION, 0, _SHA, True)

    assert note == ""
    records = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines()]
    assert [r["exit"] for r in records] == [1, 0]
    assert _verdict(ledger)[0] == "reuse"


def test_append_records_a_dirty_tree_as_false(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    green_ledger.append_record(str(ledger), _COMMIT, "unit", _SELECTION, 0, _SHA, False)
    assert json.loads(ledger.read_text(encoding="utf-8"))["tree_clean"] is False


def test_append_moves_a_malformed_ledger_aside(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("garbage\n", encoding="utf-8")

    note = green_ledger.append_record(str(ledger), _COMMIT, "unit", _SELECTION, 0, _SHA, True)

    assert note == f"; the malformed ledger was moved aside to {ledger}.malformed"
    assert (tmp_path / "ledger.jsonl.malformed").read_text(encoding="utf-8") == "garbage\n"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_append_rejects_a_non_integer_exit_code(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(ValueError):
        green_ledger.append_record(str(ledger), _COMMIT, "unit", _SELECTION, "abc", _SHA, True)
    assert not ledger.exists()


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def test_verdict_command_prints_one_tab_separated_line(tmp_path: pathlib.Path) -> None:
    ledger = _write_ledger(tmp_path / "ledger.jsonl", _record())
    result = _run_module(tmp_path, "verdict", "--", str(ledger), _COMMIT, "unit", _SELECTION, _SHA)
    assert (result.returncode, result.stderr) == (0, ""), result.stderr
    assert result.stdout == "reuse\t2026-01-01T00:00:00+00:00\n"


def test_record_command_prints_ok_and_failed_lines(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "sub" / "ledger.jsonl"
    ok = _run_module(
        tmp_path, "record", "--", str(ledger), _COMMIT, "unit", _SELECTION, "0", _SHA, "true",
    )
    assert (ok.returncode, ok.stdout, ok.stderr) == (0, "ok\t\n", "")
    assert json.loads(ledger.read_text(encoding="utf-8"))["tree_clean"] is True

    failed = _run_module(
        tmp_path, "record", "--", str(ledger), _COMMIT, "unit", _SELECTION, "x", _SHA, "true",
    )
    assert failed.returncode == 0, failed.stderr
    assert failed.stdout == "failed\tValueError: invalid literal for int() with base 10: 'x'\n"
