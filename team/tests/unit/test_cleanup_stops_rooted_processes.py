"""
test_cleanup_stops_rooted_processes.py
======================================
cleanup.sh's temp purge removes a directory only after every process whose
working directory is inside it has been stopped, and logs each one
(team/scripts/lib/temp_purge.sh, temp_purge_remove):

  1. A per-project scratch directory with a live process rooted in it: the
     process is stopped (it ignores SIGTERM, so it takes the SIGKILL), the
     directory is removed, and the cleanup log names the process's pid and
     command. The install-wide sweep does the same for an entry of its own.
  2. A process rooted in a scratch directory that has a controlling
     terminal -- an operator's interactive shell -- is never signalled; its
     directory is kept with a log line saying why, and the rest of the
     purge still runs.
  3. --dry-run names the process it would stop and signals and removes
     nothing.

Every run is cleanup.sh --temp-only against a sandbox kanban root under
tmp_path with all five root variables set or cleared together
(docs/test-authoring-guidelines.md "Live-install safety"). Each rooted
process carries a marker unique to the test in its argv and is killed in
teardown, pass or fail.
"""

from __future__ import annotations

import fcntl
import os
import pathlib
import re
import subprocess
import termios
import time
import uuid

import pytest

from tests.unit.shell_harness import run_bash

_CLEANUP_SCRIPT = "scripts/cleanup/cleanup.sh"


def _diagnostic(result, extra: str = "") -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n{extra}"


def _sandbox(tmp_path: pathlib.Path) -> tuple[pathlib.Path, dict[str, str]]:
    root = tmp_path / "kanban_root"
    project = root / "projects" / "alpha"
    (project / "tasks").mkdir(parents=True)
    (project / "requirements").mkdir()
    (project / "project.cfg").write_text("[project]\nproject_name = alpha\n", encoding="utf-8")
    (root / "logs" / "agents").mkdir(parents=True)
    (root / "projects.cfg").write_text("[project:alpha]\npriority = 1\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "temp"),
        "PGAI_TASKS_DIR": "",
        "PGAI_PROJECT_ROOT": "",
        "PGAI_PROJECT_NAME": "",
        "HOME": str(home),
        "KILL_GRACE_SECONDS": "2",
    }
    return root, env


