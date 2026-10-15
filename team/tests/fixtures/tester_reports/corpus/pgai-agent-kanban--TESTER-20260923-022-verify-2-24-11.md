# Verification Report: rc/v2.24.11

## Summary

Verified `ai_rc/v2.24.11` (commit `809e226`) against
`requirements/v2.24.11-bugfix-bundle-20260923.md` — a seven-item BUG bundle
(BUG-0368, BUG-0369, BUG-0370, BUG-0371, BUG-0372, BUG-0373, BUG-0374) delivered
by six CODER tasks and two WRITER tasks. Pre-flight passed: the task worktree's
HEAD equals `ai_rc/v2.24.11`, the tree is clean, no stashes exist, and all eight
prerequisite feature branches merged with `--no-ff` and were deleted, with no
non-merge commit landing directly on the RC. Both gated runners exited 0 and both
lint findings files were empty. Every bundled item's acceptance criteria were
exercised, with behavioral probes rather than structural inspection for the three
items that add or change guard logic: the new container-engine-free gate was
triggered with a seeded violation, the widened hygiene-lint file selector was
probed on an extensionless shebang executable and on both negative cases, the
severity ranker's new `critical` rung was driven across every level, and the git
boundary guard's re-homed throwaway-root default was exercised on both its
env-set and env-unset paths and end to end through the PATH shim. Two `bug`
findings were recorded, both `low` severity and both in the same hygiene family
the RC itself set out to close; both are filed via Path C-A (filed as BUG — code
defect / regression / test failure) and neither affects runtime behavior.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Suites

| Suite | Command | Exit | Result |
|---|---|---|---|
| unit | `bash team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=0` | 4941 passed, 31 skipped in 751.73s |
| integration | `bash team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=0` | 542 passed, 1 skipped in 560.06s |

Both runners ran from the worktree root; output tee'd to
`/tmp/pgai_kanban_tmp/tester/unit.out` and `.../integration.out`. Every lint
pre-flight stage reported OK in both, including the new
`lint_container_engine_free` (357 files checked, 0 violations) and
`lint_hygiene` (0 findings across 8 scan directories). PASS evidence below is the
runners' exit codes; no direct-pytest result is quoted as suite evidence.

## Coverage (informational only)

| Suite | line (statements) | branch | blended |
|---|---|---|---|
| unit | 76.36% | 63.17% | 73.31% |
| integration | 11.45% | 9.17% | 10.93% |

Extracted from the `totals` object of `/tmp/pgai_kanban_tmp/coverage/unit.json`
and `.../integration.json` after verifying the field names
(`percent_statements_covered`, `percent_branches_covered`, `percent_covered`)
against the generated files. Coverage does not enter the verdict.

## Findings

### Finding 1: Pre-flight — RC intact, worktree clean

**Category:** pass

`git rev-parse HEAD` = `git rev-parse ai_rc/v2.24.11` = `809e226`.
`git status --porcelain` empty, `git stash list` empty. All eight prerequisite
tasks read `## State: DONE`; every named `ai_feature/*` branch is absent from the
repository, which is the normal post-merge shape and passes both pre-flight
checks trivially. `git log --first-parent --no-merges ai_main..ai_rc/v2.24.11`
is empty — every change reached the RC through a merge commit.

### Finding 2: BUG-0368 — container-launching tests relocated out of the per-RC tier

**Category:** pass

All four docker-dependent modules moved from `team/tests/unit/` to
`team/tests/cold/` (`test_dashboard_smoke_container.py`,
`test_container_shell_launcher.py`, `test_docker_build_fixture.py`,
`test_dashboard_operator_window.py`); old paths absent, new paths present, and
`run-cold-build-tests.sh` now collects `tests/cold/` as a directory rather than
naming one module. `run-unit-tests.sh` additionally `--ignore=`s
`tests/unit/test_entrypoint_shipped_image.py`, and
`lint_container_engine_free.py` excludes the same path through
`_DEFAULT_IGNORED_SUFFIXES`, so the pytest scope and the lint scope agree rather
than contradicting each other.

Acceptance criterion 1 (zero containers on a docker-present host) verified
directly: `/usr/bin/docker` and `/usr/bin/podman` are both installed on this
host; after both gated runs `docker ps -a` listed no containers at all and the
newest image on the host predates the unit run by four hours
(`pgai-agent-kanban-debian:latest`, created `2026-09-23 05:10:19`; unit run
started ~09:26). Neither runner built an image or started a container.

