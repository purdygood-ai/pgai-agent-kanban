# Operator Commands

The command reference for running a live pgai-agent-kanban install. This is the
page a stranger uses to operate the system: what each operator script does, the
flags it takes, and when to reach for it. [HOW_TO.md](../HOW_TO.md) is the
narrative manual; this page is the command surface in one place.

Paths are live-install paths (`$KANBAN_ROOT/scripts/...`). Keys are unique only
within a project, so `--project` and `--key` are both required on every command
that targets a task or intake item — there is no environment-variable fallback
for the key.

## Before anything

Source the shell environment first — every command on this page expects
`$KANBAN_ROOT` and its sibling variables to be set:

```bash
source ~/pgai_agent_kanban/shell-env    # adjust to your install path
```

A fresh shell without this fails on the first command with empty-variable
errors.

---

## Shared design philosophy

The operator scripts are power tools for an operator who has already HALTed the
project when an agent might be mid-task. They assume you mean what you typed and
otherwise do exactly what they are told:

- **Assume intent.** The scripts exist so you don't open files and flip
  statuses, checkboxes, and blockers by hand.
- **Refuse only an unidentifiable target.** The one refusal common to every
  command is a key that does not resolve to a single item: not found, or
  ambiguous (zero or multiple matches). That is "cannot act — target
  undefined," not "won't act." One command adds a single data-safety refusal of
  its own: `reset` refuses a `WORKING` task because re-queuing it would race a
  live agent. Apart from that, the tools do not second-guess you — anything else
  that might be unwise gets a one-line warning and proceeds. You HALT first when
  an agent may be working; the scripts do not police that for you.
- **No confirmation prompts.** The command is the confirmation. (`--yes` exists
  on the wrappers for the few paths that still prompt; `--force` overrides a
  safety guard where one is documented.)
- **No cascades.** Each command touches its own artifact only. Composition is
  you running several commands deliberately.

Each command declares its **own** flag vocabulary. The scripts share the parsing
mechanism, not a single global flag set: every command accepts exactly the flags it
uses and rejects anything else with a uniform `unknown argument: --X` message — no
flag is special-cased. Each section below lists the flags that command actually
accepts; `--help`/`-h` prints that same per-command list and exits 0 on every
wrapper.

---

## intake — drop an intake file into a project, routed by filename

`intake.sh` deposits a staged intake file — a bug, priority, or requirement you
have written or `scp`'d to `/tmp` — into the right project intake directory. It
routes by the file's **name**, copies it in, and sets mode 644. It saves you the
destination-path typing; it replaces `cp /tmp/BUG-XXXX.md projects/<p>/bugs/ &&
chmod 644 ...` with one command.

```bash
scripts/intake.sh --project my-app /tmp/BUG-0400-widget-crash.md
scripts/intake.sh --project my-app /tmp/PRIORITY-0007-faster-boot.md
scripts/intake.sh --project my-app /tmp/v0.89.0-new-dashboard.md
```

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | yes | Project the file is destined for. |
| `<file>` | yes | Path to the staged intake file. Copied, not moved — the source is left in place. |
| `--help`/`-h` | no | Print usage and exit 0. |

**Routing** is by filename prefix (case-sensitive, mirroring the existing intake
filename conventions):

| Filename pattern | Destination |
|---|---|
| `BUG-*` | `projects/<name>/bugs/` |
| `PRIORITY-*` | `projects/<name>/priority/` |
| `vX.Y.Z-*.md` (version-prefixed) | `projects/<name>/requirements/` |

**It is deliberately dumb: it routes, copies, and sets 644 — it does NOT
validate the file's contents.** It does not assign numbers, check the
internal heading against the filename, or verify the body. Content validation
stays with the discovery pipeline exactly as before: a malformed file still
routes here, then the pipeline rejects it to `.rejected/`. `intake.sh` neither
duplicates nor pre-empts that.

**Refusals.** `intake.sh` refuses — copying nothing — in two cases:

- **Unroutable name.** The filename matches none of `BUG-*`, `PRIORITY-*`, or
  `vX.Y.Z-*` and cannot be routed.
- **Existing target.** A file of the same name already exists in the
  destination; `intake.sh` will not clobber an in-flight item.

---

## reset — return tasks and intake items to a re-pickable state

`reset.sh` is the unified dispatcher for all reset paths. Mode is resolved
entirely from the `--key` — the key prefix is self-identifying:

- **Task key** (`ROLE-YYYYMMDD-NNN`, e.g. `CODER-20260611-002`) → agent-task
  reset; the role is in the prefix.
