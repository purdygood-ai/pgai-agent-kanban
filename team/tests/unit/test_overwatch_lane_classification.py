"""
test_overwatch_lane_classification.py
========================================
Unit tests for the OVERWATCH three-lane block classifier:
pgai_agent_kanban.overwatch.resolution_matrix.classify_lane() (the pure
matching layer) and its wiring into
pgai_agent_kanban.overwatch.resolution_stage.run_resolution_stage() (the
Lane C escalation path and the shared OVERWATCH Decision event writer).

Coverage:
  - classify_lane(): a fixture whose finding carries Regression=yes and
    traces to a bug this RC's own bundle fixed classifies Lane A.
  - classify_lane(): a fixture whose evidence is fully explained by a
    declared INTENT.md entry classifies Lane B; the identical evidence
    against a thematically-similar-but-non-reproducing declaration, and
    against an empty declaration list, both fail closed to Lane C.
  - run_resolution_stage() wiring: with overwatch_resolve unset/false, a
    HALT-hard block leaves task state, release-state.md, and the HALT file
    byte-identical to the pre-v2.23.0 stage (no Decision event written).
  - run_resolution_stage() wiring: with overwatch_resolve true, a Lane A,
    Lane B, and Lane C block each append exactly one "## OVERWATCH
    Decision" event to release-state.md carrying the lane, evidence
    references, verbs run, and outcome; the Lane C event's outcome carries
    a one-paragraph read naming both the cause and an option; a second
    firing against the same unresolved block appends a second event
    without altering the first.

None of these fixtures need a real git repository: every scenario here
already fails the pre-existing CANCEL-AND-REBUNDLE matrix row on
operator_commits_in_lineage (BlockEvidence.dev_tree_path is never set, so
extract_facts() fails that fact closed to True per its own documented
default) before the three-lane classifier is ever consulted — the git-
backed evidence path itself is covered separately by
team/tests/unit/test_resolution_matrix.py and the integration suite.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from pgai_agent_kanban.overwatch.resolution_matrix import (
    LANE_A,
    LANE_B,
    LANE_C,
    cargo_bug_ids,
    classify_lane,
    extract_identifiers,
    find_lane_b_declaration,
    find_regression_source_finding,
    intent_entry_fully_explains,
    matrix_findings,
)
from pgai_agent_kanban.overwatch.resolution_stage import DECLINED, EXECUTED, run_resolution_stage
from pgai_agent_kanban.reports.tester_report import parse_report

# ---------------------------------------------------------------------------
# classify_lane() — pure function fixtures
# ---------------------------------------------------------------------------

_LANE_A_REPORT = (
    "# TESTER Report\n\n"
    "### Finding 1: dashboard column count regression\n"
    "**Category:** bug\n"
    "**Systemic Risk:** high\n"
    "**Regression:** yes\n"
    "**Integrity:** none\n"
    "**Filed As:** BUG-0099-dashboard-column (Path C)\n\n"
    "Traces to BUG-0042, whose fix in this RC inverted the column check.\n"
)


def test_regression_finding_tracing_to_bundled_bug_classifies_lane_a() -> None:
    findings = matrix_findings(parse_report(_LANE_A_REPORT))
    cargo = cargo_bug_ids(["BUG-0042-inverted-column.md", "BUG-0007-unrelated.md"])

    result = classify_lane(findings, cargo, intent_declarations=[], evidence_texts=[])

    assert result.lane == LANE_A
    assert result.regression_bug_id == "BUG-0042"
    assert "BUG-0042" in result.reason


def test_regression_finding_not_tracing_to_bundled_bug_is_not_lane_a() -> None:
    """Regression=yes alone is not enough — the cited bug must be one THIS
    RC's own bundle fixed, not merely any bug the finding happens to name.
    """
    findings = matrix_findings(parse_report(_LANE_A_REPORT))
    cargo = cargo_bug_ids(["BUG-0007-unrelated.md"])  # BUG-0042 absent

    match = find_regression_source_finding(findings, cargo)

    assert match is None


def test_non_regression_finding_never_matches_lane_a_even_with_matching_bug_id() -> None:
    report = _LANE_A_REPORT.replace("**Regression:** yes", "**Regression:** no")
    findings = matrix_findings(parse_report(report))
    cargo = cargo_bug_ids(["BUG-0042-inverted-column.md"])

    result = classify_lane(findings, cargo, intent_declarations=[], evidence_texts=[])

    assert result.lane != LANE_A


_LANE_B_DECLARATION = (
    "2026-07-01: Deliberately re-opened ai_rc/v2.0.1 off ai_main for a "
    "hindsight replay of v2.0.1; expected to HALT, ship via operator "
    "review only."
)


def test_evidence_fully_explained_by_intent_entry_classifies_lane_b() -> None:
    evidence_texts = ["v2.0.1", "ai_rc/v2.0.1", "ai_main"]

    result = classify_lane(
        findings=[],
        cargo_ids=set(),
        intent_declarations=[_LANE_B_DECLARATION],
        evidence_texts=evidence_texts,
    )

    assert result.lane == LANE_B
    assert result.intent_declaration == _LANE_B_DECLARATION


def test_same_evidence_with_emptied_intent_ledger_classifies_lane_c() -> None:
    evidence_texts = ["v2.0.1", "ai_rc/v2.0.1", "ai_main"]

    result = classify_lane(
        findings=[],
        cargo_ids=set(),
        intent_declarations=[],
        evidence_texts=evidence_texts,
    )

    assert result.lane == LANE_C


def test_thematic_resemblance_without_verbatim_identifiers_is_not_a_match() -> None:
    """The Lane B match test is full explanation, never a keyword or
    thematic match: a declaration that reads as describing the same kind
    of event, without reproducing the block's actual identifiers verbatim,
    must not match."""
    declaration = "2026-07-01: we rebased things this week for a hindsight experiment."
    evidence_texts = ["v2.0.1", "ai_rc/v2.0.1", "ai_main"]

    assert find_lane_b_declaration(extract_identifiers(*evidence_texts), [declaration]) is None
    result = classify_lane([], set(), [declaration], evidence_texts)
    assert result.lane == LANE_C


def test_intent_entry_fully_explains_requires_every_identifier_present() -> None:
    assert intent_entry_fully_explains("names v2.0.1 only", ["v2.0.1", "ai_main"]) is False
    assert intent_entry_fully_explains("names v2.0.1 and ai_main both", ["v2.0.1", "ai_main"]) is True


def test_intent_entry_fully_explains_fails_closed_on_no_identifiers() -> None:
    """An empty identifier list can never "fully explain" anything — a
    declaration does not vacuously match a block with no extractable
    evidence."""
    assert intent_entry_fully_explains("any declaration text at all", []) is False


def test_block_matching_neither_lane_classifies_lane_c_by_default() -> None:
    result = classify_lane([], set(), [], [])
    assert result.lane == LANE_C
    assert result.regression_finding is None
    assert result.intent_declaration is None


# ---------------------------------------------------------------------------
# run_resolution_stage() wiring — fixture builders
# ---------------------------------------------------------------------------


def _write_release_state_md(proj: pathlib.Path, active_rc: str) -> str:
    text = (
        "# Release State\n\n"
        f"## Active RC\n{active_rc}\n\n"
        "## RC Opened At\n2026-07-27T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-20260727-001-open\n\n"
        "## Last Released\nv0.0.0\n"
    )
    (proj / "release-state.md").write_text(text, encoding="utf-8")
    return text


def _write_ship_policy_halt(path: pathlib.Path) -> str:
    text = (
        "# HALT created by CM at 2026-07-27T00:00:00Z\n"
        "# Reason: TESTER systemic_risk=high in ai_rc/v2.0.1 (TESTER-20260727-001)\n"
        "# Resolution: Operator review required. See CM task status.md for full reason.\n"
    )
    path.write_text(text, encoding="utf-8")
    return text


def _write_tester_report(tasks_dir: pathlib.Path, version: str, body: str) -> None:
    task_dir = tasks_dir / "TESTER-20260727-001-verify"
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task\n\n## Role\nTESTER\n\n## Release Version\n{version}\n", encoding="utf-8"
    )
    (task_dir / "status.md").write_text("## State\nDONE\n", encoding="utf-8")
    (task_dir / "artifacts" / "report.md").write_text(body, encoding="utf-8")


def _write_bundle(requirements_dir: pathlib.Path, version: str, bug_basenames: list) -> None:
    lines = ["# Requirements bundle\n\n", "## Bundled Items\n\n"]
    for name in bug_basenames:
        lines.append(f"- {name} (`/kanban/projects/p/bugs/{name}`)\n")
    (requirements_dir / f"{version}-bugfix-bundle-20260727.md").write_text("".join(lines), encoding="utf-8")


def _write_rewritten_bug(bugs_dir: pathlib.Path, basename: str) -> None:
    """Write a cargo bug file that already carries an attributed OVERWATCH
    Fix rewrite — the precondition
    pgai_agent_kanban.overwatch.resolution_stage._run_lane_a()'s
    rewrite-precondition guard checks before running steps (b)-(e). This
    fixture tier drives run_resolution_stage() directly with no scripts/
    tree on disk (module docstring), so the rewrite is written here rather
    than through the real CLI — the real, subprocess-driven call path is
    covered by team/tests/integration/test_overwatch_lane_a_drill.py.
    """
    (bugs_dir / basename).write_text(
        "# Bug\n\n## Status\nassigned\n\n## Fix\nOriginal (ambiguous) fix direction.\n\n"
        "---\n\n## Fix (OVERWATCH rewrite)\n**Attributed To:** OVERWATCH\n"
        "**Date:** 2026-07-27T00:05:00Z\n\n"
        "**Forbidden approach — do not repeat:** inverting the column check.\n\n"
        "**Correct direction:** compare using the original direction.\n\n"
        "**Ground truth (verbatim — sourced from the record, not paraphrased):**\n"
        "- commit 0000000: test fixture ground truth\n",
        encoding="utf-8",
    )


def _build_fixture(
    tmp_path: pathlib.Path,
    project_name: str,
    *,
    overwatch_resolve: "str | None" = None,
    tester_report_body: str = "# TESTER Report\n\nNone.\n",
    cargo_bugs: "list | None" = None,
    intent_entries: "list | None" = None,
) -> dict:
    """Build a minimal kanban root/project laid out just enough to drive
    run_resolution_stage() to the three-lane classifier without needing a
    real git repository — see module docstring for why that is safe here.
    """
    version = "v2.0.1"
    root = tmp_path / "kanban_root"
    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[release]\nmax_rc_retries = 3\n", encoding="utf-8"
    )

    project_cfg_lines = [
        "[project]\n",
        f"project_name = {project_name}\n",
        "branch_prefix = ai_\n",
        "\n[release]\n",
    ]
    if overwatch_resolve is not None:
        project_cfg_lines.append(f"overwatch_resolve = {overwatch_resolve}\n")
    (proj / "project.cfg").write_text("".join(project_cfg_lines), encoding="utf-8")

    (proj / "requirements").mkdir(parents=True, exist_ok=True)
    (proj / "bugs").mkdir(parents=True, exist_ok=True)
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    (proj / "release-state").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    release_state_text = _write_release_state_md(proj, version)
    halt_path = proj / "HALT"
    halt_text = _write_ship_policy_halt(halt_path)

    (proj / "release-state" / f"{version}.json").write_text(
        json.dumps({"rc": version, "attempt": 1, "outcome": "in_progress", "family_key": None}),
        encoding="utf-8",
    )

    _write_tester_report(proj / "tasks", version, tester_report_body)

    if cargo_bugs:
        _write_bundle(proj / "requirements", version, cargo_bugs)
        # Each cargo bug already carries an attributed OVERWATCH Fix
        # rewrite, satisfying _run_lane_a()'s rewrite-precondition guard —
        # see _write_rewritten_bug()'s own docstring for why this fixture
        # tier writes it directly rather than through the real CLI.
        for basename in cargo_bugs:
            _write_rewritten_bug(proj / "bugs", basename)

    if intent_entries:
        entry_lines = "\n".join(f"- {line}" for line in intent_entries)
        (proj / "INTENT.md").write_text(f"## Entries\n\n{entry_lines}\n", encoding="utf-8")

    return {
        "kanban_root": root,
        "proj": proj,
        "version": version,
        "halt_path": halt_path,
        "halt_text": halt_text,
        "release_state_text": release_state_text,
    }


def _decision_blocks(release_state_text: str) -> list:
    return [
        block for block in release_state_text.split("## OVERWATCH Decision")[1:]
    ]


# ---------------------------------------------------------------------------
# Flag-off pass-through (Acceptance Criterion 4)
# ---------------------------------------------------------------------------


class TestFlagOffPassThrough:
    def test_overwatch_resolve_unset_leaves_state_byte_identical(self, tmp_path: pathlib.Path) -> None:
        fx = _build_fixture(tmp_path, "proj_flag_absent")

        outcome = run_resolution_stage("proj_flag_absent", str(fx["kanban_root"]))

        assert outcome.status == DECLINED
        assert (fx["proj"] / "release-state.md").read_text(encoding="utf-8") == fx["release_state_text"]
        assert fx["halt_path"].read_text(encoding="utf-8") == fx["halt_text"]
        assert "## OVERWATCH Decision" not in (fx["proj"] / "release-state.md").read_text(encoding="utf-8")

    def test_overwatch_resolve_false_leaves_state_byte_identical(self, tmp_path: pathlib.Path) -> None:
        fx = _build_fixture(tmp_path, "proj_flag_false", overwatch_resolve="false")

        outcome = run_resolution_stage("proj_flag_false", str(fx["kanban_root"]))

        assert outcome.status == DECLINED
        assert (fx["proj"] / "release-state.md").read_text(encoding="utf-8") == fx["release_state_text"]
        assert fx["halt_path"].read_text(encoding="utf-8") == fx["halt_text"]


# ---------------------------------------------------------------------------
# Lane wiring through run_resolution_stage (flag on)
# ---------------------------------------------------------------------------


class TestLaneWiringWithFlagOn:
    def test_lane_a_fixture_writes_one_decision_event(self, tmp_path: pathlib.Path) -> None:
        """This fixture deliberately carries no scripts/ tree (module
        docstring: "None of these fixtures need a real git repository") —
        the real, full-drill-through-real-subprocesses path is covered by
        team/tests/integration/test_overwatch_lane_a_drill.py. Here,
        unwind-rc.sh is simply absent on disk, so the drill's first RC step
        fails and the drill stops there: cancel-rc, the bundle-doc
        deletion and the ship-policy HALT clear are not run, and the
        bundle doc and the HALT are both left in place. The outcome is
        still EXECUTED with one Lane A Decision event — proving the wiring
        reaches _run_lane_a() and executes rather than deferring."""
        fx = _build_fixture(
            tmp_path,
            "proj_lane_a",
            overwatch_resolve="true",
            tester_report_body=_LANE_A_REPORT,
            cargo_bugs=["BUG-0042-inverted-column.md"],
        )

        outcome = run_resolution_stage("proj_lane_a", str(fx["kanban_root"]))

        assert outcome.status == EXECUTED, outcome.reason
        text = (fx["proj"] / "release-state.md").read_text(encoding="utf-8")
        blocks = _decision_blocks(text)
        assert len(blocks) == 1
        assert "Lane: A" in blocks[0]
        assert "BUG-0042" in blocks[0]
        # Prior release-state.md fields untouched (append-only) — the
        # Active RC field itself is only ever changed by the operator
        # verbs (unwind-rc.sh/cancel-rc.sh), never by this Decision event.
        assert f"## Active RC\n{fx['version']}" in text
        # The drill stopped at the failed RC unwind, so nothing after it
        # ran: the ship-policy HALT keeps the RC from shipping, byte for
        # byte as written, and the bundle doc is still on disk.
        assert fx["halt_path"].is_file()
        assert fx["halt_path"].read_text(encoding="utf-8") == fx["halt_text"]
        assert len(list((fx["proj"] / "requirements").glob(f"{fx['version']}-*.md"))) == 1

    def test_lane_b_fixture_writes_one_decision_event(self, tmp_path: pathlib.Path) -> None:
        fx = _build_fixture(
            tmp_path,
            "proj_lane_b",
            overwatch_resolve="true",
            intent_entries=[_LANE_B_DECLARATION],
        )

        outcome = run_resolution_stage("proj_lane_b", str(fx["kanban_root"]))

        assert outcome.status == DECLINED
        text = (fx["proj"] / "release-state.md").read_text(encoding="utf-8")
        blocks = _decision_blocks(text)
        assert len(blocks) == 1
        assert "Lane: B" in blocks[0]
        assert fx["halt_path"].is_file()

    def test_same_block_with_no_intent_entries_writes_lane_c_event_with_oriented_read(
        self, tmp_path: pathlib.Path
    ) -> None:
        fx = _build_fixture(tmp_path, "proj_lane_c", overwatch_resolve="true")

        outcome = run_resolution_stage("proj_lane_c", str(fx["kanban_root"]))

        assert outcome.status == DECLINED
        text = (fx["proj"] / "release-state.md").read_text(encoding="utf-8")
        blocks = _decision_blocks(text)
        assert len(blocks) == 1
        assert "Lane: C" in blocks[0]
        # The one-paragraph read names a cause and at least one option.
        assert "OVERWATCH read" in blocks[0]
        assert "Options:" in blocks[0]
        assert "Needs Human raised" in blocks[0]
        assert fx["halt_path"].is_file()

    def test_second_firing_appends_a_second_event_without_altering_the_first(
        self, tmp_path: pathlib.Path
    ) -> None:
        fx = _build_fixture(tmp_path, "proj_lane_c_twice", overwatch_resolve="true")

        run_resolution_stage("proj_lane_c_twice", str(fx["kanban_root"]))
        text_after_first = (fx["proj"] / "release-state.md").read_text(encoding="utf-8")
        first_block = _decision_blocks(text_after_first)[0].rstrip("\n")

        run_resolution_stage("proj_lane_c_twice", str(fx["kanban_root"]))
        text_after_second = (fx["proj"] / "release-state.md").read_text(encoding="utf-8")
        blocks_after_second = _decision_blocks(text_after_second)

        assert len(blocks_after_second) == 2
        assert blocks_after_second[0].rstrip("\n") == first_block
