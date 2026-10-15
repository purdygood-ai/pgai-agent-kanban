# Verification Report: ai_rc/v2.22.0

## Summary

Verified release candidate `ai_rc/v2.22.0` (commit `389edfe`, 22 files,
+8382/-281) against
`projects/pgai-agent-kanban/requirements/v2.22.0-docs-truth-rollup.md` — "Every
public claim is one that is already true." The RC lands a doc-truth lint
(`team/scripts/lint_doc_command_truth.py`, three checks: command existence /
`--help`-parses, config-key parity, dashboard window-pane parity), three
generator plus `--check` pairs (operator verb reference, dashboard window tour,
measured-pace block), a four-stage stranger's-path run-through fixture
(`team/scripts/harness/strangers-path-check.sh`), and regenerated
README / ARCHITECTURE / HOW_TO / OPERATIONS / operator-troubleshooting /
docker / dashboard documents.

Both gated runners exit 0. The three generators' `--check` modes are green and
their drift guards were exercised by seeding edits into scratch copies — each
named the drifted item, exited non-zero, and wrote nothing. The stranger's-path
fixture was re-run independently for this report and passed all four stages
(container, install, board, helloworld) with exit 0, so the second half of the
release's Acceptance is met and demonstrated rather than taken on trust.

The first half is not. `python3 team/scripts/lint_doc_command_truth.py` exits 1
with 55 command findings plus 46 key-parity and 12 pane-parity findings, so the
Acceptance clause "Doc-truth lint green" is unmet. The completion note for
`CODER-20260914-024-strangers-path-fixture` records that clause as MET, but the
evidence it quotes is four suites of the pre-existing
`team/scripts/harness/doc-lint.sh` harness rather than the lint tickets 1 and 2
delivered; those are different programs, and only the former is green.

Nothing regressed. Of the 96 distinct document-and-line sites the lint flags,
zero sit on a line this RC added — every flagged citation is context this RC
left untouched. The RC's real effect is that defects which were previously
invisible are now enumerated, which is what the release set out to build.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Findings

### Finding 1: Release Acceptance clause "Doc-truth lint green" is not met

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks — the five filings below)
**Systemic Risk:** low (documentation accuracy; no runtime path affected)
**Regression:** no — established by diffing every finding site against the RC's
own diff: of 96 distinct `<doc>:<line>` sites the lint reports, 0 fall inside a
hunk this RC added. Every one is unchanged context that shipped in v2.21.2 and
earlier.
**Integrity Damage:** no
**Filed As:** decomposed into BUG-0268, BUG-0269, BUG-0270, BUG-0271, BUG-0272,
BUG-0273 (Path C-A — filed as BUG — code defect / regression / test failure).
No separate bug is filed for the roll-up.

Evidence, run from the RC worktree:

```
$ python3 team/scripts/lint_doc_command_truth.py ; echo "EXIT=$?"
lint_doc_command_truth: scanned 509 citation(s) across 9 document(s); 55 finding(s).
lint_doc_command_truth: key-parity: scanned 90 key citation(s) across 11 document(s) against 56 registry key(s); 46 finding(s) (11 loader key(s) no scanned document mentions).
lint_doc_command_truth: pane-parity: scanned 43 window/pane citation(s) across 9 document(s) against 15 census window id(s) (16 row(s)); 12 finding(s) (0 census window(s) no scanned document describes).
EXIT=1
```

Finding headlines by class: 47 `MISSING COMMAND`, 33 `UNKNOWN CONFIG KEY`, 12
`DASHBOARD WINDOW NAME MISMATCH`, 11 `UNDOCUMENTED CONFIG KEY`, 8 `HELP FAILS`,
2 `CONFIG DEFAULT MISMATCH`.

