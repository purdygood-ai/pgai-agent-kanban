"""
__main__.py — CLI delegation shim for python -m halt_after.

Delegates entirely to team/pgai_agent_kanban/halt_after/__main__.py.
Run from the team/ directory:

    python3 -m halt_after <project_root>

Exit codes: see team/pgai_agent_kanban/halt_after/__main__.py for full docs.
"""

import sys
from pgai_agent_kanban.halt_after.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
