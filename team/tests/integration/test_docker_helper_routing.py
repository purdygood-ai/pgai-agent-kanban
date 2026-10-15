"""
test_docker_helper_routing.py
=============================
End-to-end proof that the scripts whose container-engine calls go through
the shared helper (pgai_docker, team/scripts/lib/docker_use.sh) really do:
each run lands in the install's logs/docker-use.log with the right events,
and the engine sees exactly the commands the script meant to send.

The "engine" is a stub on PATH that records every call and answers with a
fixed status -- the scripts, the helper, the log and the sandboxed kanban
root are all real. No real container engine is ever invoked.

  * container-shell.sh launches the workspace container through the
    helper: one START/FINISH pair naming the run command, and the shell's
    own exit status is the engine's.
  * run-three-host-matrix.sh, authorized by the operator, records one
    START/FINISH session for the whole run with the selected image, and
    every engine command it issues (daemon probe, image listing, base
    build, teardown) reaches the stub.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_SCRIPTS = _TEAM_DIR / "scripts"

_ENGINE_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{log}"
case "$1" in
  info|version|images|ps) exit 0 ;;
esac
exit {status}
"""


def _sandbox(tmp_path: pathlib.Path, status: int) -> tuple[dict[str, str], pathlib.Path, pathlib.Path]:
    root = tmp_path / "kanban_root"
    (root / "projects").mkdir(parents=True)
    temp = tmp_path / "tmp"
    temp.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    engine_log = tmp_path / "engine.log"
    stub = bin_dir / "docker"
    stub.write_text(_ENGINE_STUB.format(log=engine_log, status=status), encoding="utf-8")
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME", "PGAI_AGENT_WAKE_ACTIVE",
                        "PGAI_AGENT_ROLE", "PGAI_TASK_ID", "PGAI_CONTAINER_TIER", "PGAI_DOCKER_USE_SESSION")}
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        HOME=str(home),
        KANBAN_ROOT=str(root),
        PGAI_AGENT_KANBAN_ROOT_PATH=str(root),
        PGAI_AGENT_KANBAN_TEMP_DIR=str(temp),
        PGAI_DEV_TREE_PATH=str(_REPO_ROOT),
    )
    return env, root / "logs" / "docker-use.log", engine_log


def test_container_shell_launch_is_recorded_and_returns_the_engine_status(tmp_path: pathlib.Path) -> None:
    env, use_log, engine_log = _sandbox(tmp_path, status=7)
    result = subprocess.run(
        ["bash", str(_SCRIPTS / "container-shell.sh"), "--image-tag", "pgai-agent-kanban-debian:latest"],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60,
    )
    assert result.returncode == 7, result.stderr
    calls = engine_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1 and calls[0].startswith("run --rm -it "), calls
    assert calls[0].endswith(" pgai-agent-kanban-debian:latest"), calls
    lines = use_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, lines
    assert re.match(r"^\S+Z START actor=operator task=- script=container-shell\.sh images=- command=docker_run$", lines[0]), lines
    assert re.match(r"^\S+Z FINISH actor=operator task=- script=container-shell\.sh images=- command=docker_run "
                    r"duration=\d+s exit=7$", lines[1]), lines


def test_three_host_matrix_run_is_one_recorded_session_with_every_engine_call_routed(tmp_path: pathlib.Path) -> None:
    env, use_log, engine_log = _sandbox(tmp_path, status=1)
    env["PGAI_CONTAINER_TIER"] = "1"
    result = subprocess.run(
        ["bash", str(_SCRIPTS / "run-three-host-matrix.sh"), "--host", "gitless", "--image", "debian",
         "--engine", "docker"],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120,
    )
    # The stub engine fails every build, so the host is not-attempted.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "--- host: gitless@debian ---" in result.stdout
    assert "docker/debian/Dockerfile build failed" in result.stdout
    calls = engine_log.read_text(encoding="utf-8").splitlines()
    verbs = [call.split()[0] for call in calls]
    assert verbs[0] == "info", calls
    assert "build" in verbs and "images" in verbs and "ps" in verbs, calls
    assert any(call.startswith("build -f ") and "/docker/debian/Dockerfile" in call for call in calls), calls
    lines = use_log.read_text(encoding="utf-8").splitlines()
    assert [line.split()[1] for line in lines] == ["START", "FINISH"], lines
    assert "script=run-three-host-matrix.sh images=debian" in lines[0], lines
    assert lines[1].endswith(" exit=1"), lines


_DOWN_ENGINE_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{log}"
exit 1
"""

_DOC_WITH_DOCKER_BLOCKS = """# Demo

<!-- doc-lint: docker -->
```bash
touch {marker}
```

```bash
docker compose exec web true
```
"""


def test_doc_lint_engine_probes_go_through_the_helper_and_are_not_logged_as_use(tmp_path: pathlib.Path) -> None:
    """doc-lint.sh's two engine probes (the docker-gated block's daemon
    check, and the compose-exec service check) reach the engine through
    the helper; they are read-only probes, so the docker-use log records
    nothing, and with the engine down both blocks are skipped, not run."""
    env, use_log, engine_log = _sandbox(tmp_path, status=1)
    (pathlib.Path(env["PATH"].split(os.pathsep)[0]) / "docker").write_text(
        _DOWN_ENGINE_STUB.format(log=engine_log), encoding="utf-8"
    )
    doc_root = tmp_path / "doc_root"
    doc_root.mkdir()
    marker = tmp_path / "docker-gated-block-ran"
    (doc_root / "README.md").write_text(_DOC_WITH_DOCKER_BLOCKS.format(marker=marker), encoding="utf-8")
    result = subprocess.run(
        ["bash", str(_SCRIPTS / "harness" / "doc-lint.sh"), "--suite", "cmd", "--doc-root", str(doc_root), "--verbose"],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120,
    )
    output = result.stdout + result.stderr
    assert not marker.exists(), "the docker-gated block ran with the engine down"
    assert "SKIP-DOCKER" in output and "SKIP-SERVICE" in output, output
    assert "pass=0 skip=2 fail=0" in output, output
    calls = engine_log.read_text(encoding="utf-8").splitlines()
    assert "info" in calls, calls
    assert "compose ps --status running web" in calls, calls
    assert not use_log.exists(), "read-only engine probes are not recorded as docker use"
