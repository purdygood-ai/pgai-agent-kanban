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

## The ROOT-RELATIVE command contract

Commands in `pseudocron.cfg` are ROOT-RELATIVE. Each job's command is a
plain relative path starting from the kanban root — for example:

```
0 scripts/wake-batch.sh --agent=pm --sleep=0 >> logs/cron-pm.log 2>&1
```

No absolute paths. No `__KANBAN_ROOT__` placeholder. No `$VAR` expansion.
The command is the literal string the parser reads; `pseudocron.py` runs
it verbatim, and the paths inside it resolve because the loop launches
each job with `cwd` set to the resolved kanban root.

This is a runtime guarantee, not a config-time trick. `pseudocron.py`
resolves the root once at startup via `resolve_kanban_root()`
(`PGAI_AGENT_KANBAN_ROOT_PATH`) and then invokes every dispatched job as:

```python
subprocess.Popen(["bash", "-c", command], env=child_env, cwd=root)
```

Because `cwd=root` is applied to every fire, `scripts/wake-batch.sh` and
`logs/cron-pm.log` resolve against the current install location — not the
location where pseudocron was installed, and not the shell that launched
pseudocron. The install is relocation-safe: bind-mounting the root at a
different path inside a container, moving it to another disk, or renaming
the directory does not require touching `pseudocron.cfg`.

The literal-only cfg parser is preserved by design. If you need a value
expanded (a `HOME`, a `PATH`, a per-role model override), put it in
`pseudocron.env` where the env parser reads it as a key/value pair. Do
not try to `$VAR`-substitute inside cfg lines; the parser will pass the
dollar-sign literal through and the job will fail at run time.

Templates ship this form. `team/templates/install/pseudocron-{small,
medium,large}.cfg.example` all use ROOT-RELATIVE commands, and
`install-pseudocron.sh` copies the chosen tier template directly into
`$PGAI_AGENT_KANBAN_ROOT_PATH/pseudocron.cfg` — no placeholder
substitution step. When you author a new custom job by hand, write it in
the same form and it will inherit the same relocation guarantee.

### Migrating an existing absolute-path pseudocron.cfg

Earlier releases wrote `pseudocron.cfg` by substituting a
`__KANBAN_ROOT__` placeholder with the install-time absolute path,
freezing that host's directory layout into every command line. Those
cfgs still work in place — the parser is literal-only and pseudocron
executes whatever command string it reads — but they break the moment
the install moves. Bind-mounting the root at a new path in a container,
relocating the disk, or renaming the top-level directory leaves every
scheduled command pointing at the old, now-nonexistent path.

To migrate an existing absolute-path cfg to the relocation-safe
ROOT-RELATIVE form, regenerate it from the current tier template:

```bash
team/scripts/install-pseudocron.sh --wake-tier <small|medium|large> --yes
```

Passing `--wake-tier` explicitly makes the script overwrite the existing
`pseudocron.cfg` after backing it up (the backup is written to
`~/.pseudocron.cfg.before-install-<timestamp>.bak`). The new cfg uses
ROOT-RELATIVE commands and inherits the cwd guarantee described above.
`pseudocron.env` is not touched — operator customizations there are
preserved.

If you have hand-authored custom jobs in the old cfg (schedule entries
you added beyond what the tier template produced), copy those entries
from the backup into the newly-installed `pseudocron.cfg` after
rewriting each command to the ROOT-RELATIVE form — drop the leading
absolute prefix that used to precede `scripts/` or `logs/`, and leave
the rest of the line intact.

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
