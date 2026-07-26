# pgai-agent-kanban — Roadmap

The platform is feature-complete for its core promise: requirements
in, verified releases out, with a tmux dashboard, a REST API, and
containerized deployment. Development now favors stability and
operator experience over new capability. This roadmap lists the
directions actively held open — deliberately short, deliberately
unversioned. Specifics are decided at the requirements layer when
work begins.

## TLS for the operator REST API — via reverse proxy

The API serves loopback HTTP by design. Sites that need HTTPS put a
reverse proxy in front (Caddy is the recommended zero-config choice:
automatic certificates, two-line Caddyfile). TLS termination is the
deployment's responsibility, not the framework's — native TLS in the
API server is not currently planned. A worked Caddy example ships in
the operator docs.

## OVERWATCH: from observer to block-resolver

Today the observer sweeps, heals reversible mechanics, and wakes on
any block. The direction: advisory block briefings first (the human
arrives to a prepared decision), then autonomous resolution of
known-shape release blocks — matrix-prescribed, fully reversible,
retry-capped, ledger-recorded, surfaced beside Approvals. Human
authority moves from prerequisite to oversight for the
deterministic subset, and remains prerequisite for everything else.

## Additional providers — on demonstrated need

Claude is the supported production provider. The Codex/OpenAI lane
ships experimental and is maintained as the contingency; further
lanes (Gemini and others are scaffolded) are added when need is
demonstrated, not speculatively. The production bar for any lane:
one full self-build release under that provider.

---

Compatibility is owed forward from the current public release;
breaking changes ship with explicit upgrade guidance in the release
notes. Everything else — principles, architecture, operating
practice — lives in ARCHITECTURE.md and HOW_TO.md.
