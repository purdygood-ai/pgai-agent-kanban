# OVERWATCH — Operator View

OVERWATCH is the kanban's self-monitor. It runs on its own schedule
(independent of the RC pipeline), auto-fixes a small whitelist of
structural anomalies, triages blocked tasks, and files bugs for
everything else. This page is the operator's condensed view — what
fires when, what OVERWATCH will touch, how to read what it did, and how
to stop it.

The authoritative role file is
[../team/roles/OVERWATCH.md](../team/roles/OVERWATCH.md). Anything below
that reads like a policy claim traces back there; when they disagree,
the role file wins. The block-triage sections describe the shipped code
and its decision table directly.

## The two tiers

OVERWATCH runs on two independent cadences plus an event-driven nudge.

**Tier 1 — hourly deterministic sweep.** Plain bash. Iterates the
whitelist checks in `team/scripts/lib/overwatch-checks/`, writes the
action log in the standard format, and takes timestamped backups before
any auto-fix. Zero LLM cost. Driven by
`team/scripts/overwatch-sweep.sh`, which sweeps every project registered
in `projects.cfg` per firing. Most of the current checks are
deterministic detections with narrow auto-fixes — most anomalies clear
here without ever waking the LLM tier.

**Tier 2 — daily LLM deep-clean.** A single OVERWATCH agent wake per
day. Before it takes each project's repo lock, the wake runs block
triage for that project (see "Block triage" below), logging to
`logs/overwatch-block-triage.log` under the kanban root. It then reads
the action log since the last deep-clean plus anything Tier 1 flagged,
and files quality bugs via the standard bug shape for anything outside
the whitelist. Most days it has nothing to add — Tier 2 exists to catch
repeated near-misses and ambiguous residues Tier 1's exact-match
detection cannot see.

## The on-BLOCK nudge

Between the hourly sweep and the daily deep-clean sits an event-driven
nudge. When a wake chain blocks a task, it records that fact and keeps
going. After the chain has released the project's repo lock, it starts
block triage in the background:

```
overwatch-sweep.sh --only block-triage --lock-wait 60
```

A fresh block is triaged within seconds instead of at the next hourly
tick. The sweep runs the one block-triage check for every registered
project, and its output is appended to `logs/overwatch-trigger.log`
under the kanban root.

The nudge fires after the lock release on purpose. Block triage takes
the same repo lock before it writes anything; fired while the blocking
chain still held it, every task it tried would be refused as contended.

The nudge carries no payload: not the blocked task ID, not the reason,
not the agent. The woken sweep reads every blocked task's state fresh,
exactly as a scheduled tick would. The nudge only shortens latency; it
does not change what OVERWATCH does.

Four guards keep it safe:

- **No self-trigger.** A chain run by OVERWATCH never nudges.
- **No new failure mode.** The sweep is fully backgrounded with its
  exit status ignored, so the block path cannot fail because of it. A
  chain nudges at most once, however many tasks it blocked.
- **Halts are honored at the sweep, not the fire.** The sweep's own
  pre-flight checks `HALT` and `HALT_OVERWATCH`; a halted system logs
  the nudge and does no work.
- **Bounded lock wait.** If another chain takes the repo lock first, the
  sweep retries with backoff for up to 60 seconds, then declines and
  leaves every task unchanged. The next hourly sweep picks it up.

## One firing per project

Every OVERWATCH entry point does its work for a project under that
project's firing lock, `$KANBAN_ROOT/locks/overwatch-<name>.lock`, so
two firings never act on one project at once. The entry points are:

- the hourly sweep, for the whole of each project's sweep;
- the on-BLOCK nudge (`overwatch-sweep.sh --only block-triage`);
- the OVERWATCH wake's two scripted steps: block triage and the
  resolution stage.

The lock is not waited for. When another firing holds it, the entry
point runs nothing for that project and exits 0, leaving the work to the
next tick. It narrates the skip on stderr with the error key
`overwatch.firing_lock_held`, which names the lock file. It also appends
one row to the project's `overwatch/actions.log`:

```
<timestamp>  <entry>  <project>  firing-skipped:lock-held  none  <message>
```

`<entry>` is `overwatch-sweep`, `overwatch-wake-block-triage` or
`overwatch-wake-resolution-stage`. If the lock file cannot be set up at
all, the step says so and runs without the lock.

The firing lock is separate from the project's repo lock. The nudge's
bounded wait and triage's `block-triage-refused` row are both about the
repo lock.

## The whitelist philosophy

OVERWATCH's auto-fix scope is a small, closed set of exact-match
detections. Anything outside the whitelist is bug-file-only. Conservative
defaults are the rule; when in doubt, OVERWATCH files a bug and stops.

The current Tier-1 surface is fifteen check modules. Most auto-fix one
narrow anomaly, some with a bug-file fallback; one auto-kills (a leaked
test-fixture listener whose cwd is provably under the framework temp
root); several are report-only; one touches origin, under the push gate
described in "The push-lag check" below; and one, block triage, acts on blocked
tasks under the rules described below. The catalog in
[../team/scripts/lib/overwatch-checks/README.md](../team/scripts/lib/overwatch-checks/README.md)
names each module with its auto-fix path and is the source of truth for
the module list.

Three hard rules bound every auto-fix:

- **Never destructive.** Files are renamed (`.orphan` suffix) or backed
  up before modification. Branch deletion uses `git branch -d`
  (lowercase — refuses unmerged work).
- **Always backup-then-act.** Every state-file modification is preceded
  by a call to `overwatch_backup_file`. Each backup is keyed on its
  source's own path, so two different files never share a backup (see
  "Backups and revert" below). Every backup path a fix took lands in the
  action log so the revert script can find it. If the backup fails, the
  auto-fix aborts and OVERWATCH files a bug.
- **Never modify a running task.** OVERWATCH does not edit a task's
  `status.md` while that task is `WORKING` with the per-agent flock
  held. Block triage only touches tasks already in `BLOCKED`.

