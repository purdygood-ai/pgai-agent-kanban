# Document-Workflow Deliverable Checkpoint (v1.1.1)

**Cites:** [PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md](../../../projects/pgai-agent-kanban/priority/PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md)

## Purpose

Prove that the v1.1.1 workflow-plugin port is behavior-verbatim on the live
install by running a full document-workflow deliverable end-to-end on the
managed document project and comparing the published artifact against a
pre-port v1.0.0 baseline.

The managed document project is **`the-three-bears`** — identified from
`projects/the-three-bears/project.cfg` (`workflow_type = document`). The
release-workflow companion (`pgai-chomp-man`) is out of scope for this
checkpoint and is covered separately in
[release-rc-checkpoint.md](release-rc-checkpoint.md).

Unit-green is explicitly declared insufficient in the requirements. This
checkpoint is the "real gate" for the document-workflow half of the port.

## Scope

- End-to-end document-workflow deliverable on `the-three-bears` after the
  live install has been upgraded to v1.1.1.
- Baseline capture on a v1.0.0 pre-port checkout, and post-port capture on
  the live install after the deliverable publishes.
- Diff of the two captures. Byte-identical passes. Annotated
  non-behavioral diffs also pass, per PRIORITY-0001.
- Verification that the artifact landed under
  `projects/the-three-bears/artifacts/` and that its filename slug matches
  the pre-port shape.
- No engine changes. No plugin manifest edits. No new managed projects.

## Prerequisites

Before you start:

- The live install at `$KANBAN_ROOT` has been upgraded to v1.1.1 and
  `install.sh` completed cleanly.
- You have a scratch clone of the kanban source you can point at a v1.0.0
  tag (used for the pre-port baseline). Suggested path:
  `/tmp/pgai-baseline-v1.0.0/`. You can reuse the same scratch clone the
  release-workflow checkpoint used.
- `the-three-bears` is registered in `$KANBAN_ROOT/projects.cfg` and its
  `project.cfg` reports `workflow_type = document`. Verify:
  ```bash
  grep workflow_type $KANBAN_ROOT/projects/the-three-bears/project.cfg
  ```
- The project's queues are drained or HALTed for the duration of the
  checkpoint so autonomous agents do not race the operator run.
- You have write access to the operator scratch dir. Suggested:
  `$KANBAN_ROOT/projects/the-three-bears/artifacts/checkpoint/v1-1-1/`.
- Source the shell env before every step below:
  ```bash
  source $KANBAN_ROOT/shell-env
  ```

## Procedure

### 1. Capture the pre-port baseline (v1.0.0)

Run the baseline capture against a v1.0.0 checkout of the dev tree. The
capture script is idempotent and read-only.

- Prepare the scratch clone and check out the v1.0.0 tag (skip if you
  already prepared it for the release-workflow checkpoint):
  ```bash
  git clone git@github-prod:purdygood-ai/pgai-agent-kanban.git /tmp/pgai-baseline-v1.0.0
  cd /tmp/pgai-baseline-v1.0.0
  git checkout v1.0.0
  ```
- Point `PGAI_AGENT_KANBAN_ROOT_PATH` at the scratch clone so the script
  reads its projects tree and not the live install:
  ```bash
  PGAI_AGENT_KANBAN_ROOT_PATH=/tmp/pgai-baseline-v1.0.0 \
      team/scripts/capture-checkpoint-baseline.sh \
      --project the-three-bears \
      --out /tmp/pgai-baseline-v1.0.0/out-the-three-bears
  ```
- Record the three output files. Expect: `tags.txt`, `artifacts.txt`,
  `task-graph.txt`. For a document-workflow project, `tags.txt` may be
  empty at v1.0.0 (document projects do not always mint version tags);
  `artifacts.txt` is the load-bearing capture for this checkpoint.
- Inspect `artifacts.txt`. Note the highest-versioned artifact filename
  and its slug shape (for example, `v0.0.5-the-three-bears.md`). You will
  compare the post-port slug against this shape in Step 5.

### 2. Run the post-port document-workflow deliverable

