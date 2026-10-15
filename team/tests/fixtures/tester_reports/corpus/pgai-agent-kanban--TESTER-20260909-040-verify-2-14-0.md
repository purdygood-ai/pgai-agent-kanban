# Verification Report: ai_rc/v2.14.0

**Task:** TESTER-20260909-040-verify-2-14-0
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.14.0-provider-runtime-hardening.md`
**RC branch / commit:** `ai_rc/v2.14.0` @ `4de75519d177124e97279050e4a4a45f5092f5a4`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260909-040-verify-2-14-0` (detached HEAD, clean, no stashes)
**Verified:** 2026-09-09

## Summary

Verified RC v2.14.0 ("provider runtime hardening") against its requirements
document. The RC delivers four things: an operator-owned `claude-env` file
seeded at install and upgrade and sourced after `shell-env` on every entry path;
a background-wait ceiling (`BASH_MAX_TIMEOUT_MS`) derived on the wake path from
the kanban's own per-task budget; a per-firing provider CLI version and model
identity log in both wake entry points; and a safeguard-retry wrapper that stops
an optional prompt-instrumentation block from killing a wake, with that block
reworded to documentation-feedback vocabulary. A fifth deliverable documents the
four-concerns law across `HOW_TO.md`, `docs/OPERATIONS.md`, and
`docs/public-contract.md`.

Both gated runners exited 0 (unit: 3595 passed, 3 skipped; integration: 424
passed, 1 skipped). Every deliverable and all three acceptance criteria were
verified behaviourally, not structurally -- I exercised each guard against the
failure mode it exists to prevent, including a full sandboxed `install.sh` +
`upgrade.sh` cycle, a syntactically broken `claude-env`, a simulated provider
safeguard refusal, a missing provider CLI, and a hanging provider CLI.

One gap was found: `team/README.md` still ships the pre-v2.14.0 "five
configuration files" text naming `env` and `bashrc` as current, which
contradicts the four-concerns law this same RC makes canonical -- and because
`install.sh` and `upgrade.sh` deposit that file over `$KANBAN_ROOT/README.md`
(it is not in `PROTECTED_FILES`), upgrading replaces the operator's currently
correct README with the wrong text. Reproduced in a sandbox, not inferred. Two
quality-class lint findings were also ingested and filed. Recommendation:
SHIP-WITH-CONCERNS.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Test Suites (authoritative -- gated runners)

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3595 passed, 3 skipped in 459.52s |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 424 passed, 1 skipped in 387.39s |

Both runners ran their lint pre-flight and then pytest. `UNIT_EXIT == 0` AND
`INTEGRATION_EXIT == 0` -- the suite-pass precondition is satisfied. Post-run
cleanliness checks (`bare_tmp_cleanliness`, `listener_cleanliness`) both OK.

Captured output: `/tmp/pgai_kanban_tmp/tester/unit_output.txt`,
`/tmp/pgai_kanban_tmp/tester/integration_output.txt`.

## Code Coverage

Informational only; does not affect the recommendation.

- Unit:        line 74%  |  branch 61%   (14255 stmts, 4366 branches; blended `TOTAL` 71%)
- Integration: line 8%   |  branch 6%    (blended `TOTAL` 7%)

Source: `$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/unit.json` and
`.../integration.json`, `totals` object. Field names confirmed against the
generated files before extraction (`percent_statements_covered_display`,
`percent_branches_covered_display`). Lowest-covered modules are unchanged from
prior RCs (`overwatch/resolution_stage.py` 0%, `ops/read.py` 10%,
`pm_status.py` 33%); none is touched by this RC.

## Findings

### Finding 1: `team/README.md` config section contradicts the four-concerns law this RC ships, and an upgrade deposits it over a correct README

**Category:** gap
**Fix Effort:** small (1-2 CODER/WRITER tasks -- a single documentation section)
**Systemic Risk:** low (documentation only; no runtime path, no data, no chain state)
**Regression:** yes
**Integrity Damage:** no
**Filed As:** BUG-0217 (Path C-A -- filed as BUG -- code defect / regression / test failure)

