# pgai-agent-kanban — Docker image Rocky10 - Claude Abliterated

FROM FROM t2fn/claude-abliterated:latest-amd64

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV LC_CTYPE=C.UTF-8
ENV TERM=xterm-256color

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

RUN python3 -m pip install --no-cache-dir \
    'PyYAML>=6.0'  \
    pytest>=7      \
    pytest-cov>=5  

# Install the current official Claude Code and Codex packages.
RUN npm install -g \
        @anthropic-ai/claude-code \
        @openai/codex \
    && npm cache clean --force \
    && claude --version \
    && codex --version

# Non-root user. Fixed UID/GID 1000 matches the most common host operator
# UID on Linux workstations; override at build time with
# --build-arg KANBAN_UID=<uid> --build-arg KANBAN_GID=<gid> if the host
# UID differs, or run with 'docker run --user <uid>:<gid>'. Same pattern as
# the debian flavor so both images run as the same non-root user model.
ARG KANBAN_UID=1000
ARG KANBAN_GID=1000
RUN groupadd --gid "${KANBAN_GID}" kanban \
    && useradd --uid "${KANBAN_UID}" --gid "${KANBAN_GID}" \
               --create-home --shell /bin/bash kanban

WORKDIR /pgai_agent_kanban

RUN mkdir -p /home && chmod 0777 /home

# COPY runs as root so the file is root-owned; USER switches below so the
# non-root kanban user only needs read+exec on the entrypoint, never write.
COPY --chmod=+x docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

USER kanban

