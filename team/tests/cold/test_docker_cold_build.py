"""
test_docker_cold_build.py
==========================
Cold-build lane: builds every image flavor declared in
team/config/container-images.txt (docker/<flavor>/Dockerfile), under every container engine present on the host (docker and podman
at minimum), with no layer cache and a forced base-image re-pull.

This module is NOT collected by run-unit-tests.sh or run-integration-tests.sh
-- it lives under team/tests/cold/, a directory neither gated runner's
pytest argv reaches. It runs only via team/scripts/run-cold-build-tests.sh,
the dedicated lane documented as the single command for this sweep. Building
every shipped Dockerfile cold, on every available engine, is minutes of
work per flavor; that cost does not belong in the fast loop every CODER
runs on every ticket.

Flavor list
------------
Flavors come from the declared manifest team/config/container-images.txt
(read by tests.fixtures.container_images.load_container_images; see
_discover_flavors), never from scanning docker/. A directory under docker/
that the manifest does not name is never built, so a stale experiment left
there cannot join the sweep; a new supported flavor joins by adding its
name to the manifest.

Engine discovery
-----------------
Both "docker" and "podman" are checked for availability (binary on PATH AND
the engine responding to `<engine> info`) independently. A host missing one
engine still runs the full sweep under the other -- the fixture narrate-skips
only the (engine, flavor) pairs for the absent engine, never the whole lane.

Cold build
-----------
Every build invocation passes --no-cache (rebuild every layer; no reuse of
a previous build's cache) and --pull (always re-check/re-pull the base
image reference from its registry, rather than trusting whatever is already
in the local image store). A build that only passes on a machine that
happens to already hold the right cached layers is exactly the gap this
lane closes.

Environmental gap vs. Dockerfile defect
-----------------------------------------
Every discovered flavor's base image(s) are pulled standalone via
`<engine> pull <image_ref>` BEFORE the real build starts (_environmental_build_gap).
A probe failure (registry unreachable) is what narrate-skips -- never a bare
non-zero build exit. Once a build starts, reachability of every base image
is already confirmed, so any non-zero exit from that point on is attributed
to the Dockerfile and fails the suite loud, naming the flavor, the engine,
and the Dockerfile step that failed. This discrimination is deliberately
uniform across every flavor -- a second, hand-maintained "which flavors
need probing" list beside the manifest would be two lists to keep in sync;
probing everyone once is one invariant.

Pinned CLI versions
---------------------
Every real flavor build this lane performs (the cold_built_image fixture)
passes CLAUDE_CODE_VERSION and CODEX_VERSION as --build-arg, resolved from
docker/cli-versions.env via team/tests/fixtures/docker_cli_pins.py
(cli_pins_build_args) -- the single source of truth for those two versions.
No CLI version literal is hardcoded anywhere in this module.

Each docker/<flavor>/Dockerfile carries a matching ARG guard with no
default that refuses an unpinned build, and a smoke-check RUN layer,
separate from the install layer above it, that catches a pinned version
whose launcher does not actually run. Two tests prove both refusals are
real, not merely present in the file:

  * test_real_flavor_build_without_pins_fails_naming_the_unset_arg builds a
    real shipped flavor with no --build-arg at all and asserts the build
    fails naming the unset ARG.
  * test_real_flavor_bad_codex_version_fails_the_smoke_check_layer_naming_codex
    builds a synthetic stand-in whose install/smoke-check RUN split is
    extracted VERBATIM from a real shipped flavor's Dockerfile (rather than
    duplicated by hand, as the earlier synthetic-Dockerfile fixture below
    does), so the assertion is coupled to the layer split the shipped file
    actually has today, not to an author's memory of it.

Test naming (roles/TESTER.md anti-pattern 6):
Names describe behavior, never bug IDs, task IDs, or scaffolding labels.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
from typing import NamedTuple

import pytest

from tests.fixtures.container_images import load_container_images, load_selected_container_images
from tests.fixtures.docker_cli_pins import cli_pins_build_args, load_cli_pins

# ---------------------------------------------------------------------------
# Path resolution — anchored to the test file location.
# test file:  team/tests/cold/test_docker_cold_build.py
# team/:      team/tests/cold/../../ = team/  (2 levels up)
# repo root:  team/../               = repo root
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent


# ---------------------------------------------------------------------------
# Flavor configuration
# ---------------------------------------------------------------------------


class _FlavorConfig(NamedTuple):
    """Build parameters for one image flavor."""
    name: str
    dockerfile: pathlib.Path
    build_context: pathlib.Path


def _discover_flavors(repo_root: pathlib.Path) -> list[_FlavorConfig]:
    """
    Return one _FlavorConfig per flavor declared in the image manifest.

    The flavor set is team/config/container-images.txt, in manifest order;
    each entry maps to docker/<name>/Dockerfile. Nothing under docker/ that
    the manifest does not name is returned.
    """
    docker_dir = repo_root / "docker"
    return [
        _FlavorConfig(
            name=name,
            dockerfile=docker_dir / name / "Dockerfile",
            build_context=repo_root,
        )
        for name in load_container_images(repo_root)
    ]


_FLAVORS: list[_FlavorConfig] = [
    flavor
    for flavor in _discover_flavors(_REPO_ROOT)
    if flavor.name in load_selected_container_images(_REPO_ROOT)
]


def _flavor_by_name(name: str) -> _FlavorConfig:
    """
    Return the discovered _FlavorConfig for docker/<name>/Dockerfile.

    Used by tests that target one specific real, shipped flavor (rather
    than every discovered flavor) to prove a behavior against an actual
    file in the tree instead of a hand-written stand-in.
    """
    for flavor in _FLAVORS:
        if flavor.name == name:
            return flavor
    pytest.fail(
        f"flavor {name!r} is not declared in team/config/container-images.txt; "
        f"declared flavors: {[f.name for f in _FLAVORS]}"
    )

# Container engines this lane checks for. Every current pgai-agent-kanban
# deployment target runs one of these two; a host lacking one still runs
# the full sweep under the other (see _engine_available / the fixture
# below) -- engine ABSENCE is narrated per engine, never silently dropped
# from lane coverage.
_ENGINES: tuple[str, ...] = ("docker", "podman")

_FROM_LINE = re.compile(r"^\s*FROM\s+(\S+)(?:\s+AS\s+(\S+))?\s*$", re.IGNORECASE)

# Step-line formats emitted by the three builders this lane exercises, in
# the order tried. Each captures the Dockerfile instruction text of a
# logged build step -- ANY instruction (RUN, COPY, ARG, WORKDIR, USER, ...),
# not only RUN: a COPY --chmod parse error or an ARG/ENV typo fails the
# build exactly as loud as a broken RUN, and naming "RUN foo" for a COPY
# failure would misdirect the reader. Dockerfile instruction keywords are
# always upper-case, so the capture group matches on that shape rather than
# enumerating every keyword.
_STEP_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^#\d+\s+\[[^\]]+\]\s+([A-Z]+\b.*)$", re.MULTILINE),  # docker BuildKit
    re.compile(r"^Step\s+\d+/\d+\s+:\s+([A-Z]+\b.*)$", re.MULTILINE),  # docker classic builder
    # podman/buildah: "STEP N/M: ..." for a single-stage build, or
    # "[stage/total] STEP N/M: ..." once a multi-stage Dockerfile is in
    # play — the optional bracketed stage prefix is what a plain
    # "^STEP ..." anchor misses on every multi-stage flavor this lane ships.
    re.compile(r"^(?:\[\d+/\d+\]\s+)?STEP\s+\d+/\d+:\s+([A-Z]+\b.*)$", re.MULTILINE),
)


def _image_tag(engine: str, flavor_name: str, suffix: str = "") -> str:
    """Throwaway tag for one (engine, flavor) build; unique enough to grep and clean up."""
    tail = f"-{suffix}" if suffix else ""
    return f"pgai-kanban-cold-build-{engine}-{flavor_name}{tail}:test"


# ---------------------------------------------------------------------------
# Engine availability
# ---------------------------------------------------------------------------


def _engine_available(engine: str) -> bool:
    """Return True when engine's binary is present AND it responds to `<engine> info`."""
    if shutil.which(engine) is None:
        return False
    result = subprocess.run(
        [engine, "info"],
        capture_output=True,
        timeout=15,
    )
    return result.returncode == 0


