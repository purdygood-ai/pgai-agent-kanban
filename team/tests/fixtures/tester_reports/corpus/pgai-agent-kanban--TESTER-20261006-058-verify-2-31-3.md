# Verification Report: rc/v2.31.3 (ai_rc/v2.31.3)

## Summary

I verified RC v2.31.3 (`ai_rc/v2.31.3` at `992504126343372a2f0a616e72b529dd458cd515`) against `requirements/v2.31.3-bugfix-bundle-20261006.md`, which bundles four bugs: BUG-0587 (unwind-rc ignores the shared push rule), BUG-0590 (board_gather inlines the campaign filename and turns the unit suite red), BUG-0588 (llm_effort example states the wrong narration count) and BUG-0589 (the doc-truth lint stops scanning prose after a tagged code fence). Pre-flight passed. Both gated runners exited 0: unit 10070 + 5 passed, 41 skipped; integration 942 passed, 1 skipped; 0 lint findings in either. Each bundled bug was exercised behaviorally in a sandbox against both the RC and an `ai_main` extract, so the original failure and the fix were both observed. All four are fixed and every Acceptance bullet in their files is met. The requirements doc's own criteria (Python compiles, bash syntax) pass across the whole tree. The Step 6.11 spot-check found one pre-existing, currently unexercised defect: the BUG-0589 fence regex survives in a second copy, `team/scripts/harness/lib/doc_step_extract.py`. It is filed as BUG-0592 (medium, `Regression: no`). Because a bug was filed, the recommendation is SHIP-WITH-CONCERNS rather than PASS. Nothing this RC introduced is defective.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

### Finding 1: BUG-0587 — unwind-rc gates every origin call on the shared push decision

**Category:** pass

Sandbox repro with a network stub ahead of git. On `ai_main` a `push_to_remote = false` project made 3 network calls (ls-remote x2, `push origin --delete`). On the RC it makes 0, and so does each unresolved shape (no project.cfg; not registered; project.cfg without dev_tree_path while `PGAI_DEV_TREE_PATH` is set), in both `--confirm` and `--dry-run`. Each unresolved shape prints exactly one `[unwind-rc] skipped-unresolved-project` line naming the missing piece, and the local unwind completes (RC branch deleted locally, Active RC `none`). A registered project with no `push_to_remote` key still runs ls-remote and `push origin --delete`. Its dry-run makes exactly one ls-remote call. The REST twin shares `cm.unwind.unwind_rc`. Header, module docstring and `docs/cm-release-reference.md` updated. Details are in the BUG-0587 evidence section below.

### Finding 2: BUG-0590 — board_gather names the campaign file only through defer_posture

**Category:** pass

`_defer_line` uses `campaign_file_path(kanban_root)`. The fallback root (kanban root) matches the bash twin at `team/scripts/dashboard/lib/defer_scope.sh:214`. The full gated unit run is green, including the AST invariant `test_no_python_module_but_the_posture_reader_names_the_campaign_file`. The acceptance's targeted runs are green too: `--files tests/unit/test_defer_posture_reader.py` gave 41 passed, and the three board parity/prerender/snapshot files gave 36 passed (supplemental only, the verdict is the full run). A malformed-campaign parity test against the bash full path, including the no-file fallback, was added.

### Finding 3: BUG-0589 — doc-truth lint pairs every CommonMark fence

**Category:** pass

The bug's `a.md` repro goes from `scanned 0 citation(s)` on ai_main to `scanned 1` plus `MISSING COMMAND` on the RC. `~~~python`, ` ```ini `, nested 4/3-backtick fences and ` ```bash title=x ` behave the same way. The full manifest now scans 1141 citations, up from 864, with 0 findings and exit 0. Newly surfaced citations were fixed (OPERATIONS.md wake citation, which I confirmed accurate) or baselined with reasons (three test-fixture modules in the help-exempt list). A parametrized unit test pins `text`/`ini`/`python`/`~~~`/`~~~yaml`/4-backtick fences.

### Finding 4: BUG-0588 — llm_effort example states the per-role narration count

**Category:** pass

The bug's repro prints 6 narration lines, both for six exported `PGAI_<ROLE>_EFFORT` and for one `llm_effort` value. The `kanban.cfg_example` comment now says one line per role resolved (up to six), consistent with `docs/OPERATIONS.md:2670`. Doc-truth key-parity reports 0 findings.

### Finding 5: Requirements-doc acceptance criteria

**Category:** pass

All 800 `.py` files parse (0 failures). All 330 tracked `.sh` files pass `bash -n` (0 failures). Each bundled item is addressed (Findings 1-4).

### Finding 6: Gated suites

**Category:** pass

