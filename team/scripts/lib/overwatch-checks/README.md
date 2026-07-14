# overwatch-checks/

## Purpose

This directory holds detection modules for OVERWATCH — the autonomous watchdog
that observes the kanban system for structural anomalies and stale state.

Each module is a sourced bash script that implements a single named check.
Checks emit findings to the action log via `overwatch_log_action` (defined in
`../overwatch_lib.sh`) and return 0 on success, non-zero when a halt-worthy
condition is detected.

## Current Check Catalog

Thirteen modules ship in this directory as of v1.9.0. Eight are the modernized
legacy set from v0.46.0; four were added alongside the reactivation; one was
added in v1.9.0 as the temp-litter backstop. See
`team/roles/OVERWATCH.md` for the role-level view of what each check protects
against — this README is the module-level view.

| # | Module | Scope | Auto-fix path |
|---|---|---|---|
| 1 | `check-bare-tmp-litter.sh` | Bare `/tmp` top-level entries owned by the framework user, created within a known task-session window, not yet reported by the wake bracket | **REPORT-ONLY** — never deletes; snapshot-dedup so each entry is flagged once |
| 2 | `check-empty-files.sh` | Zero-byte files in `bugs/` or `priority/` | Rename to `.orphan` |
| 3 | `check-stale-active-rc.sh` | `release-state.md` Active RC after tag shipped | Reset RC fields to `none` |
| 4 | `check-blocked-tasks.sh` | `BLOCKED` tasks whose Active-RC block has cleared | Promote to `BACKLOG` |
| 5 | `check-tester-orphan-files.sh` | Bug-shaped `vX.Y.Z-*.md` files in `priority/` | Copy to `bugs/BUG-NNNN`, rename original to `.orphan` |
| 6 | `check-cache-marker-drift.sh` | `[x]` markers in backlog caches whose file is `open` | Flip marker to `[ ]` |
| 7 | `check-orphan-rc-branches.sh` | Local `rc/vX.Y.Z` branches whose tag shipped | `git branch -d` local branch |
| 8 | `check-push-lag.sh` | Local `main`/tags ahead of origin | **See gate below** |
| 9 | `check-readme-bundled.sh` | PM tasks with README-shaped inputs | Mark `WONT-DO`, file bug |
| 10 | `check-transient-api-error.sh` | `BLOCKED` tasks with transient-error log tails | Requeue to `BACKLOG` (ceiling 2), else bug-file; residue companion prunes empty per-task worktrees |
| 11 | `check-leaked-listeners.sh` | Processes with cwd under framework temp root | `SIGTERM` on cwd match, bug-file otherwise |
| 12 | `check-version-divergence.sh` | Installed `VERSION` vs dev tree `git describe` | **REPORT-ONLY** — never auto-fix |
| 13 | `check-stale-worktrees.sh` | `git worktree list` entries for terminal-state tasks older than threshold | `git worktree prune`-class only; branch-carrying worktrees bug-file |

Modules are sourced in lexical order by the OVERWATCH driver
(`team/scripts/overwatch-sweep.sh` for the Tier-1 sweep, the OVERWATCH agent
wake for Tier-2). Ordering matters only for logging determinism — the checks
are independent of each other.

## check-push-lag: the `push_to_remote` gate

`check-push-lag` is the only check in this directory that touches origin, and
it is gated on the per-project `push_to_remote` key from `project.cfg`. This
gate is the reactivation prerequisite for the v1.4.0 OVERWATCH surface.

- **`push_to_remote = false`** (local-first posture) — local-ahead state on
  `main` and unpushed tags are by design. The check logs `staged-by-design` to
  the action log, changes nothing, and never pushes. Without this gate the
  pre-modernization behavior would force-push deliberately staged work to the
  public repo.
- **`push_to_remote = true`** (remote-mode posture, the default) — the legacy
  path is intact: after the HALT-first protocol and the per-repo flock check,
  OVERWATCH pushes `main` and tags to origin.

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

The Tier-2 agent wake fires on the standard `wake-batch.sh --agent=overwatch`
path. It does not run these modules directly — it consumes the action log the
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
