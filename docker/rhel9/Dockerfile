# pgai-agent-kanban — Docker image (RHEL9 / UBI9 flavor)
#
# Base: registry.access.redhat.com/ubi9/ubi (Red Hat Universal Base Image 9)
#
# When to use this flavor:
#   RedHat-lineage shops whose security/compliance tooling requires RHEL-lineage
#   images.  The Debian flavor (docker/debian/Dockerfile) is smaller and the
#   default for non-compliance-constrained environments.
#
# Python 3.12:
#   Installed from the RHEL 9 AppStream repository via dnf.  UBI9 AppStream
#   is enabled by default in ubi9/ubi and ships python3.12.
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
#                   UBI9 REPO STATUS: tmux is NOT available in UBI9 base,
#                   AppStream, or EPEL9 as of 2026-07.
#                   RESOLUTION: built from source (tmux 3.3a) using gcc,
#                   make, libevent-devel, ncurses-devel from UBI9 + EPEL.
#                   Build deps are removed after compilation (multi-stage not
#                   used to keep the image layers readable; build-dep removal
#                   via dnf history undo is impractical on UBI9 — instead the
#                   entire build is in one RUN to minimise layer size while
#                   keeping steps legible).
#
#   tput          — 9 invocations: cursor positioning (tput cup/el/ed) in
#                   visibility-window pane loops
#                   RHEL package: ncurses (provides /usr/bin/tput)
#                   Also requires: ncurses-base (terminfo database) and a sane
#                   TERM env var — tput fails with unset/unknown TERM identically
#                   to "command not found"
#
#   watch         — 16 invocations: pane refresh loops for all dashboard windows
#                   RHEL package: procps-ng (provides /usr/bin/watch)
#
#   git           — 88 invocations: git-status.sh, git-recent-tags.sh,
#                   and chain worktree operations throughout scripts/
#                   RHEL package: git (AppStream)
#
#   python3       — 272 invocations: attention.sh (scan_attention.py),
#                   column-render.sh, next-cron-firings.sh, pm-agent chain
#                   RHEL package: python3.12 (AppStream)
#                   Note: python3.12 installs as /usr/bin/python3.12.
#                   We configure alternatives so `python3` resolves to 3.12.
#
#   awk           — 39 invocations: text processing in dashboard renderers
#                   RHEL package: gawk (base; pre-installed)
#
#   jq            — invoked in lib/token_capture.sh and lib/wake_claude_provider.sh
#                   (guarded with 'command -v jq'; optional but used when present)
#                   UBI9 REPO STATUS: jq is NOT in UBI9 base or AppStream.
#                   RHEL EPEL package: jq (Extra Packages for Enterprise Linux 9)
#                   EPEL is enabled in the first RUN layer of this image.
#                   If your security policy forbids EPEL, remove jq from the
#                   package install below; the dashboard degrades gracefully
#                   (token_capture.sh / wake_claude_provider.sh guard with
#                   'command -v jq' and skip silently when jq is absent).
#
#   Standard coreutils (sed, head, tail, cut, sort, wc, date, cat, find):
#                   Pre-installed in ubi9/ubi base image.
#
# OPERATOR TOOLS (not required by chain; included for interactive shell work):
#
#   vim-minimal   — RHEL package: vim-minimal (base; pre-installed)
#   less          — RHEL package: less (base; pre-installed)
#   curl          — RHEL package: curl (base; pre-installed)
#
#   column        — RHEL package: util-linux-core (pre-installed)
#                   Note: the `column` unix utility is NOT invoked by the
#                   dashboard chain; "column renderer" refers to the kanban's
#                   own team/scripts/dashboard/column-render.sh script.
#                   Included per the RHEL-column requirement; util-linux-core
#                   is already present in the UBI9 base image.
#
#   tree          — UBI9 REPO STATUS: tree is NOT available in UBI9 base,
#                   AppStream, or EPEL9 as of 2026-07.
#                   RESOLUTION: built from source (tree 2.1.1) using gcc and
#                   make from UBI9. tree is NOT invoked by the dashboard chain;
#                   it is an operator convenience tool included per the
#                   RHEL-column requirement. The source tarball is fetched from
#                   the upstream GitHub archive mirror.
#
# RHEL COLUMN vs DEBIAN COLUMN — package mapping table:
#
#   Command/need     Debian package     RHEL package / resolution
#   ─────────────    ──────────────     ─────────────────────────
#   tmux             tmux               SOURCE BUILD (not in repos; see above)
#   tput/terminfo    ncurses-bin +      ncurses + ncurses-base
#                    ncurses-base
#   watch            procps             procps-ng
#   column utility   util-linux*        util-linux-core (pre-installed)
#   git              git                git
#   awk              gawk               gawk (pre-installed)
#   jq               jq                 jq (EPEL — see note above)
#   vim (op tool)    vim                vim-minimal (pre-installed)
#   less (op tool)   less               less (pre-installed)
#   curl (op tool)   curl               curl (pre-installed)
#   tree (op tool)   tree               SOURCE BUILD (not in repos; see above)
#
# =============================================================================

FROM registry.access.redhat.com/ubi9/ubi

