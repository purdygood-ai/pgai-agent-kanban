# Coding Standards

This document is the authoritative list of coding standards for the
shop. Every code-producing role — human or AI agent — must follow it,
in the kanban itself and in every project the kanban builds. The
directives are written in imperative voice. Each one is followed by a
single-line rationale naming the failure mode it prevents.

If a specific requirement says otherwise, the requirement wins for
that ticket and the deviation is documented in the ticket. Silence
means these directives apply.

## Directives

1. **Split code by concern; one implementation per operation.**
   Duplicated implementations of the same operation drift out of sync
   and turn every bug into two bugs.

2. **Keep surfaces thin.** CLIs, dashboards, and API handlers are
   adapters over shared logic — they parse input, call into the core,
   and format output; they must not carry business rules of their own.
   Business rules embedded in a surface cannot be reused by the next
   surface without being copied.

3. **Put shared code in a `lib/` package and import it; never copy
   it.** Copy-paste sharing forks behavior the first time either copy
   changes and there is no compiler or test that will tell you.

4. **Write shared code generic enough that a future REST API (or
   another surface) can consume it unchanged.** Shared code shaped
   around one caller becomes private code the moment a second caller
   needs it, and the next surface pays the cost of extracting it.

5. **No hardcoded operational values.** Anything an operator might
   change lives in a config file with a commented `_example` companion;
   no hardcoded paths; missing required config fails loud and names the
   key. Hardcoded values hide the moving parts and make every
   deployment a code edit.

6. **Every script and CLI entry point ships a `--help` that describes
   every argument it accepts.** `--help` is the contract between the
   operator and the tool; a missing or stale `--help` is a silent lie
   to the next person who runs it.

7. **Scaffolding and documentation ship with the feature, in the same
   RC.** Config examples, README sections, and generators land in the
   same release as the code they describe. A feature that ships
   without its scaffolding and docs is a feature the next operator
   cannot adopt.

8. **Comments, docstrings, help text, log lines, and output strings
   describe behavior, never process history.** Do not cite bug IDs,
   ticket IDs, requirement versions, or internal framework versions
   in the code or its runtime output. Exceptions carry over from prior
   practice: format or usage examples that use bug- or version-shaped
   values, skip annotations that cite an OPEN follow-up bug, and
   references to EXTERNAL constraints such as upstream issues, CVEs,
   or RFCs. Git history and task state hold the "why"; the code holds
   the meaning, and process residue in shipped code pollutes every
   downstream reader's context.

9. **Any REST API serves interactive contract docs (Swagger, OpenAPI,
   or equivalent) and a versioned contract file.** Without published,
   pinnable contract docs, every client integrates by reading server
   source, and every server change is a silent break.

10. **Default stack: Python 3.12+ for logic, bash for orchestration.**
    Deviate only when the requirement says otherwise and name the
    reason in the ticket. A consistent default stack is what lets one
    reader debug code written by another.

11. **Ship Docker and compose deployment artifacts where
    containerization makes operational sense, to the fail-loud
    entrypoint standard.** Entrypoints that swallow startup failures
    turn a broken deployment into a silently degraded one — fail loud
    at start so the operator sees the problem before traffic does.
