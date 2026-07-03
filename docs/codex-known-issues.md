# Codex Lane — Known Issues

The Codex/OpenAI lane is exercised — a managed project has shipped
end-to-end on Codex with OAuth — and ships **experimental** in v1.0.0.
Claude remains the supported production provider. Before running the Codex
lane, know these rough edges:

- **Task dispatch.** The Codex CLI occasionally fails to launch or to
  return a parseable result for a dispatched task. The wake layer marks the
  task BLOCKED; a `reset` re-runs it cleanly. This is the most common
  failure and is recoverable, not destructive.
- **Patch application.** Applying large or complex diffs is less reliable
  than on the production lane. A task may leave partial work in its
  worktree; the worktree teardown contains it, and a reset re-derives the
  change from scratch.
- **Authentication switching.** Hot-switching between API-key and OAuth
  authentication requires operator attention. Verify the lane authenticates
  (run one small task and watch the wake log) before leaving it unattended.
- **Pricing coverage.** Cost reporting reads model prices from
  `token_pricing.json`. If the Codex models you run are not listed there,
  their cost reports as zero. Add rows for your models before relying on
  cost data.

**Recommendation:** exercise the Codex lane on a managed project first, and
do not run the self-build unattended on Codex. Switch providers with
`scripts/switch-provider.sh`; the `wake-claude` / `wake-codex` prefix in the
wake logs is ground truth for which lane is live.

Hardening the Codex lane to supported status is the v1.1.x theme — see
[ROADMAP.md](../ROADMAP.md).
