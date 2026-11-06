"""
test_sentinel_cause.py
======================
Unit tests for pgai_agent_kanban.lib.sentinel_provenance.sentinel_cause --
the read-only helper that says whether a HALT sentinel was raised by an
operator or by the chain, from the sentinel-writes log.

Coverage:
  - the latest record for the path having an operator op (halt, promote,
    and the rest of OPERATOR_OPS) reads as operator;
  - a log with no record for the path, and an absent log, read as chain;
  - a HALT directly under the kanban root reads as operator with no log;
  - records after the time asked about are ignored, and the latest record
    at or before it decides (a later non-operator op reads as chain);
  - a record written by the real writer, record_sentinel_write, is read;
  - the helper never creates or changes the log;
  - both results are values of the blocked ledger's cause vocabulary;
  - the ``cause`` command line prints the same answer and exits 0, and
    refuses an --at that is not ISO-8601 as a usage error.

Every log and sentinel path lives under tmp_path; nothing reads or writes
the live kanban root.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from pgai_agent_kanban.lib.sentinel_provenance import (
    CAUSE_CHAIN,
    main,
    CAUSE_OPERATOR,
    FIELDS,
    OPERATOR_OPS,
    log_path,
    record_sentinel_write,
    sentinel_cause,
)
from pgai_agent_kanban.metrics.blocked_ledger import VALID_CAUSES

_EPOCH_EARLY = 1791500000  # 2026-10-08T22:53:20+00:00
_EPOCH_LATE = _EPOCH_EARLY + 3600


def _kanban_root(tmp_path: Path) -> Path:
    root = tmp_path / "kanban"
    (root / "projects" / "alpha").mkdir(parents=True)
    return root


def _project_halt(root: Path) -> Path:
    """The project HALT, in place on disk (a record describes it by digest)."""
    halt = root / "projects" / "alpha" / "HALT"
    halt.write_text("error: test.halt\n", encoding="utf-8")
    return halt


def _digest_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


def _log_line(path: Path, op: str, epoch: int, sha256: str | None = None) -> str:
    """One record line in the writer's own field order; *sha256* defaults to
    the digest of *path* as it is now, as the writer records it."""
    time = dt.datetime.fromtimestamp(epoch, dt.UTC).isoformat(timespec="seconds")
    values = {
        "time": time,
        "epoch": str(epoch),
        "op": op,
        "path": str(path),
        "sha256": _digest_of(path) if sha256 is None else sha256,
        "entry": "python3 -m pgai_agent_kanban.ops " + op,
        "pid": "4242",
        "ppid": "4240",
        "parent": "bash halt.sh",
        "user": "op",
        "test": "-",
    }
    return "\t".join(f"{name}={values[name]}" for name in FIELDS) + "\n"


def _write_log(root: Path, *lines: str) -> Path:
    target = log_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(lines), encoding="utf-8")
    return target


@pytest.mark.parametrize("op", ["halt", "promote"])
def test_latest_operator_op_record_reads_as_operator(tmp_path: Path, op: str) -> None:
    root = _kanban_root(tmp_path)
    halt = _project_halt(root)
    _write_log(root, _log_line(halt, op, _EPOCH_EARLY))

    assert sentinel_cause(halt, kanban_root=root) == CAUSE_OPERATOR


@pytest.mark.parametrize("op", sorted(OPERATOR_OPS))
def test_every_operator_op_reads_as_operator(tmp_path: Path, op: str) -> None:
    root = _kanban_root(tmp_path)
    halt = _project_halt(root)
    _write_log(root, _log_line(halt, op, _EPOCH_EARLY))

    assert sentinel_cause(halt, _EPOCH_EARLY, kanban_root=root) == CAUSE_OPERATOR


def test_log_with_no_record_for_the_path_reads_as_chain(tmp_path: Path) -> None:
    root = _kanban_root(tmp_path)
    other = root / "projects" / "alpha" / "HALT-AFTER"
    _write_log(root, _log_line(other, "halt_after", _EPOCH_EARLY))

    assert sentinel_cause(_project_halt(root), kanban_root=root) == CAUSE_CHAIN


def test_absent_log_reads_as_chain_and_is_not_created(tmp_path: Path) -> None:
    root = _kanban_root(tmp_path)

    assert sentinel_cause(_project_halt(root), kanban_root=root) == CAUSE_CHAIN
    assert not log_path(root).exists()


def test_kanban_root_halt_reads_as_operator_without_a_log(tmp_path: Path) -> None:
    root = _kanban_root(tmp_path)

    assert sentinel_cause(root / "HALT") == CAUSE_OPERATOR
    assert sentinel_cause(root / "HALT", kanban_root=root) == CAUSE_OPERATOR
    assert not log_path(root).exists()


def test_kanban_root_is_derived_from_a_project_sentinel_path(tmp_path: Path) -> None:
    root = _kanban_root(tmp_path)
    halt = _project_halt(root)
    _write_log(root, _log_line(halt, "halt", _EPOCH_EARLY))

    assert sentinel_cause(halt) == CAUSE_OPERATOR


def test_records_after_the_time_asked_about_are_ignored(tmp_path: Path) -> None:
    root = _kanban_root(tmp_path)
    halt = _project_halt(root)
    _write_log(root, _log_line(halt, "halt", _EPOCH_LATE))

    assert sentinel_cause(halt, _EPOCH_EARLY, kanban_root=root) == CAUSE_CHAIN
    assert sentinel_cause(halt, _EPOCH_LATE, kanban_root=root) == CAUSE_OPERATOR


def test_time_asked_about_accepts_iso_string_and_datetime(tmp_path: Path) -> None:
    root = _kanban_root(tmp_path)
    halt = _project_halt(root)
    _write_log(root, _log_line(halt, "halt", _EPOCH_LATE))
    before = dt.datetime.fromtimestamp(_EPOCH_EARLY, dt.UTC)
    after = dt.datetime.fromtimestamp(_EPOCH_LATE, dt.UTC)

    assert sentinel_cause(halt, before.isoformat(), kanban_root=root) == CAUSE_CHAIN
    assert sentinel_cause(halt, after.isoformat(), kanban_root=root) == CAUSE_OPERATOR
    assert sentinel_cause(halt, before, kanban_root=root) == CAUSE_CHAIN
    assert sentinel_cause(halt, after, kanban_root=root) == CAUSE_OPERATOR


def test_unparseable_time_string_raises_value_error_naming_the_value(tmp_path: Path) -> None:
    root = _kanban_root(tmp_path)

    with pytest.raises(ValueError, match="not-a-time"):
        sentinel_cause(_project_halt(root), "not-a-time", kanban_root=root)


def test_latest_record_decides_when_a_non_operator_op_follows(tmp_path: Path) -> None:
    root = _kanban_root(tmp_path)
    halt = _project_halt(root)
    _write_log(
        root,
        _log_line(halt, "halt", _EPOCH_EARLY),
        _log_line(halt, "defer", _EPOCH_LATE),
    )

    assert sentinel_cause(halt, _EPOCH_EARLY, kanban_root=root) == CAUSE_OPERATOR
    assert sentinel_cause(halt, kanban_root=root) == CAUSE_CHAIN


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    root = _kanban_root(tmp_path)
    halt = _project_halt(root)
    _write_log(
        root,
        "not a record\n",
        f"op=halt\tpath={halt}\tepoch=not-a-number\ttime=garbage\n",
        _log_line(halt, "promote", _EPOCH_EARLY),
    )

    assert sentinel_cause(halt, kanban_root=root) == CAUSE_OPERATOR


def test_record_from_the_real_writer_reads_as_operator_and_the_log_is_unchanged(
    tmp_path: Path,
) -> None:
    root = _kanban_root(tmp_path)
    halt = _project_halt(root)
    assert record_sentinel_write(root, halt, "halt") is True
    before = log_path(root).read_bytes()

    assert sentinel_cause(halt, kanban_root=root) == CAUSE_OPERATOR
    assert log_path(root).read_bytes() == before


def test_results_are_values_of_the_ledger_cause_vocabulary() -> None:
    assert {CAUSE_CHAIN, CAUSE_OPERATOR} == set(VALID_CAUSES)


def test_cause_command_prints_the_answer_and_exits_zero(tmp_path: Path, capsys) -> None:
    root = _kanban_root(tmp_path)
    halt = _project_halt(root)

    assert main(["cause", str(halt), "--kanban-root", str(root)]) == 0
    assert capsys.readouterr().out.strip() == CAUSE_CHAIN

    _write_log(root, _log_line(halt, "promote", _EPOCH_EARLY))
    assert main(["cause", str(halt)]) == 0
    assert capsys.readouterr().out.strip() == CAUSE_OPERATOR

    assert main(["cause", str(halt), "--at", "2026-10-08T00:00:00+00:00"]) == 0
    assert capsys.readouterr().out.strip() == CAUSE_CHAIN


def test_cause_command_refuses_an_unparseable_at_as_a_usage_error(tmp_path: Path, capsys) -> None:
    root = _kanban_root(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(["cause", str(_project_halt(root)), "--at", "not-a-time"])
    assert excinfo.value.code == 2
    assert "ISO-8601" in capsys.readouterr().err
