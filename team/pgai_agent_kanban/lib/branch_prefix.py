"""
branch_prefix.py — Shared reader for a project's configured git branch prefix.

Every Python surface that needs to name a project's prefixed branch
(``<prefix>main``, ``<prefix>rc/<version>``, ``<prefix>feature/<task>``) reads
the prefix through :func:`resolve_branch_prefix` rather than baking ``ai_``
into a literal. The prefix lives in ``project.cfg`` ``[project] branch_prefix``
under the kanban root; this module is the Python counterpart of
``team/scripts/lib/project_paths.sh``'s ``pp_branch_prefix``.

Resolution order:

  1. ``<kanban_root>/projects/<project_name>/project.cfg`` ``[project]``
     ``branch_prefix`` when the kanban root resolves and the file parses. A
     readable project.cfg always wins, including one that deliberately
     configures an empty prefix (a pure-AI install with ``branch_prefix =``).
  2. *default* otherwise — kanban root unresolvable (the env var is unset in a
     sandboxed test or an unconfigured install), project.cfg missing or
     unparsable, or the key absent from it.

Public API
----------
    resolve_branch_prefix(project_name=..., default=...) -> str
        Return the configured prefix (``"ai_"``, ``""``, ...) or *default*.

    prefixed_main_branch(project_name=..., default=...) -> str
        Return ``f"{prefix}main"`` — the project's long-lived base branch.

    release_tag_name(version, branch_prefix) -> str
        Return ``f"{branch_prefix}{version}"`` — the git tag a release of
        *version* is cut as. The Python counterpart of ``pp_prefix_tag``:
        every Python lookup of a release tag names it through this function,
        so it finds exactly the tag the release created.
"""

from __future__ import annotations

from pgai_agent_kanban.lib import ini

# The kanban-self project, which owns the scripts that call this helper with
# no explicit project name.
DEFAULT_PROJECT_NAME = "pgai-agent-kanban"

# project.cfg_example's own shipped default for the kanban-self project. Used
# only when the live project.cfg cannot be read at all.
DEFAULT_BRANCH_PREFIX = "ai_"


def resolve_branch_prefix(
    project_name: str = DEFAULT_PROJECT_NAME,
    default: str = DEFAULT_BRANCH_PREFIX,
) -> str:
    """Return *project_name*'s configured ``branch_prefix``.

    Args:
        project_name: Project as registered in ``projects.cfg``; the project.cfg
            read is ``<kanban_root>/projects/<project_name>/project.cfg``.
        default: Value returned when the live project.cfg cannot be read
            (kanban root unresolvable, file missing or unparsable, key absent).

    Returns:
        The configured prefix with surrounding quotes and whitespace stripped
        (``"ai_"``, or ``""`` when the project deliberately configures none),
        or *default* when project.cfg is unreadable.
    """
    try:
        from pgai_agent_kanban.env import resolve_kanban_root

        kanban_root = resolve_kanban_root()
    except (ImportError, RuntimeError):
        return default

    cfg_path = kanban_root / "projects" / project_name / "project.cfg"
    if not cfg_path.is_file():
        return default

    try:
        parser = ini.load(cfg_path)
    except ini.IniError:
        return default

    if not parser.has_option("project", "branch_prefix"):
        return default
    return parser.get("project", "branch_prefix").strip().strip('"')


def prefixed_main_branch(
    project_name: str = DEFAULT_PROJECT_NAME,
    default: str = DEFAULT_BRANCH_PREFIX,
) -> str:
    """Return the project's prefixed main branch name (``<prefix>main``).

    Args:
        project_name: See :func:`resolve_branch_prefix`.
        default: Prefix used when project.cfg is unreadable; see
            :func:`resolve_branch_prefix`.

    Returns:
        ``f"{prefix}main"`` — e.g. ``"ai_main"`` with the shipped default
        prefix, ``"main"`` for a project configured with an empty prefix.
    """
    return f"{resolve_branch_prefix(project_name=project_name, default=default)}main"


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
