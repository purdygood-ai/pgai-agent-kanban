# pgai-agent-kanban — Operator How-To

The operator manual: setup, project management, workflows, agents, intake
documents, the dashboard, and stopping the system safely. The [README](README.md)
explains what the framework is; this document explains how to run it. For the
operator command surface in one place — `reset`, `show`, `close`, `delete`,
`wontdo`, the `halt` family, and `unwind-rc` — see
[docs/operator-commands.md](docs/operator-commands.md).

Paths in this document are live-install paths (`$KANBAN_ROOT/scripts/...`).
The framework runs standalone: no project is special, and a missing dev tree
degrades only the project that owns it.

---

> **New here?** This document is the reference. For a guided first run
> that ends in a real release, walk `demos/chomp-man-demo/README.md`
> (release workflow) or `demos/three-bears-demo/README.md` (document
> workflow) — everything below is easier to absorb after seeing one run.

## 1. Setup

### 1.1 Install

<!-- doc-lint: skip — install narrative requires a fresh checkout and no prior install; cannot run verbatim in harness -->
```bash
git clone <repo-url> ~/develop/pgai-agent-kanban
cd ~/develop/pgai-agent-kanban
./install.sh --add-claude-agents --wake-tier=large
```

Key flags:

| Flag | Effect |
|---|---|
| `--add-claude-agents` / `--add-codex-agents` | Deploy provider agent wrappers to `~/.claude/agents/` / `~/.codex/agents/`. Strict opt-in: without a flag, nothing is written outside the kanban tree. |
| `--wake-tier=small\|medium\|large` | Which crontab template to install (wake frequency/stagger density). |
| `--no-system-cron` | Skip crontab installation (e.g. for a secondary/test install, or when pseudocron is your scheduler). |

> No cron on your host (containers, restricted shells)? See
> [docs/pseudocron.md](docs/pseudocron.md) — same jobs, no crontab.
| `--upgrade` | Upgrade an existing install in place (preserves kanban.cfg, projects, bugs; creates a backup tarball). Normally invoked via `scripts/upgrade.sh`, which also checks out the latest tag. |

The install target defaults to `~/pgai_agent_kanban`; override with the
`PGAI_AGENT_KANBAN_ROOT_PATH` environment variable (this also enables
parallel installs — point it elsewhere and pass `--no-system-cron`).

A fresh install registers no projects. Add your shell export and create your
first project (section 2).

```bash
export PGAI_AGENT_KANBAN_ROOT_PATH="$HOME/pgai_agent_kanban"   # in .bashrc
```

### 1.2 kanban.cfg — framework settings

INI format, one file at `$KANBAN_ROOT/kanban.cfg`. Initialized from
`kanban.cfg_example` on install; upgrades never overwrite it. The example
file documents every key; the ones you will actually touch:

```ini
[paths]
# Where ALL framework transient output goes. Set this once; everything —
# worktrees, sandboxes, TMPDIR for standard tooling, the wake stop file —
# derives from it. Keeping it outside /tmp keeps /tmp clean.
tmp_root = /home/rocky/tmp
tmp_subdir = pgai_kanban_tmp

# Optional. Install-time convenience / fallback. Per-project dev trees live
# in each project.cfg; this may be empty on installs that do not manage the
# framework's own source.
dev_tree_path =

[providers]
active = claude            # which LLM lane executes work
available = claude codex   # which provider wrappers are deployed
ai_auth_mode = oauth       # oauth (Claude Code credentials) or apikey

[dashboard]
max_rows = 20              # global fallback: items shown per queue column
```

Configuration precedence everywhere: **environment variable > config file >
built-in default**, validated by one loader that fails loudly on missing
required keys.

### 1.3 shell-env — wake environment

<!-- doc-lint: skip — requires a live $KANBAN_ROOT install and an interactive editor; cannot run verbatim in harness -->
```bash
cp $KANBAN_ROOT/shell-env_example $KANBAN_ROOT/shell-env
$EDITOR $KANBAN_ROOT/shell-env
```

Sourced by every wake before any work: PATH additions, virtualenv
activation, `KANBAN_ROOT`. If a tool works in your interactive shell but an
agent can't find it, the fix belongs here.

### 1.4 secrets — credentials

<!-- doc-lint: skip — requires a live $KANBAN_ROOT install and an interactive editor; cannot run verbatim in harness -->
```bash
cp $KANBAN_ROOT/secrets_example $KANBAN_ROOT/secrets
chmod 600 $KANBAN_ROOT/secrets
$EDITOR $KANBAN_ROOT/secrets
```

Branches on `AI_AUTH_MODE` (exported from kanban.cfg before sourcing):
`oauth` mode unsets `ANTHROPIC_API_KEY` so Claude Code OAuth credentials
win; `apikey` mode exports the key. Never commit this file; 600 perms.

### 1.5 projects.cfg — the project registry

INI registry of which projects this install manages, in iteration order:

```ini
[project:my-app]
priority = 1               # iteration order (1 = first each wake)
dashboard_color = #D85A30  # per-project color on all dashboard surfaces
dashboard_max_rows = 8     # optional per-project queue-column row cap
```

Managed by the project scripts (section 2) — hand-editing is possible but
the scripts keep state consistent.

### 1.6 Runtime bounds

The wake layer caps how long work runs at three nested levels —
**task ≤ project ≤ batch** — so a single stuck agent cannot consume an
entire wake batch or starve the projects queued behind it. All three are
`[wake]` keys in `kanban.cfg`, each with a safe default:

