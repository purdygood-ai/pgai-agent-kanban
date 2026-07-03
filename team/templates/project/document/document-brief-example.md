<!--
WORKED EXAMPLES — DOCUMENT BRIEF TEMPLATE

This file holds worked examples for the unified document brief template at
team/templates/project/document/document-brief-template.md. It is not a template
itself — do not copy this whole file. Copy the template, fill it in, and use
these examples as references for the optional `## Source Documents` field.

Each example below is a complete brief that the PM agent could materialize
without further guidance. The first example demonstrates a single-source edit
(load one prior artifact, transform it). The second demonstrates a
multi-source merge (load two prior artifacts, splice content across them).

All headers follow the compact `## Header` / value convention:
the value sits on the line directly under its header, no blank line in
between.
-->

# Example 1 — Single-source edit (slide deck trim)

Take the previously published `v0.1.3-slide-deck`, drop the last three
slides, and republish as a shorter version-bumped deck. One source document;
one transformation.

```
# Document Brief: pgai-agent-kanban — slide deck (trimmed)

## Overview
Trim the existing v0.1.3 slide deck to a tighter version for a 20-minute
talk. Remove the final three slides (the deep-dive appendix) so the deck
ends on the summary slide. Publish as a new versioned artifact.

## Workflow Type
document

## Target Audience
Conference attendees who are familiar with agent systems but not with this
project specifically. Talk length is 20 minutes plus 5 minutes of Q&A.

## Length Target
12 slides (down from 15 in the source deck).

## Artifact Name
slide-deck

## Source Documents
- v0.1.3-slide-deck

## Style Notes
- Preserve the original deck's slide headings and bullet phrasing verbatim
  except where trimming forces a transition rewrite on the new closing slide.
- Keep the summary slide as the final slide. Add a one-sentence closing
  line that signals the deck is ending (the original ended on the appendix).
- Do not change slide numbering of retained slides; the closing slide will
  pick up the next sequential number from the kept slides only.

## Deliverables
- polished.md — finished trimmed deck, ready for publication.

## Constraints
- Do not edit retained slides beyond the closing-line rewrite above.
- Do not add new content slides; the trim is removal-only plus the closing
  transition.

## Acceptance Criteria
- [ ] Output is exactly 12 slides.
- [ ] Slides 1 through 11 match v0.1.3-slide-deck slides 1 through 11
      verbatim (headings, bullets, ordering).
- [ ] Slide 12 is the original summary slide with a single added closing
      line.
- [ ] No appendix slides (originally slides 13, 14, 15) appear in the
      output.

## Context Paths
- projects/pgai-agent-kanban/README.md

## Notes
The next iteration may add a different appendix tailored to the venue;
do not pre-empt that here. This brief is removal-only.
```

# Example 2 — Multi-source merge (slide-deck splice)

Take the same `v0.1.3-slide-deck` and splice three slides from
`v0.3.7-slide-deck` (slides 5, 8, and 9 of the newer deck) into the middle
of it, after the original deck's slide 5. Renumber the result and republish.

```
# Document Brief: pgai-agent-kanban — slide deck (merged)

## Overview
Produce a merged slide deck that takes the v0.1.3 deck as the base and
inserts three updated slides from the v0.3.7 deck immediately after slide
5 of the base. The inserted slides are the OVERWATCH-monitoring slides
(numbers 5, 8, and 9 in the v0.3.7 deck) — they did not exist in the
v0.1.3 deck and are needed for an upcoming venue that has asked specifically
about self-monitoring.

## Workflow Type
document

## Target Audience
Conference attendees with operations and reliability backgrounds, evaluating
whether to adopt an agent-based pipeline in production. Talk length is 30
minutes including Q&A.

## Length Target
18 slides (15 from v0.1.3-slide-deck + 3 from v0.3.7-slide-deck).

## Artifact Name
slide-deck

## Source Documents
- v0.1.3-slide-deck
- v0.3.7-slide-deck

## Style Notes
- Use the v0.1.3 deck as the base; its slide headings, body styling, and
  bullet phrasing are the canonical voice for this output.
- The three inserted slides (v0.3.7 slides 5, 8, 9) keep their headings and
  body content unchanged, but their footer/numbering must be normalized to
  the merged deck's scheme.
- Renumber every slide in the merged deck sequentially starting at 1. Do
  not preserve the original numbering of either source.
- If either source uses a different running footer or speaker-notes
  convention, normalize to the v0.1.3 convention.

## Deliverables
- polished.md — finished merged deck, ready for publication.

## Constraints
- Insert the three v0.3.7 slides as a contiguous block immediately after
  v0.1.3 slide 5. Do not interleave them elsewhere.
- Do not edit content of the inserted slides beyond the footer/numbering
  normalization above.
- Do not add new content slides beyond the three named insertions.

## Acceptance Criteria
- [ ] Output is exactly 18 slides.
- [ ] Slides 1 through 5 match v0.1.3-slide-deck slides 1 through 5
      (headings, body, ordering).
- [ ] Slides 6, 7, 8 are v0.3.7-slide-deck slides 5, 8, 9 in that order,
      content-preserved with normalized footers.
- [ ] Slides 9 through 18 match v0.1.3-slide-deck slides 6 through 15
      (headings, body, ordering).
- [ ] Slide numbering is sequential 1 to 18 across the entire output;
      no gaps, no duplicates, no original-numbering remnants.

## Context Paths
- projects/pgai-agent-kanban/README.md

## Notes
This is a one-off merge for a specific venue. The next general-purpose
release of the deck should start fresh from a newer base; do not treat this
merged output as the new canonical deck.
```

# Notes on Source Documents semantics

Both examples illustrate the same generic mechanism. The framework's job for
Source Documents is narrow: resolve each named slug to a file under
`projects/<proj>/artifacts/` and hand the file(s) to WRITER as input. The
agent's intelligence does the transformation — edit, splice, merge, trim,
renumber, rewrite — driven by the natural-language brief.

A brief with one Source Documents entry is a single-source edit. A brief
with two or more is a multi-source merge. A brief with no Source Documents
field is a start-fresh document. The pipeline shape is the same in all
three cases; only the inputs differ.

Slugs in `## Source Documents` are the same slugs that previous releases
chose as their `## Artifact Name`. That is the contract: today's `Artifact
Name` becomes tomorrow's `Source Documents` entry, and the artifacts/
directory is the library that holds the published outputs across versions.
