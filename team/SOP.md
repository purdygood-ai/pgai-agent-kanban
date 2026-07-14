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

> Operator create/remove commands for `HALT` are documented in `docs/OPERATIONS.md` under "HALT Flags — operator commands."

### Behavior

When `${PGAI_AGENT_KANBAN_ROOT_PATH}/HALT` exists, chain wake scripts (PO, PM, CODER, WRITER, TESTER, CM) must exit cleanly before pulling the next task. No new chain agent invocations are started.

- The HALT file presence is checked at the start of each wake cycle, before task selection.
- If HALT is present, the wake script logs the halt condition and exits with status 0 (clean exit, not an error).
- Any task currently WORKING is left in WORKING state. The agent already running is not interrupted.
- HALT does not change any task states.

### Who may create and remove the flag

`HALT` has two authorized creators: the operator (manual halt) and CM (autonomous halt for systemic issues or mechanical release failures). Both write to the same path; the chain treats them identically.

- The **operator** creates `HALT` for any reason at any time (governance edits, full-stop reviews, pause to investigate).
- **CM** creates `HALT` only for the eight enumerated triggers documented in `team/roles/CM.md` ("HALT Authority") and summarized in the operator-side "When the chain halts" walkthrough. CM writes a comment header to the file so the operator can see who created it and why.
- The **operator** is the only role that removes `HALT`. CM never removes it. Removal is the operator's signal that the underlying issue is resolved and the chain may resume.

Other chain agents (PO, PM, CODER, WRITER, TESTER) must not create or remove `HALT`. They have no HALT authority.

### When to use the flag

Use `HALT` (operator-initiated) when:

- A systemic problem has been discovered that affects multiple tasks in the chain
- A governance change is being applied and you want to prevent chain task pulls during the transition
- A release review is in progress and you want no new automated chain work to begin

CM may also create `HALT` autonomously when one of the eight HALT triggers documented in `team/roles/CM.md` fires.

Use neither flag during normal operation. The chain ships work autonomously.

### SUBSTRATE_BROKEN pause expectation

A third halt signal is reserved for substrate-level failures: `SUBSTRATE_BROKEN`. When the wake substrate (config loader, queue parser, agent dispatch, lockfile invariants) detects an inconsistency it cannot safely work around, it sets `SUBSTRATE_BROKEN` and exits. Agents that observe this signal — whether through an environment variable, a marker file written by the wake script, or a substrate-check helper — must pause and not attempt to advance the chain or mutate task state. The signal means the floor under the chain is unstable; running an agent on top of unstable substrate risks corrupting queues, lockfiles, or task folders in ways the system cannot detect.

Operator response is the same as for `HALT`: investigate the substrate failure, fix the underlying issue, and clear the signal before resuming. Currently the signal is recognized only by the wake substrate itself. Agents that notice substrate breakage in the course of their pre-flight checks should treat it as a blocking pre-flight failure and exit without touching shared state.

## HALT-AFTER (soft halt)

`HALT-AFTER` is a soft halt: the chain keeps running until a named event drains, then auto-promotes to a regular `HALT`. Use it when you want the chain to finish what it has in flight before stopping, instead of freezing immediately.

The difference from `HALT` in one sentence: `HALT` stops the chain at the next wake firing; `HALT-AFTER` lets the chain keep firing until the named drain condition is met, then converts itself into a `HALT` automatically.

> Operator arm/resume walkthrough (file paths, `echo <token> > HALT-AFTER`, resume commands) is in `docs/OPERATIONS.md` under "HALT-AFTER — operator arm and resume."

`HALT-AFTER` does not gate wakes while it is draining. The chain keeps running normally until the drain condition is satisfied — that is the whole point of the soft variant.

### Event tokens

The file's contents are the event token. Six tokens are supported.

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

## Release State File

Release state is split between two sources, on purpose:

- **In-flight RC state** lives in the project's `release-state.md` at:

  ```
  $KANBAN_ROOT/projects/<project-name>/release-state.md
  ```

  The file holds four fields: `## Active RC`, `## RC Opened At`, `## RC Opened By Task`, and `## Last Released`. CM-open populates the first three when it cuts the RC branch; CM-release and `cm-cancel-rc.sh` clear them back to `none`. `## Last Released` holds the most recent shipped version in `vX.Y.Z` form; CM-release Step 15 writes it on every successful release, and `cm-cancel-rc.sh` never clears it. The three RC-tracking fields are ephemeral; `## Last Released` is monotonic and latching — it only moves forward.