| Key | Default | Bounds |
|---|---|---|
| `max_task_seconds` | `5400` (90 min) | A single agent run. `0` disables. |
| `max_project_seconds` | `14400` | One project's task loop; each project gets its own clock. `0` disables. |
| `max_runtime_seconds` | `14400` | The whole multi-project batch (the outermost bound). |

`max_task_seconds` is a hard wall-clock timeout on the provider invocation.
When an agent exceeds it the wake sends SIGTERM, waits `kill_grace_seconds`
(default 30), then SIGKILL. `max_project_seconds` resets per project, so a
long or stalled project 1 cannot starve projects 2..N — each gets a fresh
clock. `max_runtime_seconds` caps the entire iteration. The wake log names
which bound tripped — `(task)`, `(project)`, or `(batch)` — and warns
(non-fatally) at startup if the configured values violate the
task ≤ project ≤ batch ordering. Each key honors environment-variable
override (`MAX_TASK_SECONDS=<N> wake-batch.sh ...`, and the parallel forms).

**BLOCKED-by-timeout vs. a crash.** When `max_task_seconds` fires, the
existing agent-exit path marks the task **BLOCKED** with the reason
`exceeded max_task_seconds (<N>s)`, tears down the worktree, and releases the
lock — the same teardown an ordinary exit takes. The reason text is what
distinguishes a timed-out task from one that crashed: a crash carries its own
failure reason, while a timeout always reads `exceeded max_task_seconds`. So
a BLOCKED task whose reason names the timeout *ran too long* (its work may
simply have been too large for one session, or it stalled); a BLOCKED task
with any other reason *died*. Either way you clear it the same way —
`scripts/reset.sh --project <name> --key <key>` (section 9.1)
re-queues it. The dashboard attention surface (window 2) also flags a WORKING
task whose age already exceeds `max_task_seconds` *before* the kill lands, so
an over-run is visible early.

---

## 2. Project Management

Each project lives at `$KANBAN_ROOT/projects/<name>/` with its own config,
queues, intake directories, and release state. Projects are fully
independent: own cadence, own workflow type, own halt flags.

### 2.1 project.cfg — per-project settings

```ini
[project]
project_name = my-app
workflow_type = release          # release | document
dev_tree_path = /home/rocky/develop/my-app
git_repo_url = git@github.com:me/my-app.git
git_remote_name = origin
branch_prefix =                  # empty: main/rc/v* ; "ai_": isolated lane
push_to_remote = true            # false: local-only releases, operator pushes

[versioning]
max_patch = 21                   # ceilings: caps on the version COMPONENT of
max_minor = 13                   # an eligible target version (NOT increment
max_major = 0                    # counters). A doc whose target exceeds a
                                 # ceiling is not picked up automatically —
                                 # raise the ceiling to approve. max_major=0
                                 # keeps major bumps operator-only.
```

**Ceiling semantics matter:** these compare version *components*. A mature
project at v3.59.x with `max_minor = 13` is gated on everything — when
registering an existing project, set ceilings above where the project
currently lives.

`branch_prefix` decides the git lane: empty uses `main`/`rc/vX.Y.Z`
directly; a prefix like `ai_` gives the kanban an isolated lane
(`ai_main`, `ai_rc/ai_vX.Y.Z`, prefixed tags) so a repo's
human branches stay untouched. The kanban ships single-lane:
each RC branches from the (prefixed) main branch and returns to
it in one squash — there is no develop hop.

### 2.2 create-project — new project from scratch

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/create-project.sh --project my-app \
  --workflow-type release \
  --dev-tree /home/rocky/develop/my-app \
  --git-repo git@github.com:me/my-app.git
```

Creates the project directory skeleton, project.cfg, empty queues, and the
projects.cfg entry. Defaults: release workflow, empty branch_prefix,
ceilings 21/13/0 (a fresh project demonstrates the chain immediately;
majors stay gated). `--max-patch/--max-minor/--max-major` override.

For a release project the git repo needs the base `main` branch (or its
prefixed equivalent) on origin — `scripts/init-project-git-repo.sh`
sets up the base branch; CM never creates base branches itself. The
single-lane flow branches every RC from that base and squashes back
into it, so only one base branch is required.

To have the kanban manage its own source, register it like anything else —
nothing in the framework treats it specially:

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/create-project.sh --project pgai-agent-kanban \
  --dev-tree ~/develop/pgai-agent-kanban --git-repo <repo-url>
```

### 2.3 add-project — register an existing project directory

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/add-project.sh --project my-app
```

Registers a `projects/<name>/` directory that already exists (e.g. restored
from backup) into projects.cfg without recreating its contents.

### 2.4 remove-project

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/remove-project.sh --project my-app             # unregister only (safe default)
scripts/remove-project.sh --project my-app --force     # also rm -rf the directory
```

The safe default unregisters the project from `projects.cfg` and leaves the
`projects/<name>/` directory in place. Add `--force` to also delete the
project directory. The project's git repo and dev tree are never touched
either way. Export first if you may want it back.

### 2.5 export-project / import-project

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/export-project.sh --project my-app                       # → tarball in cwd
scripts/import-project.sh --archive my-app-export-<ts>.tar.gz \
                          --register                              # → restores + registers
