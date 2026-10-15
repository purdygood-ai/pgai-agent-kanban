# OPERATIONS — Operator Walkthrough

<!--
Audience: the human operator running a pgai-agent-kanban install.

This file is NOT part of the agent read order defined in team/SOP-core.md
under "Required Read Order For Assigned Work." Chain agents (PO, PM,
CODER, WRITER, TESTER, CM) do not read this file; their contract lives
in team/SOP-core.md, team/DIRECTIVES.md, and team/roles/<ROLE>.md.

Operators read this file. It collects operator-facing walkthroughs,
recovery procedures, and dashboard/metrics tours; the agent-facing
contract stays in the files named above.
-->

This document is the operator-facing companion to `team/SOP-core.md`
and the per-role files. It collects the walkthroughs, recovery
procedures, and dashboard/metrics tours a human at the keyboard needs
to run the kanban. Agent-facing contracts remain outside this file:
task states and role swim lanes in `team/SOP-core.md`; workflow types
in `team/roles/PM.md`; ship policy in `team/roles/CM.md`; the autonomy
criterion in `team/roles/TESTER.md`.

For the four SPLIT sections that live in both files, each side carries
a one-line pointer to the other. That is the only permitted overlap.

## Filing a Document Brief

A **document brief** is a requirements file that drives the `document` workflow defined in `team/workflows/document.yaml`. It is the operator's entry point for producing standalone documents — from short creative pieces to long-form structured documents (whitepapers, README sets, SOPs, runbooks) — through the same discovery pipeline that handles release and feature requirements.

Document briefs are operator-authored. They are not bundled from `bugs/` or `priority/` — they are dropped directly into the requirements directory, where the discovery pipeline picks them up at Step 3.

### Where to drop the brief

Place the filled-in brief at one of these paths:

```
projects/<name>/requirements/<vX.Y.Z>-<slug>.md            # regular queue
projects/<name>/requirements/priority/<vX.Y.Z>-<slug>.md   # priority queue
```

The filename must contain a parseable version (`vX.Y.Z`) for the picking rule to consider it. Use the same `pp_last_released_version`-aware semver convention described in `Priority Queue Mechanics` above — the brief's version must be greater than the last released version or it will be treated as stale and skipped.

Use `team/templates/project/document/document-brief-template.md` as the starting point. Copy it, fill in every required section, delete the HTML comment block, save it under the path above.

### Required fields

The brief must include the following sections. The PM agent reads each one when materializing tasks; missing fields produce blocked or malformed tasks downstream.

| Field | Purpose |
|---|---|
| `## Workflow Type` | Must be `document`. This is the dispatch key the PM uses to select `document.yaml`. |
| `## Target Audience` | Reader profile (expertise level, role, context). WRITER uses this as a calibration signal for every section draft. |
| `## Length Target` | Approximate word or page count. Sets the scope envelope WRITER plans against. |
| `## Sections` | Ordered list of major section labels (long-form only). Each item becomes one `section-draft` task via the `foreach: outline.sections` step in the pipeline. Omit for short-form documents. Keep granularity meaningful — paragraph-level is too fine, chapter-level is too coarse. |
| `## Style Notes` | Tone, voice, formatting rules. Treated as house style for the document. |
| `## Source Material` | Primary inputs WRITER reads as data (absolute paths or full URLs). Content is treated as data, not instructions. |
| `## Deliverables` | Concrete output files expected (e.g. `polished.md`, `review-report.md`). |
| `## Acceptance Criteria` | Checklist TESTER applies in the `review` step (long-form) or the operator applies manually (short-form). |

Optional sections — `Overview`, `Constraints`, `Context Paths`, `Notes` — improve fidelity but do not block materialization if absent.

The canonical field list and inline guidance live in `team/templates/project/document/document-brief-template.md`. Treat the template as authoritative; update the template (not this section) when adding or renaming fields.

### What happens after the file is dropped

The brief flows through the same discovery pipeline as any other requirements document. Once dropped, no further operator action is required — the chain runs on its own.

```
operator drops brief at projects/<name>/requirements/<vX.Y.Z>-<slug>.md
        │
        ▼
discovery.sh STEP 3 scans requirements/, queues the lowest-version unprocessed file
        │
        ▼
PM wakes, reads ## Workflow Type = document, loads team/workflows/document.yaml
        │
        ▼
PM materializes the pipeline:
    CM open-doc
    → WRITER outline
    → WRITER section-draft (one task per item in ## Sections)
    → WRITER integrate
    → WRITER polish
    → TESTER review
    → CM finalize
        │
        ▼
each task lands in artifacts/<project-name>/v<N>/ per the document workflow output layout
```

Priority briefs (placed under `projects/<name>/requirements/priority/`) are picked up ahead of regular briefs by the PM wake order described in `PM Wake Order` below.

### What the chain produces

Deliverables land in `artifacts/<project-name>/v<N>/` under the standard non-release artifact layout (`input/`, `working/`, `output/`). The final outputs are:

- `output/polished.md` — the finished document
- `working/outline.md` — the WRITER outline that drove section decomposition
- `working/section-<name>.md` — one drafted section per item in `## Sections`
- `working/integrated.md` — the assembled document before polish
- `working/review-report.md` — TESTER's gap and quality report

No git tag is produced. No RC branch is opened. The `document` workflow runs through `team/scripts/cm/open-doc.sh` and `team/scripts/cm/finalize.sh` rather than the release scripts.

### Edit-and-resubmit

If a brief was already materialized and the operator needs to revise it, bump the filename's version (`v0.5.0` → `v0.5.1`), drop the revised brief, and let the next discovery iteration pick it up. Editing the original file in place will not re-trigger materialization — the discovery pipeline keys off filename version, not file modification time, for the regular and priority queues.

## HALT Flags — operator commands

The chain-side semantics of `HALT` (what it does, who honors it, who may create it) live in `team/SOP-core.md` under "HALT Flags." This section covers the operator create/remove commands.

### Two scopes, two files

Before running any command, know which file you are looking at. The path is what tells you how much of the kanban is parked.

| Scope | File | Stops | Who writes it |
|---|---|---|---|
| Fleet-wide | `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT` | Every chain agent across every project | The operator, deliberately — by hand, through the global halt command, or from the dashboard's global-halt action — plus `HALT-AFTER` auto-promotion when the sentinel that drained was itself armed at the root |
| Project | `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT` | Only the named project; sibling projects keep shipping | The operator, for one project — and CM, for every one of its autonomous halts |

The rule behind the split: a verdict about one project parks one lane, and only a system-breaking state that crosses projects parks the fleet. CM's halts are always verdicts about the one release in front of it, so they are always project-scoped. `team/SOP-core.md`, "HALT Flags," is the authority for the scope rule and for the short list of genuinely fleet-scoped writers.

### Setting and clearing the flag

These two commands are the operator's own fleet-wide manual pause — every project, every agent, stopped at once:

- To pause the whole kanban: `touch ${PGAI_AGENT_KANBAN_ROOT_PATH}/HALT`
- To resume the whole kanban: `rm ${PGAI_AGENT_KANBAN_ROOT_PATH}/HALT`

To park a single project and leave its siblings running, use the project-scoped file instead — see "Per-Project HALT Workflow" below.

The operator is the only role that removes a `HALT` file, at either scope. CM never removes one. Removal is the operator's signal that the underlying issue is resolved and the chain may resume.

## When the chain halts

A halted chain is the autonomous system's signal that operator judgment is required. The operator removes the `HALT` file when the underlying issue is resolved. This section is the operator-facing playbook for diagnosing and resolving an autonomous HALT.

The chain may halt for one of two reasons: the operator created a flag manually (covered above), or CM created one autonomously because one of its nine HALT triggers fired. The procedure below covers the autonomous case.

**An autonomous CM halt is always project-scoped.** The file is `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT`, and only that project's lane is parked; every sibling project kept shipping while you were away. If the dashboard or a wake log reports a halt and the fleet-wide file is absent, this is what you are looking at. Find it:

```bash
ls $PGAI_AGENT_KANBAN_ROOT_PATH/projects/*/HALT 2>/dev/null
```

### CM HALT triggers

CM writes `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT` for any of nine conditions. They are named here at operator granularity, for orientation only — `team/roles/CM.md`, "The nine triggers," is the authority for their wording, their numbering, and which of them a written override may pass.

1. **TESTER state is `BLOCKED`** — verification could not complete, so there is no valid report to ship against.
2. **TESTER systemic risk is high and the finding is not clear to ship** — a regression, integrity damage, or a large-effort finding.
3. **A finding has fix effort `large` in a `SHIP-WITH-SERIOUS-CONCERNS` context** — too risky to ship through; the work needs scoping first.
4. **A pre-squash hook failed** — the hook exited non-zero, or the phase is required and no hook resolved at any tier. See "Release Lifecycle Hooks" below.
5. **The squash to main has conflicts** — git state is damaged; human judgment is required before any further branch mutation.
6. **Push to origin failed after retries** — the release is locally complete but cannot be distributed.
7. **The tag already exists on remote** — a race, or a repeat invocation against an already-shipped version.
8. **The last three consecutive releases were all `NON-FUNCTIONAL`** — the chain is shipping degraded work without self-correcting.
9. **The release-notes status stamp failed** — the release record cannot be guaranteed to state the actual ship decision.

Triggers 1 through 3 are judgement calls about the release in front of CM; the next subsection has the command for passing one. Triggers 4 through 9 are mechanical or systemic. When CM halts on trigger 8 it also files a bug naming the pattern, so the systemic issue is visible in the bug queue.

On a project whose ship posture is `always`, triggers 2, 3 and 8 do not halt at all: the release ships and records the stop it passed through. See "Ship posture — shipping through a ship-policy stop" below.

### Overriding a ship-policy stop

Triggers 1, 2, and 3 are the ship-policy matrix's judgement calls about the release in front of it, and an operator carrying a written reason may pass them:

```bash
scripts/cm/release.sh --project <name> --force --reason "<why this must ship anyway>"
```

The remaining triggers are mechanical or systemic — they are repaired, not passed, and `--force` is a no-op against them. A forced ship is never quiet: the release notes are stamped `SHIPPED-BY-OPERATOR-OVERRIDE` and the project's `release-state.md` gains an `## Operator Override` block naming the timestamp, the version, the stop passed, and your reason. A halt cleared this way stays as visible afterwards as one cleared by a fix.

The flags, the mandatory reason, and each of the command's four refusals with its remedy are in `docs/operator-commands.md`, "cm-release --force." Which triggers the flag reaches is marked per trigger in `team/roles/CM.md`, "The nine triggers."

`--force` is a one-off answer to one stop. If a lane keeps stopping on triggers 2 and 3 and you keep forcing it through, the standing answer is the ship posture below. To see what the next release would decide before you run it, use `scripts/cm/release.sh --project <name> --decide-only` ("cm-release --decide-only" in `docs/operator-commands.md`).

### Operator response procedure

When the operator returns to a halted chain:

```bash
# 1. Find the halted project, then read its HALT file. The comment header
#    names who created it, what stopped, and what to do next.
ls $PGAI_AGENT_KANBAN_ROOT_PATH/projects/*/HALT 2>/dev/null
cat "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"

# 2. Find the BLOCKED CM task that wrote the HALT (most recent).
#    Glob both formats: legacy CLAUDE-CM-* folders and the
#    current CM-* format. Both layouts coexist on disk indefinitely.
ls $PGAI_AGENT_KANBAN_ROOT_PATH/projects/*/tasks/CLAUDE-CM-*/status.md \
   $PGAI_AGENT_KANBAN_ROOT_PATH/projects/*/tasks/CM-*/status.md 2>/dev/null \
  | xargs grep -l "^BLOCKED$" \
  | tail -3

# 3. Read that task's status.md for the full reason and any context CM recorded.
cat <path-from-above>

# 4. Read the project's release-state.md for the HALT Event log.
cat "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/release-state.md"
```

The CM task's `## Blockers` field contains the plain-language reason. The `release-state.md` HALT Event entry contains a timestamp, the CM task or release script that recorded it, and the same reason the HALT file's header carries — useful when reviewing the history of past halts.

Once the issue is understood and resolved:

```bash
# Resume this project's chain. Sibling projects were never stopped.
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"
```

After the project's `HALT` is removed, the next wake firing enters that project's chain again. The BLOCKED CM task must be re-run (or the operator must manually advance the release by resolving the underlying issue and re-invoking the release script). Removing `HALT` does not automatically retry the CM task — the task state is unchanged by `HALT` creation or removal.

If the fleet-wide file is present as well — an operator pause laid on top of the autonomous one — the chain stays parked until that one is removed too. The root flag wins: while it exists the wake scripts exit before the per-project loop and no project's flag is consulted at all.

### HALT-file lifecycle

The HALT file has a deliberately simple lifecycle:

| Step | Actor | Action |
|---|---|---|
| 1. Create | Operator or CM | CM writes `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT` with a comment header documenting timestamp, what stopped, and what to do about it. The operator may `touch` that same project file to park one lane, or `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT` for a fleet-wide manual pause. |
| 2. Observe | Chain wake scripts | At each cron firing, before task selection, the wake script checks for the file. If present, exits cleanly with status 0 and logs the halt condition. |
| 3. Investigate | Operator | Reads the HALT file header, the most recent BLOCKED CM task's `status.md`, and the project's `release-state.md` HALT Event log. |
| 4. Resolve | Operator | Performs whatever action the trigger requires (fix the dev tree, resolve a conflict, push manually, adjust the priority queue, etc.). |
| 5. Remove | Operator | `rm "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"` for an autonomous halt; `rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"` for the operator's own fleet-wide manual pause. The chain resumes at the next wake firing. |

CM never removes a `HALT` file. The chain never auto-resumes. Removal is always the operator's deliberate signal.

When CM creates the HALT file at `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT`, the format is:

```
# HALT created by CM at <ISO-8601 timestamp>
# error:   <stable key naming the trigger that fired>
# message: <what stopped and why, in plain language>
# remedy:  <what to do next, runnable where it can be>
# Resolution: Operator review required. See CM task status.md for full reason.
```

This makes the file self-documenting: `cat` on the project's HALT file is enough to know who created it, what stopped, and where to look next. Manual operator halts (from `touch`) have no header, at either scope; that absence itself indicates the halt was operator-initiated rather than CM-initiated.

CM never overwrites an existing HALT file. If a project is already halted when a second trigger fires, the earlier reason stands and the newer one is reported in the release log only — so a HALT file that does not match the failure you just watched happen is the older halt, still uncleared.

## Ship posture — shipping through a ship-policy stop

A project's ship posture decides what CM does when the ship-policy matrix says HALT. Under the default, `matrix`, every HALT row halts, exactly as described above. Under `always`, three stops ship the release instead and record what they would have stopped on:

- **Trigger 2** (systemic risk high, not clear to ship) and **trigger 3** (serious concerns plus a large fix) ship with release-notes Status `NON-FUNCTIONAL`.
- **Trigger 8** (the last three releases all `NON-FUNCTIONAL`) files one bug naming the pattern and ships.

Every other HALT is unchanged: trigger 1 (TESTER `BLOCKED`), the two matrix rows for an unrecognized TESTER state or no matching row, and every mechanical trigger from 4 onward — hooks, squash, push, tag, the notes stamp, and the release script's own pollution and fidelity guards. TESTER rates exactly as before; only CM's response to the rating changes.

`always` suits a local-only lane. There a tag is a checkpoint that nothing installs until you choose to, and the next bundle fixes what the last tag got wrong — so a trigger-2 or trigger-3 stop does nothing but idle the lane until someone runs `--force`. A lane that publishes to origin can use it too, but only by confirming it in a second key (the remote-lane guard, below).

Do not confuse it with the deferral posture in "Deferral campaigns" below: `why.sh`'s `== posture ==` section reports only the deferral keys and never shows the ship posture.

### The two keys

**`[release]` — ship posture:**

| Key | Default | Accepted | Effect |
|---|---|---|---|
| `ship_posture` | `matrix` | `matrix` or `always` | `matrix` applies the ship-policy matrix as written. `always` ships through triggers 2, 3 and 8 and records each stop it passed. |
| `ship_posture_confirm_remote` | `false` | `true` or `false` | The remote-lane guard. On a project that pushes to origin, `always` takes effect only when this is also `true`. Not consulted on a `push_to_remote = false` project. |

Both keys live under `[release]`, in a project's own `project.cfg` or in `kanban.cfg`. `kanban.cfg_example` ships `ship_posture = matrix` active and the confirm key commented out; `project.cfg_example` ships both commented out. Values are matched exactly, so `Always` or `True` is not an accepted spelling.

### What resolves, and in what order

Each key resolves on its own, through the same chain `max_rc_retries` uses. The first non-blank value wins:

1. The project's own `project.cfg` `[release]` section (or a legacy `PROJECT.cfg`).
2. `kanban.cfg` `[release]`.
3. The default: `matrix` for `ship_posture`, `false` for `ship_posture_confirm_remote`.

A blank value counts as absent and falls through to the next tier. An unrecognized value does not fall through: it wins its tier, then resolves to `matrix`.

**An unrecognized value is a warning, never a refusal.** A typo such as `alwasy` resolves to `matrix` and prints one warning line on stderr naming the file and the raw value; the caller carries on under `matrix`. From the bash resolver it reads:

```
project_paths.sh: pp_ship_posture: WARNING: unrecognized value 'alwasy' for project 'my-app' in /home/you/pgai_agent_kanban/projects/my-app/project.cfg [release] ship_posture (accepted: matrix|always); resolving to 'matrix'.
```

The Python resolver behind `kanban-status.sh` and `GET /status` prints the same text after its own prefix, `resolution_matrix: resolve_ship_posture: `. A typo can therefore only ever leave a lane on the stricter posture.

### The remote-lane guard

A lane that publishes to origin never slips into `always` by accident. When the project's `[project] push_to_remote` is anything but the literal `false` — and absent means `true` — `always` takes effect only if `ship_posture_confirm_remote` resolves to exactly `true`. Anything short of that resolves the posture to `matrix` and prints one warning line. Like the typo fallback, this is not a HALT: the release simply runs under `matrix`.

The full resolution:

| `ship_posture` resolves to | `push_to_remote` | `ship_posture_confirm_remote` | Resolved posture |
|---|---|---|---|
| absent, blank, or `matrix` | any | not consulted | `matrix` |
| an unrecognized value | any | not consulted | `matrix`, with the unrecognized-value warning |
| `always` | `false` | not consulted | `always` |
| `always` | `true` or absent | `true` | `always` |
| `always` | `true` or absent | absent, blank, `false`, or anything else | `matrix`, with the remote-lane warning |

The remote-lane warning names the file to edit — the one already carrying a non-`true` confirm value, or else the one that set `always`:

```
project_paths.sh: pp_ship_posture: WARNING: ship_posture 'always' for project 'my-app' in /home/you/pgai_agent_kanban/projects/my-app/project.cfg is not confirmed for a push_to_remote = true lane ([release] ship_posture_confirm_remote is absent from /home/you/pgai_agent_kanban/projects/my-app/project.cfg and /home/you/pgai_agent_kanban/kanban.cfg); resolving to 'matrix'. Set ship_posture_confirm_remote = true under [release] in /home/you/pgai_agent_kanban/projects/my-app/project.cfg to confirm.
```

Set the confirm key in the project's own `project.cfg`. A `true` in `kanban.cfg` confirms `always` for every remote lane that inherits it.

### Setting it

Edit the project's `project.cfg`. Nothing needs restarting: every reader resolves the posture afresh, and a release reads it once, at the start of its ship-policy step. An example for a local-only lane:

```
# Example: projects/my-app/project.cfg on a local-only lane
[project]
push_to_remote = false

[release]
ship_posture = always
```

And for a lane that pushes to origin, where you have decided to accept it:

```
# Example: projects/my-app/project.cfg on a lane that pushes to origin
[release]
ship_posture = always
ship_posture_confirm_remote = true
```

To go back, set `ship_posture = matrix` or delete the line. Nothing persists from the time a lane spent under `always` except the records each auto-ship left.

### Seeing the resolved value

Three surfaces show the posture after the guard has been applied, so they show what a release would actually use:

- **`scripts/kanban-status.sh --project my-app`** ends with a `Ship posture:` line. Any resolver warning goes to stderr and the exit code stays 0.

  ```
  Quarantined: 0
  Ship posture: always
  ```

- **`GET /status?project=my-app`** returns the same summary in `stdout` and adds a `ship_posture` field to the envelope: `"matrix"` or `"always"`, or `null` when the read was refused (`exit_code` non-zero). A resolver warning arrives in `stderr`. The CLI and the endpoint read the posture through the same function, so they cannot disagree.

  ```bash
  curl -sS "http://127.0.0.1:8300/status?project=my-app"
  ```

- **`scripts/cm/release.sh --project my-app --decide-only`** prints the posture in the `posture=` field of its decision line, next to the decision it leads to. See "cm-release --decide-only" in `docs/operator-commands.md`.

### What an auto-shipped release leaves behind

An auto-ship is never quiet. Each stop the posture shipped through leaves two records:

- **An `## Auto-Ship Event` block in the project's `release-state.md`.** It records the timestamp, the version, the trigger and its error key, the posture, the TESTER task, and — for trigger 8 — the bug filed for the pattern. It is a record only; no later release reads it back.
- **One line under `## Known Issues` in the release notes:**

  ```
  Auto-shipped under ship_posture=always (would have halted: trigger 2 — cm-release.trigger-2.systemic-risk-high)
  ```

A trigger-2 or trigger-3 auto-ship also stamps `NON-FUNCTIONAL` and puts the standard non-functional warning, naming the bugs behind the stop, under the same `## Known Issues` heading. A trigger-8 auto-ship leaves the Status to the rest of the matrix, so it can ship `FUNCTIONAL` and still carry its auto-ship line.

The Auto-Ship Event is appended only once the release has tagged. A release that passes the ship policy under `always` and then halts at a mechanical gate — a hook failure or a squash conflict, say — leaves no Auto-Ship Event behind, though notes the release had already stamped on the RC branch keep their auto-ship line. The block's exact shape and the Step 4c/4d mechanism are in `docs/cm-release-reference.md`, "The ship posture in full."

**`--force` under each posture.** Under `matrix`, `--force --reason` passes triggers 1, 2 and 3 as before. Under `always`, triggers 2 and 3 never reach a HALT, so there is nothing for `--force` to pass: a forced run on such a lane ships `NON-FUNCTIONAL` with an Auto-Ship Event, not with an operator-override stamp. `--force` still passes trigger 1 on either posture.

## When OVERWATCH resolves a halt for you

Some ship-policy halts are not judgement calls. Either a fix this RC itself shipped broke something and the recovery is a known playbook, or the state a verifier flagged is one you deliberately created and already decided about. OVERWATCH can take those two off your desk and escalates everything else exactly as before.

**The duty itself is stated once, in `team/roles/OVERWATCH.md`, "Autonomous Block Resolution."** That section is the authority for what each lane does, what authorises it, and what OVERWATCH may never do. This section is the operator's side of it: the key that turns it on, the ledger that authorises Lane B, the record it leaves, and where you watch it happen.

