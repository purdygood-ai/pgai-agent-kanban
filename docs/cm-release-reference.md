# CM Release Reference

The long-form reference behind eleven passages of `team/roles/CM.md`.
The role file carries the procedure CM performs on a wake — the
operations, the ship-policy matrix, the nine HALT triggers, the release
lifecycle steps. This document carries the mechanism behind those
procedures: the resolution rules, the token tables, the edge cases, and
the contracts CM reads about but never executes by hand.

Each section below is named from the role file at the point the material
used to sit inline, so it is always one hop away. The one exception is the
last: the post-release archive is a finalize step CM never performs or
checks by hand, so the role file carries no passage for it. Nothing here is new
law — each passage is either the text the role file previously carried
verbatim, that text with its cross-references re-pointed, or the
mechanism behind a rule the role file states in short form.

This document is not on any role's read-order manifest, by design: no
role in the catalog has the governance byte headroom to absorb it, and a
named pointer keeps it reachable without spending that headroom.

## Contents

| Section | Named from `team/roles/CM.md` |
|---|---|
| Local-only mode in full | "Local-only mode: `push_to_remote = false`" |
| Historical release state in full | "Release state" |
| The approval gate's dashboard surfaces | "The human approval gate" |
| HALT is reserved for system-breaking states | "HALT is reserved for system-breaking states" |
| The release-notes status stamp in full | "The status stamp (Step 4e)" and "Augmenting the auto-generated notes" |
| The known-issue ship in full | "The known-issue ship leaves two records" |
| The ship posture in full | "Ship policy" |
| Release hooks in full | "Release hooks" |
| HALT-AFTER in full | "HALT-AFTER (soft halt)" |
| The DEEP-VERIFY tier in full | "The DEEP-VERIFY tier" |
| Bundled item status promotion in full | "Bundled item status promotion" |
| The post-release archive in full | none (finalize Step 16c; nothing for CM to do) |

---

## Local-only mode in full

**Only a resolved project pushes.** `cm/open-rc.sh`, `cm/release.sh`, `cm/finalize-release.sh`, `ship-rc.sh`, `cm/cancel-rc.sh` and `unwind-rc.sh` each decide once, before any git command, through the shared push-decision resolver the OVERWATCH push-lag check and the CM push watchdog also use. A push needs a project registered in `projects.cfg` whose own `project.cfg` (a legacy `PROJECT.cfg` also counts) names `dev_tree_path`. `REPO_ROOT`, an inherited `PGAI_DEV_TREE_PATH` and every other fallback repository are never consulted for this decision, so none of them can make a project pushable.

For a resolved project, `[project] push_to_remote` decides. It is a boolean defaulting to `true`: absent, blank, or any value other than the literal `false` means push, so a registered project with no key still pushes. Only the exact value `false` opts out. In bash, `pp_push_to_remote <project>` prints the resolver's answer as `true` or `false`. `cm/open-rc.sh`, `cm/release.sh` and `cm/finalize-release.sh` take the decision through `pp_push_policy`; `ship-rc.sh`, `cm/cancel-rc.sh` and `unwind-rc.sh` take it in Python from the same resolver, `unwind-rc.sh` through the very call `cm/cancel-rc.sh` makes.

An unresolved project (not registered, no `project.cfg`, or no `dev_tree_path` in it) pushes nothing and contacts no remote. The script logs one line naming the missing piece, then runs exactly as it does for `push_to_remote = false`:

```
<site-tag> skipped-unresolved-project: project '<name>': missing <piece>: <detail>; nothing pushed and no remote contacted
```

`<site-tag>` is `[cm-open-rc]`, `[cm-release]`, `[cm-finalize-release]`, `[ship-rc]`, `[cm-cancel-rc]` or `[unwind-rc]`. `<piece>` is `registration`, `project-cfg` or `dev-tree-path`. The gate fails closed: in `cm/open-rc.sh`, `cm/release.sh` and `cm/finalize-release.sh`, a resolver that cannot run reads as unresolved with piece `resolver`, never as push.

When `false`, all release work still happens locally — squash, fidelity gate, tag, RC branch creation, notes generation and commits, bundled-item promotion — but **no origin operation runs at all**: no RC-branch push, no main or tag push, no origin-side RC deletion, no notes-augmentation push. The same holds for an unresolved project. Each skipped push logs `[push_to_remote=false] skipping <remote> push for <project>: <command>`, where `<remote>` is the project's git remote (`[project] git_remote_name`, `origin` by default), and each script logs a push-policy header at startup (for an unresolved project, the `skipped-unresolved-project` line in its place), so the output shows local-only **by design, not by failure**.

Operators use it for demo runs, customer environments whose policy forbids an agent writing to their origin, and self-contained installs with no outbound git access. Origin-sync does not apply in this mode — origin is intentionally behind — so CM marks `DONE` once the local script exits 0 and distribution is operator-driven. Any revival of the push-watchdog must skip these projects.

**`unwind-rc.sh` narrates its own shape.** Its only remote calls go to `<remote>`, the project's git remote (`[project] git_remote_name`, `origin` by default): the plan's `git ls-remote --exit-code --heads <remote> <rc>` check for the RC branch and step c's `git push <remote> --delete <rc>`. A project that does not push runs neither, in a dry run and a confirmed run alike, and every local step runs the same whether the project pushes or not. The decision is taken as soon as the project name resolves, before the repository is, so its push-policy or skip line prints even when a later check refuses. For `push_to_remote = false` it logs:

```
[unwind-rc] Push policy: push_to_remote=false — skipping <remote> operations. Local cleanup only.
```

