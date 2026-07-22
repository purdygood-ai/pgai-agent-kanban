"""
test_e2e_shipped_image.py
=========================
End-to-end docker-gated tests: verify the shipped image's baked ENTRYPOINT+CMD
chain works correctly against an INSTALL-SHAPED /pgai_agent_kanban volume, with
NO --entrypoint override and NO CMD override for the default-mode test.

WHY THIS MODULE EXISTS (BUG-0089)
----------------------------------
Prior fixtures (test_entrypoint_install_shaped.py, test_docker_build_fixture.py)
missed the dimension that mattered: the shipped image's OWN start path.

test_entrypoint_install_shaped.py exercised the entrypoint via the bind-mount
copy at /pgai_agent_kanban/scripts/entrypoint.sh with an explicit
--entrypoint override.  That proved the script's dispatch logic but NOT that
the image has a baked ENTRYPOINT wired to it.

BUG-0089 root cause: neither Dockerfile declared ENTRYPOINT, so CMD
["pseudocron"] was exec'd raw and failed with "executable file not found".

This suite exercises the image AS SHIPPED:
  - No --entrypoint flag
  - No CMD override for AC1 (the default path)
  - The baked /entrypoint.sh in the image handles all dispatch

ACCEPTANCE CRITERIA COVERED (BUG-0089 AC1-4)
---------------------------------------------
AC1  Build image; run with four mounts against an install-shaped volume and
     NO --entrypoint, NO CMD override → the baked ENTRYPOINT+CMD chain fires,
     entrypoint narration line appears in logs, mount verification passes, and
     pseudocron reaches its startup banner.

AC2  With PGAI_WORKSPACE_MOUNT set to a path that does NOT exist (simulating
     a missing /home/<user> bind-mount), the shipped image exits 1 with a
     stderr line naming PGAI_WORKSPACE_MOUNT.  No --entrypoint override used.

AC3  command: dashboard dispatches to scripts/dashboard/create.sh via the
     baked entrypoint (sentinel log line in container stderr).
     command: ["--","echo","ok"] dispatches through the entrypoint's explicit
     passthrough branch (stdout contains "ok").  Both verified without
     --entrypoint override.

AC4  Both flavors' (debian, rhel9) gated suites exit 0 when docker is available.

DOCKER-AVAILABILITY GATE
-------------------------
When the docker binary is absent or the daemon is unreachable, every test in
this module is skipped with a narrated reason — never a silent pass, never ERROR.

MOUNT CONTRACT
--------------
entrypoint.sh verifies four mounts.  Tests supply all four via docker run -v:
  /pgai_agent_kanban  — install-shaped fixture (tmp_path tree)
  PGAI_WORKSPACE_MOUNT path — explicit workspace path inside container
  /claude             — empty tmp dir
  ~/.claude           — empty tmp dir (HOME set to tmp dir via --env)

PORT PUBLISHING
---------------
No test publishes a host-reachable port.  docker run --rm is used throughout.
DIRECTIVES rule 1 is not violated.

Test naming (SOP.md anti-pattern 6):
Names describe behavior, not bug IDs or scaffolding labels.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import NamedTuple

import pytest

# ---------------------------------------------------------------------------
# Path resolution — anchored to the test file location.
# test file:  team/tests/unit/test_e2e_shipped_image.py
# team/:      team/tests/unit/../../../ = team/  (3 levels up)
# repo root:  team/../                  = repo root
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent


# ---------------------------------------------------------------------------
# Flavor configuration (parametrize both Dockerfiles for AC1-4)
# ---------------------------------------------------------------------------


class _FlavorConfig(NamedTuple):
    """Build parameters for one image flavor."""
    name: str
    dockerfile: pathlib.Path
    build_context: pathlib.Path
    image_tag: str


_FLAVORS: list[_FlavorConfig] = [
    _FlavorConfig(
        name="debian",
        dockerfile=_REPO_ROOT / "docker" / "debian" / "Dockerfile",
        build_context=_REPO_ROOT,
        image_tag="pgai-kanban-e2e-shipped-debian:test",
    ),
    _FlavorConfig(
        name="rhel9",
        dockerfile=_REPO_ROOT / "docker" / "rhel9" / "Dockerfile",
        build_context=_REPO_ROOT,
        image_tag="pgai-kanban-e2e-shipped-rhel9:test",
    ),
]

_FLAVOR_IDS = [f.name for f in _FLAVORS]


# ---------------------------------------------------------------------------
# Docker availability helpers
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
    """Print a visible skip notice to stdout before calling pytest.skip.

    A narrated skip distinguishes "fixture evaluated and consciously skipped"
    from "test accidentally excluded" when reading a CI log.
    """
    print(
        f"\n[e2e-shipped-image] SKIP — {reason}",
        flush=True,
    )
    pytest.skip(reason)


# ---------------------------------------------------------------------------
# Module-scoped parametrized fixture: build each flavor once per session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", params=_FLAVORS, ids=_FLAVOR_IDS)
def shipped_image_tag(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> str:
    """Build docker/<flavor>/Dockerfile and return the image tag on success.

    Parametrized over all flavor configurations in _FLAVORS so each flavor's
    image is built once and reused by all tests in this module, then removed
    in teardown.

    The build uses the standard Dockerfile (no --build-arg overrides) so the
    image is bit-for-bit identical to what an operator would ship.

    Skips (with narration) when Docker is unavailable or the Dockerfile is missing.
    """
    flavor: _FlavorConfig = request.param

    if not _docker_available():
        _narrate_skip(
            "docker binary not found or Docker daemon unreachable; "
            "end-to-end shipped-image tests require a running Docker environment"
        )

    if not flavor.dockerfile.exists():
        pytest.fail(
            f"docker/{flavor.name}/Dockerfile not found at {flavor.dockerfile}; "
            "ensure the file was committed to the repository"
        )

    print(
        f"\n[e2e-shipped-image] [{flavor.name}] Building shipped image "
        f"from {flavor.dockerfile} ...",
        flush=True,
    )
    result = subprocess.run(
        [
            "docker",
            "build",
            "--tag",
            flavor.image_tag,
            "--file",
            str(flavor.dockerfile),
            str(flavor.build_context),
        ],
        capture_output=True,
        text=True,
        timeout=600,  # 10 minutes — package downloads on first run (RHEL9 EPEL, tmux source)
    )

    if result.returncode != 0:
        print(
            f"\n[e2e-shipped-image] [{flavor.name}] BUILD STDOUT:\n"
            + result.stdout,
            flush=True,
        )
        print(
            f"\n[e2e-shipped-image] [{flavor.name}] BUILD STDERR:\n"
            + result.stderr,
            flush=True,
        )
        pytest.fail(
            f"docker build [{flavor.name}] exited {result.returncode}; "
            "see build output above"
        )

    print(
        f"[e2e-shipped-image] [{flavor.name}] Shipped image built: {flavor.image_tag}",
        flush=True,
    )

    yield flavor.image_tag

    # Teardown: remove the test image to avoid accumulating dangling tags.
    subprocess.run(
        ["docker", "image", "rm", "--force", flavor.image_tag],
        capture_output=True,
    )
    print(
        f"[e2e-shipped-image] [{flavor.name}] Removed image tag: {flavor.image_tag}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Per-test install-shaped fixture volume (function-scoped, fresh per test)
# ---------------------------------------------------------------------------


@pytest.fixture
def install_shaped_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build an install-shaped kanban root in tmp_path for bind-mounting.

    Delegates to build_install_shaped_kanban_root() from the fixture module.
    Each test gets a fresh root under its own tmp_path so tests do not share
    state.

    Returns:
        pathlib.Path — absolute path to the install-shaped kanban root.
    """
    from tests.fixtures.install_shaped_kanban_root import (
        build_install_shaped_kanban_root,
    )
    return build_install_shaped_kanban_root(tmp_path)