```

The export writes `<name>-export-<UTCstamp>.tar.gz` in the current
directory by default (override with `--out <path>`) and carries
everything: project.cfg (ceilings included), queues, intake files,
release-state, logs. Import is the sanctioned restore path — an imported
project resumes exactly where it left off; `--register` adds it to
`projects.cfg` in the same step (without the flag, import extracts and
prints the registration line for you to add manually). This pair is
also the migration path between installs.

### 2.6 release-state.md

Per-project, CM-written. Tracks `Active RC` (the single-threading gate) and
`Last Released`. For git-backed projects, Last Released is *derived from
git tags* in the dev tree — the file is a mirror. For document-workflow
projects with no repo, the file is authoritative; on a fresh registration
of a project with prior document releases, hand-set `## Last Released` once.

---

## 3. Workflow Types

A workflow type defines the agent pipeline for a deliverable. Set per
project in project.cfg.

### 3.1 release — software with the git RC lifecycle

Requirements doc → PM decomposes → CM opens `rc/vX.Y.Z` from the
(prefixed) main branch → CODER (and WRITER for release notes) work
per-task worktrees and merge back → TESTER verifies against the
requirements and files findings → CM applies policy, runs project hooks,
squashes the RC directly into the (prefixed) main branch (one squash, no
develop hop), runs the post-squash fidelity gate, tags, pushes, deletes
the RC. Output: a tag on origin and release notes.

Per-project hooks customize finalization — executable scripts the framework
discovers and runs. Three resolution tiers, highest precedence first:

```
(a) project.cfg [hooks] cm_release_<phase>_hook            # explicit path
(b) $KANBAN_ROOT/projects/<name>/hooks/cm-release-<phase>.sh  # kanban-side
(c) <dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh      # in-repo, portable
```

Tier (c) — the in-repo `.pgai/hooks/` location — is the **portable** tier:
it travels with the managed repo, survives `remove-project` +
`create-project` cycles, and is versioned and reviewed alongside application
code. Prefer it for hooks that should always be present regardless of the
kanban installation state (e.g., a version-bump wrapper).

```
<dev_tree_path>/.pgai/hooks/
├── cm-release-pre-squash.sh   # e.g. bump pyproject.toml to the target tag
├── cm-release-pre-tag.sh
└── cm-release-post-tag.sh     # best-effort; failure logged, not blocking
```

Every release prints one resolution line per phase (`<phase> hook: <path>
(source: cfg|kanban-side|in-repo)` or `<phase> hook: none configured`).
Setting `cm_release_<phase>_hook_required = true` in `project.cfg [hooks]`
makes a missing hook at any tier a HALT before the phase — fail-loud on
wiring loss. Full precedence, printing, required-flag semantics, and the
hook environment contract (`PGAI_TARGET_VERSION`, `PGAI_PROJECT_NAME`,
`PGAI_PROJECT_ROOT`, `PGAI_DEV_TREE_PATH`, `PGAI_RC_BRANCH`,
`PGAI_KANBAN_ROOT`, cwd = dev tree) are in `docs/OPERATIONS.md` under
"Release Lifecycle Hooks."

### 3.2 document — prose deliverables

No git repo required (`dev_tree_path` may be empty). WRITER drafts/
integrates/polishes; optional TESTER review verifies against the outline;
CM finalize publishes the deliverable to
`projects/<name>/artifacts/v<semver>-<name>.<ext>` — a versioned library
where every version is kept. Versioning drives off the requirement doc's
`## Target Version`.

---

## 4. The Agents

Six roles plus one dormant observer. Each is a separate subagent with a
role file under `$KANBAN_ROOT/roles/`. One agent of each type runs at a
time per project (per-project wake locks); cron wakes each agent type on a
stagger every minute (large tier).

| Agent | Trigger | Does | Git contract |
|---|---|---|---|
| **PM** | wake | Decomposes requirements docs into tasks across the agent queues; runs the discovery pipeline when idle | read-only |
| **CODER** | wake | Implements features in a per-task git worktree off the RC branch; merges back `--no-ff` | local-only (never push/fetch) |
| **WRITER** | wake | Prose: release notes, document-workflow deliverables | local-only |
| **TESTER** | wake | Verifies the RC against requirements in a detached-HEAD worktree; files categorized findings to `bugs/` and a structured report; halts only when verification cannot run | read-only |
| **CM** | wake | Branch/merge/tag mechanics; applies ship/no-ship policy (default: ship); the only agent that touches origin | sole origin-toucher |
| **PO** | human-invoked (`scripts/po-agent.sh`) | Drafts requirements docs from briefs in collaboration with the operator. Optional today — most operators write requirements directly (section 5) | read-only |

The two policies that make autonomy work: **TESTER reports, CM decides**
(found bugs become intake for the next iteration, never a veto), and there
is **no REVIEW state** — a task ends DONE, BLOCKED (with a reason, needs
human), or WONT-DO.

---

## 5. Intake Documents

All work enters as files dropped into per-project intake directories. The
discovery pipeline (run by PM wakes when a project is idle) processes them
in strict order: **bugs first, then priorities, then requirements** — and
does exactly one thing per iteration. An Active RC or a HALT blocks intake
processing entirely (one RC at a time).

All intake files: `chmod 644`, and discovery only handles files whose body
contains `## Status: open` (bugs/priorities) or an unprocessed
requirements doc with a target version above Last Released.

**Dropping a file: one command.** `intake.sh` deposits a staged file into the
right directory, routed by its name, with mode 644 set for you. It replaces the
manual `cp` + `chmod`:

```bash
# Instead of:
#   cp /tmp/BUG-0400-widget-crash.md projects/<name>/bugs/ && chmod 644 projects/<name>/bugs/BUG-0400-widget-crash.md
scripts/intake.sh --project <name> /tmp/BUG-0400-widget-crash.md
```

It routes by filename prefix — `BUG-*` → `bugs/`, `PRIORITY-*` → `priority/`,
`vX.Y.Z-*.md` → `requirements/` — copies the file (the source is left in
place), and sets 644. It refuses, copying nothing, if the name matches no
known prefix or if a file of that name already exists in the destination.

`intake.sh` is deliberately dumb: it routes, copies, and chmods only — it does
**NOT** validate the file's contents. A malformed file still lands here, then
the discovery pipeline rejects it to `.rejected/` exactly as before. See
[docs/operator-commands.md](docs/operator-commands.md) for the full command
reference.

### 5.1 Bugs

```
Location:  projects/<name>/bugs/
Filename:  BUG-NNNN-short-slug.md        (regex: ^BUG-[0-9]{4,}-.+\.md$)
```

Body: `## Status: open`, a summary, severity, the required fix, and —
critically — **acceptance criteria as testable commands** (`grep -q`,
`test -f`, geometry assertions). The intake file is where the human
encodes the design decision: file allowlists, "MUST NOT modify" guards,
and acceptance tripwires are how you steer the chain without sitting in
the loop. Before numbering a new bug, confirm the high-water mark:

```bash
ls projects/<name>/bugs/ | grep -oE 'BUG-[0-9]+' | sort -V | tail -1
```

Discovery bundles ALL open bugs into one requirements doc at the next
patch version. TESTER also files bugs autonomously from its findings.

### 5.2 Priorities

```
Location:  projects/<name>/priority/
Filename:  PRIORITY-NNNN-short-slug.md  (date segment optional: PRIORITY-NNNN-YYYYMMDD-slug.md also accepted)
           (regex: ^PRIORITY-[0-9]{4,}-.+\.md$ — date is optional, not required)
```

Operator-initiated improvements that aren't defects. Same body shape as
bugs. Bundled (after bugs are clear) into a patch-version requirements doc.

### 5.3 Requirements

```
Location:  projects/<name>/requirements/
Filename:  vX.Y.Z-short-name.md          (regex: ^v[0-9]+\.[0-9]+\.[0-9]+-.+\.md$)
```

The only intake that carries a version — and the only path to a **minor or
major** bump (bug/priority bundles always produce patch bumps). A
hand-authored requirements doc contains:

```markdown
# vX.Y.0 — Title

## Status: ready
## Target Version
vX.Y.0
## Workflow Type
release
## Source Branch
develop
## Test Required
yes
## Human Approval Required
auto

## Overview / ## Goals / ## Deliverables (D1, D2, ...)
## Constraints
## Acceptance Criteria   (testable commands)
## Suggested Decomposition   (so PM doesn't over-fragment)
## Notes for TESTER
## Context Paths
```

Lower version = higher priority by natural sort; discovery picks the
lowest eligible version first, so dropping v0.61.0 and v0.62.0 together
sequences them automatically.

### 5.4 The queue files (handled markers)

`projects/<name>/tasks/queues/{bug,priority}_backlog.md` record which
intake items discovery has already handled. Do not pre-add new items to
these files — a listed item is considered handled and will never bundle.

---

## 6. The Dashboard

A tmux session rendering every project's state. Lifecycle:

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd and a live tmux-capable environment; cannot run verbatim in harness -->
```bash
scripts/dashboard/create.sh     # build + attach the session
scripts/dashboard/detach.sh     # leave it running, return to your shell
scripts/dashboard/attach.sh     # re-attach from anywhere
scripts/dashboard/kill.sh       # tear the session down (e.g. before upgrades)
```

(Standard tmux detach — `Ctrl-b d` — also works.)

Windows:

| # | Window | Use |
|---|---|---|
| 0 | main | The operator surface: header (framework version, clock, global HALT), live agent logs, per-project progress, next-wake schedule — and the right column with per-project status (active provider, per-agent task counts, currently-working task, RC / Last Released, HALT scope). The right column is a fixed 25% of the window width (not operator-tunable). |
| 1 | visibility | The kanban board: BUGS / PRIORITIES / REQUIREMENTS / PM / CODER / WRITER / TESTER / CM columns for every project, colored per projects.cfg. Item colors: white = pending, amber = active, green = done. |
| 2 | attention | Items needing the operator: BLOCKED tasks with reasons |
| 3 | git | Per-project repo status and recent tags — for every registered project's repo |
| 4 | metadata | Project/config introspection |
| 5 | metrics | Cost rollups: current-day and current-RC token spend, per agent and model |
| 6 | logs | Wake logs, all agents interleaved |
| 7 | debug-logs | Per-project debug-gate output |
| 8 | training-logs | Reasoning-trace corpus activity |
| 9 | terminal | A plain shell inside the session |
| 10+ | drill-N | One drill-down window per project (all workflow types). Release/feature projects get a 5-pane release layout scoped to that project (header / queues / progress / cron / logs). Document-workflow projects get a 2-pane document layout: artifact version library + document-pipeline progress on top, shared logs on the bottom. |

The dashboard is read-only — it renders state from disk and never mutates
anything. Kill it freely; recreate it freely.

---

## 7. Stopping Things: HALT

