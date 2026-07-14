# Demo: Auditing a Shipped Release (testing-only workflow)

The third workflow type, demonstrated on the output of the first
demo. Where the chomp-man demo showed agents BUILDING and SHIPPING
software, this one shows agents VERIFYING software that already
shipped — read-only, no versions consumed, no releases produced. The
product is a report; findings, if any, are filed as bugs on the
target project's own lane.

**Prerequisite:** a completed chomp-man demo run — you need its dev
tree at `~/develop/pgai-chomp-man` (or wherever you put it) with at
least one shipped tag. Check:

```bash
git -C ~/develop/pgai-chomp-man tag | tail -3
```

Note the most recent tag; the intake requirement calls it
`<YOUR-TAG>` and you will substitute it in two places.

## What this demonstrates

- A workflow whose ROSTER is just pm + tester (no CODER, no WRITER,
  no CM — watch the queue: no other task types ever materialize).
- Read-only git (`git_mode = ro`): the tester works in a detached
  worktree of the target's tag; the target repository is
  byte-identical before and after, and the report attests to it.
- Label versioning: the requirement's version is a NAME
  (`v20260714-01-chompman-fieldcheck`), not a number — nothing is
  consumed, and you can re-run the same audit next month under a new
  label.
- `finalize = report`: "done" means a report file exists at the
  project's artifacts location — no tag, no publish.
- Cross-project findings: if the audit finds a real defect, the
  tester files it on the CHOMP-MAN lane (with Source Task/Source
  Report provenance), and chomp-man's own discovery may then bundle
  a fix release. See "Findings and cross-project filings" in
  workflows/README.md — and the brakes (`HALT`, ceilings) if you
  want the target to stay quiet.

## Steps

1. **Register the audit project** (its own lane, pointed at the
   chomp-man dev tree):

   ```bash
   scripts/create-project.sh --project testing-pgai-chomp-man \
       --workflow-type testing-only
   # then set the dev tree in the project cfg to your chomp-man tree
   ```

2. **Prepare the requirement**: copy
   `demos/testing-only-demo/intake/v20260714-01-chompman-fieldcheck.md`
   somewhere writable and replace both `<YOUR-TAG>` occurrences with
   the tag from the prerequisite check.

3. **Intake it:**

   ```bash
   scripts/intake.sh --project testing-pgai-chomp-man --file <your-copy>.md
   ```

4. **Watch** (next PM tick: selection → a pm→tester decomposition —
   note what does NOT appear: no CODER, no CM, no RC):

   ```bash
   scripts/dashboard/show-queues.sh --details --project testing-pgai-chomp-man
   ```

5. **Read the product** (a few minutes later):

   ```bash
   ls projects/testing-pgai-chomp-man/artifacts/
   git -C ~/develop/pgai-chomp-man status --short   # still clean — the ro promise
   ```

6. **Confirm the run closed itself** — the finalize step flips the
   intake item to `done` when the last ticket completes; nothing to
   press, just verify:

   ```bash
   grep -A1 "^## Status" projects/testing-pgai-chomp-man/requirements/v20260714-01-chompman-fieldcheck.md
   # → done
   ```

7. **If the audit found something:** look at
   `projects/pgai-chomp-man/bugs/` — a filed finding carries its
   Source Task and Source Report. Whether chomp-man acts on it
   autonomously is governed by chomp-man's own configuration; that
   loop (audit → filing → fix release on the target) is the suite
   behaving as a system, and it is the most instructive possible
   ending for this demo if you let it run.

## Cleanup

```bash
scripts/remove-project.sh --project testing-pgai-chomp-man
```

The audit lane is disposable; the report you keep is in its
artifacts directory, and any filings live on in chomp-man's ledger —
which is the point: the AUDIT is ephemeral, the FINDINGS are not.
