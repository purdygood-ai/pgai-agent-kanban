"""
test_dashboard_pane_signal_traps.py
====================================
Coverage for the pane-loop signal-safety preamble and the all-armed
readiness handshake (team/scripts/dashboard/lib/pane_readiness.sh,
team/scripts/dashboard/create.sh).

Background: every pane loop under team/scripts/dashboard/ is, from tmux's
point of view, one long-lived foreground process with no disposition for
the pane signal other than the OS default (terminate). Without a trap,
a signal delivered to that process during the window between pane start
and first render -- or at any later point, including a future activation
hook's refresh-on-activate signal -- kills the pane outright. This module
covers the guard: dashboard_pane_wrap_cmd installs an ignore trap as each
pane's literal first statement and reports "armed"; create.sh's return is
gated on every pane it dispatched a command to reporting armed, within a
bounded timeout that refuses (dual-channel) naming the still-unarmed panes.

Test groups
-----------
Group 1 -- Static coverage (no tmux required):
    Every pane's first command in create.sh is dispatched through
    dashboard_send_armed_keys (never a bare `tmux send-keys` for a pane's
    first command), and the wrapper's output has the trap as its literal
    first token.

Group 2 -- Library-level readiness-handshake behavior (tmux-gated, private
socket): exercises dashboard_wait_all_armed -- the EXACT function
create.sh's return is gated on -- directly, with synthetic panes on a
private tmux server. Covers the armed path, the partial-arm (delayed)
path, and the bounded-timeout path deterministically and quickly, without
depending on the timing of ~45 real per-pane scripts in the full create.sh
window set.

Group 3 -- End-to-end smoke (tmux-gated, private socket): runs the real
create.sh and kill.sh against this dev tree as KANBAN_ROOT, and asserts
the properties that only the full assembly can prove: create.sh returns
(the handshake actually completes for the real ~45-pane window set),
signalling a real pane immediately after create.sh returns leaves it
alive and still rendering, and kill.sh leaves no readiness marker behind
under the pgai temp root.

Docker is not used anywhere in this module -- every tmux operation runs on
a private socket on the test host directly (tmux -L <private>), per the
task's "Tests use a private tmux socket" constraint and the same
convention team/scripts/verify-visibility-pane-widths.sh already
establishes. Tmux-dependent groups narrate a skip (never a silent one)
when tmux is not on PATH.

Test naming (roles/TESTER.md anti-pattern 6): names describe behavior,
never bug IDs, scaffolding labels, or version strings.
"""

from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import time
import uuid
from typing import Iterator, NamedTuple

import pytest

from tests.fixtures.binary_guard import narrate_binary_skip, try_resolve_binary

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's location, matching every other
# dashboard test module (test_dashboard_operator_window.py,
# test_dashboard_smoke_container.py).
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_REPO_ROOT = _TEAM_DIR.parent
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_KILL_SH = _TEAM_DIR / "scripts" / "dashboard" / "kill.sh"
_PANE_READINESS_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "pane_readiness.sh"

_TMUX_SKIP_LABEL = "pane-signal-traps"


# ---------------------------------------------------------------------------
# Private tmux socket fixture
# ---------------------------------------------------------------------------
class PrivateTmux(NamedTuple):
    """A private, isolated tmux server plus the env every subprocess this
    test spawns (including create.sh/kill.sh, which invoke the bare `tmux`
    word with no socket flag of their own) must inherit to land on it."""

    real_tmux: str
    socket_name: str
    env: dict[str, str]

    def run(self, *args: str, timeout: float = 15) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.real_tmux, "-L", self.socket_name, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )


@pytest.fixture
def private_tmux(tmp_path: pathlib.Path) -> Iterator[PrivateTmux]:
    """
    Isolates every tmux invocation this test (and any subprocess it starts)
    makes onto a private-socket tmux server (tmux -L <private>) -- the same
    convention team/scripts/verify-visibility-pane-widths.sh already
    establishes -- so the test can never touch or disturb an operator's
    live dashboard session on the default socket.

    create.sh and kill.sh always invoke the bare `tmux` word (they take no
    --socket flag of their own), so isolation for those two scripts is
    achieved with a PATH-prepended wrapper that resolves `tmux` to
    `<real-tmux-absolute-path> -L <private>`. The wrapper calls the REAL
    tmux by its pre-resolved ABSOLUTE path, never the bare `tmux` word --
    a bare-word self-reference inside the wrapper would re-resolve through
    this same overridden PATH and recurse into itself forever. This is not
    a hypothetical: it was observed directly while authoring this fixture
    (the wrapped create.sh hung indefinitely inside its own `tmux -V`
    version probe until the wrapper was corrected to call the absolute
    real-tmux path instead of the bare word).
    """
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
        guard.teardown(
            lambda path: subprocess.run(
                [path, "-L", socket_name, "kill-server"],
                capture_output=True,
                timeout=10,
            )
        )