Two scopes, both backed by simple sentinel files honored by every pipeline
check. Each scope has a command so you no longer touch the files by hand:

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/halt-global.sh                       # GLOBAL: all projects stop (no args)
scripts/unhalt-global.sh                     # GLOBAL: all projects resume (no args)
scripts/halt.sh   --project <name>           # PROJECT: only this project stops
scripts/unhalt.sh --project <name>           # PROJECT: only this project resumes
```

HALT blocks discovery and task dispatch at iteration start. In-flight
agent processes finish their current task (a HALT is not a kill). Wakes
remain cheap while halted (they check and exit). Use the global HALT before
upgrades; use a project HALT to quarantine one project (e.g. while its
tasks are blocked awaiting a fix) while the others keep working.

**`halt-global.sh` / `unhalt-global.sh`** are bare, no-argument commands. The
global halt takes no project or key — it has nothing to scope to — and is fully
reversible, so there is no prompt. Both are idempotent: halting an
already-halted system, or unhalting one that is not halted, exits cleanly with a
message rather than an error. They write and remove `$KANBAN_ROOT/HALT`,
replacing the old `touch $KANBAN_ROOT/HALT` / `rm $KANBAN_ROOT/HALT` workflow. If
you prefer, the underlying file mechanism still works
(`touch $KANBAN_ROOT/HALT`), but the commands are the supported path.

**`halt.sh` / `unhalt.sh` are unchanged** and remain per-project: both require
`--project` and act only on `projects/<name>/HALT`. There is no `--global` mode
on `halt.sh`; the global pair above is its own command for that reason.

> **There is deliberately no global halt-after.** `halt-after` (Section 8) is
> bound to a specific project's release candidate — drain *this* project's RC,
> then halt — so it has no global meaning. There is intentionally no
> `halt-after-global.sh`; the absence enforces that constraint by design.

---

## 8. Stopping Things Later: HALT-AFTER

"Stop, but only after X finishes" — without watching for the moment:

<!-- doc-lint: skip — illustrative examples using .../HALT-AFTER shorthand for a live project path; requires an existing $KANBAN_ROOT and project directory -->
```bash
echo rc        > $KANBAN_ROOT/projects/<name>/HALT-AFTER   # after current RC ships
echo rc:v0.62.0 > .../HALT-AFTER                            # after that version ships
echo coder     > .../HALT-AFTER                             # after CODER work drains
touch            .../HALT-AFTER                             # empty = rc
```

Valid tokens: `rc`, `rc:vX.Y.Z`, `pm`, `coder`, `writer`, `tester`, `cm`.
Scope: the file works at `projects/<name>/HALT-AFTER` (one project) or
`$KANBAN_ROOT/HALT-AFTER` (all projects). Invalid tokens are warned about
and ignored — the system never silently halts on a typo.

Semantics:

- **`rc`** — captures the in-flight RC version at arm time (rewriting
  itself to `rc:vX.Y.Z`), then drains until that version (or higher) shows
  as Last Released.
- **`rc:vX.Y.Z`** — drains until Last Released ≥ that version.
- **Agent tokens** — drains until no task of that role is WORKING, BACKLOG,
  or WAITING. Downstream tokens also wait for PM to finish (PM may still be
  materializing work for that role). Conservative by design: it never
  halts early while queued work exists.

When the drain condition is met, the file atomically promotes itself:
HALT-AFTER is removed, HALT is created. You then `rm HALT` whenever you're
ready to resume.

---

## 9. Resetting Things: the reset scripts

Reset scripts exist so you don't open files and flip checkboxes, statuses,
and blockers by hand. They are operator power tools. The design philosophy
is fixed and encoded verbatim in the shared library header
(`scripts/lib/reset.sh`) and in every wrapper's `--help`. Four points:

- **Assume intent.** The scripts exist so the operator does not have to
  open many files and flip checkboxes, statuses, and blockers by hand.
- **Refuse only filesystem races.** A task an agent currently holds
  (WORKING state) and an ambiguous key (zero matches or multiple matches
  in the project tree) are the only two refusals. Everything else that
  might be unwise gets a one-line warning and proceeds.
- **No confirmation prompts.** The command is the confirmation.
- **No cascades.** Each reset touches its own artifact only. Resetting a
  task does not reset sibling tasks, dependent tasks, or any other kanban
  state. Composition is the operator running multiple commands
  deliberately.

A single dispatcher, `reset.sh`, handles every reset path. It selects mode
entirely from the `--key` prefix: a task key (`ROLE-YYYYMMDD-NNN`) routes an
agent-task reset with the role taken from the prefix, while an intake key
infers the type (`BUG-*` → bug, `PRIORITY-*` → priority, a version or other
string → requirement). `--project` and `--key` are both required in every
mode, because keys are unique only within a project (two projects can both
have a task `CODER-20260607-001`), so there is no environment-variable
fallback:

<!-- doc-lint: skip — bare reset.sh requires $KANBAN_ROOT/scripts/ on PATH; shown without full path for readability; use $KANBAN_ROOT/scripts/reset.sh in practice -->
```bash
reset.sh --project <project-name> --key <ROLE-YYYYMMDD-NNN> [--keep-artifacts] [--force] [--help]
reset.sh --project <project-name> --key <BUG-NNNN|PRIORITY-NNNN|version> [--help]
```

`--keep-artifacts` applies to agent resets only (it opts out of clearing
`artifacts/`); intake resets do not take it. `--force` applies to agent
resets only: it clears a stale worktree (left by a prior run) before
resetting — use it as the standard corpse-clearing step when bare reset
refuses with a stale-worktree warning. `--help` / `-h` prints usage and
exits 0. A missing required flag, an unknown flag (including the removed
`--bug`/`--priority`/`--requirement` selectors), or a missing key prints
usage and exits 1.

The full reset surface is also documented in `docs/operator-commands.md`,
the authoritative operator reference.

### 9.1 Agent task resets

The unified dispatcher `reset.sh` handles all five agent reset paths; it
reads the role from the task key prefix. All calls share the same flags:

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | yes | Project the task lives in. |
| `--key <key>` | yes | Full task key, e.g. `CODER-20260611-001` for `CODER-20260611-001-some-slug`; the role is in the prefix. |
| `--keep-artifacts` | no | Preserve `artifacts/` contents (default: clear). |
| `--force` | no | Clear a stale worktree (registration and/or on-disk path left by a prior run) before resetting. Without `--force`, a stale worktree causes a warning with the manual removal recipe and a non-zero exit. Use `--force` as the standard corpse-clearing step — it runs git worktree remove, prune, and rm -rf automatically before completing the reset. |
| `--help`, `-h` | no | Print usage and exit 0. |

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; task keys used here are illustrative examples, not real tasks -->
```bash
scripts/reset.sh --project my-app --key PM-20260611-001
scripts/reset.sh --project my-app --key CM-20260611-008
scripts/reset.sh --project my-app --key CODER-20260611-002
scripts/reset.sh --project my-app --key WRITER-20260611-005
scripts/reset.sh --project my-app --key TESTER-20260611-006
```

Returns the task to freshly-materialized state — **total amnesia**: the
next wake picks it up as if it had never run. Side effects: status.md
regenerated from the materializer template (BACKLOG, no blockers, no
needs-human, no prior summary), `artifacts/` and task `logs/` cleared
(`--keep-artifacts` opts out), queue marker flipped to `[ ]`, the prior
attempt's `feature/<task-id>` branch deleted from the project repo and
stale worktree registrations pruned. The task's README.md (the work
definition) and its queue position are untouched; the training-corpus
copy is never touched (operator record, not agent-visible).

A TESTER task key additionally tears down a BLOCKED-retained TESTER
worktree via the standard teardown path.

Refusal mode: a WORKING task refuses with exit code 2 (an agent holds it —
wait or investigate). A stale worktree (registration or on-disk path left
by a prior run) also refuses with exit code 2, printing the three-command
manual removal recipe to stderr. Re-run with `--force` to clear the
corpse automatically in one call. An ambiguous key (zero or multiple
matches) refuses with exit code 1, listing the directory searched or the
candidate matches. Warning-and-proceed: a feature branch already merged
into the active RC warns loudly (re-running produces a second merge
commit) and proceeds. If `--force` cannot remove the stale path because
it is an active mount target, it aborts with exit code 4 (the mount is
named in stderr; no partial cleanup occurs).

Resetting a BLOCKED task is also how you un-gate a project: while any task
is BLOCKED with Needs Human, the project dispatches no new tasks.

### 9.2 Intake resets

The unified dispatcher `reset.sh` handles all three intake reset paths.
From an intake `--key`, it infers the item type from the prefix
(`BUG-*` → bug, `PRIORITY-*` → priority, a version or other string →
requirement). All take `--project` and `--key` only (no
`--keep-artifacts`); `--help` / `-h` exits 0:

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; keys are illustrative examples -->
```bash
scripts/reset.sh --project my-app --key BUG-0123
scripts/reset.sh --project my-app --key PRIORITY-0123
scripts/reset.sh --project my-app --key v0.1.2
```

A bug or priority key performs exactly two writes: the item's
`## Status` back to `open`, and its backlog-cache marker back to `[ ]` in
`bug_backlog.md` / `priority_backlog.md`. Nothing else in the project tree
is modified — discovery will bundle the item again on the next idle tick.

