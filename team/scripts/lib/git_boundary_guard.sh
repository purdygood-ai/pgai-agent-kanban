#!/usr/bin/env bash
# team/scripts/lib/git_boundary_guard.sh
#
# Guard decision and evidence-recording functions behind the "CM is the
# ONLY toucher of any remote" boundary (team/SOP-core.md, "Git
# Repositories: Roles and Boundaries"). This file holds no entry point of
# its own -- team/scripts/wake/git-guard/git is the executable shim that
# sources this file, resolves the real git binary, and calls
# git_boundary_guard_enforce before handing the command off.
#
# Public API
# ----------
#   git_boundary_guard_enforce [args...]
#       The one function a caller needs. [args...] is the full argument
#       vector the invoking process received after "git", exactly as
#       passed -- global options and all. git accepts a run of global
#       options before the subcommand (-C <path>, -c <name>=<value>,
#       --git-dir=<path>, --work-tree=<path>, --namespace=<ns>,
#       --super-prefix=<path>, --no-pager, --exec-path[=<path>], ...), so
#       this function walks leading option-shaped tokens to find the
#       actual subcommand (fetch, pull, push, status, ...) rather than
#       assuming it is always [args...]'s first token. Options that take
#       a separate value token (-C, -c, --exec-path when given as two
#       words, --git-dir, --work-tree, --namespace, --super-prefix)
#       consume two tokens each unless their value is attached with "="
#       (--git-dir=<path> consumes one); every other leading option-shaped
#       token, recognized boolean or not, consumes exactly one -- an
#       unrecognized leading option is never assumed to swallow a value,
#       which biases the scan toward finding and checking the real verb
#       rather than skipping past it unseen. The first non-option-shaped
#       token found is the subcommand this function enforces on.
#
#       - Any subcommand other than fetch/pull/push (including "no
#         subcommand found" when the scan runs out of tokens) returns 0
#         immediately (nothing to enforce) and records nothing.
#       - fetch/pull/push under role "cm" (case-insensitive, read from
#         PGAI_AGENT_ROLE) returns 0 and records nothing -- CM is exactly
#         the role this boundary exists to let through.
#       - fetch/pull/push whose destination resolves to a filesystem path
#         under $PGAI_AGENT_KANBAN_TEMP_DIR (defaulted by
#         team/scripts/lib/temp.sh's pgai_temp_dir -- SOP-core.md's
#         "Temporary File Convention" -- the single home for that default)
#         returns 0 and records nothing, for every role including a
#         non-CM one. This is the DESTINATION half of the decision, read
#         before the role check below: a test that creates its own bare
#         repository under its own tmp_path and pushes into it is not the
#         hazard this boundary exists to stop -- see
#         _git_boundary_guard_is_fixture_local's own header for exactly
#         how the destination is resolved and what disqualifies it (a URL
#         scheme, scp-like host syntax, or a path outside that root all
#         disqualify, falling through to the refusal below). A destination
#         this check cannot resolve is conservatively NOT exempted -- the
#         refusal below is always the safe default -- EXCEPT the two
#         shapes that prove nothing can be reached at all (the named
#         remote was never configured, or the -C directory has already
#         been torn down) WHEN the repository path itself also
#         canonicalises under $PGAI_AGENT_KANBAN_TEMP_DIR: the real git
#         invocation this shim hands off to would fail identically before
#         touching any network, so those are a non-crossing rather than
#         an unresolved destination -- see
#         _git_boundary_guard_is_unreachable_destination's and
#         _git_boundary_guard_path_under_temp_root's own headers for the
#         reasoning and why the repository path, never the destination
#         token itself, is what this exemption is bound to. The same
#         shapes against a repository OUTSIDE that root are NOT exempted
#         -- a value-taking fetch/pull/push option this file's
#         destination scan does not yet recognize can put an arbitrary
#         non-remote token in the destination position, and a failed
#         probe against that token alone never proves the invocation
#         itself is fixture-local. A probe that fails for any OTHER
#         reason (no real git binary available to probe with, permission
#         denied, not a git repository, ...) stays the original
#         conservative default regardless of where the repository lives.
#       - fetch/pull/push under any other determinable role, past the
#         destination check above, emits the
#         dual-channel refusal on stderr, appends one evidence record, and
#         terminates the calling process with exit 1. It never returns in
#         this case, by design: the caller is a one-shot shim process
#         invoked once per git command, not the agent's own session, so
#         exiting here refuses the one command without touching anything
#         else the agent is doing.
#       - When the role cannot be determined (PGAI_AGENT_ROLE unset or
#         empty) or no evidence sink is configured
#         (PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE unset or empty), the guard
#         fails OPEN: it returns 0 and lets the caller proceed exactly as
#         if this file were not on PATH. A guard that hard-refuses on
#         missing context would wedge unrelated work over a plumbing gap
#         rather than a real boundary violation. When the role is unknown
#         but an evidence file IS configured, the attempt is still
#         recorded (role "unknown") before failing open, because that is
#         the one fail-open case this file can leave a trace for; when no
#         evidence file is configured there is nowhere to record without
#         inventing a path outside the one this guard was given, so
#         nothing is written.
#
# Configuration (read from the environment; neither has a config-file
# form because both are set per-invocation by the wake layer, not tuned
# by an operator):
#   PGAI_AGENT_ROLE                        The invoking role (e.g. "cm",
#                                           "coder"). Compared
#                                           case-insensitively against
#                                           "cm".
#   PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE  Absolute path to the evidence
#                                           file this guard may append to.
#                                           Never written to when unset.
#   PGAI_TASK_ID                           Already exported by the wake
#                                           driver (team/scripts/lib/
#                                           wake_driver_invoke.sh) for the
#                                           task in progress; recorded
#                                           verbatim, or "unknown" when
#                                           absent.
#   PGAI_AGENT_KANBAN_TEMP_DIR              The framework's own throwaway
#                                           root (SOP-core.md's "Temporary
#                                           File Convention"; default owned
#                                           by team/scripts/lib/temp.sh's
#                                           pgai_temp_dir, the single home
#                                           for that value -- this file
#                                           never restates it). A
#                                           fetch/pull/push destination
#                                           that resolves inside this root
#                                           is exempt from the refusal
#                                           below -- see
#                                           _git_boundary_guard_is_fixture_local.
#
# Resolving a bare remote NAME (e.g. "origin") to its configured URL, to
# decide whether that URL is fixture-local, needs a real git binary this
# guard can call without recursing back through the shim that put it on
# PATH. It uses, in order: $_gbg_real_git (a global the calling shim
# already resolved for its own final hand-off -- see team/scripts/wake/
# git-guard/git's own header for how and why), then $GIT_GUARD_REAL_GIT
# (an operator override, or the wake layer's own per-dispatch pin -- see
# team/scripts/lib/wake_guard_wiring.sh). Neither set means the probe is
# skipped and the destination is conservatively NOT exempted.
#
# Evidence format
# ---------------
# One self-contained JSON object per line (JSON Lines, matching the
# on-disk convention team/pgai_agent_kanban/metrics/blocked_ledger.py
# documents) so a crash mid-write can only ever corrupt the file's last
# line:
#   {"timestamp":"2026-09-20T00:12:03Z","role":"coder","task":"CODER-...","argv":["git","fetch","--all"]}
# Recording is best-effort: a missing parent directory is created, and any
# other failure (unwritable path, out of space, ...) is swallowed silently
# -- the refusal or pass-through this file exists to enforce must never
# depend on the evidence sink being writable.
#
# Sourcing
# --------
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   # shellcheck source=lib/git_boundary_guard.sh
#   source "${SCRIPT_DIR}/../lib/git_boundary_guard.sh"

