"""
test_temp_sweep_permissions.py
================================
Behavioral unit tests for the post-suite scratch sweep
(`pgai_temp_cleanup_tests` in `team/scripts/lib/temp.sh`), which used to end in a
bare `rm -rf` with no permission recovery. A directory an earlier aborted
or crashed run left behind mode 0555 (no write bit) made that `rm -rf`
fail, and under the gated runners' `set -euo pipefail` that turned a purely
cosmetic housekeeping problem into the whole runner's own exit status --
even though every test had passed and no lint was fatal.

These tests lock three things:

  1. `pgai_temp_cleanup_tests` now recovers from a read-only leftover on
     its own (chmod, then remove) and returns 0 -- the fix itself.
  2. Every pre-existing refuse-if-dangerous-root guard the function already
     carried (empty root, `/`, `/tmp`, tests-path-equals-root) still
     refuses exactly as before; this change must not widen what the
     function is willing to delete.
  3. The call sites in both gated runners (`run-integration-tests.sh`,
     `run-unit-tests.sh`) can no longer let a sweep problem set the
     runner's own exit status: wrapping the call through
     `gated_runner_run_quality_lint` (the same helper the two existing
     post-suite cleanliness checks already use) means even a leftover the
     sweep genuinely cannot clear is narrated as a non-blocking finding
     and the script still reaches its own `exit 0`.

TEMP-PATH CONTRACT
-------------------
Every sandbox root here is created under pytest's own `tmp_path`, which the
parent conftest.py already redirects under `$PGAI_AGENT_KANBAN_TEMP_DIR/tests`
(see shell_harness.py's TEMP-PATH CONTRACT) -- no bare `/tmp` path is ever
used, and `PGAI_AGENT_KANBAN_TEMP_DIR` is always overridden to a throwaway
subdirectory of `tmp_path` before `temp.sh` is sourced, per the
live-install-safety convention (`docs/coding-standards.md`,
`roles/CODER.md` "Live-install safety").

Every test that leaves a mode-0555 directory behind restores write
permission in a `finally` block before returning control to pytest, even
on an assertion failure -- a read-only leftover surviving past a test
process poisons every later gated-runner invocation that shares the same
temp root, exactly the failure mode under test here.
"""

from __future__ import annotations

import os
import pathlib
import stat

from tests.unit.shell_harness import run_bash

_TEMP_LIB = "scripts/lib/temp.sh"
_GATED_RUNNER_LIB = "scripts/lib/gated_runner_lint.sh"


def _restore_writable(root: pathlib.Path) -> None:
    """Recursively restore u+w on every directory under *root*, and on
    *root* itself, so pytest's own tmp_path teardown (and any later test
    sharing the same sandbox) is never handed a read-only leftover --
    the same crash-safety hazard a read-only leftover poses to the
    framework's own sweep."""
    if not root.exists():
        return
    for dirpath, dirnames, _filenames in os.walk(root):
        for name in dirnames:
            p = pathlib.Path(dirpath) / name
            try:
                p.chmod(p.stat().st_mode | stat.S_IWUSR)
            except FileNotFoundError:
                pass
    try:
        root.chmod(root.stat().st_mode | stat.S_IWUSR)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# 1. The fix itself -- a read-only leftover no longer blocks the sweep.
# ---------------------------------------------------------------------------


