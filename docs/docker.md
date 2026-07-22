# Docker Deployment Runbook

End-to-end procedure for running pgai-agent-kanban as a container: host-side
install onto a volume, image build, compose up, first-contact verification,
upgrade, and HALT semantics under a container restart.

The commands are pasteable verbatim against a fixture volume. Every
`<placeholder>` in this document appears only in prose or in a variable
assignment the reader edits **before** running the block; the block itself
then runs unmodified.

If you are new to the kanban, read [README.md](../README.md) and
[HOW_TO.md](../HOW_TO.md) first. This document assumes you already know what
the kanban is and how a release moves through the pipeline; it covers the
container path specifically.

---

## What ships in `docker/`

The container path splits into two flavor subdirectories plus a pair of
shared files. Both flavors consume the same entrypoint and bashrc — the
shared layer is flavor-neutral by construction.

| File | Purpose |
|---|---|
| `docker/debian/Dockerfile` | Debian image definition. Base `python:3.12-bookworm`. Installs the runtime dependency set (tmux, ncurses, procps, git, gawk, jq, plus operator tools), verifies `tmux -V >= 3.1` at build time, and creates a non-root `kanban` user. |
| `docker/debian/docker-compose.example.yaml` | Reference Compose file for the Debian flavor. Copy to `docker-compose.yaml` and edit. |
| `docker/rhel9/Dockerfile` | RHEL9/UBI9 image definition. Base `registry.access.redhat.com/ubi9/ubi`. Installs Python 3.12 from AppStream, jq from EPEL, and builds tmux (3.3a) and tree (2.1.1) from source because neither is packaged for UBI9. Same `tmux -V >= 3.1` build-time gate and same non-root `kanban` user pattern. |
| `docker/rhel9/docker-compose.example.yaml` | Reference Compose file for the RHEL9 flavor. Copy to `docker-compose.yaml` and edit. |
| `docker/entrypoint.sh` | Fail-loud startup shared by both flavors. Verifies the four bind mounts, exports `PGAI_AGENT_KANBAN_ROOT_PATH=/pgai_agent_kanban`, and execs the requested role — `pseudocron` (default), `shell`, or `dashboard`. |
| `docker/bashrc` | Interactive-shell environment shared by both flavors. Sources `shell-env` from the mounted install so a fresh shell satisfies the environment contract without manual sourcing. |

None of these files hold credentials or site paths. Credentials and site
content arrive at runtime, through the mounts described below.

**Migration note (v1.26.0).** Existing Debian deployments must adjust one
path: `docker/Dockerfile` moved to `docker/debian/Dockerfile`, and
`docker/docker-compose.example.yaml` moved to
`docker/debian/docker-compose.example.yaml`. Regenerate any local
`docker-compose.yaml` from the new example (the `build.context` is now
`../..` and `build.dockerfile` is `docker/debian/Dockerfile`), or update
the two lines in place. `docker/entrypoint.sh` and `docker/bashrc` did
not move and are byte-identical across the restructure.

---

## Choosing a flavor

The container ships in two flavors. Pick one before the build step; the
`cd /tmp/pgai-agent-kanban-src` in the build blocks below assumes you
have completed Phase 1 (host-side install onto the volume) and have the
source tree in the same directory Phase 1 uses.

| Flavor | When to pick it | Base image | Trade-off |
|---|---|---|---|
| **Debian** (default) | Any environment without a RHEL-lineage constraint. Smaller image, faster build, no source-compiled binaries. | `python:3.12-bookworm` | None for most sites. |
| **RHEL9 / UBI9** | RedHat-standard shops whose security or compliance tooling expects RHEL-lineage images (UBI9). The Debian image is technically fine on RedHat hosts but fails procurement/audit sniff tests at RHEL-only sites. | `registry.access.redhat.com/ubi9/ubi` | Larger image; `tmux` and `tree` are compiled from source at build time (UBI9 does not package them); `jq` comes from EPEL (disable EPEL and drop `jq` if your site policy forbids third-party repos — the dashboard degrades gracefully). |

