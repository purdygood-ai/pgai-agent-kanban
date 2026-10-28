"""
resolution_matrix.py — Config-declared resolution matrix and fail-closed
matcher for OVERWATCH's autonomous block resolution.

This module is the matrix loader plus the matcher engine; it does not
execute anything — the execution stage that performs the prescribed
remediation is pgai_agent_kanban.overwatch.resolution_stage (see
team/roles/OVERWATCH.md's "The matrix consultation"). Nothing in this
module writes to disk, runs a release-affecting command, or touches
origin.

Two layers:

  1. Pure matching (the heart of the authority boundary). ``BlockFacts``
     carries six deterministic facts:
     trigger class, findings-all-filed, anything-shipped-or-tagged,
     approval-gate-present, operator-commits-in-lineage, and
     attempts-below-ceiling. ``match_resolution(facts, matrix)`` compares a
     ``BlockFacts`` against a loaded matrix top-to-bottom and returns the
     first matching row's resolution, or the ``HALT_HARD`` sentinel when
     nothing matches. This layer is total, side-effect-free, and the one
     the acceptance criteria's golden/fail-closed fixtures exercise most
     directly.

  2. Evidence extraction. ``BlockEvidence`` bundles the raw, already-located
     artifacts a caller (OVERWATCH's resolution stage) has gathered for a
     specific blocked RC — a HALT file path, a TESTER report path, a
     release-state JSON path, project/task-graph paths, and git refs.
     ``extract_facts(evidence)`` turns that evidence into a ``BlockFacts``.
     Every extraction helper is deliberately fail-closed: when the evidence
     needed to *prove* a fact is absent, the helper returns whichever value
     keeps the block furthest from matching the shipped CANCEL-AND-REBUNDLE
     row (see each helper's docstring for its specific default). The one
     exception is the retry ceiling: a missing ``[release] max_rc_retries``
     is never silently defaulted — it raises ``CeilingConfigError`` instead,
     fail-loud, whenever the resolver needs the value and cannot resolve it.

``resolve_block(evidence, ...)`` is the convenience entry point that loads
the matrix, extracts facts, and matches in one call — the shape OVERWATCH's
resolution stage is expected to use.

The same file's ``triggers`` list holds one row per CM trigger number:
which cause checks clear that trigger's chain HALT (``clears_on``) and
whether the stage runs the fact matrix or waits while none passes
(``while_open``). ``load_trigger_rows()`` loads and validates it; the
checks themselves are pgai_agent_kanban.overwatch.halt_clearing.

A third, independent reader lives here too: ``resolve_overwatch_resolve()``
is the Python counterpart of ``pp_overwatch_resolve()``
(team/scripts/lib/project_paths.sh) — the master switch gating whether
OVERWATCH may act autonomously on a block at all, as opposed to
``resolve_max_rc_retries()``'s narrower retry-ceiling concern. Same
project.cfg-overrides-kanban.cfg precedence, opposite fail-safe direction:
absence resolves to False rather than raising. Its sibling
``resolve_block_triage()`` is the Python counterpart of ``pp_block_triage()``
— whether the block-triage sweep may act on a Block Record or only record
and diagnose — with the same resolver semantics under ``[overwatch]``, but
absence resolves to True, the shipped default.

``resolve_ship_posture()`` is the Python counterpart of ``pp_ship_posture()``
— the project's ship posture (``"matrix"`` or ``"always"``) through the
same project.cfg-then-kanban.cfg chain, ``"always"`` when neither sets it,
with a remote-lane guard that keeps a ``push_to_remote`` lane on
``"matrix"`` unless ``ship_posture_confirm_remote = true``. It never
raises: an unrecognized value and a cfg-set ``always`` on an unconfirmed
remote lane both resolve to ``"matrix"`` with one warning line.

Usage (CLI):
    python3 resolution_matrix.py resolve <evidence.json> [--matrix <path>] [--kanban-root <path>]
    python3 resolution_matrix.py match <facts.json> [--matrix <path>] [--kanban-root <path>]

Usage (import):
    from pgai_agent_kanban.overwatch.resolution_matrix import (
        BlockEvidence, BlockFacts, resolve_block, match_resolution, HALT_HARD,
    )
    result = resolve_block(evidence, kanban_root="/home/op/pgai_agent_kanban")
    if result.resolution == HALT_HARD:
        ...  # decline, narrate result.facts
    else:
        ...  # execute result.resolution (a separate follow-on task's job)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from typing import NoReturn, Optional

import yaml

# Run by file path (``python3 .../resolution_matrix.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.cm.ship_policy_report_inputs import all_findings  # noqa: E402
from pgai_agent_kanban.reports.tester_report import TesterReport, parse_report  # noqa: E402
from pgai_agent_kanban.lib import ini, project_cfg  # noqa: E402
from pgai_agent_kanban.lib.branch_prefix import release_tag_name  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sentinel returned by match_resolution() when no matrix row matches a
# block's facts. Fail-closed default: never a matrix row's own resolution
# value (load_matrix() rejects a row that tries to declare this literally).
HALT_HARD = "HALT-hard"

# The shipped default matrix's sole resolution.
CANCEL_AND_REBUNDLE = "CANCEL-AND-REBUNDLE"

# The trigger class of a chain HALT CM's release wrote (the HALT header
# names CM as its creator).
TRIGGER_CLASS_SHIP_POLICY_HALT = "ship-policy-halt"

# The trigger class the release passes for a squash into main that
# conflicted (trigger 5). No HALT carries it: the release asks the matrix
# whether the RC may be cancelled and rebundled before it writes any stop
# (pgai_agent_kanban.overwatch.resolution_stage.resolve_squash_conflict).
TRIGGER_CLASS_SQUASH_CONFLICT = "squash-conflict"

# Exact key set every matrix row must declare — six facts plus the
# prescribed resolution. Extra or missing keys fail matrix loading loudly
# rather than silently ignoring a typo.
_REQUIRED_ROW_KEYS = frozenset(
    {
        "trigger_class",
        "findings_all_filed",
        "shipped_or_tagged",
        "approval_gate_present",
        "operator_commits_in_lineage",
        "attempts_below_ceiling",
        "resolution",
    }
)

_BOOL_ROW_KEYS = (
    "findings_all_filed",
    "shipped_or_tagged",
    "approval_gate_present",
    "operator_commits_in_lineage",
    "attempts_below_ceiling",
)

# HALT file header CM writes verbatim (team/roles/CM.md "HALT procedure",
# step 1). A HALT file carrying this line was created by CM for a
# ship-policy trigger; any other content (or an absent file) is not a
# ship-policy HALT as far as this matcher is concerned.
_CM_HALT_HEADER_RE = re.compile(r"^#\s*HALT created by CM at\b", re.MULTILINE)

# "Not filed" sentinel values a finding's "filed_as" might carry when it was,
# for whatever reason, not filed. matrix_findings() already returns None for
# such a finding; these cover a caller's pre-parsed BlockEvidence.
# tester_findings, which may carry the value as the report wrote it. Matched
# case-insensitively against the stripped value.
_NOT_FILED_VALUES = {"", "n/a", "none", "-", "not filed", "tbd"}

# Finding categories that record a passing check rather than a defect.
# TESTER files nothing for them (team/roles/TESTER.md, "## Findings": only a
# bug, gap or stale-assertion carries Filed As), so findings_all_filed()
# skips them. Exact parser tokens only (Finding.category is already one
# lowercase token or None): every other category, and a missing or
# unrecognised one, still counts -- the check fails closed.
_NO_FILING_CATEGORIES = frozenset({"pass", "pass-with-caveat"})

# Finding.regression token -> the bool a matrix finding carries (see
# matrix_findings()); an absent or unreadable value maps to None.
_REGRESSION_FLAGS = {"yes": True, "no": False}

# Lane classification constants — the three-lane OVERWATCH block classifier
# (see classify_lane() below). Never confused with HALT_HARD: HALT_HARD is
# match_resolution()'s "no matrix row matched" sentinel for the pre-existing
# CANCEL-AND-REBUNDLE matrix; the three lanes are what a HALT_HARD result
# further resolves into when resolution_stage.py's caller has
# overwatch_resolve=true (see that module's "three-lane classification"
# section). LANE_A/LANE_B are classification-only in this module — the
# recovery/force-ship playbooks that ACT on a Lane A/B classification live
# in resolution_stage.py (its "Lane A recovery playbook" and "Lane B
# force-ship playbook" sections).
LANE_A = "A"  # bad-fix regression
LANE_B = "B"  # declared operator intent
LANE_C = "C"  # everything else — the default, never a fallback reached for

# The two ship postures resolve_ship_posture() returns (see that function).
# "matrix" is the default and applies the ship-policy matrix as written.
SHIP_POSTURE_MATRIX = "matrix"
SHIP_POSTURE_ALWAYS = "always"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ResolutionMatrixError(Exception):
    """Base class for all errors raised by this module."""


class MatrixError(ResolutionMatrixError):
    """Raised when a resolution matrix file is missing, malformed, or invalid.

    Malformed means: not a YAML mapping, no 'rows' list, a row missing a
    required key, a row with an extra/unrecognized key, a row with the
    wrong type for a fact, or a row whose 'resolution' is the literal
    HALT_HARD sentinel (reserved for "no match", never a real action).
    """


class CeilingConfigError(ResolutionMatrixError):
    """Raised when [release] max_rc_retries cannot be resolved.

    Mirrors pp_max_rc_retries() in team/scripts/lib/project_paths.sh:
    raised when the key is absent from BOTH the project's project.cfg and
    the kanban-wide kanban.cfg, or when either source supplies a
    non-integer value. This is never silently defaulted — the ceiling is
    required at resolve time.
    """


class InvalidOverwatchResolveValue(ResolutionMatrixError):
    """Raised when [release] overwatch_resolve holds a value that is
    neither 'true' nor 'false'.

    Mirrors pp_overwatch_resolve()'s refusal in
    team/scripts/lib/project_paths.sh: absence, blank, or an unreadable/
    unparseable config file resolves to False and never raises this — only
    a present, non-blank, non-boolean value does. This is the opposite
    fail-safe direction from CeilingConfigError above: overwatch_resolve
    gates an autonomy grant, so silence must resolve to the safe default
    (False) rather than an error, and only a value that could not possibly
    mean either boolean is refused.
    """


class InvalidBlockTriageValue(ResolutionMatrixError):
    """Raised when [overwatch] block_triage holds a value that is neither
    'true' nor 'false'.

    Mirrors pp_block_triage()'s refusal in team/scripts/lib/project_paths.sh:
    absence, blank, or an unreadable/unparseable config file resolves to
    the shipped default (True) and never raises this — only a present,
    non-blank, non-boolean value does, since guessing it into either
    boolean could grant or withhold the triage sweep's authority to act
    against the operator's intent.
    """


class EvidenceError(ResolutionMatrixError):
    """Raised when evidence extraction hits an unexpected plumbing failure.

    Distinct from a fact simply being "unknown" (which extraction helpers
    resolve to a fail-closed default value, not an exception). This is for
    genuine environment problems — a git command failing against a path
    that was supposed to be a valid repository, a ref that does not exist —
    that indicate the caller's evidence is broken rather than merely
    incomplete. Callers should surface this distinctly from a HALT-hard
    decline: HALT-hard is a considered policy outcome, EvidenceError means
    the matcher could not even evaluate the block.
    """


# ---------------------------------------------------------------------------
# Matrix row / fact dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatrixRow:
    """One row of the resolution matrix: six facts plus a prescribed resolution."""

    trigger_class: str
    findings_all_filed: bool
    shipped_or_tagged: bool
    approval_gate_present: bool
    operator_commits_in_lineage: bool
    attempts_below_ceiling: bool
    resolution: str

    def facts_key(self) -> tuple:
        """Return the six facts as an order-stable tuple for equality comparison."""
        return (
            self.trigger_class,
            self.findings_all_filed,
            self.shipped_or_tagged,
            self.approval_gate_present,
            self.operator_commits_in_lineage,
            self.attempts_below_ceiling,
        )


@dataclass(frozen=True)
class BlockFacts:
    """The six deterministic facts extracted from a block, plus narration extras.

    The first six fields are what match_resolution() compares against
    matrix rows. ``attempt`` and ``ceiling`` are carried alongside for
    narration/debugging (e.g. a future HALT-hard block message that wants
    to say "attempt 3/3") — they are not matched against directly; only
    the derived ``attempts_below_ceiling`` boolean participates in matching.
    """

    trigger_class: str
    findings_all_filed: bool
    shipped_or_tagged: bool
    approval_gate_present: bool
    operator_commits_in_lineage: bool
    attempts_below_ceiling: bool
    attempt: Optional[int] = None
    ceiling: Optional[int] = None

    def facts_key(self) -> tuple:
        """Return the six matchable facts as an order-stable tuple."""
        return (
            self.trigger_class,
            self.findings_all_filed,
            self.shipped_or_tagged,
            self.approval_gate_present,
            self.operator_commits_in_lineage,
            self.attempts_below_ceiling,
        )

    def to_dict(self) -> dict:
        """Return every block fact as a dict, keyed by field name."""
        return asdict(self)


@dataclass(frozen=True)
class ResolutionResult:
    """The outcome of resolve_block(): the matched resolution plus its facts."""

    resolution: str
    facts: BlockFacts
    matched_row: Optional[MatrixRow]

    def to_dict(self) -> dict:
        """Return the result as a JSON-ready dict.

        Keys: ``resolution``, ``facts`` (``BlockFacts.to_dict``) and
        ``matched_row`` (the matrix row's fields, or None when no row matched).
        """
        return {
            "resolution": self.resolution,
            "facts": self.facts.to_dict(),
            "matched_row": asdict(self.matched_row) if self.matched_row else None,
        }


@dataclass
class BlockEvidence:
    """Raw, already-located evidence for one blocked RC.

    Every field is optional. Extraction helpers treat an absent field as
    "cannot be proven" and resolve to whichever fact value keeps the block
    furthest from the shipped CANCEL-AND-REBUNDLE row (see each private
    ``_fact_*`` helper below) — except the retry ceiling fields, which
    raise ``CeilingConfigError`` when unresolvable rather than defaulting.

    Fields:
        trigger_class:        Explicit override for the trigger-class fact.
                               When set, halt_file_path is not consulted.
        halt_file_path:        Path to the HALT file that produced the
                               block (e.g. $KANBAN_ROOT/HALT). Consulted
                               for trigger_class when trigger_class is None.
        tester_findings:        Pre-parsed findings list — each item a dict
                               with at least a "filed_as" key, and
                               optionally a "category" key in
                               matrix_findings()'s vocabulary. Takes
                               precedence over tester_report_path.
        tester_report_path:     Path to a TESTER verification report
                               markdown file; read through parse_report
                               into matrix_findings() when
                               tester_findings is None.
        release_state_path:     Path to the blocked RC's per-RC
                               release-state JSON (release-state/<version>.json,
                               written by pgai_agent_kanban.cm.write_rc_state).
                               Consulted for 'outcome' (shipped_or_tagged)
                               and 'attempt' (attempts_below_ceiling).
        dev_tree_path:          Path to the project's git dev tree, used for
                               the git-tag check (shipped_or_tagged) and the
                               RC-lineage walk (operator_commits_in_lineage).
        target_version:         The blocked RC's version string (e.g.
                               "v2.0.1"). Used for the git-tag check and the
                               HUMAN-APPROVE task glob.
        branch_prefix:          The project's configured branch_prefix
                               (e.g. "ai_"). The git-tag check looks for the
                               tag the release cuts, release_tag_name(
                               target_version, branch_prefix) -- "ai_v2.0.1"
                               on a prefixed project. None or "" checks the
                               bare version, an unprefixed project's tag.
        rc_branch:              The RC branch name (e.g. "ai_rc/v2.0.1"),
                               used for the RC-lineage walk.
        base_branch:            The branch the RC branched from (e.g.
                               "ai_main"), used as the lineage walk's
                               boundary.
        project_tasks_dir:      Path to projects/<name>/tasks, globbed for
                               HUMAN-APPROVE-<version>-* (approval_gate_present).
        attempt:                Explicit override for the attempt number.
                               Takes precedence over reading it from
                               release_state_path.
        max_rc_retries:         Explicit override for the retry ceiling.
                               Takes precedence over resolving it from
                               project_name/kanban_root via project.cfg /
                               kanban.cfg.
        project_name:           Project name, used with kanban_root to
                               resolve max_rc_retries when max_rc_retries
                               is not given directly.
        kanban_root:            Kanban root path, used with project_name to
                               resolve max_rc_retries when max_rc_retries
                               is not given directly.
    """

    trigger_class: Optional[str] = None
    halt_file_path: Optional[str] = None
    tester_findings: Optional[list] = None
    tester_report_path: Optional[str] = None
    release_state_path: Optional[str] = None
    dev_tree_path: Optional[str] = None
    target_version: Optional[str] = None
    branch_prefix: Optional[str] = None
    rc_branch: Optional[str] = None
    base_branch: Optional[str] = None
    project_tasks_dir: Optional[str] = None
    attempt: Optional[int] = None
    max_rc_retries: Optional[int] = None
    project_name: Optional[str] = None
    kanban_root: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "BlockEvidence":
        """Construct from a plain dict (e.g. parsed JSON), ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


