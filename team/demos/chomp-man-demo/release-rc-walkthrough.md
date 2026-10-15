# Chomp-Man walkthrough — one release candidate, end to end

The [demo README](README.md) shows you how to feed Chomp-Man to the chain.
This page follows **one** release candidate (RC) through every stage of the
release workflow and gives you a check for each one. Use it the first time you
want to see exactly what happens between `intake.sh` and a tag.

Each stage below says what to run and what you should see. The commands are
read-only, apart from the one deposit in Stage 1 and the optional manual wake.

The release workflow runs four steps for every requirement, in this order:

| Step        | Agent  | What it produces                                                   |
|-------------|--------|--------------------------------------------------------------------|
| `open-rc`   | CM     | the RC branch `ai_rc/vX.Y.Z`, cut from `ai_main`                   |
| `implement` | CODER  | commits on the RC branch                                           |
| `verify`    | TESTER | a verification report for the RC                                   |
| `release`   | CM     | a squash commit on `ai_main`, the tag `ai_vX.Y.Z`, release notes   |

---

## Before you start

- You have completed the [Chomp-Man demo setup](README.md#setup-once), and
  `~/pgai-chomp-man-src` is the game's dev tree.
- The bootstrap release, v0.0.1, has shipped. This page uses the next
  feature, **v0.1.0 — screen and movement**, as its example. To follow a
  different release, change the `V=` line below. Every later command reads
  the version from it.
- No other RC is in flight. The chain runs one RC at a time per project.

Set up your shell once:

```bash
source "$PGAI_AGENT_KANBAN_ROOT_PATH/shell-env"
cd "$PGAI_AGENT_KANBAN_ROOT_PATH"
V=v0.1.0
GAME=~/pgai-chomp-man-src
STATE="$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-chomp-man/release-state.md"
```

Check the starting point:

```bash
grep -A1 -E '^## (Active RC|Last Released)' "$STATE"
git -C "$GAME" tag -l 'ai_v*'
```

You should see `Active RC` followed by `none`, and `Last Released` followed by
the most recent release, `v0.0.1`. The tag list ends with `ai_v0.0.1`.

---

## Stage 1 — Deposit the requirement

```bash
scripts/intake.sh --project pgai-chomp-man \
  --file demos/chomp-man-demo/intake/v0.1.0-screen-and-movement.md
```

`intake.sh` prints the deposited path, under
`projects/pgai-chomp-man/requirements/`. The `v` prefix on the filename is
what routed it there.

## Stage 2 — PM decomposes it

On the next PM wake, discovery picks up the requirement and PM breaks it into
tasks. To skip waiting for the schedule, run the wake yourself:

```bash
scripts/wake-batch.sh --agent=pm --max-tasks=1
```

Then check the requirement and the project's tasks:

```bash
scripts/show.sh --project pgai-chomp-man --key "$V"
ls "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-chomp-man/tasks/"
```

What you should see: the requirement's `## Status` reads `running`, and its
`## PM Task` names the PM task that decomposed it. New task folders appear
beside that PM task for CM, CODER and TESTER, named
`<ROLE>-<date>-<seq>-<slug>`.

On the dashboard's visibility window (window 1), the v0.1.0 requirement shows
as running and the agent columns fill in.

## Stage 3 — CM opens the RC

```bash
grep -A1 '^## Active RC' "$STATE"
git -C "$GAME" branch --list 'ai_*'
```

What you should see: `Active RC` is now `v0.1.0`, and the game repo has a new
branch, `ai_rc/v0.1.0`, beside `ai_main`. The branch is cut from `ai_main`.
Your own `main`, if you have one, is untouched.

## Stage 4 — CODER implements on the RC branch

```bash
git -C "$GAME" log --oneline "ai_main..ai_rc/$V"
```

What you should see: one or more commits that exist on the RC branch and not
yet on `ai_main`. Each CODER task works in its own worktree, merges back into
the RC branch, and the task's `status.md` moves to `DONE`.

## Stage 5 — TESTER verifies

TESTER runs the RC's tests in a read-only checkout of the RC branch and writes
a verification report into its own task folder. The dashboard's logs window
(window 6) shows the TESTER wake and its result. The RC branch does not change
during this stage.

If TESTER files findings, CM's ship policy decides whether the RC ships with
them recorded as known issues or stops for a fix.

## Stage 6 — CM ships

```bash
grep -A1 -E '^## (Active RC|Last Released)' "$STATE"
git -C "$GAME" tag -l 'ai_v*'
git -C "$GAME" log --oneline -3 ai_main
git -C "$GAME" branch --list 'ai_rc/*'
git -C "$GAME" ls-tree --name-only ai_main release-notes/
scripts/show.sh --project pgai-chomp-man --key "$V"
```

What you should see once the release has finished:

- `Active RC` is back to `none`, and `Last Released` is `v0.1.0`.
- The tag list now ends with `ai_v0.1.0`.
- The recent `ai_main` log includes `Release v0.1.0`: the RC's commits
  squashed into one. A release-notes commit may sit on top of it.
- No `ai_rc/*` branch is left. CM deletes the RC branch after tagging.
- `release-notes/` on `ai_main` contains a notes file for v0.1.0.
- The requirement's `## Status` reads `done`.

Nothing was pushed. Because `push_to_remote = false`, CM skips every remote
step and says so in its log, on lines starting `[push_to_remote=false] skipping`.

---

## If a stage stalls

- **Nothing happens after the deposit.** Check for a HALT on the project or
  the whole kanban, and that `Active RC` was `none` before you started.
  Discovery does not pick up a new requirement while another RC is open.
- **A task shows BLOCKED.** Read its reason with `scripts/show.sh --project
  pgai-chomp-man --key` followed by the task ID from the dashboard. The block
  record names the obstacle and what clears it.
- **The RC needs to be thrown away.** Preview what an unwind would do
  before running it for real:

  ```bash
  scripts/unwind-rc.sh --project pgai-chomp-man --key "$V" --dry-run
  ```

  An RC that has already been tagged cannot be unwound; ship a patch instead.

---

## What you just learned

- One requirement becomes one RC: `open-rc` → `implement` → `verify` →
  `release`.
- `release-state.md` is where to look for what is in flight (`Active RC`)
  and what last shipped (`Last Released`).
- Every release is real git in your local repo: an `ai_rc/` branch while it is
  open, then a squash commit and an `ai_v` tag on `ai_main`.
- `push_to_remote = false` keeps the whole cycle local, and CM's log says so
  at each step it skips.
