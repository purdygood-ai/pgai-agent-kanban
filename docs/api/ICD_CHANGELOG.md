# ICD Changelog — what each contract version added

The operator REST API is a published, versioned contract: `docs/api/icd.json`
is the OpenAPI document, `docs/api/ICD_VERSION` is its version, and
`docs/api/baselines/icd-v<version>.json` is the frozen artifact for every
version ever published. This file is the human-readable companion to those
artifacts. One entry per contract version, newest first, naming every route
the version added and every additive change it made to a route that already
existed.

Consumers pin on `info.version` (see `docs/operator-api.md`, section 9). When
that number moves, the entry here is the work list: read the routes it names,
diff your vendored `icd.json` against the new one, and extend your client.

## How this file is maintained

- **Append-only.** An entry, once published, is history. A later version adds
  a new entry above it; it never rewrites an earlier one, exactly as a
  published baseline file is never re-frozen.
- **One entry per bump, patch bumps included.** Every move of
  `docs/api/ICD_VERSION` gets an entry. A release bumps the contract at most
  once, however many routes it adds, so one entry lists everything that
  release's bump published. The entry lands with the bump, not after it.
- **Routes and additive changes only.** Each entry names the method and path
  of every route added, the request body model and response schema the verb
  census declares for it, and any new optional parameter or response field on
  an existing route. The text of an edited description is not reproduced
  here — a description edit does not move the contract surface.
- **A description-only patch bump gets a short entry.** When a bump changes
  only descriptions or examples, its entry says so, names each route whose
  description changed with one sentence on what the new text documents, and
  states that no route, request field or response field changed. It carries
  no `Routes added` or `Existing routes extended` subsections. The entry
  tells a consumer pinned on the previous version that the bump brings no
  new surface to extend, and what the reworded descriptions now say. This
  rule is the single statement of whether a description-only bump is
  recorded; `docs/operator-api.md` section 9 points here.
- **A route's authority is its census row.** Every route below has a row in
  `team/config/operator_verbs.manifest`, the operator verb census; the verb
  named beside each route is that row. Where a census row does not exist for
  a route, the route does not ship — the parity gate refuses it.

Entries begin at 1.8.0. The route set of every earlier version is frozen in
its baseline file; to recover what an earlier version added, diff the `paths`
objects of two adjacent baselines.

---

## 1.18.5

Description-only patch bump over 1.18.4. The description of
**`GET /verify-rc-state`** (`verify-rc-state`) no longer says the route runs
`verify-rc-state.sh` with a positional argument list: it now documents that
the handler calls `pgai_agent_kanban.ops.rc_state.verify_rc_state` in
process, the same function the script reaches through `pp_run_ops`, so the
envelope matches the CLI's output, and its 200 response description names
that function as the source of `stdout`. The description of
**`GET /board`** now says quarantined items are read from the
project's `rejected/` directory as well as the legacy `bugs/.rejected/`,
`priority/.rejected/` and `requirements/.rejected/` directories, and sit in
the column of the intake directory they came from. No route was added or
removed, no request or response field was added, removed, renamed or made
required, no response schema changed, and no status a route returns
changed. A client pinned on 1.18.4 has no new surface to extend.

## 1.18.4

Description-only patch bump over 1.18.3. The description of
**`POST /operations/delete`** (`delete`) no longer says every refusal
answers 500: it now documents 409 for a non-terminal item without `force`
or a key matching more than one item, 404 for a key matching nothing, the
resolver's 404 / 409 for an unknown or ambiguous `project`, 422 for a
missing or malformed field, and 500 for anything else. The description of
**`GET /verify-rc-state`** (`verify-rc-state`) no longer says every
non-zero exit answers 500: it now documents 500 for the ERROR-findings
outcome and, for a pre-flight refusal, the status the refusal's key names —
404 for an unknown project or a missing project directory, 409 for an
ambiguous prefix, 500 for an unloadable project config or an unset dev tree
path. The description of **`GET /show-queues`** (`show-queues`) no longer
says a compact-mode refusal answers 500: it now documents 404 for an
unknown project or a missing project directory and 409 for an ambiguous
prefix or no project at all. No route was added or removed, no request or
response field was added, removed, renamed or made required, no response
schema changed, and no status a route returns changed. A client pinned on
1.18.3 has no new surface to extend.

