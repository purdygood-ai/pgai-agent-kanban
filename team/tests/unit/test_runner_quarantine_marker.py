"""
test_runner_quarantine_marker.py
================================
Behavioural tests for the ``quarantine`` pytest marker and the gated
runners' marker selection (run-unit-tests.sh and run-integration-tests.sh).

WHAT THE MARKER PROMISES
------------------------
- ``quarantine`` is a registered marker in team/pyproject.toml, so
  ``--strict-markers`` accepts it;
- every default run of either runner deselects quarantined tests
  (``-m "not quarantine"``), and a ``--files`` targeted run honours the same
  exclusion;
- ``--quarantined`` runs exactly the quarantined tests and nothing else,
  alone or combined with ``--files``;
- a targeted run whose named files hold only quarantined tests exits 0 with
  a narration rather than failing on pytest's "no tests ran" exit 5;
- the printed "Replay this exact run:" line keeps the multi-word ``-m``
  expression as one argument, so pasting it reproduces the same selection;
- ``--help`` documents ``--quarantined``;
- every ``quarantine`` mark in team/tests/ carries a non-empty ``reason``.

WHY A FIXTURE TREE
------------------
Same reasoning as test_runner_targeted_files.py, whose fixture builder and
sandbox this module reuses: running a real runner against the real
team/tests/ tree from inside this pytest process would recursively re-run
the suite. Each case gets a throwaway REPO_ROOT-shaped tree holding the
REAL runner, the REAL scripts/lib/ and the REAL team/pyproject.toml, plus
stub gates, and a small set of fixture test files whose names say which
selection they belong to.
"""

from __future__ import annotations

import ast
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.test_runner_targeted_files import (
    _RUNNER_CONFIG,
    _RUNNER_NAMES,
    _Sandbox,
    _build_fixture,
    _describe,
    _exit_file,
)

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_PYPROJECT = _TEAM_DIR / "pyproject.toml"

# Fixture test files written into each runner's test root. Names carry the
# selection each test belongs to, so an output line naming one is
# unambiguous evidence of what was collected.
_MIXED_FILE = "test_mixed_selection.py"
_MIXED_SOURCE = (
    "import pytest\n"
    "\n"
    "def test_plain_case_runs_by_default():\n"
    "    assert True\n"
    "\n"
    "@pytest.mark.quarantine(reason='timing-dependent fixture case')\n"
    "def test_quarantined_case_in_mixed_file():\n"
    "    assert True\n"
)
_ALL_QUARANTINED_FILE = "test_all_quarantined.py"
_ALL_QUARANTINED_SOURCE = (
    "import pytest\n"
    "\n"
    "pytestmark = pytest.mark.quarantine(reason='order-dependent fixture module')\n"
    "\n"
    "def test_quarantined_case_in_quarantined_module():\n"
    "    assert True\n"
)

_PLAIN = "test_plain_case_runs_by_default"
_QUARANTINED = ["test_quarantined_case_in_mixed_file", "test_quarantined_case_in_quarantined_module"]


def _build_marker_fixture(tmp_path: pathlib.Path, runner_name: str) -> pathlib.Path:
    """Ticket fixture tree plus the real pyproject.toml and the marker files.

    The deliberately failing test_other.py from the shared builder is
    removed so a full (non --files) run can pass; test_named.py stays as a
    second unmarked test.
    """
    team = _build_fixture(tmp_path, runner_name)
    shutil.copy2(_PYPROJECT, team / "pyproject.toml")
    test_dir = team / _RUNNER_CONFIG[runner_name]["test_dir"]
    (test_dir / "test_other.py").unlink()
    (test_dir / _MIXED_FILE).write_text(_MIXED_SOURCE, encoding="utf-8")
    (test_dir / _ALL_QUARANTINED_FILE).write_text(_ALL_QUARANTINED_SOURCE, encoding="utf-8")
    return team


def _rel(runner_name: str, filename: str) -> str:
    return f"{_RUNNER_CONFIG[runner_name]['test_dir']}/{filename}"


