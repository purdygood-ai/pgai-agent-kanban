# Verification Report: rc/v2.22.1

## Summary

Verified release candidate `ai_rc/v2.22.1` (worktree HEAD `37e8abc`, clean tree, no stashes)
against `projects/pgai-agent-kanban/requirements/v2.22.1-bugfix-bundle-20260914.md` — a
discovery-pipeline BUG bundle of thirteen items. Pre-flight merge verification passed: every
DONE task's feature branch was already deleted post-merge (the normal lifecycle), so no
unmerged branch and no zero-commit anomaly exists. The RC carries 24 commits across 32 files
(+2606 / -292), all of them agent feature-branch merges in prerequisite order.

Each bundled bug's own `## Acceptance` block was used as the checklist. Guard logic was
exercised against the failure mode it prevents rather than read: the census rollup mismatch
detector was fed two seeded-wrong documents and a clean one; `pseudocron.py --help` was run
against a kanban root holding a sentinel-writing job to prove no scheduler loop starts;
`dashboard/create.sh --help` was run 25 times bare and 25 times piped into `head -1`, and the
pre-fix `ai_main` copy was run for contrast (it produces zero output — the original SIGPIPE
failure reproduced); the order-check's `-p no:randomly` was verified load-bearing by running
the same collection with and without it; and the engine-baseline capture was run twice under
two different ambient `MAX_TASK_SECONDS` values and byte-compared.

Both gated runners exited 0. Three defects were found and filed via Path C (autonomous
follow-up filing — non-blocking): a provenance regression introduced by this RC's own diff, one
unmet acceptance clause on BUG-0263, and a misattributing diagnostic message in code this RC
shipped. All three are small, isolated, and carry no runtime or integrity risk.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Test Suites

Authoritative gated-runner results (the same commands an operator or CM would run):

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 4498 passed, 31 skipped in 737.43s (12m17s) |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 465 passed, 1 skipped in 491.51s (8m11s) |

`UNIT_EXIT == 0` AND `INTEGRATION_EXIT == 0`. Every lint pre-flight in the breakage class
passed; the quality-class lints that fired are listed under Lint Findings below and are
non-blocking by construction. No test failures, so no `stale-assertion` or `pre-existing-bug`
categorization was needed.

Synthetic release simulation (Step 6.8) was triggered — the diff touches
`team/scripts/lib/project_paths.sh` — and ran inside the integration suite:
`tests/integration/test_cm_release_synthetic.py` — 15 passed.

## Code Coverage

Informational only; does not enter the PASS precondition.

- Unit:        line 75.3%  |  branch 61.7%   (15806 stmts, 4744 branches; blended `TOTAL` 72%)
- Integration: line 7.4%   |  branch 5.8%    (15797 stmts, 4744 branches)