# ---------------------------------------------------------------------------
# Group 1 -- Static coverage (no tmux required)
# ---------------------------------------------------------------------------


def test_no_pane_first_command_bypasses_the_armed_wrapper() -> None:
    """
    Every pane's FIRST command in create.sh is dispatched through
    dashboard_send_armed_keys, never a bare `tmux send-keys`. This is the
    static, regression-proof form of the acceptance criterion "reading any
    pane loop shows the trap installation as its first executed
    statement" -- a future edit that reverts one send-keys call back to a
    bare, unwrapped form fails this test immediately rather than needing a
    live-session probe to notice.

    The only bare `tmux send-keys` lines allowed in the file are:
      1. Inside dashboard_send_armed_keys's own definition (the one place
         that actually talks to tmux for a pane's first command).
      2. The operator shell pane's two conveniences-follow-up commands
         (bashrc source, shell-env re-source) -- typed into the pane's
         shell after the trap has already been re-armed (see #3) by an
         in-process `trap` builtin call, not another exec, so these do
         not need to re-install anything themselves.
      3. The re-arm follow-ups for the two panes whose first command
         ends in a bare `exec bash` (the operator shell and the terminal
         window's interactive shell): a freshly-started bash does NOT
         keep the ignore trap the wrapped first command installed
         (empirically verified against tmux 3.2a / bash 5.x via
         /proc/<pid>/status SigIgn straddling the exec -- bash's own
         startup path re-establishes its signal set rather than trusting
         what it inherited, contrary to the plain POSIX fork/exec rule
         that holds for every other pane). Each of these two panes gets
         exactly one follow-up `trap '' ${PGAI_DASHBOARD_PANE_SIGNAL}; : >
         ...` send-keys call, typed into the now-resident shell
         immediately after its exec-chain first command, to
         re-establish the ignore disposition the exec silently dropped
         AND touch that pane's armed marker in the same typed line --
         the two dispatched through dashboard_send_armed_keys's own
         `--defer-marker` mode never touch the marker themselves, so a
         session-window-changed hook (or create.sh's own all-armed
         handshake) can never observe "armed" for either pane ahead of
         the specific, actually-resident process whose trap that marker
         is meant to prove.
    """
    text = _CREATE_SH.read_text()
    send_keys_lines = [
        line
        for line in text.splitlines()
        if "tmux send-keys" in line and not line.strip().startswith("#")
    ]

    bare_lines = [
        line
        for line in send_keys_lines
        if "${wrapped}" not in line and "$wrapped" not in line
    ]

    allowed_bare_substrings = (
        '\'[ -f "$HOME/.bashrc" ]',  # operator shell follow-up #1
        'source ${KANBAN_ROOT}/shell-env" Enter',  # operator shell follow-up #2
        # Re-arm follow-ups (see docstring point 3): one per bare-`exec
        # bash` pane, each re-installing the ignore trap that exec drops
        # and touching that pane's armed marker in the same typed line.
        "\"trap '' ${PGAI_DASHBOARD_PANE_SIGNAL}; : > ",
    )

    unexpected = [
        line
        for line in bare_lines
        if not any(sub in line for sub in allowed_bare_substrings)
    ]

    assert not unexpected, (
        "found bare `tmux send-keys` line(s) outside dashboard_send_armed_keys "
        "and the two documented operator-shell follow-ups -- every pane's "
        "first command must route through dashboard_send_armed_keys so its "
        "trap is installed as the pane's first executed statement:\n"
        + "\n".join(unexpected)
    )


