# OPERATIONS — Operator Walkthrough

<!--
Audience: the human operator running a pgai-agent-kanban install.

This file is NOT part of the agent read order defined in team/SOP.md
under "Required Read Order For Assigned Work." Chain agents (PO, PM,
CODER, WRITER, TESTER, CM) do not read this file; their contract lives
in team/SOP.md, team/DIRECTIVES.md, and team/roles/<ROLE>.md.

Operators read this file. It collects operator-facing walkthroughs,
recovery procedures, and dashboard/metrics tours that used to sit
inside team/SOP.md before the v1.15.0 SOP/OPERATIONS split.
-->

This document is the operator-facing companion to `team/SOP.md`. It
collects the walkthroughs, recovery procedures, and dashboard/metrics
tours a human at the keyboard needs to run the kanban. Agent-facing
contracts (task states, role swim lanes, workflow types, ship policy,
autonomy criterion) remain in `team/SOP.md`.

For the four SPLIT sections that live in both files, each side carries
a one-line pointer to the other. That is the only permitted overlap.

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

## HALT Flags — operator commands

The chain-side semantics of `HALT` (what it does, who honors it, who may create it) live in `team/SOP.md` under "HALT Flags." This section covers the operator create/remove commands.

### Setting and clearing the flag

- To pause the chain manually: `touch ${PGAI_AGENT_KANBAN_ROOT_PATH}/HALT`
- To resume the chain: `rm ${PGAI_AGENT_KANBAN_ROOT_PATH}/HALT`

The operator is the only role that removes `HALT`. CM never removes it. Removal is the operator's signal that the underlying issue is resolved and the chain may resume.

## When the chain halts

A halted chain is the autonomous system's signal that operator judgment is required. The operator removes the `HALT` file when the underlying issue is resolved. This section is the operator-facing playbook for diagnosing and resolving an autonomous HALT.

The chain may halt for one of two reasons: the operator created `HALT` manually (covered above), or CM created `HALT` autonomously because one of its eight HALT triggers fired. The procedure below covers the autonomous case.

### CM HALT triggers

CM creates `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT` for any of the following eight conditions. `team/roles/CM.md` is the authoritative reference; this list summarizes for operator orientation.

1. **TESTER state is `BLOCKED`** — TESTER could not complete verification (pre-flight failure, runner crash, missing requirements, unreachable dev tree).
2. **TESTER systemic_risk is `high`** — TESTER's report-level systemic risk is `high`. Indicates a broader framework regression or a stuck CODER loop.
3. **Any finding has Fix Effort = `large` in a `SHIP-WITH-SERIOUS-CONCERNS` context** — shipping through a large-effort serious finding is too risky; operator must scope the work first.
4. **Pre-squash hook fails** — the resolved pre-squash hook exited non-zero, or the phase is required (`cm_release_pre_squash_hook_required = true`) and no hook resolved at any tier. Finalization mechanic broken. See "Release Lifecycle Hooks" below for the three-tier resolution model and the required-flag semantics.
5. **Squash to main has conflicts** — git state damaged; human judgment required before any branch mutation continues.
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

## HALT-AFTER — operator arm and resume

The grammar, drain semantics, auto-promotion behavior, and audit-entry shape for `HALT-AFTER` are documented in `team/SOP.md` under "HALT-AFTER (soft halt)." This section is the operator arm/resume walkthrough.

### File location and scope

`HALT-AFTER` mirrors `HALT` exactly. Two scopes, same precedence rules as the hard flags.

| Flag | Path | Scope |
|---|---|---|
| Global `HALT-AFTER` | `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT-AFTER` | Every chain agent across every project |
| Per-project `HALT-AFTER` | `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT-AFTER` | Only the named project |

### Arming

Write the desired event token into the file:

```bash
echo rc > HALT-AFTER          # halt after the current RC ships
touch HALT-AFTER              # empty file defaults to the rc token
echo writer > HALT-AFTER      # halt after WRITER queue idles
```

Valid tokens are `rc`, `pm`, `coder`, `writer`, `tester`, and `cm`. See the SOP-side "HALT-AFTER (soft halt)" section for full drain semantics.

### Operator resume

Resume is unchanged from a normal `HALT` resume:

```bash
# Resume after HALT-AFTER auto-promoted to HALT.
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
```