Source: the `totals` object of `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`.../integration.json`, using the `covered_lines`/`num_statements` and
`covered_branches`/`num_branches` counts. The single blended `percent_covered` field (72.18
for unit) is deliberately not reported as "line coverage".

## Bundled-Item Verification

Every bundled item's own acceptance criteria, verified individually.

| Item | Verdict | Evidence |
|---|---|---|
| BUG-0259 census lint findings undocumented | pass | All five `_FINDING_KIND_*` constants (`missing_signal`, `unindexed_anchor`, `anchor_class_mismatch`, `unresolved_locator`, and the newly added `rollup_mismatch`) each have a row in `docs/blocking-site-census.md:469-473` with meaning and fix; `grep -rln '<kind>' --include=*.md .` returns a file for all five; `team/roles/CODER.md:88`'s claim is now true as written; `governance_byte_budget` reports zero findings (all seven roles under budget). |
| BUG-0260 hygiene provenance untracked sites | pass | `grep -nE 'CODER-2026\|WRITER-2026\|TESTER-2026\|PM-2026\|BUG-[0-9]{4}' team/tests/unit/test_operator_verb_manifest.py` returns nothing; both docstrings reworded, not deleted (commit `41dc95d`); no sidecar entry added; the file appears in neither runner's findings file. |
| BUG-0261 hygiene lint provenance | pass (for its own three instances) | All three instances it named are gone. The class itself is no longer clean — see Finding 1. |
| BUG-0263 order-check nondeterministic seed | **gap** | Harness path fully fixed and verified. Acceptance clause 3 (the `shuffle:<seed>` seam) is not met — see Finding 2. |
| BUG-0264 census rollup counts stale | pass | Table has 60 numbered rows counting 32 BLOCK-WORTHY / 26 CONVERT / 2 AGENT-RESOLVABLE; the rollup line reads exactly `60 sites, 0 unclassified` and `32 BLOCK-WORTHY, 26 CONVERT, 2 AGENT-RESOLVABLE`; per-class counts sum to the site count. Detector behaviorally triggered — see Step 6.5 below. |
| BUG-0265 baseline golden capture still coupled | pass | Behaviorally triggered — see Step 6.5 below. A test covers the capture path's independence (`test_capture_is_stable_across_invoking_shell_budget_values`), not only the compare path. |
| BUG-0267 hygiene provenance release notes | pass | `grep -nE 'CODER-2026\|WRITER-2026\|TESTER-2026\|PM-2026\|CM-2026' release-notes/v2.20.2.md` returns nothing; the reworded sentence still states all 14 bundled items were verified against their own acceptance criteria; the same edit was mirrored into `CHANGELOG.md`; no sidecar entry; the file appears in neither findings file. |
| BUG-0268 doc key parity false positives | pass | `lint_doc_command_truth.py` reports exactly one `UNKNOWN CONFIG KEY` — `[chain] max_tasks_per_wake` at `docs/OPERATIONS.md:930`, the discriminator the acceptance explicitly requires to survive. Zero against `project.cfg_example`, and zero for all nine keys the acceptance enumerates. Unit tests seed both a genuinely-unknown key and a real project.cfg key (`test_lint_doc_command_truth_keys_panes.py`, +201 lines). |
| BUG-0269 docs cite renamed cm scripts | pass | Zero `MISSING COMMAND` findings. `grep -rn 'cm-open-rc\.sh\|cm-release\.sh\|cm-cancel-rc\.sh' docs/` returns zero hits outside `docs/audits/`. Zero MISSING COMMAND is itself the `test -f` confirmation the acceptance asks for. |
| BUG-0270 pseudocron help never returns | pass | Behaviorally triggered — see Step 6.5 below. `lint_help_presence.py --verbose` shows `scanning: pseudocron.py`; the lint scans 117 scripts, checks 116, exempts 1, reports 0 offenders. No `HELP FAILS` for `pseudocron.py`. |
| BUG-0271 docs stale dashboard window list | pass | Pane-parity reports `0 finding(s)` across 44 window/pane citations against 15 census window ids. `docs/cm-release-reference.md` now names window 13 for `human-review`. `python3 team/scripts/dashboard/generate_window_docs.py --check` exits 0. |
| BUG-0272 dashboard create help sigpipe | pass | Behaviorally triggered — see Step 6.5 below. Two consecutive full `lint_doc_command_truth.py` runs against the unchanged tree produced byte-identical output. |
| BUG-0273 doc truth lint never invoked | pass | `run-unit-tests.sh:566-568` invokes the check at output line 52, before pytest at line 327; findings print in full; the runner records `doc_command_truth: quality-class finding(s) (exit 1) — non-blocking, continuing` and still exits 0. `findings-unit.json` carries an entry with `lint: doc_command_truth`, `severity: quality`. A seeded false citation in a scratch document produces a `MISSING COMMAND` finding (verified with `--doc`). The default run scans 13 documents including `README.md`, `HOW_TO.md`, `ARCHITECTURE.md` and `docs/docker.md` via the new `team/config/doc-truth-extra-scan-docs.txt`; `team/config/docs-manifest.txt` is untouched and still holds exactly the install-set closure its header documents. |

Requirements-doc-level acceptance criteria:

- *Each bundled item is addressed in the implementation* — 12 of 13 fully; BUG-0263 partially (Finding 2).
- *All Python files compile cleanly* — `python3 -m compileall -q team docs` exits 0; every changed `.py` compiles.
- *All bash files pass syntax check* — `bash -n` clean on every `.sh` in the tree and on every changed `.sh`.

## Findings

### Finding 1: This RC introduces 23 new provenance references while fixing 3

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (comment-only; no runtime path, no chain or data state involved)
**Regression:** yes (all 23 instances are added by this RC's own diff; the same files carried zero `BUG-NNNN` strings on `ai_main`)
**Integrity Damage:** no
**Filed As:** BUG-0274 (Path C-A — filed as BUG — code defect / regression / test failure)

Three of this bundle's thirteen items (BUG-0260, BUG-0261, BUG-0267) exist to empty the hygiene
lint's `provenance` class so that a clean tree is distinguishable from a dirty one at a glance.
Those three named sites are genuinely fixed. But the RC's own new code adds 23 fresh provenance
citations, so the class went the wrong way:

```
ai_main:        lint_hygiene: 5 finding(s)
ai_rc/v2.22.1:  lint_hygiene: 25 finding(s)
```

Per-file, comparing `ai_main` against the RC:

```
team/scripts/lint_doc_command_truth.py:        ai_main=0  rc=1
team/scripts/lint_gate_census_compliance.py:   ai_main=0  rc=6
team/scripts/dashboard/create.sh:              ai_main=0  rc=1
team/scripts/run-unit-tests.sh:                ai_main=0  rc=1
```

plus 14 more across four test files added or extended by this RC. Every instance cites a bug ID
from this RC's own bundle (`BUG-0264`, `BUG-0268`, `BUG-0272`, `BUG-0273`) inside a comment or
docstring — `docs/coding-standards.md` directive 8, and the same boundary CODER/WRITER's
pre-completion self-check names. The RC's commit messages already carry the bug IDs correctly
(e.g. `4380927 Neutralize pytest-randomly in the order-coupling self-test (BUG-0263)`), which is
where they belong. BUG-0274 lists all 23 sites as `<path>:<line>` references.

This is recorded as a Step-7 finding rather than only a lint line because it is a
coding-standards spot-check hit (Step 6.14) inside this RC's own new code, and because it
directly defeats the outcome three of the bundle's own items were bought for.

### Finding 2: `PGAI_TEST_ORDER=shuffle:<seed>` is still not reproducible with pytest-randomly installed

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (an operator debugging seam; no gated runner and no suite verdict depends on it)
**Regression:** no (the coupling predates this RC — `pytest_collection_modifyitems` is untouched by this diff; the RC fixed the harness's two passes and left the seam as it found it)
**Integrity Damage:** no
**Filed As:** BUG-0275 (Path C-A)

BUG-0263's third acceptance clause reads: "`PGAI_TEST_ORDER=shuffle:<seed>` produces the same
order on two runs with the same seed, with `pytest-randomly` installed." The shipped fix adds
`-p no:randomly` to `_voi_run()`'s two pytest invocations inside
`team/scripts/verify-test-order-independence.sh`. That covers the harness and nothing else.
`shuffle:<seed>` is an operator-facing seam in `team/tests/conftest.py`, documented there as the
way "a specific divergence can be reproduced by re-running with the same seed"; no script in the
tree invokes it, so a hand invocation carries no flag. Measured on this host (pytest-randomly
importable), in `team/`:

```
# no flag — NOT reproducible
$ for r in 1 2; do PGAI_TEST_ORDER=shuffle:1234 python3 -m pytest \
    tests/unit/test_pseudocron.py --collect-only -q > order-$r.txt; done
