"""
test_lib_hook_timeout.py
========================
Behavioural tests for pgai_agent_kanban.lib.hook_timeout, the one home of
the kanban.cfg ``[release] hook_timeout_seconds`` default: the bash accessor
(config_loader.sh's ``config_release_hook_timeout_seconds_default``, which
runs the module through pp_run_ops) and the Python readers report the value
the settings function returns, and the command line prints it.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.cm import ship
from pgai_agent_kanban.lib import hook_timeout
from tests.unit.shell_harness import run_bash


def test_the_default_is_a_positive_whole_number() -> None:
    seconds = hook_timeout.hook_timeout_default_seconds()

    assert isinstance(seconds, int) and seconds > 0


def test_ship_rc_reads_the_settings_function() -> None:
    assert ship.DEFAULT_HOOK_TIMEOUT_SECONDS == hook_timeout.hook_timeout_default_seconds()


def test_command_line_prints_the_default(capsys: pytest.CaptureFixture[str]) -> None:
    assert hook_timeout.main(["default"]) == 0
    assert capsys.readouterr().out == f"{hook_timeout.hook_timeout_default_seconds()}\n"


@pytest.mark.parametrize("argv", [[], ["seconds"]])
def test_command_line_refuses_anything_but_default(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        hook_timeout.main(argv)
    assert caught.value.code == 2


def test_config_loader_accessor_prints_the_settings_function_value(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path, "source scripts/lib/config_loader.sh && config_release_hook_timeout_seconds_default"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{hook_timeout.hook_timeout_default_seconds()}\n"


def test_config_loader_accessor_fails_loud_when_the_module_cannot_run(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path,
        "source scripts/lib/config_loader.sh\n"
        "pp_run_ops() { return 1; }\n"
        "config_release_hook_timeout_seconds_default",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "pgai_agent_kanban.lib.hook_timeout" in result.stderr
