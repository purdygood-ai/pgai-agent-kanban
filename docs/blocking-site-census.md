# Blocking-Site Census

## Purpose

This census inventories every place in the shipped pgai-agent-kanban code that
can **halt, refuse, or block the loop**, and classifies each site against
**The Loop** doctrine (`team/SOP-core.md`, "The Loop"). It is the standing reference
that future gates are measured against: every new refusal added to the tree
must declare which class below it belongs to.

It is the input contract for two downstream consumers: the
CONVERT conversions (which turn every `CONVERT`-class site into
warn-file-continue) and the census lint (which checks that every gate declares
its class and carries the dual-channel error contract).

This is a tracking/audit document, not code. It records what each site's
blocking behavior *should* be under the doctrine; it does not assert that every
`CONVERT` row has already been converted at every call site.

## Doctrine anchor

From `team/SOP-core.md` ("The Loop"): the kanban is a looped system — human input in,
verified output out, and the loop keeps turning on new human input or on a bug
the verifier files. **Bugs heal**; the next iteration is the default repair
path. **Quality findings never stop the loop** — they carry zero risk of
runtime failure and are noted and carried forward. **Blocking is the rare
exception**, reserved for work that moves the system *backwards*: destructive
failures, unusable builds, or verification unable to run at all.

## Classification vocabulary

Every row below carries exactly one of three classes.

- **BLOCK-WORTHY** — The refusal is justified: the condition it catches moves
  the system backwards. It fits one of the doctrine's backward-motion
  categories — destructive/state-corrupting failure, breakage-class (no
  trustworthy pass signal / unusable build), verification unable to run, or
  approval-/invariant-by-design (an operator stop signal or a release-integrity
  invariant whose removal would let the loop corrupt itself). Every
  BLOCK-WORTHY row carries a one-sentence doctrine-tied justification.
- **CONVERT** — A quality-class finding that currently exits fatally but
  carries zero runtime-failure risk. Under the doctrine it should
  **warn + file a bug + continue**. `roles/CM.md` ("Quality-class lint findings
  are non-blocking") is the authoritative enumeration of this class: provenance,
  anti-pattern, orphan-test, help-presence, doc/CHANGELOG/ICD-freshness,
  API-parity, dual-channel-pair, and every other static lint.
- **AGENT-RESOLVABLE** — Fatal to the current done-claim, but self-serviceable
  by the agent in the same session (re-merge, re-commit, reword the offending
  line, or suppress it in the allowlist sidecar with a justification, then
  re-complete). A refusal here is an agent chore, never a Needs-Human block.

## Discovery method (reproducible)

Discovery combined a governance read with a code grep, scoped to **loop-bearing
machinery** — the autonomous wake → discovery → PM → CODER/WRITER → TESTER → CM
pipeline and the gate/lint fleet that runs inside it. Commands run from the dev
tree root:

```bash
# HALT writers and ship-policy triggers in the release driver and its lib stages
grep -nE "cm_halt|write_halt|append_halt_event" team/scripts/cm/release.sh team/scripts/lib/cm/*.sh

# HALT / refuse guards in the discovery pipeline and wake dispatch
grep -nE "HALT|refus|exit [1-9]" team/scripts/lib/discovery*.sh team/scripts/wake-batch.sh

# The CODER/WRITER completion gate
grep -n "coder_writer_completion_gate" team/scripts/lib/wake_lifecycle.sh

# The standalone lint / gate fleet and their fatal exits
grep -nE "sys\.exit\([1-9]|return 1|exit [1-9]" team/scripts/lint_*.py team/scripts/lint_*.sh \
  team/scripts/check_breakage_class.py team/scripts/audit_coding_standards.sh

# Breakage-class hard gate vs quality-class warn-file wrapper
grep -n "check_breakage_class\|gated_runner_lint" team/scripts/run-*-tests.sh

# Ship-policy matrix HALT rows (governance)
grep -n "HALT" team/roles/CM.md
```

The gate/lint fleet backbone is enumerated from the current tree and
**classified against The Loop doctrine**, not against whether each site owes
its caller a remedy.

**Scope decision (documented).** "Shipped code" is read through the loop lens the
doctrine defines. Pure operator-CLI argument/usage refusals in interactive
utilities (`show.sh`, `delete.sh`, `close.sh`, and the like) are *not* included:
they can only reject a human's malformed command at a terminal — they can never
halt the autonomous loop, because the loop never invokes them. The census covers
every grep-discoverable site that the *autonomous pipeline* can reach: HALT
triggers, ship-policy matrix HALT rows, release-script gates, hook guards,
wake/discovery refusals, the completion gate, breakage-class pre-flight, and the
full quality-class lint fleet the runners orchestrate.

## At-a-glance rollup

| # | Site | Source (path · locator) | Trigger | Class |
|---|---|---|---|---|
| 1 | Wake provider dispatch | `team/scripts/wake-batch.sh` · provider `exit 1` | Active provider's `wake/<provider>.sh` missing or not executable | BLOCK-WORTHY |
| 2 | Env-bootstrap source abort | `team/scripts/wake-batch.sh`, `run-*-tests.sh`, `ship-rc.sh` · `source lib/env_bootstrap.sh \|\| exit 1` | Bootstrap prelude cannot be located or sourced | BLOCK-WORTHY |
| 3 | Global HALT guard | `team/scripts/lib/discovery.sh` · `${TEAM_ROOT}/HALT` check | Operator/CM global HALT flag present | BLOCK-WORTHY |
| 4 | Per-project HALT guard | `team/scripts/lib/discovery.sh` · `pp_project_halted` | Per-project `projects/<name>/HALT` flag present | BLOCK-WORTHY |
| 5 | Active-RC single-flight guard | `team/scripts/lib/discovery_guards.sh` · `_disc_active_rc_blocks` | An RC is in flight; refuses to queue PM / bundle | BLOCK-WORTHY |
| 6 | Completion gate — merge check | `team/scripts/lib/wake_lifecycle.sh` · `coder_writer_completion_gate` #1 | Feature branch not merged into source branch at done-claim | AGENT-RESOLVABLE |
| 7 | Completion gate — cleanliness | `team/scripts/lib/wake_lifecycle.sh` · `coder_writer_completion_gate` #2 | Uncommitted changes in the task worktree at done-claim | AGENT-RESOLVABLE |
| 8 | Matrix HALT — TESTER BLOCKED | `team/roles/CM.md` matrix · `lib/cm/release_ship_policy.sh` `trigger-1.tester-blocked` | Verification could not complete | BLOCK-WORTHY |
| 9 | Matrix HALT — systemic_risk high | `team/roles/CM.md` matrix · `lib/cm/release_ship_policy.sh` `trigger-2.systemic-risk-high` | Report-level systemic risk = high, and the finding is a regression, damages integrity, or costs a large fix (a pre-existing, non-integrity-damaging, small-fix finding ships as `SHIP-KNOWN-ISSUE` instead). Under `ship_posture = always` it ships `SHIP-NON-FUNCTIONAL` and records an Auto-Ship Event instead of halting | BLOCK-WORTHY |
| 10 | Matrix HALT — serious + large fix | `team/roles/CM.md` matrix · `lib/cm/release_ship_policy.sh` `trigger-3.serious-concerns-large-fix` | SHIP-WITH-SERIOUS-CONCERNS with any large-fix finding. Under `ship_posture = always` it ships `SHIP-NON-FUNCTIONAL` and records an Auto-Ship Event instead of halting | BLOCK-WORTHY |
| 11 | Matrix HALT — missing ordered report | `team/roles/CM.md` matrix (no report, `Test Required: true`) | Verification ordered but its report is missing | BLOCK-WORTHY |
| 12 | Matrix HALT — unrecognized inputs | `team/roles/CM.md` matrix · `lib/cm/release_ship_policy.sh` `tester-state-unrecognized` / `ship-policy.halt` | TESTER state string the matrix does not recognize (an unreadable recommendation or systemic-risk value never reaches here: it floors to the SHIP-WITH-CONCERNS row and files a bug) | BLOCK-WORTHY |
| 13 | Pre-squash hook gate | `team/scripts/cm/release.sh` · `trigger-4.pre-squash-hook-not-executable` / `-failed` | Project pre-squash hook non-executable or exits non-zero | BLOCK-WORTHY |
| 14 | Squash conflict HALT | `team/scripts/lib/cm/release_squash.sh` · `trigger-5.squash-conflict` | Squash into main produced a merge conflict | BLOCK-WORTHY |
| 15 | Tag-points-at-wrong-commit | `team/scripts/lib/cm/release_finalize.sh` · `trigger-5b.tag-points-at-wrong-commit` | Existing tag does not point at the squash commit | BLOCK-WORTHY |
| 16 | Push-fails-after-retries | `team/roles/CM.md` trigger 6 · `cm/finalize-release.sh` push `exit 1` | Origin unreachable/rejecting after retries | BLOCK-WORTHY |
| 17 | Tag-exists-on-origin | `team/scripts/lib/cm/release_finalize.sh` · `trigger-7.tag-exists-on-origin` | Release tag already exists on origin | BLOCK-WORTHY |
| 18 | Last-3-RCs NON-FUNCTIONAL | `team/scripts/lib/cm/release_ship_policy.sh` · `trigger-8.last-3-rcs-non-functional` | Three consecutive RCs shipped NON-FUNCTIONAL. Under `ship_posture = always` it files a bug naming the pattern, ships, and records an Auto-Ship Event instead of halting | BLOCK-WORTHY |
| 19 | Release-notes status-stamp guard | `team/scripts/lib/cm/release_ship_policy.sh` · `trigger-9.*` (4 sub-sites) | Placeholder survived / stamp failed / no `## Status` heading | BLOCK-WORTHY |
| 20 | Squash-pollution guard (strict) | `team/scripts/lib/cm/release_ship_policy.sh` · `trigger-10.pollution-guard-strict` | Unexpected paths polluted the squash | BLOCK-WORTHY |
| 21 | Fidelity-gate divergence | `team/scripts/lib/cm/release_squash.sh` · `trigger-11.fidelity-gate-divergence` | RC tree diverges from main after squash | BLOCK-WORTHY |
| 22 | Pre-tag hook gate | `team/scripts/cm/release.sh` · `pre-tag-hook-not-executable` + pre-tag hook failure | Project pre-tag hook non-executable or exits non-zero | BLOCK-WORTHY |
| 23 | Pre-tag VERSION gate | `team/scripts/lib/cm/release_finalize.sh` · on-disk `VERSION` vs tag | On-disk `VERSION` ≠ the tag about to be cut | BLOCK-WORTHY |
| 24 | Declared-required hook absent | `team/pgai_agent_kanban/cm/ship.py` (ship-rc) / `cm/release.sh` · `cm_release_<phase>_hook_required` | A hook declared `_hook_required=true` is missing | BLOCK-WORTHY |
| 25 | Breakage-class pre-flight gate | `team/scripts/check_breakage_class.py` (via `run-unit-tests.sh`, `run-integration-tests.sh`) | Syntax error, compile failure, or unresolved first-party import | BLOCK-WORTHY |
| 26 | pytest real-defect suite failure | `team/scripts/run-{unit,integration,gated}-tests.sh` · non-zero pytest | A real assertion/defect failure in the suite | BLOCK-WORTHY |
| 27 | `require_dev_tree` pre-flight abort | `team/scripts/lib/dev_tree.sh` (via all three runners) | `dev_tree_path` not configured or does not exist | BLOCK-WORTHY |
| 28 | pytest-not-installed abort | `team/scripts/run-{unit,integration,gated}-tests.sh` · `exit 2` | `pytest` not importable | BLOCK-WORTHY |
| 29 | Per-project dev-tree verify gate | `team/scripts/verify-perproject-devtree-gate.sh` | Behavioral assertion of per-project dev-tree resolution fails | BLOCK-WORTHY |
| 30 | CHANGELOG freshness | `team/scripts/lint_changelog_freshness.py` · `return 1` | Checked-in `CHANGELOG.md` stale vs regeneration | CONVERT |
| 31 | Hygiene lint — findings | `team/scripts/lint_hygiene.py`, findings merged per-instance via `gated_runner_merge_findings_file` — no coarse `return 1` exit, and the lint itself always exits 0 | A finding in any registered class (`lint_hygiene.py --help` lists them) in a scanned file, or a checked-in allowlist sidecar that fails to load (unreadable, invalid JSON, wrong shape, or an entry with a blank or missing `justification`), reported as its own `SIDECAR-MALFORMED` finding | CONVERT |
| 32 | ICD freshness | `team/scripts/lint_icd_freshness.py` · `exit 1` | Checked-in `docs/api/icd.json` stale vs regeneration | CONVERT |
| 33 | ICD compatibility | `team/scripts/lint_icd_compat.py` | ICD breaks a SUPPORTED baseline / manifest unparseable / a baseline below the current ICD version differs byte-for-byte from its copy on the prefixed main branch (a published contract was rewritten) / the support table in `docs/operator-api.md` drifts from `docs/api/baselines/SUPPORTED` (a SUPPORTED version with no row is reported as missing, a non-retired row SUPPORTED lacks as extra, a `retired` row SUPPORTED still lists as retired, or the table is absent) | CONVERT |
| 34 | API parity (census-walk gate) | `team/scripts/lint_api_parity.py` · `return 1` (`api-parity-census-gate-01`) | The operator verb census (`team/config/operator_verbs.manifest`) disagrees with the tree: a routed verb's script is missing, a script-fronted REST route has no census row, a route's response does not validate against its declared `schema_ref`, an operator-invocable script has no census row at all, an active verb's row declares no REST twin at all (`rest_method`/`rest_route` `ABSENT` -- only a `planned` row may carry them), an active `action`-class verb has no effect-parity or refusal-parity case in the action-parity ledger (`team/tests/fixtures/action_parity_cases.py`), or an active `read`-class verb has no golden-pair case in the read-parity ledger (`team/tests/fixtures/read_parity_cases.py`) -- plus this script's original flag/body-model parity checks (API model missing an expected field) | BLOCK-WORTHY |
| 35 | Upgrade no-temp-copy guard | `team/scripts/lint_upgrade_no_temp_copy_guard.py` · `return 1` | Forbidden lines in the upgrade pre-retirement block | CONVERT |
| 36 | Help presence (Directive 6) | `team/scripts/lint_help_presence.py` · `return 1` | Script lacks a `--help`/`-h` handler | CONVERT |
| 37 | Python import completeness | `team/scripts/lint_python_import_completeness.py` · `return 1` | Third-party import not covered by `requirements*.txt` | CONVERT |
| 38 | Dashboard network-git gate | `team/scripts/lint_dashboard_network_git_gate.py` · `return 1` | Network-capable git verb under `dashboard/` | CONVERT |
| 39 | Env-bootstrap ordering | `team/scripts/lint_env_bootstrap.py` · `return 1` | Kanban-root env access outside the canonical resolver | CONVERT |
| 40 | Lib-function dedupe | `team/scripts/lint_lib_function_dedupe.py` · `return 1` | Duplicate function definition under `lib/` | CONVERT |
| 41 | Orphan tests | `team/scripts/lint_orphan_tests.py` · `return 1` | `test_*.py` unreachable from any collected root | CONVERT |
| 42 | Test anti-patterns | `team/scripts/lint_test_anti_patterns.py` · `return 1` | Banned test pattern (e.g. bare `/tmp`) | CONVERT |
| 43 | Dual-channel pair | `team/scripts/lint_dual_channel_pair.py` | Refusal narration missing a dual-channel pair | CONVERT |
| 44 | Skip-cites-real-bug gate | `team/scripts/lint_skip_bug_gate.sh` · `exit 1`/`exit 2` | Placeholder skip citation, or cited `BUG-NNNN` with no file | CONVERT |
| 45 | Coding-standards audit | `team/scripts/audit_coding_standards.sh` · `exit 2` | MANUAL-REVIEW-REQUIRED / FINDINGS in the audit | CONVERT |
| 46 | Coder stale-literal check | `team/scripts/coder-stale-literal-check.sh` · `exit 1` | Stale literal / residue in changed code | CONVERT |
| 47 | doc-lint — cmd suite | `team/scripts/harness/doc-lint.sh` | Fenced documented command exits non-zero | CONVERT |
| 48 | doc-lint — links suite | `team/scripts/harness/doc-lint.sh` | Relative Markdown link does not resolve | CONVERT |
| 49 | doc-lint — fossil-greps suite | `team/scripts/harness/doc-lint.sh` | Known internal-residue pattern matches | CONVERT |
| 50 | doc-lint — feature-greps suite | `team/scripts/harness/doc-lint.sh` | Required feature token missing from audited docs | CONVERT |
| 51 | Bare-`/tmp` cleanliness assertion | `team/scripts/run-unit-tests.sh` (via `lib/temp.sh`) | Suite left residue in bare `/tmp` | CONVERT |
| 52 | TCP-listener cleanliness assertion | `team/scripts/run-unit-tests.sh` (via `lib/temp.sh`) | Framework-rooted TCP listener survived the suite | CONVERT |
| 53 | Engine size + dupe lint | `team/scripts/lint_engine_size_dupe.py` · `return 1` (duplicate function) / `return 3` (size only) | An engine file over the line ceiling, or a function name colliding within a shared bash-process reachability set | CONVERT |
| 54 | Dockerfile static lint (malformed FROM / non-octal chmod) | `team/scripts/lint_dockerfile_static.py` (via `run-unit-tests.sh` · `dockerfile-static-lint-01`) | Malformed `FROM` arity (including the duplicated-keyword form) or a non-octal `COPY`/`ADD` `--chmod` mode in a `docker/*/Dockerfile` -- a doctrine "failed to build" shape no engine this project ships can complete | BLOCK-WORTHY |
| 55 | RC empty-commit lint | `team/scripts/lint_rc_empty_commits.py` (via `run-integration-tests.sh` · `rc-empty-commits-01`) | Non-merge commit in a locally present `<prefix>rc/vX.Y.Z` branch's range whose tree is byte-identical to its parent's | CONVERT |
| 56 | Telemetry-block provider-safeguard refusal | `team/scripts/lib/wake_driver_invoke.sh` · `_wd_run_provider_with_telemetry_safeguard_retry` (`wake-driver-invoke-01`) | Provider refuses the first invocation with a safeguard-shaped error while the optional documentation-feedback prompt block is present | CONVERT |
| 57 | Semantic dashboard palette file missing | `team/scripts/lib/dashboard_palette.sh` (`dashboard-palette-file-missing-01`) | The single-source semantic palette definition file (`team/config/default_palette.txt`) is missing at render time | BLOCK-WORTHY |
| 58 | Semantic dashboard palette key missing | `team/scripts/lib/dashboard_palette.sh` (`dashboard-palette-key-missing-01`) | A renderer asks for a semantic color key the palette file does not declare | BLOCK-WORTHY |
| 59 | Dashboard pane-arm handshake timeout | `team/scripts/dashboard/create.sh` (`dashboard-create-pane-arm-timeout-01`) | One or more panes did not report their signal trap armed within the bounded readiness-handshake timeout | BLOCK-WORTHY |
| 60 | Stale gated-runner findings-file claim | `team/scripts/lib/gated_runner_lint.sh` · `gated_runner_assert_findings_claim` (`gated-runner-stale-findings-claim-01`) | The findings file's run-identity sidecar no longer matches the token this invocation claimed it with -- a concurrent invocation of a gated runner reclaimed the same host-wide temp path in between | BLOCK-WORTHY |
| 61 | Chain lock ownership reverification | `team/scripts/lib/lock_provenance.sh` · `pgai_lock_reverify_or_narrate` (`lock-provenance-reverify-01`) | A chain's cheap, read-only re-check of its own project repo lock, run at every mutating phase boundary (agent dispatch, the post-agent merge check, the queue-marker write), finds a different holder's provenance on disk -- the chain was reaped and its lock reacquired by another process while it kept running | BLOCK-WORTHY |
| 62 | Non-CM remote git verb refused | `team/scripts/lib/git_boundary_guard.sh` · `git_boundary_guard_enforce` (`git-boundary-guard-01`) | The `git` shim on a non-CM task's PATH (`team/scripts/wake/git-guard/git`) sees `fetch`, `pull`, or `push` invoked under a role other than `cm` against a destination that does not resolve inside the framework's own throwaway temp root -- the boundary `team/SOP-core.md` ("Git Repositories: Roles and Boundaries") states as CM-only. The destination is the token `_git_boundary_guard_find_destination` finds after the subcommand, skipping every fetch/pull/push option this scan currently recognizes as consuming a SEPARATE value of its own (`--depth`, `--deepen`, `-o`/`--server-option`/`--push-option`, `-j`/`--jobs`, `--upload-pack`, `--receive-pack`, `--shallow-since`, `--shallow-exclude`, `--negotiation-tip`, `--filter`, `--refmap`, `--repo`, `--exec`, `-s`/`--strategy`, `-X`/`--strategy-option`, `-m`/`--message`) so an option's own value ahead of the remote name is not mistaken for the destination -- this list is defence in depth, not a closed set, and any value-consuming option it does not name is still a residual instance of the same misreading. A destination that resolves under `$PGAI_AGENT_KANBAN_TEMP_DIR` -- or, when that configured root is exactly `<parent>/projects/<name>` with nothing after `<name>` AND `<parent>` itself resolves at or below the framework's own temp root as `team/scripts/lib/temp.sh`'s `pgai_configured_temp_root` resolves it (kanban.cfg's `[paths]` `tmp_root`/`tmp_subdir`, or its self-locate/hard-coded fallback -- never inferred from the configured root's own text), under `<parent>` (never a root that merely contains a `projects` path component elsewhere, has one or more components after `<name>`, or whose stripped `<parent>` is empty, `/`, or otherwise not framework-owned territory, all of which compare against the root exactly as configured, un-widened) -- (a test fixture's own bare repository, for instance) passes through unrefused and unrecorded, same as any other non-boundary verb, and so does a destination the probe proves is unreachable altogether -- a remote name that was never configured, or a `-C` directory that no longer exists (`_git_boundary_guard_is_unreachable_destination`) -- but only when the repository path itself (`repo_path`, never a value derived from the destination scan) also resolves under that same bound (`_git_boundary_guard_path_under_temp_root`): the real git invocation this shim hands off to would fail identically without ever touching a network, so recording it was noise, not a crossing, but only when the repository itself is the throwaway kind every such fixture already is. The same unreachable-destination probe result against a repository outside that root is refused and recorded like any other unresolved destination, regardless of which option (listed or not) produced the false destination token: enumerating every value-consuming option can never be complete, since the set is git's own and grows, but binding the exemption to the repository's own location closes the class without depending on the list above at all. | BLOCK-WORTHY |
| 63 | Post-suite scratch-tests sweep | `team/scripts/run-integration-tests.sh` (`run-integration-tests-scratch-sweep-01`), `team/scripts/run-unit-tests.sh` (`run-unit-tests-scratch-sweep-01`), via `lib/temp.sh`'s `pgai_temp_cleanup_tests` | A read-only leftover directory under `${PGAI_AGENT_KANBAN_TEMP_DIR}/tests`, left behind by an aborted or crashed earlier run, makes the post-suite scratch sweep's `rm -rf` fail -- under `set -euo pipefail` that failure aborts the whole runner even though every test passed and no lint was fatal | CONVERT |
| 64 | Container-engine-free lint (per-RC sweep set) | `team/scripts/lint_container_engine_free.py` (via `run-unit-tests.sh` · `container-engine-free-lint-01`; via `run-integration-tests.sh` · `container-engine-free-lint-01`) | A test module collected by `run-unit-tests.sh` or `run-integration-tests.sh` (`team/tests/unit/`, `team/tests/integration/`, `team/pgai_agent_kanban/api/tests/`) invokes or probes a container engine (`docker`/`podman`) -- a runaway build-and-container cost paid on every RC. The deep-verify tier (`team/tests/cold/`) is the only lane entitled to do that | BLOCK-WORTHY |