Acceptance criterion 3 (seeded violation) verified behaviorally — see Finding 3.
Acceptance criterion 2 (deep-verify.sh still runs the relocated tests green) was
NOT exercised: `roles/TESTER.md` Step 5d forbids TESTER from running the
cold-build lane during RC verification. It is recorded under Caveats as
unverified-by-design, not as a shortfall.

Acceptance criterion "deep-verify tier README lists the relocated tests" is met by
`docs/cm-release-reference.md`'s new "What the tier holds" table, which names all
five modules in `team/tests/cold/` and what each exercises, plus "The per-RC
gated sweep runs none of them".

### Finding 3: BUG-0368 — the new gate fires on a seeded violation (behavioral)

**Category:** pass

Step 6.5 probe, run in a `.git`-stripped copy of the worktree under
`$PGAI_AGENT_KANBAN_TEMP_DIR` so no real branch could move. Seeded
`team/tests/unit/test_probe_seeded_engine.py` containing
`subprocess.run(["docker", "build", "-t", "probe:test", "."], check=True)`:

- `python3 team/scripts/lint_container_engine_free.py` → exit 1, one violation,
  naming the file and line 8 with a runnable relocate-or-redirect remedy.
- `bash team/scripts/run-unit-tests.sh` → exit 1, aborting at the gate before
  pytest, emitting the dual-channel contract:
  `error: run-unit-tests.container-engine-free-lint` /
  `message: The container-engine-free lint found a test in the per-RC sweep set
  that invokes or probes a container engine (docker/podman); ...` /
  `remedy: Scroll up to the Cause(s) list ... relocate each named file to
  team/tests/cold/ ...`, and recording a `runner_incomplete` finding first.

The negative half holds too: the PATH-redirect exemption fixture at
`team/tests/fixtures/container_engine_free/exempt/` and the real
`team/tests/unit/test_binary_guard_contract.py` both survive a clean scan (0
violations across 357 files). The gate is declared BLOCK-WORTHY in
`docs/blocking-site-census.md` row 64 with matching Locator Index anchors for
both runners, which is the class the bug's own Fix section authorised
("blocking is fair here — runaway cost on every RC is real damage").

### Finding 4: BUG-0371 — hygiene lint file surface widened (behavioral)

**Category:** pass

Four probes in the same `.git`-stripped copy, each reverted after measurement:

| probe | expected | actual |
|---|---|---|
| `# BUG-9999 probe` appended to `team/scripts/wake/git-guard/git` (extensionless, executable, shebang) | finding naming file+line | 1 finding, `team/scripts/wake/git-guard/git:209` |
| same appended to `team/tests/fixtures/dashboard_render_fixture.py` | finding naming file+line | 1 finding, line 396 |
| extensionless NON-executable file with a shebang under `team/scripts/lib/` | not scanned | 0 findings |
| extensionless executable WITHOUT a shebang under `team/scripts/lib/` | not scanned | 0 findings |

The selector widened exactly as specified — executable AND shebang AND no suffix
— and the tree is green at rest. New unit coverage landed with it
(`team/tests/unit/test_lint_hygiene.py`, +158 lines), satisfying the bug's
"at minimum one case asserting an extensionless shebang executable is collected,
and one asserting a non-shebang data file is not". The four synthetic-corpus
subdirectories moved into `_TEST_TREE_EXCLUDE_SEGMENTS` and fourteen
`fixture_*` modules into `_FILE_WHITELIST`, replacing the old blanket `fixtures`
exclusion — the selector was widened, not renarrowed.

### Finding 5: BUG-0370 — the three shipped citations are gone

**Category:** pass

`git grep -nE 'BUG-[0-9]{4}' -- team/scripts/wake/git-guard/git` returns nothing.
`team/tests/fixtures/dashboard_render_fixture.py:121` (now line 118 region) no
longer cites a ticket. Each reworded line still carries its behavior: the shim
header still states that a git invocation made through it "completes or dies with
the process that made it", and the parent-death-guard divider still reads
"the hand-off dies with the process that made it" with its dash run intact.
`python3 team/scripts/lint_hygiene.py --classes provenance` exits 0, and
`test_git_boundary_guard.py::test_shim_body_contains_no_bare_git_invocation`
passed inside the green unit suite.

### Finding 6: BUG-0372 — `critical` ranks above `high` (behavioral)

**Category:** pass

Sourced `team/scripts/lib/discovery_defer.sh` and drove the ranker directly:

