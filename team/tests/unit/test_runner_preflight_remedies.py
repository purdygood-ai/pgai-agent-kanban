"""
test_runner_preflight_remedies.py
==================================
Retrofit: the three gated test runners
(run-unit-tests.sh, run-integration-tests.sh, run-gated-tests.sh) print full
remedy blocks for deterministic pre-flight aborts and name the next
diagnostic step for non-deterministic failures, per
docs/coding-standards.md "Gates prescribe, not just diagnose", classified
as follows:

  - unknown-argument abort          (partial)
  - pytest-not-installed abort      (full-remedy)
  - pytest test-suite failure       (non-deterministic exempt)
  - bare-/tmp cleanliness assertion (CONVERT-class, warn+file+continue —
                                      see below)
  - TCP listener cleanliness check  (CONVERT-class, warn+file+continue —
                                      see below)

Converting docs/blocking-site-census.md rows 52-53 replaced the bare-/tmp
and TCP-listener cleanliness assertions' hard `exit 1` with the same
gated_runner_run_quality_lint warn+file+continue wrapper every other
quality-class lint in these runners already uses. The two tests below were
updated for that same conversion: they now extract the check function the
runner defines (`_run_unit_bare_tmp_cleanliness_check` /
`_run_integration_bare_tmp_cleanliness_check` and their listener
counterparts) rather than a top-level `if [[ $_CLEAN_EXIT -ne 0 ]]; then`
block, which no longer exists. The function itself still returns non-zero
and still prints the diagnostic guidance; what changed is that the CALLER
(gated_runner_run_quality_lint) no longer treats that non-zero return as
fatal — see team/tests/unit/test_gated_runner_lint.py for the
end-to-end warn+file+continue proof.

Rows 29 (env-bootstrap source abort) and 30 (require_dev_tree pre-flight
abort) are shared library functions already covered end-to-end by
test_env_bootstrap_bash.py and test_dev_tree_bash.py; this file adds
coverage only for the per-runner call sites that are not exercised there.

TEST-SAFETY NOTE: DELIBERATE NON-RECURSION
-------------------------------------------
Every test in this file forces the runner script to exit BEFORE it ever
invokes its own real pytest run (via an invalid flag, --help, or a
pytest-absent PATH).  None of these tests let a runner reach its own
"python3 -m pytest ..." line — doing so from inside this pytest process
would recursively re-run the whole suite (including this file).  The
non-deterministic pytest-failure remedy text and the two cleanliness-check
functions are therefore verified by extracting the real block/function
verbatim from the script (never retyped) and evaluating it in an isolated
bash subprocess with a synthetic PYTEST_EXIT value or a stubbed
pgai_tmp_cleanliness_check / pgai_listener_cleanliness_check — this
exercises the actual shipped text with no drift risk and no
recursion risk.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap

import pytest

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

_RUNNERS = [
    _SCRIPTS_DIR / "run-unit-tests.sh",
    _SCRIPTS_DIR / "run-integration-tests.sh",
    _SCRIPTS_DIR / "run-gated-tests.sh",
]
_RUNNER_IDS = [p.name for p in _RUNNERS]


# ---------------------------------------------------------------------------
# Helpers: invoking a runner script directly (early-exit paths only)
# ---------------------------------------------------------------------------


def _run_runner(
    script: pathlib.Path,
    args: list[str],
    tmp_path: pathlib.Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke *script* with *args* in an isolated, throwaway kanban root.

    PGAI_AGENT_KANBAN_ROOT_PATH points at a fresh tmp_path subdirectory (no
    kanban.cfg), so the dev-tree self-locate fallback resolves to this real
    checkout via the script's own on-disk location — never the live install.
    """
    safe_root = tmp_path / "safe_kanban_root"
    safe_root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(safe_root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(tmp_path / "temp")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _pytest_absent_path_env(tmp_path: pathlib.Path) -> dict[str, str]:
    """Build a PATH with no 'pytest' executable and a python3 stub that
    fails '-m pytest --version', simulating pytest genuinely not installed.

    All other python3 invocations delegate to the real interpreter so the
    runner's own bootstrap (ini_parser.sh, dev_tree.sh — pure bash, no
    python) is unaffected; the abort fires before any python3 lint runs.
    """
    stub_bin = tmp_path / "stub_bin"
    stub_bin.mkdir(parents=True, exist_ok=True)
    python3_stub = stub_bin / "python3"
    python3_stub.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [[ "$1" == "-m" && "$2" == "pytest" ]]; then
                exit 1
            fi
            exec /usr/bin/python3 "$@"
            """
        ),
        encoding="utf-8",
    )
    python3_stub.chmod(0o755)
    # Deliberately excludes any directory that could hold a real 'pytest'
    # executable (e.g. a venv's bin/) — only the stub dir and the base
    # system dirs are on PATH.
    return {"PATH": f"{stub_bin}:/usr/bin:/bin"}


# ---------------------------------------------------------------------------
# Helpers: extracting a conditional block verbatim from a runner script
# ---------------------------------------------------------------------------


def _find_line(lines: list[str], needle: str, start: int = 0) -> int:
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return i
    raise AssertionError(f"needle not found: {needle!r}")


def _find_fi(lines: list[str], start: int) -> int:
    for j in range(start, len(lines)):
        if lines[j].strip() == "fi":
            return j
    raise AssertionError(f"no closing 'fi' found starting at line {start}")


def _extract_block(path: pathlib.Path, start_needle: str, *, from_line: int = 0) -> str:
    """Extract one if/fi block (no nesting) verbatim, starting at the first
    line containing *start_needle* at or after *from_line*."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = _find_line(lines, start_needle, from_line)
    end = _find_fi(lines, start)
    return "\n".join(lines[start : end + 1])


def _extract_function_block(path: pathlib.Path, func_name: str) -> str:
    """Extract one `func_name() { ... }` function body verbatim.

    Used for the post-suite cleanliness check functions
    (`_run_unit_bare_tmp_cleanliness_check` and its siblings), which contain
    only a single `if ! ...; then ... fi` with no nested `{ }` pairs, so a
    plain "line 1 is the opener, first bare `}` line is the closer" scan is
    sufficient — no brace-depth counting needed.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    start = _find_line(lines, f"{func_name}() {{")
    for j in range(start + 1, len(lines)):
        if lines[j].strip() == "}":
            return "\n".join(lines[start : j + 1])
    raise AssertionError(
        f"no closing '}}' found for function {func_name!r} starting at line {start}"
    )


def _run_extracted_block(block: str, env_setup: str) -> subprocess.CompletedProcess:
    """Evaluate an extracted block in an isolated bash subprocess.

    *env_setup* pre-assigns the variable(s) the block reads (e.g.
    'PYTEST_EXIT=1').  Appends a sentinel echo after the block so tests can
    detect whether the block's own 'exit N' fired (sentinel absent) or the
    block fell through without exiting (sentinel present).
    """
    script = f"{env_setup}\n{block}\necho REACHED_AFTER_BLOCK\n"
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=10
    )


# ===========================================================================
# Unknown-argument abort — partial
# ===========================================================================


@pytest.mark.parametrize("script", _RUNNERS, ids=_RUNNER_IDS)
def test_unknown_argument_prints_full_remedy_block(
    script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """An unrecognized flag prints Cause(s)/To fix/Rule and names the flag."""
    result = _run_runner(script, ["--this-flag-does-not-exist"], tmp_path)

    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "--this-flag-does-not-exist" in result.stderr
    assert "Cause(s):" in result.stderr
    assert "To fix:" in result.stderr
    assert "Rule:" in result.stderr
    assert "--help" in result.stderr


@pytest.mark.parametrize("script", _RUNNERS, ids=_RUNNER_IDS)
def test_unknown_argument_remedys_help_command_actually_clears_the_gate(
    script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The remedy's first prescribed command (--help) succeeds when run.

    Per the gates-prescribe-remedies pattern: executing the printed command verbatim
    must clear the gate — here, '--help' is a real accepted flag, not the
    rejected one, so it exits 0 with the runner's usage text.
    """
    result = _run_runner(script, ["--help"], tmp_path)

    assert result.returncode == 0, (
        f"expected --help to exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "Usage:" in result.stdout


# ===========================================================================
# pytest-not-installed abort — full-remedy
# ===========================================================================


@pytest.mark.parametrize("script", _RUNNERS, ids=_RUNNER_IDS)
def test_pytest_not_installed_prints_full_remedy_block(
    script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """A pytest-absent environment aborts (exit 2) with a full remedy block."""
    env = _pytest_absent_path_env(tmp_path)
    result = _run_runner(script, [], tmp_path, extra_env=env)

    assert result.returncode == 2, (
        f"expected exit 2; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "Cause(s):" in result.stderr
    assert "To fix:" in result.stderr
    assert "pip install pytest" in result.stderr
    assert "Rule:" in result.stderr


@pytest.mark.parametrize("script", _RUNNERS, ids=_RUNNER_IDS)
def test_pytest_available_environment_does_not_trip_the_gate(
    script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Sanity check: with a PATH exposing the real pytest, the availability
    probe itself (isolated from the rest of the runner) passes — confirming
    'pip install pytest' is genuinely what clears this gate, without ever
    letting the runner reach its own real pytest invocation (see module
    docstring's non-recursion note)."""
    block = _extract_block(script, 'command -v pytest >/dev/null 2>&1')
    result = _run_extracted_block(block, env_setup="")

    assert "REACHED_AFTER_BLOCK" in result.stdout, (
        f"pytest-availability probe unexpectedly fired in a real pytest-installed "
        f"environment.\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert result.returncode == 0


# ===========================================================================
# pytest test-suite failure — non-deterministic exempt (row 33)
# ===========================================================================


def test_unit_runner_pytest_failure_names_next_diagnostic_step_not_a_fix() -> None:
    script = _SCRIPTS_DIR / "run-unit-tests.sh"
    block = _extract_block(script, "if [[ $PYTEST_EXIT -ne 0 ]]; then")
    result = _run_extracted_block(block, env_setup="PYTEST_EXIT=1")

    assert result.returncode == 1
    assert "REACHED_AFTER_BLOCK" not in result.stdout
    assert "Next diagnostic step" in result.stderr
    assert "FAILED" in result.stderr
    # Exempt from a fabricated fix-command block per the non-deterministic
    # exemption: a real assertion failure has no verbatim clearing command.
    assert "To fix:" not in result.stderr


def test_integration_runner_pytest_failure_names_next_diagnostic_step(
) -> None:
    script = _SCRIPTS_DIR / "run-integration-tests.sh"
    block = _extract_block(script, "if [[ $PYTEST_EXIT -eq 5 ]]; then")
    result = _run_extracted_block(block, env_setup="PYTEST_EXIT=1")

    assert result.returncode == 1
    assert "REACHED_AFTER_BLOCK" not in result.stdout
    assert "Next diagnostic step" in result.stderr
    assert "To fix:" not in result.stderr


def test_integration_runner_empty_suite_exit_5_is_still_treated_as_success(
) -> None:
    """Regression guard: the exit-5 (empty suite) branch is untouched by the
    remedy retrofit — it must still fall through without exiting non-zero."""
    script = _SCRIPTS_DIR / "run-integration-tests.sh"
    block = _extract_block(script, "if [[ $PYTEST_EXIT -eq 5 ]]; then")
    result = _run_extracted_block(block, env_setup="PYTEST_EXIT=5")

    assert "REACHED_AFTER_BLOCK" in result.stdout
    assert result.returncode == 0
    assert "empty suite treated as success" in result.stderr


def test_gated_runner_pytest_failure_names_next_diagnostic_step() -> None:
    script = _SCRIPTS_DIR / "run-gated-tests.sh"
    # Two sequential (non-nested) if-blocks handle PYTEST_EXIT; extract both
    # so a real (non-5, non-zero) exit code is evaluated exactly as the
    # shipped script evaluates it.
    lines = script.read_text(encoding="utf-8").splitlines()
    first_start = _find_line(lines, "if [[ $PYTEST_EXIT -eq 5 ]]; then")
    first_end = _find_fi(lines, first_start)
    second_start = _find_line(lines, "if [[ $PYTEST_EXIT -ne 0 ]]; then", first_end)
    second_end = _find_fi(lines, second_start)
    block = "\n".join(lines[first_start : second_end + 1])

    result = _run_extracted_block(block, env_setup="PYTEST_EXIT=1")

    assert result.returncode == 1
    assert "REACHED_AFTER_BLOCK" not in result.stdout
    assert "Next diagnostic step" in result.stderr
    assert "To fix:" not in result.stderr


def test_gated_runner_empty_suite_exit_5_is_still_treated_as_success() -> None:
    script = _SCRIPTS_DIR / "run-gated-tests.sh"
    lines = script.read_text(encoding="utf-8").splitlines()
    first_start = _find_line(lines, "if [[ $PYTEST_EXIT -eq 5 ]]; then")
    first_end = _find_fi(lines, first_start)
    second_start = _find_line(lines, "if [[ $PYTEST_EXIT -ne 0 ]]; then", first_end)
    second_end = _find_fi(lines, second_start)
    block = "\n".join(lines[first_start : second_end + 1])

    result = _run_extracted_block(block, env_setup="PYTEST_EXIT=5")

    assert result.returncode == 0
    assert "all skipped" in result.stdout


# ===========================================================================
# Bare-/tmp cleanliness assertion — CONVERT-class, warn+file+continue
# (docs/blocking-site-census.md row 52)
# ===========================================================================


def _bare_tmp_cleanliness_func_name(script: pathlib.Path) -> str:
    return (
        "_run_unit_bare_tmp_cleanliness_check"
        if script.name == "run-unit-tests.sh"
        else "_run_integration_bare_tmp_cleanliness_check"
    )


@pytest.mark.parametrize(
    "script",
    [_SCRIPTS_DIR / "run-unit-tests.sh", _SCRIPTS_DIR / "run-integration-tests.sh"],
    ids=["run-unit-tests.sh", "run-integration-tests.sh"],
)
def test_tmp_cleanliness_failure_points_to_the_residue_lines_above(
    script: pathlib.Path,
) -> None:
    """The check's own diagnostic still names the residue; the function
    itself still fails (returns 1) — but, per the CONVERT-class conversion
    in this task, only `return`s rather than `exit`ing, so the caller
    (gated_runner_run_quality_lint) can treat it as a non-fatal finding.
    See test_gated_runner_lint.py for the end-to-end continue proof."""
    func_name = _bare_tmp_cleanliness_func_name(script)
    block = _extract_function_block(script, func_name)
    script_text = (
        "pgai_tmp_cleanliness_check() { "
        'echo "[pgai-cleanliness] FAIL: kanban suite left residue in bare /tmp." >&2; '
        "return 1; }\n"
        '_TMP_SNAPSHOT_FILE="/dev/null"\n'
        f"{block}\n"
        f"{func_name}\n"
        'echo "FUNC_RETURNED=$?"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script_text], capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 0, (
        "the extracted function must never call `exit` itself — only "
        f"`return` — so a caller under `set -e` survives it.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "FUNC_RETURNED=1" in result.stdout
    assert "Next diagnostic step" in result.stderr
    assert "[pgai-cleanliness]" in result.stderr
    assert "To fix:" not in result.stderr


# ===========================================================================
# TCP listener cleanliness assertion — CONVERT-class, warn+file+continue
# (docs/blocking-site-census.md row 53)
# ===========================================================================


def _listener_cleanliness_func_name(script: pathlib.Path) -> str:
    return (
        "_run_unit_listener_cleanliness_check"
        if script.name == "run-unit-tests.sh"
        else "_run_integration_listener_cleanliness_check"
    )


@pytest.mark.parametrize(
    "script",
    [_SCRIPTS_DIR / "run-unit-tests.sh", _SCRIPTS_DIR / "run-integration-tests.sh"],
    ids=["run-unit-tests.sh", "run-integration-tests.sh"],
)
def test_listener_cleanliness_failure_points_to_the_listener_lines_above(
    script: pathlib.Path,
) -> None:
    """Same conversion proof as the bare-/tmp check above, for the listener
    cleanliness function."""
    func_name = _listener_cleanliness_func_name(script)
    block = _extract_function_block(script, func_name)
    script_text = (
        "pgai_listener_cleanliness_check() { "
        'echo "[pgai-listener] FAIL: framework-rooted TCP listeners survived the test suite." >&2; '
        "return 1; }\n"
        'PGAI_PROJECT_TEMP_SUBTREE="/dev/null"\n'
        '_LISTENER_SNAPSHOT_FILE="/dev/null"\n'
        f"{block}\n"
        f"{func_name}\n"
        'echo "FUNC_RETURNED=$?"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script_text], capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 0, (
        "the extracted function must never call `exit` itself — only "
        f"`return` — so a caller under `set -e` survives it.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "FUNC_RETURNED=1" in result.stdout
    assert "Next diagnostic step" in result.stderr
    assert "[pgai-listener]" in result.stderr
    assert "To fix:" not in result.stderr


# ===========================================================================
# bash -n syntax check on all three modified runners
# (also covered by the tree-wide test_shell_syntax_gate.py; kept here for a
#  fast, focused signal scoped to this task's modified files)
# ===========================================================================


@pytest.mark.parametrize("script", _RUNNERS, ids=_RUNNER_IDS)
def test_modified_runner_passes_bash_syntax_check(script: pathlib.Path) -> None:
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, (
        f"bash -n failed for {script}:\n{result.stderr}"
    )
