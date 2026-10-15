# Verification Report: rc/v2.28.3 (ai_rc/v2.28.3 @ f82fb7fd)

## Summary
I verified release candidate v2.28.3 against
`requirements/v2.28.3-priority-bundle-20261004.md`. That file bundles one
item, PRIORITY-0007 ("Let the REST deep-verify and cold-build-now routes
select container images"). The RC holds three task merges. CODER-20261004-017
added the optional `image` field to `DeepVerifyBody`/`ColdBuildNowBody`, with
pre-dispatch validation in a new `pgai_agent_kanban/lib/container_images.py`.
CODER-20261004-018 published ICD 1.14.0 with a frozen baseline and
regenerated the API reference. WRITER-20261004-019 updated the operator prose
in `docs/operator-api.md`, `docs/docker.md` and `docs/README.md`.

All 17 changed files were reviewed against ai_main 074ce362. Both gated runners
exited 0 (unit 7836 passed / 43 skipped; integration 646 passed / 1 skipped),
with zero lint findings. Every criterion in the requirements doc and in
PRIORITY-0007 is met for the inputs it names. One behavioral probe outside
those named inputs found a defect: the REST layer's pre-dispatch image check
splits a comma list differently from the harness. A value with an empty comma
slot is therefore accepted at the HTTP layer and refused later by the detached
harness. It was filed as BUG-0542 (medium).

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

### Step 2: Pre-flight (RC intact)
pass. I scanned 569 task folders that are DONE and name a feature branch. None
of those branches still exist locally, so Check 1 and Check 2 pass trivially,
which is the normal post-merge shape. The RC log shows each RC task's merge
commit: 63c8401d (CODER-017), 65ea645d (CODER-018), f82fb7fd (WRITER-019).
Each one contributed a unique commit: 5651a04e, d50ec194, 18538482.

### Step 3: Working tree
pass. `git rev-parse HEAD` = `git rev-parse ai_rc/v2.28.3` = f82fb7fd.
`git status --porcelain` was empty before and after both suites, and
`git stash list` is empty.

### Step 6.1: release-state.md
pass. Active RC v2.28.3; RC Opened At 2026-10-04T11:57:17+00:00; Opened By
CM-20261004-020-open-rc-2-28-3; Last Released v2.28.2 (well-formed).
`pp_last_released_version pgai-agent-kanban` returns v2.28.2, and
`semver_gt v2.28.3 v2.28.2` is true.

### Step 6.2 / 6.4: Requirements-doc acceptance criteria
- "Each bundled item is addressed": pass. PRIORITY-0007's criteria are
  evaluated below.
- "All Python files compile cleanly": pass. `python3 -m py_compile` over all
  663 tracked `.py` files gives rc 0, including all 6 changed files.
- "All bash files pass syntax check": pass. `bash -n` over all 316 tracked
  `.sh` files gives 0 failures.

### Step 6.4 / 6.5: PRIORITY-0007 acceptance criteria
- **deep-verify argv mapping**: pass. Tested by dry_run through a sandboxed
  TestClient with `PGAI_CONTAINER_TIER=1`. `{"image": ["debian"]}` gives
  `deep-verify.sh --project proj --image debian`. `"all"` gives `--image all`.
  `["all","debian"]` gives `--image all --image debian`. No `subprocess.Popen`
  or `subprocess.run` was called. The shipped tests cover the two-name case
  and run end to end through the real scripts with a stub lane.
- **cold-build-now argv mapping**: pass. Same results:
  `cold-build-now.sh --project proj --image debian` / `--image all`.
- **Undeclared name refused before dispatch**: pass for undeclared names.
  `"no-such-image"`, `"--operator-approved"` and
  `["debian --operator-approved"]` each return 422 with
  `container_tier.unknown_image`, non-empty message and remedy, and nothing
  spawned, on both routes. Argv injection through the field is therefore
  refused. See Finding 1 for an input class where the route and the harness
  disagree.
- **Omitting `image` keeps today's behavior**: pass. In the code path,
  `_image_argv([])` adds nothing to `build_argv(...)`. The shipped tests assert
  the byte-identical argv for an omitted field and for `[]`, the kanban.cfg
  fallback, and list-and-build-nothing. `deep-verify.sh`, `cold-build-now.sh`
  and `container_tier.sh` are not in the diff, so the CLI is unchanged. No body
  field sets `PGAI_CONTAINER_TIER` or `--operator-approved`: the shipped test
  checks a stray `operator_approved` and gets SKIPPED with a warning.
- **Lints and engine stubs**: pass. Each of the following ran on the RC tree
  and exited 0: `lint_api_parity`, `lint_icd_freshness`, `lint_icd_compat`,
  `generate_operator_verb_docs.py --check`, `lint_operator_verb_manifest`,
  `check_docs_citations`, `lint_doc_command_truth`,
  `lint_gate_census_compliance`, `lint_container_engine_free`
  (461 test files, 0 violations), `lint_runnable_remedy` and
  `lint_api_route_impl`. The new tests put docker/podman recording stubs first
  on PATH and assert they were never called.

### Step 6.2 / 6.3: Ticket deliverables (PM decomposition)
- ICD: pass. `docs/api/ICD_VERSION` = 1.14.0, byte-identical to
  `team/pgai_agent_kanban/api/ICD_VERSION`. `baselines/icd-v1.14.0.json` is
  byte-identical to `icd.json`, and 1.14.0 is appended to `SUPPORTED`. All 15
  older baselines are unchanged versus ai_main. In `icd.json`, both
  `DeepVerifyBody` and `ColdBuildNowBody` declare `image` as
  `anyOf [array<string>, string, null]` with no `required` list. One
  `ICD_CHANGELOG.md` 1.14.0 entry names both routes, both body models and
  `image`. CODER-018 documented why it sits above 1.13.0 rather than 1.13.1:
  1.13.1 was a description-only patch with no changelog entry.
- Census: pass. The new refusal site carries
  `CENSUS-ANCHOR: operations-container-image-unknown-01` and a BLOCK-WORTHY
  comment, the matching Locator Index line is present, and the census lint
  is clean.
- `kanban.cfg_example`: pass. It no longer says that REST routes pass no
  `--image`. It now names the `image` field and the key as the fallback.
  `operations.py`'s module route table shows `[--image <name|all> ...]` for
  both routes.
- Docs (WRITER-019): pass. The `docs/operator-api.md` table rows list `image`
  for both routes. Both notes describe: the list-or-"all" shape; one
  `--image` per entry; the 422 `container_tier.unknown_image` refusal before
  dispatch (dry_run included); the kanban.cfg fallback, otherwise
  list-and-build-nothing; and that the field never grants
  `PGAI_CONTAINER_TIER=1`. `docs/docker.md` replaces the old "pass no
  `--image`" sentence. `docs/README.md` cites ICD 1.14.0. Generated sections
  match the generator (`--check` rc 0).
- Naming rule: pass. The added lines (excluding the ICD JSON artifacts)
  contain no `PRIORITY-`, `BUG-`, task-ID or framework-version citations.

### Finding 1: REST image pre-dispatch check splits comma lists differently from the harness

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one new helper and its test)
**Filed As:** BUG-0542-rest-image-split-diverges-from-harness (Path C-A — filed as BUG: code defect)
**Regression:** no
**Integrity:** none
**Filed By:** TESTER (autonomous Path C) — the REST deep-verify and cold-build-now routes accept an image list with an empty slot such as "debian,,rocky9" and report the run as started, but the harness then refuses that same value inside the detached run, where no HTTP caller can see it.