| severity | rank | decision @low | @medium | @high |
|---|---|---|---|---|
| `low` | 1 | deferred | deferred | deferred |
| `medium` | 2 | kept | deferred | deferred |
| `high` | 3 | kept | kept | deferred |
| `critical` | 4 | kept | kept | kept |
| `CRITICAL` | 4 | kept | kept | kept |
| `" critical. "` | 4 | kept | kept | kept |
| `bogus` | UNRANKED | unparseable | unparseable | unparseable |
| `""` (empty) | UNRANKED | unparseable | unparseable | unparseable |

Every clause of the bug's Acceptance holds, including that the `unparseable`
branch and its WARNING are preserved for values that genuinely are absent or
unrecognised. The vocabulary now lives in one place (`_DISC_SEVERITY_VOCAB`) and
`_disc_severity_rank` is its only reader. The deliberate divergence — the
severity scale is four rungs wide, `deferred_bug_level` is three — is enforced
in `defer_posture.sh`'s `_defer_posture_validate_level`, which still refuses
`critical` dual-channel (`defer_posture.invalid_deferred_bug_level`) naming the
accepted set, and is documented in the same commits in `team/SOP-core.md`
("Deferral Posture", new "Two vocabularies, deliberately different widths"
paragraph), `HOW_TO.md`, `kanban.cfg_example` and `project.cfg_example` —
satisfying `SOP-core.md`'s Documentation Discipline Rule.

### Finding 7: BUG-0373 — the role file's contract cross-reference is current

**Category:** pass

`grep -n "four contracts" team/roles/TESTER.md` returns nothing. Line 399 now
reads "The five contracts ..." and enumerates the subprocess-ownership contract.
`grep -rn "four contracts\|five contracts" team/roles/ docs/` shows every
surviving occurrence saying five. `governance_byte_budget` reported OK in the
unit runner's pre-flight and `docs_citation_manifest` likewise.

### Finding 8: BUG-0374 — the throwaway-root default has one home (behavioral)

**Category:** pass

`grep -n '/tmp/pgai_kanban_tmp' team/scripts/lib/git_boundary_guard.sh` returns
nothing, the four comment/code instances are gone, and neither findings file
carries a `test_anti_patterns` finding. The fix reads
`PGAI_AGENT_KANBAN_TEMP_DIR` on the hot path and lazily sources
`team/scripts/lib/temp.sh` only when it is unset, which preserves the file's
documented zero-dependency property. Both paths exercised:

- env unset → a destination under `/tmp/pgai_kanban_tmp` is fixture-local
  (exempt), a destination at `/var/lib/elsewhere.git` is not. The default is
  therefore genuinely coming from `pgai_temp_dir`, not from a second copy.
- env set to a custom root → a destination under that root is fixture-local and a
  destination under the OLD default is not, proving the guard follows the
  configured value rather than a frozen literal.

End-to-end through the PATH shim, against a throwaway repository under the temp
root: `PGAI_AGENT_ROLE=tester git push origin <branch>` was refused with
`error: git-boundary-guard.non-cm-remote-verb` / `message: role 'tester' invoked
'git push', a remote operation only CM may run` / `remedy: ask CM to run 'git
push' ...`, exit 1, and one JSON-Lines record appended to the configured evidence
file. A push to a bare repository under the temp root by the same role completed
normally, exit 0, and added no record. Both halves of the destination exemption
behave as the bug requires.

### Finding 9: BUG-0369 — lock narration rate-limited and event band folds

**Category:** pass

`lock_provenance.sh` memoizes both `lock.provenance.contended` and
`lock.stale_threshold.floor_enforced` to at most once per lock (per derivation
for the clamp) per wake-firing process, and exposes
`pgai_lock_contention_probe_count` so `wake_driver.sh` and `wake_common.sh` can
still name the total probe count in their own single log lines — the closing
summary the bug asked for ("still contended after retry (N probe(s) this
firing), deferring to the next wake firing"). `event-band.sh` folds a narration's
`error:`/`message:`/`remedy:` triple into one row keyed by the narration name
with the distinct agent set appended, and leaves every other dual-channel block
rendering its three lines. The 30-probe-across-4-agents fixture, the
combined-key fixture (contended and clamp fold to their own rows, never merged),
the not-folded control, the once-per-process clamp case and the
absent-when-config-exceeds-the-floor case are all present as new tests and all
pass inside the green unit suite (re-run standalone as a debugging aid: 15
passed).

