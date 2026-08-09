"""
test_resolution_matrix.py
==========================
Unit tests for pgai_agent_kanban.overwatch.resolution_matrix — the v2.0.0
Goal 1 ("the resolution matrix") config-declared matrix and fail-closed
matcher.

Coverage (mirrors the task's Acceptance Criteria):
  - The shipped default matrix loads exactly one CANCEL-AND-REBUNDLE row
    from config, not a hardcoded value.
  - The 2026-07-18 golden fixture facts match CANCEL-AND-REBUNDLE.
  - Any single fact off (each of the six, one at a time) yields HALT-hard.
  - Unknown/unmatched shapes (unrecognized trigger_class, empty matrix)
    fail closed to HALT-hard.
  - Matrix structural validation: missing/extra row keys, wrong types, the
    reserved HALT-hard resolution literal, malformed top-level YAML.
  - Fact extraction: TESTER report finding parsing, the retry-ceiling
    Python counterpart of pp_max_rc_retries, and the git-backed evidence
    helpers (RC lineage cleanliness, tag existence) against real temp git
    repos.
  - End-to-end resolve_block() against a full BlockEvidence fixture,
    including the fail-closed defaults when evidence is simply absent.

All tests use tmp_path; no live filesystem paths. Function names describe
behaviour under test, never bug/task IDs (SOP.md "Test Authoring
Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.overwatch.resolution_matrix import (
    CANCEL_AND_REBUNDLE,
    HALT_HARD,
    BlockEvidence,
    BlockFacts,
    CeilingConfigError,
    EvidenceError,
    MatrixError,
    MatrixRow,
    extract_facts,
    findings_all_filed,
    git_tag_exists,
    load_matrix,
    match_resolution,
    operator_commit_in_rc_lineage,
    parse_tester_report_findings,
    resolve_block,
    resolve_max_rc_retries,
)

# ---------------------------------------------------------------------------
# Golden facts (mirrors the 2026-07-18 golden fixture: ship-policy HALT, all
# findings filed, nothing shipped, no approval gate, clean lineage, attempts
# below ceiling).
# ---------------------------------------------------------------------------


def _golden_facts(**overrides) -> BlockFacts:
    base = dict(
        trigger_class="ship-policy-halt",
        findings_all_filed=True,
        shipped_or_tagged=False,
        approval_gate_present=False,
        operator_commits_in_lineage=False,
        attempts_below_ceiling=True,
        attempt=1,
        ceiling=3,
    )
    base.update(overrides)
    return BlockFacts(**base)


def _shipped_matrix() -> list:
    from pgai_agent_kanban.overwatch.resolution_matrix import load_matrix as _lm

    return _lm()


# ---------------------------------------------------------------------------
# load_matrix — shipped default
# ---------------------------------------------------------------------------


def test_shipped_default_matrix_has_exactly_one_cancel_and_rebundle_row() -> None:
    """The shipped v2.0.0 default matrix ships exactly one row, and it is
    the CANCEL-AND-REBUNDLE golden row — not hardcoded in Python."""
    matrix = load_matrix()
    assert len(matrix) == 1
    row = matrix[0]
    assert isinstance(row, MatrixRow)
    assert row.resolution == CANCEL_AND_REBUNDLE
    assert row.trigger_class == "ship-policy-halt"
    assert row.findings_all_filed is True
    assert row.shipped_or_tagged is False
    assert row.approval_gate_present is False
    assert row.operator_commits_in_lineage is False
    assert row.attempts_below_ceiling is True


def test_kanban_root_operator_override_replaces_shipped_default(tmp_path: pathlib.Path) -> None:
    """An operator override at <kanban_root>/resolution-matrix.yaml wins over
    the shipped default when kanban_root is supplied."""
    override = tmp_path / "resolution-matrix.yaml"
    override.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: true\n"
        "    shipped_or_tagged: false\n"
        "    approval_gate_present: false\n"
        "    operator_commits_in_lineage: false\n"
        "    attempts_below_ceiling: true\n"
        "    resolution: OPERATOR-CUSTOM-ROW\n",
        encoding="utf-8",
    )
    matrix = load_matrix(kanban_root=str(tmp_path))
    assert len(matrix) == 1
    assert matrix[0].resolution == "OPERATOR-CUSTOM-ROW"


def test_explicit_matrix_path_wins_over_kanban_root_override(tmp_path: pathlib.Path) -> None:
    """An explicit matrix_path argument takes precedence over kanban_root."""
    (tmp_path / "resolution-matrix.yaml").write_text(
        "rows: []\n", encoding="utf-8"
    )
    explicit = tmp_path / "explicit-matrix.yaml"
    explicit.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: true\n"
        "    shipped_or_tagged: false\n"
        "    approval_gate_present: false\n"
        "    operator_commits_in_lineage: false\n"
        "    attempts_below_ceiling: true\n"
        "    resolution: EXPLICIT-ROW\n",
        encoding="utf-8",
    )
    matrix = load_matrix(matrix_path=str(explicit), kanban_root=str(tmp_path))
    assert len(matrix) == 1
    assert matrix[0].resolution == "EXPLICIT-ROW"


def test_empty_rows_list_is_a_valid_fully_fail_closed_matrix(tmp_path: pathlib.Path) -> None:
    """An empty 'rows' list is structurally valid — every block then
    resolves to HALT-hard, which is a legitimate fail-closed configuration."""
    matrix_file = tmp_path / "empty-matrix.yaml"
    matrix_file.write_text("rows: []\n", encoding="utf-8")
    matrix = load_matrix(matrix_path=str(matrix_file))
    assert matrix == []
    assert match_resolution(_golden_facts(), matrix) == HALT_HARD


# ---------------------------------------------------------------------------
# load_matrix — structural validation failures
# ---------------------------------------------------------------------------


def test_missing_matrix_file_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    with pytest.raises(MatrixError, match="Cannot read"):
        load_matrix(matrix_path=str(tmp_path / "does-not-exist.yaml"))


def test_non_mapping_top_level_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(MatrixError, match="must be a YAML mapping"):
        load_matrix(matrix_path=str(matrix_file))


def test_missing_rows_key_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text("not_rows: []\n", encoding="utf-8")
    with pytest.raises(MatrixError, match="missing the required 'rows'"):
        load_matrix(matrix_path=str(matrix_file))


def test_row_missing_required_key_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: true\n"
        "    resolution: X\n",
        encoding="utf-8",
    )
    with pytest.raises(MatrixError, match="missing required key"):
        load_matrix(matrix_path=str(matrix_file))


def test_row_with_extra_key_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: true\n"
        "    shipped_or_tagged: false\n"
        "    approval_gate_present: false\n"
        "    operator_commits_in_lineage: false\n"
        "    attempts_below_ceiling: true\n"
        "    resolution: X\n"
        "    extra_field: oops\n",
        encoding="utf-8",
    )
    with pytest.raises(MatrixError, match="unrecognized key"):
        load_matrix(matrix_path=str(matrix_file))


def test_row_with_non_boolean_fact_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: \"yes\"\n"
        "    shipped_or_tagged: false\n"
        "    approval_gate_present: false\n"
        "    operator_commits_in_lineage: false\n"
        "    attempts_below_ceiling: true\n"
        "    resolution: X\n",
        encoding="utf-8",
    )
    with pytest.raises(MatrixError, match="must be a boolean"):
        load_matrix(matrix_path=str(matrix_file))


def test_row_resolution_cannot_be_the_halt_hard_sentinel(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: true\n"
        "    shipped_or_tagged: false\n"
        "    approval_gate_present: false\n"
        "    operator_commits_in_lineage: false\n"
        "    attempts_below_ceiling: true\n"
        "    resolution: HALT-hard\n",
        encoding="utf-8",
    )
    with pytest.raises(MatrixError, match="reserved no-match sentinel"):
        load_matrix(matrix_path=str(matrix_file))


# ---------------------------------------------------------------------------
# match_resolution — golden fixture and single-fact-off fixtures
# ---------------------------------------------------------------------------


def test_golden_facts_match_cancel_and_rebundle() -> None:
    matrix = _shipped_matrix()
    assert match_resolution(_golden_facts(), matrix) == CANCEL_AND_REBUNDLE


@pytest.mark.parametrize(
    "override",
    [
        {"trigger_class": "operator-halt"},
        {"findings_all_filed": False},
        {"shipped_or_tagged": True},
        {"approval_gate_present": True},
        {"operator_commits_in_lineage": True},
        {"attempts_below_ceiling": False},
    ],
)
def test_any_single_fact_off_yields_halt_hard(override: dict) -> None:
    matrix = _shipped_matrix()
    facts = _golden_facts(**override)
    assert match_resolution(facts, matrix) == HALT_HARD


def test_unrecognized_trigger_class_is_an_unknown_shape_and_fails_closed() -> None:
    matrix = _shipped_matrix()
    facts = _golden_facts(trigger_class="some-new-trigger-nobody-declared")
    assert match_resolution(facts, matrix) == HALT_HARD


# ---------------------------------------------------------------------------
# TESTER report finding parsing / findings_all_filed
# ---------------------------------------------------------------------------


_REPORT_ALL_FILED = """\
# Verification Report: rc/v2.0.1

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

