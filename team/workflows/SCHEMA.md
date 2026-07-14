# Pipeline YAML Schema

This document defines the `pipeline.yaml` format used by pgai-agent-kanban workflow-type plugins. A `pipeline.yaml` file describes a complete PM decomposition pipeline: what inputs it needs, which agents participate, what steps run in order, and what output is produced.

`pipeline.yaml` is the optional Layer 2 of a workflow-type plugin. Layer 1 — the `workflow.cfg` manifest and the `workflow.sh` hooks — is required for every plugin and is described in [README.md](README.md), the workflows-directory front door. Read the README first if you have not yet seen the two-layer plugin model; this document is the field-by-field reference for authoring the pipeline file itself.

A developer should be able to write a new `pipeline.yaml` using only this document.

## File Location

A plugin's `pipeline.yaml` lives inside the plugin directory:

```
$KANBAN_ROOT/workflows/<type>/pipeline.yaml
```

PM constructs the path from the plugin's type string (there is no per-type constant). The plugin's `name` field in its `workflow.cfg` manifest must match the directory name `<type>` exactly. Case-sensitive.

A plugin without a `pipeline.yaml` decomposes via the simple `wf_agents` path — one task per roster entry in order. This is the documented default for types that do not need a multi-step pipeline; see [README.md](README.md) for the minimal-vs-rich distinction.

---

## Full Schema

```yaml
name: <workflow-type-name>
description: <one-line description>

inputs:
  required:
    - <filename>
  optional:
    - <filename>
  context:
    - <semantic-name>

agents:
  primary: <ROLE>
  <named-purpose>: <ROLE>

pipeline:
  - role: <ROLE>
    name: <step-name>
    operation: <op-name>
    foreach: <reference>
    deliverable: <output-path>
    inputs: [<file>, ...]
    optional: <bool>
    branch: <branch-pattern>
    branch_pattern: <pattern>
    target_branch: <branch-name>
    autonomous_criterion: <required|optional|none>
    when: <predicate-name>

outputs:
  format: <type|list>
  location: <path-pattern>

versioning: <auto-increment|from_requirements|none>
```

---

## Top-Level Fields

### `name` (required, string)

The workflow type name. Must match the YAML filename exactly. Lowercase, hyphenated, alphanumeric.

Examples: `release`, `document`

### `description` (required, string)

One-line human-readable description of what this workflow does.

### `inputs` (required, object)

Declares what the workflow expects as input.

| Sub-field  | Type          | Required | Description                                        |
|------------|---------------|----------|----------------------------------------------------|
| `required` | list of strings | yes    | Filenames that must exist before the workflow runs  |
| `optional` | list of strings | no     | Filenames that may exist (prior versions, guides)   |
| `context`  | list of strings | no     | Semantic context names resolved at runtime          |

Context names are not filenames. They are resolved by the runtime to actual paths or values. Common context names:

- `dev_tree_path` -- path to the development tree (e.g., `/home/rocky/develop/pgai-agent-kanban`)
- `git_repo_url` -- SSH or HTTPS URL of the git repository

### `agents` (required, object)

Maps named purposes to roles. Every workflow must declare a `primary` agent.

```yaml
agents:
  primary: CODER
  documentation: WRITER
  review: TESTER
  manage: CM
```

The key is a semantic label (freeform). The value must be a valid role.

**Valid roles:** `PM`, `CODER`, `WRITER`, `TESTER`, `CM`, `PO`, `any`

### `pipeline` (required, list)

Ordered list of pipeline steps. Must contain at least one step. Steps execute in order. See **Pipeline Step Fields** below.

### `outputs` (required, object)

Describes the final output of the workflow.

| Sub-field  | Type   | Required | Description                                      |
|------------|--------|----------|--------------------------------------------------|
| `format`   | string or list | yes | Output format(s): `markdown`, `pdf`, `git_tag`, etc. |
| `location` | string | yes      | Path pattern for output. May use reference variables. |

### `versioning` (required, string)

How versions are assigned. One of:

| Value               | Behavior                                                    |
|---------------------|-------------------------------------------------------------|
| `auto-increment`    | Version auto-incremented from PROJECT.md's `Next Version` field |
| `from_requirements` | Version taken from the requirements document                 |
| `none`              | No versioning applied                                        |

---

## Pipeline Step Fields

Each entry in the `pipeline` list is an object with the following fields.

### Required Fields

