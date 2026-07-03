# pgai-agent-kanban — Roadmap

**Status:** current as of v1.0.0.
**Companions:** `ARCHITECTURE.md` for the system model, `docs/operator-commands.md`
for the command surface, `docs/public-contract.md` for what you can depend on.

---

## Where v1.0.0 stands

v1.0.0 is the foundation release: a self-building, single-operator autonomous
task-decomposition framework, proven across hundreds of autonomous releases —
including every release of this repository itself and two managed demonstration
projects (a software project on the `release` workflow, a prose project on the
`document` workflow).

What ships in v1.0.0:

- Six specialized agent roles (PO, PM, CODER, WRITER, TESTER, CM) plus an
  installed-but-inert OVERWATCH observer
- Two workflow types: `release` (software, full git RC lifecycle) and
  `document` (versioned prose deliverables), with metrics parity
- Single-threaded, file-driven pipeline: bugs → priorities → requirements →
  tasks → tagged releases, one RC at a time per project
- Per-task git worktree isolation; local-only git for working agents; CM as
  the sole origin-toucher
- Provider abstraction: Claude is the supported production provider; the
  Codex/OpenAI lane is exercised and ships **experimental** (see
  KNOWN-ISSUES); the Gemini lane is scaffolded and unexercised
- Full per-task/per-day/per-RC cost capture and reporting
- cron and pseudocron scheduling at parity (bare hosts and containers)
- Operator command surface with a unified `--key` vocabulary, a tmux
  dashboard, and demo corpora for both workflow types

What v1.0.0 deliberately is not: a SaaS, multi-tenant, no-code, or
infinitely parallel. One operator, one VPS, one RC at a time per repo. Those
constraints are the reliability mechanism, not accidents.

---

## v1.0.x — stabilization

Patch releases only, driven by real use:

- Bug fixes surfaced by early operators and the framework's own TESTER
- Release-pipeline idempotency hardening (safe resume after operator-assisted
  completion of a release step)
- Documentation corrections as the stranger's path gets walked by strangers

No new features ride the 1.0.x line.

---

## v1.1.x — pluggable workflow types

Workflow types become plugins: a directory with a manifest and a set of hook
implementations (version resolution, git mode, finalization, agent roster,
rendering), discovered by scan rather than registered in code. The engine
queries capabilities, never type names, and fails closed on unknown types.

The litmus test ships with it: a testing-only workflow (read-only git, no
version semantics, report-only finalize) added as a pure new directory with
zero engine edits. If it forces an engine change, the interface is
incomplete and gets fixed before the feature is called done.

A scaffolding generator (`create_new_workflow`) lets the framework's own
agents build new workflow types task-by-task, fail-closed until the
contract test passes.

---

## v1.2.x — operator REST API and browser dashboard

A localhost-only REST API exposing the operator command surface — each
endpoint is a thin adapter over the one canonical operator script (the
script's flag list is the API's parameter list; reads are GET, mutations
are POST), so there is still exactly one implementation per operation. A
companion project, a static browser dashboard, consumes the API: the tmux
dashboard's information in a web page, same-machine access only (SSH
tunnel or SOCKS from elsewhere), authentication and TLS deferred until a
deployment needs them.

Design guardrails: the CLI never depends on the API being up; the API never
becomes a second scheduler — cron remains the sole driver of the autonomous
chain; endpoints propagate the scripts' own guards (refuse-on-ambiguity,
HALT-before-destructive) rather than re-implementing them. The later
shared-operations-library extraction slots underneath this API without
changing its surface.

---

## v1.3.x — provider hardening

Promote the Codex lane from experimental to supported:

- Dispatch and patch-application robustness on the OpenAI/Codex CLI
- Credential/auth switching without operator babysitting
- Pricing-table completeness so cost reporting is accurate per provider
- At least one full self-build release shipped under a second provider as the
  acceptance bar

Once two providers are production-grade, per-agent provider selection
(e.g. one provider for TESTER, another for CODER, chosen on cost/quality
data the framework already collects) becomes a configuration feature.

---

## v1.4.x — OVERWATCH reactivation

The post-task invariant observer comes back on, with a deliberately narrow
remit: deterministic, reversible, surface-don't-enforce checks.

- Transient provider-error detection and re-labeling (a momentary 5xx should
  read "safe to retry," not "needs human")
- Interrupted-operation git residue cleanup: abort incomplete merges, remove
  orphaned worktrees, reset to last-good, requeue the task — undo only,
  never forward content decisions
- A blocker ledger: structured, append-only record of every BLOCKED
  transition with the captured diagnostic

The scope boundary is firm: OVERWATCH undoes interrupted operations; it never
resolves content conflicts. That line is about reversibility under zero
supervision, not model capability.

---

## v2.x — orchestrating specialized tools

The longer trajectory: the kanban as a content-production orchestrator. Each
specialized tool (video generation, publishing, and others) exposes the same
three-audience interface — REST API, MCP adapter, CLI — and the kanban gains
workflow types that call them. An operator drops a brief; the deliverable
might be a tagged software release, a document, or a rendered artifact
produced by an external tool the agents drove through its API.

Multi-tenancy, authentication, and hosted deployment are considered only if
external users emerge who need them. The single-operator, own-hardware model
is the default indefinitely.

---

## Principles that hold across every version

- Files on disk are the source of truth; git is the safety net
- Single-threaded per repo; stacking requirements is the intended use, made
  safe by the Active-RC gate
- TESTER reports, CM decides; the chain ships and iterates — a known
  imperfection files a bug, it does not wedge the queue
- No default project, no silent fallbacks: every resolution is explicit or
  fails loudly
- One implementation per operation; new surfaces are thin adapters
- Backward compatibility is owed from first public release onward: breaking
  changes ship with migration scripts

---

*This roadmap is intentionally lossy on detail. Specifics live in per-release
requirements documents. The roadmap encodes direction and order; refinement
happens at the requirements layer.*
