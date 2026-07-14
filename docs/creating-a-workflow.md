# Creating a Workflow Type

A workflow type is a plugin: a directory under `$KANBAN_ROOT/workflows/<name>/`
that tells the pgai-agent-kanban engine how to run a class of work. Software
releases, versioned documents, and read-only test runs are all workflow types.
Adding a new one does not require editing engine code.

This guide has two parts.

- **Part 1 — Live-operator path.** You run a live install, you have no git
  clone of the kanban source, and you want a custom workflow type that lives
  in your `$KANBAN_ROOT` and never leaves your machine. This is the primary
  path. No git, no dev tree, no pull request.
- **Part 2 — Contributor path.** You want to ship a new workflow type with
  the framework itself. You clone the source, write the plugin under
  `team/workflows/`, add tests, and drop a requirement so the kanban ships
  it via its normal release lifecycle.

Both parts produce the same artifact: a plugin directory with `workflow.cfg`
(the manifest) and `workflow.sh` (the hooks). The engine discovers the
directory at load time and never learns the plugin's name any other way.

**Related contracts:**
- [../team/workflows/README.md](../team/workflows/README.md) — the
  workflows-directory front door: the two-layer plugin model, discovery
  and fail-closed behaviour, and the minimal-vs-rich distinction.
- [../team/workflows/SCHEMA.md](../team/workflows/SCHEMA.md) — the
  `pipeline.yaml` schema, needed only for rich plugins that ship a
  pipeline.
- [public-contract.md](public-contract.md) — the workflows-dir survival
  contract and the naming rule your plugin must honor.
- [ARCHITECTURE.md](../ARCHITECTURE.md) — the plugin discovery model,
  fail-closed routing, and the eight-hook interface engine code calls.

---

## Concepts (both parts read this first)

A workflow-type plugin is a directory containing two files:

```
$KANBAN_ROOT/workflows/<name>/
├── workflow.cfg   # the manifest — capabilities and status
└── workflow.sh    # the hooks — bash functions the engine calls
```

The engine loads plugins by directory discovery. When it needs to run work
for a project whose `project.cfg` says `workflow_type = acme-deploy`, it
scans `$KANBAN_ROOT/workflows/*/workflow.cfg`, finds the entry named
`acme-deploy`, sources `acme-deploy/workflow.sh`, and calls the hooks. The
engine never asks "is this type release, document, or something else?" —
it asks "what does this plugin's `wf_git_mode` return?"

### The manifest (`workflow.cfg`)

Two sections. Every field is required.

```ini
[workflow]
name        = <plugin name — must match the directory name>
description = <one-line summary>
status      = scaffold | ready

[capabilities]
version_semantics = semver | label | none
git_mode          = none | ro | rw
finalize          = tag | publish | report
agents            = <comma-separated agent roster, e.g. pm,coder,tester,cm>
```

Field meanings:

- **`status`** — `scaffold` means the plugin is not usable yet; the engine
  fail-closed refuses to route work to it. `ready` means the operator has
  implemented every hook and validated the plugin. You flip this to `ready`
  yourself, only after `validate-workflow.sh` passes.
- **`version_semantics`** — how the engine interprets the requirement's
  version field. `semver` participates in the release lifecycle
  (release-state.md, version ceilings, dashboard shipped/green). `label`
  is a name for the artifact only — never enters release-state, never
  triggers ceiling checks, and the dashboard renders label-versioned items
  by open/running/done, not by version comparison. `none` disables version
  handling entirely.
- **`git_mode`** — `none` (no git worktree), `ro` (detached read-only
  worktree of the local dev tree at the ref named by the requirement), or
  `rw` (a writable feature-branch worktree, the classic release pattern).
  Working agents never fetch, pull, or push regardless of git_mode; CM
  remains the sole origin-toucher.
- **`finalize`** — how the workflow completes. `tag` (git tag on main;
  release lifecycle), `publish` (write a versioned deliverable under
  `projects/<name>/artifacts/`; document lifecycle), `report` (write a
  test report; no git tag, no publish, no state mutation).
