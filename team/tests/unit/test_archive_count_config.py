"""
test_archive_count_config.py
============================
Unit tests for pgai_agent_kanban.archive.config, the [archive] archive_count
resolver:

  - Precedence: project.cfg, then kanban.cfg, then the built-in 55. A blank
    value falls through to the next tier; a legacy PROJECT.cfg is read when
    project.cfg is absent.
  - 0 resolves to 0 (archiving disabled); leading zeros are accepted.
  - A non-integer or negative value is refused dual-channel naming the
    file, and the refusal's remedy, pasted into a shell, fixes the file.
  - effective_archive_count() turns that refusal into "archive nothing":
    it prints the refusal and returns 0.
  - The shipped kanban.cfg_example resolves to 55, and project.cfg_example
    copied unedited changes nothing.

Every test builds its own sandbox kanban root under pytest's tmp_path and
points PGAI_AGENT_KANBAN_ROOT_PATH at it, so a refusal recorded by the
shared emitter lands in the sandbox; nothing here reads or writes the live
install.
"""

from __future__ import annotations

import io
import pathlib
import shutil
import subprocess

import pytest

from pgai_agent_kanban.archive import (
    ARCHIVE_COUNT_DEFAULT,
    ArchiveCountError,
    effective_archive_count,
    resolve_archive_count,
)
from pgai_agent_kanban.lib.error_contract import parse_error_contract

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_KANBAN_CFG_EXAMPLE = _REPO_ROOT / "kanban.cfg_example"
_PROJECT_CFG_EXAMPLE = _REPO_ROOT / "project.cfg_example"

_PROJECT = "demo-project"


@pytest.fixture
def kanban_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = tmp_path / "kanban"
    (root / "projects" / _PROJECT).mkdir(parents=True)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    return root


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.write_text(text, encoding="utf-8")
    return path


def _kanban_cfg(root: pathlib.Path, value: str) -> pathlib.Path:
    return _write(root / "kanban.cfg", f"[archive]\narchive_count = {value}\n")


def _project_cfg(root: pathlib.Path, value: str, name: str = "project.cfg") -> pathlib.Path:
    return _write(root / "projects" / _PROJECT / name, f"[project]\nproject_name = {_PROJECT}\n\n[archive]\narchive_count = {value}\n")


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


def test_absent_everywhere_resolves_to_the_default(kanban_root: pathlib.Path) -> None:
    resolved = resolve_archive_count(kanban_root, _PROJECT)
    assert (resolved.value, resolved.tier, resolved.source) == (55, "default", "")
    assert ARCHIVE_COUNT_DEFAULT == 55
    assert resolved.enabled


def test_kanban_cfg_value_applies_when_project_cfg_is_silent(kanban_root: pathlib.Path) -> None:
    cfg = _kanban_cfg(kanban_root, "20")
    _write(kanban_root / "projects" / _PROJECT / "project.cfg", "[project]\nproject_name = demo-project\n")
    resolved = resolve_archive_count(kanban_root, _PROJECT)
    assert (resolved.value, resolved.tier, resolved.source) == (20, "kanban.cfg", str(cfg))


def test_project_cfg_overrides_kanban_cfg(kanban_root: pathlib.Path) -> None:
    _kanban_cfg(kanban_root, "20")
    cfg = _project_cfg(kanban_root, "80")
    resolved = resolve_archive_count(kanban_root, _PROJECT)
    assert (resolved.value, resolved.tier, resolved.source) == (80, "project.cfg", str(cfg))


def test_blank_project_value_falls_through_to_kanban_cfg(kanban_root: pathlib.Path) -> None:
    _kanban_cfg(kanban_root, "20")
    _project_cfg(kanban_root, "")
    assert resolve_archive_count(kanban_root, _PROJECT).value == 20


def test_legacy_project_cfg_file_is_read_when_project_cfg_is_absent(kanban_root: pathlib.Path) -> None:
    _kanban_cfg(kanban_root, "20")
    _project_cfg(kanban_root, "7", name="PROJECT.cfg")
    resolved = resolve_archive_count(kanban_root, _PROJECT)
    assert (resolved.value, resolved.tier) == (7, "project.cfg")


def test_inline_comment_is_not_part_of_the_value(kanban_root: pathlib.Path) -> None:
    _kanban_cfg(kanban_root, "30   # keep a month of history")
    assert resolve_archive_count(kanban_root, _PROJECT).value == 30


# ---------------------------------------------------------------------------
# 0 disables; whole numbers only
# ---------------------------------------------------------------------------


