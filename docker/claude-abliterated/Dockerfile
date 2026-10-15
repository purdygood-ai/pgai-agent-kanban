# pgai-agent-kanban — Docker image Rocky10 - Claude Abliterated
#
# Operator-only flavor: not listed in team/config/container-images.txt, so no
# container-tier harness ever builds it. Build it by hand (docs/docker.md,
# "The claude-abliterated flavor") and run it with container-shell.sh --image-tag.

FROM t2fn/claude-abliterated:latest-amd64

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV LC_CTYPE=C.UTF-8
ENV TERM=xterm-256color

# Pin the build to root explicitly. This flavor's base image is third-party
# and upstream owns its default user -- unlike the debian and rocky9 base
# images this repo builds FROM directly, a future upstream publish could
# ship a non-root default and silently break every install step below.
USER root

# install rpms
RUN dnf module enable -y nodejs:20 \
    && dnf install -y     \
    git                   \
    glibc-langpack-en     \
    jq                    \
    ncurses               \
    ncurses-base          \
    nodejs                \
    npm                   \
    procps-ng             \
    python3.12-devel      \
    python3.12-pip        \
    tmux                  \
    tree                  \
    vim-enhanced          \
    && dnf clean all

RUN alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && alternatives --set python3 /usr/bin/python3.12 \
    && python3 --version

# Python packages come from the repository's own requirements files, the one
# dependency list shared with the host venv. The build context is the repo
# root; requirements-test.txt pulls in requirements.txt with "-r", resolved
# beside it, so both files are copied into the same directory.
WORKDIR /opt/requirements
COPY requirements.txt requirements-test.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements-test.txt

# The pinned CLI versions arrive as build args with no default -- see
# docker/cli-versions.env for the single source of truth and
# team/tests/fixtures/docker_cli_pins.py for the reader that turns it into
# --build-arg flags for the build invocation. A default value here would
# recreate the unpinned-build failure mode this guard exists to close.
ARG CLAUDE_CODE_VERSION
ARG CODEX_VERSION

# Fail fast, before the npm download, when either pinned version is
# missing -- an unpinned build must never install a floating "latest"
# silently. ${VAR:?message} is POSIX shell: it prints "VAR: message" to
# stderr and exits non-zero when VAR is unset or empty.
RUN : "${CLAUDE_CODE_VERSION:?CLAUDE_CODE_VERSION build-arg is required, e.g. --build-arg CLAUDE_CODE_VERSION=2.1.296 -- refusing to install an unpinned claude-code}" \
    && : "${CODEX_VERSION:?CODEX_VERSION build-arg is required, e.g. --build-arg CODEX_VERSION=0.162.1 -- refusing to install an unpinned codex}"

# Install the pinned Claude Code and Codex packages.
RUN npm install -g \
        @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
        @openai/codex@${CODEX_VERSION} \
    && npm cache clean --force

# Smoke-check each CLI in its own layer, separate from the install above --
# npm reporting "added N packages" is not proof the launcher actually runs,
# so a broken platform binary must fail here rather than be masked by the
# install layer's success.
RUN claude --version \
    && codex --version

# Adopt-and-rename user block. UID/GID 1000 are fixed for this flavor --
# not a build-time preference like the debian and rocky9 flavors' overridable
# KANBAN_UID/KANBAN_GID build-args -- because the kanban launcher
# (team/scripts/container-shell.sh) bind-mounts the operator's home and the
# kanban root at matching host and container paths. Whatever this
# third-party base image left at uid/gid 1000 on arrival, the image must end
# up owning exactly those ids: reconcile rather than collide. Handles all
# three arrival states and is idempotent -- rerunning this block once it has
# already resolved to kanban:kanban at 1000:1000 is a no-op.
RUN existing_group="$(getent group 1000 | cut -d: -f1)"; \
    if [ -z "${existing_group}" ]; then \
        groupadd --gid 1000 kanban; \
    elif [ "${existing_group}" != "kanban" ]; then \
        groupmod --new-name kanban "${existing_group}"; \
    fi; \
    existing_user="$(getent passwd 1000 | cut -d: -f1)"; \
    if [ -z "${existing_user}" ]; then \
        useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash kanban; \
    elif [ "${existing_user}" != "kanban" ]; then \
        usermod --login kanban --gid 1000 --home /home/kanban --move-home "${existing_user}"; \
    fi

# Fail loud, naming the ids actually resolved, unless the account this
# block landed on owns exactly uid 1000 / gid 1000 -- the mount contract
# container-shell.sh depends on, never a preference for this flavor.
RUN resolved_uid="$(getent passwd kanban | cut -d: -f3)"; \
    resolved_gid="$(getent passwd kanban | cut -d: -f4)"; \
    if [ "${resolved_uid}" != "1000" ] || [ "${resolved_gid}" != "1000" ]; then \
        echo "kanban account resolved to uid=${resolved_uid:-MISSING} gid=${resolved_gid:-MISSING}, expected 1000:1000 -- container-shell.sh's bind-mount contract requires exactly these ids"; \
        exit 1; \
    fi

WORKDIR /pgai_agent_kanban

RUN mkdir -p /home && chmod 0777 /home

# COPY runs as root so the file is root-owned; USER switches below so the
# non-root kanban user only needs read+exec on the entrypoint, never write.
COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

USER kanban
