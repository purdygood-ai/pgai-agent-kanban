# pgai-agent-kanban — Docker image (Debian 12 / Bookworm flavor)
# syntax=docker/dockerfile:1
FROM node:20-bookworm AS nodejs

# Official Python 3.12 image based on Debian 12.
FROM python:3.12-bookworm

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    LC_CTYPE=C.UTF-8 \
    TERM=xterm-256color \
    DEBIAN_FRONTEND=noninteractive

# Install Debian equivalents of the RHEL packages.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        jq \
        less \
        libcbor0.8 \
        libedit2 \
        libfido2-1 \
        locales \
        ncurses-base \
        ncurses-bin \
        ncurses-term \
        openssh-client \
        procps \
        tmux \
        tree \
        vim \
    && rm -rf /var/lib/apt/lists/*

# Copy Node.js 20 and npm from the official Node image.
COPY --from=nodejs /usr/local/bin/node /usr/local/bin/node
COPY --from=nodejs /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN ln -sfn ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sfn ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version \
    && npm --version

# python and python3 already point to Python 3.12 in this base image.
RUN python3 --version \
    && python3 -m pip --version

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

# Non-root user. Fixed UID/GID 1000 matches the most common host operator
# UID on Linux workstations; override at build time with
# --build-arg KANBAN_UID=<uid> --build-arg KANBAN_GID=<gid> if the host
# UID differs, or run with 'docker run --user <uid>:<gid>'.
ARG KANBAN_UID=1000
ARG KANBAN_GID=1000
RUN groupadd --gid "${KANBAN_GID}" kanban \
    && useradd --uid "${KANBAN_UID}" --gid "${KANBAN_GID}" \
               --create-home --shell /bin/bash kanban

WORKDIR /pgai_agent_kanban

RUN mkdir -p /home \
    && chmod 0777 /home

# COPY runs as root so the file is root-owned; USER switches below so the
# non-root kanban user only needs read+exec on the entrypoint, never write.
COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

USER kanban

