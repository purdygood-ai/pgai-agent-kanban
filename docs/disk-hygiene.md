# Disk Hygiene

Operator guide for `scripts/cleanup/purge-old-files.sh`: why the kanban accumulates filesystem state, what the script does, how to run it safely, every CLI flag, recommended retention values, the safety guarantees you can rely on, and how to wire it into cron.

If you only ever read one section, read **Quick start**. The script is dry-run by default; you can preview every change before anything is deleted.

## Overview

The kanban is an autonomous system. It runs on a wake-script schedule, and every wake produces filesystem state — task folders, status files, artifact directories, debug logs. None of that state is wrong; the framework needs it during the work and shortly after. Over weeks of operation, though, it accumulates faster than most VPS disks can absorb without help.

A representative load looks like this:

- **Task folders** under `projects/<name>/tasks/CLAUDE-*` — every agent invocation creates one (with a `README.md`, a `status.md`, an `artifacts/` directory, a `logs/` directory). At roughly 23 RCs per day with about 10 tasks per RC, that is ~230 task folders per day per project. Thirty days in, you are looking at 7,000+ folders per project.
- **Log archives** under `projects/<name>/logs/` and `$KANBAN_ROOT/logs/` — `cleanup.sh` rotates daily debug logs into per-day archive directories, but the archives themselves accumulate.
- **Shipped requirement bundles** under `projects/<name>/requirements/` — `vX.Y.Z-bugfix-bundle-*.md` and `vX.Y.Z-priority-bundle-*.md` files remain after the release ships.
- **Closed bug and priority files** under `projects/<name>/bugs/` and `priority/` — operator-authored intake files persist after their target release ships.

Left alone, this state will eventually fill the disk and stall the chain. The recovery from a full disk mid-RC-cycle is unpleasant and typically results in heavier-than-necessary cleanup performed in a hurry. `scripts/cleanup/purge-old-files.sh` exists so you can stay ahead of disk pressure on a planned cadence rather than reacting to it.

The script is purely additive. It does not replace `scripts/cleanup/cleanup.sh` — see "Relationship to scripts/cleanup/cleanup.sh" below.

## Quick start

Preview what would be purged with all defaults — safe, no deletions:

```bash
scripts/cleanup/purge-old-files.sh
```

The output is grouped by project and category. Each candidate is tagged `[WOULD PURGE]`. Nothing is deleted. Read the preview, decide you are comfortable, then run the same command with `--apply`:

```bash
scripts/cleanup/purge-old-files.sh --apply
```

That is the entire daily workflow. Everything else in this guide is tuning.

## Full CLI reference

Every flag, every default, with at least one example for each.

### `--days N`

Default retention in days for all categories except log archives. Files older than this many days become candidates for purge. Default: `30`.

```bash
# Use a 14-day cutoff for everything except logs
scripts/cleanup/purge-old-files.sh --days 14
```

Per-category overrides (below) take precedence over `--days` when both are set.

### `--tasks-days N`

Override retention for task folders. Default: `30`.

```bash
# Keep task folders for two weeks only
scripts/cleanup/purge-old-files.sh --tasks-days 14 --apply
```

A task folder is eligible only if its `status.md` reports a terminal state (`DONE` or `WONT-DO`) and the folder mtime is past the threshold. `BLOCKED` folders are preserved unless you explicitly opt in with `--include-blocked`.

### `--logs-days N`

Override retention for log archive files under `projects/<name>/logs/*/archive/` and `$KANBAN_ROOT/logs/`. Default: `7`.

```bash
# Keep log archives for three days
scripts/cleanup/purge-old-files.sh --logs-days 3 --apply
```

Logs default to a much shorter retention than other categories because they accumulate the fastest and have the least long-term value. The 7-day default is preserved even when you set `--days` alone; pass `--logs-days` explicitly to change it.

### `--bundles-days N`

Override retention for shipped requirement bundles under `projects/<name>/requirements/`. Default: `30`.

```bash
# Keep shipped bundles for 60 days
scripts/cleanup/purge-old-files.sh --bundles-days 60 --apply
```

