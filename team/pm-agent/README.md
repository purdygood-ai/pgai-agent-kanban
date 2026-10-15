# PM Agent

The PM agent decomposes a requirements document into kanban tickets.

## Files

- `pm_materialize.py` — converts a plan JSON into task folders and queue entries
- `pm_status.py` — dashboard showing the state of all tasks in the kanban
- `lib/acceptance_policy.py` — targeted-test policy applied to CODER and WRITER tickets (see below)

Templates used by this agent live in `team/templates/agent/`:
- `REQUIREMENTS-TEMPLATE.md` — copy this and fill it in to write a project spec
- `BRIEF-TEMPLATE.md` — operator brief template for new work
- `REPORT-TEMPLATE.md` — TESTER verification report template
- `TESTER-PRIORITY-TEMPLATE.md` — TESTER-authored priority requirements template

The entry point is `../scripts/pm-agent.sh`. It invokes the `pm` Claude Code subagent to read your requirements doc and produce a JSON plan, then runs `pm_materialize.py` to create the actual task folders.

## Usage

```bash
# Copy the template
cp templates/agent/REQUIREMENTS-TEMPLATE.md ~/my-project.md

# Edit it with your real requirements
$EDITOR ~/my-project.md

# Preview the plan (no tickets created yet)
scripts/pm-agent.sh ~/my-project.md --dry-run

# Create the tickets
scripts/pm-agent.sh ~/my-project.md

# Review the queues (one per agent role)
cat tasks/queues/coder_backlog.md
cat tasks/queues/writer_backlog.md

# Start work (provider-agnostic; dispatches to the active provider)
scripts/wake.sh --agent=coder         # one task
scripts/wake-batch.sh --agent=coder   # multiple tasks
```

## Plan JSON Schema

The pm subagent outputs JSON in this shape:

```json
{
  "project_name": "short-kebab-case-name",
  "summary": "One sentence summary of the project",
  "tasks": [
    {
      "sequence": 1,
      "slug": "scaffolding",
      "title": "Project Scaffolding and Setup",
      "role": "CODER",
      "goal": "What must be achieved in this task",
      "inputs": ["files or resources needed"],
      "context_paths": ["README files to read for context"],
      "required_output": "Exact deliverables — files, endpoints, configs",
      "constraints": ["specific rules for this task"],
      "acceptance_criteria": ["testable criteria as checklist items"],
      "depends_on": [],
      "notes": "any clarifications"
    }
  ]
}
```

`pm_materialize.py` reads this JSON and creates one task folder per task, with a populated README.md and status.md, and appends entries to the matching per-agent queue (`tasks/queues/<role>_backlog.md`) in dependency order.

## Targeted-test policy

Worker tickets run only the tests for the files they change, plus the runner's whole-tree pre-merge checks before merging; the full gated suites run once per RC in the TESTER task. `pm_materialize.py` enforces this as it writes each plan-authored CODER or WRITER ticket, using `lib/acceptance_policy.py`:

- Any acceptance criterion or constraint that names `run-unit-tests.sh` or `run-integration-tests.sh` without `--files` is replaced with: "`bash team/scripts/run-unit-tests.sh --files <the test files this ticket adds or changes>` (and the integration runner the same way, if integration tests were touched) exits 0." Two rewrites that would produce the same line collapse into one.
- The constraint "Run only the tests for the files you changed using `--files`. The full gated suites run once per RC in the TESTER task." is appended, unless the ticket already carries it.
- The criterion "`bash team/scripts/run-unit-tests.sh --pre-merge` exits 0 before merging." is placed last in the acceptance criteria, exactly once — a copy the plan already wrote is moved there, never rewritten to a `--files` run. Other wordings that name a runner without `--files` are still rewritten as above.
- Each rewrite prints an `acceptance-policy:` line on stderr and is recorded, with the original text, in the ticket's `## Notes`. Placing the pre-merge criterion is not a rewrite and is neither printed nor recorded.

Criteria that already pass `--files`, or never mention a runner, are left as written. TESTER, CM and HUMAN tickets, and the tickets the materializer injects itself (CM bookends, TESTER verify, feature-branch setup, document-pipeline steps), are not touched.