- **Historical release state** is git tags on the dev tree's `main` branch. A tag like `v0.21.6` IS the v0.21.6 release. The highest semver tag merged into `origin/main` is the canonical answer to "what version are we at," resolved via the `pp_last_released_version` helper. The `## Last Released` field in `release-state.md` is a separate, narrower signal used only by `halt_after/drain.py` for the `rc:vX.Y.Z` drain check (see below); it is not a substitute for the helper and consumers other than the drain code must not read it.

> Install.sh migration and the historical rationale for the split live in `docs/OPERATIONS.md` under "Release State File — migration and rationale."

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

The system uses a Release Candidate (RC) branch pattern to stage and review releases before they land on `main`. A single squash back to `main` closes each cycle. There is no develop hop — the RC branches from `main` and returns to `main`.

### Branch structure

```
${branch_prefix}main         — stable, production-ready releases only
  └── rc/vX.Y.Z              — release candidate branch for version X.Y.Z
        └── feature/<TASK-ID> — individual task branches
```

The prefixed main branch resolves per `project.cfg` `[project] branch_prefix` (for example, `ai_main` on the self-build).

### Workflow

1. **RC branch creation**: When a release is being staged, CM's `cm-open-rc.sh` creates `rc/vX.Y.Z` branching from the tip of `${branch_prefix}main`.
2. **Task branches**: Individual feature or fix tasks branch from `rc/vX.Y.Z` (not from `${branch_prefix}main` directly) and are named `feature/<TASK-ID>`.
3. **Merging to RC**: When a task is complete, the feature branch is merged back into `rc/vX.Y.Z` with `--no-ff` to preserve history.
4. **RC verification**: TESTER runs the acceptance suite against the RC. The ship-policy decision matrix determines the release-notes Status.
5. **Squash to main**: `cm-release.sh` squash-merges `rc/vX.Y.Z` directly into `${branch_prefix}main`. One squash. No develop hop.
6. **Fidelity gate**: Immediately after the squash commit and before release notes or the tag, `git diff --quiet rc/vX.Y.Z ${branch_prefix}main` must succeed — the trees are byte-identical at that moment. If the gate fails (for example, an operator commit landed on main mid-RC), CM HALTs and does not tag.
7. **Release notes**: CM stamps the ship-policy Status into `release-notes/vX.Y.Z.md`, commits it on `${branch_prefix}main`.
8. **Tag**: CM tags the squash commit (with the release-notes commit on top) as `vX.Y.Z`.

### Tagging

Releases on `${branch_prefix}main` are tagged with the version number (e.g., `v0.2.0`) at the release-notes commit that follows the squash. The fidelity gate must have passed for the tag to exist.

### Release-notes polish (autonomous)

`cm-release.sh` writes a stub `release-notes/vX.Y.Z.md` from the commit log, commits it, tags the release, and pushes. Shortly after, a WRITER polish task fires and rewrites that stub in-place with structured content (Summary, What Shipped, Bugs Resolved, Known Issues, etc.).

The polish reaches origin without operator intervention. Before pushing `main`, `cm-finalize-release.sh` runs Step 2b: it checks `release-notes/${VERSION}.md` for uncommitted changes and, if present, creates a `Polish release notes for ${VERSION}` follow-up commit and pushes it as part of the same finalize step. Operators no longer need to run `git add release-notes/v*.md && git commit && git push` after an autonomous ship.

The flow degrades gracefully:

- If WRITER polish never runs (timeout, error, agent unavailable), the stub remains committed and the release still ships — Step 2b is a no-op.
- If the polished file matches the stub byte-for-byte, no follow-up commit is created.
- If `release-notes/${VERSION}.md` is missing entirely, finalize logs a warning and continues; the release is not blocked.

This means the published tag may point at the stub commit while a follow-up commit on `main` carries the polish. That is intentional — the tag is the snapshot of code; release notes are a documentation artifact that can land separately.

### Constraints

- Agents must not merge RC branches into `main`. That is a CM action driven by `cm-release.sh`.
- Agents must not create RC branches without explicit task assignment.
- Agents work on feature branches and merge to the RC branch only — never directly to `${branch_prefix}main` during a release cycle.