### The leaked-listener check

`check-leaked-listeners` is the one check that kills a process. It looks
for test-fixture listeners left running: a process whose working
directory is under the framework temp root (`PGAI_AGENT_KANBAN_TEMP_DIR`)
and that holds a listening TCP socket.

- **One scan per sweep.** The sweep scans `/proc` once, before its project
  loop, and hands the result to every project's check. The sweep's own
  processes are never recorded.
- **Attributed to a project.** A listener under
  `<temp root>/projects/<name>/` belongs to that project, and to the task
  whose worktree holds it. One elsewhere under the temp root belongs to the
  project whose `tasks/` holds a task ID named in its path. Each project's
  check acts only on its own listeners; one that belongs to no registered
  project is reported and left alone.
- **Two exemptions.** A listener inside a `WORKING` task's worktree or
  scratch directory is skipped: that task may still be using it. So is a
  listener younger than `[overwatch] leaked_listener_min_age_seconds`. Each
  skip is one stderr line.
- **The rest get `SIGTERM`**, one `overwatch_log_action` row each, naming
  the task, the age and the threshold. No file changes, so no backup is
  taken. `--dry-run` logs what it would kill and kills nothing.

`leaked_listener_min_age_seconds` is read from `kanban.cfg` only, in the
`[overwatch]` section. It takes a whole number of seconds and defaults to
`3600`. Any other value is reported on stderr and the default is used.

### Backups and revert

Backups live under the project's `overwatch/backups/<TIMESTAMP>/`, where
`<TIMESTAMP>` is the firing's timestamp, so one firing's backups sit
together. Inside it, each backup is stored at a path built from its
source:

```
overwatch/backups/<TIMESTAMP>/project-root/<path relative to projects/<name>/>
overwatch/backups/<TIMESTAMP>/absolute/<absolute path without its leading />
```

The second form is for a source outside the project root. Because the
key is the whole source path, two tasks' `status.md` files backed up in
one firing land in two files. A second backup of the same source in the
same firing keeps the first copy, which holds the file's pre-firing
content. A caller may group its backups one level deeper. Block triage
does this: `<TIMESTAMP>/<task id>/project-root/...`.

The operator's escape hatch when OVERWATCH does something wrong is
`team/scripts/overwatch-revert.sh`. Given a timestamp, it reads the
matching action-log rows and copies each backup back to the source
path recorded in the backup's own path. A row that lists several
backups has every one restored. Rows are undone newest first, so a
file that two actions in one firing touched ends at its pre-firing
content. The revert logs itself as its own action-log entry. A backup
taken before path keying, stored under its bare file name, is still
restored by locating the original from the row's target.

## The push-lag check

`check-push-lag` is the one Tier-1 check that touches origin. It looks
for push lag on the project's own refs in its dev tree: the local
`<branch_prefix>main` ahead of its origin counterpart, or local
`<branch_prefix>vX.Y.Z` release tags that origin does not have. With
`branch_prefix = ai_` those are `ai_main` and `ai_v1.2.3`; with no
prefix, `main` and `v1.2.3`. In a repository shared with another lane,
that lane's main branch and tags are never compared and never pushed. A
`branch_prefix` that cannot be read skips the check for that tick.

Before it runs any git command, the check asks the shared push-decision
resolver (`pp_push_decision`, over `pgai_agent_kanban.lib.push_decision`)
about the project. The resolver answers `push`, `no-push` or
`unresolved`.

**It pushes only when all four of these hold:**

1. The project is registered in `projects.cfg` (a `[project:<name>]`
   section).
2. The project's own `project.cfg` exists under `projects/<name>/`. A
   legacy `PROJECT.cfg` also counts.
3. That file names a `dev_tree_path` under `[project]`. The check works
   on that dev tree and nowhere else. An inherited `PGAI_DEV_TREE_PATH`
   is never consulted, because it names one global checkout, not the
   project's.
4. `push_to_remote` in that file is explicitly `true`. The check
   treats an absent key, or any other value, like `false`. The resolver
   still answers `push` for an absent key, but the check pushes nothing
   on that answer.

Even then, the push runs under the HALT-first protocol and only while no
agent holds the project's per-repo lock. If the lock is held, the push
waits for the next tick.

**What each answer does:**

| Resolver answer | When | What the check does |
|---|---|---|
| `push`, with `push_to_remote = true` | All four conditions hold | Pushes `<branch_prefix>main` and each missing release tag to origin, each by its explicit ref (`refs/heads/...`, `refs/tags/...`). Each push is logged to the action log as `pushed-main-to-origin` or `pushed-tag-to-origin`, or as `push-main-failed` or `push-tag-failed` |
| `no-push`, or `push` with `push_to_remote` absent | Conditions 1–3 hold and `push_to_remote` is not `true` | Treats local-ahead state as by design. It still reads origin's tag list to measure the lag, but pushes nothing and changes nothing. When it finds lag, it logs `staged-by-design` to the action log, naming the setting it read |
| `unresolved` | Condition 1, 2 or 3 fails | Runs no git command at all, so no `ls-remote` and no push, and contacts no remote. It prints one stderr line with the status `skipped-unresolved-project` and exits 0. Nothing goes to the action log |

The `skipped-unresolved-project` line names the project and the missing
piece:

```text
check-push-lag: skipped-unresolved-project: project '<name>': missing <piece>: <detail>; nothing pushed and no remote contacted
```

`<piece>` is `registration`, `project-cfg` or `dev-tree-path`. It is
`resolver` when the resolver itself could not run. The gate fails
closed: a resolver that cannot answer reads as `unresolved`, never as
`push`. A sandbox root built by a test, a project with no git, and a
half-built config all land here, so none of them can steer a push at a
real checkout.

