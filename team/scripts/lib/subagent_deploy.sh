#!/usr/bin/env bash
# subagent_deploy.sh
# Shared helpers for deploying shipped files to a provider CLI's home: the
# provider-agent wrappers (subagents/) and the skills (skills/). Sourced by
# both install.sh and team/scripts/upgrade.sh so the two entry points share
# one implementation of the provider rule (which providers a step walks),
# provider-directory resolution, directory creation, the wrapper deploy and
# obsolete-wrapper removal, the provider skill install, the read-only
# deployed-copy comparison and the shipped-file record, instead of drifting
# apart with copy-pasted logic.
#
# Functions:
#   provider_home_dir <provider> <kind>    the provider-path rule, asked of
#                                          pgai_agent_kanban.lib.provider_home
#   provider_agents_dir <provider>         agent wrappers (kind "agents")
#   provider_skills_dir <provider>         skills (kind "skills")
#   ensure_deploy_dir <dir> [dry_run] [label]
#   ensure_provider_agents_dir <dir> [dry_run]
#   deploy_drift [--suffix SUFFIX] <source_dir> <target_dir> <name>...
#   deploy_tree_current [--exclude GLOB]... <src_dir> <dst_dir>
#                                          the deployed-copy comparator,
#                                          pgai_agent_kanban.lib.tree_compare
#   subagent_wrapper_drift <source_dir> <agents_dir> <name>...
#   list_shipped_skills <skills_dir>
#   deploy_file_digest <file>
#   deploy_record_write <state_file> <base_dir> <title> <path>...
#   deploy_record_unedited <state_file> <base_dir> <path>
#   deploy_record_skills <state_file>
#   deploy_read_names <file>
#   deploy_flagged_providers <label> <kind> <configured> <claude_flag> <codex_flag>
#   deploy_provider_wrappers <provider> <source_dir> <state_file> <force> <dry_run> <name>...
#   deploy_remove_obsolete_wrappers <state_file> <agents_dir> <provider> <dry_run> <name>...
#   deploy_wrapper_record_write <state_file> <writer> <name>...
#   deploy_provider_skills <provider> <kanban_root> [dry_run]
#
# Depends on the sourcing script defining info()/ok()/warn()/err() (both
# install.sh and upgrade.sh define these with matching signatures:
# info()/ok()/warn() write a narration line to stdout, err() writes to
# stderr). provider_home_dir, deploy_drift, deploy_tree_current,
# list_shipped_skills, deploy_record_unedited, deploy_record_skills and
# deploy_read_names need none of them.
#
# provider_home_dir and deploy_tree_current run the pp_run_ops.sh beside this
# file, in a subshell, so the Python they reach is read from this file's own
# tree whatever pp_run_ops the caller has defined. deploy_provider_wrappers
# loads safe_overwrite.sh from beside this file when the caller has not.

_PGAI_SUBAGENT_DEPLOY_LIB_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# provider_home_dir <provider> <kind>
#
# Prints the directory a provider's CLI reads <kind> from, with no trailing
# newline. The rule lives in one place, pgai_agent_kanban.lib.provider_home
# (also what GET /skills and the skills module resolve through); this asks
# it through the pp_run_ops.sh beside this file:
#   claude  $CLAUDE_CONFIG_DIR/<kind> when CLAUDE_CONFIG_DIR is set and
#           non-empty (the directory Claude Code reads its config from),
#           otherwise $HOME/.claude/<kind>.
#   other   $HOME/.<provider>/<kind> (e.g. $HOME/.codex/skills).
#
# Arguments:
#   provider  Provider name as listed in kanban.cfg [providers] available.
#   kind      Subdirectory the CLI reads: "agents" or "skills".
#
# Returns 0 with the path on stdout; 1 with nothing on stdout and a line on
# stderr when the module cannot be run, so a caller's "$(...)" never hands
# an empty path to a deploy step.
# ---------------------------------------------------------------------------
provider_home_dir() {
  local provider="$1"
  local kind="$2"
  local dir
  if ! dir="$(
    # shellcheck source=pp_run_ops.sh
    source "${_PGAI_SUBAGENT_DEPLOY_LIB_DIR}/pp_run_ops.sh" || exit 1
    pp_run_ops pgai_agent_kanban.lib.provider_home "$provider" "$kind"
  )" || [[ -z "$dir" ]]; then
    printf 'provider_home_dir: could not resolve the %s directory for %s (the pgai_agent_kanban.lib.provider_home module could not be run)\n' \
      "$kind" "$provider" >&2
    return 1
  fi
  printf '%s' "$dir"
}

