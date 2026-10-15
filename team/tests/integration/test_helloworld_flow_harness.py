"""
test_helloworld_flow_harness.py
================================
Integration test for team/scripts/harness/helloworld-flow-check.sh — the
documented answer to "did agent behaviour change" on the shipped
team/demos/helloworld-demo flow (see that harness's own header, section
"RELATIONSHIP TO team/scripts/harness/engine-behavior-baseline.sh", for the
resolution of the two-harness overlap this suite closes).

Coverage:
  - `--compare <fresh-dir>` against the committed golden outcomes under
    team/tests/fixtures/helloworld-flow/ exits 0 with no diff output when the
    flow (decompose -> CODER -> verify -> ship) runs unmodified.
  - Seeding a one-value change into a previously captured outcome file makes
    a subsequent `--compare` on that same directory fail, naming the
    differing file and a diff hunk; reverting the seed makes the next
    `--compare` pass again — proof the failure tracks the seeded content,
    not some other side effect of the harness run.
  - The operator's live projects.cfg is byte-identical before and after
    every test in this module, regardless of which harness mode ran.

Design notes:
  - No seam is mocked. Every test invokes the real harness script as a
    subprocess, which in turn drives the real create-project.sh, intake.sh,
    pm_materialize.py, cm/open-rc.sh, and cm/release.sh against a throwaway,
    self-registering kanban root the harness builds for itself (see the
    harness script's own header for why: this is the fix for the
    "no helloworld project registered" gap that made the demo unrunnable).
  - PGAI_AGENT_KANBAN_TEMP_DIR is redirected to a subdirectory of tmp_path so
    every scratch file the harness creates lands under tmp_path, never bare
    /tmp. The harness's own run_isolated() helper (env -i plus an explicit
    allowlist) additionally guarantees no ambient PGAI_*/KANBAN_ROOT variable
    from the pytest process's own environment leaks into the child scripts
    it drives — this suite does not need to re-implement that isolation, only
    verify from the outside that the operator's live state stays untouched.
  - The subprocess environment this suite itself hands to the harness script
    is built by tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env
    from an explicit, minimal set of names -- never inherited wholesale from
    this process's own environment -- the same discipline run_isolated()
    applies one layer further in, for the same reason: a leaked ambient
    value would make the comparison's verdict a property of the invoking
    shell instead of the tree. See _run_harness for the exact set.
  - Full-flow wall clock is a few seconds per invocation (decompose ->
    coder -> verify -> ship against a throwaway git repo), cheap enough to
    run as a normal step of every integration suite pass rather than a
    separate, slower verification tier.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test drives the harness's full flow (decompose -> CODER -> verify
    -> ship) against a throwaway self-registering kanban root, and the ship
    step runs cm/open-rc.sh + cm/release.sh against a real git repo -- there
    is no way to exercise that flow without a real git binary.
    """
    require_binary("git", label="helloworld-flow-harness")


_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_HARNESS = _TEAM_DIR / "scripts" / "harness" / "helloworld-flow-check.sh"
_GOLDEN_DIR = _TEAM_DIR / "tests" / "fixtures" / "helloworld-flow"

# Resolved once at import time, before any per-test fixture (in particular
# conftest.py's autouse _block_live_kanban_writes) redirects
# PGAI_AGENT_KANBAN_ROOT_PATH to a safe tmp root for the running test. This
# is therefore the same value run-integration-tests.sh itself resolved for
# the operator's real, on-disk install — the thing this module's autouse
# fixture below asserts the harness never touches.
_LIVE_KANBAN_ROOT = pathlib.Path(
    os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")
    or str(pathlib.Path.home() / "pgai_agent_kanban")
)
_LIVE_PROJECTS_CFG = _LIVE_KANBAN_ROOT / "projects.cfg"


