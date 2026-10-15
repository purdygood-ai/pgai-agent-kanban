# overwatch-checks/

## Purpose

This directory holds detection modules for OVERWATCH — the autonomous watchdog
that observes the kanban system for structural anomalies and stale state.

Each module is a sourced bash script that implements a single named check.
Checks emit findings to the action log via `overwatch_log_action` (defined in
`../overwatch_lib.sh`) and return 0 on success, non-zero when a halt-worthy
condition is detected.

## Current Check Catalog

Fifteen modules ship in this directory. See `team/roles/OVERWATCH.md` for the
role-level view of what each check protects against — this README is the
module-level view.

| # | Module | Scope | Auto-fix path |
|---|---|---|---|
| 1 | `check-bare-tmp-litter.sh` | Bare `/tmp` top-level entries owned by the framework user, created within a known task-session window, not yet reported by the wake bracket | **REPORT-ONLY** — never deletes; snapshot-dedup so each entry is flagged once |
| 2 | `check-empty-files.sh` | Zero-byte **report** files in `bugs/` or `priority/`. Dotfiles are never scanned at any size — a leading `.` marks protocol machinery (claim-lock sentinels, the claim ledger, similar in-flight state), not a report | Rename to `.empty.orphan`; separately prunes claim-lock-shaped `.empty.orphan` residue. Every other `.empty.orphan` stays put as evidence |
| 3 | `check-stale-active-rc.sh` | `release-state.md` Active RC after tag shipped | Reset RC fields to `none` |
| 4 | `check-blocked-tasks.sh` | `BLOCKED` tasks that carry no `## Block Record`, so block triage cannot see them | **REPORT-ONLY** — narrates each one with its `Needs Human` value; never writes |
| 5 | `check-tester-orphan-files.sh` | Bug-shaped `vX.Y.Z-*.md` files in `priority/` | Copy to `bugs/BUG-NNNN`, rename original to `.orphan` |
| 6 | `check-cache-marker-drift.sh` | `[x]` markers in backlog caches whose file is `open` | Flip marker to `[ ]` |
| 7 | `check-orphan-rc-branches.sh` | Local `rc/vX.Y.Z` branches whose tag shipped | `git branch -d` local branch |
| 8 | `check-push-lag.sh` | Local `main`/tags ahead of origin | **See gate below** |
| 9 | `check-readme-bundled.sh` | PM tasks with README-shaped inputs | Mark `WONT-DO`, file bug |
| 10 | `check-transient-api-error.sh` | `BLOCKED` tasks with transient-error log tails | Requeue to `BACKLOG` once the backoff window has passed (5, 10, then 20 min after the block, `transient_backoff_due`; inside it the task is left as found and a `transient-requeue-backoff` row is logged), below the shared `[wake] transient_retry_cap` ceiling; at the ceiling bug-file and escalate; residue companion prunes empty per-task worktrees |
| 11 | `check-leaked-listeners.sh` | Processes with cwd under framework temp root | `SIGTERM` on cwd match, bug-file otherwise |
| 12 | `check-version-divergence.sh` | Installed `VERSION` vs dev tree `git describe` | **REPORT-ONLY** — never auto-fix |
| 13 | `check-stale-worktrees.sh` | `git worktree list` entries for finished (`DONE` / `WONT-DO`) tasks older than threshold; a `BLOCKED` task's worktree is never a candidate | Under `overwatch_halt_first_fix`, after re-reading the task's state: `git worktree remove` (never `--force`) and `git worktree prune` for a worktree with nothing to lose. One holding uncommitted or untracked changes is kept and reported (`worktree-kept:holds-work`, judged by `pgai_agent_kanban.overwatch.worktree_residue`); branch-carrying worktrees (commits on no `main` / `<branch_prefix>main` / `rc/*` / `<branch_prefix>rc/*`) bug-file |
| 14 | `check-block-triage.sh` | `BLOCKED` tasks carrying a `## Block Record`, whatever their `Needs Human` value | Classify the record (`block-classifier.yaml`) and carry out the decision through the block-triage executor (`pgai_agent_kanban.overwatch.block_triage`): escalate (`Needs Human: yes` + one-paragraph `## OVERWATCH Diagnosis`; the task stays `BLOCKED`), record a first cool-down, or — for an action with a handler — `## Triage Note`, attempts +1, `cooldown_until`, then `promote_blocked_to_backlog` / `transition_task_state` (a recomputed completion gate that passes closes the task as `DONE`; stale worktree residue is cleared before a requeue; an allowlisted operator data verb runs with validated typed arguments before a requeue), or hand a `ship_policy_halt` block to the existing resolution stage, writing nothing of the task. Every write runs under `overwatch_halt_first_fix` after per-task backups and an evidence file, and logs `block-triage:<reason_code>:<action>`. An already-escalated block epoch is left alone. `[overwatch] block_triage = false` keeps it to records and diagnoses. See "check-block-triage: the safety envelope" below |
| 15 | `check-changelog-drift.sh` | `CHANGELOG.md` on the main branch (`<branch_prefix>main`) differs from a fresh regeneration by `changelog_writer.py` against the bug ledger; skipped silently when the dev tree's HEAD is on any other branch | Under `overwatch_halt_first_fix`, holding the project's repo lock and only while Active RC still reads `none`: install the regenerated file and commit it to the main branch. A held lock, an active RC, or a HALT narrates the skip and changes nothing; the next sweep retries |

