#!/usr/bin/env bash
# team/scripts/lib/operator_verb_manifest.sh
#
# The one bash reader for the operator verb manifest
# (team/config/operator_verbs.manifest). Every bash consumer that needs the
# operator verb set reads it through this file instead of hand-parsing the
# manifest a second time -- the sibling Python reader is
# team/pgai_agent_kanban/lib/operator_verb_manifest.py; both implement the
# same parse rule the manifest file's own header documents, independently,
# because bash and Python cannot share a parser module.
#
# Source this file, then call:
#
#   ovm_manifest_path
#       Echoes the default manifest file path, resolved relative to this
#       library's own location (each tree finds itself).
#
#   ovm_verb_names [manifest_path]
#       Echoes one verb name per line, in manifest (file) order.
#
#   ovm_verb_field <verb> <field> [manifest_path]
#       Echoes the raw column value for <verb>'s <field>. <field> is one of:
#       status, script, flags, mutates, idempotence, rest_method,
#       rest_route, rest_body_model, class, schema_ref, impl. Exits 1 with
#       nothing echoed if <verb> is not declared. A "-" placeholder
#       (planned verb / not applicable) is echoed verbatim -- callers that
#       need to distinguish "-" from a real empty value should check the
#       returned string directly. A row written without the trailing impl
#       column echoes an empty impl value; lint_operator_verb_manifest.py
#       reports such a row.
#
#   ovm_verb_status / ovm_verb_script / ovm_verb_flags / ovm_verb_mutates /
#   ovm_verb_idempotence / ovm_verb_rest_method / ovm_verb_rest_route /
#   ovm_verb_rest_body_model / ovm_verb_class / ovm_verb_schema_ref /
#   ovm_verb_impl <verb> [manifest_path]
#       Thin per-field wrappers around ovm_verb_field, for callers that want
#       a named function instead of a string literal field name.
#
# Usage:
#   source "${SCRIPT_DIR}/lib/operator_verb_manifest.sh"
#   ovm_verb_names
#   ovm_verb_flags halt

# ---------------------------------------------------------------------------
# ovm_manifest_path
# ---------------------------------------------------------------------------
ovm_manifest_path() {
  local lib_dir
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # lib/ -> scripts/ -> team/ -> config/operator_verbs.manifest
  echo "${lib_dir}/../../config/operator_verbs.manifest"
}

# ---------------------------------------------------------------------------
# ovm_verb_names [manifest_path]
# ---------------------------------------------------------------------------
ovm_verb_names() {
  local manifest="${1:-$(ovm_manifest_path)}"
  local kind name
  while IFS=$'\t' read -r kind name _rest; do
    [[ "$kind" == "V" ]] || continue
    echo "$name"
  done < "$manifest"
}

# ---------------------------------------------------------------------------
# ovm_verb_field <verb> <field> [manifest_path]
# ---------------------------------------------------------------------------
ovm_verb_field() {
  local target_verb="$1"
  local field="$2"
  local manifest="${3:-$(ovm_manifest_path)}"
  local kind name status script flags mutates idempotence \
        rest_method rest_route rest_body_model class schema_ref impl

  while IFS=$'\t' read -r kind name status script flags mutates idempotence \
                          rest_method rest_route rest_body_model \
                          class schema_ref impl; do
    [[ "$kind" == "V" ]] || continue
    [[ "$name" == "$target_verb" ]] || continue
    case "$field" in
      status)          echo "$status" ;;
      script)          echo "$script" ;;
      flags)           echo "$flags" ;;
      mutates)         echo "$mutates" ;;
      idempotence)     echo "$idempotence" ;;
      rest_method)     echo "$rest_method" ;;
      rest_route)      echo "$rest_route" ;;
      rest_body_model) echo "$rest_body_model" ;;
      class)           echo "$class" ;;
      schema_ref)      echo "$schema_ref" ;;
      impl)            echo "$impl" ;;
      *)
        echo "ovm_verb_field: unknown field '${field}'" >&2
        return 2
        ;;
    esac
    return 0
  done < "$manifest"
  return 1
}

# ---------------------------------------------------------------------------
# Per-field wrappers
# ---------------------------------------------------------------------------
ovm_verb_status()          { ovm_verb_field "$1" status "${2:-}"; }
ovm_verb_script()          { ovm_verb_field "$1" script "${2:-}"; }
ovm_verb_flags()           { ovm_verb_field "$1" flags "${2:-}"; }
ovm_verb_mutates()         { ovm_verb_field "$1" mutates "${2:-}"; }
ovm_verb_idempotence()     { ovm_verb_field "$1" idempotence "${2:-}"; }
ovm_verb_rest_method()     { ovm_verb_field "$1" rest_method "${2:-}"; }
ovm_verb_rest_route()      { ovm_verb_field "$1" rest_route "${2:-}"; }
ovm_verb_rest_body_model() { ovm_verb_field "$1" rest_body_model "${2:-}"; }
ovm_verb_class()           { ovm_verb_field "$1" class "${2:-}"; }
ovm_verb_schema_ref()      { ovm_verb_field "$1" schema_ref "${2:-}"; }
ovm_verb_impl()            { ovm_verb_field "$1" impl "${2:-}"; }
