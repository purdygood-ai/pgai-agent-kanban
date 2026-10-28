"""
test_wake_now_overwatch_runs_sweep.py
=====================================
``wake-now.sh --agent overwatch`` runs the OVERWATCH sweep
(``overwatch-sweep.sh``), never a model wake (``wake-batch.sh
--agent=overwatch``), in the foreground and with ``--bg``; every other agent
still fires through ``wake-batch.sh``.

Each test runs the real wake-now.sh from a throwaway copy of this tree's
scripts/ in which overwatch-sweep.sh and wake-batch.sh are replaced by stubs
that record their argv, under a sandbox kanban root with every root variable
pointed into it, so nothing under the operator's live install is read or
written.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import time

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]

_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$(basename "$0") $*" >> "{record}"
"""


def _sandbox(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "tmp").mkdir()
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(_TEAM_DIR / "config", root / "config")
    record = tmp_path / "fired.txt"
    for name in ("overwatch-sweep.sh", "wake-batch.sh"):
        stub = root / "scripts" / name
        stub.write_text(_STUB.format(record=record), encoding="utf-8")
        stub.chmod(0o755)
    return root, record


def _wake_now(tmp_path: pathlib.Path, root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(root / "scripts" / "wake-now.sh"), *args],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
        stdin=subprocess.DEVNULL, timeout=60,
    )


def _fired(record: pathlib.Path, expect: int = 1) -> list[str]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        lines = record.read_text(encoding="utf-8").splitlines() if record.is_file() else []
        if len(lines) >= expect:
            return lines
        time.sleep(0.1)
    return record.read_text(encoding="utf-8").splitlines() if record.is_file() else []


@pytest.mark.parametrize("extra", [[], ["--bg"]], ids=["foreground", "background"])
def test_overwatch_runs_the_sweep_not_a_model_wake(tmp_path: pathlib.Path, extra: list[str]) -> None:
    root, record = _sandbox(tmp_path)

    result = _wake_now(tmp_path, root, "--agent", "overwatch", *extra)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _fired(record) == ["overwatch-sweep.sh "], (
        f"expected exactly one bare overwatch-sweep.sh run\n{result.stdout}{result.stderr}"
    )


def test_a_pipeline_agent_still_fires_through_wake_batch(tmp_path: pathlib.Path) -> None:
    root, record = _sandbox(tmp_path)

    result = _wake_now(tmp_path, root, "--agent", "coder")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _fired(record) == ["wake-batch.sh --agent=coder --sleep=0"], result.stdout + result.stderr


def test_help_names_what_overwatch_runs(tmp_path: pathlib.Path) -> None:
    root, _record = _sandbox(tmp_path)

    result = _wake_now(tmp_path, root, "--help")

    assert result.returncode == 0, result.stderr
    agent_lines = [line for line in (result.stdout + result.stderr).splitlines() if "--agent" in line]
    assert any("overwatch (runs overwatch-sweep.sh)" in line for line in agent_lines), agent_lines
