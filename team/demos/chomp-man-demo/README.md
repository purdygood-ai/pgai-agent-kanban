# Chomp-Man demo — the release workflow

This demo builds a small arcade game — a Pac-Man-style "Chomp-Man" — feature by
feature, and ships each as a tagged release into a local git repo. Along the
way you deposit **bugs** (fixes) and **priorities** (enhancements) in between
the feature requirements, exactly the way real development goes: build a thing,
notice something's off, fix it, add an improvement, build the next thing.

It shows the **release** workflow end-to-end: requirement → PM decomposes →
CODER builds on a release-candidate branch → TESTER verifies → CM ships a tagged
RC. Everything is local: `push_to_remote = false` means real commits and real
tags, but nothing is ever pushed to a remote.

---

## Setup (once)

### 1. Make a local scratch repo for the game

The release workflow ships real software, so it needs a git repo to build into.
Create an empty local one (no remote):

```bash
mkdir -p ~/pgai-chomp-man-src && cd ~/pgai-chomp-man-src
git init
git commit --allow-empty -m "initial empty commit"
cd -
```

### 2. Create the project

```bash
scripts/create-project.sh --project pgai-chomp-man --workflow-type release
```

### 3. Wire the project to your scratch repo (edit project.cfg)

```bash
$EDITOR projects/pgai-chomp-man/project.cfg
#   set:  push_to_remote = false                       (local-only — never push)
#   set:  branch_prefix = ai_                           (isolate the AI's branches)
#   set:  dev_tree_path = /home/<you>/pgai-chomp-man-src   (your scratch repo)
#   set:  git_repo_url =                                (leave empty — local only)
```

`branch_prefix = ai_` keeps all the chain's branches (`ai_main`,
`ai_rc/...`, `ai_`-prefixed tags) in their own lane, so they're easy to see and
never collide with a human `main`. Each RC branches from `ai_main` and
squashes back into it — one hop, no `develop` in the middle.

---

## Run it (deposit in order, watch each ship)

Deposit the items **in the order below**. After each `intake`, watch the
dashboard (or `scripts/kanban-status.sh`): PM decomposes, CODER builds on an
`ai_rc/...` branch, TESTER verifies, CM tags the release into your local repo.

**Let each release fully ship before depositing the next** — the chain runs one
release candidate at a time per project (the Active-RC gate), so the clean
rhythm is one-in, one-out. (You *can* stack several at once and the chain will
work them in sequence — but for a first run, one at a time makes it easy to
watch each step.)

A note before you start: the **bugs and priorities below are realistic examples
from a reference run.** Because the agents are nondeterministic, your build may
not have these exact issues — that's fine. The point is to show *how* you feed a
fix or an enhancement back to the chain. Deposit them where shown; if your build
already does the right thing, the chain will handle it gracefully.

### 1. Bootstrap (v0.0.1)
```bash
scripts/intake.sh --project pgai-chomp-man \
  --file demos/chomp-man-demo/intake/v0.0.1-bootstrap.md
```
Sets up the project skeleton. First release.

### 2. Screen and movement (v0.1.0)
```bash
scripts/intake.sh --project pgai-chomp-man \
  --file demos/chomp-man-demo/intake/v0.1.0-screen-and-movement.md
```
The maze and the player appear; you can move Chomp-Man around. Watch the full
RC lifecycle for the first time here.

### 3. BUG — movement requires a held key
```bash
scripts/intake.sh --project pgai-chomp-man \
  --file demos/chomp-man-demo/intake/BUG-0001-movement-requires-held-key.md
```
Now that movement exists, here's a movement bug (the character only steps on a
fresh keypress instead of moving while a key is held). Watch the **patch lane**:
the bug is picked up, fixed, and shipped as a patch release. This is how you
report a defect — write it up, drop it with `intake.sh` (the `BUG-` prefix
routes it to `bugs/`).

