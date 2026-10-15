# Verification Report: ai_rc/v2.20.0

**Task:** TESTER-20260911-074-verify-2-20-0
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.20.0-operator-verbs-and-halt-family.md`
**RC branch / commit:** `ai_rc/v2.20.0` @ `02158bac344d8c49f6a8ab98d8e55fdd6b46807e`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260911-074-verify-2-20-0` (detached HEAD, clean, no stashes)
**Verified:** 2026-09-12

## Summary

Verified RC v2.20.0 — "Every fleet posture is one idempotent verb with a REST
twin, and the halt family speaks versions" — against its requirements
document, its twelve contributing tasks, and the shipped tree. Pre-flight
merge verification passed (all twelve prerequisite tasks DONE, feature
branches cleaned up post-merge, worktree HEAD matches the RC tip). The core
deliverables are present and behaviourally correct: the machine-readable
operator verb manifest reconciles against the tree in both directions and its
drift detection fires on planted drift; the runnable-remedy lint catches a
planted angle-bracket placeholder and finds zero offenders across 240 real
files; `halt-after --until` refuses all three of its specified failure modes
dual-channel while writing nothing to disk; and the RC-close gate was
exercised end to end — a satisfied drain defers and latches while an RC is
open, survives a later unsatisfied re-check, and promotes only once Active RC
reads `none`. The gated unit suite passed (`UNIT_EXIT=0`, 4319 passed / 36
skipped). The gated integration suite did not (`INTEGRATION_EXIT=1`): one
test asserts the prose of a refusal this RC legitimately replaced with the
shared project-name resolver — a stale assertion, not a production defect,
but the suite-pass precondition for a `PASS` recommendation is therefore not
met. Beyond that, four of the verb set's stated cross-cutting properties are
only partly delivered: five of the verbs the requirements doc names by hand
still have no dual-channel refusal at all, and two of the read verbs do not
resolve project keys by prefix — with `show-queues` silently rendering an
all-zero board for a name it never resolved. Seven findings are filed via
Path C; none of them makes the release unusable.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Suite Results

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 4319 passed, 36 skipped in 632.84s — PASS |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **1** | 452 passed, 1 failed, 1 skipped in 406.10s — FAIL |

The failing test is
`tests/integration/test_deep_verify_run.py::test_unregistered_project_refuses_dual_channel_and_writes_no_record`.
The PASS verdict cited here is the gated runners' exit codes, not a direct
`pytest` invocation.

## Code Coverage

Informational only; does not affect the recommendation.

- Unit:        line 75% | branch 62%  (blended 72%; 15721 stmts, 4712 branches, 11859 covered lines, 2915 covered branches)
- Integration: line 7%  | branch 6%   (blended 7%)

