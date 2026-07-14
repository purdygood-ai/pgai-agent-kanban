# Operator REST API

> **Status: Experimental** — full automated coverage; contract published
> (ICD 1.1.0); operator field-testing in progress; surfaces may be
> refined.
>
> The API is production-supported in the sense that it is exercised on
> every release, has a versioned ICD, and is what the sibling browser UI
> consumes. It is "experimental" because the HTTP surface itself is
> still being shaken out against real operator use — endpoint shapes,
> body fields, and error envelopes may be refined before the API
> graduates to general availability. The scripts the API fronts are
> stable and unaffected by that refinement.

A localhost-only HTTP interface to the operator command surface. The primary
consumer is the sibling pgai-agent-kanban-ui project (a browser dashboard).
The secondary consumer is `curl` — anything you can do at a shell prompt
against the operator scripts, you can do through the API.

This page is the operator manual for the API: what it is, how to start it,
how to reach it from a remote machine, the verb-discipline rule, the endpoint
table, `curl` examples per endpoint category, and what is deliberately
deferred to future releases.

The interactive OpenAPI/Swagger UI is served at `/docs` on the running
service. This page is the operator overview; `/docs` is the request-builder.

---

## 1. Overview

### What the API is

A FastAPI service under `team/pgai_agent_kanban/api/`. It is a **thin
adapter**: every endpoint shells out to the one canonical operator script,
passes query/body parameters as the script's flags, and returns the script's
exit code, stdout, and stderr in a JSON envelope.

The scripts remain the single implementation per operation. The API never
re-implements logic, never becomes a second scheduler, and never rewrites
what the script prints.

### The envelope

Every endpoint that fronts a script returns the same JSON shape:

```json
{
  "exit_code": 0,
  "stdout": "…",
  "stderr": "…",
  "warnings": []
}
```

HTTP status is derived from `exit_code`:

- `200 OK` when `exit_code == 0`.
- `500 Internal Server Error` when `exit_code` is non-zero.
- `422 Unprocessable Entity` when required parameters are missing or invalid.

The envelope is raw and honest. If a script prints a warning to stderr and
exits `0`, the envelope shows the warning in `stderr` and returns HTTP 200.
The API never reinterprets script output.

The `warnings` field is always present, on every response, and is `[]` when
the request was clean. It captures unknown inputs — an unknown query
parameter on a read, or an unknown body field on an operation — without
failing the request. See [Section 6](#6-universal-contracts-dry_run-and-warnings)
for the full contract and worked examples.

### The thin-adapter promise

- **One implementation per operation.** The script is authoritative. The
  API is a transport, not a policy layer.
- **Names preserved.** Endpoint query and body field names match each
  script's flag names verbatim (with `-` in flag names mapping to `_` in
  Python identifiers for body fields — `dry-run` becomes `dry_run`).
- **Guards propagate.** When `delete.sh` refuses on an ambiguous key, or
  `unwind-rc.sh` refuses without a HALT, the refusal reaches the caller
  unchanged. The API does not re-check.
- **State stays on disk.** The framework has no database; the API adds none.
- **Parity is a property, not a habit.** The pre-flight suite runs
  `team/scripts/lint_api_parity.py` beside `lint_orphan_tests`: for every
  script the API fronts, the lint extracts the accepted operator flags
  from the argparse spec and asserts the corresponding body model's
  fields are a superset (modulo the documented equivalences —
  `intake`'s `file` becomes `filename` + `content`, hyphens become
  underscores). A script that grows a new operator flag fails the suite
  until the API grows the matching body field. Parity does not rot
  silently.

### Non-goals for this release