The plan then lists the remote branch as `[skipped — push_to_remote=false]`, and step c logs `[push_to_remote=false] skipping <remote> push for <name>: git push <remote> --delete <rc> (Step c)`. For an unresolved project the `[unwind-rc] skipped-unresolved-project` line replaces the policy line, the plan reads `[skipped — unresolved project]`, and step c's skip line is tagged `[unresolved project]` instead of `[push_to_remote=false]`. A project that pushes gets no push-policy line: the plan checks `<remote>` and step c deletes the RC branch there when it exists, with no extra narration.

## Historical release state in full

Historical release state is git tags on the dev tree's main branch, and the highest semver tag merged into `origin/main` is the canonical answer to "what version are we at." That answer comes from one helper: `pp_last_released_version "<project>"` in `scripts/lib/project_paths.sh`, which reads `dev_tree_path` from `project.cfg`, makes a best-effort tag fetch, filters tags merged into `origin/main` to strict semver, returns the highest (`v0.0.0` when there are none or the tree is unreachable), and never changes the caller's cwd.

Do not parse `## Last Released` as a general version answer and do not run `git tag` inline; both bypass the one place dev-tree resolution lives. `## Last Released` exists for exactly one consumer — `pgai_agent_kanban.halt_after.drain` evaluating the `rc:vX.Y.Z` drain — and the role file's "Release state" section carries that field's four-field schema and lifecycle.

## The approval gate's dashboard surfaces

A pending `HUMAN-APPROVE` gate surfaces on four dashboard surfaces at once, any one enough to notice it: a yellow `✋ APPROVAL(n)` status-bar segment (`[APPROVAL(n)]` under `NO_COLOR`), dashboard window 13 (`human-review`), the attention pane and its web-UI tab, and the `HUMAN-APPROVE` queue in `show-queues.sh --details`. All clear on the next render after the gate resolves.

The operator approves with `scripts/close.sh` — advancing the gate to `DONE` and unblocking CM-release — or rejects with `scripts/wontdo.sh`; both are copy-pasteable from the `human-review` window.

Operators set `required` where a human must inspect the RC — user-facing changes, security-sensitive updates, breaking changes — and leave `auto` for routine patches and internal tooling.

## HALT is reserved for system-breaking states

Four classes of finding are system-breaking, and nothing else on a report is. A **breakage-class suite failure** — a gated runner exiting non-zero from a syntax error, compile failure, or unresolved first-party import, or a pytest failure the report classifies as a real defect — produced no trustworthy pass signal. A **destructive-path failure** damaged or corrupted state: a squash conflict, a fidelity-gate failure, a pre-squash or pre-tag hook failure, a destructive test failure. **Verification unable to run at all** means CM cannot ship on inputs it could not obtain. And **a defect too severe for the next bugfix iteration to absorb** — high systemic risk, or serious concerns with a large-fix finding — is the "loop is unhealthy" signal, not the "there are known bugs" signal.

The mechanical release-failure triggers in the role file's "The nine triggers" stand independently of all four.

## The release-notes status stamp in full

Step 4e runs after the matrix has been applied and after the pre-squash hook, but **before the squash that carries the RC into main** — stamping before the squash is what gets the corrected status onto main. Only the stamp time is at issue; the decision time is unchanged.

Where the notes file holds the placeholder under a `## Status` heading, Step 4e replaces it with the decided value and commits the stamped file on the RC branch, changing nothing beyond that value and the two section bodies described below. Where no WRITER-authored file exists it is a silent no-op, and the later auto-generation writes fresh notes with the correct status after the squash. Where the file has a recognizable `## Status` heading but no placeholder — a re-run, or a concrete value — it logs "already complete" and continues.

After stamping it verifies the placeholder is gone and a `## Status` heading remains, and HALTs (trigger 9) if the placeholder survived, if the stamp step exited non-zero, or if the file has no recognizable heading at all; on any of those the script stops before mutating branches, so a surviving placeholder never ships. CM does not invoke Step 4e — it is internal to the release script — but a trigger-9 HALT means inspecting `release-notes/<version>.md` by hand.

**The augmentation template's sections.** Where a project requires the structured template at `templates/project/release/RELEASE-NOTES-TEMPLATE.md`, the augmented notes carry a **Summary** paragraph, **What Shipped**, **Bugs Resolved**, **Bugs Skipped**, and **Known Issues**, each reading `None.` when empty. Not all five are the author's to write: **Bugs Skipped** never is — it is rendered at release time, below — and **Known Issues** is written for the author on a `KNOWN-ISSUE` decision. Commit and push the augmentation through `scripts/cm/finalize-release.sh`, never a direct `git push origin main`: the finalize script performs the same commit and push from inside a subshell where the PreToolUse protected-branch hook does not apply, so the augmentation reaches origin without weakening the guardrail.

**Bugs Skipped is rendered, not authored.** The section is no longer a gap summary CM copies out of the TESTER report by hand, on either of the two paths a notes file can reach the tag by. `cm_release_apply_ship_policy` builds the block once on every release, whether or not a WRITER draft exists: it resolves every bug, gap, and stale-assertion finding in the report to a bug file and a rationale, excludes anything the Known-Issues block already covers so no bug appears under both, and renders a table — header, separator, and one row per finding. Each row carries the substance from the bug's own `Symptom` text, its public identifier or `internal-only`, its severity, and the finding's own rationale; every cell passes through the internal-identifier scrub, so a rationale quoting an earlier finding cannot re-leak that finding's kanban task ID. Unlike the Known-Issues body, the render is independent of the ship decision, because a release can carry deferred bugs under any status.

