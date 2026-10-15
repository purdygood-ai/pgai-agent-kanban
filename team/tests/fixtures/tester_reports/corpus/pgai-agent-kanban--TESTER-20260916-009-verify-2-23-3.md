# Verification Report: rc/v2.23.3

## Summary

Verified RC `ai_rc/v2.23.3` (HEAD `6620145`, clean worktree, no stashes)
against `projects/pgai-agent-kanban/requirements/v2.23.3-bugfix-bundle-20260916.md`,
a discovery-bundled BUG bundle of seven items: BUG-0290 (Lane A doc-truth),
BUG-0291 and BUG-0292 (seven provenance hygiene instances), BUG-0293
(provenance-class non-convergence), BUG-0294 (citation-walk docstring
untrue), BUG-0295 (bug-filing provenance and bare-`/tmp` litter), BUG-0296
(CODER and WRITER governance byte budgets over). The RC is six merges over
`ai_main`: 23 files, +508/-46.

Both gated runners exited 0 (`run-unit-tests.sh`: 4624 passed, 31 skipped;
`run-integration-tests.sh`: 510 passed, 1 skipped), and both quality-class
findings files are empty — every lint in both pre-flights reported OK,
including `hygiene` (provenance), `governance_byte_budget`, `doc_command_truth`,
`docs_citation_manifest`, `skip_cites_real_bug`, and `runnable_remedy`. That
is an unusually clean result for this project: the provenance class, red on
nine consecutive releases, reports zero tree-wide here, and the byte-budget
class reports every role under.

Six of the seven bundled items are fully met and were verified behaviourally
rather than structurally where behaviour was claimed — the litter-check remedy
was exercised by planting a bare-`/tmp` file and driving the teardown path, the
bug-claim provenance ledger was exercised across all three claimant-resolution
paths, the Lane A decline described by the rewritten role file was read back
against `_run_lane_a()` in the shipped code, and the new citation-walk contract
test was read for both branches. One acceptance criterion inside BUG-0295 is
half-delivered and is filed as BUG-0297.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Findings

### Finding 1: BUG-0290 — Lane A doc-truth and the undocumented verb

**Category:** pass

`team/roles/OVERWATCH.md` line 45 no longer claims the drill "assumes (a)
already ran"; `grep -n "assumes (a) already ran" team/roles/OVERWATCH.md`
returns nothing. The rewritten paragraph and its new sibling paragraph state
that (a) is the drill's precondition, that an absent attributed
`## Fix (OVERWATCH rewrite)` block makes it decline (b)–(e) with
`overwatch.lane-a-rewrite-missing`, that the decline writes an
`## OVERWATCH Decision` event but no ledger entry (so the single attempt is not
consumed), and that `overwatch-attach-fix-rewrite.sh` clears it.

Every one of those claims was checked against the shipped code rather than
taken on the role file's word. `_run_lane_a()` in
`team/pgai_agent_kanban/overwatch/resolution_stage.py` performs the
`has_attributed_rewrite` check ahead of the drill, calls `write_decision_event`
with `lane=LANE_A`, returns `DECLINED`, writes no ledger entry, and carries the
census anchor `lane-a-rewrite-precondition-01` with a BLOCK-WORTHY class
comment. `_format_lane_a_rewrite_missing` emits the machine key, a
plain-language cause, and a remedy with `--project`, `--kanban-root` and
`--bug-id` pre-substituted. The role file matches the code exactly.

`docs/operator-commands.md` gains a hand-authored
`overwatch-attach-fix-rewrite` section (57 lines) naming all seven flags plus
`--help`, each against the script's own `--help` output: `--project`,
`--kanban-root`, `--bug-id`, `--forbidden-approach`, `--correct-direction`,
`--ground-truth-ref` (repeatable, at least one required), `--timestamp`. The
exit-0/exit-1 contract and the non-idempotence note are stated. Placement is
immediately above the generated `Verb Reference` block, which the RC correctly
did not hand-edit.

`python3 team/scripts/lint_doc_command_truth.py` → 0 findings across all three
of its checks (651 citations, key-parity, pane-parity). Both gated runners
exit 0.

### Finding 2: BUG-0291 / BUG-0292 — the seven provenance instances

**Category:** pass

