"""
test_lib_ini.py
===============
Behavioural tests for pgai_agent_kanban.lib.ini: raw parsing (no
interpolation), the tolerant one-value reader, the line-preserving
set_line_value / remove_line_value editors, and the projects.cfg registry
in priority order — including agreement with the bash ``projects_cfg_list``.

Every fixture lives under pytest's tmp_path; no live kanban file is read.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.lib import ini
from tests.unit.shell_harness import run_bash

_PROJECTS_SH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lib" / "projects.sh"

# Priority order differs from both alphabetical and file order; two entries
# share a priority so file order must break the tie; one entry has no
# priority (999) and a non-project section sits between registrations.
_REGISTRY = """\
# projects.cfg fixture
[project:zulu]
priority=2
dashboard_color=#378ADD

[project:alpha]
priority=3

[defaults]
note=not a project

[project:mike]
priority=1
dashboard_max_rows=12

[project:bravo]
description=no priority set

[project:echo]
priority=2
"""
_EXPECTED_ORDER = ["mike", "zulu", "echo", "alpha", "bravo"]


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- load ---------------------------------------------------------------------


def test_load_reads_values_raw_without_interpolation(tmp_path: pathlib.Path) -> None:
    cfg = _write(tmp_path / "kanban.cfg", "[api]\nhost = 127.0.0.1\nnote = 100% %(host)s\n")
    parser = ini.load(cfg)
    assert parser.get("api", "note") == "100% %(host)s"
    assert parser.get("api", "host") == "127.0.0.1"


def test_load_missing_file_is_an_empty_parser(tmp_path: pathlib.Path) -> None:
    parser = ini.load(tmp_path / "absent.cfg")
    assert parser.sections() == []


def test_load_malformed_file_raises_ini_error(tmp_path: pathlib.Path) -> None:
    cfg = _write(tmp_path / "project.cfg", "[project]\nkey=1\n[project]\nkey=2\n")
    with pytest.raises(ini.IniError):
        ini.load(cfg)


def test_load_text_before_first_section_raises_ini_error(tmp_path: pathlib.Path) -> None:
    cfg = _write(tmp_path / "PROJECT.cfg", "dev_tree_path=/srv/x\n")
    with pytest.raises(ini.IniError):
        ini.load(cfg)


def test_reexported_lookup_errors_are_raised_by_the_parser(tmp_path: pathlib.Path) -> None:
    parser = ini.load(_write(tmp_path / "a.cfg", "[project]\nkey=1\n"))
    with pytest.raises(ini.NoSectionError):
        parser.get("versioning", "max_major")
    with pytest.raises(ini.NoOptionError):
        parser.get("project", "absent")


# --- get_value ----------------------------------------------------------------


def test_get_value_strips_whitespace_and_matches_keys_case_insensitively(tmp_path: pathlib.Path) -> None:
    cfg = _write(tmp_path / "project.cfg", "[project]\nDev_Tree_Path =   /srv/dev  \n")
    assert ini.get_value(cfg, "project", "dev_tree_path") == "/srv/dev"


@pytest.mark.parametrize(
    "text",
    ["[other]\nkey=1\n", "[project]\nother=1\n", "[project]\nkey=1\n[project]\nkey=2\n", "key=1\n"],
    ids=["section-absent", "key-absent", "malformed-duplicate", "no-section-header"],
)
def test_get_value_returns_default_when_not_configured(tmp_path: pathlib.Path, text: str) -> None:
    cfg = _write(tmp_path / "project.cfg", text)
    assert ini.get_value(cfg, "project", "key") == ""
    assert ini.get_value(cfg, "project", "key", default="fallback") == "fallback"


def test_get_value_missing_file_returns_default(tmp_path: pathlib.Path) -> None:
    assert ini.get_value(tmp_path / "absent.cfg", "project", "key", default="d") == "d"


def test_get_value_empty_value_reads_empty(tmp_path: pathlib.Path) -> None:
    cfg = _write(tmp_path / "project.cfg", "[project]\nbranch_prefix =\n")
    assert ini.get_value(cfg, "project", "branch_prefix", default="ai_") == ""


# --- projects_registry --------------------------------------------------------


def test_registry_orders_by_priority_then_file_order(tmp_path: pathlib.Path) -> None:
    _write(tmp_path / "projects.cfg", _REGISTRY)
    names = [entry.name for entry in ini.projects_registry(tmp_path)]
    assert names == _EXPECTED_ORDER


def test_registry_entry_fields(tmp_path: pathlib.Path) -> None:
    _write(tmp_path / "projects.cfg", _REGISTRY)
    by_name = {entry.name: entry for entry in ini.projects_registry(tmp_path)}
    assert by_name["zulu"].priority == 2
    assert by_name["zulu"].color == "#378ADD"
    assert by_name["alpha"].color == ""
    assert by_name["bravo"].priority == ini.DEFAULT_PROJECT_PRIORITY == 999
    assert by_name["mike"].options["dashboard_max_rows"] == "12"
    # section_index counts every section, the [defaults] one included.
    assert [by_name[n].section_index for n in ("zulu", "alpha", "mike", "bravo", "echo")] == [0, 1, 3, 4, 5]


def test_registry_non_integer_priority_counts_as_999(tmp_path: pathlib.Path) -> None:
    _write(tmp_path / "projects.cfg", "[project:a]\npriority=high\n[project:b]\npriority=1000\n")
    assert [(e.name, e.priority) for e in ini.projects_registry(tmp_path)] == [("a", 999), ("b", 1000)]


def test_registry_missing_file_is_empty(tmp_path: pathlib.Path) -> None:
    assert ini.projects_registry(tmp_path) == []


def test_registry_malformed_file_raises(tmp_path: pathlib.Path) -> None:
    _write(tmp_path / "projects.cfg", "[project:a]\npriority=1\n[project:a]\npriority=2\n")
    with pytest.raises(ini.IniError):
        ini.projects_registry(tmp_path)


def test_registry_order_matches_bash_projects_cfg_list(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "root"
    _write(root / "projects.cfg", _REGISTRY)
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_PROJECTS_SH}'\nprojects_cfg_list\n",
        extra_env={"KANBAN_ROOT": str(root), "PGAI_AGENT_KANBAN_ROOT_PATH": str(root)},
    )
    assert result.returncode == 0, f"rc={result.returncode}\nstderr={result.stderr}"
    assert result.stdout.split() == [entry.name for entry in ini.projects_registry(root)]


# --- set_line_value / remove_line_value -----------------------------------------

_EDIT_FIXTURE = """\
# header comment
[project]
project_name = demo