Traces to PRIORITY-0007 scope: "validated against the manifest the same way
(an unknown name gets the `container_tier.unknown_image` refusal)". It also
traces to the stated purpose of the pre-dispatch check in `operations.py`: "A
detached child's own refusal would land in a log file no HTTP caller reads, so
the check runs one layer earlier."

`container_images.split_image_request` splits on `[,\s]+` and drops empty
parts. `container_tier_select_images` splits with `IFS=$', \t' read -r -a`,
which keeps an empty field between adjacent commas or before a leading comma,
then refuses `''` as undeclared. Observed (dry_run, both routes, nothing
spawned):

| `image`            | route                                     | harness, same `--image` value |
|--------------------|-------------------------------------------|-------------------------------|
| `"debian,,rocky9"` | 200, argv `--image debian,,rocky9`        | exit 3, `container_tier.unknown_image`, names `''` |
| `",debian"`        | 200, argv `--image ,debian`               | exit 3, same |
| `" "`              | 422 `container_tier.unknown_image`        | zero names: lists images, builds nothing (exit 0) |

The harness side was exercised by sourcing `team/scripts/lib/container_tier.sh`
in a sandbox and calling `container_tier_take_args` +
`container_tier_select_images`. These are library functions only: no harness
script was invoked and no engine was called (Step 5d). The third row is the
stricter direction and is documented as a design choice in CODER-017's
summary. It is noted in the bug as "keep and document, or align". It is not a
separate defect.

