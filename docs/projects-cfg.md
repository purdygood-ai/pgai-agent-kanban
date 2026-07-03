# projects.cfg

Reference for the `projects.cfg` registry: format, fields, precedence, operator workflows, and migration from the legacy colon format.

## Overview

`projects.cfg` is the single registry file that tells this kanban installation which projects it manages, in what order to iterate them, and how to display them on the unified dashboard.

It lives at the kanban root:

```
$PGAI_AGENT_KANBAN_ROOT_PATH/projects.cfg
```

The wake scripts, the dashboard, and the project-management scripts (`create-project.sh`, `add-project.sh`, `remove-project.sh`) all read this file through the helpers in `scripts/lib/projects.sh`. Operators normally edit it through those scripts, but hand-editing is supported and expected for small adjustments.

`projects.cfg` is a runtime file. It is not version-controlled with the framework; each installation owns its own copy. The shipped reference template is `example_projects.cfg` at the repository root — copy it to the kanban root to bootstrap a new installation.

## Format Specification

`projects.cfg` is INI-formatted. Each registered project gets one section with a header of the form `[project:NAME]` followed by `key=value` fields.

```ini
[project:pgai-agent-kanban]
priority=1
description=Self-build kanban framework
enabled=true
dashboard_color=#378ADD
dashboard_max_rows=20

[project:video-editor]
priority=2
dashboard_color=#1D9E75
dashboard_max_rows=30
```

Syntax rules:

- **Section header** — `[project:NAME]`, where `NAME` matches `[a-zA-Z0-9_-]+` and corresponds to a directory under `$KANBAN_ROOT/projects/<NAME>/`. Other section name patterns are rejected with a clear error pointing at the offending line.
- **Fields** — one `key=value` pair per line, under the section that owns them. Keys are lowercase with underscores. Whitespace around the `=` is not required but is tolerated.
- **Comments** — lines beginning with `#` are ignored. Blank lines are ignored.
- **Empty sections are valid** — a `[project:foo]` header with no fields registers the project at default values. A warning is emitted if `priority` is needed but unset.
- **Duplicate section names** — last definition wins.

The format is intentionally extensible: new `dashboard_*` fields can be added in future releases without changing the file structure or the parser API.

Refer to `example_projects.cfg` in the repository root for a fully annotated template suitable for copy-and-edit.

## Field Reference

The following fields are recognized under each `[project:NAME]` section:

| Field                | Type       | Default                                    | Purpose                                                                 |
|----------------------|------------|--------------------------------------------|-------------------------------------------------------------------------|
| `priority`           | integer    | none (required for ordering)               | Wake-script iteration order; lower value = higher priority.             |
| `description`        | free-text  | empty string                               | Human-readable description; documentation and display only.             |
| `enabled`            | boolean    | `true`                                     | When `false`, project is skipped by the wake-script iteration loop.     |
| `dashboard_color`    | `#RRGGBB`  | palette fallback                           | Color tag for the project on the unified visibility dashboard.          |
| `dashboard_max_rows` | integer    | `20` (or `DASHBOARD_MAX_ROWS` env if set)  | Maximum task rows shown per visibility column for this project.         |

Validation rules:

- **`priority`** — integer. Projects without a `priority` are treated as lowest priority for iteration order.
- **`enabled`** — accepts `true` or `false`. Any other value is rejected.
- **`dashboard_color`** — must be `#RRGGBB` (six hex digits with a leading `#`). The forms `0xRRGGBB` and short `#RGB` are rejected.
- **`dashboard_max_rows`** — positive integer in the range `5`–`100`. Values outside that range are clamped to the nearest endpoint with a warning. Non-integer values are rejected.

Future fields reserved for later releases (commented out in `example_projects.cfg`) include `dashboard_refresh_seconds`. These have no effect today and are mentioned only so operators know not to use those names for ad-hoc keys.

## Precedence

Per-project fields, global environment variables (where they exist), and code defaults form a clear chain. The first source that supplies a value wins.

For `dashboard_max_rows`:

| Source                                       | Precedence | Notes                                                                 |
|----------------------------------------------|------------|-----------------------------------------------------------------------|
| Per-project `dashboard_max_rows` in `projects.cfg` | Highest    | Set under the project's `[project:NAME]` section.                     |
| Global env `DASHBOARD_MAX_ROWS` (in `kanban.cfg`)  | Fallback   | Used when the per-project field is unset.                             |
| Code default `20`                            | Final      | Used when neither per-project nor environment value is set.           |

For `dashboard_color`: per-project value wins; otherwise a deterministic palette color is assigned based on registration order. There is no environment-variable override for color.

For `enabled`, `description`, and `priority`: per-project values only; no environment fallback exists. Code defaults apply when the field is omitted.

