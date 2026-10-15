#!/usr/bin/env bash
# team/scripts/lib/overwatch_resolve_seed.sh
#
# Reader for the commented `overwatch_resolve` [release] key documentation
# block that ships in every project.cfg — new or upgraded. The block's text
# lives in one file, config/overwatch_resolve_seed.txt (team/config/ in the
# repository), read by both seeding paths:
#
#   - create_project in team/pgai_agent_kanban/ops/projects.py (fresh
#     project.cfg generation; create-project.sh and
#     POST /operations/create-project both call it)
#   - team/scripts/migrations/migrate_v2.22.5_to_v2.23.0.sh (upgrade seeder
#     for already-registered projects), through this function
#
# One file keeps the two paths byte-identical. The wording mirrors
# project.cfg_example's [release] overwatch_resolve section verbatim — that
# file is the operator-facing reference copy; the seed file is the
# machine-seeded copy.
#
# Source this file to get overwatch_resolve_seed_comment_block:
#   source "$(dirname "${BASH_SOURCE[0]}")/overwatch_resolve_seed.sh"

# ---------------------------------------------------------------------------
# overwatch_resolve_seed_comment_block
#
# Echoes the commented documentation block plus the commented default line
# (`# overwatch_resolve = false`) for the [release] section, read from
# config/overwatch_resolve_seed.txt beside this tree's scripts/. Always
# commented — the block never carries a live, uncommented key. Callers
# are responsible for placing the block inside a [release] section.
# Returns 1, naming the path, when the seed file is missing.
# ---------------------------------------------------------------------------
overwatch_resolve_seed_comment_block() {
    local _seed
    _seed="$(dirname "${BASH_SOURCE[0]}")/../../config/overwatch_resolve_seed.txt"
    if [[ ! -f "$_seed" ]]; then
        echo "overwatch_resolve_seed.sh: seed block file not found: ${_seed}" >&2
        return 1
    fi
    cat "$_seed"
}
