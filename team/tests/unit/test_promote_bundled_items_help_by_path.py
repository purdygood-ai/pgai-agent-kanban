"""
test_promote_bundled_items_help_by_path.py
==========================================
team/scripts/cm/promote_bundled_items.py is run by file path -- by
lib/cm/release_finalize.sh, by cm/finalize.sh's ``--doc-mode`` step, and by
the doc-truth lint's ``--help`` probe -- so it must locate its own
``pgai_agent_kanban`` package without the caller putting it on PYTHONPATH.

Covered:
  - ``python3 -I <script> --help`` from an unrelated working directory, with
    no PYTHONPATH, exits 0 and prints the usage lines on stdout with no
    traceback;
  - ``-h`` behaves the same;
  - with no arguments the script still prints its usage on stderr and exits 1
    (the help flag is the only route to exit 0 without work).

Every child runs with an environment built from an explicit set (no
PYTHONPATH) and a scratch HOME under tmp_path.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PROMOTE_SCRIPT = _TEAM_DIR / "scripts" / "cm" / "promote_bundled_items.py"


def _run_by_path(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Run the script by file path in isolated mode from a scratch directory."""
    cwd = tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(home_dir=tmp_path / "home")
    assert "PYTHONPATH" not in env
    return subprocess.run(
        [sys.executable, "-I", str(_PROMOTE_SCRIPT), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_by_path_without_pythonpath_exits_zero_with_usage(tmp_path, flag):
    result = _run_by_path(tmp_path, flag)
    assert result.returncode == 0, (
        f"{_PROMOTE_SCRIPT.name} {flag} exited {result.returncode}; "
        f"stderr:\n{result.stderr}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    assert "Usage" in result.stdout, result.stdout
    assert "promote_bundled_items.py <bundle_file> <version>" in result.stdout
    assert "--doc-mode <req_dir> <semver>" in result.stdout


def test_no_arguments_by_path_is_still_a_usage_error(tmp_path):
    result = _run_by_path(tmp_path)
    assert result.returncode == 1, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert "Usage:" in result.stderr, result.stderr
