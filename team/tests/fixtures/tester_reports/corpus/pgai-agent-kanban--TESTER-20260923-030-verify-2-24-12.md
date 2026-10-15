# Verification Report: ai_rc/v2.24.12

**Project:** pgai-agent-kanban
**RC branch:** `ai_rc/v2.24.12` @ `9816229db229f7e2e244acfb32da0db2295d74db`
**Compared against:** `ai_main` @ `8669095`
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.24.12-bugfix-bundle-20260923.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260923-030-verify-2-24-12` (detached HEAD at the RC commit, clean, no stashes)
**Workflow type:** release
**Verified:** 2026-09-23

## Summary

v2.24.12 closes the structural hole that let the bug and priority discovery
steps write a requirements bundle whose `## Bundled Items` were pipeline
narration rather than item files — the wedge whose evidence is this RC's own
requirements document. Three new refusal sites were added and all three were
exercised directly: the shared `_disc_bundle_item_shape_ok` predicate in
`discovery_steps.sh` (eleven synthetic shapes, all classified correctly), its
application as a post-filter guard in `discovery_step_bugs` and
`discovery_step_priority` (placement confirmed to sit after
`_disc_bug_deferral_filter` and after the priority cap slice respectively),
and `_disc_write_bundle`'s zero-valid-items backstop (five cases: three
refusals with no file written and a full dual-channel trio, two happy paths
including correct `RIDE_ALONG` suffix rendering). The new operator script
`retire-empty-bundles.sh` was exercised end to end in a throwaway sandbox
across all five of its acceptance criteria — dry-run leaves every checksum
unchanged, apply retires exactly the one retirable bundle and leaves the kept,
skipped and mixed bundles byte-identical, and a second apply is idempotent.
The operator documentation is substantive and its claims check out against the
source.

Two things keep this off `PASS`. The gated integration runner exited 1 on a
single non-deterministic dashboard test that this RC's diff cannot reach, and
the RC adds a cross-reference from an installed document to one the installer
does not deliver. Neither threatens the release's usability. Both, plus a
quality-class lint finding and a pre-existing blind spot in the check that
should have caught the second, are filed via Path C-A (filed as BUG — code
defect / regression / test failure).

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Suite Results

| Suite | Command | Exit | Result |
|---|---|---|---|
| unit | `team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=0` | 4946 passed, 31 skipped in 902.43s |
| integration | `team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=1` | 1 failed, 541 passed, 1 skipped in 683.46s |

Both runners were invoked from the dev-tree root inside the task worktree;
output is captured at `/tmp/pgai_kanban_tmp/tester/unit.log` and
`/tmp/pgai_kanban_tmp/tester/integration.log`. `INTEGRATION_EXIT=1` is the
suite-pass precondition failing, so `PASS` is not available regardless of how
the single failure is attributed.

## Coverage (informational only)

Read from the `totals` object of each runner's JSON; field names verified
against the generated files before extraction (`percent_statements_covered`,
`percent_branches_covered`, `percent_covered`).

| Suite | Line (statement) | Branch | Blended |
|---|---|---|---|
| unit | 76.36 % | 63.17 % | 73.31 % |
| integration | 11.45 % | 9.17 % | 10.93 % |

Coverage did not enter the verdict.

## Findings

### Finding 1: Post-filter item-shape guard in `discovery_step_bugs`

**Category:** pass

CODER-025 AC1. `team/scripts/lib/discovery_steps.sh` line 287 applies
`_disc_bundle_item_shape_ok` to `unhandled_files` *after* the
`_disc_bug_deferral_filter` assignment at line 269 and before the Active-RC
guard — that is, to the list `_disc_write_bundle` actually renders, which is
what the criterion asks for. The pre-existing `grep -q '^/'` pre-filter check
above it is untouched.

Behavioural verification of the predicate, sourcing the library directly in a
throwaway sandbox under `$PGAI_AGENT_KANBAN_TEMP_DIR` (11/11 as expected):