Both flavors produce a functionally equivalent container: same entrypoint,
same bashrc, same mount contract, same `pseudocron` default mode, same
`tmux -V >= 3.1` build-time gate. The choice affects the image lineage
and the base package repository, not the operator's day-to-day flow.

If you have no preference, pick Debian. Everything downstream in this
document that says `docker/<flavor>/...` should be read with `debian` in
that slot.

### Build the Debian flavor

<!-- doc-lint: skip — procedural build narrative; requires a pre-cloned source tree at /tmp/pgai-agent-kanban-src, an existing compose example copy, and a running Docker daemon; harness cannot establish these prerequisites -->
```bash
# From the repository root — the same source tree used for install.sh.
cd /tmp/pgai-agent-kanban-src

# Copy the shipped example to the active compose file.
cp docker/debian/docker-compose.example.yaml docker/debian/docker-compose.yaml

# Build the image. Compose reads docker/debian/Dockerfile via the example's
# build:context: ../.. / dockerfile: docker/debian/Dockerfile block.
docker compose -f docker/debian/docker-compose.yaml build
```

### Build the RHEL9 flavor

<!-- doc-lint: skip — procedural build narrative; requires a pre-cloned source tree at /tmp/pgai-agent-kanban-src, an existing compose example copy, and a running Docker daemon; harness cannot establish these prerequisites -->
```bash
# From the repository root — the same source tree used for install.sh.
cd /tmp/pgai-agent-kanban-src

# Copy the shipped example to the active compose file.
cp docker/rhel9/docker-compose.example.yaml docker/rhel9/docker-compose.yaml

# Build the image. Compose reads docker/rhel9/Dockerfile via the example's
# build:context: ../.. / dockerfile: docker/rhel9/Dockerfile block.
docker compose -f docker/rhel9/docker-compose.yaml build
```

The RHEL9 build takes longer than the Debian build because `tmux` and
`tree` compile from source inside the image. Both builds fail the layer
with a named error if `tmux -V` falls below 3.1 — the version gate is
identical across flavors and guards against base-image drift.

Bringing the container up is Phase 3 below, once you have edited the
mounts in the compose file you copied. Both flavors invoke `docker
compose up -d` against the compose file you just copied —
`docker/debian/docker-compose.yaml` or `docker/rhel9/docker-compose.yaml`.

From here on, every `docker compose` command in this runbook reads
`docker/<flavor>/docker-compose.yaml`. The examples that follow all say
`docker/debian/docker-compose.yaml`; substitute
`docker/rhel9/docker-compose.yaml` throughout if you built the RHEL9
flavor.

---

## The four canonical mounts

The container reads its state from four bind mounts. `entrypoint.sh` exits
1 naming each missing mount, so a misconfigured Compose file fails on the
first `docker compose up` rather than silently degrading.

| Path in container | What it is | Secrets-bearing |
|---|---|---|
| `/pgai_agent_kanban` | The kanban install volume — the tree the host-side `install.sh` populated. Read/written by the container. |  |
| `/home/<user>` | User workspace: dev trees for the projects the kanban builds, plus any project source repos the pipeline needs. |  |
| `/claude` | Site-specific payload directory. Customer/site content the kanban chain reads and writes. |  |
| `~/.claude` (inside the container: `/home/kanban/.claude`) | Agent CLI configuration and credentials. **Secrets-bearing.** Never baked into the image. Mount at runtime; treat the host source path with the file permissions you would apply to any credential store. | **Yes** |

The agent-CLI config mount is the only one of the four that carries
secrets. It holds the OAuth token or API key the provider CLI uses to
reach the model. Do not commit its host source directory into a shared
git repository, do not include it in image builds, and do not screenshot
its contents when documenting a deployment.

