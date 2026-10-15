"""
test_block_record_action_args.py
================================
The typed action-argument vocabulary and the text-level record forms in
pgai_agent_kanban.overwatch.block_record:

  - parse_action_args reads liberally (any key case, a verb with ``.sh``,
    a version without ``v``, lower-case bug IDs, pairs split by spaces or
    ``;``) and returns canonical values in a fixed order;
  - it refuses, naming every fault, anything outside the closed list;
  - read_action_args gets the arguments back from a written record;
  - render_block_record / strip_block_record produce exactly what the file
    forms (write_block_record / remove_block_record) write;
  - ``codes --agent`` / ``codes --action-verbs`` list the closed vocabulary.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.overwatch.block_record import (
    ACTION_VERBS,
    AGENT_REASON_CODES,
    ActionArgsError,
    format_action_args,
    load_block_record,
    main,
    parse_action_args,
    read_action_args,
    remove_block_record,
    render_block_record,
    strip_block_record,
    write_block_record,
)

_STATUS = "# Status\n\n## State\nBLOCKED\n\n## Needs Human\nyes\n"
_NOW = "2026-01-01T00:00:00+00:00"


def test_parse_action_args_reads_liberally_and_writes_canonically() -> None:
    parsed = parse_action_args(["Verb=restamp_fixed_in.sh  PROJECT=pgai-agent-kanban", "version=2.28.0;bugs=bug-0412, "])
    assert parsed == {
        "verb": "restamp-fixed-in",
        "project": "pgai-agent-kanban",
        "version": "v2.28.0",
        "bugs": "BUG-0412",
    }
    assert format_action_args(parsed) == [
        "verb=restamp-fixed-in", "project=pgai-agent-kanban", "version=v2.28.0", "bugs=BUG-0412",
    ]


def test_parse_action_args_names_every_fault() -> None:
    with pytest.raises(ActionArgsError) as excinfo:
        parse_action_args(["verb=restamp-fixed-in", "project=Bad_Name", "bugs=12", "extra=1", "loose"])
    problems = " | ".join(excinfo.value.problems)
    for fragment in ("'loose' is not a key=value pair", "extra is not an argument", "project 'Bad_Name'",
                     "version= is missing", "bugs '12'"):
        assert fragment in problems


@pytest.mark.parametrize("values", [[], [""], ["   "]])
def test_parse_action_args_refuses_no_pairs(values: list[str]) -> None:
    with pytest.raises(ActionArgsError):
        parse_action_args(values)


def test_read_action_args_round_trips_through_a_written_record(tmp_path: pathlib.Path) -> None:
    status = tmp_path / "status.md"
    status.write_text(_STATUS, encoding="utf-8")
    args = parse_action_args(["verb=restamp-fixed-in project=demo version=v1.2.3 bugs=BUG-0001,BUG-0002"])
    write_block_record(status, "agent_operator_data_action", "CODER", format_action_args(args), now=_NOW)
    record = load_block_record(status)
    assert record is not None
    assert read_action_args(record) == args


def test_read_action_args_is_empty_without_an_allowlisted_verb(tmp_path: pathlib.Path) -> None:
    status = tmp_path / "status.md"
    status.write_text(_STATUS, encoding="utf-8")
    write_block_record(status, "per_task_timeout", "CODER", ["worktree=gone", "branch=ai_feature/x"], now=_NOW)
    record = load_block_record(status)
    assert record is not None and read_action_args(record) == {}


def test_render_and_strip_match_the_file_forms(tmp_path: pathlib.Path) -> None:
    status = tmp_path / "status.md"
    status.write_text(_STATUS, encoding="utf-8")
    rendered, record = render_block_record(_STATUS, "agent_unclassified", "WRITER", now=_NOW)
    write_block_record(status, "agent_unclassified", "WRITER", now=_NOW)
    assert status.read_text(encoding="utf-8") == rendered
    assert record.owner_role == "WRITER"
    assert strip_block_record(rendered) == _STATUS
    remove_block_record(status)
    assert status.read_text(encoding="utf-8") == _STATUS


def test_codes_lists_agent_codes_and_action_verbs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["codes", "--agent"]) == 0
    assert capsys.readouterr().out.split() == list(AGENT_REASON_CODES)
    assert main(["codes", "--action-verbs"]) == 0
    out = capsys.readouterr().out
    for verb, specs in ACTION_VERBS.items():
        assert f"verb={verb}" in out
        for spec in specs:
            assert f"{spec.name}=" in out