| Input | Expected | Result |
|---|---|---|
| `/a/b/BUG-0001-x.md` | accept | accept |
| two absolute paths | accept | accept |
| `/a/b.md` + TAB + `RIDE_ALONG:BUG-0002` | accept | accept |
| `/a/b.md` + TAB + SPACE + `RIDE_ALONG:...` | refuse | refuse |
| `[2026-01-01T00:00:00+00:00] wake(pm): ...` | refuse | refuse |
| one real path plus one narration line | refuse | refuse |
| `bugs/BUG-0001.md` (relative) | refuse | refuse |
| `/a/b.md` + TAB + `GARBAGE` | refuse | refuse |
| `/a/b.md` + TAB + empty annotation | refuse | refuse |
| blank lines only | accept (vacuous) | accept |
| empty string | accept (vacuous) | accept |

The two vacuous-accept cases are correct: emptiness is handled by the
separate `[[ -z "$unhandled_files" ]]` check above and, at the render layer,
by `_disc_write_bundle`'s own zero-valid-items guard (Finding 3).

### Finding 2: Same guard in `discovery_step_priority`

**Category:** pass

CODER-025 AC2. `discovery_steps.sh` line 433 applies the same predicate to
`bundle_files` immediately after the `head -n "$_cap"` slice that finalises
it, before the Active-RC guard. Same predicate, so Finding 1's behavioural
table covers it.

### Finding 3: `_disc_write_bundle` refuses when no entry resolves to a real file

**Category:** pass

CODER-025 AC3 and AC4, and CODER-026 AC2. Invoked directly with stubbed
`wf_load_plugin` / `wf_bundle_source_branch`:

| Files argument | Return | File created | Dual-channel output |
|---|---|---|---|
| one narration line | 1 | no | `error: discovery.bundle-write-no-valid-items` + message + remedy |
| one non-existent absolute path | 1 | no | same trio |
| empty | 1 | no | same trio |
| one real file | 0 | yes | n/a |
| one real file + one real file with `RIDE_ALONG` | 0 | yes | n/a |

The narration case is AC4 stated directly: injecting a line beginning
`[2026-01-01T00:00:00+00:00] wake(pm): discovery_step_bugs: ...` produced no
file in the requirements directory.

### Finding 4: Happy path and `RIDE_ALONG` rendering preserved

**Category:** pass

CODER-025 AC5, CODER-026 AC3. The two successful writes above rendered:

```
- BUG-0001-real.md (`/.../BUG-0001-real.md`)
- BUG-0002-rider.md (`/.../BUG-0002-rider.md`) — hygiene ride-along (piggyback on BUG-0001-real)
```

Both lines present, the ride-along suffix verbatim in the wording
`team/roles/PM.md` specifies, on its own item line.

### Finding 5: Census declarations and Locator Index entries

**Category:** pass

CODER-025 AC6. Three `CENSUS-ANCHOR` markers exist in code —
`discovery-steps-bug-item-shape-01` (line 294),
`discovery-steps-priority-item-shape-01` (line 440),
`discovery-state-write-bundle-no-valid-items-01` (line 708) — each directly
under an inline `CONVERT (docs/blocking-site-census.md)` class comment, and
each has a matching Locator Index line in `docs/blocking-site-census.md`
classified `CONVERT`. The narrative block above the index explains all three.

The declared class matches the behaviour: each site logs, emits the trio, and
returns non-zero to its in-process caller. None creates a HALT flag; none
alters task state. Verified by reading each site and by the refusals observed
in Findings 1 and 3, which ended the call and nothing else.

### Finding 6: Syntax checks across the tree

**Category:** pass

Requirements-doc AC2 and AC3, and CODER-025 AC6 / CODER-027 AC5.
`bash -n` over all 292 tracked `*.sh`: 0 failures, including both edited
libraries and the new operator script. `ast.parse` over all 531 tracked
`*.py`: 0 syntax errors.

### Finding 7: Regression tests genuinely exercise the new behaviour

**Category:** pass

CODER-026 AC1/AC2/AC3/AC5. `team/tests/unit/test_bundle_write_refuses_without_valid_items.py`
is new and was read end to end: four test classes covering all-narration,
all-nonexistent, empty-argument, and a positive control that the guard does
not fire when one entry is real. The refusal assertions check the `error`,
`message` and `remedy` keys, not just the exit code.
`team/tests/integration/test_discovery_narration_channel.py` was rewritten
from `TestSeededNarrationLineIsSkippedLoudly` (asserting the old
skip-and-still-bundle behaviour) to
`TestContaminatedPostFilterListRefusesTheWholeBundle`, asserting no bundle is
written and that neither trigger nor riders are marked `running` — the
correct fixture sweep for a guard that changed the behaviour the old test
pinned. Both files ran green inside the gated suites.