Part B (window layout) needed no change on this RC and was verified as still
correct rather than taken on trust: `team/config/dashboard_windows.census`
declares window 4 `schedule` with exactly three panes — one roster band, one
status band, one event band — so there is no duplicate event panel; the roster
pane is declared "floored to fit the full cron roster" and the status pane
"floored to one row per roster agent plus its header";
`test_schedule_window_bands_acceptance.py::TestDefaultGeometryShowsFullRosterInBothBands`
asserts all seven roster entries render at default geometry with no overflow
marker and one status row per roster agent, and both cases passed. The
`dashboard_window_census` and `doc_command_truth` lint stages reported OK.

### Finding 10: This RC added two new internal bug-ID citations to shipped surfaces

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same defect class shipped in the previous RC as BUG-0370 and recurred here through two surfaces the widened lint still does not read)
**Filed As:** BUG-0376-new-provenance-citations-unscanned-surfaces (Path C-A — filed as BUG — code defect / regression / test failure)

While closing BUG-0370 (three shipped ticket citations) and BUG-0371 (the lint
gap that hid them), this RC introduced two more citations of the same shape:

| file | line | text |
|---|---|---|
| `docs/blocking-site-census.md` | 162 | `... invokes or probes a container engine (docker/podman) -- BUG-0368: runaway build/container cost on every RC. ...` |
| `team/tests/fixtures/container_engine_free/violation/test_seeded_container_invocation.py` | 4 | `Fixture: a deliberate BUG-0368-shaped violation for` |

`SOP-core.md` ("Naming: describe behavior, not scaffolding or provenance") states
the rule: no internal bug identifier may reach a shipped artifact. None of the
three sanctioned exceptions covers either line — in both, `BUG-0368` names the
ticket that caused the work rather than a bug-shaped VALUE illustrating an
interface. Both surfaces ship: `blocking-site-census.md` is line 63 of
`team/config/docs-manifest.txt` and is installed by `install.sh`'s docs loop, and
the fixture is part of the shipped tree and read as text by
`test_lint_container_engine_free.py`.

Neither is visible to the provenance class: `docs/` is on no hygiene scan surface
(the prose surface defaults to `release-notes/` only), and `container_engine_free`
was added to `_TEST_TREE_EXCLUDE_SEGMENTS` by this same RC. The exclusion is
reasonable for corpus BODIES but also silences the module's own human-written
docstring. The census row is an outlier in its own document — it is the only one
of 64 rows naming a ticket.

Nothing is functionally broken: the row still classifies its gate correctly, the
fixture still plays its part, and no runtime path reads either string. Hence
`low` severity on `SOP-core.md`'s rubric, with `medium` systemic risk recorded
because this is the second consecutive RC in which the class shipped.

### Finding 11: Nine legacy provenance citations in the newly scanned fixtures directory were neither fixed nor recorded

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one lint class's matcher and nine fixture lines)
**Filed As:** BUG-0377-provenance-class-blind-to-string-literals (Path C-A — filed as BUG)

BUG-0371's "Note for whoever takes this" was explicit that widening the surface
to `team/tests/fixtures/` would expose pre-existing `BUG-0112` citations and that
they must be "either fix[ed] ... in the same change or record[ed] ... in the
lint's own sidecar allowlist with a justification". The selector did widen
(Finding 4 proves it), and CODER-20260923-020's sweep reworded the eight
citations the class reports. Nine survive, untouched and unrecorded:

`dashboard_render_fixture.py:281,292,296,341`, `git_network_stub.py:154`,
`installed_root.py:146`, `log_stub.py:153`, `two_project.py:125,231,247`.

Every one sits inside a Python string literal the fixture writes verbatim into a
generated file on disk — `git_network_stub.py:154` is the first comment line of a
`git` shim the fixture drops on `PATH`. The `provenance` class matches a ticket
identifier only in the scanning module's own comments and docstrings, never in
string-literal content. Isolating probe pair, same file and same surface:
appending `# BUG-9998 plain comment probe` to `dashboard_render_fixture.py`
produces one finding; appending
`PROBE = ("# kanban.cfg — BUG-9999 render-path fixture\n")` produces none.

The alternative disposition was not taken either:
`team/scripts/hygiene-lint-allowlist.json` is byte-identical between `ai_main`
and the RC (12 entries, none for `team/tests/fixtures/`). So BUG-0371's
instruction was honoured for eight lines and silently skipped for nine — not by a
decision but because the matcher never surfaced them for one to be made. Nothing
is broken at runtime; these strings only ever become comments in throwaway
fixture trees. `low` severity.