**Mount 1 must be an install, never a repo checkout.** The host directory
you mount at `/pgai_agent_kanban` must be a directory `install.sh` has
populated — never a raw clone of the kanban repository. The two layouts
differ (an install puts helper scripts at `scripts/…`; a repo checkout
puts them at `team/scripts/…`), and the container-side paths — including
the entrypoint's `exec python3 /pgai_agent_kanban/scripts/pseudocron.py`
and the Compose example's Mount 1 comment ("populate this directory on
the host first with `install.sh`") — assume the install layout. Point
Mount 1 at `$KANBAN_HOST_VOLUME` from Phase 1, not at the source tree
you cloned to build the image.

---

## The one-scheduler rule

The container runs exactly one scheduler: `pseudocron`, invoked as PID 1
by the entrypoint's default mode. **Host cron on the machine that owns the
kanban volume must not also be scheduled to drive the mounted install.**
Two schedulers firing the same wake jobs against the same file-driven
state produce race conditions the kanban's single-threaded design cannot
protect against.

Concretely:

- If you installed the kanban on the host with `install.sh` and the host
  crontab was written, remove those crontab lines before starting the
  container. On the host: `crontab -l > ~/crontab-backup.txt && crontab -r`
  (back up first, then remove).
- If you plan the container path from the start, pass `--no-system-cron`
  to `install.sh` (the pseudocron config is still written; it is inert
  until `pseudocron.py` runs, which the container will do).
- If both schedulers must coexist for some transitional reason, disable
  the container's pseudocron mode by running the container with a
  non-default entrypoint mode (`shell` or `dashboard`) and drive wake
  jobs from the host crontab. This is not the recommended shape; it is
  documented so an operator mid-migration knows the shape exists.

The kanban chain's docs cover this rule from the pseudocron side in
[pseudocron.md](pseudocron.md). This runbook restates it because the
container path is the most common place operators trip on it.

---

## Phase 1: host-side install onto the volume

Before you build the image, populate the volume the container will mount
at `/pgai_agent_kanban`. `install.sh` runs on the host, writes the kanban
tree into a target directory, and stamps a `VERSION` file. The volume is
whatever directory you choose to mount later; the install is
relocation-safe (`pseudocron.cfg` uses ROOT-RELATIVE commands, so the
in-container path does not have to match the host path).

### Choose the host paths

Edit these three assignments to match your host layout, then paste the
rest of the block verbatim.

```bash
# Host paths — edit these three lines, then paste the rest verbatim.
export KANBAN_HOST_VOLUME=/srv/pgai/kanban_install       # becomes /pgai_agent_kanban in the container
export KANBAN_HOST_HOME=/srv/pgai/operator_home          # becomes /home/operator in the container
export KANBAN_HOST_PAYLOAD=/srv/pgai/claude_payload      # becomes /claude in the container
```

The fourth mount (the agent-CLI config directory) is created when you
first install the provider CLI; it lives under `$HOME/.claude` on the
host by convention and does not need pre-creation here.

### Install with git (normal path)

If the host has `git` and network access to the kanban repository, clone
and run `install.sh`. `install.sh` derives the VERSION from `git describe`
when the source tree is a git checkout.

<!-- doc-lint: skip — install narrative requires a live network clone to a placeholder repository URL, a source tree at /tmp/pgai-agent-kanban-src, and a real KANBAN_HOST_VOLUME target; cannot run verbatim in harness -->
```bash
# Edit this one line to the kanban repository URL you clone from, then
# paste the rest verbatim.
export KANBAN_REPO_URL=git@github.com:your-org/pgai-agent-kanban.git

# Clone the source tree somewhere the host can rebuild from.
git clone "$KANBAN_REPO_URL" /tmp/pgai-agent-kanban-src
cd /tmp/pgai-agent-kanban-src

# Install onto the volume with no host crontab (the container schedules).
PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_HOST_VOLUME" \
    ./install.sh --no-system-cron
```

`--no-system-cron` skips the host crontab write while still installing the
pseudocron config the container will run.

### Install without git (the gitless / zip path)

Locked-down sites often cannot reach the kanban repository from the host
that owns the volume. For those sites, ship the source tree as a zip or
tarball. Because there is no `.git` directory in an unpacked archive,
`install.sh` cannot derive the VERSION from `git describe` — you must
pass `--stamp-version` explicitly with the release string you unpacked:

<!-- doc-lint: skip — gitless install narrative requires a shipped zip archive at a site-specific path, an unpacked source tree at /tmp/pgai-agent-kanban-src, and a real KANBAN_HOST_VOLUME target; cannot run verbatim in harness -->
```bash
# Unpack the shipped archive into a source directory.
unzip /path/to/pgai-agent-kanban-v1.24.0.zip -d /tmp/pgai-agent-kanban-src
cd /tmp/pgai-agent-kanban-src

# Install onto the volume, stamping VERSION verbatim because there is no git.
PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_HOST_VOLUME" \
    ./install.sh --no-system-cron --stamp-version v1.24.0
```

The string you pass to `--stamp-version` is the version the release was
built as; use the value from the archive filename or the release notes.

**Transition note.** The gitless install path currently requires
`--stamp-version` because `VERSION` is not committed in the source tree
of releases prior to the committed-VERSION release. Once `VERSION` is
committed at the repository root (a change tracked separately from this
runbook), `install.sh` will read the value from the file directly and
`--stamp-version` will no longer be required for the gitless path.
Until then, always pass it when installing from a zip.

### Verify the install landed

After either path, the volume directory contains the kanban tree. Spot-
check three things before moving on:

<!-- doc-lint: skip — verification commands require KANBAN_HOST_VOLUME to be exported and point to a live install populated by install.sh; harness runs from an ephemeral environment with no live volume -->
```bash
# 1. The install produced a VERSION file.
cat "$KANBAN_HOST_VOLUME/VERSION"

# 2. shell-env exists (the container's bashrc will source this from the mount).
ls "$KANBAN_HOST_VOLUME/shell-env"

# 3. pseudocron.cfg exists (the container will run pseudocron.py against it).
ls "$KANBAN_HOST_VOLUME/pseudocron.cfg"
```

Edit `$KANBAN_HOST_VOLUME/shell-env` and `$KANBAN_HOST_VOLUME/pseudocron.env`
now, before the container starts, to fill in the values that must be
literal (see [pseudocron.md](pseudocron.md) for the format rules).

### Prepare the site payload and workspace directories

The remaining two mounts must exist on the host before the container
starts; the entrypoint will not create them.

<!-- doc-lint: skip — requires KANBAN_HOST_HOME and KANBAN_HOST_PAYLOAD to be exported with real site-specific host paths; harness runs from an ephemeral environment where these variables are unset -->
```bash
mkdir -p "$KANBAN_HOST_HOME" "$KANBAN_HOST_PAYLOAD"
```

The provider CLI's config directory (the fourth mount source) is created
when you install the CLI. If it does not exist yet, install the CLI on
the host and complete its login flow before continuing. The container
does not perform interactive OAuth.

---

## Phase 2: build the image

Run the per-flavor `cp` + `docker compose build` block from "Choosing a
flavor" above. The rest of this phase uses `docker/debian/...` in its
examples; substitute `docker/rhel9/...` if you picked the RHEL9 flavor —
every subsequent step is otherwise identical.

Build succeeds only if the resulting image ships tmux 3.1 or newer. Both
Dockerfiles contain an explicit `tmux -V` version check as a build layer;
a drift in the Debian base image (`python:3.12-bookworm` regressing tmux
to a version below 3.1) fails the build with a named error rather than
producing an image that would break the dashboard at runtime. The RHEL9
flavor pins tmux to 3.3a via its own source-build step, so the gate
guards against source-URL drift there.

### Edit the Compose file mounts

`docker/debian/docker-compose.yaml` (the copy you made in "Choosing a
flavor" — or `docker/rhel9/docker-compose.yaml` if you picked the RHEL9
flavor) ships with example host paths. Edit the four `volumes:` entries
to match the three host paths you exported earlier plus the agent-CLI
config path on your host:

<!-- doc-lint: skip — interactive editor invocation; harness runs in a non-TTY environment where $EDITOR (or vi fallback) cannot open an interactive session -->
```bash
# From the repository root.
${EDITOR:-vi} docker/debian/docker-compose.yaml
```

