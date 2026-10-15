# Docker Workspace

Running pgai-agent-kanban in a container is running it in a **workspace**: the
right tools, the right environment, and a login shell sitting in the kanban root.
Nothing more. The container does not run a scheduler as PID 1 and does not
dispatch startup "modes." You start the container, you land in a shell, and from
there you drive the kanban with the same commands you would use on a host.

That is the interactive path, and the kanban stops when you leave the shell.
To run the kanban detached in one named container instead, with its scheduler
started for you and a soft stop, see
[Run the kanban in a container](#run-the-kanban-in-a-container).

**[The board-up path](#the-board-up-path) below is the whole procedure, in
order, from an empty machine to a running board.** Every step is a command with
a stated expected result. Follow it top to bottom; the reference sections after
it explain the choices each step glosses over.

This document lives in the repository checkout and is not copied into an
installed kanban root — see [Where this document lives](#where-this-document-lives)
for why, and for what it owns versus what `HOW_TO.md` owns.

If you are new to the kanban, read [README.md](../README.md) and
[HOW_TO.md](../HOW_TO.md) first. This document assumes you know what the kanban is
and how a release moves through the pipeline; it covers the container path only.

The four moving parts, in the order you do them:

1. **Build** an image with `docker/build.sh <flavor>`, which passes the pinned
   CLI versions in as build arguments.
2. **Populate the volume** on the host with `install.sh --no-system-cron`. The
   container refuses to start against anything else.
3. **Launch** an interactive workspace shell with `container-shell.sh`, naming
   the image you built.
4. **Start pseudocron and the dashboard yourself**, inside that shell, exactly as
   on a host — and start exactly one scheduler, because the host crontab and an
   in-container scheduler would be two heartbeats on one board.

---

## The board-up path

Nine steps, in order, on a machine that has nothing but a container engine, git,
and a shell. Each step states what to run and what you should see. Nothing here
assumes a variable you exported earlier in some other terminal, a file you
happened to create, or a directory that already exists: every step either
creates what it needs or names it explicitly.

Two paths appear throughout, and they are different directories on purpose:

- **The checkout** — where you cloned this repository. It holds `docker/`,
  `install.sh`, and this document. Steps 1 through 5 run here, on the host.
- **The kanban root** — the directory `install.sh` populates, which becomes the
  container's volume. Steps 6 through 9 run against it, most of them inside the
  container.

### Step 1 — Clone the repository (host)

<!-- doc-lint: skip — clones from an operator-supplied remote into a new directory; the harness has no remote to clone and must not create host directories -->
```bash
git clone <repo-url> ~/develop/pgai-agent-kanban
cd ~/develop/pgai-agent-kanban
```

Expected: a checkout containing `install.sh` at its top level and a `docker/`
directory beside it. Everything in steps 2 through 5 runs from this directory,
which is what "the repository root" means everywhere below.

### Step 2 — Confirm a container engine answers (host)

<!-- doc-lint: skip — probes the operator's real container engine; the harness has no engine installed and a missing engine is not a documentation failure -->
```bash
docker version
```

Expected: client and server version blocks. If the server block is missing or
the command errors, the engine daemon is not running — fix that before going
further, because steps 3 and 6 both need it. On a podman host run `podman
version` instead, and see [Podman](#podman) for the one configuration line that
points the launcher at it.

### Step 3 — Build an image (host, repository root)

<!-- doc-lint: skip — image build requires a running Docker/podman daemon and the full source tree as build context; the harness cannot establish a daemon -->
```bash
PGAI_CONTAINER_TIER=1 docker/build.sh debian
```

Expected: a `build.sh: building flavor=debian …` line, the engine's own build
output, and a closing `build.sh: built debian as pgai-kanban:debian
pgai-kanban:latest` line. Those two tags are what the later steps run: step 6
names `pgai-kanban:latest`, and so does `container-up.sh` by default. The
Debian flavor is the right default; see [Choosing a flavor](#choosing-a-flavor)
if you have a RHEL-lineage constraint, and [Build](#build) for the other
flavors and the script's options.

`PGAI_CONTAINER_TIER=1` is the operator's authorization for container-tier
work ([The container tier is operator-only](#the-container-tier-is-operator-only)).
Without it the script prints one `SKIPPED` line and exits `0` having built
nothing. If you see that line, the variable did not reach the script. The
script loads the pinned CLI versions from `docker/cli-versions.env` itself, so
there is nothing to source first.

### Step 4 — Choose the kanban root and export it (host)

<!-- doc-lint: skip — exports a site-specific path into the operator's own shell; the harness runs each fenced block in its own shell, where the export cannot persist to the next one -->
```bash
export PGAI_AGENT_KANBAN_ROOT_PATH="$HOME/pgai_agent_kanban"
```

Expected: no output. This one variable is the whole configuration of the
container path: it is the directory the next step populates, the directory the
launcher bind-mounts, and the directory the container's shell starts in. Put it
in your shell profile if you intend to come back to this workspace.

The directory does not have to exist yet — step 5 creates it.

### Step 5 — Populate the kanban root (host, repository root)

<!-- doc-lint: skip — install narrative requires a real target directory and a source tree; runs against site-specific host paths the harness cannot supply -->
```bash
./install.sh --no-system-cron --add-claude-agents
```

Expected: a stream of `[install] Installed: …` lines, ending in
`[install] Installation complete!` and a numbered **Next steps** block naming
your kanban root. Afterwards `$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/pseudocron.py`
exists — that file is exactly what the container's entrypoint looks for to tell
an install from a raw checkout.

Both flags matter for the container path, for different reasons:

- `--no-system-cron` leaves the host crontab untouched — no writes, no probes —
  while still installing the pseudocron config the container will run. That is
  the whole point: the scheduler config lands on the volume and the host does
  not start ticking behind your back. See
  [One heartbeat](#one-heartbeat-pseudocron-in-the-container).
- `--add-claude-agents` writes the provider agent wrappers to `~/.claude/agents/`
  **on the host**. There is no in-container installer, and the launcher mounts
  your host `$HOME` — so the wrappers the agents run inside the container are
  the ones this flag put in your host home. Omit it and the container comes up
  fine and the agents have nothing to run. Use `--add-codex-agents` instead, or
  as well, if you drive the kanban with Codex.

`install.sh` is the fresh-install path only. To move an existing kanban root to
a newer version, run `upgrade.sh` against it on the host instead; the mounted
install updates in place.

### Step 6 — Launch the workspace shell (host)

<!-- doc-lint: skip — launching a container requires a built image and a running container engine; the harness has neither -->
```bash
team/scripts/container-shell.sh --image-tag pgai-kanban:latest
```

Expected: one resolution line on stderr, then a shell prompt inside the
container, with the working directory already set to your kanban root:

```
container-shell.sh: engine=docker image=pgai-kanban:latest kanban_root=/home/you/pgai_agent_kanban home=/home/you
```

Read that line before you type anything else. It names the engine, the image,
the volume and the home the container is about to use, and it is the cheapest
way to confirm you are about to run the ones you meant.

`--image-tag` is not optional here. The launcher's own default tag is
`pgai-agent-kanban-debian:latest`, which `docker/build.sh` does not produce, so
always name the tag step 3 built.

That invocation is the checkout's copy of the launcher. `install.sh` also
installs it into the kanban root, so from anywhere on the host this equivalent
works and needs no checkout:

<!-- doc-lint: skip — launching a container requires a built image and a running container engine; the harness has neither -->
```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/container-shell.sh" --image-tag pgai-kanban:latest
```

Those are the same script at two paths — `team/scripts/…` is the checkout
layout, `scripts/…` is the install layout. If the command instead prints a
refusal naming a missing path or a raw repository checkout, read
[The mount contract](#the-mount-contract-from-the-operators-side); each refusal
names its own remedy, and every one of them is fixed on the host, not in the
container.

**Everything from here runs inside that container shell.**

### Step 7 — Start exactly one scheduler (in the container)

<!-- doc-lint: skip — starts a real scheduler process inside a running container with a live tmux server; cannot run verbatim in the harness -->
```bash
tmux new -d -s pseudocron \
  'python3 $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/pseudocron.py \
     >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/pseudocron.log 2>&1'
```

Expected: no output, and a detached tmux session named `pseudocron` now running
the scheduler. One session, one process, one heartbeat — the rule this step
exists to protect is in
[One heartbeat](#one-heartbeat-pseudocron-in-the-container), and it is the one
rule on the container path with no equivalent on a host.

The kanban root is already the working directory and
`PGAI_AGENT_KANBAN_ROOT_PATH` is already exported inside this shell; the
entrypoint did both before handing it to you. There is nothing to source first.
(`shell-env` is optional and `install.sh` does not create it — the wake scripts
source it themselves if you later add one.)

### Step 8 — Confirm the heartbeat (in the container)

<!-- doc-lint: skip — tails a live scheduler log inside a running container; the log exists only once pseudocron is running -->
```bash
tail -f "$PGAI_AGENT_KANBAN_ROOT_PATH/logs/pseudocron.log"
```

Expected: two startup banner lines, `pseudocron starting: N jobs loaded from …`
and `pseudocron starting: N vars loaded from …`, then one
`fired (minute=NN): <command>` line per scheduled minute.

Two readings of that output decide whether the board will ever move:

- **`vars loaded` is `0`** — fix `pseudocron.env` before anything else. The jobs
  will fire into an environment that cannot run them.
- **Two `fired` lines per scheduled minute** — you have two heartbeats. Stop the
  second scheduler; see [One heartbeat](#one-heartbeat-pseudocron-in-the-container).

`Ctrl-c` stops the tail. It does not stop the scheduler — that is running in its
own tmux session.

### Step 9 — Bring the board up (in the container)

<!-- doc-lint: skip — builds and attaches a tmux session; requires a live tmux server and an interactive terminal the harness does not provide -->
```bash
scripts/dashboard/create.sh
```

Expected: the tmux dashboard builds and attaches, showing window 0 — header
across the top, per-project progress, a live log tail, and a queues column on
the right. `Ctrl-b d` detaches without stopping it; `scripts/dashboard/attach.sh`
re-attaches.

The board is now up and the scheduler is ticking. A fresh install has no
projects registered yet, so the queues render empty until you create one —
that is `HOW_TO.md`'s territory, and the commands are identical inside the
container and on a host.

Window 1 (`operator`) is the window to live in when the container shell is your
only terminal: it carries the whole board plus an interactive shell at the
bottom. See [dashboard.md](dashboard.md) for the window-by-window tour.

---

## Run the kanban in a container

The board-up path gives you an interactive shell, and the kanban stops when you
exit it. Three scripts instead run the kanban **detached**, in one named
container, with its scheduler started inside it:

| Script | What it does |
|---|---|
| `container-up.sh` | Runs an image you already built, detached, as the container `pgai-kanban`; starts one scheduler inside it; confirms the scheduler's first heartbeat. |
| `container-status.sh` | Prints one status block (or JSON). Reads only. |
| `container-down.sh` | Soft stop: stops the scheduler, waits for running agents to finish, then stops and removes the container. |

`install.sh` puts them under the kanban root's `scripts/`; a checkout has the
same scripts under `team/scripts/`. The commands below use the installed paths
and assume board-up steps 1 to 5 are done: an image built with
`docker/build.sh`, a kanban root populated by `install.sh --no-system-cron`,
and `PGAI_AGENT_KANBAN_ROOT_PATH` exported in your shell. All three run on the
host, and all three read that variable: it must name the kanban root the
container mounts.

This path replaces steps 6 to 9. Do not also start a scheduler by hand; see
[One scheduler, one heartbeat](#one-scheduler-one-heartbeat) below.

### Cron or the container, never both

The host and the container are two ways to run one kanban, and exactly one runs
at a time: the host runs on cron, the container on pseudocron. `container-up.sh`
enforces that at start. **It refuses, with exit `1` and before any engine call,
while your crontab carries a kanban wake line**: a job line that runs
`scripts/wake-batch.sh`, `scripts/wake.sh` or `scripts/wake-now.sh`. Comments,
including a commented-out wake line, do not count. There is no override flag.

The refusal (error key `container_up.host_cron_wake_lines`) counts the wake
lines without printing them, because a crontab line can carry environment
assignments. Its remedy is a command of this shape, with your home directory
filled in:

```
crontab -l > /home/you/crontab-before-container-up.bak && crontab -l | grep -Ev 'scripts/wake(-batch|-now)?\.sh' | crontab -
```

It saves your crontab to `~/crontab-before-container-up.bak`, then reinstalls
it without the wake lines. Run `container-up.sh` again afterwards, and pass
`--wake-tier none` to every later `upgrade.sh` run so an upgrade does not put
the lines back. A kanban root installed with `--no-system-cron` (step 5) leaves
the crontab untouched, so there is usually nothing to remove.

The check runs only when the container starts. The host side never looks for a
running container. If wake lines come back while the container runs,
`container-status.sh` reports them as an `ERROR` line, and nothing else does.

### Operator-only

`container-up.sh` and `container-down.sh` start and stop containers, so they sit
behind the container-tier gate
([The container tier is operator-only](#the-container-tier-is-operator-only)).
Authorize a run with `PGAI_CONTAINER_TIER=1` in your shell or with
`--operator-approved` on the command line. Without either, and always inside an
agent wake, each prints one `SKIPPED` line and exits `0` having touched nothing.
`--help` needs no authorization.

`container-status.sh` reads and never changes anything, so it is not gated: it
runs anywhere, inside an agent wake included.

### Bring it up

<!-- doc-lint: skip — starts a detached container and its scheduler; requires a built image and a running container engine the harness does not have -->
```bash
PGAI_CONTAINER_TIER=1 "$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/container-up.sh"
```

In order, it:

1. Checks your crontab for kanban wake lines, as above.
2. Resolves the container name (`[docker] container_name`, default
   `pgai-kanban`, or `--name`) and the image (`[docker] image`, default
   `pgai-kanban:latest`, or `--image-tag`). **It never builds or pulls.** A
   missing image is refused with exit `1` (`container_up.image_missing`), and
   the remedy is a `docker/build.sh` command.
3. Runs the image detached under that name, with the same mounts and
   environment as `container-shell.sh`: `$HOME` and the kanban root at
   matching paths, plus any `--mount` you add.
4. Starts exactly one scheduler inside it: the tmux session `pseudocron` running
   `scripts/pseudocron.py` and logging to `logs/pseudocron.log`. That is the
   same command as step 7.
5. Waits for the scheduler's first heartbeat, a `fired (minute=NN)` line written
   after the start, for up to `--heartbeat-timeout` seconds (default `120`).
   pseudocron fires on the minute, so a healthy scheduler beats within about a
   minute.

Expected: a first line naming the engine, container, image and kanban root, and
a last line of the form
`container-up.sh: heartbeat confirmed at <time>; container 'pgai-kanban' is up`.

It is safe to run again. What it does depends on what it finds:

| Found | What `container-up.sh` does |
|---|---|
| No container of that name | Creates it and starts the scheduler. |
| A stopped container of that name | Removes it, recreates it, starts the scheduler. |
| Running, with no scheduler | Starts the scheduler. |
| Running, with a live scheduler | Says so and exits `0`, starting nothing. If it runs a different image from the one requested, it says that too; bring it down first to switch images. |

If no heartbeat appears within the timeout, it exits `1`
(`container_up.heartbeat_unconfirmed`) and **leaves the container and its
scheduler running** for you to inspect. The remedy tails the scheduler log.
Look for a startup error, or a `vars loaded` count of `0` (see step 8).

`container-up.sh` starts the scheduler and nothing else. The dashboard is not
started.

| Flag | Effect |
|---|---|
| `-t, --image-tag TAG` | Image to run; it must already exist. Default `[docker] image`, else `pgai-kanban:latest`. |
| `--name NAME` | Container name. Default `[docker] container_name`, else `pgai-kanban`. |
| `--mount HOST:CONTAINER` | Extra bind mount. Repeatable. |
| `--heartbeat-timeout SECONDS` | How long to wait for the first heartbeat. Default `120`. |
| `--operator-approved` | Authorize this run without `PGAI_CONTAINER_TIER=1`. |
| `-h, --help` | Print usage and exit `0`. |

Exit codes: `0` up with a confirmed heartbeat, already up, or skipped; `1`
refused (host wake lines, missing image, missing kanban root or `$HOME`,
engine missing or an engine command failed) or no heartbeat within the
timeout; `2` usage error.

### One scheduler, one heartbeat

[One heartbeat](#one-heartbeat-pseudocron-in-the-container) applies unchanged:
one board, one scheduler. `container-up.sh` starts one and never a second; a run
against a container whose scheduler is live starts nothing. The rest is yours:

- Do not start pseudocron by hand inside this container.
- Do not start one in a workspace shell (`container-shell.sh`) against the same
  kanban root while this container runs. That is a second container, and a
  second heartbeat on the same board.

When more than one scheduler runs inside the container, `container-up.sh`
prints a `WARNING` and `container-status.sh` prints an `ERROR` line. Neither
can see a scheduler in another container or on the host, apart from the
crontab check.

### Check it

<!-- doc-lint: skip — queries a container engine for a named container; the harness has no engine and the exit code reflects container state, not documentation truth -->
```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/container-status.sh"
```

It prints one block:

| Field | Shows |
|---|---|
| `container` | The container's name and state: `running`, `stopped` or `absent`. |
| `image` | The image it runs. |
| `uptime` | How long it has run. |
| `scheduler` | `running` with its pid, or `not running`. |
| `last heartbeat` | The age and time of the last `fired (minute=NN)` line in `logs/pseudocron.log`, or `none`. It is read even when the container is down, so it shows how long ago the board last beat. |
| `agent sessions` | How many agent sessions run in the container, then one line per session: role and task ID. |
| `host crontab` | How many kanban wake lines your crontab carries, or `no kanban wake lines`. |

`ERROR:` lines follow the block when your crontab has wake lines while the
container runs, or when more than one scheduler runs in it. `--json` prints the
same report as one JSON object, and `--name NAME` reads a container other than
`[docker] container_name`.

The exit code makes it usable as a check:

| Exit | Meaning |
|---|---|
| `0` | The container runs with a live scheduler. |
| `3` | It does not: absent, stopped, or running without a scheduler. |
| `1` | Error: kanban root missing, engine missing, an engine query failed, or the report could not be assembled. |
| `2` | Usage error. |

Exit `0` says only that the container and its scheduler run. It does not judge
the heartbeat's age or the `ERROR` lines. Read those yourself.

### Bring it down: the soft stop

<!-- doc-lint: skip — stops and removes a running container; requires a running container engine the harness does not have -->
```bash
PGAI_CONTAINER_TIER=1 "$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/container-down.sh"
```

By default the stop is soft: agents already running get time to finish.

1. **Scheduler first.** It ends the tmux session `pseudocron`, then sends
   `SIGTERM` to any pseudocron process still in the container (one started by
   hand, for example). After this no new wake starts. A scheduler still running
   about 5 seconds after `SIGTERM` makes it stop with exit `1`, and the remedy
   kills that process.
2. **Agents finish.** It waits for the agent sessions already running in the
   container to exit. A session is a liveness record under the kanban root's
   `locks/task-liveness/` whose process runs in the container. The wait lasts at
   most `[docker] container_stop_timeout_seconds` (default `1800`). It prints a
   progress line naming the tasks when the wait starts, and once every
   `--progress-interval` seconds (default `60`) after that. When the timeout
   runs out, it names the sessions still running and goes on to step 3 anyway.
3. **Stop and remove.** It stops the container, then removes it.

**`--now` skips step 2.** The scheduler is still stopped first. Then the
script names any agent sessions still running and stops and removes the
container at once, ending them. Use it when you accept cutting an agent off.

A container that is already stopped is removed. One that does not exist is
reported and the script exits `0`.

`container_stop_timeout_seconds` takes whole seconds, digits only. Any other
value is refused with exit `2` (`container_down.stop_timeout_invalid`), and the
remedy resets it to `1800`.

| Flag | Effect |
|---|---|
| `--now` | Skip the wait for agent sessions. |
| `--name NAME` | Container name. Default `[docker] container_name`, else `pgai-kanban`. |
| `--progress-interval SECONDS` | Seconds between progress lines while waiting; at least `1`. Default `60`. |
| `--operator-approved` | Authorize this run without `PGAI_CONTAINER_TIER=1`. |
| `-h, --help` | Print usage and exit `0`. |

Exit codes: `0` stopped and removed (after the wait, after the timeout, or at
once with `--now`), already absent, or skipped; `1` refused (kanban root
missing, engine missing, or an engine command failed; the container is left as
it was at that point); `2` usage error or a malformed
`container_stop_timeout_seconds`.

### HALT is never touched

`container-up.sh` and `container-down.sh` never read, write or remove a `HALT`
file, and `container-status.sh` does not look at one. A soft stop ends the
container; it is not a pause. A `HALT` on the kanban root pauses the chain
inside the container exactly as it does on a host, and it stays until you
remove it.

### Configuration

The three scripts read the `[docker]` section of the kanban root's `kanban.cfg`.
The defaults, as `kanban.cfg_example` documents them:

```ini
[docker]
docker_exe = docker
container_name = pgai-kanban
image = pgai-kanban:latest
container_stop_timeout_seconds = 1800
```

`docker_exe` picks the engine, as it does for the workspace launcher (see
[Podman](#podman)). Each script's flags override these values for one run.

---

## Where this document lives

This document ships in the repository checkout only. It is deliberately not on
`team/config/docs-manifest.txt`, so `install.sh` and `upgrade.sh` do not copy it
into a kanban root, and it will not be found under `$PGAI_AGENT_KANBAN_ROOT_PATH/docs/`.

That is not an oversight. Every step this document owns that happens before an
install exists — cloning, building an image from `docker/`, running `install.sh`
— can only be performed from a checkout, so a reader following this page
necessarily has one. The manifest's own membership rule is the set of `docs/`
paths cited from the governance files an install receives; nothing installed
cites this page. What does cite it — `README.md` and `HOW_TO.md` at the
repository root — is checkout-only for the same reason.

**What this document owns, and what `HOW_TO.md` owns.** The split is stated from
both sides and the two agree:

| Owned here | Owned by `HOW_TO.md` |
|---|---|
| The image build (`docker/build.sh`), the two flavors, and the pinned CLI versions | The `install.sh` command line and every flag it accepts |
| The volume-is-an-install rule and the mount contract | Configuring `kanban.cfg`, registering projects, daily operation |
| Launching the workspace shell and the launcher's flags; running the kanban detached with `container-up.sh`, `container-status.sh` and `container-down.sh` | The host install shapes (system cron, cron-less, container) |
| Starting pseudocron and the board *inside the container* | Starting them on a host, and everything the board shows |

`HOW_TO.md` describes the container install shape as "the cron-less shape, run
on the host" and hands the rest here. This document uses exactly that install
command in step 5 and does not restate its flag reference.

---

## What ships in `docker/`

| File | Purpose |
|---|---|
| `docker/build.sh` | Builds one flavor: `docker/build.sh <flavor>`. Loads the pinned CLI versions, uses the repository root as build context from any working directory, and tags `pgai-kanban:<flavor>` and `pgai-kanban:latest`. Repository only, never installed. See [Build](#build). |
| `docker/cli-versions.env` | The single source of the pinned provider-CLI versions every flavor installs. Two `KEY=value` lines: `CLAUDE_CODE_VERSION` and `CODEX_VERSION`. See [The pinned CLI versions](#the-pinned-cli-versions) below. |
| `docker/debian/Dockerfile` | Debian 12 flavor. Base `python:3.12-bookworm`, with Node 20 copied in from a `node:20-bookworm` build stage. |
| `docker/rocky9/Dockerfile` | Rocky Linux 9 flavor. Base `quay.io/rockylinux/rockylinux:9`, with EPEL enabled and the tool set installed from dnf. |
| `docker/claude-abliterated/Dockerfile` | Operator-only flavor on the third-party base `t2fn/claude-abliterated:latest-amd64`, for running private LLMs. Not a supported image: see [The claude-abliterated flavor](#the-claude-abliterated-flavor). |
| `docker/entrypoint.sh` | Startup shared by both flavors. Enforces the three-condition mount contract, `cd`s into the kanban root, and execs a login `bash`. It does nothing else. |

None of these files hold credentials or site paths. Everything site-specific —
the kanban install, your home directory, provider credentials — arrives at
runtime through mounts the launcher assembles.

---

## Choosing a flavor

Both produce a behaviorally equivalent workspace: same entrypoint, same
mount contract, same non-root `kanban` user at UID/GID 1000, same Node 20 and
Python 3.12, same two pinned provider CLIs. The choice affects image lineage,
not your day-to-day flow. **If you have no preference, pick Debian.**

| Flavor | Base image | Pick it when |
|---|---|---|
| **debian** (default) | `python:3.12-bookworm` | You have no RHEL-lineage constraint. Smallest image, fastest build, and the flavor the board-up path builds. |
| **rocky9** | `quay.io/rockylinux/rockylinux:9` | Your shop standardizes on a RHEL-compatible base. Larger image; pulls its tool set from dnf with EPEL enabled, so the build takes noticeably longer. |

Two things worth knowing before you pick:

**Why rocky9 builds from quay.io and not Docker Hub.** The Rocky project's
current Rocky 9 images are published to `quay.io/rockylinux/rockylinux:9`. The
Docker Hub `rockylinux:9` tag is stale — a build from it starts from an older
snapshot of the distribution. The `FROM` line in `docker/rocky9/Dockerfile` names
the quay.io reference deliberately; do not "simplify" it back to the Hub tag.

**UID/GID is a build-time preference.** Both flavors create the `kanban` user
at UID/GID 1000 and let you override with
`--build-arg KANBAN_UID=<uid> --build-arg KANBAN_GID=<gid>`. `docker/build.sh`
passes no build arguments beyond the two pinned versions, so an override means
running the engine's build command yourself; `docker/build.sh <flavor> --dry-run`
prints the command to start from.

**Only declared flavors are supported.** The supported flavors are listed, one
per line, in `team/config/container-images.txt`. The container-tier harnesses
build only from that list, and only the images the operator selects from it
(see [Choosing which images a run builds](#choosing-which-images-a-run-builds)).
A directory under `docker/` that the list does not name is never built by any
harness and is not a supported image.

### The claude-abliterated flavor

`docker/claude-abliterated/` is an operator-only image for running the kanban
against private LLMs. It builds on a third-party base image
(`t2fn/claude-abliterated:latest-amd64`, a floating tag) and is deliberately
left out of `team/config/container-images.txt`, so no harness, cold-build lane
or deep-verify run ever builds it. It installs the same pinned CLIs and
requirements files as the supported flavors, and fixes the `kanban` user at
UID/GID 1000 (whatever account the base image ships at those ids is renamed)
because the launcher bind-mounts host paths at matching ids. Build it with
`docker/build.sh` like any other flavor, then run it with
`container-shell.sh --image-tag`:

<!-- doc-lint: skip — image build requires a running Docker/podman daemon and the full source tree as build context; the harness cannot establish a daemon -->
```bash
PGAI_CONTAINER_TIER=1 docker/build.sh claude-abliterated --tag abliterated
team/scripts/container-shell.sh --image-tag pgai-kanban:claude-abliterated
```

`--tag abliterated` (an example name) tags the image `pgai-kanban:claude-abliterated`
and `pgai-kanban:abliterated`, and leaves `pgai-kanban:latest` on the image it
already named. Without `--tag`, `latest` moves to this image, and
`container-up.sh` runs `latest` by default.

---

## The pinned CLI versions

Every image installs two provider CLIs from npm. Their versions live in one
file — [`docker/cli-versions.env`](../docker/cli-versions.env) — and nowhere
else, this page included. The file holds exactly two `KEY=value` lines, the
Claude Code version from `@anthropic-ai/claude-code` and the Codex version
from `@openai/codex`:

```
CLAUDE_CODE_VERSION=<the pinned Claude Code version>
CODEX_VERSION=<the pinned Codex version>
```

Open the file to read the versions an image built from your checkout will
carry; a copy quoted here would go stale with the next bump.

**No Dockerfile carries a version of its own, and none carries a default.** Each
declares `ARG CLAUDE_CODE_VERSION` and `ARG CODEX_VERSION` with no value and
then refuses — before the npm download, naming the missing argument — if either
arrives unset or empty. A build invoked without both arguments does not quietly
install a floating "latest"; it stops.

That is why `docker/build.sh` reads the file itself and passes both values as
build arguments on every build. Only a build you run by hand, outside the
script, has to load the file into its environment first, in one line, from the
repository root:

<!-- doc-lint: skip — the path is relative to the repository root and the harness does not change directory before executing a fenced command; running it verbatim from any other cwd is a false failure -->
```bash
set -a && . docker/cli-versions.env && set +a
```

`set -a` marks everything the dot-command defines for export, so the two
variables are visible to the `--build-arg` expansions that follow. The cold-build
lane reads the same file through `team/pgai_agent_kanban/container/cli_pins.py`,
which turns it into the identical `--build-arg` flags — the lane,
`docker/build.sh` and this document cannot drift apart, because there is only
one file to drift from.

### Bumping a pinned version

The order matters. Do these two steps in this order, never the other way around:

1. **Put the candidate version through the cold-build lane first.** Edit
   `docker/cli-versions.env` on a branch, then run the lane:

   <!-- doc-lint: skip — the cold-build lane builds every flavor under every installed container engine; the harness has no engine and no minutes to spend -->
   ```bash
   # From the kanban install, in your own interactive shell. Builds every
   # declared flavor under every engine present, with no layer cache and a
   # forced base-image re-pull.
   PGAI_CONTAINER_TIER=1 scripts/cold-build-now.sh --project <name> --image all
   ```

   The lane is part of the operator-only container tier; see
   [The container tier is operator-only](#the-container-tier-is-operator-only)
   below.

   The lane is the only thing in this system that can see a bad pin. Each
   Dockerfile smoke-checks the installed CLIs in their own layer — `claude
   --version` and `codex --version` — so a version that publishes to npm but does
   not actually launch fails there, naming the CLI, rather than shipping as a
   working image.

2. **Only once the lane is green, land it as an ordinary RC.** There is no
   special release path for a version bump. It is a one-line diff that goes
   through the same pipeline as everything else.

The reason for the order is blast radius: a pin that cannot build is not one
broken flavor, it is every flavor at once, and it lands on an RC where the fast
suites cannot see it. A lane run costs minutes of wall clock. A release that
ships images nobody can build costs the next loop.

> Flags, freshness rules, and the rest of the deep-verify tier's command surface
> are in [operator-commands.md](operator-commands.md); the tier's doctrine —
> when the expensive gate runs and what each verdict is worth — is in `SOP.md`.
> This section owns only the pins policy.

---

## The container tier is operator-only

The harnesses that build images, pull base images, or start containers on the
host are the container tier: `run-cold-build-tests.sh`,
`run-three-host-matrix.sh`, `harness/strangers-path-check.sh`, `deep-verify.sh`,
and `cold-build-now.sh`, which runs `deep-verify.sh`. It is the heaviest work
the host does, so it runs only when the operator says so.
**Agents never invoke it.** A task that seems to need its coverage records that
in its status `## Summary` for the operator instead.

**The authorization is `PGAI_CONTAINER_TIER=1`, or `--operator-approved`.** Set
the variable in your own interactive shell, a crontab line you wrote, or the
environment you started the API server in; the value must be exactly `1`. Or pass
`--operator-approved` on the command line, which authorizes that one run the same
way. A harness that runs another harness passes the authorization on by plain
inheritance.

`docker/build.sh`, `container-up.sh` and `container-down.sh` sit behind the
same gate. `docker/build.sh` takes only the variable; it has no
`--operator-approved` flag. `container-status.sh` reads only and is not gated.

<!-- doc-lint: skip — runs the container tier, which builds images and starts containers; the harness has no engine and is never authorized -->
```bash
# Example: run the deep-verify lane for one project, every declared image,
# from the kanban install.
PGAI_CONTAINER_TIER=1 bash scripts/deep-verify.sh --project <name> --image all

# The same run, authorized by flag instead of the variable.
bash scripts/deep-verify.sh --operator-approved --project <name> --image all
```

**An unauthorized call is skipped, not refused.** A harness does nothing, before
its first engine command, in two cases:

- Neither `PGAI_CONTAINER_TIER=1` nor `--operator-approved` was given.
- The invocation is inside an agent wake, with or without either. The wake
  bootstrap also removes the variable after sourcing `shell-env`, `claude-env`,
  `secrets` and `kanban.cfg`, so no agent inherits an operator's authorization.

It then prints one line and exits `0`:

```
deep-verify.sh: SKIPPED key=container_tier.operator_only reason="container tier is operator-only: ..." operator_command="PGAI_CONTAINER_TIER=1 bash <path>/deep-verify.sh --project <name>"
```

`operator_command` is the command you would run from your own shell. Nothing is
built, pulled or started, and no verdict or ledger record is written, so the
deep-verify ledger keeps its last real result and a skip is never reported as a
pass. Exit `0` is deliberate: an agent that reaches for the tier finishes its
task normally and never stalls an RC. `--help` works without the authorization.

### Choosing which images a run builds

An authorized run builds only the images you select from
`team/config/container-images.txt` (today `debian` and `rocky9`):

- `--image NAME` builds `NAME`. Repeat the flag, or give a comma-separated list
  (`--image debian,rocky9`), for more than one.
- `--image all` builds every image the manifest declares.
- With no `--image`, a harness run by another harness builds what the outer run
  selected. Otherwise `kanban.cfg [release] container_images` supplies the
  default, in the same vocabulary.
- With neither a flag nor a default, the harness lists the available images,
  prints an example command, and exits `0` without building anything or writing
  a ledger record.

A name the manifest does not declare is refused before anything is built:
error key `container_tier.unknown_image`, exit `3`, and a remedy naming the
same command with `--image all`. The REST `deep-verify` and `cold-build-now`
routes select images per call with the optional `image` body field — a list of
declared names, or `"all"` — passed to the script as `--image`. A call that
omits the field builds the `kanban.cfg` default, and with no default it builds
nothing; an undeclared name is refused with the same error key before anything
is dispatched ([operator-api.md](operator-api.md)). The field never grants the
operator authorization above. A scheduled `deep-verify.sh --scheduled` entry
builds what its own line names, so the crontab examples in
[operator-commands.md](operator-commands.md) carry `--image all`.

### The docker-use log

Every container-engine command the kanban issues goes through one shared
helper, which appends one line per event to
`$PGAI_AGENT_KANBAN_ROOT_PATH/logs/docker-use.log`:

```
<UTC timestamp> <EVENT> actor=<actor> task=<task-id|-> script=<name> images=<a,b|-> [key=value ...]
```

| Event | Meaning |
|---|---|
| `START` | A container-tier run, or a standalone engine command, began. |
| `FINISH` | It ended; adds `duration=<N>s exit=<code>`. |
| `SKIPPED` | A harness was called without authorization and did nothing. |
| `REFUSED` | A harness refused before building, such as an undeclared image; adds `reason=`. |

`actor` is `operator` for a call from outside any agent wake, and the agent role
(`coder`, `tester`, ...) inside one, or `agent` when the role is not known.
Agent attempts are logged too, so the log shows who reached for docker, not only
who ran it. Read-only probes outside a run (`docker info`, `docker ps`, ...) are
not recorded. The log is append-only and never blocks: a missing log directory
is created, and one that cannot be written produces a single warning while the
harness runs or skips exactly as it would have.

`scripts/why.sh --project <name> --details` ends with a `== docker use ==`
section carrying the log's last line as `last docker use: <line>`, or
`no docker use recorded (<path>)` when there is none.

---

## Build

`docker/build.sh <flavor>` builds one image. Run it from anywhere: it finds
`docker/<flavor>/Dockerfile` and the build context, the repository root, from
its own location, loads the pinned CLI versions from `docker/cli-versions.env`,
and passes them as `--build-arg CLAUDE_CODE_VERSION=…` and
`--build-arg CODEX_VERSION=…`. It ships in the checkout only and is never
installed, because it needs the repository as its build context.

<!-- doc-lint: skip — image build requires a running Docker/podman daemon and the full source tree as build context; the harness cannot establish a daemon -->
```bash
# Debian flavor (default).
PGAI_CONTAINER_TIER=1 docker/build.sh debian

# Rocky 9 flavor.
PGAI_CONTAINER_TIER=1 docker/build.sh rocky9
```

**Every build tags the image twice: `pgai-kanban:<flavor>` and
`pgai-kanban:latest`.** So `latest` names whichever flavor you built last, and
that is the image `container-up.sh` runs by default (`[docker] image`). Pass
`--tag` to leave `latest` where it is.

A flavor is any `docker/<flavor>/` directory holding a Dockerfile: today
`debian`, `rocky9` and `claude-abliterated`. Any other name is refused with exit
`2`, listing the valid flavors.

| Option | Effect |
|---|---|
| `--tag TAG` | Tag `pgai-kanban:TAG` instead of `pgai-kanban:latest`. `pgai-kanban:<flavor>` is always added. |
| `--no-cache` | Build without the engine's layer cache. |
| `--engine docker\|podman` | Engine to build with. Default: `[docker] docker_exe` in the kanban's `kanban.cfg` (`$PGAI_AGENT_KANBAN_ROOT_PATH/kanban.cfg`, else `~/pgai_agent_kanban/kanban.cfg`) when set, else `docker`. |
| `--dry-run` | Print the one build command and run nothing. |
| `-h, --help` | Print usage and exit `0`. |

**A real build is operator-only.** It needs `PGAI_CONTAINER_TIER=1` in the
invoking shell. Without it, or inside an agent wake, the script prints one
`SKIPPED` line and exits `0` having built nothing. `--help`, the flavor check
and `--dry-run` run before that gate and touch no engine, so a preview needs no
authorization:

<!-- doc-lint: skip — the path is relative to the repository root and the harness does not change directory before executing a fenced command; running it verbatim from any other cwd is a false failure -->
```bash
docker/build.sh rocky9 --dry-run
```

It prints one line: the engine, `build -f <checkout>/docker/rocky9/Dockerfile`,
both pinned build arguments, `-t pgai-kanban:rocky9 -t pgai-kanban:latest`, and
the checkout as the build context.

The Dockerfiles use no docker-only syntax. On a podman host, set
`docker_exe = podman` (see [Podman](#podman)) or pass `--engine podman`; the
cold-build lane builds every declared flavor under both engines.

Exit codes: `0` built, printed (`--dry-run`, `--help`), or skipped; `1` the
pinned versions could not be read (`docker_build.cli_pins_unreadable`; the
remedy restores `docker/cli-versions.env` from git) or the engine is not on
`PATH`; `2` usage error or unknown flavor; any other code is the engine's own
exit status when the build itself fails.

The Debian build is the fastest; the Rocky 9 flavor pulls a larger base and
installs from dnf, so budget more time for it.

---

## Populate the volume: the volume-is-an-install rule

The directory you mount as the kanban root must be one that **`install.sh` has
populated — never a raw clone of the kanban repository.** The two layouts differ:
an install puts helper scripts at `scripts/…`, while a repo checkout puts them at
`team/scripts/…`. The entrypoint, the launcher, and every in-container command in
this document assume the install layout (`scripts/pseudocron.py`,
`scripts/dashboard/create.sh`), so a repo checkout mounted in its place lands you
in a shell where none of the runbook commands resolve.

The entrypoint refuses to start in that case rather than dropping you into a
broken shell. Populate the volume on the host first:

<!-- doc-lint: skip — install narrative requires a real target directory and a source tree; runs against site-specific host paths the harness cannot supply -->
```bash
# On the host, from the repository root, before launching the container.
PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_HOST_VOLUME" ./install.sh --no-system-cron
```

`--no-system-cron` leaves the host crontab exactly as it is — no writes, no
probes — while still installing the `pseudocron.cfg` the container will run. That
combination is the whole point: the scheduler config lands on the volume, and the
host does not start ticking behind your back. See
[One heartbeat: pseudocron in the container](#one-heartbeat-pseudocron-in-the-container)
for why that matters.

Upgrades work the same way: run `upgrade.sh` against the volume on the host, and
the mounted install updates in place.

---

## Launch a workspace shell

[`team/scripts/container-shell.sh`](../team/scripts/container-shell.sh) takes one
flag in the normal case: `--image-tag`, naming the image you built. It resolves its paths from your environment, supplies
the two standard mounts, exports the environment the entrypoint needs, and runs
whichever container engine you configured.

The launcher exists at two paths and they are the same script: `team/scripts/container-shell.sh`
in a repository checkout, and `scripts/container-shell.sh` under the kanban root
`install.sh` produced. Run whichever one is in front of you.

<!-- doc-lint: skip — launching a container requires a built image and a running container engine; the harness has neither -->
```bash
# From the repository root, with PGAI_AGENT_KANBAN_ROOT_PATH exported.
team/scripts/container-shell.sh --image-tag pgai-kanban:latest
```

The launcher's default image tag is **`pgai-agent-kanban-debian:latest`**, and
`docker/build.sh` does not produce it. After a `docker/build.sh` build, always
name the tag it built: `pgai-kanban:latest` for the flavor you built last, or
`pgai-kanban:<flavor>` for a specific one.

Before it calls the engine at all, the launcher prints one line naming what it
resolved:

```
container-shell.sh: engine=docker image=pgai-kanban:latest kanban_root=… home=…
```

Read that line. It is the cheapest way to confirm you are about to run the image
and the volume you meant to.

### The two standard mounts

The launcher always applies two bind mounts, at **matching host and container
paths** so nothing needs translating on either side:

| Mounted | Why |
|---|---|
| `$HOME` | Your login shell sources your real dotfiles, and the provider CLI reads your real credentials and agent wrappers from `~/.claude`. |
| `$PGAI_AGENT_KANBAN_ROOT_PATH` | The kanban install the container works against. |

It exports `HOME` and `PGAI_AGENT_KANBAN_ROOT_PATH` into the container to match,
and the image's entrypoint `cd`s into the kanban root before handing you the
shell.

The home mount is why step 5 passes `--add-claude-agents` on the host: the agent
wrappers the container runs are the ones in your host `~/.claude/agents/`,
because that directory *is* the one inside the container.

### The container is disposable; the mounts are not

The launcher runs the engine with `--rm`. **When you exit the shell, the
container is deleted.** That is deliberate, and it is safe for one reason: every
piece of durable state lives on the two bind-mounted paths, not in the container.
Your task folders, queues, logs, `HALT` flags, release state, config files and
credentials are all on the host filesystem, reached through the mounts.

The practical consequences:

- **Relaunching returns you to the same board.** Exit mid-release, run
  `container-shell.sh` again, and the queues, task states and logs are exactly
  where you left them. Nothing is "in" the container to lose.
- **The scheduler and the board do not survive the exit.** Both run as processes
  inside the container, so leaving the shell ends them. Steps 7 and 9 are what
  you re-run on the next launch; nothing they produced is lost, because all of
  it was written to the volume.
- **Anything you install inside the container is gone at exit.** A `dnf install`,
  a `pip install` outside the mounted paths, an edit to a file baked into the
  image — all discarded. If you need a tool permanently, add it to the flavor's
  Dockerfile and rebuild. That is the supported path.
- **A wedged container is not a wedged install.** Exit it and start another.

### Flags

`--help` prints the full set.

| Flag | Effect |
|---|---|
| `-t, --image-tag TAG` | Image to run. Default `pgai-agent-kanban-debian:latest`, which `docker/build.sh` does not produce; after a build pass `pgai-kanban:latest` or `pgai-kanban:<flavor>`, for example `pgai-kanban:rocky9`. |
| `--mount HOST:CONTAINER` | Add an extra bind mount. Repeatable. |
| `--kanban-root PATH` | Override `PGAI_AGENT_KANBAN_ROOT_PATH`. |
| `-h, --help` | Print usage and exit. |

An unknown flag, a missing flag value, or a `--mount` value without a colon exits
2 with the usage text. A missing kanban root or `$HOME` exits 1, naming the
missing path, before the engine is ever called.

### Podman

The launcher resolves its container engine from `kanban.cfg`:

```ini
[docker]
docker_exe = docker
```

RedHat-lineage shops that standardize on podman set `docker_exe = podman`. The
launcher passes flags both engines share (`run --rm -it -v -e`), with one
engine-specific addition. The images run as the unprivileged `kanban` user, and
rootless podman — the default on Rocky 9 — maps the operator's own files to
container root rather than to that user, so the in-container user could not
write the bind-mounted install. When the resolved engine is podman, the mounts
helper (`team/scripts/lib/container_mounts.sh`) therefore adds a user-namespace
mapping to the run arguments, so the in-container user owns the operator's
files. Under docker the arguments carry no mapping and are unchanged. An absent
`[docker]` section or a blank value falls back
to `docker` with no error; a configured value that is not an executable on `PATH`
fails loud, naming both the `docker_exe` key and the offending value. See
`team/scripts/lib/docker_engine.sh` for the resolution rules. `container-up.sh`,
`container-down.sh` and `container-status.sh` resolve the engine from the same
key, and `docker/build.sh` uses it as its default for `--engine`.

---

## The mount contract, from the operator's side

[`docker/entrypoint.sh`](../docker/entrypoint.sh) checks three conditions before
it gives you a shell, in this order. Each failure exits 1 and prints two lines:
what is wrong, and a remedy you run **on the host**. It never starts a degraded
shell and lets you discover the problem later.

You will normally never see any of these, because `container-shell.sh` satisfies
all three. They fire when you invoke the container engine directly, when you
point the launcher at the wrong directory, or when a mount silently did not
happen.

| Refusal | What it means | What to run on the host |
|---|---|---|
| `PGAI_AGENT_KANBAN_ROOT_PATH is not set to an existing directory` | The kanban root was not exported into the container, or the path it names is not there — usually a mount that did not happen. | `export PGAI_AGENT_KANBAN_ROOT_PATH=<kanban-install-dir>` before running `container-shell.sh`, or pass `--kanban-root <kanban-install-dir>`. |
| `HOME is not set to an existing directory` | Your home was not mounted, so the login shell would source no dotfiles and the provider CLI would find no credentials. | Run the launcher, which mounts and exports your host `$HOME` automatically. If you are driving the engine by hand, add `-v "$HOME:$HOME" -e "HOME=$HOME"`. |
| `looks like a raw repository checkout, not an install.sh-populated kanban install` | You mounted a git checkout. The entrypoint saw helpers at `team/scripts/` (checkout layout) instead of `scripts/` (install layout). | `PGAI_AGENT_KANBAN_ROOT_PATH=<install-dir> ./install.sh --no-system-cron`, then mount `<install-dir>` instead of the checkout. |
| `is not an install.sh-populated kanban install: scripts/pseudocron.py is missing` | The mounted directory is neither an install nor a checkout — an empty directory, a wrong path, or a partial copy. | Same remedy: populate a real install directory with `install.sh --no-system-cron` and mount that. |

The last two are the same check reported two ways. The entrypoint looks for
`scripts/pseudocron.py`, and when it is absent it tells you which of the two
situations you are in so the fix is unambiguous.

---

## One heartbeat: pseudocron in the container

The workspace container gives you a shell; it does not schedule anything.
Starting the scheduler is your job — and the rule that matters here has no equivalent on a
host:

> **A host crontab plus an in-container scheduler is two heartbeats on one
> board.**

The kanban root is bind-mounted at the *same path* inside the container as
outside it. A crontab on the host and a pseudocron inside the container are not
two independent systems; they are two schedulers firing wake jobs at the same
install, the same queues, the same task folders. Work gets pulled twice, agents
collide, and the logs read like a haunting. The ritual below exists to guarantee
exactly one heartbeat.

The ritual is for the workspace shell. On the detached path `container-up.sh`
performs it for you: it refuses to start while the host crontab carries kanban
wake lines, and it starts one scheduler and never a second. See
[Run the kanban in a container](#run-the-kanban-in-a-container).

**1. Keep the host crontab off.** You already did this if you installed the
volume with `--no-system-cron`, which leaves the host crontab untouched. Confirm
it on the host:

<!-- doc-lint: skip — reads the invoking operator's real crontab and is expected to exit non-zero on a host with none installed; the harness must not treat either outcome as a verdict -->
```bash
crontab -l
```

"no crontab for …" is the answer you want. If a kanban crontab is installed
instead, back it up and remove it before going further — see
[pseudocron.md](pseudocron.md), which owns the host-side procedure.

**2. Start exactly one scheduler, by hand, inside the workspace shell.** This is
step 7 of the board-up path. Run it in the container, where
`PGAI_AGENT_KANBAN_ROOT_PATH` is already set and the working directory is the
kanban root:

<!-- doc-lint: skip — starts a real scheduler process inside a running container with a live tmux server; cannot run verbatim in the harness -->
```bash
tmux new -d -s pseudocron \
  'python3 $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/pseudocron.py \
     >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/pseudocron.log 2>&1'
```

One session, one process. If you launch a second workspace shell against the same
volume, do not start a second pseudocron in it — that is the same two-heartbeat
failure with both hearts inside containers.

**3. Confirm the single heartbeat.** In the container:

<!-- doc-lint: skip — tails a live scheduler log inside a running container; the log exists only once pseudocron is running -->
```bash
tail -f "$PGAI_AGENT_KANBAN_ROOT_PATH/logs/pseudocron.log"
```

On startup pseudocron logs two banner lines — `pseudocron starting: N jobs loaded
from …` and `pseudocron starting: N vars loaded from …`. **If `vars loaded` is
`0`, fix `pseudocron.env` before anything else.** Each dispatch then logs one
`fired (minute=NN)` heartbeat. One `fired` line per scheduled minute is a healthy
board; two is the problem this section exists to prevent.

Because these are the ordinary host commands, everything [pseudocron.md](pseudocron.md)
says applies unchanged — the wake logs under `logs/`, the per-agent wake locks,
and the `HALT` file on the volume all behave identically inside the container.

---

## The dashboard

Also yours to start, with the same command as on a host, inside the workspace
shell:

<!-- doc-lint: skip — builds and attaches a tmux session; requires a live tmux server and an interactive terminal the harness does not provide -->
```bash
scripts/dashboard/create.sh
```

`Ctrl-b d` detaches without stopping it; `scripts/dashboard/attach.sh`
re-attaches. The dashboard reads the mounted volume, so it survives the container
exactly as the scheduler does: kill the container and the tmux session dies with
it, but nothing it was displaying is lost. See [dashboard.md](dashboard.md).

---

## Related documents

- [dashboard.md](dashboard.md) — the tmux dashboard, window by window.
- [operator-commands.md](operator-commands.md) — `cold-build-now.sh` and the rest of the deep-verify command surface.
- [HOW_TO.md](../HOW_TO.md) — install, configure, and daily operation on a host.
- [pseudocron.md](pseudocron.md) — the scheduler you start inside the shell, from the host's side.
- [operator-troubleshooting.md](operator-troubleshooting.md) — when something looks wrong.

The last two, like this document, ship in the repository checkout only.
