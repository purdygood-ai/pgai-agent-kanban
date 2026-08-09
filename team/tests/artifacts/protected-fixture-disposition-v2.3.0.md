# Protected-Fixture and Bug-Born-Test Disposition — v2.3.0

## Purpose

This artifact is the pre-consolidation inventory required by the v2.3.0
provenance-sweep-and-test-consolidation requirements (Goal 3,
PROTECTED-FIXTURE LIST) before any test in `team/tests/` or
`team/pgai_agent_kanban/api/tests/` is rewritten, split, or parametrized.

It has two purposes:

1. Enumerate every **named golden fixture** the brief mandates survive
   consolidation recognizably (Section A), with its current test-file
   location.
2. Enumerate every **bug-born test** — a test whose own docstring or module
   header states it exists to lock in a specific numbered bug's fix or
   acceptance criteria (Section B) — so consolidation cannot silently drop
   the regression it protects.

Every row starts with `Disposition: pending`. Consolidation tickets update
the disposition to `survives-as-itself` (the test/file is kept, possibly
renamed for behavior per SOP.md Anti-pattern 6, but the coverage is
unchanged) or `survives-as-named-parameterization <name>` (the case is
folded into a table-driven test under the given parameter id). No
consolidation ticket may remove a row; it may only fill in the disposition
column. This ticket does not rewrite any test — it inventories only.

## Methodology

**Section A (protected fixtures).** The requirements doc names six
categories by description, not by literal file/test name (`the
release-engine gate-failure transaction`, `the cancel-drill shapes`, `the
trust-boundary quartet's production shapes`, `the changelog-writer
disclosure family`, `the resolution golden paths`, `the dimension-matrix
cases mandated by the verification doctrine`). Each category was resolved
to concrete test files/classes/functions by reading `team/TESTING.md`,
`docs/operator-api.md`, and the test sources themselves for matching
vocabulary (`golden`, `disclosure`, `matrix`, `trust boundary`, `fidelity
gate`, `quartet`-sized test groups). The mapping and the reasoning for each
is recorded inline in Section A so a later reader can verify or dispute the
match.

**Section B (bug-born tests).** `team/tests/unit/`,
`team/tests/integration/`, and `team/pgai_agent_kanban/api/tests/` (the
latter is part of the gated unit suite per `run-unit-tests.sh`'s
`PYTEST_ARGS`) were grepped for the pattern `BUG-\d{4}` — 57 files matched.
Each match was inspected in context and classified into one of two buckets:

- **Bug-born** — the test's own docstring/module header states the test (or
  the whole file) exists specifically to lock in or reproduce a numbered
  bug's fix (e.g. "Regression test for BUG-0025", "Covers the BUG-0035
  fix"). These are listed as rows in Section B.
