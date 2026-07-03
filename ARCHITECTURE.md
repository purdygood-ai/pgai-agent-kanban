# pgai-agent-kanban — Architecture

**Companion:** `SOP.md` for operational procedures. `OVERVIEW.md` for the autonomy principle.

---

## What This Is

An autonomous, single-operator task-decomposition framework. You drop a requirements document; you come back to a tagged release. The system specializes in any structured deliverable that decomposes into discrete, verifiable tasks — software releases today, prose documents today, and additional workflow types as they are added.

Two properties make it distinctive:

1. **Self-building as a usage pattern.** The framework ships its own releases through its own pipeline — but only because an operator registered the framework's repo as an ordinary project. The framework itself does not know which project is its own source; self-build is a way you can use the kanban, not a feature inside it.
2. **Provider-abstracted.** Which LLM executes a task is a runtime selection, not a property of the work. The framework is built so the active provider is a config value; Claude is the production provider today. The Codex lane is exercised and ships experimental (see docs/codex-known-issues.md); the Gemini lane is designed for but not yet exercised.

The economics: a single VPS (~$200/year of hardware) plus LLM API tokens as the only variable cost. Hardware is solved; the architecture is solved and code-complete.

---

## Design Philosophy

> An 85% solution that keeps moving beats a 100% solution that stalls.

- **Files on disk are the source of truth.** No database. State lives in files; git is the history.
- **Git history is the safety net.** Agents make reversible decisions; mistakes are cleaned up in the next iteration.
- **Convention over configuration.** Versioning, branch naming, queue layout, task IDs, sentinel values — conventions enforced by tooling, not knobs.
- **Single source of truth, fail loud.** Configuration resolves through one validated loader (`config_loader.sh`) with a fixed precedence: environment variable > project config > default. Missing required keys fail loudly rather than silently falling back.
- **No default project — explicit or fail loud.** Every project resolution is explicit: a `--project` argument, `$PGAI_PROJECT_NAME`, or the owning project derived from the item being processed. Aggregation views iterate all registered projects. When no explicit resolution applies, commands exit non-zero with a clear message rather than silently substituting a default — a wrong-but-plausible default can mask a broken resolution path for as long as it happens to satisfy it.
- **Single-threaded by design.** One RC at a time. One agent of each type at a time per repo (per-agent wake locks). Trades throughput for reliability and zero race conditions.
- **Single direction of flow.** Bugs → priority items → requirements docs → tasks → tagged releases. New inputs at the front of the pipeline always traverse the same downstream path.
- **Isolation from shared global state.** Agent work, test runs, and verification routines isolate themselves from the operator's live resources — CODER/WRITER task work happens in per-task git worktrees, TESTER verifies in a detached-HEAD worktree, temp output goes to a configured temp root (with `TMPDIR` bridged to match), and verification routines use private throwaway instances rather than shared ones. A post-task pollution sweep enforces the invariant deterministically.
- **TESTER reports, CM decides.** TESTER produces categorized findings; CM applies ship/no-ship policy. TESTER halts the chain only when it cannot complete verification (pre-flight failure), never on found problems.
- **The intake file encodes the human's decision; agents execute it.** A requirements/bug/priority file is where the operator constrains the work — file allowlists, "must not modify" guards, acceptance tripwires. This is how the operator steers an autonomous chain without sitting in the loop.
- **Stalling is failure.** Agents keep working while there is work and no conflict. The system fails loud, not silent.

---

## The Agents

Six specialized roles plus one observer. Each runs as a separate subagent with a dedicated role file. One agent of each type runs at a time per repo (per-agent wake locks).

