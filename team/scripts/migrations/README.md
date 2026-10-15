# scripts/migrations/ — Migration Convention

This directory holds post-install migration scripts for the pgai-agent-kanban.
The directory and its convention give every migration a documented,
forward-compatible home. `migrate_v2.22.5_to_v2.23.0.sh` is the first real
migration to land in it (seeds the commented `overwatch_resolve` key into
every already-registered project's project.cfg — see that script's own
header for its contract). `migrate_v2.24.7_to_v2.24.8.sh` seeds
`tasks/queues/overwatch_backlog.md` into every already-registered project,
matching what `create-project.sh` now writes for a brand-new one — see that
script's own header for its contract.

---

## Naming convention

```
migrate_vX.Y.Z_to_vA.B.C.sh
```

The `from_to` form is mandatory. Both endpoints are explicit so the direction
is unambiguous: the script upgrades a system currently running vX.Y.Z to the
layout expected by vA.B.C.

Examples:
- `migrate_v1.0.0_to_v1.1.0.sh` — upgrades a v1.0.0 install to v1.1.0 layout
- `migrate_v1.2.0_to_v1.3.0.sh` — upgrades a v1.2.0 install to v1.3.0 layout

---

## Sparse rule

A migration script exists **only** when a version change introduces a
config-format or layout change that a plain file-deposit upgrade cannot handle
automatically. Most version bumps need no migration; gaps are normal and expected.

Do **not** create a stub or no-op migration just to fill a version gap.

---

## Selection rule (destination-keyed)

When upgrading from an installed version I to a target version T, run every
migration script whose **destination version** (the `vA.B.C` part) falls within
the half-open interval `(I, T]`, in ascending semver order.

The selection key is the **destination version** (`vA.B.C`), not the source
version (`vX.Y.Z`). The `from` endpoint documents intent and guards against
running a script in the wrong context; it is not the sort key.

**Example:** upgrading v1.0.0 → v1.5.0 with two migrations present:
- `migrate_v1.0.0_to_v1.1.0.sh` — destination v1.1.0 is in (v1.0.0, v1.5.0] → **run**
- `migrate_v1.2.0_to_v1.3.0.sh` — destination v1.3.0 is in (v1.0.0, v1.5.0] → **run**

Run in ascending destination-version order: v1.1.0 script first, then v1.3.0.
The absence of v1.1.0→v1.2.0 and v1.3.0→v1.5.0 migrations is fine — those
versions introduced no config or layout changes.

---

## Migration contract

Every migration script must honour the following contract:

1. **Additive** — preserve every existing config key and data field. Default new
   fields; never delete present fields.
2. **Non-destructive** — if a field or file that should be removed is encountered,
   emit a clear warning to stderr rather than silently dropping it. Let the
   operator decide what to do with it.
3. **Idempotent** — the script can be run multiple times without additional
   side-effects. Each step must detect whether it has already been applied and
   skip if so.

These three properties together ensure a migration script is safe to re-run as
a diagnostic tool and that running the wrong script by accident (or running
scripts out of order) produces a clear error rather than silent data loss.

---

## Running migrations by hand

Upgrades apply their own migrations — see "Auto-runner — runs on every
upgrade" below. Use the procedure here when you need to apply a migration
outside an upgrade: after an upgrade whose migration step failed, when a
project was registered after the upgrade ran, or to preview what a range
would do before you commit to it.

1. Identify the installed version (`vX.Y.Z`) and the version you are moving
   to (`vA.B.C`). An upgrade names the range it covered in its own
   `Checking for migrations in (…, …]` line; `cat
   "$PGAI_AGENT_KANBAN_ROOT_PATH/VERSION"` gives you what is installed now.

2. Preview the selection without writing anything:

   ```bash
   bash scripts/migrations/run-migrations.sh --from vX.Y.Z --to vA.B.C --dry-run
   ```

   The runner prints the scripts whose destination version falls in
   `(vX.Y.Z, vA.B.C]`, in the order it would run them, and forwards
   `--dry-run` to each so every script previews its own writes. A selection
   of zero scripts is normal and is not an error — see "Sparse rule" above.

