"""
defer_posture.py — the one Python reader of the deferral posture.

Every Python module that needs to know whether bugs are being deferred, at
which level, and until which release reads it here: the operator API's
posture reads, the ``ops.write`` defer/undefer verbs (which read an existing
campaign file before re-arming it), and the dashboard modules that turn an
already-resolved posture into a count or a card glyph. No other Python module
opens a ``DEFER-LOW-BUGS`` campaign file to read it.

Precedence (highest wins), the same chain ``resolve_defer_posture`` in
``team/scripts/lib/defer_posture.sh`` walks for bash callers::

    DEFER-LOW-BUGS campaign file > project.cfg [release] > kanban.cfg [release] > built-in default

The campaign file is project-scoped over root-scoped
(``<kanban_root>/projects/<project>/DEFER-LOW-BUGS`` wins over
``<kanban_root>/DEFER-LOW-BUGS``), as HALT is. While one is in force it forces
``deferred_bugs=true`` and ``deferred_bug_level=low``; ``defer_piggyback``
governs consolidation rather than which bugs wait, so it always resolves
through the cfg chain.

Both resolvers answer identically for the same tree: the INI tiers are read
with :func:`pgai_agent_kanban.lib.ini.read_line_value`, the line-for-line twin
of bash ``read_ini``, and every refusal carries the same machine key, message
and remedy text the bash resolver prints. A unit test drives both against the
same fixtures.

Public API
----------
``resolve_defer_posture(kanban_root, project=None) -> DeferPosture``
    The posture in force for *project* (root-only when *project* is empty:
    no project.cfg tier, no project-scoped campaign file). Raises
    :class:`DeferPostureError` -- never a partial or fallback answer -- when
    the campaign file in force has no parseable or no well-formed
    ``until=`` line, or a ``[release]`` value at the tier the chain actually
    lands on is outside its accepted set. A value at a tier never consulted
    is never read.

``campaign_file_path(project_root) -> Path``
    Where a project's campaign file lives.

``read_campaign_until(path) -> str``
    The raw value of a campaign file's first ``until=`` line, unvalidated;
    ``""`` when the file has none or cannot be read.

``parse_campaign_file(path) -> str``
    The validated ``until=`` value, or :class:`DeferPostureError`.

``is_well_formed_until(value) -> bool``
    Whether *value* is ``vX.Y.Z`` -- the one shape a campaign target takes.

``posture_mode(posture) -> str``
    The dashboard's three-word summary: ``"on"``, ``"off"``, or
    ``"malformed"`` (pass ``None`` for a posture that refused to resolve).

``severity_rank(value)`` / ``severity_defer_decision(severity, level)``
    Whether a bug of a given ``**Severity:**`` waits under a resolved
    level -- the Python form of ``_disc_severity_defer_decision`` in
    ``team/scripts/lib/discovery_defer.sh``.

This module only reads. Arming and clearing the campaign file are
``pgai_agent_kanban.ops.write.defer`` / ``undefer``.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
from typing import Optional, Union

from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib.error_contract import format_error_contract

__all__ = [
    "ACCEPTED_LEVELS",
    "CAMPAIGN_FILE_NAME",
    "DEFAULTS",
    "DeferPosture",
    "DeferPostureError",
    "MODE_MALFORMED",
    "MODE_OFF",
    "MODE_ON",
    "PostureValue",
    "SEVERITY_VOCABULARY",
    "TIER_CAMPAIGN_FILE",
    "TIER_DEFAULT",
    "TIER_KANBAN_CFG",
    "TIER_NONE",
    "TIER_PROJECT_CFG",
    "campaign_file_path",
    "is_well_formed_until",
    "parse_campaign_file",
    "posture_mode",
    "read_campaign_until",
    "resolve_defer_posture",
    "severity_defer_decision",
    "severity_rank",
]

PathLike = Union[str, "os.PathLike[str]"]

#: File name of the deferral campaign file, at a project root or the kanban root.
CAMPAIGN_FILE_NAME = "DEFER-LOW-BUGS"

#: Tier names, as the bash resolver reports them.
TIER_CAMPAIGN_FILE = "campaign file"
TIER_PROJECT_CFG = "project.cfg"
TIER_KANBAN_CFG = "kanban.cfg"
TIER_DEFAULT = "default"
#: Tier of ``until`` and ``campaign_file`` when no campaign file is in force.
TIER_NONE = "none"

#: Built-in defaults for the three ``[release]`` keys.
DEFAULTS = {
    "deferred_bugs": "false",
    "deferred_bug_level": "low",
    "defer_piggyback": "true",
}

#: Values ``deferred_bug_level`` accepts. ``critical`` is deliberately absent:
#: a level names the highest rung the chain may be told to wait on, and a
#: critical bug must never wait.
ACCEPTED_LEVELS = ("low", "medium", "high")

_BOOLEAN_KEYS = ("deferred_bugs", "defer_piggyback")

#: Bug severity rungs, weakest first; index + 1 is the rank. Wider than
#: ACCEPTED_LEVELS by ``critical``, which outranks every settable level.
SEVERITY_VOCABULARY = ("low", "medium", "high", "critical")

#: The dashboard's posture summary words.
MODE_OFF = "off"
MODE_ON = "on"
MODE_MALFORMED = "malformed"

# The bash [[:space:]] class in the C locale.
_POSIX_SPACE = " \t\n\r\v\f"
_UNTIL_LINE_RE = re.compile(r"^[ \t\n\r\v\f]*until[ \t\n\r\v\f]*=[ \t\n\r\v\f]*(.*)$")
_WELL_FORMED_UNTIL_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")


@dataclasses.dataclass(frozen=True)
class PostureValue:
    """One resolved field: its value and the tier it came from."""

    value: str
    tier: str


@dataclasses.dataclass(frozen=True)
class DeferPosture:
    """The deferral posture in force, field by field.

    ``until`` and ``campaign_file`` share one tier: ``"campaign file"`` when
    a campaign file is in force (``campaign_file.value`` is its path), else
    ``"none"`` with empty values.
    """

    deferred_bugs: PostureValue
    deferred_bug_level: PostureValue
    defer_piggyback: PostureValue
    until: PostureValue
    campaign_file: PostureValue

    @property
    def deferring(self) -> bool:
        """True when bugs at or below the resolved level are being deferred."""
        return self.deferred_bugs.value == "true"

    def resolver_lines(self) -> str:
        """Return the five ``<field>=<value>|<tier>`` lines the bash resolver prints."""
        fields = (
            ("deferred_bugs", self.deferred_bugs),
            ("deferred_bug_level", self.deferred_bug_level),
            ("defer_piggyback", self.defer_piggyback),
            ("until", self.until),
            ("campaign_file", self.campaign_file),
        )
        return "".join(f"{name}={field.value}|{field.tier}\n" for name, field in fields)


class DeferPostureError(Exception):
    """The posture cannot be resolved: a campaign file or cfg value is invalid.

    ``str(exc)`` is the three-line dual-channel block the bash resolver
    prints for the same fault.

    Attributes:
        error:   Stable machine key (``defer_posture.<what>``).
        message: What is wrong, naming the file.
        remedy:  The edit that fixes it.
        file:    The file whose content caused the refusal.
    """

    def __init__(self, error: str, message: str, remedy: str, file: PathLike) -> None:
        self.error = error
        self.message = message
        self.remedy = remedy
        self.file = os.fspath(file)
        super().__init__(format_error_contract(error, message, remedy))


# ---------------------------------------------------------------------------
# Campaign file
# ---------------------------------------------------------------------------


def campaign_file_path(project_root: PathLike) -> pathlib.Path:
    """Return the campaign file path under *project_root* (or the kanban root)."""
    return pathlib.Path(project_root) / CAMPAIGN_FILE_NAME


def is_well_formed_until(value: str) -> bool:
    """Return True when *value* is exactly ``vX.Y.Z`` (ASCII digits)."""
    return _WELL_FORMED_UNTIL_RE.fullmatch(value) is not None


def _first_until_line(path: PathLike) -> Optional[str]:
    """Return the first ``until=`` line's value with surrounding blanks trimmed.

    ``None`` when the file has no such line or cannot be read. The value is
    everything after the first ``=``, as the bash parser's ``sed`` keeps it.
    """
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.split("\n"):
        match = _UNTIL_LINE_RE.match(line)
        if match:
            return match.group(1).rstrip(_POSIX_SPACE)
    return None


def read_campaign_until(path: PathLike) -> str:
    """Return the raw ``until=`` value of the campaign file at *path*.

    Unvalidated: a malformed value is returned as written. ``""`` when the
    file is missing, unreadable, or carries no ``until=`` line.
    """
    value = _first_until_line(path)
    return value if value is not None else ""


def parse_campaign_file(path: PathLike) -> str:
    """Return the campaign file's validated ``until=`` value.

    Raises:
        DeferPostureError: The file has no ``until=`` line
            (``defer_posture.campaign_unparseable``) or its value is not
            ``vX.Y.Z`` (``defer_posture.campaign_malformed_until``). An
            unparseable campaign file refuses rather than reading as "no
            campaign": the file exists to force deferral on, so ignoring it
            would silently turn deferral off.
    """
    file = os.fspath(path)
    value = _first_until_line(path)
    if value is None:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a campaign file that
        # cannot be parsed would otherwise read as deferral-off, the opposite
        # of what the file is for; refused, as the bash resolver refuses.
        # CENSUS-ANCHOR: defer-posture-campaign-unparseable-01
        raise DeferPostureError(
            "defer_posture.campaign_unparseable",
            f"defer_posture: resolve_defer_posture: campaign file {file} has no parseable 'until=' line.",
            f"Add a line reading 'until=vX.Y.Z' to {file}, naming the release the deferral campaign ends at.",
            file,
        )
    if not is_well_formed_until(value):
        # BLOCK-WORTHY (docs/blocking-site-census.md): a campaign target that
        # is not vX.Y.Z names no release the campaign could end at; refused
        # rather than guessed, as the bash resolver refuses.
        # CENSUS-ANCHOR: defer-posture-campaign-malformed-until-01
        raise DeferPostureError(
            "defer_posture.campaign_malformed_until",
            f"defer_posture: resolve_defer_posture: campaign file {file} until='{value}' is not well-formed semver.",
            f"Set 'until=' in {file} to a well-formed vX.Y.Z version (got '{value}').",
            file,
        )
    return value


# ---------------------------------------------------------------------------
# cfg tiers
# ---------------------------------------------------------------------------


def _validate(key: str, value: str, cfg: str) -> None:
    """Refuse a ``[release]`` value outside its key's accepted set."""
    if key in _BOOLEAN_KEYS:
        if value in ("true", "false"):
            return
        # BLOCK-WORTHY (docs/blocking-site-census.md): an unrecognised
        # boolean cannot be resolved into a posture nobody asked for;
        # 'on'/'off' are not accepted spellings.
        # CENSUS-ANCHOR: defer-posture-invalid-boolean-01
        raise DeferPostureError(
            f"defer_posture.invalid_{key}",
            f"defer_posture: resolve_defer_posture: [release] {key}='{value}' in {cfg} is not an accepted value.",
            f"Set [release] {key} to 'true' or 'false' in {cfg} (accepted: true|false; 'on'/'off' are not accepted spellings).",
            cfg,
        )
    if value in ACCEPTED_LEVELS:
        return
    # BLOCK-WORTHY (docs/blocking-site-census.md): an unrecognised level --
    # 'critical' included, which no setting may defer -- is refused rather
    # than guessed.
    # CENSUS-ANCHOR: defer-posture-invalid-level-01
    raise DeferPostureError(
        "defer_posture.invalid_deferred_bug_level",
        f"defer_posture: resolve_defer_posture: [release] deferred_bug_level='{value}' in {cfg} is not an accepted value.",
        f"Set [release] deferred_bug_level to one of low|medium|high in {cfg}.",
        cfg,
    )


