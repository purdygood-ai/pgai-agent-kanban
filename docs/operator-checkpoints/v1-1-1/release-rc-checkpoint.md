# Release-Workflow RC Checkpoint (v1.1.1)

**Cites:** [PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md](../../../projects/pgai-agent-kanban/priority/PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md)

## Purpose

Prove that the v1.1.1 workflow-plugin port is behavior-verbatim on the live
install by running a full release-workflow RC end-to-end on the managed release
project and comparing the shipped outputs against a pre-port v1.0.0 baseline.

The managed release project is **`pgai-chomp-man`** — identified from
`projects/pgai-chomp-man/project.cfg` (`workflow_type = release`). The
document-workflow companion (`the-three-bears`) is out of scope for this
checkpoint and is covered separately in
[document-workflow-checkpoint.md](document-workflow-checkpoint.md).

Unit-green is explicitly declared insufficient in the requirements. This
checkpoint is the "real gate."

## Scope

- End-to-end release-workflow RC on `pgai-chomp-man` after the live install
  has been upgraded to v1.1.1.
- Baseline capture on a v1.0.0 pre-port checkout, and post-port capture on
  the live install after the RC ships.
- Diff of the two captures. Byte-identical passes. Annotated
  non-behavioral diffs also pass, per PRIORITY-0001.
- No engine changes. No plugin manifest edits. No new managed projects.

## Prerequisites

Before you start:

- The live install at `$KANBAN_ROOT` has been upgraded to v1.1.1 and
  `install.sh` completed cleanly.
- You have a scratch clone of the kanban source you can point at a v1.0.0
  tag (used for the pre-port baseline). Suggested path: `/tmp/pgai-baseline-v1.0.0/`.
- `pgai-chomp-man` is registered in `$KANBAN_ROOT/projects.cfg` and its
  `project.cfg` reports `workflow_type = release`. Verify:
  ```bash
  grep workflow_type $KANBAN_ROOT/projects/pgai-chomp-man/project.cfg
  ```
- The project's queues are drained or HALTed for the duration of the
  checkpoint so autonomous agents do not race the operator run.
- You have write access to the operator scratch dir. Suggested:
  `$KANBAN_ROOT/projects/pgai-chomp-man/artifacts/checkpoint/v1-1-1/`.
- Source the shell env before every step below:
  ```bash
  source $KANBAN_ROOT/shell-env
  ```

## Procedure

### 1. Capture the pre-port baseline (v1.0.0)

Run the baseline capture against a v1.0.0 checkout of the dev tree. The
capture script is idempotent and read-only.

