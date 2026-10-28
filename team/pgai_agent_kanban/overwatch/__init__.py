"""
pgai_agent_kanban.overwatch — OVERWATCH-owned autonomous mechanics.

Hosts the resolution matrix and matcher (resolution_matrix.py), the
append-only auto-resolutions ledger (ledger.py), the execution stage
that performs a matrix-prescribed remediation and calls both of these
(resolution_stage.py), and Lane A's Fix-rewrite step (fix_rewrite.py),
which rewrites a bad-fix regression's originating bug into a
single-reading direction before the Lane A recovery playbook's unwind/
cancel/delete/HALT-removal steps run. Those unwind/cancel/delete/HALT-
removal steps, and the single-attempt guard that escalates a repeat HALT
on the same finding class to Lane C instead of re-running the drill, live
in resolution_stage.py alongside the matrix-prescribed execution stage.
The durable ## Block Record a blocked task's status.md carries -- its
shape, closed reason_code and owner_role vocabularies, and the write,
re-block, update and removal entry points -- lives in block_record.py;
what a block site puts in it -- the owner role, the evidence line with its
placeholders, and the worktree-failure and provider-refusal classifications
-- is gathered by block_evidence.py. What block triage may do about a
Block Record -- the action, attempt ceiling, cool-down, backoff,
verification and preconditions per reason_code, decided fail-closed and
without side effects -- is block_classifier.py, driven by the
block-classifier.yaml table beside it. block_triage.py is the executor
that carries a decision out (evidence, Block Record, Triage Note, OVERWATCH
Diagnosis, and the requeue handlers with their verification ledger);
block_requeue.py supplies its commit snapshot, Zero Progress reset and
verifiers; block_gate.py what it reads and saves for a completion-gate
refusal (merge facts, the read-only gate recompute, the dirty-worktree
patch, the empty commits); worktree_residue.py how it finds, judges and
clears the stale worktree behind a failed worktree create;
allowlisted_verbs.py which operator data actions it may run for an agent
that blocked on one (each verb's entry point, target checks and in-process
run, beside the typed-argument validators in block_record.py); and
block_stage_deferral.py how it hands a ship-policy HALT block to
resolution_stage.py and reads back the stage's Decision event. All of them run
git only through readonly_git.py: its allowlist of read-only subcommands
and its two worktree-registration cleanup verbs. transient_backoff.py is the backoff window both
transient-error requeue paths wait out. block_storm.py decides, read-only,
whether a project's human-gated blocks are systemic -- a storm of one
reason_code inside [wake] block_storm_window back from now, or a
reason_code on [wake] systemic_block_reasons -- and so hold the wake's
task dispatch, never longer than [wake] block_hold_cap. inbox.py is the
one reader and writer of a project's operator inbox
(projects/<name>/operator-inbox.md): the questions parked for a human
answer while the chain carries on, with their write, list, answer, close
and summary operations. halt_provenance.py says, read-only, whether the
chain or an operator wrote a project HALT -- from the provenance block the
chain writers put in the HALT body, or for an empty HALT from the
sentinel-writes log -- reading anything unproven as the operator's.
halt_clearing.py says, read-only, whether a chain HALT's cause is resolved
(RC cancelled and rebundled, task reset, trigger 9 re-checked green), the
checks resolution_stage.py runs before it clears that HALT.
decide.py is the decision wake's one pure function: for a new BLOCK, Needs
Human, chain HALT or RC-level failure it returns exactly one outcome -- an
existing operator verb, an operator-inbox question, a runaway stop, or none
-- from the inputs its caller gathered and each classifier row's decision.
decision_executor.py carries that outcome out through the existing operator
writes, backing up what it changes, appending a Decision event and writing
the blocked-ledger pair, under halt_first.py: the HALT-first protocol and
repo lock (the Python counterpart of overwatch_halt_first_fix) and the
backup layout overwatch-revert.sh restores. decision_wake.py is the entry
point the sweep's block-triage check runs per project: it finds every stop
(the project's HALT, every BLOCKED task, every BACKLOG or WAITING task with
Needs Human yes, the project itself when a runaway counter trips), gathers
decide.py's inputs for each, decides it once and has decision_executor.py
carry the outcome out.
Further sibling modules may be added here.
"""
