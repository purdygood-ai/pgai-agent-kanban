# Promotion Playbook — Internal Release to Public Tag

Operator procedure for promoting an internal `vX.Y.Z` release to the
public repository. The autonomous chain ships every internal release
end-to-end; promotion to the public tag is an explicit operator step
outside the chain — the last mile between "the RC shipped internally"
and "a user can `git clone` this version."

This playbook is deliberately short. The autonomous chain has already
done the hard work (ship policy, TESTER verification, tag, release
notes). Promotion is the operator's decision to disclose the release
publicly, plus the one bookkeeping step that keeps the ledger-projected
CHANGELOG honest across releases.

## When to promote

Promote only after the internal release has shipped cleanly:

- The internal tag `vX.Y.Z` exists on origin.
- `## Last Released` in the project's `release-state.md` reads
  `vX.Y.Z`.
- The release-notes file `release-notes/vX.Y.Z.md` has been stamped by
  CM (its `## Status` field is a concrete ship-policy value, not the
  `PENDING-RELEASE` placeholder).
- No hard `HALT` is in place for the project.
- The DEEP-VERIFY (cold-build) lane holds a fresh green record for
  `vX.Y.Z`. This one is checked by a command rather than by eye — it is
  step 0 of the procedure below.

If any of those is not true, resolve it before promoting. Promotion is
not a recovery tool.

## Procedure

Execute the steps in order. Step 0 is the pre-release gate that must
pass before anything reaches the public remote; steps 1–3 are the
public-side git operations; step 4 is the internal bookkeeping the
disclosure model requires.

### 0. Confirm a fresh green deep-verify lane

