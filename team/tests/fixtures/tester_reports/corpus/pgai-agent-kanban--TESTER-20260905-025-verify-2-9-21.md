# Verification Report: ai_rc/v2.9.21

## Summary

Verified RC `ai_rc/v2.9.21` (commit `8446e763`) against
`requirements/v2.9.21-bugfix-bundle-20260905.md`, a single-item bundle whose
only content is BUG-0162 — five unit tests that read the operator's live kanban
bug ledger for assertion data and were therefore red on every release. Three
tickets landed: a committed fixture bug ledger with both unit modules repointed
at it (CODER-021), the integration module repointed plus an Anti-pattern 2 lint
extension that detects live-kanban-state coupling (CODER-022), and the matching
SOP prose (WRITER-023). Both gated runners exit 0 — unit 3063 passed / 3
skipped, integration 299 passed / 1 skipped — and every acceptance criterion of
the three tickets was executed, including the behavioural ones: the new lint
guard fires on a reintroduced live-root path naming file, line and remedy;
its per-line opt-out marker suppresses correctly; deleting the fixture's
`## Public ID` line reds four of the five tests, proving the assertions are
load-bearing; and the two modules pass with `PGAI_AGENT_KANBAN_ROOT_PATH`
pointed at a directory containing no `projects/` tree at all.

The RC nevertheless carries a serious defect that the green suite conceals.
CODER-021 also regenerated `CHANGELOG.md` from the test fixture ledger and
committed it. Three things follow, all verified rather than inferred. The
changelog-freshness gate, which resolves the project's real ledger, went from
**exit 0 on the base tree to exit 1 on the RC** — a clean green-to-red
regression. The five public Known Issues disclosures for v1.0.0 were replaced
with text that does not describe the defects those identifiers were published
against; four of the five are unrelated to the previously published wording.
And the fix does not survive its own release: `cm-release` Step 11b re-renders
`CHANGELOG.md` from the real ledger and commits it, and with the file in that
state five unit tests fail again — the same shape BUG-0162 was filed to end.
Separately, verification found that the unit suite silently edits a tracked
file in the tree under test on every run (pre-existing, reproduced on the base
tree), that the shipped v2.9.20 release notes carry an internal task ID, and
that three container images ship with no cold-build lane anywhere in the tree.

## Recommendation

SHIP-WITH-SERIOUS-CONCERNS

## Systemic Risk

high

## Findings

### Finding 1: CHANGELOG.md regenerated from the test fixture ledger — freshness gate regressed green to red, published Known Issues replaced with unrelated text, and the BUG-0162 fix reverts at release time

**Category:** bug
**Fix Effort:** medium (3-5 CODER tasks)
**Systemic Risk:** high (this RC introduces a regression: a gate that passed on the last shipped tree fails on this one, and a previously published user-facing artifact now says something different under the same identifiers)
**Regression:** yes (`lint_changelog_freshness` exits 0 against `b6c2fa3` and 1 against `8446e763`; the divergent region is the five KI lines this RC added)
**Integrity Damage:** no (CHANGELOG.md is fully regenerable by the writer; no queue, lockfile, or task state is touched)
**Filed As:** BUG-0163 (Path C-A — filed as BUG: code defect / regression / test failure)

Three verified consequences of one change (commit `3fd4b6a`, merged at
`85c5b72`):

**1a. Green-to-red on the changelog-freshness gate.** The gate resolves the
project's live bug ledger by default (`lint_changelog_freshness.py:248`), while
the committed artifact is now a render of `team/tests/fixtures/bug_ledger/`.
The two cannot agree.

```
# base tree exported from b6c2fa3
python3 team/scripts/lint_changelog_freshness.py --changelog <base>/CHANGELOG.md
  -> lint_changelog_freshness: ok — CHANGELOG.md is fresh.      exit 0

# RC worktree at 8446e763
python3 team/scripts/lint_changelog_freshness.py --changelog <rc>/CHANGELOG.md
  -> lint_changelog_freshness: FAIL — CHANGELOG.md is stale     exit 1
```

Re-checked after this run's own bug filings landed in the ledger; the base is
still green, so the filings are not what moved it.

**1b. Published Known Issues text replaced.** Previously published (reachable
at `c2d8056`), and now committed on the RC:

```
was: KI-1.0.0.3 — Discovery/PM re-activates a requirements bundle that the
     operator has terminally closed, overwriting its terminal state.
now: KI-1.0.0.3 — A task title containing non-ASCII characters produced a
     task-folder slug that collided with an unrelated task
```