- **Intake key** → intake reset; the item type is inferred from the prefix:
  `BUG-*` → bug, `PRIORITY-*` → priority, a version or other string →
  requirement.

```bash
# Agent-task resets (key carries the role in its prefix)
scripts/reset.sh --project my-app --key PM-20260611-001
scripts/reset.sh --project my-app --key CODER-20260611-002
scripts/reset.sh --project my-app --key WRITER-20260611-005
scripts/reset.sh --project my-app --key TESTER-20260611-006
scripts/reset.sh --project my-app --key CM-20260611-008

# Intake resets (type inferred from key prefix)
scripts/reset.sh --project my-app --key BUG-0042
scripts/reset.sh --project my-app --key PRIORITY-0007
scripts/reset.sh --project my-app --key v0.1.2
```

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | yes | Project the item lives in. |
| `--key <key>` | yes | Task key (`ROLE-YYYYMMDD-NNN`) or intake key (`BUG-NNNN`, `PRIORITY-NNNN`, version). The prefix selects the reset mode. |
| `--keep-artifacts` | no | Agent resets only: preserve `artifacts/` (default: clear). |
| `--force` | no | Agent resets only: clear a stale worktree (registration and/or on-disk path left by a prior run) before resetting. Without `--force`, a detected stale worktree causes a warning with the manual removal recipe and exits 2. With `--force`, reset.sh performs the cleanup itself (git worktree remove, prune, rm -rf) narrating each step to stderr, then completes the reset. Use `--force` as the standard corpse-clearing step when a prior run left a zombie worktree. |
| `--help`/`-h` | no | Print usage and exit 0. |

**Agent-task reset** returns the task to freshly-materialized state — total
amnesia. The next wake picks it up as if it had never run: `status.md`
regenerated to `BACKLOG`, `artifacts/` and task `logs/` cleared
(`--keep-artifacts` opts out), the queue marker flipped to `[ ]`, and the prior
attempt's `feature/<task-id>` branch deleted with stale worktrees pruned. The
README (the work definition) and queue position are untouched. A TESTER task key
also tears down a retained TESTER worktree.

**Stale-worktree corpse clearing:** if a prior run left a zombie worktree
(registration in `.git/worktrees/` and/or an on-disk directory), bare reset
refuses with exit 2 and prints the three-command removal recipe. Re-run with
`--force` to have reset.sh clear the corpse automatically in one call.

**Intake reset** flips the item's `## Status` back to its open value and clears
its backlog-cache marker so discovery bundles it again. `--requirement`
additionally clears the PM materializer's idempotence marker — without that, PM
silently skips a re-opened requirement.

Resetting a `BLOCKED` task is also how you un-gate a project: while any task is
`BLOCKED` with Needs Human, the project dispatches no new tasks. Reset the
blocked task once its cause is fixed and dispatch resumes.

**Exit codes:** `0` reset completed · `1` usage error or key not found /
ambiguous · `2` `WORKING`-state refusal or stale-worktree refusal without
`--force` (agent resets only) · `3` key not found · `4` `--force` aborted
because the on-disk worktree path is an active mount target (the blocking mount
is named in stderr; no partial cleanup occurs).

---

## show — print an item's content (read-only)

`show.sh` emits a task's `status.md` or `README.md`, or an intake item's file,
to stdout. It never modifies anything.

```bash
scripts/show.sh --project my-app --key 20260611-002                 # status.md
scripts/show.sh --project my-app --key 20260611-002 --file readme    # README.md
scripts/show.sh --project my-app --key BUG-0042                      # intake file
```

| Flag | Effect |
|---|---|
| `--project <name>` | Project the item lives in. Required. |
| `--key <key>` | Task folder name or intake key. Required. |
| `--file status\|readme` | For task keys: which file to emit (default `status`). Ignored for single-file intake items. |
| `--help`/`-h` | Print usage and exit 0. |

**Exit codes:** `0` content emitted · `1` usage error · `3` key not found.

---

## show-test-report — print a TESTER verification report (read-only)

`show-test-report.sh` prints the `report.md` from a TESTER verification task to
stdout. It saves you from hand-typing the full artifact path: instead of
`cat projects/<p>/tasks/TESTER-…-verify-…/artifacts/report.md`, you name the
project and a key. Like `show`, it is strictly read-only — no file is created,
modified, or deleted.

```bash
scripts/show-test-report.sh --project my-app --key TESTER-20260611-031   # task key
scripts/show-test-report.sh --project my-app --key v0.93.1               # RC version
```

