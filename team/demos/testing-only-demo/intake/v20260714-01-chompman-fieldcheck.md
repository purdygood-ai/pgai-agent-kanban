# v20260714-01-chompman-fieldcheck — Chomp-Man Shipped-Tag Field Check (testing-only)

## Status
ready

## Target Version
v20260714-01-chompman-fieldcheck

## Workflow Type
testing-only

## Source Branch
<YOUR-TAG>

## Test Required
false

## Human Approval Required
none

## Summary
Read-only field verification of the shipped chomp-man tag
`<YOUR-TAG>`: does the artifact hold up when a stranger checks out
exactly what shipped?

## Goals
1. **Suite on the tag**: run the project's test suite from the
   read-only worktree; record pass/fail counts and the exit code.
2. **Version coherence**: the shipped tag's declared version (its
   version file / package metadata, whatever the project carries)
   matches the tag name.
3. **Artifact sanity**: the game launches headless or its entry
   point exits cleanly with `--help` (whichever the project
   supports); record the invocation and exit code.

## Deliverables
- The finalize report at
  `projects/testing-pgai-chomp-man/artifacts/v20260714-01-chompman-fieldcheck-report.md`
  with per-goal verdicts, the verified SHA, and an explicit
  no-mutation attestation (target tree byte-identical before/after).

## Acceptance Criteria
1. Report exists at the finalize location with a verdict per goal.
2. The target dev tree shows zero changes (`git status --short`
   empty; HEAD unmoved) after the run.
3. No tag created, no version consumed, nothing pushed.
4. Any GENUINE defect found is filed on the pgai-chomp-man lane per
   normal Path C with Source Task/Source Report provenance — the
   report references the filing rather than duplicating it.

## Notes for TESTER
This is a demo audit: honest verdicts over impressive ones. If a
goal cannot be evaluated (e.g., the project has no version file),
say so plainly in the report — "not evaluable" is a legitimate
verdict and better than a guessed PASS.

## Notes for Operator
Re-running this audit later is normal: intake a fresh copy under a
new label (new date, new filename). Labels are names, not numbers —
nothing collides.
