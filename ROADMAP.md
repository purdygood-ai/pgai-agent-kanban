# Roadmap

**Current public release: v2.42.0** — a generic software shop in a
box: six pipeline agents plus the OVERWATCH self-monitor, the Loop
doctrine and blocking-site census, quality findings filed as bugs
instead of stops, dual-channel errors, autonomous resolution of known
block shapes, pluggable workflow types, version-targeted install and
forward-only upgrade, and four runnable demos. The loopback-only REST
API ships experimental, with a published, versioned interface contract
that the separate browser UI repository consumes.

## Next — field-driven

The near roadmap is written by operation, not speculation: fixes and
polish land as patch releases when real use surfaces them. Candidate
directions, in no committed order: one-command acquisition from the
public repo (curl-able release listing and install-from-repo
wrappers), deeper workflow-type authoring guides, and
operator-experience polish from field reports.

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
