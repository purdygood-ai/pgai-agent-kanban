# docs/operator-checkpoints/ — Operator-Run Verification Checkpoints

This directory collects the verification checkpoints an operator runs by
hand — cold-path walkthroughs and per-release verifications that the
autonomous test suites cannot fully cover. Each checkpoint is a
self-contained procedure: copy-paste commands, an expected-outcome
table, and a findings section the operator fills in.

Checkpoints are additive. Older entries stay in place as evidence of
what the prior release actually did; new checkpoints join alongside them.

## What is here

| Checkpoint | Scope | When to run |
|---|---|---|
| [public-readme-walk.md](public-readme-walk.md) | Prove that the top-level [../README.md](../README.md) five-minute demo spine and API quickstart run verbatim on a fresh clone with no additional instructions. The cold-reader gate for the public README. | Before any release that touches the top-level README or the API quickstart — and any time a public promotion candidate is on the runway. |
| [v1-1-1/](v1-1-1/) | End-to-end managed-project verification of the workflow-type plugin port: release-workflow RC, document-workflow deliverable, and a hand-authored custom workflow validated with `validate-workflow.sh`. Filed by TESTER as the post-ship operator run for that release line. | Historical reference; retained as evidence for that release line's shipped behavior. |

## How to add a checkpoint

An operator checkpoint is a Markdown file that answers three questions:

1. **What am I proving?** One or two sentences at the top of the file
   naming the scope and the citing document (a README, a HOW_TO
   section, a public promise).
2. **How do I prove it?** A numbered procedure with copy-paste command
   blocks, each with an expected outcome. No implicit "and then do the
   obvious thing" steps — every action is a printed command or a
   printed check.
3. **What did I find?** A findings table the operator fills in after
   running, plus a sign-off block (operator name, date, result).

New checkpoints for a specific release line go under a
`vX-Y-Z/` subdirectory (mirroring [v1-1-1/](v1-1-1/)) with an
`index.md` and one file per procedure. Standalone cold-path
checkpoints — like [public-readme-walk.md](public-readme-walk.md) — live
at the top level of this directory.

## See also

- [../OPERATIONS.md](../OPERATIONS.md) — the operator walkthrough:
  HALT commands, dashboard tours, RC recovery, provider switch, project
  bootstrap. The reference an operator reaches for during a checkpoint
  when something surprises them.
- [../../team/SOP.md](../../team/SOP.md) — the shared operating
  procedure agents follow. Not part of the checkpoint's execution path,
  but the source of truth for what the chain is supposed to do while a
  checkpoint watches it.
- [../operator-commands.md](../operator-commands.md) — every operator
  command's full flag surface. When a checkpoint's command block looks
  unfamiliar, this is where to reconcile.
- [../operator-troubleshooting.md](../operator-troubleshooting.md) —
  common cold-run failure modes and their fixes; helpful when a
  checkpoint step behaves unexpectedly.

## Not a replacement for the automated suites

Checkpoints run alongside the standing unit and integration test
suites. They exist because parts of the operator experience — a cold
clone, a fresh install, a hand-authored workflow plugin — cannot be
exercised inside the sandboxed test environment. If a check *can* be
automated, it belongs in a test file; a checkpoint carries the checks
that can only be verified with a human at the keyboard.