# ---------------------------------------------------------------------------
# Enable EPEL — required for jq (not available in UBI9 base or AppStream)
# ---------------------------------------------------------------------------
# EPEL (Extra Packages for Enterprise Linux) provides jq.
# We install the epel-release package which adds the EPEL repo.
#
# Security note: EPEL packages are built and signed by the Fedora project.
# If your site policy prohibits third-party repos, remove this RUN block and
# omit jq from the package install below.  The dashboard will continue to
# function; jq usage is guarded with 'command -v jq'.
RUN dnf install -y https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm \
    && dnf clean all

# ---------------------------------------------------------------------------
# System packages — install by RHEL/UBI package name
# ---------------------------------------------------------------------------
#
# Package-to-command mapping (RHEL9 / UBI9):
#   python3.12      → /usr/bin/python3.12 (AppStream)
#   python3.12-pip  → pip3.12 (AppStream)
#   ncurses         → tput, clear (/usr/bin/tput)
#   ncurses-base    → terminfo database (/usr/share/terminfo/*)
#   procps-ng       → watch, ps, free, top
#   git             → git (AppStream)
#   jq              → jq (EPEL — see note above)
#
# Not installed here (pre-installed in ubi9/ubi base):
#   gawk, util-linux-core, vim-minimal, less, curl, ncurses-base
#
RUN dnf install -y \
        python3.12 \
        python3.12-pip \
        ncurses \
        ncurses-base \
        procps-ng \
        git \
        jq \
    && dnf clean all

# ---------------------------------------------------------------------------
# Build tmux from source (tmux 3.3a)
# ---------------------------------------------------------------------------
# tmux is not available in UBI9 base, AppStream, or EPEL9.
# We compile it from the upstream release tarball.
# Build deps (gcc, make, libevent-devel, ncurses-devel, pkg-config) are
# installed and removed in a single RUN layer to keep the image layer clean.
#
# tmux 3.3a is the same version shipped on the Debian base image (Bookworm),
# ensuring parity between flavors.  The ≥3.1 build-time version gate below
# confirms correctness.
#
RUN dnf install -y gcc make libevent-devel ncurses-devel pkg-config \
    && curl -fsSL https://github.com/tmux/tmux/releases/download/3.3a/tmux-3.3a.tar.gz \
           -o /tmp/tmux-3.3a.tar.gz \
    && tar xzf /tmp/tmux-3.3a.tar.gz -C /tmp/ \
    && cd /tmp/tmux-3.3a \
    && ./configure --prefix=/usr \
    && make -j"$(nproc)" \
    && make install \
    && cd / \
    && rm -rf /tmp/tmux-3.3a /tmp/tmux-3.3a.tar.gz \
    && dnf remove -y gcc make libevent-devel ncurses-devel pkg-config \
    && dnf clean all \
    && tmux -V

# ---------------------------------------------------------------------------
# Build tree from source (tree 2.1.1) — operator convenience tool
# ---------------------------------------------------------------------------
# tree is not available in UBI9 base, AppStream, or EPEL9.
# It is NOT invoked by the dashboard chain; included as an operator tool
# per the RHEL-column requirement.
# tree is a simple C program with no external library dependencies.
#
RUN dnf install -y gcc make \
    && curl -fsSL https://github.com/Old-Man-Programmer/tree/archive/refs/tags/2.1.1.tar.gz \
           -o /tmp/tree-2.1.1.tar.gz \
    && tar xzf /tmp/tree-2.1.1.tar.gz -C /tmp/ \
    && cd /tmp/tree-2.1.1 \
    && make prefix=/usr \
    && make install prefix=/usr \
    && cd / \
    && rm -rf /tmp/tree-2.1.1 /tmp/tree-2.1.1.tar.gz \
    && dnf remove -y gcc make \
    && dnf clean all \
    && tree --version

# ---------------------------------------------------------------------------
# Configure python3 → python3.12 via alternatives
# ---------------------------------------------------------------------------
# UBI9 ships python3 pointing at python3.9 by default.  The kanban chain
# invokes python3 throughout; configure alternatives so python3 resolves to
# the AppStream 3.12 we installed above.
RUN alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && alternatives --set python3 /usr/bin/python3.12 \
    && python3 --version

# ---------------------------------------------------------------------------
# Verify tmux >= 3.1 — fail the build on version regression
# ---------------------------------------------------------------------------
# This RUN layer intentionally fails the image build if tmux is below 3.1,
# providing early warning before any container is shipped.
# (tmux is built from source above as 3.3a; this gate confirms correctness.)
#
RUN set -e; \
    tmux_ver="$(tmux -V | awk '{print $2}')"; \
    tmux_major="${tmux_ver%%.*}"; \
    tmux_minor="$(echo "${tmux_ver#*.}" | tr -cd '0-9')"; \
    echo "Detected tmux version: ${tmux_ver} (major=${tmux_major} minor=${tmux_minor})"; \
    if [ "${tmux_major}" -lt 3 ] || { [ "${tmux_major}" -eq 3 ] && [ "${tmux_minor}" -lt 1 ]; }; then \
        echo "ERROR: tmux ${tmux_ver} is below the required floor of 3.1."; \
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
# requirements-test.txt includes '-r requirements.txt', so a single pip3.12
# install against requirements-test.txt installs the complete set.
# Both files must be COPYed first so pip can resolve the -r include.
COPY requirements.txt /tmp/requirements.txt
COPY requirements-test.txt /tmp/requirements-test.txt
RUN pip3.12 install --no-cache-dir -r /tmp/requirements-test.txt

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