Both paths place that one block, and neither recomputes it or scrubs it a second time. Where a WRITER draft holding the placeholder is waiting on the RC branch, Step 4e writes the block into it in the same write as the status stamp, replacing the `## Bugs Skipped` body wholesale — whatever an author typed there — and appending the section where the notes carry no such heading at all. Where no draft exists, the auto-generation step (`cm_release_generate_notes`) emits `## Bugs Skipped` as a section of its own, between `## Other Changes` and `## Known Issues`, holding the same block. That is the path every release without a WRITER draft takes, and it is the reason the augmentation step has no Bugs-Skipped table left to type.

The one case the render does not cover is a report with no qualifying finding, and there the two paths differ. On the auto-generated path the generator writes a literal `None` and no author text is involved at all. On the WRITER-draft path the block is empty and the authored section — typically `None.` — is left exactly as written, unscrubbed. That is why the template still instructs the author to describe bugs by substance and never to type a kanban task identifier. The guarantee is that a hand-typed identifier cannot reach the shipped table; it is not a licence to type one.

Two further limits are worth knowing. Each path renders once: the stamp's render fires only on a live stamp, so a re-run over already-stamped notes leaves the table as the first run left it, and the generator's idempotency guard is the same shape, skipping generation entirely once the notes file is committed on the main branch's HEAD. And `## Bugs Resolved` has no equivalent render on either path — it remains fully hand-authored.

**Notes are final once stamped, committed, and tagged.** The immutability rule applies to the notes as they exist **after** the Step 4e stamp and after the tag. It does not apply to WRITER's `PENDING-RELEASE` placeholder — stamping that is the managed CM write that *produces* the canonical notes, not an edit of them. Once stamped, committed, tagged, and pushed, the notes must not be edited: they are the permanent record of that release. A typo, omission, or factual error found afterwards is filed as a bug and corrected in the next release's notes as an errata entry. Post-tag editing has previously produced real confusion about what actually shipped.

## The known-issue ship in full

A `KNOWN-ISSUE` decision is the ship policy's answer to a high-risk finding this release did not cause and cannot cheaply fix: the release goes out, and the finding goes on the record twice. The role file's "The known-issue ship leaves two records" states the two records; this section is the mechanism behind them.

**Resolving the causing bugs.** Every finding in the TESTER report whose *own* `Systemic Risk` is `high` is a cause, not just the first — by the time the decision is `KNOWN-ISSUE`, each of them has already cleared the regression, integrity, and fix-effort test. Each such finding's `Filed As` field is read for a bug identifier and matched against a bug file in the project's `bugs/` directory. A `Filed As` value that resolves to no file on disk is reported as a warning and skipped; no path is ever fabricated, so the list only names bugs that can actually be marked. A decision that resolves no bug file at all still ships, with a warning that no marker was written.

**The notes bullets.** Each bullet renders the bug's substance, never the bare internal ticket ID: the first sentence of the bug's own `Symptom` section, its public identifier where one has been assigned, and the fixed note that no workaround is available — bug files carry no machine-readable workaround field, so none is guessed. A bug with no `Symptom` text renders as an unresolved defect with that absence stated. Where the notes carry no `## Known Issues` heading at all, the section is appended rather than dropped.

**Why the two records land together.** The status stamp and the known-issues body are one write to one file. There is no intermediate state in which the status changed but the prose did not, which is why the caller's existing placeholder guard covers both halves without a second check.

**The marker is write-once and uncommitted.** `## Known Issue Carry Forward` is stamped onto each resolved bug file only where the field is absent or empty; a file that already carries a value is left exactly as it is, so a re-run never clobbers an earlier release's marker. The write lands under `projects/<name>/bugs/` — kanban process state, not a path inside the dev tree — so it is not part of the release commit, on the same reasoning as the `## Fixed In` writeback. A marker-write error is logged and the release continues: the notes already carry the record, and a missing marker costs ordering in the next bundle, not correctness.

**What reads it.** The discovery bundler partitions its item list so every item carrying a non-empty marker leads, each group in the order it arrived. It never writes the field, and it never inspects the version the field names — only whether a value is there. A marker whose value is blank is treated as unmarked and left in place rather than promoted or dropped, and a marker cannot make an item eligible that the pipeline's own status and cache checks did not already admit.

## The ship posture in full

The `[release] ship_posture` key decides how Steps 4c and 4d answer a matrix HALT. Under `matrix`, the default, every HALT row halts. Under `always`, triggers 2, 3 and 8 ship instead and each leaves an `## Auto-Ship Event` in `release-state.md` and one line under the release notes' `## Known Issues`. The keys, the project.cfg-over-kanban.cfg chain and the remote-lane guard are `docs/OPERATIONS.md`, "Ship posture — shipping through a ship-policy stop." This section is the mechanism inside the release script.

**The read.** Step 4c opens by resolving the posture once, through `pp_ship_posture`, which applies the chain and the guard and always answers `matrix` or `always`. A resolver that is unavailable or fails reads as `matrix`. Under `always` the step prints:

```
  Ship posture:         always — triggers 2, 3, 8 ship and record what they would have stopped on
```

Under `matrix` it prints nothing, and nothing below prints a line or changes a value: the release's output, exit code, HALT file and `release-state.md` writes are exactly what they are without a posture.