### Finding 1: stale assertion in dashboard test

**Category:** stale-assertion
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Filed As:** BUG-0042 (Path C)

Detail text here.

### Finding 2: missing guard fixture

**Category:** bug
**Fix Effort:** medium
**Systemic Risk:** low
**Filed As:** BUG-0043 (Path C)

More detail.
"""

_REPORT_ONE_UNFILED = _REPORT_ALL_FILED.replace("**Filed As:** BUG-0043 (Path C)", "**Filed As:** N/A")

_REPORT_NO_FINDINGS = """\
# Verification Report: rc/v2.0.1

## Recommendation
PASS

## Systemic Risk
low

## Findings

None.
"""


def test_parse_tester_report_findings_extracts_filed_as_values() -> None:
    findings = parse_tester_report_findings(_REPORT_ALL_FILED)
    assert len(findings) == 2
    assert findings[0]["filed_as"] == "BUG-0042 (Path C)"
    assert findings[1]["filed_as"] == "BUG-0043 (Path C)"


def test_findings_all_filed_true_when_every_finding_has_a_bug_id() -> None:
    findings = parse_tester_report_findings(_REPORT_ALL_FILED)
    assert findings_all_filed(findings) is True


def test_findings_all_filed_false_when_one_finding_unfiled() -> None:
    findings = parse_tester_report_findings(_REPORT_ONE_UNFILED)
    assert findings_all_filed(findings) is False


def test_findings_all_filed_vacuously_true_for_no_findings() -> None:
    findings = parse_tester_report_findings(_REPORT_NO_FINDINGS)
    assert findings == []
    assert findings_all_filed(findings) is True


@pytest.mark.parametrize("sentinel", ["", "N/A", "none", "-", "TBD", "not filed"])
def test_findings_all_filed_recognizes_not_filed_sentinels(sentinel: str) -> None:
    assert findings_all_filed([{"filed_as": sentinel}]) is False


# ---------------------------------------------------------------------------
# resolve_max_rc_retries — Python counterpart of pp_max_rc_retries
# ---------------------------------------------------------------------------


def _write_cfg(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_resolve_max_rc_retries_returns_kanban_default_when_no_project_override(
    tmp_path: pathlib.Path,
) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[release]\nmax_rc_retries = 3\n")
    assert resolve_max_rc_retries("myproj", str(tmp_path)) == 3


def test_resolve_max_rc_retries_project_override_wins_over_kanban_default(
    tmp_path: pathlib.Path,
) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[release]\nmax_rc_retries = 3\n")
    _write_cfg(
        tmp_path / "projects" / "pgai-agent-kanban" / "project.cfg",
        "[project]\nproject_name = pgai-agent-kanban\n\n[release]\nmax_rc_retries = 1\n",
    )
    assert resolve_max_rc_retries("pgai-agent-kanban", str(tmp_path)) == 1


def test_resolve_max_rc_retries_fails_loud_when_absent_from_both(tmp_path: pathlib.Path) -> None:
    with pytest.raises(CeilingConfigError, match="max_rc_retries is required but absent"):
        resolve_max_rc_retries("myproj", str(tmp_path))


def test_resolve_max_rc_retries_rejects_non_integer_value(tmp_path: pathlib.Path) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[release]\nmax_rc_retries = notanumber\n")
    with pytest.raises(CeilingConfigError, match="invalid value"):
        resolve_max_rc_retries("myproj", str(tmp_path))


# ---------------------------------------------------------------------------
# Git-backed evidence helpers
# ---------------------------------------------------------------------------


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo_with_rc_branch(tmp_path: pathlib.Path, rc_branch: str = "ai_rc/v2.0.1") -> pathlib.Path:
    """Init a repo with an ai_main initial commit and an rc branch off it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "ai_main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("init\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial commit")
    _git(repo, "checkout", "-q", "-b", rc_branch)
    return repo