# ---------------------------------------------------------------------------
# Matrix loading
# ---------------------------------------------------------------------------


def _default_matrix_path() -> str:
    """Path to the shipped default matrix, next to this module."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolution-matrix.yaml")


def _resolve_matrix_path(matrix_path: Optional[str], kanban_root: Optional[str]) -> str:
    """Resolve the matrix file to load.

    Search order (first match wins), mirroring the project-local-override
    pattern team/pm-agent/lib/workflow_loader.py already establishes for
    config-declared, operator-extensible YAML:

      1. matrix_path, when given explicitly by the caller.
      2. <kanban_root>/resolution-matrix.yaml — an operator override that
         replaces the shipped defaults entirely without touching the dev
         tree. Only consulted when kanban_root is given and the file exists.
      3. The shipped default next to this module (exactly one
         CANCEL-AND-REBUNDLE row).
    """
    if matrix_path:
        return matrix_path
    if kanban_root:
        override = os.path.join(kanban_root, "resolution-matrix.yaml")
        if os.path.isfile(override):
            return override
    return _default_matrix_path()


def _validate_row(raw: dict, index: int, path: str) -> MatrixRow:
    if not isinstance(raw, dict):
        raise MatrixError(
            f"Row {index} in matrix '{path}' must be a mapping, got {type(raw).__name__}"
        )
    keys = set(raw.keys())
    missing = _REQUIRED_ROW_KEYS - keys
    extra = keys - _REQUIRED_ROW_KEYS
    if missing:
        raise MatrixError(
            f"Row {index} in matrix '{path}' is missing required key(s): "
            f"{', '.join(sorted(missing))}"
        )
    if extra:
        raise MatrixError(
            f"Row {index} in matrix '{path}' has unrecognized key(s): "
            f"{', '.join(sorted(extra))} — every row must declare exactly "
            f"{', '.join(sorted(_REQUIRED_ROW_KEYS))}"
        )
    for key in _BOOL_ROW_KEYS:
        if not isinstance(raw[key], bool):
            raise MatrixError(
                f"Row {index} in matrix '{path}': field '{key}' must be a boolean, "
                f"got {type(raw[key]).__name__} ({raw[key]!r})"
            )
    trigger_class = raw["trigger_class"]
    if not isinstance(trigger_class, str) or not trigger_class.strip():
        raise MatrixError(
            f"Row {index} in matrix '{path}': field 'trigger_class' must be a "
            f"non-empty string, got {trigger_class!r}"
        )
    resolution = raw["resolution"]
    if not isinstance(resolution, str) or not resolution.strip():
        raise MatrixError(
            f"Row {index} in matrix '{path}': field 'resolution' must be a "
            f"non-empty string, got {resolution!r}"
        )
    if resolution.strip() == HALT_HARD:
        raise MatrixError(
            f"Row {index} in matrix '{path}': 'resolution' must not be the "
            f"literal '{HALT_HARD}' — that value is the matcher's reserved "
            f"no-match sentinel, never a prescribed action."
        )
    return MatrixRow(
        trigger_class=trigger_class.strip(),
        findings_all_filed=raw["findings_all_filed"],
        shipped_or_tagged=raw["shipped_or_tagged"],
        approval_gate_present=raw["approval_gate_present"],
        operator_commits_in_lineage=raw["operator_commits_in_lineage"],
        attempts_below_ceiling=raw["attempts_below_ceiling"],
        resolution=resolution.strip(),
    )


def load_matrix(
    matrix_path: Optional[str] = None,
    kanban_root: Optional[str] = None,
) -> list:
    """Load and validate the resolution matrix.

    See _resolve_matrix_path() for the search order. Raises MatrixError with
    a descriptive message on any structural problem — a missing file, a
    non-mapping top level, a missing 'rows' list, or any row-level
    validation failure (see _validate_row()). An empty 'rows' list is valid
    (every block then resolves to HALT_HARD, which is a legitimate — if
    unusual — fully-fail-closed configuration).

    Returns a list[MatrixRow] in file order (matching order, top to bottom).
    """
    path = _resolve_matrix_path(matrix_path, kanban_root)
    try:
        text = open(path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise MatrixError(f"Cannot read resolution matrix '{path}': {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MatrixError(f"YAML parse error in resolution matrix '{path}': {exc}") from exc
    if not isinstance(data, dict):
        raise MatrixError(f"Resolution matrix '{path}' must be a YAML mapping at top level")
    raw_rows = data.get("rows")
    if raw_rows is None:
        raise MatrixError(f"Resolution matrix '{path}' is missing the required 'rows' list")
    if not isinstance(raw_rows, list):
        raise MatrixError(
            f"Resolution matrix '{path}': 'rows' must be a list, got {type(raw_rows).__name__}"
        )
    return [_validate_row(raw, i, path) for i, raw in enumerate(raw_rows)]


# ---------------------------------------------------------------------------
# CM trigger rows: what the resolution stage does with a chain HALT that CM
# wrote for one numbered trigger (team/roles/CM.md, "The triggers", and
# the trigger-N error keys cm/release.sh emits)
# ---------------------------------------------------------------------------

#: The HALTed RC was cancelled after the HALT was written, Active RC reads
#: none, and none of its cargo is still marked running (it was rebundled).
CLEAR_RC_CANCELLED = "rc-cancelled"
#: The task the HALT is about was reset: it reads BACKLOG or WAITING again.
CLEAR_TASK_RESET = "task-reset"
#: Trigger 9 re-checked green: the RC's release notes now carry a Status
#: heading the release stamp can apply to.
CLEAR_NOTES_STATUS_GREEN = "notes-status-green"

#: The cause checks a trigger row's ``clears_on`` may name.
CLEAR_CHECKS = frozenset({CLEAR_RC_CANCELLED, CLEAR_TASK_RESET, CLEAR_NOTES_STATUS_GREEN})

#: While the cause stands, consult the fact matrix and the three lanes, as
#: for any ship-policy HALT.
WHILE_OPEN_MATRIX = "matrix"
#: While the cause stands, do nothing: the HALT waits for the operator.
WHILE_OPEN_WAIT = "wait"

WHILE_OPEN_VALUES = frozenset({WHILE_OPEN_MATRIX, WHILE_OPEN_WAIT})

_TRIGGER_ROW_KEYS = frozenset({"trigger", "name", "clears_on", "while_open"})
_TRIGGER_VALUE_RE = re.compile(r"^\d+[a-z]?$")


@dataclass(frozen=True)
class TriggerRow:
    """One CM trigger's row: when its chain HALT may be cleared, and what
    the stage does while it may not.

    Attributes:
        trigger:    The CM trigger number, lowercase letter suffix kept
                    (``"5b"``), as ``halt_provenance.trigger_number`` reads it.
        name:       A short label for narration (``"squash-conflict"``).
        clears_on:  Cause checks (CLEAR_CHECKS); the HALT is cleared when any
                    one of them passes. Empty: never cleared by the stage.
        while_open: WHILE_OPEN_MATRIX or WHILE_OPEN_WAIT.
    """

    trigger: str
    name: str
    clears_on: tuple
    while_open: str


def _refuse_trigger_row(path: str, index: int, problem: str) -> NoReturn:
    """Raise MatrixError carrying the dual-channel contract for a bad trigger row."""
    from pgai_agent_kanban.lib.error_contract import format_error_contract

    contract = format_error_contract(
        "resolution_matrix.bad_trigger_row",
        f"Trigger row {index} in resolution matrix '{path}' is not usable: {problem}. "
        f"Each row needs exactly {', '.join(sorted(_TRIGGER_ROW_KEYS))}; trigger is a CM trigger "
        f"number such as 5 or 5b, clears_on a list drawn from {', '.join(sorted(CLEAR_CHECKS))}, "
        f"while_open one of {', '.join(sorted(WHILE_OPEN_VALUES))}. No chain HALT is cleared "
        "until the table loads.",
        f"diff {_default_matrix_path()} {path}",
    )
    # CONVERT: a trigger table that does not load only means the resolution
    # stage clears no CM chain HALT on this call; it declines and narrates,
    # every HALT stays as it is, and nothing new stops.
    # CENSUS-ANCHOR: resolution-matrix-trigger-row-01
    raise MatrixError(contract)


def _validate_trigger_row(raw: object, index: int, path: str) -> TriggerRow:
    if not isinstance(raw, dict):
        _refuse_trigger_row(path, index, f"expected a mapping, got {type(raw).__name__}")
    keys = set(raw.keys())
    if keys != _TRIGGER_ROW_KEYS:
        missing = sorted(_TRIGGER_ROW_KEYS - keys)
        extra = sorted(str(k) for k in keys - _TRIGGER_ROW_KEYS)
        _refuse_trigger_row(path, index, f"missing key(s) {missing or 'none'}, unrecognized key(s) {extra or 'none'}")
    trigger = str(raw["trigger"]).strip().lower()
    if not _TRIGGER_VALUE_RE.match(trigger):
        _refuse_trigger_row(path, index, f"trigger {raw['trigger']!r} is not a CM trigger number")
    name = raw["name"]
    if not isinstance(name, str) or not name.strip():
        _refuse_trigger_row(path, index, f"name {name!r} is not a non-empty string")
    clears_on = raw["clears_on"]
    if clears_on is None:
        clears_on = []
    if not isinstance(clears_on, list) or any(c not in CLEAR_CHECKS for c in clears_on):
        _refuse_trigger_row(path, index, f"clears_on {raw['clears_on']!r} names a check outside the accepted set")
    while_open = raw["while_open"]
    if while_open not in WHILE_OPEN_VALUES:
        _refuse_trigger_row(path, index, f"while_open {while_open!r} is not an accepted value")
    return TriggerRow(trigger=trigger, name=name.strip(), clears_on=tuple(clears_on), while_open=while_open)


def load_trigger_rows(
    matrix_path: Optional[str] = None,
    kanban_root: Optional[str] = None,
) -> dict:
    """Load the matrix file's ``triggers`` list, keyed by trigger number.

    The file is found exactly as :func:`load_matrix` finds it. A file with
    no ``triggers`` key yields ``{}``: every CM trigger HALT then takes the
    fact matrix path it took before trigger rows existed, and none is
    cleared. A file that cannot be read or parsed raises MatrixError as
    load_matrix does.

    Raises:
        MatrixError: a row that is not a mapping, has a missing or extra
            key, an unknown check or while_open value, or repeats a trigger
            number. The message carries the dual-channel contract.
    """
    path = _resolve_matrix_path(matrix_path, kanban_root)
    try:
        text = open(path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise MatrixError(f"Cannot read resolution matrix '{path}': {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MatrixError(f"YAML parse error in resolution matrix '{path}': {exc}") from exc
    if not isinstance(data, dict):
        raise MatrixError(f"Resolution matrix '{path}' must be a YAML mapping at top level")
    raw_rows = data.get("triggers")
    if raw_rows is None:
        return {}
    if not isinstance(raw_rows, list):
        _refuse_trigger_row(path, 0, f"'triggers' must be a list, got {type(raw_rows).__name__}")
    rows: dict = {}
    for index, raw in enumerate(raw_rows):
        row = _validate_trigger_row(raw, index, path)
        if row.trigger in rows:
            _refuse_trigger_row(path, index, f"trigger {row.trigger} already has a row")
        rows[row.trigger] = row
    return rows


# ---------------------------------------------------------------------------
# Matching (the pure, side-effect-free core)
# ---------------------------------------------------------------------------


def match_resolution(facts: BlockFacts, matrix: list) -> str:
    """Return the resolution of the first matrix row whose six facts equal
    *facts*'s six facts, evaluated top to bottom (first match wins — same
    convention as CM's ship-policy decision matrix).

    Returns HALT_HARD when no row matches — including when *matrix* is
    empty, when *facts* differs from every row in even one field, or when
    *facts*.trigger_class is a value the matrix has no row for at all
    (an "unknown shape"). This is the fail-closed default: unmatched or
    unknown shapes resolve to HALT-hard, never to a default action.
    """
    target = facts.facts_key()
    for row in matrix:
        if row.facts_key() == target:
            return row.resolution
    return HALT_HARD


# ---------------------------------------------------------------------------
# TESTER report findings (findings_all_filed and Lane A evidence)
# ---------------------------------------------------------------------------


def matrix_findings(report: TesterReport) -> list:
    """Return *report*'s findings in the shape the matrix and the lane
    classifier read.

    This module never reads TESTER-report text itself. *report* is a
    pgai_agent_kanban.reports.tester_report.TesterReport (parse_report's
    result), whose module docstring states the whole reading contract: what
    opens a finding, where it ends, how a field line is recognised and how
    each value is normalised. The findings are
    pgai_agent_kanban.cm.ship_policy_report_inputs.all_findings -- each
    finding followed by its nested sub-findings, in report order -- which
    are the findings the release path's readers (the ship-policy matrix
    inputs, the Known Issues stop list, the Bugs Skipped table) read, so
    OVERWATCH and the release path see the same findings in every report.

    Returns a list of dicts, one per finding, in that order:
        {"title": str, "category": str | None, "filed_as": str | None,
         "regression": bool | None, "body": str}
    "title" is Finding.title: the heading text after its label ("stale
    assertion" for a "Finding 2: stale assertion" heading; "" when the
    heading carries nothing more). "category" is Finding.category: one of
    pass, pass-with-caveat, gap, bug, pre-existing-bug or stale-assertion,
    or None when the finding has no readable Category value (a nested
    sub-finding usually carries none) -- findings_all_filed() skips a pass
    or pass-with-caveat finding, which TESTER never files, and counts every
    other finding, None included. "filed_as" is Finding.filed_as: the
    canonical BUG-NNNN or PRIORITY-NNNN id the finding's own Filed As value
    names, or None when it names none (no Filed As line, or a value such as
    "none", "N/A" or "not filed") -- findings_all_filed() counts None as
    unfiled. "regression" is True for a Regression of "yes", False for
    "no", and None when the finding carries no readable Regression value
    (most findings: the field is only present on Systemic-Risk-high
    findings), distinct from an explicit "no". "body" is Finding.body, the
    finding's text below its heading, a nested sub-finding's lines
    included; classify_lane()'s Lane A match scans it for the BUG-NNNN ids
    the finding names as the regression's origin. Returns [] when the
    report has no findings (e.g. a clean PASS report).
    """
    return [
        {
            "title": finding.title,
            "category": finding.category,
            "filed_as": finding.filed_as,
            "regression": _REGRESSION_FLAGS.get(finding.regression),
            "body": finding.body,
        }
        for finding in all_findings(report)
    ]


# ---------------------------------------------------------------------------
# Three-lane block classification (pure, side-effect-free — see this
# module's docstring's Layer 1). resolution_stage.py's run_resolution_stage
# calls classify_lane() only when a block has already fallen through to
# HALT_HARD against the pre-existing CANCEL-AND-REBUNDLE matrix AND the
# caller's overwatch_resolve flag is true; see that module's "three-lane
# classification" section for the full wiring and for what Lane A/B/C
# execution does (this module only classifies — see LANE_A/LANE_B's
# module-level comment above).
# ---------------------------------------------------------------------------


def extract_bug_ids(text: Optional[str]) -> list:
    """Order-preserving, de-duplicated list of BUG-NNNN ids named in *text*
    (the short id only — any trailing slug is not part of the match).
    Returns [] for empty or None text.
    """
    if not text:
        return []
    seen: list = []
    for match in _BUG_ID_ONLY_RE.finditer(text):
        token = match.group(0)
        if token not in seen:
            seen.append(token)
    return seen


def cargo_bug_ids(cargo: list) -> set:
    """Normalize a bundle family's cargo basenames (e.g. "BUG-0042-foo.md",
    the shape pgai_agent_kanban.cm.bundle_family.compute_bundle_family's
    "cargo" list carries) to the short BUG-NNNN id set Lane A's
    regression-tracing match compares against. Non-bug cargo entries
    (PRIORITY-* files, anything not BUG-NNNN-prefixed) are silently
    excluded — this RC's bundle can only have "fixed" a bug it bundled.
    """
    result = set()
    for basename in cargo:
        match = _BUG_ID_ONLY_RE.match(basename)
        if match:
            result.add(match.group(0))
    return result


def find_regression_source_finding(findings: list, cargo_ids) -> Optional[tuple]:
    """Return (finding, bug_id) for the first finding (document order) that
    is Lane A's bad-fix-regression shape: carries Regression=yes AND its
    body text names a BUG-NNNN id that is a member of *cargo_ids* — i.e.
    the finding traces to a bug this RC's own bundle fixed. Returns None
    when no finding qualifies (including when *findings* or *cargo_ids* is
    empty) — the fail-closed default that sends the block on to the Lane B
    check rather than Lane A.
    """
    for finding in findings:
        if finding.get("regression") is not True:
            continue
        for bug_id in extract_bug_ids(finding.get("body")):
            if bug_id in cargo_ids:
                return finding, bug_id
    return None


# Identifier vocabulary Lane B's full-explanation match test is built on:
# BUG ids, vX.Y.Z version strings, slash-qualified refs (branch/path
# names such as "ai_rc/v2.4.0"), and underscore-compound tokens
# (config-style names such as "last_run_ai_main"). Order matters only
# for the BUG-id alternative, which must be tried before the bare
# _BUG_ID_ONLY_RE so a trailing slug is captured as part of one token.
_IDENTIFIER_RE = re.compile(
    r"BUG-\d{4}(?:-[A-Za-z0-9._-]+)?"
    r"|v\d+\.\d+\.\d+"
    r"|[A-Za-z][A-Za-z0-9_.-]*/[A-Za-z0-9_./-]+"
    r"|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"
)
_BUG_ID_ONLY_RE = re.compile(r"BUG-\d{4}")


def extract_identifiers(*texts: Optional[str]) -> list:
    """Deterministic, order-preserving, de-duplicated list of "identifier"
    tokens found across *texts* — see _IDENTIFIER_RE above for the exact
    vocabulary.

    This is the vocabulary intent_entry_fully_explains() is built on: an
    INTENT.md entry only counts as explaining a block when every one of
    these exact tokens is reproduced verbatim in its declaration text —
    never a generic keyword or thematic match (Lane B's match test: the
    finding's evidence is fully explained by the declaration — a partial
    or thematic resemblance is not a match). Requiring exact reproduction of specific,
    structured identifiers is what makes "fully explained" a checkable
    fact rather than a vibe: a declaration using similar-sounding words
    without naming the specific version/bug/branch the block is actually
    about does not match.
    """
    seen: list = []
    for text in texts:
        if not text:
            continue
        for match in _IDENTIFIER_RE.finditer(text):
            token = match.group(0)
            if token not in seen:
                seen.append(token)
    return seen


def intent_entry_fully_explains(declaration: str, identifiers: list) -> bool:
    """True when *declaration* verbatim-reproduces every token in
    *identifiers* — the Lane B match test.

    False when *identifiers* is empty: an entry cannot "fully explain" a
    block with no extractable identifying evidence, so an empty identifier
    list always fails closed to no-match rather than vacuously matching
    every declaration in the ledger.
    """
    if not identifiers:
        return False
    return all(token in declaration for token in identifiers)


def find_lane_b_declaration(identifiers: list, declarations: list) -> Optional[int]:
    """Return the index of the first declaration (file/ledger order) that
    fully explains *identifiers*, or None when no declaration does —
    including when *declarations* is empty (an emptied or never-populated
    INTENT.md ledger, which is the shipped zero-entry default).
    """
    for index, declaration in enumerate(declarations):
        if intent_entry_fully_explains(declaration, identifiers):
            return index
    return None


@dataclass(frozen=True)
class LaneClassification:
    """The outcome of classify_lane(): which of the three lanes a block
    falls into, plus the specific evidence that drove the decision — for
    narration and for the OVERWATCH Decision event resolution_stage.py's
    write_decision_event() records.
    """

    lane: str  # LANE_A | LANE_B | LANE_C
    reason: str
    regression_finding: Optional[dict] = None
    regression_bug_id: Optional[str] = None
    intent_declaration: Optional[str] = None
    identifiers: tuple = ()

    def to_dict(self) -> dict:
        """Return the classification as a JSON-ready dict.

        Keys: ``lane``, ``reason``, ``regression_finding``, ``regression_bug_id``,
        ``intent_declaration`` and ``identifiers`` (a list).
        """
        return {
            "lane": self.lane,
            "reason": self.reason,
            "regression_finding": self.regression_finding,
            "regression_bug_id": self.regression_bug_id,
            "intent_declaration": self.intent_declaration,
            "identifiers": list(self.identifiers),
        }


def classify_lane(
    findings: list,
    cargo_ids,
    intent_declarations: list,
    evidence_texts: list,
) -> LaneClassification:
    """Classify a block into exactly one of Lane A, Lane B, or Lane C.

    Fail-closed by construction: Lane A requires a positive, traceable
    regression match (find_regression_source_finding()); failing that,
    Lane B requires a positive, fully-explaining INTENT.md declaration
    (find_lane_b_declaration()); failing both, the block is Lane C — the
    default, never a fallback that had to be specially reached for (every
    return path other than the two positive matches above lands here by
    simply falling through).

    Args:
        findings: The block's TESTER report findings (matrix_findings()'s
            return shape).
        cargo_ids: The set of BUG-NNNN ids this RC's own bundle fixed
            (cargo_bug_ids() of the bundle family's cargo list).
        intent_declarations: This project's INTENT.md declaration strings,
            in file order (e.g. [e.declaration for e in
            pgai_agent_kanban.overwatch.intent.read_project_intent(...)]).
            An empty list — including a shipped zero-entry ledger — can
            never produce a Lane B match, per find_lane_b_declaration().
        evidence_texts: Free text to extract Lane B's identifier
            vocabulary from — typically every finding's body plus the
            block's own target_version/rc_branch/base_branch strings.
    """
    match = find_regression_source_finding(findings, cargo_ids)
    if match is not None:
        finding, bug_id = match
        return LaneClassification(
            lane=LANE_A,
            reason=(
                f"TESTER finding {finding.get('title') or '(untitled)'!r} carries "
                f"Regression=yes and traces to {bug_id}, which this RC's own bundle "
                f"fixed — bad-fix regression."
            ),
            regression_finding=finding,
            regression_bug_id=bug_id,
        )

    identifiers = extract_identifiers(*evidence_texts)
    if intent_declarations:
        idx = find_lane_b_declaration(identifiers, intent_declarations)
        if idx is not None:
            declaration = intent_declarations[idx]
            return LaneClassification(
                lane=LANE_B,
                reason=(
                    f"the block's evidence ({', '.join(identifiers)}) is fully "
                    f"explained by a declared INTENT.md entry: {declaration!r}."
                ),
                intent_declaration=declaration,
                identifiers=tuple(identifiers),
            )

    return LaneClassification(
        lane=LANE_C,
        reason=(
            "no TESTER finding traces a bad-fix regression to a bug this RC's own "
            "bundle fixed, and no INTENT.md entry fully explains the block's "
            "evidence — Lane C, the default."
        ),
        identifiers=tuple(identifiers),
    )


def findings_all_filed(findings: list) -> bool:
    """True when every finding in *findings* that must be filed carries a
    non-empty, non-sentinel "filed_as" value.

    A finding whose "category" is pass or pass-with-caveat records a passing
    check, which TESTER never files, so it is skipped whatever its
    "filed_as". Every other finding must be filed: gap, bug,
    pre-existing-bug and stale-assertion, and -- failing closed -- a finding
    with no "category" key, a None category, or a value outside
    matrix_findings()'s vocabulary. Vacuously true for an empty list or one
    holding only pass and pass-with-caveat findings: nothing that needed
    filing was left unfiled.
    """
    if not findings:
        return True
    for finding in findings:
        category = finding.get("category")
        if isinstance(category, str) and category in _NO_FILING_CATEGORIES:
            continue
        value = (finding.get("filed_as") or "").strip().lower()
        if value in _NOT_FILED_VALUES:
            return False
    return True


# ---------------------------------------------------------------------------
# Tier walk: project.cfg > kanban.cfg through the shared resolver
# ---------------------------------------------------------------------------


def _cfg_key_chain(
    kanban_root: str,
    project_root: Optional[str],
    section: str,
    key: str,
    *,
    legacy: bool = True,
) -> tuple:
    """Return ``(raw, source)`` for ``[section] key``, the project tier first.

    The walk is the shared resolver,
    :func:`pgai_agent_kanban.lib.project_cfg.resolve_tiered_cfg_value` (bash
    twin ``pp_resolve_tiered_cfg_value``): the project's own config under
    *project_root* (``project.cfg``, or a legacy ``PROJECT.cfg`` unless
    *legacy* is False), then ``<kanban_root>/kanban.cfg``, the first
    non-blank value winning, both read by the bash ``read_ini`` rules.
    *project_root* ``None`` skips the project tier.

    ``source`` is the winning file's path built with :func:`os.path.join`
    from the roots as given -- the form every message in this module
    names. ``("", "")`` when neither file carries a non-blank value. No
    validation here: each resolver validates the winning value itself.
    """
    found = project_cfg.resolve_tiered_cfg_value(
        kanban_root, project_root, section, key, legacy=legacy
    )
    if found.tier == project_cfg.TIER_PROJECT_CFG:
        return found.value, os.path.join(project_root, os.path.basename(found.source))
    if found.tier == project_cfg.TIER_KANBAN_CFG:
        return found.value, os.path.join(kanban_root, "kanban.cfg")
    return "", ""


def _ini_parse_error(cfg_path: str) -> Optional[Exception]:
    """Return the :func:`ini.load` error for *cfg_path*, or None when it parses.

    None as well when the file is absent. The tier walk reads line by line
    and answers even from a file ``configparser`` refuses (a duplicate key,
    text before the first section); this check lets each resolver below keep
    its own verdict on such a file -- ``resolve_max_rc_retries`` refuses it,
    the two switches read it as absent.
    """
    if not cfg_path or not os.path.isfile(cfg_path):
        return None
    try:
        ini.load(cfg_path)
    except ini.IniError as exc:
        return exc
    return None


def _switch_key_chain(project_name: str, kanban_root: str, section: str, key: str) -> tuple:
    """Return ``(raw, source)`` for an autonomy switch's ``[section] key``.

    The tier walk over ``<kanban_root>/projects/<project_name>/project.cfg``
    (never a legacy ``PROJECT.cfg``) and kanban.cfg, where a file that
    cannot be parsed reads as absent: a switch falls back to its own
    documented default rather than erroring on an unreadable file.
    ``("", "")`` when no readable file carries a non-blank value.
    """
    project_root: Optional[str] = os.path.join(kanban_root, "projects", project_name)
    kanban_cfg = os.path.join(kanban_root, "kanban.cfg")
    if _ini_parse_error(os.path.join(project_root, "project.cfg")) is not None:
        project_root = None
    raw, source = _cfg_key_chain(kanban_root, project_root, section, key, legacy=False)
    if source == kanban_cfg and _ini_parse_error(kanban_cfg) is not None:
        return "", ""
    return raw, source


# ---------------------------------------------------------------------------
# Ceiling resolution (Python counterpart of pp_max_rc_retries)
# ---------------------------------------------------------------------------


def resolve_max_rc_retries(project_name: str, kanban_root: str) -> int:
    """Python counterpart of pp_max_rc_retries() (team/scripts/lib/project_paths.sh).

    Resolution order (first non-empty source wins), walked by the shared
    resolver (``_cfg_key_chain``):
      1. <kanban_root>/projects/<project_name>/project.cfg [release] max_rc_retries
      2. <kanban_root>/kanban.cfg [release] max_rc_retries (shipped default: 3)

    Raises CeilingConfigError, naming both file paths checked, when the key
    is absent from BOTH sources or when either source's value is not a
    valid non-negative integer, and naming the file when a source the walk
    consulted cannot be parsed. Never silently substitutes a hardcoded
    default — see this module's docstring above.
    """
    project_root = os.path.join(kanban_root, "projects", project_name)
    project_cfg_path = os.path.join(project_root, "project.cfg")
    kanban_cfg = os.path.join(kanban_root, "kanban.cfg")

    raw, source = _cfg_key_chain(
        kanban_root, project_root, "release", "max_rc_retries", legacy=False
    )
    consulted = (project_cfg_path,) if source == project_cfg_path else (project_cfg_path, kanban_cfg)
    for cfg_path in consulted:
        exc = _ini_parse_error(cfg_path)
        if exc is not None:
            raise CeilingConfigError(f"Cannot parse config file '{cfg_path}': {exc}") from exc

    if raw:
        if not raw.isdigit():
            raise CeilingConfigError(
                f"resolution_matrix: invalid value '{raw}' in {source} "
                f"[release] max_rc_retries (expected non-negative integer)"
            )
        return int(raw)

    raise CeilingConfigError(
        f"resolution_matrix: max_rc_retries is required but absent for project "
        f"'{project_name}'. Checked {project_cfg_path} [release] max_rc_retries and "
        f"{kanban_cfg} [release] max_rc_retries — set one of them."
    )


# ---------------------------------------------------------------------------
# Autonomy-gate resolution (Python counterpart of pp_overwatch_resolve)
# ---------------------------------------------------------------------------


def resolve_overwatch_resolve(project_name: str, kanban_root: str) -> bool:
    """Python counterpart of pp_overwatch_resolve() (team/scripts/lib/project_paths.sh).

    Resolution order (first non-blank source wins), the same project.cfg-
    overrides-kanban.cfg precedence resolve_max_rc_retries() uses:
      1. <kanban_root>/projects/<project_name>/project.cfg [release] overwatch_resolve
      2. <kanban_root>/kanban.cfg [release] overwatch_resolve

    Absent from both sources, or a config file that cannot be read or
    parsed, resolves to False — the opposite fail-safe direction from
    resolve_max_rc_retries()/CeilingConfigError: this key gates OVERWATCH's
    autonomous-resolution authority, so silence must mean "off", never an
    error and never "on".

    Raises InvalidOverwatchResolveValue, naming the offending value, the
    project, and the file it came from, when a present value is neither
    'true' nor 'false'. Never silently guessed into either boolean.
    """
    raw, source = _switch_key_chain(project_name, kanban_root, "release", "overwatch_resolve")

    if not raw:
        return False
    if raw == "true":
        return True
    if raw == "false":
        return False

    raise InvalidOverwatchResolveValue(
        f"resolution_matrix: unrecognized value '{raw}' for project "
        f"'{project_name}' in {source} [release] overwatch_resolve "
        f"(expected 'true' or 'false')."
    )


# ---------------------------------------------------------------------------
# Block-triage switch (Python counterpart of pp_block_triage)
# ---------------------------------------------------------------------------


def resolve_block_triage(project_name: str, kanban_root: str) -> bool:
    """Python counterpart of pp_block_triage() (team/scripts/lib/project_paths.sh).

    Whether OVERWATCH's block-triage sweep may act on a blocked task's
    Block Record (requeue, promote, escalate) for this project, or must
    only write the record and its diagnosis.

    Resolution order (first non-blank source wins), the same project.cfg-
    overrides-kanban.cfg precedence resolve_overwatch_resolve() uses:
      1. <kanban_root>/projects/<project_name>/project.cfg [overwatch] block_triage
      2. <kanban_root>/kanban.cfg [overwatch] block_triage

    Absent from both sources, or a config file that cannot be read or
    parsed, resolves to True — the shipped kanban.cfg_example default, so
    an upgraded install whose kanban.cfg predates the key behaves the same
    as a fresh one.

    Raises InvalidBlockTriageValue, naming the offending value, the
    project, and the file it came from, when a present value is neither
    'true' nor 'false'. Never silently guessed into either boolean.
    """
    raw, source = _switch_key_chain(project_name, kanban_root, "overwatch", "block_triage")

    if not raw or raw == "true":
        return True
    if raw == "false":
        return False

    raise InvalidBlockTriageValue(
        f"resolution_matrix: unrecognized value '{raw}' for project "
        f"'{project_name}' in {source} [overwatch] block_triage "
        f"(accepted: true|false)."
    )


# ---------------------------------------------------------------------------
# Ship posture (Python counterpart of pp_ship_posture)
# ---------------------------------------------------------------------------


def _project_pushes_to_remote(project_cfg_path: Optional[str]) -> bool:
    """Whether the project is configured to publish to origin (the posture guard's rule).

    This is the configured intent bash ``pp_ship_posture`` reads through
    ``_pp_push_to_remote_configured``, not the push decision itself (that is
    :mod:`pgai_agent_kanban.lib.push_decision`): the remote-lane guard fails
    safe, so an unresolved project still counts as a publishing lane here.

    Only the exact value ``false`` in ``[project] push_to_remote`` (one
    surrounding double quote stripped from each side) is False; an absent
    config file, an absent or blank key, and any other value are True.
    """
    if not project_cfg_path:
        return True
    raw = project_cfg.read_cfg_key(pathlib.Path(project_cfg_path), "project", "push_to_remote")
    return project_cfg.strip_one_quote_each_side(raw, '"') != "false"


def resolve_ship_posture(project_name: str, kanban_root: str, *, stream=None) -> str:
    """Python counterpart of pp_ship_posture() (team/scripts/lib/project_paths.sh).

    Returns the project's ship posture, exactly ``"matrix"`` or
    ``"always"``: how CM answers a ship-policy trigger that would otherwise
    HALT. ``"matrix"`` applies the ship-policy matrix as written;
    ``"always"`` ships through the triggers the posture covers and records
    what they would have stopped on.

    Resolution order (first non-blank source wins), the same project.cfg-
    overrides-kanban.cfg precedence resolve_max_rc_retries() uses:
      1. <kanban_root>/projects/<project_name>/project.cfg [release] ship_posture
         (or a legacy PROJECT.cfg, as the bash resolver reads)
      2. <kanban_root>/kanban.cfg [release] ship_posture
      3. ``"always"``, the built-in default, when both are absent or blank

    Any value other than ``matrix`` or ``always`` resolves to ``"matrix"``
    and writes ONE warning line to *stream* (default ``sys.stderr``) naming
    the file and the raw value.

    Remote-lane guard: when the project's ``[project] push_to_remote`` is
    not ``false``, ``"always"`` takes effect only if ``[release]
    ship_posture_confirm_remote`` is exactly ``true`` through the same
    chain, the built-in default included. Anything short of that resolves
    to ``"matrix"``: with ONE warning line when a cfg file set ``always``,
    silently when ``always`` is only the built-in default (nobody chose a
    posture for the warning to correct). On a ``push_to_remote = false``
    lane the confirm key is not consulted.

    Neither the fallback nor the guard raises: the caller always gets a
    posture back and carries on. The warning text after its
    ``resolution_matrix: resolve_ship_posture: `` prefix is the same text
    the bash resolver prints after its own prefix.
    """
    out = stream if stream is not None else sys.stderr
    kanban_cfg = os.path.join(kanban_root, "kanban.cfg")
    project_root = os.path.join(kanban_root, "projects", project_name)
    found = project_cfg.project_cfg_file(pathlib.Path(project_root))
    project_cfg_path = os.path.join(project_root, found.name) if found is not None else None
    prefix = "resolution_matrix: resolve_ship_posture: "

    raw, source = _cfg_key_chain(kanban_root, project_root, "release", "ship_posture")
    if raw == SHIP_POSTURE_MATRIX:
        return SHIP_POSTURE_MATRIX
    if raw not in ("", SHIP_POSTURE_ALWAYS):
        print(
            f"{prefix}WARNING: unrecognized value '{raw}' for project "
            f"'{project_name}' in {source} [release] ship_posture "
            f"(accepted: matrix|always); resolving to 'matrix'.",
            file=out,
        )
        return SHIP_POSTURE_MATRIX

    if not _project_pushes_to_remote(project_cfg_path):
        return SHIP_POSTURE_ALWAYS

    confirm_raw, confirm_source = _cfg_key_chain(
        kanban_root, project_root, "release", "ship_posture_confirm_remote"
    )
    if confirm_raw == "true":
        return SHIP_POSTURE_ALWAYS

    # "always" came from the built-in default, not a cfg file: the guard
    # still keeps the remote lane on matrix, with nothing to warn about.
    if not source:
        return SHIP_POSTURE_MATRIX

    # The remedy names the file whose confirm value wins the chain: the one
    # already carrying a non-true value, else the one that set "always".
    if confirm_raw:
        confirm_desc = f"'{confirm_raw}' in {confirm_source}"
        confirm_target = confirm_source
    else:
        shown_project_cfg = project_cfg_path or os.path.join(project_root, "project.cfg")
        confirm_desc = f"absent from {shown_project_cfg} and {kanban_cfg}"
        confirm_target = source
    print(
        f"{prefix}WARNING: ship_posture 'always' for project '{project_name}' "
        f"in {source} is not confirmed for a push_to_remote = true lane "
        f"([release] ship_posture_confirm_remote is {confirm_desc}); "
        f"resolving to 'matrix'. Set ship_posture_confirm_remote = true under "
        f"[release] in {confirm_target} to confirm.",
        file=out,
    )
    return SHIP_POSTURE_MATRIX


# ---------------------------------------------------------------------------
# Git-backed evidence helpers
# ---------------------------------------------------------------------------


def _run_git(dev_tree_path: str, args: list) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", dev_tree_path, *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        raise EvidenceError(
            f"git {' '.join(args)} failed in '{dev_tree_path}': {exc} {stderr}".strip()
        ) from exc
    return result.stdout


def git_tag_exists(dev_tree_path: str, tag: str) -> bool:
    """True when the tag named *tag* exists in *dev_tree_path*.

    *tag* is the full tag name: a release tag is named through
    release_tag_name(version, branch_prefix) (e.g. "ai_v2.0.1"), never the
    bare version on a prefixed project.

    Raises EvidenceError if the git command itself fails (bad path, not a
    repository) — a plumbing failure, distinct from "tag legitimately
    absent" (which returns False cleanly).
    """
    stdout = _run_git(dev_tree_path, ["tag", "-l", tag])
    return tag in stdout.split()


def operator_commit_in_rc_lineage(dev_tree_path: str, rc_branch: str, base_branch: str) -> bool:
    """True when the RC branch's mainline carries a non-merge commit.

    Walks --first-parent of *rc_branch* since it diverged from *base_branch*
    (git log --first-parent --pretty=%P <rc_branch> --not <base_branch>).
    Every legitimate contribution to an RC branch under this kanban's
    workflow (roles/CM.md "The release lifecycle" / roles/CODER.md "Git
    Operates Locally for CODER") lands as a --no-ff merge commit of a feature/<TASK-ID>
    branch — a commit with exactly two parents. A commit with any other
    parent count (one, for a direct commit; more than two only in
    pathological octopus-merge cases this kanban never produces) sitting
    directly on the RC branch's mainline is the deterministic signal that
    something bypassed the feature-branch-and-merge convention — i.e. an
    operator commit.

    Raises EvidenceError if the git command fails (bad path, unknown ref).
    """
    stdout = _run_git(
        dev_tree_path,
        ["log", "--first-parent", "--pretty=%P", rc_branch, "--not", base_branch],
    )
    for line in stdout.splitlines():
        parent_count = len(line.split())
        if parent_count != 2:
            return True
    return False


# ---------------------------------------------------------------------------
# Fact extraction (one private helper per fact — each documents its
# fail-closed default when evidence is insufficient to prove the fact)
# ---------------------------------------------------------------------------


def _fact_trigger_class(evidence: BlockEvidence) -> str:
    """Fail-closed default: "unknown" (matches no matrix row's trigger_class,
    since the shipped default only ever declares "ship-policy-halt").
    """
    if evidence.trigger_class is not None:
        return evidence.trigger_class
    if evidence.halt_file_path and os.path.isfile(evidence.halt_file_path):
        try:
            content = open(evidence.halt_file_path, "r", encoding="utf-8").read()
        except OSError:
            return "unknown"
        if _CM_HALT_HEADER_RE.search(content):
            return TRIGGER_CLASS_SHIP_POLICY_HALT
        if content.strip():
            return "operator-halt"
    return "unknown"


def _resolve_findings(evidence: BlockEvidence) -> Optional[list]:
    if evidence.tester_findings is not None:
        return evidence.tester_findings
    if evidence.tester_report_path and os.path.isfile(evidence.tester_report_path):
        try:
            report = parse_report(pathlib.Path(evidence.tester_report_path))
        except OSError:
            return None
        return matrix_findings(report)
    return None


def _fact_findings_all_filed(evidence: BlockEvidence) -> bool:
    """Fail-closed default: False (no evidence of filings proves nothing —
    the matrix row requires True).
    """
    findings = _resolve_findings(evidence)
    if findings is None:
        return False
    return findings_all_filed(findings)


def _read_release_state(path: Optional[str]) -> dict:
    if not path or not os.path.isfile(path):
        return {}
    try:
        return json.loads(open(path, "r", encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        return {}


def _fact_shipped_or_tagged(evidence: BlockEvidence) -> bool:
    """Fail-closed default: True (insufficient evidence to prove nothing
    shipped — the matrix row requires False, so "unknown" must not look
    like "nothing shipped").
    """
    have_state_evidence = bool(evidence.release_state_path)
    have_tag_evidence = bool(evidence.dev_tree_path and evidence.target_version)
    if not have_state_evidence and not have_tag_evidence:
        return True

    if have_state_evidence:
        state = _read_release_state(evidence.release_state_path)
        if state.get("outcome") == "shipped":
            return True

    if have_tag_evidence:
        # The tag the release cuts: <branch_prefix><version>, so a prefixed
        # project's shipped tag counts and another lane's bare tag does not.
        release_tag = release_tag_name(evidence.target_version, evidence.branch_prefix or "")
        if git_tag_exists(evidence.dev_tree_path, release_tag):
            return True

    return False


def _fact_approval_gate_present(evidence: BlockEvidence) -> bool:
    """Fail-closed default: True (insufficient evidence to prove no gate
    exists — the matrix row requires False).
    """
    if not evidence.project_tasks_dir or not evidence.target_version:
        return True
    pattern = os.path.join(
        evidence.project_tasks_dir, f"HUMAN-APPROVE-{evidence.target_version}-*"
    )
    return len(glob.glob(pattern)) > 0


def _fact_operator_commits_in_lineage(evidence: BlockEvidence) -> bool:
    """Fail-closed default: True (insufficient evidence to prove clean
    lineage — the matrix row requires False).
    """
    if not (evidence.dev_tree_path and evidence.rc_branch and evidence.base_branch):
        return True
    return operator_commit_in_rc_lineage(
        evidence.dev_tree_path, evidence.rc_branch, evidence.base_branch
    )


def _resolve_attempt(evidence: BlockEvidence) -> Optional[int]:
    if evidence.attempt is not None:
        return evidence.attempt
    state = _read_release_state(evidence.release_state_path)
    value = state.get("attempt")
    if isinstance(value, int):
        return value
    return None


def _resolve_ceiling(evidence: BlockEvidence) -> int:
    """Resolve the retry ceiling. Never defaults silently — raises
    CeilingConfigError when it cannot be determined at all (neither an
    explicit override nor enough context to look it up), matching
    resolve_max_rc_retries()'s own fail-loud contract.
    """
    if evidence.max_rc_retries is not None:
        return evidence.max_rc_retries
    if evidence.project_name and evidence.kanban_root:
        return resolve_max_rc_retries(evidence.project_name, evidence.kanban_root)
    raise CeilingConfigError(
        "resolution_matrix: cannot resolve max_rc_retries — BlockEvidence supplied "
        "neither an explicit max_rc_retries value nor both project_name and "
        "kanban_root to look one up. Set [release] max_rc_retries in the "
        "project's project.cfg or in kanban.cfg; it is never silently defaulted."
    )


def _fact_attempts_below_ceiling(evidence: BlockEvidence) -> tuple:
    """Returns (attempts_below_ceiling, attempt, ceiling).

    Fail-closed default when the attempt number cannot be determined:
    attempts_below_ceiling=False regardless of the ceiling value (the
    matrix row requires True, and an unproven attempt count must not look
    like "safely under the ceiling"). The ceiling itself is always resolved
    (or raises — see _resolve_ceiling()) since it must fail loud rather
    than be treated as merely "unknown evidence".
    """
    ceiling = _resolve_ceiling(evidence)
    attempt = _resolve_attempt(evidence)
    if attempt is None:
        return False, None, ceiling
    return attempt < ceiling, attempt, ceiling


# ---------------------------------------------------------------------------
# Public extraction / resolution API
# ---------------------------------------------------------------------------


def extract_facts(evidence: BlockEvidence) -> BlockFacts:
    """Extract the six deterministic facts from *evidence*.

    Total over any BlockEvidence (never returns partial results) except for
    two failure modes that propagate as exceptions rather than being
    absorbed into a fact value:

      - CeilingConfigError: the retry ceiling could not be resolved at all.
      - EvidenceError: a git command needed for shipped_or_tagged or
        operator_commits_in_lineage failed unexpectedly (bad path, unknown
        ref) rather than cleanly proving/disproving the fact.

    Every other kind of "insufficient evidence" resolves to a fail-closed
    fact value per each _fact_* helper's docstring — the block simply won't
    match the shipped CANCEL-AND-REBUNDLE row, which is the intended
    "decline by default" behavior.
    """
    trigger_class = _fact_trigger_class(evidence)
    findings_all_filed_fact = _fact_findings_all_filed(evidence)
    shipped_or_tagged_fact = _fact_shipped_or_tagged(evidence)
    approval_gate_present_fact = _fact_approval_gate_present(evidence)
    operator_commits_fact = _fact_operator_commits_in_lineage(evidence)
    attempts_below_ceiling_fact, attempt, ceiling = _fact_attempts_below_ceiling(evidence)

    return BlockFacts(
        trigger_class=trigger_class,
        findings_all_filed=findings_all_filed_fact,
        shipped_or_tagged=shipped_or_tagged_fact,
        approval_gate_present=approval_gate_present_fact,
        operator_commits_in_lineage=operator_commits_fact,
        attempts_below_ceiling=attempts_below_ceiling_fact,
        attempt=attempt,
        ceiling=ceiling,
    )


def resolve_block(
    evidence: BlockEvidence,
    matrix_path: Optional[str] = None,
    kanban_root: Optional[str] = None,
) -> ResolutionResult:
    """Load the matrix, extract facts from *evidence*, and match — the one
    call OVERWATCH's resolution stage needs.

    *kanban_root* is used both for the matrix search (an operator override
    at <kanban_root>/resolution-matrix.yaml) and, when *evidence* does not
    carry its own kanban_root, is NOT implicitly copied onto evidence —
    callers must set evidence.kanban_root explicitly if they want ceiling
    resolution to use it. This keeps the matrix-location kanban_root and
    the ceiling-lookup kanban_root independently overridable, even though
    they are typically the same path in production.
    """
    matrix = load_matrix(matrix_path=matrix_path, kanban_root=kanban_root)
    facts = extract_facts(evidence)
    resolution = match_resolution(facts, matrix)
    matched_row = None
    if resolution != HALT_HARD:
        for row in matrix:
            if row.resolution == resolution and row.facts_key() == facts.facts_key():
                matched_row = row
                break
    return ResolutionResult(resolution=resolution, facts=facts, matched_row=matched_row)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _facts_from_dict(data: dict) -> BlockFacts:
    known = {f.name for f in fields(BlockFacts)}
    return BlockFacts(**{k: v for k, v in data.items() if k in known})


def main() -> None:
    """CLI entry point: 'resolve' (evidence -> resolution) or 'match'
    (pre-extracted facts -> resolution). Both print a JSON result to stdout.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Resolve a block against the config-declared resolution matrix. "
            "'resolve' takes a BlockEvidence JSON file and extracts facts "
            "itself; 'match' takes a pre-extracted BlockFacts JSON file."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_resolve = subparsers.add_parser("resolve", help="Resolve a block from evidence JSON.")
    p_resolve.add_argument("evidence_json", help="Path to a BlockEvidence JSON file.")
    p_resolve.add_argument("--matrix", default=None, help="Explicit matrix YAML path.")
    p_resolve.add_argument("--kanban-root", default=None, help="Kanban root path.")

    p_match = subparsers.add_parser("match", help="Match pre-extracted facts JSON.")
    p_match.add_argument("facts_json", help="Path to a BlockFacts JSON file.")
    p_match.add_argument("--matrix", default=None, help="Explicit matrix YAML path.")
    p_match.add_argument("--kanban-root", default=None, help="Kanban root path.")

    args = parser.parse_args()

    if args.command == "resolve":
        data = json.loads(open(args.evidence_json, "r", encoding="utf-8").read())
        evidence = BlockEvidence.from_dict(data)
        result = resolve_block(evidence, matrix_path=args.matrix, kanban_root=args.kanban_root)
        sys.stdout.write(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    elif args.command == "match":
        data = json.loads(open(args.facts_json, "r", encoding="utf-8").read())
        facts = _facts_from_dict(data)
        matrix = load_matrix(matrix_path=args.matrix, kanban_root=args.kanban_root)
        resolution = match_resolution(facts, matrix)
        sys.stdout.write(
            json.dumps({"resolution": resolution, "facts": facts.to_dict()}, indent=2, sort_keys=True)
            + "\n"
        )


if __name__ == "__main__":
    main()