The CM push watchdog, `scripts/cm/push-watchdog.sh`, decides through
the same resolver and reads the same prefixed release tags. Unlike this
check, it takes the resolver's answer as given, so an absent
`push_to_remote` still means push there. For an unresolved project it
logs the same `skipped-unresolved-project` line to
`projects/<name>/logs/cm-push-watchdog.log`. For
`push_to_remote = false` it logs a `[push_to_remote=false]` line. In
both cases it skips the project before any git command.

## Block triage

When a task blocks, OVERWATCH reads why from the task's Block Record.
It fixes the simple cases deterministically and leaves the hard ones
blocked for a human with a one-paragraph diagnosis. **A fix is either
done and verified, or the task stays blocked.**

Three callers run the same check (`check-block-triage.sh`):

| Caller | When | Output |
|---|---|---|
| The hourly sweep | Every Tier-1 firing | `projects/<name>/logs/overwatch/sweep.log` |
| The on-BLOCK nudge | Seconds after a chain blocks a task | `logs/overwatch-trigger.log` |
| The OVERWATCH agent wake | Before the wake takes each project's repo lock | `logs/overwatch-block-triage.log` |

All three run under the project's firing lock (see "One firing per
project"), so two of them never triage one project at once. Every write
any of them makes is recorded in the project's `overwatch/actions.log`
(see "Reading block triage as an operator").

### The Block Record

Every site that blocks a task writes a `## Block Record` section to the
task's `status.md` through one helper. It is ten `key: value` lines,
always in this order (the values here are an example):

```
## Block Record
reason_code: per_task_timeout
owner_role: CODER
attempts: 0
first_blocked_at: 2026-10-04T15:43:09+00:00
last_blocked_at: 2026-10-04T15:43:09+00:00
cooldown_until: none
evidence: worktree=gone; branch=ai_feature/example; ahead=2; merged=no; batch_log=logs/agents/coder-batch-example.log#1200-4800; diag=none
resume: fresh
requeues: 0
requeue_reasons: none
```

| Field | Meaning |
|---|---|
| `reason_code` | Why the task blocked. One of the closed vocabulary below. |
| `owner_role` | The role whose work the task is: `CODER`, `WRITER`, `TESTER`, `CM` or `PM`. |
| `attempts` | Triage attempts made in this block epoch so far. A block write never changes it. |
| `first_blocked_at` | When this block epoch began. Triage keys its "never act twice" rule on it. |
| `last_blocked_at` | When the latest block was written. |
| `cooldown_until` | No triage action before this time; `none` for no window. |
| `evidence` | `; `-joined `key=value` pointers: `worktree=<path\|gone>`, `branch=<name\|none>`, `ahead=<n>`, `merged=<yes\|no>`, `batch_log=<path>#<from>-<to>`, `diag=<log>:<line>`. `none` when the site had none. A site never invents a value; it writes the placeholder instead. |
| `resume` | `branch` re-dispatches onto the existing feature branch, keeping its commits; `fresh` starts a new one. |
| `requeues` | How many times triage has returned the task to its owner over the whole life of this record, across every re-block and block epoch. Triage compares it with `[overwatch] requeue_budget` (see "The requeue budget"). A block write never changes it. |
| `requeue_reasons` | The `reason_code` of each of those requeues, oldest first, joined with `, `. `none` before the first. |

`requeues` and `requeue_reasons` are optional on read. A record written
before they existed reads as `requeues: 0` and `requeue_reasons: none`,
and its next write adds both lines.

`resume` is acted on at the next dispatch. With `resume: branch`, the
task worktree is attached to the existing feature branch with no
`branch -D`. Only `resume: fresh` (the default) may delete and recreate
the branch.

#### The reason_code vocabulary

The vocabulary is closed: a record with any other code, or none, is
escalated to a human. To list the accepted codes, run
`python3 -m pgai_agent_kanban.overwatch.block_record codes` from the
kanban root.

| reason_code | Written when |
|---|---|
| `per_task_timeout` | The per-task timeout killed the agent at the configured `[wake] max_task_seconds`. A kill at a shorter limit set by the wake's remaining batch or project budget writes no record: the task returns to `BACKLOG` with `Needs Human: no`. |
| `provider_refusal` | The zero-progress ceiling was reached and the latest dispatch ran under 60 seconds, or its batch-log tail shows an authentication, model-not-found or usage-limit signature. |
| `zero_progress` | The zero-progress ceiling was reached otherwise. |
| `transient_api_error` | The wake's transient-error path or the transient check blocked the task on a provider error. |
| `gate_merge_not_found` | The completion gate found the feature branch but no merge of it. |
| `gate_dirty_worktree` | The completion gate found uncommitted changes in the worktree. |
| `gate_empty_commit` | The completion gate found branch commits that carry no change. |
| `worktree_create_failed` | The task worktree could not be created and the source branch exists. |
| `ship_policy_halt` | Reserved for a block held by a CM ship-policy HALT. No wake block site writes it today; the classifier row applies to any record that carries it. |
| `missing_readme` | The task folder has no `README.md`. |
| `invalid_assignment` | The task README assigns a role or owner the wake cannot dispatch. |
| `invalid_effort_config` | The effort configuration the task resolves to is invalid. |
| `invalid_state` | `status.md` carries a `## State` outside the six canonical states. |
| `branch_prefix_failed` | The project's branch prefix could not be resolved. |
| `workflow_type_unparseable` | The task README's `## Workflow Type` cannot be parsed. |
| `plugin_load_failed` | The workflow-type plugin the task names failed to load. |
| `source_branch_missing` | The source branch the task README names does not exist. |
| `gate_merge_evidence_not_found` | The completion gate could not find the feature branch to check for a merge. |
| `agent_operator_data_action` | An agent blocked its own task asking for an allowlisted operator data action, with typed arguments. |
| `agent_unclassified` | An agent blocked its own task without naming a reason code. |

The last two are agent self-blocks, written by the status-writer tool.
An agent passes `--reason-code` on a `BLOCKED` write; with no code the
record says `agent_unclassified`. `agent_operator_data_action` also
needs typed `--action-args`, stored in the `evidence` line. The shape,
with placeholders in angle brackets and capitals:

```
bash scripts/task-status.sh --project <name> --key <TASK-ID> \
    --state BLOCKED --needs-human yes --reason-code agent_operator_data_action \
    --action-args verb=restamp-fixed-in project=<name> version=vX.Y.Z bugs=BUG-NNNN,BUG-NNNN
```

A provider's usage limit is normally not a block at all. The wake
checks for it before it classifies an exit as a transient error or as
zero progress, records a timed pause, and returns the task to
`BACKLOG` — see "The usage-limit pause" below. A usage-limit exit
therefore normally neither spends `## Transient Retry` nor reaches
`provider_refusal`.

#### Lifecycle

- **Created** by the first block write, with `attempts: 0`,
  `cooldown_until: none`, `resume: fresh`, `requeues: 0`,
  `requeue_reasons: none`, and both timestamps set to now.
- **Updated** on each re-block: `reason_code`, `owner_role`,
  `evidence` and `last_blocked_at` are replaced; `first_blocked_at`,
  `attempts`, `cooldown_until`, `resume`, `requeues` and
  `requeue_reasons` are kept. Triage updates `attempts`,
  `cooldown_until` and `resume` when it acts. When its action returns
  the task to its owner, triage also records a requeue: `requeues` goes
  up by one and the block's `reason_code` is appended to
  `requeue_reasons`.
- **Preserved across promote.** A task returned to `BACKLOG` keeps its
  record, so the next block continues the same history.
- **Preserved across a budget stop.** A wake whose remaining batch or
  project time budget stops an agent before `[wake] max_task_seconds`
  returns the task to `BACKLOG` with `Needs Human: no`. That stop is not
  a block: it writes no `per_task_timeout` record and creates no record.
  A record the task already carries stays in place, `requeues` and
  `requeue_reasons` included, so the task's next block keeps counting
  from it. The one field the stop changes is `resume`, which it sets to
  `branch` so the next dispatch keeps the stopped agent's commits. A
  later re-block keeps that value, and so does a triage action whose row
  names no `resume`; only a row that names `fresh` sets it back. A task
  with no record gets a `## Resume Mode` section instead (below).
  Block-storm detection never sees the stop, because it counts `BLOCKED`
  tasks, not records.
- **Removed** only when the task reaches `DONE` or `WONT-DO`.

**`## Resume Mode`.** A budget stop on a task with no Block Record
writes a wake-owned `## Resume Mode` section to its `status.md`, with
the single-word body `branch`; no agent writes it. The wake's worktree
stage reads the section only for a task with no Block Record: `branch`
attaches the next dispatch to the existing feature branch at its tip,
and no section, or any other body, starts fresh from the source branch.
Once a block writes a record, the record's `resume` field decides and
the section is ignored. It needs no clean-up: nothing removes it, and it
is read only when the task is dispatched, so it is inert once the task
is `DONE`.

A refused record write never stops the block itself: the task still
goes `BLOCKED`, only without a classifiable record. Such tasks are
listed, never touched, by the report-only `check-blocked-tasks`
module, and `lint_block_record_sites.py` reports any `BLOCKED` write in
the tree that skips the helper.

#### Evidence files

Triage writes its evidence under the task's
`artifacts/block-evidence/` before it changes anything else. Files are
never overwritten (a taken name gets a `-2`, `-3` suffix) and are kept
with the task.

| File | Written when |
|---|---|
| `triage-<UTC>-<outcome>.json` | Every triage write: the decision, the Block Record as read, the verification verdict and the backup paths. |
| `verification.json` | Each action appends a `pending` entry, settled `passed`, `failed` or `unverified` on the task's next block. |
| `commits-ahead-<UTC>.txt` | A `per_task_timeout` requeue: the commits the feature branch holds ahead of the source branch. |
| `dirty.patch`, `untracked.txt`, `dirty.patch.sha256` | A `gate_dirty_worktree` requeue: the uncommitted changes, saved and never committed. |
| `worktree-residue-<UTC>.txt` | A `worktree_create_failed` action: the stale worktree entries, listed before they are removed. |
| `verb-<verb>-<UTC>.txt` | An `agent_operator_data_action` run: the verb's output and its re-run's. |

### The classifier table

The decision table is
[../team/pgai_agent_kanban/overwatch/block-classifier.yaml](../team/pgai_agent_kanban/overwatch/block-classifier.yaml).
For a blocked task, the row keyed by the record's `reason_code` says
what triage may do. The classifier only decides; the executor acts.

The decision **fails closed**. The first rule that applies wins:

1. No record, an unknown `owner_role`, or an unreadable `evidence`
   line: escalate.
2. A `reason_code` that is absent or has no row: escalate.
3. A row whose action is `escalate`: escalate.
4. `attempts` at or above the row's maximum: escalate.
5. Now is before `cooldown_until`: not yet (nothing written).
6. A row that waits first, seen for the first time: record the first
   `cooldown_until` and wait.
7. The row's preconditions all hold: the row's action. Otherwise each
   fallback in turn; otherwise escalate.

The executor applies one more rule after the classifier has chosen an
action. If the action is anything but `recompute_gate` and the record's
`requeues` has reached `[overwatch] requeue_budget`, it escalates
instead of acting (see "The requeue budget").

A table that fails to load or validate also escalates every task.

Every shipped row (cool-downs are `cooldown_s` in the YAML; "×2" means
the window doubles after each attempt, up to the cap):

