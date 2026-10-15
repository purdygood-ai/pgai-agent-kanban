"""
errors.py — Exception hierarchy for the pgai_agent_kanban.ops package.

All errors raised by ops functions derive from OpsError so callers can
catch the entire family with a single except clause while still being able
to distinguish individual subtypes.

    OpsError
    ├── NotFound    — requested resource (task, project, field) does not exist
    ├── Ambiguous   — lookup matched more than one result; caller must narrow
    ├── Refused     — operation was rejected by a policy or guard (e.g. HALT)
    ├── IoError     — underlying I/O failure reading kanban files
    ├── MountPinned — stale worktree path is an active mount target; cannot rm -rf
    └── CliRefusal  — a verb stopped with the exact stderr text and exit code
                      its CLI wrapper prints and exits with

cli_exit_code(exc) maps any of them to the exit code the ops CLI uses, so
every surface that reports an ops failure as an exit code agrees with it.
"""

from __future__ import annotations


class OpsError(Exception):
    """Base class for all pgai_agent_kanban.ops errors.

    Raise a subclass rather than this class directly so callers can
    distinguish the failure mode.
    """


class NotFound(OpsError):
    """Raised when a requested resource does not exist.

    Examples: a project name not registered in projects.cfg, a task ID
    that has no directory under tasks/, a field heading absent from a
    status.md file.
    """


class Ambiguous(OpsError):
    """Raised when a lookup matches more than one result.

    Callers must supply a more specific key to disambiguate.  The error
    message should enumerate the candidates so the caller can choose.

    Attributes:
        candidates: List of Path objects that all matched the prefix key.
                    Sorted alphabetically.
        result:     A ResolveResult for the first (alphabetically) candidate.
                    Show-style callers that treat ambiguity as a soft warning
                    can use this result directly.  Write callers must refuse.
    """

    def __init__(
        self,
        message: str,
        candidates: "list | None" = None,
        result: "object | None" = None,
    ) -> None:
        super().__init__(message)
        self.candidates: list = candidates or []
        self.result: object = result


class Refused(OpsError):
    """Raised when an operation is rejected by a policy or guard.

    Examples: the kanban HALT file is present, a project is halted, a
    read is attempted on a resource that requires a precondition the
    caller has not satisfied.
    """


class IoError(OpsError):
    """Raised when an underlying I/O operation fails.

    Wraps OSError and related exceptions that arise while reading kanban
    files so callers can catch ops I/O failures separately from general
    Python I/O errors.  The original exception is available as
    ``__cause__`` (use ``raise IoError(...) from original_exc``).
    """


class MountPinned(OpsError):
    """Raised when a stale worktree path cannot be removed because it is mount-pinned.

    The on-disk path is an active mount target or contains an active mount.
    The caller must not attempt umount or any other mount manipulation;
    detection and reporting are the only sanctioned actions.

    The error message names the blocking mount so the operator can identify
    and unmount it manually before retrying the reset with --force.

    Maps to exit code 4 in the reset_item CLI path.
    """


class CliRefusal(OpsError):
    """A verb stopped, carrying what its CLI prints and the code it exits with.

    The base of a verb whose refusals keep their CLI script's plain-text
    form (the CM verbs: ``cm.cancel.CancelRcError``,
    ``cm.unwind.UnwindRcError``), so the CLI and the REST twin report one
    refusal the same way.

    Attributes:
        error:     A stable machine key (``<verb>.<reason>``).
        text:      What remains to be printed to stderr (plain ``ERROR:``
                   lines, a dual-channel block, or ``""`` when nothing is
                   left to print).
        exit_code: The code the CLI exits with.
    """

    def __init__(self, error: str, text: str, exit_code: int = 1) -> None:
        super().__init__(text.rstrip("\n") or error)
        self.error = error
        self.text = text
        self.exit_code = exit_code


# Text an OpsError carries when deposit_intake refuses to overwrite an
# existing target; the one OpsError message that selects its own exit code.
NO_CLOBBER_MARKER = "already exists (no clobber)"


def cli_exit_code(exc: OpsError) -> int:
    """Return the exit code the ``python3 -m pgai_agent_kanban.ops`` CLI uses for *exc*.

    The one mapping from an ops exception to a process exit code, shared by
    the CLI entry point (``ops.write._cli_main``) and the operator API's ops
    adapter, so an operation refused over HTTP reports the same ``exit_code``
    the CLI wrapper exits with.

    Mapping:
        Ambiguous   -> 2  (key prefix matched more than one item)
        NotFound    -> 3  (no item matched)
        Refused     -> 2  (a guard rejected the operation)
        MountPinned -> 4  (reset --force blocked by a mount)
        IoError     -> 4  (filesystem failure)
        OpsError    -> 3  when the message carries the no-clobber marker
                           (deposit_intake target exists), else 1

    Args:
        exc: The exception an ops function raised.

    Returns:
        The exit code, 1 through 4.
    """
    if isinstance(exc, (Ambiguous, Refused)):
        return 2
    if isinstance(exc, NotFound):
        return 3
    if isinstance(exc, (MountPinned, IoError)):
        return 4
    if NO_CLOBBER_MARKER in str(exc):
        return 3
    return 1
