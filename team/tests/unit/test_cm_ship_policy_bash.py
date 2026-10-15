"""
test_cm_ship_policy_bash.py
============================
Behavioral unit tests for cm_ship_policy and read_ship_policy_finding_dimensions
in team/scripts/lib/cm/release_ship_policy.sh -- the ship-policy decision
matrix and the report-field reader that supplies its regression and
integrity-damage dimensions.

Tests source the shell library and invoke each function via the bash
harness, asserting on stdout. No CM release run, no repository, no TESTER
task on disk is involved -- cm_ship_policy is a pure string-in/string-out
function by design (see its own header doc in release_ship_policy.sh).

Coverage:
  - Every row of the ship-policy matrix (team/roles/CM.md, "Ship policy"),
    including the fail-loud rows this file's production change does not
    alter: TESTER state BLOCKED, low/medium-risk recommendations, no
    TESTER task found at all, and a found TESTER task in an unrecognized
    state.
  - The systemic_risk=high row this task narrows: a regression, integrity
    damage, or a large fix effort on the high-risk finding each still
    halts; a pre-existing, non-integrity-damaging, small-fix finding now
    ships as SHIP-KNOWN-ISSUE, a value distinct from the three pre-existing
    ship outcomes.
  - Absent and unrecognized values on the two new dimensions still halt --
    the row fails loud on untrusted input rather than silently defaulting
    to ship.
  - read_ship_policy_finding_dimensions's report-field parsing: only a
    finding whose own Systemic Risk is "high" contributes to the result, a
    low-risk finding's own Regression/Integrity values never leak into the
    decision, and a report with no structured Findings section (or no
    report at all) reads as "unknown unknown" rather than crashing or
    silently shipping.
  - The finding heading is recognised at any level from 3 down: a report
    that nests its findings under intermediate sub-section headings
    ("#### Finding N:", "##### Finding N:") reads exactly as the flat
    "### Finding N:" shape, a nested finding's fields stop at the next
    heading no deeper than its own, and the level-2 "## Findings" section
    heading never starts a block.
  - The zero-block narration: a readable report with no finding heading at
    all still prints "unknown unknown" on stdout, and says so on stderr
    naming the report path and that the values defaulted for that reason;
    a report with finding blocks narrates nothing.
  - read_ship_policy_report_values's normalization: a bold, backticked,
    label-prefixed or annotated Recommendation / Systemic Risk reaches the
    row it names; a report that kept the template's HTML comment beside the
    field never reads the comment as the value; a garbled value, or the
    template's own alternatives placeholder, reads as "unparseable" and
    the matrix floors it to SHIP-KNOWN-BUGS (the SHIP-WITH-CONCERNS row),
    never HALT and never SHIP-FUNCTIONAL.
  - has_large_fix_effort is anchored to each finding block's own Fix
    Effort line: prose ("the Fix Effort is not large"), the template
    legend ("small / medium / large") and "larger" never set it; a finding
    whose own line reads "Fix Effort: large" still does.
  - read_ship_policy_tester_lookup: a TESTER task whose Role or Release
    Version matches only after normalization is still found (never the
    test-not-required row), the mismatch is narrated on stderr and
    reported as a mismatch triple for the caller to file a bug from; a
    version-shaped mismatch is never confused with a task for another RC.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

# Path to the script under test, relative to the team/ directory where
# pytest runs (see test_semver_bash.py for the same convention).
_LIB = "scripts/lib/cm/release_ship_policy.sh"


def _ship_policy(*args: str) -> str:
    """Return a bash snippet that sources the library then calls
    cm_ship_policy with the given positional arguments, each single-quoted
    so an empty string or a value containing spaces is passed intact."""
    quoted = " ".join(f"'{a}'" for a in args)
    return f"source {_LIB} && cm_ship_policy {quoted}"


# ---------------------------------------------------------------------------
# Fail-loud and otherwise-unrelated rows -- pinned so this task's dimension
# addition (and any future edit) cannot silently loosen them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args, expected",
    [
        # Row 1: TESTER state BLOCKED halts regardless of every other field.
        (("blocked", "high", "pass", "no", "yes", "no", "none"), "HALT"),
        (("blocked", "low", "ship-with-concerns", "no", "yes", "no", "none"), "HALT"),
        # Row 3: DONE, low/medium risk, PASS -> SHIP-FUNCTIONAL.
        (("done", "low", "pass", "no", "yes", "no", "none"), "SHIP-FUNCTIONAL"),
        (("done", "medium", "pass", "no", "yes", "no", "none"), "SHIP-FUNCTIONAL"),
        # Row 4: DONE, low/medium risk, SHIP-WITH-CONCERNS -> SHIP-KNOWN-BUGS.
        (("done", "low", "ship-with-concerns", "no", "yes", "no", "none"), "SHIP-KNOWN-BUGS"),
        # Row 5: DONE, low/medium risk, SHIP-WITH-SERIOUS-CONCERNS, no large
        # fix -> SHIP-NON-FUNCTIONAL.
        (("done", "medium", "ship-with-serious-concerns", "no", "yes", "no", "none"), "SHIP-NON-FUNCTIONAL"),
        # Row 6: DONE, low/medium risk, SHIP-WITH-SERIOUS-CONCERNS, any large
        # fix -> HALT.
        (("done", "medium", "ship-with-serious-concerns", "yes", "yes", "no", "none"), "HALT"),
        # Row 7: no TESTER task found at all -> SHIP-FUNCTIONAL (the
        # "no report, Test Required: false" row).
        (("none", "none", "none", "no", "no", "no", "none"), "SHIP-FUNCTIONAL"),
        # Row 8: a TESTER task was found but its state is neither BLOCKED
        # nor DONE -> HALT ("unrecognized values" row).
        (("working", "none", "none", "no", "yes", "no", "none"), "HALT"),
        (("wont-do", "none", "none", "no", "yes", "no", "none"), "HALT"),
    ],
)
def test_fail_loud_and_unaffected_rows_unchanged(tmp_path, args, expected):
    result = run_bash(tmp_path, _ship_policy(*args))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


# ---------------------------------------------------------------------------
# The systemic_risk=high row this task narrows.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args, expected, reason",
    [
        # A regression finding always halts, whatever its integrity/fix-effort values.
        (
            ("done", "high", "ship-with-serious-concerns", "no", "yes", "yes", "none"),
            "HALT",
            "regression=yes must halt even with no integrity damage and a small fix",
        ),
        # A pre-existing, non-integrity-damaging, small-fix finding ships as a known issue.
        (
            ("done", "high", "ship-with-serious-concerns", "no", "yes", "no", "none"),
            "SHIP-KNOWN-ISSUE",
            "regression=no, integrity=none, small fix must ship as a distinct known-issue outcome",
        ),
        # Integrity damage halts even for a pre-existing, small-fix finding.
        (
            ("done", "high", "ship-with-serious-concerns", "no", "yes", "no", "breakage"),
            "HALT",
            "a breakage-class integrity finding must halt even when pre-existing",
        ),
        (
            ("done", "high", "ship-with-serious-concerns", "no", "yes", "no", "destructive"),
            "HALT",
            "a destructive-path integrity finding must halt even when pre-existing",
        ),
        # A large fix effort halts even for a pre-existing, non-integrity-damaging finding.
        (
            ("done", "high", "ship-with-serious-concerns", "yes", "yes", "no", "none"),
            "HALT",
            "a large-fix-effort finding must halt even when pre-existing and integrity-clean",
        ),
        # Absent/unrecognized values on the new dimensions never default to ship.
        (
            ("done", "high", "ship-with-serious-concerns", "no", "yes", "", "none"),
            "HALT",
            "an absent regression field must halt, not default-ship",
        ),
        (
            ("done", "high", "ship-with-serious-concerns", "no", "yes", "banana", "none"),
            "HALT",
            "an unrecognized regression value must halt, not default-ship",
        ),
        (
            ("done", "high", "ship-with-serious-concerns", "no", "yes", "no", ""),
            "HALT",
            "an absent integrity field must halt, not default-ship",
        ),
        (
            ("done", "high", "ship-with-serious-concerns", "no", "yes", "no", "banana"),
            "HALT",
            "an unrecognized integrity value must halt, not default-ship",
        ),
        # systemic_risk=high overrides an otherwise-clean recommendation entirely.
        (
            ("done", "high", "pass", "no", "yes", "yes", "none"),
            "HALT",
            "systemic_risk=high overrides an otherwise-clean recommendation",
        ),
    ],
)
def test_systemic_risk_high_row_split_by_regression_and_integrity(tmp_path, args, expected, reason):
    result = run_bash(tmp_path, _ship_policy(*args))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected, reason


def test_ship_known_issue_is_distinct_from_existing_ship_outcomes(tmp_path):
    known_issue = run_bash(
        tmp_path,
        _ship_policy("done", "high", "ship-with-serious-concerns", "no", "yes", "no", "none"),
    ).stdout.strip()
    functional = run_bash(
        tmp_path,
        _ship_policy("done", "low", "pass", "no", "yes", "no", "none"),
    ).stdout.strip()
    known_bugs = run_bash(
        tmp_path,
        _ship_policy("done", "low", "ship-with-concerns", "no", "yes", "no", "none"),
    ).stdout.strip()
    non_functional = run_bash(
        tmp_path,
        _ship_policy("done", "low", "ship-with-serious-concerns", "no", "yes", "no", "none"),
    ).stdout.strip()

    assert known_issue == "SHIP-KNOWN-ISSUE"
    assert {functional, known_bugs, non_functional, known_issue} == {
        "SHIP-FUNCTIONAL",
        "SHIP-KNOWN-BUGS",
        "SHIP-NON-FUNCTIONAL",
        "SHIP-KNOWN-ISSUE",
    }


# ---------------------------------------------------------------------------
# read_ship_policy_finding_dimensions: report-field parsing.
# ---------------------------------------------------------------------------


def _write_report(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    report = tmp_path / "report.md"
    report.write_text(body, encoding="utf-8")
    return report


def _run_read_dims(tmp_path: pathlib.Path, report_path: pathlib.Path):
    """Source the library and call read_ship_policy_finding_dimensions on
    *report_path*, returning the raw harness result so a test can inspect
    stderr (the narration channel) as well as stdout (the two values)."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_finding_dimensions '{report_path}'",
    )
    assert result.returncode == 0, result.stderr
    return result