_GIT_BOUNDARY_GUARD_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P)"
# shellcheck source=error_contract.sh
source "${_GIT_BOUNDARY_GUARD_LIB_DIR}/error_contract.sh"

# ---------------------------------------------------------------------------
# _git_boundary_guard_json_escape <string>
# Internal: escapes backslash, double-quote, newline and tab for embedding
# <string> inside a JSON string literal. Not a general JSON encoder -- the
# evidence line's only string payloads are a role name, a task id, and
# argv entries, none of which need more than this.
# ---------------------------------------------------------------------------
_git_boundary_guard_json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

# ---------------------------------------------------------------------------
# _git_boundary_guard_record <evidence_file> <role> <argv...>
# Internal: appends one JSON-Lines evidence record to <evidence_file>.
# Best-effort -- creates the parent directory if missing, and swallows any
# failure (unwritable path, missing evidence_file value, ...) rather than
# letting a recording problem affect the caller's own refusal/pass-through
# decision. Never called with an empty <evidence_file>; callers check that
# before recording.
# ---------------------------------------------------------------------------
_git_boundary_guard_record() {
    local evidence_file="$1"
    local role="$2"
    shift 2

    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)"
    local task_id="${PGAI_TASK_ID:-unknown}"

    local argv_json="[" arg first=1
    for arg in "$@"; do
        if [[ $first -eq 1 ]]; then
            first=0
        else
            argv_json+=","
        fi
        argv_json+="\"$(_git_boundary_guard_json_escape "$arg")\""
    done
    argv_json+="]"

    local line
    line="{\"timestamp\":\"${ts}\",\"role\":\"$(_git_boundary_guard_json_escape "$role")\",\"task\":\"$(_git_boundary_guard_json_escape "$task_id")\",\"argv\":${argv_json}}"

    {
        mkdir -p "$(dirname "$evidence_file")"
        printf '%s\n' "$line" >> "$evidence_file"
    } 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# _git_boundary_guard_find_subcommand <args...>