## Project-Specific CM-Release Hooks

`cm-release.sh` is project-agnostic — it ships a tagged version of source code and nothing more. Projects that need additional per-release work (bumping a `pyproject.toml` version, regenerating a manifest, updating a CHANGELOG header, notifying a downstream system) point CM at hook scripts via fields in the project's `project.cfg`. The release script reads those fields, resolves them to absolute paths, and runs the hooks at fixed points in the lifecycle. Projects that declare no hooks behave exactly as before — no regression, no opt-in flag.

**Operator-facing reference.** The three-tier resolution model (cfg → kanban-side → in-repo), the always-printed resolution line, the `cm_release_<phase>_hook_required` fail-loud flag, and the hook environment contract are documented in `docs/OPERATIONS.md` under "Release Lifecycle Hooks". Operators configuring hooks should read that section; the rest of this SOP section covers the historical mechanic and phase semantics.

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

The hook environment contract (the six `PGAI_*` variables set for every hook, and the `cwd` the hook runs from) is documented as the canonical reference in `docs/OPERATIONS.md` under "Release Lifecycle Hooks" → "Hook environment contract". Both `cm-release.sh` and `ship-rc.sh` set the same variables via the same shared library, so the canonical table applies to hooks invoked by either script.

Hook stdout and stderr are captured to the CM task's log file with each line prefixed `[hook <name>]` for easy filtering.

### Phase semantics

The three hooks run at three fixed points in the release lifecycle. Each phase exists because the work it enables can only be done meaningfully at that point.

**`cm-release-pre-squash.sh`** — runs on the RC branch after RC verification completes, before the squash into `${branch_prefix}main`. Use for: bumping version files (`pyproject.toml`, `package.json`, `VERSION`), regenerating manifests, updating CHANGELOG entries. Any commits this hook makes are on the RC branch and become part of what gets squashed into `${branch_prefix}main`.

**`cm-release-pre-tag.sh`** — runs after the RC has been squashed into `${branch_prefix}main`, after the fidelity gate has passed, and before `git tag` is created. Use for: final consistency checks across the merged state, generating release artifacts that depend on `${branch_prefix}main` being current, updating documentation that references the new version.

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

### Surfacing

A pending HUMAN-APPROVE task appears on four dashboard surfaces at once. Any one of them is enough to notice a gate; together they make a pending approval impossible to miss.

- **Status bar (all tmux windows).** When at least one HUMAN-APPROVE task is `WAITING` or `BACKLOG` across all registered projects, the bottom status line adds a yellow `✋ APPROVAL(n)` segment alongside the existing HALT indicator (`[APPROVAL(n)]` on `NO_COLOR` / dumb terminals). The count `n` is the exact number of pending gates. The segment disappears on the next render after every gate is resolved. Rendered by `scripts/dashboard/status-bottom.sh`.
- **Dashboard window 14 — human-review.** A dedicated window that lists every pending HUMAN-APPROVE task across all projects, one entry per gate: project, target RC, age, the task's Goal (what is being approved), and the two verbatim commands to approve or reject. When no gates are pending the window renders a single line: `no approvals pending.`. Rendered by `scripts/dashboard/human-review.sh`; column reference in [`docs/DASHBOARD-PANES.md`](../docs/DASHBOARD-PANES.md#window-14--human-review).
- **Attention pane (window 3 and the web UI Attention tab).** Every pending gate renders as a row in the needs-human stratum with a `✋` class and a project label, positioned above the OVERWATCH ledger. The web UI's Attention tab inherits the same content through the existing pane passthrough. The row disappears on the next render after approve or reject.
- **Show-queues human queue.** The `HUMAN-APPROVE` queue renders in `scripts/show-queues.sh --details` with a normal queue marker (no `⚠ no queue entry` warning). The queue-marker correctness checks cover this queue alongside the six agent queues.

### Approving or rejecting a pending gate

Both commands are copy-pasteable directly from window 14. Substitute the project name and the task ID shown on the row:

```
# Approve — advances the gate to DONE and unblocks CM-release
scripts/close.sh --project <proj> --key <task-id>

# Reject — marks the gate WONT-DO; the release does not proceed
scripts/wontdo.sh --project <proj> --key <task-id>
```

