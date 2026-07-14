# Custom-Workflow Walkthrough Checkpoint (v1.1.1)

**Cites:**
- [PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md](../../../projects/pgai-agent-kanban/priority/PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md)
- [docs/creating-a-workflow.md](../../creating-a-workflow.md) — Part 1 (Live-operator path)

## Purpose

Walk the primary user story from `docs/creating-a-workflow.md` Part 1 end-to-end
on the **live install** and record how long it takes. This is a stopwatch
exercise: the honesty metric PRIORITY-0001 calls for is the observed elapsed
time, not a pre-calculated number.

The story: an operator on a live install with no dev tree and no repo access
hand-authors a `label`-versioned custom testing workflow, validates it with
`validate-workflow.sh`, flips its status to `ready`, drops a labelled
requirement pointed at `pgai-chomp-man`, and confirms the pipeline picks it up.

If Part 1 is honest about being a five-step, no-git, no-dev-tree path, the
observed time should be short. If it is not, the recorded time is the signal
that Part 1 needs to be tightened. Do not guess or estimate the time —
observe it.

## Scope

- Live-install execution only. Every command below runs against
  `$KANBAN_ROOT` (i.e. `$PGAI_AGENT_KANBAN_ROOT_PATH`, typically
  `$HOME/pgai_agent_kanban`). **Do not** use the dev tree for any step.
- Hand-authoring of a single custom workflow plugin under
  `$KANBAN_ROOT/workflows/<name>/`, using the generator and the eight
  `wf_*` hooks documented in `docs/creating-a-workflow.md`.
- One end-to-end validate → flip → drop-requirement → confirm-pickup pass.
- Recording the observed elapsed time in the table in this document.

**Out of scope**

- Any edit to `docs/creating-a-workflow.md` itself. This checkpoint is a
  standalone doc that references Part 1; it does not modify it.
- Any engine change, plugin-manifest edit to shipped plugins, or new managed
  project.
- Waiting for `pgai-chomp-man` to finish the requirement. Pickup and PM
  decomposition prove routing; you do not need to wait for CM to finalize.

## Prerequisites

Before you start the stopwatch:

- The live install at `$KANBAN_ROOT` has been upgraded to v1.1.1 and
  `install.sh` completed cleanly.
- `pgai-chomp-man` is registered in `$KANBAN_ROOT/projects.cfg` and has a
  writable `requirements/` directory. Verify:
  ```bash
  ls -d $KANBAN_ROOT/projects/pgai-chomp-man/requirements/
  ```
- The pipeline is running normally (or you can invoke `wake-batch.sh`
  manually) so a dropped requirement will be picked up on the next tick.
  If the pipeline is HALTed, plan to run `scripts/wake-batch.sh
  --agent=pm --max-tasks=1` after Step 5 to trigger discovery.
- Shell env sourced:
  ```bash
  source $KANBAN_ROOT/shell-env
  ```
- A wall clock or `date +%s` handy. The whole point is to time this by
  observation.

## Naming the workflow (read before starting the timer)

The plugin needs a name that survives future upgrades. The shipped-name
rule refuses `release`, `document`, and `testing-only`. Pick an
org-prefixed name — this walkthrough uses `checkpoint-testing` as the
canonical example. If you use a different name, substitute it consistently
in every command below.

Capability profile for `checkpoint-testing` (matches Part 1's `testing`
user-story shape):

| Field               | Value                     |
|---------------------|---------------------------|
| `version_semantics` | `label`                   |
| `git_mode`          | `ro`                      |
| `finalize`          | `report`                  |
| `agents`            | `pm,coder,tester`         |

`label` semantics keeps the workflow out of release-state and version
ceilings. `ro` gives the workflow a read-only checkout at the labelled
ref. `report` finalize means no tag, no publish — the workflow ends by
writing a test report. The roster omits `cm` because there is nothing to
release.

## Procedure

Run every command below on the live install. All paths are under
`$KANBAN_ROOT`, and `$KANBAN_ROOT` is the live install, not the dev tree.

### 0. START TIMER

