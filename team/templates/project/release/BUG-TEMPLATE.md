# BUG-NNNN-slug

**Bug ID:** BUG-NNNN-slug
<!-- A date segment is optional: BUG-NNNN-<date>-slug is also accepted. -->
**Filed By:** (role or operator name)
**Date:** YYYY-MM-DD
**Severity:** (critical / high / medium / low)

## Status
open

## Category
misc

---

## Symptom
<!-- The opening sentence of this section becomes this bug's bullet in the
     release notes' Bug Fixes section when the fix ships, rendered as
     "Fixed: <opening sentence>". State the defect plainly in one complete
     sentence that a reader outside the kanban can follow, and put it
     first, ahead of any code block, log excerpt, or list. Do not open with
     a lead-in such as "The output contains:"; the material it introduces
     is dropped from the bullet, leaving the lead-in on its own. Detail and
     evidence follow the opening sentence. A Fix section, if you add one,
     is never quoted in the release notes. -->
What was observed.

## Expected
What should have happened.

## Actual
What actually happened.

## Reproduction
Steps to reproduce:

1.
2.

## Files Involved

-

## Hypothesis (optional)
Optional. Speculative theory about cause. Do not propose a fix here.

## Acceptance
<!-- Name the specific test(s) or command that prove the fix. Do not name a full gated runner; TESTER runs those once per RC.
     For a flaky-test fix: the proof is a test whose verdict does not depend on test order, host load or wall-clock timing; assert on behaviour, CPU time or progress, not on elapsed time. Do not generate CPU load on the host to reproduce or prove it. -->

-

---

## Affects
<!-- The earliest version that carries this defect (e.g. v1.0.0).
     Leave this section ABSENT for internal bugs that never affected a
     PUBLISHED release — absence means "affects: unverified" and the
     bug is excluded from public disclosure.
     Do NOT use prose placeholders such as "(internal — plugin era; no KI)";
     non-version text in this field triggers a loud warning during changelog
     regeneration.  Disclosure requires a recognised version string
     (e.g. v1.2.3); the generator never guesses a public identifier. -->

## Fixed In
<!-- The internal version whose release closed this bug (e.g. v1.16.2).
     Written by scripts/cm/release.sh at the fixing release; do not set by hand
     unless correcting a cm-release error. Leave absent for open bugs. -->

## Public ID
<!-- The public-facing identifier in the format KI-<version-no-v>.<counter>
     (e.g. KI-1.0.0.1). Assigned once by the changelog generator at first
     disclosure and persisted here — never recomputed at regeneration.
     An identifier that has appeared in the published CHANGELOG.md is
     spent: the generator never issues it to another bug, and a bug that
     persists an identifier the changelog already publishes for a
     different entry is given a fresh one at the next regeneration while
     the published line stays as it is.
     Internal-only bugs (never affecting a PUBLISHED version) never receive
     a Public ID. Do not edit this field after it is assigned. -->
