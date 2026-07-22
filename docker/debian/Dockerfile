# pgai-agent-kanban — Docker image
#
# Base: python:3.12-bookworm (Debian Bookworm with Python 3.12 pre-installed)
#
# Why not -slim? The dashboard pipeline requires watch, column (tput cursor ops),
# and the full ncurses terminfo database. The -slim variant strips these.
#
# =============================================================================
# AUDIT — external commands required by the shipping tree (re-run 2026-07-18)
# =============================================================================
#
# Scope: team/scripts/dashboard/** + team/scripts/lib/ (the kanban chain)
# Method: grep for command invocations in .sh files; excluded shell builtins
#         (echo, printf, read, test, [, [[, source) and Python stdlib calls.
#
# CONFIRMED DEPENDENCIES (found in shipping tree):
#
#   tmux          — 110+ invocations: dashboard session create/attach/pane mgmt
#                   Package: tmux
#
#   tput          — 9 invocations: cursor positioning (tput cup/el/ed) in
#                   visibility-window pane loops (create.sh lines 434-443, 949)
#                   Package: ncurses-bin
#                   Also requires: ncurses-base (terminfo database) and a sane
#                   TERM env var — tput fails with unset/unknown TERM identically
#                   to "command not found"
#
#   watch         — 16 invocations: pane refresh loops for all dashboard windows
#                   (create.sh lines 394-466)
#                   Package: procps
#
#   git           — 88 invocations: git-status.sh, git-recent-tags.sh,
#                   and chain worktree operations throughout scripts/
#                   Package: git
#
#   python3       — 272 invocations: attention.sh (scan_attention.py),
#                   column-render.sh (inline python3 - heredoc renderers),
#                   next-cron-firings.sh, and the full pm-agent chain
#                   Package: provided by base image (python:3.12-bookworm)
#
#   awk           — 39 invocations: text processing in dashboard renderers
#                   Package: gawk (pre-installed in bookworm base; included
#                   explicitly for clarity)
#
#   jq            — invoked in lib/token_capture.sh and lib/wake_claude_provider.sh
#                   (guarded with 'command -v jq'; optional but used when present)
#                   Package: jq
#
#   Standard coreutils (sed, head, tail, cut, sort, wc, date, cat, find):
#                   All pre-installed in python:3.12-bookworm base image.
#
# NOT FOUND IN SHIPPING TREE (brief enumeration corrected by fresh audit):
#
#   column (unix utility / bsdextrautils) — NOT invoked. The brief's "100
#     renderer call sites" refers to calls to team/scripts/dashboard/column-render.sh
#     (the kanban's own column renderer), not the `column` unix utility.
#     Excluded from install list.
#
#   tree (unix utility) — NOT invoked in any .sh file in the shipping tree.
#     Excluded from install list.
#
#   curl — NOT directly invoked in the kanban chain scripts. The Claude CLI
#     (installed separately from the claude npm package) uses its own HTTP
#     transport. Included as an operator convenience tool.
#
# OPERATOR TOOLS (not required by chain; useful for interactive shell work):
#   vim, less, curl, bash
#   bash — pre-installed; listed for clarity.
#
# =============================================================================

FROM python:3.12-bookworm

# ---------------------------------------------------------------------------
# System packages — install by Debian package name (not command name)
# ---------------------------------------------------------------------------
#
# Package-to-command mapping (Debian/bookworm):
#   tmux         → tmux
#   ncurses-bin  → tput, clear
#   ncurses-base → terminfo database (/usr/share/terminfo/*)
#   procps       → watch, ps, free, top
#   git          → git
#   gawk         → awk
#   jq           → jq
#   vim          → vim (operator tool)
#   less         → less (operator tool)
#   curl         → curl (operator tool)
#
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tmux \
        ncurses-bin \
        ncurses-base \
        procps \
        git \
        gawk \
        jq \
        vim \
        less \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Verify tmux >= 3.1 — fail the build on base-image drift