3. Apply them:

   ```bash
   bash scripts/migrations/run-migrations.sh --from vX.Y.Z --to vA.B.C
   ```

   Add `--project <name>` to limit the run to one registered project; omit it
   to cover every registered project. Both flags are forwarded to each
   selected script. A worked range — the one that seeds the
   `overwatch_resolve` key into an install still on v2.22.5:

   ```bash
   bash scripts/migrations/run-migrations.sh --from v2.22.5 --to v2.23.0
   ```

4. Verify the output. Each script prints a summary; review it for warnings.
   The runner exits `0` on success (including the nothing-to-apply case), `1`
   on a usage error, and `2` when a selected script fails — in which case it
   names the failing script and stops before running any later one.

Because every migration is additive, non-destructive, and idempotent, re-running
this command over a range you have already applied is safe: each script detects
its own prior application and no-ops.

You can still invoke one script directly —
`bash scripts/migrations/migrate_vX.Y.Z_to_vA.B.C.sh` — when you want exactly
that one. Each script is self-contained and can be run from any directory as
long as `PGAI_AGENT_KANBAN_ROOT_PATH` is set (or the kanban root is at
`~/pgai_agent_kanban`). Prefer the runner whenever you are covering a version
range: it applies the selection rule for you instead of asking you to apply it
by eye.

> **Security note — the upgrade backup contains cleartext secrets.** The upgrade
> step that precedes migration writes a backup tarball of your config files, and
> that tarball **includes the `secrets` file in cleartext** (so a full restore is
> possible). `upgrade.sh` creates the tarball `chmod 600`. Treat upgrade backups
> as sensitive credential material: keep them off shared storage, do not commit
> them, and delete them once you no longer need the restore point.

---

## Auto-runner — runs on every upgrade

`upgrade.sh` applies migrations for you. There is nothing to opt into and no
flag to remember.

In phase 2, immediately after the VERSION stamp, `upgrade.sh` invokes

```
scripts/migrations/run-migrations.sh --from <installed> --to <target>
```

where `<installed>` is the version captured before the stamp and `<target>` is
the version just stamped. The runner then:

- enumerates every `migrate_vX.Y.Z_to_vA.B.C.sh` in this directory,
- selects those whose destination version falls in `(installed, target]`,
  parsing names by the same `from_to` convention and destination-keyed
  selection rule documented above,
- runs them in ascending destination-version order with operator-visible
  output, and
- aborts on the first script that exits non-zero.

Neither `--project` nor `--dry-run` is passed on this path, so an upgrade
applies every selected migration to every registered project, for real.

Three cases the upgrade step passes over without failing:

- **Nothing selected.** The common case. The runner reports "nothing to
  apply" and exits 0.
- **Installed and target are the same version.** An empty `(X, X]` range
  selects nothing, so the step is skipped.
- **A version that is not released semver.** A stamped identity
  (`--stamp-version`, and the test fixtures that use it) is not a `vX.Y.Z`
  tag, so no migration destination could ever match it. The step is skipped
  and says so.

If the runner exits non-zero, `upgrade.sh` fails loud and exits 1. The deposit
and the VERSION stamp have already completed by then, so the recovery is to fix
the underlying cause and re-run either the upgrade or the runner directly over
the same range — see "Running migrations by hand" above. Migrations are
idempotent, so the ones already applied no-op on the second pass. If the runner
is missing from the install altogether, `upgrade.sh` warns and continues.

A fresh `install.sh` runs no migrations: there is no installed version to
migrate from.

---

## Authoring a new migration script

1. Name the script `migrate_vX.Y.Z_to_vA.B.C.sh` where `vX.Y.Z` is the last
   version that does NOT require the migration and `vA.B.C` is the first version
   that does.

2. Add a header comment block that documents:
   - what config or layout change this migration handles,
   - what each step does, and
   - the idempotency guarantee.

3. Honour the contract: additive, non-destructive, idempotent.

4. Test by running on a fixture that mimics the pre-migration state, then run
   again to verify idempotency.

5. Add this script to the list in the release notes for `vA.B.C`.
