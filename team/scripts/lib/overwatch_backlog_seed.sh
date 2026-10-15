#!/usr/bin/env bash
# team/scripts/lib/overwatch_backlog_seed.sh
#
# Single source of truth for tasks/queues/overwatch_backlog.md's seeded
# content in bash. Three call sites need byte-identical text:
#
#   - create_project in team/pgai_agent_kanban/ops/projects.py (fresh
#     project scaffolding) writes it with queue_file_text, the formatter
#     every seeded *_backlog.md comes from; team/tests/unit/test_ops_projects.py
#     asserts that output equals this function's
#   - team/scripts/migrations/migrate_v2.24.7_to_v2.24.8.sh (upgrade seeder
#     for already-registered projects)
#   - team/scripts/lib/wake_driver.sh's run_project_chain (self-heal: a
#     missing overwatch_backlog.md at wake time is recreated rather than
#     used as a reason to skip the project — see that function's own
#     comment for why the file's absence must never shadow a HALT the
#     overwatch carve-out already let through)
#
# Without this shared function the three paths would maintain their own
# copies of the same header text and drift apart the first time any one of
# them is edited. The file is always empty (no BACKLOG-marked entries) —
# OVERWATCH's resolution stage is triggered by a HALT file, never by a
# queued task — so the only thing worth single-sourcing is the header/
# legend text every other seeded *_backlog.md carries.
#
# Source this file to get overwatch_backlog_seed_content:
#   source "$(dirname "${BASH_SOURCE[0]}")/overwatch_backlog_seed.sh"

# ---------------------------------------------------------------------------
# overwatch_backlog_seed_content
#
# Echoes the full contents of a freshly seeded overwatch_backlog.md: the
# same title/marker-legend shape create_project's queue_file_text produces
# for every other agent's queue file, so a dashboard or
# script that globs tasks/queues/*_backlog.md sees a uniform shape
# regardless of which agent's file it is reading.
# ---------------------------------------------------------------------------
overwatch_backlog_seed_content() {
    cat <<'BLOCK'
# Overwatch Backlog

Tasks ready for the overwatch agent to pull. Markers:

- `[ ]` pending (BACKLOG, ready to pull)
- `[W]` waiting on prerequisites
- `[A]` actively being worked
- `[B]` blocked, needs human intervention
- `[x]` done or won't-do

BLOCK
}
