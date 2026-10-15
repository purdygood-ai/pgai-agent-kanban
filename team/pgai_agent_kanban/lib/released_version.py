"""
released_version.py — the framework version and a project's last released
version, as the dashboard reads them.

Two answers the status summary (``kanban-status.sh`` / ``GET /status``)
shows in its header row:

``kanban_version(kanban_root, repo_root, last_released="")``
    The deployed framework version, by the tier order of ``get_kanban_version``
    in ``team/scripts/dashboard/lib/version.sh``:

      1. ``<kanban_root>/VERSION`` (install-generated);
      2. ``<repo_root>/VERSION``;
      3. the highest ``<prefix>vX.Y.Z`` tag (prefix stripped) merged into
         ``<remote>/<prefix>main`` in *repo_root*, when *repo_root* holds a
         ``.git`` directory -- ``<remote>`` and ``<prefix>`` being the
         framework project's own ``git_remote_name`` and ``branch_prefix``
         (``<kanban_root>/projects/pgai-agent-kanban/project.cfg``), and
         ``origin`` and no prefix when that file does not exist; an invalid
         ``git_remote_name`` skips the tier;
      4. *last_released*, when given and not ``none``;
      5. ``unknown``.

    A VERSION file's content is read with every whitespace character removed.

``last_released_version(kanban_root, project)``
    The project's last released version, by the tiers of
    ``pp_last_released_version`` in ``team/scripts/lib/project_paths.sh``:
    with no ``project.cfg`` / ``PROJECT.cfg``, no ``[project] dev_tree_path``,
    or a dev tree that is not a git repository, the ``## Last Released`` field
    of the project's ``release-state.md`` (read through
    ``cm.read_state_field``); otherwise the highest
    ``<branch_prefix>vX.Y.Z`` tag (prefix stripped) merged into
    ``<remote>/<prefix>main`` -- ``<remote>`` being the project's
    ``git_remote_name``, ``origin`` when absent or blank
    (:mod:`pgai_agent_kanban.lib.git_remote`; an invalid value skips this
    tier) -- else into the local ``<prefix>main``, else among all local
    tags. ``v0.0.0`` when nothing is found.

Both are local reads. Neither ever contacts a remote: the tag scans read the
refs already on disk, the way the dashboard's render path always has (it
exports ``PGAI_GIT_LOCAL_ONLY``, so ``pp_last_released_version`` skips its
fetch there). The git tiers run the ``git`` binary; a project with no git dev
tree, and a kanban root holding its VERSION file, start no process at all.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from typing import Optional, Sequence

from pgai_agent_kanban.cm.read_state_field import read_state_field
from pgai_agent_kanban.lib import git_remote
from pgai_agent_kanban.lib.branch_prefix import DEFAULT_PROJECT_NAME as FRAMEWORK_PROJECT
from pgai_agent_kanban.lib.project_cfg import project_cfg_file, read_cfg_key, strip_one_quote_each_side
from pgai_agent_kanban.lib.semver import version_sort_key

__all__ = [
    "NOTHING_RELEASED",
    "UNKNOWN_VERSION",
    "dashboard_repo_root",
    "kanban_version",
    "last_released_version",
    "latest_released_tag",
]

#: What ``last_released_version`` answers when no release is found.
NOTHING_RELEASED = "v0.0.0"

#: What ``kanban_version`` answers when no tier yields a version.
UNKNOWN_VERSION = "unknown"

_RELEASE_TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")


# ---------------------------------------------------------------------------
# git reads
# ---------------------------------------------------------------------------


def _git(repo: "pathlib.Path | str", args: Sequence[str]) -> Optional[str]:
    """Run ``git -C <repo> <args>``; its stdout, or ``None`` when it fails."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def _highest_release_tag(tags: str, prefix: str = "") -> str:
    """The highest ``<prefix>vX.Y.Z`` tag in *tags* (one per line), prefix stripped, or ``""``."""
    versions = [
        line[len(prefix):]
        for line in tags.splitlines()
        if line.startswith(prefix) and _RELEASE_TAG_RE.match(line[len(prefix):])
    ]
    return max(versions, key=version_sort_key) if versions else ""


def latest_released_tag(
    repo_root: "pathlib.Path | str",
    remote: str = git_remote.DEFAULT_REMOTE_NAME,
    prefix: str = "",
) -> str:
    """The highest ``<prefix>vX.Y.Z`` tag merged into ``<remote>/<prefix>main``, or ``""``.

    The prefix is stripped from the answer. ``""`` as well when *repo_root*
    holds no ``.git`` directory or git fails. The defaults (``origin``, no
    prefix) read ``origin/main`` and bare ``vX.Y.Z`` tags.
    """
    if not (pathlib.Path(repo_root) / ".git").is_dir():
        return ""
    return _highest_release_tag(_git(repo_root, ["tag", "--merged", f"{remote}/{prefix}main"]) or "", prefix)