def test_zero_resolves_to_zero_and_disables_archiving(kanban_root: pathlib.Path) -> None:
    _project_cfg(kanban_root, "0")
    resolved = resolve_archive_count(kanban_root, _PROJECT)
    assert resolved.value == 0
    assert not resolved.enabled
    assert effective_archive_count(kanban_root, _PROJECT) == 0


def test_leading_zeros_are_accepted(kanban_root: pathlib.Path) -> None:
    _kanban_cfg(kanban_root, "055")
    assert resolve_archive_count(kanban_root, _PROJECT).value == 55


@pytest.mark.parametrize("value", ["-5", "+5", "5.0", "forty", "40x", "1e3", "٥"])
def test_invalid_value_is_refused_dual_channel(kanban_root: pathlib.Path, value: str) -> None:
    cfg = _project_cfg(kanban_root, value)
    with pytest.raises(ArchiveCountError) as caught:
        resolve_archive_count(kanban_root, _PROJECT)
    fields = parse_error_contract(str(caught.value))
    assert fields is not None, str(caught.value)
    assert fields["error"] == "archive.invalid_archive_count"
    assert f"'{value}'" in fields["message"] and str(cfg) in fields["message"]
    assert str(cfg) in fields["remedy"]
    assert "<" not in fields["remedy"], "remedy must be runnable as printed"
    assert caught.value.file == str(cfg)


def test_invalid_project_value_is_not_papered_over_by_kanban_cfg(kanban_root: pathlib.Path) -> None:
    """A bad value at the tier that supplies it refuses; the valid kanban.cfg
    value below it is never silently used instead."""
    _kanban_cfg(kanban_root, "20")
    _project_cfg(kanban_root, "-1")
    with pytest.raises(ArchiveCountError):
        resolve_archive_count(kanban_root, _PROJECT)


def test_refusal_remedy_fixes_the_file(kanban_root: pathlib.Path) -> None:
    """Pasting the remedy into a shell rewrites the line to the digits the
    value started with, and the same config then resolves."""
    _project_cfg(kanban_root, "40x")
    with pytest.raises(ArchiveCountError) as caught:
        resolve_archive_count(kanban_root, _PROJECT)
    fixed = subprocess.run(["bash", "-c", caught.value.remedy], capture_output=True, text=True, timeout=15)
    assert fixed.returncode == 0, fixed.stderr
    assert resolve_archive_count(kanban_root, _PROJECT).value == 40


def test_refusal_remedy_suggests_the_default_when_no_digits_lead(kanban_root: pathlib.Path) -> None:
    _kanban_cfg(kanban_root, "-5")
    with pytest.raises(ArchiveCountError) as caught:
        resolve_archive_count(kanban_root, _PROJECT)
    assert "archive_count = 55/" in caught.value.remedy, caught.value.remedy


def test_effective_count_treats_a_refusal_as_archive_nothing(kanban_root: pathlib.Path) -> None:
    _project_cfg(kanban_root, "lots")
    stream = io.StringIO()
    assert effective_archive_count(kanban_root, _PROJECT, stream=stream) == 0
    fields = parse_error_contract(stream.getvalue())
    assert fields is not None, stream.getvalue()
    assert fields["error"] == "archive.invalid_archive_count"


def test_effective_count_returns_the_resolved_value(kanban_root: pathlib.Path) -> None:
    _kanban_cfg(kanban_root, "12")
    stream = io.StringIO()
    assert effective_archive_count(kanban_root, _PROJECT, stream=stream) == 12
    assert stream.getvalue() == ""


# ---------------------------------------------------------------------------
# Shipped templates
# ---------------------------------------------------------------------------


def test_shipped_kanban_cfg_example_resolves_to_55(kanban_root: pathlib.Path) -> None:
    shutil.copyfile(_KANBAN_CFG_EXAMPLE, kanban_root / "kanban.cfg")
    resolved = resolve_archive_count(kanban_root, _PROJECT)
    assert (resolved.value, resolved.tier) == (55, "kanban.cfg")


def test_shipped_project_cfg_example_leaves_the_kanban_default_in_force(kanban_root: pathlib.Path) -> None:
    """project.cfg_example documents the override commented out, so copying
    it unedited defers to kanban.cfg."""
    _kanban_cfg(kanban_root, "20")
    shutil.copyfile(_PROJECT_CFG_EXAMPLE, kanban_root / "projects" / _PROJECT / "project.cfg")
    resolved = resolve_archive_count(kanban_root, _PROJECT)
    assert (resolved.value, resolved.tier) == (20, "kanban.cfg")
    assert "# archive_count = 55" in _PROJECT_CFG_EXAMPLE.read_text(encoding="utf-8")
