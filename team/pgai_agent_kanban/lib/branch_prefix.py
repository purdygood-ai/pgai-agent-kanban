"""
branch_prefix.py — Shared reader for a project's configured git branch prefix.

Every Python surface that needs to name a project's prefixed branch
(``<prefix>main``, ``<prefix>rc/<version>``, ``<prefix>feature/<task>``) reads
the prefix through :func:`resolve_branch_prefix` rather than baking ``ai_``
into a literal. The prefix lives in ``project.cfg`` ``[project] branch_prefix``
under the kanban root; this module is the Python counterpart of
``team/scripts/lib/project_paths.sh``'s ``pp_branch_prefix``.

Two families of reader share the module. :func:`resolve_branch_prefix` and
:func:`prefixed_main_branch` find the kanban root from the environment, take a
project name and default to ``ai_``; they read and validate the value as
:func:`ini_branch_prefix_from_cfg` does, answering *default* where
``pp_branch_prefix`` refuses. The root-based readers take a project
root or a config file the caller already holds and answer ``""`` for an
unset prefix. Every root-based reader reads by ``pp_branch_prefix``'s rules (the bash
``read_ini`` line rules: ``[project]`` only, the key matched exactly, an
inline comment stripped; a legacy sectionless ``PROJECT.cfg`` read by its
bare key; one double quote stripped from each side):

  - :func:`branch_prefix_from_cfg` / :func:`branch_prefix_for_project_root`
    return the value unvalidated, for a caller that refuses an invalid
    prefix with its own check and its own refusal text;
  - :func:`ini_branch_prefix_from_cfg`, :func:`bare_branch_prefix_from_cfg`
    (one file) and :func:`ini_branch_prefix_for_project_root` (``project.cfg``,
    else ``PROJECT.cfg``) validate it as ``validate_branch_prefix`` does and
    raise :class:`InvalidBranchPrefixError` where ``pp_branch_prefix``
    refuses, so for the same file they answer exactly what it prints.

:func:`resolve_branch_prefix` resolution order:

  1. ``<kanban_root>/projects/<project_name>/project.cfg`` ``[project]``
     ``branch_prefix`` when the kanban root resolves and the file sets the
     key to a value ``pp_branch_prefix`` accepts. A readable project.cfg
     always wins, including one that deliberately configures an empty prefix
     (a pure-AI install with ``branch_prefix =``).
  2. *default* otherwise — kanban root unresolvable (the env var is unset in a
     sandboxed test or an unconfigured install), project.cfg missing or
     unreadable, the key absent from ``[project]``, or a value
     ``pp_branch_prefix`` refuses (single-quoted, a slash, a space, ...).

Public API
----------
    resolve_branch_prefix(project_name=..., default=...) -> str
        Return the configured, validated prefix (``"ai_"``, ``""``, ...) or
        *default*; never raises :class:`InvalidBranchPrefixError`.

    prefixed_main_branch(project_name=..., default=...) -> str
        Return ``f"{prefix}main"`` — the project's long-lived base branch.

    branch_prefix_from_cfg(cfg_file) -> str
        The prefix *cfg_file* configures by ``pp_branch_prefix``'s rules;
        ``""`` when *cfg_file* is ``None`` or the key is absent or blank.

    branch_prefix_for_project_root(project_root) -> str
        :func:`branch_prefix_from_cfg` on the ``project.cfg`` (else legacy
        ``PROJECT.cfg``) under *project_root*.

    validate_branch_prefix(value, source="") -> str
        *value* when it is empty or ``[a-zA-Z0-9_-]+``; raises
        :class:`InvalidBranchPrefixError` otherwise.

    ini_branch_prefix_from_cfg(cfg_file) -> str
    bare_branch_prefix_from_cfg(cfg_file) -> str
        :func:`branch_prefix_from_cfg`, validated; ``""`` when *cfg_file* is
        ``None``, missing or does not set the key.

    ini_branch_prefix_for_project_root(project_root) -> str
        :func:`branch_prefix_for_project_root`, validated.

    release_tag_name(version, branch_prefix) -> str
        Return ``f"{branch_prefix}{version}"`` — the git tag a release of
        *version* is cut as. The Python counterpart of ``pp_prefix_tag``:
        every Python lookup of a release tag names it through this function,
        so it finds exactly the tag the release created.
"""

from __future__ import annotations

import pathlib
import re
from typing import Union

from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib.project_cfg import (
    project_cfg_file,
    read_cfg_key,
    strip_one_quote_each_side,
)

PathLike = Union[str, "pathlib.Path"]

# The kanban-self project, which owns the scripts that call this helper with
# no explicit project name.
DEFAULT_PROJECT_NAME = "pgai-agent-kanban"

# project.cfg_example's own shipped default for the kanban-self project. Used
# only when the live project.cfg cannot be read at all.
DEFAULT_BRANCH_PREFIX = "ai_"