| 65 | Sentinel leak assertion (HALT / HALT-AFTER / DEFER-LOW-BUGS) | `team/scripts/run-unit-tests.sh`, `team/scripts/run-integration-tests.sh` (via `lib/sentinel_leak_check.sh`) | A HALT, HALT-AFTER or DEFER-LOW-BUGS file on the LIVE kanban root (root or project scope) was added, changed or removed between the pre-suite snapshot and the end of the suite -- a fixture resolved the live root instead of its synthetic board; reported and left in place, never repaired | CONVERT |
| 66 | Ownership-checked lock release | `team/scripts/lib/lock_provenance.sh` · `pgai_lock_release` (`lock-provenance-release-not-owner-01`) | A release -- an explicit call, or the trap/finally sweep on exit -- finds that the file at the lock path is no longer the inode this process holds the flock on (another process reaped the lock and reacquired it while this one kept running, or the path is gone); it closes its own fd and drops its bookkeeping, leaving the new holder's file untouched instead of clearing and unlinking it | CONVERT |
| 67 | Provider CLI too old for the configured model (first dispatch) | `team/scripts/lib/provider_claude.sh` · `claude_refuse_if_cli_too_old` (`provider-claude-01`) | A dispatch exits non-zero and its output carries the CLI's `unrecognized_model` / "does not support this model; version X or newer is required" signature -- the installed Claude Code CLI cannot run the configured model at all, so no agent ran. The task returns to BACKLOG with its zero-progress record untouched (never BLOCKED, never Needs Human) and the wake dispatches no further task; the required version is memoed for row 68 | BLOCK-WORTHY |
| 68 | Provider CLI too old for the configured model (pre-dispatch) | `team/scripts/lib/provider_claude.sh` · `provider_model_preflight` (`provider-claude-02`) | The memo row 67 left names this wake's model and `claude --version` is still below the required version -- the preflight refuses before any task moves to WORKING, leaving the queue untouched; it clears the memo and passes once the installed CLI satisfies the requirement | BLOCK-WORTHY |
| 69 | Malformed task id in a queue line or prerequisite token | `team/scripts/lib/wake_task_id_gate.sh` · `_narrate_malformed_queue_task_ids` (`wake-task-id-gate-malformed-queue-id-01`, serving `wake_task_state.sh`'s pending, WAITING and BLOCKED scans and `wake_reconcile_sweeps.sh`'s stale-active scan) and `_narrate_malformed_prerequisite_ids` (`wake-task-id-gate-malformed-prerequisite-id-01`, serving `check_prerequisites` in the same file) | A queue line the scan would act on, or a `## Prerequisites` token, names an id outside the canonical shape `task_ids.sh`'s `kanban_task_id_pattern` prints (`..`, `../x`, `a.b`, anything with a separator or a dot). The line is narrated naming the file and line and skipped -- a prerequisite token is held as `<token>:MALFORMED`, unsatisfied -- so the id is never joined onto `TASKS_ROOT`; every well-formed entry is still served and the wake continues | CONVERT |
| 70 | Worktree residue outside the canonical base | `team/scripts/lib/worktree.sh` · `_wt_clear_task_worktree_path`, the pre-clean `create_task_worktree` and `create_detached_worktree` share (`worktree-residue-outside-base-01`) | Something sits at a task's worktree path -- a registered worktree or not -- and `pgai_worktree_path_strictly_inside` -- both paths canonicalised with `realpath` (symlinks resolved, `..` collapsed) -- finds it is not a strict descendant of the worktree base: the base itself, its parent (`<base>/..`), or a symlink leading out of it. The check runs before any removal, by git or by `rm`. The only alternative is a delete outside the directory the function owns, so the create is refused and nothing is removed | BLOCK-WORTHY |
| 71 | Targeted-run `--files` argument refusals | `team/scripts/run-unit-tests.sh` (`run-unit-tests-targeted-files-01` .. `-04`), `team/scripts/run-integration-tests.sh` (`run-integration-tests-targeted-files-01` .. `-04`) | A `--files` targeted run is given no path, is combined with `--coverage`, names a path that resolves (after `..` and symlinks) outside the runner's own test roots, or names a path under them that is not an existing file. Refused before any gate or pytest runs; the findings file is never touched | AGENT-RESOLVABLE |
| 72 | Targeted-run per-file test-authoring lint finding | `team/scripts/run-unit-tests.sh` (`run-unit-tests-targeted-files-05`), `team/scripts/run-integration-tests.sh` (`run-integration-tests-targeted-files-05`), via `lib/gated_runner_lint.sh`'s `gated_runner_targeted_file_lints` | Every test a `--files` targeted run named passed, but the test anti-pattern lint or the skip-cites-a-real-bug gate, run over exactly the named files, reported a finding in one of them. The run exits 1 after pytest; the findings file and the green ledger are never touched | AGENT-RESOLVABLE |
| 73 | Inline-Python ratchet lint | `team/scripts/lint_inline_python.py` (via `run-unit-tests.sh` and `run-integration-tests.sh` · `inline-python-01`), findings merged per-instance via `gated_runner_merge_findings_file` -- the lint itself always exits 0 | A non-test `team/scripts/*.sh` file carries more inline Python (python3 heredocs and `python3 -c` programs, counted as blocks and python lines) than its entry in `team/config/inline-python-allowlist.txt`, an unlisted file carries any, an entry sits above the measured counts and should be tightened, or a Python heredoc opens with an unquoted delimiter | CONVERT |
| 74 | Duplicated-Python ratchet lint | `team/scripts/lint_python_duplicates.py` (via `run-unit-tests.sh` and `run-integration-tests.sh` · `python-duplicates-01`), findings merged per-instance via `gated_runner_merge_findings_file` -- the lint itself always exits 0 | A function under `team/pgai_agent_kanban`, `team/scripts` or `team/pm-agent` has the same AST-normalised body as another, a deny-listed helper (`_atomic_write`, `_has_traversal`, `_parse_version`, `_parse_iso`, `read_section`, a `configparser.*Parser(` instantiation, a `## State` regex) appears outside its owning module, and the finding is not recorded in `team/config/python-duplicates-allowlist.txt` -- or an allowlist entry no longer matches and should be tightened | CONVERT |
| 75 | API route / CLI shared-implementation lint | `team/scripts/lint_api_route_impl.py` (via `run-unit-tests.sh` and `run-integration-tests.sh` · `api-route-impl-01`), findings merged per-instance via `gated_runner_merge_findings_file` -- the lint itself always exits 0 | An operator verb whose `team/config/operator_verbs.manifest` impl is `module:<function>` has a REST route that does not call that function or that calls `shell_out`/`subprocess`, or a CLI script that does not reach the function through `pp_run_ops`; or the number of impl `shell` rows is above `team/config/api-route-impl-baseline.txt`, or below it so the baseline should be tightened | CONVERT |
| 76 | Refusal ledger: unregistered project | `team/pgai_agent_kanban/lib/refusal_ledger.py` · `unregistered_project_narration` (`refusal-ledger-unregistered-project-01`), `team/scripts/lib/refusal_ledger.sh` · `refusal_ledger_record` (`refusal-ledger-unregistered-project-02`) | A refusal is recorded for a project the kanban root's `projects.cfg` does not register (or a `projects.cfg` that cannot be read); the ledger writes nothing, creates no `projects/<project>/` folder, and narrates one `refusal_ledger:` line on stderr -- the caller's own refusal, exit status and stdout are unchanged | CONVERT |
| 77 | Live projects/ leak guard (test session) | `team/tests/conftest.py` · `pytest_sessionfinish` (`tests-conftest-live-projects-leak-01`) | A test session ends with an entry under the invoking shell's `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/` that was not there at session start and that `projects.cfg` does not register; the run fails (exit 1) with `tests.live_projects_leak` naming each entry; reported, never removed | BLOCK-WORTHY |
| 78 | echo/printf-into-quiet-grep ratchet lint | `team/scripts/lint_echo_pipe_grep_q.py` (via `run-unit-tests.sh` and `run-integration-tests.sh` · `echo-pipe-grep-q-01`), findings merged per-instance via `gated_runner_merge_findings_file` -- the lint itself always exits 0 | A non-test bash file under `team/` or `install.sh` pipes `echo` or `printf` output into `grep -q` (or `--quiet` / `--silent`), a pipeline that can report no match for matching text under `set -o pipefail`, and the file has more such sites than its entry in `team/config/echo-pipe-grep-q-allowlist.txt` (an unlisted file may carry none), or an entry sits above the measured count and should be tightened | CONVERT |
| 79 | Unreadable version ceiling | `team/scripts/lib/discovery.sh` · `discovery_run_pipeline` (`discovery-ceiling-unreadable-01`), `team/scripts/lib/discovery_step_requirements.sh` · `discovery_step_requirements` (`discovery-step-requirements-ceiling-unreadable-01`), both emitting `discovery.ceiling-unreadable`; `team/scripts/pm-agent.sh` · manual-mode ceiling check (`pm-agent-ceiling-unreadable-01`) | A `[versioning]` ceiling in the project config is malformed, so its accessor fails naming the file, key and value: discovery queues nothing for that project this run, and a manual `pm-agent.sh` run refuses the requirement unless `--override-ceiling` is passed | BLOCK-WORTHY |
**Rollup: 79 sites, 0 unclassified.**
41 BLOCK-WORTHY, 34 CONVERT, 4 AGENT-RESOLVABLE.

Rows 13, 19, and 22 each fold more than one `cm_halt` call site of the same
family into a single row (pre-squash executable/failed; the four `trigger-9`
status-stamp sub-sites; pre-tag executable/failed); they are counted once each
because a single conversion or justification decision governs the whole family.

## BLOCK-WORTHY justifications

Every BLOCK-WORTHY row carries a one-sentence justification tied to The Loop
doctrine's backward-motion categories (destructive · breakage · verification
unable to run · approval/invariant by design).

1. **Wake provider dispatch** — Verification unable to run: if the loop's own
   entry point cannot dispatch to a provider, no agent runs at all, so failing
   loudly beats silently doing nothing.
   Rows 67–68 (provider CLI too old for the configured model, at first
   dispatch and pre-dispatch) are the same category one step later: the CLI
   is present but rejects the configured model outright, so no agent runs.
   The stop is scoped to one wake's dispatching and self-heals on
   `claude update`; it never writes task state, which is what keeps a host
   condition from being recorded as a stuck task.
2. **Env-bootstrap source abort** — Verification unable to run: the execution
   environment (paths, root resolution) is unresolved, and every downstream
   step would compound the corruption.
3. **Global HALT guard** — Approval-by-design: the global HALT file is the
   operator's and CM's explicit stop signal, and honoring it is the whole point
   of the flag.
4. **Per-project HALT guard** — Approval-by-design: the per-project HALT is the
   operator's scoped stop signal for one project's lane.
5. **Active-RC single-flight guard** — Invariant-by-design: allowing PM to
   decompose a second release while an RC is in flight would inject unbounded
   mid-release scope creep, moving the release backwards.
8. **Matrix HALT — TESTER BLOCKED** — Verification unable to run: CM cannot ship
   on a verification that never completed.
9. **Matrix HALT — systemic_risk high** — Defect too severe for the next
    iteration to absorb: high systemic risk signals the loop is unhealthy, not
    merely that known bugs exist. A project whose operator set
    `ship_posture = always` waives the stop on the record: the release ships
    `NON-FUNCTIONAL` and an Auto-Ship Event names the stop it passed.
10. **Matrix HALT — serious + large fix** — Defect too severe: shipping through
    a large-effort serious finding risks a build the next patch cannot repair.
    Under `ship_posture = always` the stop is waived on the record, as for
    row 9.
11. **Matrix HALT — missing ordered report** — Verification unable to run:
    verification was ordered and its evidence is absent, so the inputs cannot be
    trusted.
12. **Matrix HALT — unrecognized inputs** — Fail-loud on untrusted input: an
    unrecognized state/recommendation string means CM's inputs cannot be
    trusted, and shipping on garbage input is the one silent-default failure the
    matrix must refuse.
13. **Pre-squash hook gate** — Destructive-path: the finalization mechanic is
    broken, so the squash into main cannot proceed safely.
14. **Squash conflict HALT** — Destructive-path: the git state is damaged and
    must not be auto-resolved.
15. **Tag-points-at-wrong-commit** — Destructive-path: a tag mislabeling the
    release would permanently misrepresent what shipped.
16. **Push-fails-after-retries** — Verification/distribution unable to complete:
    the release is locally complete but cannot be distributed, and the operator
    must resolve the network/auth fault.
17. **Tag-exists-on-origin** — Destructive-path/race: re-shipping over an
    existing remote tag would overwrite a published release.
18. **Last-3-RCs NON-FUNCTIONAL** — Loop unhealthy: three degraded ships in a
    row prove the chain is not self-correcting and must pause for review.
   Under `ship_posture = always` the pause is replaced by a bug naming the
   pattern, and the release ships with an Auto-Ship Event recording the stop.
19. **Release-notes status-stamp guard** — Fail-loud on untrusted record: the
    published release record cannot be guaranteed to state the actual ship
    decision.
20. **Squash-pollution guard (strict)** — Destructive-path: unexpected paths in
    the squash mean the tree that ships is not the tree that was verified.
21. **Fidelity-gate divergence** — Destructive-path: the RC and main trees are
    not byte-identical, so an out-of-band change would ship unverified.
22. **Pre-tag hook gate** — Destructive-path: a broken pre-tag finalization step
    means the tagged state cannot be trusted.
23. **Pre-tag VERSION gate** — Destructive-path: an on-disk `VERSION` that
    disagrees with the tag would ship a mislabeled build.
24. **Declared-required hook absent** — Breakage-by-contract: the project
    explicitly declared the hook mandatory, so its absence is a mis-release the
    operator opted into catching.
25. **Breakage-class pre-flight gate** — Breakage-class: a syntax/compile/
    first-party-import failure means the suite would produce no trustworthy pass
    signal.
26. **pytest real-defect suite failure** — Breakage/defect: a real assertion
    failure is exactly the backward-motion signal the loop must not ship past.
27. **`require_dev_tree` pre-flight abort** — Verification unable to run: without
    a configured, existing dev tree there is nothing to test.
28. **pytest-not-installed abort** — Verification unable to run: the test runner
    itself is absent, so no pass signal can be produced.
29. **Per-project dev-tree verify gate** — Breakage-class: a failing behavioral
    assertion means the per-project dev-tree resolution the pipeline depends on
    is broken.
30. **Dashboard pane-arm handshake timeout** — Invariant-by-design/breakage:
    the readiness handshake exists so create.sh never hands the operator a
    session containing a pane whose signal trap is not yet installed; a pane
    still in that state after the bound is exactly as vulnerable to the
    dies-on-first-signal failure the handshake exists to prevent, so
    attaching over it would ship a session that does not actually satisfy
    the signal-safety invariant the handshake enforces. `dashboard/create.sh` is
    not on `_CENSUS_BASH_FILES` (same documented-but-unscanned state as
    `discovery_guards.sh` above), so this row records the site's class and
    dual-channel contract as inventory; the lint does not scan this file
    today.
70. **Worktree residue outside the canonical base** — Destructive: the
   pre-clean `create_task_worktree` and `create_detached_worktree` share
   (`_wt_clear_task_worktree_path`) removes whatever sits at the task's
   worktree path, by `git worktree remove --force` or a recursive delete,
   and a path whose canonical form is the worktree base itself, its parent,
   or a symlink out of the base is not residue the function owns — a
   string-prefix test would pass `<base>/..` — so the create refuses
   outright, before any removal, rather than deleting outside its own
   directory.

79. **Unreadable version ceiling** — Approval/invariant-by-design: a
    malformed `max_major`/`max_minor`/`max_patch` cannot be compared
    against, and reading it as "no ceiling" would remove the operator's
    version gate without a word, so the project is ceiling-blocked for the
    run, exactly as the exceeded-ceiling sites in `discovery_guards.sh`
    block. The `pm-agent.sh` anchor sits on the manual queue's one ceiling
    refusal, which an exceeded ceiling also reaches. Correcting the value
    (`set-version-ceiling.sh --project <name> --show` names it) clears the
    block on the next run.

## CONVERT notes

Row 69 (malformed task id in a queue line or prerequisite token) is a
runtime refusal rather than a static finding, and it is `CONVERT` for the
same reason the static ones are: a corrupt or hand-edited line is a defect
in that one line, not grounds to stop the wake. The site narrates the file
and line dual-channel, skips the line (a prerequisite token is held
unsatisfied, so its task waits rather than dispatches), and every
well-formed entry is still served; the operator fixes the one line the
remedy shows. What it protects against — an id such as `..` being joined
onto `TASKS_ROOT` and reaching the worktree layer as a path — is the
destructive case row 70 stops at the last line of defence.

The `CONVERT` rows (30–33, 35–53) are quality-class static findings: each
names a `file:line`/artifact and a fix, and none carries runtime-failure risk
to the running loop. Under the doctrine they should **warn, file a bug, and
continue**. Row 34 (API parity) is outside this range: it is `BLOCK-WORTHY`,
because a missing or schema-mismatched REST twin is real API damage, not a
documentation-accuracy note. "BLOCK-WORTHY justifications" does not list
row 34; its doctrine tie is stated in the row's own Trigger cell instead.

The gated runners already realize this at the orchestration layer:
`team/scripts/lib/gated_runner_lint.sh` runs each quality-class lint and returns
0 regardless of the lint's own exit code, writing a machine-readable findings
file that TESTER surfaces in its "Lint Findings (non-blocking)" report section
(see `roles/CM.md`, "Quality-class lint findings are non-blocking"). The
standalone lint scripts retain their own `exit 1`/`return 1` for direct/CI
invocation — **that** direct-invocation fatal exit is the `CONVERT`-class site
this census tracks, so that any remaining hard-gate call path is converted to
warn-file-continue and any new quality lint inherits the same wrapper.

Row 31 is the one `CONVERT` row with no such exit to track. The hygiene lint
reports per-instance findings and always exits 0, so the runners merge its
findings directly (`gated_runner_merge_findings_file` in the same library)
rather than wrapping an exit code; the row records the findings surface
itself. A class that never had a fatal exit to convert is the destination
this section's conversion work was aimed at, not an exception to it.

The sidecar loader sits inside that same posture. A checked-in allowlist
sidecar that fails to load once ended the run through a fatal exit of its
own; it is now one more finding under this row — `SIDECAR-MALFORMED`,
naming the sidecar and why it was refused — and the broken file supplies
no suppression at all for that run, so nothing it would have hidden stays
hidden. The lint still exits 0. The only non-zero exit left in the script
is a bad CLI argument at the call site, which no authored content can
trigger and which therefore earns no row here.

Rows 51–52 (temp/listener cleanliness) are test-side hygiene assertions: they
detect a test that leaked scratch state, which is zero-risk to the shipped
product, so they belong in the file-and-continue class rather than as a hard
suite abort.

## AGENT-RESOLVABLE notes

The two completion-gate checks (rows 6–7, `coder_writer_completion_gate` in
`team/scripts/lib/wake_lifecycle.sh`) are fatal to a CODER/WRITER done-claim but
are self-serviceable in the same session: re-merge the feature branch (row 6)
or commit or discard the dirty worktree (row 7), then re-complete. Per the
doctrine (`SOP-core.md`, "Gates and Refusals"), a refusal here is an agent
chore, not a Needs-Human block — the role files instruct the agent to
resolve it in-session and re-complete. Comment-provenance litter is not a
completion-gate check: the hygiene lint's tree-wide `provenance` class
catches it at TESTER time, in the gated runners' quality-class fleet
(row 31).

`lint_hygiene.py`'s sidecar loader has no row in this class. A malformed
checked-in sidecar is reported as a finding under row 31 and stops nothing,
so there is no done-claim for an agent to self-service. Correcting or
removing the offending entry is the remedy the finding prints — a quality
finding, healed on the next loop like every other one.

The gated runners' targeted-run argument checks (row 71) refuse a `--files`
invocation that names no path, adds `--coverage`, or names a path outside
the runner's own test roots or one that does not exist. Each is fatal to
that invocation only, and the caller fixes it in the same session: the
remedy each prints is the corrected command. A targeted run is a CODER's
check of the files it touched, never a certification result, so nothing
downstream waits on it and none of these refusals can stop the loop.

A targeted run also fails (row 72) when the two per-file test-authoring
lints -- the anti-pattern lint and the skip-cites-a-real-bug gate, run over
exactly the named files -- report a finding in a file whose tests all
passed. The finding is quality-class wherever else it surfaces; it fails
the targeted run only so the author sees it in the session that wrote the
file, and fixes it there. Nothing is recorded: certification runs keep
their own warn-and-continue handling of the same lints, unchanged.

## Locator Index (lint-consumed)

Treating every literal fatal exit (`exit N` / `sys.exit(N)`) in an
in-scope file as a candidate needing its own inline
`# BLOCK-WORTHY|CONVERT|AGENT-RESOLVABLE` comment and dual-channel emitter
call would flood an otherwise clean tree with findings, because many
documented sites carry no such inline signals. Retrofitting real emitter
calls at every one of them across the release-critical scripts
(`cm/release.sh`, `ship-rc.sh`, `cm/finalize-release.sh`, and the rest) would
touch live release machinery at dozens of points for a quality-class lint. The lower-risk
alternative is the one in force: anchor the scan to this document's own
enumerated locators instead.

The index below is that anchor. Each line is
`<path-relative-to-repo-root>:<key> <CLASS>` for a current, real refusal site
in the tree, paired with the class the site's row family above (rollup rows
1–60) already assigns to that kind of gate. **`<key>` is never a line
number.** It is one of the two names a site carries in its own text: the
literal `error` key the site already passes to the dual-channel emitter,
or the token of a `# CENSUS-ANCHOR: <token>` marker comment placed above
the refusal for sites that have no such key (see "Anchor markers" below).
Both resolve by name, so no entry below encodes a position. The `<CLASS>`
half is unchanged:
`BLOCK-WORTHY` for hooks/git-operation/tag/version/HALT/RC-state gates
(`cm/release.sh` and its five extracted stages under `lib/cm/`
— `release_preflight.sh`, `release_ship_policy.sh`, `release_squash.sh`,
`release_notes.sh`, `release_finalize.sh` — plus `ship-rc.sh`,
`cm/finalize-release.sh`, `lib/dev_tree.sh`, `lib/wake_common.sh`,
`lib/wake_bootstrap.sh`, `verify-perproject-devtree-gate.sh`,
`run-{unit,integration,gated}-tests.sh`, `wake-batch.sh` — rows 1–2, 26–29),
`CONVERT` for the quality-class lint fleet's own precondition/finding exits
(`audit_coding_standards.sh`, `coder-stale-literal-check.sh`,
`harness/doc-lint.sh`, `lint_help_presence.py`, `lint_skip_bug_gate.sh`,
`lint_engine_size_dupe.py` — rows 36, 44–50, 53).

