"""
test_docker_build_fixture.py
============================
Build fixture for per-flavor Dockerfiles (debian and rhel9).

Exercises `docker build` against each Dockerfile under docker/<flavor>/.
The test is parametrized so both flavors run the same assertions — no
duplicate test logic.

Docker-availability gate
------------------------
When docker is available (daemon reachable): the build runs; the test asserts
a successful exit and narrates the result.  When docker is not available
(binary missing or daemon unreachable): the test emits an explicit narrated
skip — never a silent pass.

Why a narrated skip instead of a pytest.skip?
----------------------------------------------
A silent skip would be indistinguishable from "test doesn't exist" when
reading a CI log quickly.  The narrated skip prints a reason to stdout so
the operator can confirm the fixture was encountered, evaluated the docker
check, and consciously skipped — not accidentally excluded.

When this fixture produces a meaningful signal
----------------------------------------------
The fixture is meaningful in two contexts:
  1. A developer workstation or CI runner with Docker available — the build
     runs and the test is a real gate.
  2. Any environment without Docker — the narrated skip confirms the fixture
     reached that branch; CI configured without Docker deliberately accepts
     this skip.

Package and tmux version verification
---------------------------------------
After a successful image build, the test also verifies:
  - The tmux version guard fires correctly for a correctly-built image.
  - tput and watch are present (spot-check two non-trivial packages).
These checks use `docker run --rm <image> <cmd>` on the built image tag.
They are skipped when the build itself was skipped.

Parametrization
---------------
Both the debian and rhel9 flavor Dockerfiles are tested.  The tests run
identically for each flavor; only the Dockerfile path and image tag differ.
This satisfies the requirement to parametrize rather than duplicate.

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
# test file:  team/tests/unit/test_docker_build_fixture.py
# team/:      team/tests/unit/../../../ = team/ (3 levels up)
# repo root:  team/../                  = repo root
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent


# ---------------------------------------------------------------------------
# Flavor configuration
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
        image_tag="pgai-kanban-build-fixture-debian:test",
    ),
    _FlavorConfig(
        name="rhel9",
        dockerfile=_REPO_ROOT / "docker" / "rhel9" / "Dockerfile",
        build_context=_REPO_ROOT,
        image_tag="pgai-kanban-build-fixture-rhel9:test",
    ),
]

# pytest parametrize IDs match the flavor names for readable test output.
_FLAVOR_IDS = [f.name for f in _FLAVORS]


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
        f"\n[docker-build-fixture] SKIP — {reason}",
        flush=True,
    )
    pytest.skip(reason)


# ---------------------------------------------------------------------------
# Module-scoped parametrized fixture: build each flavor once
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", params=_FLAVORS, ids=_FLAVOR_IDS)
def built_image_tag(request: pytest.FixtureRequest) -> str | None:
    """
    Build docker/<flavor>/Dockerfile and return the image tag on success.

    Parametrized over all flavor configurations in _FLAVORS.
    Module-scoped so each flavor's image is built once and reused by all
    tests in this module, then removed in teardown.

    Yields None (and narrates a skip) when Docker is not available.
    """
    flavor: _FlavorConfig = request.param

    if not _docker_available():
        _narrate_skip(
            "docker binary not found or Docker daemon unreachable; "
            "build fixture requires a running Docker environment"
        )
    if not flavor.dockerfile.exists():
        pytest.fail(
            f"docker/{flavor.name}/Dockerfile not found at {flavor.dockerfile}; "
            "ensure the file was committed to the repository"
        )

    print(
        f"\n[docker-build-fixture] [{flavor.name}] Building image from {flavor.dockerfile} ...",
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
        timeout=600,  # 10 minutes — allow for package downloads on first run (RHEL9 EPEL)
    )

    if result.returncode != 0:
        print(
            f"\n[docker-build-fixture] [{flavor.name}] BUILD STDOUT:\n" + result.stdout,
            flush=True,
        )
        print(
            f"\n[docker-build-fixture] [{flavor.name}] BUILD STDERR:\n" + result.stderr,
            flush=True,
        )
        pytest.fail(
            f"docker build [{flavor.name}] exited {result.returncode}; "
            "see build output above"
        )

    print(
        f"[docker-build-fixture] [{flavor.name}] Image built successfully: {flavor.image_tag}",
        flush=True,
    )

    yield flavor.image_tag

    # Teardown: remove the test image to avoid accumulating dangling tags.
    subprocess.run(
        ["docker", "image", "rm", "--force", flavor.image_tag],
        capture_output=True,
    )
    print(
        f"[docker-build-fixture] [{flavor.name}] Removed image tag: {flavor.image_tag}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Non-parametrized file-existence tests (run once per flavor via direct param)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flavor", _FLAVORS, ids=_FLAVOR_IDS)
def test_dockerfile_exists(flavor: _FlavorConfig) -> None:
    """Each flavor's Dockerfile must exist in the shipped tree."""
    assert flavor.dockerfile.exists(), (
        f"docker/{flavor.name}/Dockerfile not found at {flavor.dockerfile}. "
        "This file must be committed to the repository."
    )


