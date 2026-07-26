"""
pgai_agent_kanban.ops — package-level CLI entry point.

Invoked as ``python3 -m pgai_agent_kanban.ops <verb> [args...]``.

Running the *package* __main__ avoids the runpy double-import collision that
occurs when ``python3 -m pgai_agent_kanban.ops.write`` is used: because
ops/__init__.py eagerly imports ops.write to build the public facade, the
submodule is already in sys.modules before runpy tries to execute it as
__main__, which triggers a RuntimeWarning.  Executing the *package* instead
of the submodule does not trigger that check.

All dispatch logic lives in ops.write._cli_main().  This module is a thin
entry point that imports and delegates to it.

Supported verbs (see ops/write.py _cli_main for the full list):
    halt            PROJECT_ROOT
    unhalt          PROJECT_ROOT
    halt_after      PROJECT_ROOT [TOKEN]
    halt_global     KANBAN_ROOT
    unhalt_global   KANBAN_ROOT
    deposit_intake  PROJECT_ROOT FILE_PATH
    close_item      PROJECT_ROOT KEY [STATE] [NOTE] [DRY_RUN]
    wontdo_item     PROJECT_ROOT KEY
    delete_item     PROJECT_ROOT KEY [FORCE]
    reset_item      PROJECT_ROOT KEY [KEEP_ARTIFACTS] [FORCE]
"""

import sys

from pgai_agent_kanban.ops.write import _cli_main

sys.exit(_cli_main())