- **`agents`** — the ordered roster PM uses to decompose a requirement.
  Not every roster includes CM; `report`- and `publish`-only workflows
  may omit it.

### The hooks (`workflow.sh`)

Eight bash functions. Each does one thing. The engine calls them; policy
lives in the plugin.

| Hook | Called with | Returns | Purpose |
|---|---|---|---|
| `wf_git_mode` | (none) | `none` / `ro` / `rw` | Capability query — how the engine builds the per-task worktree. |
| `wf_resolve_target_version` | requirement version string | resolved version | For semver types, applies patch-lane logic; for label types, echoes the label back. |
| `wf_pre_task` | task_id, source_branch | (side effects only) | Per-task setup. For `git_mode=none`, usually a no-op. |
| `wf_post_task` | task_id | (side effects only) | Per-task teardown. Usually a no-op — the engine owns worktree lifecycle. |
| `wf_finalize` | version/label | `tag` / `publish` / `report` | Capability query — how the engine finishes the workflow. |
| `wf_agents` | (none) | comma-separated roster | Roster and order for PM decomposition. |
| `wf_bundle_source_branch` | target_version | branch name | Which branch discovery bundles bugs/priorities against. Typically the prefixed main branch for non-release types. |
| `wf_dashboard_render` | (context) | `semver` / `label` | Dashboard render rule — controls whether the item's version is compared against `last_released` or shown by status only. |

Every hook is required. If any hook body still contains the literal string
`NOT IMPLEMENTED`, the plugin fails validation and the engine refuses to
route work to it.

Look at `$KANBAN_ROOT/workflows/testing-only/workflow.sh` for a complete,
minimal working example. It defines all eight hooks in about 100 lines
including comments.

### The optional pipeline (`pipeline.yaml`)

Manifest + hooks is what the engine needs. A plugin may **additionally**
ship a `pipeline.yaml` inside its directory to declare a richer, multi-
step PM decomposition — named steps in an ordered pipeline, with
`foreach` fan-outs, `when:` predicates, per-step branch patterns, and
declared deliverables.

```
$KANBAN_ROOT/workflows/<name>/
├── workflow.cfg     # required — the manifest
├── workflow.sh      # required — the hooks
└── pipeline.yaml    # optional — the rich PM decomposition
```

When PM decomposes a requirement for a plugin that ships a
`pipeline.yaml`, it materialises the tasks the pipeline describes.
When the plugin does **not** ship a `pipeline.yaml`, PM falls back to
the **simple path**: it reads the roster from `wf_agents` and produces
one task per roster entry in order. That is the documented default —
minimal plugins do not need a pipeline, and the shipped `testing-only`
plugin is the reference for the shape.

Ship a `pipeline.yaml` when your workflow needs steps that are not
one-per-agent: a step that fans out over a list, a step that is
conditionally skipped, a step that produces a specific named
deliverable downstream steps consume, or a step with a per-step branch
pattern distinct from the rest. The shipped `release` and `document`
plugins are the references for the rich shape.

The pipeline format itself — every field, every predicate, every
reference variable — is documented in the pipeline schema. See
[../team/workflows/README.md](../team/workflows/README.md) for the
two-layer overview and
[../team/workflows/SCHEMA.md](../team/workflows/SCHEMA.md) for the
field-by-field reference. This guide focuses on the manifest-plus-hooks
core; consult those two files when you decide to add a pipeline to your
plugin.

### The upgrade-survival contract (read this before naming your plugin)

The framework's upgrade script (`upgrade.sh --force`) refreshes shipped
workflow plugins (`release`, `document`, `testing-only`) in place. It does
this by an overlay copy from the new install onto your live tree, which
leaves any directory absent from the source tree untouched. Your custom
plugin at `$KANBAN_ROOT/workflows/acme-deploy/` survives an upgrade
byte-identical, without you doing anything.

