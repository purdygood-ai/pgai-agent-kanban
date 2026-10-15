# Verification Report: rc/v2.24.15

Verified the release candidate `ai_rc/v2.24.15` (commit `0738ada`) against
`requirements/v2.24.15-bugfix-bundle-20260926.md` and the four bundled items it
carries (BUG-0390 subagent wrapper model pin, BUG-0391 guard destination bound,
BUG-0392 guard test env self-sufficiency, BUG-0389 provenance hygiene
ride-along). Pre-flight was clean: HEAD equals `ai_rc/v2.24.15`, the worktree is
clean, no stashes, and every `DONE` task in the project whose README names a
feature branch either has that branch merged into the RC or has it already
deleted post-merge. Both gated runners exited 0 - `run-unit-tests.sh` 5007
passed / 31 skipped, `run-integration-tests.sh` 548 passed / 1 skipped - with
zero lint findings in either findings file. Three of the four bundled items are
fixed and were verified behaviorally, not structurally: the wrapper model pins
are gone and the new guard test is non-vacuous; the model-mismatch narration
fires exactly once on disagreement and stays silent on agreement, missing,
empty, and unparseable usage, mutating nothing; the guard test module is green
both with and without the two ambient environment variables, at an identical
41-test count. The fourth (BUG-0391) closes the three shapes its ticket named
and fails against `ai_main`, but delivers only one of the two halves the report
asked for: the unreachable-destination exemption was never bounded, so the same
bypass is still reachable through ordinary git options the new skip list does
not enumerate - including `push --push-option`, the long spelling of a shape the
ticket explicitly named. I observed a completed push and a completed fetch pass
the guard unrefused and unrecorded, against a repository outside the temp root.
That bypass predates this RC and is identical on `ai_main`, so shipping
v2.24.15 strictly improves the boundary over what is already released; blocking
it would preserve the worse state. Four findings are filed via Path C-A
(autonomous follow-up filing - non-blocking; filed as BUG - code defect /
regression / test failure).

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Findings

### Finding 1: Subagent wrappers no longer pin a model, and the guard against regrowth is real

**Category:** pass

