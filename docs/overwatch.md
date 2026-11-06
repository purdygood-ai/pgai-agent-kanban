# OVERWATCH — Operator View

OVERWATCH is the kanban's self-monitor. When something in a project stops
(a blocked task, a task asking for a human, a chain HALT), OVERWATCH
decides once what to do about it and does it. There are four possible
outcomes:

- **verb** — it runs one operator write you already have (requeue, reset,
  clear a chain HALT, the resolution stage's cancel-and-rebundle, an
  allowlisted data fix) and the chain moves on.
- **inbox** — the question needs a human but does not endanger the
  pipeline. OVERWATCH parks it in the project's operator inbox, clears
  `Needs Human`, and the chain carries on around it (when the task is its
  release's last open one, the release waits on the answer).
- **runaway** — the project is repeating itself too fast (release after
  release, RC cancel loops, one task failing the same way again and
  again). OVERWATCH stops that project with a chain HALT and an inbox
  entry. This is the only way OVERWATCH stops a project.
- **none** — nothing to do now. An operator HALT always decides this.

The decisions and the verbs are code. OVERWATCH never calls a model, and
it never touches what is yours: operator HALTs, design decisions, the
installed version, providers and models, code and tests (see "What stays
with you").

This page is the operator's view. The role file
[../team/roles/OVERWATCH.md](../team/roles/OVERWATCH.md) carries the policy
and the full check list. Runbooks for halts, HALT-AFTER and metrics are in
[OPERATIONS.md](OPERATIONS.md).

## What you do

| You see | Do |
|---|---|
| Open inbox entries (dashboard attention pane, `metrics-report.sh --inbox`) | Read them with `inbox.sh --project <name> list`, act on the answer, then record and close the entry (see "The operator inbox") |
| A project stopped by the runaway detector | Read its HALT and inbox entry, fix the cause, then `unhalt.sh --project <name>` (see "The runaway detector") |
| A chain HALT that stays | Its cause check has not passed, or its trigger never clears on its own (see "Clearing a chain HALT") |
| OVERWATCH did something wrong | `overwatch-revert.sh` with the firing's timestamp (see "Backups and revert") |
| You want OVERWATCH to stand still | Create `$KANBAN_ROOT/HALT_OVERWATCH` (see "HALT flags") |

## When OVERWATCH runs

OVERWATCH has one scheduled firing and one event-driven one. Both run the
same script, `overwatch-sweep.sh`, which sweeps every project registered in
`projects.cfg`.

- **The hourly sweep.** One cron line at :30 runs `overwatch-sweep.sh`. It
  runs the check modules in `team/scripts/lib/overwatch-checks/`, and its
  block-triage check runs the decision wake for each project.
  `changelog-drift` and `version-divergence` check the kanban's own source,
  so they run only for the kanban's own project; every other project gets
  one `not applicable` line and a `check-not-applicable` row in its
  `overwatch/actions.log` for each, counted neither ok nor error. Every
  other module runs for every project; the **Projects** column of the
  [check catalog](../team/scripts/lib/overwatch-checks/README.md) gives
  each module's scope.
- **The on-BLOCK nudge.** When a wake chain blocks a task, it releases the
  project's repo lock and then starts, in the background:

  ```
  overwatch-sweep.sh --only block-triage --lock-wait 60
  ```

  A fresh block is decided within seconds instead of at the next hourly
  tick. Output goes to `logs/overwatch-trigger.log` under the kanban root.
  The nudge carries no payload; the sweep reads every stop fresh. A chain
  run by OVERWATCH never nudges, a chain nudges at most once, and the
  nudge's exit status is ignored, so the block path cannot fail because of
  it. If another chain holds the repo lock, the sweep retries for up to 60
  seconds, then leaves everything to the next hourly sweep.

`wake-now.sh --agent overwatch` runs `overwatch-sweep.sh` too. There is no
OVERWATCH model wake and no OVERWATCH queue. An install whose crontab still
carries a daily OVERWATCH wake line loses it the next time
`install-crontab.sh` rewrites the managed block.

Running `wake-batch.sh` for the overwatch agent by hand is on no schedule.
It runs plain block triage and then the resolution stage for each project,
and dispatches nothing.

### One firing per project

Every OVERWATCH entry point works on a project under that project's firing
lock, `$KANBAN_ROOT/locks/overwatch-<name>.lock`, so two firings never act
on one project at once. The lock is not waited for. A firing that finds it
held runs nothing for that project, exits 0, prints the error key
`overwatch.firing_lock_held` on stderr, and appends a
`firing-skipped:lock-held` row to the project's `overwatch/actions.log`.

The firing lock is separate from the project's repo lock. Every write a
decision makes also takes the repo lock (see "Limits on every verb").

## The decision wake

For each project with a `release-state.md`, the sweep's block-triage check
runs `pgai_agent_kanban.overwatch.decision_wake`. It finds every stop in the
project, decides each one once with `pgai_agent_kanban.overwatch.decide`,
and hands the outcome to the executor. A project with no
`release-state.md` has nowhere to record and count its decisions, so plain
block triage runs there instead (see "Block triage").

### What it decides about

Stops are decided in this order. The HALT goes first, so a chain HALT
cleared now lets the task decisions run in the same firing.

| Stop | What it is |
|---|---|
| `halt` | The project's own HALT |
| `rc-failure` | A chain HALT CM's release wrote on one of its triggers. Decided like `halt`, keyed on the trigger |
| `block` | Every task whose `## State` is `BLOCKED`, with its `## Block Record` |
| `needs-human` | Every `BACKLOG` or `WAITING` task whose `## Needs Human` reads `yes` |
| `project` | The project itself, only when a runaway counter trips and nothing above is stopped |

`decide` is a pure function. It reads the Block Record, the classifier
table, the task's earlier decisions (counted from the Decision events in
`release-state.md`), the runaway counters, the HALT's provenance and the
cause checks. It writes nothing and runs nothing.

### How it decides

The first rule that applies wins:

1. An unknown subject: **inbox**.
2. A `halt` stop with an operator HALT present (the project's or the kanban
   root's), or no HALT at all: **none**.
3. Any runaway counter tripped: **runaway**.
4. A chain HALT: written by the runaway detector or an OVERWATCH repair,
   **none** (its own writer clears it). A cause check passed:
   **verb** `clear-chain-halt`. Otherwise: **verb** `resolution-stage`.
5. A blocked task, or one asking for a human:
   - No Block Record, an unreadable one, or a classifier table that does
     not load: **inbox**.
   - `transient_api_error` below its retry ceiling, or a block inside its
     cool-down window: **none**. The transient-error handler, or the next
     firing, takes it.
   - The classifier chose a triage action: that action's **verb**. If the
     action would return the task to its owner and the Block Record's
     `requeues` has reached `[overwatch] requeue_budget`, the row's
     decision applies instead.
   - Triage has no step left (a decision-only row, attempts exhausted): the
     row's decision. That is the row's verb while the task has used it
     fewer than its budget, then the row's inbox question. A PM, TESTER or
     CM block on a decision-only row is first requeued on the pipeline
     ladder (see "The classifier table"), under the same requeue budget.
   - A precondition is unmet: the row's inbox question, never its verb.
   - Anything else that cannot be decided safely: **inbox**.
6. The project with nothing stopped: **none**.

### What each outcome does

| Outcome | What the executor writes |
|---|---|
| `verb` | Under HALT-first and the repo lock: backs up every file the verb changes, then runs the verb through its existing write (see "Verbs"). |
| `inbox` | One inbox entry (an open entry asking the same question is reused). For a task, also sets `## Needs Human` to `no` and adds a line to `## Blockers` naming the entry. The task otherwise stays as it is: a `BLOCKED` task stays `BLOCKED`. The entry's Meanwhile line says the chain carries on around the task; when every other task of its `## Release Version` is `DONE` or `WONT-DO`, it and the `## Blockers` line say instead that the release is waiting on the task, naming the version. |
| `runaway` | The runaway detector's stop: a project chain HALT naming the counter, and one inbox entry. A project that already has a HALT is left alone. |
| `none` | Nothing. |

A verb that cannot be carried out never raises `Needs Human`. That covers a
handler that refuses at run time (counted as an attempt, so the row's
`max_attempts` ends the retries) and a resolution stage that declines. The
task's own classifier row asks its question in the inbox instead.

A decided action that did not run, and would not run on a later firing
either, is reported in the inbox:

- deciding the stop, or carrying out its decision, raised an error;
- a verb `[overwatch] block_triage = false` keeps unrun while its task is
  still stopped;
- the HALT that goes ahead of a repair could not be written.

The entry names the stop, what was decided, that it did not run and why,
the task's state now, and runnable commands that resolve the stop. Nothing
else is written for it. An open entry about the same stop and decision is
reused, so a later firing adds none. A HALT that defers the action,
`HALT_OVERWATCH` and a busy repo lock are not reported: the first two are
your own stops, and the lock frees on a later firing.

Every decision that was carried out writes three records:

- one `## OVERWATCH Decision` event in the project's `release-state.md`,
  lane `decision-wake`, whose first Evidence line reads
  `decision: kind=<kind> name=<verb|none> source=<subject> result=<status>`;
- one row in the project's `overwatch/actions.log` naming every backup;
- one block-start/block-end pair in the reliability ledger (see
  "Reliability ledger").

A decision that carried nothing out writes none of these. That covers
`none`, a deferral, an inbox question already open, and a runaway on a
project already halted. An hourly sweep that keeps finding the same stop
adds nothing.

### Running it by hand

To see what the decision wake would do for one project without changing
anything:

```
cd "$KANBAN_ROOT" && python3 -m pgai_agent_kanban.overwatch.decision_wake \
    --kanban-root "$KANBAN_ROOT" --project <name> --dry-run
```

It prints one tab-separated line per stop: item, subject, outcome, verb
(`-` for none), result and detail. The result is one of `executed`,
`inbox`, `runaway`, `nothing`, `deferred`, `dry-run` or `failed`. Exit 0
means every stop was decided, 1 means one or more could not be (each is
printed on stderr, and outside `--dry-run` each also leaves an inbox entry),
and 2 is a usage error. `--dry-run` writes one `decision-wake-dry-run` row
per stop to the action log and nothing else.

## Verbs

A verb is an operator write that already exists. OVERWATCH calls what you
would call; it adds no new way to change state.

| Operator write | Verb `decide` names | What runs |
|---|---|---|
| Requeue a blocked task to its owner | `requeue`, `prune-worktree-then-requeue` | Block triage's handler for the reason code (evidence snapshot, worktree prune), then `BACKLOG` with `Needs Human: no` through the status-writer (`task-status.sh`) and the `[ ]` queue marker |
| Close a task whose branch is already merged | `recompute-gate` | Block triage's read-only gate recompute, then `DONE` through the status-writer |
| Reset a task to `BACKLOG` with `Needs Human` cleared | `reset-to-backlog` | `task-status.sh --state BACKLOG --needs-human no` |
| `restamp-fixed-in` | `restamp-fixed-in` | The allowlisted operator data verb (`restamp-fixed-in.sh`), with the typed arguments from the Block Record |
| Clear a chain HALT | `clear-chain-halt` | Re-read the HALT, back it up, remove it with `ops.write.unhalt` (`unhalt.sh`) |
| Cancel the RC, rebundle, close or wont-do its tasks | `resolution-stage` | The resolution stage's cancel-and-rebundle drill: `cm/cancel-rc.sh --yes`, then `wontdo_item`, `close_item` and `reset_item` (`wontdo.sh`, `close.sh`, `reset.sh`) |

OVERWATCH has no standalone wont-do verb. A task is closed `WONT-DO` only
inside the resolution stage's drill. For the drill's lanes and the ledger
it writes, see "When OVERWATCH resolves a halt for you" in
[OPERATIONS.md](OPERATIONS.md).

### Limits on every verb

- **HALT-first, under the repo lock.** `HALT_OVERWATCH`, or a repo lock held
  past `--lock-wait`, stops the run. The remaining stops are decided on
  the next firing.
- **A project HALT defers task verbs.** Only `clear-chain-halt` and
  `resolution-stage`, whose subject is that HALT, run while it is present.
  An operator HALT is never touched.
- **A time limit on the resolution stage's scripts.** Each script the
  resolution stage runs is bounded by
  `kanban.cfg [overwatch] verb_timeout_seconds` (default `3600`). That
  covers `cm/cancel-rc.sh` and `unwind-rc.sh` in the cancel drills, and
  `cm/release.sh` for a declared-intent ship. The script runs in its own
  process group. Past the limit, the whole group is stopped (SIGTERM,
  then SIGKILL after 5 seconds) and the verb is recorded as failed with
  the reason code `overwatch.verb-timeout`. The drill then takes its
  ordinary failure path: nothing after the failed step runs, and the
  ship-policy HALT stays. The decision wake continues. The key is read
  from `kanban.cfg` only. If it is absent, blank or not a positive whole
  number, the default applies, with a warning for a bad value. Keep it
  above what a release takes, hooks included
  (`[release] hook_timeout_seconds`, "Release Lifecycle Hooks" in
  [OPERATIONS.md](OPERATIONS.md)).
- **Backup first.** Every file a verb changes is copied under the project's
  `overwatch/backups/<firing timestamp>/` before the write.
- **Budgets.** A row's decision verb runs at most its `budget` times on one
  task (the shipped rows use 1). Triage actions are bounded by each row's
  `max_attempts` and by `[overwatch] requeue_budget`. A spent budget ends
  in the inbox.
- **`[overwatch] block_triage = false`** keeps OVERWATCH to records:
  `reset-to-backlog` and the triage verbs write no state or queue change.
  While the task stays stopped, the unrun verb is reported in the inbox.
- **The plan is re-read under the lock.** If the task is no longer blocked,
  or is now inside a cool-down window, the verb changes nothing.

## The operator inbox

Each project has one inbox, `projects/<name>/operator-inbox.md`. It holds
the questions only a human can answer, written by OVERWATCH (and by
`wake-batch.sh` for a registry fault it refuses, and by any agent through
the inbox module) while the chain carries on. A missing file is an empty
inbox.

The file has two sections, `## Open` and `## Done`. Each entry is a
`### INBOX-NNNN` heading followed by its fields:

```
### INBOX-0001
- **Date:** 2026-01-01T10:00:00+00:00
- **Source:** rc:v1.2.3
- **Question:** Cancel the RC or keep it open for the late fix?
- **Meanwhile:** Left the RC open; the rest of the queue kept running.
- **How to answer:** Answer "cancel" or "keep" with the inbox answer command.
- **Answer:** keep
- **Answered at:** 2026-01-01T11:00:00+00:00
- **Closed at:** 2026-01-01T11:05:00+00:00
```

`Source` is a task ID, `rc:vX.Y.Z`, the project name, `wake-batch`, or a bug or
priority key (an item at `needs-operator`).
`How to answer` names what to do, with runnable commands where there are
any. IDs count up per project. Every write is atomic and taken under the lock
`$KANBAN_ROOT/locks/operator-inbox-<name>.lock`.

**Recording an answer does not act on it.** Nothing reads the `Answer`
field. Do what the entry's `How to answer` says (return the task, unhalt
the project, fix the README), then record your answer and close the entry
so the open count stays true.

### Commands

```
inbox.sh --project <name> list
inbox.sh --project <name> answer --id INBOX-0001 --answer "split the task"
inbox.sh --project <name> close --id INBOX-0001
```

- `list` prints the open entries, then the closed ones.
- `answer` records an answer on an open entry. The entry stays open, and a
  second answer replaces the first.
- `close` moves an open entry to `## Done`, keeping its answer.

`--project` is required; `$PGAI_PROJECT_NAME` is never read in its place.
IDs are read liberally: `INBOX-0007`, `inbox_7` and `7` name the same
entry. Exit codes: 0 done, 1 unknown project, 2 bad input or an entry
already closed, 3 no such entry, 4 the file or lock could not be read or
written.

### Other surfaces

| Surface | What it shows |
|---|---|
| `GET /projects/{name}/inbox` | Every entry, open and closed (REST twin of `list`) |
| `POST /operations/inbox-answer` | Records an answer (REST twin of `answer`). `close` has no REST twin |
| Dashboard attention pane | One block per project with open entries: the open count, one line per open entry (newest first: ID, date, source and the question's first line), and the `inbox.sh ... list` command. Omitted when nothing is open |
| `metrics-report.sh --inbox [--project <name>]` | `<project>: <N> open`, one line per project |
| `metrics-report.sh --day <date>` | The day's JSON carries `inbox_open`, the open count when printed |

Request and response shapes are in [operator-api.md](operator-api.md).

## The runaway detector

The runaway detector is the only path by which OVERWATCH stops a project.
It counts how often the chain repeats itself on one project. When the
events inside a counter's window **exceed** its threshold, it stops that
project alone. Every other project keeps running.

| Counter | `[overwatch]` key | One event is | Window | Default |
|---|---|---|---|---|
| `releases_per_hour` | `runaway_releases_per_hour` | a release that shipped | 1 hour | 5 |
| `rc_cycles_per_day` | `runaway_rc_cycles_per_day` | an RC opened and cancelled | 1 day | 2 |
| `reason_repeats` | `runaway_reason_repeats` | one block of one task with one reason code that did not follow a triage requeue (counted per task and code) | 7 days | 3 |
| `inbox_entries_per_day` | `runaway_inbox_entries_per_day` | an inbox entry, not counting the detector's own | 1 day | 20 |

With the defaults, 6 releases in an hour, 3 RC cancel cycles in a day, or
the 4th counted block of one task with one reason code trips a counter.

A block that follows a triage requeue (its Block Record's `requeues` rose
since the task's previous block) is a retry on a requeue ladder, not a
repeat, and is not counted. So at the defaults the block that ends a
3-requeue ladder reaches its row's decision, not a runaway HALT.
`requeues` stops rising once `[overwatch] requeue_budget` is spent, so
every later block counts: a task that keeps blocking with one reason code
after its ladder is spent trips on its third further block. A task blocked
again with no requeue between its blocks trips on its 4th block.

Each key is read from the project's `project.cfg` first, then
`kanban.cfg`, then the default.
`0` turns a counter off; blank keeps the default. A value that is not a
whole number is refused. In `kanban.cfg` the refusal comes at config
validation: every agent wake fails its config load and exits 1 with
`config_loader.overwatch.invalid_runaway_threshold`, naming the key, the
value and the file. In a `project.cfg` the detector checks nothing for that
project until it is fixed, and the decision wake leaves one inbox entry,
"The runaway detector is off for project <name>", naming the key and the
command that fixes it.

Events come from the project's own records: shipped and cancelled
`release-state/` records, each task's Block Record, and the inbox file.
They are kept in `projects/<name>/overwatch/runaway-counters.json`. After a
trip, only newer events count again for that counter, so resuming never
re-trips on the same evidence.

### What a trip writes

The detector creates `projects/<name>/HALT`, never overwriting one that
exists, with this body:

```
# HALT created by the OVERWATCH runaway detector at <time>: <counter> passed its threshold
# halt-writer: overwatch-runaway
# halt-reason: runaway.<counter>: <count> <events> within <window> exceeds the threshold of <n>
# error:   runaway.<counter>
# message: <what stopped and why>
# remedy:  <the status command that prints the evidence>
# runaway-counter: <counter>
# runaway-threshold: <n>
# runaway-window: <window>
# runaway-count: <count>
# runaway-subject: <project, or TASK-ID:reason_code>
# runaway-event: <time> <key> <detail>
# Resume: <kanban root>/scripts/unhalt.sh --project <name>
```

The `runaway-event` line repeats for the counter's last 10 events, oldest
first, and the whole `runaway-*` block repeats for each counter that
tripped. The detector then writes one inbox entry naming the counter and
how to resume. A project that already has a HALT, from anyone, is left
alone: nothing is written and no trip is recorded.

OVERWATCH never clears a runaway HALT. To resume, read the HALT and the
inbox entry, fix what was repeating, run `unhalt.sh --project <name>`, then
close the entry.

To inspect the counters without writing anything:

```
cd "$KANBAN_ROOT" && python3 -m pgai_agent_kanban.overwatch.runaway status --project <name>
cd "$KANBAN_ROOT" && python3 -m pgai_agent_kanban.overwatch.runaway config --project <name>
```

`status` prints each counter's count and threshold. `config` prints the
four thresholds and `halt_after_auto_resume`.

## HALT provenance

OVERWATCH may clear a HALT the chain wrote, once its cause is resolved. It
never touches a HALT an operator wrote. Which one a HALT is comes from the
file's body and, for an empty file, the sentinel-writes log. Never from
emptiness alone.

| Origin | Writers |
|---|---|
| **chain** | CM's release (`cm-release`), HALT-AFTER promotion (`halt-after-promotion`), an OVERWATCH repair (`overwatch-repair`), the runaway detector (`overwatch-runaway`). `zero-progress-cap` is also recognised |
| **operator** | `halt.sh` and `halt-global.sh`, which write an empty file |

Every chain writer puts a provenance block after the HALT's first line:

```
# halt-writer: <writer>
# halt-reason: <one line: why the chain stopped>
# halt-trigger: <CM trigger number>        (CM trigger HALTs only)
```

A HALT is classified in this order:

1. No file: absent.
2. A file that cannot be read: operator.
3. A `# halt-writer:` line naming a chain writer: chain. Any other writer:
   operator.
4. A CM header (`# HALT created by CM at ...`) or an OVERWATCH repair marker
   (`# overwatch-repair-halt: ...`) without the block: chain.
5. Any other non-blank body: operator.
6. A blank body is looked up in `$KANBAN_ROOT/logs/sentinel-writes.log`.
   The latest record for the path is `op=promote` and still matches the
   file: chain (an empty HALT-AFTER promotion). `op=halt` or `halt_global`:
   operator. **No record, or any other record: operator.** What cannot be
   proven to be the chain's is treated as yours.

The fleet-scoped `$KANBAN_ROOT/HALT` is always operator, whatever it says.
A chain HALT beside an operator HALT is left alone too.

To see how a HALT reads:

```
cd "$KANBAN_ROOT" && python3 -m pgai_agent_kanban.overwatch.halt_provenance classify \
    projects/<name>/HALT --kanban-root "$KANBAN_ROOT"
```

It prints `origin`, `writer`, `reason`, `trigger` and `source`,
tab-separated, and exits 0.

## Clearing a chain HALT

What happens to a chain HALT depends on its writer.

| Writer | What OVERWATCH does |
|---|---|
| `cm-release` | Looks up the trigger's row in `resolution-matrix.yaml`. Clears the HALT as soon as one of the row's cause checks passes. While none passes, `matrix` rows go to the resolution stage's ship-policy path and `wait` rows are left for you |
| `halt-after-promotion` | A drain that completed. With `halt_after_auto_resume = true`, clears it and writes the inbox entry "stopped after rc:vX.Y.Z as armed; resume with unhalt". With `false` (the default), leaves it for you |
| `zero-progress-cap` | Clears it once the task its reason names is back in `BACKLOG` or `WAITING` |
| `overwatch-runaway` | Never. You clear it (see "The runaway detector") |
| `overwatch-repair` | Left to the repair that wrote it, or removed by the next sweep if that repair died (see "HALT flags") |

The cause checks:

- **`rc-cancelled`** — `## Active RC` reads `none`, the RC was cancelled
  after the HALT was written, and none of its cargo still reads `running`
  (it was rebundled).
- **`task-reset`** — the RC's latest TESTER task reads `BACKLOG` or
  `WAITING` again.
- **`notes-status-green`** — the RC branch's release notes carry a
  `## Status` section the release stamp applies to.

The shipped trigger rows:

| Trigger | Name | Clears on | While open |
|---|---|---|---|
| 1 | tester-blocked | `rc-cancelled`, `task-reset` | matrix |
| 2 | systemic-risk-high | `rc-cancelled` | matrix |
| 3 | serious-concerns-large-fix | `rc-cancelled` | matrix |
| 4 | pre-squash-hook-failed | `rc-cancelled` | wait |
| 5 | squash-conflict | `rc-cancelled` | wait |
| 9 | notes-status-stamp-failed | `rc-cancelled`, `notes-status-green` | wait |
| 10 | pollution-guard-strict | `rc-cancelled` | wait |
| 5b, 6, 7, 8, 11 | after the squash, or the cross-release pattern | nothing | wait |

The release writes no trigger-1 HALT since verification is requeued; the
trigger-1 row reads a HALT an earlier release wrote. Triggers after the
squash, and the three-non-functional-releases pattern, are never cleared: cancelling the RC does not undo them. A CM HALT that
names no trigger, or one with no row, takes the ship-policy path and is
never cleared by a check. What each trigger means is under "CM HALT
triggers" in [OPERATIONS.md](OPERATIONS.md).

Every clear takes one path: re-read the HALT and require the bytes the
decision read, copy it under `overwatch/backups/`, remove it with
`ops.write.unhalt`, then append a Decision event.

`halt_after_auto_resume` is an `[overwatch]` key, read from the project's
`project.cfg` first, then `kanban.cfg`. It accepts `true` or `false` only.
Arming and resuming HALT-AFTER by hand is in "HALT-AFTER — operator arm and
resume" in [OPERATIONS.md](OPERATIONS.md).

## What stays with you

OVERWATCH has no verb for any of these, and never asks a model to choose
one:

- design decisions, and any question a classifier row sends to the inbox;
- changing the installed kanban version;
- changing providers or models;
- creating, editing or removing an operator HALT, or `HALT_OVERWATCH`;
- editing code, tests or a task README;
- merging, committing or checking out work for a blocked task (it requeues
  the task to its owner instead);
- resuming a project the runaway detector stopped.

It also stays in its lane: it does not decompose requirements, verify
acceptance criteria, open RCs, tag or push releases (the push-lag check
excepted, under its own gate), or delete bug or priority files.

## Reliability ledger

Every decision that was carried out writes one block-start/block-end pair
to `projects/<name>/metrics/blocked-intervals.jsonl`. The source is
`task:<TASK-ID>`, `halt:project` or `project:<name>`. The block-end's
class says whether a human had to clear it:

| Decision | Class |
|---|---|
| A verb that ran | `any` |
| An inbox entry that leaves its task `BLOCKED` | `human` |
| Any other inbox entry | `any` |
| A runaway stop | `human` |

`human` blocks end the **Hands-off** streak; every block ends the
**Clean** streak. A block the verb's own write already closed (a return to
`BACKLOG` closes it as `any`) is not closed twice. Clearing a chain HALT
closes its block as `any` immediately before the unhalt. For how the
streaks are computed and shown, see [dashboard.md](dashboard.md) and
"Metrics" in [OPERATIONS.md](OPERATIONS.md).

## Block triage

Block triage is the set of handlers the task verbs run. It is also the
whole of OVERWATCH's block handling for a project with no
`release-state.md`, and for a hand-run `wake-batch.sh` for the overwatch
agent. On that path, a block triage cannot act on stays `BLOCKED` with
`Needs Human: yes` and an `## OVERWATCH Diagnosis` (see "Acting on an
OVERWATCH Diagnosis" below).

### The Block Record

Every site that blocks a task writes a `## Block Record` to its
`status.md` through one helper (the values here are an example):

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
| `reason_code` | Why the task blocked; one of the closed vocabulary below |
| `owner_role` | `CODER`, `WRITER`, `TESTER`, `CM` or `PM` |
| `attempts` | Triage attempts in this block epoch |
| `first_blocked_at` | When this block epoch began |
| `last_blocked_at` | When the latest block was written |
| `cooldown_until` | No triage action before this time; `none` for no window |
| `evidence` | `; `-joined `key=value` pointers (`worktree`, `branch`, `ahead`, `merged`, `batch_log`, `diag`), or `none` |
| `resume` | `branch` re-dispatches onto the existing feature branch; `fresh` starts a new one |
| `requeues` | Times triage, or the wake after a per-task timeout that left commits on the feature branch, returned the task to its owner over the record's whole life |
| `requeue_reasons` | The reason code of each requeue, oldest first |

The record is created by the first block, updated on each re-block
(`first_blocked_at`, `attempts`, `cooldown_until`, `resume`, `requeues` and
`requeue_reasons` are kept), kept across a return to `BACKLOG`, and removed
only when the task reaches `DONE` or `WONT-DO`.

A wake whose batch or project time budget stops an agent early writes no
record. The task returns to `BACKLOG` with `Needs Human: no`, and an
existing record only gets `resume: branch`. A task with no record gets a
`## Resume Mode` section with the body `branch` instead.

To list the accepted reason codes:

```
cd "$KANBAN_ROOT" && python3 -m pgai_agent_kanban.overwatch.block_record codes
```

| reason_code | Written when |
|---|---|
| `per_task_timeout` | The per-task timeout killed the agent at `[wake] max_task_seconds` |
| `provider_refusal` | The zero-progress ceiling was reached and the latest dispatch ran under 60 seconds, or its log tail carries a `refusal` or `billing` signature (see "The transient-error check") |
| `zero_progress` | The zero-progress ceiling was reached otherwise |
| `transient_api_error` | The wake's transient-error check blocked the task on a provider error, or rewrote an agent's `agent_unclassified` block whose log tail carries one on the provider's own error lines; or OVERWATCH's `check-transient-api-error` sweep escalated the task at the `[wake] transient_retry_cap` ceiling and set `Needs Human: yes` (see "The transient-error check") |
| `gate_merge_not_found` | The completion gate found the feature branch but no merge of it |
| `gate_dirty_worktree` | The completion gate found uncommitted changes |
| `gate_empty_commit` | The completion gate found branch commits that carry no change |
| `worktree_create_failed` | The task worktree could not be created and the source branch exists |
| `ship_policy_halt` | Reserved for a block held by a CM ship-policy HALT |
| `missing_readme` | The task folder has no `README.md` |
| `invalid_assignment` | The task README assigns a role or owner the wake cannot dispatch |
| `invalid_effort_config` | The task's effort configuration is invalid |
| `invalid_state` | `status.md` carries a `## State` outside the six canonical states |
| `branch_prefix_failed` | The project's branch prefix could not be resolved |
| `workflow_type_unparseable` | `## Workflow Type` cannot be parsed |
| `plugin_load_failed` | The workflow-type plugin failed to load |
| `source_branch_missing` | The source branch the task names does not exist |
| `gate_merge_evidence_not_found` | The completion gate could not find the feature branch to check for a merge |
| `agent_operator_data_action` | An agent blocked its task asking for an allowlisted operator data action, with typed arguments |
| `agent_transient_provider_error` | An agent blocked its task because a transient provider error stopped its work |
| `agent_unclassified` | An agent blocked its task without a reason code |

An agent passes `--reason-code` on a `BLOCKED` write. For
`agent_operator_data_action` it also passes typed `--action-args`, stored
in the `evidence` line (placeholders in angle brackets and capitals):

```
bash scripts/task-status.sh --project <name> --key <TASK-ID> \
    --state BLOCKED --needs-human yes --reason-code agent_operator_data_action \
    --action-args verb=restamp-fixed-in project=<name> version=vX.Y.Z bugs=BUG-NNNN,BUG-NNNN
```

The one allowlisted verb is `restamp-fixed-in`: `project` (a registered
project), `version` (`vX.Y.Z`) and `bugs` (comma-separated `BUG-NNNN` ids,
each existing and `done`). It stamps `## Fixed In` on each named bug whose
value is empty and never overwrites one.

### The classifier table

The table is
[../team/pgai_agent_kanban/overwatch/block-classifier.yaml](../team/pgai_agent_kanban/overwatch/block-classifier.yaml);
its header documents the row shape. The row keyed by a record's
`reason_code` says what triage may do (the action) and what resolves the
block once triage has nothing left to do (the decision). Every row has a
decision. None is "escalate".

Cool-downs are in seconds; "×2" means the window doubles after each
attempt, up to the cap.

| reason_code | Triage action | max / cool-down | Decision |
|---|---|---|---|
| `per_task_timeout` | `requeue_owner`: snapshot the commits ahead, `resume: branch` | 1 / 1800 | inbox: split the task or raise the timeout |
| `provider_refusal` | `requeue_owner` after a first cool-down; resets `## Zero Progress` | 3 / 900 ×2, cap 3600 | inbox: provider login, model or usage limit |
| `zero_progress` | `requeue_owner` with a note | 1 / 1800 | `reset-to-backlog` once, then inbox |
| `transient_api_error` | requeued by the transient-error handlers, not triage | 3 / 300 ×2, cap 1200 | `reset-to-backlog` once, then inbox |
| `gate_merge_not_found` | branch already merged: `recompute_gate`, then `DONE`; else a clean merge-tree and a CODER/WRITER owner: `requeue_owner`, "merge only, then re-complete" | 1 / 0 | inbox: merge by hand or return to owner |
| `gate_dirty_worktree` | `requeue_owner`: save `dirty.patch` and `untracked.txt` (never commit), `resume: branch` | 1 / 0 | inbox: which saved changes belong to the task |
| `gate_empty_commit` | `requeue_owner`, `resume: branch`, quoting the empty commits | 1 / 0 | inbox: drop them and re-merge, or close |
| `worktree_create_failed` | `prune_worktree_then_requeue`, only for residue inside the worktree base whose commits all exist elsewhere | 1 / 300 | inbox: keep or discard the residue's commits |
| `ship_policy_halt` | `defer_to_resolution_stage` | the stage's own ceiling / 0 | inbox when the stage declines |
| `agent_operator_data_action` | `run_allowlisted_verb` with validated arguments, re-run to confirm, then requeue | 1 / 0 | inbox: run it by hand or correct the arguments |
| `agent_transient_provider_error` | `requeue_owner` after a first cool-down, with a note quoting `## Blockers` | 3 / 900 ×2, cap 3600 | inbox: is the provider in a sustained outage |
| `agent_unclassified` | `requeue_owner` after a first cool-down, with a note quoting `## Blockers` | 3 / 900 ×2, cap 3600 | inbox: what the agent needs, from `## Blockers` |
| `invalid_state` | none | — | `reset-to-backlog` once, then inbox |
| `missing_readme` | none | — | inbox: restore `README.md` or close the task. No operator write regenerates a task README, so OVERWATCH never rebuilds one |
| `invalid_assignment`, `invalid_effort_config`, `branch_prefix_failed`, `workflow_type_unparseable`, `plugin_load_failed`, `source_branch_missing`, `gate_merge_evidence_not_found` | none | — | inbox, with the row's own question |

The two agent rows retry the agent's own block after cool-downs of 15,
30 and 60 minutes, so a one-off failure clears without a human. A
re-block keeps the epoch's `attempts`; after the third the row's decision
applies.

**The pipeline ladder.** A release cannot carry on without its PM, TESTER
or CM task, so the table's `pipeline_ladder` puts the same ladder in front
of every decision-only row (`none` above) for those owners:
`requeue_owner` after a first cool-down, with a note naming the reason
code and quoting `## Blockers`, 3 / 900 ×2, cap 3600. Only then does the
row's decision apply. A row with its own action keeps it for every role,
and a CODER or WRITER block takes the row as written.

The classifier fails closed: no record, an unknown `owner_role`,
unreadable evidence, a code with no row, or a table that does not load is
never acted on.

To see what the classifier says about one task without changing anything:

```
cd "$KANBAN_ROOT" && python3 -m pgai_agent_kanban.overwatch.block_classifier classify \
    projects/<name>/tasks/<TASK-ID>/status.md
```

### Verification and the requeue budget

A block epoch is keyed on `first_blocked_at`. When a requeued task blocks
again, the pending entry in `artifacts/block-evidence/verification.json` is
settled against the new block:

- **passed** — the new block is a new incident. The epoch restarts
  (`attempts` 0); `requeues` is kept.
- **failed** — the same failure came back. The row decides as usual if it
  still allows an attempt; otherwise triage has no step left.
- **unverified** — the row has no verification; the row decides as usual.

A task whose blocks alternate between reasons passes every verification,
so each row's `max_attempts` bounds only one epoch. `requeues` is the cap
that survives: once it reaches `[overwatch] requeue_budget` (default 3),
the next block that would be requeued goes to the row's decision instead.
`recompute_gate` closes the task rather than requeueing it and is never
counted; nor are the transient-error handlers' own requeues.

### Evidence files and safety

Triage writes its evidence under the task's `artifacts/block-evidence/`
before anything else, and never overwrites a file:

| File | Written when |
|---|---|
| `triage-<UTC>-<outcome>.json` | Every triage write |
| `verification.json` | Each action appends a `pending` entry, settled on the next block |
| `commits-ahead-<UTC>.txt` | A `per_task_timeout` requeue |
| `dirty.patch`, `untracked.txt`, `dirty.patch.sha256` | A `gate_dirty_worktree` requeue |
| `worktree-residue-<UTC>.txt` | A `worktree_create_failed` prune |
| `verb-<verb>-<UTC>.txt` | An `agent_operator_data_action` run |

Triage runs no repository-mutating git. The one exception is
`git worktree remove` (never `--force`) and `git worktree prune`, inside
the worktree base, for residue whose commits all exist on another ref.
Where a fix needs git, triage requeues the task to its owner with
`resume: branch`. It never resets a task.

### Configuration

| Key | Accepted | Default | Read from (highest first) |
|---|---|---|---|
| `[overwatch] block_triage` | `true` \| `false` | `true` | `project.cfg`, `kanban.cfg`, default |
| `[overwatch] requeue_budget` | a non-negative whole number | `3` | `project.cfg`, `kanban.cfg`, default |

`block_triage = false` keeps OVERWATCH to records, diagnoses and inbox
entries: no verb changes a task's state, its queue line or its README.
`requeue_budget = 0` requeues nothing. A value outside the accepted set
is refused loud, and triage plans nothing for that project.

### Acting on an OVERWATCH Diagnosis

On the plain block-triage path, a block triage cannot act on stays
`BLOCKED` with `Needs Human: yes` and an `## OVERWATCH Diagnosis`: the
block epoch and cause, the evidence pointers, the concrete fix for that
reason code, and runnable commands that return the task to `BACKLOG` or
close it `WONT-DO`. Fix the cause, then run the return command. The Block
Record stays, so triage sees the history if the task blocks again. Triage
does not act again on an epoch it has escalated.

## Related wake behaviour

These three are the wake's, not OVERWATCH's, and are listed here because
they decide what a block does to dispatch. Where an OVERWATCH check
applies the same rule, the section says so.

### The transient-error check

When an agent exits with its task still `WORKING`, the wake classes the
last 50 lines of its log against the provider-error signature table,
[../team/config/provider-error-signatures.txt](../team/config/provider-error-signatures.txt).
This check, `check-transient-api-error` and the block-evidence classifier
all take their provider-error signatures from that table, whose header
names each class and the action it takes; adding a signature is one line.
The usage-limit pause below keeps its own line-start match.

- **transient** — overload, rate limit, a 5xx, a safeguard or
  safety-filter abort. The task is blocked as `transient_api_error`,
  counted in its `## Transient Retry` record, and retried with
  `Needs Human: no` up to `[wake] transient_retry_cap`.
- **refusal** and **billing** — authentication, an unknown model, a usage
  limit; quota or credit exhausted. Never retried as transient, and either
  outranks a transient signature in the same tail.

A task its agent left `BLOCKED` with the catch-all `agent_unclassified`
gets a narrower check, from the wake and from OVERWATCH's
`check-transient-api-error` sweep alike. Its tail ends with the agent's
own final reply, whose file names, numbers and words can match a
transient signature (a file named "rate_limit.sh", "500 chars", an
"HTTP 503" section), so only the provider's own error lines count:

- a line that opens with `API Error:`;
- a JSON event line reporting an error: `"type": "error"`,
  `"type": "turn.failed"` or `"is_error": true`.

The block becomes `transient_api_error` only when the whole tail classes
transient (so a refusal or billing signature anywhere still outranks it)
and those lines alone class transient too. Its Block Record is then
rewritten with evidence from the log, `## Blockers` quotes the provider's
line, and the task takes the same retry path. The sweep requeues such a
block only under the same two conditions. Otherwise the block stands as
the agent wrote it, for both checks: its record, its own `## Blockers`
text and `Needs Human: yes`. The wake leaves every other agent code,
`agent_transient_provider_error` included, as written; the sweep classes
a block with any other code, or with no Block Record, on the whole tail.

### The scoped dispatch gate

A human-gated block (`BLOCKED` with `Needs Human: yes`) holds dispatch for
the whole project only when it is **systemic**: two or more share one
`reason_code` within `block_storm_window` of now (`agent_unclassified`
never forms a storm), or any one carries a code on
`systemic_block_reasons`. Any other block holds only the tasks that name it
as a prerequisite. Every hold lifts by itself no later than
`block_hold_cap` seconds after the block that began it. An inbox outcome
clears `Needs Human`, so a parked block holds nothing.

| Key | Accepted | Default | Read from (highest first) |
|---|---|---|---|
| `[wake] block_storm_window` | whole seconds | `3600` | `BLOCK_STORM_WINDOW` env var, `kanban.cfg`, default |
| `[wake] block_hold_cap` | whole seconds | `3600` | `BLOCK_HOLD_CAP` env var, `kanban.cfg`, default |
| `[wake] systemic_block_reasons` | Block Record codes, or `none` | `invalid_effort_config,workflow_type_unparseable,plugin_load_failed,branch_prefix_failed,invalid_assignment` | `SYSTEMIC_BLOCK_REASONS` env var, `kanban.cfg`, default |

A held wake logs "not dispatching new tasks until" the lift time. To print
the built-in defaults:

```
cd "$KANBAN_ROOT" && python3 -m pgai_agent_kanban.overwatch.block_storm defaults
```

### The usage-limit pause

When an agent exits with its task still `WORKING` and one of the last 50
lines of its log opens with the provider CLI's usage-limit message (for
example `Claude AI usage limit reached|<epoch>`, `5-hour limit reached`,
`usage_limit_exceeded`), the wake records a fleet-wide pause for that
provider. Only text at the start of a line counts. Billing failures such as
`insufficient_quota` do not.

The pause lasts until the reset time the message states, when the wake can
read one, or `[wake] usage_limit_pause_seconds` (default `3600`;
`USAGE_LIMIT_PAUSE_SECONDS` env var first). Every role in every project
stops dispatching to that provider at its next task boundary, refusing with
the key `wake.dispatch_paused_usage_limit`. The marker is
`$KANBAN_ROOT/provider-pause/<provider>.pause`; removing it resumes dispatch
early.

The task returns to `BACKLOG` without spending a retry, up to 3 times since
it last moved. From the 4th, each attempt counts as a zero-progress cycle,
so a task cannot cycle through usage limits forever.

## The other checks

### The whitelist

Besides block triage, the sweep runs a closed set of check modules. Most
auto-fix one narrow anomaly, some with a bug-file fallback; several are
report-only. The catalog,
[../team/scripts/lib/overwatch-checks/README.md](../team/scripts/lib/overwatch-checks/README.md),
names each module, the projects it runs for, and its fix. Three rules
bound every auto-fix:

- **Never destructive.** Files are renamed (`.orphan`) or backed up first;
  branch deletion uses `git branch -D`, only once the shipped tag is
  proven to hold the branch's work; otherwise the branch is kept and a
  bug filed.
- **Backup, then act.** If the backup fails, the fix aborts and OVERWATCH
  files a bug.
- **Never modify a running task.** A `WORKING` task's `status.md` is not
  touched.

### The leaked-listener check

`check-leaked-listeners` sends `SIGTERM` to test-fixture listeners left
running: a process whose working directory is under the framework temp
root and that holds a listening TCP socket. A listener inside a `WORKING`
task's worktree or scratch directory is skipped, and so is one younger than
`[overwatch] leaked_listener_min_age_seconds` (`kanban.cfg` only, default
`3600`). Each project's check acts only on its own listeners.

### The push-lag check

Two checks touch origin: `check-push-lag` pushes main and tags, and
`check-orphan-rc-branches` deletes a proven orphan RC branch on the remote.
Both are gated on the project's push decision. With `push_to_remote = false`
neither contacts a remote, so neither writes to one: `check-push-lag` measures
no tag lag against origin and pushes nothing.

`check-push-lag` looks for the project's own `<branch_prefix>main` ahead of
origin, or `<branch_prefix>vX.Y.Z` tags origin does not have. It pushes only
when the project is registered in `projects.cfg`, its `project.cfg` exists
and names a `dev_tree_path`, and `push_to_remote` there is explicitly `true`,
and only while no agent holds the project's repo lock.

| Resolver answer | What the check does |
|---|---|
| `push`, with `push_to_remote = true` | Pushes `<branch_prefix>main` and each missing release tag by explicit ref; logs `pushed-main-to-origin`, `pushed-tag-to-origin`, `push-main-failed` or `push-tag-failed`. When the remote cannot be listed, logs one `remote-unreachable` row naming the remote and the git error, measures no tag lag and pushes nothing |
| `no-push`, or `push_to_remote` absent | Contacts no remote and pushes nothing. Compares `<branch_prefix>main` with its local remote-tracking ref and logs `staged-by-design` when main is ahead; tag lag against origin is not measured |
| `unresolved` | Runs no git command, contacts no remote, prints one `skipped-unresolved-project` line naming the missing piece (`registration`, `project-cfg`, `dev-tree-path` or `resolver`) |

### Backups and revert

Backups live under the project's `overwatch/backups/<TIMESTAMP>/`, one
directory per firing, each keyed on its source's own path:

```
overwatch/backups/<TIMESTAMP>/project-root/<path relative to projects/<name>/>
overwatch/backups/<TIMESTAMP>/absolute/<absolute path without its leading />
```

Block triage groups one level deeper, `<TIMESTAMP>/<task id>/...`. A second
backup of the same source in one firing keeps the first copy.

`team/scripts/overwatch-revert.sh`, given a timestamp, reads the matching
action-log rows and copies every backup each row lists back to its source,
newest row first, so a file two actions touched ends at its pre-firing
content. The revert logs itself as its own row.

## Reading what OVERWATCH did

Each project's `projects/<name>/overwatch/actions.log` is append-only, one
tab-separated row per action:

```
<timestamp>  <entry>  <subject>  <action>  <backups>  <detail>
```

`<backups>` lists every file the action backed up, comma-separated, or
`none`. `overwatch-revert.sh` restores what it lists.

| Entry | Action | Meaning |
|---|---|---|
| `decision-executor` | `auto-fix:<verb>` | A decision's verb ran |
| `decision-executor` | `inbox` | A question was parked |
| `decision-executor` | `runaway` | The runaway detector stopped the project |
| `decision-wake` | `block-triage:<code>:record` | A first cool-down window was recorded |
| `decision-wake` | `decision-wake-declined` | The run stopped early (`HALT_OVERWATCH`, or the repo lock stayed held) |
| `decision-wake` | `decision-wake-dry-run` | A `--dry-run` narration; nothing written |
| `check-block-triage` | `block-triage:<code>:<action>` | Plain block triage wrote to a task |
| `check-block-triage` | `block-triage-refused` | A safety refusal; the task is unchanged |
| any | `firing-skipped:lock-held` | Another firing held the project's firing lock |

```
grep -F 'decision-executor' projects/<name>/overwatch/actions.log
grep -F '## OVERWATCH Decision' -A 12 projects/<name>/release-state.md
```

The Decision events in `release-state.md` carry the evidence each decision
read. How to read one is under "Reading an OVERWATCH Decision event" in
[OPERATIONS.md](OPERATIONS.md).

## HALT flags

| Flag | Effect on OVERWATCH |
|---|---|
| `$KANBAN_ROOT/HALT` (fleet) | The sweep does not run at all |
| `$KANBAN_ROOT/HALT_OVERWATCH` | The sweep does not run; the chain agents ignore this flag |
| `projects/<name>/HALT` | That project is still swept: read-only scans and bug filing run, every fix is deferred, and only the decisions whose subject is that HALT act (clearing a chain HALT, the resolution stage). An operator HALT is never touched |

During an OVERWATCH repair, the project's HALT carries a creator marker:

```
# overwatch-repair-halt: pid=<pid> start=<start> fn=<fix> at=<UTC time>
```

A sweep killed mid-repair cannot remove its own HALT. The next sweep finds
the marker, sees the creator is gone, removes the HALT and logs
`stale-repair-halt-removed`. Only an exact marker naming a dead creator
makes a HALT removable this way.

| Situation | Touch |
|---|---|
| Hand-editing `priority/` or `bugs/` without OVERWATCH acting on them | `HALT_OVERWATCH` |
| Stopping one project while OVERWATCH keeps watching it | `projects/<name>/HALT` |
| Full freeze | `HALT` |

## Where to look next

- [../team/roles/OVERWATCH.md](../team/roles/OVERWATCH.md) — the role file.
- [../team/scripts/lib/overwatch-checks/README.md](../team/scripts/lib/overwatch-checks/README.md)
  — the check catalog.
- [OPERATIONS.md](OPERATIONS.md) — halt runbooks, the resolution stage's
  lanes, HALT-AFTER and metrics.
- [operator-api.md](operator-api.md) — the inbox REST routes.