def _read_dims(tmp_path: pathlib.Path, report_path: pathlib.Path) -> str:
    return _run_read_dims(tmp_path, report_path).stdout.strip()


def _single_high_risk_report(heading_prefix: str, regression: str, integrity: str) -> str:
    """One high-risk finding under a heading of the given prefix ("###",
    "####", ...), carrying the given Regression/Integrity values, in the
    same surrounding report shape the level-3 cases above use."""
    return (
        "# Verification Report: rc/v0.0.0\n\n"
        "## Systemic Risk\nhigh\n\n"
        "## Findings\n\n"
        f"{heading_prefix} Finding 1: a high-risk finding\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** high\n"
        f"**Regression:** {regression}\n"
        f"**Integrity:** {integrity}\n"
        "**Filed As:** BUG-0006 (Path C)\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n"
    )


def test_regression_finding_reads_yes(tmp_path):
    report = _write_report(
        tmp_path,
        "# Verification Report: rc/v0.0.0\n\n"
        "## Systemic Risk\nhigh\n\n"
        "## Findings\n\n"
        "### Finding 1: dashboard render crash\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** high (breaks the dashboard on every render)\n"
        "**Regression:** yes\n"
        "**Integrity:** none\n"
        "**Filed As:** BUG-0001 (Path C)\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
    )
    assert _read_dims(tmp_path, report) == "yes none"