def test_every_first_send_call_site_uses_the_armed_helper() -> None:
    """
    Complementary to the above: counts `dashboard_send_armed_keys` call
    sites and cross-checks against the known window/pane inventory this
    ticket wired up, so a future window addition that forgets to route
    through the helper is caught even if it never types a raw
    `tmux send-keys` line at all (e.g. a copy-pasted CMD var with no send
    site yet is not itself a violation, but an un-helpered send is).
    """
    text = _CREATE_SH.read_text()
    # Real call sites start the (stripped) line with `dashboard_send_armed_keys "`
    # -- this excludes the function's own doc-comment references (which
    # mention the name but never immediately follow it with a quoted arg)
    # while still counting the two-line continuation calls (debug-logs.0,
    # training-logs.0) whose first line matches this shape.
    call_count = sum(
        1
        for line in text.splitlines()
        if line.strip().startswith('dashboard_send_armed_keys "')
    )
    # main(4) + shared board dispatcher dashboard_dispatch_board_panes
    # (8 columns + legend = 9, called once for operator and once for
    # visibility) + operator shell(1) + attention(1) + schedule(2) + git(2)
    # + metadata(1) + rc-metrics(2) + agent-metrics(2) + logs(1) +
    # debug-logs(1) + training-logs(1) + terminal(2) + human-review(2) +
    # drill-N document(2) + drill-N release(5) = 38 fixed call sites (both
    # drill branches are present in the source even though only one
    # executes per project at runtime).
    assert call_count == 38, (
        f"expected exactly 38 dashboard_send_armed_keys call sites (one per "
        f"pane's first command across every dashboard window), "
        f"found {call_count} -- a pane's first-command wrap may have been "
        f"reverted to a bare tmux send-keys call, or a new pane was added "
        f"without routing its first command through the helper"
    )


def test_pane_wrap_installs_trap_as_the_literal_first_statement() -> None:
    """
    dashboard_pane_wrap_cmd's output has `trap '' <signal>` as its literal
    first statement, ahead of the armed-report touch and the inner
    command -- the shape every pane's first executed line depends on.
    """
    # "/tmp/armed" is sample data, not a filesystem target: this test only
    # inspects the STRING dashboard_pane_wrap_cmd builds (asserted below) --
    # the wrapped command is never sent to a pane or executed, so nothing is
    # ever created at this path.
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source {shlex.quote(str(_PANE_READINESS_SH))}; '
            f'dashboard_pane_wrap_cmd "/tmp/armed" "main.0" "echo inner"',
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    wrapped = result.stdout
    assert wrapped.startswith("trap '' USR1;"), (
        f"expected the wrapped command to start with the ignore-trap "
        f"installation; got: {wrapped!r}"
    )
    # The inner command and the armed-report touch both still appear,
    # strictly after the trap.
    trap_pos = wrapped.index("trap ''")
    touch_pos = wrapped.index(": > ")
    inner_pos = wrapped.index("echo inner")
    assert trap_pos < touch_pos < inner_pos


def test_no_pane_command_sources_env_bootstrap_directly() -> None:
    """
    Constraint: "If env_bootstrap sourcing must precede [the trap], the
    preamble goes inside a wrapper that traps before it sources -- state
    the ordering decision in a comment at the site." This design sidesteps
    the ordering hazard entirely: no pane's top-level command string built
    by create.sh sources env_bootstrap.sh inline. Every per-pane script
    sources it for itself, internally, strictly after the pane's own shell
    has already run the trap-then-arm preamble dashboard_send_armed_keys
    prepends. `env_bootstrap.sh` therefore appears in create.sh exactly
    twice: create.sh's own startup sourcing (one line, before any pane
    exists) and the design-note comment describing this very property --
    never inside a `*_CMD=` pane-command assignment.
    """
    text = _CREATE_SH.read_text()
    cmd_assignment_lines_with_env_bootstrap = [
        line
        for line in text.splitlines()
        if "env_bootstrap" in line
        and ("_CMD=" in line or "_INIT=" in line)
    ]
    assert not cmd_assignment_lines_with_env_bootstrap, (
        "a pane command string sources env_bootstrap.sh directly -- that "
        "would run ahead of (or race) the per-pane trap installation "
        "dashboard_send_armed_keys prepends:\n"
        + "\n".join(cmd_assignment_lines_with_env_bootstrap)
    )


# ---------------------------------------------------------------------------
# Group 2 -- Library-level readiness-handshake behavior (tmux-gated)
# ---------------------------------------------------------------------------


