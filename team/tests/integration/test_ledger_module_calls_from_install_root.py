"""
test_ledger_module_calls_from_install_root.py
=============================================
End-to-end coverage for the three ledger libraries that reach their ledger
modules through pp_run_ops, run from an install-shaped kanban root rather
than from this dev tree:

  team/scripts/lib/green_ledger.sh
      green_ledger_try_reuse / green_ledger_record_exit
      (pgai_agent_kanban.testing.green_ledger, through the pp_run_ops
      lib/cm/common.sh loads)
  team/scripts/lib/refusal_ledger.sh
      refusal_ledger_read_latest
      (pgai_agent_kanban.lib.refusal_ledger latest-fields)
  team/scripts/lib/overwatch_protocol.sh
      dispatch_record_append / dispatch_record_latest
      (pgai_agent_kanban.lib.dispatch_ledger, through the runner
      overwatch_load_module_runner loads on first use)

Each lib is sourced on its own from a copy of scripts/ and
pgai_agent_kanban/ laid out the way install.sh lays out a live root, in a
subprocess whose environment is built from an explicit minimal set (no
PYTHONPATH) and whose working directory is outside that root -- the shape
in which a wrong root, a missing source of pp_run_ops.sh or a dependency
on the caller's cwd would break a migrated site while the unit tests (run
from team/) still pass.

Every fixture lives under pytest's tmp_path; no live task folder or kanban
install is read or written.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import textwrap

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]

_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_SHA = "aaaabbbbccccddddeeeeffff00001111aaaabbbbccccddddeeeeffff00001111"


@pytest.fixture()
def install_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A kanban root holding copies of scripts/ and pgai_agent_kanban/."""
    root = tmp_path / "kanban-root"
    root.mkdir()
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    shutil.copytree(
        _TEAM_DIR / "pgai_agent_kanban",
        root / "pgai_agent_kanban",
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )
    return root


def _run(tmp_path: pathlib.Path, root: pathlib.Path, body: str) -> subprocess.CompletedProcess:
    """Run *body* in bash from a directory outside *root*, scrubbed env."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    temp_dir = tmp_path / "kanban-temp"
    temp_dir.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
        },
    )
    script = "set -euo pipefail\n" + textwrap.dedent(body)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(elsewhere),
    )


def _explain(proc: subprocess.CompletedProcess) -> str:
    return f"rc={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"


def test_green_ledger_records_and_reuses_from_an_install_root(
    tmp_path: pathlib.Path, install_root: pathlib.Path
) -> None:
    ledger = tmp_path / "ledger" / "ledger.jsonl"

    proc = _run(
        tmp_path,
        install_root,
        f"""\
        source "{install_root}/scripts/lib/green_ledger.sh"
        GREEN_LEDGER_FILE="{ledger}"
        GREEN_LEDGER_SUITE=unit
        GREEN_LEDGER_SELECTION="not quarantine"
        GREEN_LEDGER_COMMIT={_COMMIT}
        GREEN_LEDGER_TREE_CLEAN=true
        GREEN_LEDGER_FINDINGS_SHA={_SHA}
        GREEN_LEDGER_PREPARED=true
        GREEN_LEDGER_REUSED=false
        green_ledger_record_exit 0
        if green_ledger_try_reuse; then echo "reuse-rc=0"; else echo "reuse-rc=1"; fi
        """,
    )

    assert proc.returncode == 0, _explain(proc)
    lines = proc.stdout.splitlines()
    assert lines[0] == f"[green-ledger] unit: recorded exit 0 at commit {_COMMIT[:12]} in {ledger}", _explain(proc)
    assert lines[1].startswith("[green-ledger] unit: reused the green result recorded "), _explain(proc)
    assert lines[2] == "reuse-rc=0", _explain(proc)
    record = json.loads(ledger.read_text(encoding="utf-8"))
    assert (record["commit"], record["exit"], record["tree_clean"]) == (_COMMIT, 0, True)


def test_refusal_ledger_reads_the_latest_record_from_an_install_root(
    tmp_path: pathlib.Path, install_root: pathlib.Path
) -> None:
    ledger = install_root / "projects" / "demo" / "refusal-ledger.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "## Refusal: 2026-01-01T00:00:00Z — demo — release.sh:git_step\n\n"
        "- Context: install\n- Error: gate.x\n- Message: stopped\n- Remedy: run it again\n",
        encoding="utf-8",
    )

    proc = _run(
        tmp_path,
        install_root,
        f"""\
        source "{install_root}/scripts/lib/refusal_ledger.sh"
        refusal_ledger_read_latest demo
        """,
    )

    assert proc.returncode == 0, _explain(proc)
    assert proc.stdout.splitlines() == [
        "found=true",
        "timestamp=2026-01-01T00:00:00Z",
        "project=demo",
        "surface=release.sh:git_step",
        "error=gate.x",
        "message=stopped",
        "remedy=run it again",
        "context=install",
    ], _explain(proc)


def test_dispatch_ledger_appends_and_reads_from_an_install_root(
    tmp_path: pathlib.Path, install_root: pathlib.Path
) -> None:
    task_dir = tmp_path / "tasks" / "CODER-20260101-001-install-root-task"

    proc = _run(
        tmp_path,
        install_root,
        f"""\
        source "{install_root}/scripts/lib/overwatch_protocol.sh"
        AGENT=coder ACTIVE_PROVIDER=claude
        dispatch_record_append "{task_dir}" dispatched /logs/coder-batch.log 12
        dispatch_record_append "{task_dir}" returned /logs/coder-batch.log 12 80 0
        dispatch_record_latest "{task_dir}"
        """,
    )

    assert proc.returncode == 0, _explain(proc)
    assert proc.stdout == "/logs/coder-batch.log\t12\t80\n", _explain(proc)
    records = [
        json.loads(line)
        for line in (task_dir / "logs" / "dispatch.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [(r["event"], r["offset_end"], r["exit_code"]) for r in records] == [
        ("dispatched", None, None), ("returned", 80, 0),
    ]
