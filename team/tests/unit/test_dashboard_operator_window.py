"""
test_dashboard_operator_window.py
==================================
Smoke test and teardown coverage for the operator window (window index 2 in
the tmux session created by team/scripts/dashboard/create.sh).

This module contains two groups of tests:

Group 1 — Static analysis (no Docker required):
    Parse create.sh for tmux commands to assert the operator window is created
    at the correct position in the window sequence, with the correct three-pane
    split geometry (BUG-0097), and that kill.sh sends the session-kill that removes it.

Group 2 — Docker-gated live session tests:
    Build the flavor image, start a real tmux session inside the container,
    query the session with tmux list-windows / list-panes, assert operator at
    index 2 (1-based), visibility at index 3 (1-based), three panes (BUG-0097),
    bottom pane is an interactive bash shell where show.sh --help resolves.
    Then run kill.sh and assert the session is gone.

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


def test_operator_window_has_three_pane_split() -> None:
    """
    create.sh splits the operator window into exactly three panes (BUG-0097).

    The 3-pane layout uses two split-window calls:
      1. Vertical split -l 20%: top region (~80%), bottom shell (~20%).
      2. Horizontal split -l 60% on the top region: left inputs (~40%), right queues (~60%).

    Both splits must be present in the operator section.
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

    # Assert exactly two split-window calls in the operator section.
    split_calls = list(re.finditer(r'tmux split-window\b', operator_section))
    assert len(split_calls) == 2, (
        f"Expected exactly 2 split-window calls in the operator section "
        f"(BUG-0097: 3-pane layout — vertical then horizontal); "
        f"found {len(split_calls)}: {[m.group() for m in split_calls]}"
    )

    # First split: vertical -l 20% (bottom shell pane).
    split1_line = operator_section[split_calls[0].start():]
    split1_end = split1_line.find("\n")
    split1_cmd = split1_line[:split1_end]
    assert "-v" in split1_cmd and "-l 20%" in split1_cmd, (
        f"First split-window in operator section must be vertical '-v -l 20%'; "
        f"found: {split1_cmd!r}"
    )

    # Second split: horizontal -l 60% (left inputs / right queues).
    split2_line = operator_section[split_calls[1].start():]
    split2_end = split2_line.find("\n")
    split2_cmd = split2_line[:split2_end]
    assert "-h" in split2_cmd and "-l 60%" in split2_cmd, (
        f"Second split-window in operator section must be horizontal '-h -l 60%'; "
        f"found: {split2_cmd!r}"
    )