def _wrap_and_send(
    private_tmux: PrivateTmux,
    armed_dir: pathlib.Path,
    pane_target: str,
    inner_cmd: str,
    extra_env: dict[str, str] | None = None,
) -> None:
    """Build the wrapped command via the real shared library function and
    send it to <pane_target> as that pane's first command -- the exact
    mechanism create.sh's dashboard_send_armed_keys uses."""
    env_prefix = ""
    if extra_env:
        env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in extra_env.items()) + " "
    script = (
        f"{env_prefix}bash -c "
        f"'source {shlex.quote(str(_PANE_READINESS_SH))}; "
        f"dashboard_pane_wrap_cmd {shlex.quote(str(armed_dir))} "
        f"{shlex.quote(pane_target)} {shlex.quote(inner_cmd)}'"
    )
    wrapped = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=10
    )
    assert wrapped.returncode == 0, wrapped.stderr
    result = private_tmux.run("send-keys", "-t", pane_target, wrapped.stdout, "Enter")
    assert result.returncode == 0, result.stderr


def test_wait_all_armed_returns_immediately_when_every_pane_already_armed(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """The armed path: two panes that both arm right away, and
    dashboard_wait_all_armed returns success well inside the timeout."""
    session = f"armed-{uuid.uuid4().hex[:6]}"
    armed_dir = tmp_path / "armed"
    armed_dir.mkdir()

    assert private_tmux.run("new-session", "-d", "-s", session, "-x", "80", "-y", "24").returncode == 0
    assert private_tmux.run("split-window", "-t", session).returncode == 0

    _wrap_and_send(private_tmux, armed_dir, f"{session}.0", "watch -n 1 date")
    _wrap_and_send(private_tmux, armed_dir, f"{session}.1", "watch -n 1 date")

    start = time.monotonic()
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(_PANE_READINESS_SH))}; "
            f"dashboard_wait_all_armed {shlex.quote(str(armed_dir))} 5 "
            f"{session}.0 {session}.1",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 0, result.stderr
    assert elapsed < 3, f"expected a fast return on an already-armed set; took {elapsed:.2f}s"


def test_create_sh_return_is_gated_on_a_delayed_panes_arm_report(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """
    The partial-arm path: one pane's armed report is deliberately delayed
    via the test-only PGAI_DASHBOARD_PANE_ARM_DELAY_* hook in
    dashboard_pane_wrap_cmd. dashboard_wait_all_armed -- the exact
    function create.sh's return is gated on -- must still be blocked while
    the delay is in flight, and must return success once the delayed pane
    arms, not before and not (spuriously) after some unrelated fixed
    sleep. This is the same mechanism create.sh uses for its own return
    gate, exercised directly so timing is deterministic and fast.
    """
    session = f"delay-{uuid.uuid4().hex[:6]}"
    armed_dir = tmp_path / "armed"
    armed_dir.mkdir()
    delay_seconds = 2

    assert private_tmux.run("new-session", "-d", "-s", session, "-x", "80", "-y", "24").returncode == 0
    assert private_tmux.run("split-window", "-t", session).returncode == 0

    _wrap_and_send(private_tmux, armed_dir, f"{session}.0", "watch -n 1 date")
    _wrap_and_send(
        private_tmux,
        armed_dir,
        f"{session}.1",
        "watch -n 1 date",
        extra_env={
            "PGAI_DASHBOARD_PANE_ARM_DELAY_LABEL": f"{session}.1",
            "PGAI_DASHBOARD_PANE_ARM_DELAY_SECONDS": str(delay_seconds),
        },
    )

    start = time.monotonic()
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(_PANE_READINESS_SH))}; "
            f"dashboard_wait_all_armed {shlex.quote(str(armed_dir))} 10 "
            f"{session}.0 {session}.1",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 0, result.stderr
    assert elapsed >= delay_seconds, (
        f"dashboard_wait_all_armed returned after {elapsed:.2f}s, before the "
        f"deliberately delayed pane's {delay_seconds}s arm report -- the "
        f"return is not actually gated on the delayed pane"
    )
    assert elapsed < delay_seconds + 3, (
        f"dashboard_wait_all_armed took {elapsed:.2f}s to notice the delayed "
        f"pane armed -- it should return promptly (poll granularity 0.1s), "
        f"not stall behind a coarser fixed wait"
    )