| reason_code | action | max / cool-down | verification | stays human when |
|---|---|---|---|---|
| `per_task_timeout` | `requeue_owner`: snapshot the commits ahead to `block-evidence/`, `resume: branch`, note "previous attempt was killed at Ns" | 1 / 1800 s (30 min) | `next_outcome_not_timeout` | a second timeout; the snapshot cannot be taken |
| `provider_refusal` | `requeue_owner` after a first cool-down; resets the `## Zero Progress` count to 0 | 3 / 900 s ×2, cap 3600 s (15, 30, 60 min); waits first | `zero_progress_not_readvanced` | still refusing after 3 — the diagnosis names a fleet-level cause (authentication, model or usage limit) |
| `zero_progress` | `requeue_owner` with a note | 1 / 1800 s (30 min) | `zero_progress_not_readvanced` | it repeats |
| `transient_api_error` | `requeue_owner`, performed by the transient-error handlers, not by triage | 3 / 300 s ×2, cap 1200 s (5, 10, 20 min); waits first | none | the `## Transient Retry` ceiling is reached |
| `gate_merge_not_found` | (a) branch already an ancestor of the source: `recompute_gate` read-only, then `DONE`; (b) otherwise, a clean `merge-tree` and a CODER/WRITER owner: `requeue_owner`, `resume: branch`, note "merge only, then re-complete" | 1 / 0 | `gate_passes` | a merge-tree conflict (the diagnosis names the paths); another owner role; a second refusal |
| `gate_dirty_worktree` | `requeue_owner`: save `dirty.patch` and `untracked.txt` to `block-evidence/` (never commit), `resume: branch`, note names the patch and its hash | 1 / 0 | `gate_passes` | the worktree is gone or no longer dirty; a second refusal |
| `gate_empty_commit` | `requeue_owner`, `resume: branch`, note quotes the empty commits' shas | 1 / 0 | `gate_passes` | no sha can be quoted; a second refusal |
| `worktree_create_failed` | `prune_worktree_then_requeue`: list the stale worktree in `block-evidence/`, `git worktree remove` (never `--force`), `git worktree prune`, requeue | 1 / 300 s (5 min) | `next_create_succeeds` | the source branch is missing; the residue holds commits no other ref holds, sits outside the worktree base, is locked or has changes. Nothing is removed in these cases |
| `ship_policy_halt` | `defer_to_resolution_stage`: hand the block to the existing resolution stage, which decides under its own rules | the stage's own ceiling / 0 | `stage_decision_event` | the stage declines; the project's `HALT` is not a CM ship-policy HALT |
| `agent_operator_data_action` | `run_allowlisted_verb`: run the verb in-process with validated typed arguments, re-run it, then requeue | 1 / 0 | `rerun_reports_no_changes` | any argument is invalid; the verb is not allowlisted; the remedy is prose only; a target does not check out; the re-run reports changes |
| `missing_readme`, `invalid_assignment`, `invalid_effort_config`, `invalid_state`, `branch_prefix_failed`, `workflow_type_unparseable`, `plugin_load_failed`, `source_branch_missing`, `gate_merge_evidence_not_found`, `agent_unclassified` | `escalate` | 0 / 0 | none | always |

The one allowlisted verb today is `restamp-fixed-in`, with arguments
`project` (a registered project), `version` (`vX.Y.Z`) and `bugs`
(comma-separated `BUG-NNNN` ids, each existing and `done`). It stamps
`## Fixed In` on each named bug whose value is empty and never
overwrites a set value.

To see what triage would decide for one task without changing
anything:

```
cd "$KANBAN_ROOT" && python3 -m pgai_agent_kanban.overwatch.block_classifier classify \
    projects/<name>/tasks/<TASK-ID>/status.md
```

It prints the decision as JSON and exits 0, whatever it decided.

### Verification and block epochs

A block epoch is keyed on the record's `first_blocked_at`. When a task
that triage requeued blocks again, the pending entry in
`verification.json` is checked against the new block:

- **passed** — the action worked and the new block is a new incident.
  The epoch restarts (`attempts` 0, `cooldown_until` none) and the new
  block is classified on its own. `requeues` and `requeue_reasons` are
  kept: an epoch restart never resets the requeue budget.
- **failed** — the same failure came back. If the row still allows an
  attempt (a provider that keeps refusing), the row decides as usual;
  otherwise the task escalates, and the diagnosis names the failed
  verification.
- **unverified** — the row has no verification; the row decides as
  usual.

The same block is never acted on twice, and an escalated epoch is never
acted on again.

#### The requeue budget

A verification passes on any block other than the one its action
addressed. So a task whose blocks alternate between reasons passes
every verification and restarts the epoch each time. A task too large
for the per-task timeout, for example, may block on `per_task_timeout`,
then on `gate_dirty_worktree`, then on `per_task_timeout` again. Each
row's own maximum bounds only one epoch, so on its own nothing would
stop that loop.

The requeue budget is the cap that survives epoch restarts. The Block
Record's `requeues` counts every time triage returned the task to its
owner over the record's whole life. It survives re-blocks, epoch
restarts, a return to `BACKLOG` and a wake's budget stop, and is reset
only when the task reaches `DONE` or `WONT-DO` and the record is removed
(see "Lifecycle"). A task whose dispatches alternate between a real
per-task timeout and a budget stop therefore still reaches the budget. `[overwatch] requeue_budget` caps it (default
`3`; see "Configuration").

When a row would act and `requeues` has already reached the budget,
triage escalates instead. The task stays `BLOCKED`, `Needs Human`
becomes `yes`, and the `## OVERWATCH Diagnosis` cause gives:

- how many requeues were made, and the budget;
- every reason code seen, oldest first, from `requeue_reasons` plus the
  block that found the budget spent (for example
  `per_task_timeout -> gate_dirty_worktree -> per_task_timeout`);
- when more than one code appears, which codes the blocks alternate
  between.

Its first option tells you to find the cause the successive blocks
share and to split the task or fix that cause.

What counts:

- Every action that returns the task to its owner (`requeue_owner`,
  `prune_worktree_then_requeue`, `run_allowlisted_verb`) is a requeue.
- `recompute_gate` closes the task `DONE` rather than requeueing it,
  so the budget never refuses it and never counts it.