`grep -L '^model:' subagents/*.md` lists all seven wrappers; `grep -n '^model:'`
returns nothing. `subagents/MANIFEST.txt` carries the ownership sentence
("Model selection belongs to kanban.cfg [models.<provider>] and task-level
overrides, never to a wrapper: no file listed here may carry a frontmatter model
key"). `kanban.cfg_example`'s `[models.claude]` block states that the configured
value is what performs the task and that the wrappers carry no key of their own.

The new guard, `team/tests/unit/test_subagent_wrapper_omits_model_pin.py`, is
not a tautology: it has two cases over one shared scan function - the real
`subagents/` directory (must be empty of offenders) and a synthetic fixture
directory holding one clean wrapper and one carrying `model: sonnet`, asserting
the scan names *only* the offender. Both pass inside the green unit suite. The
fixture writes under `tmp_path`, which the parent conftest redirects under
`$PGAI_AGENT_KANBAN_TEMP_DIR`, so no bare `/tmp` entry is created.

### Finding 2: Model-mismatch narration behaves correctly across all five cases

**Category:** pass

`resolve_dominant_output_token_model` and `narrate_model_mismatch` were sourced
into a throwaway-rooted harness (`env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT`
with `KANBAN_ROOT`/`PGAI_AGENT_KANBAN_ROOT_PATH`/`PGAI_AGENT_KANBAN_TEMP_DIR`
all repointed at a scratch sandbox) and exercised directly:

| Case | Observed |
|---|---|
| stamped `claude-fable-5-1`, dominant `claude-opus-5` | exactly one `WARNING: model mismatch for task ...` naming both values |
| stamped equals dominant | silent |
| `tokens.json` absent | silent |
| `model_usage` an empty object | silent |
| `tokens.json` not JSON | silent |
| all of the above | `status.md` md5 unchanged; return 0 every time |

Against a real artifact
(`CODER-20260926-015-guard-destination-bound/artifacts/tokens.json`, whose
`model_usage` is `{haiku: 23, fable: 4468, sonnet: 63078}`) the resolver returns
`claude-sonnet-5` - the dominant producer, not the requested model - which is
the discrimination the narration exists to make. Ranking on output tokens alone
rather than `canonical_model_id` is the correct choice and its header says why.

### Finding 3: The guard's own test module is now environment-self-sufficient

**Category:** pass

An autouse fixture at `team/tests/unit/test_git_boundary_guard.py:87` clears
`GIT_GUARD_REAL_GIT` and `PGAI_GIT_GUARD_DEPTH` with `monkeypatch.delenv(...,
raising=False)`. Run from `team/`:

```
env GIT_GUARD_REAL_GIT=/usr/bin/git PGAI_GIT_GUARD_DEPTH=1 \
    python3 -m pytest tests/unit/test_git_boundary_guard.py -q -p no:cacheprovider -p no:randomly
  -> 41 passed
env -u GIT_GUARD_REAL_GIT -u PGAI_GIT_GUARD_DEPTH \
    python3 -m pytest tests/unit/test_git_boundary_guard.py -q -p no:cacheprovider -p no:randomly
  -> 41 passed
```

Identical counts both ways, which is BUG-0392's acceptance criterion verbatim
("all of them, both ways"). The runner-level scrub was kept, and
`team/scripts/lib/wake_guard_wiring.sh`'s header now describes both halves and
states the runner scrub remains authoritative. Note: the ticket's criterion
quotes the command as `python3 -m pytest team/tests/unit/...` from the repo
root, which fails on a conftest import (`No module named 'tests'`) regardless of
either variable - a pre-existing rootdir property of this tree, not something
this RC changed, and not a finding.

### Finding 4: The unreachable-destination exemption was never bounded, so the guard is still walkable

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same guard function has now been the subject of
four bugs across consecutive releases; each round closed the shapes it was
handed and left the decision that grants the exemption untouched, so the class
recurs by construction rather than by accident)
**Filed As:** BUG-0393 (Path C-A)

`CODER-20260926-015`'s `## Required Output` names two things: "a bounded
unreachable-destination exemption ... that cannot be satisfied by a token the
caller never meant as a destination" AND "a destination scan that does not
return the value of a value-consuming fetch/pull/push option as a destination".
Only the second shipped. `_git_boundary_guard_is_fixture_local` is unchanged
from `ai_main`; `_git_boundary_guard_find_destination` gained a nine-item skip
list.

What passes (verified against a repo whose `origin` resolves outside
`$PGAI_AGENT_KANBAN_TEMP_DIR`, role `coder`, evidence file redirected to a
scratch path):

| Invocation | Verdict | Records |
|---|---|---|
| `fetch origin` | refused, exit 1 | 1 |
| `fetch --depth 5 origin` | refused, exit 1 | 1 |
| `push -o ci.skip origin main` | refused, exit 1 | 1 |

All three are the ticket's acceptance criterion 1, and all three are **not**
refused when the same probe runs against `ai_main`'s copy of the shim and lib
(exported with `git archive ai_main` into a scratch directory), so the two new
unit tests genuinely fail pre-fix - criterion 3 met. Criterion 2 also holds: the
same invocations against a repository UNDER the temp root stay exempt with zero
records, so BUG-0388's fixture-noise reduction survives.

What still fails:

| Invocation | Verdict | Records | Effect on the destination |
|---|---|---|---|
| `push --push-option ci.skip origin main` | **not refused** | **0** | **push completed; `refs/heads/main` created on the upstream** |
| `fetch --filter blob:none origin` | **not refused**, exit 0 | **0** | **fetch completed; `refs/remotes/origin/newbranch` created** |
| `pull -s ours origin main` | not refused | 0 | real git ran the pull |
| `pull -X theirs origin main` | not refused | 0 | real git ran the pull |