class _Rooted:
    """Processes started with their working directory inside a scratch directory."""

    def __init__(self) -> None:
        self.marker = f"pgai-cleanup-rooted-{uuid.uuid4().hex[:10]}"
        self.procs: list[subprocess.Popen] = []

    def start(self, cwd: pathlib.Path, label: str, *, ignore_term: bool = False, tty: bool = False) -> subprocess.Popen:
        trap = "trap '' TERM; " if ignore_term else ""
        argv = ["bash", "-c", f"{trap}exec -a {self.marker}-{label} sleep 600"]
        if tty:
            primary, secondary = os.openpty()

            def _take_terminal() -> None:
                os.setsid()
                fcntl.ioctl(secondary, termios.TIOCSCTTY, 0)

            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=secondary,
                stdout=secondary,
                stderr=secondary,
                preexec_fn=_take_terminal,
                close_fds=True,
                pass_fds=(secondary,),
            )
            os.close(secondary)
            proc._pgai_primary = primary  # type: ignore[attr-defined]
        else:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        self.procs.append(proc)
        return proc

    def cleanup(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            primary = getattr(proc, "_pgai_primary", None)
            if primary is not None:
                os.close(primary)
        subprocess.run(["pkill", "-KILL", "-f", f"{self.marker}-"], capture_output=True, timeout=10, check=False)


@pytest.fixture
def rooted():
    holder = _Rooted()
    yield holder
    holder.cleanup()


def _wait_started(proc: subprocess.Popen, marker_label: str) -> None:
    """Wait (bounded) until the exec into the marked sleep has happened."""
    for _ in range(50):
        try:
            if marker_label in pathlib.Path(f"/proc/{proc.pid}/cmdline").read_bytes().decode(errors="replace"):
                return
        except OSError:
            pass
        time.sleep(0.1)
    raise AssertionError(f"process {proc.pid} never became {marker_label}")


def _cleanup_log(root: pathlib.Path) -> str:
    """Every cleanup summary log the run wrote under the sandbox root."""
    logs = sorted(root.rglob("cleanup-*.log"))
    return "\n".join(p.read_text(encoding="utf-8") for p in logs)


def test_a_scratch_dir_is_removed_only_after_its_process_is_stopped(tmp_path, rooted) -> None:
    root, env = _sandbox(tmp_path)
    scratch = root / "temp" / "projects" / "alpha" / "scratch"
    workdir = scratch / "ff"
    workdir.mkdir(parents=True)
    install_wide = root / "temp" / "pgai_tmp.work"
    install_wide.mkdir(parents=True)
    stubborn = rooted.start(workdir, "stubborn", ignore_term=True)
    plain = rooted.start(install_wide, "plain")
    _wait_started(stubborn, f"{rooted.marker}-stubborn")
    _wait_started(plain, f"{rooted.marker}-plain")

    result = run_bash(tmp_path, f"bash {_CLEANUP_SCRIPT} --temp-only", extra_env=env, timeout=60)
    log = _cleanup_log(root)
    detail = _diagnostic(result, f"--- cleanup log ---\n{log}")

    assert result.returncode == 0, detail
    assert stubborn.wait(timeout=10) is not None and plain.wait(timeout=10) is not None, detail
    assert not scratch.exists(), detail
    assert not install_wide.exists(), detail
    stopped = re.search(
        rf"stopped a process running in {re.escape(str(scratch))} before removing it: "
        rf"pid={stubborn.pid} outcome=killed cwd={re.escape(str(workdir))} command={rooted.marker}-stubborn 600",
        log,
    )
    assert stopped, detail
    assert re.search(
        rf"stopped a process running in {re.escape(str(install_wide))} before removing it: "
        rf"pid={plain.pid} outcome=terminated .*command={rooted.marker}-plain 600",
        log,
    ), detail


def test_a_terminal_held_scratch_dir_is_kept_and_its_process_left_alone(tmp_path, rooted) -> None:
    root, env = _sandbox(tmp_path)
    scratch = root / "temp" / "projects" / "alpha" / "scratch"
    scratch.mkdir(parents=True)
    other = root / "temp" / "projects" / "alpha" / "pgai_tmp.other"
    other.mkdir()
    shell = rooted.start(scratch, "operator-shell", tty=True)
    _wait_started(shell, f"{rooted.marker}-operator-shell")

    result = run_bash(tmp_path, f"bash {_CLEANUP_SCRIPT} --temp-only", extra_env=env, timeout=60)
    log = _cleanup_log(root)
    detail = _diagnostic(result, f"--- cleanup log ---\n{log}")

    assert result.returncode == 0, detail
    assert shell.poll() is None, detail
    assert scratch.is_dir(), detail
    assert f"keeping {scratch}: a process running in it was left running" in log, detail
    assert f"pid={shell.pid} outcome=kept" in log, detail
    assert not other.exists(), detail


def test_dry_run_names_the_process_and_touches_nothing(tmp_path, rooted) -> None:
    root, env = _sandbox(tmp_path)
    scratch = root / "temp" / "projects" / "alpha" / "scratch"
    scratch.mkdir(parents=True)
    proc = rooted.start(scratch, "dry")
    _wait_started(proc, f"{rooted.marker}-dry")

    result = run_bash(tmp_path, f"bash {_CLEANUP_SCRIPT} --temp-only --dry-run", extra_env=env, timeout=60)
    log = _cleanup_log(root)
    detail = _diagnostic(result, f"--- cleanup log ---\n{log}")

    assert result.returncode == 0, detail
    assert proc.poll() is None, detail
    assert scratch.is_dir(), detail
    assert re.search(
        rf"\[DRY-RUN\] temp purge: would stop a process running in {re.escape(str(scratch))}: "
        rf"pid={proc.pid} outcome=would-stop .*command={rooted.marker}-dry 600",
        log,
    ), detail
