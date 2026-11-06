"""
test_exit_trap_chaining.py
==========================
team/scripts/lib/exit_trap.sh chains EXIT cleanups instead of replacing
them, and the scripts and libraries that used bare EXIT traps go through it:

  1. Registered commands run most recent first, then the EXIT trap that was
     installed before the first one; each sees the exiting status as $?,
     a failing one does not stop the rest, and the script's exit status is
     kept. A removed command does not run.
  2. A command registered in a subshell runs when the subshell exits and
     never runs the parent's commands there; the parent's still run once.
  3. A trap that replaced the dispatcher is kept and run after the chain
     when another command is registered.
  4. dashboard/event-band.sh, rendering a day's events (so it creates all
     three of its scratch files), leaves none of them behind on exit.
  5. A library sourced after a script set its own EXIT trap
     (lock_provenance.sh standalone, docker_use.sh's session) keeps that
     trap running.
  6. No file in team/scripts/lib/ except exit_trap.sh installs a bare
     `trap ... EXIT`.
  7. wake/claude.sh and wake/codex.sh enable strict mode on their first
     executable line.

Every run is a bash subprocess against a sandbox under tmp_path.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

from tests.fixtures.local_timestamps import today_local_iso
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_EXIT_TRAP = _LIB_DIR / "exit_trap.sh"
_EVENT_BAND = _TEAM_DIR / "scripts" / "dashboard" / "event-band.sh"


def _bash(tmp_path: pathlib.Path, script: str, **extra_env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        env=build_scrubbed_env(home_dir=tmp_path, extra_env=extra_env),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )


def test_commands_run_most_recent_first_then_the_earlier_trap(tmp_path) -> None:
    result = _bash(
        tmp_path,
        f"""
        set -euo pipefail
        trap 'echo "earlier rc=$?"' EXIT
        source '{_EXIT_TRAP}'
        exit_trap_add 'echo "first rc=$?"'
        exit_trap_add 'false'
        exit_trap_add 'echo "dropped"'
        exit_trap_add 'echo "last rc=$?"'
        exit_trap_remove 'echo "dropped"'
        exit 3
    """,
    )

    assert result.returncode == 3, result.stderr
    assert result.stdout.splitlines() == ["last rc=3", "first rc=3", "earlier rc=3"], result.stdout


def test_a_subshell_keeps_its_own_chain(tmp_path) -> None:
    result = _bash(
        tmp_path,
        f"""
        set -euo pipefail
        source '{_EXIT_TRAP}'
        exit_trap_add 'echo parent'
        ( exit_trap_add 'echo child'; exit 0 )
        echo between
    """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["child", "between", "parent"], result.stdout


def test_a_trap_that_replaced_the_dispatcher_still_runs(tmp_path) -> None:
    result = _bash(
        tmp_path,
        f"""
        source '{_EXIT_TRAP}'
        exit_trap_add 'echo a'
        trap 'echo replaced' EXIT
        exit_trap_add 'echo b'
    """,
    )

    assert result.stdout.splitlines() == ["b", "a", "replaced"], result.stdout


def test_event_band_removes_all_three_scratch_files_on_exit(tmp_path) -> None:
    root = tmp_path / "kanroot"
    logs = root / "logs"
    logs.mkdir(parents=True)
    (logs / "cron-coder.log").write_text(
        f"[{today_local_iso(9, 0, 20)}] wake(coder): task CODER-example: transitioned to WORKING [A]\n"
        f"[{today_local_iso(9, 1, 0)}] wake(coder): finished task CODER-example with state DONE\n",
        encoding="utf-8",
    )
    temp_root = tmp_path / "temp"
    temp_root.mkdir()

    result = subprocess.run(
        ["bash", str(_EVENT_BAND), "--kanban-root", str(root)],
        env=build_scrubbed_env(
            home_dir=tmp_path,
            extra_env={
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_root),
                "NO_COLOR": "1",
                "TERM": "dumb",
            },
        ),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "CODER-example" in result.stdout, result.stdout
    leftovers = sorted(p.name for p in temp_root.rglob("event-band*"))
    assert leftovers == [], f"event-band.sh left scratch files behind: {leftovers}"


def test_lock_provenance_sourced_standalone_keeps_the_callers_trap(tmp_path) -> None:
    result = _bash(
        tmp_path,
        f"""
        trap 'echo caller-cleanup' EXIT
        source '{_LIB_DIR / "lock_provenance.sh"}'
        pgai_lock_release_all_held() {{ echo released; }}
        exit 0
    """,
        PGAI_AGENT_KANBAN_TEMP_DIR=str(tmp_path / "temp"),
    )

    # With a trap already set the library composes through the pre-hook,
    # so the caller's own trap is the one that runs and is never replaced.
    assert "caller-cleanup" in result.stdout.splitlines(), result.stdout + result.stderr


def test_lock_provenance_with_no_trap_registers_through_the_chain(tmp_path) -> None:
    result = _bash(
        tmp_path,
        f"""
        source '{_LIB_DIR / "lock_provenance.sh"}'
        pgai_lock_release_all_held() {{ echo released; }}
        exit_trap_add 'echo script-cleanup'
        exit 0
    """,
        PGAI_AGENT_KANBAN_TEMP_DIR=str(tmp_path / "temp"),
    )

    assert result.stdout.splitlines() == ["script-cleanup", "released"], result.stdout + result.stderr


def test_docker_use_session_keeps_the_callers_trap(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    result = _bash(
        tmp_path,
        f"""
        trap 'echo caller-cleanup' EXIT
        source '{_LIB_DIR / "docker_use.sh"}'
        docker_use_session_begin probe.sh -
        exit 0
    """,
        PGAI_AGENT_KANBAN_ROOT_PATH=str(root),
    )

    assert result.stdout.splitlines() == ["caller-cleanup"], result.stdout + result.stderr
    log = (root / "logs" / "docker-use.log").read_text(encoding="utf-8")
    assert " START " in log and " FINISH " in log and "exit=0" in log, log


# A `trap` command whose signal list names EXIT, outside a comment.
_BARE_EXIT_TRAP = re.compile(r"^\s*(?!#)(?:.*[;&|]\s*)?trap\s+(?!-p\b)(?:--\s+)?\S.*\bEXIT\b")


def test_no_library_installs_a_bare_exit_trap() -> None:
    offenders = []
    for path in sorted(_LIB_DIR.glob("*.sh")):
        if path == _EXIT_TRAP:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _BARE_EXIT_TRAP.search(line):
                offenders.append(f"{path.relative_to(_TEAM_DIR)}:{number}: {line.strip()}")
    assert offenders == [], (
        "these lib files replace the caller's EXIT trap; register the cleanup with "
        "exit_trap_add from team/scripts/lib/exit_trap.sh instead:\n" + "\n".join(offenders)
    )


def test_the_bare_trap_check_catches_the_shapes_it_names() -> None:
    assert _BARE_EXIT_TRAP.search("    trap 'rm -f \"$X\"' EXIT")
    assert _BARE_EXIT_TRAP.search("trap cleanup EXIT INT")
    assert _BARE_EXIT_TRAP.search("trap - EXIT")
    assert not _BARE_EXIT_TRAP.search('    existing="$(trap -p EXIT)"')
    assert not _BARE_EXIT_TRAP.search("# trap cleanup EXIT")


def _first_executable_line(path: pathlib.Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def test_the_wake_entrypoints_are_strict_from_their_first_line() -> None:
    for name in ("claude.sh", "codex.sh"):
        path = _TEAM_DIR / "scripts" / "wake" / name
        assert _first_executable_line(path) == "set -euo pipefail", path