def _tiered_value(key: str, project_cfg: str, kanban_cfg: str) -> PostureValue:
    """Walk project.cfg > kanban.cfg > default for one ``[release]`` key.

    An empty *project_cfg* means root-only. A tier whose value is blank or
    absent passes to the next; the first non-blank value is validated and
    used, and a lower tier is never consulted after it.
    """
    for cfg, tier in ((project_cfg, TIER_PROJECT_CFG), (kanban_cfg, TIER_KANBAN_CFG)):
        if not cfg:
            continue
        value = ini.read_line_value(cfg, "release", key)
        if value:
            _validate(key, value, cfg)
            return PostureValue(value, tier)
    return PostureValue(DEFAULTS[key], TIER_DEFAULT)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve_defer_posture(kanban_root: PathLike, project: Optional[str] = None) -> DeferPosture:
    """Return the deferral posture in force for *project*.

    Args:
        kanban_root: The kanban root. Paths in the result and in refusals
                     are built from it as given.
        project:     Project name; ``None`` or ``""`` resolves root-only
                     (kanban.cfg and the root campaign file only).

    Raises:
        DeferPostureError: See the module docstring.
    """
    root = os.fspath(kanban_root)
    kanban_cfg = f"{root}/kanban.cfg"
    project_cfg = ""
    project_campaign = ""
    if project:
        project_cfg = f"{root}/projects/{project}/project.cfg"
        project_campaign = f"{root}/projects/{project}/{CAMPAIGN_FILE_NAME}"
    root_campaign = f"{root}/{CAMPAIGN_FILE_NAME}"

    campaign_file = ""
    if project_campaign and os.path.isfile(project_campaign):
        campaign_file = project_campaign
    elif os.path.isfile(root_campaign):
        campaign_file = root_campaign

    if campaign_file:
        until = PostureValue(parse_campaign_file(campaign_file), TIER_CAMPAIGN_FILE)
        campaign = PostureValue(campaign_file, TIER_CAMPAIGN_FILE)
        deferred_bugs = PostureValue("true", TIER_CAMPAIGN_FILE)
        level = PostureValue("low", TIER_CAMPAIGN_FILE)
    else:
        until = PostureValue("", TIER_NONE)
        campaign = PostureValue("", TIER_NONE)
        deferred_bugs = _tiered_value("deferred_bugs", project_cfg, kanban_cfg)
        level = _tiered_value("deferred_bug_level", project_cfg, kanban_cfg)

    piggyback = _tiered_value("defer_piggyback", project_cfg, kanban_cfg)
    return DeferPosture(
        deferred_bugs=deferred_bugs,
        deferred_bug_level=level,
        defer_piggyback=piggyback,
        until=until,
        campaign_file=campaign,
    )


