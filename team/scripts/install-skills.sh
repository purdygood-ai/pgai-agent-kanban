#!/usr/bin/env bash
# team/scripts/install-skills.sh
# Operator verb: copy every skill under a source directory into the provider
# CLI skill homes, or list what that would do.
#
# Usage:
#   install-skills.sh [--dir DIR] [--provider claude|codex|all]
#                     [--claude-home DIR] [--codex-home DIR]
#                     [--dry-run] [--force]
#   install-skills.sh [--dir DIR] [--provider P] [...homes] --list [--json]
#   install-skills.sh --help
#
# A skill is a directory <skill-name>/ holding a SKILL.md (front matter
# "name" equal to the directory name and a "description" under 500
# characters) and optionally scripts/, references/ and assets/.
#
# Options:
#   --dir DIR          Source directory (default: $KANBAN_ROOT/skills, the
#                      staging directory install and upgrade deposit the
#                      shipped skills into). Laid out
#                      <provider>/<skill-name>/SKILL.md, or, with one
#                      --provider, a flat <skill-name>/SKILL.md directory
#                      for that provider.
#   --provider P       claude, codex or all (default: all): which homes to
#                      install into. An install skips a provider whose CLI
#                      binary (claude, codex) is not on PATH, with a
#                      one-line notice on stderr.
#   --claude-home DIR  The claude skill home (default: $CLAUDE_CONFIG_DIR/skills,
#                      or ~/.claude/skills when CLAUDE_CONFIG_DIR is unset).
#   --codex-home DIR   The codex skill home (default: ~/.codex/skills).
#   --list             Print, per provider, each source skill as new, same,
#                      changed or invalid against its home; write nothing.
#                      Lists every requested provider, CLI on PATH or not.
#   --json             With --list: print the list as JSON, the same object
#                      GET /skills returns for the staging directory.
#   --dry-run          Print the install plan; write nothing.
#   --force            Overwrite a changed skill in a provider home.
#   --help, -h         Show this help and exit 0.
#
# Install copies each new skill as a whole directory and leaves a same skill
# untouched. A changed skill (any file differs, not only SKILL.md) is left as
# is and reported, because an operator may have edited their copy; --force
# writes the differing files over it. Nothing is ever removed from a provider
# home, and nothing is written outside the homes named above. An invalid
# skill is reported and never installed.
#
# On the default staging directory each skill is also marked shipped or
# operator-added from $KANBAN_ROOT/.installed-skills, and a missing staging
# directory or record reads as no skills / none shipped, exactly as
# GET /skills reads them.
#
# Listing and installing are pgai_agent_kanban.lib.skills_install's
# list_skills and install_skills, reached through pp_run_ops; GET /skills
# calls list_skills in process, so --list --json and the route print one
# object. A home not given by flag is resolved by the module through
# pgai_agent_kanban.lib.provider_home, the one provider-home rule (the
# agent-wrapper deploy and GET /skills use it too).
#
# Examples:
#   install-skills.sh --list
#   install-skills.sh --dry-run
#   install-skills.sh --provider claude --force
#   install-skills.sh --dir ./my-skills --provider codex
#
# Exit codes:
#   0   Everything installed or was already the same, the list or plan was
#       printed, or --help was shown.
#   1   A skill file could not be written into a provider home (error /
#       message / remedy on stderr); skills written before it stay written.
#   2   Usage error: an unknown argument, an option without its value, an
#       unknown provider, --json without --list, --list with --dry-run or
#       --force, a missing --dir, or a flat --dir without one --provider.
#   3   A changed skill was left as is because --force was not given (the
#       remedy is this command with --force); --dry-run predicts the same.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/env_bootstrap.sh"

_IS_SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
_IS_LIB_DIR="${_IS_SCRIPT_DIR}/lib"

# shellcheck source=lib/operator_args.sh
source "${_IS_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/error_contract.sh
source "${_IS_LIB_DIR}/error_contract.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_IS_LIB_DIR}/pp_run_ops.sh"

# Declared flag vocabulary: drives --help and the unknown-argument check.
OPERATOR_VALID_FLAGS=(dir provider claude-home codex-home list json dry-run force help)

declare -A OPERATOR_FLAG_HELP=(
    [dir]="  --dir DIR          Source directory (default: \$KANBAN_ROOT/skills)"
    [provider]="  --provider P       claude, codex or all (default: all)"
    [claude-home]="  --claude-home DIR  The claude skill home (default: \$CLAUDE_CONFIG_DIR/skills or ~/.claude/skills)"
    [codex-home]="  --codex-home DIR   The codex skill home (default: ~/.codex/skills)"
    [list]="  --list             Print each skill as new, same, changed or invalid per provider; write nothing"
    [json]="  --json             With --list: print the list as JSON (the GET /skills object)"
    [dry-run]="  --dry-run          Print the install plan; write nothing"
    [force]="  --force            Overwrite a changed skill in a provider home"
)

operator_args_parse --extra-value-flags "dir provider claude-home codex-home" "$@"

if argparse_has help; then
    operator_args_render_help_for_flags "install-skills.sh" \
        "Copy every skill (a directory holding a SKILL.md) under a source directory into the provider CLI skill homes." \
        OPERATOR_VALID_FLAGS \
        "" \
        "--dir is laid out <provider>/<skill-name>/SKILL.md, or, with one --provider, a flat" \
        "<skill-name>/SKILL.md directory for that provider. A new skill is copied as a whole" \
        "directory; a changed one is left as is and reported unless --force is given. Nothing is" \
        "removed from a provider home. An install skips a provider whose CLI binary is not on" \
        "PATH, with a one-line notice; --list lists every requested provider." \
        "" \
        "Examples:" \
        "  install-skills.sh --list" \
        "  install-skills.sh --dry-run" \
        "  install-skills.sh --dir ./my-skills --provider codex" \
        "" \
        "Exit codes:" \
        "  0  installed or already the same, list or plan printed, or help shown" \
        "  1  a skill file could not be written (error / message / remedy on stderr)" \
        "  2  usage error (error / message / remedy on stderr)" \
        "  3  a changed skill was left as is; re-run with --force to overwrite it"
    exit 0