Test fidelity: the unit file stubs `wf_load_plugin` / `wf_bundle_source_branch`
rather than resolving a real plugin manifest. That is the right scope for a
unit test of one guard, and the real resolution path is exercised by the
integration tests that drive the whole discovery pipeline — the condition the
guard protects against (a non-path line in the item list) is present
unmocked in both layers. No sanitised-proxy concern.

### Finding 8: `retire-empty-bundles.sh` report mode modifies nothing

**Category:** pass

CODER-027 AC1, AC3, AC4. Against a sandbox project holding five bundles — one
all-narration at `open`, one with a resolvable item, one already `done`, one
already `running`, and one mixing a narration item with a resolvable one — the
no-flag run printed a per-file stanza with a verdict and classified correctly:

```
v1.0.1 ... items: 1 item line(s) ..., none resolve to an existing file   verdict: retirable
v1.0.2 ... items: item resolves to an existing file: .../BUG-0001-real.md verdict: kept
v1.0.3 ... status: done                                                  verdict: skipped (already done)
v1.0.4 ... status: running                                               verdict: skipped (already running)
v1.0.5 ... items: item resolves to an existing file: .../BUG-0001-real.md verdict: kept
summary: 5 bundle(s) considered, 1 retirable, 2 kept, 2 skipped (dry run -- re-run with --apply to retire)
```

`md5sum` over every file before and after was identical — AC1's stated
verification method, run as stated. The mixed bundle (one narration item, one
real item) was classified `kept`, which is AC3.

### Finding 9: `retire-empty-bundles.sh` apply mode retires exactly the eligible bundle

**Category:** pass

CODER-027 AC2. The apply run set `## Status` to `done` on the one retirable
bundle and reported `1 retirable, 1 retired, 2 kept, 2 skipped`. A per-file
`diff` against a pre-run snapshot showed the kept, both skipped, and the mixed
bundle byte-identical, and the retired bundle differing only in the status
value. A second apply run was idempotent: `0 retirable, 0 retired, 2 kept,
3 skipped`. No file was deleted.

### Finding 10: `--help` states usage, both modes, and the eligibility rule

**Category:** pass

CODER-027 AC5. `--help` exits 0 and prints the usage line, the flag
vocabulary, the parenthetical distinguishing `--apply` from the report-only
default, and the eligibility rule in full (not already `done`/`running`, AND
every `## Bundled Items` line failing to resolve, including zero item lines).
This is the authoritative statement the operator documentation points at
instead of restating, per WRITER-028's constraint.

### Finding 11: Operator documentation is accurate and complete

**Category:** pass

WRITER-028 AC1, AC2, AC4, AC5. `docs/operator-troubleshooting.md` gains "The
requirements queue is full of near-identical bundles", which names the
three-part symptom (consecutive patch versions, one dateline, no resolvable
items), explains the version-slot cost ending in `max_patch` exhaustion, and
notes that `list-stale-requirements.sh` excludes bundle files so it will not
surface the run. The dry-run invocation is shown first and `--apply` second,
and the section states plainly that retiring sets `## Status` to `done` and
deletes nothing. The document's own intro enumeration was updated so it is not
stale. No file was added under `docs/` (`git diff --diff-filter=A --name-only`
over `docs/` is empty) and `team/config/docs-manifest.txt` is unchanged, which
is AC4; every manifest entry still resolves to a real file. No markdown links
were introduced in the new section, so there are no broken internal links
within it.

### Finding 12: Gated integration runner exited 1 on a dashboard pane-death race

**Category:** bug
**Fix Effort:** medium (3-5 CODER tasks — a tmux activation-hook race, and a
flaky gated test that will keep costing future RCs their `PASS` until it is
either fixed or quarantined behind a cited bug)
**Systemic Risk:** medium (nothing about this RC, but an intermittently red
gated suite recurs across every subsequent RC and degrades the one
authoritative pass/fail signal TESTER has)
**Filed As:** BUG-0379-dashboard-switch-storm-pane-death (Path C-A — filed as
BUG — code defect / regression / test failure)