- Requeues made by the transient-error handlers, not by triage, are not
  counted.

The default of `3` is no lower than any row's maximum, so a task that
blocks again and again for one reason still reaches its row's own
ceiling first.

### Safety rules

Block triage stays inside OVERWATCH's swim lane.

- **No repository-mutating git.** Triage never runs `git merge`,
  `git commit`, `git checkout`, `git push` or `git branch -D`. Every
  git call goes through a read-only wrapper. The one exception is
  clearing stale worktree residue: `git worktree remove` without
  `--force`, then `git worktree prune`, only inside the worktree base
  and only when every commit the residue holds exists on another ref.
  Neither deletes a branch or a commit.
- **Never `reset_item`.** Triage never resets a task, which would wipe
  its artifacts, logs and branch.
- **Requeue to the owner for git work.** Where a fix needs git, triage
  requeues the task to its owning role with a note and `resume: branch`.
  That role does the git work on its next wake.
- **Fail closed.** An unknown code, an unmet precondition, unreadable
  evidence, or a table that does not validate escalates. A handler
  that refuses at run time escalates instead of acting.

Every triage write to a task goes through one envelope, in this order:

1. **HALT-first, under the lock.** Everything runs inside
   `overwatch_halt_first_fix`. `HALT_OVERWATCH` (flag file or env var)
   or a held repo lock leaves the task unchanged, logs a
   `block-triage-refused` row, and stops the run.
2. **Back up.** `status.md` and the queue file are backed up, grouped
   under `overwatch/backups/<firing timestamp>/<task id>/` and keyed on
   each file's own path within it (see "Backups and revert").
3. **Evidence first.** The executor writes its evidence file, then its
   own writes: the Block Record fields, a `## Triage Note` line appended
   to the task README (the next dispatch reads it), and on escalation
   `Needs Human: yes` with an `## OVERWATCH Diagnosis`.
4. **One state mutation**, only through `promote_blocked_to_backlog`
   (back to `BACKLOG`) or `transition_task_state` (a recomputed gate
   that passes goes to `DONE`).
5. **Log** `block-triage:<reason_code>:<action>` with
   `overwatch_log_action`, listing both backups in the row's backup
   column so a revert restores the status file and the queue file.

A `ship_policy_halt` deferral takes steps 1, 3 and 5 only. Triage
writes nothing of the task; every write that follows is the
resolution stage's own.

### Configuration

Five keys govern block triage and the dispatch gate.

| Key | Accepted | Default | Read from (highest first) |
|---|---|---|---|
| `[overwatch] block_triage` | `true` \| `false` | `true` | project's `project.cfg`, then `kanban.cfg`, then the default |
| `[overwatch] requeue_budget` | a non-negative whole number | `3` | project's `project.cfg`, then `kanban.cfg`, then the default |
| `[wake] block_storm_window` | whole seconds | `3600` | `BLOCK_STORM_WINDOW` env var at invocation, then `kanban.cfg`, then the default |
| `[wake] block_hold_cap` | whole seconds | `3600` | `BLOCK_HOLD_CAP` env var at invocation, then `kanban.cfg`, then the default |
| `[wake] systemic_block_reasons` | Block Record codes, comma- or space-separated, or `none` | `invalid_effort_config,workflow_type_unparseable,plugin_load_failed,branch_prefix_failed,invalid_assignment` | `SYSTEMIC_BLOCK_REASONS` env var at invocation, then `kanban.cfg`, then the default |

**`block_triage = false`** keeps triage to records and diagnoses.
Block Records are still written and escalations still write
`Needs Human: yes` and the diagnosis; no state, queue or README
mutation happens. Absent or blank in both files resolves to `true`;
any value other than `true` or `false` is refused loud, and triage
changes nothing for that project.

**`requeue_budget`** caps how many times triage may return one task to
its owner over the life of its Block Record (see "The requeue budget").
Absent or blank in both files resolves to `3`. `0` requeues nothing:
every block that would be requeued escalates. Any value that is not a
whole number, such as a sign, a unit, a decimal point or a word, is
refused with the error key `block-triage.bad-requeue-budget`. Triage
then plans nothing for that project. The refusal names the value and
the file, and lands in the caller's log (see the callers table under
"Block triage").

**`block_storm_window`** and **`block_hold_cap`** must be decimal
digits only; anything else stops the wake's config load with a remedy
naming the fix.

**`systemic_block_reasons`** left blank or removed means the default
list; `none` empties it, so only a storm holds dispatch. A code outside
the vocabulary is warned about on the wake's stderr and ignored.

### The scoped dispatch gate

A human-gated block (`BLOCKED` with `Needs Human: yes`) no longer stops
dispatch for the whole project by itself. Before each wake dispatches,
it classifies the project's human-gated blocks, across every role's
queue:

- **Systemic** — a block storm (two or more share one `reason_code`
  with `last_blocked_at` times inside `block_storm_window` measured back
  from now), or any one carries a code on `systemic_block_reasons`. The
  wake holds task dispatch for every role in the project and logs "not
  dispatching new tasks until" the time the hold lifts.
- **Task-local** — anything else, including a block with no readable
  Block Record. It holds only the tasks that name it as a prerequisite,
  which their own `WAITING` state already does. Unrelated work keeps
  flowing.

The window is measured back from now, so a storm ends on its own: two
timeouts a minute apart are a storm until the older of them falls out
of the window, and two timeouts a day apart are two task-local blocks.
The agent catch-all `agent_unclassified` never forms a storm — two
unrelated agent self-blocks close together are two task-local blocks.
A block whose `last_blocked_at` does not parse cannot be dated, so it
never holds.

**Every hold is capped.** Storm or systemic code, a hold lifts by
itself no later than `block_hold_cap` seconds after the block that
began it, whether or not anyone has resolved the blocks. A block that
arrives during a hold does not extend it past the cap; one that arrives
after the hold lifted begins a new hold. Resolving the blocks lifts it
sooner.