This version also publishes, under a new number, two description strings
that release v2.37.13 rewrote in place in the already-published 1.18.3
baseline (`docs/api/baselines/icd-v1.18.3.json`) without a version bump:
`info.description` now says a route calls the shared `pgai_agent_kanban.ops`
function in process and names the four routes that start a process, where
it had said every endpoint shells out to its operator script; and the
description of **`GET /deep-verify-status`** now says the cadence resolves
from project.cfg, then kanban.cfg, then the default `weekly`, where it had
named project.cfg alone. The 1.18.3 baseline is left as v2.37.13 shipped it,
so a client that vendored 1.18.3 before that release holds those two older
strings; no route, field or schema differs between the two copies.

## 1.18.3

Description-only patch bump over 1.18.2. The description of
**`GET /projects/{name}`** now documents what `ceilings.max_major`,
`ceilings.max_minor` and `ceilings.max_patch` carry: the ceiling in effect,
where an absent key (or an absent project.cfg) reads as its documented
default (0, 13 and 21), `null` means no ceiling (`none` in project.cfg), and
a malformed value is also `null` with one `warnings` entry naming the file,
key and value. The descriptions of **`POST /operations/set-version-ceiling`**
(`set-version-ceiling`) and its body model `SetVersionCeilingBody` now say
that `no_minor` / `no_major` write `max_minor = none` / `max_major = none`
instead of removing the line, and that `show` reports the value in effect.
No route was added or removed, no request or response field was added,
removed, renamed or made required, and no response schema changed. A client
pinned on 1.18.2 has no new surface to extend; one that read a `null`
ceiling as "no key in project.cfg" should now read it as "no ceiling".

## 1.18.2

Description-only patch bump over 1.18.1. The description of
**`POST /operations/create-project`** (`create-project`) and its body model
`CreateProjectBody` no longer say that `dev_tree` and `git_repo` get only a
non-empty check: they now document that `workflow_type` must be one plain
name, matched by name against the ready workflow plugins; that `git_remote`,
`dev_tree` and `git_repo` may hold no newline or other control character;
that `dev_tree` must be an absolute path with no `..` segment; and that a
refused value returns 422 with `create_project.<field>_invalid` naming the
field. The description of **`POST /operations/add-project`** (`add-project`)
now documents the 422 `add_project.project_invalid` refusal of a project
name a projects.cfg section cannot hold. No route was added or removed, no
request or response field was added, removed, renamed or made required, and
no response schema changed. A client pinned on 1.18.1 has no new surface to
extend.

## 1.18.1

Description-only patch bump over 1.18.0. The description of
**`POST /operations/add-project`** (`add-project`) no longer says nothing is
created on disk except the registry entry: it now documents that a project
directory with no `README.md` gets one seeded from the project README
template, whether or not the project is already registered, that an existing
`README.md` is never overwritten and nothing else under the project directory
is touched, that `dry_run` only reports the seeding, and that a registered
project at the same priority is "nothing to do" only when its `README.md` is
present. No route was added or removed, no request or response field was
added, removed, renamed or made required, and no response schema changed. A
client pinned on 1.18.0 has no new surface to extend.

## 1.18.0

`POST /operations/archive` gains an optional request field that moves live
queue lines naming items no longer in the live project into the archive's
queue files. Additive minor bump over 1.17.1: no route was added or removed,
one optional request field was added to an existing route, no field was
removed, renamed or made required, and no response schema changed.

### Routes added

None.

### Existing routes extended (additive)

- **`POST /operations/archive`** (`archive`, action; body `ArchiveBody`,
  response `DualChannelShellEnvelope`) — new optional body field
  `reconcile_queues` (boolean, default `false`), the REST twin of
  `archive.sh --reconcile-queues`. Instead of an archive run, every live
  `tasks/queues/*_backlog.md` line naming a task, bug or priority that is
  archived and not live, or an `[x]` line naming a task whose folder is in
  neither the live project nor the archive, moves to
  `archive/<project>/tasks/queues/<same file>` with its marker and text
  unchanged; a line naming a live item is never touched, and a second call
  moves nothing. With `dry_run` it lists in `stdout` the lines that would
  move and the lines it would leave, with the reason, and moves nothing.
  Combined with `count` or `restore` it is refused `500` with
  `archive.conflicting_options`; a held repo lock returns
  `archive.repo_lock_busy` (exit 3) exactly as an archive run does.

## 1.17.1