# ---------------------------------------------------------------------------
# Helper: build the four-mount docker run argument list (NO --entrypoint flag)
# ---------------------------------------------------------------------------


def _shipped_four_mount_run_args(
    image_tag: str,
    install_root: pathlib.Path,
    workspace_dir: pathlib.Path,
    claude_payload_dir: pathlib.Path,
    claude_config_dir: pathlib.Path,
) -> list[str]:
    """Build a docker run argv satisfying the four-mount contract.

    CRITICAL DIFFERENCE from test_entrypoint_install_shaped._four_mount_run_args:
    This helper does NOT include --entrypoint.  The image's baked /entrypoint.sh
    (from ENTRYPOINT ["/entrypoint.sh"] in the Dockerfile) handles all dispatch.
    This is the end-to-end test of the SHIPPED artifact's own start path.

    All mounts use absolute paths inside tmp_path — never the live install or
    dev tree.

    Args:
        image_tag:          Image to run (the shipped, baked image).
        install_root:       Install-shaped kanban root (→ /pgai_agent_kanban).
        workspace_dir:      Directory for PGAI_WORKSPACE_MOUNT.
        claude_payload_dir: Directory for /claude.
        claude_config_dir:  Directory for ~/.claude (HOME set to parent).

    Returns:
        list[str] — partial argv for subprocess.run starting with ["docker", "run"],
        ending with the image_tag.  The caller appends CMD arguments after the
        image tag for AC3.  For AC1 (default mode), nothing is appended.
    """
    # The container's HOME must point at the parent of .claude so that
    # entrypoint.sh resolves ${HOME:-/root}/.claude to the bind-mounted dir.
    container_home = "/home/kanban-test"
    workspace_container_path = f"{container_home}/workspace"
    claude_config_container_path = f"{container_home}/.claude"

    argv = [
        "docker", "run",
        "--rm",
        # NO --entrypoint flag: the baked ENTRYPOINT ["/entrypoint.sh"] handles dispatch.
        # Mount 1: /pgai_agent_kanban — install-shaped kanban root
        "--volume", f"{install_root}:/pgai_agent_kanban",
        # Mount 2: workspace — explicit-path mode via PGAI_WORKSPACE_MOUNT
        "--volume", f"{workspace_dir}:{workspace_container_path}",
        # Mount 3: /claude — site-specific payload directory
        "--volume", f"{claude_payload_dir}:/claude",
        # Mount 4: ~/.claude — agent CLI config (HOME controls the ~ resolution)
        "--volume", f"{claude_config_dir}:{claude_config_container_path}",
        # Set HOME so ~/.claude resolves to our bind-mounted dir
        "--env", f"HOME={container_home}",
        # Set PGAI_WORKSPACE_MOUNT to the explicit container-side workspace path
        # so entrypoint uses explicit-path mode (no glob over /home/*/).
        "--env", f"PGAI_WORKSPACE_MOUNT={workspace_container_path}",
        # TERM must be set for tput-using scripts inside the container.
        "--env", "TERM=xterm-256color",
        # No published ports — DIRECTIVES rule 1.
        image_tag,
    ]

    return argv


