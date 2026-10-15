# pgai-agent-kanban — Architecture

**Companion:** [team/SOP-core.md](team/SOP-core.md) for the shared law. [team/OVERVIEW.md](team/OVERVIEW.md) for the autonomy principle.

**Where you are standing.** This is a repository document, like [README.md](README.md). `install.sh` does not deposit it at the kanban root — an installed root receives the governance tree plus the `docs/` pages named in `team/config/docs-manifest.txt`. Every path below is written from the repository checkout unless it is explicitly labelled a live-install path.

---

## What This Is

An autonomous, single-operator task-decomposition framework. You drop a requirements document; you come back to a tagged release. The system specializes in any structured deliverable that decomposes into discrete, verifiable tasks — software releases today, prose documents today, and additional workflow types as they are added.

Two properties make it distinctive:

1. **Self-building as a usage pattern.** The framework ships its own releases through its own pipeline — but only because an operator registered the framework's repo as an ordinary project. The framework itself does not know which project is its own source; self-build is a way you can use the kanban, not a feature inside it.
2. **Provider-abstracted.** Which LLM executes a task is a runtime selection, not a property of the work. The active provider is a config value; Claude is the production provider today, and the Codex lane ships experimental (see [docs/codex-known-issues.md](docs/codex-known-issues.md)).

The economics: one host you already run, plus LLM API tokens as the only variable cost. Nothing in this system needs a cluster.

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

Seven role files ship under `team/roles/`. Six are the pipeline that advances a release candidate; the seventh is a horizontal observer that never advances one. Each runs as a separate subagent with a dedicated role file, and one agent of each type runs at a time per repo (per-agent wake locks).

| Agent | Invocation | Purpose |
|---|---|---|
| `PO` | Operator-invoked (`scripts/po-agent.sh`) | Validates a human-authored brief and expands it into a requirements document. The only role with significant human collaboration; no cron line ships for it. |
| `PM` | Wake script (`pm` queue) | Decomposes requirements into tickets across agent queues. Single-shot per ticket; the materializer is idempotent (hash-marker). |
| `CODER` | Wake script (`coder` queue) | Implements features against the open RC branch in a per-task git worktree. Local-only git operations. |
| `WRITER` | Wake script (`writer` queue) | Standalone documents: guides, role files, SOPs, release notes, long-form prose. Same git contract as CODER. |
| `TESTER` | Wake script (`tester` queue) | Verifies the RC against requirements. Files findings to `bugs/` and a structured report. Observation-only — never creates kanban tickets. Halts the chain only on pre-flight failure. |
| `CM` | Wake script (`cm` queue) | Branch/merge/tag mechanics. Applies ship/no-ship policy. The sole agent that touches origin. |
| `OVERWATCH` | Wake script (`overwatch`), plus a separate sweep runner | Horizontal self-monitor outside the RC pipeline. Tier 1 is a deterministic bash sweep over every registered project; Tier 2 is an LLM deep-clean that files quality bugs for anything outside the auto-fix whitelist. An on-BLOCK trigger wakes it when any task transitions to `BLOCKED`. It never advances a release. |

PO is the only agent an operator starts by hand. Once requirements docs exist, the rest of the pipeline is autonomous.