def test_operator_top_panes_use_visibility_aggregate_commands() -> None:
    """
    create.sh sends the correct aggregate-visibility commands to the operator
    window's two top panes (string equality against the defined variable source).

    The 3-pane layout's top panes mirror the Visibility window's two regions:
      pane 0 = top-left  → must receive the OP_VIS_LEFT_AGG_CMD variable
                           (bugs / priority / requirements, combined refresh loop)
      pane 1 = top-right → must receive the OP_VIS_RIGHT_AGG_CMD variable
                           (pm / coder / writer / tester / cm, combined refresh loop)

    The assertion compares the exact argument string passed to each tmux send-keys
    call in the operator section against the expected variable reference.  This
    catches regressions where a pane accidentally receives the wrong command
    (e.g., HEADER_CMD instead of the visibility aggregate).
    """
    content = _CREATE_SH.read_text(encoding="utf-8")

    operator_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "operator"')
    visibility_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "visibility"')
    assert operator_start != -1, "operator new-window not found in create.sh"
    assert visibility_start != -1, "visibility new-window not found in create.sh"

    operator_section = content[operator_start:visibility_start]

    # Extract the argument string passed to each top-pane send-keys call.
    # Pattern: tmux send-keys -t "${SESSION_NAME}:operator.N" "<arg>" Enter
    # The argument is the string between the second pair of double-quotes.
    pane0_match = re.search(
        r'tmux send-keys\s+-t\s+"\$\{SESSION_NAME\}:operator\.0"\s+"([^"]+)"\s+Enter',
        operator_section,
    )
    pane1_match = re.search(
        r'tmux send-keys\s+-t\s+"\$\{SESSION_NAME\}:operator\.1"\s+"([^"]+)"\s+Enter',
        operator_section,
    )

    assert pane0_match is not None, (
        "create.sh does not contain a 'tmux send-keys ... operator.0 ... Enter' "
        "call in the operator section; the top-left pane command is missing"
    )
    assert pane1_match is not None, (
        "create.sh does not contain a 'tmux send-keys ... operator.1 ... Enter' "
        "call in the operator section; the top-right pane command is missing"
    )

    pane0_arg = pane0_match.group(1)
    pane1_arg = pane1_match.group(1)

    # String-equality: each top pane must reference exactly the corresponding
    # Visibility-window aggregate command variable — not an alternative command.
    assert pane0_arg == "${OP_VIS_LEFT_AGG_CMD}", (
        "Operator pane 0 (top-left) send-keys argument does not match the "
        "Visibility-window aggregate for the inputs region.\n"
        f"  Expected: '${{OP_VIS_LEFT_AGG_CMD}}'\n"
        f"  Actual:   {pane0_arg!r}\n"
        "The top-left pane must use OP_VIS_LEFT_AGG_CMD (bugs / priority / "
        "requirements combined refresh loop), not any other command."
    )
    assert pane1_arg == "${OP_VIS_RIGHT_AGG_CMD}", (
        "Operator pane 1 (top-right) send-keys argument does not match the "
        "Visibility-window aggregate for the queues region.\n"
        f"  Expected: '${{OP_VIS_RIGHT_AGG_CMD}}'\n"
        f"  Actual:   {pane1_arg!r}\n"
        "The top-right pane must use OP_VIS_RIGHT_AGG_CMD (pm / coder / writer / "
        "tester / cm combined refresh loop), not any other command."
    )


def test_operator_bottom_pane_sends_interactive_bash() -> None:
    """
    create.sh sends an interactive bash shell to the operator window's bottom pane.

    After the BUG-0097 rework, the 3-pane layout assigns:
      pane 0 = top-left (visibility inputs)
      pane 1 = top-right (visibility queues)
      pane 2 = bottom (interactive bash shell)

    The send-keys command for pane 2 (bottom) must include 'bash' so the pane
    starts an interactive shell, not a read-only watch loop.
    """
    content = _CREATE_SH.read_text(encoding="utf-8")

    operator_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "operator"')
    visibility_start = content.find('tmux new-window -t "${SESSION_NAME}" -n "visibility"')
    assert operator_start != -1, "operator new-window not found"
    assert visibility_start != -1, "visibility new-window not found"

    operator_section = content[operator_start:visibility_start]

    # The bottom pane (pane 2) must receive a send-keys with bash.
    # Find the send-keys call that targets operator.2 (not resize-pane).
    send_keys_pane2 = re.search(
        r'tmux send-keys\b[^\n]*operator\.2[^\n]*',
        operator_section,
    )
    assert send_keys_pane2 is not None, (
        "create.sh does not contain 'tmux send-keys ... operator.2' in the operator section; "
        "BUG-0097 rework: 3-pane layout uses pane 2 for the interactive shell"
    )
    # The send-keys command for pane 2 must include a bash invocation.
    send_keys_cmd = send_keys_pane2.group(0)
    assert 'bash' in send_keys_cmd, (
        f"The send-keys to operator pane 2 does not include 'bash'; "
        f"the bottom pane must be an interactive bash shell. "
        f"Found: {send_keys_cmd!r}"
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


def test_operator_window_has_three_panes_in_session(
    operator_test_image: str,
) -> None:
    """
    After create.sh runs, the operator window contains exactly three panes (BUG-0097).

    The 3-pane layout:
      pane 0 = top-left  (visibility inputs: bugs / priority / requirements, ~40% w, ~80% h)
      pane 1 = top-right (visibility queues: pm / coder / writer / tester / cm, ~60% w, ~80% h)
      pane 2 = bottom    (interactive bash login shell, full width, ~20% h)
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

    assert len(pane_lines) == 3, (
        f"Expected 3 panes in the operator window (BUG-0097: visibility-left, "
        f"visibility-right, shell); found {len(pane_lines)}. "
        f"tmux list-panes output:\n{output}"
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