# provider_agents_dir <provider> — where a provider's agent wrappers deploy.
provider_agents_dir() { provider_home_dir "$1" agents; }

# provider_skills_dir <provider> — the provider skill home, e.g.
# $CLAUDE_CONFIG_DIR/skills (default $HOME/.claude/skills), $HOME/.codex/skills.
provider_skills_dir() { provider_home_dir "$1" skills; }

# ---------------------------------------------------------------------------
# ensure_deploy_dir <dir> [dry_run] [label]
#
# Ensures <dir> exists, creating it with `mkdir -p` when absent, so a deploy
# never silently no-ops just because the target directory has not been
# created yet (by the provider CLI or an earlier install).
#
# Arguments:
#   dir      Absolute path of the deploy target directory (e.g. the output
#            of provider_agents_dir or provider_skills_dir).
#   dry_run  "true" or "false" (default "false"). In dry-run mode a missing
#            directory is reported but not created, so the caller's dry-run
#            reporting can continue into its copy loop without mutating the
#            filesystem.
#   label    What the directory is, for the failure message (default
#            "deploy directory"), e.g. "provider agents directory".
#
# Narration:
#   - Directory already exists: silent.
#   - Directory created:        "created <path>" via ok().
#   - Dry-run, dir missing:     "[dry-run] would create directory: <path>"
#                                via info().
#   - Creation fails (permissions, read-only HOME, missing parent, etc.):
#     "Failed to create <label>: <path>" plus a permissions hint via err();
#     the function returns 1 and the caller must treat that as fatal (exit
#     non-zero) rather than skip the target silently.
#
# Returns:
#   0 — directory exists, was created, or dry-run reported without creating.
#   1 — directory creation failed.
# ---------------------------------------------------------------------------
ensure_deploy_dir() {
  local dir="$1"
  local dry_run="${2:-false}"
  local label="${3:-deploy directory}"

  if [[ -d "$dir" ]]; then
    return 0
  fi

  if [[ "$dry_run" == "true" ]]; then
    info "[dry-run] would create directory: $dir"
    return 0
  fi

  if ! mkdir -p "$dir" 2>/dev/null; then
    err "Failed to create ${label}: $dir"
    err "Check write permissions on the parent directory (HOME may be read-only)."
    return 1
  fi

  ok "created $dir"
  return 0
}

# ensure_provider_agents_dir <agents_dir> [dry_run] — ensure_deploy_dir for
# a provider agents directory.
ensure_provider_agents_dir() { ensure_deploy_dir "$1" "${2:-false}" "provider agents directory"; }

