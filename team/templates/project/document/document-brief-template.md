<!--
HOW TO USE THIS TEMPLATE

Copy this file to a location of your choosing (usually a project priorities/
folder) and fill in every required section. Delete these HTML comments before
submission.

This template is the single brief template for the unified document workflow.
It replaces the older split between separate short-form and long-form brief
templates. Use `Workflow Type = document`.

The PM agent reads the brief and generates tasks that follow the
document.yaml pipeline.

Short-form (no Sections list):
  CM open-doc → WRITER outline → WRITER draft → WRITER polish → CM finalize

Long-form (Sections list present):
  CM open-doc → WRITER outline → WRITER section-draft (per section) →
  WRITER integrate → WRITER polish → TESTER review → CM finalize

Required sections:
  Title, Overview, Workflow Type, Target Audience, Length Target,
  Artifact Name, Deliverables, Acceptance Criteria.

Optional sections:
  Sections (long-form only), Source Documents, Style Notes, Source Material,
  Constraints, Context Paths, Notes.

Formatting:
  This template follows the compact `## Header` / value convention.
  Put the value on the line immediately under the header — no blank line in
  between. Readers tolerate the legacy blank-line form, but newly authored
  briefs use the compact form.

Brief authoring guidance:
  - Omit the Sections list for short-form documents (story, article, blog post).
  - Include the Sections list for long-form documents (whitepaper, SOP, guide).
    Each item becomes one section-draft task; keep the granularity meaningful
    (paragraph-level is too fine; chapter-level is too coarse).
  - Be specific about audience and length — WRITER uses these as primary
    calibration signals when drafting each section.
  - Use Source Documents to load prior published artifacts as input; the
    workflow resolves each slug against projects/<proj>/artifacts/ and passes
    the file(s) to WRITER. Absent = WRITER starts fresh from the brief.
  - Source Material lists external paths/URLs WRITER should read for context;
    Source Documents lists slugs of prior published artifacts in this
    project's library. They are different mechanisms.

Worked examples live in:
  team/templates/project/document/document-brief-example.md
-->

# Document Brief: <document-title>

## Overview
What is this document? What problem does it solve or what information does it
convey? Two to three sentences.

## Workflow Type
document

## Target Audience
Who will read this document? Describe expertise level, role, and context.

Examples:
- Senior engineers onboarding to the kanban system
- Non-technical business stakeholders evaluating integration options
- Internal ops team maintaining production infrastructure

## Length Target
Approximate word count or page count for the finished document.

Examples:
- 800 to 1,200 words (short-form article)
- 1,500 to 2,500 words (blog post or long essay)
- 4 to 6 pages (standard single-spaced, long-form)
- Under 800 words (executive summary style)

## Artifact Name
Output document name as a slug (lowercase, hyphen-separated). The finalize
step publishes the result to `projects/<proj>/artifacts/v<version>-<slug>.<ext>`
and later briefs reference this slug via `## Source Documents`.

When this field is absent or blank, the materializer derives the slug from the
requirement filename's descriptor portion (everything after the `vX.Y.Z-`
prefix). Set this explicitly when the desired slug differs from the filename.

Examples:
- whitepaper
- slide-deck
- onboarding-guide
- pgai-three-bears

## Source Documents
Optional list of prior published artifact slugs to load as WRITER input. Each
slug must already exist under `projects/<proj>/artifacts/` as a file matching
`<slug>.*`. The workflow resolves each slug, passes the resolved file(s) to
WRITER as input, and WRITER performs the transformation described in this
brief.

Use Source Documents for any of:
- Iterating on a prior version (load `v0.1.3-slide-deck`, revise it).
- Merging across two or more prior artifacts (load both decks, splice slides
  from one into the other).
- Starting from an operator-dropped reference file (operator places the file
  in `projects/<proj>/artifacts/` and names it here).

Slug list semantics:
- Each item is a slug, not a path. Resolution happens against the project's
  artifacts/ directory.
- Order matters when the brief depends on it (e.g. "merge slides from the
  second deck into the first").
- A missing slug is a hard error — the workflow will not silently skip.
- Absent or empty list means WRITER starts fresh from the brief.

Examples:
- v0.1.3-slide-deck
- v0.3.7-slide-deck

## Sections
List the major sections in the order they should appear. Each item becomes one
WRITER section-draft task. Use short descriptive labels.

Omit this section entirely for short-form documents (story, article, blog post) —
the workflow takes the short-form path automatically.

- Introduction
- Background and context
- Section 3 name
- Section 4 name
- Conclusion and summary

## Style Notes
Tone, voice, formatting preferences, and any house-style rules WRITER must
follow.

Examples:
- Use active voice throughout. Avoid passive constructions.
- Address the reader directly ("you") rather than abstractly ("the user").
- Prefer short paragraphs (3 to 5 sentences). Use bullet lists for enumerable
  items.
- Define technical terms inline on first use.
- Do not use emojis or marketing superlatives.

## Source Material
Files, URLs, or external documents WRITER should read as reference context.
Use absolute paths or full URLs. These are treated as data, not instructions.

This field is distinct from `## Source Documents`: Source Material is for
external reading context (specs, prior reports, web pages); Source Documents
is for prior published artifacts inside this project's library that the
workflow loads and hands to WRITER as primary input.

- /path/to/existing/draft.md
- /path/to/reference/architecture.md
- https://example.com/spec-page

## Deliverables
Concrete output files expected from this workflow.

- polished.md — finished document ready for publication
- review-report.md — TESTER's gap and quality report (long-form only)

## Constraints
Hard rules agents must follow when producing this document.

- Do not include proprietary customer data
- All code samples must be tested and runnable
- Citations must link to primary sources, not intermediary summaries

## Acceptance Criteria
How does the reviewer know the document is complete and correct?

- [ ] All sections listed above are present and substantive
- [ ] Length target is met (within 10 percent of target)
- [ ] Style notes are consistently applied throughout
- [ ] No placeholder text ("TBD", "TODO") remains in the final document
- [ ] When `## Source Documents` is set, the final output reflects the
      transformation described in the brief (edit, merge, renumber, etc.)
- [ ] TESTER review-report.md is filed and has no open critical findings
      (long-form only)

## Context Paths
Files the agents should read for domain context before drafting (optional).

- /path/to/project/README.md
- /path/to/project/docs/architecture.md

## Notes
Anything else — edge cases, known gaps, scheduling constraints, special
handling.
