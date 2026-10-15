# Verification Report: ai_rc/v2.23.8

**Task:** TESTER-20260916-042-verify-2-23-8
**Requirements doc:** $HOME/pgai_agent_kanban/projects/pgai-agent-kanban/requirements/v2.23.8-bugfix-bundle-20260916.md
**RC commit verified:** b74141f47c54caf82f6e572c5f8f2ae0a603d4f4 (detached HEAD in the task worktree, equal to ai_rc/v2.23.8)
**Worktree:** /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260916-042-verify-2-23-8

## Summary

v2.23.8 is a single-item bug bundle: the OVERWATCH empty-file check was quarantining the bug-id claim protocol's own zero-byte `.claim-NNNN.lock` sentinels, filing a self-inflicted report about them and consuming claim numbers. The RC narrows the check's scan to report files by excluding every dotfile, adds a bounded prune for the `.claim-NNNN.lock.empty.orphan` residue earlier firings left behind, adds a four-case unit test, and corrects the three prose surfaces that described the old scope. I verified the RC at commit `b74141f` in the task worktree: pre-flight clean, both gated runners green, every acceptance criterion in the requirements doc and in the two implementing tickets exercised as a live run against synthetic fixtures in a throwaway kanban root — including the original failure mode reproduced against the pre-RC script and confirmed absent from the RC script. No bug, gap, or stale assertion was found. Two `pass-with-caveat` notes (a bash mirror of the Python claim-lock regex in the prune path, and the prune's lack of an `-empty` predicate) are recorded with their reasoning and neither warrants a filing. The one open bug cited below, BUG-0305, was filed by the unit runner's own aggregate filer to track two quality-class hygiene findings and does not move the recommendation.

## Recommendation

PASS

## Systemic Risk

low

## Findings

Recorded per verification step below; the numbered `### Finding N` blocks follow the checkpoints.

### Checkpoint 1 — pre-flight and structural (complete)

- Pre-flight (TESTER Step 2/3): every task folder reading `## State: DONE` with a named `## Feature Branch` was scanned; no feature branch still exists locally (CODER and WRITER delete after merging), so Check 1 and Check 2 pass trivially for all. Worktree `git rev-parse HEAD` = `git rev-parse ai_rc/v2.23.8` = `b74141f47c54caf82f6e572c5f8f2ae0a603d4f4`; `git status --porcelain` empty; `git stash list` empty.
- Release state (Step 6.1): `## Active RC` `v2.23.8`, `## RC Opened At` `2026-09-16T18:36:25+00:00`, `## RC Opened By Task` `CM-20260916-041-open-rc-2-23-8`, `## Last Released` `v2.23.7` (well-formed). `pp_last_released_version "pgai-agent-kanban"` returns `v2.23.7`; `semver_gt v2.23.8 v2.23.7` is true.
- Diff scope (Step 6.2): exactly five files changed between `ai_main` and `ai_rc/v2.23.8` — `subagents/overwatch.md`, `team/roles/OVERWATCH.md`, `team/scripts/lib/overwatch-checks/README.md`, `team/scripts/lib/overwatch-checks/check-empty-files.sh`, `team/tests/unit/test_overwatch_empty_files_report_scope_bash.py`. `check-empty-files.sh` remains mode 0755.

### Checkpoint 2 — behavioral verification (complete)

All runs used a throwaway kanban root under `$PGAI_AGENT_KANBAN_TEMP_DIR` with `PGAI_TASKS_DIR`/`PGAI_PROJECT_ROOT` cleared, per TESTER.md "Live-install safety". The live install was never the target of any run.

Fixture set (synthetic project `fixtureproj`): empty `.claim-0136.lock`, empty `.bug-claims.log`, empty `BUG-0500-incomplete-report.md`, empty `priority/PRIORITY-0011-empty-thing.md`, empty `.claim-0140.lock.empty.orphan`, non-empty `BUG-0499-real.md.empty.orphan`.

Observed on the RC script:
- `.claim-0136.lock` present and unrenamed after the run.
- `.bug-claims.log` present and unrenamed after the run.
- `BUG-0500-incomplete-report.md` → `BUG-0500-incomplete-report.md.empty.orphan`, action logged `renamed-to-empty-orphan`, backup taken.
- `PRIORITY-0011-empty-thing.md` → `.empty.orphan`, logged, backed up.
- Exactly one consolidated report filed: `BUG-0501-auto-empty-files-20260916.md`, listing both renamed files and neither dotfile.
- `.claim-0140.lock.empty.orphan` pruned (deleted after backup), action logged `pruned-claim-lock-residue`.
- `BUG-0499-real.md.empty.orphan` untouched.
- Exit code 0.

Negative / guard cases:
- Fixture containing only an empty `.claim-NNNN.lock` and an empty `.bug-claims.log`: the RC script prints `no 0-byte report files or claim-lock residue found`, files no report, and writes no action log. (Acceptance criterion 1's "files no report" half, isolated.)
- Second consecutive run over the post-fix state is a clean no-op — the prune is idempotent.
- `--dry-run` over a fixture with one empty report and one residue file narrates both intentions and mutates nothing on disk.
- Five-digit `.claim-12345.lock` and non-conforming `.claim-123.lock` both survive (the dotfile rule, not a digit-count rule, is what protects them).
- A residue-shaped file in `priority/` is pruned as well as one in `bugs/`.

Original failure mode reproduced (TESTER.md Step 6.5 / anti-pattern 6): the pre-RC script (`git show ai_main:team/scripts/lib/overwatch-checks/check-empty-files.sh`, run from a copy of the lib tree against the same fixture) reported `found 2 empty file(s)`, renamed `.claim-0136.lock` → `.claim-0136.lock.empty.orphan` and `.bug-claims.log` → `.bug-claims.log.empty.orphan`, and filed a report about them. The RC script over the same fixture reports zero and leaves both in place. The defect is demonstrably fixed, and the normal case (an empty bug/priority report is still quarantined and reported) is demonstrably unbroken.

### Checkpoint 3 — gated suites (complete)

`UNIT_EXIT=0` — `bash team/scripts/run-unit-tests.sh --coverage`, 4648 passed, 31 skipped in 889.54s. Breakage-class gate OK (466 python, 269 shell files). Every quality-class lint reported OK except the hygiene lint (2 provenance findings, recorded below).
`INTEGRATION_EXIT=0` — `bash team/scripts/run-integration-tests.sh --coverage`, 515 passed, 1 skipped in 584.68s.
Output captured at `/tmp/pgai_kanban_tmp/tester/unit.log` and `/tmp/pgai_kanban_tmp/tester/integration.log`.

The RC's new test file appears in the unit run as `tests/unit/test_overwatch_empty_files_report_scope_bash.py ....` — four tests, all passing, and the orphan-test lint confirms it is reachable from the runner's pytest argv.

## Coverage (informational only — never enters the verdict)

Source: the `totals` object in each runner's JSON, field names verified against the generated files before extraction (`percent_statements_covered_display`, `percent_branches_covered_display`).

- Unit suite — line 77 %, branch 63 % (blended `TOTAL` column: 73 %).
- Integration suite — line 10 %, branch 8 % (blended `TOTAL` column: 9 %).

---

## Findings

### Finding 1: Bundled item BUG-0304 is addressed — the check no longer quarantines claim-lock sentinels

**Category:** pass

The bundle's single item is the report OVERWATCH filed about a 0-byte file in `bugs/` that turned out to be the bug-id claim protocol's own `.claim-NNNN.lock` sentinel. The RC narrows `overwatch_check_empty_files`'s scan with `_cef_is_report_candidate`, which rejects any basename starting with `.`, and adds a bounded prune (`_cef_is_claim_lock_residue`, `_cef_do_prune`) for the `.claim-NNNN.lock.empty.orphan` residue earlier firings left behind. Both behaviors were exercised live (Checkpoint 2) rather than read: the sentinel survives, the residue is deleted after a backup, and every other `.empty.orphan` file stays as evidence. The item's own ask ("reviewed and either deleted or completed") is satisfied by the prune for the residue class.

### Finding 2: Acceptance criterion — "All Python files compile cleanly"

**Category:** pass

`python3 -m py_compile` over all 488 tracked `.py` files in the RC tree: 0 failures. The gated unit runner's breakage-class gate independently reports `0 breakage-class failures` across 466 python files under `team/`.

### Finding 3: Acceptance criterion — "All bash files pass syntax check"

**Category:** pass

`bash -n` over all 280 tracked `.sh` files in the RC tree: 0 failures. `bash -n team/scripts/lib/overwatch-checks/check-empty-files.sh` exits 0 specifically (the implementing ticket's own criterion).

### Finding 4: The implementing ticket's six acceptance criteria

**Category:** pass

Each was exercised as a live run against synthetic fixtures in a throwaway root (Checkpoint 2), not inferred from the source:

1. Claim-lock sentinel present and unrenamed, no report filed — verified, including in isolation where the sentinel is the only 0-byte file present.
2. Empty `BUG-NNNN-<slug>.md` still renamed to `<name>.empty.orphan`, action logged, one consolidated report filed — verified; the filed report listed both quarantined report files and neither dotfile.
3. Empty dotfile (`.bug-claims.log`) not quarantined — verified.
4. Prune bounded to claim-lock-shaped `.empty.orphan` residue, every other `.empty.orphan` left in place, prune logged through `overwatch_log_action` — verified (`pruned-claim-lock-residue`), including the backup taken before deletion.
5. New test under `team/tests/unit/` named for the behavior, covering all four cases, discovered and passing under the unit runner — verified: `test_overwatch_empty_files_report_scope_bash.py`, 4 passed, orphan-test lint clean.
6. `bash -n` on the script and `python3 -m py_compile` on the test both exit 0 — verified.

### Finding 5: The three documentation surfaces match the shipped behavior

**Category:** pass

Read in full and compared line-by-line against the merged script (TESTER.md Step 6.3 — documentation-layer files get particular care, because an error here fails the first time an agent reads the file rather than in a test):

- `team/scripts/lib/overwatch-checks/README.md` catalog row 2 — names report files as the scan scope, states dotfiles are never scanned at any size, names the machinery the dot marks, corrects the rename suffix from `.orphan` to the `.empty.orphan` the code actually writes, and records the bounded prune plus the every-other-orphan-stays rule. All four claims match the code.
- `team/roles/OVERWATCH.md` whitelist row 1 and `subagents/overwatch.md` check-list item 1 — same scope in each file's own register, no contradiction with the README or the script.
- `grep -i "zero-byte files in"` across the three files returns nothing (criterion 4).
- `git diff --name-only ai_main..ai_rc/v2.23.8` lists exactly the five expected files, so criterion 5 ("no file outside those three is modified by this ticket") holds for the documentation ticket's half of the diff.
- Spot-check beyond the three named surfaces: `grep -rn "check-empty-files" --include=*.md` across the whole tree finds no fourth prose surface still describing the old scope.

### Finding 6: The claim-lock filename shape is mirrored in bash for the prune path

**Category:** pass-with-caveat

The implementing ticket constrains the work with "the claim-lock sentinel filename shape is owned by `team/pm-agent/lib/bug_scanner.py` — read it there rather than restating a pattern that can drift." The protection path honors this completely: `_cef_is_report_candidate` restates nothing, excluding every dotfile rather than matching the sentinel shape, so a future change to `_CLAIM_LOCK_RE` cannot expose a live claim lock to quarantine. The prune path does restate it — `_cef_is_claim_lock_residue` carries `^\.claim-[0-9]{4,}\.lock\.empty\.orphan$`, a hand-transcribed bash mirror of the Python `^\.claim-(\d{4,})\.lock$`. The two agree exactly today (verified by reading both), and the comment above the function names `bug_scanner.py` and `_CLAIM_LOCK_RE` as the owner.

Recorded as a caveat rather than a gap because the drift failure mode is inert: if the Python shape changes, the bash mirror stops *matching* and the prune silently becomes a no-op — leftover residue accumulates, nothing live is deleted and nothing is corrupted. A bash consumer cannot import a Python regex, so some mirror is unavoidable for a prune that must be shape-bounded; the alternative reading (that the constraint is literally unmet) is recorded here so a reader can weigh it rather than having it decided silently.

### Finding 7: Prune deletes a residue-named file even if it carries content

**Category:** pass-with-caveat

The residue `find` has no `-empty` predicate, so a file named `.claim-NNNN.lock.empty.orphan` that somehow holds content is pruned too. Exercised: a non-empty residue file was deleted, and its content was preserved in `overwatch/backups/<timestamp>/` by the `overwatch_backup_file` call the prune makes before `rm`. Residue files are 0-byte by construction (they are renamed 0-byte sentinels), so this is a theoretical shape; the backup-before-modify contract makes it recoverable rather than destructive. Noted, not filed.

### Finding 8: Both gated runners green

**Category:** pass

`UNIT_EXIT=0` and `INTEGRATION_EXIT=0` from `team/scripts/run-unit-tests.sh --coverage` and `team/scripts/run-integration-tests.sh --coverage`, run from the dev-tree root. This is the PASS evidence — not a pytest "N passed" line. No test failed, so there is no stale-assertion analysis to perform; CODER's `## Stale Literal Risks` section flagged only the new `pruned-claim-lock-residue` literal asserted in the test authored beside it, which is consistent with a green suite.

### Finding 9: Steps with nothing to verify

**Category:** pass

- Step 6.8 (synthetic release simulation) — skipped with a note: the diff touches no release-lifecycle file. The only `team/scripts/` paths in the diff are `lib/overwatch-checks/check-empty-files.sh` and that directory's README, neither of which is in the RC open/close cycle.
- Step 6.10 (prereq dependency graph) — no cycles; every referenced task ID resolves to an existing folder; bookends correct (`CM-20260916-041-open-rc-2-23-8` opens, `CODER-039` → `WRITER-040` → `TESTER-042` → `CM-20260916-044-release-2-23-8` closes, the release task's prerequisites naming all three).
- Step 6.13 (skip-cites-real-bug) — no placeholder skip shapes anywhere in `team/tests/`; every `BUG-NNNN` token found in the test tree is either a synthetic fixture the test itself writes into a temp dir or a fixture filename, not a skip citation. The runner's own `lint_skip_bug_gate` reported `0 missing files`.
- Step 6.9 (constraint negative checks) — no secret-shaped, credential-shaped, or banned pattern introduced by the diff; the new test uses `tmp_path` throughout and references neither a bare `/tmp` path, `$HOME`, nor the live kanban root.
- Step 6.11 (spot-check) — `Active RC v2.23.8` is consistent with an existing local `ai_rc/v2.23.8` branch and a `v2.23.7` last release (no state drift); the new mechanism is covered by both its own test and this report's live runs (not an untested mechanism); no prior-gap regression surfaced.

## Lint Findings (non-blocking)

- `hygiene` (class `provenance`, 2 instances, both suites) — `team/tests/unit/test_overwatch_empty_files_report_scope_bash.py:155` and `:249` cite the fixture filenames `BUG-0001-something.md` and `BUG-0005-something.md` in docstrings. Cites **BUG-0305-hygiene-lint-provenance** (`## Status` `open`), filed automatically by the unit runner's aggregate filer during this run at 19:08; the integration runner deduplicated against it and filed nothing further, so one bug covers the class across both suites. No second filing from me.

Context for whoever picks BUG-0305 up: these two tokens are fixture *values* illustrating the report-filename shape the check operates on, which `SOP-core.md` ("Naming") sanctions as a usage example, and the implementing CODER left them deliberately rather than suppressing them. The remedy may legitimately be the allowlist sidecar rather than a reword. Per TESTER.md Step 5c this bug tracks a quality-class finding only: it does not enter the Step 8 decision tree, does not raise `## Systemic Risk`, and does not move the recommendation off `PASS`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=9d freshness=stale — Both freshness conditions fail (9 days exceeds the 7-day window, and the record's v2.9.20 is not the v2.23.8 under verification), and the red verdict belongs to that run, which filed its own report BUG-0167-cold-build-claude-abliterated-failure for the `docker/claude-abliterated`, `podman/claude-abliterated` and `podman/rocky9` pairs — cited here, not re-filed, and nothing is known about whether this RC's images build.

## Autonomous criterion

Manual interventions required: none.

1. Tasks force-promoted from WAITING to BACKLOG by hand, bypassing the wake script's prerequisite evaluation? **NO.** This task's own `status.md` records the promotion as the wake script's ("Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied"), `CM-20260916-044-release-2-23-8` still sits at `[W]` awaiting this verification, and every queue-file mtime falls inside the cycle's wake windows (18:30–18:51).
2. `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` / `cm-cancel-rc.sh`? **NO.** Its mtime is `18:36:25`, matching its own `## RC Opened At` value of `2026-09-16T18:36:25+00:00` to the second — one write, by the open verb.
3. Scripts patched mid-build? **NO.** No commit on `ai_main` since the RC opened; the only commits in the repository during the window are the four on `ai_rc/v2.23.8` (two ticket commits and their two `--no-ff` merges). The live install's copy of `check-empty-files.sh` is still byte-identical to the `ai_main` version, so nothing hand-patched the running tooling while the RC was in flight.
4. Queue files edited by hand? **NO.** Markers match task state everywhere in this cycle: `[x]` for the three DONE tasks, `[A]` for this task while it runs, `[W]` for the blocked-on-prereq release task. No stale `[A]`, no hand-cleared marker, and every `## Zero Progress` counter on the cycle's tasks reads 0.

## Caveats

- The residue this RC teaches the check to prune still exists in the live install: `bugs/` holds ten `.claim-NNNN.lock.empty.orphan` files. That is expected and not a finding — the fix ships in the dev tree, and the prune runs the next time an upgraded OVERWATCH fires against the project. BUG-0304's `## Status` is `running` and `cm-release.sh` Step 16b flips it (and the bundle's) to `done` at ship time.
- One residue shape the prune deliberately does not cover: a `.bug-claims.log.empty.orphan` left by a prior unscoped firing stays in place as evidence, matching the "only the claim-lock shape is pruned" rule. Harmless — the claim ledger is opened append-mode, so a missing ledger is recreated on the next claim. The live install's ledger is intact and was never orphaned.
- Test fidelity: the new unit test stubs `overwatch_halt_first_fix`, `overwatch_backup_file` and `overwatch_log_action` while sourcing the real `overwatch_protocol.sh` first, so the HALT-first and per-repo-flock machinery is mocked away in the test. That is a defensible boundary (that machinery is `overwatch_protocol.sh`'s own concern and is tested elsewhere), and this report's live runs closed the gap by exercising the same paths through the unmodified protocol — the HALT file was observed being touched and removed around each fix. No concrete mocked-away breaking condition remains, so this is a caveat and not a finding.
- Coverage numbers above are informational per TESTER.md Step 5b and enter no part of this verdict. The integration suite's 10 % line figure is the expected shape for a suite whose coverage is measured against the same broad source set as the unit suite.
