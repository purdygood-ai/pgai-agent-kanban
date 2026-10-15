"""
pgai_agent_kanban.archive — package-level CLI entry point.

Invoked as ``python3 -m pgai_agent_kanban.archive <verb> [args...]``, which
is how ``team/scripts/archive.sh`` reaches the archive through
``pp_run_ops``. Running the package rather than the submodule keeps one
copy of ``archive.run`` in the interpreter (``python3 -m
pgai_agent_kanban.archive.run`` would execute a second, ``__main__`` copy
beside the importable one).

Supported verbs:
    run_archive     --kanban-root=ROOT --project=NAME [--dry-run] [--count=N]
                    [--restore=ID] [--task=ID]              (archive/run.py)
                    [--accept-chain-lock] [--reconcile-queues]
                    --reconcile-queues moves live queue lines whose item is
                    no longer live into the archive's queue files
                    (archive/reconcile.py); with --dry-run it lists them
    archived_tasks  --tasks-root=DIR ID...                  (archive/lookup.py)
                    prints the IDs that are archived and not live; the wake
                    prerequisite gate reads it through pp_run_ops
"""

import sys

from pgai_agent_kanban.archive import lookup as _lookup
from pgai_agent_kanban.archive import run as _run

_VERBS = {verb: module for module in (_run, _lookup) for verb in module.CLI_VERBS}

if len(sys.argv) > 1 and sys.argv[1] in _VERBS:
    sys.exit(_VERBS[sys.argv[1]]._cli_main())
sys.stderr.write(f"pgai_agent_kanban.archive: VERB must be one of: {', '.join(_VERBS)}\n")
sys.exit(1)
