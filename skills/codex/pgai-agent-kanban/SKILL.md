---
name: pgai-agent-kanban
description: "Operate a pgai-agent-kanban install: check why a lane is or is not moving, halt and resume, file bugs and requirements through intake, answer operator-inbox questions, unblock or retire tasks, inspect releases and costs, upgrade the live install. Use whenever the operator asks about the kanban, its dashboard, its agents (PM, CODER, WRITER, TESTER, CM, OVERWATCH), a project it manages, a release candidate, a bug or requirement file, HALT, or a purple/red item on the board."
---

# Operating pgai-agent-kanban

The kanban is a software shop in a box: cron wakes five role agents every minute, they pull tasks from per-project queues, and a release candidate (RC) ships when its tasks are done. The operator steers it with files and scripts, never by editing agent output. Your job when the operator asks for something is to pick the right verb, run it, and report what changed.

## Before anything

Every command needs the install's environment. Find the install root (the directory holding `kanban.cfg`, `scripts/`, `projects/`; often `~/pgai_agent_kanban`) and source it once per shell:

```bash
source "$KANBAN_ROOT/shell-env"      # sets PGAI_AGENT_KANBAN_ROOT_PATH and friends
```

If a script fails with an empty-variable error, this was skipped. Run scripts as `bash "$KANBAN_ROOT/scripts/<name>.sh" ...`; every one prints its flags with `--help`.

Two trees exist and must not be confused:

- **Live install** (`$KANBAN_ROOT`): what cron runs. `projects/<name>/` holds that project's queues, tasks, bugs, requirements, inbox and release state. `install.sh` and `upgrade.sh` overwrite the code here wholesale, so never hand-edit code under it.
- **Dev tree**: the kanban's own git checkout, registered like any other project. Never edit it by hand either; code changes, including fixes to the kanban itself, go through intake and are built by the agents.

## First question: what is going on?

```bash
bash scripts/why.sh --project <name>              # one verdict line + cause + remedy
bash scripts/why.sh --details --project <name>    # queues, HALT, locks, RC, inbox
bash scripts/kanban-status.sh --project <name>    # one-shot status, no tmux
bash scripts/release-state.sh --project <name>    # active RC, last released
bash scripts/inbox.sh --project <name> list       # open operator questions
```

`why` exit codes: 0 running, 2 idle, 3 stalled (WORKING over 60 min), 4 blocked, 5 halted, 6 stuck lock, 1 could not answer. Read the remedy it prints before doing anything else; it names the exact command.

The dashboard is tmux: `scripts/dashboard/create.sh` builds it, `attach.sh` reattaches, `kill.sh` tears it down. Board colours: green done, yellow working or waiting, red blocked or HALT, purple `needs-operator` (an inbox question is waiting), a marker after an ID means carried from an earlier bundle.

## The everyday situations

**A purple item or "needs-operator".** A bug or task is waiting on an answer in that project's inbox and is excluded from bundling until answered.
```bash
bash scripts/inbox.sh --project <name> list
bash scripts/inbox.sh --project <name> answer --id INBOX-NNNN --answer "<decision>"
bash scripts/inbox.sh --project <name> close  --id INBOX-NNNN
```
Answering flips the item back to open. Pick the engineering answer (additive over breaking, documented behaviour wins, no shipped field changes meaning) and tell the operator what was asked and what you answered; hold only real design or spending decisions for them.

**The operator wants work done: a feature, a change, a fix.** Nothing happens unless a file is deposited. Write it from the project's template, then intake it; the PM plans it on the next wake.
```bash
ls projects/<name>/requirements/templates/ projects/<name>/bugs/templates/ projects/<name>/priority/templates/
bash scripts/intake.sh --project <name> --file /path/to/vX.Y.Z-slug.md      # requirement
bash scripts/intake.sh --project <name> --file /path/to/BUG-0000-slug.md    # bug (number is assigned)
bash scripts/intake.sh --project <name> --file /path/to/PRIORITY-0000-slug.md
```
Routing is by filename prefix. A requirement's `## Target Version` must be above the project's last released version and within its ceiling (`max_major`, `max_minor`, `max_patch` in `projects/<name>/project.cfg`; change with `scripts/set-version-ceiling.sh --project <name> --minor N`). A bug reporting the same defect as an open one is refused and the existing ID is named. Write the behaviour and the fix in the file; never a history, never other projects' names.

