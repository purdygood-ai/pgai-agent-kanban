# Quarantine and Recovery

Operator guide for understanding why dropped priority and bug files sometimes disappear into a `.rejected/` subdirectory, how the dashboard surfaces them, and how to get them back.

## What quarantine is

The discovery pipeline retries malformed priority and bug files a few times, and after the third failed parse it moves the file into a `.rejected/` subdirectory under the project — for example, `projects/<name>/priority/.rejected/`. This stops the framework from looping forever on a broken file. The file is not deleted; it sits in `.rejected/` waiting for you to recover it.

## How you'll notice

The dashboard's attention window (`2:attention`) shows quarantine state at a glance. Watch for two indicators:

- **Warning** — a file has been rejected one or more times but has not yet crossed the threshold (for example `2/3`). The reason is shown. Fix the file in place before the next discovery tick to avoid quarantine.
- **Terminal** — a file has been moved to `.rejected/`. The dashboard names the project, lists the filenames, and prints the recovery command.

If neither indicator is showing, no project has files in quarantine.

## Common causes

Almost every quarantine in practice comes from a filename pattern mismatch.

- **Wrong slug format in a PRIORITY or BUG filename.** Files must match `PRIORITY-NNNN-<slug>.md` or `BUG-NNNN-<slug>.md`. The date segment is optional (accepted but not required) for both types — `PRIORITY-0004-pvg-pip-shape-masking.md` and `PRIORITY-0004-20260517-pvg-pip-shape-masking.md` are both valid.
- **Wrong prefix or extension.** Files dropped into `priority/` or `bugs/` must start with `PRIORITY-` or `BUG-` respectively and end with `.md`.
- **Malformed body** (rarer). If the file passes the filename pattern but the parser repeatedly fails on its contents, it still gets quarantined after three attempts. Inspect the file by hand in that case.

The dashboard reports the reason alongside the warning or terminal entry, and the quarantine log line includes it too.

## Recovering a file

Recovery is a three-step operator workflow.

1. **List what is quarantined.** Run `$KANBAN_ROOT/scripts/list-rejected.sh`. The output groups files by `project=<name> dir=priority` (or `dir=bugs`) and shows the filename, the quarantine timestamp, and the rejection reason.
2. **Identify the file and the fix.** Pick a target from the list. The reason line tells you what to change — usually a filename correction.
3. **Recover with the corrected name.** Run `$KANBAN_ROOT/scripts/recover-rejected.sh <project> <filename> --rename <NEW>`. The script moves the file out of `.rejected/`, renames it, and clears the rejection counter so discovery starts fresh on the next tick.

The `--rename` flag is optional but you almost always want it. If you omit it, the file is restored under its original (broken) name and will be re-quarantined within minutes.

## Worked example

A representative case: `priority-0004-pvg-pip-shape-masking.md`, dropped into `projects/pgai-video-generator/priority/` with a lowercase prefix.

After three discovery ticks (about 15 minutes), the file moves to `.rejected/`. The operator notices the terminal entry in the attention window and runs:

```bash
$KANBAN_ROOT/scripts/list-rejected.sh
```

Output:

```
project=pgai-video-generator dir=priority
  priority-0004-pvg-pip-shape-masking.md  (quarantined 2026-05-17T12:31)
  Reason: filename does not match expected pattern (^PRIORITY-[0-9]{4,}-.+\.md$)

To recover: $KANBAN_ROOT/scripts/recover-rejected.sh <project> <filename> [--rename NEW]
```

The reason names the mismatch: lowercase prefix. The operator recovers with a corrected name:

```bash
$KANBAN_ROOT/scripts/recover-rejected.sh pgai-video-generator \
    priority-0004-pvg-pip-shape-masking.md \
    --rename PRIORITY-0004-pvg-pip-shape-masking.md
```

Output:

```
recover-rejected.sh: restored 'priority-0004-pvg-pip-shape-masking.md' -> 'PRIORITY-0004-pvg-pip-shape-masking.md' in /.../projects/pgai-video-generator/priority
recover-rejected.sh: cleared counter entry for 'priority-0004-pvg-pip-shape-masking.md'
recover-rejected.sh: note: 'PRIORITY-0004-pvg-pip-shape-masking.md' will get a fresh counter on first discovery parse
```

The file is now sitting in `priority/` under its corrected name. Within five minutes the next discovery cron tick picks it up, parses it cleanly, and shipping resumes.

## Filename patterns

For reference, the two patterns discovery enforces:

| Type | Pattern | Dateless example | Dated example (also valid) |
|---|---|---|---|
| Priority | `^PRIORITY-[0-9]{4,}-.+\.md$` | `PRIORITY-0004-pvg-pip-shape-masking.md` | `PRIORITY-0004-20260517-pvg-pip-shape-masking.md` |
| Bug | `^BUG-[0-9]{4,}-.+\.md$` | `BUG-0093-rejected-files-silent-quarantine.md` | `BUG-0093-20260517-rejected-files-silent-quarantine.md` |

Both PRIORITY and BUG filenames require an ID and a slug. A date segment is accepted but not required for either type — the two patterns are siblings that use the same date-optional convention.

## See also

- `$KANBAN_ROOT/scripts/list-rejected.sh` — inventory all quarantined files
- `$KANBAN_ROOT/scripts/recover-rejected.sh` — restore a quarantined file