**Step 4c — the pattern stop.** When the last three shipped releases are all `NON-FUNCTIONAL`, `matrix` HALTs on `cm-release.trigger-8.last-3-rcs-non-functional`. Under `always` the step instead files one bug naming the pattern, quoting the three releases and their statuses, through `pgai_agent_kanban.cm.ship_policy_bug_filer` — or cites the open bug that already records it. It then records an Auto-Ship Event carrying that bug and carries on to Step 4d. A filer that files nothing is narrated, and the event is recorded without a bug: a bug filer never stops a release.

*Which releases it reads.* The guard reads the dev tree's `release-notes/` through the shared reader, `pgai_agent_kanban.cm.notes_status_section non-functional-pattern`, and orders the notes files by semantic version. Two kinds of file are not shipped releases and are passed over:

- the active RC's own notes;
- any file whose `## Status` still shows `PENDING-RELEASE`.

Each remaining file's Status is its first visible value: HTML comments are removed, so the template's guidance comment above the value never hides it, and Markdown emphasis around the value is ignored. With fewer than three shipped releases the check passes. Its output ends with a `NON-FUNCTIONAL pattern window: a.md,b.md,c.md` line naming the three files it read.

*Passing the stop.* `--force` does not cover trigger 8; the stop is passed by acknowledgment instead. When the guard HALTs, it first records the window of three releases in `projects/<name>/release-state/non-functional-pattern-halted-window`. The operator's `unhalt.sh --project <name>` is the acknowledgment. The next run that reads the same three releases prints `Pattern already halted on this window (…) and the HALT was cleared — continuing.` and goes on to Step 4d. That run neither halts nor files a bug, under either posture. A newer `NON-FUNCTIONAL` release forms a new window, which HALTs again. A `--decide-only` run records no window. A window that cannot be written is narrated as a warning, and the HALT still fires. Until the record is fixed, the next run halts on the same window again.

**Step 4d — the matrix stops.** The matrix decides exactly as it always has. When it returns `HALT`, the script names the row that fired, testing the rows in the matrix's own order, first match winning:

| Order | Condition | Error key | Trigger |
|---|---|---|---|
| 1 | TESTER state `BLOCKED` | `cm-release.trigger-1.tester-blocked` | 1 |
| 2 | TESTER task found, state anything but `DONE` | `cm-release.ship-policy.tester-state-unrecognized` | none |
| 3 | Systemic risk `high` | `cm-release.trigger-2.systemic-risk-high` | 2 |
| 4 | `ship-with-serious-concerns` with a large fix | `cm-release.trigger-3.serious-concerns-large-fix` | 3 |
| 5 | Anything else | `cm-release.ship-policy.halt` | none |

That one classification selects the HALT branch under either posture, so every HALT's message, remedy and `--force` reach are unchanged. Under `always`, a trigger-2 or trigger-3 row is shipped through instead: the decision becomes `SHIP-NON-FUNCTIONAL`, which stamps Status `NON-FUNCTIONAL`, and the event is recorded:

```
  Ship posture:         always — trigger 2 (cm-release.trigger-2.systemic-risk-high) ships NON-FUNCTIONAL instead of halting
  Ship decision:       SHIP-NON-FUNCTIONAL
[cm-release] Auto-Ship Event recorded for trigger 2 (appended to release-state.md after Step 12's field update).
```

The other three rows HALT under `always` exactly as under `matrix`.

**The Known Issues body after a trigger-2 or trigger-3 auto-ship.** `pgai_agent_kanban.cm.stop_bug_files` resolves the bugs behind the stop from the TESTER report: for trigger 2, every finding whose own `Systemic Risk` is `high`; for trigger 3, every finding whose own `Fix Effort` is `large`. Each finding's `Filed As` value is matched to a bug file, and one that resolves to no file is narrated and skipped. The bugs feed the same bullet renderer the known-issue ship uses, beneath the non-functional warning:

```
This release is shipped non-functional. Do not use in production. Fix expected in the next patch.
The findings behind this status are tracked for a fix in the next release:
- **<public id>** — <first sentence of the bug's Symptom> — No workaround available.
```

A bug with no public identifier renders as `- <symptom> (internal-only, not yet disclosed) — No workaround available.` With no bug resolved, the second line reads `The findings behind this status are tracked on the project's internal bug ledger.` and no bullets follow. Bugs named here are kept out of `## Bugs Skipped`. The `## Known Issue Carry Forward` marker stays exclusive to a `SHIP-KNOWN-ISSUE` decision; an auto-ship writes none.

**The release-notes line.** Every stop the posture shipped through adds one line, in the order the stops fired, to the top of the notes' `## Known Issues` body:

```
Auto-shipped under ship_posture=always (would have halted: trigger N — <error key>)
```

`pgai_agent_kanban.cm.auto_ship_notes` owns the wording and placement. The lines go ahead of whatever the section already says — the non-functional warning, a known-issue block, an author's own text — separated from it by one blank line. A body that says nothing (empty, or only `None` or `None.` around HTML comments) is replaced by the lines; a file with no `## Known Issues` heading gets the section appended at its end; and a line the section already carries is not added again, so a re-run leaves the file byte-identical.

Both notes paths place the lines before their commit: Step 4e, right after the status stamp on WRITER-authored notes, and Step 11a, on generated notes. They are placed whatever the Status, because a trigger-8 auto-ship can ship `FUNCTIONAL`. A notes file that cannot be updated is a warning naming the command that adds the lines by hand, never an exit: by that point the release has passed every gate.

