# Verification Report: rc/v2.28.1

## Summary

Verification of RC `ai_rc/v2.28.1` (HEAD 32657d23) against
`requirements/v2.28.1-bugfix-bundle-20261004.md` (10 bundled bugs: BUG-0533,
BUG-0534 headline; BUG-0527..0532, 0535, 0536 hygiene ride-alongs).
Both gated runners exited 0 (unit 7777 passed / 43 skipped; integration 646
passed / 1 skipped), with zero quality-class lint findings. Each of the ten
bundled items was checked against its own bug file's Acceptance section. For
every behavioural fix, the original failure was first reproduced on an
`ai_main` (v2.28.0) export and then shown fixed on the RC. That covers by-path
`--help`, the show-queues refusal on CLI / REST / show-multi, and the
dashboard idle CPU, measured at 67.99% of one core on the baseline and 0.55%
on the RC. Every headline acceptance criterion is met. Four follow-ups were
filed via Path C (autonomous follow-up filing — non-blocking). One is a
pre-existing medium bug: seven other package-module CLIs still crash when run
by path, the remainder of BUG-0533's root cause. Two are low documentation
gaps (the published ICD text for GET /show-queues; the dashboard docs still
describing a 10x hidden-pane slowdown). One is a low priority: the REST
deep-verify / cold-build-now routes cannot select images. None makes the
release unusable.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Pre-flight

- Step 2 (RC intact): 563 DONE task folders name a feature branch; none of those
  branches still exists locally, so Check 1 / Check 2 pass trivially for all.
  The four implementing merge commits (ebc424f8, 4d62db5d, 6b92228a, 32657d23)
  are ancestors of HEAD.
- Step 3 (working tree): `git rev-parse HEAD` = `git rev-parse ai_rc/v2.28.1`
  = 32657d23757abba7e1cbc5b2c02f9e4037606c10. `git status --porcelain` empty.
  `git stash list` empty.
- Diff `ai_main..ai_rc/v2.28.1`: 82 files, +4830 / -930, 17 commits, all
  authored by the kanban identity during the RC window (06:52-08:59 UTC).

## Findings

(appended below as verification proceeds)

### Requirements-doc acceptance criteria (whole tree)

- "All Python files compile cleanly": `py_compile` over all 661 tracked `.py`
  files (pycache redirected outside the tree) — 0 failures. **pass**
- "All bash files pass syntax check": `bash -n` over all 316 tracked `.sh`
  files plus 1 extensionless bash script — 0 failures. **pass**
- New files (8) present; the three new executables
  (`lib/docker_use.sh`, `lint_docker_helper_path.py`,
  `harness/dashboard-idle-budget.sh`) are mode 755. **pass**

### BUG-0533 — package modules crash when run by path

- Original failure reproduced on an `ai_main` (v2.28.0) export, cwd `/`,
  `PYTHONPATH` unset: `scan_human_approvals.py --help`, `agent_metrics.py --help`,
  `scan_attention.py --help` each exit 1 with `ModuleNotFoundError`.
- On the RC, the same three commands exit 0, no traceback. **pass**
- `lint_doc_command_truth.py` on the real tree: baseline 2 HELP FAILS,
  RC 0 findings, exempt count 3 (the lint script is not in the diff, so no
  exempt-list entry was added). **pass**
- Bootstrap reviewed: `if not __package__:` insert of
  `Path(__file__).resolve().parents[2]` — the file's own tree, not a
  dev-tree or repository fallback. **pass**
- `human-review.sh` (fixture with two pending HUMAN-APPROVE tasks) and
  `agent-metrics.sh --all` (copied metrics rollups) run against one sandbox
  root from baseline and RC, colour and NO_COLOR: `cmp` byte-identical in all
  four comparisons. **pass**
- The real-tree unit test is confirmed via the gated unit runner (see Step 5a).

### BUG-0534 — show-queues with no project prints all-zero counts

Fixtures: a legacy single-project root built with the bug's own recipe
(`tasks/queues/coder_backlog.md` holding one entry, no `projects.cfg`) and a
multi-project root (`projects.cfg` registering `alpha`, two coder entries).
Every run uses `env -u PGAI_PROJECT_NAME -u PGAI_PROJECT_ROOT -u PGAI_TASKS_DIR`
with KANBAN_ROOT / PGAI_AGENT_KANBAN_ROOT_PATH / TEMP_DIR pointed at the fixture.