`python3 team/scripts/lint_hygiene.py --classes provenance` reports
`OK — 0 findings across 8 scan director(y/ies)`, run standalone from the
worktree and again inside both gated runners. All seven named sites are clean:
`overwatch-attach-fix-rewrite.sh:26` (now `--bug-id BUG-NNNN`),
`test_overwatch_scaffolding_gates_resolution.py:244`,
`test_cleanup_root_override.py` (4 sites), and
`test_overwatch_lane_classification.py:222`.

`team/scripts/hygiene-lint-allowlist.json` is byte-unchanged from `ai_main`, so
zero was reached by rewording, not by suppression — the sweep's own claim,
confirmed independently. `findings-unit.json` and `findings-integration.json`
each carry `"findings": []`.

The sweep also caught one site the two bugs never named
(`release-notes/v2.23.2.md:31`, a TESTER task-ID citation added after the bugs
were filed), which is the sweep-not-a-punch-list behaviour BUG-0293 asked for,
demonstrated on its first run.

### Finding 3: BUG-0293 — provenance-class convergence

**Category:** pass-with-caveat

Four of five acceptance criteria are met and verifiable now.

- **Sweep-ticket acceptance is the lint's own zero.** `team/roles/PM.md:141`
  gains "A provenance sweep ticket's acceptance is the lint's own zero",
  stating the acceptance as the one-line lint invocation and forbidding a
  transcribed site list.
- **Sweep sequenced last.** The same section requires `depends_on` on every
  other CODER and WRITER ticket, and reconciles itself against the ride-along
  rule that otherwise puts hygiene tickets last. This RC's own plan already
  obeys it: `CODER-20260916-007-provenance-sweep-tree-wide` depends on tickets
  002, 003, 004, 005 and 006, and merged last (`6620145`).
- **Authoring-time self-check documented.** `team/roles/CODER.md` and
  `team/roles/WRITER.md` both replace the old general paragraph with a
  diff-scoped check (`git diff --name-only <source-branch>...HEAD`), explicitly
  informational — "never a BLOCK, never `Needs Human`, and never a gate
  refusal" — with findings outside the agent's own diff routed to the sweep
  ticket. WRITER additionally documents `--prose-dirs`.
- **Dedup-against-running decision recorded.** A 40-line decision block sits
  above `find_existing_bug_for_class` in
  `team/pgai_agent_kanban/lint/bug_filer.py`, choosing "leave as-is" and giving
  two reasons (a coverage-diff narrowing would reintroduce the per-finding
  flood the module exists to prevent; nothing is lost permanently, only delayed
  by at most one RC).

**Caveats.** (a) The fifth criterion — two consecutive RCs provenance-clean
with no sweep ticket in the second — is not evaluable against one RC; v2.23.3
supplies the first data point only, and the next verification run should check
the second. This is recorded in `gaps.md` as a carry-forward, not as a shortfall
of this RC. (b) The decision text lands in the filer module, which is what
`CODER-20260916-003`'s own acceptance criterion specified; BUG-0293's wording
was "where the next TESTER will read it", and nothing in `team/roles/TESTER.md`
Step 5c points a reader at that module. The decision is recorded and reasoned;
its discoverability from the TESTER side is the caveat. (c) "A ticket generated
from that pattern cannot be completed while the lint is red" is delivered as
ticket-acceptance wording rather than an enforcement mechanism — correctly, since
BUG-0293's own Scope puts making the class blocking out of bounds.

### Finding 4: BUG-0294 — `resolved_target` call contract

**Category:** pass

The docstring paragraph in `walk_docs_citation_closure`
(`team/pgai_agent_kanban/lib/docs_citation_scan.py`) now reads "Consulted only
for a citation `is_resolved` has already accepted — an unresolved citation is
recorded as a finding directly, and `resolved_target` is never called for it",
which matches the branch the call actually sits in. The false advice about
returning `None` to stop the walk is gone. The walk loop is untouched, so there
is no behaviour change — the bug's preferred fix.

`team/tests/unit/test_docs_citation_scan_walk_contract.py` is new and pins both
branches with a spy: with `is_resolved` always False, `resolved_target` records
zero calls while the report carries one finding per citation; with `is_resolved`
always True, it records exactly one call per citation and zero findings. It
drives the shared module directly rather than through either caller's adapter,
which is what makes it catch the drift that both callers' own suites could not
see.

Both callers' now-redundant re-checks were simplified with an explanatory
comment rather than deleted silently.
`python3 team/scripts/check_docs_citations.py` still exits 0 (24 governance
files, 13 citations, all in the manifest).

### Finding 5: BUG-0295 items 1 and 2 — litter remedy and claim provenance