### 4. PRIORITY — scalable 16:9 HD display
```bash
scripts/intake.sh --project pgai-chomp-man \
  --file demos/chomp-man-demo/intake/PRIORITY-0001-scalable-16x9-hd-display.md
```
An **enhancement** (not a bug): make the display scale to a 16:9 HD window. This
shows how you prioritize an improvement in — same `intake.sh`, the `PRIORITY-`
prefix routes it to the priority lane.

### 5. Dots and scoring (v0.2.0)
```bash
scripts/intake.sh --project pgai-chomp-man \
  --file demos/chomp-man-demo/intake/v0.2.0-dots-and-scoring.md
```
Now there are dots to eat and a score.

### 6. PRIORITY — sound effects
```bash
scripts/intake.sh --project pgai-chomp-man \
  --file demos/chomp-man-demo/intake/PRIORITY-0002-sound-effects.md
```
With eating and scoring in place, add sound. Another enhancement through the
priority lane.

### 7. Dragons (v0.3.0)
```bash
scripts/intake.sh --project pgai-chomp-man \
  --file demos/chomp-man-demo/intake/v0.3.0-dragons.md
```
The enemies (dragons) arrive and chase Chomp-Man.

### 8. Power pellets (v0.4.0)
```bash
scripts/intake.sh --project pgai-chomp-man \
  --file demos/chomp-man-demo/intake/v0.4.0-power-pellets.md
```
Eat a power pellet and the dragons become edible for a while.

### 9. BUG — no color change during the power-pellet window
```bash
scripts/intake.sh --project pgai-chomp-man \
  --file demos/chomp-man-demo/intake/BUG-0002-no-color-change-power-pellet.md
```
Now that power pellets exist, here's a bug against that feature: Chomp-Man's
sprite gives no visual cue that he's in the powered-up (dragon-eating) state.
A fix through the patch lane, reported against a feature that now exists.

### 10. PRIORITY — extra life for every 2 dragons eaten
```bash
scripts/intake.sh --project pgai-chomp-man \
  --file demos/chomp-man-demo/intake/PRIORITY-0003-extra-life-per-two-dragons.md
```
An enhancement that only makes sense once you can eat dragons: reward it with an
extra life per two eaten.

### 11–14. The rest of the features
```bash
scripts/intake.sh --project pgai-chomp-man --file demos/chomp-man-demo/intake/v0.5.0-fruit-bonus.md
scripts/intake.sh --project pgai-chomp-man --file demos/chomp-man-demo/intake/v0.6.0-chompman-dragon-sprites.md
scripts/intake.sh --project pgai-chomp-man --file demos/chomp-man-demo/intake/v0.7.0-chompman-warp-tunnels.md
scripts/intake.sh --project pgai-chomp-man --file demos/chomp-man-demo/intake/v0.8.0-chompman-dragon-personalities.md
```
Fruit bonuses, nicer sprites, warp tunnels at the maze edges, and distinct
dragon personalities — the finishing touches.

---

## What you just learned

- The **release workflow** end-to-end: requirement → PM → CODER (on an
  `ai_rc/...` branch) → TESTER → CM ships a tagged release into your local repo.
- **Three intake types, one command:** requirements (`v*`), bugs (`BUG-*`), and
  priorities (`PRIORITY-*`) are all deposited with `intake.sh` — the filename
  prefix routes each to the right place.
- **The real development rhythm:** build a feature, fix bugs that surface against
  it, fold in enhancements, build the next feature — bugs and priorities ride
  the patch lane between feature releases.
- **`branch_prefix = ai_`** isolates the chain's branches and tags so they never
  touch a human `main`.
- **`push_to_remote = false`** keeps everything local — real tags, no remote.

Inspect any item with `scripts/show.sh --project pgai-chomp-man --key <KEY>`
(e.g. `--key BUG-0001` or `--key v0.4.0`), and watch overall progress on the
dashboard or with `scripts/kanban-status.sh`.

When you're done, remove the project (`scripts/remove-project.sh --project
pgai-chomp-man`) and delete `~/pgai-chomp-man-src` — it was entirely local.
