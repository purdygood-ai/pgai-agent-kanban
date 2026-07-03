"""lib/artifacts.py — Helpers for project artifacts directory layout.

The artifacts directory pattern is:
    $KANBAN_ROOT/artifacts/<project-name>/v<N>/{input,working,output}

PROJECT.md in the project directory tracks workflow type, description,
output name/formats, priority, and the next version number.

Public API
----------
    get_project_path(project_name, kanban_root=None) -> Path
    get_version_path(project_name, version, kanban_root=None) -> Path
    get_next_version(project_name, kanban_root=None) -> Path
    get_input_path(project_name, version, kanban_root=None) -> Path
    get_working_path(project_name, version, kanban_root=None) -> Path
    get_output_path(project_name, version, kanban_root=None) -> Path

Raises ArtifactsError with a descriptive message on any validation failure.
"""

import fcntl
import os
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Project names must start with a lowercase letter or digit and may contain
# lowercase letters, digits, and hyphens.
_PROJECT_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')

# Matches the "## Next Version" heading and the integer value that follows it.
# Liberal: allows any amount of whitespace between heading and value.
_NEXT_VERSION_RE = re.compile(
    r'(##\s*Next\s*Version\s*\n+\s*)(\d+)',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ArtifactsError(Exception):
    """Raised when artifacts path helpers encounter an error."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_kanban_root(kanban_root=None) -> Path:
    """Resolve the kanban root path from argument or environment.

    Precedence: explicit argument > PGAI_AGENT_KANBAN_ROOT_PATH (canonical) >
    ~/pgai_agent_kanban.

    The env var is read at call time on every invocation — there is no
    module-level caching — so monkeypatch.setenv in tests produces correct
    isolated paths without leaking the operator's live path.
    """
    if kanban_root is not None:
        return Path(kanban_root)
    return Path(
        os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")
        or str(Path.home() / "pgai_agent_kanban")
    )


def _validate_project_name(project_name: str) -> None:
    """Raise ArtifactsError if project_name does not match the required pattern."""
    if not isinstance(project_name, str) or not _PROJECT_NAME_RE.match(project_name):
        raise ArtifactsError(
            f"Invalid project name '{project_name}'. "
            "Project names must match ^[a-z0-9][a-z0-9-]*$ "
            "(lowercase letters, digits, hyphens only; must not start with a hyphen)."
        )


def _read_next_version_from_text(text: str) -> int:
    """Extract the Next Version integer from PROJECT.md text.

    Returns the integer value, or 0 if not found.
    """
    m = _NEXT_VERSION_RE.search(text)
    if m:
        return int(m.group(2))
    return 0


def _write_next_version_in_text(text: str, new_version: int) -> str:
    """Return updated PROJECT.md text with Next Version set to new_version.

    If a Next Version field already exists it is updated in-place.
    Otherwise the field is appended.
    """
    m = _NEXT_VERSION_RE.search(text)
    if m:
        return text[: m.start(2)] + str(new_version) + text[m.end(2):]
    # Append: ensure exactly one trailing newline before new section
    separator = "\n" if text.endswith("\n") else "\n\n"
    return text + separator + f"## Next Version\n{new_version}\n"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_project_path(project_name: str, kanban_root=None) -> Path:
    """Return the project directory: $KANBAN_ROOT/artifacts/<project-name>/.

    Does not create the directory.

    Parameters
    ----------
    project_name:
        The project identifier. Must match ^[a-z0-9][a-z0-9-]*$.
    kanban_root:
        Override for the kanban root directory. Defaults to
        PGAI_AGENT_KANBAN_ROOT_PATH (canonical) /
        ~/pgai_agent_kanban.

    Returns
    -------
    Path
        Absolute path to the project artifacts directory.

    Raises
    ------
    ArtifactsError
        If project_name does not match the required pattern.
    """
    _validate_project_name(project_name)
    root = _resolve_kanban_root(kanban_root)
    return root / "artifacts" / project_name


def get_version_path(project_name: str, version: int, kanban_root=None) -> Path:
    """Return the versioned run directory: $KANBAN_ROOT/artifacts/<project-name>/v<N>/.

    Does not create the directory.

    Parameters
    ----------
    project_name:
        The project identifier. Must match ^[a-z0-9][a-z0-9-]*$.
    version:
        The version number (positive integer).
    kanban_root:
        Override for the kanban root directory.

    Returns
    -------
    Path
        Absolute path to the versioned run directory.

    Raises
    ------
    ArtifactsError
        If project_name is invalid or version is not a positive integer.
    """
    if not isinstance(version, int) or version < 1:
        raise ArtifactsError(
            f"Version must be a positive integer, got {version!r}"
        )
    project_path = get_project_path(project_name, kanban_root)
    return project_path / f"v{version}"


def get_next_version(project_name: str, kanban_root=None) -> Path:
    """Allocate the next version, update PROJECT.md atomically, return version path.

    Reads the 'Next Version' field from PROJECT.md in the project directory,
    uses that value as the allocated version number, writes version+1 back, and
    returns the Path for the new version directory (created immediately).

    Semantics:
      - Before call:  PROJECT.md says "Next Version: N"
      - Allocated:    version N  (the path returned is v<N>/)
      - After call:   PROJECT.md says "Next Version: N+1"

    If PROJECT.md does not exist or has no 'Next Version' field, the field is
    bootstrapped to 1 and the first call allocates v1.

    The read-modify-write cycle is protected by an exclusive flock so concurrent
    calls in separate processes do not double-allocate the same version.

    Parameters
    ----------
    project_name:
        The project identifier. Must match ^[a-z0-9][a-z0-9-]*$.
    kanban_root:
        Override for the kanban root directory.

    Returns
    -------
    Path
        Absolute path to the newly allocated version directory (created).

    Raises
    ------
    ArtifactsError
        If project_name is invalid or the PROJECT.md file cannot be read/written.
    """
    project_path = get_project_path(project_name, kanban_root)
    project_path.mkdir(parents=True, exist_ok=True)

    project_md_path = project_path / "PROJECT.md"

    # Open with O_CREAT so the file is created if absent, then lock exclusively.
    flags = os.O_RDWR | os.O_CREAT
    fd = os.open(str(project_md_path), flags, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(fd, "r+", encoding="utf-8") as fh:
            text = fh.read()

            current_next = _read_next_version_from_text(text)
            if current_next < 1:
                # Bootstrap: treat missing/zero as 1
                current_next = 1

            allocated = current_next
            new_next = current_next + 1
            new_text = _write_next_version_in_text(text, new_next)

            fh.seek(0)
            fh.write(new_text)
            fh.truncate()
    except OSError as exc:
        raise ArtifactsError(
            f"Failed to read/write PROJECT.md for project '{project_name}': {exc}"
        ) from exc

    version_path = get_version_path(project_name, allocated, kanban_root)
    version_path.mkdir(parents=True, exist_ok=True)
    return version_path


def get_input_path(project_name: str, version: int, kanban_root=None) -> Path:
    """Return the input subdirectory for a version, creating it if needed.

    Path: $KANBAN_ROOT/artifacts/<project-name>/v<N>/input/

    Parameters
    ----------
    project_name:
        The project identifier. Must match ^[a-z0-9][a-z0-9-]*$.
    version:
        The version number (positive integer).
    kanban_root:
        Override for the kanban root directory.

    Returns
    -------
    Path
        Absolute path to the input directory (created if it did not exist).

    Raises
    ------
    ArtifactsError
        If project_name is invalid or version is not a positive integer.
    """
    path = get_version_path(project_name, version, kanban_root) / "input"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_working_path(project_name: str, version: int, kanban_root=None) -> Path:
    """Return the working subdirectory for a version, creating it if needed.

    Path: $KANBAN_ROOT/artifacts/<project-name>/v<N>/working/

    Parameters
    ----------
    project_name:
        The project identifier. Must match ^[a-z0-9][a-z0-9-]*$.
    version:
        The version number (positive integer).
    kanban_root:
        Override for the kanban root directory.

    Returns
    -------
    Path
        Absolute path to the working directory (created if it did not exist).

    Raises
    ------
    ArtifactsError
        If project_name is invalid or version is not a positive integer.
    """
    path = get_version_path(project_name, version, kanban_root) / "working"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_output_path(project_name: str, version: int, kanban_root=None) -> Path:
    """Return the output subdirectory for a version, creating it if needed.

    Path: $KANBAN_ROOT/artifacts/<project-name>/v<N>/output/

    Parameters
    ----------
    project_name:
        The project identifier. Must match ^[a-z0-9][a-z0-9-]*$.
    version:
        The version number (positive integer).
    kanban_root:
        Override for the kanban root directory.

    Returns
    -------
    Path
        Absolute path to the output directory (created if it did not exist).

    Raises
    ------
    ArtifactsError
        If project_name is invalid or version is not a positive integer.
    """
    path = get_version_path(project_name, version, kanban_root) / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path