**Category:** pass

**Litter remedy, exercised rather than read.** I planted a bare-`/tmp` file,
took a pre-dispatch snapshot, and drove `wake_tmp_litter_check_and_report`
directly. The log line came back as
`litter check: TESTER-VERIFY left 1 bare /tmp entry(ies): <path> — remedy: rm -f <path>`
and `status.md` gained a `## Temp Litter` section carrying the same
`remedy:` line with the identical command. The file survived the check, which
is the deliberate report-only behaviour the existing load-bearing negative
fixture protects. The new fixture
`test_litter_report_carries_a_runnable_removal_remedy` asserts the remedy
appears in both surfaces and then actually runs it and asserts the file is
gone — a genuinely behavioural test, not a string-shape assertion.

**Claim provenance ledger, exercised in production shape.**
`claim_next_bug_id` now appends `<UTC timestamp>\t<bug id>\t<claimant>` to
`<bugs_dir>/.bug-claims.log`, with claimant resolving explicit `claimed_by` >
`PGAI_TASK_ID` > `"unknown"`. I drove all three paths in a sandbox and got the
three expected lines. The best evidence is from this verification run itself:
filing BUG-0297 below through the RC's own helper wrote
`2026-09-16T06:50:00+00:00	BUG-0297	TESTER-20260916-009-verify-2-23-3`
into the live `bugs/.bug-claims.log` — the first real filing the mechanism has
recorded, attributed correctly, against a bug file whose `Source Task` header
says the same thing. That is exactly the cross-check BUG-0295 asked for.

`team/scripts/lib/wake_driver_invoke.sh:636` exports `PGAI_TASK_ID` alongside
the three pre-existing per-project exports; `task_id` is in scope well before
that line. The two automated aggregate filers
(`lint/bug_filer.py`, `deep_verify/bug_filer.py`) pass their own fixed
identities rather than relying on the env fallback, which is right since
neither is task-scoped.

**Integration hazard checked and cleared.** The new `.bug-claims.log` dotfile
lands inside a directory that several scanners walk. I confirmed it is inert:
`get_unbundled_bugs`, `update_bug_backlog_cache`, `detect_duplicate_bug_ids`
and `next_free_number` all ignore it in a sandbox containing it, and
discovery's item scan (`_disc_find_unhandled_items`) skips non-`.md` files
silently before the filename gate sees them — so it can never be rejected or
quarantined.

`/tmp/BUG-0293-provenance-sweep-convergence.md` no longer exists.

### Finding 6: BUG-0295 item 3 — the swim-lane boundary ships untested

**Category:** gap
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (the safeguard is present and effective today; what is
missing is protection against its silent removal later)
**Regression:** no — the defect predates this RC. Before v2.23.3 there was no
boundary sentence at all and therefore nothing to test; this RC improved the
position and stopped one step short of the criterion. No test for this boundary
exists anywhere in the RC's diff or before it.
**Integrity Damage:** no
**Filed As:** BUG-0297-coder-bugs-write-boundary-untested (Path C-A — filed as
BUG — code defect / regression / test failure)

BUG-0295's fourth acceptance bullet offers two branches: "either the writing
path is fixed **and a test forbids a CODER-lane write into `bugs/`**, or the
entry is recorded as an operator action."

The RC took the first branch and delivered it three-quarters of the way. The
cause was named — `CODER-20260916-003`'s `status.md` carries a full attribution
section concluding a lane violation by the code-writing session over an
operator hand-filing, citing the wake log's litter line, a 70-minute idle gap in
that session's own progress log spanning all three affected mtimes, every other
agent's `lock.contended` exits in the same window, the operator's shell history
showing no trace of the files, and the byte-identical `/tmp` duplicate being a
write-then-place shape rather than a `mv`. The writing path was closed at the
prompt level: `team/roles/CODER.md:138` now reads "**Never write into a
project's `bugs/` directory.**"

No test pins it. `grep -rn "Never write into a project" team/tests/` returns
nothing, and no test references the boundary in any other wording. Both gated
runners exit 0 with the sentence present and would exit 0 with it removed. The
risk is concrete rather than theoretical: the same release had to raise the
CODER governance byte budget because that assembly keeps growing, so a future
trim of that file is a realistic way for the safeguard to vanish unnoticed.

### Finding 7: BUG-0296 — governance byte budgets

**Category:** pass

`budget_bytes_coder` and `budget_bytes_writer` in `kanban.cfg_example` move
from 69632 to 77824. Measured against the shipped tree:

```
ROLE             ASSEMBLED         BUDGET   VERDICT
po                   63167          65536   under
pm                   78200          81920   under
coder                71143          77824   under
writer               70453          77824   under
tester              102384         102400   under
cm                   85578          86016   under
overwatch            78453          81920   under
```

Coder headroom 6681 bytes, writer 7371 bytes — both far above the 1 KB floor
the bug asked for. Every role reads `under`, the gated runners' own
`governance_byte_budget` check reports OK, and both findings files carry zero
`governance_byte_budget` instances. The docs-manifest self-check bullet added
in v2.23.2 is still present in both `team/roles/CODER.md` and
`team/roles/WRITER.md` (one occurrence each), as the bug required, and neither
role file was touched by the rebalance ticket.

The ticket also re-baselined the matching documented defaults in
`team/scripts/lib/config_loader.sh`'s key registry in the same session, which
the doc-truth lint's key-parity check would otherwise have flagged as a config
default mismatch — caught and fixed rather than left as a new finding.

**Observation, not a finding.** The rebalance covered only the two roles the
bug named. `tester` now sits 16 bytes under its budget and `cm` 438 bytes
under — the exact "a budget nobody can author against" condition BUG-0296's
`## Expected` complains about, now relocated to two other roles. Both predate
this RC (identical assembled sizes at v2.23.2) and neither is in the bug's
acceptance, so this is not a shortfall of the RC. The next edit to
`team/roles/TESTER.md` will re-trip the class.

### Finding 8: Requirements-document acceptance criteria

**Category:** pass

- *Each bundled item is addressed in the implementation* — all seven; see
  Findings 1–7. One sub-criterion inside BUG-0295 is partially delivered
  (Finding 6).
- *All Python files compile cleanly* — `python3 -m compileall -q team/` exits 0;
  every `.py` in the diff compiles individually.
- *All bash files pass syntax check* — `bash -n` clean on every `.sh` in the
  diff and across `team/scripts/` tree-wide.

### Finding 9: Pre-flight, state, and structural checks

**Category:** pass

- Worktree HEAD `66201454207adbcf54dda5d924b9f415b7805b38` equals
  `ai_rc/v2.23.3`; `git status --short` empty; `git stash list` empty.
- Pre-flight merge verification: every DONE task's feature branch is already
  deleted (normal post-merge cleanup; no `feature/*` or `ai_feature/*` branches
  remain), so Check 1 and Check 2 are skipped per the lifecycle note. No
  UNMERGED or BUG-92 anomalies.
- `release-state.md`: `Active RC: v2.23.3` with branch `ai_rc/v2.23.3` present;
  `Last Released: v2.23.2`, well-formed, matching the highest tag on the dev
  tree (`ai_v2.23.2`); `semver_gt v2.23.3 v2.23.2` true. No drift. The file's
  mtime (01:50:22) is the RC-open moment and it has not been touched since.
- Prereq graph: all referenced task IDs exist, no cycles, bookends correct
  (`CM-20260916-008-open-rc-2-23-3` at the head,
  `CM-20260916-011-release-2-23-3` WAITING at the tail with all six work
  tickets plus this verification as prerequisites). Queue markers agree with
  every task's state (`[x]` for the six DONE, `[A]` for this task, `[W]` for
  the release bookend).
- Skip-cites-real-bug (Step 6.13): `lint_skip_bug_gate.sh` exits 0. The four
  `BUG-123x` IDs a naive grep surfaces are synthetic fixture strings inside
  `test_skip_bug_citation_scan.py`'s own corpus, verified by reading them.
