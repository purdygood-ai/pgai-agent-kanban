# Shared Operating Procedure — Core

This is the shared law. Every role reads it whole on every wake; none of it is optional to any of them. It carries what no role can act correctly without: the operating doctrine, the task-state and queue vocabulary, and the boundaries on where a role may write. Procedure is not here — how a role does its own job lives in that role's file under `roles/`, stated once.

**Single canon:** a rule is stated in exactly one place. If it is here, role files reference it rather than restate it; if it is in a role file, this file does not summarize it. **Precedence:** this file does not override `DIRECTIVES.md` or `OVERVIEW.md`'s autonomy principle, and does override every role file, workspace README, and private note.

## The Loop

These are the kanban's first principles, read by every role on every wake.

- **The loop.** Human input goes in; verified output comes out. It turns on two signals: new human input, or a bug the verifier files against shipped work.
- **Bugs heal.** The next iteration is the default repair path — almost everything wrong with a release is fixed by the loop coming around again, not by stopping it.
- **Quality findings never stop the loop.** They carry zero risk of runtime failure; they are noted and carried into the next turn.
- **Blocking is the rare exception.** Reserved for work that moves the system backwards: destructive failures, unusable builds, or verification unable to run at all.
- **Ownership.** TESTER is the sole author of verification reports and of the bugs that record quality findings; CM notes those findings and ships.

## Purpose and Participants

Operate as a coordinated work system: clear ownership, safe behavior, traceable handoff, accurate task state, minimal ambiguity. Two participant swim lanes, **claude** and **human**; each task has exactly one active owner at a time. A participant updates state for tasks it owns, comments on related work, requests follow-up work, and hands off traceably — never silently assuming control of another's active work, which moves between participants only by reassigning the task folder to the new owner.

## Role Swim Lanes

Each role has a swim lane — a boundary limiting what it may do; cross-lane actions are process violations even when the agent is capable of them. Six vertical agents move work through the RC pipeline, each doing one thing only: **PO** expands a validated human brief into a requirements document under `requirements/` and queues a PM ticket; **PM** decomposes requirements documents into tickets; **CODER** implements; **WRITER** authors documents, role files and SOPs; **TESTER** observes and files bug reports as individual files in `bugs/`; **CM** ships releases, manages RC branches and owns origin. A seventh, OVERWATCH, observes the running system and never advances a release. What each role must NOT do is stated once, in that role's own file.

A role that discovers work in another lane files a handoff — bug report, ticket, or status note — and stops, trusting the next role with its part.

## Shared Team Root

The shared team tree is rooted at `${PGAI_AGENT_KANBAN_ROOT_PATH}`, default `$HOME/pgai_agent_kanban`; override it by setting the variable before running `install.sh`. Governance and tooling live at that root, per-project state under `projects/<project-name>/`.

## Projects Layout

`$PGAI_PROJECT_ROOT` resolves to the active project's root, `${PGAI_AGENT_KANBAN_ROOT_PATH}/projects/<project-name>/`, and every per-project path is relative to it: task folders at `tasks/<TASK-ID>/`, agent queues at `tasks/queues/<agent>_backlog.md`, artifacts at `artifacts/<name>/`, and `requirements/`, `priority/` intake, `bugs/` reports and `project.cfg` directly beneath.

In single-project mode `$PGAI_PROJECT_ROOT` defaults to `$PGAI_AGENT_KANBAN_ROOT_PATH`, so legacy paths still resolve. Project names must match `^[a-z0-9][a-z0-9-]*$`.

## Standard Task States

The only approved task states:

- `BACKLOG` — not started, ready to be pulled
- `WAITING` — pulled, but a prerequisite is not yet DONE or WONT-DO; auto-promoted back to BACKLOG when satisfied
- `WORKING` — actively being worked
- `BLOCKED` — waiting on dependency, approval, input, tool, or clarification; never cleared by the agent that set it. OVERWATCH's block triage may return a block it classifies as recoverable (a transient API error, for example) to BACKLOG; every other block is returned to BACKLOG only by HUMAN
- `DONE` — accepted and finished
- `WONT-DO` — intentionally declined or closed without completion

Use these exact names in `status.md`; do not invent alternates.

### Session start: you always arrive in WORKING