Record the start time. This is a real stopwatch step — do not skip it
and do not backfill the number after the fact.

```bash
date +"START %Y-%m-%dT%H:%M:%S%z"
```

Write the start time in the table at the bottom of this document under
**Observed elapsed time**. Then begin Step 1.

### 1. Scaffold the plugin (Part 1 Step 1)

Run the generator against the live install. With
`PGAI_AGENT_KANBAN_ROOT_PATH` set (the normal post-install state), the
generator writes into `$KANBAN_ROOT/workflows/`.

```bash
python3 -m pgai_agent_kanban.workflows.create_new_workflow \
    --name checkpoint-testing \
    --description "Checkpoint-only testing workflow (v1.1.1 walkthrough)" \
    --version-semantics label \
    --git-mode ro \
    --finalize report \
    --agents pm,coder,tester
```

Expected result: a new directory at
`$KANBAN_ROOT/workflows/checkpoint-testing/` containing `workflow.cfg`,
`workflow.sh`, and `contract_check.sh`. Confirm:

```bash
ls $KANBAN_ROOT/workflows/checkpoint-testing/
```

Record any prompt, refusal, or unexpected output in the notes section
below. A refusal here (shipped-name collision, directory-exists) means
the generator did what it was supposed to — pick a different name and
re-run.

### 2. Implement the eight hooks (Part 1 Step 2)

Open `$KANBAN_ROOT/workflows/checkpoint-testing/workflow.sh` and replace
every `NOT IMPLEMENTED` stub with a real body. For a `label`/`ro`/`report`
plugin, the capability-query hooks are one-liners:

```bash
wf_git_mode() {
    echo "ro"
}

wf_resolve_target_version() {
    # label semantics: echo the label back unchanged
    echo "$1"
}

wf_pre_task() {
    # engine owns worktree lifecycle for ro; no per-task setup needed
    return 0
}

wf_post_task() {
    return 0
}

wf_finalize() {
    echo "report"
}

wf_agents() {
    echo "pm,coder,tester"
}

wf_bundle_source_branch() {
    # label workflows do not participate in RC bundling; main is a safe default
    echo "main"
}

wf_dashboard_render() {
    echo "label"
}
```

Use `$KANBAN_ROOT/workflows/testing-only/workflow.sh` as the reference
implementation for label-versioned plugins if any hook is unclear.

Save the file. Do not flip `status` in `workflow.cfg` yet.

### 3. Validate with `validate-workflow.sh` (Part 1 Step 3)

Run the operator gate. It runs all four contract checks (manifest
validity, hook presence, stub detection, capability-value validity).

```bash
$KANBAN_ROOT/scripts/validate-workflow.sh --type checkpoint-testing
```

Expected outcomes:

- **First run (status still `scaffold`).** You should see:
  `FAIL: workflow.cfg status = scaffold — flip to 'ready' after implementing hooks`.
  This confirms hooks passed contract checks but the manifest is not
  ready. Proceed to Step 4.
- **Any other failure.** Fix the reported violation and re-run.
  Common causes: a `NOT IMPLEMENTED` stub left in place; a typo in
  `git_mode`, `finalize`, or `version_semantics`; a hook returning the
  wrong string.

Do not proceed to Step 4 until validation reports either the
`status = scaffold` failure or `PASS`.

### 4. Flip status to ready (Part 1 Step 4)

Edit `$KANBAN_ROOT/workflows/checkpoint-testing/workflow.cfg`. Change
the single line:

```ini
status = scaffold
```

to:

```ini
status = ready
```

Re-run the validator to confirm:

```bash
$KANBAN_ROOT/scripts/validate-workflow.sh --type checkpoint-testing
```

Expected output:

```
PASS: workflow type 'checkpoint-testing' satisfies all contract checks.
```

If the validator still fails, fix and re-run. Do not proceed to Step 5
until `PASS` is reported.

### 5. Drop a labelled requirement (Part 1 Step 5)

Point `pgai-chomp-man` at the new workflow type and drop a labelled
requirement.