def test_pre_existing_non_integrity_finding_reads_no_none(tmp_path):
    report = _write_report(
        tmp_path,
        "# Verification Report: rc/v0.0.0\n\n"
        "## Systemic Risk\nhigh\n\n"
        "## Findings\n\n"
        "### Finding 1: legacy artefact defect nobody has run\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** high (pre-existing, unrelated to this RC)\n"
        "**Regression:** no\n"
        "**Integrity:** none\n"
        "**Filed As:** BUG-0002 (Path C)\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
    )
    assert _read_dims(tmp_path, report) == "no none"


def test_integrity_damaging_finding_reads_breakage(tmp_path):
    report = _write_report(
        tmp_path,
        "# Verification Report: rc/v0.0.0\n\n"
        "## Systemic Risk\nhigh\n\n"
        "## Findings\n\n"
        "### Finding 1: gated runner exits non-zero with no pass signal\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** high\n"
        "**Regression:** no\n"
        "**Integrity:** breakage (gated runner produced no trustworthy pass signal)\n"
        "**Filed As:** BUG-0003 (Path C)\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
    )
    assert _read_dims(tmp_path, report) == "no breakage"


def test_low_risk_finding_never_contributes_even_when_regression_yes(tmp_path):
    """A finding whose OWN Systemic Risk is not high must not leak its
    Regression/Integrity values into the decision -- only the report's
    actual high-risk finding(s) matter (see the function's header doc for
    why: the row this feeds decides whether THE high-risk finding is safe
    to ship, not whether an unrelated finding elsewhere in the same report
    happens to be a regression)."""
    report = _write_report(
        tmp_path,
        "# Verification Report: rc/v0.0.0\n\n"
        "## Systemic Risk\nhigh\n\n"
        "## Findings\n\n"
        "### Finding 1: unrelated low-risk regression\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** low (isolated to one test file)\n"
        "**Regression:** yes\n"
        "**Integrity:** none\n"
        "**Filed As:** BUG-0004 (Path C)\n\n"
        "### Finding 2: pre-existing high-risk defect\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** high (pre-existing)\n"
        "**Regression:** no\n"
        "**Integrity:** none\n"
        "**Filed As:** BUG-0005 (Path C)\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
    )
    assert _read_dims(tmp_path, report) == "no none"


