# pgai-agent-kanban — Operator How-To

The operator manual: setup, project management, workflows, agents, intake
documents, the dashboard, and stopping the system safely. The [README](README.md)
explains what the framework is; this document explains how to run it. For the
operator command surface in one place — `reset`, `show`, `show-readme`,
`close`, `delete`, `wontdo`, the `halt` family, and `unwind-rc` — see
[docs/operator-commands.md](docs/operator-commands.md). It also covers every
other operator tool the install ships, and lists the internal checks that you
never need to run yourself.

Paths in this document are live-install paths (`$KANBAN_ROOT/scripts/...`).
The framework runs standalone: no project is special, and a missing dev tree
degrades only the project that owns it.

---

> **New here?** This document is the reference. For a guided first run
> that ends in a real release, walk `demos/chomp-man-demo/README.md`
> (release workflow) or `demos/three-bears-demo/README.md` (document
> workflow) — everything below is easier to absorb after seeing one run.

## 1. Setup

### 1.1 Install

`install.sh` is the **fresh-install path only**. It lays a complete kanban
root down from this checkout — scripts, role files, governance, templates,
docs — seeds `kanban.cfg` and `claude-env` from their templates, and installs
the wake schedule. To move an install that already exists to a newer version,
use `upgrade.sh` (section 10); `install.sh --upgrade` is refused by name.

<!-- doc-lint: skip — install narrative requires a fresh checkout and no prior install; cannot run verbatim in harness -->
```bash
git clone <repo-url> ~/develop/pgai-agent-kanban
cd ~/develop/pgai-agent-kanban
pip install -r requirements.txt --break-system-packages   # drop the flag inside a venv
./install.sh --add-claude-agents --wake-tier medium
```

Install the Python dependencies before `install.sh`: it checks for them and
warns about any that are missing, and the kanban needs them at runtime.
`requirements-test.txt` adds the test runners on top of `requirements.txt`.

Both flags in that command are optional and neither is the default: without
`--add-claude-agents` nothing is written outside the kanban tree, and without
`--wake-tier` the install takes the `small` tier. Pick the tier that matches
the host (see "The wake tiers" below).

The install target defaults to `~/pgai_agent_kanban`; override it with the
`PGAI_AGENT_KANBAN_ROOT_PATH` environment variable. Pointing that variable
somewhere else is also how you run a second install on one host — give the
second one `--no-system-cron` so it does not overwrite the first one's
crontab.

A fresh install registers no projects. Put the export in your shell profile,
then create your first project (section 2).

```bash
export PGAI_AGENT_KANBAN_ROOT_PATH="$HOME/pgai_agent_kanban"   # in .bashrc
```

#### The three install shapes

There are two schedulers on two independent axes — the OS crontab and the
bundled pseudocron — and exactly one flag decides which of them the install
touches. Everything else about the three shapes below is identical.

| Shape | What you run | OS crontab | pseudocron config |
|---|---|---|---|
| **System cron** (the default) | `install.sh` | Installed at the selected wake tier | Installed, inert |
| **Cron-less host, Docker, customer site** | `install.sh --no-system-cron` | Left exactly as it is: no writes, no probes | Installed, inert |
| **Container** | `install.sh --no-system-cron` on the host, against the directory you will mount as the container's kanban root | Left exactly as it is | Installed, inert; started inside the container |

**The pseudocron config installs in all three shapes, always.** That is the
part operators most often get wrong: `--no-system-cron` skips the OS crontab
and nothing else. The pseudocron files are laid down regardless, and they do
nothing at all until you start the scheduler yourself — which is what makes a
cron-less host work out of the box and makes installing them on a
cron-equipped host harmless. Start it with the bundled pseudocron scheduler
(scripts/pseudocron.py in the install); the host-side procedure is
[docs/pseudocron.md](docs/pseudocron.md).

**The container shape is the cron-less shape, run on the host.** There is no
separate in-container installer. You run `install.sh --no-system-cron` on the
host against the directory that becomes the container's volume, then mount
that directory — the container refuses a raw git checkout, because the paths
it expects are the install layout, not the checkout layout. The image build,
the flavors, the mount contract, and starting pseudocron and the board inside
the container are all [docs/docker.md](docs/docker.md)'s; this section owns
only the install command the container path shares with the host path.

**Two schedulers, one heartbeat.** A crontab on the host and a pseudocron
inside a container pointed at the same kanban root fire the same jobs twice.
Pick one per install.