`UNIT_EXIT=0` (`run-unit-tests.sh --coverage`: 10070 passed, 41 skipped in the parallel lane; 5 passed in the serial lane). `INTEGRATION_EXIT=0` (`run-integration-tests.sh --coverage`: 942 passed, 1 skipped, including `test_cm_release_synthetic.py`). Both were recorded green in the per-RC green ledger at commit 992504126343. Logs: `/tmp/pgai_kanban_tmp/tester/058-unit.log`, `/tmp/pgai_kanban_tmp/tester/058-integration.log`. Coverage: unavailable. Both runners printed `coverage disabled by config ([testing] coverage)`, and the JSON files under `/tmp/pgai_kanban_tmp/coverage/` date from 2026-10-04, not this run. This is informational only.

### Finding 7: Step-extractor fence regex still hides blocks after a tagged fence (spot-check, pre-existing)

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one harness helper; latent today)
**Filed As:** BUG-0592 (Path C-A — filed as BUG: code defect)
**Regression:** no
**Integrity:** none
**Filed By:** TESTER (autonomous Path C) — the step extractor the documentation install-path check uses still only recognises bare and bash/sh code-block openers, so a guide step with any other tagged code block before its bash block hands the check the literal fence line as a command and misses the real one.

Step 6.11 (regression of a prior gap). `team/scripts/harness/lib/doc_step_extract.py:159` carries the exact regex BUG-0589 replaced in the citation lint: `^(`{3,}|~{3,})\s*(bash|sh)?\s*$`. A scratch doc with a ` ```text ` block before a ` ```bash ` block under `## Step 1` makes `doc_step_extract.py <doc> "Step 1" --block-index 0` exit 0 with `"block_fence_line": 7` (the text block's closer) and a first command of the literal "```bash". Its consumer, `strangers-path-check.sh`, runs extracted commands for real. It is not exercised today: the five `docs/docker.md` steps the harness extracts use only bash fences, and none of them yields a fence line. Rated **medium** per SOP-core "Rating a Finding" calibration (a real but unexercised defect in a shipped artifact). It predates this RC (`doc_step_extract.py` is untouched by the diff). It does not block shipping v2.31.3.

## Lint Findings (non-blocking)

None. `/tmp/pgai_kanban_tmp/lint/findings-unit.json` and `findings-integration.json` both hold `"findings": []`, generated by this run's runners at 00:26:15Z and 00:37:35Z.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=29d freshness=stale — The only cold-build record is red and stale on both counts (29 days old against the 7-day window, and for v2.9.20 rather than v2.31.3); that run filed BUG-0167, now marked done, so nothing current is known about whether this RC's images build.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG by hand: **NO**. Every RC task's status trail shows wake-script promotion. Queue markers match task states (CM-060 `[W]`/WAITING, TESTER-058 `[A]`, the rest `[x]`/DONE).
2. `release-state.md` edited outside the CM scripts: **NO**. Active RC `v2.31.3`, RC Opened By Task `CM-20261006-057-open-rc-2-31-3`, consistent with the RC branch and the open task.
3. Scripts patched mid-build: **NO**. All 10 RC commits (23:23Z-00:01Z, after RC open at 23:16:03Z) belong to the four task merges. No live-install file under `scripts/`, `roles/`, `pgai_agent_kanban/` or `SOP-core.md` changed since RC open, and `logs/activity.log` has no entry in the window.
4. Queue files edited by hand: **NO** evidence. Markers are well-formed and match status.
5. Non-CM remote git operation: **NO**. No `git-boundary-guard.jsonl` exists in any of this RC's task folders (PM-007, CM-057, CODER-053/054/055, WRITER-056, TESTER-058). My CM-only push-path probes used a network stub ahead of git on PATH, without a role override.

Autonomous operation criterion: Manual interventions required: none.

## Caveats

- Coverage is unavailable for this run (disabled by `[testing] coverage`). This does not affect the verdict.
- Observation, not a finding: `team/scripts/wake/claude.sh:8` header comment still names the old `wake-claude.sh <agent>` usage. It predates this RC and the doc-truth lint does not scan script headers.
- Observation, not a finding: the three new `doc-command-truth-help-exempt.txt` entries cite doc line numbers (`docs/test-authoring-guidelines.md:496`, `docs/operator-api.md:1620/1622`), which will drift as those docs change. The new block also opens with a doubled separator line. Both are cosmetic.
- My first sandbox run of the "no project.cfg" unwind shape exited 2 because my fixture repo used `ai_`-prefixed branches while a project with no project.cfg has no branch prefix. I re-ran it with unprefixed branches and it passed. That was a fixture error, not product behavior.
- Step 6.8: the diff touches no open/release-cycle script, so no separate synthetic release run was needed. `test_cm_release_synthetic.py` passed inside the integration suite.