The operator does not interact with `HALT-AFTER` at resume time — it was removed by the auto-promotion step. Only `HALT` remains, and removing `HALT` lets the next wake firing enter the chain again. The chain itself never removes either flag; promotion creates `HALT`, and the operator clears it. See "When the chain halts" for the full operator response procedure.

## Release State File — migration and rationale

The schema, field semantics, and `pp_last_released_version` contract for `release-state.md` are documented in `team/SOP.md` under "Release State File." This section covers the operator-facing install.sh migration and the historical rationale for the split.

### Why the split

Multiple bug classes traced back to the old design where `release-state.md` tried to be authoritative for both in-flight RC state and historical release state. Deterministic patches drifted because `upgrade.sh` shipped tags but never edited the file. Scripts read different copies of `release-state.md` from different paths. `install.sh` did not seed the file uniformly. All of those are gone now: git history is immutable, the tag IS the release, there is nothing to keep in sync with anything.

### Migration

`install.sh` migrates existing installs automatically:

- Preserves `## Active RC` if non-`none` (operators in the middle of an in-flight RC do not lose it).
- Drops `Last Released At` and `Last Released By Task` if present in the old file (these fields are no longer part of the schema).
- Preserves `## Last Released` if present; otherwise leaves it absent so the next `cm-release.sh` Step 15 writes it on the first successful release.
- Writes the canonical schema (`## Active RC`, `## RC Opened At`, `## RC Opened By Task`, `## Last Released`).

The previous repo-level `team/release-state.md` has been removed entirely. The only `release-state.md` files in the system are project-scoped at `$KANBAN_ROOT/projects/<project-name>/release-state.md`. No manual file editing is required during upgrade.

## CHANGELOG Disclosure Model

`CHANGELOG.md` is regenerated in full at every release as a projection of two sources of truth — the per-release notes files and the bug ledger — so historical entries update themselves whenever a bug's `## Affects` or `## Fixed In` fields change. Known Issues are disclosed only for bugs that existed in a PUBLISHED release (a version listed in `release-notes/PUBLISHED`), so internal-only bugs born and fixed between public tags are never surfaced in the changelog. Public IDs (`KI-<version>.<counter>`) are sticky citations: assigned once at first disclosure, persisted on the bug file, and never renumbered — the writer reads them back rather than recomputing them, so any KI reference ever made stays valid. A missing `release-notes/PUBLISHED` manifest is a fail-loud generator error, not a silent empty state — see [promotion-playbook.md](promotion-playbook.md) for the operator step that maintains the manifest after a public tag push.

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

## Environment: Source-Then-Fail-Loud

Every kanban entry point resolves `PGAI_AGENT_KANBAN_ROOT_PATH` the same way, through a single shared prelude (`team/scripts/lib/env_bootstrap.sh` on the bash side, `pgai_agent_kanban/env.py` on the Python side). The prelude runs before any command-specific logic. Operators do not need to source `shell-env` manually on a standard install — but when they do, their exports still win.

The contract is four points, in the order they take effect:

1. **The prelude sources `shell-env` automatically.** Every entry-point script under `scripts/` (top-level, `scripts/cm/`, `scripts/dashboard/`) sources the prelude as its first act after the shebang. If `PGAI_AGENT_KANBAN_ROOT_PATH` is not already set, the prelude derives a candidate root from the calling script's own location, walks upward past the `scripts/`, `cm/`, and `dashboard/` layers, and sources `<candidate>/shell-env` when the file exists. That is where the root env (and PATH, virtualenv, provider env) is normally exported. From a fresh unsourced shell on a standard install, `scripts/api-server.sh status`, `scripts/show.sh`, and any `scripts/cm/*.sh` or `scripts/dashboard/*.sh` script just work — the prelude handled the sourcing.

2. **Fail-loud when `shell-env` is missing or broken.** If the prelude runs, sources what it can, and still finds `PGAI_AGENT_KANBAN_ROOT_PATH` unset, it exits 1 with a diagnostic naming the exact path it tried:

    ```
    PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken at <candidate>/shell-env
    ```