`lint_gate_census_compliance.py` parses this fenced block at scan time (see
`_load_census_locators` in the lint script). A default-scope exit that falls
inside a resolved key's forward window is treated as already tracked and
audited at the document level — analogous to the existing operator-CLI
SITE-TYPE exclusion (see "SITE-TYPE SCOPE" in the lint's module docstring) —
and is exempt from the inline code-comment/emitter-call check. A fatal exit
anywhere else in an in-scope file (a genuinely new, undocumented gate) is
still scanned exactly as before, so a new non-compliant gate still surfaces
as an actionable finding.

**Line independence, and the reverse check.** A key pin carries no
position information, so an edit that moves a refusal within its own file
cannot stale the entry that names it: no stored line number drifts off its
refusal, and none needs regenerating.

The check that keeps the index honest runs in the opposite direction. `_scan_locator_resolution`
walks the census's own entries and confirms each one still resolves to
something real in the file it names; an entry matching neither a marker nor
a literal emitter key anywhere in that file is reported as one
`unresolved_locator` finding — informative and non-blocking, like every
other finding this lint produces. It covers **every** entry, including the
inventory entries in files the default scan never opens, so a pin that goes
stale because its site was deleted or its key renamed surfaces on the next
runner pass instead of sitting quietly in the index.

The remedy is the lint's own, printed beside the finding: open the file,
find the refusal the entry meant, and either add `# CENSUS-ANCHOR: <token>`
directly above it — skip that when the token is already the literal first
argument of a dual-channel emitter call there — or correct the entry to the
key that does resolve at the site. Never delete an entry to silence a real
gate.

### Where the release and wake refusals live

The release script and the wake bootstrap are split across several files,
and each index entry below names the file its own refusal occupies:

- **`cm/release.sh` and `lib/cm/`.** Most release-script entries name one of
  the stage files under `lib/cm/` (`release_preflight.sh`,
  `release_ship_policy.sh`, `release_squash.sh`, `release_notes.sh`,
  `release_finalize.sh` and their siblings). The `cm/release.sh` driver keeps
  its own refusals — the `git_step` failure exit and the operator-override
  checks described below.
- **`lib/wake_bootstrap.sh` and `lib/wake_common.sh`.** The wake bootstrap's
  refusals are in `lib/wake_bootstrap.sh`; the `python3`-on-PATH preflight and
  the OVERWATCH wake's HALT-present stop are in `lib/wake_common.sh`.

**Not every entry is lint-consumed.** The lint's default scan surface is a
maintained include-list (`_CENSUS_BASH_FILES` / `_CENSUS_PYTHON_FILES` in
`team/scripts/lint_gate_census_compliance.py`), and the `lib/cm/` stage files
and `lib/wake_bootstrap.sh` are not on it. Their entries below are inventory —
they record where each refusal lives, but no sweep of those files ever asks
whether the refusal is annotated. Adding those files to `_CENSUS_BASH_FILES`
is a code change, not a census edit; until it lands, a new unannotated gate
added inside one of them will not surface as a finding.

**What "inventory" does and does not exempt.** The phrase recurs in the
per-site notes below, and it is about one of the lint's two directions only.
The site-to-index direction (the missing-signal and unindexed-anchor scans)
walks the include-list, so it never opens an inventory file. The
index-to-site direction (`unresolved_locator`) walks the census's own
entries instead, so it checks **every** entry in the block below, inventory
included: an inventory pin whose key no longer resolves is a finding like
any other. Read each "the lint does not scan this file today" note as
scoped to the first direction.

**Anchor markers.** A `# CENSUS-ANCHOR: <token>` comment above a refusal
names the site — for a human reader and for the lint alike. It is the
fallback half of the two resolution paths: a site that already speaks a
stable dual-channel `error` key is pinned by that key and needs no marker,
and a site with no such key gets a marker whose token the index names
instead. The two paths differ only in how wide a window they exempt.
`_resolve_anchor_marker_lines` finds the marker and exempts its own line
plus the next five. `_resolve_error_key_lines` finds an emitter call whose
literal first argument equals the key and exempts its line plus the next
twenty — the wider window, because a key literal usually precedes its
refusal by several lines of message and remedy text. Neither path reads a
line number from this document.

Both shapes are live in the index below. `team/scripts/run-unit-tests.sh`'s
`run-unit-tests.breakage-class-gate` is a key pin: the lint finds the
literal key in the emitter call the site already makes.
`team/scripts/lint_engine_size_dupe.py`'s `engine-size-dupe-01` and
`team/scripts/lint_rc_empty_commits.py`'s `rc-empty-commits-01` are token
pins, each resolving through the marker comment in its own file. A single
file may carry both where two of its sites differ: `run-unit-tests.sh` has
four token pins beside its two key pins.

**Author the two halves together.** A marker and its Locator Index entry are
added in the same change and removed in the same change, because neither
half inventories a site alone. The lint reports each half as its own
finding: `unindexed_anchor` for a marker no entry names, `unresolved_locator`
for an entry no marker or key resolves. A site may also carry a second,
independent entry on purpose — `run-unit-tests.sh`'s dockerfile static lint
is pinned both by token and by key, because its marker's five-line window
stops short of the `exit 1` it names and the key resolves the site
regardless.

**The full finding vocabulary.** Those two anchor-pairing findings belong to a
set of five, and the five below are the whole set — the exact names
`lint_gate_census_compliance.py` declares in its `_FINDING_KIND_*` constants.
This table is their canonical home: `team/roles/CODER.md` sends a gate author
here for each finding's name, meaning, and fix. Every one of the five is
quality-class. The lint always exits 0 whatever it finds, and prints its own
cause and remedy beside each finding, so nothing here ever stops a runner.

| Finding | What it means | The fix |
|---|---|---|
| `missing_signal` | A refusal site on the lint's default scan surface is missing one or both of the two inline signals the new-gate rule requires: a census classification comment above it (`BLOCK-WORTHY`, `CONVERT`, or `AGENT-RESOLVABLE`), and a call to the dual-channel error-contract emitter. The likeliest finding for a newly authored gate — it is exactly the omission the gate-authoring contract exists to prevent. | Add whichever signal the finding names: the one-line class comment (`# CONVERT: <one-sentence reason>`), the emitter call carrying the `error`/`message`/`remedy` triple, or both. A resolved anchor also exempts the site from this scan, but see "What the anchor does not excuse" in `team/SOP.md` — that exemption carries the pre-retrofit inventory; it is not permission to skip the signals in new code. |
| `unindexed_anchor` | A `# CENSUS-ANCHOR: <token>` marker sits in a scanned file, but no line in the `CENSUS-LOCATOR-INDEX` block names that token. Half an anchor: the site looks inventoried while this document makes no claim about it. | Add `<path-from-repo-root>:<token> <CLASS>` to the fenced Locator Index block, using the class the site's own inline comment declares. |
| `anchor_class_mismatch` | Both halves of an anchor exist but disagree: the Locator Index entry files the token under one class, and the site's own inline comment declares another. Fixing it means understanding the two-sided anchor contract, not just the two strings. | Decide which side is wrong and correct that one, so the entry and the comment name the same class. The site's doctrine — what its refusal actually catches under The Loop — settles which is right. |
| `unresolved_locator` | A Locator Index entry names `path:key`, but nothing in that file resolves the key: no `CENSUS-ANCHOR` marker carrying that token, and no dual-channel emitter call carrying it as a literal `error` key. The site was deleted, moved to another file, or had its key renamed. This is the one check that walks **every** entry, inventory files included. | Open the file, find the refusal the entry meant, and either add `# CENSUS-ANCHOR: <token>` directly above it — skip that when the token is already the emitter call's literal first argument — or correct the entry to the key that does resolve there. Never delete an entry to silence a real gate. |
| `rollup_mismatch` | The **Rollup** line under the At-a-glance table, or its per-class companion line, disagrees with the table it summarises. Both sides of this comparison live in this document; no code tree is involved. | Edit the two rollup lines to the totals the finding prints — it derives them from the table's own numbered rows, so the corrected line cannot drift from them again. |

The first three walk the default scan surface (`_CENSUS_BASH_FILES` /
`_CENSUS_PYTHON_FILES`), so they never open an inventory file;
`unresolved_locator` walks this document's entries instead, and
`rollup_mismatch` reads this document alone. Each check fails soft: a file it
cannot read or a document half it cannot parse costs the run that one check,
never a crash.

**Block sites have a sibling lint.** Every site that writes a task's
`## State` as `BLOCKED` also records why in the task's `## Block Record`,
through `block_record_write` (`team/scripts/lib/block_record.sh`) or, in
Python, `write_block_record` / `render_block_record`. That is checked by
`team/scripts/lint_block_record_sites.py`, registered in
`run-unit-tests.sh` directly after this lint rather than added as a sixth
finding kind here. It walks every non-test `*.sh` and `*.py` under `team/`
and reports one finding, `blocked-write-without-block-record`, for each
`set_state … BLOCKED`, `transition_task_state … BLOCKED`, `set_block … State
BLOCKED`, `--section State --body BLOCKED` render, or Python `"State"` /
`"BLOCKED"` write with no helper call within the 14 lines from it (or 3
above). The status-writer tool (`task-status.sh --state BLOCKED`) records
its own Block Record and is never reported. Quality class like this one:
it always exits 0. The fix is the `block_record_write … || log` line every
wake block site carries.

The three DEEP-VERIFY tier scripts carry ten such markers between them —
`deep-verify-02` in `team/scripts/deep-verify.sh`,
`deep-verify-freshness-check-01` through `-06` in
`team/scripts/deep-verify-freshness-check.sh`, and `run-cold-build-tests-01`
through `-03` in `team/scripts/run-cold-build-tests.sh`. (`deep-verify.sh`
also carried a belt-and-suspenders `projects_cfg_has` check behind the
shared name resolver's own `project_resolve.not_found` refusal; the
resolver always refuses first, which made the site dead code, so its
marker and Locator Index entry were removed together rather than kept as
an unreachable inventory item.) Every one is indexed below under the
same rule: one line per marker, in the token form, at the class the site
declares for itself. Nine of the ten declare `BLOCK-WORTHY` in an inline
comment directly above the refusal. The tenth, `run-cold-build-tests-01`,
is the env-bootstrap source abort — rollup row 2, which classes that same
`source lib/env_bootstrap.sh || exit 1` prelude `BLOCK-WORTHY` across every
runner that carries it — so it is indexed `BLOCK-WORTHY` from its row
rather than from an inline comment it does not have.

None of the three files is on `_CENSUS_BASH_FILES`, so these ten entries are
inventory in the same sense as the `lib/cm/` stages above: they record where each
refusal lives, and the lint does not scan those files for unannotated gates.
Each token names the marker sitting directly above the refusal — the `exit N`
itself, or, for `run-cold-build-tests-01`, the `source ... || exit 1` line.

`team/scripts/lib/wake_task_transition.sh` carries a twelfth such marker,
`# CENSUS-ANCHOR: atomic-transition-01`, directly above the `return 1` that
refuses `transition_task_state`'s queue-marker write when it fails after the
`## State` write already succeeded — the site rolls the state write back
before this refusal fires, so the two surfaces never disagree.
`wake_task_transition.sh` is not on `_CENSUS_BASH_FILES` either (and this
site is a function `return`, not a script-terminating `exit`), so this entry
is inventory in the same sense as the other twelve: it records where the
refusal lives and its declared class, and the lint does not scan this file
or this refusal shape today.

The same file carries a sibling marker, `# CENSUS-ANCHOR: atomic-transition-02`,
directly above the `return 1` in `_wake_transition_restore_state` that refuses
when the `## State` rollback itself cannot land — the same-directory
scratch-plus-rename restore of the pre-call snapshot failed, so status.md
still reads the new state while the queue marker reads the old one and this
code could not repair it. The site keeps the snapshot on disk and its remedy
names the exact `cp` that restores it; the log never claims "rolled back"
for a rollback that did not land. It shares `atomic-transition-01`'s
inventory status: BLOCK-WORTHY, a function `return`, in a file the lint does
not scan.

`team/pgai_agent_kanban/cm/read_state_field.py` carries a thirteenth marker,
`# CENSUS-ANCHOR: release-state-validators-01`, directly above the `raise
ReleaseStateValidationError(...)` in `load_release_state` — the validating
loader refuses when any release-state.md field (Active RC, RC Opened At,
RC Opened By Task, Last Released) holds a value its schema does not accept,
naming the value received and the values that would have been accepted, plus
a runnable remedy, via the shared dual-channel error contract. Neither
`read_state_field.py` nor its containing directory is on `_CENSUS_PYTHON_FILES`,
so this entry is inventory in the same sense as the `lib/cm/` stages above: it
records where the refusal lives and its declared class, and the lint does not
scan this file today.

`team/scripts/lib/lock_provenance.sh` carries a fourteenth marker,
`# CENSUS-ANCHOR: lock-provenance-01`, directly above the `return 2` in
`pgai_lock_acquire` that refuses to take an advisory flock when any of its
four mandatory provenance fields (holder, pid, op, task) is blank — an
acquisition that cannot name who holds a lock, as what process, doing what,
on behalf of what task, is refused rather than granted, via the shared
dual-channel error contract. `lock_provenance.sh` is not on
`_CENSUS_BASH_FILES`, so this entry is inventory in the same sense as the
other thirteen: it records where the refusal lives and its declared class,
and the lint does not scan this file today.

`team/scripts/lib/lock_provenance.sh` carries a further marker of its own,
`# CENSUS-ANCHOR: lock-provenance-contended-01`, directly above the
`return 1` in `pgai_lock_acquire` that refuses when `flock -n` finds the
lock already held by another process — the sibling refusal to
`lock-provenance-01` above, in the same function: that one refuses an
acquisition with incomplete provenance, this one refuses an acquisition
that cannot be taken at all because someone else holds it. Its remedy
names the `team/scripts/release-locks.sh --release <name>` verb, with the
lock's own basename substituted for `<name>`, and the existing holder's
provenance record (`pgai_lock_describe_provenance`) is folded into the
message. Declared `BLOCK-WORTHY` in an inline comment directly above the
marker, sharing `lock-provenance-01`'s own doctrine family
(invariant-by-design: a caller that cannot take the flock must not
proceed as though it had). `lock_provenance.sh` is not on
`_CENSUS_BASH_FILES`, so this entry is inventory in the same sense as the
fourteen above: it records where the refusal lives and its declared
class, and the lint does not scan this file today.

`team/scripts/lib/lock_provenance.sh` carries a further marker of its
own, `# CENSUS-ANCHOR: lock-stale-threshold-floor-01`, directly above the
`pgai_emit_error_contract` call in `_pgai_lock_resolve_stale_threshold`
that fires when the configured `stale_lock_reap_after_wake_periods`
product resolves below the derived max-chain-budget floor
(`max_runtime_seconds + kill_grace_seconds` plus a fixed margin) — a wake
chain can legitimately hold the lock for its whole batch, so the
age-alone reaper must never be armed below that real budget regardless of
how the wake-periods count is configured. Declared `CONVERT` in an inline
comment directly above the marker: the site clamps the effective
threshold to the floor and narrates the correction rather than refusing
the acquisition (a config value that is merely too aggressive is not
grounds to fail an ordinary lock acquisition) or honoring the configured
value verbatim (which would reintroduce the age-alone reaper firing on a
live, on-budget chain). `lock_provenance.sh` is not on
`_CENSUS_BASH_FILES`, so this entry is inventory in the same sense as the
markers above: it records where the site lives and its declared class,
and the lint does not scan this file today.

`team/scripts/lib/lock_provenance.sh` carries one more marker,
`# CENSUS-ANCHOR: lock-holder-overdue-01`, directly above the
`pgai_emit_error_contract` call in `pgai_lock_acquire`'s contention branch
that fires when a contended lock's file has aged past the stale-lock
threshold while its recorded holder pid still answers `kill -0`. Declared
`CONVERT` in an inline comment directly above the marker: the site
narrates the overdue holder (age, threshold and derivation, the holder's
four provenance fields, and the `release-locks.sh --release <name> --force`
remedy that signals it) and then defers exactly as for any live holder —
it never reaps. Removing a live holder's file does not stop the holder;
it only lets a second chain flock a fresh inode beside it, so the one
reap condition that remains is a lock file with no provenance record that
is not fresh. A dead recorded pid is not one: this branch runs only while
some live process still holds the flock, so a dead recorded pid means an
orphan holds it (a child that outlived its killed wake, say), and the
waiter defers and narrates `lock.provenance.orphan_holder` instead of
reaping. Same inventory posture as the markers above.

`team/pgai_agent_kanban/ops/locks.py` carries six markers in
`release_lock`, the one function `release-locks.sh --release` (through
`pp_run_ops`) and `POST /operations/release-locks` both call; each declares
`BLOCK-WORTHY` inline and raises `LockRefusal` before anything on disk
changes. `locks-release-required-01` (`release_locks.release_required`,
exit 1) refuses a call naming no lock — the script lists instead and the
route refuses 422 first, so it guards direct callers.
`locks-release-not-found-01` (`release_locks.not_found`, exit 3) and
`locks-release-ambiguous-01` (`release_locks.ambiguous`, exit 2) are the
name-resolution refusals common to every operator key: no target, or more
than one, refuses rather than guessing. `locks-release-holder-alive-01`
(`release_locks.holder_alive`, exit 4) refuses releasing a lock whose
recorded pid is still running without `--force`; its message names the
holder's four provenance fields (holder, pid, op, task) and the lock file
is left in place, since a second process could otherwise believe it holds
the same lock. The remaining two,
`locks-release-holder-not-released-01` and `-02`, are the two exit-5 sites
of the `--force` path, which never swaps a live holder's file out from
under it. `--force` sends the recorded holder pid SIGTERM so its own exit
path releases the lock, then waits up to `kill_grace_seconds`; `-01` fires
when the holder cannot be signalled from this account at all, `-02` when
it is still alive and still holding after that wait.
Declared `BLOCK-WORTHY` inline: the override was requested but cannot be
completed without seating a second chain beside a live one, so the file
is left untouched and the remedy names the escalation (`kill -KILL <pid>`
with the real pid interpolated, then a plain `--release` once the holder
is dead). Two sites, two tokens, one machine key
(`release_locks.holder_not_released`) and one class — the same refusal
doctrine applied to both ways the signal can fail to land.

The same file carries a seventh marker, `locks-acquire-provenance-01`, in
`acquire_lock` — the Python form of `pgai_lock_acquire` that the archive
uses to take a project's repo lock. It refuses an acquisition naming a
blank `holder`, `pid`, `op` or `task` (`lock.provenance.incomplete`, the
key the bash helper's `lock-provenance-01` already uses) before any file
is created. Declared `BLOCK-WORTHY` inline for the same reason as its bash
twin: a lock held without a complete record could not be diagnosed when it
wedges. `pid_in_own_ancestry`, which the archive asks when that
acquisition is contended and its caller accepts the chain's own hold,
carries no marker: it answers whether the
recorded holder pid is the caller's own process or an ancestor, and
refuses nothing.

`team/pgai_agent_kanban/cm/cancel.py` carries nine markers in `cancel_rc`
and its configuration readers (plus `cancel-git-remote-name-01`, described
with the other `git_remote_name` refusals below), the one implementation `cm/cancel-rc.sh`
(through `pp_run_ops`) and `POST /operations/cancel-rc` both call; each
declares `BLOCK-WORTHY` inline and raises `CancelRcError` before the step it
guards changes anything. `cancel-confirmation-required-01`
(`cancel_rc.confirmation_required`, exit 1) refuses a call that carries
neither `yes` nor a prompt to ask, before anything is read — an approval by
design, and the only one of the eight that prints the dual-channel block:
the CLI always passes its stdin prompt and the route refuses 422 first
(`operations-cancel-rc-confirm-01`), so it guards direct callers. The other
seven print the plain `ERROR:` lines the CLI has always printed (exit 1),
which the REST twin lifts under its fallback key `cancel_rc.refused`:
`cancel-project-cfg-missing-01` and `cancel-dev-tree-unset-01` (no
repository to act on), `cancel-branch-prefix-01` (a configured
`branch_prefix` that cannot form a branch name), `cancel-version-format-01`
(a key that is not `vX.Y.Z`), `cancel-current-branch-01` (the dev tree is
on neither the main nor the RC branch, so switching or deleting branches
could discard someone's work), `cancel-release-state-missing-01` (no record
of the active RC to check or reset) and `cancel-active-rc-mismatch-01`
(the key is not the active RC — almost always a typo, and hard to undo;
`force` bypasses this one check and leaves `release-state.md`, which then
describes another RC, unchanged). Same inventory posture as the markers
above: `cm/cancel.py` is not on `_CENSUS_PYTHON_FILES`.

The ninth, `cancel-project-not-component-01`, is `cancel_rc`'s first gate:
a project name that is not one plain name (`api/validation.py`'s
`require_component`) refuses dual-channel as `cancel_rc.project_invalid`
(exit 1), before the confirmation gate, before any projects.cfg or
project.cfg is read and before any git command runs -- the name chooses the
repository whose branches the cancel deletes. Declared `BLOCK-WORTHY`
inline, an invariant by design.

`team/pgai_agent_kanban/cm/unwind.py` carries twelve markers in
`unwind_rc` and its configuration readers (plus `unwind-git-remote-name-01`,
described with the other `git_remote_name` refusals below), the one implementation
`unwind-rc.sh` (through `pp_run_ops`) and `POST /operations/unwind-rc` both
call; each declares `BLOCK-WORTHY` inline and raises `UnwindRcError` before
anything is changed. `unwind-confirmation-required-01`
(`unwind_rc.confirmation_required`, exit 1) refuses a live call that carries
neither a confirmation nor a prompt to ask, before anything is read -- an
approval by design, and the only one of the twelve that prints a
dual-channel block of its own: the CLI always passes its stdin prompt and
the route refuses 422 first (`operations-unwind-rc-confirm-02`), so it
guards direct callers. `unwind-project-unresolved-01` prints the project
resolver's own `project_resolve.not_found` / `.ambiguous` block (no single
registered project to unwind). The other ten print the plain `ERROR:` lines
the CLI has always printed (exit 1), which the REST twin lifts under its
fallback key `unwind_rc.refused`: `unwind-project-cfg-missing-01` and
`unwind-dev-tree-unset-01` (no repository to act on),
`unwind-branch-prefix-01` (a configured `branch_prefix` that cannot form a
branch name), `unwind-version-format-01` (a key that is not `vX.Y.Z`),
`unwind-halt-missing-01` (neither the global nor the project HALT is in
place, so the wake scripts could race the rewrite -- an invariant by
design), `unwind-project-dir-missing-01` (no project state to unwind),
`unwind-shipped-tag-01` (the version is a tag: shipped history is never
unwound), `unwind-release-state-missing-01` (no record of the active RC),
`unwind-active-rc-none-01` (nothing active to unwind) and
`unwind-active-rc-mismatch-01` (the key is not the active RC -- almost
always a typo, and destructive; `force` bypasses this one check). Same
inventory posture as the markers above: `cm/unwind.py` is not on
`_CENSUS_PYTHON_FILES`.

`team/pgai_agent_kanban/cm/ship.py` carries fourteen markers in `ship_rc`
and its helpers (plus `ship-rc-git-remote-name-01`, described with the other
`git_remote_name` refusals below), the one implementation `ship-rc.sh` (through `pp_run_ops`)
and `POST /operations/ship-rc` both call; each declares `BLOCK-WORTHY`
inline and raises `ShipRcError` (exit 1) before the step it guards.
`ship-rc.sh` itself keeps only `ship-rc-env-bootstrap-01` and its usage
refusals. `ship-rc-project-required-01` prints the project resolver's own
`project_resolve.not_found` / `.ambiguous` block (no single registered
project to ship). `ship-rc-halt-01` is the one HALT stop, printing a
dual-channel block between the `[ship-rc] HALT` banners and recording it in
the project's refusal ledger: a declared-required hook that is absent (row
24, the release-hook library's `cm-release.hook-required-missing.<phase>`),
a pre-squash or pre-tag hook that is not executable
(`ship-rc.<phase>-hook-not-executable`) and a failing pre-squash hook
(`ship-rc.pre-squash-hook-failed`). `ship-rc-hook-library-unavailable-01`
(`ship_rc.hook_library_unavailable`) prints its own block when the shared
release-hook library cannot be loaded, so no hook could be resolved or
enforced. The other eleven print the plain `ERROR:` lines the CLI has always
printed, which the REST twin lifts under its fallback key `ship_rc.refused`:
`ship-rc-version-format-invalid-01` (a key that is not `vX.Y.Z`),
`ship-rc-branch-prefix-01` (a configured `branch_prefix` that cannot form a
branch name), `ship-rc-not-a-repo-01` (no git repository to ship from),
`ship-rc-dirty-worktree-01` (uncommitted changes would fold into the
release), `ship-rc-tag-exists-01` (the version is already tagged),
`ship-rc-branch-missing-local-01` and `ship-rc-branch-missing-origin-01`
(no RC branch to ship, or one origin has never seen -- the origin half is
skipped under `push_to_remote = false`), `ship-rc-git-step-failure-01`
(every named git step stops at its first failure),
`ship-rc-squash-conflict-01` (a squash conflict the modify/delete resolver
could not clear), `ship-rc-fidelity-gate-divergence-01` (main and the RC
trees differ after the squash, so a mid-RC main commit would be dropped)
and `ship-rc-hook-failure-01` (a failing pre-tag hook). Same inventory
posture as the markers above: `cm/ship.py` is not on
`_CENSUS_PYTHON_FILES`.

The `git_remote_name` refusals. `project.cfg [project] git_remote_name`
names the remote every CM fetch, `ls-remote`, push and `<remote>/<branch>`
ref uses; absent or blank it is `origin`. A value that is not a valid remote
name (letters, digits, `.`, `_` and `-`, starting with a letter or digit, no
`..`, no trailing `.` or `.lock`) is refused dual-channel as
`git_remote_name.invalid`, naming the key, the value and the file, with the
one-line edit that fixes it as the remedy. Two resolver sites own the block,
byte-identical in both languages: `git-remote-name-invalid-01`
(`team/pgai_agent_kanban/lib/git_remote.py`, `remote_name_from_cfg`, which
raises `InvalidRemoteName` carrying the formatted block) and
`git-remote-name-invalid-02` (`team/scripts/lib/project_paths.sh`,
`pp_git_remote_name`, which prints it through the shared emitter and
records it in the project's refusal ledger, unless called `--quiet`). Eight
caller sites stop on that refusal before any git command runs, each
re-raising or exiting with the resolver's block already printed:
`cancel-git-remote-name-01`, `ship-rc-git-remote-name-01` and
`unwind-git-remote-name-01` (the three Python CM verbs, exit 1),
`open-rc-git-remote-name-01` (`team/scripts/cm/open-rc.sh`, exit 1),
`release-preflight-git-remote-name-01`,
`release-squash-git-remote-name-01` and
`release-finalize-git-remote-name-01` (the release preflight, squash and
finalize stages, exit 1; preflight refuses only on a run that will contact
the remote), and `init-project-git-repo-git-remote-name-01`
(`team/scripts/init-project-git-repo.sh`, exit 2). All are `BLOCK-WORTHY`,
declared inline: a value that cannot name a remote would hand git an
option, a path or a URL where the release expects its remote -- a push to,
a branch delete on, or a fetch from a destination nobody configured -- so
it is refused rather than guessed. Read-only callers (the released-version
lookups, the dashboard, `check-push-lag.sh` and `cm/push-watchdog.sh`)
resolve `--quiet` and skip their remote step with one narrated line rather
than refusing; they add no refusal site. Inventory in the same sense as the
CM verb markers above.

`team/scripts/lib/wake_task_id_gate.sh` carries two markers.
`# CENSUS-ANCHOR: wake-task-id-gate-malformed-queue-id-01` sits directly
below the `pgai_emit_error_contract` call in
`_narrate_malformed_queue_task_ids`, the one narration site
`wake_task_state.sh`'s pending-task scan (`get_first_pending_task`), WAITING
scan (`recheck_waiting_tasks`) and BLOCKED scan (`recheck_blocked_tasks`), and
`wake_reconcile_sweeps.sh`'s stale-active scan (`reconcile_stale_active`), all
call before they read ids out of the queue;
`# CENSUS-ANCHOR: wake-task-id-gate-malformed-prerequisite-id-01` sits below
the call in `_narrate_malformed_prerequisite_ids`, which `check_prerequisites`
(same file) calls for every `<token>:MALFORMED` entry its parse produced.
Both are declared `CONVERT` inline (row 69): an id outside the canonical
shape `task_ids.sh`'s `kanban_task_id_pattern` prints is narrated naming the
file and line, then skipped — never joined onto `TASKS_ROOT` — and the wake
continues serving every well-formed line. `wake_task_id_gate.sh` is not on
`_CENSUS_BASH_FILES` (its python heredocs carry ordinary non-refusal
`SystemExit` exits the line-window scan would misread as unsignalled
sites), so both entries are inventory in the same sense as the markers
above.

`team/scripts/lib/worktree.sh` carries one marker,
`# CENSUS-ANCHOR: worktree-residue-outside-base-01`, directly above the
`return 1` in `_wt_clear_task_worktree_path` — the pre-clean that
`create_task_worktree` and `create_detached_worktree` both run before `git
worktree add` when something already sits at the task's worktree path.
Declared `BLOCK-WORTHY` inline (row 70): the check runs before any removal,
by git (`git worktree remove --force`) or by `rm -rf` (the fallback for a
directory git does not know as a worktree), and the removal goes ahead only
when `pgai_worktree_path_strictly_inside` accepts the path as a strict
descendant of the worktree base after canonicalising both with `realpath`
(symlinks resolved, `..` collapsed); the `rm -rf` fallback re-checks at the
delete itself. A path that is the base itself, its parent, or a symlink
resolving outside the base is refused with the path, the base and an
`ls -ld`/`realpath` remedy interpolated, nothing is removed, and the create
returns non-zero. `worktree.sh` is not on `_CENSUS_BASH_FILES`; same
inventory posture as the markers above.

`team/scripts/lib/lock_provenance.sh` carries a further marker of its
own, `# CENSUS-ANCHOR: lock-provenance-reverify-01`, directly above the
`return 1` in `pgai_lock_reverify_or_narrate` that fires when a chain's
cheap, read-only re-check of its own project repo lock (`pgai_lock_still_
held`) finds a DIFFERENT holder's provenance now on disk — a wake chain
that was reaped and had its lock reacquired by another process while it
kept running. Declared `BLOCK-WORTHY` in an inline comment directly
above the marker, the same doctrine family as
`lock-provenance-01`/`lock-provenance-contended-01` above: a chain
proceeding as though it still held a lock it does not is exactly the
state-corrupting double-writer failure (two chains racing the same dev
tree and queue files) this fix exists to prevent. The three call sites
(`team/scripts/lib/wake_driver.sh`'s `run_project_chain` before its own
per-iteration housekeeping and `process_one_task` after the dispatched
agent returns, and `team/scripts/lib/wake_driver_finalize.sh`'s
`wd_finalize_task` immediately before the queue-marker write) each stop
that one mutating phase — dispatch, merge, queue write — without
performing it, narrated dual-channel naming the new holder's four
provenance fields. `lock_provenance.sh` is not on `_CENSUS_BASH_FILES`,
so this entry is inventory in the same sense as the markers above: it
records where the site lives and its declared class, and the lint does
not scan this file today.

`team/scripts/lib/lock_provenance.sh` carries a further marker of its
own, `# CENSUS-ANCHOR: lock-provenance-release-not-owner-01`, directly
above the `pgai_emit_error_contract` call in `pgai_lock_release` that
fires when a release -- an explicit call, or the exit-time
`pgai_lock_release_all_held` sweep -- finds that the file at the lock
path is no longer the inode this process holds the flock on: another
process reaped the lock and reacquired it (a new file at the path) while
this one kept running, or the path is gone. Declared `CONVERT` in an
inline comment directly above the marker: nothing is refused. The
release closes its own fd and drops its bookkeeping exactly as
`pgai_lock_abandon` does after a reverify loss, and leaves the file at
the path to whoever holds it, because clearing or unlinking it would
free the lock out from under a live holder mid-run and hand the loss on
to the next chain. `lock_provenance.sh` is not on `_CENSUS_BASH_FILES`,
so this entry is inventory in the same sense as the markers above: it
records where the site lives and its declared class, and the lint does
not scan this file today.

`team/scripts/lib/overwatch_protocol.sh` carries a fifteenth marker,
`# CENSUS-ANCHOR: zero-progress-cap-01`, directly above the
`if transition_task_state "$task_id" "$task_status" "BLOCKED"; then` in
`zero_progress_advance_or_escalate` — the shared decision-and-transition
core `reconcile_stale_active` (`wake_reconcile_sweeps.sh`) and
`handle_working_after_exit`'s non-transient fallback (`wake_lifecycle.sh`)
both call. Below the durable zero-progress cap it resets a task to BACKLOG
for another automatic attempt; at the cap it stops resetting and raises the
task to BLOCKED + Needs Human=yes instead, naming the count and the ceiling
via the shared dual-channel error contract — an invariant-by-design stop
(a task that has learned nothing in N consecutive cycles is not healed by
another automatic reset). Both callers share this one physical site, so
there is exactly one anchor for the escalation rather than one per caller.
`overwatch_protocol.sh` is not on `_CENSUS_BASH_FILES`, so this entry is
inventory in the same sense as the other fourteen: it records where the
refusal lives and its declared class, and the lint does not scan this file
today.

`team/scripts/cm/release.sh` carries a sixteenth and seventeenth marker,
`# CENSUS-ANCHOR: operator-force-reason-required-01` and
`# CENSUS-ANCHOR: operator-force-wake-context-01`, each directly above its
own `exit 1`, refusing the operator-only `--force`/`--reason` ship override
when the reason is missing/blank or when
the invocation carries the `PGAI_AGENT_WAKE_ACTIVE=1` agent-wake signal —
both declared `BLOCK-WORTHY` in an inline comment directly above the
marker: an approval-by-design invariant, not a judgement call, so passing
either would let the override run unattributed or from an automated
context. The file's third refusal, inside `git_step`, carries its own
`# CENSUS-ANCHOR: release-git-step-failure-01` marker and the same
`BLOCK-WORTHY` treatment (a git operation failing mid-release is stopped
rather than papered over). Unlike the fifteen markers above, `cm/release.sh`
IS on `_CENSUS_BASH_FILES`, so all three entries are lint-consumed by a
named-token match below rather than a raw line number, immune to the site
moving within the file.

`team/scripts/cm/release.sh`'s `operator-force-wake-context-01` site makes
one narrow exception, for OVERWATCH's Lane B declared-intent ship path: the
agent-wake refusal it names fires for every `PGAI_AGENT_WAKE_ACTIVE=1`
invocation EXCEPT the one that passes `--overwatch-lane-b-ship` AND whose `--reason` independently
verifies (`_cm_release_lane_b_intent_verified`, reading the resolved
project's live `INTENT.md` through the same
`pgai_agent_kanban.overwatch.intent.parse_entries` Lane B's own classifier
uses) as the verbatim declaration text of a live entry — no entry means no
force, ever, from OVERWATCH's own judgment. `operator-force-wake-context-01`
is `BLOCK-WORTHY` (an approval-by-design invariant: the operator override
must never be reachable from an automated agent context outside this one
named exception) and fires, unnarrowed, for a bare `--force`
under wake context carrying no Lane B claim at all. The file carries a
fourth marker of its own for the narrow admission's OWN failure mode —
`# CENSUS-ANCHOR: overwatch-lane-b-intent-unmatched-01`, directly above the
`exit 1` reached when `--overwatch-lane-b-ship` IS present but
`_cm_release_lane_b_intent_verified` does not confirm the match — declared
`BLOCK-WORTHY` in an inline comment directly above its own marker for the
same reason: OVERWATCH's own Lane B claim must be independently proven
against the live ledger before the override is admitted, an unverified
claim is refused exactly like a bare unauthorized `--force`, and passing
the flag alone must never be sufficient. Both sites emit through the shared
dual-channel error contract and are lint-consumed by `cm/release.sh`'s
existing `_CENSUS_BASH_FILES` membership, same as the file's other three
markers.

`team/pgai_agent_kanban/overwatch/resolution_stage.py` carries a marker of
its own, `# CENSUS-ANCHOR: lane-a-rewrite-precondition-01`, directly above
the `return ResolutionStageOutcome(status=DECLINED, ...)` in `_run_lane_a()`
reached when the Lane A recovery drill's blamed bug does not yet carry an
attributed OVERWATCH Fix rewrite. Lane A's destructive steps (b)-(e) —
unwind the RC, cancel it, delete the bundle document, clear the
ship-policy HALT — are irreversible against the
bundle's own retry story (the single-attempt guard never gets a second
try), so they must never run on the strength of an assumption; the site is
declared `BLOCK-WORTHY` in an inline comment directly above its own marker
for the same reason `overwatch-lane-b-intent-unmatched-01` above is: an
approval-by-design invariant, not a judgement call the deterministic stage
can make for itself. The judgement call — naming the failed approach and
the correct direction — belongs to the reasoning tier alone
(`team/roles/OVERWATCH.md`, "Performing that rewrite is the reasoning
tier's duty, not the drill's"), performed out-of-band via
`team/scripts/overwatch-attach-fix-rewrite.sh` (wrapping
`pgai_agent_kanban.overwatch.fix_rewrite.rewrite_bug_fix_section()`) or a
hand-edit carrying the same shape; the guard only ever checks for that
step's *result* (`has_attributed_rewrite()`), never derives the content
itself. The refusal text is built by `_format_lane_a_rewrite_missing()`
through the shared `format_error_contract()` emitter, substituting every
value the stage already resolved (`--project`, `--kanban-root`,
`--bug-id`) into the remedy verbatim and naming the three judgement-call
flags (`--forbidden-approach`, `--correct-direction`, `--ground-truth-ref`)
without an angle-bracket placeholder, per `lint_runnable_remedy.py`'s own
rule. This file is not on `_CENSUS_PYTHON_FILES` (the missing-signal scan's
maintained include-list), so this entry is inventory-only, the same
standing as `overwatch_protocol.sh`'s marker above: it records where the
refusal lives and its declared class, and the lint's missing-signal scan
does not walk this file today — only the reverse locator-resolution check,
which reads the entry's own path directly, applies to it.

`team/scripts/lib/wake_lifecycle.sh` carries an eighteenth marker,
`# CENSUS-ANCHOR: completion-gate-empty-commit-01`, directly above the
`return 0` that refuses a CODER/WRITER DONE-claim when the feature
branch's own commits (isolated via `<source_branch>^..<feature_branch>`,
valid only once the gate has confirmed by direct SHA comparison that
`<source_branch>`'s tip is that task's own --no-ff merge commit) carry an
empty non-merge commit, declared `AGENT-RESOLVABLE` in an inline comment
directly above the marker — a third check alongside the pre-existing
merge and cleanliness checks in `coder_writer_completion_gate` (rows 6-7),
sharing that function's own class: fatal to the current done-claim, but
self-serviceable by the agent in the same session (drop the empty commit,
re-merge, re-complete). `wake_lifecycle.sh` is not on `_CENSUS_BASH_FILES`
(rows 6-7's include-list entry names `lib/wake_common.sh`, not this file,
which holds `coder_writer_completion_gate` — see that file's own header), so this entry is inventory in the
same sense as the other seventeen: it records where the refusal lives and
its declared class, and the lint does not scan this file today.

`team/scripts/defer.sh`, `team/scripts/undefer.sh`, and `team/scripts/why.sh`
— the arm, clear, and read-only explainer/verdict verbs over the
deferral-posture resolver — and `team/pgai_agent_kanban/ops/write.py`, whose
`defer` / `undefer` functions both the two scripts (through `pp_run_ops`) and
`POST /operations/defer` / `undefer` call, carry eight further markers between
them: `defer-arm-project-required-01` in `defer.sh`;
`undefer-project-required-01` in `undefer.sh`; `defer-arm-until-required-01`,
`defer-arm-until-malformed-01`, and `defer-project-not-found-01` (one site
for both verbs, keyed `defer.project_not_found` / `undefer.project_not_found`)
in `ops/write.py`; `why-project-not-found-01` and
`why-details-requires-project-01` in `why.sh`. The `ops/write.py` sites raise
rather than exit, and both scripts exit 1 on them. Every one declares
`BLOCK-WORTHY` in an inline comment directly above its own marker and emits
through the shared dual-channel error contract: each names either an
unidentifiable target (a project with no directory, or `--details` with no
project named — a resolved deferral posture is always per-project, even
though `--project` is optional for the summary-line default) or, in
the defer verb only, a malformed or absent `--until` value refused at write
time — the earlier of the two validation points, ahead of the resolver's
own read-side refusal on the same malformed shape
(`defer-posture-campaign-malformed-until-01` above). None of the four
files is on `_CENSUS_BASH_FILES` / `_CENSUS_PYTHON_FILES`, matching the census's own scope decision
for pure operator-CLI argument/usage refusals in interactive utilities
that the autonomous loop never invokes (`halt.sh`, `unhalt.sh`, and
`halt-after.sh` are excluded the same way) — so these eight entries are
inventory in the same sense as the eighteen above: they record where each
refusal lives and its declared class, and the lint does not scan these
files today.

`team/pgai_agent_kanban/ops/write.py` carries two more markers for the
configuration verbs whose functions both the scripts (through `pp_run_ops`)
and the REST routes call: `ceiling-invalid-value-01` in `set_version_ceiling`
(`set_version_ceiling.invalid_ceiling`, a `--minor` / `--major` value that is
not a non-negative integer, refused before project.cfg is touched) and
`switch-provider-unknown-01` in `switch_provider`
(`switch_provider.unknown_provider`, a provider with no wake script, refused
before kanban.cfg is touched). Both declare `BLOCK-WORTHY` inline: each keeps
a value the reader downstream cannot use (discovery's version gate, the
wake dispatcher) out of the operator's config. Inventory: a `raise`, not a
`sys.exit`; `set-version-ceiling.sh` and `switch-provider.sh` exit 1 on them.

The same file also carries `write-close-invalid-state-01`, in
`_check_close_state`. `close_item` calls it first, before the key is
resolved or anything is written, and both `close.sh` (through `pp_run_ops`)
and `POST /operations/close` call `close_item`, so the CLI and the route
refuse alike. It refuses any state that is not one of the closed states,
`done` and `wont-do`, under `close.invalid_state`. The retired alias
`superseded` gets a remedy that closes the item as `wont-do` with a
`--note` saying why; any other value gets a remedy that closes it as
`done`. Both remedies name the real project
and key, so they run as printed. Declared `BLOCK-WORTHY` inline: any other
value would leave a status no reader treats as closed. Inventory: a
`raise`, not a `sys.exit`; `close.sh` exits 1 on it.

The same file carries `write-intake-filename-not-bare-01`, in
`route_intake_name`, the intake name check `deposit_intake` runs before it
copies anything and `POST /operations/intake` runs before it writes its temp
file. A staged file whose name is not a bare file name -- it carries `..`,
a backslash, a URL-encoded separator or a NUL, whatever its prefix -- is
refused under `intake.filename_not_bare`, with a remedy that renames the
staged file to `BUG-0001-example.md` and re-runs `intake.sh` on it. The
check is `lib/filename_contract.py`'s `intake_destination`, shared with the
route's own pre-write refusal (`operations-intake-filename-not-bare-01`
above), so the CLI and the API refuse the same names. Declared
`BLOCK-WORTHY` inline: the deposited name is joined under the project
directory, so a name that is a path could land outside it. Inventory: a
`raise`, not a `sys.exit`; `intake.sh` exits 2 on it.

`team/pgai_agent_kanban/ops/projects.py` carries five markers. Two are in
`remove_project`'s Active-RC guard, the one function `remove-project.sh`
(through `pp_run_ops`) and `POST /operations/remove-project` both call:
`projects-remove-active-rc-01` (`remove_project.active_rc`, a project whose
release-state.md records an Active RC) and
`projects-remove-active-rc-invalid-01` (`remove_project.release_state_invalid`,
an Active RC field `cm/read_state_field.py` refuses to validate). Both
declare `BLOCK-WORTHY` inline: removing a project with a release in flight
discards the RC's work, and an unreadable field may be hiding one. The
guard holds with and without `--force`/`force: true` and refuses before
projects.cfg or the directory is touched. Inventory: a `raise`, not a
`sys.exit`; the script and the route report exit code 2.

The other three confine the registry verbs' inputs, each raising
`ProjectValueRefused` (exit 1, a dual-channel `<verb>.<field>_invalid`
block) before the filesystem is touched. `projects-name-not-component-01`
in `check_project_name` refuses an `add_project` / `remove_project` name
that is not one plain name made of letters, digits, `_` and `-`
(`add_project.project_invalid`, `remove_project.project_invalid`), so `..`
never reaches `shutil.rmtree` and `../x` never reaches projects.cfg.
`projects-workflow-type-not-component-01` and `projects-cfg-value-invalid-01`
in `check_create_values` refuse a `workflow_type` that is not one plain name
(it is then matched by name among the ready plugins, never joined into a
path first) and a `git_remote`, `dev_tree` or `git_repo` holding a newline
or other control character, or a `dev_tree` that is relative or has a `..`
segment (`create_project.<field>_invalid`). All three declare
`BLOCK-WORTHY` inline: the values become a deleted directory, a sourced
`workflow.sh` or project.cfg lines. Inventory: a `raise`, not a `sys.exit`.

`team/pgai_agent_kanban/lib/defer_posture.py`, the Python resolver of the
deferral posture, carries the same four markers as the bash resolver
`team/scripts/lib/defer_posture.sh` — `defer-posture-campaign-unparseable-01`,
`defer-posture-campaign-malformed-until-01`, `defer-posture-invalid-boolean-01`
and `defer-posture-invalid-level-01` — each directly above a
`raise DeferPostureError(...)` whose text is the same dual-channel block the
bash site prints, and each declared `BLOCK-WORTHY` inline for the bash sites'
reasons: an unparseable or malformed campaign file, or an unaccepted
`[release]` value, is refused rather than resolved to a posture nobody asked
for. Inventory: a `raise`, not a `sys.exit`, in a file the missing-signal
scan does not walk.

`team/scripts/halt-after.sh` carries two further markers of its own —
`halt-after-until-scope-mismatch-01` and `halt-after-until-malformed-01` —
added when its `--until vX.Y.Z` flag shipped, arming the same versioned
`rc:vX.Y.Z` token `drain.py`'s own in-flight-RC capture produces (see
`pgai_agent_kanban/halt_after/token.py`'s `describe_token`). Both declare
`BLOCK-WORTHY` in an inline comment directly above their own marker and
emit through the shared dual-channel error contract: the scope-mismatch
site refuses `--until` combined with any of the five role tokens
(`pm`/`coder`/`writer`/`tester`/`cm`) — a version names a release boundary
those scopes do not have, so honouring the flag there would mean silently
ignoring it or silently promoting the scope to `rc`; the malformed-value
site refuses a `--until` value that is not well-formed semver, the same
write-time-before-resolver-backstop invariant `defer-arm-until-malformed-01`
above enforces. `halt-after.sh` is not on `_CENSUS_BASH_FILES` (see the
paragraph above), so these two entries are inventory in the same sense as
the eight before them: they record where each refusal lives and its
declared class, and the lint does not scan this file today.

`why.sh` has no unconditional "no project named" refusal: `--project` is
optional at the top level, and its absence means "walk every registered
project" through the verdict engine (`team/pgai_agent_kanban/ops/verdict.py`).
The narrower `why-details-requires-project-01` above covers the one case
that is an unidentifiable target: `--details` reports one project's
resolved posture and has nothing to resolve against with none named.

`team/scripts/lib/env_bootstrap.sh` carries a further marker,
`# CENSUS-ANCHOR: env-bootstrap-claude-env-01`, directly above the
`return 1` that refuses the shared bootstrap prelude when the optional
`claude-env` file (see `claude-env_example`) exists but fails a `bash -n` syntax check run before
it is sourced — declared `BLOCK-WORTHY` in an inline comment directly
above the marker: `source` executes a script's earlier top-level
statements as it parses along, so sourcing a syntactically broken file
directly (rather than checking it first) can silently half-configure the
caller's environment, and a cron or pseudocron firing has no human present
to notice a partial environment. This is a distinct site from rollup row 2
(the `source lib/env_bootstrap.sh || exit 1` callers use to invoke this
prelude in the first place) — row 2 covers the prelude being unreachable;
this marker covers an optional file the prelude itself sources going bad.
`env_bootstrap.sh` is not on `_CENSUS_BASH_FILES`, and this site is a
function `return`, not a script-terminating `exit`, so this entry is
inventory in the same sense as `wake_task_transition.sh`'s
`atomic-transition-01` above: it records where the refusal lives and its
declared class, and the lint does not scan this file or this refusal shape
today.

`team/scripts/lib/wake_bootstrap.sh` carries a sibling marker of its own,
`# CENSUS-ANCHOR: wake-bootstrap-claude-env-01`, directly above the `exit 1`
that stops a wake firing when `claude-env` exists but fails the identical
`bash -n` syntax check. This is a distinct site from
`env-bootstrap-claude-env-01` immediately above, not a duplicate: the
wake-path caller (`wake_bootstrap_init`) always pre-exports
`PGAI_AGENT_KANBAN_ROOT_PATH` before sourcing `env_bootstrap.sh` (so the
`BASH_SOURCE`-derivation walk never lands on the wrong directory when
`wake_bootstrap.sh` itself is the sourcing file — see the "Why pre-export"
comment at that call site), which trips `env_bootstrap.sh`'s own
idempotency guard and returns before that file's claude-env block is ever
reached. `wake_bootstrap.sh` re-sources `claude-env` directly, immediately
after its existing `shell-env` re-source, reproducing the same
syntax-check-before-source contract so the wake path actually honors the
file the way `claude-env_example` documents ("sourced ... on every entry
path that resolves `PGAI_AGENT_KANBAN_ROOT_PATH` — including a cron or
pseudocron firing"). Declared `BLOCK-WORTHY` in an inline comment directly
above the marker, for the same reason as the sibling site: a half-sourced
claude-env is a state-corrupting failure a cron or pseudocron firing has no
human present to notice. `wake_bootstrap.sh` is not on `_CENSUS_BASH_FILES`,
so this entry is inventory in the same sense as the sibling site above: it
records where the refusal lives and its declared class, and the lint does
not scan this file today.

`team/scripts/lib/discovery_guards.sh` carries six further markers of its
own — `discovery-guards-release-state-unreadable-01` and
`discovery-guards-active-rc-in-flight-01` in `_disc_active_rc_blocks`, and
`discovery-guards-ceiling-malformed-01`, `discovery-guards-ceiling-major-01`,
`discovery-guards-ceiling-minor-01`, and `discovery-guards-ceiling-patch-01`
in `_disc_check_ceiling_and_log`. Each site narrates through `log()` and
also speaks dual-channel via `pgai_emit_error_contract`, so each refusal is
recorded queryable (`team/scripts/lib/refusal_ledger.sh`) rather than
living only in a rotating wake log. The dual-channel call is
narration-only: it changes neither the site's `log()` line nor its return
value (a `warn-and-continue` skip stays a skip), so it leaves discovery's
control flow untouched. Both
`_disc_active_rc_blocks` markers are declared `BLOCK-WORTHY` in an inline
comment directly above each, sharing rollup row 5's own doctrine family
("Active-RC single-flight guard" — invariant-by-design: allowing PM to
decompose a second release while an RC is in flight would inject unbounded
mid-release scope creep) — the release-state-unreadable site is the
sibling "cannot verify RC state, so conservatively block" condition inside
the same function. The four `_disc_check_ceiling_and_log` markers are each
declared `BLOCK-WORTHY` in their own inline comment: approval/
invariant-by-design — the operator explicitly capped `max_major`/
`max_minor`/`max_patch` (or, for the malformed-version site, discovery
cannot check an unparseable version against any ceiling at all), and
honoring that cap by refusing to queue PM past it is the point of the
setting. `discovery_guards.sh` is not on `_CENSUS_BASH_FILES` (only its
sibling `lib/discovery.sh` is, for rows 3-5), so all six entries are
inventory in the same sense as the eighteen-plus markers above: they
record where each refusal lives and its declared class, and the lint does
not scan this file today.

`team/scripts/lib/discovery_steps.sh` and `team/scripts/lib/discovery_state.sh`
each carry a marker guarding the bug/priority bundle writer against
non-path content (narration, or any other stray text) in a candidate list.
The pre-filter shape check runs on the candidate list before
`_disc_bug_deferral_filter`, so on its own it never sees what the filter
itself puts on stdout. `discovery-steps-bug-item-shape-01` and
`discovery-steps-priority-item-shape-01` in `discovery_steps.sh` cover
that: each validates, via the shared `_disc_bundle_item_shape_ok`
predicate, that the finalized item list `discovery_step_bugs` /
`discovery_step_priority` is about to hand to `_disc_write_bundle` is
shaped like real bundleable items — an absolute path, optionally carrying
the `RIDE_ALONG:<trigger-id>` wire-format annotation — refusing the whole
bundle on the first line that is not, rather than silently dropping the
bad line and shipping a partial bundle.
`discovery-state-write-bundle-no-valid-items-01` in `_disc_write_bundle`
(`discovery_state.sh`) is the last-line-of-defence sibling: it refuses to
write any bundle file when zero entries in its own `files` argument
resolve to an existing regular file, so a future direct caller that
bypasses both step-level guards still cannot produce a header-only bundle
with an empty `## Bundled Items` section. All three are declared
`CONVERT` in an inline comment directly above each refusal: the step
declines to bundle this tick and returns non-zero to its own in-process
caller — precisely the shape of the pre-existing emptiness/shape checks
each guard sits beside — never creating a HALT flag or altering task
state; the next wake retries automatically once whatever produced the
malformed line is fixed. Neither `discovery_steps.sh` nor
`discovery_state.sh` is on `_CENSUS_BASH_FILES`, so these three entries
are inventory in the same sense as the `discovery_guards.sh` markers
immediately above: they record where each refusal lives and its declared
class, and the lint does not scan either file today.

`team/scripts/lib/discovery_steps.sh` carries two further markers of its
own — `discovery-steps-bug-mint-governor-01` in `discovery_step_bugs` and
`discovery-steps-priority-mint-governor-01` in `discovery_step_priority` —
the belt-and-braces mint governor closing the gap the item-shape/payload
guards above do not: those guard the shape and cargo of ONE bundle already
being written, but neither stops a SECOND bundle of the same category from
being minted while an earlier one of that category is still sitting
unprocessed in `requirements/` (Status `open` or `running`) — the
"minting storm" a bug arriving mid-flight could otherwise trigger, each
new item claiming its own patch slot instead of waiting for the pending
slot to drain. Both sites call the shared `_disc_pending_bundle_of_type`
predicate (same file) — which scans `requirements/` for a bundle file
matching this category's own naming convention
(`*-bugfix-bundle-*.md` / `*-priority-bundle-*.md`) and reports the first
one whose own `## Status` header has not reached a terminal state (`done`
or `wont-do` — the same two-value terminal set `discovery_items.sh`'s
`is_terminal()` and `discovery_eligibility.sh`'s eligibility scan already treat
as definitively resolved) — immediately after each step's Active-RC guard
and before either step computes a target version, so a declining pass
never touches the requirements directory, the incoming item's own file, or
any queue file. Both are declared `CONVERT` in an inline comment directly
above each refusal, the same classification and the same reasoning as the
three sibling markers immediately above: a pass finding the slot already
taken has behaved correctly, the step declines to bundle this tick and
returns non-zero to its own in-process caller, never creating a HALT flag
or altering task state, and the next wake retries automatically once the
pending bundle's own `## Status` reaches a terminal state.
`discovery_steps.sh` is not on `_CENSUS_BASH_FILES`, so
these two entries are inventory in the same sense as the other markers in
this file: they record where each refusal lives and its declared class,
and the lint does not scan this file today.

`team/pgai_agent_kanban/task_status.py` carries two markers of its own —
`# CENSUS-ANCHOR: task-status-invalid-state-01` directly above the `raise
TaskStatusError(...)` in `validate_state` that refuses a `--state` value
outside the canonical six, and `# CENSUS-ANCHOR:
task-status-missing-required-arg-01` directly above the single `raise
TaskStatusError(...)` in `validate_required_text` that every missing
`--project`/`--key`/`--state` presence check shares — mirroring
`overwatch_protocol.sh`'s `zero-progress-cap-01` precedent of one anchor
for one physical site serving multiple callers. Both are declared
`AGENT-RESOLVABLE` in an inline comment directly above each marker: a
caller that mistypes `--state` or omits a required flag corrects its own
next invocation in the same session — no human is needed. `task_status.py`
is not on `_CENSUS_PYTHON_FILES` (that list is resolved relative to
`team/scripts/`, and this module lives under `team/pgai_agent_kanban/`), so
these two entries are inventory in the same sense as the
`read_state_field.py`/`lock_provenance.sh`/`overwatch_protocol.sh` markers
above: they record where each refusal lives and its declared class, and
the lint does not scan this file today.

A third marker, `# CENSUS-ANCHOR: task-status-subcommand-refusal-01`, sits
directly above the single `raise TaskStatusError(...)` in `_refuse`, the
one site every `read` / `set-section` subcommand refusal shares (a missing
or conflicting target, an absent status.md, a missing `--section` or
`--body`, a `## State` write routed away from `--state`). It is declared
`AGENT-RESOLVABLE` for the same reason as the two above: the caller fixes
its own next invocation, and nothing is written on refusal.

A fourth, `# CENSUS-ANCHOR: task-status-reason-code-refusal-01`, sits
directly above the single `raise TaskStatusError(...)` in
`_refuse_block_input`, the one site every `--reason-code` /
`--action-args` check in `validate_block_inputs` shares (either flag on a
state other than BLOCKED, a code outside the closed `agent_<...>` list,
`--action-args` without `agent_operator_data_action` or that code without
them, and an unknown, repeated, missing or malformed action argument).
Declared `AGENT-RESOLVABLE` like its three siblings: the agent corrects its
own next invocation, and status.md is unchanged on refusal.

`team/pgai_agent_kanban/overwatch/block_record.py` carries one marker,
`# CENSUS-ANCHOR: block-record-refusal-01`, directly above the single
`raise BlockRecordError(...)` in `_refuse` that every `## Block Record`
refusal shares (an unknown `reason_code` or `owner_role`, a missing
status.md, an update with nothing to change or no record to change, a
malformed update value). It is declared `CONVERT` in an inline comment:
a refused Block Record write leaves status.md untouched and the block
site's own BLOCKED transition goes ahead, so the cost is a missing triage
record, never a stopped task or release. The module is not on
`_CENSUS_PYTHON_FILES`, so the entry is inventory in the same sense as the
`task_status.py` markers above.

`team/pgai_agent_kanban/overwatch/block_triage.py` carries one marker,
`# CENSUS-ANCHOR: block-triage-refusal-01`, directly above the single
`emit_error_contract` call every block-triage executor refusal shares (a
missing project root or status.md, a malformed task key, or writes that
could not finish). It is declared `CONVERT` in an inline comment: the
calling sweep check performs no state mutation after a refused `apply`, so
the task keeps its state and the cost is one task not triaged this firing,
never a stopped wake or release. The module is not on
`_CENSUS_PYTHON_FILES`, so the entry is inventory in the same sense as the
`block_record.py` marker above.

A present HALT defers OVERWATCH's fixes, and three markers inventory where.
`team/scripts/lib/overwatch_protocol.sh` carries
`# CENSUS-ANCHOR: overwatch-fix-deferred-halt-01`, directly above the
`pgai_emit_error_contract` call (key `overwatch.fix_deferred_halt_present`)
in `_overwatch_hff_narrate_deferral`, the one narration
`overwatch_halt_first_fix` makes when the fleet-wide HALT, or a project HALT
it did not create, defers a fix (sentinel 5; only the ship-policy resolution
opt-in runs under a project HALT). `team/scripts/lib/wake_common.sh` carries
`# CENSUS-ANCHOR: wake-common-overwatch-halt-01` (key
`wake.overwatch_fix_deferred_halt_present`), where the overwatch wake exits
0 under the fleet-wide HALT before any project's chain starts, and
`team/scripts/lib/wake_project.sh` carries
`# CENSUS-ANCHOR: wake-project-block-triage-halt-01` (key
`wake.overwatch_block_triage_deferred_halt_present`), where
`run_overwatch_block_triage` skips a project with a HALT present. All three
are declared `BLOCK-WORTHY` inline: an invariant by design, an operator's
(or CM's) stop that only the operator removes; nothing is changed and the
sweep or wake carries on read-only. Each remedy prints the HALT file
(`cat <the HALT path>`, interpolated). None of these files is on the
lint's default lists, so the entries are inventory in the same sense as the
markers above.

`team/pgai_agent_kanban/overwatch/worktree_residue.py` carries
`# CENSUS-ANCHOR: worktree-residue-finished-refusal-01` and
`team/scripts/lib/overwatch-checks/check-stale-worktrees.sh` carries
`# CENSUS-ANCHOR: stale-worktrees-kept-01` and
`# CENSUS-ANCHOR: stale-worktrees-state-changed-01`, all key
`overwatch.stale_worktree_kept`: the stale-worktree check keeps a finished
task's old worktree that holds uncommitted or untracked changes (or commits
no other ref holds), whether found at the scan or at the re-check under the
repo lock, and one whose task is no longer DONE or WONT-DO under the lock.
All three are declared `CONVERT` inline: the worktree is kept exactly as
found and the sweep carries on; the remedy shows what the worktree holds
(`git -C <the worktree> status --short`, interpolated). None of these files
is on the lint's default lists, so the entries are inventory in the same
sense as the markers above.

`team/scripts/lib/wake_driver.sh` carries a marker,
`# CENSUS-ANCHOR: wake-driver-systemic-block-01`, directly above the
`pgai_emit_error_contract` call (key `wake.dispatch_held_systemic_block`)
in `run_project_chain`'s blocked-task guard, which holds task dispatch for
one project while its human-gated blocks (BLOCKED, Needs Human: yes) are
systemic: two or more sharing one Block Record `reason_code` (other than
the agent catch-all `agent_unclassified`, which never forms a storm)
blocked within `[wake] block_storm_window` seconds back from now, or one
whose `reason_code` is on `[wake] systemic_block_reasons`. Declared
`BLOCK-WORTHY` in an inline comment: a systemic block is an operator stop
by design, since every task dispatched into it blocks the same way. The
hold changes no task state, never applies to OVERWATCH, leaves the
WAITING/BLOCKED re-checks and PM intake running, and lifts once the blocks
are resolved or, at the latest, `[wake] block_hold_cap` seconds after the
block that began it; the held log line names that lift time. A lone
task-local block never holds it. Its remedy prints the blockers and Block
Records of the tasks behind the hold. `wake_driver.sh` is not on
`_CENSUS_BASH_FILES`, so the entry is inventory: it records where the hold
lives and its declared class.

`team/pgai_agent_kanban/overwatch/block_storm.py` carries one marker,
`# CENSUS-ANCHOR: block-storm-config-01`, directly above the single
`emit_error_contract` call the module's input warnings share
(`block_storm.invalid_window` for a storm window that is not a whole
number of seconds, `block_storm.invalid_hold_cap` for a hold cap that is
not, `block_storm.invalid_now` for a `--now` that is not an ISO-8601
time, `block_storm.unknown_systemic_code` for a
`[wake] systemic_block_reasons` member outside the Block Record
vocabulary). Declared `CONVERT` in an inline comment: the window and cap
fall back to their defaults, the clock to the current time, and the
unknown code is dropped; the hold decision is still made and the wake
still runs. Inventory in the same sense as the `block_triage.py` marker
above.

`team/scripts/lib/wake_provider_pause.sh` carries one marker,
`# CENSUS-ANCHOR: wake-provider-pause-01`, directly above the
`pgai_emit_error_contract` call (key `wake.dispatch_paused_usage_limit`)
in `wake_provider_pause_gate`, which the wake's dispatch loop runs at
every task boundary. It holds dispatch to a provider while that
provider's usage-limit pause is in force: a marker at
`<kanban root>/provider-pause/<provider>.pause`, recorded when an agent's
log tail shows the account reached its usage limit, lasting until the
reset time the output stated or `[wake] usage_limit_pause_seconds`.
Declared `BLOCK-WORTHY` in an inline comment: every dispatch to a provider
at its usage limit fails the same way and would spend each task's
retries. The hold is timed and lifts by itself at the marker's
`resumes_at`; it changes no task state, applies to every role and project
on that provider, OVERWATCH included, and its remedy removes the marker
to resume dispatch early. The stage file is not on `_CENSUS_BASH_FILES`,
so the entry is inventory in the same sense as the `wake_driver.sh`
marker above.

`team/pgai_agent_kanban/archive/config.py` carries one marker,
`# CENSUS-ANCHOR: archive-count-invalid-01`, directly above the raise that
refuses an `[archive] archive_count` value that is not a non-negative
whole number (`archive.invalid_archive_count`, naming the value and the
file, with a `sed` remedy that rewrites the line). Declared `CONVERT` in an
inline comment: `effective_archive_count()` prints the refusal and the
archive run for that project archives nothing; no wake, release or other
project is held, and the next run retries. `team/pgai_agent_kanban/archive/layout.py`
carries one marker, `# CENSUS-ANCHOR: archive-layout-path-component-01`,
directly above the raise that refuses a project name or training role that
is not a single safe path component (`archive.invalid_path_component`).
Declared `BLOCK-WORTHY` in an inline comment: a name carrying a separator
or `..` would build an archive path outside `<kanban_root>/archive/<project>/`,
and a move to it would scatter or overwrite live state; only the operation
that asked for the path stops. Neither file is on `_CENSUS_PYTHON_FILES`,
so both entries are inventory in the same sense as the `block_storm.py`
marker above.

`team/pgai_agent_kanban/archive/move.py` and
`team/pgai_agent_kanban/archive/apply.py` carry the archive's moving
sites. `archive-move-destination-exists-01` (`archive.destination_exists`)
refuses a move whose destination already exists, leaving both copies as
they are. `archive-run-lock-busy-01` (`archive.repo_lock_busy`) is the
skip when a process outside the run's own wake chain holds the project's
repo lock: nothing moves and the holder's four provenance fields are named.
Who reaches it depends on the caller. Release finalize's Step 16c asks the
run to accept its chain's hold (`--accept-chain-lock`), so a lock whose
recorded holder pid is the run's own process or one of its ancestors -- the
wake chain CM ships from -- is worked under and never reaches this site;
Step 16c skips here only when another writer holds the lock, or when the
run cannot read its own process ancestry. `scripts/archive.sh` and
`POST /operations/archive` do not ask, so any other holder, a wake chain
included, skips them here. `archive-run-failed-01`
(`archive.run_failed`) is the report of a run stopped at a failing item.
All three are declared `CONVERT` inline: archiving is quality-class, so
each stops or skips the archive run for that project, logs dual-channel,
and returns a result; no release or wake waits on it and the next archive
run retries. `archive-restore-not-archived-01`
(`archive.restore_not_archived`) refuses restoring an ID the archive does
not hold, and `archive-restore-live-exists-01`
(`archive.restore_live_exists`) refuses restoring over an item the live
project already holds. Both are declared `BLOCK-WORTHY` inline: the first
has nothing to move and names where it looked rather than guessing at a
near match, the second would overwrite live state; each stops only that
restore, before anything moves. Neither file is on `_CENSUS_PYTHON_FILES`;
the entries are inventory in the same sense as the markers above.

`team/scripts/archive.sh` and `team/pgai_agent_kanban/archive/run.py` carry
the archive's operator-surface refusals, each declared `BLOCK-WORTHY` inline
and each refusing before anything is read or moved. The script refuses its
own malformed arguments: an argument it does not define, a flag or a bare
word (`archive-cli-unknown-argument-01` and `-02`,
`archive.unknown_argument`); an option given without its value
(`archive-cli-option-value-required-01`, `archive.option_value_required`);
and a missing `--project` (`archive-cli-project-required-01`,
`archive.project_required`), which `$PGAI_PROJECT_NAME` never stands in for.
`run_archive`, shared by the script and `POST /operations/archive`, refuses
`--restore` combined with `--dry-run` or `--count`
(`archive-run-conflicting-options-01`, `archive.conflicting_options`), a
`--count` that is not decimal digits (`archive-run-invalid-count-01`,
`archive.invalid_count`), and a project with no live directory
(`archive-run-project-missing-01`, `archive.project_missing`). The route's
own missing-`project` refusal is the shared
`operations-required-field-missing-01`. Neither file is on
`_CENSUS_PYTHON_FILES`; the entries are inventory in the same sense as the
markers above.

`team/scripts/import-project.sh` carries two refusals for an export that
carries the project's archive (the embedded `<name>/ARCHIVE/` that
`export-project.sh` writes from `archive/<name>/`), each declared
`BLOCK-WORTHY` inline and each refusing before anything is extracted or
moved. `import-project-archive-name-01`
(`import.invalid_archive_project_name`, exit 1) refuses a final project name
outside the project-name shape, which would place archived items outside
`archive/` or under a name no archive reader resolves; its remedy re-runs the
import with a lower-cased, sanitised `--name`.
`import-project-archive-exists-01` (`import.archive_destination_exists`,
exit 4) refuses placing the imported archive over an existing
`archive/<final-name>/`, which would overwrite or mix two ledgers; its
remedy re-runs with `--force`, which moves the existing directory aside to
`archive/<final-name>.bak-<UTCstamp>` and deletes nothing.
`import-project-archive-placement-01` (`import.archive_placement_failed`,
exit 5) is the one failure after extraction: the archive directory could not
be created, the existing archive could not be moved aside, or the imported
archive could not be moved into place. The imported archive stays inside the
extracted project, nothing is deleted, and the remedy is the `mv` that
finishes the placement. Two further refusals guard the project directory
itself; the archive is extracted into a staging directory under
`projects/` and moved straight to `projects/<final-name>/`, so only the
final name is ever checked or backed up.
`import-project-destination-exists-01` (`import.destination_exists`,
exit 4) refuses replacing an existing `projects/<final-name>/` without
`--force`, before anything is extracted or moved; its remedy re-runs with
`--force`, which moves the existing directory aside to
`projects/<final-name>.bak-<UTCstamp>` once extraction has succeeded and
deletes nothing. `import-project-staging-01` (`import.staging_unavailable`,
exit 5) refuses when no staging directory can be created under
`projects/`, since there is then nowhere to extract that cannot clobber an
existing project; nothing has changed, and the remedy creates `projects/`,
checks it is writable and re-runs the same import. The script is not on the
lint's bash scan list; the entries are inventory in the same sense as the
markers above.

`team/scripts/cleanup/cleanup.sh` carries three path guards, each declared
`BLOCK-WORTHY` inline and each refusing dual-channel with exit 1 before any
step runs. `cleanup-root-mismatch-01` (`cleanup.root_mismatch`) refuses when
`KANBAN_ROOT` and `PGAI_AGENT_KANBAN_ROOT_PATH` name different roots: the
deleting steps would act on a root neither variable unambiguously named.
`cleanup-project-root-export-mismatch-01`
(`cleanup.project_root_export_mismatch`) refuses an exported
`PGAI_PROJECT_ROOT` that does not match the reconciled project root, which
would let the release-state lookup and every path derived from it read
outside that root. `cleanup-tasks-dir-export-mismatch-01`
(`cleanup.tasks_dir_export_mismatch`) refuses an exported `PGAI_TASKS_DIR`
that does not match the reconciled tasks directory. Step 2 deletes no task
folder — it only counts terminal ones for its report — so this guard no
longer protects a task-folder prune. Its class stands for Step 6: the temp
purge reads task state from that directory to decide which task worktrees
are `WORKING` and must survive, so a tasks directory the caller did not
sandbox could let a live agent's worktree be purged. The inline comment
carries that justification.

`cleanup.sh` has no refusal for a missing project. With neither
`--project` nor `PGAI_PROJECT_NAME`, it re-invokes itself once per project
registered in `projects.cfg`. Each per-project run prints its own refusals.
The loop only records which project failed and returns the first failing
run's exit status, so it adds no refusal site. The script is not on the lint's bash scan list;
the three entries are inventory in the same sense as the markers above.

`team/scripts/cleanup/cleanup_logs.sh` carries two markers. The first,
`# CENSUS-ANCHOR: cleanup-logs-unknown-argument-01`, sits directly above the
exit that refuses an argument the script does not define
(`cleanup_logs.unknown_argument`, exit 2, naming the argument and the
accepted `--dry-run` and `--help`, with a `--dry-run` remedy). Declared
`BLOCK-WORTHY` in an inline comment: an unknown argument may be an
operator expecting a narrower or safer run than the default prune, so it
is refused before anything is deleted rather than ignored. Inventory in
the same sense as the `cleanup.sh` markers above.

The second, `# CENSUS-ANCHOR: cleanup-logs-log-dir-not-found-01`, sits
directly above the exit that refuses a run whose `$KANBAN_ROOT/logs/agents`
directory does not exist (`cleanup_logs.log_dir_not_found`, exit 1, naming
the missing directory and the `KANBAN_ROOT` it was resolved from). Declared
`BLOCK-WORTHY` in an inline comment: the prune deletes only inside an
existing log directory, and a missing one means `KANBAN_ROOT` names a tree
the wake scripts never logged to, so the run refuses rather than reporting
"pruned 0" for a root it was never pointed at. Nothing is deleted. The
remedy is runnable for both causes: when `KANBAN_ROOT` is the default root,
`mkdir -p` of the log directory followed by a `--dry-run` preview; otherwise
a `--dry-run` preview with `KANBAN_ROOT` set to the default root. The script
is not on the lint's bash scan list; pass `--bash-dirs team/scripts/cleanup`
to check it.

`team/scripts/lib/cm/release_archive.sh`, release finalize's post-release
archive step, adds no refusal site. Whatever the archive run returns, the
step narrates it on a `[Step 16c]` line and returns 0, so the release's exit
status is unchanged. The refusals it can surface are the archive's own
sites listed above. Its `--accept-chain-lock` request adds none either:
working under the wake chain's own hold is the path that avoids
`archive-run-lock-busy-01`, not a new refusal, and a lock held by any
other writer still reaches that site unchanged in class.

`team/scripts/lib/discovery.sh`'s two HALT guards (rollup rows 3-4) have
Locator Index entries:
`discovery.per-project-halt` and `discovery.global-halt`, the literal
error keys each guard's own `pgai_emit_error_contract` call already
passes as its first argument — key-resolved, so neither needs a
`# CENSUS-ANCHOR` marker of its own (see "Anchor markers" above). Both
guards `return 0` rather than a nonzero `exit`/`return` (discovery's own
`DISCOVERY_LAST_STATUS="blocked"` convention, documented at this file's
own header, keeps `discovery_run_pipeline`'s return code always 0 so
cron callers are unaffected), so the lint's default scan — which only
walks literal `exit N`/`return N` (`N` 1-9) — never asked either site for
a class comment or emitter call in the first place; these two entries
are added purely so an operator reading the census can find where each
guard's remedy lives, not because the lint required it. Both are declared
`BLOCK-WORTHY` in an inline comment directly above each `return 0`,
matching their rollup rows' own doctrine (approval-by-design: the
per-project and fleet-wide HALT files are the operator's/CM's explicit
stop signals). The remedy each carries names the verb that clears it —
`team/scripts/unhalt.sh --project <name>` for the per-project guard,
`team/scripts/unhalt-global.sh` for the fleet-wide one.