`tests/integration/test_dashboard_activation_hook_lifecycle.py::test_thirty_immediate_window_switches_leave_every_pane_alive`
failed: after thirty immediate `select-window` calls, pane `%37` of forty was
gone. `Missing: ['%37']`, `New/unexpected: []`, at line 194.

Attribution was investigated rather than assumed, because the previous release
(v2.24.11) recorded `INTEGRATION_EXIT=0` with 542 passed — so on the face of
it a green command went red.

- Diff analysis: `git diff --name-only ai_main..ai_rc/v2.24.12` lists eight
  files. None is a dashboard, tmux, or `create.sh` path. The failing test file
  is itself unchanged by this RC. There is no code path by which the RC can
  reach this test.
- Repetition on an isolated clone of the RC: 1 failure in 17 runs.
- Repetition on an isolated clone of `ai_main`: 0 failures in 15 runs.

Fifteen clean runs is not proof that `ai_main` never fails — the sample is
small for a race, and a 1-in-17 rate would plausibly produce zero failures in
fifteen attempts. What the two samples together do establish is that the
failure is non-deterministic rather than a deterministic break, and the diff
establishes that the RC cannot have introduced it. Rated `medium` on that
evidence, with **Regression: no**, and stated here in prose rather than
buried: a reader who weighs "green last release, red this one" more heavily
than the diff evidence should read this finding as the argument for that view
and is free to reach a different rating.

The underlying defect is real and user-visible: a dashboard pane can vanish
when an operator flips between windows faster than the activation hook
settles. The dashboard is recoverable by recreating the session, which is why
this is `medium` and not `high`.

### Finding 13: Installed `OPERATIONS.md` cites a document the installer does not deliver

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks — one manifest line, or a reword of two
citations)
**Systemic Risk:** low (isolated to documentation delivery; no runtime path
depends on it)
**Filed As:** BUG-0381-installed-operations-cites-uninstalled-doc (Path C-A)

`team/config/docs-manifest.txt` states the rule in its own header: *"A
document listed here must never cite a docs/ path this file omits — that would
just move the dangling pointer one hop deeper."* It then records
`operator-troubleshooting.md` as a deliberate exclusion, checkout-only.

`docs/OPERATIONS.md` is on the manifest and is installed. This RC adds a
paragraph to it (line 3141) pointing the reader at
`operator-troubleshooting.md`. Confirmed empirically against the live
installed root: `docs/OPERATIONS.md` is present at 225203 bytes;
`docs/operator-troubleshooting.md` does not exist there.

Two things keep this at `gap`/medium rather than higher. First, the condition
predates the RC — `docs/operator-commands.md`, also manifest-listed and also
installed, already carried a markdown link to the same target on `ai_main`
(line 1980). This RC widens an existing defect rather than creating one.
Second, the new paragraph repeats both runnable invocations inline and names
the script's `--help` as authoritative, so an installed-root reader can still
do the work; only the longer walkthrough is unreachable.

WRITER-028's own `status.md` flagged this concern before merge, which is the
process working — it was recorded rather than hidden. It is filed here because
a concern noted in a task status is not a tracked item.

Separately noted in the filing: `docs/README.md` line 22 still describes
`operator-troubleshooting.md` as "Five common symptoms". This RC made it six.

### Finding 14: The docs-citation closure check cannot see the citation form that broke

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks — widen the pattern for files already
inside `docs/`, resolve relative to the citing file, add two fixtures)
**Systemic Risk:** low (a guard with a blind spot, not a runtime defect)
**Filed As:** BUG-0382-docs-citation-check-misses-bare-filenames (Path C-A)

`python3 team/scripts/check_docs_citations.py` exits 0 on this RC and reports
"24 governance file(s) scanned, 13 docs/ citation(s) checked, all named in
docs-manifest.txt" — while the two citations in Finding 13 stand.

The transitive walk (`walk_docs_citation_closure` in
`team/pgai_agent_kanban/lib/docs_citation_scan.py`) is correct and does follow
manifest-listed documents one hop deeper. The defect is upstream of it, at
line 56:

```python
DOCS_CITATION_RE: re.Pattern[str] = re.compile(r"docs/[A-Za-z0-9._/-]+\.md")
```

A citation counts only when the author wrote the literal `docs/` prefix.
Inside `docs/` the natural forms are a bare sibling filename and a relative
markdown link, and neither carries it, so the walk is handed nothing to
follow and terminates with a clean verdict it has not earned. This is why
Finding 13's pre-existing instance has gone unreported and why the new one
passed pre-merge checks.

The pattern predates this RC. Filed separately from Finding 13 because the
fixes are different files and different work: one is a manifest or wording
decision, the other is a scanner change with its own regression fixtures.

### Finding 15: Stray blank line splits the `discovery_state.sh` header comment block

**Category:** pass-with-caveat

Step 6.14 coding-standards spot-check. The RC's addition to the file header
ends with a `#` line followed by a bare empty line, splitting what was one
contiguous comment block into two:

```
#       section -- see the guard's own comment for the full rationale.
#
                                    <- bare blank line, no '#'
#       A line in <files> may carry a trailing "\t<annotation>".
```

Cosmetic. Nothing parses this block, no lint flagged it, and both suites
reached pytest. Recorded rather than filed: it is a one-character fix that
belongs to whoever next edits the file, and a bug report would cost more to
triage than the defect is worth.

### Finding 16: Operator verb manifest registration not taken

**Category:** pass-with-caveat

CODER-027's Required Output made manifest registration conditional — *"if that
manifest governs operator scripts"*. It does not govern all of them.
`team/config/operator_verbs.manifest` catalogues the operator *verb* set with
REST twins; thirteen scripts under `team/scripts/` declare
`OPERATOR_VALID_FLAGS` and are absent from it, twelve of them predating this
RC (`list-stale-requirements.sh`, `show-test-report.sh`,
`export-project.sh`, `verdict-report.sh` and others).
`team/scripts/lint_operator_verb_manifest.py` checks manifest-to-tree
direction plus `operations.py` route coverage; it does not require every
operator script to have a row, and it reported no drift.

The script is nonetheless delivered to installed roots: `install.sh` and
`team/scripts/upgrade.sh` both copy the whole `team/scripts` directory to
`<kanban-root>/scripts`, so the documentation's
`$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/retire-empty-bundles.sh` invocation
resolves. Verified by reading both install loops.

### Finding 17: Requirements-doc acceptance criterion 1

**Category:** pass-with-caveat

The requirements doc's first criterion is *"Each bundled item is addressed in
the implementation"*. Its two `## Bundled Items` are not item files — they are
the verbatim narration lines that are the evidence of the wedge, which is what
makes this bundle its own exhibit. PM read them as that evidence and
decomposed them into four tickets that fix the mechanism which produced them.
Findings 1 through 11 confirm every one of those tickets' criteria is met.

Recorded as `pass-with-caveat` rather than `pass` because the criterion as
literally written cannot be satisfied by a bundle whose items are not items;
the substance behind it is fully addressed.

### Finding 18: Release-state schema and version ordering

**Category:** pass

Step 6.1. `projects/pgai-agent-kanban/release-state.md` holds all four fields.
`## Active RC` is `v2.24.12`, matching the branch under test;
`## RC Opened At` is a well-formed ISO-8601 value; `## RC Opened By Task` names
`CM-20260923-029-open-rc-2-24-12`, which exists and reports `DONE`;
`## Last Released` is `v2.24.11`, well-formed. `pp_last_released_version
"pgai-agent-kanban"` returns `v2.24.11`, and `semver_gt "v2.24.12" "v2.24.11"`
via `team/scripts/lib/semver.sh` returns true. No inline comparison was made.

### Finding 19: Pre-flight — every DONE task landed on the RC

**Category:** pass

Step 2. All task folders under `projects/pgai-agent-kanban/tasks/` were scanned
for `## State: DONE` with a `## Feature Branch` other than `none`. No such
branch still exists locally, which passes both checks trivially — CODER and
WRITER delete branches after merging, and each of the four prerequisite tasks'
merges is present in the RC history as a `--no-ff` merge commit (`e6c935d`,
`227b4af`, `84818c6`, `9816229`). Step 3: `git rev-parse HEAD` equals
`git rev-parse ai_rc/v2.24.12`; `git status --porcelain` empty;
`git stash list` empty.