`--push-option` is git's own long form of `push -o`; the skip list names `-o` and
the *fetch* long form `--server-option` but not the *push* long form, so the
exact command in BUG-0391's reproduction still walks past the guard when written
long. `--refmap`, `--exec` and `--repo` happen to refuse, but only because their
values contain `/` or resolve as a configured remote - the loose path heuristic
catching them by accident, not the skip list.

Two header comments consequently assert an invariant the code does not have. The
file's "Public API" block and `_git_boundary_guard_is_fixture_local` point 4 both
justify the exemption with "the real git invocation this shim hands off to would
fail identically before touching any network". The `fetch --filter blob:none
origin` case exited 0 having actually fetched, so that sentence is false as
shipped - acceptance criterion 4 unmet. `docs/blocking-site-census.md` row 62 was
widened in the same commit to say the scan skips "every fetch/pull/push option
that consumes a SEPARATE value of its own ... so an option's own value ahead of the
remote name is never mistaken for the destination", which overstates what ships
- criterion 5 unmet. `_git_boundary_guard_find_destination`'s own rewritten
header is, to its credit, honest: it states that an unlisted value-consuming
option "is still a residual instance of the same class - a gap to close by
extending this list".

**Why this is `SHIP-WITH-CONCERNS` and not `SHIP-WITH-SERIOUS-CONCERNS`.** The
residual bypass is not this RC's work: `ai_main` is unrefused and unrecorded on
all three residual shapes too, verified directly. The already-released v2.24.14
carries the identical hole. v2.24.15 closes nine option shapes and adds two
tests that lock them, so shipping moves the boundary forward and holding the
release preserves the worse state in the field. The honest severity of the
defect is `high` (a BLOCK-WORTHY control does not fire, and no workaround exists
- an agent cannot be relied on to avoid an option), and that is what the filed
bug records; the recommendation reflects what shipping versus not shipping does
to the running system, which is the question CM is answering.

### Finding 5: Six sites still name the subagent-frontmatter default this RC removed

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (reading only; the resolution chain itself is correct and
was verified)
**Filed As:** BUG-0394 (Path C-A)

`docs/OPERATIONS.md` now documents three tiers ending in "the provider CLI's
built-in default", and it is internally coherent - `grep` finds no contradicting
statement inside that file, so `WRITER-20260926-016`'s AC4 is met as written. Its
other claims check out too: the tier-3 warning it promises exists at
`wake_driver_prep.sh:143` and names the role via `log()`'s `wake(${AGENT})`
prefix; the `--add-claude-agents` / `--add-codex-agents` refresh table matches
`install.sh`; `team/roles/PM.md`'s "Model overrides" section carries no stale
frontmatter tier.

Outside that file, six sites still name the removed tier, two of them in
operator-visible output:

- `team/scripts/lib/wake_driver_prep.sh:145` - `model_source="subagent default
  for ${SUBAGENT}"`, printed alone by `provider_claude.sh:162` /
  `provider_codex.sh:413` when `selected_model` is empty, so the wake log reads
  `wake(coder): model: subagent default for coder`.
- `team/scripts/lib/provider_claude.sh:126` - `<subagent default>` pre-flight
  placeholder.
- `team/scripts/lib/wake_driver.sh:522` - comment.
- `team/templates/agent/REQUIREMENTS-TEMPLATE.md:203` and `:222`,
  `team/templates/agent/BRIEF-TEMPLATE.md:87` - operator-facing prose telling a
  requirements author that an omitted section means "all tasks use their
  subagent defaults".

`SOP-core.md`'s Documentation Discipline Rule and its naming rule ("log lines,
output strings, and shipped prose: describe BEHAVIOR") both bind here.

### Finding 6: A bundled item was unactionable - its findings do not exist at its own observed commit

