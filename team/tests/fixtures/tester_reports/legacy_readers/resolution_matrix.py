# TEST-ONLY LEGACY COPY -- imported by no production code.
#
# A frozen excerpt of team/pgai_agent_kanban/overwatch/resolution_matrix.py:
# its TESTER-report finding reader as it read before the report reading
# moved onto pgai_agent_kanban.reports.tester_report. It is kept for this
# release only, so that team/tests/unit/test_tester_report_differential.py
# can run the old reader over the TESTER report corpus; it is deleted
# together with that test once the readers are migrated. The code below is
# the behaviour being recorded: do not edit it. Only the report-reading part
# is copied -- parse_tester_report_findings, the bug-id reader the lane
# classifier applies to it, findings_all_filed, and the patterns they use,
# each verbatim; the block split is the legacy copy of stop_bug_files'
# finding_sections beside this file, exactly as the original imported it.
"""Legacy OVERWATCH resolution-matrix TESTER-report reader (test-only copy)."""

from __future__ import annotations

import re
from typing import Optional

from tests.fixtures.tester_reports.legacy_readers.stop_bug_files import finding_sections

# "Not filed" sentinel values a TESTER report's "Filed As:" field might
# carry when a finding was, for whatever reason, not filed. Matched
# case-insensitively against the stripped field value.
_NOT_FILED_VALUES = {"", "n/a", "none", "-", "not filed", "tbd"}

# Where a finding starts and ends is not decided here: the report is split
# by pgai_agent_kanban.cm.stop_bug_files.finding_sections, the one splitter
# every TESTER-report reader shares. This pattern only trims a finding
# heading's own label -- its first word, an optional number and an optional
# separator -- so "Finding 2: stale assertion" reads as the title "stale
# assertion".
_FINDING_TITLE_LABEL_RE = re.compile(r"^\S+(?:\s+#?\d+)?\s*[:.\u2013\u2014-]?\s*")
_FILED_AS_RE = re.compile(r"^Filed As\s*:\s*(.*)$", re.IGNORECASE)
# team/roles/TESTER.md, "Step 6.14"/"the two fields a high-risk finding
# carries": **Regression:** yes|no, present on Systemic-Risk-high findings.
_REGRESSION_RE = re.compile(r"^Regression\s*:\s*(?P<value>yes|no)\s*$", re.IGNORECASE)

_BUG_ID_ONLY_RE = re.compile(r"BUG-\d{4}")


def parse_tester_report_findings(report_text: str) -> list:
    """Extract the findings from a TESTER report's markdown text.

    Where each finding starts and ends is the shared finding-block rule:
    the report is split by pgai_agent_kanban.cm.stop_bug_files.
    finding_sections, so this parser sees exactly the blocks every other
    reader of a TESTER report sees. finding_heading_level's docstring in
    that module states the rule -- which headings open a finding and where
    its block ends -- in full; it is not restated here.

    Within a finding's block, two field lines are read: "**Filed As:**
    <value>" and "**Regression:** yes|no" (markdown bold markers and
    surrounding whitespace are stripped before matching, so both bold and
    plain forms match). A finding with no "Filed As:" line gets
    filed_as=None; a finding with no "Regression:" line (most findings --
    the field is only present on Systemic-Risk-high findings per
    TESTER.md) gets regression=None, distinct from an explicit "no". A
    field line under a later heading that closed the block (a following
    "### Step 7" or "## Lint Findings") is never read as the finding's own.

    Returns a list of dicts, one per finding block, in document order:
        {"title": str, "filed_as": str | None, "regression": bool | None,
         "body": str}
    "title" is the heading text after its label ("stale assertion" for
    "### Finding 2: stale assertion"; "" when the heading carries nothing
    more). "body" is the finding's raw block text (original, non-stripped
    lines, excluding the heading line) -- the OVERWATCH three-lane
    classifier's Lane A match (classify_lane() below) scans it for
    BUG-NNNN references the finding names as the regression's origin.
    Returns [] when the report has no finding blocks (e.g. a clean PASS
    report).
    """
    findings: list = []
    for heading, body in finding_sections(report_text):
        finding = {
            "title": _FINDING_TITLE_LABEL_RE.sub("", heading, count=1).strip(),
            "filed_as": None,
            "regression": None,
            "body": body,
        }
        for raw_line in body.splitlines():
            clean = raw_line.strip().replace("*", "").strip()
            filed_match = _FILED_AS_RE.match(clean)
            if filed_match:
                finding["filed_as"] = filed_match.group(1).strip()
            regression_match = _REGRESSION_RE.match(clean)
            if regression_match:
                finding["regression"] = regression_match.group("value").lower() == "yes"
        findings.append(finding)
    return findings


# ---------------------------------------------------------------------------
# Three-lane block classification (pure, side-effect-free — see this
# module's docstring's Layer 1). resolution_stage.py's run_resolution_stage
# calls classify_lane() only when a block has already fallen through to


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