The cold-build lane no longer runs on every RC — it is the DEEP-VERIFY
tier, which runs weekly on a schedule and on demand, and is *checked*
here (see "The DEEP-VERIFY tier" in [team/roles/CM.md](../team/roles/CM.md) for
the rule and the tier's shape). That trade only holds if the lane
provably ran, green, against the tree state you are about to disclose.
Confirm it before you push anything:

```bash
# From the internal repository root. Substitute the project name and the
# version being promoted.
bash team/scripts/deep-verify-freshness-check.sh --project <name> --version vX.Y.Z
```

Exit 0 means the most recent ledger record is `built`, for exactly this
version, inside the freshness window (`PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS`,
default 7 days) — promotion may proceed to step 1. The check is
read-only: it reads the project's deep-verify ledger, writes nothing,
and never starts the lane on your behalf.

**The three refusals.** Each exits 1 and prints a dual-channel error
contract — a machine key, a plain-language message, and a runnable
remedy. Where the remedy re-runs the lane it is the operator-only
container tier: it carries `PGAI_CONTAINER_TIER=1` and `--image all`,
and it runs from your own shell, not from inside an agent wake (there
it prints a `SKIPPED` line, exits 0, and records nothing). What each
refusal means, and what to do about it:

**1. No record at all — `freshness_gate.no_record`.** The DEEP-VERIFY
tier has never run for this project, so nothing whatever is known about
whether its images build. Silence is not a pass. Run the lane, then
re-run the check:

```bash
PGAI_CONTAINER_TIER=1 bash team/scripts/deep-verify.sh --project <name> --image all
```

**2. The last record is not green — `freshness_gate.failed_to_build` or
`freshness_gate.could_not_attempt`.** These are two different facts and
they need two different responses; what each verdict means is defined in
[team/SOP-core.md](../team/SOP-core.md) under "Gates and Refusals"
("Unusable build blocks; an unavailable build environment does not").

- `failed_to_build` is a tree defect. The deep-verify run that produced
  it already filed a bug naming the failing engine/flavor pair — do not
  file another. Let the loop fix the build, then re-run the lane against
  the fixed tree and re-run this check. Do not promote over it: the tag
  would publish something known not to build.
- `could_not_attempt` means nothing was learned — no container engine on
  the host, or an unreachable registry. It can never satisfy promotion no
  matter how recent it is. Re-run the lane on a host that has a working
  container engine and reachable registries, then re-run this check.

**3. Green, but it does not speak for this version —
`freshness_gate.stale_record`.** The most recent record is `built`, but
it is for a different version, or older than the freshness window, or
both; the message names which. A green result ages: a byte-identical
tree can stop building because something upstream moved. Re-run the lane
against the tree being promoted, then re-run this check.

Two further refusals guard the check's own inputs rather than the
promotion decision: `freshness_gate.unregistered_project` (the
`--project` name is not in `projects.cfg`) and
`freshness_gate.ledger_unreadable` (the ledger file exists but its most
recent record does not parse — repair or remove the malformed record by
hand, then re-run the lane). A malformed ledger refuses rather than
reporting never-run, so a parse failure can never be mistaken for a
clean slate.

A refusal looks like this — the machine key is greppable, and the remedy
line is runnable as printed from your own shell. The script path is
printed absolute (`<kanban-root>` below stands in for it), and a remedy
that needs a step first, such as repairing the ledger or fixing the
build, names that step in a trailing `# after ...` shell comment:

```
error:   freshness_gate.no_record
message: project 'pgai-agent-kanban' has no deep-verify record at all -- the DEEP-VERIFY tier has never run for it.
remedy:  PGAI_CONTAINER_TIER=1 bash <kanban-root>/scripts/deep-verify.sh --project pgai-agent-kanban --image all
```

### 1. Prepare the public working tree

Fetch the latest state of the public repository and check out the tag
the internal release produced:

```bash
cd <path-to-public-clone>
git fetch --tags
git checkout vX.Y.Z
```

Confirm the tag matches the internal release: the `## Summary` in
`release-notes/vX.Y.Z.md` and the top entry of `CHANGELOG.md` on the
internal repo should describe the same release. If they diverge, stop
— that is a signal the internal ship did not land cleanly.

### 2. Push the public tag

Push the tag to the public origin:

```bash
git push public vX.Y.Z
```

The remote name (`public` above) is whatever the operator's public
remote is called; use the name the local clone actually configures. The
push is the visible act of promotion — as of the moment it succeeds, a
user can install this version by tag.

### 3. Append the just-published version to `release-notes/PUBLISHED`

After pushing the public tag, append it to `release-notes/PUBLISHED` so
the next internal release's regeneration translates all pending
fix-references into the new public coordinate:

```bash
cd <internal-repo>
echo "vX.Y.Z" >> release-notes/PUBLISHED
git add release-notes/PUBLISHED
git commit -m "Promote vX.Y.Z to public: append to PUBLISHED manifest"
git push origin main
```

Why this step matters. The internal changelog writer projects Known
Issues from the bug ledger, but discloses a bug only if it existed in a
PUBLISHED release. Once `vX.Y.Z` is on the manifest, a bug affecting
`vX.Y.Z` becomes disclosable and any `fix pending next release`
citation on an earlier entry can resolve to `fixed in vX.Y.Z` at the
next regeneration. Forgetting this step does not corrupt anything — it
just leaves the next CHANGELOG understated until the manifest catches
up.

The manifest is one public version per line, no blank lines. Append
mode is correct; never rewrite or reorder the file (the order records
the chronology of public disclosures).

### 4. Sanity-check the disclosure

At the next internal release, the regenerated `CHANGELOG.md` should
reflect the newly published version:

- Any pending fix-references for bugs `Fixed In` a version at or
  before `vX.Y.Z` translate to a concrete `fixed in <published>` under
  the earliest affected published entry.
- Any newly disclosable bugs (affecting `vX.Y.Z` for the first time)
  appear in Known Issues under the correct historical entry, with a
  freshly assigned `KI-X.Y.Z.<counter>` public ID persisted to the
  bug file.

The next internal release runs the writer; nothing further is required
from the operator. If the CHANGELOG does not update as expected,
inspect the bug file's `## Affects` / `## Fixed In` / `## Public ID`
fields — those are the source of truth the projection reads.

## Failure modes and recovery

- **Tag push rejected by public remote.** Confirm the public remote
  accepts fast-forwards for that ref pattern and that you have write
  access. Do not force-push a promotion tag.
- **`release-notes/PUBLISHED` edited by hand and now out of order.**
  The file is a manifest, not a log. Order does not affect the writer's
  disclosure decision (the writer treats the file as a set), but keep
  the file append-only by convention so its history reads cleanly.
- **Version appended before the public push succeeded.** Revert the
  append commit, re-run step 2, and re-append. The internal
  regeneration would otherwise disclose a version that is not actually
  public — the disclosure model's whole point is to not do that.

## Notes

- The step order matters: push the public tag first, then append the
  version to the manifest. Reversing them creates a window where the
  next internal release could disclose a version that has not yet
  reached users.
- The append step is intentionally manual. The autonomous chain does
  not touch the public remote and does not append to `PUBLISHED` on the
  operator's behalf. Promotion is a policy decision; the manifest
  records that decision.
- For the disclosure model — how the manifest is used at
  regeneration time — see the CHANGELOG Disclosure Model section in
  [OPERATIONS.md](OPERATIONS.md).