def test_cleanup_tests_removes_read_only_leftover_and_returns_0(
    tmp_path: pathlib.Path,
) -> None:
    """Two mode-0555 directories left behind under tests/ by an earlier
    aborted run, each containing files a later invocation still needs to
    remove, must not survive the sweep, and the sweep must return 0."""
    sandbox = tmp_path / "sandbox"
    tests_root = sandbox / "tests"
    leftover_a = tests_root / "pytest-of-rocky" / "pytest-1" / "case_a" / "kanban_root"
    leftover_b = tests_root / "pytest-of-rocky" / "pytest-1" / "case_b" / "kanban_root"
    leftover_a.mkdir(parents=True)
    leftover_b.mkdir(parents=True)
    (leftover_a / "projects.cfg").write_text("")
    (leftover_b / "projects.cfg").write_text("")
    (leftover_b / "HALT").write_text("")
    leftover_a.chmod(0o555)
    leftover_b.chmod(0o555)

    try:
        result = run_bash(
            tmp_path,
            f"set -euo pipefail\nsource {_TEMP_LIB}\npgai_temp_cleanup_tests\necho REACHED_EXIT_0",
            extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)},
        )
    finally:
        _restore_writable(tests_root)

    assert result.returncode == 0, (
        "pgai_temp_cleanup_tests must recover from a read-only leftover and "
        f"return 0; got exit {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "REACHED_EXIT_0" in result.stdout, (
        "the sweep must not abort the caller under set -euo pipefail: "
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert not tests_root.exists(), (
        f"tests/ subtree was not fully removed: {tests_root} still exists"
    )


def test_cleanup_tests_removes_nested_read_only_leftover(
    tmp_path: pathlib.Path,
) -> None:
    """A read-only directory nested two levels below another read-only
    directory must also be recovered -- chmod -R u+w must be able to
    traverse into a 0555 parent (it carries the execute bit) to reach and
    fix a 0555 child."""
    sandbox = tmp_path / "sandbox"
    tests_root = sandbox / "tests"
    outer = tests_root / "outer"
    inner = outer / "middle" / "inner"
    inner.mkdir(parents=True)
    (inner / "leftover.txt").write_text("")
    inner.chmod(0o555)
    (outer / "middle").chmod(0o555)
    outer.chmod(0o555)

    try:
        result = run_bash(
            tmp_path,
            f"source {_TEMP_LIB}\npgai_temp_cleanup_tests",
            extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)},
        )
    finally:
        _restore_writable(tests_root)

    assert result.returncode == 0, (
        f"nested read-only leftover was not recovered: exit {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert not tests_root.exists()


def test_cleanup_tests_returns_0_with_no_leftovers(tmp_path: pathlib.Path) -> None:
    """Baseline: an ordinary (writable) tests/ subtree is still removed
    cleanly -- the permission-recovery addition must not regress the
    common case."""
    sandbox = tmp_path / "sandbox"
    tests_root = sandbox / "tests"
    (tests_root / "ordinary").mkdir(parents=True)
    (tests_root / "ordinary" / "file.txt").write_text("")

    result = run_bash(
        tmp_path,
        f"source {_TEMP_LIB}\npgai_temp_cleanup_tests",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)},
    )

    assert result.returncode == 0
    assert not tests_root.exists()


