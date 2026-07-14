# pgai-agent-kanban — Roadmap

**Public launch:** launched at v1.23.0.
**Companions:** `ARCHITECTURE.md` for the system model,
`docs/operator-commands.md` for the command surface,
`docs/public-contract.md` for what you can depend on.

This roadmap is theme-organized, not version-numbered. Public version
numbers skip private releases; numbered promises here would age
badly and misrepresent the pace of work. Each theme is a direction the
project is actively steering toward; the requirements-layer documents
that ride each RC name the concrete work.

---

## Near-term themes

Work already in view, driven by field feedback from the current
release line.

- **Operator REST API graduating from experimental to GA.** The API
  ships experimental today (full automated coverage; contract published
  as ICD 1.1.0; operator field-testing in progress; surfaces may be
  refined). The graduation criteria are cold-run stability on
  first-time operators, contract-freshness gates staying green across
  several releases, and the browser UI consuming the API in real use
  without operator hand-holding. When those hold, the "experimental"
  label comes off and the API joins the public-contract set.
- **Provider hardening beyond the primary lane.** Claude is the
  supported production provider; the Codex/OpenAI lane ships
  experimental and the Gemini lane is scaffolded and unexercised.
  The near-term goal is a second production-grade lane: dispatch and
  patch-application robustness under a second provider, pricing-table
  completeness so cost reporting is accurate everywhere, credential
  switching without operator babysitting, and at least one full
  self-build release under a non-Claude provider as the acceptance bar.
  Once two providers are production-grade, per-agent provider selection
  (one provider for TESTER, another for CODER, chosen on the cost and
  quality data the framework already collects) becomes a
  configuration feature.
- **Reader-facing polish across the public-launch surface.** The
  cold-reader path — README, HOW_TO, quickref, demos, the operator
  checkpoints — is exercised by strangers as the project promotes to
  a public release. Every rendered command must execute against a
  fixture install; every "see X" must resolve; every era-stale
  description must go. This is a permanent theme, not a one-off
  cleanup — every RC that touches a public surface earns a light pass.

## Mid-term themes

Work whose value is clear but whose shape depends on how the
near-term themes settle.

- **OVERWATCH remit expansion within the reversibility line.** The
  self-monitor's remit stays firmly on the deterministic, reversible
  side of the line (undoes interrupted operations; surfaces
  content-decision concerns as BLOCKED tasks; never resolves content
  conflicts itself). Within that line there is room to grow: broader
  transient-error re-labeling, richer blocker-ledger analysis, and
  operator-facing summaries of recurring failure modes. The line is
  fixed; what fits inside it is the mid-term work.
- **Capability-driven agent specialization.** The workflow-type
  plugin surface already lets a plugin declare a bespoke agent
  roster. The mid-term extension is agent capability packs — a set of
  named capabilities a role file may declare, so a project's
  requirements layer can express "this ticket needs a CODER with
  capability X" and the dispatcher routes accordingly. The framework
  remains capability-queried, never named-check.
- **Hybrid-shop and multi-operator patterns.** Today's supported
  model is one operator per install. The mid-term theme is
  documenting and stabilizing patterns where humans and agents share
  a repository (the hybrid-shop guide is a starting point) and where
  more than one operator drives a single install through separate
  projects. Neither turns the framework into a SaaS; both make the
  single-operator floor more expressive.

## Exploratory themes

Directions worth naming so the design does not close doors to them,
but whose commitment level is deliberately low.

- **Orchestrating specialized external tools.** The kanban as a
  content-production orchestrator: each specialized tool (video
  generation, publishing, structured data pipelines, and others)
  exposes the same three-audience interface — REST API, MCP adapter,
  CLI — and the kanban gains workflow types that call them. An
  operator drops a brief; the deliverable might be a tagged software
  release, a document, or a rendered artifact produced by an external
  tool the agents drove through its API. The workflow-type plugin
  surface is designed so this is possible without engine edits; the
  exploratory work is proving it end-to-end on a real tool.
- **Hosted / multi-tenant deployments.** Considered only if external
  users emerge who need them. The single-operator, own-hardware model
  is the default indefinitely. Naming this here is a promise not to
  refactor toward multi-tenancy speculatively; the current
  architecture accommodates it if the requirement ever lands.

---

## Principles that hold across every theme

- Files on disk are the source of truth; git is the safety net.
- Single-threaded per repository; stacking requirements is the
  intended use, made safe by the Active-RC gate.
- TESTER reports, CM decides; the chain ships and iterates — a known
  imperfection files a bug, it does not wedge the queue.
- No default project, no silent fallbacks: every resolution is
  explicit or fails loudly.
- One implementation per operation; new surfaces are thin adapters.
- Backward compatibility is owed from the first public release
  onward: breaking changes ship with migration scripts.

---

*This roadmap is intentionally lossy on detail. Specifics live in
per-release requirements documents. The roadmap encodes direction
and shape; concrete acceptance criteria are decided at the
requirements layer.*