The five documents the requirement names as its deliverable are much cleaner
than the manifest set: scanned explicitly, `README.md`, `HOW_TO.md`,
`ARCHITECTURE.md` and `docs/docker.md` produce 4 command findings (all of them
Findings 4 and 6 below), 1 key finding, and 11 worklist-direction pane entries —
no missing commands, no stale window names. The regeneration work did its job
on the documents it was pointed at; the shortfall is in the wider manifest set
(`docs/OPERATIONS.md` alone carries 55 of the 96 sites; `kanban.cfg_example` 15 and `project.cfg_example` 12 more) and in the check itself.

### Finding 2: Key-parity check reports genuinely-accepted configuration keys as unknown

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium — a check that is wrong about thirty of its
thirty-three assertions trains its readers to ignore it, and a CODER acting on
one of the false findings would delete correct documentation. This is the
failure the release's own Intent names ("it teaches them to distrust the rest of
the page"), reproduced in the tool built to prevent it.
**Regression:** no — the check is new in this RC and no prior behavior existed
to break; the shipped documents it misjudges are unchanged.
**Integrity Damage:** no
**Filed As:** BUG-0268 (Path C-A)

The check treats `team/scripts/lib/config_loader.sh`'s `# Key Registry` comment
block as the system's accepted key set. It is not, in three ways:

1. The registry's own opening line reads "Every **kanban.cfg** key known to the
   wake chain is listed here", but the check validates `project.cfg_example`
   against it, so all twelve ordinary project.cfg keys are reported unknown —
   including `[project] branch_prefix`, which is what produces this project's
   own `ai_` branch prefix.
2. Keys read through `read_ini` / `config_get` outside `config_loader.sh` are
   invisible to it. `[governance] budget_bytes_cm` is read at
   `team/scripts/lint_governance_byte_budgets.sh:229`;
   `[dashboard] snapshot_ttl_seconds` at
   `team/scripts/dashboard/lib/snapshot.sh:124`; `[docker] docker_exe` at
   `team/scripts/lib/docker_engine.sh:67`; `[paths] cleanup_retention_days` at
   `team/scripts/cleanup/cleanup.sh:97`; `[release] max_rc_retries` at
   `team/scripts/lib/project_paths.sh:1019`. All are reported unknown.
3. `team/scripts/lib/config_loader.sh:427` reads `[wake] zero_progress_cap`, and
   the registry block does not list it, so `kanban.cfg_example:322` documenting
   it is reported unknown.

The check is not uniformly wrong — `docs/OPERATIONS.md:930` genuinely documents
`max_tasks_per_wake` under `[chain]` where the loader reads it under `[wake]`.
That real catch is currently buried under roughly thirty false ones.

### Finding 3: Installed reference documents cite scripts that were renamed and no longer exist

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks; mostly mechanical, two citations need a
decision)
**Systemic Risk:** low
**Regression:** no — none of the 47 citations sits on a line this RC added; the
rename predates this RC and the documents have been stale since it happened.
**Integrity Damage:** no
**Filed As:** BUG-0269 (Path C-A)

`cm-open-rc.sh` (14 citations), `cm-cancel-rc.sh` (11), `cm-release.sh` (7),
`cm-finalize.sh`, `cm-open-doc.sh`, `dashboard-create.sh`,
`dashboard-column-render.sh`, `$KANBAN_ROOT/team/scripts/dashboard.sh` (5),
`halt_after/drain.py` (2) and `aggregate_tokens.py` all resolve to nothing:

```
$ find . -path ./.git -prune -o -name 'cm-open-rc.sh' -print
$ ls team/scripts/cm/
cancel-rc.sh  finalize-release.sh  finalize.sh  open-doc.sh  open-rc.sh  promote_bundled_items.py  push-watchdog.sh  release.sh
```

`docs/OPERATIONS.md` carries the bulk, and it is on
`team/config/docs-manifest.txt`, so the dead citations are copied onto every
installed and upgraded kanban root. `docs/OPERATIONS.md:1915` instructs a reader
recovering from a botched RC to run `cm-open-rc.sh v0.15.5`.