# ---------------------------------------------------------------------------
# Helper: create the three ancillary mount directories under tmp_path
# ---------------------------------------------------------------------------


def _make_ancillary_mounts(tmp_path: pathlib.Path) -> tuple[
    pathlib.Path, pathlib.Path, pathlib.Path
]:
    """Create the three ancillary mount directories under tmp_path.

    Returns:
        (workspace_dir, claude_payload_dir, claude_config_dir)
    """
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True)

    claude_payload_dir = tmp_path / "claude_payload"
    claude_payload_dir.mkdir(parents=True)

    claude_config_dir = tmp_path / "claude_config"
    claude_config_dir.mkdir(parents=True)

    return workspace_dir, claude_payload_dir, claude_config_dir


# ---------------------------------------------------------------------------
# AC1 — Default mode (baked CMD "pseudocron", baked ENTRYPOINT, no overrides)
# ---------------------------------------------------------------------------


def test_baked_entrypoint_default_mode_reaches_pseudocron(
    shipped_image_tag: str,
    install_shaped_root: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Baked ENTRYPOINT+CMD default path reaches pseudocron startup with no overrides.

    AC1 (BUG-0089): the shipped image runs with NO --entrypoint and NO CMD
    override.  The baked ENTRYPOINT ["/entrypoint.sh"] receives CMD ["pseudocron"]
    as $1, dispatches to pseudocron mode, and the startup banner appears in
    container logs.

    Evidence asserted:
      - "entrypoint: starting pseudocron" in combined output — the baked
        entrypoint received "pseudocron" as $1 and dispatched correctly.
      - "pseudocron starting:" in combined output — pseudocron.py was found
        at the install-layout path scripts/pseudocron.py and parsed pseudocron.cfg.

    The absence of an --entrypoint flag is the critical constraint: if the
    Dockerfile has no ENTRYPOINT baked in, docker will exec CMD directly
    ("pseudocron" is not in PATH → exec failure).  Seeing the startup banner
    proves ENTRYPOINT is baked and wired.
    """
    workspace, claude_payload, claude_config = _make_ancillary_mounts(tmp_path)

    # Build argv with NO extra CMD argument — baked CMD ["pseudocron"] is used.
    argv = _shipped_four_mount_run_args(
        shipped_image_tag,
        install_shaped_root,
        workspace,
        claude_payload,
        claude_config,
    )
    # Do NOT append any CMD args; the Dockerfile's CMD ["pseudocron"] is used as-is.

    print(
        f"\n[e2e-shipped-image] AC1 test: running {shipped_image_tag} with "
        "baked ENTRYPOINT+CMD (no overrides); expect pseudocron startup banner",
        flush=True,
    )

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=30,  # pseudocron banner emits before any sleep
        )
    except subprocess.TimeoutExpired as exc:
        # Timeout is acceptable: pseudocron entered its sleep loop, meaning
        # it started successfully.  Collect what was captured.
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
    else:
        stdout = result.stdout
        stderr = result.stderr

    combined = stdout + stderr
    print(
        f"[e2e-shipped-image] AC1 container output (combined):\n{combined}",
        flush=True,
    )

    # Assert the baked entrypoint dispatched to pseudocron mode.
    # If the Dockerfile has no ENTRYPOINT, docker exec's "pseudocron" directly
    # and this assertion fails with an exec error (not this string).
    assert "entrypoint: starting pseudocron" in combined, (
        "baked entrypoint did not dispatch to pseudocron mode; "
        "expected 'entrypoint: starting pseudocron' in combined output.\n\n"
        "This likely means:\n"
        "  - The Dockerfile has no baked ENTRYPOINT (BUG-0089 not fixed)\n"
        "  - The baked /entrypoint.sh does not recognize 'pseudocron' as $1\n\n"
        f"Full combined output:\n{combined}"
    )

    # Assert pseudocron found and parsed its config at the install-layout path.
    assert "pseudocron starting:" in combined, (
        "pseudocron startup banner not found in container output; "
        "expected 'pseudocron starting:' confirming scripts/pseudocron.py "
        "was found at the install-layout path and pseudocron.cfg was parsed.\n\n"
        "This may mean:\n"
        "  - PGAI_AGENT_KANBAN_ROOT_PATH was not set to /pgai_agent_kanban\n"
        "  - scripts/pseudocron.py is missing from the install-shaped fixture\n"
        "  - pseudocron.cfg is missing from the fixture root\n\n"
        f"Full combined output:\n{combined}"
    )


# ---------------------------------------------------------------------------
# AC2 — Missing workspace causes exit 1 naming PGAI_WORKSPACE_MOUNT
# ---------------------------------------------------------------------------


def test_baked_entrypoint_missing_workspace_exits_with_diagnostic(
    shipped_image_tag: str,
    install_shaped_root: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Missing workspace bind-mount causes the shipped image to exit 1 with diagnostic.

    AC2 (BUG-0089): with PGAI_WORKSPACE_MOUNT set to a path that does NOT exist
    inside the container (the workspace bind-mount is absent), the shipped image
    exits 1 and stderr names PGAI_WORKSPACE_MOUNT in the error message.

    This is verified via the SHIPPED image (baked /entrypoint.sh), not via a
    wrapper or an --entrypoint override.

    The test withholds the workspace bind-mount (--volume for workspace is
    omitted) but keeps PGAI_WORKSPACE_MOUNT set so the entrypoint uses explicit-
    path mode.  The declared path does not exist → _MISSING is non-empty → exit 1.
    """
    claude_payload_dir = tmp_path / "claude_payload"
    claude_payload_dir.mkdir(parents=True)

    container_home = "/home/kanban-test"
    workspace_container_path = f"{container_home}/workspace"
    claude_config_dir = tmp_path / "claude_config"
    claude_config_dir.mkdir(parents=True)
    claude_config_container_path = f"{container_home}/.claude"

    # Build argv WITHOUT the workspace volume mount so the path is absent.
    argv = [
        "docker", "run",
        "--rm",
        # NO --entrypoint: baked /entrypoint.sh handles everything.
        # Mount 1: /pgai_agent_kanban — install-shaped kanban root (present)
        "--volume", f"{install_shaped_root}:/pgai_agent_kanban",
        # Mount 2: workspace intentionally OMITTED so PGAI_WORKSPACE_MOUNT path is missing.
        # Mount 3: /claude — present
        "--volume", f"{claude_payload_dir}:/claude",
        # Mount 4: ~/.claude — present
        "--volume", f"{claude_config_dir}:{claude_config_container_path}",
        "--env", f"HOME={container_home}",
        # PGAI_WORKSPACE_MOUNT points at a path that does NOT exist in the container
        # (because we omitted the workspace volume mount above).
        "--env", f"PGAI_WORKSPACE_MOUNT={workspace_container_path}",
        "--env", "TERM=xterm-256color",
        shipped_image_tag,
        # No CMD argument: baked CMD ["pseudocron"] would be used, but mount
        # verification fires first and exits 1 before any dispatch.
    ]

    print(
        f"\n[e2e-shipped-image] AC2 test: running {shipped_image_tag} with "
        "workspace mount absent; expect exit 1 naming PGAI_WORKSPACE_MOUNT",
        flush=True,
    )

    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=180,  # 3 minutes — covers cold-start docker run on reference host (BUG-0096)
    )

    combined = result.stdout + result.stderr
    print(
        f"[e2e-shipped-image] AC2 container output (combined):\n{combined}",
        flush=True,
    )

    # Assert exit code 1 — the entrypoint's missing-mount path.
    assert result.returncode == 1, (
        f"container exited {result.returncode}; expected 1 when workspace mount absent.\n\n"
        "This means the entrypoint did not detect the missing workspace and "
        "exit with failure — either the mount verification logic is broken or "
        "the baked /entrypoint.sh was not executed.\n\n"
        f"Full combined output:\n{combined}"
    )

    # Assert stderr names PGAI_WORKSPACE_MOUNT so the operator knows what to fix.
    assert "PGAI_WORKSPACE_MOUNT" in combined, (
        "PGAI_WORKSPACE_MOUNT not found in container output; "
        "expected the entrypoint to name the missing mount variable in its "
        "error message so the operator has an actionable diagnostic.\n\n"
        "entrypoint.sh should include PGAI_WORKSPACE_MOUNT in the MISSING entry "
        "when the explicit-path mode path does not exist.\n\n"
        f"Full combined output:\n{combined}"
    )