Trigger a real document-workflow deliverable on the live install against
`the-three-bears`.

- Drop a requirements file for the deliverable into the project's intake
  dir. Use a trivial requirements doc (for example, a no-op editorial
  revision that keeps the story content unchanged) so the deliverable
  exercises the pipeline end-to-end without introducing content changes
  you would have to explain away in the diff:
  ```bash
  scripts/intake.sh --project the-three-bears /tmp/v1.1.1-checkpoint-noop.md
  ```
- Let the pipeline pick it up (PM decomposes into document sub-tasks —
  outline, section-draft, integrate, polish; WRITER works the tickets;
  TESTER verifies; CM publishes). Or drive each stage manually with the
  operator commands documented in
  [operator-commands.md](../../operator-commands.md).
- Wait until CM completes the document-workflow publish step so the final
  polished artifact lands under `projects/the-three-bears/artifacts/`.
- Confirm the published artifact exists on the live install:
  ```bash
  ls -1 $KANBAN_ROOT/projects/the-three-bears/artifacts/ | tail -5
  ```

### 3. Capture the post-port outputs

Run the same capture script against the live install after the
deliverable publishes.

```bash
team/scripts/capture-checkpoint-baseline.sh \
    --project the-three-bears \
    --out $KANBAN_ROOT/projects/the-three-bears/artifacts/checkpoint/v1-1-1/post
```

The default `PGAI_AGENT_KANBAN_ROOT_PATH` is `$KANBAN_ROOT`, so no override
is needed. Expect the same three output files as Step 1.

### 4. Verify the artifact landed and the slug matches

Before diffing, confirm the delivery shape matches pre-port conventions.
A document-workflow deliverable that lands anywhere other than
`projects/the-three-bears/artifacts/`, or that lands with a mangled
filename, is a behavioral regression regardless of what the diff says.

- Confirm the new artifact is under the project's artifacts directory:
  ```bash
  test -d $KANBAN_ROOT/projects/the-three-bears/artifacts/ \
      && echo "artifacts dir present" \
      || echo "MISSING artifacts dir — FAIL"
  ```
- Identify the newest published artifact (the one this deliverable
  produced) and confirm its filename matches the pre-port slug shape
  observed in Step 1 (`v<version>-<slug>.md`, no extra suffixes, no
  renamed slug root):
  ```bash
  NEWEST=$(ls -1t $KANBAN_ROOT/projects/the-three-bears/artifacts/*.md | head -1)
  basename "$NEWEST"
  ```
- Compare the newest filename against the highest-versioned entry you
  recorded from the baseline `artifacts.txt` in Step 1. The version
  component may bump (that is expected for the checkpoint deliverable
  itself); the slug root must not change. A slug drift (for example,
  `v0.0.6-three-bears.md` where pre-port used `v0.0.6-the-three-bears.md`)
  is a **behavioral** regression and fails this step.

If either check fails, stop here. Record the failure in the outcome note
per Step 6 and skip the diff — a missing or mis-shaped artifact is
already a **FAIL-BEHAVIORAL-DRIFT** outcome.

### 5. Diff pre-port against post-port

Diff each output file. Redirect the diff output to a file so you can
attach it to the outcome note.

```bash
DIFF_DIR=$KANBAN_ROOT/projects/the-three-bears/artifacts/checkpoint/v1-1-1/diff
mkdir -p "$DIFF_DIR"

diff -u /tmp/pgai-baseline-v1.0.0/out-the-three-bears/tags.txt        \
        $KANBAN_ROOT/projects/the-three-bears/artifacts/checkpoint/v1-1-1/post/tags.txt        \
        > "$DIFF_DIR/tags.diff"        || true
diff -u /tmp/pgai-baseline-v1.0.0/out-the-three-bears/artifacts.txt   \
        $KANBAN_ROOT/projects/the-three-bears/artifacts/checkpoint/v1-1-1/post/artifacts.txt   \
        > "$DIFF_DIR/artifacts.diff"   || true
diff -u /tmp/pgai-baseline-v1.0.0/out-the-three-bears/task-graph.txt  \
        $KANBAN_ROOT/projects/the-three-bears/artifacts/checkpoint/v1-1-1/post/task-graph.txt  \
        > "$DIFF_DIR/task-graph.diff"  || true
```