| Agent | Invocation | Purpose |
|---|---|---|
| `PO` | Human-invoked | Translates briefs and architecture into requirements documents. The only role with significant human collaboration. |
| `PM` | Wake script (`pm` queue) | Decomposes requirements into tickets across agent queues. Single-shot per ticket; the materializer is idempotent (hash-marker). |
| `CODER` | Wake script (`coder` queue) | Implements features against the open RC branch in a per-task git worktree. Local-only git operations. |
| `WRITER` | Wake script (`writer` queue) | Prose deliverables: release notes, documents, marketing copy. Same git contract as CODER. |
| `TESTER` | Wake script (`tester` queue) | Verifies the RC against requirements. Files findings to `bugs/` and a structured report. Observation-only — never creates kanban tickets. Halts the chain only on pre-flight failure. |
| `CM` | Wake script (`cm` queue) | Branch/merge/tag mechanics. Applies ship/no-ship policy. The sole agent that touches origin. |

PO is the only human-invoked agent. Once requirements docs exist, the rest of the pipeline is autonomous.

### Task IDs

Format: `<AGENT>-YYYYMMDD-NNN-slug`. No provider prefix — which LLM ran a task is recorded in that task's `tokens.json` and `status.md`, not in the ID. Examples:

- `CODER-20260609-003-config-loader-sot`
- `TESTER-20260609-008-verify-worktree-isolation`
- `CM-20260609-001-open-rc-v0.57.0`

---

## The Discovery Pipeline

The single source of truth for "what does the system work on next." Runs per-project. There is exactly one path through it; new inputs at the front always feed the same downstream consumer.

```
ITERATION START
  Guard: if a HALT file is present (global or per-project), block.
  Guard: if an Active RC exists for this project, exit. One RC at a time.
        │
        ▼
STEP 1 — Bugs check
  Scan bugs/ for unhandled BUG-*.md (Status: open), cross-ref bug_backlog.md.
  If any: bundle ALL into one requirements file at current+patch version,
          mark them handled, STOP this iteration.
        │
        ▼ (only if no unhandled bugs)
STEP 2 — Priority queue check
  Scan priority/ for PRIORITY-*.md.
  If any: bundle into one requirements file at current+patch version,
          mark handled, STOP this iteration.
        │
        ▼ (only if no bugs and no priorities)
STEP 3 — Regular requirements pickup
  Scan requirements/ for unprocessed docs where target_version > current.
  If any: pick the lowest version, hand off to PM, STOP this iteration.
        │
        ▼ (nothing found)
STEP 4 — Idle exit
  No work pending. Exit cleanly. Idle is a normal state.
```

### Why this shape

- **Bugs and priorities are *producers* of requirements files; Step 3 is the only *consumer*.** PM only ever processes requirements files. One canonical processing path.
- **Priority is encoded in version numbers, not directory layout.** Lower version = higher priority by natural sort.
- **One stop per iteration.** Each producing step stops so the next iteration sees the new requirements file and picks it up via Step 3.
- **An Active RC or a HALT blocks the pipeline.** Single-threaded by design.

### HALT scopes

Two halt scopes, both honored by the pipeline:

- **Global** — `$KANBAN_ROOT/HALT`. Blocks all projects.
- **Per-project** — `projects/<name>/HALT`. Blocks only that project.

A `HALT-AFTER` token (`projects/<name>/HALT-AFTER`, e.g. `rc` or `rc:vX.Y.Z`) drains the current RC to completion, then promotes itself to a full HALT — the timing-agnostic way to stop "after the current release ships."

### Modes

| Mode | Behavior |
|---|---|
| **Continuous** (cron / pseudocron) | Runs the pipeline; on success loops until idle, then exits. The default unattended mode. |
| **One-shot** (`--auto`) | Runs the pipeline exactly once. Same path, single iteration. For testing the automatic path manually. |
| **Manual** | Operator passes a specific requirements file; PM is queued directly. Pipeline routing is bypassed. |

---

## The Release Lifecycle (`release` workflow)

```
Requirements doc picked up by pipeline
        │
        ▼
PM decomposes → CM-open + N×CODER + N×WRITER + TESTER + CM-release
        │
        ▼
CM-open creates rc/<branch_prefix>vX.Y.Z, updates release-state.md
        │
        ▼
CODER/WRITER work in per-task git worktrees off the RC branch,
merge --no-ff back into the RC
        │
        ▼ (all features DONE)
TESTER verifies the RC against requirements, files a structured report
        │
        ▼ (always continues — TESTER does not block on findings)
CM reads report, applies policy:
   - TESTER state BLOCKED (pre-flight failure) → CM refuses; human attention
   - otherwise → CM runs project hooks, squashes to develop and main,
     tags, pushes, deletes the RC
        │
        ▼
release-state.md updated. Bugs that survive a release are filed by
TESTER's findings; discovery picks them up next iteration.
```

