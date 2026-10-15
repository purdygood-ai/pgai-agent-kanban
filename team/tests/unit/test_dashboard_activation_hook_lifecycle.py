"""
test_dashboard_activation_hook_lifecycle.py
=============================================
Coverage for the session-scoped tmux activation hook
(team/scripts/dashboard/lib/activation_hook.sh) and its sender
(team/scripts/dashboard/activation-hook-sender.sh).

Background: the board had no activation hook at all, which is why a
window could not refresh the instant it became active. This module
covers the lifecycle: create.sh installs the hook only after every pane
has proven its signal trap is armed (lib/pane_readiness.sh's all-armed
handshake); the hook is scoped to the dashboard's own tmux session so it
can never fire against an unrelated session on the same server; kill.sh
removes it and stays idempotent when the session is already gone; and the
sender never dies on an unarmed pane -- it skips, narrates which pane it
skipped and why, and still signals every other pane.

Test groups
-----------
Group 1 -- Static coverage (no tmux required):
    create.sh's call to dashboard_install_activation_hook appears, in
    source order, strictly after the all-armed handshake resolves and
    strictly after every dashboard_send_armed_keys call site -- the
    regression-proof form of "installed only after every pane has
    reported armed."

Group 2 -- Library-level lifecycle behavior (tmux-gated, private socket):
    dashboard_install_activation_hook / dashboard_remove_activation_hook
    exercised directly against synthetic sessions on a private tmux
    server -- the session-scope assertion (a second session is
    unaffected), and idempotent removal.

Group 3 -- Sender behavior (tmux-gated, private socket): the skip-and-
    narrate contract against a synthetic armed/unarmed pane pair, proving
    both halves in one run -- the armed pane actually receives the
    signal (a custom, non-ignoring trap the pane installs for this test
    only proves delivery), and the unarmed pane is never signalled at
    all (it survives untouched, which a real send would not have let it
    do under the OS default disposition).

Timing: every verdict depends only on the hook library and the sender.
No wait in this module is a fixed span of wall-clock time. Each tmux
command, library call and sender run is waited on until it exits; a pane
is used only once it has written its own ready file (after installing its
trap, for the armed pane); a backgrounded hook command and a delivered
signal are waited on until their marker file appears. The one guard on
every wait is a stall guard from tests/fixtures/process_progress.py: no
process on the private tmux server, and no process the test started,
showing any change for its whole bound. Pane survival is checked against
each pane's own process as well as tmux's pane_dead flag, so a pane that
was signalled to death fails the test even before tmux reaps it.

Docker is not used anywhere in this module -- every tmux operation runs on
a private socket on the test host directly (tmux -L <private>), matching
the same convention team/tests/unit/test_dashboard_pane_signal_traps.py
already establishes. Tmux-dependent groups narrate a skip (never a
silent one) when tmux is not on PATH.

Test naming (roles/TESTER.md anti-pattern 6): names describe behavior,
never bug IDs, scaffolding labels, or version strings.
"""

from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import uuid
from typing import Iterator, NamedTuple

import pytest

from tests.fixtures.binary_guard import (
    narrate_binary_skip,
    teardown_private_tmux,
    try_resolve_binary,
)
from tests.fixtures.process_progress import (
    pane_process_death,
    proc_start_time,
    process_tree_activity,
    require_procfs,
    run_until_exit,
    wait_while_progressing,
)

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's location, matching every other
# dashboard test module.
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_ACTIVATION_HOOK_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "activation_hook.sh"
_SENDER_SH = _TEAM_DIR / "scripts" / "dashboard" / "activation-hook-sender.sh"

_TMUX_SKIP_LABEL = "activation-hook-lifecycle"


# ---------------------------------------------------------------------------
# Private tmux socket fixture -- same convention as
# test_dashboard_pane_signal_traps.py's PrivateTmux.
# ---------------------------------------------------------------------------
class PrivateTmux(NamedTuple):
    real_tmux: str
    socket_name: str
    env: dict[str, str]

    def run(self, *args: str) -> subprocess.CompletedProcess:
        """Run one tmux command against the private server and wait for it
        to exit, guarded only against a stall of the client and server."""
        return run_until_exit(
            [self.real_tmux, "-L", self.socket_name, *args],
            cmdline_marker=self.socket_name,
        )

    def activity(self) -> frozenset:
        """Progress probe: every process on this private server (the
        server, its panes, and anything a hook backgrounded) and its CPU
        use. It stops changing only when nothing on the server is doing
        anything at all."""
        return process_tree_activity(cmdline_marker=self.socket_name)