Modules are sourced in lexical order by the OVERWATCH driver
(`team/scripts/overwatch-sweep.sh` for the Tier-1 sweep, the OVERWATCH agent
wake for Tier-2). Ordering matters only for logging determinism — the checks
are independent of each other.

## check-block-triage: the safety envelope

`check-block-triage.sh` is the one check that acts on blocked tasks. The
module decides nothing itself: `pgai_agent_kanban.overwatch.block_triage plan`
lists every `[B]` task that reads `BLOCKED` and carries a `## Block Record`
with the outcome the classifier's decision leads to, and `apply` makes the
writes for one task. Around each task with something to write, the check:

1. runs under `overwatch_halt_first_fix` — `HALT_OVERWATCH` (flag file or
   env var) or a held per-project repo lock leaves the task unchanged, logs
   a `block-triage-refused` row (the lock row names the holder), and stops
   the run; a present `HALT` (the fleet-wide one, or the project's own)
   leaves the task unchanged with a `fix-deferred` row and moves on to the
   next task;
2. backs up `status.md` and the queue file with `overwatch_backup_file`
   under `backups/<firing timestamp>/<task id>/`;
3. calls `apply`, which writes an evidence file under the task's
   `artifacts/block-evidence/` before anything else, then its own
   `status.md` / `README.md` writes;
4. performs the state mutation `apply` names (none, `promote` or
   `transition:<STATE>`) only through `promote_blocked_to_backlog` or
   `transition_task_state`;
5. logs `block-triage:<reason_code>:<action>` with the `status.md` backup.

A task the plan names `defer` (a `ship_policy_halt` block) takes steps 1, 3
and 5 only: nothing of the task is written, so no backup is taken, `apply`
runs with `--defer-only` (it writes nothing unless the plan under the lock
is still `defer`), and the row's backup path is `none`. It is the one task
that still runs under the project's `HALT`: step 1 passes
`--ship-policy-resolution`, because the resolution stage exists to resolve
the HALT CM wrote. The fleet-wide `HALT` defers it like any other.

It never commits, merges, checks out, pushes or deletes a branch, and never
resets a task; the one repository write it makes is clearing a stale
worktree registration (`git worktree remove` without `--force`, then
`git worktree prune`), which deletes no branch and no commit. The check
resolves the worktree base with `worktree.sh`'s `pgai_worktree_base` and
passes it to the executor as `--worktree-base`. An action
the executor has no handler for fails closed: it escalates a task whose
`Needs Human` is `yes`, and leaves a task whose `Needs Human` is `no` to the
handler that recorded the block.
`--dry-run` narrates what would happen and writes nothing.

### Requeue classes and verification

The executor performs `requeue_owner` for three reason codes:

| reason_code | What the requeue does | Attempts / window | Verified on the next block by |
|---|---|---|---|
| `per_task_timeout` | snapshots the commits the feature branch holds ahead of the source branch to `artifacts/block-evidence/commits-ahead-<UTC>.txt` (read-only git), then requeues with `resume: branch` and the note "previous attempt was killed at Ns"; a snapshot that cannot be taken escalates | 1 | `next_outcome_not_timeout` |
| `provider_refusal` | after a first 15-minute cool-down, requeues and resets the `## Zero Progress` count to 0 | 3; 15, 30, then 60 min | `zero_progress_not_readvanced` |
| `zero_progress` | requeues with a note | 1 | `zero_progress_not_readvanced` |

Each requeue appends a `pending` entry to the task's
`artifacts/block-evidence/verification.json`. When the task blocks again,
the entry is verified against the new block: passed restarts the block
epoch (the new block is a new incident, classified on its own); failed
escalates with the failed verification in the diagnosis, unless the same
failure came back and the row still allows an attempt (a provider that
keeps refusing); a provider refusal still refusing after 3 escalates naming
a fleet-level cause (authentication, model or usage limit). A task still
`BLOCKED` on the block occurrence an action was taken on escalates: the
same block is never acted on twice.

### Gate and worktree classes

Four git-facing reason codes are acted on with facts read from the dev tree
when the decision is made (read-only git), each once per block epoch:

| reason_code | What triage does | Stays with a human when | Verified on the next block by |
|---|---|---|---|
| `gate_merge_not_found` | (a) the feature branch is already an ancestor of its source branch: the completion gate's checks are recomputed read-only (no ref moves, no reflog grows), a `--no-ff` merge commit bringing the branch in is required, `Needs Human` -> `no`, `Blockers` -> `none`, and the task transitions to `DONE`; (b) otherwise, a conflict-free `merge-tree --write-tree` requeues the CODER/WRITER owner with `resume: branch` and the note "merge only, then re-complete" | a merge-tree conflict (the diagnosis names the paths), another owner role, an ancestor with no merge commit or a dirty worktree, or a second refusal | `gate_passes` |
| `gate_dirty_worktree` | saves the worktree's uncommitted changes to `artifacts/block-evidence/dirty.patch` (tracked changes plus every untracked file as a new-file diff; `git apply` restores them), lists the untracked files in `untracked.txt` and the patch hash in `dirty.patch.sha256`, commits nothing, and requeues the owner with `resume: branch`, the Triage Note naming the patch and its hash | the worktree is gone or no longer dirty, or a second refusal | `gate_passes` |
| `gate_empty_commit` | requeues the owner with `resume: branch` and a note quoting the empty commits' shas | no sha can be quoted, or a second refusal | `gate_passes` |
| `worktree_create_failed` | a registered worktree claiming the task's worktree path or feature branch, inside the worktree base, is listed in `artifacts/block-evidence/worktree-residue-<UTC>.txt`, removed (`git worktree remove`, never `--force`) and pruned; the task is requeued so the next dispatch retries the create | the source (RC) branch is missing; the residue's HEAD reaches a commit no other branch, tag or remote-tracking ref holds; the residue is outside the worktree base, locked, or holds uncommitted changes; or the next create fails again. Nothing is removed in any of these | `next_create_succeeds` |

A recomputed gate that passes settles its verification at once (`passed`);
the other actions leave a `pending` entry, failed by another gate refusal
(`gate_passes`) or another failed create (`next_create_succeeds`).

### Operator data actions

`agent_operator_data_action` is acted on with `run_allowlisted_verb` only
for a verb on the allowlist (`pgai_agent_kanban.overwatch.allowlisted_verbs`;
the typed-argument validators are `ACTION_VERBS` in
`pgai_agent_kanban.overwatch.block_record`). Every Block Record value is
untrusted: the arguments are re-validated when the decision is made and
again immediately before the run, and the verb runs in-process with each
typed argument passed as its own value — no shell, no command string.

| verb | Arguments | Targets that must check out | What runs |
|---|---|---|---|
| `restamp-fixed-in` (`restamp-fixed-in.sh`) | `project`, `version` (`vX.Y.Z`), `bugs` (comma-separated `BUG-NNNN`) | the project is registered in `projects.cfg` by its exact name and has a `bugs/` ledger; every bug resolves to exactly one file there and is `done` | the script's own body (`pgai_agent_kanban.cm.fixed_in_restamp`): stamps `## Fixed In` on each named bug whose value is empty, never overwriting a set value |

The verb runs once, then again; the re-run must report 0 changes, or the
block escalates. Both runs' output goes to
`artifacts/block-evidence/verb-<verb>-<UTC>.txt`, and the owner is requeued
with a Triage Note naming both results. A record naming no verb (a
prose-only remedy), a verb not on the allowlist, an invalid argument, an
unknown project, or a bug that is missing or not done escalates, and
nothing runs. Once per block epoch; verified on the next block by
`rerun_reports_no_changes` (failed when the task asks for an operator data
action again).

### Ship-policy deferral

`ship_policy_halt` is handed to the existing resolution stage
(`pgai_agent_kanban.overwatch.resolution_stage`, the same stage the
overwatch wake runs) — no new authority, and the stage's own ceiling. It is
planned only while the project's `HALT` carries the CM ship-policy header;
otherwise the task is skipped and stays with a human. `apply` runs the stage
once and the `block-triage:ship_policy_halt:defer_to_resolution_stage` row
reports its decision: verified when the stage executed and appended a
`## OVERWATCH Decision` event to `release-state.md`; a decline leaves the
task with a human, unchanged. Every other write is the stage's own (its
`resolution-stage` action-log row, its Decision event, its ledger entry and
its drill); triage writes no evidence, Block Record, Triage Note, diagnosis
or state.