Source: `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`coverage/integration.json`, `totals` object (`percent_statements_covered`,
`percent_branches_covered`).

## Findings

### Finding 1: Integration suite fails on a stale refusal-wording assertion

**Category:** stale-assertion
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one assertion in one test; production behaviour is correct and still refuses, dual-channel, writing no ledger record)
**Regression:** yes (this RC's change to `deep-verify.sh` is what made the asserted literal unreachable)
**Integrity Damage:** no
**Filed As:** BUG-0249 (Path C — autonomous follow-up filing, non-blocking; Path C-A — filed as BUG)

Stale-assertion evidence, per the Step 7 requirements:

- **Test file and line:** `team/tests/integration/test_deep_verify_run.py:318`
- **Failing assertion:** `assert "not registered" in result.stderr` — expected
  the literal `not registered`; actual stderr was
  `error:   project_resolve.not_found` /
  `message: pp_resolve_project_name: no project matches 'nonexistent-project' in <root>/projects.cfg.` /
  `remedy:  Check the project name against projects.cfg (registered: ).`
- **Production code change:** `team/scripts/deep-verify.sh`, the block
  inserted at ~line 228 — `_dv_project="$(pp_resolve_project_name "${_dv_project}")" || exit 1`
  — placed ahead of the pre-existing `projects_cfg_has` check. The literal
  `not registered` lived in that check's message
  (`ai_main:team/scripts/deep-verify.sh:240`), which the RC's own comment now
  describes as "effectively unreachable".
- **RC commit / task:** `8c7ec4a` / `f03e472` (CODER-20260911-063,
  verb-keys-and-idempotence), merged at `6f68d8f`.
- **Recommendation:** update the test in the next iteration to assert on the
  stable machine key `project_resolve.not_found` rather than on prose. The
  test, not the production code, is what needs to change. The verb's other
  guarantees are unaffected and still hold — exit 1, all three dual-channel
  fields present, no ledger record written.

### Finding 2: not-found remedy degenerates to an empty list and supersedes a more useful refusal

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (a weaker refusal message; no state is damaged and no operation proceeds wrongly)
**Regression:** yes (introduced by wiring `pp_resolve_project_name` in front of the verbs in this RC)
**Integrity Damage:** no
**Filed As:** BUG-0250 (Path C-A)

Against a kanban root whose `projects.cfg` lists nothing, the
`project_resolve.not_found` remedy renders as
`Check the project name against projects.cfg (registered: ).` — the entire
advice is "compare your name against this list", and the list is empty
(`_pp_join_names` returns the empty string). It also now fires ahead of
`deep_verify.unregistered_project`, whose remedy named the fixing command
(`add-project.sh --project X`). That superseded site is unreachable yet is
still indexed in `docs/blocking-site-census.md` as
`team/scripts/deep-verify.sh:deep-verify-01 BLOCK-WORTHY`; the census lint
reconciles anchors by name against file text, not by reachability, so it
passes over a censused stop that can no longer occur.

### Finding 3: five verbs in the named set have no dual-channel refusals

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks — nine scripts, each needing an error key, a remedy, a census class and, where BLOCK-WORTHY, an anchor plus index line)
**Systemic Risk:** low (the verbs still refuse and still exit non-zero; what is missing is the machine-readable channel)
**Regression:** no (the plain-stderr validation predates this RC — `halt-global.sh`, `unhalt-global.sh` and `wake-now.sh` are untouched by the RC diff, and `verify-rc-state.sh:165`'s `ERROR: <project> is required.` is present verbatim at `ai_main:team/scripts/verify-rc-state.sh:163`)
**Integrity Damage:** no
**Filed As:** BUG-0251 (Path C-A)

The deliverable states the verb set carries "dual-channel refusals". Five
verbs the doc names by hand do not: `halt-global`, `unhalt-global`,
`wake-now`, `show-queues`, `verify-rc-state`. Four more outside the doc's
list (`switch-provider`, `create-project`, `add-project`, `remove-project`)
are in the same state — 9 of 27 active verbs. The RC records this honestly
rather than concealing it: `_DUAL_CHANNEL_SCENARIOS` in
`team/tests/unit/test_verb_fixture_matrix.py` marks each one `NotApplicable`
with the reason "validates its own arguments with plain stderr lines, not the
shared pgai_emit_error_contract/dual-channel emitter". That honesty is worth
noting, but an inventory of where a property is absent is not the property.
`verify-rc-state.sh:165` additionally prints a literal angle-bracket
placeholder (`ERROR: <project> is required.`) to the operator; the
runnable-remedy lint does not see it because it scans emitter call sites only.

### Finding 4: read verbs do not resolve project keys by prefix; show-queues renders a false all-zero board

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (read-only surfaces; the misleading board could prompt a wrong operator judgement but changes no state)
**Regression:** no (`git show ai_main:team/scripts/dashboard/show-queues.sh` has no project validation either; this RC touched the file only to add `OPERATOR_VALID_FLAGS`)
**Integrity Damage:** no
**Filed As:** BUG-0252 (Path C-A)

Behaviourally verified against the live kanban root (one registered project):

```
$ bash team/scripts/dashboard/show-queues.sh --project pgai-agent-kanban
pm: 36/36  coder: 155/155  writer: 40/40        (real counts)
$ bash team/scripts/dashboard/show-queues.sh --project pgai
pm: 0/0  coder: 0/0  writer: 0/0  tester: 0/0   (exit 0)
$ bash team/scripts/dashboard/show-queues.sh --project totally-not-a-project
pm: 0/0  coder: 0/0  writer: 0/0  tester: 0/0   (exit 0)
$ bash team/scripts/verify-rc-state.sh pgai
ERROR: project directory not found: .../projects/pgai   (exit 2)
```

`pgai` is an unambiguous prefix that the shared resolver does resolve
(`pp_resolve_project_name pgai` → `pgai-agent-kanban`). Neither script calls
it. The zero board is the more serious half: zeros are indistinguishable from
a genuinely idle project, so the failure is silent.

### Finding 5: manifest lint's drift refusal names the default manifest, not the file scanned

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (the gated-runner invocation passes no `--manifest`, so the two paths coincide there)
**Regression:** yes (the file is new in this RC)
**Integrity Damage:** no
**Filed As:** BUG-0253 (Path C-A)

`team/scripts/lint_operator_verb_manifest.py:314` interpolates
`ovm.default_manifest_path()` into the `operator_verb_manifest.drift`
message. Verified by scanning a deliberately drifted copy: the lint correctly
found all three planted drifts (missing script, undeclared flag, non-existent
route) but reported the failure against
`team/config/operator_verbs.manifest`, which it had not read. The remedy's
re-run command likewise omits `--manifest`.

### Finding 6: provenance litter in a new shipped config file, unseen by the hygiene lint

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (hygiene)
**Regression:** yes (both the file and its comments are new in this RC)
**Integrity Damage:** no
**Filed As:** BUG-0254 (Path C-A)

`team/config/operator_verbs.manifest` names `CODER-20260911-071`, "ticket 3",
"ticket 9" and "ticket 12 (WRITER)" in its shipped comment block. The
`hygiene` quality-class lint flagged the same class of reference in the RC's
test files but not here, because its file selection does not include
`team/config/*.manifest`. Both halves — the litter and the blind spot — are in
the filed bug.

### Finding 7: placeholder sweep replaced a remedy with a canned reason the refusal itself forbids

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (wording of two refusals)
**Regression:** yes (introduced by this RC's sweep, commit `0bd8ba6`)
**Integrity Damage:** no
**Filed As:** BUG-0255 (Path C-A)

`team/scripts/cm/release.sh`'s two operator-force refusals state that "an
operator override must carry a written reason, never a default one" and then
hand the operator a pasteable default:
`--reason "operator override: shipping despite the ship-policy matrix stop"`.
Message and remedy contradict each other in the same emission. Related, milder
instances noted in the bug: `defer.sh` and `halt-after.sh` remedies now
hardcode this install's project name (`pgai-agent-kanban`) and version
literals (`v2.20.0`, `v2.20.1`) as examples, which will not be runnable on a
different install — though both name `$PGAI_PROJECT_NAME` first, so they stay
actionable.

### Finding 8: operator verb manifest and its reconciliation check

**Category:** pass

`team/config/operator_verbs.manifest` declares 27 verbs with script, flag
vocabulary, mutation, idempotence class and REST twin. Every verb the
requirements doc names is present and `active`; no row is `planned`.
`python3 team/scripts/lint_operator_verb_manifest.py` exits 0 — "27 verb(s)
checked, 42 router route(s) scanned, 0 drift finding(s)". Guard logic
exercised by planting three drifts in a copy (a script path that does not
exist, a flag the script does not accept, a route no router declares): the
lint caught all three, named each one, and refused dual-channel with a
runnable remedy (exit 1). `python3 team/scripts/lint_api_parity.py` also
exits 0 over 19 script/model pairs.

### Finding 9: runnable-remedy lint and the placeholder sweep

**Category:** pass

`python3 team/scripts/lint_runnable_remedy.py` on the real tree: "OK — 240
file(s) checked, 0 offenders" (166 bash, 74 python), exit 0. Guard logic
exercised with a synthetic pair: a call site whose remedy contained
`--project <name>` was flagged with file, line, the extracted placeholder and
a per-instance remedy, and the run refused dual-channel (exit 1); the
byte-identical call site with a literal project name was not flagged. The
"no angle-bracket placeholders anywhere in emitted remedies" deliverable holds
for every emitter call site in the tree. (Its limitation — plain `echo`
refusals are outside the lint's scope — is Finding 3.)

### Finding 10: halt-after `--until`, rc scope only

**Category:** pass

All three specified refusals exercised directly against the live tree; each
emitted all three dual-channel fields with a placeholder-free remedy, exited
1, and wrote no sentinel (`projects/pgai-agent-kanban/HALT-AFTER` still
absent afterwards):

- `--project pgai-agent-kanban --key coder --until v2.21.0` →
  `halt-after.until_scope_mismatch`, "--until targets a release boundary;
  scope 'coder' has none."
- `--until v2.21.0` with no project and no `$PGAI_PROJECT_NAME` →
  `halt-after.project_required`
- `--project pgai-agent-kanban --until 2.9` → `halt-after.until_malformed`,
  "not well-formed semver (expected vX.Y.Z)"

The scope-mismatch check is correctly ordered ahead of the project-required
check, so it fires whether or not a project was named. `--help` lists the new
flag and its rc-scope-only restriction. The three refusals match the wording
SOP.md specifies for this family, and each carries a census anchor with a
matching Locator Index line.

### Finding 11: HALT-AFTER converts only at RC close

**Category:** pass

Exercised `pgai_agent_kanban.halt_after.rc_gate.evaluate` directly against two
synthetic project roots.

Versioned token `rc:v9.9.9`, Active RC open:
`NOT_DRAINED` → (ship recorded, RC still open) `DEFERRED` with
`HALT-AFTER.rc-gate` latch written, `HALT` absent, `HALT-AFTER` still present
→ (Active RC set to `none`) `PROMOTED`, latch removed, `HALT` created,
`HALT-AFTER` removed.

Role token `coder`, the requirements doc's literal scenario: drain satisfied
mid-RC → `DEFERRED` + latch; a CODER task then appears in `WORKING` so the
condition is no longer satisfied → still `DEFERRED` (the latch wins, and the
condition is not re-derived); RC closes with that CODER task still `WORKING` →
`PROMOTED`. The "deferring a satisfied drain must never lose it" invariant
holds under exactly the sequence that would break a naive implementation.

CM's ship-time spend is wired at `release_finalize.sh:439`
(`cm_release_spend_halt_after_sentinel`, Step 12c) through the same evaluator
the wake loop calls, and `release_halt_after_spend.sh` returns 0 on every path
so a bookkeeping failure cannot roll back an already-cut tag. The integration
fixtures for the boundary
(`test_halt_after_rc_close_gate.py::test_role_token_drain_defers_through_rc_close_then_promotes`,
`::test_latched_drain_is_not_undone_by_a_later_unsatisfied_recheck`,
`::test_cm_release_spends_versioned_sentinel_when_shipped_version_matches_target`,
`::test_cm_release_spends_versioned_sentinel_when_shipped_version_skips_past_target`)
all passed in the integration run.

### Finding 12: release-locks verb

**Category:** pass-with-caveat

`team/scripts/release-locks.sh` (new, 342 lines, executable) lists every lock
under `locks/` with its provenance and a `held`/`stale`/`unparseable` verdict,
and releases a named lock with a live-holder guard behind `--force`. Exercised:
`--help` (exit 0, documents the four exit codes), no-flag listing (exit 0,
read-only), `--bogus` (uniform unknown-argument refusal, exit 1),
`--release no-such-lock` (exit 3, `release_locks.not_found` dual-channel, its
remedy naming the listing command and the prefix rule).

Caveat, no finding: the verb's `--help` renders `--release` as a bare
`  --release` with no value placeholder and no description, because the shared
`operator_args_render_help_for_flags` has no built-in description for that
flag name; the script compensates with appended lines that read as detached
from the option list. Second caveat: the manifest declares `release-locks`
`mutates=yes` with a `GET /release-locks` twin, which is deliberate and
documented — the GET route is the read-only listing half and returns 422 if a
caller supplies `release` or `force`.

### Finding 13: gates prescribe their unblock verb

**Category:** pass

Reviewed the sweep (commit `06e4e3d`). `discovery.sh`'s per-project and global
HALT guards now emit dual-channel refusals naming
`team/scripts/unhalt.sh --project ${PROJECT_NAME}` and
`team/scripts/unhalt-global.sh`; `discovery_guards.sh`'s Active-RC guard names
`team/scripts/unwind-rc.sh --project ... --key ...`;
`lock_provenance.sh`'s contended-lock path names
`team/scripts/release-locks.sh --release ${_lock_name}`. Each interpolates the
resolved value rather than a placeholder, which is why the runnable-remedy
lint is clean over them.

### Finding 14: census compliance

**Category:** pass

`python3 team/scripts/lint_gate_census_compliance.py` exits 0 — "35 file(s)
checked, 0 offenders", 137 census-anchored sites exempted. The RC adds 13
Locator Index entries, each paired with a `# CENSUS-ANCHOR:` marker in the
named file: three in `halt-after.sh`, two in `discovery.sh`, one in
`lock_provenance.sh`, two in `project_paths.sh`, three in `release-locks.sh`,
and two CONVERT-class entries for the new lints. (The one census weakness
found is reachability, not compliance — see Finding 2.)

### Finding 15: REST twins and ICD

**Category:** pass

Seven new endpoints ship: `POST /operations/{defer,undefer,deep-verify,wake-now}`
in `routers/operations.py` and `GET /{show-queues,verify-rc-state,release-locks}`
in `reads.py`. `docs/api/ICD_VERSION` and
`team/pgai_agent_kanban/api/ICD_VERSION` both read `1.7.0` and agree; baselines
`icd-v1.6.0.json` and `icd-v1.7.0.json` are added and both listed in
`docs/api/baselines/SUPPORTED`. The `why` verb's twin is `GET /verdicts`,
which resolves through the same `verdict_resolve_project()` engine as the CLI,
and the manifest documents that choice. WRITER recorded, and resolved in
favour of the implementation, a ticket-text claim of "nine REST endpoints"
that the merged tree contradicts — the diff shows exactly seven new paths,
which this verification independently confirms.

### Finding 16: per-verb fixture matrix

**Category:** pass

`team/tests/unit/test_verb_fixture_matrix.py` (1020 lines) parametrizes five
columns over all 27 active verbs: `--help` exits 0 and lists exactly the
verb's own declared flags (no leakage from other verbs' vocabularies), an
undeclared flag is refused with the uniform message, a declared refusal emits
all three dual-channel fields with a placeholder-free remedy, an idempotent
verb produces a byte-identical tree hash on repeat invocation, and the REST
twin reproduces the CLI's machine error key. Coverage tests fail naming any
manifest verb missing from a scenario registry, and that claim is itself
proven behaviourally by loading a manifest with a planted bogus verb. All 16
verbs the manifest declares `idempotent` have a real repeat-invocation
fixture — the "coverage gap" `NotApplicable` branch is unused. The
`NotApplicable` entries in the dual-channel (9) and REST-parity (13) columns
each carry a written reason; the dual-channel ones are Finding 3.

### Finding 17: operator documentation

**Category:** pass

`docs/operator-commands.md` gains sections for `show-queues`,
`release-locks`, and `verify-rc-state`, a `--until` row in the halt-after flag
table, prose on why `--until` is rc-scope only, and an error-key table whose
`halt-after.until_scope_mismatch` and `halt-after.until_malformed` message
strings match what the script actually printed in Finding 10.
`docs/operator-api.md` documents all seven new endpoints with their
parameters, including the two non-obvious ones (`verify-rc-state` maps
`project` to a positional argument, not a `--project` flag;
`GET /release-locks` forbids `release`/`force` with 422).

### Finding 18: prerequisite graph and RC state

**Category:** pass

`bash team/scripts/verify-rc-state.sh pgai-agent-kanban` reports
**0 errors, 14 warnings, 89 ok**. Checks a–e (release-state consistency,
queue↔folder, status↔marker, Prerequisites references resolve, release-state↔
git branch) are all clean. The 14 warnings are check f bundle-invariant notes
about PRIORITY backlog entries marked done but not present in any active
requirements file — historical bookkeeping that predates this RC and is not
RC-specific.

Independently: `## Last Released` reads `v2.19.1` and is well formed;
`pp_last_released_version` resolves to the same value from the tag
`ai_v2.19.1`; `v2.20.0` is strictly greater by semver; `## Active RC` reads
`v2.20.0` and the `ai_rc/v2.20.0` branch exists at the verified commit.

### Finding 19: syntax, shell hygiene, and skip gate

**Category:** pass

`bash -n` over every changed `*.sh` and `python3 -m py_compile` over every
changed `*.py` in the RC diff: clean, no output. The new operator scripts
(`release-locks.sh`, `halt-after.sh`) carry `set -euo pipefail`; the new
sourced libraries correctly omit it, matching the convention of every existing
library in `team/scripts/lib/`. Skip-cites-real-bug gate: no `BUG-SKIP` or
non-numeric `BUG-` placeholder in any live test (the only grep hits are
archived runner logs under `team/tests/artifacts/`), and no skip annotation
cites a `BUG-NNNN` that lacks a file in `bugs/`.

## Lint Findings (non-blocking)

Ingested from `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (11
findings) and `findings-integration.json` (11 findings). Both files carry the
same two lint classes; grouped by class across both suites, per the
one-bug-per-lint-class rule.

- `hygiene` — 5 instances, `severity: quality`. Task-ID provenance references
  in shipped text: `team/tests/unit/test_operator_verb_manifest.py:155` and
  `:172` (`CODER-20260911-066`, both new in this RC),
  `team/tests/unit/test_posture_bar_truth.py:20` (`CODER-20260911-049`),
  `release-notes/v2.16.0.md:42` (`TESTER-20260910-070`),
  `release-notes/v2.19.0.md:37` (`TESTER-20260911-053`). — cites **BUG-0245**
  (open). No duplicate filed.
- `governance_byte_budget` — 6 instances, `severity: quality`. `po`
  65540/65536, `coder` 72840/69632, `writer` 71170/69632, `tester`
  103889/102400, `cm` 89547/86016, `overwatch` 76366/73728 (assembled/budget
  bytes; role-scoped, no file or line). — cites **BUG-0246** (open). No
  duplicate filed.

Neither class moved the gated runners' exit codes, and neither raises
`## Recommendation` or `## Systemic Risk` on its own. The separate
`team/config/operator_verbs.manifest` provenance litter is *not* in either
findings file — the lint does not scan that path — and is therefore recorded
as Step-7 Finding 6 (BUG-0254) rather than here.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=5d freshness=stale — the last lane run was red, with docker/claude-abliterated and podman/claude-abliterated failing on a Dockerfile FROM parse error and podman/rocky9 failing at its COPY --chmod step; the record is inside the 7-day window but names v2.9.20, not the v2.20.0 under verification, so nothing has been built cold against this tree. The claude-abliterated failure is the one BUG-0167 describes, and that bug is now closed (`## Status: done`); no open bug currently names these pairs. Per the DEEP-VERIFY tier's rules this is reported, not re-filed, and it does not move this RC's recommendation or systemic risk. TESTER ran no container build and started no container engine.

## Autonomous criterion

1. **Tasks force-promoted from WAITING to BACKLOG manually?** NO. All twelve
   prerequisite tasks show `## State: DONE`, `## Needs Human: no`, and no
   status.md carries an intervention note.
2. **`release-state.md` edited outside the CM scripts?** NO. Its four fields
   are internally consistent with the live branch state and the git tags.
3. **Scripts patched mid-build?** NO. All 35 commits between `ai_main` and the
   RC tip carry the single configured agent identity and arrive through the
   twelve feature-branch merges the task folders name; there is no
   out-of-band commit touching `team/scripts/` outside a ticket's own merge.
4. **Queue files manually edited?** NO. Queue mutations are consistent with
   wake-script appends; `verify-rc-state.sh` check c (status↔marker
   consistency) reports zero errors.

**Manual interventions required: none.**

One in-lane judgement call is worth recording as healthy autonomy rather than
intervention: WRITER-20260911-072 found its ticket text claiming "nine REST
endpoints" where the merged tree has seven, and that `GET /verdicts` "only
becomes real in this release" where it shipped in ICD 1.4.0. It resolved both
in favour of the implementation, per its own task constraint, and wrote the
conflict into its `## Instruction Conflicts` section. Independent diffing of
`docs/api/icd.json` against the baselines confirms seven new paths.

## Caveats

- **The integration-suite failure is a test defect, not a production defect.**
  The verb it exercises still refuses, still emits all three dual-channel
  fields, and still writes no ledger record. Only the asserted prose changed.
  A reader deciding what this RC is worth should weigh Finding 1 as a test
  that needs one line changed, not as broken behaviour. It does, however,
  mean the suite-pass precondition for `PASS` is not met, which is why the
  recommendation is `SHIP-WITH-CONCERNS` rather than `PASS`.
- **`release-locks.sh` reports `unparseable` for every lock on this host.**
  The three live lock files under `locks/` are zero bytes with a sibling
  `.provenance` file, written by the v2.9.x-era implementation installed at
  the kanban root; the RC's `pgai_lock_acquire` writes provenance into the
  lock file itself, which is what `release-locks.sh` reads. The verdict
  degrades gracefully (listed by path, never assumed stale, never silently
  dropped) and self-heals on the next acquire by the new code. Noted as an
  upgrade-path observation, not a finding against the RC, since the RC tree
  is internally consistent. Orphan `.provenance` sidecars will be left behind.
- **The kanban root install is at `v2.9.19` while the dev tree ships
  `v2.20.0`.** That is host state, not RC state, and nothing in this
  verification depends on it; recorded only because it is what produced the
  lock-file observation above.
- **Pre-existing `.claim-NNNN.lock` residue in `bugs/`** — eleven stale claim
  sentinels (0136, 0137, 0160, 0172, 0173, 0211, 0212 and four `.orphan`
  variants) from earlier runs. They do not block claiming (this run claimed
  0249–0255 cleanly and released every one), and they predate this RC.
- **`--help` rendering for non-canonical flags.** Any flag outside
  `operator_args_render_help_for_flags`'s built-in description table renders
  as a bare `  --flag` with no value placeholder. `release-locks.sh`'s
  `--release` is the instance in this RC; it compensates with appended lines.
  Arguable rather than wrong — recorded as an observation, not a finding.
- **Coverage figures are informational.** Integration-suite coverage reads 7%
  because the integration suite exercises shell entry points out of process,
  where the Python instrumentation does not see them.

## Verification commands run

```
git rev-parse HEAD                                   # 02158ba, matches ai_rc/v2.20.0
git stash list                                       # empty
<pre-flight merge verification loop>                 # "Pre-flight merge verification passed."
bash team/scripts/run-unit-tests.sh --coverage        # exit 0 — 4319 passed, 36 skipped
bash team/scripts/run-integration-tests.sh --coverage # exit 1 — 452 passed, 1 failed, 1 skipped
python3 team/scripts/lint_operator_verb_manifest.py   # exit 0 — 27 verbs, 42 routes, 0 drift
python3 team/scripts/lint_operator_verb_manifest.py --manifest <drifted>  # exit 1 — 3 drifts caught
python3 team/scripts/lint_runnable_remedy.py          # exit 0 — 240 files, 0 offenders
python3 team/scripts/lint_runnable_remedy.py --bash-dirs <synthetic>      # exit 1 — planted placeholder caught
python3 team/scripts/lint_api_parity.py               # exit 0 — 19 pairs
python3 team/scripts/lint_gate_census_compliance.py   # exit 0 — 35 files, 0 offenders
bash team/scripts/halt-after.sh --project pgai-agent-kanban --key coder --until v2.21.0   # exit 1
bash team/scripts/halt-after.sh --until v2.21.0                                           # exit 1
bash team/scripts/halt-after.sh --project pgai-agent-kanban --until 2.9                   # exit 1
bash team/scripts/release-locks.sh {--help,,--bogus,--release no-such-lock}                # 0,0,1,3
bash team/scripts/verify-rc-state.sh pgai-agent-kanban # exit 0 — 0 errors, 14 warnings, 89 ok
<rc_gate.evaluate synthetic probes, two project roots> # DEFERRED/latched/PROMOTED as specified
bash -n <every changed *.sh>; python3 -m py_compile <every changed *.py>   # clean
```