- Original failure reproduced on the `ai_main` export: CLI exit 0 with
  `coder: 0/0` on both roots; REST `GET /show-queues` HTTP 200 with the same
  zeros; `show-multi.sh --mode queues` on the legacy root prints zeros, exit 0.
- RC, multi-project root, no project: `show-queues.sh` exit 1, 0 bytes on
  stdout, stderr is the `show_queues.no_project` error/message/remedy block;
  the remedy names `--project alpha`. **pass**
- RC, legacy root: refuses the same way (the chosen behaviour); remedy says to
  register the project first. **pass**
- RC REST (FastAPI TestClient, loopback base URL), no project: HTTP 500,
  `exit_code` 1, `stdout` "", stderr starts `error:   show_queues.no_project`,
  on both roots. 500 matches the existing compact-mode refusal convention
  (`show_queues.project_directory_missing` is also 500). **pass**
- RC `show-multi.sh --mode queues` on the legacy root: passes the refusal
  through, exit 1, no counter lines. **pass**
- Unchanged paths, byte-compared baseline vs RC on the multi-project root:
  `--project alpha`, `PGAI_PROJECT_NAME=alpha`, and `--details` with no
  project (walks every project) — all three `cmp` identical, exit 0. REST
  `?project=alpha` and `?details=true` return 200 on both trees. **pass**
- New refusal sites carry `CENSUS-ANCHOR` comments with matching
  `docs/blocking-site-census.md` entries (`ops-read-queues-no-project-01`,
  `container-tier-unknown-image-01`); `lint_gate_census_compliance.py` clean.
  **pass**
- `docs/operator-api.md` GET /show-queues row now describes the refusal. **pass**
- Published ICD (`docs/api/icd.json` GET /show-queues description, generated
  from the `get_show_queues` docstring) still says "with no `project` the
  counters come from the server's `PGAI_PROJECT_NAME` project, else read 0/0",
  and its HTTP-status paragraph does not list `show_queues.no_project` among
  the 500 causes. **gap** — see Finding 2.

### BUG-0531 — agents invoke the container tier (code side)

TESTER.md Step 5d forbids TESTER from invoking any container harness, even
against stubs. The harness behaviour is therefore verified through code
review and through the gated suites' sandboxed tests (engine stubs, zero
engine calls asserted), whose fidelity was reviewed.

- `lib/container_tier.sh`: an unauthorized call (no `PGAI_CONTAINER_TIER=1`,
  no `--operator-approved`, or inside a wake) prints one
  `SKIPPED key=container_tier.operator_only reason=... operator_command=...`
  line, records SKIPPED in docker-use.log and `exit 0` (CONVERT comment
  present). `--operator-approved` is accepted alongside the variable. `--image NAME`
  (repeatable, comma lists) / `--image all` select from
  `team/config/container-images.txt`; otherwise an inherited
  `PGAI_CONTAINER_IMAGES` or `kanban.cfg [release] container_images` applies;
  with none, the images are listed and the script exits 0 with nothing built.
  An undeclared name refuses `container_tier.unknown_image`, exit 3,
  BLOCK-WORTHY with census anchor. **pass**
- Test fidelity: `test_container_tier_gate.py` runs the four real harness
  scripts (deep-verify, run-cold-build-tests, run-three-host-matrix,
  strangers-path-check) with docker/podman stubs that log every call, and
  asserts exit 0, exactly one SKIPPED line, and an empty engine log. It also
  asserts the in-wake skip writes one `SKIPPED actor=tester task=...` line and
  that a skipped deep-verify leaves a prior ledger byte-identical / writes
  none. The image-selection, START/FINISH, nested-session and
  unwritable-log cases are covered the same way. `cold-build-now.sh` is not
  parametrized, but it `exec`s `deep-verify.sh` with the container-tier flags
  passed through, so it inherits the gate. **pass**
- `lib/docker_use.sh` (new, read in full): START/FINISH/SKIPPED/REFUSED lines
  carrying actor and task; a missing directory is created; an unwritable one
  produces one stderr warning and never changes an exit code. **pass**
- `lint_docker_helper_path.py`: 0 findings on the shipped tree (453 files).
  In a `git clone` of the RC with a seeded `docker build` / `podman run`
  script it names both lines and exits 0 (quality class). It also catches a
  quoted engine variable (`"$ENGINE" build`). **pass-with-caveat**: it does
  not flag `${CONTAINER_ENGINE:-podman} pull ...` (unquoted expansion with a
  default) or `sudo docker rm ...`. A grep of the shipped tree finds no call
  of either shape, so nothing escapes today. Not filed.
