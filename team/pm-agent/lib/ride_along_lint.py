"""
ride_along_lint.py — the plan lint for a PM plan's ``ride_along`` list.

A ride-along is a deferred bug the PM tickets inside a requirement RC. The
plan names each one in its top-level ``ride_along`` list (a bug key such as
``BUG-NNNN`` or a bug file name; an object ``{"item": "BUG-NNNN"}`` is read
the same way). ``pm_materialize.py`` runs this lint before it writes
anything: a plan with any finding is refused whole, so no task folder, queue
line or status is written for it.

Rules
-----
Every finding names its rule and, where there is one, the bug.

``mixing``
    ``[release] bundle_mixing`` must resolve to ``ride-along`` for the
    project (project.cfg, then kanban.cfg, then the default ``separate``;
    :func:`pgai_agent_kanban.lib.defer_posture.resolve_defer_posture`). Under
    ``separate`` every ride-along entry is refused. A posture that fails to
    resolve is refused with the resolver's own message.
``count``
    At most ``[release] ride_along_max`` distinct bugs (a bug listed twice
    counts once).
``severity``
    No listed bug is ``high`` or ``critical`` (critical outranks high). The
    bug's ``**Severity:**`` is read as discovery reads it
    (:func:`pgai_agent_kanban.discovery.items.bug_severity`) and ranked by
    :func:`pgai_agent_kanban.lib.defer_posture.severity_rank`; a severity
    outside ``low|medium|high|critical``, or none, cannot be shown to be
    below high and is refused too.
``file-overlap``
    No listed bug names a file the requirement's ``## Deliverables`` section
    names (see "File matching").

Preconditions, checked for every entry, since a bug the lint cannot read
cannot be shown to pass the rules:

``entry``
    ``ride_along`` is a list, and each entry names a bug key.
``bug``
    The key resolves (``pgai_agent_kanban.ops.resolve_item``) to exactly one
    bug file of the project.
``status``
    The bug is ``open`` (or carries no ``## Status``), or it is ``running``
    and this requirement's ``## Bundled Items`` already lists it -- a re-run
    after an earlier run recorded it.
``requirement``
    The plan names a requirements file that can be read; the file-overlap
    rule reads its ``## Deliverables``.

File matching
-------------
A file name is a token of letters, digits, ``_``, ``.``, ``-`` and ``/``
that ends in an extension: a dot and a letter, then letters or digits.
Trailing ``.`` and leading ``./`` are dropped. A token without a ``/`` counts
only when its extension is at least two characters, so ``e.g.`` or ``v1.2.3``
is never a file. Tokens are read from the whole ``## Deliverables`` body and
from the whole bug file. Two tokens match when:

- they are equal;
- either one is a bare file name (no ``/``) and the basenames are equal --
  ``close.sh`` matches ``team/scripts/close.sh``; or
- one ends with ``/`` followed by the other -- ``scripts/close.sh`` matches
  ``team/scripts/close.sh``, ``docs/README.md`` does not match
  ``team/README.md``.

Matching is case-sensitive, as the file systems are. Over-matching only
refuses a plan, never lets a bug ride that should not.

Public API
----------
``lint_ride_along(entries, *, kanban_root, project, requirements_path) -> LintResult``
``error_key(findings) -> str``
``describe(findings) -> str``
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
from typing import Iterable, Optional, Sequence, Union

from pgai_agent_kanban.discovery.items import bug_severity
from pgai_agent_kanban.lib.bundle_items import parse_bundled_item_paths
from pgai_agent_kanban.lib.defer_posture import (
    DeferPostureError,
    resolve_defer_posture,
    severity_rank,
)
from pgai_agent_kanban.lib.terminal_states import OPEN_STATE, RUNNING_STATE, canonical_state
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import Ambiguous, NotFound, OpsError
from pgai_agent_kanban.ops.resolve import resolve_item
from pgai_agent_kanban.task_status import read_section

__all__ = [
    "Finding",
    "LintResult",
    "RideAlongBug",
    "describe",
    "error_key",
    "file_tokens",
    "lint_ride_along",
    "paths_match",
]

PathLike = Union[str, pathlib.Path]

#: The ``bundle_mixing`` value that lets a bug ride along.
RIDE_ALONG_MIXING = "ride-along"
#: Severity ranks at or above this one never ride (``high``; ``critical`` is 4).
_HIGH_RANK = 3
#: Keys an object entry may carry its bug under, first match wins.
_ENTRY_KEYS = ("item", "bug", "key")

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]*")
_EXTENSION_RE = re.compile(r"\.([A-Za-z][A-Za-z0-9]*)$")


@dataclasses.dataclass(frozen=True)
class Finding:
    """One reason the plan's ``ride_along`` list is refused.

    Attributes:
        rule:   ``mixing``, ``count``, ``severity``, ``file-overlap``,
                ``entry``, ``bug``, ``status`` or ``requirement``.
        item:   The bug (key or file name) the finding is about; ``""`` for
                a finding about the whole list.
        detail: Plain-language reason, naming the values involved.
    """

    rule: str
    item: str
    detail: str

    def describe(self) -> str:
        """One line: ``<rule>: <detail>``."""
        return f"{self.rule}: {self.detail}"


@dataclasses.dataclass(frozen=True)
class RideAlongBug:
    """A listed bug that passed every rule.

    Attributes:
        key:      The entry as the plan wrote it.
        path:     The bug file.
        severity: Its ``**Severity:**`` text.
    """

    key: str
    path: pathlib.Path
    severity: str


@dataclasses.dataclass(frozen=True)
class LintResult:
    """What :func:`lint_ride_along` found.

    Attributes:
        accepted: The bugs that ride, in plan order; empty when any finding
                  was made (a plan is accepted or refused whole).
        findings: Every finding, in rule order per bug.
    """

    accepted: tuple[RideAlongBug, ...] = ()
    findings: tuple[Finding, ...] = ()

    @property
    def ok(self) -> bool:
        """True when the lint made no finding."""
        return not self.findings


def error_key(findings: Sequence[Finding]) -> str:
    """The machine key for a refusal: ``ride_along_lint.<rule of the first finding>``."""
    rule = findings[0].rule if findings else "entry"
    return f"ride_along_lint.{rule.replace('-', '_')}"


def describe(findings: Iterable[Finding]) -> str:
    """Every finding on one line, separated by ``; ``."""
    return "; ".join(finding.describe() for finding in findings)


def file_tokens(text: str) -> set[str]:
    """The file-name tokens in *text* (see "File matching" in the module docstring)."""
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        token = raw.rstrip(".")
        while token.startswith("./"):
            token = token[2:]
        m = _EXTENSION_RE.search(token)
        if not m or token.startswith("."):
            continue
        if "/" not in token and len(m.group(1)) < 2:
            continue
        tokens.add(token.rstrip("/"))
    return tokens


def paths_match(a: str, b: str) -> bool:
    """True when file tokens *a* and *b* name the same file (module docstring)."""
    if a == b:
        return True
    if "/" not in a or "/" not in b:
        return a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]
    return a.endswith("/" + b) or b.endswith("/" + a)


def _entry_key(entry: object) -> str:
    """The bug key an entry names, or ``""`` when it names none."""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for name in _ENTRY_KEYS:
            value = entry.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _read(path: pathlib.Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _mixing_findings(kanban_root: PathLike, project: str) -> tuple[list[Finding], Optional[int]]:
    """Findings for the ``mixing`` rule, and the resolved ``ride_along_max``."""
    try:
        posture = resolve_defer_posture(kanban_root, project)
    except DeferPostureError as exc:
        return [Finding("mixing", "", f"the [release] posture does not resolve: {exc.message}")], None
    findings: list[Finding] = []
    mixing = posture.bundle_mixing
    if mixing.value != RIDE_ALONG_MIXING:
        cfg = pathlib.Path(kanban_root) / "projects" / project / "project.cfg"
        findings.append(Finding(
            "mixing", "",
            f"[release] bundle_mixing resolves to '{mixing.value}' (from {mixing.tier}), so no bug "
            f"may ride along in a requirement RC; set bundle_mixing = {RIDE_ALONG_MIXING} under "
            f"[release] in {cfg} to allow it, or drop ride_along from the plan",
        ))
    try:
        maximum = int(posture.ride_along_max.value)
    except ValueError:
        maximum = None
        findings.append(Finding(
            "count", "", f"[release] ride_along_max '{posture.ride_along_max.value}' is not a whole number",
        ))
    return findings, maximum


def lint_ride_along(
    entries: object,
    *,
    kanban_root: PathLike,
    project: str,
    requirements_path: Optional[PathLike],
) -> LintResult:
    """Check a plan's ``ride_along`` value against the rules and preconditions.

    Reads only; writes nothing.

    Args:
        entries:           The plan's ``ride_along`` value as parsed from
                           JSON. ``None`` or an empty list is accepted with
                           nothing riding (no rule is evaluated, and the
                           posture is not read).
        kanban_root:       The kanban root; the posture and the bugs are
                           read beneath it.
        project:           The project name.
        requirements_path: The requirement being decomposed (the plan's
                           ``requirements_path``); ``None`` or ``"none"``
                           when the plan names none.

    Returns:
        A :class:`LintResult`.
    """
    if entries is None or entries == []:
        return LintResult()
    if not isinstance(entries, list):
        return LintResult(findings=(Finding(
            "entry", "", f"ride_along must be a list of bug keys, not {type(entries).__name__}",
        ),))

    findings, maximum = _mixing_findings(kanban_root, project)

    requirement_text: Optional[str] = None
    requirement: Optional[pathlib.Path] = None
    if requirements_path and str(requirements_path).strip().lower() != "none":
        requirement = pathlib.Path(requirements_path)
        requirement_text = _read(requirement)
    if requirement_text is None:
        findings.append(Finding(
            "requirement", "",
            f"the plan's requirements file {requirement or '(none)'} cannot be read, so the "
            f"file-overlap rule cannot be checked",
        ))
    deliverables = file_tokens(read_section(requirement_text or "", "Deliverables") or "")
    already_listed = {pathlib.PurePath(p).name for p in parse_bundled_item_paths(requirement_text or "")}

    ctx = OpsContext(kanban_root=pathlib.Path(kanban_root))
    accepted: list[RideAlongBug] = []
    seen: set[pathlib.Path] = set()
    for entry in entries:
        key = _entry_key(entry)
        if not key:
            findings.append(Finding("entry", "", f"ride_along entry {entry!r} names no bug key"))
            continue
        try:
            found = resolve_item(ctx, project, key)
        except Ambiguous:
            findings.append(Finding("bug", key, f"{key} matches more than one item; give the whole bug file name"))
            continue
        except NotFound:
            findings.append(Finding("bug", key, f"{key} names no bug in project {project}"))
            continue
        except OpsError as exc:
            findings.append(Finding("bug", key, f"{key} cannot be resolved: {exc}"))
            continue
        if found.item_type != "bug":
            findings.append(Finding("bug", key, f"{key} is a {found.item_type}, not a bug"))
            continue
        path = found.path.resolve()
        if path in seen:
            continue
        seen.add(path)
        name = found.path.name
        state = canonical_state(found.state) or OPEN_STATE
        if state != OPEN_STATE and not (state == RUNNING_STATE and name in already_listed):
            findings.append(Finding(
                "status", name,
                f"{name} has ## Status '{found.state.strip()}'; only an open bug can ride along",
            ))

        try:
            severity = bug_severity(str(found.path))
        except OSError as exc:
            findings.append(Finding("severity", name, f"{name} cannot be read: {exc}"))
            continue
        rank = severity_rank(severity.split()[0] if severity.split() else "")
        if rank is None:
            findings.append(Finding(
                "severity", name,
                f"{name} has severity '{severity or '(none)'}', which is not one of "
                f"low|medium|high|critical, so it cannot be shown to be below high",
            ))
        elif rank >= _HIGH_RANK:
            findings.append(Finding(
                "severity", name, f"{name} is {severity.split()[0].lower()} severity; a high or critical bug never rides along",
            ))

        bug_text = _read(found.path) or ""
        overlaps = sorted(
            {d for d in deliverables for b in file_tokens(bug_text) if paths_match(d, b)}
        )
        if overlaps:
            findings.append(Finding(
                "file-overlap", name,
                f"{name} names {', '.join(overlaps)}, which the requirement's ## Deliverables names",
            ))
        accepted.append(RideAlongBug(key, found.path, severity))

    if maximum is not None and len(seen) > maximum:
        findings.append(Finding(
            "count", "",
            f"ride_along lists {len(seen)} bugs; [release] ride_along_max allows {maximum}",
        ))

    if findings:
        return LintResult(findings=tuple(findings))
    return LintResult(accepted=tuple(accepted))
