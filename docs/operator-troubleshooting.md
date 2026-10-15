# Operator Troubleshooting

Operator guide for the symptoms you are most likely to see when something looks wrong with the kanban: a slow chain, a slow dashboard, an RC that will not ship, a requirements queue clogged with bundles that carry no work, work that appears to have vanished, CPU limits tripped by a heavy co-hosted workload, and an agent whose behaviour you cannot explain from its task output. Each section is ordered from cheapest, most-likely root cause to most expensive, least-likely. Run the steps top-to-bottom and stop as soon as one fixes the symptom.

**Every section below starts the same way: run `why.sh`.** It is one read-only command that turns "something is wrong" into a named verdict per project, and it costs about a second. Every later step in a section assumes you ran it and it did not settle the question. Nothing in this document asks you to open a log, a task folder, or a hosting control panel before that.

The commands assume `PGAI_AGENT_KANBAN_ROOT_PATH` is exported in your shell. Substitute the literal path if it is not.

## Start here: `why.sh`

```bash
# Every registered project, one verdict line each.
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/why.sh"

# One project.
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/why.sh" --project my-app

# That line, unchanged, plus the full diagnostic pane.
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/why.sh" --project my-app --details
```

Run bare, it walks every registered project and prints one line each, carrying the verdict word, its cause, and a runnable remedy in the same sentence. The verdict word is one of four:

| Verdict | What it means | Where it sends you |
|---|---|---|
| `RUNNING` | A task is WORKING. The line names the task, its state, and how many whole minutes it has held that state — so a lane that keeps picking work up and putting it down again does not read as healthy just because something is technically running. | Nowhere, unless the minute count is implausible. Then "Chain seems slow." |
| `IDLE` | Nothing is WORKING and nothing is BLOCKED. This covers a project disabled in `projects.cfg`, a project holding only queued work, and a project with no non-terminal tasks at all; the line names which. | "Where did my work go?", if you expected something to be moving. |
| `BLOCKED` | A task named an obstacle and stopped. The remedy half of the line names the task to open. | That task's `status.md`. |
| `HALTED` | A project-scoped `HALT` file is holding the whole project. | "Where did my work go?", the HALT flow. |

`IDLE` and a healthy `RUNNING` are ordinary operation. `BLOCKED` and `HALTED` want you, and both name the file or task to open next.

**One scope caveat that applies to every flow below.** `HALTED` names a *project-scoped* halt — a `HALT` file at `projects/<name>/HALT`. The fleet-wide `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT` is a different file at a different scope, and it produces no `HALTED` verdict at all: while it holds, each lane still reports whatever its own task state says, which settles to `IDLE` once in-flight work finishes. So `HALTED` on some projects and not others means a per-project halt, while `IDLE` everywhere means either nothing is queued or the fleet-wide flag is set. One command tells you which:

```bash
ls "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT" 2>/dev/null
```

`--details` adds the diagnostic pane for one project: every lock held for it with holder, age and provenance; the last five entries of every task queue with their raw markers; the dashboard's queue pane embedded verbatim; the whole posture in force (HALT/HALT-AFTER, the resolved deferral posture and the tier each value came from, the Active RC, and every pending requirements bundle with its status); and the project's most recent discovery or gate refusal.

Two cautions about that pane, both of which matter when you are trying to prove a lane is clean. A project that has never recorded a refusal says so in words — but a refusal ledger that cannot be parsed makes the pane refuse outright rather than report an absence it cannot confirm, so a missing pane is never evidence of a clean lane. And the pane is all-or-nothing: every section is computed before anything prints, so you never read a half-rendered answer.

The exit codes are ordered by severity — `0` RUNNING, `2` IDLE, `3` RUNNING but stalled past the threshold, `4` BLOCKED, `5` HALTED, with `1` reserved for a pre-flight refusal where no verdict could be reached at all. An all-projects walk exits with the worst code it saw, which is what makes a single cron line a whole-fleet monitor. The full exit-code table and every flag are in `docs/operator-commands.md` under "why — one verdict per project, with its cause, its remedy, and an exit code"; the walkthrough around the verb is `docs/OPERATIONS.md` under "Why is this lane not moving?".

