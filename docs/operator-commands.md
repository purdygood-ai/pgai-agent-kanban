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
  undefined," not "won't act." Two commands add a single data-safety refusal of
  their own: `reset` refuses a `WORKING` task because re-queuing it would race a
  live agent, and `release-locks` refuses to free a lock whose recorded holder
  pid is verifiably still running, because two processes believing they hold the
  same lock is worse than a wait (`--force` overrides it). Apart from those, the
  tools do not second-guess you — anything else that might be unwise gets a
  one-line warning and proceeds. You HALT first when an agent may be working;
  the scripts do not police that for you.
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
routes here, then the pipeline rejects it and, if it keeps rejecting it,
quarantines it into `projects/<name>/rejected/` (see
[Quarantined intake](#quarantined-intake)). `intake.sh` neither duplicates nor
pre-empts that.

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

Resetting a `BLOCKED` task is also how you un-gate a project early: while the
project's `BLOCKED` + Needs Human tasks are systemic -- two or more sharing one
Block Record `reason_code` blocked within `[wake] block_storm_window` seconds
back from now, or one whose `reason_code` is on `[wake] systemic_block_reasons`
-- the project dispatches no new tasks for any role but OVERWATCH. The agent
catch-all `agent_unclassified` never forms a storm, and a lone task-local block
holds only the tasks that depend on it. Every hold lifts by itself no later
than `[wake] block_hold_cap` seconds after the block that began it, and the
held wake's log line ends "not dispatching new tasks until <time>" with the
lift time. Reset the blocked tasks once their cause is fixed and dispatch
resumes at the next wake, without waiting for the cap.

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

## show-readme — print a task's README (read-only)

`show-readme.sh` prints a task's `README.md` — the task's spec — to stdout. It
never modifies anything. It takes task keys only: a bug, priority or
requirement key is refused with a line pointing you at `show.sh`, which prints
that item's own file.

```bash
$KANBAN_ROOT/scripts/show-readme.sh --project my-app --key CODER-20260611-002   # example project and task key
```

Usage: `show-readme.sh --project <name> --key <task-key> [--help]`

| Flag | Effect |
|---|---|
| `--project <name>` | Project the task lives in. Falls back to `$PGAI_PROJECT_NAME` when omitted. |
| `--key <task-key>` | Task folder name. Required. |
| `--help`/`-h` | Print usage and exit 0. |

**Exit codes:** `0` README emitted · `1` usage error, or the key names a bug,
priority or requirement · `3` key not found.

`show.sh --file readme` prints the same file. Over REST the read is
`GET /projects/{project}/tasks/{key}/readme`.

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

## show-queues — the queue-state pane, outside tmux (read-only)

`dashboard/show-queues.sh` prints the queue-state pane: one line per agent
(`pm`, `coder`, `writer`, `tester`, `cm`) with its done count, its working and
blocked indicators, and the currently-working task underneath. It is a
dashboard renderer promoted to an operator verb — the same script the tmux
queues pane and `why.sh --details` call, never a second copy of the rendering —
so running it in a plain terminal answers "what does the board say right now"
without starting tmux. Strictly read-only.

```bash
scripts/dashboard/show-queues.sh --project my-app              # compact summary
scripts/dashboard/show-queues.sh --project my-app --details    # per-agent, per-state listing
scripts/dashboard/show-queues.sh --project my                  # abbreviation: resolves to my-app
scripts/dashboard/show-queues.sh --details                     # every registered project in turn
```

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | no | Project to render. Resolved by exact match, then by unambiguous hyphen-boundary prefix — the same exact-then-prefix rule every other operator key uses. Omit it and compact mode falls back to the first registered project (the historical dashboard-pane behavior), while `--details` mode iterates every registered project in turn; omitting it is a mode, not a mistake. |
| `--details` | no | Per-agent, per-state task listing instead of the compact one-line-per-agent summary. |
| `--kanban-root <path>` | no | Render against a different kanban root. Dashboard-internal plumbing; the REST twin deliberately does not expose it. |
| `--help`/`-h` | no | Print usage and exit 0. |

The first output line is `Active provider: <name>`, so the operator can see
which provider the lane is configured against before reading the counts.

A `--project` value that resolves to nothing — a typo, or a name that was never
registered — refuses with a machine key and a runnable remedy naming the
registered projects, and renders no board at all. A board of zeros would read
exactly like a real board for a quiet project, and a read-only display has no
plausible answer for a project it cannot find.

**Exit codes:** `0` summary emitted · `1` usage error (an undeclared flag was
supplied), or a `--project` that resolves to no registered project.

---

## task-status — read and write a task's status.md fields

`task-status.sh` reads and writes the fields of a task's `status.md`. Agents
use it to change state instead of hand-editing; an operator uses its read mode
to ask what a task's state, or any other section, says right now. It is a thin
wrapper: every argument goes unchanged to the Python module
`pgai_agent_kanban.task_status`, so the script and `python3 -m` are the same
command and print the same output. `--project`/`--key` resolve the task folder
from the kanban root and work from any directory; a `status.md` path given
instead is read relative to the current directory, so the by-path examples
below assume you are in `$KANBAN_ROOT`.

It has four forms. The examples name the task once, in a variable:

```bash
KEY=CODER-20260611-002-add-login    # the task's folder name under tasks/
```

Read a task:

```bash
scripts/task-status.sh read --project my-app --key "$KEY"                       # ## State, uppercased
scripts/task-status.sh read --project my-app --key "$KEY" --section Summary     # one section's body
scripts/task-status.sh read "projects/my-app/tasks/$KEY/status.md"              # by path
scripts/task-status.sh --read-state "projects/my-app/tasks/$KEY/status.md"      # ## State only, by path
```

Write a task:

```bash
scripts/task-status.sh --project my-app --key "$KEY" --state BLOCKED \
    --needs-human yes --blockers "dev tree missing"
scripts/task-status.sh set-section --project my-app --key "$KEY" \
    --section "Next Recommended Step" --body "Re-run once the dev tree is restored."
```

The same calls through the module, with the install root on `PYTHONPATH`:

```bash
PYTHONPATH="$KANBAN_ROOT" python3 -m pgai_agent_kanban.task_status read --project my-app --key "$KEY"
PYTHONPATH="$KANBAN_ROOT" python3 -m pgai_agent_kanban.task_status set-section --project my-app \
    --key "$KEY" --section Artifacts --body "artifacts/report.md"
```

| Form | Writes | Effect |
|---|---|---|
| `read (STATUS_FILE \| --project P --key K) [--section HEADING]` | nothing | With no `--section`, or `--section State`, prints the `## State` value uppercased. With any other heading (named without `## `) prints that section's body. An absent section prints an empty line and exits 0; an absent `status.md` is refused. |
| `--read-state STATUS_FILE` | nothing | Prints the `## State` value uppercased, or an empty line when the file or the value is absent, and always exits 0. Needs no `--project`/`--key`; do not combine it with the write flags. |
| `--project P --key K --state STATE [--needs-human yes\|no] [--summary TEXT] [--blockers TEXT]` | `## State`, plus each section whose flag is given | Validates `STATE` against `BACKLOG\|WAITING\|WORKING\|BLOCKED\|DONE\|WONT-DO` and writes atomically. A section whose flag is omitted is untouched. |
| `set-section (STATUS_FILE \| --project P --key K) --section HEADING --body TEXT` | that one section | Replaces one section's body atomically, preserving every other section byte-for-byte. The section must already exist. `State` is refused here — change it with `--state`, which validates it — and a `Needs Human` body must be `yes` or `no`. |

`--help`/`-h` prints the top-level flags and exits 0; `read --help` and
`set-section --help` print each subcommand's own flags.

The two read forms differ only on a missing file: `--read-state` prints an
empty line and exits 0, so a shell caller can test it without an error branch;
`read` refuses, because a task named by `--project`/`--key` whose `status.md`
is missing is a fault worth seeing.

Neither write form creates a task folder or a missing section. A write touches
only what it names; every other section is preserved byte-for-byte.

**Exit codes:** `0` written, or the value printed · `1` bad input — a missing
`--project` or `--key`, an invalid state or `--needs-human` value, a missing
`status.md` or section, or a missing or conflicting `read`/`set-section`
target. A refusal prints a machine `error` key, a plain-language `message`,
and a runnable `remedy` to stderr, and writes nothing.

---

## close — close an item by key

`close.sh` closes an item identified by `--key`. It performs the close and
refuses only when `--state` is not a closed state or when it cannot resolve the
key to a single target. The target's type and state do not matter — `close`
does its function on whatever the key resolves to.

```bash
scripts/close.sh --project my-app --key BUG-NNNN --state wont-do \
                 --note 'subsumed by PRIORITY-0099'
scripts/close.sh --project my-app --key CODER-20260611-002   # closes the task as DONE
```

| Flag | Effect |
|---|---|
| `--project <name>` | Project the item lives in. Required. |
| `--key <key>` | Item key: an intake key (`BUG-0001`, `PRIORITY-0042`, a requirement version) or an agent task ID. Required. |
| `--state <state>` | Closed state for intake items: `done` (default) or `wont-do`. These are the only closed states. An item that another item subsumes closes as `wont-do`, with a `--note` saying why. Any other value is refused: see "Refused states" below. On an agent task, `--state` is intake-only vocabulary; `close` always closes the task as `DONE`. |
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

`close` refuses in only two cases: a `--state` value that is not a closed state,
or a key it cannot resolve. A key that matches nothing or matches more than one
item is a resolution failure. `close` never refuses because of the target's
type or current state.

**Refused states.** `--state` is checked before the key is resolved, for agent
tasks too. Any value other than `done` or `wont-do` is refused with exit 1 and
the dual-channel `close.invalid_state` error, and nothing is written. That
includes `superseded`, which is not a kanban state. The remedy names your
`--project` and `--key`, so it runs as printed. Example output for
`--project my-app --key BUG-NNNN --state superseded`:

```text
error:   close.invalid_state
message: close.sh: 'superseded' is not a kanban state (valid: done | wont-do); an item another item subsumed closes as 'wont-do' with a note saying why.
remedy:  close.sh --project my-app --key BUG-NNNN --state wont-do --note 'superseded by another item'
```

Any other unknown value gets a different message and a remedy that closes
the item as `done`:

```text
message: close.sh: invalid --state value: <value> (valid: done | wont-do)
```

An existing file whose `## Status` already says `superseded` is not rewritten.
The board, `GET /board`, discovery and the bug backlog cache all read it as
`wont-do`. It is drawn as closed and never offered for bundling again.

**Exit codes:** `0` closed (or `--help` / `--dry-run`) · `1` usage error or
refused `--state` · `2` ambiguous key (zero or multiple matches) · `3` key not
found · `4` state mutation failed.

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
scripts/halt-after.sh    --project my-app --until v2.20.1   # stop after v2.20.1 (or later) ships
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
usage. Use them rather than a manual `touch ${KANBAN_ROOT}/HALT` /
`rm ${KANBAN_ROOT}/HALT`; reach for the global pair before an upgrade.

> **There is deliberately no global halt-after command.** `halt-after` is bound to a
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

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | yes | Project to arm. Falls back to `$PGAI_PROJECT_NAME`. |
| `--key <token>` | no | Drain token (default `rc`). One of `rc`, `pm`, `coder`, `writer`, `tester`, `cm`. |
| `--until vX.Y.Z` | no | Arm the versioned `rc:vX.Y.Z` token directly. `rc` scope only. A leading `v` is optional and normalised, exactly as `defer.sh --until` handles it. |
| `--help`/`-h` | no | Print usage and exit 0. |

**`--until` names a release boundary the project has not reached yet.** It
writes the same `rc:vX.Y.Z` token a bare `rc` arm captures for itself from the
in-flight RC — one token, two ways to fill it in, byte-identical on disk. The
difference is which boundary you can name: a capture can only bind to the RC
that is in flight right now ("stop after this one"), while `--until` says "keep
shipping RCs until a release at or past `vX.Y.Z` lands, then park." The drain is
monotonic and latching — `>=`, not string equality — so a token armed at
`v2.20.1` still drains if the project jumps straight from v2.20.0 to v2.20.2.

**`--until` is `rc` scope only.** The five role tokens drain on task state and
have no version boundary for a version to bind to, so combining `--until` with
one of them refuses rather than silently ignoring one of the two flags.

**Promotion happens at RC close, not the moment the drain is satisfied.** Every
token — bare `rc`, versioned `rc:vX.Y.Z`, and all five role tokens — waits for
the project's Active RC to read `none` before a satisfied drain may convert
HALT-AFTER into HALT. A satisfied drain observed mid-RC is latched, not lost, and
converts on the first evaluation after the RC closes. Arming `--key coder` in the
middle of a release therefore lets that release finish and ship before the chain
parks, rather than freezing it with a half-built RC on the branch. The invariant,
its latch, and CM's ship-time spend of a version-pinned sentinel are in
`team/roles/CM.md`, "HALT-AFTER (soft halt)".

When the drain condition is met and the RC has closed, the signal promotes itself
atomically: HALT-AFTER is removed and HALT is created. Then `unhalt.sh` (or
`rm HALT`) when you are ready to resume.

**Refusals.** `halt-after.sh` refuses dual-channel — a stable `error` key, a
plain-language `message`, a runnable `remedy` — and arms nothing when it does. A
refused invocation writes no sentinel and touches no existing one; the state on
disk afterwards is byte-identical to the state before it. There are three, and
the scope check runs first, before the project is even resolved.

| Refusal | Cause | Operator-facing message |
|---|---|---|
| `halt-after.until_scope_mismatch` | `--until` given with a `--key` naming anything but `rc`. Honouring it would mean either ignoring `--until` or silently promoting the scope to `rc`, and both reinterpret the arm request instead of obeying it. | `halt-after.sh: --until targets a release boundary; scope 'coder' has none.` |
| `halt-after.project_required` | No project named and no `$PGAI_PROJECT_NAME`. The sentinel, its drain evaluation, and its promotion audit entry are all per-project, so there is no location to write to. | `halt-after.sh: no project named -- the HALT-AFTER sentinel is always per-project.` |
| `halt-after.until_malformed` | `--until` is not well-formed `vX.Y.Z`. Refused at write time so a typo never reaches disk, and never resolved to a guessed version. | `halt-after.sh: --until value '2.9' is not well-formed semver (expected vX.Y.Z).` |

The `'coder'` and `'2.9'` values above are the offending input quoted back —
your own value appears in its place. Each refusal exits 1.

---

## why — one verdict per project, with its cause, its remedy, and an exit code

`why.sh` is the diagnostics front door. Run it with no arguments and it reports
the project `$PGAI_PROJECT_NAME` names when that is set. Only when neither
`--project` nor `$PGAI_PROJECT_NAME` is given does it walk every registered
project in `projects.cfg` priority order. Either way it prints one verdict line
per project — the verdict word, the cause, and a runnable remedy in the same
sentence — and exits with the worst verdict's code. `--project` names one
project and wins over `$PGAI_PROJECT_NAME`. `--details` opens the diagnostic
pane behind that one project's verdict.

```bash
scripts/why.sh                               # $PGAI_PROJECT_NAME's line if set, else every project's
scripts/why.sh --project my-app              # one project's verdict line
scripts/why.sh --project my-app --details    # that line, plus the full diagnostic pane
```

The verb is read-only and side-effect-free: it creates nothing, removes
nothing, and mutates no config or task state. Running it twice leaves every
file under every project root byte-identical.

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | no | Report this project only; wins over `$PGAI_PROJECT_NAME`. Omitted, it falls back to `$PGAI_PROJECT_NAME`; every registered project is walked only when neither is set. |
| `--details` | no | Print the diagnostic pane below the verdict line. Needs a project — `--project` or `$PGAI_PROJECT_NAME`. |
| `--help`/`-h` | no | Print usage and exit 0. |

**Where the posture report lives.** `--project` is optional: a bare
`scripts/why.sh` is the normal invocation. The deferral-posture report — four
`field=value (tier)` lines — is printed inside `--details`, under
`== posture ==`. To read the posture back, run `scripts/why.sh --project my-app
--details`; `scripts/why.sh --project my-app` on its own prints that project's
verdict line.

### The five verdicts

Exactly five verdict words exist. Never a sixth, never two at once. Each line
the verb prints carries the word, the cause, and the remedy together, so the
table below is what the sentence is telling you and what you do about it.

| Verdict | What it means | What you do next |
|---|---|---|
| `RUNNING` | A task is in `WORKING` state for this project. The line names the task, its state, and the whole minutes it has held that state — never a bare "RUNNING" with no evidence attached. | Nothing, while the elapsed figure stays under the 60-minute stall threshold. Past it, the word stays `RUNNING` but the line says so and the exit code changes — see the stalled row below. |
| `RUNNING`, stalled | The same verdict word, past `60` minutes in one state. A task pinned that long has missed roughly thirty wake windows without a state transition: churn or a hang, not scheduling jitter. | Read the named task's `status.md` and its agent log. If it is genuinely stuck, set it to `BLOCKED` with the obstacle named so a human can reset it; if it is hung, file a bug. |
| `BLOCKED` | No task is `WORKING`, and at least one non-terminal task is in `BLOCKED` state. The line names that task. | Read the named task's `status.md` `## Blockers` for the obstacle. A human resolves it, then flips that task's `## State` from `BLOCKED` back to `BACKLOG` — `BLOCKED` returns to `BACKLOG` only by human action. |
| `HALTED` | A project-scoped `HALT` file is present at `projects/<name>/HALT`. Nothing can be dequeued regardless of task state, which is why this outranks `RUNNING` and `BLOCKED`. The line quotes the HALT file's reason, or says no reason was recorded. | Only the operator removes a `HALT`. Resolve the underlying issue, then `rm projects/<name>/HALT`. See "When the chain halts" in `docs/OPERATIONS.md` for the full response procedure. |
| `STUCK-LOCK` | No task is `WORKING` or `BLOCKED`, but work is queued (`BACKLOG`/`WAITING`) and this project's own lock is stale (past the reaper's own age threshold — `kanban.cfg` `[locks] stale_lock_reap_after_wake_periods`) or carries no provenance record at all. The queued work has nowhere to go; the line carries the `release-locks` remedy inline. | Run the printed `scripts/release-locks.sh --release <name>` command (add `--force` when the line says so — the recorded holder pid is still alive). If it recurs, inspect that holder's exit behavior. |
| `IDLE` | Nothing is running and nothing is blocked, and no lock is stuck. Three different situations reach this word, and the line's text names which one. | Depends on which: **disabled** (`enabled=false` in `projects.cfg`) — set `enabled=true` to re-enable the project; **queued but not picked up** (tasks in `BACKLOG`/`WAITING`, no stuck lock) — nothing to do, the next scheduled wake firing takes them; **no non-terminal tasks at all** — queue work (a requirements document, a priority file, or a bug report) when there is something for this lane to do. |

**Precedence, highest first:** disabled in `projects.cfg` → `IDLE`; a
project-scoped `HALT` → `HALTED`; a `WORKING` task → `RUNNING`; a `BLOCKED`
task → `BLOCKED`; queued work plus a stale or unprovenanced project lock →
`STUCK-LOCK`; otherwise `IDLE`. The order is fixed in one place
(`team/pgai_agent_kanban/ops/verdict.py`), so a halted project with a `WORKING` task
reads `HALTED` every time, not sometimes.

**Two extra `HALTED` lines, when OVERWATCH is resolving.** On a project that has
opted into autonomous block resolution, the verdict word and the exit code are
unchanged — nothing can be dequeued either way — but the cause reads
`OVERWATCH resolving (lane A, attempt 1) for vX.Y.Z` while a lane is executing,
and reports the recorded `## OVERWATCH Decision` afterwards, so long as that
decision's own version still matches the project's `Active RC`. A decision
about an RC that has since shipped or been cancelled is never shown as the
explanation of a later halt; the line falls back to the HALT file's own reason.
What the lanes are and what authorises each is `team/roles/OVERWATCH.md`,
"Autonomous Block Resolution"; the operator walkthrough is `docs/OPERATIONS.md`,
"When OVERWATCH resolves a halt for you."

**Where the elapsed figure comes from.** `status.md` carries no "entered this
state at" field, so time-in-state is measured from the task's `status.md`
mtime — the same proxy the dashboard and `kanban-status.sh` already display
elapsed time from. Any edit to any other field in that file resets the clock,
which makes the figure a conservative under-count of true time-in-state, never
an over-count.

### Exit codes

The codes are stable, and they are ordered by ascending severity so that a
plain numeric comparison is a severity comparison.

| Code | Verdict | Meaning |
|---|---|---|
| `0` | `RUNNING` | Healthy. The active task is `WORKING` and within the stall threshold. |
| `1` | — | **Pre-flight refusal: no verdict was reached at all.** Either no project was named and none could be walked, or a `--details` section refused (an unparseable campaign file, an invalid `[release]` value, or a malformed refusal ledger). Distinct from every code below — a refusal means the verb could not answer, not that the answer was bad. |
| `2` | `IDLE` | Nothing is `WORKING` or `BLOCKED`, and no lock is stuck. Covers all three IDLE causes above; the printed line names which. |
| `3` | `RUNNING` | Stalled: `WORKING` past the 60-minute threshold. The one case where the verdict word stays `RUNNING` but the exit code is not the healthy `0`. |
| `4` | `BLOCKED` | No task is `WORKING`; a task is `BLOCKED`. |
| `5` | `HALTED` | A project-scoped `HALT` file is present. |
| `6` | `STUCK-LOCK` | No task is `WORKING` or `BLOCKED`, work is queued, and this project's lock is stale or unprovenanced. |

**All-projects aggregation.** When neither `--project` nor `$PGAI_PROJECT_NAME`
names a project, the process exit code is the **maximum** numeric code produced
across every project walked. Because the codes ascend by severity, a plain max
*is* the worst-verdict rule: one halted project among nine healthy ones exits
`5`.

Code `1` is the one value never folded into that maximum. The walk only visits
names that are registered in `projects.cfg`, so a mid-walk pre-flight failure
for one of them is a tree-consistency problem rather than a bad verdict — the
whole invocation refuses at `1` instead of averaging the failure into a
per-project result. A monitor that treats `1` as "below 4, therefore fine" has
it backwards: `1` means the answer is missing.

**Watching it from cron.** Nothing else is needed to automate against this —
the codes above are the whole contract.

```cron
# Every 15 minutes: page only when a lane needs a human (BLOCKED=4, HALTED=5, STUCK-LOCK=6).
# Exit 1 is escalated too: it means no verdict was reached, which is not good news.
*/15 * * * * out=$($HOME/pgai_agent_kanban/scripts/why.sh 2>&1); rc=$?; [ "$rc" -ge 4 ] || [ "$rc" -eq 1 ] && printf '%s\n' "$out" | mail -s "kanban lanes need attention (exit $rc)" ops@example.com
```

A quieter channel is the usual home for `3` (a stalled lane is worth knowing
about but rarely worth waking someone), and `0` and `2` are ordinary operation.

### What `--details` shows

`--details` is additive: the verdict line prints first, unchanged, and the pane
prints below it. Five sections, each answering a different question.

| Section | The question it answers |
|---|---|
| `== locks ==` | *Is something holding this project, and for how long?* Every lock file under `<kanban_root>/locks/` that names this project, with how long it has been held — `held: <duration> (since <acquired_at>) by <holder> pid <pid> — <task>`, where the task reads `chain, no task yet` until the chain dispatches one — a `held verdict:` of `live`, `long (Nm past floor)` against the stale-age threshold, or `dead (pid N not running)`, and its full provenance record — `holder`, `pid`, `op`, and `task`. The duration comes from the `acquired_at` the lock recorded when it was taken, never the file's mtime; a lock taken before that field existed reads `acquired_at not recorded (pre-upgrade lock)` and gets no verdict. A lock with no readable record reads `(no provenance recorded)`; a project with no locks held reads `no locks held for this project`. |
| `== queue tails (last 5, raw markers) ==` | *What is at the end of each queue, and does the queue agree with the tasks?* The last five entries of every `*_backlog.md` under the project's `tasks/queues/`, each shown with its **raw** marker character alongside that task's real `status.md` `## State`. A marker that disagrees with the task's actual state is therefore visible as a disagreement, not silently resolved one way or the other. A queue with no entries says `(no entries)`; a project with no queues directory says `no queues found for this project`. |
| `== show-queues --details ==` | *What does the dashboard's queue pane say right now?* `show-queues.sh --project <name> --details` embedded verbatim — called, never copied, so the two renderings cannot drift. |
| `== posture ==` | *What standing posture is in force?* `HALT`/`HALT-AFTER` (a version-pinned `HALT-AFTER` token also renders its until target), the four resolved deferral-posture lines with the tier each value came from, the project's Active RC, and every pending requirements bundle with its `## Status` (`no pending bundles` when there are none). This is where the deferral-posture report lives now. |
| `== discovery skip reason ==` | *Why did discovery or a gate last refuse for this project?* The project's most recent refusal, read back from its refusal ledger — see below. |

**The most recent refusal is queryable, and "none" is not the same answer as
"unreadable."** Every dual-channel refusal in the framework records itself as
the project's most-recent-refusal entry at `projects/<name>/refusal-ledger.md`.
That file holds exactly one record and is overwritten on each new refusal — it
answers "right now," not "ever"; the wake log already keeps the history.
`== discovery skip reason ==` reads that record back and prints its timestamp,
the surface that raised it, and the refusal's own three fields: `error`,
`message`, and `remedy`. A project that has never recorded one reads
`no discovery/gate refusal recorded for this project`.

A ledger file that exists but does not parse is a **different answer**, and the
verb keeps the two apart deliberately. A record with no recognizable header, or
one missing any of its six required fields, is malformed: `--details` refuses at
exit `1`, names the ledger by path in a diagnostic on stderr, and prints no pane
at all — rather than reporting an absence of refusals it cannot actually
confirm. An unreadable ledger reads as unusable, never as "none." Never take a
missing pane for a clean lane.

That last point generalises to the whole pane. Every section is computed before
anything is printed, so a section that refuses means **nothing** from
`--details` prints — never a half-rendered pane with the failure buried in the
middle of it.

### Refusals

`why.sh` refuses dual-channel — a stable `error` key, a plain-language
`message`, a runnable `remedy` — and prints no report when it does.

| Refusal | Cause |
|---|---|
| `why.project_not_found` | The named project has no directory. Reporting there would answer confidently about a project that was never resolved. |
| `why.details_requires_project` | `--details` given with no project named and no `$PGAI_PROJECT_NAME`. `--project` is optional for the verdict walk; `--details` reports one project's resolved posture and has nothing to resolve against with none named. |
| `defer_posture.campaign_unparseable`, `defer_posture.campaign_malformed_until` | Under `--details`: the campaign file on disk has no parseable `until=` line, or its value is not semver. The refusal names the file by path, and no posture is printed on top of it. |
| `defer_posture.invalid_deferred_bugs`, `defer_posture.invalid_deferred_bug_level`, `defer_posture.invalid_defer_piggyback` | Under `--details`: a `[release]` value at the tier the precedence chain actually landed on is outside its accepted set. A bad value at a tier that was never consulted is not read and cannot refuse. |

### The same verdicts over HTTP

`GET /verdicts` on the operator REST API returns these verdicts as JSON. Both
surfaces resolve through the same verdict engine, so a dashboard and a terminal
cannot disagree about what a lane's verdict is. `GET /verdicts?project=NAME&details=true`
returns the `--details` pane the same way — `posture`, `release_locks` and
`queue_tails` as JSON, resolved through the same functions the text pane above
calls, and refusing without a project exactly as `--details` does. The
most-recent-refusal section stays CLI-only. The endpoint, its response shape,
and the parity guarantee are in [`docs/operator-api.md`](operator-api.md) —
the verdict vocabulary and the exit-code table above are not repeated there.

---

## release-locks — read lock provenance, release a wedged lock

`release-locks.sh` is the operator surface over the advisory-flock lock files
under `$KANBAN_ROOT/locks/`. With no flags it lists every lock with its recorded
provenance — who holds it, its pid, what it is doing, and on behalf of which
task — and a `held`/`stale`/`unparseable` verdict for each. With `--release` it
frees one named lock. Before this verb, a wedged lock had no operator-facing
surface at all: the recourse was to find the file by hand, guess whether the
recorded pid was still alive, and delete it.

```bash
scripts/release-locks.sh                                    # list every lock (read-only)
scripts/release-locks.sh --release repo-wake-my-app         # release that lock
scripts/release-locks.sh --release repo-wake-my-app --force # signal a live holder to release it
```

| Flag | Required | Effect |
|---|---|---|
| `--release <name>` | no | Release the named lock. A lock's name is its file's basename with the trailing `.lock` stripped; a `.lock` you typed by habit is stripped for you. Resolved by exact match, then by unambiguous hyphen-boundary prefix — the same exact-then-prefix rule every other operator key uses. Omit the flag entirely for read-only listing. |
| `--force` | no | Release the lock even when its recorded holder pid is still alive — by sending that pid SIGTERM so its own exit path releases the lock, then waiting up to `kill_grace_seconds` for it to. The live holder's file is never replaced or removed. Ignored when `--release` is not given. |
| `--help`/`-h` | no | Print usage and exit 0. |

**It takes no `--project`.** `locks/` is a single kanban-root-wide directory
shared by every project's wake, repo, and OVERWATCH acquisitions, so there is no
project to scope a lock to.

**The listing shows how long each lock has been held** with the same `held:` line and `held verdict:` the `why.sh --details` locks section prints (see that row above), computed from the lock's recorded `acquired_at`.

**The verdict is evidence, not inference.** `held` means the recorded pid
answered `kill -0`; `stale` means it did not; `unparseable` means the lock file
carries no readable provenance record. The listing always names the pid and the
check performed, so you see the basis for the word. Every lock file is listed
whatever its verdict — an unparseable record is reported by path, never silently
dropped and never assumed stale, because a garbled or empty record is no
evidence that a holder is dead.

**What "release" actually does.** This process never held the lock's flock, so it
cannot close that file descriptor. For a *dead* holder it atomically replaces the
lock file with a fresh, empty one: the kernel released that flock the instant the
process exited, so the swap is a formality that clears the stale-looking record
and the next acquirer's recovery sweep finishes the removal. For a *live* holder,
`--force` never touches the file. Replacing or unlinking it would not stop the
holder — its flock lives on on the old inode — it would only hand the *path* to
the next acquirer, which would flock a brand-new inode while the old chain kept
running: two chains each believing they hold "the" lock, driving one project's
dev tree and queue files at once. Instead the script sends the recorded pid
SIGTERM, so the holder's own exit path releases the lock and removes the file,
and waits up to `kill_grace_seconds` (`[wake]` in `kanban.cfg`, or the
`KILL_GRACE_SECONDS` override; default 30) for that to happen. A holder that
lets go exits the script 0. One still alive and still holding after the wait —
or one this account cannot signal at all — is reported dual-channel
(`release_locks.holder_not_released`, exit 5) with the file still untouched;
escalating to `kill -KILL` is your call, and the remedy prints the exact
command. A killed holder's record is cleared by the next acquirer (dead pid) or
by re-running `--release` without `--force`.

The wake chain's own stale-lock classifier applies the same rule: a lock whose
holder pid is alive is never deleted on age. A live holder whose lock has aged
past the stale-lock threshold — the chain refreshes its lock's mtime while a
task runs, so this means it has stopped refreshing — is narrated as overdue
(`lock.provenance.holder_overdue`) and deferred to, and that narration's remedy
is this verb's `--force`.

**Re-releasing is a no-op, not an error.** A lock that already carries no
provenance — cleared by this verb or by its holder's own release — is reported as
already gone rather than swapped a second time.

**Exit codes:** `0` listing printed, or release succeeded (including a re-release
of a lock that was already gone) · `1` usage error · `2` `--release` name matched
more than one lock (every candidate is named) · `3` `--release` name matched no
lock · `4` the target's recorded holder pid is alive and `--force` was not given
· `5` `--force` signalled the live holder but it still holds the lock after
`kill_grace_seconds`, or it could not be signalled. The last four refuse
dual-channel (`release_locks.ambiguous`, `release_locks.not_found`,
`release_locks.holder_alive`, `release_locks.holder_not_released`) and change
nothing; the two live-holder refusals name the holder's `holder`, `pid`, `op`
and `task`, and its lock file stays in place.

**Over HTTP, the two modes are two routes.** `GET /release-locks` is the
listing — the same provenance and verdict per lock, read-only, and it refuses
`422` if `release` or `force` is supplied so a GET can never free a lock.
`POST /operations/release-locks` with a body of `{"release": "<name>"}` (plus
`"force": true` to pass a live holder) is the release: the same name
resolution, and the same refusals reaching the caller as JSON `error`,
`message` and `remedy` with the exit code the CLI would have used. Both
routes and the script share one implementation,
`pgai_agent_kanban.ops.locks` (`list_locks` and `release_lock`), so the
three surfaces list, refuse and release identically.

**Reading locks in context** is `scripts/why.sh --project my-app --details`,
whose `== locks ==` section prints the same provenance for the locks naming one
project — a lock carrying no provenance record at all also gets an extra
`⚠️ VIOLATION` line there, the same evidence the `STUCK-LOCK` verdict above acts
on. Reach for `release-locks.sh` when you want the whole root-wide set, or when
you intend to free one.

---

## defer, undefer — arm and clear the deferral posture

Where HALT stops the chain, the deferral posture only changes *what* the chain
picks up: a deferred bug waits instead of opening a release of its own. The
founding rule, the four `[release]` keys, and the precedence order these verbs
sit on top of are in `team/SOP-core.md` under "Deferral Posture" — read the law
there; this section is the command surface. These two verbs set and clear the
highest precedence tier, the campaign file. Reading the whole chain back is
`why.sh --details`, one section above.

```bash
scripts/defer.sh   --project my-app --until v2.14.0   # arm the campaign
scripts/why.sh     --project my-app --details         # what posture is in force, and from where
scripts/undefer.sh --project my-app                   # clear the campaign early
```

**`defer.sh`** writes `projects/<name>/DEFER-LOW-BUGS` carrying the validated
`until=` target. A leading `v` on `--until` is optional and normalised, exactly
as `halt-after.sh --until` handles it. Arming a project that already carries a
campaign file overwrites the target — exactly one campaign file exists
afterwards, holding the most recently armed value. The write is atomic, and a
refusal writes nothing at all: the state on disk after a refused arm is
byte-identical to the state before it.

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | yes | Project to arm. Falls back to `$PGAI_PROJECT_NAME`. |
| `--until vX.Y.Z` | yes | The release the campaign runs until. Well-formed semver; a leading `v` is optional. |
| `--help`/`-h` | no | Print usage and exit 0. |

**`undefer.sh`** removes that file. It is the early exit, not the normal one:
the campaign is spent for you when a release at or past the `until=` target
ships, which is the promise you made when you armed it. Reach for `undefer.sh`
when you have changed your mind before then. It is idempotent — a project with
no campaign file reports there was nothing to clear and exits 0, because
clearing an already-clear posture is not an error.

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | yes | Project to clear. Falls back to `$PGAI_PROJECT_NAME`. |
| `--help`/`-h` | no | Print usage and exit 0. |

**Reading the posture back** is `scripts/why.sh --project my-app --details`.
The `== posture ==` section of that pane prints the resolved posture and names
the tier every value came from. Four tiers is exactly the number at which
reading the files yourself stops being reliable, and the value of that section
is that the answer it gives is the answer discovery actually acted on.

```
deferred_bugs=true (campaign file)
deferred_bug_level=low (campaign file)
defer_piggyback=true (default)
until=v2.14.0 (campaign file: /home/you/pgai_agent_kanban/projects/my-app/DEFER-LOW-BUGS)
```

Every tier reads as one of four words: `campaign file`, `project.cfg`,
`kanban.cfg`, `default`. With no campaign armed the last line reads
`until=(none) (default)`.

**Refusals.** Both verbs refuse dual-channel — a stable `error` key, a
plain-language `message`, a runnable `remedy` — and write nothing when they do.
The read side's refusals are in the `why.sh` section above.

| Refusal | Verbs | Cause |
|---|---|---|
| `defer.project_required`, `undefer.project_required` | both | No project named and no `$PGAI_PROJECT_NAME`. A posture is always per-project: its cfg tiers and its campaign file both live under one project's directory. |
| `defer.project_not_found`, `undefer.project_not_found` | both | The named project has no directory. Arming there would write a file nothing ever reads. |
| `defer.until_required` | `defer.sh` | No `--until`. A campaign always names the release it runs until — a file with no target is the silently-off state the design forbids. |
| `defer.until_malformed` | `defer.sh` | `--until` is not well-formed `vX.Y.Z`. Refused at write time so it never reaches disk. |

---

## deep-verify, cold-build-now — run the expensive verification tier

`deep-verify.sh` is the DEEP-VERIFY tier's umbrella verb: it runs the tier's
expensive suites for one project and appends one record to that project's
deep-verify ledger. Cold-build is the tier's only member today, so today the
verb means "rebuild the image flavors you select from scratch, under every
container engine on this host, and write down what happened." Budget roughly
thirteen minutes per run.

`cold-build-now.sh` is a named alias for exactly that call — same flags, same
work, same exit codes. Reach for it when "run the cold build now" is what you
mean; reach for `deep-verify.sh` when you mean the tier.

The tier does not run during RC verification. When it runs, why the freshness
of its result is itself a risk input, and what each verdict is worth all live in
`team/roles/CM.md` under "The DEEP-VERIFY tier (when the expensive gate runs)."

**Both verbs are operator-only.** Running the lane is the container tier, so
every command below carries `PGAI_CONTAINER_TIER=1` and runs from your own
shell, a crontab line you wrote, or the environment you started the API server
in. `--operator-approved` on the command line authorizes one run the same way.
Inside an agent wake neither counts, and the wake strips the variable before any
agent starts. An unauthorized or in-wake call is skipped, not refused — the verb
prints one line and exits 0:

```
deep-verify.sh: SKIPPED key=container_tier.operator_only reason="container tier is operator-only: ..." operator_command="PGAI_CONTAINER_TIER=1 bash <path>/deep-verify.sh --project my-app"
```

No lane runs and no ledger record is written, so the ledger keeps its last real
result and a skip is never reported as a pass. Each run also needs an image
selection; see `--image` in the table below.

```bash
# Run the lane now for one project, outside any RC, every declared image
PGAI_CONTAINER_TIER=1 scripts/deep-verify.sh --project my-app --image all

# The same run, under its narrow name
PGAI_CONTAINER_TIER=1 scripts/cold-build-now.sh --project my-app --image all

# The scheduled dispatch mode — what the weekly cron/pseudocron entry invokes.
# No --project: fans out over every active registered project.
PGAI_CONTAINER_TIER=1 scripts/deep-verify.sh --scheduled --image all
```

| Flag | Command | Required | Effect |
|---|---|---|---|
| `--project <name>` | both | yes, except with `--scheduled` alone | Project whose ledger the record is filed under. `deep-verify.sh` falls back to `$PGAI_PROJECT_NAME`; `cold-build-now.sh` wants the flag spelled out. |
| `--scheduled` | `deep-verify.sh` | no | Weekly off-hours dispatch mode. Self-gates on the project's ledger age and off-hours window before anything costly happens. With `--project`, evaluates that one project; without it, fans out over every active project in `projects.cfg`, passing `--image` and `--operator-approved` on to each. |
| `--image NAME` | both | yes, unless `kanban.cfg [release] container_images` is set | Image to build, from `team/config/container-images.txt`. Repeatable; `--image all` builds every declared image. Without the flag the `kanban.cfg` default applies; with neither, the verb lists the available images and exits 0 having built nothing. |
| `--operator-approved` | both | no | Operator authorization for this run, the same as `PGAI_CONTAINER_TIER=1`. Has no effect inside an agent wake. |
| `--help`/`-h` | both | no | Print usage and exit 0. Needs no authorization. |

**Exit codes.** `0` when the verdict is `built` or `could-not-attempt` and no
cold test failed — in both cases nothing wrong with the tree was found. `1`
when the verdict is `failed-to-build`; when the verdict is `built` but a
non-build cold test failed (the images built, and a bug naming the test is filed
or cited); when the verdict is `could-not-attempt` but a non-build cold test
failed (no image could be built, the lane exited 1, and a bug naming the test is
filed or cited); or when `--project` did not resolve to a registered project (no
record is written in that case). `3` when `--image` or the `kanban.cfg`
default names an image the manifest does not declare
(`container_tier.unknown_image`; nothing runs, no record is written). Three
outcomes exit 0 having run nothing and written no record: a `--scheduled`
invocation that was not due, a `SKIPPED` call that lacked authorization or came
from inside an agent wake, and a run with no image selected.

**Who reached for docker.** Every run, skip and image refusal appends one line to
`$KANBAN_ROOT/logs/docker-use.log`:

```
<UTC timestamp> <EVENT> actor=<operator|agent role> task=<task-id|-> script=<name> images=<a,b|-> [key=value ...]
```

A run writes `START` and then `FINISH`, which adds `duration=` and `exit=`. An
unauthorized call writes `SKIPPED`, and an undeclared image writes `REFUSED`
with a `reason=`. Agent attempts are logged with their role and task, so the log
shows who reached for docker, not only who ran it. The log is append-only and
never blocks: when it cannot be written, the verb warns once and carries on.
`scripts/why.sh --project my-app --details` ends with an `== docker use ==`
section showing the log's last line.

**Where the record goes.** `$KANBAN_ROOT/projects/<name>/deep-verify-ledger.md`,
one append-only block per run carrying the timestamp, the tree state exercised
(version and commit), the verdict, and the per-pair engine/flavor detail. It is
kanban process state, a sibling of `release-state.md`; it is never committed into
a dev tree. Reading it back is the `deep-verify-status` read verb
(`scripts/deep-verify-status.sh --project my-app`, or `GET /deep-verify-status`
on the REST API): the configured cadence, every entry, and the newest one,
through one shared resolver. It reads; it never runs the lane.

**A red run files its own bug.** A `failed-to-build` verdict files a bug into the
project's `bugs/` directory naming the failing engine/flavor pairs, or the
failing image-build test when no step line was captured. A `built` run whose
lane still failed a non-build cold test files one naming each failing test's
file and test id. So does a `could-not-attempt` run whose lane exited 1 for the
same reason. `built` needs at least one built pair, or no narrated skip, so a
run in which no pair built and pairs were narrate-skipped — a host with no
usable engine, where only the engine-free cold tests ran — records
`could-not-attempt`, and that record does not satisfy the pre-release check;
the engine-free test that failed is still a finding. In every case, an open or
running bug already covering the same failure — the same failing pairs, or the
same set of failing tests — is cited instead of filing a duplicate. Filing
touches nothing else — not `release-state.md`, not a task, not a queue, not a
HALT file — so a run that fires while an RC is open leaves that RC alone. Any
other `could-not-attempt` verdict files no bug: nothing was learned about
whether the images build.

### The scheduled trigger's per-project switch

Two `[release]` keys, set in a project's own `project.cfg` or kanban-wide in
`kanban.cfg`. Each resolves per project in the same order: the project's
`project.cfg` value, else the `kanban.cfg` value, else the default below. So
`deep_verify = off` in `kanban.cfg` turns the scheduled trigger off for every
project that does not set its own value. They govern the `--scheduled` trigger
only; on-demand runs and the pre-release check ignore both.

| Key | Accepted values | Default | Effect |
|---|---|---|---|
| `deep_verify` | `weekly`\|`off` (or `true`\|`false`) | `weekly` | `off` turns the weekly trigger off — a scheduled invocation for that project then narrates one line and exits 0, starting no lane and no container engine. The boolean spellings are aliases of the word spellings: `true`=`weekly`, `false`=`off`. |
| `deep_verify_offhours_window` | `HH:MM-HH:MM` (UTC) | `03:00-03:59` | The daily window in which a scheduled run may fire. Absent or blank in one file means the next one applies; a malformed value is treated the same way, with one warning line naming the file and the value. It never refuses. |

```ini
# projects/<name>/project.cfg — or kanban.cfg, for every project
[release]
deep_verify = weekly
deep_verify_offhours_window = 03:00-03:59
```

The default is opt-out rather than opt-in on purpose: a lane that runs only when
someone remembers to enable it is a lane that stops running. Set `off` for a
project that ships no container images. An alias is a recognized spelling, not a
guess: a `deep_verify` value outside the accepted set is never guessed into a
mode — in `project.cfg` the scheduled invocation refuses dual-channel
(`deep_verify.invalid_schedule_mode`) and runs nothing, and in `kanban.cfg` the
configuration load refuses, naming the file and the key.

Put both keys under `[release]`, as the block above shows — that is the only
section either one is read from, in either file. An INI key belongs to the
nearest section header above it, so a key appended at the end of the file, or
written under any other section, is never seen and the next source applies
instead: the `kanban.cfg` value, else the default. The resolver warns on stderr
when it finds a deep-verify key outside `[release]`, naming the file, the
section it landed in, `[release]`, and what applies instead; move the key under
`[release]` to make it take effect. The warning is advisory only — nothing
refuses and nothing exits non-zero.

**Wiring the weekly firing is an operator step on this lineage.** The
`--scheduled` mode is shipped and self-gating, but no deep-verify entry ships in
`team/scripts/cron-suggested.template.txt` or
`team/scripts/pseudocron.cfg.example` yet, so a fresh install schedules nothing
until you add one. Under cron, one weekly line covers every project, because
cron expresses the cadence directly in its day-of-week field:

```cron
# --- Deep-Verify tier (weekly, Sundays at 03:15) ---
15 3 * * 0 PGAI_CONTAINER_TIER=1 $KANBAN_ROOT/scripts/deep-verify.sh --scheduled --image all >> $KANBAN_ROOT/logs/cron-deep-verify.log 2>&1
```

Under pseudocron, whose job table matches minute-of-hour alone, the same entry
fires hourly and the script's own due-check supplies the "once a week" half —
every firing outside the off-hours window, or under a week since the last run,
costs nothing:

```
15  PGAI_CONTAINER_TIER=1 scripts/deep-verify.sh --scheduled --image all >> logs/cron-deep-verify.log 2>&1
```

Either way, no `--project`: one entry fans out over every active registered
project. Both parts of the line are load-bearing. The `PGAI_CONTAINER_TIER=1`
prefix is your authorization, written into an entry you own; without it every
due firing is `SKIPPED`, records nothing, and the pre-release freshness gate
later refuses promotion for lack of a fresh record. `--image all` is the
selection; drop it only if `kanban.cfg [release] container_images` is set,
because with neither a due firing lists the images and builds nothing. Both
cron and pseudocron run the line through a shell, so the prefix works as
printed.

---

## deep-verify-freshness-check — refuse promotion without a fresh green lane

`deep-verify-freshness-check.sh` is the pre-release gate: it reads a project's
deep-verify ledger and answers one question — does the record already there still
speak for the version you are about to promote to a public tag? It is read-only.
It writes nothing, and it never starts the lane on your behalf.

```bash
scripts/deep-verify-freshness-check.sh --project my-app --version v0.7.17
```

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | yes | Project whose ledger is read. Falls back to `$PGAI_PROJECT_NAME`. |
| `--version vX.Y.Z` | yes | The tag about to be promoted. Must be well-formed semver with the `v` prefix. |
| `--help`/`-h` | no | Print usage and exit 0. |

**Exit 0** means the most recent record is `built`, for exactly `--version`, and
no older than the freshness window — `PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS`,
default 7 days, matching the tier's weekly cadence. **Exit 1** covers every other
case, each with its own greppable key: `freshness_gate.no_record` (never run),
`freshness_gate.failed_to_build` and `freshness_gate.could_not_attempt` (the last
record was not green), `freshness_gate.stale_record` (green, but wrong version
and/or past the window), plus `freshness_gate.ledger_unreadable` and
`freshness_gate.unregistered_project` for a malformed ledger and an unregistered
project name. All speak the dual-channel error contract, so the remedy line names
the command to run next.

What each refusal means and what to do about it is the walkthrough in
[promotion-playbook.md](promotion-playbook.md), step 0 — this command exists to
be that step.

---

## cm-release --decide-only — the ship-policy decision, without the release

`--decide-only` runs the release script's preflight and its ship-policy matrix,
with the project's ship posture applied, prints the decision a full run would
reach, and stops. It writes nothing. Run it before a release to see whether the
RC would ship, under which Status, or which HALT it would stop on.

```bash
scripts/cm/release.sh --project my-app --decide-only
```

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | yes | Project whose Active RC is decided. Falls back to `$PGAI_PROJECT_NAME`. |
| `--decide-only` | yes | Stop after the ship-policy decision and write nothing. Cannot be combined with `--force`, `--reason` or `--overwatch-lane-b-ship`. |

**The output.** Narration goes to stderr. Stdout carries one line:

```
ship-policy: decision=<SHIP-…|HALT> posture=<matrix|always> trigger=<n|none> notes_status=<…>
```

| Field | Value |
|---|---|
| `decision` | The decision a full run would reach: `SHIP-FUNCTIONAL`, `SHIP-KNOWN-BUGS`, `SHIP-NON-FUNCTIONAL`, `SHIP-KNOWN-ISSUE`, or `HALT`. |
| `posture` | The project's resolved ship posture, after the remote-lane guard. |
| `trigger` | On `HALT`, the trigger number of the stop — `none` for the unrecognized-TESTER-state and no-row-matched stops, which have no number. On a ship, each trigger the `always` posture shipped through, comma-separated in the order they fired (`8,2`); `none` when nothing would have stopped. |
| `notes_status` | The release-notes `## Status` value the ship would stamp; `none` on `HALT`. |

On `HALT` the line is followed, also on stdout, by the would-be HALT's
dual-channel contract. Its `error:` line is the key a full run would write to
the project's HALT file.

Three examples. A clean RC on a `matrix` lane:

```
ship-policy: decision=SHIP-FUNCTIONAL posture=matrix trigger=none notes_status=FUNCTIONAL
```

A report that fires trigger 2, on a `matrix` lane (message and remedy
abbreviated here):

```
ship-policy: decision=HALT posture=matrix trigger=2 notes_status=none
error:   cm-release.trigger-2.systemic-risk-high
message: Trigger 2: TESTER report systemic_risk=high for <version> (task <tester-task>) and the finding is not clear to ship: …
remedy:  Read <report path> for the high-risk findings, fix or triage them, re-run TESTER, then re-invoke scripts/cm/release.sh.
```

The same report on an `always` lane:

```
ship-policy: decision=SHIP-NON-FUNCTIONAL posture=always trigger=2 notes_status=NON-FUNCTIONAL
```

**Exit codes.**

| Exit | Meaning | Stdout |
|---|---|---|
| `0` | The decision is a `SHIP-*` decision. | The decision line. |
| `3` | The decision is `HALT`. No HALT file is written. | The decision line, then `error:`, `message:` and `remedy:`. |
| `1` | No decision was reached: the same preflight failures a full run exits 1 on (no project context, no RC branch, a `release-state.md` that disagrees with the branch), or the override-flag refusal below. | Nothing. |

**It writes nothing.** No fetch, push or checkout; no commit, branch or tag; no
HALT file; no `release-state.md` change; no bug filed; no release-notes stamp;
no refusal-ledger record. Where a full run checks out the RC branch to read its
`release-notes/` for the trigger-8 pattern check, the decider reads them from
the dev tree when the RC branch is already checked out, and otherwise from a
scratch copy extracted under the framework temp directory and removed on exit.
Where a full run would file a bug or record an Auto-Ship Event, the decider
narrates on stderr what the full run would write. It reads local refs only:
on a lane that pushes to origin it validates the RC branch locally, as a
`push_to_remote = false` lane does.

**What it cannot see.** The decider stops at the end of the ship-policy step.
Every gate a full run reaches later — the pre-squash hook, the release-notes
stamp guard, the squash pollution guard, the squash and fidelity gate, the
pre-tag hook, tag and push failures — is out of its sight. Exit 0 means the
ship policy would ship this RC, not that the release will complete.

**It runs inside an agent wake.** Unlike `--force`, the decider is not refused
under `PGAI_AGENT_WAKE_ACTIVE=1`, so the same command answers an operator at a
shell and an automated caller alike.

**The refusal.**

| Refusal | What it means | What to do next |
|---|---|---|
| `cm-release.decide-only.override-flag-refused` | `--decide-only` was combined with `--force`, `--reason` or `--overwatch-lane-b-ship`. The decider reports the decision the unforced release reaches, so it takes no override flag. Refused before any project or repository state is read: exit 1, stderr only, nothing recorded | Drop the override flags and run the decider alone. The printed remedy is runnable as printed for the project you asked about: `Run the decider alone: release.sh --project <name> --decide-only`, where `<name>` is the `--project` you passed, or `PGAI_PROJECT_NAME` when that supplied it. With neither set, the remedy reads `Run the decider alone, replacing PROJECT_NAME with the project to decide for: release.sh --project PROJECT_NAME --decide-only`. The upper-case `PROJECT_NAME` placeholder can never match a real project, because project names are lower-case |

**How it relates to `--force`, posture by posture.** The decider always
reports the unforced decision. What `--force` can then do with it depends on
the posture:

| The decider reports | Under `matrix` | Under `always` |
|---|---|---|
| `decision=HALT trigger=1` | `--force --reason`, from an operator shell, passes it. | The same. |
| `decision=HALT trigger=2` or `trigger=3` | `--force --reason` passes it; the release ships `SHIPPED-BY-OPERATOR-OVERRIDE` and leaves an `## Operator Override` record. | Does not occur. The decider reports `SHIP-NON-FUNCTIONAL` with that trigger, and the full run ships that way with no flag, leaving an `## Auto-Ship Event` record. `--force` changes nothing. |
| `decision=HALT trigger=8` | Out of `--force`'s reach; the pattern is repaired, not passed. | Does not occur. The decider reports a ship with `8` in `trigger`; the full run files a bug naming the pattern and ships. |
| `decision=HALT trigger=none` | Out of `--force`'s reach; fix the TESTER input. | The same. |

The ship posture's keys, its remote-lane guard, and the records an auto-ship
leaves are in `docs/OPERATIONS.md`, "Ship posture — shipping through a
ship-policy stop."

---

## cm-release --force — pass a ship-policy stop, on the record

`cm/release.sh` refuses to ship a release the ship-policy matrix stopped. `--force`,
carrying a mandatory written `--reason`, passes three of those stops and only those
three. It is the operator's last say on a release decision: loud, single-invocation,
and permanently recorded in two places.

```bash
scripts/cm/release.sh --project my-app \
  --force --reason "Failing lane is a known upstream outage, not a defect in this tree."
```

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | yes | Project whose Active RC is being shipped. Falls back to `$PGAI_PROJECT_NAME`. |
| `--force` | no | Pass an in-scope ship-policy stop instead of halting on it. Never valid alone — `--reason` is mandatory whenever `--force` is present. |
| `--reason "<text>"` | with `--force` | Why this release must ship despite the stop. Free text, copied verbatim into the permanent record. |
| `--help`/`-h` | no | Print usage and exit 0. `--force` is deliberately not listed as a flag there — the output names it only to say `--decide-only` refuses it — and an unforced invocation, `--help` included, behaves exactly as it did before the flag existed. |

**What it reaches.** Exactly three stops — the matrix's judgement calls about *this*
release's own findings:

| Trigger | Stop |
|---|---|
| 1 | TESTER state is `BLOCKED` |
| 2 | `systemic_risk = high` and the finding is not clear to ship |
| 3 | `SHIP-WITH-SERIOUS-CONCERNS` with a finding at `Fix Effort: large` |

Every other stop is out of reach by enumeration, not by omission: the matrix's
fail-loud rows for untrustworthy input, the three-consecutive-`NON-FUNCTIONAL`
pattern guard, the release-notes stamp guards, the squash pollution guard, and
every mechanical gate outside the matrix — squash conflicts, the fidelity gate,
hook failures, push failures. Those say the machinery or the chain is in a bad
state, which an opinion about a finding's severity cannot fix.

On a project whose ship posture is `always`, triggers 2 and 3 never halt — the
release ships `NON-FUNCTIONAL` and records an `## Auto-Ship Event` instead — so
`--force` has nothing to pass there and leaves no `## Operator Override` record.
It still passes trigger 1. Run `--decide-only` first to see which stop, if any,
the release is heading for.

**The two records a forced ship leaves.** The release notes are stamped
`SHIPPED-BY-OPERATOR-OVERRIDE` under `## Status`, in place of the ordinary decision,
and the project's `release-state.md` gains an appended block:

```
## Operator Override
Timestamp: 2026-01-31T04:15:09Z
Version: vX.Y.Z
Stop Overridden: cm-release.trigger-2.systemic-risk-high
Reason: Failing lane is a known upstream outage, not a defect in this tree.
```

**It is an invocation, not a mode.** Nothing is written to `project.cfg` or any
file a later run reads back — the `release-state.md` block is a record of what
happened, not a switch. The very next invocation, run without the flag, applies
the matrix normally.

**Refusals.** An operator usually meets this command refused. There are four ways,
and the first three fire before any project or repository state is touched — before
the RC branch is even discovered — so a refused invocation ships nothing, writes no
HALT file, and leaves `release-state.md` and every release-notes file exactly as it
found them.

| Refusal | What it means | What to do next |
|---|---|---|
| `--reason` with no value — `ERROR: --reason requires a value`, then the usage block | The flag was the last argument, or the next token was swallowed as its value | Re-run with the reason quoted and immediately after the flag: `--reason "<text>"` |
| `cm-release.operator-force.reason-required` | `--force` was given with no `--reason`, or with an empty or whitespace-only one. An override with no attributed reason would make its own permanent record a lie, so the reason is refused rather than defaulted | Re-run with both flags: `scripts/cm/release.sh --project <name> --force --reason "<why this must ship>"` |
| `cm-release.operator-force.agent-wake-refused` | `--force` was passed while `PGAI_AGENT_WAKE_ACTIVE=1` — the positive signal an agent wake exports, never inferred from a TTY or a parent process name. The override is operator-only and must not be reachable from an automated invocation | Run it from an interactive operator shell, outside any wake or cron invocation. Unsetting the variable to get around the refusal defeats the guarantee the record depends on |
| No message of its own — the release HALTs exactly as it would have unforced | `--force` was aimed at a stop it does not reach; the flag was simply a no-op | Read the HALT's own remedy line and fix the cause. These stops are repaired, not passed. `team/roles/CM.md`'s trigger table names, per trigger, which is which |

The matrix itself, its rows, and the two records a known-issue ship leaves are in
`team/roles/CM.md`, "Ship policy."

---

## unwind-rc — fully unwind an in-flight release candidate

`unwind-rc.sh` rewinds an in-flight release candidate across all state stores. It
is the operator escape hatch when an RC must be abandoned entirely. HALT the
project (or the whole kanban) first; the unwind refuses without a HALT in place.

```bash
scripts/unwind-rc.sh --project my-app --key v0.7.17 --dry-run   # print the plan
scripts/unwind-rc.sh --project my-app --key v0.7.17             # execute; asks "Type 'yes' to continue"
scripts/unwind-rc.sh --project my-app --key v0.7.17 --confirm   # execute without the prompt
```

| Flag | Effect |
|---|---|
| `--project <name>` | Project name (must match `projects/<name>/` and `projects.cfg`). Required. |
| `--key vX.Y.Z` | RC version to unwind. Must match the project's Active RC unless `--force` is given. Required. |
| `--dry-run` | Print the unwind plan and exit without modifying state. |
| `--confirm` | Answer the confirmation prompt non-interactively, for a caller with no terminal on stdin. Bypasses no check. |
| `--force` | Skip the Active-RC-version mismatch check. On the CLI it also answers the prompt, so a scripted recovery can pass it alone. It does not bypass the HALT, project-existence, version-format or shipped-tag checks. |

Run `--dry-run` first to read the plan. Confirming and forcing are separate
decisions: `--confirm` says yes to the plan and skips nothing, and `--force` is
the only way past the Active-RC check. Over REST the split is strict — every
live call carries `confirm: true`, and `force` never stands in for it; see
`unwind-rc` under "Mutations (POST)" in [operator-api.md](operator-api.md).

---

## cancel-rc — abandon an RC branch and reset release-state

`cm/cancel-rc.sh` deletes an in-flight RC's branch and resets the project's
`release-state.md` so it records no Active RC. It leaves task folders, queue
files and intake files alone; to unwind those as well, use `unwind-rc` above.
The full footprint — what it touches and what it leaves — is in its generated
block under [Verb Reference](#verb-reference-generated).

```bash
scripts/cm/cancel-rc.sh --project my-app --key v0.7.17          # execute; asks "Proceed with cancellation of ...? [y/N]"
scripts/cm/cancel-rc.sh --project my-app --key v0.7.17 --yes    # execute without the prompt
```

| Flag | Effect |
|---|---|
| `--project <name>` | Project name. Required. |
| `--key vX.Y.Z` | RC version to cancel. Must match the project's Active RC unless `--force` is given. Required. |
| `--yes` | Answer the confirmation prompt. |
| `--force` | Skip only the Active-RC match check. The named RC's branch and per-RC record are cleaned up, and `release-state.md`, which then names a different RC, is left unchanged. It does not answer the prompt: without `--yes` the command still asks. |

Confirmation and `--force` stay separate here too. Over REST the confirmation
field is `yes: true`, required on every live call; `force` never stands in for
it.

---

## verify-rc-state — check a project's RC state for self-consistency (read-only)

`verify-rc-state.sh` walks a project's state stores and reports where they
disagree with each other: `release-state.md` against the dev tree's branches and
tags, queue entries against task folders on disk, each task's `status.md` state
against its queue marker, cross-task `## Prerequisites` references, and the bundle
invariants. It is the companion to `unwind-rc.sh` — run it after an unwind, and
any time a lane's state looks wrong — and it is strictly read-only.

**Its project is a positional argument, not a `--project` flag.** This is the one
operator verb that takes it that way; typing `--project my-app` here is an
undeclared flag and refuses.

```bash
scripts/verify-rc-state.sh my-app              # warnings and errors only
scripts/verify-rc-state.sh my-app --verbose    # every check, including the passes
scripts/verify-rc-state.sh my                  # abbreviation: resolves to my-app
```

| Argument / flag | Required | Effect |
|---|---|---|
| `<project>` | yes | Positional. Project name, resolved by exact match, then by unambiguous hyphen-boundary prefix — the same exact-then-prefix rule every other operator key uses. |
| `--verbose` | no | Print every check, `OK` included. Without it, only `WARN` and `ERROR` findings and the summary line are printed. |
| `--help`/`-h` | no | Print usage and exit 0. |

Findings print as `[OK]`, `[WARN]`, or `[ERROR]` lines, closing with a summary:

```
verify-rc-state: 0 errors, 14 warnings, 89 ok
```

**Exit codes:** `0` no `ERROR` findings — warnings do not affect the exit code ·
`1` one or more `ERROR` findings · `2` pre-flight failure: no project named, a
name that resolves to no registered project, or an undeclared flag. A missing
or unresolvable project name refuses with a machine key and a runnable remedy,
and prints no findings and no summary line — an unresolved name gets no report
rather than an empty one.

The unwind-and-verify playbook that pairs this verb with `unwind-rc.sh` is in
[OPERATIONS.md](OPERATIONS.md), "Verifying clean state with verify-rc-state.sh."

---

## overwatch-attach-fix-rewrite — unstick a declined OVERWATCH Lane A recovery

`overwatch-attach-fix-rewrite.sh` appends a dated, OVERWATCH-attributed
`## Fix (OVERWATCH rewrite)` block to a bug file: it names the approach that was
implemented and regressed as FORBIDDEN, states the correct direction on its own
terms, and reproduces the ground-truth references verbatim. That block is step
(a) of OVERWATCH's Lane A recovery playbook (`team/roles/OVERWATCH.md`,
"Lane A — bad-fix regression").

**When you reach for it.** A Lane A classification declines its own drill when
the blamed bug does not yet carry that block. The decline is visible as an
`## OVERWATCH Decision` event in the project's `release-state.md` carrying the
key `overwatch.lane-a-rewrite-missing`, and it is not self-clearing: steps
(b)–(e) — unwind, cancel, delete the bundle document, clear the ship-policy HALT
— do not run, and every later firing declines the same way until the rewrite is
attached. Run this verb to attach it. The decline writes no ledger entry, so it
never spends Lane A's single attempt; the next OVERWATCH firing re-classifies
the block and runs the drill.

The two judgment calls are yours. `--forbidden-approach` and
`--correct-direction` are never derived automatically — the whole point of the
block is that a human (or a reasoning-tier agent) has decided which fix was
actually wrong and which is right.

```bash
scripts/overwatch-attach-fix-rewrite.sh --project my-app --kanban-root "$KANBAN_ROOT" \
  --bug-id BUG-NNNN \
  --forbidden-approach "<what was implemented and caused the regression>" \
  --correct-direction "<the correct fix, stated on its own terms>" \
  --ground-truth-ref "<verbatim reference>"
```

| Flag | Required | Effect |
|---|---|---|
| `--project <name>` | yes | Project name as registered in `projects.cfg`. Its `bugs/` directory is where the bug is resolved. |
| `--kanban-root <path>` | yes | Kanban root path. |
| `--bug-id BUG-NNNN` | yes | Short bug id to resolve within the project's `bugs/` directory. Use the id the Lane A decline blamed. |
| `--forbidden-approach "<text>"` | yes | Names, unambiguously, the approach that was implemented and produced the regression. |
| `--correct-direction "<text>"` | yes | States the correct fix on its own terms, not merely the opposite of the forbidden approach. |
| `--ground-truth-ref "<ref>"` | at least one | A verbatim reference the correct direction depends on — a git ref, the exact shipped text being changed. Repeatable; pass the flag once per reference. |
| `--timestamp <iso8601>` | no | ISO 8601 UTC timestamp override. Tests only; defaults to the current time. |
| `--help`/`-h` | no | Print usage and exit 0. |

**Exit 0** means the block was appended. **Exit 1** covers bad input, no bug file
matching `--bug-id`, and the fail-loud preconditions — a bug file with no
existing `## Fix` section, an empty `--forbidden-approach`, no
`--ground-truth-ref`. A refused invocation prints its reason to stderr and
writes nothing, so the bug file is left exactly as it was found.

The append never edits in place: the original `## Fix` section is preserved
above the new block, a timestamped copy of the file lands under
`projects/<name>/overwatch/backups/`, and the action is recorded in that
project's OVERWATCH action log. Nothing guards against a second run — it appends
another block — and one attached rewrite already satisfies the precondition.

---

## remove-project — unregister a project, or delete it with --force

`remove-project.sh` takes a project out of the kanban. By default it only
unregisters: the project's entry leaves `projects.cfg`, and `projects/<name>/`
stays on disk, so `add-project` can register it again. `--force` also deletes
the project directory, and that cannot be undone.

```bash
scripts/remove-project.sh --project my-app --dry-run   # print the plan
scripts/remove-project.sh --project my-app             # unregister; keep projects/my-app/
scripts/remove-project.sh --project my-app --force     # unregister and delete projects/my-app/
```

| Flag | Effect |
|---|---|
| `--project <name>` | Project name. Not resolved by prefix, so a project that is no longer registered can still be named. Required. |
| `--force` | Also delete `projects/<name>/`. Does not bypass the Active-RC refusal. |
| `--dry-run` | Print the plan and exit without changing anything. |

**A project with an RC in flight is never removed.** When the project's
`release-state.md` records an Active RC, or its Active RC field does not
validate, the command refuses before changing anything: exit 2,
`remove_project.active_rc` (or `remove_project.release_state_invalid`).
`--force` does not get past it, and neither does `--dry-run`, which refuses
the same way. Ship or cancel the RC first, then re-run.

Unregistering a project that is already unregistered succeeds, so the default
form can be repeated safely. Over REST, `force: true` additionally requires a
`confirm` string equal to `remove-project <project>`; see `remove-project`
under "Mutations (POST)" in [operator-api.md](operator-api.md).

---

## Operator tools — reports, recovery, configuration and housekeeping

The scripts in this section are operator tools that are not census verbs, so
none of them has a block in the generated reference below. A few front a
read-only REST endpoint, and their entries say which. Each entry gives the
tool's purpose, a usage line naming every flag it accepts, and an example you
can run as printed once `shell-env` is sourced. A value marked *example* — a
project name such as `my-app`, a release tag, a timestamp, a path — stands in
for your own.

Tools that delete or overwrite something say exactly what they change, and
show the safe form first: a dry run where the tool has one, otherwise the
command that inspects without writing. Run the safe form, read its output, then
run the real one.

### Reports and inspection

#### agent-timing.sh — average wake time per role

Reads the wake logs' `elapsed=` lines and prints a plain-text table of average
wake time per agent role and project: role, project, average seconds, wake
count, total seconds. Read-only.

```text
Usage: agent-timing.sh [--days N] [--all-time] [--kanban-root <path>] [--help]
```

- `--days N` — only wakes from the last N days. Default: 7.
- `--all-time` — every wake in the logs, whatever its age.
- `--kanban-root <path>` — read a different kanban root than
  `$PGAI_AGENT_KANBAN_ROOT_PATH`.

```bash
$KANBAN_ROOT/scripts/agent-timing.sh --days 30
```

#### project-summary.sh — historical audit report for a project

Reads a project's bugs, priorities, requirements, release notes and task
status files and prints one report: version and release history, requirements
shipped and queued, bug counts by status and severity, priorities, bundles and
recent activity. Read-only. With `--llm` it sends each item to the Anthropic
API for a prose summary, which needs `ANTHROPIC_API_KEY` and costs tokens; the
default offline mode builds every one-liner from file metadata.

```text
Usage: project-summary.sh [--project <name>|all] [--days N] [--brief] [--all] [--llm] [--format text|md|json] [--output FILE] [--kanban-root <path>] [--help]
```

- `--project <name>` — the project to report on; required when more than one
  is registered. `--project all` adds one section per project plus totals.
- `--days N` — limit the "recent" sections to the last N days.
- `--brief` — counts only. `--all` — no truncation in any section.
- `--format` — `text` (default), `md` or `json`. `--output FILE` writes there
  instead of stdout.

```bash
$KANBAN_ROOT/scripts/project-summary.sh --project my-app --brief   # example project  # doc-lint: skip
```

#### verdict-report.sh — the `why` verdicts as key=value blocks

Prints the same verdict `why.sh` reaches, as machine-readable `key=value`
blocks — one per project, separated by a blank line — instead of `why.sh`'s
sentence. It and `GET /verdicts` resolve through the same function, so a script
reading this output and a REST caller always agree. Read-only.

```text
Usage: verdict-report.sh [--project <name>] [--details] [--help]
```

- No flags — the block of the project `$PGAI_PROJECT_NAME` names, when it is
  set. Only when neither `--project` nor `$PGAI_PROJECT_NAME` is given, every
  registered project, in `projects.cfg` priority order.
- `--project <name>` — that project's block only. A registered project with no
  directory on disk gets an `error=project directory not found` block rather
  than a false `IDLE`.
- `--details` — also print one block per release lock, queue-tail entry and
  pending requirements bundle, plus one posture block. Needs a project:
  `--project`, or `$PGAI_PROJECT_NAME` when `--project` is omitted. With
  neither set it refuses and exits `1`.

```bash
$KANBAN_ROOT/scripts/verdict-report.sh                              # $PGAI_PROJECT_NAME's block if set, else every project's
$KANBAN_ROOT/scripts/verdict-report.sh --project my-app --details   # example project
```

#### collect-reasoning-traces.sh — bundle an agent's reasoning traces

Gathers the reasoning-trace files one agent type wrote to the per-project
training corpora (`projects/<name>/logs/training/<agent-type>/`) and prints
them, newest first, as one markdown bundle on stdout, each trace headed by its
project, agent type, filename and timestamp. Read-only; an empty result is not
an error.

```text
Usage: collect-reasoning-traces.sh <agent-type> [--last N] [--project <name>] [--help]
```

- `<agent-type>` — `coder`, `cm`, `tester`, `writer`, `pm` and so on;
  case-insensitive.
- `--last N` — the N most recent traces. Default: 10.
- `--project <name>` — one project only. Default: every registered project.

```bash
$KANBAN_ROOT/scripts/collect-reasoning-traces.sh coder --last 5 > coder-traces.md
```

#### log-filter.sh — colour and de-noise a log for live reading

A filter for watching a wake log: it drops empty and duplicate adjacent lines
and colours errors and `BLOCKED` red, warnings and `WORKING` yellow, `DONE`
green and `WAITING` dim. It reads stdin or a log file named as its argument and
writes to stdout. `TERM=dumb` turns colour off; `PGAI_LOG_FILTER_VERBOSE=1`
turns the noise filter off.

```text
Usage: log-filter.sh [--kanban-root <path>] [--help] [<logfile>]
```

Any other `--` option is ignored rather than refused.

```bash
tail -n 200 -f "$(ls -t "$KANBAN_ROOT"/logs/agents/*.log | head -1)" | "$KANBAN_ROOT/scripts/log-filter.sh"
```

#### list-stale-requirements.sh — find orphaned requirements files

Lists requirements files whose target version is at or below the project's
live version and which have no matching release notes — files that never
shipped and never will as they stand. Each one prints with a recovery hint: the
other project it probably belongs to, when one matches, or deletion if it is
obsolete. Bundle files are never listed. Read-only; moving or deleting a file
it names is your call.

```text
Usage: list-stale-requirements.sh [--project <name>] [--kanban-root <path>] [--no-color] [--help]
```

`NO_COLOR` in the environment also turns colour off.

```bash
$KANBAN_ROOT/scripts/list-stale-requirements.sh --no-color
```

### Quarantined intake

The discovery pipeline moves a bug or priority file it keeps rejecting into the
project's `rejected/` directory, `projects/<name>/rejected/`, beside a
`<filename>.reason` sidecar recording why and where it came from. The hidden
`priority/.rejected/`, `bugs/.rejected/` and `requirements/.rejected/`
directories are the legacy location, used only when discovery has no project
name. The two tools below find and restore quarantined files in either
location; [quarantine-recovery.md](quarantine-recovery.md) is the full guide.

#### list-rejected.sh — list quarantined intake files

Prints, per project and directory, each quarantined file with its quarantine
time and the reason it was rejected, followed by the `recover-rejected.sh`
line that restores it. Read-only. Over REST the same listing is
`GET /rejected`.

```text
Usage: list-rejected.sh [--project <name>] [--help]
```

Without `--project` it covers every project. `NO_COLOR=1` turns colour off.

```bash
$KANBAN_ROOT/scripts/list-rejected.sh --project my-app   # example project
```

#### recover-rejected.sh — return a quarantined file to its queue

Moves one quarantined file back to the intake directory it came from and clears
that file's entry in the rejection counter, so discovery considers it afresh.
It searches `projects/<name>/rejected/` first, then the legacy
`priority/.rejected/`, `bugs/.rejected/` and `requirements/.rejected/`
directories. A file in `rejected/` goes back to the intake directory its
`.reason` sidecar records or, with no usable sidecar, to `bugs/` for a `BUG-`
name and `priority/` for a `PRIORITY-` name; a file in `<dir>/.rejected/` goes
back to `<dir>/`. When the origin cannot be told, the script refuses with
`recover_rejected.origin_unknown` and moves nothing.

```text
Usage: recover-rejected.sh --project <name> --file <filename> [--rename <new-name>] [--help]
```

- `--file` — the file's base name, exactly as `list-rejected.sh` prints it.
- `--rename` — restore it under a corrected name, given as a plain base name
  (no `/`, not `.` or `..`); any other value is refused with
  `recover_rejected.rename_not_basename` and nothing moves. Without it the file
  goes back under the name that was rejected, and the next discovery run
  rejects it again; the script never guesses a name for you.

```bash
$KANBAN_ROOT/scripts/recover-rejected.sh --project my-app \
  --file PRIORITY-7-faster-boot.md --rename PRIORITY-0007-faster-boot.md   # example project and filenames
```

### Waking an agent and the workspace container

#### wake.sh — run one task for one agent, now

Runs a wake for one agent that processes at most one task and then exits. It
is `wake-batch.sh` with `--max-tasks=1` put in front of your arguments, so the
same HALT, lock and queue rules apply as on a scheduled firing; it runs in the
foreground until that task finishes. To fire a wake and return at once, use
the `wake-now` verb (in the generated reference below) instead.

```text
Usage: wake.sh --agent=<pm|coder|writer|tester|cm|po> [--max-tasks=N] [--help]
```

`--max-tasks=N` raises the one-task limit. A bare agent name (`wake.sh coder`)
still works but prints a deprecation warning.

```bash
$KANBAN_ROOT/scripts/wake.sh --agent=coder
```

#### container-shell.sh — interactive shell in the workspace container

Starts an interactive login shell in the kanban workspace container, with
`$HOME` and the kanban root bind-mounted at the same paths inside it. The
engine is `kanban.cfg [docker] docker_exe` (docker by default, podman when
set). It runs the image you name and never builds one; images are built from
the `docker/` directory of the kanban repository checkout.

```text
Usage: container-shell.sh [-t|--image-tag <tag>] [--mount <host-path>:<container-path>] [--kanban-root <path>] [--help]
```

- `--image-tag` — the image to run. Default: `pgai-agent-kanban-debian:latest`.
- `--mount` — an extra bind mount; repeat it for more than one.

```bash
$KANBAN_ROOT/scripts/container-shell.sh --image-tag pgai-agent-kanban-debian:latest
```

### Releases and pushing

#### cm/finalize-release.sh — push a finished release by hand

The operator's manual publish step after CM has tagged a release. It commits
any release-notes polish that arrived after the release ran (together with a
regenerated `CHANGELOG.md`), then pushes the main branch and the release tag,
and creates a GitHub release when the `gh` CLI is installed and signed in. It
asks for confirmation before pushing. Run it from your own terminal, not from
inside an agent session.

It publishes only for a project registered in `projects.cfg` whose
`project.cfg` names `dev_tree_path` and does not set `push_to_remote = false`;
any other project finalizes locally and contacts no remote. The remote is the
`GIT_REMOTE_NAME` environment variable when set; otherwise the project's own
`project.cfg` `git_remote_name`; otherwise `origin` when that key is absent or
blank. An invalid `git_remote_name` stops the script before any git command,
refused `git_remote_name.invalid` with a remedy naming the fix. Set
`GIT_REMOTE_NAME` on the command line only to override the project's
configured remote for one run.

**Late release-notes polish (Step 2b).** When `release-notes/<version>.md`
has uncommitted changes that arrived after `cm/release.sh` ran, the script
commits that polish together with a regenerated `CHANGELOG.md` and moves the
local release tag onto the new commit, so the tag carries the polished notes.
With no pending polish the step does nothing. Before staging anything it
refuses, committing nothing, in three cases:

- HEAD in the dev tree is not the main branch
  (`cm-finalize-release.polish-off-main`): the commit would land on a branch
  the push does not carry. The remedy checks out the main branch and re-runs.
- The tag cannot follow the polish (`cm-finalize-release.polish-would-strand-tag`):
  the tag is not on the main branch's tip, or — for a project that pushes — it
  is already on the remote, so moving it would change a published release.
  The remedy saves the polish as a patch, discards it from the working tree,
  and re-runs; polish belongs before `cm/release.sh`, or in the next release.
- The `CHANGELOG.md` render fails (`cm-finalize-release.changelog-render-failed`).
  The render writes to a scratch file and replaces `CHANGELOG.md` only on
  success, so a failed render leaves the old file byte-for-byte and nothing
  is committed.

```text
Usage: cm/finalize-release.sh [--project <name>] [-y|--yes] [<version-tag>] [--help]
```

- `--project` — falls back to `$PGAI_PROJECT_NAME`; with neither, it refuses.
- `-y`/`--yes` — skip the confirmation prompt.
- `<version-tag>` — the release tag exactly as it exists locally, branch prefix
  included (`ai_v1.5.0` under the `ai_` prefix). Omitted, it uses the most
  recent release tag on the main branch.

```bash
$KANBAN_ROOT/scripts/cm/finalize-release.sh --project my-app ai_v1.5.0   # example project and tag
GIT_REMOTE_NAME=upstream $KANBAN_ROOT/scripts/cm/finalize-release.sh --project my-app   # example remote name
```

#### cm/push-watchdog.sh — push what a release left unpushed

For every registered project (or one), pushes local main-branch commits the
remote lacks and release tags (`<branch_prefix>vX.Y.Z`) missing from the
remote, each tag by its explicit ref; no other tag is ever pushed. It uses each
project's `git_remote_name` (`origin` when absent or blank). It skips a
halted project, a local-only project (`push_to_remote = false`) and one it
cannot resolve to a dev tree — those last two with one log line each — before
running any git command. Every action and failure is logged to
`projects/<name>/logs/cm-push-watchdog.log`, and it always exits 0, so a
network failure never mails you from cron. The shipped crontab templates do
not include it; add a line yourself if you want it.

```text
Usage: cm/push-watchdog.sh [--dry-run] [--project <name>] [--help]
```

```bash
$KANBAN_ROOT/scripts/cm/push-watchdog.sh --dry-run --project my-app   # example project; report only
$KANBAN_ROOT/scripts/cm/push-watchdog.sh --project my-app             # push
```

#### regenerate-changelog.sh — rebuild a CHANGELOG from release notes

Rebuilds a `CHANGELOG.md` from every `vX.Y.Z*.md` file in the kanban
project directory's `projects/<name>/release-notes/`, newest version first,
each release headed with its `**Release Date:**`. It writes
`projects/<name>/CHANGELOG.md` unless `--output` names another path, and stops
with an error when that `release-notes/` directory does not exist. It does not
read or write the project's dev tree: the repository's own `CHANGELOG.md` is
rendered by CM at release time.

```text
Usage: regenerate-changelog.sh --project <name> [--output <path>] [--help]
```

```bash
$KANBAN_ROOT/scripts/regenerate-changelog.sh --project my-app --output ./CHANGELOG-preview.md   # example project
```

### Configuration backup and restore

#### export-kanban-config.sh — archive the root configuration

Writes a `.tar.gz` of the kanban root's own configuration — `kanban.cfg`,
`projects.cfg`, `shell-env` and `active-provider`, whichever are present — plus
a `MANIFEST` naming the kanban version and what was included. It reads those
files and changes none of them. The archive is created mode 0600. The
`secrets` file is left out unless you pass `--include-secrets`, which prints a
warning: an archive made that way holds credentials, so move it securely and
never commit it.

```text
Usage: export-kanban-config.sh [--file <path>] [--include-secrets] [--help]
```

Without `--file` the archive is `./kanban-config-export-<UTC timestamp>.tar.gz`.

```bash
$KANBAN_ROOT/scripts/export-kanban-config.sh --file "$HOME/kanban-config.tar.gz"   # example path
```

#### import-kanban-config.sh — restore the root configuration

Restores the files an `export-kanban-config.sh` archive's `MANIFEST` lists into
the kanban root (`secrets` only when the archive includes it). When it writes
`shell-env` it tells you to re-source it.

**What it changes.** It writes each listed file into the kanban root. Without
`--force` it first checks every destination, and if any already exists it
lists them all and exits 1 having written nothing; on a root where none exists
it restores them straight away. `--force` overwrites existing files, copying
each one to `<file>.bak-<UTC timestamp>` first. There is no dry-run flag: list
the archive with `tar` to see what it holds, and run without `--force` to see
what it would overwrite.

```text
Usage: import-kanban-config.sh --file <archive> [--force] [--help]
```

```bash
tar -tzf "$HOME/kanban-config.tar.gz"                                                # example path; list contents
$KANBAN_ROOT/scripts/import-kanban-config.sh --file "$HOME/kanban-config.tar.gz"           # restore; refuses to overwrite
$KANBAN_ROOT/scripts/import-kanban-config.sh --file "$HOME/kanban-config.tar.gz" --force   # overwrite, backing up each file
```

### Cleanup and disk hygiene

#### cleanup/cleanup_logs.sh — prune trivial agent logs

Deletes `*.log` files under `$KANBAN_ROOT/logs/agents` that are both under
1024 bytes and more than a day old — wake logs with nothing in them. The
suggested crontab runs it daily.

**What it changes.** With no arguments it deletes; there is no confirmation.
`--dry-run` (or `DRY_RUN=1` in the environment) lists each file it would
delete and deletes nothing.

```text
Usage: cleanup/cleanup_logs.sh [--dry-run] [--help]
```

```bash
$KANBAN_ROOT/scripts/cleanup/cleanup_logs.sh --dry-run   # list only
$KANBAN_ROOT/scripts/cleanup/cleanup_logs.sh             # delete
```

#### cleanup/purge-old-files.sh — retention purge

Removes files past their retention window: DONE and WONT-DO task folders,
rotated log archives, shipped requirements bundles, and closed bug and priority
files. A task the active RC's bundle references is never purged, and BLOCKED
tasks are kept unless you opt in.

**What it changes.** Nothing by default: without `--apply` it is a dry run that
tags each eligible item `[WOULD PURGE]`. With `--apply` it deletes them, and
`--archive` first tars everything it is about to delete to
`$KANBAN_ROOT/archive/purge-<timestamp>.tar.gz`, aborting the whole purge if
the tar fails.

```text
Usage: cleanup/purge-old-files.sh [--days N] [--tasks-days N] [--logs-days N] [--bundles-days N] [--bugs-days N] [--priorities-days N] [--project <name>] [--include-blocked] [--apply] [--archive] [--verbose] [--quiet] [--help]
```

- `--days N` — retention for every category. Default: 30.
- `--tasks-days`, `--logs-days`, `--bundles-days`, `--bugs-days`,
  `--priorities-days` — per-category retention. Defaults: 30, 7, 30, 30, 30.
- `--project <name>` — one project only. Default: every project.
- `--include-blocked` — purge BLOCKED task folders too.
- `--verbose` — show every file considered. `--quiet` — the summary only.

```bash
$KANBAN_ROOT/scripts/cleanup/purge-old-files.sh --project my-app                   # example project; dry run
$KANBAN_ROOT/scripts/cleanup/purge-old-files.sh --project my-app --archive --apply  # archive, then delete
```

#### cleanup-stale-artifact-only-branches.sh — delete empty feature branches

Finds local feature branches left behind by artifact-only tasks — tasks whose
whole deliverable sits in the task folder — that are DONE and carry no commit
of their own beyond the merge-base with the RC branch. Main, develop and RC
branches, prefixed or not, are never candidates.

It works on the git repository in the **current directory**, so run it from
inside the project's dev tree. `--project-root` names the kanban project
folder holding the tasks to scan; without it the script uses
`$PGAI_PROJECT_ROOT`, then the kanban root. The RC branch is the newest
`<branch_prefix>rc/*` branch, then the newest `rc/*`; `--rc` names one.

**What it changes.** Nothing by default: it prints the candidates. With
`--apply` it deletes each one with `git branch -D`. It refuses `--apply`
(exit 2) when a candidate is checked out in a worktree, and exits 3 when it
cannot find an RC branch. It never pushes or fetches.

```text
Usage: cleanup-stale-artifact-only-branches.sh [--apply] [--kanban-root <dir>] [--project-root <dir>] [--rc <branch>] [--help]
```

```bash
cd /srv/checkouts/my-app   # example: the project's dev tree
$KANBAN_ROOT/scripts/cleanup-stale-artifact-only-branches.sh --project-root "$KANBAN_ROOT/projects/my-app"           # list
$KANBAN_ROOT/scripts/cleanup-stale-artifact-only-branches.sh --project-root "$KANBAN_ROOT/projects/my-app" --apply   # delete
```

### Undoing an OVERWATCH action

#### overwatch-revert.sh — restore files from an OVERWATCH backup

Every OVERWATCH action that edits a file first copies it to
`projects/<name>/overwatch/backups/<timestamp>/`. This tool puts the files from
one firing's backup back.

**What it changes.** For the timestamp you name, it copies each backed-up file
back over the file at its original path, newest action first, so a file two
actions touched ends at its content before the firing, and it appends a revert
record to the project's OVERWATCH `actions.log`. It never deletes a backup, and
running it twice is a logged no-op. `--list` and `--show` only read, and
`--dry-run` prints what would be restored without writing.

```text
Usage: overwatch-revert.sh --project <name> [--kanban-root <path>] [--dry-run] <timestamp>
       overwatch-revert.sh --project <name> --list
       overwatch-revert.sh --project <name> --show <timestamp>
       overwatch-revert.sh --help
```

`--project` may be left out when `OVERWATCH_PROJECT` is set.

```bash
$KANBAN_ROOT/scripts/overwatch-revert.sh --project my-app --list                            # example project
$KANBAN_ROOT/scripts/overwatch-revert.sh --project my-app --show 2026-05-10T12:00:00Z       # example timestamp
$KANBAN_ROOT/scripts/overwatch-revert.sh --project my-app --dry-run 2026-05-10T12:00:00Z
$KANBAN_ROOT/scripts/overwatch-revert.sh --project my-app 2026-05-10T12:00:00Z              # restore
```

### Workflow plugins

#### validate-workflow.sh — check a workflow plugin before enabling it

Runs the four plugin contract checks against one workflow type: the manifest
(`workflow.cfg` present, required fields set, `status = ready`), every `wf_*`
hook defined in `workflow.sh`, no hook still carrying the scaffold's
`NOT IMPLEMENTED` stub, and valid capability values. Run it after writing a
plugin and before relying on it. Exit 0 means it passes; exit 1 prints the
check that failed and why. Read-only.

```text
Usage: validate-workflow.sh --type <name> [--workflows-dir <dir>] [--help]
```

`--workflows-dir` defaults to `$KANBAN_ROOT/workflows/`.

```bash
$KANBAN_ROOT/scripts/validate-workflow.sh --type release
```

### One-time migrations

Each of these converts state an older install left behind. Both are safe to
re-run, and both have a dry run.

#### migrate/projects-cfg.sh — convert a colon-format projects.cfg to INI

**What it changes.** When `$KANBAN_ROOT/projects.cfg` is still in the old
colon format, it copies it to `projects.cfg.colon-format-backup` and rewrites
`projects.cfg` as INI in place, keeping each project's priority and colour.
An INI file is left untouched. It refuses (exit 2) when that backup already
exists, unless you pass `--force`, which overwrites the backup.

```text
Usage: migrate/projects-cfg.sh [--force] [--dry-run] [--help]
```

```bash
$KANBAN_ROOT/scripts/migrate/projects-cfg.sh --dry-run   # preview
$KANBAN_ROOT/scripts/migrate/projects-cfg.sh             # convert
```

#### migrate/bug-status-done.sh — close items stuck at `running` after they shipped

**What it changes.** For every requirements bundle whose target version has a
tag in the project's dev tree, it rewrites `## Status` from `running` to
`done` in each bug or priority file the bundle lists. Files at any other
status, and files no shipped bundle lists, are left alone. Each project needs
`dev_tree_path` set in its `project.cfg`. Its usage lives in its header
comment.

```text
Usage: migrate/bug-status-done.sh [--dry-run]
```

```bash
$KANBAN_ROOT/scripts/migrate/bug-status-done.sh --dry-run   # preview  # doc-lint: skip
$KANBAN_ROOT/scripts/migrate/bug-status-done.sh             # rewrite  # doc-lint: skip
```

---

## Internal checks

`install.sh` copies every script under the kanban's `scripts/` tree into
`$KANBAN_ROOT/scripts/`, so the install also carries the checks, runners and
helpers the release machinery and the agents use. They are listed here so you
can tell them apart from the operator tools above. Day-to-day operation needs
none of them; the two container-tier runners are the only ones that wait on
you, because agents never run that tier. In the "Who runs it" column, *unit runner* and
*integration runner* are the pre-flight stages of `run-unit-tests.sh` and
`run-integration-tests.sh` (both run on every TESTER verification), and
*pre-merge* is `run-unit-tests.sh --pre-merge`, which CODER and WRITER run
before merging.

**Gates and checks**

| Script | What it checks | Who runs it |
|---|---|---|
| `audit_coding_standards.sh` | One check per directive in the coding standard, reusing the lints that already cover a directive; writes a PASS / FINDINGS / MANUAL-REVIEW-REQUIRED verdict per directive to a results file. | On demand, for a standards audit; no runner calls it. |
| `check_breakage_class.py` | That every Python file parses, every shell script tokenizes, and every first-party import resolves; a failure stops the test run, because results from a tree that cannot compile carry no signal. | Unit and integration runners. |
| `check_docs_citations.py` | That every `docs/` page the installed governance files cite is listed in the docs manifest, so no citation dangles on an installed root. | Unit runner, pre-merge, and CODER/WRITER before merging. |
| `coder-stale-literal-check.sh` | Which tests and recorded fixtures still contain a literal that a diff changed in production code. | CODER, for the `## Stale Literal Risks` section of its status. |
| `generate-icd.sh` | Not a check: regenerates the REST interface description (`docs/api/icd.json`) from the API's own schema. | CODER, after changing a REST request or response model; the ICD freshness lint regenerates the same way to compare. |

**Lints**

| Script | What it checks | Who runs it |
|---|---|---|
| `lint_api_parity.py` | Every operator verb in the census against the tree: each declared REST twin, script and flag/body model exists and matches. | Unit runner. |
| `lint_api_route_docs.py` | That every REST route has a hand-written row and a curl example in `docs/operator-api.md`, and that the page's list of routes that start a process matches the verb census. | Unit runner. |
| `lint_api_route_impl.py` | That a verb's REST route and its script call the same Python function, and that the count of shell-only verbs never rises. | Unit and integration runners, pre-merge. |
| `lint_block_record_sites.py` | Code that sets a task `BLOCKED` without writing its `## Block Record`. | Unit runner. |
| `lint_config_key_coverage.py` | That the `kanban.cfg` and `project.cfg` keys the code reads, the keys their templates (and the `project.cfg` `create-project.sh` writes) scaffold, and the keys the Key Registries accept agree, with each key's stated default, section and commented value matching its registry. | Unit runner. |
| `lint_container_engine_free.py` | That no test in the per-RC suites invokes or probes docker or podman. | Unit and integration runners. |
| `lint_dashboard_hardcode_colors.py` | Raw ANSI or tmux colour literals in the dashboard code instead of the shared palette. | Unit runner. |
| `lint_dashboard_network_git_gate.py` | Any network git command (`fetch`, `pull`, `push`, `clone`, `ls-remote`) in the dashboard scripts. | Unit and integration runners. |
| `lint_doc_command_truth.py` | That every script or verb a document cites exists and answers `--help`, that documented config keys and defaults match the key registries, that dashboard window references match the window census, and that every installed script is named in an operator doc or in this section. | Unit runner, pre-merge. |
| `lint_docker_helper_path.py` | Code that reaches a container engine without the shared helper that logs each use. | Unit and integration runners. |
| `lint_dockerfile_static.py` | The shipped Dockerfiles, as plain text and with no engine, for the shapes that have broken the cold-build lane. | Unit runner. |
| `lint_docstring_coverage.py` | Kanban Python modules, public functions, classes and methods with no docstring, and `add_argument` calls with no `help=`. | Unit runner. |
| `lint_dual_channel_pair.py` | That every error-contract call supplies a real `error`, `message` and `remedy`. | Unit and integration runners. |
| `lint_echo_pipe_grep_q.py` | `echo … \| grep -q` pipelines, which race under `pipefail`; the count may fall but never rise. | Unit and integration runners, pre-merge. |
| `lint_engine_size_dupe.py` | That the decomposed engine files stay under their size limit and no shell function name is defined twice. | Unit and integration runners, pre-merge. |
| `lint_env_bootstrap.py` | That bash and Python entry points resolve the kanban root through the shared bootstrap. | Unit and integration runners, pre-merge. |
| `lint_gate_census_compliance.py` | That every gate or refusal carries its census class, the dual-channel error contract and an indexed census anchor. | Unit runner. |
| `lint_governance_byte_budgets.sh` | Each role's assembled governance size against its `kanban.cfg [governance]` budget; reports only. | Unit and integration runners. |
| `lint_halt_scope_writers.py` | Code that creates the fleet-wide root `HALT` file from a function not named as a global halt. | Unit runner. |
| `lint_help_presence.py` | That every operator-facing script answers `--help` or `-h`, apart from a listed set of internal helpers. | Unit and integration runners. |
| `lint_hygiene.py` | The registered hygiene classes, among them provenance: bug IDs, task IDs and version citations in shipped code and prose. | Unit and integration runners; CODER and WRITER before merging; CM over release notes. |
| `lint_icd_compat.py` | That the current REST interface description still contains everything each supported earlier version did. | Unit and integration runners. |
| `lint_icd_freshness.py` | That the checked-in REST interface description matches a fresh regeneration. | Unit and integration runners. |
| `lint_inline_python.py` | Python embedded in bash scripts; the count may fall but never rise. | Unit and integration runners, pre-merge. |
| `lint_lib_function_dedupe.py` | Shell function names defined in more than one `lib/*.sh` file. | `audit_coding_standards.sh`; the runners use `lint_engine_size_dupe.py` instead. |
| `lint_operator_verb_manifest.py` | That the operator verb census matches the scripts and routes it describes. | Unit runner. |
| `lint_orphan_tests.py` | Test files no gated runner collects. | Unit runner. |
| `lint_python_duplicates.py` | Copy-pasted Python function bodies and private copies of shared helpers; the count may fall but never rise. | Unit and integration runners, pre-merge. |
| `lint_python_import_completeness.py` | That every third-party Python import is declared in the requirements files. | Unit runner. |
| `lint_raw_marker_read.py` | Discovery code that decides by reading a queue-marker character instead of the task's own state. | Unit runner. |
| `lint_rc_empty_commits.py` | Non-merge commits on a release branch that change no file. | Integration runner, and the wake's completion gate for each finished task. |
| `lint_runnable_remedy.py` | Error-contract remedies that still carry an angle-bracket placeholder instead of a runnable command. | Unit runner. |
| `lint_skip_bug_gate.sh` | That every skipped test citing a follow-up bug names a bug file that exists. | Unit and integration runners. |
| `lint_status_writer_bypass.py` | Scripts that edit a task's `## State`, `## Needs Human`, `## Summary` or `## Blockers` directly instead of through the status writer. | Unit runner. |
| `lint_test_anti_patterns.py` | The two worst test anti-patterns in the TESTER guidelines, in tests and runtime scripts. | Unit and integration runners. |
| `lint_tester_report_census.py` | Code that parses a TESTER report itself instead of calling the one report parser. | Unit runner. |
| `lint_tracked_ignored.py` | Tracked files that a `.gitignore` rule matches, and tracked generated files (logs, locks, coverage output). | Pre-merge. |
| `lint_upgrade_no_temp_copy_guard.py` | That `upgrade.sh` carries no temp-copy guard variables. | Unit runner. |

**Test runners**

| Script | What it checks | Who runs it |
|---|---|---|
| `run-unit-tests.sh` | The unit suite, after its pre-flight lints; `--files` runs named test files only, `--pre-merge` runs the whole-tree checks for a branch about to merge. | TESTER through `run-gated-suites.sh`; CODER and WRITER with `--files` and `--pre-merge`. |
| `run-integration-tests.sh` | The integration suite, after its pre-flight lints; `--files` runs named test files only. | TESTER through `run-gated-suites.sh`; CODER and WRITER with `--files`. |
| `run-gated-suites.sh` | Both suites at once, splitting the `kanban.cfg [testing] workers` budget, each writing its own exit and findings file. | TESTER, once per RC. |
| `run-gated-tests.sh` | The docker-gated tests against the shipped images. | The operator only (container tier); inside an agent wake it skips. |
| `run-three-host-matrix.sh` | The unit and integration suites on three constructed hosts: the dev tree, an install with no git, a container with no tmux. | The operator only (container tier); inside an agent wake it skips. |

**Verifiers**

| Script | What it checks | Who runs it |
|---|---|---|
| `verify-blocked-task-guard.sh` | That the wake holds dispatch for a project only while its human-gated blocks are systemic. | On demand, as a regression check. |
| `verify-config-loader.sh` | That the config loader loads a valid `kanban.cfg`, treats optional keys as optional, and refuses bad values. | On demand, as a regression check. |
| `verify-install-policy.sh` | `install.sh`'s re-run safety on an existing install, by read-only inspection and a dry run. | On demand, as a regression check. |
| `verify-perproject-devtree-gate.sh` | That the wake's dev-tree presence check is scoped per project. | On demand, as a regression check. |
| `verify-perproject-devtree.sh` | That task worktrees are created in, and torn down from, each project's own dev tree. | On demand, as a regression check. |
| `verify-temp-root-relocation.sh` | That a relocated framework temp root is honoured end to end. It edits the kanban root's `kanban.cfg` and runs both full suites, restoring the file on exit. | On demand, as a regression check. |
| `verify-test-order-independence.sh` | That unit tests give the same results when run in a different order. | On demand. |
| `verify-visibility-pane-widths.sh` | The visibility dashboard's pane widths at several terminal widths, on a private tmux server. | On demand, as a regression check. |
| `verify-worktree-isolation.sh` | The task-worktree lifecycle — create, merge, tear down — against a synthetic repository. | On demand, as a regression check. |

**Helpers in subfolders**

| Script | What it does | Who runs it |
|---|---|---|
| cm/promote_bundled_items.py | Moves each bug or priority file a shipped bundle lists from `running` to `done`, but only when a DONE task claims its fix. | CM's release, and the document workflow's finalize step. |
| `cleanup/test-cleanup.sh` | Lists leaked `/tmp/tmp.*` test directories older than 60 minutes; removes them with `--delete`. | On demand, after interrupted test runs. |
| `cleanup/test-stale-artifact-branches.sh` | Smoke tests for `cleanup-stale-artifact-only-branches.sh`. | On demand. |
| `migrations/migrate_v2.22.5_to_v2.23.0.sh` | Seeds the commented `overwatch_resolve` key into every existing project's `project.cfg`. | `upgrade.sh`, through `migrations/run-migrations.sh`, when an upgrade crosses that version. |
| `migrations/migrate_v2.24.7_to_v2.24.8.sh` | Seeds the OVERWATCH queue file into every existing project. | `upgrade.sh`, through `migrations/run-migrations.sh`, when an upgrade crosses that version. |

---

## Verb Reference (generated)

Every block below is generated by `team/scripts/generate_operator_verb_docs.py` from the operator verb census (`team/config/operator_verbs.manifest`, read through its sanctioned Python reader `team/pgai_agent_kanban/lib/operator_verb_manifest.py`): the **Script**, **Mutates state**, **Idempotence**, **Class** and **REST twin** facts are the verb's census row, and the invocation and flag vocabulary are that verb's own `--help` output. Adding a census row and regenerating is the only edit a new verb needs to appear here. Everything between the `OPERATOR-VERBS-GENERATED` section marker pair -- including the per-verb markers inside it -- is regenerated on every render and must not be hand-edited; run the generator and commit the result instead. Run `python3 team/scripts/generate_operator_verb_docs.py --check` to verify this section still matches the census and every verb's own `--help` output without writing anything; the gated runners run that check as a doc-truth pre-flight, so a census row edited without regenerating is reported.

Each block's **Implementation** line names where the verb's logic lives. `module:<function>` means the script is a thin wrapper over that Python function, and the REST route calls the same function. `shell` means the script itself is the implementation. Three verbs are `shell` by design because their whole job is to start a process: `wake-now`, which fires `wake-batch.sh` for one agent or every pipeline agent and returns; `cold-build-now`, which validates `--project` and execs `deep-verify.sh` for that project; and `deep-verify`, which starts the cold-build lane and reaches every decision around the launch — the scheduled due-check, the ledger, verdict classification and failure-path bug filing — as a `pgai_agent_kanban.deep_verify` function through `pp_run_ops`. The launcher list and each reason are in `team/config/api-route-impl-launchers.txt`, explained under "Launchers" in [operator-api.md](operator-api.md).

<!-- OPERATOR-VERBS-GENERATED:BEGIN section=verb-reference -->
<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=halt -->
#### `halt`

Generated from `scripts/halt.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/halt.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/halt`
- **Implementation:** `module:pgai_agent_kanban.ops.write.halt`

```text
Usage: halt.sh [OPTIONS]

Create the HALT signal for a project, stopping the wake loop cleanly.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --help, -h         Show this help and exit
```
<!-- OPERATOR-VERBS-GENERATED:END verb=halt -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=unhalt -->
#### `unhalt`

Generated from `scripts/unhalt.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/unhalt.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/unhalt`
- **Implementation:** `module:pgai_agent_kanban.ops.write.unhalt`

```text
Usage: unhalt.sh [OPTIONS]

Remove the HALT signal for a project, allowing the wake loop to resume.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --help, -h         Show this help and exit
```
<!-- OPERATOR-VERBS-GENERATED:END verb=unhalt -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=halt-global -->
#### `halt-global`

Generated from `scripts/halt-global.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/halt-global.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/halt-global`
- **Implementation:** `module:pgai_agent_kanban.ops.write.halt_global`

```text
Usage: halt-global.sh [OPTIONS]

Create the global HALT — stops all projects at next wake.

Options:
  --help, -h         Show this help and exit

  Global HALT is created at ${KANBAN_ROOT}/HALT.
  Idempotent.  Reverse with unhalt-global.sh.
```
<!-- OPERATOR-VERBS-GENERATED:END verb=halt-global -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=unhalt-global -->
#### `unhalt-global`

Generated from `scripts/unhalt-global.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/unhalt-global.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/unhalt-global`
- **Implementation:** `module:pgai_agent_kanban.ops.write.unhalt_global`

```text
Usage: unhalt-global.sh [OPTIONS]

Remove the global HALT — resumes all projects at next wake.

Options:
  --help, -h         Show this help and exit

  Global HALT is removed from ${KANBAN_ROOT}/HALT.
  Idempotent.  Set global HALT with halt-global.sh.
```
<!-- OPERATOR-VERBS-GENERATED:END verb=unhalt-global -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=halt-after -->
#### `halt-after`

Generated from `scripts/halt-after.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/halt-after.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/halt-after`
- **Implementation:** `module:pgai_agent_kanban.ops.write.halt_after`

```text
Usage: halt-after.sh [OPTIONS]

Arm the HALT-AFTER soft-drain signal for a project.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --key KEY          Arbitrary key or identifier
  --until vX.Y.Z     Release version boundary (a leading 'v' is optional)
  --help, -h         Show this help and exit

The --key flag sets the drain event token (default: rc).
Supported tokens: rc  pm  coder  writer  tester  cm
--until vX.Y.Z arms the versioned rc:vX.Y.Z token directly (rc scope only).
```
<!-- OPERATOR-VERBS-GENERATED:END verb=halt-after -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=defer -->
#### `defer`

Generated from `scripts/defer.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/defer.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/defer`
- **Implementation:** `module:pgai_agent_kanban.ops.write.defer`

```text
Usage: defer.sh [OPTIONS]

Arm the DEFER-LOW-BUGS deferral campaign for a project, until the named release ships.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --until vX.Y.Z     Release version boundary (a leading 'v' is optional)
  --help, -h         Show this help and exit

Writes PROJECT_ROOT/DEFER-LOW-BUGS carrying the validated until= target.
See undefer.sh to clear the campaign and why.sh to see the posture it produces.
```
<!-- OPERATOR-VERBS-GENERATED:END verb=defer -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=undefer -->
#### `undefer`

Generated from `scripts/undefer.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/undefer.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/undefer`
- **Implementation:** `module:pgai_agent_kanban.ops.write.undefer`

```text
Usage: undefer.sh [OPTIONS]

Clear the DEFER-LOW-BUGS deferral campaign for a project (idempotent).

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --help, -h         Show this help and exit

Running this against a project with no campaign file in force succeeds
and reports there was nothing to clear.
```
<!-- OPERATOR-VERBS-GENERATED:END verb=undefer -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=deep-verify -->
#### `deep-verify`

Generated from `scripts/deep-verify.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/deep-verify.sh`
- **Mutates state:** yes
- **Idempotence:** non-idempotent
- **Class:** action
- **REST twin:** `POST /operations/deep-verify`
- **Implementation:** `shell`

```text
Usage: deep-verify.sh [OPTIONS]

Run the DEEP-VERIFY tier for a project on demand and record the result.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --help, -h         Show this help and exit

Runs the cold-build lane (team/scripts/run-cold-build-tests.sh) --
the tier's only member today -- and appends one record to the
named project's deep-verify ledger
($KANBAN_ROOT/projects/<name>/deep-verify-ledger.md) naming the
timestamp, the tree state exercised (version and commit), the
verdict (built / failed-to-build / could-not-attempt), and the
per-pair engine/flavor detail behind it.

--scheduled: the weekly, off-hours dispatch mode a cron/pseudocron
entry invokes. Self-gates on the project's deep-verify ledger and
its [release] deep_verify (weekly|off, true|false also accepted as
aliases) / deep_verify_offhours_window config, each resolved from
project.cfg, then kanban.cfg, then the default (weekly;
03:00-03:59) -- a scheduled invocation that
is not due costs nothing (no lane, no container engine, no ledger
write) and exits 0 after one narration line. Given with --project
it evaluates just that project; given alone it fans out over every
active project in projects.cfg.

Operator-only: running the lane is the container tier. Without
PGAI_CONTAINER_TIER=1 in the invoking shell or --operator-approved,
or from inside an agent wake, it prints one SKIPPED line and exits
0 with no lane run and no ledger record. Operator form, e.g.:
  PGAI_CONTAINER_TIER=1 scripts/deep-verify.sh --project pgai-agent-kanban --image all

  --image NAME       Build image NAME from team/config/container-images.txt
                     (repeatable), or --image all for every declared image.
                     Default: kanban.cfg [release] container_images. With
                     neither, the available images are listed and nothing runs.
  --operator-approved  Operator authorization, same as PGAI_CONTAINER_TIER=1.

A failed-to-build verdict files a bug naming the failing
engine/flavor/step. When every image built but a non-build cold
test failed, the verdict is built and a bug naming the failing
test's file and test id is filed. When no image could be built
(every pair narrate-skipped) and a non-build cold test failed, the
verdict is could-not-attempt and the same test bug is filed. An
open or running bug already covering the same failure is cited
instead of filing a duplicate. Any other could-not-attempt run
(the lane exited 0, or exited 2 because pytest is not installed)
files no bug.

Exit codes: 0 = verdict is built or could-not-attempt and no
non-build cold test failed (nothing wrong with the tree was found),
or the run was SKIPPED or no image was selected (no record
written); 1 = verdict is failed-to-build,
or verdict is built but a non-build cold test failed (a bug naming
the test is filed or cited), or verdict is could-not-attempt but a
non-build cold test failed (the lane exited 1; a bug naming the
test is filed or cited), or --project did not resolve to a
registered project (no record is written in that case); 3 =
refused, an image name the manifest does not declare (no record
written).
```
<!-- OPERATOR-VERBS-GENERATED:END verb=deep-verify -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=cold-build-now -->
#### `cold-build-now`

Generated from `scripts/cold-build-now.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/cold-build-now.sh`
- **Mutates state:** yes
- **Idempotence:** non-idempotent
- **Class:** action
- **REST twin:** `POST /operations/cold-build-now`
- **Implementation:** `shell`

```text
Usage: cold-build-now.sh [OPTIONS]

Run the cold-build lane on demand, outside any RC.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --help, -h         Show this help and exit

A thin, named alias for deep-verify.sh run with --project NAME -- cold-build
is the DEEP-VERIFY tier's only member today, so this verb and that
one do exactly the same work: run the cold lane, classify the
outcome, and append one record to the named project's deep-verify
ledger ($KANBAN_ROOT/projects/<name>/deep-verify-ledger.md).

Operator-only: without PGAI_CONTAINER_TIER=1 or --operator-approved,
or from inside an agent wake, the run prints one SKIPPED line and
exits 0 with no ledger record. Operator form, e.g.:
  PGAI_CONTAINER_TIER=1 scripts/cold-build-now.sh --project pgai-agent-kanban --image all

  --image NAME       Passed to deep-verify.sh: an image from
                     team/config/container-images.txt (repeatable), or all.
                     Default: kanban.cfg [release] container_images; with
                     neither, the available images are listed and nothing runs.
  --operator-approved  Operator authorization, same as PGAI_CONTAINER_TIER=1.

Exit codes: 0 = verdict is built or could-not-attempt and no
non-build cold test failed, or the run was SKIPPED or no image was
selected (no record); 1 = verdict is failed-to-build, or verdict
is built but a non-build cold test
failed (a bug naming the test is filed or cited), or verdict is
could-not-attempt but a non-build cold test failed (the lane exited
1; a bug naming the test is filed or cited), or --project did
not resolve to a registered project (no record is written in that
case); 3 = refused, an image name the manifest does not declare
(no record).
```
<!-- OPERATOR-VERBS-GENERATED:END verb=cold-build-now -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=wake-now -->
#### `wake-now`

Generated from `scripts/wake-now.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/wake-now.sh`
- **Mutates state:** yes
- **Idempotence:** non-idempotent
- **Class:** action
- **REST twin:** `POST /operations/wake-now`
- **Implementation:** `shell`

```text
Usage: wake-now.sh [OPTIONS]

Fire one or all agents immediately (--sleep=0).

Options:
  --agent ROLE       Agent role: pm|coder|writer|tester|cm|po
  --all              Fire all pipeline agents backgrounded (pm coder cm writer tester)
  --bg               Background the agent process instead of running in foreground
  --help, -h         Show this help and exit
```
<!-- OPERATOR-VERBS-GENERATED:END verb=wake-now -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=show -->
#### `show`

Generated from `scripts/show.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/show.sh`
- **Mutates state:** no
- **Idempotence:** read-only
- **Class:** read
- **REST twin:** `GET /show`
- **Implementation:** `module:pgai_agent_kanban.ops.read.get_task_status`

```text
Usage: show.sh [OPTIONS]

Emit a task or intake item's content to stdout (read-only).

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --key KEY          Arbitrary key or identifier
  --file PATH        File path
  --help, -h         Show this help and exit

         value: status (default) or readme
                status -> tasks/<KEY>/status.md
                readme -> tasks/<KEY>/README.md
                Ignored for intake keys (single-file items).

Behavior:
  Task key:   emits status.md (default) or README.md via --file
  Intake key: emits the intake .md file directly
  Unknown key: non-zero exit with a clear error message

Exit codes:
  0  Content emitted to stdout
  1  Usage error or missing argument
  3  Key not found
```
<!-- OPERATOR-VERBS-GENERATED:END verb=show -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=show-queues -->
#### `show-queues`

Generated from `scripts/dashboard/show-queues.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/dashboard/show-queues.sh`
- **Mutates state:** no
- **Idempotence:** read-only
- **Class:** read
- **REST twin:** `GET /show-queues`
- **Implementation:** `module:pgai_agent_kanban.ops.read.get_queues`

```text
Usage: show-queues.sh [OPTIONS]

Render the queue-state pane for one or every registered project (read-only).

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --kanban-root PATH Override the kanban root path (default: $PGAI_AGENT_KANBAN_ROOT_PATH)
  --help, -h         Show this help and exit

No flags:   compact one-line-per-agent queue summary (pm/coder/writer/
            tester/cm) plus the currently-working task, if any.
--details:  per-agent, per-state task listing for --project, or every
            registered project in turn when --project is omitted.

This is the same renderer the dashboard's queues pane and why.sh
--details call -- there is exactly one implementation.

Exit codes:
  0  Queue summary emitted to stdout
  1  Usage error (an undeclared flag was supplied), --project names
     no registered project, or (compact mode) neither --project nor
     PGAI_PROJECT_NAME names one -- refusal on stderr, nothing on stdout
```
<!-- OPERATOR-VERBS-GENERATED:END verb=show-queues -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=why -->
#### `why`

Generated from `scripts/why.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/why.sh`
- **Mutates state:** no
- **Idempotence:** read-only
- **Class:** read
- **REST twin:** `GET /verdicts`
- **Implementation:** `module:pgai_agent_kanban.ops.verdict.resolve_verdicts`

```text
Usage: why.sh [OPTIONS]

Report why a project's lane stopped moving (every registered project's when neither --project nor $PGAI_PROJECT_NAME names one), or its deferral posture with --details.

Options:
  --project NAME     Project to report (default: $PGAI_PROJECT_NAME; with neither set, every registered project)
  --details          Also print the full diagnostic pane: locks, queue tails, posture and refusals (needs --project or $PGAI_PROJECT_NAME)
  --help, -h         Show this help and exit

With no --project: report the project $PGAI_PROJECT_NAME names when
it is set. Only when neither --project nor $PGAI_PROJECT_NAME is
given, walk every registered project in projects.cfg priority order
and print one verdict line each (RUNNING / BLOCKED / HALTED /
STUCK-LOCK / IDLE), exiting with the worst verdict's code.

--details: needs a project, named by --project or, when --project
is omitted, by $PGAI_PROJECT_NAME; with neither set it refuses.
Prints that project's verdict line, then the full diagnostic pane
below it: every lock held for this project
(held duration/provenance), the last five entries of every task queue
with their raw markers, the show-queues --details pane embedded
verbatim, the whole posture in force (HALT/HALT-AFTER, the resolved
deferral posture and its tiers, the Active RC, and every pending
requirements bundle with its ## Status), the project's most
recent discovery/gate refusal, and the install's last docker use
(the last line of logs/docker-use.log).
```
<!-- OPERATOR-VERBS-GENERATED:END verb=why -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=release-locks -->
#### `release-locks`

Generated from `scripts/release-locks.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/release-locks.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/release-locks`
- **Implementation:** `module:pgai_agent_kanban.ops.locks.release_lock`

```text
Usage: release-locks.sh [OPTIONS]

List every advisory lock under the kanban root's locks/ directory with its provenance, or release one named lock.

Options:
  --force            Override safety guards
  --help, -h         Show this help and exit

         value: a lock's name (its lock-file basename with the
                trailing .lock stripped), or an unambiguous prefix of
                one. Ambiguous or unmatched names refuse rather than
                guessing.
  (--force overrides the refusal to release a lock whose recorded
   holder pid is still alive: the holder is sent SIGTERM so its own
   exit path releases the lock, and this script waits up to twice
   kill_grace_seconds plus 10 seconds for it to; a lock that changed
   hands since it was read is reported let go and nobody is
   signalled. The live holder's file is never replaced or removed.
   Ignored when --release is not given.)

Behavior:
  No flags:        list every lock file with how long it has been
                   held (from its recorded acquired_at), its
                   provenance, and a held/stale/unparseable verdict
                   (read-only).
  --release NAME:  release the named lock. A dead holder's file is
                   swapped for an empty one once its flock is free.
                   Refuses if the recorded holder pid is alive, or
                   if it is dead but another process still holds the
                   flock, unless --force is given, in which case the
                   holder (or the processes holding the flock) is
                   signalled (see above).

Exit codes:
  0  listing printed, or release succeeded (a re-release of an
     already-released lock also exits 0)
  1  usage error
  2  --release name matches more than one lock (candidates named)
  3  --release name matches no lock
  4  --release target's holder pid is alive (or dead while another
     process holds its flock) and --force was not given
  5  --release --force signalled the live holder, but the lock is still
     held after twice kill_grace_seconds plus 10 seconds (by the holder,
     or by a process that inherited its lock descriptor), or it could
     not be signalled
```
<!-- OPERATOR-VERBS-GENERATED:END verb=release-locks -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=unwind-rc -->
#### `unwind-rc`

Generated from `scripts/unwind-rc.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/unwind-rc.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/unwind-rc`
- **Implementation:** `module:pgai_agent_kanban.cm.unwind.unwind_rc`

```text
Usage: unwind-rc.sh [OPTIONS]

Fully unwind an in-flight release candidate across all state stores.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --key KEY          Arbitrary key or identifier
  --dry-run          Report what would happen without making changes
  --force            Override safety guards
  --help, -h         Show this help and exit

  (--key: RC version to unwind, format vX.Y.Z; must match Active RC in release-state.md)
  (--confirm: answers the confirmation prompt non-interactively; bypasses no pre-flight check)
  (--force: skips Active-RC-version mismatch check and the prompt; does not bypass existence or tag checks)
```
<!-- OPERATOR-VERBS-GENERATED:END verb=unwind-rc -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=verify-rc-state -->
#### `verify-rc-state`

Generated from `scripts/verify-rc-state.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/verify-rc-state.sh`
- **Mutates state:** no
- **Idempotence:** read-only
- **Class:** read
- **REST twin:** `GET /verify-rc-state`
- **Implementation:** `module:pgai_agent_kanban.ops.rc_state.verify_rc_state`

```text
Usage: verify-rc-state.sh [OPTIONS]

Read-only consistency checker for kanban project RC state.

Options:
  --help, -h         Show this help and exit
  --verbose          Print every check (OK, WARN, ERROR); by default only WARN/ERROR and the summary line print

Usage: verify-rc-state.sh PROJECT [--verbose]

Arguments:
  PROJECT      Project name; resolved by exact match then unambiguous
               prefix against projects.cfg.

Checks performed:
  a. release-state.md consistency (Active RC format, timestamps, Last Released).
  b. Queue <-> folder consistency (open queue entries have matching task folders).
  c. status.md state <-> queue marker consistency.
  d. Cross-task Prerequisites references resolve to existing task folders.
  e. release-state.md <-> git branch consistency.
  f. Bundle invariants (PRIORITY/BUG files referenced in requirements bundles exist).

Output format:
  [OK]    <message>
  [WARN]  <message>
  [ERROR] <message>
  verify-rc-state: N errors, N warnings, N ok

Exit codes:
  0   No ERRORs found (WARN findings do not affect exit code).
  1   One or more ERROR findings.
  2   Pre-flight failure (bad arguments, project not found, etc.).

Configuration:
  KANBAN_ROOT           Path to the kanban live install root
                        (default: $HOME/pgai_agent_kanban).
  PGAI_DEV_TREE_PATH    Path to the dev tree git clone
                        (derived from project config when not set).
```
<!-- OPERATOR-VERBS-GENERATED:END verb=verify-rc-state -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=reset -->
#### `reset`

Generated from `scripts/reset.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/reset.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/reset`
- **Implementation:** `module:pgai_agent_kanban.ops.write.reset_item`

```text
Usage: reset.sh [OPTIONS]

Reset agent tasks or intake items to re-pickable state (operator power tool).

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --key KEY          Arbitrary key or identifier
  --keep-artifacts   Preserve the task's artifacts/ contents (default: clear them; agent-task resets only)
  --force            Override safety guards
  --help, -h         Show this help and exit
  (--force is the stale-worktree cleanup flag: clears any residual
   worktree registration and on-disk path before resetting the task;
   without --force a detected stale worktree causes a warning and
   non-zero exit.  Agent-task resets only.)

         value: full item key (see formats):
                 Agent task:   ROLE-YYYYMMDD-NNN[-slug] (role in prefix)
                 Bug intake:   BUG-NNNN
                 Priority:     PRIORITY-NNNN
                 Requirement:  version string (e.g. v0.1.2)

Examples:
  reset.sh --project pgai-agent-kanban --key ROLE-YYYYMMDD-NNN-some-slug
  reset.sh --project pgai-agent-kanban --key ROLE-YYYYMMDD-NNN
  reset.sh --project pgai-agent-kanban --key BUG-NNNN
  reset.sh --project pgai-agent-kanban --key PRIORITY-NNNN
  reset.sh --project pgai-agent-kanban --key v0.1.2

Exit codes:
  0  reset completed
  1  usage error or key not found / ambiguous
  2  WORKING state refusal (agent-task resets only); or stale worktree
     detected without --force (recipe printed to stderr)
  3  key not found
  4  --force aborted: on-disk worktree path is a mount target
     (blocking mount name printed to stderr)
```
<!-- OPERATOR-VERBS-GENERATED:END verb=reset -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=close -->
#### `close`

Generated from `scripts/close.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/close.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/close`
- **Implementation:** `module:pgai_agent_kanban.ops.write.close_item`

```text
Usage: close.sh [OPTIONS]

Close/resolve an item (bug/priority/requirement/task) by setting its terminal status.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --key KEY          Arbitrary key or identifier
  --state STATE      Closed state: done|wont-do (default: done)
  --note TEXT        Free-form text recorded in the item's ## Close Note section (intake items only)
  --dry-run          Report what would happen without making changes
  --help, -h         Show this help and exit

         value: terminal state for intake items (default: done):
                 done        — item resolved/addressed
                 wont-do     — item intentionally not addressed
                               (an item another item subsumed closes
                               as wont-do with a --note saying why)
                 (For agent tasks, close always sets state to DONE.)

  close.sh performs the close operation and refuses in two cases only:
    - --state is not a closed state (done|wont-do): close.invalid_state,
      exit 1, nothing written;
    - the key cannot be resolved to a single target: ambiguous (exit 2)
      or not found (exit 3).

Example:
  close.sh --project myproject --key BUG-NNNN --state wont-do \
           --note 'subsumed by PRIORITY-NNNN'

Exit codes:
  0  Item closed (or --help shown, or --dry-run reported)
  1  Usage error, missing argument, or --state not done|wont-do
  2  Ambiguous key — key matches zero or multiple items
  3  Key not found
  4  State mutation failed
```
<!-- OPERATOR-VERBS-GENERATED:END verb=close -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=wontdo -->
#### `wontdo`

Generated from `scripts/wontdo.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/wontdo.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/wontdo`
- **Implementation:** `module:pgai_agent_kanban.ops.write.wontdo_item`

```text
Usage: wontdo.sh [OPTIONS]

Mark a task as WONT-DO, retiring it cleanly without marking it DONE.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --key KEY          Arbitrary key or identifier
  --help, -h         Show this help and exit

This script produces WONT-DO only and cannot set DONE through any
argument combination.  Use close.sh to mark a task as DONE.
```
<!-- OPERATOR-VERBS-GENERATED:END verb=wontdo -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=delete -->
#### `delete`

Generated from `scripts/delete.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/delete.sh`
- **Mutates state:** yes
- **Idempotence:** non-idempotent
- **Class:** action
- **REST twin:** `POST /operations/delete`
- **Implementation:** `module:pgai_agent_kanban.ops.write.delete_item`

```text
Usage: delete.sh [OPTIONS]

Delete a task or intake item by key, with a terminal-state safety guard.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --key KEY          Arbitrary key or identifier
  --force            Override safety guards
  --dry-run          Report what would happen without making changes
  --help, -h         Show this help and exit

Guard: deletion is refused unless the item is in state DONE or WONT-DO.
Use --force to bypass the guard (data-loss risk: no undo available).
Use --dry-run to preview the target before committing to deletion.
```
<!-- OPERATOR-VERBS-GENERATED:END verb=delete -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=intake -->
#### `intake`

Generated from `scripts/intake.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/intake.sh`
- **Mutates state:** yes
- **Idempotence:** non-idempotent
- **Class:** action
- **REST twin:** `POST /operations/intake`
- **Implementation:** `module:pgai_agent_kanban.ops.write.deposit_intake`

```text
Usage: intake.sh [OPTIONS]

Deposit a staged intake file into the correct project intake directory.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --file PATH        File path
  --help, -h         Show this help and exit

  <source_file> may also be supplied as a positional argument instead of --file.

Routing by filename prefix (case-sensitive):
  BUG-*        -> projects/<name>/bugs/
  PRIORITY-*   -> projects/<name>/priority/
  v[0-9]*.md   -> projects/<name>/requirements/
  (any other)  -> REFUSED; nothing is copied

Copy semantics:
  - Source file is NOT moved or deleted (copy only).
  - Deposited copy is always mode 644.
  - Copy is atomic (temp + link into place); discovery never sees a partial file.
  - REFUSED if the target already exists (no clobber).

NOTE: intake.sh is a DUMB router. It does NOT validate file contents,
assign numbers, or check headings. The discovery pipeline checks names
afterwards and quarantines a file it keeps rejecting into
projects/<name>/rejected/ (legacy fallback: <intake>/.rejected/);
list-rejected.sh and recover-rejected.sh find and restore it.

Examples:
  intake.sh --project myproject $PGAI_AGENT_KANBAN_TEMP_DIR/BUG-NNNN-some-slug.md
  intake.sh --project myproject --file $PGAI_AGENT_KANBAN_TEMP_DIR/PRIORITY-NNNN-urgent.md

Exit codes:
  0  File deposited (prints the deposited path)
  1  Usage error, source file problem, or destination directory missing
  2  Routing refused: filename does not match a known intake prefix
  3  Target already exists (no clobber)
  4  Filesystem error during copy
```
<!-- OPERATOR-VERBS-GENERATED:END verb=intake -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=set-version-ceiling -->
#### `set-version-ceiling`

Generated from `scripts/set-version-ceiling.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/set-version-ceiling.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/set-version-ceiling`
- **Implementation:** `module:pgai_agent_kanban.ops.write.set_version_ceiling`

```text
Usage: set-version-ceiling.sh [OPTIONS]

Read, set, or lift (none) max_minor / max_major ceilings in a project's project.cfg.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --show             Print the max_minor and max_major values in effect (after any change): a number, the default marked (default) when the key is absent, or none
  --minor N          Set max_minor to the non-negative integer N
  --major N          Set max_major to the non-negative integer N
  --no-minor         Write max_minor = none: no minor ceiling (wins over --minor; deleting the line instead restores the default 13)
  --no-major         Write max_major = none: no major ceiling (wins over --major; deleting the line instead restores the default 0)
  --dry-run          Report what would happen without making changes
  --help, -h         Show this help and exit
```
<!-- OPERATOR-VERBS-GENERATED:END verb=set-version-ceiling -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=switch-provider -->
#### `switch-provider`

Generated from `scripts/switch-provider.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/switch-provider.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/switch-provider`
- **Implementation:** `module:pgai_agent_kanban.ops.write.switch_provider`

```text
Usage: switch-provider.sh [OPTIONS]

Switch the active LLM provider for the kanban framework.

Options:
  --help, -h         Show this help and exit

  --provider accepts: claude, codex, gemini  (required)
```
<!-- OPERATOR-VERBS-GENERATED:END verb=switch-provider -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=create-project -->
#### `create-project`

Generated from `scripts/create-project.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/create-project.sh`
- **Mutates state:** yes
- **Idempotence:** non-idempotent
- **Class:** action
- **REST twin:** `POST /operations/create-project`
- **Implementation:** `module:pgai_agent_kanban.ops.projects.create_project`

```text
Usage: create-project.sh [OPTIONS]

Bootstrap a new project under $KANBAN_ROOT/projects/<name>/.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --dry-run          Report what would happen without making changes
  --help, -h         Show this help and exit

  --workflow-type TYPE workflow_type: any registered ready plugin type (default: release)
  --max-patch N        max_patch ceiling (default: 21)
  --max-minor N        max_minor ceiling (default: 13)
  --max-major N        max_major ceiling (default: 0)
  --git-remote NAME    git_remote_name (default: origin)
  --priority INT       registry priority (default: next available)
  --color '#RRGGBB'    registry display color (default: next unused)
  --dev-tree PATH      dev_tree_path (alias: --dev-tree-path; default: empty, edit manually)
  --git-repo URL       git_repo_url (alias: --git-repo-url; default: empty, edit manually)
  --no-migrate         skip auto-migration of projects.cfg (not recommended)

Note: when --dev-tree names a path that does not currently exist, the
script WARNs (naming the path) but still writes the value and exits 0.
```
<!-- OPERATOR-VERBS-GENERATED:END verb=create-project -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=add-project -->
#### `add-project`

Generated from `scripts/add-project.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/add-project.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/add-project`
- **Implementation:** `module:pgai_agent_kanban.ops.projects.add_project`

```text
Usage: add-project.sh [OPTIONS]

Register an existing on-disk project directory in projects.cfg.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --dry-run          Report what would happen without making changes
  --help, -h         Show this help and exit

  --priority INT       registry priority (default: next available)
  --color '#RRGGBB'    registry display color (default: next unused)
  --no-migrate         skip auto-migration of projects.cfg (not recommended)
```
<!-- OPERATOR-VERBS-GENERATED:END verb=add-project -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=remove-project -->
#### `remove-project`

Generated from `scripts/remove-project.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/remove-project.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/remove-project`
- **Implementation:** `module:pgai_agent_kanban.ops.projects.remove_project`

```text
Usage: remove-project.sh [OPTIONS]

Unregister a project from projects.cfg; with --force also delete the directory.

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --force            Override safety guards
  --dry-run          Report what would happen without making changes
  --help, -h         Show this help and exit
```
<!-- OPERATOR-VERBS-GENERATED:END verb=remove-project -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=ship-rc -->
#### `ship-rc`

Generated from `scripts/ship-rc.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/ship-rc.sh`
- **Mutates state:** yes
- **Idempotence:** non-idempotent
- **Class:** action
- **REST twin:** `POST /operations/ship-rc`
- **Implementation:** `module:pgai_agent_kanban.cm.ship.ship_rc`

```text
Usage: ship-rc.sh --project <name> --key vX.Y.Z [--dry-run] [--help]

Ship a release candidate by squash-merging it directly into main (single-lane).

Both --project and --key are REQUIRED. Positional invocation is not supported.

Arguments:
  --project <name>  Required. Project name; drives project resolution via the pp layer.
  --key vX.Y.Z      Required. Release version in format vX.Y.Z (e.g. v0.5.0).
  --dry-run         Print the plan (project, version, branch names) and exit 0.
                    No git operations are performed. Safe to use from the API.
  --help            Print this help and exit 0.

Branch and tag prefixing
------------------------
When the project's project.cfg sets [project] branch_prefix (e.g. "ai_"),
ALL git refs operated on by this script are prefixed:

  rc branch:     ai_rc/<version>    (instead of rc/<version>)
  main:          ai_main            (instead of main)
  tag:           ai_v<version>      (instead of v<version>)

When branch_prefix is empty or absent (the common case for pure-AI shops),
this script behaves identically to the earlier un-prefixed version.

The script will error pre-flight if it cannot resolve the project context
or if the prefixed RC branch does not exist locally and on origin.

Environment variables
---------------------
  REPO_ROOT                    Override the repository root directory (default:
                               per-project dev_tree_path from project.cfg, then
                               global PGAI_DEV_TREE_PATH, then script's parent-parent)
  PGAI_AGENT_KANBAN_ROOT_PATH  Path to kanban root (default: ~/pgai_agent_kanban)
```
<!-- OPERATOR-VERBS-GENERATED:END verb=ship-rc -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=rc-metrics -->
#### `rc-metrics`

Generated from `scripts/dashboard/show-metrics.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/dashboard/show-metrics.sh`
- **Mutates state:** no
- **Idempotence:** read-only
- **Class:** read
- **REST twin:** `GET /metrics`
- **Implementation:** `module:pgai_agent_kanban.lib.metrics.show_metrics`

```text
show-metrics.sh
Historical RC metrics viewer for the pgai kanban dashboard.

Reads metrics from:
  projects/<name>/metrics/history.csv   (default view, all projects)
  projects/<name>/metrics/rc/v*.json    (--per-agent view, single project)

Usage:
  show-metrics.sh [options]

Options:
  --project <name>          Project name (default: all registered projects)
  --last <N>                Show last N rows (default: 10)
  --history-csv <path>      Override CSV path (for testing; implies single-project mode)
  --per-agent               Show per-agent token breakdown for most recent RC
  --kanban-root <path>      Override kanban root directory
  --no-color                Disable ANSI color output
  -h, --help                Show this help and exit

Default CSV columns:  rc | wall_time | blocked | input | output | cache_read | cache_write | tasks
Per-agent columns:    agent | input | output | cache_read | cache_write | invocations

Wall time shows '--' when opened_at/closed_at/wall_time_minutes
are not yet populated in history.csv.

Blocked shows the merged blocked-interval minutes for the row's RC, a
real 0 for a clean run, or a single '-' when the row's history.csv
entry carries no value for that column -- the table then prints one
footer line explaining when the column started being tracked.

Numbers are formatted with commas for readability (e.g. 5,644,879).

Environment:
  PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root override
  NO_COLOR                            Set non-empty to disable colors
  TERM=dumb                           Also disables colors
```
<!-- OPERATOR-VERBS-GENERATED:END verb=rc-metrics -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=agent-metrics -->
#### `agent-metrics`

Generated from `scripts/dashboard/agent-metrics.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/dashboard/agent-metrics.sh`
- **Mutates state:** no
- **Idempotence:** read-only
- **Class:** read
- **REST twin:** `GET /agent-metrics`
- **Implementation:** `module:pgai_agent_kanban.lib.metrics.compute_agent_averages`

```text
agent-metrics.sh
Standalone per-agent, per-project averages renderer for the pgai kanban.

The CLI twin of show-metrics.sh's per-agent view: that view renders a
single RC's raw per-agent token breakdown, while this script averages wall
time, tokens, and task count per agent, per project, and totals tokens and
tasks, across a SPAN of SHIPPED per-RC metrics rollups
(projects/<name>/metrics/rc/v*.json). Only rollups whose outcome is
"shipped" count; cancelled ones enter no figure and are counted in a
footer line instead. Figures are per agent PER PROJECT -- never blended
across projects -- and a missing or malformed rollup file is skipped
without taking a slot in the span.

Usage:
  agent-metrics.sh [options]

Options:
  --project <name>       Project to report on (default: all registered projects)
  --window <N>           Newest N shipped RCs (default: 10)
  --last <N>             Newest N shipped RCs (the same span as --window N)
  --all                  Every shipped RC
  --since <vX.Y.Z>       Every shipped RC at or above vX.Y.Z (semver order;
                         the leading "v" is optional)
  --kanban-root <path>   Override the kanban root directory
  --no-color             Disable ANSI color output
  -h, --help             Show this help and exit

  --window, --last, --all and --since choose the span. They are
  mutually exclusive: give at most one. With none, the span is
  --window 10.

Output, one block per project:
  === Agent Metrics: <project> (<span>) ===
  Agent  Avg Wall (min/task)  Avg Tokens (/RC)  Avg Tasks (/RC)  Total Tokens  Total Tasks
  <one row per agent>
  window: <N> shipped RCs (<M> cancelled excluded)

  The header span reads "last N RCs", "all shipped RCs" or "shipped RCs
  since vX.Y.Z", and the footer label "window", "all" or "since vX.Y.Z"
  to match. N is the number of shipped RCs used, M the cancelled (or
  otherwise unshipped) rollups inside the span's range; the footer prints
  even when M is 0.

  Averages print with exactly one decimal and thousands separators
  (2.0, 4.7, 41,000.0); totals as thousands-separated integers. Avg Tokens
  and Avg Tasks are means per invocation RC (the RCs an agent was actually
  invoked in). Avg Wall is a mean per TASK, pooled from each invocation
  RC's own per-agent wall-clock data, and prints '--' when none of an
  agent's invocation RCs carry it. An agent with no invocation in the span
  has no row.

Environment:
  PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root override
  NO_COLOR                     Set non-empty to disable colors
  TERM=dumb                    Also disables colors

Exit codes:
  0  Report printed (also when metrics files are missing or malformed for
     a project -- that block renders '(no metrics yet)')
  1  Usage error: unknown argument, more than one span flag, --window or
     --last not a positive integer, or --since not of the form vX.Y.Z

Read-only: never writes kanban state. The arithmetic and rounding live in
pgai_agent_kanban.lib.agent_metrics, the rendering in
pgai_agent_kanban.lib.metrics.agents (reached through pp_run_ops, the
same compute_agent_averages GET /agent-metrics calls); this script
resolves the project list and validates arguments.
```
<!-- OPERATOR-VERBS-GENERATED:END verb=agent-metrics -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=cancel-rc -->
#### `cancel-rc`

Generated from `scripts/cm/cancel-rc.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/cm/cancel-rc.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/cancel-rc`
- **Implementation:** `module:pgai_agent_kanban.cm.cancel.cancel_rc`

```text
Usage: cancel-rc.sh --project <name> --key vX.Y.Z [--yes] [--force] [--help]

Cancel an active release candidate branch and reset release-state.md.

Both --project and --key are REQUIRED. Positional invocation is not supported.

Arguments:
  --project <name>  project name; drives project resolution via the pp layer
  --key vX.Y.Z      RC version to cancel (format: vX.Y.Z, e.g. v0.15.4)
  --yes             skip the interactive confirmation prompt
  --force           bypass only the Active-RC-matches-version check; the
                    prompt still runs unless --yes is also given. The RC
                    branch and its per-RC record are cleaned up and
                    release-state.md, which then names a different RC,
                    is left unchanged
  --help            print this message and exit 0

FOOTPRINT — what this script touches vs. leaves alone. <remote> is the
project's git remote: project.cfg [project] git_remote_name, origin when
that key is absent or blank.

  TOUCHES:
    <remote>/rc/<version>        deleted (git push <remote> --delete)
    local rc/<version>           deleted (git branch -D)
    release-state.md             Active RC / RC Opened At / RC Opened By Task
                                 reset to 'none'
    main branch (local)          checked out; fast-forward pulled from <remote> when push_to_remote=true

  DOES NOT TOUCH:
    main branch on <remote>      never pushed (operator pushes via finalize-release.sh if needed)
    git tags                     never created, moved, or deleted
    release-state.md ## Last Released field
                                 read before the rewrite and written back
                                 unchanged (canonical value comes from the
                                 git tag; cancel never clears it)
    task folders / status.md     not modified; caller must mark tasks WONT-DO
    queue files                  coder_backlog.md, cm_backlog.md, etc. not modified
    requirements/ files          not renamed or deleted
    priority/ files              not modified
    bugs/ files                  not modified
    artifacts/                   not modified
    dev tree source files        not modified

  NOTE: For a full unwind (task folders, queue markers, requirements file,
  priority files, bug files, PM plan markers) use the top-level
  scripts/unwind-rc.sh instead.

  NOTE: To re-run the CM release step without deleting the rc branch, use
  reset.sh --project <name> --key <version> instead.

Configuration:
  PGAI_PROJECT_NAME   fallback project name (when --project flag not used)
  REPO_ROOT           override path to the repository root
  KANBAN_ROOT         path to the kanban root (default: $HOME/pgai_agent_kanban)
```
<!-- OPERATOR-VERBS-GENERATED:END verb=cancel-rc -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=release-state -->
#### `release-state`

Generated from `scripts/release-state.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/release-state.sh`
- **Mutates state:** no
- **Idempotence:** read-only
- **Class:** read
- **REST twin:** `GET /release-state`
- **Implementation:** `module:pgai_agent_kanban.ops.read.get_release_state`

```text
Usage: release-state.sh [OPTIONS]

Print a project's release-state.md fields (read-only).

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --help, -h         Show this help and exit

Prints the four validated fields -- active_rc, rc_opened_at,
rc_opened_by_task, last_released -- one 'key=value' line each,
resolved through the SAME validating loader GET /release-state
calls (never a second, independent parse of release-state.md).

Exit codes:
  0  Fields printed
  1  Usage error
  2  Project not found
  3  release-state.md failed schema validation (dual-channel
     error/message/remedy block printed to stderr)
```
<!-- OPERATOR-VERBS-GENERATED:END verb=release-state -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=deep-verify-status -->
#### `deep-verify-status`

Generated from `scripts/deep-verify-status.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/deep-verify-status.sh`
- **Mutates state:** no
- **Idempotence:** read-only
- **Class:** read
- **REST twin:** `GET /deep-verify-status`
- **Implementation:** `module:pgai_agent_kanban.ops.read.get_deep_verify_status`

```text
Usage: deep-verify-status.sh [OPTIONS]

Print a project's DEEP-VERIFY ledger entries and current status (read-only).

Options:
  --project NAME     Project name (default: $PGAI_PROJECT_NAME)
  --help, -h         Show this help and exit

Prints the project's resolved cadence (mode: weekly|off, from
[release] deep_verify in project.cfg, else kanban.cfg, else the
default weekly), the most recent verdict, and every ledger
record -- resolved through the SAME functions GET /deep-verify-status
calls (never a second, independent parse of the ledger).

Read-only: never runs the cold-build lane and never writes a ledger
record. To run a verification pass, use deep-verify.sh instead.

Exit codes:
  0  Status printed
  1  Usage error
  2  Project not found
  3  Malformed ledger, or an unrecognized deep_verify config value
     (dual-channel error/message/remedy block printed to stderr)
```
<!-- OPERATOR-VERBS-GENERATED:END verb=deep-verify-status -->

<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=archive -->
#### `archive`

Generated from `scripts/archive.sh --help` and this verb's census row. Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

- **Script:** `scripts/archive.sh`
- **Mutates state:** yes
- **Idempotence:** idempotent
- **Class:** action
- **REST twin:** `POST /operations/archive`
- **Implementation:** `module:pgai_agent_kanban.archive.run.run_archive`

```text
Usage: archive.sh [OPTIONS]

Move a project's finished items beyond archive_count into $KANBAN_ROOT/archive/<project>/, preview it, restore one item, or reconcile queue lines.

Options:
  --project NAME     Project to archive (required)
  --dry-run          Print the planned moves per category; change nothing
  --count N          Keep the newest N items per category (tasks: per role) live (default: archive_count)
  --restore ID       Move one archived item back, with its queue lines and logs
  --reconcile-queues Move live queue lines naming items no longer live to the archive
  --help, -h         Show this help and exit

Without --dry-run or --restore the run plans, then moves: only finished items older
than the newest N per category (tasks: per role) move, with their queue lines and
training logs, and each move is recorded in archive/<project>/ARCHIVE-LEDGER.tsv.
Nothing is deleted.
The run then moves any legacy projects/<p>/tasks/archive/requirements/ files into
archive/<project>/requirements/ (PRIORITY-NNNN items into priority/), one ledger row each.
--restore is not combined with --dry-run or --count.

--reconcile-queues runs instead of an archive run: each live tasks/queues/*_backlog.md
line naming an archived, not-live item, or a [x] line whose task folder is in neither
the live project nor the archive, moves to archive/<project>/tasks/queues/<same file>.
Lines naming live items are never touched; a second run moves nothing. Add --dry-run
to list what would move (and what is left, with why) first. Not combined with --count.

Examples:
  archive.sh --project pgai-agent-kanban --dry-run
  archive.sh --project pgai-agent-kanban --restore BUG-NNNN
  archive.sh --project pgai-agent-kanban --reconcile-queues --dry-run

Exit codes:
  0  finished (or nothing to move), dry run printed, item restored, or help shown
  1  refused before anything moved (error / message / remedy on stderr)
  2  stopped at a failing item; earlier moves are in the ledger
  3  skipped: the project's repo lock is held by another process
```
<!-- OPERATOR-VERBS-GENERATED:END verb=archive -->
<!-- OPERATOR-VERBS-GENERATED:END section=verb-reference -->

---

## See also

- [HOW_TO.md](../HOW_TO.md) — the narrative operator manual (setup, projects,
  workflows, the dashboard, stopping the system).
- [operator-api.md](operator-api.md) — every verb's REST twin, the verb
  census that governs the set, and the published contract (ICD).
- [OPERATIONS.md](OPERATIONS.md) — diagnosing and recovering from stuck
  states: "Why is this lane not moving?" for a lane that has stopped
  advancing, "When the chain halts" for CM's nine autonomous halt
  triggers and what clears each one.
- [quarantine-recovery.md](quarantine-recovery.md) — recovering a quarantined
  project.