# Internal: walks leading option-shaped tokens in <args...> (the full
# argument vector received after "git") and prints the first
# non-option-shaped token found -- the git subcommand this invocation
# will run. Prints nothing if every token is option-shaped (no
# subcommand present).
#
# -C, -c, --git-dir, --work-tree, --namespace, --super-prefix and
# --exec-path (when given as two separate words, i.e. with no attached
# "=value") consume the token that follows them as their value, so both
# are skipped. A long option written attached ("--git-dir=<path>") is
# already self-contained and consumes only itself. Every other
# option-shaped token -- a recognized boolean (--no-pager, --paginate,
# -p, --bare, --literal-pathspecs, --no-replace-objects, -P,
# --html-path, ...) or one this scan does not recognize at all --
# consumes only itself. Treating an unrecognized option as if it might
# swallow a value would risk skipping straight over the real
# subcommand unseen; treating it as self-contained instead biases the
# scan toward finding and checking that subcommand, per this guard's
# widen-inspection-never-widen-refusal design.
# ---------------------------------------------------------------------------
_git_boundary_guard_find_subcommand() {
    local tok
    while [[ $# -gt 0 ]]; do
        tok="$1"
        case "$tok" in
            --git-dir=*|--work-tree=*|--namespace=*|--super-prefix=*|--exec-path=*)
                shift
                ;;
            --git-dir|--work-tree|--namespace|--super-prefix|--exec-path|-C|-c)
                shift 2 2>/dev/null || shift
                ;;
            -*)
                shift
                ;;
            *)
                printf '%s' "$tok"
                return 0
                ;;
        esac
    done
    return 0
}

# ---------------------------------------------------------------------------
# _git_boundary_guard_find_repo_path <args...>
# Internal: walks the SAME leading option-shaped tokens
# _git_boundary_guard_find_subcommand does, remembering the value of the
# LAST "-C <path>" seen before the subcommand (git's own -C is cumulative
# and repeatable; taking the last one is the closest single value to "the
# directory git will actually operate in" without re-implementing git's
# full chaining). Prints the empty string when no -C was given -- the
# caller then resolves relative to its own cwd, matching plain git.
# --git-dir/--work-tree are deliberately NOT tracked here: a caller using
# either without -C is rare enough in this tree (grep confirms every
# fetch/pull/push call site the destination check exists for uses -C) that
# treating it as "no -C given" and falling through to the conservative
# not-fixture-local default is an acceptable, documented simplification
# rather than added parsing risk for paths this guard has no test in view
# to exercise.
# ---------------------------------------------------------------------------
_git_boundary_guard_find_repo_path() {
    local tok repo_path=""
    while [[ $# -gt 0 ]]; do
        tok="$1"
        case "$tok" in
            -C)
                shift
                repo_path="${1:-}"
                shift
                ;;
            --git-dir=*|--work-tree=*|--namespace=*|--super-prefix=*|--exec-path=*)
                shift
                ;;
            --git-dir|--work-tree|--namespace|--super-prefix|--exec-path|-c)
                shift 2 2>/dev/null || shift
                ;;
            -*)
                shift
                ;;
            *)
                break
                ;;
        esac
    done
    printf '%s' "$repo_path"
}