---

# Evidence (checkpoints written during verification)

## Pre-flight (Steps 2-3)

- Step 3 worktree: `git rev-parse HEAD` = `992504126343372a2f0a616e72b529dd458cd515` = `git rev-parse ai_rc/v2.31.3`. `git status --porcelain` empty. `git stash list` empty. PASS.
- Step 2 RC intactness: 640 DONE tasks with a named feature branch scanned; no `ai_feature/*` branch exists locally (all deleted post-merge), so Check 1 and Check 2 pass trivially. The four prerequisite tasks (CODER-053, CODER-054, CODER-055, WRITER-056) are DONE and each has a `--no-ff` merge commit on the RC (95a3280d, e62aa5ce, a25d3815, 99250412) carrying unique commits. PASS.
- Gated suites launched (unit then integration, `--coverage`), output under `/tmp/pgai_kanban_tmp/tester/058-*.log`.

## Checkpoint: BUG-0587 (unwind-rc push gate) — behavioral verification

Independent sandbox reproduction (script `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-058/repro_unwind.sh`), network stub ahead of git logging every `fetch|pull|push|ls-remote|clone` and answering 0; `PGAI_TASKS_DIR`, `PGAI_PROJECT_ROOT`, `PGAI_PROJECT_NAME`, `PGAI_DEV_TREE_PATH`, `REPO_ROOT` unset; HALT present; Active RC v9.9.9.

| Tree | Shape | Mode | rc | Network calls | skip lines | Local unwind |
|---|---|---|---|---|---|---|
| ai_main (baseline) | push_to_remote=false | --confirm | 0 | 3 (ls-remote x2, push --delete) | 0 | done |
| ai_main (baseline) | push_to_remote=false | --dry-run | 0 | 1 (ls-remote) | 0 | n/a |
| RC | push_to_remote=false | --confirm | 0 | 0 | 0 (policy line printed) | done (rc branch gone, Active RC none) |
| RC | no project.cfg (REPO_ROOT set) | --confirm | 0 | 0 | 1, `missing project-cfg` | done |
| RC | not registered (no projects.cfg, PGAI_DEV_TREE_PATH set) | --confirm | 0 | 0 | 1, `missing registration` | done |
| RC | project.cfg w/o dev_tree_path, PGAI_DEV_TREE_PATH set | --confirm | 0 | 0 | 1, `missing dev-tree-path` | done |
| RC | registered, dev_tree_path, no push key | --confirm | 0 | 3 (ls-remote x2, push --delete) | 0 | done |
| RC | each of the four non-pushing shapes | --dry-run | 0 | 0 | — | n/a (plan shows `[skipped — ...]`) |
| RC | push-default | --dry-run | 0 | 1 (ls-remote) | — | n/a |

Original failure mode reproduced on ai_main; fixed on the RC; pushing default preserved. All four BUG-0587 Acceptance bullets met. Only two origin call sites exist in `cm/unwind.py` (lines 415, 1051), both gated on `inv.origin_skip`. The REST route `POST /operations/unwind-rc` calls the same `cm.unwind.unwind_rc`. `unwind-rc.sh` header and `docs/cm-release-reference.md` updated (Documentation Discipline). **pass**.

## Checkpoint: BUG-0589 (doc-truth lint fences) — behavioral verification

Scratch docs under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-058/fence/docs/`, each citing a non-existent script in prose after a non-shell fence. `python3 team/scripts/lint_doc_command_truth.py --repo-root . --doc <file>`:

| Doc | Shape | ai_main | RC |
|---|---|---|---|
| a.md | bug's own repro: ```` ```text ```` block, citation, bare block | scanned 0, 0 findings | scanned 1, MISSING COMMAND |
| b.md | no fences (control) | scanned 1, MISSING | scanned 1, MISSING |
| c.md | `~~~python` / `~~~` | scanned 0 | scanned 1, MISSING |
| d.md | ```` ```ini ```` | scanned 0 | scanned 1, MISSING |
| e.md | 4-backtick `text` fence wrapping a 3-backtick inner fence | scanned 0 | scanned 1, MISSING |
| f.md | ```` ```bash title=x ```` block citing a missing script | scanned 0 | scanned 1, MISSING |

