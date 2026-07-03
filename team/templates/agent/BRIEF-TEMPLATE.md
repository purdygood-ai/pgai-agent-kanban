# <title: short descriptive name for this work>

<!-- Replace the title above with a short, descriptive name — e.g. "Add webhook support for task events" -->

## Goal

<!-- Required. 1-3 sentences describing what you want to achieve and why it matters.
     Focus on the outcome, not the implementation.
     Example: "Enable external services to receive real-time task state changes
     without polling the kanban API." -->

## Target Version

<!-- Required. The semver version this work targets, in vX.Y.Z format.
     Example: v0.7.0

     Increment guidance:
       PATCH (vX.Y.Z+1) — bug fixes, doc corrections, minor internal refactors
       MINOR (vX.Y+1.0) — new features that are backward compatible
       MAJOR (vX+1.0.0) — breaking changes, removed APIs, incompatible schema changes -->

vX.Y.Z

## Version Bump Rationale

<!-- Required. 1-2 sentences explaining WHY this version increment is correct.
     Tie it back to the increment guidance above.
     Example: "This is a MINOR bump because it adds a new subagent without
     removing or changing any existing interface." -->

## Constraints

<!-- Required. Hard rules the agents must follow while executing this work.
     List one constraint per bullet. Be specific — vague constraints are ignored.

     Examples:
       - No external API calls without human approval
       - Must pass ruff linting with zero errors
       - Feature branches must not be pushed to origin
       - All new code must have corresponding pytest tests -->

-

## Human Approval Required

<!--
Controls whether the PM agent injects a HUMAN-APPROVE gate task into the
generated plan before the CM release task.

Valid values:
  auto      — No HUMAN-APPROVE task is injected. The release proceeds
               automatically once all feature tasks are complete.
               This is the default.

  required  — A HUMAN-APPROVE task is injected between the final feature
               task and the CM release task. A human must manually advance
               the gate before the release proceeds.

Leave blank or omit to accept the default (auto).
-->

auto

## Model Overrides

<!-- Optional. Use this section to request that specific tasks use a particular
     model instead of the default assigned by the subagent.

     Format: one hint per line, in plain English. The PO agent will copy these
     hints verbatim into the requirements doc. The PM agent will translate them
     into model_override fields on individual tasks in the plan JSON.

     Recognized model values:
       opus              (alias for claude-opus-4-7)
       sonnet            (alias for claude-sonnet-4-6)
       haiku             (alias for claude-haiku-4-5)
       claude-opus-4-7   (full model ID)
       claude-sonnet-4-6 (full model ID)
       claude-haiku-4-5  (full model ID)

     Examples:
       - The scaffolding task should use haiku
       - Task writing the migration script should use opus
       - All documentation tasks should use sonnet
       - The integration test task should use claude-opus-4-7

     If this section is omitted or blank, all tasks use their subagent defaults.
-->

## Context

<!-- Required. Anything the PM agent or coder agents need to understand the domain.

     Useful things to include:
       - Paths to existing files that are relevant (e.g. /opt/pgai/team/pm-agent/README.md)
       - Current system behavior the work changes or extends
       - Prior decisions or rejected approaches
       - External docs or specs that define required behavior

     If there is no special context, write "None." -->

## Notes

<!-- Optional. Edge cases, preferences, warnings, or anything that doesn't fit above.
     This section is for human-to-agent commentary that doesn't belong in a formal field.
     Delete this section if you have nothing to add. -->