[versioning]
# max_minor: a comment that names the key
max_patch = 21
max_minor = 13
max_major = 0

# comment above the next section
[release]
max_minor = 99
"""


def test_set_line_value_rewrites_only_the_key_line_in_its_section() -> None:
    edited = ini.set_line_value(_EDIT_FIXTURE, "versioning", "max_minor", "7", delimiter="=")
    assert edited == _EDIT_FIXTURE.replace("max_minor = 13\n", "max_minor=7\n")


def test_set_line_value_default_delimiter_is_spaced() -> None:
    edited = ini.set_line_value("[providers]\n# active = x\nactive = codex\n", "providers", "active", "claude")
    assert edited == "[providers]\n# active = x\nactive = claude\n"


def test_set_line_value_adds_a_missing_key_after_the_sections_last_key() -> None:
    removed = ini.remove_line_value(_EDIT_FIXTURE, "versioning", "max_major")
    edited = ini.set_line_value(removed, "versioning", "max_major", "2", delimiter="=")
    assert edited == _EDIT_FIXTURE.replace("max_major = 0\n", "max_major=2\n")


def test_set_line_value_adds_a_key_straight_after_an_empty_sections_header() -> None:
    assert ini.set_line_value("[a]\n\n[b]\nx=1\n", "a", "k", "v") == "[a]\nk = v\n\n[b]\nx=1\n"


def test_set_line_value_appends_a_missing_section() -> None:
    assert ini.set_line_value("[a]\nx=1", "versioning", "max_minor", "5", delimiter="=") == (
        "[a]\nx=1\n\n[versioning]\nmax_minor=5\n"
    )
    assert ini.set_line_value("", "s", "k", "v") == "[s]\nk = v\n"


def test_set_line_value_without_add_refuses_a_missing_key() -> None:
    with pytest.raises(ini.NoOptionError):
        ini.set_line_value("[providers]\n# active = claude\n", "providers", "active", "codex", add=False)
    with pytest.raises(ini.NoOptionError):
        ini.set_line_value("[other]\nactive = claude\n", "providers", "active", "codex", add=False)


def test_set_line_value_keeps_untouched_crlf_lines() -> None:
    text = "[providers]\r\nactive = codex\r\nother = 1\r\n"
    assert ini.set_line_value(text, "providers", "active", "claude") == (
        "[providers]\r\nactive = claude\nother = 1\r\n"
    )


def test_remove_line_value_deletes_only_the_first_match_in_the_section() -> None:
    edited = ini.remove_line_value(_EDIT_FIXTURE, "versioning", "max_minor")
    assert edited == _EDIT_FIXTURE.replace("max_minor = 13\n", "")
    assert "max_minor = 99\n" in edited


def test_remove_line_value_returns_the_text_unchanged_when_absent() -> None:
    text = "[versioning]\nmax_patch = 1"
    assert ini.remove_line_value(text, "versioning", "max_minor") is text


def test_an_edited_value_reads_back_through_read_line_value(tmp_path: pathlib.Path) -> None:
    cfg = _write(tmp_path / "project.cfg", ini.set_line_value(_EDIT_FIXTURE, "versioning", "max_minor", "8"))
    assert ini.read_line_value(cfg, "versioning", "max_minor") == "8"
    assert ini.read_line_value(cfg, "release", "max_minor") == "99"