Traces to Deliverable 4 of the requirements doc: "Four-concerns law documented:
kanban.cfg / shell-env / claude-env / secrets."

The law itself is documented well. `HOW_TO.md` section 1.2 is the canonical
statement (ownership sentence per file, a decidability test, and a consequence
clause per misplacement), section 1.6 covers `claude-env` in operator depth,
`docs/OPERATIONS.md` and `docs/public-contract.md` reference it without a second
full copy, and the 1.2-1.6 renumbering to 1.3-1.8 left every internal and
external cross-reference consistent (checked: all anchors in `HOW_TO.md`, both
citations in `docs/OPERATIONS.md`, one in `docs/public-contract.md`).

What was not updated is `team/README.md`. On the RC it still reads:

- line 170 -- "The framework uses five configuration files across two formats.
  `env` and `bashrc` are bash-sourced for wake scripts and personal shell setup
  respectively."
- lines 174-175 -- table rows presenting `env` and `bashrc` as the current bash
  config files. No `shell-env`, no `secrets`, no `claude-env` row exists.
- the template list points at `team/templates/kanban.cfg.example` and
  `team/templates/project.cfg.example`; neither path exists in the tree.

Three documents shipped by this same RC say `env`/`bashrc` are
legacy-compatibility-only and that the four current root files are
`kanban.cfg`, `shell-env`, `claude-env`, `secrets`. The shipped doc set is
internally contradictory.

The operator consequence is the reason this is a `gap` and not an observation.
`install.sh:734` and `team/scripts/upgrade.sh:1084` both deposit `README.md`
(from `team/README.md`) into `$KANBAN_ROOT/README.md`, and `README.md` is not in
`upgrade.sh`'s `PROTECTED_FILES` (verified at `upgrade.sh:253-261`). The live
install's `$PGAI_AGENT_KANBAN_ROOT_PATH/README.md` currently carries the correct
six-file / `claude-env` text. I reproduced the regression in a fully sandboxed
root: after `install.sh` followed by `upgrade.sh --force --no-backup
--wake-tier none`, `grep -n "five configuration files" "$SB/root/README.md"`
matched at line 170. An upgrade to v2.14.0 installs the wrong text over the
right one.

On `Regression: yes` -- stated precisely so CM can weigh it. The stale text in
`team/README.md` predates this RC and the file is untouched by this RC's diff.
What is new in this RC is the contradiction: v2.13.1 shipped no four-concerns
law for the README to disagree with, and v2.13.1's deposit of the same README
did not overwrite correct text with incorrect text in the way v2.14.0's does.
The defect being reported is the inconsistency, and the inconsistency is
produced by this RC, so the honest value is `yes`. It is `Systemic Risk: low`
and `Fix Effort: small`, and the next loop heals it.

### Finding 2: new wake-bootstrap comment cites the release-bundle slug

**Category:** bug
**Fix Effort:** small (1 CODER task -- delete one parenthetical)
**Systemic Risk:** low (a comment; no runtime effect, no operator-visible output)
**Regression:** yes (the line was added by this RC's CODER-034 work; the file's other provenance was clean on `ai_main`)
**Integrity Damage:** no
**Filed As:** BUG-0218 (Path C-A)

`team/scripts/lib/wake_bootstrap.sh:359` contains
`(see v2.14.0-provider-runtime-hardening)` inside the comment block above the
background-wait-ceiling derivation. This is the `comment_provenance` lint's
`bare-release-slug` pattern in production code. Recorded here as a Step-7
finding as well as in the Lint Findings section below, because unlike the
empty-commit finding it is a defect introduced by this RC's own new code rather
than a process artifact around it.

Verified in scope: the same slug appears in the docstrings of this RC's six new
files under `team/tests/`, but `team/scripts/lint_comment_provenance.py`
Exception 7 explicitly exempts `bare-release-slug` under `team/tests/`. Those
are sanctioned, not violations, and BUG-0218 says so explicitly so a CODER does
not "fix" them.

