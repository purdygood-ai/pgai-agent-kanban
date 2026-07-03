# PGAI Agent Kanban

**An autonomous task-decomposition framework that ships its own releases.**

Drop a requirements document. Walk away. Come back to a tagged release.

The kanban orchestrates specialized AI agents through a single-threaded,
file-driven pipeline that decomposes structured work — software releases,
prose documents — into discrete, verifiable tasks, executes them, verifies
the result, and ships it. It runs unattended on a single VPS, driven by cron.

The framework is provider-extensible. **Claude is the supported, production
provider today.** The Codex/OpenAI lane is exercised — a managed project has
shipped end-to-end on Codex — and ships **experimental** in v1.0.0; see
[docs/codex-known-issues.md](docs/codex-known-issues.md) before running it
unattended. The Gemini lane is scaffolded in the architecture —
provider-neutral task IDs, queues, role files, and per-provider wake
structure — but not yet exercised. Adding a provider is a configuration and
wake-script change, not a refactor.

Every release of this framework was decomposed, implemented, verified,
tagged, and pushed by the framework itself — but only because we
registered the framework's own repository as a regular project. Self-build
is a usage pattern (one `create-project.sh` invocation), not a feature
hard-coded into the framework: nothing in the code knows or cares that the
managed repo happens to be its own source.

---

## How it works

```
You drop:           requirements/v1.4.0-add-export-feature.md
                          │
Discovery (cron) ──▶ PM decomposes into tasks
                          │
                    CM opens rc/v1.4.0
                          │
                    CODER / WRITER implement in isolated git worktrees
                          │
                    TESTER verifies against the requirements, files findings
                          │
                    CM applies ship policy: squash → develop → main, tag, push
                          │
You return to:      v1.4.0 tagged on origin, release notes written,
                    any found bugs filed for the next iteration
```

**Six specialized agents**, each with a dedicated role file:

| Agent | Role |
|---|---|
| PO | Translates briefs into requirements documents (the only human-collaborative role) |
| PM | Decomposes requirements into tasks across agent queues |
| CODER | Implements features in per-task git worktrees |
| WRITER | Prose deliverables: release notes, documents |
| TESTER | Verifies the release candidate; files findings; never blocks on imperfection |
| CM | Branch/merge/tag mechanics; the only agent that touches origin |

**Design principles:**

- **Files on disk are the source of truth.** No database. Git is the history
  and the safety net.
- **Single-threaded by design.** One release candidate at a time per project;
  one agent of each type at a time. Zero race conditions, traded for raw
  throughput.
- **Six task states, no REVIEW.** BACKLOG → WAITING → WORKING →
  DONE / BLOCKED / WONT-DO. There is deliberately no "needs human review"
  state — accumulating review items is the death of autonomy. If a human is
  needed, the task is BLOCKED with a clear reason.
- **TESTER reports, CM decides.** TESTER categorizes findings (pass, gap,
  bug); CM ships unless verification could not complete. Found bugs become
  intake files for the next iteration. An 85% solution that keeps moving
  beats a 100% solution that stalls.
- **The intake file encodes the human's decision; agents execute it.**
  Requirements, bug, and priority files are where the operator constrains
  the work — file allowlists, must-not-modify guards, acceptance tripwires.
  That is how you steer an autonomous chain without sitting in the loop.
- **Single source of truth configuration.** Everything resolves through one
  validated loader with a fixed precedence (environment > config file >
  default), failing loudly on missing required keys.

## Good citizenship on your host

Agent work is fully isolated from your live resources:

- **Per-task git worktrees** — CODER and WRITER work in disposable worktrees
  off the RC branch; TESTER verifies in a detached-HEAD worktree. The
  canonical checkout is never an agent's working directory.
- **Configured temp root** — all transient output goes under a temp
  directory you configure (`kanban.cfg [paths] tmp_root` + `tmp_subdir`);
  `TMPDIR` is exported to match, so standard tooling lands there too.
- **Post-task pollution sweep** — after every task, the framework diffs the
  canonical tree's git state; anything an agent left behind is quarantined
  (never deleted) and logged. The invariant: only CM's managed release
  operations change the canonical tree.
