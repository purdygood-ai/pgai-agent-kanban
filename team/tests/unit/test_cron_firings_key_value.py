"""
test_cron_firings_key_value.py
==============================
The flattening show-status-window.sh applies to the cron-firing JSON before
it fills its firing table: ``flatten_firings_json`` in
``pgai_agent_kanban.dashboard.cron_firings`` and the module's
``--key-value`` mode.

Covered behaviour:

  * a valid object yields one ``key=value`` line per entry in object order,
    with a non-string value rendered as Python's ``str()`` of it;
  * an empty object yields no lines;
  * invalid JSON, and JSON that is not an object, raise ``ValueError``;
  * the command line prints the lines and exits 0, prints nothing and
    exits 0 for an empty object, and prints nothing on stdout, one line on
    stderr and exits 1 for invalid JSON — which the pane's
    ``2>/dev/null || true`` turns into an empty table.

Subprocesses run through team/scripts/lib/pp_run_ops.sh, as the pane runs
the module, with an environment built from an explicit set and a throwaway
HOME.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.dashboard.cron_firings import flatten_firings_json
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"
_MODULE = "pgai_agent_kanban.dashboard.cron_firings"


def _run(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_path_dirs=[pathlib.Path(sys.executable).parent])
    return subprocess.run(
        ["bash", str(_PP_RUN_OPS), _MODULE, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


def test_valid_object_flattens_to_key_value_lines_in_order() -> None:
    text = '{"pm": "in 6 min", "coder": "now", "cleanup": "Sun 4am", "n": 3, "x": null}'
    assert flatten_firings_json(text) == [
        "pm=in 6 min",
        "coder=now",
        "cleanup=Sun 4am",
        "n=3",
        "x=None",
    ]


def test_empty_object_flattens_to_no_lines() -> None:
    assert flatten_firings_json("{}") == []


@pytest.mark.parametrize("text", ["not json", '{"pm": ', "", '["pm", "coder"]', '"pm"'])
def test_invalid_or_non_object_json_raises_value_error(text: str) -> None:
    with pytest.raises(ValueError):
        flatten_firings_json(text)


def test_cli_prints_one_line_per_entry(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, "--key-value", '{"pm": "in 6 min", "coder": "in 1 min"}')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "pm=in 6 min\ncoder=in 1 min\n"


def test_cli_prints_nothing_for_an_empty_object(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, "--key-value", "{}")
    assert (result.returncode, result.stdout) == (0, ""), result.stderr


def test_cli_exits_one_with_empty_stdout_for_invalid_json(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, "--key-value", "{not json")
    assert (result.returncode, result.stdout) == (1, "")
    assert "--key-value" in result.stderr and len(result.stderr.splitlines()) == 1