def test_legacy_report_with_no_structured_findings_reads_unknown(tmp_path):
    report = _write_report(
        tmp_path,
        "# Verification Report: rc/v0.0.0\n\n"
        "## Systemic Risk\nhigh\n\n"
        "## Findings\n\n"
        "No structured findings recorded in this legacy-shaped report.\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
    )
    assert _read_dims(tmp_path, report) == "unknown unknown"


def test_missing_report_path_reads_unknown(tmp_path):
    missing = tmp_path / "does-not-exist.md"
    assert _read_dims(tmp_path, missing) == "unknown unknown"


# ---------------------------------------------------------------------------
# read_ship_policy_finding_dimensions: the finding heading at any level from
# 3 down, and the zero-block narration.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("heading_prefix", ["####", "#####"])
@pytest.mark.parametrize(
    "regression, integrity, expected",
    [
        ("no", "none", "no none"),
        ("yes", "none", "yes none"),
        ("no", "breakage", "no breakage"),
    ],
)
def test_deeper_finding_heading_reads_same_values_as_level_3(
    tmp_path, heading_prefix, regression, integrity, expected
):
    """A finding under "#### Finding" or "##### Finding" reads exactly the
    values the identical finding reads under "### Finding" -- the
    **Field:** value lines are the contract, not the heading level."""
    level3 = _write_report(tmp_path, _single_high_risk_report("###", regression, integrity))
    assert _read_dims(tmp_path, level3) == expected
    deeper = _write_report(tmp_path, _single_high_risk_report(heading_prefix, regression, integrity))
    assert _read_dims(tmp_path, deeper) == expected


def test_level_4_no_none_finding_reaches_the_same_ship_known_issue_verdict(tmp_path):
    """The values read from a "####" finding drive cm_ship_policy to the
    same SHIP-KNOWN-ISSUE verdict the "###" shape reaches: the decision
    table is unchanged, only what the reader recognises."""
    verdicts = {}
    for prefix in ("###", "####"):
        report = _write_report(tmp_path, _single_high_risk_report(prefix, "no", "none"))
        result = run_bash(
            tmp_path,
            f"source {_LIB} && "
            f"read -r reg integ < <(read_ship_policy_finding_dimensions '{report}') && "
            "cm_ship_policy done high ship-with-serious-concerns no yes \"$reg\" \"$integ\"",
        )
        assert result.returncode == 0, result.stderr
        verdicts[prefix] = result.stdout.strip()
    assert verdicts == {"###": "SHIP-KNOWN-ISSUE", "####": "SHIP-KNOWN-ISSUE"}


def test_findings_nested_under_step_subsections_read_their_own_fields(tmp_path):
    """The shape a step-by-step report produces: findings demoted to level 4
    under level-3 step headings. Each finding's fields stop at the next
    heading no deeper than its own, so a stray field in a following step's
    prose is never attributed to the finding before it, and the stronger
    value across the two high-risk findings still wins."""
    report = _write_report(
        tmp_path,
        "# Verification Report: rc/v0.0.0\n\n"
        "## Systemic Risk\nhigh\n\n"
        "## Findings\n\n"
        "### Step 5a: gated unit runner\n\n"
        "Step prose.\n\n"
        "#### Finding 12: gated unit runner is RED\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** high\n"
        "**Regression:** no\n"
        "**Integrity:** breakage\n\n"
        "### Step 5b: gated integration runner\n\n"
        "**Regression:** yes\n\n"
        "#### Finding 14: integration suite removed the live HALT\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** high\n"
        "**Regression:** no\n"
        "**Integrity:** none\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
    )
    assert _read_dims(tmp_path, report) == "no breakage"


def test_findings_section_heading_alone_never_starts_a_block(tmp_path):
    """"## Findings" is a section heading, not a finding: a report whose
    fields sit directly under it with no finding heading yields zero blocks
    and reads unknown rather than picking those fields up."""
    report = _write_report(
        tmp_path,
        "# Verification Report: rc/v0.0.0\n\n"
        "## Systemic Risk\nhigh\n\n"
        "## Findings\n\n"
        "**Systemic Risk:** high\n"
        "**Regression:** no\n"
        "**Integrity:** none\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
    )
    result = _run_read_dims(tmp_path, report)
    assert result.stdout.strip() == "unknown unknown"
    assert "zero finding blocks" in result.stderr


def test_zero_finding_blocks_narrates_report_path_and_default_on_stderr(tmp_path):
    """A readable report with no finding heading still prints the fail-loud
    default on stdout, and says why on stderr: the report path, that zero
    finding blocks were found, and that regression/integrity defaulted to
    unknown for that reason."""
    report = _write_report(
        tmp_path,
        "# Verification Report: rc/v0.0.0\n\n"
        "## Systemic Risk\nhigh\n\n"
        "## Findings\n\n"
        "No structured findings recorded in this legacy-shaped report.\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
    )
    result = _run_read_dims(tmp_path, report)
    assert result.stdout.strip() == "unknown unknown"
    narration = result.stderr
    assert "zero finding blocks" in narration
    assert str(report) in narration
    assert "regression/integrity defaulted to unknown" in narration