- **Local-only git for non-CM agents** — CODER/WRITER branch, commit, and
  merge locally; they never fetch or push. Origin is touched at exactly two
  moments per release, both by CM.

## Workflow types

A workflow type is a YAML definition — adding one is data, not code.

| Workflow | Deliverable |
|---|---|
| `release` | Software, through the full git RC lifecycle, ending in a pushed tag |
| `document` | Prose, published as versioned artifacts under the project (`artifacts/v<semver>-<name>.<ext>`, every version kept) |

## Multi-project

One installation hosts multiple independent projects, each with its own
state, config, workflow type, and branch namespace. A per-project
`branch_prefix` (e.g. `ai_`) lets the kanban manage a repository whose human
`main` must stay untouched — the framework works entirely in `ai_main` /
`ai_develop` / prefixed tags.

## Quickstart

**Fastest first run: the demos.** The repo ships two complete guided
walkthroughs that end in a real release —
[demos/chomp-man-demo/README.md](team/demos/chomp-man-demo/README.md)
(a small game shipped through the `release` workflow, git tags and all)
and [demos/three-bears-demo/README.md](team/demos/three-bears-demo/README.md)
(a story shipped through the `document` workflow). If you're evaluating
the framework, run a demo before writing your own requirements — every
file you need is included.

Prerequisites: Linux host, bash 5+, git, Python 3.12, tmux (for the
dashboard), cron (or the bundled pseudocron for cron-less hosts), and the
Claude CLI authenticated (OAuth by default; API-key mode available via
`kanban.cfg [providers] ai_auth_mode`). Python dependencies are declared in
`requirements.txt` (runtime: PyYAML) and `requirements-test.txt` (adds pytest
and pytest-cov for the suites and coverage reporting) — install per the
Quickstart below.

```bash
git clone <this-repo> ~/develop/pgai-agent-kanban
cd ~/develop/pgai-agent-kanban
./install.sh                  # installs to ~/pgai_agent_kanban by default
                              # fresh installs register ZERO projects

# Install Python dependencies (one time, into the Python the agents run under;
# drop --break-system-packages if that Python is a venv).
# Runtime only:                pip install -r requirements.txt --break-system-packages
# Runtime + test toolchain:    pip install -r requirements-test.txt --break-system-packages
#   The test file adds pytest (to run the suites) and pytest-cov (for coverage
#   in the TESTER report). install.sh's preflight warns if any are missing.

# Register a project (its repo, dev tree, and workflow type)
~/pgai_agent_kanban/scripts/create-project.sh --project my-app \
    --workflow-type release \
    --dev-tree ~/develop/my-app --git-repo <repo-url>

# One-time git setup for release-workflow projects (pushes the base branches)
~/pgai_agent_kanban/scripts/init-project-git-repo.sh --project my-app

# Drop work and let discovery find it (file named vX.Y.0-<slug>.md —
# see the demo intake files or templates/agent/REQUIREMENTS-TEMPLATE.md)
~/pgai_agent_kanban/scripts/intake.sh --project my-app v0.1.0-my-feature.md

# Watch it happen
~/pgai_agent_kanban/scripts/dashboard/create.sh
```

