"""
pgai_agent_kanban.lint -- the gated runners' aggregate lint bug filer.

Hosts bug_filer.py, the autonomous filer that turns the hygiene lint's
per-instance findings (team/scripts/lint_hygiene.py, merged into the
gated-runner findings file by team/scripts/lib/gated_runner_lint.sh) into
at most one aggregated bug per hygiene class per run. The bash adapter over
it, team/scripts/lib/lint_bug_filer.sh, is what the gated runners
(run-unit-tests.sh, run-integration-tests.sh) actually call.
"""
