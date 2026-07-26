# Docker Workspace

Running pgai-agent-kanban in a container is running it in a **workspace**: the
right tools, the right environment, and a login shell sitting in the kanban root.
Nothing more. The container does not run a scheduler as PID 1, does not dispatch
startup "modes," and does not enforce a mount contract. You start the container,
you land in a shell, and from there you drive the kanban with the same commands
you would use on a host.

If you are new to the kanban, read [README.md](../README.md) and
[HOW_TO.md](../HOW_TO.md) first. This document assumes you know what the kanban is
and how a release moves through the pipeline; it covers the container path only.

The three moving parts:

1. **Build** an image (Debian or Rocky 9 flavor).
2. **Launch** an interactive workspace shell with `container-shell.sh`.
3. **Start pseudocron and the dashboard yourself**, inside that shell, exactly as
   on a host.

---

## What ships in `docker/`

| File | Purpose |
|---|---|
| `docker/debian/Dockerfile` | Debian image. Base `python:3.12-bookworm`, plus a `node:20-bookworm` stage for the provider CLI. Installs the runtime tool set (git, jq, tmux, tree, ncurses, procps, curl) and creates a non-root `kanban` user. |
| `docker/rocky9/Dockerfile` | Rocky Linux 9 image. Base `rockylinux:9`, with EPEL enabled. Installs the same core tool set (git, jq, tmux, tree, ncurses, nodejs/npm, procps-ng, vim) from dnf and creates the same non-root `kanban` user. Uses no docker-only syntax, so it builds under podman too. |
| `docker/entrypoint.sh` | Minimal startup shared by both flavors. Verifies that `PGAI_AGENT_KANBAN_ROOT_PATH` names an existing directory (fails loud if not), `cd`s there, and execs a login `bash`. That is all it does. |

None of these files hold credentials or site paths. Everything site-specific —
the kanban install, your home directory, provider credentials — arrives at
runtime through mounts the launcher assembles.

---

## Choosing a flavor

| Flavor | When to pick it | Base image |
|---|---|---|
| **Debian** (default) | Any environment without a RHEL-lineage constraint. Smaller image, faster build. | `python:3.12-bookworm` |
| **Rocky 9** | RedHat-lineage shops that want a RHEL-compatible base. Larger image; pulls its tool set from dnf with EPEL enabled. | `rockylinux:9` |

Both flavors produce a behaviorally equivalent workspace: same entrypoint, same
tools, same non-root user. The choice affects image lineage, not your day-to-day
flow. If you have no preference, pick Debian.

---

## Build

Build from the repository root — the Dockerfiles copy `docker/entrypoint.sh`
relative to that root, so the build context is `.`.

<!-- doc-lint: skip — image build requires a running Docker/podman daemon and the full source tree as build context; the harness cannot establish a daemon -->
```bash
# Debian flavor (default). Run from the repository root.
docker build -f docker/debian/Dockerfile -t pgai-agent-kanban-debian:latest .
```

<!-- doc-lint: skip — image build requires a running Docker/podman daemon and the full source tree as build context; the harness cannot establish a daemon -->
```bash
# Rocky 9 flavor. Run from the repository root.
docker build -f docker/rocky9/Dockerfile -t pgai-agent-kanban-rocky9:latest .
```

The Rocky 9 build pulls a larger base image and installs its tools from dnf, so it
takes longer than the Debian build. Substitute `podman build` for `docker build` on
a podman host — the Dockerfiles use no docker-only syntax.

---

## Launch a workspace shell

`team/scripts/container-shell.sh` starts the container and drops you into an
interactive login shell in the kanban root. It supplies the two standard mounts,
exports the environment the entrypoint needs, and runs whichever container engine
you configured.

<!-- doc-lint: skip — launching a container requires a built image and a running container engine; the harness has neither -->
```bash
# From the kanban install (with PGAI_AGENT_KANBAN_ROOT_PATH set). Default image
# is pgai-agent-kanban-debian:latest.
team/scripts/container-shell.sh
```

The launcher always applies two bind mounts, at **matching host and container
paths** so nothing needs translating on either side:

| Mounted | Why |
|---|---|
| `$HOME` | Your login shell sources your real dotfiles and the provider CLI reads your real credentials from `~/.claude`. |
| `$PGAI_AGENT_KANBAN_ROOT_PATH` | The kanban install the container works against. |