**One hazard, one rule.** If you name your plugin `release`, `document`,
or `testing-only`, the upgrade will silently overwrite your work. The
generator refuses these names for exactly this reason. Use an org-prefixed
name — `acme-deploy`, `contoso-audit`, `internal-canary` — and you have a
contract guarantee that upgrades will not touch it.

This is a stable contract for the 1.x line. See
[public-contract.md](public-contract.md).

---

## Part 1 — Live-operator path (no git, no dev tree)

You are on a live install. `$KANBAN_ROOT` points at your kanban directory
(typically `$HOME/pgai_agent_kanban`). You want a custom workflow type that
lives entirely in your install and never leaves your machine.

The end-to-end sequence is five steps:

1. Scaffold the plugin directory with the generator.
2. Implement the eight hooks in `workflow.sh`.
3. Run `validate-workflow.sh` until it exits 0.
4. Flip `status = ready` in `workflow.cfg`.
5. Drop a requirement that names the new type; the pipeline picks it up
   on the next tick.

### Step 1 — Scaffold the plugin

Run the generator. It emits a plugin directory pre-filled with a manifest
and stub hooks that fail loudly with `NOT IMPLEMENTED` until you replace
them.

```bash
python3 -m pgai_agent_kanban.workflows.create_new_workflow \
    --name acme-deploy \
    --description "Acme's canary-deploy workflow" \
    --version-semantics semver \
    --git-mode rw \
    --finalize publish \
    --agents pm,coder,tester,cm
```

The generator writes:

```
$KANBAN_ROOT/workflows/acme-deploy/
├── workflow.cfg        # manifest, status = scaffold
├── workflow.sh         # eight hook stubs (each exits 1, prints NOT IMPLEMENTED)
└── contract_check.sh   # self-contained check you can run in-place
```

**Output-root resolution.** With no `--workflows-dir` flag, the generator
resolves the output root in this order:

1. Explicit `--workflows-dir <path>` if you passed it.
2. `$PGAI_AGENT_KANBAN_ROOT_PATH/workflows/` — the live-install default.
3. `<git-root>/team/workflows/` — the dev-tree fallback (Part 2 case).

On a live install with `$PGAI_AGENT_KANBAN_ROOT_PATH` set (which is the
normal state after `install.sh` completes), step 2 fires. You get
`$KANBAN_ROOT/workflows/acme-deploy/`.

**Refusal cases.** The generator refuses in two situations:

- **Shipped name.** `--name release`, `--name document`, `--name
  testing-only` — the generator exits non-zero rather than silently
  scaffolding over a name that upgrade will later overwrite. Use an
  org-prefixed name.
- **Directory exists.** If `workflows/acme-deploy/` already exists, the
  generator refuses. Pass `--force` to overwrite (this deletes the
  existing directory and recreates it from scratch — you lose any hooks
  you had implemented).

### Step 2 — Implement the hooks

Open `$KANBAN_ROOT/workflows/acme-deploy/workflow.sh`. You will see eight
stub functions, each of which looks like this:

```bash
wf_git_mode() {
    echo "NOT IMPLEMENTED: wf_git_mode" >&2
    exit 1
}
```

Replace each stub body with the real logic. Every hook must return
successfully with the expected output; a stub left in place is a plugin
that fails contract validation.

At minimum, capability-query hooks (`wf_git_mode`, `wf_finalize`,
`wf_agents`, `wf_bundle_source_branch`, `wf_dashboard_render`) can be
one-liners that echo the capability value:

```bash
wf_git_mode() {
    echo "rw"
}

wf_finalize() {
    echo "publish"
}

wf_agents() {
    echo "pm,coder,tester,cm"
}
```

The two hooks with real per-workflow logic are:

- **`wf_resolve_target_version`** — take the version from the requirement
  and apply your semver/label rules. For semver types, the release
  workflow's implementation is the reference. For label types (like
  testing-only), you echo the label back unchanged.
- **`wf_pre_task` / `wf_post_task`** — usually no-ops. The engine
  creates and tears down worktrees for `git_mode=ro` and `git_mode=rw`
  tasks. Add logic here only if your workflow has a genuine per-task
  setup step the engine cannot infer from capability values.

