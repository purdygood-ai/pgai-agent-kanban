# Verification Report: rc/v2.26.6

## Summary

I verified RC `ai_rc/v2.26.6` (a1ab87d2) against `requirements/v2.26.6-bugfix-bundle-20260930.md`, which bundles BUG-0470 (Fixed-In restamp can't correct a wrong value), BUG-0471 (bundled bugs never reach Bug Fixes / `### Fixed`) and BUG-0472 (status writers delete the section after an empty one). The diff `ai_main..ai_rc/v2.26.6` covers 12 files, +789/-77, from CODER-20260930-025/026/027.

Both gated runners exited 0. Every acceptance bullet of the three bundled bugs was exercised behaviorally in sandboxes, reproducing the original failure (on ai_main where applicable) and then the fixed behavior. All three fixes work as specified.

One pre-existing defect of the same class as BUG-0472 was found in the status *readers* of the same file and filed via Path C (autonomous follow-up filing — non-blocking) as BUG-0474. It predates this RC and is not a regression.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Gated Runners (Step 5a / 6.6 / 6.7)

| Runner | Exit | Result | Log |
|---|---|---|---|
| `team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=0` | 5842 passed, 43 skipped (20:00) | `/tmp/pgai_kanban_tmp/tester/029-unit.log` |
| `team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=0` | 638 passed, 1 skipped (17:41) | `/tmp/pgai_kanban_tmp/tester/029-integ.log` |

PASS evidence is these two exit codes, not the pytest summary lines.

## Coverage (informational, Step 5b)

These come from the `totals` object of `coverage/unit.json` / `integration.json`. I checked the field names against the generated JSON first.

- unit: line 77.9 % (`percent_statements_covered`), branch 63.5 % (`percent_branches_covered`)
- integration: line 12.8 %, branch 10.0 %

## Findings

### Pre-flight (Steps 2-3)

- pass. HEAD `a1ab87d2` equals `git rev-parse ai_rc/v2.26.6`. `git status --porcelain` is empty and `git stash list` is empty.
- pass. I scanned 501 DONE tasks naming a feature branch. None still exists locally (deleted post-merge), so Check 1 and Check 2 pass trivially. The three CODER merges (5612acaa, 61adcc8d, a1ab87d2) are on the RC.

### Bundle acceptance criteria (requirements doc)

- pass. **Each bundled item is addressed**: see BUG-0470/0471/0472 below.
- pass. **All Python files compile cleanly**: `ast.parse` succeeded on all 8 changed `.py` files.
- pass. **All bash files pass syntax check**: `bash -n` succeeded on `release_notes.sh`, `wake_task_state.sh` and `restamp-fixed-in.sh`.

### BUG-0470: restamp correction mode (`--replace-from`)

I tested against a sandbox copy of the real ledger's BUG-042x..045x files, run through `team/scripts/restamp-fixed-in.sh` with a throwaway kanban root.

- pass. **Original failure mode preserved without the flag**: `--version v2.26.1 BUG-0431 BUG-0432` prints `skipped (already set: v2.26.4)` twice, exit 0, and the ledger is byte-identical (`diff -r`).
- pass. **Correction**: `--version v2.26.1 --replace-from v2.26.4 BUG-0431..0437` prints `7 replaced from v2.26.4`, exit 0. Exactly 7 files changed, one line each (`v2.26.4` -> `v2.26.1`). Every unnamed bug is byte-identical.
- pass. **Refuse on mismatch**: `--replace-from v2.26.3` on bugs holding v2.26.4 returns `fixed_in_restamp.replace_from_mismatch`, exit 2, with dual-channel error/message/remedy, and the ledger is unchanged. A rerun of an already-corrected bug is also refused (exit 2), as documented.
- pass. **Malformed flag**: `--replace-from banana` returns `fixed_in_restamp.replace_from_malformed`, exit 2, and nothing is written.
- pass-with-caveat. **"After the operator runs the three corrections, no v2.26.1/2/3 bundle bug reads v2.26.4"**: this is an operator action after ship and was not run on the live ledger. I simulated it on the sandbox copy. After the three commands CODER-025 recorded (v2.26.1, v2.26.2, v2.26.3, each `--replace-from v2.26.4`), 22 files changed. Only BUG-0427, 0429, 0430, 0450, 0451, 0452, 0453 (the genuine v2.26.4 bundle) still read v2.26.4. The live ledger still carries the 22 wrong values until CM/the operator runs those commands.
- pass. `--help` documents `--replace-from` with a worked example. The census prose names both new refusal keys under the existing `fixed-in-restamp-01` anchor.

### BUG-0471: bundled bugs under `## Bug Fixes` / `### Fixed`

The new test `test_release_notes_bug_fixes_section.py` runs the real `RELNOTES_PY` heredoc and the real `changelog_writer.regenerate`, so fidelity is good. Its fixture bugs carry a `## Fix` section that real ledger bugs lack, so I also exercised the production shape.

- pass. **Real v2.26.6 bundle** (categories release, release, wake): I ran the extracted heredoc on BUG-0470/0471/0472. All three render under `## Bug Fixes`, `## Other Changes` is `None`, and no `BUG-\d` id appears in the notes.
- pass. **Real v2.26.4 bundle** (categories test-infrastructure, test-estate, hygiene-lint, intake, api, worktree, docs): all 7 render under `## Bug Fixes`, including the `docs` one.
- pass. **CHANGELOG `### Fixed`**: `regenerate()` on those notes lists all three v2.26.6 bugs under `### Fixed` (previously `None`).
- pass. **Already-tagged release notes are not edited**: the diff touches no `release-notes/` file and no `CHANGELOG.md`.
- pass. **Both runners exit 0**: see above. Priority items keep their category mapping (covered by `test_priority_items_keep_their_category_mapping`, which passed in the green unit run).

### BUG-0472: status writers delete the section after an empty one

I ran the bug's own reproduction in a sandbox against both ai_main (`git archive` of `team/scripts/lib`) and the RC.

- pass. **`set_block` on an empty `## Summary`**: on ai_main the result is `## State|BACKLOG||## Summary||new summary||## Needs Human|no|`, so `## Blockers` is deleted, which reproduces the bug. On the RC the result is `...## Summary|new summary||## Blockers|none||## Needs Human|no|`, with every other section intact.
- pass. **`set_state` on an empty `## State`**: on ai_main the result is `## State||WORKING||## Blockers|none|`, so `## Summary` is deleted. On the RC the result is `## State|WORKING||## Summary|x||## Blockers|none|`.
- pass. **Separator preservation (normal case)**: after three rounds of set_state/set_block on populated sections, one blank line stays between sections with no growth. The extra shapes I tried also behave: an empty section at EOF, a heading with trailing spaces, and three consecutive empty sections.
- pass. **Sibling writers**: `task-status.sh` (task_status.py) fills an empty `## State` and `## Blockers` in one call, and `diff` against the original shows only the two inserted lines. `stamp_model_field` on an empty `## Model` fills it and keeps `## State`. The promote_* splices are covered by new unit cases in the green run.
- pass. **`run-unit-tests.sh` exits 0**.

#### Finding 1: wake status readers misread an empty `## State` as the next section

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to four reader regexes in one file; pre-existing)
**Filed As:** BUG-0474 (Path C-A — filed as BUG: code defect)
**Regression:** no
**Integrity:** none
**Filed By:** TESTER (autonomous Path C) — the wake scripts' status readers misread a task whose State section is empty, taking the text of the next section as its state, so such a task is never dispatched and never re-checked.