def _passed_names(stdout: str) -> set[str]:
    """Test function names pytest --verbose reported as PASSED."""
    names = set()
    for line in stdout.splitlines():
        if " PASSED" in line and "::" in line:
            names.add(line.split("::", 1)[1].split(" ", 1)[0])
    return names


def _replay_line(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("Replay this exact run: "):
            return line.removeprefix("Replay this exact run: ")
    raise AssertionError("runner printed no 'Replay this exact run:' line\n" + stdout)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_quarantine_marker_is_registered_so_strict_markers_accepts_it(tmp_path: pathlib.Path) -> None:
    shutil.copy2(_PYPROJECT, tmp_path / "pyproject.toml")
    (tmp_path / _MIXED_FILE).write_text(_MIXED_SOURCE, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    # Built from an explicit base: a PYTEST_ADDOPTS inherited from the
    # runner that launched this suite would point this inner pytest at the
    # outer session's basetemp.
    env = build_scrubbed_env(home_dir=home, extra_path_dirs=[pathlib.Path(sys.executable).parent])

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", "--strict-markers",
         "--collect-only", "-q", "-m", "quarantine", _MIXED_FILE],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        env=env,
    )

    assert result.returncode == 0, (
        "pytest --strict-markers refused the quarantine marker: it must be "
        "registered under [tool.pytest.ini_options] markers in team/pyproject.toml\n"
        + _describe(result)
    )
    assert f"{_MIXED_FILE}::test_quarantined_case_in_mixed_file" in result.stdout, _describe(result)
    assert _PLAIN not in result.stdout, _describe(result)


