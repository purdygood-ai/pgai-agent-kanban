"""
test_shared_record_and_marker_helpers.py
========================================
The shared helpers several modules now import instead of copying:

  1. dataclass_records.from_known_fields builds a record from a dict and
     ignores keys the record does not declare; every ledger record's
     from_dict goes through it.
  2. doc_markers extracts and replaces the block between two markers (both
     inclusive) and raises MarkerNotFoundError, a ValueError, naming the
     missing marker; the docs generators re-export that one class.
  3. env.resolve_kanban_root_or_default prefers an explicit root, then the
     variable, then ~/pgai_agent_kanban, read on every call.
  4. metrics.task_tokens.all_rc_versions lists each distinct RC once,
     'v'-prefixed.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import pathlib
import sys

import pytest

from pgai_agent_kanban.deep_verify.ledger import DeepVerifyRecord
from pgai_agent_kanban.env import resolve_kanban_root_or_default
from pgai_agent_kanban.lib.dataclass_records import from_known_fields
from pgai_agent_kanban.lib.doc_markers import MarkerNotFoundError, extract_between_markers, replace_between_markers
from pgai_agent_kanban.metrics.task_tokens import all_rc_versions
from pgai_agent_kanban.overwatch.ledger import LedgerEntry

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts"


@dataclasses.dataclass
class _Record:
    a: str
    b: int = 0


def test_from_known_fields_ignores_undeclared_keys() -> None:
    assert from_known_fields(_Record, {"a": "x", "b": 2, "later": True}) == _Record("x", 2)


def test_every_ledger_record_reads_through_the_shared_builder() -> None:
    record = DeepVerifyRecord.from_dict(
        {
            **DeepVerifyRecord(timestamp="t", project="p", version="v1.0.0", commit="c", verdict="pass").to_dict(),
            "extra": 1,
        }
    )
    assert record.verdict == "pass"
    entry_fields = {f.name for f in dataclasses.fields(LedgerEntry)}
    entry = LedgerEntry.from_dict({name: f"value-{name}" for name in entry_fields} | {"unknown": 1})
    assert {name: getattr(entry, name) for name in entry_fields} == {name: f"value-{name}" for name in entry_fields}


def test_markers_extract_and_replace_inclusive() -> None:
    text = "head\n<!-- B -->\nold\n<!-- E -->\ntail\n"
    assert extract_between_markers(text, "<!-- B -->", "<!-- E -->") == "<!-- B -->\nold\n<!-- E -->"
    assert replace_between_markers(text, "<!-- B -->", "<!-- E -->", "<!-- B -->new<!-- E -->") == (
        "head\n<!-- B -->new<!-- E -->\ntail\n"
    )


@pytest.mark.parametrize("missing", ["<!-- B -->", "<!-- E -->"])
def test_a_missing_marker_is_named(missing: str) -> None:
    text = "<!-- B -->x<!-- E -->".replace(missing, "")
    with pytest.raises(MarkerNotFoundError, match="marker not found") as caught:
        extract_between_markers(text, "<!-- B -->", "<!-- E -->")
    assert missing in str(caught.value)
    assert isinstance(caught.value, ValueError)


def test_the_generators_share_one_marker_error(monkeypatch) -> None:
    for name in ("generate_operator_verb_docs.py", "generate_measured_pace_docs.py"):
        spec = importlib.util.spec_from_file_location(name[:-3], _SCRIPTS_DIR / name)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        assert module.MarkerNotFoundError is MarkerNotFoundError, name


def test_kanban_root_or_default_precedence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path / "from-env"))
    assert resolve_kanban_root_or_default(tmp_path / "explicit") == tmp_path / "explicit"
    assert resolve_kanban_root_or_default() == tmp_path / "from-env"
    monkeypatch.delenv("PGAI_AGENT_KANBAN_ROOT_PATH")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert resolve_kanban_root_or_default() == tmp_path / "home" / "pgai_agent_kanban"


def test_all_rc_versions_lists_each_rc_once_prefixed() -> None:
    records = [{"rc_version": "1.2.0"}, {"rc_version": "v1.2.0"}, {"rc_version": "v1.3.0"}, {}]
    assert all_rc_versions(records) == ["v1.2.0", "v1.3.0"]