In short: a per-project setting always wins, the global environment variable is consulted only for `dashboard_max_rows`, and the code default is the last resort.

## Operator Workflows

The three operator scripts are the supported way to mutate `projects.cfg`. All three accept `--dry-run` to preview changes without writing.

### Add a brand-new project

`scripts/create-project.sh <name>` bootstraps a new project directory under `projects/<name>/` (with `PROJECT.cfg`, queue files, requirements/, bugs/, priority/, and an empty `release-state.md`) and registers it in `projects.cfg`. New projects are dormant by default (`max_minor=0` and `max_major=0` in `PROJECT.cfg`) until the operator raises the ceiling.

```bash
scripts/create-project.sh --project my-new-project
scripts/create-project.sh --project my-new-project --priority 3 --color "#9E5A1D"
```

### Register an existing on-disk project

`scripts/add-project.sh --project <name>` registers a project directory that already exists under `projects/<name>/` (for example, one migrated from another machine). It updates `projects.cfg` only; it does not touch the project directory contents.

```bash
scripts/add-project.sh --project imported-project
scripts/add-project.sh --project imported-project --priority 4
```

### Unregister or remove a project

`scripts/remove-project.sh <name>` unregisters a project from `projects.cfg`. With `--force` it also deletes the project directory under `projects/<name>/`. It refuses to remove a project that has an active RC in flight unless `--force` is given (and warns even then).

```bash
scripts/remove-project.sh old-project              # unregister only
scripts/remove-project.sh old-project --force      # also rm -rf the directory
```

### List or inspect projects

The dashboard scripts are the canonical way to view the registry visually — `scripts/dashboard/attach.sh` starts the unified tmux dashboard, which renders one column per registered project using each project's `dashboard_color` and `dashboard_max_rows`.

For a non-interactive listing, read `projects.cfg` directly or source `scripts/lib/projects.sh` and call `projects_cfg_list` to get project names in priority order.

### After editing

After any edit to `projects.cfg` (whether via script or by hand), restart the tmux dashboard session so it picks up new colors and row counts. The wake scripts re-read the file each invocation and need no restart.

## Migration Guide

If your `projects.cfg` is in the legacy colon format, run the migration script when you are ready to switch:

```bash
scripts/migrate/projects-cfg.sh
```

The script is idempotent — running it on a file that is already in INI format prints a friendly message and exits without touching anything. Running it on a colon-format file does three things:

1. Writes a backup of the original file to `projects.cfg.colon-format-backup` next to `projects.cfg`. The script refuses to overwrite an existing backup unless `--force` is given.
2. Converts `projects.cfg` to INI format in-place. Existing `priority` and `dashboard_color` values are preserved without information loss.
3. Prints a confirmation showing the backup path and a one-liner you can use to restore the original if needed.

Useful flags:

- `--dry-run` — preview the planned actions without writing.
- `--force` — overwrite an existing `projects.cfg.colon-format-backup` and proceed.

The backup file exists as a single-step rollback target. If the conversion produced something unexpected, restore it with:

```bash
cp projects.cfg.colon-format-backup projects.cfg
```

You do not need to run the migration immediately. The parser accepts both formats during the deprecation window, and the operator scripts will auto-migrate the file the first time you add or create a project (unless you pass `--no-migrate` to opt out).

Migration is operator-initiated by design. The parser never silently rewrites your file: it warns once per process when it reads the colon format and then continues.

## Legacy Colon Format

The colon format is the previous syntax for `projects.cfg`. Each project occupied one line of `name:priority[:color]`:

```
pgai-agent-kanban:1:#378ADD
video-editor:2:#1D9E75
```

It is **deprecated** and will be **removed in a future major release** (the parser's deprecation warning names `v1.0.0` as that target). Until then it remains fully supported: the parser detects and reads it transparently, all operator scripts handle it, and no data is lost. The only visible behavior change is a one-time deprecation warning per parser invocation:

```
[projects.cfg] WARNING: colon-format detected. This format is deprecated.
[projects.cfg] WARNING: Run 'scripts/migrate/projects-cfg.sh' to convert to INI format.
[projects.cfg] WARNING: Old format will be removed in v1.0.0.
```

The warning is emitted once per process — not once per line — to keep logs readable.

When that warning appears, the recommended path is to run `scripts/migrate/projects-cfg.sh` and switch to the INI format at your convenience. The colon format cannot express the new per-project fields (`description`, `enabled`, `dashboard_max_rows`), so projects that need those fields must use INI either way.

Mixed-format files (some colon lines plus some INI sections) are not a supported stable state. The parser treats any file with at least one `[` section header as INI and ignores stray colon lines, but the operator scripts emit a mixed-format warning when they would produce one — your cue to run the migration script and end up in a clean state.
