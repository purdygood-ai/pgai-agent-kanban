# Verification Report: ai_rc/v2.18.1

**Task:** TESTER-20260911-037-verify-2-18-1
**Project:** pgai-agent-kanban
**RC branch:** `ai_rc/v2.18.1` @ `f908d06c35a87273bf033318f9a48c92a46c40ec`
**Baseline:** `ai_main`
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.18.1-bugfix-bundle-20260911.md`
**Bundled item:** `BUG-0243-deep-verify-alias-vocabulary-split.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260911-037-verify-2-18-1` (detached HEAD, clean, no stashes)
**Verified:** 2026-09-11

## Summary

v2.18.1 is a single-bug patch RC. It closes BUG-0243, in which two shipped readers of the
same `project.cfg [release] deep_verify` key disagreed about what the key accepts: the
kanban-wide `config_loader.sh` accepted `weekly|off|true|false`, while
`pp_deep_verify_mode()` -- the reader the scheduled deep-verify dispatch actually consults --
accepted only `weekly|off` and failed loud on the boolean spellings that every shipped
configuration example, and every `project.cfg` written by `create-project.sh` since v2.18.0,
advertises as accepted. An operator who followed the comment scaffolded into their own
project file ended up with the weekly cold-build lane silently switched off.

The RC takes the widening direction the bug's Expected section named: `pp_deep_verify_mode`
now recognizes `true` as an alias of `weekly` and `false` as an alias of `off`, while still
printing only the two canonical mode words on stdout and still failing loud, with nothing on
stdout, for any value outside the four. Three tickets landed: CODER-033 (the reader widening
plus its dual-channel remedy and `--help` text, with unit and integration coverage),
CODER-034 (a drift-proof integration test that derives the accepted-value list from the
generated `project.cfg` comment itself rather than from a hardcoded list), and WRITER-035
(one accepted-value set stated identically across `docs/operator-commands.md`,
`docs/cm-release-reference.md`, and `team/SOP-core.md`).

Verification covered pre-flight merge checks, both gated test suites, the quality-class lint
findings files, release-state drift, structural and content review of all eight changed
files, executable acceptance criteria, behavioral guard verification (both the original
failure mode and the new normal case), the census lint, the skip-cites-real-bug gate, the
synthetic release simulation, whole-tree syntax checks, and the autonomous-operation
criterion. Everything the requirements doc and BUG-0243 ask for is met, and the fix was
exercised end to end rather than read.

## Recommendation

PASS

## Systemic Risk

low

## Test Suites (authoritative verdict)

Both gated runners were run from the worktree root with `--coverage`.

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3833 passed, 3 skipped in 509.82s |
| `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 448 passed, 1 skipped in 403.35s |

`UNIT_EXIT == 0` AND `INTEGRATION_EXIT == 0` -- the suite-pass precondition for a PASS
recommendation is satisfied by the gated runners themselves, not by a direct pytest
invocation. Every lint pre-flight inside both runners reported OK
(`test_anti_patterns`, `skip_cites_real_bug`, `orphan_tests`, `api_parity`,
`upgrade_no_temp_copy_guard`, `icd_freshness`, `icd_compat`, `changelog_freshness`,
`engine_size_dupe`, `env_bootstrap`, `help_presence`, `dockerfile_static`,
`bare_tmp_cleanliness`, `listener_cleanliness`) except the non-fatal `hygiene` lint, whose
single quality-class finding is dispositioned in the Lint Findings section below.

Captured output: `/tmp/pgai_kanban_tmp/tester/unit_output.txt`,
`/tmp/pgai_kanban_tmp/tester/integration_output.txt`.

## Code Coverage

Read from the `totals` object of the `pytest-cov` JSON reports (field names verified against
the generated files, not assumed).

- Unit:        line 75%  |  branch 62%   (14928 stmts, 4496 branches; 11202 covered lines, 2768 covered branches)
- Integration: line 8%   |  branch 6%    (14919 stmts, 4496 branches)