**Category:** bug
**Fix Effort:** medium (3-5 CODER tasks - the root cause has to be diagnosed
before the invariant can be added)
**Systemic Risk:** medium (it will recur on the next run that fires the same
path, and each occurrence costs a bundle slot and an agent slot)
**Filed As:** BUG-0395 (Path C-A)

Requirements acceptance criterion 1 is "Each bundled item is addressed in the
implementation." BUG-0389 could not be. Its `## Symptom` claims its four
findings were "every one of them committed at the observed commit below"; its
`## Observed At Commit` is `747b37a`; none of `BUG-0001`, `BUG-0002`,
`PRIORITY-0005`, `BUG-0385` appears in
`team/tests/unit/test_promote_bundled_items_carries_unfixed.py` at that commit.
At lines 11, 13, 207 and 250 the file already carries the fictitious
`BUG-9001`/`PRIORITY-9001`-style fixture numbering it documents in its own
docstring. `python3 team/scripts/lint_hygiene.py --classes provenance` reports
`0 findings` on a scratch export of `747b37a`, on `ai_main`, and on the RC; all
three registered classes report zero on the RC.

`CODER-20260926-017-provenance-sweep` reached the same conclusion independently
and set `WONT-DO` with `Needs Human: yes` and a full written justification. I
re-derived it rather than accepting it, and its reasoning is correct. The
outcome the bundled item wanted is satisfied - the class is clean tree-wide -
but the item was never actionable, and `team/pgai_agent_kanban/lint/bug_filer.py`
documents a tree-cleanliness gate whose stated job is to prevent exactly this
filing. Corroborating detail: `747b37a` is on no branch in the dev tree, a
feature-branch commit whose branch was deleted after the v2.24.14 squash,
consistent with the scan having run in a worktree while the file was still being
edited.

### Finding 7: Step 6.12 question (5) is YES - 72 refused remote-verb records

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (the boundary held in every record; no ref state changed)
**Filed As:** BUG-0396 (Path C-A)

Detail under "Autonomous criterion" below.

### Finding 8: Requirements acceptance criteria 2 and 3

**Category:** pass

Every tracked Python file compiles (`python3 -m py_compile` over all 537) and
every tracked bash file passes `bash -n` (all 294), including each of the six
files this RC changed. `set -euo pipefail` is absent from the three changed
libs in both the RC and `ai_main` - they are sourced libraries, not entry
points, so this is unchanged baseline shape and not a constraint violation. No
hardcoded secret, password, API key or token appears in any added line.

### Finding 9: `release-state.md`, versions, and lifecycle bookends

**Category:** pass

`release-state.md` holds the four-field schema: `## Active RC` `v2.24.15`,
`## RC Opened At` `2026-09-26T17:47:12+00:00`, `## RC Opened By Task`
`CM-20260926-018-open-rc-2-24-15`, `## Last Released` `v2.24.14` (well-formed).
`pp_last_released_version pgai-agent-kanban` returns `v2.24.14`, and
`semver_gt v2.24.15 v2.24.14` succeeds - the RC version is strictly greater, via
the shared helper, never an inline compare. Prerequisite graph: no cycles, every
referenced task ID resolves to a real folder, bookends correct (CM-018 open ->
five build tickets -> TESTER-019 -> CM-021 release, currently `[W]` in
`cm_backlog.md`). Sequence numbers 012-021 are consistent and non-colliding.

### Finding 10: Naming, skip-gate, and coding-standards spot-checks

**Category:** pass

No `BUG-NNNN`, `PRIORITY-NNNN`, task ID, or `vX.Y.Z` citation appears in any
added line of the diff - the naming rule holds across all five commits.
`bash team/scripts/lint_skip_bug_gate.sh` exits 0 with "all skip annotations are
compliant" and "BUG-NNNN file-existence check: 0 missing files"; a raw grep for
`BUG-[0-9]{4}` across `team/tests/` returns many synthetic fixture IDs, which is
why the authoritative gate and not the grep is the signal here. No new script
was added under `team/scripts/`, so the `--help` check has no subject. No newly
hardcoded operational value and no duplicated `lib/` implementation surfaced in
the changed regions.

