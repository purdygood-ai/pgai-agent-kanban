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
- Shared operating procedure (`SOP.md`)
- Participant identity files (`participants/`)
- Role files (`roles/`)
- Workspace conventions (`workspaces/`)
- Task folders (`tasks/`)
- Wake scripts and PM agent (`scripts/`, `pm-agent/`)

The goal is to let any participant receive a task, read a deterministic stack of documents, perform the work, and write a clear status update that humans and other participants can inspect.

## Core Operating Model

Each participant should normally do this:

1. Receive tasking
2. Read the required documents in the expected read order
3. Determine identity, role, scope, constraints, and output location
4. Read the `status.md` at the start of a new agent session — if work was already started, pick up where it left off
5. Execute the task
6. Write status into the assigned task folder `status.md`

This system runs autonomously. Read `OVERVIEW.md` first for the autonomy principle and the binary terminal-state contract (DONE / BLOCKED / WONT-DO). Read `SOP.md` for procedural detail. The reading order is defined in `SOP.md` under "Required Read Order For Assigned Work."

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

Recommended task naming format:

```
<OWNER>-<YYYYMMDD>-<sequence>-<short-slug>
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

The approved task states are defined in `SOP.md` and should be used consistently in each task folder's `status.md`.

Approved states:

- `BACKLOG` — ready to be pulled
- `WAITING` — pulled but prerequisites are not yet satisfied (auto-resolved)
- `WORKING` — actively being worked
- `BLOCKED` — needs human attention (manual resolution)
- `DONE` — accepted and finished
- `WONT-DO` — intentionally not done

See `SOP.md` for the full state transition rules and the WAITING vs BLOCKED distinction.

## Role Model

Participant identity and task role are different things.

- A participant file describes *who* the worker is
- A role file describes *what kind of work* the worker is performing on the current task

Claude may perform any role when the task assigns it.

## New Agent Session

If a task is not completed for any reason, the participant must record that in `status.md`. The next session reads the status before starting, so it knows where to pick up (e.g., "task was previously blocked" or "model ran out of tokens"). Once it knows the status, it begins the task from that point.

## Wake Scripts and Cron Scheduling

The primary wake entry point is `scripts/wake-batch.sh`. It reads the active
provider from `kanban.cfg [providers] active` and dispatches to the matching
`scripts/wake/<provider>.sh` (currently `claude.sh` or `codex.sh`).
`scripts/wake.sh` is a convenience wrapper that adds `--max-tasks=1` for
one-shot single-task invocations.

```
wake-batch.sh --agent=AGENT [--sleep=N] [--max-tasks=N]
wake-batch.sh AGENT          # deprecated positional form; emits a warning
wake.sh AGENT                # single-task wrapper
```

Operator benefit: switching providers (claude → codex → gemini) no longer
requires editing the crontab. Update `kanban.cfg [providers] active` (or run
`scripts/switch-provider.sh PROVIDER`) and the next cron firing dispatches to
the new provider automatically.

The recommended cron schedule fires each agent every 2 minutes with a sub-minute
stagger so agents in the same 2-minute window do not collide.  PM, CM, and TESTER
fire on even minutes; CODER and WRITER fire on odd minutes.  Operators can swap
the even/odd assignment if preferred.

```cron
# Even-minute agents
*/2 * * * *    $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm     --sleep=0
*/2 * * * *    $KANBAN_ROOT/scripts/wake-batch.sh --agent=cm     --sleep=21
*/2 * * * *    $KANBAN_ROOT/scripts/wake-batch.sh --agent=tester --sleep=42

# Odd-minute agents
1-59/2 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder  --sleep=0
1-59/2 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=writer --sleep=21
```

Run `scripts/install-crontab.sh` to install the full annotated crontab automatically.
See `templates/install/crontab.example` for the complete annotated crontab template.

## Configuration

The framework uses five configuration files across two formats. `env` and `bashrc` are bash-sourced for wake scripts and personal shell setup respectively. `kanban.cfg`, `projects.cfg`, and `project.cfg` (per project) use INI format (`[section] key = value`) — data files that are never executed, preventing command-injection errors.

| File | Format | Role |
|------|--------|------|
| `env` | Bash | Wake script tunables (per-agent models, PM mode, verbose) |
| `bashrc` | Bash | Personal shell config (PATH, OAuth tokens, KANBAN_ROOT) |
| `kanban.cfg` | INI | Framework operational settings (dashboard, chain, paths) |
| `projects.cfg` | INI | Project registry |
| `project.cfg` (per project) | INI | Per-project settings (git repo, dev tree, workflow type, version ceilings) |

Templates with inline documentation for every key:

- `team/templates/kanban.cfg.example` — schema for `kanban.cfg`
- `team/templates/project.cfg.example` — schema for `project.cfg`

`install.sh` creates `kanban.cfg` from the template on a fresh install and migrates `config.cfg` → `kanban.cfg` (and `PROJECT.cfg` → `project.cfg`) on upgrade. See `docs/OPERATIONS.md` "Configuration File System" for full operator guidance.

## Helpful Information

- HUMAN is the human authority and reviewer
- The wake scripts in `scripts/` are how Claude gets invoked
- The PM agent in `scripts/pm-agent.sh` is how project requirements get decomposed into tickets