- grep-zero: there is no non-comment `docker/*` glob in the four harnesses. **pass**
- `why.sh --details` (sandbox root with a docker-use.log): prints
  `== docker use ==` / `last docker use: <last line>`, or
  `no docker use recorded (<path>)` without a log. Output differs from the
  baseline only by that section; exit codes match. **pass**

### BUG-0527 — container-tier remedies lack authorization

- `deep-verify-freshness-check.sh` in a sandbox (no ledger, engine stubs):
  remedy is `PGAI_CONTAINER_TIER=1 bash <abs>/team/scripts/deep-verify.sh
  --project pgai-agent-kanban --image all`; zero engine calls. All five remedy
  sites now use one builder (`container_tier_operator_command`). **pass**
- `test_freshness_remedy_runs_as_printed_and_reaches_the_lane` runs each
  remedy verbatim (`bash -c`) against a stubbed lane. It asserts no SKIPPED,
  no image-listing stop, that the lane ran with both images, a `built`
  record, and zero engine calls. **pass** (it runs inside the gated suite)
- `grep -rnE 'deep-verify\.sh --project|cold-build-now\.sh --project'
  team/scripts --include=*.sh --include=*.py | grep -v PGAI_CONTAINER_TIER`
  is empty. **pass**
- `suggested-commands.sh --no-color` prints
  `PGAI_CONTAINER_TIER=1 scripts/deep-verify.sh --project <proj> --image all
  (alias: cold-build-now.sh)`. **pass**

### BUG-0528 — suggested-commands output says "pane"

- `grep -n -i pane team/scripts/dashboard/suggested-commands.sh` is empty;
  line 2 of the output reads "Copy a line from this list and run it in the
  shell -- this command never runs anything for you." **pass**

### BUG-0529 — operator-verb docs stale for deep-verify

- `generate_operator_verb_docs.py --check`: OK, 32 verb blocks.
  `docs/operator-commands.md` carries `PGAI_CONTAINER_TIER=1` 15 times. **pass**

### BUG-0535 — metrics atomic writer not shared

- `git grep -n "def _atomic_write" -- 'team/*.py' | grep -v /tests/` is
  empty. **pass**
- `metrics_aggregator.py` still answers by-path `--help` (exit 0) after its
  new package import. **pass**

### BUG-0536 — inline-python unquoted heredoc delimiters

- `lint_inline_python.py`: 292 files, 145 blocks, 0 findings. **pass**

### BUG-0530 — container-tier docs lack authorization (doc side)

- Acceptance grep (`(deep-verify|cold-build-now|run-cold-build-tests|
  run-three-host-matrix|strangers-path-check)\.sh` over `docs/ team/roles/
  HOW_TO.md`, minus lines carrying `PGAI_CONTAINER_TIER=1` or
  `--operator-approved`): the remaining hits are descriptive prose ("Generated
  from `scripts/deep-verify.sh --help`", "a scheduled `deep-verify.sh
  --scheduled` entry builds what its own line names", the REST alias
  description). No runnable invocation lacks authorization. **pass**
- Crontab examples in `docs/operator-commands.md` (lines 911, 1005, 1014)
  read `PGAI_CONTAINER_TIER=1 ... deep-verify.sh --scheduled --image all`. **pass**
- `team/roles/CM.md:242`: "The remedy for a refusal is the operator's to run,
  never yours ... Record the need in `## Summary`". **pass**
- `lint_hygiene.py --classes provenance`: 0 findings; `check_docs_citations`
  is checked under the gated runners. **pass**