Use `$KANBAN_ROOT/workflows/testing-only/workflow.sh` and
`$KANBAN_ROOT/workflows/release/workflow.sh` as reference implementations
for label-versioned and semver-versioned workflows respectively.

### Step 3 — Validate with `validate-workflow.sh`

The operator gate. Runs on the live install with no dev tree and no
pytest required. Four contract checks:

1. **Manifest validity** — `workflow.cfg` present, required sections and
   keys present, `status = ready` (not `scaffold`, not missing).
2. **Hook presence** — `workflow.sh` defines all eight `wf_*` functions.
3. **Stub detection** — no hook body contains the literal
   `NOT IMPLEMENTED`.
4. **Capability validity** — `git_mode`, `version_semantics`, and
   `finalize` all have allowed values (see the manifest table above).

Run it:

```bash
$KANBAN_ROOT/scripts/validate-workflow.sh --type acme-deploy
```

Expected output on a still-scaffolded plugin (Step 2 not yet complete):

```
Validating workflow plugin: acme-deploy
Plugin directory: /home/you/pgai_agent_kanban/workflows/acme-deploy

FAIL: workflow.sh contains stub markers (NOT IMPLEMENTED) — implement all hooks
```

Expected output on a plugin that has all hooks implemented but still has
`status = scaffold` in `workflow.cfg`:

```
FAIL: workflow.cfg status = scaffold — flip to 'ready' after implementing hooks
```

Expected output when the plugin passes:

```
PASS: workflow type 'acme-deploy' satisfies all contract checks.
```

Fix and re-run until you see `PASS`. Do not proceed to Step 4 until the
command exits 0.

### Step 4 — Flip status to ready

Edit `$KANBAN_ROOT/workflows/acme-deploy/workflow.cfg`. Change the
manifest's `status` line:

```ini
[workflow]
name        = acme-deploy
description = Acme's canary-deploy workflow
status      = ready

[capabilities]
version_semantics = semver
git_mode          = rw
finalize          = publish
agents            = pm,coder,tester,cm
```

The `status` flip is the operator's signed statement that the plugin is
implemented and validated. The engine treats a `scaffold` plugin as
fail-closed: any requirement pointed at a `scaffold` type routes to
BLOCKED with the type name in the reason. Any typo in the `status` value
(`redy`, `Ready`, `read`) also fail-closes for the same safety reason.
Only the exact literal `ready` opens the plugin for engine use.

Optionally re-run `validate-workflow.sh --type acme-deploy` once more.
It should still pass, and now the manifest is in the state the engine
will accept.

### Step 5 — Drop a requirement

Point a project at the new workflow type. Two things need to be true:

- The project's `projects/<name>/project.cfg` has `workflow_type =
  acme-deploy`.
- A requirements file exists under `projects/<name>/requirements/` that
  the pipeline will pick up on its next tick.

If this is a brand-new project, use `create-project.sh` with
`--workflow-type acme-deploy` (see the operator commands doc). If it is
an existing project, edit `project.cfg` to change `workflow_type`.

**Registration validates against the plugin registry.** `create-project.sh`
does not carry a hardcoded list of known workflow types. When you pass
`--workflow-type acme-deploy`, the script loads the plugin from
`$KANBAN_ROOT/workflows/acme-deploy/` (via the same registry loader the
engine uses at run time) and accepts the type if — and only if — the
plugin's manifest has `status = ready`. Any `status = ready` plugin the
registry discovers is a valid `--workflow-type` value, including the
shipped `release`, `document`, and `testing-only` plugins and any
operator-authored custom type. No engine edit or docs edit is needed to
add a new type to the accepted set — Step 4's status flip is the whole
enrolment.

**Minimal plugins do not require a `templates/project/<type>/` directory.**
If your workflow does not ship a type-specific template directory,
`create-project.sh` falls back to the type-agnostic `templates/project/release/`
templates (the bugs, priority, and requirements READMEs and the BUG/PRIORITY
templates are workflow-neutral) and derives the seeded queue files from the
plugin manifest's `agents` field. A plugin with just `workflow.cfg` and
`workflow.sh` is registrable end-to-end; you never have to ship a template
directory alongside it.

**Registration failure modes are named.** If `create-project.sh` refuses
the type, the error identifies the concrete reason:

- **Scaffold status** — the plugin exists but its manifest still says
  `status = scaffold`:

  ```
  ERROR: workflow type 'acme-deploy' cannot be used: plugin status is 'scaffold' — flip status to 'ready' after implementing all hooks
  ```

  Return to Step 4 and flip the status.

- **Unknown type** — no plugin with that name is discovered under the
  workflows root. The error lists the ready types the registry did
  find, so you can see what is actually installed:

  ```
  ERROR: unknown workflow type 'acme-deploy'; discovered ready types are: document release testing-only
  ```

  If no ready plugins are installed at all, the message points at the
  workflows directory instead:

  ```
  ERROR: unknown workflow type 'acme-deploy'; no ready workflow plugins found under /home/you/pgai_agent_kanban/workflows
  ```

  Confirm the plugin directory name matches the `--workflow-type`
  argument exactly, and that you completed Step 4 (`status = ready`)
  so the registry sees the plugin as installed.

Then drop a requirements file. The intake filename is the frozen public
contract: `vX.Y.Z-slug.md` (for `semver` semantics) or the same shape
where the version is a label string (for `label` semantics). For
`acme-deploy` with `semver` semantics:

```bash
cp your-requirement.md \
    $KANBAN_ROOT/projects/<name>/requirements/v0.1.0-first-deploy.md
```

On the next pipeline tick:

- Discovery picks up the requirement.
- PM decomposes it using the roster `wf_agents` returns.
- Working agents run tasks in per-task worktrees consistent with
  `wf_git_mode`.
- The workflow finalizes according to `wf_finalize` (`tag`, `publish`,
  or `report`).

If the pipeline routes the requirement to BLOCKED, read the block
reason. The three common causes:

1. `status = scaffold` in the manifest — go back to Step 4 and flip it.
   `create-project.sh` catches this at registration time with the
   scaffold error above; the pipeline catches it at routing time with
   the same reason.
2. Typo in the manifest — `git_mode = rwx` or similar. Re-run
   `validate-workflow.sh`; it will name the offending value.
3. The project's `workflow_type` in `project.cfg` does not match the
   plugin directory name.

### That is the whole live-operator path

Five steps, zero engine edits, no dev tree, no repo access. Your
`acme-deploy/` directory lives in `$KANBAN_ROOT/workflows/` and survives
`upgrade.sh --force` untouched forever — as long as you do not name it
`release`, `document`, or `testing-only`.

---

## Part 2 — Contributor path (dev tree, tests, ship with the framework)

You want the new workflow type to ship *with* pgai-agent-kanban itself,
so every operator gets it after the next upgrade. This means you write
the plugin inside the source tree, add tests, and drop a requirement
that runs the plugin through the framework's own release lifecycle.

The mechanics of authoring the plugin are identical to Part 1. The
differences are location, tests, and the requirement to ship.

### Step 1 — Work in the dev tree

Clone the source, activate the environment, and put yourself inside
the dev tree.

```bash
git clone git@github.com:purdygood-ai/pgai-agent-kanban.git
cd pgai-agent-kanban
```

The dev-tree layout mirrors the live-install layout with a `team/`
prefix:

```
team/
├── scripts/                          # dev-tree copy of $KANBAN_ROOT/scripts/
├── workflows/                        # dev-tree copy of $KANBAN_ROOT/workflows/
│   ├── release/
│   ├── document/
│   └── testing-only/
└── pgai_agent_kanban/workflows/
    └── create_new_workflow.py        # the generator (library + CLI)