### TESTER: categorize, don't block

TESTER produces a structured report with categorized findings (`pass`, `pass-with-caveat`, `gap`, `bug`). Each `gap`/`bug` is filed to `bugs/` for the next iteration. TESTER's terminal state is:

- **DONE** — verification completed; the report's recommendation (`PASS` / `SHIP-WITH-CONCERNS`) is informational, not a veto.
- **BLOCKED** — verification could not complete (pre-flight failure: dirty checkout, runner crash, missing requirements). The only state that halts the chain.

### CM: apply policy, don't second-guess

CM's default on a release task is to ship. It refuses only when TESTER is BLOCKED, a prerequisite task is missing/incomplete, or the release script fails. Known bugs, gaps, and imperfect work are not reasons to refuse — the system ships and iterates.

### Project-specific CM-release hooks

Projects supply finalization scripts the framework discovers and runs:

```
projects/<name>/hooks/
├── cm-release-pre-squash.sh   # before squash to develop
├── cm-release-pre-tag.sh      # before tag
└── cm-release-post-tag.sh     # after tag (best-effort, non-blocking)
```

Pre-squash/pre-tag failures block the release; post-tag failures are logged. Projects without a `hooks/` directory behave as the framework default.

---

## Per-Task Worktree Isolation

CODER and WRITER do not mutate a shared dev-tree checkout. Each task runs in its own **git worktree** created off the active RC branch, seeded from the local repository, and torn down under the configured temp root when the task completes.

TESTER is isolated the same way, with one refinement: its worktree is created at the RC head in **detached-HEAD** state. Detached checkout cannot conflict with a branch checked out elsewhere (so no dev-tree parking is needed for TESTER), it matches TESTER's read-only git contract, and everything verification produces — ad-hoc scripts, Python bytecode caches, scratch files — lands in a disposable directory that teardown deletes. For the duration of a TESTER task, `PGAI_DEV_TREE_PATH` is re-exported to point at the worktree (the sanctioned env-over-cfg override) and restored afterward.

**Post-task pollution sweep**: the wake layer snapshots the canonical dev tree's `git status --porcelain` state before each agent task and diffs it after. New untracked files are quarantined (moved, never deleted) to `<temp_root>/pollution/<task_id>/` with a logged warning; tracked-file modification during a git-read-only agent's task logs an error. The enforced invariant: only CM's managed release operations change the canonical tree. The sweep is best-effort and never blocks a task — its output is evidence, feeding the training-trace loop that refines role files.

This solves the "two agents fighting over one working tree" problem without remote pushes: worktrees share the same `.git` but have independent checkouts. All git operations use explicit `git -C "$dev_tree"` targeting so the worktree's location is unambiguous. Branch visibility is local-only — no agent fetches or pushes during a task; CM is the sole origin-toucher.

---

## Configuration: Single Source of Truth

All configuration resolves through one validated loader, `config_loader.sh`, with a fixed precedence:

```
environment variable  >  project config (project.cfg / kanban.cfg)  >  built-in default
```

The canonical pattern (config_loader.sh):

```bash
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-${_dev_tree_path}}"
```

Required keys that are missing fail loudly. There is no silent fallback to a wrong default. This eliminates a class of bugs where divergent ad-hoc config resolution in different scripts produced inconsistent behavior between the dev tree and the live install.

### Temp root resolution

A single resolver (`temp.sh`) provides the temp directory for all transient output, routed through one function rather than scattered `mktemp`/`/tmp` calls. Resolution order:

1. `PP_TEMP_DIR` (exported by `pp_load_config` for the active project), else
2. `${tmp_root}/${tmp_subdir}` from `kanban.cfg [paths]`, else
3. a safe default.