| Flag | Effect |
|---|---|
| `--project <name>` | Project the report lives in. Required; falls back to `$PGAI_PROJECT_NAME` when unset. |
| `--key <key>` | RC version (`vX.Y.Z`) or TESTER task key (`TESTER-YYYYMMDD-NNN`, or a unique prefix). Required. |
| `--help`/`-h` | Print usage and exit 0. |

**Key resolution** is self-identifying — the key's shape selects the path:

- **TESTER task key** (`TESTER-YYYYMMDD-NNN`, or a unique prefix): resolves to
  that task via the shared resolver and prints its `artifacts/report.md`. The
  key must resolve to a single TESTER task.
- **RC version** (`vX.Y.Z`, e.g. `v0.93.1`): finds the TESTER tasks whose name
  encodes that version (the `TESTER-…-verify-X-Y-Z` naming convention, dots
  replaced by dashes) and prints the report of the **latest** one — the highest
  task number, i.e. the most recent re-run. This is the common case: name the
  released version and read the report that verified it.

**Exit codes:** `0` report emitted · `1` usage error or missing argument · `2`
ambiguous key (multiple tasks matched a prefix; the first match is still
printed) · `3` key not found — no matching TESTER task, the key does not resolve
to a TESTER task, or its `artifacts/report.md` is absent.

| Exit | Meaning |
|---|---|
| `0` | Report emitted to stdout. |
| `1` | Usage error, missing/invalid argument, or configuration error. |
| `2` | Ambiguous key — multiple tasks matched the prefix; the first match is printed. |
| `3` | Key not found — no matching TESTER task, the key is not a TESTER task, or `report.md` is absent. |

---

## close — close an item by key

`close.sh` closes an item identified by `--key`. It performs the close and
refuses only when it cannot resolve the key to a single target. The target's
type and state do not matter — `close` does its function on whatever the key
resolves to.

```bash
scripts/close.sh --project my-app --key BUG-0362 --state superseded \
                 --note 'subsumed by PRIORITY-0099'
scripts/close.sh --project my-app --key CODER-20260611-002   # closes the task as DONE
```

| Flag | Effect |
|---|---|
| `--project <name>` | Project the item lives in. Required. |
| `--key <key>` | Item key: an intake key (`BUG-0001`, `PRIORITY-0042`, a requirement version) or an agent task ID. Required. |
| `--state <state>` | Terminal state for intake items: `done` (default), `wont-do`, `superseded`. On an agent task, `--state` is intake-only vocabulary; `close` always closes the task as `DONE`. |
| `--note <text>` | Free-form note recorded in the item's `## Close Note` section. |
| `--dry-run` | Report what would change without writing. |
| `--help`/`-h` | Print usage and exit 0. |

**What close does by target type:**

- **Intake item** (bug, priority, requirement): sets `## Status` to the
  `--state` value, records the optional `--note`, and flips the queue/backlog
  marker to `[x]`.