# ---------------------------------------------------------------------------
# This RUN layer intentionally fails the image build if tmux regresses
# below 3.1, providing early warning before any container is shipped.
#
# python:3.12-bookworm currently ships tmux 3.3a (as of 2026-07).
# The floor is set to 3.1 to match the dashboard's documented minimum.
#
RUN set -e; \
    tmux_ver="$(tmux -V | awk '{print $2}')"; \
    tmux_major="${tmux_ver%%.*}"; \
    tmux_minor="$(echo "${tmux_ver#*.}" | tr -cd '0-9')"; \
    echo "Detected tmux version: ${tmux_ver} (major=${tmux_major} minor=${tmux_minor})"; \
    if [ "${tmux_major}" -lt 3 ] || { [ "${tmux_major}" -eq 3 ] && [ "${tmux_minor}" -lt 1 ]; }; then \
        echo "ERROR: tmux ${tmux_ver} is below the required floor of 3.1. Base image may have drifted."; \
        exit 1; \
    fi; \
    echo "tmux version check passed: ${tmux_ver} >= 3.1"

# ---------------------------------------------------------------------------
# Python dependencies — runtime AND test/verification toolchain
# ---------------------------------------------------------------------------
# Both requirements files are installed so verification-in-place (agents
# running run-unit-tests.sh / run-integration-tests.sh inside this container)
# never hits a ModuleNotFoundError.  Image size cost is accepted per
# PRIORITY-0010-container-python-deps-complete.
#
# requirements-test.txt includes '-r requirements.txt', so a single pip
# install against requirements-test.txt installs the complete set.
# Both files must be COPYed first so pip can resolve the -r include.
COPY requirements.txt /tmp/requirements.txt
COPY requirements-test.txt /tmp/requirements-test.txt
RUN pip install --no-cache-dir -r /tmp/requirements-test.txt

# ---------------------------------------------------------------------------
# Non-root user
# ---------------------------------------------------------------------------
# We create a 'kanban' user (UID 1000, GID 1000) matching the most common
# operator UID on Linux workstations. If the mounted volumes are owned by a
# different UID, the operator should either:
#   a) rebuild with --build-arg KANBAN_UID=<uid> (see ARG below), or
#   b) run with 'docker run --user <uid>:<gid>'
#
# We cannot use 'dynamic UID matching at runtime' without a writable /etc/passwd,
# which is not available in a minimal image unless gosu/su-exec is installed.
# The chosen approach (fixed UID with build arg override) covers the common
# case without requiring additional binaries or root-at-runtime.
#
ARG KANBAN_UID=1000
ARG KANBAN_GID=1000
RUN groupadd --gid "${KANBAN_GID}" kanban \
    && useradd --uid "${KANBAN_UID}" --gid "${KANBAN_GID}" \
               --create-home --shell /bin/bash kanban

# ---------------------------------------------------------------------------
# Terminal environment
# ---------------------------------------------------------------------------
# tput fails identically with an unset or unknown TERM.
# xterm-256color is widely supported, provided by ncurses-base, and matches
# tmux's internal terminal emulation for pane cursor operations.
ENV TERM=xterm-256color

# ---------------------------------------------------------------------------
# Default working directory
# ---------------------------------------------------------------------------
WORKDIR /pgai_agent_kanban

# ---------------------------------------------------------------------------
# Container entrypoint — install the shared dispatcher script
# ---------------------------------------------------------------------------
# COPY --chmod=+x ensures the script is executable inside the image
# without a separate RUN chmod layer.  The build context is the repo root,
# so the source path is docker/entrypoint.sh (relative to that root).
# The target /entrypoint.sh is the path declared in ENTRYPOINT below.
# USER is set AFTER this COPY so the mode is applied while root owns the
# file; the non-root kanban user needs only read+exec (no write required).
COPY --chmod=+x docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# ---------------------------------------------------------------------------
# Switch to non-root user for runtime
# ---------------------------------------------------------------------------
USER kanban

# ---------------------------------------------------------------------------
# Default command — pseudocron in foreground (PID 1).
# Passed to docker/entrypoint.sh as $1; entrypoint dispatches to the
# pseudocron branch.  Use `command: shell` or `command: dashboard` in
# docker-compose.yaml to override.
# ---------------------------------------------------------------------------
CMD ["pseudocron"]