A requirement key (a version or other string) performs three writes:
`## Status` back to `ready`,
the PM task's marker flipped to `[ ]` in `pm_backlog.md`, **and the PM
materializer's idempotence hash-marker cleared.** The third step is the
headline behavior — the PM materializer keys idempotence on a sentinel
file under the PM task's `artifacts/`, not on the requirements file's
status, so without clearing the marker PM skips a "reset" requirement
silently on re-pickup. Clearing it is what makes the reset-and-rerun
workflow work end-to-end on requirements.

Key resolution is by glob within the project tree; the requirements
wrapper also accepts a bare `<key>.md` filename. Zero matches errors out
with the directory searched; multiple matches refuses and lists the
candidates.

**No cascades.** Resetting a requirement does not reset the bugs or
priorities it bundled, nor any prior round's task folders; resetting a bug
does not touch its bundle. If you want three things reset, run three
commands. Resetting an item whose bundle's RC is currently in flight warns
(possible double-handling) and proceeds.

### 9.3 RC operations (the RC trio)

The RC trio brings release-candidate control under the same uniform
`--project <name> --key vX.Y.Z` signature:

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; also operates on live release state that does not exist in harness -->
```bash
scripts/cm/cancel-rc.sh                  --project my-app --key v0.7.17         # abandon an active RC
scripts/ship-rc.sh                       --project my-app --key v0.7.17         # manual ship escape hatch
scripts/reset.sh                         --project my-app --key <cm-task-key>   # re-run the CM release step
```

