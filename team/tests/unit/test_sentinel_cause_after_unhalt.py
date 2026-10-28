"""
test_sentinel_cause_after_unhalt.py
===================================
A project HALT the chain writes after an operator's HALT was raised and
cleared reads as the chain's (pgai_agent_kanban.lib.sentinel_provenance
sentinel_cause, and ops.write.unhalt which records its removal):

  1. Operator halt, then unhalt, then a direct (chain) write of the HALT:
     sentinel_cause returns chain.
  2. An operator halt still in place returns operator.
  3. An operator record whose digest no longer matches the HALT on disk
     (the log missed the removal) returns chain.
  4. Through ops.write.unhalt: the chain HALT from case 1, stamped with
     sentinel_cause as the wake adapter stamps it, closes class=human
     cause=chain, and reliability() reports it as the last block of both
     the Hands-off and the Clean streak.

Every root lives under tmp_path; nothing reads or writes the live install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pgai_agent_kanban.lib.sentinel_provenance import (
    CAUSE_CHAIN,
    CAUSE_OPERATOR,
    log_path,
    sentinel_cause,
)
from pgai_agent_kanban.metrics.blocked_ledger import (
    append_block_start,
    block_intervals,
    ledger_path_for_project,
)
from pgai_agent_kanban.metrics.reliability import reliability
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.context import OpsContext

_PROJECT = "p1"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "kanban"
    project = root / "projects" / _PROJECT
    (project / "tasks").mkdir(parents=True)
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\n", encoding="utf-8")
    (project / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nv1.0.0\n",
        encoding="utf-8",
    )
    return root


def _halt(root: Path) -> Path:
    return root / "projects" / _PROJECT / "HALT"


def _chain_writes_halt(root: Path) -> Path:
    """Write the project HALT as a chain writer does: directly, no record."""
    halt = _halt(root)
    halt.write_text("error: cm.release_trigger\n", encoding="utf-8")
    return halt


def test_a_chain_halt_after_an_operator_halt_and_unhalt_reads_as_chain(root: Path) -> None:
    ctx = OpsContext(kanban_root=root)
    ops_write.halt(ctx, _PROJECT)
    ops_write.unhalt(ctx, _PROJECT)
    assert not _halt(root).exists()
    assert "op=unhalt\t" in log_path(root).read_text(encoding="utf-8")

    halt = _chain_writes_halt(root)

    assert sentinel_cause(halt) == CAUSE_CHAIN


def test_an_operator_halt_still_in_place_reads_as_operator(root: Path) -> None:
    ops_write.halt(OpsContext(kanban_root=root), _PROJECT)

    assert sentinel_cause(_halt(root)) == CAUSE_OPERATOR


def test_an_operator_record_that_no_longer_describes_the_halt_reads_as_chain(root: Path) -> None:
    """The log holds the operator's halt but not its removal (an older
    unhalt, or a hand removal); the HALT on disk now is the chain's."""
    ops_write.halt(OpsContext(kanban_root=root), _PROJECT)
    _halt(root).unlink()
    halt = _chain_writes_halt(root)

    assert sentinel_cause(halt) == CAUSE_CHAIN
    # Asked about a time before the chain wrote it, the record still answers.
    assert sentinel_cause(halt, at="2999-01-01T00:00:00+00:00") == CAUSE_OPERATOR


def test_unhalt_closes_the_chain_halt_as_human_chain_and_resets_both_streaks(root: Path) -> None:
    ctx = OpsContext(kanban_root=root)
    ops_write.halt(ctx, _PROJECT)
    ops_write.unhalt(ctx, _PROJECT)
    halt = _chain_writes_halt(root)
    ledger = ledger_path_for_project(str(root), _PROJECT)
    append_block_start(
        ledger, "no-rc", "halt:project", "2026-10-18T10:00:00+00:00",
        cause=sentinel_cause(halt), reason_code="cm.release_trigger",
    )

    ops_write.unhalt(ctx, _PROJECT)

    blocks = [b for b in block_intervals(ledger) if b["reason_code"] == "cm.release_trigger"]
    assert len(blocks) == 1, block_intervals(ledger)
    block = blocks[0]
    assert (block["class"], block["cause"]) == ("human", "chain"), block
    assert block["end"] is not None

    doc = reliability(_PROJECT, kanban_root=root)
    for streak in ("hands_off", "clean"):
        last = doc[streak]["last_block"]
        assert last is not None, doc[streak]
        assert last["reason_code"] == "cm.release_trigger" and last["cause"] == "chain", (streak, last)