# ---------------------------------------------------------------------------
# AC3a — command: dashboard dispatches via baked entrypoint
# ---------------------------------------------------------------------------


def test_baked_entrypoint_dashboard_command_dispatches_to_create_sh(
    shipped_image_tag: str,
    install_shaped_root: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """command: dashboard dispatches to create.sh via the baked entrypoint.

    AC3 (BUG-0089) — dashboard path: docker run ... <image> dashboard uses
    the baked ENTRYPOINT to dispatch the "dashboard" argument to
    scripts/dashboard/create.sh.

    Evidence: the stub create.sh in the install-shaped fixture emits
    "install-fixture: dashboard create.sh stub reached" to stderr.  The
    baked entrypoint also emits its own narration line ("entrypoint: starting
    dashboard tmux session") before exec'ing create.sh.

    No --entrypoint flag is used.  The baked /entrypoint.sh is what runs.
    """
    workspace, claude_payload, claude_config = _make_ancillary_mounts(tmp_path)

    argv = _shipped_four_mount_run_args(
        shipped_image_tag,
        install_shaped_root,
        workspace,
        claude_payload,
        claude_config,
    )
    # Append the dashboard command — overrides the baked CMD ["pseudocron"].
    argv.append("dashboard")

    print(
        f"\n[e2e-shipped-image] AC3a test: running {shipped_image_tag} with "
        "command=dashboard (baked entrypoint); expect create.sh stub sentinel",
        flush=True,
    )

    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=180,  # 3 minutes — covers cold-start docker run on reference host (BUG-0096)
    )

    combined = result.stdout + result.stderr
    print(
        f"[e2e-shipped-image] AC3a container output (combined):\n{combined}",
        flush=True,
    )

    # Assert the baked entrypoint dispatched to dashboard mode.
    assert "entrypoint: starting dashboard" in combined, (
        "baked entrypoint did not dispatch to dashboard mode; "
        "expected 'entrypoint: starting dashboard' in combined output.\n\n"
        "This means the baked /entrypoint.sh did not receive 'dashboard' as $1, "
        "or the dashboard case branch is broken.\n\n"
        f"Full combined output:\n{combined}"
    )

    # Assert the stub create.sh was reached at the install-layout path.
    assert "dashboard create.sh stub reached" in combined, (
        "create.sh stub sentinel not found in container output; "
        "expected 'dashboard create.sh stub reached' confirming "
        "scripts/dashboard/create.sh was invoked at the install-layout path.\n\n"
        "This may mean:\n"
        "  - scripts/dashboard/create.sh is missing from the install-shaped fixture\n"
        "  - The baked entrypoint used a different path for dashboard dispatch\n\n"
        f"Full combined output:\n{combined}"
    )