The wake driver sets `WORKING` (and the `[A]` marker) **before** it starts you, so every session — fresh or not — opens on `WORKING`. `WORKING` alone therefore says nothing about history. Treat the session as a **resume** only when there is evidence of earlier work: lines in `logs/progress.log` from a previous session, files in `artifacts/` you did not just create, or commits on the feature branch ahead of the source branch. With none of those it is a **fresh start**: replace any placeholder `## Summary`, `## Blockers` and `## Artifacts` left by the queue and begin. On a resume, git state outranks status prose: if the feature branch is already merged into the source branch, verify the work, skip the merge, and finish. Any state other than `WORKING` at session start means you should not have been invoked: record it in `## Summary`, do no work, exit.

### Writing task state

`status.md` is a machine-parsed surface. The canonical write path for a state change is the status-writer tool, not a hand-edit:

```bash
bash team/scripts/task-status.sh --project <name> --key <TASK-ID> --state <STATE> \
    [--needs-human yes|no] [--summary TEXT] [--blockers TEXT] \
    [--reason-code CODE [--action-args KEY=VALUE ...]]
```

In a live install the path is `scripts/task-status.sh`; either copy resolves the task folder from the kanban root, so it works from any working directory, worktrees included. It requires `--project`, `--key`, and `--state`, validates the state against the six names above, writes atomically, preserves every section it was not asked to change byte-for-byte, and refuses bad input dual-channel — `error`, `message`, `remedy` — writing nothing. It edits an existing `status.md`; it never creates a task folder and never invents a missing section.

**What it writes.** `## State` always, plus `## Needs Human`, `## Summary`, and `## Blockers` when their flags are given; omit a flag and that section is untouched. `--state BLOCKED` also writes the task's `## Block Record`, which OVERWATCH's block triage reads, and `--state DONE` or `WONT-DO` removes it. Everything else — `## Artifacts`, `## Next Recommended Step`, `## Instruction Conflicts`, `## Blocked By Agent`, `## Blocked Reason`, any role-specific section — is written directly. Record which agent and model you are as the first words of `## Summary` (`Agent: <role>, Model: <id>`); `## Model` is wake-stamped, so never edit it.

**Classify every block you set.** With `--state BLOCKED`, pass `--reason-code` from the closed agent list. `agent_operator_data_action` means the fix is one allowlisted operator data command, given as typed `--action-args` (today `verb=restamp-fixed-in project=<name> version=vX.Y.Z bugs=BUG-NNNN[,BUG-NNNN]`); OVERWATCH may run it and requeue you. Anything else is `agent_unclassified`, which always escalates to a human. Prose never substitutes for the typed arguments.

Hand-editing still parses, so older task folders and by-hand operator procedures are not broken; the tool is the taught path rather than a new gate, because it removes the reformatting failure a small model hits when it writes the state in a reasonable-but-nonstandard shape. **Every markdown `## State` block shown in a role file is reference output** — what the tool produces — never something to transcribe by hand.

### When a task cannot continue

When a task hits something it cannot resolve alone — a missing dependency, an unavailable credential, broken upstream code — set `BLOCKED` (with a reason code, above), set `Needs Human: yes`, record the exact reason in `## Blockers`, and stop.

**A silent, ambiguous or contradictory brief is not by itself a block.** One rule for every role except PO (whose job is to surface ambiguity before planning):
- **Silent** — the brief does not cover a choice: choose, state the choice in `## Summary`, ship.
- **Contradictory or unreachable** — two parts of the brief cannot both hold, or an acceptance criterion cannot be met within the files the task names: implement the reading that best serves the task's `## Goal`, meet the rest for the files in scope, record the conflict and your reading in `## Instruction Conflicts`, and finish `DONE`.
- **Block** only when no reading is safe — every option would destroy data, break a released contract, or act outside your lane.

**The tree wins over the README's facts.** A task README describes the code as PM last saw it. Where the merged source branch differs — a path moved, a function renamed, a count changed — work from the tree, and record the difference under `## Instruction Conflicts`.

**Work already delivered is `DONE`, not `WONT-DO`.** If earlier commits already satisfy every acceptance criterion and you confirmed it by running them, finish `DONE` and cite the satisfying commits in `## Summary`; no new commit is required. A *named* obstacle is what makes a BLOCK legitimate; `OVERVIEW.md` carries the binary terminal-state contract that says so. Never work around a block by modifying other tasks, queue files, or governance state outside the assigned task folder, and never simulate autonomy with undocumented state changes.

### WONT-DO authority

Any participant may set `WONT-DO`. State the exact reason in `status.md`, set `Needs Human: yes`, and do not treat it as closed until HUMAN confirms — HUMAN is the final authority on whether it stands.

### Prior artifacts are preserved, never deleted