`team/scripts/verify-rc-state.sh` carries five markers of its own. This
read-only checker resolves its project argument through
`pp_resolve_project_name`, and its argument-validation refusals speak
through the shared dual-channel emitter: `verify-rc-state-project-
required-01` and `verify-rc-state-unexpected-positional-01` (the
argument-shape checks — zero or more than one positional project name),
and `verify-rc-state-project-directory-missing-01`,
`verify-rc-state-project-config-unloadable-01`, and
`verify-rc-state-dev-tree-path-unset-01` (the belt-and-suspenders checks
after the resolver call succeeds — the resolver's own pass-through when
`projects.cfg` does not exist at all, documented in its header, is what
keeps the directory check reachable rather than dead code). All five are
declared `BLOCK-WORTHY` in an inline comment directly above their own
marker: a read-only status checker that cannot identify its target project
has nothing to check, so it refuses rather than rendering a report against
the wrong project or an empty one — the same "never render a plausible
result for an unresolved target" doctrine `why-project-not-found-01` and
`pp_resolve_project_name`'s own two markers apply. The resolver call
itself (`pp_resolve_project_name "$PROJECT_ARG" || exit 2`) carries no
local marker of its own — its refusal is the resolver's, already anchored
at `project-resolve-not-found-01`/`project-resolve-ambiguous-01` above,
matching the same convention `defer.sh`/`halt.sh`/`why.sh` use for their
own resolver call sites. `verify-rc-state.sh` is not on
`_CENSUS_BASH_FILES`, matching the census's own scope decision for pure
operator-CLI argument/usage refusals in interactive utilities the
autonomous loop never invokes (the `defer.sh`/`undefer.sh`/`why.sh`
paragraph above), so these five entries are inventory in the same sense as
the rest of this section: they record where each refusal lives and its
declared class, and the lint does not scan this file today.

