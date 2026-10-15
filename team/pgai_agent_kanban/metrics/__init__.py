"""
pgai_agent_kanban.metrics — release-metrics sidecar ledgers.

Hosts per-project, per-RC on-disk records the rc-metrics reader consumes
(Wall elapsed already comes from release-state.md / history.csv; this
package holds the sidecars that carry lifecycle state release-state.md's
four fixed fields cannot: the blocked-interval ledger (blocked_ledger.py)
records the minutes an RC spent unable to proceed).
"""
