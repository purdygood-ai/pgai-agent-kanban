# team/pgai_agent_kanban/halt_after — HALT-AFTER soft-halt helper sub-package.
#
# Exposes three public callables for use by the wake script or any other caller:
#
#   parse_token(content)             -> str | None
#   check_drain(event, project_root) -> bool
#   promote(project_root, event)     -> None
#
# See module docstrings in token.py, drain.py, and promote.py for full API docs.
# The CLI entry point lives in __main__.py (python -m halt_after <project_root>).

from .token import parse_token
from .drain import check_drain
from .promote import promote

__all__ = ["parse_token", "check_drain", "promote"]
