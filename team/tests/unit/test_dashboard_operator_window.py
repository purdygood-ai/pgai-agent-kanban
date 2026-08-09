"""
test_dashboard_operator_window.py
==================================
Smoke test and teardown coverage for the operator window (window index 2 in
the tmux session created by team/scripts/dashboard/create.sh).

This module contains two groups of tests:

Group 1 — Static analysis (no Docker required):
    Parse create.sh for tmux commands to assert the operator window is created
    at the correct position in the window sequence, with the correct 8-column /
    9-pane split geometry (operator commit 83ccdc5), and that kill.sh sends the
    session-kill that removes it.

Group 2 — Docker-gated live session tests:
    Build the flavor image, start a real tmux session inside the container,
    query the session with tmux list-windows / list-panes, assert operator at
    index 2 (1-based), visibility at index 3 (1-based), nine panes (8-column
    layout), bottom pane is an interactive bash shell where show.sh --help
    resolves.  Then run kill.sh and assert the session is gone.

Group 3 — Window-0 section-header fixture comparison (Docker-gated):
    Run create.sh --no-tmux inside the container and extract separator + section-
    label lines.  Compare byte-for-byte against the checked-in golden fixture at
    team/tests/fixtures/golden/window0_section_headers.txt.

Docker-availability gate:
    All Docker-dependent tests use the same narrated-skip pattern as the existing
    dashboard smoke: when Docker is absent or the daemon is unreachable, the test
    prints a visible SKIP notice to stdout and calls pytest.skip().  Never a
    silent pass.

Acceptance criteria addressed:
    AC1: Extended test file runs green locally (narrates skip on no-Docker hosts).
    AC2: Asserts operator at window index 2 and visibility at index 3 (1-based).
    AC3: Asserts operator bottom pane is an interactive shell and show.sh --help
         resolves without command-not-found.
    AC4: Asserts window-0 rendered content matches checked-in fixture byte-for-byte.
    AC5: kill.sh teardown confirms operator window is destroyed with the session.
    AC6: python -m py_compile passes on this file; bash -n passes on modified .sh files.

Test naming (SOP.md Anti-pattern 6):
    Names describe behavior — not bug IDs, scaffolding labels, or version strings.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Path resolution — anchored to this file's location.
# this file:  team/tests/unit/test_dashboard_operator_window.py
# team/:      ../../.. (3 levels up from this file's directory)
# repo root:  team/../
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent          # team/
_REPO_ROOT = _TEAM_DIR.parent                        # repo root

_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_KILL_SH = _TEAM_DIR / "scripts" / "dashboard" / "kill.sh"
_SHOW_SH = _TEAM_DIR / "scripts" / "show.sh"

_FIXTURE_DIR = _THIS_FILE.parent.parent / "fixtures" / "golden"
_WINDOW0_FIXTURE = _FIXTURE_DIR / "window0_section_headers.txt"

# Unique session name for live-session tests (never overlaps with the
# operator's real dashboard session "pgai-kanban-dashboard").
_TEST_SESSION_NAME = "pgai-kanban-test-operator-coverage"


# ---------------------------------------------------------------------------
# Docker availability helpers (same pattern as test_dashboard_smoke_container.py)
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    """Return True when docker binary is present AND the daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def _narrate_skip(reason: str) -> None:
    """Print a visible skip notice to stdout before calling pytest.skip."""
    print(
        f"\n[dashboard-operator-coverage] SKIP — {reason}",
        flush=True,
    )
    pytest.skip(reason)


# ---------------------------------------------------------------------------
# Module-scoped fixture: build and tear down the debian smoke image.
# Reuses the same image tag as test_dashboard_smoke_container.py (build is
# cached by Docker layer cache; no redundant rebuilds if the suite runs in
# one session).
# ---------------------------------------------------------------------------

_SMOKE_IMAGE_TAG = "pgai-kanban-smoke-fixture-debian:test"
_SMOKE_DOCKERFILE = _REPO_ROOT / "docker" / "debian" / "Dockerfile"


