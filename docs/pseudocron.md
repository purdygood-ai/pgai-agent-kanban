# Pseudocron — Scheduling Without cron

Pseudocron drives the autonomous chain on hosts where cron is unavailable or
unwanted — containers, restricted shells, or a box where you don't want the
framework touching the system crontab. It is a small foreground Python loop
that fires the same wake jobs the crontab would, on the same tiers.

Use exactly one scheduler at a time. If cron is installed, pseudocron is
redundant; if you are testing pseudocron, remove the crontab first:

```bash
crontab -l > ~/crontab-backup.txt && crontab -r
```

Note that the installers re-apply a crontab by default when pseudocron is
your scheduler: pass `--no-system-cron` to `install.sh`; for `upgrade.sh`,
answer N at the crontab prompt (it prompts even under `--force`), or pass
`--wake-tier none` when you are not changing tiers.

## Setup

`install.sh` (or `scripts/install-pseudocron.sh`) puts three files in place:

- `pseudocron.cfg` — the job table (same entries as the crontab tier)
- `pseudocron.env` — environment for dispatched jobs
- `scripts/pseudocron.py` — the loop

Edit `pseudocron.env` and fill in real values. **Literal values only — no
`$VAR` references.** The parser performs no shell expansion; a value like
`$HOME/pgai_agent_kanban` is passed through as a literal dollar-sign string.
The three that matter:

```ini
PGAI_AGENT_KANBAN_ROOT_PATH=/home/you/pgai_agent_kanban
HOME=/home/you
PATH=/home/you/.local/bin:/usr/local/bin:/usr/bin:/bin
```

`HOME` is how the provider CLI finds its credential store (e.g. `~/.claude`
for OAuth); `PATH` must include wherever the provider CLI and your Python
live. API keys stay in the `secrets` file (chmod 600) — the wake scripts
source it per run; do not copy credentials into `pseudocron.env`.

## Running

`pseudocron.py` takes **no arguments** — it resolves `pseudocron.cfg` and
`pseudocron.env` from `$PGAI_AGENT_KANBAN_ROOT_PATH` (default
`~/pgai_agent_kanban`). It is a foreground process; wrap it in tmux, or a
container entrypoint, or systemd:

```bash
source ~/pgai_agent_kanban/shell-env
tmux new -d -s pseudocron \
  'python3 $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/pseudocron.py \
     >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/pseudocron.log 2>&1'
```

On startup it prints two banner lines — `N jobs loaded` (the tier's job
count) and `N vars loaded` (your env file parsed; if this is 0, fix
pseudocron.env before anything else). Each dispatch then logs a
`<timestamp> fired (minute=NN): <command>` heartbeat line.

A minimal systemd unit, for container/service deployments:

```ini
[Unit]
Description=pgai-agent-kanban pseudocron
After=network.target

[Service]
Environment=PGAI_AGENT_KANBAN_ROOT_PATH=/home/you/pgai_agent_kanban
ExecStart=/usr/bin/python3 /home/you/pgai_agent_kanban/scripts/pseudocron.py
Restart=on-failure
User=you

[Install]
WantedBy=multi-user.target
```

## Verifying it is the sole driver

```bash
crontab -l                        # "no crontab" — nothing else is ticking
tail -f logs/pseudocron.log       # fired lines at the tier cadence
tail -f logs/cron-pm.log          # the jobs land in the same wake logs cron used
```

The wake logs are identical between schedulers by design — pseudocron
dispatches the same `wake-batch.sh` commands the crontab lines contain.

## Stopping

Send SIGINT/SIGTERM (Ctrl-C, `tmux kill-session -t pseudocron`, or
`systemctl stop`); it logs a shutdown line and exits cleanly. Jobs already
dispatched run to completion — the per-agent wake locks and the HALT file
behave exactly as under cron.
