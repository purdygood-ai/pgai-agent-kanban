# Shared Operating Procedure (SOP)

This document defines the shared operating model for all participants in the system:

- HUMAN
- CLAUDE

This file is shared governance. It does not override `DIRECTIVES.md`.

## Shared Team Root

The shared team tree is rooted at:

```
${PGAI_AGENT_KANBAN_ROOT_PATH}
```

Default value:

```
$HOME/pgai_agent_kanban
```

Installs land at `$HOME/pgai_agent_kanban`. Override the location by setting `PGAI_AGENT_KANBAN_ROOT_PATH` before running `install.sh`.

## Purpose

Operate as a coordinated internal work system with:

- Clear ownership
- Safe behavior
- Traceable handoff
- Accurate task state
- Minimal ambiguity

Each participant must understand the full workflow but should act only within its own lane unless explicitly instructed otherwise.

## Core Model

There are two participant swim lanes:

- claude
- human

Each participant primarily works within its own lane, but each task must still have one clear active owner at a time.

Participants may:

- Update task state for tasks they currently own
- Comment or report status on related work
- Request follow-up work from other participants
- Hand off work with clear traceability

Participants must not silently assume control of another participant's active work.

## Three Operating Modes (Discovery Pipeline)

The system runs in one of three modes, all of which share the same canonical discovery pipeline (`team/scripts/lib/discovery.sh`). The pipeline has four steps that run in order, with the first step that produces work stopping the iteration:

```
GUARD   if Active RC ≠ none for this project, exit immediately
STEP 1  scan bugs/, bundle ALL unhandled into a patch-bumped requirements file
STEP 2  scan priority/, bundle ALL unhandled (only if Step 1 found nothing)
STEP 3  scan requirements/, queue the lowest-version unprocessed file for PM
STEP 4  idle exit (no error)
```

The three operating modes that share this pipeline:

| Mode | Invocation | Behavior |
|---|---|---|
| **Continuous** | `wake-batch.sh --agent=pm` (driven by cron) | Runs the pipeline. The next cron tick runs the next iteration. Equivalent to looping at cron cadence. |
| **One-shot** | `pm-agent.sh --auto` | Runs the pipeline EXACTLY ONCE then exits. Same path, same logic, single iteration. For testing the automatic path manually. |
| **Manual** | `pm-agent.sh <doc-path>` | Pipeline NOT used. Queues PM directly with the supplied requirements doc. Operator's escape hatch for "I know exactly what to ship." |

