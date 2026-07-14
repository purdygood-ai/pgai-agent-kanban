<!--
GUIDE NOT GATE: This template is a guide, not a gate.

The structure here is a starting point. Agents consuming this document must
tolerate imperfect, partial, or reordered sections. The absence of a section
is never a hard error — agents should infer reasonable defaults and continue.

Contract for agents reading requirements produced from this template:
  - Required fields: Overview, Goals, Deliverables, Acceptance Criteria
  - Optional fields: Tech Stack, Constraints, Context Paths, Notes
  - If a required field is missing or vague, flag it in status.md and proceed
    with the best available interpretation
  - Never block work solely because a template section is absent
-->

# Project: <project-name>

## Overview
What is this project? 2-3 sentences max.

## Goals
What must be true when this is done? Bullet each goal.

- Goal 1
- Goal 2

## Tech Stack
What languages, frameworks, tools are in play?

- Python 3.12 / FastAPI
- PostgreSQL
- etc.

## Working Directory
<!--
Where should Claude work on this project?

Possible values:
  - An absolute path: /home/user/projects/my-app
    All tasks for this project will share this directory.
    Use this when you have an existing project tree or want one in a
    specific location.

  - "local-development-only"
    All tasks work inside their own task artifacts/ folder. Useful for
    throwaway experiments or trivial single-task projects.

  - Leave omitted / blank
    The PM agent defaults to:
    $PGAI_AGENT_KANBAN_ROOT_PATH/workspaces/projects/<project-name>/
-->

## Target Version
<!--
The semver version this requirements doc is for, in vX.Y.Z format.

Examples: v0.4.0, v1.2.3

The version is propagated to each generated task's `## Release Version` field
and drives the release-lifecycle automation: the CM subagent opens the RC branch
(`rc/vX.Y.Z`) at the start of the plan and ships it at the end.

You may leave this blank if the work isn't release-driven.
-->

vX.Y.Z

## Git Repo
<!--
The git repository for this work, if any.

Possible values:
  - A repo URL: git@github.com:org/repo.git
  - "none" — no git operations needed

Branch convention enforced by the system:
  - CM opens a release-candidate branch `rc/vX.Y.Z` from the prefixed
    main branch (prefixed per project.cfg branch_prefix, e.g.
    ai_rc/ai_vX.Y.Z branches from ai_main)
  - Each task works in its own per-task git worktree on a feature branch
    named `feature/<task-id>`, branched from the RC branch — never from
    main, where <task-id> follows the agent-prefixed kebab-case format:
    CODER-YYYYMMDD-NNN-short-slug   (e.g. CODER-20260501-001-add-login)
    CM-YYYYMMDD-NNN-short-slug      (e.g. CM-20260501-001-open-rc)
    PM-YYYYMMDD-NNN-short-slug      (e.g. PM-20260501-001-decompose)
    WRITER-YYYYMMDD-NNN-short-slug
    TESTER-YYYYMMDD-NNN-short-slug
  - Tasks merge their feature branch back into the RC branch locally with
    --no-ff. Working agents NEVER push, pull, or fetch — CM is the sole
    origin-toucher (it pushes the release squash and tag)
  - The prefixed main branch receives one squash per release (CM's
    release squash), then the tag. A post-squash fidelity gate asserts
    the RC and main trees are byte-identical before the tag. The
    operator creates the prefixed main branch with init-project-git-repo.sh
    — never agents

Anti-pattern acknowledgment: this bypasses PR review on individual tasks.
That's intentional for autonomous work. TESTER verifies the release
candidate against this requirements document before CM ships.
-->

## Workflow Type
<!--
Controls the task assembly pipeline used by pm_materialize.py.

Valid values:
  release   — Standard release lifecycle. CM-open is injected as ticket 1,
               features follow, then TESTER (if Test Required = true), then
               CM-release. Requires ## Target Version to be set.
               This is the default.

  feature   — Lightweight feature workflow. A CODER create-shared-branch
               ticket is injected as ticket 1. Feature tasks follow. TESTER
               is appended when Test Required = true. No CM bookends.
               No RC branch is created. Requires ## Source Branch to be set
               (or an Active RC must be present in the project's
               release-state.md).

Leave blank or omit to accept the default (release).
-->

release

## Source Branch
<!--
The shared feature branch all tasks in a 'feature' workflow will branch from
and merge back into. Required when Workflow Type = feature.

Example: feature/my-big-feature

The CODER create-shared-branch ticket (ticket 1 of feature workflows) will
create this branch from Parent Branch and push it to origin.

Ignored when Workflow Type = release.

If Workflow Type = feature and this field is blank, the materializer falls
back to the Active RC from the project's release-state.md. If neither is
set, the materializer exits with an error.

Leave blank when Workflow Type = release.
-->

none

## Test Required
<!--
Controls whether a TESTER verification task is appended after all feature
tasks complete.

Valid values:
  true      — A TESTER task is appended. The TESTER verifies all feature
               tasks against the requirements document and produces a report.
               This is the default.

  false     — No TESTER task is appended. The workflow ends after the last
               feature task (or CM-release for release workflows).

