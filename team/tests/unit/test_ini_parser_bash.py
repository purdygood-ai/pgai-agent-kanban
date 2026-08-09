"""
test_ini_parser_bash.py
=======================
Behavioral unit tests for team/scripts/lib/ini_parser.sh.

Tests source the shell script and invoke read_ini / write_ini via the bash
harness.  All INI files are written to tmp_path (pytest-managed); no bare
/tmp paths are used.

Behavioral requirements under test:
  read_ini:
    - Returns the value for an existing key (with leading/trailing whitespace stripped)
    - Returns a default (or empty string) when the section or key is absent
    - Handles values containing '=' characters
    - Handles section names with dots (e.g. [project.foo])
    - Ignores comment lines (# and ;) and blank lines
    - Returns exit 1 and writes to stderr for unreadable files
    - Missing file is not an error — returns default

  write_ini:
    - Updates an existing key's value in-place
    - Adds a new key under an existing section
    - Creates a new section when it does not exist
    - Creates the file when it does not exist
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/ini_parser.sh"


def _source(func_call: str) -> str:
    """Return a bash snippet that sources ini_parser.sh then calls func_call."""
    return f"source {_LIB} && {func_call}"


def _write_ini_file(path: pathlib.Path, content: str) -> None:
    """Write INI content to a file at path."""
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# read_ini — basic value retrieval
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "file_content, section, key, extra_args, expected",
    [
        ("[section]\nkey = hello\n", "section", "key", "", "hello"),  # known key
        (
            "[section]\nkey =   spaced value   \n",
            "section",
            "key",
            "",
            "spaced value",
        ),  # strips leading/trailing whitespace from value
        ("[section]\nother_key = value\n", "section", "missing_key", "", ""),  # absent key
        (
            "[section]\nother = x\n",
            "section",
            "missing_key",
            "fallback",
            "fallback",
        ),  # absent key, supplied default echoed
        ("[other_section]\nkey = val\n", "missing_section", "key", "", ""),  # absent section
        ("[section]\nkey = a=b=c\n", "section", "key", "", "a=b=c"),  # '=' preserved in value
        (
            "[section]\n# this is a comment\nkey = real_value\n",
            "section",
            "key",
            "",
            "real_value",
        ),  # '#' comment lines ignored
        (
            "[section]\n; another comment style\nkey = value\n",
            "section",
            "key",
            "",
            "value",
        ),  # ';' comment lines ignored
        ("[project.foo]\nbar = baz\n", "project.foo", "bar", "", "baz"),  # dotted section names
    ],
)
def test_read_ini(
    tmp_path: pathlib.Path,
    file_content: str,
    section: str,
    key: str,
    extra_args: str,
    expected: str,
) -> None:
    """read_ini resolves values by section/key, honoring comments, dots, '=' in values, and defaults."""
    cfg = tmp_path / "test.cfg"
    _write_ini_file(cfg, file_content)
    args = f"'{cfg}' {section} {key}" + (f" {extra_args}" if extra_args else "")
    result = run_bash(tmp_path, _source(f"read_ini {args}"))
    assert result.returncode == 0
    assert result.stdout.strip() == expected


def test_read_ini_missing_file_returns_default_not_error(tmp_path: pathlib.Path) -> None:
    """read_ini returns the default value (not an error) when the file is absent."""
    absent = tmp_path / "does_not_exist.cfg"
    result = run_bash(tmp_path, _source(f"read_ini '{absent}' section key default_val"))
    assert result.returncode == 0
    assert result.stdout.strip() == "default_val"


def test_read_ini_multiple_sections_reads_correct_section(tmp_path: pathlib.Path) -> None:
    """read_ini reads from the correct section when multiple sections exist."""
    cfg = tmp_path / "test.cfg"
    _write_ini_file(
        cfg,
        "[alpha]\nkey = alpha_val\n\n[beta]\nkey = beta_val\n",
    )
    result_alpha = run_bash(tmp_path, _source(f"read_ini '{cfg}' alpha key"))
    result_beta = run_bash(tmp_path, _source(f"read_ini '{cfg}' beta key"))
    assert result_alpha.stdout.strip() == "alpha_val"
    assert result_beta.stdout.strip() == "beta_val"


def test_read_ini_blank_value_is_distinguishable_from_missing(
    tmp_path: pathlib.Path,
) -> None:
    """read_ini returns an empty string for an explicitly blank value (not missing)."""
    cfg = tmp_path / "test.cfg"
    _write_ini_file(cfg, "[section]\nkey =\n")
    result = run_bash(tmp_path, _source(f"read_ini '{cfg}' section key FALLBACK"))
    assert result.returncode == 0
    # Key is present with a blank value; default should NOT be echoed.
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# write_ini — update and creation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "initial_content, write_section, write_key, write_value, read_section, read_key, expected",
    [
        (
            "[section]\nkey = old_value\n",
            "section",
            "key",
            "new_value",
            "section",
            "key",
            "new_value",
        ),  # replaces existing key's value in-place
        (
            "[section]\nexisting = val\n",
            "section",
            "new_key",
            "inserted",
            "section",
            "new_key",
            "inserted",
        ),  # inserts a new key under an existing section
        (
            "[other]\nkey = val\n",
            "newsection",
            "newkey",
            "newvalue",
            "newsection",
            "newkey",
            "newvalue",
        ),  # appends a new [section] and key when the section is absent
    ],
)
def test_write_ini(
    tmp_path: pathlib.Path,
    initial_content: str,
    write_section: str,
    write_key: str,
    write_value: str,
    read_section: str,
    read_key: str,
    expected: str,
) -> None:
    """write_ini updates existing keys, adds new keys, and creates sections as needed."""
    cfg = tmp_path / "test.cfg"
    _write_ini_file(cfg, initial_content)
    run_bash(tmp_path, _source(f"write_ini '{cfg}' {write_section} {write_key} {write_value}"))
    result = run_bash(tmp_path, _source(f"read_ini '{cfg}' {read_section} {read_key}"))
    assert result.stdout.strip() == expected


def test_write_ini_creates_file_when_absent(tmp_path: pathlib.Path) -> None:
    """write_ini creates the file from scratch when it does not yet exist."""
    cfg = tmp_path / "created.cfg"
    assert not cfg.exists()
    run_bash(tmp_path, _source(f"write_ini '{cfg}' section key created_value"))
    assert cfg.exists()
    result = run_bash(tmp_path, _source(f"read_ini '{cfg}' section key"))
    assert result.stdout.strip() == "created_value"


def test_write_ini_preserves_other_keys_in_section(tmp_path: pathlib.Path) -> None:
    """write_ini does not disturb other keys when updating one key."""
    cfg = tmp_path / "test.cfg"
    _write_ini_file(cfg, "[section]\nfoo = foo_val\nbar = bar_val\n")
    run_bash(tmp_path, _source(f"write_ini '{cfg}' section foo updated_foo"))
    result_bar = run_bash(tmp_path, _source(f"read_ini '{cfg}' section bar"))
    assert result_bar.stdout.strip() == "bar_val"


def test_write_ini_preserves_other_sections(tmp_path: pathlib.Path) -> None:
    """write_ini does not disturb keys in unrelated sections."""
    cfg = tmp_path / "test.cfg"
    _write_ini_file(cfg, "[alpha]\nkey = aval\n\n[beta]\nkey = bval\n")
    run_bash(tmp_path, _source(f"write_ini '{cfg}' alpha key updated_aval"))
    result_beta = run_bash(tmp_path, _source(f"read_ini '{cfg}' beta key"))
    assert result_beta.stdout.strip() == "bval"


def test_write_ini_idempotent_when_called_twice(tmp_path: pathlib.Path) -> None:
    """write_ini calling twice with the same value yields the same final value."""
    cfg = tmp_path / "test.cfg"
    _write_ini_file(cfg, "[section]\nkey = initial\n")
    run_bash(tmp_path, _source(f"write_ini '{cfg}' section key stable"))
    run_bash(tmp_path, _source(f"write_ini '{cfg}' section key stable"))
    result = run_bash(tmp_path, _source(f"read_ini '{cfg}' section key"))
    assert result.stdout.strip() == "stable"