On re-queue the wake script rotates agent-written files under `artifacts/` — `report.md` → `report.md.previous-RUN-N`, likewise `gaps.md` — so the incoming agent starts clean while every prior run survives as read-only history. Never delete a prior artifact by hand.

## Task Folder Source of Truth

Each assigned task folder is the source of truth for that task, and `status.md` holds its state. Shared workspace folders and private runtime areas are never the primary source of task status. A folder at `$PGAI_PROJECT_ROOT/tasks/<TASK-ID>/` holds `README.md` (the spec), `status.md` (the live progress), `artifacts/` (deliverables) and `logs/` (execution logs).

Task IDs use the format `<AGENT>-YYYYMMDD-NNN-slug`. The framework still parses the legacy `CLAUDE-`-prefixed form; no existing task folder is ever renamed.

## Per-Agent Queues and Marker Correctness

Each agent has its own backlog file at `tasks/queues/<agent>_backlog.md`; the wake script reads it to decide what to pull next. A task is registered in its own agent's backlog, never a shared catch-all, and a new agent type gets its file before any task is assigned to it. Queue files order work; they are not the source of truth for task details or state.

Each line is a Markdown list item — a leading `- ` (part of the shape, not decoration), the marker, the task ID, and an optional description. Hand-added entries use the same shape: `- [ ] TASK-ID — short description`.

| Marker | State | Written by |
|---|---|---|
| `[ ]` | BACKLOG | wake reconciliation |
| `[W]` | WAITING | the wake prerequisite check |
| `[A]` | WORKING | the wake driver, spawning the agent |
| `[B]` | BLOCKED | wake reconciliation from `## State`, or wake prep on a task rediscovered still WORKING |
| `[x]` | DONE or WONT-DO | wake reconciliation from `## State` |

**Agents never edit queue files** — not their own entry, not `bug_backlog.md`. The one exception is PO, whose procedure appends its PM ticket's line to `pm_backlog.md` (`roles/PO.md`, Step 7). Set `## State` with the status-writer tool and the wake renders the marker. `[ ]` is canonical; the alias `[]` is accepted by parsers, but the wake script always writes `[ ]`. Any other character inside the brackets is an error — PM and the dashboard scripts compute queue depth and progress from these.

**`status.md` is the single truth; the marker is derived.** A task's state is what its own `## State` says; the marker renders that state, written by the same transition and never chosen separately. Idle, discovery, and readiness decisions read task state, never a marker character. Before invoking an agent the wake script reconciles the two — a non-`BACKLOG` state corrects the marker and skips the task (`WORKING` corrects to `[B]` with a stuck-task log line), a `BACKLOG` task with unmet prerequisites becomes `WAITING` — and an end-of-cycle pass returns satisfied `[W]` entries to `BACKLOG`, which is how WAITING re-enters the queue without human intervention.

**Never hand-clear a marker.** A stale `[A]` is a diagnosis, not a state: it says an agent was started and should still be running, and one whose task has no live process means an interrupted wake, which the reconciliation pass heals by tearing down the abandoned worktree and feature branch and returning the task to BACKLOG. Clearing a live one hides a running agent. **Retrying is bounded** by `kanban.cfg` `[wake] zero_progress_cap` (default 3): a `WORKING` task with no forward motion resets to BACKLOG below the cap and goes `BLOCKED` with `Needs Human: yes` at it — what learned nothing in that many attempts is not healed by one more — and any cycle the task moves under its own power resets the count.

**Derived caches are not gates.** Whether the discovery pipeline considers a bug or priority file is that file's own `## Status` header, never its marker in `bug_backlog.md` or `priority_backlog.md`: `open` is eligible for re-bundling, `running` and `done` are skipped unconditionally. To revive a file, set `## Status` back to `open` — never hand-flip `[x]` to `[ ]`.

## Instruction Precedence

When instructions conflict, precedence runs highest to lowest: direct HUMAN instruction on the current task, `DIRECTIVES.md`, `OVERVIEW.md` (the autonomy principle and the binary terminal-state contract), this file, the **wake dispatch prompt**, the role file the task names, the project `README.md` (`projects/<name>/README.md`), the task folder `README.md`, and last the participant's private runtime notes.

Two refinements settle the conflicts agents meet most:
- **The dispatch prompt owns the mechanics of this dispatch** — your worktree, your branch, your task paths. Where it names an isolated worktree, that worktree is your working directory whatever the task README's `## Working Directory` says, and `## Git Repo: none` means *no remote*, not *no git*: a task with a `## Source Branch` and a dispatched worktree uses the full branch-and-merge law.
- **The task README owns scope** — which files, what output, which acceptance criteria — within everything above it. A role file's generic step that the task's scope makes impossible is recorded under `## Instruction Conflicts`, not obeyed into a contradiction.