The resolver never returns empty or `/`. ~68 call sites route through it. This makes the framework's temp footprint configurable and relocatable, and keeps transient files out of shared `/tmp`.

**TMPDIR bridge** : the config loader exports `TMPDIR` derived from the resolved `PGAI_AGENT_KANBAN_TEMP_DIR`, so standard tooling — `mktemp`, Python's `tempfile`, anything POSIX — lands in the framework temp root by default rather than bare `/tmp`. The bridge is a downstream consumer of the single resolver, not a second resolution site: `kanban.cfg [paths] tmp_root + tmp_subdir` remains the only place the value is configured. 

---

## Multi-Project Layout

One installation hosts multiple independent projects. Each has its own state, cadence, and workflow type.

```
$KANBAN_ROOT/                       # Shared infrastructure (live install: no team/ prefix)
├── DIRECTIVES.md                   # Top-level safety rules
├── OVERVIEW.md                     # Autonomy principle
├── SOP.md                          # Operational procedures
├── README.md                       # Entry point
├── HALT                            # Global halt flag (when present)
├── kanban.cfg                      # Global config ([providers] active, [paths], [debug], ...)
├── scripts/                        # All bash scripts and libs
├── pm-agent/                       # PM tooling, materializer
├── roles/                          # Role files
├── workflows/                      # Workflow YAML definitions
└── projects/                       # Per-project state
    └── <project-name>/
        ├── project.cfg             # dev_tree_path, git_repo_url, workflow_type, branch_prefix, ceilings, [debug]
        ├── hooks/                  # Project-specific CM-release hooks
        ├── tasks/                  # Task folders + queues
        ├── requirements/           # Requirements docs
        ├── priority/               # Priority intake
        ├── bugs/                   # Bug reports (TESTER + operator filed)
        ├── artifacts/              # Per-project deliverables (document workflow output)
        ├── HALT / HALT-AFTER       # Per-project halt flags (when present)
        └── release-state.md        # Active RC, last released version
```

**Dev tree vs. live install.** The source tree (`~/develop/pgai-agent-kanban`) carries a `team/` prefix (`team/scripts/...`). The installed tree (`$KANBAN_ROOT`, e.g. `~/pgai_agent_kanban`) drops it (`scripts/...`). Role files, SOP, and operator instructions use live-install paths.

**Shared vs. per-project.** Shared at the root: tooling, governance docs, workflow definitions, global config. Per-project under `projects/<name>/`: all runtime state. The line: if a file describes the *system* it is shared; if it describes the *work* it is per-project.

### Per-project trees with optional global fallback

Each project owns its own dev-tree checkout, declared in `project.cfg` as `dev_tree_path`; worktree creation/teardown, the pollution sweep, canonical parking, and all release mechanics resolve per-project via `PP_dev_tree_path` after `pp_load_config`. `kanban.cfg [paths] dev_tree_path` is the OPTIONAL global fallback: an install-time convenience that `install.sh` seeds from the source repo on a fresh install, and a fallback value for projects whose `project.cfg` omits `dev_tree_path`. A customer install that manages OTHER projects may leave the global empty — the kanban's basic operations require only `$KANBAN_ROOT`. The loader validates config shape, not infrastructure state; existence is gated per-consumer via `scripts/lib/dev_tree.sh`. The wake layer skips a release-workflow project whose `PP_dev_tree_path` is missing with a clear per-project log line (document-workflow projects exempt — no dev tree by design); the two test runners (`run-integration-tests.sh`, `run-unit-tests.sh`) keep a hard requirement on the global, because running the kanban's own suite is a development operation. A missing tree degrades that project, never the installation.

### Per-project branch prefix

Each project sets `branch_prefix` in `project.cfg`:

- **Empty** → the standard convention: `main`, `develop`, `rc/vX.Y.Z`, `feature/<task-id>`.
- **`ai_`** → an isolated lane: `ai_main`, `ai_develop`, `ai_rc/ai_vX.Y.Z`, tags prefixed. Lets the kanban manage a project whose human `main` branch must stay untouched.

