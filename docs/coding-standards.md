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

## Gates prescribe, not just diagnose

A deterministic gate that only reports what is wrong forces the reader to
rediscover the fix that was already knowable at the moment of failure.
Every deterministic gate — every lint, freshness check, pre-flight abort,
release-script guard, and pre-tag hook — MUST, on failure, prescribe its own
remedy, not merely diagnose. The test is concrete: a stranger reading only
the gate's failure output, with no other guidance, must be able to clear the
gate.

A "deterministic gate" is any check whose passing condition and whose fix are
both computable from the state the gate inspects. The comment-provenance lint
(`team/scripts/lint_comment_provenance.py`) is the seed pattern: on a finding
it prints the offending `file:line`, the exact token that tripped it, the
one-line rule it enforces, and the precise way to clear it. Generalize that
shape to every gate.

### The remedy block: three required parts

On failure a deterministic gate MUST print, in this order:

1. **Cause(s), each with its concrete location.** Name the exact path, line
   number, config key, or field that tripped the check. "Something is stale"
   is a diagnosis of nothing; "`CHANGELOG.md` is missing the `## v1.27.2`
   section" names the file and the field. List every cause when there is more
   than one — the reader fixes what they can see.

2. **A numbered fix block of verbatim-runnable commands.** The commands must
   be copy-paste runnable exactly as printed, in the order printed, with no
   editing required to run them. Number them so the reader follows a sequence,
   not a paragraph. End the block with the re-check command that proves the
   fix worked (typically re-running the gate).

3. **The one-line rule the gate enforces.** State the single standing
   invariant the gate exists to protect, in one sentence, so the reader learns
   *why* the gate fired and not only *how* to silence it. This is what stops
   the reader from defeating the gate instead of satisfying it.

### The house placeholder law

The fix commands carry no angle-bracket placeholders. Never print `<path>`,
`<version>`, `<repo-root>`, or any other bracketed stand-in for a value the
gate could resolve. Where a value is site-specific, the gate derives it at
detection time and prints the actual literal: the gate already knows the
offending path, the current version, the resolved repo root at the moment it
fails, so it emits them. A placeholder the gate could have resolved is a
defect in the gate, not a convenience for the reader.

The only slot that may remain textual is genuinely free-form input that only
a human can author — a justification string, a chosen name — which the gate
cannot know. Even then, print a concrete, filled-in example line the reader
can adapt, never a bare `<slot>`.

### Remedy text lives beside the check

The remedy text is maintained in the same file as the check it describes.
When the check moves, its message moves with it; when a reviewer changes the
check, the now-stale remedy is in the same diff and is caught in review.
Remedy text stored apart from its check drifts silently until the day a reader
runs the printed commands and they no longer clear the gate.

### The non-deterministic exemption

Failures whose fix is not knowable at detection time are exempt from the
numbered command block. Real test-assertion failures, provider or API errors,
and network faults have no verbatim command that clears them — prescribing one
would be a lie.

They are NOT exempt from direction. Each such failure MUST still
name the next diagnostic step: point the reader at the specific evidence to
read next and what to look for in it — for example, "the failing test is
`test_release_gate.py::test_fidelity`; read the assertion tail printed above."
Exempt from prescription, never exempt from telling the reader where to look
next.

### The shape, generically

Gate-specific command text belongs beside each gate, not here. The generic
shape a compliant remedy block fills is:

```
FAIL: <one-line statement of what failed>

Cause(s):
  - <derived path/line/field 1> — <what is wrong with it>
  - <derived path/line/field 2> — <what is wrong with it>

To fix:
  1. <verbatim command, site-specific values already substituted>
  2. <verbatim command>
  3. <re-run the gate to confirm>

Rule: <the one-line invariant this gate enforces>
```

The angle brackets above mark the slots a gate author fills; a compliant gate
never prints them at runtime — by the placeholder law it prints the derived
literals in their place.