A bundle is eligible only if its filename matches `*-bugfix-bundle-*.md` or `*-priority-bundle-*.md`, the matching release-notes file for the target version exists (i.e. the bundle shipped), and the bundle mtime is past the threshold. A misnamed file or an unshipped bundle is never purged.

### `--bugs-days N`

Override retention for closed bug files under `projects/<name>/bugs/`. Default: `30`.

```bash
# Keep bug history for a quarter
scripts/cleanup/purge-old-files.sh --bugs-days 90 --apply
```

A bug file is eligible only if its `## Status` field is `done` or `wont-do` and the file mtime is past the threshold. Open bugs are never purged.

### `--priorities-days N`

Override retention for closed priority files under `projects/<name>/priority/`. Default: `30`.

```bash
# Keep priority history for a quarter
scripts/cleanup/purge-old-files.sh --priorities-days 90 --apply
```

Same rule as bugs: terminal state plus mtime past threshold.

### `--project NAME`

Limit the purge to a single project. Without this flag, every project under `projects/` is processed. Default: all projects.

```bash
# Only touch one project
scripts/cleanup/purge-old-files.sh --project pgai-video-generator --apply
```

If the named project does not exist, the script exits with an error and changes nothing.

### `--include-blocked`

Also purge task folders whose status is `BLOCKED`. This is an explicit opt-in because `BLOCKED` means a human needs to look at it; deleting `BLOCKED` work silently is almost always wrong.

```bash
# Aggressive cleanup: also purge old BLOCKED tasks (rare)
scripts/cleanup/purge-old-files.sh --include-blocked --apply
```

Use this only when you have already triaged outstanding `BLOCKED` work and decided the old ones are abandonable.

### `--apply`

Actually delete the candidates. Without this flag, every run is a dry-run.

```bash
scripts/cleanup/purge-old-files.sh --apply
```

This is the only way to make the script delete anything. If `--apply` is not on the command line, the script will not touch a file.

### `--archive`

Before deleting, copy every candidate into a single tarball at `$KANBAN_ROOT/archive/purge-YYYYMMDDTHHMMSSZ.tar.gz`. If the tar step fails, the run aborts and nothing is deleted.

```bash
# Archive everything before deleting — cheap insurance
scripts/cleanup/purge-old-files.sh --archive --apply
```

The tarball preserves the relative path from `$KANBAN_ROOT`. To restore an item, extract the tarball into `$KANBAN_ROOT`:

```bash
cd "$KANBAN_ROOT"
tar -xzf archive/purge-20260517T030000Z.tar.gz
```

In dry-run mode, `--archive` causes the planned tarball path to appear in the summary so you know where the file would be written. No tarball is created in dry-run.

### `--verbose`

Show every file considered, including `[SKIP]` entries (too recent, wrong state, active-RC guard). Useful when you want to confirm the script is seeing a file you expected it to purge.

```bash
scripts/cleanup/purge-old-files.sh --verbose
```

### `--quiet`

Suppress per-file output. Only the run banner and final summary are printed. Useful inside cron.

```bash
scripts/cleanup/purge-old-files.sh --quiet --apply
```

`--verbose` and `--quiet` are mutually exclusive.

### `--help` / `-h`

Print the usage text and exit.

## Per-category retention guidance

Defaults are conservative — they err on the side of keeping things. Tighten or relax them based on the disk you have and the history you want to keep.

| Category   | Default | Tighter (low disk)         | Looser (long history)     |
|------------|---------|----------------------------|---------------------------|
| Tasks      | 30 days | `--tasks-days 14`          | `--tasks-days 60`         |
| Logs       | 7 days  | `--logs-days 3`            | `--logs-days 14`          |
| Bundles    | 30 days | `--bundles-days 14`        | `--bundles-days 90`       |
| Bugs       | 30 days | `--bugs-days 14`           | `--bugs-days 90`          |
| Priorities | 30 days | `--priorities-days 14`     | `--priorities-days 90`    |

Notes on tuning:

