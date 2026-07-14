# PGAI Agent Kanban

**A software shop in a box.** It ships its own releases, watches itself
through OVERWATCH, serves itself through an operator REST API and a
browser UI, and extends without repo access through workflow-type
plugins dropped into a directory.

Drop a requirements document. Walk away. Come back to a tagged release.

The kanban orchestrates specialized AI agents through a single-threaded,
file-driven pipeline that decomposes structured work — software
releases, prose documents, whatever a plugin declares — into discrete,
verifiable tasks, executes them, verifies the result, and ships it. It
runs unattended on a single VPS, driven by cron.

---

## Feature census

One sentence, one link per row. Depth lives in the linked document.

| Capability | What it is | Where to look |
|---|---|---|
| **Pipeline** | Six specialized agents (PO, PM, CODER, WRITER, TESTER, CM) advance one release candidate at a time per project through a file-driven, single-threaded pipeline. | [team/OVERVIEW.md](team/OVERVIEW.md) |
| **Workflow-type plugins** | Every workflow type is a directory under `workflows/` with a manifest, hooks, and an optional pipeline — the engine reads capabilities, never type names, and fails closed on unknown types. | [team/workflows/README.md](team/workflows/README.md) |
| **Operator REST API** (experimental — see [status](#operator-rest-api-and-browser-ui-status)) | A loopback-only FastAPI service (`scripts/api-server.sh`) that thin-adapts the operator command surface — every endpoint shells out to the one canonical script and returns a `{exit_code, stdout, stderr}` envelope. | [docs/operator-api.md](docs/operator-api.md) |
| **Structured reads** (experimental — see [status](#operator-rest-api-and-browser-ui-status)) | JSON-native reads (`/board`, `/projects/{name}`, `?format=json` on `/metrics` and `/costs`, `/logs/{kind}`, `/traces`) designed for the browser UI, coexisting with the ANSI-pane reads without disturbing them. | [docs/operator-api.md](docs/operator-api.md) |
| **OVERWATCH self-monitor** | A horizontal watchdog that runs an hourly deterministic sweep (Tier 1) and a daily LLM deep-clean (Tier 2), with an on-BLOCK trigger that wakes it within seconds of a fresh failure. | [team/roles/OVERWATCH.md](team/roles/OVERWATCH.md) |
| **Two-phase upgrade** | `upgrade.sh` runs as a small stable Phase-1 bootstrap in the installed tree, then execs into the dev-tree's own `upgrade.sh --phase2` for the real deposit — so upgrades honor the version being installed, not the version already installed. | [team/scripts/upgrade.sh](team/scripts/upgrade.sh) |
| **Provider abstraction** | Claude is the supported production provider; the Codex/OpenAI lane is exercised and ships experimental; the Gemini lane is scaffolded — switching providers is a `switch-provider.sh` call, not a refactor. | [docs/codex-known-issues.md](docs/codex-known-issues.md) |
| **Browser UI (sibling repo)** (experimental — see [status](#operator-rest-api-and-browser-ui-status)) | The kanban ships the API; a separate `pgai-agent-kanban-ui` repository ships the browser dashboard that consumes it — deliberate separation, not delayed. | [docs/operator-api.md](docs/operator-api.md) |

Every capability above is checkable against the tree of the release you
are reading. If a row's link is broken or its file is missing, that is
a bug, not a promise.

### Operator REST API and browser UI status

Experimental — full automated coverage; contract published (ICD 1.1.0);
operator field-testing in progress; surfaces may be refined.

This applies to every reader-facing mention of the REST API and the
sibling browser UI in this document, in [docs/operator-api.md](docs/operator-api.md),
and in the quickstart flows below. The underlying scripts the API
fronts are stable; the HTTP surface is the part still being field-tested.

---

## Prerequisites

- **tmux ≥ 3.1** — the dashboard uses features (popup panes, `-Z`
  zoom semantics) that are not present in older tmux builds. The
  chain is exercised regularly against tmux 3.4; anything from 3.1
  forward should work.
- **Python 3.9+**, `git`, `bash`, `curl` — standard shop tooling.
- **cron** — the pipeline is driven by cron ticks. Container hosts
  without cron can substitute `pseudocron.sh`; see
  [docs/pseudocron.md](docs/pseudocron.md).

## Five-minute demo spine

The fastest path from empty directory to a running kanban. Every
command is verbatim; adjust the URLs to your fork.

```bash
# 1. Clone and install (canonical default: $HOME/pgai_agent_kanban).
git clone <this-repo> ~/develop/pgai-agent-kanban
cd ~/develop/pgai-agent-kanban
./install.sh

# 2. Install Python deps (runtime = PyYAML; test toolchain adds pytest + pytest-cov).
#    Drop --break-system-packages if the target Python is a venv.
pip install -r requirements.txt --break-system-packages

# 3. Register a project (its repo, dev tree, and workflow type).
~/pgai_agent_kanban/scripts/create-project.sh --project my-app \
    --workflow-type release \
    --dev-tree ~/develop/my-app --git-repo <repo-url>

# 4. One-time git setup for release-workflow projects (pushes base branches).
~/pgai_agent_kanban/scripts/init-project-git-repo.sh --project my-app

# 5. Drop a requirements document and let discovery find it.
~/pgai_agent_kanban/scripts/intake.sh --project my-app v1.0.0-my-feature.md

# 6. Watch the tmux dashboard while cron drives the pipeline.
~/pgai_agent_kanban/scripts/dashboard/create.sh
```

**What to expect after intake.** Nothing visible happens until the next
wake tick; on the default schedule that can be several minutes, and an
idle dashboard immediately after intake is the schedule, not a failure.
Then PM decomposes the document into tasks and the chain runs
unattended. A small release (like the demos' bootstrap) ships in a
couple of hours of wall-clock time for a few dollars in tokens; the
dashboard's cost pane and `scripts/cost-report.sh --project <name>`
show the real spend as it accrues.

**Fastest first run: the demos.** The repo ships two complete guided
walkthroughs that end in a real release —
[team/demos/chomp-man-demo/README.md](team/demos/chomp-man-demo/README.md)
(a small game shipped through the `release` workflow, git tags and all)
and [team/demos/three-bears-demo/README.md](team/demos/three-bears-demo/README.md)
(a story shipped through the `document` workflow). Evaluating the
framework? Run a demo first — every file you need is included.

---

## API quickstart

The REST API is experimental — full automated coverage; contract
published (ICD 1.1.0); operator field-testing in progress; surfaces
may be refined. See [Operator REST API and browser UI status](#operator-rest-api-and-browser-ui-status)
above.

Three commands that reach a running server. Copy-paste; no edits.

```bash
# 1. Start the loopback-only API server.
~/pgai_agent_kanban/scripts/api-server.sh start

# 2. Read the unified kanban board (one JSON call, every project, every column).
curl -sS http://127.0.0.1:8300/board

# 3. Halt a project (an operate verb — mutations are POST).
curl -sS -X POST http://127.0.0.1:8300/operations/halt \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app"}'
```

Every operate endpoint returns the same envelope — the raw exit code,
stdout, and stderr from the underlying script:

```json
{
  "exit_code": 0,
  "stdout": "halt: HALT signal set for project my-app (…/projects/my-app/HALT)\n",
  "stderr": ""
}
```

HTTP status derives from `exit_code`: `200` on `0`, `500` when the
underlying script exits non-zero, `422` when required parameters are
missing or invalid. The API never re-interprets script output — a
warning to stderr with exit 0 is a warning in the envelope with HTTP
200. The interactive Swagger UI is at `http://127.0.0.1:8300/docs`.

Loopback-only is not a configuration option — the server refuses to
bind to a routable address. Reach it from your workstation with an SSH
tunnel or a SOCKS proxy; see [docs/operator-api.md](docs/operator-api.md)
for the full model.

---

## Feeding the system

Three intake types, all plain Markdown, all discovered automatically.

| Intake | Filename shape | Lands in | Versioning |
|---|---|---|---|
| Bug | `BUG-####-short-description.md` | `projects/<name>/bugs/` | bundled into the next patch release |
| Priority | `PRIORITY-####-short-name.md` | `projects/<name>/priority/` | bundled into the next patch release |
| Requirements | `vX.Y.Z-feature-name.md` | `projects/<name>/requirements/` | declares its own target version |

A requirements document carries control fields (`## Target Version`,
`## Workflow Type`, `## Test Required`, `## Source Branch`,
`## Human Approval Required`), goals, deliverables, and testable
acceptance criteria. Discovery processes bugs first, then priorities,
then requirements — exactly one action per iteration, blocked entirely
while a release candidate is in flight.

To have the kanban manage its own source (the self-build pattern),
register the framework's repository the same way you would any other
project — there is no special flag, no special mode:

```bash
~/pgai_agent_kanban/scripts/create-project.sh --project pgai-agent-kanban \
    --dev-tree ~/develop/pgai-agent-kanban \
    --git-repo <your fork or clone URL>
```

---

## Design principles

- **Files on disk are the source of truth.** No database. Git is the
  history and the safety net.
- **Single-threaded by design.** One release candidate at a time per
  project; one agent of each type at a time. Zero race conditions,
  traded for raw throughput.
- **Six task states, no REVIEW.** BACKLOG → WAITING → WORKING →
  DONE / BLOCKED / WONT-DO. There is deliberately no "needs human
  review" state — accumulating review items is the death of autonomy.
  If a human is needed, the task is BLOCKED with a clear reason.
- **TESTER reports, CM decides.** TESTER categorizes findings (pass,
  gap, bug); CM ships unless verification could not complete. Found
  bugs become intake files for the next iteration.
- **The intake file encodes the human's decision; agents execute it.**
  Requirements, bug, and priority files are where the operator
  constrains the work — file allowlists, must-not-modify guards,
  acceptance tripwires.
- **Per-task git worktrees.** CODER and WRITER work in disposable
  worktrees off the RC branch; TESTER verifies in a detached-HEAD
  worktree. The canonical checkout is never an agent's working
  directory.
- **Local-only git for non-CM agents.** Working agents branch, commit,
  and merge locally; they never fetch or push. Origin is touched at
  exactly two moments per release, both by CM.
- **Loopback-only API.** The API binds to `127.0.0.1` and refuses to
  bind anywhere else. SSH does the exposure.

---

## Operating it

- **Dashboard.** A tmux surface showing every project's queues, agent
  activity, progress, HALT state, and next cron firings, side by side.
- **HALT controls.** `touch $KANBAN_ROOT/HALT` stops everything;
  `projects/<name>/HALT` stops one project; a `HALT-AFTER` token drains
  the current release to completion and then halts — the timing-
  agnostic way to say "stop after this one ships."
- **Cost visibility.** Every agent invocation records provider, model,
  and token counts; rollups per day, per release, per agent, per
  provider. `scripts/cost-report.sh` answers "what did today cost"
  with real numbers.
- **Per-project release hooks.** Drop scripts in
  `projects/<name>/hooks/` (`cm-release-pre-squash.sh`,
  `cm-release-pre-tag.sh`, `cm-release-post-tag.sh`) and CM runs them
  at the right moments: bump a `pyproject.toml`, regenerate a TOC,
  whatever your deliverable needs.

---

## What this doesn't try to be

- **Not a SaaS.** It runs on your VPS, with your data. The API is
  loopback-only and there is no HTTP-layer authentication in this
  release — SSH is the authentication.
- **Not multi-tenant.** One operator per install. Any auth story is
  future work, not a shipping feature.
- **Not no-code.** You write requirements documents and YAML pipelines,
  and tune role files.
- **Not infinitely parallel.** Single-threaded per repository, on
  purpose. One RC at a time is the reliability mechanism.
- **Not TLS-terminated.** Traffic on the loopback socket doesn't leave
  the box; the SSH tunnel encrypts the wire.
- **Not the browser UI.** The UI ships as a sibling repository
  (`pgai-agent-kanban-ui`) that consumes the API; keeping them
  separate is deliberate — the transport is the contract.
- **Not finished.** It is a framework that improves with use; agent
  reasoning traces accumulate into evidence that refines the role
  files.

---

## Documentation

| Page | What it covers |
|---|---|
| [HOW_TO.md](HOW_TO.md) | Install, configure, register projects, daily operation |
| [ARCHITECTURE.md](ARCHITECTURE.md) | The system model and the architectural contract |
| [ROADMAP.md](ROADMAP.md) | Where the project is headed, version by version |
| [docs/operator-api.md](docs/operator-api.md) | The REST API (experimental — see [status](#operator-rest-api-and-browser-ui-status)), envelope, endpoints, and the loopback trust boundary |
| [docs/operator-commands.md](docs/operator-commands.md) | Every operator command, flags, and examples |
| [docs/public-contract.md](docs/public-contract.md) | What you can depend on across the 1.x line |
| [docs/pseudocron.md](docs/pseudocron.md) | Scheduling without cron (containers) |
| [docs/dashboard.md](docs/dashboard.md), [docs/DASHBOARD-PANES.md](docs/DASHBOARD-PANES.md) | The tmux dashboard |
| [docs/projects-cfg.md](docs/projects-cfg.md) | The project registry format |
| [docs/operator-troubleshooting.md](docs/operator-troubleshooting.md) | When something looks wrong |
| [docs/quarantine-recovery.md](docs/quarantine-recovery.md) | Rejected-intake recovery |
| [docs/disk-hygiene.md](docs/disk-hygiene.md) | Log and artifact housekeeping |
| [docs/codex-known-issues.md](docs/codex-known-issues.md) | The experimental Codex lane |
| [docs/hybrid-shop-setup.md](docs/hybrid-shop-setup.md) | Mixed human + agent development |
| [docs/creating-a-workflow.md](docs/creating-a-workflow.md) | Author a new workflow-type plugin end-to-end |
| [team/workflows/README.md](team/workflows/README.md) | The plugin surface — manifest, hooks, `pipeline.yaml` |
| [team/roles/OVERWATCH.md](team/roles/OVERWATCH.md) | The self-monitor's two tiers, on-BLOCK trigger, and whitelist |
| [team/demos/chomp-man-demo/README.md](team/demos/chomp-man-demo/README.md) | Guided release-workflow walkthrough |
| [team/demos/three-bears-demo/README.md](team/demos/three-bears-demo/README.md) | Guided document-workflow walkthrough |

Inside the install: `SOP.md` (operational procedures), `roles/`
(per-agent specifications), `workflows/` (workflow type definitions),
`docs/` (the full docs tree). Browse `docs/` directly for anything not
listed above.

---

*The framework that builds itself can build for you.*

See CHANGELOG.md for release history.
