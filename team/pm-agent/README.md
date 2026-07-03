# PM Agent

The PM agent decomposes a requirements document into kanban tickets.

## Files

- `pm_materialize.py` — converts a plan JSON into task folders and queue entries
- `pm_status.py` — dashboard showing the state of all tasks in the kanban

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