Informational only. Coverage does not enter the PASS precondition and is not a finding.

## Findings

### Finding 1: `pp_deep_verify_mode` accepts every spelling the shipped comments advertise

**Category:** pass

BUG-0243 acceptance item 1. Built a throwaway kanban root
(`/tmp/pgai_kanban_tmp/probe.cX8F/kanban_root`, never the live install), created a project
with the shipped `team/scripts/create-project.sh`, and drove each value through the reader
the generated comment names. The generated `[release]` block reads:

```
# Accepted: weekly|off (true|false also accepted as aliases: true=weekly,
# false=off). Default: weekly
```

Observed, one row per value (`source scripts/lib/project_paths.sh && pp_deep_verify_mode`):

| `deep_verify =` | stdout | exit | stderr |
|---|---|---|---|
| `weekly` | `weekly` | 0 | (none) |
| `off` | `off` | 0 | (none) |
| `true` | `weekly` | 0 | (none) |
| `false` | `off` | 0 | (none) |
| (absent / commented out) | `weekly` | 0 | (none) |

Every value the comment advertises resolves, and the alias spelling is never echoed back --
stdout carries exactly one of the two canonical mode words, which is the property the
function's own header defends.

### Finding 2: a value outside the accepted set still fails loud with nothing on stdout

**Category:** pass

BUG-0243 acceptance item 1 (negative half) and the guard the widening must not weaken.
Same throwaway root, four out-of-set values:

| `deep_verify =` | stdout | exit | stderr |
|---|---|---|---|
| `WEEKLY` | *(empty)* | 1 | `unrecognized value 'WEEKLY' ... (expected 'weekly' or 'off', aliases 'true'/'false' also accepted; blank/absent defaults to 'weekly')` |
| `True` | *(empty)* | 1 | same shape, naming `True` |
| `weeklyish` | *(empty)* | 1 | same shape, naming `weeklyish` |
| `yes` | *(empty)* | 1 | same shape, naming `yes` |

The widening did not degrade into case-insensitive or prefix matching: `WEEKLY`, `True`, and
`weeklyish` are all still refused. The stderr diagnostic names the accepted set it now
enforces, including the aliases. A caller can still tell "resolved to a mode" from "refused"
without parsing prose.

### Finding 3: `deep-verify.sh --scheduled` no longer refuses an advertised spelling

**Category:** pass

BUG-0243 acceptance item 2, verified behaviorally against the same throwaway root at
11:58 UTC (outside the default `03:00-03:59` off-hours window, so no container engine was
started and no lane ran -- consistent with TESTER.md Step 5d).

| `deep_verify =` | exit | stdout | `deep_verify.invalid_schedule_mode`? |
|---|---|---|---|
| `weekly` | 0 | `scheduled run not due (outside off-hours window 03:00-03:59 UTC; checked at 11:58 UTC)` | no |
| `true` | 0 | identical not-due narration -- reaches the due-evaluation path exactly like `weekly` | no |
| `off` | 0 | `scheduled run skipped (deep_verify=off)` | no |
| `false` | 0 | identical skip narration -- skips exactly like `off` | no |
| `bogus` | 1 | *(empty)* | **yes**, dual-channel with `error:` / `message:` / `remedy:` all populated |

The `bogus` row is the guard-still-fires half of Step 6.5: the refusal that BUG-0243 said was
firing on legitimate values still fires on a genuine typo, and its remedy now names the full
accepted set (`Set project.cfg [release] deep_verify to 'weekly' or 'off' (aliases
'true'/'false' also accepted) ...`).

### Finding 4: the original failure mode reproduces against the pre-fix reader

**Category:** pass