The four entries to update:

```yaml
volumes:
  - <KANBAN_HOST_VOLUME>:/pgai_agent_kanban        # Mount 1 — the install
  - <KANBAN_HOST_HOME>:/home/<user>                # Mount 2 — user workspace
  - <KANBAN_HOST_PAYLOAD>:/claude                  # Mount 3 — site payload
  - <HOST_HOME>/.claude:/home/kanban/.claude       # Mount 4 — CLI credentials (secrets-bearing)
```

Substitute the actual values in place of each `<PLACEHOLDER>`. When you
save, the file is ready to run.

---

## Phase 3: compose up

Start the container. On the default entrypoint mode, `pseudocron` runs
as PID 1 and logs to the container's stdout, which `docker logs` reads.

<!-- doc-lint: skip — requires a fully configured docker/debian/docker-compose.yaml (copied from the example and edited with real host paths) and a running Docker daemon; harness cannot establish this state -->
```bash
# From the repository root.
docker compose -f docker/debian/docker-compose.yaml up -d
```

If any of the four mounts is missing, the container exits immediately.
Retrieve the reason from the logs:

<!-- doc-lint: skip — requires a running pgai-kanban service started via docker compose; harness cannot start the service -->
```bash
docker compose -f docker/debian/docker-compose.yaml logs pgai-kanban
```

A missing-mount failure looks like this in the logs:

```
ERROR: entrypoint.sh — required bind mount(s) not found:
  MISSING: /claude
```

Fix the Compose file, then `docker compose up -d` again. There is no
partial-startup state; either all four mounts resolve and pseudocron
starts, or the container exits with the named missing mount.

---

## Phase 4: first-contact verification

Three checks confirm the kanban is alive and the chain is scheduled.

### Attach the dashboard

Open an interactive shell in the running container and start the tmux
dashboard. The `dashboard` entrypoint mode also works, but attaching to
a running pseudocron container preserves the scheduler.

<!-- doc-lint: skip — requires the pgai-kanban service to be running via docker compose; harness cannot start the compose service -->
```bash
docker compose -f docker/debian/docker-compose.yaml exec pgai-kanban bash -lc \
    'bash $PGAI_AGENT_KANBAN_ROOT_PATH/team/scripts/dashboard/create.sh'
```

The `-l` on `bash` sources the container's login environment (via
`docker/bashrc`), which puts `dashboard` and `show` on PATH and gives
you the canonical prompt. From inside the tmux session, `Ctrl-b d`
detaches without stopping the dashboard.

### Run a demo intake

Drop the shipped chomp-man demo requirements into a project the kanban
manages, and confirm PM picks it up on the next wake tick. The demo lives
inside the install tree; the intake script reads from `--project` context.

<!-- doc-lint: skip — requires the pgai-kanban service to be running via docker compose; harness cannot start the compose service -->
```bash
docker compose -f docker/debian/docker-compose.yaml exec pgai-kanban bash -lc \
    '$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/intake.sh --project pgai-agent-kanban \
        $PGAI_AGENT_KANBAN_ROOT_PATH/team/demos/chomp-man-demo/v1.0.0-chomp-man-demo.md'
```

Then watch the dashboard's PM lane, or tail the wake log:

<!-- doc-lint: skip — requires the pgai-kanban service to be running via docker compose; harness cannot start the compose service -->
```bash
docker compose -f docker/debian/docker-compose.yaml exec pgai-kanban bash -lc \
    'tail -f $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-pm.log'
```

Nothing visible happens until the next PM wake tick. On the default
schedule this can be a few minutes; an idle log immediately after intake
is the schedule, not a failure.

### Check the health endpoint

The API server is off by default. If you enabled it in `kanban.cfg`,
the `/health` endpoint reports the loopback-only status:

<!-- doc-lint: skip — requires the pgai-kanban service to be running via docker compose with the API server enabled in kanban.cfg; harness cannot start the compose service -->
```bash
docker compose -f docker/debian/docker-compose.yaml exec pgai-kanban bash -lc \
    'curl -sS http://127.0.0.1:8300/health'
```

