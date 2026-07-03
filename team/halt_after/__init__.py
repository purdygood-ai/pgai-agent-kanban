# team/halt_after — thin delegation shim for the halt_after CLI.
#
# This package exists so the bash wake script can invoke the halt_after
# logic as:
#
#   cd team && python3 -m halt_after <project_root>
#
# All implementation lives in team/pgai_agent_kanban/halt_after/.  This
# package simply re-exports the public API from there.
#
# The fully-qualified import path remains:
#   from team.pgai_agent_kanban.halt_after import parse_token, check_drain, promote

from pgai_agent_kanban.halt_after import parse_token, check_drain, promote

__all__ = ["parse_token", "check_drain", "promote"]