Step 6.5 requires synthesizing the original failure mode as well as the new normal case. An
isolated copy of `team/scripts/lib/project_paths.sh` (with its whole sibling `lib/` tree, so
`read_ini` and friends resolve) was patched back to the pre-fix `case "$raw" in weekly|off)`
arm under the temp root -- the RC worktree itself was never modified. Against that pre-fix
reader, with the same `project.cfg` the shipped `create-project.sh` wrote:

- `deep_verify = true` -> exit 1, empty stdout, `unrecognized value 'true' ... `
- `deep_verify = false` -> exit 1, empty stdout, `unrecognized value 'false' ... `
- `deep_verify = weekly` -> `weekly`, exit 0
- `deep_verify = off` -> `off`, exit 0

That is BUG-0243's reported symptom reproduced exactly, and it confirms the new tests would
fail against the pre-fix code. The old normal case (`weekly` / `off`) works identically
before and after, so the fix did not trade one case for the other.

### Finding 5: the drift-proof test derives its value list from the comment, not a hardcoded copy

**Category:** pass

CODER-034's deliverable. Exercised the three new module-level helpers in
`team/tests/integration/test_create_project.py` directly against the real generated
`project.cfg`:

- `_extract_release_section` / `_extract_deep_verify_comment` returned the joined comment,
  which spans two physical lines in the generated file and reads as one string.
- `_parse_accepted_deep_verify_values` returned `['weekly', 'off', 'true', 'false']` -- all
  four spellings, parsed from the comment text, with no list hardcoded in the test.
- Negative probe: with the alias clause removed from the comment, the parser refuses with
  `Could not find the '<word>|<word> also accepted as aliases: X=Y, Z=W' clause in the
  deep_verify comment; if the comment's shape changed, update this parser to match.` It
  fails loud rather than silently narrowing the value list it checks.

Either half of the pair (comment or reader) being widened or narrowed without the other now
fails the suite. See the caveat in Finding 11 about the parser's coupling to
`create-project.sh`'s exact sentence shape.

### Finding 6: the two readers of `[release] deep_verify` agree on the accepted set

**Category:** pass

BUG-0243 acceptance item 3. Both readers now carry byte-equivalent `case` arms:

- `team/scripts/lib/config_loader.sh:532` -- `weekly|true)` -> `PGAI_DEEP_VERIFY="true"`;
  `off|false)` -> `"false"`; `*)` -> `config_loader.release.invalid_deep_verify`, return 1.
- `team/scripts/lib/project_paths.sh:1838` -- `weekly|true)` -> prints `weekly`;
  `off|false)` -> prints `off`; `*)` -> stderr diagnostic, return 1.

A value accepted by one is accepted by the other, and a value refused by one is refused by
the other, including the case-sensitivity boundary (`True` and `WEEKLY` are refused by both).

### Finding 7: one accepted-value set named on every published surface

**Category:** pass

BUG-0243 acceptance item 4 and WRITER-035's deliverable. Independently grepped every surface
that states the set:

| Surface | Statement |
|---|---|
| `team/scripts/create-project.sh:497` (scaffolded comment) | `Accepted: weekly\|off (true\|false also accepted as aliases: true=weekly, false=off). Default: weekly` |
| `project.cfg_example:222` | `Accepted: weekly\|off (or true\|false). Kanban-wide default: weekly` |
| `kanban.cfg_example:532` | `Accepted: weekly\|off (or true\|false). Default: weekly` |
| `team/scripts/lib/config_loader.sh:543` (remedy) | `Set [release] deep_verify to one of weekly\|off (or true\|false) in <cfg>.` |
| `team/SOP-core.md:268` (`[release]` key registry) | `` `weekly`\|`off` (or `true`\|`false`) `` -- "Not a boolean but a mode ... Every reader of the key accepts all four." |
| `docs/operator-commands.md:661` (per-project switch table) | `` `weekly`\|`off` (or `true`\|`false`) `` plus "The boolean spellings are aliases of the word spellings: `true`=`weekly`, `false`=`off`." |
| `docs/cm-release-reference.md:172` (per-project switch prose) | "`deep_verify` takes `weekly` (default) or `off`, with `true` and `false` accepted as aliases of those two words" |
| `team/scripts/deep-verify.sh` remedy + `--help` | `(weekly\|off, true\|false also accepted as aliases)` |