**The Auto-Ship Event.** Events are recorded during Steps 4c and 4d but appended to `release-state.md` only in Step 12's post-write block, right after any `## Operator Override` event, through `pgai_agent_kanban/cm/write_rc_state.py auto-ship-event`. A release that clears the ship policy under `always` and then halts at a mechanical gate therefore leaves no event behind, because it did not ship. Each event is a block of its own, appended after everything already in the file, in the order the stops fired. An example, with placeholder values:

```
## Auto-Ship Event
Timestamp: 2026-10-06T04:12:33Z
Version: vX.Y.Z
Trigger: 8
Error Key: cm-release.trigger-8.last-3-rcs-non-functional
Ship Posture: always
TESTER Task: <tester-task-id>
Filed Bug: BUG-NNNN
```

The fields always appear in that order, one `Label: value` line each. `Filed Bug` appears only when a bug was filed or cited, which in practice means trigger 8. No existing byte of the file changes, so a second event is a second block and never overwrites the first.

Like `## Operator Override`, the event is a record only: no later release reads it back, and the release-state loader's `--validate` returns the same result for a file with appended events as for one without. The writer refuses an empty or multi-line value, a trigger that is not a positive whole number, and a `release-state.md` that does not exist. If the append fails, the release has already been tagged, so the script prints a warning carrying the exact `pgai_agent_kanban/cm/write_rc_state.py auto-ship-event` command that appends the event by hand, and the remaining steps run.

**Under `--decide-only`.** The posture branch runs the same way, with three differences: the bug filer is replaced by a narration of the bug a full run would file or cite; each event is narrated (`[cm-release] decide-only: a full run records an Auto-Ship Event for trigger N; nothing is written.`) rather than recorded; and the run ends after Step 4d, before any notes stamp. The triggers the posture shipped through appear in the decision line's `trigger=` field. The decider's output contract is `docs/operator-commands.md`, "cm-release --decide-only."

## Release hooks in full

Three optional keys under `project.cfg [hooks]` declare one path per phase: `cm_release_pre_squash_hook`, `cm_release_pre_tag_hook`, `cm_release_post_tag_hook`. An absent or empty key means no hook for that phase, with no error and no warning. A value beginning with `/` is absolute; anything else resolves against `dev_tree_path` from the same `project.cfg`.

Existence is not checked at discovery: a missing file at phase time logs `hook <name>: not present, skipping` and the release proceeds, and a non-executable one logs a warning and is skipped. For backward compatibility CM also recognizes legacy paths at `$KANBAN_ROOT/projects/<name>/hooks/cm-release-<phase>.sh`, consulted **only** when the matching key is absent or empty and logging `Legacy hook path detected; declare in project.cfg [hooks].` as the cue to migrate; a non-empty declared key always wins, silently, even where a legacy file also exists.

The three phases exist because the work each enables can only be done meaningfully at that point:

- **pre-squash** — on the RC branch after verification, before the squash. Version-file bumps, manifest regeneration, CHANGELOG entries; its commits are on the RC and become part of what gets squashed. Blocking.
- **pre-tag** — after the squash and the fidelity gate, before `git tag`. Final consistency checks across the merged state, artifacts that depend on main being current. Blocking.
- **post-tag** — after the tag exists locally, before the best-effort push. Notifications, asset uploads, downstream triggers; the local release is already complete. Non-blocking.

Hook authors owe four things — exit 0 on success and non-zero on failure; **idempotence**, because CM may re-invoke a hook on an operator retry and re-running must not fail when the work is already done; speed, targeting sub-30-second runtime; and staying inside the project's own dev tree or kanban directory with no global side effects. A hook that mutates a sibling project's tree or hangs for minutes is a project defect, and the bug is filed against the project, not the framework.

The operator-facing reference — the three-tier resolution model, the always-printed resolution line, the `cm_release_<phase>_hook_required` fail-loud flag, and the hook environment contract shared by `cm/release.sh` and `ship-rc.sh` — is `docs/OPERATIONS.md`, "Release Lifecycle Hooks."

## HALT-AFTER in full

`HALT-AFTER` is the soft variant: the chain keeps running until a named event drains, then auto-promotes itself to a regular `HALT`. `HALT` stops the chain at the next wake firing; `HALT-AFTER` lets it keep firing until the drain condition is met, so use it to let in-flight work finish instead of freezing immediately. It does **not** gate wakes while draining — that is the whole point of the soft variant.

The file's contents are the event token. Six are supported.

| Token | Drains when |
|---|---|
| `rc` | The RC that was in flight at arm time ships (its version tags on main), regardless of whether a follow-on RC opens immediately |
| `pm` | No PM task in the project's queues is `WORKING`, `BACKLOG`, or `WAITING` |
| `coder` | No CODER task in the project's queues is `WORKING`, `BACKLOG`, or `WAITING` |
| `writer` | No WRITER task in the project's queues is `WORKING`, `BACKLOG`, or `WAITING` |
| `tester` | No TESTER task in the project's queues is `WORKING`, `BACKLOG`, or `WAITING` |
| `cm` | No CM task in the project's queues is `WORKING`, `BACKLOG`, or `WAITING` |

The role tokens count queued work, not just running work: a `BACKLOG` or
`WAITING` task of that role holds the drain open, so a role token never halts
the chain early on a momentary gap between two tasks. A task stuck `WAITING` on
a prerequisite that will never resolve holds it open too — remove the stuck task
to let the drain through.