### Finding 20: Skip-cites-a-real-bug gate

**Category:** pass

Step 6.13. `team/scripts/lint_skip_bug_gate.sh` reports
"OK — all skip annotations are compliant" with "BUG-NNNN file-existence check:
0 missing files". No `BUG-SKIP` placeholder or `skip`-near-`BUG-<letters>`
shape exists outside archived runner logs under `team/tests/artifacts/` and one
test that deliberately contains the literal string `reason=BUG-NNNN` as its
own fixture data.

A naive grep for every `BUG-NNNN` token anywhere under `team/tests/` returns
132 ids, most of which have no file — those are synthetic fixture values inside
test bodies, not skip citations, and the project's own gate correctly scopes to
skip annotations.

### Finding 21: Constraint negative checks

**Category:** pass

Step 6.9. No credential-shaped token in any changed file. `set -euo pipefail`
present in `retire-empty-bundles.sh` (line 138, after argument parsing and
bootstrap, as its header explains). No provenance leakage: every `BUG-NNNN`
token among the RC's added lines is a synthetic fixture value inside the new
unit test, which is SOP-core's sanctioned "usage EXAMPLES where a bug-shaped
VALUE illustrates an interface" exception. `python3 team/scripts/lint_hygiene.py
--classes provenance` reports "OK — 0 findings across 8 scan director(y/ies)".

### Finding 22: Synthetic release simulation not applicable

**Category:** pass

Step 6.8. The diff touches no release-lifecycle file — not `cm-release.sh`,
`cm-open-rc.sh`, `cm-cancel-rc.sh`, `team/scripts/lib/project_paths.sh`, nor
anything in the open/close cycle. `retire-empty-bundles.sh` is a new operator
read/report tool outside that cycle. The step is skipped on its own terms; for
information, `test_cm_release_synthetic.py` ran green (17 passed) inside the
gated integration suite.

## Lint Findings (non-blocking)

- `engine_size_dupe` — `team/scripts/lib/discovery_state.sh` is 814 lines
  against an 800-line engine ceiling; this RC added 50 lines to it. Reported
  identically by both runners (`findings-unit.json` and
  `findings-integration.json`), `severity: quality`, `exit_code: 3`; neither
  runner was stopped by it. No open bug in `bugs/` described it — the closest
  matches (BUG-0350, BUG-0351, BUG-0359, BUG-0364) are all `done` and name
  other files — so one was filed:
  **filed BUG-0380-discovery-state-over-engine-ceiling (Path C-A)**.

One class, one bug, listing the single instance with its file, line count and
message. Per Step 5c this finding does not feed the recommendation, does not
raise `## Systemic Risk`, and BUG-0380 is not counted among the defects that
elevated the recommendation — Findings 12, 13 and 14 did that on their own.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=16d freshness=stale — Both conditions for fresh fail: the record is 16 days old against a 7-day window, and it was taken at v2.9.20 rather than the v2.24.12 under verification, so nothing current is known about whether this tree's images build; the red verdict it does carry is the `docker/claude-abliterated` Dockerfile `FROM` parse error and the `podman/rocky9` `COPY --chmod` step, already recorded in BUG-0167-cold-build-claude-abliterated-failure (now `done`, closed after that record was written and never re-verified since).

Read through `deep_verify_ledger_read_latest` in
`scripts/lib/deep_verify_ledger.sh`; the reader returned 0. The lane was not
run by TESTER and no verdict was re-derived. The tree ships three Dockerfiles
(`docker/claude-abliterated`, `docker/debian`, `docker/rocky9`) and
`team/scripts/deep-verify.sh` exists, so neither Step 5d gap fault applies.
Staleness raised no systemic risk and did not affect the recommendation.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. This TESTER
   task's own `status.md` recorded "Promoted from WAITING to BACKLOG by wake
   script: prerequisites satisfied" — the wake script's prerequisite
   evaluation, which is the sanctioned path.
2. **`release-state.md` edited outside the CM scripts?** NO. Its contents match
   what `CM-20260923-029-open-rc-2-24-12` reported writing (Active RC
   v2.24.12, opened 11:02:35, opened-by that task, Last Released v2.24.11
   unchanged), and that task recorded `open-rc.sh` exiting 0.