The held wake names the lift time in its log line, and the `why` it
gives ends with the cap. An example, with illustrative values and the
task list elided:

```
project example: 2 BLOCKED task(s) need human attention; systemic reason_code=per_task_timeout: 2 human-gated blocks share reason_code per_task_timeout within block_storm_window=3600s of now: <task list>; the hold lifts at 2026-01-01T11:00:00+00:00 (block_hold_cap=3600s) unless resolved sooner -- not dispatching new tasks until 2026-01-01T11:00:00+00:00
```

The gate never holds OVERWATCH, whose wake is what triages the blocks.
While a project is held, the `WAITING` and `BLOCKED` re-checks and PM
intake still run. To print the built-in window, hold cap and systemic
list, run `python3 -m pgai_agent_kanban.overwatch.block_storm defaults`
from the kanban root.

### The usage-limit pause

When a provider account reaches its usage limit, every dispatch to that
provider fails the same way until the limit resets. The wake treats
that as a timed, fleet-wide pause rather than a task failure.

**What triggers it.** An agent exits with its task still `WORKING`,
and one of the last 50 lines of its log opens with the provider CLI's
own usage-limit message. Only leading whitespace and an optional
`Error:` or `API Error:` label may come before it. Matching ignores
case, and these openings count:

- `Claude usage limit reached` or `Claude AI usage limit reached` (for
  example `Claude AI usage limit reached|<epoch>`)
- `You've hit your limit` or `You have reached your usage limit`, with
  `usage`, `weekly`, `daily`, `session` or `opus` allowed before
  `limit`
- `5-hour limit reached`, or any other number of hours
- `Weekly limit reached`, `Daily limit reached`, `Session limit
  reached` or `Opus limit reached`
- the error codes `usage_limit_exceeded` and `usage_limit_reached`

Nothing that sits mid-line counts. The log tail also holds the agent's
own closing message, so a sentence that mentions the usage limit, the
usage-limit pause or `usage_limit_pause_seconds`, or that quotes a
provider message after a bullet or other text, records no pause. An
agent message that quotes the provider's message at the start of a
line of its own, in a code block for example, does still match.
Billing failures such as `insufficient_quota` or a credit balance that
is too low are not matched, because they do not reset on a clock.

**How long it lasts.** Until the reset time the output states, when it
states one the wake can read: an epoch time, an ISO-8601 time, a
relative delay ("try again in 2h 30m"), or a clock time with an
optional date and IANA zone ("resets 3pm (America/New_York)"). A stated
time already past, or more than eight days ahead, is not trusted.
Without a trusted time the pause lasts the configured default:

| Key | Accepted | Default | Read from (highest first) |
|---|---|---|---|
| `[wake] usage_limit_pause_seconds` | whole seconds | `3600` | `USAGE_LIMIT_PAUSE_SECONDS` env var at invocation, then `kanban.cfg`, then the default |

A second usage limit recorded while a pause is in force keeps the later
of the two end times.

**What it does to the task.** Every usage-limit attempt increments
the task's `## Usage Limit Requeue` record in `status.md`, which holds
`count`, `limit`, `signature` and `resumes_at`. What happens next
depends on the count since the task last moved under its own power:

- **Requeues 1 to 3.** The task returns to `BACKLOG` with a
  `## Summary` that names the pause and the requeue count (for example
  `usage-limit requeue 2/3`). These attempts count against no retry
  budget: `## Transient Retry` and `## Zero Progress` stay unchanged,
  and no Block Record is written, so triage never sees them.
- **Requeue 4 onwards.** The wake still records the pause, but it now
  counts the attempt as a zero-progress cycle with reason code
  `usage_limit_requeue`. Below `[wake] zero_progress_cap` the task
  returns to `BACKLOG` with `## Zero Progress` advanced. At the cap it
  goes `BLOCKED` with `Needs Human: yes`, the error key
  `wake.usage-limit-requeue.zero-progress-cap` and a Block Record.
  This way a task cannot cycle through usage limits forever.

The bound of 3 is fixed and has no config key, because
`[wake] zero_progress_cap` already sets how many counted cycles follow
it. A cycle in which the task's own state moves resets the record to
`none`.

**What it does to dispatch.** Every role in every project stops
dispatching to that provider at its next task boundary, OVERWATCH
included — its wake dispatches to the same provider. Each held wake
refuses dual-channel with the key `wake.dispatch_paused_usage_limit`.
The first wake to find the pause over removes it and dispatch resumes.

**The marker.** The pause is one file per provider,
`<kanban root>/provider-pause/<provider>.pause`, holding `key=value`
lines: `provider`, `paused_at`, `resumes_at`, `source` (`stated` or
`default`), `signature`, `task` and `project`. Removing the file
resumes dispatch early; the refusal's remedy is that `rm -f`.

**How it is narrated.** The wake log carries one line when the pause is
recorded, one per held wake while it lasts, and one when it ends. A
recording past the bound uses the second form below instead of the
first; the zero-progress cycle it counts writes its own line as well.
Shown here with illustrative values:

```
provider claude usage limit reached (<signature>); fleet-wide dispatch pause recorded until 2026-01-01T15:00:00+00:00 (stated reset time) -- task <task id> returned to BACKLOG, no retry counted (usage-limit requeue 1/3)
provider claude usage limit reached (<signature>); fleet-wide dispatch pause recorded until 2026-01-01T15:00:00+00:00 (stated reset time) -- task <task id> already requeued for a usage limit 3 times since it last moved; counting this attempt as a zero-progress cycle
project example: provider claude is paused after reaching its usage limit (<signature>) -- not dispatching any task until 2026-01-01T15:00:00+00:00 (stated reset time)
provider claude usage-limit pause ended (was until 2026-01-01T15:00:00+00:00) -- dispatch resumes
```

## Reading block triage as an operator

### Finding triage rows in actions.log