Description-only patch bump over 1.17.0. The description of
**`GET /show`** (`show`) now documents the archive fallback: a key matching
nothing live is read from the project's archive
(`<kanban_root>/archive/<project>/`), `stdout` is the archived document
unchanged, `stderr` carries the `archived: <key> ...` line naming the
archive path, the exit code is 0, and a live item always wins over an
archived one. No route was added or removed, no request or response field
was added, removed, renamed or made required, and no response schema
changed. A client pinned on 1.17.0 has no new surface to extend.

## 1.17.0

`POST /operations/archive` moves a project's finished items beyond
`[archive] archive_count` into the kanban root's `archive/<project>/`,
previews that run, or restores one archived item. Additive minor bump over
1.16.1: one route was added, no route was removed, no request or response
field was added to, removed from or renamed on an existing route, and no
response schema changed.

### Routes added

- **`POST /operations/archive`** (`archive`, action; body `ArchiveBody`,
  response `DualChannelShellEnvelope`) — the REST twin of
  `scripts/archive.sh`; both call `pgai_agent_kanban.archive.run.run_archive`.
  Body fields: `project` (string, required by the route: a body without it
  is refused `422` with `archive.project_required`), `dry_run` (boolean,
  default `false`), `count` (integer or digit string, or `null`; items kept
  live per category, overriding `archive_count`) and `restore` (string or
  `null`; an archived ID to move back live). With neither `dry_run` nor
  `restore` the route plans, then moves the selected items and records one
  ledger row per item. `dry_run` returns the planned moves per category in
  `stdout` — the same text `archive.sh --dry-run` prints — and moves
  nothing; unlike the other action routes it does not describe a CLI call.
  Refusals (`project_resolve.not_found`, `archive.invalid_count`,
  `archive.conflicting_options`, `archive.project_missing`,
  `archive.restore_not_archived`, `archive.restore_live_exists`), a run
  stopped at a failing item (`archive.run_failed`) and a run skipped on a
  held repo lock (`archive.repo_lock_busy`) return `500` with `error`,
  `message` and `remedy` lifted to top-level keys, and the CLI's exit code
  (1, 2 or 3) as `exit_code`.

### Existing routes extended (additive)

None.

## 1.16.1

Description-only patch bump over 1.16.0. The description of
**`POST /operations/close`** (`close`) and of its request body `CloseBody`
now name `done` and `wont-do` as the only close states for the `state`
field, and the route description adds that any other value, the retired
`superseded` included, is refused with `close.invalid_state`. The 1.16.0
descriptions still listed `superseded` as a close state. No route was added
or removed, no request or response field was added, removed, renamed or made
required, and no response schema changed. A client pinned on 1.16.0 has no
new surface to extend; one that sends `state: "superseded"` should expect the
refusal.

## 1.16.0

`GET /status` returns the project's resolved ship posture as a
`ship_posture` field beside the shell envelope. Additive minor bump over
1.15.0: no route was added or removed, one response field was added to an
existing route, no response field was removed or renamed, no request field
became required, and no response schema changed (the route declares no
response model; the field is documented in its description).

### Routes added

None.

### Existing routes extended (additive)

- **`GET /status`** (status summary read; response the shell envelope) —
  new response field `ship_posture` (string or `null`), after `warnings`:
  `"matrix"` or `"always"`, what `[release] ship_posture` resolves to for
  the project once the remote-lane guard is applied. A project that pushes
  to origin with `always` set but not confirmed by
  `ship_posture_confirm_remote = true` reads `"matrix"`, and the resolver's
  warning line is in `stderr`. `null` when the read is refused (non-zero
  `exit_code`). The same value is the summary's new last line,
  `Ship posture: <value>`, in `stdout`; every earlier line is unchanged.

## 1.15.0

`GET /dashboard/{pane}` takes a `column_type` query parameter, so the `input`
and `queue` panes render a named board column; `POST /operations/cancel-rc`
takes `force`; and `POST /operations/unwind-rc` accepts `true` as its
confirmation. Nineteen existing routes now call, in process, the function
their CLI script reaches through `pp_run_ops` instead of running the script,
and their descriptions say so. Additive minor bump over 1.14.0: no route was
added or removed, no response field was added, removed or renamed, no request
field became required in the schema, and no response schema changed.

### Routes added

None.

### Existing routes extended (additive)