### Finding 4: `pseudocron.py --help` starts the scheduler instead of printing usage

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low — the side effect is a scheduler the caller did not ask
for, which they can kill; nothing is corrupted.
**Regression:** no — `team/scripts/pseudocron.py` is not in this RC's diff. The
new lint is what surfaced it.
**Integrity Damage:** no
**Filed As:** BUG-0270 (Path C-A)

```
$ timeout 10 python3 team/scripts/pseudocron.py --help ; echo "rc=$?"
Terminated
rc=124
```

`grep -n 'argparse\|sys.argv' team/scripts/pseudocron.py` returns nothing:
`main()` (line 164) resolves the kanban root, reads `pseudocron.cfg`, installs
SIGINT/SIGTERM handlers, and enters `while True:` at line 215 without ever
looking at its arguments. Cited at `docs/docker.md:120` and `docs/docker.md:451`.

The existing `team/scripts/lint_help_presence.py` reports `87 script(s)
checked, 0 exempt, 0 offenders` because its scan set is the `.sh` files under
`team/scripts` and `team/scripts/cm`; a `.py` entry point beside them is outside
its view. That is why this survived until a lint that reads documents rather
than directories went looking.

### Finding 5: Two installed documents describe the previous dashboard layout

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Regression:** no — neither passage is on a line this RC added.
**Integrity Damage:** no
**Filed As:** BUG-0271 (Path C-A)

`docs/OPERATIONS.md:1138-1142` lists window 0 as `Status`, 1 as `logs`, 2 as
`terminal`, 3 as `attention`. `team/config/dashboard_windows.census` — the
tree's declared authority — says `0 main`, `1 operator`, `2 visibility`,
`3 attention`, with `logs` at 9 and `terminal` at 12. Only window 3 matches.

`docs/cm-release-reference.md:53` tells the reader a pending `HUMAN-APPROVE`
gate surfaces on "dashboard window 14 (human-review)" and line 55 says the
approval commands are copy-pasteable from window 14. The census puts
`human-review` at 13; window 14 is `drill-N`.

### Finding 6: `dashboard/create.sh --help` dies of SIGPIPE intermittently

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Regression:** no — `team/scripts/dashboard/create.sh`'s help path is not in
this RC's diff, and the same flake is recorded independently in
`WRITER-20260914-022-regen-operations-trouble`'s completion note, including a
finding citing a document line that task never touched.
**Integrity Damage:** no
**Filed As:** BUG-0272 (Path C-A)

```
$ for i in 1 2 3; do timeout 20 bash team/scripts/dashboard/create.sh --help >/dev/null 2>&1; echo "attempt$i rc=$?"; done
attempt1 rc=0
attempt2 rc=141
attempt3 rc=141
```

141 is 128+13 (SIGPIPE). Because the doc-truth lint invokes `--help` on every
cited command, this makes its `HELP FAILS` count differ between runs against a
byte-identical tree.

### Finding 7: The doc-truth lint is invoked by nothing, and its default scan set omits four of the five documents the release names

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks, sequenced after Finding 2)
**Systemic Risk:** medium — `team/SOP.md` ("No silent skips") states the rule
this breaks: a check nobody can see the result of is indistinguishable from a
check that passed. A future TESTER report that does not happen to run this lint
by hand says nothing about doc truth, which reads as "fine".
**Regression:** no — the lint is new in this RC; nothing previously ran it
either.
**Integrity Damage:** no
**Filed As:** BUG-0273 (Path C-A)

```
$ grep -rn "lint_doc_command_truth" --include='*.sh' --include='*.py' \
    --include='*.txt' --include='*.cfg' --include='*.json' . \
  | grep -v '^./.git' | grep -v '^./team/scripts/lint_doc_command_truth.py' \
  | grep -v '/tests/'
$
```