def test_cleanup_tests_no_error_when_tests_subtree_absent(
    tmp_path: pathlib.Path,
) -> None:
    """Pre-existing behavior, unchanged: calling the sweep when tests/
    was never created is a silent no-op, not an error."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = run_bash(
        tmp_path,
        f"source {_TEMP_LIB}\npgai_temp_cleanup_tests",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)},
    )

    assert result.returncode == 0


# ---------------------------------------------------------------------------
# 2. When recovery genuinely cannot help, the sweep narrates and fails --
#    it must never delete nothing silently.
# ---------------------------------------------------------------------------


def test_cleanup_tests_narrates_and_returns_1_when_recovery_cannot_help(
    tmp_path: pathlib.Path,
) -> None:
    """Shadows `chmod` with a no-op (simulating a permission problem the
    recovery step itself cannot fix, e.g. a directory this process does
    not own) so the read-only leftover survives the recovery attempt.
    The sweep must still narrate the exact path to stderr and return
    non-zero rather than silently reporting success with the leftover
    still in place."""
    sandbox = tmp_path / "sandbox"
    tests_root = sandbox / "tests"
    leftover = tests_root / "stuck"
    leftover.mkdir(parents=True)
    (leftover / "file.txt").write_text("")
    leftover.chmod(0o555)

    try:
        result = run_bash(
            tmp_path,
            f"chmod() {{ return 0; }}\nsource {_TEMP_LIB}\n"
            f"if pgai_temp_cleanup_tests; then echo SWEEP_SUCCEEDED; "
            f"else echo SWEEP_FAILED; fi",
            extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)},
        )
    finally:
        _restore_writable(tests_root)

    assert "SWEEP_FAILED" in result.stdout, (
        "a leftover that chmod recovery cannot actually fix must make the "
        f"sweep report failure, not success\nstdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    # rm's own "cannot remove '<path>'" diagnostic (unsuppressed on stderr)
    # names the specific leftover; temp.sh's own summary line names the
    # swept tests/ subtree as a whole -- both land on stderr.
    assert str(leftover) in result.stderr, (
        "the failure must name the exact path it could not remove: "
        f"stderr: {result.stderr!r}"
    )
    assert str(tests_root) in result.stderr, (
        "the failure must also name the tests/ subtree the sweep was "
        f"asked to clear: stderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# 3. Every pre-existing refuse-if-dangerous-root guard still refuses,
#    unchanged. pgai_temp_dir() is overridden after sourcing so each guard
#    can be exercised directly regardless of what the real resolver would
#    ever return on this host.
# ---------------------------------------------------------------------------


def test_cleanup_tests_refuses_empty_root(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path,
        f"source {_TEMP_LIB}\n"
        "pgai_temp_dir() { printf '%s' ''; }\n"
        "if pgai_temp_cleanup_tests; then echo UNEXPECTED_SUCCESS; "
        "else echo GUARD_FIRED; fi",
    )
    assert "GUARD_FIRED" in result.stdout, result.stdout
    assert "refusing to clean empty path" in result.stderr, result.stderr


def test_cleanup_tests_refuses_filesystem_root(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path,
        f"source {_TEMP_LIB}\n"
        "pgai_temp_dir() { printf '/'; }\n"
        "if pgai_temp_cleanup_tests; then echo UNEXPECTED_SUCCESS; "
        "else echo GUARD_FIRED; fi",
    )
    assert "GUARD_FIRED" in result.stdout, result.stdout
    assert "refusing to clean filesystem root" in result.stderr, result.stderr


def test_cleanup_tests_refuses_bare_tmp(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path,
        f"source {_TEMP_LIB}\n"
        "pgai_temp_dir() { printf '/tmp'; }\n"
        "if pgai_temp_cleanup_tests; then echo UNEXPECTED_SUCCESS; "
        "else echo GUARD_FIRED; fi",
    )
    assert "GUARD_FIRED" in result.stdout, result.stdout
    assert "refusing to clean /tmp directly" in result.stderr, result.stderr


def test_cleanup_tests_root_equals_tests_path_guard_is_present_in_source() -> None:
    """The fourth guard -- "resolved tests path equals the temp root
    itself" -- compares `"${root}/tests"` against `"$root"`. Given how
    `tests_path` is always built (root with a non-empty "/tests" suffix
    appended), that comparison can never be true for any value
    `pgai_temp_dir()` could return: a string concatenated with a non-empty
    suffix can never equal the original string. There is therefore no
    behavioral scenario -- real or stubbed -- that can drive this branch,
    unlike the three guards above.  This is a structural regression test
    instead: it locks the guard's exact conditional and refusal message in
    the source so a future edit cannot silently drop it, satisfying "must
    stay exactly as they are" for the one guard behavioral coverage cannot
    reach."""
    lib_path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lib" / "temp.sh"
    source_text = lib_path.read_text()
    assert '[[ "$tests_path" == "$root" ]]' in source_text, (
        "pgai_temp_cleanup_tests's tests-path-equals-root guard condition "
        "is missing or was reworded"
    )
    assert (
        "refusing to remove the temp root itself" in source_text
        and "tests path resolved to root" in source_text
    ), "the tests-path-equals-root guard's refusal message was changed"


# ---------------------------------------------------------------------------
# 4. Call-site level: the gated_runner_run_quality_lint wrapping both
#    runners now use must absorb a sweep failure under set -euo pipefail
#    and still let the script reach its own exit 0.
# ---------------------------------------------------------------------------


def test_gated_runner_wrapped_sweep_never_aborts_caller_under_strict_mode(
    tmp_path: pathlib.Path,
) -> None:
    """Reproduces the exact call-site shape both run-integration-tests.sh
    and run-unit-tests.sh now use: `gated_runner_run_quality_lint
    "scratch_sweep" -- pgai_temp_cleanup_tests` under `set -euo pipefail`.
    Shadows `chmod` as a no-op so the leftover survives recovery and the
    sweep genuinely fails -- the case where the sweep narrates a warning
    naming the path and the caller's own verdict (pytest's result and the
    fatal lints) must stand regardless, rather than the sweep's status
    overriding it. The wrapped script must still reach its own
    `exit 0`, matching what a passing suite with no fatal lint findings
    would report."""
    sandbox = tmp_path / "sandbox"
    tests_root = sandbox / "tests"
    leftover = tests_root / "stuck"
    leftover.mkdir(parents=True)
    (leftover / "file.txt").write_text("")
    leftover.chmod(0o555)

    script = f"""