- **`GET /dashboard/{pane}`** (dashboard pane read; response the shell
  envelope) — new optional query parameter `column_type` (string or
  `null`): the board column the `input` pane (`bugs`, `priorities`,
  `requirements`) or the `queue` pane (`pm`, `coder`, `writer`, `tester`,
  `cm`) renders, matched case-insensitively so a `GET /board` column name
  is accepted. The pane returns one row per item across every registered
  project, coloured by the status `GET /board` reports. An `input` or
  `queue` call with no `column_type`, or one the pane does not render, is
  refused `422` with error `dashboard_pane.column_required` before any
  script runs; under 1.14.0 such a call rendered no board content. The
  other panes ignore the parameter with a warning.
- **`POST /operations/cancel-rc`** (`cancel-rc`, action; body
  `CancelRcBody`, response `DualChannelShellEnvelope`) — new optional
  request field `force` (boolean, default `false`), the CLI's `--force`:
  bypasses only the Active-RC mismatch check. It is never a confirmation;
  `yes` is still required.
- **`POST /operations/unwind-rc`** (`unwind-rc`, action; body
  `UnwindRcBody`, response `DualChannelShellEnvelope`) — request field
  `confirm` now also accepts a boolean. `true`, or the typed phrase
  `unwind-rc <project> <key>` byte-for-byte, confirms the call; missing,
  `false` or empty is refused `422` with `unwind_rc.confirm_required`, any
  other string with `unwind_rc.confirm_mismatch`.

### Existing routes changed (provenance of response text)

Each route below calls the named function in process; the route starts no
script subprocess. `stdout`, `stderr` and `exit_code` carry the text and
exit code the CLI script prints, and refusals keep their stable keys.

- **`POST /operations/add-project`** (`add-project`, `ops.projects.add_project`),
  **`POST /operations/create-project`** (`create-project`, `ops.projects.create_project`),
  **`POST /operations/remove-project`** (`remove-project`, `ops.projects.remove_project`),
  **`POST /operations/defer`** (`defer`, `ops.write.defer`),
  **`POST /operations/undefer`** (`undefer`, `ops.write.undefer`),
  **`POST /operations/set-version-ceiling`** (`set-version-ceiling`, `ops.write.set_version_ceiling`),
  **`POST /operations/switch-provider`** (`switch-provider`, `ops.write.switch_provider`),
  **`POST /operations/release-locks`** (`release-locks`, `ops.locks.release_lock`),
  **`POST /operations/cancel-rc`** (`cancel-rc`, `cm.cancel.cancel_rc`),
  **`POST /operations/ship-rc`** (`ship-rc`, `cm.ship.ship_rc`) and
  **`POST /operations/unwind-rc`** (`unwind-rc`, `cm.unwind.unwind_rc`) —
  action routes with response `DualChannelShellEnvelope`. The descriptions
  now document: `remove-project` refuses a project with an Active RC in
  both modes (`remove_project.active_rc`), `force` included; `release-locks`
  with `force` signals a live holder and waits up to `kill_grace_seconds`
  for it to release, and may refuse with `release_locks.holder_not_released`
  (exit 5); `defer`, `undefer` and `set-version-ceiling` accept an
  unambiguous project prefix; `switch-provider` accepts a provider name in
  any case; a `dry_run` call to `unwind-rc` returns the equivalent CLI call.
- **`GET /costs`** (`lib.metrics.cost_report`), **`GET /metrics`**
  (`rc-metrics`, `lib.metrics.show_metrics`), **`GET /status`**
  (`ops.read.get_status`), **`GET /test-report`**
  (`ops.read.get_test_report`), **`GET /rejected`**
  (`ops.read.list_rejected`), **`GET /release-locks`**
  (`ops.locks.list_locks`) and
  **`GET /projects/{project}/tasks/{key}/readme`**
  (`ops.read.get_task_readme`) — read routes returning the text envelope;
  for `/costs` and `/metrics` this is the output returned when `format` is
  omitted.
- **`GET /verdicts`** (`why`, `ops.verdict.resolve_verdicts`; response
  `VerdictsResponse`) — no script is run or parsed. With `details=true`, a
  deferral posture that does not resolve is a `500` whose `detail` carries
  the resolver's dual-channel block.
- **`GET /board`** — the description names `pgai_agent_kanban.board` as
  the shared classifier; behavior is unchanged.

---

## 1.14.0

`POST /operations/deep-verify` and `POST /operations/cold-build-now` take an
`image` selection, so a REST call chooses the container images its run
builds the way `--image` does on the command line. Additive minor bump over
1.13.1: no route was added or removed, no response field was added, removed
or renamed, no request field became required in the schema, and the
response schema (`DualChannelShellEnvelope`) is unchanged.