- Confirm `pgai-chomp-man`'s current workflow type. It is normally
  `release`; the checkpoint requires temporarily pointing it at
  `checkpoint-testing`:
  ```bash
  grep '^workflow_type' $KANBAN_ROOT/projects/pgai-chomp-man/project.cfg
  ```
- Edit `$KANBAN_ROOT/projects/pgai-chomp-man/project.cfg` and change:
  ```ini
  workflow_type = release
  ```
  to:
  ```ini
  workflow_type = checkpoint-testing
  ```
  Note the original value in the notes section — you will restore it in
  Step 7.
- Drop a labelled requirements file. `label` semantics require the
  intake filename shape `<label>-<slug>.md`. Pick a label that names
  the ref you are testing at (for example, `smoke-2026-07-04`):
  ```bash
  cat > /tmp/smoke-2026-07-04-walkthrough.md <<'EOF'
  # Requirement: v1.1.1 walkthrough smoke test

  ## Target Version
  smoke-2026-07-04

  ## Workflow Type
  checkpoint-testing

  ## Summary
  Walkthrough smoke test for the v1.1.1 custom-workflow checkpoint.
  Read-only run against pgai-chomp-man at the labelled ref.

  ## Acceptance Criteria
  - [ ] The pipeline routes this requirement to the checkpoint-testing
        workflow and PM decomposes it.
  EOF

  cp /tmp/smoke-2026-07-04-walkthrough.md \
      $KANBAN_ROOT/projects/pgai-chomp-man/requirements/smoke-2026-07-04-walkthrough.md
  ```
- Trigger discovery so PM picks it up on the next tick (or wait for the
  next cron firing if the pipeline is live):
  ```bash
  $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm --max-tasks=1
  ```

### 6. Confirm pickup (Part 1 Step 5, continued)

Confirm the requirement was picked up and routed to
`checkpoint-testing`, not to `BLOCKED` and not to a fallback type.

- Read the requirement's status line:
  ```bash
  grep -A2 '^## Status' \
      $KANBAN_ROOT/projects/pgai-chomp-man/requirements/smoke-2026-07-04-walkthrough.md
  ```
  Expected: the status has advanced past `backlog` (typically to
  `running` or a PM-decomposed state). If it is still `backlog` after
  one wake tick, run the wake command a second time.
- Confirm PM created tasks under the roster `wf_agents` returned
  (`pm,coder,tester` — no `cm`):
  ```bash
  ls $KANBAN_ROOT/projects/pgai-chomp-man/tasks/ | grep smoke-2026-07-04 || true
  ```
- If the requirement routed to `BLOCKED`, read the block reason. The
  three common causes are called out in Part 1 Step 5 of
  `docs/creating-a-workflow.md`:
  1. `status = scaffold` in the manifest (Step 4 was skipped).
  2. Typo in the manifest (re-run `validate-workflow.sh`).
  3. `workflow_type` mismatch between `project.cfg` and the plugin
     directory name (double-check the spelling in both files).

Pickup by PM with the expected roster is the acceptance signal. You do
not need to wait for CM finalize — the plugin has proven the routing
path.

### 7. STOP TIMER

Record the stop time and cleanup.

```bash
date +"STOP %Y-%m-%dT%H:%M:%S%z"
```

Write the stop time and computed elapsed time in the table under
**Observed elapsed time** below.

Then restore state so the checkpoint does not leak into future runs:

- Restore `pgai-chomp-man`'s `workflow_type` in `project.cfg` to its
  original value (recorded in the notes section).
- Optionally remove the smoke requirement file if the pipeline has not
  already consumed it:
  ```bash
  rm -f $KANBAN_ROOT/projects/pgai-chomp-man/requirements/smoke-2026-07-04-walkthrough.md
  ```
- Leave `$KANBAN_ROOT/workflows/checkpoint-testing/` in place. It
  survives future upgrades by the upgrade-survival contract (org-prefixed
  name, not shipped). Removing it is optional.

## Observed elapsed time

Fill this table in during the run. **Do not** pre-fill it. Empty rows
are the honest signal that the walkthrough has not yet been performed.