This is the same fail-loud posture established for `api-server.sh`, upgraded to guard the new class of failure — "shell-env missing or broken" — instead of the old class ("operator forgot to source"). A syntax error inside `shell-env`, a wrong path, or a deleted file all surface with the same actionable message. No silent defaults paper over the failure.

One deliberate consequence for upgrades: invoking `upgrade.sh` from
the DEV TREE requires the explicit target
(`PGAI_AGENT_KANBAN_ROOT_PATH=... ` or `--kanban-root`) — the prelude
derives its root from the script's own location and never derives
across trees, so a dev-tree invocation has no live shell-env to find.
This is the two-repo boundary applied to the upgrader itself.

3. **Operator-set env always wins.** If `PGAI_AGENT_KANBAN_ROOT_PATH` is already exported in the caller's shell when the entry point runs, the prelude honors that value verbatim and returns without touching `shell-env`. This is the no-masking guarantee — the operator's explicit env outranks anything the file would have said. `PGAI_AGENT_KANBAN_ROOT_PATH=/custom/root scripts/show.sh …` uses `/custom/root` even when `shell-env` on disk says otherwise. Sourcing the prelude twice is harmless: the second call sees the export and returns immediately.

4. **Manual `source shell-env` still works.** Operators who prefer the classic workflow — source `shell-env` once at the start of a shell session, then run commands — lose nothing. Because the prelude honors an already-set env (point 3), sourcing `shell-env` yourself sets the export before any prelude runs, and every subsequent invocation short-circuits at the idempotency guard. Sourcing manually and letting the prelude do it produce identical results.

The Python side follows the same contract via `pgai_agent_kanban/env.py`: it reads the env, absolutizes with `realpath`, and fails loud with the same message grammar if unset. Python entry points (the API app factory, the changelog writer, the lint CLIs) all route through this one resolver, so the `bash → prelude → shell-env → uvicorn → Python` chain preserves the operator's env end-to-end.

Two consequences worth stating plainly:

- **No conventional default remains.** The historical `:-$HOME/pgai_agent_kanban` fallback has been deleted from every entry point. The prelude is the guard. Non-standard installs that used to self-mask now surface the misconfiguration through the fail-loud message.
- **The wake family still self-bootstraps.** Cron-driven wake scripts have always established their own environment via `wake_common.sh`; that path is preserved and either delegates to the prelude or is verified equivalent. Cron behavior does not change.

For the shell-side reference, see `team/scripts/lib/env_bootstrap.sh`. For the Python-side reference, see `pgai_agent_kanban/env.py`. For the historical decision this supersedes, see `projects/pgai-agent-kanban/bugs/BUG-0040-bare-root-env-refs-fail-loud.md`.

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
2. Checks that the current branch is `${branch_prefix}main` or `rc/<version>`.
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
- **Git unwind.** Checks out `${branch_prefix}main` in the dev tree, deletes the local `rc/<version>` branch (with `-D`, force-delete), deletes the remote `origin/rc/<version>`, and force-deletes any orphan `feature/CLAUDE-*` branches whose task IDs belong to the cancelled RC.
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

## Release Lifecycle Hooks

`cm-release.sh` and the operator-manual `ship-rc.sh` script both call up to three lifecycle hooks during a release: **pre-squash** (before the RC branch is squashed to main), **pre-tag** (after squash to main, before `git tag`), and **post-tag** (after the tag exists, before the push to origin). Hooks are optional by default; configure them per project when there is release work that must run at one of those seams (version bump, consistency check, notification, asset upload).

Both scripts resolve, print, and enforce hooks through the same shared library (`team/scripts/lib/cm_release_hooks.sh`) — behavior is identical between the autonomous CM path and the manual operator path.

### Three-tier resolution precedence

For each phase, the release script searches three locations and uses the first one that yields a path. The tiers, highest precedence first:

| Tier | Source label | Location | Purpose |
|---|---|---|---|
| (a) | `cfg` | `project.cfg [hooks] cm_release_<phase>_hook` | Explicit deployment config. Absolute path, or relative to `dev_tree_path`. |
| (b) | `kanban-side` | `$KANBAN_ROOT/projects/<name>/hooks/cm-release-<phase>.sh` | Kanban-side deployment override. Not versioned with the managed repo. |
| (c) | `in-repo` | `<dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh` | Portable hook that travels with the managed repository. Versioned and reviewed alongside application code. |