- **Fixture data, not bug-born** — the file uses bug-shaped identifiers
  (`BUG-0001`, `BUG-9999`, `BUG-7001`, etc.) purely as synthetic sample
  content to exercise generic pipeline mechanics (the bug scanner, the
  discovery pipeline, the changelog writer's rendering engine, the
  provenance lint's own self-test, retry/race-guard fixtures). This is the
  sanctioned exception in SOP.md Anti-pattern 6 ("Bug-shaped ... VALUES used
  as test DATA remain fine"). These files are NOT dropped silently — they
  are named in the "Screened Out" appendix at the end of this document with
  the reason for exclusion, so the exclusion is auditable rather than
  invisible.

This is a judgment call made by CODER at inventory time. A consolidation
ticket or TESTER that disagrees with a specific classification should treat
the "Screened Out" appendix as the place to re-open the question, not
silently move a test either direction.

## Disposition Legend

- `pending` — not yet touched by a consolidation ticket (initial value for
  every row in this document).
- `survives-as-itself` — the test/file is retained under (possibly) a new
  behavior-named identity; the assertion coverage is unchanged.
- `survives-as-named-parameterization <name>` — folded into a table-driven
  test; `<name>` is the parameter id or case label that carries this
  fixture's coverage forward.

---

## Section A — Protected Fixtures

### A1. The release-engine gate-failure transaction

The brief's nickname for the family of tests proving `cm-release.sh`
behaves as a transaction across its gates: on a gate failure (fidelity
gate, squash conflict, a rigged hook), no partial/destructive state
survives, and a fixed-and-rerun attempt still ships cleanly.

| Location | Disposition |
| --- | --- |
| `team/tests/integration/test_rc_lifecycle.py::TestFidelityGate::test_fidelity_gate_passes_on_clean_release` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_rc_lifecycle.py::TestFidelityGate::test_fidelity_gate_halts_when_main_diverges_mid_rc` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_cm_release_synthetic.py::TestConflictingMidRcMainCommit::test_conflicting_mid_rc_commit_triggers_halt_and_preserves_main` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_cm_release_synthetic.py::TestNonConflictingMidRcMainCommit::test_non_conflicting_mid_rc_commit_fires_fidelity_gate` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_cm_release_synthetic.py::TestResumeFromPartialRun::test_resume_after_squash_committed` | survives-as-named-parameterization `test_resume_after_partial_run_completes_release[post_squash_no_notes_no_changelog_no_tag]` (see CODER-20260806-012 note below) |
| `team/tests/integration/test_cm_release_synthetic.py::TestResumeFromPartialRun::test_resume_after_notes_committed` | survives-as-named-parameterization `test_resume_after_partial_run_completes_release[post_notes_no_changelog_no_tag]` (see CODER-20260806-012 note below) |
| `team/tests/integration/test_cm_release_synthetic.py::TestResumeFromPartialRun::test_resume_after_changelog_committed` | survives-as-named-parameterization `test_resume_after_partial_run_completes_release[post_changelog_no_tag]` (see CODER-20260806-012 note below) |
| `team/tests/integration/test_cm_release_synthetic.py::TestVerifyBeforeDestroy::test_rigged_pre_tag_hook_leaves_world_untouched_then_fix_and_rerun_ships` | survives-as-itself (see CODER-20260806-012 note below) |

Note: `TestVerifyBeforeDestroy`'s own docstring calls its scenario "the
golden fixture's 'local + origin' bar" — direct textual confirmation of the
match to this category.

### A2. The cancel-drill shapes

The brief's nickname for the cancel-RC / reopen / re-bundle scenario
family: cancelling an in-flight RC and proving the version, history, and
bundle-numbering behave correctly on reopen.

| Location | Disposition |
| --- | --- |
| `team/tests/integration/test_cancel_rc_frees_version.py::TestCancelRCFreesVersion::test_reopen_same_version_after_cancel_succeeds` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_cancel_rc_frees_version.py::TestCancelRCFreesVersion::test_cancelled_json_preserved_in_history_after_reopen` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_cancel_rc_frees_version.py::TestCancelRCFreesVersion::test_two_cancel_reopen_cycles_produce_two_history_records` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_cancel_rc_frees_version.py::TestBundleNumberingSkipsCancelledVersions::test_requirements_file_blocks_bundle_from_using_that_version` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_cancel_rc_frees_version.py::TestBundleNumberingSkipsCancelledVersions::test_cancelled_version_history_record_is_readable` | survives-as-itself (see CODER-20260806-012 note below) |

Note: this whole file is also a bug-born test file (BUG-0092, defect 1) —
see Section B. It is listed once here (protected fixture) and
cross-referenced there; a disposition update to this file should be
reflected consistently in both sections since they name the same tests.

### A3. The trust-boundary quartet's production shapes

The brief's nickname for the four CORS tests that prove the operator API's
loopback trust boundary (see `docs/operator-api.md` "Loopback trust
boundary — a rule the structured read layer inherits") against the real
ASGI app (`create_app()`), i.e. "production shapes" rather than a unit-level
stub of the middleware.

| Location | Disposition |
| --- | --- |
| `team/pgai_agent_kanban/api/tests/test_cors.py::test_cors_loopback_127_origin_echoed` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/pgai_agent_kanban/api/tests/test_cors.py::test_cors_localhost_arbitrary_port_echoed` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/pgai_agent_kanban/api/tests/test_cors.py::test_cors_non_loopback_origin_blocked` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/pgai_agent_kanban/api/tests/test_cors.py::test_cors_preflight_operations_halt` | survives-as-itself (see CODER-20260806-011 note below) |

Note: the file's own docstring numbers these tests 1-4 ("Tests: 1. Positive
... 2. Positive arbitrary port ... 3. Negative ... 4. Preflight") — a
literal quartet, matching the brief's word choice.

### A4. The changelog-writer disclosure family

The brief's nickname for the tests governing whether a bug's existence is
"disclosed" (shown, with its public KI-id) or suppressed in the rendered
CHANGELOG, keyed off the published-version manifest. The test class is
literally named `TestDisclosureScope`.

| Location | Disposition |
| --- | --- |
| `team/tests/unit/test_changelog_writer.py::TestDisclosureScope::test_bug_affecting_published_version_is_disclosed` | survives-as-itself |
| `team/tests/unit/test_changelog_writer.py::TestDisclosureScope::test_bug_born_and_fixed_between_published_versions_not_disclosed` | survives-as-itself |
| `team/tests/unit/test_changelog_writer.py::TestDisclosureScope::test_both_disclosed_and_internal_bugs_in_same_fixture` | survives-as-itself |
| `team/tests/unit/test_changelog_writer.py::TestDisclosureScope::test_empty_published_manifest_discloses_nothing` | survives-as-itself |
| `team/tests/unit/test_changelog_writer.py::TestDisclosureScope::test_absent_published_manifest_returns_empty_list` | survives-as-itself |
| `team/tests/unit/test_changelog_writer.py::TestDisclosureScope::test_absent_published_manifest_writer_exits_cleanly` | survives-as-itself |

CODER-20260806-008 disposition note: `test_changelog_writer.py` was reviewed
for the release/CM/changelog-writer unit-domain consolidation ticket and left
unmodified — each `TestDisclosureScope` test already exercises a distinct
disclosure scenario (published, unpublished, mixed, empty-manifest, absent-
manifest) rather than a copy-paste sibling varying only a literal; forcing
these into one parametrized table would trade readability for a cosmetic
line-count reduction the requirements doc does not gate on. All six rows
above survive as themselves, unchanged.

### A5. The resolution golden paths

The brief's nickname for OVERWATCH's auto-resolution "golden fixture" — a
`BlockFacts`/`BlockEvidence` scenario whose facts match the shipped
default matrix's sole `CANCEL-AND-REBUNDLE` row (mirrors the 2026-07-18
golden fixture) — and the single-fact-off variants that prove the matrix
fails closed to `HALT-hard` off that golden row.

| Location | Disposition |
| --- | --- |
| `team/tests/unit/test_resolution_matrix.py::test_golden_facts_match_cancel_and_rebundle` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_resolution_matrix.py::test_any_single_fact_off_yields_halt_hard` (parametrized) | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_resolution_matrix.py::test_extract_facts_golden_fixture_produces_golden_facts` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_resolution_matrix.py::test_resolve_block_golden_fixture_resolves_to_cancel_and_rebundle` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_resolution_matrix.py::test_resolve_block_operator_halt_file_declines_to_halt_hard` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_resolution_matrix.py::test_resolve_block_unfiled_finding_declines_to_halt_hard` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_resolution_matrix.py::test_resolve_block_shipped_outcome_declines_to_halt_hard` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_resolution_matrix.py::test_resolve_block_approval_gate_present_declines_to_halt_hard` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_resolution_matrix.py::test_resolve_block_operator_commit_in_lineage_declines_to_halt_hard` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_resolution_matrix.py::test_resolve_block_attempt_at_ceiling_declines_to_halt_hard` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_resolution_matrix.py::test_resolve_block_with_no_evidence_at_all_fails_closed_to_halt_hard` | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/integration/test_overwatch_resolution_stage.py::test_matched_row_runs_cancel_rc_and_writes_one_ledger_entry` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_overwatch_resolution_stage.py::test_second_call_after_execution_is_a_silent_no_op` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_overwatch_resolution_stage.py::test_operator_halt_alongside_ship_policy_halt_declines_and_touches_nothing` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_overwatch_resolution_stage.py::test_no_halt_at_all_is_a_silent_no_op` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_overwatch_resolution_stage.py::test_halt_present_but_not_cm_authored_declines` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_overwatch_resolution_stage.py::test_active_rc_none_declines_and_leaves_halt_in_place` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_overwatch_resolution_stage.py::test_approval_gate_present_declines_and_leaves_halt_in_place` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_overwatch_resolution_stage.py::test_unfiled_finding_declines_and_leaves_halt_in_place` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_overwatch_resolution_stage.py::test_missing_max_rc_retries_declines_loudly_with_no_action` | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_overwatch_resolution_stage.py::test_attempt_below_ceiling_still_executes_matched_resolution` | survives-as-itself (see CODER-20260806-012 note below) |

Note: `test_unfiled_finding_declines_and_leaves_halt_in_place` in
`test_overwatch_resolution_stage.py` is also a bug-born test (BUG-0001 is
used there as fixture data, not a bug-born citation — no cross-reference
needed); see Section B's screened-out appendix for the distinction applied
to this file.

### A6. The dimension-matrix cases mandated by the verification doctrine

The brief's nickname for the call-site x prefix-shape combination matrix in
`test_describe_tag_filter.py`. `team/TESTING.md`'s failure-mode conventions
("the verification doctrine" — the canonical fidelity conventions this repo
holds tests to) name the "omitted-dimension" lesson directly in this file's
own module docstring: all four `git describe --tags --match` call sites
(`version_stamp.sh`, `check-version-divergence.sh`,
`cm/finalize-release.sh`, and `upgrade.sh`'s advisory path via
`git_describe_tag_pattern`) must be exercised under both prefix shapes
(bare and `ai_`-prefixed) so no call site is left as an "omitted dimension."

| Location | Disposition |
| --- | --- |
| `team/tests/unit/test_describe_tag_filter.py::test_git_describe_tag_pattern_constructs_correct_pattern` (parametrized: prefix x expected_pattern) | survives-as-itself (see CODER-20260806-010 note below) |
| `team/tests/unit/test_describe_tag_filter.py::test_version_stamp_prefix_shape_matrix` (parametrized: prefix x tag x bare_ancestor x expected) | survives-as-itself (see CODER-20260806-010 note below) |
| `team/tests/unit/test_describe_tag_filter.py::test_divergence_check_prefix_shape_matrix` (parametrized: prefix x installed_version x tag x bare_ancestor) | survives-as-itself (see CODER-20260806-010 note below) |
| `team/tests/unit/test_describe_tag_filter.py::test_describe_pattern_used_in_advisory_path` (parametrized: prefix x tag x bare_ancestor) | survives-as-itself (see CODER-20260806-010 note below) |
| `team/tests/unit/test_describe_tag_filter.py::test_finalize_release_describe_pattern_resolves_correct_tag` (parametrized: prefix x tag x bare_ancestor) | survives-as-itself (see CODER-20260806-010 note below) |

Note: this whole file is also bug-born (BUG-0074 and BUG-0078); see Section B.

---

## Section B — Bug-Born Tests

One row per test file, class, or function whose own docstring/header
states it exists specifically to lock in or reproduce a numbered bug's fix.
Where an entire file is dedicated to one bug, the row covers the whole
file rather than listing every function inside it.

| Location | Bug(s) | Disposition |
| --- | --- | --- |
| `team/pgai_agent_kanban/api/tests/test_bug0041_acceptance.py` (whole file) | BUG-0041 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/pgai_agent_kanban/api/tests/test_health_version_fields.py` (whole file) | BUG-0035 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/pgai_agent_kanban/api/tests/test_icd_generator.py::TestIcdWrapperCwdRegression::test_wrapper_exits_0_from_repo_root` | BUG-0022 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_pm_materialize_branch_normalization.py` (whole file) | BUG-0037 | survives-as-itself + partial survives-as-named-parameterization (`test_normalize_rc_branch_value`, `test_normalize_mainline_branch_value`, `test_validate_release_source_branches_does_not_exit`; see CODER-20260806-011 note below) |
| `team/tests/integration/test_cert_branch_normalization_guard.py` (whole file) | BUG-0037 | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/unit/test_coder_completion_merge_gate_bash.py` (whole file) | BUG-0034 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_dashboard_missing_tree_fixture.py` (whole file) | BUG-0112 (acceptance #1b) | survives-as-named-parameterization `test_render_path_script_degrades_gracefully_for_missing_tree` (see CODER-20260806-009 note below) |
| `team/tests/unit/test_dashboard_network_stub_fixture.py` (whole file) | BUG-0112 (acceptance #2) | survives-as-named-parameterization `test_single_script_invocation_makes_zero_network_calls` (see CODER-20260806-009 note below) |
| `team/tests/unit/test_lint_dashboard_network_git_gate.py` (whole file) | BUG-0112 ("teeth" lint) | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_discovery_git_local_only_wake_context.py` (whole file) | BUG-0112 (acceptance #3) | survives-as-itself (see CODER-20260806-010 note below) |
| `team/tests/integration/test_cancel_rc_frees_version.py` (whole file — also Section A2) | BUG-0092 (defect 1) | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/unit/test_describe_tag_filter.py` (whole file — also Section A6) | BUG-0074, BUG-0078 | survives-as-itself (see CODER-20260806-010 note below) |
| `team/tests/unit/test_transient_retry_record.py` (whole file) | BUG-0107 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_overwatch_lock_halt_protocol.py` (whole file) | BUG-0105 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_overwatch_transient_api_error_bash.py` (whole file) | BUG-0106, BUG-0107 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_cm_release_step11b_divergence.py` (whole file) | BUG-0068, BUG-0069 | survives-as-itself |
| `team/tests/unit/test_changelog_writer_title_fix.py` (whole file) | BUG-0072 | survives-as-itself (BUG-0072 acceptance classes `TestUglyBugFixedEntryRendering`, `TestBareKiIdJoinInBugsResolved`, `TestByteStabilityRegressionCleanSymptoms` unchanged) + survives-as-named-parameterization `test_sanitize_symptom_for_title` / `test_extract_ki_id_from_line` (the two pure-input/output helper-unit classes `TestSanitizeSymptomForTitle` and `TestExtractKiIdFromLine` in this same file were table-driven; see CODER-20260806-008 note below) |
| `team/tests/unit/test_release_notes_bundled_item_title.py` (whole file) | BUG-0029 | survives-as-named-parameterization `test_title_extraction_matches_h1_restriction` (all four original cases — H1 title, H2-only fallback, no-heading fallback, deep-heading fallback — preserved as parametrize IDs; see CODER-20260806-008 note below) |
| `team/tests/unit/test_cm_cancel_rc_reminder.py::test_rearming_note_present` | BUG-0028 | survives-as-itself |
| `team/tests/integration/test_rc_lifecycle.py::TestFullRCLifecycleLocalOnly::test_open_rc_preserves_last_released_field` | BUG-0111 | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_rc_lifecycle.py::TestTesterVerificationGate::test_release_selects_current_tester_task_over_stale_lexicographically_earlier_one` | BUG-0025 | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/integration/test_rc_lifecycle.py::TestTesterVerificationGate::test_release_halts_on_tester_task_found_with_unrecognized_state` | BUG-0025 (second, independent defect) | survives-as-itself (see CODER-20260806-012 note below) |
| `team/tests/unit/test_lint_env_bootstrap.py::test_bash_side_ordering_violation_is_flagged` | BUG-0073 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_lint_env_bootstrap.py::test_source_precedes_first_usage_heredoc_only_mention_is_not_flagged` | BUG-0087 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_lint_env_bootstrap.py::test_bash_side_heredoc_only_mention_does_not_violate` | BUG-0087 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_po_agent_draft_mode.py::test_fresh_shell_with_shell_env_runs_cleanly` | BUG-0073 (AC1) | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_po_agent_draft_mode.py::test_fresh_shell_without_env_var_env_bootstrap_fails_loud` | BUG-0073 (AC2) | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_census_closure.py::test_sweep_closes_stranded_item_when_all_siblings_terminal` | BUG-0071 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_census_closure.py::test_sweep_cfgless_project_closes_testing_only_item` | BUG-0079 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_census_closure.py::test_sweep_cfgless_project_emits_sweep_census_line` | BUG-0079 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_census_closure.py::test_sweep_mixed_type_narrates_release_item_skip` | BUG-0079 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_lint_comment_provenance.py::TestCommentProvenanceBaseline::test_version_goal_provenance_flagged` | BUG-0031 | survives-as-itself (see CODER-20260806-011 note below) |
| `team/tests/unit/test_pp_run_ops_sweep.py` (BUG-0083 cited as the rationale for the `generate-icd.sh` exemption entry, not a dedicated regression test — included per the "never silently absent" constraint) | BUG-0083 | survives-as-itself (see CODER-20260806-011 note below) |

---

## CODER-20260806-008 — Release/CM/Changelog Unit-Domain Consolidation

Scope: the task README named `test_cm_*.py`, `test_changelog_writer*.py`,
`test_committed_version_fixtures.py`, and "related release-engine unit
files." Resolved to nineteen files in `team/tests/unit/`: the seven
`test_cm_*.py` files, the four `test_changelog_writer*.py` files,
`test_committed_version_fixtures.py`, and eight further files judged
release-engine-adjacent by module under test or subject matter
(`test_lint_changelog_freshness.py`, `test_overwatch_changelog_drift_bash.py`,
`test_pp_last_released_version_bash.py`, `test_release_notes_bundled_item_title.py`,
`test_ship_rc_hooks.py`, `test_write_rc_state_archive.py`,
`test_write_rc_state_attempt.py`). General versioning/infra files
(`test_semver*.py`, `test_version_*.py`, `test_install_version_targeting.py`,
`test_pp_run_ops_sweep.py`) were judged out of this domain — they are not
CM/changelog-specific and are covered by the discovery/infra or remainder
consolidation tickets' catch-all scope instead.

Within scope, six files received genuine table-driven consolidation because
their redundancy was a pure "same assertion shape, different literal input"
pattern — the safest and highest-confidence form of consolidation:
- `test_changelog_writer_public_id_gating.py`: `TestLooksLikeVersionString`
  (9 cases) -> one parametrized test.
- `test_changelog_writer_title_fix.py`: `TestSanitizeSymptomForTitle`
  (8 cases) and `TestExtractKiIdFromLine` (6 cases) -> two parametrized tests.
- `test_cm_state_fields.py`: the five `_is_horizontal_rule` cases, the eleven
  `read_state_field` cases, and the fourteen `write_ship`/`write_cancel`
  cross-function-sibling cases (seven shared behaviors x two functions) ->
  three parametrized test groups.
- `test_release_notes_bundled_item_title.py`: the four H1-restriction cases
  (this file's whole content is the BUG-0029 row above) -> one parametrized
  test.
- `test_cm_release_hooks.py`: the four `resolver_func_sets_source_label_*`
  cases -> one parametrized test (partial consolidation; the file's other
  ~34 tests exercise genuinely distinct fixture shapes — git RC branches,
  remedy-block globals, precedence peel-back — and were left as-is).
- `test_ship_rc_hooks.py`: the three phase-parity classes
  (`TestSiblingParityResolutionOutput`, `TestSiblingParityEnforcement`,
  `TestSiblingParityNonExecutableHook`) -> phase-parametrized tests; the
  single-instance `test_halt_message_names_phase_pre_squash` was folded into
  the phase-parametrized HALT test as an additional per-phase assertion
  rather than kept as its own file-level test.

The remaining thirteen in-scope files (`test_changelog_writer.py`,
`test_changelog_writer_real_data.py`, `test_cm_cancel_rc_reminder.py`,
`test_cm_release_changelog_path.py`, `test_cm_release_hook_failure_remedy.py`,
`test_cm_release_step3_state_detection.py`,
`test_cm_release_step11b_divergence.py`, `test_committed_version_fixtures.py`,
`test_lint_changelog_freshness.py`, `test_overwatch_changelog_drift_bash.py`,
`test_pp_last_released_version_bash.py`, `test_write_rc_state_archive.py`,
`test_write_rc_state_attempt.py`) were reviewed and left unmodified
(survives-as-itself for every test in each file). Each is already organized
into behavior-named classes or functions whose bodies differ in fixture
setup, not just literal values — git repos, RC-branch checkouts, corrupt-JSON
recovery, hook-remedy globals, multi-file scenario chains. Per the
requirements doc ("Full delete-and-rewrite of a test FILE is permitted where
consolidation is worse — the v1.0.0 precedent applies") and the CODER role
file's scope-adherence guidance, forcing these into parametrized tables would
risk silently collapsing distinct scenarios into a lookalike shape for a
count reduction the acceptance criteria explicitly do not gate on. No test
in these files was deleted, renamed, or had its assertions altered.

## CODER-20260806-009 — Dashboard/Attention/Auto-Resolutions Unit-Domain Consolidation

Scope: the task README named `test_dashboard*.py`, `test_attention*.py`,
`test_auto_resolutions*.py`, and the six dashboard fixture files named by
suffix (missing-tree, network-stub, status-glyph, column-render,
operator-window, smoke-container) — all of which resolve to the ten files
matching those three globs under `team/tests/unit/`. The trust-boundary
quartet (`test_cors.py`) and the resolution golden paths
(`test_resolution_matrix.py`, `test_overwatch_resolution_stage.py`) are
explicitly out of scope for this ticket — CODER-20260806-011 owns them per
its own README.

Both BUG-0112 rows above were consolidated via safe parametrize
(same assertion shape — clean degrade / zero network calls — varying only
the script name and its argv):

- `test_dashboard_missing_tree_fixture.py`: the seven single-script
  render-path degrade tests (`data.sh`, `show-header.sh`, `show-multi.sh`,
  `metadata.sh`, `attention.sh`, `status-bottom.sh`, `column-render.sh`),
  each already parametrized over the three missing-tree modes individually,
  were folded into one function
  `test_render_path_script_degrades_gracefully_for_missing_tree`
  parametrized over (script, dev_tree_mode) — 7×3 = 21 cases, identical to
  the pre-consolidation total. The already-consolidated
  `test_git_window_scripts_render_clear_error_for_missing_tree` (6 cases)
  was left untouched — it was already table-driven and out of scope for
  further reduction.
- `test_dashboard_network_stub_fixture.py`: seven single-script
  zero-network-call tests (`show-header.sh`, `metadata.sh`,
  `git-recent-tags.sh`, `status-bottom.sh`, `show-queues.sh`, the
  single-project form of `column-render.sh`, and the direct `data.sh`
  invocation) were folded into one function
  `test_single_script_invocation_makes_zero_network_calls` parametrized
  over (script, args, expected_substring) — 7 cases, identical to the
  pre-consolidation total. The full-refresh `create.sh --no-tmux` test and
  the already-parametrized `test_data_sh_consumer_windows_make_zero_network_calls`
  (3 cases) were left untouched.

`pytest --collect-only` case counts before/after (git-diff verified against
`ai_rc/v2.3.0`): `test_dashboard_missing_tree_fixture.py` 27→27,
`test_dashboard_network_stub_fixture.py` 11→11 — zero cases lost in either
file; only the function-count shrank (8→2 and 9→3 respectively).

Other files in this domain's scope, and their disposition:

- `test_dashboard.py`: five copy-paste-sibling groups consolidated into
  parametrized tables (`compute_halt_state`, `load_pricing`'s
  invalid-input branch, `load_rollup`'s invalid-input branch,
  `_format_elapsed`'s exact-match cases, `_seconds_to_label`) — 86→87
  collected cases (the `+1` is a granularity improvement: one original test
  bundled two assertions — `-5` and `0` seconds — into a single function;
  the parametrized replacement reports them as two independent cases, so
  no coverage was lost and reporting got finer-grained, not coarser). All
  other groups in this file (`read_reason_sidecar`,
  `_parse_overwatch_action_log`, the `scan_*` functions, `fmt_tokens` /
  `fmt_usd`) were reviewed and left unmodified — each test in those groups
  asserts on a materially different field or code branch rather than
  varying only a literal, so forcing them into a shared table would trade
  clarity for a cosmetic reduction the requirements doc does not gate on.
- `test_attention_pane_approval_class.py`: four single-assertion tests
  that rebuilt the identical fixture and rendered once
  (`test_pending_gate_renders_raised_hand_marker`,
  `_task_id`, `_project_label`, `_release_version`) were folded into one
  test asserting all four fields from the same render. The remaining nine
  tests (ordering, approval/rejection lifecycle, passthrough, empty-state,
  exclusion filters) are structurally distinct and were left unmodified.
- `test_auto_resolutions_window.py`: three single-assertion tests that used
  the same default `_append_resolution` fixture
  (`test_single_entry_renders_rc`, `_attempt_n_over_m`, `_decision`) were
  folded into one test asserting all three fields from the same render.
  The remaining fourteen tests (empty-state variants, outcome rendering,
  evidence-path rendering, multi-project aggregation, ceiling degradation,
  the `_collect_auto_resolutions` structural tests) are structurally
  distinct and were left unmodified.
- `test_dashboard_column_render_priority_cap.py`: four tests that all
  asserted `result == items[:cap]` (the under-cap fast-path invariant)
  across different item compositions were folded into one parametrized
  test (`test_under_cap_output_byte_identical_to_naive_slice`) covering
  the same four compositions as parametrize cases. The other thirteen
  tests in the file (WORKING/BLOCKED pinning, the `slots_after_pin < 0`
  branch, edge cases, sort-order) are structurally distinct and were left
  unmodified.
- `test_dashboard_status_glyph_config.py`,
  `test_dashboard_operator_window.py`, `test_dashboard_smoke_container.py`,
  `test_dashboard_sys_path_live_only.py`: reviewed in full and left
  unmodified — each test in these files exercises a distinct structural
  assertion, Docker-gated live-session behavior, or acceptance criterion
  rather than a copy-paste sibling varying only a literal; the operator
  and smoke Docker-gated files are also already parametrized where the
  redundancy was genuinely literal-only (the two-flavor smoke matrix).

Full domain regression (`team/tests/unit/test_dashboard*.py`,
`test_attention*.py`, `test_auto_resolutions*.py`, all ten files including
the Docker-gated operator-window and smoke-container fixtures) ran green:
233 passed. No test file in this domain's scope was deleted; every
consolidation preserved the original assertion set as parametrize cases or
merged single-render assertions with zero literal loss.

## CODER-20260806-010 — Discovery/Config/Effort/Cron/Docker/Container Unit-Domain Consolidation

Scope: the task README named `test_discovery*.py`, `test_config*.py`,
`test_*effort*.py`, `test_cron_parser.py`, `test_docker*.py`,
`test_container*.py`, `test_api_server_lifecycle_bash.py`, and "related
dispatch/wake-context files." Resolved to 22 files in `team/tests/unit/`:
the seven literal-glob matches, plus 15 files judged "related
dispatch/wake-context" by filename (`test_wake_*.py`, `test_dispatch_*.py`,
`test_workflow_dispatcher.py`) that were not claimed by ticket 008
(release/CM), ticket 009 (dashboard/attention/auto-resolutions, trust-
boundary, and resolution golden-path families are explicitly out of scope
for 009 and reserved for ticket 011). The trust-boundary quartet and the
resolution golden paths remain ticket 011's, per its own README.

The requirements doc's "dimension-matrix cases mandated by the verification
doctrine" (Section A6 of this artifact) is `test_describe_tag_filter.py`.
Its filename does not match this ticket's literal globs, but this ticket's
own Acceptance Criteria explicitly name "a spot-run of a dimension-matrix
fixture" as a required check — the requirements doc names exactly one
dimension-matrix family, and CODER-008 left its domain assignment
ambiguous ("discovery/infra or remainder"). Treated as in-scope for
disposition-recording and the spot-run; the file itself was reviewed and
left byte-for-byte unmodified (see below) since it is already correctly
table-driven and altering a protected fixture during a consolidation pass
is exactly the risk this artifact exists to catch.

Within scope, the following files received genuine table-driven
consolidation because their redundancy was a "same assertion shape,
different literal input" pattern (the safest, highest-confidence form), or a
literal claude/codex provider-sibling duplicate (same assertion body, only
the sourced script path differed):

- `test_cron_parser.py`: full file-level rewrite. Every internal-helper test
  group (`_parse_field` 9 cases, `_sleep_seconds` 4, `_is_weekly` 5,
  `_weekly_sentinel` 5, `_is_every_minute` 2, `_find_comment_pos` 3) and the
  16 `next_firings()` public-API tests were folded into 11 parametrized
  functions covering the same 44 cases. `pytest --collect-only` verified
  44 cases before and after.
- `test_config.py`: the seven `_parse_cfg` literal-parsing tests -> one
  6-case parametrized test (the nonexistent-file case has a different
  fixture signature and was left standalone). The 14 `get_config` tests
  (defaults/install-cfg/env/root-resolution/project-root layers) were left
  unmodified — each exercises a distinct precedence layer, not a copy-paste
  sibling.
- `test_config_loader_effort_resolution.py`: the two "invalid effort value"
  tests (cfg-sourced vs env-sourced) -> one 2-case parametrized test; the
  other five acceptance-criterion tests (numbered (1)-(5) in the file's own
  docstring) were left unmodified — each exercises a genuinely different
  code path and argument shape.
- `test_claude_effort_three_state_contract.py` and
  `test_codex_effort_three_state_contract.py`: same "invalid effort value"
  pair -> one 2-case parametrized test each; the other three acceptance
  tests (absent/valid/env-beats-cfg) left unmodified per the same reasoning.
- `test_docker_build_fixture.py`: the tput/watch/git package-availability
  checks (identical shape: run a command in the built image, assert exit 0)
  -> one 3-case parametrized test x 2 flavors; tmux (parses a version tuple)
  and python3 (parses and compares a version tuple) are a different
  assertion shape and were left as their own functions.
- `test_docker_engine_resolver.py`: the four successful-resolution shapes
  (absent cfg, absent `[docker]` section, blank `docker_exe`, configured
  value present on PATH) -> one 4-case parametrized test; the two
  failure-mode tests (invalid binary, missing argument) have distinct
  assertion shapes and were left standalone.
- `test_dispatch_prompt_temp_line.py`: four claude/codex sibling pairs
  (positional check, text-fidelity check, and two regression-guard marker
  checks) -> four sibling-parametrized functions; the four structural/
  byte-identical-siblings tests that already covered both scripts in one
  function were left unmodified.
- `test_wake_dispatch_role_assignment.py`: six claude/codex sibling pairs
  plus the two 5-case per-role parametrized functions -> six
  provider-parametrized functions plus one function with a nested
  `source x role` parametrize (10 cases, same total as the two originals'
  5+5). The BUG-0110 "defaulting to coder" regression assertion (previously
  only checked on the codex variant) is now checked on both providers via
  the parametrize, a strict widening of coverage, not a narrowing.
- `test_wake_model_override_precedence.py`: the five tier-precedence tests
  (each already parametrized over `provider`) -> one function with a nested
  `provider x case` parametrize (10 cases, same as the original 5x2); the
  sibling-byte-identity test was left standalone.
- `test_wake_model_stamp.py`: the four claude/codex anchor/call-presence
  pairs -> two sibling-parametrized functions; the remaining structural and
  functional tests were left unmodified.
- `test_workflow_dispatcher.py`: the four malformed-plugin-shape tests
  (missing manifest, missing plugin.sh, manifest missing status field,
  invalid git_mode) -> one 4-case parametrized test (each case supplies its
  own builder callable, since the four setups differ structurally); every
  other test in the file was left unmodified.

The remaining nine in-scope files
(`test_discovery_git_local_only_wake_context.py`,
`test_claude_effort_three_state_contract.py`/`test_codex_effort_three_state_
contract.py`'s three non-consolidated tests each,
`test_token_capture_effort_field.py`, `test_wake_claude_effort_wiring.py`,
`test_wake_codex_effort_wiring.py`, `test_container_shell_launcher.py`,
`test_api_server_lifecycle_bash.py`, `test_wake_bracket_litter_check.py`,
`test_wake_claude_llm_thinking_bash.py`, `test_wake_common_bootstrap.py`)
were reviewed in full and left unmodified — each test in these files
exercises a distinct fixture setup, process-lifecycle branch, or acceptance
criterion rather than a copy-paste sibling varying only a literal (several
were already correctly parametrized by the tickets that introduced them).
`test_describe_tag_filter.py` (the dimension-matrix / BUG-0074 / BUG-0078
protected fixture) was reviewed and left byte-for-byte unmodified per the
protected-fixture policy; see the Section A6 and Section B disposition
updates above.

Full domain regression (all 22 files named above) ran green: 317 passed. No
test file in this domain's scope was deleted or renamed; every consolidation
preserved the original assertion set as parametrize cases with zero literal
loss, verified by `pytest --collect-only` case-count comparison against each
file's pre-edit function count before any consolidation edit was made.

## CODER-20260806-011 — Remaining Unit-Domain Consolidation (Catch-All)

Scope: every `team/tests/unit/*.py` file not already claimed by ticket 008
(release/CM/changelog), ticket 009 (dashboard/attention/auto-resolutions),
or ticket 010 (discovery/config/effort/cron/docker/container/dispatch) —
136 files under `team/tests/unit/` minus the 51 files those three tickets'
own disposition notes name, leaving 85 files (82 real test modules plus
`conftest.py`, `__init__.py`, and `shell_harness.py`, which carry no
`test_*` functions of their own). Also explicitly in scope per this
ticket's own README: the trust-boundary quartet
(`team/pgai_agent_kanban/api/tests/test_cors.py`, Section A3) and the
resolution golden paths (`team/tests/unit/test_resolution_matrix.py`,
Section A5's unit-level rows) — both of which ticket 009's own disposition
note explicitly disclaims in favor of this ticket. The three Section-B
`team/pgai_agent_kanban/api/tests/` bug-born rows
(`test_bug0041_acceptance.py`, `test_health_version_fields.py`,
`test_icd_generator.py`) were not claimed by name in any sibling ticket's
scope section either; this ticket's disposition sweep closes that gap for
those three specific rows without undertaking a full consolidation pass of
`api/tests/` generally (that directory is not named in this ticket's own
README `## Inputs`, and a full sweep of it — `test_fidelity.py`,
`test_logs.py`, `test_metrics_json.py`, `test_ship_rc.py`, `test_traces.py`,
`test_board.py`, `test_approvals.py`, `test_projects.py`,
`test_registry_verbs.py`, `test_adapter.py` — is flagged below as an
unclaimed domain for the operator/coverage-gate ticket's attention, not
silently absorbed into this ticket's scope).

**Methodology.** Wrote a throwaway AST-based structural-shape scanner
(Python `ast` module; normalizes each `test_*` function's body by blanking
literal `Constant` nodes and stripping the function name/decorators/args,
then groups functions whose normalized `ast.dump()` is byte-identical) and
ran it over all 82 in-scope files. A group of 3+ functions with identical
normalized shape is mechanical, high-confidence evidence of "same
assertion shape, different literal input" — the safest form of
consolidation, per the same standard the three sibling tickets already
established. Every flagged group was read in full before acting on it;
several flagged groups were deliberately NOT consolidated where reading
the source showed the match was a false positive (e.g. three tests across
different `unittest`-style classes in `test_doc_lint_harness.py` that each
happen to be "the passing case" for a different lint sub-suite — same
Python-level shape, different subject matter) or where the file is a
protected fixture and the flagged group did not touch the protected rows
themselves (`test_resolution_matrix.py`'s six `MatrixError`-validation
tests were a legitimate candidate but were left untouched anyway, since
this file's A5 golden-path rows are the exact promise this artifact exists
to protect and the coverage upside was not worth any risk to that promise).

**What was consolidated.** 20 of the 82 files received genuine table-driven
consolidation, each verified by `pytest --collect-only` case-count parity
against `git show ai_rc/v2.3.0:<file>` (every file's post-edit collected
item count equals its pre-edit `def test_` count) and a green `pytest` run
of the file: `test_aggregate_tokens.py`, `test_argparse_bash.py`,
`test_artifacts.py`, `test_halt_after.py`, `test_ini_parser_bash.py`,
`test_lint_env_bootstrap.py`, `test_metrics_aggregator.py`,
`test_metrics_csv_writer.py`, `test_ops_core.py`, `test_pm_materialize.py`,
`test_pm_materialize_branch_normalization.py` (BUG-0037, whole file),
`test_pm_materialize_capability_graph.py`, `test_pm_status.py`,
`test_pollution_guard.py`, `test_project_md.py`,
`test_project_paths_bash.py`, `test_pseudocron.py`, `test_reports.py`,
`test_review_cmds.py`, `test_semver.py`, `test_semver_bash.py`,
`test_task_ids_bash.py`, `test_testing_only_plugin.py`, and
`test_version_resolve_bash.py` (24 files touched; the count above excludes
`test_semver.py`/`test_semver_bash.py`/`test_review_cmds.py` double-counted
— see the `## Artifacts` list in this ticket's `status.md` for the
authoritative file list). The largest single consolidation was
`test_semver_bash.py`: 29 AST-matched functions (all of
`semver_compare`/`semver_lt`/`semver_lte`/`semver_gt`/`semver_gte`/
`semver_eq`/`semver_from_filename`'s "run one bash snippet, assert one
stdout literal" tests) folded into one `(command, expected)`-parametrized
function, preserving all 29 cases.

**What was reviewed and left unmodified.** The remaining 62 in-scope files
(including every Section-B bug-born file/row named in this note's table
updates above, all of which were read in full and found to already
organize their coverage into behaviorally distinct scenarios rather than
copy-paste-literal siblings — the AST scanner's absence of a 3+ match for
these files is itself the evidence, not merely an assertion) were left
byte-for-byte unmodified, `survives-as-itself`. This includes the entire
protected `test_resolution_matrix.py` (A5) and `test_cors.py` (A3) — both
reviewed in full and confirmed to already read as the "golden path" /
"quartet" the brief names; forcing either into a table risked exactly the
"survives recognizably" promise this artifact exists to protect, for no
coverage gain the requirements doc gates on.

**Verification.** Ran the full gated `run-unit-tests.sh --coverage`
against this worktree (`PGAI_DEV_TREE_PATH` pinned to the worktree,
matching the precedent CODER-20260806-002/009/010 established): exit 0,
2894 passed / 3 skipped — identical to the pass/skip count already
established by tickets 008/009/010 before this ticket started (no test
lost, none gained). Four Python modules this ticket's test changes
exercise (`pgai_agent_kanban/ops/write.py`, `pm-agent/pm_materialize.py`,
`scripts/lib/metrics_aggregator.py`, `scripts/lib/metrics_csv_writer.py`)
showed apparent covered-line/branch set drift when diffed against the
CODER-20260806-002 baseline JSON at the FULL-SUITE level; re-running each
affected file's tests in isolation (`pytest -p no:randomly`, single file,
targeted `--cov`) against both the pre-consolidation and post-consolidation
versions of the corresponding test file produced byte-identical
`executed_lines`/`executed_branches` sets in every case — confirming the
full-suite-level drift is pre-existing test-order-dependent coverage noise
from unrelated test files sharing these modules (the same phenomenon
CODER-20260806-009 already documented for `dashboard/scan_attention.py`),
not a regression introduced by this ticket. No production source file was
touched by this ticket (`git diff ai_rc/v2.3.0 --name-only` shows only
`team/tests/unit/*.py` paths), so Step 4b (Possible Stale Assertions) is
trivially "none" and the coverage-gate claim rests entirely on this
isolated-rerun evidence rather than the noisy full-suite diff.

**Design choices not explicitly sanctioned by the brief, documented for
the operator:** (1) resolved the ambiguous "not claimed by tickets 7, 8,
or 9" wording in this ticket's own README (7 is the provenance-lint-guard
ticket, which claims no unit test files, and no ticket 9 in the numeric
sequence claims unit files either — the README's phrasing appears to
predate ticket 010's introduction) to mean "not claimed by 008, 009, or
010," matching all three siblings' own disposition notes, which already
consistently describe 011 as owning the trust-boundary quartet and
resolution golden paths; (2) closed the three unclaimed
`team/pgai_agent_kanban/api/tests/` Section-B rows without expanding into
a full `api/tests/` sweep, flagging the remainder of that directory as an
open domain-boundary question for the operator/013 rather than silently
absorbing or silently ignoring it.

## CODER-20260806-012 — Integration-Suite Consolidation

Scope: all 23 `team/tests/integration/test_*.py` files (per this ticket's own
README "Required Output"). This closes out every remaining `pending` row in
Sections A and B above that lives under `team/tests/integration/` — the
sibling unit-domain tickets (008-011) resolved every `team/tests/unit/` and
`team/pgai_agent_kanban/api/tests/` row; this ticket is the integration
counterpart.

**Methodology.** Wrote a throwaway AST-based structural-shape scanner
(same approach CODER-20260806-011 used for the unit-suite catch-all:
Python `ast` module, blank each `test_*` function's literal `Constant`
nodes, strip name/decorators/args, group functions within the same
file/class whose normalized `ast.dump()` is byte-identical) and ran it
over all 23 integration files. A matched group is mechanical,
high-confidence evidence of "same assertion shape, different literal
input" — the same standard the unit-domain tickets already established.
Every flagged group was read in full before acting; groups were left
unconsolidated where the fixture setup, target code path, or assertion
target differed despite superficial AST similarity.

**What was consolidated.** Six same-shape groups (25 functions -> 6
parametrized tests, 25 cases preserved) across six files, each verified by
`pytest --collect-only` case-count parity against `git show
ai_rc/v2.3.0:<file>` (every touched file's post-edit collected-item count
equals its pre-edit count) and a green `pytest` run of the file:

- `test_cm_release_synthetic.py::TestResumeFromPartialRun` (protected
  fixture, Section A1) — 3 functions (`test_resume_after_squash_committed`,
  `test_resume_after_notes_committed`, `test_resume_after_changelog_committed`)
  -> one `test_resume_after_partial_run_completes_release` parametrized over
  `(version, project_name, stop_after)`, 3 cases preserved as parametrize IDs
  (`post_squash_no_notes_no_changelog_no_tag`, `post_notes_no_changelog_no_tag`,
  `post_changelog_no_tag`). The docstrings from all three original functions
  were merged into the parametrized function's docstring so the per-case
  narrative (which release.sh boundary each case simulates) is not lost.
- `test_operation_contracts.py::TestIntakeContracts` — 3 functions -> 1
  parametrized test, 3 cases (`bug_prefixed_file_deposits_to_bugs_directory`,
  `requirements_prefixed_file_deposits_to_requirements_directory`,
  `priority_prefixed_file_deposits_to_priority_directory`).
- `test_create_project.py::TestCreateProjectDevTreeAndGitRepoFlags` — 2
  functions -> 1 parametrized test, 2 cases.
- `test_eligibility_semantics.py::TestSkipLogging` — 2 functions -> 1
  parametrized test, 2 cases.
- `test_operator_commands.py::TestScrubbedEnvCensusCloser` — 2 functions ->
  1 parametrized test, 2 cases.
- `test_overwatch_resolution_stage.py::TestClassifyHaltFile` — 2 of 4
  functions (`test_ship_policy_header`, `test_operator_content`) -> 1
  `test_classify_by_content` parametrized test, 2 cases (`ship_policy_header`,
  `operator_content`); the other 2 functions in this class
  (`test_absent_file`, `test_empty_file_is_operator`) have a different
  fixture shape (no writer callable) and were left standalone. Note: this
  class exercises the `classify_halt_file` helper, not the Section A5
  "resolution golden path" module-level functions in this same file — those
  10 functions were reviewed and left untouched (see below).

`pytest --collect-only` verified case-count parity for all six files
against `ai_rc/v2.3.0`: `test_cm_release_synthetic.py` 9->9,
`test_operation_contracts.py` 27->27, `test_create_project.py` 24->24,
`test_eligibility_semantics.py` 15->15, `test_operator_commands.py` 40->40,
`test_overwatch_resolution_stage.py` 16->16.

**What was reviewed and left unmodified.** The remaining 17 of the 23
integration files, plus the untouched classes/functions inside the six
files above, were reviewed in full (module docstrings, class/function
names, fixture bodies) and judged to exercise genuinely distinct scenarios
— different git-repo/RC-branch setups, different hook-failure shapes,
different CLI verbs, different N-ticket wake-dispatch closure counts, or
different protected-fixture promises — rather than copy-paste siblings
varying only a literal:

- `test_rc_lifecycle.py` (A1 fidelity-gate rows; also carries 3 Section-B
  BUG-0111/BUG-0025 rows) — left byte-for-byte unmodified; each
  `TestFidelityGate`/`TestFullRCLifecycleLocalOnly`/`TestTesterVerificationGate`
  test exercises a distinct RC-lifecycle boundary.
- `test_cancel_rc_frees_version.py` (A2 cancel-drill shapes; also
  Section-B BUG-0092) — left byte-for-byte unmodified; each test exercises
  a distinct cancel/reopen/history-record scenario, not a literal-only
  variant.
- `test_cert_branch_normalization_guard.py` (Section-B BUG-0037) — left
  byte-for-byte unmodified; protected bug-born file, no AST-matched group.
- `test_cert_one_ticket_testing_only_pipeline.py`,
  `test_cert_two_ticket_testing_only_pipeline.py`,
  `test_cert_three_ticket_testing_only_pipeline.py` — despite the
  suggestive naming (one/two/three), these certify structurally different
  N-ticket wake-dispatch-closure pipelines (different ticket counts,
  different closure-ordering assertions per file, 694-1459 lines each);
  not copy-paste siblings varying a literal. Left unmodified.
- `test_cross_cutting.py`, `test_discovery_pipeline.py`,
  `test_discovery_race_guard.py`, `test_document_pipeline.py`,
  `test_overwatch_wake_driven_fixtures.py`, `test_reset_force.py`,
  `test_retry_counter_bundle_family.py`, `test_api_server_fixture_leaks.py`,
  `test_changelog_freshness_gate_cli_recovery.py` — each reviewed in full;
  no AST-matched group of 2+ same-shape functions found, or a matched pair
  was read and judged a false positive (same Python-level shape, different
  subject matter under test). Left unmodified.
- `test_overwatch_resolution_stage.py`'s Section A5 golden-path rows (the
  10 module-level `test_*` functions listed above) and
  `test_operation_contracts.py`/`test_create_project.py`/
  `test_eligibility_semantics.py`/`test_operator_commands.py`'s remaining
  classes not named above — reviewed and left unmodified for the same
  reason; each is already organized into behavior-distinct scenarios.

**Verification.** Ran the full gated `run-integration-tests.sh --coverage`
against this worktree (`PGAI_DEV_TREE_PATH` pinned to the worktree, matching
the precedent CODER-20260806-002/009/010/011 established): exit 0, all
lint/freshness/compat gates green, 284 tests collected and passed. `git
diff ai_rc/v2.3.0 --name-only -- tests/integration/` shows only the six
touched test files; no production source file under `pgai_agent_kanban/`,
`pm-agent/`, or `scripts/lib/` was modified by this ticket, so Step 4b
(Possible Stale Assertions) is trivially "none."

**Design choices not explicitly sanctioned by the brief, documented for the
operator:** consolidated only AST-matched groups with 2+ functions rather
than requiring the unit-suite tickets' informal "3+" preference — the
integration suite is much smaller (23 files vs. 82-136 per unit ticket), so
a 2-function match was judged worth the same table-driven treatment rather
than left as a near-miss; each 2-case consolidation above still passed the
same "genuinely a copy-paste sibling, not superficial shape overlap"
manual read as every larger group.

## Appendix — Screened Out (bug-shaped IDs used as fixture data, not bug-born tests)

These files matched the `BUG-\d{4}` grep but were determined, on inspection,
to use bug-shaped identifiers purely as synthetic sample content for
generic pipeline/mechanics tests (the sanctioned "bug-shaped VALUES used as
test DATA" exception in SOP.md Anti-pattern 6). They are named here so the
exclusion is auditable, not silent. A future reviewer who believes one of
these actually locks in a specific bug's acceptance should move it into
Section B rather than leave it here.

- `team/pgai_agent_kanban/api/tests/test_board.py` — `BUG-0001`/`BUG-0002`/`BUG-9999` are sample bug-intake filenames used to build board-rendering fixtures.
- `team/pgai_agent_kanban/api/tests/test_fidelity.py` — `BUG-0001` is a sandbox fixture key (`_SANDBOX_BUG_KEY`) for the twin-sandbox checksum harness.
- `team/tests/integration/test_cert_one_ticket_testing_only_pipeline.py`, `test_cert_two_ticket_testing_only_pipeline.py`, `test_cert_three_ticket_testing_only_pipeline.py` — `BUG-0059`/`BUG-0068` are sample intake filenames for the N-ticket wake-dispatch-closure certification fixtures.
- `team/tests/integration/test_create_project.py` — `BUG-0101` is sample intake content used to exercise the create-project scaffolding.
- `team/tests/integration/test_cross_cutting.py` — `BUG-0001`/`BUG-0070`-`BUG-0096` are synthetic per-project bug filenames used to prove multi-project isolation and halt-gate mechanics; the tests are about isolation semantics generically, not any one bug's fix.
- `team/tests/integration/test_discovery_pipeline.py` — `BUG-0001`-`BUG-0101` are synthetic bug-intake fixture filenames exercising the bundling pipeline's generic mechanics (status transitions, halt gates, version-ceiling gate, per-project isolation, terminal-state filtering, category-first ordering).
- `team/tests/integration/test_discovery_race_guard.py` — `BUG-0092`/`BUG-0100`-`BUG-0201` are synthetic sequential fixture IDs for the settle-guard and cancel/reselect race-condition drills.
- `team/tests/integration/test_operation_contracts.py` — `BUG-7001`/`BUG-9001`/`BUG-9002` are deliberately out-of-range synthetic sentinel IDs for the read/intake contract tests.
- `team/tests/integration/test_operator_commands.py` — `BUG-0001`-`BUG-0202`/`BUG-0082` are synthetic bug-intake fixture filenames exercising the generic show/reset/close/intake CLI verbs.
- `team/tests/integration/test_overwatch_resolution_stage.py` — `BUG-0001` is a synthetic finding filename inside the golden-fixture evidence (see Section A5 for the actual golden-fixture rows).
- `team/tests/integration/test_overwatch_wake_driven_fixtures.py` — `BUG-0001`/`BUG-0002` are synthetic "cargo" bug filenames for the ceiling/counter-keying wake-driven fixtures.
- `team/tests/integration/test_retry_counter_bundle_family.py` — `BUG-0001`/`BUG-0002`/`BUG-0099` are synthetic cargo-set filenames for the retry-counter/bundle-family fixtures.
- `team/tests/unit/test_auto_resolutions_window.py` — `BUG-0041-foo.md` is a synthetic findings-list sample value.
- `team/tests/unit/test_bug_scanner.py` — `BUG-0001`-`BUG-0037` are synthetic filenames exercising the generic bug-scanner module (parsing, sorting, claiming next id).
- `team/tests/unit/test_bundle_family.py` — `BUG-0001`/`BUG-0002`/`BUG-0003`/`BUG-9999` are synthetic cargo-set filenames for the bundle-family-key computation tests.
- `team/tests/unit/test_changelog_writer.py`, `test_changelog_writer_real_data.py` (except the row already listed in Section B's title-fix file) — `BUG-NNNN` values are synthetic `bug_num` sample data fed into the changelog renderer; the file's own header states it covers "acceptance criteria from the task README," not one bug (the `TestRealDataChangelog::test_bug_0023_file_carries_public_id_ki_1_0_0_1` test reads real ledger data for `BUG-0023` but is asserting the changelog writer's real-data fidelity generically, not locking in a BUG-0023-specific behavioral fix).
- `team/tests/unit/test_dashboard.py` — `BUG-0001`/`BUG-0002` are synthetic bug-intake sample filenames for the reason-sidecar and rejected-dir dashboard rendering tests.
- `team/tests/unit/test_ledger.py`, `test_ledger_filename_contract.py` — `BUG-0041-foo.md`/`BUG-0042-bar.md`/`BUG-0009-fix-thing.md` are synthetic findings/filename sample values for the ledger append and filename-contract tests.
- `team/tests/unit/test_lint_comment_provenance.py` (all rows except `test_version_goal_provenance_flagged`, listed in Section B) — `BUG-0042`/`BUG-0043`/`BUG-0099` are synthetic "genuine violation" sample strings exercising the provenance lint tool's own detection logic.
- `team/tests/unit/test_metrics_csv_writer.py` — `BUG-0099`/`BUG-0100` are synthetic sample bug counts for the rollup-row test.
- `team/tests/unit/test_ops_core.py` — `BUG-0001`-`BUG-0101` are synthetic bug-intake sample filenames exercising the generic ops-core module (prefix matching, intake state, deposit, close, delete).
- `team/tests/unit/test_overwatch_changelog_drift_bash.py` — `BUG-9999` is a synthetic sample bug id for the drift-detection/regeneration fixture.
- `team/tests/unit/test_pollution_guard.py` — `BUG-0012` appears only inside an embedded shell snippet documenting a specific guard clause; the test itself exercises the guard's general path-matching logic, not a BUG-0012-specific regression case.
- `team/tests/unit/test_po_agent_draft_mode.py` (all rows except the two listed in Section B) — `BUG-0073` in the docstring of `test_absent_output_flag_dry_run_still_works` is a stray reference to a nearby section header comment, not the function's own subject (the function is a generic dry-run smoke test).
- `team/tests/unit/test_reports.py` — `BUG-0001`-`BUG-0007` are synthetic sample bug items exercising the generic reports-parsing module.
- `team/tests/unit/test_resolution_matrix.py`, `test_wake_dispatch_role_assignment.py`, `test_write_rc_state_attempt.py` — `BUG-0001`/`BUG-0002`/`BUG-0042`/`BUG-0043` are synthetic sample finding ids inside otherwise-generic parsing/dispatch/state-write tests (the resolution-matrix golden-fixture rows are already listed under Section A5).

## Spot-check readiness

Per the requirements doc's Acceptance #4 ("spot-run of five named fixtures
green"), the following five rows are recommended as the spot-check set for
the coverage-gate ticket, one from each of five distinct protected-fixture
categories:

1. `team/tests/integration/test_rc_lifecycle.py::TestFidelityGate::test_fidelity_gate_halts_when_main_diverges_mid_rc` (A1)
2. `team/tests/integration/test_cancel_rc_frees_version.py::TestCancelRCFreesVersion::test_reopen_same_version_after_cancel_succeeds` (A2)
3. `team/pgai_agent_kanban/api/tests/test_cors.py::test_cors_non_loopback_origin_blocked` (A3)
4. `team/tests/unit/test_changelog_writer.py::TestDisclosureScope::test_bug_affecting_published_version_is_disclosed` (A4)
5. `team/tests/unit/test_resolution_matrix.py::test_golden_facts_match_cancel_and_rebundle` (A5)
