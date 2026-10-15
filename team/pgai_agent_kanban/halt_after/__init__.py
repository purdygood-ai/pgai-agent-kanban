"""team/pgai_agent_kanban/halt_after — HALT-AFTER soft-halt helper sub-package.

Exposes the public callables for use by the wake script, CM, or any other
caller:

  parse_token(content)                          -> str | None
  check_drain(event, project_root)               -> bool
  promote(project_root, event)                   -> None
  rc_gate.evaluate(project_root, release_state_root, event) -> RcGateResult
  rc_gate.rc_is_open(release_state_root)          -> bool

rc_gate.evaluate is the entry point every promotion call site should use --
it is the RC-close gate that decides whether a satisfied drain condition
(check_drain) is allowed to act (promote) right now, or must be latched
and deferred until the project's Active RC reads "none". check_drain and
promote remain individually exported for direct use in tests and by
rc_gate.py itself, but a new call site outside this package should prefer
rc_gate.evaluate over composing check_drain + promote by hand -- that
composition is exactly what rc_gate.py exists to own in one place.

See module docstrings in token.py, drain.py, promote.py, and rc_gate.py
for full API docs. The CLI entry point lives in __main__.py
(python -m halt_after <project_root>).
"""

from .token import parse_token
from .drain import check_drain
from .promote import promote
from . import rc_gate

__all__ = ["parse_token", "check_drain", "promote", "rc_gate"]