All eight name the identical set `{weekly, off, true, false}` with `true`=`weekly`,
`false`=`off`. The never-guess doctrine is preserved and sharpened in the two operator docs
("An alias is a recognized spelling, not a guess"), which is what WRITER-035's brief asked
for. BUG-0243's Files Involved named `team/SOP.md`; that file no longer exists in the dev
tree (renamed to `team/SOP-core.md` at v2.10.0, `git log --diff-filter=DR` -> `33ba4dc
Release v2.10.0`), and `team/SOP-core.md` is the surface that was correctly updated in its
place.

### Finding 8: requirements-doc acceptance criteria

**Category:** pass

| Criterion | Result |
|---|---|
| Each bundled item is addressed in the implementation | BUG-0243's five acceptance items are each met -- see Findings 1-7 and 9. |
| All Python files compile cleanly | 403 tracked `.py` files compiled via `py_compile`; 0 failures. |
| All bash files pass syntax check | Every tracked `.sh` file passed `bash -n`; 0 failures. |

### Finding 9: test coverage for the new behavior

**Category:** pass

BUG-0243 acceptance item 5 plus CODER-033's own criteria. New cases landed and are green
inside the gated runners:

- `team/tests/unit/test_project_paths_bash.py` -- parametrized
  `test_deep_verify_mode_accepts_every_documented_spelling` covering all four spellings and
  asserting the canonical word on stdout with empty stderr, plus
  `test_deep_verify_mode_still_refuses_a_value_outside_the_widened_set` pinning `weeklyish`
  as refused (explicitly guarding against prefix/case-blind matching).
- `team/tests/integration/test_deep_verify_scheduled_dispatch.py` --
  `test_true_alias_reaches_due_evaluation_like_weekly` and
  `test_false_alias_skips_the_lane_like_off`, both asserting the absence of
  `deep_verify.invalid_schedule_mode`. The pre-existing unrecognized-value refusal test is
  unchanged and still passes.
- `team/tests/integration/test_create_project.py` -- the drift-proof test of Finding 5. The
  pre-existing `TestCreateProjectDeepVerifyReleaseSection` cases are unmodified in the diff.

Test-fidelity checklist: the new integration cases exercise the real shipped bash entry
points (`create-project.sh`, `deep-verify.sh`) under a sandboxed `KANBAN_ROOT`, not a Python
re-implementation, so an `errexit` chain-killer or a path-resolution defect would surface.
No state-mutating script is pointed at the live install, by the RC's tests or by this
verification's own ad-hoc commands.

### Finding 10: structural, census, gate, and lifecycle checks

**Category:** pass

- **Pre-flight merge verification** -- every DONE task's feature branch is already deleted
  post-merge (the normal Procedure A lifecycle); no unmerged branch and no BUG-92
  zero-commits-ahead anomaly. Each of the three tickets contributed exactly one non-merge
  commit (4, 1, and 3 files respectively); no empty commits on the RC.
- **Ticket scope containment** -- `CODER-033` touched only `deep-verify.sh`,
  `project_paths.sh`, and its two test files; `CODER-034` touched only
  `test_create_project.py`; `WRITER-035` touched only `docs/cm-release-reference.md`,
  `docs/operator-commands.md`, `team/SOP-core.md` -- satisfying WRITER-035's "no file outside
  the three documents" criterion exactly.
- **Census** -- `docs/blocking-site-census.md` Locator Index still carries
  `team/scripts/deep-verify.sh:deep-verify-02 BLOCK-WORTHY` with its class unchanged, and the
  anchor still resolves: `python3 team/scripts/lint_gate_census_compliance.py` -> `OK -- 34
  file(s) checked, 0 offenders`, `FINDINGS_JSON: []`, exit 0.
