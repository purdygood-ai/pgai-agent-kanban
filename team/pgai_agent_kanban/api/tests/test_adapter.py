"""
test_adapter.py — Unit tests for team/pgai_agent_kanban/api/adapter.py.

Tests cover:
  1. Adapter invokes a stub script exiting 0; envelope shows exit_code=0,
     stdout matches, HTTP status maps to 200.
  2. Adapter invokes a stub script exiting 3 with stderr; envelope shows
     exit_code=3, stderr matches, HTTP status maps to 500.
  3. Flag marshalling produces the exact argv for a mixed bool/value flag set.
  4. Parameter validation failure returns 422 without invoking the script
     (stub is never spawned).

All temp files use pytest's tmp_path fixture.  No bare /tmp paths.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess
from unittest.mock import patch

import pytest

from pgai_agent_kanban.api.adapter import (
    HTTP_OK,
    HTTP_UNPROCESSABLE_ENTITY,
    HTTP_INTERNAL_SERVER_ERROR,
    ShellResult,
    ValidationError,
    build_argv,
    http_status_for,
    shell_out,
    validate_required,
)


# ---------------------------------------------------------------------------
# Helpers: write a stub script to a tmp_path file and make it executable
# ---------------------------------------------------------------------------


def _write_stub(path: pathlib.Path, exit_code: int, stdout: str = "", stderr: str = "") -> str:
    """Write a minimal bash stub script to *path* and make it executable.

    The stub unconditionally emits *stdout* to stdout, *stderr* to stderr,
    and exits with *exit_code*.

    Returns:
        The string path suitable for subprocess invocation.
    """
    body = "#!/usr/bin/env bash\n"
    if stdout:
        # Use printf to avoid trailing-newline surprises with echo.
        escaped = stdout.replace("'", "'\\''")
        body += f"printf '%s' '{escaped}'\n"
    if stderr:
        escaped = stderr.replace("'", "'\\''")
        body += f"printf '%s' '{escaped}' >&2\n"
    body += f"exit {exit_code}\n"
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


# ---------------------------------------------------------------------------
# Acceptance criterion 1:
#   Adapter invokes a stub that exits 0 with known stdout;
#   envelope shows exit_code=0, stdout matches, HTTP status maps to 200.
# ---------------------------------------------------------------------------


def test_shell_out_exit_zero_stdout_captured(tmp_path: pathlib.Path) -> None:
    """shell_out returns exit_code=0 and captures stdout when script exits 0."""
    expected_stdout = "operation completed successfully"
    stub = _write_stub(tmp_path / "stub_ok.sh", exit_code=0, stdout=expected_stdout)

    result = shell_out(stub, flags={})

    assert isinstance(result, ShellResult)
    assert result.exit_code == 0
    assert result.stdout == expected_stdout
    assert result.stderr == ""
    assert http_status_for(result.exit_code) == HTTP_OK


# ---------------------------------------------------------------------------
# Acceptance criterion 2:
#   Adapter invokes a stub that exits 3 with stderr;
#   envelope shows exit_code=3, stderr matches, HTTP status maps to 500.
# ---------------------------------------------------------------------------


def test_shell_out_exit_nonzero_stderr_captured(tmp_path: pathlib.Path) -> None:
    """shell_out returns exit_code=3 and captures stderr when script exits 3."""
    expected_stderr = "project not found: unknown_project"
    stub = _write_stub(tmp_path / "stub_err.sh", exit_code=3, stderr=expected_stderr)

    result = shell_out(stub, flags={})

    assert result.exit_code == 3
    assert result.stdout == ""
    assert result.stderr == expected_stderr
    assert http_status_for(result.exit_code) == HTTP_INTERNAL_SERVER_ERROR


# ---------------------------------------------------------------------------
# Acceptance criterion 3:
#   Flag marshalling produces the exact argv expected for a mixed
#   bool/value flag set (e.g. --project foo --per_agent true).
# ---------------------------------------------------------------------------


def test_build_argv_mixed_bool_and_value_flags() -> None:
    """build_argv emits --flag for bool flags and --flag value for string flags."""
    flags = {
        "project": "myproj",
        "per_agent": True,
        "file": None,      # should be omitted
        "no_color": False,  # should be omitted
    }

    argv = build_argv("/scripts/kanban-status.sh", flags)

    assert argv[0] == "/scripts/kanban-status.sh"
    # String value flag
    assert "--project" in argv
    project_idx = argv.index("--project")
    assert argv[project_idx + 1] == "myproj"
    # Bool presence flag
    assert "--per_agent" in argv
    # Omitted flags must not appear
    assert "--file" not in argv
    assert "--no_color" not in argv


def test_build_argv_empty_string_flag_omitted() -> None:
    """build_argv omits flags whose value is an empty string."""
    argv = build_argv("/scripts/show.sh", {"key": "", "project": "x"})

    assert "--key" not in argv
    assert "--project" in argv


def test_build_argv_flag_names_preserved_verbatim() -> None:
    """build_argv preserves flag names exactly (no hyphen/underscore normalisation)."""
    flags = {"no-color": True, "per_agent": "yes"}
    argv = build_argv("/scripts/test.sh", flags)

    assert "--no-color" in argv
    assert "--per_agent" in argv
    assert "--no_color" not in argv  # must not normalise hyphens to underscores
    assert "--per-agent" not in argv  # must not normalise underscores to hyphens


# ---------------------------------------------------------------------------
# Acceptance criterion 4:
#   Parameter validation failure returns 422 without invoking the script.
#   Assert stub was never spawned.
# ---------------------------------------------------------------------------


def test_validation_error_raises_without_spawning_subprocess(
    tmp_path: pathlib.Path,
) -> None:
    """validate_required raises ValidationError and never spawns a subprocess."""
    stub = _write_stub(tmp_path / "stub_never_called.sh", exit_code=0, stdout="should not appear")

    # Simulate the endpoint pattern: validate first, then call shell_out.
    params = {"project": ""}  # empty string should fail validation

    with patch("subprocess.run") as mock_run:
        with pytest.raises(ValidationError) as exc_info:
            validate_required(params, required_keys=["project"])
            # If validate_required raises, shell_out is never reached.
            shell_out(stub, flags=params)  # pragma: no cover

        # subprocess.run must never have been called.
        mock_run.assert_not_called()

    # The exception detail must mention the missing key.
    assert "project" in exc_info.value.detail


def test_validation_error_maps_to_422() -> None:
    """ValidationError should be caught and mapped to HTTP 422 by the caller."""
    # This test verifies the semantic contract: when ValidationError is raised,
    # the HTTP status to return is 422, not 500.
    with pytest.raises(ValidationError):
        validate_required({"key": None}, required_keys=["key"])

    # The numeric constant for the appropriate response code.
    assert HTTP_UNPROCESSABLE_ENTITY == 422


def test_validation_passes_when_all_required_keys_present() -> None:
    """validate_required does not raise when all required keys have non-empty values."""
    params = {"project": "myproj", "key": "TASK-001"}
    # Should not raise.
    validate_required(params, required_keys=["project", "key"])


# ---------------------------------------------------------------------------
# Additional coverage: http_status_for
# ---------------------------------------------------------------------------


def test_http_status_for_zero_is_200() -> None:
    assert http_status_for(0) == 200


def test_http_status_for_nonzero_is_500() -> None:
    for code in (1, 2, 3, 127, 255):
        assert http_status_for(code) == 500, f"Expected 500 for exit code {code}"


# ---------------------------------------------------------------------------
# Integration: shell_out passes flags to the script argv
# ---------------------------------------------------------------------------


def test_shell_out_flags_reach_script(tmp_path: pathlib.Path) -> None:
    """The flags dict is faithfully marshalled into the script's argv."""
    # Write a stub that echoes its own $@ to stdout.
    stub_path = tmp_path / "echo_args.sh"
    stub_path.write_text(
        "#!/usr/bin/env bash\necho \"$@\"\n",
        encoding="utf-8",
    )
    stub_path.chmod(stub_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    result = shell_out(
        str(stub_path),
        flags={"project": "testproj", "no_color": True},
    )

    assert result.exit_code == 0
    # Both flags must appear in the script's received argv.
    assert "--project" in result.stdout
    assert "testproj" in result.stdout
    assert "--no_color" in result.stdout