def _narrate_skip(reason: str) -> None:
    """Print a visible skip notice to stdout before calling pytest.skip.

    A narrated skip distinguishes "fixture evaluated and consciously
    skipped" from "test accidentally excluded" when reading a sweep log.
    """
    print(f"\n[cold-build] SKIP — {reason}", flush=True)
    pytest.skip(reason)


# ---------------------------------------------------------------------------
# Dockerfile inspection
# ---------------------------------------------------------------------------


def _extract_base_images(dockerfile: pathlib.Path) -> list[str]:
    """
    Return the external image references named by dockerfile's FROM lines.

    Handles multi-stage builds: a later `FROM <alias>` that refers to an
    earlier stage's `AS <alias>` name resolves to a local build stage, not
    an external registry pull, and is excluded from the result.
    """
    stage_aliases: set[str] = set()
    images: list[str] = []
    for line in dockerfile.read_text().splitlines():
        match = _FROM_LINE.match(line)
        if not match:
            continue
        image_ref, alias = match.group(1), match.group(2)
        if image_ref not in stage_aliases:
            images.append(image_ref)
        if alias:
            stage_aliases.add(alias)
    return images


def _extract_failing_step(build_output: str) -> str:
    """
    Best-effort extraction of the Dockerfile step that failed, from raw
    build output text (stdout+stderr combined).

    Tries each of docker's BuildKit step format, docker's classic builder
    step format, and podman/buildah's step format, in that order, and
    returns the LAST matching step line found across all three -- for a
    sequential single-stage RUN chain, the last step line logged before the
    builder aborts is the one that failed. Falls back to the last non-empty
    output line when no step marker matches, so the reader always gets
    something concrete rather than nothing.
    """
    last_match: str | None = None
    for pattern in _STEP_LINE_PATTERNS:
        matches = pattern.findall(build_output)
        if matches:
            last_match = matches[-1].strip()
    if last_match:
        return last_match
    lines = [ln.strip() for ln in build_output.splitlines() if ln.strip()]
    return lines[-1] if lines else "(no build output captured)"