# ---------------------------------------------------------------------------
# Tests parametrized via the built_image_tag fixture
# ---------------------------------------------------------------------------


def test_image_builds_without_error(built_image_tag: str) -> None:
    """
    Docker image builds successfully from the flavor's Dockerfile.

    Acceptance criterion: image builds from the shipped Dockerfile
    (the built_image_tag fixture performs the build; this test verifies
    the fixture completed without error by asserting the tag exists).
    """
    result = subprocess.run(
        ["docker", "image", "inspect", built_image_tag],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"Built image tag '{built_image_tag}' not found after build; "
        f"inspect stderr: {result.stderr}"
    )


def test_tmux_version_meets_floor_in_built_image(built_image_tag: str) -> None:
    """
    tmux inside the built image meets the >= 3.1 floor.

    The tmux version guard RUN layer in each Dockerfile fails the build if
    tmux regresses below 3.1.  Here we confirm tmux reports >= 3.1 at
    runtime inside the container for each flavor.

    --entrypoint "" bypasses the baked entrypoint's four-mount verification so
    this sanity check can run without the full production mount contract.
    The entrypoint's own contract is covered end-to-end by test_e2e_shipped_image.py.
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "", built_image_tag, "tmux", "-V"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"tmux -V inside built image failed (rc={result.returncode}): {result.stderr}"
    )
    version_line = result.stdout.strip()
    # Expected: "tmux 3.3a" or "tmux 3.2a" — parse the version token
    parts = version_line.split()
    assert len(parts) >= 2 and parts[0].lower() == "tmux", (
        f"Unexpected tmux -V output: {version_line!r}"
    )
    version_str = parts[1]
    import re
    match = re.match(r"(\d+)\.(\d+)", version_str)
    assert match is not None, f"Could not parse version from {version_str!r}"
    major, minor = int(match.group(1)), int(match.group(2))
    assert (major, minor) >= (3, 1), (
        f"tmux {version_str} inside built image is below required floor 3.1"
    )


def test_tput_available_in_built_image(built_image_tag: str) -> None:
    """
    tput is present and functional inside the built image.

    Spot-check that the ncurses package was installed correctly.
    tput is critical for the visibility-window cursor positioning loops
    in the dashboard (create.sh pane commands use tput cup/el/ed).

    --entrypoint "" bypasses the baked entrypoint's four-mount verification so
    this sanity check can run without the full production mount contract.
    The entrypoint's own contract is covered end-to-end by test_e2e_shipped_image.py.
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "", "-e", "TERM=xterm-256color",
         built_image_tag, "tput", "cols"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"tput cols failed inside built image (rc={result.returncode}): "
        f"{result.stderr.strip()}"
    )


def test_watch_available_in_built_image(built_image_tag: str) -> None:
    """
    watch is present inside the built image.

    Spot-check that the procps/procps-ng package was installed correctly.
    watch is required by all dashboard pane refresh loops.

    --entrypoint "" bypasses the baked entrypoint's four-mount verification so
    this sanity check can run without the full production mount contract.
    The entrypoint's own contract is covered end-to-end by test_e2e_shipped_image.py.
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "", built_image_tag, "watch", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"watch --version failed inside built image (rc={result.returncode}): "
        f"{result.stderr.strip()}"
    )


def test_git_available_in_built_image(built_image_tag: str) -> None:
    """
    git is present inside the built image.

    git is required by the kanban chain for worktree operations and by
    dashboard git-status.sh / git-recent-tags.sh.

    --entrypoint "" bypasses the baked entrypoint's four-mount verification so
    this sanity check can run without the full production mount contract.
    The entrypoint's own contract is covered end-to-end by test_e2e_shipped_image.py.
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "", built_image_tag, "git", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"git --version failed inside built image (rc={result.returncode}): "
        f"{result.stderr.strip()}"
    )


def test_python3_available_in_built_image(built_image_tag: str) -> None:
    """
    python3 is present and is >= 3.12 inside the built image.

    python3 is required by the pm-agent chain, column-render.sh, and
    the attention scanner.

    --entrypoint "" bypasses the baked entrypoint's four-mount verification so
    this sanity check can run without the full production mount contract.
    The entrypoint's own contract is covered end-to-end by test_e2e_shipped_image.py.
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "", built_image_tag,
         "python3", "-c", "import sys; print(sys.version_info[:2])"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"python3 check failed inside built image (rc={result.returncode}): "
        f"{result.stderr.strip()}"
    )
    output = result.stdout.strip()
    import ast
    version_tuple = ast.literal_eval(output)
    assert version_tuple >= (3, 12), (
        f"python3 inside built image reports {version_tuple}; "
        f"expected >= (3, 12)"
    )
