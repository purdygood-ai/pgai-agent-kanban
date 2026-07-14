# PO Briefs — the operator's guide

`scripts/po-agent.sh` turns a short human brief into a full requirements
document that PM decomposes into tickets. This page is for the operator
who is writing that brief. It covers the brief template, the three ways
to run `po-agent.sh`, the human-approval gate, and the batch-draft
workflow.

If you have never run PO before, start with the [60-second how-to](#60-second-how-to)
and come back for the depth when you need it. Command-flag reference for
every operator script lives in
[operator-commands.md](operator-commands.md).

---

## 60-second how-to

1. Copy the brief template:

   ```bash
   cp team/templates/agent/BRIEF-TEMPLATE.md /tmp/my-brief.md
   ```

2. Edit `/tmp/my-brief.md`. **Fill in `## Goal`, `## Target Version`,
   `## Version Bump Rationale`, `## Constraints`, and `## Context`.**
   Everything else has a working default.

3. Preview what would happen (no writes):

   ```bash
   scripts/po-agent.sh /tmp/my-brief.md --dry-run
   ```

4. Produce a reviewable draft without touching the pipeline:

   ```bash
   scripts/po-agent.sh /tmp/my-brief.md --output drafts/
   ```

   The final stdout line is `DRAFT: drafts/<target-version>-<slug>.md`.
   Read the draft, edit it, and intake it later.

5. Or send the brief straight into the pipeline:

   ```bash
   scripts/po-agent.sh /tmp/my-brief.md --project my-app
   ```

   PO writes the requirements doc under `projects/my-app/requirements/`
   and queues a PM ticket. The RC advances from there.

That is the whole loop. The rest of this page is depth.

---

## The brief template, field by field

The template lives at `team/templates/agent/BRIEF-TEMPLATE.md`. Every
brief is a Markdown file with the sections below. Two are load-bearing;
the rest have defaults you can accept.

| Field | Required? | Default | What it is |
|---|---|---|---|
| Title (H1) | yes | none | Short descriptive name for the work. First line of the file. |
| `## Goal` | **yes** | none | 1–3 sentences describing the outcome you want and why it matters. Focus on the outcome, not the implementation. |
| `## Target Version` | **yes** | none | Semver `vX.Y.Z`. PO refuses if the value is missing or malformed. |
| `## Version Bump Rationale` | recommended | placeholder inserted | 1–2 sentences tying the bump to patch/minor/major guidance. If missing, PO stamps the placeholder `[Version Bump Rationale not provided — human must fill this in]` and continues. |
| `## Constraints` | **yes** | none | One hard rule per bullet. Vague constraints are ignored; be specific. PO blocks if this section is missing or empty. |
| `## Human Approval Required` | no | `auto` | `auto` ships straight through after TESTER; `required` injects a HUMAN-APPROVE gate before CM-release. |
| `## Model Overrides` | no | omitted | Per-task model hints in plain English. See the template for the recognised model names. |
| `## Context` | yes | none | Domain background, existing paths, prior decisions, external specs. Write "None." if there really is none. |
| `## Notes` | no | omitted | Edge cases, preferences, warnings that do not fit above. Delete the section if empty. |

**The short version.** `## Goal` and `## Constraints` carry the intent.
`## Target Version` gates entry. `## Context` gives the agents enough to
work from. Everything else is judgement dial or noise; leave defaults
until you have a reason to change them.

---

## A filled example brief

The following file — saved as `/tmp/webhook-support.md` — is a complete,
runnable example. Copy it, adjust the version, and try `--dry-run` on
it.

```markdown
# Add webhook support for task events

## Goal

Enable external services to receive real-time task state changes without
polling the kanban API. A registered webhook receives one HTTP POST per
state transition (`BACKLOG` -> `WORKING`, `WORKING` -> `DONE`, etc.).

## Target Version

v0.7.0

## Version Bump Rationale

This is a MINOR bump because it adds a new outbound integration surface
without removing or changing any existing interface.

## Constraints

- No external network I/O without an explicit registered webhook URL.
- Webhook delivery must not block the state-transition path.
- Must pass ruff linting with zero errors.
- All new code must have corresponding pytest tests.
- Feature branches must not be pushed to origin.

## Human Approval Required

required

## Context

- Existing HTTP API lives at team/pgai_agent_kanban/api/.
- Task state transitions currently happen in
  team/pgai_agent_kanban/ops/write.py.
- The dashboard already subscribes to a similar in-process signal —
  see team/pgai_agent_kanban/dashboard/subscribe.py for prior art.

## Notes

Retry policy is out of scope for this version; a fire-and-forget POST is
acceptable. Retries and back-pressure can land in a follow-up.
```

That brief is what `po-agent.sh` reads. From it PO writes a full
requirements document (goals, deliverables, acceptance criteria,
workflow control fields) and queues a PM ticket.

---

## The three modes side by side

`po-agent.sh` runs in one of three modes. The mode is selected by which
flags are present.

| Mode | Command shape | Writes a file? | Creates a PM ticket? | Touches `projects/`? | When to use it |
|---|---|---|---|---|---|
| **Preview** (`--dry-run`) | `po-agent.sh <brief> --dry-run` | no | no | no | Sanity-check the brief metadata (Target Version, computed PM task ID). The LLM never runs. |
| **Draft** (`--output`) | `po-agent.sh <brief> --output <dir>` | yes, to `<dir>/` | no | **no** | Batch-draft requirements over time, review them in an editor, then intake the ones you keep. |
| **Full** (default) | `po-agent.sh <brief> --project <name>` | yes, to `projects/<name>/requirements/` | yes | yes | Land the brief in the pipeline immediately. PM picks up on the next wake. |

### Preview mode (`--dry-run`)

```bash
scripts/po-agent.sh /tmp/webhook-support.md --dry-run
```

Prints the resolved brief file, target version, computed PM task ID, and
the exact `claude` invocation that would run. Writes nothing. Invokes no
subagent. Use it to confirm the Target Version parses and the PM ticket
ID looks right before you commit real writes.

### Draft mode (`--output <dir>`)

```bash
scripts/po-agent.sh /tmp/webhook-support.md --output drafts/
```

Runs the **full PO expansion** (governance read, tree verification, the
9-rule method, the Assumptions ledger) and writes the resulting
requirements document to `<dir>/<target-version>-<slug>.md`. Nothing
lands under `projects/`. No PM ticket is created. The slug is derived
from the brief's basename.

Final stdout line is:

```text
DRAFT: drafts/v0.7.0-webhook-support.md
```

That single line is the manifest — a loop over many briefs produces one
`DRAFT:` line per successful run, so a grep gives you the file list.

**Collisions never overwrite.** If `drafts/v0.7.0-webhook-support.md`
already exists, PO writes `drafts/v0.7.0-webhook-support-2.md` (or `-3`,
`-4`, …) and prints `NOTICE: output collision — writing to <name>-N.md
instead of <name>.md`.

**`--output` and `--dry-run` are mutually exclusive.** Passing both is
refused loudly with a non-zero exit and this message:

```text
ERROR: --output and --dry-run are mutually exclusive.
--dry-run means no writes; --output means write a draft file.
Choose one: --output <dir> to produce a draft, or --dry-run to preview metadata.
```

Garbage briefs still refuse in draft mode. A missing or malformed Target
Version fails the same way it does in full mode; no file is written.

To intake a draft later:

```bash
scripts/intake.sh --project my-app --file drafts/v0.7.0-webhook-support.md
```

`intake.sh` routes by the filename's version prefix (`vX.Y.Z-*.md` →
`projects/<name>/requirements/`). Refuses to clobber an existing target.
See [operator-commands.md](operator-commands.md#intake--drop-an-intake-file-into-a-project-routed-by-filename)
for the full routing table.

### Full mode (default)

```bash
scripts/po-agent.sh /tmp/webhook-support.md --project my-app
```

Runs the full PO expansion and writes:

- `projects/my-app/requirements/<target-version>-<slug>.md` — the
  requirements doc.
- `projects/my-app/tasks/PM-<date>-<seq>-decompose-<slug>/` — the PM
  ticket folder (`README.md` + `status.md`).
- One line appended to `projects/my-app/tasks/queues/pm_backlog.md` so
  the next PM wake picks it up.

`--project` is required in full mode (also accepted via
`$PGAI_PROJECT_NAME`). Full mode does everything draft mode does, plus
the pipeline handoff.

---

## The human-approval gate

If your brief sets:

```markdown
## Human Approval Required

required
```

then PM injects a `HUMAN-APPROVE` task into the plan between TESTER and
CM-release. The release cannot ship until a human closes that task.

The task ordering becomes:

```text
CM-open -> feature tasks -> TESTER -> HUMAN-APPROVE -> CM-release
```

With `auto` (the default), no gate is injected and CM-release runs as
soon as TESTER passes.

### What the gate looks like

The materializer names the gate task
`HUMAN-APPROVE-<target-version>-<seq>` — for example
`HUMAN-APPROVE-v1.8.0-042`. The `<seq>` is assigned at materialization
time; check the `tasks/` directory or the CM-release task's
`prerequisite_ids` to find the exact ID for your RC.

Its folder lives at
`projects/<name>/tasks/HUMAN-APPROVE-<target-version>-<seq>/`. Read its
`README.md` for the deliverables and TESTER report the operator is being
asked to review.

### Approving the release

Close the gate with `state=DONE`. The RC then advances to CM-release on
the next wake.

```bash
scripts/close.sh --project my-app \
                 --key HUMAN-APPROVE-v1.8.0-042 \
                 --state DONE
```

`close.sh` on an agent task always closes as `DONE` — the `--state` flag
is intake vocabulary and is ignored on tasks. Either form works:

```bash
scripts/close.sh --project my-app --key HUMAN-APPROVE-v1.8.0-042
```

### Rejecting the release

If the RC should not ship, retire the gate with `wontdo.sh`. The
CM-release task's prerequisite is never satisfied, so the release does
not proceed.

```bash
scripts/wontdo.sh --project my-app --key HUMAN-APPROVE-v1.8.0-042
```

After rejection, decide what to do next: file bugs against the RC,
open a new PRIORITY item, or reset feature tasks and iterate. The gate
itself is single-use; a rejected RC needs a new plan, not a re-opened
gate.

---

## Batch workflow — 50 briefs, draft, refine, intake selectively

The workflow the draft mode was built for: sketch many briefs quickly,
generate drafts, refine them in an editor over days, and intake only the
ones you keep. Every command below runs verbatim from the repo root.

### Step 1 — Stage the briefs

```bash
mkdir -p briefs/batch drafts
```

Write one brief per file under `briefs/batch/`. Use the template from
the [60-second how-to](#60-second-how-to) as the starting point for each.
Every file must have `## Goal`, `## Target Version`, `## Constraints`,
and `## Context` filled in — anything else is refused loudly at
generation time.

### Step 2 — Generate a draft for every brief

```bash
for b in briefs/batch/*.md; do
  scripts/po-agent.sh "$b" --output drafts/
done
```

Every successful run prints a final `DRAFT: <path>` line. Collect the
manifest with:

```bash
for b in briefs/batch/*.md; do
  scripts/po-agent.sh "$b" --output drafts/
done | grep '^DRAFT: ' > drafts/manifest.txt
```

Failed briefs (bad Target Version, missing Constraints) refuse loudly
and produce no `DRAFT:` line — those briefs need editing before the
next batch.

### Step 3 — Refine

Open `drafts/*.md` in your editor. Rewrite goals, tighten constraints,
delete drafts that no longer make sense. Refine over hours or days; the
drafts touch nothing outside `drafts/`, so nothing in the pipeline is
waiting on them.

### Step 4 — Intake selectively

When a draft is ready to enter the pipeline, hand it to `intake.sh`:

```bash
scripts/intake.sh --project my-app --file drafts/v0.7.0-webhook-support.md
```

To intake several drafts at once:

```bash
for d in drafts/v0.7.0-*.md; do
  scripts/intake.sh --project my-app --file "$d"
done
```

`intake.sh` routes each `vX.Y.Z-*.md` file into
`projects/my-app/requirements/`. The discovery pipeline picks up the
deposited files on the next wake and hands them to PO for full-mode
expansion (which creates the PM ticket). Drafts you never intake stay
in `drafts/` — no cleanup is forced on you.

### Step 5 — Clean up (optional)

When you are done, remove the drafts you did not use:

```bash
rm drafts/v0.7.0-someversion-you-abandoned.md
```

`drafts/` is not tracked by any pipeline; the operator owns it.

---

## Related pages

- [operator-commands.md](operator-commands.md) — every operator command
  and its flag surface (`close.sh`, `intake.sh`, `wontdo.sh`,
  `halt.sh`, and friends).
- [../team/roles/PO.md](../team/roles/PO.md) — the PO agent's own
  procedure: what it reads, what it writes, when it blocks.
- [../team/templates/agent/BRIEF-TEMPLATE.md](../team/templates/agent/BRIEF-TEMPLATE.md)
  — the brief template file with inline commentary for every field.
