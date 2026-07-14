# Workflows — The Plugin System

This directory is the pgai-agent-kanban plugin surface. Every workflow type
the engine can run — software releases, versioned documents, read-only test
runs, and any type an operator or contributor adds — is a plugin directory
under this folder. This README is the written contract for how those
plugins are shaped, how the engine finds them, and what a plugin author
must produce.

If you are here to *author* a new workflow type, read this file, then jump
to the authoring walkthrough at
[../../docs/creating-a-workflow.md](../../docs/creating-a-workflow.md).
If you are here to write the `pipeline.yaml` file for a plugin that needs
one, read this file, then jump to [SCHEMA.md](SCHEMA.md).

---

## The two layers

A workflow-type plugin is made of two layers that speak to two different
parts of the engine.

**Layer 1 — the engine layer: manifest and hooks.** Every plugin declares
its capabilities in `workflow.cfg` (the manifest) and implements a small
set of bash functions in `workflow.sh` (the hooks). The engine reads
capability values from the manifest and calls the hooks to make routing
decisions: which git mode to use for a task, how the workflow finalizes,
which agent roster PM decomposes with, how the dashboard should render
items of this type. This layer is **required for every plugin**. Without
it the engine cannot know the plugin exists, cannot route work to it, and
cannot decide anything about the tasks it produces.

**Layer 2 — the PM layer: the optional `pipeline.yaml`.** A plugin may
additionally ship a `pipeline.yaml` file that declares a richer,
multi-step decomposition — named steps in an ordered pipeline, with
`foreach` fan-outs, `when:` predicates, deliverables, and per-step branch
patterns. When PM decomposes a requirement for this workflow type, it
loads `pipeline.yaml` and materialises the tasks the pipeline describes.
When a plugin does not ship a `pipeline.yaml`, PM falls back to the
**simple path**: it reads the agent roster from the plugin's `wf_agents`
hook and materialises a single-track decomposition, one task per roster
entry in order.

Layer 1 tells the engine what a plugin *is*. Layer 2 tells PM how to
*decompose* a requirement for it. The two are decoupled: a plugin may
have Layer 1 without Layer 2, but never Layer 2 without Layer 1.

---

## The directory-is-the-plugin rule

A workflow-type plugin is a single directory under `workflows/`. That
directory is the plugin — its name, its capabilities, its hooks, and (if
it needs one) its decomposition pipeline. Nothing about the plugin lives
outside the directory. No engine table maps plugin names to code paths.
No PM constants enumerate the type names.

The layout of a plugin directory:

```
workflows/<type>/
├── workflow.cfg     # the manifest (required)
├── workflow.sh      # the hooks (required)
└── pipeline.yaml    # the PM decomposition (optional)
```

The directory name is the plugin name. The manifest's `name` field must
match the directory name exactly. When a project's `project.cfg` sets
`workflow_type = <name>`, the engine finds the plugin by that name and
only that name.

To add a new workflow type, create a new directory under `workflows/`
with a manifest, hooks, and (if the type needs it) a pipeline. To
remove one, delete the directory. Nothing else changes.

---

## Discovery and fail-closed behaviour

The engine and PM never enumerate a closed set of workflow types. They
resolve the plugin by the type string a project or requirement supplied,
by looking for `workflows/<type>/`. There are two safety rails.

**Unknown type — refused.** If a project's `workflow_type` names a
directory that does not exist under `workflows/`, the engine cannot find
a manifest or hooks to consult, and work does not route. The failure is
loud (the offending type name appears in the error) rather than silent.
Discovery does not fall back to a "default" workflow when the named one
is missing; the operator gets a diagnosable error instead of a run
against the wrong plugin.

**Scaffold status — refused.** A plugin's manifest declares
`status = scaffold` while the plugin is still being authored, and
`status = ready` once the hooks are implemented and validated. The
engine treats `scaffold` as fail-closed: any requirement pointed at a
`scaffold` plugin routes to BLOCKED with the plugin name in the reason.
Only the exact literal `ready` opens the plugin for engine use — a typo
(`redy`, `Ready`, `read`) also fail-closes, because the manifest field
is the operator's signed statement that the plugin is complete.

**Pipeline errors are hard, not fallbacks.** If a plugin ships a
`pipeline.yaml` and PM cannot parse or validate it, PM exits with an
error — it does not silently fall back to the simple `wf_agents` path.
The simple path applies only when there is no `pipeline.yaml` at all.
This matters: an operator who intended a rich decomposition and shipped
a malformed pipeline gets the actual problem in their face, not a
mysteriously simpler decomposition than they authored.

---

## The capability vocabulary

The engine reads five capability values from the plugin to make its
routing decisions. Four come from the manifest's `[capabilities]`
section; one comes from a hook return.

| Capability | Where declared | Allowed values | What the engine does with it |
|---|---|---|---|
| `version_semantics` | manifest field | `semver`, `label`, `none` | Decides whether the requirement's version enters `release-state.md`, whether the version ceiling applies, and whether the patch-lane resolver runs. `semver` participates in the release lifecycle; `label` names the artifact without semver machinery; `none` disables version handling. |
| `git_mode` | manifest field | `none`, `ro`, `rw` | Decides how per-task worktrees are built. `none` = no worktree; `ro` = detached read-only at the requirement's named ref; `rw` = a writable feature-branch worktree, the classic release pattern. Working agents never fetch, pull, or push regardless of `git_mode`; CM remains the sole origin-toucher. |
| `finalize` | manifest field | `tag`, `publish`, `report` | Decides how the workflow completes. `tag` = git tag on main; `publish` = write a versioned deliverable under `projects/<name>/artifacts/`; `report` = write a report file, no git tag, no publish. |
| `agents` | manifest field | comma-separated roster (e.g. `pm,coder,tester,cm`) | The ordered roster PM uses for the simple decomposition path when there is no `pipeline.yaml`. When a `pipeline.yaml` exists, each pipeline step names its own agent role and the manifest roster is not the driver. |
| dashboard render | `wf_dashboard_render` hook | `semver`, `label` | Decides how the dashboard renders items of this type. `semver` items are compared against the project's `last_released` (shipped-vs-open colouring); `label` items render by their own status (open → running → done) with no version comparison. |