# ---------------------------------------------------------------------------
# Registry-reachability probe (environmental gap discriminator)
# ---------------------------------------------------------------------------


def _probe_base_image_reachable(engine: str, image_ref: str) -> tuple[bool, str]:
    """
    Probe registry reachability for image_ref via a standalone
    `<engine> pull`, independent of any build step. A pull of an image
    already present locally at the current digest returns 0 without a full
    re-download, so re-running this probe against a cached image is cheap.

    Returns (True, "") on success. Returns (False, detail) on failure,
    where detail is the last non-empty output line -- the skip-reason tail
    naming what the probe actually observed.
    """
    result = subprocess.run(
        [engine, "pull", image_ref],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode == 0:
        return True, ""
    lines = [ln.strip() for ln in (result.stderr or result.stdout).splitlines() if ln.strip()]
    detail = lines[-1] if lines else f"{engine} pull exited {result.returncode}"
    return False, detail


def _environmental_build_gap(engine: str, flavor: _FlavorConfig) -> str | None:
    """
    Return a narrate-skip reason when one of flavor's base images is
    unreachable under engine, or None when the build should proceed.

    Discriminates BEFORE the build runs: every external FROM image in the
    Dockerfile is probed for reachability with a standalone pull. A probe
    failure names the specific engine, flavor, and image reference, rather
    than inferring a network cause from a bare non-zero build exit later.
    """
    for image_ref in _extract_base_images(flavor.dockerfile):
        reachable, detail = _probe_base_image_reachable(engine, image_ref)
        if not reachable:
            return (
                f"{flavor.name} base-image reachability probe failed under {engine}: "
                f"`{engine} pull {image_ref}` did not succeed ({detail}); treating this "
                "as an environmental network gap rather than a Dockerfile defect"
            )
    return None


# ---------------------------------------------------------------------------
# Cold build
# ---------------------------------------------------------------------------


def _build_flavor_image(
    engine: str,
    flavor: _FlavorConfig,
    tag: str | None = None,
    build_arg_flags: list[str] | None = None,
) -> str:
    """
    Cold-build flavor's Dockerfile under engine (--no-cache --pull) and
    return the resulting image tag.

    build_arg_flags, when given, is a flat list of alternating
    "--build-arg" / "KEY=value" entries, spliced directly into the engine
    build argv. Defaults to no build-args at all -- callers that need the
    pinned CLI versions pass cli_pins_build_args() explicitly (the
    cold_built_image fixture below does this for every real flavor build);
    the synthetic single-purpose Dockerfiles built by the discriminator
    tests in this module need no default either (they declare no ARGs of
    their own, or exactly the ARG(s) their own test passes explicitly).

    Discriminates an environmental failure from a Dockerfile defect BEFORE
    attempting the build (_environmental_build_gap) and narrate-skips only
    when a base-image probe fails. Once the build starts, every base image
    is already confirmed reachable, so a non-zero build exit is always
    attributed to the Dockerfile and fails the suite loud, naming the
    flavor, the engine, and the failing step.
    """
    if not flavor.dockerfile.exists():
        pytest.fail(
            f"docker/{flavor.name}/Dockerfile not found at {flavor.dockerfile}; "
            "ensure the file was committed to the repository"
        )

    gap_reason = _environmental_build_gap(engine, flavor)
    if gap_reason is not None:
        _narrate_skip(gap_reason)

    image_tag = tag or _image_tag(engine, flavor.name)
    build_arg_flags = build_arg_flags or []
    print(
        f"\n[cold-build] [{engine}] [{flavor.name}] Building (--no-cache --pull) "
        f"from {flavor.dockerfile} ...",
        flush=True,
    )
    result = subprocess.run(
        [
            engine,
            "build",
            "--no-cache",
            "--pull",
            "--tag",
            image_tag,
            "--file",
            str(flavor.dockerfile),
            *build_arg_flags,
            str(flavor.build_context),
        ],
        capture_output=True,
        text=True,
        timeout=1800,  # 30 minutes — cold, no-cache builds re-download and re-install everything
    )

    if result.returncode != 0:
        combined_output = f"{result.stdout}\n{result.stderr}"
        failing_step = _extract_failing_step(combined_output)
        print(
            f"\n[cold-build] [{engine}] [{flavor.name}] BUILD STDOUT:\n{result.stdout}",
            flush=True,
        )
        print(
            f"\n[cold-build] [{engine}] [{flavor.name}] BUILD STDERR:\n{result.stderr}",
            flush=True,
        )
        pytest.fail(
            f"{engine} build of docker/{flavor.name}/Dockerfile failed (exit "
            f"{result.returncode}) at step: {failing_step}; base-image reachability "
            "was confirmed for every FROM in this Dockerfile before the build started, "
            "so this is a Dockerfile defect, not an environmental gap — see build "
            "output above"
        )

    print(
        f"[cold-build] [{engine}] [{flavor.name}] Image built successfully: {image_tag}",
        flush=True,
    )
    return image_tag


# ---------------------------------------------------------------------------
# Module-scoped parametrized fixture: cold-build each (engine, flavor) pair
# ---------------------------------------------------------------------------

_ENGINE_FLAVOR_PAIRS: list[tuple[str, _FlavorConfig]] = [
    (engine, flavor) for engine in _ENGINES for flavor in _FLAVORS
]
_ENGINE_FLAVOR_IDS = [f"{engine}-{flavor.name}" for engine, flavor in _ENGINE_FLAVOR_PAIRS]


@pytest.fixture(scope="module", params=_ENGINE_FLAVOR_PAIRS, ids=_ENGINE_FLAVOR_IDS)
def cold_built_image(request: pytest.FixtureRequest) -> tuple[str, str, str] | None:
    """
    Cold-build one (engine, flavor) pair and yield (engine, flavor_name, image_tag).

    Parametrized over every discovered flavor crossed with every candidate
    engine. Narrate-skips this specific pair (never the whole lane) when
    engine is unavailable on the host, or when the flavor's base-image
    registry is unreachable under engine. Removes the built image in
    teardown so the lane leaves no images behind on success or failure.

    Passes CLAUDE_CODE_VERSION and CODEX_VERSION as --build-arg, resolved
    from docker/cli-versions.env via cli_pins_build_args() -- every
    docker/<flavor>/Dockerfile this lane builds refuses an unpinned build,
    so a real flavor build with no build-args would fail every flavor for
    the wrong reason (see the module docstring's "Pinned CLI versions"
    section).
    """
    engine, flavor = request.param

    if not _engine_available(engine):
        _narrate_skip(
            f"{engine} binary not found or {engine} unreachable; cold-build lane "
            f"requires a working {engine} installation to exercise the "
            f"{flavor.name} flavor under {engine}"
        )

    image_tag = _build_flavor_image(engine, flavor, build_arg_flags=cli_pins_build_args())

    yield engine, flavor.name, image_tag

    subprocess.run([engine, "image", "rm", "--force", image_tag], capture_output=True)
    print(
        f"[cold-build] [{engine}] [{flavor.name}] Removed image tag: {image_tag}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Discovery sanity
# ---------------------------------------------------------------------------


def test_flavor_list_is_the_declared_manifest_and_each_dockerfile_exists() -> None:
    """The sweep builds exactly the manifest's flavors, and each one ships a Dockerfile."""
    flavors = _discover_flavors(_REPO_ROOT)
    assert [f.name for f in flavors] == load_container_images(_REPO_ROOT)
    assert {"debian", "rocky9"} <= {f.name for f in flavors}
    missing = [str(f.dockerfile) for f in flavors if not f.dockerfile.is_file()]
    assert not missing, f"manifest names flavors with no Dockerfile: {missing}"


def test_extract_base_images_returns_each_stage_image(tmp_path: pathlib.Path) -> None:
    """_extract_base_images returns every external image in a multi-stage Dockerfile."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM node:20-bookworm AS nodejs\n"
        "FROM python:3.12-bookworm\n"
        "COPY --from=nodejs /usr/local/bin/node /usr/local/bin/node\n"
    )
    assert _extract_base_images(dockerfile) == ["node:20-bookworm", "python:3.12-bookworm"]


def test_extract_base_images_skips_local_stage_reference(tmp_path: pathlib.Path) -> None:
    """A later FROM referencing an earlier stage's alias is not an external pull."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM debian:12 AS base\n"
        "FROM base\n"
        "RUN echo hi\n"
    )
    assert _extract_base_images(dockerfile) == ["debian:12"]


def test_engine_unavailable_reports_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """_engine_available returns False when the engine binary is missing."""
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    assert _engine_available("docker") is False
    assert _engine_available("podman") is False


def test_engine_absent_skip_reason_names_the_engine(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A skip for an absent engine names that engine, not a generic reason.

    The narration line is captured, not printed: the lane runs with capture
    off, and the deep-verify classifier reads every "[cold-build] SKIP"
    line in its output as an (engine, flavor) pair that was skipped.
    """
    with pytest.raises(pytest.skip.Exception) as exc_info:
        _narrate_skip("podman binary not found or podman unreachable")
    assert "podman" in str(exc_info.value)
    assert "[cold-build] SKIP — podman" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Discriminator behavior — verifies the reachability probe distinguishes an
# environmental network gap from a genuine Dockerfile defect, per engine.
# ---------------------------------------------------------------------------


def _write_broken_dockerfile(tmp_path: pathlib.Path, base_image: str) -> pathlib.Path:
    """Write a Dockerfile whose RUN step cannot succeed against base_image."""
    dockerfile = tmp_path / "Dockerfile.broken"
    dockerfile.write_text(
        f"FROM {base_image}\n"
        "RUN this-command-does-not-exist-and-never-will\n"
    )
    return dockerfile


@pytest.mark.parametrize("engine", _ENGINES)
def test_broken_run_step_fails_loud_on_reachable_base_image(
    tmp_path: pathlib.Path, engine: str
) -> None:
    """
    A Dockerfile whose RUN step cannot succeed fails the lane — not skips —
    when its base image registry is reachable, under every engine.

    Uses a small, widely cached stand-in base image (alpine) rather than a
    real flavor's much larger production base image: the behavior under
    test is the discriminator's decision logic, not a real Dockerfile's own
    build, which is already covered by the cold_built_image fixture against
    the real Dockerfiles.
    """
    if not _engine_available(engine):
        pytest.skip(f"{engine} not available; cannot demonstrate build behavior")

    dockerfile = _write_broken_dockerfile(tmp_path, "alpine:3")
    flavor = _FlavorConfig(
        name="synthetic-broken-run", dockerfile=dockerfile, build_context=tmp_path
    )
    tag = _image_tag(engine, flavor.name, "broken")
    with pytest.raises(pytest.fail.Exception):
        _build_flavor_image(engine, flavor, tag=tag)


@pytest.mark.parametrize("engine", _ENGINES)
def test_unreachable_base_image_skips_naming_the_probe(
    tmp_path: pathlib.Path, engine: str
) -> None:
    """
    An unreachable base image registry skips (does not fail) under every
    engine, and the skip reason names the reachability probe that failed.

    Points the flavor at a nonexistent registry host rather than disabling
    host networking outright, so the test exercises the exact discriminator
    the lane uses without altering the network environment the rest of the
    suite runs in.
    """
    if not _engine_available(engine):
        pytest.skip(f"{engine} not available; cannot demonstrate build behavior")

    dockerfile = _write_broken_dockerfile(
        tmp_path,
        "this-registry-host-does-not-exist.invalid/nope:latest",
    )
    flavor = _FlavorConfig(
        name="synthetic-unreachable-base", dockerfile=dockerfile, build_context=tmp_path
    )
    tag = _image_tag(engine, flavor.name, "unreachable")
    with pytest.raises(pytest.skip.Exception) as exc_info:
        _build_flavor_image(engine, flavor, tag=tag)
    assert "reachability probe" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Smoke-check layer isolation — a hand-written, throwaway Dockerfile fixture
# that supplies its own --build-arg directly rather than through
# docker_cli_pins, and proves a broken CLI launcher fails at the dedicated
# smoke-check RUN layer, never inside the install layer above it. This is
# the synthetic version of the same proof the "Real-flavor pin enforcement"
# section below performs against an actual shipped flavor's own
# install/smoke-check layer split.
# ---------------------------------------------------------------------------


def _write_smoke_check_split_dockerfile(tmp_path: pathlib.Path, base_image: str) -> pathlib.Path:
    """
    Write a Dockerfile that mirrors the real docker/<flavor>/Dockerfile's
    install/smoke-check layer split: an install layer that always reports
    success -- exactly like `npm install -g` reporting "added N packages"
    even when the resolved package is broken for this platform -- and a
    separately-layered smoke-check RUN that is the only place a broken
    codex launcher fails.
    """
    dockerfile = tmp_path / "Dockerfile.smoke-check-split"
    dockerfile.write_text(
        f"FROM {base_image}\n"
        "ARG CODEX_VERSION\n"
        # Install layer: always succeeds, regardless of CODEX_VERSION --
        # models npm reporting a successful install for a package version
        # whose platform binary is missing or broken.
        "RUN printf '#!/bin/sh\\necho codex: launcher error, no working binary for "
        "pinned version %s >&2\\nexit 1\\n' \"$CODEX_VERSION\" > /usr/local/bin/codex "
        "&& chmod +x /usr/local/bin/codex "
        "&& echo \"install layer: codex ${CODEX_VERSION} placed, launcher untested\"\n"
        # Smoke-check layer: its own RUN, separate from the install layer
        # above -- the layer this fixture's mutation must fail in.
        "RUN codex --version\n"
    )
    return dockerfile


@pytest.mark.parametrize("engine", _ENGINES)
def test_bad_codex_version_fails_the_smoke_check_layer_naming_codex(
    tmp_path: pathlib.Path, engine: str
) -> None:
    """
    A CODEX_VERSION whose installed launcher cannot answer --version fails
    the build at the dedicated smoke-check RUN layer -- never inside the
    install layer above it -- and the failure names codex, proving that
    installing and smoke-checking the CLI in separate RUN instructions
    actually isolates the two failure modes: a package manager reporting
    success and a broken launcher failing are distinguishable from the
    build output alone.
    """
    if not _engine_available(engine):
        pytest.skip(f"{engine} not available; cannot demonstrate build behavior")

    dockerfile = _write_smoke_check_split_dockerfile(tmp_path, "alpine:3")
    flavor = _FlavorConfig(
        name="synthetic-bad-codex-version", dockerfile=dockerfile, build_context=tmp_path
    )
    tag = _image_tag(engine, flavor.name, "bad-codex")
    # The exact value doesn't drive the synthetic launcher's behavior above
    # (it always fails, modeling a codex release whose platform binary is
    # broken regardless of which version string was requested) -- what
    # matters is that a CODEX_VERSION flows through --build-arg to a
    # dedicated smoke-check layer, and that layer is where the failure
    # surfaces.
    bad_codex_version = "0.0.0-deliberately-broken-mutation"
    with pytest.raises(pytest.fail.Exception) as exc_info:
        _build_flavor_image(
            engine,
            flavor,
            tag=tag,
            build_arg_flags=["--build-arg", f"CODEX_VERSION={bad_codex_version}"],
        )
    failure_text = str(exc_info.value)
    assert "codex --version" in failure_text, (
        "expected the failure to be attributed to the 'RUN codex --version' "
        f"smoke-check layer specifically, not the install layer above it; got: "
        f"{failure_text}"
    )
    assert "codex" in failure_text.lower(), (
        f"expected the failure to name codex; got: {failure_text}"
    )


# ---------------------------------------------------------------------------
# Real-flavor pin enforcement — proves both new refusals against an actual
# shipped flavor's own Dockerfile, not the hand-written throwaway Dockerfile
# above. "debian" is named directly rather than picked from _FLAVORS by
# position: any discovered flavor would demonstrate the same guard (every
# docker/<flavor>/Dockerfile carries an identical ARG guard and smoke-check
# split), and naming one explicitly keeps these two tests' cost to a single
# flavor instead of multiplying the already-expensive cold-build cost by
# every flavor this lane discovers.
# ---------------------------------------------------------------------------

_REAL_SMOKE_CHECK_START = re.compile(r"^RUN\s+claude\s+--version\b")


def _extract_dockerfile_instruction(
    dockerfile: pathlib.Path, start_pattern: re.Pattern[str]
) -> str:
    """
    Return the verbatim source text of the first Dockerfile instruction
    whose first line matches start_pattern, including any backslash-
    continuation lines that belong to it.

    Used to couple a synthetic test Dockerfile to an ACTUAL instruction a
    real, shipped flavor carries, rather than a hand-duplicated copy of it
    that could silently drift from the shipped file over time.
    """
    lines = dockerfile.read_text().splitlines()
    for index, line in enumerate(lines):
        if start_pattern.match(line):
            block = [line]
            cursor = index
            while block[-1].rstrip().endswith("\\"):
                cursor += 1
                block.append(lines[cursor])
            return "\n".join(block)
    pytest.fail(
        f"no instruction matching {start_pattern.pattern!r} found in {dockerfile}; "
        "the install/smoke-check layer split this test depends on may have "
        "changed shape in the shipped Dockerfile"
    )


def _write_real_smoke_check_dockerfile(
    flavor: _FlavorConfig, tmp_path: pathlib.Path, base_image: str
) -> pathlib.Path:
    """
    Write a synthetic Dockerfile that installs stand-in claude/codex
    launchers (fast, no npm registry round-trip) and then runs flavor's
    ACTUAL smoke-check RUN instruction verbatim -- extracted from the
    shipped Dockerfile by _extract_dockerfile_instruction, never duplicated
    by hand -- so a failure below is attributed to the real flavor's own
    install/smoke-check layer split, not to an author's memory of it.

    A real `npm install -g @openai/codex@<bad-version>` fails INSIDE the
    install layer itself (the registry has no such version to resolve),
    which would prove nothing about smoke-check isolation -- this stand-in
    install layer is what lets the test target the smoke-check layer
    specifically, exactly like the hand-written throwaway fixture above
    does, but coupled to the real file's own smoke-check instruction text.
    """
    smoke_check_instruction = _extract_dockerfile_instruction(
        flavor.dockerfile, _REAL_SMOKE_CHECK_START
    )
    dockerfile = tmp_path / "Dockerfile.real-flavor-smoke-check"
    dockerfile.write_text(
        f"FROM {base_image}\n"
        "ARG CLAUDE_CODE_VERSION\n"
        "ARG CODEX_VERSION\n"
        # Install layer stand-in: always reports success for claude
        # regardless of CLAUDE_CODE_VERSION, and places a codex launcher
        # that always fails -- models npm reporting "added N packages" for
        # a package whose platform binary is broken.
        "RUN printf '#!/bin/sh\\necho claude ${CLAUDE_CODE_VERSION}\\n' "
        "> /usr/local/bin/claude && chmod +x /usr/local/bin/claude "
        "&& printf '#!/bin/sh\\necho codex: launcher error, no working binary "
        "for pinned version %s >&2\\nexit 1\\n' \"$CODEX_VERSION\" "
        "> /usr/local/bin/codex && chmod +x /usr/local/bin/codex "
        "&& echo \"install layer: claude ${CLAUDE_CODE_VERSION} and codex "
        "${CODEX_VERSION} placed, launchers untested\"\n"
        f"{smoke_check_instruction}\n"
    )
    return dockerfile


@pytest.mark.parametrize("engine", _ENGINES)
def test_real_flavor_build_without_pins_fails_naming_the_unset_arg(engine: str) -> None:
    """
    Building a real, shipped flavor's Dockerfile with no --build-arg at all
    fails, and the failure names the specific unset ARG -- proving the
    unpinned-build guard actually fires against the file this lane ships,
    not merely that the guard's text is present in it.
    """
    if not _engine_available(engine):
        pytest.skip(f"{engine} not available; cannot demonstrate build behavior")

    flavor = _flavor_by_name("debian")
    tag = _image_tag(engine, flavor.name, "unpinned-guard")
    with pytest.raises(pytest.fail.Exception) as exc_info:
        _build_flavor_image(engine, flavor, tag=tag, build_arg_flags=[])
    failure_text = str(exc_info.value)
    assert "CLAUDE_CODE_VERSION" in failure_text, (
        "expected the failure to name the unset CLAUDE_CODE_VERSION build-arg "
        f"(the first ARG the guard checks); got: {failure_text}"
    )


@pytest.mark.parametrize("engine", _ENGINES)
def test_real_flavor_bad_codex_version_fails_the_smoke_check_layer_naming_codex(
    tmp_path: pathlib.Path, engine: str
) -> None:
    """
    A bad CODEX_VERSION fails at a real, shipped flavor's own smoke-check
    RUN instruction -- extracted verbatim from the Dockerfile, not
    duplicated by hand -- and the failure names codex, never the install
    layer above it. This is the same proof as
    test_bad_codex_version_fails_the_smoke_check_layer_naming_codex above,
    performed against the shipped file's own layer split rather than a
    hand-written throwaway Dockerfile: if the real flavor's smoke-check
    instruction ever changes shape, this test's synthetic build changes
    with it instead of silently drifting out of sync.
    """
    if not _engine_available(engine):
        pytest.skip(f"{engine} not available; cannot demonstrate build behavior")

    flavor = _flavor_by_name("debian")
    dockerfile = _write_real_smoke_check_dockerfile(flavor, tmp_path, "alpine:3")
    synthetic_flavor = _FlavorConfig(
        name="synthetic-real-flavor-bad-codex-version",
        dockerfile=dockerfile,
        build_context=tmp_path,
    )
    tag = _image_tag(engine, synthetic_flavor.name, "bad-codex")
    good_claude_version = load_cli_pins()["CLAUDE_CODE_VERSION"]
    # Same reasoning as the hand-written fixture's bad_codex_version above:
    # the exact value doesn't drive the stand-in launcher's failure (it
    # always fails) -- what matters is that a bad CODEX_VERSION flows
    # through --build-arg to the real flavor's own smoke-check layer, and
    # that layer, extracted from the shipped file, is where the failure
    # surfaces.
    bad_codex_version = "0.0.0-deliberately-broken-mutation"
    with pytest.raises(pytest.fail.Exception) as exc_info:
        _build_flavor_image(
            engine,
            synthetic_flavor,
            tag=tag,
            build_arg_flags=[
                "--build-arg", f"CLAUDE_CODE_VERSION={good_claude_version}",
                "--build-arg", f"CODEX_VERSION={bad_codex_version}",
            ],
        )
    failure_text = str(exc_info.value)
    assert "codex --version" in failure_text, (
        "expected the failure to be attributed to the real flavor's own "
        "smoke-check instruction ('claude --version && codex --version'), "
        f"not the install layer above it; got: {failure_text}"
    )
    assert "codex" in failure_text.lower(), (
        f"expected the failure to name codex; got: {failure_text}"
    )


# ---------------------------------------------------------------------------
# Tests parametrized via the cold_built_image fixture
# ---------------------------------------------------------------------------


def test_image_builds_without_error(cold_built_image: tuple[str, str, str]) -> None:
    """
    Cold image build succeeds from the flavor's Dockerfile under this engine.

    Acceptance: the cold_built_image fixture performs the --no-cache --pull
    build; this test verifies the fixture completed without error by
    asserting the resulting tag is inspectable.
    """
    engine, _flavor_name, image_tag = cold_built_image
    result = subprocess.run(
        [engine, "image", "inspect", image_tag],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"Built image tag '{image_tag}' not found under {engine} after build; "
        f"inspect stderr: {result.stderr}"
    )


def test_tmux_version_meets_floor_in_built_image(cold_built_image: tuple[str, str, str]) -> None:
    """
    tmux inside the cold-built image meets the >= 3.1 floor.

    Runs `tmux -V` inside the built image and confirms the reported version
    is >= 3.1, for each flavor under each engine. This runtime check is the
    only place that floor is enforced today — none of the Dockerfiles pin
    or guard the tmux package version at build time.
    """
    engine, _flavor_name, image_tag = cold_built_image
    result = subprocess.run(
        [engine, "run", "--rm", "--entrypoint", "", image_tag, "tmux", "-V"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"tmux -V inside built image failed (rc={result.returncode}): {result.stderr}"
    )
    version_line = result.stdout.strip()
    # Expected: "tmux 3.3a", "tmux 3.2a", or a git-snapshot build's
    # "tmux next-3.4" (Rocky 10's tmux-3.3a package reports this at
    # runtime even though its rpm version is 3.3a) — search for the first
    # digit.digit pair anywhere in the version token rather than anchoring
    # at its start, so both formats parse the same way.
    parts = version_line.split()
    assert len(parts) >= 2 and parts[0].lower() == "tmux", (
        f"Unexpected tmux -V output: {version_line!r}"
    )
    version_str = parts[1]
    match = re.search(r"(\d+)\.(\d+)", version_str)
    assert match is not None, f"Could not parse version from {version_str!r}"
    major, minor = int(match.group(1)), int(match.group(2))
    assert (major, minor) >= (3, 1), (
        f"tmux {version_str} inside built image is below required floor 3.1"
    )


@pytest.mark.parametrize(
    "package, extra_engine_args, command_args",
    [
        pytest.param(
            "ncurses (tput)", ["-e", "TERM=xterm-256color"], ["tput", "cols"],
            id="tput",
        ),
        pytest.param("procps/procps-ng (watch)", [], ["watch", "--version"], id="watch"),
        pytest.param("git", [], ["git", "--version"], id="git"),
    ],
)
def test_package_available_in_built_image(
    cold_built_image: tuple[str, str, str],
    package: str,
    extra_engine_args: list[str],
    command_args: list[str],
) -> None:
    """A spot-checked package's command runs successfully inside the cold-built image."""
    engine, _flavor_name, image_tag = cold_built_image
    result = subprocess.run(
        [engine, "run", "--rm", "--entrypoint", "", *extra_engine_args,
         image_tag, *command_args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"{' '.join(command_args)} failed inside built image (rc={result.returncode}, "
        f"package={package}): {result.stderr.strip()}"
    )


def test_python3_available_in_built_image(cold_built_image: tuple[str, str, str]) -> None:
    """python3 is present and is >= 3.12 inside the cold-built image."""
    engine, _flavor_name, image_tag = cold_built_image
    result = subprocess.run(
        [engine, "run", "--rm", "--entrypoint", "", image_tag,
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
        f"python3 inside built image reports {version_tuple}; expected >= (3, 12)"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([str(_THIS_FILE), "-v"]))