| Field  | Type   | Description                                           |
|--------|--------|-------------------------------------------------------|
| `role` | string | Agent role for this step. One of: `PM`, `CODER`, `WRITER`, `TESTER`, `CM`, `PO`, `any` |
| `name` | string | Step name. Used in task naming (e.g., `open-rc`, `draft`, `verify`). Lowercase, hyphenated. |

### Optional Fields

| Field                  | Type            | Default | Description                                              |
|------------------------|-----------------|---------|----------------------------------------------------------|
| `operation`            | string          | (none)  | Special operation name. See **Special Operations** below. |
| `foreach`              | string          | (none)  | Reference that generates one ticket per item.             |
| `deliverable`          | string          | (none)  | Output path for this step's deliverable.                  |
| `inputs`               | list of strings | (none)  | Input files or references consumed by this step.          |
| `optional`             | boolean         | `false` | Whether this step can be skipped without failing the pipeline. |
| `branch`               | string          | (none)  | Branch pattern for work done in this step.                |
| `branch_pattern`       | string          | (none)  | Pattern for branch creation operations (used with `create_branch`). |
| `target_branch`        | string          | (none)  | Target branch for merge or tag operations (used with `tag_and_push`). |
| `autonomous_criterion` | string          | (none)  | Whether autonomous criterion check is required for this step. Values: `required`, `optional`, `none`. |
| `when`                 | string          | (none)  | Predicate name that gates this step. Step is skipped when the predicate evaluates to false at runtime. See **`when` Predicate Names** below. |

---

## `when` Predicate Names

The `when` field accepts a single predicate name. The materializer evaluates the predicate against runtime state and skips the step if the predicate is false.

Two predicate names are supported:

| Predicate                  | True when                                                                 | False when                                        |
|----------------------------|---------------------------------------------------------------------------|---------------------------------------------------|
| `foreach_was_used`         | The most recent `foreach` step in the pipeline produced at least one ticket. | No `foreach` step ran, or it produced zero tickets. |
| `review_agent_configured`  | The project declares a `review` agent in its `agents` block.              | No `review` agent is declared for this project.   |

### Usage Pattern

Use `when: foreach_was_used` on steps that are only meaningful when foreach tickets were generated (for example, an integration step that assembles per-section drafts). Use `when: review_agent_configured` on steps that should be skipped for projects where no reviewer is declared.

```yaml
pipeline:
  - role: WRITER
    name: section-draft
    foreach: outline.sections
    deliverable: section-{section_name}.md
    when: foreach_was_used        # skipped if no sections discovered

  - role: WRITER
    name: integrate
    deliverable: integrated.md
    when: foreach_was_used        # skipped if no sections to integrate

  - role: TESTER
    name: review
    deliverable: review-report.md
    when: review_agent_configured # skipped if no review agent declared
```

Steps without a `when` field always run.

---

## Special Operations

Special operations are built-in behaviors invoked by setting the `operation` field on a pipeline step. They perform side effects beyond normal task execution.

### `create_branch`

Creates a git branch. Used by release workflows to open RC branches.

**Required step fields:** `branch_pattern`

```yaml
- role: CM
  name: open-rc
  operation: create_branch
  branch_pattern: rc/{version}
```

The `branch_pattern` supports reference variables. In the example above, `{version}` is replaced with the resolved version string (e.g., `rc/v0.16.0`).

### `tag_and_push`

Tags the current state and pushes to a target branch. Used for release finalization.

**Required step fields:** `target_branch`

```yaml
- role: CM
  name: release
  operation: tag_and_push
  target_branch: main
```

This merges the RC branch into the target branch, tags it with the version, and pushes.

### `open_doc`

Creates the artifacts directory structure for non-release workflows. Filesystem only -- no git operations.

```yaml
- role: CM
  name: open-doc
  operation: open_doc
```

This operation:

1. Validates PROJECT.md exists and is well-formed
2. Increments `Next Version` in PROJECT.md
3. Creates `artifacts/<project-name>/v<N>/{input,working,output}`
4. Copies required inputs into `v<N>/input/` if they exist in staging

### `finalize`

Packages intermediate output from the working directory into final form in the output directory.

```yaml
- role: CM
  name: finalize
  operation: finalize
```

This operation:

1. Reads PROJECT.md to determine output formats
2. Reads the latest deliverable from `working/`
3. Converts to each requested output format (markdown, pdf, etc.)
4. Places final output in `output/`
5. Writes `output/SUMMARY.md`

