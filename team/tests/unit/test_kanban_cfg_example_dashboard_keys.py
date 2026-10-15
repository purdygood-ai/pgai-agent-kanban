"""
test_kanban_cfg_example_dashboard_keys.py
=========================================
Drift guard between the [dashboard] keys the dashboard scripts read and
what kanban.cfg_example documents for them.

Each dashboard script reads its kanban.cfg [dashboard] keys with
``read_ini <kanban.cfg> dashboard <key> <default>``, the last argument
being the in-script default an install with no such line runs on. For
every key below:

  - kanban.cfg_example's [dashboard] section sets it to that default, so a
    freshly seeded kanban.cfg behaves exactly as one without the line;
  - the comment block directly above the line names the same default;
  - every script that reads the key uses that same default, so the
    documented value cannot be true for one pane and false for another.

The key list is explicit rather than discovered, so a reader added for a
new key is a deliberate addition here, not an accident of a source scan.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_REPO_ROOT = _TEAM_DIR.parent
_CFG_EXAMPLE = _REPO_ROOT / "kanban.cfg_example"
_SCRIPTS = _TEAM_DIR / "scripts"

# key -> the default every reader passes to read_ini.
_DASHBOARD_KEYS = {
    "session_name": "pgai-kanban-dashboard",
    "refresh_seconds": "5",
    "rows_per_column": "21",
    "color_done": "green",
    "color_working": "yellow",
    "color_blocked": "red",
    "color_waiting": "yellow",
    "color_label": "cyan",
    "color_dim": "white",
    "color_header": "cyan",
    "color_halt": "red",
    "color_ok": "green",
}


def _dashboard_section() -> list[str]:
    lines: list[str] = []
    inside = False
    for line in _CFG_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = stripped == "[dashboard]"
            continue
        if inside:
            lines.append(stripped)
    return lines


def _readers(key: str) -> dict[str, set[str]]:
    """script path -> the read_ini defaults it passes for [dashboard] <key>."""
    pattern = re.compile(rf"read_ini\s+\S+\s+dashboard\s+{re.escape(key)}\s+\"?([^\s\")]+)")
    found: dict[str, set[str]] = {}
    for path in sorted(_SCRIPTS.rglob("*.sh")):
        for match in pattern.finditer(path.read_text(encoding="utf-8", errors="replace")):
            found.setdefault(str(path.relative_to(_TEAM_DIR)), set()).add(match.group(1))
    return found


@pytest.mark.parametrize("key, default", sorted(_DASHBOARD_KEYS.items()))
def test_example_sets_key_to_its_in_script_default_and_names_it(key: str, default: str) -> None:
    section = _dashboard_section()
    value_lines = [i for i, l in enumerate(section) if re.match(rf"^{re.escape(key)}\s*=", l)]
    assert len(value_lines) == 1, f"[dashboard] {key} must appear exactly once in {_CFG_EXAMPLE.name}"
    index = value_lines[0]
    assert section[index].split("=", 1)[1].strip() == default, section[index]

    comment: list[str] = []
    for line in reversed(section[:index]):
        if not line.startswith("#"):
            break
        comment.append(line)
    assert any(f"Default: {default}" in l for l in comment), (
        f"the comment above [dashboard] {key} must name its default {default!r}: {comment}"
    )


@pytest.mark.parametrize("key, default", sorted(_DASHBOARD_KEYS.items()))
def test_every_reader_uses_the_documented_default(key: str, default: str) -> None:
    readers = _readers(key)
    assert readers, f"no script reads [dashboard] {key}; drop it from kanban.cfg_example"
    mismatched = {path: d for path, d in readers.items() if d != {default}}
    assert not mismatched, f"[dashboard] {key} is documented as {default!r} but read with {mismatched}"
