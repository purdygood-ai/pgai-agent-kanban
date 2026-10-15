"""
test_engine_behavior_baseline_harness.py
=========================================
Integration test for team/scripts/harness/engine-behavior-baseline.sh — the
behavior-preservation harness the v2.9.20 engine decomposition is verified
against.

Coverage:
  - `--compare <fresh-dir>` against the committed golden snapshots under
    team/tests/fixtures/engine-baseline/ exits 0 with no diff output on the
    unmodified tree.
  - Seeding a one-character change into a previously captured surface makes
    a subsequent `--compare` on that same directory exit non-zero and print
    the differing file and a diff hunk locating the change.

Design notes:
  - No seam is mocked. Both tests invoke the real harness script, which in
    turn drives the real column-render.sh, wake/harness.sh, and
    cm/open-rc.sh + cm/release.sh against ephemeral synthetic fixtures the
    harness builds for itself (see the harness script's own header comment
    for why: no "helloworld" project is registered in this tree, so the
    harness substitutes a self-contained synthetic fixture for each of the
    three named operator surfaces).
  - PGAI_AGENT_KANBAN_TEMP_DIR is redirected to a subdirectory of tmp_path so
    every scratch file the harness creates lands under tmp_path, never bare
    /tmp.
  - The subprocess environment is built by
    tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env from an
    explicit, minimal set of names -- never inherited wholesale from this
    process's own environment -- so the comparison's verdict is a property
    of the tree, not of whatever the invoking shell happens to export. See
    _run_harness for the exact set.
  - MAX_TASK_SECONDS and KILL_GRACE_SECONDS are pinned inside
    engine-behavior-baseline.sh's own wake-log capture step (not by this
    test module), so neither this suite nor an operator's shell needs to
    set them for --capture or --compare to agree with the committed golden.
    test_capture_is_stable_across_invoking_shell_budget_values and
    test_compare_is_stable_across_invoking_shell_budget_values prove that
    independence by handing the harness subprocess two different ambient
    values via _run_harness's extra_env and asserting the pin wins both
    times. See the harness script's own header comment for the pinned
    values (_GOLDEN_MAX_TASK_SECONDS / _GOLDEN_KILL_GRACE_SECONDS there) --
    that is the single place either value is defined; this module does not
    keep its own copy.
  - This test can take longer than a typical unit test (tens of seconds):
    the ship-summary surface drives a real cm/open-rc.sh + cm/release.sh
    pass over a synthetic git repo.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Both tests drive the harness's full three-surface capture, and the
    ship-summary surface drives a real cm/open-rc.sh + cm/release.sh pass
    over a synthetic git repo -- there is no way to exercise that surface
    without a real git binary.
    """
    require_binary("git", label="engine-behavior-baseline-harness")


_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_HARNESS = _TEAM_DIR / "scripts" / "harness" / "engine-behavior-baseline.sh"
_GOLDEN_DIR = _TEAM_DIR / "tests" / "fixtures" / "engine-baseline"


