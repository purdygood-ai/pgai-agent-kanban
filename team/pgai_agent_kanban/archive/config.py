"""
config.py — resolve ``[archive] archive_count`` for a project.

``archive_count`` is how many of the newest items per category (bugs,
priorities, requirements, tasks — each counted separately, and tasks per
role) stay live when finished work is archived. ``0`` disables archiving.

Precedence (first non-blank value wins), the project.cfg-overrides-kanban.cfg
chain every per-project key uses::

    <kanban_root>/projects/<project>/project.cfg [archive] archive_count
    <kanban_root>/kanban.cfg                       [archive] archive_count
    built-in default                               55

The project's config file is located and read by the same rules the bash
resolvers apply (``pgai_agent_kanban.lib.project_cfg``: ``project.cfg``,
else a legacy ``PROJECT.cfg``; values read with the bash ``read_ini`` line
rules, inline comments stripped).

A value must be decimal digits only. Leading zeros are accepted (``055``
reads as 55). Anything else — a sign, a unit, a decimal point, a word — is
refused at the tier that supplied it; a lower tier is never consulted to
paper over a bad value, because the operator wrote that value on purpose.

Public API
----------
``resolve_archive_count(kanban_root, project) -> ArchiveCount``
    The count in force, with the tier and file it came from. Raises
    :class:`ArchiveCountError` (dual-channel) on an invalid value.

``effective_archive_count(kanban_root, project, *, stream=None) -> int``
    The count an archive run uses: the resolved value, or ``0`` — archive
    nothing — after printing the refusal when the value is invalid. Archiving
    is quality-class work, so an invalid setting stops archiving for that
    project and nothing else.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
import shlex
from typing import Optional, TextIO, Union

from pgai_agent_kanban.archive.layout import live_layout
from pgai_agent_kanban.lib import ini, project_cfg
from pgai_agent_kanban.lib.error_contract import emit_error_contract, format_error_contract

__all__ = [
    "ARCHIVE_COUNT_DEFAULT",
    "ARCHIVE_COUNT_KEY",
    "ARCHIVE_SECTION",
    "ArchiveCount",
    "ArchiveCountError",
    "TIER_DEFAULT",
    "TIER_KANBAN_CFG",
    "TIER_PROJECT_CFG",
    "effective_archive_count",
    "resolve_archive_count",
]

PathLike = Union[str, "os.PathLike[str]"]

ARCHIVE_SECTION = "archive"
ARCHIVE_COUNT_KEY = "archive_count"
#: Built-in default, the value kanban.cfg_example ships.
ARCHIVE_COUNT_DEFAULT = 55

#: Tier names reported in :attr:`ArchiveCount.tier`.
TIER_PROJECT_CFG = "project.cfg"
TIER_KANBAN_CFG = "kanban.cfg"
TIER_DEFAULT = "default"

_DIGITS_RE = re.compile(r"[0-9]+")


@dataclasses.dataclass(frozen=True)
class ArchiveCount:
    """The resolved ``archive_count``.

    Attributes:
        value:  Items kept live per category (per role for tasks); 0 means
                archiving is disabled.
        tier:   ``"project.cfg"``, ``"kanban.cfg"`` or ``"default"``.
        source: The file the value came from; ``""`` for the default.
    """

    value: int
    tier: str
    source: str

    @property
    def enabled(self) -> bool:
        """True when archiving runs at all (``value > 0``)."""
        return self.value > 0


class ArchiveCountError(Exception):
    """``[archive] archive_count`` holds a value that is not a whole number.

    ``str(exc)`` is the three-line dual-channel block.

    Attributes:
        error:   ``archive.invalid_archive_count``.
        message: The value, the file, and what happens instead.
        remedy:  A ``sed`` edit that rewrites the line to a valid value.
        file:    The file holding the invalid value.
    """

    def __init__(self, error: str, message: str, remedy: str, file: str) -> None:
        self.error = error
        self.message = message
        self.remedy = remedy
        self.file = file
        super().__init__(format_error_contract(error, message, remedy))


def _refuse(raw: str, cfg: str, project: str) -> ArchiveCountError:
    """Build the refusal for an invalid *raw* value read from *cfg*.

    The remedy keeps the digits the value started with when it had any
    (``40x`` suggests 40), else the built-in default.
    """
    leading = _DIGITS_RE.match(raw)
    suggested = leading.group(0) if leading else str(ARCHIVE_COUNT_DEFAULT)
    return ArchiveCountError(
        "archive.invalid_archive_count",
        f"archive: resolve_archive_count: [{ARCHIVE_SECTION}] {ARCHIVE_COUNT_KEY}='{raw}' in {cfg} "
        "is not a non-negative whole number (decimal digits only; 0 disables archiving); "
        f"nothing is archived for project '{project}' until it is fixed.",
        f"sed -i 's/^[[:space:]]*{ARCHIVE_COUNT_KEY}[[:space:]]*=.*/{ARCHIVE_COUNT_KEY} = {suggested}/' "
        f"{shlex.quote(cfg)}",
        cfg,
    )


def resolve_archive_count(kanban_root: PathLike, project: str) -> ArchiveCount:
    """Return the ``archive_count`` in force for *project*.

    Args:
        kanban_root: The kanban root; both config files are read beneath it.
        project:     Project name (a single path component).

    Raises:
        ArchiveCountError: The first non-blank value in the chain is not
            decimal digits.
        ArchiveLayoutError: *project* is not a valid project name.
    """
    root = pathlib.Path(kanban_root)
    tiers = []
    project_file = project_cfg.project_cfg_file(live_layout(root, project).root)
    if project_file is not None:
        tiers.append((TIER_PROJECT_CFG, project_file))
    tiers.append((TIER_KANBAN_CFG, root / "kanban.cfg"))

    for tier, cfg in tiers:
        if tier == TIER_PROJECT_CFG:
            raw = project_cfg.read_cfg_key(cfg, ARCHIVE_SECTION, ARCHIVE_COUNT_KEY)
        else:
            raw = ini.read_line_value(cfg, ARCHIVE_SECTION, ARCHIVE_COUNT_KEY) or ""
        if not raw:
            continue
        if not _DIGITS_RE.fullmatch(raw):
            # CONVERT (docs/blocking-site-census.md): an invalid count stops
            # archiving for this project only. effective_archive_count()
            # prints this refusal and archives nothing; no wake, release or
            # other project is held by it, and the next run retries.
            # CENSUS-ANCHOR: archive-count-invalid-01
            raise _refuse(raw, str(cfg), project)
        return ArchiveCount(int(raw), tier, str(cfg))

    return ArchiveCount(ARCHIVE_COUNT_DEFAULT, TIER_DEFAULT, "")


def effective_archive_count(
    kanban_root: PathLike, project: str, *, stream: Optional[TextIO] = None
) -> int:
    """Return the count an archive run uses for *project*.

    The resolved ``archive_count``, or ``0`` (archive nothing) when the
    configured value is refused — the refusal is printed dual-channel to
    *stream* (stderr by default) first, so the operator sees why nothing
    moved.

    Raises:
        ArchiveLayoutError: *project* is not a valid project name.
    """
    try:
        return resolve_archive_count(kanban_root, project).value
    except ArchiveCountError as exc:
        # CONVERT: the refusal raised at archive-count-invalid-01, reported;
        # the run continues with nothing to archive.
        emit_error_contract(exc.error, exc.message, exc.remedy, file=stream, project=project)
        return 0