def posture_mode(posture: Optional[DeferPosture]) -> str:
    """Return ``"on"``, ``"off"`` or ``"malformed"`` for a resolved posture.

    *posture* is ``None`` when resolution refused; that reads as
    ``"malformed"``, the word the dashboard renders a warning glyph for.
    """
    if posture is None:
        return MODE_MALFORMED
    return MODE_ON if posture.deferring else MODE_OFF


# ---------------------------------------------------------------------------
# Per-bug decision under a resolved level
# ---------------------------------------------------------------------------


def severity_rank(value: str) -> Optional[int]:
    """Return 1-4 for a recognised severity rung, or ``None``.

    Matched case-insensitively after trimming surrounding whitespace and then
    one trailing period.
    """
    norm = value.lower().strip(_POSIX_SPACE)
    if norm.endswith("."):
        norm = norm[:-1]
    try:
        return SEVERITY_VOCABULARY.index(norm) + 1
    except ValueError:
        return None


def severity_defer_decision(severity: str, level: str) -> str:
    """Return ``"deferred"``, ``"kept"`` or ``"unparseable"``.

    A bug of *severity* is deferred when its rank is at or below *level*'s
    rank; an unrecognised severity or level is ``"unparseable"`` (never
    deferred). ``critical`` outranks every settable level, so it is always
    kept.
    """
    rank = severity_rank(severity)
    if rank is None:
        return "unparseable"
    level_rank = severity_rank(level)
    if level_rank is None:
        return "unparseable"
    return "deferred" if rank <= level_rank else "kept"