def _quarantine_marks_missing_a_reason(path: pathlib.Path) -> list[str]:
    """Each pytest.mark.quarantine use in *path* without a non-empty
    string-literal reason= keyword, as 'path:line'."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    # ast.walk visits a call's .func attribute node on its own as well; the
    # call already accounts for it, so it must not also count as a bare mark.
    call_funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    problems = []
    for node in ast.walk(tree):
        if id(node) in call_funcs:
            continue
        target = node.func if isinstance(node, ast.Call) else node
        if not (isinstance(target, ast.Attribute) and target.attr == "quarantine"):
            continue
        if not (isinstance(target.value, ast.Attribute) and target.value.attr == "mark"):
            continue
        if not isinstance(node, ast.Call):
            problems.append(f"{path}:{node.lineno} (bare mark, no reason)")
            continue
        reason = next((kw.value for kw in node.keywords if kw.arg == "reason"), None)
        text = None
        if isinstance(reason, ast.Constant) and isinstance(reason.value, str):
            text = reason.value
        elif isinstance(reason, ast.JoinedStr):
            text = "f-string"
        if not text or not text.strip():
            problems.append(f"{path}:{node.lineno}")
    return problems


def test_every_quarantine_mark_in_the_test_tree_names_its_reason() -> None:
    """A quarantined test is excluded from certification, so the mark must
    say why -- which order or timing dependence -- for whoever fixes it."""
    problems = []
    scanned = 0
    for path in sorted((_TEAM_DIR / "tests").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "mark.quarantine" not in text:
            continue
        scanned += 1
        problems.extend(_quarantine_marks_missing_a_reason(path))

    assert scanned > 0, "no test module carries a quarantine mark; the scan found nothing to check"
    assert not problems, (
        "every @pytest.mark.quarantine needs reason='<the order or timing "
        "dependence>'; missing or empty at:\n  " + "\n  ".join(problems)
    )


# ---------------------------------------------------------------------------
# Runner selection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_default_run_deselects_every_quarantined_test(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_marker_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--verbose")

    assert result.returncode == 0, _describe(result)
    passed = _passed_names(result.stdout)
    assert passed == {_PLAIN, "test_named_passes"}, (
        f"a default run must run every unmarked test and no quarantined one; ran {sorted(passed)}\n"
        + _describe(result)
    )
    assert "2 deselected" in result.stdout, _describe(result)
    assert 'Marker selection: -m "not quarantine"' in result.stdout, _describe(result)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_quarantined_flag_runs_exactly_the_quarantined_tests(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_marker_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--quarantined", "--verbose")

    assert result.returncode == 0, _describe(result)
    passed = _passed_names(result.stdout)
    assert passed == set(_QUARANTINED), (
        f"--quarantined must run exactly the quarantined tests; ran {sorted(passed)}\n" + _describe(result)
    )
    assert 'Marker selection: -m "quarantine"' in result.stdout, _describe(result)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_honours_the_default_exclusion(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_marker_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _rel(runner_name, _MIXED_FILE), "--verbose")

    assert result.returncode == 0, _describe(result)
    assert _passed_names(result.stdout) == {_PLAIN}, _describe(result)
    assert "1 deselected" in result.stdout, _describe(result)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_with_quarantined_runs_only_the_quarantined_tests_in_the_named_files(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    team = _build_marker_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(
        team, runner_name, "--files", _rel(runner_name, _MIXED_FILE), "--quarantined", "--verbose"
    )

    assert result.returncode == 0, _describe(result)
    assert _passed_names(result.stdout) == {"test_quarantined_case_in_mixed_file"}, _describe(result)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_of_a_fully_quarantined_file_exits_zero(runner_name: str, tmp_path: pathlib.Path) -> None:
    """Every test in the named file is quarantined, so the default exclusion
    leaves nothing to run. pytest reports exit 5; the runner narrates it and
    exits 0, and the targeted exit file records 0."""
    team = _build_marker_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _rel(runner_name, _ALL_QUARANTINED_FILE))

    assert result.returncode == 0, _describe(result)
    assert "1 deselected" in result.stdout, _describe(result)
    assert "passed" not in result.stdout.split("Replay this exact run:")[-1], _describe(result)
    assert _exit_file(sandbox, runner_name).read_text(encoding="utf-8") == "0\n"


def test_unit_certification_run_that_collects_nothing_still_fails(tmp_path: pathlib.Path) -> None:
    """Control for the exit-5 allowance above: it applies to marker-filtered
    runs only. A full default unit run whose test roots hold no test at all
    is still a failure."""
    team = _build_fixture(tmp_path, "run-unit-tests.sh")
    shutil.copy2(_PYPROJECT, team / "pyproject.toml")
    for path in (team / "tests" / "unit").glob("test_*.py"):
        path.unlink()
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, "run-unit-tests.sh")

    assert result.returncode != 0, _describe(result)
    assert "no test matched the marker selection" not in result.stdout, _describe(result)


# ---------------------------------------------------------------------------
# Replay line and --help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
@pytest.mark.parametrize(
    ("extra", "expected_passed"),
    [
        pytest.param([], {_PLAIN, "test_named_passes"}, id="default"),
        pytest.param(["--quarantined"], set(_QUARANTINED), id="quarantined"),
    ],
)
def test_pasted_replay_line_reproduces_the_marker_selection(
    runner_name: str, extra: list[str], expected_passed: set[str], tmp_path: pathlib.Path
) -> None:
    team = _build_marker_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--verbose", *extra)
    assert result.returncode == 0, _describe(result)
    replay = _replay_line(result.stdout)

    replayed = subprocess.run(
        ["bash", "-c", replay],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PATH": f"{pathlib.Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(sandbox.home),
        },
    )

    assert replayed.returncode == 0, f"replay command: {replay}\n" + _describe(replayed)
    assert _passed_names(replayed.stdout) == expected_passed, (
        f"the pasted replay line must select the same tests the runner ran; "
        f"replay command: {replay}\n" + _describe(replayed)
    )


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_help_documents_quarantined_flag(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--help")

    assert result.returncode == 0, _describe(result)
    assert "--quarantined" in result.stdout, _describe(result)
    assert '-m "not quarantine"' in result.stdout, _describe(result)