def test_wait_all_armed_times_out_and_names_the_unarmed_pane(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """
    The timeout path: one pane forced never to arm (its inner command
    sleeps far longer than the bound and never reaches the armed touch --
    simulated here by never sending it any wrapped command at all, the
    simplest form of "this pane's trap never got installed").
    dashboard_wait_all_armed must refuse within the bound and name exactly
    the still-unarmed pane, matching the acceptance criterion that
    create.sh's own timeout refusal names which panes failed to arm.
    """
    session = f"timeout-{uuid.uuid4().hex[:6]}"
    armed_dir = tmp_path / "armed"
    armed_dir.mkdir()
    timeout_bound = 2

    assert private_tmux.run("new-session", "-d", "-s", session, "-x", "80", "-y", "24").returncode == 0
    assert private_tmux.run("split-window", "-t", session).returncode == 0

    # Pane 0 arms normally; pane 1 never receives a wrapped command at all,
    # so it can never report armed -- the "trap never installed" case.
    _wrap_and_send(private_tmux, armed_dir, f"{session}.0", "watch -n 1 date")

    # The bound is a stall bound (pane 0 arming mid-wait would restart it),
    # so wait for pane 0's own marker first: the measured wait then holds
    # no progress at all, and the refusal lands one bound after it starts.
    pane0_marker = armed_dir / f"{session}.0"
    give_up = time.monotonic() + 30
    while not pane0_marker.exists():
        assert time.monotonic() < give_up, (
            f"pane {session}.0 never reported armed after its wrapped command "
            f"was sent; expected marker {pane0_marker}"
        )
        time.sleep(0.05)

    start = time.monotonic()
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(_PANE_READINESS_SH))}; "
            f"dashboard_wait_all_armed {shlex.quote(str(armed_dir))} {timeout_bound} "
            f"{session}.0 {session}.1",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 1, (
        f"expected dashboard_wait_all_armed to refuse (exit 1) once the "
        f"bound elapsed with pane {session}.1 still unarmed; got rc="
        f"{result.returncode}"
    )
    assert result.stdout.strip() == f"{session}.1", (
        f"expected the timeout output to name exactly the unarmed pane "
        f"({session}.1); got: {result.stdout!r}"
    )
    assert timeout_bound <= elapsed < timeout_bound + 2, (
        f"expected the timeout to fire within ~{timeout_bound}s of the bound; "
        f"took {elapsed:.2f}s"
    )


def test_wait_all_armed_keeps_waiting_while_panes_keep_arming_past_the_bound(
    tmp_path: pathlib.Path,
) -> None:
    """
    The slow-but-progressing path: panes that arm steadily, one at a time,
    for longer in total than the bound must not be refused. The bound
    measures how long arming has stalled, not how long it has taken, so a
    loaded host that arms every pane eventually gets a dashboard rather
    than a refusal naming panes that were only slow.

    No tmux needed: dashboard_wait_all_armed reads marker files only, so
    the test writes them itself on a fixed schedule -- one every
    `step_seconds`, each gap well inside the bound, the whole run well past
    it -- and checks the wait is still running when the bound has already
    passed and returns success once the last marker lands.
    """
    armed_dir = tmp_path / "armed"
    armed_dir.mkdir()
    labels = [f"steady.{i}" for i in range(5)]
    stall_bound = 3
    step_seconds = 1.0

    proc = subprocess.Popen(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(_PANE_READINESS_SH))}; "
            f"dashboard_wait_all_armed {shlex.quote(str(armed_dir))} {stall_bound} "
            + " ".join(shlex.quote(label) for label in labels),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        start = time.monotonic()
        for label in labels:
            time.sleep(step_seconds)
            if label == labels[-1]:
                # By now more than the bound has passed since the wait began;
                # a total-elapsed bound would already have refused.
                assert time.monotonic() - start > stall_bound
                assert proc.poll() is None, (
                    "dashboard_wait_all_armed returned while panes were still "
                    f"arming one every {step_seconds}s (stall bound {stall_bound}s); "
                    f"rc={proc.returncode}, stdout={proc.stdout.read()!r}"
                )
            (armed_dir / label).touch()
        stdout, stderr = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert proc.returncode == 0, (
        f"expected success once every pane armed, each within the "
        f"{stall_bound}s stall bound of the last; rc={proc.returncode}, "
        f"pending reported: {stdout!r}, stderr: {stderr!r}"
    )


# ---------------------------------------------------------------------------
# Group 3 -- End-to-end smoke (tmux-gated, private socket, real create.sh)
# ---------------------------------------------------------------------------