## Chain seems slow

The kanban chain ships work on cron ticks. "Slow" almost always means one of three things: the hosting provider has CPU-throttled the VPS, the box itself is overloaded, or the chain has a legitimately long-running RC. There is also a fourth possibility that looks identical from the outside and is not slowness at all — the chain is stopped. Establish which of those you are in before you diagnose anything.

### 1. Ask `why.sh` what the lanes are actually doing

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/why.sh"
```

One command, one line per project, and it separates the four cases immediately:

- **Every lane `RUNNING`, minute counts plausible.** The chain is working. What you are calling slow is heavy work, and the rest of this section is about why each tick takes longer than it should.
- **Lanes `RUNNING` but the minute counts keep climbing past anything the task should need.** This is the throttling and overload signature: the agents are alive and being denied cycles. Go to step 2.
- **Any lane `HALTED` or `BLOCKED`.** The chain is not slow, it is stopped, and the line names the file or task holding it. Leave this section and read "Where did my work go?" or "RC will not ship."
- **Every lane `IDLE`.** Nothing is queued, cron is not firing at all, or the fleet-wide `HALT` is set — the scope caveat above separates those. Skip to step 4.

Add `--details` for a project whose verdict you do not believe. A lock with an implausible age, or a queue tail whose raw markers disagree with the tasks' real states, explains a stalled lane faster than any log will.

### 2. Check the hosting provider

Once `why.sh` says the agents are alive but not getting anywhere, look at the layer underneath. The textbook pattern: the chain looks slow, every kanban-internal metric reads nominal, and the root cause is a CPU resource limit applied at the VPS layer because the provider flagged the box for abuse. No amount of kanban-side diagnosis will find it.

1. Log into your hosting provider's control panel and check for CPU abuse flags, throttling notices, or unread support tickets. Most providers surface these on the VPS dashboard before they email you.
2. From the VPS itself, compare observed load against the top consumers:

   ```bash
   uptime
   ps aux --sort=-%cpu | head -20
   ```

   If `uptime` shows high load averages but `ps` shows no process consuming significant CPU, the kernel is being denied cycles by the hypervisor — that is hosting-provider throttling.

If the host is throttling, follow "Capping CPU for heavy co-hosted workloads" below to clear it. Do not continue with kanban diagnostics until the throttle is lifted; the readings will lie to you.

### 3. Check OS-level load

If the provider is clean, look at the box itself.

```bash
uptime
ps aux --sort=-%cpu | head -20
free -h
df -h "$PGAI_AGENT_KANBAN_ROOT_PATH"
```

What you are looking for:

- A load average well above the number of vCPUs you have.
- A process at the top of `ps` that is not part of the kanban (a misbehaving co-tenant service, a runaway render or build job, a tar/rsync left over from a manual session).
- Memory pressure forcing swap.
- Disk full or nearly full — full disk stalls the chain immediately because status writes fail.

If a non-kanban process is hot, address it directly. If the disk is full, run the disk-hygiene purge (see `disk-hygiene.md`).

### 4. Kanban-internal checks

Only after the hosting and OS layers are clean should you look at the chain itself. Step 1 already ruled out the halted and blocked cases, so what is left here is cron and cost.

1. Open the dashboard and check the metrics window. In tmux: `Ctrl-B 6`. Read the most recent RC's `wall_time`. A long wall time means an individual task ran long, not that the chain is stuck — the system is working as designed, just on heavy work.
2. Verify cron is actually firing:

   ```bash
   crontab -l | grep pgai
   grep pgai /var/log/cron | tail -20
   ```

   If the crontab is empty or the log shows no recent pgai entries, the chain is not slow — it is stopped. That is the case a fleet-wide `IDLE` from step 1 points at. Reinstall cron with `$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/install-crontab.sh`.

## Dashboard takes forever to load

The dashboard reads task folders on every refresh. When loads start taking many seconds, the cause is almost always either VPS throttling or accumulated state under `projects/<name>/tasks/`.

### 1. Time `why.sh` to find out whether it is the dashboard at all

```bash
time "$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/why.sh"
```

`why.sh` reads the same task state the dashboard renders, without tmux, without panes, and without a refresh loop. That makes it the cheapest way to split the two candidate causes apart:

- **`why.sh` is also slow.** The problem is underneath the dashboard — throttling or state volume. Continue with step 2 and step 3.
- **`why.sh` returns promptly and prints sane verdicts.** The state layer is healthy and the dashboard's own session is the problem. Skip to step 4 and recycle it.

Read the verdicts while you are here. A dashboard that looks wedged on stale data and a chain that is genuinely `HALTED` render very similarly, and only one of them is a dashboard problem.

### 2. Verify the VPS is not throttled

```bash
uptime
ps aux --sort=-%cpu | head -20
```

If load is high without a visible top consumer, you are being throttled at the hypervisor. Follow "Capping CPU for heavy co-hosted workloads" below before doing anything else.

### 3. Check for accumulated task folders

The dashboard scans every task directory under `projects/*/tasks/` on each refresh. A few hundred folders is fine; several thousand is slow; tens of thousands is unusable. A representative chain at ~230 task folders per project per day reaches the slow range in about a month.

```bash
ls "$PGAI_AGENT_KANBAN_ROOT_PATH"/projects/*/tasks 2>/dev/null | wc -l
```

If the count is in the thousands, run the disk-hygiene purge to delete terminal task folders past the retention window. See `disk-hygiene.md` for the full reference; the quick form is:

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/cleanup/purge-old-files.sh"
```