### Finding 12: Autonomous operation criterion

**Category:** pass

See the dedicated section below. All five checks answer NO.

### Finding 13: Release-state, version ordering and prerequisite graph

**Category:** pass

`release-state.md` holds all four fields — `## Active RC` `v2.24.11`,
`## RC Opened At` `2026-09-23T04:32:26+00:00`, `## RC Opened By Task`
`CM-20260923-021-open-rc-2-24-11`, `## Last Released` `v2.24.10` (well-formed).
`pp_last_released_version "pgai-agent-kanban"` returns `v2.24.10`, and
`semver_gt "v2.24.11" "v2.24.10"` succeeds — the version helper, never an inline
compare. The file's mtime is `2026-09-23 04:32:26`, byte-identical to its own
`## RC Opened At` value, which is the signature of `cm-open-rc.sh` having written
it and nothing having touched it since.

Prerequisite graph: eleven tasks, acyclic, every referenced task ID resolves to an
existing folder, bookends correct (`CM-20260923-021-open-rc-2-24-11` at the head
with no prerequisites; `TESTER-20260923-022-verify-2-24-11` depending on all eight
build tasks; `CM-20260923-024-release-2-24-11` depending on those eight plus
TESTER). Sequence numbers are consistent and non-colliding.

### Finding 14: Constraint negative checks and skip-gate

**Category:** pass

No hardcoded secret shapes in the RC diff. `set -euo pipefail` present in every
modified executable script (`run-unit-tests.sh`, `run-integration-tests.sh`,
`run-cold-build-tests.sh`, `event-band.sh`); correctly absent from the six
modified `lib/` files, which are sourced libraries. Step 6.13: no `BUG-SKIP`
placeholder shape anywhere under `team/tests/`, and every `BUG-NNNN` string near
a skip is a fixture literal inside `test_skip_bug_citation_scan.py`'s own corpus
— the sanctioned usage-example exception. The `skip_cites_real_bug` lint stage
reported OK in the unit runner. Step 6.14: the one new script,
`lint_container_engine_free.py`, has a `--help` naming every argument
(`--sweep-dir`, `--verbose`) with defaults stated, and `help_presence` reported
OK.

### Finding 15: Step 6.8 — synthetic release simulation

**Category:** pass

The RC diff touches no release-lifecycle script — `cm-release.sh`,
`cm-open-rc.sh`, `cm-cancel-rc.sh` and `team/scripts/lib/project_paths.sh` are all
unchanged; the only matching path in the diff is the prose file
`docs/cm-release-reference.md`. The step is therefore not triggered.
`team/tests/integration/test_cm_release_synthetic.py` nonetheless ran as part of
the green integration suite (17 tests, all passing) and is recorded here as
confirmation rather than as a triggered obligation.

## Lint Findings (non-blocking)

None. Both gated runners wrote an empty findings array on the verification run:
the unit runner reported `findings file written: /tmp/pgai_kanban_tmp/lint/findings-unit.json (0 finding(s))`
and the integration runner the same for `findings-integration.json`. No
quality-class lint fired, so no aggregate bug was owed or filed for this section.

Note for a reader inspecting the files after the fact: `findings-unit.json` on
disk now carries a single `runner_incomplete` entry generated at 09:51:26 by the
Finding 3 seeded-violation probe, which ran the runner inside a throwaway copy
but writes its findings to the shared `$PGAI_AGENT_KANBAN_TEMP_DIR/lint/` path.
That entry is TESTER's own probe artifact, not a result of the verification run —
the verification run's own 0-finding line is in
`/tmp/pgai_kanban_tmp/tester/unit.out`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=16d freshness=stale — Stale on both counts: the record is 16 days old against a 7-day window and was taken at v2.9.20 rather than the v2.24.11 under verification; its `failed-to-build` verdict (docker and podman `claude-abliterated`, podman `rocky9`) was filed at the time as BUG-0167-cold-build-claude-abliterated-failure, which is now `## Status: done`, so nothing is owed here beyond noting that no container image on this tree has been proven to build since v2.9.20.

## Autonomous criterion

Autonomous operation criterion: Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. Every queue
   marker matches its task's own `## State`; the six CODER and two WRITER entries
   read `[x]`, `CM-20260923-024-release-2-24-11` reads `[W]` with unmet
   prerequisites, and this task reads `[A]` while running.