def _run_harness(
    args: list[str],
    *,
    temp_root: pathlib.Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Invoke helloworld-flow-check.sh as a subprocess.

    Args:
        args:      CLI arguments after the script path (e.g. ["--compare", str(dir)]).
        temp_root: Directory to use as PGAI_AGENT_KANBAN_TEMP_DIR for this run.
        timeout:   Subprocess timeout in seconds.

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    home_dir = temp_root.parent / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home_dir,
        # The flow drives the wake/pm-agent substrate (pm_materialize.py,
        # the status-file helpers this harness sources directly), which
        # refuses to run under a `python3` older than 3.12 (see
        # wake_bootstrap.sh's own version guard) -- and the verify stage
        # below runs `python3 -m pytest test_hello.py` against the demo's
        # own tiny test file, which needs pytest importable on that same
        # interpreter. The base builder's standard toolchain directories
        # are not guaranteed to resolve `python3` to a 3.12+-with-pytest
        # interpreter on every host, so the directory holding the
        # interpreter currently running this test process (guaranteed both,
        # for the same two reasons: this whole suite requires 3.12+ and is
        # itself running under pytest) is prepended, matching this host's
        # own toolchain rather than hardcoding a path.
        extra_path_dirs=(pathlib.Path(sys.executable).parent,),
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_root)},
    )
    return subprocess.run(
        ["bash", str(_HARNESS)] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


@pytest.fixture(autouse=True)
def _require_golden_outcomes() -> None:
    """Fail with a clear message if the golden outcomes are missing.

    A missing golden directory means the fixtures were never captured/
    committed for this tree — a setup defect, not a behaviour regression, so
    it is reported distinctly from a real diff failure.
    """
    if not _GOLDEN_DIR.is_dir() or not any(_GOLDEN_DIR.iterdir()):
        pytest.fail(
            f"Golden outcomes missing at {_GOLDEN_DIR}. "
            f"Run: bash {_HARNESS} --capture {_GOLDEN_DIR}"
        )


@pytest.fixture(autouse=True)
def _assert_live_projects_cfg_untouched():
    """Assert the operator's live projects.cfg is byte-identical before and
    after every test body in this module, whatever mode the harness ran in.

    The harness is documented to self-register pgai-helloworld only inside
    its own throwaway root (see helloworld-flow-check.sh's header, step 2).
    This fixture is the regression lock on that guarantee: if a future edit
    to the harness (or one of the production scripts it drives) ever
    resolves a path from the ambient environment instead of its throwaway
    root, this is what catches the leak into the operator's real install.
    """
    if not _LIVE_PROJECTS_CFG.is_file():
        # Nothing to protect against on a host that has never registered a
        # project — the assertion has no baseline to compare against.
        yield
        return
    before = _LIVE_PROJECTS_CFG.read_bytes()
    yield
    after = _LIVE_PROJECTS_CFG.read_bytes()
    assert after == before, (
        f"{_LIVE_PROJECTS_CFG} changed during the test body — the harness "
        f"wrote into the operator's live install instead of its own "
        f"throwaway root."
    )


def test_compare_against_golden_is_clean_on_unmodified_flow(tmp_path: pathlib.Path) -> None:
    """A fresh --compare against the committed golden outcomes exits 0 with no diff.

    This drives the full decompose -> CODER -> verify -> ship flow (the
    target directory does not exist yet, so the harness captures fresh
    before comparing) and is the regression lock on agent behaviour: as long
    as this test stays green, the helloworld demo's decompose ticket shape,
    verify checks, and ship tag are unchanged from the committed baseline.
    """
    temp_root = tmp_path / "pgai_kanban_tmp"
    temp_root.mkdir()
    scratch = tmp_path / "compare-clean"

    result = _run_harness(["--compare", str(scratch)], temp_root=temp_root)

    assert result.returncode == 0, (
        f"Expected a clean compare against {_GOLDEN_DIR} to exit 0.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "FAIL" not in result.stdout, (
        f"Expected no diff output on an unmodified flow.\nstdout: {result.stdout}"
    )


def test_compare_detects_and_recovers_from_a_seeded_outcome_change(
    tmp_path: pathlib.Path,
) -> None:
    """Seeding a one-value change into a captured outcome fails --compare;
    reverting it passes again.

    Captures fresh into a directory, corrupts the release tag recorded in
    ship.txt, then re-runs --compare against that SAME directory (no
    recapture — the harness only recaptures when the target directory is
    absent or empty). The compare must exit non-zero and name the differing
    file with enough context (a diff hunk) to locate the change. Reverting
    the seed and comparing again must return to a clean pass, which is the
    proof that the earlier failure tracked the seeded content and not some
    unrelated side effect of the harness run — a test that cannot fail is
    not evidence.
    """
    temp_root = tmp_path / "pgai_kanban_tmp"
    temp_root.mkdir()
    scratch = tmp_path / "compare-seeded"

    capture_result = _run_harness(["--capture", str(scratch)], temp_root=temp_root)
    assert capture_result.returncode == 0, (
        f"Fresh --capture must succeed before it can be seeded.\n"
        f"stdout: {capture_result.stdout}\nstderr: {capture_result.stderr}"
    )

    ship_path = scratch / "ship.txt"
    original = ship_path.read_text(encoding="utf-8")
    assert "release_tag:" in original, (
        f"{ship_path} must contain a release_tag line to seed a change into."
    )

    mutated = original.replace("ai_v0.1.0", "ai_v9.9.9", 1)
    assert mutated != original, "Seed mutation produced no change — captured content shifted."
    ship_path.write_text(mutated, encoding="utf-8")

    compare_result = _run_harness(["--compare", str(scratch)], temp_root=temp_root)
    assert compare_result.returncode != 0, (
        f"Expected --compare to fail after seeding a release-tag change.\n"
        f"stdout: {compare_result.stdout}\nstderr: {compare_result.stderr}"
    )
    assert "ship.txt" in compare_result.stdout, (
        f"Expected the differing file name in --compare output.\nstdout: {compare_result.stdout}"
    )
    assert "@@" in compare_result.stdout, (
        f"Expected a diff hunk locating the change.\nstdout: {compare_result.stdout}"
    )

    # Revert the seed and confirm the failure clears — this is what proves
    # the earlier non-zero exit was caused by the seeded content and not by
    # some unrelated flakiness in the harness or its throwaway root.
    ship_path.write_text(original, encoding="utf-8")
    recovery_result = _run_harness(["--compare", str(scratch)], temp_root=temp_root)
    assert recovery_result.returncode == 0, (
        f"Expected --compare to pass again after reverting the seeded change.\n"
        f"stdout: {recovery_result.stdout}\nstderr: {recovery_result.stderr}"
    )