- Prepare the scratch clone and check out the v1.0.0 tag:
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
      --project pgai-chomp-man \
      --out /tmp/pgai-baseline-v1.0.0/out
  ```
- Record the three output files. Expect: `tags.txt`, `artifacts.txt`,
  `task-graph.txt`.
- If `tags.txt` is empty, the pre-port tag prefix may be the legacy
  `chomp-man/*`. Re-run against a scratch clone with `--project chomp-man`
  and note the rename in your outcome record.

### 2. Open and run the post-port RC

Trigger a real release-workflow RC on the live install against
`pgai-chomp-man`.

- Drop a requirements file for the RC into the project's intake dir. Use a
  trivial requirements doc (e.g., a no-op version bump) so the RC exercises
  the pipeline end-to-end without introducing behavioral changes you'd have
  to explain away in the diff:
  ```bash
  scripts/intake.sh --project pgai-chomp-man /tmp/v1.0.1-checkpoint-noop.md
  ```
- Let the pipeline pick it up (PM decomposes, CODER/WRITER/TESTER work the
  tickets, CM opens and ships the RC). Or drive each stage manually with
  the operator commands documented in
  [operator-commands.md](../../operator-commands.md).
- Wait until CM completes `cm-release.sh` (tag pushed to
  `origin/main`, RC branch deleted, release-notes committed).
- Confirm the shipped tag exists on origin:
  ```bash
  git -C $KANBAN_ROOT/projects/pgai-chomp-man/dev-tree \
      ls-remote --tags origin | grep pgai-chomp-man/v1.0.1
  ```

### 3. Capture the post-port outputs

Run the same capture script against the live install after the RC ships.

```bash
team/scripts/capture-checkpoint-baseline.sh \
    --project pgai-chomp-man \
    --out $KANBAN_ROOT/projects/pgai-chomp-man/artifacts/checkpoint/v1-1-1/post
```

The default `PGAI_AGENT_KANBAN_ROOT_PATH` is `$KANBAN_ROOT`, so no override
is needed. Expect the same three output files as Step 1.

### 4. Diff pre-port against post-port

Diff each output file. Redirect the diff output to a file so you can attach
it to the outcome note.

```bash
DIFF_DIR=$KANBAN_ROOT/projects/pgai-chomp-man/artifacts/checkpoint/v1-1-1/diff
mkdir -p "$DIFF_DIR"

diff -u /tmp/pgai-baseline-v1.0.0/out/tags.txt        \
        $KANBAN_ROOT/projects/pgai-chomp-man/artifacts/checkpoint/v1-1-1/post/tags.txt        \
        > "$DIFF_DIR/tags.diff"        || true
diff -u /tmp/pgai-baseline-v1.0.0/out/artifacts.txt   \
        $KANBAN_ROOT/projects/pgai-chomp-man/artifacts/checkpoint/v1-1-1/post/artifacts.txt   \
        > "$DIFF_DIR/artifacts.diff"   || true
diff -u /tmp/pgai-baseline-v1.0.0/out/task-graph.txt  \
        $KANBAN_ROOT/projects/pgai-chomp-man/artifacts/checkpoint/v1-1-1/post/task-graph.txt  \
        > "$DIFF_DIR/task-graph.diff"  || true
```

Record which diffs are empty and which are not. Any non-empty diff must be
inspected line-by-line and annotated in the outcome note.

### 5. Classify each diff and apply the rubric

Walk each non-empty diff. For every added line, deleted line, or hunk, tag
it as either **behavioral** or **non-behavioral**:

- **Behavioral** — the port changed what the release pipeline produces,
  ships, or names in a way an operator or downstream automation would
  observe. Examples: an artifact appearing under a new path; a task role
  emitted at a different stage; a tag naming a different SHA than the
  release commit; release-notes structure changing.
- **Non-behavioral** — the change is expected, explainable, and does not
  alter the shipped outcome. Examples: additional tag entries because the
  RC itself minted a new tag between baseline and post-port capture
  (`pgai-chomp-man/v1.0.1` did not exist at v1.0.0 by definition); new
  task-IDs that correspond to the checkpoint RC itself; artifact paths that
  the checkpoint RC's requirements deliberately introduced.

Write each annotation next to the hunk in the diff file (comment lines or
a sidecar `.notes` file). The annotation must state the reason
non-behavioral in one sentence.

### 6. Record the outcome

Append a section to
[`outcome-notes.md`](outcome-notes.md) in this checkpoint directory naming
one of the three outcomes below. Reference the diff files by path.

## Pass/Fail Rubric

| Outcome | Meaning | Action |
|---|---|---|
| **PASS-BYTE-IDENTICAL** | All three diffs are empty. Post-port capture matches the pre-port baseline byte for byte, ignoring the trivially-different RC-under-test tag/task IDs (see note below). | Record PASS in `outcome-notes.md`, cite this checkpoint doc, close the item. |
| **PASS-ANNOTATED-NON-BEHAVIORAL** | One or more diffs are non-empty, but every hunk has been annotated as non-behavioral with an explanation. No hunk is behavioral. | Record PASS with annotated diffs attached in `outcome-notes.md`. This outcome is explicitly a pass per PRIORITY-0001. |
| **FAIL-BEHAVIORAL-DRIFT** | At least one hunk is behavioral. The port changed observable outcomes. | Record FAIL in `outcome-notes.md` with the offending hunks called out. File a bug against v1.1.1 (route through `intake.sh --project pgai-agent-kanban /tmp/BUG-<n>-...`) and HALT the live install pending fix. |

Both **PASS-BYTE-IDENTICAL** and **PASS-ANNOTATED-NON-BEHAVIORAL** satisfy
the PRIORITY-0001 acceptance criterion — the plugin port is
behavior-verbatim when either holds.

**Note on the "trivially-different" allowance.** The checkpoint RC itself
mints a new tag and new task IDs by design. Their absence from the v1.0.0
baseline is expected. Recognizing them and annotating them is what
PASS-ANNOTATED-NON-BEHAVIORAL captures; there is no PASS-BYTE-IDENTICAL
without them because the baseline predates the RC.

## References

- Requirements: [v1.1.1-priority-bundle-20260704.md](../../../projects/pgai-agent-kanban/requirements/v1.1.1-priority-bundle-20260704.md)
- Priority: [PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md](../../../projects/pgai-agent-kanban/priority/PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md)
- Baseline capture: `team/scripts/capture-checkpoint-baseline.sh`
- Companion checkpoints: [index.md](index.md), [document-workflow-checkpoint.md](document-workflow-checkpoint.md), [custom-workflow-walkthrough.md](custom-workflow-walkthrough.md)
- Outcome ledger: [outcome-notes.md](outcome-notes.md)