To run the kanban detached in a container rather than on host cron, use
`container-up.sh`, `container-status.sh` and `container-down.sh`, which start,
report on and soft-stop one named container with its scheduler inside; see
[Run the kanban in a container](docs/docker.md#run-the-kanban-in-a-container).

#### Every flag install.sh accepts

Reconciled against `install.sh --help`. Nothing it accepts is omitted here.

| Flag | Effect |
|---|---|
| `[VERSION]` (positional) | Lay down a specific released tag's tree instead of this checkout's. See "Targeting a specific version" below. |
| `--add-claude-agents` | Deploy provider agent wrappers to `~/.claude/agents/`. Strict opt-in: without the flag nothing is written under `~/.claude` — no files, no directory creation, no prompts. Override the directory with `CLAUDE_CONFIG_DIR`. |
| `--add-codex-agents` | The same, for `~/.codex/agents/`. Pass both flags to deploy wrappers to both providers. |
| `--wake-tier <tier>` | `small`, `medium`, or `large`; also accepted as `--wake-tier=<tier>`. Selects the cadence template for **both** the OS crontab and pseudocron. Default when omitted: `small`. |
| `--no-system-cron` | Skip OS crontab installation. pseudocron config is still installed. Use for cron-less, Docker, and customer-site deployments. |
| `--force` | Overwrite existing files without prompting, and proceed past the existing-install precondition check. This is the flag behind the sanctioned downgrade path in section 10. |
| `--dry-run` | Print what would be installed and write nothing. Does not modify `$KANBAN_ROOT/.installed-subagents`, and installs neither scheduler. |
| `--stamp-version STRING` | Write `STRING` verbatim to `VERSION`, bypassing git resolution and suppressing the divergence advisory. Build and packaging use; not an operator knob. |
| `--help`, `-h` | Print usage and exit 0. |

Two environment variables complete the surface: `PGAI_AGENT_KANBAN_ROOT_PATH`
(where to install; default `$HOME/pgai_agent_kanban`) and `CLAUDE_CONFIG_DIR`
(the Claude Code config directory; default `$HOME/.claude`).

**The wake tiers.** `small` fires each agent every 5 minutes on
integer-minute phase offsets, and assumes 1 core / ≤8 GB RAM or a shared
host. `medium` fires every 2 minutes with a sub-minute sleep stagger, and
assumes 2–4 cores / 8–16 GB RAM. `large` fires every minute with a
pipeline-ordered sub-minute stagger, and assumes 8+ cores / 16+ GB RAM. These
are cadences the scheduler is configured with, not measurements of how fast
work completes — for that, see the measured pace block in 1.8. To change the
tier after installation, re-run both installers:

<!-- doc-lint: skip — requires a live $KANBAN_ROOT install; rewrites the operator's real crontab and pseudocron config -->
```bash
$KANBAN_ROOT/scripts/install-crontab.sh --wake-tier <tier>
$KANBAN_ROOT/scripts/install-pseudocron.sh --wake-tier <tier>
```

**When the crontab step is skipped anyway.** Besides `--no-system-cron`, the
crontab hook stands down — loudly, naming the reason and the manual command to
run later — under `--dry-run`, when the install target is under a temp or test
directory, when no `crontab` binary is on PATH, and when `install-crontab.sh`
is missing from the installed tree. A crontab failure never fails the install:
the kanban is fully installed, only the schedule is missing, and the recovery
command is printed.

#### Deprecated flags

Both are still accepted and both emit a deprecation notice on stderr before
doing the right thing:

| Deprecated | Maps to | Notice it prints |
|---|---|---|
| `--no-crontab` | `--no-system-cron` | `[install] DEPRECATED: --no-crontab; use --no-system-cron instead.` |
| `--crontab-tier=none` | `--no-system-cron` | `[install] DEPRECATED: --crontab-tier=none; use --no-system-cron instead.` |
| `--crontab-tier=<tier>` | `--wake-tier <tier>` | `[install] DEPRECATED: --crontab-tier=<tier>; use --wake-tier <tier> instead.` |

#### install.sh refuses an existing install

Point `install.sh` at a directory that already holds `kanban.cfg`, `VERSION`,
or `scripts/` and it refuses, names the directory, and sends you to
`upgrade.sh`. That refusal is the guard protecting operator state — your
config, your projects, your bugs. `--force` proceeds past it, which is only
what you want for a deliberate clean re-lay of a version. (`install.sh --help`
is self-contradictory on this point today: its usage banner says `--force`
bypasses the guard, and a note further down its options list says it does not.
The behaviour above is the one the script actually implements.)

**Targeting a specific version.** `install.sh` takes an optional positional
version argument to lay down a specific released tag's tree, instead of
whatever this checkout currently has:

<!-- doc-lint: skip — requires a git checkout of the repo carrying the target tag; cannot run verbatim in harness -->
```bash
./install.sh v2.1.0        # lay down tag v2.1.0's tree
./install.sh ai_v2.1.0     # same tag; the internal "ai_" prefix is normalized
```

The tag must exist in this clone's git history. If it does not, the install
fails loud, names the version you requested, and lists the nearest available
tags — nothing is laid down. The resolved tag's tree is materialized into a
temporary detached worktree and the entire install comes from there, so this
repo's checked-out branch is never touched and the temporary worktree is
removed before the script exits (success or failure).

A fresh install is **directionless**: any existing tag may be targeted, even
one older than other tags that also exist. This is the sanctioned way to move
an installation *backwards* in version — `upgrade.sh` is forward-only and
refuses it, so a fresh install is the path (see the upgrade note in section 10).
Omit the version argument to install from the current checkout; that is the
default.

### 1.2 The four-concerns law — where a value goes

Four operator-owned files sit at the kanban root, and each owns exactly one
concern. Decide which concern your value belongs to first; the file follows
from the concern.

| File | Owns | Format | Detail |
|---|---|---|---|
| `kanban.cfg` | **Kanban behaviour** — dashboard layout, chain tuning, active provider, paths, wake bounds, per-agent model overrides. | INI, parsed | [1.3](#13-kanbancfg--framework-settings) |
| `shell-env` | **The shell environment the kanban needs to run** — `PGAI_AGENT_KANBAN_ROOT_PATH`, PATH additions, Python virtualenv activation. | Bash, sourced | [1.4](#14-shell-env--wake-environment) |
| `secrets` | **Credentials, and nothing else** — OAuth tokens, API keys. Mode `600`, never committed. | Bash, sourced | [1.5](#15-secrets--credentials) |
| `claude-env` | **Tuning the provider CLI binary itself reads** — runtime knobs that belong to the installed `claude` binary, not to the kanban. | Bash, sourced | [1.6](#16-claude-env--provider-cli-tuning) |

**The boundary is decidable by asking who reads the value.** The kanban reads
`kanban.cfg`. The shell the kanban runs in reads `shell-env`. An
authentication path reads `secrets`. The provider CLI binary reads
`claude-env`. If two answers seem to fit, you probably have two values.

**Why this is a law and not a table.** A rule without its reason is the first
thing a future editor relaxes, so the reason travels with the boundary. Each
of the four misplacements costs something specific:

- **A provider-tuning value in `secrets`** parks a knob behind the one file
  operators are told to `chmod 600`, never commit, and never paste into a bug
  report. It cannot then be reviewed, diffed, or shared with whoever is
  debugging your wake — and the next operator, reading a credentials file,
  has no reason to look for it there.
- **A credential in `claude-env`** loses every protection `secrets` carries:
  no `600`, no never-commit rule, and a file that install and upgrade both
  seed from a shipped template. The kanban's own sourcing path will not
  disclose it — nothing in that path ever prints or logs a *value* set by
  `claude-env`, only the file's path — but that guarantee covers the sourcing
  path, not your backups, your screenshots, or a walkthrough you paste into
  an issue.
- **Kanban behaviour in `shell-env` or `claude-env`** leaves the config system
  entirely. `kanban.cfg` is parsed and never executed, resolved by one loader
  with a documented precedence (environment variable > config file > built-in
  default) that fails loud on a missing required key. The same setting written
  as a bash `export` is executable code with no precedence, no validation, and
  no failure when it is wrong.
- **CLI tuning in `kanban.cfg`** never reaches the binary at all. `kanban.cfg`
  is data the kanban reads; a key there reaches the provider only where
  framework code explicitly forwards it, as `[models.<provider>]` does. A knob
  the CLI reads from its own environment has no such forwarding — it will look
  configured and do nothing.

Those four files are the whole tuning surface at the root.
`projects.cfg` ([1.7](#17-projectscfg--the-project-registry)) and each
project's `project.cfg` ([2.1](#21-projectcfg--per-project-settings)) are
registry and per-project identity, not tunables.

Sections 1.3–1.6 walk each file in turn. **This subsection is the canonical
statement of the law**; `docs/OPERATIONS.md` and `docs/public-contract.md`
point here rather than restating it, so there is one place to correct when
the boundary moves.

### 1.3 kanban.cfg — framework settings

INI format, one file at `$KANBAN_ROOT/kanban.cfg`. Initialized from
`kanban.cfg_example` on install; upgrades never overwrite it. Every key the
wake chain accepts, with its status and its default, is listed in
[1.9](#19-the-configuration-key-reference). The handful you will actually
touch on a new install, shown at their shipped defaults:

```ini
[paths]
# Where ALL framework transient output goes. Set this once; everything —
# worktrees, sandboxes, TMPDIR for standard tooling, the wake stop file —
# derives from it. Point it off /tmp when /tmp is small, noexec, or swept.
tmp_root = /tmp
tmp_subdir = pgai_kanban_tmp

# Optional. Install-time convenience / fallback. Per-project dev trees live
# in each project.cfg; this may be empty on installs that do not manage the
# framework's own source.
dev_tree_path =

[providers]
# Which LLM lane executes work. Switch it by hand or with switch-provider.sh.
active = claude
# Which provider wrappers install.sh deploys role files for.
available = claude codex
# oauth (Claude Code credentials) or apikey (ANTHROPIC_API_KEY in secrets).
ai_auth_mode = oauth

[dashboard]
# Global fallback for items shown per queue column.
max_rows = 20
```

Configuration precedence everywhere: **environment variable > config file >
built-in default**. One loader (`team/scripts/lib/config_loader.sh`) reads and
validates the file, and its Key Registry is the single source of truth for
what the chain accepts — see 1.9.

### 1.4 shell-env — wake environment

<!-- doc-lint: skip — requires a live $KANBAN_ROOT install and an interactive editor; cannot run verbatim in harness -->
```bash
cp $KANBAN_ROOT/shell-env_example $KANBAN_ROOT/shell-env
$EDITOR $KANBAN_ROOT/shell-env
```

Sourced by every wake before any work: PATH additions, virtualenv activation,
and the export of `PGAI_AGENT_KANBAN_ROOT_PATH` itself — this file is the
canonical place to set it, because a cron or pseudocron firing inherits no
interactive shell. If a tool works in your interactive shell but an agent
can't find it, the fix belongs here. Check your edits with
`bash -n $KANBAN_ROOT/shell-env` before the next wake picks them up.

### 1.5 secrets — credentials

<!-- doc-lint: skip — requires a live $KANBAN_ROOT install and an interactive editor; cannot run verbatim in harness -->
```bash
cp $KANBAN_ROOT/secrets_example $KANBAN_ROOT/secrets
chmod 600 $KANBAN_ROOT/secrets
$EDITOR $KANBAN_ROOT/secrets
```

Branches on `AI_AUTH_MODE` (exported from kanban.cfg before sourcing):
`oauth` mode unsets `ANTHROPIC_API_KEY` so Claude Code OAuth credentials
win; `apikey` mode exports the key. Never commit this file; 600 perms.

### 1.6 claude-env — provider CLI tuning

<!-- doc-lint: skip — requires a live $KANBAN_ROOT install and an interactive editor; cannot run verbatim in harness -->
```bash
$EDITOR $KANBAN_ROOT/claude-env
```

`claude-env` is the provider-runtime file: it carries tuning that the
installed provider CLI binary reads from its own environment, and nothing
else. Whether a given value belongs here rather than in `kanban.cfg`,
`shell-env`, or `secrets` is decided by the four-concerns law in
[1.2](#12-the-four-concerns-law--where-a-value-goes) — that subsection is the
canonical statement and this one does not repeat it. What follows is what an
operator does with the file once the law has told them the value belongs here.

No copy step: unlike `shell-env` and `secrets`, this one is already there.
`install.sh` seeds `claude-env` from `claude-env_example` on a fresh install,
and `upgrade.sh` creates it during an upgrade when the install predates the
file. Every line ships commented out, so a seeded-and-untouched `claude-env`
changes nothing.

**Sourcing order: `shell-env` first, `claude-env` second, on every entry
path.** The shared prelude (`team/scripts/lib/env_bootstrap.sh`) sources
`shell-env`, then `claude-env`, for every entry-point script; the wake path
sources both again directly, so a cron or pseudocron firing — which has no
interactive shell to inherit tuning from — ends up with the same environment
as a command you type by hand. That is the whole reason the file exists: a
value tuning the CLI binary has nowhere else to live that a headless wake
firing can reach. The order matters twice. `shell-env` is what establishes
the root and the virtualenv `claude-env` resolves against, and a name set in
**both** files resolves to whatever `claude-env` sets, because it sources
later.

**Absent is a clean no-op; broken is loud.** No `claude-env` at all means no
error, no warning, no output, and no change to any script's exit status. A
`claude-env` that does exist is syntax-checked with `bash -n` *before* it is
sourced; one that fails the check is not sourced at all, and the entry point
refuses with a stable machine key, a message naming the file's path, and the
`bash -n` command to run. The check is deliberate — `source` executes a
script's earlier statements as it parses along, so sourcing a broken file
would half-configure the environment of a wake firing with no human present
to notice. Check your edits yourself with
`bash -n $KANBAN_ROOT/claude-env`.

**Your tuning survives upgrades.** `claude-env` is in the upgrader's
protected set: once the file exists, neither install nor upgrade overwrites
it. `claude-env_example` beside it *is* refreshed on every upgrade, so the
template always shows the current shipped guidance while your edits stay
exactly as you left them — diff the two after an upgrade to see what moved.

**The template records its calibration.** `claude-env_example`'s header names
the Claude CLI version its entries were verified against. A knob that was
correct on one CLI version is not guaranteed to still be correct, or still
exist, on a later one — re-audit after a CLI upgrade.

**Never put credentials here.** That is `secrets` (1.5), for the reasons in
the four-concerns law (1.2).

#### The background-wait ceiling

The first shipped entry is `BASH_MAX_TIMEOUT_MS`, which caps how long the
installed Claude CLI will wait on a Bash tool invocation — including a
backgrounded one — before giving up on its own. The CLI's own default is
600000 ms (10 minutes), an order of magnitude below the kanban's default
configured per-task limit of 5400 s (1.8), so an untuned CLI abandons work the
kanban's own watchdog is still happily waiting on.

Precedence, in the order it resolves:

1. **A value you set in `claude-env` wins.** `claude-env` is sourced before
   the wake path reaches its derivation, so your value is already in the
   environment, and the derivation fires only when the variable is unset or
   empty. When yours is the value in play, the wake log records
   `background-wait ceiling: operator override in force`.
2. **Otherwise the wake path derives one on every firing**, as
   `(max_task_seconds + kill_grace_seconds) × 1000` — the kanban's own
   per-task budget plus its kill grace, in the milliseconds
   `BASH_MAX_TIMEOUT_MS` takes. On the defaults that is
   `(5400 + 30) × 1000 = 5430000` ms. Raise `max_task_seconds` in
   `kanban.cfg` (1.8) and the ceiling tracks it on the next firing with no
   second edit here — which is exactly why it is derived rather than pinned.
   The wake log names the derived value and both inputs it came from.
3. **`max_task_seconds = 0` derives nothing.** A disabled per-task watchdog
   asked for no ceiling, so none is exported and the CLI's own default
   stands. The wake log says so rather than staying silent.

Uncomment and set a value here only when the derived ceiling is wrong for
your environment — a provider or network known to need more headroom than
your task budget implies.

### 1.7 projects.cfg — the project registry

INI registry of which projects this install manages, in iteration order:

```ini
[project:my-app]
priority = 1
description = What this project is
enabled = true
dashboard_color = #D85A30
dashboard_max_rows = 20
```

`priority` sets iteration order (1 runs first each wake). `description` is
free text for the operator's own benefit. `enabled` defaults to `true`;
setting it `false` pauses the project without a HALT file: every agent's
wakes, PM discovery and OVERWATCH sweeps skip it, each narrating one
"disabled in projects.cfg (enabled=false) — skipped" line per firing, while
`why` reports it IDLE and the dashboards and `GET /projects` still list it.
Only `false` disables, in any case and with surrounding spaces ignored; any
other value leaves the project enabled, and a value other than `true` or
`false` (such as `no`) also prints a warning. `dashboard_color`
is the per-project colour (`#RRGGBB` only) used on every dashboard surface and
defaults to a deterministic palette entry. `dashboard_max_rows` is the
per-project queue-column row cap: valid range 5–100, values outside it clamped
with a warning, default 20 or the `DASHBOARD_MAX_ROWS` environment variable
when that is set. `priority` is the one required field — a project without one
is treated as lowest priority; the other four are optional.

Managed by the project scripts (section 2) — hand-editing is possible but
the scripts keep state consistent. `projects.cfg_example` is the annotated
template.

### 1.8 Runtime bounds

The wake layer caps how long work runs at three nested levels —
**task ≤ project ≤ batch** — so a single stuck agent cannot consume an
entire wake batch or starve the projects queued behind it. All three are
`[wake]` keys in `kanban.cfg`, each with a safe default:

| Key | Default | Bounds |
|---|---|---|
| `max_task_seconds` | `5400` (90 min) | A single agent run. `0` disables. |
| `max_project_seconds` | `14400` | One project's task loop; each project gets its own clock. `0` disables. |
| `max_runtime_seconds` | `14400` | The whole multi-project batch (the outermost bound). |

`max_task_seconds` is a hard wall-clock timeout on the provider invocation.
When an agent exceeds it the wake sends SIGTERM, waits `kill_grace_seconds`
(default 30), then SIGKILL. `max_project_seconds` resets per project, so a
long or stalled project 1 cannot starve projects 2..N — each gets a fresh
clock. `max_runtime_seconds` caps the entire iteration. The wake log names
which bound tripped — `(task)`, `(project)`, or `(batch)` — and warns
(non-fatally) at startup if the configured values violate the
task ≤ project ≤ batch ordering. Each key honors environment-variable
override (`MAX_TASK_SECONDS=<N> wake-batch.sh ...`, and the parallel forms).

**BLOCKED-by-timeout vs. a crash.** When `max_task_seconds` fires, the
existing agent-exit path marks the task **BLOCKED** with the reason
`exceeded max_task_seconds (<N>s)`, tears down the worktree, and releases the
lock — the same teardown an ordinary exit takes. The reason text is what
distinguishes a timed-out task from one that crashed: a crash carries its own
failure reason, while a timeout always reads `exceeded max_task_seconds`. So
a BLOCKED task whose reason names the timeout *ran too long* (its work may
simply have been too large for one session, or it stalled); a BLOCKED task
with any other reason *died*. Either way you clear it the same way —
`scripts/reset.sh --project <name> --key <key>` (section 9.1)
re-queues it. The dashboard attention surface (window 3) also flags a WORKING
task whose age already exceeds `max_task_seconds` *before* the kill lands, so
an over-run is visible early.

**Measured pace, not a ceiling.** The bounds above are configured limits —
how long the wake layer will *wait* before it acts. What a task or an RC
actually takes is a separate, measured question, answered from the
project's own history rather than from the configured ceiling:

<!-- MEASURED-PACE-GENERATED:BEGIN -->
<!-- generated by team/scripts/generate_measured_pace_docs.py from
     projects/<project>/metrics/rc/v*.json. Do not hand-edit
     between these markers; run 'python3 team/scripts/
     generate_measured_pace_docs.py' (from the repo root) and
     commit the result instead. -->

**Per-task wall time (measured):** 5.4–142.7 min, median 17.3 min. Sample: 112 RC(s), 864 task(s), window = every rollup available at render time (v1.27.1 .. v2.21.2). This is an RC's own wall-clock duration spread evenly across its tasks (clock-to-clock, including wake-cadence idle time), not an isolated per-task stopwatch reading -- see this generator's module docstring for why.

**Composed RC cost (measured):** ~162 min = 1.1×pm bookend + 3.7×coder + 1.4×writer + 1.1×tester + 1.1×cm bookend, each term the average tasks per RC for that role times the median per-task minutes above.

<!-- MEASURED-PACE-GENERATED:END -->

### 1.9 The configuration key reference

Every key below is one the wake chain's configuration loader
(`team/scripts/lib/config_loader.sh`) accepts, carrying the default its own
Key Registry declares. The registry is the single source of truth: not the
templates, not this table, not memory. Where `kanban.cfg_example`'s inline
comment disagrees with the registry, the registry is what the loader applies
and the template is what needs correcting.

Three things are true of the whole set and are therefore not repeated per row:

- **Every key is OPTIONAL.** The registry declares no REQUIRED key at all,
  which is why there is no status column below. An absent key takes its
  default; nothing here can fail a wake by being missing.
- **Precedence is environment variable > config file > default**, per key.
- **These are `kanban.cfg` keys**, with one shared exception: the `[release]`
  keys are read through the same registry from a project's own `project.cfg`
  as well, where they override the kanban-wide value for that project.

#### `[paths]`

| Key | Default | What it controls |
|---|---|---|
| `dev_tree_path` |  | Install-wide fallback dev tree. Per-project dev trees live in each `project.cfg`; empty is normal and is not an error. |
| `tmp_root` | `/tmp` | Base directory for all framework temp space. Everything transient derives from it. |
| `tmp_subdir` | `pgai_kanban_tmp` | The framework's subdirectory under `tmp_root`. Full temp path is the two joined. |
| `worktree_base` |  | Absolute parent directory for every task worktree. Empty means `<tmp_root>/<tmp_subdir>/worktrees`; the `PGAI_WORKTREE_BASE` environment variable overrides both. |
| `cleanup_retention_days` | `30` | Deprecated spelling of `[cleanup] retention_days`, still found in older `kanban.cfg` files. `cleanup.sh` falls back to it only when `[cleanup] retention_days` is absent or blank, and whenever the line is present it prints one warning naming the edit that retires it. Move the value to `[cleanup] retention_days` and delete this line. |

#### `[providers]`

| Key | Default | What it controls |
|---|---|---|
| `active` | `claude` | Which provider's wake script does work each firing. Non-active wake scripts fast-exit. This is the only provider selector read at runtime. |
| `available` | `claude codex` | Which providers `install.sh` deploys role files for. A provider whose CLI is not on PATH is skipped with a log line, never an error. |
| `ai_auth_mode` | `oauth` | `oauth` uses Claude Code credentials; `apikey` expects a key in `secrets`. |
| `llm_effort` |  | Global reasoning-effort level passed to the provider CLI. Absent means no effort flag is passed and the provider's own default applies. |

An older `kanban.cfg` may still carry an `initial = claude` line in this
section, written there by an earlier template. It is a retired key: nothing
reads it and its value is ignored. Every configuration load prints one NOTE
saying the line is safe to delete, with a `sed` command that removes exactly
that line. Delete it; do not rename it to `active`, which the section already
carries.

#### `[chain]`

| Key | Default | What it controls |
|---|---|---|
| `pm_mode` | `automatic` | `automatic` lets PM self-ticket from requirements docs when its backlog is empty; `manual` disables the autonomous scan. |

#### `[dashboard]`

| Key | Default | What it controls |
|---|---|---|
| `rows_per_column` | `21` | Nominal rows rendered per dashboard column. |
| `min_rows_per_column` | `13` | Floor for the sparse-column layout. |
| `max_rows_per_column` | `34` | Ceiling for the full-column layout. |
| `min_rows_per_project` | `3` | Floor for a single project's allocation within a column. |
| `max_rows_per_project` | `8` | Ceiling for a single project's allocation within a column. |
| `max_rows` | `20` | Global fallback for items shown per queue column. The per-project override is `dashboard_max_rows` in `projects.cfg`. |
| `dashboard_status_glyph` | `■` | Marker character for status tags. Must be exactly one visible character; a multi-character value is rejected non-zero, naming the key. |
| `session_name` | `pgai-kanban-dashboard` | tmux session name the dashboard is created under and killed by. |
| `refresh_seconds` | `5` | Seconds between dashboard pane refreshes. |
| `color_done` | `green` | tmux colour for DONE status text in the queue and progress panes. |
| `color_working` | `yellow` | tmux colour for WORKING status text. |
| `color_blocked` | `red` | tmux colour for BLOCKED status text. |
| `color_waiting` | `yellow` | tmux colour for WAITING status text. |
| `color_label` | `cyan` | tmux colour for pane labels and headings. |
| `color_dim` | `white` | tmux colour for de-emphasised text. |
| `color_header` | `cyan` | tmux colour for the multi-project overview header. |
| `color_halt` | `red` | tmux colour for a HALT indicator in the overview. |
| `color_ok` | `green` | tmux colour for a healthy indicator in the overview. |

#### `[wake]`

The three nested runtime bounds — `max_task_seconds`, `max_project_seconds`,
`max_runtime_seconds` — are explained in [1.8](#18-runtime-bounds). All are
configured limits, not measurements.

| Key | Default | What it controls |
|---|---|---|
| `max_tasks_per_wake` | `5` | Tasks the wake loop processes in one firing before exiting. |
| `max_runtime_seconds` | `14400` | Wall-clock cap on the whole multi-project batch. |
| `max_project_seconds` | `14400` | Wall-clock cap per project; each project gets a fresh clock. `0` disables. |
| `max_task_seconds` | `5400` | Hard wall-clock cap on one agent run. `0` disables. |
| `kill_grace_seconds` | `30` | Seconds between SIGTERM and SIGKILL when the per-task cap fires. |
| `pause_between_tasks` | `5` | Sleep between consecutive tasks in one firing, so a SIGTERM lands between tasks rather than mid-task. |
| `stop_on_blocked` | `true` | Halt the wake loop when a task finishes BLOCKED. |
| `stop_file` | `${PGAI_AGENT_KANBAN_TEMP_DIR}/wakeup/stop` | Touch this path to stop every running wake batch (all agents, all projects) at its next task boundary; the in-flight task finishes. Persists until you remove it; no batch dispatches while it exists. |
| `pm_max_tasks` | `15` | Ceiling on tasks PM generates from one requirements doc. |
| `transient_retry_cap` | `3` | Ceiling on the durable transient-retry record before a task is blocked needing a human. |
| `zero_progress_cap` | `3` | Ceiling on the durable zero-progress record, a separate counter from `transient_retry_cap`: a `WORKING` task with no forward motion resets to `BACKLOG` below it and goes `BLOCKED` needing a human at it. |

#### `[cleanup]`

| Key | Default | What it controls |
|---|---|---|
| `retention_days` | `30` | Days archived task folders and logs are kept. `0` disables the retention sweep. |
| `trivial_log_bytes` | `1700` | Byte threshold below which a wake log counts as trivial (an agent-had-nothing-to-do log). |
| `trivial_log_hours` | `6` | Age below which a trivial log is preserved anyway. |

#### `[release]`

The deferral-posture keys. These are the one part of the registry a project's
own `project.cfg` may override for itself; both files are read through the
same validation path. Booleans are spelled `true`/`false` only — `on`/`off`
refuse loud rather than resolving into a posture nobody asked for.
`deep_verify` is the single exception: it also accepts `true`/`false` as
aliases for `weekly`/`off`.

| Key | Default | What it controls |
|---|---|---|
| `deferred_bugs` | `false` | Master switch. `false` means every bug opens its own RC. |
| `deferred_bug_level` | `low` | The highest severity that waits. A bug above it still triggers an RC. Accepts `low`, `medium`, `high`; `critical` is not settable, so at `high` a critical bug still triggers. |
| `defer_piggyback` | `true` | Whether deferred bugs ride along when a bug above the level triggers an RC. |
| `deep_verify` | `weekly` | The scheduled cadence for the DEEP-VERIFY tier. `off` turns the scheduled trigger off: set here, for every project that does not set its own value; set in a project's `project.cfg`, for that project. |
| `deep_verify_offhours_window` | `03:00-03:59` | The daily UTC window (`HH:MM-HH:MM`) in which a scheduled DEEP-VERIFY run may fire. Ships commented out. A malformed value is skipped with one warning, never refused. |
| `overwatch_resolve` | `false` | Whether OVERWATCH may resolve a halted release itself instead of only observing it. Ships commented out everywhere; absent means `false`, and `false` leaves OVERWATCH a pure observer. See Section 7. |

**The two DEEP-VERIFY keys resolve per project, highest first:** the project's
own `project.cfg` `[release]` value, then this file's `[release]` value, then
the default — `weekly` for `deep_verify`, `03:00-03:59` for
`deep_verify_offhours_window`. A blank or absent value at one tier passes to
the next, and so does a malformed window, with a warning naming the file. An
unrecognized `deep_verify` value in `kanban.cfg` is refused when the
configuration loads, naming the file and the key. Both keys govern only the
scheduled trigger; [docs/operator-commands.md](docs/operator-commands.md)
("The scheduled trigger's per-project switch") has the rest.

**`max_rc_retries`, the RC retry ceiling,** is declared in the registry's
`[release]` block with a default of `3`, but the loader does not read it.
`pp_max_rc_retries` (`team/scripts/lib/project_paths.sh`) reads the project's
own `project.cfg` `[release]` value, then this file's, and refuses a value
that is not a non-negative whole number. It applies no default: when neither
file sets the key it refuses, naming both files. `kanban.cfg_example` ships
it set to `3`.

#### The two per-provider model sections

One section per provider, the same seven keys in each. The active provider's
section is the one that applies. Each section ships with its `default` set —
`claude-sonnet-5-5` for Claude, `gpt-6.1-sol` for Codex — and every per-role
key empty, so every role runs on the section default until you choose
otherwise. A model is resolved per role, highest first:

1. a `## Model Override` field in the task README;
2. the role's own key (`pm`, `coder`, `writer`, `tester`, `cm`, `po`);
3. the section's `default`;
4. the provider CLI's own default — only when the role key and `default` are
   both empty, and with a warning in the wake log.

A `PGAI_<ROLE>_MODEL` already exported in the environment takes the place of
tiers 2 and 3, as the environment beats the file for every key here. Every
per-role key defaults to empty. The `default` key's Default below is the value
`kanban.cfg_example` ships, which the loader's Key Registry records as a
shipped value: an absent or empty `default` is not filled in by the loader and
falls through to tier 4. The shipped values reach only a fresh install's
`kanban.cfg`, because an upgrade never rewrites an existing one. To give one role a
different model, set its key — for example `coder = claude-opus-5-5` for
Claude or `tester = gpt-6.1-sol` for Codex. Deeper reasoning is usually a
higher `[providers] llm_effort` rather than a different model. Model names are
provider-specific and change over time, so check the names your CLI currently
lists before you set or keep one.

#### `[models.claude]`

| Key | Default | What it controls |
|---|---|---|
| `default` | `claude-sonnet-5-5` | Fallback model for any role below left unset. Shipped value; absent or empty, the provider CLI picks its own model. |
| `pm` |  | Model for PM tasks. |
| `coder` |  | Model for CODER tasks. |
| `writer` |  | Model for WRITER tasks. |
| `tester` |  | Model for TESTER tasks. |
| `cm` |  | Model for CM tasks. |
| `po` |  | Model for PO tasks. |

#### `[models.codex]`

| Key | Default | What it controls |
|---|---|---|
| `default` | `gpt-6.1-sol` | Fallback model for any role below left unset. Shipped value; absent or empty, the provider CLI picks its own model. |
| `pm` |  | Model for PM tasks. |
| `coder` |  | Model for CODER tasks. |
| `writer` |  | Model for WRITER tasks. |
| `tester` |  | Model for TESTER tasks. |
| `cm` |  | Model for CM tasks. |
| `po` |  | Model for PO tasks. |

#### Keys in kanban.cfg read by their owning component

The Key Registry declares every key `kanban.cfg` carries, with its default,
but the tables above are not the whole of the file. The keys below are read
directly by the one component that owns them, at the moment that component
runs, rather than by the loader on every wake. They are real, they work, and
this reference deliberately does not restate their defaults — the registry
entry and the owning surface named beside each one are where they are
documented.

- `[dashboard]` `snapshot_ttl_seconds` — the per-tick render snapshot cache's
  staleness bound. Owned by `kanban.cfg_example`'s own `[dashboard]` block.
- `[providers]` `llm_thinking_enabled` — the provider's extended-thinking switch.
  Owned by `kanban.cfg_example`'s `[providers]` block.
- `[docker]` `docker_exe`, `container_name`, `image` and
  `container_stop_timeout_seconds` — which container engine the container
  scripts invoke, and the name, image and soft-stop wait of the detached kanban
  container. Owned by [docs/docker.md](docs/docker.md).
- `[paths]` `worktree_base` — where task worktrees are created. Owned by
  `kanban.cfg_example`'s `[paths]` block; ships commented out.
- `[governance]` `budget_bytes_*` (one per role) — per-role governance byte
  budgets. Owned by `kanban.cfg_example`'s `[governance]` block.
- `[api]` `host` and `port` — the operator REST API's loopback bind address and
  port. Owned by [docs/operator-api.md](docs/operator-api.md).

The per-project keys in each project's own `project.cfg` — the `[project]`,
`[versioning]`, `[hooks]` and `[debug]` sections — are not in this registry,
because a different reader consumes them. They are
[2.1](#21-projectcfg--per-project-settings)'s, and `project.cfg_example` is
their annotated template.

---

## 2. Project Management

Each project lives at `$KANBAN_ROOT/projects/<name>/` with its own config,
queues, intake directories, and release state. Projects are fully
independent: own cadence, own workflow type, own halt flags.

### 2.1 project.cfg — per-project settings

One INI file per project at `$KANBAN_ROOT/projects/<name>/project.cfg`, written
for you by the project scripts in 2.2 and 2.3. Its keys are read by the
per-project path resolver, not by the wake chain's configuration loader, so
they are deliberately absent from the key reference in
[1.9](#19-the-configuration-key-reference). `project.cfg_example` at the
kanban root is their annotated template, and the one exception runs the other
way: the `[release]` keys in 1.9 may also be set here, where they override
the kanban-wide value for this project alone.

Identity and git lane, the project section:

| Key | Default | What it sets |
|---|---|---|
| project_name | *(the project's name)* | The project's own name; matches its directory under `projects/`. |
| workflow_type | release | Any registered ready workflow plugin type — `release` and `document` ship. |
| dev_tree_path | *(empty)* | Local git dev tree. May be empty for a document-workflow project with no repo. |
| git_repo_url | *(empty)* | The remote the project's releases go to. |
| git_remote_name | origin | The git remote this project's releases talk to: CM's open, ship, cancel and unwind steps, `init-project-git-repo.sh` and the push-lag checks all use it. Blank or absent means `origin`. Letters, digits, `.`, `_` and `-` only, starting with a letter or digit; anything else is refused, naming the key. The manual `cm/finalize-release.sh` reads it too, but lets the `GIT_REMOTE_NAME` environment variable override it for one run. |
| branch_prefix | ai_ | The git lane. See below. |
| push_to_remote | true | `false` keeps releases local in `dev_tree_path` and leaves pushing to you. |
| temp_dir | `projects/<project_name>` | This project's subdirectory under the framework temp root. Absent or empty, it is `projects/<project_name>`, so one project's cleanup cannot reach another's. Ships commented out. |

Version ceilings, the versioning section:

| Key | Default | What it sets |
|---|---|---|
| max_patch | 21 | Ceiling on the patch component of an eligible target version. |
| max_minor | 13 | Ceiling on the minor component. |
| max_major | 0 | Ceiling on the major component. `0` keeps major bumps operator-only. |
| max_priorities_per_run | 1 | Priority items bundled per discovery run. Ships commented out. |

**Ceiling semantics matter:** these compare version *components*, and they are
caps on an eligible target version — not increment counters. A document whose
target version exceeds a ceiling is not picked up automatically; raising the
ceiling is how you approve it. A mature project at v3.59.x with
`max_minor = 13` is gated on everything, so when registering an existing
project, set the ceilings above where the project already lives.

`branch_prefix` decides the git lane: empty uses `main`/`rc/vX.Y.Z`
directly; a prefix like `ai_` gives the kanban an isolated lane
(`ai_main`, `ai_rc/ai_vX.Y.Z`, prefixed tags) so a repo's
human branches stay untouched. `ai_` is the shipped default — the
hybrid-shop assumption that a human lane already exists in the repo. Clear it
for a pure-AI install. The kanban ships single-lane: each RC branches from the
(prefixed) main branch and returns to it in one squash — there is no develop
hop.

Two more sections live in the same file and are documented in
`project.cfg_example` rather than restated here: a hooks section carrying the
three release-lifecycle hook paths and their `_required` flags (see 3.1), and
a debug section carrying the per-project verbose-logging switches.

### 2.2 create-project — new project from scratch

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/create-project.sh --project my-app \
  --workflow-type release \
  --dev-tree ~/develop/<project> \
  --git-repo git@github.com:<org>/<repo>.git
```

Creates the project directory skeleton, project.cfg, empty queues, and the
projects.cfg entry. The project.cfg is rendered from `project.cfg_example`, so
it carries every per-project key with a comment giving its accepted values
and default; a key left to its default or to `kanban.cfg` appears as a
commented `# key = value` line showing the value in force. Defaults: release
workflow, `git_remote_name = origin`,
`branch_prefix = ai_`, ceilings 21/13/0 (a fresh project demonstrates the
chain immediately; majors stay gated). `--max-patch`, `--max-minor` and
`--max-major` override the ceilings; `--workflow-type`, `--git-remote`,
`--priority` and `--color` override their fields. There is no branch-prefix
flag — edit `project.cfg` afterwards to clear or change the lane.

**Next, fill in the project README.** `create-project.sh` also writes
`projects/<name>/README.md`, copied from `templates/project/README-TEMPLATE.md`
with its fields still showing `<...>` placeholders. Every agent working on
this project reads that file on every wake, after its role file and before
the task — it is the project layer of the instruction stack
(`docs/OPERATIONS.md`, "Instruction layers"). Replace each placeholder with
this project's facts:

- **Test runners** — the targeted-test command a CODER or WRITER runs on
  only the files it touched, and the gated runner whose exit code is
  TESTER's verdict.
- **Lints and checks** — what runs before a merge, and whether each one
  blocks or only reports.
- **Coding standard** — the file agents follow, or "match the existing code".
- **Contracts** — API or interface versions, generated files, schema rules,
  and who updates what.
- **Git and push settings** — dev tree, branch prefix, remote and
  `push_to_remote`, workflow type. Scripts read these from `project.cfg`;
  the README states them for the agents, so keep the two in step.

Keep it to this project's facts. Generic rules — anything true of every
project — never go there: they belong to `SOP-core.md` and the role files,
which the project README narrows but never overrides. Edit it by hand
whenever the project changes; the next wake reads the new version, and
upgrades never touch it.

For a release project the git repo needs the base `main` branch (or its
prefixed equivalent) on origin — `scripts/init-project-git-repo.sh`
sets up the base branch; CM never creates base branches itself. The
single-lane flow branches every RC from that base and squashes back
into it, so only one base branch is required.

To have the kanban manage its own source, register it like anything else —
nothing in the framework treats it specially:

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/create-project.sh --project pgai-agent-kanban \
  --dev-tree ~/develop/pgai-agent-kanban --git-repo <repo-url>
```

### 2.3 add-project — register an existing project directory

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/add-project.sh --project my-app
```

Registers a `projects/<name>/` directory that already exists (e.g. restored
from backup) into projects.cfg without recreating its contents.

Inside the project directory it writes at most two files, each only when it
is missing, and never touches one that exists. If the directory has no
`project.cfg` (nor a legacy `PROJECT.cfg`), add-project writes one from
`project.cfg_example` with the template's defaults and says so in one line;
set its `dev_tree_path` and `git_repo_url` before the first release. If it
has no `projects/<name>/README.md`, add-project seeds one from
`templates/project/README-TEMPLATE.md` and tells you to fill it in. Either
way, check the README before the first
wake: fill in or update the test runners and targeted-test command, lints
and checks, coding standard, contracts, and git and push settings, exactly
as in the create-project step in 2.2, and keep generic rules out of it.

### 2.4 remove-project

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/remove-project.sh --project my-app             # unregister only (safe default)
scripts/remove-project.sh --project my-app --force     # also rm -rf the directory
```

The safe default unregisters the project from `projects.cfg` and leaves the
`projects/<name>/` directory in place. Add `--force` to also delete the
project directory. The project's git repo and dev tree are never touched
either way. Export first if you may want it back. A project with an RC in
flight (`## Active RC` other than `none`) is refused in both modes; ship or
cancel the RC first.

### 2.5 export-project / import-project

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/export-project.sh --project my-app                       # → tarball in cwd
scripts/import-project.sh --archive my-app-export-<ts>.tar.gz \
                          --register                              # → restores + registers
```

The export writes `<name>-export-<UTCstamp>.tar.gz` in the current
directory by default (override with `--out <path>`) and carries
everything: project.cfg (ceilings included), queues, intake files,
release-state, logs. Import is the sanctioned restore path — an imported
project resumes exactly where it left off; `--register` adds it to
`projects.cfg` in the same step (without the flag, import extracts and
prints the registration line for you to add manually). This pair is
also the migration path between installs.

### 2.6 release-state.md

Per-project, CM-written. Tracks `Active RC` (the single-threading gate) and
`Last Released`. For git-backed projects, Last Released is *derived from
git tags* in the dev tree — the file is a mirror. For document-workflow
projects with no repo, the file is authoritative; on a fresh registration
of a project with prior document releases, hand-set `## Last Released` once.

---

## 3. Workflow Types

A workflow type defines the agent pipeline for a deliverable. Set per
project in project.cfg.

### 3.1 release — software with the git RC lifecycle

Requirements doc → PM decomposes → CM opens `rc/vX.Y.Z` from the
(prefixed) main branch → CODER (and WRITER for release notes) work
per-task worktrees and merge back → TESTER verifies against the
requirements and files findings → CM applies policy, runs project hooks,
squashes the RC directly into the (prefixed) main branch (one squash, no
develop hop), runs the post-squash fidelity gate, tags, pushes, deletes
the RC. Output: a tag on origin and release notes.

Per-project hooks customize finalization — executable scripts the framework
discovers and runs. Three resolution tiers, highest precedence first:

```
(a) project.cfg [hooks] cm_release_<phase>_hook            # explicit path
(b) $KANBAN_ROOT/projects/<name>/hooks/cm-release-<phase>.sh  # kanban-side
(c) <dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh      # in-repo, portable
```

Tier (c) — the in-repo `.pgai/hooks/` location — is the **portable** tier:
it travels with the managed repo, survives `remove-project` +
`create-project` cycles, and is versioned and reviewed alongside application
code. Prefer it for hooks that should always be present regardless of the
kanban installation state (e.g., a version-bump wrapper).

```
<dev_tree_path>/.pgai/hooks/
├── cm-release-pre-squash.sh   # e.g. bump pyproject.toml to the target tag
├── cm-release-pre-tag.sh
└── cm-release-post-tag.sh     # best-effort; failure logged, not blocking
```

Every release prints one resolution line per phase (`<phase> hook: <path>
(source: cfg|kanban-side|in-repo)` or `<phase> hook: none configured`).
Setting `cm_release_<phase>_hook_required = true` in `project.cfg [hooks]`
makes a missing hook at any tier a HALT before the phase — fail-loud on
wiring loss. Full precedence, printing, required-flag semantics, and the
hook environment contract (`PGAI_TARGET_VERSION`, `PGAI_PROJECT_NAME`,
`PGAI_PROJECT_ROOT`, `PGAI_DEV_TREE_PATH`, `PGAI_RC_BRANCH`,
`PGAI_KANBAN_ROOT`, cwd = dev tree) are in `docs/OPERATIONS.md` under
"Release Lifecycle Hooks."

### 3.2 document — prose deliverables

No git repo required (`dev_tree_path` may be empty). WRITER drafts/
integrates/polishes; optional TESTER review verifies against the outline;
CM finalize publishes the deliverable to
`projects/<name>/artifacts/v<semver>-<name>.<ext>` — a versioned library
where every version is kept. Versioning drives off the requirement doc's
`## Target Version`.

---

## 4. The Agents

Six roles plus one dormant observer. Each is a separate subagent with a
role file under `$KANBAN_ROOT/roles/`. One agent of each type runs at a
time per project (per-project wake locks); the scheduler wakes each agent
type on a stagger, at whatever cadence the installed wake tier sets — every
5 minutes on the default `small` tier, every minute on `large` (1.1).

| Agent | Trigger | Does | Git contract |
|---|---|---|---|
| **PM** | wake | Decomposes requirements docs into tasks across the agent queues; runs the discovery pipeline when idle | read-only |
| **CODER** | wake | Implements features in a per-task git worktree off the RC branch; merges back `--no-ff` | local-only (never push/fetch) |
| **WRITER** | wake | Prose: release notes, document-workflow deliverables | local-only |
| **TESTER** | wake | Verifies the RC against requirements in a detached-HEAD worktree; files categorized findings to `bugs/` and a structured report; halts only when verification cannot run | read-only |
| **CM** | wake | Branch/merge/tag mechanics; applies ship/no-ship policy (default: ship); the only agent that touches origin | sole origin-toucher |
| **PO** | human-invoked (`scripts/po-agent.sh`) | Drafts requirements docs from briefs in collaboration with the operator. Optional today — most operators write requirements directly (section 5) | read-only |

The two policies that make autonomy work: **TESTER reports, CM decides**
(found bugs become intake for the next iteration, never a veto), and there
is **no REVIEW state** — a task ends DONE, BLOCKED (with a reason, needs
human), or WONT-DO.

---

## 5. Intake Documents

All work enters as files dropped into per-project intake directories. The
discovery pipeline (run by PM wakes when a project is idle) processes them
in strict order: **bugs first, then priorities, then requirements** — and
does exactly one thing per iteration. An Active RC or a HALT blocks intake
processing entirely (one RC at a time).

All intake files: `chmod 644`, and discovery only handles files whose body
contains `## Status: open` (bugs/priorities) or an unprocessed
requirements doc with a target version above Last Released.

**Dropping a file: one command.** `intake.sh` deposits a staged file into the
right directory, routed by its name, with mode 644 set for you. It replaces the
manual `cp` + `chmod`:

```bash
# Instead of:
#   cp /tmp/BUG-0400-widget-crash.md projects/<name>/bugs/ && chmod 644 projects/<name>/bugs/BUG-0400-widget-crash.md   (example file name)
scripts/intake.sh --project <name> /tmp/BUG-0400-widget-crash.md   # example file name
```

It routes by filename prefix — `BUG-*` → `bugs/`, `PRIORITY-*` → `priority/`,
`vX.Y.Z-*.md` → `requirements/` — copies the file (the source is left in
place), and sets 644. It refuses, copying nothing, if the name matches no
known prefix or if a file of that name already exists in the destination.

`intake.sh` is deliberately dumb: it routes, copies, and chmods only — it does
**NOT** validate the file's contents. A malformed file still lands here, then
the discovery pipeline rejects it to `.rejected/` exactly as before. See
[docs/operator-commands.md](docs/operator-commands.md) for the full command
reference.

### 5.1 Bugs

```
Location:  projects/<name>/bugs/
Filename:  BUG-NNNN-short-slug.md        (regex: ^BUG-[0-9]{4,}-.+\.md$)
```

Body: `## Status: open`, a summary, severity, the required fix, and —
critically — **acceptance criteria as testable commands** (`grep -q`,
`test -f`, geometry assertions). The intake file is where the human
encodes the design decision: file allowlists, "MUST NOT modify" guards,
and acceptance tripwires are how you steer the chain without sitting in
the loop. Before numbering a new bug, confirm the high-water mark:

```bash
ls projects/<name>/bugs/ | grep -oE 'BUG-[0-9]+' | sort -V | tail -1
```

Discovery bundles ALL open bugs into one requirements doc at the next
patch version. TESTER also files bugs autonomously from its findings.

### 5.2 Priorities

```
Location:  projects/<name>/priority/
Filename:  PRIORITY-NNNN-short-slug.md  (date segment optional: PRIORITY-NNNN-YYYYMMDD-slug.md also accepted)
           (regex: ^PRIORITY-[0-9]{4,}-.+\.md$ — date is optional, not required)
```

Operator-initiated improvements that aren't defects. Same body shape as
bugs. Bundled (after bugs are clear) into a patch-version requirements doc.

### 5.3 Requirements

```
Location:  projects/<name>/requirements/
Filename:  vX.Y.Z-short-name.md          (regex: ^v[0-9]+\.[0-9]+\.[0-9]+-.+\.md$)
```

The only intake that carries a version — and the only path to a **minor or
major** bump (bug/priority bundles always produce patch bumps). A
hand-authored requirements doc contains:

```markdown
# vX.Y.0 — Title

## Status: ready
## Target Version
vX.Y.0
## Workflow Type
release
## Source Branch
develop
## Test Required
yes
## Human Approval Required
auto

## Overview / ## Goals / ## Deliverables (D1, D2, ...)
## Constraints
## Acceptance Criteria   (testable commands)
## Suggested Decomposition   (so PM doesn't over-fragment)
## Notes for TESTER
## Context Paths
```

Lower version = higher priority by natural sort; discovery picks the
lowest eligible version first, so dropping v0.61.0 and v0.62.0 together
sequences them automatically.

### 5.4 The queue files (handled markers)

`projects/<name>/tasks/queues/{bug,priority}_backlog.md` record which
intake items discovery has already handled. Do not pre-add new items to
these files — a listed item is considered handled and will never bundle.

---

## 6. The Dashboard

A tmux session rendering every project's state. Lifecycle:

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd and a live tmux-capable environment; cannot run verbatim in harness -->
```bash
scripts/dashboard/create.sh     # build + attach the session
scripts/dashboard/detach.sh     # leave it running, return to your shell
scripts/dashboard/attach.sh     # re-attach from anywhere
scripts/dashboard/kill.sh       # tear the session down (e.g. before upgrades)
```

(Standard tmux detach — `Ctrl-b d` — also works.)

Fifteen windows, in the order the session builds them. The window list and
each window's pane layout are declared once, machine-readably, in
`team/config/dashboard_windows.census`; the guided tour and the per-window
pane tables are generated from that file into
[docs/dashboard.md](docs/dashboard.md) and
[docs/DASHBOARD-PANES.md](docs/DASHBOARD-PANES.md). The summary here is for
orientation — read those two for detail.

| # | Window | Use |
|---|---|---|
| 0 | main | The operator surface: header (framework version, clock, global HALT), live agent logs, per-project progress — and a right-hand column with per-project status (active provider, per-agent task counts, currently-working task, RC / Last Released, HALT scope). The right column is a fixed 25% of the window width (not operator-tunable). |
| 1 | operator | The kanban board as eight columns across every project — BUGS / PRIORITIES / REQUIREMENTS on the left, PM / CODER / WRITER / TESTER / CM on the right — then a full-width interactive command-line shell below it, then the legend bar at the bottom. Run `scripts/dashboard/suggested-commands.sh` from the shell for a reminder of the common commands. |
| 2 | visibility | The same eight board columns, read-only, with a legend row. Colored per projects.cfg. Item colors: white = pending, amber = active, green = done. |
| 3 | attention | Items needing the operator: BLOCKED tasks with reasons. |
| 4 | schedule | Next agent wake across the top, and a live tail of each agent's cron log (PM / CODER / WRITER / TESTER / CM) below it. |
| 5 | git | Per-project repo status and recent tags — for every registered project's repo. |
| 6 | metadata | Project/config introspection. |
| 7 | rc-metrics | Cost rollups: current-day and current-RC token spend beside the running history. |
| 8 | agent-metrics | Per-agent metrics. |
| 9 | logs | Wake logs, all agents interleaved. |
| 10 | debug-logs | Per-project debug-gate output. |
| 11 | training-logs | Reasoning-trace corpus activity. |
| 12 | terminal | Live logs beside an interactive shell at the kanban root. |
| 13 | human-review | Approvals beside auto-resolutions. |
| 14+ | drill-N | One drill-down window per project, from index 14 upward. Release and feature projects get a 5-pane layout scoped to that project (header / logs / queues / progress / cron). Document-workflow projects get a 2-pane layout: artifact version library and document-pipeline progress on top, shared logs below. |

The dashboard is read-only — it renders state from disk and never mutates
anything. Kill it freely; recreate it freely.

---

## 7. Stopping Things: HALT

Two scopes, both backed by simple sentinel files honored by every pipeline
check. Each scope has a command, so you never need to touch the files by hand:

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir -->
```bash
scripts/halt-global.sh                       # GLOBAL: all projects stop (no args)
scripts/unhalt-global.sh                     # GLOBAL: all projects resume (no args)
scripts/halt.sh   --project <name>           # PROJECT: only this project stops
scripts/unhalt.sh --project <name>           # PROJECT: only this project resumes
```

HALT blocks discovery and task dispatch at iteration start. In-flight
agent processes finish their current task (a HALT is not a kill). Wakes
remain cheap while halted (they check and exit). Use the global HALT before
upgrades; use a project HALT to quarantine one project (e.g. while its
tasks are blocked awaiting a fix) while the others keep working.

**`halt-global.sh` / `unhalt-global.sh`** are bare, no-argument commands. The
global halt takes no project or key — it has nothing to scope to — and is fully
reversible, so there is no prompt. Both are idempotent: halting an
already-halted system, or unhalting one that is not halted, exits cleanly with a
message rather than an error. They write and remove `$KANBAN_ROOT/HALT`,
replacing the old `touch $KANBAN_ROOT/HALT` / `rm $KANBAN_ROOT/HALT` workflow. If
you prefer, the underlying file mechanism still works
(`touch $KANBAN_ROOT/HALT`), but the commands are the supported path.

**`halt.sh` / `unhalt.sh` are unchanged** and remain per-project: both require
`--project` and act only on `projects/<name>/HALT`. There is no `--global` mode
on `halt.sh`; the global pair above is its own command for that reason.

> **There is deliberately no global halt-after.** `halt-after.sh` (Section 8)
> is bound to a specific project's release candidate — drain *this* project's
> RC, then halt — so it has no global meaning. No global variant of it ships,
> and the absence is what enforces that constraint.

### 7.1 When OVERWATCH clears a halt for you

Not every halt needs you. A halt caused by a fix this RC itself shipped has a
known recovery playbook, and a halt caused by a state you deliberately created
is a decision you already made. OVERWATCH can take those two on — cancelling
and recycling the RC in the first case, shipping past the stop in the second —
and escalates everything else to Needs Human exactly as it always did, with a
one-paragraph read of the block attached so you start oriented.

**It is off until you turn it on.** One key, `[release] overwatch_resolve`
(Section 1.9), ships commented out in every `kanban.cfg` and `project.cfg` and
resolves to `false` when absent. With it off, OVERWATCH is a pure observer:
nothing is classified, nothing is executed, nothing new appears on any surface.

Authorising a forced ship takes a second, separate act: a dated entry in that
project's `INTENT.md` whose text names the exact versions, branches, and bug
ids the halt's evidence carries. No entry, no forced ship — ever.

The duty itself is stated once, in `team/roles/OVERWATCH.md`, "Autonomous
Block Resolution." The operator's side of it — turning the key on, writing an
entry the match test will honour, reading the `## OVERWATCH Decision` event it
records, and watching a resolution on `why.sh` and the dashboard — is
[docs/OPERATIONS.md](docs/OPERATIONS.md), "When OVERWATCH resolves a halt for
you."

---

## 8. Stopping Things Later: HALT-AFTER

"Stop, but only after X finishes" — without watching for the moment:

<!-- doc-lint: skip — illustrative examples using .../HALT-AFTER shorthand for a live project path; requires an existing $KANBAN_ROOT and project directory -->
```bash
echo rc        > $KANBAN_ROOT/projects/<name>/HALT-AFTER   # after current RC ships
echo rc:v0.62.0 > .../HALT-AFTER                            # after that version ships
echo coder     > .../HALT-AFTER                             # after CODER work drains
touch            .../HALT-AFTER                             # empty = rc
```

Valid tokens: `rc`, `rc:vX.Y.Z`, `pm`, `coder`, `writer`, `tester`, `cm`.
Scope: the file works at `projects/<name>/HALT-AFTER` (one project) or
`$KANBAN_ROOT/HALT-AFTER` (all projects). Invalid tokens are warned about
and ignored — the system never silently halts on a typo.

Semantics:

- **`rc`** — captures the in-flight RC version at arm time (rewriting
  itself to `rc:vX.Y.Z`), then drains until that version (or higher) shows
  as Last Released.
- **`rc:vX.Y.Z`** — drains until Last Released ≥ that version.
- **Agent tokens** — drains until no task of that role is WORKING, BACKLOG,
  or WAITING. Downstream tokens also wait for PM to finish (PM may still be
  materializing work for that role). Conservative by design: it never
  halts early while queued work exists.

When the drain condition is met, the file atomically promotes itself:
HALT-AFTER is removed, HALT is created. You then `rm HALT` whenever you're
ready to resume.

---

## 9. Resetting Things: the reset scripts

Reset scripts exist so you don't open files and flip checkboxes, statuses,
and blockers by hand. They are operator power tools. The design philosophy is
fixed and encoded verbatim in the dispatcher's own header
(`scripts/reset.sh`) and in its `--help`. Four points:

- **Assume intent.** The scripts exist so the operator does not have to
  open many files and flip checkboxes, statuses, and blockers by hand.
- **Refuse only filesystem races.** A task an agent currently holds
  (WORKING state) and an ambiguous key (zero matches or multiple matches
  in the project tree) are the only two refusals. Everything else that
  might be unwise gets a one-line warning and proceeds.
- **No confirmation prompts.** The command is the confirmation.
- **No cascades.** Each reset touches its own artifact only. Resetting a
  task does not reset sibling tasks, dependent tasks, or any other kanban
  state. Composition is the operator running multiple commands
  deliberately.

A single dispatcher, `reset.sh`, handles every reset path. It selects mode
entirely from the `--key` prefix: a task key (`ROLE-YYYYMMDD-NNN`) routes an
agent-task reset with the role taken from the prefix, while an intake key
infers the type (`BUG-*` → bug, `PRIORITY-*` → priority, a version or other
string → requirement). `--project` and `--key` are both required in every
mode, because keys are unique only within a project (two projects can both
have a task `CODER-20260607-001`, for example), so there is no environment-variable
fallback:

<!-- doc-lint: skip — bare reset.sh requires $KANBAN_ROOT/scripts/ on PATH; shown without full path for readability; use $KANBAN_ROOT/scripts/reset.sh in practice -->
```bash
reset.sh --project <project-name> --key <ROLE-YYYYMMDD-NNN> [--keep-artifacts] [--force] [--help]
reset.sh --project <project-name> --key <BUG-NNNN|PRIORITY-NNNN|version> [--help]
```

`--keep-artifacts` applies to agent resets only (it opts out of clearing
`artifacts/`); intake resets do not take it. `--force` applies to agent
resets only: it clears a stale worktree (left by a prior run) before
resetting — use it as the standard corpse-clearing step when bare reset
refuses with a stale-worktree warning. `--help` / `-h` prints usage and
exits 0. A missing required flag, an unknown flag (including the removed
`--bug`/`--priority`/`--requirement` selectors), or a missing key prints
usage and exits 1.

The full reset surface is also documented in `docs/operator-commands.md`,
the authoritative operator reference.

### 9.1 Agent task resets

The unified dispatcher `reset.sh` handles all five agent reset paths; it
reads the role from the task key prefix. All calls share the same flags:

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | yes | Project the task lives in. |
| `--key <key>` | yes | Full task key, e.g. `CODER-20260611-001` for `CODER-20260611-001-some-slug`; the role is in the prefix. |
| `--keep-artifacts` | no | Preserve `artifacts/` contents (default: clear). |
| `--force` | no | Clear a stale worktree (registration and/or on-disk path left by a prior run) before resetting. Without `--force`, a stale worktree causes a warning with the manual removal recipe and a non-zero exit. Use `--force` as the standard corpse-clearing step — it runs git worktree remove, prune, and rm -rf automatically before completing the reset. |
| `--help`, `-h` | no | Print usage and exit 0. |

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; task keys used here are illustrative examples, not real tasks -->
```bash
scripts/reset.sh --project my-app --key PM-20260611-001   # example key
scripts/reset.sh --project my-app --key CM-20260611-008   # example key
scripts/reset.sh --project my-app --key CODER-20260611-002   # example key
scripts/reset.sh --project my-app --key WRITER-20260611-005   # example key
scripts/reset.sh --project my-app --key TESTER-20260611-006   # example key
```

Returns the task to freshly-materialized state — **total amnesia**: the
next wake picks it up as if it had never run. Side effects: status.md
regenerated from the materializer template (BACKLOG, no blockers, no
needs-human, no prior summary), `artifacts/` and task `logs/` cleared
(`--keep-artifacts` opts out), queue marker flipped to `[ ]`, the prior
attempt's `feature/<task-id>` branch deleted from the project repo and
stale worktree registrations pruned. The task's README.md (the work
definition) and its queue position are untouched; the training-corpus
copy is never touched (operator record, not agent-visible).

A TESTER task key additionally tears down a BLOCKED-retained TESTER
worktree via the standard teardown path.

Refusal mode: a WORKING task refuses with exit code 2 (an agent holds it —
wait or investigate). A stale worktree (registration or on-disk path left
by a prior run) also refuses with exit code 2, printing the three-command
manual removal recipe to stderr. Re-run with `--force` to clear the
corpse automatically in one call. An ambiguous key (zero or multiple
matches) refuses with exit code 1, listing the directory searched or the
candidate matches. Warning-and-proceed: a feature branch already merged
into the active RC warns loudly (re-running produces a second merge
commit) and proceeds. If `--force` cannot remove the stale path because
it is an active mount target, it aborts with exit code 4 (the mount is
named in stderr; no partial cleanup occurs).

Resetting a BLOCKED task is also how you un-gate a project: while any task
is BLOCKED with Needs Human, the project dispatches no new tasks.

### 9.2 Intake resets

The unified dispatcher `reset.sh` handles all three intake reset paths.
From an intake `--key`, it infers the item type from the prefix
(`BUG-*` → bug, `PRIORITY-*` → priority, a version or other string →
requirement). All take `--project` and `--key` only (no
`--keep-artifacts`); `--help` / `-h` exits 0:

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; keys are illustrative examples -->
```bash
scripts/reset.sh --project my-app --key BUG-0123   # example key
scripts/reset.sh --project my-app --key PRIORITY-0123   # example key
scripts/reset.sh --project my-app --key v0.1.2
```

A bug or priority key performs exactly two writes: the item's
`## Status` back to `open`, and its backlog-cache marker back to `[ ]` in
`bug_backlog.md` / `priority_backlog.md`. Nothing else in the project tree
is modified — discovery will bundle the item again on the next idle tick.

A requirement key (a version or other string) performs three writes:
`## Status` back to `ready`,
the PM task's marker flipped to `[ ]` in `pm_backlog.md`, **and the PM
materializer's idempotence hash-marker cleared.** The third step is the
headline behavior — the PM materializer keys idempotence on a sentinel
file under the PM task's `artifacts/`, not on the requirements file's
status, so without clearing the marker PM skips a "reset" requirement
silently on re-pickup. Clearing it is what makes the reset-and-rerun
workflow work end-to-end on requirements.

Key resolution is by glob within the project tree; the requirements
wrapper also accepts a bare `<key>.md` filename. Zero matches errors out
with the directory searched; multiple matches refuses and lists the
candidates.

**No cascades.** Resetting a requirement does not reset the bugs or
priorities it bundled, nor any prior round's task folders; resetting a bug
does not touch its bundle. If you want three things reset, run three
commands. Resetting an item whose bundle's RC is currently in flight warns
(possible double-handling) and proceeds.

### 9.3 RC operations (the RC trio)

The RC trio brings release-candidate control under the same uniform
`--project <name> --key vX.Y.Z` signature:

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; also operates on live release state that does not exist in harness -->
```bash
scripts/cm/cancel-rc.sh                  --project my-app --key v0.7.17         # abandon an active RC
scripts/ship-rc.sh                       --project my-app --key v0.7.17         # manual ship escape hatch
scripts/reset.sh                         --project my-app --key <cm-task-key>   # re-run the CM release step
```

`cm/cancel-rc.sh` cleanly unwinds an active release candidate (deletes the
local and origin RC branch, resets `release-state.md`); a `--yes` flag skips
the confirmation prompt for non-interactive use.
`ship-rc.sh` squash-merges a verified RC into develop and then main as a
manual ship escape hatch.

The third member of the trio is `reset.sh` on a CM task key: to re-run just
the CM release step on an already-verified RC, reset the CM release task with
`reset.sh --project <name> --key <cm-task-key>`. That returns the CM task to
BACKLOG so the next CM wake re-runs the release step.

---

## 10. Common Operations

### Reading lint findings (non-blocking)

Quality-class lints never fail a suite. Findings print in full and
land in `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (and
`findings-integration.json`). The TESTER report carries each finding
with the bug that tracks it. Operators need to do nothing: the next
bugfix bundle picks the bugs up. If a suite exits non-zero, the cause
is breakage (syntax/compile) or a test failure — never a lint.


**Upgrade the live install** (drains nothing by itself — HALT first):

<!-- doc-lint: skip — upgrade narrative requires a live $KANBAN_ROOT install, a tracked git clone, and scripts/ on the relative path from cwd; cannot run verbatim in harness -->
```bash
scripts/halt-global.sh           # or HALT-AFTER and wait
scripts/dashboard/kill.sh
cd ~/develop/pgai-agent-kanban && git pull
$KANBAN_ROOT/scripts/upgrade.sh  # backs up, checks out latest tag, installs
scripts/unhalt-global.sh
scripts/dashboard/create.sh
```

**Targeting a specific version (forward-only).** Like `install.sh`,
`upgrade.sh` takes an optional positional version argument to upgrade *to* a
specific released tag's tree, instead of whatever the dev tree currently has
checked out:

<!-- doc-lint: skip — requires a live $KANBAN_ROOT install and a dev-tree checkout carrying the target tag; cannot run verbatim in harness -->
```bash
$KANBAN_ROOT/scripts/upgrade.sh v2.2.0      # upgrade to tag v2.2.0's tree
$KANBAN_ROOT/scripts/upgrade.sh ai_v2.2.0   # same tag; the "ai_" prefix is normalized
```

The tag is resolved exactly the way the version-targeted install resolves it:
it must exist in the dev tree's git history, or the upgrade fails loud, names
the requested version, and lists the nearest available tags. The resolved tree
is laid down from a temporary detached worktree, so the dev tree's checked-out
branch is never touched.

Upgrades are **forward-only**. The target must be strictly newer than the
version recorded in the live install's `VERSION` file — a semver-aware
comparison, not a string compare — or the upgrade is refused before anything
is deposited:

- **Target equals the installed version** — refused. To force a clean re-lay
  of the version you already run, use `install.sh --force` instead.
- **Target is older than the installed version** — refused. There is no
  `--force` override for direction; `--force` only controls per-file overwrite
  prompts, never which way time moves.

To move an installation *backwards* to an older version, do a fresh install —
`install.sh` is directionless and lays everything down from scratch, so it can
go backwards where `upgrade.sh` will not (this is the same path referenced in
section 1.1):

<!-- doc-lint: skip — requires a git checkout carrying the target tag; sanctioned downgrade path, cannot run verbatim in harness -->
```bash
./install.sh v2.1.0 --force    # fresh install of an older tag: the sanctioned downgrade
```

Omit the version argument to upgrade using whatever the dev tree currently has
checked out; that is the default. On success the summary
narrates the move, for example `upgraded v2.1.0 -> v2.2.0 (from tag v2.2.0)`.

**Two-phase upgrade model.** The command above is all an operator
runs. Under the hood `upgrade.sh` works in **two phases**: the installed
script and the dev-tree script split the work, so the piece that
performs the upgrade is always the one that knows the version being
installed.

- **Phase 1 — bootstrap** (the *installed* `scripts/upgrade.sh` when
  invoked without `--phase2`). Parses operator args; resolves and
  validates `<dev_tree>`; creates the pre-upgrade backup tarball;
  runs `bash -n` on `<dev_tree>/team/scripts/upgrade.sh`; probes it
  for `--phase2` support; then `exec`s into it as phase 2. **Phase 1
  performs no deposit, no config changes, and no crontab work.** It
  is a small, stable bootstrap that changes rarely.
- **Phase 2 — the upgrade proper** (the *dev-tree*
  `team/scripts/upgrade.sh` invoked with `--phase2 --phase2-protocol
  1 --kanban-root <root> --backup <tarball>` plus the original
  operator args). Does the real work — deposit, state preservation,
  crontab tier handling, VERSION stamping, divergence advisory — from
  the source tree, with BASH_SOURCE-relative library sourcing, so the
  code executing the upgrade is the code being installed. The deposit
  writes into the kanban root; it never touches the executing file.

The handoff protocol is versioned (`--phase2-protocol 1`) and
fail-loud: a broken new script (syntax error), a dev-tree script
that lacks `--phase2` support (an older script, or a downgrade), or a
phase-2 side that rejects the protocol number all cause phase 1 to
exit non-zero **before anything is deposited**. Your pre-upgrade
backup is untouched in every failure path.

**Retirement step (graveyard).** After the deposit, `upgrade.sh` runs a
**retirement** step that moves obsolete managed files out of the live tree
without deleting them. The only files touched are the exact relative paths
listed in `$KANBAN_ROOT/templates/retired-files.txt` (the retirement
manifest). For each manifest entry that exists at the live root, the file
is moved — never `rm`'d — to
`$KANBAN_ROOT/retired/<UTC-ts>/<original-relative-path>`, and one loud
`[upgrade] Retired: ...` line prints per file; the summary block ends with
a `Retired: N file(s) -> retired/<ts>/` line (or `Retired: none`). To
restore a retired file, move the graveyard copy back to its original
relative path — e.g. a retired
`$KANBAN_ROOT/retired/20260707T143000Z/workflows/release.yaml` is restored
with `mv "$KANBAN_ROOT/retired/20260707T143000Z/workflows/release.yaml"
"$KANBAN_ROOT/workflows/release.yaml"`. **Operator-authored content is
untouchable by construction**: the manifest is a strict allow-list of exact
relative paths, so any file whose path is not named in
`retired-files.txt` — custom workflow plugin dirs, wrappers, local
edits — is byte-preserved by construction and never enters the graveyard.
Manifest entries whose paths are absent from the live root are silent
no-ops (fresh installs, already-cleaned installs). The graveyard plus the
pre-upgrade backup tarball give you two independent recovery layers.

**Phase 1 fail-loud: "Manual bootstrap: cp … scripts/ then retry".**
The phase-1 bootstrap refuses to hand off when the dev-tree
`upgrade.sh` fails one of its probes. You will see one of these error
lines on stderr and a non-zero exit, and **no deposit will have
happened** (your pre-upgrade backup is intact):

```
[upgrade] Dev-tree upgrade.sh not found: <dev_tree>/team/scripts/upgrade.sh
[upgrade] Manual bootstrap: cp <dev_tree>/team/scripts/upgrade.sh scripts/ then retry
```

```
[upgrade] Dev-tree upgrade.sh failed bash -n syntax check:
<syntax error output>
[upgrade] Nothing has been deposited.  Your pre-upgrade backup is still intact.
[upgrade] Manual bootstrap: cp <dev_tree>/team/scripts/upgrade.sh scripts/ then retry
```

```
[upgrade] Dev-tree upgrade.sh does not appear to support --phase2 (it may be an older script or a downgrade).
[upgrade] Nothing has been deposited.  Your pre-upgrade backup is still intact.
[upgrade] Manual bootstrap: cp <dev_tree>/team/scripts/upgrade.sh scripts/ then retry
```

Recovery is the same in every case: copy the dev-tree `upgrade.sh`
into the installed `scripts/` directory by hand, then re-run the
standard upgrade above. `<dev_tree>` in the error line is your local
clone — the same one you `git pull`ed at the top of the upgrade
procedure — and the message prints the absolute path in place of
`<dev_tree>` so you can copy-paste it verbatim.

<!-- doc-lint: skip — recovery procedure requires a live $KANBAN_ROOT install and a dev-tree checkout; <dev_tree> is a placeholder for the absolute path printed in the error line -->
```bash
cd $KANBAN_ROOT
cp <dev_tree>/team/scripts/upgrade.sh scripts/   # exact path from the error line
$KANBAN_ROOT/scripts/upgrade.sh                  # retry the standard upgrade
```

The same recipe applies whenever an installed `upgrade.sh` cannot hand
off to the dev tree's: replace the installed copy with the dev tree's,
then upgrade as normal.

**Stop the wake batches between tasks** (lighter than HALT — the
in-flight task finishes): `touch` the stop file (default
`<temp_root>/wakeup/stop`; key: `kanban.cfg [wake] stop_file`). Every
running batch, for every agent and every project, stops at its next task
boundary, and no new batch dispatches while the file exists. No wake
removes it — `rm` the file to resume.

**Unblock a BLOCKED task** after resolving its cause:
`scripts/reset.sh --project <name> --key <key>` (section 9.1) —
the task returns to BACKLOG with a clean slate and the next wake picks it
up. While any task is BLOCKED with Needs Human, the project dispatches no
new tasks (by design — fix the blocker first).

**Check costs**: `scripts/cost-report.sh --project <name>` (day / RC / month scopes), or
dashboard window 7 (rc-metrics). Every task writes a `tokens.json`; rollups are
per-day, per-RC, per-agent, per-provider.

**Read the Blocked column**: window 7's history table prints `Blocked`
(`blocked_minutes` in `history.csv`) beside `Wall` — the minutes an RC spent
under a HALT or parked on a task in `BLOCKED` awaiting a human, overlaps
counted once. Wall stays honest open-to-ship elapsed, so real execution time is
Wall minus Blocked. A row whose `blocked_minutes` is empty shows `-`.

**Cancel a stuck RC** (rare, operator-only): section 9.3 —
`scripts/cm/cancel-rc.sh --project <name> --key vX.Y.Z` rewinds release
state; read its `--help` first.
