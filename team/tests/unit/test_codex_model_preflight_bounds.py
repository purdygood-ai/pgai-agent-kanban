"""
test_codex_model_preflight_bounds.py
====================================
The codex model preflight is a real model request made while the wake holds
the project's repo lock, so it is bounded three ways:

  (1) Only a firing with a dispatchable task runs it: a real
      ``scripts/wake/codex.sh`` firing against an empty queue never calls
      ``codex exec``.
  (2) A PASS is remembered per model for [wake] model_preflight_ttl_seconds:
      two preflights inside the window call the stub once; with the TTL at
      0 each calls it; a FAIL is not remembered.
  (3) The call is bounded by [wake] model_preflight_timeout_seconds: a stub
      that sleeps past it makes the preflight fail within the bound, and a
      real firing with a pending task finishes promptly, leaves the task
      BACKLOG and the repo lock free.
  (4) An error event reporting a usage or rate limit is reported as a limit,
      not as a rejected model with advice to change the model config; a real
      model rejection is still reported as one.

Every ``codex`` is a PATH-shadowing bash stub that records each call; no
real provider is reached. The full-firing tests run against a throwaway
kanban root (a copy of this tree's scripts/, pgai_agent_kanban/, workflows/,
pm-agent/ and config/, as install.sh mirrors them) with a scrubbed
environment; the sleeping stub sleeps, it never spins.
"""

from __future__ import annotations

import fcntl
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = _TEAM_DIR / "scripts"
_PROVIDER = _SCRIPTS / "lib" / "provider_codex.sh"
_MIRRORED_DIRS = ("scripts", "pgai_agent_kanban", "workflows", "pm-agent", "config")
_PROJECT = "preflight-probe"
_AGENTS = ("pm", "coder", "writer", "tester", "cm", "overwatch")
_PENDING = "CODER-20260101-001-pending-work"

_PASS_STREAM = '{"type":"thread.started","thread_id":"t"}\n{"type":"turn.completed","usage":{}}\n'
_RATE_LIMIT_STREAM = '{"type":"error","message":"stream error: exceeded retry limit, last status: 429 Too Many Requests"}\n'
_USAGE_LIMIT_STREAM = '{"type":"error","message":"You\'ve hit your usage limit. Try again in 2h."}\n'
_REJECT_STREAM = '{"type":"error","message":"The model `gpt-nope` does not exist or you do not have access to it."}\n'