def test_create_sh_signals_a_real_pane_alive_immediately_after_return(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """
    End-to-end: run the real create.sh against this dev tree as
    KANBAN_ROOT, on the private socket. Once it returns, every pane it
    dispatched must have reported armed (the marker directory is fully
    populated), and signalling a real pane's pid with the pane signal
    immediately afterward must leave it alive and still rendering --
    the survival guarantee this module covers. Then kill.sh must
    remove every readiness marker under the pgai temp root.
    """
    if not _CREATE_SH.exists() or not _KILL_SH.exists():
        pytest.fail(f"create.sh/kill.sh not found under {_TEAM_DIR / 'scripts' / 'dashboard'}")

    session = f"e2e-{uuid.uuid4().hex[:6]}"
    kanban_temp = tmp_path / "kanban-temp"
    kanban_temp.mkdir()

    env = dict(private_tmux.env)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(_REPO_ROOT)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(kanban_temp)
    env["TERM"] = "xterm-256color"

    create_result = subprocess.run(
        [
            "bash",
            str(_CREATE_SH),
            "--kanban-root",
            str(_REPO_ROOT),
            "--session",
            session,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        stdin=subprocess.DEVNULL,
    )

    # A no-tty "open terminal failed" on the final exec-attach line is
    # expected and harmless in this headless test environment -- the
    # session was already created detached before that final line runs;
    # what matters is that create.sh's own all-armed handshake did not
    # refuse (which would print the dual-channel "pane_arm_timeout"
    # refusal instead, and this narrated fail below catches that).
    assert "dashboard_create.pane_arm_timeout" not in create_result.stdout, (
        "create.sh refused the all-armed handshake against the real window "
        f"set:\nSTDOUT:\n{create_result.stdout}\nSTDERR:\n{create_result.stderr}"
    )
    assert "dashboard_create.pane_arm_timeout" not in create_result.stderr, (
        f"create.sh refused the all-armed handshake against the real window "
        f"set:\nSTDOUT:\n{create_result.stdout}\nSTDERR:\n{create_result.stderr}"
    )

    try:
        has_session = private_tmux.run("has-session", "-t", session)
        assert has_session.returncode == 0, (
            f"expected the dashboard session to exist after create.sh ran; "
            f"create.sh STDOUT:\n{create_result.stdout}\nSTDERR:\n{create_result.stderr}"
        )

        # Every armed marker this run should have produced is present --
        # the handshake genuinely completed rather than returning early.
        armed_root = kanban_temp / "dashboard" / "armed" / session
        assert armed_root.is_dir(), f"expected armed marker directory at {armed_root}"
        markers = sorted(p.name for p in armed_root.iterdir())
        assert "main.0" in markers and "main.1" in markers, (
            f"expected core window-0 panes among the armed markers; got: {markers}"
        )

        # Signal a real pane immediately after create.sh returned and
        # confirm it survives -- the acceptance criterion verbatim.
        pid_result = private_tmux.run(
            "display-message", "-p", "-t", f"{session}:main.0", "#{pane_pid}"
        )
        assert pid_result.returncode == 0, pid_result.stderr
        pane_pid = int(pid_result.stdout.strip())

        os.kill(pane_pid, 10)  # SIGUSR1 -- must match PGAI_DASHBOARD_PANE_SIGNAL's default
        time.sleep(0.5)

        panes = private_tmux.run(
            "list-panes", "-t", f"{session}:main", "-F", "#{pane_dead}"
        )
        assert panes.returncode == 0, panes.stderr
        dead_flags = panes.stdout.split()
        assert dead_flags and all(flag == "0" for flag in dead_flags), (
            f"expected every pane in window main to survive the signal "
            f"(pane_dead=0); got: {panes.stdout!r}"
        )
    finally:
        kill_result = subprocess.run(
            [
                "bash",
                str(_KILL_SH),
                "--kanban-root",
                str(_REPO_ROOT),
                "--session",
                session,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        assert kill_result.returncode == 0, (
            f"kill.sh failed: {kill_result.stdout}\n{kill_result.stderr}"
        )

    # No readiness marker survives kill.sh -- the acceptance criterion
    # verbatim. kill.sh wipes the whole "dashboard" temp subtree wholesale
    # (pgai_temp_cleanup on pgai_temp_subdir "dashboard"), so the session's
    # armed directory must be gone entirely, not merely emptied.
    armed_root = kanban_temp / "dashboard" / "armed" / session
    assert not armed_root.exists(), (
        f"expected kill.sh to remove the readiness marker directory; "
        f"{armed_root} still exists"
    )
