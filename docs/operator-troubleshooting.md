# Operator Troubleshooting

Operator guide for the five symptoms you are most likely to see when something looks wrong with the kanban: a slow chain, a slow dashboard, an RC that will not ship, work that appears to have vanished, and a warning email from your hosting provider. Each section is ordered from cheapest, most-likely root cause to most expensive, least-likely. Run the steps top-to-bottom and stop as soon as one fixes the symptom.

The commands assume `PGAI_AGENT_KANBAN_ROOT_PATH` is exported in your shell. Substitute the literal path if it is not.

## Chain seems slow

The kanban chain ships work on cron ticks. "Slow" almost always means one of three things: the hosting provider has CPU-throttled the VPS, the box itself is overloaded, or the chain has a legitimately long-running RC. Check in that order — the hosting layer is by far the most common cause and you cannot diagnose anything else cleanly until you have ruled it out.

### 1. Check the hosting provider first

Before opening the dashboard or any task folder, look at the layer underneath. The textbook pattern: the chain looks slow, every kanban-internal metric reads nominal, and the root cause is a CPU resource limit applied at the VPS layer because the provider flagged the box for abuse. No amount of kanban-side diagnosis will find it.

1. Log into your hosting provider's control panel and check for CPU abuse flags, throttling notices, or unread support tickets. Most providers surface these on the VPS dashboard before they email you.
2. From the VPS itself, compare observed load against the top consumers:

   ```bash
   uptime
   ps aux --sort=-%cpu | head -20
   ```

   If `uptime` shows high load averages but `ps` shows no process consuming significant CPU, the kernel is being denied cycles by the hypervisor — that is hosting-provider throttling.

If the provider is throttling, follow the "Hosting provider sent me a warning" flow below to clear it. Do not continue with kanban diagnostics until the throttle is lifted; the readings will lie to you.

### 2. Check OS-level load

If the provider is clean, look at the box itself.

```bash
uptime
ps aux --sort=-%cpu | head -20
free -h
df -h "$PGAI_AGENT_KANBAN_ROOT_PATH"
```

What you are looking for:

- A load average well above the number of vCPUs you have.
- A process at the top of `ps` that is not part of the kanban (a misbehaving co-tenant service, a runaway `pgai-video-generator` job, a tar/rsync left over from a manual session).
- Memory pressure forcing swap.
- Disk full or nearly full — full disk stalls the chain immediately because status writes fail.

If a non-kanban process is hot, address it directly. If the disk is full, run the disk-hygiene purge (see `disk-hygiene.md`).

### 3. Kanban-internal checks

Only after the hosting and OS layers are clean should you look at the chain itself.

1. Open the dashboard and check the metrics window. In tmux: `Ctrl-B 6`. Read the most recent RC's `wall_time`. A long wall time means an individual task ran long, not that the chain is stuck — the system is working as designed, just on heavy work.
2. Verify cron is actually firing:

   ```bash
   crontab -l | grep pgai
   grep pgai /var/log/cron | tail -20
   ```

   If the crontab is empty or the log shows no recent pgai entries, the chain is not slow — it is stopped. Reinstall cron with `$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/install-crontab.sh`.
3. Check whether the chain is paused: `ls "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT" 2>/dev/null`. If the file exists, the chain is intentionally halted — see "Where did my work go?" below.

## Dashboard takes forever to load

The dashboard reads task folders on every refresh. When loads start taking many seconds, the cause is almost always either VPS throttling or accumulated state under `projects/<name>/tasks/`.

### 1. Verify the VPS is not throttled

Same check as the slow-chain flow:

```bash
uptime
ps aux --sort=-%cpu | head -20
```

If load is high without a visible top consumer, you are being throttled at the hypervisor. Follow the "Hosting provider sent me a warning" flow before doing anything else.

### 2. Check for accumulated task folders

