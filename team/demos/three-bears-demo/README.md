# Three Bears demo — the document workflow

This demo writes a children's bedtime story, "The Three Bears," and evolves it
across five revisions. It shows the **document** workflow: you deposit a
requirement, the chain decomposes it, a WRITER produces the prose, and CM
finalizes it to a published artifact. No code, no git repo to build — the
deliverable is the document itself.

It also shows something real about iterating on a deliverable: midway through,
we add a fox character, then remove it, then bring it back — because that's how
real editing goes (you try something, change your mind, restore it).

---

## Setup (once)

Create the project as a **document**-workflow project:

```bash
scripts/create-project.sh --project pgai-three-bears --workflow-type document
```

Then open its config and set it to local-only (the document workflow does not
push anywhere, but set this for consistency and so nothing ever touches a
remote):

```bash
$EDITOR projects/pgai-three-bears/project.cfg
#   set:  push_to_remote = false
```

That's the whole setup — document projects need no code dev tree and no git repo.

---

## Run it (deposit each requirement, watch it publish)

Deposit the revisions **in order**. After each `intake`, watch the dashboard
(or `scripts/kanban-status.sh`): the chain wakes, PM decomposes the requirement,
a WRITER produces the story text, and CM finalizes it. The published document
lands under `projects/pgai-three-bears/artifacts/`.

**Let each revision fully finish (you'll see it published) before depositing the
next** — the chain runs one release candidate at a time per project, so a clean
sequence is one-in, one-out.

### 1. The story (v0.0.1)

```bash
scripts/intake.sh --project pgai-three-bears \
  --file demos/three-bears-demo/intake/v0.0.1-three-bears-story.md
```
Watch: PM decomposes → WRITER writes the bedtime story → CM finalizes. Check
`projects/pgai-three-bears/artifacts/` for the first published version.

### 2. Add a friendly fox at the stream (v0.0.2)

```bash
scripts/intake.sh --project pgai-three-bears \
  --file demos/three-bears-demo/intake/v0.0.2-three-bears-friendly-fox-at-the-stream.md
```
Watch: the story is revised to introduce the fox. A new artifact version
publishes.

### 3. Add an interior scene (v0.0.3)

```bash
scripts/intake.sh --project pgai-three-bears \
  --file demos/three-bears-demo/intake/v0.0.3-bears-add-interior-scene.md
```

### 4. Remove the fox (v0.0.4)

```bash
scripts/intake.sh --project pgai-three-bears \
  --file demos/three-bears-demo/intake/v0.0.4-bears-remove-fox.md
```
This is the "changed my mind" step — the fox we added in v0.0.2 comes back out.
A real document evolves like this.

### 5. Restore the fox (v0.0.5)

```bash
scripts/intake.sh --project pgai-three-bears \
  --file demos/three-bears-demo/intake/v0.0.5-bears-restore-fox.md
```
And back in again. By the end you have five published versions in
`artifacts/`, each a snapshot of the story at that revision.

---

## What you just learned

- The **document workflow** end-to-end: requirement → decomposition → WRITER →
  finalize → published artifact.
- Each requirement is a versioned revision (`vX.Y.Z`), deposited with
  `intake.sh` (routed to `requirements/` by the `v*` filename prefix).
- The deliverable **evolves** across revisions — you can add, remove, and
  restore content, and each change is its own published version with full
  history.
- Where published documents land: `projects/pgai-three-bears/artifacts/`.

When you're done, you can remove the project (`scripts/remove-project.sh
--project pgai-three-bears`) and delete its directory — it was entirely local.