- REST surface: `docs/operator-api.md` documents that the REST
  `deep-verify` / `cold-build-now` routes build only what `kanban.cfg
  [release] container_images` selects ("without them the run is skipped or
  builds nothing, exit 0, no record"). That is accurate, but those routes give
  the operator no way to choose an image per call. **gap** — see Finding 4.
- `kanban.cfg_example` says "Scheduled deep-verify runs (--scheduled) ... pass
  no --image, so they build only what this key selects", while every
  documented scheduled crontab line passes `--image all`. No internal
  scheduler issues `--scheduled` (only operator-written cron/pseudocron
  lines and the REST route do), so nothing silently stops. The sentence is
  imprecise rather than wrong for cron lines without `--image`.
  **pass-with-caveat**; not filed separately.

### BUG-0532 — dashboard idle CPU hog (code side)

- grep: exactly one visibility decider under `team/scripts/dashboard`:
  `dashboard_pane_visible` in `lib/cadence.sh`, visible only when
  `#{session_attached} > 0` AND `#{window_active} == 1`; the old
  `dashboard_cadence_window_active` is gone, and the `create.sh` hits are
  status-bar format strings. **pass**
- `lint_pane_refresh_cadence.py`: "every census pane declares what it does
  while hidden". **pass**
- `PGAI_DASHBOARD_HIDDEN_MULTIPLIER` now sets only the hidden re-check interval
  (one tmux query, no render). **pass**
- Idle-budget harness: run by TESTER, result recorded below.
- Docs: `docs/dashboard.md:114,118,190` and `docs/OPERATIONS.md:1489,1510`
  still say hidden panes "slow to 10x the active interval (50 seconds by
  default)" / render "`PGAI_DASHBOARD_HIDDEN_MULTIPLIER`-times slower". The
  shipped behaviour is no render while hidden, and a detached session counts
  as hidden. Neither file's dashboard passages changed in this RC (CODER-004
  flagged them for WRITER; the WRITER ticket's scope did not include them).
  **gap** (Documentation Discipline Rule) — see Finding 3.

### Step 6 methodology

- 6.1 release-state: `pp_last_released_version` = v2.28.0;
  `semver_gt v2.28.1 v2.28.0` is true; `## Last Released` is well-formed;
  `## Active RC` v2.28.1 matches the only `ai_rc/*` branch. **pass**
- 6.8 synthetic release simulation: skipped. The diff touches no
  open/close-cycle file (`cm-*.sh`, `lib/project_paths.sh`, `scripts/cm/`);
  the two gated runners changed only to wire in the new lint.
- 6.9 constraints: no secret-shaped literal added; every changed `.sh` keeps
  its `set -e*` line count; the one task-ID-shaped literal added
  (`CODER-20261001-001-demo`) is a test fixture value (sanctioned example);
  `lint_hygiene --classes provenance` 0 findings. **pass**
- 6.10 prerequisite graph: PM-001 → CM-006 → CODER-002/003, CODER-004
  (← 002), WRITER-005 (← 003, 004), TESTER-007 (← 002..005), CM-009
  (← all). No cycles, and every referenced task exists. Sequence 008 is
  unused (observation only; probably the auto-skipped approval slot). **pass**
- 6.13 skip-cites-real-bug: no new skip annotation in the diff; the BUG IDs
  that look missing occur only inside the gate's own test fixtures;
  `lint_skip_bug_gate.sh` OK. **pass**
- 6.14 coding standards: the new harness and lint have `--help` naming every
  option; the idle-budget thresholds are flags, not hardcoded; no new
  duplicate atomic writer. **pass**

### Step 5a — gated unit runner

- `bash team/scripts/run-unit-tests.sh --coverage` from the worktree root:
  **UNIT_EXIT=0** (exit file written 09:32:39; a stale 2026-09-30 exit file in
  the shared scratch directory was ignored). Pytest summary: `7777 passed,
  43 skipped in 1650.75s`. Every pre-flight lint reported OK. This includes
  the real-tree `test_real_tree_default_exempt_list_closes_the_named_process_internal_citations`
  (BUG-0533 acceptance) and `test_operator_verb_docs_generator.py` (BUG-0529).
  The installed-governance citation test WRITER-005 saw failing mid-task
  also passed.
- Coverage (unit, from `coverage/unit.json` `totals`, field names checked
  against the generated file): line 80.41% (16900/21017), branch 66.77%
  (3990/5976); blended `TOTAL` 77%. Informational only.

### Dashboard idle budget (BUG-0532 measured acceptance)

- `team/scripts/harness/dashboard-idle-budget.sh` (throwaway root, private
  tmux socket, detached, 30 s settle, 600 s window, CPU summed from /proc over
  the dashboard process tree only) on the RC:
  `verdict=UNDER cpu_pct=0.55 cpu_seconds=3.27 window_seconds=600
  budget_pct=25 processes=143`. **pass**

### Step 5a — gated integration runner

- `bash team/scripts/run-integration-tests.sh --coverage`:
  **INTEGRATION_EXIT=0** (exit file written 09:48:52). Pytest summary:
  `646 passed, 1 skipped in 940.93s`; "Integration tests PASSED".
