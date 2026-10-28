"""
test_wake_batch_single_instance.py
==================================
wake-batch.sh takes a single-instance guard per (project, agent) pair
(scripts/lib/wake_batch_guard.sh), so firings of one agent never overlap
within a project, while different projects run concurrently:

  1. While a batch of an agent runs for a project, a second firing of that
     agent exits 0 within a second, logs one line naming the project and
     the running batch's pid and age, never reaches the provider wake
     script for that project and leaves the guard record as it was.
  2. A guard record whose pid is dead -- or whose pid now belongs to
     another process (a different start time) -- is taken over: the batch
     runs, and the record names it.
  3. Another agent's guard, and the same agent's guard for another project,
     do not hold this one back: a firing while project 1's batch still runs
     starts a batch for project 2 only.
  4. One firing starts one batch per registered, enabled project, each
     scoped to its own project; a disabled project gets none, and an empty
     registry gets one line and no batch.
  5. The script's usage comment and --help say firings of one agent never
     overlap within a project while different projects run concurrently.

Every run copies wake-batch.sh, scripts/lib/ and config/default_palette.txt
into a throwaway install whose wake/claude.sh is a stub, under a sandbox
kanban root built from the five live-install names
(docs/test-authoring-guidelines.md "Live-install safety"). No real wake,
provider or project is touched.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import shutil
import subprocess
import time

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = _TEAM_DIR / "scripts"

# The stub provider wake: records that it ran, the pid it ran as (the
# batch's own pid, kept across wake-batch.sh's exec) and the project its
# batch is scoped to, then -- for the project STUB_HOLD_PROJECT names, or
# every project when that is unset -- waits for the test's release file
# when told to.
_STUB_WAKE = """#!/usr/bin/env bash
echo "ran pid=$$" >> "$PGAI_AGENT_KANBAN_ROOT_PATH/stub-ran"
echo "$$ ${PGAI_WAKE_BATCH_PROJECT:-}" >> "$PGAI_AGENT_KANBAN_ROOT_PATH/stub-projects"
if [[ -n "${STUB_HOLD_UNTIL:-}" && "${STUB_HOLD_PROJECT:-$PGAI_WAKE_BATCH_PROJECT}" == "$PGAI_WAKE_BATCH_PROJECT" ]]; then
    for _ in $(seq 1 200); do [[ -e "$STUB_HOLD_UNTIL" ]] && break; sleep 0.05; done