That is a preview. Read it, then run again with `--apply` to delete.

### 4. Kill and recreate the dashboard

If the underlying state is healthy but the tmux session itself has accumulated render glitches or is wedged on a stale read, recycle it.

```bash
tmux kill-session -t kanban
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/dashboard/create.sh"
```

Substitute your actual session name if you renamed it from the default. The dashboard re-scans on first paint, so the first load after recycle may still be slow until the cache is warm.

## RC will not ship

An RC that sits open past its expected ship time is signaling one of four things: TESTER did not recommend ship, CM blocked itself, the chain is paused with `HALT`, or the push to origin is failing. `why.sh` tells you which of the middle two you are in outright, so ask it first and let the answer choose your next step.

### 1. Get the project's verdict

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/why.sh" --project my-app --details
```

The verdict word routes you directly:

- **`HALTED`** — a project-scoped `HALT` file is holding this project, and the remedy half of the line names it. Jump to step 4; you can skip steps 2 and 3 entirely.
- **`BLOCKED`** — a task named an obstacle and stopped, and the remedy half of the line names that task. If it is a CM task, go to step 3 and read its `status.md`. If it is a CODER, WRITER or TESTER task, the RC is not stuck on release mechanics at all — it is stuck on the work, and the named task tells you on what.
- **`RUNNING`** — the chain is still working the RC. Check the minute count before concluding anything is wrong; an RC that is merely long is not an RC that will not ship.
- **`IDLE`** — nothing is WORKING and nothing is BLOCKED, yet an RC is open. That is the case step 2 and step 5 exist for: TESTER has already spoken, or CM's push is failing silently.

The `--details` pane also prints the Active RC and every pending requirements bundle with its status, which is the quickest confirmation that the RC you think is open actually is.

### 2. Check TESTER's recommendation

The most common reason an RC does not ship is that TESTER ran and recommended against shipping. Find the most recent TESTER task for the project and read its report:

```bash
ls -t "$PGAI_AGENT_KANBAN_ROOT_PATH"/projects/*/tasks/TESTER-*/artifacts/report.md 2>/dev/null | head -1
```

Open the file and look at the `## Recommendation` field. If it is `BLOCK` or `SHIP-WITH-SERIOUS-CONCERNS`, the chain is doing exactly what it should: declining to ship a degraded build. Read the report's findings, address them on the source branch, and let the chain re-run TESTER on the next cycle.

### 3. Check the most recent CM task