- Coverage (integration, `coverage/integration.json` `totals`): line 14.10%
  (2961/21005), branch 9.89% (591/5976); blended `TOTAL` 13%. Informational
  only.
- The worktree is still clean (`git status --porcelain` empty) after both runs.

### Dashboard idle budget — original failure mode

- The same harness was copied into the `ai_main` (v2.28.0) export and run
  against that tree's dashboard, with the same fixture shape, socket
  isolation and 600 s window:
  `verdict=OVER cpu_pct=67.99 cpu_seconds=407.94 window_seconds=600
  budget_pct=25 processes=144`. Before 67.99%, after 0.55% of one core.
  CODER-004 recorded 74.19% / 0.60%. **pass**

### Step 6.5 behavioural summary

Each guard and refusal added by this RC was triggered, not just read: the
show-queues no-project refusal (CLI, REST, show-multi; legacy and
multi-project roots); the by-path package bootstrap (from `/`, `PYTHONPATH`
unset); the docker-helper lint (seeded bare and variable engine calls); the
freshness-gate remedy text (sandbox with no ledger). The container-tier
SKIPPED path and unknown-image refusal were exercised by the gated suite's
sandboxed tests, because Step 5d forbids TESTER from invoking the harnesses
directly.

### Finding 1: Seven package-module CLIs still crash when run by path

**Category:** bug (pre-existing — introduced by v2.28.0, not by this RC)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same by-path import breakage BUG-0533 fixed for three modules; a fix scoped to doc-cited modules left the rest)
**Filed As:** BUG-0537 (Path C-A — filed as BUG: code defect / regression / test failure)
**Filed By:** TESTER (autonomous Path C) — seven package modules whose own help text says to run them as `python3 <file>.py ...` crash with "module not found" when run that way.

`deep_verify/ledger.py`, `lib/refusal_ledger.py`, `metrics/blocked_ledger.py`,
`overwatch/ledger.py`, `overwatch/resolution_matrix.py`,
`reports/agent_timing.py` and `dashboard/queue_details.py` each have a
`__main__` CLI, and six document `python3 <file>.py ...` usage in their
docstring. Run by path from `/` with `PYTHONPATH` unset, each exits 1 with
`ModuleNotFoundError` on both the RC and `ai_main`; each exits 0 on
`ai_v2.27.3`. The `-m` form works, and no script calls them by path
(workaround exists, nothing depends on it → severity medium). This traces
to BUG-0533's Fix clause ("check any other module this release converted
that a document cites by path"). These are cited only by their own
docstrings, which the doc lint does not scan.

### Finding 2: Published ICD still describes GET /show-queues as "else read 0/0"

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one route's contract text)
**Filed As:** BUG-0538 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the published API contract still tells clients the queue board with no project answers "0/0", but it now refuses that request.

`team/pgai_agent_kanban/api/reads.py:1774` and `docs/api/icd.json` (GET
/show-queues description) say the counters come "from the server's
`PGAI_PROJECT_NAME` project, else read 0/0". The HTTP-status paragraph
(`reads.py:1809-1812`) omits `show_queues.no_project`. CODER-003 deferred the
fix to "the release's ICD bump step". `docs/operator-api.md` says the bump
belongs in the API-changing RC itself, and no CM step runs `generate-icd.sh`.
This traces to BUG-0534's Files Involved / acceptance ("update together with
the fix") and to the SOP-core Documentation Discipline Rule. The
`docs/operator-api.md` route row itself is correct.

### Finding 3: Dashboard docs still describe hidden panes as refreshing 10x slower

**Category:** gap
**Fix Effort:** small (1-2 WRITER tasks)
**Systemic Risk:** low (isolated documentation; the decomposition omitted a doc update the CODER flagged)
**Filed As:** BUG-0539 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the dashboard guides still say hidden-window panes keep refreshing ten times slower, but they now stop rendering entirely.

`docs/dashboard.md:114,118,190` and `docs/OPERATIONS.md:1489,1510` describe
the pre-RC behaviour. The shipped behaviour: no render while hidden; a
detached session counts as hidden; `PGAI_DASHBOARD_HIDDEN_MULTIPLIER` is only
the re-check interval. This traces to BUG-0532 and the SOP-core
Documentation Discipline Rule ("TESTER records a missing ... update as a gap").

### Finding 4: REST deep-verify / cold-build-now cannot select container images

