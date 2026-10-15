"""
pgai_agent_kanban.lib.metrics — the metrics and cost surfaces, one
implementation each.

Every text and JSON view of RC metrics and token costs comes from here, so
the REST routes and the operator scripts print the same bytes:

    surface                         function                        module
    GET /metrics, show-metrics.sh   show_metrics()                  history
    GET /metrics?format=json        metrics_rows_for_project()      rows
    GET /costs, cost-report.sh      cost_report()                   costs
    GET /costs?format=json          costs_rows_for_scope()          rows
    metrics-report.sh --csv         history_csv()                   history
    metrics-report.sh --format jsonl rc_rollups_jsonl()             history
    dashboard/costs.sh              cost_block(), rc_label(),       costs_pane
                                    sum_amounts()
    GET /agent-metrics,             compute_agent_averages()        lib.agent_metrics
    agent-metrics.sh                (rendered by agents.main())     (agents)

A route calls the function in process; a script reaches it through
``pp_run_ops pgai_agent_kanban.lib.metrics <function> ...`` (this
package's ``__main__``). ``compute_agent_averages`` is re-exported from
``pgai_agent_kanban.lib.agent_metrics``, where the averaging arithmetic
lives.

This package is separate from ``pgai_agent_kanban.metrics``, which holds the
release-metrics sidecar ledgers (the blocked-interval ledger).
"""

from ..agent_metrics import compute_agent_averages
from .common import TextResult, color_enabled
from .costs import cost_report
from .costs_pane import cost_block, rc_label, render_cost_block, sum_amounts
from .history import history_csv, rc_rollups_jsonl, read_history_csv, show_metrics
from .rows import costs_rows_for_scope, metrics_rows_for_project

__all__ = [
    "TextResult",
    "color_enabled",
    "compute_agent_averages",
    "cost_block",
    "cost_report",
    "costs_rows_for_scope",
    "history_csv",
    "metrics_rows_for_project",
    "rc_label",
    "rc_rollups_jsonl",
    "read_history_csv",
    "render_cost_block",
    "show_metrics",
    "sum_amounts",
]