`transient_api_error` stays with the transient-error handlers (this
directory's `check-transient-api-error.sh` and the wake's auto-retry, both
behind `transient_backoff_due`): below the `## Transient Retry` ceiling the
block is skipped, at it the block escalates.

## check-push-lag: the `push_to_remote` gate

`check-push-lag` is the only check in this directory that touches origin, and
it is gated on the per-project `push_to_remote` key from `project.cfg`. This
gate is the reactivation prerequisite for the OVERWATCH surface.

- **`push_to_remote = false`** (local-first posture) — local-ahead state on
  `main` and unpushed tags are by design. The check logs `staged-by-design` to
  the action log, changes nothing, and never pushes. Without this gate the
  pre-modernization behavior would force-push deliberately staged work to the
  public repo.
- **`push_to_remote = true`** (remote-mode posture, the default) — the legacy
  path is intact: after the HALT-first protocol and the per-repo flock check,
  OVERWATCH pushes `main` and tags to origin.
- **Unresolved project** — the project is not registered in `projects.cfg`,
  has no `project.cfg` of its own, or that file names no `dev_tree_path` (a
  no-git project, a half-built config, or a sandbox root a test builds). The
  check declines before any git call: one stderr line with the status
  `skipped-unresolved-project` names the missing piece, no remote is
  contacted, and it exits 0. It never falls back to `PGAI_DEV_TREE_PATH`,
  which names one global checkout; pushing that checkout's tags under another
  project's `push_to_remote` setting would publish the wrong repository's
  refs.