`team/scripts/halt-global.sh` and `team/scripts/unhalt-global.sh` each carry
two markers of their own, on argument-validation refusals that speak
through the shared dual-channel emitter: `halt-global-kanban-root-
missing-01` and `halt-global-sentinel-create-failed-01` on the halt side;
`unhalt-global-kanban-root-missing-01` and `unhalt-global-sentinel-remove-
failed-01` on the unhalt side. Both scripts' refusal surface really is just
these two sites each — the idempotent already-halted / already-clear paths
exit 0 and are not refusals, and neither script takes any project or key
argument to validate beyond `--help`. All four are declared `BLOCK-WORTHY`
in an inline comment directly above their own marker: the global HALT
sentinel these verbs create or remove lives under `KANBAN_ROOT`, so a
missing root or a failed write/removal means the operator cannot rely on
the HALT's state matching what the verb just reported — reporting success
on an unverified write, or silently continuing past a directory that does
not exist, would let the loop's fleet-wide stop signal drift out of sync
with reality. Neither script is on `_CENSUS_BASH_FILES`, matching the same
census scope decision as `verify-rc-state.sh` and `why.sh` for pure
operator-CLI argument/usage refusals the autonomous loop never invokes
directly, so these four entries are inventory in the same sense as the rest
of this section.

`team/pgai_agent_kanban/ops/read.py` carries two markers, both in
`get_queues`. The first, `ops-read-queues-project-missing-01`, sits in
`get_queues` -- the function
`team/scripts/dashboard/show-queues.sh` reaches through `pp_run_ops` and
`GET /show-queues` calls in process -- directly above the `raise NotFound(...)`
whose text is the `show_queues.project_directory_missing` dual-channel
block. It is the backstop for the same registry-less gap
`verify-rc-state.sh` already closes: a supplied `--project` value is routed
through `pp_resolve_project_name` (or its Python twin), but that resolver
passes its candidate through unchecked, by documented design, when the
kanban root carries no `projects.cfg` at all. Without the check, that
unvalidated name would reach the queue scan and render a full board of
zeros with exit 0 -- indistinguishable from a genuinely idle project. The
marker is declared `BLOCK-WORTHY` in an inline comment directly above it: a
read-only queue display that cannot find the project directory it would
render has nothing to show, so it refuses rather than rendering a
plausible-looking board for a project it never found -- the same doctrine
`verify-rc-state-project-directory-missing-01` and
`why-project-not-found-01` already apply. show-queues.sh exits 1 on it and
the route reports exit code 1 in its envelope. The check is reachable from
the CLI only on the registry-less path; against a root whose `projects.cfg`
exists, the resolver's own refusal (`project-resolve-not-found-01`) already
stops an unregistered name before this site is reached. Inventory: a
`raise`, not a `sys.exit`, so the lint's missing-signal scan does not walk
it -- only the reverse locator-resolution check, which reads the entry's
own path directly, applies to it.

The same function carries a second marker, `ops-read-queues-no-project-01`,
directly above the `raise Refused(...)` whose text is the
`show_queues.no_project` dual-channel block (built by
`no_project_refusal`). It fires when `get_queues` is asked for an empty
project name -- which the compact pane always asks for when neither
`--project` / `?project=` nor `PGAI_PROJECT_NAME` names one, because the
compact plan fetches its data project unconditionally. That covers a
single-project root with no `projects.cfg` too: its `<root>/tasks/queues/`
is not a registered project directory, so the board has nothing it could
read there. The marker is declared `BLOCK-WORTHY` in an inline comment
directly above it, for the same reason as its sibling: a board of zeros for
a project nobody named is indistinguishable from a genuinely idle project,
so the pane refuses rather than print counts it never read. `--details`
with no project is unaffected -- it walks every registered project and
never asks for an empty name. show-queues.sh exits 1 on it (show-multi.sh's
registry-less `queues` fallback `exec`s it and passes the refusal through)
and `GET /show-queues` reports exit code 1 with the same block in its
envelope's `stderr`. Inventory in the same sense as its sibling: a `raise`,
reconciled only by the reverse locator-resolution check.