Follow the higher-precedence instruction and report the conflict in `status.md`. Where layers conflict on *guidance* rather than rules, the more specific layer is usually right — but a layer telling you to stop and wait, defer to a human, or hand off without naming an obstacle is wrong: it contradicts `OVERVIEW.md`.

## Required Read Order For Assigned Work

`OVERVIEW.md` states the four layers; the order an agent doing assigned work reads them in is: `DIRECTIVES.md`, `OVERVIEW.md`, `SOP-core.md`, then `README.md` — all four under `${PGAI_AGENT_KANBAN_ROOT_PATH}` — then the role file the task names (`roles/<ROLE>.md`), then the project's `${PGAI_PROJECT_ROOT}/README.md` when present, then the task folder's `README.md` and `status.md` and the requirement, bug or priority file the task names, then any task-referenced repository or worktree `README.md`, and private runtime notes only as needed. Project-specific rules live only in the project README, never in this file or a role file.

Do not reread unrelated documentation without reason. What a role's wake assembles is declared in `read-order/<role>.txt` — adding a document is an explicit manifest edit, not an assumption.

## Write Boundary

The shared team tree is read-only by default **except for assigned task folders** under `$PGAI_PROJECT_ROOT/tasks/...`. Inside one, update `status.md`, put deliverables in `artifacts/` and logs in `logs/`, and edit task-local files the task requires.

Do not write elsewhere in the shared tree unless HUMAN assigns documentation maintenance work. Source-tree edits belong in the task's working directory — a git worktree — never the live install, whose contents `install.sh` replaces wholesale.

**Your subject is the git repository, never the live install.** The files under `$PGAI_AGENT_KANBAN_ROOT_PATH` — the governance files, `roles/`, `scripts/`, and every `projects/<name>/` file, the project README included — are your instructions and the running kanban, not the subject of your task. The live install can be a release behind or ahead of the branch you work on, so a difference between the two is expected and is never a finding, a bug or an operator note. Compare, verify, review and file bugs only against the branch in your working directory. Reading kanban process state (task folders, queues, ledgers, `release-state.md`) through the scripts that own it is unchanged.

**Role and governance files take fact fixes only.** A ticket may correct a fact in a role file, `SOP-core.md`, `DIRECTIVES.md` or `OVERVIEW.md` — a count, a path, a flag, what a script does — so it matches the code it describes. It never changes a procedure, a precedence or a rule there; those change only through an operator patch. A ticket that seems to need one does the rest of its work and names the change it needs in its `## Summary`.

## Branch and Merge Law

Task agents share one branch discipline, whatever their role. Work happens on a feature branch named for the task, branched from the source branch the task README names — during a release cycle that is the RC branch, never the prefixed main branch.

- Completed work merges back with `git merge --no-ff`. The merge commit is how the kanban records that a task's work landed; without it the audit trail breaks.
- Agents must not merge an RC branch into the prefixed main branch or create RC branches without explicit task assignment. Those are CM actions.
- Never `git stash`. Work parked in a stash is invisible to anyone reading the repo.
- Never auto-resolve a merge conflict. Set `BLOCKED` with the conflicting file paths named, leave the feature branch in place for inspection, and exit.

DONE means the work is merged into the local source branch. Commits on an unmerged feature branch are not delivered; neither are uncommitted changes.

- **Branch names carry the project's prefix.** The main branch is `main`; a project's `branch_prefix` (`project.cfg`) goes in front of its main branch, its RC branches and its tags: with `ai_` that is `ai_main`, `ai_rc/vX.Y.Z`, `ai_vX.Y.Z`; with no prefix, `main`, `rc/vX.Y.Z`, `vX.Y.Z`. Prose that says `rc/vX.Y.Z` or `vX.Y.Z` means the prefixed form; `## Source Branch` and the dispatch prompt name the real branch.
- **Delete the merged feature branch in-session.** After the `--no-ff` merge, delete the feature branch with `git branch -d` and leave the worktree on the source branch before you set `DONE`. The completion gate runs after you exit and accepts a deleted ref when the worktree HEAD is the source branch and a merge commit names the feature branch. Do not detach HEAD.

## Project Git Settings

Each project's `project.cfg [project]` describes its git topology in four keys:

| Key | Meaning |
|---|---|
| `dev_tree_path` | the local git repository — every project has one; task worktrees are made from it |
| `git_remote_name` | the remote's name (normally `origin`) |
| `git_repo_url` | the remote's URL |
| `push_to_remote` | `true`: CM's release pushes main and tags to the remote. `false`: releases stay local and the operator publishes by hand |

Under `push_to_remote = false` the two remote keys are not used and may be empty. **Neither an empty `git_repo_url` nor a task README's `## Git Repo: none` ever means "no git"** — the local repository and the full branch-and-merge law still apply; they only mean no agent talks to a remote. A project may well have a remote and still run `false` — the kanban does, keeping its release cycle private and publishing public releases manually.

## Git Repositories: Roles and Boundaries

Two kinds of git repository exist here; violating either boundary is a defect, not a style choice.

**The remote (origin)** is never assumed reachable. CM is the ONLY toucher of any remote: no other agent pushes, pulls, or fetches, and no agent writes code that requires origin at runtime. A best-effort `git fetch` must degrade cleanly when origin is absent.

That boundary is guarded, not merely asserted: every role but CM runs behind a wrapper ahead of `git` on `PATH` that refuses `fetch`, `pull` and `push` dual-channel, recording each attempt in the task's own `logs/git-boundary-guard.jsonl`. A refusal ends the command, not the session — note the need in `## Summary` for CM and finish the task. The guard's own header at `team/scripts/lib/git_boundary_guard.sh` carries the rest in full: the decision table, how global options are read past, the record's fields, and where it fails open.

**The local repository** — a managed project's dev tree — is where code is written during the release lifecycle and the source of record for its history. It is NOT a runtime dependency:

- **Live running code must never import, source, or resolve anything from a repository checkout.** The live install is self-contained: its scripts source its own libs, its python imports its own package, and a missing copy FAILS LOUD naming its own path. A repository is never a backup, a fallback, or a shadow copy for running code.
- **A checkout is data, not code, to everything outside it.** The release machinery operates ON dev trees; nothing operates FROM them except the task inside its own isolated worktree.
- **The litmus test:** checking out a different branch in any dev tree must change NOTHING about live behavior.

Corollary: `sys.path`, `PYTHONPATH`, and `source` targets in live-runtime code contain live-install paths only; a "dev-tree fallback" inverts silently, coupling live behavior to checkout state.

## Path Canonicalization Rule

Three path contexts, each with one canonical source; mixing them makes an agent read stale state or write to the wrong place.