### Finding 3: stray empty commit is the RC branch tip

**Category:** bug
**Fix Effort:** small (guard the two demo README snippets)
**Systemic Risk:** low (the commit changes no file; the shipped tree is byte-identical either way)
**Regression:** no -- the defect predates this RC. The same pattern was filed as BUG-0183 and BUG-0192 against earlier RCs (both now `done`), and nothing in this RC's diff touches commit creation. This is a recurrence of a known class, not something v2.14.0's changes introduced.
**Integrity Damage:** no
**Filed As:** BUG-0219 (Path C-A)

`4de7551` ("initial empty commit") is the tip of `ai_rc/v2.14.0` and changes no
file against its parent. Reflog places it 19 seconds after the WRITER-038 merge.
Author is the normal agent identity (`purdygood`), not the harness identity
(`PGAI Harness <harness@pgai.local>`) -- `harness_init_scratch_repo`'s
already-inside-a-repo refusal
(`team/scripts/harness/lib/throwaway-root.sh:80-86`) held. The message is the
verbatim boilerplate published in `team/demos/*/README.md`, which is the
likeliest copy-path. `WRITER-20260909-038`'s `status.md` independently records
the same observation. Per the lint's own remedy the finding is recorded, not
repaired -- rewriting a branch other agents have merged into is not proposed.

### Finding 4: Deliverable 1 -- claude-env template, sourcing order, and install/upgrade seeding

**Category:** pass

Exercised, not inspected:

- **Sourcing order.** In a sandboxed root under `env -i` (no inherited
  environment -- a genuinely cron-shaped context), `shell-env` set
  `ORDER_PROOF=shell-env` and `claude-env` set `ORDER_PROOF=claude-env`. The
  probe printed `ORDER_PROOF=claude-env`, proving `claude-env` sources second
  and wins.
- **Absent file is a clean no-op.** With `claude-env` removed, the same probe
  exited 0, printed no error or warning, and `ORDER_PROOF` fell back to
  `shell-env`.
- **Syntax error refuses, nothing half-sourced.** A `claude-env` whose first
  line set `EARLY_VAR` and whose second line was a broken `if` produced exit 1
  and a complete dual-channel refusal --
  `error: env_bootstrap.claude_env_syntax_error`, a message naming the file's
  path, and a runnable `bash -n` remedy. `EARLY_VAR` was never set: the
  `bash -n`-before-`source` ordering does what its comment claims. The refusal
  names the path and no value, honouring the template's no-disclosure rule.
- **Fresh install seeds it.** A fully sandboxed `install.sh`
  (`HOME`, `CLAUDE_CONFIG_DIR`, and `PGAI_AGENT_KANBAN_ROOT_PATH` all repointed
  at a temp root per the Live-Install Safety rule) exited 0 and logged
  `Initialized: claude-env (from claude-env_example)`. Both `claude-env` and
  `claude-env_example` were present at 4901 bytes.
- **Upgrade never clobbers.** I appended an operator marker to the seeded
  `claude-env` and ran `upgrade.sh --force --no-backup --dev-tree <RC>`. The
  md5 was byte-identical before and after and the marker survived.
  `claude-env` is in `PROTECTED_FILES` (`upgrade.sh:258`).
- **Upgrade from a pre-`claude-env` install creates it.** With `claude-env` and
  `claude-env_example` deleted, the same upgrade logged
  `Initialized: claude-env (from claude-env_example)` and the created file
  matched the template exactly. This is the gap the new upgrade block was
  written to close, and it closes it.
- **Template header records calibration.** `claude-env_example` states "Claude
  CLI version this file's tuning was calibrated against: 2.1.251", as the
  deliverable requires, and every line ships commented out.

The sandboxed `upgrade.sh` run also confirmed its own temp-target guard fired
("Crontab: skipped -- target is under a temp/test directory"); the live crontab
was checked afterwards and is unmodified, still pointing at the live root.

### Finding 5: Deliverable 1 -- derived background-wait ceiling