def _merge_feature_branch(repo: pathlib.Path, target_branch: str, feature_name: str, filename: str) -> None:
    """Simulate a CODER task: branch from target, commit, merge back --no-ff."""
    _git(repo, "checkout", "-q", "-b", feature_name, target_branch)
    (repo / filename).write_text(f"{feature_name}\n")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", f"Add {filename}")
    _git(repo, "checkout", "-q", target_branch)
    _git(repo, "merge", "--no-ff", "-q", "-m", f"Merge {feature_name} into {target_branch}", feature_name)


def test_operator_commit_in_rc_lineage_false_when_only_merge_commits(tmp_path: pathlib.Path) -> None:
    repo = _init_repo_with_rc_branch(tmp_path)
    _merge_feature_branch(repo, "ai_rc/v2.0.1", "feature/CODER-1", "one.txt")
    _merge_feature_branch(repo, "ai_rc/v2.0.1", "feature/CODER-2", "two.txt")
    assert operator_commit_in_rc_lineage(str(repo), "ai_rc/v2.0.1", "ai_main") is False


def test_operator_commit_in_rc_lineage_true_when_direct_commit_present(tmp_path: pathlib.Path) -> None:
    repo = _init_repo_with_rc_branch(tmp_path)
    _merge_feature_branch(repo, "ai_rc/v2.0.1", "feature/CODER-1", "one.txt")
    # A direct commit on the RC branch, bypassing the feature-branch-and-merge
    # convention -- the operator-commit signal.
    _git(repo, "checkout", "-q", "ai_rc/v2.0.1")
    (repo / "direct.txt").write_text("hand-placed\n")
    _git(repo, "add", "direct.txt")
    _git(repo, "commit", "-q", "-m", "operator direct edit")
    assert operator_commit_in_rc_lineage(str(repo), "ai_rc/v2.0.1", "ai_main") is True


