"""
pgai_agent_kanban.cm — package-level CLI entry point for the CM verbs.

Invoked as ``python3 -m pgai_agent_kanban.cm <verb> [args...]``, which is how
the CM wrapper scripts reach their verb through ``pp_run_ops``. Running the
package rather than the submodule keeps one copy of the verb's module in
the interpreter: ``python3 -m pgai_agent_kanban.cm.cancel`` would execute a
second, ``__main__`` copy beside the importable one.

Supported verbs:
    cancel_rc   --kanban-root=ROOT --project=NAME --key=vX.Y.Z
                [--yes] [--force] [--repo-root=PATH]     (cm/cancel.py)
    unwind_rc   --kanban-root=ROOT --project=NAME --key=vX.Y.Z
                [--dry-run] [--confirm] [--force]
                [--repo-root=PATH] [--temp-root=PATH]    (cm/unwind.py)
    ship_rc     --kanban-root=ROOT --project=NAME --key=vX.Y.Z
                [--dry-run] [--repo-root=PATH]           (cm/ship.py)
"""

import sys

from pgai_agent_kanban.cm import cancel as _cancel
from pgai_agent_kanban.cm import ship as _ship
from pgai_agent_kanban.cm import unwind as _unwind

_VERBS = {verb: module for module in (_cancel, _unwind, _ship) for verb in module.CLI_VERBS}

if len(sys.argv) > 1 and sys.argv[1] in _VERBS:
    sys.exit(_VERBS[sys.argv[1]]._cli_main())
sys.stderr.write(f"pgai_agent_kanban.cm: VERB must be one of: {', '.join(_VERBS)}\n")
sys.exit(1)