```

`install.sh` copies `team/*` into `$KANBAN_ROOT/*` on install, dropping
the prefix. Edits you make under `team/` land in live installs after
upgrade.

### Step 2 — Scaffold under `team/workflows/`

Run the generator from the dev tree. With `PGAI_AGENT_KANBAN_ROOT_PATH`
unset (or set to a throwaway path outside the repo) and the CWD inside
the dev tree, the generator resolves the output root as
`<git-root>/team/workflows/`.

```bash
unset PGAI_AGENT_KANBAN_ROOT_PATH   # only if it is currently set
python3 -m team.pgai_agent_kanban.workflows.create_new_workflow \
    --name my-new-type \
    --description "One-line description" \
    --version-semantics semver \
    --git-mode rw \
    --finalize tag \
    --agents pm,coder,tester,cm
```

Alternatively, be explicit about the output root:

```bash
python3 -m team.pgai_agent_kanban.workflows.create_new_workflow \
    --name my-new-type \
    --workflows-dir team/workflows \
    <other flags>
```

The generator writes `team/workflows/my-new-type/` with the same three
files Part 1 gets.

**The shipped-name rule still applies.** You cannot use `release`,
`document`, or `testing-only`. The generator refuses on the dev tree
just as it refuses on the live install. This is not a Part 1 quirk — it
is the naming rule, and it is a public contract.

### Step 3 — Implement the hooks

Same as Part 1 Step 2. Edit `team/workflows/my-new-type/workflow.sh`,
replace each stub with real logic, and use `team/workflows/testing-only/`
and `team/workflows/release/` as reference implementations.

Because you are in a git tree, commit as you go. The kanban's own tests
run against your working tree, not a separate build product.

### Step 4 — Add tests

The framework has three test surfaces the plugin must engage:

1. **Contract tests** — validation the plugin passes structural
   validation. The shared library `team/scripts/lib/workflow-contract.sh`
   exposes `wfc_check_all <plugin_dir> <plugin_name>`; wrap it in a
   pytest fixture that points at `team/workflows/my-new-type/` and
   asserts the return code is 0.

2. **Behavior tests** — the plugin's hooks produce the expected outputs.
   Source `workflow.sh` in a bash test harness and assert each hook's
   stdout matches the manifest's capability values. Example (the
   testing-only plugin ships this pattern already):

   ```bash
   source team/workflows/my-new-type/workflow.sh
   test "$(wf_git_mode)"   = "rw"
   test "$(wf_finalize)"   = "tag"
   test "$(wf_agents)"     = "pm,coder,tester,cm"
   ```

3. **End-to-end litmus** — run a fixture requirement through the pipeline
   in a sandbox `$KANBAN_ROOT`. The plugin's diff must touch only the new
   `team/workflows/my-new-type/` directory, its tests, and its docs. If
   the plugin forced you to edit `team/scripts/wake/claude.sh`,
   `team/scripts/wake/codex.sh`, `team/scripts/lib/discovery.sh`,
   `team/scripts/cm/*`, or any dashboard/metrics file, the abstraction is
   incomplete and the plugin is failing the litmus test. Stop and fix the
   engine to expose a capability flag instead.

Run the full test suite:

```bash
team/scripts/run-unit-tests.sh
team/scripts/run-integration-tests.sh
```

Both must pass.

### Step 5 — Validate on a live-install layout

The generator is root-aware, so its output layout on the dev tree
already matches what will land in the live install after `install.sh`
copies `team/` in. Verify the plugin passes `validate-workflow.sh`
without any dev-tree crutches:

```bash
team/scripts/validate-workflow.sh --type my-new-type --workflows-dir team/workflows
```

Expected: `PASS`. If it fails, fix and re-run. Contract-test coverage
above should have caught this already; if it did not, that is a
signal your contract tests are incomplete.

### Step 6 — Drop a requirement to ship it

You ship the new workflow type by writing a requirements document that
the kanban itself will pick up. This is the release-workflow path; the
requirements doc gets a real semver bump, PM decomposes it, and the
plugin ships in a tagged release.

Place a file under `projects/pgai-agent-kanban/requirements/`:

```
projects/pgai-agent-kanban/requirements/v1.2.0-add-my-new-type.md
```

The document declares:

- The plugin's manifest values (name, capabilities, description).
- The tests you added and where they live.
- Any docs updates (this file may need a note; ARCHITECTURE.md may need
  a bullet in the Workflow Types section).
- Acceptance criteria — at minimum, `validate-workflow.sh --type
  my-new-type` passes on a fresh install.

Follow the requirements format the pipeline expects; existing requirements
under `projects/pgai-agent-kanban/requirements/` are the reference.

Once the requirement is dropped, the pipeline runs it through the normal
release lifecycle: PM decomposes into CODER + WRITER + TESTER + CM tasks,
CM opens an RC, CODER and WRITER work on the RC branch, TESTER verifies,
CM tags and pushes. When the release ships, every operator running
`upgrade.sh` picks up your new workflow type.

### Step 7 — Verify the plugin survives upgrade

The upgrade-survival contract cuts both ways. Custom types under
`$KANBAN_ROOT/workflows/<custom>/` survive an upgrade untouched — that
is Goal 9 of v1.1.0. Shipped types are refreshed in place. Your new
shipped plugin lives in the source tree at `team/workflows/my-new-type/`
and lands in the live install at `$KANBAN_ROOT/workflows/my-new-type/`
via the overlay copy on install and every subsequent upgrade.

The framework's own upgrade-survival tests verify this by running
`upgrade.sh --force` against a sandbox root that contains both a
shipped plugin and a fixture custom plugin, then asserting the shipped
one refreshed while the custom one is byte-identical. If your new
shipped plugin ships in the same release that introduced upgrade
survival, add your plugin's name to the shipped-plugin refresh fixture
so the test proves it comes across on upgrade.

### That is the contributor path

The plugin ships in the tree, the tests prove it works, and the release
lifecycle carries it to every operator. Two constraints stay the same
as Part 1: use org-prefixed or otherwise unique names to avoid future
shipped-name collisions, and keep the plugin's diff scoped to
`team/workflows/<name>/`, its tests, and its docs. Any engine edit is a
signal the abstraction has a gap; file that gap as its own ticket.

---

## Reference

- **Worked tutorial** — [tutorial-first-workflow.md](tutorial-first-workflow.md)
  builds a deliberately silly `bulletin` type end-to-end in ~15
  minutes (scaffold → hooks → validate → ready → run → delete).
  Start there if this is your first type.
- **Manifest schema** — see the "Concepts" section above for the full
  `workflow.cfg` field list.
- **Hook interface** — see the "Concepts" section above for the eight
  `wf_*` hooks and their responsibilities.
- **`validate-workflow.sh`** — `--help` prints the flag list and the
  four contract checks in detail.
- **Generator** — `python3 -m pgai_agent_kanban.workflows.create_new_workflow
  --help` (live install) or `python3 -m
  team.pgai_agent_kanban.workflows.create_new_workflow --help` (dev
  tree) prints the flag list and defaults.
- **Shipped-plugin examples** — `$KANBAN_ROOT/workflows/release/`,
  `$KANBAN_ROOT/workflows/document/`, `$KANBAN_ROOT/workflows/testing-only/`.
- **Workflows front door** —
  [../team/workflows/README.md](../team/workflows/README.md) covers the
  two-layer plugin model, discovery and fail-closed behaviour, and the
  minimal-vs-rich distinction.
- **Pipeline schema** —
  [../team/workflows/SCHEMA.md](../team/workflows/SCHEMA.md) is the
  field-by-field reference for `pipeline.yaml` (rich plugins only).
- **Public contract** — [public-contract.md](public-contract.md) covers
  the workflows-dir survival guarantee and the shipped-name rule.
- **Architecture** — [ARCHITECTURE.md](../ARCHITECTURE.md) covers plugin
  discovery, fail-closed routing, and how the engine reads capabilities
  rather than type names.