def test_operator_commit_in_rc_lineage_false_when_no_commits_ahead(tmp_path: pathlib.Path) -> None:
    """An RC branch that has diverged with zero commits yet (freshly opened) is clean."""
    repo = _init_repo_with_rc_branch(tmp_path)
    assert operator_commit_in_rc_lineage(str(repo), "ai_rc/v2.0.1", "ai_main") is False


def test_operator_commit_in_rc_lineage_raises_evidence_error_on_bad_ref(tmp_path: pathlib.Path) -> None:
    repo = _init_repo_with_rc_branch(tmp_path)
    with pytest.raises(EvidenceError):
        operator_commit_in_rc_lineage(str(repo), "no-such-branch", "ai_main")


def test_git_tag_exists_true_after_tagging(tmp_path: pathlib.Path) -> None:
    repo = _init_repo_with_rc_branch(tmp_path)
    _git(repo, "tag", "v2.0.1")
    assert git_tag_exists(str(repo), "v2.0.1") is True


def test_git_tag_exists_false_when_untagged(tmp_path: pathlib.Path) -> None:
    repo = _init_repo_with_rc_branch(tmp_path)
    assert git_tag_exists(str(repo), "v2.0.1") is False


def test_git_tag_exists_raises_evidence_error_on_bad_path(tmp_path: pathlib.Path) -> None:
    with pytest.raises(EvidenceError):
        git_tag_exists(str(tmp_path / "not-a-repo"), "v2.0.1")


# ---------------------------------------------------------------------------
# extract_facts / resolve_block — end-to-end BlockEvidence fixtures
# ---------------------------------------------------------------------------


def _write_halt_file(path: pathlib.Path, *, from_cm: bool) -> None:
    if from_cm:
        path.write_text(
            "# HALT created by CM at 2026-07-18T10:00:00Z\n"
            "# Reason: TESTER systemic_risk=high in rc/v2.0.1\n"
            "# Resolution: Operator review required.\n",
            encoding="utf-8",
        )
    else:
        path.write_text(
            "# HALT created by operator at 2026-07-18T09:00:00Z\n"
            "# Reason: governance review\n",
            encoding="utf-8",
        )


