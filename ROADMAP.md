# Roadmap

**Current public release: v2.4.0** — the resilient-autonomy era: the
Loop doctrine and blocking-site census, quality findings as tickets
instead of stops, dual-channel errors, autonomous block resolution,
the REST control plane (ICD 1.3.0) with the browser operator surface,
pluggable workflow types, and version-targeted forward-only
deployment.

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