### Routes added

None.

### Existing routes extended (additive)

- **`POST /operations/deep-verify`** (`deep-verify`, action; body
  `DeepVerifyBody`, response `DualChannelShellEnvelope`) and
  **`POST /operations/cold-build-now`** (`cold-build-now`, action; body
  `ColdBuildNowBody`, response `DualChannelShellEnvelope`) — new optional
  request field `image`: a list of image names declared in
  `team/config/container-images.txt`, or the string `"all"`. Each entry is
  passed to the script as one `--image <entry>`, in order. A name the
  manifest does not declare is refused `422` before anything is spawned,
  `dry_run` included, with error `container_tier.unknown_image`. Omitted
  (or `null`), the script runs with no `--image`, exactly as before:
  `kanban.cfg [release] container_images` applies, else the script lists
  the images and builds nothing. The field never grants the container
  tier's operator authorization; the API server's own environment must
  still carry it.

---

## 1.13.1

Description-only patch bump over 1.13.0. The description of
**`GET /show-queues`** (`show-queues`) now documents that a compact-mode call
with no `project`, on a server whose environment has no
`PGAI_PROJECT_NAME`, refuses with `show_queues.no_project`: HTTP 500,
`exit_code` 1, empty `stdout`, the dual-channel block in `stderr`, and no
pane. The 1.13.0 description said such a call renders all-zero counters. No
route was added or removed, no request or response field was added, removed,
renamed or made required, and no response schema changed. A client pinned on
1.13.0 has no new surface to extend; one that calls the route without
`project` should expect the refusal.

---

## 1.13.0

Ten action routes now call the `pgai_agent_kanban.ops.write` function their
CLI script reaches through `pp_run_ops`, in process, instead of running the
script: the census `impl` column for each reads `module:<function>`. Their
`stdout` and `stderr` are therefore no longer a pass-through of the script's
own output; the route composes the same narration and refusal text the
script prints, and returns the script's exit code. Two read routes,
`GET /show` and `GET /show-queues`, likewise call the
`pgai_agent_kanban.ops.read` function their CLI script reaches. Minor bump
over 1.12.0: no route was added or removed, no request or response field was
added, removed, renamed or made required, and the response schemas
(`DualChannelShellEnvelope`, `ShellEnvelope`) are unchanged.

### Routes added

None.

### Existing routes changed (provenance of response text)

- **`POST /operations/halt`** (`halt`, `ops.write.halt`),
  **`POST /operations/unhalt`** (`unhalt`, `ops.write.unhalt`),
  **`POST /operations/halt-global`** (`halt-global`, `ops.write.halt_global`),
  **`POST /operations/unhalt-global`** (`unhalt-global`, `ops.write.unhalt_global`),
  **`POST /operations/halt-after`** (`halt-after`, `ops.write.halt_after`),
  **`POST /operations/intake`** (`intake`, `ops.write.deposit_intake`),
  **`POST /operations/close`** (`close`, `ops.write.close_item`),
  **`POST /operations/wontdo`** (`wontdo`, `ops.write.wontdo_item`),
  **`POST /operations/delete`** (`delete`, `ops.write.delete_item`),
  **`POST /operations/reset`** (`reset`, `ops.write.reset_item`) — all
  action routes with response `DualChannelShellEnvelope`. `stdout` carries
  the script's success narration, `stderr` and `exit_code` the script's
  refusal text and code, and the lifted `error`/`message`/`remedy` the same
  triple, as before. One difference: `POST /operations/reset` for a
  `TESTER-` key no longer appends reset.sh's "TESTER worktree: ..." lines,
  because the reset itself refuses, or with `force` removes, any worktree
  for the task before it resets anything, leaving none for that step.
- **`GET /show`** (`show`, `ops.read.get_task_status`) and
  **`GET /show-queues`** (`show-queues`, `ops.read.get_queues`) — read
  routes with response `ShellEnvelope`. `GET /show`'s `stdout` is the
  document show.sh prints, with its line endings as written (the script
  pass-through used to turn CRLF into LF); `stderr` and `exit_code` follow
  show.sh: the ambiguity notice for a key prefix matching several items
  (exit 0), a `project_resolve.*` refusal (1), a key matching nothing (3).
  Compact-mode `GET /show-queues`' `stdout` is the pane the shared renderer
  prints for show-queues.sh; its `stderr` no longer carries the dashboard
  snapshot cache's narration, and a call with no `project` (and no
  `PGAI_PROJECT_NAME` in the server's environment) returns the all-zero
  pane with exit code 0 where the script exited 1. `details=true` is
  unchanged.