This was found in the Step 6.11 spot-check (untested mechanism adjacent to the BUG-0472 fix; CODER-027 flagged it as out of scope). `get_state`, `get_first_pending_task`, `recheck_waiting_tasks` and `recheck_blocked_tasks` in `team/scripts/lib/wake_task_state.sh` still use `^## State\s*\n(.*?)(\n+##|\Z)` with `re.S`.

Run on `printf '## State\n\n## Summary\nDONE\n\n## Blockers\nnone\n'`, `get_state` prints `## SUMMARY` / `DONE`, and the other three regexes yield `'## Summary\nDONE'`. A populated state reads correctly. The reader lines are unchanged from ai_main, so this predates the RC. It is rated medium per SOP-core calibration: a real defect with no current production trigger, since writers now never produce an empty `## State` by deletion.

### Step 6.1: release-state sanity

- pass. `## Active RC v2.26.6`, `## Last Released v2.26.5` (well-formed). `pp_last_released_version pgai-agent-kanban` returns `v2.26.5`, and `semver_gt v2.26.6 v2.26.5` is true. The release-state.md mtime (2026-09-30T12:32:00) matches `## RC Opened At` and CM-028's open-rc.log.

### Step 6.8: synthetic release simulation

- pass (skipped by rule). The diff touches `team/scripts/lib/cm/release_notes.sh` (release-notes content, not the open/close cycle scripts). `test_cm_release_synthetic.py` runs inside the integration suite, which exited 0.