---

## Reference Syntax

Reference variables use curly-brace syntax: `{variable_name}`. They are resolved at runtime against the current workflow context.

### Available Variables

| Variable           | Source                                | Example Value                |
|--------------------|---------------------------------------|------------------------------|
| `{version}`        | Version string from requirements or auto-incremented from PROJECT.md | `v0.16.0`, `3` |
| `{task_id}`        | Unique task identifier assigned by PM | `CLAUDE-CODER-20260428-050`  |
| `{deliverable_name}` | Name of the deliverable output file | `sam-and-rusty`              |
| `{section_name}`   | Section identifier within a deliverable | `chapter-1`, `outline`     |
| `{project_name}`   | Project name from PROJECT.md          | `kids-story-creek`           |

### Where Variables Are Used

Variables may appear in:

- `branch_pattern` -- e.g., `rc/{version}`
- `branch` -- e.g., `feature/{task_id}`
- `deliverable` -- e.g., `working/{section_name}.md`
- `outputs.location` -- e.g., `artifacts/{project_name}/v{version}/output/`

Unresolvable variables at runtime produce a clear error with the variable name and the context in which resolution was attempted.

---

## Versioning Modes

### `auto-increment`

Used by non-release workflows (document). The version number is an integer stored in PROJECT.md under `## Next Version`. The `open_doc` operation reads this value, uses it, and increments it for the next run.

Version values in path patterns appear as integers: `v1`, `v2`, `v3`.

### `from_requirements`

Used by release workflows. The version string comes from the requirements document (e.g., `v0.16.0`). No auto-increment occurs.

### `none`

No versioning. Output paths must not use `{version}`.

---

## PROJECT.md Format

Every non-release project has a `PROJECT.md` file at:

```
$KANBAN_ROOT/artifacts/<project-name>/PROJECT.md
```

### Required Format

```markdown
# Project: <name>

## Workflow Type
<workflow-type-name>

## Description
<one-paragraph description>

## Output Name
<filename-base>

## Output Formats
- markdown
- pdf

## Priority
<integer>

## Next Version
<integer>
```

### Field Descriptions

| Field           | Type    | Description                                              |
|-----------------|---------|----------------------------------------------------------|
| `Project`       | string  | Project name (in the H1 heading). Must match directory name. |
| `Workflow Type`  | string  | Name of the workflow YAML to use (e.g., `creative`, `document`). |
| `Description`   | string  | One-paragraph description of the project.                 |
| `Output Name`   | string  | Base filename for the final deliverable (no extension).   |
| `Output Formats` | list   | Supported output formats. Each on its own line with `- ` prefix. |
| `Priority`      | integer | Scheduling priority. Lower numbers are higher priority.   |
| `Next Version`  | integer | Next version number to use. Incremented by `open_doc`.    |

### Required Fields Per Workflow Type

PROJECT.md is validated against the workflow YAML's requirements at runtime. The required fields depend on the workflow type:

**All workflow types require:** `Workflow Type`, `Description`, `Next Version`

**`document` workflow additionally requires:** `Output Name`, `Output Formats`, `Priority`

**`release` workflow:** Does not use PROJECT.md. Release metadata comes from requirements documents.

### Parsing Rules

PROJECT.md parsing follows the liberal regex principle:

- Variations in spacing around `##` headings are accepted
- Trailing whitespace is ignored
- Empty lines between sections are accepted
- Field names are matched case-insensitively
- The parser accepts both `Output Format` and `Output Formats` (singular or plural)

### Project Name Convention

Project names must be lowercase, hyphenated, alphanumeric.

Regex: `^[a-z0-9][a-z0-9-]*$`

Examples: `kids-story-creek`, `claude-code-whitepaper`, `team-sop-v2`

---

## Artifacts Directory Structure

Non-release workflows write deliverables to a versioned directory tree:

```
$KANBAN_ROOT/artifacts/<project-name>/
  PROJECT.md
  v1/
    input/        -- requirements and (optional) prior version
    working/      -- agent intermediate state (outlines, drafts)
    output/       -- the final deliverable
  v2/
    input/
    working/
    output/
```

- `input/` contains what was asked for, plus any prior version used as reference.
- `working/` contains agent intermediate files. Pipeline steps write their deliverables here.
- `output/` contains the final packaged deliverable after `finalize`.
- Empty version directories are valid (a run might fail before producing output).
- Version directories use plain integers: `v1/`, `v2/`, `v3/` -- no zero-padding.