`team/scripts/wake-now.sh` carries five markers of its own, one per
distinct refusal cause named in the script's argument-validation logic:
`wake-now-agent-value-required-01` (a bare `--agent` with no value),
`wake-now-unexpected-positional-01` (a stray positional argument),
`wake-now-agent-all-mutually-exclusive-01` (`--all` and `--agent` both
supplied), `wake-now-agent-required-01` (neither `--all` nor `--agent`
supplied), and `wake-now-unknown-agent-01` (an `--agent` value outside the
declared vocabulary). Each cause carries its own machine key rather than
one catch-all, so a caller can distinguish a missing flag from a
mutually-exclusive pair from an unknown agent name without parsing prose.
All five are declared `BLOCK-WORTHY` in an inline comment directly above
their own marker: each names a shape of input this verb cannot dispatch
from at all (no agent named, two dispatch modes named at once, or a name
outside the fixed agent vocabulary), so refusing here is the same
"never render a plausible result for unresolvable input" doctrine the
other argument-validation sites in this section apply. The two sites that
already printed a `Usage:` line before this conversion (the unexpected-
positional and agent-required refusals) keep that line, printed after the
dual-channel triple and a separating blank line — the same coexistence
shape `verify-rc-state.sh` established: the dual-channel fields give a
tool the stable key and a runnable one-liner, and the usage block remains
the same existing reference a human scrolls to, so neither channel
replaces the other. `wake-now.sh` is not on `_CENSUS_BASH_FILES`, matching
the same scope decision as the other pure operator-CLI utilities in this
section, so these five entries are inventory in the same sense as the rest
of this section.

`team/scripts/overwatch-sweep.sh` carries four markers of its own, one per
distinct refusal of its command line, each directly above its own `exit 2`:
`overwatch-sweep-option-value-required-01` (`--only` or `--lock-wait` given
with no value), `overwatch-sweep-unknown-argument-01` (an option the runner
does not define), `overwatch-sweep-invalid-lock-wait-01` (a `--lock-wait`
that is not a whole number of seconds), and
`overwatch-sweep-unknown-check-01` (an `--only` slug that names no
`check-*.sh` module; the message lists every available slug). All four are
declared `BLOCK-WORTHY` in an inline comment beside their marker, under the
same "never render a plausible result for unresolvable input" doctrine as
`wake-now.sh` above: running every check in place of an unknown `--only`
slug would do far more than the caller asked. Each refuses before the HALT
gates or the project registry are read and before any check runs, so
nothing is swept and nothing is written. Unlike the pure operator-CLI utilities, this command
line is also driven by the loop itself -- the on-BLOCK nudge runs
`overwatch-sweep.sh --only block-triage --lock-wait 60` -- which is why the
sites are inventoried rather than left as unmarked usage errors.
`overwatch-sweep.sh` is not on `_CENSUS_BASH_FILES`, so these entries are
inventory in the same sense as the rest of this section.

`team/scripts/harness/strangers-path-check.sh` carries one marker of its
own, `strangers-path-check-stage-failed-01`, directly above the final
`exit 1` the fixture reaches when any of its four documented stages
(container, install, board, helloworld) does not behave as documented.
Declared `BLOCK-WORTHY` in an inline comment directly above the marker,
the same breakage-class doctrine `run-cold-build-tests-03` already applies
to a Dockerfile that fails to build cold: the defect is in the tree, not
the environment, and this fixture is the gate that would have caught it
before the release shipped. The fixture's other two verdicts —
`COULD-NOT-ATTEMPT` for a missing container engine or a missing `tmux`,
and each individual stage's own `FAIL` detail line — are narration, not
process-terminating refusals in their own right; only the run's own final
exit is the census-relevant site. The script's own argument-parsing exits
(`--doc-root` misuse, an unrecognized flag) are pure operator-CLI usage
errors the autonomous loop never invokes this harness with, matching the
same scope decision `run-cold-build-tests.sh`'s own unrecognized-argument
site documents, so they carry no marker. `strangers-path-check.sh` is not
on `_CENSUS_BASH_FILES`, so this entry is inventory in the same sense as
the rest of this section: it records where the refusal lives and its
declared class, and the lint does not scan this file today.

`team/scripts/run-three-host-matrix.sh` carries one marker,
`run-three-host-matrix-env-bootstrap-01`, directly above its
`source lib/env_bootstrap.sh || exit 1` prelude: the env-bootstrap source
abort of rollup row 2, declared `BLOCK-WORTHY` in an inline comment directly
above the marker, the same class every runner carrying that prelude is
indexed at. `run-three-host-matrix.sh` is not on `_CENSUS_BASH_FILES`, so
this entry is inventory in the same sense as the rest of this section;
check it with `--bash-dirs team/scripts`.

`team/scripts/lib/container_tier.sh` carries one marker,
`container-tier-unknown-image-01`, directly above the `exit` of
`container_tier_select_images`: the image-selection step the four
container-tier harnesses (`run-cold-build-tests.sh`,
`run-three-host-matrix.sh`, `harness/strangers-path-check.sh`,
`deep-verify.sh`) run before their first engine command. It refuses with
the stable key `container_tier.unknown_image` and exit 3 when `--image`
(or `kanban.cfg [release] container_images`) names an image
`team/config/container-images.txt` does not declare. Declared
`BLOCK-WORTHY` inline: an invariant by design -- the manifest is the one
declared list of images the tier builds, so an undeclared name is never
guessed at; nothing is built, pulled or started, and the remedy is the
same command with `--image all`. The operator-only check in
`container_tier_require_operator` is not a refusal site: an unauthorized
call (no `PGAI_CONTAINER_TIER=1` or `--operator-approved`, or any call
inside an agent wake) prints one `SKIPPED` line keyed
`container_tier.operator_only` and exits 0 -- CONVERT-class narration,
declared inline, so an agent that reaches for the tier never fails its
task or stalls an RC, and no verdict or ledger record is written.

`docker/build.sh` (repository only, never installed) carries four markers,
each declared `BLOCK-WORTHY` inline and each firing before any engine
command, so a refusal builds, pulls and starts nothing:
`docker-build-usage-01` (`docker_build.usage`, exit 2: an unknown option, a
missing or second FLAVOR, an `--engine` other than docker or podman, or a
malformed `--tag`), `docker-build-unknown-flavor-01`
(`docker_build.unknown_flavor`, exit 2: no `docker/<flavor>/Dockerfile`; the
message lists the valid flavors and the remedy is a `--dry-run` of the first
one), `docker-build-cli-pins-unreadable-01`
(`docker_build.cli_pins_unreadable`, exit 1: `docker/cli-versions.env` cannot
supply both pinned versions, so the build is refused rather than run
unpinned), and `docker-build-engine-not-found-01`
(`docker_build.engine_not_found`, exit 1: the engine binary is not on PATH,
checked only after the operator-only gate). An unauthorized build is the
container tier's `SKIPPED` narration above, not a refusal. `docker/` is not
on `_CENSUS_BASH_FILES`; check it with `--bash-dirs docker`.