def test_report_with_finding_blocks_narrates_nothing(tmp_path):
    """The narration is the zero-block signal only: a report that yields
    blocks -- at level 3 or deeper -- writes nothing to stderr, so the
    release step's output stays quiet on the common path."""
    for prefix in ("###", "####"):
        report = _write_report(tmp_path, _single_high_risk_report(prefix, "no", "none"))
        result = _run_read_dims(tmp_path, report)
        assert result.stdout.strip() == "no none"
        assert result.stderr.strip() == "", result.stderr


# ---------------------------------------------------------------------------
# read_ship_policy_report_values: value normalization, the unparseable
# floor, and the anchored per-finding Fix Effort flag.
# ---------------------------------------------------------------------------


def _report_values(tmp_path: pathlib.Path, report_path: pathlib.Path) -> dict:
    """Source the library, run read_ship_policy_report_values on
    *report_path*, and return its key=value stdout as a dict (stderr is
    narration and is not part of the contract asserted here)."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_report_values '{report_path}'",
    )
    assert result.returncode == 0, result.stderr
    values = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def _decision_from_report(tmp_path: pathlib.Path, report_path: pathlib.Path, tester_state: str = "done") -> str:
    """Feed a report's normalized values (and its finding dimensions) to
    cm_ship_policy exactly as Step 4d does, and return the decision."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && "
        f"declare -A v && while IFS= read -r line; do v[\"${{line%%=*}}\"]=\"${{line#*=}}\"; done "
        f"< <(read_ship_policy_report_values '{report_path}' 2>/dev/null) && "
        f"read -r reg integ < <(read_ship_policy_finding_dimensions '{report_path}' 2>/dev/null) && "
        f"cm_ship_policy '{tester_state}' \"${{v[systemic_risk]}}\" \"${{v[recommendation]}}\" "
        f"\"${{v[has_large_fix_effort]}}\" yes \"$reg\" \"$integ\"",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _report_with_values(recommendation_section: str, systemic_risk_section: str, findings: str = "") -> str:
    return (
        "# Verification Report: rc/v0.0.0\n\n"
        f"## Recommendation\n\n{recommendation_section}\n\n"
        f"## Systemic Risk\n\n{systemic_risk_section}\n\n"
        "## Findings\n\n"
        f"{findings}"
        "## Lint Findings (non-blocking)\n\nNone.\n"
    )


_TEMPLATE_RECOMMENDATION_COMMENT = (
    "<!-- Allowed values: PASS | SHIP-WITH-CONCERNS | SHIP-WITH-SERIOUS-CONCERNS\n"
    "PASS                       -- All acceptance criteria pass.\n"
    "There is no BLOCK recommendation.\n"
    "-->"
)


@pytest.mark.parametrize(
    "recommendation_section, expected_token",
    [
        ("**Recommendation:** **SHIP-WITH-SERIOUS-CONCERNS**", "ship-with-serious-concerns"),
        ("`SHIP-WITH-CONCERNS`", "ship-with-concerns"),
        ("**Decision: PASS**", "pass"),
        ("SHIP-WITH-SERIOUS-CONCERNS -- two suites red", "ship-with-serious-concerns"),
        ("Pass.", "pass"),
        ("ship_with_concerns", "ship-with-concerns"),
        (_TEMPLATE_RECOMMENDATION_COMMENT + "\n\nSHIP-WITH-SERIOUS-CONCERNS", "ship-with-serious-concerns"),
    ],
)
def test_decorated_recommendation_reads_the_token_it_names(tmp_path, recommendation_section, expected_token):
    report = _write_report(tmp_path, _report_with_values(recommendation_section, "low"))
    values = _report_values(tmp_path, report)
    assert values["recommendation"] == expected_token
    assert values["recommendation_raw"].strip() != ""


def test_bold_serious_concerns_report_yields_ship_non_functional_never_functional(tmp_path):
    report = _write_report(
        tmp_path, _report_with_values("**Recommendation:** **SHIP-WITH-SERIOUS-CONCERNS**", "low")
    )
    assert _decision_from_report(tmp_path, report) == "SHIP-NON-FUNCTIONAL"


def test_report_keeping_the_template_html_comment_reads_the_value_below_it(tmp_path):
    report = _write_report(
        tmp_path,
        _report_with_values(
            _TEMPLATE_RECOMMENDATION_COMMENT + "\n\nSHIP-WITH-SERIOUS-CONCERNS",
            "<!-- Allowed values: low | medium | high -->\n\nlow",
        ),
    )
    values = _report_values(tmp_path, report)
    assert values["recommendation"] == "ship-with-serious-concerns"
    assert values["systemic_risk"] == "low"
    assert _decision_from_report(tmp_path, report) == "SHIP-NON-FUNCTIONAL"


