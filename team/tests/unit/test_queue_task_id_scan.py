"""
test_queue_task_id_scan.py
==========================
The scan wake_task_id_gate.sh runs before each queue scan reads ids:
``pgai_agent_kanban.lib.queue_task_id_scan``, which names every queue entry
of a marker class whose task id is outside the canonical shape.

Covered behaviour:

  * a class containing the BACKLOG space marker also admits the ``[]``
    alias, so a malformed id behind ``[]`` is reported;
  * a class without the space sees only its own marker characters: ``[]``,
    ``[ ]`` and other markers are not its entries;
  * canonical ids (the legacy CLAUDE- form included) are never reported;
    malformed ids are reported with their line number and stripped line;
  * an unreadable queue file (missing, or a directory) reports nothing, and
    the command line prints nothing and exits 0;
  * the command line prints ``<number>\\t<line>`` per finding;
  * through the gate, the refusal still names the malformed line when the
    caller defined its own pp_run_ops before sourcing the gate.

The canonical pattern is read from task_ids.sh's kanban_task_id_pattern, the
one source the gate passes in. Subprocesses run with an environment built
from an explicit set and a throwaway HOME under tmp_path.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.lib import queue_task_id_scan
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib"
_TASK_IDS = _LIB / "task_ids.sh"
_GATE = _LIB / "wake_task_id_gate.sh"
_MODULE = "pgai_agent_kanban.lib.queue_task_id_scan"

_QUEUE = "\n".join([
    "# Coder Backlog",
    "",
    "## Queue",
    "- [ ] CODER-20261018-001-well-formed — description",
    "- [] ../escape",
    "-[ ]  bad.id",
    "  [B] a.b blocked entry",
    "- [W] Upper_Slug-20261018-001",
    "- [W] CODER-20261018-002-waiting",
    "- [x] ..",
    "- [ ] CLAUDE-PM-20260101-001-legacy-form",
    "- [Q] not-a-marker",
    "",
])


@pytest.fixture(scope="module")
def canonical_pattern() -> str:
    result = subprocess.run(
        ["bash", "-c", f"source '{_TASK_IDS}'; kanban_task_id_pattern"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0 and result.stdout.strip(), result.stderr
    return result.stdout.strip()


def _run_module(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    return subprocess.run(
        [sys.executable, "-m", _MODULE, "--", *args],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60, check=False,
    )


def test_space_marker_class_admits_the_empty_bracket_alias(canonical_pattern: str) -> None:
    found = queue_task_id_scan.malformed_queue_entries(_QUEUE.splitlines(), " B", canonical_pattern)
    assert found == [(5, "- [] ../escape"), (6, "-[ ]  bad.id"), (7, "[B] a.b blocked entry")]


def test_class_without_the_space_sees_only_its_own_markers(canonical_pattern: str) -> None:
    lines = _QUEUE.splitlines()
    assert queue_task_id_scan.malformed_queue_entries(lines, "W", canonical_pattern) == [
        (8, "- [W] Upper_Slug-20261018-001"),
    ]
    assert queue_task_id_scan.malformed_queue_entries(lines, "B", canonical_pattern) == [
        (7, "[B] a.b blocked entry"),
    ]
    assert queue_task_id_scan.malformed_queue_entries(lines, "x", canonical_pattern) == [(10, "- [x] ..")]


def test_canonical_ids_are_never_reported(canonical_pattern: str) -> None:
    lines = [
        "- [ ] CODER-20261018-001-well-formed — description",
        "- [] TESTER-20261018-003-verify",
        "-   [ ]   CLAUDE-PM-20260101-001-legacy-form   — spaced",
        "- [W] CODER-20261018-002-waiting",
    ]
    for marker_class in (" B", "W ", "B", "A"):
        assert queue_task_id_scan.malformed_queue_entries(lines, marker_class, canonical_pattern) == []


def test_unreadable_queue_file_reports_nothing(tmp_path: pathlib.Path, canonical_pattern: str) -> None:
    assert queue_task_id_scan.scan_queue_file(tmp_path / "missing.md", " B", canonical_pattern) == []
    as_directory = tmp_path / "queue_dir"
    as_directory.mkdir()
    assert queue_task_id_scan.scan_queue_file(as_directory, " B", canonical_pattern) == []
    result = _run_module(tmp_path, str(as_directory), " B", canonical_pattern)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_command_line_prints_number_and_line_per_finding(tmp_path: pathlib.Path, canonical_pattern: str) -> None:
    queue = tmp_path / "coder_backlog.md"
    queue.write_text(_QUEUE, encoding="utf-8")
    result = _run_module(tmp_path, str(queue), "W ", canonical_pattern)
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "5\t- [] ../escape\n"
        "6\t-[ ]  bad.id\n"
        "8\t- [W] Upper_Slug-20261018-001\n"
    )


def test_gate_refusal_names_the_line_when_the_caller_defined_its_own_runner(tmp_path: pathlib.Path) -> None:
    queue = tmp_path / "coder_backlog.md"
    queue.write_text(_QUEUE, encoding="utf-8")
    script = textwrap.dedent(f"""\
        set -euo pipefail
        pp_run_ops() {{ echo "caller-runner $*"; }}
        source '{_GATE}'
        _narrate_malformed_queue_task_ids '{queue}' ' B' "pending-task scan"
        echo "rc=$?"
    """)
    env = build_scrubbed_env(home_dir=tmp_path / "home")
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env,
        cwd=str(tmp_path), timeout=60, check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert result.stdout == "rc=0\n", f"the scan must not print on stdout\nstdout={result.stdout}"
    assert "caller-runner" not in result.stderr, result.stderr
    assert result.stderr.count("error:   wake.queue.malformed-task-id") == 3, result.stderr
    assert f"{queue} line 5 ('- [] ../escape')" in result.stderr, result.stderr
    assert f"sed -n '7p' -- '{queue}'" in result.stderr, result.stderr
