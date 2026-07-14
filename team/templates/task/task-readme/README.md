# Task: <title>

## Task ID
<!-- Format: <AGENT>-YYYYMMDD-NNN-short-slug  e.g. CODER-20260412-001-add-login -->
<TASK-ID>

## Owner
CLAUDE | HUMAN

## Role
CODER | WRITER | TESTER | CM | PM | PO

## Assigned Agent
<!--
OPTIONAL. The specific agent subtype assigned to execute this task.
Accepts: CODER, WRITER, TESTER, CM, PM, PO
Default: derived from the Role field (Role CODER → CODER, Role WRITER → WRITER).
Set explicitly when routing to a specialized agent subtype that differs from
the generic Role label, or when the PM agent populates agent routing metadata.
-->
none

## Release Version
<!--
OPTIONAL. The release milestone this task is scoped to.
Accepts: a semver string like v0.2.0, or "none" if not tied to a release.
Used by the PM agent and release tooling to group tasks by release.
-->
none

## Working Directory
<!--
The directory the agent should cd into and work in.

Possible values:
  - An absolute path: /home/user/projects/my-app
  - "local-development-only" — work entirely inside this task's artifacts/
    directory; throwaway local work, not destined for any repo or deployment
  - "none" — same effect as local-development-only; use artifacts/

For PM-decomposed multi-task projects, all tasks in the same project
should share the same Working Directory so they collaborate on one tree.
-->
none

## Git Repo
<!--
The git repository for this work.

Possible values:
  - A repo URL: git@github.com:org/repo.git
  - "none" — no git operations needed (working directory is plain folder
    or local-development-only)

Branch convention: features always branch from the Source Branch (default
main, prefixed per project.cfg branch_prefix). The RC branch is the
usual intermediate branch during a release cycle; main receives one
squash per release from CM.

If the working directory is not yet a git checkout and Git Repo is set,
the FIRST task to run for this project must clone it. If the prefixed
main branch does not exist on the remote, the operator creates it with
init-project-git-repo.sh.
-->
none

## Source Branch
<!--
The branch this task's feature branch should be created from.
Default: main (the prefixed main branch resolves per project.cfg
branch_prefix). For release-workflow tasks, the materializer overrides
this to rc/<target_version>.

Future use: a parent feature branch name for sub-features. Currently the
PM agent only generates one layer deep, so this is always main, an RC
branch, or none.
-->
none

## Feature Branch
<!--
The branch name this task should create and work on.
PM-generated tasks use the convention: feature/<task-id>
For tasks with no git involvement, use "none".
-->
none

## Model Override
<!--
OPTIONAL. Override the model used to execute this task.

Accepts:
  - Aliases:    opus, sonnet, haiku
                (resolved to the current default version of each tier)
  - Full names: claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5, etc.
  - Empty, "none", or omit the field entirely for no override — the wake
    script will use the system default model.

Behavior: when set to a non-empty, non-"none" value, the wake script passes
the model to the agent invocation so the task runs on the specified model.
Use this to pin expensive tasks to a cheaper model, or to require a more
capable model for a complex task.
-->
none

## Goal
What must be achieved.

## Inputs
Files, tickets, comments, repos, links, or services relevant to the task.

## Context Paths
<!--
List the additive README chain the agent should read before working.
Order matters — read parent before child.
Make sure the file path exists before adding it.
Example:
- /path/to/project/README.md
- /path/to/project/docs/architecture.md
Leave blank if no domain context is needed.
-->

## Required Output
Exactly what should exist when the task is complete.

## Constraints
Rules specific to this task.

## Acceptance Criteria
- [ ] How the reviewer will decide this task is good enough
- [ ] Each criterion should be testable

## Prerequisites
<!--
Other tasks that must be in DONE or WONT-DO state before this
task can start. List by full task ID, one per line, with a leading dash.

If any prerequisite is not yet satisfied when this task is pulled from
the queue, the wake script will set this task to WAITING state and the
[W] queue marker. The wake script will automatically promote it back to
BACKLOG when prerequisites complete.

Use "none" if this task has no prerequisites and can start immediately.
-->
none

## Notes
Any clarifications, assumptions, or edge cases.
