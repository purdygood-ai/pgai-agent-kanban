"""
test_order_independence_seeded_shuffle_reproducible.py
========================================================
PGAI_TEST_ORDER=shuffle:<seed> (tests/conftest.py's
pytest_collection_modifyitems hook) must reproduce the same collection
order on two independent invocations of the same seed, using the plain
command line an operator debugging an order-coupled failure would type --
no extra flag required, even with a third-party ordering plugin
(pytest-randomly) active in the environment.

Without the hook's own canonicalisation step, pytest-randomly reorders the
collected item list before the hook ever sees it, so the same seed feeds a
different starting list on every invocation and produces a different
permutation each time. The hook canonicalises first (sorts the collected
items by nodeid) precisely so its output depends only on the requested seed
and the set of collected tests, never on what order an earlier plugin
handed it.

Coverage:
  - Behavioral (plugin-present case, skipped when pytest-randomly is not
    installed): the plain `PGAI_TEST_ORDER=shuffle:<seed>` invocation --
    with NO `-p no:randomly` and no other flag -- run twice against a tiny
    fixture suite with pytest-randomly active, produces the identical
    collection order both times.
  - A different seed against the same fixture produces a different order,
    proving the hook still shuffles rather than degrading into a fixed
    canonical order.

Test names describe behaviour under test (team/roles/TESTER.md, Test
Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]

_RANDOMLY_INSTALLED = importlib.util.find_spec("pytest_randomly") is not None

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

# The fixture's own conftest.py delegates to the real production hook
# (imported from tests/conftest.py) rather than reimplementing it, so this
# test exercises the actual seam an operator invokes, not a stand-in.
_FIXTURE_CONFTEST_MODULE = f'''
import sys

sys.path.insert(0, {str(_TEAM_DIR)!r})
from tests.conftest import pytest_collection_modifyitems  # noqa: F401
'''


def _seeded_collection_order(
    fixture_dir: pathlib.Path, workdir: pathlib.Path, label: str, seed: str
) -> list[str]:
    """Collect the fixture suite with PGAI_TEST_ORDER=shuffle:<seed> and no
    other ordering flag -- the exact command line an operator reproducing a
    divergence would type -- and return the per-test collection order.

    PYTEST_ADDOPTS and PYTEST_DEBUG_TEMPROOT are stripped from the child's
    environment for the same reason test_order_independence_plugin_
    neutralization.py's _run_fixture_pass strips them: this test runs
    inside the outer suite's own pytest session, which inherits an outer
    --basetemp pinned under the OUTER suite's temp root, and pytest refuses
    a --basetemp that is the current working directory or a parent of it.
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(fixture_dir),
        "--collect-only",
        "-q",
        f"--basetemp={workdir / f'basetemp_{label}'}",
        f"--rootdir={fixture_dir}",
    ]
    child_env = dict(os.environ)
    child_env.pop("PYTEST_ADDOPTS", None)
    child_env.pop("PYTEST_DEBUG_TEMPROOT", None)
    child_env["PGAI_TEST_ORDER"] = f"shuffle:{seed}"
    result = subprocess.run(
        cmd,
        cwd=fixture_dir,
        capture_output=True,
        text=True,
        timeout=180,
        env=child_env,
    )
    order = [
        line.strip()
        for line in result.stdout.splitlines()
        if "::" in line
    ]
    if not order:
        raise AssertionError(
            f"Nested pytest invocation collected no tests.\n"
            f"command: {' '.join(cmd)}\n"
            f"returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return order


@pytest.mark.skipif(
    not _RANDOMLY_INSTALLED,
    reason=(
        "pytest-randomly is not installed in this environment -- this test "
        "covers only the plugin-present case, which is exactly the "
        "condition under which an unpatched hook fails to reproduce a seed."
    ),
)
def test_same_shuffle_seed_reproduces_collection_order_with_randomly_active(
    tmp_path: pathlib.Path,
) -> None:
    """Two independent pytest sessions invoked with the same
    PGAI_TEST_ORDER=shuffle:<seed> and no other ordering flag must produce
    the identical collection order, with pytest-randomly active -- proving
    the hook's own canonicalisation neutralises the plugin's reordering
    rather than requiring the caller to pass -p no:randomly.
    """
    fixture_dir = tmp_path / "fixture_suite"
    fixture_dir.mkdir()
    (fixture_dir / "test_fixture_order.py").write_text(_FIXTURE_TEST_MODULE, encoding="utf-8")
    (fixture_dir / "conftest.py").write_text(_FIXTURE_CONFTEST_MODULE, encoding="utf-8")

    workdir = tmp_path / "voi_work"
    workdir.mkdir()

    order_1 = _seeded_collection_order(fixture_dir, workdir, "run1", "reproducible-seed")
    order_2 = _seeded_collection_order(fixture_dir, workdir, "run2", "reproducible-seed")

    assert order_1 == order_2, (
        "Two pytest sessions invoked with the same PGAI_TEST_ORDER=shuffle:"
        "<seed> and no other flag must produce the same collection order "
        "with pytest-randomly installed -- a mismatch means the hook is "
        "still shuffling whatever order the plugin handed it, rather than "
        "a canonicalised starting order."
    )


@pytest.mark.skipif(
    not _RANDOMLY_INSTALLED,
    reason=(
        "pytest-randomly is not installed in this environment -- this test "
        "covers only the plugin-present case; the seam's shuffle behavior "
        "itself (independent of any ordering plugin) is covered by "
        "test_suite_order_independence_hook.py."
    ),
)
def test_different_shuffle_seeds_produce_different_collection_orders_with_randomly_active(
    tmp_path: pathlib.Path,
) -> None:
    """The seam must still shuffle -- two different seeds against the same
    fixture must not collapse to the same (or a fixed canonical) order."""
    fixture_dir = tmp_path / "fixture_suite"
    fixture_dir.mkdir()
    (fixture_dir / "test_fixture_order.py").write_text(_FIXTURE_TEST_MODULE, encoding="utf-8")
    (fixture_dir / "conftest.py").write_text(_FIXTURE_CONFTEST_MODULE, encoding="utf-8")

    workdir = tmp_path / "voi_work"
    workdir.mkdir()

    order_a = _seeded_collection_order(fixture_dir, workdir, "seed_a", "first-seed")
    order_b = _seeded_collection_order(fixture_dir, workdir, "seed_b", "second-seed")

    assert order_a != order_b, (
        "Two different PGAI_TEST_ORDER=shuffle:<seed> values produced the "
        "same collection order -- the seam should still shuffle per-seed, "
        "not degrade into a fixed canonical order."
    )
