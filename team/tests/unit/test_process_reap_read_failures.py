"""
test_process_reap_read_failures.py
==================================
pgai_agent_kanban.lib.process_reap reports a /proc read it could not make
instead of treating it as "nothing there":

  1. A process entry whose stat cannot be read (a stub /proc where the read
     fails with EISDIR, root or not) is counted with its errno and path, and
     the scan's empty answer is not the whole story.
  2. _is_running on that entry answers False and the failure is counted.
  3. A process that has exited (its entry is missing: ENOENT) is the
     ordinary "gone" answer and is not counted.
  4. A /proc that cannot be listed is reported as a listing failure; the
     command line exits 4 and prints the summary on stderr, never a bare
     exit 0 with no output.
  5. An unreadable entry during a command-line scan prints the summary line
     on stderr and leaves the exit status to the matched processes.
  6. agent_run_reap (the wake's caller) logs the summary as a WARNING.
  7. One wake whose reap reports unreadable /proc leaves the operator inbox
     empty; two in a row leave exactly one entry naming the first unreadable
     path, a third adds none, and a clean reap in between starts the count
     over. Each wake is its own bash process over one sandbox kanban root,
     the reap's scan stubbed through the environment and the inbox written
     by the real inbox module.
  8. Two wakes whose unreadable reaps finish together, each pausing between
     reading and writing the streak record, leave it at 2 rather than 1 and
     raise one inbox entry; a wake that cannot take the streak lock leaves
     the record as it was.

The stub /proc is a directory this test builds under tmp_path; nothing
reads or signals a real process.
"""

from __future__ import annotations

import errno
import os
import pathlib
import subprocess
import sys
import time

import pytest

from pgai_agent_kanban.lib import process_reap
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_AGENT_RUN_REAP = _TEAM_DIR / "scripts" / "lib" / "agent_run_reap.sh"

_STUB_PID = 4242