The decision comes from the shared push-decision resolver
(`pp_push_decision` in `scripts/lib/push_decision.sh`, over
`pgai_agent_kanban.lib.push_decision`), which the CM push watchdog
(`scripts/cm/push-watchdog.sh`) also uses, so both out-of-band push sites
apply one rule.

Behavioral fixtures cover both modes. `staged-by-design` is a first-class
action-log outcome distinct from a successful push — the log is the operator's
audit trail for telling the local-first pattern from a real replay of CM's
work.

Every other check in this directory is safe to run regardless of
`push_to_remote`; the push-lag gate is the only origin-touching decision the
sweep can make.

## Module Naming Convention

```
check-<slug>.sh
```

Examples:
- `check-empty-files.sh`
- `check-push-lag.sh`
- `check-transient-api-error.sh`

The exported function name mirrors the slug with underscores:
`overwatch_check_<slug_with_underscores>` (e.g. `overwatch_check_push_lag`).

## Module Contract

Every module in this directory must:

1. Be sourceable without side effects (no top-level commands that alter state).
2. Export a single function named `overwatch_check_<slug>` where `<slug>`
   matches the filename's slug (with hyphens replaced by underscores).
3. Accept zero arguments. All context is read from the environment. The
   driver sets:
   - `KANBAN_ROOT` — absolute path to the kanban installation.
   - `OVERWATCH_PROJECT` — the project name currently being swept.
   - Any check-specific optional variables (documented in the module header).