Leave blank or omit to accept the default (true).
-->

true

## Parent Branch
<!--
The branch that the shared feature branch is created from. Used by the CODER
create-shared-branch ticket in feature workflows.

Default: main

Only relevant when Workflow Type = feature. Ignored for release workflows.

Leave blank or omit to accept the default (main). The prefixed main
branch resolves per project.cfg branch_prefix.
-->

main

## Human Approval Required
<!--
Controls whether the PM agent injects a HUMAN-APPROVE gate task into the
generated plan before the CM release task.

Only applies when Workflow Type = release.

Valid values:
  auto      — No HUMAN-APPROVE task is injected. The release proceeds
               automatically once all feature tasks are complete.
               This is the default.

  required  — A HUMAN-APPROVE task is injected between the final feature
               task and the CM release task. A human must manually advance
               the gate before the release proceeds.

Leave blank or omit to accept the default (auto).
-->

auto

## Model Overrides

<!--
Optional. Use this section to request that specific tasks use a particular
model instead of the subagent default.

Recognized model values:
  opus              (alias for claude-opus-4-7)
  sonnet            (alias for claude-sonnet-4-6)
  haiku             (alias for claude-haiku-4-5)
  claude-opus-4-7   (full model ID)
  claude-sonnet-4-6 (full model ID)
  claude-haiku-4-5  (full model ID)

Format: one hint per line in plain English. The PM agent reads these hints
and sets model_override fields on matching tasks in the plan JSON.

Examples:
  - The scaffolding task should use haiku
  - The migration script task should use opus
  - All documentation tasks should use sonnet
  - The integration test task should use claude-opus-4-7

If this section is omitted or blank, all tasks use their subagent defaults.
-->

## Deliverables
What are the concrete outputs? Be specific about files, endpoints, services.

- A REST API with endpoints X, Y, Z
- A CLI tool that does A, B, C
- Tests with >80% coverage

## Constraints
Hard rules the agents must follow.

- No external API calls without approval
- Must run on Python 3.12
- Must pass ruff linting
- All web services must have Swagger/OpenAPI documentation

## Acceptance Criteria
How does HUMAN know it's done?

- [ ] All endpoints return correct responses
- [ ] Tests pass
- [ ] README updated

## Context Paths
Files the agents should read for domain context (optional).

- /path/to/project/README.md
- /path/to/project/docs/architecture.md

## Notes
Anything else — edge cases, preferences, warnings.

---

## Notes For The PM Agent

The PM agent will read this document and produce a JSON plan with tasks decomposed in dependency order. Each task in the plan inherits the Working Directory, Git Repo, and tech stack constraints from this document.

Tips:

- **Be explicit about file paths and function signatures.** Vague requirements produce vague tasks.
- **Specify acceptance criteria as commands the reviewer can run.** "Run `pytest` and see all tests pass" is testable. "Tests pass" is not.
- **State your tech stack and constraints up front.** Every generated task will inherit them.
- **Mention if there's an existing git repo.** The first task will include cloning or working directory setup.
- **Default max is 15 tasks.** Override with `--max-tasks N` on the pm-agent command. Smaller projects (3-8 tasks) often produce better results than the maximum.
- **Task IDs use agent-prefixed kebab-case.** Generated task IDs follow the pattern
  `<AGENT>-<YYYYMMDD>-<NNN>-<short-slug>`, for example:
  - `CODER-20260501-001-add-login-endpoint`
  - `WRITER-20260501-002-update-api-docs`
  - `CM-20260501-003-open-rc`
  - `TESTER-20260501-004-verify-rc`
  The slug must be lowercase, hyphen-separated, and under 30 characters.
  Which LLM provider ran a task is recorded in the task's tokens.json and
  status.md, not in the ID.

### Ticket 1: Always a CM open-rc operation

**Ticket 1 of every release lifecycle MUST be a CM ticket with `## CM Operation: open-rc`, not a CODER ticket.**

The CM subagent creates the RC branch (`rc/vX.Y.Z`) from the prefixed main branch AND atomically updates the project's `release-state.md` (`Active RC`, `RC Opened At`, `RC Opened By Task`) before any feature tickets begin. The release-state file is per-install at `$KANBAN_ROOT/projects/<project-name>/release-state.md` and is owned by the CM scripts.

Why this matters: CODER agents are prohibited from modifying state files (e.g., `release-state.md`). A CODER create-rc-branch ticket can push the branch but cannot update release-state.md, which causes the CM-release ticket to find "Active RC: none" and block. CM owns RC branch creation for exactly this reason. Released-version state (Last Released) is derived from git tags via `pp_last_released_version`, not stored in `release-state.md`.

Correct ticket 1 shape:
```
## Role
CM

## CM Operation
open-rc

## Source Branch
main

## Feature Branch
none
```

Do NOT generate a CODER ticket to create the RC branch. Always generate a CM open-rc ticket as ticket 1.