The dashboard scans every task directory under `projects/*/tasks/` on each refresh. A few hundred folders is fine; several thousand is slow; tens of thousands is unusable. A representative chain at ~230 task folders per project per day reaches the slow range in about a month.

```bash
ls "$PGAI_AGENT_KANBAN_ROOT_PATH"/projects/*/tasks 2>/dev/null | wc -l
```

If the count is in the thousands, run the disk-hygiene purge to delete terminal task folders past the retention window. See `disk-hygiene.md` for the full reference; the quick form is:

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/cleanup/purge-old-files.sh"
```

That is a preview. Read it, then run again with `--apply` to delete.

### 3. Kill and recreate the dashboard

If the underlying state is healthy but the tmux session itself has accumulated render glitches or is wedged on a stale read, recycle it.

```bash
tmux kill-session -t kanban
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/dashboard/create.sh"
```

Substitute your actual session name if you renamed it from the default. The dashboard re-scans on first paint, so the first load after recycle may still be slow until the cache is warm.

## RC will not ship

An RC that sits open past its expected ship time is signaling one of four things: TESTER did not recommend ship, CM blocked itself, the chain is paused with `HALT`, or the push to origin is failing. Check in that order.

### 1. Check TESTER's recommendation

The most common reason an RC does not ship is that TESTER ran and recommended against shipping. Find the most recent TESTER task for the project and read its report:

```bash
ls -t "$PGAI_AGENT_KANBAN_ROOT_PATH"/projects/*/tasks/TESTER-*/artifacts/report.md 2>/dev/null | head -1
```

Open the file and look at the `## Recommendation` field. If it is `BLOCK` or `SHIP-WITH-SERIOUS-CONCERNS`, the chain is doing exactly what it should: declining to ship a degraded build. Read the report's findings, address them on the source branch, and let the chain re-run TESTER on the next cycle.

### 2. Check the most recent CM task

If TESTER recommended ship but CM did not finalize, CM blocked itself. Find its status.md:

```bash
ls "$PGAI_AGENT_KANBAN_ROOT_PATH"/projects/*/tasks/CM-*/status.md 2>/dev/null \
  | xargs grep -l "^BLOCKED$" \
  | tail -3
```

Read the most recent BLOCKED CM task's status.md. CM records the trigger (one of the eight enumerated in `team/SOP.md` "When the chain halts"): pre-squash hook failure, merge conflict, push failure, tag already on remote, three consecutive NON-FUNCTIONAL RCs, and so on. The trigger names the work you need to do before the chain can ship.

### 3. Check whether HALT is set

```bash
cat "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT" 2>/dev/null
```

If the file exists, the chain is paused. CM-written HALTs carry a comment header naming the trigger; operator-initiated HALTs are usually empty. Read the header, resolve the underlying issue, then `rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"` to resume.

### 4. Check git push permissions for CM

If TESTER said ship, CM is not blocked, and HALT is not set, but the RC has still not shipped, CM may be failing silently on the push step. Verify origin is reachable and CM has push permission:

```bash
cd "$PGAI_DEV_TREE_PATH"
git remote -v
git ls-remote origin >/dev/null && echo "origin reachable" || echo "origin unreachable"
```

If origin is unreachable or rejects credentials, fix that at the git-config or SSH-key layer. Once push works, CM will retry on the next cron tick.

## Where did my work go?

Three different scenarios all look like "my work has vanished": the chain is paused with `HALT`, the server restarted, or you edited the wrong tree. They have different recoveries.

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

## Hosting provider sent me a warning

You opened your inbox to find a CPU-abuse warning from your VPS provider. This section covers what the warning likely means, why the kanban does not produce sustained CPU load on its own, the PVG-co-hosting consideration, the immediate steps to take, and how to prevent recurrence.

### What the warning likely means

VPS providers send these warnings when a guest's CPU usage trips an abuse threshold — sustained high utilization, excessive process spawning, or both. The pgai-agent-kanban is a cron-driven autonomous system whose wake scripts fast-exit in under one second when there is no BACKLOG task to act on, so an idle chain consumes negligible CPU.