def _write_stub(bin_dir: pathlib.Path, calls: pathlib.Path, *, stream: str = _PASS_STREAM, sleep_s: int = 0) -> None:
    """A fake ``codex``: records each call's argv on one line, then answers.

    ``--version`` prints a version; any other call prints *stream* (after
    sleeping *sleep_s* seconds when set) and exits 0.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "codex"
    stub.write_text(
        "#!/bin/bash\n"
        'if [[ "${1:-}" == "--version" ]]; then echo "codex-cli 0.0.0-stub"; exit 0; fi\n'
        f'printf "%s\\n" "$*" >> "{calls}"\n'
        + (f"sleep {sleep_s}\n" if sleep_s else "")
        + f"cat <<'JSONL'\n{stream}JSONL\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


def _exec_calls(calls: pathlib.Path) -> list[str]:
    if not calls.exists():
        return []
    return [line for line in calls.read_text(encoding="utf-8").splitlines() if line.startswith("exec ")]


# ---------------------------------------------------------------------------
# Direct: the real provider_model_preflight, sourced
# ---------------------------------------------------------------------------


def _preflight(tmp_path: pathlib.Path, *, runs: int = 1, ttl: str = "3600", timeout: str = "60",
               model: str = "gpt-test") -> tuple[list[int], str, float]:
    """Call provider_model_preflight *runs* times; return each status, the log and the wall time."""
    log = tmp_path / "wake.log"
    script = tmp_path / "preflight.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'export SCRIPT_DIR="{_SCRIPTS / "wake"}"\n'
        f'source "{_PROVIDER}"\n'
        "AGENT=coder\n"
        f"for _ in $(seq {runs}); do\n"
        "  rc=0\n"
        f'  provider_model_preflight "{model}" "{log}" >/dev/null || rc=$?\n'
        '  echo "rc=$rc"\n'
        "done\n",
        encoding="utf-8",
    )
    env = build_scrubbed_env(
        home_dir=tmp_path,
        extra_path_dirs=[tmp_path / "bin"],
        extra_env={
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp"),
            "MODEL_PREFLIGHT_TTL_SECONDS": ttl,
            "MODEL_PREFLIGHT_TIMEOUT_SECONDS": timeout,
        },
    )
    started = time.monotonic()
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=120)
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    statuses = [int(line[3:]) for line in result.stdout.splitlines() if line.startswith("rc=")]
    return statuses, log.read_text(encoding="utf-8") if log.exists() else "", elapsed


def test_two_preflights_inside_the_ttl_call_codex_once(tmp_path: pathlib.Path) -> None:
    calls = tmp_path / "calls.log"
    _write_stub(tmp_path / "bin", calls)

    statuses, log, _ = _preflight(tmp_path, runs=2)

    assert statuses == [0, 0], log
    assert len(_exec_calls(calls)) == 1, log
    assert "remembered for 3600s; no codex call" in log, log


def test_a_zero_ttl_checks_on_every_preflight(tmp_path: pathlib.Path) -> None:
    calls = tmp_path / "calls.log"
    _write_stub(tmp_path / "bin", calls)

    statuses, log, _ = _preflight(tmp_path, runs=2, ttl="0")

    assert statuses == [0, 0], log
    assert len(_exec_calls(calls)) == 2, log


def test_a_failed_preflight_is_not_remembered(tmp_path: pathlib.Path) -> None:
    calls = tmp_path / "calls.log"
    _write_stub(tmp_path / "bin", calls, stream=_REJECT_STREAM)

    statuses, log, _ = _preflight(tmp_path, runs=2)

    assert statuses == [1, 1], log
    assert len(_exec_calls(calls)) == 2, log


def test_a_codex_call_that_outlives_the_timeout_fails_within_it(tmp_path: pathlib.Path) -> None:
    calls = tmp_path / "calls.log"
    _write_stub(tmp_path / "bin", calls, sleep_s=60)

    statuses, log, elapsed = _preflight(tmp_path, timeout="1")

    assert statuses == [1], log
    assert elapsed < 30, f"the preflight must stop the call near its 1s bound, took {elapsed:.1f}s"
    assert "did not answer within 1s" in log, log
    assert "rejected" not in log, log


@pytest.mark.parametrize("stream", [_RATE_LIMIT_STREAM, _USAGE_LIMIT_STREAM])
def test_a_limit_error_event_is_not_reported_as_a_model_rejection(tmp_path: pathlib.Path, stream: str) -> None:
    _write_stub(tmp_path / "bin", tmp_path / "calls.log", stream=stream)

    statuses, log, _ = _preflight(tmp_path)

    assert statuses == [1], log
    assert "reported a usage or rate limit, not a model problem" in log, log
    assert "rejected by codex" not in log, log
    assert "fix the model" not in log, log


def test_a_model_rejection_is_still_reported_as_one(tmp_path: pathlib.Path) -> None:
    _write_stub(tmp_path / "bin", tmp_path / "calls.log", stream=_REJECT_STREAM)

    statuses, log, _ = _preflight(tmp_path)

    assert statuses == [1], log
    assert "model 'gpt-test' rejected by codex" in log, log
    assert "fix the model" in log, log


# ---------------------------------------------------------------------------
# Full firing: the real scripts/wake/codex.sh against a sandbox root
# ---------------------------------------------------------------------------


class _Sandbox:
    """A throwaway kanban root with one document-workflow project and codex active."""

    def __init__(self, tmp_path: pathlib.Path, *, timeout_s: int = 60) -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "kanban"
        self.project = self.root / "projects" / _PROJECT
        self.tasks = self.project / "tasks"
        self.queues = self.tasks / "queues"
        self.bin_dir = tmp_path / "stub-bin"
        self.calls = tmp_path / "codex-calls.log"
        scratch = tmp_path / "scratch"
        scratch.mkdir(parents=True)
        self.queues.mkdir(parents=True)
        (self.root / "logs").mkdir(parents=True)
        (self.root / "locks").mkdir(parents=True)
        (self.root / "kanban.cfg").write_text(
            f"[paths]\ntmp_root = {scratch}\ntmp_subdir = pgai_kanban_tmp\n\n"
            "[providers]\nactive = codex\n\n"
            "[chain]\npm_mode = manual\n\n"
            "[wake]\nmax_tasks_per_wake = 1\n"
            f"model_preflight_timeout_seconds = {timeout_s}\n",
            encoding="utf-8",
        )
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8")
        (self.project / "project.cfg").write_text(
            f"[project]\nproject_name = {_PROJECT}\nworkflow_type = document\n", encoding="utf-8"
        )
        for agent in _AGENTS:
            (self.queues / f"{agent}_backlog.md").write_text(f"# {agent} backlog\n\n", encoding="utf-8")
        for name in _MIRRORED_DIRS:
            shutil.copytree(_TEAM_DIR / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))

    def add_pending_task(self, task_id: str) -> None:
        task_dir = self.tasks / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "README.md").write_text(
            f"# Task: {task_id}\n\n## Task ID\n{task_id}\n\n## Role\nCODER\n\n"
            "## Workflow Type\ndocument\n\n## Git Repo\nnone\n\n## Working Directory\nnone\n\n"
            "## Source Branch\nnone\n\n## Prerequisites\nnone\n",
            encoding="utf-8",
        )
        (task_dir / "status.md").write_text(
            f"# Status\n\n## Task\n{task_id}\n\n## State\nBACKLOG\n\n## Summary\nnone\n\n"
            "## Blockers\nnone\n\n## Needs Human\nno\n",
            encoding="utf-8",
        )
        with (self.queues / "coder_backlog.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- [ ] {task_id} — fixture task\n")

    def state(self, task_id: str) -> str:
        text = (self.tasks / task_id / "status.md").read_text(encoding="utf-8")
        return text.split("## State", 1)[1].strip().splitlines()[0].strip()

    def wake(self) -> subprocess.CompletedProcess:
        env = build_scrubbed_env(
            home_dir=self.tmp_path / "home",
            extra_path_dirs=[self.bin_dir, pathlib.Path(sys.executable).parent],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.tmp_path / "scratch"),
                "PAUSE_BETWEEN_TASKS": "0",
            },
        )
        (self.tmp_path / "home").mkdir(exist_ok=True)
        cwd = self.tmp_path / "foreign_cwd"
        cwd.mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "wake" / "codex.sh"), "--agent=coder"],
            cwd=cwd, env=env, capture_output=True, text=True, timeout=240,
        )


def _output(result: subprocess.CompletedProcess) -> str:
    return f"exit={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def test_a_firing_with_an_empty_queue_never_calls_codex_exec(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path)
    _write_stub(box.bin_dir, box.calls)

    result = box.wake()

    assert result.returncode == 0, _output(result)
    assert _exec_calls(box.calls) == [], _output(result)
    assert "model-preflight skipped" in result.stdout + result.stderr, _output(result)


def test_a_hung_preflight_ends_the_firing_promptly_and_frees_the_repo_lock(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path, timeout_s=2)
    box.add_pending_task(_PENDING)
    _write_stub(box.bin_dir, box.calls, sleep_s=120)

    started = time.monotonic()
    result = box.wake()
    elapsed = time.monotonic() - started

    out = result.stdout + result.stderr
    assert result.returncode == 0, _output(result)
    assert elapsed < 120, f"the firing waited on the hung preflight ({elapsed:.0f}s)\n{_output(result)}"
    assert "did not answer within 2s" in out, _output(result)
    assert len(_exec_calls(box.calls)) == 1, _output(result)
    assert box.state(_PENDING) == "BACKLOG", "a failed preflight leaves the queue untouched"
    for lock in (box.root / "locks").glob("*.lock"):
        with lock.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if still held