3. **Scripts patched mid-build?** NO. Every commit between `ai_main` and the RC
   tip belongs to one of the four ticketed feature branches and arrived through
   a `--no-ff` merge; the changes to `team/scripts/` are the ticketed
   deliverables themselves, not out-of-band tooling patches. No commit landed
   on `ai_main` after the RC opened at 11:02 (the last was 10:07:10, the
   v2.24.11 release).
4. **Queue files edited by hand?** NO. Every file under `tasks/queues/` carries
   an mtime coinciding with a wake cycle in the RC window (10:53–11:45); none
   shows an edit outside one.
5. **A remote git operation by an agent other than CM?** NO — answered from
   the file, not inferred. No `git-boundary-guard.jsonl` exists in the `logs/`
   directory of any of this cycle's five task folders
   (`CM-...-029`, `CODER-...-025`, `CODER-...-026`, `CODER-...-027`,
   `WRITER-...-028`). The single such file dated today belongs to
   `TESTER-20260923-022-verify-2-24-11`, a prior cycle's task.

The build ran unattended from RC open through the four ticket merges. Every
commit in the RC is agent-authored inside the 11:14–11:44 window.

## Caveats

**The ticket premise about the queue no longer holds on this install.**
CODER-027's notes describe forty-five narration-item bundles dated 2026-09-23
with the highest at v2.24.55. The requirements directory now holds 32 bundle
files in total, only two dated 2026-09-23, and the highest version present is
v2.24.12 — the run was cleared before this RC was verified. BUG-0378's own
"Operator note" instructs exactly that deletion, so this reads as an operator
acting on a filed bug rather than a bypass of the agent pipeline, and it is
not one of the five things Step 6.12 asks about. It does mean the shipped
tool's headline use case has already been handled by hand on this install;
the tool remains correct and useful for the next occurrence and for other
projects.

**BUG-0378 is only partly closed by this RC.** The operator-filed root-cause
bug (`## Status: open`) proposes three heads. Head 1 (narration off stdout)
was closed upstream before this RC; head 2 (bundle-writer payload validation)
is what this RC implements, though as a whole-bundle refusal at step level
rather than the per-line loud skip the bug sketched — the ticket acceptance
criteria specify the refusal, and the ticket is authoritative. Head 3, a mint
governor refusing to mint when a pending bundle of the same type already
exists, is not addressed anywhere in this RC. That is not a gap against this
RC's tickets, but a reader tracking BUG-0378 to closure should know head 3 is
still outstanding.

**Coverage numbers are not comparable between suites.** The integration
figures (11.45 % line, 9.17 % branch) measure Python statement coverage of a
suite that spends most of its work driving bash through subprocesses, which
the Python instrumentation cannot see. The number is low for that reason, not
because the suite is thin. Coverage did not enter the verdict either way.

**`_disc_set_status_field` adds a blank line when it rewrites a status.**
Observed while verifying `retire-empty-bundles.sh --apply`: the retired
bundle gained one blank line after the status value (296 → 297 bytes). This is
the pre-existing behaviour of the shared writer, not something the new script
does — live-install requirements files written by the discovery pipeline show
the same double blank line. Header lines themselves stayed byte-identical, so
CODER-027 AC2 is met. Recorded here rather than as a finding.

**Scratch and diagnostic output.** All under
`$PGAI_AGENT_KANBAN_TEMP_DIR` (`/tmp/pgai_kanban_tmp`): suite logs at
`tester/unit.log` and `tester/integration.log`, sandboxes at `sandbox-CIC3Yb`
and `sandbox-ZrH2ws`, and the two isolated clones used for the Finding 12
attribution at `tester/preexist-clone-2412` and `tester/rc-clone-2412`. Every
state-mutating command was run against a throwaway root with
`PGAI_TASKS_DIR` and `PGAI_PROJECT_ROOT` cleared; `--apply` was never run
against the live install.

**What could not be verified.** Whether the Finding 12 race also fires on
`ai_main` — fifteen isolated runs produced no failure, which at the observed
1-in-17 rate is consistent with either answer. The diff evidence is what
carries the attribution, not the sample. Nothing else in scope was left
unverified.