---

## 1.12.0

`GET /verdicts?details=true` publishes how long each release lock has been
held, measured from the acquisition time recorded in the lock's provenance,
beside the existing refresh age. Additive minor bump over 1.11.0: no route
was added or removed, no response field was removed or renamed, and no
request field became required in the schema.

### Routes added

None.

### Existing routes extended (additive)

- **`GET /verdicts`** (`why`, read; response `VerdictsResponse`) — each
  `release_locks[]` entry gains `held_seconds` (integer or `null`): now
  minus the lock record's `acquired_at`, the same held time
  `why.sh --details` and `release-locks.sh` render. It is `null` when the record carries no
  `acquired_at` (a pre-upgrade lock) and is never derived from the lock
  file's modification time. The existing `age_seconds` field is unchanged
  in value and is now documented as what it always measured: seconds since
  the lock file was last refreshed (a heartbeat age), not held time.

---

## 1.11.0

`POST /operations/remove-project` and `POST /operations/unwind-rc` take a
`confirm` string that separates confirmation from `force`, so each route
follows its script's own safety ladder: the default call is the
non-destructive path with every script guard honored, `force` is the
explicit escalation, and a destructive or guard-skipping call must also
carry the exact confirm string. Additive minor bump over 1.10.0: no route
was added or removed, no response field was removed or renamed, and no
request field became required in the schema.

### Routes added

None.

### Existing routes extended (additive)

- **`POST /operations/remove-project`** (`remove-project`, action; body
  `RemoveProjectBody`, response `DualChannelShellEnvelope`) — new optional
  request field `confirm` (string). The default call (no `force`) now runs
  the script's safe mode -- unregister from `projects.cfg`, leave the
  directory in place, and surface the script's Active-RC refusal
  dual-channel at `500` -- where it was previously refused `422` for
  lacking `force`. A live `force` call (the `rm -rf`, guard-skipping mode)
  must carry `confirm` equal to `remove-project <project>` byte-for-byte;
  without it the call is refused `422` before any subprocess runs
  (`remove_project.confirm_required` when absent,
  `remove_project.confirm_mismatch` when wrong). A `dry_run` preview needs
  neither `force` nor `confirm`.
- **`POST /operations/unwind-rc`** (`unwind-rc`, action; body
  `UnwindRcBody`, response `DualChannelShellEnvelope`) — new optional
  request field `confirm` (string). Every call that is not a `dry_run`
  preview must carry `confirm` equal to `unwind-rc <project> <key>`
  byte-for-byte; the route then passes the script's new `--confirm` flag,
  which answers its interactive prompt. Without it the call is refused
  `422` before any subprocess runs (`unwind_rc.confirm_required` when
  absent, `unwind_rc.confirm_mismatch` when wrong) -- including a call that
  sends `force: true` alone, which previously ran. `force` still passes
  `--force` and bypasses only the script's Active-RC version-mismatch
  check; it no longer stands in for the confirmation. `dry_run` spawns
  nothing and never forwards `--dry-run` to the script.

## 1.10.0

`GET /agent-metrics` takes the same span choices as `agent-metrics.sh` and
returns the totals and shipped / excluded counts the CLI shows. Additive
minor bump over 1.9.0: no route was added or removed, no response field was
removed or renamed, and no request field became required.

### Routes added

None.

### Existing routes extended (additive)

- **`GET /agent-metrics`** (`agent-metrics`, read; response
  `AgentMetricsResponse`) — three new optional query parameters, mirroring
  the CLI's span flags: `last` (integer, the newest N shipped RCs), `all`
  (boolean, every shipped RC) and `since` (string `vX.Y.Z`, every shipped RC
  at or above that version). `window` is unchanged. At most one of
  `window`, `last`, `all=true` and `since` may be given; a conflict, a
  non-positive `window` or `last`, or a `since` that is not a version is
  `422` with a `detail` naming the parameter. New top-level response keys:
  `span` (`{"kind": "last"|"all"|"since", "count": int|null, "since":
  str|null}`), `shipped_count` and `excluded_count` (the shipped RCs the
  span selected and the non-shipped rollups inside it). Each `agents` entry
  gains `total_tokens` and `total_tasks` (integers). `avg_wall_minutes`,
  `avg_tokens` and `avg_tasks` keep their names and remain JSON numbers,
  now carrying one decimal (`2.5`) by the CLI's rounding rule instead of a
  whole number; `window` remains an integer — the span's RC bound, or
  `shipped_count` for an `all` or `since` span.

