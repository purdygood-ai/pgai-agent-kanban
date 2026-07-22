"""
test_dashboard_smoke_container.py
==================================
In-container dashboard smoke harness — parametrized over both image flavors.

Runs team/scripts/dashboard/create.sh --no-tmux inside each built flavor
image and asserts that no "command not found" errors appear in the output.
This exercises every pane's external commands through their real execution
paths, confirming the dependency audit in each Dockerfile is complete.

Parametrization
---------------
The smoke test is parametrized to run against BOTH flavor images (debian and
rhel9).  A single set of test functions exercises all flavors — no duplication.

Docker-availability gate
------------------------
This test is gated behind a docker-available check.  When Docker is not
present or the daemon is unreachable, the test emits a narrated skip — never
a silent pass.  This ensures:

  - On docker-capable runners: the test is a real gate (build + smoke run).
  - On docker-absent runners: the narrated skip confirms the fixture was
    encountered and consciously skipped, not accidentally excluded.

How the smoke works
-------------------
create.sh --no-tmux produces one-shot output by running each dashboard
section's sub-scripts directly (show-multi.sh, attention.sh, column-render.sh,
etc.) and printing their output to stdout.  The smoke harness:

  1. Builds the flavor image from docker/<flavor>/Dockerfile (or reuses the
     existing build fixture image if present in this module scope).
  2. Runs: docker run --rm <image> bash -c "create.sh --no-tmux ..."
     inside a minimal kanban root (so path lookups resolve but no real data
     is required).
  3. Checks that neither stdout nor stderr contains the string
     "command not found", which is how bash reports a missing external command.

The check is intentionally narrow: we look for the specific phrase bash
produces on a missing command.  Other errors (missing config, empty data)
are tolerated — they do not indicate a missing binary.

Minimal kanban root
-------------------
create.sh requires PGAI_AGENT_KANBAN_ROOT_PATH to exist.  We bind-mount
the kanban source tree (the dev tree root) read-only at /pgai_agent_kanban
so that all scripts under team/scripts/ are reachable without needing a full
installed kanban.  A minimal projects.cfg is written to a scratch temp
directory and bind-mounted over the default path.

Compose file tests
------------------
The compose-file tests check per-flavor compose examples at
docker/<flavor>/docker-compose.example.yaml.  These tests run without Docker.

Test naming convention (SOP.md anti-pattern 6):
Names describe behavior, never bug IDs, scaffolding labels, or version strings.
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
# test file:  team/tests/unit/test_dashboard_smoke_container.py
# team/:      team/tests/unit/../../ = team/
# repo root:  team/../                = repo root
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent          # team/
_REPO_ROOT = _TEAM_DIR.parent                        # repo root
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"


# ---------------------------------------------------------------------------
# Flavor configuration (mirrors test_docker_build_fixture.py)
# ---------------------------------------------------------------------------

class _FlavorConfig(NamedTuple):
    """Smoke parameters for one image flavor."""
    name: str
    dockerfile: pathlib.Path
    compose_example: pathlib.Path
    image_tag: str


_FLAVORS: list[_FlavorConfig] = [
    _FlavorConfig(
        name="debian",
        dockerfile=_REPO_ROOT / "docker" / "debian" / "Dockerfile",
        compose_example=_REPO_ROOT / "docker" / "debian" / "docker-compose.example.yaml",
        image_tag="pgai-kanban-smoke-fixture-debian:test",
    ),
    _FlavorConfig(
        name="rhel9",
        dockerfile=_REPO_ROOT / "docker" / "rhel9" / "Dockerfile",
        compose_example=_REPO_ROOT / "docker" / "rhel9" / "docker-compose.example.yaml",
        image_tag="pgai-kanban-smoke-fixture-rhel9:test",
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
    """Print a visible skip notice to stdout before calling pytest.skip."""
    print(
        f"\n[dashboard-smoke-fixture] SKIP — {reason}",
        flush=True,
    )
    pytest.skip(reason)


# ---------------------------------------------------------------------------
# Module-scoped parametrized fixture: build each flavor once, clean up after
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", params=_FLAVORS, ids=_FLAVOR_IDS)
def smoke_image_tag(request: pytest.FixtureRequest) -> str:
    """
    Build docker/<flavor>/Dockerfile and return the image tag for use in smoke tests.

    Parametrized over all flavor configurations in _FLAVORS.
    Skips (with narration) when Docker is unavailable.
    Builds once per flavor per module; tears down in finally.
    """
    flavor: _FlavorConfig = request.param

    if not _docker_available():
        _narrate_skip(
            "docker binary not found or Docker daemon unreachable; "
            "dashboard smoke fixture requires a running Docker environment"
        )

    if not flavor.dockerfile.exists():
        pytest.fail(
            f"docker/{flavor.name}/Dockerfile not found at {flavor.dockerfile}; "
            "ensure the file was committed to the repository"
        )

    print(
        f"\n[dashboard-smoke-fixture] [{flavor.name}] Building image from {flavor.dockerfile} ...",
        flush=True,
    )
    build_result = subprocess.run(
        [
            "docker",
            "build",
            "--tag",
            flavor.image_tag,
            "--file",
            str(flavor.dockerfile),
            str(_REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=600,  # 10 minutes — package downloads on first run (RHEL9 EPEL)
    )

    if build_result.returncode != 0:
        print(
            f"\n[dashboard-smoke-fixture] [{flavor.name}] BUILD STDOUT:\n"
            + build_result.stdout,
            flush=True,
        )
        print(
            f"\n[dashboard-smoke-fixture] [{flavor.name}] BUILD STDERR:\n"
            + build_result.stderr,
            flush=True,
        )
        pytest.fail(
            f"docker build [{flavor.name}] exited {build_result.returncode}; "
            "see output above"
        )

    print(
        f"[dashboard-smoke-fixture] [{flavor.name}] Image built: {flavor.image_tag}",
        flush=True,
    )

    try:
        yield flavor.image_tag
    finally:
        subprocess.run(
            ["docker", "image", "rm", "--force", flavor.image_tag],
            capture_output=True,
        )
        print(
            f"[dashboard-smoke-fixture] [{flavor.name}] Removed image tag: {flavor.image_tag}",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Helper: run create.sh --no-tmux inside the container
# ---------------------------------------------------------------------------


def _run_create_sh_no_tmux(image_tag: str) -> subprocess.CompletedProcess:
    """
    Run create.sh --no-tmux inside a container built from the given image tag.

    The kanban source tree is bind-mounted read-only at /pgai_agent_kanban
    (the container's expected mount path) so that all scripts under team/scripts/
    are reachable.  The dashboard scripts tolerate an empty or missing
    projects.cfg via their internal || true guards, so no separate projects.cfg
    mount is required.

    Returns the CompletedProcess (stdout + stderr captured as text).
    """
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            # Bind-mount the repo root read-only as the kanban install path.
            "--volume",
            f"{_REPO_ROOT}:/pgai_agent_kanban:ro",
            # Set the kanban root env var so create.sh uses the mount.
            "--env",
            "PGAI_AGENT_KANBAN_ROOT_PATH=/pgai_agent_kanban",
            # Provide a known TERM so tput does not fail.
            "--env",
            "TERM=xterm-256color",
            image_tag,
            # Run create.sh --no-tmux via bash (the script uses BASH_SOURCE).
            "bash",
            "/pgai_agent_kanban/team/scripts/dashboard/create.sh",
            "--no-tmux",
            "--kanban-root",
            "/pgai_agent_kanban",
        ],
        capture_output=True,
        text=True,
        timeout=120,  # 2 minutes — enough for all pane commands to run
    )
    return result


# ---------------------------------------------------------------------------
# Non-parametrized file-existence tests (run once per flavor via direct param)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flavor", _FLAVORS, ids=_FLAVOR_IDS)
def test_dockerfile_exists(flavor: _FlavorConfig) -> None:
    """Each flavor's Dockerfile must exist in the shipped tree."""
    assert flavor.dockerfile.exists(), (
        f"docker/{flavor.name}/Dockerfile not found at {flavor.dockerfile}; "
        "this file must be committed to the repository"
    )


@pytest.mark.parametrize("flavor", _FLAVORS, ids=_FLAVOR_IDS)
def test_compose_example_yaml_exists(flavor: _FlavorConfig) -> None:
    """Each flavor's docker-compose.example.yaml must exist in the shipped tree."""
    assert flavor.compose_example.exists(), (
        f"docker/{flavor.name}/docker-compose.example.yaml not found at "
        f"{flavor.compose_example}; this file must be committed to the repository"
    )


@pytest.mark.parametrize("flavor", _FLAVORS, ids=_FLAVOR_IDS)
def test_compose_example_has_four_mounts(flavor: _FlavorConfig) -> None:
    """Each flavor's docker-compose.example.yaml references all four canonical mount targets."""
    if not flavor.compose_example.exists():
        pytest.skip(
            f"docker/{flavor.name}/docker-compose.example.yaml not found — see test above"
        )

    content = flavor.compose_example.read_text(encoding="utf-8")
    expected_targets = [
        "/pgai_agent_kanban",
        "/home/",       # partial match: /home/<user> or /home/operator
        "/claude",
        "/.claude",     # partial match: /home/kanban/.claude or /root/.claude
    ]
    for target in expected_targets:
        assert target in content, (
            f"Mount target {target!r} not found in "
            f"docker/{flavor.name}/docker-compose.example.yaml; "
            f"all four canonical mounts must be present"
        )


@pytest.mark.parametrize("flavor", _FLAVORS, ids=_FLAVOR_IDS)
def test_compose_example_port_is_commented_out(flavor: _FlavorConfig) -> None:
    """Each flavor's docker-compose.example.yaml must not publish a host-reachable port."""
    if not flavor.compose_example.exists():
        pytest.skip(
            f"docker/{flavor.name}/docker-compose.example.yaml not found — see test above"
        )

    content = flavor.compose_example.read_text(encoding="utf-8")
    lines = content.splitlines()
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if "ports:" in stripped:
            continue
        import re
        if re.search(r'^\s*-\s+["\']?\d+:\d+', line) or re.search(
            r'^\s*-\s+["\']?[\d.]+:\d+:\d+', line
        ):
            pytest.fail(
                f"docker/{flavor.name}/docker-compose.example.yaml contains an "
                f"uncommented port mapping: {line.strip()!r}\n"
                "Port mappings must be commented out by default."
            )


@pytest.mark.parametrize("flavor", _FLAVORS, ids=_FLAVOR_IDS)
def test_compose_example_dockerfile_line_matches_flavor(flavor: _FlavorConfig) -> None:
    """Each flavor's compose example must reference that flavor's Dockerfile."""
    if not flavor.compose_example.exists():
        pytest.skip(
            f"docker/{flavor.name}/docker-compose.example.yaml not found — see test above"
        )

    content = flavor.compose_example.read_text(encoding="utf-8")
    expected_dockerfile_ref = f"docker/{flavor.name}/Dockerfile"
    assert expected_dockerfile_ref in content, (
        f"docker/{flavor.name}/docker-compose.example.yaml does not reference "
        f"'{expected_dockerfile_ref}' in the dockerfile: line; "
        f"compose examples must differ only in the dockerfile: line"
    )


# ---------------------------------------------------------------------------
# Docker-gated smoke tests — parametrized via smoke_image_tag fixture
# ---------------------------------------------------------------------------


def test_create_sh_no_tmux_produces_no_command_not_found(
    smoke_image_tag: str,
) -> None:
    """
    create.sh --no-tmux inside the built flavor image produces no 'command not found' output.

    This is the in-container dashboard smoke: it confirms that every external
    command invoked by the dashboard scripts (tmux, tput, watch, git, python3,
    awk, jq, etc.) is present and resolvable inside the image.  A single
    'command not found' line means the dependency audit in that flavor's
    Dockerfile is incomplete.
    """
    result = _run_create_sh_no_tmux(smoke_image_tag)

    combined = result.stdout + result.stderr
    print(
        f"\n[dashboard-smoke-fixture] create.sh --no-tmux exit code: {result.returncode}",
        flush=True,
    )

    assert "command not found" not in combined, (
        "create.sh --no-tmux produced 'command not found' output inside the "
        "container — the Dockerfile dependency audit is incomplete.\n\n"
        "Relevant lines from combined output:\n"
        + "\n".join(
            line
            for line in combined.splitlines()
            if "command not found" in line
        )
    )


def test_create_sh_script_exists_in_repo() -> None:
    """team/scripts/dashboard/create.sh must exist in the shipped tree."""
    assert _CREATE_SH.exists(), (
        f"team/scripts/dashboard/create.sh not found at {_CREATE_SH}; "
        "this file must be committed to the repository"
    )