If TESTER recommended ship but CM did not finalize, CM blocked itself. Find its status.md:

```bash
ls "$PGAI_AGENT_KANBAN_ROOT_PATH"/projects/*/tasks/CM-*/status.md 2>/dev/null \
  | xargs grep -l "^BLOCKED$" \
  | tail -3
```

Read the most recent BLOCKED CM task's status.md. CM records the trigger (one of those enumerated in `docs/OPERATIONS.md` "When the chain halts"): pre-squash hook failure, merge conflict, push failure, tag already on remote, three consecutive NON-FUNCTIONAL RCs, and so on. The trigger names the work you need to do before the chain can ship.

### 4. Check whether HALT is set

Check both scopes — a `HALTED` verdict in step 1 already named the project-scoped one, and the fleet-wide file produces no verdict at all:

```bash
cat "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/my-app/HALT" 2>/dev/null
cat "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT" 2>/dev/null
```

If either file exists, the chain is paused. CM-written HALTs carry a comment header naming the trigger; operator-initiated HALTs are usually empty. Read the header, resolve the underlying issue, then remove whichever file you found — the project-scoped one resumes that lane alone, the fleet-wide one resumes everything.

### 5. Check git push permissions for CM

If TESTER said ship, CM is not blocked, and HALT is not set, but the RC has still not shipped, CM may be failing silently on the push step. Verify origin is reachable and CM has push permission:

```bash
cd "$PGAI_DEV_TREE_PATH"
git remote -v
git ls-remote origin >/dev/null && echo "origin reachable" || echo "origin unreachable"
```

If origin is unreachable or rejects credentials, fix that at the git-config or SSH-key layer. Once push works, CM will retry on the next cron tick.

## The requirements queue is full of near-identical bundles

You opened a project's `requirements/` directory and found a long run of bundle files that all look the same: consecutive patch versions, the same dateline in every filename, and — when you open two or three of them — a `## Bundled Items` section carrying pipeline log lines instead of bug or priority files. Each of those files is a release the chain still intends to ship, and each one that reaches PM is decomposed into an RC with no work in it.

The cost is the version line. A bundle claims its target version the moment discovery writes it, so a run of thirty empty bundles has already spent thirty patch numbers. The chain works through them one RC at a time, the patch component climbs, and the lane eventually stops queuing PM at all because the next target exceeds the project's `max_patch` ceiling. From the outside that reads as an idle lane with a full queue, which is the least informative symptom the kanban produces.

`list-stale-requirements.sh` will not find these. It excludes bundle files by design — they are ordinarily legitimate history — so the inventory it prints is empty while the queue is anything but.

### 1. Ask `why.sh` what the lane is doing

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/why.sh" --project my-app --details
```

The verdict line is usually `IDLE` or an unremarkable `RUNNING`; neither is the tell. The tell is in the `--details` pane, which lists every pending requirements bundle with its status. A column of bundles at `open`, several patch versions deep, is this condition. Nothing is broken, so nothing has refused and nothing is blocked — that is exactly why the condition survives so long.

### 2. Read the run in the requirements directory

```bash
ls "$PGAI_AGENT_KANBAN_ROOT_PATH"/projects/my-app/requirements/
```

Three things together identify it, and all three have to hold:

- **Consecutive patch versions.** `v0.21.30-bugfix-bundle-20260419.md`, `v0.21.31-...`, `v0.21.32-...`, with no gaps.
- **One dateline across the whole run.** Every filename carries the same date, because the run was produced by repeated discovery passes inside a single day rather than by a month of real work.
- **No resolvable items.** Open one and read `## Bundled Items`. A healthy bundle's lines carry a parenthesised, backquoted path to a bug or priority file that exists on disk. An empty bundle's lines are narration — timestamps and pipeline messages — with nothing behind them.

A run that fails the third test is ordinary backlog, not this condition. Do not retire it.

### 3. Confirm the ceiling is where the lane stopped

