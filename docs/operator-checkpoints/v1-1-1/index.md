# Operator Checkpoint: v1.1.0 Plugin-Port Verification

## Origin

This checkpoint was filed by TESTER (autonomous Path C) in PRIORITY-0001
(`priority/PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md`) during the
v1.1.0 release. Unit tests passed, but end-to-end verification against the live
managed projects (chomp-man, the-three-bears) could not be performed inside the
TESTER sandbox. The requirements document explicitly deferred that verification
to a post-ship operator run and instructed TESTER to file the gap rather than
block. This directory is the canonical home for the operator's findings.

## Scope

Verification-only. No engine changes, no plugin-manifest edits, no new managed
projects. The sole purpose is confirming that the v1.1.0 plugin port is
behavior-verbatim on the live install.

## Checkpoint Artifacts

| Document | Description | State |
|---|---|---|
| [release-rc-checkpoint.md](release-rc-checkpoint.md) | End-to-end release-workflow RC run on the managed release project; artifacts diffed against pre-port baseline. | authored |
| [document-workflow-checkpoint.md](document-workflow-checkpoint.md) | End-to-end document-workflow deliverable run on the managed document project; published artifact diffed against pre-port baseline. | authored |
| [custom-workflow-walkthrough.md](custom-workflow-walkthrough.md) | Operator hand-authors a `testing` custom workflow on the live install, validates with `validate-workflow.sh`, and runs it against a labelled ref; walkthrough time recorded per the requirements doc. | authored |
| [outcome-notes.md](outcome-notes.md) | Fill-in template for pass/fail per PRIORITY-0001 criterion and observed-drift notes; operator completes after running the three checkpoints. | authored |

## Acceptance Criteria (from PRIORITY-0001)

- [ ] Release-workflow RC run end-to-end on the managed release project after
      v1.1.0 upgrade; shipped artifacts diffed against pre-port baseline and
      confirmed byte-identical (or diffs annotated and confirmed non-behavioral).
- [ ] Document-workflow deliverable run end-to-end on the managed document
      project after v1.1.0 upgrade; published document diffed against pre-port
      baseline and confirmed byte-identical (or diffs annotated and confirmed
      non-behavioral).
- [ ] Primary user story from `docs/creating-a-workflow.md` Part 1 walked
      through end-to-end on the live install; walkthrough time recorded.
- [ ] Outcome note filed in `outcome-notes.md` describing pass/fail per
      criterion and any observed drift.