CM is the sole origin-toucher and only via managed scripts (open-rc, release); it refuses to push base branches as a safety invariant. The operator sets up base branches via `init-project-git-repo.sh` — not CM.

---

## Provider Abstraction

The active LLM provider is a runtime selection, read from `kanban.cfg [providers] active`. Wake scripts for each provider can be wired so only the active one does work; the others exit immediately on a stagger tick.

**Current reality:** Claude is the production provider. The Codex lane is exercised — a managed project has shipped end-to-end on Codex with OAuth — and ships experimental for v1.0.0 (see docs/codex-known-issues.md for the known rough edges). The Gemini lane is designed-for (provider-neutral task IDs, queues, and role files; per-provider wake-script structure; provider-aware token capture and pricing) but not yet exercised in production. Adding a provider is a wake-script + pricing-table change, not a refactor.

Token capture is provider-aware: each task's `tokens.json` records `provider`, `model`, agent, RC, token counts, and timing, so cost rollups break down per provider when more than one is in use.

---

## Workflow Types

A workflow type is a YAML file defining inputs, the agent pipeline, outputs, and versioning behavior. Adding a workflow type is data, not code.

| Workflow | Status | Description |
|---|---|---|
| `release` | Shipped | Software release with the git RC branch lifecycle. The default; where most daily activity happens. |
| `document` | Shipped | Prose/document deliverables. Drives off the requirement's semver `## Target Version`; CM finalize publishes the main deliverable to `projects/<name>/artifacts/v<semver>-<name>.<ext>` (versioned library, every version kept). |
| (future) | Planned post-v1.0 | Presentation, image, and other types as needs arise. |

The agent roles work across all workflow types; their specialization adapts to the workflow context (TESTER runs unit tests for `release`, verifies completeness against an outline for `document`).

---

## Branch and Git Conventions

| Branch | Purpose | Creator | Deleter |
|---|---|---|---|
| `main` (or `<prefix>main`) | Tagged releases only | exists | never |
| `develop` (or `<prefix>develop`) | Integration buffer | exists | never |
| `rc/vX.Y.Z` (prefixed per project) | Active release candidate | CM (open-rc) | CM (release) |
| `feature/<task-id>` | Single task, in a worktree | CODER/WRITER | self, after merge |

**Git contract per agent:** PO, PM, TESTER are read-only on git. CODER and WRITER do local-only operations in per-task worktrees (branch, commit, merge `--no-ff`; never fetch or push). CM is the sole origin-toucher — pulls on RC open, squash-merges RC → develop → main on release, pushes tags, deletes the RC.

**Version semantics:** X = breaking, Y = new non-breaking feature, Z = bug/patch. Discovery's bug and priority bundling produces Z bumps. Minor (Y) bumps come only from a hand-authored `vX.Y.0` requirements doc; major (X) bumps are operator-driven.

---

## Task Model

### State machine — six states, no REVIEW

```
BACKLOG → WAITING → WORKING → DONE
                    WORKING → BLOCKED
                    WORKING → WONT-DO
                    WAITING → BACKLOG (auto-promote when prerequisites clear)
```

- **BACKLOG** — ready, no blocking prerequisites.
- **WAITING** — soft, automatic: a prerequisite isn't DONE/WONT-DO yet; auto-promotes to BACKLOG when it clears. No human action.
- **WORKING** — an agent currently holds the task.
- **BLOCKED** — hard, manual: the task hit something it cannot resolve (missing credentials, ambiguous requirements, broken upstream). A human must investigate.
- **DONE** / **WONT-DO** — terminal.

There is deliberately no "needs human review" state. If something needs human eyes it is BLOCKED with a clear reason. Accumulating review items is the death of autonomy.

### Task folder shape

```
projects/<name>/tasks/<task-id>/
├── README.md       # Goal, inputs, acceptance criteria
├── status.md       # State, history, blocked reason
├── artifacts/      # Outputs, including tokens.json
└── logs/           # Wake-script logs
```