---

## Worked Example 1: Release Workflow

The `release` workflow expresses the existing software release lifecycle as YAML. It uses git branches, code implementation, testing, and a final tag-and-push.

```yaml
name: release
description: Software release with git RC branch lifecycle

inputs:
  required:
    - requirements.md
  context:
    - dev_tree_path
    - git_repo_url

agents:
  primary: CODER
  documentation: WRITER
  review: TESTER
  manage: CM

pipeline:
  - role: CM
    name: open-rc
    operation: create_branch
    branch_pattern: rc/{version}

  - role: CODER
    name: implement
    foreach: requirements.tickets
    branch: feature/{task_id}

  - role: TESTER
    name: verify
    deliverable: report.md
    autonomous_criterion: required

  - role: CM
    name: release
    operation: tag_and_push
    target_branch: main

outputs:
  format: git_tag
  location: refs/tags/

versioning: from_requirements
```

### How This Pipeline Executes

1. **open-rc** (CM): Creates the RC branch `rc/v0.16.0` from the prefixed main branch. The `create_branch` operation handles the git checkout and push.

2. **implement** (CODER): The `foreach: requirements.tickets` directive generates one ticket per requirement item. Each ticket gets its own feature branch (`feature/CLAUDE-CODER-20260428-050`, etc.), branched from the RC. The coder implements, commits, and merges back into the RC branch.

3. **verify** (TESTER): Runs the test suite against the RC branch. Produces `report.md` as its deliverable. The `autonomous_criterion: required` field means the tester must explicitly verify that the build ran without manual intervention.

4. **release** (CM): The `tag_and_push` operation squashes the RC branch into the prefixed main branch (one squash), runs the post-squash fidelity gate, stamps and commits release notes, tags the release, and pushes.

### What This Workflow Does Not Use

- No `open_doc` or `finalize` operations (those are for non-git workflows).
- No PROJECT.md (version comes from requirements).
- No artifacts directory (output is a git tag, not a file).

---

## Worked Example 2: Document Workflow

The `document` workflow handles all non-release deliverables: short-form content (stories, blog posts, poems) and long-form structured documents (whitepapers, SOPs, architecture docs). A single pipeline covers both cases using `when:` conditions to gate the long-form-only steps.

```yaml
name: document
description: "Unified document workflow supporting both short-form and long-form documents; long-form steps are gated by when: conditions."

inputs:
  required:
    - brief.md
  optional:
    - style_guide.md
    - reference_material.md
  context:
    - project_context_path

agents:
  primary: WRITER
  review: TESTER
  manage: CM

pipeline:
  - role: CM
    name: open-doc
    operation: open_doc

  - role: WRITER
    name: outline
    deliverable: outline.md

  # Short-form only: single full-document draft from the outline.
  - role: WRITER
    name: draft
    deliverable: draft.md
    inputs: [outline.md]

  # Long-form only: one drafting ticket per section.
  - role: WRITER
    name: section-draft
    foreach: outline.sections
    deliverable: section-{section_name}.md
    when: foreach_was_used

  # Long-form only: merge all section drafts into one document.
  - role: WRITER
    name: integrate
    deliverable: integrated.md
    inputs:
      - outline.md
      - "section-{section_name}.md"
    when: foreach_was_used

  - role: WRITER
    name: polish
    deliverable: polished.md

  # Optional review — only when a review agent is declared.
  - role: TESTER
    name: review
    deliverable: review-report.md
    inputs: [polished.md]
    autonomous_criterion: required
    when: review_agent_configured

  - role: CM
    name: finalize
    operation: finalize

outputs:
  format: document
  location: artifacts/

versioning: auto-increment
```

### Short-Form Pipeline (no sections in outline)

When the brief produces no sections, `foreach_was_used` is false and the materializer skips `section-draft` and `integrate`. The effective step sequence is:

```
open-doc → outline → draft → polish → [review] → finalize
```

### Long-Form Pipeline (sections in outline)

When the outline yields sections, `foreach_was_used` is true. The materializer generates one `section-draft` ticket per section and runs the `integrate` step afterward. The effective step sequence is:

```
open-doc → outline → section-draft (×N) → integrate → polish → [review] → finalize
```

The `[review]` step appears only when `review_agent_configured` is true for the project.

### Example PROJECT.md for a Short-Form Document Project

