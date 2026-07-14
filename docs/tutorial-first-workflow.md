# Tutorial: Your First Custom Workflow Type (in ~15 minutes)

_This is the worked-example companion to `creating-a-workflow.md`. That
document is the reference; this one builds a real (deliberately silly)
workflow type end-to-end so you can watch every step happen once. The
type we build is throwaway by design — delete it afterward without
ceremony._

**What we build:** a `bulletin` workflow type — a PM and a WRITER, no
git repository at all, label-dated versions, and a report as the final
product. Then we drop one requirement on it: *fetch the current Bitcoin
price and write a one-page bulletin.* The bitcoin part is the payload,
not the point — the point is seeing a type go from nothing to a
completed run.

**Two layers, one sentence each:** a workflow TYPE defines lifecycle
semantics (which agents run, how versions work, whether git is
involved, what "finished" produces). A REQUIREMENT is a document that
runs ON a type. We create one of each.

---

## Step 1 — Scaffold the type

```bash
python3 -m pgai_agent_kanban.workflows.create_new_workflow \
    --name bulletin \
    --description "Dated one-page bulletins written by WRITER; no git, no versions consumed" \
    --version-semantics label \
    --git-mode none \
    --finalize report \
    --agents pm,writer
```

The generator writes `$KANBAN_ROOT/workflows/bulletin/` with a manifest
(`status = scaffold`), eight hook stubs that fail loudly, and a
`contract_check.sh`. Nothing can route work to it yet — scaffold-status
types are discovered but refused.

## Step 2 — Implement the hooks (mostly: delete code)

A `git_mode = none`, `finalize = report` type is the MINIMAL shape —
the shipped `testing-only` plugin is your reference (it is one notch
richer: it uses read-only git; you use none). For `bulletin`, the
eight hooks reduce to:

- `wf_version_semantics` → echo `label`
- `wf_git_mode` → echo `none` (the engine skips worktree creation
  entirely — WRITER works in its task folder)
- `wf_agents` → echo `pm,writer`
- `wf_finalize` → the report hook: the terminal ticket's deliverable
  IS the finalize artifact, written to
  `projects/<name>/artifacts/<label>-<slug>.md`
- `resolve_target_version` → echo the label from the requirement
  verbatim (labels are names, not numbers — nothing is consumed)
- `pre_task` / `post_task` → no-ops (exit 0)
- `dashboard_render` → the default renderer (delete the stub body,
  return 0)

Copy the testing-only hook bodies, delete the worktree-related lines,
run `bash -n workflow.sh` until clean.

## Step 3 — Validate

```bash
scripts/validate-workflow.sh --type bulletin
```

Iterate until exit 0. The validator checks the manifest schema, hook
presence, capability coherence (e.g. `finalize = report` with a
roster whose terminal agent can write one), and the fail-closed
contract.

## Step 4 — Flip it live

Edit `workflows/bulletin/workflow.cfg`: `status = scaffold` →
`status = ready`. The dispatcher will now route real requirements to
it. (This flip is deliberately manual — a human decides when a type
is real.)

## Step 5 — A project for it

```bash
scripts/create-project.sh --project btc-watch --workflow-type bulletin
```

With `git_mode = none` the project needs no dev tree and no
repository — the registry entry and the kanban-side directories are
the whole footprint.

## Step 6 — The requirement (the fun part)

Save as `v20260714-btc-morning.md` and intake it:

```markdown
# v20260714-btc-morning — Bitcoin Morning Bulletin

## Status
ready

## Target Version
v20260714-btc-morning

## Workflow Type
bulletin

## Test Required
false

## Human Approval Required
none

## Summary
Produce a one-page dated bulletin on the current Bitcoin price.

## Goals
1. Fetch the current BTC price in USD from the free CoinGecko
   endpoint (no API key):
   `curl -s "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"`
2. Write a one-page bulletin: the price, the fetch timestamp (UTC),
   the source URL, and one sentence of context (e.g. versus a round
   number). No investment commentary.

## Deliverables
- The bulletin at the finalize location
  (`projects/btc-watch/artifacts/v20260714-btc-morning-bulletin.md`).

## Acceptance Criteria
1. The bulletin exists at the finalize location and contains a USD
   price, a UTC timestamp, and the source URL.
2. Nothing else was created or modified outside the task folder and
   the finalize artifact.

## Notes for Operator
Requires outbound network from the agent environment. If the fetch
fails, WRITER should report the failure honestly in the bulletin
rather than inventing a number.
```

```bash
scripts/intake.sh --project btc-watch --file v20260714-btc-morning.md
```

## Step 7 — Watch it run

Next PM tick: discovery selects the file (label semantics — the
filename just needs the `v<digits>-slug.md` shape), PM decomposes to
the pm→writer roster, WRITER fetches, writes, and the report lands at
the finalize location. Total wall time: a few minutes.

```bash
scripts/dashboard/show-queues.sh --details --project btc-watch
cat projects/btc-watch/artifacts/v20260714-btc-morning-bulletin.md
```

## Step 8 — Throw it away (optional, and the point)

```bash
scripts/remove-project.sh --project btc-watch
rm -rf "$KANBAN_ROOT/workflows/bulletin"
```

Custom types under `workflows/` (non-shipped names) survive upgrades
per the upgrade-survival contract — so keep it if the silly bulletin
grew on you.

---

## What you just exercised

Every layer a real type uses: the generator's refusal-and-scaffold
contract, the eight-hook surface, the validator, the ready-flip
human gate, capability-driven engine behavior (no CM tasks
materialized — the roster said so; no worktrees — git_mode said so;
no version consumed — label semantics said so), and finalize=report.
The `release` and `document` types are the same machinery with more
capabilities switched on. For the full reference — including the
contributor path that ships a type WITH the framework, tests and
all — see `creating-a-workflow.md`.
