# Chomp-Man walkthrough — your own workflow type

This walkthrough builds a small custom workflow type on a live install, points
`pgai-chomp-man` at it, and watches the pipeline pick up a requirement through
it. When you finish, you will have written a workflow plugin, validated it,
and seen the engine route work to it, all without a dev tree or a git clone of
the kanban.

It is a concrete run of Part 1 of
[Creating a Workflow Type](../../../docs/creating-a-workflow.md), with every
value filled in for the demo. That guide lives in the kanban's source
repository, at `docs/creating-a-workflow.md`, and explains the reasoning
behind each field and hook. This page is the hands-on version, and you can
follow it without the guide.

The plugin you build is `chomp-smoke`: a label-versioned, read-only,
report-only workflow. It checks out the game's code without changing it, runs
a test pass, and writes a report. It never tags, publishes, or touches release
state.

---

## Before you start

- You have completed the [Chomp-Man demo setup](README.md#setup-once), so
  `pgai-chomp-man` is registered and wired to `~/pgai-chomp-man-src`.
- **No release is in flight for `pgai-chomp-man`.** Every requirement, bug
  and priority you have deposited has shipped. Discovery will not pick up a
  new requirement while an RC is open. Once you switch the project's workflow
  type, discovery also treats any requirement still waiting in
  `requirements/` as work for the new type.
- The project is not HALTed.
- Your shell is set up for the live install, and you are in the kanban root.
  The generator in Step 1 runs as a Python module from this directory:

  ```bash
  source "$PGAI_AGENT_KANBAN_ROOT_PATH/shell-env"
  cd "$PGAI_AGENT_KANBAN_ROOT_PATH"
  ```

Check there is no active RC:

```bash
grep -A1 '^## Active RC' "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-chomp-man/release-state.md"
```

You should see `none` on the line under the heading.

---

## The plugin you will build

| Field               | Value       | What it means                                                       |
|---------------------|-------------|---------------------------------------------------------------------|
| `version_semantics` | `label`     | The version is a name for the run. It never enters release state.   |
| `git_mode`          | `ro`        | Agents get a detached, read-only worktree of the game's dev tree.   |
| `finalize`          | `report`    | The run ends with a test report: no tag, no publish.                |
| `agents`            | `pm,tester` | PM decomposes; TESTER runs the tests. No CODER, no CM.              |

This is the same shape as the shipped `testing-only` plugin. That plugin is
your reference if a hook is unclear:
`$PGAI_AGENT_KANBAN_ROOT_PATH/workflows/testing-only/workflow.sh`.

The name matters. The upgrade script refreshes the shipped plugins
(`release`, `document`, `testing-only`) in place. The generator refuses those
names, and any other name survives an upgrade untouched.

---

## 1. Scaffold the plugin

```bash
python3 -m pgai_agent_kanban.workflows.create_new_workflow \
    --name chomp-smoke \
    --description "Read-only smoke-test workflow for pgai-chomp-man" \
    --version-semantics label \
    --git-mode ro \
    --finalize report \
    --agents pm,tester
```

What you should see:

```
Scaffolded workflow plugin: /home/you/pgai_agent_kanban/workflows/chomp-smoke
  workflow.cfg       — manifest (status = scaffold)
  workflow.sh        — hook stubs (NOT IMPLEMENTED)
  contract_check.sh  — run after implementing hooks
```

The new directory holds three files: `workflow.cfg`, `workflow.sh` and
`contract_check.sh`.

If the generator refuses, it is protecting you: either the name is a shipped
one or `workflows/chomp-smoke/` already exists. Remove the old directory or
pick another name. If you pick another name, use it in every later command.

## 2. Implement the eight hooks

Every hook in the generated `workflow.sh` is a stub that prints
`NOT IMPLEMENTED` and exits 1. For a label, read-only, report-only plugin,
each hook is a one-liner. Replace the whole file:

```bash
cat > "$PGAI_AGENT_KANBAN_ROOT_PATH/workflows/chomp-smoke/workflow.sh" <<'EOF'
#!/usr/bin/env bash
# workflows/chomp-smoke/workflow.sh — label-versioned, read-only, report-only.

wf_git_mode() {
    echo "ro"
}

wf_resolve_target_version() {
    # label semantics: the label is the version, echoed back unchanged
    if [[ -z "${1:-}" ]]; then
        echo "chomp-smoke: wf_resolve_target_version requires a label" >&2
        return 1
    fi
    echo "$1"
}

wf_pre_task() {
    # the engine creates the read-only worktree for git_mode=ro
    return 0
}

wf_post_task() {
    # the engine removes the read-only worktree for git_mode=ro
    return 0
}

wf_finalize() {
    echo "report"
}

wf_agents() {
    echo "pm,tester"
}

wf_bundle_source_branch() {
    # consulted only when discovery bundles bugs or priorities
    echo "main"
}

wf_dashboard_render() {
    echo "label"
}
EOF
```

Now run the plugin's own contract check. It reports every problem at once:

```bash
bash "$PGAI_AGENT_KANBAN_ROOT_PATH/workflows/chomp-smoke/contract_check.sh"
```

What you should see now that the hooks are real:

```
ERROR: status = scaffold — flip to 'ready' after implementing hooks
FAIL: 1 contract check error(s) — see above
```

That one remaining error is expected. If you also see
`workflow.sh contains NOT IMPLEMENTED stubs`, a stub survived. Fix it before
going on.

## 3. Flip the status to ready

The `status` line is your statement that the plugin is implemented. The
engine refuses to route work to a `scaffold` plugin, and to any status that is
not exactly `ready`.

```bash
sed -i 's/^status = scaffold$/status = ready/' \
    "$PGAI_AGENT_KANBAN_ROOT_PATH/workflows/chomp-smoke/workflow.cfg"
grep '^status' "$PGAI_AGENT_KANBAN_ROOT_PATH/workflows/chomp-smoke/workflow.cfg"
```

You should see `status = ready`.

## 4. Validate with the operator gate

`validate-workflow.sh` runs the four contract checks in order: manifest,
hook presence, stubs, capability values. It stops at the first failure.

```bash
"$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/validate-workflow.sh" --type chomp-smoke
```

What you should see:

```
Validating workflow plugin: chomp-smoke
Plugin directory: /home/you/pgai_agent_kanban/workflows/chomp-smoke

PASS: workflow type 'chomp-smoke' satisfies all contract checks.
```

The command exits 0. If it prints `FAIL:` instead, the message names the
check and the value. The usual causes are a typo in a capability value
(`git_mode`, `finalize`, `version_semantics`) or a hook missing from
`workflow.sh`. Fix it and run the command again.

## 5. Point `pgai-chomp-man` at the new type

Switch the project's workflow type, and confirm the change:

```bash
sed -i 's/^workflow_type *= *release *$/workflow_type = chomp-smoke/' \
    "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-chomp-man/project.cfg"
grep '^workflow_type' "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-chomp-man/project.cfg"
```

You should see `workflow_type = chomp-smoke`. Step 7 switches it back to
`release`.

## 6. Drop a labelled requirement and wake PM

A label requirement still uses the intake filename contract: the name starts
with `v` and a digit and ends in `.md`. For a label workflow, everything
between the `v` and the `.md` is the label. A date makes a good label.

The `## Target Version` field must state the label explicitly. Label projects
refuse `auto`. The `## Source Branch` names the branch the read-only worktree
is made from; for this demo that is `ai_main`.

```bash
STAGE="$(mktemp -d)"
cat > "$STAGE/v20261013-chomp-smoke.md" <<'EOF'
# Requirements: v20261013-chomp-smoke — smoke test of the released game

## Status
open

## Target Version
v20261013-chomp-smoke

## Workflow Type
chomp-smoke

## Test Required
true

## Source Branch
ai_main

## Human Approval Required
auto

## Summary
Run the game's test suite against ai_main in a read-only worktree and
report the result. No code changes.

## Acceptance Criteria
- [ ] The test suite runs against ai_main and a report is written.
EOF
scripts/intake.sh --project pgai-chomp-man --file "$STAGE/v20261013-chomp-smoke.md"
```

`intake.sh` prints the path it deposited to, under
`projects/pgai-chomp-man/requirements/`.

The PM wake runs discovery. Wait for the next scheduled PM wake, or run one
yourself:

```bash
scripts/wake-batch.sh --agent=pm --max-tasks=1
```

## 7. Confirm pickup, then restore the project

Read the requirement back:

```bash
scripts/show.sh --project pgai-chomp-man --key v20261013-chomp-smoke
```

What you should see: the `## Status` has moved from `open` to `running`. On
the dashboard, or with `scripts/kanban-status.sh`, the new work shows up in
the **pm** and **tester** queues only. There is no CODER or CM task, because
the `wf_agents` roster says `pm,tester`.

That routing is what this walkthrough set out to show. You do not need to
wait for the test report.

If the status is still `open` after one wake, run the wake command once more.
If the requirement went to BLOCKED instead, read the block reason. The usual
causes are these:

1. The manifest still says `status = scaffold`. Go back to Step 3.
2. A capability value has a typo. `validate-workflow.sh` names it.
3. `workflow_type` in `project.cfg` and the plugin directory name differ.
   Compare the spelling in both.

Switch the project back to the release workflow once the smoke run's pm and
tester tasks show as done. Until then, those tasks still route through
`chomp-smoke`.

```bash
sed -i 's/^workflow_type *= *chomp-smoke *$/workflow_type = release/' \
    "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-chomp-man/project.cfg"
grep '^workflow_type' "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-chomp-man/project.cfg"
```

You should see `workflow_type = release`. The plugin directory can stay. It
survives upgrades because its name is not a shipped one.

---

## What you just learned

- A workflow type is a directory holding a manifest and eight hooks. The
  engine discovers it there, and no engine code changes.
- `contract_check.sh` shows every problem at once while you write the
  plugin. `validate-workflow.sh` is the gate before live use.
- `status = ready` is the switch that opens a plugin to the engine.
- A project changes lifecycle by changing one line, `workflow_type`, in its
  `project.cfg`.
- Label requirements keep the `v<digit>…` intake filename and need an
  explicit `## Target Version`.
