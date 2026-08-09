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
     carries the six deterministic facts the requirements doc names:
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
import configparser
import glob
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from typing import Optional

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sentinel returned by match_resolution() when no matrix row matches a
# block's facts. Fail-closed default: never a matrix row's own resolution
# value (load_matrix() rejects a row that tries to declare this literally).
HALT_HARD = "HALT-hard"

# The shipped default matrix's sole resolution.
CANCEL_AND_REBUNDLE = "CANCEL-AND-REBUNDLE"

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

# "Not filed" sentinel values a TESTER report's "Filed As:" field might
# carry when a finding was, for whatever reason, not filed. Matched
# case-insensitively against the stripped field value.
_NOT_FILED_VALUES = {"", "n/a", "none", "-", "not filed", "tbd"}

_FINDING_HEADER_RE = re.compile(r"^#{2,4}\s*Finding\s+\d+\s*:\s*(.*)$", re.IGNORECASE)
_FILED_AS_RE = re.compile(r"^Filed As\s*:\s*(.*)$", re.IGNORECASE)


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
        return asdict(self)


@dataclass(frozen=True)
class ResolutionResult:
    """The outcome of resolve_block(): the matched resolution plus its facts."""

    resolution: str
    facts: BlockFacts
    matched_row: Optional[MatrixRow]

    def to_dict(self) -> dict:
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
                               with at least a "filed_as" key. Takes
                               precedence over tester_report_path.
        tester_report_path:     Path to a TESTER verification report
                               markdown file; parsed for Finding sections
                               and their "Filed As:" values when
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
# Matching (the pure, side-effect-free core)
# ---------------------------------------------------------------------------


def match_resolution(facts: BlockFacts, matrix: list) -> str:
    """Return the resolution of the first matrix row whose six facts equal
    *facts*'s six facts, evaluated top to bottom (first match wins — same
    convention as CM's ship-policy decision matrix).

    Returns HALT_HARD when no row matches — including when *matrix* is
    empty, when *facts* differs from every row in even one field, or when
    *facts*.trigger_class is a value the matrix has no row for at all
    (an "unknown shape"). This is the fail-closed default the requirements
    doc mandates: "Unmatched or unknown shapes resolve to HALT-hard —
    never a default action."
    """
    target = facts.facts_key()
    for row in matrix:
        if row.facts_key() == target:
            return row.resolution
    return HALT_HARD


# ---------------------------------------------------------------------------
# TESTER report finding parsing (findings_all_filed evidence)
# ---------------------------------------------------------------------------


def parse_tester_report_findings(report_text: str) -> list:
    """Extract Finding sections and their "Filed As:" values from a TESTER
    report's markdown text.

    Recognizes headers of the form "### Finding N: <title>" (2-4 leading
    '#', case-insensitive "Finding", per team/roles/TESTER.md's report
    template) and, within a finding's body, a line of the form
    "**Filed As:** <value>" (markdown bold markers and surrounding
    whitespace are stripped before matching, so both bold and plain forms
    match). A finding with no "Filed As:" line at all gets filed_as=None.

    Returns a list of {"title": str, "filed_as": str | None} dicts, one per
    Finding section, in document order. Returns [] when the report has no
    recognizable Finding sections (e.g. a clean PASS report).
    """
    findings: list = []
    current: Optional[dict] = None
    for raw_line in report_text.splitlines():
        line = raw_line.strip()
        header_match = _FINDING_HEADER_RE.match(line)
        if header_match:
            if current is not None:
                findings.append(current)
            current = {"title": header_match.group(1).strip(), "filed_as": None}
            continue
        if current is None:
            continue
        clean = line.replace("*", "").strip()
        filed_match = _FILED_AS_RE.match(clean)
        if filed_match:
            current["filed_as"] = filed_match.group(1).strip()
    if current is not None:
        findings.append(current)
    return findings


def findings_all_filed(findings: list) -> bool:
    """True when every finding in *findings* carries a non-empty, non-sentinel
    "filed_as" value. Vacuously true for an empty list — no findings means
    nothing was left unfiled.
    """
    if not findings:
        return True
    for finding in findings:
        value = (finding.get("filed_as") or "").strip().lower()
        if value in _NOT_FILED_VALUES:
            return False
    return True