If the lane has already gone quiet, check the ceiling against the versions in the run:

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/set-version-ceiling.sh" --project my-app --show
```

When the highest bundle in the run sits above `max_patch`, discovery is refusing to queue PM for it and saying so in the cron log. `docs/OPERATIONS.md` under "Version Ceilings" has the check, the rejection log lines, and how to move a ceiling. Raising the ceiling is not the fix here — it buys the empty bundles more room to consume. Clear them first, then decide whether the ceiling still needs to move.

### 4. Dry-run the retirement

`retire-empty-bundles.sh` is the tool for this class of file. Report mode is the default: it scans, prints a verdict per bundle, and writes nothing.

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/retire-empty-bundles.sh" --project my-app
```

Each bundle gets a stanza, and the verdict word is the line to read:

```
file:    v0.21.31-bugfix-bundle-20260419.md
status:  open
items:   2 item line(s) under Bundled Items, none resolve to an existing file
verdict: retirable
```

`retirable` is what the apply pass will act on. `kept` means at least one item line resolved to a real file — that bundle carries work and is never touched. `skipped` means the bundle is already `done` or `running`. The closing summary counts all three.

Read the retirable list against what you found in step 2 before going further. The eligibility rule the script applies is stated authoritatively by the script itself:

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/retire-empty-bundles.sh" --help
```

### 5. Apply

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/retire-empty-bundles.sh" --project my-app --apply
```

The apply pass performs the same scan and then sets `## Status` to `done` on exactly the bundles it classified `retirable`. Three things it does not do, all of which matter if you are nervous about running it:

- **It deletes nothing.** Every requirements file survives, readable, exactly where it was.
- **It edits one field.** Every other header line in every file — retirable, kept, or skipped — is left byte-identical.
- **It touches nothing it did not classify retirable.** A `kept` or `skipped` bundle is unmodified in apply mode just as it is in report mode.

`## Status: done` is what the discovery pipeline reads to mean "this bundle is finished", so a retired bundle stops being eligible for PM pickup and stops consuming a version slot. It remains on disk as history.

### 6. Confirm the queue drained

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/retire-empty-bundles.sh" --project my-app
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/why.sh" --project my-app --details
```

A second report run should show the same files as `skipped (already done)` and zero retirable. The `--details` pane should show the pending-bundle list reduced to the bundles that carry real work. The chain picks up the lowest remaining eligible bundle on the next cron tick — provided its target version is still under the ceiling, which is the point at which step 3 becomes a real decision rather than a diagnostic.

## Where did my work go?

Three different scenarios all look like "my work has vanished": the chain is paused with `HALT`, the server restarted, or you edited the wrong tree. They have different recoveries, and `why.sh` tells you which one you are in before you start undoing anything.

### First: which of the three is it?

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/why.sh"
```

Read the verdicts against the three scenarios:

- **`HALTED` on one or more projects.** That project's chain is paused, not lost. Go to the HALT flow immediately below; the verdict line already names the file holding the project and the trigger recorded in it.
- **`IDLE` everywhere, with tasks you know exist.** Nothing is being pulled. Per the scope caveat in "Start here," this is also how a fleet-wide `HALT` looks, so check that file first; if it is absent, the crontab probably did not survive a restart, and the restart flow is yours.
- **`RUNNING` or `BLOCKED`, with the work visibly progressing or stopped on a named obstacle.** The chain has your work and is acting on it. The thing that vanished is almost certainly an edit you made in the wrong tree — go to the manual-edit flow.

Run this before you `rm` anything. The most expensive mistake available in this section is deleting a `HALT` file that was carrying a real signal, and the verdict line names the trigger before you touch it.

### HALT set — chain is paused

**Symptom.** The dashboard shows the chain idle. No new tasks pull. A `HALT` file exists at `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT`.

**Recovery.**

1. Read the HALT reason:

   ```bash
   cat "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
   ```

   CM-written HALTs carry a comment header naming the trigger. Operator-initiated HALTs are usually empty — meaning someone (possibly you) paused the chain manually.

2. Find the BLOCKED CM task that wrote the HALT:

   ```bash
   ls "$PGAI_AGENT_KANBAN_ROOT_PATH"/projects/*/tasks/CM-*/status.md 2>/dev/null \
     | xargs grep -l "^BLOCKED$" \
     | tail -3
   ```