---

## 1.9.0

Every action route now refuses dual-channel, and `POST /operations/halt-after`
accepts the CLI's minimal parameter set. Additive minor bump over 1.8.0: no
route was added or removed, no response field was removed, and no request
field became required.

### Routes added

None.

### Existing routes extended (additive)

- **`POST /operations/halt-after`** (`halt-after`, action) — the body field
  `key` is now optional. When the body omits it, no `--key` is passed and the
  script's own default (`rc`) applies, so `{"project": P, "until": "vX.Y.Z"}`
  arms the same sentinel file `halt-after.sh --project P --until vX.Y.Z`
  arms. `project` is declared optional in the schema so that a body without
  it is refused with the dual-channel triple rather than a framework
  validation body; the route still requires it.

- **Response schema on every remaining action route** — the census row of
  each route below moved from `ShellEnvelope` to `DualChannelShellEnvelope`,
  the shape `halt-after`, `cold-build-now`, `cancel-rc`, and `release-locks`
  adopted in 1.8.0: a refused call (non-zero exit) returns the standard
  envelope extended with the top-level keys `error`, `message`, and `remedy`,
  and an API-layer refusal (a missing required field) carries the same three
  keys at `422`. The success shape is unchanged. In the OpenAPI document this
  lands as each operation's description of the refused-call shape, and as
  the request body models in the last column, whose route-required fields
  are now declared optional in the schema for the same reason as
  `HaltAfterBody`'s.

| Method | Route | Verb | Request body fields declared optional |
|---|---|---|---|
| `POST` | `/operations/halt` | `halt` | `HaltBody`: `project` |
| `POST` | `/operations/unhalt` | `unhalt` | `HaltBody`: `project` |
| `POST` | `/operations/halt-global` | `halt-global` | no body |
| `POST` | `/operations/unhalt-global` | `unhalt-global` | no body |
| `POST` | `/operations/defer` | `defer` | `DeferBody`: `project` |
| `POST` | `/operations/undefer` | `undefer` | `UndeferBody`: `project` |
| `POST` | `/operations/deep-verify` | `deep-verify` | `DeepVerifyBody`: unchanged (every field already optional) |
| `POST` | `/operations/wake-now` | `wake-now` | `WakeNowBody`: unchanged (every field already optional) |
| `POST` | `/operations/unwind-rc` | `unwind-rc` | `UnwindRcBody`: `project`, `key` |
| `POST` | `/operations/reset` | `reset` | `ResetBody`: `project` |
| `POST` | `/operations/close` | `close` | `CloseBody`: `project`, `key` |
| `POST` | `/operations/wontdo` | `wontdo` | `WontdoBody`: `project`, `key` |
| `POST` | `/operations/delete` | `delete` | `DeleteBody`: `project`, `key` |
| `POST` | `/operations/intake` | `intake` | `IntakeBody`: `project`, `filename`, `content` |
| `POST` | `/operations/set-version-ceiling` | `set-version-ceiling` | `SetVersionCeilingBody`: `project` |
| `POST` | `/operations/switch-provider` | `switch-provider` | `SwitchProviderBody`: `provider` |
| `POST` | `/operations/create-project` | `create-project` | `CreateProjectBody`: `project` |
| `POST` | `/operations/add-project` | `add-project` | `AddProjectBody`: `project` |
| `POST` | `/operations/remove-project` | `remove-project` | `RemoveProjectBody`: `project` |
| `POST` | `/operations/ship-rc` | `ship-rc` | `ShipRcBody`: `project`, `key`, `confirm` |

Notes on the extended routes:

- **`POST /operations/wake-now`**'s census row moved with the rest, but its
  operation object in the document is byte-identical to 1.8.0's: every
  `WakeNowBody` field was already optional and its description did not
  change. Consumers diffing the two artifacts will see no change on that
  path.
- The four action routes already on `DualChannelShellEnvelope` in 1.8.0
  (`cold-build-now`, `cancel-rc`, `release-locks`, and `halt-after` apart
  from the `key` change above) are unchanged.

### Response shapes introduced

None. `DualChannelShellEnvelope` is unchanged; after this version it is the
declared response schema of every action route in the census.