$ diff order-1.txt order-2.txt
Files order-1.txt and order-2.txt differ

# with -p no:randomly — reproducible
$ ... same loop with -p no:randomly ...
$ diff order-1.txt order-2.txt      # no output
```

The other three clauses of BUG-0263 are met and were verified directly (see Step 6.5). Nothing in
the tree tells a hand-invoking engineer the flag is required.

### Finding 3: key-parity findings name the wrong registry file

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (diagnostic text only; the lint's verdict, counts, and the runner's exit code are all correct)
**Regression:** yes (the two-registry merge is introduced by this RC; `ai_main`'s copy of the lint reads one registry, so its single-file message was accurate there)
**Integrity Damage:** no
**Filed As:** BUG-0277 (Path C-A)

This RC teaches `lint_doc_command_truth.py`'s key-parity check to read two Key Registries —
`config_loader.sh` for `kanban.cfg` keys and the newly added block in `project_paths.sh` for
`project.cfg` keys — and merge them into one lookup table. The lint's own inline comment states
the design ("That source is TWO registries, not one"). The three finding formatters and the
dual-channel summary still hardcode `config_loader.sh` as the declaring file
(`lint_doc_command_truth.py:1182,1206,1218,1937`). Ten of the thirteen key-parity findings the
lint currently reports name a key that lives in `project_paths.sh`:

```
$ python3 team/scripts/lint_doc_command_truth.py | grep -A1 'UNDOCUMENTED CONFIG KEY: .hooks.'
UNDOCUMENTED CONFIG KEY: [hooks] cm_release_post_tag_hook
  Cause: config_loader.sh's Key Registry declares [hooks] cm_release_post_tag_hook ...