# ---------------------------------------------------------------------------
# AC3b — command: ["--","echo","ok"] dispatches via baked entrypoint passthrough
# ---------------------------------------------------------------------------


def test_baked_entrypoint_explicit_passthrough_executes_command(
    shipped_image_tag: str,
    install_shaped_root: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """command: ['--','echo','ok'] dispatches through the baked entrypoint passthrough.

    AC3 (BUG-0089) — explicit passthrough: docker run ... <image> -- echo ok
    uses the '--' sentinel to trigger the entrypoint's explicit passthrough branch
    (shift; exec "$@"), which exec's 'echo ok' directly.

    Evidence: stdout contains "ok" (echo output) and the container exits 0.

    No --entrypoint flag is used.  The baked /entrypoint.sh is what runs.
    """
    workspace, claude_payload, claude_config = _make_ancillary_mounts(tmp_path)

    argv = _shipped_four_mount_run_args(
        shipped_image_tag,
        install_shaped_root,
        workspace,
        claude_payload,
        claude_config,
    )
    # Append ["--", "echo", "ok"] — the "--" triggers the explicit passthrough branch.
    argv.extend(["--", "echo", "ok"])

    print(
        f"\n[e2e-shipped-image] AC3b test: running {shipped_image_tag} with "
        "command=['--','echo','ok'] (baked entrypoint passthrough); expect 'ok' in stdout",
        flush=True,
    )

    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=30,
    )

    combined = result.stdout + result.stderr
    print(
        f"[e2e-shipped-image] AC3b container output (combined):\n{combined}",
        flush=True,
    )

    assert result.returncode == 0, (
        f"container exited {result.returncode}; expected 0 for 'echo ok' passthrough.\n\n"
        f"Full combined output:\n{combined}"
    )

    # Assert the echo output reached stdout.
    assert "ok" in result.stdout, (
        "echo output 'ok' not found in container stdout; "
        "the explicit passthrough branch (--) did not exec 'echo ok'.\n\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Non-parametrized structural tests (no docker required)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flavor", _FLAVORS, ids=_FLAVOR_IDS)
