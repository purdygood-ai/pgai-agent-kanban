# Verification Report: rc/v2.10.0

**Task:** TESTER-20260907-039-verify-2-10-0
**RC branch:** `ai_rc/v2.10.0` @ `aaae6923bfa2b2df3d54122950cdc3e70d1112ea`
**Baseline:** `ai_main` (last released `v2.9.23`, resolved via `pp_last_released_version`)
**Requirements:** `requirements/v2.10.0-lean-governance-read-orders.md`
**Verified:** 2026-09-07, fresh run (artifacts/ was empty at start; no prior report existed)

## Summary

v2.10.0 splits the kanban's governance stack so each agent reads only what its
role needs. It retires the 2010-line `team/SOP.md`, replaces it with a
279-line `team/SOP-core.md` plus per-role procedure absorbed into seven
rewritten role files, adds a per-role read-order manifest under
`team/read-order/`, makes the wake composer assemble each agent's governance
sentence from that manifest, and adds a narrating per-role byte-budget lint
with budgets in `kanban.cfg [governance]`. The mechanism works, and works
well: manifest isolation, manifest ordering, the two fallback paths, the
project-README splice, and all four lint verdict states were exercised
behaviourally and behave exactly as `docs/read-order-manifests.md` describes.
The size reduction is real -- TESTER's assembled stack falls from roughly
261 KB to 113 KB, CODER's to 61 KB. What the release does not do is reach the
sizes it set for itself: TESTER lands at 112591 bytes against a 92160 budget
and CM at 82540 against 71680, which is exactly what the release's own
clean-tree fixture asserts against, so the unit gated runner exits 1. The
integration runner exits 0 (337 passed, 1 skipped). Beyond the budget miss,
verification found one governance contradiction the split itself created
(`TESTER.md` and `PM.md` now tell their agents that `release-state.md` has no
`## Last Released` field and that finding one is drift, while `CM.md`, the
operations guide, the project seeder and the live file all say otherwise), one
unmeasured acceptance criterion (the helloworld behaviour flow could not be
run -- no such project is registered), one cosmetic manifest-comment drift, and
one pre-existing verification-integrity defect surfaced by the red suite (a
failing unit runner leaves the previous run's lint findings file on disk for
the next TESTER to ingest as current). None of these makes the release
unusable; all five are filed and heal on the next loop.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Suite Results (authoritative -- gated runners)

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | **1** | 3220 passed, 1 failed, 3 skipped (399.81s) |
| `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 337 passed, 1 skipped (216.47s) |

The single unit failure is
`tests/unit/test_composer_governance_byte_budget.py::test_every_role_governance_assembly_stays_under_configured_budget`.
Both breakage-class pre-flights passed; every quality-class lint except
`comment_provenance` reported OK. Evidence for every suite claim in this
report is the gated runners' exit codes, never a direct-pytest line.

## Code Coverage

- Unit:        line 74%  |  branch 61%   (14136 stmts, 4316 branches)
- Integration: line  8%  |  branch  6%   (same denominators)

Source: `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`.../integration.json`, `totals` object, `covered_lines/num_statements` and
`covered_branches/num_branches`. Informational only; coverage does not enter
the recommendation.

## Findings

### Finding 1: TESTER and CM exceed their governance byte budgets; unit suite red