# The characters validate_branch_prefix (team/scripts/lib/project_paths.sh)
# accepts; the empty string is accepted separately.
_VALID_BRANCH_PREFIX_RE = re.compile(r"[a-zA-Z0-9_-]+")


class InvalidBranchPrefixError(ValueError):
    """A configured ``branch_prefix`` that ``pp_branch_prefix`` refuses.

    Attributes:
        value:  The prefix as read (one double quote stripped from each side).
        source: The config file it was read from, or ``""`` when unknown.

    ``str(error)`` is ``validate_branch_prefix``'s own refusal text, followed
    by the file when *source* is known. Every caller of the validated readers
    catches it and carries on as it does for an unreadable config file.
    """

    def __init__(self, value: str, source: str = "") -> None:
        self.value = value
        self.source = source
        message = (
            f"invalid branch_prefix '{value}' (allowed characters: a-z A-Z 0-9 _ -; "
            "spaces, slashes, dots, and other special characters are not permitted)"
        )
        if source:
            message += f" in {source}"
        super().__init__(message)


def resolve_branch_prefix(
    project_name: str = DEFAULT_PROJECT_NAME,
    default: str = DEFAULT_BRANCH_PREFIX,
) -> str:
    """Return *project_name*'s configured ``branch_prefix``.

    The value is read and validated by ``pp_branch_prefix``'s rules (the
    validated path :func:`ini_branch_prefix_from_cfg` uses): ``[project]``
    only, the key matched exactly, an inline comment stripped, one double
    quote stripped from each side. Only ``project.cfg`` is read; a legacy
    ``PROJECT.cfg`` alone reads as missing.

    Args:
        project_name: Project as registered in ``projects.cfg``; the project.cfg
            read is ``<kanban_root>/projects/<project_name>/project.cfg``.
        default: Value returned when the live project.cfg cannot be read
            (kanban root unresolvable, file missing or unreadable, key absent
            from ``[project]``) and when the configured value is one
            ``pp_branch_prefix`` refuses.

    Returns:
        The configured prefix (``"ai_"``, or ``""`` when the project
        deliberately configures none), or *default* when project.cfg is
        unreadable or sets no ``[project] branch_prefix``. A refused value
        (single-quoted, or any character outside ``a-z A-Z 0-9 _ -``) is
        never returned: it yields *default*, the same answer as an
        unreadable project.cfg, so no caller has a refusal to handle.
    """
    try:
        from pgai_agent_kanban.env import resolve_kanban_root

        kanban_root = resolve_kanban_root()
    except (ImportError, RuntimeError):
        return default

    cfg_path = kanban_root / "projects" / project_name / "project.cfg"
    if not cfg_path.is_file():
        return default

    # Absent (None) is told apart from a deliberately blank value ("") here;
    # the validated reader below answers "" for both.
    if ini.read_line_value(cfg_path, "project", "branch_prefix") is None:
        return default

    try:
        return _validated_branch_prefix_from_cfg(cfg_path)
    except InvalidBranchPrefixError:
        return default


def prefixed_main_branch(
    project_name: str = DEFAULT_PROJECT_NAME,
    default: str = DEFAULT_BRANCH_PREFIX,
) -> str:
    """Return the project's prefixed main branch name (``<prefix>main``).

    Args:
        project_name: See :func:`resolve_branch_prefix`.
        default: Prefix used when project.cfg is unreadable or configures a
            value ``pp_branch_prefix`` refuses; see :func:`resolve_branch_prefix`.

    Returns:
        ``f"{prefix}main"`` — e.g. ``"ai_main"`` with the shipped default
        prefix, ``"main"`` for a project configured with an empty prefix.
        A refused prefix is never glued in: *default* stands in for it.
    """
    return f"{resolve_branch_prefix(project_name=project_name, default=default)}main"


def branch_prefix_from_cfg(cfg_file: pathlib.Path | None) -> str:
    """Return the ``[project] branch_prefix`` *cfg_file* configures, by ``pp_branch_prefix``'s rules.

    The Python twin of ``pp_branch_prefix``'s read in
    ``team/scripts/lib/project_paths.sh``, without its validation: the value
    is read by :func:`pgai_agent_kanban.lib.project_cfg.read_cfg_key` (the
    bash ``read_ini`` line rules; a legacy sectionless ``PROJECT.cfg`` by its
    bare ``branch_prefix = value`` line) and one double quote is stripped
    from each side, so ``branch_prefix = ""`` reads as empty.

    Args:
        cfg_file: The project's ``project.cfg`` or legacy ``PROJECT.cfg``, or
            ``None`` when the project has neither.

    Returns:
        The configured prefix (``"ai_"``, ...), or ``""`` when *cfg_file* is
        ``None`` or the key is absent or blank. Never the ``ai_`` default.
    """
    if cfg_file is None:
        return ""
    return strip_one_quote_each_side(read_cfg_key(cfg_file, "project", "branch_prefix"), '"')