**It ships off.** `[release] overwatch_resolve` is seeded commented-out into every `kanban.cfg` and every `project.cfg` — on a fresh install, on a new project, and by the upgrade migration, which never flips it. Absent, blank, or unreadable means `false`. A project with the key off gets no lane, no Decision event, and no surface change whatsoever — every halt takes the default path through the ship-policy matrix. (The matrix's own CANCEL-AND-REBUNDLE row for a fully-filed, nothing-shipped ship-policy halt sits outside the lanes, is unaffected by this key, and applies either way. Everything the matrix does not resolve raises Needs Human.)

### Turning it on

The key lives under the `[release]` section, in the project's own `project.cfg` for one project or in `kanban.cfg` for the fleet default. The project's own value wins; the fleet value is consulted only when the project has none. Both files ship the line commented out — uncomment it in the file you want it read from, and set it to `true`.

Only `true` and `false` are accepted. A typo — `yes`, `on`, `1` — is never guessed into a boolean in either direction: the read refuses loud, naming the key, the offending value, and the file it came from. Check what a project resolves to today by reading both files, project first:

```bash
grep -n "overwatch_resolve" "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/project.cfg" \
                            "$PGAI_AGENT_KANBAN_ROOT_PATH/kanban.cfg"
```

A commented line in both files is the shipped state and resolves to `false`.

**If the grep finds no line at all for a project**, that `project.cfg` predates the key and has not been seeded yet. Upgrading seeds it: `upgrade.sh` runs the migration automatically, as part of its post-stamp migration step, for any upgrade whose version range covers v2.23.0. To seed an install without upgrading it, run the migration directly:

```bash
bash "$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/migrations/run-migrations.sh" \
     --from v2.22.5 --to v2.23.0
```

Add `--project <name>` to seed one project instead of all registered ones, or `--dry-run` to preview the writes. The migration is additive and idempotent: it only ever inserts the commented line into a `[release]` section that has none, and leaves any `overwatch_resolve` line already present — including a value you set yourself — byte-for-byte untouched. Re-running it is safe. `scripts/migrations/README.md` carries the runner's full operator procedure.

What turning it on buys: a bad-fix regression halt is recycled within the hour instead of waiting for you, and a halt whose cause you have already declared ships instead of parking the lane. What it costs: on the projects you opt in, an RC can be cancelled and a release can go out while you are away — always reversibly, always one attempt, always on the record, but without you in the loop. Turn it on per project, not fleet-wide, until you have watched it behave on a lane you can afford to have moving.

### Writing an INTENT.md entry Lane B will honour

`projects/<name>/INTENT.md` is your dated ledger of deliberate decisions about a project's state. It is the *only* thing that can authorise a forced ship by OVERWATCH — no entry, no force, ever. `create-project.sh` seeds the file with a header, a fenced worked example, and zero live entries.

An entry is one line, under the `## Entries` heading:

```
- 2026-09-15: ai_main was rebased to v2.23.1 as a hindsight experiment; the old lineage is preserved on last_run_ai_main, so ai_rc/v2.23.1 diverging from ai_main is deliberate.
```

Six rules govern whether that line is read at all. They are all mechanical, and a line that breaks any of them is dropped whole and silently — never parsed as a partial entry:

1. **It must sit under `## Entries`.** The section runs from that heading to the next `##` heading. Text anywhere else in the file is commentary.
2. **The bullet starts at column 0.** `- ` then the date. An indented or nested bullet is not an entry.
3. **The date is `YYYY-MM-DD` and must be a real calendar date**, followed by `:` and a space. Use the day you made the decision, not the day you edited the file.
4. **One physical line. Do not wrap it.** If the line immediately after your entry is anything other than blank, another bullet, or a code fence, the parser reads it as a continuation attempt and discards the entry rather than silently truncating what you wrote. Long entries are fine; wrapped ones are not. The template's own trailing HTML comment counts as such a line, which is why it tells you to add entries *below* it — leave a blank line between an entry and any prose that follows.
5. **Not inside a code fence.** That is exactly how the template's own worked example stays inert. Copy its shape, not its fence.
6. **The declaration text must not be empty.**

A well-formed entry is still only *half* of what Lane B needs. It has to pass two different tests on its way to a shipped release, and they are not the same rule — an entry can pass one and fail the other, so it is worth being able to tell them apart.

**Test 1 — the classifier's identifier test: does this entry explain this block?** OVERWATCH collects every identifier-shaped token in the block's evidence — every `BUG-NNNN` id in every TESTER finding body, every `vX.Y.Z` version, every slash-qualified ref such as `ai_rc/v2.23.1`, and every underscore-compound name such as `last_run_ai_main` — plus the RC's own target version, RC branch, and base branch, and requires **all** of them to appear verbatim somewhere in a single declaration. This is a containment test: the declaration has to *contain* every token, and it is expected to say more besides — the explanation around the identifiers is the point of writing the entry. A thematically similar sentence that does not name the specific version, branch, and bug ids does not match, and a block with no identifier-shaped evidence at all never matches anything.

In practice that means: open the TESTER report the halt cites, and write your declaration so it names, spelled exactly as they are spelled there, the version, the RC branch, the base branch, and every bug id the findings mention. Then explain why that state is deliberate. Write it as a statement you are prepared to stand behind — the text is recorded verbatim as the release's `--reason` in `release-state.md`, so it is the sentence a reader six months from now will hold you to.

**Test 2 — the release engine's intent re-verification: is this reason a declaration you actually wrote?** The release engine does not trust OVERWATCH's claim. When the forced ship is attempted, `cm/release.sh` re-reads your live `INTENT.md` through the same parser the classifier used and compares the `--reason` it was handed against each entry's declaration by whole-string equality. `--reason` must be **byte-for-byte identical** to one live entry's declaration — not a substring of one, and not one plus extra words around it. Containment is test 1's job; test 2 does not repeat it.

The two tests never disagree on the automated path, because OVERWATCH ships with the matched entry's declaration verbatim as `--reason` — the string that passed test 1 *is* the string test 2 compares. The distinction bites when a human writes the reason by hand: a `--reason` that paraphrases an entry, quotes part of one, or wraps one in framing words is refused under the error key `cm-release.overwatch-lane-b.intent-not-matched`, whose remedy tells you to make the entry's declaration byte-for-byte identical to the reason you are passing. Copy the declaration; do not retype it.

One last thing worth knowing: entries are live until you delete them — an entry describing a one-off experiment keeps authorising matching ships for as long as it sits in the file, so prune it when the experiment is over.

### Reading an OVERWATCH Decision event

Every classification — including Lane C, where nothing was executed — appends one block to the project's `release-state.md`. It is append-only; the last one is the most recent.

```
## OVERWATCH Decision
Timestamp: 2026-09-15T04:12:33Z
Version: v2.23.1
Lane: C
Evidence:
- /home/op/pgai_agent_kanban/projects/<name>/HALT
- /home/op/pgai_agent_kanban/projects/<name>/tasks/TESTER-.../artifacts/report.md
- /home/op/pgai_agent_kanban/projects/<name>/INTENT.md
Verbs Run:
- Needs Human raised (existing default — no new mechanism)
Outcome: OVERWATCH read: v2.23.1 is HALTed and classifies as Lane C — ...
```

Read it in this order. **Lane** tells you what OVERWATCH thought the block was: `A` a bad-fix regression, `B` a state you had declared, `C` everything else. **Verbs Run** is the complete list of what it actually did — for Lane C it is one line saying Needs Human was raised, and nothing was touched. **Evidence** names the files it read, so you can check its reasoning against the same sources. **Outcome** is the plain-language result; on Lane C it is the one-paragraph read naming the cause and your options, which is the fastest orientation you will get on a halt you did not watch happen.

A Lane A decision recorded against a bundle OVERWATCH already recycled once does not run the playbook again. It escalates, and its Outcome carries both attempts' evidence — one attempt, never a loop.

### Watching a resolution happen

Two live surfaces read the same two files, so they cannot disagree with each other.

`why.sh` keeps the `HALTED` verdict and exit code `5` throughout — nothing can be dequeued either way — and only the cause and remedy change. While a lane is executing, the line reads `OVERWATCH resolving (lane A, attempt 1) for v2.23.1` and the remedy tells you to wait. Once the lane finishes, the line reports the recorded decision instead, but only while that decision's own version still matches the project's current `Active RC`, so a decision about a since-shipped RC is never presented as an explanation of a later halt.

The dashboard's attention window shows the same two states, per project: a cyan `resolving` entry while a lane runs, or a `decision` entry summarising the last decision — the latter only while the project's HALT is still present and the versions still match. An in-progress resolution always wins over a recorded one, and neither is rendered in the red reserved for needs-human blocks, because neither is waiting on you yet.

If a project's `overwatch/resolving.md` marker is present but nothing is running, a firing was killed mid-drill. That marker is deliberate evidence of exactly that, not a bug. The marker names the lane, the attempt, and the version; for a Lane A or CANCEL-AND-REBUNDLE drill, the project's `auto-resolutions.md` ledger already lists every action that drill had committed to taking, because the entry is written before the drill runs. Read both, then let the next OVERWATCH sweep reconcile the rest.

## Per-Project HALT Workflow

Per-project HALT pauses one registered project while the rest of the chain keeps running. It is a peer of the global `HALT` flag, scoped to a single project's directory. Use it when one project needs to sit still — mid-investigation, mid-edit, or while you reshape its inputs — without freezing every other project on the kanban.

The architectural shape is documented in `ARCHITECTURE.md` under "Multi-Project Support". This section is the operator workflow.

### Halt one project

The flag file lives at `$KANBAN_ROOT/projects/<name>/HALT`. Create it to halt; delete it to resume.

```bash
# Halt the named project (replace <name> with the actual project name).
touch "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"

# Resume.
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"
```

Substitute the real project name in place of `<name>`. The project name is the directory name under `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/`, and it matches the `project_name` key in that project's `project.cfg [project]` section. To list registered projects:

```bash
ls "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/"
```

The HALT file's contents are ignored — only its presence matters. `touch` is sufficient; an empty file does the job.

### Verify the halt

Two ways to confirm a project is actually halted.

**Helper invocation.** Source the `pp_*` helpers and call `pp_project_halted`. It returns 0 (true) when the HALT file exists and 1 (false) when it does not, with no output.

```bash
KANBAN_ROOT="$PGAI_AGENT_KANBAN_ROOT_PATH" \
  bash -c 'source "$KANBAN_ROOT/team/scripts/lib/project_paths.sh"; \
    pp_project_halted "<name>" && echo "halted" || echo "running"'
```

Substitute the real project name for `<name>`. The command prints `halted` or `running`.

**Wake log.** The next wake firing emits one log line per project as it iterates the registry. Halted projects produce a `per-project HALT present, skipping` line; running projects produce a `beginning chain` line. The log lives in the single shared wake log with a per-project prefix, so grep the project name to filter:

```bash
grep "<name>" "$PGAI_LOGS_DIR/cron-pm.log" | tail -20
```

The wake script also tags each line with the agent that fired (`pm`, `coder`, `writer`, etc.), so check the log for the agent you expect to run against the project. If no wake has fired since you touched the HALT file, wait one cron tick or invoke `team/scripts/wake/claude.sh --agent=<agent>` manually to see the next iteration's decision.

### Global HALT versus per-project HALT

The two flags have different scopes and different precedence. Pick the one that matches what you want to pause.

| Flag | Path | Scope | When to use |
|---|---|---|---|
| Global `HALT` | `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT` | Every chain agent across every project | Systemic problems, governance transitions, full-stop reviews |
| Per-project `HALT` | `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT` | Only the named project; other projects keep running | One project needs to sit still while the rest of the kanban ships |

The per-project file is also where every autonomous CM halt lands — see "When the chain halts" above for the response procedure. A file you did not create yourself under `projects/<name>/` is CM's, and it carries a comment header saying so.

Global HALT wins. If `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT` exists, the wake scripts exit at startup before entering the per-project loop and no project iterates — per-project flags are not even consulted in that state. Per-project HALT only takes effect when the global flag is absent.

### When to reach for per-project HALT

- A specific project is mid-investigation and you do not want its chain to keep churning while you read its `bugs/`, `priority/`, or `release-state.md`.
- You are hand-editing requirements or queue files inside one project and want that project paused without freezing siblings that are mid-release.
- You want to drain one project's WORKING task without queuing new work for it. Touch the HALT file; the current task finishes; nothing new starts.

Per-project HALT does not change task states. Tasks already in WORKING stay in WORKING. Tasks in BACKLOG stay in BACKLOG. The flag only gates whether the wake script enters that project's chain on the next firing.

## HALT-AFTER — operator arm and resume

The grammar, drain semantics, auto-promotion behavior, and audit-entry shape for `HALT-AFTER` are documented in `team/roles/CM.md` under "HALT-AFTER (soft halt)." This section is the operator arm/resume walkthrough.

### File location and scope

`HALT-AFTER` mirrors `HALT` exactly. Two scopes, same precedence rules as the hard flags.

| Flag | Path | Scope |
|---|---|---|
| Global `HALT-AFTER` | `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT-AFTER` | Every chain agent across every project |
| Per-project `HALT-AFTER` | `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT-AFTER` | Only the named project |

### Arming

Write the desired event token into the file:

```bash
echo rc > HALT-AFTER          # halt after the current RC ships
touch HALT-AFTER              # empty file defaults to the rc token
echo writer > HALT-AFTER      # halt after WRITER queue idles
```

Valid tokens are `rc`, `pm`, `coder`, `writer`, `tester`, and `cm`. See the SOP-side "HALT-AFTER (soft halt)" section for full drain semantics.

### Operator resume

Resume is unchanged from a normal `HALT` resume. Promotion happens at the scope the sentinel was armed at, so remove the flag at that same scope:

```bash
# Resume after a per-project HALT-AFTER auto-promoted to a per-project HALT.
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"

# Resume after a root-armed HALT-AFTER auto-promoted to the operator's
# fleet-wide HALT.
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
```

The operator does not interact with `HALT-AFTER` at resume time — it was removed by the auto-promotion step. Only `HALT` remains, and removing `HALT` lets the next wake firing enter the chain again. The chain itself never removes either flag; promotion creates `HALT`, and the operator clears it. See "When the chain halts" for the full operator response procedure.

## Why is this lane not moving?

`scripts/why.sh` is the first thing to run when a project looks stuck — before
you open a log, a task folder, or the dashboard. With no arguments it reports
the project `$PGAI_PROJECT_NAME` names when that is set, and walks every
registered project only when neither `--project` nor `$PGAI_PROJECT_NAME` is
given. Either way it prints one verdict line per project, carrying the verdict
word, the cause, and a runnable remedy in the same sentence.

```bash
scripts/why.sh                               # $PGAI_PROJECT_NAME's line if set, else every project's
scripts/why.sh --project my-app              # just this one
scripts/why.sh --project my-app --details    # that line, plus the diagnostic pane
```

The five verdicts and what each one asks of you, the exit-code table, and the
`--details` sections are documented once, in `docs/operator-commands.md` under
"why — one verdict per project, with its cause, its remedy, and an exit code."
Read the reference there. This section is the walkthrough around it: when to
reach for the verb, and what to do with what comes back.

### The daily check

Run it bare. Each line names one project, and the verdict word is the first
thing after the colon: `RUNNING`, `BLOCKED`, `HALTED`, `STUCK-LOCK` or `IDLE`.
A `RUNNING` line always carries evidence — the active task, its state, and how
many whole minutes it has held that state — so a lane that is picking work up
and putting it down again does not read as healthy just because something is
technically running.

Three verdicts want you: `BLOCKED` (a task named an obstacle and stopped),
`HALTED` (a `HALT` file is holding the whole project) and `STUCK-LOCK` (work is
queued, but the project's lock is stale or carries no provenance record, so
nothing can pick the work up). `BLOCKED` and `HALTED` name the file or task to
open next in the remedy half of the line. `STUCK-LOCK`'s remedy half is a
command: run the `release-locks.sh --release` command the line prints, exactly
as printed — it already carries `--force` when the release needs it. If the
lock wedges again, look at how its holder exits. `IDLE` and a healthy
`RUNNING` are ordinary operation.

### The second question

When the verdict is right but the cause is one layer down, add `--details` to a
single project. That pane answers five separate questions — who holds a lock,
what the queue tails look like against the tasks' real states, what the
dashboard's queue pane says, what standing posture is in force, and what the
project's most recent discovery or gate refusal was.

The refusal section is the one to read carefully, because two of its answers
look alike and are not. Every dual-channel refusal in the framework records
itself to the project's refusal ledger, and `--details` reads the most recent
one back with its timestamp, the surface that raised it, and its
`error`/`message`/`remedy`. A project that has never recorded a refusal says so
in words. A ledger that cannot be parsed is a different answer entirely: the
pane refuses rather than reporting an absence of refusals it cannot confirm — so
a missing pane is never evidence of a clean lane.

### Watching it without watching it

The verb's exit codes are ordered by severity, and the all-projects walk exits
with the worst code it saw, which makes a single cron line a whole-fleet
monitor. The codes, the aggregation rule, and a worked cron line are in the
`docs/operator-commands.md` reference named above.

### The same verdicts on a dashboard

`GET /verdicts` on the operator REST API returns the same verdicts as JSON,
resolved through the same engine the CLI calls. A dashboard built on the
endpoint and a terminal running `why.sh` cannot disagree. See
`docs/operator-api.md`.

## Deferral campaigns — arm, honour, spend

The founding rule, and the canonical statement of the `[release]` keys and the
precedence order they resolve through, are in `team/SOP-core.md` under "Deferral
Posture"; the three verbs and their flags are in `docs/operator-commands.md`
under "defer, undefer, why." This section is the walkthrough between them: what
you type, what the chain does with it, and what you see while it is in force.

Reach for a campaign when you want low bugs to stop opening releases *for a
while* — through a feature push, an upgrade window, a holiday — without editing
config and remembering to edit it back. The campaign names the release it ends
at, and the chain spends it there on its own.

### What resolves, and in what order

Three `[release]` keys carry the posture. Their accepted values and defaults
belong to the configuration key reference — [HOW_TO.md](../HOW_TO.md) section
1.9 — and the rule they serve belongs to `team/SOP-core.md`. What you need at
the keyboard is what each one decides and where you may set it:

| Key | Decides | Settable in |
|---|---|---|
| `deferred_bugs` | Whether bugs are held back at all. | `project.cfg`, `kanban.cfg` |
| `deferred_bug_level` | The highest severity that waits. Anything above it still opens a bugfix RC. | `project.cfg`, `kanban.cfg` |
| `defer_piggyback` | Whether held-back bugs ride along when a bug above the level opens an RC. | `project.cfg`, `kanban.cfg` |

The resolver behind every surface that reports a posture
(`team/scripts/lib/defer_posture.sh`) walks four tiers for a key, highest
first: **the campaign file, then `project.cfg`, then `kanban.cfg`, then the
built-in default.** The first tier holding a value wins and the walk stops
there — which is why a wrong value sitting in `kanban.cfg` can be invisible for
months and then refuse the moment you delete the `project.cfg` line above it.
Only the tier actually landed on is validated; a bad value at a tier the walk
never reached is never read and never refuses.

The campaign tier is narrower than the other three, and that is the part worth
knowing before you arm one: **an armed campaign forces exactly two of the three
keys.** It sets `deferred_bugs` on and `deferred_bug_level` to low whatever
either config file says, because that pair is what the file's name promises.
`defer_piggyback` governs consolidation rather than which bugs wait, so a
campaign never touches it — it walks `project.cfg`, `kanban.cfg`, default on its
own, campaign or no campaign. A campaign therefore cannot switch piggybacking on
for you. If you want the ride-along, set it in config.

Two campaign files can exist and exactly one is ever read: a project's own
`projects/<name>/DEFER-LOW-BUGS` beats the fleet-wide
`$KANBAN_ROOT/DEFER-LOW-BUGS`, the same project-over-root scope rule `HALT`
uses. The resolver consults one file per resolution and names the one it used.

A campaign file it cannot read is never resolved as "no campaign in force." A
file with no parseable `until=` line, or an `until=` that is not well-formed
`vX.Y.Z`, refuses dual-channel and names the file by path — because the whole
point of the file is to force deferral on, and falling through to the config
tiers would quietly resolve to whatever they happen to say.

### Arming

```bash
scripts/defer.sh --project my-app --until v2.14.0
scripts/why.sh   --project my-app --details    # confirm what is now in force
```

`defer.sh` writes `projects/my-app/DEFER-LOW-BUGS`; what an armed campaign
forces, and what it deliberately leaves to the config tiers, is the SOP-core
rule.

`why.sh --details` is how you confirm the arm landed. Its `== posture ==`
section prints one line per resolved field, and every line carries in
parentheses the tier the value came from:

```
deferred_bugs=true (campaign file)
deferred_bug_level=low (campaign file)
defer_piggyback=true (kanban.cfg)
until=v2.14.0 (campaign file: /home/you/pgai_agent_kanban/projects/my-app/DEFER-LOW-BUGS)
```

A campaign that took effect reads `(campaign file)` beside the two keys it
forces and `(project.cfg)`, `(kanban.cfg)` or `(default)` beside
`defer_piggyback`, which it never forces. The `until=` line is where the file
is named by path, so the tier word and the path arrive together. With no
campaign in force that last line reads `until=(none) (default)` and the other
three name whichever config tier or default supplied them. That is the whole
check — you never need to open the file.

`defer.sh` and `undefer.sh` only ever touch the project-scoped file. A
root-scoped `$KANBAN_ROOT/DEFER-LOW-BUGS`, which speaks for every project that
has no file of its own, has no verb: a fleet-wide campaign is an operator
writing that file by hand.

### Honouring

Nothing else changes. Requirements decompose normally, priority intake runs
normally, and a bug **above** the level still opens a bugfix RC exactly as it
always did. What changes is discovery's bug step, and it narrates every
decision into the wake log:

| Log line | What happened |
|---|---|
| `deferring N bug(s) at or below level=low` | Those N were held back this tick. |
| `piggybacking N deferred bugs (trigger: BUG-XXXX high)` | A bug above the level opened an RC, so the held-back ones rode along on it. |
| `N bug(s) remain deferred at level=low; requirements queue not empty, hygiene sweep withheld` | Everything unhandled is deferred, but the chain still has requirements work to do. |
| `hygiene sweep -- bug queue and requirements queue both empty; bundling N deferred bug(s)` | Nothing else was left to do, so the litter was bundled. |
| `WARNING severity field in <file> is missing or unparseable` | The bug's `**Severity:**` field is absent or not one of low/medium/high. It is treated as above the level and stays a candidate — an unreadable severity is never quietly swallowed into "deferred." |

A ride-along is marked on its own line in the bundle's `## Bundled Items`, and
PM consolidates those into hygiene tickets under the cap in `team/roles/PM.md`.

### Spending

You do not normally clear a campaign by hand. When a release at or past the
`until=` target ships, CM removes the project's campaign file as part of
finalizing that release and appends an audit block to the project's
`release-state.md`:

```
## Campaign Spend Event
- Timestamp: 2026-09-14T03:11:07+00:00
- Trigger: DEFER-LOW-BUGS campaign spend at ship of v2.14.0
- Until target satisfied: v2.14.0
- Resulting posture: resolves from project.cfg / kanban.cfg tiers (DEFER-LOW-BUGS removed)
```

A project that jumps straight past its target still spends the campaign — the
check is "at or past," not an exact match. With the file gone, the next
`why.sh --details` reports the config tiers instead, with no operator action in
between.

Two things the spend step deliberately does not do. It never touches a
root-scoped campaign file: that one speaks for every project, and one project's
release does not get to spend a fleet-wide promise. And it never fails a
release — a campaign file it cannot parse at ship time is left in place with a
warning, because by then the release has already tagged and a spend problem is
a finding, not a rollback.

To end a campaign before its target, run `scripts/undefer.sh --project my-app`.

### What you see while it is in force

**The status bar** carries a `BUGS DEFERRED` segment whenever the posture is on
and at least one open bug is actually being held:

```
✋ BUGS DEFERRED 4 (<=low) until v2.14.0
```

The count is deferred open bugs, the level is the resolved
`deferred_bug_level`, and the `until` suffix appears only while a campaign file
is in force. A zero count renders nothing — the bar says "bugs are waiting" or
says nothing, never "zero bugs are waiting." Under `NO_COLOR` or a dumb
terminal the text is identical minus the glyph. The global bar resolves the
framework-wide posture and sums the count across every registered project; a
per-project drill bar resolves that project's own posture and counts only its
own bugs.

**Each deferred bug card** in the BUGS column carries the same `✋` glyph as a
suffix after its id — `BUG-0348 ✋`, a single space, no glyph before the id —
so every card's id stays left-aligned in one column whether or not it carries
a glyph. The segment and the cards cannot disagree about which bugs are
waiting: both come from one resolver and one severity comparison, the same
pair discovery itself applies.

**An unreadable posture never renders as a posture.** If the campaign file
cannot be parsed, or a `[release]` value at the tier the chain landed on is
outside its accepted set, the segment reads with a warning glyph instead and
names the offending path:

```
⚠️ DEFER CONFIG UNREADABLE: /home/you/pgai_agent_kanban/projects/my-app/DEFER-LOW-BUGS
```

In that state every open bug card carries the warning glyph too, because the
code could not determine which of them would have been deferred — which is
exactly what discovery does with the same failure: it bundles nothing that
tick. Run `scripts/why.sh --project my-app --details` to get the full
dual-channel refusal, fix the named file, and the surfaces heal on the next
render.

## Deep-verify — the cold-build lane you drive yourself

The cold-build lane rebuilds every shipped container image from scratch, under
every container engine on the host. It is the only check in the system that can
see whether the artefact you ship actually builds on a stranger's machine, and
it costs roughly thirteen minutes of wall clock per sweep.

**It does not run during RC verification.** TESTER starts no container engine and
runs no container build; an RC runs the fast suites only. The lane lives in its
own tier, DEEP-VERIFY, and it is reached by a weekly scheduled job, by a
promotion-time check, or by you at a keyboard — never as a side effect of opening
or verifying an RC. An operator waiting for an RC to confirm the images still
build will wait forever.

The tier's rules are documented once and are not repeated here: its shape, the
ledger contract, what each verdict is worth, the freshness doctrine and the
no-silent-skips rule are in `docs/cm-release-reference.md` under "The DEEP-VERIFY
tier in full." Every flag on all three verbs is in `docs/operator-commands.md`,
and the promotion walkthrough is in [promotion-playbook.md](promotion-playbook.md).
This section is the sequence of actions between them: how to find out whether a
fresh green exists, what to run when it does not, and what to do with each answer.

### Is there a fresh green?

Ask the check rather than reading the ledger yourself. It is read-only — it
writes nothing and never starts the lane on your behalf:

```bash
scripts/deep-verify-freshness-check.sh --project my-app --version v2.14.0
```

Exit 0 means the most recent record is `built`, for exactly the version named,
and inside the freshness window. Exit 1 means it is not, and the message names
which condition failed. The refusal table further down maps each one to its
remedy.

Two surfaces report the same fact without being asked. Every TESTER report
carries a mandatory `## Deep-Verify Freshness` line naming when the lane last
ran, against what tree state, and with what result — including "never run" and
"the last result was red." The scheduled dispatch narrates each project it
skipped and why, naming the next due time when the reason is simply that a week
has not passed. Neither is permitted to imply a green lane that did not run, so
for a project that ships images, a report saying nothing about the lane is a
report to distrust. A project that ships no Dockerfiles is the one exception:
the lane has no subject there, and the report owes no line.

### Running it

The lane is the container tier, which is operator-only: run it from your own
shell with `PGAI_CONTAINER_TIER=1` (or `--operator-approved`) and an image
selection.

```bash
# Run the lane now for one project, outside any RC, every declared image
PGAI_CONTAINER_TIER=1 scripts/deep-verify.sh --project my-app --image all

# The same run, under its narrow name
PGAI_CONTAINER_TIER=1 scripts/cold-build-now.sh --project my-app --image all
```

Budget thirteen minutes. The run appends exactly one record to the project's
ledger at `$KANBAN_ROOT/projects/<name>/deep-verify-ledger.md`, and that record
is what every later reader consults — the freshness check, the TESTER report, and
the next scheduled run's own self-gate.

A call without the authorization, or from inside an agent wake, is not an error:
it prints one `SKIPPED` line naming the command to run instead and exits 0,
with no lane run and no record written, so the ledger keeps its last real result.
A call with no `--image` and no `kanban.cfg [release] container_images` default
lists the available images and also exits 0 having built nothing. Every run and
every skip leaves a line in `logs/docker-use.log`, and
`scripts/why.sh --project my-app --details` shows the latest one. The flags,
exit codes and log format are in `docs/operator-commands.md`, "deep-verify,
cold-build-now."

The weekly dispatch is a different invocation and belongs in cron rather than in
your hands:

```bash
PGAI_CONTAINER_TIER=1 scripts/deep-verify.sh --scheduled --image all
```

It takes no project. It fans out over every active registered project and
self-gates on each one's ledger age and off-hours window before anything
expensive happens, so a project that is not due costs a narration line and
nothing else. The ready-to-paste cron and pseudocron entries are in
`docs/operator-commands.md`, "deep-verify, cold-build-now."

### What each verdict asks of you

A run ends in exactly one of three verdicts, and they do not share a response.

| Verdict | What it establishes | What you do |
|---|---|---|
| `built` | The images built cold, on this host, against this tree state. Recorded only when at least one pair built, or no pair was narrate-skipped. | Nothing. The record now speaks for that version until it ages out. If the run still exited 1, a non-build cold test failed: `detail` names it, and the run filed a bug naming the test's file and test id, or cited the open one already naming it. Do not file a second; the loop fixes the test, and the `built` record still satisfies the pre-release check. |
| `failed-to-build` | The build environment was working and the build still failed — a defect in the tree. | Nothing by hand. The run already filed a bug naming the failing engine/flavor pair, or the failing image-build test when no step line was captured; do not file a second. Let the loop fix it, re-run the lane against the fixed tree, and do not promote over it. |
| `could-not-attempt` | Nothing was learned about whether the images build. No container engine on the host, or an unreachable registry — including a run where no pair built, every pair it reached was narrate-skipped, and only the engine-free cold tests ran. | Fix the host, then re-run the lane; this record does not satisfy the pre-release check. File no bug against the build — no agent in the loop can install a container engine on your box. If the run exited 1, an engine-free cold test failed: `detail` names it beside the skip reasons, and the run filed a bug naming the test, or cited the open one already naming it. Do not file a second. |

The discrimination between the second and third is made by the run, not by you,
and it is already in the record's `verdict` field. Read it; do not re-derive it.
The trap worth naming is that `could-not-attempt` is **not** a weak red — it can
never satisfy a promotion no matter how recent it is, and it never earns a bug
against the build. The one bug it can carry is for a failing engine-free test,
which the run files itself.

### The per-project switch

Two `[release]` keys govern the **scheduled** trigger and nothing else;
on-demand and pre-release invocations ignore both. `deep_verify` sets the
cadence. `deep_verify_offhours_window` sets the daily UTC window a scheduled run
may fire in, and a malformed value is skipped with a warning rather than
refused. Each resolves per project, highest first: the project's own
`project.cfg`, then `kanban.cfg`, then the default (`weekly`, `03:00-03:59`). So
`deep_verify = off` in `kanban.cfg` turns the schedule off for every project
that does not set its own value, and a project's `project.cfg` can turn it back
on or off for that project alone. Accepted values and defaults are the
configuration key reference's — [HOW_TO.md](../HOW_TO.md) section 1.9.

The default is opt-out rather than opt-in, deliberately: a lane that runs only
when somebody remembers to enable it is a lane that stops running. Set the
cadence off for a project that ships no container images, or one whose operator
has decided the weekly cost is not worth paying.

This kanban's own project is the worked example of the second case. It ships
three Dockerfiles, and `$KANBAN_ROOT/projects/pgai-agent-kanban/project.cfg`
nonetheless carries:

```
[release]
max_rc_retries = 1
deep_verify = off
```

That setting costs nothing on a scheduled tick — no lane, no container engine, no
ledger write, one narration line, exit 0. It also has one consequence that is
easy to miss and expensive to discover at the wrong moment: **turning the
schedule off does not turn the pre-release check off.** Nothing will have run to
satisfy that check, so a public tag for this project needs an explicit on-demand
run first. An unrecognized value is never resolved into a guessed mode; the
scheduled invocation refuses dual-channel and runs nothing.

### Satisfying the pre-release refusal

The freshness check is a named step in the promotion playbook, run before a
public tag is pushed. Each refusal exits 1 and prints a dual-channel error
contract whose machine key is greppable and whose remedy line is runnable as
printed. What each one asks for:

| Refusal key | What it found | Remedy |
|---|---|---|
| `freshness_gate.no_record` | The tier has never run for this project. Silence is not a pass. | Run the lane, then re-run the check. |
| `freshness_gate.failed_to_build` | The last record is a tree defect. | Let the loop fix the build, re-run the lane against the fixed tree, re-run the check. Do not promote over it. |
| `freshness_gate.could_not_attempt` | The last record learned nothing about the tree. | Re-run the lane on a host with a working container engine and reachable registries. |
| `freshness_gate.stale_record` | Green, but for a different version, or past the window, or both. The message names which. | Re-run the lane against the tree being promoted. |
| `freshness_gate.ledger_unreadable` | The ledger exists but its most recent record does not parse. | Repair or remove the malformed record by hand, then re-run the lane. |
| `freshness_gate.unregistered_project` | The `--project` name is not in `projects.cfg`. | Correct the name. |

A malformed ledger refuses rather than reporting never-run, so a parse failure
can never be mistaken for a clean slate. The full walkthrough for each refusal,
with the worked error-contract output, is step 0 of
[promotion-playbook.md](promotion-playbook.md).

## Release State File — migration and rationale

The schema, field semantics, and `pp_last_released_version` contract for `release-state.md` are documented in `team/roles/CM.md` under "Release state." This section covers the operator-facing install.sh migration and the historical rationale for the split.

### Why the split

Multiple bug classes traced back to the old design where `release-state.md` tried to be authoritative for both in-flight RC state and historical release state. Deterministic patches drifted because `upgrade.sh` shipped tags but never edited the file. Scripts read different copies of `release-state.md` from different paths. `install.sh` did not seed the file uniformly. All of those are gone now: git history is immutable, the tag IS the release, there is nothing to keep in sync with anything.

### Migration

`install.sh` migrates existing installs automatically:

- Preserves `## Active RC` if non-`none` (operators in the middle of an in-flight RC do not lose it).
- Drops `Last Released At` and `Last Released By Task` if present in the old file (these fields are no longer part of the schema).
- Preserves `## Last Released` if present; otherwise leaves it absent so the next `team/scripts/cm/release.sh` Step 15 writes it on the first successful release.
- Writes the canonical schema (`## Active RC`, `## RC Opened At`, `## RC Opened By Task`, `## Last Released`).

The previous repo-level `team/release-state.md` has been removed entirely. The only `release-state.md` files in the system are project-scoped at `$KANBAN_ROOT/projects/<project-name>/release-state.md`. No manual file editing is required during upgrade.

## CHANGELOG Disclosure Model

`CHANGELOG.md` is regenerated in full at every release as a projection of two sources of truth — the per-release notes files and the bug ledger — so historical entries update themselves whenever a bug's `## Affects` or `## Fixed In` fields change. Known Issues are disclosed only for bugs that existed in a PUBLISHED release (a version listed in `release-notes/PUBLISHED`), so internal-only bugs born and fixed between public tags are never surfaced in the changelog. Public IDs (`KI-<version>.<counter>`) are sticky citations: assigned once at first disclosure, persisted on the bug file, and never renumbered — the writer reads them back rather than recomputing them, so any KI reference ever made stays valid. A missing `release-notes/PUBLISHED` manifest is a fail-loud generator error, not a silent empty state — see [promotion-playbook.md](promotion-playbook.md) for the operator step that maintains the manifest after a public tag push.

### Releases produced outside `cm/release.sh`

`team/scripts/cm/release.sh` renders `CHANGELOG.md` and commits it before it tags. A release produced any other way, such as an operator hand patch, must do the same by hand. Otherwise the tag ships a changelog with no section for its own release, and `team/scripts/lint_changelog_freshness.py` reports the file stale on the main branch and on every feature branch cut after it.

After the release notes for the new version are committed and before you tag, run from the dev-tree root:

<!-- doc-lint: skip — pp_run_ops.sh is a module runner that takes a module name, not a command that answers --help, and this block overwrites CHANGELOG.md and commits in the tree it runs in -->
```bash
bash team/scripts/lib/pp_run_ops.sh pgai_agent_kanban.cm.changelog_writer "$PWD" "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-agent-kanban/bugs" > CHANGELOG.md
python3 team/scripts/lint_changelog_freshness.py --changelog CHANGELOG.md --bugs-dir "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-agent-kanban/bugs"
git add CHANGELOG.md
git commit -m "Update CHANGELOG.md for vX.Y.Z"
```

These are the two commands `lint_changelog_freshness.py` prints under its `To fix:` heading when it finds the file stale; it prints them with your absolute paths filled in. Use the bugs directory of the project you are releasing, and its version in the commit message. The lint must exit 0 before you commit. Never edit the rendered file by hand: the writer owns it, and a hand edit trips the same lint.

## Manual Hotfixes During In-Flight RC

This section states the project's policy on direct commits to an in-flight RC branch outside the kanban pipeline. The canonical recovery path runs through the agents; manual hotfixes are **sanctioned-with-attribution** as an exception, never as routine practice.

### Canonical halt-recovery path

When a bug is filed against an in-flight RC (typically by TESTER, triggering a halt), the canonical sequence is:

1. **TESTER files the bug** in `projects/<name>/bugs/` and, in the autonomous Path C case, writes a priority requirements document targeting the next patch.
2. **PM bundles the bug** into a priority requirements document at next wake and marks it `[x]` in `bug_backlog.md`.
3. **PM decomposes** that document into one or more fix tickets via the PM 3-Path Decision Tree.
4. **CODER (or WRITER) implements the fix** on a feature branch, commits, merges `--no-ff` into the RC branch, and deletes the feature branch.
5. **TESTER re-verifies** the RC against the original acceptance criteria plus the new fix.
6. **CM ships** under the ship-by-default policy.

This path produces a complete audit trail: bug file, requirements doc, task folder, feature branch, merge commit, verification report.

### Policy on manual hotfix commits

Manual hotfix commits to an in-flight RC branch — commits not produced by a CODER or WRITER feature-branch merge — are **sanctioned-with-attribution**. They are permitted only when the operator judges the canonical path unworkable for this specific fix (for example, the wake scripts themselves are broken in a way that prevents agents from running, or the fix is a single-line correction the operator can verify by inspection faster than a CODER cycle can complete).

A manual hotfix is never the preferred path. The default is always the canonical recovery path above. Choosing the hotfix path is an explicit operator decision, not a fallback an agent may take.

### Attribution requirement

When a manual hotfix is taken, the closing bug file must record the attribution in its `## Resolved By` field in the form:

```
## Resolved By
manual hotfix by operator on <commit-sha>
```

A bug closed via the canonical path records the CODER task ID instead (e.g. `CODER-YYYYMMDD-NNN-fix-bug-XXXX`). These are the only two accepted forms.

This section is the field's definition; the bug template does not carry it. `projects/<name>/bugs/templates/BUG-TEMPLATE.md` has no `## Resolved By` heading, so add one when you close the bug. The PM bug scanner (`team/pm-agent/lib/bug_scanner.py`) reads the field. It checks only that the section is present and non-empty, not which of the two forms it holds — see Enforcement below.

### Enforcement

The PM bug scanner (`team/pm-agent/lib/bug_scanner.py`) refuses to treat a `## Status: done` bug as closed unless `## Resolved By` is populated. A done bug with the field empty or absent is treated as open (`[ ]` in `bug_backlog.md`) and emits a UserWarning. This makes manual hotfixes visible in the audit trail by forcing the operator to record them at close time.

## Environment: Source-Then-Fail-Loud

Every kanban entry point resolves `PGAI_AGENT_KANBAN_ROOT_PATH` the same way, through a single shared prelude (`team/scripts/lib/env_bootstrap.sh` on the bash side, `pgai_agent_kanban/env.py` on the Python side). The prelude runs before any command-specific logic. Operators do not need to source `shell-env` manually on a standard install — but when they do, their exports still win.

The contract is five points, in the order they take effect:

1. **The prelude sources `shell-env` automatically.** Every entry-point script under `scripts/` (top-level, `scripts/cm/`, `scripts/dashboard/`) sources the prelude as its first act after the shebang. If `PGAI_AGENT_KANBAN_ROOT_PATH` is not already set, the prelude derives a candidate root from the calling script's own location, walks upward past the `scripts/`, `cm/`, and `dashboard/` layers, and sources `<candidate>/shell-env` when the file exists. That is where the root env (and PATH, virtualenv, provider env) is normally exported. From a fresh unsourced shell on a standard install, `scripts/api-server.sh status`, `scripts/show.sh`, and any `scripts/cm/*.sh` or `scripts/dashboard/*.sh` script just work — the prelude handled the sourcing.

2. **The prelude then sources `claude-env`, always after `shell-env`.** `claude-env` carries runtime tuning the installed provider CLI binary reads — a concern `shell-env`, `secrets`, and `kanban.cfg` do not own; see the four-concerns law in [HOW_TO.md](../HOW_TO.md) section 1.2 for which file a given value belongs in and what each misplacement costs. The order is fixed and not incidental: `shell-env` is what establishes the root and the virtualenv `claude-env` resolves against, and a name set in both files resolves to whatever `claude-env` sets, because it sources later. The file is optional — an absent `claude-env` is a clean no-op, with no error, no warning, no output, and no change to the calling script's exit status. A `claude-env` that exists is syntax-checked with `bash -n` *before* it is sourced; a file that fails the check is not sourced at all, and the prelude refuses through the shared dual-channel error contract, naming the file's path and the `bash -n` command to run. That refusal is BLOCK-WORTHY by the [blocking-site census](blocking-site-census.md): `source` executes a script's earlier statements as it parses along, so sourcing a broken file half-configures the environment of a firing that may have no human present to notice. The refusal names the file's path and never a value the file sets.

3. **Fail-loud when `shell-env` is missing or broken.** If the prelude runs, sources what it can, and still finds `PGAI_AGENT_KANBAN_ROOT_PATH` unset, it exits 1 with a diagnostic naming the exact path it tried:

    ```
    PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken at <candidate>/shell-env
    ```

This is the same fail-loud posture established for `api-server.sh`, upgraded to guard the new class of failure — "shell-env missing or broken" — instead of the old class ("operator forgot to source"). A syntax error inside `shell-env`, a wrong path, or a deleted file all surface with the same actionable message. No silent defaults paper over the failure.

One deliberate consequence for upgrades: invoking `upgrade.sh` from
the DEV TREE requires the explicit target
(`PGAI_AGENT_KANBAN_ROOT_PATH=... ` or `--kanban-root`) — the prelude
derives its root from the script's own location and never derives
across trees, so a dev-tree invocation has no live shell-env to find.
This is the two-repo boundary applied to the upgrader itself.

4. **Operator-set env always wins.** If `PGAI_AGENT_KANBAN_ROOT_PATH` is already exported in the caller's shell when the entry point runs, the prelude honors that value verbatim and returns without touching `shell-env`. This is the no-masking guarantee — the operator's explicit env outranks anything the file would have said. `PGAI_AGENT_KANBAN_ROOT_PATH=/custom/root scripts/show.sh …` uses `/custom/root` even when `shell-env` on disk says otherwise. Sourcing the prelude twice is harmless: the second call sees the export and returns immediately.

5. **Manual `source shell-env` still works.** Operators who prefer the classic workflow — source `shell-env` once at the start of a shell session, then run commands — lose nothing. Because the prelude honors an already-set env (point 4), sourcing `shell-env` yourself sets the export before any prelude runs, and every subsequent invocation short-circuits at the idempotency guard. Sourcing manually and letting the prelude do it produce identical results.

The Python side follows the same contract via `pgai_agent_kanban/env.py`: it reads the env, absolutizes with `realpath`, and fails loud with the same message grammar if unset. Python entry points (the API app factory, the changelog writer, the lint CLIs) all route through this one resolver, so the `bash → prelude → shell-env → uvicorn → Python` chain preserves the operator's env end-to-end.

Three consequences worth stating plainly:

- **No conventional default remains.** The historical `:-$HOME/pgai_agent_kanban` fallback has been deleted from every entry point. The prelude is the guard. Non-standard installs that used to self-mask now surface the misconfiguration through the fail-loud message.
- **The wake family still self-bootstraps.** Cron-driven wake scripts have always established their own environment via `wake_common.sh`; that path is preserved and either delegates to the prelude or is verified equivalent. Cron behavior does not change.
- **The wake path sources `shell-env` and `claude-env` itself, in that order.** A wake firing pre-exports the root before it calls the prelude, which trips the prelude's own idempotency guard (point 4) and short-circuits before either file is reached — so `wake_bootstrap.sh` re-sources both directly, applying the same `bash -n`-before-`source` check to `claude-env` that the prelude applies. That is what makes "sourced on every entry path" true of a cron or pseudocron firing, which has no interactive shell to inherit provider tuning from.

For the shell-side reference, see `team/scripts/lib/env_bootstrap.sh`. For the Python-side reference, see `pgai_agent_kanban/env.py`. (These supersede the earlier convention of bare root-relative env references, which failed loud in cross-tree contexts.)

## Configuration File System

The framework has six configuration files across two formats: four operator-owned files at the kanban root, plus the project registry and each project's own identity file. Together they are the complete operator-visible configuration surface; no other file needs to be edited to tune framework behavior.

**Which file a given value belongs in is not a matter of taste.** Four of them sit at the kanban root, each owning exactly one concern — `kanban.cfg` kanban behaviour, `shell-env` the shell environment the kanban needs to run, `claude-env` tuning the provider CLI binary itself reads, `secrets` credentials. A provider-tuning value that lands in `secrets`, or a credential that lands in `claude-env`, defeats the separation all four exist to preserve — the tuning becomes unreviewable behind a file nobody may share, and the credential loses the `600` mode and the never-commit rule that were the whole point of `secrets`. That rule, and what each of the four misplacements costs, is stated once in [HOW_TO.md](../HOW_TO.md) section 1.2, "The four-concerns law." It is deliberately not restated here: one statement of a boundary cannot drift from itself. Read it there before editing any of the four.

### The config files

| File | Format | Role |
|------|--------|------|
| `kanban.cfg` | INI (`[section] key = value`) | Framework operational settings: dashboard layout, chain tuning, active provider, directory paths, wake bounds, per-agent model overrides |
| `shell-env` | Bash (`export VAR=value`) | The wake environment: `PGAI_AGENT_KANBAN_ROOT_PATH`, PATH additions, Python virtualenv activation |
| `claude-env` | Bash | Runtime tuning the installed provider CLI binary reads. Sourced by the prelude immediately after `shell-env` on every entry path — see "Environment: Source-Then-Fail-Loud" above |
| `secrets` | Bash | Credentials only: OAuth tokens, API keys. Mode `600`; never committed |
| `projects.cfg` | INI | Project registry: one entry per registered project |
| `project.cfg` (per project) | INI | Per-project identity: git repo URL, dev tree path, workflow type, version ceilings |

The three bash files are sourced because the wake layer needs `export` semantics and one of them holds credentials. The three INI files are data files — they are parsed, not executed, which prevents the command-injection class of errors.

Two older bash files, `env` and `bashrc`, are legacy compatibility only: no template ships for either and no installer creates them, but a number of standalone scripts still source them when present so that installs old enough to have them keep working. Nothing new belongs in either — route the value to one of the four above.

`install.sh` auto-seeds `kanban.cfg` and `claude-env` from their templates on a fresh install; `shell-env` and `secrets` are deliberately opt-in, copied from `shell-env_example` and `secrets_example` by hand, because PATH layouts and tokens are personal. All four root files are create-if-absent and protected thereafter: once the file exists, neither install nor upgrade overwrites it, while the `*_example` template beside it is refreshed on every upgrade — so the template always shows the current shipped guidance and your edits stay as you left them. [HOW_TO.md](../HOW_TO.md) section 1.6 covers the `claude-env` case in full, including the background-wait ceiling and its operator override.

### Getting started

`install.sh` creates `kanban.cfg` from the template on a fresh install. To customize it:

```bash
# View the template with inline comments for every key
cat "$PGAI_AGENT_KANBAN_ROOT_PATH/kanban.cfg_example"

# Edit per-install settings
$EDITOR "$PGAI_AGENT_KANBAN_ROOT_PATH/kanban.cfg"
```

For a new project's `project.cfg`:

```bash
cat "$PGAI_AGENT_KANBAN_ROOT_PATH/project.cfg_example"
$EDITOR "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/project.cfg"
```

### Migration from legacy config file names

If upgrading from an older install with legacy config files, `install.sh` handles migration automatically:

- `config.cfg` (bash format) is migrated to `kanban.cfg` (INI format) inline by `install.sh`
- `PROJECT.cfg` (bash format, uppercase) per project is migrated to `project.cfg` (INI format, lowercase)

The migration is idempotent — running it multiple times is safe. No manual file edits are required.

### INI format

All three INI files use standard `[section] key = value` syntax. Comments begin with `#` or `;`.

```ini
[dashboard]
# Minimum column height even when few projects are registered
min_rows_per_column = 13
max_rows_per_column = 34
min_rows_per_project = 3
max_rows_per_project = 8
```

Scripts call `read_ini` from `team/scripts/lib/ini_parser.sh` to retrieve values with a fallback default:

```bash
source "$KANBAN_ROOT/team/scripts/lib/ini_parser.sh"
val=$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard min_rows_per_column 13)
```

### Tuning kanban.cfg

Operators most commonly tune five sections.

**`[dashboard]` — layout knobs:**

| Key | Default | Effect |
|-----|---------|--------|
| `min_rows_per_column` | `13` | Column height floor (prevents sparse look with few projects) |
| `max_rows_per_column` | `34` | Column height ceiling (your terminal/screen constraint) |
| `min_rows_per_project` | `3` | Rows guaranteed to each project (readability floor) |
| `max_rows_per_project` | `8` | Rows allocated to each project when space allows (visual ceiling) |

The dashboard renderer applies a dynamic algorithm: as project count grows, per-project allocation steps down from `max_rows_per_project` toward `min_rows_per_project` to stay within `max_rows_per_column`. With few projects, `min_rows_per_column` enforces a minimum visual height. This replaces the old hardcoded `MIN_PER_PROJECT = 3` constant.

Example with 5 projects and defaults (`min_proj=3, max_proj=8, max_col=34`):
`5 × 8 = 40 > 34` → step down: `5 × 6 = 30 ≤ 34` → allocate 6 rows per project.

**`[chain]` — pipeline behavior:**

| Key | Default | Effect |
|-----|---------|--------|
| `pm_mode` | `automatic` | `automatic` enables PM autonomous scan; `manual` disables it |

**`[wake]` — per-wake work limits:**

| Key | Default | Effect |
|-----|---------|--------|
| `max_tasks_per_wake` | `5` | Tasks processed per agent wake; lower it to `1` when you want one task per wake for traceability |
| `lock_wait_seconds` | `120` | Seconds a wake retries, with backoff, for a project repo lock another wake chain holds before it defers; `0` = a single attempt. See "Repo lock and contention" |
| `lock_starvation_firings` | `3` | Consecutive firings a role may end deferred on repo-lock contention with a BACKLOG task before the wake logs a starvation warning and PM wakes skip discovery; `0` turns the guard off. See "Repo lock and contention" |

**`[paths]` — directory layout:**

| Key | Default | Effect |
|-----|---------|--------|
| `dev_tree_path` | `""` | Install-wide Git checkout where CODER agents do source-tree work. No default — empty unless you set it; it is a convenience fallback for projects whose own `project.cfg` omits the key |

Leave the per-project path keys (`requirements_dir`, `priority_dir`, `archive_dir`, `tasks_dir`, `logs_dir`) commented out for multi-project mode — per-project helpers resolve these automatically. Set them only for single-project legacy installs.

**`[cleanup]` — what `cleanup.sh` keeps:**

| Key | Default | Effect |
|-----|---------|--------|
| `retention_days` | `30` | Days `cleanup.sh` keeps log files and briefs. Task folders are never deleted; see "Cleanup Script" and "Archive — finished work out of the live project" |
| `trivial_log_bytes` | `1700` | Size below which a wake batch log counts as trivial and is purged once it is older than `trivial_log_hours`; see "Log retention (two-tier model)" |
| `trivial_log_hours` | `6` | Age in hours a trivial log must pass before it is purged |

An environment variable set in the calling shell wins over each key. An older `kanban.cfg` may carry the log retention as `[paths] cleanup_retention_days`. That spelling is deprecated: `cleanup.sh` reads it only when `[cleanup] retention_days` is absent, and warns about it. Move the value to `[cleanup] retention_days` and delete the `[paths]` line. The full resolution order is "Configuration" under "Cleanup Script".

The template with every key, its inline comment, and its shipped value is `kanban.cfg_example`: at the repository root in a checkout, and beside `kanban.cfg` at the kanban root in an install, where every upgrade refreshes it. The accepted key set and each key's default are declared by the Key Registry in `team/scripts/lib/config_loader.sh`; where the template and the registry disagree, the registry is what the loader applies. [HOW_TO.md](../HOW_TO.md) section 1.9 is the per-key reference.

The `[release]` section also carries the ship posture, `ship_posture` and `ship_posture_confirm_remote`, which a project's own `project.cfg` overrides. Their values, resolution, and the remote-lane guard are in "Ship posture — shipping through a ship-policy stop" above.

### Model override variables

The model a task runs on is resolved from `kanban.cfg` at wake startup, or from the task's own override. Nothing else participates in that choice — in particular, no shipped subagent wrapper does.

Per-agent model overrides live under the per-provider sections `[models.claude]` and `[models.codex]`. The framework resolves the active provider from `[providers] active`, reads the matching `[models.<active_provider>]` section, and exports each role key in it as `PGAI_<ROLE>_MODEL` for the wake script to consume.

As shipped in `kanban.cfg_example` (a fresh install's `kanban.cfg` is a copy;
an upgrade never rewrites an existing one):

```ini
[models.claude]
default = claude-sonnet-5-5
pm =
coder =
writer =
tester =
cm =
po =

[models.codex]
default = gpt-6.1-sol
pm =
coder =
writer =
tester =
cm =
po =
```

Every role runs on its section's `default` until you set the role's own key —
for example `coder = claude-opus-5-5`. Check the model names your provider CLI
currently lists before setting or keeping one; they change over time.

**Selection precedence, highest first:**

1. **`## Model Override` in the task README** — pins that one task to one model. PM writes the field from a requirements document's `## Model Overrides` hints; see `team/roles/PM.md` "Model overrides".
2. **`PGAI_<ROLE>_MODEL`**, populated from the active provider's section — the role's own key when it is set, otherwise that section's `default` key.
3. **The provider CLI's built-in default** — reached only when the role key and the section `default` are both empty. The wake logs a warning naming the role when it takes this path, because at that point nothing in the configuration chose the model.

Whichever tier wins is the model that performs the work, not merely the one that dispatches it. The wake stamps that value into the task's `status.md` `## Model`, and `artifacts/tokens.json` records which model actually produced the tokens; when the two disagree the wake narrates a warning naming both, so a substitution cannot pass unnoticed.

Model IDs are provider-specific (`claude-sonnet-5-5` is meaningful only to the Claude CLI; codex uses its own model namespace). Grouping by provider lets a single `kanban.cfg` carry the correct values for every provider and pick the right ones automatically when you switch active providers — no edit, no restart.

Switching `[providers] active` (or running `scripts/switch-provider.sh --provider PROVIDER`) flips which `[models.<provider>]` section the next wake firing reads. No code change, no crontab edit. See "Provider Switch" below.

There is no fallback to a flat `[models]` section. A pre-existing flat `[models]` block in an older `kanban.cfg` is silently ignored — move its values into `[models.claude]` (or whichever provider's section applies) when you upgrade.

**The subagent wrappers do not select a model.** The role wrappers the kanban ships — `subagents/*.md`, deployed to `~/.claude/agents/` and `~/.codex/agents/` — carry no frontmatter `model:` key. They define a role's name, description, tool set, and prompt, and they inherit whatever model the precedence chain above resolved. `subagents/MANIFEST.txt` states the rule for anyone adding a wrapper, and a unit test fails the suite if a wrapper grows the key.

The prohibition is not cosmetic. An explicit model in a subagent definition outranks the model of the session that invoked it, so a wrapper carrying one would quietly beat all three tiers above — including a per-task `## Model Override` — while the configuration, the wake log, and `status.md` all went on reporting the value the operator set. Model selection is a configuration concern; the wrappers stay out of it.

**Editing a wrapper in the repository changes nothing about what runs.** The deployed copies refresh only on an install or upgrade run that explicitly asks for them:

| Lane | Deployed to | Refreshed by |
|---|---|---|
| Claude | `~/.claude/agents/` | `install.sh --add-claude-agents` |
| Codex | `~/.codex/agents/` | `install.sh --add-codex-agents` |

Without the flag, the installer writes nothing under that provider's agents directory — no files, no directory creation, no prompts. Pass both flags to refresh both lanes. So after changing a wrapper, or after taking an upgrade whose release changed one, run the install with the flag for every lane you actually use; until you do, the copy already on disk keeps serving every wake. Existing copies are backed up before being overwritten — see "Role-file handling (multi-provider)" under "Defensive install.sh Behavior" below.

## Cleanup Script

The cleanup script at `scripts/cleanup/cleanup.sh` performs automated housekeeping: it deletes old logs, rotates debug logs, moves old briefs aside, and purges the framework temp directory. It is designed for cron invocation and requires no LLM involvement.

**It never deletes a task folder.** Finished tasks, bugs, priorities and shipped requirements leave the live project only through the archive, which moves them and can restore them — see "Archive — finished work out of the live project" below.

Command examples in this section run from the kanban root (`cd "$PGAI_AGENT_KANBAN_ROOT_PATH"`). In a dev tree the same script is `team/scripts/cleanup/cleanup.sh`.

### What it does

A full run performs these steps in order for one project. The numbers are the script's own step labels, as they appear in its log; there is no Step 3.

0. **Purge trivial logs** — deletes small, old per-firing wake batch logs under `logs/agents/` (see "Log retention" below).
1. **Prune old log files** — deletes files directly in `PGAI_LOGS_DIR` (default `logs/agents/`) older than the log retention period.
2. **Report terminal task folders** — counts the `DONE` and `WONT-DO` task folders whose `status.md` is older than `PGAI_CLEANUP_TASK_RETENTION_DAYS` (default 7), logs that count, and names the command that previews the archive. Nothing is deleted, at any age, in a dry run or a real one.
4. **Rotate debug logs** — moves each `logs/debug/<agent>.log`, kanban-wide and per project, last written on an earlier UTC day to `logs/debug/archive/<YYYYMMDD>-<agent>.log` beside it, and starts a fresh file.
5. **Archive old briefs** — moves brief files in `PGAI_BRIEFS_DIR` older than the log retention period into `PGAI_ARCHIVE_DIR/briefs/` (default `projects/<project>/tasks/archive/briefs/`).
6. **Purge the framework temp dir** — removes the kanban-owned contents of `PGAI_AGENT_KANBAN_TEMP_DIR`, keeping the root itself and the worktree of every task still `WORKING`. The purge is skipped while any wake lock is held.

Cleanup does not touch requirements. Shipped requirements leave the live project through the archive, which also empties any `projects/<project>/tasks/archive/requirements/` directory left by older installs into `archive/<project>/requirements/` — see "The old in-project requirements archive" below. The briefs archive under `tasks/archive/briefs/` is not part of that move and stays where it is.

### Running without a project

With no `--project`, and `PGAI_PROJECT_NAME` unset or empty, the script runs once per project registered in `projects.cfg`, in priority order. Each pass re-invokes the script as `cleanup.sh --project <p>` with the same `--dry-run`, `--trivial-only` and `--temp-only` flags, so each pass is exactly the single-project run above and writes its own summary log.

- One result line per project is printed: `project '<p>': ok` or `project '<p>': FAILED (exit N)`.
- A failing project does not stop the remaining ones.
- The exit status is `0` when every pass succeeded, otherwise the exit status of the first pass that failed.
- With no project registered, the script logs one line saying there is nothing to clean and exits `0`.
- `--trivial-only` and `--temp-only` repeat per project too. Their purges are framework-wide, so the first pass does the work and later passes find nothing left to remove.

This is what the bare weekly cron line does. A crontab installed before this behavior shipped carries that bare line and needs no change.

### Log retention (two-tier model)

Cron-driven wake scripts fire on a stagger across every agent, every minute of every hour. The majority of those firings find no work to do — the agent logs "starting / no pending tasks / done" and exits. These trivial logs are typically under 1700 bytes and lose their diagnostic value within a few hours. Real agent work (subagent transcripts, debug output, error traces) produces logs an order of magnitude larger, and remains useful for days.

To avoid drowning substantive logs in trivial chatter — and to keep file-descriptor and inotify pressure bounded — the cleanup script applies two different retention rules:

- **Tier 1 — trivial logs (aggressive purge).** A log under `logs/agents/` is "trivial" when it is BOTH smaller than `PGAI_CLEANUP_TRIVIAL_LOG_BYTES` AND older than `PGAI_CLEANUP_TRIVIAL_LOG_HOURS`. Trivial logs are deleted on every cleanup run (Step 0). Substantive logs (≥ the size threshold) are left alone here and fall through to Tier 2.
- **Tier 2 — substantive logs (normal retention).** Everything in `PGAI_LOGS_DIR` older than `PGAI_CLEANUP_RETENTION_DAYS` is deleted (Step 1). This is the original, coarser sweep; it now handles only logs that survived Tier 1 (recent or substantive) plus anything outside the per-firing batch-log path.

#### Tunable env vars

| Env var | Default | Purpose |
|---|---|---|
| `PGAI_CLEANUP_RETENTION_DAYS` | `30` | Tier-2 retention for substantive logs (Step 1), and the age at which Step 5 moves a brief to the briefs archive. |
| `PGAI_CLEANUP_TASK_RETENTION_DAYS` | `7` | Age past which Step 2 counts a terminal task folder in its report. No task folder is deleted at any age. |
| `PGAI_CLEANUP_TRIVIAL_LOG_BYTES` | `1700` | Tier-1 size threshold. A log smaller than this is a candidate for aggressive purge. |
| `PGAI_CLEANUP_TRIVIAL_LOG_HOURS` | `6` | Tier-1 age threshold. A log younger than this is preserved even if small, so the operator can still inspect a freshly-fired agent. |

The defaults are tuned for the observed log-size distribution. Raise `PGAI_CLEANUP_TRIVIAL_LOG_BYTES` if you find legitimate work logs are being eaten; lower it if you want a more aggressive sweep. Raise `PGAI_CLEANUP_TRIVIAL_LOG_HOURS` if you frequently investigate "what happened during last night's run" the morning after.

#### The `--trivial-only` flag

```bash
# Trivial-tier sweep only — fast, safe to run on a tight schedule.
scripts/cleanup/cleanup.sh --trivial-only

# Full sweep — Steps 0, 1, 2, 4, 5 and 6 (requires each project's release-state.md).
scripts/cleanup/cleanup.sh
```

`--trivial-only` runs Step 0 and exits. It does NOT touch substantive logs, task folders, debug logs, or briefs. It also skips the release-state preflight, so it is safe to run on systems without an active RC. Combine with `--dry-run` to preview what would be deleted.

#### The `--temp-only` flag

```bash
# Framework temp-dir purge only — safe; does not touch logs, tasks, or archives.
scripts/cleanup/cleanup.sh --temp-only
```

`--temp-only` runs the temp-dir purge step and exits. Like `--trivial-only`, it skips the release-state preflight and the archive-directory setup, so it is safe to run on systems without an active RC. Use this for targeted recovery when `/tmp` (or the configured temp dir) needs reclaiming without touching anything else.

#### What is never auto-deleted

The trivial purge is scoped narrowly on purpose, and nothing in the script deletes kanban state:

- **`actions.log`** — the framework's audit trail. Excluded both by path scope (it lives outside `logs/agents/`) and by an explicit `-not -name actions.log` safety guard in case a future layout change places one inside the swept tree.
- **Per-task logs in `projects/*/tasks/<TASK-ID>/logs/`** — these are the audit trail of what each task did. No cleanup step reaches them.
- **Task folders, at any state and any age.** Step 2 only counts them. Finished ones leave the live project through the archive, which moves rather than deletes.
- **Anything under `archive/<project>/`.** Cleanup never reads or deletes the archive tree.

If you discover something else should be exempt, fix the find predicate in `cleanup.sh`. Do not work around it by raising thresholds — the goal is to keep operators from ever needing to hand-edit retention numbers in an emergency.

#### Recommended cron cadence

Two entries: a fast trivial-only sweep that runs often, and a full sweep that runs less often. Neither needs `--project`: each runs once per registered project.

```cron
# Daily — trivial-log purge at 04:15. Fast; can run any time, but off-peak is courteous.
15 4 * * * $HOME/pgai_agent_kanban/scripts/cleanup/cleanup.sh --trivial-only >> $HOME/pgai_agent_kanban/logs/cron-cleanup-trivial.log 2>&1

# Weekly — full cleanup Sunday at 04:00, once per registered project. Includes
# substantive log retention, the terminal-task report, debug-log rotation,
# the brief archive and the temp purge.
0 4 * * 0 $HOME/pgai_agent_kanban/scripts/cleanup/cleanup.sh >> $HOME/pgai_agent_kanban/logs/cron-cleanup.log 2>&1
```

These lines assume the default kanban root, `$HOME/pgai_agent_kanban`. `scripts/install-crontab.sh` installs both from the tier template it selects (`templates/install/crontab-{small,medium,large}.example`) with your actual root substituted: the weekly line active, the trivial-only line commented out so you opt in deliberately. Stagger the start times so the two entries cannot collide on the same minute.

The two-tier model avoids drowning substantive agent logs in trivial chatter while keeping file-descriptor and inotify pressure bounded.

#### logs/debug/ is a write-only sink

**logs/debug/ is a write-only sink.** Agents write diagnostic output to `logs/debug/` but must never read from it. Reading from this directory is not part of any agent's procedure. If an agent finds itself reading `logs/debug/`, it is off-procedure and should stop, backtrack to the last known-good step, and resume from there.

Debug logs (under logs/debug/) are write-only sinks for human observability. No agent reads from logs/debug/ during normal operation. Agents must not include log content in their task analysis.

### Running the script

```bash
# Preview what would be cleaned up, for every registered project (no changes made)
scripts/cleanup/cleanup.sh --dry-run

# Run cleanup for real, for every registered project
scripts/cleanup/cleanup.sh

# One project only
scripts/cleanup/cleanup.sh --project pgai-agent-kanban
```

The script writes a summary log to `PGAI_LOGS_DIR/cleanup-YYYYMMDD-HHMMSS.log` on every per-project run, including dry runs. The summary's `task_folders_kept` line is Step 2's count.

### Configuration

The cleanup script reads three keys from the `[cleanup]` section of the `kanban.cfg` at the root it runs against, once per run, before the per-project passes start, so every pass uses the same values. Each resolves highest first: the environment variable, the `[cleanup]` key, the default. A blank value reads as absent.

| `[cleanup]` key | Environment variable | Default |
|---|---|---|
| `retention_days` | `PGAI_CLEANUP_RETENTION_DAYS` | `30` |
| `trivial_log_bytes` | `PGAI_CLEANUP_TRIVIAL_LOG_BYTES` | `1700` |
| `trivial_log_hours` | `PGAI_CLEANUP_TRIVIAL_LOG_HOURS` | `6` |

Log retention has one more tier, between the `[cleanup]` key and the default: the deprecated `[paths] cleanup_retention_days`, used only when `[cleanup] retention_days` is absent or blank. Whenever that line is present and the environment does not set the value, the run prints one warning naming it and the edit that retires it. `PGAI_CLEANUP_TASK_RETENTION_DAYS` has no `kanban.cfg` key; it is set in the environment or left at its default of `7`.

## Archive — finished work out of the live project

After each release ships, the kanban moves a project's finished items beyond a configured count out of `projects/<project>/` and into `archive/<project>/` at the kanban root. Live scans — the dashboard, discovery, PM — then walk only recent and unfinished work.

Three rules hold on every run:

- **Nothing unfinished moves.** An item whose status is unknown or unreadable stays live.
- **Nothing is deleted.** Every move is recorded in a ledger.
- **Every archived item can be restored.**

Archiving is quality class. It never blocks or delays a release; a run that fails or is skipped is logged, and the archive runs again after the next release.

Command examples in this section run from the kanban root (`cd "$PGAI_AGENT_KANBAN_ROOT_PATH"`). In a dev tree the script is `team/scripts/archive.sh`.

### Before the first release after an upgrade

The first archive run on an established project moves the whole finished backlog in one pass — on a long-running project, hundreds of bugs and task folders. Preview it before the release that triggers it:

```bash
scripts/archive.sh --project pgai-agent-kanban --dry-run
```

The dry run prints, per category, every item that would move and where it would go. It then lists the queue lines and training logs that would follow those items, every older item that stays live with the reason it stays, and the legacy requirements files that would be folded in. Nothing on disk changes. If the plan moves more than you want, raise `archive_count` (below) before the release ships.

### Configuration: `[archive] archive_count`

`archive_count` is how many of the newest items per category stay live. Bugs, priorities and requirements are counted once each. Tasks are counted per role: each role named at the head of a task ID (PM, CODER, WRITER, TESTER, CM, PO, OVERWATCH, and any other prefix) keeps its own newest `archive_count`, so the live tree holds up to that many tasks for every role.

Roles are counted apart because each numbers its tasks from its own daily counter. PM's tasks run `-001`, `-002`, … while the other roles share one counter that runs far higher, so a single cross-role order would treat PM's newest tasks as old and archive them first.

| Where | Effect |
|---|---|
| `projects/<project>/project.cfg` `[archive] archive_count` | This project's value. Checked first. |
| `kanban.cfg` `[archive] archive_count` | The kanban-wide default, used when the project sets nothing. |
| Built-in default | `55`, when neither file sets a value. |

- **`0` disables archiving** for that project, including the legacy requirements migration below. Set it in one `project.cfg` to keep that project fully live while the rest of the fleet archives.
- **The value must be a whole number** (decimal digits; leading zeros are read as a number). Anything else — a sign, a unit, a decimal point — is refused dual-channel (`archive.invalid_archive_count`), naming the file. The run then archives nothing for that project. No wake or release stops over it.
- **`--count N` overrides it for one run** of `archive.sh` or the REST route.

Both example files, `kanban.cfg_example` and `project.cfg_example`, carry the key with its full comment.

### What moves, and what stays

In each category, and for tasks in each role, the newest `archive_count` items always stay live, whatever their state. An older item moves only when it is finished:

| Category | Live path | Newest by | Finished when |
|---|---|---|---|
| bugs | `bugs/BUG-NNNN-*.md` | bug number | `## Status` is `done`, `wont-do`, `duplicate` or `rejected` |
| priority | `priority/PRIORITY-NNNN-*.md` | priority number | `## Status` is `done` or `wont-do` |
| requirements | `requirements/vX.Y.Z-*.md` | version in the file name | `## Status` is `done` or `shipped`, and `## Target Version` is at or below Last Released |
| tasks | `tasks/<TASK-ID>/` | per role: `## Release Version`, then task date and sequence | `## State` is `DONE` or `WONT-DO` |

Within a role, a task of a newer release is never archived before one of an older release. A task with no readable `## Release Version` takes the highest version among the tasks its role created before it, so it sits where it was created; with none before it, it is its role's oldest.

An older, finished item still stays live while live work needs it:

- a task named in the `## Prerequisites` of a live task that is not `DONE` or `WONT-DO`;
- a bug or priority listed in the `## Bundled Items` of a bundle that is not `done`, `shipped` or `wont-do`;
- a requirement whose `## Target Version` is the Active RC;
- a task whose `## Release Version` is the Active RC.

**Fail closed.** An item whose own status cannot be read — a missing or empty field, an unreadable file, a version that does not parse — never moves. When `release-state.md` fails validation, the Active RC is unknown, so no task and no requirement moves that run; bugs and priorities do not depend on it.

**Followers travel with their item.** Every line in `tasks/queues/*_backlog.md` naming a moved task, bug or priority is appended, marker unchanged, to the queue file of the same name under `archive/<project>/tasks/queues/`. Every `logs/training/<role>/*<TASK-ID>*.md` file of a moved task moves to the same path under the archive.

**Numbering is unaffected.** The bug and priority allocators read the live folder, and the highest-numbered item is always among the newest `archive_count` kept live, so the next number claimed is the same before and after a run.

**A run is idempotent.** A second run with nothing new finished moves nothing.

### Layout

The archive mirrors the live project under the same sub-directory names:

```text
$PGAI_AGENT_KANBAN_ROOT_PATH/archive/<project>/
  bugs/                    BUG-NNNN-*.md
  priority/                PRIORITY-NNNN-*.md
  requirements/            vX.Y.Z-*.md
  tasks/                   <TASK-ID>/
  tasks/queues/            <agent>_backlog.md   (archived queue lines, markers unchanged)
  logs/training/<role>/    *<TASK-ID>*.md
  ARCHIVE-LEDGER.tsv       one row per move or restore
```

An item at `projects/<project>/<path>` lands at `archive/<project>/<path>`. Each item is moved with a rename on the same filesystem (copy, fsync, then remove across filesystems), under the project's repo lock.

### The ledger: `ARCHIVE-LEDGER.tsv`

The ledger is append-only: one row per item, written right after the item moves, never rewritten. There is no header row. Each row has six tab-separated fields:

| Column | Holds |
|---|---|
| `utc` | When the move finished, `YYYY-MM-DDThh:mm:ss+00:00`. |
| `release` | The project's Last Released version when the move ran (`v0.0.0` before the first release). |
| `category` | `bugs`, `priority`, `requirements` or `tasks` for a move into the archive; `restore` for a move back. |
| `id` | `BUG-NNNN`, `PRIORITY-NNNN`, the requirements file name, or the task ID. |
| `from` | Where the item was, relative to the kanban root. |
| `to` | Where it went, relative to the kanban root. |

A restore row has `from` and `to` the other way round: the archive path first, the live path second. Its `id` says what kind of item came back. Queue lines and training logs get no row of their own; they sit beside their item's row in the archive.

Two example rows, an archive move and the restore of the same bug (placeholders stand for the real number, slug and version):

```text
2026-10-07T18:40:12+00:00	vX.Y.Z	bugs	BUG-NNNN	projects/pgai-agent-kanban/bugs/BUG-NNNN-slug.md	archive/pgai-agent-kanban/bugs/BUG-NNNN-slug.md
2026-10-08T09:02:55+00:00	vX.Y.Z	restore	BUG-NNNN	archive/pgai-agent-kanban/bugs/BUG-NNNN-slug.md	projects/pgai-agent-kanban/bugs/BUG-NNNN-slug.md
```

### When it runs

**After every successful release.** The last step of release finalize (Step 16c) archives the released project — that project only — once the tag is cut, the RC branch is gone, and `release-state.md` reads Active RC `none` with the new Last Released. If any of those does not hold, the step archives nothing and says so. See `docs/cm-release-reference.md`, "The post-release archive in full."

**Never on a cancelled RC.** `cm/cancel-rc.sh` and `unwind-rc.sh` do not run it.

**Runs under CM's own wake chain.** The archive works under the project's repo lock, the same lock every wake chain holds for its whole run on that project. CM ships from inside its own wake chain, so Step 16c finds the lock already held by that chain. It recognizes the hold as its own and archives under it, without taking or releasing the lock. A release run from a shell outside any wake finds the lock free, takes it, and archives. In a fleet where CM ships autonomously, the post-release step archives after every release.

**Skipped when another writer holds the repo lock.** The skip happens only when the lock is held by a process outside the release's own wake chain. In practice that means the release was run from a shell while a wake chain, an operator's `scripts/archive.sh` run, or a `POST /operations/archive` call held the lock. When it skips, nothing moves: the archive logs `archive.repo_lock_busy` naming the holder, Step 16c prints a WARNING, and the release continues and completes normally. The archive runs again after the next release, or on demand (next section).

On-demand runs never treat a wake chain's hold as their own. `scripts/archive.sh` and `POST /operations/archive` skip (exit 3, `archive.repo_lock_busy`) whenever any other process holds the lock, so run them when no wake chain is working the project.

Whatever the outcome — archived, nothing to move, skipped, or stopped at a failing item — the release finishes with the exit status it would have had without the step.

### Running it on demand

```bash
# Preview: what would move, and what stays live and why
scripts/archive.sh --project pgai-agent-kanban --dry-run

# Archive now, at the configured archive_count
scripts/archive.sh --project pgai-agent-kanban

# Archive now, keeping the newest 100 items per category live
scripts/archive.sh --project pgai-agent-kanban --count 100
```

`--project` is always required; `$PGAI_PROJECT_NAME` never stands in for it. A run prints one `archive: <category> <id>: <from> -> <to>` line per item moved.

| Exit | Meaning |
|---|---|
| `0` | Finished (items moved, or nothing to move), dry run printed, or item restored. |
| `1` | Refused before anything moved — error, message and remedy on stderr. |
| `2` | Stopped at a failing item (`archive.run_failed`). Items moved before it are in the ledger; the next run retries. |
| `3` | Skipped: another process holds the project's repo lock (`archive.repo_lock_busy`). Nothing moved; run it again later. |

The REST twin, `POST /operations/archive`, calls the same function, so its `dry_run` returns the same text in `stdout`:

```bash
curl -sS -X POST http://127.0.0.1:8300/operations/archive \
    -H 'Content-Type: application/json' \
    -d '{"project": "pgai-agent-kanban", "dry_run": true}'
```

Body fields are `project` (required), `dry_run`, `count` and `restore`, each mirroring the CLI flag. A body without `project` is refused `422` before anything runs. A refusal, a failed run, or a skipped run returns `500` with `error`, `message`, `remedy` and the CLI's `exit_code`. Every flag and every refusal: `docs/operator-commands.md` ("archive") and `docs/operator-api.md`.

### Restoring an item

```bash
# Find the exact ID: category and id of the most recent moves, newest last
cut -f3,4 archive/pgai-agent-kanban/ARCHIVE-LEDGER.tsv | tail -n 20

# Restore it (replace BUG-NNNN with that ID)
scripts/archive.sh --project pgai-agent-kanban --restore BUG-NNNN
```

The ID is a `BUG-NNNN`, a `PRIORITY-NNNN`, a requirements file name (`vX.Y.Z-slug.md`) or a task ID. The item moves back to its live path with its queue lines and, for a task, its training logs, and the ledger gains a `restore` row. `--restore` is not combined with `--dry-run` or `--count`.

A restore is refused, with nothing moved, when the archive does not hold that ID (`archive.restore_not_archived`) or when the live path is already taken (`archive.restore_live_exists`).

A restored item that is still finished and older than the newest `archive_count` is selected again by the next run. To keep it live, reopen it (change its status) or raise `archive_count`.

### What reads the archive

These readers look an item up in the archive when its ID is not live. A live item always wins.

| Reader | What it does with an archived item |
|---|---|
| Wake prerequisite gate | Treats an archived prerequisite task as `DONE` (only finished tasks are archived), so a waiting task is still promoted. A lookup fault leaves the prerequisite unsatisfied. |
| `verify-rc-state.sh` | Counts archived prerequisites as resolved, not as dangling references. |
| `show.sh`, `GET /show` | Emits the archived document unchanged; stderr carries `archived: <KEY> is not in the live project; read from the archive: <path>`. |
| `why.sh --details` | Shows a queue entry's archived state, marked `(archived)`, in the text, report and JSON forms. |
| `restamp-fixed-in.sh` | Stamps an archived bug in place in the archive; its output line ends in `(archived)`. |
| Release Fixed-In writeback | Stamps a bug the release names in place in the archive when it is no longer live. |
| Changelog writer | Loads archived bugs, so a regenerated `CHANGELOG.md` is byte-identical before and after archiving. |
| `cost-report.sh`, `metrics-report.sh` | Roll-ups rebuilt after a run include archived tasks, so totals are unchanged. |
| `export-project.sh`, `import-project.sh` | Carry `archive/<project>/` and its ledger inside the export and place it at `archive/<name>/` on import. |

**These stay live-only and never read the archive:** dashboard board columns, discovery, PM filtering, and the lane verdict's own task scan. An archived item never appears in a board column and never drives a verdict. Write commands — `close`, `wontdo`, `delete`, `reset` — also act on live items only. The dashboard's document drill pane reads live tasks only.

### The old in-project requirements archive

Older installs kept shipped requirements inside the live project, at `projects/<project>/tasks/archive/requirements/` (priority items in its `priority/` sub-directory). No reader that falls back to `archive/<project>/` could see them there.

Every archive run that finishes, with `archive_count` not `0`, empties that directory into the archive tree:

- a file named `PRIORITY-NNNN-*.md` moves to `archive/<project>/priority/`;
- every other file, at any depth, moves to `archive/<project>/requirements/`.

Each file gets one ledger row. A file never overwrites anything: when its name is already taken, it moves to the first free name of the form `<stem>.legacy-<n><suffix>`, so both copies are kept. The emptied directories are left in place. Once the directory is empty, later runs find nothing there and write no rows. The dry run lists these files after the plan.

## Briefs Directory Convention

Brief files for the PO agent should be placed in `$PGAI_AGENT_KANBAN_ROOT_PATH/briefs/`. This is a recommended convention, not a hard requirement.

### Why use it

Placing briefs in `$KANBAN_ROOT/briefs/` keeps them organized alongside the kanban tree. The cleanup script automatically archives briefs from this directory after the retention period expires, and the `PGAI_BRIEFS_DIR` config variable points here by default.

### Not enforced

The `po-agent.sh` script accepts any file path as its argument. You can pass a brief from any location on the filesystem:

```bash
# Convention — brief in $KANBAN_ROOT/briefs/
./scripts/po-agent.sh "$PGAI_AGENT_KANBAN_ROOT_PATH/briefs/my-feature-brief.md"

# Also valid — brief anywhere
./scripts/po-agent.sh ~/Desktop/my-feature-brief.md
```

Briefs placed outside `$KANBAN_ROOT/briefs/` will not be automatically archived by the cleanup script.

## Dashboard

The dashboard gives a live view of kanban system state: version, RC progress, HALT status, active task, queue depths, and blocked tasks. It is intended for human monitoring, not agent use.

### Launching the dashboard

```bash
# Standard launch — opens a tmux session named pgai-kanban-dashboard
# Minimum terminal size: 100 columns x 30 rows
$KANBAN_ROOT/scripts/dashboard/create.sh

# Override the kanban root
$KANBAN_ROOT/scripts/dashboard/create.sh --kanban-root /path/to/kanban

# Override the tmux session name
$KANBAN_ROOT/scripts/dashboard/create.sh --session my-session
```

If a session named `pgai-kanban-dashboard` already exists, the script attaches to it rather than creating a new one.

### Window layout

The board's shape is declared once, in `team/config/dashboard_windows.census` — the machine-readable single source of truth the session builder reads and the window documentation is generated from. This page deliberately keeps no second copy of that list. A hand-maintained window table drifts the first time a window is added or renumbered — which is exactly what happened to the four-window table that used to sit here.

The generated window tour, and a paragraph on what each window is for, are in [`dashboard.md`](dashboard.md). Per-pane detail — which script populates each region of each window, and the command that reproduces that region outside tmux — is in `DASHBOARD-PANES.md`, which ships in the repository checkout rather than into an installed root.

Three orientation facts are worth carrying here, because they are the ones an operator needs before reading anything else:

- **Window 0 (`main`) is where the session drops you.** Framework version, clock and HALT state across the top; per-project progress in the middle; a live tail of the agent logs below; a per-project queues column pinned to the right quarter. If you watch one window while a release runs, watch this one.
- **Window 3 (`attention`) answers "is anything waiting on me?"** A single pane listing tasks sitting in BLOCKED, each with the reason it recorded and its `## Next Recommended Step`. An empty pane is the healthy state.
- **Window 1 (`operator`) is the board plus somewhere to type.** Top to bottom: window 2's eight-column board at the same widths, then a full-width command-line shell with the kanban environment already sourced, then window 2's legend bar. Run `scripts/dashboard/suggested-commands.sh` from that shell for a reminder of the common commands. This is the window to live in when your only terminal is an attach — over ssh, or inside a container.

Navigate with `Ctrl-b` followed by the window number for windows 0 through 9, and `Ctrl-b '` then the number for windows 10 and above.

Every interval-based pane refreshes on a visibility-aware cadence rather than a flat interval: it renders at `PGAI_DASHBOARD_REFRESH_SECONDS` (default 5 seconds) while it is visible — its window is the active one and a client is attached — and renders nothing while hidden, which includes every pane of a detached session. It repaints immediately the moment its window becomes visible again. Log panes pause while hidden and catch up when shown; the status bar is the one surface that keeps refreshing whichever window you are on. See "Refresh cadence" in [`dashboard.md`](dashboard.md) for the full behavior and the named constants that govern it.

### Status bar

Every window carries the same two-line tmux status bar. The top line is tmux's own window list, with the active window marked. The bottom line is rendered by `team/scripts/dashboard/status-bottom.sh` and carries the install-wide posture, in a fixed segment order:

1. The installed framework version, read from `$KANBAN_ROOT/VERSION`.
2. On a per-project drill window only, that project's version and workflow type.
3. The PM mode indicator.
4. One segment per active halt scope — `HALT GLOBAL` for the kanban-root sentinel, or the project's own name for a per-project one, and the draining `HALT-AFTER` form when a soft halt is armed. Every active scope renders; none suppresses another.
5. `APPROVAL(n)` when human-approval gates are pending, then `BUGS DEFERRED n` when a low-severity bug deferral is in force. Both are omitted when the count is zero.
6. The date and time, always last.

Glyphs (`📝`, `🛑`, `⚠️`, `✋`, `📅`) are rendered only on a color-capable terminal; under `NO_COLOR` or `TERM=dumb` the same segments render as plain text.

### Operator workflow with the dashboard

**Starting a work session:**

1. Launch the dashboard: `$KANBAN_ROOT/scripts/dashboard/create.sh`
2. Window 0 (`main`) loads by default. Check the HALT indicator on the status bar, per-project progress, and the queues column.
3. Check window 3 (`attention`) for any BLOCKED tasks needing intervention.
4. Watch window 9 (`logs`) — or the log pane on window 0 — for agent activity in real time.
5. Use the shell on window 1 (`operator`) to inspect queue files, task folders, or run commands directly. Window 12 (`terminal`) pairs the same shell with a full-height log stream when you want to watch a command's effect land.

**Checking system health without tmux:**

Use `kanban-status.sh` for a one-shot status view from any terminal — SSH sessions, scripts, or environments without tmux:

```bash
# One-shot output (fits 80x24)
$KANBAN_ROOT/team/scripts/kanban-status.sh

# Continuously refreshing view in any terminal
watch -n 5 $KANBAN_ROOT/team/scripts/kanban-status.sh

# Disable color output
$KANBAN_ROOT/team/scripts/kanban-status.sh --no-color
```

Color output is automatically suppressed when `NO_COLOR` is set or `TERM=dumb`.

**Halting and resuming the whole kanban** (the operator's fleet-wide manual pause; to park one project instead, see "Per-Project HALT Workflow"):

```bash
# Pause every project (no new tasks will be pulled anywhere)
touch $PGAI_AGENT_KANBAN_ROOT_PATH/HALT

# Resume
rm $PGAI_AGENT_KANBAN_ROOT_PATH/HALT
```

The status bar on every window shows the current HALT state.

**Unblocking a task:**

1. Check window 3 (`attention`) for the blocked task ID and reason.
2. Use the shell on window 1 (`operator`) to investigate or apply the fix.
3. Set the task's state back to `BACKLOG` — either by hand or, preferably, with the status-writer tool (see "Writing task state" in `SOP-core.md` for the invocation).
4. The agent will pick it up on the next cron wake cycle.

### Visibility window: rows and sort order

The unified visibility window (a dedicated tmux window separate from the main status window) renders eight columns of work items: BUGS, PRIORITIES, REQUIREMENTS, PM, CODER, WRITER, TESTER, CM. Each column shows up to **13 rows** of entries. Columns with fewer than 13 items show only the items that exist — no padding to 13 with empty rows. Tmux pane heights are sized so all 13 rows fit without scrolling.

#### Sort order within a column

Within each column, entries sort **DESC by identifier** (newest / highest-numbered first). The sort key depends on the column:

| Column                                              | Sort key                |
|-----------------------------------------------------|-------------------------|
| BUGS, PRIORITIES, PM, CODER, WRITER, TESTER, CM     | numeric ID, DESC        |
| REQUIREMENTS                                        | semver, DESC            |

So BUG-0099 ranks above BUG-0001, and `v0.23.25` ranks above `v0.0.1`.

#### Multi-project minimum representation

When more than one project contributes items to the same column, a per-project minimum keeps a small project from being completely buried by a large one. The algorithm runs in three steps:

1. **Per-project minimum.** Every project with at least one item in the column is guaranteed `min(N_items, 3)` rows.
2. **Spare-row fill.** Any rows left over after minimums are filled from a **global DESC** merge of all items not already claimed by a minimum.
3. **Final ordering.** The combined set (minimums + spare fill) is sorted **globally DESC** by the column's sort key for display.

**Worked example.** Column BUGS, 13 rows, two projects: project A has 100 bugs (BUG-0001 to BUG-0099 plus one more), project B has 3 bugs (BUG-0001 to BUG-0003).

1. Minimums: A gets `min(100, 3) = 3` rows; B gets `min(3, 3) = 3` rows. Six guaranteed.
2. Spare rows: `13 − 6 = 7`. Global DESC of unclaimed items fills these with A's next 7 (BUG-0096 through BUG-0090).
3. Final globally-DESC display:

   ```
   ■A BUG-0099
   ■A BUG-0098
   ■A BUG-0097
   ■A BUG-0096
   ■A BUG-0095
   ■A BUG-0094
   ■A BUG-0093
   ■A BUG-0092
   ■A BUG-0091
   ■A BUG-0090
   ■B BUG-0003
   ■B BUG-0002
   ■B BUG-0001
   ```

The algorithm scales to three or more projects without change: every project with items still receives `min(N_items, 3)`; the remaining rows still come from a global DESC merge. If only one project has items, that project takes all 13 rows. If the total across all projects is fewer than 13, the column shows everything it has and stops there.

#### Project tag prefix

Every row is prefixed with a colored square (`■`) rendered in the project's `display_color` from `projects.cfg`. There is no project-name text on the row — the colored square is the only project-identifying signal. Operators distinguish projects by color alone, with the legend below the grid as the lookup table. See the **Dashboard Color Conventions** section below for the `display_color` field and the default palette.

The row-count limit and sort algorithm both live in `team/scripts/dashboard/column-render.sh`. The pane heights live in `team/scripts/dashboard/create.sh`.

### Dashboard Color Conventions

The unified 8-column visibility window encodes two pieces of information on every row using two independent color dimensions. Operators read the dashboard at a glance by treating these dimensions as a pair: "where" and "what state."

- **Project color (left tag)** — fixed per project, does not change as work progresses. Answers "where does this row belong?"
- **Status color (entry text)** — reflects lifecycle state, changes as work moves. Answers "what state is this row in?"

#### projects.cfg format

The source of truth for project colors is `projects.cfg` at the kanban root. The format is extended from `name:priority` to:

```
name:priority[:display_color]
```

`display_color` is an HTML-standard hex value like `#378ADD` (not `0xRRGGBB`). Direct copy-paste from any color picker is CSS- and SVG-compatible without translation. The field is optional: lines with only `name:priority` remain valid and the dashboard falls back to the next deterministic palette entry at read time, so installs predating the redesign keep working without manual edits.

Example:

```
pgai-agent-kanban:1:#378ADD
pgai-chomp-man:2:#1D9E75
```

#### Default palette

`create-project.sh` auto-assigns colors from a built-in palette of eight visually distinct entries, ordered for maximum legibility across mixed-project rows. Newly registered projects pick up the next unused palette index. If all eight slots are already in use, assignment wraps to index 0 and the operator should edit `projects.cfg` to disambiguate.

| Slot | Hex       | Color name (intent)           |
|------|-----------|-------------------------------|
| 0    | `#378ADD` | blue 400 — typically kanban-self |
| 1    | `#1D9E75` | teal 400                      |
| 2    | `#D85A30` | coral 400                     |
| 3    | `#BA7517` | amber 400                     |
| 4    | `#D4537E` | pink 400                      |
| 5    | `#7F77DD` | purple 400                    |
| 6    | `#639922` | green 400                     |
| 7    | `#888780` | gray 400 (last resort)        |

The palette is defined once in `team/scripts/lib/projects_cfg.sh` as the `PGAI_DEFAULT_PALETTE` array. Do not duplicate the hex values elsewhere; reference the array by name.

#### Project color: left tag

Every row in every column carries a small colored square at its left edge in the project's `display_color`. The square is the only project-identifying signal in the unified view, so mixing rows from several projects in a single column stays unambiguous. Project color is set in `projects.cfg` and is fixed for the life of the project unless an operator edits the file.

#### Status color: entry text

The text color of each entry reflects its current lifecycle status, read from each row's `## Status` field (for bugs and priorities) or `## State` field (for tasks) on every dashboard refresh.

| Status    | Text color                    | Hex       |
|-----------|-------------------------------|-----------|
| `open`    | default text (theme-driven)   | `var(--color-text-primary)` |
| `running` | amber                         | `#BA7517` |
| `done`    | green                         | `#639922` |
| `blocked` | red                           | `#E24B4A` |

The mapping is implemented once in `team/pgai_agent_kanban/dashboard/column_render/cells.py` (`status_color()`), and the legend maps the status keywords through the same arms (running; done and wont-do; blocked; open) so legend and rows never disagree.

#### Legend

A persistent legend below the visibility grid lists each active project with its color tag and renders the four status keywords in their mapped colors, so the convention can be relearned without leaving the dashboard:

```
PROJECT: ■ pgai-agent-kanban  ■ pgai-chomp-man   |   STATUS: open  running  done  blocked
```

The legend template lives in `team/scripts/lib/dashboard_legend.sh` as the `DASHBOARD_LEGEND_TEMPLATE` constant; the renderer substitutes the per-project block and the colored status block into the template on every refresh.

#### Operator override

To change a project's color or priority, edit its line in `projects.cfg`. The dashboard re-reads `projects.cfg` only on startup, so a running session continues with the cached values until it is restarted.

```bash
# Edit projects.cfg, then restart the dashboard to pick up the change
$EDITOR $KANBAN_ROOT/projects.cfg
$KANBAN_ROOT/scripts/dashboard/kill.sh
$KANBAN_ROOT/scripts/dashboard/create.sh
```

Manual edits are only needed when the operator wants a non-default color, when two projects collide on the same palette index, or when all eight palette slots are already in use.

#### Migration behavior

Installs whose `projects.cfg` predates the redesign — entries with only `name:priority`, no color field — get a one-time top-up the next time `install.sh` or `upgrade.sh` runs: each color-less line is rewritten to include the next available palette entry. The migration is idempotent (running twice does not double-add a color) and logged (operators see exactly which lines were touched). Two-field lines remain a valid format after migration, so manual rollback is always available.

### Refresh loops: `cadence-loop.sh`, not `watch`

Every interval-based pane in `team/scripts/dashboard/create.sh` — including the visibility-pane loops that used to be hand-rolled `bash -c 'while true; do clear; ...; sleep N; done'` — is dispatched through `team/scripts/dashboard/cadence-loop.sh`, not `watch -c -n N -- ...`. Maintainers editing dashboard scripts must preserve this pattern. Do not "simplify" a pane command back to a bare `watch -c` invocation.

The reason is twofold:

1. **Truecolor.** procps-ng `watch(1)`, even with `-c` / `--color`, only preserves basic 16-color ANSI sequences (`\033[3Xm` and `\033[9Xm`). It silently strips 24-bit truecolor sequences of the form `\033[38;2;R;G;Bm`. The dashboard's per-project tag glyph (`■`) is rendered with truecolor so the operator can configure any hex value in `projects.cfg`'s `display_color` field, including arbitrary brand colors that do not map cleanly to a 16-color palette. Under `watch -c`, those truecolor escapes never reach tmux and every project tag glyph collapses to the default terminal color (white), defeating the visual purpose of the unified visibility window. The basic 8-color status indicators (yellow `running`, green `done`, red `blocked`) keep working under `watch -c`, which is exactly what made the regression hard to spot — status colors render, project colors do not.
2. **Visibility-aware cadence.** `watch` ticks at one flat interval regardless of whether its pane's window is the one the operator is looking at. `cadence-loop.sh` renders at the configured interval only while its pane is visible — its window is the session's active window and at least one client is attached. A hidden pane, including every pane of a detached session, renders once on arrival and nothing after that: the loop only re-checks visibility (one tmux query, no render) every `PGAI_DASHBOARD_HIDDEN_MULTIPLIER` times its interval, and the activation hook wakes it to render at once when its window becomes visible. Under `--stream`, the log panes' never-returning renderer is paused while hidden and resumed when visible — see "Refresh cadence" in `docs/dashboard.md` for the full behavior. `watch` cannot express any of this.

`cadence-loop.sh`'s render step (`team/scripts/dashboard/lib/cadence.sh` `dashboard_cadence_render_once`) is the direct descendant of the old hand-rolled visibility-pane loop body: cursor-home, per-line clear-to-end-of-line, clear-below — the same ANSI-preserving flicker-free redraw, now shared by every pane instead of duplicated per visibility column. Pane exit kills the shell running the loop, so there are no orphaned processes. The active-window interval (driven by `PGAI_DASHBOARD_REFRESH_SECONDS`, or a pane-specific fixed value such as the git/metadata windows' 10s) is passed to `cadence-loop.sh --interval`; the hidden re-check multiplier and the stagger-jitter bound are named constants in `team/scripts/lib/dashboard_constants.sh` (`PGAI_DASHBOARD_HIDDEN_MULTIPLIER`, `PGAI_DASHBOARD_JITTER_MAX_SECONDS`), never per-pane magic numbers. This pattern must be used for every interval-based pane, project-colored or not — an in-script `IMPORTANT — why cadence-loop.sh, not watch` comment block at the top of the visibility-pane command definitions calls this out at the point of edit.

### Metadata window

The metadata window (rendered by `team/scripts/dashboard/metadata.sh`) shows kanban-wide state at the top and one block per registered project below it. The kanban-wide section reports the installed version, PM mode, and HALT state. Each per-project block reports:

- `workflow:` — the project's `workflow_type` from `project.cfg [project]`
- `active RC:` — the current release-candidate version from `release-state.md`, or `none`
- `last released:` — the newest released tag for the project
- `max minor:` — the `max_minor` ceiling from `project.cfg [versioning]`
- `max major:` — the `max_major` ceiling from `project.cfg [versioning]`
- `max patch:` — the `max_patch` ceiling from `project.cfg [versioning]`

Each `max ...` line shows the ceiling in effect: the configured number, the default (`21` patch, `13` minor, `0` major) when the key is absent, `none` when the cap is lifted, or `invalid` for a malformed value.

The three `max ...` lines mirror the discovery-time version ceilings documented under [Version Ceilings](#version-ceilings); the metadata window is the at-a-glance view of which ceilings each project currently has. Layout and spacing match across the three lines so a value change is immediately visible.

### Dashboard configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `PGAI_DASHBOARD_REFRESH_SECONDS` | 5 | Active-window auto-refresh interval for every interval-based pane; a hidden pane (another window active, or no client attached) renders nothing and only re-checks visibility every `PGAI_DASHBOARD_HIDDEN_MULTIPLIER` times this interval (see `team/scripts/lib/dashboard_constants.sh`) |
| `PGAI_DASHBOARD_SESSION_NAME` | `pgai-kanban-dashboard` | Tmux session name |

Set these in your `env` file or export them before launching the dashboard.

## Metrics

The kanban writes a structured metrics surface on every RC close. The surface is the primary operator and downstream-agent contract for "what happened on this RC, this day, and across the project's history." Cost is a derived view layered on top (see `Tracking Token Costs` below); the metrics surface itself is the canonical data.

The principle is data first. The schemas below are designed so that any future visualization — Grafana, spreadsheets, notebooks, alerting, anomaly detection — can be built without changing the source files. The framework only commits to publishing the data and a small CLI for extracting it. It does not commit to projections, comparisons, or charts; those are out of scope and intentionally not built.

### File layout

All metrics files live under one tree per project:

```
projects/<name>/metrics/
  rc/<version>.json        # per-RC rollup, one file per closed RC
  day/<YYYY-MM-DD>.json    # per-day rollup, one file per day with activity
  history.csv              # cumulative append-only history, one row per RC
```

The per-task `tokens.json` files under `projects/<name>/tasks/<task-id>/artifacts/tokens.json` are the source data every rollup is computed from. The metrics surface is strictly derived: deleting any rollup file and re-running the aggregator must reproduce the same content from the per-task data. Operators may safely delete and regenerate any file in `metrics/rc/` or `metrics/day/`. `history.csv` is append-only — see "Cumulative history.csv" below.

### Per-task `tokens.json` schema (the source of truth)

Each agent invocation writes one `tokens.json` to its task's `artifacts/` directory. The schema is the input the metrics aggregator reads; getting it right is what unblocks every rollup downstream.

```json
{
  "model": "claude-opus-4-7",
  "provider": "claude",
  "agent": "coder",
  "rc_version": "v0.24.12",
  "input_tokens": 234,
  "output_tokens": 1856,
  "cache_creation_input_tokens": 12567,
  "cache_read_input_tokens": 87654,
  "invocations": 1,
  "elapsed_seconds": 245,
  "timestamp": "2026-05-17T21:08:12Z"
}
```

Two fields carry the record's contract:

**`model` must be the canonical model ID** — `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`, and so on. A shortname alias (`opus`, `sonnet`, `haiku`) in this field makes every downstream tool keyed by canonical ID silently drop the record. The token-capture helper canonicalizes before write and rejects unmapped shortnames with a loud warning. Files that already carry a shortname are tolerated at read time (see "Read-time canonicalization" below), but every new write is expected to be canonical.

**`rc_version` must be the RC the task belongs to** — `v0.24.12` for any task that ran under the rc/v0.24.12 branch. The aggregator uses this field to attribute tokens to RCs without walking task READMEs. Tasks that have no RC association (rare; usually utility scripts) may omit the field, and the aggregator attributes them to no RC.

The remaining fields (`provider`, `agent`, the four token counts, `invocations`, `elapsed_seconds`, `timestamp`) are recorded as shown in the example above.

### Per-RC JSON rollup

Written by `team/scripts/lib/metrics_aggregator.py` on RC close (invoked from `team/scripts/cm/release.sh` Step 19a) and on demand by `metrics-report.sh` when a requested file is missing. Path: `projects/<name>/metrics/rc/<version>.json`. The wake driver's finalize stage recomputes the rollup once more (`team/scripts/lib/metrics_rollup_refresh.sh`) whenever a task attributed to that RC writes its `tokens.json` after the rollup already exists — the CM release task that shipped the RC is the standing case, since its own usage is captured only after its session ends — so a shipped RC's rollup counts every task that worked it, that task's tokens and wall time included. The refresh is the same deterministic recompute as the original write; it never rewrites `history.csv`, whose row keeps the figures from RC close, and it never revisits rollups written before a task's `tokens.json` landed under the pre-refresh wake driver.

The rollup carries identity (`rc`, `project`, `workflow_type`), lifecycle (`opened_at`, `closed_at`, `wall_time_minutes`, `outcome`), task counts (`tasks.total`, `tasks.by_agent`), and the full token breakdown:

```json
{
  "rc": "v0.24.12",
  "project": "pgai-agent-kanban",
  "workflow_type": "release",
  "opened_at": "2026-05-18T01:04:37Z",
  "closed_at": "2026-05-18T02:30:15Z",
  "wall_time_minutes": 86,
  "outcome": "SHIPPED",
  "tasks": {
    "total": 8,
    "by_agent": { "pm": 1, "coder": 3, "writer": 1, "tester": 1, "cm": 2 }
  },
  "tokens": {
    "total":    { "input": ..., "output": ..., "cache_read": ..., "cache_write": ..., "invocations": ... },
    "by_model": { "claude-opus-4-7": { ... }, "claude-haiku-4-5-20251001": { ... } },
    "by_agent": { "coder": { ... }, "tester": { ... }, ... }
  },
  "wall": {
    "by_agent": { "coder": { "seconds": 620, "task_count": 3 }, "tester": { "seconds": 90, "task_count": 1 }, ... }
  },
  "bugs_filed_during_verification": [],
  "operator_interventions": [],
  "input_files": {}
}
```

`wall.by_agent` carries each agent's own wall-clock time, summed from that agent's tasks' `elapsed_seconds` (in the per-task `tokens.json` schema above), alongside a `task_count` of how many of that agent's tasks in this RC actually carried a usable value. It exists because `wall_time_minutes` above is a single RC-level figure every agent shares, which cannot answer "how long does coder's own work typically take" per agent. A task whose `tokens.json` omits `elapsed_seconds`, or carries a non-numeric one, is left out of both the sum and the count rather than folded in as a zero. Downstream averaging divides `seconds` by `task_count` itself; the rollup only carries the raw per-agent sums. Absent from a rollup written before this field existed, `wall.by_agent` reads as a missing key to older readers, which they must treat as "no per-agent wall data available," exactly like any other rollup lacking the field.

Aggregation is fully deterministic and idempotent. Running `aggregate_rc()` twice on the same RC produces the same bytes (sorted keys, fixed indent). Deleting a rollup and regenerating it is the supported recovery path for any rollup that drifted, was hand-edited, or pre-dates a schema fix.

### Per-day JSON rollup

Written lazily on demand: `metrics-report.sh --day 2026-05-18` invokes `aggregate_day()` if the file is missing. Path: `projects/<name>/metrics/day/<YYYY-MM-DD>.json`.

A per-day rollup sums every per-task `tokens.json` whose timestamp falls within that UTC calendar day, regardless of which RC each task belongs to. It also records which RCs contributed (`rcs_included`):

```json
{
  "date": "2026-05-18",
  "project": "pgai-agent-kanban",
  "rcs_included": ["v0.24.11", "v0.24.12"],
  "tokens": {
    "total":    { "input": ..., "output": ..., "cache_read": ..., "cache_write": ..., "invocations": ... },
    "by_model": { ... },
    "by_agent": { ... }
  }
}
```

Day rollups are derived from the same per-task data as RC rollups; they are not derived from RC rollups. This matters when a single RC straddles a UTC day boundary: the day file counts only the portion that timestamps into that day, while each RC file counts the whole RC.

### Cumulative `history.csv`

Written by `team/scripts/lib/metrics_csv_writer.py` on RC close (invoked from `team/scripts/cm/release.sh` Step 19b). Path: `projects/<name>/metrics/history.csv`.

One row per RC, appended on close, never modified. The header is written on first call and never re-emitted. Columns, in order:

```
rc, project, workflow_type, opened_at, closed_at, wall_time_minutes,
outcome, tasks_total, tasks_pm, tasks_coder, tasks_writer, tasks_tester,
tasks_cm, input_tokens, output_tokens, cache_read_tokens,
cache_write_tokens, cache_hit_rate_pct, bugs_filed_during, operator_waivers,
blocked_minutes
```

`cache_hit_rate_pct` is `cache_read_tokens / (input_tokens + cache_read_tokens) * 100`, rounded to one decimal. The remaining fields map directly to the per-RC rollup.

`blocked_minutes` sits last, after the columns that were already a published contract, so every positional consumer keeps reading the same fields at the same offsets. It carries the RC's merged blocked-interval total in minutes — `0` for an RC that was never blocked, and an empty cell when the rollup has no usable value, which the dashboard renders as a dash. See "Blocked" in [dashboard.md](dashboard.md) for what the intervals are and how they are merged.

Concurrent RC closes are serialized with an exclusive `fcntl` advisory lock on `history.csv` itself; the writer also performs a TOCTOU-safe duplicate check inside the lock, so re-running a release that already wrote a row is a no-op rather than a duplicate. Append-only is the contract — the file is meant to be tailed, piped into spreadsheets, or shipped to Grafana with no further transformation. Do not edit existing rows. If a row is wrong, fix the upstream rollup and accept that the historical CSV row stays as written; the kanban's append-only audit is more valuable than retrospective edits.

### Read-time canonicalization (backfill)

Legacy `tokens.json` files may carry the shortname `model` field (`opus`, `sonnet`, `haiku`) and have no `rc_version`. The aggregator handles both gracefully without rewriting any source file:

- Shortnames are mapped at read time: `opus` -> `claude-opus-4-7`, `sonnet` -> `claude-sonnet-4-6`, `haiku` -> `claude-haiku-4-5-20251001`. Any other unrecognized model string is left as-is with a single stderr warning.
- Missing `rc_version` is resolved by reading `## Release Version` from the task's `README.md`; tasks without that field are excluded from per-RC rollups but still contribute to per-day rollups.

The source files are not modified by the aggregator. Historical fidelity is preserved; canonicalization happens in memory at aggregation time. Current writes are expected to be canonical at the source, so the read-time map is a compatibility shim.

### `metrics-report.sh` CLI

`team/scripts/metrics-report.sh` is the operator-facing entry point for everything under `metrics/`. The scope flags select what to emit; output goes to stdout in the requested format.

| Flag | Argument | Purpose |
|---|---|---|
| `--rc` | `vX.Y.Z` | Emit the per-RC rollup JSON. Triggers on-demand aggregation if the file is missing. |
| `--day` | `YYYY-MM-DD` | Emit the per-day rollup JSON. Triggers on-demand aggregation if the file is missing. |
| `--csv` | (none) | Emit the full contents of `history.csv`. Combine with `--project` to filter. |
| `--format` | `jsonl` | Emit one JSON object per line (JSON Lines). Without `--rc` or `--day`, streams every per-RC rollup found, sorted by version. |
| `--tail` | (none) | Live-tail `history.csv` via `tail -f`. Streams each new row as `team/scripts/cm/release.sh` appends it. Stop with Ctrl-C. |
| `--project` | `<name>` | Override `PGAI_PROJECT_NAME` (default: `pgai-agent-kanban`). |
| `--kanban-root` | `<path>` | Override `PGAI_AGENT_KANBAN_ROOT_PATH`. |

Example invocations:

```bash
# Per-RC rollup for v0.24.12
team/scripts/metrics-report.sh --rc v0.24.12

# Per-day rollup for today (UTC)
team/scripts/metrics-report.sh --day 2026-05-18

# Full history CSV
team/scripts/metrics-report.sh --csv

# Filtered CSV for a different project
team/scripts/metrics-report.sh --csv --project pgai-chomp-man

# Streaming JSON Lines for every RC ever closed
team/scripts/metrics-report.sh --format jsonl

# Live tail of new RC closes
team/scripts/metrics-report.sh --tail
```

Exit codes: 0 on success (warnings on stderr are fine), 1 on usage or configuration errors, 2 when the requested data does not exist (no rollup file, empty CSV, etc.). Downstream consumers should treat exit 2 as "nothing to report for this scope" rather than a hard failure.

### The `rc-metrics` dashboard window

The left pane of the kanban dashboard's window 7 (`rc-metrics`) renders a live operator view of the same data, refreshed on the standard dashboard interval while its window is active (visibility-aware — see "Refresh cadence" in `docs/dashboard.md`). It runs `team/scripts/dashboard/metrics.sh` under `cadence-loop.sh` so truecolor is preserved.

The pane shows two stacked blocks:

- **Today** — per-project summary for the current UTC day: RCs shipped, wall time, total tokens with cache-hit percentage, task count. Sourced from `metrics/day/<today>.json` per project.
- **Current RC** — per-project open-RC progress: tasks done / total, elapsed wall time, tokens so far. Sourced from `metrics/rc/<active>.json` per project plus the project's `release-state.md` for active-RC discovery.

The pane is intentionally facts-only: no pace projections, no comparison to historical averages, no charts. Operators who want richer views should consume `history.csv` or the per-RC JSON files directly through their tool of choice.

### `team/scripts/cm/release.sh` integration

`team/scripts/cm/release.sh` Step 19 invokes both writers as a non-blocking pair. Step 19a runs `metrics_aggregator.py` to write the per-RC JSON; Step 19b runs `metrics_csv_writer.py` to append the history.csv row from that JSON. Both steps are wrapped in non-blocking error handling: if either fails, the release still ships and a `[metrics]` WARNING is printed to stderr. The release contract (branch merged, tag pushed, state file updated) does not depend on metrics succeeding. Operators who notice missing rollups can rebuild them after the fact by deleting the file and re-running `metrics-report.sh --rc <version>`.

### `cost-report.sh` (backward-compatibility wrapper)

`team/scripts/cost-report.sh` is preserved as the cost-specific lens on the same underlying data. Existing cron jobs, alert scripts, and operator muscle memory continue to work; the script now prints a footer pointing at `metrics-report.sh` for the richer surface. New code and new tooling should target `metrics-report.sh` directly. See "Tracking Token Costs" below for the cost-report flag set, the cache-aware cost decomposition, and how the cost numbers feed provider decisions.

### What is explicitly out of scope

The metrics surface ships data, not interpretation. The following are deliberately not built and should not be added without a fresh priority brief:

- Pace projections ("RC will complete in ~30 min")
- Comparison to historical averages
- Multi-day or multi-RC trend charts
- Per-provider arbitrage recommendations
- Anomaly detection ("this RC used 3x more tokens than typical")
- Any visualization beyond the `rc-metrics` window's facts-only pane

These are all reasonable future work, and the schemas above are designed to support them as derived consumers. None of them belong in the framework's metrics layer itself. Build them externally — Grafana, notebooks, spreadsheets, whatever fits — against `history.csv` and the per-RC JSON files. The contract the framework commits to is the data, not the chart.

## Tracking Token Costs

The kanban captures token usage on every `claude -p` invocation, rolls those counts up per RC and per day, and translates them into dollar estimates with `cost-report.sh`. The output is the operator's source of truth for what the kanban actually costs to run and which provider arrangement makes sense going forward.

### What is captured

Three layers of data accumulate as the kanban runs:

**Per-task — `projects/<name>/tasks/<task-id>/artifacts/tokens.json`.** Written after each agent invocation. One file per task; multiple `claude -p` calls inside the same task are summed into the single file. Fields include `model`, `provider`, `agent`, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `invocations`, `elapsed_seconds`, and `timestamp`. This is the raw datum every roll-up derives from.

**Per-RC — `projects/<name>/usage/rc/<version>-tokens.json`.** Written by the aggregator (invoked from `team/scripts/cm/release.sh` at ship time, or on demand). Contains a `version`, a `shipped_at` timestamp, a `tasks` array (one entry per task that contributed to the RC, with agent, token counts, and `cost_usd`), and a `totals` block aggregating the whole RC.

**Per-day — `projects/<name>/usage/daily/YYYY-MM-DD.json`.** Written end-of-day or on demand. Contains `date`, `project`, an `rcs_shipped` list, a `totals` block, and a `by_agent` breakdown.

If a roll-up file is missing when a report is requested, `cost-report.sh` invokes `team/pm-agent/aggregate_tokens.py` to build it from the per-task files, then proceeds with the report.

### Cache token economics

Anthropic bills four distinct token categories on every Claude API call. Cost data in the kanban only makes sense once you understand what each category is and why one of them dominates this framework's spend.

**The four billing categories.** Anthropic charges separately for:

| Category | tokens.json field | Rate relative to input | Why it exists |
|---|---|---|---|
| Input (new) | `input_tokens` | 1.00x (base rate) | Fresh prompt content the model has not seen this session. |
| Cache writes | `cache_creation_input_tokens` | 1.25x | Tokens added to the prompt cache this turn, so future turns can read them cheaply. |
| Cache reads | `cache_read_input_tokens` | 0.10x | Tokens served from a previously written cache entry. |
| Output | `output_tokens` | 5.00x (varies by model) | Tokens the model generated. |

The exact dollar amounts live in `team/scripts/lib/token_pricing.json` under `providers.<provider>.models.<model>.{input,output,cache_creation,cache_read}_per_1m`. Do not memorize numbers from this document — they will drift. The ratios above are stable (they are how Anthropic structures the pricing) but the absolute rates are not.

**Why cache reads dominate this framework's input volume.** The kanban runs each agent task as a fresh `claude -p` invocation. There is no persistent agent process holding context between tasks; every task pays to load the governance stack (DIRECTIVES, OVERVIEW, SOP, role file, project README, task README, task status) from scratch.

That sounds expensive. It is not, because Anthropic's prompt cache absorbs almost all of it. The governance stack is large and stable; once cached, it returns as `cache_read_input_tokens` at 10% of the normal input rate on subsequent invocations within the cache window. Only the small per-task delta — the actual user prompt, the changed task state — shows up as `input_tokens` at the full rate.

The net effect is that `cache_read_input_tokens` typically accounts for the large majority of input *volume* across a busy day, but represents only a small fraction of input *cost* because of the 0.10x multiplier. A report that summed only `input_tokens` would under-report real input volume by one to two orders of magnitude. The corrected report shows all four categories with their token counts and per-category dollar costs, so the cache economics are visible rather than buried.

**Worked example — a single CODER invocation.** A representative `tokens.json` record from a CODER task:

```json
{
  "model": "opus",
  "provider": "claude",
  "agent": "coder",
  "input_tokens": 7,
  "output_tokens": 1200,
  "cache_creation_input_tokens": 11603,
  "cache_read_input_tokens": 44108,
  "invocations": 1,
  "elapsed_seconds": 151,
  "timestamp": "2026-05-16T13:14:32Z"
}
```

Total input *volume* is `7 + 11,603 + 44,108 = 55,718` tokens. Only 7 of those (0.01%) are paid at the full input rate. Cache reads alone are 79% of input volume. This is the typical shape of a CODER invocation in this framework: a small delta against a large cached prefix.

The cost decomposition (using the current `token_pricing.json` rates for `claude-opus-4-7` at the time of writing — verify against the live file before quoting numbers in any decision document) shows the trade-off clearly:

- `7 input × input_per_1m / 1M` — negligible
- `11,603 cache writes × cache_creation_per_1m / 1M` — the largest single line
- `44,108 cache reads × cache_read_per_1m / 1M` — material but small
- `1,200 output × output_per_1m / 1M` — the second-largest line

Across this one invocation, cache *creation* costs more than cache reads — the 1.25x multiplier on the writes outweighs the volume of reads at the 0.10x rate. The arithmetic flips across many invocations that reuse the same cached prefix: write once at 1.25x, read many times at 0.10x, and the read side wins the running total.

That asymmetry is the whole point of the prompt cache. The kanban's task-per-invocation model takes the cache write hit on the first task that loads a given context, then amortizes it across every subsequent task that reads the same prefix back. Day-scope and month-scope cost reports show the amortized picture; single-task or single-invocation analysis shows the per-call picture.

Compare against what this would cost without caching: `55,718 × input_per_1m / 1M`. Real-world numbers will vary with the live rate sheet, but the savings is typically in the 60-70% range for a single invocation and grows from there as the same cached prefix is read by additional tasks in the same cache window.

### Running cost-report.sh

`cost-report.sh` lives at `team/scripts/cost-report.sh`. With no flags it produces a month-to-date report for the current project.

The scope flags are mutually exclusive — pick at most one. Other flags compose freely with the scope.

| Flag | Argument | Purpose |
|---|---|---|
| `--month` | `YYYY-MM` | Report on a specific calendar month. |
| `--day` | `YYYY-MM-DD` | Report on a single day. |
| `--rc` | `vX.Y.Z` | Report on a single RC. |
| `--project` | `<name>` | Override `PGAI_PROJECT_NAME` (default: `pgai-agent-kanban`). |
| `--csv` | (none) | Emit CSV instead of the human-readable block. |

Example invocations, one per flag:

```bash
# Specific month
team/scripts/cost-report.sh --month 2026-05

# Single day
team/scripts/cost-report.sh --day 2026-05-16

# Single RC
team/scripts/cost-report.sh --rc v0.23.22

# Override project
team/scripts/cost-report.sh --project pgai-chomp-man

# CSV output for spreadsheet analysis
team/scripts/cost-report.sh --month 2026-05 --csv
```

The script exits 0 on success (warnings on stderr are fine) and 1 on usage or configuration errors. If there is no data for the requested scope it prints `no data for <scope>` and exits 0.

### How to read the report

The human-readable report is organized as a header, then a sequence of blocks. The blocks are stable; absent data hides a block rather than re-shuffling layout. Read it from top to bottom: each block narrows from raw activity to per-dimension breakdowns to the decision data.

A canonical month-scope report looks like this:

```
=== Token Usage: May 2026 — pgai-agent-kanban ===
Days with activity: 12 / 16
RCs shipped:        7
Total invocations:  342

Token totals:
  Input (new):              4,128 tokens    $0.06
  Cache writes:         1,256,400 tokens    $23.56
  Cache reads:          9,884,200 tokens    $14.83
  Output:                  487,910 tokens   $36.59

By agent (cost share):
  CODER   $42.18 (56%)  — 14 invocations/day avg
  TESTER  $14.07 (19%)  — 4 invocations/day avg
  ...

By model:
  claude-opus-4-7              $75.04 (100%)

Total cost:         $75.04
Daily average:      $6.25
Projected monthly:  $187.62 (extrapolated from observed days)

Subscription comparison:
  Anthropic Pro programmatic credit ($20.00):   $168 short
  Anthropic Max programmatic credit ($200.00):  $12 surplus
  ...
```

Read it in this order:

**Header line.** `=== Token Usage: <scope> — <project> ===`. The scope label is whatever you asked for: `May 2026` for `--month`, `Day 2026-05-16` for `--day`, `RC v0.23.22` for `--rc`. The project name comes from `--project` or the default.

**Activity context.** Three lines orient you to how much work this scope represents:

- `Days with activity: N / M` — number of distinct days that produced any token usage, against the days elapsed in the scope. A 12-of-16 ratio means four days were quiet (no agent runs). Only shown for month scope.
- `RCs shipped: N` — distinct RC versions that closed during the scope. For day scope this becomes `RCs on this day: N`. For RC scope it becomes `RC: vX.Y.Z`.
- `Total invocations: N` — sum of `invocations` across every `tokens.json` in scope. One invocation is one `claude -p` call. A single task may produce multiple invocations if the agent restarted.

**Token totals (the four-category block).** This is the cache-aware breakdown described in the previous subsection. Each line shows a category, its token count, and its dollar cost:

- `Input (new):` — fresh `input_tokens`. Usually tiny in this framework. Multiplied by `input_per_1m`.
- `Cache writes:` — `cache_creation_input_tokens`. Multiplied by `cache_creation_per_1m` (1.25x input).
- `Cache reads:` — `cache_read_input_tokens`. Multiplied by `cache_read_per_1m` (0.10x input).
- `Output:` — `output_tokens`. Multiplied by `output_per_1m`.

The four dollar amounts sum to `Total cost` further down. If you see token counts but zero dollar amounts, either `token_pricing.json` is missing entries for the recorded models, or the roll-up file pre-dates the per-category cost fields (the report falls back to total-only display in that case — re-run the aggregator to refresh).

For legacy roll-up files written before the cache-aware aggregator landed, the block degrades gracefully: token counts still appear but dollar columns are omitted, and `Total cost` reflects the aggregator's stored total (which may itself be wrong if the legacy aggregator under-counted cache categories). Re-aggregate any RC or day you care about by deleting its roll-up file and re-running `cost-report.sh`; the missing roll-up triggers a fresh build from the per-task `tokens.json` files.

**By agent (cost share).** Per-agent dollar amount, percentage of total cost, and average invocations per day. Sorted by cost descending. This answers "which agent is the expensive one this scope." CODER usually dominates because it does the most work per task; significant share from any other agent (especially TESTER or PM running gap-analysis loops) is worth a look. The percentage uses the same `Total cost` figure shown below, so the numbers add up.

**By model.** Cost share per model name. Models appear here under their canonical IDs (`claude-opus-4-7`, `claude-haiku-4-5`, etc.) as captured from the provider's JSON response — not as the short aliases (`opus`, `sonnet`) used in the wake script. A model with `cost_usd=0` and a stderr warning means `token_pricing.json` is missing an entry for it; add the entry, re-run, the number appears. This is also where mixed-model strategies become visible: if some agents run Opus and others run Haiku, both lines show up here with their independent cost shares.

**Total cost.** The actual dollar figure for the scope. Always present. Equal to the sum of the four token-total category costs.

**Daily average / Projected monthly.** Only on month-scope reports. `Daily average` is `Total cost / days_with_activity`. `Projected monthly` is `daily_average * 30`. The projection is a linear extrapolation that assumes today's burn rate continues unchanged for the rest of the month — a quiet weekend or a heavy release week skews it. Day-scope and RC-scope reports show actual cost only and skip extrapolation.

**Subscription comparison.** For each subscription option in `token_pricing.json` under the `subscriptions` key, the report shows the dollar surplus or shortfall against projected (month) or actual (day, RC) cost. A "shortfall" means the credit would not cover the projected spend; you would pay the gap as API overage. A "surplus" means the credit covers the projected spend with margin. The `Cheapest if mixed` line at the end models a subscription plus API overflow strategy. Read this block as decision input, not a recommendation — the numbers are arithmetic; the choice is yours.

### Cross-checking the math by hand

When you need to verify the report is computing costs correctly — for example, after editing `token_pricing.json`, after a fix to the aggregator, or as a spot-check before quoting numbers in a provider decision — the math is straightforward enough to redo by hand from a single `tokens.json` record. The recipe is the same one the aggregator uses.

**Step 1 — pick a tokens.json record.** Any `projects/<name>/tasks/<task-id>/artifacts/tokens.json` will do. The example below uses a representative record:

```json
{
  "model": "opus",
  "provider": "claude",
  "agent": "coder",
  "input_tokens": 7,
  "output_tokens": 1200,
  "cache_creation_input_tokens": 11603,
  "cache_read_input_tokens": 44108,
  "invocations": 1,
  "elapsed_seconds": 151,
  "timestamp": "2026-05-16T13:14:32Z"
}
```

**Step 2 — resolve the canonical model ID.** The aggregator looks up rates in `token_pricing.json` by canonical model ID, not by short alias. Current records carry the canonical ID directly (e.g. `claude-opus-4-7`). If you are checking an older record with a short alias like `"opus"`, map it to the canonical ID the wake script would have used (Opus → `claude-opus-4-7`, Sonnet → `claude-sonnet-4-6`, Haiku → `claude-haiku-4-5`, subject to the rates currently in the pricing file).

**Step 3 — read the four rates from token_pricing.json.** Look up `providers.claude.models.<model_id>` and copy out the four `*_per_1m` values. Do not hardcode these from memory — open the live file. Example for one model (rates as of this writing; verify before using):

```bash
python3 -m json.tool team/scripts/lib/token_pricing.json | grep -A 1 'claude-opus-4-7'
```

**Step 4 — apply the per-category formula.** Each category cost is `tokens * rate_per_1m / 1_000_000`:

```
input_cost          = input_tokens                 * input_per_1m          / 1_000_000
cache_create_cost   = cache_creation_input_tokens  * cache_creation_per_1m / 1_000_000
cache_read_cost     = cache_read_input_tokens      * cache_read_per_1m     / 1_000_000
output_cost         = output_tokens                * output_per_1m         / 1_000_000
total_cost          = input_cost + cache_create_cost + cache_read_cost + output_cost
```

For the sample record above, plugging in the rates listed for `claude-opus-4-7` in the current `token_pricing.json` gives values on the order of:

- `input_cost`: a few hundredths of a cent (7 tokens × the input rate is barely visible)
- `cache_create_cost`: the largest line (`11,603 × 18.75 / 1M ≈ $0.218` at current rates)
- `cache_read_cost`: a few cents (`44,108 × 1.50 / 1M ≈ $0.066` at current rates)
- `output_cost`: a few cents (`1,200 × 75.00 / 1M = $0.090` at current rates)
- `total_cost`: a few tens of cents

If you re-run the calculation with whatever rates are in the live `token_pricing.json` at the time you read this, you will get the precise number that should appear in any roll-up that includes this task.

**Step 5 — compare against the report.** Find the same task in the relevant per-RC roll-up (`projects/<name>/usage/rc/<version>-tokens.json`, look in the `tasks` array for an entry with this task's agent and timestamp) and read the `cost_usd` field. It should match your hand calculation within rounding (the aggregator rounds to six decimal places). If it differs by more than rounding, suspect either a pricing-file mismatch (the rates the aggregator used differ from the rates you read), a model-name resolution failure (the aggregator could not find the model and silently used 0), or a stale roll-up file that pre-dates a pricing change.

For a coarser check, sum the `cost_usd` of every entry in a single RC's `tasks` array and compare against the RC's `totals.cost_usd`. They should agree. If they do not, the roll-up is internally inconsistent and re-aggregation is the fix: delete the roll-up file, re-run `cost-report.sh --rc <version>`, watch it rebuild from the per-task files.

### Keeping token_pricing.json fresh

Pricing lives in `team/scripts/lib/token_pricing.json`. The `cost-report.sh` script reads it from the project's `dev_tree_path` (per `project.cfg [project]`), falling back to the kanban root copy. Dollar amounts are never hardcoded in the script — the JSON is the source of truth.

The file has three top-level keys: `providers` (per-provider, per-model rates expressed as `*_per_1m`), `subscriptions` (subscription credit values used by the comparison block), and `updated` (an ISO date string). Bump the `updated` field whenever you change any rate or subscription value — it is the audit trail for "when did this pricing snapshot last reflect public list prices."

Update the file whenever:

- A provider publishes new list prices for an existing model.
- A new model is added that the kanban will use (otherwise the model shows `cost_usd=0` in reports and emits a stderr warning on first encounter).
- A subscription tier price or programmatic-credit value changes.

The file is plain JSON. Edit it, validate it parses (`python3 -m json.tool team/scripts/lib/token_pricing.json`), commit the change with the `updated` field bumped, and the next report will use the new rates.

### Linking cost data to provider decisions

The metrics layer exists because real numbers beat guesses for provider decisions. The operator's current option set spans Anthropic Pro/Max programmatic credit, Anthropic API direct (pay-as-you-go), Codex via ChatGPT Plus or Pro, and mixed strategies (subscription for the bulk of usage, API overflow above the credit cap). Per-agent provider selection is also on the table once the provider abstraction lands.

`cost-report.sh` does not prescribe an answer. It produces the data each option needs to be evaluated against — projected monthly burn, per-agent cost share (relevant if different agents are routed to different providers), and the explicit subscription-versus-API comparison block. Reading those numbers and choosing an arrangement is the operator's call.


## RC Abandonment: `team/scripts/cm/cancel-rc.sh`

When a Release Candidate needs to be abandoned before it ships — for example, when TESTER finds catastrophic gaps that warrant starting fresh, when the operator decides scope has changed significantly, or when an RC was opened for the wrong version — use `team/scripts/cm/cancel-rc.sh` to cleanly abandon it.

### When to use cancel-rc.sh

Use `team/scripts/cm/cancel-rc.sh` in these situations:

- **TESTER finds catastrophic gaps** that cannot be patched without a full re-decomposition — the RC quality is too low to iterate on.
- **Operator decides scope change is needed** — the work decomposed under this RC is no longer the right work to do; fresh requirements are needed.
- **Wrong RC version opened** — `team/scripts/cm/open-rc.sh` was run with the wrong version number and the error must be undone before starting over.
- **RC branch became inconsistent** — partial failures or manual edits left the project's `release-state.md` or the branch in a state that cannot be recovered automatically.

Do NOT use `team/scripts/cm/cancel-rc.sh` for minor gaps. TESTER writes priority requirements documents for the next patch release when gaps are small enough to fix without abandoning the current RC.

### Usage

```bash
# Interactive (prompts for confirmation):
team/scripts/cm/cancel-rc.sh v0.15.4

# Non-interactive (--yes skips confirmation prompt):
team/scripts/cm/cancel-rc.sh v0.15.4 --yes
```

### What cancel-rc.sh does

1. Validates the version format (`vX.Y.Z`).
2. Checks that the current branch is `${branch_prefix}main` or `rc/<version>`.
3. Reads the project's `release-state.md` and verifies `Active RC` matches the requested version.
4. Lists any pending kanban tasks that reference this RC version (for awareness).
5. Prompts for confirmation (unless `--yes` is passed).
6. Deletes `rc/<version>` from origin (if it exists there).
7. Deletes `rc/<version>` locally (if it exists).
8. Clears `Active RC`, `RC Opened At`, and `RC Opened By Task` in the project's `release-state.md` back to `none`.
9. Prints a summary and lists tasks to manually mark as `WONT-DO`.

### What cancel-rc.sh does NOT do

- Does NOT touch `main`.
- Does NOT delete any git tags.
- Does NOT automatically mark kanban tasks as `WONT-DO` — you must do that manually after running the script.
- Does NOT create a new RC — run `team/scripts/cm/open-rc.sh <new_version>` separately.

### Idempotency

`team/scripts/cm/cancel-rc.sh` is safe to re-run if it fails midway. Each step checks current state before acting:

- If `rc/<version>` is already gone from origin, the remote-delete step is skipped.
- If `rc/<version>` is already gone locally, the local-delete step is skipped.
- If `Active RC` is already `none`, the `release-state.md` reset is skipped.
- If all steps are already complete, the script reports "idempotent run" and exits 0.

### After running cancel-rc.sh

1. Manually set any pending kanban tasks for the cancelled RC to `WONT-DO` in their `status.md`.
2. If needed, archive or remove the requirements document for the cancelled version.
3. Open a new RC when ready: `team/scripts/cm/open-rc.sh <next_version>`.

### Example: abandoning v0.15.4 and starting v0.15.5

```bash
# Abandon the current RC
team/scripts/cm/cancel-rc.sh v0.15.4 --yes

# Verify the cleanup
git ls-remote origin rc/v0.15.4   # should return nothing
grep -A1 "Active RC" "$KANBAN_ROOT/projects/<project-name>/release-state.md"  # should show "none"

# Open the next RC
team/scripts/cm/open-rc.sh v0.15.5
```

## Cancelling an in-flight RC

This section is the operator playbook for unwinding an in-flight release candidate end-to-end with `team/scripts/unwind-rc.sh` and verifying the result with `team/scripts/verify-rc-state.sh`. Use it when a TESTER-blocked RC needs to be re-rolled rather than waived, when an RC was opened against the wrong version, or when the chain's state stores have drifted far enough that the next iteration cannot pick up cleanly.

The broader script suite is the successor to `team/scripts/cm/cancel-rc.sh`. The older script (covered in the section above) deletes branches and resets `release-state.md` only; it leaves task folders, queue caches, requirements files, PM plan markers, and bundled PRIORITY/BUG markers untouched. `unwind-rc.sh` walks all of those state stores in one pass and creates a recoverable backup before it does anything destructive. Reach for the new script when an RC has accumulated real material — task folders, queue activity, materialized plan markers. Reach for the older `team/scripts/cm/cancel-rc.sh` only when nothing has happened beyond `team/scripts/cm/open-rc.sh` and you want a minimal undo.

### When to cancel versus when to waive

Cancellation and waiver are the two ways out of a stuck RC. They are not interchangeable.

**Cancel** when restarting the RC is cheaper than fixing what's wrong in place:

- TESTER reports catastrophic gaps that span multiple decomposed tickets. The decomposition itself is wrong, not a single ticket.
- The operator has decided the scope is wrong and wants to re-author the requirements document before the chain produces anything more on this branch.
- `team/scripts/cm/open-rc.sh` was run with the wrong version number and the rest of the chain has not progressed past trivial work.
- The RC's state stores are inconsistent enough that `verify-rc-state.sh` reports multiple errors and the cheapest path forward is a clean re-roll.

**Waive** (per CM's `SHIP-WITH-CONCERNS` / `SHIP-WITH-SERIOUS-CONCERNS` ship policy) when the RC is materially complete and the residual gaps are small enough to fix in the next patch release:

- TESTER's findings are small, isolated, and addressable as PRIORITY items in the next RC.
- The release tag would still represent forward motion for downstream consumers.
- The fix effort to address concerns is `small` or `medium`, not `large`.

The waiver path stays inside the autonomous chain — CM applies it, ships the release, and TESTER's findings become priority requirements for the next RC. The cancel path requires the operator to halt, run scripts, drop a fresh requirements doc, and unhalt. Prefer waiver when the chain is producing usable output. Prefer cancel when the chain is producing the wrong output.

### Pre-flight checklist

`unwind-rc.sh` enforces these checks itself and refuses to run when any fails. Knowing them up front shortens the iteration when something is off.

1. **The system is halted.** Either the global `HALT` flag (`$PGAI_AGENT_KANBAN_ROOT_PATH/HALT`) or the per-project `HALT` flag (`$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT`) must exist before `unwind-rc.sh` will modify anything. This prevents the wake scripts from scheduling new work mid-cancellation. `--force` does not bypass this check.

2. **The version argument is the right version.** `unwind-rc.sh --project <name> --key <version>` confirms that `Active RC` in the project's `release-state.md` matches `<version>` before proceeding. The check exists because cancelling the wrong RC is hard to recover from and easy to do by typo. `--force` bypasses only this single check — useful for partial-cancellation recovery where `release-state.md` has already been reset but other stores remain. `--confirm` is not a substitute: it answers the confirmation prompt for a caller with no terminal, and bypasses no pre-flight check — this one included.

3. **The version has not already shipped.** The script refuses to operate on any version that exists as a git tag in the dev tree. Shipped releases are immutable; if a tagged release is broken, the response is a new patch version, never a cancellation of the tag. `--force` does not bypass this check.

4. **The project exists and the dev tree is reachable.** `unwind-rc.sh` resolves the project name through `pp_require_project_context` and reads `dev_tree_path` from the project config. If either fails, the script exits before touching state.

To halt the entire kanban before cancelling:

```bash
touch "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
```

To halt only one project:

```bash
touch "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"
```

### Running unwind-rc.sh

The script lives at `team/scripts/unwind-rc.sh`. Its full signature is:

```
unwind-rc.sh --project <name> --key <vX.Y.Z> [--dry-run] [--confirm] [--force]
```

Start with `--dry-run`. It runs every pre-flight check, builds the full inventory of what would be modified, prints the plan, and exits 0 without changing anything. Reading the plan is the cheapest way to confirm the script will touch exactly what you expect.

```bash
team/scripts/unwind-rc.sh --project pgai-agent-kanban --key v0.26.3 --dry-run
```

The plan lists, in the order the script will execute them: backup destination, RC branch state (local and remote), task folders that will flip to `WONT-DO`, queue entries that will flip to `[x]`, the requirements file that will be renamed, PM plan markers that will be removed, PRIORITY and BUG backlog entries that will flip back to `[ ]`, the `release-state.md` reset, and any discovery-state cache files that will be removed.

When the plan matches your intent, run the script for real:

```bash
team/scripts/unwind-rc.sh --project pgai-agent-kanban --key v0.26.3
```

The interactive run prints the same plan, then prompts with `Proceed with cancellation of rc/<version>? Type 'yes' to continue:`. Type `yes` exactly — any other input aborts without modifying state. For scripted invocation, `--confirm` answers the prompt and leaves every pre-flight check in place. `--force` also skips the prompt, but it skips the Active-RC-mismatch check too, so reserve it for the recovery case above.

What the script touches, in execution order:

- **Backup.** Before any destructive step, the script copies `tasks/queues/`, `requirements/`, `priority/`, `release-state.md`, and every task folder referencing the cancelled version to `$(pgai_temp_dir)/unwind-rc-<version>-backup-<TIMESTAMP>/`. Restore is one `cp -a` away.
- **Git unwind.** Checks out `${branch_prefix}main` in the dev tree, deletes the local `rc/<version>` branch (with `-D`, force-delete), deletes the remote `origin/rc/<version>`, and force-deletes any orphan `feature/CLAUDE-*` branches whose task IDs belong to the cancelled RC.
- **Task folders.** For every task folder whose README references the cancelled version, flips `## State` to `WONT-DO` and appends a `## Cancellation Note (TIMESTAMP)` block. Folders already carrying a Cancellation Note are skipped — the step is idempotent on re-run.
- **Queue caches.** Walks every `*_backlog.md` and flips `[ ]`, `[W]`, `[A]`, and `[B]` markers to `[x]` for any line referencing a cancelled task ID. Existing `[x]` markers are left alone.
- **Requirements file.** Renames the requirements document that produced the cancelled RC from `<version>-<slug>.md` to `<version>-<slug>.SUPERSEDED-on-cancel-<TIMESTAMP>.md` and sets its `## Status` to `wont-do`. A superseded bundle is closed: it never holds discovery's bundling slot and never counts as queued requirements work, so discovery goes on minting new bundles. Every other line is preserved verbatim so the bundled PRIORITY/BUG list can still be read. A file already superseded by an earlier run is closed the same way; a file with no `## Status` is left alone with a warning.
- **PM plan markers.** Removes `.materialized.*` files under `tasks/queues/plans/` whose contents reference the cancelled version's plan hash.
- **Priority backlog.** Reads the `## Bundled Items` section of the superseded requirements file, finds each `PRIORITY-NNNN` entry, flips its `[x]` marker back to `[ ]` in `priority_backlog.md`, and resets each priority file's `## Status` to `open` so discovery re-bundles it on the next iteration.
- **Bug backlog.** Same logic as priority — `BUG-NNNN` entries flip back to `[ ]` and each bug file's `## Status` resets to `open`.
- **release-state.md.** Resets `Active RC`, `RC Opened At`, and `RC Opened By Task` to `none`. Historical fields are preserved verbatim.
- **Discovery state cache.** Removes any files under `projects/<name>/.discovery-state/` whose names contain the cancelled version string.

Exit codes are deliberate:

- `0` — every step completed (or `--dry-run` succeeded). State is fully unwound.
- `1` — a pre-flight check failed (no HALT, wrong version, shipped tag, missing project) or the operator declined the confirmation prompt. No state was modified.
- `2` — partial completion. The backup directory was created but a later step failed. Inspect the script output for the failure point, fix the underlying issue, and either re-run (most steps are idempotent) or restore from backup (see "Restoring from backup" below).

### Verifying clean state with verify-rc-state.sh

After `unwind-rc.sh` exits 0, run `team/scripts/verify-rc-state.sh` to confirm the project's state stores are consistent with each other.

```bash
team/scripts/verify-rc-state.sh pgai-agent-kanban
```

The checker is read-only. It walks every state store and reports `OK`, `WARN`, or `ERROR` findings, then prints a summary line like `verify-rc-state: 0 errors, 1 warning, 24 ok`. By default only warnings and errors are printed; add `--verbose` to print every check.

The checks span six categories:

- **release-state.md consistency** — `Active RC` is `none` after cancellation; if non-`none`, the named `rc/<version>` branch must exist locally.
- **Queue ↔ folder consistency** — every non-`[x]` task ID in a backlog has a matching task folder, and every task folder has a matching queue entry.
- **Marker ↔ state consistency** — `[ ]` lines correspond to `BACKLOG`, `[W]` to `WAITING`, `[A]` to `WORKING`, `[B]` to `BLOCKED`, and `[x]` to `DONE` or `WONT-DO`.
- **Prerequisites resolution** — every task ID listed in any task README's `## Prerequisites` section resolves to a real task folder.
- **release-state.md ↔ git branch consistency** — when `Active RC` is `none`, no `rc/*` branches should exist locally (this is a warning, not an error — it may flag operator-staged work).
- **Bundle invariants** — every PRIORITY/BUG file referenced in a requirements bundle exists on disk, and every PRIORITY/BUG marked `[x]` in its backlog corresponds to a bundled requirements file.

**Both branch topologies are covered.** The branch-consistency check builds the
RC branch name through the framework's one branch-name resolver, so a project
carrying a `[project] branch_prefix` in its `project.cfg` is checked against
`<prefix>rc/vX.Y.Z` and a project without one is checked against `rc/vX.Y.Z`.
Neither topology is the special case: a prefixed project with a consistent RC
passes exactly as a bare one does. The `Active RC` value that name is built
from is read through the shared release-state reader as well, so this check and
the release-state loader cannot drift apart on what the field says.

Exit codes:

- `0` — no errors. Warnings, if any, are informational.
- `1` — one or more errors. The state stores are inconsistent and the next iteration may produce wrong work.
- `2` — pre-flight failure (bad arguments, project not found).

After a clean cancellation, `verify-rc-state.sh` should exit 0. If it reports errors, either re-run the failed `unwind-rc.sh` step (most steps are idempotent) or restore from backup and investigate before proceeding.

### Dropping the fresh requirements document for the re-roll

With state verified clean, the next step is to drop a new requirements document that PM will pick up on the next wake firing. The mechanics are the same as any operator-authored brief — the directory layout and the picking rule are documented in the `Filing a Document Brief` section above (for document workflows) and the `Workflow Types` and `Priority Queue Mechanics` sections (for release and feature workflows).

The short version for a release re-roll:

- Place the new file at `$KANBAN_ROOT/projects/<name>/requirements/<vX.Y.Z>-<slug>.md` (regular queue) or `requirements/priority/<vX.Y.Z>-<slug>.md` (priority queue).
- The filename's version must be greater than `pp_last_released_version` for the project, or it will be skipped as stale.
- The version may be the same as the cancelled RC's version. The cancelled requirements file has been renamed with `.SUPERSEDED-on-cancel-<TIMESTAMP>.md` and will not collide.
- The discovery pipeline picks the new file up at Step 3 on the next iteration and queues PM to materialize it.

Bundled PRIORITY and BUG items from the cancelled RC are back in their original `[ ]` state and their `## Status` headers are back to `open`. Discovery will re-bundle them into the next requirements document automatically — the operator does not have to list them by hand in the new brief unless the intent is to exclude some of them deliberately.

Once the new requirements file is in place, remove the HALT flag to resume the chain:

```bash
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"
# or, for a per-project halt:
rm "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT"
```

The next wake firing picks up the new requirements document and the chain proceeds against the re-rolled RC.

### Common pitfalls

**Forgot to halt.** `unwind-rc.sh` refuses to run without a HALT flag in place. The check exists because the wake scripts will otherwise dispatch new work into a project whose state is being torn down underneath them, and the chain ends up in a much worse position than either a clean cancel or a clean continuation. The error message includes both `touch` commands. Pick the scope you want, create the flag, re-run.

**Wrong version argument.** The Active-RC-mismatch check refuses by default when `<version>` is not the value in `release-state.md`. The check exists because cancelling the wrong RC is almost always a typo. Re-read the script's error message — it prints both the requested version and the value found in `release-state.md`. Correct the argument and try again. Use `--force` only when you have genuinely intended to bypass this check (for example, when an earlier partial cancellation left `release-state.md` already at `none` but you need to clean up other stores referencing the old version).

**Partial completion (exit code 2).** When `unwind-rc.sh` exits 2, the backup at `$(pgai_temp_dir)/unwind-rc-<version>-backup-<TIMESTAMP>/` was created but a later step failed. Read the script's stderr to identify which step failed. Most steps are individually idempotent and can be re-run by re-invoking `unwind-rc.sh` (it will re-do the inventory and skip steps that have already completed). If the failure is structural — a permission error, a git operation that needs operator attention, an unexpected file format — restoring from backup is often faster than debugging in place.

**Restoring from backup.** The backup directory holds a snapshot of `tasks/queues/`, `requirements/`, `priority/`, `release-state.md`, and the matching task folders as they existed just before the destructive steps began. To restore byte-identical pre-cancellation state, copy the backup contents back into the project root:

```bash
cp -a $(pgai_temp_dir)/unwind-rc-<version>-backup-<TIMESTAMP>/. "$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/"
```

The git unwind step (deleting the local and remote RC branch) is not covered by the backup — restoring branches requires either re-creating them from a reflog entry or re-running `team/scripts/cm/open-rc.sh` to cut a fresh branch. If branch state matters and a restore is likely, capture the branch SHAs before running `unwind-rc.sh` for real.

**Re-running after a partial run.** Every action step in `unwind-rc.sh` is written to be idempotent. Task folders that already carry a `## Cancellation Note` block are skipped. Queue entries already at `[x]` are left alone. The requirements file is only renamed if a non-superseded original exists. `release-state.md` fields already at `none` are not rewritten. The orphan-branch detection only flags branches whose task IDs are in the cancelled RC's task set. Re-running on a partially-cancelled RC converges to the same end state as a clean run.

**Cancelled the wrong RC.** If you discover after the fact that the wrong RC was cancelled, restore from backup, re-cut the RC branch with `team/scripts/cm/open-rc.sh`, and re-bundle the requirements file by renaming it back from `*.SUPERSEDED-on-cancel-*.md` to `<version>-<slug>.md`. Renaming alone is not enough: the unwind set the file's `## Status` to `wont-do`, which discovery reads as closed and skips. Set it back to `open`, the value discovery reads as a bundle not yet queued for PM, so the next iteration picks it up. The bundled PRIORITY/BUG items need the same care: the unwind reset each one's `## Status` to `open`, and discovery reads that header, never the `[ ]`/`[x]` backlog marker, so set each item's `## Status` back to `running` by hand. Otherwise discovery offers them again as unbundled work. This is recoverable but tedious; the `--dry-run` pass exists to prevent it.

**Forgot to verify before unhalting.** Without `verify-rc-state.sh` between cancellation and unhalt, an inconsistency missed by `unwind-rc.sh` propagates into the next iteration as wrong work. The chain may produce tasks against stale state, fail in unexpected places, or — worse — produce work that materially conflicts with the next RC's intent. The verify step takes seconds. Run it every time.

## RC open and release recovery

The historical "uncommitted release-state.md" failure class no longer exists. `release-state.md` lives at `$KANBAN_ROOT/projects/<project-name>/release-state.md` (the install location, not the dev-tree's `team/`). It is not version-controlled. There is no git-commit step for `team/scripts/cm/open-rc.sh` to fail mid-way through, and `team/scripts/cm/release.sh` does not guard against uncommitted changes to it.

If CM scripts fail mid-run today, the symptom surface is one of:

- **`Active RC` is set but the RC branch does not exist on origin.** `team/scripts/cm/open-rc.sh` exited between writing the file and creating the branch. Re-run `team/scripts/cm/open-rc.sh <version>` — it is idempotent and will detect the existing `Active RC` value, skip the file write, and complete the branch creation.
- **The RC branch exists on origin but `Active RC` is `none`.** The file write failed but the branch push succeeded. Re-run `team/scripts/cm/open-rc.sh <version>` — it will detect the existing branch, write the file, and complete.
- **Neither side completed.** Re-run `team/scripts/cm/open-rc.sh <version>` from a clean state.

If a recovery situation falls outside these patterns, run `team/scripts/cm/cancel-rc.sh <version>` to clear in-flight state and reopen.

## Release Lifecycle Hooks

`team/scripts/cm/release.sh` and the operator-manual `ship-rc.sh` script both call up to three lifecycle hooks during a release: **pre-squash** (before the RC branch is squashed to main), **pre-tag** (after squash to main, before `git tag`), and **post-tag** (after the tag exists, before the push to origin). Hooks are optional by default; configure them per project when there is release work that must run at one of those seams (version bump, consistency check, notification, asset upload).

Both scripts resolve, print, and enforce hooks through the same shared library (`team/scripts/lib/cm_release_hooks.sh`) — behavior is identical between the autonomous CM path and the manual operator path.

### Three-tier resolution precedence

For each phase, the release script searches three locations and uses the first one that yields a path. The tiers, highest precedence first:

| Tier | Source label | Location | Purpose |
|---|---|---|---|
| (a) | `cfg` | `project.cfg [hooks] cm_release_<phase>_hook` | Explicit deployment config. Absolute path, or relative to `dev_tree_path`. |
| (b) | `kanban-side` | `$KANBAN_ROOT/projects/<name>/hooks/cm-release-<phase>.sh` | Kanban-side deployment override. Not versioned with the managed repo. |
| (c) | `in-repo` | `<dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh` | Portable hook that travels with the managed repository. Versioned and reviewed alongside application code. |

The design mirrors the ports-are-topology philosophy: deployment overrides app default. Tier (c) is the project's own default — versioned, reviewed, and immune to project recreation. Tier (b) exists so an operator can wire a temporary override without editing the repo. Tier (a) exists so a hook can live at any explicit path (e.g., a shared script directory).

An in-repo hook (tier c) that exists but is not executable is an **error**, not a silent skip. The release halts with a message naming the path and the `chmod +x` fix. Kanban-side and cfg hooks that exist but are not executable emit a warning and skip (backward-compatible behavior).

### The always-printed hook line

Every release run prints exactly one resolution line per phase, immediately before the phase runs. Absence is never silent again — it is a stated fact in every release log. The two forms:

```
<phase> hook: <resolved absolute path> (source: cfg|kanban-side|in-repo)
<phase> hook: none configured
```

`<phase>` is literally one of `pre-squash`, `pre-tag`, `post-tag`. The `(source: …)` suffix names the winning tier. When nothing resolves at any tier and the phase is not required, the release proceeds and the line reads `none configured`.

Three lines per release is the honest common case for projects without lifecycle hooks. When reviewing a release log after this feature ships, expect to see three of these lines regardless of configuration.

### Required-hook enforcement (opt-in, per phase)

For projects where "no hook" is always wrong — for example, a project whose pre-squash hook bumps the version file before every release — set the matching required flag in `project.cfg [hooks]`:

```ini
cm_release_pre_squash_hook_required = true
cm_release_pre_tag_hook_required = true
cm_release_post_tag_hook_required = true
```

Default is `false` for all three keys. Existing projects are unaffected.

When a required flag is `true` and no hook resolves at any of the three tiers, the release **HALTs before the phase runs**. The HALT message names the phase, all three searched locations, and the config key that made the phase required. No tag is created. The autonomous CM path files a BLOCKED CM task and creates the project's `HALT` file (`$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/HALT`) so the operator can inspect; sibling projects keep running. The manual `ship-rc.sh` path exits non-zero and prints the same message.

The failure grammar matches the lane-fidelity gate: name what was expected, name where it was looked for, name the key that governs the expectation. This is the operator-diagnosable form — everything needed to fix the wiring is in the halt message.

### Hook environment contract

The two scripts run hooks with an identical environment. This is the **canonical reference**; any other doc that discusses hook variables links back here rather than duplicating the list.

| Variable | Value | Notes |
|---|---|---|
| `PGAI_TARGET_VERSION` | The version being shipped (e.g. `v1.18.0`) | Same as the RC's target version. |
| `PGAI_PROJECT_NAME` | Project name as registered in `projects.cfg` | Not derived from `cwd`. |
| `PGAI_PROJECT_ROOT` | Absolute path to `$KANBAN_ROOT/projects/<name>/` | The per-project kanban directory (queues, requirements, release-state.md). |
| `PGAI_DEV_TREE_PATH` | Absolute path to the managed git checkout | Same as `project.cfg [project] dev_tree_path`. Equals `cwd` at hook start. |
| `PGAI_RC_BRANCH` | Full RC branch name (e.g. `ai_rc/v1.18.0`) | Includes the project's branch prefix. |
| `PGAI_KANBAN_ROOT` | Absolute path to the kanban install root | Same as `$PGAI_AGENT_KANBAN_ROOT_PATH`. |

**cwd** is the dev tree path (`PGAI_DEV_TREE_PATH`). Hooks may assume `git` operates against the managed repo without an explicit `-C` flag.

Hook stdout and stderr are captured and prefixed with `[hook <name>]` in the release log. Non-zero exit blocks the release for pre-squash and pre-tag; for post-tag the failure is logged but does not block (the tag already exists, so aborting after it is fruitless).

### Configuring hooks in project.cfg

`project.cfg_example` carries commented `[hooks]` entries for all three phase-hook keys and all three `*_required` keys. See the file for inline documentation; the common patterns:

- **No hook needed.** Leave all six keys commented out (the default). The release prints `none configured` three times and proceeds.
- **Portable hook that survives project recreation.** Drop an executable script at `<dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh` and commit it to the managed repo. The `.pgai/` prefix is the portable in-repo hook location — no `project.cfg` edit is required for the hook to resolve.
- **Required portable hook (fail-loud on missing wiring).** Same as above, plus set `cm_release_<phase>_hook_required = true` in `project.cfg [hooks]`.
- **Kanban-side override.** Drop the hook at `$KANBAN_ROOT/projects/<name>/hooks/cm-release-<phase>.sh` when the hook should NOT ship with the managed repo (temporary, deployment-specific, or a testing override).
- **Explicit path.** Set `cm_release_<phase>_hook` in `project.cfg [hooks]` to point at a script anywhere. Absolute paths are used as-is; relative paths resolve against `dev_tree_path`.

### The pre-squash HALT trigger, in context

The pre-squash hook failure is trigger #4 of the nine CM HALT triggers (see "CM HALT triggers" above). Two failure paths reach that trigger under this design:

- The pre-squash hook resolved and ran, but exited non-zero. The release blocks; the operator inspects the `[hook cm-release-pre-squash]` log lines.
- No hook resolved at any tier and `cm_release_pre_squash_hook_required = true`. The release HALTs before the phase; the operator adds the missing hook or clears the required flag.

Both paths land in the same BLOCKED CM task and the same operator-response procedure. The required-flag path makes lost hook wiring visible at the first release that misses it, rather than letting it drift silently across releases.

## Completing a Manually Unblocked Ticket

When a human performs work that the automated system considers BLOCKED -- for example, manually running a release script, manually resolving a dependency, or directly applying a fix -- the system state will not update itself. The ticket will remain in BLOCKED state even though the work is done. This section describes how to reconcile the record.

### When this applies

Use this procedure when ALL of the following are true:

- A task's `status.md` shows `State: BLOCKED`
- The work described in the task's `README.md` has been completed by a human (or by a process outside the automated agent pipeline)
- No automated agent completed the task through the normal WORKING -> DONE path

### When this does NOT apply

This is **not** a "TESTER waiver" procedure. The old waiver pattern — operator flipping a TESTER task from `BLOCKED` to `DONE` after deciding a found bug was acceptable — is no longer used. TESTER no longer reaches `BLOCKED` for found bugs, stale assertions, pre-existing failures, or gaps. TESTER files those via Path C and continues to `DONE` with a `SHIP-WITH-CONCERNS` or `SHIP-WITH-SERIOUS-CONCERNS` recommendation. CM applies ship policy.

If you see a TESTER task in `BLOCKED` state, it means verification literally could not complete (pre-flight failure, runner crash, missing requirements doc, unreachable dev tree). The fix is to repair the infrastructure problem and re-run TESTER — not to flip the state by hand. Manually marking a `BLOCKED` TESTER task as `DONE` would discard the signal that verification did not happen and silently bypass the chain's halt mechanism.

For CM tasks in `BLOCKED` state, see the "When the chain halts" section above. CM blocks itself when it creates an autonomous HALT; the operator's job is to investigate the trigger, resolve the underlying issue, remove that project's HALT file, and re-run (or supersede) the CM task. The procedure below applies only when the operator has independently completed the underlying release work outside the pipeline.

### Step-by-step procedure

Steps 2 through 5 change exactly the four fields the status-writer tool manages — `## State`, `## Needs Human`, `## Blockers`, and `## Summary` — so one tool call does all four at once. See "Writing task state" in `SOP-core.md` for the invocation. The hand-edits below still parse and remain a valid operator path; they are spelled out here so the shape of a correctly completed status file is on the record.

1. **Open the task's `status.md`** in the relevant task folder.

2. **Set `State` to `DONE`.**

   ```
   ## State
   DONE
   ```

3. **Set `Needs Human` to `no`.**

   ```
   ## Needs Human
   no
   ```

4. **Clear `## Blockers`** or set it to `none`. The blocker has been resolved.

   ```
   ## Blockers
   none
   ```

5. **Update `## Summary`** to briefly describe what was done and by whom (e.g., "Manually completed by human on 2026-04-27. Release script was run by hand.").

6. **Update the queue marker.** Find the task's entry in the appropriate queue backlog file (e.g., `team/tasks/queues/cm_backlog.md`). Change the marker from `[B]` to `[x]`:

   Before:
   ```
   [B] TASK-ID — short description
   ```

   After:
   ```
   [x] TASK-ID — short description
   ```

7. **Commit the changes** to the task's `status.md` and the queue backlog file together:

   ```bash
   git add team/tasks/queues/cm_backlog.md
   git add team/tasks/<TASK-ID>/status.md
   git commit -m "Mark <TASK-ID> DONE (manually completed by human)"
   git push origin <branch>
   ```

### Why this matters

Stale BLOCKED entries cause downstream confusion: PM agents may misread the state, wake scripts may attempt to re-process a finished task, and queue depth counts will be inaccurate. Keeping queue markers and `status.md` synchronized with actual work state ensures the system remains an accurate record of truth.

### Do not use WONT-DO

If the work was genuinely completed, use `DONE`. `WONT-DO` means the work was intentionally skipped or declined. Using `WONT-DO` for completed work misrepresents the history.

## Cron and Wake Cadence

Cron drives the chain at a fixed cadence — every 2 minutes per agent by default, with a sub-minute stagger so agents in the same 2-minute window do not collide. In `team/templates/install/crontab.example`, PM fires at :00, CM at :00+21s and TESTER at :00+42s on even minutes; CODER fires at :01 and WRITER at :01+21s on odd minutes. The size-tier templates beside it (`crontab-small.example`, `crontab-medium.example`, `crontab-large.example`) fire each agent every 5 minutes, every 2 minutes and every minute respectively.

The stagger spreads start times only. Every role's wake for a project takes the same repo lock, so a wake that runs past the next role's slot still holds it when that role fires; "Repo lock and contention" below covers what the later wake does.

Each firing runs one main-loop pass per registered project: pick the next BACKLOG task, run the agent, repeat until the project's backlog is empty or a stop condition trips. When the loop has no more BACKLOG tasks and PM is operating in `automatic` mode, the autonomous scan (`discovery_run_pipeline`) fires before the firing exits — it inspects bugs, priority requirements, and regular requirements for fresh work and may queue a new PM task. The scan runs without the repo lock, and on a busy chain it exits in seconds without scanning anything.

Without further machinery, a PM task queued by the autonomous scan would sit in the just-emptied backlog until the next cron tick before being processed. For the common operator workflow ("drop a bug, see what happens, drop another") that wait time compounds across iterations.

### Same-firing post-discovery processing

When `discovery_run_pipeline` queues a fresh PM task and the chain is genuinely idle, the wake script re-enters the main loop body **exactly once** within the same firing to process that task. The fresh-task scenario therefore converges in well under a minute after a cron tick, not 5+ minutes.

The post-discovery iteration sits in the PM wake's discovery branch (`wd_run_pm_discovery` in `team/scripts/lib/wake_driver_discovery.sh`), which `run_project_chain` calls after its task loop. It is bounded to one extra iteration — not a loop. After that single iteration, the project's per-firing work is complete and the next batch of decomposed downstream tickets (CODER, WRITER, TESTER, CM) waits for the next cron tick belonging to the appropriate agent.

The trigger is the discovery library's `DISCOVERY_LAST_STATUS=produced_work` signal. That signal fires whenever Step 3 of the pipeline queued PM. It also fires when Step 1 or Step 2 bundled bugs or priority items into a requirements file, which stops that iteration before Step 3 runs, so the post-discovery iteration **does not trust `produced_work` alone** — it re-checks the safety conditions before re-entering.

### Three safety gates

Before the post-discovery iteration fires, three explicit gates must all clear. If any gate fails, the wake script logs the reason, skips the iteration, and falls back to the normal cron cadence (the next firing handles the work). The gates are intentionally redundant — the cost of a wrong re-entry during an RC in flight is much higher than the cost of waiting one cron interval.

1. **HALT re-check.** If `${TEAM_ROOT}/HALT` exists at this point in the firing, skip. The flag may have been created between the start of the autonomous scan and the post-discovery check, and HALT must be honored as soon as it is observed.
2. **Active RC re-check.** If the project's `release-state.md` shows `## Active RC` other than `none`, skip. An RC is in flight and PM must not decompose new work mid-flight. Discovery's chain-busy guard already exits before any scan while an RC is active, but the post-discovery iteration re-checks as defense in depth — the state could have changed between that guard and this point.
3. **`process_one_task` natural empty-queue guard.** Even after the first two gates pass, `process_one_task` only runs when a real BACKLOG task is selectable from the project's queue. If none is found, the call is a no-op and the loop exits cleanly without firing an agent.

The dispatch also runs only under the repo lock. PM let go of the lock for the discovery scan, and discovery took it back only if it wrote something. If PM does not hold the lock again at this point, or it no longer owns it when ownership is re-verified, the iteration is skipped and the next PM wake dispatches the task.

The three-gate design preserves a key invariant: the post-discovery iteration cannot process a task during an Active RC, cannot run while HALT is set, and cannot fire on a phantom queue entry. Any failure mode falls back to "wait for the next cron tick" — the worst outcome of a gate trip is one cron interval of latency, never incorrect execution.


### Operator implications

- Drop a bug or priority requirement: discovery picks it up on the first PM wake that finds the chain idle. While any role-queue task is unfinished or an RC is active, discovery exits before scanning, so the file waits.
- Drop a brief mid-cascade while an RC is in flight: discovery exits before any scan while an RC is active, and the post-discovery iteration re-checks Active RC; the new brief waits until the current RC ships.
- Set HALT mid-firing: the post-discovery iteration honors HALT even if the autonomous scan already started, so a late HALT takes effect within seconds rather than across firings.
- The same gating logic applies to `pm-agent.sh --auto` (the convenience kicker) so the autonomous and immediate paths behave identically.
- A role with ready work behind a running wake does not need a manual wake. Its own firing waits for the lock (below), and repeated losses trip the starvation guard, which steps PM's discovery aside.

### Repo lock and contention

Each project has one repo lock. A role's wake takes it at the start of its chain for that project, recording op `wake-chain:<project>` in the lock's provenance, and holds it until the chain ends — through task selection, the agent run and the merge. Two roles never hold it at once. Three behaviours decide what happens when two wakes want it.

#### An idle PM wake holds no lock while it scans

When PM's queue is empty (or dispatch is held by the blocked-task guard) and PM is in `automatic` mode, PM releases the repo lock before it runs discovery and logs `repo lock released for read-only discovery; discovery re-takes it only to write`. After the HALT and pending-PM-ticket guards, discovery's first check is whether the chain is busy: an Active RC, or any task named in any role queue whose own `status.md` is not DONE or WONT-DO. A busy chain exits before any scan, so the whole PM wake takes seconds. The wake log names the cause, then the exit:

```text
discovery_run_pipeline [<project>]: chain busy — <agent>_backlog task <TASK-ID> is <STATE>
discovery_run_pipeline [<project>]: chain busy, idle exit before any scan
```

The cause line reads `discovery_run_pipeline [<project>]: chain busy — Active RC=<version>` when an RC is open, and `discovery_run_pipeline [<project>]: chain busy — <agent>_backlog names <TASK-ID> with unparseable status.md` when a queued task's state cannot be read.

On an idle chain, discovery scans without the lock and takes it only once it has something to write: a bundle into `requirements/`, an intake item's status flip, or a PM ticket. That acquisition is a single attempt and never waits. Two outcomes stop the write, and the iteration ends idle:

- Another role holds the lock: `<step>: repo lock not available for the discovery write; deferring to the next wake`.
- The busy test, re-run under the lock, finds the chain turned busy during the scan: `<step>: chain became busy during the unlocked scan; not writing`.

A PM wake that wrote keeps the lock for its post-discovery dispatch and releases it when its chain ends. `pm-agent.sh --auto` runs the same pipeline outside any wake chain. It takes the lock for the write itself, with op `discovery-write:<project>`, and releases it before returning.

#### A contended wake waits for another wake chain

A wake that finds the lock held by another wake chain — the lock record's op is `wake-chain:<project>` — does not give up at the first probe. It retries with backoff, starting at 1 s and doubling to 8 s between attempts, for up to `[wake] lock_wait_seconds` (default `120`; `0` makes every acquisition a single attempt). That outlasts an idle chain's hold, so a role with ready work takes the lock and dispatches in the same firing once the holder lets go.

```text
project <project>: waiting up to <S>s for the repo lock held by wake chain holder=<h> pid=<p> op=wake-chain:<project> task=<t> (lock_wait_seconds=<N>, batch budget left=<B>s)
project <project>: repo lock acquired after waiting <S>s
project <project>: repo lock still held after waiting <S>s (lock_wait_seconds=<N>)
```

`batch budget left=` reads `unbounded` instead of a number of seconds when the wake has no batch runtime clock. The wait has three bounds:

- **Batch budget.** It never runs past the batch's remaining `max_runtime_seconds`.
- **Stop file.** It stops as soon as the operator stop file appears: `project <project>: not waiting for the repo lock: stop file present at <path>`.
- **One budget per project per firing.** The end-of-iteration retry waits only for what the first attempt left unused. When nothing is left the wake says so: `project <project>: no repo-lock wait left this firing (waited <W>s of lock_wait_seconds=<N>, batch budget left=<B>s)`.

Any other holder is not waited on: an OVERWATCH repair, `pm-agent.sh --auto`'s `discovery-write:<project>` lock, an operator tool, or a lock with no record. The wake defers at once and logs `project <project>: not waiting for the repo lock: its holder is not a wake chain (<provenance>)`. A refused acquisition — incomplete provenance or an unusable lock directory — also returns at once.

Whenever the first attempt ends without the lock — the wait ran out, or the holder was not one to wait on — the wake logs `project <project>: repo lock held by <provenance> -- probe <n>, deferring for an end-of-iteration retry` and moves on to its other projects. It tries once more at the end of the iteration, waiting only for any budget left. If the lock is still held, it logs `project <project>: retry pass: still contended after retry (<n> probe(s) this firing), deferring to the next wake firing`.

#### The starvation guard gives a losing role priority

A role that ends a firing still deferred on contention while its own queue holds a BACKLOG task adds one to a count kept per project and per role in `$KANBAN_ROOT/locks/lock-starvation/<project>/<role>.state`. A firing whose retry was cut short by the batch budget counts. A deferral caused by the stop file or a HALT does not. Below the threshold the wake logs `project <project>: lock starvation count for <role>: <n> of <threshold> consecutive contention deferral(s) with task <TASK-ID> BACKLOG`.

At `[wake]` `lock_starvation_firings` consecutive firings (default `3`; `0` turns the guard off), and on every deferral after that, the wake logs the starvation warning:

```text
project <project>: WARNING: lock starvation: <role> has deferred on repo-lock contention for <N> consecutive firing(s) while its task <TASK-ID> is BACKLOG -- PM wakes skip discovery while that task is ready
```

From then on, every PM wake for the project skips discovery while that role's task is still BACKLOG, so PM stops taking the lock for discovery. PM re-checks the task's state at each wake and logs:

```text
project <project>: autonomous scan: skipping discovery -- <role> task <TASK-ID> is ready and <role> has deferred on repo-lock contention for <N> consecutive firing(s)
```

The count resets, and its file is removed, when the role takes the lock (`project <project>: lock starvation count for <role> reset: repo lock acquired`) or defers with no BACKLOG task (`project <project>: lock starvation count for <role> reset: no BACKLOG task`). The guard changes no task state, writes no HALT and refuses nothing.

The PM skip removes only PM's discovery from the contention. A holder that is running a real task keeps the lock for the length of that task, and a warning that repeats across many firings points at that holder: the provenance in the `repo lock held by` line names it.

### When the wake's time budget stops a task

Three `[wake]` ceilings in `kanban.cfg` bound a dispatch: `max_task_seconds` per task, `max_project_seconds` per project and `max_runtime_seconds` per batch. The watchdog is armed with the smallest of the per-task limit and the two remaining budgets. A kill at the full `max_task_seconds` blocks the task as a `per_task_timeout`. A kill below it is a **budget stop**: the wake ran out of time, the task did not overrun, so the wake returns the task to `BACKLOG` with `Needs Human: no` and logs:

```text
task <TASK-ID>: stopped at the wake's remaining time budget (<N>s armed, below max_task_seconds=<M>s) -- returning it to BACKLOG; this is not a per-task timeout
```

The commits the stopped agent made are kept. The stop records resume mode `branch`, so the next dispatch attaches the task's worktree to its existing feature branch instead of re-creating the branch from the source branch. Where it records that depends on the task:

- **The task carries a `## Block Record`:** the record's `resume` field is set to `branch` and nothing else in it changes, so the requeue budget keeps counting. The OVERWATCH operator guide's "The Block Record" section carries the record's full lifecycle.
- **The task carries none:** the wake writes a `## Resume Mode` section to the task's `status.md` with the body `branch`. It creates no record, because a budget stop is not a block.

The `## Resume Mode` section:

- **Who writes it:** the wake, on a budget stop. No agent writes it, and the status-writer tool leaves it as it finds it.
- **Who reads it:** the wake's worktree stage, at the task's next dispatch, and only when the task has no Block Record. A body of `branch` (first word, any case) attaches the existing feature branch at its tip; no section, or any other body, starts fresh from the source branch. A `status.md` whose section the stage cannot read counts as `branch`, the mode that never deletes a branch. If the feature branch no longer exists, `branch` creates it fresh and deletes nothing.
- **A Block Record outranks it.** Once a block writes a record, the record's `resume` field decides and the section is ignored.
- **Inert once the task is `DONE`.** Nothing removes the section, and it is read only at dispatch, so one left in a finished task's `status.md` does nothing and needs no clean-up.

When the next dispatch attaches the branch this way, the wake logs:

```text
task <TASK-ID>: ## Resume Mode: branch (the wake's time budget stopped an earlier dispatch); an existing feature branch is attached unchanged
```

## Provider Switch

The kanban supports running its agent chain on more than one LLM provider against the same
task queues, on the same machine, without restart and without data migration. Claude and Codex
are first-class providers; Gemini is reserved as a future slot. Which provider actually does
work at any given moment is controlled by a single config value.

### The switch key

`kanban.cfg` `[providers] active` is the single source of truth for the active provider.
Valid values are `claude`, `codex`, and `gemini`. On a fresh install the key is seeded from
`[providers] default` (default `claude`); upgrades never overwrite an operator's existing
value, so the choice is preserved. The legacy `$KANBAN_ROOT/active-provider` file is retired
and is no longer read or written.

When the key is unset, empty, whitespace-only, or contains an unrecognized value, the
framework defaults to `claude` and logs a warning to stderr. Input is case-insensitive and
surrounding whitespace is stripped — `Codex` and ` CODEX ` both resolve to `codex`.

### Reading the current value

`read_active_provider <kanban_root>` in `team/scripts/lib/active_provider.sh` is the
canonical helper. Source it into any bash context that needs to know the active provider:

```bash
source "$KANBAN_ROOT/scripts/lib/active_provider.sh"
ACTIVE_PROVIDER="$(read_active_provider "$KANBAN_ROOT")"
```

The helper always returns a valid name (`claude`, `codex`, or `gemini`) and exits zero.
It performs a file read, a `tr -d '[:space:]'`, a `tr` to lowercase, and a `case`
statement — designed for sub-50-millisecond execution because every wake script calls it
on every cron tick.

`show-queues.sh` prints `Active provider: <name>` as its first output line so the operator
can verify the current setting at a glance without opening a file.

### Switching providers

The recommended path is the convenience wrapper:

```bash
$KANBAN_ROOT/scripts/switch-provider.sh --provider codex
```

The wrapper validates the provider name, refuses to switch to any provider whose CLI
binary is not in `PATH` (loud error, non-zero exit), rewrites the `[providers] active` key
in place (preserving comments and unrelated keys), and prints a confirmation showing the
old and new values along with the file it wrote to and the CLI binary it confirmed. The
change takes effect on the next cron tick, typically within 1–2 minutes.

Editing `kanban.cfg` by hand is also supported — set `[providers] active = codex` directly.
The hand-edit path has no CLI-presence guard, so use it when you have a specific reason to
bypass the wrapper (scripting, recovery, intentionally setting a provider with no installed
CLI to halt all work).

Switch back the same way:

```bash
$KANBAN_ROOT/scripts/switch-provider.sh --provider claude
# or edit kanban.cfg directly:
#   [providers]
#   active = claude
```

### Fast inactive-exit contract

Both provider dispatchers (`scripts/wake/claude.sh` and `scripts/wake/codex.sh`) read
`[providers] active` immediately after argument parsing, before sourcing any provider-specific
code, acquiring any flock, or reading any queue. The inactive script exits in under one second from cron-firing to process-exit.

This is a hard contract. The crontab installs entries for every provider on every tick;
the cheapness of the inactive-exit path is what keeps the unused entries from consuming
real machine resources. If a future provider adds a long initialization step before the
active-provider check, that contract has been broken and the change needs to be moved
later in the wake script.

Verifying the contract is straightforward when needed:

```bash
$KANBAN_ROOT/scripts/switch-provider.sh --provider claude
time $KANBAN_ROOT/scripts/wake/codex.sh --agent=pm   # should report < 1.0 second
```

### What happens to an in-flight task on switch

The wake scripts read `[providers] active` once per firing, not per task within a firing. A
task already in `WORKING` state at the moment the value is switched runs to completion under
the original provider; the switch takes effect on the next cron tick. Operators who need a
hard stop should set `HALT` first, wait for the chain to drain, then switch.

### Calibration is iterative, not gating

Role files are written in plain English and depend on the LLM's interpretation. Switching
providers may surface subtle behavioral differences — a role that ships cleanly under
Claude may produce slightly different output under Codex. Treat those differences as
follow-up work: file calibration bugs as they appear and fix them in later patch RCs that
adjust role-file prompts. A calibration difference is not a reason to hold back a provider
switch.

### Proxy compatibility: llm_thinking_enabled

Some deployment environments route provider traffic through older HTTP proxies that reject responses carrying extended-thinking (reasoning) blocks; the visible symptom is that every agent invocation fails outright. Set `[providers] llm_thinking_enabled = false` in `kanban.cfg` on such deployments — the framework then passes the provider's thinking-disable flag on each invocation (currently `--thinking disabled` on the Claude lane; the same key maps to the equivalent switch on other provider lanes as they mature). The default is `true` and modern deployments should not change it; leave the key absent for the default behavior.

## Reasoning-Effort Controls

Both first-class providers expose a reasoning-effort knob that trades tokens (and latency) for depth of deliberation. The kanban surfaces it through the standard config pattern: one global cfg key, per-role env overrides that mirror the model mechanism, and an absent-means-default contract. The controls are deliberately simple — there is no per-role/per-provider matrix in cfg and no automatic escalation ladder.

### The global key: `[providers] llm_effort`

`kanban.cfg` `[providers] llm_effort` sets the reasoning effort for every agent invocation. When set, the framework translates the value into each lane's native flag: the Claude lane passes `--effort <value>` and the Codex lane passes `model_reasoning_effort`. The key ships commented out in the example config.

The absent-means-default contract is the important part. When `llm_effort` is unset, empty, or commented out, the framework passes **no effort flag at all** — each provider falls through to its own vendor default, and behavior is byte-identical to a kanban that never knew the key existed. You lose nothing by leaving it alone.

**Valid values depend on the active provider.** Each provider declares its own list in its lib (`PROVIDER_EFFORT_VALUES` in `scripts/lib/provider_<name>.sh`), and the value is validated against the active provider's list at config load. Both first-class providers declare one:

| Provider | Accepted `llm_effort` values |
|----------|------------------------------|
| `claude` | `low`, `medium`, `high`, `xhigh`, `max` |
| `codex`  | `minimal`, `low`, `medium`, `high`, `xhigh` |

The lists differ: `minimal` is Codex-only and `max` is Claude-only. A value the active provider does not accept fails config load loud, with no silent fallback and before any provider CLI is invoked. The dual-channel refusal `config_loader.providers.invalid_effort` names the key that supplied the value (`[providers] llm_effort`, or the `PGAI_<ROLE>_EFFORT` variable below), the value, the provider and its valid list. Its remedy is runnable. For the cfg key it is a `sed` that rewrites `llm_effort` to a level the provider accepts. For the env variable it is `unset` of that variable, or an `export` of an accepted level. A typo therefore surfaces at once, instead of running a whole cycle at the wrong effort or failing at dispatch. After switching providers, check that the configured value is still on the new provider's list.

**A provider that declares no list is not validated.** When the active provider has no `scripts/lib/provider_<name>.sh`, or its lib has no `PROVIDER_EFFORT_VALUES` line (a test-only stub provider, for example), there is no list to check the value against, so config load does not refuse it. The value passes through unchanged and is exported as `PGAI_<ROLE>_EFFORT` like any other. Each resolution prints one line on stderr, so a single `llm_effort` value prints one line per role at config load:

```
config_loader: provider '<name>' declares no reasoning-effort levels (no PROVIDER_EFFORT_VALUES in provider_<name>.sh); <source> value '<value>' is passed through unvalidated.
```

`<source>` is `[providers] llm_effort`, or the `PGAI_<ROLE>_EFFORT` variable when an exported override supplied the value; the same pass-through applies to both. The refusal above happens only for a provider that declares a list.

### Per-role override: `PGAI_<ROLE>_EFFORT`

Effort is overridable per role through the `PGAI_<ROLE>_EFFORT` environment variable, resolved exactly like `PGAI_<ROLE>_MODEL`: same precedence tier, same narration line naming the winning source. **The env override beats the cfg key.** When `PGAI_WRITER_EFFORT` is set, it wins over `[providers] llm_effort` for the WRITER lane, and the wake narration records which source supplied the value in force.

This is the token-optimization lever. A role whose output does not benefit from deep deliberation — WRITER on a routine doc edit, say — can run at `low` while the rest of the chain stays at the global setting, trimming token spend without a global change. The effort in force is also recorded on the per-task cost line, so effort experiments are readable in the existing cost report (see "Tracking Token Costs").

### Vendor-default asymmetry

The providers do not default to the same effort, and the difference is large enough to matter when you compare them:

| Provider | Vendor default |
|----------|----------------|
| Anthropic (Claude) | high |
| OpenAI (Codex) | medium |

If you are benchmarking one provider against another — cost, latency, or output quality — set `llm_effort` explicitly on both lanes so the comparison is not silently confounded by mismatched defaults. Leaving the key absent means each lane runs at its own vendor default, which is correct for normal operation but not for an apples-to-apples comparison.

Supported effort values vary by provider, and within a provider by model. Config load enforces the provider's own list (above). A level on that list can still be one a particular model does not support, so check the target model's documentation before setting a value outside the common low/medium/high range.

### Deprecation caution: no legacy thinking params

Do **not** attempt to steer reasoning by passing legacy `budget_tokens`-style thinking parameters to current models. That older token-budget interface is deprecated; current models expect the effort control described here. Sending legacy thinking params to a current model is unsupported and will not do what you intend — use `llm_effort` (or `PGAI_<ROLE>_EFFORT`) and let the framework emit the correct flag for each lane.

## PM Mode Control

The autonomous scan described above can be toggled between two modes via the `PGAI_KANBAN_PM_MODE` environment variable.

### Modes

| Mode | Behavior |
|---|---|
| `automatic` | Default. When PM's backlog is empty, the wake script runs the autonomous scan — scanning `requirements/` and `requirements/priority/` for eligible briefs and creating PM self-tickets. This is the fully autonomous pipeline. |
| `manual` | The autonomous scan is disabled. PM still processes tickets already in `pm_backlog.md` normally — it just does not create new ones from requirements docs on its own. |

### Default behavior

When `PGAI_KANBAN_PM_MODE` is unset, the system defaults to `automatic`. This preserves autonomous behavior for installations that do not set the variable.

### How it works

The wake script checks PM mode after PM finishes processing its backlog. The actual guard is:

```bash
if [[ "$AGENT" == "pm" && "$STOP_REASON" == *"no more BACKLOG tasks"* && "${PGAI_KANBAN_PM_MODE:-automatic}" != "manual" ]]; then
```

When mode is `manual`, the wake script skips the autonomous scan entirely and exits cleanly. All other PM behavior — processing tickets from `pm_backlog.md`, pre-flight checks, decomposition — is unaffected.


### How to set it

Set `PGAI_KANBAN_PM_MODE` through any of the standard configuration channels:

- **`kanban.cfg`** `[chain] pm_mode` key (recommended for permanent settings; replaces the old `config.cfg` approach)
- **`env`** file in the kanban root (sourced by wake scripts; set `PGAI_KANBAN_PM_MODE=manual`)
- **`~/.bashrc`** or shell profile (user-wide)
- **Cron environment** (per-schedule control)

### Crontab convention

The recommended crontab installs entries for every provider listed in `kanban.cfg`
`[providers] available`. Claude and Codex are first-class providers; both fire on every
tick, and the one whose name does not match `[providers] active` exits in under one second.

The default pattern fires each agent every 2 minutes per provider with a sub-minute stagger
so agents in the same 2-minute window do not collide.  PM, CM, and TESTER fire on even
minutes; CODER and WRITER fire on odd minutes.  Operators can swap the even/odd assignment
if preferred.

```cron
PGAI_AGENT_KANBAN_ROOT_PATH=/home/<user>/pgai_agent_kanban
PATH=/path/to/claude:/path/to/codex:/usr/local/bin:/usr/bin:/bin

# --- Claude entries — even-minute agents (pm, cm, tester) ---
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/claude.sh --agent=pm     --sleep=0  >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-pm.log 2>&1
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/claude.sh --agent=cm     --sleep=21 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-cm.log 2>&1
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/claude.sh --agent=tester --sleep=42 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-tester.log 2>&1

# --- Claude entries — odd-minute agents (coder, writer) ---
1-59/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/claude.sh --agent=coder  --sleep=0  >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-coder.log 2>&1
1-59/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/claude.sh --agent=writer --sleep=21 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-writer.log 2>&1

# --- Codex entries — same schedule, parallel script ---
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/codex.sh --agent=pm     --sleep=0  >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-pm.log 2>&1
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/codex.sh --agent=cm     --sleep=21 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-cm.log 2>&1
*/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/codex.sh --agent=tester --sleep=42 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-tester.log 2>&1
1-59/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/codex.sh --agent=coder  --sleep=0  >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-coder.log 2>&1
1-59/2 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/codex.sh --agent=writer --sleep=21 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-writer.log 2>&1

# --- OVERWATCH Tier-1 sweep (hourly at :30) ---
# Deterministic bash sweep across every registered project. Zero LLM cost.
# The :30 minute slot puts the sweep off-beat from every 2-minute agent tick,
# so it never collides with an agent wake.
30 * * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/overwatch-sweep.sh >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-overwatch-sweep.log 2>&1

# --- OVERWATCH Tier-2 deep-clean (daily at 03:30) ---
# LLM-driven review of the action log and any sweep-flagged anomalies.
# The daily cadence keeps model cost bounded; the 03:30 slot avoids the
# hourly sweep at :30 by an hour and sits during a low-traffic window.
30 3 * * * $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake-batch.sh --agent=overwatch --sleep=0 --max-tasks=1 >> $PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-overwatch.log 2>&1
```

The overwatch lines are the two-tier cadence in cron form. Both are staggered off the every-2-minutes agent slots — the hourly sweep fires at the :30 mark (equidistant from :00 and :00+21s and :01+21s slots), the daily deep-clean fires at 03:30. Neither line touches the provider-dispatch pattern above: `overwatch-sweep.sh` is provider-independent bash, and the Tier-2 agent wake honors the standard `[providers] active` resolution just like the vertical agents. See `roles/OVERWATCH.md` for the tier semantics.

The on-BLOCK trigger (in the wake scripts' generic set-BLOCKED path) is the event-driven complement to these cron lines: fresh BLOCKED tasks nudge OVERWATCH within seconds rather than waiting for the next :30 tick. The trigger fires-and-forgets — the block path's exit status is unchanged whether or not the nudge lands, so a broken `wake-now.sh` cannot add a failure mode to the chain. Storms dedupe via the per-agent wake flock: many blocks in one minute produce one OVERWATCH run.

Both providers' entries fire on every tick. The script whose provider name does not match
`[providers] active` exits in under a second; the matching script proceeds to acquire the
flock and do real work. Only one provider does work at a time. See "Provider Switch" above.

The `--sleep=N` parameter delays the script N seconds after cron fires but before acquiring
the work-queue lock, creating sub-minute stagger without cron fractions.  The legacy
positional form (`wake/claude.sh pm`) still works but emits a deprecation warning; switch
to `--agent=` at your next crontab edit.

To install or update the crontab automatically, run `team/scripts/install-crontab.sh`.
That script reads `team/templates/install/crontab.example`, substitutes the live kanban root path,
and emits both the Claude and the Codex blocks.

While you are stabilizing a new install, you may also wish to include `PGAI_KANBAN_PM_MODE=manual` at
the top of the crontab to disable the autonomous scan.  Switch
to `automatic` (or simply remove the override) after three clean autonomous RCs ship without manual
intervention.

## Defensive install.sh Behavior

`install.sh` touches operator state outside `$KANBAN_ROOT` — the user's crontab and
per-provider role-file directories (`~/.claude/agents/`, `~/.codex/agents/`). Every such
write is prompt-protected and backed up. The script has no code path that silently
overwrites operator state. Both fresh installs and upgrades use the same code via the
`--upgrade` flag; the only operational difference is that upgrades skip interactive prompts
on subagent and role-file installs.

The prompt-and-backup contract described below protects against upgrades that silently
overwrite the operator's live crontab or role-file directories.

### Invocation modes

```
./install.sh                       # interactive install (prompts on every overwrite)
./install.sh --upgrade             # non-interactive overwrite of kanban tree and subagents
./install.sh --dry-run             # report what would happen, write nothing
./install.sh --force               # legacy: overwrite without prompts (kept for parity)
./install.sh --force-config-rewrite # rebuild config.cfg from template, preserve customizations
```

`--upgrade` keeps the same crontab prompts as the interactive install (crontab is operator
state that warrants confirmation even on upgrade). It does suppress prompts for subagent
files in `~/.claude/agents/` and role files in `~/.{provider}/agents/`, because those are
framework-shipped and an upgrade is expected to refresh them.

`--dry-run` reports every prompt and every write that would occur and exits without
modifying anything. Safe to run before any upgrade to preview the changes.

### Crontab handling

`install.sh` examines the operator's current crontab before doing anything and dispatches
on three states:

| Current state | Prompt | Default |
|---|---|---|
| No crontab present | "Install PGAI Kanban crontab now? [Y/n]" | Y (convenience for fresh installs) |
| Crontab with PGAI entries | "Replace with new template? [y/N]" | N (conservative; upgrade-in-place is opt-in) |
| Crontab with non-PGAI entries only | "REPLACE this crontab will discard existing entries. Continue? [y/N]" | N (conservative; foreign entries protected) |

Before any modification, the existing crontab is written to
`$HOME/.crontab.before-install-YYYYMMDD-HHMMSS.bak`. The backup is created **before** the
new crontab is installed, so a mid-flight failure leaves the original untouched and
recoverable via `crontab $HOME/.crontab.before-install-*.bak`.

After the install attempt completes, a sanity check runs: if the active crontab is empty
or absent, `install.sh` prints a loud warning and recovery instructions. An empty crontab
is the failure signature of the original incident, so it is now an explicit checked
condition.

When the operator declines a crontab prompt, `install.sh` prints the manual install command
and continues. The wake schedule simply will not fire until the operator installs it; no
silent partial install occurs.

### Role-file handling (multi-provider)

`install.sh` deploys `team/roles/*.md` to one directory per configured provider:

- `~/.claude/agents/` for Claude
- `~/.codex/agents/` for Codex

The provider list comes from `kanban.cfg`'s `[providers] available` key when present, with
a hardcoded fallback of `claude codex` until that key is universally present. Providers
whose CLI binary is missing on `PATH` are skipped with a log message — not silently ignored,
and not treated as an error.

For each provider, three checks happen per role file:

1. **Target does not exist** — prompt "Install `<target>`? [Y/n]", default Y.
2. **Target exists, identical to source** — silent no-op via `cmp -s` (no prompt).
3. **Target exists, differs from source** — show existing size and modification time,
   prompt "Overwrite `<target>`? [y/N]", default N.

When the operator agrees to overwrite, the existing file is copied to
`<target>.before-install-YYYYMMDD-HHMMSS.bak` (in the same directory as the target) before
the new file is written. Decline prints the manual `cp` command and skips that file; other
files and other providers continue to be processed independently.

If `~/.{provider}/agents/` does not exist on disk, `install.sh` first prompts before
creating it ("Create directory `<path>` for `<provider>` role files? [Y/n]"). Decline skips
the entire provider with a `mkdir -p` recovery instruction.

### Backup file naming and recovery

All backups are placed in predictable locations with timestamp-based names so operators can
find them with shell globs:

| Backup of | Path |
|---|---|
| Operator crontab | `$HOME/.crontab.before-install-YYYYMMDD-HHMMSS.bak` |
| Provider role file | `~/.{provider}/agents/<NAME>.md.before-install-YYYYMMDD-HHMMSS.bak` |

Examples of operator-side recovery:

```bash
# Find every backup install.sh has ever made for the crontab
ls -lt $HOME/.crontab.before-install-*.bak

# Restore the most recent crontab backup
crontab "$(ls -t $HOME/.crontab.before-install-*.bak | head -1)"

# Find every role-file backup for a given provider
ls -lt ~/.claude/agents/*.before-install-*.bak

# Restore a specific role file
cp ~/.claude/agents/WRITER.md.before-install-20260519-130050.bak \
   ~/.claude/agents/WRITER.md
```

Backups are never auto-purged by `install.sh`. They accumulate under `$HOME` and
`~/.{provider}/agents/`; the operator removes them when they are no longer wanted.

### Recovery when prompts are declined

Every "no" path prints the exact manual command the operator can run later to do what was
declined. Examples:

- Decline the crontab prompt → `crontab "$KANBAN_ROOT/templates/install/crontab.example"`
  (with placeholder paths to substitute) and a pointer to
  `$KANBAN_ROOT/scripts/install-crontab.sh`.
- Decline a role-file overwrite → `cp <source> <target>` for the specific file.
- Decline directory creation → `mkdir -p <path>` for the provider directory.

Declining a prompt is always safe: no partial state is left behind, no other prompts are
skipped, and the rest of the install continues. Operators can re-run `install.sh` at any
time; identical content is detected and silently skipped, so a re-run is idempotent for
files the operator already chose to install.

### Where the safety guarantees live

The prompt-and-backup primitives are implemented in `team/scripts/lib/safe_overwrite.sh`.
`install.sh` sources that library and dispatches to its functions:

- `safe_overwrite_file` — generic prompt-cmp-backup-copy for any file.
- `safe_overwrite_crontab` — three-state crontab handler.
- `backup_current_crontab` — pure backup helper used internally.
- `warn_if_empty_crontab` — post-install empty-crontab sanity check.

Other scripts that need to modify operator state outside `$KANBAN_ROOT` should source the
same library rather than reinventing the pattern. The library defines functions only and
has no top-level side effects, so it is safe to source repeatedly.

## VERSION vs VERSION_DETAIL — the deposit-time stamp split

At deposit time, `install.sh` and `upgrade.sh` stamp the deposit into two sibling files under `$KANBAN_ROOT`. `VERSION` is written from the sources listed below; `VERSION_DETAIL` is written only when the source tree is a git checkout:

- **`VERSION`** — the operator-facing stamp, taken from the first source that applies:
  1. **The source tree's committed `VERSION` file, copied verbatim.** The committed file is the identity; no `git describe` rewrite occurs. Its content need not match the tag name — for example, a tree tagged `ai_v1.19.0` whose committed file reads `v1.19.0` installs `v1.19.0`.
  2. **Fallback, used only when the tree has no committed `VERSION`** and is a git checkout: the clean tag portion of `git describe --tags` at the deposited HEAD, with the describe suffix (`-N-gSHA`) stripped. At a tag-exact checkout the strip is a no-op; past a tag (a polish commit ahead of the last tag), the suffix is removed. Example of this fallback case: a deposit from `ai_v1.19.0-1-gab12cd3` writes `ai_v1.19.0` to `VERSION`.
  3. With neither a committed `VERSION` nor a `.git`, `VERSION` is not written.

  Every operator-facing renderer — the tmux status-bar `📝` segment, the dashboard header, any `/status` field, the `GET /health` response — reads `VERSION` directly.
- **`VERSION_DETAIL`** — the forensics sibling. Contains the full describe string plus the deposit SHA on a single line (`<full-describe>  deposit-sha=<sha>`). It is **tool-owned**: written by the deposit path, never edited by operator surfaces, and **displayed nowhere by default**. It exists so that when debugging a deploy — "which exact tree is installed on this box?" — the forensic answer is one file read away without polluting the ambient display.

  `VERSION_DETAIL` is **written only when the source tree is a git checkout**, meaning its `.git` is a directory or the pointer file of a linked `git worktree`. Without git there is no describe string or deposit SHA to record. A normal deposit from a tree with no `.git`, such as a `git archive` export, therefore writes `VERSION` alone, copied from the tree's committed file (item 3 above covers a tree with neither). It also **removes** any `VERSION_DETAIL` an earlier deposit left under `$KANBAN_ROOT`, because a file left in place would name a deposit that is no longer installed. After such a deposit, `VERSION_DETAIL` is absent, exactly as after a `--stamp-version` override (below).

The split is implemented by a single shared helper, `team/scripts/lib/version_stamp.sh` (function `stamp_version_files`). `install.sh` and `upgrade.sh` both source it; there is one implementation of the stamp write, not two.

### Why the split exists

Operator-facing surfaces render what is installed, not deposit forensics. The old single-stamp behavior wrote the raw `git describe` output — for example `ai_v1.16.1-1-gb99f668` — to `VERSION`, so every surface that read `VERSION` carried the describe suffix even when the operator only wanted to see `ai_v1.16.1`. The suffix was noise for reading and useful for debugging, and the two audiences were served by the same file. The split lets each audience read the file that suits it: `VERSION` for operators, `VERSION_DETAIL` for forensic questions.

Zero renderer changes were needed. Every surface that already read `VERSION` renders clean because `VERSION` is now clean.

### The `--stamp-version` override

The `--stamp-version <value>` flag on `install.sh` and `upgrade.sh` replaces the deposit's own identity with an explicit value. When the operator supplies one:

- `VERSION` receives the override value verbatim (explicit values are clean by definition). Neither the committed `VERSION` file nor `git describe` is consulted.
- No new `VERSION_DETAIL` is written — the override carries no forensic suffix to record.
- A `VERSION_DETAIL` an earlier deposit left under `$KANBAN_ROOT` is **removed**, not kept beside the new `VERSION`: left in place, the forensic file would name a deposit that is no longer installed. After an override deposit, `VERSION_DETAIL` is absent.

### The staged-vs-published divergence advisory

The upgrade-time divergence advisory in `upgrade.sh` is **unchanged**. When deposit runs past a released tag (a polish commit ahead of the last shipped version), the advisory still prints — the moment git exactness is surfaced remains the deposit decision, which is when it matters. The stamp split preserves the advisory: `VERSION` renders clean everywhere, `VERSION_DETAIL` records the full describe permanently, and the one-time upgrade-time advisory tells the operator right then that the deposit is past the tag. No honesty regression — the forensic truth is captured, not suppressed.

## Bootstrap Paradox

When a new version of the system ships, the build that produces and validates that version is itself running on the prior version's infrastructure. This is the bootstrap paradox: the version being built cannot yet run the build that produces it.

### Why it happens

The wake script, subagent prompts, and scripts used to execute the RC build are the ones installed on the operator's machine at build time. If a release adds improvements to the wake scripts (`wake-batch.sh` and the provider dispatchers under `scripts/wake/`), those improvements are not in effect during that release's own build cycle — the build runs using the previously installed wake scripts.

The new code ships to `main` via CM-release. The operator must then run `upgrade.sh` or `install.sh` to deploy the new version. Only builds that start after the upgrade will benefit from the new code.

### Implications for bug fixes

A bug fix included in version N will not prevent that exact bug from occurring in the build that produces version N. The first fully clean build — where the fix is in effect throughout the entire pipeline — is the build for version N+1 (or later), after the operator installs version N.

This is expected behavior. Release notes for a given version may include a "bootstrap paradox" note when a fix that ships in that version was not in effect during its own build. This is not a defect — it is an inherent property of any self-hosting release pipeline.

### Example

Suppose version N ships a fix for duplicate PM materialization. The build that produces version N still runs the installed materializer, so it can create a second copy of its own task set. Mark those duplicates WONT-DO by hand. Builds that start after the operator installs version N run the fixed materializer and do not duplicate.

### What to do when the paradox occurs

When a build produces an artifact that the fix being shipped is meant to prevent:

1. Record the occurrence in the release notes under "Known Issues" with a bootstrap paradox note.
2. Clean up the affected artifacts (e.g., mark duplicate tasks WONT-DO) as a manual step.
3. Do not block the release. The fix will be in effect for the next build cycle.
4. The TESTER verification report should note the bootstrap paradox occurrence under "Autonomous operation criterion" if any manual cleanup was required.

## Multi-Project Path Forward

This section documents the multi-project model and the single-root layout it superseded. The single-root layout is preserved as a backward-compatibility fallback so older installs continue to operate without a forced migration.

### Single-root model (legacy fallback)

In the legacy single-root layout, the kanban system operates as a single flat tree rooted at:

```
${PGAI_AGENT_KANBAN_ROOT_PATH}
```

All work artifacts live directly under this root:

```
<KANBAN_ROOT>/
  tasks/
  queues/
  artifacts/
  requirements/
  bugs/
  scripts/
  team/
  roles/
  workflows/
```

There is one task namespace, one queue namespace, and one set of artifacts. All workstreams share them.

### Multi-project model (current)

The kanban tree contains a `projects/` directory that allows multiple independent projects to coexist under a single kanban installation:

```
<KANBAN_ROOT>/
  projects/
    <project-name>/
      tasks/
      queues/
      artifacts/
      requirements/
      bugs/
  scripts/
  team/
  roles/
  workflows/
```

Each project gets its own isolated subdirectory at:

```
<KANBAN_ROOT>/projects/<project-name>/
```

Per-project directories include: `tasks/`, `queues/`, `artifacts/`, `requirements/`, and `bugs/`.

Shared infrastructure remains at the kanban root level. This includes:

- `scripts/` -- wake scripts, PM agent, materializer, upgrade tooling
- `team/` -- governance documents (SOP-core.md, DIRECTIVES.md)
- `roles/` -- role definitions
- `workflows/` -- workflow definitions

Wake scripts, the PM agent, and the materializer are project-aware. They operate on a per-project basis, accepting a project name as context.

### Why This Matters

The single-namespace model creates contention when the kanban system is used for more than one workstream. Concrete problems:

- Queue contention: tasks from unrelated workstreams compete for the same queue slots.
- Version collision: release cycles for different deliverables (e.g., the kanban system itself vs. external project work) interfere with each other.
- Artifact confusion: outputs from different workstreams share the same `artifacts/` directory with no namespace boundary.

The multi-project model eliminates these problems by giving each workstream its own isolated task/queue/artifact space while preserving shared governance and tooling at the root.

## Instruction layers

Every agent wake reads four layers of instructions, top down. Each layer holds only what is true at its level and narrows the one above it. Of the four, the project README is the one you maintain. The kanban-wide files and role files come with each release, and the chain writes the task folders itself. The agents' own statement of this stack is `OVERVIEW.md`, "How to read the kanban's instruction stack"; this section is the operator's view of it.

| Order | Layer | Files (paths under the kanban root) | Where you change it |
|---|---|---|---|
| 1 | Kanban-wide | `DIRECTIVES.md`, `OVERVIEW.md`, `SOP-core.md`, `README.md` | By upgrade only. |
| 2 | Role | `roles/<ROLE>.md` — the role the task names | By upgrade only. |
| 3 | Project | `projects/<name>/README.md`, when the project has one | By hand, at any time. The next wake reads the new version. |
| 4 | Work | The task folder's `README.md` and `status.md`, plus the requirement, bug or priority file the task names | Through the chain. PM writes each task README when it decomposes a requirement, and the working agent keeps `status.md`. You steer this layer with intake documents (`HOW_TO.md` section 5). |

Each role's wake reads layers 1 and 2 as listed in `read-order/<role>.txt`. The wake then appends the project README, and the dispatch prompt points the agent at its task folder. That prompt is specific to one dispatch: it names the task's worktree, branch and paths, and those override the task README's own fields.

**Layers 1 and 2 belong to the release.** `upgrade.sh` lays down `DIRECTIVES.md`, `OVERVIEW.md`, `SOP-core.md`, `README.md` and `roles/` from the version you upgrade to. Don't edit these files by hand. Whenever an installed file differs from the release, `upgrade.sh` either overwrites it or skips updating it, so a local edit is either lost or leaves that file behind the release. A change to a generic rule is a change to the kanban's own source (`team/` in its repository), and it reaches your install through an upgrade.

**Layer 3 belongs to you.** `create-project.sh` seeds `projects/<name>/README.md` from `templates/project/README-TEMPLATE.md`. `add-project.sh` does the same when the file is absent. After that the file is never overwritten: upgrade protects everything under `projects/`, and its template refresh skips the project README. Put only this project's facts in it: test runners and the targeted-test command, lints and checks, coding standard, contracts, and git and push settings (`HOW_TO.md` section 2.2 walks through filling it in). Generic rules never go there.

**When layers conflict**, `SOP-core.md`, "Instruction Precedence", decides. `DIRECTIVES.md`, `OVERVIEW.md` and `SOP-core.md` outrank the role file, the role file outranks the project README, and the project README outranks the task README. A project README narrows what an agent does on that project; it cannot override the kanban-wide law above it.

## Bootstrapping a new project

The multi-project directory layout, `$PGAI_PROJECT_ROOT` resolution, per-project path table, and single-project shim are documented in `team/SOP-core.md` under "Projects Layout." This section covers the operator create-project flow and workflow-template dispatch.

New projects are added with `team/scripts/create-project.sh`. It bootstraps the full project skeleton, registers the project in `projects.cfg`, and writes a `project.cfg` rendered from the `project.cfg_example` template — every per-project key, each with its accepted values and default — with **permissive ceilings**: `max_patch=21`, `max_minor=13`, `max_major=0`. A freshly-created project is therefore **live** — the discovery pipeline can queue PM for any requirements doc within those ceilings on the next wake. `max_major=0` keeps major releases gated until the operator raises it.

### Canonical four-step flow

```bash
# 1. Bootstrap the project (skeleton, queues, templates, registration),
#    supplying the path-bearing fields at creation time.
team/scripts/create-project.sh --project <name> [--workflow-type <type>] \
  --dev-tree /absolute/path/to/local/clone \
  --git-repo git@github.com:<org>/<repo>.git

# 2. Fill in the project README (runners, lints, coding standard, contracts).
$EDITOR $PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/README.md

# 3. Release workflow only: create the chain's base branch (<prefix>main) once.
team/scripts/init-project-git-repo.sh --project <name>   # push_to_remote = true

# 4. Drop one or more requirements docs.
$EDITOR $PGAI_AGENT_KANBAN_ROOT_PATH/projects/<name>/requirements/v0.1.0.md
```

Nothing holds the chain back after step 1: the discovery pipeline scans the project's `bugs/`, `priority/`, and `requirements/` directories on every wake and begins decomposing anything within the ceilings. Finish steps 1–3 before step 4, because dropping a requirements doc is the act that starts work.

`HOW_TO.md` section 2.2 walks through filling in the project README in step 2. Step 3 follows `push_to_remote`. With the default `true`, the base branch must exist on origin, and `init-project-git-repo.sh` creates and pushes it. With `false`, create it locally in the dev tree instead (`git branch <prefix>main main`) — the init script always pushes.

### Defaults written by `create-project.sh`

| Field | Default | Notes |
|---|---|---|
| `project_name` | `--project <name>` | Validated `^[a-zA-Z][a-zA-Z0-9_-]*$`. |
| `workflow_type` | `release` | Any registered, ready workflow type (`HOW_TO.md` section 3). |
| `git_remote_name` | `origin` | The dev tree's remote that CM's release verbs, `init-project-git-repo.sh` and the push-lag checks fetch from and push to. Blank or absent means `origin`. |
| `dev_tree_path` | *(empty)* | Set with `--dev-tree`, or edit `project.cfg` later. |
| `git_repo_url` | *(empty)* | Set with `--git-repo`, or edit `project.cfg` later. |
| `branch_prefix` | `ai_` | No flag; edit `project.cfg` to clear or change it. |
| `push_to_remote` | `true` | No flag; edit `project.cfg` for a local-only project. |
| `max_patch` | `21` | Permissive. PM is queued for patch numbers up to 21. |
| `max_minor` | `13` | Permissive. PM is queued for minor numbers up to 13. |
| `max_major` | `0` | Operator gate. Raise to authorize major releases. |

### Override flags

Pass any subset alongside `--project <name>`; flags are independent. Apart from `--project` and `--help`, a flag not in this table is refused as unknown.

| Flag (alias) | Effect |
|---|---|
| `--workflow-type <type>` | Override `workflow_type`. |
| `--max-patch <N>` | Override `max_patch` (non-negative integer). |
| `--max-minor <N>` | Override `max_minor` (non-negative integer). |
| `--max-major <N>` | Override `max_major` (non-negative integer). |
| `--git-remote <name>` | Override `git_remote_name`. |
| `--dev-tree <path>` (`--dev-tree-path`) | Write `dev_tree_path`. |
| `--git-repo <url>` (`--git-repo-url`) | Write `git_repo_url`. |
| `--priority <int>` | Registry priority in `projects.cfg` (default: next available). |
| `--color '#RRGGBB'` | Registry display color (default: next unused palette entry). Quote it: an unquoted `#` starts a shell comment. |
| `--no-migrate` | Skip auto-migration of a colon-legacy `projects.cfg` to INI (not recommended). |
| `--dry-run` | Print the plan, do not write. |

### Why the defaults are permissive

A fresh project should demonstrate the chain immediately: drop a brief, and the next wakes decompose, build, verify, and ship it. The 21/13 patch and minor ceilings allow that out of the box, while `max_major=0` keeps a major release an explicit operator decision.

Ceilings are operator checkpoints, not an activation switch. To hold a new project back, pass a ceiling below the version of its first requirements doc — `--max-minor 0` keeps a `v0.1.0` requirement from being queued — and raise it later with `set-version-ceiling.sh` (below). "Version Ceilings" describes exactly what each field gates.

### Path fields

`dev_tree_path` and `git_repo_url` are **per-machine** by nature: the same project lives at a different path on a laptop, a VPS, or a CI host. Supply them with the flags at creation time, or leave them out and edit `project.cfg` before the chain's first release.

- A flag's value is written verbatim. The only check is that it is non-empty.
- A `--dev-tree` path that does not exist on this host produces a warning naming the path. The value is still written and the script exits 0, because projects are often registered before their dev tree is cloned.
- An omitted flag leaves its field empty.
- When a flag and its alias are both given, the alias wins.

### Inspecting and changing the ceiling later

```bash
# Show current ceilings.
team/scripts/set-version-ceiling.sh --project <name> --show

# Raise minor or major ceiling.
team/scripts/set-version-ceiling.sh --project <name> --minor <N>
team/scripts/set-version-ceiling.sh --project <name> --major <N>

# Lift a ceiling (no cap): writes max_minor = none / max_major = none.
team/scripts/set-version-ceiling.sh --project <name> --no-minor
team/scripts/set-version-ceiling.sh --project <name> --no-major
```

`set-version-ceiling.sh` edits `project.cfg` in place; all other fields are preserved verbatim. `--no-minor` and `--no-major` write the explicit spelling `none` rather than deleting the line, because a deleted line does not mean "no cap": an absent ceiling is the default (21 / 13 / 0), so deleting `max_major` restores the major-release gate instead of removing it. `--show` prints each value in effect: a number, the default marked `(default)` when the key is absent (`max_minor=13 (default)`), `none`, or a malformed value marked invalid.

### Registering an existing project

`create-project.sh` aborts if `projects/<name>/` already exists. To register a project directory that's already on disk but missing from `projects.cfg`, use `team/scripts/add-project.sh` instead. When that directory has no `project.cfg` (nor a legacy `PROJECT.cfg`), add-project writes one from the same template, says so in one line, and leaves `dev_tree_path` and `git_repo_url` for you to set; an existing `project.cfg` is never touched.

### Portable release hooks (`.pgai/hooks/`)

Release lifecycle hooks (pre-squash, pre-tag, post-tag) can live in three locations. The portable location — the one that travels with the managed repository and survives `remove-project` + `create-project` cycles — is:

```
<dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh
```

Drop an executable script at that path, commit it to the managed repo, and the release will resolve it automatically. No `project.cfg` edit is required for the in-repo tier to work. This is the recommended location for hooks that should always be present regardless of the kanban installation state (for example, a version-bump wrapper whose absence causes shipping drift).

The kanban's `create-project.sh` does not seed a `.pgai/hooks/` directory in the managed repo — the directory is opt-in per project and created only when the project actually needs a portable hook. Full behavior — the three-tier precedence order, the always-printed resolution line, the `cm_release_<phase>_hook_required` flag, and the hook environment contract — is documented in "Release Lifecycle Hooks" above.

### Workflow template dispatch

`create-project.sh` does not embed the project skeleton's templates inline. It reads them from per-workflow template directories under `templates/project/<workflow>/`. The value passed to `--workflow-type` (default `release`) selects the directory; everything copied into the new project — templates, queue files, README files — comes from that one directory.

This is what makes new workflow types additive: a new workflow is a new directory under `templates/project/`, not a code change.

#### Directory layout

Each `templates/project/<workflow>/` directory contains the same set of files. Their roles:

| File | Role |
|---|---|
| `REQUIREMENTS-TEMPLATE.md` | Copied to `requirements/templates/REQUIREMENTS-TEMPLATE.md` in the new project, and refreshed by every install and upgrade (see "Seeded intake templates on install and upgrade" below). Defines the requirements-doc schema operators fill in. The `## Workflow Type` field in this template is pre-filled for the workflow. |
| `BUG-TEMPLATE.md` | Copied to `bugs/templates/BUG-TEMPLATE.md`, and refreshed by every install and upgrade. Schema for bug reports. |
| `PRIORITY-TEMPLATE.md` | Copied to `priority/templates/PRIORITY-TEMPLATE.md`, and refreshed by every install and upgrade. Schema for priority overrides. |
| `queue-files.list` | Drives which `tasks/queues/*.md` queue files get seeded. Colon-separated: `<filename>:<title>:<description>`. Comment lines start with `#`. |
| `README-bugs.md` | Copied to `bugs/README.md` once, at creation; never refreshed. Workflow-flavored description of the bug intake. |
| `README-priority.md` | Copied to `priority/README.md` once, at creation; never refreshed. Workflow-flavored description of the priority intake. |
| `README-requirements.md` | Copied to `requirements/README.md` once, at creation; never refreshed. Workflow-flavored description of the requirements intake. |
| `BRIEF-EXAMPLE.md` *(optional)* | If present, copied to `brief-example.md` at the project root. Used by workflows whose primary input is a long-form brief (e.g., `document`). |

#### Dispatch and error handling

`create-project.sh` resolves the template directory as `templates/project/${WORKFLOW}/`. If that directory does not exist, the script exits with:

```
ERROR: unknown workflow type <name>; available types: <space-separated list of subdirectory names>
```

The available types are derived from whatever directories are present under `templates/project/` at runtime. There is no allow-list in the script.

#### Document project vs release project (out of the box)

Both workflows produce the same directory skeleton (`tasks/`, `bugs/`, `priority/`, `requirements/`, `artifacts/`, `release-notes/`, `logs/`) and the same `project.cfg` fields. The differences operators see immediately:

| Aspect | `release` project | `document` project |
|---|---|---|
| Queue files seeded | `coder_backlog.md`, `pm_backlog.md`, `writer_backlog.md`, `tester_backlog.md`, `cm_backlog.md`, `bug_backlog.md`, `priority_backlog.md`, `overwatch_backlog.md` | Same minus `coder_backlog.md` (no CODER pulls in a document chain); `overwatch_backlog.md` is seeded the same either way — OVERWATCH is horizontal, not tied to a workflow's agent roster |
| `REQUIREMENTS-TEMPLATE.md` `## Workflow Type` | `release` | `document` |
| `REQUIREMENTS-TEMPLATE.md` content focus | Source branch, test required, code-shipping fields | Audience, brief, outline, deliverables, voice-and-tone notes |
| `brief-example.md` at project root | not seeded | seeded — short, concrete example of the brief format WRITER expects |
| READMEs | describe the release pipeline (PM → CODER → TESTER → CM) | describe the document pipeline (PM → WRITER outline → WRITER drafts → WRITER integrate → WRITER polish → TESTER → CM) |

The `project.cfg [project] workflow_type` key is set to the value the operator passed, so downstream tooling (PM materializer, subagent dispatch) reads the correct workflow without further configuration.

#### Adding a new workflow type

Three steps:

1. Create `templates/project/<new-workflow>/` in the dev tree.
2. Populate the file set above (all required files; `BRIEF-EXAMPLE.md` if the workflow uses one). Reuse the generic `BUG-TEMPLATE.md` and `PRIORITY-TEMPLATE.md` from an existing workflow when you have no workflow-specific changes.
3. Run `install.sh` so the live install picks up the new directory.

No edits to `create-project.sh` are needed. After installation, `create-project.sh --project foo --workflow-type <new-workflow>` works immediately, and `--workflow-type bogus` continues to error with the updated list of available types.

`install.sh` copies the whole `team/templates/project/` tree into `$KANBAN_ROOT/templates/project/`, so templates added in the dev tree become available to operators on the next install.

#### Seeded intake templates on install and upgrade

**The current bug template lives in the installed tree**, at `$KANBAN_ROOT/templates/project/<workflow_type>/BUG-TEMPLATE.md`. That is `templates/project/release/` for a release project. Install and upgrade both deposit that directory from the release they lay down, so it always matches the release you are running. `PRIORITY-TEMPLATE.md` and `REQUIREMENTS-TEMPLATE.md` sit beside it. Their source in the dev tree is `team/templates/project/<workflow_type>/`.

**Each project's own copies are kept in step with it.** `upgrade.sh`, and `install.sh --force` over an existing install, refresh every registered project's three seeded intake templates after the new tree is deposited:

| Project copy | Refreshed from |
|---|---|
| `projects/<name>/bugs/templates/BUG-TEMPLATE.md` | `$KANBAN_ROOT/templates/project/<workflow_type>/BUG-TEMPLATE.md` |
| `projects/<name>/priority/templates/PRIORITY-TEMPLATE.md` | `$KANBAN_ROOT/templates/project/<workflow_type>/PRIORITY-TEMPLATE.md` |
| `projects/<name>/requirements/templates/REQUIREMENTS-TEMPLATE.md` | `$KANBAN_ROOT/templates/project/<workflow_type>/REQUIREMENTS-TEMPLATE.md` |

`<workflow_type>` is the project's `project.cfg` `[project] workflow_type`, `release` when unset. A workflow with no template directory of its own uses the `release` set, exactly as `create-project.sh` does. Filing from the project copy or from the installed template gives the same result once a refresh has run cleanly.

**What happens to each copy:**

- **Byte-identical to the installed template** — left alone; nothing is printed.
- **Missing** — seeded from the installed template.
- **Different, including a copy you edited** — the existing copy is kept beside it as `<file>.<UTC timestamp>.bak`, then overwritten with the installed template. One line names the project, the file and the backup.

**A differing copy is never skipped.** The refresh cannot tell an operator's edit from a copy seeded by an older release, and skipping would leave the stale copies the refresh exists to replace. A local customization therefore does not survive an upgrade in the live file; it survives in the backup. To keep it, re-apply it from the `.bak` after each upgrade, or propose it as a change to the shipped template so every project gets it. The `.bak` suffix keeps backups out of every `*.md` scan, so they are never read as bug, priority or requirements files. The refresh does not remove old backups; delete them when you no longer want them.

**Nothing else under `projects/` is written.** The README copies are seeded once at creation and never refreshed, and the rest of each project's state stays protected through an upgrade. Templates are read only from the installed tree, never from a dev-tree checkout, so a template change reaches projects only once it ships and is installed.

**Reading the output.** Every line the step prints starts with `Seeded templates:`. An example, for a project with an edited bug template and no requirements template (the project name and timestamp are illustrative):

```
Seeded templates: project 'demo': refreshed bugs/templates/BUG-TEMPLATE.md from the installed release template; the previous copy differed and is kept as bugs/templates/BUG-TEMPLATE.md.20261007T234701Z.bak
Seeded templates: project 'demo': seeded missing requirements/templates/REQUIREMENTS-TEMPLATE.md from the installed release template
Seeded templates: refreshed 1 file(s) (each backed up first) and seeded 1 across 1 registered project(s).
```

A run over copies that already match prints a single line saying nothing changed.

**A failure never stops the install or upgrade.** A project whose directory is missing, or a copy that cannot be written, is reported on stderr and skipped while the other projects still refresh. The installer then warns and prints the command to re-run once the cause is fixed:

<!-- doc-lint: skip — pp_run_ops.sh is a module runner that takes a module name, not a command that answers --help, and this command writes into a live kanban root's registered projects -->
```bash
bash "$KANBAN_ROOT/scripts/lib/pp_run_ops.sh" pgai_agent_kanban.ops \
    refresh_seeded_templates --kanban-root="$KANBAN_ROOT"
```

Add `--dry-run` to that command to list what would change without writing anything. `install.sh --dry-run` reports the step without running it.

## Version Ceilings

Version ceilings are operator-controlled gates that prevent the discovery pipeline from queuing PM for any target version that exceeds a configured limit. They are set in `project.cfg [versioning]` and enforced exclusively by the discovery pipeline before PM is invoked — once PM is running, the ceiling no longer applies (work is already validated and in flight).

### The three ceiling fields

| Field | Applies to | Default | Semantics |
|---|---|---|---|
| `max_major` | X in vX.Y.Z | `0` | PM will not be queued for any version where X > max_major. The default keeps every major release an operator decision. |
| `max_minor` | Y in vX.Y.Z | `13` | PM will not be queued for any version where Y > max_minor. |
| `max_patch` | Z in vX.Y.Z | `21` | PM will not be queued for any version where Z > max_patch. |

All three are **independent**: each caps its own component and nothing else.

**Every project has all three ceilings.** A field that is absent or empty — or a project with no `project.cfg` at all — takes the default above. Deleting a line therefore restores its default; it never removes the cap, so deleting `max_major` puts the major-release gate back at `0`.

**`none` is the only way to remove a cap.** Write `max_minor = none` (any case) for no minor ceiling; `set-version-ceiling.sh --no-minor` and `--no-major` write exactly that. `0` is a real ceiling, not "unset": `max_patch = 0` allows only vX.Y.0.

**A malformed value blocks rather than opens.** Any value that is neither a non-negative whole number nor `none` — `max_major = O` with a letter O, say — is never read as "no ceiling". Discovery and pm-agent print one line naming the file, the key and the value, treat the project as ceiling-blocked for that run and queue nothing for it; discovery also emits the dual-channel refusal `discovery.ceiling-unreadable`. Fix the value (`set-version-ceiling.sh --show` names it) and the next run proceeds.

### How the check works

Before queuing PM for a target version `vX.Y.Z`, discovery reads `max_major`, `max_minor`, and `max_patch` from `project.cfg [versioning]` and checks all three:

```
X <= max_major   (absent: 0;  none: no check)
Y <= max_minor   (absent: 13; none: no check)
Z <= max_patch   (absent: 21; none: no check)
```

If any check fails, PM is **not** queued, the requirements file is left untouched, and discovery emits a log line:

```
[<timestamp>] discovery: PM not queued for <version>: <component> version <value> exceeds <ceiling_name>=<ceiling_value>
```

Example rejection lines:

```
[2026-05-10T22:25:01Z] discovery: PM not queued for v0.22.0: minor version 22 exceeds max_minor=21
[2026-05-10T18:15:00Z] discovery: PM not queued for v1.0.0: major version 1 exceeds max_major=0
[2026-05-10T07:39:59Z] discovery: PM not queued for v0.21.42: patch version 42 exceeds max_patch=41
```

The rejection does not error out the discovery iteration. If a lower-versioned bundle exists within the ceiling, discovery picks it up in the same run. If no eligible work remains after ceiling checks, discovery exits idle.

### Operator workflow

**To gate minor releases** (stop the chain at the current minor, allow patches):

```ini
# In project.cfg [versioning]:
max_minor = 21   # only vX.21.Z and below are queued
```

**To gate patch releases** (stop at a specific patch, useful for time-boxed milestones):

```ini
# In project.cfg [versioning]:
max_patch = 5    # only vX.Y.Z where Z <= 5 are queued
```

**To raise a ceiling** (allow the next minor):

```bash
# Edit project.cfg [versioning] directly, or use set-version-ceiling.sh:
team/scripts/set-version-ceiling.sh --project <name> --minor 22
```

**To remove a ceiling entirely** (no cap):

```bash
team/scripts/set-version-ceiling.sh --project <name> --no-minor
# Or manually: set max_minor = none in project.cfg [versioning].
# Removing or commenting out the line restores the default (13), not "no cap".
```

**To inspect current ceilings:**

```bash
team/scripts/set-version-ceiling.sh --project <name> --show
```

The discovery pipeline also logs the ceilings in effect, defaults included and any `none` left out of the line, making it easy to confirm the operator's intent in the cron log. The line comes after the HALT, pending-PM-ticket and chain-busy guards, so it appears only on a wake that goes on to scan an idle chain.

**Before you raise a ceiling, find out what spent it.** A patch ceiling reached far sooner than the project's real output explains is usually a run of discovery-generated bundles that carry no resolvable work: consecutive patch versions, one dateline across the whole run, and a `## Bundled Items` section holding pipeline narration instead of bug or priority files. Each one claimed a version slot when discovery wrote it, and each reaching PM decomposes into an empty release. `retire-empty-bundles.sh` reports that class of file without writing anything —

```bash
team/scripts/retire-empty-bundles.sh --project <name>            # report only
team/scripts/retire-empty-bundles.sh --project <name> --apply    # set ## Status: done
```

— and retiring one sets its `## Status` to `done` rather than deleting it, which takes it out of PM's eligible set and frees the version line to move again. Raising the ceiling over an uncleared run only gives the empty bundles more room to consume. The three tells above — consecutive versions, one dateline, narration where item files should be — are what separate this run from ordinary backlog, and the script's `--help` carries the authoritative eligibility rule: a bundle is retirable only when its `## Status` is neither `done` nor `running` and no line under `## Bundled Items` resolves to a file on disk. Report first, read the list, then `--apply`.

---

## Pseudocron

Pseudocron is a minimal, foreground cron-like scheduler for environments where real cron is unavailable or prohibited. It is a Python 3 script at `team/scripts/pseudocron.py` that reads a schedule file and an environment file, then fires matching jobs once per minute in a clock-aligned loop.

### When to use pseudocron

Use pseudocron **only** when real cron is not available:

- Docker containers with restricted system access where `cron` is not installed.
- Sandboxed demo environments where the operator cannot modify crontab.
- Quick local testing without polluting the system crontab.
- Environments where policy prevents automated crontab writes ("the AI is not allowed to touch crontab").

**Real cron is preferred whenever it is available.** Do not use pseudocron on production hosts where cron works normally.

### When NOT to use pseudocron

- Any host with a working crontab — use `crontab -e` instead.
- Situations requiring log rotation, job supervision, restart-on-failure, or catch-up firing for missed minutes.
- Production deployments where reliability matters — pseudocron has no restart or supervision; if it crashes, jobs stop firing silently.

### Config and env file setup

Pseudocron reads two files from `$PGAI_AGENT_KANBAN_ROOT_PATH` at startup:

| File | Purpose | Required? |
|---|---|---|
| `pseudocron.cfg` | Job schedule (minute + command, one per line) | Yes — missing file is a hard error |
| `pseudocron.env` | Environment variables injected into child processes | No — missing file is logged and skipped |

**Setup steps:**

```bash
# Copy the example files to the kanban root.
cp team/scripts/pseudocron.cfg.example "$PGAI_AGENT_KANBAN_ROOT_PATH/pseudocron.cfg"
cp team/scripts/pseudocron.env.example "$PGAI_AGENT_KANBAN_ROOT_PATH/pseudocron.env"

# Edit the schedule.
$EDITOR "$PGAI_AGENT_KANBAN_ROOT_PATH/pseudocron.cfg"

# Edit the environment (add any vars your jobs need).
$EDITOR "$PGAI_AGENT_KANBAN_ROOT_PATH/pseudocron.env"
```

**`pseudocron.cfg` format** — one job per line:

```
# Lines beginning with '#' are comments.
# Blank lines are ignored.
# Format: <minute>  <command>
#   <minute> is a literal integer 0–59 (no *, */N, ranges, or lists).
#   <command> is run via bash -c; shell metacharacters work.

5   /home/<user>/pgai_agent_kanban/scripts/wake/claude.sh --agent=pm
6   /home/<user>/pgai_agent_kanban/scripts/wake/claude.sh --agent=coder
30  curl -fsS https://example.com/health >> /tmp/health.log 2>&1
```

**`pseudocron.env` format** — bash-style variable assignments:

```bash
# export NAME=VALUE or bare NAME=VALUE; both are accepted.
export PGAI_AGENT_KANBAN_ROOT_PATH=/home/<user>/pgai_agent_kanban
export PATH=/usr/local/bin:/usr/bin:/bin
export HOME=/home/<user>
```

### How to run pseudocron

**Foreground (tmux pane — recommended):**

```bash
python3 $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/pseudocron.py
```

**Background with log file:**

```bash
nohup python3 $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/pseudocron.py \
    >> /tmp/pseudocron.log 2>&1 &
echo "pseudocron PID: $!"
```

Pseudocron writes fired-job lines to stdout and startup/error messages to stderr. Redirect as needed. It writes no log files itself.

### How to stop pseudocron

Send SIGINT (Ctrl-C in the terminal) or SIGTERM. Pseudocron prints a shutdown notice to stderr and exits with status 0. Already-running child processes are **not** killed — they complete normally.

```bash
# Kill by PID (from the background example above).
kill <PID>

# Or, if you know the process name.
pkill -f pseudocron.py
```

### Limitations

Pseudocron is intentionally minimal. The following are **not implemented** and will not be added to v1:

- Log rotation or log file management.
- Daemonization (`--daemon` flag).
- Restart-on-failure or supervision.
- Special minute values: `*`, `*/N`, ranges (`1-5`), or comma lists (`1,5,10`).
- Multi-field schedules (hour, day-of-week, month) — minute-only.
- Job timeout or kill-after-N-seconds.
- Concurrency limits per job.
- Live config reload (SIGHUP).
- Catch-up firing for missed minutes (clock-jump-forward or system-resume).
- Email-on-failure.
- Per-job environment overrides.

For any of these requirements, use real cron or a proper job scheduler (e.g., systemd timers).