`team/scripts/container-up.sh` (installed as `scripts/container-up.sh`)
carries seven markers, each declared `BLOCK-WORTHY` inline. Every one fires
after the operator-only gate (an unauthorized or in-wake call is the
container tier's `SKIPPED` narration, not a refusal), and none writes or
removes a HALT file: `container-up-usage-01` (`container_up.usage`, exit 2:
an unknown argument, a missing value, `--image` in place of `--image-tag`, or
a `--heartbeat-timeout` that is not whole seconds);
`container-up-host-cron-01` (`container_up.host_cron_wake_lines`, exit 1,
before any engine call: the invoking user's crontab carries a kanban wake
line, so cron already drives the board -- cron XOR pseudocron, no override
flag; the remedy saves the crontab to a backup and reinstalls it without the
wake lines); `container-up-crontab-check-01`
(`container_up.crontab_check_failed`, exit 1: the wake-line check itself
could not run, so a second scheduler cannot be ruled out);
`container-up-engine-not-found-01` (`container_up.engine_not_found`, exit 1);
`container-up-image-missing-01` (`container_up.image_missing`, exit 1: the
image is not in the engine's store; the script never builds or pulls, and
the remedy is a `docker/build.sh` command); `container-up-engine-failed-01`
(`container_up.engine_failed`, exit 1: a state query, process listing,
`rm`, `run` or scheduler start failed, or the container exited right after
its run, naming the engine's own error); and
`container-up-heartbeat-unconfirmed-01`
(`container_up.heartbeat_unconfirmed`, exit 1: no heartbeat within
`--heartbeat-timeout`; the container and scheduler are left running and "up"
is not claimed). Check it with `--bash-dirs team/scripts`.

`team/scripts/container-down.sh` (installed as `scripts/container-down.sh`)
carries five markers, each declared `BLOCK-WORTHY` inline. Every one fires
after the operator-only gate (an unauthorized or in-wake call is the
container tier's `SKIPPED` narration, not a refusal), none writes or removes
a HALT file, and an absent container is reported with exit 0, not refused:
`container-down-usage-01` (`container_down.usage`, exit 2: an unknown
argument, a missing value, `--image`, or a `--progress-interval` that is not
whole seconds of at least 1); `container-down-kanban-root-missing-01`
(`container_down.kanban_root_missing`, exit 1: the agent sessions the soft
stop waits for are read from the kanban root, so without it they would be
ended unseen); `container-down-stop-timeout-invalid-01`
(`container_down.stop_timeout_invalid`, exit 2: `[docker]
container_stop_timeout_seconds` is not decimal digits; the remedy sets the
default); `container-down-engine-not-found-01`
(`container_down.engine_not_found`, exit 1); and
`container-down-engine-failed-01` (`container_down.engine_failed`, exit 1: a
state query, process listing, scheduler stop, `stop` or `rm` failed, naming
the engine's own error; the container is left as it is, so no agent session
is ended by a stop that could not see it). A session outliving the timeout
is narrated, not refused: the container is still stopped and removed. Check
it with `--bash-dirs team/scripts`.

`team/scripts/container-status.sh` (installed as `scripts/container-status.sh`)
carries five markers, each declared `BLOCK-WORTHY` inline as verification
unable to run (or, for usage, an invariant by design). The script is
read-only and not container-tier gated, so it has no `SKIPPED` path: it
reports inside an agent wake as anywhere else. A container that is absent,
stopped or running without a scheduler is reported with exit 3, not
refused, and host crontab wake lines while the container runs are an
`ERROR:` line in the report, not a refusal: `container-status-usage-01`
(`container_status.usage`, exit 2: an unknown argument or a missing
`--name` value); `container-status-kanban-root-missing-01`
(`container_status.kanban_root_missing`, exit 1: the heartbeat log, the
agent-session records and `kanban.cfg` live under the kanban root);
`container-status-engine-not-found-01` (`container_status.engine_not_found`,
exit 1); `container-status-engine-failed-01`
(`container_status.engine_failed`, exit 1: the state query failed with
anything other than "no such container", or the process listing failed on a
container that still runs, naming the engine's own error -- reporting "not
running" there would be a guess); and `container-status-report-failed-01`
(`container_status.report_failed`, exit 1: the lifecycle module could not
assemble the report). Check it with `--bash-dirs team/scripts`.

`team/scripts/lib/project_paths.sh` carries a further marker of its own,
`# CENSUS-ANCHOR: overwatch-resolve-invalid-01`, directly above the
`return 1` in `pp_overwatch_resolve` that refuses when the `[release]
overwatch_resolve` value present in project.cfg or kanban.cfg is neither
`true` nor `false` — the sibling refusal shape to `project-resolve-
not-found-01`/`project-resolve-ambiguous-01` above, in the same file:
those two refuse an unresolvable project name, this one refuses an
unresolvable autonomy-grant value. Declared `BLOCK-WORTHY` in a `CENSUS
(docs/blocking-site-census.md):` line inside `pp_overwatch_resolve`'s own
header comment block, not immediately above the marker — the class is
stated once, beside the rest of that accessor's contract, where a reader
meets it before the code. (Its two siblings carry no inline class
signal at all; they are pre-existing inventory the anchor exemption
carries.) `overwatch_resolve` gates whether
OVERWATCH may act autonomously on a CM block, so a malformed value could
otherwise leave that authority silently mis-resolved in either direction —
granted when the operator meant to withhold it, or withheld when the
operator meant to grant it — rather than the safe, documented default
(absence resolves to `false`; only a value that cannot mean either boolean
refuses). `project_paths.sh` is not on `_CENSUS_BASH_FILES`, so this entry
is inventory in the same sense as the file's other two markers: it records
where the refusal lives and its declared class, and the lint's
missing-signal scan does not walk this file today — only the reverse
locator-resolution check, which reads the entry's own path directly,
applies to it.

`team/scripts/lib/project_paths.sh` carries a sibling marker,
`# CENSUS-ANCHOR: block-triage-invalid-01`, directly above the `return 1`
in `pp_block_triage` that refuses when the `[overwatch] block_triage`
value present in project.cfg or kanban.cfg is neither `true` nor `false`
(key `block_triage.invalid_value`). Declared `BLOCK-WORTHY` in the
accessor's own `CENSUS (docs/blocking-site-census.md):` header line, for
the same reason as `overwatch-resolve-invalid-01`: the key grants or
withholds OVERWATCH's block-triage sweep the authority to act on task
state, so a malformed value is refused rather than guessed in either
direction. The stop is scoped to the sweep's own action — its caller
treats the refusal as a safety refusal that leaves the task unchanged and
logs it — so it never stops a wake or a release. Absence is not a fault:
it resolves to the shipped default, `true`. Inventory in the same sense as
the file's other markers.

`team/pgai_agent_kanban/api/routers/operations.py` carries a marker of its
own, `# CENSUS-ANCHOR: operations-cancel-rc-confirm-01`, directly above the
`return _refusal_response(...)` in `post_cancel_rc` that refuses a
`POST /operations/cancel-rc` whose body carries neither `yes: true` nor
`dry_run: true`. The CLI twin (`scripts/cm/cancel-rc.sh`) stops at an
interactive "Proceed?" prompt unless `--yes` is given; an HTTP caller has
no terminal to answer it, so the body field is the answer and a request
without it is refused at the API layer — HTTP 422 with a JSON body carrying
the dual-channel triple (`cancel_rc.confirmation_required`, a message naming
the branch and release-state file the call would have touched, and a
runnable `curl` remedy built from the request's own URL that re-sends the
call as a `dry_run` preview) — before any subprocess is spawned. Declared
`BLOCK-WORTHY` in an inline comment directly above the marker: an approval
by design, the same explicit-opt-in shape `POST /operations/remove-project`
applies to a `force` call's `confirm`. The refusal is a `return` of a JSON response, not a
`sys.exit`, and `routers/operations.py` is not on `_CENSUS_PYTHON_FILES`, so
this entry is inventory in the same sense as the `lib/cm/` stages above: it
records where the refusal lives and its declared class, and the lint's
missing-signal scan does not walk this file today — only the reverse
locator-resolution check, which reads the entry's own path directly,
applies to it.

The same file carries `# CENSUS-ANCHOR: operations-halt-after-project-required-01`,
directly above the `return _refusal_response(...)` in `post_halt_after` that
refuses a `POST /operations/halt-after` whose body names no `project` — HTTP
422 with the dual-channel triple (`halt_after.project_required`, a message
saying the HALT-AFTER sentinel is always per-project, and a runnable `curl`
remedy re-sending the CLI's minimal form) before any subprocess is spawned.
Declared `BLOCK-WORTHY` inline: the same invariant-by-design stop as
`scripts/halt-after.sh`'s own `halt-after-project-required-01` above, raised
one layer earlier so an HTTP caller never sees the framework's `detail` list.
Inventory in the same sense as the cancel-rc entry: a `return`, not a
`sys.exit`, in a file the missing-signal scan does not walk.

Nine more markers in the same file inventory the API-layer refusals every
other action route raises before spawning anything, all of them `return`s
of the dual-channel `_refusal_response` JSON body at HTTP 422 and all
declared `BLOCK-WORTHY` inline. `operations-required-field-missing-01`
sits in `_missing_field_refusal`, the one helper every action route's
missing-required-field check lands in (`<verb>.<field>_required` — for
example `halt.project_required`, `close.key_required`,
`ship_rc.confirm_required` — with a runnable `curl` remedy re-sending a
body the route accepts): an invariant by design, since the script would
refuse the same absent argument itself, raised one layer earlier so an
HTTP caller never sees the framework's `detail` list.
`operations-reset-selector-required-01` and
`operations-reset-selector-conflict-01` are `post_reset`'s exactly-one-of
rule over its five kind selectors (`reset.selector_required`,
`reset.selector_conflict`): no selector leaves nothing to forward as
`--key`, and two name two items with no silent precedence between them.
`operations-remove-project-confirm-01` is `post_remove_project`'s
byte-for-byte confirm gate (`remove_project.confirm_mismatch`) on the
destructive escalation only: the default call runs the safe
unregister-only mode, while `force: true` -- which also deletes the
project directory -- must carry `confirm` equal to
`remove-project <project>` exactly, an approval by design. The Active-RC
guard is not the route's: it lives in `remove_project` and holds in both
modes (`projects-remove-active-rc-01` below). `operations-unwind-rc-confirm-02`
and `operations-unwind-rc-confirm-01` are `post_unwind_rc`'s confirmation
gate on every live call: the CLI's interactive "Type 'yes'" prompt cannot
be answered over HTTP, so `confirm` is the answer -- `true`, or the typed
phrase `unwind-rc <project> <key>` byte-for-byte. A missing, `false` or
empty `confirm` is refused by the first (`unwind_rc.confirm_required`),
any other string by the second (`unwind_rc.confirm_mismatch`), both before
`cm.unwind.unwind_rc` runs; `force` is a separate escalation that bypasses
only the Active-RC mismatch check and never stands in for the
confirmation. `operations-ship-rc-confirm-01`
is `post_ship_rc`'s byte-for-byte confirm gate (`ship_rc.confirm_mismatch`)
on the one origin-touching verb in the API. And
`operations-deep-verify-project-required-01` is `post_deep_verify`'s
project-or-scheduled requirement (`deep_verify.project_required`), the same
conditional deep-verify.sh enforces, refused before dispatch so it reaches
the caller instead of a detached child's log.
`operations-intake-filename-not-bare-01` is `post_intake`'s bare-filename
rule (`intake.filename_not_bare`): a `filename` with a path separator, `..`,
an absolute path, a URL-encoded separator or a NUL is refused before the
dry-run description is built and before any directory or file is created,
with a message that never echoes the refused value and a runnable `curl`
remedy re-sending the body with `BUG-0001-example.md` -- an invariant by
design, since the name is joined under the intake temp directory. The check
is `lib/filename_contract.py`'s `intake_destination`, the one `intake.sh`
reaches through `deposit_intake` (`write-intake-filename-not-bare-01`
below). Every one is inventory in the same sense as the two entries above: a `return` in a file the
missing-signal scan does not walk, covered by the reverse
locator-resolution check alone.

One more API-layer refusal in the same file,
`operations-container-image-unknown-01`, sits in `_unknown_image_refusal`,
the image check `post_deep_verify` and `post_cold_build_now` run on the
body's optional `image` field before dispatch: a name
`team/config/container-images.txt` does not declare is refused at HTTP 422
with the dual-channel triple (`container_tier.unknown_image`, the harness's
own key; a message naming the undeclared images and the declared list and
saying nothing was built, pulled, started or spawned; and a runnable `curl`
remedy re-sending the caller's body with `image` set to `"all"`), on a real
call and a `dry_run` alike. Declared `BLOCK-WORTHY` inline: the same
invariant by design `lib/container_tier.sh`'s
`container-tier-unknown-image-01` above enforces, raised one layer earlier
because a detached child's own refusal would reach only a log file the HTTP
caller never reads. Inventory in the same sense as the entries above: a
`return` in a file the missing-signal scan does not walk.

Six more markers in `routers/operations.py` inventory the refusals a route
that calls its `ops.write` function in process reproduces from its CLI
script, so an HTTP caller reads the same exit code, stderr and lifted triple
the script produces, at HTTP 500. `operations-halt-after-until-scope-mismatch-01`
and `operations-halt-after-until-malformed-01` sit in `_halt_after_result`
(`halt-after.until_scope_mismatch`, `halt-after.until_malformed`), mirroring
`scripts/halt-after.sh`'s own two `--until` refusals above.
`operations-halt-global-kanban-root-missing-01`,
`operations-halt-global-sentinel-create-failed-01`,
`operations-unhalt-global-kanban-root-missing-01` and
`operations-unhalt-global-sentinel-remove-failed-01` sit in
`_halt_global_result` / `_unhalt_global_result`, mirroring the two global
scripts' four refusals. `post_close` has no close-state refusal of its
own: `close_item` raises it (`write-close-invalid-state-01`, above) and the
route lifts its dual-channel triple as it lifts any other. All six are
declared `BLOCK-WORTHY` inline, each for the same reason as the script site
it mirrors, and are inventory in the same sense as the entries above: a
`return`, not a `sys.exit`, in a file the missing-signal scan does not walk.

One more marker in the same file, `operations-intake-temp-path-unconfined-01`,
sits in `_refuse_unconfined_intake_path`, which `post_intake`'s temp-file
writer calls for the per-request directory before creating it and for the
temp file before writing it (`intake.temp_path_unconfined`): a path that
resolves, through a symlink, outside the intake temp directory is refused
and nothing is created there. Declared `BLOCK-WORTHY` inline -- an invariant
by design, the second half of the bare-filename rule above. It is a `raise`
of `Refused` inside the route's ops call, so the caller reads it as the
dual-channel envelope at HTTP 500 with exit code 2, its remedy a runnable
`ls -la` of the intake temp directory. Inventory in the same sense as the
entries above.

Three markers inventory the confinement check every API route applies to
its `project`, `key` and selector fields before it acts on them.
`# CENSUS-ANCHOR: validation-not-component-01` sits in
`team/pgai_agent_kanban/api/validation.py`, directly above the
`raise ComponentRefused(...)` in `require_component`: a value that is empty,
`.` or contains `..`, a path separator, NUL or another control character
(newline included), a URL-encoded separator, a glob character (`* ? [ ]`),
or starts with `-`, is not one plain name. `operations-field-not-component-01`
sits in `routers/operations.py`'s `_component_refusal` and
`reads-field-not-component-01` in `reads.py`'s helper of the same name;
each turns that raise into HTTP 422 with the dual-channel triple
`<verb>.<field>_invalid` (`remove_project.project_invalid`,
`halt_after.key_invalid`, `status.project_invalid`), a message naming the
field and what is wrong with it, and a runnable `curl` remedy re-sending the
request with an example value -- before the dry-run description, project
resolution, any path, glob, sentinel write, read or dispatch. All three are
declared `BLOCK-WORTHY` inline, an invariant by design: the field becomes a
path component, a glob prefix, a sentinel line or an argv value, and a value
that is not one plain name reaches outside the kanban root or changes what
runs. Inventory in the same sense as the `routers/operations.py` entries
above: a `raise` and two `return`s, in files the missing-signal scan does not
walk.

`operations-project-value-invalid-01` sits in `routers/operations.py`'s
`_project_value_refusal`, where create-project and add-project turn the
registry verbs' own value refusals (`ops/projects.py`'s
`check_create_values` and `check_project_name`, below) into HTTP 422 with
the ops layer's `<verb>.<field>_invalid` key and message and a runnable
`curl` remedy carrying an example value for the field -- before the dry-run
description. Declared `BLOCK-WORTHY` inline for the same reason as its
neighbours. Inventory: a `return`.

`team/pgai_agent_kanban/ops/resolve.py` carries five markers. Two,
`# CENSUS-ANCHOR: ops-resolve-project-not-found-01` and
`# CENSUS-ANCHOR: ops-resolve-project-ambiguous-01`, sit directly above the
`raise NotFound(...)` / `raise Ambiguous(...)` in `resolve_project_name`,
the Python twin of `pp_resolve_project_name` (the `project_paths.sh`
entries above). The exception's text is the same three-line block the bash
resolver prints (`project_resolve.not_found`, `project_resolve.ambiguous`),
so a route that resolves `project` this way refuses byte-identically to the
CLI. Declared `BLOCK-WORTHY` inline for the bash sites' own reasons: an
unregistered name has no directory to write to, and two prefix matches
leave no project to pick. Inventory: a `raise`, not a `sys.exit`.

The other three guard `resolve_item`, the one funnel through which close,
wontdo, delete, reset and show reach an item -- from the API and from the
CLI scripts alike. `ops-resolve-project-not-component-01` and
`ops-resolve-key-not-component-01` sit in `_require_plain_project` and
`_require_plain_key`, directly above the `raise OpsError(...)` that turns
`api/validation.py`'s `require_component` refusal into the dual-channel
block `resolve_item.project_invalid` / `resolve_item.key_invalid` (remedy:
a `grep` of the registered names, or an `ls` of the project's item
directories) before any path is built, globbed or read; the resolve CLI
shim the bash scripts call prints the same block and exits 1.
`ops-resolve-path-outside-tree-01` sits in `_require_inside`, above the
`resolve_item.path_outside_tree` refusal of a match whose realpath lies
outside the tree searched (a symlink out of the project or archive tree),
before its state is read. All three declare `BLOCK-WORTHY` inline, an
invariant by design: the resolved path is what is rewritten, deleted or
returned. Inventory: a `raise`, not a `sys.exit`.

`team/pgai_agent_kanban/api/app.py` carries two markers of its own, one in
each of the pure-ASGI middlewares the app factory stacks around every
route. `# CENSUS-ANCHOR: app-host-not-loopback-01` sits in
`LoopbackHostMiddleware`, the outermost layer, directly above the HTTP 400
refusal of any request -- read or mutating, any route, the CORS preflight
included -- whose `Host` header is not a loopback name (`127.0.0.1` or any
127/8 address, `localhost`, `[::1]`, with or without a port; a missing or
empty `Host` refuses too). Declared `BLOCK-WORTHY` in an inline comment
directly above the marker: an invariant by design, since a loopback bind
alone cannot see a DNS-rebinding page whose hostname resolves to the
loopback socket, and the `Host` header is the one field that names it. The
JSON body carries the dual-channel triple (`api.host_not_loopback`, a
message quoting the refused `Host` and naming the three accepted names, and
a runnable `curl` remedy re-targeting the same method, path and query at
`http://127.0.0.1:<port>` -- the port taken from the refused `Host` when it
named one, else the configured bind port) plus an empty `warnings` list.
The allow-list is fixed and not a tunable; the test suite reaches the
routes by giving its `TestClient` a loopback `base_url`, not through any
allowance in the gate. `# CENSUS-ANCHOR: app-content-type-not-json-01`
sits in `JsonMutationMiddleware`, the innermost layer, directly above the
HTTP 415 refusal of any request whose method is not `GET`, `HEAD` or
`OPTIONS` and whose `Content-Type` media type is not `application/json`
(parameters such as `charset` are ignored; form encodings, `text/plain`
and an absent header all refuse). Declared `BLOCK-WORTHY` inline: an
invariant by design, since every mutating route reads a JSON body and
nothing else, refused one layer earlier so a browser simple-request
forgery never reaches a route or spawns a subprocess. Its triple is
`api.content_type_not_json`, a message naming the method, path and the
content type received, and a runnable `curl` re-sending the request as
JSON with an empty-object example body. Both are inventory in the same
sense as the `routers/operations.py` entries above: each refusal is an
`await response(...)` on a JSON response, not a `sys.exit`, in a file the
missing-signal scan does not walk, covered by the reverse
locator-resolution check alone.

`team/scripts/lib/config_loader.sh` carries a fifth marker beside its four
`[release]` posture-key anchors: `# CENSUS-ANCHOR: config-loader-wake-integer-01`,
directly above the `return 1` in `_config_loader_export_wake_integer` that
refuses a `[wake]` integer key — `max_tasks_per_wake`, `max_runtime_seconds`,
`pause_between_tasks`, `pm_max_tasks`, `max_task_seconds`,
`max_project_seconds`, `kill_grace_seconds`, `transient_retry_cap`,
`zero_progress_cap` — whose resolved value (an already-exported environment
override, else the cfg value, else the default) is anything but a run of
decimal digits. Declared `BLOCK-WORTHY` in an inline comment directly above
the marker and emitted through the shared dual-channel contract under the
stable key `config_loader.wake.not_an_integer`: every one of these values
feeds bash arithmetic in the wake loop, so `300s`, `6h`, or a value that
once carried an inline comment would either abort a batch mid-run with an
arithmetic error or bound it by a number nobody chose — refusing before any
task is selected is the earlier and louder of the two failures. The remedy
is the `sed` edit (or the `export`, for an environment override) that
restores the digits the value started with, else the key's default. The
loader's companion unknown-key and unknown-section check in the same file
is deliberately a WARNING that returns 0 — `CONVERT` by nature, a
misspelled key must not stop a wake — so it carries no anchor and no
refusal. `config_loader.sh` is on `_CENSUS_BASH_FILES`, but this site is a
function `return`, not a script-terminating `exit`, so the entry is
inventory in the same sense as the four posture-key anchors: the reverse
locator-resolution check reconciles the marker against this index.

The same file carries `# CENSUS-ANCHOR: config-loader-effort-01` in
`_config_loader_check_effort`, directly above the `return 1` that refuses a
reasoning-effort value the active provider does not accept.
`resolve_role_effort` calls it for each role, from `load_config` and from
the provider wake lanes, with the value from a `PGAI_<ROLE>_EFFORT`
override or else `[providers] llm_effort`. Declared `BLOCK-WORTHY` inline
as an invariant by design: a level the provider's CLI rejects would be
passed on every dispatch for the role and fail there, so it is refused at
config load instead. The refusal is emitted through the shared dual-channel
contract under `config_loader.providers.invalid_effort`; the message names
the source that supplied the value, the value, the provider and its list.
The remedy is the `sed` that rewrites `llm_effort` to a level the provider
accepts, or, for the environment override, the `unset` of that variable
(with an `export` of an accepted level as the alternative).

The list is the provider's own `PROVIDER_EFFORT_VALUES` line in
`scripts/lib/provider_<name>.sh`, so the refusal is reachable only for a
provider that declares one. A provider with no such lib, or whose lib
declares no list, leaves nothing to check against: the value is not
validated, one stderr line per resolution says it was passed through
unvalidated, and the function returns 0. That narration is not a refusal
and carries no anchor. The site is a function `return`, inventory in the
same sense as the wake-integer anchor above.

The same file carries two `[testing]` anchors, one in each resolver the
gated test runners call: `# CENSUS-ANCHOR: config-loader-testing-workers-01`
directly above the `return 1` in `config_testing_workers` that refuses a
`[testing] workers` value which is not decimal digits only (`abc`, `3x`,
`-1`), and `# CENSUS-ANCHOR: config-loader-testing-coverage-01` directly
above the `return 1` in `config_testing_coverage` that refuses anything but
`true`/`false` for `[testing] coverage` (`on`, `yes`, `True`). Both are
declared `BLOCK-WORTHY` inline as invariants by design: the worker value
bounds how many pytest workers a gated run starts on a shared host, and the
coverage value follows the same boolean-strictness rule as the `[release]`
posture keys, so neither is guessed from a malformed spelling. They emit
through the shared dual-channel contract under the stable keys
`config_loader.testing.invalid_workers` and
`config_loader.testing.invalid_coverage`; each message names the value and
the accepted set, and each remedy is a `sed` edit scoped to the `[testing]`
section that restores a valid value (the leading digits of a worker value,
else the default `3`; `false` for coverage) and leaves a same-named key in
any other section as it was. `load_config` does not read `[testing]`, so neither refusal
can stop a wake. Both sites are function `return`s, inventory in the same
sense as the wake-integer anchor above.

A third `[testing]` anchor sits in the resolver a pre-merge run calls:
`# CENSUS-ANCHOR: config-loader-testing-pre-merge-cap-01` directly above the
`return 1` in `config_testing_pre_merge_affected_cap` that refuses a
`[testing] pre_merge_affected_cap` value which is not decimal digits only
(`abc`, `-5`, `1.5`). It is declared `BLOCK-WORTHY` inline on the same
invariant-by-design ground as the worker value: the number decides how many
affected test files a pre-merge run selects, so it is not guessed from a
malformed spelling. It emits under the stable key
`config_loader.testing.invalid_pre_merge_affected_cap`; the message names
the value and the accepted form (`0` = no cap), and the remedy is a `sed`
edit scoped to the `[testing]` section that restores the leading digits of
the value, else the default `60`. Only the pre-merge run reads the key, so
the refusal never stops a wake or an RC. The site is a function `return`,
inventory in the same sense as the wake-integer anchor above.

Each gated runner carries two matching anchors where it calls the workers
and coverage resolvers: `# CENSUS-ANCHOR: run-unit-tests-testing-config-01` /
`run-integration-tests-testing-config-01` directly above the `exit 1` that
follows a refused `[testing] workers` value (read only by a full
certification run), and `run-unit-tests-testing-config-02` /
`run-integration-tests-testing-config-02` directly above the `exit 1` that
follows a refused `[testing] coverage` value (read only when `--coverage`
was given). Both are `BLOCK-WORTHY` for the reasons the resolver anchors
above give. The runner site emits nothing itself -- the resolver has
already printed the dual-channel error, message and `sed` remedy on stderr
-- and records a `runner_incomplete` finding before exiting, so the
findings file reads as an aborted run rather than a clean pass.

Both runners accept `--workers N`, which lowers one run's budget below
`[testing] workers` and never raises it. A value that is not decimal digits
is refused in the parse loop, before any gate, lint or pytest runs, under
`# CENSUS-ANCHOR: run-unit-tests-workers-flag-01` /
`run-integration-tests-workers-flag-01` (keys
`run-unit-tests.workers-flag-invalid` /
`run-integration-tests.workers-flag-invalid`; remedy `--workers 1`). Both
are `AGENT-RESOLVABLE`: the caller corrects its own argv and re-runs.

Both runners accept `--pre-merge [--base REF]`, which hands the run to the
pre-merge engine and writes its own exit file (`pre-merge.exit` for the unit
runner, `pre-merge-integration.exit` for the integration runner) instead of
anything a certification run writes. Each runner carries four anchors for it,
named the same in both files with the runner's slug in front.
`<runner>-pre-merge-01` (keys `run-unit-tests.pre-merge-base-missing` /
`run-integration-tests.pre-merge-base-missing`) refuses `--base` with no git
ref after it, or one starting with `-`, in the parse loop; the remedy is the
same run with `--pre-merge` and no `--base`, which uses the default base.
`<runner>-pre-merge-02` (`...pre-merge-incompatible-flags`) refuses
`--pre-merge` beside `--files`, `--coverage`, `--quarantined` or
`--reuse-if-green`, naming each, and its remedy drops them.
`<runner>-pre-merge-03` (`...base-without-pre-merge`) refuses `--base`
without `--pre-merge`, and its remedy adds `--pre-merge` with the ref given.
All three are `AGENT-RESOLVABLE` and refuse before any gate, lint or pytest
runs. `<runner>-testing-config-03` (`BLOCK-WORTHY`) follows a refused
`[testing] pre_merge_affected_cap` value, read only by a pre-merge run, on the
ground the resolver anchor `config-loader-testing-pre-merge-cap-01` gives; the
resolver has already printed the error. An unknown base, or no RC or main
branch to default to, is refused by the engine itself, dual-channel, and the
runner passes its exit code 2 through. A failed pre-merge check is the
engine's exit 1, passed through the same way: it stops the ticket until the
change is fixed in the same session, and never blocks an RC by itself --
nothing in the release path reads either pre-merge exit file.

`team/scripts/run-gated-suites.sh` runs both gated runners inside one
`[testing] workers` budget and carries six anchors. Each refusal happens
before either suite starts. `run-gated-suites-env-bootstrap-01`
(`BLOCK-WORTHY`) is the same unreachable-bootstrap exit every runner
carries. `run-gated-suites-testing-config-01` (`BLOCK-WORTHY`) follows a
refused `[testing] workers` value, for the reason the resolver anchor above
gives; the resolver has already printed the error. `run-gated-suites-runner-missing-01`
(`BLOCK-WORTHY`, key `run-gated-suites.runner-missing`): without both runners
beside it, verification cannot run. `run-gated-suites-out-dir-01`
(`BLOCK-WORTHY`, key `run-gated-suites.out-dir-unwritable`): an output
directory that cannot be written leaves the suites' exit codes and logs
nowhere to land. `run-gated-suites-args-01` (key
`run-gated-suites.unrecognized-argument`) and `run-gated-suites-args-02` (key
`run-gated-suites.out-dir-missing`) are `AGENT-RESOLVABLE` argv refusals.

`team/scripts/api-server.sh` carries `# CENSUS-ANCHOR: api-server-stop-target-not-api-01`,
directly above the `return 1` in `_signal_api_server_group` that refuses to
signal a pid whose command line does not name the API entrypoint module
(`pgai_agent_kanban.api.main`, what `start` launches). Both `stop` branches —
the pidfile pid and the port-probe occupant — signal through that one helper,
so it is the single place a reused pid or an unrelated listener on the API
port can reach a `kill`. Declared `BLOCK-WORTHY` in an inline comment directly
above the emitter call and emitted through the shared dual-channel contract
under the stable key `api-server.stop.target_not_api_server`: signalling a
process group the script does not own is a destructive action, and refusing
is what leaves the unrelated process alive. The message names the pid, the
port and the command line it found; the remedy is the `ps` inspection of that
pid, a by-hand `kill` of it if it is the operator's to stop, and the
`PGAI_API_PORT` override that runs the API elsewhere. `api-server.sh` is not
on `_CENSUS_BASH_FILES` and the site is a function `return` the caller turns
into exit 1, so the entry is inventory in the same sense as the
`config_loader.sh` marker above: the reverse locator-resolution check
reconciles the marker against this index.

`team/scripts/api-server.sh` also carries
`# CENSUS-ANCHOR: api-server-port-unresolved-01`, directly above the `exit 1`
that follows port resolution. Every subcommand takes the API port from
`load_api_config` (`PGAI_API_PORT` if set, else `kanban.cfg [api] port`, else
8300), the same function the server binds with; when that call fails or
prints something that is not a port number, the script refuses before any
probe or signal under the stable key `api-server.port.unresolved`, declared
`BLOCK-WORTHY` inline: probing a guessed port would report a live server as
not running, or stop another kanban's API server. The remedy is the resolver
command itself, with the real kanban root and package root interpolated, so
the operator sees the underlying error.

`team/scripts/restamp-fixed-in.sh` and the module it drives,
`team/pgai_agent_kanban/cm/fixed_in_restamp.py`, carry one marker each. The
script's `restamp-fixed-in-project-required-01` refuses when no project is
named; its project-name resolution refuses on its own, through the
resolver's `project-resolve-not-found-01`/`project-resolve-ambiguous-01`. The
module's `fixed-in-restamp-01` sits above the one emitter call every other
refusal of this operator back-fill reaches: a missing or malformed version
(`fixed_in_restamp.version_missing`, `fixed_in_restamp.version_malformed`),
no bug named (`fixed_in_restamp.no_bugs_named`), no bugs directory for the
project (`fixed_in_restamp.bugs_dir_missing`), and a bug name that matches no
file or several (`fixed_in_restamp.bug_not_found`,
`fixed_in_restamp.bug_ambiguous`), and the two refusals of its opt-in
correction mode: a `--replace-from` that is not a release version
(`fixed_in_restamp.replace_from_malformed`), and a named bug whose
`## Fixed In` is neither empty nor the `--replace-from` value
(`fixed_in_restamp.replace_from_mismatch`) -- every named bug is checked
before the first write, so one mismatch refuses the whole correction rather
than overwriting a release record nobody asked to change. The correction
refusals raise into the same emitter call and so share
`fixed-in-restamp-01`; they add no refusal site of their own and need no
anchor of their own. Each raise site declares `BLOCK-WORTHY`
inline, as does the emitter site: every one of them would otherwise write a
wrong or partial release record onto the bug ledger, and all are checked
before the first write, so a refusal leaves the ledger untouched and exits 2.
Neither file is on `_CENSUS_BASH_FILES` / `_CENSUS_PYTHON_FILES` -- the
autonomous loop never invokes this operator back-fill -- so both entries are
inventory in the same sense as the rest of this section, reconciled by the
reverse locator-resolution check.

<!-- CENSUS-LOCATOR-INDEX-BEGIN -->
docker/build.sh:docker-build-usage-01 BLOCK-WORTHY
docker/build.sh:docker-build-unknown-flavor-01 BLOCK-WORTHY
docker/build.sh:docker-build-cli-pins-unreadable-01 BLOCK-WORTHY
docker/build.sh:docker-build-engine-not-found-01 BLOCK-WORTHY
install.sh:install-stamp-helper-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/adapter.py:api-adapter-shell-out-timeout-01 AGENT-RESOLVABLE
team/pgai_agent_kanban/api/routers/operations.py:operations-cancel-rc-confirm-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-halt-after-project-required-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-required-field-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-reset-selector-required-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-reset-selector-conflict-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-remove-project-confirm-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-unwind-rc-confirm-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-unwind-rc-confirm-02 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-ship-rc-confirm-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-deep-verify-project-required-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-container-image-unknown-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-halt-after-until-scope-mismatch-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-halt-after-until-malformed-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-halt-global-kanban-root-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-halt-global-sentinel-create-failed-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-unhalt-global-kanban-root-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-unhalt-global-sentinel-remove-failed-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-intake-filename-not-bare-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-intake-temp-path-unconfined-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-field-not-component-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/routers/operations.py:operations-project-value-invalid-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/validation.py:validation-not-component-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/read.py:ops-read-queues-project-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/read.py:ops-read-queues-no-project-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/read.py:ops-read-test-report-outside-tree-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/rc_state.py:verify-rc-state-dev-tree-path-unset-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/rc_state.py:verify-rc-state-project-config-unloadable-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/rc_state.py:verify-rc-state-project-directory-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/resolve.py:ops-resolve-project-not-found-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/resolve.py:ops-resolve-project-ambiguous-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/resolve.py:ops-resolve-project-not-component-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/resolve.py:ops-resolve-key-not-component-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/resolve.py:ops-resolve-path-outside-tree-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/write.py:defer-arm-until-required-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/write.py:defer-arm-until-malformed-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/write.py:defer-project-not-found-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/write.py:ceiling-invalid-value-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/write.py:switch-provider-unknown-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/write.py:write-close-invalid-state-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/write.py:write-intake-filename-not-bare-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/projects.py:projects-remove-active-rc-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/projects.py:projects-remove-active-rc-invalid-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/projects.py:projects-create-name-invalid-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/projects.py:projects-name-not-component-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/projects.py:projects-workflow-type-not-component-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/projects.py:projects-cfg-value-invalid-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/projects.py:projects-priority-invalid-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/locks.py:locks-release-required-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/locks.py:locks-release-not-found-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/locks.py:locks-release-ambiguous-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/locks.py:locks-release-holder-alive-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/locks.py:locks-release-holder-not-released-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/locks.py:locks-release-holder-not-released-02 BLOCK-WORTHY
team/pgai_agent_kanban/ops/locks.py:locks-release-orphan-holder-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/locks.py:locks-release-orphan-not-released-01 BLOCK-WORTHY
team/pgai_agent_kanban/ops/locks.py:locks-release-orphan-not-released-02 BLOCK-WORTHY
team/pgai_agent_kanban/ops/locks.py:locks-acquire-provenance-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/cancel.py:cancel-confirmation-required-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/cancel.py:cancel-branch-prefix-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/cancel.py:cancel-git-remote-name-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/cancel.py:cancel-project-cfg-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/cancel.py:cancel-dev-tree-unset-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/cancel.py:cancel-version-format-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/cancel.py:cancel-current-branch-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/cancel.py:cancel-release-state-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/cancel.py:cancel-active-rc-mismatch-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/cancel.py:cancel-project-not-component-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-confirmation-required-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-project-unresolved-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-project-cfg-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-dev-tree-unset-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-branch-prefix-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-git-remote-name-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-version-format-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-halt-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-project-dir-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-shipped-tag-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-release-state-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-active-rc-none-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/unwind.py:unwind-active-rc-mismatch-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-version-format-invalid-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-project-required-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-branch-prefix-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-git-remote-name-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-not-a-repo-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-dirty-worktree-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-tag-exists-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-branch-missing-local-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-branch-missing-origin-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-git-step-failure-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-squash-conflict-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-fidelity-gate-divergence-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-halt-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-hook-failure-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/ship.py:ship-rc-hook-library-unavailable-01 BLOCK-WORTHY
team/pgai_agent_kanban/lib/defer_posture.py:defer-posture-campaign-unparseable-01 BLOCK-WORTHY
team/pgai_agent_kanban/lib/defer_posture.py:defer-posture-campaign-malformed-until-01 BLOCK-WORTHY
team/pgai_agent_kanban/lib/defer_posture.py:defer-posture-invalid-boolean-01 BLOCK-WORTHY
team/pgai_agent_kanban/lib/defer_posture.py:defer-posture-invalid-level-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/reads.py:reads-dashboard-column-required-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/reads.py:reads-field-not-component-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/app.py:app-host-not-loopback-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/app.py:app-content-type-not-json-01 BLOCK-WORTHY
team/pgai_agent_kanban/api/generate_icd.py:generate-icd-published-baseline-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/fixed_in_restamp.py:fixed-in-restamp-01 BLOCK-WORTHY
team/pgai_agent_kanban/cm/read_state_field.py:release-state-validators-01 BLOCK-WORTHY
team/pgai_agent_kanban/overwatch/resolution_stage.py:lane-a-rewrite-precondition-01 BLOCK-WORTHY
team/pgai_agent_kanban/overwatch/block_record.py:block-record-refusal-01 CONVERT
team/pgai_agent_kanban/overwatch/block_triage.py:block-triage-refusal-01 CONVERT
team/pgai_agent_kanban/overwatch/block_storm.py:block-storm-config-01 CONVERT
team/pgai_agent_kanban/overwatch/worktree_residue.py:worktree-residue-finished-refusal-01 CONVERT
team/pgai_agent_kanban/archive/config.py:archive-count-invalid-01 CONVERT
team/pgai_agent_kanban/archive/layout.py:archive-layout-path-component-01 BLOCK-WORTHY
team/pgai_agent_kanban/archive/move.py:archive-move-destination-exists-01 CONVERT
team/pgai_agent_kanban/archive/apply.py:archive-run-lock-busy-01 CONVERT
team/pgai_agent_kanban/archive/apply.py:archive-run-failed-01 CONVERT
team/pgai_agent_kanban/archive/apply.py:archive-restore-not-archived-01 BLOCK-WORTHY
team/pgai_agent_kanban/archive/apply.py:archive-restore-live-exists-01 BLOCK-WORTHY
team/pgai_agent_kanban/archive/run.py:archive-run-conflicting-options-01 BLOCK-WORTHY
team/pgai_agent_kanban/archive/run.py:archive-run-invalid-count-01 BLOCK-WORTHY
team/pgai_agent_kanban/archive/run.py:archive-run-project-missing-01 BLOCK-WORTHY
team/pgai_agent_kanban/metrics/blocked_ledger.py:blocked-ledger-01 CONVERT
team/pgai_agent_kanban/task_status.py:task-status-invalid-state-01 AGENT-RESOLVABLE
team/pgai_agent_kanban/task_status.py:task-status-missing-required-arg-01 AGENT-RESOLVABLE
team/pgai_agent_kanban/task_status.py:task-status-reason-code-refusal-01 AGENT-RESOLVABLE
team/pgai_agent_kanban/task_status.py:task-status-subcommand-refusal-01 AGENT-RESOLVABLE
team/pgai_agent_kanban/task_status.py:task-status-undecodable-read-01 AGENT-RESOLVABLE
team/scripts/lib/wake_task_id_gate.sh:wake-task-id-gate-malformed-prerequisite-id-01 CONVERT
team/scripts/lib/wake_task_id_gate.sh:wake-task-id-gate-malformed-queue-id-01 CONVERT
team/scripts/lib/worktree.sh:worktree-residue-outside-base-01 BLOCK-WORTHY
team/scripts/lint_api_parity.py:api-parity-census-gate-01 BLOCK-WORTHY
team/scripts/lint_api_route_impl.py:api-route-impl-01 CONVERT
team/scripts/archive.sh:archive-cli-unknown-argument-01 BLOCK-WORTHY
team/scripts/archive.sh:archive-cli-unknown-argument-02 BLOCK-WORTHY
team/scripts/archive.sh:archive-cli-option-value-required-01 BLOCK-WORTHY
team/scripts/archive.sh:archive-cli-project-required-01 BLOCK-WORTHY
team/scripts/audit_coding_standards.sh:audit-coding-standards-01 CONVERT
team/scripts/audit_coding_standards.sh:audit-coding-standards-02 CONVERT
team/scripts/audit_coding_standards.sh:audit-coding-standards-03 CONVERT
team/scripts/cleanup/cleanup.sh:cleanup-project-root-export-mismatch-01 BLOCK-WORTHY
team/scripts/cleanup/cleanup.sh:cleanup-root-mismatch-01 BLOCK-WORTHY
team/scripts/cleanup/cleanup.sh:cleanup-tasks-dir-export-mismatch-01 BLOCK-WORTHY
team/scripts/cleanup/cleanup_logs.sh:cleanup-logs-log-dir-not-found-01 BLOCK-WORTHY
team/scripts/cleanup/cleanup_logs.sh:cleanup-logs-unknown-argument-01 BLOCK-WORTHY
team/scripts/container-up.sh:container-up-usage-01 BLOCK-WORTHY
team/scripts/container-up.sh:container-up-host-cron-01 BLOCK-WORTHY
team/scripts/container-up.sh:container-up-crontab-check-01 BLOCK-WORTHY
team/scripts/container-up.sh:container-up-engine-not-found-01 BLOCK-WORTHY
team/scripts/container-up.sh:container-up-image-missing-01 BLOCK-WORTHY
team/scripts/container-up.sh:container-up-engine-failed-01 BLOCK-WORTHY
team/scripts/container-up.sh:container-up-heartbeat-unconfirmed-01 BLOCK-WORTHY
team/scripts/container-down.sh:container-down-usage-01 BLOCK-WORTHY
team/scripts/container-down.sh:container-down-kanban-root-missing-01 BLOCK-WORTHY
team/scripts/container-down.sh:container-down-stop-timeout-invalid-01 BLOCK-WORTHY
team/scripts/container-down.sh:container-down-engine-not-found-01 BLOCK-WORTHY
team/scripts/container-down.sh:container-down-engine-failed-01 BLOCK-WORTHY
team/scripts/container-status.sh:container-status-usage-01 BLOCK-WORTHY
team/scripts/container-status.sh:container-status-kanban-root-missing-01 BLOCK-WORTHY
team/scripts/container-status.sh:container-status-engine-not-found-01 BLOCK-WORTHY
team/scripts/container-status.sh:container-status-engine-failed-01 BLOCK-WORTHY
team/scripts/container-status.sh:container-status-report-failed-01 BLOCK-WORTHY
team/scripts/cm/finalize-release.sh:cm-finalize-release-config-unloadable-01 BLOCK-WORTHY
team/scripts/cm/finalize-release.sh:cm-finalize-release-dev-tree-path-unset-01 BLOCK-WORTHY
team/scripts/dashboard/create.sh:dashboard-create-pane-arm-timeout-01 BLOCK-WORTHY
team/scripts/dashboard/generate_window_docs.py:generate-window-docs-check-drift-01 CONVERT
team/scripts/dashboard/lint_pane_refresh_cadence.py:lint-pane-refresh-cadence-violation-01 CONVERT
team/scripts/dashboard/lint_tmux_format_jobs.py:lint-tmux-format-jobs-01 CONVERT
team/scripts/generate_measured_pace_docs.py:generate-measured-pace-docs-check-drift-01 CONVERT
team/scripts/generate_operator_verb_docs.py:generate-operator-verb-docs-check-drift-01 CONVERT
team/scripts/cm/finalize-release.sh:cm-finalize-release-project-required-01 BLOCK-WORTHY
team/scripts/cm/finalize-release.sh:cm-finalize-release-tag-missing-01 BLOCK-WORTHY
team/scripts/cm/finalize-release.sh:cm-finalize-release-version-unresolvable-01 BLOCK-WORTHY
team/scripts/cm/finalize-release.sh:cm-finalize-release-polish-off-main-01 BLOCK-WORTHY
team/scripts/cm/finalize-release.sh:cm-finalize-release-polish-would-strand-tag-01 BLOCK-WORTHY
team/scripts/cm/finalize-release.sh:cm-finalize-release-changelog-render-failed-01 BLOCK-WORTHY
team/scripts/cm/release.sh:operator-force-reason-required-01 BLOCK-WORTHY
team/scripts/cm/release.sh:operator-force-wake-context-01 BLOCK-WORTHY
team/scripts/cm/release.sh:release-git-step-failure-01 BLOCK-WORTHY
team/scripts/cm/release.sh:overwatch-lane-b-intent-unmatched-01 BLOCK-WORTHY
team/scripts/cm/release.sh:release-decide-only-override-refused-01 BLOCK-WORTHY
team/scripts/api-server.sh:api-server-stop-target-not-api-01 BLOCK-WORTHY
team/scripts/api-server.sh:api-server-port-unresolved-01 BLOCK-WORTHY
team/scripts/coder-stale-literal-check.sh:coder-stale-literal-check-lib-missing-01 CONVERT
team/scripts/coder-stale-literal-check.sh:coder-stale-literal-check-not-a-repo-01 CONVERT
team/scripts/deep-verify-freshness-check.sh:deep-verify-freshness-check-01 BLOCK-WORTHY
team/scripts/deep-verify-freshness-check.sh:deep-verify-freshness-check-02 BLOCK-WORTHY
team/scripts/deep-verify-freshness-check.sh:deep-verify-freshness-check-03 BLOCK-WORTHY
team/scripts/deep-verify-freshness-check.sh:deep-verify-freshness-check-04 BLOCK-WORTHY
team/scripts/deep-verify-freshness-check.sh:deep-verify-freshness-check-05 BLOCK-WORTHY
team/scripts/deep-verify-freshness-check.sh:deep-verify-freshness-check-06 BLOCK-WORTHY
team/scripts/deep-verify.sh:deep-verify-02 BLOCK-WORTHY
team/scripts/defer.sh:defer-arm-project-required-01 BLOCK-WORTHY
team/scripts/halt-after.sh:halt-after-project-required-01 BLOCK-WORTHY
team/scripts/halt-after.sh:halt-after-until-malformed-01 BLOCK-WORTHY
team/scripts/halt-after.sh:halt-after-until-scope-mismatch-01 BLOCK-WORTHY
team/scripts/halt-global.sh:halt-global-kanban-root-missing-01 BLOCK-WORTHY
team/scripts/halt-global.sh:halt-global-sentinel-create-failed-01 BLOCK-WORTHY
team/scripts/harness/doc-lint.sh:doc-lint-suite-required-01 CONVERT
team/scripts/harness/doc-lint.sh:doc-lint-suite-unknown-01 CONVERT
team/scripts/harness/strangers-path-check.sh:strangers-path-check-stage-failed-01 BLOCK-WORTHY
team/scripts/import-project.sh:import-project-name-01 BLOCK-WORTHY
team/scripts/import-project.sh:import-project-archive-name-01 BLOCK-WORTHY
team/scripts/import-project.sh:import-project-archive-exists-01 BLOCK-WORTHY
team/scripts/import-project.sh:import-project-archive-placement-01 BLOCK-WORTHY
team/scripts/import-project.sh:import-project-destination-exists-01 BLOCK-WORTHY
team/scripts/import-project.sh:import-project-staging-01 BLOCK-WORTHY
team/scripts/lib/cm/release_decide_only.sh:release-decide-only-halt-01 BLOCK-WORTHY
team/scripts/lib/cm/release_finalize.sh:release-finalize-git-remote-name-01 BLOCK-WORTHY
team/scripts/lib/cm/release_finalize.sh:release-finalize-git-tag-failure-01 BLOCK-WORTHY
team/scripts/lib/cm/release_finalize.sh:release-finalize-hook-failure-01 BLOCK-WORTHY
team/scripts/lib/cm/release_finalize.sh:release-finalize-pretag-version-mismatch-01 BLOCK-WORTHY
team/scripts/lib/cm/release_finalize.sh:release-finalize-release-state-write-failure-01 BLOCK-WORTHY
team/scripts/lib/cm/release_notes.sh:release-notes-changelog-bug-token-leak-01 BLOCK-WORTHY
team/scripts/lib/cm/release_notes.sh:release-notes-changelog-commit-failure-01 BLOCK-WORTHY
team/scripts/lib/cm/release_notes.sh:release-notes-commit-failure-01 BLOCK-WORTHY
team/scripts/lib/cm/release_notes.sh:release-notes-polish-commit-failure-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-active-rc-mismatch-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-active-rc-none-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-branch-missing-local-pushed-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-branch-missing-local-unpushed-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-config-unloadable-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-dev-tree-path-unset-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-git-remote-name-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-halt-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-multiple-rc-branches-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-no-rc-branch-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-project-required-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-rc-repush-failure-01 BLOCK-WORTHY
team/scripts/lib/cm/release_preflight.sh:release-preflight-release-state-missing-01 BLOCK-WORTHY
team/scripts/lib/cm/release_ship_policy.sh:release-ship-policy-stamp-commit-failure-01 BLOCK-WORTHY
team/scripts/lib/cm/release_squash.sh:release-squash-commit-failure-01 BLOCK-WORTHY
team/scripts/lib/cm/release_squash.sh:release-squash-git-remote-name-01 BLOCK-WORTHY
team/scripts/lib/cm/release_version_file.sh:release-notes-version-commit-failure-fresh-01 BLOCK-WORTHY
team/scripts/lib/cm/release_version_file.sh:release-notes-version-commit-failure-reentry-01 BLOCK-WORTHY
team/scripts/lib/config_loader.sh:config-loader-deep-verify-01 BLOCK-WORTHY
team/scripts/lib/config_loader.sh:config-loader-defer-piggyback-01 BLOCK-WORTHY
team/scripts/lib/config_loader.sh:config-loader-deferred-bug-level-01 BLOCK-WORTHY
team/scripts/lib/config_loader.sh:config-loader-deferred-bugs-01 BLOCK-WORTHY
team/scripts/lib/config_loader.sh:config-loader-effort-01 BLOCK-WORTHY
team/scripts/lib/config_loader.sh:config-loader-testing-coverage-01 BLOCK-WORTHY
team/scripts/lib/config_loader.sh:config-loader-testing-pre-merge-cap-01 BLOCK-WORTHY
team/scripts/lib/config_loader.sh:config-loader-testing-workers-01 BLOCK-WORTHY
team/scripts/lib/config_loader.sh:config-loader-wake-integer-01 BLOCK-WORTHY
team/scripts/lib/container_tier.sh:container-tier-unknown-image-01 BLOCK-WORTHY
team/scripts/lib/defer_posture.sh:defer-posture-campaign-malformed-until-01 BLOCK-WORTHY
team/scripts/lib/defer_posture.sh:defer-posture-campaign-unparseable-01 BLOCK-WORTHY
team/scripts/lib/defer_posture.sh:defer-posture-invalid-boolean-01 BLOCK-WORTHY
team/scripts/lib/defer_posture.sh:defer-posture-invalid-level-01 BLOCK-WORTHY
team/scripts/lib/dashboard_palette.sh:dashboard-palette-file-missing-01 BLOCK-WORTHY
team/scripts/lib/dashboard_palette.sh:dashboard-palette-key-missing-01 BLOCK-WORTHY
team/scripts/lib/dev_tree.sh:dev-tree-path-missing-01 BLOCK-WORTHY
team/scripts/lib/dev_tree.sh:dev-tree-path-unconfigured-01 BLOCK-WORTHY
team/scripts/lib/discovery.sh:discovery-ceiling-unreadable-01 BLOCK-WORTHY
team/scripts/lib/discovery.sh:discovery.global-halt BLOCK-WORTHY
team/scripts/lib/discovery.sh:discovery.per-project-halt BLOCK-WORTHY
team/scripts/lib/discovery_guards.sh:discovery-guards-active-rc-in-flight-01 BLOCK-WORTHY
team/scripts/lib/discovery_guards.sh:discovery-guards-ceiling-major-01 BLOCK-WORTHY
team/scripts/lib/discovery_guards.sh:discovery-guards-ceiling-malformed-01 BLOCK-WORTHY
team/scripts/lib/discovery_guards.sh:discovery-guards-ceiling-minor-01 BLOCK-WORTHY
team/scripts/lib/discovery_guards.sh:discovery-guards-ceiling-patch-01 BLOCK-WORTHY
team/scripts/lib/discovery_guards.sh:discovery-guards-release-state-unreadable-01 BLOCK-WORTHY
team/scripts/lib/discovery_state.sh:discovery-state-write-bundle-no-valid-items-01 CONVERT
team/scripts/lib/discovery_steps.sh:discovery-steps-bug-item-shape-01 CONVERT
team/scripts/lib/discovery_steps.sh:discovery-steps-bug-mint-governor-01 CONVERT
team/scripts/lib/discovery_steps.sh:discovery-steps-priority-item-shape-01 CONVERT
team/scripts/lib/discovery_steps.sh:discovery-steps-priority-mint-governor-01 CONVERT
team/scripts/lib/discovery_steps.sh:discovery-steps-stuck-bundle-01 CONVERT
team/scripts/lib/discovery_step_requirements.sh:discovery-step-requirements-01 BLOCK-WORTHY
team/scripts/lib/discovery_step_requirements.sh:discovery-step-requirements-ceiling-unreadable-01 BLOCK-WORTHY
team/scripts/pm-agent.sh:pm-agent-01 BLOCK-WORTHY
team/scripts/pm-agent.sh:pm-agent-ceiling-unreadable-01 BLOCK-WORTHY
team/pm-agent/pm_materialize.py:pm-materialize-01 BLOCK-WORTHY
team/pm-agent/pm_materialize.py:pm-materialize-02 BLOCK-WORTHY
team/scripts/lib/env_bootstrap.sh:env-bootstrap-claude-env-01 BLOCK-WORTHY
team/scripts/lib/gated_runner_lint.sh:gated-runner-stale-findings-claim-01 BLOCK-WORTHY
team/scripts/lib/git_boundary_guard.sh:git-boundary-guard-01 BLOCK-WORTHY
team/scripts/lib/lock_provenance.sh:lock-holder-overdue-01 CONVERT
team/scripts/lib/lock_provenance.sh:lock-provenance-01 BLOCK-WORTHY
team/scripts/lib/lock_provenance.sh:lock-provenance-contended-01 BLOCK-WORTHY
team/scripts/lib/lock_provenance.sh:lock-provenance-release-not-owner-01 CONVERT
team/scripts/lib/lock_provenance.sh:lock-provenance-reverify-01 BLOCK-WORTHY
team/scripts/lib/lock_provenance.sh:lock-stale-threshold-floor-01 CONVERT
team/scripts/lib/overwatch_protocol.sh:zero-progress-cap-01 BLOCK-WORTHY
team/scripts/lib/overwatch_protocol.sh:overwatch-fix-deferred-halt-01 BLOCK-WORTHY
team/scripts/lib/overwatch_lib.sh:overwatch-lib-firing-lock-held-01 BLOCK-WORTHY
team/scripts/lib/overwatch-checks/check-stale-worktrees.sh:stale-worktrees-kept-01 CONVERT
team/scripts/lib/overwatch-checks/check-stale-worktrees.sh:stale-worktrees-state-changed-01 CONVERT
team/scripts/lib/overwatch-checks/check-changelog-drift.sh:check-changelog-drift-backup-failed-01 CONVERT
team/scripts/lib/project_paths.sh:block-triage-invalid-01 BLOCK-WORTHY
team/scripts/lib/project_paths.sh:git-remote-name-invalid-02 BLOCK-WORTHY
team/scripts/lib/project_paths.sh:overwatch-resolve-invalid-01 BLOCK-WORTHY
team/scripts/lib/project_paths.sh:project-resolve-ambiguous-01 BLOCK-WORTHY
team/scripts/lib/project_paths.sh:project-resolve-not-found-01 BLOCK-WORTHY
team/scripts/lib/wake_bootstrap.sh:wake-bootstrap-claude-env-01 BLOCK-WORTHY
team/scripts/lib/wake_bootstrap.sh:wake-bootstrap-load-config-failure-01 BLOCK-WORTHY
team/scripts/lib/wake_bootstrap.sh:wake-bootstrap-python-version-01 BLOCK-WORTHY
team/scripts/lib/wake_bootstrap.sh:wake-bootstrap-require-file-missing-01 BLOCK-WORTHY
team/scripts/lib/wake_common.sh:wake-common-01 BLOCK-WORTHY
team/scripts/lib/wake_common.sh:wake-common-overwatch-halt-01 BLOCK-WORTHY
team/scripts/lib/wake_project.sh:wake-project-block-triage-halt-01 BLOCK-WORTHY
team/scripts/lib/wake_driver_invoke.sh:wake-driver-invoke-01 CONVERT
team/scripts/lib/wake_driver.sh:wake-driver-systemic-block-01 BLOCK-WORTHY
team/scripts/lib/provider_claude.sh:provider-claude-01 BLOCK-WORTHY
team/scripts/lib/provider_claude.sh:provider-claude-02 BLOCK-WORTHY
team/scripts/lib/wake_lifecycle.sh:completion-gate-empty-commit-01 AGENT-RESOLVABLE
team/scripts/lib/wake_provider_pause.sh:wake-provider-pause-01 BLOCK-WORTHY
team/scripts/lib/wake_task_transition.sh:atomic-transition-01 BLOCK-WORTHY
team/scripts/lib/wake_task_transition.sh:atomic-transition-02 BLOCK-WORTHY
team/scripts/lint_doc_command_truth.py:doc-command-truth-01 CONVERT
team/scripts/lint_doc_command_truth.py:doc-key-truth-01 CONVERT
team/scripts/lint_doc_command_truth.py:doc-pane-truth-01 CONVERT
team/scripts/lint_echo_pipe_grep_q.py:echo-pipe-grep-q-01 CONVERT
team/scripts/lint_engine_size_dupe.py:engine-size-dupe-01 CONVERT
team/scripts/lint_help_presence.py:lint-help-presence-exempt-list-missing-01 CONVERT
team/scripts/lint_inline_python.py:inline-python-01 CONVERT
team/scripts/lint_operator_verb_manifest.py:operator-verb-manifest-drift-01 CONVERT
team/scripts/lint_python_duplicates.py:python-duplicates-01 CONVERT
team/scripts/lint_rc_empty_commits.py:rc-empty-commits-01 CONVERT
team/scripts/lint_runnable_remedy.py:runnable-remedy-placeholder-01 CONVERT
team/scripts/lint_skip_bug_gate.sh:lint-skip-bug-gate-findings-01 CONVERT
team/scripts/lint_skip_bug_gate.sh:lint-skip-bug-gate-files-usage-01 CONVERT
team/scripts/lint_skip_bug_gate.sh:lint-skip-bug-gate-tests-dir-missing-01 CONVERT
team/scripts/overwatch-sweep.sh:overwatch-sweep-invalid-lock-wait-01 BLOCK-WORTHY
team/scripts/overwatch-sweep.sh:overwatch-sweep-option-value-required-01 BLOCK-WORTHY
team/scripts/overwatch-sweep.sh:overwatch-sweep-unknown-argument-01 BLOCK-WORTHY
team/scripts/overwatch-sweep.sh:overwatch-sweep-unknown-check-01 BLOCK-WORTHY
team/scripts/recover-rejected.sh:recover-rejected-origin-unknown-01 BLOCK-WORTHY
team/scripts/recover-rejected.sh:recover-rejected-rename-not-basename-01 BLOCK-WORTHY
team/scripts/restamp-fixed-in.sh:restamp-fixed-in-project-required-01 BLOCK-WORTHY
team/scripts/run-cold-build-tests.sh:run-cold-build-tests-01 BLOCK-WORTHY
team/scripts/run-cold-build-tests.sh:run-cold-build-tests-02 BLOCK-WORTHY
team/scripts/run-cold-build-tests.sh:run-cold-build-tests-03 BLOCK-WORTHY
team/scripts/run-gated-suites.sh:run-gated-suites-args-01 AGENT-RESOLVABLE
team/scripts/run-gated-suites.sh:run-gated-suites-args-02 AGENT-RESOLVABLE
team/scripts/run-gated-suites.sh:run-gated-suites-env-bootstrap-01 BLOCK-WORTHY
team/scripts/run-gated-suites.sh:run-gated-suites-out-dir-01 BLOCK-WORTHY
team/scripts/run-gated-suites.sh:run-gated-suites-runner-missing-01 BLOCK-WORTHY
team/scripts/run-gated-suites.sh:run-gated-suites-testing-config-01 BLOCK-WORTHY
team/scripts/run-gated-tests.sh:run-gated-tests-env-bootstrap-01 BLOCK-WORTHY
team/scripts/run-gated-tests.sh:run-gated-tests-pytest-failure-01 BLOCK-WORTHY
team/scripts/run-gated-tests.sh:run-gated-tests-pytest-missing-01 BLOCK-WORTHY
team/scripts/run-integration-tests.sh:container-engine-free-lint-01 BLOCK-WORTHY
team/scripts/run-integration-tests.sh:run-integration-tests-env-bootstrap-01 BLOCK-WORTHY
team/scripts/run-integration-tests.sh:run-integration-tests.container-engine-free-lint BLOCK-WORTHY
team/scripts/run-integration-tests.sh:run-integration-tests-pre-merge-01 AGENT-RESOLVABLE
team/scripts/run-integration-tests.sh:run-integration-tests-pre-merge-02 AGENT-RESOLVABLE
team/scripts/run-integration-tests.sh:run-integration-tests-pre-merge-03 AGENT-RESOLVABLE
team/scripts/run-integration-tests.sh:run-integration-tests-pytest-missing-01 BLOCK-WORTHY
team/scripts/run-integration-tests.sh:run-integration-tests-scratch-sweep-01 CONVERT
team/scripts/run-integration-tests.sh:run-integration-tests-targeted-files-01 AGENT-RESOLVABLE
team/scripts/run-integration-tests.sh:run-integration-tests-targeted-files-02 AGENT-RESOLVABLE
team/scripts/run-integration-tests.sh:run-integration-tests-targeted-files-03 AGENT-RESOLVABLE
team/scripts/run-integration-tests.sh:run-integration-tests-targeted-files-04 AGENT-RESOLVABLE
team/scripts/run-integration-tests.sh:run-integration-tests-targeted-files-05 AGENT-RESOLVABLE
team/scripts/run-integration-tests.sh:run-integration-tests-testing-config-01 BLOCK-WORTHY
team/scripts/run-integration-tests.sh:run-integration-tests-testing-config-02 BLOCK-WORTHY
team/scripts/run-integration-tests.sh:run-integration-tests-testing-config-03 BLOCK-WORTHY
team/scripts/run-integration-tests.sh:run-integration-tests-workers-flag-01 AGENT-RESOLVABLE
team/scripts/run-three-host-matrix.sh:run-three-host-matrix-env-bootstrap-01 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:api-parity-census-gate-01 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:run-unit-tests.api-parity-census-gate BLOCK-WORTHY
team/scripts/run-unit-tests.sh:container-engine-free-lint-01 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:dockerfile-static-lint-01 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:run-unit-tests.container-engine-free-lint BLOCK-WORTHY
team/scripts/run-unit-tests.sh:run-unit-tests.breakage-class-gate BLOCK-WORTHY
team/scripts/run-unit-tests.sh:run-unit-tests.dockerfile-static-lint BLOCK-WORTHY
team/scripts/run-unit-tests.sh:run-unit-tests-env-bootstrap-01 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:run-unit-tests-pytest-failure-01 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:run-unit-tests-pre-merge-01 AGENT-RESOLVABLE
team/scripts/run-unit-tests.sh:run-unit-tests-pre-merge-02 AGENT-RESOLVABLE
team/scripts/run-unit-tests.sh:run-unit-tests-pre-merge-03 AGENT-RESOLVABLE
team/scripts/run-unit-tests.sh:run-unit-tests-pytest-missing-01 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:run-unit-tests-scratch-sweep-01 CONVERT
team/scripts/run-unit-tests.sh:run-unit-tests-targeted-files-01 AGENT-RESOLVABLE
team/scripts/run-unit-tests.sh:run-unit-tests-targeted-files-02 AGENT-RESOLVABLE
team/scripts/run-unit-tests.sh:run-unit-tests-targeted-files-03 AGENT-RESOLVABLE
team/scripts/run-unit-tests.sh:run-unit-tests-targeted-files-04 AGENT-RESOLVABLE
team/scripts/run-unit-tests.sh:run-unit-tests-targeted-files-05 AGENT-RESOLVABLE
team/scripts/run-unit-tests.sh:run-unit-tests-testing-config-01 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:run-unit-tests-testing-config-02 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:run-unit-tests-testing-config-03 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:run-unit-tests-workers-flag-01 AGENT-RESOLVABLE
team/scripts/ship-rc.sh:ship-rc-env-bootstrap-01 BLOCK-WORTHY
team/scripts/undefer.sh:undefer-project-required-01 BLOCK-WORTHY
team/scripts/unhalt-global.sh:unhalt-global-kanban-root-missing-01 BLOCK-WORTHY
team/scripts/unhalt-global.sh:unhalt-global-sentinel-remove-failed-01 BLOCK-WORTHY
team/scripts/upgrade.sh:upgrade-partial-deposit-01 BLOCK-WORTHY
team/scripts/upgrade.sh:upgrade-stamp-helper-missing-01 BLOCK-WORTHY
team/scripts/verdict-report.sh:verdict-report-details-requires-project-01 BLOCK-WORTHY
team/scripts/verify-perproject-devtree-gate.sh:verify-perproject-devtree-gate-assertions-failed-01 BLOCK-WORTHY
team/scripts/verify-perproject-devtree-gate.sh:verify-perproject-devtree-gate-extract-failed-01 BLOCK-WORTHY
team/scripts/verify-perproject-devtree-gate.sh:verify-perproject-devtree-gate-temp-sh-missing-01 BLOCK-WORTHY
team/scripts/verify-rc-state.sh:verify-rc-state-project-required-01 BLOCK-WORTHY
team/scripts/verify-rc-state.sh:verify-rc-state-unexpected-positional-01 BLOCK-WORTHY
team/scripts/wake-batch.sh:wake-batch-provider-script-missing-01 BLOCK-WORTHY
team/scripts/wake-now.sh:wake-now-agent-all-mutually-exclusive-01 BLOCK-WORTHY
team/scripts/wake-now.sh:wake-now-agent-required-01 BLOCK-WORTHY
team/scripts/wake-now.sh:wake-now-agent-value-required-01 BLOCK-WORTHY
team/scripts/wake-now.sh:wake-now-unexpected-positional-01 BLOCK-WORTHY
team/scripts/wake-now.sh:wake-now-unknown-agent-01 BLOCK-WORTHY
team/scripts/why.sh:why-details-requires-project-01 BLOCK-WORTHY
team/scripts/why.sh:why-project-not-found-01 BLOCK-WORTHY
team/pgai_agent_kanban/lib/refusal_ledger.py:refusal-ledger-unregistered-project-01 CONVERT
team/pgai_agent_kanban/lib/git_remote.py:git-remote-name-invalid-01 BLOCK-WORTHY
team/scripts/cm/open-rc.sh:open-rc-git-remote-name-01 BLOCK-WORTHY
team/scripts/init-project-git-repo.sh:init-project-git-repo-git-remote-name-01 BLOCK-WORTHY
team/scripts/lib/refusal_ledger.sh:refusal-ledger-unregistered-project-02 CONVERT
team/tests/conftest.py:tests-conftest-live-projects-leak-01 BLOCK-WORTHY
<!-- CENSUS-LOCATOR-INDEX-END -->

## Coverage statement

Every family named in the ticket is covered: HALT triggers (rows 3–5, 8–24),
ship-policy matrix HALT rows (rows 8–12), fatal lint exits (rows 30–52), matrix
HALT rows (rows 8–12), hook guards (rows 13, 22, 24), and wake-time refusals
(rows 1–2). The completion gate (rows 6–7) and breakage-class pre-flight
(row 25) are covered beyond the named minimum. Every row carries exactly one
class; every BLOCK-WORTHY row carries a doctrine-tied justification; zero rows
are unclassified.

**Note on the `SUBSTRATE_BROKEN` signal.** `team/SOP-core.md` reserves a
`SUBSTRATE_BROKEN` pause signal for substrate-level failures. It is recognized
only by the wake substrate itself and has **no grep-discoverable code site** in
the shipped scripts of this tree, so it is intentionally not tabled above; it is
recorded here so a future reviewer does not read its absence as an omission.