### Finding 11: Step 6.8 synthetic release simulation - not applicable

**Category:** pass

The diff touches no release-lifecycle file: not `cm-release.sh`, `cm-open-rc.sh`,
`cm-cancel-rc.sh`, `team/scripts/lib/project_paths.sh`, nor anything else in the
open/close cycle. `team/scripts/lib/wake_driver_finalize.sh` is the wake path,
not the release path. Step skipped with this note;
`team/tests/integration/test_cm_release_synthetic.py` ran green as part of the
integration suite regardless.

## Lint Findings (non-blocking)

None. `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`.../findings-integration.json` both exist with `"findings": []` and
`schema_version: 1`. An independent run of `python3 team/scripts/lint_hygiene.py`
across all three registered classes (`provenance`, `version-named-test`,
`comment-drift`) also reports `OK - 0 findings across 9 scan director(y/ies)`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=19d freshness=stale — the last cold-build run failed on `docker/claude-abliterated` (a `FROM` arity parse error under both engines) and `podman/rocky9` (a `COPY --chmod` step), a result already recorded as BUG-0167 (`## Status: done`, severity critical) and BUG-0166 (`## Status: done`); the record is stale on both counts — 19 days exceeds the 7-day window, and v2.9.20 is not the v2.24.15 under verification — so nothing is currently known about whether this tree's three Dockerfiles build cold.

## Autonomous criterion

- **(1) Tasks force-promoted from WAITING to BACKLOG by hand?** NO. Every
  promotion in this cycle is a wake-script line: `wake(coder): WAITING task
  CODER-20260926-017-provenance-sweep: prerequisites satisfied, promoting to
  BACKLOG` at 21:03:03 and `wake(writer): WAITING task
  WRITER-20260926-016-model-ownership-docs: ... promoting to BACKLOG` at 20:44:03,
  each followed by the matching `re-check complete: promoted 1 task(s)` line.
- **(2) `release-state.md` edited outside the CM scripts?** NO. Its mtime is
  17:47 on 2026-09-26, matching its own `## RC Opened At` of
  `2026-09-26T17:47:12+00:00` and `CM-20260926-018-open-rc-2-24-15`'s run; no
  later modification, and its four fields are internally consistent.
- **(3) Scripts patched mid-build?** NO. All five non-merge commits touch only
  files named in their own ticket's `## Required Output`: `8114349` the seven
  wrappers plus `MANIFEST.txt`, `kanban.cfg_example` and the new wrapper test;
  `06c2b6e` `wake_driver_finalize.sh` plus its new test; `a396092`
  `wake_guard_wiring.sh` plus the module fixture; `12cde72`
  `git_boundary_guard.sh`, census row 62 and two new tests; `5f2e0b7`
  `docs/OPERATIONS.md` alone. No out-of-band tooling patch.
- **(4) Queue files edited by hand?** NO. Queue mtimes fall inside wake
  reconciliation windows (`coder_backlog.md` 21:08, `tester_backlog.md` 21:12,
  `writer_backlog.md` 20:59) and every marker matches its task's own `## State`,
  including `[W] CM-20260926-021-release-2-24-15` for the waiting release task
  and `[x]` for the closed build tickets.