`rc` means "the current RC": when the file is created (or first evaluated) holding it, the arm-time RC version is captured from `release-state.md` and recorded alongside the token as `rc:v0.40.0`. The drain compares the shipped version against that recorded one; it does **not** test for RC-idle. Back-to-back RCs do not defer it — if v0.40.0 was in flight at arm time and v0.40.1 opens the moment v0.40.0 tags, the sentinel still promotes on v0.40.0's ship, because that is the version the token bound to. With no RC in flight at arm time there is no version to bind, so arm `rc` only while the RC you want to halt after is actually in flight — or name the boundary directly with `halt-after.sh --until vX.Y.Z`, which writes the identical `rc:vX.Y.Z` token up front and can therefore name a version the project has not reached yet. `--until` is `rc` scope only; its flag surface and three refusals are `docs/operator-commands.md`, the halt-family section. The five role tokens are narrower: wait until the named role has no in-flight work.

The `rc:vX.Y.Z` drain is **monotonic and latching**. `pgai_agent_kanban.halt_after.drain` reads `## Last Released` and compares it with semver `>=`, not string equality, so the token drains as soon as its own version *or any later one* has shipped: captured at `rc:v0.44.2`, it drains when the field reads `v0.44.2`, `v0.44.3`, or higher. Drain evaluation does not always run in the narrow window between one version tagging and the next landing, and `>=` makes it immune to that race; the same semantics tolerate an absent or empty field on a project that has never shipped, returning False until a release populates it.

The file is parsed with `.strip().lower()`: whitespace stripped, token lowercased, an empty or whitespace-only file defaulting to `rc`, an unrecognized token logged as a warning and treated as absent — the chain continues and no promotion fires until the file holds a valid token. Use the exact lowercase spellings above.

When the drain condition is met **and the project's Active RC reads `none`**, the chain auto-promotes at whatever scope the file lives at: remove `HALT-AFTER`, create `HALT` at the same scope, append an audit entry to the project's `release-state.md`. The chain is then hard-halted exactly as if the operator had touched `HALT` directly. A drain satisfied while an RC is still open is latched and converts at RC close instead — that invariant, and CM's own ship-time spend of a version-pinned sentinel at Step 12c, are in the role file's "HALT-AFTER (soft halt)". The audit entry's shape:

```
## HALT Event
- Timestamp: 2026-05-29T01:23:45+00:00
- Trigger: HALT-AFTER rc:v0.39.6 auto-promotion
- Event drained: rc (until v0.39.6 shipped)
- Promoted: HALT-AFTER → HALT
```

`Trigger` names the token that was draining, verbatim — a version-pinned promotion reads `rc:v0.39.6`, not a bare `rc`. `Event drained` records the concrete condition that resolved, in one of three forms: `rc (until vX.Y.Z shipped)` for the versioned family, composed through the one shared "until vX.Y.Z" phrase the status bar and the arm confirmation also read, so none of the three can drift; `rc (Active RC cleared)` for a bare `rc`; and `<role> (no WORKING <ROLE> tasks found)` for the five role tokens. `Promoted` is always the literal `HALT-AFTER → HALT`.

The operator arm and resume walkthrough is `docs/OPERATIONS.md`, "HALT-AFTER — operator arm and resume."

## The DEEP-VERIFY tier in full

Cold-build is the only lane that can see whether the shipped artefact builds on a stranger's machine, and a sweep costs roughly thirteen minutes of wall clock whether or not anything container-shaped changed. Hence the tier's rule:

> **Run the expensive gate when it can learn something; skip it, loudly, when it cannot.**

Both halves bind: re-confirming a tree already confirmed learns nothing, and a skip nobody records is indistinguishable from a pass.

**Shape.** Cold-build has left per-RC verification entirely — an RC runs the fast suites only and TESTER starts no container engine. The lane now sits in a tier named for the tier rather than the lane, so other expensive suites can join `scripts/deep-verify.sh` later without a second entry point. Every authorized run runs the lane and appends one record to `$KANBAN_ROOT/projects/<name>/deep-verify-ledger.md`: timestamp, tree state exercised (version and commit), verdict, per-pair engine/flavor detail. That ledger is append-only kanban process state, a sibling of `release-state.md`, never committed into a dev tree, read only through `scripts/lib/deep_verify_ledger.sh`.

**What the tier holds.** `scripts/run-cold-build-tests.sh` collects the whole of `team/tests/cold/` as a directory rather than naming modules one at a time, so an expensive suite joins the tier by being dropped there. Five modules live in it:

| Module | What it exercises |
|---|---|
| `test_docker_cold_build.py` | The Dockerfile cold-build sweep: every flavor under every installed engine, no cache, base image force-repulled. |
| `test_docker_build_fixture.py` | `docker build` against each `docker/<flavor>/` Dockerfile, parametrized over both flavors. |
| `test_dashboard_smoke_container.py` | The in-container dashboard smoke harness — `dashboard/create.sh --no-tmux` inside each built flavor image, asserting no pane's external command comes back missing. |
| `test_dashboard_operator_window.py` | Operator-window geometry and teardown, statically and again inside a live tmux session in a built image. |
| `test_container_shell_launcher.py` | The workspace-container launcher's own contract: flag validation, required-path failures, and which engine binary it resolves and execs. |

Every module that needs a real engine carries its own absence guard and emits a narrated skip when none answers, the same way the cold-build sweep does — a host without docker or podman records a conscious skip rather than a failure. The launcher module needs no such guard: it drives `container-shell.sh` against a stub engine placed first on `PATH` and asserts on the invocation the launcher assembles, so it starts nothing.