$ grep -c 'cm_release_post_tag_hook' team/scripts/lib/config_loader.sh
0
$ grep -c 'cm_release_post_tag_hook' team/scripts/lib/project_paths.sh
1
```

An operator following the "To fix" step is sent to a file that does not contain the key — the
runnable-remedy property the dual-channel contract exists to guarantee.

## Step 6.5 — Guard logic triggered against its own failure mode

Each of these attempted the thing the logic prevents rather than observing the happy path.

- **Census rollup detector (BUG-0264).** Called `_scan_census_rollup(repo_root)` against three
  scratch trees: the real census unchanged, one with `60 sites` edited to `59 sites`, and one
  with the per-class line edited to `31/27/2` (a wrong split whose total is still right).
  Results: `0`, `1 rollup_mismatch at line 160`, `1 rollup_mismatch at line 160`. The detector
  fires on both the total and the per-class direction and stays silent on the clean document.
- **`pseudocron.py --help` (BUG-0270).** Exits 0 in well under a second, printing a usage block
  that names every argument the script accepts (`-h/--help` is the only one; argparse is
  invoked at `main()` line 212 before anything else). Run against a kanban root whose
  `pseudocron.cfg` held a `touch <sentinel>` job scheduled for the current minute, then waited:
  the sentinel never appeared — `--help` starts no scheduler loop.
- **`dashboard/create.sh --help` (BUG-0272).** 25 consecutive bare invocations: zero failures.
  25 consecutive invocations piped into `head -1`, checking `PIPESTATUS[0]`: zero failures.
  Original failure mode reproduced for contrast — the `ai_main` copy of the same script,
  extracted with `git show`, prints **zero lines** and exits non-zero on `--help`, which is the
  SIGPIPE-under-`pipefail` defect the fix removes. The fixed version prints 30 lines,
  reproducing `head -30`'s output exactly.
- **Order-check determinism (BUG-0263).** Forward and reverse collection orders each captured
  twice with `-p no:randomly`: identical across runs, and the reverse order is exactly the
  forward order reversed (66 items each). Control without the flag: two forward runs of the
  same collection **differ**, proving the flag is load-bearing rather than decorative.
- **Engine-baseline capture budget pin (BUG-0265).** `engine-behavior-baseline.sh --capture`
  run twice, once from a shell exporting `MAX_TASK_SECONDS=10800 KILL_GRACE_SECONDS=120` and
  once with `5400/30`. The two `wake-log.txt` files are byte-identical (`cmp` clean), and both
  are byte-identical to the committed golden. `--compare` from the hostile-budget shell exits 0
  with "3 surfaces match goldens".
- **Doc-truth lint on a seeded false citation (BUG-0273).** A scratch document citing
  `team/scripts/definitely-not-a-real-script.sh` produced a `MISSING COMMAND` finding naming
  every path the resolver tried.

## Step 6.13 — Skip-cites-real-bug

No placeholder skip IDs. The four `BUG-12NN` strings that a naive grep surfaces are in-string
fixtures inside `team/tests/unit/test_skip_bug_citation_scan.py`, which is the gate's own test.
The authoritative gate agrees: `lint_skip_bug_gate: BUG-NNNN file-existence check: 0 missing
files` / `OK — all skip annotations are compliant`.

## Step 6.1 — release-state.md drift

No drift. `pp_last_released_version pgai-agent-kanban` returns `v2.22.0`; `semver_gt v2.22.1
v2.22.0` holds. `## Last Released` reads `v2.22.0` — well-formed `vX.Y.Z`, and its presence is
by design. `## Active RC` is `v2.22.1`, matching the branch under verification, opened
`2026-09-14T18:55:07+00:00` by `CM-20260914-039-open-rc-2-22-1`. On-disk `VERSION` reads
`v2.22.0`; that is correct at this point in the lifecycle — `release_notes.sh` step 11d writes
the new version before `release_finalize.sh`'s pre-tag VERSION gate reads it.