**Category:** pass

The derivation block (`team/scripts/lib/wake_bootstrap.sh`, between the
`BG-WAIT-CEILING-DERIVATION-BEGIN/END` sentinels) was extracted and driven under
`env -i` across all four states:

| Inputs | Result | Log line |
|---|---|---|
| `MAX_TASK_SECONDS=5400`, `KILL_GRACE_SECONDS=30` | `BASH_MAX_TIMEOUT_MS=5430000` | derived, naming both inputs |
| `MAX_TASK_SECONDS=10800`, `KILL_GRACE_SECONDS=60` | `BASH_MAX_TIMEOUT_MS=10860000` | derived -- tracks a raised budget with no second edit |
| `MAX_TASK_SECONDS=0` | unset | "per-task watchdog disabled; BASH_MAX_TIMEOUT_MS left unset" |
| `BASH_MAX_TIMEOUT_MS=777000` pre-set | `777000` preserved | "operator override in force" |

The derived default (5430000 ms) is 9x the CLI's own 600000 ms ceiling the
requirements doc names as the hazard, so the truncation this exists to prevent
is prevented. The operator-wins ordering was independently confirmed end-to-end
in the sourcing test above (`claude-env` setting `BASH_MAX_TIMEOUT_MS=777000`
reached the probe's environment).

The RC's own integration lane covers the same claim at the assembled-pipeline
level (`test_cron_wake_exports_derived_ceiling_to_the_agent_process` and
`test_cron_wake_honours_claude_env_ceiling_override_in_the_agent_process`,
both passing), driving a real `bash scripts/wake/claude.sh` subprocess under a
cron-shaped environment. That is the "cron-context fixture" the acceptance
section asks for.

### Finding 6: Deliverable 2 -- per-firing provider CLI version and model log

**Category:** pass

The `PROVIDER-IDENTITY-LOG` block was extracted from
`team/scripts/wake/claude.sh` and driven with stub binaries:

- Working CLI -> `provider CLI version: 2.1.251 (Claude Code)`.
- `PGAI_CODER_MODEL=claude-opus-5` -> `model: claude-opus-5 (configured default
  for coder; a task-level Model Override may replace this per task)`.
- No model configured -> `model: <provider CLI default> (no PGAI_CODER_MODEL
  configured; ...)`.
- CLI absent from PATH -> the explicit unknown marker, exit 0. Never a gate.
- CLI that hangs -> bounded at exactly 10s by `timeout 10`, then the same
  unknown marker, exit 0.

Placement checked: the block sits at `claude.sh:294-329`, after the HALT check
(which exits 0 at lines 272-274) and before `wake_common_preflight` at line 332,
so it fires on every firing that begins work, including one that finds no work.
`team/scripts/wake/codex.sh` carries a byte-identical block with the same field
names, so one grep correlates both providers, as the deliverable requires.

I checked one thing the block could plausibly have broken: it contains a
`set +e` / `set -e` pair and `claude.sh` has no `set -euo pipefail` of its own.
Tracing the sourcing chain, `wake_bootstrap.sh:283` runs `set -euo pipefail`
inside `wake_bootstrap_init`, which `wake_bootstrap.sh:506` invokes at file
top-level, which `wake_common.sh:84` sources, which `claude.sh:223` sources. So
errexit is already active when line 305 runs and the block restores the
pre-existing state rather than newly enabling it. Not a defect.

The RC's integration lane covers the "with work and without work" halves
(`test_identity_header_present_for_firing_with_and_without_work`, passing).

### Finding 7: Deliverable 3 -- telemetry-block safeguard retry and rewording

**Category:** pass

Both halves exercised.

*Classifier* -- `_wd_is_safeguard_refusal` was extracted and driven with twelve
inputs. All twelve behaved correctly: safety-system, content-policy,
prompt-injection-guard and moderation wording classify as refusals; timeout,
connection-refused, 401/auth, 429/rate-limit and "overloaded" do not; empty
input does not; unrecognised failure text does not (an unrecognised failure is
never assumed to be the block's fault); and a string containing BOTH a safeguard
signature and a timeout signature classifies as NOT a refusal, confirming the
documented exclusion-first ordering.

*Retry wrapper* -- `_wd_run_provider_with_telemetry_safeguard_retry` was driven
with a stubbed provider across four scenarios:

| Scenario | Attempts | Block present on retry | Final exit | Narration |
|---|---|---|---|---|
| Safeguard refusal then success | 2 | no | 0 | verbatim provider output + `skipping trace injection: provider safeguard` |
| Timeout on first attempt | 1 | -- | 1 (real failure preserved) | none |
| First attempt succeeds | 1 | -- | 0 | none |
| Safeguard refusal on both attempts | 2 | no | 1 (real failure returned) | narrated exactly once; never loops |

This is the deliverable's core claim -- "optional telemetry blocks NEVER kill a
wake" -- demonstrated by triggering the failure mode, and the exclusion cases
demonstrate the fix does not mask real provider failures. The narration phrase
is the literal string the requirements doc specifies. The refusal site carries
`# CENSUS-ANCHOR: wake-driver-invoke-01`, an inline `CENSUS CLASS: CONVERT`
comment, a full dual-channel error contract, and a matching Locator Index entry
in `docs/blocking-site-census.md` (rollup updated to 56 sites / 26 CONVERT).
`python3 team/scripts/lint_gate_census_compliance.py` exits 0 with 0 offenders.

*Rewording* -- the injected block was extracted (41 lines) and grepped for the
forbidden vocabulary. Zero hits for `trace`, `introspect`, `confidence`, or
`confidence calibration`. Section 5 is now "Documentation sufficiency", section
7 "Documentation feedback", and the artifact is `task-notes.md`. The
counterpart copy gate in `wake_driver_finalize.sh` was renamed in lockstep
(`notes_source="${task_artifact_dir}/task-notes.md"`), and a tree-wide grep
finds no residual `reasoning-trace.md` reference outside `team/tests/`, so the
injection and copy halves cannot silently disagree.

### Finding 8: acceptance criteria

**Category:** pass

All three named criteria verified. Criterion 1 (cron-context fixture shows the
derived ceiling in the agent env) -- Finding 5. Criterion 2 (simulated safeguard
refusal -> wake completes with narrated skip) -- Finding 7. Criterion 3
(CLI/model version present in every wake log header block) -- Finding 6.

### Finding 9: test fidelity of the new suites

**Category:** pass

Checked against the Test Fidelity checklist because the RC's claims are
shell-environment claims, the class most often faked by a sanitized proxy.
`team/tests/integration/test_provider_runtime_integration.py` drives a real
`bash scripts/wake/claude.sh --agent=writer` subprocess against a real throwaway
kanban root under `env -i`, with the `claude` binary replaced by a
PATH-shadowing stub (`team/tests/fixtures/claude_provider_stub.py`) rather than
mocking the wake internals. Real strict-mode bash is exercised (item 1); an
installed-tree copy is built rather than running from the dev tree (item 2);
every root is a throwaway under the framework temp root (item 6). The golden
fixture `team/tests/fixtures/engine-baseline/wake-log.txt` was refreshed with
the new ceiling line rather than left to drift -- the BUG-0213 failure mode did
not recur.

Two production defects the integration task found and fixed in the same session
are documented at their fix sites and were independently confirmed here: the
`claude-env` wake-path dead-code path (`env_bootstrap.sh`'s idempotency guard
short-circuits before its own `claude-env` block, so `wake_bootstrap.sh`
re-sources directly -- this is why the second census anchor
`wake-bootstrap-claude-env-01` exists), and `provider_dispatch`'s
process-global `set -e` reactivation that would have prevented the safeguard
retry from ever firing.

### Finding 10: structural, constraint and standards checks

**Category:** pass-with-caveat

- Pre-flight merge verification passed: every DONE task's feature branch is
  already deleted post-merge (normal lifecycle), so no unmerged-branch or BUG-92
  zero-commit anomaly exists.
- Worktree HEAD matches `ai_rc/v2.14.0` exactly; `git status --short` clean;
  `git stash list` empty.
- `bash -n` clean on all eight changed shell files and on `claude-env_example`.
- Strict mode preserved where it was present (`env_bootstrap.sh`,
  `wake_bootstrap.sh`, `install.sh`, `upgrade.sh`); see Finding 6 for why the
  wake entry points legitimately have none of their own.
- No hardcoded secrets in the new code. `claude-env_example` carries an explicit
  no-disclosure rule and both refusal sites honour it (path named, value never).
- `.gitignore` correctly adds `claude-env` so an operator's tuning is never
  committed.
- Skip-cites-real-bug gate (Step 6.13): no placeholder `BUG-SKIP-*` IDs, and
  every cited `BUG-NNNN` in `team/tests/` resolves to a real file in `bugs/`.
  The RC's five new test files contain no skips at all.
- Synthetic release simulation (Step 6.8): the RC touches no open/close
  lifecycle script, but `team/scripts/upgrade.sh` is under `team/scripts/`, so
  the lane was run anyway -- `tests/integration/test_cm_release_synthetic.py`,
  15 passed.
- Prereq graph (Step 6.10): acyclic, every referenced task ID exists, sequence
  032-042 non-colliding, bookends correct (CM-039 open-RC at the head,
  TESTER-040 then CM-042 release at the tail, currently `[W]`).
- Step 6.1 drift check: `pp_last_released_version` returns `v2.13.1`;
  `semver_gt v2.14.0 v2.13.1` is true; `release-state.md` `## Active RC` is
  `v2.14.0` with the `ai_rc/v2.14.0` branch actually present; `## Last Released`
  is a well-formed `v2.13.1`. No drift. No HALT flag at either scope.

The **caveat** is a coding-standards spot-check hit (Step 6.14) that I judged
borderline rather than a defect, so it is recorded here and not filed: the
~25-line provider-identity block is duplicated verbatim in
`team/scripts/wake/claude.sh` and `team/scripts/wake/codex.sh` rather than
factored into `team/scripts/lib/`, which is the shape directive 3 ("put shared
code in a `lib/` package and import it; never copy") exists to discourage. Both
copies carry a comment stating the duplication is deliberate so one grep pattern
matches both providers, and the two wake entry points are already parallel files
by design. It is a real duplication with a stated rationale -- an arguable call
rather than a violation -- so per Step 6.11 it is an observation, not a finding.

## Lint Findings (non-blocking)

Ingested from `$PGAI_AGENT_KANBAN_TEMP_DIR/lint/findings-unit.json` (1 finding)
and `.../findings-integration.json` (2 findings). Grouped by lint class: two
distinct classes, one bug filed per class.

- `comment_provenance` -- `team/scripts/lib/wake_bootstrap.sh:359`:
  `[bare-release-slug]` provenance reference
  `v2.14.0-provider-runtime-hardening` in production code -- filed BUG-0218
  (Path C-A). No open or running bug covered this class at filing time (the
  nearest, BUG-0186 / BUG-0202 / BUG-0212 / BUG-0214, are all `done`).
- `rc_empty_commits` -- empty non-merge commit `4de7551` ("initial empty
  commit") in `ai_main..ai_rc/v2.14.0`, currently the branch tip -- filed
  BUG-0219 (Path C-A). No open or running bug covered this class at filing time
  (the nearest, BUG-0183 and BUG-0192, are both `done`).

Both classes are also carried as Step-7 findings above (Findings 2 and 3),
because each is a concrete defect in the RC's own artifacts rather than a bare
lint signal. Neither raises `## Systemic Risk`, and neither is what elevates the
recommendation -- Finding 1 does that.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=2d freshness=stale — the docker and podman claude-abliterated pairs failed on a malformed FROM and podman/rocky9 failed at its COPY --chmod step; recent in days but recorded against v2.9.20 rather than the v2.14.0 under verification, so nothing is known about whether this tree's images build; cites BUG-0167, filed by that deep-verify run.

## Autonomous criterion

Manual interventions required: none.

1. Were any tasks force-promoted from WAITING to BACKLOG manually? **NO.** All
   nine upstream tasks (PM-004, CM-039, CODER-032 through 037, WRITER-038) are
   `DONE` with `Needs Human: no`, and this task's own `status.md` records the
   wake script's automatic WAITING -> BACKLOG promotion on prerequisite
   satisfaction.
2. Was `release-state.md` edited outside the CM scripts? **NO.** Its four fields
   are internally consistent and match `CM-20260909-039-open-rc-2-14-0`'s
   recorded open at 2026-09-09T11:03:26+00:00, which is also the
   branch-creation reflog entry for `ai_rc/v2.14.0`.
3. Were any scripts patched mid-build? **NO.** Every commit in
   `ai_main..ai_rc/v2.14.0` arrived through a `--no-ff` merge of its own task's
   feature branch, in prerequisite order, between 11:13 and 13:45. The RC does
   change files under `team/scripts/` -- that is the release's declared
   deliverable, not out-of-band patching. The one commit not accounted for by a
   task is the empty `4de7551` (Finding 3), which changes no file.
4. Were any queue files manually edited? **NO.** Queue mtimes track the wake
   firings that wrote them, and `cm_backlog.md` shows the expected progression
   with `CM-20260909-042-release-2-14-0` sitting at `[W]` awaiting this report.

## Caveats

- **`reasoning_trace` survives as the config key and script name.** The
  requirements doc scoped the rewording to the injected *block*, and the block
  is clean. The surrounding machinery still uses the old vocabulary:
  `[training] reasoning_trace` in `project.cfg`, the `PGAI_REASONING_TRACE`
  env shim, `pp_reasoning_trace`, `team/scripts/collect-reasoning-traces.sh`,
  and the dashboard pane text. That is consistent with the requirement as
  written and preserves backward compatibility, but the operator-facing
  vocabulary is now split between two names for one mechanism. Worth a future
  ticket; not a gap against this spec.
- **Governance-doc tension on test docstrings.** `team/roles/TESTER.md` Step 9
  states that test docstrings must never cite the RC or framework version that
  motivated them, while `team/scripts/lint_comment_provenance.py` Exception 7
  deliberately exempts `bare-release-slug` under `team/tests/` for a stated
  reason (a `vX.Y.Z-slug` is indistinguishable from fixture data in test prose).
  This RC's six new test files cite the slug and are therefore lint-clean but
  read against the role file's blanket wording. I treated the shipped lint as
  authoritative and filed nothing. Flagging it because the two will keep
  disagreeing until one is amended.
- **`docs/blocking-site-census.md` also names the release slug**, in prose. The
  provenance lint's code scan does not cover `docs/`, so this is not a lint
  finding and is not filed. Noted only so a future census reader knows it was
  seen and judged in-scope-for-prose.
- **A HALTed firing logs no identity header.** The identity block sits after the
  HALT check, so a firing that exits on a HALT flag produces no
  `provider CLI version:` line. This looks correct -- a halted firing invokes no
  provider -- but "every wake log" in the acceptance text reads slightly
  stronger than what ships. Recorded as an observation, not a finding.
- **Live-install safety.** Every state-mutating invocation I made (`install.sh`,
  `upgrade.sh` twice) ran with `PGAI_AGENT_KANBAN_ROOT_PATH`, `HOME`, and
  `CLAUDE_CONFIG_DIR` repointed at a fresh `mktemp -d -p
  "$PGAI_AGENT_KANBAN_TEMP_DIR"` root. The live crontab and the live kanban root
  were re-checked afterwards and are unmodified. All scratch output is under
  `/tmp/pgai_kanban_tmp/...`, never bare `/tmp`.
- **No prior-run artifacts.** `artifacts/` was empty at start and no
  `.previous-RUN-N` files exist. This is a fresh first run; nothing was
  inherited or re-issued from an earlier verification.