set -euo pipefail
chmod() {{ return 0; }}
source {_TEMP_LIB}
source {_GATED_RUNNER_LIB}
gated_runner_run_quality_lint "scratch_sweep" -- pgai_temp_cleanup_tests
echo VERDICT_UNCHANGED_BY_SWEEP
exit 0
"""

    try:
        result = run_bash(
            tmp_path,
            script,
            extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)},
        )
    finally:
        _restore_writable(tests_root)

    assert result.returncode == 0, (
        "a sweep failure must never set the caller's own exit status under "
        f"set -euo pipefail; got exit {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "VERDICT_UNCHANGED_BY_SWEEP" in result.stdout, (
        f"script aborted before its own exit 0: stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    # gated_runner_run_quality_lint prints the wrapped command's own
    # captured stdout+stderr verbatim to its caller's stdout, and narrates
    # the "[gated-runner] <lint-name>: ... non-blocking" line to stderr --
    # so the lint name is on stderr and the specific path (from
    # pgai_temp_cleanup_tests's own diagnostics, captured verbatim) is on
    # stdout.
    assert "scratch_sweep" in result.stderr, (
        f"the finding must be narrated, naming the lint: stderr: {result.stderr!r}"
    )
    assert "non-blocking" in result.stderr, (
        f"the narration must say the finding did not block: stderr: {result.stderr!r}"
    )
    assert str(leftover) in result.stdout, (
        "the narration must name the exact path the sweep could not clear: "
        f"stdout: {result.stdout!r}"
    )


def test_gated_runner_wrapped_sweep_reports_ok_on_success(
    tmp_path: pathlib.Path,
) -> None:
    """Companion to the failure-path test above: when the sweep succeeds
    (the normal case, including the recovered-permission case), the
    wrapper reports OK rather than a finding."""
    sandbox = tmp_path / "sandbox"
    tests_root = sandbox / "tests"
    leftover = tests_root / "recoverable"
    leftover.mkdir(parents=True)
    (leftover / "file.txt").write_text("")
    leftover.chmod(0o555)

    script = f"""
set -euo pipefail
source {_TEMP_LIB}
source {_GATED_RUNNER_LIB}
gated_runner_run_quality_lint "scratch_sweep" -- pgai_temp_cleanup_tests
exit 0
"""

    result = run_bash(
        tmp_path,
        script,
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)},
    )

    assert result.returncode == 0
    assert "[gated-runner] scratch_sweep: OK" in result.stdout, result.stdout
    assert not tests_root.exists()