Each role also carries a governance reading manifest at `team/read-order/<role>.txt` — seven manifests, one per role file. See [Governance Stack](#governance-stack).

### Task IDs

Format: `<AGENT>-YYYYMMDD-NNN-slug`. No provider prefix — which LLM ran a task is recorded in that task's `tokens.json` and `status.md`, not in the ID. Examples: `CODER-20260412-001-add-login-endpoint`, `TESTER-20260412-004-verify-login-flow`, `CM-20260412-005-open-rc-v1.2.0`.

The framework also parses the older `CLAUDE-<AGENT>-...` form in task folders and queue entries; no existing task folder is ever renamed.

---

## The Discovery Pipeline

The single source of truth for "what does the system work on next." Runs per-project. There is exactly one path through it; new inputs at the front always feed the same downstream consumer.

```
ITERATION START
  Guard: project.cfg must be readable.
  Guard: per-project HALT (projects/<name>/HALT) — blocks this project.
  Guard: global HALT ($KANBAN_ROOT/HALT) — blocks every project.
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

A `HALT-AFTER` token (`projects/<name>/HALT-AFTER`, e.g. `rc` or `rc:vX.Y.Z`) drains the current RC to completion, then promotes itself to a full HALT — the timing-agnostic way to stop "after the current release ships." The token vocabulary and the drain semantics are owned by `team/SOP-core.md`.

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
CM-open creates <branch_prefix>rc/vX.Y.Z, updates release-state.md
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

CM's default on a release task is to ship. It refuses only when TESTER is BLOCKED, a prerequisite task is missing/incomplete, or the release script fails. Known bugs, gaps, and imperfect work are not reasons to refuse — the system ships and iterates. The full HALT trigger list is owned by `team/roles/CM.md`.

### Project-specific CM-release hooks

Projects supply finalization scripts the framework discovers and runs:

```
projects/<name>/hooks/
├── cm-release-pre-squash.sh   # before squash to develop
├── cm-release-pre-tag.sh      # before tag
└── cm-release-post-tag.sh     # after tag, before the tag is pushed
```

A pre-squash or pre-tag hook that fails — or that exists but is not executable — HALTs the release. A post-tag hook failure is a logged warning only and does not block: the tag already exists locally by then. Projects without a `hooks/` directory behave as the framework default.

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

The loader carries its own Key Registry — the accepted section/key set with each key's status and default. That registry, not this document, is the inventory; the doc-truth lint reconciles every key a document names against it. Which file a given setting belongs in is the placement rule in `HOW_TO.md`.

### Temp root resolution

A single resolver (`temp.sh`) provides the temp directory for all transient output, routed through one set of `pgai_temp_*` helpers rather than scattered `mktemp`/`/tmp` calls. Resolution order, highest precedence first:

1. `PGAI_AGENT_KANBAN_TEMP_DIR` — if already set in the environment, used as-is.
2. `${tmp_root}/${tmp_subdir}` from `kanban.cfg [paths]`, located through `PGAI_AGENT_KANBAN_ROOT_PATH`, or by the library self-locating its own install root when that variable is unset.
3. A last-resort fallback, used only when the config is unreadable.

The resolver never returns empty or `/`. When `pp_load_config` has run for a project, `PP_TEMP_DIR` holds that project's own temp path and the project-aware helpers place files there. The result: the framework's temp footprint is configurable and relocatable, and transient files stay out of shared `/tmp`.

**TMPDIR bridge**: the config loader exports `TMPDIR` derived from the resolved temp root, so standard tooling — `mktemp`, Python's `tempfile`, anything POSIX — lands in the framework temp root by default rather than bare `/tmp`. The bridge is a downstream consumer of the single resolver, not a second resolution site: `kanban.cfg [paths] tmp_root + tmp_subdir` remains the only place the value is configured.

---

## Multi-Project Layout

One installation hosts multiple independent projects. Each has its own state, cadence, and workflow type.

```
$KANBAN_ROOT/                       # Shared infrastructure (live install: no team/ prefix)
├── DIRECTIVES.md                   # Top-level safety rules
├── OVERVIEW.md                     # Autonomy principle
├── SOP-core.md                     # Shared law
├── README.md                       # Installed entry point (from team/README.md)
├── HALT                            # Global halt flag (when present)
├── kanban.cfg                      # Global config ([providers] active, [paths], [debug], ...)
├── projects.cfg                    # Project registry
├── scripts/                        # All bash scripts and libs
├── pm-agent/                       # PM tooling, materializer, token aggregation
├── pgai_agent_kanban/              # The Python package (API, libs, CM modules)
├── halt_after/                     # HALT-AFTER token and drain logic
├── roles/                          # Role files
├── read-order/                     # Per-role governance manifests (<role>.txt)
├── workflows/                      # Workflow-type plugins
├── demos/                          # The four shipped demos
├── templates/                      # Install templates (crontab, pseudocron tiers)
├── config/                         # Manifests and censuses (verbs, docs, dashboard windows)
├── docs/                           # The docs/ pages named in team/config/docs-manifest.txt
├── logs/                           # Cron and wake logs
└── projects/                       # Per-project state
    └── <project-name>/
        ├── project.cfg             # dev_tree_path, git_repo_url, workflow_type, branch_prefix, ceilings, [debug]
        ├── hooks/                  # Project-specific CM-release hooks (optional)
        ├── tasks/                  # Task folders + queues
        ├── requirements/           # Requirements docs
        ├── priority/               # Priority intake
        ├── bugs/                   # Bug reports (TESTER + operator filed)
        ├── release-notes/          # Authored release notes, one file per version
        ├── artifacts/              # Per-project deliverables (document workflow output)
        ├── metrics/                # Per-RC metric rollups
        ├── usage/                  # Token and cost rollups
        ├── logs/                   # Per-project agent and sweep logs
        ├── HALT / HALT-AFTER       # Per-project halt flags (when present)
        ├── deep-verify-ledger.md   # Append-only DEEP-VERIFY tier record
        └── release-state.md        # Active RC, last released version
```

**Dev tree vs. live install.** The source tree (`~/develop/pgai-agent-kanban`) carries a `team/` prefix (`team/scripts/...`). The installed tree (`$KANBAN_ROOT`, e.g. `~/pgai_agent_kanban`) drops it (`scripts/...`). Role files, `SOP-core.md`, read-order manifest lines, and operator instructions all use live-install paths. This document and [README.md](README.md) are the exception: they are repository documents and use repository paths.

**Shared vs. per-project.** Shared at the root: tooling, governance docs, workflow definitions, global config. Per-project under `projects/<name>/`: all runtime state. The line: if a file describes the *system* it is shared; if it describes the *work* it is per-project.

### Per-project trees with optional global fallback

Each project owns its own dev-tree checkout, declared in `project.cfg` as `dev_tree_path`; worktree creation/teardown, the pollution sweep, canonical parking, and all release mechanics resolve per-project via `PP_dev_tree_path` after `pp_load_config`. `kanban.cfg [paths] dev_tree_path` is the OPTIONAL global fallback: an install-time convenience that `install.sh` seeds from the source repo on a fresh install, and a fallback value for projects whose `project.cfg` omits `dev_tree_path`. A customer install that manages OTHER projects may leave the global empty — the kanban's basic operations require only `$KANBAN_ROOT`. The loader validates config shape, not infrastructure state; existence is gated per-consumer via `scripts/lib/dev_tree.sh`. The wake layer skips a release-workflow project whose `PP_dev_tree_path` is missing with a clear per-project log line (document-workflow projects exempt — no dev tree by design); the two test runners (`run-integration-tests.sh`, `run-unit-tests.sh`) keep a hard requirement on the global, because running the kanban's own suite is a development operation. A missing tree degrades that project, never the installation.

### Per-project branch prefix

Each project sets `branch_prefix` in `project.cfg`. The prefix is applied to the branch's leading segment and to tag names — never to the version inside an RC branch path:

- **Empty** → the standard convention: `main`, `develop`, `rc/vX.Y.Z`, `feature/<task-id>`, tags `vX.Y.Z`.
- **`ai_`** → an isolated lane: `ai_main`, `ai_develop`, `ai_rc/vX.Y.Z`, `ai_feature/<task-id>`, tags `ai_vX.Y.Z`. Lets the kanban manage a project whose human `main` branch must stay untouched.

CM is the sole origin-toucher and only via managed scripts (open-rc, release); it refuses to push base branches as a safety invariant. The operator sets up base branches via `init-project-git-repo.sh` — not CM.

---

## Provider Abstraction

The active LLM provider is a runtime selection, read from `kanban.cfg [providers] active`. Each provider has its own wake script under `scripts/wake/`; the dispatcher runs the active one and the others exit immediately on a stagger tick.

**Current reality.** Two provider wake scripts ship: `wake/claude.sh` and `wake/codex.sh`. Claude is the production provider. The Codex lane is exercised — a managed project has shipped end-to-end on Codex with OAuth — and ships experimental (see [docs/codex-known-issues.md](docs/codex-known-issues.md) for the known rough edges). A Gemini lane is designed for but not shipped: `switch-provider.sh` and the `[providers] active` reader accept `gemini` as a name, but `kanban.cfg` has no model section for it and `scripts/wake/` holds no Gemini script, so selecting it gives the dispatcher nothing to run. `[providers] available` defaults to `claude codex` accordingly.

What makes adding a provider a wake-script-plus-pricing-table change rather than a refactor is that nothing above the wake layer is provider-shaped: task IDs, queues, role files, and the task folder are all provider-neutral.

Token capture is provider-aware: each task's `tokens.json` records `provider`, `model`, agent, RC, token counts, and timing, so cost rollups break down per provider when more than one is in use.

---

## Workflow Types

A workflow type is a plugin — a directory under `$KANBAN_ROOT/workflows/<name>/` containing a manifest and a set of hook functions the engine calls. Adding a workflow type is a new directory, not an engine edit. The engine never reads the type's name to decide behavior; it reads capability values from the manifest and hook return values.

### Plugin shape

A plugin has two layers. Layer 1 is required; Layer 2 is optional.

```
$KANBAN_ROOT/workflows/<name>/
├── workflow.cfg    # Layer 1, required — manifest: capabilities and status
├── workflow.sh     # Layer 1, required — eight bash hooks the engine calls
└── pipeline.yaml   # Layer 2, optional — a declarative PM decomposition pipeline
```

The manifest (`workflow.cfg`) declares:

- `[workflow] name`, `description`, `status` (`scaffold` or `ready`)
- `[capabilities] version_semantics` (`semver` | `label` | `none`), `git_mode` (`none` | `ro` | `rw`), `finalize` (`tag` | `publish` | `report`), `agents` (comma-separated ordered roster)

The hook set (`workflow.sh`) defines eight `wf_*` functions: `wf_git_mode`, `wf_resolve_target_version`, `wf_pre_task`, `wf_post_task`, `wf_finalize`, `wf_agents`, `wf_bundle_source_branch`, `wf_dashboard_render`. Each hook does one thing. Policy lives inside the hook; the engine never dispatches on a type-name switch.

The `pipeline.yaml` format — what inputs a pipeline needs, which agents participate, what steps run in order — is specified field by field in `team/workflows/SCHEMA.md`. Of the three shipped plugins, `release` and `document` carry one; `testing-only` does not.

### Discovery

The dispatcher (`scripts/lib/workflow.sh`) resolves a project's workflow type by reading `project.cfg workflow_type`, discovery-scanning `<workflows-root>/*/workflow.cfg`, validating the matching manifest, and sourcing the plugin's `workflow.sh`. There is no central enum, no registration file, no engine-side list. Drop a directory in, the dispatcher finds it. Remove a directory, the dispatcher no longer offers it. Registration is directory existence.

### Fail-closed routing

The dispatcher is fail-closed on every load-time error:

- Unknown workflow type (no matching directory) → the requirement routes to BLOCKED with a reason naming the type.
- Missing or malformed `workflow.cfg` → BLOCKED with the manifest problem named.
- `status = scaffold` (or any value other than the literal `ready`) → BLOCKED with the type name in the reason.

No misconfiguration silently falls back to `release`: a broken or unknown type stops with its reason named, and a correct configuration routes exactly as it declares.

### Upgrade-survival contract

The framework's `upgrade.sh --force` refreshes shipped plugins in place and leaves operator-authored plugins untouched. The mechanism is an overlay copy (`cp -r "$src/." "$dst/"`) that copies files present in the new install onto the live tree; directories absent from the source tree are not affected. An operator-authored `$KANBAN_ROOT/workflows/acme-deploy/` survives every upgrade byte-identical.

The one hazard: a custom plugin whose name collides with a shipped plugin (`release`, `document`, `testing-only`) is silently overwritten on upgrade. The generator refuses those names, `validate-workflow.sh` documents the rule, and [docs/public-contract.md](docs/public-contract.md) states the recommendation to use org-prefixed names.

The upgrade-survival guarantee is a stable contract — see [docs/public-contract.md](docs/public-contract.md).

### Shipped plugins

| Workflow | Capabilities | Description |
|---|---|---|
| `release` | `version_semantics=semver`, `git_mode=rw`, `finalize=tag`, `agents=pm,coder,writer,tester,cm` | Software release with the git RC branch lifecycle. The default; where most daily activity happens. |
| `document` | `version_semantics=semver`, `git_mode=none`, `finalize=publish`, `agents=pm,writer,tester,cm` | Prose/document deliverables. Drives off the requirement's semver `## Target Version`; publishes to `projects/<name>/artifacts/v<semver>-<artifact-name>.<ext>` (versioned library, every version kept). |
| `testing-only` | `version_semantics=label`, `git_mode=ro`, `finalize=report`, `agents=pm,tester` | Runs a project's test suites at a named ref. Label semantics: the version is a name for the report artifact only — never enters release-state, never triggers ceiling checks, and the dashboard renders label items by open/running/done. No CM step. |
| (operator-authored) | declared by the plugin | Any org-authored plugin under `$KANBAN_ROOT/workflows/<name>/` — see [docs/creating-a-workflow.md](docs/creating-a-workflow.md). |

All three shipped plugins carry `status = ready`. The agent roles work across every workflow type; their specialization adapts to the workflow context by reading capability flags (TESTER runs unit tests for `release`, verifies completeness against an outline for `document`, runs the project's test suite for `testing-only`).

For the full authoring walkthrough — live-operator (no git) and contributor (dev tree) paths — see [docs/creating-a-workflow.md](docs/creating-a-workflow.md).

---

## Branch and Git Conventions

Branch names below are shown without a prefix. A project with a non-empty `branch_prefix` carries it on the leading segment, as described under [Per-project branch prefix](#per-project-branch-prefix).

| Branch | Purpose | Creator | Deleter |
|---|---|---|---|
| `main` | Tagged releases only | exists | never |
| `develop` | Integration buffer | exists | never |
| `rc/vX.Y.Z` | Active release candidate | CM (open-rc) | CM (release) |
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

The canonical write path for a state change is the status-writer tool, not a hand-edit — `team/SOP-core.md` owns that procedure.

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

Token usage is captured per-task (`tokens.json`) and rolled up per-day and per-RC by `pm-agent/aggregate_tokens.py`. Pricing is keyed by provider → model. Operator surfaces: a `cost-report.sh` CLI (day/RC/month scopes) and a dashboard metrics window. Because capture is provider-aware, switching providers makes the per-provider cost delta visible empirically. RC metrics measure the kanban's own task executions only — the operator's interactive usage does not contaminate them.

The same per-RC rollups under `projects/<name>/metrics/rc/` are the source the measured-pace figures in [README.md](README.md) are generated from; no pace claim in either document is hand-written.

---

## Scheduling: cron and pseudocron

Two interchangeable schedulers drive the continuous mode:

- **System cron** — the default. The installed crontab fires each pipeline agent every two minutes on a sub-minute stagger: PM, CM, and TESTER on even minutes (`--sleep=0/21/42`), CODER and WRITER on odd minutes (`--sleep=0/21`). OVERWATCH gets two lines of its own — an hourly Tier-1 sweep and a daily Tier-2 wake — plus separate cleanup entries. The annotated template is `team/templates/install/crontab.example`; `install-crontab.sh` installs it.
- **Pseudocron** — a self-contained scheduler for hosts without usable cron (containers, restricted environments), installed via `install-pseudocron.sh` with small/medium/large tier templates and parity with the system-cron tiers.

---

## Verification Doctrine — the Loop Applied to Gates

The kanban is a looped system: work in, verified output out, the loop
continuing on new input or on the bugs the verifier files. Bugs are the
healing mechanism, so the machinery blocks only for states the next
iteration cannot absorb.

- **Runner classes.** Gated runners run pre-flights in two classes:
  breakage (syntax/compile/import — exit 1 before tests) and quality
  (all lints — findings print, land in a machine-readable findings
  file, and the run continues; exit code reflects tests and breakage
  only).
- **Findings lifecycle.** TESTER ingests the findings file; every
  finding cites an open bug or TESTER files one. Green suites plus
  quality findings map to ship-with-concerns; the release ships and
  the next bundle heals.
- **The census.** Every halting site in the shipped tree is
  inventoried in [docs/blocking-site-census.md](docs/blocking-site-census.md) and classified
  BLOCK-WORTHY / CONVERT / AGENT-RESOLVABLE; a census lint
  (quality-class, like everything else) keeps new gates honest. New
  gates declare their class in a code comment and speak dual-channel.
- **Dual-channel errors.** Every refusal emits error: (stable machine
  key) / message: (stranger-readable cause) / remedy: (runnable next
  step) — machine precision and human legibility, never one without
  the other.
- **The completion gate.** At done-claims it checks delivery only:
  work merged (with branch-history evidence accepted when the feature
  branch was already tidied), worktree clean. Wording concerns belong
  to verification time, one loop later.
- **Doc truth.** A separate lint holds the documents to the same
  standard as the code: every command a document cites must exist in
  the tree and answer `--help`, every configuration key it names must
  be in the loader's Key Registry, and every dashboard window it
  describes must match the window census.

The three census classes and the new-gate rule are law in `team/SOP-core.md`; this section is the architectural summary, not the canon.

## Governance Stack

Each role declares its own governance reading list in a manifest at `read-order/<role>.txt`. The wake composer resolves that manifest and assembles the agent's prompt from it, in manifest order. **A document not named in a role's manifest is not read by that role** — there is no implicit stack behind the manifests.

Seven manifests ship, one per role in the catalog (the six pipeline agents plus OVERWATCH). Each currently names the same five-document shape, differing only in its role file:

1. **DIRECTIVES.md** — top-level safety rules
2. **OVERVIEW.md** — autonomy principle
3. **SOP-core.md** — shared law
4. **README.md** — kanban entry point
5. **roles/<AGENT>.md** — the agent's role

That uniformity is a starting point, not a constraint; the mechanism exists so the lists can diverge per role. Two things the composer supplies that no manifest may name: the **per-project README.md**, spliced in after the kanban README when the active project has one (its existence is a runtime fact), and the **task folder** — `README.md`, `status.md`, and the ticket's context paths — which is assembled from the task, not the manifest.

Resolution degrades rather than refuses. A manifest line naming a missing file is skipped and narrated; a role whose manifest resolves to zero files falls back to the hardcoded five-document list above, with a `read_order:` line in the wake log recording the degradation. Prompt assembly is not a gate.

Each role also carries a byte budget under `kanban.cfg [governance]`, reported on demand by `scripts/lint_governance_byte_budgets.sh`. The lint narrates and always exits 0: an over-budget role costs tokens, not correctness, so it is a quality finding and quality findings never stop the loop. Full mechanism — manifest format, resolver contract, budget units, add-a-doc procedure — is in [docs/read-order-manifests.md](docs/read-order-manifests.md); the reading order as law is in `SOP-core.md`, "Required Read Order For Assigned Work."

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

These are stable architectural commitments — the load-bearing contracts other parts of the system are built against. The operator-facing version of this list, with the compatibility promise attached, is [docs/public-contract.md](docs/public-contract.md).

- Task folder shape; the six-state machine (no REVIEW)
- Manifest-declared governance reading order (`read-order/<role>.txt`), with the composer's fallback when a manifest resolves to nothing
- Single-threaded execution per repo; local-only git for non-CM agents; CM as sole origin-toucher
- File system as source of truth; git as history
- Config single-source-of-truth precedence (env > cfg > default, fail-loud)
- Per-task worktree isolation (CODER/WRITER branched, TESTER detached); configurable temp root with the TMPDIR bridge; the post-task pollution sweep
- Per-project hooks, branch_prefix, and HALT scopes
- The `v0.0.0` fresh-install sentinel
- `$PGAI_*` env-var-driven roots; parallel installs supported

---

*This document is the architectural contract. SOP-core.md is the shared law. Role files are the per-agent specifications and procedure.*
