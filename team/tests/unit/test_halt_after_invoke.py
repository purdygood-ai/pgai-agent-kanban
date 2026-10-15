"""
test_halt_after_invoke.py
=========================
Behavioral unit tests for pgai_halt_after_run in
team/scripts/lib/halt_after_invoke.sh -- the shared wrapper the wake loop
and cm/release.sh's ship-time HALT-AFTER spend both call to run the
halt_after evaluator.

The contract under test is the wrapper's errexit behaviour: it returns the
evaluator's exit code and sets PGAI_HALT_AFTER_EXIT / PGAI_HALT_AFTER_OUTPUT,
and it leaves the caller's errexit (set -e) state exactly as it found it.
A caller running under ``set -euo pipefail`` can therefore capture a
not-yet-drained answer and keep running, and a caller that switched errexit
off around the call does not find it switched back on behind its back.

Each test sources the real library under bash and points KANBAN_ROOT at a
throwaway root holding a stub ``halt_after`` package whose ``__main__``
prints one line and exits with a chosen code, so the wrapper's own shell
behaviour is exercised without the real evaluator's drain logic.

The subprocess runs with its working directory set to that throwaway root:
``python3 -m`` puts the working directory first on sys.path, and team/
carries its own ``halt_after`` package that would otherwise shadow the stub.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import BashResult, run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib" / "halt_after_invoke.sh"

_STUB_LINE = "stub evaluator ran"

# Exit codes the evaluator uses for every non-promote answer: not yet
# drained, invalid token, sentinel absent, drained but deferred.
_NON_PROMOTE_EXIT_CODES = [1, 2, 3, 5]


def _diagnostic(result: BashResult) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _stub_root(tmp_path: pathlib.Path, exit_code: int) -> pathlib.Path:
    """Build a kanban root whose ``python3 -m halt_after`` exits *exit_code*."""
    root = tmp_path / "stub_root"
    package = root / "halt_after"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        f"import sys\nprint({_STUB_LINE!r})\nsys.exit({exit_code})\n",
        encoding="utf-8",
    )
    (root / "projects" / "probe").mkdir(parents=True)
    (root / "projects" / "probe" / "HALT-AFTER").write_text("coder\n", encoding="utf-8")
    return root


def _run(tmp_path: pathlib.Path, exit_code: int, body: str) -> tuple[BashResult, pathlib.Path]:
    """Source the library under ``set -euo pipefail`` and run *body*.

    *body* sees the stub root as ``$ROOT`` and its project as ``$PROJ``, and
    reports its observations with ``report`` -- which prints the call's
    captured code, both globals, and the errexit state at that point.
    """
    root = _stub_root(tmp_path, exit_code)
    script = (
        "set -euo pipefail\n"
        f'source "{_LIB}"\n'
        'PROJ="$ROOT/projects/probe"\n'
        "report() {\n"
        '  echo "rc=$1"\n'
        '  echo "exit=$PGAI_HALT_AFTER_EXIT"\n'
        '  echo "output=$PGAI_HALT_AFTER_OUTPUT"\n'
        '  case "$2" in *e*) echo "errexit=on" ;; *) echo "errexit=off" ;; esac\n'
        "}\n"
        f"{body}\n"
        'echo "reached-next-statement"\n'
    )
    result = run_bash(
        tmp_path,
        script,
        extra_env={"KANBAN_ROOT": str(root), "ROOT": str(root)},
        cwd=root,
    )
    return result, root


def _lines(result: BashResult) -> list[str]:
    return result.stdout.splitlines()


@pytest.mark.parametrize("exit_code", _NON_PROMOTE_EXIT_CODES)
def test_run_under_errexit_returns_evaluator_code_and_keeps_errexit_on(
    tmp_path: pathlib.Path, exit_code: int
) -> None:
    """Under set -euo pipefail the call returns the code and the shell runs on.

    The call is a statement of its own (not an ``if`` condition) that
    captures its status with ``|| rc=$?`` -- the shape cm/release.sh's spend
    step uses. The shell must reach the following statements, see the
    evaluator's code both as the return status and in PGAI_HALT_AFTER_EXIT,
    and still have errexit on afterwards.
    """
    result, _ = _run(
        tmp_path,
        exit_code,
        'rc=0\npgai_halt_after_run "$ROOT" "$PROJ" || rc=$?\nflags="$-"\nreport "$rc" "$flags"',
    )
    assert result.returncode == 0, _diagnostic(result)
    lines = _lines(result)
    assert f"rc={exit_code}" in lines, _diagnostic(result)
    assert f"exit={exit_code}" in lines, _diagnostic(result)
    assert f"output={_STUB_LINE}" in lines, _diagnostic(result)
    assert "errexit=on" in lines, _diagnostic(result)
    assert "reached-next-statement" in lines, _diagnostic(result)


@pytest.mark.parametrize("exit_code", _NON_PROMOTE_EXIT_CODES)
def test_run_with_errexit_off_returns_evaluator_code_and_keeps_errexit_off(
    tmp_path: pathlib.Path, exit_code: int
) -> None:
    """A caller that switched errexit off still has it off after the call.

    Starting from set -euo pipefail, the caller turns errexit off and makes
    a plain call with nothing capturing its status. The wrapper must not
    switch errexit back on: if it did, its own non-zero return would end
    the shell at the call and the following statements would never run.
    """
    result, _ = _run(
        tmp_path,
        exit_code,
        'set +e\npgai_halt_after_run "$ROOT" "$PROJ"\nrc=$?\nflags="$-"\nreport "$rc" "$flags"\nset -e',
    )
    assert result.returncode == 0, _diagnostic(result)
    lines = _lines(result)
    assert f"rc={exit_code}" in lines, _diagnostic(result)
    assert f"exit={exit_code}" in lines, _diagnostic(result)
    assert "errexit=off" in lines, _diagnostic(result)
    assert "reached-next-statement" in lines, _diagnostic(result)


def test_run_called_bare_inside_a_condition_checked_function_keeps_running(
    tmp_path: pathlib.Path,
) -> None:
    """The wake loop's shape: a plain call inside a function run from ``if``.

    The checker function calls the wrapper as a statement of its own and
    then returns the captured code; the shell runs it as an ``if``
    condition. A not-yet-drained answer must reach the checker's next
    statement with errexit still on, and the ``if`` must take its else
    branch.
    """
    result, _ = _run(
        tmp_path,
        1,
        "check() {\n"
        '  pgai_halt_after_run "$ROOT" "$PROJ"\n'
        '  local flags="$-"\n'
        '  report "$PGAI_HALT_AFTER_EXIT" "$flags"\n'
        '  return "$PGAI_HALT_AFTER_EXIT"\n'
        "}\n"
        'if check; then echo "branch=promoted"; else echo "branch=not-promoted"; fi',
    )
    assert result.returncode == 0, _diagnostic(result)
    lines = _lines(result)
    assert "rc=1" in lines, _diagnostic(result)
    assert "errexit=on" in lines, _diagnostic(result)
    assert "branch=not-promoted" in lines, _diagnostic(result)
    assert "reached-next-statement" in lines, _diagnostic(result)


def test_run_bare_call_on_promote_returns_zero_and_keeps_errexit_on(
    tmp_path: pathlib.Path,
) -> None:
    """A bare call that promotes (exit 0) under set -euo pipefail runs on.

    The promote answer is the only one a set -e caller may leave uncaptured;
    the wrapper must report 0 in PGAI_HALT_AFTER_EXIT, carry the evaluator's
    output in PGAI_HALT_AFTER_OUTPUT, and leave errexit on.
    """
    result, _ = _run(
        tmp_path,
        0,
        'pgai_halt_after_run "$ROOT" "$PROJ"\nrc=$?\nflags="$-"\nreport "$rc" "$flags"',
    )
    assert result.returncode == 0, _diagnostic(result)
    lines = _lines(result)
    assert "rc=0" in lines, _diagnostic(result)
    assert "exit=0" in lines, _diagnostic(result)
    assert f"output={_STUB_LINE}" in lines, _diagnostic(result)
    assert "errexit=on" in lines, _diagnostic(result)
    assert "reached-next-statement" in lines, _diagnostic(result)


def test_uncaptured_bare_call_under_errexit_ends_the_shell_on_non_promote(
    tmp_path: pathlib.Path,
) -> None:
    """An uncaptured non-zero call under set -euo pipefail ends the script.

    This pins the documented limit of the errexit contract: the wrapper
    leaves errexit alone, so the caller's own set -e acts on its return
    status like any other command. A bare call that gets a not-yet-drained
    answer (exit 1) and captures nothing terminates the shell with status 1
    at that line, and the following statements never run. A set -e caller
    must capture the status (``|| rc=$?``, an ``if`` condition, or its own
    ``set +e`` bracket).
    """
    result, _ = _run(
        tmp_path,
        1,
        'pgai_halt_after_run "$ROOT" "$PROJ"\necho "after-uncaptured-call"',
    )
    assert result.returncode == 1, _diagnostic(result)
    lines = _lines(result)
    assert "after-uncaptured-call" not in lines, _diagnostic(result)
    assert "reached-next-statement" not in lines, _diagnostic(result)
