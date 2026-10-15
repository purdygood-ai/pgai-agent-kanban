# Quarantine and Recovery

Operator guide to quarantined intake. If discovery keeps rejecting a priority or bug file's name, it moves the file out of the intake directory into the project's `rejected/` directory, `projects/<name>/rejected/`. This guide covers how to spot that, how to list what is quarantined, and how to get a file back.

## What quarantine is

On every tick, discovery checks the name of each `.md` file in a project's `priority/` and `bugs/` directories. It counts how many times each name has been rejected. On the third rejection it moves the file to `projects/<name>/rejected/<filename>`. The threshold is `PGAI_DISCOVERY_REJECT_THRESHOLD` and defaults to 3. Quarantine keeps discovery from rejecting the same broken file on every tick forever.

Nothing is deleted. The file sits in `rejected/` with a `<filename>.reason` sidecar beside it until you recover it.

### The `.reason` sidecar

The sidecar is a short `key=value` file that records where the quarantined file came from and why it was moved. Recovery reads it to decide which intake directory the file goes back to.

| Key | Holds |
|---|---|
| `original_type` | The intake directory the file came from: `priority` or `bugs` |
| `original_dir` | The absolute path of that directory |
| `reason` | Discovery's rejection text, e.g. `'2' is under-padded; did you mean PRIORITY-0002? Next free number is PRIORITY-0001.` |
| `rejected_at` | The UTC time of the quarantine, e.g. `2026-05-17T12:31:04Z` |
| `retry_count` | `0` on the first quarantine; one higher each time the same name is quarantined again |

Recovery moves the file and leaves the sidecar where it is. If the same name is quarantined again, discovery reads the old `retry_count` and adds one. The listing ignores sidecars, so a leftover sidecar never shows up as a quarantined file.

### The legacy `.rejected/` location

A file can also be quarantined into a hidden `.rejected/` directory inside the intake directory it came from: `priority/.rejected/`, `bugs/.rejected/` or `requirements/.rejected/`. This is the older location, and the fallback discovery uses only when it is not given a project name. The discovery pipeline always passes a project name, so new quarantines do not land there. Only files quarantined before the project-level directory existed, or written by hand, are found there.

Both recovery tools still read the legacy location. They search `projects/<name>/rejected/` first and the three `.rejected/` directories after it. A file found in `<dir>/.rejected/` goes back to `<dir>/`.

## How you'll notice

The dashboard's `attention` window shows quarantine state in two sections.

- **QUARANTINED FILES.** This section has one row per file in each project's `rejected/` directory. Each row shows the project, the intake type, the filename, the sidecar's reason (cut to 60 characters) and the retry count. The section is hidden when nothing is quarantined.
- **QUARANTINE ALERTS → warning.** A file that has been rejected but has not yet reached the threshold shows here with its count, for example `2/3`, and the reason. Fix the file in place before the next discovery tick and it never gets quarantined. If no file is close to the threshold and none is quarantined, the section reads `(no quarantine alerts)`.