fi
exit 0
"""


def _register(root: pathlib.Path, *projects: str, disabled: tuple[str, ...] = ()) -> None:
    sections = []
    for priority, name in enumerate(projects, start=1):
        (root / "projects" / name).mkdir(parents=True, exist_ok=True)
        enabled = "false" if name in disabled else "true"
        sections.append(f"[project:{name}]\npriority={priority}\nenabled={enabled}\n")
    (root / "projects.cfg").write_text("\n".join(sections), encoding="utf-8")


@pytest.fixture
def install(tmp_path: pathlib.Path) -> dict:
    scripts = tmp_path / "install" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(_SCRIPTS / "wake-batch.sh", scripts / "wake-batch.sh")
    shutil.copytree(_SCRIPTS / "lib", scripts / "lib", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (tmp_path / "install" / "config").mkdir()
    shutil.copy(_TEAM_DIR / "config" / "default_palette.txt", tmp_path / "install" / "config" / "default_palette.txt")
    (scripts / "wake").mkdir()
    stub = scripts / "wake" / "claude.sh"
    stub.write_text(_STUB_WAKE, encoding="utf-8")
    stub.chmod(0o755)
    root = tmp_path / "sandbox"
    (root / "tmp").mkdir(parents=True)
    _register(root, "demo")
    return {"script": scripts / "wake-batch.sh", "root": root, "tmp": tmp_path}


def _env(install: dict, **extra: str) -> dict:
    root = install["root"]
    env = build_scrubbed_env(
        home_dir=install["tmp"] / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            **extra,
        },
    )
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME"):
        env.pop(name, None)
    return env


def _fire(install: dict, *args: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(install["script"]), *args],
        capture_output=True, text=True, env=_env(install, **extra), cwd=str(install["tmp"]),
        timeout=60, check=False,
    )


def _record_path(install: dict, agent: str, project: str = "demo") -> pathlib.Path:
    return install["root"] / "locks" / f"wake-batch-{agent}-{project}.record"


def _proc_start(pid: int) -> str:
    stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
    return stat.rsplit(") ", 1)[1].split()[19]


def _boot_id() -> str:
    return pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _write_record(
    install: dict, agent: str, pid: int, *, age_seconds: int, pid_start: str, project: str = "demo",
) -> str:
    acquired = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_seconds))
    text = (
        f"holder=wake-batch\npid={pid}\nop=wake-batch:{agent}:{project}\ntask=none\n"
        f"acquired_at={acquired.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"pid_start={pid_start}\nboot_id={_boot_id()}\n"
    )
    path = _record_path(install, agent, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def _skip_lines(stdout: str, agent: str) -> list[str]:
    return [l for l in stdout.splitlines() if l.startswith(f"wake-batch({agent}): previous firing still running")]


def _record_pid(install: dict, agent: str, project: str = "demo") -> int:
    for line in _record_path(install, agent, project).read_text(encoding="utf-8").splitlines():
        if line.startswith("pid="):
            return int(line.removeprefix("pid="))
    raise AssertionError(f"no pid line in the {agent}/{project} record")


def _ran_projects(install: dict) -> list[tuple[int, str]]:
    path = install["root"] / "stub-projects"
    if not path.exists():
        return []
    rows = [line.split(" ", 1) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [(int(pid), project) for pid, project in rows]


def _wait_for(predicate, what: str) -> None:
    for _ in range(200):
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def live_process():
    proc = subprocess.Popen(["sleep", "30"])
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()


def test_a_second_firing_while_one_runs_logs_one_line_and_changes_nothing(install, live_process) -> None:
    record = _write_record(install, "coder", live_process.pid, age_seconds=75, pid_start=_proc_start(live_process.pid))
    before = sorted(p.relative_to(install["root"]) for p in install["root"].rglob("*"))

    started = time.monotonic()
    result = _fire(install, "--agent=coder")
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stdout + result.stderr
    assert elapsed < 1.0, f"the skipping firing took {elapsed:.2f}s"
    lines = _skip_lines(result.stdout, "coder")
    assert len(lines) == 1, result.stdout
    assert f"(pid {live_process.pid}, age 7" in lines[0] and lines[0].endswith("s); skipping"), lines[0]
    assert "wake_batch.previous_firing_running" in result.stderr
    assert not (install["root"] / "stub-ran").exists(), "the provider wake ran"
    assert _record_path(install, "coder").read_text(encoding="utf-8") == record
    after = sorted(p.relative_to(install["root"]) for p in install["root"].rglob("*"))
    assert [p for p in after if p not in before and not str(p).startswith("locks")] == []


def test_overlapping_firings_of_one_agent_run_one_at_a_time(install) -> None:
    release = install["tmp"] / "release-first"
    first = subprocess.Popen(
        ["bash", str(install["script"]), "--agent=coder"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=_env(install, STUB_HOLD_UNTIL=str(release)), cwd=str(install["tmp"]),
    )
    try:
        ran = install["root"] / "stub-ran"
        _wait_for(ran.exists, "the first firing's batch to start")
        batch_pid = _record_pid(install, "coder")
        assert ran.read_text(encoding="utf-8").strip() == f"ran pid={batch_pid}"

        second = _fire(install, "--agent=coder")
        assert second.returncode == 0
        lines = _skip_lines(second.stdout, "coder")
        assert len(lines) == 1 and f"(pid {batch_pid}, age " in lines[0], second.stdout
        assert ran.read_text(encoding="utf-8").strip() == f"ran pid={batch_pid}", "the second firing ran"
    finally:
        release.touch()
        first.communicate(timeout=30)
    assert first.returncode == 0


def test_a_guard_left_by_a_dead_pid_is_taken_over(install) -> None:
    gone = subprocess.Popen(["true"])
    gone.wait()
    _write_record(install, "coder", gone.pid, age_seconds=600, pid_start="1")

    result = _fire(install, "--agent=coder")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _skip_lines(result.stdout, "coder") == []
    ran = (install["root"] / "stub-ran").read_text(encoding="utf-8").strip()
    assert ran.startswith("ran pid=")
    record = _record_path(install, "coder").read_text(encoding="utf-8")
    assert f"pid={ran.removeprefix('ran pid=')}\n" in record
    assert "holder=wake-batch\n" in record and "op=wake-batch:coder:demo\n" in record and "task=none\n" in record


def test_a_guard_whose_pid_was_reused_is_taken_over(install, live_process) -> None:
    _write_record(install, "coder", live_process.pid, age_seconds=600, pid_start="1")

    result = _fire(install, "--agent=coder")

    assert result.returncode == 0
    assert _skip_lines(result.stdout, "coder") == []
    assert (install["root"] / "stub-ran").exists()


def test_another_agents_running_firing_does_not_hold_this_one(install, live_process) -> None:
    _write_record(install, "coder", live_process.pid, age_seconds=10, pid_start=_proc_start(live_process.pid))

    result = _fire(install, "--agent=pm")

    assert result.returncode == 0
    assert (install["root"] / "stub-ran").exists()
    assert _record_path(install, "pm").is_file()


_RULE = "Firings of one agent never overlap within a project, while different projects run concurrently"


def _flat(text: str) -> str:
    return " ".join(text.replace("#", " ").split())


def test_usage_says_firings_never_overlap_within_a_project(install) -> None:
    header = (_SCRIPTS / "wake-batch.sh").read_text(encoding="utf-8").split("set -euo pipefail", 1)[0]
    assert _RULE in _flat(header)
    result = _fire(install, "--help")
    assert result.returncode == 0
    assert _RULE in _flat(result.stdout)


def test_one_firing_starts_one_batch_per_project_each_scoped_to_it(install) -> None:
    _register(install["root"], "alpha", "beta", "paused", disabled=("paused",))

    result = _fire(install, "--agent=coder")

    assert result.returncode == 0, result.stdout + result.stderr
    ran = _ran_projects(install)
    assert sorted(project for _, project in ran) == ["alpha", "beta"], ran
    for pid, project in ran:
        assert _record_pid(install, "coder", project) == pid
    assert not _record_path(install, "coder", "paused").exists()
    assert "wake-batch(coder): project paused: disabled in projects.cfg (enabled=false)" in result.stdout


def test_a_firing_while_project_1_runs_starts_project_2_only(install) -> None:
    _register(install["root"], "alpha", "beta")
    release = install["tmp"] / "release-alpha"
    first = subprocess.Popen(
        ["bash", str(install["script"]), "--agent=coder"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=_env(install, STUB_HOLD_UNTIL=str(release), STUB_HOLD_PROJECT="alpha"), cwd=str(install["tmp"]),
    )
    try:
        _wait_for(lambda: sorted(p for _, p in _ran_projects(install)) == ["alpha", "beta"], "both batches to start")
        alpha_pid = _record_pid(install, "coder", "alpha")
        beta_first_pid = _record_pid(install, "coder", "beta")
        _wait_for(lambda: not pathlib.Path(f"/proc/{beta_first_pid}").exists(), "beta's first batch to end")

        second = _fire(install, "--agent=coder")

        assert second.returncode == 0, second.stdout + second.stderr
        lines = _skip_lines(second.stdout, "coder")
        assert len(lines) == 1, second.stdout
        assert "for project alpha " in lines[0] and f"(pid {alpha_pid}, age " in lines[0], lines[0]
        ran = _ran_projects(install)
        assert [p for _, p in ran].count("alpha") == 1, ran
        assert [p for _, p in ran].count("beta") == 2, ran
        assert _record_pid(install, "coder", "beta") == ran[-1][0]
    finally:
        release.touch()
        first.communicate(timeout=30)
    assert first.returncode == 0


def test_an_empty_registry_starts_no_batch(install) -> None:
    (install["root"] / "projects.cfg").unlink()

    result = _fire(install, "--agent=coder")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "wake-batch(coder): no registered, enabled project in " in result.stdout
    assert not (install["root"] / "stub-ran").exists()