The API binds to `127.0.0.1` inside the container. To reach it from the
host, publish the port explicitly in `docker/<flavor>/docker-compose.yaml` (the
Compose file ships with the `ports:` block commented out; loopback-only
publishing is the pattern to use — `- "127.0.0.1:8300:8300"`). See
[operator-api.md](operator-api.md) for the loopback trust boundary.

---

## Phase 5: upgrade

The container never rebuilds itself. Upgrades land on the volume: you
place a new source tree on the host, run `upgrade.sh`, and the mounted
kanban tree updates in place. The container keeps running against the
updated files. Because `upgrade.sh` is a two-phase design that execs into
the dev-tree's own `upgrade.sh --phase2`, the upgrade honors the version
being installed, not the version already installed.

The choice is where to run `upgrade.sh` from — the host, or inside the
container. Either works. The container path is convenient when the host
does not have Python installed; the host path is convenient when you
want to keep the container idle during the upgrade.

### From outside the container (host)

<!-- doc-lint: skip — upgrade narrative requires a pre-existing source tree at /tmp/pgai-agent-kanban-src, a live KANBAN_HOST_VOLUME pointing to a real install, and upgrade.sh; cannot run verbatim in harness -->
```bash
# Unpack or update the source tree the same way you did for install.
cd /tmp/pgai-agent-kanban-src
git pull   # or unzip the new archive

# Run upgrade against the mounted volume. Use --stamp-version on the
# gitless path, exactly as for install.
PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_HOST_VOLUME" \
    ./team/scripts/upgrade.sh --force
```

Add `--stamp-version vX.Y.Z` to the command above if the source tree is
a zip unpack rather than a git checkout, using the version string from
the archive. Add `--no-system-cron` if `install.sh` was originally run
with it and you want the crontab prompt suppressed on upgrade.

### From inside the container

<!-- doc-lint: skip — requires the pgai-kanban service to be running via docker compose and a source tree at /tmp/pgai-agent-kanban-src on the host; harness cannot start the compose service or establish this layout -->
```bash
# Ship the new source tree into the container (or mount it as an
# additional bind mount). This example uses `docker cp` to copy an
# unpacked source tree into /tmp inside the running container.
docker cp /tmp/pgai-agent-kanban-src \
    "$(docker compose -f docker/debian/docker-compose.yaml ps -q pgai-kanban):/tmp/pgai-agent-kanban-src"

# Run upgrade from inside the container against the mounted install.
docker compose -f docker/debian/docker-compose.yaml exec pgai-kanban bash -lc \
    'cd /tmp/pgai-agent-kanban-src && ./team/scripts/upgrade.sh --force'
```

Either path leaves the container running. `upgrade.sh` swaps the files on
the volume in place; pseudocron continues firing between the swap and the
next wake tick. In-flight tasks (tasks already in `WORKING` state when
the swap happens) complete against the new tree.

---

## Phase 6: HALT semantics under a container restart

`docker restart` and `docker compose restart` stop the container and start
it again. That interrupts pseudocron and any wake job it dispatched. The
kanban is designed for exactly this: state lives on disk (the mounted
volume), and the chain resumes from disk on the next scheduler tick.

### What actually happens on restart

1. **Container stop.** Docker sends SIGTERM to PID 1 (pseudocron). If
   pseudocron receives it, it logs a shutdown line and exits cleanly.
   Wake jobs it already dispatched keep running as child processes until
   Docker's stop-timeout expires, then Docker sends SIGKILL. The default
   stop-timeout is 10 seconds; increase it in Compose
   (`stop_grace_period: 60s`) if your chain regularly needs longer to
   drain a wake job.
2. **Container start.** `entrypoint.sh` re-runs the mount checks, exports
   `PGAI_AGENT_KANBAN_ROOT_PATH`, and execs pseudocron again. Pseudocron
   reads the same `pseudocron.cfg` from the volume and resumes firing
   at the same cadence.
