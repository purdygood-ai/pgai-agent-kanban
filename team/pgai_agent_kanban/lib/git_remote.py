"""
git_remote.py — the name of the git remote a project's release machinery
talks to.

Every push, fetch, ``ls-remote`` and remote-tracking ref (``<remote>/<branch>``)
the CM verbs, the push-lag checks and the released-version readers build
names its remote through this module rather than spelling ``origin``. The
Python twin of ``pp_git_remote_name`` / ``validate_git_remote_name`` in
``team/scripts/lib/project_paths.sh``: both apply the same rule to the same
file and refuse with the same dual-channel block, which
``tests/unit/test_git_remote_name_resolver.py`` checks value by value.

The rule
--------
The value of ``[project] git_remote_name`` in the project's ``project.cfg``
(or a legacy ``PROJECT.cfg``), read by
:func:`pgai_agent_kanban.lib.project_cfg.read_cfg_key` with one surrounding
double quote stripped from each side:

- absent, blank, or no config file at all -> :data:`DEFAULT_REMOTE_NAME`
  (``origin``);
- a valid remote name -> that name;
- anything else -> :class:`InvalidRemoteName`, never a guess.

A valid remote name is letters, digits, ``.``, ``_`` and ``-``, starting
with a letter or digit, with no ``..`` and no trailing ``.`` or ``.lock``.
That is deliberately narrower than everything git itself would accept: the
value is handed to ``git push`` / ``git fetch`` as an argument and spliced
into ``refs/remotes/<remote>/...``, so a leading ``-`` (read as an option), a
``/``, whitespace, a URL or a refname git would reject is refused before any
git command runs.

Public API
----------
    DEFAULT_REMOTE_NAME
        ``"origin"`` — the fallback for an absent or blank key.

    is_valid_remote_name(value) -> bool
        Whether *value* passes the rule above.

    remote_name_from_cfg(cfg_file, project="") -> str
        The resolved remote for one config file (``None``: no file).
        Raises :class:`InvalidRemoteName`.

    resolve_git_remote_name(project_root, project) -> str
        The same, locating the config file under *project_root* with
        :func:`pgai_agent_kanban.lib.project_cfg.project_cfg_file`.

    InvalidRemoteName
        Raised for a configured value that is not a valid remote name. Its
        ``error`` / ``message`` / ``remedy`` are the dual-channel fields and
        ``text`` the house-shape block
        (:func:`pgai_agent_kanban.lib.error_contract.format_error_contract`)
        a caller prints or re-raises.
"""

from __future__ import annotations

import pathlib
import re
from typing import Optional

from pgai_agent_kanban.lib.error_contract import format_error_contract
from pgai_agent_kanban.lib.project_cfg import project_cfg_file, read_cfg_key, strip_one_quote_each_side

__all__ = [
    "DEFAULT_REMOTE_NAME",
    "ERROR_KEY",
    "InvalidRemoteName",
    "is_valid_remote_name",
    "remote_name_from_cfg",
    "resolve_git_remote_name",
]

#: The remote used when ``[project] git_remote_name`` is absent or blank.
DEFAULT_REMOTE_NAME = "origin"

#: The machine key of the invalid-value refusal (shared with project_paths.sh).
ERROR_KEY = "git_remote_name.invalid"

_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_valid_remote_name(value: str) -> bool:
    """True when *value* is a remote name the release machinery may pass to git.

    Letters, digits, ``.``, ``_`` and ``-``, starting with a letter or digit;
    no ``..``; no trailing ``.`` or ``.lock``.
    """
    return bool(
        _REMOTE_NAME_RE.match(value)
        and ".." not in value
        and not value.endswith(".")
        and not value.endswith(".lock")
    )


class InvalidRemoteName(ValueError):
    """``[project] git_remote_name`` holds a value that cannot name a git remote.

    Attributes:
        value:    The configured value, as read (quotes stripped).
        cfg_file: The config file it came from.
        project:  The project name ("" when the caller gave none).
        error:    :data:`ERROR_KEY`.
        message:  Plain-language account naming the key, value and file.
        remedy:   The edit that fixes it.
        text:     The three-line house-shape block, newline-terminated.
    """

    def __init__(self, value: str, cfg_file: pathlib.Path, project: str = "", dev_tree: str = "") -> None:
        self.value = value
        self.cfg_file = cfg_file
        self.project = project
        self.error = ERROR_KEY
        self.message = invalid_remote_message(value, str(cfg_file), project)
        self.remedy = invalid_remote_remedy(str(cfg_file), dev_tree)
        self.text = format_error_contract(self.error, self.message, self.remedy) + "\n"
        super().__init__(self.message)


def invalid_remote_message(value: str, cfg_file: str, project: str) -> str:
    """The refusal's ``message`` field (byte-identical to project_paths.sh's)."""
    return (
        f"[project] git_remote_name is '{value}' for project '{project}' in {cfg_file}, "
        "which is not a valid git remote name (letters, digits, '.', '_' and '-', starting "
        "with a letter or digit; no '..', no trailing '.' or '.lock'); "
        "no git remote operation was run."
    )


def invalid_remote_remedy(cfg_file: str, dev_tree: str) -> str:
    """The refusal's ``remedy`` field (byte-identical to project_paths.sh's)."""
    listed = f" listed by 'git -C {dev_tree} remote'" if dev_tree else ""
    return (
        f"Set git_remote_name in the [project] section of {cfg_file} to a remote name{listed} "
        f"(for example: git_remote_name = {DEFAULT_REMOTE_NAME}), or delete the line to use "
        f"{DEFAULT_REMOTE_NAME}."
    )


def remote_name_from_cfg(cfg_file: Optional[pathlib.Path], project: str = "") -> str:
    """The remote *cfg_file* configures, by the rule in the module docstring.

    Args:
        cfg_file: The project's ``project.cfg`` / ``PROJECT.cfg``, or ``None``
            when the project has neither (resolves to the default).
        project:  The project name, used only in the refusal's message.

    Returns:
        The configured remote name, or :data:`DEFAULT_REMOTE_NAME`.

    Raises:
        InvalidRemoteName: the configured value fails :func:`is_valid_remote_name`.
    """
    if cfg_file is None:
        return DEFAULT_REMOTE_NAME
    raw = strip_one_quote_each_side(read_cfg_key(cfg_file, "project", "git_remote_name"), '"')
    if not raw.strip():
        return DEFAULT_REMOTE_NAME
    if is_valid_remote_name(raw):
        return raw
    dev_tree = read_cfg_key(cfg_file, "project", "dev_tree_path")
    # BLOCK-WORTHY (docs/blocking-site-census.md): a value that cannot name a
    # remote would hand git an option, a path or a URL where the release
    # expects its remote -- pushing to, deleting on or fetching from a
    # destination nobody configured -- so it refuses before any git command.
    # CENSUS-ANCHOR: git-remote-name-invalid-01
    raise InvalidRemoteName(raw, cfg_file, project, dev_tree)


def resolve_git_remote_name(project_root: "pathlib.Path | str", project: str) -> str:
    """The remote *project* (rooted at *project_root*) configures.

    Locates ``project.cfg`` (else ``PROJECT.cfg``) under *project_root* and
    applies :func:`remote_name_from_cfg`.

    Raises:
        InvalidRemoteName: see :func:`remote_name_from_cfg`.
    """
    return remote_name_from_cfg(project_cfg_file(pathlib.Path(project_root)), project)
