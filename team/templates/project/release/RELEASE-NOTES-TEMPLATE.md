<!--
GUIDE NOT GATE: This template is a guide, not a gate.
Agents must tolerate imperfect or missing sections.
Use the structure when it helps; skip sections that do not apply.

CHANGELOG.md IS NOT A WRITER DELIVERABLE
========================================
WRITER authors release-notes/vX.Y.Z.md ONLY. Do NOT edit CHANGELOG.md.
CHANGELOG.md is regenerated deterministically from this release-notes
file and the bug ledger by:
  - team/pgai_agent_kanban/cm/changelog_writer.py, or
  - cm/release.sh Step 11b at release time.
Hand-editing CHANGELOG.md bypasses the changelog writer's internal-
identifier safety pass and desynchronizes its byte-exact rendering,
which trips the CHANGELOG freshness gate and the internal-bug-
identifier unit test. Write the notes; let CM regenerate the changelog.

The release-notes body must refer to bugs by symptom and public
identifier — not by internal ticket ID. The changelog writer strips
internal identifiers automatically when it renders CHANGELOG.md; writing
symptoms here keeps both this file and the rendered changelog clean.

STATUS FIELD GUIDE
==================
WRITER authors notes with "## Status: PENDING-RELEASE" as a placeholder.
cm-release.sh stamps the actual ship decision at release time based on TESTER's recommendation:
  PASS                    → Status: FUNCTIONAL
  SHIP-WITH-CONCERNS      → Status: KNOWN-BUGS
  SHIP-WITH-SERIOUS-CONCERNS → Status: NON-FUNCTIONAL

Do NOT change "PENDING-RELEASE" to a concrete value — cm-release.sh will
replace it with the correct decision. Writing a concrete value here risks
mismatching the actual ship policy applied at release time.

NON-FUNCTIONAL WARNING
======================
When Status == NON-FUNCTIONAL, the ## Known Issues section MUST include:
  "Do not use this version in production. Issues filed: BUG-NNNN, BUG-MMMM. Fix expected in v.next-patch."
Replace BUG-NNNN / BUG-MMMM with the actual filed bug IDs.
Replace v.next-patch with the actual next patch version (e.g. vX.Y.Z+1).

KNOWN-BUGS BUG LIST
====================
When Status == KNOWN-BUGS or NON-FUNCTIONAL, ## Known Issues MUST list
each filed bug. Format each entry as:
  - **BUG-NNNN** — Short description. Workaround if known, else "No workaround available."
-->

# Release Notes: <project-name> vX.Y.Z

**Release Date:** YYYY-MM-DD
**Released By:** <agent or human name>
**Repository:** <repo URL>

---

## Status
<!-- cm-release.sh stamps this field at release time. WRITER must leave the value below as-is. -->
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

Bugs that were identified and fixed in this release.

| Bug ID | Description | Severity | Resolution |
|--------|-------------|----------|------------|
| `<id>` | Short description | HIGH / MEDIUM / LOW | Fixed in commit `<hash>` |

If no bugs were resolved, write "None."

---

## Bugs Skipped

Bugs that were identified during verification but intentionally not fixed in this release. Include the rationale for each.

| Bug ID | Description | Severity | Rationale for Skipping |
|--------|-------------|----------|------------------------|
| `<id>` | Short description | HIGH / MEDIUM / LOW | Deferred to vX.Y.Z / Low impact / Will not fix |

If no bugs were skipped, write "None."

---

## Known Issues

<!--
FUNCTIONAL release: write "None." below and stop.

KNOWN-BUGS release: replace the placeholder lines below with one bullet per filed bug:
  - **BUG-NNNN** — Short description. Workaround if known, else "No workaround available."

NON-FUNCTIONAL release: include the warning line first, then the bug list:
  Do not use this version in production. Issues filed: BUG-NNNN, BUG-MMMM. Fix expected in v.next-patch.

  - **BUG-NNNN** — Short description. Workaround if known, else "No workaround available."
  - **BUG-MMMM** — Short description. Workaround if known, else "No workaround available."
-->

Issues that exist in this release and have not been resolved or formally skipped. Users should be aware of these.

- **<Issue title>** — Description and any available workaround.

If there are no known issues, write "None."
