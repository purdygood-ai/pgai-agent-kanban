"""
pgai_agent_kanban.deep_verify — the DEEP-VERIFY tier's ledger, verdict
classifier, scheduled-dispatch due-check, and pre-release freshness
classifier.

Hosts the append-only per-project deep-verify ledger (ledger.py), the pure
classifier that maps a run-cold-build-tests.sh invocation's exit code and
captured output onto the three SOP verdict classes (classify.py), the pure
due-check the scheduled dispatch path uses to decide whether a weekly
invocation should run the lane now or cost nothing this cycle (schedule.py),
and the pure classifier the pre-release promotion gate uses to decide
whether the ledger's most recent record is fresh and green enough to speak
for the tree state about to be promoted (freshness.py). The umbrella
operator verb that ties the run/schedule/ledger pieces together,
team/scripts/deep-verify.sh, and the promotion gate's own operator verb,
team/scripts/deep-verify-freshness-check.sh, live in team/scripts/ alongside
every other operator entry point.
"""