- **Kanban operations** — task folders, queues, status files, `release-state.md`, HALT flags — resolve through `$PGAI_AGENT_KANBAN_ROOT_PATH`. Never hardcode `~/pgai_agent_kanban`.
- **Git reads** of version-controlled governance, scripts and tests use an explicit ref (`git show <branch>:<path>`), not the current checkout. "What version are we at" is `pp_last_released_version "<project>"` — never a parsed file, never `git tag` inline.
- **The dev tree** for a task is the isolated worktree the dispatch prompt names (the task README's `## Working Directory` only when no worktree was dispatched), a checked-out worktree holding code during active work. It is not authoritative for in-flight RC state; that is CM's `release-state.md`, below.

## Release State Fields

`$PGAI_PROJECT_ROOT/release-state.md` is CM's file — per-install, never version-controlled. Four fields: `## Active RC` and `## Last Released` hold `none` or `vX.Y.Z`, `## RC Opened At` holds `none` or ISO-8601 `YYYY-MM-DDThh:mm:ss` optionally suffixed `+HH:MM`, `-HH:MM` or `Z`, and `## RC Opened By Task` holds `none` or non-empty text. The first three headings are required; `## Last Released` is optional and its absence reads as `none`. Which CM verb writes each is `roles/CM.md`.

A loader validates every field on read and refuses loud rather than guess: `none` is a value, not an absence, and case-sensitive — `NONE` is refused, naming what it received, the forms it accepts, and the edit that fixes it — and a missing required heading and a present one with no value are distinct faults, never folded into `none`. `## Last Released` records what shipped for the drain to read, not the general version answer above.

## Gates and Refusals

Every gate, guard, and refusal is inventoried in `docs/blocking-site-census.md` under exactly one class:

- **BLOCK-WORTHY** — the refusal catches backward motion: a destructive or state-corrupting failure, an unusable build, verification unable to run, or an approval/invariant by design. A real stop.
- **CONVERT** — a quality-class finding carrying zero runtime-failure risk; it should warn, file a bug, and continue.
- **AGENT-RESOLVABLE** — fatal to the current done-claim but self-serviceable by the agent in the same session.

**Completion-gate refusals are self-service, never a block.** The CODER/WRITER completion gate is AGENT-RESOLVABLE: on a refused done-claim the agent fixes the cause in the same session and re-completes — an unmerged feature branch gets merged into the source branch, a dirty worktree committed or discarded. An agent chore: do not set `BLOCKED` or `Needs Human: yes`, do not defer it. This applies ONLY to this gate; squash conflicts, fidelity-gate divergence, and HALT flags remain real stops no agent may paper over.

**An unusable build blocks; an unavailable build environment does not.** A lane that builds an artefact has three outcomes with different verdicts. *Built* — record the pass. *Failed to build* — a tree that cannot produce its own artefact is BLOCK-WORTHY; tagging over it publishes something that does not work. *Could not be attempted* — no engine, an unreachable registry, missing credentials; nothing was exercised, so record a finding naming the unavailable resource and do not block. The boundary is evidence, not inference: claim "failed to build" only where the environment can be shown working when it failed, and a bare non-zero exit is a defect in the lane rather than grounds to block the tree.

**Authoring a new gate.** Every new refusal site states its class and emits the dual-channel error contract (a stable machine `error` key, a plain-language `message`, a runnable `remedy`). A project that keeps a census of its refusal sites (the kanban does) states its anchor and index rules in its project README.

## Blocking Severity: only real damage blocks

A check may fail a gated runner, or contribute to `systemic_risk: high`, only when it evidences real damage — the BLOCK-WORTHY test above, applied to checks. Soft estimates, style, size heuristics and advisory thresholds meet none of it and are CONVERT by their nature: an over-threshold reading is a prompt about weight, not evidence anything is broken, so such a check narrates one line, files its finding, and lets the runner exit 0. Enforcement class is therefore part of a check's specification and not a wiring detail — the requirement that authorizes a check says whether it blocks or narrates, and no implementation may escalate that. A TESTER verifying a new check confirms its class against the requirement that authorized it; a hard gate no requirement authorized is itself a bug, filed against the gate rather than worked around by tuning the value it measured.

## Rating a Finding

A severity rating is an input the release decision computes from, so two verifiers reading the same finding must reach the same rating. Rate what the defect does to a running system:

- **critical** — blocks the build: the tree cannot produce its own artefact, or verification cannot run at all.
- **high** — breaks functionality: something that should work does not, and there is no workaround.
- **medium** — incorrect, but a workaround exists or nothing depends on the broken path yet.
- **low** — cosmetic or minor.

This rubric rates a finding that exists; whether a check was entitled to block at all is "Blocking Severity: only real damage blocks" above.

Severity says how bad a finding is; two further fields, read only on a finding whose own `Systemic Risk` is `high`, say what shipping it costs. **Regression** is `yes` when this RC's own work introduced it, `no` when it predates this RC. **Integrity** is `breakage` or `destructive` for a suite breakage or a destructive-path failure, `none` for neither. An absent or unrecognized value in either reads as `unknown`. Which combinations ship and which stop is `roles/CM.md`; the report fields are `roles/TESTER.md`.

**Calibration, at the two boundaries that get argued.** A real but unexercised defect in a shipped artefact is **medium**, not `high`: nothing is failing today and not using it is itself the workaround — and it predates the RC (`Regression: no`, `Integrity: none`), so it ships as a known issue and carries into the next iteration. A command that worked in the last release and exits non-zero in this one is **high**, not `medium`: "the operator can do it by hand" is a recovery, not a workaround — and the previous release proves this RC introduced it (`Regression: yes`), which stops the release in that project's lane until it is fixed or an operator overrides on the record.

**A rating is never softened to reach a preferred ship outcome.** Lowering `high` to `medium`, or calling this RC's own regression pre-existing, because the honest rating would stop the line corrupts the one input the decision is computed from — invisibly, since the report still reads as honest. When the stop looks disproportionate, argue that in the report's prose where a reader can weigh it; the remedies are an operator override on the record or a ticket against this rubric, never a quieter number.

## HALT Flags

`HALT` flag files pause the chain. Every role honors them; **only the operator ever removes one**.

- **Presence check.** Wake scripts check at the start of each cycle, before task selection. A present flag is logged and the script exits cleanly — status 0, not an error.
- **In-flight work is untouched.** A WORKING task stays WORKING; the running agent is not interrupted. HALT changes no task states. A HALT that appears after you were dispatched does not stop you: finish the task and exit normally. A role's own "no HALT" prerequisite is checked once, when the task starts.
- **Two scopes.** A verdict about one project — every CM ship-policy and release-mechanics halt, every OVERWATCH repair — goes to that project's own `projects/<name>/HALT` and stops that lane alone. Parking a one-project fault at the root freezes sibling lanes for a fault they do not have.
- **The fleet-scoped file has exactly three writers**, each an operator stopping every project at once: the operator at the kanban root (by hand, the global halt command, or the dashboard's global-halt action); the global halt and unhalt operations those entry points call and nothing else calls; and `HALT-AFTER` auto-promotion of a sentinel armed at the root. CM's autonomous triggers and OVERWATCH's repairs never write there — each names one project — and everything else that creates a HALT file writes at project scope.

`HALT-AFTER` is a soft variant: the chain keeps running until a named event drains, then it promotes itself to `HALT`. It does not gate wakes while draining, so a role that sees one keeps working. **Conversion happens only at RC close** — no token promotes while `## Active RC` is anything but `none`; a drain satisfied mid-RC is latched and converts once the RC closes, so a release in flight always finishes rather than freezing half-built. Tokens, drain semantics, that invariant in full, and CM's ship-time spend of a version-pinned sentinel live in `roles/CM.md`.

A third signal, `SUBSTRATE_BROKEN`, means the wake substrate itself — config loader, queue parser, agent dispatch, lockfile invariants — found an inconsistency it cannot safely work around. Pause rather than advance the chain or mutate task state: unstable substrate corrupts queues, lockfiles, and task folders undetectably. Treat it as a blocking pre-flight failure and exit.

## Deferral Posture

Low bugs are real, but must not set the tempo. **A deferred bug never triggers an RC** — it waits while the chain works requirements; a bug ABOVE the level opens a bugfix RC as always.

Four `[release]` keys configure it, in `kanban.cfg` or a project's `project.cfg`. Booleans are `true`/`false` only; `on`/`off` refuse loud with the accepted set.

| Key | Accepted | Default | Effect |
|---|---|---|---|
| `deferred_bugs` | `true`\|`false` | `false` | Master switch: whether bugs are deferred at all. |
| `deferred_bug_level` | `low`\|`medium`\|`high` | `low` | Defers every bug at or below this severity. At `high` only a `critical` bug still triggers an RC; every settable rung waits for that trigger or for deferral to lift — the operator-knows-best case, not a warning. |
| `defer_piggyback` | `true`\|`false` | `true` | When a bug above the level triggers an RC, carry every deferred bug onto it; `false` sends the trigger alone. |
| `deep_verify` | `weekly`\|`off` (aliases `true`\|`false`) | `weekly` | The DEEP-VERIFY cadence — a mode, not a boolean, so it stands outside the strictness rule above; every reader accepts all four spellings. |

**Two vocabularies, deliberately different widths.** A bug's own `**Severity:**` field is ranked on `low < medium < high < critical` (matched case-insensitively, tolerant of surrounding whitespace and one trailing period). `deferred_bug_level` accepts `low|medium|high` and nothing else: `critical` — the build-blocking rung "Rating a Finding" defines — is not a settable level, and both the config loader and the posture resolver refuse it dual-channel naming the accepted set. The gap is the mechanism, not an oversight: a level names the highest rung the chain may be told to wait on, no accepted value outranks `critical`, so a critical bug is kept at every setting rather than deferred by any of them. A severity outside those four words, or absent, is neither ranked nor deferred — it resolves to `unparseable`, stays a candidate that can trigger an RC, and is narrated naming the file, so a typo parks nothing silently.

**Precedence, highest first: campaign file, then `project.cfg`, then `kanban.cfg`, then the built-in default.** `defer_piggyback` is a bare boolean and stays one — "triggered" means "above the level" by construction, so there is no second threshold; a carry narrates its count and trigger and marks each carried item on its own bundle line, and the consolidation cap is `roles/PM.md`.

The `DEFER-LOW-BUGS` campaign file is project-scoped over root-scoped, as HALT is. Armed, it forces `deferred_bugs=true` and `deferred_bug_level=low`, leaves `defer_piggyback` to the tiers below, and names the release it runs until; CM removes it when that or a later version ships. Unparseable, it refuses loud, never silently off. **The hygiene sweep** is the only way a deferred bug bundles on its own: it fires when every unhandled bug is deferred and the requirements queue holds nothing unprocessed.

## Temporary File Convention

All framework subsystems write temporary files under one configurable directory instead of scattering them across `/tmp`: `PGAI_AGENT_KANBAN_TEMP_DIR`, default `/tmp/pgai_kanban_tmp`. Wake scripts export the resolved value so every subagent inherits it, and the dispatch prompt names each agent's scratch subtree. Write scratch and diagnostic output there; never create bare `/tmp` entries. Bash callers source `scripts/lib/temp.sh` and its `pgai_mktemp` family; Python honours the env var when set and falls back to the system temp directory. Leave your scratch in your subtree when you finish; do not spend effort deleting it.

Enforcement is report-only: the wake bracket appends a `## Temp Litter` section to `status.md` naming any fresh entry outside the framework root, and deletes nothing. Litter is a hygiene report, not a failure.

## Lock Provenance

The kanban's advisory locks record who took them (`holder`, `pid`, `op`, `task`). Never delete or hand-edit a lock file; a lock left by a dead process never blocks reacquisition. The rules for code that takes locks are in the kanban's project README.

## Activity Log (Directive 3)

Host-level software changes — system packages, global tools, services, anything persisting beyond a single task — append one line to `$PGAI_AGENT_KANBAN_ROOT_PATH/logs/activity.log`: ISO timestamp, action, command, reason. Ephemeral per-task venvs and pip installs inside a worktree are exempt — workspace, not host. The log is append-only.

## Documentation Discipline Rule

Every ticket that adds or changes a tunable, environment variable, configuration value, script behavior, or team convention updates the matching documentation artifact in the same commit. Documentation is not a follow-up — it is part of done.

The matching artifact is the `*_example` / `.env.example` file for an environment variable or tunable, the example configuration file for a configuration value or block, that script's README or usage comment for script behavior, and this file or the owning role file for a team convention or operating rule. A ticket must not be marked `DONE` until every applicable update is included. CODER puts them in the same feature branch as the code — example files, usage comments, `--help` text, generated blocks and index lines a lint keys on; a standalone prose document the change makes stale goes to a paired WRITER ticket in the same release, which PM splits out. TESTER records a missing example-file update as a gap; PM writes the requirement into acceptance criteria whenever scope touches one.

## Scope: what you may fix next to your task

Change what the task names. Two adjacent fixes are always in scope: an **identical defect in a byte-duplicate sibling** of the code you are changing, and **a sentence in a file you are editing that your own change makes false**. Anything else you notice — a different defect, stale text elsewhere — goes in `## Summary` for TESTER to file; do not fix it unasked.

## Resource safety

The host is shared and CPU-capped. Never generate synthetic load — CPU spinners, busy loops, fork loops, `yes`, stress tools, many parallel suite runs — to reproduce a race, prove a fix, or measure a timeout. Prove timing-sensitive behavior with assertions on behavior, progress or CPU time, and run test suites only within the configured worker budget. The container tier (image builds, base-image pulls, containers) is operator-only: inside a wake its harnesses print `SKIPPED` and exit 0; note the coverage need in `## Summary` instead.

## Naming: describe behavior, not scaffolding or provenance

This governs every name an agent authors — functions, scripts, variables, classes, headings, tests — in every project the framework manages.

**Name it for what it does or represents, not for the scaffolding or the history that produced it.** A name should read clean to someone who never saw the design discussion or the incident behind the work. That rules out *scaffolding names*, encoding an internal sequence, phase, or code-path label instead of meaning (`phase2_handler`, `pathB_check`), and *provenance names*, encoding why the work happened (`fix_bug_0382`) and dangling once that provenance is gone.

It extends to docstrings, comments, log lines, output strings, and shipped prose: describe BEHAVIOR, never process history, and let no internal bug identifier, task ID, RC, or framework-version citation reach a shipped artifact — git history holds the "why", the artifact the meaning.

**Only identifiers count.** The rule is about IDs: a bug, priority, task or requirement ID, an RC, or a version cited as history. Plain wording such as "the fix", "this ticket" or "the task's acceptance criteria" is not a violation and is never filed as a bug.

Three sanctioned exceptions: usage EXAMPLES where a bug- or version-shaped VALUE illustrates an interface; a skip annotation citing an OPEN follow-up bug, removed when it closes; and EXTERNAL constraints — an upstream issue, a CVE, an RFC — documenting behavior the code must honor.

**Docs are short.** Write what a reader needs to use or change the thing: what it does, how to run it, its defaults and limits. No history, no rationale essays, and no restating what another doc already says — link to it. When in doubt, cut.

**A fact is fixed everywhere it is stated.** When a change corrects or alters a fact — a flag, a default, a path, what a command does — search for every place that states it (help text, docs, READMEs, comments, examples) and fix them all in the same change.