Full manifest (no args): ai_main `scanned 864 citation(s) ... 0 finding(s)`; RC `scanned 1141 citation(s) ... 0 finding(s)`, exit 0 — 277 previously hidden citations now scanned, the newly surfaced ones fixed (OPERATIONS.md `wake-claude.sh` → `wake/claude.sh`, which I confirmed does emit the positional-deprecation warning at `team/scripts/wake/claude.sh:90-94`) or baselined (three test-fixture modules added to `team/config/doc-command-truth-help-exempt.txt`, each with a reason). Unit test `test_prose_citation_after_any_tagged_fence_is_scanned` parametrizes `text`, `ini`, `python`, `~~~`, `~~~yaml`, 4-backtick `json`. All three Acceptance bullets met. **pass**.

## Checkpoint: BUG-0588 (llm_effort example narration count)

Re-ran the bug's reproduction on the RC in a sandbox root: `active = harness` with all six `PGAI_<ROLE>_EFFORT=high` exported prints 6 `declares no reasoning-effort levels` lines; with `llm_effort = high` in the file, also 6. The `kanban.cfg_example` comment now says "one stderr line ... for each role it resolves the value for ... one line per role (up to six: PM, CODER, WRITER, TESTER, CM, PO)", consistent with `docs/OPERATIONS.md:2670` and with observed behavior. Comment-only change. Doc-truth key-parity reports 0 findings over 187 key citations. **pass**.

## Checkpoint: BUG-0590 (board_gather campaign filename)

`board_gather.py::_defer_line` now imports `campaign_file_path` from `pgai_agent_kanban.lib.defer_posture`; no `DEFER-LOW-BUGS` string constant remains outside `lib/defer_posture.py` (the only other hits are a docstring and a longer comment string in `ops/write.py`, neither matching the invariant's equality/suffix rule). The fallback root is the kanban root, matching the bash twin `team/scripts/dashboard/lib/defer_scope.sh:214` (`${DEFER_POSTURE_LAST_ERROR_FILE:-${kanban_root}/DEFER-LOW-BUGS}`), as the bug asked to confirm. New parity tests `test_a_malformed_posture_gathers_the_line_the_bash_full_path_builds` (four faults, including `refusal-naming-no-file`, which exercises the changed fallback) and `test_a_malformed_campaign_serves_a_bugs_pre_render_identical_to_its_direct_render` were added. Invariant test outcome comes from the full gated unit run below. **pass** (pending the unit suite — resolved: `UNIT_EXIT=0`; targeted acceptance runs 41 and 36 passed).

## Checkpoint: requirements-doc criteria and methodology steps

- **AC "All Python files compile cleanly":** `ast.parse` over all 800 `.py` files in the RC tree: 0 failures. **pass**.
- **AC "All bash files pass syntax check":** `bash -n` over all 330 tracked `.sh` files: 0 failures. **pass**.
- **AC "Each bundled item is addressed":** BUG-0587, BUG-0588, BUG-0589, BUG-0590 each verified above. **pass**.
- **6.1 release-state.md:** Active RC `v2.31.3`, RC Opened By Task `CM-20261006-057-open-rc-2-31-3`, Last Released `v2.31.2` (well-formed). `pp_last_released_version pgai-agent-kanban` = `v2.31.2`; `semver_gt v2.31.3 v2.31.2` true. **pass**.
- **6.2/6.3 structure and content:** 13 files changed, all modified (no new files). Each changed region read in context. `unwind.py` module docstring, `unwind-rc.sh` header and `docs/cm-release-reference.md` describe the same three-way contract the code implements and the sandbox runs printed. **pass**.
- **6.8 synthetic release simulation:** the diff touches `team/scripts/unwind-rc.sh` and `lint_doc_command_truth.py`, neither in the open/release cycle (`cm-open-rc.sh`, `cm-release.sh`, `cm-cancel-rc.sh`, `project_paths.sh`); not separately required. `test_cm_release_synthetic.py` still runs inside the gated integration suite.
- **6.9 constraints:** no hardcoded secrets in added lines; `unwind-rc.sh` keeps `set -euo pipefail` (line 182). **pass**.
- **6.10 prereq graph:** CM-057 (open, DONE) → CODER-053/054/055 (DONE) → WRITER-056 (prereqs CM-057, CODER-053, CODER-055; DONE) → TESTER-058 → CM-060 (release, WAITING, prereqs all five). No cycles, every ID exists, bookends linked. Sequence 059 unused, the same unused slot pattern as the prior cycle (051) for the approval task, which `Human Approval Required: auto` skips; no collisions. **pass**.
- **6.13 skip gate:** no skip/skipif/xfail added in this RC's test diff; no `BUG-SKIP`-style placeholders in live tests (only hits are historical run logs under `team/tests/artifacts/`). **pass**.
- **6.14 coding standards:** no bug/task/version identifiers in added code, comments or help text (the only ID-shaped strings are fixture data values in golden JSON and unit assertions, a sanctioned exception). No new scripts. **pass**.