# ---------------------------------------------------------------------------
# _git_boundary_guard_find_destination <args...>
# Internal: given the SAME [args...] git_boundary_guard_enforce received,
# returns the token git will resolve the fetch/pull/push remote from --
# the first non-option-shaped argument found AFTER the subcommand itself.
# Prints nothing when no such token is present (a bare "git push" relying
# on the current branch's configured upstream) -- the caller treats an
# empty destination as conservatively NOT fixture-local, since resolving
# "the configured upstream" needs a further probe this guard does not
# make.
#
# A fetch/pull/push option that consumes a SEPARATE value token of its own
# -- --depth, --deepen, -o/--server-option/--push-option, -j/--jobs,
# --upload-pack, --receive-pack, --shallow-since, --shallow-exclude,
# --negotiation-tip, --filter, --refmap, --repo, --exec, and the merge
# options pull inherits (-s/--strategy, -X/--strategy-option,
# -m/--message) -- is recognized here (in both its attached
# "--name=value" form, already self-contained, and its two-word form)
# and skipped along with its value, so neither token is ever read as the
# destination. Before this list existed, "git fetch --depth 5 origin"
# read "5" as the destination: a value-consuming option's own value is
# essentially never itself a configured remote name, so the
# fixture-local probe below reliably failed with "No such remote" -- a
# shape _git_boundary_guard_is_unreachable_destination treats as proof
# nothing can be reached at all -- and used to exempt the WHOLE
# invocation on that basis, including the real "origin" sitting later in
# the same argv this scan never looked at. That was a false destination
# widening the EXEMPTION, not merely inspection, exactly the failure this
# guard's own widen-inspection-never-widen-refusal design forbids.
# Skipping the known value-consuming options keeps the scan on the actual
# remote token, AND (see _git_boundary_guard_is_fixture_local's own
# header, point 4) the failed-probe exemption itself is now bound to
# repo_path resolving under the throwaway root, so even an option this
# list does not yet name can no longer widen the exemption on its own --
# it can only make this scan report the wrong (non-remote) token as the
# destination, which the destination-vs-throwaway-root check below still
# catches whenever the repository itself is a real one.
#
# The list above is defence in depth, not the fix -- extending it can
# never be complete on its own, since the set of value-taking options is
# git's, it grows, and pull inherits every merge option too. Any OTHER
# fetch/pull/push option that consumes a separate value and is not on it
# is still a residual instance of the same class, closed at its root by
# the repo_path bound described above rather than by enumeration.
# ---------------------------------------------------------------------------
_git_boundary_guard_find_destination() {
    local tok seen_subcommand=0
    while [[ $# -gt 0 ]]; do
        tok="$1"
        if [[ $seen_subcommand -eq 0 ]]; then
            case "$tok" in
                --git-dir=*|--work-tree=*|--namespace=*|--super-prefix=*|--exec-path=*)
                    shift
                    ;;
                --git-dir|--work-tree|--namespace|--super-prefix|--exec-path|-C|-c)
                    shift 2 2>/dev/null || shift
                    ;;
                fetch|pull|push)
                    seen_subcommand=1
                    shift
                    ;;
                *)
                    shift
                    ;;
            esac
            continue
        fi
        case "$tok" in
            --depth=*|--deepen=*|--server-option=*|--push-option=*|--jobs=*|--upload-pack=*|--receive-pack=*|--shallow-since=*|--shallow-exclude=*|--negotiation-tip=*|--filter=*|--refmap=*|--repo=*|--exec=*|--strategy=*|--strategy-option=*|--message=*)
                shift
                ;;
            --depth|--deepen|--server-option|--push-option|-o|--jobs|-j|--upload-pack|--receive-pack|--shallow-since|--shallow-exclude|--negotiation-tip|--filter|--refmap|--repo|--exec|--strategy|-s|--strategy-option|-X|--message|-m)
                shift 2 2>/dev/null || shift
                ;;
            -*)
                shift
                ;;
            *)
                printf '%s' "$tok"
                return 0
                ;;
        esac
    done
    return 0
}

# ---------------------------------------------------------------------------
# _git_boundary_guard_looks_like_real_remote <value>
# Internal: true when <value> names a network-reachable remote by
# construction -- an explicit URL scheme ("scheme://...") or scp-like
# syntax ("user@host:path", checked only in the segment before the first
# "/" so an "@" inside a throwaway directory name is never mistaken for
# host syntax). Used on both the raw destination token and, after
# resolution, on a remote name's configured URL -- either shape means
# "never fixture-local" regardless of what the destination check below
# would otherwise conclude.
# ---------------------------------------------------------------------------
_git_boundary_guard_looks_like_real_remote() {
    local value="$1"
    case "$value" in
        *://*)
            return 0
            ;;
    esac
    local before_first_slash="${value%%/*}"
    case "$before_first_slash" in
        *@*:*)
            return 0
            ;;
    esac
    return 1
}