### Step 6.9: constraint negative checks

- pass. No secrets in the diff. The changed bash files keep their existing strict-mode headers, and `restamp-fixed-in.sh` still refuses dual-channel with exit 2 before any write.

### Step 6.10: prereq graph

- pass. CM-028 (open) -> CODER-025/026/027 (each prereq CM-028) -> TESTER-029 (prereq the three CODERs) -> CM-031 release (WAITING on all four). There are no cycles and every ID exists.

### Step 6.13: skip-cites-real-bug

- pass. There are no placeholder skips. The only skip-with-BUG hits are fixtures inside `test_skip_bug_citation_scan.py` (BUG-1234/1235) and a historical artifact table. `lint_skip_bug_gate` ran green inside both runners.

### Step 6.14: coding standards

- pass-with-caveat. The version literals added to test fixtures (`_WRONG = "v2.26.4"`, `_RIGHT = "v2.26.1"`, the `replace_fixed_in` cases) are values illustrating the interface, which the naming rule's first exception allows. The hygiene lint raised no finding on them. New code comments describe behavior, and `--help` names the new flag.

## Lint Findings (non-blocking)

- `hygiene` (class `provenance`): `release-notes/v2.26.5.md:49` cites task ID `CODER-020`. It appears in both `findings-unit.json` and `findings-integration.json` (the same instance) and cites BUG-0473 (open). It is pre-existing: the file shipped with v2.26.5 and is not in this RC's diff.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=23d freshness=stale — The only ledger record is 23 days old and for v2.9.20, not v2.26.6, so it is stale on both counts; its red result (docker/podman claude-abliterated FROM parse error, podman rocky9 COPY --chmod) was filed by that run as BUG-0167, now done, and nothing is known about whether this RC's images build.

## Autonomous criterion (Step 6.12)

1. Tasks force-promoted WAITING->BACKLOG by hand? **NO.** This task's status reads "Promoted from WAITING to BACKLOG by wake script". Each CODER task has only wake-written dispatch.jsonl/progress.log.
2. `release-state.md` edited outside the CM scripts? **NO.** Its mtime equals the RC-open timestamp written by CM-028's `open-rc.log`.
3. Scripts patched mid-build? **NO.** The RC's 7 commits are exactly the three ticketed CODER changes and their merges. No other commit touched `team/scripts/` while the RC was open.
4. Queue files edited by hand? **NO.** I found no evidence of it. Queue files are not version-controlled, so this answer rests on the absence of anomalies in the task audit trail.
5. Non-CM remote git operation? **NO.** No `git-boundary-guard.jsonl` exists in any 2026-09-30 task's `logs/`.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- The BUG-0470 live-ledger correction is still an operator/CM follow-up. The three commands are recorded verbatim in CODER-20260930-025's status Summary. Until they run, 22 bugs in the live ledger still read `## Fixed In: v2.26.4`.
- `team/tests/unit/test_wake_model_stamp.py:86` holds a pure-Python mirror of `stamp_model_field` that still uses the old (buggy) regex; CODER-027 listed it. It is not a failing assertion. The shipped shell function is tested directly by `test_wake_task_state_section_separator.py::test_stamp_model_field_fills_empty_model_and_keeps_following_sections`, so no defect is masked today, but the mirror tests a copy rather than the product.
- Release-notes bullet prose derived from a real bug's first Symptom sentence is scrubbed of internal IDs by `internal_id_scrub.py`. This can yield awkward text: BUG-0470's bullet renders "another item through another item carry ...". This is pre-existing generator behavior, outside BUG-0471's acceptance, and only an observation.
- Observation (not a finding): `deep_verify_ledger_read_latest`, when passed a ledger *path* instead of a project name, printed `found=false` and returned 0 rather than refusing. A caller making that mistake would write a false "never run" Form B line. I re-read with the project name for the line above.
- Git workflow: the dispatch prompt forbids creating a feature branch in this detached worktree, so the report, gaps and notes live only in the task folder and nothing was committed to the RC.
