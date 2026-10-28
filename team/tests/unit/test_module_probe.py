"""
test_module_probe.py
====================
pgai_agent_kanban.lib.module_probe -- the import check install.sh asks
instead of an inline ``python3 -c 'import ...'``:

  1. missing_modules lists the names that fail to import, in order.
  2. The command line exits 0 (nothing printed) when every module imports,
     and 1 naming the first that does not on stderr.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

from pgai_agent_kanban.lib import module_probe
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]


def test_missing_modules_lists_only_the_names_that_fail_to_import() -> None:
    assert module_probe.missing_modules(["json", "no_such_module_for_probe", "re"]) == [
        "no_such_module_for_probe"
    ]


def _probe(tmp_path: pathlib.Path, *names: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    return subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.lib.module_probe", *names],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60, check=False,
    )


def test_probe_exits_zero_when_every_module_imports(tmp_path: pathlib.Path) -> None:
    result = _probe(tmp_path, "json", "re")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_probe_exits_one_naming_the_first_missing_module(tmp_path: pathlib.Path) -> None:
    result = _probe(tmp_path, "json", "no_such_module_for_probe")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "no_such_module_for_probe" in result.stderr