3. Read that task's status.md for the full reason and any context CM recorded.

4. Resolve the underlying issue. The trigger names what to do: a merge conflict needs hand-resolution on the source branch; a failed push needs the origin connection fixed; a NON-FUNCTIONAL-pattern halt (trigger 8) needs human review of the last three RCs.

5. Remove HALT:

   ```bash
   rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
   ```

6. The chain resumes on the next cron tick. In-flight WORKING tasks survived the pause and pick up from where they were.

### Server restart or reboot

**Symptom.** The tmux dashboard session is gone. The chain appears stopped. Cron entries may or may not have survived depending on whether the box was rebuilt or just rebooted.

**Recovery.**

1. Verify the crontab is intact:

   ```bash
   crontab -l | grep pgai
   ```

   If the output is empty or missing the pgai entries, reinstall:

   ```bash
   "$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/install-crontab.sh"
   ```

   The script prompts for a cadence tier (`small` / `medium` / `large`; default: `small`).
   Pass `--tier=<tier>` to skip the prompt, for example `--tier=medium`.
   Non-interactive environments (no TTY) automatically select `small`.

2. Recreate the tmux dashboard from your kanban root's dashboard create script. The session name and exact script path depend on your install; the script is under `scripts/dashboard/`.

3. Check for in-flight WORKING tasks. They survived the reboot — task state is on disk, not in memory:

   ```bash
   grep -rl "^WORKING$" "$PGAI_AGENT_KANBAN_ROOT_PATH"/projects/*/tasks/*/status.md 2>/dev/null
   ```

   The chain will resume working these tasks automatically once cron fires.

4. If a `HALT` file exists from a pre-restart CM halt, address its trigger before removing it. Do not blindly delete HALT during recovery — it may be carrying a serious signal.

### Work appears missing after a manual edit

**Symptom.** Changes you saw in the dashboard or made directly in a task folder are gone. You are sure you saved them.

**Most likely cause.** You edited the live install path (`$PGAI_AGENT_KANBAN_ROOT_PATH`) instead of the dev tree (`$PGAI_DEV_TREE_PATH`). The live install is regenerated by `install.sh` from the dev tree — anything edited only in the live install is overwritten the next time install runs.

**Recovery.**

1. Confirm which path you edited:

   ```bash
   echo "live:  $PGAI_AGENT_KANBAN_ROOT_PATH"
   echo "dev:   $PGAI_DEV_TREE_PATH"
   ```

2. If you edited the live install, the changes are gone — `install.sh` does not back them up. Redo the edit in the dev tree, commit on a feature branch, merge into the source branch, and re-run `install.sh` to propagate. The dev tree is the source of truth; the live install is downstream.

3. To prevent recurrence, keep two terminals: one rooted in `$PGAI_DEV_TREE_PATH` for editing, one rooted in `$PGAI_AGENT_KANBAN_ROOT_PATH` for inspecting live state. Never edit in the live root.

## Capping CPU for heavy co-hosted workloads

The kanban is light on CPU, but it often shares a host with something that is not: a media-rendering pipeline, a large build, a model-training job. When the two peak together, their combined load can trip the host's CPU limits even though each one alone is fine. You see it as throttling — the chain slows while every kanban-internal metric reads nominal — or as a CPU-usage warning from whoever runs the host.

This section covers how to find out what was actually running, why the kanban is rarely the source of sustained load, how to cap the heavy workload, what to do in the moment, and how to keep it from recurring.

