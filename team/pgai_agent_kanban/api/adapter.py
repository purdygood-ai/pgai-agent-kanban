"""
adapter.py — Shell-out adapter for the pgai-agent-kanban operator API.

Provides the canonical bridge between FastAPI endpoint handlers and operator
shell scripts.  Responsibilities:

  1. Build the subprocess argv from a flag dict, preserving flag names verbatim.
  2. Invoke the script, await completion, capture exit code / stdout / stderr.
  3. Return a typed envelope (ShellResult) with those three fields.
  4. Map exit codes to HTTP status integers (200 / 422 / 500).
  5. Raise ValidationError when required parameters are missing before any
     subprocess is spawned.

Design constraints enforced here:

  - The adapter never re-implements script logic; it only marshals args and
    captures output.
  - Flag names in the flags dict must match the script's own flag names verbatim
    (case, spelling, hyphen/underscore convention).  The adapter emits them as-is.
  - The adapter never spawns child processes in the background; subprocess.run()
    blocks until completion.
  - Both stdout and stderr are returned raw in the envelope; neither is
    suppressed or filtered.

Flag marshalling rules:

  flags: dict[str, str | bool | None]

  For each key–value pair:
    - Value is True      → emit ``--key`` (boolean presence flag).
    - Value is a non-empty string → emit ``--key value``.
    - Value is False, None, or empty string → omit (flag not passed to script).

  Example: {"project": "myproj", "per_agent": True, "file": None}
    → ["--project", "myproj", "--per_agent"]

HTTP status mapping:

  exit_code == 0        → 200 OK
  non-zero exit_code    → 500 Internal Server Error
  validation failure    → 422 Unprocessable Entity (no subprocess spawned)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ShellResult",
    "ValidationError",
    "build_argv",
    "shell_out",
    "http_status_for",
]

# ---------------------------------------------------------------------------
# HTTP status constants (from requirements: 200 / 422 / 500)
# ---------------------------------------------------------------------------

HTTP_OK = 200
HTTP_UNPROCESSABLE_ENTITY = 422
HTTP_INTERNAL_SERVER_ERROR = 500


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------


@dataclass
class ShellResult:
    """Envelope returned by shell_out().

    Fields:
        exit_code: The script's process exit code (0 = success).
        stdout:    Raw standard output captured from the script.
        stderr:    Raw standard error captured from the script.
    """

    exit_code: int
    stdout: str
    stderr: str


# ---------------------------------------------------------------------------
# Validation error
# ---------------------------------------------------------------------------


class ValidationError(ValueError):
    """Raised when required parameters fail validation before script invocation.

    Raising this error signals that no subprocess was (or should be) spawned.
    Callers map this to HTTP 422 Unprocessable Entity.

    Attributes:
        detail: Human-readable description of the validation failure, suitable
                for inclusion in the API response body.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


# ---------------------------------------------------------------------------
# Flag marshalling
# ---------------------------------------------------------------------------


def build_argv(
    script_path: str,
    flags: dict[str, Any],
) -> list[str]:
    """Build the subprocess argv list from a script path and a flags dict.

    Args:
        script_path: Absolute or relative path to the script to invoke.
        flags:       Mapping of flag name → value.  Flag names are emitted
                     verbatim (no hyphen/underscore normalisation).

    Returns:
        A list starting with script_path, followed by the marshalled flags.

    Flag marshalling rules:
        - True                → ``--name`` (boolean presence flag)
        - non-empty string    → ``--name`` ``value``
        - False / None / ""   → omitted (flag not passed)

    Example:
        build_argv("/scripts/kanban-status.sh", {"project": "myproj", "no_color": True})
        → ["/scripts/kanban-status.sh", "--project", "myproj", "--no_color"]
    """
    argv: list[str] = [script_path]

    for name, value in flags.items():
        if value is True:
            argv.append(f"--{name}")
        elif isinstance(value, str) and value:
            argv.extend([f"--{name}", value])
        # False, None, empty string → omit

    return argv


# ---------------------------------------------------------------------------
# Core shell-out helper
# ---------------------------------------------------------------------------


def shell_out(
    script_path: str,
    flags: dict[str, Any],
) -> ShellResult:
    """Invoke a script with the given flags and return a ShellResult envelope.

    Builds the argv via build_argv(), then runs the script via subprocess.run().
    Both stdout and stderr are captured raw and returned in the envelope.
    The subprocess is awaited to completion; no background execution.

    Args:
        script_path: Path to the operator script to execute.
        flags:       Mapping of flag name → value; marshalled by build_argv().

    Returns:
        ShellResult with exit_code, stdout, and stderr from the completed process.

    Raises:
        No exceptions for non-zero exit codes — those are reported in
        ShellResult.exit_code.  OSError or PermissionError from subprocess.run()
        propagate to the caller (e.g., script not found or not executable).
    """
    argv = build_argv(script_path, flags)

    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
    )

    return ShellResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


# ---------------------------------------------------------------------------
# HTTP status helper
# ---------------------------------------------------------------------------


def http_status_for(exit_code: int) -> int:
    """Map a script exit code to an HTTP status integer.

    Mapping:
        0           → 200 (OK)
        non-zero    → 500 (Internal Server Error)

    Args:
        exit_code: The process exit code from ShellResult.exit_code.

    Returns:
        200 when exit_code is 0; 500 otherwise.
    """
    return HTTP_OK if exit_code == 0 else HTTP_INTERNAL_SERVER_ERROR


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------


def validate_required(
    params: dict[str, Any],
    required_keys: list[str],
) -> None:
    """Validate that required parameter keys are present and non-empty.

    Raises ValidationError (never spawns a subprocess) when any required key
    is missing, None, or an empty string.  The adapter caller maps
    ValidationError to HTTP 422 Unprocessable Entity.

    Args:
        params:        The full parameter dict (mirrors the flags dict passed
                       to shell_out, or a superset of it).
        required_keys: Names of keys that must be present and non-empty.

    Raises:
        ValidationError: When one or more required keys are absent or empty.
                         The detail message names the first missing key found.
    """
    for key in required_keys:
        value = params.get(key)
        if value is None or value == "":
            raise ValidationError(
                f"Required parameter '{key}' is missing or empty."
            )