QUARANTINE ALERTS can also show a red **terminal** entry for files already quarantined. It lists the files in `projects/<name>/rejected/` and any in the legacy `.rejected/` directories, grouped by the intake directory each came from (`rejected` when that cannot be told). Each group ends with a `Recover:` hint in the flag form `recover-rejected.sh` accepts, `scripts/recover-rejected.sh --project <name> --file <filename> [--rename NEW]`, with the project filled in. [Recovering a file](#recovering-a-file) walks through it.

`kanban-status.sh` also prints a `Quarantined:` count, the number of files in the project's `rejected/` directory.

Discovery logs each quarantine. The entry gives the reason, a rename hint and the `Quarantine path:` the file was moved to.

## Common causes

Every quarantine comes from a filename the discovery pipeline rejected.

- **Wrong number or slug format.** Names must match `PRIORITY-NNNN-<slug>.md` or `BUG-NNNN-<slug>.md`. The number is exactly four digits. An optional eight-digit date may follow it immediately, as in `PRIORITY-NNNN-YYYYMMDD-<slug>.md`. The slug is lowercase letters, digits and hyphens. The usual mistakes are an under-padded number (`PRIORITY-2-…`), a date before the number (`PRIORITY-YYYYMMDD-NNN-…`) and capital letters in the slug.
- **Wrong prefix.** Every `.md` file in `priority/` must start with `PRIORITY-`, and every one in `bugs/` with `BUG-`. `README.md` is the only exception. Files that do not end in `.md` are ignored, not rejected.

The reason discovery recorded is in the sidecar, in the QUARANTINED FILES row and in the discovery log. The listing below shows the pattern the name failed.

## Recovering a file

Recovery takes three steps. Both commands live in the installed kanban's `scripts/` directory.

1. **List what is quarantined.**

   ```bash
   $KANBAN_ROOT/scripts/list-rejected.sh --project <name>
   ```

   Without `--project` the command lists every project. Files are grouped under a `project=<name> dir=<intake>` heading by the intake directory they came from (`priority`, `bugs` or `requirements`). That directory comes from the sidecar, or else from the `PRIORITY-`/`BUG-` prefix. Each file shows its name, its quarantine time and the reason its name fails the pattern. If the command finds nothing, it prints `(no quarantined files found across all projects)`.

2. **Work out the fix.** The reason line, or the sidecar's `reason`, tells you what to change. It is almost always the filename.

3. **Recover the file under a corrected name.**

   ```bash
   $KANBAN_ROOT/scripts/recover-rejected.sh --project <name> --file <filename> --rename <NEW>
   ```

   `--file` takes the base name exactly as the listing prints it. The script finds the file in `projects/<name>/rejected/`, or in a legacy `.rejected/` directory, and moves it back to the intake directory it came from under the `--rename` name. It also deletes the file's line from `.discovery-state/rejected-counts.tsv`, so discovery starts counting from zero. `--project` can be left out when `PGAI_PROJECT_NAME` is set.

`--rename` is optional, but you almost always want it. Without it the file comes back under its original, broken name, and discovery starts counting it again on the next tick. If a file already exists at the destination, the script refuses and moves nothing. Choose another `--rename` target.

### When the origin cannot be told

A file in `rejected/` with no usable sidecar and no `PRIORITY-` or `BUG-` prefix gives recovery nowhere to send it. The listing puts it under `dir=rejected`. `recover-rejected.sh` refuses with `recover_rejected.origin_unknown` and moves nothing. The `remedy:` line it prints adds an `original_type=bugs` line to the sidecar and runs the recovery again. Change `bugs` to `priority` or `requirements` first if that is where the file belongs, then run it.

## Worked example

A representative case: `PRIORITY-2-sound-effects.md`, dropped into `projects/pgai-chomp-man/priority/` with a one-digit ID where four digits are required.

After three discovery ticks the file is moved to `projects/pgai-chomp-man/rejected/`, with its `.reason` sidecar beside it. The operator sees the file in the QUARANTINED FILES section of the attention window and runs:

```bash
$KANBAN_ROOT/scripts/list-rejected.sh --project pgai-chomp-man
```

Output:

```
project=pgai-chomp-man dir=priority
  PRIORITY-2-sound-effects.md  (quarantined 2026-05-17T12:31)
  Reason: filename does not match expected pattern (^PRIORITY-[0-9]{4}(?:-[0-9]{8})?-[a-z0-9][a-z0-9-]*\.md$)

To recover: scripts/recover-rejected.sh --project <name> --file <filename> [--rename NEW]
```

The file is listed under `dir=priority` because its sidecar records `original_type=priority`. The reason shows the number needs four digits. The operator recovers it under a corrected name:

```bash
$KANBAN_ROOT/scripts/recover-rejected.sh --project pgai-chomp-man \
    --file PRIORITY-2-sound-effects.md \
    --rename PRIORITY-0002-sound-effects.md
```

Output:

```
recover-rejected.sh: restored 'PRIORITY-2-sound-effects.md' -> 'PRIORITY-0002-sound-effects.md' in /.../projects/pgai-chomp-man/priority
recover-rejected.sh: cleared counter entry for 'PRIORITY-2-sound-effects.md' in /.../projects/pgai-chomp-man/.discovery-state/rejected-counts.tsv
recover-rejected.sh: note: 'PRIORITY-0002-sound-effects.md' will get a fresh counter on first discovery parse
```

The file is back in `priority/` under its corrected name, and `rejected/` now holds only the old sidecar. The next discovery tick parses it cleanly and the item moves on through the pipeline.

## Filename patterns

For reference, the two patterns discovery enforces:

| Type | Pattern | Examples (both valid) |
|---|---|---|
| Priority | `^PRIORITY-[0-9]{4}(?:-[0-9]{8})?-[a-z0-9][a-z0-9-]*\.md$` | e.g. `PRIORITY-0002-sound-effects.md`, `PRIORITY-0002-20260517-sound-effects.md` |
| Bug | `^BUG-[0-9]{4}(?:-[0-9]{8})?-[a-z0-9][a-z0-9-]*\.md$` | e.g. `BUG-0001-movement-requires-held-key.md`, `BUG-0001-20260517-movement-requires-held-key.md` |

Both types need a four-digit ID and a lowercase slug. The date segment is optional for both, and when present it comes straight after the ID.

## See also

- `$KANBAN_ROOT/scripts/list-rejected.sh` — list every quarantined file
- `$KANBAN_ROOT/scripts/recover-rejected.sh` — put a quarantined file back
