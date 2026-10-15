"""
adapter.py — Shell-out and ops-call adapters for the pgai-agent-kanban operator API.

Two bridges from a FastAPI handler to the work it fronts, both producing the
same ``ShellResult`` (exit_code / stdout / stderr) the routers turn into the
JSON envelope:

  - ``shell_out`` runs an operator script as a subprocess (the bridge for a
    verb whose logic still lives in bash).
  - ``ops_call`` is a context manager around a direct call to a
    ``pgai_agent_kanban.ops`` function -- the same function the verb's CLI
    script reaches through ``pp_run_ops`` -- with no process started. It
    captures what the function prints and maps an ``OpsError`` to the exit
    code the ops CLI uses for it (``ops.errors.cli_exit_code``) and to the
    message the CLI prints, so the router's dual-channel envelope reads the
    same whichever bridge produced the result. See "Ops-call adapter" below.

Shell-out responsibilities:

  1. Build the subprocess argv from a flag dict, preserving flag names verbatim.
  2. Invoke the script, await completion (bounded by a timeout), capture
     exit code / stdout / stderr.
  3. Return a typed envelope (ShellResult) with those three fields.
  4. Map a result to its HTTP status (see "HTTP status mapping" below).
  5. Raise ValidationError when required parameters are missing before any
     subprocess is spawned.

Design constraints enforced here:

  - The adapter never re-implements script logic; it only marshals args and
    captures output.
  - Flag names in the flags dict must match the script's own flag names verbatim
    (case, spelling, hyphen/underscore convention).  The adapter emits them as-is.
  - The adapter never spawns child processes in the background; it blocks
    until the script exits -- or until its timeout, when the script's whole
    process group is stopped (SIGTERM, then SIGKILL after a grace period)
    and the call returns the ``shell_out.timeout`` refusal (HTTP 504), so a
    hung script never holds a server worker forever.
  - The child's stdin is closed (``stdin=subprocess.DEVNULL``), never inherited
    from the server process.  A script that prompts (``read -r``) therefore
    reads EOF at once and fails with its own message instead of blocking on
    whatever terminal the server was started from; that message comes back in
    the envelope's stderr like any other refusal.
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

HTTP status mapping (``http_status_for``):

  exit_code == 0                          → 200 OK
  refusal: something named does not exist → 404 Not Found
           (an ops ``NotFound``; an error key naming a missing thing)
  refusal: conflicting state              → 409 Conflict
           (an ops ``Refused`` or ``Ambiguous``; an ``ambiguous`` or
           ``conflict`` error key)
  refusal: a field value is invalid       → 422 Unprocessable Entity
           (an error key naming an invalid or non-component value)
  shell-out timeout                       → 504 Gateway Timeout
  any other non-zero exit_code            → 500 Internal Server Error
  validation failure                      → 422 (no subprocess spawned)
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shlex
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from ..lib.error_contract import format_error_contract
from ..ops.errors import Ambiguous, CliRefusal, NotFound, OpsError, Refused, cli_exit_code

__all__ = [
    "ShellResult",
    "ValidationError",
    "build_argv",
    "shell_out",
    "run_bounded",
    "http_status_for",
    "result_http_status",
    "status_for_error_key",
    "OpsOutcome",
    "ops_call",
]

# ---------------------------------------------------------------------------
# HTTP status constants (from requirements: 200 / 422 / 500)
# ---------------------------------------------------------------------------

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_UNPROCESSABLE_ENTITY = 422
HTTP_INTERNAL_SERVER_ERROR = 500
HTTP_GATEWAY_TIMEOUT = 504

#: The exit code a shell-out stopped by its timeout reports (timeout(1)'s).
SHELL_OUT_TIMEOUT_EXIT = 124
#: Seconds a timed-out script's process group gets between SIGTERM and SIGKILL.
_SHELL_OUT_KILL_GRACE_S = 5


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
        error:     The ops exception behind a refused ops call, when the
                   result came from ``ops_call``; it selects the HTTP status
                   and is never part of the response body.
        status:    An HTTP status the producer already decided (the
                   shell-out timeout's 504), or None to derive it.
    """

    exit_code: int
    stdout: str
    stderr: str
    error: Optional[OpsError] = field(default=None, compare=False, repr=False)
    status: Optional[int] = field(default=None, compare=False)


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
        key:    The name of the parameter that failed, when the failure is
                about one named parameter (``validate_required`` sets it);
                None when no single parameter is to blame. Lets a caller
                build a stable machine key such as ``halt.project_required``
                without parsing ``detail``.
    """

    def __init__(self, detail: str, key: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.key = key


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
    *,
    timeout_seconds: Optional[float] = None,
) -> ShellResult:
    """Invoke a script with the given flags and return a ShellResult envelope.

    Builds the argv via build_argv(), then runs the script in a process group
    of its own and waits for it. Both stdout and stderr are captured raw and
    returned in the envelope; no background execution. Its stdin is
    /dev/null, so a script that prompts gets EOF and exits on its own terms
    rather than waiting on the server's stdin.

    When *timeout_seconds* passes first, the script's whole process group is
    sent SIGTERM and, if anything is left after a short grace, SIGKILL; the
    result is then the ``shell_out.timeout`` refusal -- exit code 124, the
    dual-channel block appended to whatever the script had written to
    stderr, HTTP status 504 -- whose remedy runs the same command from a
    shell, where no time limit applies.

    Args:
        script_path:     Path to the operator script to execute.
        flags:           Mapping of flag name → value; marshalled by build_argv().
        timeout_seconds: Seconds the script may run; None or 0 waits without
                         a bound (for a caller whose script bounds itself).
                         Routes pass the server's ``[api]
                         shell_out_timeout_seconds``.

    Returns:
        ShellResult with exit_code, stdout, and stderr from the completed
        process, or the timeout refusal.

    Raises:
        No exceptions for non-zero exit codes — those are reported in
        ShellResult.exit_code.  OSError or PermissionError from starting the
        process propagate to the caller (e.g., script not found or not
        executable).
    """
    return run_bounded(build_argv(script_path, flags), timeout_seconds=timeout_seconds)


def run_bounded(argv: list[str], *, timeout_seconds: Optional[float] = None) -> ShellResult:
    """Run *argv* (stdin /dev/null, output captured) in its own process group, bounded.

    The one bounded runner behind :func:`shell_out` and every route that
    builds its own argv (a positional-argument script). See
    :func:`shell_out` for the timeout behaviour and the refusal it returns.
    """
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        process_group=0,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds or None)
    except subprocess.TimeoutExpired:
        stdout, stderr = _stop_process_group(proc)
        return _shell_out_timeout_result(argv, timeout_seconds or 0, stdout, stderr)

    return ShellResult(
        exit_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _stop_process_group(proc: "subprocess.Popen[str]") -> tuple[str, str]:
    """SIGTERM *proc*'s process group, SIGKILL what remains after the grace; return its output so far.

    A descendant that left the group and still holds the output pipes cannot
    hold the call: reading stops after one more grace period.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        try:
            return proc.communicate(timeout=_SHELL_OUT_KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            continue
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()
    proc.wait()
    return "", ""


def _shell_out_timeout_result(argv: list[str], seconds: float, stdout: str, stderr: str) -> ShellResult:
    """The ``shell_out.timeout`` refusal for *argv*, stopped after *seconds*."""
    command = shlex.join(argv)
    message = (
        f"{os.path.basename(argv[0])} did not finish within {seconds:g}s (the API server's "
        "[api] shell_out_timeout_seconds); its process group was stopped, and the request "
        "may have done part of its work. Run the same command from a shell, where no time "
        "limit applies, to finish it or see where it waits."
    )
    # AGENT-RESOLVABLE: the caller is not stuck -- the remedy is the same
    # command run from a shell, with no time limit, in the same session; the
    # bound only keeps a hung script from holding a server worker forever.
    # CENSUS-ANCHOR: api-adapter-shell-out-timeout-01
    return ShellResult(
        exit_code=SHELL_OUT_TIMEOUT_EXIT,
        stdout=stdout or "",
        stderr=(stderr or "") + format_error_contract("shell_out.timeout", message, command) + "\n",
        status=HTTP_GATEWAY_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# HTTP status helper
# ---------------------------------------------------------------------------


# An error key's last segment, read for what kind of refusal it names. Liberal
# on separators: project_resolve.not_found, show_queues.project_directory_missing,
# show_test_report.no_tester_task, close.project_invalid, x.key-not-component.
_NOT_FOUND_KEY_RE = re.compile(r"not[_-]?found|missing|no[_-]tester[_-]task|not[_-]?registered")
_INVALID_KEY_RE = re.compile(r"invalid|not[_-]?component|malformed|required|conflicting[_-]?options?")
_CONFLICT_KEY_RE = re.compile(r"ambiguous|conflict")


def status_for_error_key(error_key: str) -> Optional[int]:
    """Return the HTTP status a refusal's stable error key names, or None.

    422 for a key naming an invalid, malformed, missing-but-required or
    non-component value (or a conflicting combination of options), 404 for
    one naming something
    missing or not found, 409 for an ambiguous or conflicting state; None
    when the key says neither (the caller keeps 500).
    """
    reason = (error_key or "").rpartition(".")[2].lower()
    if not reason:
        return None
    if _INVALID_KEY_RE.search(reason):
        return HTTP_UNPROCESSABLE_ENTITY
    if _NOT_FOUND_KEY_RE.search(reason):
        return HTTP_NOT_FOUND
    if _CONFLICT_KEY_RE.search(reason):
        return HTTP_CONFLICT
    return None


def http_status_for(
    exit_code: int,
    *,
    error_key: str = "",
    error: Optional[OpsError] = None,
    status: Optional[int] = None,
) -> int:
    """Map a result to its HTTP status integer.

    Mapping, first match wins:
        exit_code 0                       → 200 (OK)
        *status* given                    → *status* (the shell-out timeout's 504)
        *error_key* (or a CliRefusal's    → 422 / 404 / 409, see
        own key) names the refusal kind     :func:`status_for_error_key`
        *error* an ops NotFound           → 404
        *error* an ops Refused/Ambiguous  → 409
        otherwise                         → 500 (an unexpected failure)

    The key is read first because it is the more specific of the two: a
    guard that raises ``Refused`` over a malformed value names the value
    (``defer.until_malformed``) and answers 422, not 409.

    Args:
        exit_code: The process exit code from ShellResult.exit_code.
        error_key: The refusal's stable machine key (the envelope's
                   ``error``), when one was printed.
        error:     The ops exception behind the result, when known.
        status:    A status the producer already decided.

    Returns:
        The HTTP status for the response.
    """
    if exit_code == 0:
        return HTTP_OK
    if status is not None:
        return status
    if isinstance(error, CliRefusal):
        error_key = error_key or error.error
    by_key = status_for_error_key(error_key)
    if by_key is not None:
        return by_key
    if isinstance(error, NotFound):
        return HTTP_NOT_FOUND
    if isinstance(error, (Refused, Ambiguous)):
        return HTTP_CONFLICT
    return HTTP_INTERNAL_SERVER_ERROR


def result_http_status(result: ShellResult, error_key: str = "") -> int:
    """:func:`http_status_for` over everything a ShellResult carries."""
    return http_status_for(result.exit_code, error_key=error_key, error=result.error, status=result.status)


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
                f"Required parameter '{key}' is missing or empty.", key=key
            )


# ---------------------------------------------------------------------------
# Ops-call adapter
# ---------------------------------------------------------------------------
#
# An action route whose verb has a Python implementation calls that function
# directly -- the one its CLI script reaches through pp_run_ops -- inside
# ``with ops_call() as outcome:``. The adapter is the single place an
# OpsError becomes envelope material:
#
#   exit_code  cli_exit_code(exc), the code ``python3 -m pgai_agent_kanban.ops``
#              exits with for that exception (or the caller's override, for a
#              wrapper-level step its script ends with a plain ``exit 1``)
#   stderr     whatever the function printed to stderr, then ``str(exc)`` and
#              a newline -- exactly what the CLI prints before exiting
#   stdout     whatever the function printed to stdout
#
# so ``as_shell_result()`` hands the router the same ShellResult shape a
# shell_out() of the CLI script produced, and the router's dual-channel
# envelope lifts a house-shape block out of stderr (or falls back to the
# route's ``<verb>.refused`` key) unchanged. Exceptions that are not OpsError
# propagate: they are server faults, not refusals.
#
# Output capture swaps the process-wide sys.stdout/sys.stderr for the length
# of the call, so calls are serialised through one lock; a line another
# thread prints during that window lands in the captured text rather than
# the server's own stream.

_OPS_CAPTURE_LOCK = threading.RLock()


@dataclass
class OpsOutcome:
    """What one ops function call produced, in shell_out()'s terms.

    Fields:
        exit_code: 0 when the call returned; the mapped exit code when it
                   raised an OpsError.
        stdout:    Text the call printed to stdout (route narration may be
                   appended after the call).
        stderr:    Text the call printed to stderr, followed by the OpsError
                   message and a newline when it raised.
        error:     The OpsError the call raised, or None.
    """

    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error: Optional[OpsError] = field(default=None)

    @property
    def ok(self) -> bool:
        """True when the call completed without raising an OpsError."""
        return self.exit_code == 0

    def as_shell_result(self) -> ShellResult:
        """Return the outcome as the ShellResult the routers' envelopes take.

        The OpsError a refused call raised rides along (``error``) so the
        envelope can answer 404 / 409 for it rather than a bare 500.
        """
        return ShellResult(exit_code=self.exit_code, stdout=self.stdout, stderr=self.stderr, error=self.error)


class ops_call(contextlib.AbstractContextManager):  # noqa: N801 -- used as a with-statement verb
    """Context manager that runs an ops function call and records its outcome.

    Usage::

        with ops_call() as outcome:
            ops_write.halt(ctx, project)
        if outcome.ok:
            outcome.stdout += "halt: HALT signal set ...\\n"
        return _make_dual_channel_envelope(outcome.as_shell_result(), ...)

    Args:
        exit_code: Exit code to report when the call raises, in place of
                   ``cli_exit_code(exc)``. For a step the CLI script performs
                   itself and ends with a plain ``exit 1`` (resolving
                   ``--project``), so the route reports that same code.
                   None (the default) uses the shared ops CLI table.
        capture:   True (the default) captures what the call prints, under
                   the process-wide capture lock. False captures nothing and
                   takes no lock -- for a function that hands its narration
                   to a callback the route supplies, and may wait (a
                   ``release_lock`` with ``force``) without holding up every
                   other ops route; the route fills ``outcome.stdout`` itself.

    The body's OpsError is suppressed and recorded on the outcome; any other
    exception propagates after the captured streams are restored.
    """

    def __init__(self, *, exit_code: Optional[int] = None, capture: bool = True) -> None:
        self._exit_code_override = exit_code
        self._capture = capture
        self._outcome = OpsOutcome()
        self._stdout = io.StringIO()
        self._stderr = io.StringIO()
        self._redirects: Optional[contextlib.ExitStack] = None

    def __enter__(self) -> OpsOutcome:
        if not self._capture:
            return self._outcome
        _OPS_CAPTURE_LOCK.acquire()
        stack = contextlib.ExitStack()
        try:
            stack.enter_context(contextlib.redirect_stdout(self._stdout))
            stack.enter_context(contextlib.redirect_stderr(self._stderr))
        except BaseException:
            stack.close()
            _OPS_CAPTURE_LOCK.release()
            raise
        self._redirects = stack
        return self._outcome

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._capture:
            try:
                if self._redirects is not None:
                    self._redirects.close()
            finally:
                _OPS_CAPTURE_LOCK.release()

        outcome = self._outcome
        outcome.stdout += self._stdout.getvalue()
        outcome.stderr += self._stderr.getvalue()
        if exc is None or not isinstance(exc, OpsError):
            return False
        outcome.error = exc
        outcome.stderr += f"{exc}\n"
        outcome.exit_code = (
            self._exit_code_override
            if self._exit_code_override is not None
            else cli_exit_code(exc)
        )
        return True