**Category:** gap
**Fix Effort:** medium (3-5 tasks: request model, census and generated docs, ICD minor bump, tests)
**Systemic Risk:** low (isolated to two REST routes; documented accurately)
**Filed As:** PRIORITY-0007 (Path C-B — filed as PRIORITY: enhancement / design change)
**Filed By:** TESTER (autonomous Path C) — the operator can now pick which images a deep-verify run builds from the command line, but the REST calls have no way to say which images, so by default they build nothing.

BUG-0531 item 2 says "the operator chooses the image". On the CLI that
holds. The REST request model (`project`, `scheduled`, `dry_run`) has no
image field. With the shipped `container_images =` default empty, a REST run
returns 200, builds nothing and writes no record. That outcome is documented
in `docs/operator-api.md` and `kanban.cfg_example`. CODER-004 named the
follow-up and nobody filed it. This is an enhancement, not incorrect
behaviour, so it is filed as Path C-B.

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` were both written
by this run (09:32:39 and 09:48:52) with `"findings": []`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=27d freshness=stale — The last DEEP-VERIFY record is red and stale on both counts (27 days old against a 7-day window, and for v2.9.20 rather than v2.28.1); that run's failure (the since-removed docker/claude-abliterated image and a rocky9 COPY step) was filed as BUG-0167, now done, and nothing is known about whether the current debian/rocky9 images build.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO.** Every cycle
   task's promotion note reads "Promoted from WAITING to BACKLOG by wake
   script: prerequisites satisfied", and start times follow prerequisite
   completion (WRITER-005 started 08:34 after CODER-004 merged 08:25).
2. `release-state.md` edited outside the CM scripts? **NO.** Its mtime
   (06:36:58.30) equals `## RC Opened At`, written by `scripts/cm/open-rc.sh`
   per CM-006's status. Its fields are consistent with the only `ai_rc/*`
   branch.
3. Scripts patched mid-build? **NO.** No file under the live
   `scripts/`, `pm-agent/` or `subagents/` changed after the RC opened. The
   RC's `team/scripts/` commits are the task deliverables themselves, merged
   `--no-ff` by their tasks.
4. Queue files edited by hand? **NO.** Queue mtimes line up with agent
   transitions (coder_backlog 08:26:47 after the CODER-004 merge,
   writer_backlog 09:00:15 after the WRITER-005 merge, tester_backlog
   09:02:06 at this task's dispatch). No anomaly was found.
5. Non-CM remote git operation? **NO.** None of the cycle's task folders
   holds a `git-boundary-guard.jsonl`, and this verification ran no remote
   verb and no role override.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Process artifacts not committed.** TESTER.md "Git workflow" says to
  commit report artifacts on a feature branch off the RC. The wake prompt
  forbids a feature branch, and SOP-core / TESTER.md Step 9 say process
  artifacts never enter a dev tree. The report and gaps live in the task
  folder only. Nothing was committed to the RC by this task.
- **Container-tier harnesses not invoked by TESTER** (Step 5d). Their
  behaviour rests on code review plus the gated suite's stubbed tests. The
  real-engine cold lane (`tests/cold`) and a live attached dashboard remain
  operator-only (CODER-004 notes the cold-lane command).
- **WRITER-005 disclosed** that it ran `deep-verify.sh` once in a scratch
  sandbox with `PGAI_CONTAINER_TIER=1` and `PGAI_AGENT_WAKE_ACTIVE=0`
  overridden, against stub engines, with zero engine calls. No damage resulted. It shows
  the operator-only gate rests on environment signals an agent can override.
  The gate is documented that way, and it is not one of the Step 6.12
  questions. Recorded as an observation, not filed.
- **pass-with-caveat items, not filed:** (a) `lint_docker_helper_path.py`
  misses `${VAR:-docker} pull` and `sudo docker ...` shapes; the shipped tree
  has none. (b) The `kanban.cfg_example` sentence "Scheduled deep-verify runs
  ... pass no --image" is imprecise next to the documented `--scheduled
  --image all` crontab lines.
- **Observations (no spec trace):** `show-queues.sh --details` on a
  multi-project fixture prints a `=== unknown ===` header, byte-identical to
  v2.28.0 (pre-existing). The legacy-root no-project remedy suggests the fixed
  example name `pgai-agent-kanban`. Task sequence 008 is unused in this cycle.
- **Scratch hygiene.** The shared scratch directory
  `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester/` held
  `UNIT_EXIT` / `INTEGRATION_EXIT` from 2026-09-30. They were ignored, and
  only files written after this run started were trusted.