4. Return 0 when no issue is detected; return non-zero when an anomaly is
   detected and logged. REPORT-ONLY checks return 0 in all cases.
5. Use `overwatch_log_action` to record findings — never write directly to
   actions.log.
6. Back up any state file before modifying it via `overwatch_backup_file`.
   The backup lands under
   `$PGAI_PROJECT_ROOT/overwatch/backups/<TIMESTAMP>/<basename>` and is
   referenced by relative path in the action-log entry.
7. Never mutate kanban task state (`status.md`, queue files) outside the
   narrow auto-fix scope declared in the module's header. Anything outside
   scope is bug-file.

Modules should also honor two conventions used across the current set:

- Support a `--dry-run` mode when invoked directly (scans and logs findings
  but does not apply any auto-fix). Sourced use goes through the driver,
  which owns dry-run selection at the sweep level.
- Emit stderr diagnostics for internal errors (missing dependencies,
  unreadable state) and return 1. The driver treats these as sweep-level
  failures without aborting the rest of the modules.

## When Modules Are Invoked

The Tier-1 sweep runner `team/scripts/overwatch-sweep.sh` iterates every
registered project (aggregation form — no `--project` argument) and, for each,
sources every `check-*.sh` file in this directory in lexical order and invokes
the exported function with `OVERWATCH_PROJECT` set to the current project. Per-
project sweep logs land under `projects/<name>/logs/overwatch/sweep.log`.

The driver manages the per-firing flock, HALT/HALT_OVERWATCH checks, and
state-dir bootstrapping before calling any module. Individual modules must not
touch those primitives themselves.

A project with its own `projects/<name>/HALT` is still swept: its read-only
scans, reports and bug filing run, and the sweep logs one
`sweep-fixes-deferred` row for it. Every fix a module attempts goes through
`overwatch_halt_first_fix`, which defers it while any HALT is present — the
fleet-wide one or a project HALT OVERWATCH did not create — returning
sentinel `5` with a `fix-deferred` row ("fix deferred: HALT present") and
nothing changed. The only fix that runs under a project HALT is block
triage handing a `ship_policy_halt` task to the resolution stage.

The cron tier runs the sweep with no options. Two options serve event-driven
callers and operators:

- `--only <check-slug>` runs a single module (`--only block-triage` runs
  `check-block-triage.sh`) for every registered project. A slug that names no
  module is refused dual-channel (exit 2) with the available slugs listed, and
  nothing is swept.
- `--lock-wait <seconds>` makes a module that finds a project's repo lock held
  keep retrying it with backoff for up to that many seconds
  (`overwatch_halt_first_fix`, through `OVERWATCH_LOCK_WAIT_SECONDS`) before it
  declines and leaves the task unchanged. The default, 0, declines at once.

The on-BLOCK nudge uses both: when a wake chain has blocked a task, it runs
`overwatch-sweep.sh --only block-triage --lock-wait 60` in the background after
it has released the project's repo lock (`fire_block_triage_nudge`,
`team/scripts/lib/wake_project.sh`), and the sweep's output is appended to
`logs/overwatch-trigger.log` under the kanban root.

The Tier-2 agent wake fires on the standard `wake-batch.sh --agent=overwatch`
path. It runs one module directly: `run_overwatch_block_triage`
(`team/scripts/lib/wake_project.sh`) runs `check-block-triage.sh` for each
project before the wake takes that project's repo lock, logging to
`logs/overwatch-block-triage.log`. Otherwise it consumes the action log the
Tier-1 sweep produced and files bugs for anything outside the whitelist.

## Adding a New Check

1. Create `check-<slug>.sh` in this directory following the contract above.
2. Add a corresponding entry to the module header documenting scope,
   auto-fix path (if any), and required/optional environment variables.
3. Update the catalog table above and the role-level entry in
   `team/roles/OVERWATCH.md` under the Whitelist section.
4. Test that `bash -n check-<slug>.sh` passes.
5. Test that sourcing the file produces no output.
6. Verify the check function returns 0 on a clean system.
7. Add a fixture under `tests/` exercising both the detection path and the
   auto-fix (or bug-file) path.

New auto-fix scopes require review at the role level — the whitelist is
deliberately conservative. When in doubt, ship the module as bug-file only and
add the auto-fix path in a follow-up once fixtures prove it safe.