@pytest.mark.parametrize(
    "systemic_risk_section",
    ["High — one dashboard finding", "**High**", "high (broader framework regression)", "HIGH."],
)
def test_annotated_high_systemic_risk_reaches_the_high_risk_row(tmp_path, systemic_risk_section):
    """An annotated report-level Systemic Risk of high reaches the Trigger 2
    row: with no readable finding dimensions the row halts, exactly as a
    bare "high" does -- never the FUNCTIONAL row a mismatch used to select."""
    report = _write_report(tmp_path, _report_with_values("PASS", systemic_risk_section))
    values = _report_values(tmp_path, report)
    assert values["systemic_risk"] == "high"
    assert _decision_from_report(tmp_path, report) == "HALT"


def test_garbled_recommendation_reads_unparseable_and_floors_to_known_bugs(tmp_path):
    report = _write_report(tmp_path, _report_with_values("Recommendation: SHIPP-WITH-CONCERNZ", "low"))
    values = _report_values(tmp_path, report)
    assert values["recommendation"] == "unparseable"
    assert values["recommendation_raw"] == "Recommendation: SHIPP-WITH-CONCERNZ"
    decision = _decision_from_report(tmp_path, report)
    assert decision == "SHIP-KNOWN-BUGS", "an unparseable value is the SHIP-WITH-CONCERNS row"
    assert decision not in ("HALT", "SHIP-FUNCTIONAL")


def test_unedited_template_placeholder_is_unparseable_not_pass(tmp_path):
    """The template's own "Decision: PASS | SHIP-WITH-CONCERNS | ..." line
    lists every value; reading its first word as PASS would ship a report
    nobody filled in."""
    report = _write_report(
        tmp_path,
        _report_with_values("**Decision: PASS | SHIP-WITH-CONCERNS | SHIP-WITH-SERIOUS-CONCERNS**", "low"),
    )
    assert _report_values(tmp_path, report)["recommendation"] == "unparseable"


def test_garbled_systemic_risk_floors_a_pass_to_known_bugs(tmp_path):
    report = _write_report(tmp_path, _report_with_values("PASS", "hgih"))
    values = _report_values(tmp_path, report)
    assert values["systemic_risk"] == "unparseable"
    assert _decision_from_report(tmp_path, report) == "SHIP-KNOWN-BUGS"


def test_absent_systemic_risk_reads_none_and_is_not_high(tmp_path):
    """A report with no Systemic Risk section reads "none" -- the template's
    own default is low -- so a PASS still ships FUNCTIONAL."""
    report = _write_report(
        tmp_path,
        "# Verification Report: rc/v0.0.0\n\n## Recommendation\nPASS\n\n## Findings\n\nNone.\n",
    )
    values = _report_values(tmp_path, report)
    assert values["systemic_risk"] == "none"
    assert _decision_from_report(tmp_path, report) == "SHIP-FUNCTIONAL"


def test_missing_report_reads_none_for_every_value(tmp_path):
    values = _report_values(tmp_path, tmp_path / "does-not-exist.md")
    assert values == {
        "recommendation": "none",
        "recommendation_raw": "none",
        "systemic_risk": "none",
        "systemic_risk_raw": "none",
        "has_large_fix_effort": "no",
    }


@pytest.mark.parametrize(
    "args, expected, reason",
    [
        (("done", "low", "unparseable", "no", "yes", "no", "none"), "SHIP-KNOWN-BUGS",
         "an unparseable recommendation takes the SHIP-WITH-CONCERNS row"),
        (("done", "low", "none", "no", "yes", "no", "none"), "SHIP-KNOWN-BUGS",
         "an absent recommendation on a DONE task takes the SHIP-WITH-CONCERNS row"),
        (("done", "low", "shipp-with-concernz", "no", "yes", "no", "none"), "SHIP-KNOWN-BUGS",
         "a garbled recommendation passed straight in still never ships FUNCTIONAL"),
        (("done", "unparseable", "pass", "no", "yes", "no", "none"), "SHIP-KNOWN-BUGS",
         "an unparseable systemic risk floors a PASS to the SHIP-WITH-CONCERNS row"),
        (("done", "unparseable", "ship-with-serious-concerns", "no", "yes", "no", "none"), "SHIP-NON-FUNCTIONAL",
         "the floor never loosens a stricter recommendation"),
        (("done", "none", "pass", "no", "yes", "no", "none"), "SHIP-FUNCTIONAL",
         "systemic risk none reads as not-high, not as unreadable"),
    ],
)
def test_matrix_never_ships_functional_on_an_unreadable_value(tmp_path, args, expected, reason):
    result = run_bash(tmp_path, _ship_policy(*args))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected, reason


