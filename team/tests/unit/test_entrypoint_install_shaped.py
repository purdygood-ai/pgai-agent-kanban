"""
test_entrypoint_install_shaped.py
==================================
Docker-gated tests: install-shaped fixture volume proves the entrypoint's
default, dashboard, and passthrough modes reach their targets when the
/pgai_agent_kanban bind-mount is INSTALL-SHAPED (no top-level team/ directory).

WHY INSTALL-SHAPED MATTERS (BUG-0088 AC1-3)
--------------------------------------------
The v1.26.0 docker-gated tests used a dev-tree-shaped volume for the
/pgai_agent_kanban bind-mount.  In that layout both INSTALL paths
(scripts/pseudocron.py) and legacy DEV-TREE paths (team/scripts/pseudocron.py)
could resolve — masking defects in the entrypoint path.  This suite uses
install_shaped_kanban_root (team/tests/fixtures/install_shaped_kanban_root.py)
to produce a volume that mirrors what a real operator mounts: scripts at
scripts/, not team/scripts/.  Only the INSTALL-layout path resolves.

ACCEPTANCE CRITERIA COVERED
----------------------------
AC1  compose up with NO command: line, four mounts, INSTALL-shaped volume
     → pseudocron reaches its first scheduled-wake barrier (startup banner
     in container stderr confirms install-layout scripts/pseudocron.py was found).
AC2  command: dashboard, same volume → scripts/dashboard/create.sh is reached
     (stub sentinel line in container stderr; full tmux session not required).
AC3  arbitrary passthrough command → entrypoint's *) narration line appears
     in container stderr.

All three checks run against BOTH docker/rhel9 and docker/debian Dockerfiles.
The entrypoint is shared (docker/entrypoint.sh), so both must pass identically.

DOCKER-AVAILABILITY GATE
------------------------
When the docker binary is absent or the daemon is unreachable, every test in
this module is skipped with a narrated reason — never silent pass, never ERROR.

MOUNT CONTRACT SATISFIED IN TESTS
----------------------------------
entrypoint.sh verifies four mounts.  Tests supply all four via docker run -v:
  /pgai_agent_kanban  — install-shaped fixture (tmp_path tree)
  /home/<user>        — empty tmp dir, PGAI_WORKSPACE_MOUNT set explicitly
  /claude             — empty tmp dir
  ~/.claude           — empty tmp dir (HOME set to tmp dir via --env)

PORT PUBLISHING
---------------
No test publishes a host-reachable port.  docker run --rm is used throughout.
DIRECTIVES rule 1 is not violated.

Test naming (SOP.md anti-pattern 6):
Names describe behavior, never bug IDs or scaffolding labels.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
from typing import NamedTuple

import pytest

# ---------------------------------------------------------------------------
# Path resolution — anchored to the test file location.
# test file:  team/tests/unit/test_entrypoint_install_shaped.py
# team/:      team/tests/unit/../../../ = team/  (3 levels up)
# repo root:  team/../                  = repo root
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent


# ---------------------------------------------------------------------------
# Flavor configuration (parametrize both Dockerfiles for AC1-3)
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
        image_tag="pgai-kanban-install-shaped-debian:test",
    ),
    _FlavorConfig(
        name="rhel9",
        dockerfile=_REPO_ROOT / "docker" / "rhel9" / "Dockerfile",
        build_context=_REPO_ROOT,
        image_tag="pgai-kanban-install-shaped-rhel9:test",
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
        f"\n[install-shaped-fixture] SKIP — {reason}",
        flush=True,
    )
    pytest.skip(reason)


# ---------------------------------------------------------------------------
# Module-scoped parametrized fixture: build each flavor once per session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", params=_FLAVORS, ids=_FLAVOR_IDS)
def install_shaped_image_tag(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> str:
    """Build docker/<flavor>/Dockerfile and return the image tag on success.

    Parametrized over all flavor configurations in _FLAVORS so each flavor's
    image is built once and reused by all tests in this module, then removed
    in teardown.

    Skips (with narration) when Docker is unavailable or the Dockerfile is missing.
    """
    flavor: _FlavorConfig = request.param

    if not _docker_available():
        _narrate_skip(
            "docker binary not found or Docker daemon unreachable; "
            "install-shaped fixture tests require a running Docker environment"
        )

    if not flavor.dockerfile.exists():
        pytest.fail(
            f"docker/{flavor.name}/Dockerfile not found at {flavor.dockerfile}; "
            "ensure the file was committed to the repository"
        )

    print(
        f"\n[install-shaped-fixture] [{flavor.name}] Building image "
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
        timeout=600,  # 10 minutes — package downloads on first run (RHEL9 EPEL)
    )

    if result.returncode != 0:
        print(
            f"\n[install-shaped-fixture] [{flavor.name}] BUILD STDOUT:\n"
            + result.stdout,
            flush=True,
        )
        print(
            f"\n[install-shaped-fixture] [{flavor.name}] BUILD STDERR:\n"
            + result.stderr,
            flush=True,
        )
        pytest.fail(
            f"docker build [{flavor.name}] exited {result.returncode}; "
            "see build output above"
        )

    print(
        f"[install-shaped-fixture] [{flavor.name}] Image built: {flavor.image_tag}",
        flush=True,
    )

    yield flavor.image_tag

    # Teardown: remove the test image to avoid accumulating dangling tags.
    subprocess.run(
        ["docker", "image", "rm", "--force", flavor.image_tag],
        capture_output=True,
    )
    print(
        f"[install-shaped-fixture] [{flavor.name}] Removed image tag: {flavor.image_tag}",
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
# Helper: build the four-mount docker run argument list
# ---------------------------------------------------------------------------


def _four_mount_run_args(
    image_tag: str,
    install_root: pathlib.Path,
    workspace_dir: pathlib.Path,
    claude_payload_dir: pathlib.Path,
    claude_config_dir: pathlib.Path,
) -> list[str]:
    """Build a docker run argv that satisfies the four-mount contract.

    The docker run command uses the install-shaped entrypoint script at
    /pgai_agent_kanban/scripts/entrypoint.sh via the --entrypoint flag.
    This is necessary because the Dockerfiles set CMD but not ENTRYPOINT;
    the entrypoint script is provided via the bind-mounted install volume.

    All mounts use absolute paths inside tmp_path — never the live install or
    dev tree (Constraint: self-contained under tmp_path).

    Args:
        image_tag:          Image to run.
        install_root:       Install-shaped kanban root (→ /pgai_agent_kanban).
        workspace_dir:      Empty dir for PGAI_WORKSPACE_MOUNT (→ container path).
        claude_payload_dir: Empty dir for /claude.
        claude_config_dir:  Empty dir for ~/.claude (HOME set to parent).

    Returns:
        list[str] — partial argv for subprocess.run starting with ["docker", "run"],
        ending with the image_tag.  The caller appends CMD arguments after the
        image tag.
    """
    # The container's HOME must point at the parent of .claude so that
    # entrypoint.sh resolves ${HOME:-/root}/.claude to the bind-mounted dir.
    container_home = "/home/kanban-test"
    claude_config_container_path = f"{container_home}/.claude"

    argv = [
        "docker", "run",
        "--rm",
        # Use the entrypoint script from the install-shaped volume.
        # The Dockerfiles set CMD but not ENTRYPOINT; we invoke the entrypoint
        # explicitly via --entrypoint so the tests exercise entrypoint.sh dispatch.
        "--entrypoint", "/pgai_agent_kanban/scripts/entrypoint.sh",
        # Mount 1: /pgai_agent_kanban — install-shaped kanban root
        "--volume", f"{install_root}:/pgai_agent_kanban",
        # Mount 2: /home/<user> — workspace (explicit-path mode via PGAI_WORKSPACE_MOUNT)
        "--volume", f"{workspace_dir}:{container_home}/workspace",
        # Mount 3: /claude — site-specific payload directory
        "--volume", f"{claude_payload_dir}:/claude",
        # Mount 4: ~/.claude — agent CLI config (HOME controls the ~ resolution)
        "--volume", f"{claude_config_dir}:{claude_config_container_path}",
        # Set HOME so ~/.claude resolves to our bind-mounted dir
        "--env", f"HOME={container_home}",
        # Set PGAI_WORKSPACE_MOUNT to the explicit container-side workspace path
        # so entrypoint uses explicit-path mode (no glob over /home/*/).
        "--env", f"PGAI_WORKSPACE_MOUNT={container_home}/workspace",
        # TERM must be set for tput-using scripts inside the container.
        "--env", "TERM=xterm-256color",
        # No published ports — DIRECTIVES rule 1.
        image_tag,
    ]

    return argv


# ---------------------------------------------------------------------------
# Helper: build the ancillary mount directories under tmp_path
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
# AC1 — Default-CMD startup (no command: line) reaches pseudocron startup
# ---------------------------------------------------------------------------


def test_default_mode_reaches_pseudocron_startup(
    install_shaped_image_tag: str,
    install_shaped_root: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Default-CMD container startup with install-shaped volume reaches pseudocron.

    AC1 (BUG-0088): compose up with NO command: line → pseudocron reaches its
    first scheduled-wake barrier.

    Evidence: the pseudocron startup banner ("pseudocron starting:") appears in
    container stderr, confirming:
      - entrypoint dispatched to pseudocron mode (no stray CMD override)
      - PGAI_AGENT_KANBAN_ROOT_PATH was set to /pgai_agent_kanban
      - scripts/pseudocron.py was found at the INSTALL-layout path
      - pseudocron.cfg was found and parsed without error
      - the install-shaped volume has no team/ prefix to fall back on

    The test runs docker with a short timeout: the startup banner emits before
    pseudocron enters its first sleep, so 30 seconds is more than sufficient.
    """
    workspace, claude_payload, claude_config = _make_ancillary_mounts(tmp_path)

    # Build argv: no extra CMD → entrypoint default dispatches to pseudocron.
    argv = _four_mount_run_args(
        install_shaped_image_tag,
        install_shaped_root,
        workspace,
        claude_payload,
        claude_config,
    )

    print(
        f"\n[install-shaped-fixture] AC1 test: running {argv[-1]} with "
        "no CMD arg (default pseudocron dispatch); expect pseudocron startup banner",
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
        # Timeout is acceptable: pseudocron entered its sleep loop, which means
        # it started successfully.  Collect what we have.
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
    else:
        stdout = result.stdout
        stderr = result.stderr

    combined = stdout + stderr
    print(
        f"[install-shaped-fixture] AC1 container output (combined):\n{combined}",
        flush=True,
    )

    # Assert the entrypoint dispatched to pseudocron mode.
    assert "entrypoint: starting pseudocron" in combined, (
        "entrypoint did not dispatch to pseudocron mode; "
        "expected 'entrypoint: starting pseudocron' in combined output.\n\n"
        f"Full combined output:\n{combined}"
    )

    # Assert pseudocron found and parsed its config file at the INSTALL-layout path.
    # This is the critical check: it fails if pseudocron.py is not at scripts/pseudocron.py
    # or if PGAI_AGENT_KANBAN_ROOT_PATH was not set to /pgai_agent_kanban.
    assert "pseudocron starting:" in combined, (
        "pseudocron startup banner not found in container output; "
        "expected 'pseudocron starting:' (confirms scripts/pseudocron.py "
        "was found at the install-layout path and pseudocron.cfg was parsed).\n\n"
        "This may mean:\n"
        "  - The entrypoint used a dev-tree path (team/scripts/...) not present "
        "in the install-shaped fixture\n"
        "  - PGAI_AGENT_KANBAN_ROOT_PATH was not set to /pgai_agent_kanban\n"
        "  - pseudocron.cfg was missing from the fixture root\n\n"
        f"Full combined output:\n{combined}"
    )


# ---------------------------------------------------------------------------
# AC2 — command: dashboard reaches create.sh (stub sentinel)
# ---------------------------------------------------------------------------


def test_dashboard_mode_reaches_create_sh(
    install_shaped_image_tag: str,
    install_shaped_root: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """command: dashboard → scripts/dashboard/create.sh stub sentinel appears.

    AC2 (BUG-0088): compose up with command: dashboard → create.sh is reached.

    Evidence: the stub create.sh in the install-shaped fixture emits
    "install-fixture: dashboard create.sh stub reached" to stderr.  This
    confirms the entrypoint dispatched to the dashboard path AND found
    scripts/dashboard/create.sh at the install-layout path.

    The entrypoint's own narration line ("entrypoint: starting dashboard")
    is also asserted as a secondary signal.
    """
    workspace, claude_payload, claude_config = _make_ancillary_mounts(tmp_path)

    # Pass "dashboard" as the entrypoint argument to activate dashboard mode.
    argv = _four_mount_run_args(
        install_shaped_image_tag,
        install_shaped_root,
        workspace,
        claude_payload,
        claude_config,
    )
    argv.append("dashboard")

    print(
        f"\n[install-shaped-fixture] AC2 test: running {argv[-2]} with arg=dashboard; "
        "expect create.sh stub sentinel",
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
        f"[install-shaped-fixture] AC2 container output (combined):\n{combined}",
        flush=True,
    )

    # Assert the entrypoint dispatched to dashboard mode.
    assert "entrypoint: starting dashboard" in combined, (
        "entrypoint did not dispatch to dashboard mode; "
        "expected 'entrypoint: starting dashboard' in combined output.\n\n"
        f"Full combined output:\n{combined}"
    )

    # Assert the stub create.sh was reached at the install-layout path.
    assert "dashboard create.sh stub reached" in combined, (
        "create.sh stub sentinel not found in container output; "
        "expected 'dashboard create.sh stub reached' (confirms "
        "scripts/dashboard/create.sh was found at the install-layout path).\n\n"
        "This may mean:\n"
        "  - The entrypoint used a dev-tree path (team/scripts/dashboard/create.sh)\n"
        "  - scripts/dashboard/create.sh is missing from the fixture\n\n"
        f"Full combined output:\n{combined}"
    )


# ---------------------------------------------------------------------------
# AC3 — Arbitrary passthrough command → narration line in container stderr
# ---------------------------------------------------------------------------


def test_passthrough_command_emits_narration_line(
    install_shaped_image_tag: str,
    install_shaped_root: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Arbitrary passthrough command → entrypoint *) narration line in stderr.

    AC3 (BUG-0088): compose up with an arbitrary command → the narration line
    the entrypoint prints on the *) branch appears in docker logs verbatim.

    Passing "echo" as the command exercises the *) passthrough case; the
    entrypoint should log "entrypoint: passthrough exec: echo" to stderr before
    exec'ing the command.
    """
    workspace, claude_payload, claude_config = _make_ancillary_mounts(tmp_path)

    # Pass an arbitrary command to exercise the *) passthrough branch.
    # "echo" is a safe, always-available command that exits 0.
    # The entrypoint's *) branch narrates the exec before forwarding.
    argv = _four_mount_run_args(
        install_shaped_image_tag,
        install_shaped_root,
        workspace,
        claude_payload,
        claude_config,
    )
    argv.extend(["echo", "pgai-passthrough-test"])

    print(
        f"\n[install-shaped-fixture] AC3 test: running passthrough (echo); "
        "expect entrypoint narration line",
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
        f"[install-shaped-fixture] AC3 container output (combined):\n{combined}",
        flush=True,
    )

    assert result.returncode == 0, (
        f"container exited {result.returncode}; expected 0 for passthrough 'echo'\n\n"
        f"Full combined output:\n{combined}"
    )

    # Assert the *) narration line that the entrypoint prints before exec'ing.
    # This confirms BUG-0088 defect 1 fix: the *) branch now narrates what it
    # is about to exec (previously it was a silent path).
    assert "entrypoint: passthrough exec:" in combined, (
        "entrypoint passthrough narration line not found; "
        "expected 'entrypoint: passthrough exec:' in container output.\n\n"
        "This either means the entrypoint did not reach the *) branch, "
        "or the narration line was removed.\n\n"
        f"Full combined output:\n{combined}"
    )

    # The echoed payload should also appear in stdout.
    assert "pgai-passthrough-test" in combined, (
        "echo output 'pgai-passthrough-test' not found in container output; "
        "the passthrough exec did not complete.\n\n"
        f"Full combined output:\n{combined}"
    )


# ---------------------------------------------------------------------------
# Non-parametrized structural tests (run once per flavor, no docker required)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flavor", _FLAVORS, ids=_FLAVOR_IDS)
def test_dockerfile_exists_for_flavor(flavor: _FlavorConfig) -> None:
    """Each flavor's Dockerfile must exist in the shipped tree.

    This check requires no docker daemon — it only inspects the repository.
    """
    assert flavor.dockerfile.exists(), (
        f"docker/{flavor.name}/Dockerfile not found at {flavor.dockerfile}; "
        "this file must be committed to the repository before docker-gated "
        "tests can build it"
    )


def test_install_shaped_fixture_has_no_team_directory(
    tmp_path: pathlib.Path,
) -> None:
    """install-shaped fixture root must not contain a top-level team/ directory.

    The critical constraint for BUG-0088 regression prevention: only
    INSTALL-layout paths must resolve.  If team/ is present, a future
    entrypoint regression to dev-tree paths would go undetected.
    """
    from tests.fixtures.install_shaped_kanban_root import (
        build_install_shaped_kanban_root,
    )
    root = build_install_shaped_kanban_root(tmp_path)

    assert not (root / "team").exists(), (
        f"install-shaped fixture root {root} contains a 'team/' directory; "
        "the fixture must NOT contain team/ — only install-layout paths "
        "(scripts/...) must be present"
    )


def test_install_shaped_fixture_has_pseudocron_at_install_path(
    tmp_path: pathlib.Path,
) -> None:
    """install-shaped fixture must have pseudocron.py at scripts/pseudocron.py.

    Verifies the install-layout path the entrypoint dispatches to.
    """
    from tests.fixtures.install_shaped_kanban_root import (
        build_install_shaped_kanban_root,
    )
    root = build_install_shaped_kanban_root(tmp_path)

    install_path = root / "scripts" / "pseudocron.py"
    assert install_path.exists(), (
        f"scripts/pseudocron.py not found at {install_path}; "
        "the install-shaped fixture must populate this path"
    )
    # Dev-tree path must NOT exist.
    dev_path = root / "team" / "scripts" / "pseudocron.py"
    assert not dev_path.exists(), (
        f"dev-tree path team/scripts/pseudocron.py exists at {dev_path}; "
        "the fixture must not contain team/ (install-only shape)"
    )


def test_install_shaped_fixture_has_create_sh_at_install_path(
    tmp_path: pathlib.Path,
) -> None:
    """install-shaped fixture must have create.sh at scripts/dashboard/create.sh.

    Verifies the install-layout path the entrypoint dispatches to for dashboard mode.
    """
    from tests.fixtures.install_shaped_kanban_root import (
        build_install_shaped_kanban_root,
    )
    root = build_install_shaped_kanban_root(tmp_path)

    install_path = root / "scripts" / "dashboard" / "create.sh"
    assert install_path.exists(), (
        f"scripts/dashboard/create.sh not found at {install_path}; "
        "the install-shaped fixture must populate this path"
    )
    # Dev-tree path must NOT exist.
    dev_path = root / "team" / "scripts" / "dashboard" / "create.sh"
    assert not dev_path.exists(), (
        f"dev-tree path team/scripts/dashboard/create.sh exists at {dev_path}; "
        "the fixture must not contain team/ (install-only shape)"
    )


def test_install_shaped_fixture_has_entrypoint_at_install_path(
    tmp_path: pathlib.Path,
) -> None:
    """install-shaped fixture must have entrypoint.sh at scripts/entrypoint.sh.

    Verifies the install-layout path that docker-gated tests invoke via
    docker run --entrypoint /pgai_agent_kanban/scripts/entrypoint.sh.
    """
    from tests.fixtures.install_shaped_kanban_root import (
        build_install_shaped_kanban_root,
    )
    root = build_install_shaped_kanban_root(tmp_path)

    install_path = root / "scripts" / "entrypoint.sh"
    assert install_path.exists(), (
        f"scripts/entrypoint.sh not found at {install_path}; "
        "the install-shaped fixture must populate this path "
        "(copied from docker/entrypoint.sh)"
    )
    # Dev-tree path must NOT exist.
    dev_path = root / "docker" / "entrypoint.sh"
    assert not dev_path.exists(), (
        f"dev-tree path docker/entrypoint.sh exists at {dev_path}; "
        "the fixture must not contain docker/ (install-only shape)"
    )


def test_install_shaped_fixture_has_pseudocron_cfg(
    tmp_path: pathlib.Path,
) -> None:
    """install-shaped fixture must have pseudocron.cfg at the root.

    pseudocron.py reads pseudocron.cfg from PGAI_AGENT_KANBAN_ROOT_PATH at startup.
    """
    from tests.fixtures.install_shaped_kanban_root import (
        build_install_shaped_kanban_root,
    )
    root = build_install_shaped_kanban_root(tmp_path)

    cfg_path = root / "pseudocron.cfg"
    assert cfg_path.exists(), (
        f"pseudocron.cfg not found at {cfg_path}; "
        "pseudocron.py requires this file at the kanban root"
    )
    content = cfg_path.read_text(encoding="utf-8")
    assert content.strip(), f"pseudocron.cfg at {cfg_path} is empty"
