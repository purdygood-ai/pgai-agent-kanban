# Three Bears walkthrough — one document version, end to end

The [demo README](README.md) shows you how to deposit the five story
revisions. This page follows **one** revision through every stage of the
document workflow and gives you a check for each one. Use it the first time you
want to see exactly how a requirement becomes a published, versioned file.

Each stage below says what to run and what you should see. The commands are
read-only, apart from the deposits and the optional manual wake.

The document workflow runs these steps for every requirement, in order:

| Step            | Agent  | What it produces                                             |
|-----------------|--------|--------------------------------------------------------------|
| `open-doc`      | CM     | opens the version for writing                                |
| `outline`       | WRITER | `outline.md`                                                 |
| `draft`         | WRITER | `draft.md`: the whole piece (short-form path)                |
| `section-draft` | WRITER | one `section-<name>.md` per outline section (long-form only) |
| `integrate`     | WRITER | `integrated.md` from the sections (long-form only)           |
| `polish`        | WRITER | `polished.md`, the final text                                |
| `review`        | TESTER | a review report (only when the project declares a reviewer)  |
| `finalize`      | CM     | the published file under `projects/pgai-three-bears/artifacts/` |

A bedtime story is short, so expect the short-form path: `outline`, `draft`,
`polish`. The long-form steps appear when an outline splits the work into
sections.

There is no git in this workflow: no dev tree, no branch, no tag. The
deliverable is the file CM publishes.

---

## Before you start

- You have completed the [Three Bears demo setup](README.md#setup-once), so
  `pgai-three-bears` is registered as a `document` project.
- No earlier revision is still in progress. The chain works one version at a
  time per project.
- The project is not HALTed.

Set up your shell once:

```bash
source "$PGAI_AGENT_KANBAN_ROOT_PATH/shell-env"
cd "$PGAI_AGENT_KANBAN_ROOT_PATH"
ARTS="$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-three-bears/artifacts"
grep '^workflow_type' "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-three-bears/project.cfg"
```

You should see `workflow_type = document`.

---

## Stage 1 — Deposit the first version

```bash
scripts/intake.sh --project pgai-three-bears \
  --file demos/three-bears-demo/intake/v0.0.1-three-bears-story.md
```

`intake.sh` prints the deposited path, under
`projects/pgai-three-bears/requirements/`.

Two fields in that file decide what gets published. `## Target Version`
holds the version, `v0.0.1`. `## Artifact Name` holds the published file's
name, `three-bears-bedtime-story`. All five demo revisions use the same
artifact name, so every version lands beside the last under the same name.

## Stage 2 — PM decomposes it

On the next PM wake, discovery picks up the requirement and PM breaks it into
the steps above. To skip waiting for the schedule, run the wake yourself:

```bash
scripts/wake-batch.sh --agent=pm --max-tasks=1
```

Then check the requirement and the project's tasks:

```bash
scripts/show.sh --project pgai-three-bears --key v0.0.1
ls "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-three-bears/tasks/"
```

What you should see: the requirement's `## Status` reads `running`. New task
folders appear for CM and WRITER, named `<ROLE>-<date>-<seq>-<slug>`. There is
no CODER task; a document needs none.

## Stage 3 — WRITER writes

Each WRITER task leaves its deliverable in its own task folder's `artifacts/`
directory. You can watch the story take shape there:

```bash
ls "$PGAI_AGENT_KANBAN_ROOT_PATH"/projects/pgai-three-bears/tasks/WRITER-*/artifacts/
```

What you should see, as the tasks finish: `outline.md`, then `draft.md`, then
`polished.md`. On the dashboard's logs window (window 6), each WRITER wake
moves its task to `DONE`.

## Stage 4 — CM finalizes and publishes

`finalize` takes the polish task's `polished.md`, publishes it, and marks the
requirement done.

```bash
ls -1 "$ARTS"
scripts/show.sh --project pgai-three-bears --key v0.0.1
```

What you should see:

- `v0.0.1-three-bears-bedtime-story.md` in the artifacts directory. The name
  is always `v<version>-<artifact name>.md`.
- The requirement's `## Status` reads `done`.

Read what was published:

```bash
cat "$ARTS/v0.0.1-three-bears-bedtime-story.md"
```

## Stage 5 — Publish the next version beside it

Published versions are never overwritten. A later version is a new file, and
earlier files stay byte-for-byte as they were. You can see this for yourself.
Record a checksum of v0.0.1, then deposit the fox revision:

```bash
sha256sum "$ARTS/v0.0.1-three-bears-bedtime-story.md" > ~/three-bears-v0.0.1.sha256
scripts/intake.sh --project pgai-three-bears \
  --file demos/three-bears-demo/intake/v0.0.2-three-bears-friendly-fox-at-the-stream.md
```

The v0.0.2 requirement tells WRITER to start from the v0.0.1 text and add a
scene, rather than write from scratch. Let it run through the same stages.
When its requirement reads `done`, check the directory and the checksum:

```bash
ls -1 "$ARTS"
sha256sum -c ~/three-bears-v0.0.1.sha256
```

What you should see: `v0.0.1-three-bears-bedtime-story.md` and
`v0.0.2-three-bears-bedtime-story.md` side by side, and the check reporting
`OK` for v0.0.1. The name stays the same; only the version changes.

If CM ever finds a file already at the path it would publish to, it does not
overwrite it. It logs the collision and leaves the existing file in place.

---

## If a stage stalls

- **Nothing happens after the deposit.** Check for a HALT on the project or
  the whole kanban, and that no earlier version is still in progress.
- **A task shows BLOCKED.** Read its reason with `scripts/show.sh --project
  pgai-three-bears --key` followed by the task ID from the dashboard. The
  block record names the obstacle and what clears it.
- **A version went wrong before it published.** HALT the project, wait for
  any task that is WORKING to finish, reset the version's requirement, then
  resume. PM decomposes it afresh on the next wake:

  ```bash
  scripts/halt.sh --project pgai-three-bears
  scripts/reset.sh --project pgai-three-bears --key v0.0.2
  scripts/unhalt.sh --project pgai-three-bears
  ```

  The scripts do exactly what you tell them, so the wait is yours to keep.
- **You want to change text that has already published.** Deposit a new
  version that says what to change. A rerun of a published version cannot
  replace its file, because the publish step never overwrites.

---

## What you just learned

- One requirement becomes one published version: `open-doc` → WRITER steps
  → `finalize`.
- Each WRITER step leaves a named deliverable in its task folder, so you can
  read the document at every stage.
- The published name is `v<version>-<artifact name>.md`, taken from the
  requirement's `## Target Version` and `## Artifact Name`.
- Published versions accumulate and are never overwritten. The artifacts
  directory is the document's full history.