# --- has_large_fix_effort: anchored to each finding block's own field line ---


def _small_finding(n: int, extra_lines: str = "") -> str:
    return (
        f"### Finding {n}: a small finding\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** low\n"
        f"{extra_lines}\n"
    )


def test_negation_in_prose_never_sets_large_fix_effort(tmp_path):
    """The reproduction shape: a SHIP-WITH-SERIOUS-CONCERNS report whose
    findings are all small and whose prose says the Fix Effort is not
    large ships NON-FUNCTIONAL, never HALT."""
    report = _write_report(
        tmp_path,
        _report_with_values(
            "SHIP-WITH-SERIOUS-CONCERNS",
            "medium",
            "Overall the Fix Effort is not large for this bundle.\n\n"
            + _small_finding(1, "The `Fix Effort: large` clearing rule was reviewed and does not apply here.\n"),
        ),
    )
    assert _report_values(tmp_path, report)["has_large_fix_effort"] == "no"
    assert _decision_from_report(tmp_path, report) == "SHIP-NON-FUNCTIONAL"


@pytest.mark.parametrize(
    "stray_text",
    [
        "Fix Effort: small | medium | large\n",
        "  Fix Effort: small / medium / large\n",
        "**Fix Effort:** larger than usual but still small\n",
        "the Fix Effort is not large\n",
        "fix_effort=large was the old shape\n",
    ],
)
def test_legend_larger_and_prose_never_set_large_fix_effort(tmp_path, stray_text):
    report = _write_report(
        tmp_path,
        _report_with_values("SHIP-WITH-SERIOUS-CONCERNS", "medium", stray_text + "\n" + _small_finding(1)),
    )
    assert _report_values(tmp_path, report)["has_large_fix_effort"] == "no"


@pytest.mark.parametrize(
    "large_line",
    ["**Fix Effort:** large (architectural)", "Fix Effort: large", "**Fix Effort**: LARGE", "  Fix Effort: large."],
)
def test_a_finding_whose_own_line_reads_large_still_sets_the_flag(tmp_path, large_line):
    report = _write_report(
        tmp_path,
        _report_with_values(
            "SHIP-WITH-SERIOUS-CONCERNS",
            "medium",
            _small_finding(1)
            + f"### Finding 2: the big one\n\n**Category:** bug\n{large_line}\n**Systemic Risk:** low\n\n",
        ),
    )
    assert _report_values(tmp_path, report)["has_large_fix_effort"] == "yes"
    assert _decision_from_report(tmp_path, report) == "HALT"


def test_large_fix_effort_outside_any_finding_block_is_not_read(tmp_path):
    """Only a finding block's own line counts: the same line in the report
    prose above the first finding heading is out of scope."""
    report = _write_report(
        tmp_path,
        _report_with_values("SHIP-WITH-SERIOUS-CONCERNS", "medium", "**Fix Effort:** large\n\n" + _small_finding(1)),
    )
    assert _report_values(tmp_path, report)["has_large_fix_effort"] == "no"


# ---------------------------------------------------------------------------
# read_ship_policy_tester_lookup: normalized matching and mismatch reporting.
# ---------------------------------------------------------------------------


def _make_tester_task(
    tasks_dir: pathlib.Path,
    task_id: str,
    *,
    role: str = "TESTER",
    version: str = "v1.2.3",
    state: str = "DONE",
    with_report: bool = True,
) -> pathlib.Path:
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n## Role\n{role}\n\n## Release Version\n{version}\n", encoding="utf-8"
    )
    (task_dir / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")
    if with_report:
        (task_dir / "artifacts").mkdir()
        (task_dir / "artifacts" / "report.md").write_text("## Recommendation\nPASS\n", encoding="utf-8")
    return task_dir


def _lookup(tmp_path: pathlib.Path, tasks_dir: pathlib.Path, version: str = "v1.2.3"):
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_tester_lookup '{tasks_dir}' '{version}'",
    )
    assert result.returncode == 0, result.stderr
    values: dict = {}
    mismatches = []
    pending: dict = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key.startswith("mismatch_"):
            pending[key] = value
            if key == "mismatch_path":
                mismatches.append((pending["mismatch_field"], pending["mismatch_raw"], pending["mismatch_path"]))
                pending = {}
        else:
            values[key] = value
    return values, mismatches, result.stderr