**The per-RC gated sweep runs none of them.** `run-unit-tests.sh` and `run-integration-tests.sh` do not reach `team/tests/cold/` with their pytest arguments, and both refuse — before collection — any module in the per-RC sweep set that invokes or probes a container engine, naming the file and pointing at this tier as the one lane entitled to do it. The guarantee that buys is about cost, not capability: an RC verified on a host with a working container engine builds no images, starts no containers, and costs the same wall clock as an RC verified on a host with no engine installed at all. Nothing is weakened by sitting in the tier — every assertion in the table above still runs, on the tier's own three triggers rather than on every pre-merge check.

**Verdicts** are exactly `built`, `failed-to-build`, or `could-not-attempt` — the three outcomes of core's "an unusable build blocks; an unavailable build environment does not," which is the canon for what each is worth. `failed-to-build` files a bug into the project's `bugs/`, or cites an open bug covering the same failing pairs, and never touches whatever RC is open: the finding belongs to the loop, not to whichever RC happened to be open when the run fired. A `built` record can come with a bug too: when every image built but a non-build cold test failed, the run records `built`, names the failing tests in `detail`, exits 1, and files a bug naming each test's file and test id — or cites the open bug already naming the same set of tests, never a second. That bug goes through the loop like any other; the record is still `built` and still satisfies the pre-release check. `built` needs evidence that a build happened: at least one pair built, or no pair was narrate-skipped. A run in which no pair built and pairs were narrate-skipped — a host with no usable engine, where only the engine-free cold tests ran — records `could-not-attempt`, never `built`, and does not satisfy the pre-release check. If one of those engine-free tests failed (the lane exited 1), the run names it in `detail` beside the skip reasons, files or cites the same test bug, and exits 1: the missing engine is not a finding, but the failing test is. Any other `could-not-attempt` files no bug, because nothing was learned about whether the images build.

**Three triggers**, and no others — no per-RC mode, no diff-triggered mode:

| Trigger | Invocation | When it fires |
|---|---|---|
| **Scheduled** | `PGAI_CONTAINER_TIER=1 deep-verify.sh --scheduled --image all` — one operator-written cron entry, no `--project`, fanning out over every active project | Weekly, off-hours. Self-gating: nothing runs unless this project's last ledger record is at least seven days old and the clock is inside its off-hours window. |
| **Pre-release** | `deep-verify-freshness-check.sh --project <name> --version <vX.Y.Z>` | A named promotion-playbook step before the public tag is pushed. A public tag never ships without a fresh green lane. |
| **On demand** | `PGAI_CONTAINER_TIER=1 deep-verify.sh --project <name> --image all`, alias `PGAI_CONTAINER_TIER=1 cold-build-now.sh --project <name> --image all` | Whenever an operator runs it from their own shell. |

Pre-release is a **check, not a run**: it reads the ledger, decides whether the record there still speaks for the version being promoted, writes nothing, and never starts the lane on the operator's behalf. The remedy for its refusal is the on-demand verb, printed with the authorization prefix and `--image all` so it runs as printed from the operator's shell.

**Scheduled and on-demand runs are the operator's.** Running the lane is the container tier, which is operator-only: a harness runs only with `PGAI_CONTAINER_TIER=1` in the invoking environment or `--operator-approved` on its command line, and never inside an agent wake, CM's included. An unauthorized or in-wake call prints one `SKIPPED` line keyed `container_tier.operator_only` and exits 0 — no lane, no verdict, no ledger record — so the ledger keeps its last real result and a skip can never satisfy the pre-release check. A run also needs an image selection (`--image NAME`, `--image all`, or the `kanban.cfg [release] container_images` default); with none it lists the images and builds nothing. Every run and every skip is recorded in `logs/docker-use.log`, and `why.sh --details` shows the latest line. The flags, exit codes and log format are `docs/operator-commands.md`, "deep-verify, cold-build-now."

**The per-project switch.** Two `project.cfg [release]` keys govern the scheduled trigger alone; on-demand and pre-release ignore both, and both are read from `[release]` and no other section. `deep_verify` takes `weekly` (default) or `off`, with `true` and `false` accepted as aliases of those two words, and `off` costs nothing — no lane, no container engine, no ledger write, one narration line, exit 0. `deep_verify_offhours_window` takes `HH:MM-HH:MM` in UTC, default `03:00-03:59`, falling back to that default when absent, blank, or malformed rather than refusing. Opt-out is deliberate: a lane enabled only when somebody remembers to enable it stops running. An alias is a recognized spelling, not a guess: a value outside the accepted set is never resolved into a guessed mode — the scheduled invocation refuses dual-channel and runs nothing. A key written outside `[release]` is invisible to the resolver, so `pp_deep_verify_mode` and `pp_deep_verify_offhours_window` look for it elsewhere in the file before falling back and warn on stderr, naming the section it was found in, `[release]`, the default applied, and the remedy — a warning, not a refusal.

**Freshness is a risk input, not bookkeeping.** A byte-identical tree can stop building because something upstream moved — an image tag retagged, a package pulled — so a green from eleven days ago is a materially weaker claim about today's tree than one from this morning. Every TESTER report therefore carries a mandatory `## Deep-Verify Freshness` line naming when the lane last ran, against what tree state, with what result, including "never run" and "the last result was red"; it states the fact without re-deriving the verdict, and the fact alone moves neither the recommendation nor systemic risk. The pre-release check enforces it: a record satisfies promotion only when it is `built`, for exactly the version being promoted, and no older than `PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS` (default 7, matching the weekly cadence). Anything else refuses, and silence refuses loudest — never-run never buys a promotion.

**No silent skips.** Wherever the lane did not run, whatever would have reported it says so instead: the freshness line whatever its content, the scheduled invocation naming the project it skipped and why (including the next due time when a week simply has not passed), the pre-release check naming the condition that failed. Nothing in this tier may imply a green lane that did not run.