# ---------------------------------------------------------------------------
# deploy_drift [--suffix SUFFIX] <source_dir> <target_dir> <name>...
#
# Read-only comparison of deployed copies against the copies this tree
# ships. For each <name>, compares <source_dir>/<name><SUFFIX> (the shipped
# copy) with <target_dir>/<name><SUFFIX> (the deployed copy) and prints one
# line per entry that is out of date, in argument order:
#
#   missing <name>   the deployed copy does not exist
#   differs <name>   the deployed copy exists but does not match
#
# A shipped file matches when the deployed file is byte-identical. A shipped
# directory (a skill) matches when every directory and file under it exists
# in the deployed copy and every file is byte-identical; a file that exists
# only in the deployed copy is not a difference. Entries that match print
# nothing, so empty output means every named entry is current. A name whose
# shipped source is absent is skipped — there is nothing to compare it
# against.
#
# Never writes, creates, or removes anything: callers use it to report drift
# on a protected path, where nothing under the target may change.
#
# Arguments:
#   --suffix SUFFIX  Appended to each name to form the file name (".md" for
#                    agent wrappers); default empty (skill directories).
#   source_dir       Directory holding the shipped copies.
#   target_dir       Directory holding the deployed copies.
#   name...          Entry names.
#
# Returns:
#   0 — comparison ran (output lists any drift).
#   1 — <target_dir> is not a directory; nothing was compared. Callers
#       decide what an absent directory means (upgrade.sh skips it).
#   2 — usage error: fewer than two positional arguments.
# ---------------------------------------------------------------------------
deploy_drift() {
  local suffix=""
  if [[ "${1-}" == "--suffix" ]]; then
    suffix="${2-}"
    shift
    [[ $# -gt 0 ]] && shift
  fi
  if [[ $# -lt 2 ]]; then
    printf 'deploy_drift: usage: deploy_drift [--suffix SUFFIX] <source_dir> <target_dir> <name>...\n' >&2
    return 2
  fi
  local source_dir="$1"
  local target_dir="$2"
  shift 2

  [[ -d "$target_dir" ]] || return 1

  local name src dst
  for name in "$@"; do
    src="${source_dir}/${name}${suffix}"
    dst="${target_dir}/${name}${suffix}"
    if [[ -d "$src" ]]; then
      if [[ ! -e "$dst" ]]; then
        printf 'missing %s\n' "$name"
      elif ! deploy_tree_current "$src" "$dst"; then
        printf 'differs %s\n' "$name"
      fi
    elif [[ -f "$src" ]]; then
      if [[ ! -f "$dst" ]]; then
        printf 'missing %s\n' "$name"
      elif ! cmp -s "$src" "$dst"; then
        printf 'differs %s\n' "$name"
      fi
    fi
  done
  return 0
}

# ---------------------------------------------------------------------------
# deploy_tree_current [--exclude GLOB]... <src_dir> <dst_dir>
#
# Succeeds when <dst_dir> is a directory holding every directory and a
# byte-identical copy of every file under <src_dir>; a file only in
# <dst_dir> is not a difference. An entry whose name matches an --exclude
# glob (one path component, e.g. __pycache__ or '*.pyc') is not compared.
# The comparison is pgai_agent_kanban.lib.tree_compare, the one deployed-copy
# comparator (the skills module imports the same function), run through the
# pp_run_ops.sh beside this file in a subshell. Never writes anything.
#
# Returns:
#   0 — <dst_dir> is current.
#   1 — anything differs, <dst_dir> is absent, or the comparator could not
#       run (the caller then treats the tree as differing and deploys it).
# ---------------------------------------------------------------------------
deploy_tree_current() {
  local -a opts=()
  while [[ "${1-}" == "--exclude" ]]; do
    opts+=(--exclude "${2-}")
    shift
    [[ $# -gt 0 ]] && shift
  done
  if [[ $# -ne 2 ]]; then
    printf 'deploy_tree_current: usage: deploy_tree_current [--exclude GLOB]... <src_dir> <dst_dir>\n' >&2
    return 1
  fi
  (
    # shellcheck source=pp_run_ops.sh
    source "${_PGAI_SUBAGENT_DEPLOY_LIB_DIR}/pp_run_ops.sh" || exit 1
    pp_run_ops pgai_agent_kanban.lib.tree_compare "${opts[@]+"${opts[@]}"}" -- "$1" "$2" >/dev/null
  ) || return 1
  return 0
}

# subagent_wrapper_drift <source_dir> <agents_dir> <name>... — deploy_drift
# over <name>.md agent wrappers (source subagents/, target the agents dir).
subagent_wrapper_drift() { deploy_drift --suffix .md "$@"; }

# ---------------------------------------------------------------------------
# list_shipped_skills <skills_dir>
#
# Prints one "<provider>/<skill-name>" line for every skill under a
# provider-layout skills directory (<skills_dir>/<provider>/<skill-name>/
# holding a SKILL.md), sorted. Entries whose name starts with "." are
# ignored. Prints nothing when <skills_dir> is absent.
# ---------------------------------------------------------------------------
list_shipped_skills() {
  local skills_dir="$1" skill_file rel
  [[ -d "$skills_dir" ]] || return 0
  for skill_file in "$skills_dir"/[!.]*/[!.]*/SKILL.md; do
    [[ -f "$skill_file" ]] || continue
    rel="${skill_file#"$skills_dir"/}"
    printf '%s\n' "${rel%/SKILL.md}"
  done | LC_ALL=C sort
}

# ---------------------------------------------------------------------------
# deploy_file_digest <file>
#
# Prints the SHA-256 hex digest of <file> (sha256sum, or `shasum -a 256`
# where sha256sum is absent, as on macOS). Returns 1, printing nothing,
# when neither tool is available or the file cannot be read.
# ---------------------------------------------------------------------------
deploy_file_digest() {
  local file="$1" out
  if command -v sha256sum >/dev/null 2>&1; then
    out="$(sha256sum -- "$file" 2>/dev/null)" || return 1
  elif command -v shasum >/dev/null 2>&1; then
    out="$(shasum -a 256 -- "$file" 2>/dev/null)" || return 1
  else
    return 1
  fi
  printf '%s\n' "${out%% *}"
}

# ---------------------------------------------------------------------------
# deploy_record_write <state_file> <base_dir> <title> <path>...
#
# Records the shipped copy of each <path> (relative to <base_dir>; a file or
# a directory) in <state_file>: a "#" comment header naming <title>, then
# one sha256sum-style line per regular file at or under each <path>:
#
#   <sha256>  <path>/<file>
#
# sorted by path. Paths are relative, so the same record checks the deployed
# tree that mirrors <base_dir> (e.g. `cd "$KANBAN_ROOT" && sha256sum -c`),
# and the last field of each line names the provider and skill for
# pgai_agent_kanban.lib.skills_install.read_shipped_manifest. The file is
# written beside itself and renamed into place, so a failure leaves any
# previous record whole.
#
# Returns:
#   0 — record written.
#   1 — a digest could not be computed or the file could not be written;
#       err() names the cause and <state_file> is unchanged.
#   2 — usage error: fewer than three arguments.
# ---------------------------------------------------------------------------
deploy_record_write() {
  if [[ $# -lt 3 ]]; then
    printf 'deploy_record_write: usage: deploy_record_write <state_file> <base_dir> <title> <path>...\n' >&2
    return 2
  fi
  local state_file="$1" base_dir="$2" title="$3"
  shift 3

  local -a files=()
  local path rel
  for path in "$@"; do
    if [[ -d "$base_dir/$path" ]]; then
      while IFS= read -r -d '' rel; do
        files+=("$path/${rel#./}")
      done < <(cd "$base_dir/$path" && find . -type f -print0)
    elif [[ -f "$base_dir/$path" ]]; then
      files+=("$path")
    fi
  done

  local tmp="${state_file}.tmp.$$"
  local digest
  {
    printf '# pgai-agent-kanban: %s\n' "$title"
    printf '# One line per shipped file: <sha256>  <path>, the digest of the copy\n'
    printf '# last shipped. Managed automatically; do not edit by hand. Deleting it\n'
    printf '# is safe: nothing is then provably unedited, so nothing is removed.\n'
    printf '# Last updated: %s\n' "$(date -Iseconds)"
  } > "$tmp" 2>/dev/null || { err "Failed to write $tmp"; rm -f "$tmp"; return 1; }
  if [[ ${#files[@]} -gt 0 ]]; then
    while IFS= read -r rel; do
      if ! digest="$(deploy_file_digest "$base_dir/$rel")"; then
        err "Failed to compute a SHA-256 digest of $base_dir/$rel (needs sha256sum or shasum); $state_file left unchanged."
        rm -f "$tmp"
        return 1
      fi
      printf '%s  %s\n' "$digest" "$rel" >> "$tmp"
    done < <(printf '%s\n' "${files[@]}" | LC_ALL=C sort)
  fi
  if ! mv -f "$tmp" "$state_file" 2>/dev/null; then
    err "Failed to write $state_file"
    rm -f "$tmp"
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# deploy_record_unedited <state_file> <base_dir> <path>
#
# Succeeds only when <base_dir>/<path> is provably the copy last shipped:
# <state_file> records at least one file at or under <path>, every recorded
# file exists under <base_dir> with the recorded digest, and no other file
# exists under <base_dir>/<path>. A caller may then remove it knowing no
# operator edit or addition is lost. Never writes anything.
#
# Returns:
#   0 — unedited copy of what was last shipped.
#   1 — anything else: no record for <path>, a missing, edited or extra
#       file, an absent state file, or no digest tool.
# ---------------------------------------------------------------------------
deploy_record_unedited() {
  local state_file="$1" base_dir="$2" path="$3"
  [[ -f "$state_file" ]] || return 1

  local -A recorded=()
  local digest rel actual
  while read -r digest rel; do
    [[ -z "$digest" || "$digest" == \#* ]] && continue
    [[ "$rel" == "$path" || "$rel" == "$path/"* ]] || continue
    recorded["$rel"]="$digest"
  done < "$state_file"
  [[ ${#recorded[@]} -gt 0 ]] || return 1

  for rel in "${!recorded[@]}"; do
    [[ -f "$base_dir/$rel" ]] || return 1
    actual="$(deploy_file_digest "$base_dir/$rel")" || return 1
    [[ "$actual" == "${recorded[$rel]}" ]] || return 1
  done

  if [[ -d "$base_dir/$path" ]]; then
    while IFS= read -r -d '' rel; do
      [[ -n "${recorded["$path/${rel#./}"]+set}" ]] || return 1
    done < <(cd "$base_dir/$path" && find . \( -type f -o -type l \) -print0)
  fi
  return 0
}

# ---------------------------------------------------------------------------
# deploy_record_skills <state_file>
#
# Prints one "<provider>/<skill-name>" line, sorted and unique, for every
# skill <state_file> records a file of: a "<sha256>  skills/<provider>/
# <skill-name>/<file>" line as deploy_record_write writes it for the skill
# staging record. Comment and blank lines, paths of any other shape, and a
# provider or skill name that is empty or starts with "." are ignored, so a
# printed name never leaves the staging directory. Prints nothing when
# <state_file> is absent. Never writes anything.
# ---------------------------------------------------------------------------
deploy_record_skills() {
  local state_file="$1" digest rel rest provider name
  [[ -f "$state_file" ]] || return 0
  while read -r digest rel; do
    [[ -z "$digest" || "$digest" == \#* ]] && continue
    [[ "$rel" == skills/*/*/* ]] || continue
    rest="${rel#skills/}"
    provider="${rest%%/*}"
    rest="${rest#*/}"
    name="${rest%%/*}"
    [[ -n "$provider" && -n "$name" && "$provider" != .* && "$name" != .* ]] || continue
    printf '%s/%s\n' "$provider" "$name"
  done < "$state_file" | LC_ALL=C sort -u
}

# ---------------------------------------------------------------------------
# deploy_read_names <file>
#
# Prints each name listed in <file> (subagents/MANIFEST.txt, the
# .installed-subagents record), one per line: "#" comments stripped,
# surrounding whitespace trimmed, blank lines skipped. Prints nothing when
# <file> is absent. Never writes anything.
# ---------------------------------------------------------------------------
deploy_read_names() {
  local file="$1" line
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" ]] && printf '%s\n' "$line"
  done < "$file"
  return 0
}

# ---------------------------------------------------------------------------
# deploy_flagged_providers <label> <kind> <configured> <claude_flag> <codex_flag>
#
# The provider rule both install.sh and upgrade.sh apply to a deploy step:
# the step walks <configured> (kanban.cfg [providers] available), and an
# explicit --add-<provider>-<kind> flag is the operator's opt-in for that
# provider, so it is honoured even when <configured> does not list it.
#
# Sets DEPLOY_STEP_PROVIDERS to <configured> plus each flagged provider
# missing from it, and prints one notice per provider added via info(),
# naming it and the reason:
#   "<label>: codex is not listed in kanban.cfg [providers] available
#    (claude); deploying it anyway because --add-codex-agents was passed"
#
# Arguments:
#   label        Notice prefix, e.g. "Provider-agent wrappers".
#   kind         "agents" or "skills" (the flag is --add-<provider>-<kind>).
#   configured   Space-separated configured providers.
#   claude_flag  "true" when --add-claude-<kind> was passed.
#   codex_flag   "true" when --add-codex-<kind> was passed.
# ---------------------------------------------------------------------------
deploy_flagged_providers() {
  local label="$1" kind="$2" configured_list="$3" claude_flag="$4" codex_flag="$5"
  local provider flag listed configured
  DEPLOY_STEP_PROVIDERS="$configured_list"
  for provider in claude codex; do
    case "$provider" in
      claude) flag="$claude_flag" ;;
      codex)  flag="$codex_flag" ;;
    esac
    [[ "$flag" == "true" ]] || continue
    listed=false
    for configured in $configured_list; do
      [[ "$configured" == "$provider" ]] && { listed=true; break; }
    done
    [[ "$listed" == "true" ]] && continue
    info "$label: $provider is not listed in kanban.cfg [providers] available ($configured_list); deploying it anyway because --add-${provider}-${kind} was passed"
    DEPLOY_STEP_PROVIDERS="${DEPLOY_STEP_PROVIDERS:+$DEPLOY_STEP_PROVIDERS }$provider"
  done
  return 0
}

# _deploy_load_safe_overwrite — define safe_overwrite_file from the
# safe_overwrite.sh beside this file when the caller has not; returns 1 when
# it cannot be loaded.
_deploy_load_safe_overwrite() {
  declare -F safe_overwrite_file >/dev/null 2>&1 && return 0
  [[ -f "${_PGAI_SUBAGENT_DEPLOY_LIB_DIR}/safe_overwrite.sh" ]] || return 1
  # shellcheck source=safe_overwrite.sh
  source "${_PGAI_SUBAGENT_DEPLOY_LIB_DIR}/safe_overwrite.sh" || return 1
  declare -F safe_overwrite_file >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# deploy_provider_wrappers <provider> <source_dir> <state_file> <force> <dry_run> <name>...
#
# The provider-agent wrapper deploy for one opted-in provider, shared by
# install.sh and upgrade.sh:
#   1. A provider whose CLI binary is not on PATH is skipped with a warning.
#   2. The agents directory (provider_agents_dir) is created when absent
#      (ensure_deploy_dir; a creation failure is fatal to the caller).
#   3. Each <source_dir>/<name>.md is copied to <agents dir>/<name>.md:
#      <force> "true" copies directly; otherwise safe_overwrite_file
#      (safe_overwrite.sh) skips an identical copy and prompts before
#      writing; <dry_run> "true" only reports. Narrates "deployed N
#      wrapper(s)".
#   4. deploy_remove_obsolete_wrappers removes each wrapper <state_file>
#      records that the current names no longer list.
#
# Returns:
#   0 — deployed.
#   1 — the agents directory could not be resolved or created (err() named
#       it); the caller exits non-zero.
#   3 — skipped: the provider's CLI binary is not on PATH (warned).
# ---------------------------------------------------------------------------
deploy_provider_wrappers() {
  local provider="$1" source_dir="$2" state_file="$3" force="$4" dry_run="$5"
  shift 5
  local agents_dir name src dst count=0 have_prompt=true

  info "Provider-agent wrapper deployment: provider=$provider"
  # Codex CLI may be absent on hosts running claude only, and vice versa.
  if ! command -v "$provider" >/dev/null 2>&1; then
    warn "Skipping $provider: CLI binary not found on PATH"
    return 3
  fi

  agents_dir="$(provider_agents_dir "$provider")" || return 1
  ensure_deploy_dir "$agents_dir" "$dry_run" "provider agents directory" || return 1
  _deploy_load_safe_overwrite || have_prompt=false

  for name in "$@"; do
    src="${source_dir}/${name}.md"
    dst="${agents_dir}/${name}.md"
    if [[ ! -f "$src" ]]; then
      warn "Subagent source missing: $src"
      continue
    fi
    count=$((count + 1))

    if [[ "$dry_run" == "true" ]]; then
      if [[ ! -f "$dst" ]]; then
        info "[dry-run] would prompt to install subagent: $provider/$name"
      elif cmp -s "$src" "$dst" 2>/dev/null; then
        info "[dry-run] subagent unchanged (identical): $provider/$name"
      else
        info "[dry-run] would prompt to overwrite subagent: $provider/$name"
      fi
    elif [[ "$force" == "true" ]]; then
      cp "$src" "$dst"
      ok "Installed/upgraded: $provider/$name"
    elif [[ "$have_prompt" == "true" ]]; then
      safe_overwrite_file "$src" "$dst" || true
    elif [[ ! -e "$dst" ]]; then
      cp "$src" "$dst"
      ok "Installed: $provider/$name"
    elif ! cmp -s "$src" "$dst" 2>/dev/null; then
      warn "Left unchanged: $provider/$name differs from $src (safe_overwrite.sh is missing beside $_PGAI_SUBAGENT_DEPLOY_LIB_DIR/subagent_deploy.sh, so there is no overwrite prompt); re-run with --force to overwrite it"
    fi
  done
  ok "deployed ${count} wrapper(s)"

  deploy_remove_obsolete_wrappers "$state_file" "$agents_dir" "$provider" "$dry_run" "$@"
  ok "Provider-agent wrapper deployment complete for: $provider"
  return 0
}

# ---------------------------------------------------------------------------
# deploy_remove_obsolete_wrappers <state_file> <agents_dir> <provider> <dry_run> <name>...
#
# Removes <agents_dir>/<prev>.md for each name <state_file> records (the
# .installed-subagents written by deploy_wrapper_record_write) that is not
# among the current <name>s, narrating each removal; <dry_run> "true" only
# reports. Only wrappers an earlier install recorded are touched, never a
# file the operator or another tool put there. Nothing happens when
# <state_file> is absent.
# ---------------------------------------------------------------------------
deploy_remove_obsolete_wrappers() {
  local state_file="$1" agents_dir="$2" provider="$3" dry_run="$4"
  shift 4
  local prev current still_shipped obsolete_file
  while IFS= read -r prev; do
    still_shipped=false
    for current in "$@"; do
      [[ "$prev" == "$current" ]] && { still_shipped=true; break; }
    done
    [[ "$still_shipped" == "true" ]] && continue
    obsolete_file="${agents_dir}/${prev}.md"
    [[ -f "$obsolete_file" ]] || continue
    if [[ "$dry_run" == "true" ]]; then
      info "[dry-run] would remove obsolete subagent (no longer in manifest): $provider/$prev"
    else
      info "Removing obsolete subagent (no longer in manifest): $provider/$prev"
      rm -f "$obsolete_file"
    fi
  done < <(deploy_read_names "$state_file")
  return 0
}

# ---------------------------------------------------------------------------
# deploy_wrapper_record_write <state_file> <writer> <name>...
#
# Records the wrapper names just deployed in <state_file>
# (.installed-subagents), under a "#" header naming <writer> (install.sh,
# upgrade.sh), so the next run's deploy_remove_obsolete_wrappers can remove
# a wrapper the manifest drops. Returns 1, after err(), when the file cannot
# be written.
# ---------------------------------------------------------------------------
deploy_wrapper_record_write() {
  local state_file="$1" writer="$2"
  shift 2
  if ! {
    echo "# pgai-agent-kanban: subagents installed by ${writer}"
    echo "# This file is managed automatically. Deleting it is safe — the next"
    echo "# install will rebuild it (and will NOT delete any existing agents,"
    echo "# because it cannot prove ownership). Do not edit by hand."
    echo "# Last updated: $(date -Iseconds)"
    printf '%s\n' "$@"
  } > "$state_file" 2>/dev/null; then
    err "Failed to write $state_file"
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# deploy_provider_skills <provider> <kanban_root> <dry_run>
#
# The provider skill install for one opted-in provider (--add-<provider>-
# skills), shared by install.sh and upgrade.sh: runs the root's installed
# <kanban_root>/scripts/install-skills.sh --provider <provider> --force
# against its staging directory <kanban_root>/skills, with PYTHONPATH cleared
# so the installed package, not a checkout, does the work. A provider whose
# CLI binary is not on PATH is skipped with a warning, as the wrapper deploy
# skips it. <dry_run> "true" reports the command and runs nothing.
#
# Returns, and sets DEPLOY_SKILLS_RC to install-skills.sh's exit code (0
# when it was not run):
#   0 — installed (or, under dry-run, reported).
#   1 — install-skills.sh exited non-zero; warned with the re-run command.
#   3 — skipped: the provider's CLI binary is not on PATH (warned).
#   4 — skipped: install-skills.sh is not installed under <kanban_root>.
# ---------------------------------------------------------------------------
deploy_provider_skills() {
  local provider="$1" kanban_root="$2" dry_run="${3:-false}"
  local verb="$kanban_root/scripts/install-skills.sh"
  local cmd="$verb --provider $provider --force"
  DEPLOY_SKILLS_RC=0

  info "Provider skills: installing the staged skills for provider=$provider (--add-${provider}-skills)"
  if ! command -v "$provider" >/dev/null 2>&1; then
    warn "Skipping $provider skills: CLI binary not found on PATH"
    return 3
  fi
  if [[ "$dry_run" == "true" ]]; then
    info "[dry-run] would run: $cmd"
    return 0
  fi
  if [[ ! -f "$verb" ]]; then
    warn "Skipping $provider skills: $verb is not installed"
    return 4
  fi
  PYTHONPATH="" PGAI_AGENT_KANBAN_ROOT_PATH="$kanban_root" \
    bash "$verb" --provider "$provider" --force || DEPLOY_SKILLS_RC=$?
  if [[ "$DEPLOY_SKILLS_RC" -eq 0 ]]; then
    ok "Provider skills: install complete for $provider"
    return 0
  fi
  warn "Provider skills: install-skills.sh exited $DEPLOY_SKILLS_RC for $provider (see the lines above); the kanban root itself is complete. Re-run: PGAI_AGENT_KANBAN_ROOT_PATH='$kanban_root' $cmd"
  return 1
}