def branch_prefix_for_project_root(project_root: PathLike) -> str:
    """Return the prefix the config file under *project_root* configures.

    Locates ``project.cfg`` (else a legacy ``PROJECT.cfg``) with
    :func:`pgai_agent_kanban.lib.project_cfg.project_cfg_file` and reads it
    with :func:`branch_prefix_from_cfg`.

    Args:
        project_root: The project's root directory
            (``<kanban_root>/projects/<name>``).

    Returns:
        The configured prefix, or ``""`` when there is no config file or the
        key is absent or blank.
    """
    return branch_prefix_from_cfg(project_cfg_file(pathlib.Path(project_root)))


def validate_branch_prefix(value: str, source: str = "") -> str:
    """Return *value* when ``validate_branch_prefix`` (bash) accepts it.

    Args:
        value:  A prefix as read from a config file.
        source: The file it came from, named in the refusal.

    Returns:
        *value* unchanged: ``""``, or one or more of ``a-z A-Z 0-9 _ -``.

    Raises:
        InvalidBranchPrefixError: *value* holds any other character (a space,
            a slash, a quote, an unstripped inline comment, ...).
    """
    if value and not _VALID_BRANCH_PREFIX_RE.fullmatch(value):
        raise InvalidBranchPrefixError(value, source)
    return value


def _validated_branch_prefix_from_cfg(cfg_file: PathLike | None) -> str:
    """:func:`branch_prefix_from_cfg` on *cfg_file*, then :func:`validate_branch_prefix`."""
    if not cfg_file:
        return ""
    path = pathlib.Path(cfg_file)
    return validate_branch_prefix(branch_prefix_from_cfg(path), str(path))


def ini_branch_prefix_from_cfg(cfg_file: PathLike | None) -> str:
    """Return the ``[project] branch_prefix`` *cfg_file* sets, as ``pp_branch_prefix`` answers it.

    Read by :func:`branch_prefix_from_cfg` and validated by
    :func:`validate_branch_prefix`: an inline comment is stripped, a key
    outside ``[project]`` is ignored, one double quote is stripped from each
    side, and a value bash refuses (a slash, a single-quoted ``'ai_'``, ...)
    raises.

    Args:
        cfg_file: The config file, or ``None`` / ``""`` when the project has
            none. A missing or unreadable file reads as unset.

    Returns:
        The configured prefix, or ``""`` when *cfg_file* sets none.

    Raises:
        InvalidBranchPrefixError: the value is one ``pp_branch_prefix`` refuses.
    """
    return _validated_branch_prefix_from_cfg(cfg_file)


def ini_branch_prefix_for_project_root(project_root: PathLike) -> str:
    """Return the prefix under *project_root*, as ``pp_branch_prefix`` answers it.

    The file read is ``project.cfg``, else a legacy ``PROJECT.cfg``
    (:func:`pgai_agent_kanban.lib.project_cfg.project_cfg_file`), by the
    rules of :func:`ini_branch_prefix_from_cfg`.

    Args:
        project_root: The project's root directory.

    Returns:
        The configured prefix, or ``""`` when there is no config file or it
        sets none.

    Raises:
        InvalidBranchPrefixError: the value is one ``pp_branch_prefix`` refuses.
    """
    return _validated_branch_prefix_from_cfg(project_cfg_file(pathlib.Path(project_root)))


def bare_branch_prefix_from_cfg(cfg_file: PathLike | None) -> str:
    """Return the prefix *cfg_file* configures; the same answer as :func:`ini_branch_prefix_from_cfg`.

    Args:
        cfg_file: The config file, or ``None`` / ``""`` for none.

    Returns:
        The configured prefix, or ``""`` when the file is missing or sets
        none.

    Raises:
        InvalidBranchPrefixError: the value is one ``pp_branch_prefix`` refuses.
    """
    return _validated_branch_prefix_from_cfg(cfg_file)


def release_tag_name(version: str, branch_prefix: str) -> str:
    """Return the git tag name a release of *version* is cut as.

    The same rule as ``pp_prefix_tag`` in
    ``team/scripts/lib/project_paths.sh``: the project's branch prefix
    glued in front of the version, the version unchanged when the prefix is
    empty.

    Args:
        version: The release version, e.g. ``"v0.31.0"``.
        branch_prefix: The project's configured ``branch_prefix`` (``"ai_"``,
            or ``""`` for an unprefixed project).

    Returns:
        ``f"{branch_prefix}{version}"`` — e.g. ``"ai_v0.31.0"``, or
        ``"v0.31.0"`` when the prefix is empty.
    """
    return f"{branch_prefix}{version}"
