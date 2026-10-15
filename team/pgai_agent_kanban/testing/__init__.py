"""
pgai_agent_kanban.testing — test-selection and pre-merge helpers for the
kanban's own test tree: ``affected`` decides which test files a change should
run (pure selection, it never runs pytest), and ``pre_merge`` runs the
pre-merge checks -- the by-path module test, the ratchet lints and the
affected tests -- from a worktree.
"""