- **Skip-cites-real-bug (Step 6.13)** -- `bash team/scripts/lint_skip_bug_gate.sh` -> `OK --
  all skip annotations are compliant`, `0 missing files`. No placeholder `BUG-SKIP` IDs in
  live test sources (the only `BUG-NNNN`-shaped grep hits are inside archived runner logs
  under `team/tests/artifacts/`, not skip annotations).
- **Synthetic release simulation (Step 6.8)** -- triggered, because the diff touches
  `team/scripts/lib/project_paths.sh` and `team/scripts/deep-verify.sh`.
  `python3 -m pytest tests/integration/test_cm_release_synthetic.py` -> 15 passed, run under a
  throwaway `PGAI_AGENT_KANBAN_ROOT_PATH`.
- **Constraint negative checks (Step 6.9)** -- `set -euo pipefail` intact at
  `deep-verify.sh:112`; `project_paths.sh` is a sourced library and correctly carries none.
  The RC's diff adds and removes no `set -` line. No secrets, no banned patterns introduced.
- **Release-state drift (Step 6.1)** -- `pp_last_released_version` -> `v2.18.0`;
  `semver_gt v2.18.1 v2.18.0` is true. `release-state.md` holds `Active RC: v2.18.1`,
  `RC Opened By Task: CM-20260911-036-open-rc-2-18-1`, and a well-formed
  `Last Released: v2.18.0`. The `ai_rc/v2.18.1` branch exists and matches the worktree HEAD.
  No drift.
- **Prereq graph (Step 6.10)** -- this task's three prerequisites (CODER-033, CODER-034,
  WRITER-035) all exist and are DONE; the lifecycle bookend `CM-20260911-036-open-rc-2-18-1`
  exists and is DONE; sequence numbers 032-037 are consistent and non-colliding; no cycles.
- **Coding-standards spot-check (Step 6.14)** -- no new script (so no new `--help` surface is
  owed), no new hardcoded operational value, no re-implementation of an existing `lib/`
  operation. Directive 8 (provenance) is clean for this RC's diff: the tree-wide hygiene lint
  found exactly one provenance instance and it is in a pre-existing release-notes file, not
  in anything this RC touched.

### Finding 11: accepted-value wording differs in shape between surfaces

**Category:** pass-with-caveat

The accepted-value *set* is identical on all eight surfaces (Finding 7), which is what
BUG-0243 acceptance item 4 and CODER-034's criterion require. The *sentence shape* is not:
`create-project.sh` uses the long form that spells the alias mapping out
(`(true|false also accepted as aliases: true=weekly, false=off)`), while
`project.cfg_example`, `kanban.cfg_example`, and `config_loader.sh`'s remedy use the terser
`(or true|false)`. CODER-034 recorded this deliberately in its completion note and left the
already-correct surfaces unedited.

The caveat is the coupling this creates: `_parse_accepted_deep_verify_values` matches the
long form's `also accepted as aliases: X=Y, Z=W` clause specifically, so a future editor who
reworded `create-project.sh`'s comment into the terser shape used by its three siblings would
break the test. That is the safe failure direction -- the parser asserts with an explicit
"if the comment's shape changed, update this parser to match" message rather than silently
checking a narrower set (verified in Finding 5) -- so this is a note for whoever next edits
that comment, not a defect. No filing.

## Lint Findings (non-blocking)

- `hygiene` (class `provenance`) -- `release-notes/v2.16.0.md:42`: `[task-ID]` provenance
  reference `TESTER-20260910-070` in shipped operator-facing text; comments and output must
  describe behavior, not internal IDs -- **cites BUG-0242-release-notes-cite-task-id (open)**.
  The same single instance appears in both `findings-unit.json` and
  `findings-integration.json`; one lint class, one bug, already tracked. No new filing --
  the cite-an-open-bug half of the disposition rule applies.

