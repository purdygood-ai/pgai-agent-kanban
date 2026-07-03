# Public Contract — What You Can Depend On Across 1.x

This page declares the stable surface of pgai-agent-kanban for the 1.x line.
Anything listed here changes only with a major version. Anything NOT listed
here may change in any 1.x release; where a change breaks an existing
install, it ships with a migration script.

## Intake filename keys

Files deposited into a project (via `intake.sh` or a direct copy) are routed
and validated by filename:

| Kind | Pattern | Notes |
|---|---|---|
| Bug | `BUG-NNNN[-YYYYMMDD]-slug.md` | NNNN is 4+ digits; the date segment is optional |
| Priority | `PRIORITY-NNNN[-YYYYMMDD]-slug.md` | same rules as bugs |
| Requirements | `vX.Y.Z-slug.md` | the version is the target version |

The internal `# BUG-NNNN...` / `# PRIORITY-NNNN...` heading must match the
filename's ID. Malformed files are quarantined to `.rejected/` with a
`.reason` sidecar — never silently dropped (see
[quarantine-recovery.md](quarantine-recovery.md)).

## Operator command surface

- The unified `--project` / `--key` vocabulary on operator commands:
  `show`, `reset`, `close`, `wontdo`, `delete`, `halt` / `unhalt` /
  `halt-after`, `halt-global` / `unhalt-global`, `intake`, `unwind-rc`,
  `create-project` / `add-project` / `remove-project`,
  `set-version-ceiling`, `switch-provider`, `ship-rc`.
- `--key` is self-identifying (`BUG-NNNN`, `PRIORITY-NNNN`, `vX.Y.Z`,
  `AGENT-YYYYMMDD-NNN`), resolves by prefix, and write commands refuse on
  ambiguity.
- **No default project.** Project resolution is explicit — `--project`,
  `$PGAI_PROJECT_NAME`, or the owning project of the item — or the command
  fails loudly. Aggregation views iterate all registered projects.
- Unknown flags are rejected uniformly; `--help` reflects each command's
  actual flag set.

## Project layout and state

- Per-project state lives under `projects/<name>/`: `project.cfg`, `tasks/`,
  `requirements/`, `priority/`, `bugs/`, `artifacts/`, `release-state.md`,
  and optional `hooks/` and `HALT` / `HALT-AFTER`.
- `project.cfg` keys: `dev_tree_path`, `git_repo_url`, `workflow_type`,
  `branch_prefix`, and the version-ceiling keys.
- `release-state.md` fields `Active RC` / `Last Released`, with `v0.0.0` as
  the fresh-install sentinel: a new project accepts whatever version its
  first requirements document declares.
- Files on disk are the source of truth; the framework never requires a
  database.

## Task and workflow model

- The six-state task model: BACKLOG, WAITING, WORKING, BLOCKED, DONE,
  WONT-DO. There is no review state; anything needing human eyes is BLOCKED
  with a reason.
- Task IDs: `<AGENT>-YYYYMMDD-NNN-slug`.
- Workflow types `release` (git RC lifecycle, tags on main) and `document`
  (versioned deliverables published to `projects/<name>/artifacts/`).
- Git contract: working agents never push, pull, or fetch; CM is the sole
  origin-toucher; per-project `branch_prefix` isolates managed branches and
  tags.
- The governance reading order: DIRECTIVES → OVERVIEW → SOP → README → role
  file → task README → task status → requirements.

## Environment

- `$PGAI_AGENT_KANBAN_ROOT_PATH` (the live install root), `shell-env` as the
  canonical way to set it, `$PGAI_PROJECT_NAME` and `$PGAI_DEV_TREE_PATH` as
  the sanctioned explicit overrides, and the configured temp root (framework
  writes stay under it, never bare `/tmp`).
- Configuration precedence: environment variable > project config > default,
  fail-loud on missing required keys.

## Explicitly NOT stable

Internal script names and locations under `scripts/lib/`, dashboard layout
and rendering, role-file wording, metrics file formats, and the test suite's
shape may all change within 1.x without notice. Build against the surfaces
above, not against internals.