If you are seeing abuse warnings, the most likely culprits are a non-kanban tenant on the same box (commonly the pgai-video-generator), an accumulated state problem (thousands of task folders making the discovery scan expensive), or the combined load of co-hosted projects under sustained work.

### Why the kanban does not run away

Unused provider wake scripts return in well under a second. The chain only does real work when a BACKLOG task exists; everything else is a near-instant no-op. CM also holds HALT authority — if the chain enters a degraded pattern (for example, three consecutive NON-FUNCTIONAL RCs, the eighth HALT trigger), CM autonomously stops the chain and files a bug naming the systemic issue. That is the runaway-defeat mechanism: the system catches its own bad patterns and pauses rather than burning cycles producing more bad work.

In short: a healthy kanban does not produce sustained CPU load by itself. If your VPS is complaining, something else is going on.

### PVG CPU cap

The pgai-video-generator (PVG) project is computationally intensive — it does real video work. When PVG and the kanban share a VPS, their combined load can push the box over the provider's threshold even though each project on its own is fine. The common case: the chain looks slow because the provider has throttled CPU, and the trigger for the throttle is the combined load of PVG and the kanban under sustained activity.

Mitigations:

- **Set a CPU cap at the VPS level for the PVG project**, either via cgroup limits, a `cpulimit` wrapper, or whatever your provider exposes. The kanban does not need a cap because it fast-exits when idle; PVG might.
- **Schedule PVG runs during off-peak hours** if your provider's abuse thresholds are time-sensitive. Many providers tolerate spikes outside business hours that they alert on during the day.
- **Stagger heavy work**. Avoid configurations where both projects can be at peak at the same time.

### Immediate steps when you receive a warning

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

3. **Reply to the provider.** Briefly explain that the box runs an autonomous build system with scheduled cron-driven activity, that you have paused chain work pending diagnosis, and that you are reducing concurrency. Most providers respond well to a clear, prompt acknowledgement.

4. **Reduce concurrency if needed.** Options, in order of preference:
   - Pause PVG separately so the kanban stays responsive on intake while PVG is held:

     ```bash
     touch "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-video-generator/HALT"
     ```

   - Cut cron frequency in the kanban crontab (edit `crontab -e`) if even idle ticks are too frequent for your VPS tier.
   - Stop unrelated services on the same box.

5. **Remove the global HALT once load is under control:**

   ```bash
   rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
   ```

   The chain resumes on the next cron tick. If you paused PVG separately, leave its project-level HALT in place until you have applied the longer-term mitigation (CPU cap, scheduling change).

### Preventing recurrence

- **Keep disk usage and task-folder count low.** The discovery scan reads every task folder on every tick; thousands of accumulated folders add real CPU even on a healthy chain. Run the purge regularly:

  ```bash
  "$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/cleanup/purge-old-files.sh" --apply
  ```

  See `disk-hygiene.md` for the full reference.

- **Keep PVG CPU-capped at the VPS level** when co-hosted with the kanban.

- **Monitor load.** Watch `uptime` or your provider's metrics panel. A load average that climbs week-over-week is a signal to purge state or check for a misbehaving co-tenant before the provider notices it for you.

## Enabling debug logs

When an agent's behavior is hard to diagnose from its task output alone — a queue scan that picks the wrong task, an INI read that returns an unexpected value, a git operation that fails without context — turn on debug logging for that agent and re-run the chain. Debug output is gated per project and per agent, so you can isolate noise to exactly the slice you are debugging without flooding the rest of the chain.

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

`project.cfg` is per-project. Turning on verbose mode for `pgai-agent-kanban` does not affect `pgai-video-generator`, and vice versa. This matters when you co-host: you can debug a misbehaving agent in one project without the other project's logs adding noise to the dashboard.

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
- `team/SOP.md` — full operator SOP, including the "When the chain halts" reference.