The design mirrors the ports-are-topology philosophy: deployment overrides app default. Tier (c) is the project's own default — versioned, reviewed, and immune to project recreation. Tier (b) exists so an operator can wire a temporary override without editing the repo. Tier (a) exists so a hook can live at any explicit path (e.g., a shared script directory).

An in-repo hook (tier c) that exists but is not executable is an **error**, not a silent skip. The release halts with a message naming the path and the `chmod +x` fix. Kanban-side and cfg hooks that exist but are not executable emit a warning and skip (backward-compatible behavior).

### The always-printed hook line

Every release run prints exactly one resolution line per phase, immediately before the phase runs. Absence is never silent again — it is a stated fact in every release log. The two forms:

```
<phase> hook: <resolved absolute path> (source: cfg|kanban-side|in-repo)
<phase> hook: none configured
```

`<phase>` is literally one of `pre-squash`, `pre-tag`, `post-tag`. The `(source: …)` suffix names the winning tier. When nothing resolves at any tier and the phase is not required, the release proceeds and the line reads `none configured`.

Three lines per release is the honest common case for projects without lifecycle hooks. When reviewing a release log after this feature ships, expect to see three of these lines regardless of configuration.

### Required-hook enforcement (opt-in, per phase)

For projects where "no hook" is always wrong — the primary example is PVG's pre-squash version-bump wrapper — set the matching required flag in `project.cfg [hooks]`:

```ini
cm_release_pre_squash_hook_required = true
cm_release_pre_tag_hook_required = true
cm_release_post_tag_hook_required = true
```

Default is `false` for all three keys. Existing projects are unaffected.

When a required flag is `true` and no hook resolves at any of the three tiers, the release **HALTs before the phase runs**. The HALT message names the phase, all three searched locations, and the config key that made the phase required. No tag is created. The autonomous CM path files a BLOCKED CM task and creates the global `HALT` file so the operator can inspect. The manual `ship-rc.sh` path exits non-zero and prints the same message.

The failure grammar matches the lane-fidelity gate: name what was expected, name where it was looked for, name the key that governs the expectation. This is the operator-diagnosable form — everything needed to fix the wiring is in the halt message.

### Hook environment contract

The two scripts run hooks with an identical environment. This is the **canonical reference**; any other doc that discusses hook variables links back here rather than duplicating the list.

| Variable | Value | Notes |
|---|---|---|
| `PGAI_TARGET_VERSION` | The version being shipped (e.g. `v1.18.0`) | Same as the RC's target version. |
| `PGAI_PROJECT_NAME` | Project name as registered in `projects.cfg` | Not derived from `cwd`. |
| `PGAI_PROJECT_ROOT` | Absolute path to `$KANBAN_ROOT/projects/<name>/` | The per-project kanban directory (queues, requirements, release-state.md). |
| `PGAI_DEV_TREE_PATH` | Absolute path to the managed git checkout | Same as `project.cfg [project] dev_tree_path`. Equals `cwd` at hook start. |
| `PGAI_RC_BRANCH` | Full RC branch name (e.g. `ai_rc/v1.18.0`) | Includes the project's branch prefix. |
| `PGAI_KANBAN_ROOT` | Absolute path to the kanban install root | Same as `$PGAI_AGENT_KANBAN_ROOT_PATH`. |

**cwd** is the dev tree path (`PGAI_DEV_TREE_PATH`). Hooks may assume `git` operates against the managed repo without an explicit `-C` flag.

Hook stdout and stderr are captured and prefixed with `[hook <name>]` in the release log. Non-zero exit blocks the release for pre-squash and pre-tag; for post-tag the failure is logged but does not block (the tag already exists, so aborting after it is fruitless).

### Configuring hooks in project.cfg

`project.cfg_example` carries commented `[hooks]` entries for all three phase-hook keys and all three `*_required` keys. See the file for inline documentation; the common patterns:

- **No hook needed.** Leave all six keys commented out (the default). The release prints `none configured` three times and proceeds.
- **Portable hook that survives project recreation.** Drop an executable script at `<dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh` and commit it to the managed repo. The `.pgai/` prefix is the portable in-repo hook location — no `project.cfg` edit is required for the hook to resolve.
- **Required portable hook (fail-loud on missing wiring).** Same as above, plus set `cm_release_<phase>_hook_required = true` in `project.cfg [hooks]`.
- **Kanban-side override.** Drop the hook at `$KANBAN_ROOT/projects/<name>/hooks/cm-release-<phase>.sh` when the hook should NOT ship with the managed repo (temporary, deployment-specific, or a testing override).
- **Explicit path.** Set `cm_release_<phase>_hook` in `project.cfg [hooks]` to point at a script anywhere. Absolute paths are used as-is; relative paths resolve against `dev_tree_path`.

### The pre-squash HALT trigger, in context

The pre-squash hook failure is trigger #4 of the eight CM HALT triggers (see "CM HALT triggers" above). Two failure paths reach that trigger under this design:

- The pre-squash hook resolved and ran, but exited non-zero. The release blocks; the operator inspects the `[hook cm-release-pre-squash]` log lines.
- No hook resolved at any tier and `cm_release_pre_squash_hook_required = true`. The release HALTs before the phase; the operator adds the missing hook or clears the required flag.

Both paths land in the same BLOCKED CM task and the same operator-response procedure. The required-flag path is the pattern the v1.18.0 RC introduces so wiring loss becomes visible immediately rather than drifting silently across releases.

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

### Proxy compatibility: llm_thinking_enabled

Some deployment environments route provider traffic through older HTTP proxies that reject responses carrying extended-thinking (reasoning) blocks; the visible symptom is that every agent invocation fails outright. Set `[providers] llm_thinking_enabled = false` in `kanban.cfg` on such deployments — the framework then passes the provider's thinking-disable flag on each invocation (currently `--thinking disabled` on the Claude lane; the same key maps to the equivalent switch on other provider lanes as they mature). The default is `true` and modern deployments should not change it; leave the key absent for the default behavior.

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

# --- OVERWATCH Tier-1 sweep (hourly at :30) ---
# Deterministic bash sweep across every registered project. Zero LLM cost.
# The :30 minute slot puts the sweep off-beat from every 2-minute agent tick,
# so it never collides with an agent wake.
30 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/overwatch-sweep.sh >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-overwatch-sweep.log 2>&1

