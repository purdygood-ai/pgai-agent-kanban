"""
test_config_export_tunables.py
==============================
The operator tunables that used to be environment-only are kanban.cfg keys
read by config_loader.sh, with the environment still winning:

  1. config_export_tunables exports each of the five from kanban.cfg when
     the environment does not set it, keeps a value the environment sets,
     and falls back to the documented default when neither does.
  2. config_cleanup_tunables resolves [cleanup] task_retention_days into
     PGAI_CLEANUP_TASK_RETENTION_DAYS the same way.
  3. Every key and default here is the one kanban.cfg_example scaffolds.
  4. The attention pane's quarantine warning uses [discovery]
     reject_threshold from kanban.cfg (no default of its own): a file
     rejected 4 times is warned about under a threshold of 5 and not under
     the default 3.

Every root is a sandbox under tmp_path.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import ini
from tests.fixtures.dashboard_render_fixture import build_dashboard_project_root, run_dashboard_script
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"
_ATTENTION_SH = _TEAM_DIR / "scripts" / "dashboard" / "attention.sh"
_EXAMPLE = _TEAM_DIR.parent / "kanban.cfg_example"

#: env var -> (section, key, default)
_TUNABLES = {
    "PGAI_DISCOVERY_SETTLE_SECONDS": ("discovery", "settle_seconds", "60"),
    "PGAI_DISCOVERY_REJECT_THRESHOLD": ("discovery", "reject_threshold", "3"),
    "PGAI_DASHBOARD_STATUS_MAX_WIDTH": ("dashboard", "status_max_width", "320"),
    "PGAI_DASHBOARD_ARM_TIMEOUT_SECONDS": ("dashboard", "arm_timeout_seconds", "20"),
    "PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS": ("release", "deep_verify_freshness_window_days", "7"),
    "PGAI_CLEANUP_TASK_RETENTION_DAYS": ("cleanup", "task_retention_days", "7"),
}


def _resolve(tmp_path: pathlib.Path, cfg_text: str | None, env: dict[str, str]) -> dict[str, str]:
    cfg = tmp_path / "kanban.cfg"
    if cfg_text is not None:
        cfg.write_text(cfg_text, encoding="utf-8")
    names = " ".join(_TUNABLES)
    script = (
        f"source '{_LOADER}'\n"
        f"config_export_tunables '{cfg}'\n"
        f"config_cleanup_tunables '{cfg}'\n"
        f'for n in {names}; do printf \'%s=%s\\n\' "$n" "${{!n}}"; done\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env=build_scrubbed_env(home_dir=tmp_path, extra_env=env),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def _cfg_setting_every_key() -> str:
    sections: dict[str, list[str]] = {}
    for number, (_name, (section, key, _default)) in enumerate(sorted(_TUNABLES.items()), start=41):
        sections.setdefault(section, []).append(f"{key} = {number}")
    return "".join(f"[{s}]\n" + "\n".join(lines) + "\n\n" for s, lines in sections.items())


def test_each_tunable_is_read_from_kanban_cfg(tmp_path) -> None:
    values = _resolve(tmp_path, _cfg_setting_every_key(), {})

    expected = {name: str(n) for n, name in enumerate(sorted(_TUNABLES), start=41)}
    assert values == expected


def test_the_environment_wins_over_kanban_cfg(tmp_path) -> None:
    env = {name: "9" for name in _TUNABLES}

    values = _resolve(tmp_path, _cfg_setting_every_key(), env)

    assert values == env


def test_each_tunable_defaults_without_a_key(tmp_path) -> None:
    values = _resolve(tmp_path, None, {})

    assert values == {name: default for name, (_s, _k, default) in _TUNABLES.items()}


@pytest.mark.parametrize("name", sorted(_TUNABLES))
def test_the_example_scaffolds_each_key_at_its_default(name) -> None:
    section, key, default = _TUNABLES[name]
    assert ini.get_value(_EXAMPLE, section, key) == default, (name, section, key)


@pytest.mark.parametrize("threshold, warned", [("5", True), (None, False)], ids=["cfg-threshold-5", "default-3"])
def test_the_attention_pane_uses_the_configured_reject_threshold(tmp_path, threshold, warned) -> None:
    root = build_dashboard_project_root(tmp_path, project_name="alpha").kanban_root
    (root / "tmp").mkdir(exist_ok=True)
    state = root / "projects" / "alpha" / ".discovery-state" / "rejected-counts.tsv"
    state.parent.mkdir(parents=True)
    state.write_text("BUG-0042-flaky.md\t4\t2026-10-31T00:00:00\tbad header\n", encoding="utf-8")
    if threshold is not None:
        with (root / "kanban.cfg").open("a", encoding="utf-8") as cfg:
            cfg.write(f"\n[discovery]\nreject_threshold = {threshold}\n")
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "NO_COLOR": "1",
            "TERM": "dumb",
        },
    )

    result = run_dashboard_script(["bash", str(_ATTENTION_SH), "--kanban-root", str(root)], env=env, timeout=60)

    assert result.returncode == 0, result.stdout + result.stderr
    assert ("BUG-0042-flaky.md" in result.stdout) is warned, result.stdout