Bundlers always produce **patches** (Z bumps off the value returned by `pp_last_released_version` — the highest semver tag on the dev tree's `origin/main`). Minors and majors are operator-authored. If a patch slot is taken (drafted file exists, materialized marker exists, or git tag exists), the bundler bumps to the next slot and re-checks.

### Status is authoritative; backlog markers are derived

`bug_backlog.md` and `priority_backlog.md` are **derived caches**, not gates. The source of truth for whether the discovery pipeline considers a bug or priority file is the file's own `## Status` header.

- A file with `## Status: open` is eligible for re-bundling, regardless of whether it is currently marked `[x]` in the backlog cache.
- A file with `## Status: running` or `## Status: done` is skipped unconditionally, regardless of its cache marker.
- The `[x]` / `[ ]` markers in `bug_backlog.md` and `priority_backlog.md` reflect *the most recent bundling decision recorded for that file*. They do not block re-evaluation.

This matters for the **edit-and-rebundle** flow. If an operator edits a bug or priority file that was already bundled (for example, to add missing content, correct a typo, or revive a previously-empty file) and resets `## Status` back to `open`, the next discovery iteration re-bundles the file into a new requirements document. The cache marker is updated to reflect the new bundling; it does not need to be hand-cleared first.

If you ever find yourself reaching to manually flip `[x]` to `[ ]` in a backlog file to "force" re-evaluation, stop — that is not the supported recovery path. Edit the underlying bug or priority file, set its `## Status` back to `open`, and let the next discovery iteration do the right thing on its own.

## Filing a Document Brief

A **document brief** is a requirements file that drives the `document` workflow defined in `team/workflows/document.yaml`. It is the operator's entry point for producing standalone documents — from short creative pieces to long-form structured documents (whitepapers, README sets, SOPs, runbooks) — through the same discovery pipeline that handles release and feature requirements.

Document briefs are operator-authored. They are not bundled from `bugs/` or `priority/` — they are dropped directly into the requirements directory, where the discovery pipeline picks them up at Step 3.

### Where to drop the brief

Place the filled-in brief at one of these paths:

```
projects/<name>/requirements/<vX.Y.Z>-<slug>.md            # regular queue
projects/<name>/requirements/priority/<vX.Y.Z>-<slug>.md   # priority queue
```

The filename must contain a parseable version (`vX.Y.Z`) for the picking rule to consider it. Use the same `pp_last_released_version`-aware semver convention described in `Priority Queue Mechanics` above — the brief's version must be greater than the last released version or it will be treated as stale and skipped.

Use `team/templates/project/document/document-brief-template.md` as the starting point. Copy it, fill in every required section, delete the HTML comment block, save it under the path above.

### Required fields

The brief must include the following sections. The PM agent reads each one when materializing tasks; missing fields produce blocked or malformed tasks downstream.

| Field | Purpose |
|---|---|
| `## Workflow Type` | Must be `document`. This is the dispatch key the PM uses to select `document.yaml`. |
| `## Target Audience` | Reader profile (expertise level, role, context). WRITER uses this as a calibration signal for every section draft. |
| `## Length Target` | Approximate word or page count. Sets the scope envelope WRITER plans against. |
| `## Sections` | Ordered list of major section labels (long-form only). Each item becomes one `section-draft` task via the `foreach: outline.sections` step in the pipeline. Omit for short-form documents. Keep granularity meaningful — paragraph-level is too fine, chapter-level is too coarse. |
| `## Style Notes` | Tone, voice, formatting rules. Treated as house style for the document. |
| `## Source Material` | Primary inputs WRITER reads as data (absolute paths or full URLs). Content is treated as data, not instructions. |
| `## Deliverables` | Concrete output files expected (e.g. `polished.md`, `review-report.md`). |
| `## Acceptance Criteria` | Checklist TESTER applies in the `review` step (long-form) or the operator applies manually (short-form). |

Optional sections — `Overview`, `Constraints`, `Context Paths`, `Notes` — improve fidelity but do not block materialization if absent.

The canonical field list and inline guidance live in `team/templates/project/document/document-brief-template.md`. Treat the template as authoritative; update the template (not this section) when adding or renaming fields.

### What happens after the file is dropped

The brief flows through the same discovery pipeline as any other requirements document. Once dropped, no further operator action is required — the chain runs on its own.

```
operator drops brief at projects/<name>/requirements/<vX.Y.Z>-<slug>.md
        │
        ▼
discovery.sh STEP 3 scans requirements/, queues the lowest-version unprocessed file
        │
        ▼
PM wakes, reads ## Workflow Type = document, loads team/workflows/document.yaml
        │
        ▼
PM materializes the pipeline:
    CM open-doc
    → WRITER outline
    → WRITER section-draft (one task per item in ## Sections)
    → WRITER integrate
    → WRITER polish
    → TESTER review
    → CM finalize
        │
        ▼
each task lands in artifacts/<project-name>/v<N>/ per the document workflow output layout
```

Priority briefs (placed under `projects/<name>/requirements/priority/`) are picked up ahead of regular briefs by the PM wake order described in `PM Wake Order` below.

### What the chain produces

Deliverables land in `artifacts/<project-name>/v<N>/` under the standard non-release artifact layout (`input/`, `working/`, `output/`). The final outputs are:

- `output/polished.md` — the finished document
- `working/outline.md` — the WRITER outline that drove section decomposition
- `working/section-<name>.md` — one drafted section per item in `## Sections`
- `working/integrated.md` — the assembled document before polish
- `working/review-report.md` — TESTER's gap and quality report

No git tag is produced. No RC branch is opened. The `document` workflow runs through `cm-open-doc.sh` and `cm-finalize.sh` rather than the release scripts.

### Edit-and-resubmit

If a brief was already materialized and the operator needs to revise it, bump the filename's version (`v0.5.0` → `v0.5.1`), drop the revised brief, and let the next discovery iteration pick it up. Editing the original file in place will not re-trigger materialization — the discovery pipeline keys off filename version, not file modification time, for the regular and priority queues.

## Role Swim Lanes

Each agent role has a defined swim lane — a boundary of responsibility that limits what that role may do. Cross-lane actions are process violations, even if the agent is technically capable of performing them.

The kanban has six vertical agents that move work through the RC pipeline: PO, PM, CODER, WRITER, TESTER, and CM.

- **PO**: Brief expansion only. Validates a human-authored brief, expands it into a structured requirements document at `projects/<name>/requirements/<target-version>.md`, and queues a PM decomposition ticket. Does NOT decompose work into per-task tickets, implement code, or touch the bug pipeline. See `team/roles/PO.md`.
- **TESTER**: Observation only. Files bug reports as individual files in `bugs/`. Does NOT write to `bug_backlog.md`, create fix tickets, or participate in the bug-to-fix pipeline beyond filing the initial report. When evaluating test quality during verification, TESTER cross-references the **Test Authoring Guidelines** section of this SOP — that section is the canonical reference for the five anti-patterns the suite is held to.
- **PM**: Decomposition and synthesis only. Decomposes requirements documents (operator-authored or pipeline-bundled) into fix tickets. Does NOT scan `bugs/` directly, fix bugs, write code, or verify fixes. The discovery pipeline (`lib/discovery.sh`) handles bug bundling automatically; PM picks up the resulting requirements doc through the same path as any other.
- **CODER**: Implementation only. Implements fixes and features from decomposed tickets. Does NOT file bugs, triage bugs, or modify the bug pipeline.
- **WRITER**: Documentation and role governance only. Updates documentation, role files, and SOPs. Does NOT fix code bugs or modify the bug pipeline.
- **CM**: Release operations only. Ships releases, manages RC branches, owns origin. Does NOT decompose requirements or implement fixes.

Cross-lane violations — such as TESTER writing to `bug_backlog.md`, CODER filing its own fix tickets, or PM implementing a bug fix directly — are process violations. Each role trusts that the next role in the pipeline will handle its part. If a role discovers work that belongs to another lane, it files a handoff (bug report, ticket, or status note) and stops.

## Standard Task States

The only approved task states are:

- `BACKLOG` — not started, ready to be pulled
- `WAITING` — pulled from queue but at least one prerequisite task is not yet in DONE or WONT-DO state. Will be auto-promoted back to BACKLOG when prerequisites are satisfied.
- `WORKING` — actively being worked
- `BLOCKED` — waiting on dependency, approval, input, tool, or clarification (not auto-resolved)
- `DONE` — accepted and finished
- `WONT-DO` — intentionally declined or closed without completion

Use these exact state names in task `status.md`. Do not invent alternate state names.

### State transitions

```
BACKLOG  ──pulled, prereqs satisfied────→  WORKING
BACKLOG  ──pulled, prereqs unmet────────→  WAITING
WAITING  ──prereqs become satisfied─────→  BACKLOG  (automatic, by wake script)
WORKING  ──work complete────────────────→  DONE
WORKING  ──cannot continue──────────────→  BLOCKED
WORKING  ──intentionally declined───────→  WONT-DO
BLOCKED  ──unblocked by HUMAN───────────→  BACKLOG  (manual)
```

### WAITING vs BLOCKED

These two states sound similar but mean different things:

- **WAITING** is a soft, automatic state. The task hasn't started because something else needs to finish first. The wake script will automatically flip WAITING → BACKLOG when prerequisites complete. No human action needed.
- **BLOCKED** is a hard, manual state. The task tried to do work and hit something it cannot resolve on its own — missing credentials, ambiguous requirements, broken upstream code. A human must investigate and unblock it.

The queue marker reflects the difference:

- `[ ]` or `[]` — pending (BACKLOG)
- `[W]` — waiting on prerequisites
- `[B]` — hard blocked
- `[x]` — done or won't-do

### WONT-DO Authority

Any participant may set a task to `WONT-DO`. When doing so, the participant must:

- State the exact reason in `status.md`
- Set `Needs Human: yes`
- Not treat the task as closed until HUMAN confirms

HUMAN is the final authority on whether a `WONT-DO` stands.

### Re-running a BLOCKED task

When an operator decides a BLOCKED task should run again — usually because a fix has landed on the active RC branch or the upstream prerequisite has been resolved — the entire operator action is a single state flip:

```bash
# Flip the task's State field in status.md from BLOCKED back to BACKLOG.
# Also update the queue marker: [B] -> [ ]
```

That is the whole procedure. **Do not delete or move `artifacts/report.md`, `artifacts/gaps.md`, or any other prior artifacts.** The framework handles artifact preservation automatically on the next BACKLOG → WORKING transition.

When the wake script picks up the re-queued task, `process_one_task` rotates any existing agent-written artifacts under the task folder's `artifacts/` directory before invoking the agent:

- `report.md` → `report.md.previous-RUN-N`
- `gaps.md` → `gaps.md.previous-RUN-N`

`N` starts at 1 and increments so successive re-runs produce `.previous-RUN-1`, `.previous-RUN-2`, etc. The rotation is logged in the wake log (`stale artifact rotated: ... -> ...`). The incoming agent therefore always starts from a clean slate while every prior run's evidence is preserved as read-only history. The invariant is role-agnostic — it protects TESTER, CODER, and WRITER alike against stale-artifact trust.

This means the re-run workflow is intentionally minimal:

1. Operator fixes the underlying problem (applies patch, edits brief, etc.).
2. Operator flips `State: BLOCKED` → `State: BACKLOG` in `status.md` and updates the queue marker from `[B]` to `[ ]`.
3. Walk away. The next wake firing picks up the task, rotates the prior artifacts, and runs the agent fresh.

If you find yourself manually deleting `report.md` or `gaps.md` before re-queuing, stop — the framework does that for you, and manual deletion loses the evidence trail. The wake script's preservation rotation ensures TESTER always starts fresh from a clean artifacts state.

## Ownership Rule

A task must have one clear active owner at a time.

If work needs to move across participants, it should be handed off or reassigned with a new or updated task folder that clearly identifies the owner.

## Instruction Precedence

When instructions conflict, use this precedence from highest to lowest:

1. Direct HUMAN instruction on the current task
2. `DIRECTIVES.md`
3. `SOP.md`
4. Task folder `README.md`
5. Role file referenced by the task
6. Participant file
7. Workspace `README.md`
8. Participant private runtime notes

Lower-precedence files must not override higher-precedence files.

If a conflict is discovered, follow the higher-precedence instruction and report the conflict in `status.md`.

## Required Read Order For Assigned Work

Unless a task explicitly says otherwise, a participant performing substantial work should read in this order. The order mirrors the layered stack in `OVERVIEW.md` (How to read the kanban's instruction stack) and the eight-layer governance stack at the top of every role file.

1. `${PGAI_AGENT_KANBAN_ROOT_PATH}/DIRECTIVES.md` — top-level rules
2. `${PGAI_AGENT_KANBAN_ROOT_PATH}/OVERVIEW.md` — autonomy principle and orientation
3. `${PGAI_AGENT_KANBAN_ROOT_PATH}/SOP.md` — this file
4. `${PGAI_AGENT_KANBAN_ROOT_PATH}/README.md` — kanban project entry-point and context
5. `${PGAI_PROJECT_ROOT}/README.md` — per-project orientation when present (for the kanban-self project this is the same file as layer 4; the wake script deduplicates)
6. The role file referenced by the task (`team/roles/<ROLE>.md`)
7. The assigned task folder `README.md`
8. The assigned task folder `status.md`
9. Any task-referenced local repository or worktree `README.md` files
10. Private runtime notes only as needed

Each layer narrows scope. If layers conflict on rules, the Instruction Precedence section above is authoritative. Participants should not reread unrelated documentation without a reason.

## Write Boundary

The shared team tree is read-only by default except for assigned task folders under `${PGAI_AGENT_KANBAN_ROOT_PATH}/tasks/...`.

Participants may write inside an assigned task folder to:

- Update `status.md`
- Place deliverables in `artifacts/`
- Place logs in `logs/`
- Update task-local files when the task explicitly requires it

Participants should not write elsewhere in the shared tree unless HUMAN explicitly assigns documentation maintenance work.

## Task Folder Source of Truth

Each assigned task folder is the source of truth for that task.

Standard task folder shape:

```
${PGAI_AGENT_KANBAN_ROOT_PATH}/tasks/<TASK-ID>/
  README.md
  status.md
  artifacts/
  logs/
```

## Per-Agent Queue Model (Multi-Queue)

The system supports multiple per-agent queues. Each agent has its own backlog file under:

```
tasks/queues/<agent>_backlog.md
```

For example, a coder agent queue would live at:

```
tasks/queues/coder_backlog.md
```

### Task ID and queue path format

Task IDs use the format `<AGENT>-YYYYMMDD-NNN-slug`; the framework continues to parse the legacy `CLAUDE-<AGENT>-YYYYMMDD-NNN-slug` format from historical task folders and queue entries. Queue files use the flat layout `tasks/queues/<agent>_backlog.md`. The `pp_queue_path` helper transparently resolves either location, so projects with legacy task folders still operate cleanly. No existing task folder is renamed — old `CLAUDE-`-prefixed task folders remain readable and processable forever.

### How it works

- Each backlog file lists tasks assigned to that specific agent, one per line, using the standard queue marker format (`[ ]`, `[W]`, `[B]`, `[x]`).
- Wake scripts read the agent-specific backlog file to determine which task to pull next.
- Tasks in a per-agent queue follow the same state machine and acceptance criteria as tasks in the general queue.
- When a new task is created for a specific agent type, it should be registered in that agent's backlog file, not in a shared catch-all.

### Queue file format

Each line in a backlog file represents one task:

```
[ ] TASK-ID — short description
```

Markers follow the same conventions as the main queue:

- `[ ]` or `[]` — BACKLOG (pending)
- `[W]` — WAITING on prerequisites
- `[B]` — hard BLOCKED
- `[x]` — DONE or WONT-DO

### Creating new queues

If a new agent type is introduced, create its backlog file at the appropriate path before assigning tasks to it. Do not create tasks for an agent that has no backlog file.

## HALT Flags

The kanban uses a `HALT` flag to pause the chain. When set, all chain agents stop pulling new tasks.

### Behavior

When `${PGAI_AGENT_KANBAN_ROOT_PATH}/HALT` exists, chain wake scripts (PO, PM, CODER, WRITER, TESTER, CM) must exit cleanly before pulling the next task. No new chain agent invocations are started.

- The HALT file presence is checked at the start of each wake cycle, before task selection.
- If HALT is present, the wake script logs the halt condition and exits with status 0 (clean exit, not an error).
- Any task currently WORKING is left in WORKING state. The agent already running is not interrupted.
- HALT does not change any task states.

### Setting and clearing the flag

- To pause the chain manually: `touch ${PGAI_AGENT_KANBAN_ROOT_PATH}/HALT`
- To resume the chain: `rm ${PGAI_AGENT_KANBAN_ROOT_PATH}/HALT`

### Who may create and remove the flag

`HALT` has two authorized creators: the operator (manual halt) and CM (autonomous halt for systemic issues or mechanical release failures). Both write to the same path; the chain treats them identically.

- The **operator** creates `HALT` for any reason at any time (governance edits, full-stop reviews, pause to investigate).
- **CM** creates `HALT` only for the eight enumerated triggers documented in `team/roles/CM.md` ("HALT Authority") and summarized below in the "When the chain halts" section. CM writes a comment header to the file so the operator can see who created it and why.
- The **operator** is the only role that removes `HALT`. CM never removes it. Removal is the operator's signal that the underlying issue is resolved and the chain may resume.

Other chain agents (PO, PM, CODER, WRITER, TESTER) must not create or remove `HALT`. They have no HALT authority.

### When to use the flag

Use `HALT` (operator-initiated) when:

- A systemic problem has been discovered that affects multiple tasks in the chain
- A governance change is being applied and you want to prevent chain task pulls during the transition
- A release review is in progress and you want no new automated chain work to begin

CM may also create `HALT` autonomously when one of the eight HALT triggers documented in `team/roles/CM.md` fires. See "When the chain halts" below for the operator response procedure.

Use neither flag during normal operation. The chain ships work autonomously.

### SUBSTRATE_BROKEN pause expectation

A third halt signal is reserved for substrate-level failures: `SUBSTRATE_BROKEN`. When the wake substrate (config loader, queue parser, agent dispatch, lockfile invariants) detects an inconsistency it cannot safely work around, it sets `SUBSTRATE_BROKEN` and exits. Agents that observe this signal — whether through an environment variable, a marker file written by the wake script, or a substrate-check helper — must pause and not attempt to advance the chain or mutate task state. The signal means the floor under the chain is unstable; running an agent on top of unstable substrate risks corrupting queues, lockfiles, or task folders in ways the system cannot detect.

Operator response is the same as for `HALT`: investigate the substrate failure, fix the underlying issue, and clear the signal before resuming. Currently the signal is recognized only by the wake substrate itself. Agents that notice substrate breakage in the course of their pre-flight checks should treat it as a blocking pre-flight failure and exit without touching shared state.

## When the chain halts

A halted chain is the autonomous system's signal that operator judgment is required. The operator removes the `HALT` file when the underlying issue is resolved. This section is the operator-facing playbook for diagnosing and resolving an autonomous HALT.

The chain may halt for one of two reasons: the operator created `HALT` manually (covered above), or CM created `HALT` autonomously because one of its eight HALT triggers fired. The procedure below covers the autonomous case.

### CM HALT triggers

CM creates `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT` for any of the following eight conditions. `team/roles/CM.md` is the authoritative reference; this list summarizes for operator orientation.

1. **TESTER state is `BLOCKED`** — TESTER could not complete verification (pre-flight failure, runner crash, missing requirements, unreachable dev tree).
2. **TESTER systemic_risk is `high`** — TESTER's report-level systemic risk is `high`. Indicates a broader framework regression or a stuck CODER loop.
3. **Any finding has Fix Effort = `large` in a `SHIP-WITH-SERIOUS-CONCERNS` context** — shipping through a large-effort serious finding is too risky; operator must scope the work first.
4. **Pre-squash hook fails** — `cm-release-pre-squash.sh` exited non-zero. Finalization mechanic broken.
5. **Squash to develop or main has conflicts** — git state damaged; human judgment required before any branch mutation continues.
6. **Push to origin fails after retries** — origin not reachable or rejecting the push. Release is locally complete but cannot be distributed.
7. **Tag already exists on remote** — race condition or repeated invocation against an already-shipped version.
8. **Last 3 consecutive RCs for this project were all marked `NON-FUNCTIONAL`** — pattern indicates the chain is shipping degraded work repeatedly without self-correcting.

When CM halts on trigger 8, it also files a bug naming the pattern so the systemic issue is visible in the bug queue.

### Operator response procedure

When the operator returns to a halted chain:

```bash
# 1. Confirm HALT is present and read its comment header to see who created it and why.
cat "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"

# 2. Find the BLOCKED CM task that wrote the HALT (most recent).
#    Glob both formats: legacy CLAUDE-CM-* folders and the
#    current CM-* format. Both layouts coexist on disk indefinitely.
ls $PGAI_AGENT_KANBAN_ROOT_PATH/projects/*/tasks/CLAUDE-CM-*/status.md \
   $PGAI_AGENT_KANBAN_ROOT_PATH/projects/*/tasks/CM-*/status.md 2>/dev/null \
  | xargs grep -l "^BLOCKED$" \
  | tail -3

# 3. Read that task's status.md for the full reason and any context CM recorded.
cat <path-from-above>

# 4. Read the project's release-state.md for the HALT Event log.
cat $PGAI_AGENT_KANBAN_ROOT_PATH/projects/*/release-state.md
```

The CM task's `## Blockers` field contains the plain-language reason. The `release-state.md` HALT Event entry contains a timestamp, the trigger number, the task ID, and the one-line reason — useful when reviewing the history of past halts.

Once the issue is understood and resolved:

```bash
# Resume the chain.
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
```

After `HALT` is removed, the next wake firing enters the chain again. The BLOCKED CM task must be re-run (or the operator must manually advance the release by resolving the underlying issue and re-invoking the release script). Removing `HALT` does not automatically retry the CM task — the task state is unchanged by `HALT` creation or removal.

### HALT-file lifecycle

The HALT file has a deliberately simple lifecycle:

| Step | Actor | Action |
|---|---|---|
| 1. Create | Operator or CM | Write the file at `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT`. CM writes with a comment header documenting timestamp, trigger reason, and resolution pointer. Operator may `touch` for manual halts. |
| 2. Observe | Chain wake scripts | At each cron firing, before task selection, the wake script checks for the file. If present, exits cleanly with status 0 and logs the halt condition. |
| 3. Investigate | Operator | Reads the HALT file header, the most recent BLOCKED CM task's `status.md`, and the project's `release-state.md` HALT Event log. |
| 4. Resolve | Operator | Performs whatever action the trigger requires (fix the dev tree, resolve a conflict, push manually, adjust the priority queue, etc.). |
| 5. Remove | Operator | `rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"`. The chain resumes at the next wake firing. |

CM never removes `HALT`. The chain never auto-resumes. Removal is always the operator's deliberate signal.

When CM creates the HALT file, the format is:

```
# HALT created by CM at <ISO-8601 timestamp>
# Reason: <one-line description of the trigger>
# Resolution: Operator review required. See CM task status.md for full reason.
```

This makes the file self-documenting: `cat HALT` is enough to know who created it and where to look next. Manual operator HALTs (from `touch`) have no header; that absence itself indicates the halt was operator-initiated rather than CM-initiated.

## Per-Project HALT Workflow

Per-project HALT pauses one registered project while the rest of the chain keeps running. It is a peer of the global `HALT` flag, scoped to a single project's directory. Use it when one project needs to sit still — mid-investigation, mid-edit, or while you reshape its inputs — without freezing every other project on the kanban.

The architectural shape is documented in `ARCHITECTURE.md` under "Multi-Project Support". This section is the operator workflow.

### Halt one project

The flag file lives at `$KANBAN_ROOT/projects/<name>/HALT`. Create it to halt; delete it to resume.

```bash
# Halt the named project (replace <name> with the actual project name).
touch "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"

# Resume.
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"
```

Substitute the real project name in place of `<name>`. The project name is the directory name under `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/`, and it matches the `project_name` key in that project's `project.cfg [project]` section. To list registered projects:

```bash
ls "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/"
```

The HALT file's contents are ignored — only its presence matters. `touch` is sufficient; an empty file does the job.

### Verify the halt

Two ways to confirm a project is actually halted.

**Helper invocation.** Source the `pp_*` helpers and call `pp_project_halted`. It returns 0 (true) when the HALT file exists and 1 (false) when it does not, with no output.

```bash
KANBAN_ROOT="$PGAI_AGENT_KANBAN_ROOT_PATH" \
  bash -c 'source "$KANBAN_ROOT/team/scripts/lib/project_paths.sh"; \
    pp_project_halted "<name>" && echo "halted" || echo "running"'
```

Substitute the real project name for `<name>`. The command prints `halted` or `running`.

**Wake log.** The next wake firing emits one log line per project as it iterates the registry. Halted projects produce a `per-project HALT present, skipping` line; running projects produce a `beginning chain` line. The log lives in the single shared wake log with a per-project prefix, so grep the project name to filter:

```bash
grep "<name>" "$PGAI_LOGS_DIR/cron-pm.log" | tail -20
```

The wake script also tags each line with the agent that fired (`pm`, `coder`, `writer`, etc.), so check the log for the agent you expect to run against the project. If no wake has fired since you touched the HALT file, wait one cron tick or invoke `team/scripts/wake/claude.sh --agent=<agent>` manually to see the next iteration's decision.

### Global HALT versus per-project HALT

The two flags have different scopes and different precedence. Pick the one that matches what you want to pause.

| Flag | Path | Scope | When to use |
|---|---|---|---|
| Global `HALT` | `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT` | Every chain agent across every project | Systemic problems, governance transitions, full-stop reviews |
| Per-project `HALT` | `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT` | Only the named project; other projects keep running | One project needs to sit still while the rest of the kanban ships |

Global HALT wins. If `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT` exists, the wake scripts exit at startup before entering the per-project loop and no project iterates — per-project flags are not even consulted in that state. Per-project HALT only takes effect when the global flag is absent.

### When to reach for per-project HALT

- A specific project is mid-investigation and you do not want its chain to keep churning while you read its `bugs/`, `priority/`, or `release-state.md`.
- You are hand-editing requirements or queue files inside one project and want that project paused without freezing siblings that are mid-release.
- You want to drain one project's WORKING task without queuing new work for it. Touch the HALT file; the current task finishes; nothing new starts.

Per-project HALT does not change task states. Tasks already in WORKING stay in WORKING. Tasks in BACKLOG stay in BACKLOG. The flag only gates whether the wake script enters that project's chain on the next firing.

## HALT-AFTER (soft halt)

`HALT-AFTER` is a soft halt: the chain keeps running until a named event drains, then auto-promotes to a regular `HALT`. Use it when you want the chain to finish what it has in flight before stopping, instead of freezing immediately.

The difference from `HALT` in one sentence: `HALT` stops the chain at the next wake firing; `HALT-AFTER` lets the chain keep firing until the named drain condition is met, then converts itself into a `HALT` automatically.

### File location and scope

`HALT-AFTER` mirrors `HALT` exactly. Two scopes, same precedence rules as the hard flags.

| Flag | Path | Scope |
|---|---|---|
| Global `HALT-AFTER` | `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT-AFTER` | Every chain agent across every project |
| Per-project `HALT-AFTER` | `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT-AFTER` | Only the named project |

`HALT-AFTER` does not gate wakes while it is draining. The chain keeps running normally until the drain condition is satisfied — that is the whole point of the soft variant.

### Event tokens

The file's contents are the event token. Write the token into the file (`echo rc > HALT-AFTER`, or `touch HALT-AFTER` for the default). Six tokens are supported.

| Token | Drains when |
|---|---|
| `rc` | The RC that was in flight at arm time ships (its version tags on `main`), regardless of whether a follow-on RC opens immediately |
| `pm` | No WORKING tasks with role=PM exist in the project's queues |
| `coder` | No WORKING tasks with role=CODER exist in the project's queues |
| `writer` | No WORKING tasks with role=WRITER exist in the project's queues |
| `tester` | No WORKING tasks with role=TESTER exist in the project's queues |
| `cm` | No WORKING tasks with role=CM exist in the project's queues |

`rc` means "the current RC" — when the file is created (or first evaluated) with token `rc`, the arm-time RC version is captured from the project's `release-state.md` and recorded alongside the token (e.g. `rc:v0.40.0`). The drain check compares the shipped version against that recorded version; it does **not** test for RC-idle. Back-to-back RCs do not defer the drain: if v0.40.0 was in flight at arm time and v0.40.1 opens immediately after v0.40.0 tags, the sentinel still promotes to `HALT` on v0.40.0's ship, because v0.40.0 is the version the token was bound to. If no RC is in flight at arm time, the token has no version to bind to and the drain cannot fire until an RC opens and then ships under that token's lifetime — arm `rc` only while the RC you want to halt after is actually in flight. The role-specific tokens are narrower — wait until the named role has no in-flight work.

The `rc:vX.Y.Z` drain is **monotonic and latching**. `halt_after/drain.py` reads `## Last Released` from the project's `release-state.md` (written by `cm-release.sh` Step 15 on every successful release) and compares it against the captured version with semver `>=`, not string equality. The token drains as soon as the captured version OR ANY LATER version has shipped. Concretely: a token captured at `rc:v0.44.2` drains once `## Last Released` reads `v0.44.2`, `v0.44.3`, or any higher semver. This matters because the drain evaluation does not always run in the narrow window between "vX.Y.Z just tagged" and "the next RC's first release also lands" — `>=` makes the drain immune to that race. The `>=` semantics also tolerate `## Last Released` being absent or empty on fresh projects that have never shipped — drain returns False until a release populates the field.

### Token parsing

The file is parsed with `.strip().lower()`:

- Leading and trailing whitespace is stripped.
- The token is lowercased.
- An empty file (or whitespace-only) defaults to `rc`.
- An unrecognized token is logged as a warning and treated as absent — the chain continues, and no auto-promotion fires until the file holds a valid token.

Use the exact lowercase spellings above: `rc`, `pm`, `coder`, `writer`, `tester`, `cm`.

### Auto-promotion

When the drain condition for the file's token is met, the chain auto-promotes `HALT-AFTER` to `HALT` at the same scope:

1. `rm HALT-AFTER` (at whichever scope the file lives — global or per-project).
2. `touch HALT` (at the same scope).
3. Append an audit entry to that project's `release-state.md` recording the promotion.

After promotion, the chain is hard-halted exactly as if the operator had touched `HALT` directly. No further wake firings advance work until the operator removes `HALT`.

### Audit entry shape

The auto-promotion appends a `## HALT Event` block to the project's `release-state.md`. The exact shape:

```
## HALT Event
- Timestamp: 2026-05-29T01:23:45+00:00
- Trigger: HALT-AFTER rc auto-promotion
- Event drained: rc (arm-time RC v0.39.6 shipped)
- Promoted: HALT-AFTER → HALT
```

The `Trigger` field names the token that was draining. The `Event drained` field records the concrete condition that resolved (which RC cleared, which role finished, etc.). The `Promoted` line is always literal: `HALT-AFTER → HALT`.

### Operator resume

Resume is unchanged from a normal `HALT` resume:

```bash
# Resume after HALT-AFTER auto-promoted to HALT.
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
```

The operator does not interact with `HALT-AFTER` at resume time — it was removed by the auto-promotion step. Only `HALT` remains, and removing `HALT` lets the next wake firing enter the chain again. The chain itself never removes either flag; promotion creates `HALT`, and the operator clears it. See "When the chain halts" above for the full operator response procedure.

## Release State File

Release state is split between two sources, on purpose:

- **In-flight RC state** lives in the project's `release-state.md` at:

  ```
  $KANBAN_ROOT/projects/<project-name>/release-state.md
  ```

  The file holds four fields: `## Active RC`, `## RC Opened At`, `## RC Opened By Task`, and `## Last Released`. CM-open populates the first three when it cuts the RC branch; CM-release and `cm-cancel-rc.sh` clear them back to `none`. `## Last Released` holds the most recent shipped version in `vX.Y.Z` form; CM-release Step 15 writes it on every successful release, and `cm-cancel-rc.sh` never clears it. The three RC-tracking fields are ephemeral; `## Last Released` is monotonic and latching — it only moves forward.

- **Historical release state** is git tags on the dev tree's `main` branch. A tag like `v0.21.6` IS the v0.21.6 release. The highest semver tag merged into `origin/main` is the canonical answer to "what version are we at," resolved via the `pp_last_released_version` helper. The `## Last Released` field in `release-state.md` is a separate, narrower signal used only by `halt_after/drain.py` for the `rc:vX.Y.Z` drain check (see below); it is not a substitute for the helper and consumers other than the drain code must not read it.

The schema for `release-state.md` is exactly:

```
# Release State

## Active RC
none

## RC Opened At
none

## RC Opened By Task
none

## Last Released
v0.44.3
```

`## Active RC`, `## RC Opened At`, and `## RC Opened By Task` track the in-flight RC. They are populated by `cm-open-rc.sh` and cleared back to `none` by `cm-release.sh` or `cm-cancel-rc.sh`.

`## Last Released` holds the most recent shipped version in `vX.Y.Z` form. It is written by `cm-release.sh` Step 15 after the tag is created, and is consumed by `team/pgai_agent_kanban/halt_after/drain.py` to evaluate the `rc:vX.Y.Z` HALT-AFTER drain via semver `>=`. Lifecycle:

- **Fresh install** — the field may be absent or empty on projects that have never shipped. Drain treats absent/empty as "no release has shipped yet" and does not fire.
- **First release** — `cm-release.sh` Step 15 populates the field with the just-shipped version.
- **Subsequent releases** — Step 15 overwrites the field with each new shipped version. The value only moves forward in semver order.
- **Cancel** — `cm-cancel-rc.sh` never clears `## Last Released`. Cancelling an RC has no historical-release effect.

The retired `Last Released At` and `Last Released By Task` fields are not part of the current schema.

### How to ask "what version are we at"

All consumers — discovery, CM, PO, dashboard, the autonomous scan — call the canonical helper:

```bash
source "$KANBAN_ROOT/team/scripts/lib/project_paths.sh"
pp_last_released_version "pgai-agent-kanban"
# -> v0.21.6  (or whatever is highest on origin/main)
```

The helper reads `dev_tree_path` from the project's `project.cfg`, runs a best-effort `git fetch origin --tags --quiet`, lists tags merged into `origin/main`, filters to strict semver, and returns the highest. It returns `v0.0.0` when no tags exist or when the dev tree is unreachable. It never changes the caller's CWD.

Do not parse `## Last Released` out of `release-state.md` as a general "what version are we at" answer — the field exists only as a drain signal for the `rc:vX.Y.Z` HALT-AFTER token and is owned by `cm-release.sh` + `halt_after/drain.py`. Do not run `git tag` directly — call `pp_last_released_version` so the dev-tree resolution stays in one place. The helper reads git tags; it does not read `## Last Released`.

### Why the split

Multiple bug classes traced back to the old design where `release-state.md` tried to be authoritative for both in-flight RC state and historical release state. Deterministic patches drifted because `upgrade.sh` shipped tags but never edited the file. Scripts read different copies of `release-state.md` from different paths. `install.sh` did not seed the file uniformly. All of those are gone now: git history is immutable, the tag IS the release, there is nothing to keep in sync with anything.

### Migration

`install.sh` migrates existing installs automatically:

- Preserves `## Active RC` if non-`none` (operators in the middle of an in-flight RC do not lose it).
- Drops `Last Released At` and `Last Released By Task` if present in the old file (these fields are no longer part of the schema).
- Preserves `## Last Released` if present; otherwise leaves it absent so the next `cm-release.sh` Step 15 writes it on the first successful release.
- Writes the canonical schema (`## Active RC`, `## RC Opened At`, `## RC Opened By Task`, `## Last Released`).

The previous repo-level `team/release-state.md` has been removed entirely. The only `release-state.md` files in the system are project-scoped at `$KANBAN_ROOT/projects/<project-name>/release-state.md`. No manual file editing is required during upgrade.

## Workflow Types

The system supports multiple workflow types that define how requirements are decomposed into tasks and how deliverables are produced. Workflow types are declared in the requirements document under `## Workflow Type` or, for project-based workflows, in the project's `PROJECT.md`.

### YAML Workflow Definitions

Workflow types are defined as YAML configuration files in `team/workflows/<name>.yaml`. Each YAML file specifies:

- **Inputs** -- what the workflow expects (required files, optional files, context references)
- **Pipeline** -- the ordered sequence of agent steps that transforms input to output
- **Outputs** -- the format and location of the final deliverable
- **Versioning** -- how versions are assigned (`auto-increment`, `from_requirements`, or `none`)

Adding a new workflow type is a data operation: create a new YAML file in `team/workflows/`, no code changes required. The materializer and subagent prompts adapt automatically based on the workflow definition.

Each ticket generated by the PM agent includes a `## Workflow Type` field. Tickets without this field default to `release` behavior for backward compatibility.

PM reads `PROJECT.md` (if it exists for the requested project) to determine which workflow type applies. If no `PROJECT.md` exists, the default is `release`.

### Available Workflow Types

#### release (default)

The standard software release lifecycle. Produces a full CM-bookended task plan:

```
CM-open → feature tasks → TESTER (if Test Required = true) → CM-release
```

- Creates an RC branch (`rc/vX.Y.Z`)
- Requires `## Target Version` to be set in the requirements document
- PM blocks if an Active RC is already open
- Output: git tag on `main`
- Versioning: `from_requirements`

#### feature

A lightweight feature workflow for multi-task work that shares a common branch but does not constitute a full release. Produces:

```
CODER(create-shared-branch) → feature tasks → TESTER (if Test Required = true)
```

- No CM bookends; no RC branch
- Requires `## Source Branch` to be set (or falls back to the Active RC if one is open)
- PM does not block on an open Active RC for feature workflows

#### document

Document content — from short creative pieces to long-form structured documents. Produces:

**Short-form** (no `## Sections` in brief):

```
CM-open-doc → WRITER(outline) → WRITER(draft) → WRITER(polish) → CM-finalize
```

**Long-form** (brief includes a `## Sections` list):

```
CM-open-doc → WRITER(outline) → WRITER(section-draft, foreach) → WRITER(integrate)
           → WRITER(polish) → TESTER(review) → CM-finalize
```

- No git operations; deliverables are written to the artifacts directory
- Primary agent is WRITER (not CODER)
- CM uses `cm-open-doc.sh` and `cm-finalize.sh` instead of the release scripts
- Output: markdown in `artifacts/<project-name>/v<N>/output/`
- Versioning: `auto-increment`

### Workflow Type x Test Required combinations

| Combination | Task assembly |
|---|---|
| `release` + `true` | CM-open → feature tasks → TESTER → CM-release |
| `release` + `false` | CM-open → feature tasks → CM-release |
| `feature` + `true` | CODER(create-shared-branch) → feature tasks → TESTER |
| `feature` + `false` | CODER(create-shared-branch) → feature tasks |
| `document` + N/A | CM-open-doc → WRITER tasks → CM-finalize |

If `Workflow Type = feature` and `Source Branch` is unset and no Active RC is found, PM emits a single blocked task and halts decomposition.

## Artifacts Directory Layout

Non-release workflows (document, etc.) write deliverables to a structured artifacts directory rather than producing git tags or branches.

### Directory structure

```
$KANBAN_ROOT/artifacts/<project-name>/v<N>/
├── input/        — what was asked + (optional) prior version
├── working/      — agent intermediate state (outlines, drafts)
└── output/       — the final deliverable
```

### Naming conventions

- **Project name**: lowercase, hyphenated, alphanumeric only. Must match the regex `^[a-z0-9][a-z0-9-]*$`. Examples: `kids-story-creek`, `claude-code-whitepaper`.
- **Version directories**: `v1/`, `v2/`, `v3/`, etc. No zero-padding. Versions increment sequentially.
- **Empty version directories are valid**: a run might fail before producing output.

### PROJECT.md

Each project has a `PROJECT.md` file at `$KANBAN_ROOT/artifacts/<project-name>/PROJECT.md` with metadata:

```markdown
# Project: <name>

## Workflow Type
<workflow-type-name>

## Description
<one-paragraph description>

## Output Name
<filename-base>

## Output Formats
- markdown
- pdf

## Priority
<integer>

## Next Version
<integer>
```

Required fields are validated against the workflow YAML's specification at runtime. `## Next Version` is incremented automatically by the `cm-open-doc.sh` script when a new version is started.

### CM operations for non-release workflows

Two new CM scripts handle the bookend operations for non-release workflows:

- **`cm-open-doc.sh <project-name>`** -- validates PROJECT.md, increments the version, creates the `v<N>/{input,working,output}` directory structure, and copies any staged inputs.
- **`cm-finalize.sh <project-name> <version>`** -- reads PROJECT.md to determine output formats, packages the working draft into final form in `output/`, and writes a summary.

## Release Lifecycle

The system uses a Release Candidate (RC) branch pattern to stage and review releases before they reach `main`.

### Branch structure

```
main          — stable, production-ready releases only
  └── develop — ongoing integration of completed features
        └── rc/vX.Y.Z  — release candidate branch for version X.Y.Z
              └── feature/<TASK-ID>  — individual task branches
```

### Workflow

1. **RC branch creation**: When a release is being staged, a human or PM agent creates `rc/vX.Y.Z` branching from `develop`.
2. **Task branches**: Individual feature or fix tasks branch from `rc/vX.Y.Z` (not from `develop` directly) and are named `feature/<TASK-ID>`.
3. **Merging to RC**: When a task is complete, the feature branch is merged back into `rc/vX.Y.Z` with `--no-ff` to preserve history.
4. **Human review**: A human reviews the RC branch. This may include running tests, reviewing changes, and verifying acceptance criteria.
5. **Merge to main**: After human approval, the RC branch is merged into `main` for release.
6. **Back-merge to develop**: After the release, `main` (or the RC branch) is merged back into `develop` to keep develop current.

### Tagging

Releases on `main` should be tagged with the version number (e.g., `v0.2.0`) at the merge commit.

### Release-notes polish (autonomous)

`cm-release.sh` writes a stub `release-notes/vX.Y.Z.md` from the commit log, commits it, tags the release, and pushes. Shortly after, a WRITER polish task fires and rewrites that stub in-place with structured content (Summary, What Shipped, Bugs Resolved, Known Issues, etc.).

The polish reaches origin without operator intervention. Before pushing `main`, `cm-finalize-release.sh` runs Step 2b: it checks `release-notes/${VERSION}.md` for uncommitted changes and, if present, creates a `Polish release notes for ${VERSION}` follow-up commit and pushes it as part of the same finalize step. Operators no longer need to run `git add release-notes/v*.md && git commit && git push` after an autonomous ship.

The flow degrades gracefully:

- If WRITER polish never runs (timeout, error, agent unavailable), the stub remains committed and the release still ships — Step 2b is a no-op.
- If the polished file matches the stub byte-for-byte, no follow-up commit is created.
- If `release-notes/${VERSION}.md` is missing entirely, finalize logs a warning and continues; the release is not blocked.

This means the published tag may point at the stub commit while a follow-up commit on `main` carries the polish. That is intentional — the tag is the snapshot of code; release notes are a documentation artifact that can land separately.

### Constraints

- Agents must not merge RC branches into `main`. That is a HUMAN action.
- Agents must not create RC branches without explicit task assignment.
- Agents work on feature branches and merge to the RC branch only — never directly to `main` or `develop` during a release cycle.

## Project-Specific CM-Release Hooks

`cm-release.sh` is project-agnostic — it ships a tagged version of source code and nothing more. Projects that need additional per-release work (bumping a `pyproject.toml` version, regenerating a manifest, updating a CHANGELOG header, notifying a downstream system) point CM at hook scripts via fields in the project's `project.cfg`. The release script reads those fields, resolves them to absolute paths, and runs the hooks at fixed points in the lifecycle. Projects that declare no hooks behave exactly as before — no regression, no opt-in flag.

### Hook discovery

Hook script locations are declared in `project.cfg` under the `[hooks]` section. The hook scripts themselves typically live inside the project's own dev tree, version-controlled alongside the project's own code. The kanban executes whatever path the project points at; it does not host the script.

Three optional keys in `project.cfg [hooks]` declare hook paths, one per phase:

| Key | Phase |
|---|---|
| `cm_release_pre_squash_hook` | pre-squash |
| `cm_release_pre_tag_hook` | pre-tag |
| `cm_release_post_tag_hook` | post-tag |

All three keys are optional. A key that is absent or empty means no hook runs for that phase (no error, no warning).

**Path resolution.** A value beginning with `/` is treated as an absolute path and used as-is. Any other value is resolved relative to `dev_tree_path` from the same `project.cfg [project]` section. The resolved path is what CM executes.

CM does not check that the resolved file exists at discovery time. If the file is missing when the phase fires, the script logs `hook <name>: not present, skipping` and proceeds. If the file exists but is not executable, the script logs a warning and skips.

#### Example

A `project.cfg` declaring all three hooks, mixing absolute and relative paths:

```ini
[project]
project_name = pgai-video-generator
dev_tree_path = /home/rocky/develop/pgai-video-generator

[hooks]
cm_release_pre_squash_hook = scripts/bump-pyproject-version.sh
cm_release_pre_tag_hook = scripts/regenerate-manifest.sh
cm_release_post_tag_hook = /opt/pgai-shared/notify-release.sh
```

The release script resolves these to:

| Key | Raw value | Resolved path |
|---|---|---|
| `cm_release_pre_squash_hook` | `scripts/bump-pyproject-version.sh` | `/home/rocky/develop/pgai-video-generator/scripts/bump-pyproject-version.sh` |
| `cm_release_pre_tag_hook` | `scripts/regenerate-manifest.sh` | `/home/rocky/develop/pgai-video-generator/scripts/regenerate-manifest.sh` |
| `cm_release_post_tag_hook` | `/opt/pgai-shared/notify-release.sh` | `/opt/pgai-shared/notify-release.sh` |

The first two are relative and resolve against `dev_tree_path`. The third begins with `/` and is used unchanged.

#### Legacy fallback (backward compatibility)

For backward compatibility, CM also recognizes hook scripts at fixed legacy paths under the kanban tree:

```
$KANBAN_ROOT/projects/<name>/hooks/
├── cm-release-pre-squash.sh
├── cm-release-pre-tag.sh
└── cm-release-post-tag.sh
```

The legacy path is consulted **only** when the corresponding `project.cfg [hooks]` key is absent or empty. When a legacy file is found and used, CM logs:

```
Legacy hook path detected; declare in project.cfg [hooks].
```

This warning is the operator's cue to migrate the hook to a `project.cfg`-declared path. The legacy mechanism still works; the warning is not an error.

**Precedence.** When the `project.cfg [hooks]` key for a phase is set to a non-empty value, that path wins — the legacy path is not consulted, and no warning is emitted, even if a legacy file also exists at `$KANBAN_ROOT/projects/<name>/hooks/cm-release-<phase>.sh`. The explicit declaration is authoritative.

### Hook environment

Each hook runs as a subprocess with these six environment variables set:

| Variable | Value |
|---|---|
| `PGAI_TARGET_VERSION` | Version being released, e.g. `v0.0.2` |
| `PGAI_PROJECT_NAME` | Project name, e.g. `pgai-video-generator` |
| `PGAI_PROJECT_ROOT` | Project's kanban directory, e.g. `$KANBAN_ROOT/projects/<name>` |
| `PGAI_DEV_TREE_PATH` | Project's source repo dev tree (where the working clone lives) |
| `PGAI_RC_BRANCH` | RC branch name, e.g. `rc/v0.0.2` |
| `PGAI_KANBAN_ROOT` | Kanban root path (`$PGAI_AGENT_KANBAN_ROOT_PATH`) |

Hooks run from `cwd=$PGAI_DEV_TREE_PATH` so that bare `git` commands target the project's source repo by default.

Hook stdout and stderr are captured to the CM task's log file with each line prefixed `[hook <name>]` for easy filtering.

### Phase semantics

The three hooks run at three fixed points in the release lifecycle. Each phase exists because the work it enables can only be done meaningfully at that point.

**`cm-release-pre-squash.sh`** — runs on the RC branch after RC verification completes, before the squash into `develop`. Use for: bumping version files (`pyproject.toml`, `package.json`, `VERSION`), regenerating manifests, updating CHANGELOG entries. Any commits this hook makes are on the RC branch and become part of what gets squashed into `develop`.

**`cm-release-pre-tag.sh`** — runs after `develop` has been squashed, after `main` has been squashed, and before `git tag` is created. Use for: final consistency checks across the merged state, generating release artifacts that depend on `main` being current, updating documentation that references the new version.

**`cm-release-post-tag.sh`** — runs after the tag is created locally, before the best-effort push of `main` and tags at Step 18. Use for: external notifications, asset uploads, downstream triggers. The local release is already complete when this hook runs.

### Failure semantics

Failure semantics differ by phase. Pre-squash and pre-tag are blocking phases — the release has not yet reached a published state, so the script can refuse to continue. Post-tag is non-blocking — the tag already exists locally and failure cannot roll the release back.

| Hook | Failure behavior |
|---|---|
| `cm-release-pre-squash.sh` | Non-zero exit BLOCKS the release. Hook's stderr is included in the block reason. |
| `cm-release-pre-tag.sh` | Non-zero exit BLOCKS the release. Hook's stderr is included in the block reason. |
| `cm-release-post-tag.sh` | Non-zero exit is a LOGGED WARNING ONLY. The release continues; the tag stands. |

### Hook contract

Hook authors should observe these rules:

- Exit 0 on success; non-zero on failure.
- Be idempotent. CM may invoke the same hook again on operator retry; re-running must not fail when the work is already done. A version-bumper, for example, should check the current value and only edit if it differs from the target.
- Be fast. Target sub-30-second runtime. Heavy work belongs elsewhere.
- Stay inside the project. Hooks must not modify state outside the project's dev tree or kanban directory; no global side effects.

When a hook violates the contract — for example, mutates a sibling project's tree, or hangs for several minutes — operators should treat it as a project defect and file a bug against the project, not against the framework.

## Bug Queue

> **Note:** Bugs are filed as individual report files in `projects/<name>/bugs/` and flow through the Bug-Reporting Flow pipeline described below. The `bug_backlog.md` file serves as a tracking index — see the next section.

The bug queue tracks bugs discovered during verification or at any other point in the lifecycle. It lives at:

```
team/tasks/queues/bug_backlog.md
```

### Filing bugs

Any agent may file a bug by appending a line to `bug_backlog.md`. Each entry uses the standard queue marker format:

```
[ ] TASK-ID — short description of the bug
```

The TESTER agent files bugs when verification reveals implementation defects (as distinct from gaps, which are incomplete requirements). Other agents may file bugs if they encounter broken behavior during their work.

### Marking bugs resolved

When an agent completes a bug-fix task, it marks its own entry in `bug_backlog.md` as done by changing `[ ]` to `[x]`:

```
[x] TASK-ID — short description of the bug
```

This is a best-effort cleanup step. If the entry is not found, the agent proceeds without error. Agents must not remove lines from `bug_backlog.md` -- they only change the marker.

### Who reads the bug queue

- **PM agent**: reads `bug_backlog.md` as the first step of every wake cycle (pre-flight). Open bugs feed into the 4-path decision tree.
- **CM agent**: reads `bug_backlog.md` before invoking the release script. Open bugs inform the ship-or-wait judgment.
- **Coder and Writer agents**: read `bug_backlog.md` only at task completion to perform self-cleanup on their own bug-fix entries.

## Bug-Reporting Flow

Bugs flow through a structured pipeline that separates filing, triage, decomposition, and resolution into distinct stages — each owned by the role whose swim lane covers that activity.

### 1. Filing

TESTER (or human operators) files bug reports as individual Markdown files in `projects/<name>/bugs/`. Each report uses the template at `projects/<name>/bugs/BUG-TEMPLATE.md`.

File naming follows a monotonic 4-padded numbering convention:

```
BUG-NNNN-<3-word-slug>.md
```

Examples:

```
BUG-0001-duplicate-pm-tickets.md
BUG-0002-missing-semver-guard.md
BUG-0003-cron-halt-ignored.md
```

The filer creates the bug report file and stops. Filing is the only action the filer takes — it does not write to `bug_backlog.md`, create fix tickets, or attempt repairs.

### 2. Bug queue tracking

`team/tasks/queues/bug_backlog.md` serves as a tracking index for bug reports. It contains pointers to bug report files with two possible states:

- `[ ]` — open: filed but not yet bundled into a priority requirements doc.
- `[x]` — bundled: PM has folded this bug into a priority requirements doc for decomposition.

PM is the only writer to this queue. Other roles do not add entries to or modify `bug_backlog.md`.

The cache markers are a **derived view**, not the source of truth. The authoritative signal for whether a bug is eligible for re-bundling is the `## Status` field inside the bug report file itself (see "Status is authoritative; backlog markers are derived" above). A bug marked `[x]` in this index is not protected from re-evaluation — if its `## Status` is reset to `open`, the next discovery iteration will pick it up and re-bundle it. This is the supported recovery path for bug reports that were edited or fleshed out after a prior bundling.

### 3. PM scanning

During the autonomous scan, PM scans `projects/<name>/bugs/` for unhandled bugs — those present as files but not yet marked `[x]` in `bug_backlog.md`. If unhandled bugs exist, PM:

1. Bundles them into a single priority requirements doc in `requirements/priority/`.
2. Marks each bundled bug as `[x]` in `bug_backlog.md`.
3. Continues to the priority queue check in the autonomous scan (the newly created priority doc will be picked up in the same or next cycle).

### 4. Decomposition

The priority requirements doc flows through normal PM decomposition (Path B or C in the PM decision tree) to produce fix tickets. Each ticket references the originating bug report file for traceability.

### 5. Fix and verification

Fix tickets are assigned to CODER or WRITER based on the nature of the fix, verified by TESTER, and shipped by CM — the standard release lifecycle. No special handling is needed once the fix ticket exists.

## Manual Hotfixes During In-Flight RC

This section states the project's policy on direct commits to an in-flight RC branch outside the kanban pipeline. The canonical recovery path runs through the agents; manual hotfixes are **sanctioned-with-attribution** as an exception, never as routine practice.

### Canonical halt-recovery path

When a bug is filed against an in-flight RC (typically by TESTER, triggering a halt), the canonical sequence is:

1. **TESTER files the bug** in `projects/<name>/bugs/` and, in the autonomous Path C case, writes a priority requirements document targeting the next patch.
2. **PM bundles the bug** into a priority requirements document at next wake and marks it `[x]` in `bug_backlog.md`.
3. **PM decomposes** that document into one or more fix tickets via the PM 3-Path Decision Tree.
4. **CODER (or WRITER) implements the fix** on a feature branch, commits, merges `--no-ff` into the RC branch, and deletes the feature branch.
5. **TESTER re-verifies** the RC against the original acceptance criteria plus the new fix.
6. **CM ships** under the ship-by-default policy.

This path produces a complete audit trail: bug file, requirements doc, task folder, feature branch, merge commit, verification report.

### Policy on manual hotfix commits

Manual hotfix commits to an in-flight RC branch — commits not produced by a CODER or WRITER feature-branch merge — are **sanctioned-with-attribution**. They are permitted only when the operator judges the canonical path unworkable for this specific fix (for example, the wake scripts themselves are broken in a way that prevents agents from running, or the fix is a single-line correction the operator can verify by inspection faster than a CODER cycle can complete).

A manual hotfix is never the preferred path. The default is always the canonical recovery path above. Choosing the hotfix path is an explicit operator decision, not a fallback an agent may take.

### Attribution requirement

When a manual hotfix is taken, the closing bug file must record the attribution in its `## Resolved By` field in the form:

```
## Resolved By
manual hotfix by operator on <commit-sha>
```

A bug closed via the canonical path records the CODER task ID instead (e.g. `CODER-YYYYMMDD-NNN-fix-bug-XXXX`). These are the only two accepted forms. See `projects/<name>/bugs/BUG-TEMPLATE.md` for the field definition.

### Enforcement

The PM bug scanner (`team/pm-agent/lib/bug_scanner.py`) refuses to treat a `## Status: done` bug as closed unless `## Resolved By` is populated. A done bug with the field empty or absent is treated as open (`[ ]` in `bug_backlog.md`) and emits a UserWarning. This makes manual hotfixes visible in the audit trail by forcing the operator to record them at close time.

## TESTER Gap Loop (Rework Cycle) — superseded

**Note:** TESTER no longer runs a rework loop or blocks on found gaps. TESTER files gaps via Path C (BUG or PRIORITY) and continues to `DONE` with a `SHIP-WITH-CONCERNS` or `SHIP-WITH-SERIOUS-CONCERNS` recommendation. CM applies the ship-policy decision matrix (see "CM Ship-By-Default Policy" above). The historical rework-loop mechanism described below is retained for context on legacy task folders that may carry a `## Rework Cycle` counter; new TESTER tasks do not use it.

### Historical behavior

When the TESTER agent verified an RC and found gaps (requirements not fully implemented), it triggered a rework cycle rather than simply blocking.

1. TESTER wrote `artifacts/gaps.md` listing each gap with: criterion, expected behavior, actual behavior, and severity.
2. TESTER checked the `## Rework Cycle` counter in its own `status.md` (default: 0).
3. **If the counter was less than 3**, TESTER opened a PM rework ticket and blocked:
   - Created a PM rework ticket ID in the format `CLAUDE-PM-<YYYYMMDD>-<seq>-rework-<version>-gaps`.
   - Appended the ticket to `pm_backlog.md`.
   - Added the PM rework ticket as a prerequisite on the CM-release task.
   - Set its own task to BLOCKED with `Blocked By Agent: PM`.
   - Incremented the `## Rework Cycle` counter by 1.
4. **If the counter reached 3 or higher**, TESTER escalated to the human.

Critical bugs (data loss, broken invariants, incorrect dispatch) bypassed the rework loop entirely. TESTER set BLOCKED with `Needs Human: yes` immediately.

Under v0.25.0, none of the above blocking behavior applies. Gaps and critical findings both flow through Path C; severity influences the `Recommendation`, `Systemic Risk`, and `Fix Effort` fields that CM reads to decide ship-versus-HALT.

## PM 3-Path Decision Tree

After pre-flight checks pass (see PM Wake Order), the PM agent evaluates a 3-path decision tree. PM does not amend active RCs.

### Decision paths

The PM evaluates these paths in order and takes the first match:

| Path | Condition | Action |
|------|-----------|--------|
| **Path B** | Active RC is `none`, active requirements document found (from priority or regular queue) | Standard decomposition into a new release |
| **Path C** | Active RC is `none`, active requirements document found in `projects/<name>/requirements/priority/`, authored by TESTER, last release is not `v0.0.0` | Autonomous patch release (see below) |
| **Path D** | None of the above (Active RC is `none`, no suitable requirements document) | No-op -- nothing to decompose |

### Path C semantics

Path C enables autonomous patch releases driven by priority requirements that TESTER wrote.

When Path C triggers:

1. Read the priority requirements document (TESTER already wrote it -- PM does not re-author it).
2. Compute the patch version from `## Target Version` in the document, or if absent, increment the value returned by `pp_last_released_version` by one patch segment (e.g., v0.6.0 becomes v0.6.1).
3. Decompose the requirements document using the same standards as Path B.

### Path C preconditions

All of the following must be true:

- `## Active RC` in the project's `release-state.md` is `none`
- An active requirements document was found in `projects/<name>/requirements/priority/`
- The document was authored by TESTER (PM does not author priority requirements)
- `pp_last_released_version` returns a value other than `v0.0.0` (a release has shipped at some point)

### Bug-first pre-flight

Regardless of which path is selected, PM always reads `bug_backlog.md` as Step 1 of its pre-flight checks. This ensures bug state is current for every PM decision, even though Path C now triggers from priority requirements documents rather than from `bug_backlog.md` directly.

## Priority Queue Mechanics

The system supports a priority queue for requirements that need expedited processing. Priority requirements take precedence over regular requirements in the PM wake order.

### Directory structure

Priority requirements live in:

```
projects/<name>/requirements/priority/
```

Regular requirements live in:

```
projects/<name>/requirements/
```

### Picking rule

When the autonomous scan in the wake script (`wake-batch.sh`, which dispatches to the active provider's `scripts/wake/<provider>.sh`) selects requirements to process, it applies this picking rule to each queue:

1. List all `.md` files in the directory (non-recursive).
2. Filter: keep only files whose filename contains a parseable version (`vX.Y.Z`). Files without a version are silently skipped.
3. Filter: keep only files where the parsed version is greater than the value returned by `pp_last_released_version` (using semver-aware comparison via `team/scripts/lib/semver.sh`).
4. Sort by parsed semver version ascending (lowest version first). Tiebreak by filename ascending (lexicographic).
5. For the **priority queue**: take all files at the lowest eligible version (they are merged as combined PM inputs). For the **regular queue**: take the single lexically-first file at the lowest eligible version.

### Stale document handling

A priority or regular requirements document is considered stale when its `## Target Version` is less than or equal to the value returned by `pp_last_released_version`. Stale documents are skipped during the picking step. They are not deleted -- they remain in the directory for audit purposes but are no longer eligible for processing.

### Who writes priority requirements

TESTER agents write priority requirements documents and place them in `projects/<name>/requirements/priority/`. PM does not author priority requirements -- it only decomposes them. This separation ensures that bugs and gaps discovered during verification are tracked as first-class requirements with their own acceptance criteria.

## PM Wake Order

When the PM agent wakes, it selects the active requirements document using this order:

1. **Priority queue first:** Scan `projects/<name>/requirements/priority/` using the picking rule above. If a valid (non-stale) document is found, use it.
2. **Regular queue second:** If no priority document was selected, scan `projects/<name>/requirements/` (non-recursive) using the same picking rule. If a valid document is found, use it.
3. **No active requirements:** If neither queue has a suitable document, proceed to Path D (no-op).

This order ensures that priority work (bug fixes, gap remediation) is always processed before new feature requirements.

### Pre-flight steps

Before evaluating the wake order, PM runs pre-flight checks:

1. Read `bug_backlog.md` passively for awareness.
2. Read the project's `release-state.md` to get `## Active RC`.
3. Resolve `Last Released` by calling `pp_last_released_version` (highest semver tag merged into the dev tree's `origin/main`; falls back to `v0.0.0` for fresh projects).
4. If `## Active RC` is not `none`, stop immediately and set the task to BLOCKED (Blocked By Agent: cm). PM must not decompose new work while an RC is open.

Only after all pre-flight steps pass does PM proceed to the wake order and the 3-path decision tree (Paths B, C, D).

## RC Immutability

Once a Release Candidate (RC) branch is created and active (`## Active RC` in the project's `release-state.md` is not `none`), the RC is immutable from PM's perspective.

### What immutability means

- PM cannot decompose new requirements while an RC is active. If PM wakes and finds `Active RC != none`, it blocks immediately.
- No new feature work is added to an active RC after its initial task plan is created.
- The only way to influence an active RC is indirectly: TESTER discovers gaps during verification and writes priority requirements documents for the NEXT release cycle.

### Why RCs are immutable

Immutability prevents scope creep during the release process. Without it, new requirements could be injected mid-release, causing unbounded rework cycles and delaying the release indefinitely.

### How gaps feed back

When TESTER finds gaps in an active RC, it writes a priority requirements document to `projects/<name>/requirements/priority/` targeting the next patch version. This document is picked up by PM on its next wake cycle after the current RC closes. The gap becomes a first-class requirement in the next release, not a mid-release amendment.

## CM Ship-By-Default Policy

The CM (Change Manager) agent operates under a ship-by-default policy. Releases proceed unless one of the eight enumerated HALT triggers fires. Known bugs, gaps, and imperfect work are never reasons to refuse the release on their own — they flow into the release notes and into the next iteration's bundle.

### Default behavior

CM reads TESTER's report and applies the ship-policy decision matrix documented in `team/roles/CM.md`. The matrix dispatches on three TESTER report fields:

- **State** — `DONE` (verification ran to completion) or `BLOCKED` (verification could not complete).
- **Systemic Risk** — `low`, `medium`, or `high` (max across findings).
- **Recommendation** — `PASS`, `SHIP-WITH-CONCERNS`, or `SHIP-WITH-SERIOUS-CONCERNS`.

There is no `BLOCK` recommendation. TESTER does not block the chain on found bugs. Bugs and gaps are filed via Path C and continue to the next iteration; TESTER's state remains `DONE`.

### Ship-policy summary

| TESTER state | systemic_risk | recommendation | fix_effort | CM action | Release notes Status |
|---|---|---|---|---|---|
| BLOCKED | (any) | (any) | (any) | HALT | — |
| DONE | high | (any) | (any) | HALT | — |
| DONE | low/medium | PASS | (any) | Ship | `FUNCTIONAL` |
| DONE | low/medium | SHIP-WITH-CONCERNS | (any) | Ship; list filed bugs | `KNOWN-BUGS` |
| DONE | low/medium | SHIP-WITH-SERIOUS-CONCERNS | all small/medium | Ship with NON-FUNCTIONAL warning | `NON-FUNCTIONAL` |
| DONE | low/medium | SHIP-WITH-SERIOUS-CONCERNS | any large | HALT | — |

`team/roles/CM.md` is the authoritative version; this is a summary for operator orientation.

### Filed bugs in release notes

When CM ships a `KNOWN-BUGS` or `NON-FUNCTIONAL` release, filed bugs from the TESTER report are listed in the release notes for visibility. The chain continues; the bugs enter the next iteration's bundle.

### Rationale

Ship-by-default keeps the release pipeline moving. Halting on found bugs adds operator latency without speeding up the fix — the bug enters the priority queue either way. The only halts that justify stopping the chain are the eight enumerated triggers in CM's HALT Authority section, which indicate the chain itself or the release mechanism is unhealthy.

## Opt-In Human Approval Gate

The release pipeline supports an opt-in human approval gate between the TESTER verification step and the CM release step.

### How it works

Requirements documents and brief templates include a `## Human Approval Required` field with two valid values:

- **`auto`** (default) -- No HUMAN-APPROVE task is injected. The release proceeds automatically after TESTER verification passes. CM-release depends only on the TESTER task and feature tasks.
- **`required`** -- A HUMAN-APPROVE gate task is injected into the plan. A human must manually advance this task to DONE before the CM release task can proceed.

### Task ordering

When `Human Approval Required` is set to `required`:

```
CM-open -> feature tasks -> TESTER -> HUMAN-APPROVE -> CM-release
```

When set to `auto` (or omitted):

```
CM-open -> feature tasks -> TESTER -> CM-release
```

### Default behavior

If the field is absent or blank, the system defaults to `auto`. Invalid values produce a warning and fall back to `auto`. This preserves backward compatibility with plans that predate this feature.

### When to use `required`

Use `required` for releases where a human must inspect the RC before it ships -- for example, releases with user-facing changes, security-sensitive updates, or breaking changes. Use `auto` for routine patch releases and internal tooling updates where the TESTER verification is sufficient.

## Test Scripts

The repository ships two test scripts that TESTER agents and humans can run to verify the system:

### run-unit-tests.sh

```
scripts/run-unit-tests.sh [--verbose]
```

Runs the pytest unit suite at `team/tests/unit/`. Tests pure-Python logic: queue parsing, materializer output, status file parsing.

Exit codes:
- `0` — all tests passed
- `1` — one or more tests failed
- `2` — pytest not installed (see Installation below)

### run-integration-tests.sh

```
scripts/run-integration-tests.sh [--verbose]
```

Runs the pytest integration suite at `team/tests/integration/`. Tests wake-script behavior end-to-end against a temporary kanban tree.

Exit codes: same as unit tests (0/1/2).

### pytest dependency

Both scripts require pytest. Install it with:

```
pip install pytest --break-system-packages
```

Verify the install: `python3 -m pytest --version`

### When to run tests

- TESTER agents: run both scripts as part of every RC verification cycle (Steps 11–12 in the TESTER role instructions). When evaluating new or modified tests for quality, cross-reference the **Test Authoring Guidelines** section below — it is the canonical reference for the five anti-patterns the suite is being held to.
- CODER agents: run unit tests after modifying `pm_materialize.py`, `pm_status.py`, or any Python in `team/`. When authoring new tests, conform to the **Test Authoring Guidelines** section below.
- Humans: run both before merging an RC to main.

## Naming: describe behavior, not scaffolding or provenance

This principle governs every name an agent authors — functions, scripts, variables, classes, and test names alike. It applies to all roles that author artifacts (CODER names production code; TESTER and any test author name tests) and to every project the framework manages, not just this one.

**Name a thing for what it does or represents, not for the internal scaffolding or the history that produced it.** A name should read clean to someone encountering the code for the first time, who never saw the design discussion, the build sequence, or the incident that prompted the work.

Two kinds of bad names this rules out:

- **Scaffolding names** encode an internal sequence, phase, branch, or code-path label instead of meaning: `gate5_branch3_executions`, `phase2_handler`, `do_step4`, `pathB_check`. These are notes-to-self from the moment of writing; they tell a later reader nothing about what the unit is *for*.
- **Provenance names** encode why the work happened — an issue-tracker ID, a version, a release gate: `fix_bug_0382`, `v0_49_0_patch`, `test_gate3_pathB_works`. When the provenance is gone — history flattened, version long past, the labels refactored away — the name is a dangling reference to something no longer in the tree.

```
# BAD — scaffolding/provenance: tells a reader nothing about behavior
gate5_branch3_executions()
def fix_bug_0382(): ...
phase2_handler.sh

# BETTER — names the behavior; readable in isolation
get_active_task_by_project()
def apply_branch_prefix_to_checkout(): ...
sync-queue-markers.sh
```

The rule is the same whether the artifact is a production function or a test: the artifact carries the meaning; git history carries the "why." Issue IDs, versions, and incident references belong in commit messages and git history — not baked into the names of the things that outlive them. (For the test-name application of this principle, see Anti-pattern 6 in **Test Authoring Guidelines** below. For the production-code application, see the naming rule in `roles/CODER.md`.)

**When acceptable:** The same rule extends to docstrings, comments, log lines, and output strings: they describe BEHAVIOR, never process history. No internal bug ID (`BUG-NNNN`), task ID, RC, or framework-version citation appears in any code artifact — git history and the kanban's bug/task state hold the "why"; the artifact holds the meaning. Three sanctioned exceptions: (1) format and usage EXAMPLES, where a bug-shaped or version-shaped VALUE illustrates an interface (`--key BUG-0042`, `e.g. BUG-0001-foo.md`, `test_project_version('v1.2.3')`); (2) a skip annotation citing an OPEN follow-up bug (a live cross-reference, enforced by the skip-cites-real-bug gate, and removed when the bug closes); (3) references to EXTERNAL constraints (an upstream project's issue, a CVE, an RFC) — those document behavior the code must honor, not internal history.

## Test Authoring Guidelines

Tests in this kanban must encode INTENT explicitly. A passing test should pass because the production code satisfies the specification the test captures — not because the test happened to match incidental implementation details, naming conventions, or shared state.

This section is the canonical reference for what TESTER cross-references when judging test quality, and what CODER, WRITER, and any other agent authoring tests must follow. New tests that violate these guidelines without a documented exception are gaps; TESTER should flag them as such.

### Why this section exists

Tests that couple to naming conventions rather than explicit specifications fail spuriously when new code legitimately introduces a symbol that matches the pattern but does not need to satisfy the assertion. A test may pass for months, then fail not because behavior was violated but because the scan scope was never bounded. Each guideline below maps back to a real failure the operator absorbed, not a theoretical concern.

### Anti-pattern 1 — Pattern-scan universal invariants

A test enumerates all variables, files, or symbols matching a naming pattern (regex, glob, prefix) and asserts each conforms to behavior X. The assertion is coupled to the naming convention rather than to an explicit list of subjects. When production code legitimately introduces a new symbol that matches the pattern but does not need to satisfy X, the test fails — not because behavior X was violated, but because the scan scope was never bounded.

```python
# BAD — assertion couples to naming convention, not semantics
for cmd in find_variables_matching(r"VIS_.*_CMD"):
    assert "--all-projects" in cmd.contents
```

```python
# BETTER — explicit allowlist that documents intent
PER_COLUMN_CMDS = ["VIS_BUGS_CMD", "VIS_PRIORITIES_CMD", "VIS_REQUIREMENTS_CMD",
                   "VIS_PM_CMD", "VIS_CODER_CMD", "VIS_WRITER_CMD",
                   "VIS_TESTER_CMD", "VIS_CM_CMD"]
for cmd in PER_COLUMN_CMDS:
    assert "--all-projects" in load_variable(cmd)
```

**When acceptable:** Only when the test genuinely intends to cover every current and future symbol matching the pattern (e.g., "no test file may import from `team/internal/`"). Document the intent inline with a comment beginning `# Intentional pattern-scan:` that names the invariant being enforced.

### Anti-pattern 2 — Environment-coupled fixtures

A test assumes a hardcoded working directory, environment variable value, or temp path that may differ between operator machines, CI runners, or framework configurations. The test passes on the author's machine and fails — or worse, pollutes shared state — elsewhere.

```bash
# BAD — assumes a hardcoded /tmp path; ignores framework temp-dir convention
TEST_DIR=/tmp/some_specific_path
mkdir -p "$TEST_DIR" && ...
```

```bash
# BETTER — respect PGAI_AGENT_KANBAN_TEMP_DIR, use mktemp, trap cleanup
TEST_DIR="$(mktemp -d -p "${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp}" test.XXXXXX)"
trap "rm -rf '$TEST_DIR'" EXIT
```

In Python, the equivalent is `pathlib.Path(os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR") or "/tmp")` or pytest's `tmp_path` fixture.

**When acceptable:** Almost never. A test that genuinely needs a fixed system path (e.g., verifying a script's behavior when `/etc/something` exists) must check for that path's existence and skip cleanly when absent, with a comment beginning `# Path-dependent test:` explaining what the test would lose by being made portable.

### Anti-pattern 3 — Order-dependent state

`test_a` populates shared state that `test_b` reads. The tests pass in declaration order; reordering or running `test_b` in isolation breaks the suite in non-obvious ways. The dependency is invisible to anyone reading either test alone.

```python
# BAD — test_b silently depends on test_a having run first
def test_a():
    create_global_thing()

def test_b():
    assert global_thing_exists()  # passes only if test_a ran first
```

```python
# BETTER — each test self-contained via a fixture with teardown
@pytest.fixture
def global_thing():
    thing = create_global_thing()
    yield thing
    thing.cleanup()

def test_a(global_thing):
    assert global_thing.is_valid()

def test_b(global_thing):
    assert global_thing.is_valid()
```

Module-scoped (`scope="module"`) fixtures are a milder form of the same anti-pattern: shared state across tests in the module that no individual test makes explicit. Prefer function-scoped fixtures unless the construction cost is genuinely prohibitive.

**When acceptable:** Multi-cycle integration tests that simulate a sequence of pipeline iterations within a single test (e.g., "simulate three RC cycles back-to-back") may intentionally mutate state between steps. The sequence must live entirely inside one test function and the test docstring must document the step order explicitly. Cross-test ordering dependencies are not acceptable.

### Anti-pattern 4 — Production-coupling tests

A test passes because production code happens to contain a specific string, regex, or implementation construct — not because the assertion captures the behavioral spec the test is supposed to verify. The symptom is diagnostic: when the test fails, the failure message ("string `--sort=-version:refname` missing from script") does not tell the operator what behavior was broken, only that the implementation was changed.

```python
# BAD — static source assertion couples test to implementation choice
def test_script_uses_version_sort(self):
    assert "--sort=-version:refname" in self._text
```

```python
# BETTER — behavioral assertion captures the spec intent
def test_recent_tags_lists_newest_first(self, tmp_path):
    """dashboard-git-recent-tags.sh lists tags in newest-first semver order."""
    repo = _make_git_repo(tmp_path, tags=["v0.1.0", "v0.2.0", "v0.10.0", "v0.9.0"])
    result = subprocess.run([str(_GIT_RECENT_TAGS)],
                            env={**os.environ, "PGAI_DEV_TREE_PATH": str(repo)},
                            capture_output=True, text=True)
    assert result.returncode == 0
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    assert lines.index("v0.10.0") < lines.index("v0.9.0"), \
        "Tags must be newest-first in semver order, not lexicographic order"
```

The failure message of the BETTER form tells the reader what was supposed to be true and what is actually true. The BAD form tells the reader only that a string is missing.

**When acceptable:** Static source assertions are acceptable as cheap structural guards (e.g., "this file does not import `os.system`") when paired with a companion behavioral test, or when there is no runtime invocation path to exercise (e.g., asserting a config file template contains a required key). Document the intent inline with a comment beginning `# Static guard:` that names the invariant.

### Anti-pattern 5 — Side-effect-leaking tests

A test creates files, opens file descriptors, forks subprocesses, or mutates environment variables without proper cleanup. Subsequent runs read stale state; parallel runs collide; failures in one test mask or amplify failures in others.

```bash
# BAD — temp files created without cleanup; survive between runs
mkdir -p /tmp/pgai_kanban_tmp
cmd 2>/tmp/pgai_kanban_tmp/stderr.txt
# Stale stderr from a prior failure can mask a current failure.
```

```bash
# BETTER — mktemp + trap guarantees cleanup even on test failure
PGAI_TEMP="${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}"
WORK_DIR="$(mktemp -d -p "$PGAI_TEMP" test.XXXXXX)"
trap "rm -rf '$WORK_DIR'" EXIT
cmd 2>"$WORK_DIR/stderr.txt"
```

```python
# BAD — bare open() leaks fd on exception
fd = open(lock_file, "w")
fcntl.flock(fd, fcntl.LOCK_EX)
do_thing()
fd.close()
```

```python
# BETTER — context manager guarantees close on every path
with open(lock_file, "w") as fd:
    fcntl.flock(fd, fcntl.LOCK_EX)
    do_thing()
```

For Python tests, prefer pytest's `tmp_path` fixture, context managers (`with open(...)`), and `monkeypatch` for environment changes (which auto-reverts on test teardown). For bash tests, use `mktemp -d` plus a `trap "rm -rf ..." EXIT`.

**When acceptable:** Tests that legitimately need to leave artifacts behind for inspection (debugging aids, integration captures) must opt in explicitly via a flag or environment variable, default to cleanup, and document the opt-in with a comment beginning `# Cleanup opt-out:`.

### Anti-pattern 6 — Provenance and scaffolding in test names

This is the test-name application of the cross-role naming principle — see **Naming: describe behavior, not scaffolding or provenance** above. A test named after *why it was written* (a bug number, a version, a release gate) or after *internal scaffolding* (a phase label, a code-path tag) records which incident prompted the test, not what the system is supposed to do. When the provenance is squashed away, the name is a dangling reference and a cold reader cannot tell what the test guards without reading its body. It is also the test-name form of Anti-pattern 4: the name couples to the implementation's history instead of capturing the behavioral spec.

```python
# BAD — names encode provenance/scaffolding, not behavior
def test_v0_49_0_bug_fix(self): ...
def test_gate3_pathB_works(self): ...
def test_bug0382_regression(self): ...
```

```python
# BETTER — names describe the behavior under test, readable in isolation
def test_agent_marks_task_and_queue_done_after_task_completes(self): ...
def test_wake_scripts_check_for_flock_block(self): ...
def test_unwind_rc_applies_branch_prefix_to_develop_checkout(self): ...
```

The same rule applies to test *file* names: name the file after the unit or behavior under test (`test_unwind_rc.py`), never after the bug that motivated it (`test_bug0382.py`). A test that exists to lock in a former regression is welcome — name it after the behavior that regressed, and let git history hold the "why."

**When acceptable:** Never — in names, docstrings, OR comments. A test's docstring describes the behavior it locks in, not the incident that motivated it; `BUG-NNNN` and `vX.Y.Z` citations are forbidden in test names, file names, docstrings, and comments alike. The single exception is a skip annotation citing an OPEN follow-up bug (see the skip-cites-real-bug gate below) — that is a live, verified cross-reference which is deleted when the bug closes, not historical context. Bug-shaped and version-shaped VALUES used as test DATA remain fine (`test_project_version('v1.2.3')`, a fixture file named `BUG-0001-foo.md` exercising the intake format).

### Skipped tests must cite a REAL, existing follow-up bug

The never-block test-handling rule is: when updating a specific test is non-obvious, mark the test skipped AND file a follow-up bug rather than blocking the release. The second half is not optional. A `skip()` / `@pytest.mark.skip` / `# SKIP:` annotation that cites a bug MUST reference a real `BUG-NNNN` whose file exists in the project's `bugs/` directory. A skip citing a placeholder ID (`BUG-SKIP-*` or any non-`BUG-NNNN` form), a non-existent bug, or no bug at all is a verification FAILURE — not an acceptable never-block deferral.

```python
# BAD — placeholder ID; no such bug was ever filed
@pytest.mark.skip(reason="compute_layout floor case — BUG-SKIP-compute-layout-floor-n1")
def test_compute_layout_floor(self): ...
```

```python
# BETTER — cites a real, filed bug whose file exists in bugs/
@pytest.mark.skip(reason="compute_layout floor case deferred — see BUG-NNNN")
def test_compute_layout_floor(self): ...
```

This is the recurring false-completion pattern: an agent claims it did the verifiable-but-unverified half of a two-step instruction. The "skip AND file a bug" instruction — designed to prevent blocking — becomes a new place to fake completion when the "file a bug" half is faked with a placeholder ID and an in-test comment claiming a bug "was filed." The cure is the same grep-gate completeness medicine applied to the BUG-0362 `/tmp` and lint work: do not trust the in-test comment that says a bug was filed — grep for the bug ID and confirm the `BUG-NNNN-*.md` file exists in `bugs/`. The check is the source of truth, not the comment.

The gate is enforced mechanically by `team/scripts/lint_skip_bug_gate.sh`, which the gated unit and integration runners invoke before pytest. TESTER also runs the same check as a standing step during RC verification — see TESTER.md Step 6.13 — so the catch is routine, not a lucky one.

**When acceptable:** A skip that does NOT cite a bug because it is an infrastructure guard (e.g., `# Path-dependent test:` skipping cleanly when a required path is absent, or skipping when an optional dependency is not installed) is not a follow-up-bug skip and does not need a `BUG-NNNN`. The rule applies to skips that defer a test because fixing it is non-obvious — those are deferrals and must name the bug that tracks the deferred fix.

### Rule of thumb

If a test passes today because production code happens to contain a string, match a pattern, or sit at a path — rather than because production behavior satisfies a documented spec — the test is one legitimate change away from spurious failure. The cost of a spurious failure is not the five-line fix; it is the operator interruption, the waiver, and the erosion of autonomous-build discipline.

Encode intent. Bound scope. Clean up after yourself. Make the failure message tell the next operator what behavior was supposed to hold.

### CODER status header: `## Stale Literal Risks`

CODER's status.md may include a `## Stale Literal Risks` section. This section is the output of the `scripts/coder-stale-literal-check.sh` pre-flight check, which scans the task's production diff for literals (strings, integers) that also appear in test assertions. Each line names a test file, line number, and the literal at risk — for example, a test that asserts `version == "0.0.1"` when the RC bumped the version source of truth to `"0.0.2"`.

**The section is advisory, not blocking.** Its presence does not change the task's terminal state and does not require TESTER to do anything beyond noting the flagged tests during verification. Operators should read it as a forward-looking hint: "these tests are likely to fail in a future RC if the production value drifts further from the asserted literal." Acting on the hint — updating tests to a semantic check, filing a follow-on bug, or accepting the risk — is the operator's or PM's call.

**Empty or absent sections are acceptable.** A `## Stale Literal Risks` section reading `(none)` means the script ran and found nothing. An entirely absent section means either the task was authored before the v0.24.9 rollout completed (the section is OPTIONAL during rollout) or the production diff contained no literals worth checking. Neither form indicates a procedure violation. Do not file a bug against CODER for an absent section during the rollout window.

This section is distinct from CODER's `## Possible Stale Assertions` section (the manual Step 4b check authored by CODER's own judgment). The two complement each other: `## Possible Stale Assertions` captures what CODER's reasoning surfaced; `## Stale Literal Risks` captures what the mechanical diff-and-grep surfaced. Both may appear in the same status.md.

## Model Override Mechanism

Wake scripts support per-task model overrides via the `## Model Override` field in each task's `README.md`.

### Field format

```
## Model Override
<value>
```

Valid values:

| Value | Resolved model |
|---|---|
| `opus` | Current default Opus model |
| `sonnet` | Current default Sonnet model |
| `haiku` | Current default Haiku model |
| Full model ID (e.g. `claude-opus-4-6`) | Pinned to that exact version |
| `none` or empty | No override — system default |

### 3-Tier override precedence

Model selection uses a 3-tier precedence chain (highest wins):

| Tier | Source | Example |
|------|--------|---------|
| 1 | `## Model Override` field in the task README | `opus`, `claude-sonnet-4-6` |
| 2 | `PGAI_<AGENT>_MODEL` environment variable | `PGAI_CODER_MODEL=opus` |
| 3 | Subagent frontmatter default (no `--model` flag passed) | Agent uses its built-in default |

**Tier 1 — Task README override:** If the task's `README.md` contains a `## Model Override` field with a non-empty, non-`none` value, that value is used. The wake script passes it to `claude --model <value>`.

**Tier 2 — Environment variable:** If no task override is set, the wake script checks the environment variable `PGAI_<AGENT_UPPER>_MODEL`, where `<AGENT_UPPER>` is the uppercased role name from the task (e.g., `PGAI_CODER_MODEL`, `PGAI_TESTER_MODEL`, `PGAI_PM_MODEL`, `PGAI_WRITER_MODEL`). If set and non-empty, that value is used. Wake scripts populate this variable from `kanban.cfg` `[models.<active_provider>]` at startup — see "Model override variables" under "Configuration File System" for the per-provider schema.

**Tier 3 — Subagent default:** If neither tier 1 nor tier 2 provides a value, no `--model` flag is passed to the Claude CLI. The subagent runs with whatever model the CLI defaults to for that agent configuration.

### Model source logging

Wake scripts log which tier was used for every invocation:

- `model: <value> (from task override)` — Tier 1
- `model: <value> (from PGAI_<AGENT>_MODEL env)` — Tier 2
- `model: subagent default for <subagent>` — Tier 3

This logging makes it easy to audit which model ran each task.

### Recognized values and aliases

The wake script passes the resolved value directly to `claude --model <value>`. Aliases (`opus`, `sonnet`, `haiku`) are resolved by the Claude CLI to the current default model for that tier. Full model IDs (e.g. `claude-sonnet-4-6`) pin to an exact version.

### PM propagation

When the requirements document includes model override values in the `## Model Overrides` section, the PM materializer writes the override into each generated task's `## Model Override` field. This allows a project author to specify model preferences once in the requirements doc and have them flow automatically to all generated tasks.

## Configuration File System

The framework uses five configuration files across two formats. The five files are the complete operator-visible configuration surface; no other files need to be edited to tune framework behavior.

### The five config files

| File | Format | Role |
|------|--------|------|
| `env` | Bash (`export VAR=value`) | Wake script runtime tunables: PM mode, verbose flag (per-agent model overrides moved to `kanban.cfg` `[models.<provider>]`) |
| `bashrc` | Bash | Personal shell config: PATH extensions, OAuth tokens, `KANBAN_ROOT` export |
| `kanban.cfg` | INI (`[section] key = value`) | Framework operational settings: dashboard layout, chain tuning, directory paths |
| `projects.cfg` | INI | Project registry: one entry per registered project |
| `project.cfg` (per project) | INI | Per-project identity: git repo URL, dev tree path, workflow type, version ceilings |

`env` and `bashrc` remain bash-sourced because wake scripts need `export` semantics and `bashrc` holds per-operator secrets (tokens, personal PATH additions). The three INI files are data files — they are parsed, not executed, which prevents the command-injection class of errors.

### Getting started

`install.sh` creates `kanban.cfg` from the template on a fresh install. To customize it:

```bash
# View the schema with inline comments for every key
cat "$KANBAN_ROOT/team/templates/kanban.cfg.example"

# Edit per-install settings
$EDITOR "$PGAI_AGENT_KANBAN_ROOT_PATH/kanban.cfg"
```

For a new project's `project.cfg`:

```bash
cat "$KANBAN_ROOT/team/templates/project.cfg.example"
$EDITOR "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/project.cfg"
```

### Migration from legacy config file names

If upgrading from an older install with legacy config files, `install.sh` handles migration automatically:

- `config.cfg` (bash format) is migrated to `kanban.cfg` (INI format) inline by `install.sh`
- `PROJECT.cfg` (bash format, uppercase) per project is migrated to `project.cfg` (INI format, lowercase)

The migration is idempotent — running it multiple times is safe. No manual file edits are required.

### INI format

All three INI files use standard `[section] key = value` syntax. Comments begin with `#` or `;`.

```ini
[dashboard]
# Minimum column height even when few projects are registered
min_rows_per_column = 13
max_rows_per_column = 34
min_rows_per_project = 3
max_rows_per_project = 8
```

Scripts call `read_ini` from `team/scripts/lib/ini_parser.sh` to retrieve values with a fallback default:

```bash
source "$KANBAN_ROOT/team/scripts/lib/ini_parser.sh"
val=$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard min_rows_per_column 13)
```

### Tuning kanban.cfg

Operators most commonly tune three sections.

**`[dashboard]` — layout knobs:**

| Key | Default | Effect |
|-----|---------|--------|
| `min_rows_per_column` | `13` | Column height floor (prevents sparse look with few projects) |
| `max_rows_per_column` | `34` | Column height ceiling (your terminal/screen constraint) |
| `min_rows_per_project` | `3` | Rows guaranteed to each project (readability floor) |
| `max_rows_per_project` | `8` | Rows allocated to each project when space allows (visual ceiling) |

The dashboard renderer applies a dynamic algorithm: as project count grows, per-project allocation steps down from `max_rows_per_project` toward `min_rows_per_project` to stay within `max_rows_per_column`. With few projects, `min_rows_per_column` enforces a minimum visual height. This replaces the old hardcoded `MIN_PER_PROJECT = 3` constant.

Example with 5 projects and defaults (`min_proj=3, max_proj=8, max_col=34`):
`5 × 8 = 40 > 34` → step down: `5 × 6 = 30 ≤ 34` → allocate 6 rows per project.

**`[chain]` — pipeline behavior:**

| Key | Default | Effect |
|-----|---------|--------|
| `pm_mode` | `automatic` | `automatic` enables PM autonomous scan; `manual` disables it |
| `agent_lock_timeout_seconds` | `3600` | Stale lock threshold for agent cleanup |
| `max_tasks_per_wake` | `1` | Tasks processed per agent wake (1 recommended for traceability) |

**`[paths]` — directory layout:**

| Key | Default | Effect |
|-----|---------|--------|
| `dev_tree_path` | `~/develop/pgai-agent-kanban` | Git checkout where CODER agents do source-tree work |
| `cleanup_retention_days` | `30` | Days to keep archived task folders (`0` = disable auto-cleanup) |

Leave the per-project path keys (`requirements_dir`, `priority_dir`, `archive_dir`, `tasks_dir`, `logs_dir`) commented out for multi-project mode — per-project helpers resolve these automatically. Set them only for single-project legacy installs.

The canonical schema with every key, inline comment, and default value is `team/templates/kanban.cfg.example`. It is the source of truth for new key additions.

### Model override variables

Per-agent model overrides live in `kanban.cfg` under per-provider sections `[models.claude]`, `[models.codex]`, and `[models.gemini]`. At wake startup, the framework resolves the active provider from `[providers] active` and reads the matching `[models.<active_provider>]` section. Each role key in that section is exported as `PGAI_<ROLE>_MODEL` for the wake script to consume.

```ini
[models.claude]
# pm =
# coder = claude-sonnet-4-6
# writer = claude-opus-4-7
# tester =
# cm =
# po =

[models.codex]
# pm =
# coder =
# writer =
# tester =
# cm =
# po =

[models.gemini]
# pm =
# coder =
# writer =
# tester =
# cm =
# po =
```

Model IDs are provider-specific (`claude-sonnet-4-6` is meaningful only to the Claude CLI; codex and gemini use their own model namespaces). Grouping by provider lets a single `kanban.cfg` carry the correct values for every provider and pick the right ones automatically when you switch active providers — no edit, no restart.

An empty (or commented-out) value for a role means "use the subagent frontmatter default" — same fallback behavior as before. A role with no entry in the active provider's section receives no `PGAI_<ROLE>_MODEL` export and falls through to the frontmatter default.

The 3-tier precedence chain documented under "Model Override Mechanism" is unchanged: task README `## Model Override` beats the env var, which beats the subagent frontmatter default. Only the source that populates `PGAI_<ROLE>_MODEL` at wake startup moved — from operator-edited `export` lines in `env` to the active provider's `[models.<provider>]` section in `kanban.cfg`.

Switching `[providers] active` (or running `scripts/switch-provider.sh --provider PROVIDER`) flips which `[models.<provider>]` section the next wake firing reads. No code change, no crontab edit. See "Provider Switch" below.

There is no fallback to a flat `[models]` section. A pre-existing flat `[models]` block in an older `kanban.cfg` is silently ignored — move its values into `[models.claude]` (or whichever provider's section applies) when you upgrade.

## Temporary File Convention

All framework subsystems write temporary files under a single, configurable directory rather than scattering them across `/tmp`. The location is controlled by the `PGAI_AGENT_KANBAN_TEMP_DIR` environment variable.

### The env var

```bash
# Default
PGAI_AGENT_KANBAN_TEMP_DIR="/tmp/pgai_kanban_tmp"
```

The default is `/tmp/pgai_kanban_tmp`. Override it in `env` or by setting `PGAI_AGENT_KANBAN_TEMP_DIR` before launching to redirect framework temp space when `/tmp` is constrained (small tmpfs, mounted noexec, RAM-backed) or when you want all framework files isolated under a known path. The wake scripts export the resolved value so every subagent invocation inherits the same setting.

### Layout

Subsystems organize their temp files into subdirectories under the root:

```
$PGAI_AGENT_KANBAN_TEMP_DIR/
├── tests/        # bash and python test fixtures
├── dashboard/    # dashboard scratch space
└── scratch/      # general agent scratch
```

Bash callers source `team/scripts/lib/temp.sh` and use the `pgai_mktemp`, `pgai_mktemp_d`, `pgai_temp_subdir`, and `pgai_temp_cleanup` helpers. Python tests honour the env var when set and fall back to the system temp directory otherwise. `install.sh` creates the temp dir at install time (idempotent; warns rather than fails if creation is denied).

### Cleanup integration

Because everything lives under one root, cleanup is one operation. `cleanup.sh` includes a temp-dir purge in its default sweep (the final step), and `cleanup.sh --temp-only` purges just the temp dir without touching logs, task folders, or archives:

```bash
# Purge framework temp files only (safe; ignores everything else)
team/scripts/cleanup/cleanup.sh --temp-only

# Preview without deleting
team/scripts/cleanup/cleanup.sh --temp-only --dry-run
```

The root directory itself is preserved across purges; only its contents are removed. Operators can confidently `rm -rf "$PGAI_AGENT_KANBAN_TEMP_DIR"/*` without risking unrelated `/tmp` content from other applications.

## Cleanup Script

The cleanup script at `team/scripts/cleanup/cleanup.sh` performs automated housekeeping. It is designed for cron invocation and requires no LLM involvement.

### What it does

> **Note:** Several path predicates below still reference the legacy `tasks/queues/claude/logs/` location and walk `CLAUDE-*` task folders. Queue flattening moved `*.md` queue files up one level (to `tasks/queues/`) but intentionally left the per-firing batch-log subdirectory and existing task folders in place for backward compatibility. The references below describe what the cleanup script actually targets today.

The script performs six actions in order:

0. **Purge trivial logs** — deletes small, old per-firing batch logs under `projects/*/tasks/queues/claude/logs/` (see "Log retention" below).
1. **Prune old log files** — deletes files in `PGAI_LOGS_DIR` older than the retention period.
2. **Delete terminal task folders** — removes `DONE` and `WONT-DO` task folders from `PGAI_TASKS_DIR` older than the retention period (based on `status.md` modification time).
3. **Archive shipped requirements** — moves requirements documents whose `Target Version` is less than or equal to the value returned by `pp_last_released_version` (highest semver tag on the dev tree's `origin/main`) into `PGAI_ARCHIVE_DIR/requirements/`. Priority requirements are archived separately under `PGAI_ARCHIVE_DIR/requirements/priority/`.
4. **Archive old briefs** — moves brief files in `PGAI_BRIEFS_DIR` older than the retention period into `PGAI_ARCHIVE_DIR/briefs/`.
5. **Purge the framework temp dir** — removes the contents of `PGAI_AGENT_KANBAN_TEMP_DIR` (the root itself is preserved). See "Temporary File Convention" above.

### Log retention (two-tier model)

Cron-driven wake scripts fire on a stagger across every agent, every minute of every hour. The majority of those firings find no work to do — the agent logs "starting / no pending tasks / done" and exits. These trivial logs are typically under 1700 bytes and lose their diagnostic value within a few hours. Real agent work (subagent transcripts, debug output, error traces) produces logs an order of magnitude larger, and remains useful for days.

To avoid drowning substantive logs in trivial chatter — and to keep file-descriptor and inotify pressure bounded — the cleanup script applies two different retention rules:

- **Tier 1 — trivial logs (aggressive purge).** A log under `projects/*/tasks/queues/claude/logs/` is "trivial" when it is BOTH smaller than `PGAI_CLEANUP_TRIVIAL_LOG_BYTES` AND older than `PGAI_CLEANUP_TRIVIAL_LOG_HOURS`. Trivial logs are deleted on every cleanup run (Step 0). Substantive logs (≥ the size threshold) are left alone here and fall through to Tier 2.
- **Tier 2 — substantive logs (normal retention).** Everything in `PGAI_LOGS_DIR` older than `PGAI_CLEANUP_RETENTION_DAYS` is deleted (Step 1). This is the original, coarser sweep; it now handles only logs that survived Tier 1 (recent or substantive) plus anything outside the per-firing batch-log path.

#### Tunable env vars

| Env var | Default | Purpose |
|---|---|---|
| `PGAI_CLEANUP_RETENTION_DAYS` | `30` | Tier-2 retention for substantive logs and other dated artifacts. Set to `0` to disable automatic cleanup. |
| `PGAI_CLEANUP_TRIVIAL_LOG_BYTES` | `1700` | Tier-1 size threshold. A log smaller than this is a candidate for aggressive purge. |
| `PGAI_CLEANUP_TRIVIAL_LOG_HOURS` | `6` | Tier-1 age threshold. A log younger than this is preserved even if small, so the operator can still inspect a freshly-fired agent. |

The defaults are tuned for the observed log-size distribution. Raise `PGAI_CLEANUP_TRIVIAL_LOG_BYTES` if you find legitimate work logs are being eaten; lower it if you want a more aggressive sweep. Raise `PGAI_CLEANUP_TRIVIAL_LOG_HOURS` if you frequently investigate "what happened during last night's run" the morning after.

#### The `--trivial-only` flag

```bash
# Trivial-tier sweep only — fast, safe to run on a tight schedule.
team/scripts/cleanup/cleanup.sh --trivial-only

# Full sweep — Step 0 + Steps 1–4 (requires release-state.md to be present).
team/scripts/cleanup/cleanup.sh
```

`--trivial-only` runs Step 0 and exits. It does NOT touch substantive logs, terminal task folders, requirements archives, or briefs. It also skips the release-state preflight, so it is safe to run on systems without an active RC. Combine with `--dry-run` to preview what would be deleted.

#### The `--temp-only` flag

```bash
# Framework temp-dir purge only — safe; does not touch logs, tasks, or archives.
team/scripts/cleanup/cleanup.sh --temp-only
```

`--temp-only` runs the temp-dir purge step and exits. Like `--trivial-only`, it skips the release-state preflight and the archive-directory setup, so it is safe to run on systems without an active RC. Use this for targeted recovery when `/tmp` (or the configured temp dir) needs reclaiming without touching anything else.

#### What is never auto-deleted

The trivial purge is scoped narrowly on purpose. The following are NEVER touched by Step 0:

- **`actions.log`** — the framework's audit trail. Excluded both by path scope (it lives outside `projects/*/tasks/queues/claude/logs/`) and by an explicit `-not -name actions.log` safety guard in case a future layout change places one inside the swept tree.
- **Per-task logs in `projects/*/tasks/CLAUDE-*/logs/`** — these are the audit trail of what each task did. They follow the normal Tier-2 retention sweep, not the trivial purge, regardless of their size.
- **Active-task state files.** Cleanup only deletes log files; it never touches `status.md` or other task-folder files. Step 2's terminal-task-folder deletion (a separate action, governed by `PGAI_CLEANUP_TASK_RETENTION_DAYS`) applies only to tasks already in `DONE` or `WONT-DO`.

If you discover something else should be exempt, fix the find predicate in `cleanup.sh`. Do not work around it by raising thresholds — the goal is to keep operators from ever needing to hand-edit retention numbers in an emergency.

#### Recommended cron cadence

Two entries: a fast trivial-only sweep that runs often, and a full sweep that runs less often.

```cron
# Daily — trivial-log purge at 04:15. Fast; can run any time, but off-peak is courteous.
15 4 * * * $HOME/pgai_agent_kanban/team/scripts/cleanup/cleanup.sh --trivial-only >> $HOME/pgai_agent_kanban/logs/cleanup-cron.log 2>&1

# Weekly — full cleanup Sunday at 04:00. Includes substantive log retention,
# terminal-task-folder deletion, requirements archive, and brief archive.
0 4 * * 0 $HOME/pgai_agent_kanban/team/scripts/cleanup/cleanup.sh >> $HOME/pgai_agent_kanban/logs/cleanup-cron.log 2>&1
```

Stagger the start times so the two entries cannot collide on the same minute. Both entries are commented-out in `team/scripts/cron-suggested.template.txt` so operators opt in deliberately.

The two-tier model avoids drowning substantive agent logs in trivial chatter while keeping file-descriptor and inotify pressure bounded.

#### logs/debug/ is a write-only sink

**logs/debug/ is a write-only sink.** Agents write diagnostic output to `logs/debug/` but must never read from it. Reading from this directory is not part of any agent's procedure. If an agent finds itself reading `logs/debug/`, it is off-procedure and should stop, backtrack to the last known-good step, and resume from there.

Debug logs (under logs/debug/) are write-only sinks for human observability. No agent reads from logs/debug/ during normal operation. Agents must not include log content in their task analysis.

### Running the script

```bash
# Preview what would be cleaned up (no changes made)
team/scripts/cleanup/cleanup.sh --dry-run

# Run cleanup for real
team/scripts/cleanup/cleanup.sh
```

The script writes a summary log to `PGAI_LOGS_DIR/cleanup-YYYYMMDD-HHMMSS.log` on every run, including dry runs.

### Configuration

The cleanup script reads configuration from `kanban.cfg` (INI format) via `read_ini`. Retention values are in the `[paths]` section of `kanban.cfg` or can be overridden via environment variables (highest precedence tier).

## Briefs Directory Convention

Brief files for the PO agent should be placed in `$PGAI_AGENT_KANBAN_ROOT_PATH/briefs/`. This is a recommended convention, not a hard requirement.

### Why use it

Placing briefs in `$KANBAN_ROOT/briefs/` keeps them organized alongside the kanban tree. The cleanup script automatically archives briefs from this directory after the retention period expires, and the `PGAI_BRIEFS_DIR` config variable points here by default.

### Not enforced

The `po-agent.sh` script accepts any file path as its argument. You can pass a brief from any location on the filesystem:

```bash
# Convention — brief in $KANBAN_ROOT/briefs/
./scripts/po-agent.sh "$PGAI_AGENT_KANBAN_ROOT_PATH/briefs/my-feature-brief.md"

# Also valid — brief anywhere
./scripts/po-agent.sh ~/Desktop/my-feature-brief.md
```

Briefs placed outside `$KANBAN_ROOT/briefs/` will not be automatically archived by the cleanup script.

## Dashboard

The dashboard gives a live view of kanban system state: version, RC progress, HALT status, active task, queue depths, and blocked tasks. It is intended for human monitoring, not agent use.

### Launching the dashboard

```bash
# Standard launch — opens a tmux session named pgai-kanban-dashboard
# Minimum terminal size: 100 columns x 30 rows
$KANBAN_ROOT/team/scripts/dashboard.sh

# Override the kanban root
$KANBAN_ROOT/team/scripts/dashboard.sh --kanban-root /path/to/kanban

# Override the tmux session name
$KANBAN_ROOT/team/scripts/dashboard.sh --session my-session
```

If a session named `pgai-kanban-dashboard` already exists, the script attaches to it rather than creating a new one.

### Four-window layout

The dashboard creates four tmux windows. Window 0 (status) is the default. Navigate with `Ctrl-B` followed by the window number.

| Window | Name | Purpose |
|--------|------|---------|
| 0 | `status` | Five-second-glance view: version, RC, HALT, progress, queues, active task |
| 1 | `logs` | Merged colored log stream from all agent cron logs (live tail) |
| 2 | `terminal` | Three interactive shell panes pre-cd'd to useful directories |
| 3 | `attention` | BLOCKED tasks with reasons and recommended next steps |

#### Window 0: Status

The default window. Five panes arranged as a header strip across the top, a three-column middle row, and a logs strip across the bottom:

```
┌──────────────────────────────────────────────────────────────────────┐
│ HEADER  (top ~11%)  — version / active RC / HALT / workflow         │
├──────────────────────┬──────────────────────────────────┬────────────┤
│ QUEUES (~30%)        │ PROGRESS (~45%)                  │ CRON (~25%)│
│ per-agent summary    │ shipped + summary stats          │ next-fire  │
├──────────────────────┴──────────────────────────────────┴────────────┤
│ LIVE LOGS  (bottom ~40%)                                             │
└──────────────────────────────────────────────────────────────────────┘
```

The middle row is a 75/25 horizontal split: the left 75% is shared by QUEUES (~30%) and PROGRESS (~45%); the right 25% is the CRON pane (next cron firings). Pane widths are set as percentages by `dashboard-create.sh`, so they scale with terminal width. Drill-down windows (`drill-1`, `drill-2`, ...) reuse the same five-pane layout scoped to a single project; only the CRON pane stays shared, because cron is a system-level schedule rather than per-project state.

The header, queues, progress, and cron panes all auto-refresh every 5 seconds. Configure the interval with `PGAI_DASHBOARD_REFRESH_SECONDS`.

#### Window 1: Logs

Merged colored log stream from all six agent cron logs. Each line is tagged `[<agent> HH:MM:SS]` with agent-specific color (pm=cyan, coder=green, writer=yellow, tester=blue, cm=magenta, cleanup=dim). Lines from all agents are interleaved chronologically in `tail -F` style. Powered by `team/scripts/dashboard/logs.sh`.

#### Window 2: Terminal

Three interactive shell panes, each pre-cd'd to a useful directory:

- Top-left: kanban root (`$PGAI_AGENT_KANBAN_ROOT_PATH`)
- Top-right: tasks directory (`$KANBAN_ROOT/tasks`)
- Bottom: queues directory (`$KANBAN_ROOT/tasks/queues/claude`)

#### Window 3: Attention

Shows all BLOCKED tasks with their task ID, time blocked, blocker reason, and recommended next step (from `## Next Recommended Step` in the task's `status.md`). When no tasks are blocked, shows a clean "(no blocked tasks)" message. Auto-refreshes every 5 seconds.

### Status bar

Every window shows a green status bar at the bottom:

```
[pgai-kanban] 0:status* 1:logs 2:terminal 3:attention | v0.15.5  HALT:off  Mon 14:23
```

Components (left to right): session name, window list with active window highlighted by `*`, installed version, HALT indicator (green when off, red/yellow when on), date and time.

### Operator workflow with the dashboard

**Starting a work session:**

1. Launch the dashboard: `$KANBAN_ROOT/team/scripts/dashboard.sh`
2. Window 0 (status) loads by default. Check HALT indicator, RC progress, and queue summary.
3. Check Window 3 (attention) for any BLOCKED tasks needing intervention.
4. Monitor Window 1 (logs) to watch agent activity in real time.
5. Use Window 2 (terminal) to inspect queue files, task folders, or run commands directly.

**Checking system health without tmux:**

Use `kanban-status.sh` for a one-shot status view from any terminal — SSH sessions, scripts, or environments without tmux:

```bash
# One-shot output (fits 80x24)
$KANBAN_ROOT/team/scripts/kanban-status.sh

# Continuously refreshing view in any terminal
watch -n 5 $KANBAN_ROOT/team/scripts/kanban-status.sh

# Disable color output
$KANBAN_ROOT/team/scripts/kanban-status.sh --no-color
```

Color output is automatically suppressed when `NO_COLOR` is set or `TERM=dumb`.

**Halting and resuming the system:**

```bash
# Pause (no new tasks will be pulled)
touch $PGAI_AGENT_KANBAN_ROOT_PATH/HALT

# Resume
rm $PGAI_AGENT_KANBAN_ROOT_PATH/HALT
```

The status bar on every window shows the current HALT state.

**Unblocking a task:**

1. Check Window 3 (attention) for the blocked task ID and reason.
2. Use Window 2 (terminal) to investigate or apply the fix.
3. Edit the task's `status.md` to set state back to `BACKLOG`.
4. The agent will pick it up on the next cron wake cycle.

### Visibility window: rows and sort order

The unified visibility window (a dedicated tmux window separate from the main status window) renders eight columns of work items: BUGS, PRIORITIES, REQUIREMENTS, PM, CODER, WRITER, TESTER, CM. Each column shows up to **13 rows** of entries. Columns with fewer than 13 items show only the items that exist — no padding to 13 with empty rows. Tmux pane heights are sized so all 13 rows fit without scrolling.

#### Sort order within a column

Within each column, entries sort **DESC by identifier** (newest / highest-numbered first). The sort key depends on the column:

| Column                                              | Sort key                |
|-----------------------------------------------------|-------------------------|
| BUGS, PRIORITIES, PM, CODER, WRITER, TESTER, CM     | numeric ID, DESC        |
| REQUIREMENTS                                        | semver, DESC            |

So BUG-0099 ranks above BUG-0001, and `v0.23.25` ranks above `v0.0.1`.

#### Multi-project minimum representation

When more than one project contributes items to the same column, a per-project minimum keeps a small project from being completely buried by a large one. The algorithm runs in three steps:

1. **Per-project minimum.** Every project with at least one item in the column is guaranteed `min(N_items, 3)` rows.
2. **Spare-row fill.** Any rows left over after minimums are filled from a **global DESC** merge of all items not already claimed by a minimum.
3. **Final ordering.** The combined set (minimums + spare fill) is sorted **globally DESC** by the column's sort key for display.

**Worked example.** Column BUGS, 13 rows, two projects: project A has 100 bugs (BUG-0001 to BUG-0099 plus one more), project B has 3 bugs (BUG-0001 to BUG-0003).

1. Minimums: A gets `min(100, 3) = 3` rows; B gets `min(3, 3) = 3` rows. Six guaranteed.
2. Spare rows: `13 − 6 = 7`. Global DESC of unclaimed items fills these with A's next 7 (BUG-0096 through BUG-0090).
3. Final globally-DESC display:

   ```
   ■A BUG-0099
   ■A BUG-0098
   ■A BUG-0097
   ■A BUG-0096
   ■A BUG-0095
   ■A BUG-0094
   ■A BUG-0093
   ■A BUG-0092
   ■A BUG-0091
   ■A BUG-0090
   ■B BUG-0003
   ■B BUG-0002
   ■B BUG-0001
   ```

The algorithm scales to three or more projects without change: every project with items still receives `min(N_items, 3)`; the remaining rows still come from a global DESC merge. If only one project has items, that project takes all 13 rows. If the total across all projects is fewer than 13, the column shows everything it has and stops there.

#### Project tag prefix

Every row is prefixed with a colored square (`■`) rendered in the project's `display_color` from `projects.cfg`. There is no project-name text on the row — the colored square is the only project-identifying signal. Operators distinguish projects by color alone, with the legend below the grid as the lookup table. See the **Dashboard Color Conventions** section below for the `display_color` field and the default palette.

The row-count limit and sort algorithm both live in `team/scripts/dashboard/column-render.sh`. The pane heights live in `team/scripts/dashboard/create.sh`.

### Dashboard Color Conventions

The unified 8-column visibility window encodes two pieces of information on every row using two independent color dimensions. Operators read the dashboard at a glance by treating these dimensions as a pair: "where" and "what state."

- **Project color (left tag)** — fixed per project, does not change as work progresses. Answers "where does this row belong?"
- **Status color (entry text)** — reflects lifecycle state, changes as work moves. Answers "what state is this row in?"

#### projects.cfg format

The source of truth for project colors is `projects.cfg` at the kanban root. The format is extended from `name:priority` to:

```
name:priority[:display_color]
```

`display_color` is an HTML-standard hex value like `#378ADD` (not `0xRRGGBB`). Direct copy-paste from any color picker is CSS- and SVG-compatible without translation. The field is optional: lines with only `name:priority` remain valid and the dashboard falls back to the next deterministic palette entry at read time, so installs predating the redesign keep working without manual edits.

Example:

```
pgai-agent-kanban:1:#378ADD
pgai-video-generator:2:#1D9E75
```

#### Default palette

`create-project.sh` auto-assigns colors from a built-in palette of eight visually distinct entries, ordered for maximum legibility across mixed-project rows. Newly registered projects pick up the next unused palette index. If all eight slots are already in use, assignment wraps to index 0 and the operator should edit `projects.cfg` to disambiguate.

| Slot | Hex       | Color name (intent)           |
|------|-----------|-------------------------------|
| 0    | `#378ADD` | blue 400 — typically kanban-self |
| 1    | `#1D9E75` | teal 400                      |
| 2    | `#D85A30` | coral 400                     |
| 3    | `#BA7517` | amber 400                     |
| 4    | `#D4537E` | pink 400                      |
| 5    | `#7F77DD` | purple 400                    |
| 6    | `#639922` | green 400                     |
| 7    | `#888780` | gray 400 (last resort)        |

The palette is defined once in `team/scripts/lib/projects_cfg.sh` as the `PGAI_DEFAULT_PALETTE` array. Do not duplicate the hex values elsewhere; reference the array by name.

#### Project color: left tag

Every row in every column carries a small colored square at its left edge in the project's `display_color`. The square is the only project-identifying signal in the unified view, so mixing rows from several projects in a single column stays unambiguous. Project color is set in `projects.cfg` and is fixed for the life of the project unless an operator edits the file.

#### Status color: entry text

The text color of each entry reflects its current lifecycle status, read from each row's `## Status` field (for bugs and priorities) or `## State` field (for tasks) on every dashboard refresh.

| Status    | Text color                    | Hex       |
|-----------|-------------------------------|-----------|
| `open`    | default text (theme-driven)   | `var(--color-text-primary)` |
| `running` | amber                         | `#BA7517` |
| `done`    | green                         | `#639922` |
| `blocked` | red                           | `#E24B4A` |

The mapping is implemented once in `dashboard-column-render.sh` (`status_to_color()`), and the legend uses the same function so legend and rows never disagree.

#### Legend

A persistent legend below the visibility grid lists each active project with its color tag and renders the four status keywords in their mapped colors, so the convention can be relearned without leaving the dashboard:

```
PROJECT: ■ pgai-agent-kanban  ■ pgai-video-generator   |   STATUS: open  running  done  blocked
```

The legend template lives in `team/scripts/lib/dashboard_legend.sh` as the `DASHBOARD_LEGEND_TEMPLATE` constant; the renderer substitutes the per-project block and the colored status block into the template on every refresh.

#### Operator override

To change a project's color or priority, edit its line in `projects.cfg`. The dashboard re-reads `projects.cfg` only on startup, so a running session continues with the cached values until it is restarted.

```bash
# Edit projects.cfg, then restart the dashboard to pick up the change
$EDITOR $KANBAN_ROOT/projects.cfg
$KANBAN_ROOT/team/scripts/dashboard/kill.sh
$KANBAN_ROOT/team/scripts/dashboard.sh
```

Manual edits are only needed when the operator wants a non-default color, when two projects collide on the same palette index, or when all eight palette slots are already in use.

#### Migration behavior

Installs whose `projects.cfg` predates the redesign — entries with only `name:priority`, no color field — get a one-time top-up the next time `install.sh` or `upgrade.sh` runs: each color-less line is rewritten to include the next available palette entry. The migration is idempotent (running twice does not double-add a color) and logged (operators see exactly which lines were touched). Two-field lines remain a valid format after migration, so manual rollback is always available.

### Refresh loops: `while sleep`, not `watch`

The visibility-pane refresh loops in `team/scripts/dashboard/create.sh` are written as `bash -c 'while true; do clear; ...; sleep N; done'`, not as `watch -c -n N -- ...`. Maintainers editing dashboard scripts must preserve this pattern. Do not "simplify" it back to `watch -c`.

The reason is that procps-ng `watch(1)`, even with `-c` / `--color`, only preserves basic 16-color ANSI sequences (`\033[3Xm` and `\033[9Xm`). It silently strips 24-bit truecolor sequences of the form `\033[38;2;R;G;Bm`. The dashboard's per-project tag glyph (`■`) is rendered with truecolor so the operator can configure any hex value in `projects.cfg`'s `display_color` field, including arbitrary brand colors that do not map cleanly to a 16-color palette. Under `watch -c`, those truecolor escapes never reach tmux and every project tag glyph collapses to the default terminal color (white), defeating the visual purpose of the unified visibility window. The basic 8-color status indicators (yellow `running`, green `done`, red `blocked`) keep working under `watch -c`, which is exactly what made the regression hard to spot — status colors render, project colors do not.

The `while sleep` loop is the equivalent refresh primitive without the ANSI filter. `clear` substitutes for `watch`'s implicit clear-between-iterations. Pane exit kills the shell running the loop, so there are no orphaned processes. The `REFRESH_INTERVAL` (driven by `PGAI_DASHBOARD_REFRESH_SECONDS`) semantics are unchanged. This pattern must be used for every pane that renders project-colored content; an in-script `IMPORTANT — why while-sleep loops, not watch` comment block at the top of the visibility-pane command definitions calls this out at the point of edit.

If a future pane is added that renders only basic-ANSI content (status colors only, no per-project tag), `watch -c` would technically work for that one pane. Prefer `while sleep` anyway for consistency, so a later change that introduces truecolor content does not silently regress.

### Metadata window

The metadata window (rendered by `team/scripts/dashboard/metadata.sh`) shows kanban-wide state at the top and one block per registered project below it. The kanban-wide section reports the installed version, PM mode, and HALT state. Each per-project block reports:

- `workflow:` — the project's `workflow_type` from `project.cfg [project]`
- `active RC:` — the current release-candidate version from `release-state.md`, or `none`
- `last released:` — the newest released tag for the project
- `max minor:` — the `max_minor` ceiling from `project.cfg [versioning]`
- `max major:` — the `max_major` ceiling from `project.cfg [versioning]`
- `max patch:` — the `max_patch` ceiling from `project.cfg [versioning]` (defaults to `0` when unset)

The three `max ...` lines mirror the discovery-time version ceilings documented under "Project version ceilings"; the metadata window is the at-a-glance view of which ceilings each project currently has. Layout and spacing match across the three lines so a value change is immediately visible.

### Dashboard configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `PGAI_DASHBOARD_REFRESH_SECONDS` | 5 | Auto-refresh interval for Status (Window 0) and Attention (Window 3) |
| `PGAI_DASHBOARD_SESSION_NAME` | `pgai-kanban-dashboard` | Tmux session name |

Set these in your `env` file or export them before launching the dashboard.

## Metrics

The kanban writes a structured metrics surface on every RC close. The surface is the primary operator and downstream-agent contract for "what happened on this RC, this day, and across the project's history." Cost is a derived view layered on top (see `Tracking Token Costs` below); the metrics surface itself is the canonical data.

The principle is data first. The schemas below are designed so that any future visualization — Grafana, spreadsheets, notebooks, alerting, anomaly detection — can be built without changing the source files. The framework only commits to publishing the data and a small CLI for extracting it. It does not commit to projections, comparisons, or charts; those are out of scope and intentionally not built.

### File layout

All metrics files live under one tree per project:

```
projects/<name>/metrics/
  rc/<version>.json        # per-RC rollup, one file per closed RC
  day/<YYYY-MM-DD>.json    # per-day rollup, one file per day with activity
  history.csv              # cumulative append-only history, one row per RC
```

The per-task `tokens.json` files under `projects/<name>/tasks/<task-id>/artifacts/tokens.json` are the source data every rollup is computed from. The metrics surface is strictly derived: deleting any rollup file and re-running the aggregator must reproduce the same content from the per-task data. Operators may safely delete and regenerate any file in `metrics/rc/` or `metrics/day/`. `history.csv` is append-only — see "Cumulative history.csv" below.

### Per-task `tokens.json` schema (the source of truth)

Each agent invocation writes one `tokens.json` to its task's `artifacts/` directory. The schema is the input the metrics aggregator reads; getting it right is what unblocks every rollup downstream.

```json
{
  "model": "claude-opus-4-7",
  "provider": "claude",
  "agent": "coder",
  "rc_version": "v0.24.12",
  "input_tokens": 234,
  "output_tokens": 1856,
  "cache_creation_input_tokens": 12567,
  "cache_read_input_tokens": 87654,
  "invocations": 1,
  "elapsed_seconds": 245,
  "timestamp": "2026-05-17T21:08:12Z"
}
```

Two fields carry the contract that v0.24.12 added:

**`model` must be the canonical model ID** — `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`, and so on. Shortname aliases (`opus`, `sonnet`, `haiku`) used by older subagent capture paths cause every downstream tool keyed by canonical ID to silently drop the record. The token-capture helper now canonicalizes before write and rejects unmapped shortnames with a loud warning. Legacy files written before v0.24.12 are tolerated at read time (see "Read-time canonicalization" below) but new writes are expected to be canonical.

**`rc_version` must be the RC the task belongs to** — `v0.24.12` for any task that ran under the rc/v0.24.12 branch. The aggregator uses this field to attribute tokens to RCs without walking task READMEs. Tasks that have no RC association (rare; usually utility scripts) may omit the field, and the aggregator attributes them to no RC.

The remaining fields (`provider`, `agent`, the four token counts, `invocations`, `elapsed_seconds`, `timestamp`) are unchanged from the prior schema.

### Per-RC JSON rollup

Written by `team/scripts/lib/metrics_aggregator.py` on RC close (invoked from `cm-release.sh` Step 19a) and on demand by `metrics-report.sh` when a requested file is missing. Path: `projects/<name>/metrics/rc/<version>.json`.

The rollup carries identity (`rc`, `project`, `workflow_type`), lifecycle (`opened_at`, `closed_at`, `wall_time_minutes`, `outcome`), task counts (`tasks.total`, `tasks.by_agent`), and the full token breakdown:

```json
{
  "rc": "v0.24.12",
  "project": "pgai-agent-kanban",
  "workflow_type": "release",
  "opened_at": "2026-05-18T01:04:37Z",
  "closed_at": "2026-05-18T02:30:15Z",
  "wall_time_minutes": 86,
  "outcome": "SHIPPED",
  "tasks": {
    "total": 8,
    "by_agent": { "pm": 1, "coder": 3, "writer": 1, "tester": 1, "cm": 2 }
  },
  "tokens": {
    "total":    { "input": ..., "output": ..., "cache_read": ..., "cache_write": ..., "invocations": ... },
    "by_model": { "claude-opus-4-7": { ... }, "claude-haiku-4-5-20251001": { ... } },
    "by_agent": { "coder": { ... }, "tester": { ... }, ... }
  },
  "bugs_filed_during_verification": [],
  "operator_interventions": [],
  "input_files": {}
}
```

Aggregation is fully deterministic and idempotent. Running `aggregate_rc()` twice on the same RC produces the same bytes (sorted keys, fixed indent). Deleting a rollup and regenerating it is the supported recovery path for any rollup that drifted, was hand-edited, or pre-dates a schema fix.

### Per-day JSON rollup

Written lazily on demand: `metrics-report.sh --day 2026-05-18` invokes `aggregate_day()` if the file is missing. Path: `projects/<name>/metrics/day/<YYYY-MM-DD>.json`.

A per-day rollup sums every per-task `tokens.json` whose timestamp falls within that UTC calendar day, regardless of which RC each task belongs to. It also records which RCs contributed (`rcs_included`):

```json
{
  "date": "2026-05-18",
  "project": "pgai-agent-kanban",
  "rcs_included": ["v0.24.11", "v0.24.12"],
  "tokens": {
    "total":    { "input": ..., "output": ..., "cache_read": ..., "cache_write": ..., "invocations": ... },
    "by_model": { ... },
    "by_agent": { ... }
  }
}
```

Day rollups are derived from the same per-task data as RC rollups; they are not derived from RC rollups. This matters when a single RC straddles a UTC day boundary: the day file counts only the portion that timestamps into that day, while each RC file counts the whole RC.

### Cumulative `history.csv`

Written by `team/scripts/lib/metrics_csv_writer.py` on RC close (invoked from `cm-release.sh` Step 19b). Path: `projects/<name>/metrics/history.csv`.

One row per RC, appended on close, never modified. The header is written on first call and never re-emitted. Columns, in order:

```
rc, project, workflow_type, opened_at, closed_at, wall_time_minutes,
outcome, tasks_total, tasks_pm, tasks_coder, tasks_writer, tasks_tester,
tasks_cm, input_tokens, output_tokens, cache_read_tokens,
cache_write_tokens, cache_hit_rate_pct, bugs_filed_during, operator_waivers
```

`cache_hit_rate_pct` is `cache_read_tokens / (input_tokens + cache_read_tokens) * 100`, rounded to one decimal. The remaining fields map directly to the per-RC rollup.

Concurrent RC closes are serialized with an exclusive `fcntl` advisory lock on `history.csv` itself; the writer also performs a TOCTOU-safe duplicate check inside the lock, so re-running a release that already wrote a row is a no-op rather than a duplicate. Append-only is the contract — the file is meant to be tailed, piped into spreadsheets, or shipped to Grafana with no further transformation. Do not edit existing rows. If a row is wrong, fix the upstream rollup and accept that the historical CSV row stays as written; the kanban's append-only audit is more valuable than retrospective edits.

### Read-time canonicalization (backfill)

Legacy `tokens.json` files may carry the shortname `model` field (`opus`, `sonnet`, `haiku`) and have no `rc_version`. The aggregator handles both gracefully without rewriting any source file:

- Shortnames are mapped at read time: `opus` -> `claude-opus-4-7`, `sonnet` -> `claude-sonnet-4-6`, `haiku` -> `claude-haiku-4-5-20251001`. Any other unrecognized model string is left as-is with a single stderr warning.
- Missing `rc_version` is resolved by reading `## Release Version` from the task's `README.md`; tasks without that field are excluded from per-RC rollups but still contribute to per-day rollups.

The source files are not modified by the aggregator. Historical fidelity is preserved; canonicalization happens in memory at aggregation time. Current writes are expected to be canonical at the source, so the read-time map is a compatibility shim.

### `metrics-report.sh` CLI

`team/scripts/metrics-report.sh` is the operator-facing entry point for everything under `metrics/`. The scope flags select what to emit; output goes to stdout in the requested format.

| Flag | Argument | Purpose |
|---|---|---|
| `--rc` | `vX.Y.Z` | Emit the per-RC rollup JSON. Triggers on-demand aggregation if the file is missing. |
| `--day` | `YYYY-MM-DD` | Emit the per-day rollup JSON. Triggers on-demand aggregation if the file is missing. |
| `--csv` | (none) | Emit the full contents of `history.csv`. Combine with `--project` to filter. |
| `--format` | `jsonl` | Emit one JSON object per line (JSON Lines). Without `--rc` or `--day`, streams every per-RC rollup found, sorted by version. |
| `--tail` | (none) | Live-tail `history.csv` via `tail -f`. Streams each new row as `cm-release.sh` appends it. Stop with Ctrl-C. |
| `--project` | `<name>` | Override `PGAI_PROJECT_NAME` (default: `pgai-agent-kanban`). |
| `--kanban-root` | `<path>` | Override `PGAI_AGENT_KANBAN_ROOT_PATH`. |

Example invocations:

```bash
# Per-RC rollup for v0.24.12
team/scripts/metrics-report.sh --rc v0.24.12

# Per-day rollup for today (UTC)
team/scripts/metrics-report.sh --day 2026-05-18

# Full history CSV
team/scripts/metrics-report.sh --csv

# Filtered CSV for a different project
team/scripts/metrics-report.sh --csv --project pgai-video-generator

# Streaming JSON Lines for every RC ever closed
team/scripts/metrics-report.sh --format jsonl

# Live tail of new RC closes
team/scripts/metrics-report.sh --tail
```

Exit codes: 0 on success (warnings on stderr are fine), 1 on usage or configuration errors, 2 when the requested data does not exist (no rollup file, empty CSV, etc.). Downstream consumers should treat exit 2 as "nothing to report for this scope" rather than a hard failure.

### Dashboard W6 (Metrics)

The kanban dashboard's window 6 (`Metrics`) renders a live operator view of the same data, refreshed on the standard dashboard interval. It runs `team/scripts/dashboard/metrics.sh` under `watch -t -c` so truecolor is preserved.

The pane shows two stacked blocks:

- **Today** — per-project summary for the current UTC day: RCs shipped, wall time, total tokens with cache-hit percentage, task count. Sourced from `metrics/day/<today>.json` per project.
- **Current RC** — per-project open-RC progress: tasks done / total, elapsed wall time, tokens so far. Sourced from `metrics/rc/<active>.json` per project plus the project's `release-state.md` for active-RC discovery.

The pane is intentionally facts-only: no pace projections, no comparison to historical averages, no charts. Operators who want richer views should consume `history.csv` or the per-RC JSON files directly through their tool of choice.

### `cm-release.sh` integration

`cm-release.sh` Step 19 invokes both writers as a non-blocking pair. Step 19a runs `metrics_aggregator.py` to write the per-RC JSON; Step 19b runs `metrics_csv_writer.py` to append the history.csv row from that JSON. Both steps are wrapped in non-blocking error handling: if either fails, the release still ships and a `[metrics]` WARNING is printed to stderr. The release contract (branch merged, tag pushed, state file updated) does not depend on metrics succeeding. Operators who notice missing rollups can rebuild them after the fact by deleting the file and re-running `metrics-report.sh --rc <version>`.

### `cost-report.sh` (backward-compatibility wrapper)

`team/scripts/cost-report.sh` is preserved as the cost-specific lens on the same underlying data. Existing cron jobs, alert scripts, and operator muscle memory continue to work; the script now prints a footer pointing at `metrics-report.sh` for the richer surface. New code and new tooling should target `metrics-report.sh` directly. See "Tracking Token Costs" below for the cost-report flag set, the cache-aware cost decomposition, and how the cost numbers feed provider decisions.

### What is explicitly out of scope

The metrics surface ships data, not interpretation. The following are deliberately not built and should not be added without a fresh priority brief:

- Pace projections ("RC will complete in ~30 min")
- Comparison to historical averages
- Multi-day or multi-RC trend charts
- Per-provider arbitrage recommendations
- Anomaly detection ("this RC used 3x more tokens than typical")
- Any visualization beyond the dashboard W6 facts-only pane

These are all reasonable future work, and the schemas above are designed to support them as derived consumers. None of them belong in the framework's metrics layer itself. Build them externally — Grafana, notebooks, spreadsheets, whatever fits — against `history.csv` and the per-RC JSON files. The contract the framework commits to is the data, not the chart.

## Tracking Token Costs

The kanban captures token usage on every `claude -p` invocation, rolls those counts up per RC and per day, and translates them into dollar estimates with `cost-report.sh`. The output is the operator's source of truth for what the kanban actually costs to run and which provider arrangement makes sense going forward.

### What is captured

Three layers of data accumulate as the kanban runs:

**Per-task — `projects/<name>/tasks/<task-id>/artifacts/tokens.json`.** Written after each agent invocation. One file per task; multiple `claude -p` calls inside the same task are summed into the single file. Fields include `model`, `provider`, `agent`, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `invocations`, `elapsed_seconds`, and `timestamp`. This is the raw datum every roll-up derives from.

**Per-RC — `projects/<name>/usage/rc/<version>-tokens.json`.** Written by the aggregator (invoked from `cm-release.sh` at ship time, or on demand). Contains a `version`, a `shipped_at` timestamp, a `tasks` array (one entry per task that contributed to the RC, with agent, token counts, and `cost_usd`), and a `totals` block aggregating the whole RC.

**Per-day — `projects/<name>/usage/daily/YYYY-MM-DD.json`.** Written end-of-day or on demand. Contains `date`, `project`, an `rcs_shipped` list, a `totals` block, and a `by_agent` breakdown.

If a roll-up file is missing when a report is requested, `cost-report.sh` invokes `aggregate_tokens.py` to build it from the per-task files, then proceeds with the report.

### Cache token economics

Anthropic bills four distinct token categories on every Claude API call. Cost data in the kanban only makes sense once you understand what each category is and why one of them dominates this framework's spend.

**The four billing categories.** Anthropic charges separately for:

| Category | tokens.json field | Rate relative to input | Why it exists |
|---|---|---|---|
| Input (new) | `input_tokens` | 1.00x (base rate) | Fresh prompt content the model has not seen this session. |
| Cache writes | `cache_creation_input_tokens` | 1.25x | Tokens added to the prompt cache this turn, so future turns can read them cheaply. |
| Cache reads | `cache_read_input_tokens` | 0.10x | Tokens served from a previously written cache entry. |
| Output | `output_tokens` | 5.00x (varies by model) | Tokens the model generated. |

The exact dollar amounts live in `team/scripts/lib/token_pricing.json` under `providers.<provider>.models.<model>.{input,output,cache_creation,cache_read}_per_1m`. Do not memorize numbers from this document — they will drift. The ratios above are stable (they are how Anthropic structures the pricing) but the absolute rates are not.

**Why cache reads dominate this framework's input volume.** The kanban runs each agent task as a fresh `claude -p` invocation. There is no persistent agent process holding context between tasks; every task pays to load the governance stack (DIRECTIVES, OVERVIEW, SOP, role file, project README, task README, task status) from scratch.

That sounds expensive. It is not, because Anthropic's prompt cache absorbs almost all of it. The governance stack is large and stable; once cached, it returns as `cache_read_input_tokens` at 10% of the normal input rate on subsequent invocations within the cache window. Only the small per-task delta — the actual user prompt, the changed task state — shows up as `input_tokens` at the full rate.

The net effect is that `cache_read_input_tokens` typically accounts for the large majority of input *volume* across a busy day, but represents only a small fraction of input *cost* because of the 0.10x multiplier. A report that summed only `input_tokens` would under-report real input volume by one to two orders of magnitude. The corrected report shows all four categories with their token counts and per-category dollar costs, so the cache economics are visible rather than buried.

**Worked example — a single CODER invocation.** A representative `tokens.json` record from a CODER task:

```json
{
  "model": "opus",
  "provider": "claude",
  "agent": "coder",
  "input_tokens": 7,
  "output_tokens": 1200,
  "cache_creation_input_tokens": 11603,
  "cache_read_input_tokens": 44108,
  "invocations": 1,
  "elapsed_seconds": 151,
  "timestamp": "2026-05-16T13:14:32Z"
}
```

Total input *volume* is `7 + 11,603 + 44,108 = 55,718` tokens. Only 7 of those (0.01%) are paid at the full input rate. Cache reads alone are 79% of input volume. This is the typical shape of a CODER invocation in this framework: a small delta against a large cached prefix.

The cost decomposition (using the current `token_pricing.json` rates for `claude-opus-4-7` at the time of writing — verify against the live file before quoting numbers in any decision document) shows the trade-off clearly:

- `7 input × input_per_1m / 1M` — negligible
- `11,603 cache writes × cache_creation_per_1m / 1M` — the largest single line
- `44,108 cache reads × cache_read_per_1m / 1M` — material but small
- `1,200 output × output_per_1m / 1M` — the second-largest line

Across this one invocation, cache *creation* costs more than cache reads — the 1.25x multiplier on the writes outweighs the volume of reads at the 0.10x rate. The arithmetic flips across many invocations that reuse the same cached prefix: write once at 1.25x, read many times at 0.10x, and the read side wins the running total.

That asymmetry is the whole point of the prompt cache. The kanban's task-per-invocation model takes the cache write hit on the first task that loads a given context, then amortizes it across every subsequent task that reads the same prefix back. Day-scope and month-scope cost reports show the amortized picture; single-task or single-invocation analysis shows the per-call picture.

Compare against what this would cost without caching: `55,718 × input_per_1m / 1M`. Real-world numbers will vary with the live rate sheet, but the savings is typically in the 60-70% range for a single invocation and grows from there as the same cached prefix is read by additional tasks in the same cache window.

### Running cost-report.sh

`cost-report.sh` lives at `team/scripts/cost-report.sh`. With no flags it produces a month-to-date report for the current project.

The scope flags are mutually exclusive — pick at most one. Other flags compose freely with the scope.

| Flag | Argument | Purpose |
|---|---|---|
| `--month` | `YYYY-MM` | Report on a specific calendar month. |
| `--day` | `YYYY-MM-DD` | Report on a single day. |
| `--rc` | `vX.Y.Z` | Report on a single RC. |
| `--project` | `<name>` | Override `PGAI_PROJECT_NAME` (default: `pgai-agent-kanban`). |
| `--csv` | (none) | Emit CSV instead of the human-readable block. |

Example invocations, one per flag:

```bash
# Specific month
team/scripts/cost-report.sh --month 2026-05

# Single day
team/scripts/cost-report.sh --day 2026-05-16

# Single RC
team/scripts/cost-report.sh --rc v0.23.22

# Override project
team/scripts/cost-report.sh --project pgai-video-generator

# CSV output for spreadsheet analysis
team/scripts/cost-report.sh --month 2026-05 --csv
```

The script exits 0 on success (warnings on stderr are fine) and 1 on usage or configuration errors. If there is no data for the requested scope it prints `no data for <scope>` and exits 0.

### How to read the report

The human-readable report is organized as a header, then a sequence of blocks. The blocks are stable; absent data hides a block rather than re-shuffling layout. Read it from top to bottom: each block narrows from raw activity to per-dimension breakdowns to the decision data.

A canonical month-scope report looks like this:

```
=== Token Usage: May 2026 — pgai-agent-kanban ===
Days with activity: 12 / 16
RCs shipped:        7
Total invocations:  342

Token totals:
  Input (new):              4,128 tokens    $0.06
  Cache writes:         1,256,400 tokens    $23.56
  Cache reads:          9,884,200 tokens    $14.83
  Output:                  487,910 tokens   $36.59

By agent (cost share):
  CODER   $42.18 (56%)  — 14 invocations/day avg
  TESTER  $14.07 (19%)  — 4 invocations/day avg
  ...

By model:
  claude-opus-4-7              $75.04 (100%)

Total cost:         $75.04
Daily average:      $6.25
Projected monthly:  $187.62 (extrapolated from observed days)

Subscription comparison:
  Anthropic Pro programmatic credit ($20.00):   $168 short
  Anthropic Max programmatic credit ($200.00):  $12 surplus
  ...
```

Read it in this order:

**Header line.** `=== Token Usage: <scope> — <project> ===`. The scope label is whatever you asked for: `May 2026` for `--month`, `Day 2026-05-16` for `--day`, `RC v0.23.22` for `--rc`. The project name comes from `--project` or the default.

**Activity context.** Three lines orient you to how much work this scope represents:

- `Days with activity: N / M` — number of distinct days that produced any token usage, against the days elapsed in the scope. A 12-of-16 ratio means four days were quiet (no agent runs). Only shown for month scope.
- `RCs shipped: N` — distinct RC versions that closed during the scope. For day scope this becomes `RCs on this day: N`. For RC scope it becomes `RC: vX.Y.Z`.
- `Total invocations: N` — sum of `invocations` across every `tokens.json` in scope. One invocation is one `claude -p` call. A single task may produce multiple invocations if the agent restarted.

**Token totals (the four-category block).** This is the cache-aware breakdown described in the previous subsection. Each line shows a category, its token count, and its dollar cost:

- `Input (new):` — fresh `input_tokens`. Usually tiny in this framework. Multiplied by `input_per_1m`.
- `Cache writes:` — `cache_creation_input_tokens`. Multiplied by `cache_creation_per_1m` (1.25x input).
- `Cache reads:` — `cache_read_input_tokens`. Multiplied by `cache_read_per_1m` (0.10x input).
- `Output:` — `output_tokens`. Multiplied by `output_per_1m`.

The four dollar amounts sum to `Total cost` further down. If you see token counts but zero dollar amounts, either `token_pricing.json` is missing entries for the recorded models, or the roll-up file pre-dates the per-category cost fields (the report falls back to total-only display in that case — re-run the aggregator to refresh).

For legacy roll-up files written before the cache-aware aggregator landed, the block degrades gracefully: token counts still appear but dollar columns are omitted, and `Total cost` reflects the aggregator's stored total (which may itself be wrong if the legacy aggregator under-counted cache categories). Re-aggregate any RC or day you care about by deleting its roll-up file and re-running `cost-report.sh`; the missing roll-up triggers a fresh build from the per-task `tokens.json` files.

**By agent (cost share).** Per-agent dollar amount, percentage of total cost, and average invocations per day. Sorted by cost descending. This answers "which agent is the expensive one this scope." CODER usually dominates because it does the most work per task; significant share from any other agent (especially TESTER or PM running gap-analysis loops) is worth a look. The percentage uses the same `Total cost` figure shown below, so the numbers add up.

**By model.** Cost share per model name. Models appear here under their canonical IDs (`claude-opus-4-7`, `claude-haiku-4-5`, etc.) as captured from the provider's JSON response — not as the short aliases (`opus`, `sonnet`) used in the wake script. A model with `cost_usd=0` and a stderr warning means `token_pricing.json` is missing an entry for it; add the entry, re-run, the number appears. This is also where mixed-model strategies become visible: if some agents run Opus and others run Haiku, both lines show up here with their independent cost shares.

**Total cost.** The actual dollar figure for the scope. Always present. Equal to the sum of the four token-total category costs.

**Daily average / Projected monthly.** Only on month-scope reports. `Daily average` is `Total cost / days_with_activity`. `Projected monthly` is `daily_average * 30`. The projection is a linear extrapolation that assumes today's burn rate continues unchanged for the rest of the month — a quiet weekend or a heavy release week skews it. Day-scope and RC-scope reports show actual cost only and skip extrapolation.

**Subscription comparison.** For each subscription option in `token_pricing.json` under the `subscriptions` key, the report shows the dollar surplus or shortfall against projected (month) or actual (day, RC) cost. A "shortfall" means the credit would not cover the projected spend; you would pay the gap as API overage. A "surplus" means the credit covers the projected spend with margin. The `Cheapest if mixed` line at the end models a subscription plus API overflow strategy. Read this block as decision input, not a recommendation — the numbers are arithmetic; the choice is yours.

### Cross-checking the math by hand

When you need to verify the report is computing costs correctly — for example, after editing `token_pricing.json`, after a fix to the aggregator, or as a spot-check before quoting numbers in a provider decision — the math is straightforward enough to redo by hand from a single `tokens.json` record. The recipe is the same one the aggregator uses.

**Step 1 — pick a tokens.json record.** Any `projects/<name>/tasks/<task-id>/artifacts/tokens.json` will do. The example below uses a representative record:

```json
{
  "model": "opus",
  "provider": "claude",
  "agent": "coder",
  "input_tokens": 7,
  "output_tokens": 1200,
  "cache_creation_input_tokens": 11603,
  "cache_read_input_tokens": 44108,
  "invocations": 1,
  "elapsed_seconds": 151,
  "timestamp": "2026-05-16T13:14:32Z"
}
```

**Step 2 — resolve the canonical model ID.** The aggregator looks up rates in `token_pricing.json` by canonical model ID, not by short alias. Current records carry the canonical ID directly (e.g. `claude-opus-4-7`). If you are checking an older record with a short alias like `"opus"`, map it to the canonical ID the wake script would have used (Opus → `claude-opus-4-7`, Sonnet → `claude-sonnet-4-6`, Haiku → `claude-haiku-4-5`, subject to the rates currently in the pricing file).

**Step 3 — read the four rates from token_pricing.json.** Look up `providers.claude.models.<model_id>` and copy out the four `*_per_1m` values. Do not hardcode these from memory — open the live file. Example for one model (rates as of this writing; verify before using):

```bash
python3 -m json.tool team/scripts/lib/token_pricing.json | grep -A 1 'claude-opus-4-7'
```

**Step 4 — apply the per-category formula.** Each category cost is `tokens * rate_per_1m / 1_000_000`:

```
input_cost          = input_tokens                 * input_per_1m          / 1_000_000
cache_create_cost   = cache_creation_input_tokens  * cache_creation_per_1m / 1_000_000
cache_read_cost     = cache_read_input_tokens      * cache_read_per_1m     / 1_000_000
output_cost         = output_tokens                * output_per_1m         / 1_000_000
total_cost          = input_cost + cache_create_cost + cache_read_cost + output_cost
```

For the sample record above, plugging in the rates listed for `claude-opus-4-7` in the current `token_pricing.json` gives values on the order of:

- `input_cost`: a few hundredths of a cent (7 tokens × the input rate is barely visible)
- `cache_create_cost`: the largest line (`11,603 × 18.75 / 1M ≈ $0.218` at current rates)
- `cache_read_cost`: a few cents (`44,108 × 1.50 / 1M ≈ $0.066` at current rates)
- `output_cost`: a few cents (`1,200 × 75.00 / 1M = $0.090` at current rates)
- `total_cost`: a few tens of cents

If you re-run the calculation with whatever rates are in the live `token_pricing.json` at the time you read this, you will get the precise number that should appear in any roll-up that includes this task.

**Step 5 — compare against the report.** Find the same task in the relevant per-RC roll-up (`projects/<name>/usage/rc/<version>-tokens.json`, look in the `tasks` array for an entry with this task's agent and timestamp) and read the `cost_usd` field. It should match your hand calculation within rounding (the aggregator rounds to six decimal places). If it differs by more than rounding, suspect either a pricing-file mismatch (the rates the aggregator used differ from the rates you read), a model-name resolution failure (the aggregator could not find the model and silently used 0), or a stale roll-up file that pre-dates a pricing change.

For a coarser check, sum the `cost_usd` of every entry in a single RC's `tasks` array and compare against the RC's `totals.cost_usd`. They should agree. If they do not, the roll-up is internally inconsistent and re-aggregation is the fix: delete the roll-up file, re-run `cost-report.sh --rc <version>`, watch it rebuild from the per-task files.

### Keeping token_pricing.json fresh

Pricing lives in `team/scripts/lib/token_pricing.json`. The `cost-report.sh` script reads it from the project's `dev_tree_path` (per `project.cfg [project]`), falling back to the kanban root copy. Dollar amounts are never hardcoded in the script — the JSON is the source of truth.

The file has three top-level keys: `providers` (per-provider, per-model rates expressed as `*_per_1m`), `subscriptions` (subscription credit values used by the comparison block), and `updated` (an ISO date string). Bump the `updated` field whenever you change any rate or subscription value — it is the audit trail for "when did this pricing snapshot last reflect public list prices."

Update the file whenever:

- A provider publishes new list prices for an existing model.
- A new model is added that the kanban will use (otherwise the model shows `cost_usd=0` in reports and emits a stderr warning on first encounter).
- A subscription tier price or programmatic-credit value changes.

The file is plain JSON. Edit it, validate it parses (`python3 -m json.tool team/scripts/lib/token_pricing.json`), commit the change with the `updated` field bumped, and the next report will use the new rates.

### Linking cost data to provider decisions

The metrics layer exists because real numbers beat guesses for provider decisions. The operator's current option set spans Anthropic Pro/Max programmatic credit, Anthropic API direct (pay-as-you-go), Codex via ChatGPT Plus or Pro, and mixed strategies (subscription for the bulk of usage, API overflow above the credit cap). Per-agent provider selection is also on the table once the provider abstraction lands.

`cost-report.sh` does not prescribe an answer. It produces the data each option needs to be evaluated against — projected monthly burn, per-agent cost share (relevant if different agents are routed to different providers), and the explicit subscription-versus-API comparison block. Reading those numbers and choosing an arrangement is the operator's call.


## RC Abandonment: cm-cancel-rc.sh

When a Release Candidate needs to be abandoned before it ships — for example, when TESTER finds catastrophic gaps that warrant starting fresh, when the operator decides scope has changed significantly, or when an RC was opened for the wrong version — use `cm-cancel-rc.sh` to cleanly abandon it.

### When to use cm-cancel-rc.sh

Use `cm-cancel-rc.sh` in these situations:

- **TESTER finds catastrophic gaps** that cannot be patched without a full re-decomposition — the RC quality is too low to iterate on.
- **Operator decides scope change is needed** — the work decomposed under this RC is no longer the right work to do; fresh requirements are needed.
- **Wrong RC version opened** — `cm-open-rc.sh` was run with the wrong version number and the error must be undone before starting over.
- **RC branch became inconsistent** — partial failures or manual edits left the project's `release-state.md` or the branch in a state that cannot be recovered automatically.

Do NOT use `cm-cancel-rc.sh` for minor gaps. TESTER writes priority requirements documents for the next patch release when gaps are small enough to fix without abandoning the current RC.

### Usage

```bash
# Interactive (prompts for confirmation):
cm-cancel-rc.sh v0.15.4

# Non-interactive (--yes skips confirmation prompt):
cm-cancel-rc.sh v0.15.4 --yes
```

### What cm-cancel-rc.sh does

1. Validates the version format (`vX.Y.Z`).
2. Checks that the current branch is `develop` or `rc/<version>`.
3. Reads the project's `release-state.md` and verifies `Active RC` matches the requested version.
4. Lists any pending kanban tasks that reference this RC version (for awareness).
5. Prompts for confirmation (unless `--yes` is passed).
6. Deletes `rc/<version>` from origin (if it exists there).
7. Deletes `rc/<version>` locally (if it exists).
8. Clears `Active RC`, `RC Opened At`, and `RC Opened By Task` in the project's `release-state.md` back to `none`.
9. Prints a summary and lists tasks to manually mark as `WONT-DO`.

### What cm-cancel-rc.sh does NOT do

- Does NOT touch `main`.
- Does NOT delete any git tags.
- Does NOT automatically mark kanban tasks as `WONT-DO` — you must do that manually after running the script.
- Does NOT create a new RC — run `cm-open-rc.sh <new_version>` separately.

### Idempotency

`cm-cancel-rc.sh` is safe to re-run if it fails midway. Each step checks current state before acting:

- If `rc/<version>` is already gone from origin, the remote-delete step is skipped.
- If `rc/<version>` is already gone locally, the local-delete step is skipped.
- If `Active RC` is already `none`, the `release-state.md` reset is skipped.
- If all steps are already complete, the script reports "idempotent run" and exits 0.

### After running cm-cancel-rc.sh

1. Manually set any pending kanban tasks for the cancelled RC to `WONT-DO` in their `status.md`.
2. If needed, archive or remove the requirements document for the cancelled version.
3. Open a new RC when ready: `cm-open-rc.sh <next_version>`.

### Example: abandoning v0.15.4 and starting v0.15.5

```bash
# Abandon the current RC
cm-cancel-rc.sh v0.15.4 --yes

# Verify the cleanup
git ls-remote origin rc/v0.15.4   # should return nothing
grep -A1 "Active RC" "$KANBAN_ROOT/projects/<project-name>/release-state.md"  # should show "none"

# Open the next RC
cm-open-rc.sh v0.15.5
```

## RC Abandonment: cm-cancel-rc.sh

When a Release Candidate needs to be abandoned before it ships — for example, when TESTER finds catastrophic gaps that warrant starting fresh, when the operator decides scope has changed significantly, or when an RC was opened for the wrong version — use `cm-cancel-rc.sh` to cleanly abandon it.

### When to use cm-cancel-rc.sh

Use `cm-cancel-rc.sh` in these situations:

- **TESTER finds catastrophic gaps** that cannot be patched without a full re-decomposition — the RC quality is too low to iterate on.
- **Operator decides scope change is needed** — the work decomposed under this RC is no longer the right work to do; fresh requirements are needed.
- **Wrong RC version opened** — `cm-open-rc.sh` was run with the wrong version number and the error must be undone before starting over.
- **RC branch became inconsistent** — partial failures or manual edits left the project's `release-state.md` or the branch in a state that cannot be recovered automatically.

Do NOT use `cm-cancel-rc.sh` for minor gaps. TESTER writes priority requirements documents for the next patch release when gaps are small enough to fix without abandoning the current RC.

### Usage

```bash
# Interactive (prompts for confirmation):
cm-cancel-rc.sh v0.15.4

# Non-interactive (--yes skips confirmation prompt):
cm-cancel-rc.sh v0.15.4 --yes
```

### What cm-cancel-rc.sh does

1. Validates the version format (`vX.Y.Z`).
2. Checks that the current branch is `develop` or `rc/<version>`.
3. Reads the project's `release-state.md` and verifies `Active RC` matches the requested version.
4. Lists any pending kanban tasks that reference this RC version (for awareness).
5. Prompts for confirmation (unless `--yes` is passed).
6. Deletes `rc/<version>` from origin (if it exists there).
7. Deletes `rc/<version>` locally (if it exists).
8. Clears `Active RC`, `RC Opened At`, and `RC Opened By Task` in the project's `release-state.md` back to `none`.
9. Prints a summary and lists tasks to manually mark as `WONT-DO`.

### What cm-cancel-rc.sh does NOT do

- Does NOT touch `main`.
- Does NOT delete any git tags.
- Does NOT automatically mark kanban tasks as `WONT-DO` — you must do that manually after running the script.
- Does NOT create a new RC — run `cm-open-rc.sh <new_version>` separately.

### Idempotency

`cm-cancel-rc.sh` is safe to re-run if it fails midway. Each step checks current state before acting:

- If `rc/<version>` is already gone from origin, the remote-delete step is skipped.
- If `rc/<version>` is already gone locally, the local-delete step is skipped.
- If `Active RC` is already `none`, the `release-state.md` reset is skipped.
- If all steps are already complete, the script reports "idempotent run" and exits 0.

### After running cm-cancel-rc.sh

1. Manually set any pending kanban tasks for the cancelled RC to `WONT-DO` in their `status.md`.
2. If needed, archive or remove the requirements document for the cancelled version.
3. Open a new RC when ready: `cm-open-rc.sh <next_version>`.

### Example: abandoning v0.15.4 and starting v0.15.5

```bash
# Abandon the current RC
cm-cancel-rc.sh v0.15.4 --yes

# Verify the cleanup
git ls-remote origin rc/v0.15.4   # should return nothing
grep -A1 "Active RC" "$KANBAN_ROOT/projects/<project-name>/release-state.md"  # should show "none"

# Open the next RC
cm-open-rc.sh v0.15.5
```

## Cancelling an in-flight RC

This section is the operator playbook for unwinding an in-flight release candidate end-to-end with `team/scripts/unwind-rc.sh` and verifying the result with `team/scripts/verify-rc-state.sh`. Use it when a TESTER-blocked RC needs to be re-rolled rather than waived, when an RC was opened against the wrong version, or when the chain's state stores have drifted far enough that the next iteration cannot pick up cleanly.

The broader script suite is the successor to `cm-cancel-rc.sh`. The older script (covered in the section above) deletes branches and resets `release-state.md` only; it leaves task folders, queue caches, requirements files, PM plan markers, and bundled PRIORITY/BUG markers untouched. `unwind-rc.sh` walks all of those state stores in one pass and creates a recoverable backup before it does anything destructive. Reach for the new script when an RC has accumulated real material — task folders, queue activity, materialized plan markers. Reach for the older `cm-cancel-rc.sh` only when nothing has happened beyond `cm-open-rc.sh` and you want a minimal undo.

### When to cancel versus when to waive

Cancellation and waiver are the two ways out of a stuck RC. They are not interchangeable.

**Cancel** when restarting the RC is cheaper than fixing what's wrong in place:

- TESTER reports catastrophic gaps that span multiple decomposed tickets. The decomposition itself is wrong, not a single ticket.
- The operator has decided the scope is wrong and wants to re-author the requirements document before the chain produces anything more on this branch.
- `cm-open-rc.sh` was run with the wrong version number and the rest of the chain has not progressed past trivial work.
- The RC's state stores are inconsistent enough that `verify-rc-state.sh` reports multiple errors and the cheapest path forward is a clean re-roll.

**Waive** (per CM's `SHIP-WITH-CONCERNS` / `SHIP-WITH-SERIOUS-CONCERNS` ship policy) when the RC is materially complete and the residual gaps are small enough to fix in the next patch release:

- TESTER's findings are small, isolated, and addressable as PRIORITY items in the next RC.
- The release tag would still represent forward motion for downstream consumers.
- The fix effort to address concerns is `small` or `medium`, not `large`.

The waiver path stays inside the autonomous chain — CM applies it, ships the release, and TESTER's findings become priority requirements for the next RC. The cancel path requires the operator to halt, run scripts, drop a fresh requirements doc, and unhalt. Prefer waiver when the chain is producing usable output. Prefer cancel when the chain is producing the wrong output.

### Pre-flight checklist

`unwind-rc.sh` enforces these checks itself and refuses to run when any fails. Knowing them up front shortens the iteration when something is off.

1. **The system is halted.** Either the global `HALT` flag (`$PGAI_AGENT_KANBAN_ROOT_PATH/HALT`) or the per-project `HALT` flag (`$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT`) must exist before `unwind-rc.sh` will modify anything. This prevents the wake scripts from scheduling new work mid-cancellation. `--force` does not bypass this check.

2. **The version argument is the right version.** `unwind-rc.sh --project <name> --key <version>` confirms that `Active RC` in the project's `release-state.md` matches `<version>` before proceeding. The check exists because cancelling the wrong RC is hard to recover from and easy to do by typo. `--force` bypasses only this single check — useful for partial-cancellation recovery where `release-state.md` has already been reset but other stores remain.

3. **The version has not already shipped.** The script refuses to operate on any version that exists as a git tag in the dev tree. Shipped releases are immutable; if a tagged release is broken, the response is a new patch version, never a cancellation of the tag. `--force` does not bypass this check.

4. **The project exists and the dev tree is reachable.** `unwind-rc.sh` resolves the project name through `pp_require_project_context` and reads `dev_tree_path` from the project config. If either fails, the script exits before touching state.

To halt the entire kanban before cancelling:

```bash
touch "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
```

To halt only one project:

```bash
touch "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"
```

### Running unwind-rc.sh

The script lives at `team/scripts/unwind-rc.sh`. Its full signature is:

```
unwind-rc.sh --project <name> --key <vX.Y.Z> [--dry-run] [--force]
```

Start with `--dry-run`. It runs every pre-flight check, builds the full inventory of what would be modified, prints the plan, and exits 0 without changing anything. Reading the plan is the cheapest way to confirm the script will touch exactly what you expect.

```bash
team/scripts/unwind-rc.sh --project pgai-agent-kanban --key v0.26.3 --dry-run
```

The plan lists, in the order the script will execute them: backup destination, RC branch state (local and remote), task folders that will flip to `WONT-DO`, queue entries that will flip to `[x]`, the requirements file that will be renamed, PM plan markers that will be removed, PRIORITY and BUG backlog entries that will flip back to `[ ]`, the `release-state.md` reset, and any discovery-state cache files that will be removed.

When the plan matches your intent, run the script for real:

```bash
team/scripts/unwind-rc.sh --project pgai-agent-kanban --key v0.26.3
```

The interactive run prints the same plan, then prompts with `Proceed with cancellation of rc/<version>? Type 'yes' to continue:`. Type `yes` exactly — any other input aborts without modifying state. For scripted invocation, `--force` skips the prompt (and the Active-RC-mismatch check).

What the script touches, in execution order:

- **Backup.** Before any destructive step, the script copies `tasks/queues/`, `requirements/`, `priority/`, `release-state.md`, and every task folder referencing the cancelled version to `$(pgai_temp_dir)/unwind-rc-<version>-backup-<TIMESTAMP>/`. Restore is one `cp -a` away.
- **Git unwind.** Checks out `develop` in the dev tree, deletes the local `rc/<version>` branch (with `-D`, force-delete), deletes the remote `origin/rc/<version>`, and force-deletes any orphan `feature/CLAUDE-*` branches whose task IDs belong to the cancelled RC.
- **Task folders.** For every task folder whose README references the cancelled version, flips `## State` to `WONT-DO` and appends a `## Cancellation Note (TIMESTAMP)` block. Folders already carrying a Cancellation Note are skipped — the step is idempotent on re-run.
- **Queue caches.** Walks every `*_backlog.md` and flips `[ ]`, `[W]`, `[A]`, and `[B]` markers to `[x]` for any line referencing a cancelled task ID. Existing `[x]` markers are left alone.
- **Requirements file.** Renames the requirements document that produced the cancelled RC from `<version>-<slug>.md` to `<version>-<slug>.SUPERSEDED-on-cancel-<TIMESTAMP>.md`. The original content is preserved verbatim so the bundled PRIORITY/BUG list can still be read.
- **PM plan markers.** Removes `.materialized.*` files under `tasks/queues/plans/` whose contents reference the cancelled version's plan hash.
- **Priority backlog.** Reads the `## Bundled Items` section of the superseded requirements file, finds each `PRIORITY-NNNN` entry, flips its `[x]` marker back to `[ ]` in `priority_backlog.md`, and resets each priority file's `## Status` to `open` so discovery re-bundles it on the next iteration.
- **Bug backlog.** Same logic as priority — `BUG-NNNN` entries flip back to `[ ]` and each bug file's `## Status` resets to `open`.
- **release-state.md.** Resets `Active RC`, `RC Opened At`, and `RC Opened By Task` to `none`. Historical fields are preserved verbatim.
- **Discovery state cache.** Removes any files under `projects/<name>/.discovery-state/` whose names contain the cancelled version string.

Exit codes are deliberate:

- `0` — every step completed (or `--dry-run` succeeded). State is fully unwound.
- `1` — a pre-flight check failed (no HALT, wrong version, shipped tag, missing project) or the operator declined the confirmation prompt. No state was modified.
- `2` — partial completion. The backup directory was created but a later step failed. Inspect the script output for the failure point, fix the underlying issue, and either re-run (most steps are idempotent) or restore from backup (see "Restoring from backup" below).

### Verifying clean state with verify-rc-state.sh

After `unwind-rc.sh` exits 0, run `team/scripts/verify-rc-state.sh` to confirm the project's state stores are consistent with each other.

```bash
team/scripts/verify-rc-state.sh pgai-agent-kanban
```

The checker is read-only. It walks every state store and reports `OK`, `WARN`, or `ERROR` findings, then prints a summary line like `verify-rc-state: 0 errors, 1 warning, 24 ok`. By default only warnings and errors are printed; add `--verbose` to print every check.

The checks span six categories:

- **release-state.md consistency** — `Active RC` is `none` after cancellation; if non-`none`, the named `rc/<version>` branch must exist locally.
- **Queue ↔ folder consistency** — every non-`[x]` task ID in a backlog has a matching task folder, and every task folder has a matching queue entry.
- **Marker ↔ state consistency** — `[ ]` lines correspond to `BACKLOG`, `[W]` to `WAITING`, `[A]` to `WORKING`, `[B]` to `BLOCKED`, and `[x]` to `DONE` or `WONT-DO`.
- **Prerequisites resolution** — every task ID listed in any task README's `## Prerequisites` section resolves to a real task folder.
- **release-state.md ↔ git branch consistency** — when `Active RC` is `none`, no `rc/*` branches should exist locally (this is a warning, not an error — it may flag operator-staged work).
- **Bundle invariants** — every PRIORITY/BUG file referenced in a requirements bundle exists on disk, and every PRIORITY/BUG marked `[x]` in its backlog corresponds to a bundled requirements file.

Exit codes:

- `0` — no errors. Warnings, if any, are informational.
- `1` — one or more errors. The state stores are inconsistent and the next iteration may produce wrong work.
- `2` — pre-flight failure (bad arguments, project not found).

After a clean cancellation, `verify-rc-state.sh` should exit 0. If it reports errors, either re-run the failed `unwind-rc.sh` step (most steps are idempotent) or restore from backup and investigate before proceeding.

### Dropping the fresh requirements document for the re-roll

With state verified clean, the next step is to drop a new requirements document that PM will pick up on the next wake firing. The mechanics are the same as any operator-authored brief — the directory layout and the picking rule are documented in the `Filing a Document Brief` section above (for document workflows) and the `Workflow Types` and `Priority Queue Mechanics` sections (for release and feature workflows).

The short version for a release re-roll:

- Place the new file at `$KANBAN_ROOT/projects/<name>/requirements/<vX.Y.Z>-<slug>.md` (regular queue) or `requirements/priority/<vX.Y.Z>-<slug>.md` (priority queue).
- The filename's version must be greater than `pp_last_released_version` for the project, or it will be skipped as stale.
- The version may be the same as the cancelled RC's version. The cancelled requirements file has been renamed with `.SUPERSEDED-on-cancel-<TIMESTAMP>.md` and will not collide.
- The discovery pipeline picks the new file up at Step 3 on the next iteration and queues PM to materialize it.

Bundled PRIORITY and BUG items from the cancelled RC are back in their original `[ ]` state and their `## Status` headers are back to `open`. Discovery will re-bundle them into the next requirements document automatically — the operator does not have to list them by hand in the new brief unless the intent is to exclude some of them deliberately.

Once the new requirements file is in place, remove the HALT flag to resume the chain:

```bash
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
# or, for a per-project halt:
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"
```

The next wake firing picks up the new requirements document and the chain proceeds against the re-rolled RC.

### Common pitfalls

**Forgot to halt.** `unwind-rc.sh` refuses to run without a HALT flag in place. The check exists because the wake scripts will otherwise dispatch new work into a project whose state is being torn down underneath them, and the chain ends up in a much worse position than either a clean cancel or a clean continuation. The error message includes both `touch` commands. Pick the scope you want, create the flag, re-run.

**Wrong version argument.** The Active-RC-mismatch check refuses by default when `<version>` is not the value in `release-state.md`. The check exists because cancelling the wrong RC is almost always a typo. Re-read the script's error message — it prints both the requested version and the value found in `release-state.md`. Correct the argument and try again. Use `--force` only when you have genuinely intended to bypass this check (for example, when an earlier partial cancellation left `release-state.md` already at `none` but you need to clean up other stores referencing the old version).

**Partial completion (exit code 2).** When `unwind-rc.sh` exits 2, the backup at `$(pgai_temp_dir)/unwind-rc-<version>-backup-<TIMESTAMP>/` was created but a later step failed. Read the script's stderr to identify which step failed. Most steps are individually idempotent and can be re-run by re-invoking `unwind-rc.sh` (it will re-do the inventory and skip steps that have already completed). If the failure is structural — a permission error, a git operation that needs operator attention, an unexpected file format — restoring from backup is often faster than debugging in place.

**Restoring from backup.** The backup directory holds a snapshot of `tasks/queues/`, `requirements/`, `priority/`, `release-state.md`, and the matching task folders as they existed just before the destructive steps began. To restore byte-identical pre-cancellation state, copy the backup contents back into the project root:

```bash
cp -a $(pgai_temp_dir)/unwind-rc-<version>-backup-<TIMESTAMP>/. "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/"
```

The git unwind step (deleting the local and remote RC branch) is not covered by the backup — restoring branches requires either re-creating them from a reflog entry or re-running `cm-open-rc.sh` to cut a fresh branch. If branch state matters and a restore is likely, capture the branch SHAs before running `unwind-rc.sh` for real.

**Re-running after a partial run.** Every action step in `unwind-rc.sh` is written to be idempotent. Task folders that already carry a `## Cancellation Note` block are skipped. Queue entries already at `[x]` are left alone. The requirements file is only renamed if a non-superseded original exists. `release-state.md` fields already at `none` are not rewritten. The orphan-branch detection only flags branches whose task IDs are in the cancelled RC's task set. Re-running on a partially-cancelled RC converges to the same end state as a clean run.

**Cancelled the wrong RC.** If you discover after the fact that the wrong RC was cancelled, restore from backup, re-cut the RC branch with `cm-open-rc.sh`, and re-bundle the requirements file by renaming it back from `*.SUPERSEDED-on-cancel-*.md` to `<version>-<slug>.md`. The bundled PRIORITY/BUG entries will need to be re-flipped from `[ ]` back to `[x]` by hand — discovery will otherwise re-bundle them into a new requirements document on the next iteration. This is recoverable but tedious; the `--dry-run` pass exists to prevent it.

**Forgot to verify before unhalting.** Without `verify-rc-state.sh` between cancellation and unhalt, an inconsistency missed by `unwind-rc.sh` propagates into the next iteration as wrong work. The chain may produce tasks against stale state, fail in unexpected places, or — worse — produce work that materially conflicts with the next RC's intent. The verify step takes seconds. Run it every time.

## cm-open-rc / cm-release Recovery

The historical "uncommitted release-state.md" failure class no longer exists. `release-state.md` lives at `$KANBAN_ROOT/projects/<project-name>/release-state.md` (the install location, not the dev-tree's `team/`). It is not version-controlled. There is no git-commit step for `cm-open-rc.sh` to fail mid-way through, and `cm-release.sh` does not guard against uncommitted changes to it.

If CM scripts fail mid-run today, the symptom surface is one of:

- **`Active RC` is set but the RC branch does not exist on origin.** `cm-open-rc.sh` exited between writing the file and creating the branch. Re-run `cm-open-rc.sh <version>` — it is idempotent and will detect the existing `Active RC` value, skip the file write, and complete the branch creation.
- **The RC branch exists on origin but `Active RC` is `none`.** The file write failed but the branch push succeeded. Re-run `cm-open-rc.sh <version>` — it will detect the existing branch, write the file, and complete.
- **Neither side completed.** Re-run `cm-open-rc.sh <version>` from a clean state.

If a recovery situation falls outside these patterns, run `cm-cancel-rc.sh <version>` to clear in-flight state and reopen.

## Completing a Manually Unblocked Ticket

When a human performs work that the automated system considers BLOCKED -- for example, manually running a release script, manually resolving a dependency, or directly applying a fix -- the system state will not update itself. The ticket will remain in BLOCKED state even though the work is done. This section describes how to reconcile the record.

### When this applies

Use this procedure when ALL of the following are true:

- A task's `status.md` shows `State: BLOCKED`
- The work described in the task's `README.md` has been completed by a human (or by a process outside the automated agent pipeline)
- No automated agent completed the task through the normal WORKING -> DONE path

### When this does NOT apply

This is **not** a "TESTER waiver" procedure. The old waiver pattern — operator flipping a TESTER task from `BLOCKED` to `DONE` after deciding a found bug was acceptable — is no longer used. TESTER no longer reaches `BLOCKED` for found bugs, stale assertions, pre-existing failures, or gaps. TESTER files those via Path C and continues to `DONE` with a `SHIP-WITH-CONCERNS` or `SHIP-WITH-SERIOUS-CONCERNS` recommendation. CM applies ship policy.

If you see a TESTER task in `BLOCKED` state, it means verification literally could not complete (pre-flight failure, runner crash, missing requirements doc, unreachable dev tree). The fix is to repair the infrastructure problem and re-run TESTER — not to flip the state by hand. Manually marking a `BLOCKED` TESTER task as `DONE` would discard the signal that verification did not happen and silently bypass the chain's halt mechanism.

For CM tasks in `BLOCKED` state, see the "When the chain halts" section above. CM blocks itself when it creates an autonomous HALT; the operator's job is to investigate the trigger, resolve the underlying issue, remove the HALT file, and re-run (or supersede) the CM task. The procedure below applies only when the operator has independently completed the underlying release work outside the pipeline.

### Step-by-step procedure

1. **Open the task's `status.md`** in the relevant task folder.

2. **Set `State` to `DONE`.**

   ```
   ## State
   DONE
   ```

3. **Set `Needs Human` to `no`.**

   ```
   ## Needs Human
   no
   ```

4. **Clear `## Blockers`** or set it to `none`. The blocker has been resolved.

   ```
   ## Blockers
   none
   ```

5. **Update `## Summary`** to briefly describe what was done and by whom (e.g., "Manually completed by human on 2026-04-27. Release script was run by hand.").

6. **Update the queue marker.** Find the task's entry in the appropriate queue backlog file (e.g., `team/tasks/queues/cm_backlog.md`). Change the marker from `[B]` to `[x]`:

   Before:
   ```
   [B] TASK-ID — short description
   ```

   After:
   ```
   [x] TASK-ID — short description
   ```

7. **Commit the changes** to the task's `status.md` and the queue backlog file together:

   ```bash
   git add team/tasks/queues/cm_backlog.md
   git add team/tasks/<TASK-ID>/status.md
   git commit -m "Mark <TASK-ID> DONE (manually completed by human)"
   git push origin <branch>
   ```

### Why this matters

Stale BLOCKED entries cause downstream confusion: PM agents may misread the state, wake scripts may attempt to re-process a finished task, and queue depth counts will be inaccurate. Keeping queue markers and `status.md` synchronized with actual work state ensures the system remains an accurate record of truth.

### Do not use WONT-DO

If the work was genuinely completed, use `DONE`. `WONT-DO` means the work was intentionally skipped or declined. Using `WONT-DO` for completed work misrepresents the history.

## Liberal Regex Principle

Parsers and regex patterns in this system should be liberal in what they accept and strict in what they produce. This follows from Postel's Law applied to text parsing: be conservative in what you generate, be liberal in what you consume.

### What this means in practice

When a script or parser reads markdown fields, status files, queue markers, or any structured text, it should tolerate common formatting variations:

- **Liberal whitespace (`\s+`)** — Match one or more whitespace characters instead of exactly one space. A human editing a markdown file might use two spaces, a tab, or mixed whitespace between tokens.

  ```
  # Strict (fragile):
  ^## State WORKING$

  # Liberal (robust):
  ^##\s+State\s+WORKING\s*$
  ```

- **Liberal newlines (`\n+`)** — Match one or more newlines between sections instead of exactly one. Editors and humans often leave extra blank lines between blocks.

  ```
  # Strict (fragile):
  ^## State\nWORKING\n## Summary

  # Liberal (robust):
  ^## State\n+WORKING\n+## Summary
  ```

- **Flexible identifiers** — Accept dots, dashes, and underscores interchangeably in identifiers where the semantic meaning is unambiguous. A task ID like `CLAUDE-20260427-007` should match whether written with dashes or underscores.

- **Optional trailing whitespace** — Markdown headings and field values may have trailing spaces or tabs. Always strip or ignore trailing whitespace when parsing.

  ```
  # Strict (fragile):
  ^## State$

  # Liberal (robust):
  ^##\s+State\s*$
  ```

### Production vs. consumption distinction

The liberal principle applies only to **parsing (consumption)**, not to **generation (production)**.

- When **reading** a file, accept variations. Use `\s+` instead of a literal space. Use `\n+` instead of a single newline.
- When **writing** a file, produce clean, canonical output. Use exactly one space, exactly one newline between sections, no trailing whitespace.

This asymmetry keeps files clean while preventing brittle parsers from breaking on harmless formatting differences.

### Real-world instance: Bug 9

Bug 9 was caused by a wake script regex that expected an exact single newline between a markdown heading and its value. When an agent wrote the field with an extra blank line, the regex failed to match and the wake script could not read the task state. The fix was to replace `\n` with `\n+` in the parsing regex — a one-character change that eliminated an entire class of failures.

### Summary rule

| Context | Rule |
|---------|------|
| Parsing (reading files) | Use `\s+`, `\n+`, and `\s*$` to tolerate formatting variation |
| Generating (writing files) | Produce exactly one space, one newline, no trailing whitespace |
| Identifiers | Accept dots, dashes, underscores when semantically unambiguous |
| Markdown headings | Strip or ignore trailing whitespace after heading text |

## Path Canonicalization Rule

The system operates across three distinct path contexts. Each context has a canonical path source that scripts and agents must use. Mixing path contexts causes subtle bugs where an agent reads stale state or writes to the wrong location.

### The three path contexts

| Context | Canonical source | What lives there |
|---------|-----------------|------------------|
| **Kanban operations** | `$PGAI_AGENT_KANBAN_ROOT_PATH` | Task folders, queue backlogs, status files, project `release-state.md` (in-flight RC tracker), HALT flag |
| **Git operations** | Explicit branch refs via `git show`; tag queries via `pp_last_released_version` | Version-controlled governance files (`team/SOP.md`), scripts, tests, release tags on `main` |
| **Dev tree (working directory)** | The path in `## Working Directory` per task | Where code lives on disk during active work; a checked-out git worktree |

### Kanban operations use the environment variable

All kanban operations — reading queue backlogs, updating `status.md`, checking the HALT flag, resolving task folder paths — use `$PGAI_AGENT_KANBAN_ROOT_PATH` (defaulting to `$HOME/pgai_agent_kanban`).

This variable is the single source of truth for the kanban tree location. Scripts must never hardcode `~/pgai_agent_kanban` or any other assumed path. Always reference the environment variable.

### Git operations use explicit branch refs

When a script needs to read version-controlled state from a specific branch (for example, a governance file on `develop` or an RC branch), it should use `git show` with an explicit branch reference:

```bash
# Read a governance file from the develop branch without switching branches
git show develop:team/SOP.md

# Read a file from an RC branch
git show rc/v0.15.2:team/SOP.md
```

This avoids depending on which branch is currently checked out in the working directory.

For "what version are we at" queries, scripts call `pp_last_released_version "<project>"` rather than parsing any file. The helper queries git tags on `origin/main` directly. See "Release State File" above.

For in-flight RC state (`Active RC`, `RC Opened At`, `RC Opened By Task`), scripts read the project's `release-state.md` at `$KANBAN_ROOT/projects/<project-name>/release-state.md`. This file is per-install, not version-controlled, and is owned by the CM scripts.

### The dev tree is where code lives

The working directory (`## Working Directory` in each task README) is where agents check out code, make edits, run tests, and commit changes. It is a git worktree — the on-disk representation of a branch.

The dev tree is not authoritative for in-flight RC state. An agent should not look for `release-state.md` inside the dev tree — it is not there. The canonical location is `$KANBAN_ROOT/projects/<project-name>/release-state.md`. For "what version are we at" queries, call `pp_last_released_version` instead of parsing any file.

### Why this matters

The separation prevents a common failure class: an agent reads a governance file from the dev tree, which happens to have a feature branch checked out, and gets stale or branch-specific data instead of the canonical value from `develop` or the RC branch. By routing each operation through its canonical path source, the system avoids cross-context contamination.

## Documentation Discipline Rule

Every ticket that introduces or modifies a tunable, environment variable, configuration value, script behavior, or team convention must also update the corresponding documentation artifact in the same commit or PR. Documentation is not a follow-up step — it is part of the definition of done.

### Coverage map

| What the ticket adds or changes | What must be updated |
|---------------------------------|----------------------|
| New environment variable or tunable | Corresponding `env_example` or `.env.example` file |
| New configuration value or config block | Corresponding example configuration file |
| New or changed script behavior | `README` or inline usage comment for that script |
| New team convention or operating rule | This `SOP.md` file |

### Enforcement

A ticket is not complete — and must not be marked `DONE` — until all applicable documentation updates from the table above are included.

- **CODER agents** must include documentation updates in the same feature branch as the code change. Do not defer documentation to a follow-up ticket.
- **TESTER agents** must verify that any ticket adding a tunable, env var, or config also updates the corresponding example file. If the example file was not updated, record it as a **gap** in `artifacts/gaps.md`.
- **PM agents** must include documentation update requirements in task acceptance criteria whenever the task scope touches a tunable, env var, config, or convention.

### Rationale

Documentation drift is a compounding failure: each undocumented tunable makes the next operator's job harder, and the cost grows with time. Requiring documentation in the same change keeps the example files and SOP accurate at the moment they are most likely to be reviewed — during code review of the change that introduced them.

## Per-Repo Wake Lock

The wake scripts use a per-project repo flock to prevent unsafe concurrent execution against a shared git working directory.

### The per-project repo flock

For each project it processes, the wake script acquires a `flock -n` on a per-project lock file:

```
locks/repo-wake-<project-repo-id>.lock
```

The lock directory is `$KANBAN_ROOT/locks/`. The `<project-repo-id>` segment is derived from the project name, so each project gets its own lock file.

The flock is held as an open file descriptor for the entire time that project's task loop runs, including the subagent invocation. When the script exits (for any reason, including signals), the file descriptor closes and the lock releases automatically. No cleanup step is required.

### Why the lock

The repo lock prevents two different agent roles from running concurrently against the same project's git working directory. Without it, a coder agent and a writer agent could run simultaneously, both committing to the same working tree and producing a corrupted git history.

### Per-project parallelism

Because the lock is keyed by project, agents operating on different projects can run in parallel without contention — only agents operating on the same project are serialized by that project's repo lock. No per-project configuration of the lock is required; the wake script derives the lock path from the project name automatically.

### Lock behavior on conflict

When an agent attempts to acquire a project's repo lock and finds it already held:

```
[timestamp] project <project>: another agent holds repo lock, skipping this project
```

The script skips that project and moves on; the cron scheduler re-invokes the wake on the next cycle. This is the correct behavior — a brief skip is preferable to contention or queue corruption.

### Cleanup on exit

No explicit cleanup step is required. The flock releases when its file descriptor closes, which happens automatically on process exit (for any reason, including signals). A stale lock file left on disk is harmless — the lock is the held descriptor, not the file's existence, so the next invocation acquires it cleanly the moment the prior holder terminates.

## Queue Marker Correctness

Every line in a backlog file carries a marker that reflects the current state of the task. The wake script reads and writes these markers to synchronize the queue file with each task's `status.md`. Correct markers are essential: PM agents and dashboard scripts use them to compute queue depth and progress.

### Canonical marker values

| Marker | State |
|--------|-------|
| `[ ]` | BACKLOG — pending, ready to pull |
| `[W]` | WAITING — prerequisites not yet satisfied |
| `[B]` | BLOCKED — hard blocked, requires human intervention |
| `[x]` | DONE or WONT-DO — terminal state |

The `[ ]` marker is the canonical BACKLOG marker. Its alias `[]` (no space) is also accepted by parsers but the wake script always writes `[ ]`. Do not use other characters inside the brackets — any unlisted value is treated as an error.

### Re-check cascade on task selection

Before invoking an agent, the wake script reads the task's `status.md` to verify the actual state. The queue marker is treated as a hint, not ground truth. This re-check catches drift between the marker and the real state — for example, a task that was manually promoted or a marker that was written incorrectly.

The cascade works as follows:

1. The wake script scans the backlog file for the first `[ ]` entry.
2. It reads `status.md` for that task and gets the current `## State` value.
3. If the state is not `BACKLOG`, the marker is corrected and the task is skipped:
   - `BLOCKED` → marker updated to `[B]`
   - `WAITING` → marker updated to `[W]`
   - `DONE` or `WONT-DO` → marker updated to `[x]`
   - `WORKING` → marker updated to `[B]` and a log message is emitted noting the task was stuck
4. If the state is `BACKLOG`, the wake script checks prerequisites. If prerequisites are unmet, the state is set to `WAITING`, the marker is updated to `[W]`, and the task is skipped.
5. If the state is `BACKLOG` and all prerequisites are satisfied, the agent is invoked.

This re-check prevents the system from acting on stale queue data. It also self-corrects queue files that were manually edited or written with incorrect markers.

### WAITING auto-promotion

The wake script also runs a WAITING promotion pass at the end of each wake cycle. For every `[W]` entry in the backlog, it re-evaluates prerequisites. If all prerequisites are now satisfied, the task's state is updated to `BACKLOG` and the marker is updated to `[ ]`. This promotion happens without human intervention and is how WAITING tasks re-enter the active queue.

The net effect: a task that enters WAITING because of an unsatisfied prerequisite will automatically become eligible again as soon as that prerequisite reaches DONE or WONT-DO — whichever comes first.

## Autonomous Lifecycle

Starting with v0.17.0, the kanban operates autonomously by default. The primary operator workflow is: **drop a requirements brief, walk away, wake up to a tagged release.** The system scans for queued work, decomposes it, builds it, tests it, and ships it — all driven by cron and file conventions.

### How the autonomous scan works

When the wake script fires for PM via cron (`scripts/wake/<provider>.sh --agent=pm`) and `pm_backlog.md` is empty (no PM work already queued), the wake script runs an autonomous scan before exiting:

1. **Guard checks.** If Active RC is not `none`, exit — an RC is already in flight and PM must not decompose new work. If the HALT flag is present, exit.
2. **Scan the priority queue.** List `.md` files in `projects/<name>/requirements/priority/` whose filenames contain a parseable version (`vX.Y.Z`). Filter to versions greater than Last Released. Sort by parsed semver version ascending (lowest first), then filename ascending as tiebreaker. Group eligible docs by Target Version — if multiple priority docs share the lowest eligible version, take all of them as combined inputs (TESTER may write several priority docs at the same version when surfacing multiple gaps).
3. **Scan the regular queue.** If no priority docs are eligible, list `.md` files in `projects/<name>/requirements/` (non-recursive). Same filename-version filter, same semver sort. At most one is selected — if multiple regular briefs share the same version, the lexically-first filename wins (this is operator error; rename the other brief).
4. **Drop a self-ticket.** If either scan found eligible work, create a PM task folder with `README.md` and `status.md`, append it to `pm_backlog.md`, and log the pickup. The wake script's next loop iteration picks up the just-created ticket and invokes PM normally.
5. **Both queues empty.** Exit cleanly. No error, no ticket.

### Priority queue vs regular queue

| Behavior | Priority queue (`projects/<name>/requirements/priority/`) | Regular queue (`projects/<name>/requirements/`) |
|---|---|---|
| Who writes docs | TESTER (gap remediation, bug reports) | Human operator (feature briefs) |
| Precedence | Checked first; wins over regular queue | Checked only if priority queue has no eligible docs |
| Same-version handling | All docs at the lowest eligible version are merged into one PM decomposition (combined inputs) | Lexically-first filename wins; others are skipped with a warning (operator error) |
| Sort order | Semver ascending, then filename ascending | Semver ascending, then filename ascending |

### Filename convention

Requirements filenames **must contain a parseable `vX.Y.Z` version string** somewhere in the name. The autonomous scan extracts the version using a liberal regex (`v\d+\.\d+\.\d+`), taking the first match. Files without a parseable version are silently skipped.

Good filenames:

```
v0.18.0.md
v0.18.0-feature-multi-project.md
v0.17.1-bugfix-cascade.md
```

Bad filenames (skipped by autonomous scan):

```
feature-multi-project.md        # no version — invisible to scanner
next-release.md                 # no version
```

### Active RC and HALT guards

The autonomous scan respects the same guards that PM's pre-flight checks enforce:

- **Active RC guard.** If `## Active RC` in the project's `release-state.md` is not `none`, the scan exits immediately. Only one RC at a time. The next PM cron firing after the RC ships will pick up the next queued brief.
- **HALT flag.** If `${TEAM_ROOT}/HALT` exists, the scan exits immediately. The operator must remove the HALT file to resume autonomous operation.

### Multi-RC queueing

Drop multiple briefs at once and the system processes them sequentially, lowest version first:

```bash
# Drop three briefs — PM picks them up in semver order
cp v0.18.0-brief.md $KANBAN/projects/<name>/requirements/v0.18.0.md
cp v0.19.0-brief.md $KANBAN/projects/<name>/requirements/v0.19.0.md
cp v0.20.0-brief.md $KANBAN/projects/<name>/requirements/v0.20.0.md
# Walk away. Cron does the rest.
```

Each RC builds sequentially: PM picks up v0.18.0 → CODER/WRITER implement → TESTER verifies → CM releases → PM wakes again with empty pm_backlog → autonomous scan finds v0.19.0 → repeat. The Active RC guard naturally serializes the pipeline.

Don't mix major version jumps with patches mid-stream: if v0.17.x patches are pending and v0.18.0 is queued, the patches ship first (lowest version wins).

### pm-agent.sh as convenience kicker

`pm-agent.sh` continues to work exactly as before. The difference is when to use it:

- **Default flow (autonomous).** Drop a brief in `projects/<name>/requirements/vX.Y.Z.md`. Walk away. PM picks it up on the next cron firing when `pm_backlog` is empty.
- **Convenience flow (immediate).** Drop a brief AND run `pm-agent.sh <brief>` to skip the cron wait. This creates a PM ticket immediately, which the wake script processes on its next firing.

Both flows produce the same result — a PM ticket pointing at the brief. The autonomous scan from the wake script and `pm-agent.sh` create identical self-tickets. The only difference is timing: cron-driven vs immediate.

Use `pm-agent.sh` when you want to kick off a build right now instead of waiting for the next cron cycle. For queued multi-RC workflows, just drop the briefs and let cron handle it.

### Operator workflow examples

**Single release (autonomous):**

```bash
# Write your brief, drop it, done
cp ~/my-brief.md $KANBAN/projects/<name>/requirements/v0.18.0.md
# Walk away. Next PM cron firing picks it up.
```

**Single release (immediate kick):**

```bash
# Drop the brief and kick PM immediately
cp ~/my-brief.md $KANBAN/projects/<name>/requirements/v0.18.0.md
$KANBAN/team/scripts/pm-agent.sh $KANBAN/projects/<name>/requirements/v0.18.0.md
```

**Multi-release cascade:**

```bash
# Drop multiple briefs at once — PM processes them in version order
cp v0.18.0-feature-a.md $KANBAN/projects/<name>/requirements/v0.18.0.md
cp v0.19.0-feature-b.md $KANBAN/projects/<name>/requirements/v0.19.0.md
cp v0.20.0-feature-c.md $KANBAN/projects/<name>/requirements/v0.20.0.md
# Walk away. Wake up to three tagged releases.
```

**HALT mid-cascade:**

```bash
# Pause between RCs
touch $KANBAN/HALT
# ... investigate, fix, review ...
rm $KANBAN/HALT
# Cron resumes from where it stopped.
```

### Version-compare correctness

All version comparisons in the autonomous scan (and throughout the system) use semver-aware comparison via `team/scripts/lib/semver.sh` (shell) and `team/pm-agent/lib/semver.py` (Python). Naive string comparison of version strings is incorrect — `"v0.9.7" > "v0.17.1"` under lexicographic ordering, but `v0.9.7 < v0.17.1` under semver.

The semver library is the single source of truth for version ordering. Role files (PM, CM, TESTER) reference these helpers explicitly. LLMs comparing version strings "naturally" (as text) are unreliable, which is why the library exists — always call the helper, never compare versions inline.

Key functions:

- `semver_lt`, `semver_lte`, `semver_gt`, `semver_gte`, `semver_eq` — shell comparison functions
- `semver_from_filename` — extracts `vX.Y.Z` from a filename (used by the autonomous scan)
- Python equivalents in `team/pm-agent/lib/semver.py`: `lt()`, `le()`, `gt()`, `ge()`, `eq()`, `from_filename()`

All functions accept versions with or without the `v` prefix, per the liberal regex principle.

## Cron and Wake Cadence

Cron drives the chain at a fixed cadence — every 2 minutes per agent by default, with a sub-minute stagger so agents in the same 2-minute window do not collide. Each firing runs one main-loop pass per registered project: pick the next BACKLOG task, run the agent, repeat until the project's backlog is empty or a stop condition trips. When the loop has no more BACKLOG tasks and PM is operating in `automatic` mode, the autonomous scan (`discovery_run_pipeline`) fires before the firing exits — it inspects bugs, priority requirements, and regular requirements for fresh work and may queue a new PM task.

Without further machinery, a PM task queued by the autonomous scan would sit in the just-emptied backlog until the next cron tick before being processed. For the common operator workflow ("drop a bug, see what happens, drop another") that wait time compounds across iterations.

### Same-firing post-discovery processing

When `discovery_run_pipeline` queues a fresh PM task and the chain is genuinely idle, the wake script re-enters the main loop body **exactly once** within the same firing to process that task. The fresh-task scenario therefore converges in well under a minute after a cron tick, not 5+ minutes.

The post-discovery iteration sits in `run_project_chain` immediately after the autonomous scan block. It is bounded to one extra iteration — not a loop. After that single iteration, the project's per-firing work is complete and the next batch of decomposed downstream tickets (CODER, WRITER, TESTER, CM) waits for the next cron tick belonging to the appropriate agent.

The trigger is the discovery library's `DISCOVERY_LAST_STATUS=produced_work` signal. That signal fires whenever Step 3 of the pipeline queued PM. The same signal can also fire when Step 1/2 merely bundled work under an active RC and Step 3 was gated, so the post-discovery iteration **does not trust `produced_work` alone** — it re-checks the safety conditions before re-entering.

### Three safety gates

Before the post-discovery iteration fires, three explicit gates must all clear. If any gate fails, the wake script logs the reason, skips the iteration, and falls back to the normal cron cadence (the next firing handles the work). The gates are intentionally redundant — the cost of a wrong re-entry during an RC in flight is much higher than the cost of waiting one cron interval.

1. **HALT re-check.** If `${TEAM_ROOT}/HALT` exists at this point in the firing, skip. The flag may have been created between the start of the autonomous scan and the post-discovery check, and HALT must be honored as soon as it is observed.
2. **Active RC re-check.** If the project's `release-state.md` shows `## Active RC` other than `none`, skip. An RC is in flight and PM must not decompose new work mid-flight. Discovery Step 3 also gates on this, but the post-discovery iteration re-checks as defense in depth — the state could have changed between Step 3's gate evaluation and this point.
3. **`process_one_task` natural empty-queue guard.** Even after the first two gates pass, `process_one_task` only runs when a real BACKLOG task is selectable from the project's queue. If none is found, the call is a no-op and the loop exits cleanly without firing an agent.

The three-gate design preserves a key invariant: the post-discovery iteration cannot process a task during an Active RC, cannot run while HALT is set, and cannot fire on a phantom queue entry. Any failure mode falls back to "wait for the next cron tick" — the worst outcome of a gate trip is one cron interval of latency, never incorrect execution.


### Operator implications

- Drop a bug or priority requirement: the next PM cron firing decomposes it and processes the first task in the same firing.
- Drop a brief mid-cascade while an RC is in flight: discovery's Step 3 is gated on Active RC and so is the post-discovery iteration; the new brief waits until the current RC ships.
- Set HALT mid-firing: the post-discovery iteration honors HALT even if the autonomous scan already started, so a late HALT takes effect within seconds rather than across firings.
- The same gating logic applies to `pm-agent.sh --auto` (the convenience kicker) so the autonomous and immediate paths behave identically.

## Provider Switch

The kanban supports running its agent chain on more than one LLM provider against the same
task queues, on the same machine, without restart and without data migration. Claude and Codex
are first-class providers; Gemini is reserved as a future slot. Which provider actually does
work at any given moment is controlled by a single config value.

### The switch key

`kanban.cfg` `[providers] active` is the single source of truth for the active provider.
Valid values are `claude`, `codex`, and `gemini`. On a fresh install the key is seeded from
`[providers] default` (default `claude`); upgrades never overwrite an operator's existing
value, so the choice is preserved. The legacy `$KANBAN_ROOT/active-provider` file is retired
and is no longer read or written.

When the key is unset, empty, whitespace-only, or contains an unrecognized value, the
framework defaults to `claude` and logs a warning to stderr. Input is case-insensitive and
surrounding whitespace is stripped — `Codex` and ` CODEX ` both resolve to `codex`.

### Reading the current value

`read_active_provider <kanban_root>` in `team/scripts/lib/active_provider.sh` is the
canonical helper. Source it into any bash context that needs to know the active provider:

```bash
source "$KANBAN_ROOT/scripts/lib/active_provider.sh"
ACTIVE_PROVIDER="$(read_active_provider "$KANBAN_ROOT")"
```

The helper always returns a valid name (`claude`, `codex`, or `gemini`) and exits zero.
It performs a file read, a `tr -d '[:space:]'`, a `tr` to lowercase, and a `case`
statement — designed for sub-50-millisecond execution because every wake script calls it
on every cron tick.

`show-queues.sh` prints `Active provider: <name>` as its first output line so the operator
can verify the current setting at a glance without opening a file.

### Switching providers

The recommended path is the convenience wrapper:

```bash
$KANBAN_ROOT/scripts/switch-provider.sh --provider codex
```

The wrapper validates the provider name, refuses to switch to any provider whose CLI
binary is not in `PATH` (loud error, non-zero exit), rewrites the `[providers] active` key
in place (preserving comments and unrelated keys), and prints a confirmation showing the
old and new values along with the file it wrote to and the CLI binary it confirmed. The
change takes effect on the next cron tick, typically within 1–2 minutes.

Editing `kanban.cfg` by hand is also supported — set `[providers] active = codex` directly.
The hand-edit path has no CLI-presence guard, so use it when you have a specific reason to
bypass the wrapper (scripting, recovery, intentionally setting a provider with no installed
CLI to halt all work).

Switch back the same way:

```bash
$KANBAN_ROOT/scripts/switch-provider.sh --provider claude
# or edit kanban.cfg directly:
#   [providers]
#   active = claude
```

### Fast inactive-exit contract

Both provider dispatchers (`scripts/wake/claude.sh` and `scripts/wake/codex.sh`) read
`[providers] active` immediately after argument parsing, before sourcing any provider-specific
code, acquiring any flock, or reading any queue. The inactive script exits in under one second from cron-firing to process-exit.

This is a hard contract. The crontab installs entries for every provider on every tick;
the cheapness of the inactive-exit path is what keeps the unused entries from consuming
real machine resources. If a future provider adds a long initialization step before the
active-provider check, that contract has been broken and the change needs to be moved
later in the wake script.

Verifying the contract is straightforward when needed:

```bash
$KANBAN_ROOT/scripts/switch-provider.sh --provider claude
time $KANBAN_ROOT/scripts/wake/codex.sh --agent=pm   # should report < 1.0 second
```

### What happens to an in-flight task on switch

The wake scripts read `[providers] active` once per firing, not per task within a firing. A
task already in `WORKING` state at the moment the value is switched runs to completion under
the original provider; the switch takes effect on the next cron tick. Operators who need a
hard stop should set `HALT` first, wait for the chain to drain, then switch.

### Calibration is iterative, not gating

Role files are written in plain English and depend on the LLM's interpretation. Switching
providers may surface subtle behavioral differences — a role that ships cleanly under
Claude may produce slightly different output under Codex. v0.27.0 deliberately treats those
differences as follow-up work: file calibration bugs as they appear, ship patch RCs
(v0.27.1+) to adjust role-file prompts. Calibration regressions do not gate v0.27.0; the
substrate that enables provider switching is the achievement here.

## PM Mode Control

The autonomous scan described above can be toggled between two modes via the `PGAI_KANBAN_PM_MODE` environment variable.

### Modes

| Mode | Behavior |
|---|---|
| `automatic` | Default. When PM's backlog is empty, the wake script runs the autonomous scan — scanning `requirements/` and `requirements/priority/` for eligible briefs and creating PM self-tickets. This is the fully autonomous pipeline. |
| `manual` | The autonomous scan is disabled. PM still processes tickets already in `pm_backlog.md` normally — it just does not create new ones from requirements docs on its own. |

### Default behavior

When `PGAI_KANBAN_PM_MODE` is unset, the system defaults to `automatic`. This preserves autonomous behavior for installations that do not set the variable.

### How it works

The wake script checks PM mode after PM finishes processing its backlog. The actual guard is:

```bash
if [[ "$AGENT" == "pm" && "$STOP_REASON" == *"no more BACKLOG tasks"* && "${PGAI_KANBAN_PM_MODE:-automatic}" != "manual" ]]; then
```

When mode is `manual`, the wake script skips the autonomous scan entirely and exits cleanly. All other PM behavior — processing tickets from `pm_backlog.md`, pre-flight checks, decomposition — is unaffected.


### How to set it

Set `PGAI_KANBAN_PM_MODE` through any of the standard configuration channels:

- **`kanban.cfg`** `[chain] pm_mode` key (recommended for permanent settings; replaces the old `config.cfg` approach)
- **`env`** file in the kanban root (sourced by wake scripts; set `PGAI_KANBAN_PM_MODE=manual`)
- **`~/.bashrc`** or shell profile (user-wide)
- **Cron environment** (per-schedule control)

### Crontab convention

The recommended crontab installs entries for every provider listed in `kanban.cfg`
`[providers] available`. Claude and Codex are first-class providers; both fire on every
tick, and the one whose name does not match `[providers] active` exits in under one second.

The default pattern fires each agent every 2 minutes per provider with a sub-minute stagger
so agents in the same 2-minute window do not collide.  PM, CM, and TESTER fire on even
minutes; CODER and WRITER fire on odd minutes.  Operators can swap the even/odd assignment
if preferred.

```cron
PGAI_AGENT_KANBAN_ROOT_PATH=/home/rocky/pgai_agent_kanban
PATH=/path/to/claude:/path/to/codex:/usr/local/bin:/usr/bin:/bin

# --- Claude entries — even-minute agents (pm, cm, tester) ---
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/claude.sh --agent=pm     --sleep=0  >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-pm.log 2>&1
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/claude.sh --agent=cm     --sleep=21 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-cm.log 2>&1
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/claude.sh --agent=tester --sleep=42 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-tester.log 2>&1

# --- Claude entries — odd-minute agents (coder, writer) ---
1-59/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/claude.sh --agent=coder  --sleep=0  >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-coder.log 2>&1
1-59/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/claude.sh --agent=writer --sleep=21 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-writer.log 2>&1

# --- Codex entries — same schedule, parallel script ---
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/codex.sh --agent=pm     --sleep=0  >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-pm.log 2>&1
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/codex.sh --agent=cm     --sleep=21 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-cm.log 2>&1
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/codex.sh --agent=tester --sleep=42 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-tester.log 2>&1
1-59/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/codex.sh --agent=coder  --sleep=0  >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-coder.log 2>&1
1-59/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/codex.sh --agent=writer --sleep=21 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-writer.log 2>&1
```

Both providers' entries fire on every tick. The script whose provider name does not match
`[providers] active` exits in under a second; the matching script proceeds to acquire the
flock and do real work. Only one provider does work at a time. See "Provider Switch" above.

The `--sleep=N` parameter delays the script N seconds after cron fires but before acquiring
the work-queue lock, creating sub-minute stagger without cron fractions.  The legacy
positional form (`wake-claude.sh pm`) still works but emits a deprecation warning; switch
to `--agent=` at your next crontab edit.

To install or update the crontab automatically, run `team/scripts/install-crontab.sh`.
That script reads `team/templates/install/crontab.example`, substitutes the live kanban root path,
and emits both the Claude and the Codex blocks.

During the v0.17.x foundation period, you may also wish to include `PGAI_KANBAN_PM_MODE=manual` at
the top of the crontab to disable the autonomous scan while stabilizing the infrastructure.  Switch
to `automatic` (or simply remove the override) after three clean autonomous RCs ship without manual
intervention.

## Defensive install.sh Behavior

`install.sh` touches operator state outside `$KANBAN_ROOT` — the user's crontab and
per-provider role-file directories (`~/.claude/agents/`, `~/.codex/agents/`). Every such
write is prompt-protected and backed up. The script has no code path that silently
overwrites operator state. Both fresh installs and upgrades use the same code via the
`--upgrade` flag; the only operational difference is that upgrades skip interactive prompts
on subagent and role-file installs.

The prompt-and-backup contract described below protects against upgrades that silently
overwrite the operator's live crontab or role-file directories.

### Invocation modes

```
./install.sh                       # interactive install (prompts on every overwrite)
./install.sh --upgrade             # non-interactive overwrite of kanban tree and subagents
./install.sh --dry-run             # report what would happen, write nothing
./install.sh --force               # legacy: overwrite without prompts (kept for parity)
./install.sh --force-config-rewrite # rebuild config.cfg from template, preserve customizations
```

`--upgrade` keeps the same crontab prompts as the interactive install (crontab is operator
state that warrants confirmation even on upgrade). It does suppress prompts for subagent
files in `~/.claude/agents/` and role files in `~/.{provider}/agents/`, because those are
framework-shipped and an upgrade is expected to refresh them.

`--dry-run` reports every prompt and every write that would occur and exits without
modifying anything. Safe to run before any upgrade to preview the changes.

### Crontab handling

`install.sh` examines the operator's current crontab before doing anything and dispatches
on three states:

| Current state | Prompt | Default |
|---|---|---|
| No crontab present | "Install PGAI Kanban crontab now? [Y/n]" | Y (convenience for fresh installs) |
| Crontab with PGAI entries | "Replace with new template? [y/N]" | N (conservative; upgrade-in-place is opt-in) |
| Crontab with non-PGAI entries only | "REPLACE this crontab will discard existing entries. Continue? [y/N]" | N (conservative; foreign entries protected) |

Before any modification, the existing crontab is written to
`$HOME/.crontab.before-install-YYYYMMDD-HHMMSS.bak`. The backup is created **before** the
new crontab is installed, so a mid-flight failure leaves the original untouched and
recoverable via `crontab $HOME/.crontab.before-install-*.bak`.

After the install attempt completes, a sanity check runs: if the active crontab is empty
or absent, `install.sh` prints a loud warning and recovery instructions. An empty crontab
is the failure signature of the original incident, so it is now an explicit checked
condition.

When the operator declines a crontab prompt, `install.sh` prints the manual install command
and continues. The wake schedule simply will not fire until the operator installs it; no
silent partial install occurs.

### Role-file handling (multi-provider)

`install.sh` deploys `team/roles/*.md` to one directory per configured provider:

- `~/.claude/agents/` for Claude
- `~/.codex/agents/` for Codex

The provider list comes from `kanban.cfg`'s `[providers] available` key when present, with
a hardcoded fallback of `claude codex` until that key is universally present. Providers
whose CLI binary is missing on `PATH` are skipped with a log message — not silently ignored,
and not treated as an error.

For each provider, three checks happen per role file:

1. **Target does not exist** — prompt "Install `<target>`? [Y/n]", default Y.
2. **Target exists, identical to source** — silent no-op via `cmp -s` (no prompt).
3. **Target exists, differs from source** — show existing size and modification time,
   prompt "Overwrite `<target>`? [y/N]", default N.

When the operator agrees to overwrite, the existing file is copied to
`<target>.before-install-YYYYMMDD-HHMMSS.bak` (in the same directory as the target) before
the new file is written. Decline prints the manual `cp` command and skips that file; other
files and other providers continue to be processed independently.

If `~/.{provider}/agents/` does not exist on disk, `install.sh` first prompts before
creating it ("Create directory `<path>` for `<provider>` role files? [Y/n]"). Decline skips
the entire provider with a `mkdir -p` recovery instruction.

### Backup file naming and recovery

All backups are placed in predictable locations with timestamp-based names so operators can
find them with shell globs:

| Backup of | Path |
|---|---|
| Operator crontab | `$HOME/.crontab.before-install-YYYYMMDD-HHMMSS.bak` |
| Provider role file | `~/.{provider}/agents/<NAME>.md.before-install-YYYYMMDD-HHMMSS.bak` |

Examples of operator-side recovery:

```bash
# Find every backup install.sh has ever made for the crontab
ls -lt $HOME/.crontab.before-install-*.bak

# Restore the most recent crontab backup
crontab "$(ls -t $HOME/.crontab.before-install-*.bak | head -1)"

# Find every role-file backup for a given provider
ls -lt ~/.claude/agents/*.before-install-*.bak

# Restore a specific role file
cp ~/.claude/agents/WRITER.md.before-install-20260519-130050.bak \
   ~/.claude/agents/WRITER.md
```

Backups are never auto-purged by `install.sh`. They accumulate under `$HOME` and
`~/.{provider}/agents/`; the operator removes them when they are no longer wanted.

### Recovery when prompts are declined

Every "no" path prints the exact manual command the operator can run later to do what was
declined. Examples:

- Decline the crontab prompt → `crontab "$KANBAN_ROOT/templates/install/crontab.example"`
  (with placeholder paths to substitute) and a pointer to
  `$KANBAN_ROOT/scripts/install-crontab.sh`.
- Decline a role-file overwrite → `cp <source> <target>` for the specific file.
- Decline directory creation → `mkdir -p <path>` for the provider directory.

Declining a prompt is always safe: no partial state is left behind, no other prompts are
skipped, and the rest of the install continues. Operators can re-run `install.sh` at any
time; identical content is detected and silently skipped, so a re-run is idempotent for
files the operator already chose to install.

### Where the safety guarantees live

The prompt-and-backup primitives are implemented in `team/scripts/lib/safe_overwrite.sh`.
`install.sh` sources that library and dispatches to its functions:

- `safe_overwrite_file` — generic prompt-cmp-backup-copy for any file.
- `safe_overwrite_crontab` — three-state crontab handler.
- `backup_current_crontab` — pure backup helper used internally.
- `warn_if_empty_crontab` — post-install empty-crontab sanity check.

Other scripts that need to modify operator state outside `$KANBAN_ROOT` should source the
same library rather than reinventing the pattern. The library defines functions only and
has no top-level side effects, so it is safe to source repeatedly.

## Autonomous Criterion Expectations

A build cycle is considered autonomous when it runs from PM materialization through CM-release without any manual intervention on the task pipeline. This is not merely a preference — it is a verified success criterion that the TESTER agent checks explicitly (Step 15a of the verification methodology).

### What counts as autonomous

A build is autonomous when:

- All task state transitions were made by the wake script or by agents in response to task-defined instructions.
- No human edited a task's `status.md` to force a state change that the automation should have made.
- No queue backlog file was edited by hand to add, remove, or reorder entries.
- The project's `release-state.md` was updated only by `cm-open-rc.sh`, `cm-release.sh`, and `cm-cancel-rc.sh`, not by direct human edits.
- No scripts under `team/scripts/` or `subagents/` were patched while an RC build was in-flight (after PM materialization, before CM-release).

### What does not disqualify autonomous operation

Not every human touch is an intervention. The following are explicitly permitted and do not break the autonomous criterion:

- Using the HALT flag to pause and resume the system.
- Manually completing a task that was BLOCKED because the automation could not proceed — but only if this was caused by a pre-existing external dependency outside the pipeline's control, and the action is recorded in `status.md`.
- Opening or cancelling an RC by running `cm-open-rc.sh` or `cm-cancel-rc.sh` as directed by the task plan.

### Agent behavior when autonomy fails

If a task unexpectedly requires human input mid-execution — for example, because a dependency is missing, a credential is unavailable, or an ambiguity cannot be resolved from the task instructions — the agent must:

1. Set the task state to `BLOCKED`.
2. Set `Needs Human: yes` in `status.md`.
3. Record the exact reason in `## Blockers`.
4. Stop work immediately. Do not attempt workarounds that would require modifying other tasks, queue files, or governance state outside the agent's assigned task folder.

The agent must not simulate autonomy by making undocumented state changes. If the task genuinely cannot be completed without a human decision, BLOCKED is the correct outcome.

### TESTER's role

TESTER verifies autonomous operation in Step 15a by reviewing task logs, git commit timestamps and authors, and the audit trail in each task's `status.md`. Any manual intervention discovered during this check must be recorded as a finding in the TESTER report and filed via Path C; if the intervention indicates a defect that would recur, elevate the recommendation to at least `SHIP-WITH-CONCERNS` and set `Systemic Risk: high` if the autonomous loop itself is unhealthy. See the TESTER role file for the exact four-point checklist.

## Bootstrap Paradox

When a new version of the system ships, the build that produces and validates that version is itself running on the prior version's infrastructure. This is the bootstrap paradox: the version being built cannot yet run the build that produces it.

### Why it happens

The wake script, subagent prompts, and scripts used to execute the RC build are the ones installed on the operator's machine at build time. If a release adds improvements to the wake scripts (`wake-batch.sh` and the provider dispatchers under `scripts/wake/`), those improvements are not in effect during that release's own build cycle — the build runs using the previously installed wake scripts.

The new code ships to `main` via CM-release. The operator must then run `upgrade.sh` or `install.sh` to deploy the new version. Only builds that start after the upgrade will benefit from the new code.

### Implications for bug fixes

A bug fix included in version N will not prevent that exact bug from occurring in the build that produces version N. The first fully clean build — where the fix is in effect throughout the entire pipeline — is the build for version N+1 (or later), after the operator installs version N.

This is expected behavior. Release notes for a given version may include a "bootstrap paradox" note when a fix that ships in that version was not in effect during its own build. This is not a defect — it is an inherent property of any self-hosting release pipeline.

### Example

v0.15.4 shipped a fix for duplicate PM materialization (Bug 12). The v0.15.4 build itself ran with the pre-fix materializer, so duplicate tasks (010–018) were created as duplicates of the working task set (002–009). Those duplicates were marked WONT-DO manually. Future builds running the v0.15.4 code did not exhibit duplication.

### What to do when the paradox occurs

When a build produces an artifact that the fix being shipped is meant to prevent:

1. Record the occurrence in the release notes under "Known Issues" with a bootstrap paradox note.
2. Clean up the affected artifacts (e.g., mark duplicate tasks WONT-DO) as a manual step.
3. Do not block the release. The fix will be in effect for the next build cycle.
4. The TESTER verification report should note the bootstrap paradox occurrence under "Autonomous operation criterion" if any manual cleanup was required.

## Multi-Project Path Forward

This section documents the multi-project model and the single-root layout it superseded. The single-root layout is preserved as a backward-compatibility fallback so older installs continue to operate without a forced migration.

### Single-root model (legacy fallback)

In the legacy single-root layout, the kanban system operates as a single flat tree rooted at:

```
${PGAI_AGENT_KANBAN_ROOT_PATH}
```

All work artifacts live directly under this root:

```
<KANBAN_ROOT>/
  tasks/
  queues/
  artifacts/
  requirements/
  bugs/
  scripts/
  team/
  roles/
  workflows/
```

There is one task namespace, one queue namespace, and one set of artifacts. All workstreams share them.

### Multi-project model (current)

The kanban tree contains a `projects/` directory that allows multiple independent projects to coexist under a single kanban installation:

```
<KANBAN_ROOT>/
  projects/
    <project-name>/
      tasks/
      queues/
      artifacts/
      requirements/
      bugs/
  scripts/
  team/
  roles/
  workflows/
```

Each project gets its own isolated subdirectory at:

```
<KANBAN_ROOT>/projects/<project-name>/
```

Per-project directories include: `tasks/`, `queues/`, `artifacts/`, `requirements/`, and `bugs/`.

Shared infrastructure remains at the kanban root level. This includes:

- `scripts/` -- wake scripts, PM agent, materializer, upgrade tooling
- `team/` -- governance documents (SOP.md, DIRECTIVES.md)
- `roles/` -- role definitions
- `workflows/` -- workflow definitions

Wake scripts, the PM agent, and the materializer are project-aware. They operate on a per-project basis, accepting a project name as context.

### Why This Matters

The single-namespace model creates contention when the kanban system is used for more than one workstream. Concrete problems:

- Queue contention: tasks from unrelated workstreams compete for the same queue slots.
- Version collision: release cycles for different deliverables (e.g., the kanban system itself vs. external project work) interfere with each other.
- Artifact confusion: outputs from different workstreams share the same `artifacts/` directory with no namespace boundary.

The multi-project model eliminates these problems by giving each workstream its own isolated task/queue/artifact space while preserving shared governance and tooling at the root.

## Projects Layout

The multi-project model described in the previous section is implemented. Per-project state lives under `projects/` within the kanban tree. Shared governance and tooling remain at the kanban root.

### Directory structure

```
${PGAI_AGENT_KANBAN_ROOT_PATH}/
  projects/
    <project-name>/
      tasks/           — per-project task folders
      queues/          — per-project agent queue files
      artifacts/       — per-project deliverables
      requirements/    — per-project requirements documents
      bugs/            — per-project bug reports
      project.cfg      — project-level configuration (INI format)
  scripts/             — shared wake scripts, PM agent, materializer
  team/                — shared governance (SOP.md, DIRECTIVES.md)
  roles/               — shared role definitions
  workflows/           — shared workflow definitions
```

### PGAI_PROJECT_ROOT

The environment variable `$PGAI_PROJECT_ROOT` resolves to the active project's root directory:

```
${PGAI_AGENT_KANBAN_ROOT_PATH}/projects/<project-name>/
```

All per-project paths (tasks, queues, artifacts, requirements, bugs) are relative to `$PGAI_PROJECT_ROOT`. Role files, wake scripts, and the materializer use `$PGAI_PROJECT_ROOT` to locate project-specific state.

### Where per-project state lives

| Resource | Path |
|---|---|
| Task folders | `$PGAI_PROJECT_ROOT/tasks/<TASK-ID>/` |
| Agent queues | `$PGAI_PROJECT_ROOT/tasks/queues/<agent>_backlog.md` |
| Artifacts | `$PGAI_PROJECT_ROOT/artifacts/<name>/` |
| Requirements | `$PGAI_PROJECT_ROOT/requirements/` |
| Priority requirements | `$PGAI_PROJECT_ROOT/requirements/priority/` |
| Bug reports | `$PGAI_PROJECT_ROOT/bugs/` |
| Project config | `$PGAI_PROJECT_ROOT/project.cfg` |

### Backward-compatibility shim (single-project mode)

For single-project installations and during the migration window, `$PGAI_PROJECT_ROOT` defaults to `$PGAI_AGENT_KANBAN_ROOT_PATH`. This means:

- Existing task paths (`$PGAI_AGENT_KANBAN_ROOT_PATH/tasks/...`) continue to resolve correctly.
- Existing queue paths, requirements paths, and bug paths are unchanged.
- No operator action is required until the operator explicitly creates a second project.

The shim is active when no `projects/` directory exists, or when the system is invoked without a project name argument. Wake scripts detect single-project mode automatically and set `$PGAI_PROJECT_ROOT` accordingly.

### Naming conventions

Project names follow the same rules as artifact project names: lowercase, hyphenated, alphanumeric only. Must match the regex `^[a-z0-9][a-z0-9-]*$`.

### Bootstrapping a new project

New projects are added with `team/scripts/create-project.sh`. The script is **safe-by-default**: it bootstraps the full project skeleton, registers the project in `projects.cfg`, and writes a `project.cfg` whose version ceiling is `max_minor=0` / `max_major=0`. A freshly-created project is therefore **dormant** — the discovery pipeline will not pick up any work for it until the operator explicitly raises the ceiling.

#### Canonical four-step flow

```bash
# 1. Bootstrap the project (skeleton, queues, templates, registration).
team/scripts/create-project.sh --project <name> [--workflow-type <type>] [--max-minor <N>] ...

# 2. Edit project.cfg to fill in the path-bearing fields.
$EDITOR $PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/project.cfg
#   set dev_tree_path = /absolute/path/to/local/clone   (under [project])
#   set git_repo_url  = git@github.com:owner/repo.git   (under [project])

# 3. Drop one or more requirements docs.
$EDITOR $PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/requirements/v0.1.0.md

# 4. Authorize the first release by raising the version ceiling.
team/scripts/set-version-ceiling.sh --project <name> --minor 1
```

After step 4 the project is live: the discovery pipeline scans its `bugs/`, `priority/`, and `requirements/` directories on the next wake and begins decomposing work. Steps 1–3 can be performed at any pace; nothing ships while the ceiling is `0/0`.

#### Defaults written by `create-project.sh`

| Field | Default | Notes |
|---|---|---|
| `project_name` | `<name>` argument | Validated `^[a-zA-Z][a-zA-Z0-9_-]*$`. |
| `workflow_type` | `release` | `release`, `feature`, or `document` (see Workflow Types). |
| `is_self_build` | `false` | Set `true` only for the kanban itself. |
| `git_remote_name` | `origin` | Remote name in the dev tree clone. |
| `max_minor` | `0` | Dormant. Raise to authorize minor releases. |
| `max_major` | `0` | Dormant. Raise to authorize major releases. |
| `dev_tree_path` | *(empty)* | **Operator must edit project.cfg.** No flag. |
| `git_repo_url` | *(empty)* | **Operator must edit project.cfg.** No flag. |

#### Override flags

Every default-able field has a flag. Pass any subset; flags are independent.

| Flag (alias) | Effect |
|---|---|
| `--workflow-type <type>` (`--workflow`) | Override `workflow_type`. |
| `--max-minor <N>` | Override `max_minor` (non-negative integer). |
| `--max-major <N>` | Override `max_major` (non-negative integer). |
| `--self-build` | Set `is_self_build=true`. |
| `--git-remote <name>` (`--git-remote-name`) | Override `git_remote_name`. |
| `--priority <int>` | Registry priority in `projects.cfg`. |
| `--dry-run` | Print the plan, do not write. |

**`--dev-tree`, `--dev-tree-path`, `--git-repo`, and `--git-repo-url` are intentionally rejected.** The script exits with a clear error pointing the operator at manual `project.cfg` editing. See "Why path fields stay manual" below.

#### Why dormant-by-default

The kanban runs autonomously by design — drop a brief, walk away, wake up to a tagged release. For an existing, well-configured project that's exactly what you want. For a **new** project it's the wrong default. Two failure modes the dormant ceiling prevents:

- **Premature shipping.** Operator creates a project intending to configure it gradually, drops a requirements file, and the next cron firing tries to ship work before paths are set.
- **Forgotten paths.** Operator creates a project, drops requirements, and the chain attempts to ship against a `dev_tree_path` that doesn't yet exist locally. The error surfaces mid-RC, often hours later.

A `0/0` ceiling makes the new project sit dormant until the operator runs `set-version-ceiling.sh --project <name> --minor N`. That command is the explicit "I am ready for autonomous shipping on this project" gate. The kanban itself uses `max_major=0` permanently as its v1.0.0 gate; new projects extending this pattern with `max_minor=0` initially is a natural extension.

#### Why path fields stay manual

`dev_tree_path` and `git_repo_url` are **per-machine and per-operator** by nature. A flag-based approach forces operators to maintain machine-specific `create-project.sh` invocations that drift over time and break silently across deployment environments (laptop vs VPS vs CI). By writing these fields empty and forcing a manual `project.cfg` edit, the script makes the operator consciously confirm "this is the right path on this machine" — preventing a class of cross-machine bugs the framework has hit before.

The empty values are intentional and load-bearing. Do not "fix" them by adding `--dev-tree` flags later; the rejection is the design.

#### Inspecting and changing the ceiling later

```bash
# Show current ceilings.
team/scripts/set-version-ceiling.sh --project <name> --show

# Raise minor or major ceiling.
team/scripts/set-version-ceiling.sh --project <name> --minor <N>
team/scripts/set-version-ceiling.sh --project <name> --major <N>

# Remove a ceiling field entirely (no cap).
team/scripts/set-version-ceiling.sh --project <name> --no-minor
team/scripts/set-version-ceiling.sh --project <name> --no-major
```

`set-version-ceiling.sh` edits `project.cfg` in place; all other fields are preserved verbatim.

#### Registering an existing project

`create-project.sh` aborts if `projects/<name>/` already exists. To register a project directory that's already on disk but missing from `projects.cfg`, use `team/scripts/add-project.sh` instead.

#### Workflow template dispatch

`create-project.sh` does not embed the project skeleton's templates inline. It reads them from per-workflow template directories under `templates/project/<workflow>/`. The value passed to `--workflow-type` (default `release`) selects the directory; everything copied into the new project — templates, queue files, README files — comes from that one directory.

This is what makes new workflow types additive: a new workflow is a new directory under `templates/project/`, not a code change.

##### Directory layout

Each `templates/project/<workflow>/` directory contains the same set of files. Their roles:

| File | Role |
|---|---|
| `REQUIREMENTS-TEMPLATE.md` | Copied to `requirements/templates/REQUIREMENTS-TEMPLATE.md` in the new project. Defines the requirements-doc schema operators fill in. The `## Workflow Type` field in this template is pre-filled for the workflow. |
| `BUG-TEMPLATE.md` | Copied to `bugs/templates/BUG-TEMPLATE.md`. Schema for bug reports. |
| `PRIORITY-TEMPLATE.md` | Copied to `priority/templates/PRIORITY-TEMPLATE.md`. Schema for priority overrides. |
| `queue-files.list` | Drives which `tasks/queues/*.md` queue files get seeded. Colon-separated: `<filename>:<title>:<description>`. Comment lines start with `#`. |
| `README-bugs.md` | Copied to `bugs/README.md`. Workflow-flavored description of the bug intake. |
| `README-priority.md` | Copied to `priority/README.md`. Workflow-flavored description of the priority intake. |
| `README-requirements.md` | Copied to `requirements/README.md`. Workflow-flavored description of the requirements intake. |
| `BRIEF-EXAMPLE.md` *(optional)* | If present, copied to `brief-example.md` at the project root. Used by workflows whose primary input is a long-form brief (e.g., `document`). |

##### Dispatch and error handling

`create-project.sh` resolves the template directory as `templates/project/${WORKFLOW}/`. If that directory does not exist, the script exits with:

```
ERROR: unknown workflow type <name>; available types: <space-separated list of subdirectory names>
```

The available types are derived from whatever directories are present under `templates/project/` at runtime. There is no allow-list in the script.

##### Document project vs release project (out of the box)

Both workflows produce the same directory skeleton (`tasks/`, `bugs/`, `priority/`, `requirements/`, `artifacts/`, `release-notes/`, `logs/`) and the same `project.cfg` fields. The differences operators see immediately:

| Aspect | `release` project | `document` project |
|---|---|---|
| Queue files seeded | `coder_backlog.md`, `pm_backlog.md`, `writer_backlog.md`, `tester_backlog.md`, `cm_backlog.md`, `bug_backlog.md`, `priority_backlog.md` | Same minus `coder_backlog.md` (no CODER pulls in a document chain) |
| `REQUIREMENTS-TEMPLATE.md` `## Workflow Type` | `release` | `document` |
| `REQUIREMENTS-TEMPLATE.md` content focus | Source branch, test required, code-shipping fields | Audience, brief, outline, deliverables, voice-and-tone notes |
| `brief-example.md` at project root | not seeded | seeded — short, concrete example of the brief format WRITER expects |
| READMEs | describe the release pipeline (PM → CODER → TESTER → CM) | describe the document pipeline (PM → WRITER outline → WRITER drafts → WRITER integrate → WRITER polish → TESTER → CM) |

The `project.cfg [project] workflow_type` key is set to the value the operator passed, so downstream tooling (PM materializer, subagent dispatch) reads the correct workflow without further configuration.

##### Adding a new workflow type

Three steps:

1. Create `templates/project/<new-workflow>/` in the dev tree.
2. Populate the file set above (all required files; `BRIEF-EXAMPLE.md` if the workflow uses one). Reuse the generic `BUG-TEMPLATE.md` and `PRIORITY-TEMPLATE.md` from an existing workflow when you have no workflow-specific changes.
3. Run `install.sh` so the live install picks up the new directory.

No edits to `create-project.sh` are needed. After installation, `create-project.sh --project foo --workflow-type <new-workflow>` works immediately, and `--workflow-type bogus` continues to error with the updated list of available types.

`install.sh` copies the whole `team/templates/project/` tree into `$KANBAN_ROOT/templates/project/`, so templates added in the dev tree become available to operators on the next install.


---

## Version Ceilings

Version ceilings are operator-controlled gates that prevent the discovery pipeline from queuing PM for any target version that exceeds a configured limit. They are set in `project.cfg [versioning]` and enforced exclusively by the discovery pipeline before PM is invoked — once PM is running, the ceiling no longer applies (work is already validated and in flight).

### The three ceiling fields

| Field | Applies to | Default | Semantics |
|---|---|---|---|
| `max_major` | X in vX.Y.Z | no constraint (infinity) | PM will not be queued for any version where X > max_major. |
| `max_minor` | Y in vX.Y.Z | no constraint (infinity) | PM will not be queued for any version where Y > max_minor. |
| `max_patch` | Z in vX.Y.Z | no constraint (infinity) | PM will not be queued for any version where Z > max_patch. |

All three are **independent**. Setting `max_minor=21` constrains Y but leaves X and Z unconstrained. Setting `max_patch=5` constrains Z but leaves X and Y unconstrained.

**The zero sentinel:** For all three fields, `0` is a **real ceiling value** — it means "only component value 0 is allowed." Omitting the field entirely (or leaving it empty) means "no constraint" (infinity). This differs from many parsers where 0 is treated as unset. `max_patch=0` means only vX.Y.0 variants are accepted; `max_patch=` (empty) means any patch value is fine.

### How the check works

Before queuing PM for a target version `vX.Y.Z`, discovery reads `max_major`, `max_minor`, and `max_patch` from `project.cfg [versioning]` and checks all three:

```
X <= max_major   (or max_major is unset — no check)
Y <= max_minor   (or max_minor is unset — no check)
Z <= max_patch   (or max_patch is unset — no check)
```

If any check fails, PM is **not** queued, the requirements file is left untouched, and discovery emits a log line:

```
[<timestamp>] discovery: PM not queued for <version>: <component> version <value> exceeds <ceiling_name>=<ceiling_value>
```

Example rejection lines:

```
[2026-05-10T22:25:01Z] discovery: PM not queued for v0.22.0: minor version 22 exceeds max_minor=21
[2026-05-10T18:15:00Z] discovery: PM not queued for v1.0.0: major version 1 exceeds max_major=0
[2026-05-10T07:39:59Z] discovery: PM not queued for v0.21.42: patch version 42 exceeds max_patch=41
```

The rejection does not error out the discovery iteration. If a lower-versioned bundle exists within the ceiling, discovery picks it up in the same run. If no eligible work remains after ceiling checks, discovery exits idle.

### Operator workflow

**To gate minor releases** (stop the chain at the current minor, allow patches):

```ini
# In project.cfg [versioning]:
max_minor = 21   # only vX.21.Z and below are queued
```

**To gate patch releases** (stop at a specific patch, useful for time-boxed milestones):

```ini
# In project.cfg [versioning]:
max_patch = 5    # only vX.Y.Z where Z <= 5 are queued
```

**To raise a ceiling** (allow the next minor):

```bash
# Edit project.cfg [versioning] directly, or use set-version-ceiling.sh:
team/scripts/set-version-ceiling.sh --project <name> --minor 22
```

**To remove a ceiling entirely** (no cap):

```bash
team/scripts/set-version-ceiling.sh --project <name> --no-minor
# Or manually: remove or comment out the max_minor line in project.cfg [versioning]
```

**To inspect current ceilings:**

```bash
team/scripts/set-version-ceiling.sh --project <name> --show
```

The discovery pipeline also logs active ceilings at the top of each iteration when at least one ceiling is configured, making it easy to confirm the operator's intent in the cron log.

---

## Pseudocron

Pseudocron is a minimal, foreground cron-like scheduler for environments where real cron is unavailable or prohibited. It is a Python 3 script at `team/scripts/pseudocron.py` that reads a schedule file and an environment file, then fires matching jobs once per minute in a clock-aligned loop.

### When to use pseudocron

Use pseudocron **only** when real cron is not available:

- Docker containers with restricted system access where `cron` is not installed.
- Sandboxed demo environments where the operator cannot modify crontab.
- Quick local testing without polluting the system crontab.
- Environments where policy prevents automated crontab writes ("the AI is not allowed to touch crontab").

**Real cron is preferred whenever it is available.** Do not use pseudocron on production hosts where cron works normally.

### When NOT to use pseudocron

- Any host with a working crontab — use `crontab -e` instead.
- Situations requiring log rotation, job supervision, restart-on-failure, or catch-up firing for missed minutes.
- Production deployments where reliability matters — pseudocron has no restart or supervision; if it crashes, jobs stop firing silently.

### Config and env file setup

Pseudocron reads two files from `$PGAI_AGENT_KANBAN_ROOT_PATH` at startup:

| File | Purpose | Required? |
|---|---|---|
| `pseudocron.cfg` | Job schedule (minute + command, one per line) | Yes — missing file is a hard error |
| `pseudocron.env` | Environment variables injected into child processes | No — missing file is logged and skipped |

**Setup steps:**

```bash
# Copy the example files to the kanban root.
cp team/scripts/pseudocron.cfg.example "$PGAI_AGENT_KANBAN_ROOT_PATH/pseudocron.cfg"
cp team/scripts/pseudocron.env.example "$PGAI_AGENT_KANBAN_ROOT_PATH/pseudocron.env"

# Edit the schedule.
$EDITOR "$PGAI_AGENT_KANBAN_ROOT_PATH/pseudocron.cfg"

# Edit the environment (add any vars your jobs need).
$EDITOR "$PGAI_AGENT_KANBAN_ROOT_PATH/pseudocron.env"
```

**`pseudocron.cfg` format** — one job per line:

```
# Lines beginning with '#' are comments.
# Blank lines are ignored.
# Format: <minute>  <command>
#   <minute> is a literal integer 0–59 (no *, */N, ranges, or lists).
#   <command> is run via bash -c; shell metacharacters work.

5   /home/rocky/pgai_agent_kanban/scripts/wake/claude.sh --agent=pm
6   /home/rocky/pgai_agent_kanban/scripts/wake/claude.sh --agent=coder
30  curl -fsS https://example.com/health >> /tmp/health.log 2>&1
```

**`pseudocron.env` format** — bash-style variable assignments:

```bash
# export NAME=VALUE or bare NAME=VALUE; both are accepted.
export PGAI_AGENT_KANBAN_ROOT_PATH=/home/rocky/pgai_agent_kanban
export PATH=/usr/local/bin:/usr/bin:/bin
export HOME=/home/rocky
```

### How to run pseudocron

**Foreground (tmux pane — recommended):**

```bash
python3 $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/pseudocron.py
```

**Background with log file:**

```bash
nohup python3 $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/pseudocron.py \
    >> /tmp/pseudocron.log 2>&1 &
echo "pseudocron PID: $!"
```

Pseudocron writes fired-job lines to stdout and startup/error messages to stderr. Redirect as needed. It writes no log files itself.

### How to stop pseudocron

Send SIGINT (Ctrl-C in the terminal) or SIGTERM. Pseudocron prints a shutdown notice to stderr and exits with status 0. Already-running child processes are **not** killed — they complete normally.

```bash
# Kill by PID (from the background example above).
kill <PID>

# Or, if you know the process name.
pkill -f pseudocron.py
```

### Limitations

Pseudocron is intentionally minimal. The following are **not implemented** and will not be added to v1:

- Log rotation or log file management.
- Daemonization (`--daemon` flag).
- Restart-on-failure or supervision.
- Special minute values: `*`, `*/N`, ranges (`1-5`), or comma lists (`1,5,10`).
- Multi-field schedules (hour, day-of-week, month) — minute-only.
- Job timeout or kill-after-N-seconds.
- Concurrency limits per job.
- Live config reload (SIGHUP).
- Catch-up firing for missed minutes (clock-jump-forward or system-resume).
- Email-on-failure.
- Per-job environment overrides.

For any of these requirements, use real cron or a proper job scheduler (e.g., systemd timers).
