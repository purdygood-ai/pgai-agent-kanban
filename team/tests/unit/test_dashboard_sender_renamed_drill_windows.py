"""
test_dashboard_sender_renamed_drill_windows.py
==============================================
Switching to a dashboard drill window wakes its panes.

create.sh arms a drill window's panes under its plain name ("drill-1.0" ...)
and then renames the window "drill-1: <project>", pinning the plain name as
the window's @pgai_arm_label option first. activation-hook-sender.sh, which
runs on every window switch, must find those armed markers through the label
-- the renamed window's own name never matches one -- while still waking a
board window's armed panes and still skipping a pane that never armed.

tmux is a stub on PATH answering exactly the three queries the sender makes
(has-session, display-message, list-panes) for a fixture session; every
"pane" is a small bash process that records the signal it receives (it
waits on `sleep`, so nothing spins). The armed markers live in a fixture
temp root; no real tmux server is involved.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import time
from collections.abc import Iterator

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SENDER = _TEAM_DIR / "scripts" / "dashboard" / "activation-hook-sender.sh"
_SESSION = "dash-probe"


class _Pane:
    """A process standing in for a pane's shell: records SIGUSR1 to a file, then exits."""

    def __init__(self, record: pathlib.Path) -> None:
        self.record = record
        self.proc = subprocess.Popen(
            ["bash", "-c", f"trap 'echo USR1 > \"{record}\"; exit 0' USR1; sleep 300 & wait"],
        )

    def signalled(self) -> bool:
        return self.record.exists()

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
        self.proc.wait(timeout=10)


@pytest.fixture
def panes(tmp_path: pathlib.Path) -> Iterator[dict[str, _Pane]]:
    started: dict[str, _Pane] = {}

    def start(label: str) -> _Pane:
        pane = _Pane(tmp_path / f"signal-{label.replace(' ', '_').replace(':', '')}")
        started[label] = pane
        return pane

    yield start  # type: ignore[misc]
    for pane in started.values():
        pane.stop()


def _write_tmux_stub(bin_dir: pathlib.Path, window_id: str, window_name: str, listing: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "listing.txt").write_text(listing, encoding="utf-8")
    stub = bin_dir / "tmux"
    stub.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{bin_dir}/calls.log"\n'
        'case "$1" in\n'
        "  has-session) exit 0 ;;\n"
        f"  display-message) printf '%s\\t%s\\n' '{window_id}' '{window_name}' ;;\n"
        f'  list-panes) cat "{bin_dir}/listing.txt" ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


def _armed_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    armed = tmp_path / "temp" / "dashboard" / "armed" / _SESSION
    armed.mkdir(parents=True, exist_ok=True)
    return armed


def _run_sender(tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "TMUX", "TMUX_PANE")}
    env.update(
        PATH=f"{tmp_path / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}",
        KANBAN_ROOT=str(root),
        PGAI_AGENT_KANBAN_ROOT_PATH=str(root),
        PGAI_AGENT_KANBAN_TEMP_DIR=str(tmp_path / "temp"),
    )
    return subprocess.run(
        ["bash", str(_SENDER), "--session", _SESSION, "--kanban-root", str(root)],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _wait_signalled(pane: _Pane) -> bool:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if pane.signalled():
            return True
        time.sleep(0.05)
    return False


def test_every_armed_pane_of_a_renamed_drill_window_is_signalled(tmp_path: pathlib.Path, panes) -> None:
    armed = _armed_dir(tmp_path)
    lines = []
    drill = []
    for index in range(5):
        pane = panes(f"drill-1: alpha.{index}")
        drill.append(pane)
        (armed / f"drill-1.{index}").touch()
        lines.append(f"drill-1: alpha.{index}\t%1{index}\t{pane.proc.pid}\tdrill-1")
    _write_tmux_stub(tmp_path / "bin", "@7", "drill-1: alpha", "\n".join(lines) + "\n")

    result = _run_sender(tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "", "the sender never writes to the pane it was switched to"
    assert all(_wait_signalled(pane) for pane in drill), [pane.signalled() for pane in drill]
    calls = (tmp_path / "bin" / "calls.log").read_text(encoding="utf-8")
    assert "list-panes -t @7 " in calls, f"the renamed window is listed by its id, not its name\n{calls}"


def test_board_panes_still_wake_and_unarmed_panes_are_still_skipped(tmp_path: pathlib.Path, panes) -> None:
    armed = _armed_dir(tmp_path)
    board_armed = panes("main.0")
    board_unarmed = panes("main.1")
    drill_armed = panes("drill-2: beta.0")
    drill_unarmed = panes("drill-2: beta.1")
    (armed / "main.0").touch()
    (armed / "drill-2.0").touch()
    listing = (
        f"main.0\t%1\t{board_armed.proc.pid}\t\n"
        f"main.1\t%2\t{board_unarmed.proc.pid}\t\n"
        f"drill-2: beta.0\t%3\t{drill_armed.proc.pid}\tdrill-2\n"
        f"drill-2: beta.1\t%4\t{drill_unarmed.proc.pid}\tdrill-2\n"
    )
    _write_tmux_stub(tmp_path / "bin", "@1", "main", listing)

    result = _run_sender(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _wait_signalled(board_armed), "an armed board pane must still be woken"
    assert _wait_signalled(drill_armed), "an armed drill pane must be woken"
    time.sleep(0.5)
    assert not board_unarmed.signalled(), "an unarmed board pane must be skipped"
    assert not drill_unarmed.signalled(), "an unarmed drill pane must be skipped"
    assert board_unarmed.proc.poll() is None and drill_unarmed.proc.poll() is None


def test_create_sh_pins_the_arm_label_before_renaming_a_drill_window() -> None:
    text = (_TEAM_DIR / "scripts" / "dashboard" / "create.sh").read_text(encoding="utf-8")
    label_at = text.find('@pgai_arm_label "${_DRILL_WIN}"')
    rename_at = text.find('tmux rename-window -t "${SESSION_NAME}:${_DRILL_WIN}"')
    assert label_at != -1, "create.sh must pin each drill window's arm label"
    assert rename_at != -1 and label_at < rename_at, "the label must be set before the rename"
