# Status

## Task
CODER-20261011-088-upgrade-preserve-state

## Participant
Claude

## Role
CODER

## Model
claude-opus-5-5

## State
DONE

## Summary
Agent: CODER, Model: claude-opus-5-5. Merged --no-ff into ai_rc/v2.37.7 at merge commit 6fa250a3; feature branch deleted (git branch -d). Resumed after the first attempt was killed at the per-task timeout; its implementation commit 719be920 was kept, and this session added tests (e9530a3f, db2af85d) and one fix (6e1f8cb9). What changed: (1) upgrade.sh without --wake-tier never writes the crontab. _detect_installed_tier is report-only and returns none when no non-comment wake.sh/wake-batch.sh/wake-now.sh job line exists. A header with no wake job no longer falls back to small; unmatched wake lines report custom instead of an invented tier. (2) With an explicit tier, install-crontab.sh and safe_overwrite_crontab rewrite only kanban-managed lines: a new marked '# BEGIN/END pgai-agent-kanban managed block: <root>' block, unmarked lines naming the install root, and shipped-template commentary. Every other line and any other root's block is kept. The merge is idempotent, and an unterminated block is refused with the crontab unchanged. (3) The pseudocron hook reads its tier from pseudocron.cfg's '# Tier:' header, is skipped when there is none, and calls install-pseudocron.sh with the new --keep-existing flag, so a hand-tuned cfg is never overwritten. (4) A skipped deposit (no TTY and no --force, or an N/q answer) stops the run before the scheduler hooks, the VERSION stamp and the migrations. It exits 1 through the shared emitter: error upgrade.partial-deposit, BLOCK-WORTHY, CENSUS-ANCHOR upgrade-partial-deposit-01 indexed in docs/blocking-site-census.md, and a --force re-run command as the remedy. A directory whose shipped files are all identical in place counts as current, not as differing. (5) Migrations run from the source tree's team/scripts/migrations/run-migrations.sh with PGAI_AGENT_KANBAN_ROOT_PATH set to the target root; run-migrations.sh itself needed no change. Header comment, --help and install-crontab/install-pseudocron usage text were updated in the same commits. Design choice: of the two remedies BUG-0734 offers, I implemented its first Expected (stop and exit non-zero), not the last-migrated marker. Tests: integration test_upgrade_partial_deposit.py 4 passed. Unit test_upgrade_preserves_scheduler_state.py plus test_install_crontab_keeps_foreign_lines.py 11 passed. Against the source-branch scripts, 3 of 4 and 9 of 11 failed, which shows the tests detect the bugs. Existing tests passed with assertions unchanged: test_upgrade_retirement_behavioral.py plus test_claude_env_install_seeding.py 9; 11 related upgrade unit files 101; test_migration_runner_upgrade_wiring.py 13. run-unit-tests.sh --pre-merge --base ai_rc/v2.37.7 passed all 11 checks after its echo-pipe-grep-q lint caught a printf|grep -q pipefail defect in the new detection code, fixed in 6e1f8cb9. Provenance lint: 0 findings. docs-manifest check: ok. Census lint: no new findings in the changed files (42 before and after, all pre-existing). Runnable-remedy lint: ok. Scripts are mode 100755. Concerns for TESTER/WRITER: (a) docs/pseudocron.md lines 15-18 and docs/docker.md around lines 296-298 still say an upgrade re-applies the crontab by default; that is now false (WRITER). (b) upgrade.sh's temp-root guard makes the scheduler hooks unreachable from sandboxed tests (see Instruction Conflicts). (c) Pre-existing: safe_overwrite.sh's 'exec 3<>/dev/tty 2>/dev/null' TTY probe still prints 'No such device or address' to stderr when there is no TTY; not changed.

## Artifacts
- Merge commit 6fa250a3 on ai_rc/v2.37.7 (feature commits 719be920, e9530a3f, db2af85d, 6e1f8cb9)
- team/scripts/upgrade.sh, team/scripts/install-crontab.sh, team/scripts/install-pseudocron.sh, team/scripts/lib/safe_overwrite.sh, docs/blocking-site-census.md
- team/tests/fixtures/upgrade_sandbox.py (shared sandbox + crontab shim + process-group runner)
- team/tests/integration/test_upgrade_partial_deposit.py
- team/tests/unit/test_upgrade_preserves_scheduler_state.py
- team/tests/unit/test_install_crontab_keeps_foreign_lines.py
- artifacts/stale-literal-risks.md (full coder-stale-literal-check output)
- artifacts/task-notes.md

## Blockers
none

## Needs Human
no

## Next Recommended Step
Work merged to ai_rc/v2.37.7; downstream tasks may proceed. The paired WRITER ticket (or TESTER filing) should update the stale upgrade-crontab sentences in docs/pseudocron.md (lines 15-18) and docs/docker.md (around lines 296-298) named in the Summary.

## Instruction Conflicts
- Acceptance "an upgrade with an empty shim crontab does not report a pseudocron failure": upgrade.sh's existing temp-root guard skips the crontab and pseudocron hooks for any root under a temp directory, and every sandbox root the test rules require is one. So no sandboxed upgrade can reach install-pseudocron.sh. Reading taken: the upgrade-level test asserts the stated outcome (no failure reported, pseudocron.cfg byte-identical), and test_keep_existing_leaves_a_hand_tuned_cfg_byte_identical drives install-pseudocron.sh --keep-existing, the exact mode the hook now passes, directly. The guard was left unchanged; it is outside this task's scope.

## Possible Stale Assertions
none (grepped team/tests and docs for the changed literals "will preserve", "Detected installed tier", "Crontab: applying tier", "Pseudocron: installing tier", "Pseudocron: ready (tier", "force_overwrite=true", "applying new template", "upgrade, preserve tier", "To install manually", "Wake tier:", "applied unconditionally", "Skipped (no-TTY)": the only hits are in this task's own new test)

## Stale Literal Risks
Full output: artifacts/stale-literal-risks.md (364 lines). Literal list with hit counts: "none" 296, "custom" 27, " --force" 22, "small" 3, "upgrade.partial-deposit" 2, "$KANBAN_ROOT" 2, "${3:-}" 1, "${2:-}" 1. All are generic values or this task's own new tests; none judged substantive.

## Zero Progress
reason_code: none
count: 0
limit: 3
detail: none