| Field                       | Value                                        |
|-----------------------------|----------------------------------------------|
| Operator                    |                                              |
| Live install root           | `$KANBAN_ROOT` = `<expanded path>`           |
| Plugin name used            | `checkpoint-testing` (or substitute)         |
| Start time (Step 0)         |                                              |
| Stop time (Step 7)          |                                              |
| Total elapsed (mm:ss)       |                                              |
| Step 1 scaffold             |                                              |
| Step 2 implement hooks      |                                              |
| Step 3 first validate       |                                              |
| Step 4 flip + re-validate   |                                              |
| Step 5 drop requirement     |                                              |
| Step 6 confirm pickup       |                                              |

Per-step minutes are optional but useful — they tell PM which stage
consumed the walkthrough time and where Part 1 could be tightened.

## Notes

Use this section during the run to capture anything that surprised you
or that the doc did not predict.

- **`pgai-chomp-man` original `workflow_type`:** `<record here before
  Step 5>`
- **Refusals or unexpected prompts from the generator (Step 1):**
- **Hook stubs that were harder to fill than the doc suggested (Step 2):**
- **Failure paths hit in Step 3 or Step 4:**
- **Pickup outcome (Step 6) — routed to checkpoint-testing / BLOCKED /
  other:**
- **Any drift from Part 1 as written:**

Anything recorded here is the checkpoint's contribution back to
`docs/creating-a-workflow.md`. Do not edit that file from this
checkpoint; the follow-up ticket does that.

## Pass/Fail Rubric

| Outcome                            | Meaning                                                                                                                                                                       | Action                                                                                                                                     |
|------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| **PASS-WALKTHROUGH-CLEAN**         | All seven steps completed on the live install, validator reported `PASS` after Step 4, PM picked up the requirement at Step 6 with the expected roster, elapsed time recorded. | Record PASS in `outcome-notes.md`, cite this checkpoint doc, close the item.                                                               |
| **PASS-WALKTHROUGH-WITH-FRICTION** | All seven steps completed and the walkthrough succeeded, but one or more steps required detours the doc did not predict (a Step 2 hook needed extra logic, a validator failure required an undocumented fix, PM needed a second wake to pick up). Elapsed time recorded. Notes section describes the friction. | Record PASS with friction summary in `outcome-notes.md`. File a follow-up ticket to tighten Part 1 based on the notes.                    |
| **FAIL-WALKTHROUGH-BLOCKED**       | The walkthrough could not complete on the live install. Common causes: generator refuses on the live install without a dev-tree crutch; validator reports a failure that cannot be resolved from Part 1's guidance; PM never picks up the requirement even after two wake ticks. | Record FAIL in `outcome-notes.md` with the exact failure step and observed output. File a bug against v1.1.1 (route through `intake.sh --project pgai-agent-kanban /tmp/BUG-<n>-...`) and HALT the live install pending fix. |

Both `PASS-WALKTHROUGH-CLEAN` and `PASS-WALKTHROUGH-WITH-FRICTION`
satisfy the PRIORITY-0001 acceptance criterion for the walkthrough. The
elapsed time is the honesty metric regardless of outcome.

## References

- Requirements: [v1.1.1-priority-bundle-20260704.md](../../../projects/pgai-agent-kanban/requirements/v1.1.1-priority-bundle-20260704.md)
- Priority: [PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md](../../../projects/pgai-agent-kanban/priority/PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md)
- Source guide walked through: [docs/creating-a-workflow.md](../../creating-a-workflow.md) — Part 1
- Validator: `$KANBAN_ROOT/scripts/validate-workflow.sh`
- Reference plugin: `$KANBAN_ROOT/workflows/testing-only/workflow.sh` (label-versioned exemplar)
- Companion checkpoints: [index.md](index.md), [release-rc-checkpoint.md](release-rc-checkpoint.md), [document-workflow-checkpoint.md](document-workflow-checkpoint.md)
- Outcome ledger: [outcome-notes.md](outcome-notes.md)