- **Agent task**: sets `## State` to `DONE`, clears `## Blockers` to `none` and
  `## Needs Human` to `no`, and flips the queue marker to `[x]`. Closing a task
  *means* marking it `DONE` — that is `close`'s function. The `--state` flag is
  intake vocabulary; on a task it is ignored and the task closes as `DONE`. To
  abandon a task instead, use [`wontdo`](#wontdo--retire-a-task-as-wont-do) —
  the separate abandon verb.

The only refusals are resolution failures: a key that matches nothing, or a key
that matches more than one item. `close` never refuses based on the target's
type or state.

**Exit codes:** `0` closed (or `--help` / `--dry-run`) · `1` usage error · `2`
ambiguous key (zero or multiple matches) · `3` key not found · `4` state
mutation failed.

---

## delete — remove a task or intake item

`delete.sh` removes an item by key, guarded by a terminal-state check.

```bash
scripts/delete.sh --project my-app --key 20260611-002 --dry-run   # preview
scripts/delete.sh --project my-app --key 20260611-002             # delete (DONE/WONT-DO)
scripts/delete.sh --project my-app --key 20260611-002 --force     # override guard
```

| Flag | Effect |
|---|---|
| `--project <name>` | Project the item lives in. Required. |
| `--key <key>` | Task folder name or intake file base name. Required. |
| `--force` | Bypass the guard and delete regardless of state (data-loss risk: no undo). |
| `--dry-run` | Print the target without removing anything. |
| `--help`/`-h` | Print usage and exit 0. |

**Guard:** deletion is refused unless the item is `DONE` or `WONT-DO`. Use
`--force` to bypass at your own risk; there is no undo.

---

## wontdo — retire a task as WONT-DO

`wontdo.sh` marks an agent task `WONT-DO`, retiring it cleanly without marking it
`DONE`.

```bash
scripts/wontdo.sh --project my-app --key CODER-20260611-002
```

| Flag | Effect |
|---|---|
| `--project <name>` | Project the task lives in. Required. |
| `--key <task-id>` | Task ID (the folder name under `tasks/`; the role is in the prefix). Required. |
| `--help`/`-h` | Print usage and exit 0. |

The agents-decide-`DONE` invariant is enforced: no argument combination can make
`wontdo.sh` produce `DONE`. It retires; it never completes.

---

## halt, unhalt, halt-global, unhalt-global, halt-after — stop and resume the wake loop

These wrappers manage the signal files that pause task dispatch. A HALT blocks
discovery and dispatch at iteration start; in-flight agents finish their current
task (HALT is not a kill). Wakes stay cheap while halted — they check and exit.

```bash
scripts/halt.sh          --project my-app    # stop this project now
scripts/unhalt.sh        --project my-app    # resume this project
scripts/halt-global.sh                       # stop ALL projects (no args)
scripts/unhalt-global.sh                     # resume ALL projects (no args)
scripts/halt-after.sh    --project my-app --key rc      # stop after current RC ships
scripts/halt-after.sh    --project my-app --key coder   # stop after CODER work drains
```

**`halt.sh`** creates the per-project HALT signal at
`projects/<name>/HALT`, stopping that one project's wake loop cleanly.
**`unhalt.sh`** removes it and lets the project resume. Both are **unchanged**:
they remain per-project and require `--project` — there is no global mode on
`halt.sh` (the global pair below is its own command for that reason). Use a
project HALT to quarantine one project while the others keep working.

**`halt-global.sh`** creates the global HALT at `${KANBAN_ROOT}/HALT`, which
discovery honors to stop **every** project at the next wake. **`unhalt-global.sh`**
removes it so all projects resume. Both take **no arguments** — no `--project`,
no `--key`, no prompt — because a global halt has no project to scope to and is
fully reversible. Both are **idempotent**: `halt-global.sh` on an already-set
HALT and `unhalt-global.sh` with no HALT present each exit 0 with an
informational message rather than an error. `--help`/`-h` prints the no-arg
usage. These commands replace the old manual `touch ${KANBAN_ROOT}/HALT` /
`rm ${KANBAN_ROOT}/HALT` workflow; reach for the global pair before an upgrade.

> **There is deliberately no `halt-after-global.sh`.** `halt-after` is bound to a
> specific project's release candidate — "drain *this* project's current RC, then
> halt" — so it has no global meaning. The absence of the script enforces that
> constraint by design; do not expect (or add) a global halt-after.

**`halt-after.sh`** arms the soft-drain signal: "stop, but only after X
finishes," without watching for the moment. The drain token is set with `--key`
(default `rc`). Supported tokens: `rc`, `pm`, `coder`, `writer`, `tester`, `cm`.

- **`rc`** captures the in-flight RC version at arm time and drains until that
  version (or higher) shows as Last Released.
- **Agent tokens** drain until no task of that role is `WORKING`, `BACKLOG`, or
  `WAITING`. Conservative by design: never halts early while queued work exists.

When the drain condition is met, the signal promotes itself atomically:
HALT-AFTER is removed and HALT is created. Then `unhalt.sh` (or `rm HALT`) when
you are ready to resume.

---

## unwind-rc — fully unwind an in-flight release candidate

`unwind-rc.sh` rewinds an in-flight release candidate across all state stores. It
is the operator escape hatch when an RC must be abandoned entirely.

```bash
scripts/unwind-rc.sh --project my-app --key v0.7.17 --dry-run   # print the plan
scripts/unwind-rc.sh --project my-app --key v0.7.17             # execute
```

| Flag | Effect |
|---|---|
| `--project <name>` | Project name (must match `projects/<name>/` and `projects.cfg`). Required. |
| `--key vX.Y.Z` | RC version to unwind. Must match the project's Active RC. Required. |
| `--dry-run` | Print the unwind plan and exit without modifying state. |
| `--force` | Skip the Active-RC-version mismatch check. |

Run `--dry-run` first to read the plan. The version must match the Active RC
unless you pass `--force`.

---

## See also

- [HOW_TO.md](../HOW_TO.md) — the narrative operator manual (setup, projects,
  workflows, the dashboard, stopping the system).
- [operator-troubleshooting.md](operator-troubleshooting.md) — diagnosing and
  recovering from stuck states.
- [quarantine-recovery.md](quarantine-recovery.md) — recovering a quarantined
  project.