2. **`release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`?** NO. Its mtime (`2026-09-23 04:32:26.435`) equals its own
   `## RC Opened At` field to the second, and `## RC Opened By Task` names
   `CM-20260923-021-open-rc-2-24-11`.
3. **Scripts patched mid-build?** NO. Every commit under `team/scripts/` is on a
   ticketed feature branch that reached the RC through a `--no-ff` merge;
   `git log --first-parent --no-merges ai_main..ai_rc/v2.24.11` is empty. The
   three small follow-up commits inside CODER-20260923-018's branch
   (`368c438`, `c07c4ed`, `f28d516`) are that agent's own in-session corrections
   before its merge, which is ordinary CODER work, not an out-of-band patch.
4. **Queue files edited by hand?** NO. Marker states are consistent with task
   states throughout, and every bundled bug is `[x]` in `bug_backlog.md` with a
   matching `## Status: running` in its own file.
5. **Remote git operation by a non-CM agent?** NO for the build. No task folder
   for any of `CODER-20260923-013..020`, `WRITER-20260923-017/019` or
   `CM-20260923-021` holds a `git-boundary-guard.jsonl`. One record does exist in
   THIS task's own `logs/git-boundary-guard.jsonl`, timestamped
   `2026-09-23T09:52:59Z` — it is TESTER's own deliberate Step 6.5 guard probe
   (Finding 8), a `git push origin` against a throwaway repository whose remote
   points at `https://example.invalid/`, which the guard correctly refused before
   any network contact. It is disclosed here rather than omitted, and it is not a
   build intervention.

## Caveats

- **The deep-verify cold-build lane was not run**, by design: `roles/TESTER.md`
  Step 5d forbids TESTER from starting a container engine during RC
  verification. BUG-0368's acceptance clause "deep-verify.sh still runs the
  relocated tests green" is therefore unverified by this report and belongs to
  CM's DEEP-VERIFY tier. The relocation's static shape was verified (the four
  modules are present under `team/tests/cold/`, the lane collects the directory,
  and each relocated module retains its own engine-absence guard), but no
  execution evidence exists. Given the ledger is 16 days and fourteen minor
  versions stale, a deep-verify run before or shortly after this release would be
  worth scheduling.
- **`BUG-0375-hygiene-lint-provenance` is open but already satisfied.** It was
  filed autonomously by the hygiene-lint aggregate filer mid-RC at commit
  `47c1a2b`, listing 13 provenance instances; CODER-20260923-020's sweep cleared
  every one of them, and the class now reports 0 findings on the RC head. Because
  it was filed after the bundle was assembled it is not in this RC's
  `## Bundled Items`, so `cm-release.sh` step 16b will not close it, and it will
  re-enter discovery as an open bug describing work already done. Flagged as
  state drift under Step 6.11; TESTER does not triage, so nothing was changed.
- **`event-band.sh --help` prints `!/usr/bin/env bash` as its first line.** The
  help path extracts `grep '^#' "$0" | sed 's/^# \{0,1\}//'`, which necessarily
  includes the shebang. This is pre-existing — the identical extraction is on
  `ai_main`; this RC changed only the slice length from 58 to 70 lines. The new
  length is exactly right (69 header comment lines plus the shebang), so the
  header is no longer truncated. Recorded as an observation, not a finding:
  cosmetic, and not introduced by this RC. Worth noting for a future ticket that
  the slice length is a hand-maintained magic number which had to be bumped
  mid-task once already on this RC.
- **Test fidelity.** The behavioral probes in Findings 3, 4, 6 and 8 were run
  against production shapes — the real gated runner, the real extensionless shim
  file, the real `discovery_defer.sh` functions, and the real PATH shim against a
  real git repository — rather than mocks, so no mocked-away condition is
  suspected in the RC's own new coverage. One observation without a concrete
  defect attached: the new `lint_container_engine_free.py` decides reachability
  statically from the AST ("a call structurally proven unreachable by an earlier
  PATH redirect in the same test function"), so a test that redirects PATH in a
  fixture or a helper rather than in the test function body would be reported
  even though it cannot reach an engine. No such test exists in the tree today
  (0 violations across 357 files), so this is noted here rather than filed.
- **Live-install safety.** Every mutating probe ran either inside a
  `.git`-stripped copy of the worktree or inside a throwaway root under
  `$PGAI_AGENT_KANBAN_TEMP_DIR`. The verification worktree itself was left
  untouched: `git status --porcelain` is empty and HEAD is still `809e226`.