**A task is BLOCKED.** Read its blockers, supply what it asked for, requeue it. Never edit its status file by hand.
```bash
bash scripts/show.sh --project <name> --key <TASK-ID>
bash scripts/task-status.sh --project <name> --key <TASK-ID> --state BACKLOG --needs-human no
bash scripts/wontdo.sh --project <name> --key <TASK-ID>          # retire it instead
bash scripts/reset.sh  --project <name> --key <TASK-ID>          # fresh start; refuses a WORKING task
```
`reset` keeps a feature branch that holds unmerged commits and the task resumes from it on its next dispatch; `--discard-branch` deletes it instead and prints the discarded commit count and tip. A fully merged branch is always deleted.
OVERWATCH retries transient provider errors on its own ladder; a block that keeps coming back is a bug to file, not a status to flip repeatedly.

**Stop or resume a project.**
```bash
bash scripts/halt.sh --project <name>              # stop this project's wakes now
bash scripts/unhalt.sh --project <name>
bash scripts/halt-global.sh                        # stop every project
bash scripts/halt-after.sh --project <name> --until vX.Y.Z   # finish the release in flight, then halt
```
A halt is the only way to make the tree safe to touch. Halt before an upgrade, before a hand edit of anything under `projects/<name>/`, and before `reset`. A HALT file is never removed by anyone but the operator or the unhalt script.

**The lane says STUCK-LOCK or a lock looks wedged.**
```bash
bash scripts/release-locks.sh --project <name>              # read provenance; releases only a dead holder
```
Never delete lock files or `[A]` queue markers by hand. If the holder pid is alive, wait or halt.

**A release is weak, stopped, or the operator wants to see what shipped.**
```bash
bash scripts/release-state.sh --project <name>
bash scripts/verify-rc-state.sh --project <name>
bash scripts/cm/release.sh --project <name> --decide-only     # which ship-policy trigger would fire
bash scripts/cm/cancel-rc.sh --project <name>                 # abandon the RC branch, reset state
bash scripts/unwind-rc.sh --project <name>                    # fully unwind an in-flight RC
```
Releases ship by default with a status (FUNCTIONAL, KNOWN-BUGS, NON-FUNCTIONAL) in `release-notes/vX.Y.Z.md` in the project's repo; a weak release is normal and gets fixed by the next bundle. Only the operator pushes a public `main`; the kanban never does.

**Bugs are piling up or not being picked up.** Deferral is a posture: bugs at or below `deferred_bug_level` in `project.cfg` wait until a higher bug, a bound, or a ride-along opens a bundle. Lower the level (`low`) to flush them in one bundle, then restore it. `scripts/defer.sh` / `undefer.sh` arm a campaign; `why --details` shows the posture.

**Cost and pace.**
```bash
bash scripts/metrics-report.sh --project <name>            # per-RC wall time, tasks, tokens
bash scripts/metrics-report.sh --reliability               # hands-off and clean streaks
bash scripts/cost-report.sh
```

**Upgrade the live install.** Only when no wake chain is running: halt every project, confirm no `locks/repo-wake-*.lock` exists, then from the install:
```bash
bash scripts/upgrade.sh <tag> --force [--add-claude-agents --add-codex-agents] [--add-claude-skills --add-codex-skills]
```
Pass the agent flags when role files changed, the skill flags when skills changed. Reinstall the crontab if its template changed (`scripts/install-crontab.sh --wake-tier <tier> --yes`). Unhalt last.

## Rules that are not negotiable

- Never edit code in the live install or the dev tree by hand. Intake a bug or requirement.
- Never hand-edit a task's `status.md`, a queue file, a lock or a `[A]` marker. Use `task-status`, `reset`, `wontdo`, `close`, `release-locks`.
- Halt before touching anything under `projects/<name>/` by hand and before an upgrade.
- Never push, pull or fetch in a project's repository on the kanban's behalf; CM owns origin, and public pushes are the operator's.
- Prompts and governance (`roles/*.md`, `SOP-core.md`, `DIRECTIVES.md`, `OVERVIEW.md`) are operator patches made during a planned stop, never tickets.
- Every command is its own confirmation: there are no prompts, so read `--help` before a destructive verb (`delete`, `remove-project --force`, `cancel-rc`).
- Report outcomes as the scripts print them. If a verdict or exit code is not what the operator expected, say so with the output.

## Where the detail is

- `docs/operator-commands.md`: every verb, flag and refusal.
- `docs/OPERATIONS.md`: HALT scopes, the inbox, deferral, ship posture, deep-verify, cleanup, the dashboard, metrics.
- `docs/dashboard.md`, `docs/DASHBOARD-PANES.md`: windows, panes, keys.
- `projects/<name>/README.md`: that project's runners, lints and conventions.
- `SOP-core.md`, `OVERVIEW.md`: how the agents think, if the operator asks why an agent did something.
