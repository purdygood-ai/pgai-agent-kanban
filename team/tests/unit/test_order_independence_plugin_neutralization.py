"""
test_order_independence_plugin_neutralization.py
==================================================
verify-test-order-independence.sh's two passes must both start from a
stated, reproducible order even when a third-party ordering plugin
(pytest-randomly) is active in the environment.

Without plugin control, `_voi_run()`'s `python3 -m pytest $paths ...`
invocation lets pytest-randomly reorder the collected item list before
tests/conftest.py's own `pytest_collection_modifyitems` hook runs, so
neither the "forward" pass (PGAI_TEST_ORDER unset) nor the "reverse" pass
(PGAI_TEST_ORDER=reverse) actually starts from the order its label
claims, and the two orders are not even stable across repeated
invocations of the same label.

`-p no:randomly` on the pytest invocation neutralises pytest-randomly (or
any other plugin registered under that exact name) and is a documented
no-op when the plugin is absent (the container hosts built by
run-three-host-matrix.sh install only requirements-test.txt, which does
not declare pytest-randomly).

Coverage:
  - Static: the exact pytest invocation line in _voi_run() still carries
    `-p no:randomly` -- catches a future edit that silently drops the flag.
  - Behavioral (plugin-present case, skipped when pytest-randomly is not
    installed): the literal command shape `_voi_run()` uses, run twice
    against a tiny fixture suite with pytest-randomly active, produces the
    identical per-test execution order both times, and that order matches
    the fixture's own file-declaration order -- proving the neutralisation
    is real, not a coincidence of two random seeds agreeing.

Test names describe behaviour under test (team/roles/TESTER.md, Test
Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_VOI_SCRIPT = _TEAM_DIR / "scripts" / "verify-test-order-independence.sh"

_RANDOMLY_INSTALLED = importlib.util.find_spec("pytest_randomly") is not None


def test_voi_run_invokes_pytest_with_randomly_plugin_disabled() -> None:
    """_voi_run()'s pytest invocation line must carry `-p no:randomly`.

    Static guard: catches a future edit that drops the flag (or renames it
    to something pytest no longer recognises) without needing to pay the
    cost of a full nested pytest run for every ordinary unit-test pass.
    """
    text = _VOI_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"python3 -m pytest\s+(-p no:randomly)\s+\$paths", text)
    assert match is not None, (
        "verify-test-order-independence.sh's _voi_run() must invoke pytest "
        "as `python3 -m pytest -p no:randomly $paths ...` -- the flag "
        "neutralises pytest-randomly so the forward/reverse orders it "
        "diffs are the orders it claims to diff."
    )


def _run_fixture_pass(fixture_dir: pathlib.Path, workdir: pathlib.Path, label: str) -> list[str]:
    """Run the exact pytest invocation shape _voi_run() uses against a tiny
    fixture suite and return the per-test execution order from the
    resulting junit-xml report (testcase elements are emitted in the order
    they ran).

    PYTEST_ADDOPTS and PYTEST_DEBUG_TEMPROOT are stripped from the child's
    environment. This test runs inside the suite's own pytest session, which
    inherits run-unit-tests.sh's PYTEST_ADDOPTS (a --basetemp pinned under
    the OUTER suite's temp root). Left in place, the nested pytest below
    would receive that inherited --basetemp on top of its own explicit one,
    and pytest refuses a --basetemp that is the current working directory or
    a parent of it -- exactly the collision this fixture's own cwd sits in.
    The same seam is used for the same reason in
    scripts/harness/helloworld-flow-check.sh's own nested pytest call.

    A generous timeout and a full diagnostic on a missing report (command,
    exit code, stdout, stderr) keep a failure actionable on a loaded host
    instead of surfacing a bare FileNotFoundError from the xml parse.
    """
    junit_path = workdir / f"{label}.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:randomly",
        str(fixture_dir),
        "-q",
        f"--basetemp={workdir / f'basetemp_{label}'}",
        f"--junitxml={junit_path}",
    ]
    child_env = dict(os.environ)
    child_env.pop("PYTEST_ADDOPTS", None)
    child_env.pop("PYTEST_DEBUG_TEMPROOT", None)
    result = subprocess.run(
        cmd,
        cwd=fixture_dir,
        capture_output=True,
        text=True,
        timeout=180,
        env=child_env,
    )
    if not junit_path.is_file():
        raise AssertionError(
            f"Nested pytest invocation produced no junit-xml report at {junit_path}.\n"
            f"command: {' '.join(cmd)}\n"
            f"returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    tree = ET.parse(junit_path)
    return [
        f'{tc.get("classname")}::{tc.get("name")}' for tc in tree.getroot().iter("testcase")
    ]


_FIXTURE_TEST_MODULE = '''
def test_a():
    assert True


def test_b():
    assert True


def test_c():
    assert True


def test_d():
    assert True


def test_e():
    assert True
'''


@pytest.mark.skipif(
    not _RANDOMLY_INSTALLED,
    reason=(
        "pytest-randomly is not installed in this environment -- this test "
        "covers only the plugin-present case; -p no:randomly's absent-plugin "
        "no-op path is exercised structurally by the flag being harmless to "
        "pass regardless."
    ),
)
def test_no_randomly_flag_neutralizes_active_randomly_plugin(tmp_path: pathlib.Path) -> None:
    """With pytest-randomly active, two independent pytest sessions invoked
    with `-p no:randomly` (the exact flag _voi_run() passes) must produce
    the identical per-test execution order, and that order must match the
    fixture module's own declaration order -- proving the plugin was
    actually neutralised rather than two random seeds happening to agree.
    """
    fixture_dir = tmp_path / "fixture_suite"
    fixture_dir.mkdir()
    (fixture_dir / "test_fixture_order.py").write_text(_FIXTURE_TEST_MODULE, encoding="utf-8")

    workdir = tmp_path / "voi_work"
    workdir.mkdir()

    order_1 = _run_fixture_pass(fixture_dir, workdir, "run1")
    order_2 = _run_fixture_pass(fixture_dir, workdir, "run2")

    assert order_1 == order_2, (
        "Two pytest sessions invoked with -p no:randomly must produce the "
        "same execution order with pytest-randomly installed -- a mismatch "
        "means the flag is no longer neutralising the plugin."
    )

    declared_order = ["test_a", "test_b", "test_c", "test_d", "test_e"]
    actual_short_names = [node_id.rsplit("::", 1)[-1] for node_id in order_1]
    assert actual_short_names == declared_order, (
        "With pytest-randomly neutralised, execution order must match the "
        "fixture module's file-declaration order -- pytest's own default "
        "collection order with no reordering plugin active."
    )