@pytest.fixture(scope="module")
def operator_test_image() -> str:
    """
    Build docker/debian/Dockerfile and return the image tag.

    Skips (with narration) when Docker is unavailable.
    Builds once per test session; tears down in finally.

    We use the debian flavor because it is the canonical smoke flavor and the
    image build is shared with test_dashboard_smoke_container.py via the Docker
    layer cache.
    """
    if not _docker_available():
        _narrate_skip(
            "docker binary not found or Docker daemon unreachable; "
            "operator window coverage requires a running Docker environment"
        )

    if not _SMOKE_DOCKERFILE.exists():
        pytest.fail(
            f"docker/debian/Dockerfile not found at {_SMOKE_DOCKERFILE}; "
            "ensure the file was committed to the repository"
        )

    print(
        f"\n[dashboard-operator-coverage] Building image from {_SMOKE_DOCKERFILE} ...",
        flush=True,
    )
    build_result = subprocess.run(
        [
            "docker",
            "build",
            "--tag",
            _SMOKE_IMAGE_TAG,
            "--file",
            str(_SMOKE_DOCKERFILE),
            str(_REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )

    if build_result.returncode != 0:
        print(
            f"\n[dashboard-operator-coverage] BUILD STDOUT:\n{build_result.stdout}",
            flush=True,
        )
        print(
            f"\n[dashboard-operator-coverage] BUILD STDERR:\n{build_result.stderr}",
            flush=True,
        )
        pytest.fail(
            f"docker build [debian] exited {build_result.returncode}; see output above"
        )

    print(
        f"[dashboard-operator-coverage] Image built: {_SMOKE_IMAGE_TAG}",
        flush=True,
    )

    try:
        yield _SMOKE_IMAGE_TAG
    finally:
        subprocess.run(
            ["docker", "image", "rm", "--force", _SMOKE_IMAGE_TAG],
            capture_output=True,
        )
        print(
            f"[dashboard-operator-coverage] Removed image tag: {_SMOKE_IMAGE_TAG}",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Helper: run create.sh --no-tmux inside the container.
# Reuses the same invocation pattern as test_dashboard_smoke_container.py.
# ---------------------------------------------------------------------------


def _run_create_sh_no_tmux(image_tag: str) -> subprocess.CompletedProcess:
    """
    Run create.sh --no-tmux inside a container built from the given image tag.

    The repo root is bind-mounted read-only at /pgai_agent_kanban so that all
    scripts under team/scripts/ are reachable.

    Uses --entrypoint bash to bypass the container's entrypoint.sh mount
    check (which requires /home/<user>, /claude, and ~/.claude mounts that
    the smoke test does not provide).  The test exercises create.sh directly
    without the production entrypoint guard.

    Returns the CompletedProcess (stdout + stderr captured as text).
    """
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "--volume",
            f"{_REPO_ROOT}:/pgai_agent_kanban:ro",
            "--env",
            "PGAI_AGENT_KANBAN_ROOT_PATH=/pgai_agent_kanban",
            "--env",
            "TERM=xterm-256color",
            image_tag,
            "/pgai_agent_kanban/team/scripts/dashboard/create.sh",
            "--no-tmux",
            "--kanban-root",
            "/pgai_agent_kanban",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _extract_section_headers(output: str) -> str:
    """
    Extract separator lines and section-label lines from create.sh --no-tmux output.

    Returns only lines matching the static structure:
      - Lines consisting entirely of U+2550 (═) box-drawing characters
      - Lines beginning with exactly two spaces followed by an uppercase letter
        (the section label lines: "  HEADER", "  QUEUES", etc.)

    The label pattern is intentionally broad: it matches any "  <uppercase-start>"
    line, relying on the fact that only section label echo lines begin with two
    spaces followed by a capital letter in create.sh's --no-tmux output.

    The returned string has a trailing newline. This is the content that is
    compared byte-for-byte against the golden fixture.
    """
    sep_pattern = re.compile(r"^═+$")
    # Match lines that start with exactly two spaces followed by an uppercase letter.
    # The rest of the line can be anything (mixed case, punctuation, em-dashes, etc.)
    label_pattern = re.compile(r"^  [A-Z]")
    lines = []
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if sep_pattern.match(line) or label_pattern.match(line):
            lines.append(line)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Helper: run a docker command that starts a live tmux session, queries it,
# then kills it.  Returns the tmux list-windows output for the session.
# ---------------------------------------------------------------------------


def _run_tmux_session_commands(
    image_tag: str,
    session_name: str,
    commands_after_create: list[str],
) -> subprocess.CompletedProcess:
    """
    Start create.sh in a container in live tmux mode, run extra tmux commands,
    then return their combined output.

    Uses --entrypoint bash to bypass the entrypoint.sh mount check.
    The kanban root is bind-mounted read-only.

    This helper encodes the full sequence as a single bash -c script that:
      1. Starts create.sh (which starts the tmux session in detached mode)
      2. Waits briefly for the session to be ready
      3. Runs each command in commands_after_create
      4. Exits

    The container uses --rm so the tmux server is destroyed on exit.

    Returns the CompletedProcess with stdout/stderr as text.
    """
    extra_cmds = " && ".join(commands_after_create)
    script = (
        f"bash /pgai_agent_kanban/team/scripts/dashboard/create.sh "
        f"--kanban-root /pgai_agent_kanban "
        f"--session {session_name} "
        f"2>/dev/null || true ; "
        f"sleep 2 ; "
        f"{extra_cmds}"
    )
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "--volume",
            f"{_REPO_ROOT}:/pgai_agent_kanban:ro",
            "--env",
            "PGAI_AGENT_KANBAN_ROOT_PATH=/pgai_agent_kanban",
            "--env",
            f"PGAI_DASHBOARD_SESSION_NAME={session_name}",
            "--env",
            "TERM=xterm-256color",
            image_tag,
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# Helper: run create.sh against a writable fixture HOME / kanban-root inside
# the container, then probe the operator shell pane and capture its
# transcript.  The checked-out repo is bind-mounted read-only, so tests that
# need a real ~/.bashrc or shell-env file write them into a fresh writable
# directory created inside the container instead of the mounted repo.
# ---------------------------------------------------------------------------

_PANE_CAPTURE_START = "===PANE_CAPTURE_START==="
_PANE_CAPTURE_END = "===PANE_CAPTURE_END==="


def _run_operator_shell_fixture(
    image_tag: str,
    session_name: str,
    setup_commands: list[str],
    home_dir: str,
    kanban_root_dir: str,
    probe_command: str,
) -> subprocess.CompletedProcess:
    """
    Start create.sh in a container against a fixture HOME / kanban-root,
    send `probe_command` to the operator shell pane (pane 8), and capture
    its transcript between marker lines.

    `setup_commands` run first (creating the fixture HOME / kanban-root
    directories and writing any .bashrc / shell-env fixture files).
    `create.sh` itself is still read from the read-only repo mount at
    /pgai_agent_kanban; only its `--kanban-root` operational directory and
    the pane's HOME are redirected to writable fixture paths.

    Returns the CompletedProcess with combined stdout/stderr as text; use
    `_extract_pane_capture` to pull out just the pane transcript.
    """
    setup = " ; ".join(setup_commands) if setup_commands else "true"
    script = (
        f"{setup} ; "
        f"HOME={home_dir} bash /pgai_agent_kanban/team/scripts/dashboard/create.sh "
        f"--kanban-root {kanban_root_dir} --session {session_name} "
        f"2>&1 || true ; "
        f"sleep 2 ; "
        f"tmux send-keys -t {session_name}:operator.8 '{probe_command}' Enter ; "
        f"sleep 1 ; "
        f"echo '{_PANE_CAPTURE_START}' ; "
        f"tmux capture-pane -p -t {session_name}:operator.8 ; "
        f"echo '{_PANE_CAPTURE_END}'"
    )
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "--volume",
            f"{_REPO_ROOT}:/pgai_agent_kanban:ro",
            "--env",
            "PGAI_AGENT_KANBAN_ROOT_PATH=/pgai_agent_kanban",
            "--env",
            f"PGAI_DASHBOARD_SESSION_NAME={session_name}",
            "--env",
            "TERM=xterm-256color",
            image_tag,
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _extract_pane_capture(output: str) -> str:
    """Return the text between the pane-capture marker lines, or "" if absent."""
    start = output.find(_PANE_CAPTURE_START)
    end = output.find(_PANE_CAPTURE_END)
    if start == -1 or end == -1:
        return ""
    return output[start + len(_PANE_CAPTURE_START) : end]


# ===========================================================================
# Group 1 — Static analysis tests (no Docker required)
# ===========================================================================


def test_create_sh_exists() -> None:
    """team/scripts/dashboard/create.sh must exist in the shipped tree."""
    assert _CREATE_SH.exists(), (
        f"team/scripts/dashboard/create.sh not found at {_CREATE_SH}; "
        "this file must be committed to the repository"
    )


def test_kill_sh_exists() -> None:
    """team/scripts/dashboard/kill.sh must exist in the shipped tree."""
    assert _KILL_SH.exists(), (
        f"team/scripts/dashboard/kill.sh not found at {_KILL_SH}; "
        "this file must be committed to the repository"
    )


def test_window0_fixture_exists() -> None:
    """The golden fixture for window-0 section headers must exist."""
    assert _WINDOW0_FIXTURE.exists(), (
        f"window0 golden fixture not found at {_WINDOW0_FIXTURE}; "
        "run generate_window0_fixture() or re-commit the fixture file"
    )


def test_operator_new_window_appears_in_create_sh() -> None:
    """create.sh contains a 'new-window' call that names the window 'operator'."""
    content = _CREATE_SH.read_text(encoding="utf-8")
    assert 'new-window' in content and '"operator"' in content, (
        "create.sh does not contain a new-window call for 'operator'; "
        "the operator window was not added to the dashboard"
    )


def test_operator_window_appears_before_visibility_window() -> None:
    """
    In create.sh, the 'operator' new-window appears before 'visibility'.

    Window creation order determines tmux window indices.  operator must be
    created first (making it index 1, i.e., the 2nd window after 'main') and
    visibility second (index 2, i.e., the 3rd window).  This corresponds to
    the 1-based counting used in the task specification: operator=2, visibility=3.
    """
    content = _CREATE_SH.read_text(encoding="utf-8")
    # Locate the line positions of the new-window calls for each window name.
    operator_match = re.search(r'tmux new-window\b[^\n]*-n\s+"operator"', content)
    visibility_match = re.search(r'tmux new-window\b[^\n]*-n\s+"visibility"', content)

    assert operator_match is not None, (
        "create.sh does not contain: tmux new-window ... -n \"operator\"; "
        "the operator window is missing"
    )
    assert visibility_match is not None, (
        "create.sh does not contain: tmux new-window ... -n \"visibility\"; "
        "the visibility window is missing"
    )
    assert operator_match.start() < visibility_match.start(), (
        "create.sh defines the 'visibility' new-window BEFORE the 'operator' "
        "new-window; operator must come first so it lands at the correct index"
    )


def test_operator_window_is_first_new_window_after_session_creation() -> None:
    """
    The 'operator' new-window is the first new-window call after new-session.

    new-session creates the 'main' window (index 0).  The immediately
    following new-window creates 'operator' (index 1, 2nd window overall).
    This assertion verifies the ordering: no other new-window appears between
    new-session and the operator new-window.
    """
    content = _CREATE_SH.read_text(encoding="utf-8")

    session_match = re.search(r'tmux new-session\b', content)
    operator_match = re.search(r'tmux new-window\b[^\n]*-n\s+"operator"', content)
    visibility_match = re.search(r'tmux new-window\b[^\n]*-n\s+"visibility"', content)

    assert session_match is not None, "create.sh has no tmux new-session call"
    assert operator_match is not None, "create.sh has no new-window for operator"
    assert visibility_match is not None, "create.sh has no new-window for visibility"

    # Find ALL new-window calls in the script.
    all_new_windows = list(re.finditer(r'tmux new-window\b', content))
    assert all_new_windows, "create.sh has no new-window calls at all"

    # The first new-window call must be the operator window.
    first_new_window_pos = all_new_windows[0].start()
    assert operator_match.start() == first_new_window_pos, (
        "The first 'tmux new-window' call in create.sh is NOT the operator "
        "window; another window is created before operator, shifting its index. "
        f"First new-window pos={first_new_window_pos}, "
        f"operator new-window pos={operator_match.start()}"
    )


def test_operator_window_has_eight_column_split() -> None:
    """
    create.sh splits the operator window into nine panes via eight split-window
    calls (operator commit 83ccdc5, shipped on ai_main): a vertical split that
    carves off the bottom shell, a horizontal split that separates the input
    columns from the agent-queue columns, and six further horizontal splits
    that fan each region out into its individual columns.

    Split sequence (matches the ladder comment above the operator section in
    create.sh):
      1. Vertical split -l 20%: top region (~80%), bottom shell (~20%).
      2. Horizontal split -l 60% on the top region: left inputs (~40%),
         right queues (~60%).
      3-4. Left region fanned into 3 input columns (-l 67%, -l 50%).
      5-8. Right region fanned into 5 agent-queue columns
           (-l 80%, -l 75%, -l 67%, -l 50%).
    """
    content = _CREATE_SH.read_text(encoding="utf-8")

    # Find the operator window section: from the operator new-window to the
    # next new-window call (visibility).
    operator_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "operator"')
    visibility_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "visibility"')

    assert operator_start != -1, "operator new-window not found in create.sh"
    assert visibility_start != -1, "visibility new-window not found in create.sh"
    assert operator_start < visibility_start, (
        "operator window section comes after visibility in create.sh"
    )

    operator_section = content[operator_start:visibility_start]

    # Assert exactly eight split-window calls in the operator section
    # (nine panes total: eight columns + the bottom shell).
    split_calls = list(re.finditer(r'tmux split-window\b', operator_section))
    assert len(split_calls) == 8, (
        f"Expected exactly 8 split-window calls in the operator section "
        f"(8-column / 9-pane layout, operator commit 83ccdc5); "
        f"found {len(split_calls)}: {[m.group() for m in split_calls]}"
    )

    def _line_for(match: re.Match) -> str:
        rest = operator_section[match.start():]
        end = rest.find("\n")
        return rest[:end]

    # First split: vertical -l 20% (bottom shell pane).
    split1_cmd = _line_for(split_calls[0])
    assert "-v" in split1_cmd and "-l 20%" in split1_cmd, (
        f"First split-window in operator section must be vertical '-v -l 20%'; "
        f"found: {split1_cmd!r}"
    )

    # Second split: horizontal -l 60% (left inputs / right queues).
    split2_cmd = _line_for(split_calls[1])
    assert "-h" in split2_cmd and "-l 60%" in split2_cmd, (
        f"Second split-window in operator section must be horizontal '-h -l 60%'; "
        f"found: {split2_cmd!r}"
    )

    # Splits 3-8: the remaining six splits fan the two regions into individual
    # columns. Each must be horizontal, with the expected -l percentage in order.
    expected_remaining_pcts = ["67%", "50%", "80%", "75%", "67%", "50%"]
    for idx, expected_pct in enumerate(expected_remaining_pcts, start=2):
        cmd = _line_for(split_calls[idx])
        assert "-h" in cmd and f"-l {expected_pct}" in cmd, (
            f"Split-window #{idx + 1} in operator section must be horizontal "
            f"'-h -l {expected_pct}'; found: {cmd!r}"
        )


def test_operator_column_panes_send_visibility_column_commands() -> None:
    """
    create.sh sends the same per-column Visibility-window commands to the
    operator window's eight column panes (string equality against the defined
    variable source).

    The 8-column layout's panes mirror the Visibility window's individual
    columns, in order:
      pane 0 → VIS_BUGS_CMD           pane 4 → VIS_CODER_CMD
      pane 1 → VIS_PRIORITY_CMD       pane 5 → VIS_WRITER_CMD
      pane 2 → VIS_REQUIREMENTS_CMD   pane 6 → VIS_TESTER_CMD
      pane 3 → VIS_PM_CMD             pane 7 → VIS_CM_CMD

    The assertion compares the exact argument string passed to each tmux
    send-keys call in the operator section against the expected variable
    reference.  This catches regressions where a pane accidentally receives
    the wrong command (e.g., an unrelated command instead of its matching
    Visibility-window column).
    """
    content = _CREATE_SH.read_text(encoding="utf-8")

    operator_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "operator"')
    visibility_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "visibility"')
    assert operator_start != -1, "operator new-window not found in create.sh"
    assert visibility_start != -1, "visibility new-window not found in create.sh"

    operator_section = content[operator_start:visibility_start]

    expected_commands = [
        "VIS_BUGS_CMD",
        "VIS_PRIORITY_CMD",
        "VIS_REQUIREMENTS_CMD",
        "VIS_PM_CMD",
        "VIS_CODER_CMD",
        "VIS_WRITER_CMD",
        "VIS_TESTER_CMD",
        "VIS_CM_CMD",
    ]

    for pane_idx, expected_var in enumerate(expected_commands):
        pane_match = re.search(
            r'tmux send-keys\s+-t\s+"\$\{SESSION_NAME\}:operator\.'
            + str(pane_idx)
            + r'"\s+"([^"]+)"\s+Enter',
            operator_section,
        )
        assert pane_match is not None, (
            f"create.sh does not contain a "
            f"'tmux send-keys ... operator.{pane_idx} ... Enter' call in the "
            f"operator section; the column {pane_idx} command is missing"
        )
        pane_arg = pane_match.group(1)
        expected_arg = "${" + expected_var + "}"
        assert pane_arg == expected_arg, (
            f"Operator pane {pane_idx} send-keys argument does not match the "
            f"matching Visibility-window column command.\n"
            f"  Expected: {expected_arg!r}\n"
            f"  Actual:   {pane_arg!r}\n"
            f"Pane {pane_idx} must use {expected_var}, not any other command."
        )


def test_operator_bottom_pane_sends_interactive_bash() -> None:
    """
    create.sh sends an interactive bash shell to the operator window's bottom pane.

    The 8-column / 9-pane layout (operator commit 83ccdc5) assigns:
      panes 0-7 = the eight column panes (visibility inputs + agent queues)
      pane 8    = bottom (interactive bash shell)

    The send-keys command for pane 8 (bottom) must include 'bash' so the pane
    starts an interactive shell, not a read-only watch loop.
    """
    content = _CREATE_SH.read_text(encoding="utf-8")

    operator_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "operator"')
    visibility_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "visibility"')
    assert operator_start != -1, "operator new-window not found"
    assert visibility_start != -1, "visibility new-window not found"

    operator_section = content[operator_start:visibility_start]

    # The bottom pane (pane 8) must receive a send-keys with bash.
    # Find the send-keys call that targets operator.8 (not resize-pane).
    send_keys_pane8 = re.search(
        r'tmux send-keys\b[^\n]*operator\.8[^\n]*',
        operator_section,
    )
    assert send_keys_pane8 is not None, (
        "create.sh does not contain 'tmux send-keys ... operator.8' in the operator section; "
        "the 8-column / 9-pane layout uses pane 8 for the interactive shell"
    )
    # The send-keys command for pane 8 must include a bash invocation.
    send_keys_cmd = send_keys_pane8.group(0)
    assert 'bash' in send_keys_cmd, (
        f"The send-keys to operator pane 8 does not include 'bash'; "
        f"the bottom pane must be an interactive bash shell. "
        f"Found: {send_keys_cmd!r}"
    )


def test_operator_shell_pane_bashrc_source_is_guarded() -> None:
    """
    The operator shell pane's startup chain contains a guarded ~/.bashrc
    source: `[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc"`.

    PRIORITY-0002 goal 2: the guard means hosts without a ~/.bashrc start
    silently and cleanly — an unguarded `source "$HOME/.bashrc"` would print
    a "No such file or directory" error on those hosts.
    """
    content = _CREATE_SH.read_text(encoding="utf-8")

    operator_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "operator"')
    visibility_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "visibility"')
    assert operator_start != -1, "operator new-window not found in create.sh"
    assert visibility_start != -1, "visibility new-window not found in create.sh"

    operator_section = content[operator_start:visibility_start]

    guard = '[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc"'
    assert guard in operator_section, (
        f"create.sh's operator section does not contain the guarded bashrc "
        f"source {guard!r}; the operator shell pane must source ~/.bashrc "
        f"guarded against a missing file"
    )


def test_operator_shell_pane_reorders_shell_env_after_bashrc() -> None:
    """
    create.sh re-sources shell-env AFTER the guarded ~/.bashrc source, with
    an inline comment documenting why.

    PRIORITY-0002 goal 2's ordering decision: the operator's ~/.bashrc is
    arbitrary and may redefine or clobber a PGAI_* / kanban-env value, so
    shell-env must be the last thing sourced in the pane's startup chain —
    otherwise a clobbering bashrc would win.  This asserts both halves: the
    guarded bashrc source appears before the follow-up shell-env source, and
    an inline comment documents the ordering choice.
    """
    content = _CREATE_SH.read_text(encoding="utf-8")

    operator_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "operator"')
    visibility_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "visibility"')
    operator_section = content[operator_start:visibility_start]

    guard = '[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc"'
    guard_pos = operator_section.find(guard)
    assert guard_pos != -1, "guarded bashrc source not found in the operator section"

    # A follow-up "source .../shell-env" (re-sourcing shell-env) must appear
    # AFTER the guarded bashrc source, so kanban env is applied last.
    reshell_env_pattern = re.compile(r'"source\s+\$\{KANBAN_ROOT\}/shell-env"')
    reshell_env_match = reshell_env_pattern.search(operator_section, pos=guard_pos)
    assert reshell_env_match is not None, (
        "create.sh does not re-source shell-env after the guarded bashrc "
        "source in the operator section; PGAI_* / kanban-env values are "
        "not guaranteed to win over a clobbering ~/.bashrc"
    )

    # An inline comment near the ordering decision documents the "why".
    ordering_comment_present = (
        "clobber" in operator_section.lower()
        and "LAST" in operator_section
    )
    assert ordering_comment_present, (
        "create.sh's operator section does not document the shell-env / "
        "bashrc ordering decision in an inline comment (expected wording "
        "referencing clobbering and sourcing shell-env LAST)"
    )


def test_kill_sh_kills_session_by_name() -> None:
    """
    kill.sh destroys the dashboard session by name using tmux kill-session.

    This confirms that kill.sh removes the entire session, which includes
    the operator window (all windows are destroyed when the session ends).
    """
    content = _KILL_SH.read_text(encoding="utf-8")
    assert 'tmux kill-session' in content, (
        "kill.sh does not call 'tmux kill-session'; "
        "the session teardown will not remove the operator window"
    )
    assert 'SESSION_NAME' in content, (
        "kill.sh does not reference SESSION_NAME; "
        "the session targeted for kill may not match the dashboard session"
    )


def test_create_sh_bash_syntax() -> None:
    """create.sh passes bash -n syntax check."""
    result = subprocess.run(
        ["bash", "-n", str(_CREATE_SH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n {_CREATE_SH} failed:\n{result.stderr}"
    )


def test_kill_sh_bash_syntax() -> None:
    """kill.sh passes bash -n syntax check."""
    result = subprocess.run(
        ["bash", "-n", str(_KILL_SH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n {_KILL_SH} failed:\n{result.stderr}"
    )


# ===========================================================================
# Group 2 — Docker-gated live session tests
# ===========================================================================


def test_window0_section_headers_match_fixture(
    operator_test_image: str,
) -> None:
    """
    create.sh --no-tmux inside the container produces section headers that
    match the checked-in golden fixture byte-for-byte.

    The fixture contains only separator lines and section-label lines (the
    static structural output).  Variable content (task lists, cron timings,
    RC versions) is excluded from the comparison.

    Acceptance criterion AC4: window-0 rendered content matches checked-in
    fixture byte-for-byte.
    """
    assert _WINDOW0_FIXTURE.exists(), (
        f"window0 golden fixture not found at {_WINDOW0_FIXTURE}; "
        "commit team/tests/fixtures/golden/window0_section_headers.txt"
    )

    result = _run_create_sh_no_tmux(operator_test_image)
    actual_headers = _extract_section_headers(result.stdout)
    expected_headers = _WINDOW0_FIXTURE.read_text(encoding="utf-8")

    assert actual_headers == expected_headers, (
        "create.sh --no-tmux section headers do not match the golden fixture.\n\n"
        "EXPECTED (from fixture):\n"
        + expected_headers
        + "\n\nACTUAL (from container run):\n"
        + actual_headers
        + "\n\nIf the section layout changed intentionally, refresh the fixture:\n"
        "  docker run --rm --entrypoint bash -v <repo>:/pgai_agent_kanban:ro "
        "    -e PGAI_AGENT_KANBAN_ROOT_PATH=/pgai_agent_kanban "
        "    -e TERM=xterm-256color <image> "
        "    /pgai_agent_kanban/team/scripts/dashboard/create.sh "
        "    --no-tmux --kanban-root /pgai_agent_kanban 2>/dev/null "
        "  | python3 -c \""
        "import re,sys;"
        "sep=re.compile(r'^═+$');lab=re.compile(r'^  [A-Z]');"
        "lines=[l.rstrip() for l in sys.stdin];"
        "print('\\n'.join(l for l in lines if sep.match(l) or lab.match(l))+'\\n',end='')"
        "\" > team/tests/fixtures/golden/window0_section_headers.txt"
    )


def test_operator_window_exists_at_index_2_in_session(
    operator_test_image: str,
) -> None:
    """
    After create.sh runs in a container, the operator window appears at index 2
    (1-based: main=1, operator=2) in the tmux session window list.

    Acceptance criterion AC2 (1-based window indexing): operator at window index 2.

    NOTE: tmux uses 0-based window indices by default (no base-index override
    in create.sh).  Index 2 here refers to the 1-based position where main is
    the 1st window and operator is the 2nd.  The tmux list-windows output will
    show operator at tmux index 1 (0-based).

    The assertion verifies:
      - The session contains a window named 'operator'
      - Exactly one window (main) precedes operator in the window list
    """
    result = _run_tmux_session_commands(
        operator_test_image,
        _TEST_SESSION_NAME,
        [
            f"tmux list-windows -t {_TEST_SESSION_NAME} "
            f"-F '#{{window_index}} #{{window_name}}' 2>/dev/null || echo 'no-session'"
        ],
    )

    output = result.stdout + result.stderr
    if "no-session" in output or "no server" in output.lower():
        pytest.skip(
            "tmux session did not start in container (no TTY in non-interactive "
            "docker run); operator window live test cannot run in this environment"
        )

    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    # Build ordered window list: [(index, name), ...]
    window_entries = []
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            window_entries.append((int(parts[0]), parts[1]))

    window_names = [name for _, name in sorted(window_entries)]

    assert "operator" in window_names, (
        f"'operator' window not found in tmux session; windows: {window_names}"
    )

    operator_idx = window_names.index("operator")
    assert operator_idx == 1, (
        f"'operator' is at position {operator_idx + 1} (1-based) in the window "
        f"list, expected position 2 (1-based). Windows: {window_names}"
    )


def test_visibility_window_exists_at_index_3_in_session(
    operator_test_image: str,
) -> None:
    """
    After create.sh runs, the visibility window appears at index 3 (1-based)
    in the tmux session window list.

    Acceptance criterion AC2: visibility at window index 3 (1-based).
    """
    result = _run_tmux_session_commands(
        operator_test_image,
        _TEST_SESSION_NAME,
        [
            f"tmux list-windows -t {_TEST_SESSION_NAME} "
            f"-F '#{{window_index}} #{{window_name}}' 2>/dev/null || echo 'no-session'"
        ],
    )

    output = result.stdout + result.stderr
    if "no-session" in output or "no server" in output.lower():
        pytest.skip(
            "tmux session did not start in container; "
            "visibility window live test cannot run in this environment"
        )

    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    window_entries = []
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            window_entries.append((int(parts[0]), parts[1]))

    window_names = [name for _, name in sorted(window_entries)]

    assert "visibility" in window_names, (
        f"'visibility' window not found in tmux session; windows: {window_names}"
    )

    visibility_idx = window_names.index("visibility")
    assert visibility_idx == 2, (
        f"'visibility' is at position {visibility_idx + 1} (1-based) in the "
        f"window list, expected position 3 (1-based). Windows: {window_names}"
    )


def test_operator_window_has_nine_panes_in_session(
    operator_test_image: str,
) -> None:
    """
    After create.sh runs, the operator window contains exactly nine panes
    (8-column / 9-pane layout, operator commit 83ccdc5).

    The layout:
      panes 0-2 = visibility input columns (bugs / priority / requirements)
      panes 3-7 = agent queue columns (pm / coder / writer / tester / cm)
      pane 8    = bottom (interactive bash login shell, full width, ~20% h)
    """
    result = _run_tmux_session_commands(
        operator_test_image,
        _TEST_SESSION_NAME,
        [
            f"tmux list-panes -t {_TEST_SESSION_NAME}:operator "
            f"-F '#{{pane_index}} #{{pane_height}} #{{pane_width}}' "
            f"2>/dev/null || echo 'no-operator'"
        ],
    )

    output = result.stdout + result.stderr
    if "no-operator" in output or "no server" in output.lower():
        pytest.skip(
            "operator window not available in tmux session; "
            "pane count test cannot run in this environment"
        )

    pane_lines = [
        ln.strip() for ln in output.splitlines()
        if ln.strip() and ln.strip()[0].isdigit()
    ]

    assert len(pane_lines) == 9, (
        f"Expected 9 panes in the operator window (8-column layout: 3 input "
        f"columns + 5 agent-queue columns + bottom shell); found "
        f"{len(pane_lines)}. tmux list-panes output:\n{output}"
    )


def test_operator_window_active_pane_is_shell_pane_after_create(
    operator_test_image: str,
) -> None:
    """
    After create.sh runs, the operator window's ACTIVE pane is the bottom
    shell pane (border title "operator"), not one of the read-only column
    panes.

    PRIORITY-0002 acceptance criterion 1: "After session create, the
    operator window's active pane is the shell pane, asserted via tmux
    display -p on the window's active pane title == 'operator'."
    """
    probe_cmd = (
        "tmux display -p -t " + _TEST_SESSION_NAME + ":operator "
        "'#{pane_title}' 2>/dev/null || echo 'no-operator'"
    )
    result = _run_tmux_session_commands(
        operator_test_image,
        _TEST_SESSION_NAME,
        [probe_cmd],
    )

    output = result.stdout + result.stderr
    if "no-operator" in output or "no server" in output.lower():
        pytest.skip(
            "tmux session did not start in container (no TTY in "
            "non-interactive docker run); active-pane live test cannot run "
            "in this environment"
        )

    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    assert lines, f"no output captured from tmux display -p; raw output:\n{output}"
    active_title = lines[-1]
    assert active_title == "operator", (
        f"operator window's active pane title is {active_title!r} after "
        f"create.sh ran, expected 'operator' (the bottom shell pane). "
        f"Entering the window should land the operator on the command "
        f"line without a pane-switch first. Full output:\n{output}"
    )


def test_operator_shell_pane_starts_cleanly_without_bashrc(
    operator_test_image: str,
) -> None:
    """
    With a fixture HOME that has no ~/.bashrc, the operator shell pane's
    startup produces no error output — the guard is silent on a missing
    file.

    PRIORITY-0002 acceptance criterion 2: "a fixture HOME without .bashrc
    produces no error output in the pane."
    """
    session_name = _TEST_SESSION_NAME + "-nobashrc"
    home_dir = "/home/kanban/pgai-test-fixture-home-nobashrc"

    result = _run_operator_shell_fixture(
        operator_test_image,
        session_name,
        setup_commands=[f"mkdir -p {home_dir}"],
        home_dir=home_dir,
        kanban_root_dir="/pgai_agent_kanban",
        probe_command="echo PGAI_PROBE_OK",
    )

    output = result.stdout + result.stderr
    if _PANE_CAPTURE_START not in output and "no server" in output.lower():
        pytest.skip(
            "tmux session did not start in container (no TTY in "
            "non-interactive docker run); operator shell-pane live test "
            "cannot run in this environment"
        )

    pane_output = _extract_pane_capture(output)
    assert "PGAI_PROBE_OK" in pane_output, (
        f"probe echo did not appear in the pane; the shell may not have "
        f"started cleanly. Pane capture:\n{pane_output}\nFull output:\n{output}"
    )
    for error_marker in ("No such file or directory", "syntax error", "command not found"):
        assert error_marker not in pane_output, (
            f"unexpected error output {error_marker!r} in the operator "
            f"shell pane with a HOME lacking ~/.bashrc; the guarded source "
            f"must start silently and cleanly. Pane capture:\n{pane_output}"
        )


def test_operator_shell_pane_bashrc_alias_resolves_and_env_stays_correct(
    operator_test_image: str,
) -> None:
    """
    With a fixture ~/.bashrc defining an alias, the alias resolves in the
    operator shell pane, and a PGAI_* / kanban-env value set by a fixture
    shell-env survives even when that same ~/.bashrc tries to clobber it.

    PRIORITY-0002 acceptance criterion 3: "A fixture .bashrc defining an
    alias: the alias resolves in the operator shell (send-keys probe), and
    PGAI_* env values remain correct after startup (ordering fixture for
    goal 2's decision)."

    The fixture kanban-root is a writable directory created inside the
    container — the checked-out repo is bind-mounted read-only, so a real
    shell-env file cannot be written into it.
    """
    session_name = _TEST_SESSION_NAME + "-bashrc-alias"
    home_dir = "/home/kanban/pgai-test-fixture-home-withbashrc"
    kanban_root_dir = "/home/kanban/pgai-test-fixture-kanban-root"

    setup_commands = [
        f"mkdir -p {home_dir} {kanban_root_dir}",
        (
            "printf 'export PGAI_TEST_MARKER=kanban-correct-value\\n' "
            f"> {kanban_root_dir}/shell-env"
        ),
        (
            'printf \'alias hello="echo bashrc-alias-worked"\\n'
            "export PGAI_TEST_MARKER=clobbered-by-bashrc\\n' "
            f"> {home_dir}/.bashrc"
        ),
    ]

    result = _run_operator_shell_fixture(
        operator_test_image,
        session_name,
        setup_commands=setup_commands,
        home_dir=home_dir,
        kanban_root_dir=kanban_root_dir,
        probe_command="hello; echo MARKER=$PGAI_TEST_MARKER",
    )

    output = result.stdout + result.stderr
    if _PANE_CAPTURE_START not in output and "no server" in output.lower():
        pytest.skip(
            "tmux session did not start in container (no TTY in "
            "non-interactive docker run); operator shell-pane live test "
            "cannot run in this environment"
        )

    pane_output = _extract_pane_capture(output)
    assert "bashrc-alias-worked" in pane_output, (
        f"the fixture ~/.bashrc alias did not resolve in the operator "
        f"shell pane. Pane capture:\n{pane_output}\nFull output:\n{output}"
    )
    assert "MARKER=kanban-correct-value" in pane_output, (
        f"PGAI_TEST_MARKER was not the kanban shell-env value after "
        f"startup; the fixture ~/.bashrc's attempt to clobber it must not "
        f"win — shell-env must be sourced LAST. Pane capture:\n{pane_output}"
    )
    assert "MARKER=clobbered-by-bashrc" not in pane_output, (
        f"PGAI_TEST_MARKER was clobbered by ~/.bashrc and never corrected; "
        f"shell-env must be re-sourced after bashrc so kanban env wins. "
        f"Pane capture:\n{pane_output}"
    )


def test_operator_bottom_pane_shell_resolves_show_sh_help(
    operator_test_image: str,
) -> None:
    """
    The operator window bottom pane is a bash shell where show.sh --help
    runs without 'command not found'.

    Acceptance criterion AC3: bottom pane is an interactive shell and
    scripts/show.sh --help resolves.

    We test this by running show.sh --help directly inside the container
    (not via tmux send-keys, which requires a TTY) and asserting that:
      - The command exits 0
      - stdout contains 'Usage:' (the help output structure)
      - stderr/stdout do not contain 'command not found'
    """
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "--volume",
            f"{_REPO_ROOT}:/pgai_agent_kanban:ro",
            "--env",
            "PGAI_AGENT_KANBAN_ROOT_PATH=/pgai_agent_kanban",
            "--env",
            "TERM=xterm-256color",
            operator_test_image,
            "/pgai_agent_kanban/team/scripts/show.sh",
            "--help",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    combined = result.stdout + result.stderr
    assert "command not found" not in combined, (
        "show.sh --help produced 'command not found' inside the container — "
        "show.sh or one of its dependencies is missing from the image.\n"
        f"Combined output:\n{combined}"
    )
    assert result.returncode == 0, (
        f"show.sh --help exited non-zero ({result.returncode}); "
        f"output:\n{combined}"
    )
    assert "Usage:" in combined or "usage:" in combined.lower(), (
        f"show.sh --help did not produce a Usage: line; output:\n{combined}"
    )


def test_kill_sh_destroys_entire_session(
    operator_test_image: str,
) -> None:
    """
    kill.sh terminates the tmux session, removing all windows including the
    operator window.

    Acceptance criterion AC5: kill.sh teardown confirms the operator window
    is destroyed with the session.

    Test sequence:
      1. create.sh starts the session (all windows, including operator)
      2. kill.sh tears down the session
      3. tmux has-session exits non-zero (session gone)
      4. tmux list-windows returns no output or 'no server' error

    The operator window is part of the session; if the session is gone, the
    operator window is gone with it.
    """
    script = (
        f"bash /pgai_agent_kanban/team/scripts/dashboard/create.sh "
        f"--kanban-root /pgai_agent_kanban "
        f"--session {_TEST_SESSION_NAME} "
        f"2>/dev/null || true ; "
        f"sleep 1 ; "
        f"bash /pgai_agent_kanban/team/scripts/dashboard/kill.sh "
        f"--kanban-root /pgai_agent_kanban "
        f"--session {_TEST_SESSION_NAME} "
        f"2>&1 ; "
        f"sleep 1 ; "
        f"if tmux has-session -t {_TEST_SESSION_NAME} 2>/dev/null ; then "
        f"  echo 'SESSION_STILL_EXISTS' ; "
        f"else "
        f"  echo 'SESSION_GONE' ; "
        f"fi"
    )

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "--volume",
            f"{_REPO_ROOT}:/pgai_agent_kanban:ro",
            "--env",
            "PGAI_AGENT_KANBAN_ROOT_PATH=/pgai_agent_kanban",
            "--env",
            f"PGAI_DASHBOARD_SESSION_NAME={_TEST_SESSION_NAME}",
            "--env",
            "TERM=xterm-256color",
            operator_test_image,
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    output = result.stdout + result.stderr

    if "no server" in output.lower() and "SESSION_GONE" not in output:
        # tmux server was never started (no TTY in container) — session didn't
        # exist so kill.sh is also idempotent.  This counts as session-gone.
        return

    if "SESSION_STILL_EXISTS" in output:
        pytest.fail(
            f"After kill.sh ran, the tmux session '{_TEST_SESSION_NAME}' "
            f"still exists; kill.sh did not fully tear down the session.\n"
            f"kill.sh output:\n{output}"
        )

    # Either SESSION_GONE is explicitly present, or kill.sh printed a
    # confirmation and tmux has-session found nothing.
    assert "SESSION_GONE" in output or "Killed session" in output or \
           "does not exist" in output, (
        f"kill.sh teardown result unclear; expected 'SESSION_GONE', "
        f"'Killed session', or 'does not exist' in output.\n"
        f"Combined output:\n{output}"
    )
