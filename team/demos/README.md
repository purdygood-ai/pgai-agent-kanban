# Demos — two worked examples

These are two complete, runnable examples that show how the kanban drives real
work end-to-end. You run them yourself, entirely on your own machine — nothing
here auto-runs, nothing pushes to any remote, nothing is seeded for you. You
create the project, you deposit the work, you watch the chain build it.

There are two demos, one per workflow type:

- **`chomp-man-demo/`** — the **release** workflow. Builds a small arcade game
  (a Pac-Man-style "Chomp-Man") feature by feature, with bugs and enhancements
  dropped in between, each shipping a tagged release into a local git repo.
- **`three-bears-demo/`** — the **document** workflow. Writes a children's
  bedtime story, evolving it across several revisions, each publishing a
  document artifact.

## Which to run first

Start with **`three-bears-demo/`**. It's the document workflow — simpler, no git
repo to set up, no code to build — so it's the fastest way to see the chain wake,
decompose, work, and finalize. Then do **`chomp-man-demo/`** for the full release
lifecycle (RC branches, tests, tagged releases) and to see bugs and priorities
flow through the patch lane.

## Everything is local and safe

Both demos run with **`push_to_remote = false`** — the chain produces real
commits, real tags, and real artifacts, but never pushes to any remote. You can
delete the demo projects and their scratch repos afterward with no trace. You
are in control of every step; the chain only acts on what you deposit.

## How you drive the system (the operator commands)

You use the same operator commands for the demos that you'd use for real work:

- **`create-project.sh`** — register a new project
- **`intake.sh`** — deposit a requirement, bug, or priority (routed by filename)
- the **dashboard** (or `kanban-status.sh`) — watch the chain work
- **`show.sh`** — inspect any item

Each demo's `README.md` gives the exact commands, in order, with notes on what to
watch at each step.

## A note on the bugs and priorities

The bugs and enhancements in the Chomp-Man demo are realistic examples drawn from
an actual run. Because the agents are nondeterministic, **your build may or may
not have the exact issues the bug files describe** — that's expected. The point
of including them is to teach the *reactive loop*: when you play the game and
notice something wrong (or think of an enhancement), this is how you write it up
and feed it back to the chain. Treat them as "here's how you'd handle it," not
"this will definitely happen."