def _framework_refs(kanban_root: "pathlib.Path | str") -> Optional[tuple[str, str]]:
    """The framework project's ``(remote, prefix)`` for :func:`latest_released_tag`.

    Read from ``<kanban_root>/projects/<FRAMEWORK_PROJECT>/project.cfg``;
    ``("origin", "")`` when the project has no config file. ``None`` when its
    ``git_remote_name`` is invalid, so the caller skips the tier.
    """
    cfg = project_cfg_file(pathlib.Path(kanban_root) / "projects" / FRAMEWORK_PROJECT)
    try:
        remote = git_remote.remote_name_from_cfg(cfg, FRAMEWORK_PROJECT)
    except git_remote.InvalidRemoteName:
        return None
    prefix = strip_one_quote_each_side(read_cfg_key(cfg, "project", "branch_prefix"), '"') if cfg else ""
    return remote, prefix


# ---------------------------------------------------------------------------
# kanban_version
# ---------------------------------------------------------------------------


def _version_file(directory: "pathlib.Path | str") -> str:
    """``<directory>/VERSION`` with all whitespace removed, or ``""``."""
    path = pathlib.Path(directory) / "VERSION"
    if not path.is_file():
        return ""
    try:
        return "".join(path.read_text(encoding="utf-8", errors="replace").split())
    except OSError:
        return ""


def dashboard_repo_root() -> pathlib.Path:
    """The repository root the dashboard's version lookup consults.

    Starting from this tree's ``scripts/dashboard/`` directory (the tree
    this package is installed in), the nearest ancestor holding a ``.git``
    directory; when there is none, the directory above the tree root.
    """
    tree_root = pathlib.Path(__file__).resolve().parents[2]
    candidate = tree_root / "scripts" / "dashboard"
    while candidate != candidate.parent:
        if (candidate / ".git").is_dir():
            return candidate
        candidate = candidate.parent
    return tree_root.parent


def kanban_version(
    kanban_root: "pathlib.Path | str",
    repo_root: "pathlib.Path | str | None" = None,
    last_released: str = "",
) -> str:
    """Return the deployed framework version by the tier order in the module docstring.

    Args:
        kanban_root:   The kanban root whose VERSION file is tier 1.
        repo_root:     The repository consulted by tiers 2 and 3; default
                       :func:`dashboard_repo_root`.
        last_released: Tier 4's value; ``""`` or ``none`` skips the tier.
    """
    repo = dashboard_repo_root() if repo_root is None else pathlib.Path(repo_root)
    version = _version_file(kanban_root) or _version_file(repo)
    if not version:
        refs = _framework_refs(kanban_root)
        if refs is not None:
            version = latest_released_tag(repo, *refs)
    if not version and last_released and last_released != "none":
        version = last_released
    return version or UNKNOWN_VERSION


# ---------------------------------------------------------------------------
# last_released_version
# ---------------------------------------------------------------------------


def _released_from_state(project_root: pathlib.Path) -> str:
    """``## Last Released`` from release-state.md when it is a ``vX.Y.Z`` version, else ``""``."""
    value = read_state_field(str(project_root / "release-state.md"), "Last Released")
    return value if _RELEASE_TAG_RE.match(value) else ""


def last_released_version(kanban_root: "pathlib.Path | str", project: str) -> str:
    """Return *project*'s last released version by the tiers in the module docstring.

    Args:
        kanban_root: The kanban root holding ``projects/<project>/``.
        project:     The project name.

    Returns:
        A ``vX.Y.Z`` version, or :data:`NOTHING_RELEASED`.
    """
    project_root = pathlib.Path(kanban_root) / "projects" / project
    cfg = project_cfg_file(project_root)
    dev_tree = read_cfg_key(cfg, "project", "dev_tree_path") if cfg is not None else ""
    if not dev_tree or _git(dev_tree, ["rev-parse", "--git-dir"]) is None:
        return _released_from_state(project_root) or NOTHING_RELEASED

    prefix = strip_one_quote_each_side(read_cfg_key(cfg, "project", "branch_prefix"), '"')
    main_branch = f"{prefix}main"
    # The project's own remote; an invalid git_remote_name skips the remote
    # tier (a read never refuses) and the scan continues from local refs.
    try:
        refs = [f"{git_remote.remote_name_from_cfg(cfg, project)}/{main_branch}", main_branch]
    except git_remote.InvalidRemoteName:
        refs = [main_branch]

    highest = ""
    for ref in refs:
        if not highest and _git(dev_tree, ["rev-parse", "--verify", ref]) is not None:
            highest = _highest_release_tag(_git(dev_tree, ["tag", "--merged", ref]) or "", prefix)
    if not highest:
        highest = _highest_release_tag(_git(dev_tree, ["tag", "-l"]) or "", prefix)
    return highest or NOTHING_RELEASED
