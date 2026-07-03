# scripts/migrations/ — Migration Convention

This directory holds post-install migration scripts for the pgai-agent-kanban.
At v1.0.0 it is **empty of actual migrations** — the directory and its
convention exist so future migrations have a documented, forward-compatible home.

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

## Manual operator procedure (v1.0.0 — no migrations exist)

After every upgrade:

1. Identify the installed version before upgrade (`I`) and the version being
   installed (`T`). These are typically printed by `upgrade.sh`.

2. List migration scripts whose destination version falls in `(I, T]`:

   ```bash
   ls scripts/migrations/migrate_v*.sh 2>/dev/null | sort -V
   ```

   At v1.0.0 there are no scripts, so this step is a **no-op**.

3. For each script in ascending semver order, run:

   ```bash
   bash scripts/migrations/migrate_vX.Y.Z_to_vA.B.C.sh
   ```

   Each script is self-contained and can be run from any directory as long
   as `PGAI_AGENT_KANBAN_ROOT_PATH` is set (or the kanban root is at
   `~/pgai_agent_kanban`).

4. Verify the output. Each script prints a summary; review for warnings.

> **Security note — the upgrade backup contains cleartext secrets.** The upgrade
> step that precedes migration writes a backup tarball of your config files, and
> that tarball **includes the `secrets` file in cleartext** (so a full restore is
> possible). `upgrade.sh` creates the tarball `chmod 600`. Treat upgrade backups
> as sensitive credential material: keep them off shared storage, do not commit
> them, and delete them once you no longer need the restore point.

---

## Auto-runner — deferred

**The automatic migration runner is not implemented in this release (v1.0.0).**

The manual procedure above is the supported path. An auto-runner will be
integrated into `upgrade.sh` when the first real migration is written. At that
point `upgrade.sh` will:

- enumerate all migration scripts in this directory,
- filter to those whose destination version falls in `(installed, target]`,
- run them in ascending semver order with operator-visible output, and
- abort with a clear error if any script exits non-zero.

The auto-runner will parse script names using the same `from_to` convention
and destination-keyed selection rule documented above. Scripts placed in this
directory today are forward-compatible with that future runner.

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