No output. In particular the name appears in neither
`team/scripts/lib/gated_runner_lint.sh`, `team/scripts/run-unit-tests.sh`,
`team/scripts/run-integration-tests.sh`, nor
`team/scripts/harness/doc-lint.sh`. Consistent with that,
`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` from this run's gated
unit runner contains only `hygiene` entries.

Second half: the default scan set is `team/config/docs-manifest.txt`, whose nine
scanned entries do not include `README.md`, `HOW_TO.md`, `ARCHITECTURE.md`, or
`docs/docker.md` — four of the five documents the requirement's first
Deliverable names. Checking them requires twelve explicit flags.

### Finding 8: Suites, generators, fixture, and structural criteria — all pass

**Category:** pass

- **Gated unit runner.** `bash team/scripts/run-unit-tests.sh --coverage` →
  `UNIT_EXIT=0`; `4463 passed, 31 skipped in 750.04s`.
- **Gated integration runner.**
  `bash team/scripts/run-integration-tests.sh --coverage` →
  `INTEGRATION_EXIT=0`; `464 passed, 1 skipped in 510.90s`.
- **Stranger's-path fixture (Acceptance half 2).** Re-run independently for this
  report: `SP_EXIT=0`, all four stages PASS — container (image
  `pgai-agent-kanban-debian:latest` built, `docs/docker.md:146` launch, mount
  contract held), install (`docs/docker.md:115`, `Installation complete!`),
  board (pseudocron started, dashboard session up with 14 windows), helloworld
  (`helloworld-flow-check.sh --compare` exit 0, outcome files match the
  committed goldens). Every step is reported with the `docs/docker.md:<line>` it
  was taken from, which is the "docs verbatim" property the Acceptance asks for.
- **Generators, `--check` green.** `generate_operator_verb_docs.py --check` →
  `OK -- all 27 verb block(s) match the manifest and their own --help output`
  (27 = the manifest's active-verb count, and 27 `OPERATOR-VERBS-GENERATED:BEGIN`
  markers are present in `docs/operator-commands.md`).
  `dashboard/generate_window_docs.py --check` → `OK -- all surfaces match the
  census` (16 tour rows = 16 census `W` rows).
  `generate_measured_pace_docs.py --check` → `OK -- both docs match the measured
  rollups`.
- **Drift guards exercised (Step 6.5).** Seeding a changed flag into a `halt`
  verb block in a scratch copy of `docs/operator-commands.md` produced
  `DRIFT -- verb='halt' block disagrees with its --help output`, exit 1, file
  md5 unchanged. Seeding a wrong pane count for window 5 into a scratch
  `docs/dashboard.md` produced `DRIFT -- docs/dashboard.md: window tour entry
  for window=5 name='git' variant='-' disagrees with the census`, exit 1, file
  unchanged. Both refusals carry the dual-channel contract.
- **Measured-pace honesty (Deliverable 2).** The rendered block reads "5.4–142.7
  min, median 17.3 min. Sample: 112 RC(s), 864 task(s)" — the measured value,
  not the brief's estimated 13–17 min/task, which is the correct precedence. No
  `10–20 minutes` fiction survives anywhere in the tree
  (`grep -rn "10–20 minutes\|10-20 minutes\|10 to 20 minutes" --include=*.md`
  returns nothing). Thin-data and no-data paths were exercised against scratch
  rollup directories: one rollup renders "thin sample (1 RC(s)); treat as
  provisional", zero rollups renders "not available yet -- no per-RC metric
  rollups were found"; neither fabricates a number.
- **Deliverable 1 spot-checks.** All ten flags `install.sh --help` accepts are
  documented in `HOW_TO.md`, including `--no-system-cron` and the deprecated
  `--no-crontab` / `--crontab-tier=none`. `docs/OPERATIONS.md` now carries the
  deep-verify material (8 case-insensitive hits, 0 at the RC base);
  `docs/operator-troubleshooting.md` leads with `why.sh` (20 hits, 0 at the RC
  base). `docs/docker.md` states the board-up path as 9 ordered `### Step N`
  headings. The README agent roster reconciles one-for-one with the seven files
  under `team/roles/`.