# ---------------------------------------------------------------------------
# _git_boundary_guard_resolve_real_git
# Internal: prints the real git binary this guard may call to probe a
# configured remote's URL (never the guard's own shim), or nothing when
# none is available. Precedence: $_gbg_real_git (the global the calling
# shim already resolved for its own hand-off; see this file's
# "Configuration" header) -- $GIT_GUARD_REAL_GIT (an operator override or
# the wake layer's per-dispatch pin; team/scripts/lib/wake_guard_wiring.sh)
# -- neither set prints nothing, and the caller treats that as "cannot
# resolve", never as "assume real git is just `git`" (which would risk
# resolving back through this same shim).
# ---------------------------------------------------------------------------
_git_boundary_guard_resolve_real_git() {
    local candidate="${_gbg_real_git:-${GIT_GUARD_REAL_GIT:-}}"
    [[ -n "$candidate" && -x "$candidate" ]] && printf '%s' "$candidate"
}

# ---------------------------------------------------------------------------
# _git_boundary_guard_is_fixture_local <repo_path> <destination>
# Internal: true when <destination> -- the token
# _git_boundary_guard_find_destination found, or the empty string -- names
# a location this guard treats as a throwaway fixture rather than a real
# remote:
#
#   1. Empty <destination>: never exempted (see that function's header).
#   2. <destination> itself looks like a real remote
#      (_git_boundary_guard_looks_like_real_remote): never exempted.
#   3. <destination> contains a "/" or starts with "." -- git's own
#      remote-vs-path heuristic, a token with a path separator is always a
#      filesystem path, never a configured remote's short name: resolved
#      as that path directly, relative to <repo_path> (or the caller's own
#      cwd when <repo_path> is empty) if it is not already absolute.
#   4. Otherwise <destination> is a configured remote's short name (e.g.
#      "origin"): resolved to its URL via
#      "<real-git> -C <repo_path> remote get-url <destination>", using
#      _git_boundary_guard_resolve_real_git so this probe can never
#      recurse back through the shim that is calling this guard.
#      Resolution failure is conservatively NOT exempted -- UNLESS BOTH
#      of the following hold, in which case it IS exempted:
#        a. the probe's own output proves nothing can be reached at all
#           (see _git_boundary_guard_is_unreachable_destination) -- the
#           remote name was never configured, or the -C directory no
#           longer exists; and
#        b. <repo_path> itself (never a value derived from the
#           destination scan) canonicalises under
#           $PGAI_AGENT_KANBAN_TEMP_DIR (see
#           _git_boundary_guard_path_under_temp_root).
#      Condition (b) is what keeps this exemption bounded: <destination>
#      can be an arbitrary token when a value-taking option
#      _git_boundary_guard_find_destination does not yet recognize sits
#      ahead of the real remote, so a failed probe against that token
#      alone never proves the INVOCATION itself is fixture-local -- only
#      <repo_path>, which is never subject to that same misreading, can.
#      No real git binary to probe with, permission denied, and every
#      other resolution failure still fall through to the conservative
#      default regardless of (b). A resolved URL that itself looks like a
#      real remote is never exempted either, even though the raw
#      <destination> token (the short name) did not.
#
# Whatever path results from (3) or a successful (4) is realpath-resolved
# (falling back to the raw string when realpath cannot run -- e.g. the
# path does not exist yet, which is normal for a push destination) and
# compared against $PGAI_AGENT_KANBAN_TEMP_DIR (see this file's
# "Configuration" header; its default is owned by team/scripts/lib/
# temp.sh's pgai_temp_dir, resolved lazily below rather than restated
# here). A resolved path AT or BELOW that root is fixture-local.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# _git_boundary_guard_is_unreachable_destination <probe_output>
# Internal: true when <probe_output> -- the combined stdout+stderr of a
# FAILED "<real-git> remote get-url <name>" probe -- proves the operation
# can never reach anything, as opposed to merely being a destination this
# guard failed to resolve. Two shapes, both git's own diagnostic text for
# fixture repositories a test builds and tears down without ever wiring a
# real remote:
#
#   - "No such remote" -- <name> was never configured as a remote at all.
#     The real git binary this shim eventually hands the command to would
#     fail with this exact same message before contacting anything, so
#     refusing here and recording it as a boundary attempt is treating a
#     no-op as a crossing.
#   - "cannot change to" -- the "-C <repo_path>" directory named on the
#     command line no longer exists (a fixture already cleaned up after
#     itself). Git never gets far enough to look at a remote at all, real
#     binary or this guard's probe alike.
#
# Every OTHER probe failure (no real git binary was available to probe
# with, permission denied, "<repo_path>" exists but is not a git
# repository, ...) returns false here and falls through to this guard's
# ordinary conservative default -- NOT exempted -- because those leave
# open the possibility a real, reachable remote IS configured and this
# guard simply could not read it.
#
# Matched case-insensitively and loosely -- these are git's own
# human-readable diagnostics, not a machine contract, so Postel's law
# applies: a substring match survives a punctuation or wording tweak
# across git versions that an exact-string match would not. The call site
# forces LC_ALL=C on the probe so the wording being matched here does not
# depend on the invoking shell's locale.
# ---------------------------------------------------------------------------
_git_boundary_guard_is_unreachable_destination() {
    local probe_output_lc
    probe_output_lc="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
    case "$probe_output_lc" in
        *"no such remote"*|*"cannot change to"*)
            return 0
            ;;
    esac
    return 1
}