After either command runs, the next dashboard refresh clears the `✋ APPROVAL(n)` status-bar segment (if this was the last pending gate), removes the row from window 14 and the attention pane, and — for `close.sh` — unblocks the downstream CM-release task.

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

## Temporary File Convention

All framework subsystems write temporary files under a single, configurable directory rather than scattering them across `/tmp`. The location is controlled by the `PGAI_AGENT_KANBAN_TEMP_DIR` environment variable.

Three report-only mechanisms enforce the convention on agent sessions: the wake dispatch prompt names each agent's scratch subtree so it knows where to write (`Scratch/diagnostic output goes under ${PGAI_PROJECT_TEMP_SUBTREE}`); the wake bracket snapshots `/tmp` before dispatch, diffs it after the session on both the DONE and BLOCKED paths, and appends a `## Temp Litter` section to the task's `status.md` naming any fresh top-level entry outside the framework root; and OVERWATCH's Tier-1 `check-bare-tmp-litter` backstops sessions that crashed before the post-check ran, flagging framework-user-owned `/tmp` entries created within a known task window and not yet reported. Nothing is ever deleted — litter is a hygiene report, not a failure.

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

## Activity Log (Directive 3)

Host-level software changes (persisting beyond a task — system packages, global tools, services) append one line to `$PGAI_AGENT_KANBAN_ROOT_PATH/logs/activity.log`: ISO timestamp, action, command, reason. Ephemeral per-task venvs/pip installs inside worktrees are exempt (Directive 3's scope). The log is append-only; no tool rotates it.

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

When a script needs to read version-controlled state from a specific branch (for example, a governance file on `main` or an RC branch), it should use `git show` with an explicit branch reference:

```bash
# Read a governance file from the main branch without switching branches
git show main:team/SOP.md

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

The separation prevents a common failure class: an agent reads a governance file from the dev tree, which happens to have a feature branch checked out, and gets stale or branch-specific data instead of the canonical value from `${branch_prefix}main` or the RC branch. By routing each operation through its canonical path source, the system avoids cross-context contamination.

## Git Repositories: Roles and Boundaries

Two kinds of git repository exist in this system, with different rules.
Violating either boundary is a defect, not a style choice.

### The remote (origin)

Not assumed reachable — ever. The CM role is the ONLY toucher of any
remote: no other agent pushes, pulls, or fetches, and no agent designs
code that requires origin to be reachable at runtime. Code that needs
something from origin at run time is wrong by construction. (Helpers
that attempt a best-effort `git fetch` — such as
`pp_last_released_version` — degrade cleanly when origin is absent;
that is the required shape for anything that touches a remote at all.)

### The local repository (a managed project's dev tree)

This is where code is WRITTEN and READ as part of the release
lifecycle — worktrees, feature branches, merges, tags. It is the
source of record for the project's code history. It is NOT a runtime
dependency:

- **Live running code must never import, source, or resolve anything
  from a repository checkout.** The live install is self-contained:
  its scripts source its own libs, its python imports its own package,
  and if its own copy of something is missing or broken, it FAILS LOUD
  naming its own path — it never quietly borrows the missing piece
  from a checkout. A repository is never a backup, a fallback, or a
  shadow copy for running code.
- **A checkout is data, not code, to everything outside it.** The
  release machinery operates ON dev trees (git commands, worktree
  checkouts for tasks); nothing operates FROM them except the task
  working inside its own isolated worktree.
- **The litmus test:** checking out a different branch — any branch —
  in any dev tree must change NOTHING about live behavior. If a branch
  flip anywhere can break or alter anything running, a runtime
  boundary has been crossed. This test is cheap; run it when in doubt.

Corollary for path construction: sys.path, PYTHONPATH, and `source`
targets in live-runtime code contain live-install paths only. A
"dev-tree fallback" on an import path is the defect this section
exists to prevent — it inverts silently, masks deployment gaps, and
couples live behavior to checkout state.

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

## Projects Layout

The multi-project model described in the previous section is implemented. Per-project state lives under `projects/` within the kanban tree. Shared governance and tooling remain at the kanban root.

> Operator create-project flow and workflow-template dispatch are documented in `docs/OPERATIONS.md` under "Bootstrapping a new project."

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