The engine never reads the plugin's type name to make any of these
decisions. It reads the capability values, then acts. This is what makes
the plugin abstraction real: the engine's routing does not know or care
that a plugin is called `release` or `contoso-audit` — it only knows
what the plugin's capabilities say.

Two capability queries are also exposed as hooks — `wf_git_mode` and
`wf_finalize` — so a plugin can return a capability value programmatically
if it ever needs to. In today's plugins these hooks echo the manifest
value verbatim; the hook exists so future plugins can compute it if
their capability truly depends on runtime state.

---

## Minimal vs. rich: what a plugin actually needs

A workflow-type plugin is **minimal** when Layer 1 is enough — the
manifest declares the capabilities, the hooks implement them, and PM
decomposes a requirement into one task per agent in the manifest roster.
A plugin is **rich** when it additionally ships a `pipeline.yaml` for a
multi-step decomposition.

### Minimal type — `workflow.cfg` + `workflow.sh`, no `pipeline.yaml`

The shipped `testing-only` plugin is the reference for the minimal shape.
Its findings are filed on the target project's lane with full provenance;
see "Findings and cross-project filings" below. Its manifest declares
label versioning, read-only git, a `pm,tester` roster, and a `report`
finalize. There is no `pipeline.yaml`. When a testing-only requirement
lands, PM reads the roster from `wf_agents` and materialises a PM task and
a TESTER task — one task per agent in the declared order. That is the
entire decomposition.

A minimal plugin is the right shape when the workflow's job is a linear
"one deliverable per agent in the roster" shape and the ordering is
already captured by the roster itself. If your workflow fits that
description, do not add a `pipeline.yaml` — the simple path is the
documented default and it saves you an authoring step.

### Rich type — adds `pipeline.yaml`

The shipped `release` and `document` plugins are the references for the
rich shape. Both ship a `pipeline.yaml` that declares multiple named
steps in an ordered pipeline, with per-step branch patterns, `foreach`
fan-outs, and `when:` predicates gating optional steps. For `release`,
the pipeline is `open-rc → implement (foreach requirements.tickets) →
verify → release`. For `document`, the pipeline is
`open-doc → outline → draft | (section-draft ×N → integrate) → polish →
[review] → finalize`, with the middle branch selected by the
`foreach_was_used` predicate at materialisation time.

A rich plugin is the right shape when the workflow needs steps that are
not one-per-agent — a step that fans out over a list, a step that is
conditionally skipped, a step that produces a specific named deliverable
downstream steps consume, or a step with a per-step branch pattern
distinct from other steps in the same workflow.

The `pipeline.yaml` format itself — every field, every predicate, every
reference variable — is documented in [SCHEMA.md](SCHEMA.md). Nothing
about the pipeline schema is written here except that the file is
optional.

---

## Findings and cross-project filings

A `testing-only` run verifies a TARGET project's shipped artifact. When
the run finds a genuine defect, the TESTER files a bug **on the target
project's lane** (normal Path C rules) — not on the testing project's
own lane. This is by design: the defect belongs to the project that
ships the fix.

What this means in practice:

- **The audited tree is never touched.** Bug files live on the kanban
  side (`projects/<target>/bugs/`); the read-only worktree guarantee
  applies to the target's git tree and holds regardless of findings.
- **Filings carry provenance.** Every cross-project bug records its
  `Source Task` (the testing-only TESTER task) and `Source Report`
  (the finalize report), so the trail from finding to filing is one
  `show.sh` away.
- **The target lane may act on the filing autonomously.** An open bug
  on the target project is picked up by that project's normal
  discovery and may be bundled into a fix release without further
  operator action. This is the standard bug lifecycle — the filing is
  ordinary once it lands.
- **You control the target's response with the usual brakes.** To keep
  a target project quiescent while auditing it: `HALT` that project
  (`touch projects/<target>/HALT`) or set its version ceilings. The
  testing-only run itself needs neither — it never writes to the
  target lane's queues, only (possibly) to its bug ledger.

In short: a testing-only workflow can *diagnose* any project it is
pointed at, and *prescribes* onto that project's ledger; whether and
when the patient takes the medicine is governed by that project's own
configuration.

---

## Where to go next
- **First time? The worked tutorial** —
  [../../docs/tutorial-first-workflow.md](../../docs/tutorial-first-workflow.md)
  builds and runs a throwaway type in ~15 minutes.
- **Authoring a new plugin** (either as a live-install operator or as a
  contributor shipping the plugin with the framework):
  [../../docs/creating-a-workflow.md](../../docs/creating-a-workflow.md).
  That guide walks the full end-to-end sequence: scaffold, implement the
  eight hooks, validate, flip status to ready, drop a requirement.
- **Writing or editing a `pipeline.yaml`** (either for a shipped plugin
  or for a custom rich plugin): [SCHEMA.md](SCHEMA.md). That document is
  the full field-by-field reference for the pipeline format, including
  the `when:` predicates, the `foreach` mechanism, the special
  operations, and the reference-variable syntax.
- **The 1.x stable-surface guarantees** for custom plugins (upgrade
  survival, naming rule): the "Custom workflow types" section of
  [../../docs/public-contract.md](../../docs/public-contract.md).
