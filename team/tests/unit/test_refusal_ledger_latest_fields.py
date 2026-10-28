"""
test_refusal_ledger_latest_fields.py
====================================
The latest-refusal read refusal_ledger.sh's refusal_ledger_read_latest
makes through pp_run_ops: ``pgai_agent_kanban.lib.refusal_ledger``'s
``latest_field_lines`` and its ``latest-fields`` command-line verb.

Covered behaviour:

  * a record present prints ``found=true`` and one ``key=value`` line per
    field in a fixed order, with a pre-context record's context reading
    ``unknown`` and a newline inside a value folded to a space;
  * an empty or missing ledger prints the single line ``found=false`` and
    exits 0;
  * a malformed ledger prints one ``refusal_ledger: ...`` line on stderr,
    carrying that label exactly once, nothing on stdout, and exits 1.

Every ledger is written under pytest's tmp_path. Subprocesses run with an
environment built from an explicit set and a throwaway HOME.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import refusal_ledger
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MODULE = "pgai_agent_kanban.lib.refusal_ledger"

_RECORD = "\n".join([
    "# Refusal ledger",
    "",
    "## Refusal: 2026-01-01T00:00:00Z — demo — release.sh:git_step",
    "",
    "- Context: install",
    "- Error: gate.rc-branch-mismatch",
    "- Message: the release stopped",
    "- Remedy: git checkout ai_rc/v1.2.3",
    "",
])


def _run_module(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    return subprocess.run(
        [sys.executable, "-m", _MODULE, *args],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60, check=False,
    )


def test_record_present_prints_found_and_each_field_in_order(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "refusal-ledger.md"
    ledger.write_text(_RECORD, encoding="utf-8")

    result = _run_module(tmp_path, "latest-fields", str(ledger))

    assert (result.returncode, result.stderr) == (0, ""), result.stderr
    assert result.stdout.splitlines() == [
        "found=true",
        "timestamp=2026-01-01T00:00:00Z",
        "project=demo",
        "surface=release.sh:git_step",
        "error=gate.rc-branch-mismatch",
        "message=the release stopped",
        "remedy=git checkout ai_rc/v1.2.3",
        "context=install",
    ]


def test_record_without_context_reads_unknown(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "refusal-ledger.md"
    ledger.write_text(_RECORD.replace("- Context: install\n", ""), encoding="utf-8")
    lines = refusal_ledger.latest_field_lines(refusal_ledger.read_latest(str(ledger)))
    assert lines[-1] == "context=unknown"


def test_values_stay_on_one_line_and_missing_values_print_empty() -> None:
    lines = refusal_ledger.latest_field_lines({"message": "two\nlines", "remedy": None})
    assert "message=two lines" in lines
    assert "remedy=" in lines and "timestamp=" in lines
    assert len(lines) == 1 + len(refusal_ledger.LATEST_FIELD_KEYS)


def test_empty_or_missing_ledger_prints_found_false(tmp_path: pathlib.Path) -> None:
    assert refusal_ledger.latest_field_lines(None) == ["found=false"]
    empty = tmp_path / "refusal-ledger.md"
    empty.write_text("  \n", encoding="utf-8")
    for path in (empty, tmp_path / "missing.md"):
        result = _run_module(tmp_path, "latest-fields", str(path))
        assert (result.returncode, result.stdout, result.stderr) == (0, "found=false\n", ""), path


def test_ledger_error_prints_one_stderr_line_and_exits_one(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "refusal-ledger.md"
    ledger.write_text("not a refusal record\n", encoding="utf-8")

    result = _run_module(tmp_path, "latest-fields", str(ledger))

    assert result.returncode == 1
    assert result.stdout == ""
    assert len(result.stderr.splitlines()) == 1, result.stderr
    assert result.stderr.startswith("refusal_ledger: "), result.stderr
    assert str(ledger) in result.stderr and "malformed" in result.stderr, result.stderr


@pytest.mark.parametrize("verb", ["latest-fields", "latest"])
def test_malformed_ledger_error_carries_the_label_exactly_once(tmp_path: pathlib.Path, verb: str) -> None:
    ledger = tmp_path / "refusal-ledger.md"
    ledger.write_text("garbage content\nnot a record\n", encoding="utf-8")

    result = _run_module(tmp_path, verb, str(ledger))

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stderr.count("refusal_ledger:") == 1, result.stderr
    assert result.stderr.startswith(f"refusal_ledger: {ledger} has content"), result.stderr