- **Link integrity.** All 107 relative links across `README.md`, `HOW_TO.md`,
  `ARCHITECTURE.md`, `docs/docker.md`, `docs/dashboard.md`,
  `docs/OPERATIONS.md`, `docs/operator-commands.md` and
  `docs/operator-troubleshooting.md` resolve; 0 dead. The four
  `team/scripts/harness/doc-lint.sh` suites (`links`, `cmd`, `fossil-greps`,
  `feature-greps`) all exit 0.
- **Census and gate compliance.** `lint_gate_census_compliance.py` → `OK -- 36
  file(s) checked, 0 offenders`. The RC's four new anchors
  (`doc-command-truth-01`, `doc-key-truth-01`, `doc-pane-truth-01` as `CONVERT`;
  `strangers-path-check-stage-failed-01` as `BLOCK-WORTHY`) each resolve to
  exactly one source marker and one matching Locator Index line.
  `lint_operator_verb_manifest.py` → `0 drift finding(s)`.
  `lint_skip_bug_gate.sh` → `OK — all skip annotations are compliant`;
  `0 missing files` (the four `BUG-123x` greps hit only fixture literals inside
  `team/tests/unit/test_skip_bug_citation_scan.py`).
- **Release-state drift (Step 6.1).** `pp_last_released_version` returns
  `v2.21.2`; `semver_gt v2.22.0 v2.21.2` is true. `## Active RC` reads `v2.22.0`
  and matches the live `ai_rc/v2.22.0` branch; `## Last Released` is a
  well-formed `v2.21.2`.
- **Pre-flight (Steps 2a, 3, 3a).** Worktree HEAD `389edfe` equals
  `ai_rc/v2.22.0`; `git status --short` empty; `git stash list` empty. All ten
  prerequisite tasks are DONE with their feature branches merged and deleted
  (normal post-merge cleanup), so both merge checks are satisfied; no live
  feature branch remains to examine anywhere in the project.
- **Prereq graph (Step 6.10).** 23 tasks in the v2.21.1–v2.22.0 span; no cycles,
  every referenced task ID exists, sequence numbers 015–026 non-colliding,
  bookends correct (`CM-20260914-025-open-rc-2-22-0` at the start,
  `CM-20260914-028-release-2-22-0` waiting at the end).
- **Synthetic release simulation (Step 6.8).** Skipped with note: the RC's diff
  touches no release-lifecycle file — no `team/scripts/cm/*`, no
  `project_paths.sh`, nothing in the open/close cycle.
- **Coding-standards spot-check (Step 6.14).** No bug ID, task ID, or version
  provenance appears in any of the five new source files
  (`lint_doc_command_truth.py`, `generate_operator_verb_docs.py`,
  `generate_measured_pace_docs.py`, `strangers-path-check.sh`,
  `doc_step_extract.py`). Every new script ships a `--help` naming its arguments;
  each generator exposes path overrides rather than hardcoding operational
  paths, which is what made the drift-guard verification above possible without
  touching the worktree.

## Code Coverage

- Unit:        line 75%  |  branch 62%   (15806 stmts, 4744 branches; blended 72%)
- Integration: line 7%   |  branch 6%    (15797 stmts, 4744 branches; blended 7%)

Read from `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`integration.json` `totals` objects (`percent_statements_covered` /
`percent_branches_covered`). Informational only; coverage does not enter the
suite-pass precondition or the recommendation.

## Lint Findings (non-blocking)

Three quality-class findings, identical in
`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`findings-integration.json`. One lint class (`hygiene`), so one group — and
every instance is already covered by an open bug, so nothing new is filed.

- `hygiene` — provenance: `team/tests/unit/test_operator_verb_manifest.py:159`
  and `:176` name the internal task ID `CODER-20260911-066` in comments —
  cites BUG-0260 (open).
