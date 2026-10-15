# TEST-ONLY LEGACY COPY -- imported by no production code.
#
# A frozen copy of the report-reading modes of the embedded Python program in
# team/scripts/lib/cm/release_ship_policy_report.sh (_ship_policy_report_py):
# the "dims" mode behind read_ship_policy_finding_dimensions and the "values"
# mode behind read_ship_policy_report_values, as they read before the report
# reading moved onto pgai_agent_kanban.reports.tester_report. It is kept for
# this release only, so that team/tests/unit/test_tester_report_differential.py
# can run the old reader over the TESTER report corpus; it is deleted
# together with that test once the readers are migrated.
#
# The program was already Python inside a bash heredoc, so it is kept here as
# a Python module rather than a shell copy that would have to be sourced:
# each mode's body is one function, its argv read replaced by a parameter and
# its printed stdout lines returned instead (the stderr narration is kept).
# Every helper, pattern and decision line is the original's, verbatim, and
# its two imports point at the legacy copies beside this file -- the finding
# split and field reader of stop_bug_files, and read_state_field -- exactly
# as the original imported the production modules. Do not edit the logic:
# it is the behaviour being recorded. The "lookup" mode reads task folders,
# not a report, and is not copied.
"""Legacy CM ship-policy TESTER-report reader (test-only copy)."""

from __future__ import annotations

import re
import sys


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def first_word(value):
    """The first letters-and-dashes word of *value*, lowercased, with
    underscores read as dashes and trailing punctuation dropped."""
    m = re.match(r"([A-Za-z][A-Za-z_-]*)", value.strip())
    return m.group(1).lower().replace("_", "-").rstrip("-") if m else ""


# A "Label:" prefix — plain words followed by a colon — as in
# "Recommendation: PASS" or "Decision: SHIP-WITH-CONCERNS". Hyphenated
# tokens never match, so "SHIP-WITH-CONCERNS: rationale" keeps its token.
LABEL_PREFIX_RE = re.compile(r"^\s*[A-Za-z][A-Za-z ]*?\s*:\s*(.*)$")
# The template's own alternatives list ("PASS | SHIP-WITH-CONCERNS",
# "low / medium / high"): a placeholder nobody edited, never a value.
PLACEHOLDER_RE = re.compile(r"\|| / ")


def normalize_value(raw, accepted):
    """Reduce a raw report field line to a token in *accepted*, "none" for an
    absent field, or "unparseable". The rules are the header doc of
    read_ship_policy_report_values."""
    if raw is None:
        return "none"
    text = re.sub(r"<!--.*?-->", " ", raw)
    text = re.sub(r"[*`]", " ", text).strip().strip("_").strip()
    if not text or text.lower() == "none":
        return "none"

    def token_of(candidate):
        if PLACEHOLDER_RE.search(candidate):
            return "unparseable"
        word = first_word(candidate)
        return word if word in accepted else "unparseable"

    token = token_of(text)
    if token == "unparseable":
        m = LABEL_PREFIX_RE.match(text)
        if m and m.group(1).strip():
            token = token_of(m.group(1))
    return token


RECOMMENDATION_TOKENS = ("pass", "ship-with-concerns", "ship-with-serious-concerns")
SYSTEMIC_RISK_TOKENS = ("low", "medium", "high", "none")


def read_field(path, heading):
    """The single field reader every CM lookup uses. None when the file is
    missing or the heading has no value (read_state_field's "none")."""
    from tests.fixtures.tester_reports.legacy_readers.read_state_field import read_state_field

    value = read_state_field(path, heading)
    return None if value == "none" else value


def read_report_text(report_path):
    try:
        with open(report_path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Mode: dims — regression/integrity for the systemic_risk=high row
# ---------------------------------------------------------------------------

def finding_dimensions(report_path):
    """The "dims" mode: returns the "<regression> <integrity>" line it printed."""
    text = read_report_text(report_path)
    if text is None:
        return "unknown unknown"

    from tests.fixtures.tester_reports.legacy_readers.stop_bug_files import (
        field_first_word,
        finding_blocks,
    )

    blocks = finding_blocks(text)
    if not blocks:
        # Say so before printing the fail-loud default: without this line
        # the caller cannot distinguish a report whose shape hid every
        # finding from a finding that genuinely reads unknown.
        print(
            f"zero finding blocks in {report_path}: no heading at level 3 to 6 "
            f'whose text starts with the word "Finding"; regression/integrity '
            f"defaulted to unknown",
            file=sys.stderr,
        )
        sys.stderr.flush()

    regression = "unknown"
    integrity = "unknown"
    for section in blocks:
        if field_first_word(section, "Systemic Risk") != "high":
            continue
        reg = field_first_word(section, "Regression")
        if reg == "yes":
            regression = "yes"
        elif reg == "no" and regression != "yes":
            regression = "no"
        integ = field_first_word(section, "Integrity")
        if integ in ("breakage", "destructive"):
            integrity = integ
        elif integ == "none" and integrity not in ("breakage", "destructive"):
            integrity = "none"

    return f"{regression} {integrity}"


# ---------------------------------------------------------------------------
# Mode: values — normalized Recommendation / Systemic Risk + large-fix flag
# ---------------------------------------------------------------------------

def report_values(report_path):
    """The "values" mode: returns its five "key=value" lines as a dict, in
    the order it printed them."""
    text = read_report_text(report_path)
    if text is None:
        print(f"report not readable at {report_path}: recommendation and "
              f"systemic risk defaulted to none", file=sys.stderr)
        sys.stderr.flush()
        rec_raw = risk_raw = None
        large = "no"
    else:
        from tests.fixtures.tester_reports.legacy_readers.stop_bug_files import (
            field_first_word,
            finding_blocks,
        )

        rec_raw = read_field(report_path, "Recommendation")
        risk_raw = read_field(report_path, "Systemic Risk")
        large = "no"
        for section in finding_blocks(text):
            if field_first_word(section, "Fix Effort") == "large":
                large = "yes"
                break

    recommendation = normalize_value(rec_raw, RECOMMENDATION_TOKENS)
    systemic_risk = normalize_value(risk_raw, SYSTEMIC_RISK_TOKENS)
    for label, raw, token in (
        ("Recommendation", rec_raw, recommendation),
        ("Systemic Risk", risk_raw, systemic_risk),
    ):
        if token == "unparseable":
            print(f"{label} in {report_path} could not be parsed: {raw!r}",
                  file=sys.stderr)
        elif raw is not None and raw.strip().lower() != token:
            print(f"{label} in {report_path} normalized: {raw!r} -> {token}",
                  file=sys.stderr)
    sys.stderr.flush()

    return {
        "recommendation": recommendation,
        "recommendation_raw": rec_raw if rec_raw is not None else "none",
        "systemic_risk": systemic_risk,
        "systemic_risk_raw": risk_raw if risk_raw is not None else "none",
        "has_large_fix_effort": large,
    }
