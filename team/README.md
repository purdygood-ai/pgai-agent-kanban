# PGAI Agent Kanban — Team Tree

This directory is the shared collaboration root for the kanban system. Set the environment variable `PGAI_AGENT_KANBAN_ROOT_PATH` to point at this directory.

The default location is `$HOME/pgai_agent_kanban`.

## Participants

- **HUMAN** — the human operator, reviewer, and final authority
- **CLAUDE** — the AI worker that executes assigned tasks

## Design Intent

This tree provides:

- Top-level rules (`DIRECTIVES.md`)
- Autonomy principle and orientation (`OVERVIEW.md`)
- Shared operating procedure (`SOP-core.md`)
- Role files (`roles/`)
- Per-project rules (`projects/<name>/README.md`, one per registered project)
- Per-role governance manifests (`read-order/`)
- Task folders (`tasks/`)
- Wake scripts and PM agent (`scripts/`, `pm-agent/`)

The goal is to let any participant receive a task, read a deterministic stack of documents, perform the work, and write a clear status update that humans and other participants can inspect.

## Core Operating Model

Each participant should normally do this:

1. Receive tasking
2. Read the required documents in the expected read order
3. Determine identity, role, scope, constraints, and output location
4. Read the task's `status.md` — every session starts in `WORKING`; `SOP-core.md` ("Session start") says how to tell a fresh start from a resume
5. Execute the task
6. Write status into the assigned task folder `status.md`

This system runs autonomously. Read `OVERVIEW.md` first for the autonomy principle and the binary terminal-state contract (DONE / BLOCKED / WONT-DO). Read `SOP-core.md` for the shared law. The reading order is defined in `SOP-core.md` under "Required Read Order For Assigned Work."

## Read-Order Manifests

What each role reads at wake time is declared in `read-order/<role>.txt`, one path per line in reading order. **A document not named in a role's manifest is not read by that role.** Per-role byte budgets live in `kanban.cfg [governance]`; the lint narrates, never blocks. See `docs/read-order-manifests.md`.

## Important Write Rule

The shared tree is read-only by default **except for assigned task folders under** `$PGAI_PROJECT_ROOT/tasks/...` (which resolves to `${PGAI_AGENT_KANBAN_ROOT_PATH}/projects/<project-name>/tasks/...` in multi-project mode, or `${PGAI_AGENT_KANBAN_ROOT_PATH}/tasks/...` in single-project mode).

That means:

- Participants may always read the shared tree
- Participants may write task status and assigned deliverables only inside task folders explicitly assigned to them
- Participants should keep private runtime memory and local logs in their own private runtime areas unless a task explicitly says otherwise

## Task Folder Source of Truth

Each assigned task should normally have its own folder, and that folder is the source of truth for the task.

For any active task, participants and HUMAN should inspect `status.md` first to determine the current state.

Participants should not use shared workspace folders or private runtime areas as the primary source of active task status.

## Task Folder Model

```
$PGAI_PROJECT_ROOT/tasks/<TASK-ID>/
  README.md      # the spec
  status.md      # the live progress
  artifacts/     # deliverables
  logs/          # execution logs
```

In multi-project mode, `$PGAI_PROJECT_ROOT` resolves to `${PGAI_AGENT_KANBAN_ROOT_PATH}/projects/<project-name>/`. In single-project mode (backward compat), it defaults to `${PGAI_AGENT_KANBAN_ROOT_PATH}`.

Task naming format (the materializer assigns it):

```
<ROLE>-<YYYYMMDD>-<sequence>-<short-slug>
```

Examples:

- `CODER-20260412-001-add-login-endpoint`
- `WRITER-20260412-002-write-api-docs`

## Task Queue Files

Task priority queues are stored under:

```
$PGAI_PROJECT_ROOT/tasks/queues/
```

In multi-project mode, this resolves to `${PGAI_AGENT_KANBAN_ROOT_PATH}/projects/<project-name>/tasks/queues/`. In single-project mode, it defaults to `${PGAI_AGENT_KANBAN_ROOT_PATH}/tasks/queues/`.

Each agent role has its own priority queue:

- `tasks/queues/<agent>_backlog.md` (e.g., `coder_backlog.md`, `writer_backlog.md`, `pm_backlog.md`)

These queue files define dispatch priority. Queue files are control files for task ordering. They are not the source of truth for task details or task state. The source of truth for task details and current task state remains the individual task folder, especially `status.md`.

## Standard Task States

The approved task states are defined in `SOP-core.md` and should be used consistently in each task folder's `status.md`.

Approved states:

- `BACKLOG` — ready to be pulled
- `WAITING` — pulled but prerequisites are not yet satisfied (auto-resolved)
- `WORKING` — actively being worked
- `BLOCKED` — a named obstacle stops the task; OVERWATCH's block triage may requeue a recoverable block, every other block waits for HUMAN
- `DONE` — accepted and finished
- `WONT-DO` — intentionally not done

See `SOP-core.md` for the full state transition rules and the WAITING vs BLOCKED distinction.

## Role Model

Participant identity and task role are different things.

- A participant file describes *who* the worker is
- A role file describes *what kind of work* the worker is performing on the current task

Claude may perform any role when the task assigns it.

## New Agent Session

If a task is not completed for any reason, the participant records that in `status.md` and `logs/progress.log`. The next session uses that evidence, not the `WORKING` state alone, to decide whether it is resuming — `SOP-core.md`, "Session start".

## Wake Scripts, Scheduling and Configuration

Operator material — how wakes are scheduled, which configuration file holds which setting, and how to install or upgrade — is in `docs/OPERATIONS.md` and `HOW_TO.md`, not here. Agents do not need it to do assigned work: the wake prompt that invoked you names everything a dispatch needs.

## Helpful Information

- HUMAN is the human authority and reviewer
- The wake scripts in `scripts/` are how Claude gets invoked
- The PM agent in `scripts/pm-agent.sh` is how project requirements get decomposed into tickets
