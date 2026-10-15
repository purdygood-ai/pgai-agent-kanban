"""
pgai_agent_kanban.ops — package-level CLI entry point.

Invoked as ``python3 -m pgai_agent_kanban.ops <verb> [args...]``.

Running the *package* __main__ avoids the runpy double-import collision that
occurs when ``python3 -m pgai_agent_kanban.ops.write`` is used: because
ops/__init__.py eagerly imports ops.write to build the public facade, the
submodule is already in sys.modules before runpy tries to execute it as
__main__, which triggers a RuntimeWarning.  Executing the *package* instead
of the submodule does not trigger that check.

Dispatch logic lives in the submodules' ``_cli_main()``: the read verbs
(``ops.read.CLI_VERBS``) go to ops.read._cli_main(), the project registry
verbs (``ops.projects.CLI_VERBS``) to ops.projects._cli_main(), the
seeded-template refresh (``ops.seeded_templates.CLI_VERBS``) to
ops.seeded_templates._cli_main(), the lock
verbs (``ops.locks.CLI_VERBS``) to ops.locks._cli_main(), the verdict verb
(``ops.verdict.CLI_VERBS``) to ops.verdict._cli_main(), the RC-state check
(``ops.rc_state.CLI_VERBS``) to ops.rc_state._cli_main(), every other verb to
ops.write._cli_main().  This module is a thin entry point that
picks one.

Supported verbs (see ops/write.py _cli_main for the full list):
    halt            PROJECT_ROOT
    unhalt          PROJECT_ROOT
    halt_after      PROJECT_ROOT [TOKEN]
    halt_global     KANBAN_ROOT
    unhalt_global   KANBAN_ROOT
    defer           KANBAN_ROOT PROJECT UNTIL
    undefer         KANBAN_ROOT PROJECT
    set_version_ceiling KANBAN_ROOT PROJECT SHOW MINOR MAJOR NO_MINOR NO_MAJOR DRY_RUN
    switch_provider KANBAN_ROOT PROVIDER
    deposit_intake  PROJECT_ROOT FILE_PATH
    close_item      PROJECT_ROOT KEY [STATE] [NOTE] [DRY_RUN]
    wontdo_item     PROJECT_ROOT KEY
    delete_item     PROJECT_ROOT KEY [FORCE]
    reset_item      PROJECT_ROOT KEY [KEEP_ARTIFACTS] [FORCE]
    set_marker      QUEUE_FILE KEY MARKER [IGNORE_CASE]
    remove_marker   QUEUE_FILE KEY [IGNORE_CASE]
    render_marker   QUEUE_FILE KEY MARKER [IGNORE_CASE]
    render_ensure_marker QUEUE_FILE KEY MARKER
    get_markers     QUEUE_FILE KEY [IGNORE_CASE]
    scan_queue      QUEUE_FILE TASKS_ROOT ID_PATTERN LIMIT RULE...

Project registry verbs (see ops/projects.py; options, not positionals):
    create_project  --kanban-root=ROOT --project=NAME [--scripts-dir=DIR] [...]
    add_project     --kanban-root=ROOT --project=NAME [--scripts-dir=DIR]
                    [--priority=P] [--color=C] [--no-migrate] [--dry-run]
    remove_project  --kanban-root=ROOT --project=NAME [--force] [--dry-run]

Seeded-template refresh (see ops/seeded_templates.py; run by install.sh and
upgrade.sh):
    refresh_seeded_templates --kanban-root=ROOT [--scripts-dir=DIR] [--dry-run]

Advisory-lock verbs (see ops/locks.py; options, not positionals):
    list_locks      --kanban-root=ROOT
    release_lock    --kanban-root=ROOT --name=NAME [--force]

Lane-verdict verb (see ops/verdict.py; options, then project names):
    resolve_verdicts --kanban-root=ROOT --render=why|report [--details] [PROJECT...]

RC-state check (see ops/rc_state.py; the output of verify-rc-state.sh):
    verify_rc_state KANBAN_ROOT PROJECT [VERBOSE]

Read verbs (see ops/read.py _cli_main):
    get_task_status KANBAN_ROOT PROJECT KEY [status|readme]
    get_queues      KANBAN_ROOT PROJECT DATA_PROJECT DETAILS
    get_status      KANBAN_ROOT PROJECT NO_COLOR
    get_test_report KANBAN_ROOT PROJECT KEY
    get_task_readme KANBAN_ROOT PROJECT KEY
    list_rejected   KANBAN_ROOT [PROJECT]
"""

import sys

from pgai_agent_kanban.ops import locks as _locks
from pgai_agent_kanban.ops import projects as _projects
from pgai_agent_kanban.ops import rc_state as _rc_state
from pgai_agent_kanban.ops import read as _read
from pgai_agent_kanban.ops import seeded_templates as _seeded_templates
from pgai_agent_kanban.ops import verdict as _verdict
from pgai_agent_kanban.ops import write as _write

if len(sys.argv) > 1 and sys.argv[1] in _read.CLI_VERBS:
    sys.exit(_read._cli_main())
if len(sys.argv) > 1 and sys.argv[1] in _projects.CLI_VERBS:
    sys.exit(_projects._cli_main())
if len(sys.argv) > 1 and sys.argv[1] in _seeded_templates.CLI_VERBS:
    sys.exit(_seeded_templates._cli_main())
if len(sys.argv) > 1 and sys.argv[1] in _locks.CLI_VERBS:
    sys.exit(_locks._cli_main())
if len(sys.argv) > 1 and sys.argv[1] in _verdict.CLI_VERBS:
    sys.exit(_verdict._cli_main())
if len(sys.argv) > 1 and sys.argv[1] in _rc_state.CLI_VERBS:
    sys.exit(_rc_state._cli_main())
sys.exit(_write._cli_main())