def test_dockerfile_has_baked_entrypoint_declaration(flavor: _FlavorConfig) -> None:
    """Each Dockerfile must contain ENTRYPOINT ['/entrypoint.sh'].

    Structural check: verifies the Dockerfile declares the baked entrypoint
    so the image does not need an --entrypoint override to dispatch correctly.
    This check requires no docker daemon — only filesystem access.
    """
    assert flavor.dockerfile.exists(), (
        f"docker/{flavor.name}/Dockerfile not found at {flavor.dockerfile}; "
        "ensure the file was committed to the repository"
    )
    content = flavor.dockerfile.read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["/entrypoint.sh"]' in content, (
        f"docker/{flavor.name}/Dockerfile does not contain "
        'ENTRYPOINT ["/entrypoint.sh"].\n\n'
        "The Dockerfile must bake the entrypoint dispatcher so that CMD arguments "
        "are passed through to the dispatcher rather than exec'd directly.\n\n"
        "Add to the Dockerfile:\n"
        '  COPY --chmod=+x docker/entrypoint.sh /entrypoint.sh\n'
        '  ENTRYPOINT ["/entrypoint.sh"]'
    )


@pytest.mark.parametrize("flavor", _FLAVORS, ids=_FLAVOR_IDS)
def test_dockerfile_copies_entrypoint_with_exec_permission(flavor: _FlavorConfig) -> None:
    """Each Dockerfile must COPY docker/entrypoint.sh /entrypoint.sh with +x mode.

    Structural check: verifies the Dockerfile both copies the script AND sets the
    executable bit.  Without --chmod=+x, docker exec'ing /entrypoint.sh fails with
    'permission denied'.  No docker daemon required.
    """
    assert flavor.dockerfile.exists(), (
        f"docker/{flavor.name}/Dockerfile not found at {flavor.dockerfile}"
    )
    content = flavor.dockerfile.read_text(encoding="utf-8")
    # Accept either --chmod=+x or --chmod=755 (both grant execute permission).
    has_copy_with_x = (
        "COPY --chmod=+x docker/entrypoint.sh /entrypoint.sh" in content
        or "COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh" in content
    )
    assert has_copy_with_x, (
        f"docker/{flavor.name}/Dockerfile does not COPY docker/entrypoint.sh "
        "with executable permissions.\n\n"
        "Expected one of:\n"
        "  COPY --chmod=+x docker/entrypoint.sh /entrypoint.sh\n"
        "  COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh\n\n"
        f"Dockerfile content relevant excerpt:\n"
        + "\n".join(
            line for line in content.splitlines() if "entrypoint" in line.lower()
        )
    )