**What to expect after intake:** nothing visible happens until the next
wake tick — on the default small tier that can be several minutes; an
idle dashboard right after intake is the schedule, not a failure. Then PM
decomposes the document into tasks and the pipeline runs unattended. A
small release (like the demos' bootstrap) ships in a couple of hours of
wall-clock time for a few dollars in tokens; the dashboard's cost pane
and `scripts/cost-report.sh --project <name>` show the real spend as it
accrues.

To have the kanban manage its own source (the self-build pattern),
register the framework's repository the same way you would any other
project — there is no special flag, no special mode:

```bash
~/pgai_agent_kanban/scripts/create-project.sh --project pgai-agent-kanban \
    --dev-tree ~/develop/pgai-agent-kanban \
    --git-repo <your fork or clone URL>
```

The installer offers tiered cron schedules; review
`scripts/cron-suggested.txt` if you prefer to install them yourself.
Upgrades (`scripts/upgrade.sh`) preserve your configuration, project state,
and bug history, and create a backup tarball first.

## Feeding the system

Three intake types, all plain Markdown files, all discovered automatically:

| Intake | Filename | Lands in | Versioning |
|---|---|---|---|
| Bug | `BUG-0042-short-description.md` | `projects/<name>/bugs/` | bundled into a patch release |
| Priority | `PRIORITY-0007-short-name.md` | `projects/<name>/priority/` | bundled into a patch release |
| Requirements | `v1.4.0-feature-name-20260610.md` | `projects/<name>/requirements/` | declares its own target version |

A requirements document carries control fields (`## Target Version`,
`## Workflow Type`, `## Test Required`, `## Source Branch`,
`## Human Approval Required`), goals, deliverables, and testable acceptance
criteria. The discovery pipeline processes bugs first, then priorities, then
requirements — exactly one action per iteration, blocked entirely while a
release candidate is in flight.

## Operating it

- **Dashboard** — a tmux surface showing every project's queues, agent
  activity, progress, HALT state, and next cron firings, side by side.
- **HALT controls** — `touch $KANBAN_ROOT/HALT` stops everything;
  `projects/<name>/HALT` stops one project; a `HALT-AFTER` token (e.g.
  `rc:v1.4.0`) drains the current release to completion and then halts —
  the timing-agnostic way to say "stop after this one ships."
- **Cost visibility** — every agent invocation records provider, model, and
  token counts; rollups per day, per release, per agent, per provider.
  `scripts/cost-report.sh` answers "what did today cost" with real numbers.
- **Per-project release hooks** — drop scripts in `projects/<name>/hooks/`
  (`cm-release-pre-squash.sh`, `cm-release-pre-tag.sh`,
  `cm-release-post-tag.sh`) and CM runs them at the right moments: bump a
  `pyproject.toml`, regenerate a TOC, whatever your deliverable needs.

## What this is not

- Not a SaaS — it runs on your VPS, with your data.
- Not multi-tenant — one operator per install.
- Not no-code — you write requirements docs and YAML, and tune role files.
- Not infinitely scalable — single-threaded per repository, on purpose.
- Not finished — it is a framework that improves with use; agent reasoning
  traces accumulate into the evidence that refines the role files.

## Economics

A small VPS plus LLM API tokens as the only variable cost. At an
active shipping pace, expect single-digit to low-double-digit dollars per
day — and the per-provider cost capture means a provider switch is both a
one-line config change and an immediately measurable experiment.

## Documentation

| Page | What it covers |
|---|---|
| [HOW_TO.md](HOW_TO.md) | Install, configure, register projects, daily operation |
| [ARCHITECTURE.md](ARCHITECTURE.md) | The system model and the architectural contract |
| [ROADMAP.md](ROADMAP.md) | Where the project is headed, version by version |
| [docs/operator-commands.md](docs/operator-commands.md) | Every operator command, flags, and examples |
| [docs/public-contract.md](docs/public-contract.md) | What you can depend on across 1.x |
| [docs/pseudocron.md](docs/pseudocron.md) | Scheduling without cron (containers) |
| [docs/dashboard.md](docs/dashboard.md), [docs/DASHBOARD-PANES.md](docs/DASHBOARD-PANES.md) | The tmux dashboard |
| [docs/projects-cfg.md](docs/projects-cfg.md) | The project registry format |
| [docs/operator-troubleshooting.md](docs/operator-troubleshooting.md) | When something looks wrong |
| [docs/quarantine-recovery.md](docs/quarantine-recovery.md) | Rejected-intake recovery |
| [docs/disk-hygiene.md](docs/disk-hygiene.md) | Log and artifact housekeeping |
| [docs/codex-known-issues.md](docs/codex-known-issues.md) | The experimental Codex lane |
| [docs/hybrid-shop-setup.md](docs/hybrid-shop-setup.md) | Mixed human+agent development |
| [demos/chomp-man-demo/README.md](team/demos/chomp-man-demo/README.md) | Guided release-workflow walkthrough |
| [demos/three-bears-demo/README.md](team/demos/three-bears-demo/README.md) | Guided document-workflow walkthrough |

Inside the install: `SOP.md` (operational procedures), `roles/` (per-agent
specifications), `workflows/` (workflow type definitions).

---

*The framework that builds itself can build for you.*

See CHANGELOG.md for release history.