- **(5) Any agent other than CM invoked `fetch`, `pull`, or `push`?** **YES**,
  and filed as BUG-0396 (Path C-A). 72 records across six of the cycle's task
  folders, every one a `fetch`, every one refused, none changing ref state
  anywhere. Roles: `coder` 58, `tester` 13, `pm` 1. By origin: 67 from
  `pp_last_released_version`'s unconditional best-effort `git fetch origin
  --tags --quiet` inside the gated suites' fixture repositories; 3 from the same
  helper against `$HOME/develop/pgai-agent-kanban` (one in
  `PM-20260926-002-decompose-v2-24-15-bugfix-bundle-2026092` at 17:23:09, two in
  this TESTER task at 21:44:30-31 from my own `release-state.md` version check);
  1 from `CODER-20260926-015`'s own throwaway guard-reproduction fixture
  (`fetch --depth 5 origin` against a repo under the temp root); 1
  `git fetch --dry-run` in `CODER-20260926-013` at 19:01:16. Rated `low` per the
  reference rubric - refused attempts that moved no ref - so it leaves the
  recommendation where the rest of the report puts it. No record carries role
  `unknown`.

**Autonomous operation criterion: one manual-intervention-class YES - question
(5), rated `low`, filed as BUG-0396.** Questions (1) through (4): none. The
cycle ran from PM materialization through this verification without a hand edit
to any task state, queue file, or release-state field.

## Caveats

- **The fix ships in the repository; the running system still has the old
  wrappers.** `grep -n '^model:' ~/.claude/agents/*.md` still shows all seven
  live wrappers pinned (`opus`/`sonnet`). That is expected and is exactly what
  the new `docs/OPERATIONS.md` section documents: deployed copies refresh only
  on an install or upgrade run with `--add-claude-agents` (and
  `--add-codex-agents`). BUG-0390's second acceptance criterion ("after
  deploying the wrappers, the next CODER task's `tokens.json` dominant model
  equals its `status.md ## Model`") is therefore not verifiable from the RC and
  is an operator step after the release. The substitution is still live and
  observable today: `CODER-20260926-015`'s `tokens.json` shows
  `claude-sonnet-5` with 63,078 output tokens against a `## Model` stamp of
  `claude-fable-5-1`. Once deployed, the new narration will name exactly that
  pair; CM's release notes should carry the refresh instruction, per BUG-0390
  item 5.
- **`CODER-20260926-017-provenance-sweep` is `WONT-DO` with `Needs Human: yes`
  and its feature branch is left in place.**
  `ai_feature/CODER-20260926-017-provenance-sweep` exists locally at the same
  commit as `ai_rc/v2.24.15` with zero unique commits, deliberately left for
  inspection. It is exempt from Step 2's contributed-commits check under the
  same reasoning (nothing to contribute) and is not an anomaly, but CM will
  encounter an unmerged feature branch at release time and may want to delete
  it as the no-op cleanup that task's own `## Next Recommended Step` describes.
- **Coverage, informational only, never part of the verdict.** Unit: statements
  76 %, branches 63 % (blended 73 %). Integration: statements 11 %, branches
  9 % (blended 11 %). Read from the `totals` objects in
  `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and `.../integration.json`,
  using `percent_statements_covered_display` and
  `percent_branches_covered_display` after checking the key names against the
  generated files.
- **Test fidelity.** The two new guard tests drive the real shim as a
  subprocess against real git repositories with a real configured remote - the
  production shape, not a mock - which is why they discriminate pre-fix from
  post-fix. The wrapper-pin test scans the real `subagents/` directory and
  proves its own failure path. The narration tests are the one place where I
  would note a fidelity limit: they exercise the two functions, and I verified
  them directly too, but nothing in the suite exercises `wd_finalize_task`
  end-to-end with a real `tokens.json` written by `token_capture.sh`, so the
  call-site wiring (argument order, `task_artifact_dir` shape) rests on reading
  rather than on a test. This is a plausible rather than a concrete concern - I
  could not point at a condition mocked away - so it belongs here and not in
  `## Findings`.
- **`CODER` status `## Stale Literal Risks`.** No task in this cycle carried a
  non-empty section; nothing was flagged for me to watch.
- Prior-run artifacts: none. `artifacts/` was empty at start, consistent with a
  fresh first run of this task; no `.previous-RUN-N` file exists, and no prior
  verdict was reused.