# --- OVERWATCH Tier-2 deep-clean (daily at 03:30) ---
# LLM-driven review of the action log and any sweep-flagged anomalies.
# The daily cadence keeps model cost bounded; the 03:30 slot avoids the
# hourly sweep at :30 by an hour and sits during a low-traffic window.
30 3 * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake-batch.sh --agent=overwatch --sleep=0 --max-tasks=1 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-overwatch.log 2>&1
```

The overwatch lines are the two-tier cadence in cron form. Both are staggered off the every-2-minutes agent slots — the hourly sweep fires at the :30 mark (equidistant from :00 and :00+21s and :01+21s slots), the daily deep-clean fires at 03:30. Neither line touches the provider-dispatch pattern above: `overwatch-sweep.sh` is provider-independent bash, and the Tier-2 agent wake honors the standard `[providers] active` resolution just like the vertical agents. See `roles/OVERWATCH.md` for the tier semantics.

The on-BLOCK trigger (in the wake scripts' generic set-BLOCKED path) is the event-driven complement to these cron lines: fresh BLOCKED tasks nudge OVERWATCH within seconds rather than waiting for the next :30 tick. The trigger fires-and-forgets — the block path's exit status is unchanged whether or not the nudge lands, so a broken `wake-now.sh` cannot add a failure mode to the chain. Storms dedupe via the per-agent wake flock: many blocks in one minute produce one OVERWATCH run.

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

## VERSION vs VERSION_DETAIL — the deposit-time stamp split

At deposit time, `install.sh` and `upgrade.sh` write two sibling files under `$KANBAN_ROOT`:

- **`VERSION`** — the operator-facing stamp. Contains the clean tag portion of `git describe --tags` at the deposited HEAD, with the describe suffix (`-N-gSHA`) stripped. At a tag-exact checkout the strip is a no-op; past a tag (a polish commit ahead of the last tag), the suffix is removed. Example: a deposit from `ai_v1.19.0-1-gab12cd3` writes `ai_v1.19.0` to `VERSION`. Every operator-facing renderer — the tmux status-bar `📝` segment, the dashboard header, any `/status` field, the `GET /health` response — reads `VERSION` directly.
- **`VERSION_DETAIL`** — the forensics sibling. Contains the full describe string plus the deposit SHA on a single line (`<full-describe>  deposit-sha=<sha>`). It is **tool-owned**: written by the deposit path, never edited by operator surfaces, and **displayed nowhere by default**. It exists so that when debugging a deploy — "which exact tree is installed on this box?" — the forensic answer is one file read away without polluting the ambient display.

The split is implemented by a single shared helper, `team/scripts/lib/version_stamp.sh` (function `stamp_version_files`). `install.sh` and `upgrade.sh` both source it; there is one implementation of the stamp write, not two.

### Why the split exists

Operator-facing surfaces render what is installed, not deposit forensics. The old single-stamp behavior wrote the raw `git describe` output — for example `ai_v1.16.1-1-gb99f668` — to `VERSION`, so every surface that read `VERSION` carried the describe suffix even when the operator only wanted to see `ai_v1.16.1`. The suffix was noise for reading and useful for debugging, and the two audiences were served by the same file. The split lets each audience read the file that suits it: `VERSION` for operators, `VERSION_DETAIL` for forensic questions.

Zero renderer changes were needed. Every surface that already read `VERSION` renders clean because `VERSION` is now clean.

### The `--stamp-version` override

The `--stamp-version <value>` flag on `install.sh` and `upgrade.sh` is **unchanged**. When the operator supplies an explicit value:

- `VERSION` receives the override value verbatim (explicit values are clean by definition).
- `VERSION_DETAIL` is **not written** — the override carries no forensic suffix to record, and writing a stale `VERSION_DETAIL` from a previous deposit would be a lie.

### The staged-vs-published divergence advisory

The upgrade-time divergence advisory in `upgrade.sh` is **unchanged**. When deposit runs past a released tag (a polish commit ahead of the last shipped version), the advisory still prints — the moment git exactness is surfaced remains the deposit decision, which is when it matters. The stamp split preserves the advisory: `VERSION` renders clean everywhere, `VERSION_DETAIL` records the full describe permanently, and the one-time upgrade-time advisory tells the operator right then that the deposit is past the tag. No honesty regression — the forensic truth is captured, not suppressed.

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

## Bootstrapping a new project

The multi-project directory layout, `$PGAI_PROJECT_ROOT` resolution, per-project path table, and single-project shim are documented in `team/SOP.md` under "Projects Layout." This section covers the operator create-project flow and workflow-template dispatch.

New projects are added with `team/scripts/create-project.sh`. The script is **safe-by-default**: it bootstraps the full project skeleton, registers the project in `projects.cfg`, and writes a `project.cfg` whose version ceiling is `max_minor=0` / `max_major=0`. A freshly-created project is therefore **dormant** — the discovery pipeline will not pick up any work for it until the operator explicitly raises the ceiling.

### Canonical four-step flow

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

### Defaults written by `create-project.sh`

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

### Override flags

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

### Why dormant-by-default

The kanban runs autonomously by design — drop a brief, walk away, wake up to a tagged release. For an existing, well-configured project that's exactly what you want. For a **new** project it's the wrong default. Two failure modes the dormant ceiling prevents:

- **Premature shipping.** Operator creates a project intending to configure it gradually, drops a requirements file, and the next cron firing tries to ship work before paths are set.
- **Forgotten paths.** Operator creates a project, drops requirements, and the chain attempts to ship against a `dev_tree_path` that doesn't yet exist locally. The error surfaces mid-RC, often hours later.

A `0/0` ceiling makes the new project sit dormant until the operator runs `set-version-ceiling.sh --project <name> --minor N`. That command is the explicit "I am ready for autonomous shipping on this project" gate. The kanban itself uses `max_major=0` permanently as its v1.0.0 gate; new projects extending this pattern with `max_minor=0` initially is a natural extension.

### Why path fields stay manual

`dev_tree_path` and `git_repo_url` are **per-machine and per-operator** by nature. A flag-based approach forces operators to maintain machine-specific `create-project.sh` invocations that drift over time and break silently across deployment environments (laptop vs VPS vs CI). By writing these fields empty and forcing a manual `project.cfg` edit, the script makes the operator consciously confirm "this is the right path on this machine" — preventing a class of cross-machine bugs the framework has hit before.

The empty values are intentional and load-bearing. Do not "fix" them by adding `--dev-tree` flags later; the rejection is the design.

### Inspecting and changing the ceiling later

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

### Registering an existing project

`create-project.sh` aborts if `projects/<name>/` already exists. To register a project directory that's already on disk but missing from `projects.cfg`, use `team/scripts/add-project.sh` instead.

### Portable release hooks (`.pgai/hooks/`)

Release lifecycle hooks (pre-squash, pre-tag, post-tag) can live in three locations. The portable location — the one that travels with the managed repository and survives `remove-project` + `create-project` cycles — is:

```
<dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh
```

Drop an executable script at that path, commit it to the managed repo, and the release will resolve it automatically. No `project.cfg` edit is required for the in-repo tier to work. This is the recommended location for hooks that should always be present regardless of the kanban installation state (for example, a version-bump wrapper whose absence causes shipping drift).

The kanban's `create-project.sh` does not seed a `.pgai/hooks/` directory in the managed repo — the directory is opt-in per project and created only when the project actually needs a portable hook. Full behavior — the three-tier precedence order, the always-printed resolution line, the `cm_release_<phase>_hook_required` flag, and the hook environment contract — is documented in "Release Lifecycle Hooks" above.

### Workflow template dispatch

`create-project.sh` does not embed the project skeleton's templates inline. It reads them from per-workflow template directories under `templates/project/<workflow>/`. The value passed to `--workflow-type` (default `release`) selects the directory; everything copied into the new project — templates, queue files, README files — comes from that one directory.

This is what makes new workflow types additive: a new workflow is a new directory under `templates/project/`, not a code change.

#### Directory layout

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

#### Dispatch and error handling

`create-project.sh` resolves the template directory as `templates/project/${WORKFLOW}/`. If that directory does not exist, the script exits with:

```
ERROR: unknown workflow type <name>; available types: <space-separated list of subdirectory names>
```

The available types are derived from whatever directories are present under `templates/project/` at runtime. There is no allow-list in the script.

#### Document project vs release project (out of the box)

Both workflows produce the same directory skeleton (`tasks/`, `bugs/`, `priority/`, `requirements/`, `artifacts/`, `release-notes/`, `logs/`) and the same `project.cfg` fields. The differences operators see immediately:

| Aspect | `release` project | `document` project |
|---|---|---|
| Queue files seeded | `coder_backlog.md`, `pm_backlog.md`, `writer_backlog.md`, `tester_backlog.md`, `cm_backlog.md`, `bug_backlog.md`, `priority_backlog.md` | Same minus `coder_backlog.md` (no CODER pulls in a document chain) |
| `REQUIREMENTS-TEMPLATE.md` `## Workflow Type` | `release` | `document` |
| `REQUIREMENTS-TEMPLATE.md` content focus | Source branch, test required, code-shipping fields | Audience, brief, outline, deliverables, voice-and-tone notes |
| `brief-example.md` at project root | not seeded | seeded — short, concrete example of the brief format WRITER expects |
| READMEs | describe the release pipeline (PM → CODER → TESTER → CM) | describe the document pipeline (PM → WRITER outline → WRITER drafts → WRITER integrate → WRITER polish → TESTER → CM) |

The `project.cfg [project] workflow_type` key is set to the value the operator passed, so downstream tooling (PM materializer, subagent dispatch) reads the correct workflow without further configuration.

#### Adding a new workflow type

Three steps:

1. Create `templates/project/<new-workflow>/` in the dev tree.
2. Populate the file set above (all required files; `BRIEF-EXAMPLE.md` if the workflow uses one). Reuse the generic `BUG-TEMPLATE.md` and `PRIORITY-TEMPLATE.md` from an existing workflow when you have no workflow-specific changes.
3. Run `install.sh` so the live install picks up the new directory.

No edits to `create-project.sh` are needed. After installation, `create-project.sh --project foo --workflow-type <new-workflow>` works immediately, and `--workflow-type bogus` continues to error with the updated list of available types.

`install.sh` copies the whole `team/templates/project/` tree into `$KANBAN_ROOT/templates/project/`, so templates added in the dev tree become available to operators on the next install.

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