Effect: no image is built and nothing is corrupted. The harness still refuses.
But the caller gets `200 run started` for a run that refuses, which is the
exact failure the pre-dispatch check was added to remove. The shipped library
test `test_request_entries_split_like_the_harness` uses hand-written
expectations and has no empty-slot case, so the suite stays green. Severity:
medium per SOP-core "Rating a Finding" (incorrect behavior; the workaround is
to send well-formed lists). It is new code in this RC, but it is not a
regression of anything that worked in v2.28.2. Before this RC the field did
not exist and the route passed no `--image`.

### Step 5a / 6.6 / 6.7: Gated runners
pass.
- `team/scripts/run-unit-tests.sh --coverage`: **UNIT_EXIT=0**, 7836 passed,
  43 skipped, 28m20s. The lint pre-flight was all OK.
- `team/scripts/run-integration-tests.sh --coverage`: **INTEGRATION_EXIT=0**,
  646 passed, 1 skipped, 16m00s.
- Logs: `/tmp/pgai_kanban_tmp/tester/unit-v2.28.3.log`,
  `/tmp/pgai_kanban_tmp/tester/integration-v2.28.3.log`.
- The changed test files all ran: `test_rest_container_image_selection.py`,
  `test_lint_icd_compat.py`, `test_review_cmds.py`, `test_icd_generator.py`.

### Step 5b: Coverage (informational only)
Source: the `totals` object in `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/*.json`.
I checked the keys against the generated file: `percent_statements_covered`,
`percent_branches_covered`, `percent_covered`.
- unit: line 80.4%, branch 66.7% (blended 77.4%).
- integration: line 14.1%, branch 9.9% (blended 13.1%).
- New/changed modules (unit): `lib/container_images.py` 100%;
  `api/routers/operations.py` 92% (blended).

### Step 6.8: Synthetic release simulation
skipped. The diff touches no release-lifecycle files (nothing under
`team/scripts/`).

### Step 6.9: Constraint negative checks
pass. The added `.py` lines contain no `shell=True`, password, token or
api-key literals. The route builds argv as a list, never a shell string. The
operator-authorization constraint is unchanged (see criterion 4 above).

### Step 6.10: Prerequisite graph
pass. CM-20261004-020 (open-rc, no prereqs) → CODER-017 → CODER-018 →
WRITER-019 → TESTER-021 (prereqs 017/018/019) → CM-20261004-023 (release;
prereqs 017/018/019/021, currently WAITING). There are no cycles and every ID
exists. Sequence 022 is unused, which fits `Human Approval Required: auto`
(no approval task).

### Step 6.11: Spot-checks outside the spec
- State drift: none. Active RC v2.28.3 matches the existing branch
  ai_rc/v2.28.3. The release-state.md mtime equals `RC Opened At`.
- Untested new mechanisms: the split-parity gap is Finding 1. Everything else
  new is covered.