fi

_IS_ACCEPTED="Accepted: --dir DIR, --provider claude|codex|all, --claude-home DIR, --codex-home DIR, --list, --json, --dry-run, --force, --help."
_IS_SELF="${_IS_SCRIPT_DIR}/install-skills.sh"

# ---------------------------------------------------------------------------
# _is_refuse_usage <error_key> <message> <remedy>
# Refuse the command line dual-channel on stderr and exit 2, before anything
# is read or written.
# ---------------------------------------------------------------------------
_is_refuse_usage() {
    # BLOCK-WORTHY (docs/blocking-site-census.md): a command line this verb
    # cannot read names no source, provider or mode to act on; guessing one
    # could write skills into the wrong home.
    pgai_emit_error_contract "$1" "$2" "$3"
    # CENSUS-ANCHOR: install-skills-usage-01
    exit 2
}

_is_unknown=""
if ! _is_unknown="$(operator_args_validate_known "install-skills.sh" OPERATOR_VALID_FLAGS 2>&1)"; then
    _is_refuse_usage "install_skills.unknown_argument" \
        "${_is_unknown//$'\n'/; }. ${_IS_ACCEPTED} Nothing was written." \
        "${_IS_SELF} --help"
fi
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    _is_refuse_usage "install_skills.unknown_argument" \
        "install-skills.sh: unknown argument: ${ARGPARSE_POSITIONAL[0]}. ${_IS_ACCEPTED} Nothing was written." \
        "${_IS_SELF} --help"
fi

for _is_flag in dir provider claude-home codex-home; do
    if argparse_missing "${_is_flag}" \
        || { argparse_has "${_is_flag}" && [[ -z "$(operator_args_get "${_is_flag}")" ]]; }; then
        _is_refuse_usage "install_skills.option_value_required" \
            "install-skills.sh: --${_is_flag} needs a value and none was given. Nothing was written." \
            "${_IS_SELF} --list --provider all"
    fi
done

_is_provider="all"
argparse_has provider && _is_provider="$(operator_args_get provider)"
case "${_is_provider}" in
    claude|codex|all) ;;
    *)
        _is_refuse_usage "install_skills.unknown_provider" \
            "install-skills.sh: --provider ${_is_provider} is not one of claude, codex, all. Nothing was written." \
            "${_IS_SELF} --list --provider all"
        ;;
esac

if argparse_has json && ! argparse_has list; then
    _is_refuse_usage "install_skills.json_needs_list" \
        "install-skills.sh: --json formats the --list output and was given without --list. Nothing was written." \
        "${_IS_SELF} --list --json"
fi
if argparse_has list && { argparse_has dry-run || argparse_has force; }; then
    _is_refuse_usage "install_skills.list_is_read_only" \
        "install-skills.sh: --list only reads, so it does not combine with --dry-run or --force. Nothing was written." \
        "${_IS_SELF} --list"
fi

# Source directory and the shipped-skills record. The default staging
# directory is read as GET /skills reads it (missing directory or record
# allowed); an explicit --dir must exist and carries no shipped marks.
_is_module_args=()
if argparse_has dir; then
    _is_module_args+=(--dir "$(operator_args_get dir)")
else
    _is_module_args+=(
        --dir "${PGAI_AGENT_KANBAN_ROOT_PATH}/skills"
        --shipped-manifest "${PGAI_AGENT_KANBAN_ROOT_PATH}/.installed-skills"
        --missing-ok
    )
fi

# Provider homes: an explicit flag is handed to the module; any other home
# the module resolves itself, by the rule GET /skills resolves through.
_is_module_args+=(--provider "${_is_provider}")
for _is_flag in claude-home codex-home; do
    if argparse_has "${_is_flag}"; then
        _is_module_args+=("--${_is_flag}" "$(operator_args_get "${_is_flag}")")
    fi
done

if argparse_has list; then
    _is_list_args=("${_is_module_args[@]}")
    argparse_has json && _is_list_args+=(--json)
    _is_rc=0
    pp_run_ops pgai_agent_kanban.lib.skills_install list_skills "${_is_list_args[@]}" || _is_rc=$?
    exit "${_is_rc}"
fi

# The command a changed-skill refusal tells the operator to re-run (the
# module appends --force): this verb with the source, provider and home
# flags that were given, without --dry-run, so the remedy really overwrites.
_is_rerun="$(printf '%q' "${_IS_SELF}")"
for _is_flag in dir provider claude-home codex-home; do
    if argparse_has "${_is_flag}"; then
        _is_rerun+=" --${_is_flag} $(printf '%q' "$(operator_args_get "${_is_flag}")")"
    fi
done

_is_install_args=("${_is_module_args[@]}" --skip-missing-cli --rerun-command "${_is_rerun}")
argparse_has force && _is_install_args+=(--force)
argparse_has dry-run && _is_install_args+=(--dry-run)

_is_rc=0
pp_run_ops pgai_agent_kanban.lib.skills_install install_skills "${_is_install_args[@]}" || _is_rc=$?
exit "${_is_rc}"