Each project's `projects/<name>/overwatch/actions.log` is append-only,
one tab-separated line per action:

```
<timestamp>  check-block-triage  <task id>  <action>  <backups>  <detail>
```

The `<backups>` column is machine-readable: every file the action backed
up, comma-separated. A triage write lists two, the `status.md` backup
first and then the queue-file backup. A row that backed nothing up
(a deferral, a refusal, a dry run) says `none`. `overwatch-revert.sh`
restores every backup the column lists.

Block triage writes three kinds of row:

| Action column | Meaning |
|---|---|
| `block-triage:<reason_code>:<action>` | Triage wrote to the task. The detail column says what, and also names the queue-file backup. |
| `block-triage-refused` | A safety refusal: `HALT_OVERWATCH`, or the repo lock held (the row names the holder). The task is unchanged. |
| `block-triage-dry-run` | A `--dry-run` run's narration. Nothing was written. |

The `<action>` part of a write row says what was done:

- `requeue_owner`, `prune_worktree_then_requeue`, `run_allowlisted_verb`
  — the task went back to `BACKLOG` for its owner.
- `recompute_gate` — the gate was recomputed and the task closed `DONE`.
- `defer_to_resolution_stage` — the block went to the resolution stage;
  the detail reports its decision.
- `escalate` — the task stays `BLOCKED` with a diagnosis. This
  includes a requeue the requeue budget refused.
- `not_yet` — the first cool-down of a wait-first row was recorded.

A task triage looked at and left alone — cooling down, already
escalated, or left to the transient-error handlers — writes no row; the
check's own log narrates it as `skip`.

To list them:

```
grep -F 'block-triage' projects/<name>/overwatch/actions.log
grep -F 'block-triage:' projects/<name>/overwatch/actions.log | grep -F ':escalate'
```

The task's own README carries the matching `## Triage Note` line for
every action, with the attempt number and the evidence path. A task with
a block but no triage rows usually has no Block Record; the report-only
`check-blocked-tasks` module lists those in the sweep log. To narrate what
triage would do for a project right now, run
`check-block-triage.sh --dry-run` with `OVERWATCH_PROJECT` set to the
project name.

### Acting on an OVERWATCH Diagnosis

An escalated task stays `BLOCKED`, with `Needs Human: yes` and an
`## OVERWATCH Diagnosis` section in `status.md`. The diagnosis is one
paragraph with four parts:

1. **The block epoch and cause** — `first_blocked_at`, the
   `reason_code`, the owner role, the attempts made, and why triage
   stopped (always human, attempts exhausted, a precondition unmet, a
   failed verification, the requeue budget spent). A spent budget's
   cause names every reason code seen, oldest first.
2. **Evidence pointers** — the Block Record's `evidence` line, the
   triage evidence file under `artifacts/block-evidence/`, and the
   backup paths.
3. **Option 1** — the concrete fix for that reason code (for example,
   for `per_task_timeout`: split the task or raise
   `[wake] max_task_seconds`; the branch and the commits-ahead snapshot
   keep the work done so far). For a spent requeue budget: find the
   cause the successive blocks share from the `## Triage Note` lines and
   their evidence, then split the task or fix that cause.
4. **Options 2 and 3** — runnable commands that return the task to
   `BACKLOG` or close it `WONT-DO`, with the project and task already
   filled in.

To act on one:

1. Read the diagnosis, then the evidence file it names. For git classes,
   look at the worktree, branch, `dirty.patch` or merge-tree conflict
   paths it quotes.
2. Fix the cause as option 1 says. Git work is yours, or the owning
   role's on its next wake. Triage will not do it.
3. Return the task with option 2's command. The Block Record stays, so
   if the task blocks again, triage sees the history. That includes its
   `requeues` count. A task whose budget is spent goes straight back to
   a human on its next block that triage would otherwise requeue.
4. If the task is no longer wanted, close it with option 3's command.
   `DONE` and `WONT-DO` remove the record.

Triage does not act on an epoch it has already escalated, so a
diagnosis stays put until you change the task. If a project's dispatch
is also held as systemic, the wake's log names the shared
`reason_code`; resolving those blocks lifts the hold.

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

**The HALT a repair creates.** A state-changing fix runs under the
HALT-first protocol: OVERWATCH creates the project's own HALT
(`projects/<name>/HALT`) for the length of the repair and removes it when
the repair ends. That HALT carries a creator marker line naming the
process that made it:

```
# overwatch-repair-halt: pid=<pid> start=<start> fn=<fix> at=<UTC time>
```

`<start>` is the process start time from `/proc/<pid>/stat`, so a
recycled pid never passes for the creator. A sweep stopped mid-repair (a
`SIGKILL`, a container stop) cannot remove its own HALT. The next sweep,
at that project's start, finds the marker, sees that its creator is no
longer running, removes the HALT and logs a `stale-repair-halt-removed`
row in the project's `overwatch/actions.log`. A repair whose creator is
still running keeps its HALT.

A HALT without a well-formed marker — every operator HALT and every CM
HALT — is never removed by OVERWATCH. The check fails closed: only an
exact marker line naming a dead creator makes a HALT removable.

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
- Does not commit, merge or check out work for a blocked task — it
  requeues the task to its owner instead.

If a check looks like it might fall into one of those areas, OVERWATCH
files a bug and stops.

## Where to look next

- [../team/roles/OVERWATCH.md](../team/roles/OVERWATCH.md) — the full
  role file: every check, every guard, the action-log format, the
  HALT-first protocol.
- [../team/scripts/lib/overwatch-checks/README.md](../team/scripts/lib/overwatch-checks/README.md)
  — the check catalog and the full block-triage envelope, class by
  class.
- `$PGAI_PROJECT_ROOT/overwatch/actions.log` — the append-only audit
  trail of every action OVERWATCH has taken on your install.
- [operator-troubleshooting.md](operator-troubleshooting.md) — when a
  symptom looks like OVERWATCH itself is wedged.