# ---------------------------------------------------------------------------
# _git_boundary_guard_path_at_or_below <path> <root>
# Internal: true when <path> canonically equals <root> or sits below it.
# Refuses to treat an empty string or the bare filesystem root "/" as a
# usable <root> -- a prefix comparison built from either degenerates into
# a glob that matches every absolute path on the host, which is never a
# legitimate throwaway-root boundary. The single home for that refusal so
# every prefix comparison in this file -- the configured root, the
# widened framework root, and the provenance root the widening decision
# consults -- goes through the same guard rather than each repeating (or
# missing) it.
# ---------------------------------------------------------------------------
_git_boundary_guard_path_at_or_below() {
    local path="$1" root="$2"
    [[ -z "$root" || "$root" == "/" ]] && return 1
    [[ "$path" == "$root" || "$path" == "$root"/* ]]
}

# ---------------------------------------------------------------------------
# _git_boundary_guard_framework_provenance_root
# Internal: echoes the framework's own temp root exactly as
# team/scripts/lib/temp.sh's pgai_configured_temp_root resolves it --
# kanban.cfg's [paths] tmp_root/tmp_subdir, or its self-locate/hard-coded
# fallback -- DELIBERATELY ignoring this invocation's own
# PGAI_AGENT_KANBAN_TEMP_DIR. Sourced lazily, mirroring
# _git_boundary_guard_path_under_temp_root's own lazy-source of temp.sh, so
# a call whose configured root never takes the "projects/<name>" shape
# never pays this sourcing cost either.
#
# See pgai_configured_temp_root's own header for why this, not a value
# derived from the configured root's text, is what the widening decision
# below must consult: a bare two-component SHAPE alone never proves the
# root came from the framework's own team/scripts/lib/temp.sh's
# pgai_project_temp_dir -- an operator's own directory can share that
# shape by coincidence.
# ---------------------------------------------------------------------------
_git_boundary_guard_framework_provenance_root() {
    if ! command -v pgai_configured_temp_root >/dev/null 2>&1; then
        # shellcheck source=temp.sh
        source "${_GIT_BOUNDARY_GUARD_LIB_DIR}/temp.sh"
    fi
    pgai_configured_temp_root
}

# ---------------------------------------------------------------------------
# _git_boundary_guard_path_under_temp_root <path>
# Internal: true when <path> -- resolved with the same realpath-or-raw-
# fallback rule every caller in this file uses (a nonexistent path, normal
# for a push destination that has not been created yet, is compared by
# its literal string rather than failing the check) -- canonicalises AT
# or BELOW $PGAI_AGENT_KANBAN_TEMP_DIR (this file's "Configuration"
# header; default owned by team/scripts/lib/temp.sh's pgai_temp_dir,
# resolved lazily below so the common case -- the wake layer exports the
# env var on every dispatch -- never pays a sourcing cost on this
# latency-sensitive path). An empty <path> is never under the root, and
# neither the configured root nor the widened framework root below is
# ever allowed to serve as an empty or bare "/" prefix -- see
# _git_boundary_guard_path_at_or_below.
#
# The comparison also normalises UP exactly one level, and ONLY when BOTH
# of the following hold:
#
#   1. The resolved root's last two path components are literally
#      "projects/<name>" with nothing else after <name> -- the exact
#      shape team/scripts/lib/temp.sh's pgai_project_temp_dir produces
#      (<framework_root>/projects/<project_name>, never anything deeper).
#      A root that merely CONTAINS a "projects" path component -- anywhere
#      ABOVE the last two components, or with one or more components AFTER
#      <name> (".../projects/<name>/<more>") -- does not match, and the
#      comparison falls back to the root exactly as configured, un-widened.
#
#   2. The candidate parent -- the root with "/projects/<name>" stripped
#      -- itself resolves AT OR BELOW the framework's OWN temp root, as
#      _git_boundary_guard_framework_provenance_root resolves it (kanban.cfg,
#      or its fallback -- never inferred from the configured root's own
#      text). This is the bound that matters: the bare
#      two-component SHAPE in (1) is necessary but not sufficient,
#      because a configured root an operator chose for reasons entirely
#      unrelated to the framework -- "$HOME/projects/pgai", say --
#      can share that shape by coincidence, and stripping it would
#      otherwise hand the exemption to that root's ENTIRE parent
#      directory. Condition (2) additionally rules out the degenerate
#      case where stripping "/projects/<name>" from the configured root
#      yields the EMPTY STRING (a root of exactly "/projects/<name>", one
#      component below the filesystem root) or a bare "/": neither the
#      empty string nor "/" is ever at-or-below a real provenance root
#      (pgai_configured_temp_root never returns either), so the widening
#      is refused and the comparison falls back to the un-widened
#      configured root, exactly as condition (1) failing would.
#
# When both hold, a destination under a DIFFERENT project's subtree, or
# directly under the bare framework root's own shared scratch (pytest/,
# tests/, ...), compares equal rather than failing the prefix check just
# because the ROOT this particular dispatch happened to be given names one
# project's corner of the framework temp tree rather than the tree itself
# -- a fixture under $PGAI_AGENT_KANBAN_TEMP_DIR/pytest/... would
# otherwise be refused whenever the dispatch's own root compared against
# was .../projects/<name>, a sibling path, not an ancestor of the
# fixture. The whole framework temp root is throwaway space by
# construction (SOP-core.md's "Temporary File Convention"), so a
# destination anywhere under it is fixture-local the same way regardless
# of which project subtree the caller's own root variable names for this
# dispatch.
#
# The single home for "is this location fixture-local" so every caller
# that needs the answer -- the resolved destination in
# _git_boundary_guard_is_fixture_local's normal path, and repo_path
# itself in its failed-probe path below -- asks the same question the
# same way rather than each re-deriving the temp root and re-implementing
# the comparison.
# ---------------------------------------------------------------------------
_git_boundary_guard_path_under_temp_root() {
    local path="$1"
    [[ -z "$path" ]] && return 1

    local canon
    canon="$(realpath -m -- "$path" 2>/dev/null)" || canon="$path"

    local temp_root
    if [[ -n "${PGAI_AGENT_KANBAN_TEMP_DIR:-}" ]]; then
        temp_root="${PGAI_AGENT_KANBAN_TEMP_DIR}"
    else
        if ! command -v pgai_temp_dir >/dev/null 2>&1; then
            # shellcheck source=temp.sh
            source "${_GIT_BOUNDARY_GUARD_LIB_DIR}/temp.sh"
        fi
        temp_root="$(pgai_temp_dir)"
    fi
    local canon_temp_root
    canon_temp_root="$(realpath -m -- "$temp_root" 2>/dev/null)" || canon_temp_root="$temp_root"

    # Normalise up to the framework's own temp root ONLY when the shape
    # test (1) matches AND the provenance test (2) confirms the candidate
    # parent is framework-owned territory. See this function's header for
    # why both are required and what each rules out on its own.
    local canon_framework_root="$canon_temp_root"
    case "$canon_temp_root" in
        */projects/*)
            local after_projects="${canon_temp_root##*/projects/}"
            case "$after_projects" in
                */*|"")
                    : # not the bare "<root>/projects/<name>" shape -- leave unwidened
                    ;;
                *)
                    local candidate_parent="${canon_temp_root%/projects/*}"
                    local provenance_root canon_provenance_root
                    provenance_root="$(_git_boundary_guard_framework_provenance_root)"
                    canon_provenance_root="$(realpath -m -- "$provenance_root" 2>/dev/null)" \
                        || canon_provenance_root="$provenance_root"
                    if _git_boundary_guard_path_at_or_below "$candidate_parent" "$canon_provenance_root"; then
                        canon_framework_root="$candidate_parent"
                    fi
                    # else: candidate_parent is not framework-owned (or is
                    # itself empty/"/", the degenerate case a root of exactly
                    # "/projects/<name>" strips down to) -- canon_framework_root
                    # stays un-widened.
                    ;;
            esac
            ;;
    esac

    _git_boundary_guard_path_at_or_below "$canon" "$canon_temp_root" \
        || _git_boundary_guard_path_at_or_below "$canon" "$canon_framework_root"
}

_git_boundary_guard_is_fixture_local() {
    local repo_path="$1" destination="$2"

    [[ -z "$destination" ]] && return 1
    _git_boundary_guard_looks_like_real_remote "$destination" && return 1

    local resolved
    case "$destination" in
        */*|.*)
            resolved="$destination"
            ;;
        *)
            local real_git
            real_git="$(_git_boundary_guard_resolve_real_git)"
            [[ -z "$real_git" ]] && return 1
            local probe_output
            if ! probe_output="$(LC_ALL=C "$real_git" -C "${repo_path:-.}" remote get-url "$destination" 2>&1)"; then
                # A failed probe is not by itself "unresolved, refuse
                # conservatively" -- when the failure proves nothing could
                # ever be reached, the real git invocation this shim hands
                # off to would fail exactly the same way without ever
                # touching a network, so this is a non-crossing rather
                # than an unresolved one, see the helper's own header --
                # BUT that alone is not sufficient to grant the
                # exemption: this branch never established that
                # "$destination" is what git will actually treat as the
                # destination position (a value-taking option this file's
                # skip list does not yet name can put an arbitrary token
                # here, per _git_boundary_guard_find_destination's own
                # header), and the guard's design rule says a false
                # destination may widen INSPECTION but must never widen
                # the EXEMPTION on its own. So the exemption additionally
                # requires repo_path itself -- the one part of this
                # invocation that is never derived from destination
                # position -- to canonicalise under the throwaway root.
                # Every fixture this exemption exists for already lives
                # there by construction; a real dev tree never does.
                if _git_boundary_guard_is_unreachable_destination "$probe_output" \
                    && _git_boundary_guard_path_under_temp_root "${repo_path:-.}"; then
                    return 0
                fi
                return 1
            fi
            resolved="$probe_output"
            [[ -z "$resolved" ]] && return 1
            _git_boundary_guard_looks_like_real_remote "$resolved" && return 1
            ;;
    esac

    if [[ "$resolved" != /* && -n "$repo_path" ]]; then
        resolved="${repo_path%/}/${resolved}"
    fi

    _git_boundary_guard_path_under_temp_root "$resolved"
}

# ---------------------------------------------------------------------------
# git_boundary_guard_enforce [args...]
# See "Public API" above for the full decision table.
# ---------------------------------------------------------------------------
git_boundary_guard_enforce() {
    local subcommand
    subcommand="$(_git_boundary_guard_find_subcommand "$@")"

    case "$subcommand" in
        fetch|pull|push) ;;
        *) return 0 ;;
    esac

    if _git_boundary_guard_is_fixture_local \
        "$(_git_boundary_guard_find_repo_path "$@")" \
        "$(_git_boundary_guard_find_destination "$@")"; then
        return 0
    fi

    local evidence_file="${PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE:-}"
    if [[ -z "$evidence_file" ]]; then
        return 0
    fi

    local role="${PGAI_AGENT_ROLE:-}"
    if [[ -z "$role" ]]; then
        _git_boundary_guard_record "$evidence_file" "unknown" "git" "$@"
        return 0
    fi

    local role_lc
    role_lc="$(printf '%s' "$role" | tr '[:upper:]' '[:lower:]')"

    if [[ "$role_lc" == "cm" ]]; then
        return 0
    fi

    _git_boundary_guard_record "$evidence_file" "$role_lc" "git" "$@"
    # BLOCK-WORTHY: SOP-core.md ("Git Repositories: Roles and Boundaries")
    # reserves every remote verb for CM -- an approval/invariant-by-design boundary.
    pgai_emit_error_contract "git-boundary-guard.non-cm-remote-verb" "role '${role_lc}' invoked 'git ${subcommand}', a remote operation only CM may run" "ask CM to run 'git ${subcommand}' -- CM is the only role permitted to fetch, pull, or push against origin"
    # CENSUS-ANCHOR: git-boundary-guard-01
    exit 1
}
