"""
errors.py — Exception hierarchy for the pgai_agent_kanban.ops package.

All errors raised by ops functions derive from OpsError so callers can
catch the entire family with a single except clause while still being able
to distinguish individual subtypes.

    OpsError
    ├── NotFound    — requested resource (task, project, field) does not exist
    ├── Ambiguous   — lookup matched more than one result; caller must narrow
    ├── Refused     — operation was rejected by a policy or guard (e.g. HALT)
    └── IoError     — underlying I/O failure reading kanban files
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