def test_exact_role_and_version_are_found_with_no_mismatch(tmp_path):
    tasks = tmp_path / "tasks"
    _make_tester_task(tasks, "TESTER-20260601-001-verify")
    values, mismatches, stderr = _lookup(tmp_path, tasks)
    assert values["tester_task_found"] == "yes"
    assert values["tester_task_id"] == "TESTER-20260601-001-verify"
    assert values["tester_state"] == "DONE"
    assert values["tester_report_path"].endswith("artifacts/report.md")
    assert mismatches == []
    assert stderr.strip() == ""


@pytest.mark.parametrize(
    "role, version, expected_fields",
    [
        ("**TESTER**", "v1.2.3", ["Role"]),
        ("tester", "v1.2.3", ["Role"]),
        ("TESTER", "`v1.2.3`", ["Release Version"]),
        ("TESTER", "v1.2.3 (bugfix bundle)", ["Release Version"]),
        ("TESTER", "1.2.3", ["Release Version"]),
        ("`Tester`", "**v1.2.3**", ["Role", "Release Version"]),
    ],
)
def test_mismatched_role_or_version_is_still_found_and_reported(tmp_path, role, version, expected_fields):
    """A lookup field that matches only after normalization never turns the
    RC into an untested one: the task is found, the mismatch is narrated on
    stderr naming the README path, the field and its raw text, and one
    mismatch triple per field is printed for the caller to file from."""
    tasks = tmp_path / "tasks"
    task_dir = _make_tester_task(tasks, "TESTER-20260601-001-verify", role=role, version=version)
    values, mismatches, stderr = _lookup(tmp_path, tasks)
    assert values["tester_task_found"] == "yes", "a normalizable mismatch must not select the test-not-required row"
    assert values["tester_task_id"] == "TESTER-20260601-001-verify"
    assert [m[0] for m in mismatches] == expected_fields
    for field, raw, path in mismatches:
        assert path == str(task_dir / "README.md")
        assert raw == (role if field == "Role" else version)
        assert field in stderr and raw in stderr and str(task_dir / "README.md") in stderr


def test_a_task_for_another_version_is_neither_found_nor_a_mismatch(tmp_path):
    tasks = tmp_path / "tasks"
    _make_tester_task(tasks, "TESTER-20260601-001-verify", version="v1.2.2")
    values, mismatches, stderr = _lookup(tmp_path, tasks)
    assert values["tester_task_found"] == "no"
    assert values["tester_task_id"] == "none"
    assert mismatches == []
    assert stderr.strip() == ""


def test_a_non_version_shaped_release_version_is_reported_and_never_selected(tmp_path):
    tasks = tmp_path / "tasks"
    task_dir = _make_tester_task(tasks, "TESTER-20260601-001-verify", version="v1.2.x")
    values, mismatches, stderr = _lookup(tmp_path, tasks)
    assert values["tester_task_found"] == "no"
    assert mismatches == [("Release Version", "v1.2.x", str(task_dir / "README.md"))]
    assert "not version-shaped" in stderr


def test_release_version_none_is_a_task_for_no_rc_and_skipped_silently(tmp_path):
    tasks = tmp_path / "tasks"
    _make_tester_task(tasks, "TESTER-20260601-001-doc-check", version="none")
    values, mismatches, stderr = _lookup(tmp_path, tasks)
    assert values["tester_task_found"] == "no"
    assert mismatches == []
    assert stderr.strip() == ""


def test_most_recent_done_candidate_wins_over_a_later_non_done_one(tmp_path):
    """The original selection rule, unchanged: a DONE task beats any
    non-DONE task for the same version, and among equals the most recently
    created (last in folder-name order) wins."""
    tasks = tmp_path / "tasks"
    _make_tester_task(tasks, "TESTER-20260601-001-first", state="DONE")
    _make_tester_task(tasks, "TESTER-20260602-002-second", state="DONE")
    _make_tester_task(tasks, "TESTER-20260603-003-third", state="WORKING")
    values, _, _ = _lookup(tmp_path, tasks)
    assert values["tester_task_id"] == "TESTER-20260602-002-second"
    assert values["tester_state"] == "DONE"


def test_without_any_done_candidate_the_most_recent_one_wins(tmp_path):
    tasks = tmp_path / "tasks"
    _make_tester_task(tasks, "TESTER-20260601-001-first", state="BLOCKED", with_report=False)
    _make_tester_task(tasks, "TESTER-20260602-002-second", state="WORKING", with_report=False)
    values, _, _ = _lookup(tmp_path, tasks)
    assert values["tester_task_id"] == "TESTER-20260602-002-second"
    assert values["tester_state"] == "WORKING"
    assert values["tester_report_path"] == ""


def test_missing_tasks_dir_reads_not_found(tmp_path):
    values, mismatches, _ = _lookup(tmp_path, tmp_path / "no-such-dir")
    assert values["tester_task_found"] == "no"
    assert values["tester_task_id"] == "none"
    assert mismatches == []