Neither findings file was absent. This finding is quality-class, carries zero runtime-failure
risk, and does not move `## Recommendation` or `## Systemic Risk`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=4d freshness=stale — the last lane run was red on the claude-abliterated flavor (a `FROM` line taking two arguments, under both docker and podman) and on podman/rocky9 (`COPY --chmod=+x`), and it is stale because v2.9.20 is nine minors behind the v2.18.1 under verification, so nothing has been built cold against this tree; the two Dockerfile defects that record names are no longer present on it (`docker/claude-abliterated/Dockerfile:3` now reads `FROM t2fn/claude-abliterated:latest-amd64`, `docker/rocky9/Dockerfile:85` now reads `COPY --chmod=755`), BUG-0167-cold-build-claude-abliterated-failure covered that failure and is closed, and no open bug describes it — so nothing is cited and, per the never-re-file rule, nothing is filed.

The tree ships three Dockerfiles (`docker/claude-abliterated`, `docker/debian`,
`docker/rocky9`) and `team/scripts/deep-verify.sh` is present, so the lane exists and the
absence-of-runner gap finding does not apply. No container build was run during this
verification and no container engine was started, per TESTER.md Step 5d. Staleness is context
the rating was made with; on its own it does not elevate the recommendation or systemic risk.

## Autonomous criterion

Manual interventions required: **none**.

1. Were any tasks force-promoted from WAITING to BACKLOG manually? **NO** -- every task in the
   v2.18.1 chain (CM-036, PM-005, CODER-033, CODER-034, WRITER-035) reached DONE with
   `Needs Human: no`; this task's own status carried the wake script's automatic
   "Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied" note.
2. Was `release-state.md` edited outside the CM scripts? **NO** -- its four fields are exactly
   what `cm-open-rc.sh` writes, and `RC Opened By Task` names the real CM task.
3. Were any scripts patched mid-build? **NO** -- the only commit touching `team/scripts/` is
   `bc0ed54`, CODER-033's own in-scope change. All six RC commits are agent-authored, in
   ticket order, between 10:53 and 11:36 UTC on 2026-09-11, with no out-of-band edits.
4. Were any queue files manually edited? **NO** -- `tester_backlog.md` shows the normal
   marker progression with this task at `[A]`; no hand-flipped markers observed.

## Caveats

- **The live kanban root install is at v2.9.19 (2026-09-05), many releases behind the dev
  tree.** `$HOME/pgai_agent_kanban/VERSION` reads `v2.9.19`, and that root still carries
  the pre-v2.10.0 monolithic `SOP.md` (whose "The per-project switch" table lists
  `deep_verify` values as `weekly`, `off` with no aliases) and no `SOP-core.md`. This is
  install state, not tree state: the dev tree has shipped `team/SOP-core.md` since v2.10.0,
  `install.sh` / `upgrade.sh` install that file and never write `SOP.md`, and nothing in this
  RC's diff could change the installed root. Recorded as an observation for the operator --
  upgrading the install will replace that stale table -- not as a finding against v2.18.1.
- The `bogus` / `weeklyish` / `WEEKLY` / `True` probes above were all run against a throwaway
  kanban root under `$PGAI_AGENT_KANBAN_TEMP_DIR`, never the live install, per TESTER.md
  "Live-Install Safety". The RC worktree was not modified at any point; the pre-fix reader of
  Finding 4 was synthesized in an isolated copy of `team/scripts/lib/` under the temp root.
- `git stash list` was empty and `git status --short` produced no output throughout.
- No prior-run artifacts existed in this task's `artifacts/` directory; this is a fresh
  first-run verification with no stale report to inherit from. No `gaps.md` is written and no
  Path C filing is made, because no `gap`, `bug`, or `stale-assertion` finding was recorded.