`cm/cancel-rc.sh` cleanly unwinds an active release candidate (deletes the
local and origin RC branch, resets `release-state.md`); a `--yes` flag skips
the confirmation prompt for non-interactive use.
`ship-rc.sh` squash-merges a verified RC into develop and then main as a
manual ship escape hatch.

The third member of the trio is `reset.sh` on a CM task key: to re-run just
the CM release step on an already-verified RC, reset the CM release task with
`reset.sh --project <name> --key <cm-task-key>`. That returns the CM task to
BACKLOG so the next CM wake re-runs the release step.

---

## 10. Common Operations

**Upgrade the live install** (drains nothing by itself — HALT first):

<!-- doc-lint: skip — upgrade narrative requires a live $KANBAN_ROOT install, a tracked git clone, and scripts/ on the relative path from cwd; cannot run verbatim in harness -->
```bash
scripts/halt-global.sh           # or HALT-AFTER and wait
scripts/dashboard/kill.sh
cd ~/develop/pgai-agent-kanban && git pull
$KANBAN_ROOT/scripts/upgrade.sh  # backs up, checks out latest tag, installs
scripts/unhalt-global.sh
scripts/dashboard/create.sh
```

**Two-phase upgrade model (v1.7.0+).** The command above is unchanged
from the operator's perspective. Under the hood, from v1.7.0 onward
`upgrade.sh` runs in **two phases** — the installed script and the
dev-tree script split the work so the piece that actually performs
the upgrade always runs at *current* knowledge of the version being
installed.

- **Phase 1 — bootstrap** (the *installed* `scripts/upgrade.sh` when
  invoked without `--phase2`). Parses operator args; resolves and
  validates `<dev_tree>`; creates the pre-upgrade backup tarball;
  runs `bash -n` on `<dev_tree>/team/scripts/upgrade.sh`; probes it
  for `--phase2` support; then `exec`s into it as phase 2. **Phase 1
  performs no deposit, no config changes, and no crontab work.** It
  is a small, stable bootstrap that changes rarely.
- **Phase 2 — the upgrade proper** (the *dev-tree*
  `team/scripts/upgrade.sh` invoked with `--phase2 --phase2-protocol
  1 --kanban-root <root> --backup <tarball>` plus the original
  operator args). Does everything upgrade.sh does today — deposit,
  state preservation, crontab tier handling, VERSION stamping,
  divergence advisory — but it runs from the source tree, with
  BASH_SOURCE-relative library sourcing, so the code executing the
  upgrade knows about the version being installed. The deposit
  writes into the kanban root; it never touches the executing file.

The handoff protocol is versioned (`--phase2-protocol 1`) and
fail-loud: a broken new script (syntax error), a dev-tree script
that lacks `--phase2` support (e.g. a downgrade), or a phase-2 side
that rejects the protocol number all cause phase 1 to exit non-zero
**before anything is deposited**. Your pre-upgrade backup is
untouched in every failure path.

**One-final-lag transition note (v1.6.x → v1.7.0).** The *first*
upgrade after v1.7.0 ships still runs the old monolithic
`upgrade.sh` end-to-end, because your currently installed script
does not know about phases. That final monolithic run is what
**deposits** the new two-phase `upgrade.sh` into `scripts/`. Every
upgrade after that uses the phase-1 → phase-2 handoff described
above. There is one final generation of lag on the v1.6.x → v1.7.0
step; from v1.7.0 onward, the class of "the installed script cannot
correctly upgrade to newer code" is structurally extinct.

**Retirement step (graveyard).** After the deposit, `upgrade.sh` runs a
**retirement** step that moves obsolete managed files out of the live tree
without deleting them. The only files touched are the exact relative paths
listed in `$KANBAN_ROOT/templates/retired-files.txt` (the retirement
manifest). For each manifest entry that exists at the live root, the file
is moved — never `rm`'d — to
`$KANBAN_ROOT/retired/<UTC-ts>/<original-relative-path>`, and one loud
`[upgrade] Retired: ...` line prints per file; the summary block ends with
a `Retired: N file(s) -> retired/<ts>/` line (or `Retired: none`). To
restore a retired file, move the graveyard copy back to its original
relative path — e.g. a retired
`$KANBAN_ROOT/retired/20260707T143000Z/workflows/release.yaml` is restored
with `mv "$KANBAN_ROOT/retired/20260707T143000Z/workflows/release.yaml"
"$KANBAN_ROOT/workflows/release.yaml"`. **Operator-authored content is
untouchable by construction**: the manifest is a strict allow-list of exact
relative paths, so any file whose path is not named in
`retired-files.txt` — custom workflow plugin dirs, wrappers, local
edits — is byte-preserved by construction and never enters the graveyard.
Manifest entries whose paths are absent from the live root are silent
no-ops (fresh installs, already-cleaned installs). The graveyard plus the
pre-upgrade backup tarball give you two independent recovery layers.

**Phase 1 fail-loud: "Manual bootstrap: cp … scripts/ then retry".**
From v1.7.0 onward, the phase-1 bootstrap refuses to hand off when
the dev-tree `upgrade.sh` fails one of its probes. You will see one
of these error lines on stderr and a non-zero exit, and **no deposit
will have happened** (your pre-upgrade backup is intact):

```
[upgrade] Dev-tree upgrade.sh not found: <dev_tree>/team/scripts/upgrade.sh
[upgrade] Manual bootstrap: cp <dev_tree>/team/scripts/upgrade.sh scripts/ then retry
```

