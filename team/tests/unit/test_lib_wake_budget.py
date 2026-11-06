"""
test_lib_wake_budget.py
=======================
Behavioural tests for pgai_agent_kanban.lib.wake_budget, the one home of the
kanban.cfg ``[wake] max_task_seconds`` and ``[wake] max_runtime_seconds``
defaults: the bash accessors (config_loader.sh's
``config_wake_max_task_seconds_default`` and
``config_wake_max_runtime_seconds_default``, which run the module through
pp_run_ops), the lock reaper's chain budget, the attention pane's scanner
and the dashboard pane script all report the module's values, and the
command line prints them.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

from pgai_agent_kanban.dashboard import scan_attention
from pgai_agent_kanban.lib import wake_budget
from pgai_agent_kanban.ops import locks
from pgai_agent_kanban.ops.context import OpsContext
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_DEFAULTS = {
    "max_task_seconds": wake_budget.MAX_TASK_SECONDS_DEFAULT,
    "max_runtime_seconds": wake_budget.MAX_RUNTIME_SECONDS_DEFAULT,
}


def test_the_defaults_are_positive_whole_numbers_nesting_task_inside_batch() -> None:
    assert all(isinstance(seconds, int) and seconds > 0 for seconds in _DEFAULTS.values())
    assert wake_budget.MAX_TASK_SECONDS_DEFAULT < wake_budget.MAX_RUNTIME_SECONDS_DEFAULT


@pytest.mark.parametrize("key", sorted(_DEFAULTS))
def test_command_line_prints_the_default(key: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert wake_budget.main(["default", key]) == 0
    assert capsys.readouterr().out == f"{_DEFAULTS[key]}\n"


@pytest.mark.parametrize("argv", [[], ["default"], ["default", "max_project_seconds"], ["seconds", "max_task_seconds"]])
def test_command_line_refuses_anything_else(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        wake_budget.main(argv)
    assert caught.value.code == 2


@pytest.mark.parametrize("key", sorted(_DEFAULTS))
def test_config_loader_accessor_prints_the_module_value(key: str, tmp_path: pathlib.Path) -> None:
    result = run_bash(tmp_path, f"source scripts/lib/config_loader.sh && config_wake_{key}_default")

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{_DEFAULTS[key]}\n"


@pytest.mark.parametrize("key", sorted(_DEFAULTS))
def test_config_loader_accessor_fails_loud_when_the_module_cannot_run(key: str, tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path,
        f"source scripts/lib/config_loader.sh\npp_run_ops() {{ return 1; }}\nconfig_wake_{key}_default",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "pgai_agent_kanban.lib.wake_budget" in result.stderr


_ENV_NAMES = {"max_runtime_seconds": "MAX_RUNTIME_SECONDS", "max_task_seconds": "MAX_TASK_SECONDS"}

# Sourced before load_config: a pp_run_ops that cannot run stands in for a
# loader whose pp_run_ops.sh or Python package is unreachable, so the
# [wake] default accessors fail the way they do in that install.
_ACCESSOR_DOWN = "pp_run_ops() { return 1; }"


def _load_config_with_accessor_down(tmp_path: pathlib.Path, cfg_text: str, **env: str):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    cfg = root / "kanban.cfg"
    cfg.write_text(cfg_text, encoding="utf-8")
    base_env = build_scrubbed_env(
        home_dir=tmp_path,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp"),
            **env,
        },
    )
    script = (
        f"source scripts/lib/config_loader.sh\n{_ACCESSOR_DOWN}\n"
        f"load_config '{cfg}' && echo \"RUNTIME=$MAX_RUNTIME_SECONDS TASK=$MAX_TASK_SECONDS\""
    )
    return cfg, run_bash(tmp_path, script, base_env=base_env)


def _contract(stderr: str) -> dict[str, str]:
    """The three dual-channel fields of the refusal on *stderr*."""
    fields: dict[str, str] = {}
    for line in stderr.splitlines():
        if m := re.match(r"^(error|message|remedy):\s+(.*)$", line):
            fields[m.group(1)] = m.group(2)
    return fields


@pytest.mark.parametrize(
    ("key", "supplied"),
    [("max_runtime_seconds", {}), ("max_task_seconds", {"MAX_RUNTIME_SECONDS": "14400"})],
)
def test_load_config_refuses_naming_the_unreadable_default(tmp_path: pathlib.Path, key: str, supplied: dict) -> None:
    """Nothing sets the key and its default cannot be read: the refusal names
    the default, its accessor and the command that checks it -- not a
    kanban.cfg value -- and its remedy writes no value at all."""
    cfg, result = _load_config_with_accessor_down(tmp_path, "[paths]\n", **supplied)

    assert result.returncode == 1, result.stdout + result.stderr
    fields = _contract(result.stderr)
    assert fields.get("error") == "config_loader.wake.default_unreadable", result.stderr
    assert f"the [wake] {key} default could not be read" in fields["message"], fields["message"]
    assert f"config_wake_{key}_default" in fields["message"], fields["message"]
    assert "is ''" not in fields["message"], fields["message"]
    assert fields["remedy"].endswith(f"&& config_wake_{key}_default'"), fields["remedy"]
    assert "sed" not in fields["remedy"] and str(cfg) not in fields["remedy"], fields["remedy"]
    assert "config_loader.wake.not_an_integer" not in result.stderr, result.stderr

    # The remedy is the check itself: run as printed in a working tree, it prints the default.
    check = run_bash(tmp_path, fields["remedy"])
    assert check.returncode == 0, check.stderr
    assert check.stdout == f"{_DEFAULTS[key]}\n"


@pytest.mark.parametrize(
    ("cfg_text", "env"),
    [
        pytest.param("[wake]\nmax_runtime_seconds = 7200\nmax_task_seconds = 600\n", {}, id="kanban.cfg"),
        pytest.param("[paths]\n", {"MAX_RUNTIME_SECONDS": "7200", "MAX_TASK_SECONDS": "600"}, id="environment"),
    ],
)
def test_load_config_needs_no_accessor_when_both_keys_are_set(tmp_path: pathlib.Path, cfg_text: str, env: dict) -> None:
    """An explicit setting loads without the default accessor: rc 0, the
    set values exported, and no accessor error even though it cannot run."""
    _cfg, result = _load_config_with_accessor_down(tmp_path, cfg_text, **env)

    assert result.returncode == 0, result.stderr
    assert "RUNTIME=7200 TASK=600" in result.stdout, result.stdout
    assert "wake_budget" not in result.stderr, result.stderr


@pytest.mark.parametrize(
    ("cfg_text", "env", "remedy"),
    [
        pytest.param(
            "[wake]\nmax_runtime_seconds = six-hours\n", {}, "sed -i '/^[[:space:]]*max_runtime_seconds", id="cfg"
        ),
        pytest.param("[paths]\n", {"MAX_RUNTIME_SECONDS": "abc"}, "unset MAX_RUNTIME_SECONDS", id="env"),
    ],
)
def test_bad_value_with_no_readable_default_removes_the_setting(
    tmp_path: pathlib.Path, cfg_text: str, env: dict, remedy: str
) -> None:
    """A value with no leading digits normally suggests the default; when the
    default cannot be read, the remedy removes the setting instead of
    writing an empty value."""
    _cfg, result = _load_config_with_accessor_down(tmp_path, cfg_text, **env)

    assert result.returncode == 1
    fields = _contract(result.stderr)
    assert fields.get("error") == "config_loader.wake.not_an_integer", result.stderr
    assert fields["remedy"].startswith(remedy), fields["remedy"]
    assert "max_runtime_seconds = /" not in fields["remedy"] and not fields["remedy"].endswith("="), fields["remedy"]


def test_lock_chain_budget_falls_back_to_the_module_values(tmp_path: pathlib.Path, monkeypatch) -> None:
    for name in ("MAX_RUNTIME_SECONDS", "MAX_TASK_SECONDS", "KILL_GRACE_SECONDS"):
        monkeypatch.delenv(name, raising=False)

    floor = locks.chain_budget_floor_seconds(OpsContext(kanban_root=tmp_path))

    assert floor == (
        wake_budget.MAX_RUNTIME_SECONDS_DEFAULT
        + wake_budget.MAX_TASK_SECONDS_DEFAULT
        + locks.DEFAULT_KILL_GRACE_SECONDS
        + locks.CHAIN_BUDGET_MARGIN_SECONDS
    )


def test_stale_working_scanner_defaults_to_the_module_value(
    tmp_path: pathlib.Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    task = tmp_path / "tasks" / "T1"
    task.mkdir(parents=True)
    (task / "status.md").write_text("# Status\n\n## State\nWORKING\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["scan_attention.py", "stale-working-tasks", str(tmp_path / "tasks"), "--no-color"])

    scan_attention.main()

    assert f"within {wake_budget.MAX_TASK_SECONDS_DEFAULT}s threshold" in capsys.readouterr().out