### Request body models introduced

None. Sixteen existing models changed as the table above records:
`AddProjectBody`, `CloseBody`, `CreateProjectBody`, `DeferBody`,
`DeleteBody`, `HaltAfterBody`, `HaltBody`, `IntakeBody`,
`RemoveProjectBody`, `ResetBody`, `SetVersionCeilingBody`, `ShipRcBody`,
`SwitchProviderBody`, `UndeferBody`, `UnwindRcBody`, `WontdoBody`.

---

## 1.8.0

Closes REST parity across the operator verb set: after this version every
active verb in the census has a REST twin, and the parity gate keeps it that
way. Additive minor bump over 1.7.0.

### Routes added

| Method | Route | Twin of | Request | Response schema |
|---|---|---|---|---|
| `GET` | `/release-state` | `release-state` (read) | query `project` (required) | `ReleaseStateResponse` |
| `GET` | `/deep-verify-status` | `deep-verify-status` (read) | query `project` (required) | `DeepVerifyStatusResponse` |
| `POST` | `/operations/cold-build-now` | `cold-build-now` (action) | body `ColdBuildNowBody`: `project` (required), `dry_run` | `DualChannelShellEnvelope` |
| `POST` | `/operations/cancel-rc` | `cancel-rc` (action) | body `CancelRcBody`: `project`, `key`, `yes` (required unless `dry_run` is true), `dry_run` | `DualChannelShellEnvelope` |
| `POST` | `/operations/release-locks` | `release-locks` (action, `--release` mode) | body `ReleaseLocksBody`: `release` (required), `force`, `dry_run` | `DualChannelShellEnvelope` |

Notes on the added routes:

- **`GET /release-state`** returns the project's `release-state.md` fields —
  `active_rc`, `rc_opened_at`, `rc_opened_by_task`, `last_released` — validated
  through the same loader the CLI verb uses. An unset field is `null`. A
  project that is not registered is `404`; a field that fails validation is
  `422` carrying `error`/`message`/`remedy`, never a guessed value.
- **`GET /deep-verify-status`** returns the project's DEEP-VERIFY cadence
  (`mode`), its ledger `entries`, and the `latest` entry (`null` when the
  project has never been deep-verified). It reads the ledger; it never runs
  the lane — that is `POST /operations/deep-verify`.
- **`POST /operations/cold-build-now`** dispatches the cold-build lane
  detached and returns as soon as the child starts. The response confirms
  the run began; it never carries a verdict.
- **`POST /operations/cancel-rc`** stands the body field `yes` in for the
  CLI's interactive confirmation. A request without `yes: true` and without
  `dry_run: true` is refused `422` before any process runs.
- **`POST /operations/release-locks`** is the mutating half of the
  `release-locks` verb. The read-only listing stays on `GET /release-locks`;
  its `422` refusal of a supplied `release` or `force` names this route as
  the mutating twin.

### Existing routes extended (additive)

- **`GET /verdicts`** gained the optional query parameter `details`. With
  `details=true` (which requires `project`), the response carries three
  additional keys — `posture`, `release_locks`, `queue_tails` — the JSON face
  of the CLI's `why --details` pane, resolved through the same functions.
  Without it, the response shape is unchanged.
- **`POST /operations/halt-after`** joins the dual-channel envelope: a
  refused call returns the standard envelope extended with the script's own
  `error`, `message`, and `remedy` as top-level keys. The success shape is
  unchanged.

### Response shapes introduced

- **`DualChannelShellEnvelope`** — the standard shell envelope
  (`exit_code`, `stdout`, `stderr`, `warnings`) extended on a refused call
  with the top-level keys `error`, `message`, and `remedy`. Returned by the
  four action routes named above. An API-layer refusal on those routes
  (a missing required field, a confirmation not given) carries the same
  three keys at `422`.
- **`ReleaseStateResponse`** — `{project, active_rc, rc_opened_at,
  rc_opened_by_task, last_released, warnings}` on success; `{project, error,
  message, remedy, warnings}` on refusal.
- **`DeepVerifyStatusResponse`** — `{project, mode, entries, latest,
  warnings}` on success; `{project, error, message, remedy, warnings}` on
  refusal.

### Request body models introduced

`CancelRcBody`, `ColdBuildNowBody`, `ReleaseLocksBody`. Required fields on
each are declared optional in the schema so that a missing one is refused
with the dual-channel triple rather than a framework-default validation body.