```
[upgrade] Dev-tree upgrade.sh failed bash -n syntax check:
<syntax error output>
[upgrade] Nothing has been deposited.  Your pre-upgrade backup is still intact.
[upgrade] Manual bootstrap: cp <dev_tree>/team/scripts/upgrade.sh scripts/ then retry
```

```
[upgrade] Dev-tree upgrade.sh does not appear to support --phase2 (this may be a pre-v1.7 script or a downgrade).
[upgrade] Nothing has been deposited.  Your pre-upgrade backup is still intact.
[upgrade] Manual bootstrap: cp <dev_tree>/team/scripts/upgrade.sh scripts/ then retry
```

Recovery is the same in every case: copy the dev-tree `upgrade.sh`
into the installed `scripts/` directory by hand, then re-run the
standard upgrade above. `<dev_tree>` in the error line is your local
clone — the same one you `git pull`ed at the top of the upgrade
procedure — and the message prints the absolute path in place of
`<dev_tree>` so you can copy-paste it verbatim.

<!-- doc-lint: skip — recovery procedure requires a live $KANBAN_ROOT install and a dev-tree checkout; <dev_tree> is a placeholder for the absolute path printed in the error line -->
```bash
cd $KANBAN_ROOT
cp <dev_tree>/team/scripts/upgrade.sh scripts/   # exact path from the error line
$KANBAN_ROOT/scripts/upgrade.sh                  # retry the standard upgrade
```

**One-time cross-v1.2.3–v1.2.5 hand-copy** (only if your *installed*
version is v1.2.3, v1.2.4, or v1.2.5). Installs on those three
versions carry an `upgrade.sh` that deadlocks on the next upgrade
and cannot be resolved without the manual bootstrap above. The
symptom is the same "Manual bootstrap" message described in the
previous entry; the fix is the same one-liner:

<!-- doc-lint: skip — one-time recovery procedure requiring a live $KANBAN_ROOT install at v1.2.3–v1.2.5 and a local dev checkout -->
```bash
cd $KANBAN_ROOT
cp ~/develop/pgai-agent-kanban/team/scripts/upgrade.sh scripts/
$KANBAN_ROOT/scripts/upgrade.sh
```

This is a permanent troubleshooting entry rather than a transitional
note: from v1.7.0 onward, the phase-1 fail-loud paths point every
operator at exactly this recipe whenever the dev-tree script is
missing, malformed, or missing `--phase2` support — the class of
"the installed script cannot correctly hand off" is now a single
recovery recipe rather than three separate incident postmortems.

**One-time cross-v0.80.0 upgrade** (only if your *installed* version is
older than v0.80.0): the standard upgrade above fails when crossing the
v0.80.0 boundary, with:

```
[upgrade] Running install.sh --upgrade ...
[install] --upgrade is not supported by install.sh.
[upgrade] Upgrade failed (exit code 1).
```

This is expected and harmless — the upgrade aborts safely (backup made,
dev tree restored, install untouched). It happens because the v0.80.0
rewrite changed the install/upgrade contract: the old `upgrade.sh`
delegated the deposit by calling `install.sh --upgrade`, but the rewritten
v0.80.0 `install.sh` is fresh-install-only and rejects `--upgrade`. The
old installed `upgrade.sh` cannot drive the new `install.sh`. You must
first replace the installed `upgrade.sh` with the target dev tree's
version, then run it (the new one does its own deposit and succeeds):

<!-- doc-lint: skip — one-time recovery procedure for pre-v0.80.0 installs; requires a live $KANBAN_ROOT at that version and a local dev checkout -->
```bash
cd $KANBAN_ROOT
mv scripts/upgrade.sh scripts/upgrade.sh_old
cp ~/develop/pgai-agent-kanban/team/scripts/upgrade.sh scripts/
scripts/upgrade.sh               # new upgrade.sh does its own deposit; succeeds
```

This is a **one-time, pre-1.0 transitional step**. Each install older than
v0.80.0 hits it exactly once, when first upgrading past v0.80.0; once the
new `upgrade.sh` is in place, every subsequent upgrade works normally with
the standard procedure above. Post-v0.80.0 → newer upgrades are
unaffected, and new installs at v0.80.0 or later never see it (the
post-flatten v1.0.0 origin has no pre-v0.80.0 installs in the world). The
leftover `scripts/upgrade.sh_old` is inert and can be deleted at leisure.

**Stop a wake batch between tasks** (lighter than HALT — one-shot,
self-deleting): `touch` the stop file (default
`<temp_root>/wakeup/stop`; key: `kanban.cfg [wake] stop_file`). The
current task finishes, the batch stops, the file deletes itself.

**Unblock a BLOCKED task** after resolving its cause:
`scripts/reset.sh --project <name> --key <key>` (section 9.1) —
the task returns to BACKLOG with a clean slate and the next wake picks it
up. While any task is BLOCKED with Needs Human, the project dispatches no
new tasks (by design — fix the blocker first).

**Check costs**: `scripts/cost-report.sh --project <name>` (day / RC / month scopes), or
dashboard window 5. Every task writes a `tokens.json`; rollups are
per-day, per-RC, per-agent, per-provider.

**Cancel a stuck RC** (rare, operator-only): section 9.3 —
`scripts/cm/cancel-rc.sh --project <name> --key vX.Y.Z` rewinds release
state; read its `--help` first.