All five lines were rewritten this way; only KI-1.0.0.1 is a recognisable
paraphrase of the original. The source is five fixture records authored for
this ticket, carrying `**Filed By:** operator` and `**Date:** 2026-07-02`
headers, which the repointed module's docstring describes as "real historical
bug records rather than synthetic placeholder content."

**1c. The fix does not survive the release step.** `cm-release` Step 11b
(`team/scripts/lib/cm/release_notes.sh:562-610`) renders `CHANGELOG.md` from
the project's real bugs directory, byte-compares, installs the fresh render
when it differs, and commits it. OVERWATCH's changelog-drift auto-fix does the
same on the main branch. Simulated directly in a scratch copy of the RC tree:

```
PYTHONHASHSEED=0 bash team/scripts/lib/pp_run_ops.sh \
  pgai_agent_kanban.cm.changelog_writer "<scratch>" \
  "$HOME/pgai_agent_kanban/projects/pgai-agent-kanban/bugs" > CHANGELOG.md
cd team && python3 -m pytest -q tests/unit/test_lint_changelog_freshness.py \
  tests/unit/test_changelog_writer_real_data.py \
  tests/integration/test_changelog_freshness_gate_cli_recovery.py
-> 5 failed, 29 passed
```

The failures are `test_fresh_artifact_prints_ok_message`,
`test_fresh_artifact_exits_0`, `test_ki_1_0_0_1_renders_under_v1_0_0`,
`test_rc_mode_tolerates_new_release_section_addition`, and
`test_rc_mode_tolerates_post_rc_bug_entry`. The coupling to state outside the
tree was moved one hop rather than removed: the suite no longer reads the live
ledger, but it compares against a `CHANGELOG.md` that the release pipeline
regenerates from that same live ledger.

### Finding 2: the unit suite silently edits a tracked file in the tree under test