It exports `HOME` and `PGAI_AGENT_KANBAN_ROOT_PATH` into the container to match,
and the image's entrypoint `cd`s into the kanban root before handing you the
shell.

Common flags (`--help` prints the full set):

| Flag | Effect |
|---|---|
| `-t, --image-tag TAG` | Image to run. Default `pgai-agent-kanban-debian:latest`; pass `pgai-agent-kanban-rocky9:latest` for the Rocky 9 flavor. |
| `--mount HOST:CONTAINER` | Add an extra bind mount. Repeatable. |
| `--kanban-root PATH` | Override `PGAI_AGENT_KANBAN_ROOT_PATH`. |
| `-h, --help` | Print usage and exit. |

If the kanban root or `$HOME` does not exist, the launcher exits 1 naming the
missing path before it ever calls the engine.

### Podman

The launcher resolves its container engine from `kanban.cfg`:

```ini
[docker]
docker_exe = docker
```

RedHat-lineage shops that standardize on podman set `docker_exe = podman`. The
launcher passes only flags both engines share (`run --rm -it -v -e`), so it works
identically under either. An absent `[docker]` section or a blank value falls back
to `docker` with no error; a configured value that is not an executable on `PATH`
fails loud, naming both the `docker_exe` key and the offending value. See
`team/scripts/lib/docker_engine.sh` for the resolution rules.

---

## Then start pseudocron and the dashboard yourself

The container gives you a shell; it does not schedule anything. Starting the
scheduler and the dashboard is your job, using the **same commands as on a host**
(see [pseudocron.md](pseudocron.md) and [dashboard.md](dashboard.md)). Run these
inside the workspace shell, where `PGAI_AGENT_KANBAN_ROOT_PATH` is already set and
the working directory is the kanban root.

Start pseudocron in a detached tmux session:

<!-- doc-lint: skip — starts a real scheduler process inside a running container with a live tmux server; cannot run verbatim in the harness -->
```bash
source $PGAI_AGENT_KANBAN_ROOT_PATH/shell-env
tmux new -d -s pseudocron \
  'python3 $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/pseudocron.py \
     >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/pseudocron.log 2>&1'
```

On startup it logs two banner lines — `N jobs loaded` and `N vars loaded`. If
`vars loaded` is `0`, fix `pseudocron.env` before anything else. Each dispatch
then logs a `fired (minute=NN)` heartbeat.

Start the dashboard:

<!-- doc-lint: skip — builds and attaches a tmux session; requires a live tmux server and an interactive terminal the harness does not provide -->
```bash
scripts/dashboard/create.sh
```

`Ctrl-b d` detaches without stopping it; `scripts/dashboard/attach.sh` re-attaches.

Because these are the ordinary host commands, everything the host docs say about
them applies unchanged — one scheduler at a time, the wake logs under `logs/`, and
the `HALT` file on the volume all behave identically inside the container.

---

## The volume-is-an-install rule

The directory you mount as the kanban root must be one that **`install.sh` has
populated — never a raw clone of the kanban repository.** The two layouts differ:
an install puts helper scripts at `scripts/…`, while a repo checkout puts them at
`team/scripts/…`. The entrypoint, the launcher, and the commands above all assume
the install layout (`scripts/pseudocron.py`, `scripts/dashboard/create.sh`), so a
repo checkout mounted in its place lands you in a shell where none of the runbook
commands resolve.

Populate the volume on the host first:

<!-- doc-lint: skip — install narrative requires a real target directory and a source tree; runs against site-specific host paths the harness cannot supply -->
```bash
# On the host, before launching the container. KANBAN_HOST_VOLUME is the
# directory you will point PGAI_AGENT_KANBAN_ROOT_PATH at.
PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_HOST_VOLUME" ./install.sh --no-system-cron
```

`--no-system-cron` skips the host crontab write while still installing the
`pseudocron.cfg` the container will run — you start the scheduler by hand inside
the workspace shell, so the host does not need a crontab. Upgrades work the same
way: run `upgrade.sh` against the volume on the host, and the mounted install
updates in place.

---

## Related documents

- [pseudocron.md](pseudocron.md) — the scheduler you start inside the shell.
- [dashboard.md](dashboard.md) — the tmux dashboard.
- [HOW_TO.md](../HOW_TO.md) — install, configure, and daily operation on a host.
- [operator-troubleshooting.md](operator-troubleshooting.md) — when something looks wrong.