---

## release-state.md

Per-project file; CM is the only writer. Tracks Active RC, RC-opened time/task, Last Released version/time/task. Readers: PO (refuses to draft if an RC is active), PM (gates on Active RC), discovery (computes patch versions from Last Released). Updated on open (sets Active RC) and release (clears it, sets Last Released); never on failure.

`v0.0.0` is the fresh-install sentinel: patch-only paths are disabled until the first real release ships, so a brand-new project accepts whatever version its first requirements doc declares.

---

## Cost Visibility

Token usage is captured per-task (`tokens.json`) and rolled up per-day and per-RC by `aggregate_tokens.py`. Pricing is keyed by provider → model. Operator surfaces: a `cost-report.sh` CLI (day/RC/month scopes) and a dashboard metrics window. Because capture is provider-aware, switching providers makes the per-provider cost delta visible empirically. RC metrics measure the kanban's own task executions only — the operator's interactive usage does not contaminate them.

---

## Scheduling: cron and pseudocron

Two interchangeable schedulers drive the continuous mode:

- **System cron** — the default; per-agent entries on a stagger (e.g. PM at :00/:05, CM at :01/:06).
- **Pseudocron** — a self-contained scheduler for hosts without usable cron (containers, restricted environments), installed via `install-pseudocron.sh` with small/medium/large tier templates and parity with the system-cron tiers.

---

## Governance Stack

Reading order; every agent reads top-down before processing a task. Each layer narrows scope.

1. **DIRECTIVES.md** — top-level safety rules
2. **OVERVIEW.md** — autonomy principle
3. **SOP.md** — operational procedures
4. **README.md** — kanban entry point
5. **Per-project README.md** (when present)
6. **roles/<AGENT>.md** — the agent's role
7. **Task README.md** — what this task is
8. **Task status.md** — current state
9. **Requirements doc / context paths** — what to actually do

Role files are written without provider-specific language. The same `CODER.md` applies whichever provider is active; provider quirks (if any) are addressed in the wake-script prompt prelude, not in role files.

---

## Self-Build Property

The kanban builds itself. Every release of the kanban codebase is shipped by the kanban — autonomous decomposition, implementation, verification, and git tag. This is the highest-fidelity validation possible: structural refactors of the framework's own infrastructure (temp root, worktree isolation, config single-source-of-truth, pseudocron) go through the same pipeline as any feature. If the chain can ship structural refactors of its own infrastructure, it can ship anything decomposable.

**Self-build is a usage pattern, not a framework feature.** The mechanism is one `create-project.sh` invocation: an operator registers the framework's own repository as a regular release-workflow project, and from that moment the pipeline treats it identically to any other project. Nothing in the code knows or cares that the managed repository happens to be its own source.

> Any release software project is treated the same; the agents must not care what the project is and must not be able to tell from anything in the code that a project is the framework itself.

Per-project priority ordering (`projects.cfg priority=`) is the scheduling lever the operator uses to ensure the framework's own release work runs ahead of, or behind, other registered projects. That is a scheduling feature, available to every project — not self-build awareness.

The kanban is one project among several under `projects/`; the self-build is one usage of the release workflow, not a special case.

---

## What's Stable

These are stable architectural commitments — the load-bearing contracts other parts of the system are built against:

- Task folder shape; the six-state machine (no REVIEW)
- Governance stack reading order
- Single-threaded execution per repo; local-only git for non-CM agents; CM as sole origin-toucher
- File system as source of truth; git as history
- Config single-source-of-truth precedence (env > cfg > default, fail-loud)
- Per-task worktree isolation (CODER/WRITER branched, TESTER detached); configurable temp root with the TMPDIR bridge; the post-task pollution sweep
- Per-project hooks, branch_prefix, and HALT scopes
- The `v0.0.0` fresh-install sentinel
- `$PGAI_*` env-var-driven roots; parallel installs supported

---

*This document is the architectural contract. SOP.md is the procedure. Role files are the per-agent specifications.*
