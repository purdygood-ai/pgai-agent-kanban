"""
test_config_loader_retired_keys.py
==================================
Unit tests for how team/scripts/lib/config_loader.sh's load_config() treats
a retired kanban.cfg key: one an earlier kanban.cfg_example seeded into
installs and that nothing reads any more ([providers] initial).

  - An existing install whose kanban.cfg still carries the line loads with
    no unknown-key WARNING and no "Nearest known key" hint (which would
    suggest renaming it onto [providers] active, a key the section already
    has). Instead it prints one NOTE naming deletion as the remedy.
  - The remedy is a runnable command: pasting it deletes exactly the
    retired line, leaves every other byte alone, and pasted a second time
    changes nothing.
  - Retirement is scoped to its own section: the same key name under a
    different section is still an unknown key and still warns.
  - A retired key is not a live key: it is absent from the Key Registry
    the doc-truth lint parses, absent from _CONFIG_LOADER_KNOWN_KEYS, and
    kanban.cfg_example carries no live line for it.

Tests invoke config_loader.sh via subprocess (bash -c "source ... && ..."),
matching test_config_loader_key_validation.py, so the fixtures exercise the
real production shell code rather than a reimplementation. Each subprocess
runs against a throwaway root under pytest's tmp_path.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"
_INI_PARSER = _TEAM_DIR / "scripts" / "lib" / "ini_parser.sh"
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_doc_command_truth.py"
_CFG_EXAMPLE = _REPO_ROOT / "kanban.cfg_example"

_UNKNOWN_KEY_PHRASE = "is not a known kanban.cfg key"
_RETIRED_LINE = "initial = claude"


def _sandbox_env(root: pathlib.Path) -> dict[str, str]:
    """An explicit environment: PATH/HOME plus a throwaway kanban root, so
    the verdict never depends on what the operator happened to export."""
    tmp = root / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(root)),
        "LC_ALL": "C.UTF-8",
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp),
    }


def _run_bash(script: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)


def _load_config(cfg: pathlib.Path, root: pathlib.Path) -> subprocess.CompletedProcess:
    script = f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg)!r}"
    return _run_bash(script, _sandbox_env(root))


def _seeded_cfg(root: pathlib.Path, extra_providers_lines: list[str]) -> pathlib.Path:
    """kanban.cfg_example copied into the sandbox with *extra_providers_lines*
    inserted directly under its [providers] header, the way an earlier
    example seeded them."""
    lines = _CFG_EXAMPLE.read_text(encoding="utf-8").splitlines(keepends=True)
    header = next(i for i, l in enumerate(lines) if re.match(r"^\[providers\]\s*$", l))
    lines[header + 1 : header + 1] = [f"{l}\n" for l in extra_providers_lines]
    cfg = root / "kanban.cfg"
    cfg.write_text("".join(lines), encoding="utf-8")
    return cfg


def _notes(stderr: str) -> list[str]:
    return [l for l in stderr.splitlines() if "NOTE:" in l]


def _remedy(note: str) -> str:
    m = re.search(r"Remove it with: (.*)$", note)
    assert m, f"retired-key note carries no remedy command: {note}"
    return m.group(1)


# ---------------------------------------------------------------------------
# A seeded retired key loads without the unknown-key warning
# ---------------------------------------------------------------------------


def test_seeded_providers_initial_draws_a_deletion_note_not_an_unknown_key_warning(tmp_path: pathlib.Path) -> None:
    cfg = _seeded_cfg(tmp_path, [_RETIRED_LINE])
    result = _load_config(cfg, tmp_path)
    assert result.returncode == 0, result.stderr
    assert _UNKNOWN_KEY_PHRASE not in result.stderr, result.stderr
    assert "WARNING" not in result.stderr, result.stderr
    assert "Nearest known key" not in result.stderr, "a retired key must not be steered onto a live key"

    notes = _notes(result.stderr)
    assert len(notes) == 1, result.stderr
    note = notes[0]
    assert "[providers] initial" in note and str(cfg) in note, note
    assert "retired" in note and "safe to delete" in note, note
    assert "do not rename" in note, note
    assert "sed -i" in _remedy(note) and "/d}" in _remedy(note), "the remedy deletes the line"


def test_remedy_deletes_only_the_retired_line_and_is_safe_to_paste_twice(tmp_path: pathlib.Path) -> None:
    cfg = _seeded_cfg(tmp_path, [_RETIRED_LINE])
    before = cfg.read_text(encoding="utf-8")
    remedy = _remedy(_notes(_load_config(cfg, tmp_path).stderr)[0])

    env = _sandbox_env(tmp_path)
    assert _run_bash(remedy, env).returncode == 0
    after = cfg.read_text(encoding="utf-8")
    assert after == before.replace(f"{_RETIRED_LINE}\n", "", 1), "only the retired line is removed"

    assert _run_bash(remedy, env).returncode == 0
    assert cfg.read_text(encoding="utf-8") == after, "a second paste changes nothing"

    result = _load_config(cfg, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == "", result.stderr


def test_repeated_retired_line_draws_one_note_whose_remedy_removes_every_copy(tmp_path: pathlib.Path) -> None:
    cfg = _seeded_cfg(tmp_path, [_RETIRED_LINE, "  initial   =   codex"])
    result = _load_config(cfg, tmp_path)
    notes = _notes(result.stderr)
    assert len(notes) == 1, result.stderr

    assert _run_bash(_remedy(notes[0]), _sandbox_env(tmp_path)).returncode == 0
    reload = _load_config(cfg, tmp_path)
    assert reload.stderr.strip() == "", reload.stderr
    assert "initial" not in cfg.read_text(encoding="utf-8").split("[providers]", 1)[1].split("\n[", 1)[0]


def test_retirement_is_scoped_to_its_section(tmp_path: pathlib.Path) -> None:
    """'initial' under [wake] was never seeded there: it is an unknown key
    and keeps the existing warning with its nearest-name hint."""
    cfg = tmp_path / "kanban.cfg"
    cfg.write_text("[wake]\ninitial = 3\n", encoding="utf-8")
    result = _load_config(cfg, tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"[wake] initial in {cfg} {_UNKNOWN_KEY_PHRASE}" in result.stderr, result.stderr
    assert not _notes(result.stderr), result.stderr


# ---------------------------------------------------------------------------
# A retired key is not a live key
# ---------------------------------------------------------------------------


def _bash_array(name: str) -> set[str]:
    m = re.search(rf"^{name}=\(\n(.*?)^\)", _CONFIG_LOADER.read_text(encoding="utf-8"), re.S | re.M)
    assert m, f"{name} array not found in {_CONFIG_LOADER}"
    return set(m.group(1).split())


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_doc_command_truth_retired_keys", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_retired_keys_are_not_live_registry_keys() -> None:
    retired = _bash_array("_CONFIG_LOADER_RETIRED_KEYS")
    assert "providers:initial" in retired, retired
    assert not retired & _bash_array("_CONFIG_LOADER_KNOWN_KEYS"), "a key is either live or retired, never both"

    registry = _import_lint_module()._parse_config_loader_registry(_CONFIG_LOADER)
    live = {f"{section}:{key}" for section, key in registry}
    assert not retired & live, "a retired key must not count as a registry key needing documentation"


def test_cfg_example_carries_no_live_retired_key(tmp_path: pathlib.Path) -> None:
    script = f"source {str(_INI_PARSER)!r} && list_ini_keys {str(_CFG_EXAMPLE)!r}"
    result = _run_bash(script, _sandbox_env(tmp_path))
    assert result.returncode == 0, result.stderr
    live = {line.replace("\t", ":", 1) for line in result.stdout.splitlines()}
    assert not live & _bash_array("_CONFIG_LOADER_RETIRED_KEYS"), "kanban.cfg_example seeds a retired key"