Record which diffs are empty and which are not. Any non-empty diff must
be inspected line-by-line and annotated in the outcome note.

### 6. Classify each diff and apply the rubric

Walk each non-empty diff. For every added line, deleted line, or hunk,
tag it as either **behavioral** or **non-behavioral**:

- **Behavioral** — the port changed what the document pipeline produces,
  publishes, or names in a way an operator or downstream automation would
  observe. Examples: an artifact appearing under a new path; a filename
  slug root changing; a task role emitted at a different stage; the
  document sub-task sequence (outline → section-draft → integrate →
  polish) reordering; the published artifact missing from
  `artifacts.txt`.
- **Non-behavioral** — the change is expected, explainable, and does not
  alter the shipped outcome. Examples: an additional entry in
  `artifacts.txt` because the checkpoint deliverable itself minted a new
  version (`v0.0.6-the-three-bears.md` did not exist at v1.0.0 by
  definition); new task-IDs that correspond to the checkpoint
  deliverable's own sub-task tickets; an additional tag entry if the
  document project began minting tags after v1.0.0.

Write each annotation next to the hunk in the diff file (comment lines
or a sidecar `.notes` file). The annotation must state the reason
non-behavioral in one sentence.

### 7. Record the outcome

Append a section to
[`outcome-notes.md`](outcome-notes.md) in this checkpoint directory
naming one of the three outcomes below. Reference the diff files and the
Step 4 slug check by path.

## Pass/Fail Rubric

| Outcome | Meaning | Action |
|---|---|---|
| **PASS-BYTE-IDENTICAL** | All three diffs are empty, the artifact landed under `projects/the-three-bears/artifacts/`, and its slug shape matches pre-port. Post-port capture matches the pre-port baseline byte for byte, ignoring the trivially-different checkpoint deliverable tag/task IDs (see note below). | Record PASS in `outcome-notes.md`, cite this checkpoint doc, close the item. |
| **PASS-ANNOTATED-NON-BEHAVIORAL** | The artifact landed correctly and the slug shape matches pre-port. One or more diffs are non-empty, but every hunk has been annotated as non-behavioral with an explanation. No hunk is behavioral. | Record PASS with annotated diffs attached in `outcome-notes.md`. This outcome is explicitly a pass per PRIORITY-0001. |
| **FAIL-BEHAVIORAL-DRIFT** | The artifact failed the Step 4 landing/slug check, or at least one hunk is behavioral. The port changed observable outcomes. | Record FAIL in `outcome-notes.md` with the offending hunks or slug mismatch called out. File a bug against v1.1.1 (route through `intake.sh --project pgai-agent-kanban /tmp/BUG-<n>-...`) and HALT the live install pending fix. |

Both **PASS-BYTE-IDENTICAL** and **PASS-ANNOTATED-NON-BEHAVIORAL** satisfy
the PRIORITY-0001 acceptance criterion — the document-workflow plugin
port is behavior-verbatim when either holds.

**Note on the "trivially-different" allowance.** The checkpoint
deliverable itself publishes a new artifact and mints new task IDs by
design. Their absence from the v1.0.0 baseline is expected. Recognizing
them and annotating them is what PASS-ANNOTATED-NON-BEHAVIORAL captures;
there is no PASS-BYTE-IDENTICAL without them because the baseline
predates the deliverable.

## References

- Requirements: [v1.1.1-priority-bundle-20260704.md](../../../projects/pgai-agent-kanban/requirements/v1.1.1-priority-bundle-20260704.md)
- Priority: [PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md](../../../projects/pgai-agent-kanban/priority/PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md)
- Baseline capture: `team/scripts/capture-checkpoint-baseline.sh`
- Companion checkpoints: [index.md](index.md), [release-rc-checkpoint.md](release-rc-checkpoint.md), [custom-workflow-walkthrough.md](custom-workflow-walkthrough.md)
- Outcome ledger: [outcome-notes.md](outcome-notes.md)