3. **In-flight tasks.** Any task whose worker process was killed mid-run
   (SIGKILL during Docker's stop) is not automatically restarted by
   pseudocron. It stays in whatever state its `status.md` records at the
   moment of the kill — usually `WORKING`, with any partial artifacts
   still on disk. The next wake tick for that agent sees a `WORKING`
   task and resumes it: agents are written to treat `WORKING` as
   "either fresh or interrupted; check `status.md` and pick up where
   left off." This is the same recovery path a host-crash restart uses.

### What the operator should check after a restart

Right after `docker compose up -d` (either a fresh start or a restart),
scan for tasks that were mid-flight when the container stopped:

<!-- doc-lint: skip — requires the pgai-kanban service to be running via docker compose after a restart; harness cannot start the compose service -->
```bash
docker compose -f docker/debian/docker-compose.yaml exec pgai-kanban bash -lc \
    'bash $PGAI_AGENT_KANBAN_ROOT_PATH/team/scripts/dashboard/create.sh'
```

Watch the WORKING lanes. Any task that was in flight before the restart
should either advance on the next agent wake or, if the interruption
left it in an ambiguous state the resuming agent cannot reconcile, land
in BLOCKED with a specific reason. If a task stays `WORKING` across
multiple wake ticks without advancing, inspect its `status.md` for the
partial state — a stuck wake lock or an inconsistent worktree is the
typical cause.

Also verify pseudocron actually resumed:

<!-- doc-lint: skip — requires the pgai-kanban service to be running via docker compose; harness cannot start the compose service -->
```bash
docker compose -f docker/debian/docker-compose.yaml exec pgai-kanban bash -lc \
    'tail -n 20 $PGAI_AGENT_KANBAN_ROOT_PATH/logs/pseudocron.log'
```

The tail should show a fresh startup banner (`N jobs loaded`) and a
`fired (minute=NN)` line on the next scheduled tick.

### Global HALT survives a restart

The `HALT` file on the volume is state; it does not vanish on container
stop. If `touch $PGAI_AGENT_KANBAN_ROOT_PATH/HALT` was set before the
restart, the container will start pseudocron cleanly and pseudocron will
refuse to dispatch any wake jobs until the HALT file is removed. This is
the intended behavior: a container restart does not clear a HALT that
the operator or OVERWATCH set. To resume dispatch after intentional
downtime, remove the file:

<!-- doc-lint: skip — requires the pgai-kanban service to be running via docker compose; harness cannot start the compose service -->
```bash
docker compose -f docker/debian/docker-compose.yaml exec pgai-kanban bash -lc \
    'rm -f $PGAI_AGENT_KANBAN_ROOT_PATH/HALT'
```

Per-project HALT files
(`$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT`) and `HALT-AFTER`
tokens behave the same way — they are files on the mounted volume, so
they survive container lifecycle events and take effect on the next
wake tick after the container restarts.

---

## Reference: the four canonical mounts, one more time

Because this is where deployments go wrong. When the container refuses
to start, or the dashboard shows blank, or scheduled jobs never fire,
one of these four is almost always the reason.

| Container path | Host source example | Purpose | Secrets |
|---|---|---|---|
| `/pgai_agent_kanban` | `$KANBAN_HOST_VOLUME` (a directory `install.sh` populated) | The kanban install |  |
| `/home/<user>` | `$KANBAN_HOST_HOME` | Dev trees and project repos |  |
| `/claude` | `$KANBAN_HOST_PAYLOAD` | Site-specific payload |  |
| `/home/kanban/.claude` | `$HOME/.claude` on the host | Agent CLI config and credentials | **Yes — do not commit, do not screenshot** |

---

## Related documents

- [pseudocron.md](pseudocron.md) — the scheduler the container runs.
- [OPERATIONS.md](OPERATIONS.md) — day-to-day operational procedures.
- [operator-api.md](operator-api.md) — the loopback API and its trust boundary.
- [dashboard.md](dashboard.md) — the tmux dashboard the `dashboard` entrypoint mode attaches to.
- [operator-troubleshooting.md](operator-troubleshooting.md) — when something looks wrong.