# ---------------------------------------------------------------------------
# Ceiling resolution (Python counterpart of pp_max_rc_retries)
# ---------------------------------------------------------------------------


def _read_ini_int(cfg_path: str, section: str, key: str) -> Optional[int]:
    """Read an integer value from an INI-format config file's [section] key.

    Returns None when the file, section, or key is absent, or when the
    value is present but empty. Raises CeilingConfigError when the value is
    present but not a valid non-negative integer.
    """
    if not cfg_path or not os.path.isfile(cfg_path):
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(cfg_path, encoding="utf-8")
    except configparser.Error as exc:
        raise CeilingConfigError(f"Cannot parse config file '{cfg_path}': {exc}") from exc
    if not parser.has_option(section, key):
        return None
    raw = parser.get(section, key).strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise CeilingConfigError(
            f"resolution_matrix: invalid value '{raw}' in {cfg_path} "
            f"[{section}] {key} (expected non-negative integer)"
        )
    return int(raw)


def resolve_max_rc_retries(project_name: str, kanban_root: str) -> int:
    """Python counterpart of pp_max_rc_retries() (team/scripts/lib/project_paths.sh).

    Resolution order (first non-empty source wins):
      1. <kanban_root>/projects/<project_name>/project.cfg [release] max_rc_retries
      2. <kanban_root>/kanban.cfg [release] max_rc_retries (shipped default: 3)

    Raises CeilingConfigError, naming both file paths checked, when the key
    is absent from BOTH sources or when either source's value is not a
    valid non-negative integer. Never silently substitutes a hardcoded
    default — see this module's docstring above.
    """
    project_cfg = os.path.join(kanban_root, "projects", project_name, "project.cfg")
    project_val = _read_ini_int(project_cfg, "release", "max_rc_retries")
    if project_val is not None:
        return project_val

    kanban_cfg = os.path.join(kanban_root, "kanban.cfg")
    kanban_val = _read_ini_int(kanban_cfg, "release", "max_rc_retries")
    if kanban_val is not None:
        return kanban_val

    raise CeilingConfigError(
        f"resolution_matrix: max_rc_retries is required but absent for project "
        f"'{project_name}'. Checked {project_cfg} [release] max_rc_retries and "
        f"{kanban_cfg} [release] max_rc_retries — set one of them."
    )


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


def git_tag_exists(dev_tree_path: str, version: str) -> bool:
    """True when *version* (e.g. "v2.0.1") exists as a git tag in *dev_tree_path*.

    Raises EvidenceError if the git command itself fails (bad path, not a
    repository) — a plumbing failure, distinct from "tag legitimately
    absent" (which returns False cleanly).
    """
    stdout = _run_git(dev_tree_path, ["tag", "-l", version])
    return version in stdout.split()


def operator_commit_in_rc_lineage(dev_tree_path: str, rc_branch: str, base_branch: str) -> bool:
    """True when the RC branch's mainline carries a non-merge commit.

    Walks --first-parent of *rc_branch* since it diverged from *base_branch*
    (git log --first-parent --pretty=%P <rc_branch> --not <base_branch>).
    Every legitimate contribution to an RC branch under this kanban's
    workflow (SOP.md "Release Lifecycle" / roles/CODER.md "Git Operates
    Locally for CODER") lands as a --no-ff merge commit of a feature/<TASK-ID>
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
            return "ship-policy-halt"
        if content.strip():
            return "operator-halt"
    return "unknown"


def _resolve_findings(evidence: BlockEvidence) -> Optional[list]:
    if evidence.tester_findings is not None:
        return evidence.tester_findings
    if evidence.tester_report_path and os.path.isfile(evidence.tester_report_path):
        try:
            text = open(evidence.tester_report_path, "r", encoding="utf-8").read()
        except OSError:
            return None
        return parse_tester_report_findings(text)
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
        if git_tag_exists(evidence.dev_tree_path, evidence.target_version):
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