### First: find out what the chain was actually doing

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/why.sh"
```

Do this before you pause anything, because pausing the chain destroys the evidence you are about to need. The walk takes a second and gives you two things at once: a record of what each lane was doing while the load was high, and the answer to whether the kanban is even a plausible suspect.

A fleet that reads `IDLE` or `HALTED` across the board was not the cause of a sustained CPU load, and that is worth knowing before you spend an afternoon tuning cron. A fleet with several lanes `RUNNING` on long-held tasks is at least a contributor, and the project names on those lines tell you which lane to pause first in step 4 of "Immediate steps" below.

Save the output. If whoever runs the host has asked what the box was doing, a short, factual "here is what my build system was running" is the most useful reply you can give.

### Why the kanban is rarely the source

The kanban is a cron-driven autonomous system whose wake scripts fast-exit in under one second when there is no BACKLOG task to act on, so an idle chain consumes negligible CPU. The chain only does real work when a BACKLOG task exists; everything else is a near-instant no-op.

CM also holds HALT authority. If the chain enters a degraded pattern (for example, three consecutive NON-FUNCTIONAL RCs, one of CM's autonomous HALT triggers), CM stops the chain and files a bug naming the systemic issue. That is the runaway-defeat mechanism: the system catches its own bad patterns and pauses rather than burning cycles producing more bad work.

So when a host hits its CPU limits, the likely culprits are, in order: a heavy workload co-hosted on the same box, an accumulated state problem (thousands of task folders making the discovery scan expensive), or the combined load of several co-hosted projects under sustained work.

### Cap the heavy workload

Cap the workload that actually needs it. The kanban does not need a cap because it fast-exits when idle; a heavy co-hosted job might.

- **Set a CPU cap on the heavy workload at the host level** — cgroup limits, a `cpulimit` wrapper, or whatever CPU controls your host exposes.
- **Schedule heavy runs off-peak** if your host's CPU limits are time-sensitive. Some hosts tolerate spikes outside business hours that they would flag during the day.
- **Stagger heavy work.** Avoid configurations where the heavy workload and the kanban's busiest lanes can peak at the same time.

### Immediate steps when the host is throttled or warns you

1. **Pause the chain.** Touch `HALT` so no new chain work pulls while you investigate:

   ```bash
   touch "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
   ```

2. **Identify the top CPU consumers**:

   ```bash
   ps aux --sort=-%cpu | head -20
   uptime
   ```

   If the top processes are not kanban-related, that is your culprit. Address it directly.

3. **Reply to the host if it contacted you.** Briefly explain that the box runs an autonomous build system with scheduled cron-driven activity, that you have paused chain work pending diagnosis, and that you are reducing concurrency. A clear, prompt acknowledgement usually resolves the conversation.

4. **Reduce concurrency if needed.** Options, in order of preference:
   - If the heavy workload runs as a kanban project, pause that project alone so the rest of the kanban stays responsive on intake while it is held:

     ```bash
     touch "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/my-app/HALT"
     ```

   - Cut cron frequency in the kanban crontab (edit `crontab -e`) if even idle ticks are too frequent for your host's tier.
   - Stop unrelated services on the same box.

5. **Remove the global HALT once load is under control:**

   ```bash
   rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
   ```

   The chain resumes on the next cron tick. If you paused the heavy project separately, leave its project-level HALT in place until you have applied the longer-term mitigation (CPU cap, scheduling change).

### Preventing recurrence

- **Keep disk usage and task-folder count low.** The discovery scan reads every task folder on every tick; thousands of accumulated folders add real CPU even on a healthy chain. Run the purge regularly:

  ```bash
  "$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/cleanup/purge-old-files.sh" --apply
  ```

  See `disk-hygiene.md` for the full reference.

- **Keep heavy co-hosted workloads CPU-capped** at the host level.

- **Monitor load.** Watch `uptime` or your host's metrics panel. A load average that climbs week-over-week is a signal to purge state or check for a misbehaving co-tenant before the host's limits catch it for you.

## Enabling debug logs

When an agent's behavior is hard to diagnose from its task output alone — a queue scan that picks the wrong task, an INI read that returns an unexpected value, a git operation that fails without context — turn on debug logging for that agent and re-run the chain. Debug output is gated per project and per agent, so you can isolate noise to exactly the slice you are debugging without flooding the rest of the chain.

### 0. Try `--details` before you turn anything on

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/why.sh" --project my-app --details
```