```markdown
# Project: kids-story-creek

## Workflow Type
document

## Description
A 1000-1500 word illustrated kids story about Sam and Rusty's creek walk, for ages 5-8.

## Output Name
sam-and-rusty

## Output Formats
- markdown

## Priority
1

## Next Version
1
```

### Example PROJECT.md for a Long-Form Document Project

```markdown
# Project: claude-code-whitepaper

## Workflow Type
document

## Description
A technical whitepaper on autonomous code generation using Claude Code and the pgai-kanban framework.

## Output Name
claude-code-whitepaper

## Output Formats
- markdown
- pdf

## Priority
2

## Next Version
1
```

---

## Writing a New Workflow YAML

Follow these steps to define a new workflow type.

### Step 1: Choose a Name

Pick a lowercase, hyphenated name for your workflow. This becomes both the filename (`<name>.yaml`) and the `name` field.

### Step 2: Declare Inputs

List what the workflow needs to start. Every workflow needs at least one required input (typically `requirements.md`). Optional inputs are files that enhance the output if present. Context entries are runtime-resolved values like paths or URLs.

### Step 3: Declare Agents

Map semantic purposes to roles. Always include `primary`. Add others as needed for your pipeline steps.

### Step 4: Design the Pipeline

Write an ordered list of steps. Each step needs a `role` and a `name`. Add optional fields as needed:

- Use `operation` for built-in side effects (git operations, directory setup, packaging).
- Use `foreach` when a step should generate multiple tickets from a list.
- Use `deliverable` to declare what file a step produces.
- Use `inputs` to declare what files a step reads.
- Use `optional: true` for steps that can be skipped.
- Use `when: <predicate>` to gate a step on a runtime condition. Supported predicates: `foreach_was_used`, `review_agent_configured`.

### Step 5: Declare Outputs

Specify the output format and location pattern.

### Step 6: Choose Versioning

- `auto-increment` if the workflow manages its own version counter via PROJECT.md.
- `from_requirements` if the version comes from a requirements document (typically release workflows).
- `none` if versioning does not apply.

### Step 7: Validate

Place the file at `team/workflows/<type>/pipeline.yaml` — inside the plugin directory, alongside `workflow.cfg` and `workflow.sh`. The workflow loader validates:

- `name` matches filename
- `pipeline` is non-empty
- Every step's `role` is a valid role
- `versioning` is one of the three allowed values
- Special operations are from the known set (`create_branch`, `tag_and_push`, `open_doc`, `finalize`)
- Reference variables in patterns are resolvable

### Liberal Parsing

The loader follows the liberal regex principle. It accepts minor variations:

- `output:` and `outputs:` are both accepted (canonical form is `outputs:`)
- Spacing variations in YAML are tolerated per standard YAML rules
- String values may be quoted or unquoted

---

## Validation Rules Summary

| Rule                           | Error if violated                                 |
|--------------------------------|---------------------------------------------------|
| `name` matches filename        | "Workflow name 'X' does not match filename 'Y'"   |
| `pipeline` non-empty           | "Pipeline must contain at least one step"          |
| Step `role` is valid           | "Unknown role 'X' in step 'Y'. Valid: PM, CODER, WRITER, TESTER, CM, PO, any" |
| `versioning` is valid          | "Unknown versioning mode 'X'. Valid: auto-increment, from_requirements, none" |
| `operation` is known           | "Unknown operation 'X' in step 'Y'. Valid: create_branch, tag_and_push, open_doc, finalize" |
| `create_branch` has `branch_pattern` | "Step 'X' uses create_branch but missing branch_pattern" |
| `tag_and_push` has `target_branch`   | "Step 'X' uses tag_and_push but missing target_branch"   |
| Reference variables resolvable | "Unresolvable variable '{X}' in field 'Y' of step 'Z'"  |
| `when` value is known predicate | "Unknown when predicate 'X' in step 'Y'. Valid: foreach_was_used, review_agent_configured" |

---

## Quick Reference

### Valid Roles

`PM`, `CODER`, `WRITER`, `TESTER`, `CM`, `PO`, `any`

### Special Operations

`create_branch`, `tag_and_push`, `open_doc`, `finalize`

### Reference Variables

`{version}`, `{task_id}`, `{deliverable_name}`, `{section_name}`, `{project_name}`

### `when` Predicates

`foreach_was_used`, `review_agent_configured`

### Versioning Modes

`auto-increment`, `from_requirements`, `none`

### Project Name Regex

`^[a-z0-9][a-z0-9-]*$`