- `hygiene` — provenance: `release-notes/v2.20.2.md:58` names the internal task
  ID `TESTER-20260913-013` — cites BUG-0267 (open).
- The umbrella filing the lint raises for the class as a whole is BUG-0261
  (open).

These do not, on their own, change the `## Recommendation` or `## Systemic Risk`
above.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=7d freshness=stale — the docker and podman claude-abliterated pairs failed on a Dockerfile FROM parse error and the podman/rocky9 pair on its entrypoint COPY step; cites BUG-0167, filed for that failure and since closed, so the last recorded lane result is both red and stale on both counts (7d is at the edge of the 7-day window, and v2.9.20 is not the v2.22.0 under verification) and nothing is known about whether this tree's three Dockerfiles build cold.

## Autonomous criterion

Manual interventions required: none.

1. Any task force-promoted from WAITING to BACKLOG by hand? **NO.** This task's
   own `status.md` records "Promoted from WAITING to BACKLOG by wake script:
   prerequisites satisfied"; the other ten carry no promotion note and reached
   DONE through their agents.
2. `release-state.md` edited outside the CM scripts? **NO.** Its mtime is
   `2026-09-14 12:17`, matching its own `## RC Opened At:
   2026-09-14T12:17:15+00:00` written by `cm/open-rc.sh`; it has not been
   touched since.
3. Any script patched mid-build? **NO.** Every commit between `ai_main` and
   `ai_rc/v2.22.0` is a task feature-branch merge or a commit on one; there is
   no out-of-band commit to `team/scripts/` or `subagents/` during the RC.
4. Any queue file manually edited? **NO.** `tasks/queues/*_backlog.md` mtimes
   line up with wake-script activity, and `logs/activity.log` records no
   host-level action during this RC.

## Caveats

- **The stranger's-path fixture was run for real on this host.** It built the
  `pgai-agent-kanban-debian:latest` image, launched a container, installed into a
  throwaway root under the framework temp directory, and brought up a tmux
  dashboard on a private socket (`tmux -L <unique>`), so no operator session was
  touched. Nothing under `$KANBAN_ROOT` was mutated.
- **The board stage reports 14 windows against a 15-id census.** Window 14
  (`drill-N`) is created on demand when an operator drills into an RC or
  document, so 14 at session creation is the expected shape. The fixture reports
  the count as an observation and asserts nothing against the census. Not a
  finding.
- **`docker/entrypoint.sh --help` appears twice in the lint's `HELP FAILS` list**
  (`docs/docker.md:281`, `:397`) but exits 0 when run from the worktree. It is
  environment-sensitive: with a scrubbed environment it prints a
  `PGAI_AGENT_KANBAN_ROOT_PATH is not set` error and its remedy. Whether the
  lint should sanitize the environment for a container entrypoint is a design
  question, not a defect I can demonstrate, so it is recorded here rather than
  filed. It is subsumed by BUG-0269's scope if the ticket chooses to address it.
- **`docs/operator-api.md` carries four of the false key findings**
  (`[api] host` / `[api] port` at lines 155, 156, 161, 162). They are BUG-0268's
  business, not that document's.
- **`team/config/docs-manifest.txt` has a known one-hop closure gap**, recorded
  and deliberately left by `WRITER-20260914-022`: `docs/operator-commands.md` is
  on the list and cites `docs/operator-troubleshooting.md`, which is not, and
  closing it correctly is a four-line addition
  (`operator-troubleshooting.md`, `DASHBOARD-PANES.md`, `disk-hygiene.md`,
  `quarantine-recovery.md`). That is a scoped decision about the installed
  document set rather than a defect in this RC; it is noted here for the
  operator and not filed.
- **Prior-run artifacts.** The task's `artifacts/` directory was empty at the
  start of this run; no `.previous-RUN-N` file existed. Every result above comes
  from a fresh execution.
