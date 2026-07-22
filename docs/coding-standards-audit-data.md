# Coding Standards Audit Data

Run: 2026-07-18T16:57:21Z
Repo root: /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260718-041-self-audit-automation
Script: /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260718-041-self-audit-automation/team/scripts/audit_coding_standards.sh

Each section below corresponds to one directive in docs/coding-standards.md.
Verdict key:
  PASS                   — automated check found no violations
  FINDINGS               — automated check found issues; see body for paths/lines
  MANUAL-REVIEW-REQUIRED — check cannot be fully automated; human judgment required

## D01 — Split code by concern
Verdict: MANUAL-REVIEW-REQUIRED

lint_lib_function_dedupe: ok — 37 lib/*.sh file(s) scanned, no duplicate function names.

Broader architectural compliance (one implementation per operation across the
whole tree) requires manual inspection; function deduplication check passed.

## D02 — Keep surfaces thin
Verdict: MANUAL-REVIEW-REQUIRED

Surface thinness (CLI/dashboard/API adapters must not carry business rules)
cannot be verified mechanically.  Reviewer should confirm:
  - CLI entry points in team/scripts/ call helpers in team/scripts/lib/ rather
    than embedding logic directly.
  - API handlers in team/pgai_agent_kanban/api/routers/ delegate to core modules.
  - Dashboard pane scripts in team/scripts/dashboard/ render only; no logic.
Architecture context: the lib/ structure, pgai_agent_kanban/ package, and
dashboard rendering chain separate concerns at the directory level.

## D03 — Put shared code in lib/
Verdict: MANUAL-REVIEW-REQUIRED

Structural check passed: team/scripts/lib/ contains 37 .sh helpers;
team/pgai_agent_kanban/ Python package exists.

Copy-paste duplication across non-lib files cannot be detected mechanically.
Manual review should spot-check that non-lib scripts source lib/ functions
rather than reimplementing them inline.

## D04 — Write generic shared code
Verdict: MANUAL-REVIEW-REQUIRED

Shared-code genericity (designed for reuse by future surfaces, not shaped
around a single caller) is a design property not detectable by static analysis.
Reviewer should inspect team/scripts/lib/ and team/pgai_agent_kanban/ to confirm:
  - Helper functions accept parameters rather than reading globals.
  - No helper function is named after a specific caller.
  - The REST API (team/pgai_agent_kanban/api/) consumes shared logic without
    duplicating it.

## D05 — No hardcoded operational values
Verdict: FINDINGS

Config companion present: kanban.cfg_example
Config companion present: project.cfg_example
Config companion present: projects.cfg_example
lint_env_bootstrap.py FINDINGS:
  [bash] FAIL /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260718-041-self-audit-automation/team/scripts/cm/finalize.sh: sources approved prelude at line 112 but PGAI_AGENT_KANBAN_ROOT_PATH is first used at line 80 — the source must appear before the first usage so the script bootstraps correctly from a fresh shell
  [bash] FAIL /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260718-041-self-audit-automation/team/scripts/cm/open-doc.sh: sources approved prelude at line 125 but PGAI_AGENT_KANBAN_ROOT_PATH is first used at line 96 — the source must appear before the first usage so the script bootstraps correctly from a fresh shell
  
  lint_env_bootstrap: 2 violation(s) found (sides checked: bash, python)

## D06 — Every script ships --help
Verdict: PASS

lint_help_presence: scanning 71 scripts in /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260718-041-self-audit-automation/team/scripts and /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260718-041-self-audit-automation/team/scripts/cm
lint_help_presence: OK — 71 script(s) checked, 0 exempt, 0 offenders.

## D07 — Scaffolding and docs ship with feature
Verdict: MANUAL-REVIEW-REQUIRED

docs/ directory present with 22 markdown file(s)
docker-compose.example.yaml present

Structural scaffolding files present.  Whether every new feature in this RC
shipped with its documentation requires manual review of the RC diff.

## D08 — Comments describe behavior
Verdict: PASS

lint_comment_provenance: scanning 235 file(s) in /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260718-041-self-audit-automation/team/scripts, /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260718-041-self-audit-automation/team/scripts/lib, /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260718-041-self-audit-automation/team/scripts/cm, /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260718-041-self-audit-automation/team/pgai_agent_kanban
lint_comment_provenance: OK — 0 findings across 235 file(s).

## D09 — REST API serves Swagger and versioned contract
Verdict: PASS

api/app.py: FastAPI default Swagger UI at /docs (docs_url not overridden)
api/app.py: /docs route documented in module docstring
ICD_VERSION file present: 1.2.0

## D10 — Default stack: Python 3.12+ and bash
Verdict: MANUAL-REVIEW-REQUIRED

Python version constraint in pyproject.toml: requires-python=">=3.12"
No non-standard language files (Ruby, JS, Go, Rust, Java) found under team/

Automated check found no non-standard language files.  Manual verification:
confirm no logic-heavy scripts use awk/perl beyond one-liners, and that any
authorized deviation from the default stack is documented in the relevant ticket.

## D11 — Docker artifacts and fail-loud entrypoint
Verdict: PASS

Dockerfile present: docker/Dockerfile
docker-compose example present: docker/docker-compose.example.yaml
docker/entrypoint.sh: uses set -e (fail-loud)
docker/entrypoint.sh: startup validation present (error exits detected)

---

## Audit Summary

Run: 2026-07-18T16:57:21Z
Total directives: 11
PASS: 4
FINDINGS: 1
MANUAL-REVIEW-REQUIRED: 6

---

## Fixes Applied in This RC

Small findings turned up during the pre-audit lint run and fixed on this branch:

- **D08 fix** — `team/scripts/cm/open-rc.sh:91`: replaced a concrete internal
  task ID in the help text example (`CM-20260718-001-open-rc`) with a format
  placeholder (`CM-YYYYMMDD-NNN-open-rc`) so the example illustrates the ID
  shape without citing a specific internal task.  Confirmed PASS by
  lint_comment_provenance.py after fix.

## Outstanding Findings (not fixed in this RC)

- **D05 / lint_env_bootstrap** — `team/scripts/cm/finalize.sh` and
  `team/scripts/cm/open-doc.sh`: the env-bootstrap source call appears after
  the first non-comment reference to `PGAI_AGENT_KANBAN_ROOT_PATH` (lines 80
  and 96 respectively) because the first reference is inside the help-text
  heredoc function body.  Fixing this requires restructuring the help-function
  placement relative to the bootstrap source, which is a multi-line change
  that may affect `--help` behavior on shells with no kanban env set.
  Recorded here for follow-up; not fixed in this RC per task constraints.

  **Closed in v1.25.1 by BUG-0087-env-bootstrap-lint-fails-help-heredoc-cm-scripts.**
  The lint (`team/scripts/lint_env_bootstrap.py`) was taught to skip heredoc
  body content when scanning for the first non-comment occurrence of
  `PGAI_AGENT_KANBAN_ROOT_PATH`, so a mention inside a `--help` heredoc above
  the bootstrap source no longer trips the ordering check.  The D05 verdict
  block above is retained as the historical record of what the v1.25.0 audit
  scan found; the two `cm/` scripts are no longer flagged on v1.25.1.