- **No authentication, no TLS.** Loopback binding is the sole access-control
  mechanism. Auth and TLS are documented in [Section 8](#8-future-work) as
  deferred.
- **No chain triggering.** No run-discovery endpoint, no wake endpoint.
  Cron remains the sole scheduler. The API manages inputs and reads state
  — exactly like an operator at a shell — but it does not start work.
- **No WebSocket or streaming.** The UI polls GET endpoints.
- **No script rewrites.** The scripts are the implementation; the API wraps
  them. Any refactor into a shared Python ops library is a separate,
  post-feedback effort.

---

## 2. Starting the server

### The wrapper script

`scripts/api-server.sh` manages the API process. It backgrounds a `uvicorn`
worker, writes a pidfile and log to the framework temp root, and handles
stale pidfile cleanup on `status`.

```bash
scripts/api-server.sh start
scripts/api-server.sh status
scripts/api-server.sh stop
```

Subcommands:

| Subcommand | Effect |
|---|---|
| `start`  | Start the API in the background. No-op with a message when already running. |
| `stop`   | Send `SIGTERM` and remove the pidfile. No-op when not running. |
| `status` | Print `running (pid N)` or `not running`. Cleans up stale pidfiles. |

Flags:

| Flag | Effect |
|---|---|
| `--kanban-root <path>` | Override `PGAI_AGENT_KANBAN_ROOT_PATH` for this invocation. |
| `--help` / `-h`        | Print usage and exit 0. |

Environment:

| Variable | Purpose |
|---|---|
| `PGAI_AGENT_KANBAN_ROOT_PATH` | Kanban root; required so the server finds `kanban.cfg` and `VERSION`. |
| `PGAI_AGENT_KANBAN_TEMP_DIR`  | Framework temp root; pidfile and log live here. Defaults to `/tmp/pgai_kanban_tmp`. |
| `PGAI_DEV_TREE_PATH`          | Prepended to `PYTHONPATH` so the package is importable from a dev tree. |

The pidfile is at `${PGAI_AGENT_KANBAN_TEMP_DIR}/api/api-server.pid` and the
log at `${PGAI_AGENT_KANBAN_TEMP_DIR}/api/api-server.log`. Neither pollutes
the kanban root or the git tree.

### Configuration keys

The `[api]` section of `kanban.cfg` controls the bind host and port:

```ini
[api]
host = 127.0.0.1
port = 8300
```

| Key | Default | Effect |
|---|---|---|
| `host` | `127.0.0.1` | Address `uvicorn` binds to. Must be a loopback address (see [Section 3](#3-access-model)). |
| `port` | `8300` | TCP port the server listens on. |

When `kanban.cfg` is absent or the `[api]` section is missing, both keys fall
back to their defaults. This makes the service startable with no configuration
file at all — useful for local testing.

### Verifying the server is up

`GET /health` is the liveness endpoint. It also reports the installed
kanban version.

```bash
curl -sS http://127.0.0.1:8300/health
# {"service":"pgai-agent-kanban-api","kanban_root":"/home/rocky/pgai_agent_kanban","version":"v1.2.0"}
```

The interactive Swagger UI is at `http://127.0.0.1:8300/docs`.

### systemd unit example (commented, do not enable blindly)

The API is deliberately not a cron citizen — the operator starts it when
wanted. If you want it up at boot, adapt the following unit to your
install. Review every path before enabling; this block is illustrative,
not a drop-in file.

```ini
# /etc/systemd/system/pgai-agent-kanban-api.service
#
# [Unit]
# Description=pgai-agent-kanban operator REST API (localhost)
# After=network-online.target
# Wants=network-online.target
#
# [Service]
# Type=simple
# User=rocky
# Group=rocky
# Environment=PGAI_AGENT_KANBAN_ROOT_PATH=/home/rocky/pgai_agent_kanban
# Environment=PGAI_AGENT_KANBAN_TEMP_DIR=/tmp/pgai_kanban_tmp
# ExecStart=/home/rocky/pgai_agent_kanban/scripts/api-server.sh start
# ExecStop=/home/rocky/pgai_agent_kanban/scripts/api-server.sh stop
# RemainAfterExit=yes
# Restart=on-failure
# RestartSec=5s
#
# [Install]
# WantedBy=multi-user.target
```

The unit is shown inside a commented block on purpose. This document
distributes an example, not a live systemd unit — leaving hash marks on
every line prevents copy-and-paste installation without inspection. Strip
the leading `# ` per line if you decide to install it, and confirm the
paths, user, and environment lines match your install first.

---

## 3. Access model

### Loopback-only by design

The server binds to `127.0.0.1` (or another address in `127.0.0.0/8`, or
`::1`) and refuses to bind anywhere else. On startup the process checks the
configured host; if it does not resolve to a loopback address, the server
exits non-zero with:

```
ERROR: Refusing to start — configured host '<host>' is not a loopback
address. The pgai-agent-kanban API must bind to 127.0.0.1 (or another
loopback address). Access from remote hosts is by SSH tunnel or SOCKS proxy.
```

There is no configuration knob to disable this guard. Direct external
exposure is refused by design — see [Why direct binding is refused](#why-direct-external-binding-is-refused)
below.

### Reaching the API from a remote machine

You reach the API from your workstation the same way you reach any local
service on a server: an SSH tunnel or a SOCKS proxy. The API stays on
loopback; SSH does the exposure.

#### Option A — SSH port forward (single service)

Forward a local port on your workstation to `127.0.0.1:8300` on the server:

```bash
ssh -L 8300:127.0.0.1:8300 rocky@kanban-host
```

While that session is open, `http://127.0.0.1:8300/health` on your
workstation reaches the API on the server. Close the session, the tunnel
closes with it.

For a background tunnel (no interactive shell), add `-N`:

```bash
ssh -N -L 8300:127.0.0.1:8300 rocky@kanban-host
```

#### Option B — SOCKS proxy (multiple services)

If you also want to reach the dashboard or other loopback services on the
same host, run a SOCKS proxy instead of one tunnel per port:

```bash
ssh -D 1080 -N rocky@kanban-host
```

Then point `curl` (or a browser) at the SOCKS proxy:

```bash
curl --socks5-hostname 127.0.0.1:1080 http://127.0.0.1:8300/health
```

The browser UI project will consume the API the same way — through a
tunnel or a SOCKS proxy from the operator's workstation.

### CORS — browser access from loopback origins

Browser pages served from any loopback origin may call the API. "Loopback
origin" means an `http` or `https` URL whose host is `127.0.0.1` or
`localhost`, on any port — for example `http://127.0.0.1:8000` (the default
serve.sh port) or `http://localhost:3000`.

Non-loopback origins receive no `Access-Control-Allow-Origin` header and are
blocked by the browser. `allow_origins=["*"]` (open wildcard) is intentionally
not used: the mutation endpoints (`POST /operations/*`) would otherwise be
driveable by any web page an operator's browser visits — the loopback story
must hold at the CORS layer exactly as it holds at the bind layer.

The regex form is required (not a static list) because the UI's serving port
is operator-chosen and may vary between installs.

A reverse-proxy same-origin deployment — nginx `location /api` proxying to
`127.0.0.1:8300` — is the equivalent alternative for browser consumers and
needs no CORS at all: from the browser's perspective, the API and the page
share the same origin.

### Why direct external binding is refused

The API is authenticated by network topology. There is no HTTP-layer auth
in this release. If the port were bound to a routable address, any client
on the network could halt projects, delete tasks, deposit intake files, or
unwind an in-flight RC — the endpoints are the operator command surface, in
full.

Loopback binding is the simplest control that reduces the exposed surface
to "shells on the box." SSH already handles who gets a shell; layering the
API on SSH inherits that decision instead of reproducing it. When the auth
work in [Section 8](#8-future-work) lands, direct binding can become a
configuration choice. Until then, it stays refused.

### Loopback trust boundary — a rule the structured read layer inherits

The structured read layer added in v1.6.0 (`/board`, `/projects/{name}`,
`?format=json` on `/metrics` and `/costs`, `/logs/{kind}`, `/traces`,
`/traces/{id}`) exposes fields that are safe on loopback but not safe
on a public network — a project's `dev_tree_path` and `git_repo` URL,
for example. **The structured read layer assumes the loopback trust
boundary: `dev_tree_path` and `git_repo` are returned verbatim, never
redacted; public exposure requires redaction that does not exist.**
The bind guard, the CORS regex, and the SSH-tunnel access model are the
only exposure controls in this release. Any deployment that routes the
API through a non-loopback interface — even inadvertently — leaks those
fields to whoever reaches the port. Do not do it.

---

## 4. Verb discipline

**GET endpoints are side-effect-free. Mutations are POST.**

This is a load-bearing rule, not a stylistic preference. Every GET listed
below can be issued repeatedly without changing any state on disk. Every
state change is a POST.

### Why the rule is load-bearing

Browsers, HTTP libraries, corporate proxies, and observability tools all
treat GETs as safe:

- Browsers prefetch GET URLs on hover, on tab restore, and on speculative
  navigation.
- Aggressive caches replay GET responses without re-hitting the origin.
- Retry logic in HTTP libraries retries GETs on network errors by default;
  POSTs are usually not retried.
- Link-safety scanners follow GET URLs found in emails, chat messages, and
  Slack unfurls.

If a GET endpoint mutated state, any one of those would silently trigger
the mutation. A state-changing GET is a silent-side-effect bug regardless
of whether the endpoint is "convenient" as a GET. The verb-discipline
checksum test in the fidelity harness enforces this: issuing every GET
against a sandbox project must leave the sandbox's on-disk state
byte-identical. A GET that mutates fails the RC.

### The rule in operation

- All read endpoints in [Section 5](#5-endpoint-table) are GET and mutate
  nothing. The ANSI-pane reads shell out to read-only scripts; the
  structured-read endpoints added in v1.6.0 (`/board`, `/projects/{name}`,
  `?format=json` on `/metrics` and `/costs`, `/logs/{kind}`, `/traces`,
  `/traces/{id}`) read the underlying data files directly in Python and
  do not shell out at all.
- All mutation endpoints are POST under `/operations/*`, with parameters
  in the JSON body (not the query string). This keeps mutations out of
  browser history, referer headers, and access logs that record URLs.
- `POST /operations/halt-global` and `POST /operations/unhalt-global` take
  no body fields, but they are still POSTs — halting the entire kanban is
  a state change, not a read.

---

## 5. Endpoint table

Every endpoint below is a thin adapter over the named script (except
`/health` and `/projects`, which read data directly). Parameter names
match the script's flag names verbatim. Where a script takes a positional
argument (intake, dashboard column-render panes), the table notes it.

Script paths are relative to `<kanban_root>/scripts/`.

### Reads (GET)

| Endpoint | Underlying script | Parameters (query string) |
|---|---|---|
| `GET /health` | (none — reads `VERSION` directly) | none |
| `GET /status` | `kanban-status.sh` | `project` (required), `no_color` |
| `GET /show` | `show.sh` | `project` (required), `key` (required), `file` |
| `GET /test-report` | `show-test-report.sh` | `project` (required), `key` (required) |
| `GET /metrics` | `dashboard/show-metrics.sh` | `project`, `last`, `per_agent` (omit `project` to iterate all) |
| `GET /costs` | `cost-report.sh` | `project`, `month`, `day`, `rc` (at most one of month/day/rc) |
| `GET /rejected` | `list-rejected.sh` | `project` |
| `GET /projects` | (none — reads `projects.cfg` directly) | none |
| `GET /dashboard/input` | `dashboard/column-render.sh` | (positional: `input`, plus `--all-projects` against kanban root) |
| `GET /dashboard/queue` | `dashboard/column-render.sh` | (positional: `queue`, plus `--all-projects` against kanban root) |
| `GET /dashboard/metrics` | `dashboard/show-metrics.sh` | `project` |
| `GET /dashboard/attention` | `dashboard/show-attention.sh` | `project` |
| `GET /dashboard/header` | `dashboard/show-header.sh` | `project` |
| `GET /dashboard/status-window` | `dashboard/show-status-window.sh` | `project` |

`GET /dashboard/{pane}` returns `422 Unprocessable Entity` when `{pane}`
is not one of the six values above.

### Structured reads (GET, v1.6.0)

The endpoints below are the **structured read layer**: JSON-native reads
designed for the browser UI. They coexist with the six ANSI-pane reads
above — the ANSI panes are untouched and remain byte-identical to their
pre-v1.6.0 output. Every structured endpoint is read-only, GET only, and
subject to the loopback trust boundary described in [Section 3](#3-access-model).

Where an endpoint returns the standard envelope, HTTP status derives from
`exit_code` as with the shell-out reads. Where an endpoint returns a
domain object or an array directly, the HTTP status is `200` on success
and the error codes noted per row otherwise.

| Endpoint | Required params | Optional params | Response shape | Error codes |
|---|---|---|---|---|
| `GET /board` | none | — | `{generated_at, projects:[{name,color,halt}], columns:[{name, items:[…], truncated}]}`. Columns in fixed order: `BUGS, PRIORITIES, REQUIREMENTS, PM, CODER, WRITER, TESTER, CM`. Each item: `{id, project, kind, key, title, status, version_label, active_rc, color}` where `id` is the composite `<project>/<kind>/<key>`, `status` ∈ `open \| working \| done \| wont-do \| blocked \| label`, and `color` is the registry hex string verbatim. | `422` when `?project=` is supplied (the board is the unfiltered aggregation view; the UI filters client-side). |
| `GET /projects/{name}` | `name` (path) | — | `{name, workflow_type, branch_prefix, dev_tree_path, git_repo, priority, color, ceilings:{max_major,max_minor,max_patch}, last_released, active_rc, halt, queue_counts:{PM:{open,working,done}, CODER:{…}, WRITER:{…}, TESTER:{…}, CM:{…}}}`. `active_rc` and `last_released` are `null` when the release-state sentinel is `none`. `dev_tree_path` and `git_repo` are returned verbatim. | `404` `{"error":"project not found","name":"<name>"}` when the name is not in `projects.cfg`. |
| `GET /metrics?format=json` | `project`, `format=json` | `last` | JSON array of per-RC row objects — `[{version, tasks, wall_seconds, tokens_in, tokens_out, cache_read_pct, est_cost}, …]`. Fields are omitted when the underlying source has no value. Rows are in CSV order (oldest to newest). | `422` when `format=json` is set without `project`. Omit `format` for the pre-v1.6.0 text envelope (byte-identical). `per_agent` is not applicable to the JSON branch. |
| `GET /costs?format=json` | `project`, `format=json` | `month`, `day`, `rc` (at most one) | JSON array of per-RC row objects — `[{version, tasks, tokens_in, tokens_out, cache_read_pct, est_cost}, …]`. Fields are omitted when the underlying source has no value. `rc` returns a single-element array; `month` and `day` filter by `shipped_at` prefix; omitting all three returns every RC file under `usage/rc/`. | `422` when `format=json` is set without `project`. Omit `format` for the pre-v1.6.0 text envelope (byte-identical). |
| `GET /logs/{kind}` | see per-kind table below | `tail` (int, default 200, hard cap 2000) | Standard envelope. `stdout` is the tail text; `exit_code == 0` when the file was read, `exit_code == 1` when the resolved path does not exist on disk (still HTTP 200). | `404` `{"error":"unknown log kind","kind":"<kind>"}` when `{kind}` is not one of the six known values. `422` when a required param is missing, when `agent`/`project` contains a traversal sequence (`..`, `/`, `\`, `%2F`, `%5C`, `%2E`, null bytes), when `agent` is not in the fixed role set, when `project` is not registered (and not `global` for the overwatch kind), or when `tail` is not a positive integer. |
| `GET /traces` | none | `project`, `agent`, `limit` (int, default 50, hard cap 2000) | JSON array of trace index entries, newest-first — `[{id, project, agent, task_key, timestamp, path_basename}, …]`. `id` is an opaque server-minted token (the trace file stem). `timestamp` is ISO-8601 UTC derived from the filename prefix. Unknown `project` or `agent` values return an empty array (not an error). | `422` when `project` or `agent` contains a traversal sequence, when `agent` is not in the fixed role set, or when `limit` is not a positive integer. |
| `GET /traces/{id}` | `id` (path) | — | Standard envelope. `stdout` is the trace file's markdown body; `exit_code == 0`. | `404` `{"detail":{"error":"trace not found","id":"<id>"}}` when `{id}` is not present in the server's own enumeration. Traversal-shaped or fabricated ids also resolve to 404 — a path component inside `{id}` is never interpreted as filesystem navigation. |
| `GET /approvals` | none | `project` | JSON array of pending HUMAN-APPROVE gate records, one per task, sorted by project name then task ID — `[{task_id, project, state, rc, target_version, age, review, review_cmds, approve_cmd, reject_cmd}, ...]`. Empty array `[]` when no gates are pending. `state` is `WAITING` or `BACKLOG`; `target_version` is an alias for `rc` (both are included for consumers that prefer one or the other). `approve_cmd` and `reject_cmd` are the verbatim shell commands the operator would copy-paste. `review` is the task's `## Goal` lines joined with ` \| `. `review_cmds` is an ordered array of verbatim inspect-before-deciding commands (see field notes below). Absent `?project=` aggregates across ALL registered projects (no default project). Added in ICD 1.1.0: the `review_cmds` field. | `422` `{"detail":"Unknown project '<name>'. No such project directory found."}` when `?project=` names a project that is not registered. An empty array is **never** substituted for the unknown-project error. |

**`GET /logs/{kind}` — kinds and their required params.** The `{kind}`
path segment selects a fixed row of the server-side confinement table;
each row names the params the endpoint requires and the concrete log
file the row resolves to. Unknown kinds return `404`; missing required
params return `422`.

| Kind | Required params | Resolved log path |
|---|---|---|
| `wake` | `agent` | `<kanban_root>/logs/cron-<agent>.log` |
| `cm` | none | `<kanban_root>/logs/cron-cm.log` |
| `agent` | `project`, `agent` | `<kanban_root>/projects/<project>/logs/agents/<agent>.log` |
| `debug` | `project`, `agent` | `<kanban_root>/projects/<project>/logs/debug/<agent>.log` |
| `overwatch` | `project` (or the literal `global`) | `<kanban_root>/projects/<project>/logs/overwatch/sweep.log`, or `<kanban_root>/logs/overwatch.log` when `project=global` |
| `api-server` | none | `<temp_root>/api/api-server.log` |

The fixed agent-role set for the `agent` parameter is:
`pm, po, coder, writer, tester, cm, overwatch`. Values outside that set
return `422`. The `project` parameter is validated against the
`projects.cfg` registry — an unknown name is `422`, not a filesystem
lookup.

#### Path confinement — how the structured layer avoids arbitrary reads

`/logs/{kind}` and `/traces/{id}` do not accept a filesystem path from
the client, ever. Two disciplines carry that promise:

- **The server-side confinement table (used by `/logs/{kind}`).** Every
  supported `(kind, params)` tuple maps to a KNOWN path in the table
  above. The handler fully constructs the resolved path from trusted
  components — the framework's `kanban_root`, a literal kind-specific
  sub-path, and whitelist-checked param values. Traversal characters in
  `agent` or `project` (`..`, `/`, `\`, and the URL-encoded variants
  `%2F`, `%5C`, `%2E`, plus null bytes) fire the confinement gate and
  return `422` before any `open()` is attempted. Unknown agent roles or
  unregistered projects fail the whitelist and return `422` for the same
  reason. No client-supplied path fragment ever touches the filesystem.
- **Opaque server-minted ids (used by `/traces/{id}`).** The trace `id`
  returned by `GET /traces` is the file stem — a token the server mints
  during its own enumeration of `logs/training/<agent>/*.md`. `GET
  /traces/{id}` resolves that id ONLY by re-enumerating and matching
  against known stems; the path passed to `read_text()` is reconstructed
  from the enumeration entry, never from the id string. A fabricated id,
  a traversal-shaped id, or any id absent from the enumeration returns
  `404`. The client can name a trace, but the client can never name a
  path.

Both disciplines share the same invariant: **the only filesystem reads
in these handlers use paths the server itself assembled from trusted
values**. That is the property the traversal-negative tests in the
fidelity suite verify — an `agent=../../etc/passwd` or `project=..%2F..`
request returns `422` before any `open()` call is made.

#### `GET /approvals` — pending HUMAN-APPROVE gates

`GET /approvals` is the structured read behind the tmux dashboard's
window 14 (pending-approvals list) and the surface an operator UI or a
`curl` script consumes to inspect pending human-approval gates without
parsing rendered pane text. The window-14 renderer and this endpoint
share one implementation (`collect_pending_approvals` in
`dashboard/scan_human_approvals.py`) — the JSON body and the pane are
guaranteed to agree on which tasks are pending.

**URL and parameters.** The path is `GET /approvals`. The only query
parameter is `project`, and it is optional. Absent `?project=`, the
endpoint aggregates pending gates across ALL registered projects — the
same "no default project" rule the rest of the read layer follows.
When `?project=<name>` is supplied and `<name>` names a registered
project, results are filtered to that project.

**Response shape.** The body is a JSON array (not the standard
envelope), one object per pending HUMAN-APPROVE gate task, sorted by
project name then task ID:

```json
[
  {
    "task_id": "HUMAN-APPROVE-v1.10.0-099",
    "project": "pgai-agent-kanban",
    "state": "WAITING",
    "rc": "v1.10.0",
    "target_version": "v1.10.0",
    "age": "2h 5m",
    "review": "Approve v1.10.0 RC | Report at reports/tester-v1.10.0.md",
    "review_cmds": [
      "scripts/show.sh --project pgai-agent-kanban --key HUMAN-APPROVE-v1.10.0-099",
      "scripts/show-test-report.sh --project pgai-agent-kanban --key v1.10.0"
    ],
    "approve_cmd": "scripts/close.sh --project pgai-agent-kanban --key HUMAN-APPROVE-v1.10.0-099",
    "reject_cmd": "scripts/wontdo.sh --project pgai-agent-kanban --key HUMAN-APPROVE-v1.10.0-099"
  }
]
```

Field notes:

- `state` is either `WAITING` or `BACKLOG` — both mean "pending human
  action." No other values are emitted.
- `target_version` duplicates `rc`. Both fields are included so
  consumers that prefer one name over the other need not translate.
- `age` is a human-readable elapsed string derived from `status.md`
  mtime (`45s`, `12m`, `3h 4m`, `2d 1h`). It is a display value, not a
  parseable duration.
- `review` is the task README's `## Goal` lines joined with ` | ` (a
  space-pipe-space separator). It is intended for display; the source
  of truth for gate detail is the task folder.
- `review_cmds` is an **ordered** array of verbatim shell commands the
  operator runs to inspect the gate before deciding — the read step
  that completes the read-decide-paste loop. The array is populated in
  a fixed order:
    1. `scripts/show.sh --project <p> --key <task-id>` — always
       present, shows the gate task itself (goal, prerequisites, notes).
    2. `scripts/show-test-report.sh --project <p> --key <rc-version>` —
       shows TESTER's verdict for the RC being approved. Present only
       when the target RC version is known (identified by starting
       with `v`); **omitted rather than invented** when unknown.
  Because entry 2 is conditionally omitted, consumers must not index
  by position — iterate the array, or key by prefix. The scanner is
  the single source of truth (`collect_pending_approvals` in
  `dashboard/scan_human_approvals.py`); this endpoint and the tmux
  window-14 renderer emit the same strings from the same call. Added
  in ICD 1.1.0.
- `approve_cmd` and `reject_cmd` are the verbatim shell commands the
  operator would copy-paste — the same strings the window-14 renderer
  prints. A UI can echo them without inventing new confirmation UX.

**Empty state.** A clean system with no pending gates returns `[]` with
HTTP 200. An empty array is the success shape, not an error.

**Unknown project.** `?project=<name>` where `<name>` is not a
registered project returns `422` with the read layer's standard error
shape:

```json
{"detail": "Unknown project 'does-not-exist'. No such project directory found."}
```

An empty array is **never** substituted for the unknown-project error —
"the project does not exist" and "the project exists but has no pending
gates" must not collapse to the same response.

**Standard read-layer conventions.** The endpoint follows the read
layer's conventions established in v1.6.0 and reinforced by the
structured reads above:

- **Loopback bind.** Access is by the same loopback trust boundary
  described in [Section 3](#3-access-model). No auth layer, no TLS, no
  non-loopback bind.
- **Opaque server-minted ids.** `task_id` is the task folder name
  minted by the pipeline — never a filesystem path a caller can
  fabricate to escape the projects tree.
- **422-before-subprocess on malformed input.** `?project=<unknown>`
  returns `422` at the API layer before any filesystem scan is
  performed; the check is a whitelist against the registered-project
  set, not a path lookup.
- **Read-only.** The handler reads task state directly from the kanban
  filesystem and does not shell out. It mutates nothing.

**curl example.**

```bash
# All pending gates across every project
curl -sS http://127.0.0.1:8300/approvals | jq

# Narrow to one project
curl -sS "http://127.0.0.1:8300/approvals?project=pgai-agent-kanban" | jq

# Clean system — returns []
curl -sS http://127.0.0.1:8300/approvals
# []
```

### Mutations (POST)

Body is JSON. Field names match the script flag names (`-` in flag names
maps to `_` in body fields — `dry-run` becomes `dry_run`, `keep-artifacts`
becomes `keep_artifacts`).

| Endpoint | Underlying script | Required body fields | Optional body fields |
|---|---|---|---|
| `POST /operations/halt` | `halt.sh` | `project` | — |
| `POST /operations/unhalt` | `unhalt.sh` | `project` | — |
| `POST /operations/halt-after` | `halt-after.sh` | `project`, `key` | — |
| `POST /operations/halt-global` | `halt-global.sh` | (none) | — |
| `POST /operations/unhalt-global` | `unhalt-global.sh` | (none) | — |
| `POST /operations/reset` | `reset.sh` | `project`, and exactly one of `key` / `agent` / `bug` / `priority` / `requirement` | `keep_artifacts` |
| `POST /operations/close` | `close.sh` | `project`, `key` | `state`, `note`, `dry_run` |
| `POST /operations/wontdo` | `wontdo.sh` | `project`, `key` | — |
| `POST /operations/delete` | `delete.sh` | `project`, `key` | `force`, `dry_run` |
| `POST /operations/intake` | `intake.sh` | `project`, `filename`, `content` | — |
| `POST /operations/unwind-rc` | `unwind-rc.sh` | `project`, `key` | `dry_run`, `force` |
| `POST /operations/set-version-ceiling` | `set-version-ceiling.sh` | `project` | `show`, `minor`, `major`, `no_minor`, `no_major`, `dry_run` |
| `POST /operations/switch-provider` | `switch-provider.sh` | `provider` | — |
| `POST /operations/create-project` | `create-project.sh` | `project` | `workflow_type`, `max_patch`, `max_minor`, `max_major`, `git_remote`, `priority`, `color`, `no_migrate`, `dry_run`, `dev_tree`, `git_repo` |
| `POST /operations/add-project` | `add-project.sh` | `project` | `priority`, `color`, `no_migrate`, `dry_run` |
| `POST /operations/remove-project` | `remove-project.sh` | `project`, `force` (when `dry_run` is not `true`) | `force`, `dry_run` |
| `POST /operations/ship-rc` | `ship-rc.sh` | `project`, `key`, `confirm` | `dry_run` |

Notes on specific mutations:

- **`intake`** — The API writes `content` to a temp file named `filename`
  under `${PGAI_AGENT_KANBAN_TEMP_DIR}/intake/`, then invokes
  `intake.sh --project <name> <tempfile>` (the file path is positional,
  not a `--file` flag). The pipeline's existing name-prefix routing
  (`BUG-*`, `PRIORITY-*`, `v[0-9]*.md`) and quarantine behavior apply
  unchanged.
- **`reset`** — Body carries a mutually-exclusive kind selector matching
  `reset.sh`. Supply `project` plus exactly one of `key`, `agent`, `bug`,
  `priority`, or `requirement`. Two or more selectors returns 422 naming
  the conflicting fields; zero selectors returns 422 naming the missing
  choice. No silent precedence between selector kinds. `keep_artifacts`
  is optional.
- **`delete`** — Refuses on ambiguous keys and on non-terminal items
  (unless `force` is true). Refusals propagate as the script's non-zero
  exit code, HTTP 500 in the envelope.
- **`unwind-rc`** — Pre-flight guards are enforced by the script: HALT
  must be set (per-project or global), and the Active-RC must match `key`
  (`force` bypasses the version-mismatch check but does **not** bypass
  HALT or shipped-tag checks). Guard failures surface as non-zero
  `exit_code` with the guard message in `stderr`.
- **`remove-project`** — The API refuses live removal without explicit
  opt-in. When `dry_run` is not `true`, `force` must be `true`; a
  missing or `false` `force` returns 422 at the API layer before any
  subprocess is spawned. The API is deliberately not gentler than the
  script.
- **`ship-rc`** — Double-gated. Beyond the script's own guards, the API
  requires the body to carry a `confirm` string whose value equals
  exactly `ship-rc <project> <key>` (a single space between each token,
  no trimming, no case-folding). The confirm-gate fires at the API layer
  before any subprocess is spawned: a wrong or missing string returns
  422 naming `confirm`, and `ship-rc.sh` is never invoked. The gate
  exists because `ship-rc` is the one origin-touching verb in the API —
  a stray POST becomes inert, and a future dashboard "Ship" button can
  echo the string verbatim without inventing new confirmation UX.

  Example:

  ```bash
  curl -sS -X POST http://127.0.0.1:8300/operations/ship-rc \
      -H 'Content-Type: application/json' \
      -d '{
            "project": "my-app",
            "key": "v1.5.0",
            "confirm": "ship-rc my-app v1.5.0",
            "dry_run": true
          }'
  ```

---

## 6. Universal contracts: `dry_run` and `warnings`

Two contracts apply to every endpoint the API exposes. They are described
here once so the endpoint table above does not have to restate them
per-row.

### 6.1 `dry_run` — no-mutation preview on every operation

Every one of the 17 `POST /operations/*` routes accepts an optional
`dry_run: bool` body field. The contract has four properties:

- **All 17 operations honour it.** Every operation body model carries
  `dry_run` with default `false`. A parity lint fails the RC if any
  operation is added without it, so a caller that supplies `dry_run:
  true` on any operation is guaranteed to get dry-run semantics.
- **Uniform semantics.** The short-circuit lives in the API dispatch
  layer. The request is validated and the exact invocation is resolved,
  and then the envelope is returned WITHOUT spawning the underlying
  script. `exit_code` is `0`, `stdout` is a one-line description of the
  planned action in the form `dry-run: would execute <script>.sh
  <args>`, `stderr` is empty, and `warnings` carries whatever was
  accumulated for the request.
- **No mutation, ever.** A `dry_run: true` request is guaranteed not to
  touch the filesystem, git tree, or any on-disk state. The acceptance
  test asserts this on-disk: a dry-run POST to `/operations/halt` must
  leave the HALT file absent. Verbs whose underlying script has native
  dry-run support MAY pass through instead of short-circuiting, provided
  the observable contract is identical — same envelope shape, no
  mutation.
- **Reads do not get `dry_run`.** A dry-run of a GET is the GET; the
  contract's scope is mutations only. Do not send `dry_run` as a query
  parameter on a read — it will be flagged as an unknown parameter (see
  Section 6.2).

**Example — dry-run a halt.** The load-bearing case from the field
report: `POST /operations/halt` with `dry_run: true` must return 200,
describe the planned action, and leave no HALT file on disk.

```bash
curl -sS -X POST http://127.0.0.1:8300/operations/halt \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "dry_run": true}'
```

Response:

```json
{
  "exit_code": 0,
  "stdout": "dry-run: would execute halt.sh --project my-app",
  "stderr": "",
  "warnings": []
}
```

No HALT file is created. The real invocation (without `dry_run`) is
what mutates state.

### 6.2 `warnings` — always present, unknown inputs surfaced

Every response body — read or mutation — carries a top-level
`warnings: list[str]` field. The contract has four properties:

- **Always present.** Never omitted. `warnings: []` on a clean request
  is the success shape, not an artifact of "there was nothing to
  report."
- **Populated per unknown input.** On a read (GET), each unknown query
  parameter produces one entry: `unknown parameter: <name>`. On an
  operation (POST), each unknown body field produces one entry:
  `unknown field: <name>`.
- **Warn and execute.** Unknown inputs never fail the request. The
  named operation runs as specified with the recognized fields, and the
  discarded input is surfaced as a warning instead of a silent drop.
  This is the deliberate policy — see Section 6.3 for the rationale.
- **Near-miss diagnostics.** When an unknown field is within edit
  distance 2 of a known field on the same route, the warning names the
  likely-intended field: `unknown field: <name> — did you mean
  <candidate>? (operation executed)`. The most consequential case is a
  typo of `dry_run` (for example `dryrun`, `dry-run`, or `forse` for
  `force`) — the message makes the discard visible on the one typo
  that most often defeats an operator's expressed safety intent.

**Example — clean read.** A read with no unknown parameters carries
`warnings: []`.

```bash
curl -sS http://127.0.0.1:8300/health
```

Response:

```json
{
  "service": "pgai-agent-kanban-api",
  "kanban_root": "/home/rocky/pgai_agent_kanban",
  "version": "v1.20.7",
  "warnings": []
}
```

**Example — read with an unknown query parameter.** The unknown
parameter is captured in `warnings`; the read still returns 200 with
its normal payload.

```bash
curl -sS "http://127.0.0.1:8300/health?bob=unknown_var"
```

Response:

```json
{
  "service": "pgai-agent-kanban-api",
  "kanban_root": "/home/rocky/pgai_agent_kanban",
  "version": "v1.20.7",
  "warnings": ["unknown parameter: bob"]
}
```

**Example — operation with an unknown body field.** The operation
executes; the unknown field surfaces in `warnings` with near-miss
wording when the field name resembles a known one.

```bash
curl -sS -X POST http://127.0.0.1:8300/operations/halt \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "forse": true}'
```

Response:

```json
{
  "exit_code": 0,
  "stdout": "…halt.sh stdout…",
  "stderr": "",
  "warnings": ["unknown field: forse — did you mean dry_run? (operation executed)"]
}
```

The HALT file IS created — the operation ran. The warning tells the
caller that `forse` did nothing, and hints at the field they probably
meant. Unrelated unknown fields (no near-miss within edit distance 2)
produce the shorter form `unknown field: <name>`.

### 6.3 Warn-and-execute, not 422-reject — the rationale

The obvious alternative to warn-and-execute is to reject an operation
with `422 Unprocessable Entity` when its body carries an unknown field.
The API deliberately does not do that, and the reason turns on which
failure mode actually matters.

The safety-critical case is the one that motivated the contract in the
first place: an operator sends `{"project": "p", "dry_run": true}` to
`/operations/halt`, the API silently discards `dry_run`, and the real
halt runs. That case is now eliminated **structurally**. The dry_run
parity lint asserts that every operation body model carries a `dry_run`
field, and the freshness lint asserts that the checked-in ICD reflects
the code. A new operation cannot ship without `dry_run`, and an
existing operation cannot lose it. `dry_run: true` is guaranteed to
mean dry-run on every route, forever. No runtime check is required
because the property holds by construction.

With the safety-critical case gone, the remaining unknown fields are
genuinely harmless. A field named `forse` on `/operations/halt` was
never going to change the operation's behaviour — `halt.sh` does not
know about `forse`, has no flag by that name, and would have discarded
it under any policy. The only question is whether the discard is
visible. `warnings` makes it visible. A hard 422 would trade a
diagnostic entry for a refused request, which is a worse trade for the
90% case (typo of a real field) and no better for the 10% case (an old
client with a field the server no longer knows). Warnings diagnose
bugs and inform future users that a variable did nothing; that is the
role they play. The structural guarantee is what guards against the
one failure mode that would have mattered.

---

## 7. curl examples

The examples below use `http://127.0.0.1:8300` and target a project named
`my-app`. Adjust the host, port, project name, and keys to your install.

### Read example: project status

```bash
curl -sS "http://127.0.0.1:8300/status?project=my-app"
```

Envelope on success:

```json
{
  "exit_code": 0,
  "stdout": "…kanban status output…",
  "stderr": ""
}
```

Other read examples:

```bash
# Show a task README/status
curl -sS "http://127.0.0.1:8300/show?project=my-app&key=CODER-20260704-026-read-endpoints"

# Show a TESTER verification report by RC version
curl -sS "http://127.0.0.1:8300/test-report?project=my-app&key=v1.2.0"

# Cost report for a specific RC
curl -sS "http://127.0.0.1:8300/costs?project=my-app&rc=v1.2.0"

# Historical metrics, last 5 rows, with per-agent breakdown
curl -sS "http://127.0.0.1:8300/metrics?project=my-app&last=5&per_agent=true"

# Quarantined intake inventory
curl -sS "http://127.0.0.1:8300/rejected?project=my-app"

# Registered projects (no shell-out)
curl -sS "http://127.0.0.1:8300/projects"

# A dashboard pane
curl -sS "http://127.0.0.1:8300/dashboard/attention?project=my-app"
```

### Halt-family example: halt a project, then unhalt

```bash
# Halt (per-project)
curl -sS -X POST http://127.0.0.1:8300/operations/halt \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app"}'

# Arm HALT-AFTER (soft drain until the current RC ships)
curl -sS -X POST http://127.0.0.1:8300/operations/halt-after \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "rc"}'

# Global halt — halts every project at next wake iteration
curl -sS -X POST http://127.0.0.1:8300/operations/halt-global \
    -H 'Content-Type: application/json' -d '{}'

# Unhalt (per-project)
curl -sS -X POST http://127.0.0.1:8300/operations/unhalt \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app"}'

# Remove the global HALT
curl -sS -X POST http://127.0.0.1:8300/operations/unhalt-global \
    -H 'Content-Type: application/json' -d '{}'
```

### Intake example: deposit a bug report

`filename` must follow the intake routing convention (`BUG-*`, `PRIORITY-*`,
or `v[0-9]*.md`). The API writes the content to a temp file, then invokes
`intake.sh` on it.

```bash
curl -sS -X POST http://127.0.0.1:8300/operations/intake \
    -H 'Content-Type: application/json' \
    -d '{
          "project": "my-app",
          "filename": "BUG-0501-20260704-widget-crash.md",
          "content": "# BUG-0501-20260704-widget-crash\n\nWidget crashes on load.\n"
        }'
```

The pipeline's own validation and quarantine rules apply — a malformed
filename or mismatched internal heading is quarantined to `.rejected/`
with a `.reason` sidecar, exactly as with a direct `intake.sh` call.

### Unwind-RC example: dry-run first, then real

```bash
# Prerequisite: the project must be HALTed (per-project or global).

# Dry-run: prints the plan, changes nothing.
curl -sS -X POST http://127.0.0.1:8300/operations/unwind-rc \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "v1.2.0", "dry_run": true}'

# Real unwind after confirming the plan.
curl -sS -X POST http://127.0.0.1:8300/operations/unwind-rc \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "v1.2.0"}'
```

If HALT is not set, the script refuses with a guard message in `stderr`
and a non-zero `exit_code`; the API returns HTTP 500 with the envelope
unchanged. Set the HALT first (`POST /operations/halt` or
`POST /operations/halt-global`) and retry.

### Other mutation examples

```bash
# Reset a task, keeping any artifacts already produced
curl -sS -X POST http://127.0.0.1:8300/operations/reset \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "CODER-20260704-026-read-endpoints", "keep_artifacts": true}'

# Close a task with a note
curl -sS -X POST http://127.0.0.1:8300/operations/close \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "CODER-20260704-026-read-endpoints", "note": "Superseded by 027"}'

# Mark a task WONT-DO
curl -sS -X POST http://127.0.0.1:8300/operations/wontdo \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "CODER-20260704-026-read-endpoints"}'

# Dry-run a delete (preview, no mutation)
curl -sS -X POST http://127.0.0.1:8300/operations/delete \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "BUG-0400", "dry_run": true}'
```

---

## 8. Future work

The following are deliberately out of scope for this release and are
documented here so operators know not to look for them.

### Authentication

There is no HTTP-layer auth. Anyone who can reach the loopback socket can
call every endpoint. This is why loopback binding is not optional (see
[Section 3](#3-access-model)). A future release will add an auth layer
(likely a shared-secret token in a header, or an OAuth-style flow for the
UI project); until then, SSH is the authentication.

### TLS

There is no TLS. Traffic on the loopback socket does not leave the box,
so plaintext is the pragmatic choice for the localhost segment; the SSH
tunnel encrypts the wire between operator and server. A future release
that supports non-loopback binding will require TLS termination — either
directly in the service or via a reverse proxy — before the bind guard
relaxes.

### Multi-user

There is no notion of "which operator called this endpoint." The API
inherits whichever user account started `api-server.sh`. Any auth
addition will bring identity with it.

### Chain triggering

There is no endpoint that triggers a wake, promotes a task, or advances
the RC pipeline. Cron remains the sole scheduler. The API deliberately
mirrors what an operator can do at a shell — manage inputs, read state,
mutate task state — but does not start work.

### Script rewrites into a shared ops library

The API wraps the scripts; it does not replace them. A future
shared-ops-library arc may extract the operations into a Python module
that both the CLI and the API call directly. That is post-feedback work
and not part of this release.

### export-project and import-project — the one intentional non-parity

`export-project.sh` and `import-project.sh` are not exposed over HTTP,
and this is the one intentional non-parity in the API. Both scripts are
file-path-centric: they write a tar archive to a caller-chosen path on
disk, or read one back from a caller-chosen path. Those semantics do not
translate to a body-content API cleanly — the API has no concept of a
filesystem path the caller can reach, and streaming multi-gigabyte
archives through a JSON envelope is not the right shape. Every other
operator verb has an HTTP counterpart; these two are deferred until a
remote-operator story exists that motivates the transport question. The
parity lint knows to skip them.

---

## 9. ICD — the published API contract

The API is a published, versioned contract. This section is the
consumer's manual for that contract: where it lives, what its version
number means, how to pin against it, how to regenerate it, what
guarantees you get while a version is supported, and how retirement
works.

### The artifact

The contract is a single deterministic JSON file at:

```
docs/api/icd.json
```

It is the OpenAPI 3 document produced by the FastAPI application — the
same shape the running service serves at `/openapi.json`. It is
checked in to the repo, regenerated deterministically from the code, and
gated so it cannot drift: the freshness lint asserts the checked-in
artifact matches what the current code would produce, and the
compatibility lint asserts the current API is a compatible superset of
every supported past version. A stale or incompatible artifact fails
the RC.

The version of the contract itself lives at:

```
docs/api/ICD_VERSION
```

A one-line file containing the current ICD version (for v1.19.0:
`1.1.0`). This is the single source of truth: the generator stamps
`info.version` in `icd.json` from it, and the running service serves
the same value on `/openapi.json`. The kanban release version and the
ICD version are independent — the ICD version only moves when the API
surface moves.

The v1.19.0 release is the first minor bump on the additive-minor path:
`GET /approvals` gained the `review_cmds` array (a new optional
response field on an existing endpoint), so `ICD_VERSION` moved from
`1.0.0` to `1.1.0`. Both baselines remain supported — the
compatibility gate protects every consumer still pinned to `1.0.0`,
and the additive-only promise below is what makes carrying both
possible.

### Version semantics

`info.version` in the ICD is the **API contract version**. It starts
at `1.0.0` and follows semantic versioning against the API surface, not
against the kanban release:

- **Minor bump (for example `1.0.0` → `1.1.0`)** — additive only. New
  paths, new optional request fields, new response fields, new optional
  query params. Everything that worked against the previous version
  still works.
- **Patch bump (for example `1.0.0` → `1.0.1`)** — documentation-only
  changes to the ICD (descriptions, examples). No surface change.
- **Major bump (for example `1.0.0` → `2.0.0`)** — breaking change:
  removal or rename of a path, removal of a response field, a new
  REQUIRED request field, a type change, an enum shrink. Majors are a
  deliberate, operator-approved event, never a side effect — the
  compatibility gate makes an accidental break impossible to ship.

The version bump is part of the API-changing RC itself. An RC that
adds an endpoint also bumps `ICD_VERSION` (minor, in that case) and
re-freezes the baseline. The freshness and compatibility gates
enforce it: a surface change without a correct version bump fails the
gated test suites.

The kanban release version (`v1.16.0`, `v1.17.0`, and so on) can
advance many times without the ICD version moving. Consumers pin on
`info.version`, not on the kanban release.

### Pinning against the contract

Consumers pin against the contract by vendoring the artifact and
keying on two things: the set of `paths` (and the schemas they
reference) and `info.version`.

The recommended pattern for a consumer (a UI project, an SDK, a
`curl`-based script suite):

1. **Vendor `docs/api/icd.json`** into the consumer repo at a known
   path — for example `vendor/pgai-icd.json`. Vendor a specific
   version, not a floating reference.
2. **Record the vendored version.** The vendored file carries its own
   `info.version`; the consumer's build asserts the version it depends
   on is what it vendored. A mismatched vendor is caught locally
   before it becomes a runtime surprise.
3. **Diff against a fresh copy at upgrade time.** When the operator
   ships a new kanban release, the consumer replaces its vendored
   copy with the new `docs/api/icd.json` and diffs. Additive changes
   (a new path, a new optional field) show up in the diff as a work
   list. The version bump signals scope.
4. **Do not pin against the kanban release version.** The kanban may
   ship ten releases without the ICD version moving. Pin on the ICD
   `info.version`; treat the kanban release as build metadata.

The `paths` object plus `info.version` is the whole contract surface.
Consumers depending on other OpenAPI fields (descriptions, examples,
tags) are depending on documentation, not on the contract — those
change under patch and minor bumps.

### Regenerating the artifact

The artifact is deterministic — two runs on a clean tree produce a
byte-identical file. To regenerate after an API change:

```bash
bash team/scripts/generate-icd.sh
```

The script builds the FastAPI app in-process (no server, no network),
calls `app.openapi()`, stamps `info.version` from `docs/api/ICD_VERSION`,
and writes sorted-key, fixed-indent JSON with a trailing newline to
`docs/api/icd.json`. A dirty `git status` after regeneration means the
artifact was stale before you ran it — commit the regenerated file
before running the gated suites.

Deterministic byte equality (not JSON-semantic equality) is the
property the freshness gate depends on. A flapping generator would
make the gate useless, so any change that adds nondeterminism to the
output is treated as a bug, not a preference.

### The additive-only promise

While a contract version is listed as supported, the current API is
guaranteed to be a compatible superset of it. Concretely, for every
supported baseline:

- Every path and method in the baseline still exists in the current
  API.
- No response field present in the baseline has been removed.
- No request field that was optional in the baseline has become
  required, and no new required request field has appeared on a
  baseline endpoint.
- Every enum value in the baseline is still accepted.

Additive changes — a new path, a new optional request field, a new
response field, a new optional query param, a new enum value — are
permitted at any time and land under a minor bump. They do not touch
the baseline manifest; the compatibility gate accepts them because
they preserve every supported baseline.

Breaking changes against a supported baseline are blocked by the
compatibility gate before they can ship. A code change that removes a
response field, renames a path, or adds a required request field will
fail the RC until the operator either reverts the change or retires
the baseline the change breaks (see below). The compatibility promise
is enforced by test, not by convention.

### Support table

The supported baselines are recorded in `docs/api/baselines/SUPPORTED`
(one version per line). The compatibility gate protects exactly the
versions listed there. This table mirrors that manifest and records
when each baseline was published.

| Version | Status | Since |
|---|---|---|
| 1.0.0 | supported | v1.16.0 |
| 1.1.0 | supported | v1.19.0 |

Column meanings:

- **Version** — the ICD contract version (`info.version` in
  `docs/api/baselines/icd-v<version>.json`).
- **Status** — `supported` while the version's line is present in
  `docs/api/baselines/SUPPORTED`; `retired` after the operator removes
  it (see retirement below).
- **Since** — the kanban release that first published this baseline.
  This is metadata for operators tracking when a contract version
  entered service, and does not affect the gate.

### Retiring a version

Retiring a supported contract version is a deliberate operator act,
never a side effect. To retire a version:

1. **Remove the version's line from `docs/api/baselines/SUPPORTED`.**
   The manifest is the only source of truth for which baselines the
   compatibility gate protects. Removing the line ends protection.
2. **Update the support table above.** Change the row's `Status` from
   `supported` to `retired` and leave the `Since` value as-is (it is
   the historical publish version, not a retirement version).
3. **Ship via an operator-approved RC.** Retirement lands like any
   other change — through an RC whose release notes call out the
   retirement so consumers get notice.
4. **Leave the baseline file alone.** `docs/api/baselines/icd-v<version>.json`
   stays in the repo forever as history. Only the manifest entry is
   removed; the frozen artifact remains.

After retirement, breaking changes relative to that baseline are
permitted (subject to the major-bump rule against any baseline that is
still listed as supported). The point of the manifest is that support
is exactly what it says — never inferred, never assumed.

An empty manifest is a lint error. Support status must be explicit;
"we deleted every line" does not implicitly mean "everything is
retired." If every listed baseline is retired at once, that is itself
a policy event that requires deliberate operator intent, not an
accidental empty file.
