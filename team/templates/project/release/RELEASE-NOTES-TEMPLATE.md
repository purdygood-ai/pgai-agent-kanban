<!--
GUIDE NOT GATE: This template is a guide, not a gate.
Agents must tolerate imperfect or missing sections.
Use the structure when it helps; skip sections that do not apply.

CHANGELOG.md IS NOT A WRITER DELIVERABLE
========================================
WRITER authors release-notes/vX.Y.Z.md ONLY. Never hand-edit CHANGELOG.md.
CHANGELOG.md is regenerated deterministically from this release-notes
file and the bug ledger by team/pgai_agent_kanban/cm/changelog_writer.py,
which CM runs only at release time (cm/release.sh Step 11b). A committed
CHANGELOG.md that is stale on an RC branch is expected; no ticket renders
it. Hand-editing CHANGELOG.md bypasses the changelog writer's internal-
identifier safety pass and trips the internal-bug-identifier unit test.
Write the notes; CM regenerates the changelog at release.

The release-notes body must describe every resolved item by SUBSTANCE —
what was wrong, drawn from the opening sentence of the bug's own Symptom
text — never by the bare internal ticket ID (BUG-NNNN, PRIORITY-NNNN).
A bug's Fix section is never a source: in a filed bug it proposes a
remedy before the work is done, often as a list of options, so it does
not say what shipped. The generated Bug Fixes section follows the same
rule: each bug's bullet reads "Fixed: " followed by that Symptom sentence.
There is no lint carve-out for this file: release-notes/*.md is scanned
by team/scripts/lint_hygiene.py's provenance class the same as production code.
When an item has already been disclosed, cite its public KI-N.N.N.N
identifier alongside the substance description — that public namespace
is unchanged by this rule and is exactly what a reader outside the
kanban can look up. The changelog writer also strips any internal
identifier that slips through when it renders CHANGELOG.md, but that is
a backstop, not a license to write bare ticket IDs here.

STATUS FIELD GUIDE
==================
WRITER authors notes with "## Status: PENDING-RELEASE" as a placeholder.
scripts/cm/release.sh stamps the actual ship decision at release time based on TESTER's recommendation:
  PASS                    → Status: FUNCTIONAL
  SHIP-WITH-CONCERNS      → Status: KNOWN-BUGS
  SHIP-WITH-SERIOUS-CONCERNS → Status: NON-FUNCTIONAL

Do NOT change "PENDING-RELEASE" to a concrete value — scripts/cm/release.sh will
replace it with the correct decision. Writing a concrete value here risks
mismatching the actual ship policy applied at release time.

NON-FUNCTIONAL WARNING
======================
When Status == NON-FUNCTIONAL, the ## Known Issues section MUST include a
warning line naming what is broken IN PLAIN LANGUAGE (substance, drawn from
each bug's own Symptom), never the bare internal ticket ID:
  "Do not use this version in production. <substance of what's broken>. Fix expected in v.next-patch."
Replace v.next-patch with the actual next patch version (e.g. vX.Y.Z+1).
If any of the underlying items have a public KI-N.N.N.N identifier, cite it
next to its substance description (see KNOWN-BUGS BUG LIST below) — the
public disclosure namespace is unaffected by this rule.

KNOWN-BUGS BUG LIST
====================
When Status == KNOWN-BUGS or NON-FUNCTIONAL, ## Known Issues MUST list
each open item by substance, sourced from its own Symptom/Fix text. Format
each entry as:
  - <Substance description of what's wrong, from the Symptom text> — Workaround if known, else "No workaround available."
When the item has a public KI-N.N.N.N identifier assigned, prefix the
substance with it:
  - **KI-N.N.N.N** — <Substance description> — Workaround if known, else "No workaround available."
Never render the bare internal ticket ID (BUG-NNNN) in this list — there is
no lint carve-out for this file.
-->

# Release Notes: <project-name> vX.Y.Z

**Release Date:** YYYY-MM-DD
**Released By:** <agent or human name>
**Repository:** <repo URL>

---

## Status
<!-- scripts/cm/release.sh stamps this field at release time. WRITER must leave the value below as-is. -->
PENDING-RELEASE

---

## Summary

One paragraph describing what this release delivers, who it affects, and any important context about the release.

---

## What Shipped

Features, improvements, and changes included in this release.

- **<Feature or change title>** — Brief description of what changed and why it matters.
- **<Feature or change title>** — Brief description.

---

## Bugs Resolved

Bugs that were identified and fixed in this release, described by substance
(drawn from the opening sentence of each bug's own Symptom text, not from a
proposed Fix) — never by the bare internal ticket ID. Cite the public
KI-N.N.N.N identifier when one has been assigned; that public namespace
is unaffected by this rule.

| Substance (what was wrong / what changed) | Public ID (if disclosed) | Severity | Resolution |
|--------------------------------------------|---------------------------|----------|------------|
| Short substance description | `KI-N.N.N.N` or "internal-only" | HIGH / MEDIUM / LOW | Fixed in commit `<hash>` |

If no bugs were resolved, write "None."

---

## Bugs Skipped

<!--
THIS SECTION IS RENDERED AT RELEASE TIME, NOT AUTHORED.

Where the TESTER report for this RC carries bug, gap, or stale-assertion
findings, the release script replaces this section's entire body — this
comment included — with a freshly rendered table, whatever was typed here.
Nothing you write below survives that render, so do not treat it as the
shipped record.

What the pipeline guarantees. One row per qualifying finding, in the four
columns below: the substance taken from that bug's own Symptom text, its
public identifier (or "internal-only" where none has been assigned), its
severity, and the finding's own rationale text. Every rendered cell passes
through the internal-identifier scrub, so a rationale that quotes another
release's finding cannot re-leak that finding's kanban task ID. A bug
already rendered under "## Known Issues" is never repeated here. The render
is independent of the ship decision — a release carries deferred bugs under
any status.

What you are still responsible for. The text below is the fallback for the
one case the render does not cover: a report with no qualifying finding. In
that case your text ships exactly as typed, with no scrub in front of it.
Write "None." there when nothing was skipped, describe anything else by
substance rather than by the bare internal ticket ID, and never type a
kanban task identifier into it.
-->

Bugs that were identified during verification but intentionally not fixed in this release, described by substance — never by the bare internal ticket ID. Include the rationale for each.

| Substance (what was wrong) | Public ID (if disclosed) | Severity | Rationale for Skipping |
|------------------------------|---------------------------|----------|------------------------|
| Short substance description | `KI-N.N.N.N` or "internal-only" | HIGH / MEDIUM / LOW | Deferred to vX.Y.Z / Low impact / Will not fix |

If no bugs were skipped, write "None."

---

## Known Issues

<!--
FUNCTIONAL release: write "None." below and stop.

KNOWN-BUGS release: replace the placeholder lines below with one bullet per
open item, described by substance (from its own Symptom/Fix text), citing
the public KI-N.N.N.N identifier when one has been assigned:
  - **KI-N.N.N.N** — <Substance description> — Workaround if known, else "No workaround available."
  - <Substance description> (internal-only, not yet disclosed) — Workaround if known, else "No workaround available."

NON-FUNCTIONAL release: include the warning line first, then the substance list:
  Do not use this version in production. <substance of what's broken>. Fix expected in v.next-patch.

  - **KI-N.N.N.N** — <Substance description> — Workaround if known, else "No workaround available."
  - <Substance description> (internal-only, not yet disclosed) — Workaround if known, else "No workaround available."

Never render the bare internal ticket ID (BUG-NNNN) anywhere in this
section — there is no lint carve-out for this file.
-->

Issues that exist in this release and have not been resolved or formally skipped. Users should be aware of these.

- **<Issue title>** — Description and any available workaround.

If there are no known issues, write "None."
