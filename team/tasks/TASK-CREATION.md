# Task Creation Workflow

This guide is for HUMAN creating tasks by hand in the kanban system. For automated decomposition of larger projects, use the PM agent instead — see `scripts/pm-agent.sh`.

## Quick Reference

1. Decide owner and naming
2. Create task directory
3. Copy and populate README.md
4. Create initial status.md
5. Add to claude_backlog.md
6. Verify and hand off

## Task Naming Convention

Format:

```
<OWNER>-<YYYYMMDD>-<SEQ>-<short-slug>
```

Examples:

- `CLAUDE-20260412-001-add-login-endpoint`
- `CLAUDE-20260412-002-write-api-docs`

Rules:

- `<OWNER>`: CLAUDE or HUMAN
- `<YYYYMMDD>`: UTC date, no hyphens
- `<SEQ>`: zero-padded sequence for that owner+date — `001`, `002`, etc.
- `<short-slug>`: kebab-case description, lowercase, hyphens, under 30 characters

## When to Create a Task

### For CLAUDE

- Production-grade coding
- Long-form writing (articles, docs, books, whitepapers)
- Content analysis and summarization
- Research summaries
- Coding-focused work (CODER role)
- Writing-focused work (WRITER role)

Token constraints: once Claude's threshold is hit, it goes offline. Tokens refresh on the Max plan's cycle. Leave the task as-is — Claude resumes on the next wake invocation after token refresh.

### For HUMAN

- Human judgment or approval
- Business decisions
- Clarifications or missing requirements
- Reviews of deliverables
- Acceptance decisions

## Step-by-Step

### Step 1: Plan the Task

Gather:

- What needs to be done? (the goal)
- Who should do it? (CLAUDE or HUMAN)
- What's the output? (required output, acceptance criteria)
- Any constraints?
- What context do they need? (context paths)

### Step 2: Create the Task Directory

```bash
TASK_ID="CLAUDE-20260412-001-add-login-endpoint"
mkdir -p "$PGAI_AGENT_KANBAN_ROOT_PATH/tasks/${TASK_ID}/artifacts"
mkdir -p "$PGAI_AGENT_KANBAN_ROOT_PATH/tasks/${TASK_ID}/logs"
```

### Step 3: Copy and Populate README.md

```bash
cp "$PGAI_AGENT_KANBAN_ROOT_PATH/templates/task/task-readme/README.md" \
   "$PGAI_AGENT_KANBAN_ROOT_PATH/tasks/${TASK_ID}/README.md"

$EDITOR "$PGAI_AGENT_KANBAN_ROOT_PATH/tasks/${TASK_ID}/README.md"
```

Fill in the fields. The most important ones:

- **Working Directory**: where the agent works
  - An absolute path for a real project
  - `local-development-only` for throwaway work in `artifacts/`
  - `none` (same effect as `local-development-only`)
- **Git Repo**: a repo URL or `none`
- **Source Branch**: usually `develop`, or `none` if no git
- **Feature Branch**: `feature/<task-id>` if git is involved, or `none`
- **Goal**: 1-2 sentences on what must be achieved
- **Required Output**: exact files or deliverables that must exist when done
- **Acceptance Criteria**: testable checklist items the reviewer will use
- **Prerequisites**: list of full task IDs (one per line, with `-` prefix) that must be in DONE or WONT-DO state before this task starts. Use `none` if independent.

### Step 4: Create Initial status.md

```bash
cp "$PGAI_AGENT_KANBAN_ROOT_PATH/templates/task/task-status/status.md" \
   "$PGAI_AGENT_KANBAN_ROOT_PATH/tasks/${TASK_ID}/status.md"
```

Edit it to set the task ID and ensure state is `BACKLOG`. The wake script will create the status file automatically if you skip this step, but doing it explicitly is cleaner.

### Step 5: Add to Backlog Queue

```bash
echo "- [ ] ${TASK_ID}" >> "$PGAI_AGENT_KANBAN_ROOT_PATH/tasks/queues/claude_backlog.md"
```

The queue uses markdown checkboxes:

- `[ ]` or `[]` — pending, ready to pull (empty brackets are tolerated)
- `[W]` — waiting on prerequisites (auto-resolved by wake script)
- `[B]` — blocked — needs manual human attention
- `[x]` — done or won't-do

Append to the end for normal priority. Edit and move to the top for urgent work.

### Step 6: Verify and Hand Off

```bash
ls "$PGAI_AGENT_KANBAN_ROOT_PATH/tasks/${TASK_ID}/"
```

You should see:

```
README.md
status.md
artifacts/
logs/
```

Then run the wake script when you're ready:

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake-claude.sh"
```

## Common Patterns

### Pattern 1: Pass Output From One Task To Another

1. Create task A
2. Wake Claude — task A moves to DONE
3. You inspect the artifacts (optional — the next task can pick up regardless)
4. Create task B with `Inputs:` referencing task A's `artifacts/` folder
5. Wake Claude on task B

For automated chains, declare task B's `## Prerequisites` as task A. The wake script will hold task B in WAITING until task A is DONE, then auto-promote it to BACKLOG.

### Pattern 2: Task Needs Multiple Rounds

If Claude finishes a draft and you want revision:

1. Update the README.md with new acceptance criteria
2. Move status.md state back to BACKLOG
3. Wake Claude — it sees the BACKLOG state and resumes

### Pattern 3: Task Blocks On Missing Input

If Claude can't start:

1. Claude updates status to BLOCKED with the exact reason
2. You provide the missing input
3. You move state back to BACKLOG
4. Wake Claude

## FAQ

**Q: What if Claude goes offline mid-task (token refresh)?**
Leave the task as-is. The next wake invocation reads the README and status, sees where work was left, and resumes.

**Q: Can I change the task after it's queued?**
Yes. Edit the README.md. Claude will re-read it on the next session. For major changes, prefer creating a new task instead.

**Q: What's the difference between README.md and status.md?**
README.md is the spec — what to do. status.md is the progress report — what's been done. Claude updates status.md; you may update README.md.

**Q: How do I prioritize tasks?**
Queue priority is file order. Move high-priority task IDs to the top of `claude_backlog.md`.

**Q: Why use this when the PM agent can decompose for me?**
For one-off tasks, manual creation is faster than writing a requirements doc. For multi-task projects, use the PM agent.