## Step 6.10 — Prerequisite dependency graph

Acyclic. Every referenced task ID resolves to a real task directory. Bookends correct:
`CM-20260914-039-open-rc-2-22-1` opens (no prerequisites), the ten implementation tasks depend on
it, `CODER-033` additionally depends on `029/030/031/032`, `WRITER-035` on `034`, `CODER-036` on
`035`, this TESTER task on all ten, and `CM-20260914-042-release-2-22-1` sits `[W]` in
`cm_backlog.md` waiting. Sequence numbers 029-040 and 042 are consistent and non-colliding.

## Lint Findings (non-blocking)

Grouped by lint class across both runners' findings files, one bug per class per run.

- `hygiene` — 50 instances (25 in `findings-unit.json`, 25 in `findings-integration.json`; the
  same 25 provenance sites seen by both runners), all `severity: quality`, rolled up to `low` —
  filed **BUG-0274** (Path C-A). 23 of the 25 are this RC's own new sites and are also
  Finding 1 above; the remaining two are `release-notes/v2.22.0.md:34` and `:53`, which shipped
  with v2.22.0 and are untouched here.
- `doc_command_truth` — 1 coarse finding carrying 18 underlying instances (5 `HELP FAILS`, 1
  `UNKNOWN CONFIG KEY`, 2 `CONFIG DEFAULT MISMATCH`, 10 `UNDOCUMENTED CONFIG KEY`),
  `severity: quality`, rolled up to `medium` — filed **BUG-0276** (Path C-A). These are not
  caused by this RC; they are what the newly-wired check found on its first run inside the gate,
  and no open or running bug covered them. All 18 sites are listed in BUG-0276.

No other lint class produced a finding. Both findings files were present and parsed.