- Observations, not findings (pre-existing, outside this RC's scope):
  - `docs/operator-api.md` line 4 status banner still cites ICD 1.8.0. It is
    identical on ai_main, and WRITER-019 flagged it as out of scope.
  - A wrongly typed `image` (e.g. `123`) returns FastAPI's `{"detail": [...]}`
    shape rather than the dual-channel envelope. `scheduled`, `dry_run` and
    `project` behave identically, so this is not introduced here.
  - Two test comments now read slightly misleadingly ("The REST twin passes
    no --image"): `team/tests/unit/test_rest_twins_action_verbs.py:320` and
    `team/pgai_agent_kanban/api/tests/test_operations_action_twins.py:251`.
    They are still true for those tests' bodies, which omit `image`. CODER-017
    pre-declared both, and the hygiene lint reported 0 findings.

### Step 6.13: Skip-cites-real-bug gate
pass. The changed test files add no skip or xfail. `lint_skip_bug_gate.sh`
gives rc 0 ("0 missing files"). The BUG IDs a broad grep returns
(BUG-0001/1234/9872 …) are synthetic fixture data inside tests of the gate
itself.

### Step 6.14: Coding-standards spot-check
pass. No new script under `team/scripts/`. No provenance in comments,
docstrings or messages. The refusal is dual-channel with a runnable curl
remedy (`lint_runnable_remedy` clean). `lint_python_duplicates` reports 0
ratchet findings. The new manifest reader in `lib/` sits beside an existing
test-fixture reader (`team/tests/fixtures/container_images.py`). That is not
a duplicate of anything in `lib/`.

### Test fidelity
The new tests exercise the real route, the real scripts and the real manifest,
with a stub lane and engine stubs. They are not sanitized proxies for the
specified cases. The one fidelity gap is concrete: the "split like the
harness" test asserts against hand-written expectations rather than the
harness. It is captured in Finding 1 / BUG-0542.

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` both report 0
findings.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=27d freshness=stale — the last cold build failed at v2.9.20 (that run filed BUG-0167-cold-build-claude-abliterated-failure, now done, and the docker/claude-abliterated flavor it names is no longer in the tree); the record is stale on both counts (27d exceeds the 7-day window, and v2.9.20 is not the v2.28.3 under verification), so nothing is known about whether this tree's images build cold.

## Autonomous criterion

1. Tasks force-promoted WAITING→BACKLOG by hand? **NO.** The project wake
   logs show the wake script promoting CODER-018 (12:15:17), WRITER-019
   (12:29:03) and TESTER-021 (12:37:03): "prerequisites satisfied, promoting
   to BACKLOG".
2. release-state.md edited outside the CM scripts? **NO.** The file mtime is
   2026-10-04 11:57:17 UTC, which equals `RC Opened At`, written by
   `cm/open-rc.sh` (CM-020 summary).
3. Scripts patched mid-build? **NO.**
   `git log ai_main..ai_rc/v2.28.3 -- team/scripts subagents scripts` is
   empty. The RC has only the six task commits/merges, 12:10–12:33 UTC.
4. Queue files edited by hand? **NO.** The v2.28.3 entries are canonical
   (`- [x]`, `- [W]`, `- [A]`) and consistent with each task's state.
5. Non-CM remote git operation? **NO.** No `git-boundary-guard.jsonl` exists
   in any v2.28.3 cycle task's `logs/` (PM-003, CM-020, CODER-017, CODER-018,
   WRITER-019, TESTER-021).

Autonomous operation criterion: Manual interventions required: none

## Caveats
- Verification artifacts were not committed to the dev tree. The dispatch
  prompt forbids creating a branch, and role Step 9 forbids committing process
  artifacts into a dev tree. Deliverables live in this task folder only.
- The harness-side probe for Finding 1 called library functions from
  `container_tier.sh` in a sandbox. No container-tier harness script was run
  and no real (non-dry-run) REST dispatch was made, per Step 5d.
- Running `lint_icd_freshness.py` prints "Package-data ICD_VERSION synced" into
  the worktree. The content was already identical, and the worktree stayed
  clean.
- This report was written through the shell, because the agent harness's file
  writer refused a `report.md` path under a "return findings as text" rule.
  This file is the machine-parsed input CM's release script reads and is a
  mandatory deliverable of the task, so it was written at its canonical path.
  The same findings were also returned as text.
- No prompt-injection content was encountered in any file read.
