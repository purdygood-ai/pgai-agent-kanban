# PM Agent

The PM agent decomposes a requirements document into kanban tickets.

## Files

- `pm_materialize.py` — converts a plan JSON into task folders and queue entries
- `pm_status.py` — dashboard showing the state of all tasks in the kanban
- `lib/acceptance_policy.py` — targeted-test policy applied to CODER and WRITER tickets (see below)
- `lib/ride_along_lint.py` — the plan lint for the `ride_along` field (see below)

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

## Plan fields: `needs_operator` and `ride_along`

Two optional top-level fields record what the plan leaves out and what it carries extra. A plan without them is checked only for bundled-item coverage (below).

```json
{
  "needs_operator": [
    {"item": "BUG-NNNN", "question": "Which mirror should the installer use?"}
  ],
  "ride_along": ["BUG-NNNN-small-glyph", "BUG-NNNN-help-typo"]
}
```

- **`needs_operator`** — items the plan leaves out of every ticket because a human must answer first. Each entry names the bug or priority (`item`) and the operator's `question`. The materializer sets the item to `needs-operator` and writes the question to the operator inbox (`pgai_agent_kanban.ops.write.set_needs_operator`). Bundling and the release step leave it alone; answering the inbox entry sets it back to `open`, and the next discovery pass bundles it.
- **`ride_along`** — deferred bugs ticketed inside this requirement RC, by bug key. `lib/ride_along_lint.py` checks the list against four rules: `[release] bundle_mixing` resolves to `ride-along` (the default `separate` refuses every entry); at most `[release] ride_along_max` bugs; none is `high` or `critical` severity (an unrecognised severity is refused too); none names a file the requirement's `## Deliverables` names. Each accepted bug is listed in the requirement's `## Bundled Items` (``- NAME (`PATH`) — ride-along``) and set to `running` (`pgai_agent_kanban.ops.write.ride_along_item`), so the release promotes it when a DONE task claims it and reports it as carried when none does — exactly as a bundle item.

Both fields are checked before anything is written: a failed check exits 1, names every problem, and writes no task folder, queue line or status. They are recorded after every other materializer check and before the first task folder; both writes are idempotent, so a re-run after a failure repeats nothing. How the lint matches file names (path tokens, suffix and bare-basename matches) is in the module's docstring.

**Bundled-item coverage.** When the requirement carries `## Bundled Items`, every item listed there must be named by at least one ticket or listed under `needs_operator`; an accepted `ride_along` bug counts as covered. A ticket names an item when the item's ID (`BUG-NNNN` or `PRIORITY-NNNN`, any case, `-`, `_` or a space after the prefix; the file stem for any other item) appears in any of its text fields — goal, inputs, notes and the rest. A plan that leaves an item uncovered is refused before anything is written: exit 1, error key `materialize.bundled_item_unassigned`, a message naming every uncovered item, and no task folder, queue line or inbox entry. A requirement without `## Bundled Items` is not checked.

## Targeted-test policy

Worker tickets run only the tests for the files they change, plus the project's whole-tree pre-merge check before merging; the full gated suites run once per RC in the TESTER task. `pm_materialize.py` enforces this as it writes each plan-authored CODER or WRITER ticket, using `lib/acceptance_policy.py` and the commands the plan's project names in its own `project.cfg` `[testing]` section (`project.cfg_example` documents both keys):

- `targeted_test_command` — a command template; `{files}` marks where the test files go, and the option just before it is the runner's targeted flag. Several runners that take files the same way are joined with `&&`, each with its own `{files}`. When it is set:
  - Any acceptance criterion or constraint that names a full run of a configured runner is replaced with "`<the first command, {files} reading <the test files this ticket adds or changes>>` exits 0.", with "(and the <name> runner the same way, if <name> tests were touched)" before "exits 0" for each further runner, <name> being its script name without the extension, a `run-` prefix and a `-tests` suffix. Two rewrites that would produce the same line collapse into one. A runner with a targeted flag runs in full when the text names it without that flag. A runner whose template has no flag before `{files}` runs in full when the text names its script with no file list after it: past any options, the next word is not a path, a file name, a glob or a `<...>` placeholder.
  - The constraint "Run only the tests for the files you changed using `<the targeted flag>`. The full gated suites run once per RC in the TESTER task." (the whole first command in place of the flag when it has none) is appended, unless the ticket already carries it.
- `pre_merge_command` — when it is set, the criterion "`<command>` exits 0 before merging." is placed last in the acceptance criteria, exactly once. A plan criterion or constraint that names the command, however it is worded around it, is never rewritten to a targeted run; each such criterion is replaced by that one line. The command counts only when it appears whole with no further option after it: `--pre-merge --base x` is a different run of the runner and is rewritten as above.
- A key that is absent or empty adds nothing, and nothing falls back to the kanban's own commands: a project with neither key gets no runner criterion, no runner constraint and no rewrite. The kanban project's `project.cfg` sets both to its own runners, so its tickets read "`bash team/scripts/run-unit-tests.sh --files <the test files this ticket adds or changes>` (and the integration runner the same way, if integration tests were touched) exits 0." and "`bash team/scripts/run-unit-tests.sh --pre-merge` exits 0 before merging."
- Each rewrite prints an `acceptance-policy:` line on stderr and is recorded, with the original text, in the ticket's `## Notes`. Placing the pre-merge criterion is not a rewrite and is neither printed nor recorded.

Criteria that already pass the targeted flag, or never mention a configured runner, are left as written. TESTER, CM and HUMAN tickets, and the tickets the materializer injects itself (CM bookends, TESTER verify, feature-branch setup, document-pipeline steps), are not touched.