A note on why the `hygiene` class got a new bug rather than a citation of the open one: BUG-0261
is `running` and covers the same class, which is normally a cite-don't-duplicate case — and the
gated runners' own aggregate filer (`team/scripts/lib/lint_bug_filer.sh`) deduplicated against it
and filed nothing. But BUG-0261 is a member of *this RC's own bundle*, so `cm-release.sh` Step 16b
will set it to `done` on ship, and its instance list names three sites that are already fixed.
Citing it would leave 23 live instances tracked by a closed bug. BUG-0274 is a genuinely distinct
set of sites, not a duplicate.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=7d freshness=stale — the last lane run was red on the `docker/claude-abliterated` flavor under both engines (malformed `FROM` arity) and on `podman/rocky9` (`COPY --chmod=+x`), and it is stale on both counts (186h exceeds the 7-day window, and v2.9.20 is not the v2.22.1 under verification); it cites BUG-0167, filed by that deep-verify run and since closed, and the two failure shapes it names are now covered by the `dockerfile-static-lint-01` gate (census row 54) which runs green in this RC's unit gate.

Read through `deep_verify_ledger_read_latest`; the verdict is the ledger's own word and is not
re-derived. No deep-verify lane was run during this verification, per Step 5d. No bug was filed
for this record, per the cite-never-re-file rule. `team/scripts/deep-verify.sh` is present and
executable, so the "images ship with no cold-build gate anywhere" gap does not apply.

## Autonomous criterion

| # | Check | Answer |
|---|---|---|
| 1 | Any task force-promoted from WAITING to BACKLOG manually? | NO — the wake log records every promotion, e.g. `[2026-09-14T23:26:14+00:00] wake(coder): WAITING task CODER-20260914-033-wire-doc-truth-lint: prerequisites satisfied, promoting to BACKLOG` |
| 2 | Was `release-state.md` edited outside the cm scripts? | NO — its four fields are internally consistent and match `CM-20260914-039-open-rc-2-22-1`'s recorded open |
| 3 | Were any scripts patched mid-build? | NO — all 24 commits are agent feature-branch work merged in prerequisite order; every tooling change traces to its own ticket |
| 4 | Were any queue files manually edited? | NO — markers are consistent with task states (`[A]` on this task, `[x]` on the completed ten, `[W]` on the pending CM release) |

**Autonomous operation criterion: Manual interventions required: none.**

## Caveats

- **The gated runners' hygiene auto-filer has a same-bundle blind spot.** It deduplicates a
  hygiene class against any `open` or `running` bug for that class. When the matching bug is a
  member of the RC currently in flight — exactly the case here with BUG-0261 — new instances
  introduced by that same RC are absorbed into a bug that is about to be marked `done`, and they
  would have gone untracked had this verification not filed BUG-0274 by hand. This is arguable
  design rather than a demonstrated defect in isolation, so it is recorded here rather than
  filed; a future cycle may want the filer to treat a `running` bug that belongs to the open RC
  as not-a-match.
- **Two operator-root files are stale relative to the dev tree.** The installed
  `$KANBAN_ROOT/SOP.md` and `$KANBAN_ROOT/roles/TESTER.md` both date from 2026-09-05 and are
  assembled catalogs much larger than their dev-tree sources (`team/SOP-core.md` is 255 lines;
  `team/roles/TESTER.md` is 397). The installed TESTER.md cites
  `team/pgai_agent_kanban/reports/lint_finding_groups.py` as the lint-aggregation helper; that
  module does not exist in the RC tree, and the RC's own `team/roles/TESTER.md` does not cite it
  (its aggregate rule is stated in prose instead). Grouping for this report was therefore done by
  hand — trivially, since only two lint classes fired. This is operator-install state, not RC
  state, so it is not a finding against v2.22.1; it is recorded because the next reader of that
  installed file will hit the same dead reference.
- **A new help-presence exemption ships in this RC.** `team/scripts/help_presence_exempt.txt`
  gains `promote_bundled_items.py`, with a thorough comment explaining that it is
  release-mechanics internal and that a `--help` of its own is "left for a follow-up ticket to
  add". No such ticket or bug exists. The exemption is well-justified and BUG-0270's acceptance
  does not forbid it, so this is not a finding — but the follow-up it names is currently tracked
  by nothing except that comment.
- Coverage is reported for information only and did not enter any verdict. The integration
  suite's low figures are expected: it is measured against the whole tree while exercising
  cross-cutting paths.