Flags for all three verbs and the walkthrough for each refusal: `docs/operator-commands.md` and `docs/promotion-playbook.md`.

## Bundled item status promotion in full

Every bug and priority item moves `open -> running -> done`. The bundler in `scripts/lib/discovery.sh` writes `open -> running` when it generates a bundle file; `cm/release.sh` writes `running -> done` as the last release-state mutation on its success path, closing the lifecycle invariant. Items at any other value are skipped.

By then the squash commit exists, the fidelity gate has passed, the RC branch is deleted, `release-state.md` is cleared, and the tag has been created locally. The script finds the RC's bundle file in the project's `requirements/` by glob (`<active-rc>-*bundle*.md`), reads its `## Bundled Items` section, and for each absolute path at `running` asks whether some `DONE` task's README names that item's `BUG-NNNN` / `PRIORITY-NNNN` identifier — only then does it rewrite `## Status` from `running` to `done`. Closing an item is a claim its fix shipped, so an item with no such reference (a withdrawn fix ticket, or one that never reached `DONE`) is left at its prior status and named in the post-ship report as carried rather than closed, remaining eligible for the next bundle. Missing files are logged and skipped, no other field is touched, and the bundle requirements file's own `## Status` promotes to `done` regardless of how many items were carried — the release still ships. No matching bundle file means a warning and everything left at `running`, recoverable by an operator-run migration script.

Because the promotion is last and the script runs under `set -euo pipefail`, a non-zero exit means no status was updated and an exit 0 means every item reached `done` or was logged as skipped or carried — the exit code is authoritative, and CM verifies nothing by hand. The `[Step 16b]` block in stdout, ending in `Bundled items: N promoted, M skipped (not running), K missing, J carried (unfixed)`, confirms the lifecycle closed cleanly and states the carried count.

## The post-release archive in full

**It is the last step of release finalize.** Step 16c runs after the `## Fixed In` stamp (Step 16b) and before the closing "Local release preparation complete" summary. It moves the released project's finished items beyond `[archive] archive_count` into `$KANBAN_ROOT/archive/<project>/`, for that project only. The helper is `_cm_release_archive_finished_items` in `scripts/lib/cm/release_archive.sh`. It calls the same `run_archive` function as `scripts/archive.sh` and `POST /operations/archive`, passing the kanban root explicitly so the run can act only on that root.

**It is quality class: a failure is logged and the release completes.** Whatever the archive does — moves items, finds nothing to move, is skipped, or stops at a failing item — the release finishes with the exit status it would have had without the step. Step 16c writes no HALT, changes no exit code, and writes no `release-state.md` field. A run that did not finish is narrated and runs again at the next release's Step 16c, or on demand through `scripts/archive.sh --project <name>`.

**It runs only once the RC is closed.** Three conditions must all hold: the local RC branch is gone, `release-state.md` reads `## Active RC` `none`, and `## Last Released` equals the version being released. If any one fails, or the state cannot be read, nothing is archived and the step says why. The RC's own tasks only count as finished once the release has closed it.

**It is skipped on a cancelled RC.** `cm/cancel-rc.sh` and `unwind-rc.sh` never reach release finalize, so a cancelled RC archives nothing and leaves `archive/<name>/` as it was.

**Inside CM's wake it works under the chain's own repo lock.** The archive works under the project's repo lock, which the wake chain holds for its whole run on that project. A release run inside CM's wake therefore reaches Step 16c with the lock already held by its own chain. Step 16c passes `--accept-chain-lock` to `run_archive`, so the archive checks the lock's recorded holder pid. When that pid is the archive's own process or one of its ancestors, the hold is the release's own chain: the archive, and the legacy requirements migration that follows it, run under that hold without taking or releasing the lock, and the chain's lock record is left as it was. A release run from a shell outside any wake finds the lock free and takes it itself.

**It is skipped only when another writer holds the lock.** A holder outside the release's own process ancestry skips the archive. Examples: a wake chain working the project while the release runs from a shell, an operator's `scripts/archive.sh` run, or a `POST /operations/archive` call. The archive reports `archive.repo_lock_busy` with the holder's four provenance fields, moves nothing, and exits 3. It also skips when it cannot read its own process ancestry, because the check answers "not this chain" rather than guess. Either way the release completes. `scripts/archive.sh` and `POST /operations/archive` never pass the flag, so they skip whenever any other process holds the lock.

Every line the archive prints is prefixed `[Step 16c] [archive] `. Step 16c then closes with one of these lines:

| Archive result | Closing line |
|---|---|
| Finished (exit 0), items moved or none to move | `[Step 16c] Archive finished for <project>.` |
| RC not closed (not run) | `[Step 16c] WARNING: the RC is not closed (...) -- nothing archived; release continues.` |
| Repo lock held by another writer (exit 3) | `[Step 16c] WARNING: archive skipped for <project>: the project's repo lock is held by a process outside this release's own wake chain (...); nothing moved; release continues.` |
| Refused (exit 1) | `[Step 16c] WARNING: archive refused for <project> (exit 1; ...); nothing moved; release continues.` |
| Stopped at a failing item (exit 2, or any other code) | `[Step 16c] WARNING: archive exited <N> for <project> (...); items moved before the failure are in the ledger; release continues.` |

Each WARNING line ends with the on-demand command. What the archive selects, its layout, the ledger and restore are in `docs/OPERATIONS.md`, "Archive — finished work out of the live project."
