# Public Contract — What You Can Depend On Within a Major Version

This page declares the stable surface of pgai-agent-kanban within a major
version line. Anything listed here changes only with a major version.
Anything NOT listed here may change in any minor or patch release; where a
change breaks an existing install, it ships with a migration script.

## Intake filename keys

Files deposited into a project (via `intake.sh` or a direct copy) are routed
and validated by filename:

| Kind | Pattern | Notes |
|---|---|---|
| Bug | `BUG-NNNN[-YYYYMMDD]-slug.md` | NNNN is exactly four digits; the date segment is optional |
| Priority | `PRIORITY-NNNN[-YYYYMMDD]-slug.md` | same rules as bugs |
| Requirements | `vX.Y.Z-slug.md` | the version is the target version |

The internal `# BUG-NNNN...` / `# PRIORITY-NNNN...` heading must match the
filename's ID. Malformed files are quarantined to the project's
`rejected/` directory with a `.reason` sidecar — never silently dropped (see
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
- Workflow types are plugins under `$KANBAN_ROOT/workflows/<name>/`. The
  framework ships `release` (git RC lifecycle, tags on main), `document`
  (versioned deliverables published to `projects/<name>/artifacts/`), and
  `testing-only` (label-versioned, read-only, report finalize). Operators
  may add their own — see the workflows-dir contract below.
- Git contract: working agents never push, pull, or fetch; CM is the sole
  origin-toucher; per-project `branch_prefix` isolates managed branches and
  tags.
- The governance reading order: DIRECTIVES → OVERVIEW → SOP → README → role
  file → task README → task status → requirements.

## Custom workflow types (workflows-dir survival and naming)

Operators may author their own workflow-type plugins under
`$KANBAN_ROOT/workflows/<name>/` — the same directory the framework's own
plugins live in. Two contract guarantees apply within a major version.

- **Upgrade survival.** A plugin directory absent from the shipped source
  tree survives `upgrade.sh --force` byte-identical. The mechanism is an
  overlay copy that refreshes shipped files and leaves operator-authored
  directories untouched. A custom `$KANBAN_ROOT/workflows/acme-deploy/`
  does not need to be backed up, git-tracked, or re-installed after an
  upgrade.
- **Naming rule.** A custom plugin whose name matches a shipped plugin
  (`release`, `document`, `testing-only`) is silently overwritten by the
  upgrade's overlay copy. The generator (`create_new_workflow`) refuses
  these names to prevent the collision at authoring time. Operators
  authoring custom types should use org-prefixed names (e.g.
  `acme-deploy`, `contoso-audit`) so future shipped types cannot collide
  with them either.

See [creating-a-workflow.md](creating-a-workflow.md) for the full authoring
walkthrough (both the live-operator no-git path and the contributor
dev-tree path).

## Environment

- `$PGAI_AGENT_KANBAN_ROOT_PATH` (the live install root), `shell-env` as the
  canonical way to set it, `$PGAI_PROJECT_NAME` and `$PGAI_DEV_TREE_PATH` as
  the sanctioned explicit overrides, and the configured temp root (framework
  writes stay under it, never bare `/tmp`).
- Configuration precedence: environment variable > project config > default,
  fail-loud on missing required keys.
- **The four-concerns separation of the root config files.** `kanban.cfg`
  carries kanban behaviour, `shell-env` the shell environment the kanban
  needs to run, `claude-env` tuning the provider CLI binary itself reads, and
  `secrets` credentials. Which file a value belongs in is decidable, and the
  boundary does not move within a major version. It is load-bearing, not cosmetic: a
  provider-tuning value that lands in `secrets`, or a credential that lands in
  `claude-env`, defeats the separation all four exist to preserve. The full
  statement — one sentence per file plus what each misplacement costs — is
  [HOW_TO.md](../HOW_TO.md) section 1.2, "The four-concerns law"; it is
  canonical there and deliberately not duplicated here.
- **Sourcing order: `claude-env` after `shell-env`, on every entry path**,
  including a cron or pseudocron firing. A name set in both files resolves to
  the `claude-env` value. `claude-env` is optional — an absent file is a
  clean no-op — and a syntactically broken one is refused before it is
  sourced rather than half-applied.
- **Operator config survives upgrade.** `kanban.cfg`, `shell-env`,
  `claude-env`, `secrets`, `projects.cfg` and each project's `project.cfg`
  are never overwritten by install or upgrade once they exist; the
  `*_example` templates beside them are refreshed. Where the framework
  derives a runtime value the operator may also set — the provider CLI's
  background-wait ceiling, derived from the per-task budget — the operator's
  value wins.

## Explicitly NOT stable

Internal script names and locations under `scripts/lib/`, dashboard layout
and rendering, role-file wording, metrics file formats, and the test suite's
shape may all change in any minor or patch release without notice. Build against the surfaces
above, not against internals.