@pytest.fixture
def private_tmux(tmp_path: pathlib.Path) -> Iterator[PrivateTmux]:
    # Resolve without skipping yet: the skip decision below happens INSIDE
    # the try block so that, on a scrubbed PATH, this fixture's own finally
    # clause genuinely runs guard.teardown() as a real no-op (proving the
    # teardown path exercised, not merely reasoned about) instead of the
    # skip unwinding before the try is ever entered.
    guard = try_resolve_binary("tmux")
    socket_name = f"pgai-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        if not guard.resolved:
            narrate_binary_skip("tmux", label=_TMUX_SKIP_LABEL)
        require_procfs(_TMUX_SKIP_LABEL)
        real_tmux = guard.path

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "tmux"
        wrapper.write_text(
            f"#!/usr/bin/env bash\nexec {shlex.quote(real_tmux)} -L {shlex.quote(socket_name)} \"$@\"\n"
        )
        wrapper.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

        pt = PrivateTmux(real_tmux=real_tmux, socket_name=socket_name, env=env)
        yield pt
    finally:
        teardown_private_tmux(
            guard,
            socket_name,
            kill=lambda path: run_until_exit(
                [path, "-L", socket_name, "kill-server"],
                cmdline_marker=socket_name,
            ),
        )


# ---------------------------------------------------------------------------
# Pane and sender helpers shared by the tmux-gated groups
# ---------------------------------------------------------------------------


def _wait_for_file(private_tmux: PrivateTmux, path: pathlib.Path, *, what: str, on_stall: str) -> None:
    """Wait until <path> exists -- written by a pane, or by a command the
    private server backgrounded -- for as long as anything on the server
    is still active. <on_stall> is the verdict when nothing is."""
    wait_while_progressing(path.exists, private_tmux.activity, what=what, on_stall=on_stall)


def _wait_for_panes_ready(private_tmux: PrivateTmux, ready_files: dict[str, pathlib.Path]) -> None:
    """Wait until every pane in <ready_files> ({pane target: ready file})
    has written its ready file, i.e. its shell has started and run every
    command sent to it ahead of the `touch`."""
    for pane, ready in ready_files.items():
        _wait_for_file(
            private_tmux,
            ready,
            what=f"pane {pane} starting its command",
            on_stall=f"pane {pane} never ran the command sent to it; this is a fixture failure, not a sender verdict",
        )


def _pane_processes(private_tmux: PrivateTmux, window: str) -> dict[str, tuple[int, str]]:
    """{pane label: (pane_pid, kernel start time)} for every pane in <window>."""
    listed = private_tmux.run("list-panes", "-t", window, "-F", "#{window_name}.#{pane_index} #{pane_pid}")
    assert listed.returncode == 0, listed.stderr
    panes: dict[str, tuple[int, str]] = {}
    for line in listed.stdout.splitlines():
        label, pid_text = line.split()
        start_time = proc_start_time(int(pid_text))
        assert start_time is not None, f"pane {label}'s process {pid_text} vanished before the sender ran"
        panes[label] = (int(pid_text), start_time)
    return panes


def _assert_pane_processes_alive(panes: dict[str, tuple[int, str]], after_what: str) -> None:
    """Every pane process in <panes> is still alive and not being killed,
    read from /proc ahead of tmux reaping a dead pane."""
    deaths = {
        label: death
        for label, (pid, start_time) in panes.items()
        if (death := pane_process_death(pid, start_time)) is not None
    }
    assert not deaths, (
        f"expected every pane to survive {after_what}; these panes' processes "
        f"died (checked directly, ahead of tmux reaping them): {deaths}"
    )


