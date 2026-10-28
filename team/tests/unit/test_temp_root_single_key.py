"""
test_temp_root_single_key.py
============================
A kanban.cfg whose ``[paths]`` sets only ``tmp_root`` resolves to
``<tmp_root>/pgai_kanban_tmp`` -- ``tmp_subdir`` takes its documented
default -- and every place that turns kanban.cfg into a temp root gives
that one answer:

  * ``resolve_temp_root(<kanban root>)``, the Python resolver;
  * ``load_config <kanban.cfg>`` in ``scripts/lib/config_loader.sh``, whose
    ``PGAI_AGENT_KANBAN_TEMP_DIR`` export every wake inherits;
  * ``install.sh``, which creates the framework temp directory at install
    time from the kanban.cfg it seeds or finds.

Each scenario is run twice: ``tmp_subdir`` absent, and ``tmp_subdir =``
present but blank. (The bash wrapper's own agreement with the Python
resolver, row by row, is test_temp_root_parity.py's.)

Sandboxing: every kanban.cfg lives under pytest ``tmp_path``. The bash
subprocesses get an environment built by ``build_scrubbed_env``; install.sh
runs from this checkout against a throwaway kanban root and ``HOME`` with
all five live-install variables set or cleared together (``KANBAN_ROOT``,
``PGAI_AGENT_KANBAN_ROOT_PATH`` and ``PGAI_AGENT_KANBAN_TEMP_DIR`` into
``tmp_path``; ``PGAI_TASKS_DIR`` and ``PGAI_PROJECT_ROOT`` absent) and
``--wake-tier none``, so it never touches the live install or a crontab.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from pgai_agent_kanban.lib import temp_root
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"
_INSTALL_SH = _REPO_ROOT / "install.sh"
_INSTALL_TIMEOUT = 180

_SUBDIR_FORMS = {
    "subdir-absent": "",
    "subdir-blank": "tmp_subdir =\n",
}


def _single_key_root(tmp_path: pathlib.Path, form: str) -> tuple[pathlib.Path, pathlib.Path]:
    """A kanban root whose kanban.cfg sets tmp_root alone; returns (root, tmp_root dir)."""
    root = tmp_path / "kanban"
    root.mkdir()
    bigdisk = tmp_path / "bigdisk"
    (root / "kanban.cfg").write_text(
        f"[paths]\ntmp_root = {bigdisk}\n{_SUBDIR_FORMS[form]}", encoding="utf-8"
    )
    return root, bigdisk


@pytest.fixture
def no_temp_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PGAI_AGENT_KANBAN_TEMP_DIR", raising=False)


@pytest.mark.parametrize("form", sorted(_SUBDIR_FORMS))
def test_resolver_and_load_config_give_tmp_root_with_the_default_subdir(
    tmp_path: pathlib.Path, form: str, no_temp_override: None
) -> None:
    root, bigdisk = _single_key_root(tmp_path, form)
    expected = f"{bigdisk}/pgai_kanban_tmp"

    assert temp_root.resolve_temp_root(root) == expected, (
        f"[paths] tmp_root = {bigdisk} with tmp_subdir {form.split('-')[1]}: resolve_temp_root "
        f"must apply tmp_subdir's default and return {expected!r}"
    )

    home = tmp_path / "home"
    home.mkdir()
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={"KANBAN_ROOT": str(root), "PGAI_AGENT_KANBAN_ROOT_PATH": str(root)},
    )
    assert "PGAI_AGENT_KANBAN_TEMP_DIR" not in env
    script = "\n".join([
        f"source '{_CONFIG_LOADER}'",
        f"load_config '{root / 'kanban.cfg'}' || exit 7",
        "printf 'temp_dir=%s\\n' \"$PGAI_AGENT_KANBAN_TEMP_DIR\"",
    ])
    result = run_bash(tmp_path, script, base_env=env, cwd=tmp_path)
    assert result.returncode == 0, f"load_config exited {result.returncode}\n{result.stdout}\n{result.stderr}"
    exported = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert exported.get("temp_dir") == expected, (
        f"load_config exported PGAI_AGENT_KANBAN_TEMP_DIR={exported.get('temp_dir')!r}; "
        f"resolve_temp_root gives {expected!r} for the same kanban.cfg\n{result.stderr}"
    )
    assert pathlib.Path(expected).is_dir(), "load_config leaves the root it exports created"


@pytest.mark.parametrize("form", sorted(_SUBDIR_FORMS))
def test_install_creates_the_resolver_root_for_a_single_key_cfg(
    tmp_path: pathlib.Path, form: str, no_temp_override: None
) -> None:
    root, bigdisk = _single_key_root(tmp_path, form)
    expected = temp_root.resolve_temp_root(root, honour_override=False)
    assert expected == f"{bigdisk}/pgai_kanban_tmp"

    home = tmp_path / "home"
    home.mkdir()
    sandbox_tmp = tmp_path / "sandbox_tmp"
    sandbox_tmp.mkdir()
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox_tmp),
        },
    )
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "CLAUDE_CONFIG_DIR", "PGAI_PROJECT_NAME"):
        assert name not in env, f"{name} must be absent from the install sandbox"

    result = subprocess.run(
        ["bash", str(_INSTALL_SH), "--force", "--wake-tier", "none", "--stamp-version", "v0.0.0-sandbox-test"],
        capture_output=True,
        text=True,
        timeout=_INSTALL_TIMEOUT,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    output = f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    assert result.returncode == 0, f"install.sh failed\n{output}"
    assert f"Ensured framework temp directory: {expected}\n" in result.stdout, (
        f"install.sh must create the resolver's root {expected!r} for a kanban.cfg setting only "
        f"tmp_root\n{output}"
    )
    assert pathlib.Path(expected).is_dir()
    assert (root / "kanban.cfg").read_text(encoding="utf-8").count("tmp_root") == 1, (
        "install.sh must keep the operator's existing kanban.cfg"
    )