Verbose mode costs a full chain cycle before it tells you anything, and most of what operators reach for it to find is already in this pane, now: which lock is held and by whom, the queue tails with their raw markers beside the tasks' real states, the whole resolved posture with the tier each value came from, the Active RC, the pending bundles, and the project's most recent discovery or gate refusal with its `error`/`message`/`remedy`.

Three of the four symptoms in the paragraph above are visible there. A queue scan picking the wrong task shows up as a marker/state disagreement in the queue tails. An INI read returning an unexpected value shows up as a posture line naming a tier you did not expect. A refusal you never saw shows up in the refusal section verbatim.

Turn on verbose mode when `--details` did not answer it — typically for git operations and for behaviour inside a single agent's run, neither of which the pane can see.

### 1. Enable verbose mode in the project's `project.cfg`

Open the project config at `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<project>/project.cfg` and set the `[debug]` section:

```ini
[debug]
verbose_mode = true
verbose_agents = pm,coder,writer,tester,cm
```

- `verbose_mode` — master switch for that project. When `true`, agents listed in `verbose_agents` emit extra diagnostic output (expanded INI reads, queue scans, git operations) to their debug log on every wake. Default is `false`.
- `verbose_agents` — comma-separated list of agent roles that emit debug logs when `verbose_mode = true`. Allowed values are `pm`, `coder`, `writer`, `tester`, `cm`. Default is all five. Narrow the list to silence agents you do not care about — for example, `verbose_agents = coder,writer` keeps the debug stream focused while you investigate a CODER issue.

Both keys live under `[debug]`. If the section or either key is absent, the default is "off" for that project — no debug output regardless of what other projects have configured.

### 2. Each project gates independently

`project.cfg` is per-project. Turning on verbose mode for `pgai-agent-kanban` does not affect `pgai-chomp-man`, and vice versa. This matters when you co-host: you can debug a misbehaving agent in one project without the other project's logs adding noise to the dashboard.

### 3. Per-agent gating still applies

Even when `verbose_mode = true`, only agents named in `verbose_agents` emit debug logs. An agent absent from the list runs silently no matter what the master switch says. This is how you keep the signal-to-noise ratio sane during long debugging sessions.

### 4. The legacy `PGAI_VERBOSE_MODE` env var is deprecated

Older installs enabled verbose output by exporting `PGAI_VERBOSE_MODE=1` in the cron environment. That shim still works — when the env var is set, every project and every agent emits debug output — but each wake also logs a one-shot deprecation warning:

```
WARNING: PGAI_VERBOSE_MODE env var is deprecated; move per-project debug control to project.cfg [debug] verbose_mode
```

The canonical replacement is the `[debug]` block above. Remove `PGAI_VERBOSE_MODE` from your cron environment once you have configured `project.cfg` for the projects you actually want to debug.

### 5. Where the debug output goes

Debug logs are written to `$PGAI_AGENT_KANBAN_ROOT_PATH/logs/debug/<agent>.log` — one file per agent role, shared across all projects (the log files themselves are kanban-wide, not per-project). View them either with `tail -F` directly or via the dashboard's debug-logs pane:

```bash
tail -F "$PGAI_AGENT_KANBAN_ROOT_PATH"/logs/debug/*.log
```

In the tmux dashboard, the debug-logs pane is Window 8. When more than one project has verbose mode enabled, each line in the pane is prefixed with `[proj1,proj2] [agent] ...` so you can tell which multi-project context produced the stream. When only one project is verbose, the prefix is omitted and lines render as `[agent] ...`. See `DASHBOARD-PANES.md` for the full pane reference.

## See also

- `quarantine-recovery.md` — recovering rejected priority and bug files.
- `disk-hygiene.md` — the purge script, retention defaults, cron integration.
- `dashboard.md` — dashboard layout and panes.
- `DASHBOARD-PANES.md` — per-pane script and standalone-command reference.
- `docs/operator-commands.md` — the `why.sh` reference: every flag, the four verdicts, and the full exit-code table.
- `docs/OPERATIONS.md` — full operator walkthrough, including "Why is this lane not moving?" and the "When the chain halts" reference.
