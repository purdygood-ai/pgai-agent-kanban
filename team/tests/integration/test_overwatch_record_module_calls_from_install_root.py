"""
test_overwatch_record_module_calls_from_install_root.py
=======================================================
End-to-end coverage for the OVERWATCH status.md call sites that reach
pgai_agent_kanban.task_status through pp_run_ops, run from an
install-shaped kanban root rather than from this dev tree:

  team/scripts/lib/overwatch_protocol.sh
      transient_retry_field / transient_retry_write,
      zero_progress_field / zero_progress_record / zero_progress_reset
      (each loads pp_run_ops.sh and atomic_write.sh on first use through
      overwatch_load_module_runner)
  team/scripts/lib/overwatch-checks/check-transient-api-error.sh
      _ctae_do_requeue / _ctae_do_escalate
  the ## Block Record writes at the ceiling escalation and the zero-progress
      cap (block_record.sh, lazily loaded by overwatch_load_module_runner)

The harness provider's ## State / ## Summary writers (team/scripts/wake/
harness.sh) are exercised end-to-end by the testing-only pipeline
certification tests, which dispatch through wake-batch.sh with the harness
as the active provider.

Each lib is sourced on its own -- overwatch_protocol.sh with nothing else
loaded, so its lazy loader is what finds the runner -- from a copy of
scripts/ and pgai_agent_kanban/ laid out the way install.sh lays out a live
root, in a subprocess whose environment is built from an explicit minimal
set (no PYTHONPATH) and whose working directory is outside that root. That
is the shape in which a wrong root, a missing source of pp_run_ops.sh or
atomic_write.sh, or a dependency on the caller's cwd would break a migrated
site while the unit tests (run from team/) still pass.

The assertions are the bytes the wake and OVERWATCH depend on: the durable
records read back the counts written, the requeue leaves the task BACKLOG
with TRANSIENT labelled and its retry record bumped, the escalation sets
Needs Human and narrates the ceiling, the file keeps its mode, and no
scratch file is left beside the target.

Every fixture lives under pytest's tmp_path; no live task folder or kanban
install is read or written.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.overwatch.block_record import load_block_record
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]

_TASK = "CODER-20260101-001-install-root-task"


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
            "KANBAN_ROOT": str(root),
            "TRANSIENT_RETRY_CAP": "3",
            "ZERO_PROGRESS_CAP": "3",
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


def _no_scratch(directory: pathlib.Path) -> None:
    leftovers = sorted(p.name for p in directory.iterdir() if ".tmp." in p.name)
    assert leftovers == [], f"scratch files left behind in {directory}: {leftovers}"


def _status(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    status = tmp_path / "tasks" / _TASK / "status.md"
    status.parent.mkdir(parents=True)
    status.write_text(text, encoding="utf-8")
    status.chmod(0o640)
    return status


def test_durable_records_read_and_write_from_an_install_root(
    tmp_path: pathlib.Path, install_root: pathlib.Path
) -> None:
    status = _status(tmp_path, "## State\nWORKING\n\n## Zero Progress\n\n## Summary\nx\n")

    proc = _run(
        tmp_path,
        install_root,
        f"""\
        source "{install_root}/scripts/lib/overwatch_protocol.sh"
        declare -F pp_run_ops >/dev/null && echo "runner loaded at source time" >&2
        echo "zp-empty=$(zero_progress_field "{status}" count 0)"
        zero_progress_record "{status}" stale_active_orphan 2 3 "orphan (cycle 2/3)"
        echo "zp=$(zero_progress_field "{status}" count 0)"
        transient_retry_write "{status}" transient_api_error 1 3 claude "API Error: 529"
        echo "tr=$(transient_retry_field "{status}" retry_count 0)"
        zero_progress_reset "{status}"
        echo "zp-reset=$(zero_progress_field "{status}" count x) reason=$(zero_progress_field "{status}" reason_code x)"
        """,
    )

    assert proc.returncode == 0, _explain(proc)
    assert "runner loaded at source time" not in proc.stderr, _explain(proc)
    assert proc.stdout.splitlines() == [
        "zp-empty=0", "zp=2", "tr=1", "zp-reset=0 reason=none",
    ], _explain(proc)
    assert status.read_text(encoding="utf-8") == (
        "## State\nWORKING\n\n## Zero Progress\nreason_code: none\ncount: 0\nlimit: 3\ndetail: none\n\n"
        "## Summary\nx\n\n## Transient Retry\nreason_code: transient_api_error\nretry_count: 1\n"
        "retry_limit: 3\nprovider: claude\nlast_error: API Error: 529\n"
    )
    assert status.stat().st_mode & 0o777 == 0o640
    _no_scratch(status.parent)


def test_transient_requeue_and_escalate_run_from_an_install_root(
    tmp_path: pathlib.Path, install_root: pathlib.Path
) -> None:
    status = _status(
        tmp_path,
        "## State\nBLOCKED\n\n## Summary\nold\n\n## Needs Human\nyes\n\n## Blockers\nboom\n\n"
        "## Transient Retry\nreason_code: transient_api_error\nretry_count: 1\nretry_limit: 3\n"
        "provider: claude\nlast_error: 529\n",
    )

    proc = _run(
        tmp_path,
        install_root,
        f"""\
        source "{install_root}/scripts/lib/overwatch_protocol.sh"
        source "{install_root}/scripts/lib/overwatch-checks/check-transient-api-error.sh"
        overwatch_backup_file() {{ echo none; }}
        overwatch_log_action() {{ echo "LOG: $3"; }}
        _CTAE_TASK_ID="{_TASK}" _CTAE_STATUS_FILE="{status}" _CTAE_NEW_COUNT=2 _ctae_do_requeue
        _CTAE_TASK_ID="{_TASK}" _CTAE_STATUS_FILE="{status}" _CTAE_CURRENT_COUNT=2 _CTAE_CAP=3 _ctae_do_escalate
        echo "count=$(_ctae_read_requeue_count "{status}")"
        """,
    )

    assert proc.returncode == 0, _explain(proc)
    assert proc.stdout.splitlines() == [
        "LOG: transient-auto-requeued", "LOG: transient-ceiling-needs-human-escalated", "count=2",
    ], _explain(proc)
    assert status.read_text(encoding="utf-8") == (
        "## State\nBACKLOG\n\n## Summary\nAuto-requeued by OVERWATCH check-transient-api-error: "
        "transient API error detected in provider batch log. Requeue #2.\n\n## Needs Human\nyes\n\n"
        "## Blockers\nnone\n\n## Transient Retry\nreason_code: transient_api_error\nretry_count: 2\n"
        "retry_limit: 3\nprovider: overwatch\nlast_error: transient signature matched in provider batch log\n\n"
        "## Labels\nTRANSIENT\n\n## Blocked Reason\nTransient API error retry ceiling reached (2/3, see "
        "## Transient Retry record); human investigation required.\n"
    )
    assert status.stat().st_mode & 0o777 == 0o640
    _no_scratch(status.parent)


def test_block_records_written_from_an_install_root(
    tmp_path: pathlib.Path, install_root: pathlib.Path
) -> None:
    """The ceiling escalation and the zero-progress cap each write a ## Block Record.

    Both reach block_record.sh only through overwatch_load_module_runner's
    lazy load, and the record module and its evidence helpers through
    pp_run_ops, so a wrong root or a missing source breaks this, not the
    unit tests. The wake collaborators the zero-progress cap calls are
    stubbed; the transition itself is not what this covers.
    """
    escalated = _status(
        tmp_path,
        "## State\nBLOCKED\n\n## Needs Human\nno\n\n## Transient Retry\n"
        "reason_code: transient_api_error\nretry_count: 3\nretry_limit: 3\nprovider: claude\nlast_error: 529\n",
    )
    (escalated.parent / "README.md").write_text("# Task\n\n## Role\nWRITER\n", encoding="utf-8")
    capped = tmp_path / "tasks" / "CODER-20260101-002-capped-task" / "status.md"
    capped.parent.mkdir(parents=True)
    capped.write_text(
        "## State\nWORKING\n\n## Needs Human\nno\n\n## Blockers\nnone\n\n## Zero Progress\n"
        "reason_code: clean_exit_no_progress\ncount: 2\nlimit: 3\ndetail: earlier\n",
        encoding="utf-8",
    )
    (capped.parent / "README.md").write_text("# Task\n\n## Role\nCM\n", encoding="utf-8")

    proc = _run(
        tmp_path,
        install_root,
        f"""\
        source "{install_root}/scripts/lib/overwatch_protocol.sh"
        source "{install_root}/scripts/lib/overwatch-checks/check-transient-api-error.sh"
        overwatch_backup_file() {{ echo none; }}
        overwatch_log_action() {{ echo "LOG: $3"; }}
        log() {{ echo "log: $*" >&2; }}
        transition_task_state() {{ :; }}
        set_block() {{ :; }}
        normalize_status_file() {{ :; }}
        pgai_format_error_contract() {{ echo "$1"; }}
        _CTAE_TASK_ID="{_TASK}" _CTAE_STATUS_FILE="{escalated}" _CTAE_CURRENT_COUNT=3 _CTAE_CAP=3 _ctae_do_escalate
        echo "state=$(zero_progress_advance_or_escalate CODER-20260101-002-capped-task "{capped}" clean_exit_no_progress "no progress" key.example)"
        """,
    )

    assert proc.returncode == 0, _explain(proc)
    assert "Block Record not written" not in proc.stderr, _explain(proc)
    assert proc.stdout.splitlines() == ["LOG: transient-ceiling-needs-human-escalated", "state=BLOCKED"], _explain(proc)
    record = load_block_record(escalated)
    assert (record.reason_code, record.owner_role) == ("transient_api_error", "WRITER")
    record = load_block_record(capped)
    assert (record.reason_code, record.owner_role) == ("zero_progress", "CM")
    assert record.evidence.startswith("worktree=gone; branch=none; ahead=0; merged=no; batch_log=none")
    assert escalated.stat().st_mode & 0o777 == 0o640
    _no_scratch(escalated.parent)