def _golden_evidence(tmp_path: pathlib.Path, **overrides) -> BlockEvidence:
    """Build a full BlockEvidence fixture matching the golden facts, with
    every field explicitly provided (real git repo, real files)."""
    repo = _init_repo_with_rc_branch(tmp_path, rc_branch="ai_rc/v2.0.1")
    _merge_feature_branch(repo, "ai_rc/v2.0.1", "feature/CODER-1", "one.txt")

    halt_file = tmp_path / "HALT"
    _write_halt_file(halt_file, from_cm=True)

    report_file = tmp_path / "report.md"
    report_file.write_text(_REPORT_ALL_FILED, encoding="utf-8")

    release_state = tmp_path / "release-state" / "v2.0.1.json"
    release_state.parent.mkdir(parents=True, exist_ok=True)
    release_state.write_text(
        json.dumps({"rc": "v2.0.1", "outcome": "in_progress", "attempt": 1}), encoding="utf-8"
    )

    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    evidence = BlockEvidence(
        halt_file_path=str(halt_file),
        tester_report_path=str(report_file),
        release_state_path=str(release_state),
        dev_tree_path=str(repo),
        target_version="v2.0.1",
        rc_branch="ai_rc/v2.0.1",
        base_branch="ai_main",
        project_tasks_dir=str(tasks_dir),
        max_rc_retries=3,
    )
    for key, value in overrides.items():
        setattr(evidence, key, value)
    return evidence


def test_extract_facts_golden_fixture_produces_golden_facts(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    facts = extract_facts(evidence)
    assert facts.trigger_class == "ship-policy-halt"
    assert facts.findings_all_filed is True
    assert facts.shipped_or_tagged is False
    assert facts.approval_gate_present is False
    assert facts.operator_commits_in_lineage is False
    assert facts.attempts_below_ceiling is True
    assert facts.attempt == 1
    assert facts.ceiling == 3


def test_resolve_block_golden_fixture_resolves_to_cancel_and_rebundle(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    result = resolve_block(evidence)
    assert result.resolution == CANCEL_AND_REBUNDLE
    assert result.matched_row is not None
    assert result.matched_row.resolution == CANCEL_AND_REBUNDLE


def test_resolve_block_operator_halt_file_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    _write_halt_file(pathlib.Path(evidence.halt_file_path), from_cm=False)
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.trigger_class == "operator-halt"


def test_resolve_block_unfiled_finding_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    pathlib.Path(evidence.tester_report_path).write_text(_REPORT_ONE_UNFILED, encoding="utf-8")
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.findings_all_filed is False


def test_resolve_block_shipped_outcome_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    state_path = pathlib.Path(evidence.release_state_path)
    state_path.write_text(json.dumps({"rc": "v2.0.1", "outcome": "shipped", "attempt": 1}), encoding="utf-8")
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.shipped_or_tagged is True


def test_resolve_block_approval_gate_present_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    (pathlib.Path(evidence.project_tasks_dir) / "HUMAN-APPROVE-v2.0.1-001").mkdir(parents=True)
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.approval_gate_present is True


def test_resolve_block_operator_commit_in_lineage_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    repo = pathlib.Path(evidence.dev_tree_path)
    _git(repo, "checkout", "-q", "ai_rc/v2.0.1")
    (repo / "direct.txt").write_text("hand-placed\n")
    _git(repo, "add", "direct.txt")
    _git(repo, "commit", "-q", "-m", "operator direct edit")
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.operator_commits_in_lineage is True


def test_resolve_block_attempt_at_ceiling_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    pathlib.Path(evidence.release_state_path).write_text(
        json.dumps({"rc": "v2.0.1", "outcome": "in_progress", "attempt": 3}), encoding="utf-8"
    )
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.attempts_below_ceiling is False
    assert result.facts.attempt == 3
    assert result.facts.ceiling == 3


def test_resolve_block_with_no_evidence_at_all_fails_closed_to_halt_hard(tmp_path: pathlib.Path) -> None:
    """An almost-empty BlockEvidence (only the ceiling resolvable) proves
    none of the other five facts -- every fail-closed default should push
    away from the golden row, resolving to HALT-hard."""
    evidence = BlockEvidence(max_rc_retries=3)
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.trigger_class == "unknown"
    assert result.facts.findings_all_filed is False
    assert result.facts.shipped_or_tagged is True
    assert result.facts.approval_gate_present is True
    assert result.facts.operator_commits_in_lineage is True
    # attempt is undeterminable -> attempts_below_ceiling fails closed too.
    assert result.facts.attempts_below_ceiling is False
    assert result.facts.attempt is None


def test_resolve_block_raises_ceiling_config_error_when_ceiling_unresolvable(
    tmp_path: pathlib.Path,
) -> None:
    """The ceiling is the one fact that is never silently defaulted --
    absence raises rather than failing closed to a fact value."""
    evidence = BlockEvidence()
    with pytest.raises(CeilingConfigError):
        resolve_block(evidence)