@pytest.fixture
def stub_proc(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """A stub /proc with one entry whose stat is unreadable (a directory)."""
    proc = tmp_path / "proc"
    entry = proc / str(_STUB_PID)
    (entry / "stat").mkdir(parents=True)
    target = tmp_path / "scope"
    target.mkdir()
    os.symlink(target, entry / "cwd")
    monkeypatch.setattr(process_reap, "_PROC", proc)
    process_reap.reset_read_failures()
    yield proc, target
    process_reap.reset_read_failures()


def test_an_unreadable_stat_is_counted_with_its_errno(stub_proc) -> None:
    proc, target = stub_proc

    found = process_reap.find_by_cwd(target)

    failures = process_reap.read_failures()
    assert found == []
    assert failures.count == 1, failures
    assert failures.first_errno == errno.EISDIR, failures
    assert failures.first_path == str(proc / str(_STUB_PID) / "stat")
    assert failures.listing is False


def test_is_running_on_an_unreadable_stat_is_counted(stub_proc) -> None:
    assert process_reap._is_running(_STUB_PID, "1") is False
    assert process_reap.read_failures().count == 1


def test_an_exited_process_is_gone_not_unreadable(stub_proc) -> None:
    assert process_reap._is_running(_STUB_PID + 1, "1") is False
    assert process_reap.read_failures().count == 0


def test_an_unlistable_proc_is_a_listing_failure_not_an_empty_scan(tmp_path, monkeypatch, capsys) -> None:
    not_a_dir = tmp_path / "proc"
    not_a_dir.write_text("", encoding="utf-8")
    monkeypatch.setattr(process_reap, "_PROC", not_a_dir)
    scope = tmp_path / "scope"
    scope.mkdir()

    rc = process_reap.main(["--cwd-under", str(scope), "--dry-run"])

    err = capsys.readouterr().err
    assert rc == process_reap.EXIT_PROC_UNLISTABLE
    assert "process_reap: unreadable=" in err and "first_errno=ENOTDIR" in err, err
    assert "no process was scanned" in err, err


def test_the_command_line_reports_an_unreadable_entry_on_stderr(stub_proc, capsys) -> None:
    _proc, target = stub_proc

    rc = process_reap.main(["--cwd-under", str(target), "--dry-run"])

    captured = capsys.readouterr()
    assert rc == process_reap.EXIT_OK
    assert captured.out == ""
    assert "process_reap: unreadable=1 first_errno=EISDIR" in captured.err, captured.err


def test_agent_run_reap_logs_the_summary_as_a_warning(tmp_path) -> None:
    script = (
        "set -euo pipefail\n"
        f"source '{_AGENT_RUN_REAP}'\n"
        "pp_run_ops() { echo 'process_reap: unreadable=2 first_errno=EACCES first_path=/proc/9/stat -- x' >&2; }\n"
        'log() { echo "LOG: $*"; }\n'
        "agent_run_reap run-id 1 '' TASK-1\n"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env=build_scrubbed_env(home_dir=tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "LOG: WARNING: task TASK-1: process reap could not read part of /proc: "
        "process_reap: unreadable=2 first_errno=EACCES"
    ) in result.stdout, (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# Consecutive unreadable reaps reach the operator inbox
# ---------------------------------------------------------------------------

_PROJECT = "demo"
_FIRST_PATH = "/proc/9/stat"


def _unreadable_summary(path: str) -> str:
    return f"process_reap: unreadable=2 first_errno=EACCES first_path={path} -- 2 /proc read(s) failed"


def _wake_reap_script(before_reap: str = "") -> str:
    """The bash a wake runs for one agent_run_reap; *before_reap* runs just ahead of it.

    The scan (pp_run_ops pgai_agent_kanban.lib.process_reap) is stubbed
    through STUB_REAP_SUMMARY: the stub prints that line on stderr when it
    is set and exits 0. Every other ops call, the inbox write included, runs
    the real module.
    """
    return (
        "set -euo pipefail\n"
        f"source '{_AGENT_RUN_REAP}'\n"
        'eval "_real_pp_run_ops() $(declare -f pp_run_ops | tail -n +2)"\n'
        "pp_run_ops() {\n"
        '  if [[ "$1" == pgai_agent_kanban.lib.process_reap ]]; then\n'
        '    [[ -z "${STUB_REAP_SUMMARY:-}" ]] || echo "$STUB_REAP_SUMMARY" >&2\n'
        "    return 0\n"
        "  fi\n"
        '  _real_pp_run_ops "$@"\n'
        "}\n"
        'log() { echo "LOG: $*"; }\n'
        f"_CURRENT_PROJECT={_PROJECT}\n"
        f"{before_reap}"
        "agent_run_reap run-id 1 '' TASK-1\n"
    )


def _wake_env(root: pathlib.Path, summary: str | None) -> dict[str, str]:
    """A scrubbed environment pointing every kanban-root variable at sandbox *root*."""
    extra = {
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
    }
    if summary is not None:
        extra["STUB_REAP_SUMMARY"] = summary
    return build_scrubbed_env(
        home_dir=root / "home", extra_path_dirs=[pathlib.Path(sys.executable).parent], extra_env=extra
    )


def _wake_reap(root: pathlib.Path, summary: str | None) -> subprocess.CompletedProcess:
    """One wake's agent_run_reap in its own bash process over sandbox *root*."""
    result = subprocess.run(
        ["bash", "-c", _wake_reap_script()],
        env=_wake_env(root, summary),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(root),
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture
def sandbox_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban"
    for d in (root / "projects" / _PROJECT, root / "tmp", root / "home"):
        d.mkdir(parents=True)
    return root


def _open_entries(root: pathlib.Path) -> tuple[inbox.InboxEntry, ...]:
    return inbox.list_open(OpsContext(kanban_root=root), _PROJECT)


def test_one_wake_with_an_unreadable_reap_stays_a_log_line(sandbox_root: pathlib.Path) -> None:
    result = _wake_reap(sandbox_root, _unreadable_summary(_FIRST_PATH))

    assert "LOG: WARNING: task TASK-1: process reap could not read part of /proc" in result.stdout, result.stdout
    assert _open_entries(sandbox_root) == ()


def test_two_consecutive_wakes_with_unreadable_reaps_raise_one_inbox_entry(sandbox_root: pathlib.Path) -> None:
    _wake_reap(sandbox_root, _unreadable_summary(_FIRST_PATH))
    second = _wake_reap(sandbox_root, _unreadable_summary("/proc/10/stat"))

    entries = _open_entries(sandbox_root)
    assert len(entries) == 1, (entries, second.stdout + second.stderr)
    assert _FIRST_PATH in entries[0].question, entries[0].question
    assert entries[0].source == "wake-process-reap"
    assert f"operator inbox entry {entries[0].entry_id} names {_FIRST_PATH}" in second.stdout, second.stdout

    _wake_reap(sandbox_root, _unreadable_summary("/proc/11/stat"))
    assert len(_open_entries(sandbox_root)) == 1


def test_a_clean_reap_between_two_unreadable_ones_raises_nothing(sandbox_root: pathlib.Path) -> None:
    _wake_reap(sandbox_root, _unreadable_summary(_FIRST_PATH))
    _wake_reap(sandbox_root, None)
    _wake_reap(sandbox_root, _unreadable_summary(_FIRST_PATH))

    assert _open_entries(sandbox_root) == ()


# ---------------------------------------------------------------------------
# Concurrent wakes share the streak without losing an increment
# ---------------------------------------------------------------------------

_STREAK = pathlib.Path("logs") / "process-reap-unreadable.streak"
_STREAK_LOCK = pathlib.Path("locks") / "process-reap-unreadable-streak.lock"


def _streak_fields(root: pathlib.Path) -> list[str]:
    return (root / _STREAK).read_text(encoding="utf-8").rstrip("\n").split("\t")


def test_two_wakes_reaping_together_count_two_not_one(sandbox_root: pathlib.Path) -> None:
    arrived = sandbox_root / "tmp" / "arrived"
    arrived.mkdir()
    # Each wake waits at a start line until both have reached it, then
    # pauses one second between reading the streak record and writing it,
    # so without the streak lock both would read 0 and both would write 1.
    before_reap = (
        f"touch '{arrived}'/$$\n"
        "for _ in $(seq 400); do\n"
        f"  [[ $(ls '{arrived}' | wc -l) -ge 2 ]] && break\n"
        "  sleep 0.05\n"
        "done\n"
        'eval "_real_streak_read() $(declare -f _agent_run_reap_streak_read | tail -n +2)"\n'
        '_agent_run_reap_streak_read() { _real_streak_read "$@"; sleep 1; }\n'
    )
    script = _wake_reap_script(before_reap)
    env = _wake_env(sandbox_root, _unreadable_summary(_FIRST_PATH))
    wakes = [
        subprocess.Popen(
            ["bash", "-c", script],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(sandbox_root),
        )
        for _ in range(2)
    ]
    outputs = []
    for wake in wakes:
        out, err = wake.communicate(timeout=120)
        outputs.append(out + err)
        assert wake.returncode == 0, out + err

    fields = _streak_fields(sandbox_root)
    assert fields[0] == "2", (fields, outputs)
    assert fields[2] == "1", (fields, outputs)
    assert len(_open_entries(sandbox_root)) == 1, outputs
    assert not (sandbox_root / _STREAK_LOCK).exists(), outputs


def test_a_wake_that_cannot_take_the_streak_lock_leaves_the_streak_alone(sandbox_root: pathlib.Path) -> None:
    _wake_reap(sandbox_root, _unreadable_summary(_FIRST_PATH))
    before = (sandbox_root / _STREAK).read_text(encoding="utf-8")
    lock = sandbox_root / _STREAK_LOCK
    holder = subprocess.Popen(
        ["flock", "-x", str(lock), "sleep", "30"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if lock.exists():
                break
            time.sleep(0.05)
        result = subprocess.run(
            ["bash", "-c", _wake_reap_script("_AGENT_RUN_REAP_STREAK_LOCK_WAIT_SECONDS=0\n")],
            env=_wake_env(sandbox_root, _unreadable_summary(_FIRST_PATH)),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(sandbox_root),
            check=False,
        )
    finally:
        holder.kill()
        holder.wait(timeout=10)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "the unreadable-reap streak is left as it was" in result.stdout, result.stdout + result.stderr
    assert (sandbox_root / _STREAK).read_text(encoding="utf-8") == before
    assert _open_entries(sandbox_root) == ()