**Category:** gap
**Fix Effort:** medium (3-5 tasks, at least one in WRITER's lane)
**Systemic Risk:** low (isolated to this release's own size target; the chain runs normally)
**Regression:** yes (the failing fixture is new in this RC and the tree does not satisfy it)
**Integrity Damage:** no
**Filed As:** BUG-0174 (Path C-A -- filed as BUG: code defect / regression / test failure)

The release's headline acceptance criterion -- "Composer fixture prints
assembled byte counts per role; all under budget on a clean tree" -- is not met
for two of seven roles, and the fixture that asserts it fails, so "Full suite
passes" is not met either.

Measured on the RC tree, via `lint_governance_byte_budgets.sh --kanban-cfg kanban.cfg_example`
and reproduced identically by the unit fixture:

```
ROLE             ASSEMBLED         BUDGET   VERDICT
po                   55719          57344   under
pm                   65527          65536   under
coder                61013          61440   under
writer               60763          61440   under
tester              112591          92160   OVER
cm                   82540          71680   OVER
overwatch            66546          66560   under
```

The brief's own Intent targets are split: CODER's "< 60KB" is met at 61013
bytes (59.6 KiB); TESTER's "< 90KB" is missed at 112591 bytes (110.0 KiB), 22%
over. CM is 15% over. Components: the shared four documents total 41755 bytes
(`DIRECTIVES.md` 1804, `OVERVIEW.md` 6202, `SOP-core.md` 25091, `README.md`
8658), on top of which sit `roles/TESTER.md` at 70836 and `roles/CM.md` at
40785.

The overrun is not a surprise to the release. `CODER-20260907-035` flagged it,
`WRITER-20260907-036` documented it (the example table in
`docs/read-order-manifests.md` shows the same two `OVER` rows), and
`CODER-20260907-037` audited it as its closing ticket and concluded no repair
exists inside CODER's lane -- trimming role-file content is WRITER's
role-governance lane, and `budget_bytes_tester = 92160` is a literal
transcription of the brief's own target, so raising it revises the brief. That
reasoning is sound and the honest recording is the right call; the criterion is
nonetheless unmet, and a red gated runner is a `gap` finding whatever its
provenance. The repair is a WRITER trimming pass (BUG-0174 names the largest
movable block: TESTER.md's Test Authoring Guidelines, which CODER and WRITER
also need and which could live in a document their manifests name) or an
explicit operator decision to revise the target.

The lint itself behaves exactly as specified -- narrate, never block, exit 0 in
every state. That half of the deliverable passes; see Finding 6.

### Finding 2: Role files disagree on the `release-state.md` schema, and the split removed the correction

**Category:** bug
**Fix Effort:** small (1-2 tasks)
**Systemic Risk:** medium (a standing false positive in the verification lane, recurring on every future RC)
**Regression:** yes (the wrong claim predates this RC, but this RC removed the corrective statement from TESTER's and PM's reading lists)
**Integrity Damage:** no
**Filed As:** BUG-0175 (Path C-A)

Shipped in the same tree:

- `team/roles/TESTER.md:167` -- "The schema holds exactly three fields --
  `Active RC`, `RC Opened At`, `RC Opened By Task` -- and no `Last Released`
  ... Check ... that no leftover `## Last Released*` field survived the
  install migration."
- `team/roles/PM.md:21` -- "`release-state.md` has no `Last Released` field to
  read."
- `team/roles/CM.md:54` -- "holding four fields -- `## Active RC`,
  `## RC Opened At`, `## RC Opened By Task`, `## Last Released` ... written by
  `cm/release.sh` Step 15 after the tag is created ... exists for exactly one
  consumer, `halt_after/drain.py`."
- `docs/OPERATIONS.md:319` -- "Writes the canonical schema (`## Active RC`,
  `## RC Opened At`, `## RC Opened By Task`, `## Last Released`)."

The tree agrees with CM.md: `create-project.sh:635` seeds the field,
`cm/open-doc.sh:200-226` preserves it, and this project's live
`release-state.md` carries `## Last Released: v2.9.23`. A TESTER following the
shipped Step 6.1 literally runs a check whose passing condition is the absence
of a field the release script writes on every ship, and reports
`DRIFT: release-state.md still contains Last Released* fields after migration`
against a healthy file -- on every RC, indefinitely.

Attribution matters here. The wrong claim is pre-existing: `ai_main`'s
`team/roles/TESTER.md` lines 625 and 641-645 carry it, as does its `PM.md`.
What v2.10.0 changed is reachability. On `ai_main`, `team/SOP.md` lines
520-553 carried the authoritative four-field schema and *every* role read
`SOP.md` on every wake, so the wrong statement was preceded by the right one.
This RC retired `SOP.md`, did not carry that passage into `SOP-core.md`, and
placed it in `CM.md` -- a file named in neither `read-order/tester.txt` nor
`read-order/pm.txt`. The lean split is working as designed; the design's cost
is that a fact previously guaranteed by shared reading can now be stated two
different ways with nothing to notice. This is the first observed instance of
that class, which is why the systemic risk is `medium` rather than `low`.

No content was lost overall -- the schema, the HALT-AFTER drain semantics, and
the Test Authoring Guidelines all landed somewhere. The defect is which role
can reach which statement.

### Finding 3: The helloworld behaviour-unchanged criterion was never exercised

**Category:** gap
**Fix Effort:** medium (3-5 tasks)
**Systemic Risk:** medium (the behavioural half of a seven-role rewrite is unmeasured, and the release line will keep leaning role files)
**Regression:** no (the demo has been unrunnable on this host since at least the v2.9.20 decomposition, which is why `engine-behavior-baseline.sh` was built)
**Integrity Damage:** no
**Filed As:** PRIORITY-0001 (Path C-B -- filed as PRIORITY: enhancement / design change)

Acceptance bullet 3's second clause asks that "agent behavior fixtures
(decompose, verify, ship) [are] unchanged on the helloworld flow." The flow was
not run. `team/demos/helloworld-demo` exists in the tree but requires a
registered `pgai-helloworld` project; `projects.cfg` on this host registers
only `[project:pgai-agent-kanban]` (verified independently).
`CODER-20260907-037` recorded this as an environment gap naming the
unavailable resource -- correct per SOP's "an unavailable build environment
does not block" doctrine -- and substituted
`team/scripts/harness/engine-behavior-baseline.sh` evidence:
`tests/integration/test_engine_behavior_baseline_harness.py` passed 2/2 against
golden snapshots in this verification's own integration run.

That evidence is real but narrower than the criterion. A snapshot comparison
does not run a decompose -> verify -> ship cycle with the new per-role
governance stacks in the agents' prompts, which is precisely where a role file
that lost a needed procedure would show up. No positive evidence of a
behaviour change was found; the point is that the release's central
behavioural claim rests on inference rather than a run. Filed as a priority
rather than a bug because the remedy -- making the demo runnable against a
self-registering throwaway root -- is a structural improvement the current code
was never asked to provide.

### Finding 4: Four of seven read-order manifests carry a stale header comment

**Category:** bug
**Fix Effort:** small (1 task)
**Systemic Risk:** low (comment text; no runtime effect)
**Regression:** yes (introduced in this RC, and partially corrected within it)
**Integrity Damage:** no
**Filed As:** BUG-0176 (Path C-A)

`team/read-order/coder.txt:8`, `tester.txt:8`, `writer.txt:8` and
`overwatch.txt:8` still read "team/scripts/lib/read_order.sh resolves this file
into an ordered list of absolute paths; **nothing consumes that list yet**."
The composer consumes it -- that is deliverable 2, verified working in Finding 5
below. `cm.txt`, `pm.txt` and `po.txt` were updated at commit `32ee8cc`; the
other four were missed, so the seven sibling files now disagree about whether
the mechanism is live. The resolver strips `#` lines, so there is no runtime
effect; the cost is that a maintainer opening `tester.txt` is told the exact
opposite of the rule `docs/read-order-manifests.md` opens with.

### Finding 5: Read-order manifests and the composer -- behaviourally verified

**Category:** pass

Deliverables 2 and 3 were exercised directly, not inferred. Four cases were run
against a synthetic root under `$PGAI_AGENT_KANBAN_TEMP_DIR`, driving
`_wd_build_governance_line` from `team/scripts/lib/wake_driver_invoke.sh` with
the real `read_order.sh`:

1. **Isolation.** A document present in the tree but absent from the manifest
   (`SECRET-NOT-IN-MANIFEST.md`) does not appear in the assembled governance
   sentence. The sentence names exactly the five manifest entries, in manifest
   order.
2. **Manifest edit, no code change.** Appending that document to the manifest
   makes it appear, with no change to `wake_driver_invoke.sh`.
3. **Missing manifest -> fallback.** Deleting the manifest falls back to the
   hardcoded five-document list and narrates the degradation through `log()`.
4. **Dangling lines -> fallback.** A manifest naming only non-existent files
   resolves to zero entries and takes the same fallback path.

The project-README splice lands immediately after the kanban-root `README.md`
entry, as documented. All seven `SUBAGENT` tokens the wake layer dispatches
(`wake_driver_prep.sh:91-96`, plus `overwatch`) have a matching manifest file.
`install.sh:714` ships `read-order` alongside `roles` and `scripts`, so a
manifest added in the dev tree reaches a live install with no installer change.
`team/templates/retired-files.txt` registers `SOP.md` for retirement in
v2.10.0, and `tests/unit/test_upgrade_fossil_advisory.py` covers the upgrade
path that moves a stale root `SOP.md` to the graveyard.

The unit suite carries 16 dedicated tests across
`test_wake_composer_governance_manifest.py` (13),
`test_composer_manifest_isolation.py` (3) and `test_read_order_bash.py` (8),
all passing.

One observation, not a finding: when a manifest does not put the role file
last, the trailing entry loses the "your role file at ..." phrasing. Every
shipped manifest puts it last and the code comments the assumption, so nothing
is wrong today.

### Finding 6: The byte-budget lint narrates and never blocks -- as specified

**Category:** pass

`team/scripts/lint_governance_byte_budgets.sh` exits 0 in every state
exercised: all roles under, two roles OVER, a role with a non-numeric budget
(`invalid-budget`), a role with no key (`unbudgeted`), and a missing config
file (all roles `unbudgeted`). `--verbose` narrates the resolved config path,
surfaces the resolver's own stderr, and prints the closing reminder that the
result is narrated, not a gate. `--help` names every argument. The script
sources `read_order.sh` rather than re-parsing manifests, carries
`set -euo pipefail`, and correctly carries no census anchor or dual-channel
contract because it has no refusal. `docs/read-order-manifests.md` describes
all of this accurately, including the two `OVER` rows.

`tests/unit/test_lint_governance_byte_budgets.py` (10 tests) passes.

### Finding 7: A red unit suite leaves a stale lint findings file behind

**Category:** bug
**Fix Effort:** small (1-2 tasks)
**Systemic Risk:** medium (silently attributes another branch's lint findings to the RC under verification, and loses the real ones)
**Regression:** no (pre-existing; `ai_main`'s `run-unit-tests.sh` has the identical `exit 1` at line 541 and findings write at line 610, and this RC's diff to that file is comment-only)
**Integrity Damage:** no (verification data, reconstructible by re-running the suite)
**Filed As:** BUG-0177 (Path C-A)

Surfaced by this run's red unit suite, under Step 6.11's
verification-integrity spot-check. `run-unit-tests.sh` writes
`findings-unit.json` at line 610 under a comment promising it is "written
unconditionally ... so a downstream consumer ... can rely on the file's
existence as proof this pre-flight ran," but the pytest-failure branch exits
at line 541 first. After this run's 15:32-15:39 UTC unit invocation -- which
did produce a real `comment_provenance` finding -- the on-disk
`findings-unit.json` still carried `generated_at: 2026-09-07T11:14:45+00:00`
with every path inside pointing at
`worktrees/CODER-20260907-027-composer-obeys-manifest/`. The integration
runner, which exited 0, wrote its file normally and logged
`[gated-runner] findings file written`; the unit output has no such line.

The temp root is shared across worktrees and projects, and nothing in the file
or in TESTER's Step 5c distinguishes a current file from a stale one. This
report's Lint Findings section below is therefore taken from the unit runner's
captured stdout for the unit suite, and from the freshly written
`findings-integration.json` for the integration suite.

### Finding 8: Pre-flight, structure, constraints, and process checks

**Category:** pass

- **Pre-flight merge verification.** All 42 DONE tasks with a live
  `## Feature Branch` value have branches already deleted post-merge (normal
  Procedure A cleanup). No unmerged branch, no BUG-92 zero-commits anomaly.
- **Working tree.** Worktree HEAD `aaae692` equals `ai_rc/v2.10.0`,
  `git status --short` empty, `git stash list` empty.
- **6.1 release-state drift.** `pp_last_released_version` returns `v2.9.23`;
  `semver_gt v2.10.0 v2.9.23` holds. `## Active RC: v2.10.0` matches the live
  branch. `## Last Released: v2.9.23` is well-formed -- its presence is by
  design per `CM.md` and is *not* reported as drift here (see Finding 2 for
  why the shipped TESTER.md would report it).
- **6.2 / 6.3 structural and content.** All seven manifests, `SOP-core.md`
  (25091 bytes, inside the stated <25KB target), `read_order.sh`,
  `lint_governance_byte_budgets.sh` (both executable),
  `docs/read-order-manifests.md` (345 lines, read fully -- accurate throughout,
  including its honest example table showing the two `OVER` roles).
  `team/SOP.md` is gone from the tree and registered in `retired-files.txt`.
  The rewritten `TESTER.md` preserves its PROTECTED SURFACE marker, the five
  Core Principles, Step 6.5 verbatim, the Anti-Patterns list, and absorbs the
  Test Authoring Guidelines with all six anti-patterns and the
  skip-cites-a-real-bug rule.
- **6.8 synthetic release simulation.** The diff touches release-lifecycle
  files (`cm/cancel-rc.sh`, `cm/open-rc.sh` -- comment-only changes).
  `tests/integration/test_cm_release_synthetic.py` ran in the integration
  suite: 15 passed.
- **6.9 constraint negatives.** No hardcoded secrets, no bare `/tmp` or
  `$HOME` paths, and no bug/task/version provenance identifiers in any new
  file. `lint_governance_byte_budgets.sh` carries `set -euo pipefail`;
  `read_order.sh` deliberately does not, correctly, being a sourced library
  that must not alter the caller's shell. `bash -n` passes on all changed
  shell files and `py_compile` on all changed Python.
- **6.10 prereq graph.** Twelve work tickets `026`-`037` plus `CM-038` open-RC
  and this TESTER task. No cycles, no colliding sequence numbers, every
  referenced task ID exists, bookends correctly linked. All thirteen
  prerequisites are DONE with `Needs Human: no`.
- **6.13 skip-cites-real-bug.** `lint_skip_bug_gate.sh` ran green inside the
  unit runner: no placeholder IDs, 0 missing bug files for cited IDs.
- **6.14 coding standards.** `lint_help_presence` OK across 79 scripts;
  `lint_engine_size_dupe` OK; the new lint's `--help` names every argument;
  no operational value hardcoded where an operator would want to change it
  (budgets live in config, the manifest list lives in data files); no
  duplicated implementation -- both the lint and the composer resolve manifests
  through `read_order.sh` alone.

## Lint Findings (non-blocking)

- `comment_provenance` -- `release-notes/v2.9.22.md:31` carries the task
  identifier `TESTER-20260907-017` in the Known Issues text; the check must
  describe behaviour, not internal IDs -- **cites BUG-0173 (open)**. Reported
  by both runners. No duplicate filed.

Unit-suite findings were read from the unit runner's captured stdout rather
than `findings-unit.json`, because the red suite prevented that file from
being written and the on-disk copy is four hours stale from a different
worktree -- see Finding 7. Integration-suite findings were read from the
freshly written `findings-integration.json` (`generated_at`
`2026-09-07T15:43:12+00:00`, 1 finding, same class).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=0d freshness=stale — the last lane run was this morning and so is well inside the 7-day window, but it exercised v2.9.20 and not the v2.10.0 under verification, so it is stale on version; its red verdict names the docker/podman claude-abliterated FROM parse error and the podman/rocky9 symbolic-chmod COPY, both of which are BUG-0167 (already marked done, fixed in a prior RC and confirmed here by `lint_dockerfile_static` reporting 0 violations across 3 Dockerfiles), so nothing about the current tree's images has been built cold since that fix landed.

## Autonomous criterion

**Manual interventions required: none.** All four checks NO.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. All thirteen
   RC tasks reached DONE with `Needs Human: no`; no status.md shows a manual
   state edit.
2. **`release-state.md` edited outside the CM scripts?** NO. Its four fields
   are consistent with `cm/open-rc.sh` having run at
   `2026-09-07T10:51:18+00:00` for `CM-20260907-038-open-rc-2-10-0`, and no
   HALT event is recorded.
3. **Scripts patched mid-build?** NO. All 43 commits on the RC are attributable
   to merges of the twelve planned feature branches. The one extra branch,
   `ai_feature/CODER-20260907-028-governance-byte-budgets-fixup`, is an
   in-ticket autonomous self-repair documented in that task's own status.md
   (an `env_bootstrap.sh` sourcing omission its own regression run caught),
   not an outside patch.
4. **Queue files edited by hand?** NO. Queue mtimes track agent activity
   through the RC window; no HALT or HALT-AFTER flag exists at either scope.

Five of the thirteen tasks recorded `## Instruction Conflicts` and resolved
them autonomously with reasoning, which is the designed behaviour rather than
an intervention. `CODER-20260907-037`'s conflict entry is the clearest: it
names the tension between "the full suite passes" and "record the measured
byte counts, whatever they are," chose the explicitly-authorised escape hatch,
weakened no assertion, and said so. That is the autonomous loop working, and
it is why Finding 1 is a clean, well-documented `gap` rather than a surprise.

## Caveats

- **Systemic risk is `medium`, driven by Findings 2 and 7, not by Finding 1.**
  The byte overrun is hygiene: the chain runs, every agent reads its stack,
  every task completes, and the only cost is tokens. What raises the rating is
  that this RC produced the first instance of a new drift class (a shared fact
  restated differently in two role files with nothing to reconcile them), and
  that a red suite can hand the next verifier another branch's lint findings.
  Both are cheap to fix and both are filed.
- **The measured improvement is large even though the target was missed.**
  TESTER's assembled governance falls from roughly 261 KB on `ai_main`
  (`SOP.md` 129412 + `TESTER.md` 115530 + the three shared files) to 112591
  bytes here -- a 57% reduction. Four roles sit within 700 bytes of their
  budgets, so any future growth in `SOP-core.md`, `OVERVIEW.md` or `README.md`
  is multiplied sevenfold and will push `pm` (9 bytes of headroom) and
  `overwatch` (14 bytes) over immediately. That fragility is worth an
  operator's attention when BUG-0174 is triaged.
- **The budgets are absent from live installs.** They ship in
  `kanban.cfg_example`, and `install.sh` seeds `kanban.cfg` only on a fresh
  install. On this upgraded install the lint reports all seven roles
  `unbudgeted`. That is documented, deliberate behaviour
  (`docs/read-order-manifests.md`: "A missing key is not an error"), so it is
  recorded as an observation, not a finding -- but it does mean the budget
  signal is inert on every existing install until an operator adds the section
  by hand.
- **`OVERWATCH` has a manifest and a budget but no `SUBAGENT` mapping** in
  `wake_driver_prep.sh`, because it runs outside the RC pipeline on its own
  path. The manifests document this as catalog completeness. Noted as an
  observation; nothing in the requirements asks for more.
- **A stale `integration_exit.txt` from 2026-09-04 was present in the shared
  tester temp directory** and briefly read as this run's exit code before its
  mtime gave it away. It was moved aside, and the integration verdict in this
  report comes from the completed run's own captured output. Same root shape
  as Finding 7: shared, unversioned temp state that no consumer can date.