def _run_harness(
    args: list[str],
    *,
    temp_root: pathlib.Path,
    timeout: int = 180,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Invoke engine-behavior-baseline.sh as a subprocess.

    Args:
        args:      CLI arguments after the script path (e.g. ["--compare", str(dir)]).
        temp_root: Directory to use as PGAI_AGENT_KANBAN_TEMP_DIR for this run.
        timeout:   Subprocess timeout in seconds.
        extra_env: Additional name/value pairs layered on top of the base
                   scrubbed environment, applied last so a caller-supplied
                   value always wins. Tests use this to simulate an
                   operator's ambient MAX_TASK_SECONDS / KILL_GRACE_SECONDS
                   and prove the harness's own pin (in
                   engine-behavior-baseline.sh, not here) overrides them --
                   see test_capture_is_stable_across_invoking_shell_budget_values
                   and test_compare_is_stable_across_invoking_shell_budget_values.

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    home_dir = temp_root.parent / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home_dir,
        # The wake-log capture drives the real wake bootstrap, which refuses
        # to run under a `python3` older than 3.12 (see wake_bootstrap.sh's
        # own version guard). The base builder's standard toolchain
        # directories are not guaranteed to resolve `python3` to a 3.12+
        # interpreter on every host, so the directory holding the
        # interpreter currently running this test process (guaranteed 3.12+
        # for the same reason: this whole suite requires it) is prepended,
        # matching this host's own toolchain rather than hardcoding a path.
        extra_path_dirs=(pathlib.Path(sys.executable).parent,),
        extra_env={
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_root),
            **(extra_env or {}),
        },
    )
    return subprocess.run(
        ["bash", str(_HARNESS)] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


@pytest.fixture(autouse=True)
def _require_golden_snapshots() -> None:
    """Fail with a clear message if the golden snapshots are missing.

    A missing golden directory means the fixtures were never captured/committed
    for this tree — a setup defect, not a behavior regression, so it is
    reported distinctly from a real diff failure.
    """
    if not _GOLDEN_DIR.is_dir() or not any(_GOLDEN_DIR.iterdir()):
        pytest.fail(
            f"Golden snapshots missing at {_GOLDEN_DIR}. "
            f"Run: bash {_HARNESS} --capture {_GOLDEN_DIR}"
        )


def test_compare_against_goldens_is_clean_on_unmodified_tree(tmp_path: pathlib.Path) -> None:
    """A fresh --compare against the committed goldens exits 0 with no diff.

    This is the regression lock for the whole engine decomposition: as long
    as this test stays green, the operator-facing board render, wake log
    line shapes, and CM ship summary block are byte-identical (modulo the
    normalized volatile fields) to the pre-decomposition baseline.
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
        f"Expected no diff output on an unmodified tree.\nstdout: {result.stdout}"
    )


def test_compare_detects_a_seeded_one_character_change(tmp_path: pathlib.Path) -> None:
    """Seeding a one-character change into a captured surface fails --compare.

    Captures fresh into a directory, corrupts one character in one captured
    surface file, then re-runs --compare against that SAME directory (no
    recapture — the harness only recaptures when the target directory is
    absent or empty). The compare must exit non-zero and name the differing
    file with enough context (a diff hunk) to locate the change.
    """
    temp_root = tmp_path / "pgai_kanban_tmp"
    temp_root.mkdir()
    scratch = tmp_path / "compare-seeded"

    capture_result = _run_harness(["--capture", str(scratch)], temp_root=temp_root)
    assert capture_result.returncode == 0, (
        f"Fresh --capture must succeed before it can be seeded.\n"
        f"stdout: {capture_result.stdout}\nstderr: {capture_result.stderr}"
    )

    board_render_path = scratch / "board-render.txt"
    original = board_render_path.read_text(encoding="utf-8")
    assert original, f"{board_render_path} must be non-empty to seed a change into it."

    # Flip exactly one character (first digit of the compact task ID) so the
    # corrupted file differs from the golden by a single byte.
    mutated = original.replace("1", "9", 1)
    assert mutated != original, "Seed mutation produced no change — fixture content shifted."
    board_render_path.write_text(mutated, encoding="utf-8")

    compare_result = _run_harness(["--compare", str(scratch)], temp_root=temp_root)

    assert compare_result.returncode != 0, (
        f"Expected --compare to fail after seeding a one-character change.\n"
        f"stdout: {compare_result.stdout}\nstderr: {compare_result.stderr}"
    )
    assert "board-render.txt" in compare_result.stdout, (
        f"Expected the differing file name in --compare output.\nstdout: {compare_result.stdout}"
    )
    # A unified diff hunk (@@ ... @@) locates the differing line for the reader.
    assert "@@" in compare_result.stdout, (
        f"Expected a diff hunk locating the change.\nstdout: {compare_result.stdout}"
    )


def test_capture_is_stable_across_invoking_shell_budget_values(
    tmp_path: pathlib.Path,
) -> None:
    """A fresh --capture writes the same wake-log.txt bytes whatever
    MAX_TASK_SECONDS and KILL_GRACE_SECONDS the invoking shell exports.

    This is the capture path itself -- not --compare falling back to a
    capture -- run twice with two ambient budget pairs that both differ
    from the golden's own 5400s/30s and from each other, via _run_harness's
    extra_env (the closest a subprocess test can get to "an operator's
    shell already has these exported"). Both captures must reproduce the
    committed golden byte-for-byte: engine-behavior-baseline.sh pins
    MAX_TASK_SECONDS / KILL_GRACE_SECONDS for its own wake-log capture step
    (see _GOLDEN_MAX_TASK_SECONDS / _GOLDEN_KILL_GRACE_SECONDS in that
    script), so the ambient values passed here are expected to be ignored
    for that surface. Before that pin existed, this test failed: the
    captured wake-log.txt's derived-ceiling line tracked whichever ambient
    pair was exported, and only matched the golden by coincidence when the
    ambient pair happened to equal 5400/30.
    """
    temp_root = tmp_path / "pgai_kanban_tmp"
    temp_root.mkdir()
    golden_wake_log = (_GOLDEN_DIR / "wake-log.txt").read_bytes()

    for label, max_task_seconds, kill_grace_seconds in (
        ("ten-thousand-eight-hundred", "10800", "45"),
        ("one", "1", "5"),
    ):
        scratch = tmp_path / f"capture-{label}"

        result = _run_harness(
            ["--capture", str(scratch)],
            temp_root=temp_root,
            extra_env={
                "MAX_TASK_SECONDS": max_task_seconds,
                "KILL_GRACE_SECONDS": kill_grace_seconds,
            },
        )

        assert result.returncode == 0, (
            f"Expected --capture to succeed with the invoking shell's "
            f"MAX_TASK_SECONDS={max_task_seconds} KILL_GRACE_SECONDS={kill_grace_seconds}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        captured = (scratch / "wake-log.txt").read_bytes()
        assert captured == golden_wake_log, (
            f"Expected wake-log.txt captured with ambient "
            f"MAX_TASK_SECONDS={max_task_seconds} KILL_GRACE_SECONDS={kill_grace_seconds} "
            f"to be byte-identical to the committed golden (the harness pins its "
            f"own values for this surface regardless of the caller's environment).\n"
            f"captured: {captured!r}\ngolden: {golden_wake_log!r}"
        )


def test_compare_is_stable_across_invoking_shell_budget_values(
    tmp_path: pathlib.Path,
) -> None:
    """A clean compare reaches the same verdict whatever MAX_TASK_SECONDS
    and KILL_GRACE_SECONDS the invoking shell happens to export.

    Exercises two combinations that both differ from the golden's own
    5400s/30s -- and from each other -- so a pass here demonstrates real
    independence from the ambient environment, not a lucky coincidence
    with the pinned default. The target directory starts empty each time,
    so --compare falls back to a fresh --capture internally before
    diffing -- this test therefore also covers the capture-via-compare
    path, in addition to test_capture_is_stable_across_invoking_shell_budget_values
    covering --capture directly.
    """
    temp_root = tmp_path / "pgai_kanban_tmp"
    temp_root.mkdir()

    for label, max_task_seconds, kill_grace_seconds in (
        ("ten-thousand-eight-hundred", "10800", "45"),
        ("one", "1", "5"),
    ):
        scratch = tmp_path / f"compare-{label}"

        result = _run_harness(
            ["--compare", str(scratch)],
            temp_root=temp_root,
            extra_env={
                "MAX_TASK_SECONDS": max_task_seconds,
                "KILL_GRACE_SECONDS": kill_grace_seconds,
            },
        )

        assert result.returncode == 0, (
            f"Expected a clean compare to exit 0 with the invoking shell's "
            f"MAX_TASK_SECONDS={max_task_seconds} KILL_GRACE_SECONDS={kill_grace_seconds} "
            f"(the harness pins its own subprocess values regardless).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "FAIL" not in result.stdout, (
            f"Expected no diff output on an unmodified tree.\nstdout: {result.stdout}"
        )
