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

If any of those is not true, resolve it before promoting. Promotion is
not a recovery tool.

## Procedure

Execute the steps in order. Steps 1–3 are the public-side git
operations; step 4 is the internal bookkeeping the disclosure model
requires.

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