def _run_sender(private_tmux: PrivateTmux, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    """Run the sender to completion against the private server."""
    return run_until_exit(
        [
            "bash", str(_SENDER_SH),
            # Sample data, not a filesystem target: the sender resolves its
            # armed-marker directory via PGAI_AGENT_KANBAN_TEMP_DIR (set in
            # every caller's env), which wins ahead of --kanban-root in
            # temp.sh's resolution order, so this value is never read from
            # disk.
            "--kanban-root", "/tmp/kanban-root-unused",
            *args,
        ],
        env=env,
        cmdline_marker=private_tmux.socket_name,
    )


# ---------------------------------------------------------------------------
# Group 1 -- Static coverage (no tmux required)
# ---------------------------------------------------------------------------


def test_hook_install_call_site_follows_the_all_armed_handshake_in_source_order() -> None:
    """
    The regression-proof form of "create.sh installs the hook only after
    every pane has reported armed": the install call must appear, in
    source-line order, strictly after the all-armed handshake's own
    success path (the `unset _ARM_PENDING _ARM_STATUS` line that only
    runs once dashboard_wait_all_armed returned zero) and strictly after
    every dashboard_send_armed_keys pane dispatch. A future edit that
    moves the install call earlier (e.g. back into the pane-creation
    block) fails this test immediately.
    """
    text = _CREATE_SH.read_text()

    install_pos = text.index("dashboard_install_activation_hook ")
    handshake_resolved_pos = text.index("unset _ARM_PENDING _ARM_STATUS")

    # rfind on the bare call name finds the LAST pane-dispatch call site in
    # the file, which must still precede both the handshake and the
    # install call.
    last_send_pos = text.rfind("dashboard_send_armed_keys")

    assert last_send_pos != -1, "expected at least one dashboard_send_armed_keys call site"
    assert last_send_pos < handshake_resolved_pos, (
        "expected the last pane dispatch to precede the all-armed handshake's "
        "success path in source order"
    )
    assert handshake_resolved_pos < install_pos, (
        "expected dashboard_install_activation_hook to be called strictly "
        "AFTER the all-armed handshake resolves -- installing any earlier "
        "reopens the race the handshake exists to close"
    )


def test_create_sh_sources_the_activation_hook_library() -> None:
    """Static guard: create.sh must source lib/activation_hook.sh (the
    library dashboard_install_activation_hook lives in) before it is
    called."""
    text = _CREATE_SH.read_text()
    source_pos = text.index('source "${SCRIPT_DIR}/lib/activation_hook.sh"')
    call_pos = text.index("dashboard_install_activation_hook ")
    assert source_pos < call_pos


# ---------------------------------------------------------------------------
# Group 2 -- Library-level lifecycle behavior (tmux-gated)
# ---------------------------------------------------------------------------


def _source_and_call(private_tmux: PrivateTmux, *statements: str) -> subprocess.CompletedProcess:
    script = f"source {shlex.quote(str(_ACTIVATION_HOOK_SH))}; " + " ".join(statements)
    return run_until_exit(["bash", "-c", script], env=private_tmux.env, cmdline_marker=private_tmux.socket_name)


def test_installed_hook_is_scoped_to_its_own_session_only(
    private_tmux: PrivateTmux,
) -> None:
    """
    dashboard_install_activation_hook registers a SESSION-scoped hook: a
    second, unrelated session on the same private server must show no
    trace of it. This is the constraint's own acceptance test -- "a
    second session on the same private server is unaffected by it."
    """
    session_a = f"hook-a-{uuid.uuid4().hex[:6]}"
    session_b = f"hook-b-{uuid.uuid4().hex[:6]}"

    assert private_tmux.run("new-session", "-d", "-s", session_a, "-x", "80", "-y", "24").returncode == 0
    assert private_tmux.run("new-session", "-d", "-s", session_b, "-x", "80", "-y", "24").returncode == 0

    # kanban_root and script_dir below are sample data, not filesystem
    # targets: dashboard_install_activation_hook embeds them, unread, into a
    # tmux set-hook command string that only fires on a window switch this
    # test never triggers -- neither path is ever created, opened, or read.
    install = _source_and_call(
        private_tmux,
        f"dashboard_install_activation_hook /tmp/kanban-root {shlex.quote(session_a)} /tmp/dashboard-scripts",
    )
    assert install.returncode == 0, install.stderr

    hooks_a = private_tmux.run("show-hooks", "-t", session_a)
    hooks_b = private_tmux.run("show-hooks", "-t", session_b)

    assert "session-window-changed" in hooks_a.stdout, (
        f"expected the hook installed on {session_a} to appear in its own "
        f"show-hooks output; got: {hooks_a.stdout!r}"
    )
    assert "session-window-changed" not in hooks_b.stdout, (
        f"expected session {session_b} to be completely unaffected by the "
        f"hook installed on {session_a}; got: {hooks_b.stdout!r}"
    )


def test_remove_activation_hook_unsets_it_and_is_idempotent(
    private_tmux: PrivateTmux,
) -> None:
    """
    dashboard_remove_activation_hook unsets exactly the hook
    dashboard_install_activation_hook set, and calling it a second time
    (or against a session that never had the hook) does not fail --
    kill.sh relies on both properties.
    """
    session = f"hook-rm-{uuid.uuid4().hex[:6]}"
    assert private_tmux.run("new-session", "-d", "-s", session, "-x", "80", "-y", "24").returncode == 0

    # Sample data, not filesystem targets -- see the identical comment in
    # test_installed_hook_is_scoped_to_its_own_session_only above.
    install = _source_and_call(
        private_tmux,
        f"dashboard_install_activation_hook /tmp/kanban-root {shlex.quote(session)} /tmp/dashboard-scripts",
    )
    assert install.returncode == 0, install.stderr
    assert "session-window-changed" in private_tmux.run("show-hooks", "-t", session).stdout

    remove_once = _source_and_call(private_tmux, f"dashboard_remove_activation_hook {shlex.quote(session)}")
    assert remove_once.returncode == 0, remove_once.stderr
    assert "session-window-changed" not in private_tmux.run("show-hooks", "-t", session).stdout

    # Idempotent: removing again (already-removed) does not fail.
    remove_twice = _source_and_call(private_tmux, f"dashboard_remove_activation_hook {shlex.quote(session)}")
    assert remove_twice.returncode == 0, remove_twice.stderr

    # Idempotent against a session that never had the hook at all.
    never_installed = f"hook-never-{uuid.uuid4().hex[:6]}"
    remove_missing = _source_and_call(
        private_tmux, f"dashboard_remove_activation_hook {shlex.quote(never_installed)}"
    )
    assert remove_missing.returncode == 0, remove_missing.stderr


def test_switching_windows_actually_fires_the_installed_hook(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """
    End-to-end proof that the installed hook is not merely present in
    show-hooks but genuinely fires on a real window switch -- the
    `run-shell` command touches a marker file each time.
    """
    session = f"hook-fire-{uuid.uuid4().hex[:6]}"
    marker = tmp_path / "fired"

    assert private_tmux.run("new-session", "-d", "-s", session, "-n", "main", "-x", "80", "-y", "24").returncode == 0
    # -d: create "second" WITHOUT switching to it, so "main" stays the
    # session's current window -- the select-window below is then a real
    # active-window change, not a same-window no-op that session-window-
    # changed would not fire for.
    assert private_tmux.run("new-window", "-d", "-t", session, "-n", "second").returncode == 0

    hook_cmd = f"run-shell -b \"touch {marker}\""
    set_hook = private_tmux.run("set-hook", "-t", session, "session-window-changed", hook_cmd)
    assert set_hook.returncode == 0, set_hook.stderr

    assert private_tmux.run("select-window", "-t", f"{session}:second").returncode == 0

    # The hook's run-shell -b runs in the background after select-window
    # returns: wait for its marker while the server is doing anything.
    _wait_for_file(
        private_tmux,
        marker,
        what="the private tmux server after a real window switch",
        on_stall="expected session-window-changed to fire the run-shell command on a real window switch",
    )
    assert marker.exists(), "expected session-window-changed to fire the run-shell command on a real window switch"


# ---------------------------------------------------------------------------
# Group 3 -- Sender behavior (tmux-gated)
# ---------------------------------------------------------------------------


def test_sender_skips_and_narrates_an_unarmed_pane_but_still_signals_the_armed_one(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """
    The sender's core contract: it NEVER dies on an unarmed pane, and it
    NEVER writes to its own stdout -- stdout is what tmux's run-shell
    wiring paints onto whichever pane is active, so any narration there
    would stamp permanently onto that pane's display. This
    uses window "main", whose real census pane layout is
    0=show-multi.sh (cadence-loop), 1=logs.sh (streaming -- census-excluded
    as a signal target entirely), 2=show-multi.sh (cadence-loop):
      - main.0 ("armed"): installs its OWN real USR2 handler (not the
        production ignore-trap) so receipt is directly observable, and
        must receive the signal.
      - main.1 ("streaming/excluded"): the real census names this pane's
        script logs.sh, so the sender must never arm-check, signal, or
        narrate it at all, regardless of its (absent) arm marker.
      - main.2 ("unarmed cadence-loop"): an idle shell with no trap of
        its own, so the test signal is fatal to it (terminate)
        -- if the sender ever signalled it, it would die; staying alive
        is the proof it was skipped, and the skip must be narrated to
        its own pane-scoped debug log, never to stdout.
    """
    session = f"sender-{uuid.uuid4().hex[:6]}"
    kanban_temp = tmp_path / "kanban-temp"
    kanban_temp.mkdir()
    marker_armed = tmp_path / "armed-pane-signalled"

    env = dict(private_tmux.env)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(kanban_temp)
    # A signal distinct from the production default so this test's custom
    # trap on the "armed" pane is unambiguous proof of delivery.
    env["PGAI_DASHBOARD_PANE_SIGNAL"] = "USR2"

    assert private_tmux.run("new-session", "-d", "-s", session, "-n", "main", "-x", "80", "-y", "24").returncode == 0
    assert private_tmux.run("split-window", "-t", f"{session}:main").returncode == 0
    assert private_tmux.run("split-window", "-t", f"{session}:main").returncode == 0

    # Every pane touches its own ready file once its shell is running the
    # command sent to it; the sender runs only after all three have. Each
    # pane then idles in its own shell's `read` builtin -- no child
    # process, nothing started on a schedule -- so an idle pane never
    # registers as activity on the server and the stall guard on every
    # wait below measures only the hook library and the sender.
    ready = {f"{session}:main.{index}": tmp_path / f"pane-{index}-ready" for index in range(3)}
    ready_main0, ready_main1, ready_main2 = ready.values()

    # Pane 0 ("armed"): installs its OWN real USR2 handler (not the
    # production ignore-trap) so receipt is directly observable. It reports
    # ready only AFTER the trap is installed -- before that, its shell's own
    # handling of USR2 is fatal, so a signal sent earlier would kill the
    # pane instead of proving delivery. A trapped signal interrupts the
    # idle `read`, so the trap runs as soon as the signal lands.
    assert private_tmux.run(
        "send-keys", "-t", f"{session}:main.0",
        f"trap 'touch {marker_armed}' USR2; touch {ready_main0}; while true; do read -r _ || exit; done",
        "Enter",
    ).returncode == 0
    # Pane 1 ("streaming/excluded", census script logs.sh) and pane 2
    # ("unarmed cadence-loop"): both idle shells with no trap of their own,
    # so USR2 is fatal to them -- if the sender ever signalled either, it
    # would die; staying alive proves neither was.
    assert private_tmux.run("send-keys", "-t", f"{session}:main.1", f"touch {ready_main1}; read -r _", "Enter").returncode == 0
    assert private_tmux.run("send-keys", "-t", f"{session}:main.2", f"touch {ready_main2}; read -r _", "Enter").returncode == 0
    _wait_for_panes_ready(private_tmux, ready)
    panes = _pane_processes(private_tmux, f"{session}:main")

    armed_dir = kanban_temp / "dashboard" / "armed" / session
    armed_dir.mkdir(parents=True)
    (armed_dir / "main.0").touch()  # main.1 and main.2 deliberately left unarmed

    result = _run_sender(private_tmux, env, "--session", session)
    assert result.returncode == 0, (
        f"the sender must never exit non-zero on an unarmed pane; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.stdout == "", (
        f"the sender must never write skip/signal narration to its own stdout "
        f"-- got: {result.stdout!r}"
    )

    # The sender has exited, so any signal it sent has already been
    # delivered; the pane's trap writes the marker once its shell runs it.
    _wait_for_file(
        private_tmux,
        marker_armed,
        what="the armed pane (main.0) after the sender exited",
        on_stall="expected the armed pane (main.0) to receive the signal",
    )
    assert marker_armed.exists(), "expected the armed pane (main.0) to receive the signal"

    _assert_pane_processes_alive(panes, "the sender run")
    dead_flags = private_tmux.run(
        "list-panes", "-t", f"{session}:main", "-F", "#{pane_dead}"
    ).stdout.split()
    assert dead_flags == ["0", "0", "0"], (
        f"expected all three panes to survive (main.1 and main.2 were never "
        f"armed, and neither was ever signalled); "
        f"pane_dead flags: {dead_flags}"
    )

    # main.2 (an unarmed CADENCE-LOOP pane, not census-excluded) must have
    # its skip narrated to its own pane-scoped debug log. The sender writes
    # it before exiting, so it is complete by now.
    debug_log_main2 = kanban_temp / "dashboard" / "logs" / "main.2.log"
    assert debug_log_main2.exists(), (
        f"expected a skip narration for the unarmed cadence-loop pane main.2 "
        f"at {debug_log_main2}"
    )
    narration = debug_log_main2.read_text()
    assert "main.2" in narration and "not armed" in narration, (
        f"expected the debug log to narrate the main.2 skip; got: {narration!r}"
    )

    # main.1 (the logs.sh stream pane) runs under cadence-loop.sh --stream
    # like every rendering pane, so it is arm-checked like main.2: unarmed,
    # it is skipped, never signalled, and the skip is narrated to its own
    # debug log.
    debug_log_main1 = kanban_temp / "dashboard" / "logs" / "main.1.log"
    assert debug_log_main1.exists(), (
        f"expected a skip narration for the unarmed stream pane main.1 at {debug_log_main1}"
    )
    assert "not armed" in debug_log_main1.read_text()


def test_window_switch_storm_against_an_unarmed_pane_produces_zero_display_bytes(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """
    However many times the operator switches to a window whose pane has
    not reported armed, the sender never paints a byte onto the display
    and never signals that pane. This drives 30 simulated window switches
    (30 sequential sender invocations against the SAME window, standing in
    for tmux's `run-shell -b` firing on every `session-window-changed`
    event) against a window named "logs" -- the logs.sh stream pane's
    window -- and asserts the sender's own stdout across every one of the
    30 invocations is zero bytes (the "display stream" tmux's run-shell
    wiring would otherwise paint onto whichever pane is active), that every
    skip narration lands in the pane's own debug log instead, and that the
    pane itself survives the entire storm (it was never signalled).
    """
    session = f"storm-{uuid.uuid4().hex[:6]}"
    kanban_temp = tmp_path / "kanban-temp"
    kanban_temp.mkdir()

    env = dict(private_tmux.env)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(kanban_temp)

    assert private_tmux.run(
        "new-session", "-d", "-s", session, "-n", "logs", "-x", "80", "-y", "24"
    ).returncode == 0
    # An idle shell with no trap of its own, so the pane signal is fatal to
    # it: if the sender ever signalled this pane (it must not -- it never
    # reported armed), it would die, which the survival checks below would
    # catch. The storm starts only once the pane's shell is idling in its
    # `read` builtin (its ready file exists).
    ready = tmp_path / "pane-ready"
    assert private_tmux.run(
        "send-keys", "-t", f"{session}:logs.0", f"touch {ready}; read -r _", "Enter"
    ).returncode == 0
    _wait_for_panes_ready(private_tmux, {f"{session}:logs.0": ready})
    panes = _pane_processes(private_tmux, f"{session}:logs")

    total_stdout_bytes = 0
    for _ in range(30):
        result = _run_sender(private_tmux, env, "--session", session, "--window", "logs")
        assert result.returncode == 0, (
            f"sender must never exit non-zero on a window of unarmed panes; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        total_stdout_bytes += len(result.stdout.encode("utf-8"))

    assert total_stdout_bytes == 0, (
        f"expected zero bytes on the sender's display stream across 30 "
        f"simulated window switches against an unarmed pane's window; got "
        f"{total_stdout_bytes} total bytes"
    )

    debug_log = kanban_temp / "dashboard" / "logs" / "logs.0.log"
    assert debug_log.exists(), f"expected the skips narrated to {debug_log}"
    assert debug_log.read_text().count("not armed") == 30

    _assert_pane_processes_alive(panes, "all 30 simulated switches")
    dead_flag = private_tmux.run(
        "list-panes", "-t", f"{session}:logs", "-F", "#{pane_dead}"
    ).stdout.strip()
    assert dead_flag == "0", (
        f"expected the unarmed pane to survive all 30 simulated switches "
        f"untouched (never signalled); pane_dead={dead_flag!r}"
    )


def test_sender_exits_cleanly_when_the_session_no_longer_exists(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """
    A hook can fire in the brief window between a session tearing down
    and the hook's own run-shell command executing. The sender must ride
    this out as a clean no-op, never a failure.
    """
    kanban_temp = tmp_path / "kanban-temp"
    kanban_temp.mkdir()
    env = dict(private_tmux.env)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(kanban_temp)

    result = _run_sender(private_tmux, env, "--session", f"never-existed-{uuid.uuid4().hex[:6]}")
    assert result.returncode == 0, (
        f"expected a clean no-op exit for a nonexistent session; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
