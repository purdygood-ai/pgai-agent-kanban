# OVERWATCH — Operator View

OVERWATCH is the kanban's self-monitor. It runs on its own schedule
(independent of the RC pipeline), auto-fixes a small whitelist of
structural anomalies, and files bugs for everything else. This page is
the operator's condensed view — what fires when, what OVERWATCH will
touch, and how to stop it.

The authoritative role file is
[../team/roles/OVERWATCH.md](../team/roles/OVERWATCH.md). Anything below
that reads like a policy claim traces back there; when they disagree,
the role file wins.

## The two tiers

OVERWATCH runs on two independent cadences plus an event-driven nudge.

**Tier 1 — hourly deterministic sweep.** Plain bash. Iterates the
whitelist checks in `team/scripts/lib/overwatch-checks/`, writes the
action log in the standard format, and takes timestamped backups before
any auto-fix. Zero LLM cost. Driven by
`team/scripts/overwatch-sweep.sh`, which sweeps every project registered
in `projects.cfg` per firing. Nine of the twelve current checks are
deterministic detections with narrow auto-fixes — most anomalies clear
here without ever waking the LLM tier.

**Tier 2 — daily LLM deep-clean.** A single OVERWATCH agent wake per
day. Reads the action log since the last deep-clean plus anything Tier 1
flagged, then files quality bugs via the standard bug shape for anything
outside the whitelist. Most days it has nothing to add — Tier 2 exists
to catch repeated near-misses and ambiguous residues Tier 1's
exact-match detection cannot see.

## The on-BLOCK trigger

Between the hourly sweep and the daily deep-clean sits an event-driven
nudge. When any agent's task transitions to `BLOCKED`, the wake script's
block path fires a non-blocking `wake-now.sh --agent overwatch` before
returning. Fresh failures are inspected within seconds instead of at
the next hourly tick. Both provider siblings (`scripts/wake/claude.sh`
and `scripts/wake/codex.sh`) carry the identical hook.

The trigger is deliberately payload-free: it does not carry the blocked
task ID or the block reason. OVERWATCH's next firing scans the whole
state fresh, the same way it would on a scheduled tick. The nudge only
shortens latency; it does not change what OVERWATCH does.

Four guards keep the trigger safe: it never fires when the blocking
agent is OVERWATCH itself (loop guard); the invocation is fully
backgrounded so it cannot add a failure mode to the block path; a storm
of five blocks in one tick still produces one OVERWATCH run because
later nudges find the wake flock held and exit immediately; and `HALT`
/ `HALT_OVERWATCH` are honored at the wake, not the fire — a halted
system produces trigger-log entries but no work. See "On-BLOCK Trigger"
in [../team/roles/OVERWATCH.md](../team/roles/OVERWATCH.md) for the
full contract.

## The whitelist philosophy

OVERWATCH's auto-fix scope is a small, closed set of exact-match
detections. Anything outside the whitelist is bug-file-only. Conservative
defaults are the rule; when in doubt, OVERWATCH files a bug and stops.

The current Tier-1 surface is twelve checks: six auto-fix, three
auto-fix with bug-file fallback, one auto-kill (a leaked test-fixture
listener whose cwd is provably under the framework temp root), one
report-only (version divergence between the installed VERSION and the
dev tree's `git describe`), and one origin-touching check gated on the
per-project `push_to_remote` key. The role file's "Whitelist of Auto-Fix
Operations" section names each one; the source of truth for the module
list is
[../team/scripts/lib/overwatch-checks/](../team/scripts/lib/overwatch-checks/).

Three hard rules bound every auto-fix:

- **Never destructive.** Files are renamed (`.orphan` suffix) or backed
  up before modification. Branch deletion uses `git branch -d`
  (lowercase — refuses unmerged work).
- **Always backup-then-act.** Every state-file modification is preceded
  by a call to `overwatch_backup_file`. The backup path lands in the
  action log so the revert script can find it. If the backup fails, the
  auto-fix aborts and OVERWATCH files a bug.
- **Never modify a running task.** OVERWATCH does not edit a task's
  `status.md` while that task is `WORKING` with the per-agent flock
  held. The blocked-task check only touches tasks already in `BLOCKED`.

The operator's escape hatch when OVERWATCH does something wrong is
`team/scripts/overwatch-revert.sh`. It reads the action log entry at a
given timestamp, restores files from `backups/<TIMESTAMP>/`, and logs
the revert as its own action-log entry.

## HALT and HALT_OVERWATCH

The kanban has two distinct halt flags. They serve different purposes
and operate on different agents.

- **`$KANBAN_ROOT/HALT`** — pauses the RC chain. PO, PM, CODER,
  WRITER, TESTER, and CM all check this flag and skip their firing if
  it exists. OVERWATCH ignores `HALT` for read-only scans (it keeps
  observing and may file bugs that aid the investigation) but respects
  it for state-changing auto-fixes.
- **`$KANBAN_ROOT/HALT_OVERWATCH`** — pauses OVERWATCH only. The chain
  agents ignore this flag. OVERWATCH checks it as pre-flight Step 1
  and exits cleanly if it exists.

Operator use cases:

| Situation | Touch |
|---|---|
| Hand-editing files in `priority/` or `bugs/` and don't want OVERWATCH second-guessing the edits | `HALT_OVERWATCH` |
| Investigating a chain failure; want OVERWATCH's observations to keep landing | `HALT` |
| Full freeze | Both |
| Normal operation | Neither |

A single shared flag was rejected during design — it would force "all
or nothing," with no way to stop OVERWATCH from interfering with
manual recovery work without also going blind to the state of the
system.

## What OVERWATCH does not do

OVERWATCH is a safety net that runs underneath the chain. It never
replaces another agent.

- Does not decompose requirements (PM's job).
- Does not implement fixes to the dev tree (CODER's job).
- Does not verify acceptance criteria (TESTER's job).
- Does not open or close RCs, manage version tags, or push releases
  (CM's job).
- Does not delete bug or priority files — rename only, `.orphan`
  suffix.

If a check looks like it might fall into one of those areas, OVERWATCH
files a bug and stops.

## Where to look next

- [../team/roles/OVERWATCH.md](../team/roles/OVERWATCH.md) — the full
  role file: every check, every guard, the action-log format, the
  HALT-first protocol.
- `$PGAI_PROJECT_ROOT/overwatch/actions.log` — the append-only audit
  trail of every action OVERWATCH has taken on your install.
- [operator-troubleshooting.md](operator-troubleshooting.md) — when a
  symptom looks like OVERWATCH itself is wedged.