- **Tasks dominate volume.** They are the largest single source of files and grow linearly with chain throughput. If you only tune one number, tune `--tasks-days`.
- **Logs dominate file count.** Daily-rotated archives accumulate quickly. Seven days is a sensible default; three is usually safe if you do not debug from week-old logs.
- **Bugs and priorities are intake history.** They are tiny on disk but valuable for context. Most operators set these to 90 days even when they tighten everything else.
- **Bundles are stable after ship.** Once a bundle has shipped (its release-notes exists), it is unlikely to be read again. Thirty days is fine; longer if you cross-reference shipped bundles during retros.

A common combined invocation:

```bash
scripts/cleanup/purge-old-files.sh \
    --tasks-days 14 \
    --logs-days 3 \
    --bundles-days 30 \
    --bugs-days 90 \
    --priorities-days 90 \
    --archive --apply
```

That keeps the working set tight, log churn aggressive, and intake history broad — with an archive tarball you can mine if you need to.

## Safety guarantees

The script is built around the assumption that an operator will run it half-asleep at some point, so the destructive paths are gated and the guards are explicit. These are the guarantees you can rely on.

### Dry-run is the default

Running `scripts/cleanup/purge-old-files.sh` with no flags shows you a preview. Nothing is deleted. The word `DRY-RUN` appears in the banner so you cannot miss it. To actually delete files, you must type `--apply` on the command line. There is no environment variable, config file setting, or alias that flips the default — `--apply` is the only way.

This is the single most important safety property. It exists because the cost of an unintended apply is much higher than the cost of running the preview an extra time.

### Active-RC defense

Even when a task folder is old, the script refuses to touch it if the project has an active RC and the task is referenced by that RC's open requirement bundle. This prevents purging the working set during a stalled release cycle.

In practice: if `rc/v0.24.8` is still open and its requirements bundle still mentions `CLAUDE-CODER-20260315-002`, that task folder will be skipped even if it is 90 days old. The script logs the skip so you can see it happened.

You do not configure this guard. It is always on.

### BLOCKED tasks are preserved

A task folder whose `status.md` reports state `BLOCKED` is never purged unless you pass `--include-blocked` explicitly. `BLOCKED` is the kanban's signal that a human needs to look at something; deleting `BLOCKED` work would discard the signal along with the work.

If you want to clean up old `BLOCKED` folders, triage them first (mark as `WONT-DO` if abandoned, or unblock and let the chain consume them), then run the script normally.

### Recent work is preserved

The retention check uses file mtime, not creation time, and the threshold applies to the folder's most recent modification. A task folder that was touched yesterday is not eligible regardless of when it was created. This is the guard that protects in-flight work from a too-aggressive `--tasks-days` value.

### Static files are skipped

The script never traverses `.git/`, `hooks/`, or `workflows/`. It never touches `README.md`, `PROJECT.cfg`, or `release-state.md`. These are framework structure, not state, and they are out of scope for any purge run.

Shipped bundles also get a secondary guard: the script only treats a `*-bugfix-bundle-*.md` or `*-priority-bundle-*.md` file as a shipped bundle if the matching `release-notes/<vX.Y.Z>.md` exists. A misnamed file that happens to match the pattern but has not actually shipped is left alone.

### Archive mode is recoverable insurance

With `--archive`, every candidate is copied into `$KANBAN_ROOT/archive/purge-TIMESTAMP.tar.gz` before any deletion. If `tar` fails, the run aborts before any file is removed. If you realize after the fact that you wanted something, extract the tarball and put it back.

The archive itself is a candidate for future purges only after 90 days, so you have a long window to retrieve.

### Audit log

Every run writes a log to `$KANBAN_ROOT/logs/purge-YYYYMMDDTHHMMSSZ.log`. The log records every file considered and every action taken, with greppable tags (`[WOULD PURGE]`, `[PURGED]`, `[SKIP]`, `[PURGED dir]`). If you ever need to answer "what did the purge do last Sunday?", the log answers it.

To count what shipped:

```bash
grep -c '^\[PURGED\]' "$KANBAN_ROOT/logs/purge-20260517T030000Z.log"
```

## Cron integration

If your disk pressure justifies it, schedule a weekly purge under cron. The recommended pattern is Sunday early-morning UTC, with `--archive` for safety:

```cron
# Weekly purge, Sunday 03:00 UTC, archive before delete
0 3 * * 0 PGAI_AGENT_KANBAN_ROOT_PATH=/home/rocky/pgai_agent_kanban \
    /home/rocky/pgai_agent_kanban/scripts/cleanup/purge-old-files.sh --archive --apply \
    >> /home/rocky/pgai_agent_kanban/logs/cron-purge.log 2>&1
```

A few notes on running under cron:

- **Export `PGAI_AGENT_KANBAN_ROOT_PATH` on the cron line.** Cron starts with a minimal environment. The script refuses to run if this variable is unset.
- **Use the absolute path to the script.** Cron's `PATH` does not include your kanban root.
- **Redirect both stdout and stderr** (`>> ... 2>&1`) so the cron log captures the summary line and any error messages.
- **Append, do not overwrite.** Use `>>` so successive cron runs leave history rather than stomping each other.
- **Pair with `--quiet` if the cron log is noisy** — the per-file lines are redundant once you trust the run, and the summary is always printed regardless.

The cron entry is not added by default. It is operator-initiated because your disk pressure and history-retention preferences are not the framework's call to make.

## Relationship to `scripts/cleanup/cleanup.sh`

The kanban ships two disk-management scripts. They are complementary, not redundant.

| Script | Cadence | Scope | What it does |
|---|---|---|---|
| `scripts/cleanup/cleanup.sh` | Daily (cron) | Logs, tasks (short-window), briefs, framework temp | Rotates per-project debug logs into archive directories; deletes log files past `PGAI_CLEANUP_RETENTION_DAYS`; deletes terminal task folders past `PGAI_CLEANUP_TASK_RETENTION_DAYS`; archives requirements docs whose target version has shipped; purges the framework temp directory. |
| `scripts/cleanup/purge-old-files.sh` | Weekly or monthly (operator-initiated) | Tasks, log archives, shipped bundles, closed bugs, closed priorities — per project, configurable | Removes the broader categories of accumulated state with per-category retention, active-RC defense, BLOCKED preservation, and an optional archive tarball. |

In practice:

- **`cleanup.sh` is housekeeping.** It runs constantly, keeps daily churn from getting out of hand, and rotates log files into archives. You set it up once and forget it.
- **`purge-old-files.sh` is gardening.** It runs less often, with broader scope, and gives you preview-before-commit control over what gets removed.

You want both. `cleanup.sh` keeps the daily firehose tame; `purge-old-files.sh` periodically clears the accumulated archive directories, the shipped bundles, and the closed intake files that `cleanup.sh` does not address.

If you have to pick one, pick `cleanup.sh` — daily log rotation is non-negotiable. But once `cleanup.sh` is running, `purge-old-files.sh` is the tool that keeps your disk usage flat over a quarter rather than climbing.

## Troubleshooting

**The script says "PGAI_AGENT_KANBAN_ROOT_PATH is not set."**
Export the variable to point at your kanban install root before running. Under cron, set it on the same line as the command (see the cron example above).

**The script says "projects directory not found."**
Your `$PGAI_AGENT_KANBAN_ROOT_PATH` is set but the path does not contain a `projects/` directory. Verify the variable points at a real kanban install.

**Dry-run shows zero candidates but I know there are old files.**
Two common causes: (1) the files are inside the retention window — check the threshold value in the banner; (2) the files are protected by active-RC defense or BLOCKED preservation — run with `--verbose` to see `[SKIP]` lines that explain why.

**`--apply` ran but the summary shows zero items.**
Nothing was eligible. This is success, not failure. The run log under `$KANBAN_ROOT/logs/purge-*.log` confirms.

**I ran `--apply` and want something back.**
If you ran with `--archive`, extract from `$KANBAN_ROOT/archive/purge-TIMESTAMP.tar.gz`. If you did not, the file is gone. Going forward, always pair `--apply` with `--archive` when you are uncertain.

**A task folder I expected to be purged is still there.**
Check three things: (1) is the task's `status.md` actually in a terminal state (`DONE` or `WONT-DO`)? (2) is the folder mtime actually past the threshold? (3) is the project's active RC still open and does its bundle reference the task? Any one of these will protect the folder.