- Coding-standards spot-check (Step 6.14): no new scripts, so no `--help`
  obligation; no new operational value hardcoded; the provenance lint (which is
  directive 8's enforcement) reports zero over the whole scanned tree.

### Finding 10: Autonomous operation criterion

**Category:** pass

All four checks answer NO — see the dedicated section below.

## Lint Findings (non-blocking)

None. `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`findings-integration.json` were both written by their runners and both carry
`"findings": []`. Every named pre-flight check in both runners reported OK,
including `test_anti_patterns`, `skip_cites_real_bug`, `orphan_tests`,
`api_parity`, `operator_verb_manifest`, `icd_freshness`, `icd_compat`,
`changelog_freshness`, `doc_command_truth`, `help_presence`,
`python_import_completeness`, `halt_scope_writers`, `status_writer_bypass`,
`runnable_remedy`, `governance_byte_budget`, `docs_citation_manifest`,
`bare_tmp_cleanliness`, and `listener_cleanliness`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=8d freshness=stale — the docker and podman claude-abliterated pairs failed on a Dockerfile FROM parse error and podman/rocky9 failed at its entrypoint COPY step; cites BUG-0167-cold-build-claude-abliterated-failure, filed by that deep-verify run, and the record is stale on both counts (8d exceeds the 7-day window, and v2.9.20 is not the v2.23.3 under verification), so nothing has been built cold against this tree.

Per `team/roles/TESTER.md` Step 5d this is reported, not re-filed and not
re-derived: the lane was not run during this verification, the red verdict
already has its own bug, and the record's age changes neither the
recommendation nor the systemic risk above.

## Code Coverage

Informational only; does not enter the recommendation.

- Unit:        line 77%  |  branch 63%   (16489 stmts, 4950 branches; blended 73%)
- Integration: line 10%  |  branch 8%    (16480 stmts, 4950 branches; blended 9%)

Read from `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`integration.json` `totals` objects, using `percent_statements_covered` and
`percent_branches_covered` (the field names were confirmed against the
generated files rather than assumed; `percent_covered` is the blended figure
and is labelled as such).

## Autonomous criterion

1. **Any task force-promoted from WAITING to BACKLOG by hand?** NO. Every
   status.md mtime falls in sequence with its own agent's completion time
   (02:25, 03:19, 03:55, 04:31, 05:23, 06:14), and each carries a wake-written
   promotion summary.
2. **Was `release-state.md` edited outside the CM scripts?** NO. Its mtime is
   `2026-09-16T01:50:22`, the exact RC-open timestamp it records, and the file
   has not been written since.
3. **Were any scripts patched mid-build?** NO. Every commit on the RC belongs
   to one of the six merged feature branches, each authored inside its own
   ticket's session; there are no loose commits on `ai_rc/v2.23.3` outside the
   merge/feature structure.
4. **Were any queue files manually edited?** NO. Queue mtimes track the wake
   script's own marker writes (01:50:59 at RC open, then 04:31 and 06:14 as
   tasks completed), and every marker agrees with its task's recorded state.

Manual interventions required: none.

## Caveats

- **One bundled criterion is carried forward, not closed.** BUG-0293's
  two-consecutive-RCs criterion needs the next RC to evaluate. v2.23.3 is the
  first clean data point. If v2.23.4 ships provenance-clean with no sweep
  ticket in its plan, BUG-0293's convergence claim is demonstrated; if it needs
  another sweep, the process fix did not take and that is the signal to say so.
- **`PGAI_TASK_ID`'s shell half is not directly tested.** The four new
  `bug_scanner` tests set the variable by monkeypatch; nothing drives
  `wake_driver_invoke.sh` and asserts the export happens. There is no precedent
  for testing that export site (the three sibling exports beside it are equally
  untested), so this is noted rather than filed. The export itself is sound —
  `task_id` is in scope from the top of `wd_invoke_task`, and the variable is
  correctly absent from this session's environment because the live install is
  still running v2.23.2.
- **Pre-existing bare-`/tmp` residue remains.**
  `/tmp/PRIORITY-0003-docs-manifest-authoring-guard.md` (mtime 2026-09-14) is
  still on the host. It predates BUG-0295's filing, was never named by it, and
  is a PRIORITY rather than a bug report, so it falls outside that bug's
  acceptance. The litter check is report-only by deliberate design, so old
  residue is expected to persist until someone runs the remedy the check now
  emits.
- **`overwatch-attach-fix-rewrite.sh --help` still prints `e.g. BUG-0163`.**
  The shell wrapper's usage comment was reworded to `BUG-NNNN` by the sweep,
  while the argparse help string it delegates to
  (`team/pgai_agent_kanban/overwatch/fix_rewrite.py:437`) keeps the concrete
  example. This is not a lint violation — `e.g.` is one of the provenance
  class's deliberate example-marker exceptions — and the file is untouched by
  this RC. Cosmetic inconsistency only; recorded because the two surfaces now
  disagree.
- **A shipped release note was edited.** `release-notes/v2.23.2.md` lost the
  parenthetical naming the v2.23.2 verification task as part of the tree-wide
  provenance sweep. The change is cosmetic and the file is prose that the
  sweep's scan surface legitimately covers, but it does mean an already-published
  release note differs from what was tagged.