**Category:** pre-existing-bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect any agent can hit — it dirties a worktree with no failing test to explain it — but the behaviour is identical on the base tree, so shipping this RC does not make it worse)
**Regression:** no (reproduced on `b6c2fa3`, the tip at v2.9.20; the test file `team/tests/unit/test_lint_comment_provenance.py` is untouched by this RC's diff)
**Integrity Damage:** no (a working-tree modification to a tracked file, fully recoverable with `git checkout --`)
**Filed As:** BUG-0164 (Path C-A)

A clean worktree becomes dirty across a unit run:

```
$ git status --short                     # before: empty
$ bash team/scripts/run-unit-tests.sh --coverage
  ================= 3063 passed, 3 skipped in 330.56s =================
$ git status --short
 M release-notes/v2.9.20.md
$ git diff -- release-notes/v2.9.20.md
+# provenance-allowlist: retained intentionally as a deliberate internal identifier reference
```

The inserted line is verbatim the first numbered command of
`lint_comment_provenance`'s own remedy block. The remedy-block test near line
690 of `test_lint_comment_provenance.py` parses the numbered commands out of
captured stdout and runs each with `subprocess.run(command_text, shell=True)`
— deliberately, to prove the printed text is runnable. The defect is that the
lint call it captures from passes only `--code-dirs` (a scratch fixture) and
leaves `--prose-dirs` at its default, which is the repository's own
`release-notes/`. So a real violation's remedy is captured alongside the
fixture's and executed against the real file.

Isolated to the single test and confirmed pre-existing:

```
git archive b6c2fa3 | tar -x -C <base>
md5sum <base>/release-notes/v2.9.20.md
cd <base>/team && python3 -m pytest -q tests/unit/test_lint_comment_provenance.py -k remedy
  -> 2 passed, 23 deselected
md5sum <base>/release-notes/v2.9.20.md    # changed
```

Second-order effect worth noting: because the unit run inserts the allowlist
line, the integration runner's own `comment_provenance` pre-flight then reports
OK on the same tree. The two runners disagree purely on run order. This is also
what CODER-022 reported in its status Summary, having reverted the mutation by
hand twice before committing.

### Finding 3: shipped release notes carry an internal task identifier

**Category:** pre-existing-bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (hygiene; the lint is quality-class and non-fatal, and the reference steers nobody into a wrong action)
**Regression:** no (`release-notes/v2.9.20.md` entered the tree at `4753c59`, before this RC branched, and is untouched by this RC's diff)
**Integrity Damage:** no
**Filed As:** BUG-0165 (Path C-A)

One instance of the `comment_provenance` lint class, filed as one bug per the
one-bug-per-class-per-run rule:

- `release-notes/v2.9.20.md:33` — `[task-ID] provenance reference
  'TESTER-20260905-018' in code — comments and output must describe behavior,
  not internal IDs`

The line reads `(See TESTER report for filed bug IDs:
TESTER-20260905-018-verify-2-9-20)`. A user holding the release cannot open a
TESTER report, so the reference has no reader value outside the kanban. Note
for the fixer: do not apply the lint's own `sed -i` remedy while Finding 2 is
open — the unit suite applies that same command to that same line.

### Finding 4: three container images ship with no cold-build lane

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks — port four scripts and wire the ledger)
**Systemic Risk:** medium (a real coverage gap: nothing in the release path ever builds the shipped images. Nothing in the loop reads those images, and they were equally unbuilt the day before this RC, so shipping worsens nothing)
**Regression:** no (absent from `ai_main` before this RC branched; this RC's diff touches no container or lane file)
**Integrity Damage:** no
**Filed As:** BUG-0166 (Path C-A)

The tree ships `docker/claude-abliterated/Dockerfile`,
`docker/debian/Dockerfile`, and `docker/rocky9/Dockerfile`. Searching the whole
worktree for a lane:

```
$ find . -name '*deep*verify*' -o -name '*cold-build*'
(no output)
```

None of `deep-verify.sh`, `cold-build-now.sh`, `deep-verify-freshness-check.sh`
or `run-cold-build-tests.sh` exists on `ai_rc/v2.9.21` or on `ai_main`, and the
project has no deep-verify ledger, so the reader answers `found=false`. The
four scripts do exist at `bba1238` (`Release v2.9.19`, reachable from
`last_run_ai_main`), which is **not** an ancestor of `ai_main` — the current
ai_main lineage is a shorter one that does not carry them. They can be ported
rather than rewritten. Whether the lineage difference was intended is an
operator question and is not asserted here; see Caveats.

### Finding 5: BUG-0162 provenance citations added to test comments

**Category:** pass-with-caveat

Four added lines in `team/tests/` cite `BUG-0162` in docstrings and comments
(`test_changelog_writer_real_data.py` x2, `test_lint_changelog_freshness.py`
x2). The comment-provenance boundary applies inside tests, but the lint scans
only `team/scripts`, `team/scripts/lib`, `team/scripts/cm` and
`team/pgai_agent_kanban`, so nothing enforces it here. Pre-existing density in
those same two files is 8 and 14 `BUG-0` references respectively, so this is
consistent with the files as they stood rather than a new habit. Recorded as a
caveat, not filed — folding it into a filing would be noise against that
background.

### Finding 6: pre-flight, structural and acceptance checks

**Category:** pass

- **Pre-flight merge verification** — every DONE task's feature branch is
  already deleted (normal post-merge cleanup), so Check 1 and Check 2 are
  skipped per the lifecycle note. No anomalies.
- **Worktree identity** — `git rev-parse HEAD` equals `git rev-parse
  ai_rc/v2.9.21` (`8446e763`); `git status --short` empty at pre-flight;
  `git stash list` empty.
- **release-state.md** — `Active RC: v2.9.21` with `ai_rc/v2.9.21` present;
  `pp_last_released_version` returns `v2.9.20`; `semver_gt v2.9.21 v2.9.20`
  true; `## Last Released` is a well-formed `v2.9.20`. No drift.
- **CODER-021 acceptance** — no live-root assertion path remains in either
  module (only comments naming the variable); `PGAI_AGENT_KANBAN_ROOT_PATH`
  pointed at an empty directory gives `31 passed, 1 skipped`, exit 0; the five
  named tests all run and pass, none skipped; deleting the fixture's
  `## Public ID` line reds four of the five (`4 failed, 1 passed`), so the
  assertions are load-bearing; `run-unit-tests.sh` exits 0.
- **CODER-022 acceptance** — `lint_test_anti_patterns.py` exits 0 (0 findings
  across 341 files); reintroducing the old `_LIVE_KANBAN_ROOT` /
  `_REAL_BUGS_DIR` construct into a real test file makes it exit 1 and print a
  finding naming the file, the line and the remedy; a preceding-line
  `# anti-pattern-allowlist: 2 (justification: ...)` marker suppresses it back
  to exit 0 (`_OPT_OUT_WINDOW = 5`; the marker must precede the flagged line —
  a trailing same-line comment does not suppress, which matches the lint's
  existing convention for every other anti-pattern); `lint_orphan_tests.py`
  exits 0; `test_lint_live_kanban_bugs_dir_coupling.py` gives 4 passed;
  `test_changelog_freshness_gate_cli_recovery.py` resolves the fixture ledger.
- **WRITER-023 acceptance** — `team/SOP.md` Anti-pattern 2 names the
  `bugs/`, `priority/`, `requirements/`, `tasks/` state under
  `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/`; carries a BAD block
  resolving a live kanban bugs dir and a BETTER block resolving
  `team/tests/fixtures/`; the "When acceptable" paragraph states the
  opt-in-and-skip requirement and names `# anti-pattern-allowlist: 2
  (justification: ...)`; the six anti-pattern headings are byte-identical to
  the base. One WRITER acceptance criterion — `doc-lint.sh` exits 0 — is not
  met, but the failing suite is `feature-greps` on the required token `ubi9`,
  which fails identically on the base tree; `cmd`, `links` and `fossil-greps`
  all exit 0. Pre-existing and correctly disclosed in the WRITER status.
- **Bundle acceptance** — all Python compiles and all bash passes syntax
  check: the breakage-class gate reports `291 python file(s), 218 shell
  file(s) checked, 0 breakage-class failures`.
- **Skip-cites-real-bug (Step 6.13)** — no placeholder skip IDs in live test
  code (the only grep hits are inside archived run logs under
  `team/tests/artifacts/`), and every cited `BUG-NNNN` resolves to a file. The
  gated runner's own `lint_skip_bug_gate` agrees: `0 missing files`.
- **Constraint negatives (Step 6.9)** — no hardcoded secret, key, token or
  password added by the diff.
- **Coding standards (Step 6.14)** — no operational value hardcoded, no
  duplicated `lib/` implementation, no `--help` owed (no new script); the only
  hit is Finding 5.
- **Prereq graph (Step 6.10)** — CM-024 -> CODER-021 -> CODER-022 ->
  WRITER-023 -> TESTER-025 -> CM-027. No cycles, all referenced IDs exist as
  task folders, bookends present, sequence numbers non-colliding.
- **Synthetic release simulation (Step 6.8)** — skipped with note: the diff
  touches no release-lifecycle file (`cm-release`, `cm-open-rc`,
  `cm-cancel-rc`, `project_paths.sh`, `team/scripts/cm/`). The only
  `team/scripts/` file in the diff is `lint_test_anti_patterns.py`, which is a
  gated-runner pre-flight, not part of the open/close cycle.

### Finding 7: gated runner verdicts

**Category:** pass

```
bash team/scripts/run-unit-tests.sh --coverage         -> UNIT_EXIT=0
  3063 passed, 3 skipped in 330.56s
bash team/scripts/run-integration-tests.sh --coverage  -> INTEGRATION_EXIT=0
  299 passed, 1 skipped in 219.47s
```

Both PASS verdicts come from the gated runners' exit codes, not from a direct
`pytest` line. Direct `pytest` was used only to isolate individual behaviours
after the runners had already reported.

## Code Coverage

- Unit:        line 74%  |  branch 60%   (10100/13622 stmts, 2522/4178 branches)
- Integration: line 8%   |  branch 6%    (1070/13622 stmts, 259/4178 branches)

Read from the `totals` object of
`${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and `integration.json` after
confirming the field names against the generated files. The blended `TOTAL`
figure both runners print is 71% (unit) and 7% (integration); it is not the
line number and is not what is recorded above. Informational only — coverage
does not enter the recommendation.

## Lint Findings (non-blocking)

- `changelog_freshness` — `CHANGELOG.md is stale`: the committed artifact
  differs from a fresh regeneration by the five v1.0.0 KI lines this RC added,
  and RC-mode tolerance does not admit the diff — filed BUG-0163 (Path C-A).
  This one is **not** merely a lint signal: it is the observable face of
  Finding 1, which is categorised as a `bug` and does move the recommendation.
- `comment_provenance` — one instance, `release-notes/v2.9.20.md:33`:
  `[task-ID] provenance reference 'TESTER-20260905-018' in code` — filed
  BUG-0165 (Path C-A).

Both classes appear in `findings-unit.json` (2 findings). The integration
findings file carries 1 finding, the same `changelog_freshness` class;
`comment_provenance` reports OK there only because the unit run had already
mutated the file — see Finding 2.

## Deep-Verify Freshness

deep-verify: found=false — this project has never been deep-verified; no lane result exists, so nothing whatever is known about whether its three shipped container images build.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. Every
   promotion is in the wake logs: CODER-021 at 14:51:14, CODER-022 at 15:19:48,
   WRITER-023 at 15:44:38, TESTER-025 at 15:48:50, each reading `prerequisites
   satisfied, promoting to BACKLOG`.
2. **`release-state.md` edited outside the CM scripts?** NO. Its four fields
   match what CM-024's status records `cm-open-rc.sh` as having written.
3. **Scripts patched mid-build?** NO. `team/scripts/lint_test_anti_patterns.py`
   changed during the RC, but it is CODER-022's own ticketed deliverable, not
   an out-of-band patch.
4. **Queue files edited by hand?** NO. Every marker matches its task's state:
   `[x]` on the five DONE tasks, `[A]` on this task, `[W]` on
   CM-20260905-027-release-2-9-21.

One agent-level self-service action is worth recording even though it is not a
human intervention: CODER-022 reported reverting an unrequested edit to
`release-notes/v2.9.20.md` with `git checkout --` twice, because the unit suite
kept re-creating it. The revert was correct and the edit did not land in any
commit. The underlying defect is Finding 2, filed as BUG-0164.

## Caveats

- **The green suite conceals Finding 1.** Both runners exit 0, and the
  changelog-freshness failure is quality-class and non-fatal, which is why it
  passed unnoticed through authoring. A reader who takes `UNIT_EXIT=0` as the
  whole verdict for this RC will draw the wrong conclusion. That is the
  specific reason this report does not sit at PASS despite two green runners.
- **Finding 1's third consequence is a prediction with a direct experiment
  behind it, not an inference.** The release step was not run. What was run is
  the exact regeneration command Step 11b issues, against the same ledger,
  followed by the suite. The prediction is that the release step produces that
  state; the failure that follows from that state is measured.
- **Repository lineage.** `bba1238` (`Release v2.9.19`) is not an ancestor of
  `ai_main`, and `ai_main` is missing 231 paths present there — 130 under
  `team/tests/`, plus release notes for v2.4.1 through v2.9.19, five ICD
  baselines, and the four deep-verify scripts. The richer lineage is preserved
  on a branch named `last_run_ai_main`, and the installed kanban root reports
  `ai_v2.9.19-1-g228f078`, matching its tip. The branch name reads as a
  deliberate operator snapshot rather than corruption, so this is recorded as
  an observation and nothing about it is filed. Only its one spec-traceable
  consequence — images shipping with no lane — is filed, as BUG-0166.
- **Something creates an empty commit in every agent worktree.** The WRITER
  feature branch carries a commit titled `initial empty commit` (`76f96ec`)
  with an empty diff, which was merged into the RC. During this verification an
  identical commit (`04b2674`, same title, same empty diff, timestamped about
  three minutes into the session) appeared on this task's own detached HEAD
  without TESTER creating it — which identifies it as harness behaviour rather
  than something either agent authored. No string matching `initial empty
  commit` exists in the wake scripts or in `team/scripts/`. Impact on this RC
  is nil and was checked rather than assumed: `git rev-parse
  8446e76^{tree}` and `git rev-parse 04b2674^{tree}` are the same tree, the
  `ai_rc/v2.9.21` branch ref still points at `8446e76`, and the WRITER merge's
  net effect on the RC is exactly the 17-line `team/SOP.md` edit. Recorded as
  an environment observation, not filed: it changes no content, and the commit
  on this task's HEAD is detached so it reaches no branch. It is worth an
  operator glance because it is noise in the RC's history and because the title
  reads alarming in `git log --graph`.
- **Verification's own footprint.** The unit run dirtied the worktree via
  Finding 2; `release-notes/v2.9.20.md` was restored with `git checkout --` and
  the worktree left clean. All scratch work (base-tree export, scratch copies,
  mutation and injection experiments) was done on copies under
  `$PGAI_AGENT_KANBAN_TEMP_DIR`, never on the RC worktree, and no
  state-mutating script was pointed at the live kanban root.
- **Fix-order note for the next cycle.** BUG-0163 and BUG-0165 both touch
  files the suite regenerates or rewrites; BUG-0164 should land first so the
  suite stops editing the tree while the other two are being fixed.
