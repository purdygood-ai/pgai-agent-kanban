# Operator REST API

> **Status: Experimental** — full automated coverage; contract published
> as a versioned ICD (see [Section 9](#9-icd--the-published-api-contract));
> operator field-testing in progress; surfaces may be refined.
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
table, the verb census that governs which routes exist, `curl` examples per
endpoint category, the published contract, and what is deliberately deferred
to future releases.

The interactive OpenAPI/Swagger UI is served at `/docs` on the running
service. This page is the operator overview; `/docs` is the request-builder.

---

## 1. Overview

### What the API is

A FastAPI service under `team/pgai_agent_kanban/api/`. It is a **thin
adapter**: every endpoint reaches the same implementation its operator
script reaches, passes query/body parameters as that script's flags, and
returns the exit code, stdout, and stderr in a JSON envelope.

Each operation has one implementation, and the verb census names it. For
most verbs that is a Python function: the census row's **Implementation**
reads `module:<function>`, the CLI script is a thin wrapper that reaches the
function, and the route calls the same function in process without starting
the script. A row whose implementation reads `shell` still runs its script.
Every such row in the census is a **launcher**, a verb whose whole job is
to start a process, which stays shell-backed by design; any other `shell`
row would be a verb not yet moved into a module (see
[Launchers](#launchers--verbs-whose-route-starts-a-process-by-design)).
Either way the API never re-implements logic, never becomes a second
scheduler, and never rewrites what the implementation prints.

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

HTTP status is derived from `exit_code` and, on a non-zero exit, from the
kind of refusal (`adapter.http_status_for`, first match wins):

- `200 OK` when `exit_code == 0`.
- `422 Unprocessable Entity` when required parameters are missing or
  invalid, and when the refusal's error key names an invalid, malformed,
  required or conflicting-options value (`defer.until_malformed`,
  `archive.invalid_count`).
- `404 Not Found` when the key names something not found or missing
  (`project_resolve.not_found`, `show_queues.project_directory_missing`),
  or the operation raised the ops layer's `NotFound`.
- `409 Conflict` when the key names an ambiguous or conflicting state
  (`project_resolve.ambiguous`), or the operation raised the ops layer's
  `Refused` or `Ambiguous` (`remove_project.active_rc`, a `delete` of a
  non-terminal item).
- `504 Gateway Timeout` when a shelled-out script outlives the server's
  `[api] shell_out_timeout_seconds` (`shell_out.timeout`).
- `500 Internal Server Error` for any other non-zero exit — a key that names
  none of the kinds above, such as a `<verb>.refused` fallback key over a
  CM verb's plain-text refusal (`unwind_rc.refused`, `cancel_rc.refused`).

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
- **Parity is a property, not a habit.** The operator verb census
  (`team/config/operator_verbs.manifest`) is the source of truth for the
  verb set, and every active verb in it has a REST twin. The parity gate,
  `team/scripts/lint_api_parity.py`, walks the census on every gated run
  and fails — a real stop, not a warning — when a verb has no census row,
  a route has no script, a script has no route, a response does not match
  its declared schema, or an action verb has no parity fixture. It still
  holds each body model's fields to the script's own flags (modulo the
  documented equivalences — `intake`'s `file` becomes `filename` +
  `content`, hyphens become underscores). Parity does not rot silently;
  the census and the gate are described under "The verb census" in
  [Section 5](#5-endpoint-table).

### Non-goals

- **No authentication, no TLS.** Access rests on loopback trust in three
  layers — the loopback bind, the `Host` allow-list, and JSON-only mutations
  — described in [Section 3](#3-access-model). Auth and TLS are documented
  in [Section 8](#8-future-work) as deferred.
- **No scheduler of its own.** Cron remains the sole scheduler. The API
  manages inputs, reads state, and fires the same one-off wake the CLI's
  `wake-now` verb fires (`POST /operations/wake-now`) — exactly what an
  operator can do at a shell — but nothing in it runs discovery, promotes
  a task, or advances a release on its own.
- **No WebSocket or streaming.** The UI polls GET endpoints.
- **No second implementation.** The API never carries its own copy of an
  operation's logic. A verb implemented as a Python function has that one
  function, called by the route and reached by the CLI script; a verb
  implemented in shell is run as its script.

---

## 2. Starting the server

### The wrapper script

`scripts/api-server.sh` manages the API process. It backgrounds a `uvicorn`
worker, writes a pidfile under the kanban root and a log under the framework
temp root, and handles stale pidfile cleanup on `status`.

```bash
scripts/api-server.sh start
scripts/api-server.sh status
scripts/api-server.sh stop
```

Subcommands:

| Subcommand | Effect |
|---|---|
| `start`  | Start the API in the background. No-op with a message when already running. |
| `stop`   | Verify that the target — the pidfile's pid, or the untracked process holding the API port — is the API server by its command line, then send `SIGTERM` to its process group, remove the pidfile, and wait for the port to be released. A target that is not the API server is refused dual-channel with exit 1 (`api-server.stop.target_not_api_server`) and nothing is signalled. No-op when not running. |
| `status` | Print `running (pid N)` or `not running`, or name an untracked process holding the API port. A pidfile whose pid is dead, or alive but reused by a process that is not the API server, reads as not running. Cleans up stale pidfiles once the port is also free. |

Flags:

| Flag | Effect |
|---|---|
| `--kanban-root <path>` | Override `PGAI_AGENT_KANBAN_ROOT_PATH` for this invocation. |
| `--help` / `-h`        | Print usage and exit 0. |

Environment:

| Variable | Purpose |
|---|---|
| `PGAI_AGENT_KANBAN_ROOT_PATH` | Kanban root; required so the server finds `kanban.cfg` and `VERSION`. |
| `PGAI_AGENT_KANBAN_TEMP_DIR`  | Framework temp root; the log lives here. Defaults to `/tmp/pgai_kanban_tmp`. |
| `PGAI_API_PORT`               | TCP port override. Precedence, highest first: `PGAI_API_PORT` when set to an integer, then `kanban.cfg [api] port`, then `8300`. A blank value reads as unset; a non-integer value is ignored with a warning on stderr. The script and the server it starts resolve the port through the same loader, and `start` hands the resolved port to the server, so `status` and `stop` probe the port the server actually bound. If the port cannot be resolved, every subcommand refuses with exit 1 (`api-server.port.unresolved`) before probing anything. |
| `PYTHONPATH`                  | Preserved. The script puts its own package root first (the parent of its `scripts/` directory — the kanban root in a live install, `<checkout>/team` in a dev tree) and keeps the caller's entries after it; `PGAI_DEV_TREE_PATH` is not read. |

The pidfile is at `<kanban root>/run/api/api-server.pid` — durable service
state, kept out of the temp root so temp-tree cleanup cannot orphan a running
server by removing it. The log is at
`${PGAI_AGENT_KANBAN_TEMP_DIR}/api/api-server.log`. Neither touches the git
tree.

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
| `port` | `8300` | TCP port the server listens on, unless `PGAI_API_PORT` overrides it (see the environment table above). |

When `kanban.cfg` is absent or the `[api]` section is missing, both keys fall
back to their defaults. This makes the service startable with no configuration
file at all — useful for local testing.

### Verifying the server is up

`GET /health` is the liveness endpoint. It also reports the installed
kanban version.

```bash
curl -sS http://127.0.0.1:8300/health
# {"service":"pgai-agent-kanban-api","kanban_root":"/home/<user>/pgai_agent_kanban","version":"v1.2.0"}
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
# User=<user>
# Group=<user>
# Environment=PGAI_AGENT_KANBAN_ROOT_PATH=/home/<user>/pgai_agent_kanban
# Environment=PGAI_AGENT_KANBAN_TEMP_DIR=/tmp/pgai_kanban_tmp
# ExecStart=/home/<user>/pgai_agent_kanban/scripts/api-server.sh start
# ExecStop=/home/<user>/pgai_agent_kanban/scripts/api-server.sh stop
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
ssh -L 8300:127.0.0.1:8300 <user>@kanban-host
```

While that session is open, `http://127.0.0.1:8300/health` on your
workstation reaches the API on the server. Close the session, the tunnel
closes with it.

For a background tunnel (no interactive shell), add `-N`:

```bash
ssh -N -L 8300:127.0.0.1:8300 <user>@kanban-host
```

#### Option B — SOCKS proxy (multiple services)

If you also want to reach the dashboard or other loopback services on the
same host, run a SOCKS proxy instead of one tunnel per port:

```bash
ssh -D 1080 -N <user>@kanban-host
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

CORS is a browser rule about *origins*, and there is one browser case it
never sees. That case is why the loopback CORS rule alone is not the
boundary, and why two further checks stand in front of every route — the
next subsection.

### Host allow-list and JSON-only mutations — why CORS alone is not the boundary

**The case CORS cannot see.** A page the operator's browser visits at
`http://evil.example:8300/` can have its hostname re-resolved to
`127.0.0.1` after the page has loaded (DNS rebinding). Every request the
page then makes to `http://evil.example:8300/operations/...` lands on the
loopback socket — the bind guard sees an ordinary loopback packet — and
carries `Host: evil.example:8300`. From the browser's point of view the
page and the API share an origin, so no preflight is sent and the CORS rule
is never consulted. Two request-level checks turn that request away. Both
run before routing, before any script is spawned, and both refuse with the
same `error`/`message`/`remedy`/`warnings` JSON body the action routes use
for their own refusals (Section 5), so a client parses one refusal shape
whichever layer refused.

**Host allow-list — every request, every route.** The `Host` header must
name a loopback host:

| Accepted `Host` values | Examples |
|---|---|
| `127.0.0.1`, or any other address in `127.0.0.0/8`, with or without a port | `127.0.0.1`, `127.0.0.1:8300` |
| `localhost` (case-insensitive), with or without a port | `localhost`, `localhost:3000` |
| `[::1]`, with or without a port | `[::1]`, `[::1]:8300` |

Any other value — a routable address, a DNS name, or a missing or empty
`Host` — is refused **HTTP 400** on every route, read or mutating, the CORS
preflight included, under the machine key `api.host_not_loopback`:

```bash
curl -sS -H 'Host: evil.example' http://127.0.0.1:8300/health
```

```json
{"error": "api.host_not_loopback",
 "message": "the request named Host 'evil.example', which is not a loopback host; this API answers only to 127.0.0.1, localhost or [::1] (with or without a port). Nothing was run.",
 "remedy": "curl -sS http://127.0.0.1:8300/health",
 "warnings": []}
```

The allow-list is fixed by design and is not a configuration key: widening
it to a non-loopback name is exactly the exposure the check exists to
close. Reaching the API from a remote machine goes through the SSH tunnel
or SOCKS proxy above, both of which present a loopback `Host`.

**JSON-only mutations.** A request whose method is anything but `GET`,
`HEAD` or `OPTIONS` must carry `Content-Type: application/json` — the
media type is compared case-insensitively and a parameter such as
`; charset=utf-8` is fine. A form encoding, `text/plain`, or an absent
`Content-Type` is refused **HTTP 415** under `api.content_type_not_json`
before the route is reached. Read routes are untouched. This is the second
half of the rebinding defence: a browser form or "simple" cross-site
request can produce the form and `text/plain` types without a preflight,
but it cannot produce `application/json` without one — and the preflight
is what the loopback CORS rule refuses for a non-loopback origin.

The `curl` examples on this page carry the JSON header for that reason; a
`POST` without it is refused whatever its body says.

### Why direct external binding is refused

The API is authenticated by network topology. There is no HTTP-layer
auth. If the port were bound to a routable address, any client
on the network could halt projects, delete tasks, deposit intake files, or
unwind an in-flight RC — the endpoints are the operator command surface, in
full.

Loopback binding is the simplest control that reduces the exposed surface
to "shells on the box." SSH already handles who gets a shell; layering the
API on SSH inherits that decision instead of reproducing it. When the auth
work in [Section 8](#8-future-work) lands, direct binding can become a
configuration choice. Until then, it stays refused.

### Loopback trust boundary — a rule the structured read layer inherits

The structured read layer (`/board`, `/projects/{name}`,
`?format=json` on `/metrics` and `/costs`, `/logs/{kind}`, `/traces`,
`/traces/{trace_id}`) exposes fields that are safe on loopback but not safe
on a public network — a project's `dev_tree_path` and `git_repo` URL,
for example. **The structured read layer assumes the loopback trust
boundary: `dev_tree_path` and `git_repo` are returned verbatim, never
redacted; public exposure requires redaction that does not exist.**
The bind guard, the `Host` allow-list, the JSON-only rule for mutating
requests, the CORS regex, and the SSH-tunnel access model are the only
exposure controls the API has. Any deployment that routes the
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
  nothing. Every route, read or mutation, calls the shared Python function
  its CLI twin reaches, in process, except four that start a process:
  `GET /dashboard/{pane}` runs the pane's dashboard script and waits for
  it; `POST /operations/wake-now` runs `wake-now.sh`, detached when `bg`
  or `all` is set; and `POST /operations/deep-verify` and
  `POST /operations/cold-build-now` start their script detached. The
  structured-read endpoints (`/board`, `/projects/{name}`,
  `?format=json` on `/metrics` and `/costs`, `/logs/{kind}`, `/traces`,
  `/traces/{trace_id}`) read the underlying data files directly in Python.
- All mutation endpoints are POST under `/operations/*`, with parameters
  in the JSON body (not the query string). This keeps mutations out of
  browser history, referer headers, and access logs that record URLs.
- `POST /operations/halt-global` and `POST /operations/unhalt-global` take
  no body fields, but they are still POSTs — halting the entire kanban is
  a state change, not a read.

---

## 5. Endpoint table

Every endpoint below calls, in process, the Python function its **CLI
twin** — the operator script named in the table — reaches, and returns the
text and exit code that script would produce for the same arguments. No
script is started, with four exceptions:

- `GET /dashboard/{pane}` runs the pane's dashboard script and waits for it.
- `POST /operations/wake-now` runs `wake-now.sh` — detached when `bg` or
  `all` is set, in the foreground otherwise.
- `POST /operations/deep-verify` and `POST /operations/cold-build-now`
  start their script detached and return as soon as it has started.

`/health` and `/projects` have no CLI twin and read their data directly.
Parameter names match the CLI twin's flag names verbatim. Where the CLI
twin takes a positional argument (intake, verify-rc-state, dashboard
column-render panes), the table notes it.

CLI twin paths are relative to `<kanban_root>/scripts/`.

### Reads (GET)

| Endpoint | CLI twin | Parameters (query string) |
|---|---|---|
| `GET /health` | (none — reads `VERSION` directly) | none |
| `GET /status` | `kanban-status.sh` | `project` (required), `no_color` |
| `GET /show` | `show.sh` | `project` (required), `key` (required), `file` |
| `GET /projects/{project}/tasks/{key}/readme` | `show-readme.sh` | none in the query string; path parameters `project` and `key` (both required; `key` may be an unambiguous prefix). See [the subsection below](#get-projectsprojecttaskskeyreadme--a-tasks-readme) |
| `GET /test-report` | `show-test-report.sh` | `project` (required), `key` (required) |
| `GET /metrics` | `dashboard/show-metrics.sh` | `project`, `last`, `per_agent` (omit `project` to iterate all) |
| `GET /costs` | `cost-report.sh` | `project`, `month`, `day`, `rc` (at most one of month/day/rc) |
| `GET /rejected` | `list-rejected.sh` | `project` |
| `GET /projects` | (none — reads `projects.cfg` directly) | none |
| `GET /dashboard/input` | `dashboard/column-render.sh` | `column_type` (required: `bugs`, `priorities` or `requirements`; passed as `--column-type`). Positional `input`, plus `--all-projects` against kanban root |
| `GET /dashboard/queue` | `dashboard/column-render.sh` | `column_type` (required: `pm`, `coder`, `writer`, `tester` or `cm`; passed as `--column-type`). Positional `queue`, plus `--all-projects` against kanban root |
| `GET /dashboard/metrics` | `dashboard/show-metrics.sh` | `project` |
| `GET /dashboard/attention` | `dashboard/show-attention.sh` | `project` |
| `GET /dashboard/header` | `dashboard/show-header.sh` | `project` |
| `GET /dashboard/status-window` | `dashboard/show-status-window.sh` | `project` |
| `GET /show-queues` | `dashboard/show-queues.sh` | `project`, `details` (omit `project` in compact mode to read the server's `PGAI_PROJECT_NAME` project; with neither, compact mode refuses with `show_queues.no_project` and prints no counts; omit it in `details` mode to walk every project) |
| `GET /verify-rc-state` | `verify-rc-state.sh` (in process, through `ops.rc_state.verify_rc_state`) | `project` (required; **the CLI's positional argument**, not a `--project` flag), `verbose` |
| `GET /release-locks` | `release-locks.sh` (listing mode only) | `release` and `force` are **forbidden** — supplying either returns `422` naming the mutating twin, `POST /operations/release-locks` |

`GET /dashboard/{pane}` returns `422 Unprocessable Entity` when `{pane}`
is not one of the six values above.

**The column panes draw one board column per request.** `input` and
`queue` are the board's two column kinds, and a request selects the column
with `column_type`: `input` renders `bugs`, `priorities` or
`requirements`; `queue` renders `pm`, `coder`, `writer`, `tester` or `cm`.
The value is matched case-insensitively, so a `GET /board` column name
such as `CODER` works too. The handler runs
`column-render.sh <pane> none --kanban-root <root> --all-projects --column-type <column>`
and returns what it prints: one row per item in that column across every
registered project, drawn in the colour of the status `GET /board` reports
for it. `project` does not narrow a column pane.

```bash
curl -sS "http://127.0.0.1:8300/dashboard/queue?column_type=coder"
```

A column-pane request that names no column — `column_type` absent or
blank — or names one the pane does not render (an unknown value, or the
other pane's column) is refused `422` before any script runs. The body is
dual-channel, `{error, message, remedy, warnings}` with no
`exit_code`/`stdout`/`stderr`: `error` is `dashboard_pane.column_required`,
`message` names the columns that pane accepts, and `remedy` is a runnable
`curl` line re-sending the request with the first of them. The other four
panes take no column; a `column_type` sent to one is ignored and named in
`warnings`.

**The three verb reads added in ICD 1.6.0.** Each is the HTTP twin of an
operator verb, and none of them starts its script.
`GET /verify-rc-state` calls `ops.rc_state.verify_rc_state`, the function
`verify-rc-state.sh` reaches through `pp_run_ops`; `GET /show-queues`
calls the script's own function, `ops.read.get_queues`; and
`GET /release-locks` calls `ops.locks.list_locks`, the function
`release-locks.sh` lists through. Each returns the text and exit code its
CLI produces. None of them re-derives a result of its own.

- **`GET /show-queues`** returns the same queue-state pane the tmux queues
  pane and `why.sh --details` embed — one renderer
  (`pgai_agent_kanban.dashboard.queue_pane`), three callers. The
  script's own `--kanban-root` override is dashboard-internal plumbing and
  is deliberately **not** exposed as a query parameter: every REST
  invocation renders against the server's configured kanban root, never a
  caller-supplied path. `200` on `exit_code` 0. A refusal carries its
  dual-channel block in `stderr`, and `stdout` is empty rather than a board
  of zero counts: `404` for a `project` naming no registered project
  (`project_resolve.not_found`) or no project directory
  (`show_queues.project_directory_missing`), and `409` in compact mode for
  no project at all, with neither `project` nor the server's
  `PGAI_PROJECT_NAME` naming one (`show_queues.no_project`).
- **`GET /verify-rc-state`** fronts the one read verb whose CLI takes its
  project positionally rather than as a flag. The handler passes `project`
  and `verbose` straight to `ops.rc_state.verify_rc_state`; no argv is
  built and no subprocess starts, and `stdout`, `stderr` and `exit_code`
  are what `verify-rc-state.sh` prints and returns for the same project.
  `200` on `exit_code` 0; `500` for the verb's "1 — ERROR findings"
  outcome; for "2 — pre-flight failure", the status the refusal's key names
  (`404` for `verify_rc_state.project_directory_missing`, `500` for an
  unloadable project config or an unset dev tree path); `422` when
  `project` is missing or empty. The route's description in the published
  ICD gives the same status for each exit code and refusal key, and also
  names `404` for a name that matches no registered project
  (`project_resolve.not_found`), `409` for an ambiguous prefix
  (`project_resolve.ambiguous`), and `422` for a `project` that is not one
  plain name. It describes the same in-process call to
  `ops.rc_state.verify_rc_state`, and the status follows the mapping in
  [The envelope](#the-envelope).
- **`GET /release-locks`** fronts the verb's **read-only listing mode
  only.** The mutating `--release`/`--force` half is
  `POST /operations/release-locks` (see "Mutations" below) — a GET route
  must never mutate state — so this endpoint only ever lists, printing what
  the script prints with no flags. `release` and `force` are declared as
  parameters purely so
  that a caller who supplies one gets a loud `422` naming the POST twin
  rather than a silently ignored flag that might read as "it tried to
  release something."

#### `GET /projects/{project}/tasks/{key}/readme` — a task's README

Returns one task's `README.md` — the spec the task was written to —
byte-identical to what `show-readme.sh --project <project> --key <key>`
prints. It runs in process through `ops.read.render_task_readme`, the
function `show-readme.sh` reaches; no script is started.

Path parameters:

- `project` (required) — the project name.
- `key` (required) — the task ID, or a prefix of it that resolves to one
  task unambiguously (the agent, date and sequence without the slug is
  enough), as on `show.sh` and `show-readme.sh`.

Responses:

- `200` — the standard envelope, with the README in `stdout`:
  `{"exit_code": 0, "stdout": "<README.md content>", "stderr": "", "warnings": []}`.
- `404` — there is no README at this project and key: the key matches no
  task, it names a bug, priority or requirements file rather than a task
  (READMEs are task artifacts only), or the task folder has no
  `README.md`. The body is
  `{"error": "task readme not found", "project": "<project>", "key": "<key>", "detail": "<the message show-readme.sh prints>"}`.
- `422` — `project` or `key` contains a path-traversal sequence (`..`,
  `/`, `\`, or their URL-encoded forms), refused with
  `{"error": "invalid path parameter", "detail": "…", "project": "<project>", "key": "<key>"}`;
  or a value that is not one plain name in some other way (a control
  character, a glob character, a leading `-`), refused dual-channel with
  `task_readme.project_invalid` or `task_readme.key_invalid`. Both are
  returned before any path is built or file opened.

The example is in [Section 7](#7-curl-examples).

### Structured reads (GET)

The endpoints below are the **structured read layer**: JSON-native reads
designed for the browser UI. They coexist with the six ANSI-pane reads
above and leave them unchanged: an ANSI pane's output is the same whether
or not any structured read is in use. Every structured endpoint is
read-only, GET only, and subject to the loopback trust boundary described in [Section 3](#3-access-model).

Where an endpoint returns the standard envelope, HTTP status derives from
`exit_code` as with the text-envelope reads. Where an endpoint returns a
domain object or an array directly, the HTTP status is `200` on success
and the error codes noted per row otherwise.

| Endpoint | Required params | Optional params | Response shape | Error codes |
|---|---|---|---|---|
| `GET /board` | none | — | `{generated_at, projects:[{name,color,halt}], columns:[{name, items:[…], truncated}]}`. Columns in fixed order: `BUGS, PRIORITIES, REQUIREMENTS, PM, CODER, WRITER, TESTER, CM`. Each item: `{id, project, kind, key, title, status, version_label, active_rc, color}` where `id` is the composite `<project>/<kind>/<key>`, `status` ∈ `open \| working \| done \| wont-do \| blocked \| label`, and `color` is the registry hex string verbatim. A `WONT-DO` task and a `wont-do` intake file are `wont-do`, never `done`. An intake file that still carries the retired status `superseded` is also returned as `wont-do`, because the closed states are `done` and `wont-do` only. | `422` when `?project=` is supplied (the board is the unfiltered aggregation view; the UI filters client-side). |
| `GET /projects/{name}` | `name` (path) | — | `{name, workflow_type, branch_prefix, dev_tree_path, git_repo, priority, color, ceilings:{max_major,max_minor,max_patch}, last_released, active_rc, halt, queue_counts:{PM:{open,working,done}, CODER:{…}, WRITER:{…}, TESTER:{…}, CM:{…}}}`. `active_rc` and `last_released` are `null` when the release-state sentinel is `none`. `dev_tree_path` and `git_repo` are returned verbatim. | `404` `{"error":"project not found","name":"<name>"}` when the name is not in `projects.cfg`. |
| `GET /metrics?format=json` | `project`, `format=json` | `last` | JSON array of per-RC row objects — `[{version, tasks, wall_seconds, blocked_seconds, tokens_in, tokens_out, cache_read_pct, est_cost}, …]`. Fields are omitted when the underlying source has no value. `blocked_seconds` is `history.csv`'s `blocked_minutes` column (the same figure the text renderer's "Blocked" column shows) converted to seconds — resolved through `pgai_agent_kanban.metrics.blocked_ledger.blocked_minutes()` at RC-close time, never recomputed from task folders in this handler. Rows are in CSV order (oldest to newest). | `422` when `format=json` is set without `project`. Omit `format` for the pre-v1.6.0 text envelope (byte-identical). `per_agent` is not applicable to the JSON branch. |
| `GET /costs?format=json` | `project`, `format=json` | `month`, `day`, `rc` (at most one) | JSON array of per-RC row objects — `[{version, tasks, tokens_in, tokens_out, cache_read_pct, est_cost}, …]`. Fields are omitted when the underlying source has no value. `rc` returns a single-element array; `month` and `day` filter by `shipped_at` prefix; omitting all three returns every RC file under `usage/rc/`. | `422` when `format=json` is set without `project`. Omit `format` for the pre-v1.6.0 text envelope (byte-identical). |
| `GET /logs/{kind}` | see per-kind table below | `tail` (int, default 200, hard cap 2000) | Standard envelope. `stdout` is the tail text; `exit_code == 0` when the file was read, `exit_code == 1` when the resolved path does not exist on disk (still HTTP 200). | `404` `{"error":"unknown log kind","kind":"<kind>"}` when `{kind}` is not one of the six known values. `422` when a required param is missing, when `agent`/`project` contains a traversal sequence (`..`, `/`, `\`, `%2F`, `%5C`, `%2E`, null bytes), when `agent` is not in the fixed role set, when `project` is not registered (and not `global` for the overwatch kind), or when `tail` is not a positive integer. |
| `GET /traces` | none | `project`, `agent`, `limit` (int, default 50, hard cap 2000) | JSON array of trace index entries, newest-first — `[{id, project, agent, task_key, timestamp, path_basename}, …]`. `id` is an opaque server-minted token (the trace file stem). `timestamp` is ISO-8601 UTC derived from the filename prefix. Unknown `project` or `agent` values return an empty array (not an error). | `422` when `project` or `agent` contains a traversal sequence, when `agent` is not in the fixed role set, or when `limit` is not a positive integer. |
| `GET /traces/{trace_id}` | `trace_id` (path) — the `id` of an entry `GET /traces` returned | — | Standard envelope. `stdout` is the trace file's markdown body; `exit_code == 0`. | `404` `{"detail":{"error":"trace not found","id":"<trace_id>"}}` when `{trace_id}` is not present in the server's own enumeration. Traversal-shaped or fabricated ids also resolve to 404 — a path component inside `{trace_id}` is never interpreted as filesystem navigation. |
| `GET /approvals` | none | `project` | JSON array of pending HUMAN-APPROVE gate records, one per task, sorted by project name then task ID — `[{task_id, project, state, rc, target_version, age, review, review_cmds, approve_cmd, reject_cmd}, ...]`. Empty array `[]` when no gates are pending. `state` is `WAITING` or `BACKLOG`; `target_version` is an alias for `rc` (both are included for consumers that prefer one or the other). `approve_cmd` and `reject_cmd` are the verbatim shell commands the operator would copy-paste. `review` is the task's `## Goal` lines joined with ` \| `. `review_cmds` is an ordered array of verbatim inspect-before-deciding commands (see field notes below). Absent `?project=` aggregates across ALL registered projects (no default project). Added in ICD 1.1.0: the `review_cmds` field. | `422` `{"detail":"Unknown project '<name>'. No such project directory found."}` when `?project=` names a project that is not registered. An empty array is **never** substituted for the unknown-project error. |
| `GET /auto-resolutions` | none | `project` | JSON array of OVERWATCH auto-resolution ledger records, newest-first within each project, projects ordered alphabetically — `[{project, rc, attempt_from, attempt_to, ceiling, attempt_display, decision, outcome, trigger, findings, evidence_paths, ledger_path, entry_id, family_key, timestamp}, ...]`. Empty array `[]` when no autonomous resolutions have ever run (the good news). `attempt_display` is `"N/M"` (`attempt_from` over the project's configured retry ceiling); `ceiling` renders `"?"` when the ceiling config cannot be resolved (a read-only display degrades gracefully rather than raising). `outcome` is a backfilled terminal state (e.g. `"SHIPPED attempt 2"`) or the literal `"pending"`. `evidence_paths` plus `ledger_path`/`entry_id` are the drilldown to the TESTER report and the ledger entry — the browser Auto-Resolutions tab's list-plus-drilldown pattern. Read-only: no approve/reject/mutate affordance in the response. Absent `?project=` aggregates across ALL registered projects (no default project). Added in ICD 1.3.0. | `422` `{"detail":"Unknown project '<name>'. No such project directory found."}` when `?project=` names a project that is not registered. An empty array is **never** substituted for the unknown-project error. |
| `GET /verdicts` | none | `project`, `details` (requires `project`) | `{"verdicts": [...], "warnings": [...]}`; with `details=true`, also `posture`, `release_locks`, `queue_tails` — the JSON face of `why.sh --details`, resolved through the same functions (added in ICD 1.8.0; see the field notes below). Each `verdicts` entry is one of two shapes. Resolved: `{project, verdict, exit_code, cause, remedy, line, task_id, task_state, minutes_in_state}` where `verdict` ∈ `RUNNING \| BLOCKED \| HALTED \| STUCK-LOCK \| IDLE` and `task_id`/`task_state` are present only for `RUNNING`/`BLOCKED`, `minutes_in_state` only for `RUNNING` (fields with no content are omitted, never null). Unresolved (the project is registered in `projects.cfg` but has no directory on disk): `{project, error: "project directory not found", cause, remedy}`. Resolved in process by `resolve_verdicts` (`team/pgai_agent_kanban/ops/verdict.py`) — the exact function `why.sh` reaches through `pp_run_ops` — so the CLI and this endpoint can never disagree about a lane's verdict. Absent `?project=` walks every registered project in `projects.cfg` priority order (same order `why.sh` walks them). Added in ICD 1.4.0. | `422` `{"detail":"Unknown project '<name>'. Not registered in projects.cfg."}` when `?project=` names a value not registered in `projects.cfg` at all, and `422` when `details=true` is given without `project`. A project registered in `projects.cfg` but missing its directory returns `200` with an unresolved entry, never `422` and never `5xx`. |
| `GET /agent-metrics` | `project` | `window` (int, default 10), `last` (int), `all` (bool, default false), `since` (`vX.Y.Z`, leading `v` optional) — the span, mutually exclusive: give at most one; with none the span is the newest 10 shipped RCs. `window` counts as given only when it appears in the query string, and `all=false` is the same as omitting it. | `{"project": str, "window": int, "span": {"kind", "count", "since"}, "shipped_count": int, "excluded_count": int, "agents": [...], "warnings": [...]}`. Each `agents` entry: `{agent, avg_wall_minutes, avg_tokens, avg_tasks, rc_count, total_tokens, total_tasks}`. Only shipped RCs count: a cancelled or otherwise unshipped rollup enters no figure — neither numerator nor denominator — and is counted in `excluded_count` instead; `shipped_count` is the number of shipped RCs the figures rest on (the CLI footer's "window: 10 shipped RCs (2 cancelled excluded)"). `span.kind` is `last` (with `count` N), `all`, or `since` (with `since` the bound as given); the unused member is `null`. `window` is N for a `window`/`last` span and `shipped_count` for an `all` or `since` span. `avg_wall_minutes`, `avg_tokens` and `avg_tasks` are JSON numbers carrying exactly one decimal (`2.0`, `4.7`), never integer-floored; `total_tokens` and `total_tasks` are integer sums across the span; none is comma-formatted — thousands separation is a CLI-renderer-only concern. `avg_wall_minutes` is in minutes and is a mean PER TASK, pooled from each invocation RC's own per-agent wall-clock data (never the RC's own shared elapsed time, which every agent that touched the RC would otherwise show identically) — it is `null` when none of the agent's invocation RCs carry that per-task data. `avg_tokens`/`avg_tasks` are each a mean PER INVOCATION RC (`rc_count`, the count of shipped RCs in the span the agent was invoked in) — a different denominator from `avg_wall_minutes`, by design; see the field notes below. `agents` is sorted by agent name ascending and is empty when the span holds no shipped rollup. Computed by the same functions `team/scripts/dashboard/agent-metrics.sh` calls, in `team/pgai_agent_kanban/lib/agent_metrics.py`, so the CLI and this endpoint can never disagree about a figure. No `projects.cfg` registration check is performed on `project` — exactly matching the CLI's own `--project` handling, which accepts any name and renders the empty/no-metrics placeholder for one that resolves to no rollup files. Added in ICD 1.5.0. ICD 1.10.0 extended it additively: the `last`, `all` and `since` parameters and the `span`, `shipped_count`, `excluded_count`, `total_tokens` and `total_tasks` keys are new, no key was removed or renamed, and the three averages keep their names and stay JSON numbers but now carry one decimal instead of a whole number. | `422` FastAPI's standard missing-required-query-param body (`{"detail":[{"type":"missing","loc":["query","project"],...}]}`) when `project` is omitted entirely, and `{"detail":"Required parameter 'project' is missing or empty."}` when it is empty; `{"detail":"window, last, all and since are mutually exclusive; give at most one (got: <names>)."}` when two or more span parameters are given; `{"detail":"window must be a positive integer (got: <n>)."}` or `{"detail":"last must be a positive integer (got: <n>)."}` for a non-positive count; `{"detail":"since must be a version of the form vX.Y.Z (got: <value>)."}` when `since` is not a version. |
| `GET /release-state` | `project` | — | `{project, active_rc, rc_opened_at, rc_opened_by_task, last_released, warnings}` — the project's `release-state.md` fields, validated through the same loader the `release-state` CLI verb uses (`pgai_agent_kanban.ops.read.get_release_state`); one resolver, two renderers. A field that is legitimately unset is `null`, matching `/projects/{name}`. Read-only: this route never writes CM's file. Added in ICD 1.8.0. | `404` `{project, error, message, remedy, warnings}` when `project` is not a registered project. `422` with the same four keys when a field fails validation — the machine key, message and remedy the CLI prints, never a guessed value. |
| `GET /deep-verify-status` | `project` | — | `{project, mode, entries, latest, warnings}` — the project's resolved DEEP-VERIFY cadence (`mode` ∈ `weekly \| off`, from `[release] deep_verify` in its `project.cfg`, else `kanban.cfg`, else the default `weekly`), every ledger entry (`{timestamp, project, version, commit, verdict, detail}`), and the newest one as `latest` (`null`, with `entries` `[]`, when the project has never been deep-verified). Resolved through `pgai_agent_kanban.ops.read.get_deep_verify_status`, the function the `deep-verify-status` CLI verb calls. Reads the ledger only; running the lane is `POST /operations/deep-verify`. Added in ICD 1.8.0. | `404` `{project, error, message, remedy, warnings}` when `project` is not a registered project. `422` with the same keys when the ledger is malformed or the configured `deep_verify` value is outside its accepted vocabulary. |

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

`/logs/{kind}` and `/traces/{trace_id}` do not accept a filesystem path from
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
- **Opaque server-minted ids (used by `/traces/{trace_id}`).** The trace `id`
  returned by `GET /traces` is the file stem — a token the server mints
  during its own enumeration of `logs/training/<agent>/*.md`. `GET
  /traces/{trace_id}` resolves that id ONLY by re-enumerating and matching
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
window 13 (`human-review`), whose left pane is the pending-approvals
list, and the surface an operator UI or a `curl` script consumes to
inspect pending human-approval gates without parsing rendered pane
text. The `human-review` renderer and this endpoint share one
implementation (`collect_pending_approvals` in
`team/pgai_agent_kanban/dashboard/scan_human_approvals.py`) — the JSON
body and the pane are guaranteed to agree on which tasks are pending.

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
  `team/pgai_agent_kanban/dashboard/scan_human_approvals.py`); this
  endpoint and the tmux `human-review` renderer emit the same strings
  from the same call. Added in ICD 1.1.0.
- `approve_cmd` and `reject_cmd` are the verbatim shell commands the
  operator would copy-paste — the same strings the `human-review` renderer
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
layer's standard conventions, the same ones the structured reads above
follow:

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

#### `GET /auto-resolutions` — OVERWATCH auto-resolution ledger

`GET /auto-resolutions` is the structured read behind the human-review
tmux window's Auto-Resolutions section and the browser UI's
Auto-Resolutions tab. The browser tab is
the tmux surface's sibling — both read the same append-only ledger
(`projects/<name>/auto-resolutions.md`, written by
`pgai_agent_kanban.overwatch.ledger`) through one implementation
(`collect_auto_resolutions` in
`team/pgai_agent_kanban/dashboard/scan_auto_resolutions.py`), so the
JSON body and the tmux pane are guaranteed to agree on which resolution
events exist.

This endpoint is **read-only**: it never writes to the ledger and the
response carries no approve/reject/mutate affordance — the surface
informs the operator, it never acts on their behalf.

**URL and parameters.** The path is `GET /auto-resolutions`. The only
query parameter is `project`, and it is optional. Absent `?project=`,
the endpoint aggregates ledger entries across ALL registered projects —
the same "no default project" rule the rest of the read layer follows.
When `?project=<name>` is supplied and `<name>` names a registered
project, results are filtered to that project.

**Response shape.** The body is a JSON array (not the standard
envelope), one object per ledger entry, newest-first within each
project (projects ordered alphabetically):

```json
[
  {
    "project": "pgai-agent-kanban",
    "rc": "v2.0.1",
    "attempt_from": 1,
    "attempt_to": 2,
    "ceiling": "3",
    "attempt_display": "1/3",
    "decision": "CANCEL-AND-REBUNDLE",
    "outcome": "pending",
    "trigger": "ship-policy-halt",
    "findings": ["BUG-0041-foo.md"],
    "evidence_paths": [
      "projects/pgai-agent-kanban/tasks/CM-20260718-001/artifacts/report.md"
    ],
    "ledger_path": "projects/pgai-agent-kanban/auto-resolutions.md",
    "entry_id": "entry-3f9a2b1c0d4e",
    "family_key": "bug-0041,bug-0042",
    "timestamp": "2026-07-27T18:03:00Z"
  }
]
```

Field notes:

- `attempt_display` is the display string `"N/M"` — `attempt_from` (the
  attempt count at which the block occurred) over `ceiling` (the
  project's configured `max_rc_retries`). `ceiling` renders `"?"` when
  the retry-ceiling config cannot be resolved for that project (missing
  `kanban.cfg`/`project.cfg` `[release] max_rc_retries`); a read-only
  display degrades gracefully rather than raising the resolver's
  fail-loud `CeilingConfigError`.
- `outcome` is the backfilled terminal state (e.g. `"SHIPPED attempt 2"`
  or `"CEILING-BLOCKED"`) once the bundle family reaches a terminal
  state, or the literal string `"pending"` while it has not.
- `decision` is the resolution matrix row matched (e.g.
  `"CANCEL-AND-REBUNDLE"`).
- `findings` is the list of bug-report identifiers the resolution acted
  on. `family_key` is the bundle-family key used for retry-counter
  keying, or `null` when absent.
- `evidence_paths` plus `ledger_path`/`entry_id` together form the
  **drilldown pair** the Auto-Resolutions tab renders: `evidence_paths`
  points at the underlying reports (TESTER report, ship-policy HALT
  file, release-state file, ...) — the ledger is the index, not the
  record, so evidence paths are never duplicated content.
  `ledger_path`/`entry_id` link back to the ledger entry itself. This is
  the same list-plus-drilldown pattern the Approvals tab uses
  (`review_cmds` for Approvals; `evidence_paths` + `ledger_path`/
  `entry_id` for Auto-Resolutions).

**Empty state.** A clean system with no ledger entries returns `[]`
with HTTP 200 — no autonomous resolution is the good news, not an
error.

**Unknown project.** `?project=<name>` where `<name>` is not a
registered project returns `422`, using the same error shape as `GET
/approvals`:

```json
{"detail": "Unknown project 'does-not-exist'. No such project directory found."}
```

An empty array is **never** substituted for the unknown-project error.

**Standard read-layer conventions.** Same conventions as `GET
/approvals`: loopback bind only, opaque server-minted ids (`entry_id`),
`422`-before-any-filesystem-scan on an unknown project, and no
shell-out — the handler reads the ledger and config files directly.

**curl example.**

```bash
# All auto-resolution events across every project
curl -sS http://127.0.0.1:8300/auto-resolutions | jq

# Narrow to one project
curl -sS "http://127.0.0.1:8300/auto-resolutions?project=pgai-agent-kanban" | jq

# Clean system — returns []
curl -sS http://127.0.0.1:8300/auto-resolutions
# []
```

#### `GET /verdicts` — per-project lane verdicts, in parity with the CLI

`GET /verdicts` is the JSON face of `team/scripts/why.sh`. It answers the
same question the CLI verb answers — *why is this lane not moving?* — for
every registered project or for one, as a structured body a dashboard or a
`curl` script can consume without parsing a rendered sentence.

**The parity guarantee.** Both surfaces resolve the same verdicts through
the same decision, so a dashboard and a terminal cannot disagree. Concretely:
`resolve_verdicts` in `team/pgai_agent_kanban/ops/verdict.py` is the only
place in the system that decides what a lane's verdict is. `why.sh` reaches
it through `pp_run_ops` and prints a sentence; `verdict-report.sh` reaches
it the same way and prints the *same resolved fields* as machine-readable
`key=value` blocks; this endpoint calls it in process and returns them as
JSON. No surface starts a process to ask another, text-scrapes a rendered
sentence, or re-derives a verdict on its own. There is one decision and
three renderings of it.

**The verdict vocabulary is documented once**, in
[`docs/operator-commands.md`](operator-commands.md) under "why — one verdict
per project, with its cause, its remedy, and an exit code": what each of the
five verdicts means, what the operator does next about it, and the exit-code
table. This section documents the endpoint, not the vocabulary — the `verdict`
and `exit_code` values below are the ones defined there.

**URL and parameters.** The path is `GET /verdicts`. Two query parameters,
both optional: `project` and `details`. Absent `?project=`, the endpoint walks
every project registered in `projects.cfg`, in the same priority order `why.sh`
walks them with no `--project`. Supplied, it narrows to that one project.
`details=true` opens the diagnostic pane behind one project's verdict, exactly
as the CLI's `--details` does, and for the same reason requires `project` —
see "The details pane" below.

**Response shape.** The body is an object with a `verdicts` array and the read
layer's standard `warnings` array. An example body, one project, healthy:

```json
{
  "verdicts": [
    {
      "project": "pgai-agent-kanban",
      "verdict": "RUNNING",
      "exit_code": 0,
      "cause": "an agent is actively processing it",
      "remedy": "none needed while it stays within the 60m stall threshold.",
      "line": "pgai-agent-kanban: RUNNING — task CODER-20260101-014-add-retry-backoff is WORKING, 3m in that state; cause: an agent is actively processing it; remedy: none needed while it stays within the 60m stall threshold.",
      "task_id": "CODER-20260101-014-add-retry-backoff",
      "task_state": "WORKING",
      "minutes_in_state": 3
    }
  ],
  "warnings": []
}
```

Field notes:

- `verdict` is one of `RUNNING`, `BLOCKED`, `HALTED`, `STUCK-LOCK`, `IDLE`. `exit_code` is
  the integer the CLI would exit with for this project — including the `3` that
  distinguishes a stalled `RUNNING` from a healthy one, which the verdict word
  alone does not carry.
- `line` is the exact sentence `why.sh` prints for this project. It is a
  display convenience for a UI that wants the human phrasing; the structured
  fields beside it are the contract. Do not parse `line`.
- `task_id` and `task_state` are present only for `RUNNING` and `BLOCKED` —
  the two verdicts that name an active task. `minutes_in_state` is present
  only for `RUNNING`.
- A field with no content for a given project is **omitted entirely**, never
  null and never zero-filled — the same convention `/metrics?format=json` and
  `/costs?format=json` already use.

**The unresolved shape.** A project registered in `projects.cfg` whose
`projects/<name>` directory does not exist on disk returns a second entry
shape, with no `verdict` key at all — an example entry:

```json
{
  "project": "ghost-project",
  "error": "project directory not found",
  "cause": "ghost-project is registered but has no directory at /home/you/pgai_agent_kanban/projects/ghost-project",
  "remedy": "check the project name against projects.cfg, or restore the missing project directory if it was deleted in error"
}
```

This exists so that a missing directory is never reported as a false `IDLE`.
An empty task scan and an absent `HALT` file against a directory that is not
there read exactly like a healthy, work-free project — which would be a
confident answer about a project that was never resolved. The endpoint names
the condition instead.

**HTTP status.**

- `200` — success, including when individual entries carry the unresolved
  shape, and including an empty `verdicts` array when nothing is registered.
- `422` — `?project=` names a value that is not registered in `projects.cfg`
  at all: `{"detail": "Unknown project '<name>'. Not registered in
  projects.cfg."}`; or `details=true` was given without `project`. A
  registered project with a missing directory is `200`
  with an unresolved entry, never `422` and never `5xx` — "you named something
  that is not a project" and "this project's directory is gone" are different
  problems and do not collapse to one response.
- `500` — the underlying script failed for a reason other than an unresolved
  project directory (a genuine resolver failure).

**The details pane — `?details=true`.** With `details=true` and a `project`,
the body gains three keys beside `verdicts` and `warnings`, the JSON face of
the pane `why.sh --project NAME --details` prints:

- `posture` — the resolved deferral and halt posture: `halt_state`
  (`normal`, `halted` or `draining`, plus `halt_event` when one was
  recorded), `deferred_bugs`, `deferred_bug_level`, `defer_piggyback` and
  `until`, each as `{value, tier}` naming the precedence tier the value came
  from (`until` also carries `campaign_file` when that tier is the campaign
  file), `active_rc`, and `pending_bundles` (`[{name, status}, …]`).
- `release_locks` — one entry per lock held for the project:
  `{path, age_seconds, held_seconds, provenance, stale_threshold_seconds,
  stale_threshold_derivation, violation}`. Empty when the project holds none.
  The two durations answer different questions:
  - `held_seconds` (integer or `null`) — how long the lock has been held:
    now minus the `acquired_at` time recorded in the lock's provenance. It
    is the same duration `why.sh --details` and `release-locks.sh` show as
    `held: <duration>`. It is `null` when the record carries no
    `acquired_at` (a lock taken before acquisition times were recorded;
    the CLI says "acquired_at not recorded (pre-upgrade lock)"). It is
    never filled in from the lock file's modification time. Added in ICD
    1.12.0.
  - `age_seconds` (integer) — seconds since the lock file was last
    refreshed (its modification time). A live holder refreshes its lock
    while it works, so this is a heartbeat age: a large value means the
    holder has stopped refreshing, not that the lock has been held that
    long. Use `held_seconds` for held time.
- `queue_tails` — one entry per `tasks/queues/*_backlog.md` file:
  `{agent, entries: [{marker, task_id, status_state}, …]}`.

Every one of those fields comes from the same resolved details the CLI's
`--details` text sections and `verdict-report.sh --details` blocks are
rendered from — lock provenance, deferral posture, the HALT/HALT-AFTER state,
the pending bundles. There is no second derivation of lock state, queue state
or posture. When the project's deferral posture does not resolve (a malformed
campaign file or an invalid `[release]` value) the endpoint answers `500`
with the resolver's error/message/remedy block in `detail`, the refusal the
CLI prints. The three keys are omitted
for an unresolved project (registered, but no directory on disk): there is
nothing to detail, matching the CLI's own refusal for that case. Stall
verdicts need no extra field — a stalled `RUNNING` lane keeps `verdict:
"RUNNING"` with `exit_code: 3`, on both surfaces. `details=true` without
`project` is `422`, mirroring `why.details_requires_project` on the CLI.

**The most recent refusal stays on the CLI side.** The CLI's `--details`, not
`?details=true`, answers "what did discovery or a gate last refuse for this
project, and why" — it reads the project's refusal ledger and prints the refusal's
timestamp, surface, `error`, `message`, and `remedy`. Two of its answers must
not be confused, and the CLI keeps them apart: a project that has never
recorded a refusal says so in words, while a ledger file that exists but does
not parse refuses rather than reporting an absence it cannot confirm. An
unreadable ledger reads as unusable, never as "no refusals." The full
description is in [`docs/operator-commands.md`](operator-commands.md).

**Standard read-layer conventions.** Loopback bind only, per
[Section 3](#3-access-model). Read-only — the underlying script creates no
file, edits no file, and removes no file, so a poll loop against this endpoint
changes nothing. `422` on an unregistered project: the registry check
happens at the API layer before any verdict is resolved.

**curl example.**

```bash
# Every registered project, in projects.cfg priority order
curl -sS http://127.0.0.1:8300/verdicts | jq

# Narrow to one project
curl -sS "http://127.0.0.1:8300/verdicts?project=pgai-agent-kanban" | jq

# That project's verdict plus the --details pane: posture, locks, queue tails
curl -sS "http://127.0.0.1:8300/verdicts?project=pgai-agent-kanban&details=true" | jq

# Just the projects that need a human
curl -sS http://127.0.0.1:8300/verdicts \
  | jq '.verdicts[] | select(.exit_code >= 4)'
```

#### `GET /agent-metrics` — per-agent, per-project averages and totals, in parity with the CLI

`GET /agent-metrics` is the JSON twin of
`team/scripts/dashboard/agent-metrics.sh`. For one project, it returns each
agent's average wall time, tokens and task count, plus its total tokens and
total tasks. The figures cover a chosen span of **shipped** RCs, read from the
per-RC metrics rollups (`projects/<name>/metrics/rc/v*.json`, written by
`team/scripts/lib/metrics_aggregator.py`). The span is the newest 10 unless
you choose another.

**The parity guarantee.** Both surfaces call the same functions in
`team/pgai_agent_kanban/lib/agent_metrics.py`. `select_rc_span()` picks the
span's rollups. `compute_agent_averages()` derives each agent's averages and
totals from them. `round_one_decimal()` rounds every average. The CLI's own
renderer, `pgai_agent_kanban.lib.metrics.agents` (which
`agent-metrics.sh` reaches through `pp_run_ops pgai_agent_kanban.lib.metrics
compute_agent_averages`), imports those functions rather than defining a
second copy, then renders a thousands-separated text table over them. This endpoint imports the same package module directly and never
re-derives the span, averaging or rounding arithmetic. There is one
computation and two renderings of it. That module is the reference for the
arithmetic; this section describes behavior.

**URL and parameters.** The path is `GET /agent-metrics`.

| Parameter | Type | Meaning | CLI twin |
|---|---|---|---|
| `project` | string, **required** | The project to report on. | `--project <name>` |
| `window` | integer, default `10` | The newest N shipped RCs. | `--window <N>` |
| `last` | integer | The newest N shipped RCs. Same span as `window`. | `--last <N>` |
| `all` | boolean, default `false` | When `true`, every shipped RC. `all=false` is the same as omitting it. | `--all` |
| `since` | string `vX.Y.Z` | Every shipped RC at or above that version, in semantic-version order. The leading `v` is optional. | `--since <vX.Y.Z>` |

`window`, `last`, `all=true` and `since` choose the span and are **mutually
exclusive**: give at most one. The CLI's span flags work the same way. With
none, the span is the newest 10 shipped RCs. `window` counts as given only when
it appears in the query string. Its default of 10 does not collide with
`last`, `all` or `since`.

No `projects.cfg` registration check is performed on `project`. This
deliberately mirrors the CLI twin's own `--project` handling. The CLI accepts
any string and renders the empty/no-data placeholder when the resulting
`metrics/rc/` directory does not exist, whether the name is unregistered or
the project has not shipped an RC yet. `/verdicts`, `/approvals` and
`/auto-resolutions` do validate `project` against the registry, because their
CLI twins do. `/agent-metrics` performs no such check because
`agent-metrics.sh --project` performs none either. The REST and CLI surfaces
would disagree if this endpoint added a check the CLI does not have.

**Only shipped RCs count.** A rollup counts only when its own `outcome` is
`shipped`. A cancelled or otherwise unshipped RC enters **no** figure: it is
in neither the numerator nor the denominator of any average, and adds nothing
to any total. Instead, it is counted in `excluded_count`. `shipped_count`
and `excluded_count` are the two numbers the CLI's footer narrates, e.g.
`window: 10 shipped RCs (2 cancelled excluded)`. A missing or malformed
rollup file is skipped without taking a slot in the span.

**Response shape.** The body is an object with these top-level keys:

| Key | Type | Meaning |
|---|---|---|
| `project` | string | The project as requested. |
| `window` | integer | The span's RC bound. It is N for a `window` or `last` span (10 by default) and equals `shipped_count` for an `all` or `since` span. |
| `span` | object | Which span was selected. `{"kind": "last"\|"all"\|"since", "count": int\|null, "since": str\|null}`. `count` is set only for `last`, and `since` (the bound as given) only for `since`. The unused member is `null`. A `window` span reports `kind` `last`. |
| `shipped_count` | integer | The number of shipped RCs the span selected, meaning the RCs every figure rests on. It can be below N when fewer have shipped. |
| `excluded_count` | integer | The number of non-shipped rollups inside the span's range. None of them entered any figure. |
| `agents` | array | One entry per agent invoked in the span (below). |
| `warnings` | array | The read layer's standard warnings, e.g. an unrecognized query parameter. |

Each `agents` entry:

| Key | Type | Meaning |
|---|---|---|
| `agent` | string | Agent name. |
| `avg_wall_minutes` | number (one decimal) or `null` | Mean wall time **per task**, in minutes. |
| `avg_tokens` | number (one decimal) | Mean tokens **per invocation RC**. |
| `avg_tasks` | number (one decimal) | Mean tasks **per invocation RC**. |
| `rc_count` | integer | Invocation RCs: the shipped RCs in the span this agent was invoked in. |
| `total_tokens` | integer | Tokens summed across the agent's invocation RCs. |
| `total_tasks` | integer | Tasks summed across the agent's invocation RCs. |

An example body (illustrative values, newest-10 span with one cancelled RC in
range):

```json
{
  "project": "pgai-agent-kanban",
  "window": 10,
  "span": {"kind": "last", "count": 10, "since": null},
  "shipped_count": 10,
  "excluded_count": 1,
  "agents": [
    {"agent": "coder", "avg_wall_minutes": 30.4, "avg_tokens": 41000.0, "avg_tasks": 2.3, "rc_count": 4, "total_tokens": 164000, "total_tasks": 9},
    {"agent": "writer", "avg_wall_minutes": null, "avg_tokens": 3000.0, "avg_tasks": 1.0, "rc_count": 1, "total_tokens": 3000, "total_tasks": 1}
  ],
  "warnings": []
}
```

Field notes:

- **One decimal, never floored.** `avg_wall_minutes`, `avg_tokens` and
  `avg_tasks` each carry exactly one decimal (`2.0`, `4.7`). They are rounded
  by the same one-decimal rule the CLI applies before formatting, so an agent
  that averages two tasks per RC reads `2.0`, not `1`. `total_tokens` and
  `total_tasks` are integer sums. No number in the body is comma-formatted.
  Thousands separation is a presentation concern that belongs to the CLI's
  text renderer alone. A consumer that wants `"41,000.0"` formats it itself.
- `agents` is sorted by agent name ascending, the same sort
  `agent-metrics.sh`'s renderer applies.
- `avg_tokens` and `avg_tasks` are each a mean **per invocation RC**, with
  `rc_count` as the denominator. `total_tokens` and `total_tasks` are the
  matching numerators, so for any agent the total divided by `rc_count` is
  the average before rounding.
- `avg_wall_minutes` is a mean **per task**, a different denominator from
  `avg_tokens`/`avg_tasks` above. It pools each invocation RC's own
  `wall.by_agent.<agent>` block (`seconds` and `task_count`, written by
  `metrics_aggregator.py`) across the span and divides the pooled sums. It
  never uses the RC-level `wall_time_minutes` field. That field is the whole
  RC's elapsed time, shared identically by every agent that touched the RC,
  so it cannot say how long this agent's own work took. `avg_wall_minutes`
  is `null` when none of the agent's invocation RCs carry a `wall.by_agent`
  entry for it. That happens with an older rollup written before per-task
  wall-time capture existed, or when every task in those RCs lacked a
  usable `elapsed_seconds`. The CLI renders that case as `--`.
- `rc_count` is the `avg_tokens`/`avg_tasks` denominator, not the
  `avg_wall_minutes` one. The two averages are pooled from different data
  and can legitimately rest on different sample sizes for the same agent.
- An agent with zero invocations in the span is simply **absent** from
  `agents`. It never appears as a zero-average entry or a division by zero.
- `agents` is an empty array when the span holds no shipped rollup. That is
  the same condition the CLI renders as `(no metrics yet)`.

**HTTP status.**

- `200` — success, including an empty `agents` array.
- `422` — the request is refused, with a `detail` naming the parameter:
  - `project` omitted entirely: FastAPI's standard missing-required-query-param
    body (`{"detail":[{"type":"missing","loc":["query","project"],...}]}`).
  - `project` empty:
    `{"detail": "Required parameter 'project' is missing or empty."}`.
  - More than one of `window`, `last`, `all=true` and `since`:
    `{"detail": "window, last, all and since are mutually exclusive; give at most one (got: <names>)."}`.
    The exclusivity check runs first, so a conflicting request is refused
    for the conflict even when one of its values is also invalid.
  - `window` or `last` not a positive integer:
    `{"detail": "window must be a positive integer (got: <n>)."}` or
    `{"detail": "last must be a positive integer (got: <n>)."}`. A value that
    is not an integer at all gets FastAPI's standard type-validation body
    instead.
  - `since` not of the form `vX.Y.Z`:
    `{"detail": "since must be a version of the form vX.Y.Z (got: <value>)."}`.

**Contract compatibility.** The route was added in ICD 1.5.0. ICD 1.10.0 extended
it **additively**:

- The `last`, `all` and `since` parameters are new.
- The `span`, `shipped_count` and `excluded_count` keys are new, and each
  `agents` entry gains `total_tokens` and `total_tasks`.
- The three averages kept their names and remain JSON numbers, now carrying
  one decimal instead of a whole number. `window` remains an integer.

No key was removed or renamed and no parameter became required, so a 1.5.0
client keeps working. It simply ignores the new keys.

**Standard read-layer conventions.** Loopback bind only, per
[Section 3](#3-access-model). Read-only: no file is created, edited, or
removed, so a poll loop against this endpoint changes nothing.

**curl example.**

```bash
# Default span: the newest 10 shipped RCs
curl -sS "http://127.0.0.1:8300/agent-metrics?project=pgai-agent-kanban" | jq

# The newest 3 shipped RCs (window=3 is the same span)
curl -sS "http://127.0.0.1:8300/agent-metrics?project=pgai-agent-kanban&last=3" | jq

# Every shipped RC -- the dashboard's full-history pane, as JSON
curl -sS "http://127.0.0.1:8300/agent-metrics?project=pgai-agent-kanban&all=true" | jq

# Every shipped RC from v2.20.0 on
curl -sS "http://127.0.0.1:8300/agent-metrics?project=pgai-agent-kanban&since=v2.20.0" | jq

# How many RCs the figures rest on, and how many were excluded
curl -sS "http://127.0.0.1:8300/agent-metrics?project=pgai-agent-kanban&all=true" \
  | jq '{span, shipped_count, excluded_count}'

# Per agent: average and total tasks
curl -sS "http://127.0.0.1:8300/agent-metrics?project=pgai-agent-kanban" \
  | jq '.agents[] | {agent, avg_tasks, total_tasks}'
```

The CLI equivalents of the span choices, runnable from the operator window's
cmdline pane:

```bash
scripts/dashboard/agent-metrics.sh --project pgai-agent-kanban
scripts/dashboard/agent-metrics.sh --project pgai-agent-kanban --last 3
scripts/dashboard/agent-metrics.sh --project pgai-agent-kanban --all
scripts/dashboard/agent-metrics.sh --project pgai-agent-kanban --since v2.20.0
```

### Mutations (POST)

Body is JSON. Field names match the script flag names (`-` in flag names
maps to `_` in body fields — `dry-run` becomes `dry_run`, `keep-artifacts`
becomes `keep_artifacts`).

| Endpoint | CLI twin | Required body fields | Optional body fields |
|---|---|---|---|
| `POST /operations/halt` | `halt.sh` | `project` | — |
| `POST /operations/unhalt` | `unhalt.sh` | `project` | — |
| `POST /operations/halt-after` | `halt-after.sh` | `project` | `key` (defaults to `rc`, the script's own default, when omitted), `until` (checked as the script checks it, refused with the script's own `halt-after.until_malformed` / `halt-after.until_scope_mismatch`), `dry_run` |
| `POST /operations/halt-global` | `halt-global.sh` | (none) | — |
| `POST /operations/unhalt-global` | `unhalt-global.sh` | (none) | — |
| `POST /operations/defer` | `defer.sh` | `project` | `until` (required by the script, not pre-validated here), `dry_run` |
| `POST /operations/undefer` | `undefer.sh` | `project` | `dry_run` |
| `POST /operations/deep-verify` | `deep-verify.sh` | `project` (unless `scheduled` is true with no project) | `scheduled`, `image`, `dry_run` |
| `POST /operations/wake-now` | `wake-now.sh` | `agent` (unless `all` is true) | `all`, `bg`, `dry_run` |
| `POST /operations/reset` | `reset.sh` | `project`, and exactly one of `key` / `agent` / `bug` / `priority` / `requirement` | `keep_artifacts` |
| `POST /operations/close` | `close.sh` | `project`, `key` | `state`, `note`, `dry_run` |
| `POST /operations/wontdo` | `wontdo.sh` | `project`, `key` | — |
| `POST /operations/delete` | `delete.sh` | `project`, `key` | `force`, `dry_run` |
| `POST /operations/intake` | `intake.sh` | `project`, `filename`, `content` | `dry_run` |
| `POST /operations/unwind-rc` | `unwind-rc.sh` | `project`, `key`, `confirm` (`true`, when `dry_run` is not `true`) | `force`, `dry_run` |
| `POST /operations/set-version-ceiling` | `set-version-ceiling.sh` | `project` | `show`, `minor`, `major`, `no_minor`, `no_major`, `dry_run` |
| `POST /operations/switch-provider` | `switch-provider.sh` | `provider` | — |
| `POST /operations/create-project` | `create-project.sh` | `project` | `workflow_type`, `max_patch`, `max_minor`, `max_major`, `git_remote`, `priority`, `color`, `no_migrate`, `dry_run`, `dev_tree`, `git_repo` |
| `POST /operations/add-project` | `add-project.sh` | `project` | `priority`, `color`, `no_migrate`, `dry_run` |
| `POST /operations/remove-project` | `remove-project.sh` | `project`, `confirm` (when `force` is `true` and `dry_run` is not) | `force`, `confirm`, `dry_run` |
| `POST /operations/ship-rc` | `ship-rc.sh` | `project`, `key`, `confirm` | `dry_run` |
| `POST /operations/cold-build-now` | `cold-build-now.sh` | `project` | `image`, `dry_run` |
| `POST /operations/cancel-rc` | `cm/cancel-rc.sh` | `project`, `key`, `yes` (`true`, when `dry_run` is not `true`) | `force`, `dry_run` |
| `POST /operations/release-locks` | `release-locks.sh` (`--release` mode) | `release` | `force`, `dry_run` |
| `POST /operations/archive` | `archive.sh` | `project` | `dry_run`, `count`, `restore`, `reconcile_queues` (`restore` is refused with `dry_run`, `count` or `reconcile_queues`, and `reconcile_queues` with `count` — `archive.conflicting_options`; see the note below) |

Notes on specific mutations:

- **`defer` / `undefer`** — Added in ICD 1.7.0. Both follow the
  `halt`/`unhalt` pattern, with one deliberate exception: `until` is **not**
  pre-validated at the API layer. A missing or malformed value is forwarded
  verbatim, so `defer.sh`'s own dual-channel refusal
  (`defer.until_required` / `defer.until_malformed`) propagates unchanged in
  the envelope's `stderr` — the HTTP surface is not gentler, and not
  stricter, than the CLI. `defer` is idempotent: re-arming with the same
  `until` target is a no-op, a different target replaces it. `undefer` on a
  project with no campaign file succeeds and reports there was nothing to
  clear. `200` on `exit_code` 0; `422` on a missing `project` and on the
  script's own `until` refusals; any other refusal answers the status its
  kind maps to under [The envelope](#the-envelope).
- **`deep-verify`** — Added in ICD 1.7.0. Like `cold-build-now` and a
  `wake-now` with `bg` or `all`, it does **not** wait for its script to
  finish. `deep-verify.sh` blocks
  for the cold-build lane's full duration (budget roughly thirteen
  minutes), and holding an HTTP connection open that long is the case this
  endpoint exists to avoid: the script is launched detached and the handler
  returns as soon as the child starts. **The response confirms the run
  started; it never reports a verdict**, because the lane has not produced
  one yet. Poll the project's `deep-verify-ledger.md` or `GET /verdicts`
  once the run completes. The run is the operator-only container tier, and
  no body field authorizes it: it runs only when the server was started
  with `PGAI_CONTAINER_TIER=1` in its environment.

  The optional `image` field selects the images this call builds. It is the
  REST form of `--image`: a list of names declared in
  `team/config/container-images.txt`, or the string `"all"`, with each
  entry passed to the script as one `--image <entry>`, in order. A name the
  manifest does not declare is refused `422` with
  `container_tier.unknown_image` before anything is spawned, `dry_run`
  included. Omit the field (or send `null` or an empty list) and the run
  builds what `kanban.cfg [release] container_images` selects; with no image
  default either, it lists the available images and builds nothing. The
  field never grants the `PGAI_CONTAINER_TIER=1` authorization the server's
  environment must carry.

  Without the authorization the detached run prints one `SKIPPED` line and
  exits 0; with no image selected it lists the images and exits 0. Either
  way nothing is built and no ledger record is written, so a `200` here is
  not evidence the lane ran (`docs/operator-commands.md`, "deep-verify,
  cold-build-now"). `200` once dispatch succeeds; `422` when `project` is
  missing and `scheduled` is not true, or when `image` names an undeclared
  image. A start failure (the script not executable, say) raises rather
  than returning a fabricated envelope — there is no completed subprocess
  to report an `exit_code` from.
- **`wake-now`** — Added in ICD 1.7.0. Dispatch splits on the flags.
  `bg` or `all` take the same detached path as `deep-verify` and return
  `200` confirming `wake-now.sh` **started**, not that any fired agent
  finished; `bg` is the recommended form for an HTTP caller. Supplying
  neither flag uses the ordinary blocking path and returns the script's own
  completed `exit_code`/`stdout`/`stderr` — a faithful mirror of the bare
  CLI's foreground contract. The mutual-exclusivity refusal between `agent`
  and `all`, and the "one of them is required" refusal when neither is
  given, are the script's own guards and propagate unchanged on the
  foreground path rather than being re-implemented at the API layer.
  `dry_run` is checked before `bg`/`all` route anywhere, so a dry run fires
  nothing.
- **`intake`** — `filename` must be a bare file name: no path separator
  (`/` or `\`), no `..`, not an absolute path, and no URL-encoded
  separator, dot or NUL byte. It must also match one of the intake routing
  prefixes — `BUG-*`, `PRIORITY-*` or `v[0-9]*.md` — which send the file to
  the project's `bugs/`, `priority/` or `requirements/` directory. The
  route and `intake.sh` share one filename check, so both accept and refuse
  the same names.

  Any other filename is refused before anything is written. A name that is
  a path is refused `422` dual-channel with `intake.filename_not_bare`
  before the route resolves the project, builds the dry-run plan, or
  creates any directory or file, so a dry run naming a path is refused the
  same way. The `message` never echoes the refused value, and `remedy` is a
  runnable `curl` line re-sending the request with a bare example name.

  A bare name that matches no routing prefix (`notes.md`, say) is refused
  with the deposit's own `deposit_intake: cannot route ...` text as
  `message`, at `409` under the fallback key `intake.refused`, before the
  temp file is written. Its `remedy` says nothing was written and gives a
  runnable `curl` line re-sending the request under the routable example
  name `BUG-0001-example.md` — the same body the bare-filename refusal
  offers — so it names no temp file. A deposit that refuses with plain text
  after the temp file is written falls back to the same key, with a remedy
  that re-runs `team/scripts/intake.sh` on the file now in place. A dry run
  applies only the path rule: it does not catch an unroutable bare name.

  An accepted name is written to a new file in a fresh per-request
  directory, `${PGAI_AGENT_KANBAN_TEMP_DIR}/intake/<request-id>/<filename>`
  (`/tmp/pgai_kanban_tmp` when the variable is unset), so two requests
  naming the same file never share or overwrite a temp file. The request
  directory and the file are each checked to resolve under the intake
  directory before they are created, and the file is created exclusively.
  The route then deposits it in process through
  `pgai_agent_kanban.ops.write.deposit_intake`, the function `intake.sh`
  reaches; no subprocess starts. The dry-run plan names the equivalent
  `intake.sh --project <name> <tempfile>` call (the file path is
  positional, not a `--file` flag). The temp file is left in place after
  the deposit, and the pipeline's quarantine behavior applies unchanged to
  what was deposited.
- **`reset`** — Body carries a mutually-exclusive kind selector matching
  `reset.sh`. Supply `project` plus exactly one of `key`, `agent`, `bug`,
  `priority`, or `requirement`. Two or more selectors returns 422 naming
  the conflicting fields; zero selectors returns 422 naming the missing
  choice. No silent precedence between selector kinds. `keep_artifacts`
  is optional.
- **`delete`** — Refuses on ambiguous keys and on non-terminal items
  (unless `force` is true). Refusals propagate as the script's non-zero
  exit code, with the refusal lifted to the top-level
  `error`/`message`/`remedy` keys described below, at the status its kind
  maps to: `409` for a non-terminal item (exit code 2, under the fallback
  key `delete.refused`) or a key that matches more than one item, `404` for
  a key that matches nothing, and the resolver's `404` / `409` for an
  unknown or ambiguous `project`. Anything else — a registered project with
  no directory, a task folder missing its `status.md`, the filesystem
  refusing the removal — answers `500`.
- **`unwind-rc`** — Two fields, two meanings: `confirm` is the
  confirmation, `force` is the one guard bypass, and neither stands in for
  the other. The route calls `pgai_agent_kanban.cm.unwind.unwind_rc`, the
  function `unwind-rc.sh` reaches, in process.

  `confirm` answers the prompt. The CLI asks `Type 'yes' to continue`
  before it touches anything, and an HTTP caller has no terminal to answer
  it, so every live call (`dry_run` not `true`) must carry `confirm: true`.
  The typed phrase `unwind-rc <project> <key>` is accepted as the same
  answer (byte-for-byte: single spaces, no trimming, no case-folding). A
  missing, `false` or empty `confirm` returns 422
  `unwind_rc.confirm_required`; any other string returns 422
  `unwind_rc.confirm_mismatch`. Both refusals come before anything runs.
  `confirm` bypasses no check.

  `force: true` skips exactly one check: that `key` matches the project's
  `## Active RC`. Only `force` skips it; `confirm` never does. Nor is
  `force` a confirmation: `force` without `confirm` is refused
  `unwind_rc.confirm_required` like any other unconfirmed call. It does
  **not** bypass the HALT, project-existence, version-format or
  shipped-tag checks. Those checks belong to `unwind_rc`; a failed one
  returns a non-zero `exit_code`, the guard message in `stderr`, and the
  refusal lifted to the top-level keys (see the refusal shape below). The
  guards refuse in plain text, lifted under the fallback key
  `unwind_rc.refused`, which names no kind of refusal, so the HALT,
  Active-RC, project-directory, version-format and shipped-tag refusals
  return HTTP 500. A `project` the resolver cannot match is the
  exception: `404` for `project_resolve.not_found`, `409` for
  `project_resolve.ambiguous`.

  `dry_run: true` stops at the API layer: nothing runs, `stdout` describes
  the CLI call a live request is equivalent to, and `confirm` is not
  required. Because nothing runs, a preview does not evaluate the HALT or
  Active-RC checks. The CLI differs in one respect: there, `--force` also
  answers the prompt, so a scripted recovery can pass it alone. Over REST
  it never does. The worked example is in
  [Section 7](#unwind-rc-example-dry-run-first-then-real).
- **`remove-project`** — Unregisters only, unless `force` is given. The
  route calls `pgai_agent_kanban.ops.projects.remove_project`, the function
  `remove-project.sh` reaches, in process.

  The default call — `project` alone, no `force` needed — is the safe,
  reversible mode: the project is removed from `projects.cfg` and its
  directory is left in place. Removing a project that is already
  unregistered succeeds, so the call can be repeated.

  `force: true` is the destructive escalation: the project directory is
  deleted too. Because that cannot be undone, a live `force` call must also
  carry `confirm` equal to exactly `remove-project <project>` (a single
  space, no trimming, no case-folding). A `force` call with no `confirm`
  returns 422 `remove_project.confirm_required` and a wrong string 422
  `remove_project.confirm_mismatch`, both before anything runs. `confirm`
  is ignored when `force` is not `true`.

  A project with an RC in flight is refused in both modes, and `force`
  does **not** bypass it. When the project's `## Active RC` is anything but
  `none`, the call returns HTTP 409 at exit code 2 with
  `remove_project.active_rc` before anything changes — the same block
  `remove-project.sh` prints with or without `--force`. When that field
  does not validate, the refusal is `remove_project.release_state_invalid`
  at HTTP 422, also at exit code 2. Ship or cancel the RC first.

  `dry_run: true` previews either mode without `force` or `confirm`. It
  stops at the API layer, so it does not evaluate the Active-RC guard; the
  CLI's `--dry-run` does, and refuses first.

  Examples:

  ```bash
  # Default: unregister, keep the directory. Refused (HTTP 409,
  # remove_project.active_rc, exit_code 2) while the project has an Active RC.
  curl -sS -X POST http://127.0.0.1:8300/operations/remove-project \
      -H 'Content-Type: application/json' \
      -d '{"project": "my-app"}'

  # Refused at the API layer (HTTP 422, remove_project.confirm_required):
  # force without confirm removes nothing.
  curl -sS -X POST http://127.0.0.1:8300/operations/remove-project \
      -H 'Content-Type: application/json' \
      -d '{"project": "my-app", "force": true}'

  # Destructive: unregister and delete the directory. Still refused while
  # the project has an Active RC.
  curl -sS -X POST http://127.0.0.1:8300/operations/remove-project \
      -H 'Content-Type: application/json' \
      -d '{"project": "my-app", "force": true, "confirm": "remove-project my-app"}'
  ```
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

- **`cold-build-now`** — Backgrounded, exactly like `deep-verify`:
  `cold-build-now.sh` is a named alias that execs
  `deep-verify.sh --project NAME`, so the handler launches it detached and
  returns `200` as soon as the child starts. **The response confirms the
  run started; it never reports a verdict.** It needs the same server-side
  `PGAI_CONTAINER_TIER=1` authorization as `deep-verify`, and takes the
  same optional `image` field — a list of names declared in
  `team/config/container-images.txt`, or `"all"` — passed as one
  `--image <entry>` each and handed on to `deep-verify.sh` unchanged. An
  undeclared name is refused `422` with `container_tier.unknown_image`
  before anything is spawned, `dry_run` included. Omitting the field falls
  back to `kanban.cfg [release] container_images`; only when neither
  selects an image does the run list the images and build nothing. The
  field never grants the authorization. A skipped or empty run exits 0 and
  writes no record. Poll `GET /deep-verify-status` or `GET /verdicts` once
  the run completes; a refusal the script raises after dispatch (an
  unregistered project, say) lands in the captured log file the response
  names and in the project's ledger, not in the response. `422` when
  `project` is missing, or when `image` names an undeclared image.
- **`cancel-rc`** — Confirmation and `force` are separate, as on
  `unwind-rc`; here the confirmation field is `yes`, after the CLI's
  `--yes`. The route calls `pgai_agent_kanban.cm.cancel.cancel_rc`, the
  function `cm/cancel-rc.sh` reaches, in process.

  `yes` answers the prompt. The CLI asks `Proceed with cancellation of
  <branch>? [y/N]` unless `--yes` is given, and an HTTP call cannot answer
  a prompt, so `yes: true` is required whenever `dry_run` is not `true`. A
  request without it returns `422` (`cancel_rc.confirmation_required`)
  before anything runs.
  `dry_run` previews without `yes`.

  `force: true` skips exactly one check: that `key` matches the project's
  `## Active RC`. With it, the named RC's branch and per-RC record are
  cleaned up and `release-state.md`, which then names a different RC, is
  left unchanged. `force` is never a confirmation: `force` without `yes`
  is refused `cancel_rc.confirmation_required` like any other unconfirmed
  call. The CLI behaves the same way — `--force` still prompts unless
  `--yes` is also given.

  The function's other checks (version format, current branch, the
  idempotent already-cancelled no-op) propagate unchanged. They refuse with
  plain stderr lines, so a refusal returns `500` with that text as
  `message` under the stable key `cancel_rc.refused` (see "Refusals on
  every action route" below).
- **`release-locks`** — The verb's mutating `--release` mode; the
  read-only listing is `GET /release-locks`. `release` is required (a
  POST that names no lock has nothing to release — `422`,
  `release_locks.release_required`). The route calls
  `ops.locks.release_lock`, the function the script reaches, so name
  resolution and the refusals reach the caller with the same machine key
  and runnable remedy the CLI prints: `release_locks.not_found` (exit 3),
  `release_locks.ambiguous` (exit 2), and `release_locks.holder_alive`
  (exit 4, naming the live holder's `holder`, `pid`, `op` and `task`; the
  lock is left in place; cleared by `force: true`). With
  `force: true` the live holder is signalled rather than its file
  replaced, and the call waits up to `kill_grace_seconds` for it to let go;
  a holder that
  does not is `release_locks.holder_not_released` (exit 5), reaching the
  caller the same way. Releasing a lock that is already gone succeeds and
  says so, as on the CLI.

- **`halt-after`** — Takes the CLI's own parameter set, minimal form
  included: `{"project": "my-app", "until": "v2.30.0"}` arms the same
  sentinel `halt-after.sh --project my-app --until v2.30.0` arms, and
  `{"project": "my-app"}` alone arms the current-RC drain. `key` is
  optional and defaults to `rc`: when the body omits it, no `--key` flag
  is passed and the script applies its own default, so the default is
  defined once, in the CLI, and never duplicated at the API layer. `until`
  (optional) arms the versioned `rc:vX.Y.Z` token directly, the same token
  the CLI's `--until` writes; it binds to the `rc` scope only. Its format
  is deliberately not checked at the API layer: a malformed value is
  forwarded verbatim so `halt-after.sh`'s own refusals reach the caller
  with the machine key the CLI prints — `halt-after.until_malformed` for a
  value that is not `vX.Y.Z`, `halt-after.until_scope_mismatch` for
  `until` combined with one of the five role tokens — lifted to top-level
  keys as described next. A body with no `project` is refused at the API
  layer with the same three keys (`halt_after.project_required`, `422`)
  before any subprocess runs.

- **`archive`** — The REST twin of `archive.sh`. The route calls
  `pgai_agent_kanban.archive.run.run_archive`, the function the script
  reaches, in process; no confirmation is asked for, because nothing is
  deleted, every move is recorded in the archive ledger, and any archived
  item can be restored. The body selects what the call does:

  - **Archive run** (no `dry_run`, `restore` or `reconcile_queues`) — moves the finished
    items older than the newest `count` per category (bugs, priorities,
    requirements, tasks) into `archive/<project>/`, with the queue lines
    and training logs that follow them, under the project's repo lock. A
    second call moves nothing. `count` defaults to
    `[archive] archive_count`; `0` moves nothing.
  - **`dry_run: true`** — returns the plan per category in `stdout`, the
    same text `archive.sh --dry-run` prints, and moves nothing. Unlike
    most routes, an archive dry run is the plan itself, not a description
    of the CLI call.
  - **`restore: "<ID>"`** — moves one archived item (a bug or priority ID,
    a requirements file name, or a task ID) back live, with its queue lines
    and training logs.
  - **`reconcile_queues: true`** — instead of an archive run, moves each
    live queue line that names an archived item, or a finished (`[x]`) task
    whose folder is in neither the live project nor the archive, into the
    archive's queue file of the same name. With `dry_run` it lists those
    lines and moves nothing.

  Refusals and outcomes reach the caller with the CLI's own machine keys:

  | Key | Status | When |
  |---|---|---|
  | `archive.project_required` | `422` | The body has no `project`; nothing runs. |
  | `archive.project_invalid`, `archive.restore_invalid` | `422` | `project` or `restore` is not one plain name; nothing runs. |
  | `archive.invalid_count` | `422` (exit 1) | `count` is not decimal digits. |
  | `archive.conflicting_options` | `422` (exit 1) | `restore` with `dry_run`, `count` or `reconcile_queues`, or `reconcile_queues` with `count`. |
  | `archive.project_missing` | `404` (exit 1) | The project has no directory to archive from. |
  | `archive.restore_not_archived` | `500` (exit 1) | `restore` names an item the archive does not hold. |
  | `archive.restore_live_exists` | `500` (exit 1) | `restore` names an item that already exists live. |
  | `archive.run_failed` | `500` (exit 2) | The run stopped at a failing item; items moved before it are in the ledger. |
  | `archive.repo_lock_busy` | `500` (exit 3) | Another process holds the project's repo lock; nothing moved. |

  An unknown or ambiguous `project` is refused with the shared resolver's
  `project_resolve.not_found` or `project_resolve.ambiguous` (exit 1).
  The worked example is in [Section 7](#archive-example-dry-run-first-then-real);
  `docs/OPERATIONS.md` describes when the release itself archives.

**Refusals on every action route carry `error`/`message`/`remedy` as JSON.**
Every `POST /operations/*` route returns the standard envelope on success
and, on a refused call, the same envelope extended with the script's
dual-channel triple lifted out of `stderr` as top-level keys:

```json
{"exit_code": 3, "stdout": "", "stderr": "error:   release_locks.not_found\n...",
 "error": "release_locks.not_found",
 "message": "release-locks.sh: no lock matches 'nope' under /srv/kanban/locks (...)",
 "remedy": "Run team/scripts/release-locks.sh with no flags to list every lock by name, then re-run with the full name or an unambiguous prefix.",
 "warnings": []}
```

The three keys are always what the script printed, only re-shaped: when the
script emitted the `error:`/`message:`/`remedy:` block they are lifted
verbatim; when it refused with plain text (as `cm/cancel-rc.sh` does) that
text becomes `message` under a stable per-route fallback key of the form
`<verb>.refused` (`halt.refused`, `cancel_rc.refused`, `unwind_rc.refused`,
and so on for every action verb) with a runnable CLI remedy. `stdout` and
`stderr` are still returned in full.

An API-layer refusal — a missing required field on any route, `reset`'s
selector checks, `remove-project`'s and `unwind-rc`'s `confirm` gates,
`ship-rc`'s `confirm` gate, `cancel-rc`'s confirmation gate, `intake`'s
bare-filename refusal (`intake.filename_not_bare`) — uses the same
three keys at `422`
(plus `warnings`; no `exit_code`/`stdout`/`stderr`, because no process
ran), so a client parses one refusal shape whichever layer refused. A
missing required field is never reported as the framework's
`{"detail": [...]}` validation list: each route checks its own required
fields before spawning anything and refuses under a stable key naming the
field, `<verb>.<field>_required` (`halt.project_required`,
`close.key_required`, `release_locks.release_required`), with a remedy that
is a runnable `curl` line. This is why the request body models declare
every field optional in the OpenAPI schema, and why the generated route
reference below marks no `POST` body field as required — a body missing a
field must reach the route to be refused in this shape, so "required" for
a `POST` route is what the hand-written table above states and the route
enforces, not what the schema declares. The HTTP status convention is the
one under [The envelope](#the-envelope): `200` on `exit_code` 0, `422` for
an API-layer refusal, and on a non-zero exit the status the refusal's kind
maps to — `422`, `404` or `409` — with `500` for a refusal that names none
of them. The operator verb census names this shape
`DualChannelShellEnvelope` and declares it on every action row.

### The verb census — source of truth for the verb set

The operator verb set is declared in one place: the operator verb census,
`team/config/operator_verbs.manifest`. One row per verb carries the script
that implements it, its declared flags, whether it mutates state, its
read|action class, its REST method and route, the request body model the
route takes, and the response schema its twin returns. Everything else that
claims to know the verb set derives from that row rather than restating it:
the generated verb reference in `docs/operator-commands.md`, the generated
route table below, the reconciliation check that holds each script's own
flag array to its row (`team/scripts/lint_operator_verb_manifest.py`), and
the parity gate.

**A verb without a census row fails the parity gate.** The gate,
`team/scripts/lint_api_parity.py`, walks the census against the tree on
every gated run and refuses — a blocking stop, not a quality finding —
on any of these findings, each naming the verb:

- an operator script in the tree has no census row;
- a census row names a script that does not exist;
- a route in the routers has no census row;
- a route's response does not match the schema its row declares;
- an active row declares no REST twin at all (only a `planned` row may);
- an action-class row has no case in the action-parity ledger
  (`team/tests/fixtures/action_parity_cases.py`);
- a read-class row has no golden-pair case in the read-parity ledger
  (`team/tests/fixtures/read_parity_cases.py`).

Adding a verb therefore *requires* the census row, and the row is what
registers the verb everywhere at once: in both generated references, in the
gate's walk, and — because the routers are reconciled against it — in the
API. There is no second list to forget.

**Two kinds of verb, two kinds of proof.** A `read` verb's twin must return
the same resolved data the CLI renders — one shared resolver, two renderers
(text and JSON), never two derivations. Golden-pair fixtures hold that
property: each runs the real CLI script and the real REST server, over the
same seeded board, and asserts the two JSON bodies agree modulo the
envelope (`GET /verdicts` with and without `details`, `GET /show-queues`,
`GET /metrics?format=json`, `GET /agent-metrics`). The read-parity ledger
inventories every read verb's pair — `GET /show` and `GET /verify-rc-state`
compared to the CLI's stdout byte-for-byte, `GET /release-state` and
`GET /deep-verify-status` held with their CLI to the one resolver both
call — so a read verb without a golden pair fails the gate. An `action` verb's twin
must accept the CLI's own parameters and enforce the same refusals
dual-channel. The action-parity ledger holds that: one case per action verb,
asserting either that the REST call changed the board exactly as the CLI
did, or that a malformed parameter was refused with the same `error`,
`message` and `remedy`.

**What is deliberately outside the census.** The census is the operator
*verb* set, not every script under `scripts/`. A script outside it is listed,
with its reason, in `team/config/operator_verb_census_exempt.txt`: the
read-only convenience endpoints the API also serves (`GET /status`,
`GET /costs`, `GET /test-report`, `GET /rejected`, the task-README read), the
second route of an already-censused verb (`GET /release-locks`, the listing
mode), the thin report script behind `GET /verdicts`, and CLI-only tools with
no twin — `export-project.sh` and `import-project.sh` (see
[Section 8](#8-future-work)) among them. The sidecar is the only way to stand
outside the census without failing the gate, and every entry names its
reason.

### Launchers — verbs whose route starts a process by design

The **Implementation** column of the generated reference below reads
`module:<function>` when a verb's route and CLI script share one Python
function, and `shell` when the route still runs the script. A `shell` row
that is not a launcher is a verb not yet moved into a module; the census
holds none today. A **launcher** is different:
its whole job is to start a process or a lane, so there is no in-process
function for the route and the script to share. A launcher stays
shell-backed by design, and it is not work left to do.

The launchers are declared, each with its reason, in
`team/config/api-route-impl-launchers.txt`:

| Verb | Why it is a launcher |
|---|---|
| `wake-now` | Fires `wake-batch.sh` for one agent or every pipeline agent and returns; after validating its flags it only starts the wake process. The work is the fired agent's wake cycle, not anything `wake-now` does in-process. |
| `cold-build-now` | Validates `--project` and execs `deep-verify.sh --project NAME`; it owns no state change of its own. Its route starts that lane detached and returns before it finishes. What the lane does is `deep-verify`'s row. |
| `deep-verify` | Starts the operator-only cold-build lane (`run-cold-build-tests.sh`), which builds images and runs containers for many minutes. Its route starts `deep-verify.sh` detached and returns before the lane finishes, and no in-process function can run the lane without starting a process. |

`deep-verify` is a launcher because starting the lane is the only work
its script does itself. Every decision around the launch is a
`pgai_agent_kanban.deep_verify` function the script reaches through
`pp_run_ops`: the scheduled due-check (`schedule`), the ledger read and
write (`ledger`), verdict classification (`classify`) and failure-path bug
filing (`bug_filer`). The CLI and the route share those functions through
the script; neither re-implements them.

`team/scripts/lint_api_route_impl.py` reads the launcher file on every run.
It prints each launcher with its reason and counts launchers apart from
the `shell` rows still to port. It reports four problems: a launcher with
no reason (that verb stays counted as a row to port), a verb the census
does not declare, a verb whose implementation is not `shell`, and a
duplicate line.

### Census-derived route reference (generated)

The table below is generated by `team/scripts/generate_operator_verb_docs.py`
from every active row of the operator verb census
(`team/config/operator_verbs.manifest`, read through its sanctioned Python
reader `team/pgai_agent_kanban/lib/operator_verb_manifest.py`). The verb, its
read|action class, REST method and route, request body model and response
schema reference are the census row itself; the parameter list is resolved
for that route from the checked-in ICD (`docs/api/icd.json`) -- query and
path parameters for a GET route, the body model's fields for a POST route,
required ones marked. Adding a census row and regenerating is the only edit a
new route needs to appear here. Everything between the
`OPERATOR-VERBS-GENERATED` section marker pair is overwritten on every
render; run the generator and commit the result rather than hand-editing it.
`python3 team/scripts/generate_operator_verb_docs.py --check` reports drift
without writing, and the gated runners run that check as a doc-truth
pre-flight, so a census row edited without regenerating is reported. A
**Body model** of `none` means the route takes no flag-parity JSON body
(query parameters, or a body accepted and ignored); the hand-written tables
above carry the per-route notes the census does not.

<!-- OPERATOR-VERBS-GENERATED:BEGIN section=api-reference -->
Generated from every active row of `team/config/operator_verbs.manifest` (parameters resolved from `docs/api/icd.json`). Do not hand-edit between these markers; run `python3 team/scripts/generate_operator_verb_docs.py` (from the repo root) and commit the result instead.

| Verb | Class | Method | Route | Parameters | Body model | Response schema | Script | Implementation |
|---|---|---|---|---|---|---|---|---|
| `halt` | action | `POST` | `/operations/halt` | `dry_run`, `project` | `HaltBody` | `DualChannelShellEnvelope` | `scripts/halt.sh` | `module:pgai_agent_kanban.ops.write.halt` |
| `unhalt` | action | `POST` | `/operations/unhalt` | `dry_run`, `project` | `HaltBody` | `DualChannelShellEnvelope` | `scripts/unhalt.sh` | `module:pgai_agent_kanban.ops.write.unhalt` |
| `halt-global` | action | `POST` | `/operations/halt-global` | `dry_run` | none | `DualChannelShellEnvelope` | `scripts/halt-global.sh` | `module:pgai_agent_kanban.ops.write.halt_global` |
| `unhalt-global` | action | `POST` | `/operations/unhalt-global` | `dry_run` | none | `DualChannelShellEnvelope` | `scripts/unhalt-global.sh` | `module:pgai_agent_kanban.ops.write.unhalt_global` |
| `halt-after` | action | `POST` | `/operations/halt-after` | `dry_run`, `key`, `project`, `until` | `HaltAfterBody` | `DualChannelShellEnvelope` | `scripts/halt-after.sh` | `module:pgai_agent_kanban.ops.write.halt_after` |
| `defer` | action | `POST` | `/operations/defer` | `dry_run`, `project`, `until` | `DeferBody` | `DualChannelShellEnvelope` | `scripts/defer.sh` | `module:pgai_agent_kanban.ops.write.defer` |
| `undefer` | action | `POST` | `/operations/undefer` | `dry_run`, `project` | `UndeferBody` | `DualChannelShellEnvelope` | `scripts/undefer.sh` | `module:pgai_agent_kanban.ops.write.undefer` |
| `deep-verify` | action | `POST` | `/operations/deep-verify` | `dry_run`, `image`, `project`, `scheduled` | `DeepVerifyBody` | `DualChannelShellEnvelope` | `scripts/deep-verify.sh` | `shell` |
| `cold-build-now` | action | `POST` | `/operations/cold-build-now` | `dry_run`, `image`, `project` | `ColdBuildNowBody` | `DualChannelShellEnvelope` | `scripts/cold-build-now.sh` | `shell` |
| `wake-now` | action | `POST` | `/operations/wake-now` | `agent`, `all`, `bg`, `dry_run` | `WakeNowBody` | `DualChannelShellEnvelope` | `scripts/wake-now.sh` | `shell` |
| `show` | read | `GET` | `/show` | `project` (required), `key` (required), `file` | none | `ShellEnvelope` | `scripts/show.sh` | `module:pgai_agent_kanban.ops.read.get_task_status` |
| `show-queues` | read | `GET` | `/show-queues` | `project`, `details` | none | `ShellEnvelope` | `scripts/dashboard/show-queues.sh` | `module:pgai_agent_kanban.ops.read.get_queues` |
| `why` | read | `GET` | `/verdicts` | `project`, `details` | none | `VerdictsResponse` | `scripts/why.sh` | `module:pgai_agent_kanban.ops.verdict.resolve_verdicts` |
| `release-locks` | action | `POST` | `/operations/release-locks` | `dry_run`, `force`, `release` | `ReleaseLocksBody` | `DualChannelShellEnvelope` | `scripts/release-locks.sh` | `module:pgai_agent_kanban.ops.locks.release_lock` |
| `unwind-rc` | action | `POST` | `/operations/unwind-rc` | `confirm`, `dry_run`, `force`, `key`, `project` | `UnwindRcBody` | `DualChannelShellEnvelope` | `scripts/unwind-rc.sh` | `module:pgai_agent_kanban.cm.unwind.unwind_rc` |
| `verify-rc-state` | read | `GET` | `/verify-rc-state` | `project` (required), `verbose` | none | `ShellEnvelope` | `scripts/verify-rc-state.sh` | `module:pgai_agent_kanban.ops.rc_state.verify_rc_state` |
| `reset` | action | `POST` | `/operations/reset` | `agent`, `bug`, `dry_run`, `force`, `keep_artifacts`, `key`, `priority`, `project`, `requirement` | `ResetBody` | `DualChannelShellEnvelope` | `scripts/reset.sh` | `module:pgai_agent_kanban.ops.write.reset_item` |
| `close` | action | `POST` | `/operations/close` | `dry_run`, `key`, `note`, `project`, `state` | `CloseBody` | `DualChannelShellEnvelope` | `scripts/close.sh` | `module:pgai_agent_kanban.ops.write.close_item` |
| `wontdo` | action | `POST` | `/operations/wontdo` | `dry_run`, `key`, `project` | `WontdoBody` | `DualChannelShellEnvelope` | `scripts/wontdo.sh` | `module:pgai_agent_kanban.ops.write.wontdo_item` |
| `delete` | action | `POST` | `/operations/delete` | `dry_run`, `force`, `key`, `project` | `DeleteBody` | `DualChannelShellEnvelope` | `scripts/delete.sh` | `module:pgai_agent_kanban.ops.write.delete_item` |
| `intake` | action | `POST` | `/operations/intake` | `content`, `dry_run`, `filename`, `project` | `IntakeBody` | `DualChannelShellEnvelope` | `scripts/intake.sh` | `module:pgai_agent_kanban.ops.write.deposit_intake` |
| `set-version-ceiling` | action | `POST` | `/operations/set-version-ceiling` | `dry_run`, `major`, `minor`, `no_major`, `no_minor`, `project`, `show` | `SetVersionCeilingBody` | `DualChannelShellEnvelope` | `scripts/set-version-ceiling.sh` | `module:pgai_agent_kanban.ops.write.set_version_ceiling` |
| `switch-provider` | action | `POST` | `/operations/switch-provider` | `dry_run`, `provider` | `SwitchProviderBody` | `DualChannelShellEnvelope` | `scripts/switch-provider.sh` | `module:pgai_agent_kanban.ops.write.switch_provider` |
| `create-project` | action | `POST` | `/operations/create-project` | `color`, `dev_tree`, `dry_run`, `git_remote`, `git_repo`, `max_major`, `max_minor`, `max_patch`, `no_migrate`, `priority`, `project`, `workflow_type` | `CreateProjectBody` | `DualChannelShellEnvelope` | `scripts/create-project.sh` | `module:pgai_agent_kanban.ops.projects.create_project` |
| `add-project` | action | `POST` | `/operations/add-project` | `color`, `dry_run`, `no_migrate`, `priority`, `project` | `AddProjectBody` | `DualChannelShellEnvelope` | `scripts/add-project.sh` | `module:pgai_agent_kanban.ops.projects.add_project` |
| `remove-project` | action | `POST` | `/operations/remove-project` | `confirm`, `dry_run`, `force`, `project` | `RemoveProjectBody` | `DualChannelShellEnvelope` | `scripts/remove-project.sh` | `module:pgai_agent_kanban.ops.projects.remove_project` |
| `ship-rc` | action | `POST` | `/operations/ship-rc` | `confirm`, `dry_run`, `key`, `project` | `ShipRcBody` | `DualChannelShellEnvelope` | `scripts/ship-rc.sh` | `module:pgai_agent_kanban.cm.ship.ship_rc` |
| `rc-metrics` | read | `GET` | `/metrics` | `project`, `last`, `per_agent`, `format` | none | `ShellEnvelope` | `scripts/dashboard/show-metrics.sh` | `module:pgai_agent_kanban.lib.metrics.show_metrics` |
| `agent-metrics` | read | `GET` | `/agent-metrics` | `project` (required), `window`, `last`, `all`, `since` | none | `AgentMetricsResponse` | `scripts/dashboard/agent-metrics.sh` | `module:pgai_agent_kanban.lib.metrics.compute_agent_averages` |
| `cancel-rc` | action | `POST` | `/operations/cancel-rc` | `dry_run`, `force`, `key`, `project`, `yes` | `CancelRcBody` | `DualChannelShellEnvelope` | `scripts/cm/cancel-rc.sh` | `module:pgai_agent_kanban.cm.cancel.cancel_rc` |
| `release-state` | read | `GET` | `/release-state` | `project` (required) | none | `ReleaseStateResponse` | `scripts/release-state.sh` | `module:pgai_agent_kanban.ops.read.get_release_state` |
| `deep-verify-status` | read | `GET` | `/deep-verify-status` | `project` (required) | none | `DeepVerifyStatusResponse` | `scripts/deep-verify-status.sh` | `module:pgai_agent_kanban.ops.read.get_deep_verify_status` |
| `archive` | action | `POST` | `/operations/archive` | `count`, `dry_run`, `project`, `reconcile_queues`, `restore` | `ArchiveBody` | `DualChannelShellEnvelope` | `scripts/archive.sh` | `module:pgai_agent_kanban.archive.run.run_archive` |

Every active census verb has a REST twin.
<!-- OPERATOR-VERBS-GENERATED:END section=api-reference -->

---

## 6. Universal contracts: `dry_run` and `warnings`

Two contracts apply to every endpoint the API exposes. They are described
here once so the endpoint table above does not have to restate them
per-row.

### 6.1 `dry_run` — no-mutation preview on every operation

Every `POST /operations/*` route accepts an optional `dry_run: bool`
body field. The contract has four properties:

- **Every action verb in the census honours it.** The endpoint table in
  Section 5 and `team/config/operator_verbs.manifest` are the authority
  on which routes exist; every one of their operation body models
  carries `dry_run` with default `false`. A parity lint fails the RC if
  any operation is added without it, so a caller that supplies `dry_run:
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
  "kanban_root": "/home/<user>/pgai_agent_kanban",
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
  "kanban_root": "/home/<user>/pgai_agent_kanban",
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

The safety-critical case is the one the contract exists to prevent: an
operator sends `{"project": "p", "dry_run": true}` to
`/operations/halt`, the API silently discards `dry_run`, and the real
halt runs. That case is eliminated **structurally**. The dry_run
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

# The queue-state pane, per-agent and per-state
curl -sS "http://127.0.0.1:8300/show-queues?project=my-app&details=true"

# RC-state consistency check, every check printed
curl -sS "http://127.0.0.1:8300/verify-rc-state?project=my-app&verbose=true"

# Every advisory lock with its provenance (listing only — releasing is POST /operations/release-locks, below)
curl -sS "http://127.0.0.1:8300/release-locks"

# One project's verdict plus the --details pane (posture, locks, queue tails) as JSON
curl -sS "http://127.0.0.1:8300/verdicts?project=my-app&details=true"

# Per-RC metrics as a JSON array (the text renderer's Blocked column is blocked_seconds here)
curl -sS "http://127.0.0.1:8300/metrics?project=my-app&format=json"

# Per-agent averages over the newest 10 RC rollups
curl -sS "http://127.0.0.1:8300/agent-metrics?project=my-app&window=10"

# One task's README.md (the key may be an unambiguous prefix of the task ID)
curl -sS "http://127.0.0.1:8300/projects/my-app/tasks/CODER-20260412-001-add-login-endpoint/readme"  # example task ID
```

Structured reads, which return JSON rather than the text envelope (except
`/logs/{kind}` and `/traces/{trace_id}`, which carry their text in the
envelope's `stdout`):

```bash
# The whole board: every column across every registered project
curl -sS "http://127.0.0.1:8300/board"

# One project's registry entry, ceilings, release state and queue counts
curl -sS "http://127.0.0.1:8300/projects/my-app"

# The last 100 lines of the coder's cron wake log
curl -sS "http://127.0.0.1:8300/logs/wake?agent=coder&tail=100"

# One project's agent log (kinds and their required params: Section 5)
curl -sS "http://127.0.0.1:8300/logs/agent?project=my-app&agent=coder"

# The five newest training traces for one project and agent
curl -sS "http://127.0.0.1:8300/traces?project=my-app&agent=coder&limit=5"

# One trace, by the opaque id GET /traces returned: here the newest one's
TRACE_ID=$(curl -sS "http://127.0.0.1:8300/traces?limit=1" | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["id"])')
curl -sS "http://127.0.0.1:8300/traces/$TRACE_ID"

# The project's release-state.md fields, validated
curl -sS "http://127.0.0.1:8300/release-state?project=my-app"

# The project's DEEP-VERIFY cadence and ledger (reads only; running it is POST /operations/deep-verify)
curl -sS "http://127.0.0.1:8300/deep-verify-status?project=my-app"
```

### Halt-family example: halt a project, then unhalt

```bash
# Halt (per-project)
curl -sS -X POST http://127.0.0.1:8300/operations/halt \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app"}'

# Arm HALT-AFTER (soft drain until the current RC ships; key is optional and defaults to rc, as on the CLI)
curl -sS -X POST http://127.0.0.1:8300/operations/halt-after \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app"}'

# Arm HALT-AFTER until a named release ships -- the same sentinel `halt-after.sh --project my-app --until v2.30.0` arms
curl -sS -X POST http://127.0.0.1:8300/operations/halt-after \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "until": "v2.30.0"}'

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

`filename` must be a bare file name — no path separator, no `..`, not
absolute — matching one of the intake routing prefixes (`BUG-*`,
`PRIORITY-*`, or `v[0-9]*.md`). The API writes the content to a temp file
in a fresh per-request directory under `${PGAI_AGENT_KANBAN_TEMP_DIR}/intake/`,
then deposits it with the same routing `intake.sh` uses.

```bash
curl -sS -X POST http://127.0.0.1:8300/operations/intake \
    -H 'Content-Type: application/json' \
    -d '{
          "project": "my-app",
          "filename": "BUG-0501-20260704-widget-crash.md",
          "content": "# BUG-0501-20260704-widget-crash\n\nWidget crashes on load.\n"
        }'
```

Any other filename is refused before anything is written. A path such as
`../notes.md` or `/tmp/x.md` returns `422` with
`"error": "intake.filename_not_bare"` and a `remedy` that re-sends the
request with a bare name. A bare name that matches no routing prefix
returns `409` with `"error": "intake.refused"`, the deposit's
`cannot route` text in `message`, and a `remedy` that re-sends the request
under a routable example name. The `intake` note under
[Mutations (POST)](#mutations-post) carries the full rule.

Once a file is deposited, the pipeline's own validation and quarantine
rules apply — a file the pipeline keeps rejecting for a malformed filename
or mismatched internal heading is quarantined into the project's
`rejected/` directory, `projects/<name>/rejected/`, with a `.reason`
sidecar, exactly as with a direct `intake.sh` call.

### Unwind-RC example: dry-run first, then real

```bash
# Prerequisite: the project must be HALTed (per-project or global).

# Dry-run: the API describes the CLI call a live request is equivalent to
# and runs nothing. No confirm is needed for a preview.
curl -sS -X POST http://127.0.0.1:8300/operations/unwind-rc \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "v1.2.0", "dry_run": true}'

# Real unwind. confirm: true answers the CLI's prompt; it bypasses no check.
curl -sS -X POST http://127.0.0.1:8300/operations/unwind-rc \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "v1.2.0", "confirm": true}'

# Recovery only: the Active RC in release-state.md no longer matches key.
# force skips that one check and nothing else -- confirm is still required.
curl -sS -X POST http://127.0.0.1:8300/operations/unwind-rc \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "v1.2.0", "confirm": true, "force": true}'
```

A live call whose `confirm` is missing, `false` or empty is refused HTTP 422
`unwind_rc.confirm_required` before anything runs, with or without `force`;
a string other than the typed phrase `unwind-rc my-app v1.2.0` is refused
`unwind_rc.confirm_mismatch`. The `remedy` is a runnable `curl` line
carrying `"confirm": true`. If HALT is not set, the unwind refuses with a
guard message in `stderr` and a non-zero `exit_code`; the API returns HTTP
500 with the envelope extended by the top-level `error`/`message`/`remedy`
keys every action route carries on a refusal (Section 5). Set the HALT
first (`POST /operations/halt` or `POST /operations/halt-global`) and
retry.

### Archive example: dry-run first, then real

```bash
# Dry-run: returns the plan per category in stdout and moves nothing.
curl -sS -X POST http://127.0.0.1:8300/operations/archive \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "dry_run": true}'

# Real run: moves the finished items the plan named into archive/my-app/.
curl -sS -X POST http://127.0.0.1:8300/operations/archive \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app"}'
```

Run it when no wake chain is working the project: an archive that finds
the project's repo lock held by another process moves nothing and returns
`500` with `archive.repo_lock_busy`. The `archive` note under
[Mutations (POST)](#mutations-post) lists every refusal.

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

# Arm the deferral campaign until v2.20.1 ships
curl -sS -X POST http://127.0.0.1:8300/operations/defer \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "until": "v2.20.1"}'

# Clear it early
curl -sS -X POST http://127.0.0.1:8300/operations/undefer \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app"}'

# Start the cold-build lane (returns as soon as it STARTED — no verdict yet)
curl -sS -X POST http://127.0.0.1:8300/operations/deep-verify \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app"}'

# Fire one agent's wake cycle in the background (recommended form over HTTP)
curl -sS -X POST http://127.0.0.1:8300/operations/wake-now \
    -H 'Content-Type: application/json' \
    -d '{"agent": "coder", "bg": true}'

# Run the cold-build lane under its narrow name (backgrounded; no verdict in the response)
curl -sS -X POST http://127.0.0.1:8300/operations/cold-build-now \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app"}'

# Cancel an in-flight RC: preview first, then confirm with "yes": true
curl -sS -X POST http://127.0.0.1:8300/operations/cancel-rc \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "v1.5.0", "dry_run": true}'
curl -sS -X POST http://127.0.0.1:8300/operations/cancel-rc \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "v1.5.0", "yes": true}'

# Recovery only: release-state.md names a different RC. force skips the
# Active-RC match check and nothing else -- yes is still required
curl -sS -X POST http://127.0.0.1:8300/operations/cancel-rc \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "key": "v1.5.0", "yes": true, "force": true}'

# Release a wedged lock (add "force": true to override a live holder)
curl -sS -X POST http://127.0.0.1:8300/operations/release-locks \
    -H 'Content-Type: application/json' \
    -d '{"release": "repo-wake-my-app"}'

# Show the version ceilings in effect, then raise the minor ceiling to 20
curl -sS -X POST http://127.0.0.1:8300/operations/set-version-ceiling \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "show": true}'
curl -sS -X POST http://127.0.0.1:8300/operations/set-version-ceiling \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app", "minor": 20, "show": true}'

# Preview switching the active provider (drop dry_run to switch; the
# provider's CLI must be installed on the server's PATH)
curl -sS -X POST http://127.0.0.1:8300/operations/switch-provider \
    -H 'Content-Type: application/json' \
    -d '{"provider": "codex", "dry_run": true}'

# Preview, then create, a new release-workflow project
curl -sS -X POST http://127.0.0.1:8300/operations/create-project \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-new-app", "workflow_type": "release", "dry_run": true}'
curl -sS -X POST http://127.0.0.1:8300/operations/create-project \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-new-app", "workflow_type": "release"}'

# Register a project directory that already exists under projects/
# (copied from another machine, say) but is not in projects.cfg
curl -sS -X POST http://127.0.0.1:8300/operations/add-project \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-migrated-app", "priority": 2}'
```

---

## 8. Future work

The following are deliberately out of scope today and are
documented here so operators know not to look for them.

### Authentication

There is no HTTP-layer auth. Any caller that reaches the loopback socket
with a loopback `Host` header — and, for a mutating request, a
`Content-Type: application/json` body — can call every endpoint; the bind,
the `Host` allow-list, and the JSON-only rule are the whole of the access
model (see [Section 3](#3-access-model)). This is why loopback binding is
not optional. A future release will add an auth layer
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

There is no endpoint that runs discovery, promotes a task, or advances the
RC pipeline on its own. Cron remains the sole scheduler.
`POST /operations/wake-now` fires one wake cycle exactly as the CLI's
`wake-now` verb does — an operator action with an operator's consequences,
not a scheduler — and beyond that the API mirrors what an operator can do
at a shell: manage inputs, read state, mutate task state.

### Remaining shell-backed verbs

A census verb whose **Implementation** still reads `shell`, and which is
not a launcher, runs its script from the route. Each is a candidate for
the same move the other verbs have made: one Python function, called by
the route and reached by the CLI script. The generated route reference in
[Section 5](#5-endpoint-table) is the current list. Read it alongside the
launcher table
([Launchers](#launchers--verbs-whose-route-starts-a-process-by-design)):
a launcher's `shell` is by design, not work left to do.

### export-project and import-project — the one intentional non-parity

`export-project.sh` and `import-project.sh` are not exposed over HTTP,
and this is the one intentional non-parity in the API. Both scripts are
file-path-centric: they write a tar archive to a caller-chosen path on
disk, or read one back from a caller-chosen path. Those semantics do not
translate to a body-content API cleanly — the API has no concept of a
filesystem path the caller can reach, and streaming multi-gigabyte
archives through a JSON envelope is not the right shape. Every other
operator verb has an HTTP counterpart; these two are deferred until a
remote-operator story exists that motivates the transport question. Both
are listed, with this reason, in the census-exempt sidecar
`team/config/operator_verb_census_exempt.txt` — which is how the parity gate
knows to skip them without a census row.

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

A one-line file containing the current ICD version (for example
`1.1.0`). This is the single source of truth: the generator stamps
`info.version` in `icd.json` from it, and the running service serves
the same value on `/openapi.json`. The kanban release version and the
ICD version are independent — the ICD version only moves when the API
surface moves.

### The changelog

`docs/api/ICD_CHANGELOG.md` is the human-readable companion to the
artifact: one entry per contract version, newest first, naming every route
the version added and every additive change to a route that already
existed. It is append-only — an entry, once published, is never rewritten —
and each entry lands with the bump it describes. When `info.version` moves,
that entry is a consumer's work list; the baseline diff is the proof.

The `1.0.0` to `1.1.0` bump shows the additive-minor path:
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
freezes a new baseline for the new version. The freshness and
compatibility gates enforce it: a surface change without a correct
version bump fails the gated test suites. A release bumps the contract
at most once, however many routes it adds, and a published number never
moves again — the baseline file for a version that has shipped is
immutable, only the bump creates a new one, and the generator refuses to
rewrite a published one (see "Regenerating the artifact" below).

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

**A published baseline is never re-frozen.** Regeneration decides what
to do about `docs/api/baselines/icd-v<ICD_VERSION>.json` from two facts:
whether that file exists and matches the new artifact, and whether its
version is listed in `docs/api/baselines/SUPPORTED` — the manifest of
versions published to clients. Only the version the current bump
introduces is ever frozen; every older baseline is left byte-identical on
every run.

- **No baseline for `ICD_VERSION` yet, and it is newer than every
  existing baseline** — this regeneration is the bump. The new baseline
  is created from the freshly written `icd.json`. If an existing baseline
  is already at or above `ICD_VERSION`, none is created and a warning
  names it: check `docs/api/ICD_VERSION`.
- **Baseline present and byte-identical to the artifact** — nothing to
  do.
- **Baseline present, differs, version not yet in `SUPPORTED`** — frozen
  by a bump but not published, so the in-flight baseline is refreshed to
  the new bytes.
- **Baseline present, differs, version listed in `SUPPORTED`** — the
  baseline is published. The generator refuses dual-channel under the
  stable key `generate_icd.published_baseline_immutable`, leaves the
  baseline untouched, and exits 1. `icd.json` and the package-data
  `ICD_VERSION` copy are written before the refusal, so the artifact is
  fresh; only the published baseline is protected. The `message` names
  the file and the manifest and says the ICD changed without an
  `ICD_VERSION` bump; the `remedy` is the bump step as a runnable line —
  write the next version to `docs/api/ICD_VERSION`, then run
  `bash team/scripts/generate-icd.sh` again.

So regenerating at an unchanged, published `ICD_VERSION` after an API
change refuses rather than silently mutating a contract clients have
pinned on. That refusal is the expected shape of a mid-release run, not a
fault to repair by hand: routes land across a release cycle while
`ICD_VERSION` stays at the published number, each regeneration in that
window refreshes `icd.json` and refuses the baseline, and the bump
resolves it.

**The one bump per release.** The bump is a single deliberate step,
applied once per release however many routes the release added, when
the release publishes the contract:

1. Write the next version to `docs/api/ICD_VERSION` (minor for additive
   changes — see "Version semantics" above).
2. Run `bash team/scripts/generate-icd.sh`. The new baseline is frozen,
   every older baseline is left untouched, and the package-data
   `ICD_VERSION` copy is synced.
3. Add the new version's line to `docs/api/baselines/SUPPORTED`. This is a
   separate, deliberate act: freezing a baseline does not by itself place
   it under the compatibility gate.
4. Add the version's row to the support table below and its entry to
   `docs/api/ICD_CHANGELOG.md`. Whether a bump gets an entry, and what
   the entry for a description-only patch bump says, is set by that
   file's "How this file is maintained" rules.
5. Commit the regenerated artifact, the new baseline, the manifest and
   the changelog together.

The compatibility lint backs the immutability rule at the gated runners:
every baseline whose version is below the current `ICD_VERSION` must be
byte-identical to its copy on the project's prefixed main branch (the last
released tree), and a rewritten published baseline is reported naming the
file. The runners record that finding and continue; the comparison is
skipped with a one-line note where the main branch is absent locally or
does not yet carry the baseline (the version the RC introduces).

`--refreeze` is the explicit override for the refusal case:
`bash team/scripts/generate-icd.sh --refreeze` rewrites the published
`icd-v<ICD_VERSION>.json` to the new artifact, says so loudly on stderr,
and never touches any other version's baseline. Clients pinned on that
version then see a different contract, so it is a deliberate operator act,
never part of the routine flow.

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

The mirror is checked, not assumed: `team/scripts/lint_icd_compat.py`
compares this table with `SUPPORTED` on every gated run. It reports a
version `SUPPORTED` lists with no row here, a row not marked `retired`
whose version `SUPPORTED` does not list, and a `retired` row whose
version `SUPPORTED` still lists, naming the version and both files. The
runners record that drift as a finding and continue.

| Version | Status | Since |
|---|---|---|
| 1.0.0 | supported | v1.16.0 |
| 1.1.0 | supported | v1.19.0 |
| 1.2.0 | supported | v1.23.0 |
| 1.3.0 | supported | v2.0.0 |
| 1.4.0 | supported | v2.15.0 |
| 1.5.0 | supported | v2.19.0 |
| 1.6.0 | supported | v2.20.0 |
| 1.7.0 | supported | v2.20.0 |
| 1.8.0 | supported | v2.25.0 |
| 1.9.0 | supported | v2.25.1 |
| 1.10.0 | supported | v2.26.0 |
| 1.11.0 | supported | v2.26.3 |
| 1.12.0 | supported | v2.26.12 |
| 1.13.0 | supported | v2.28.0 |
| 1.13.1 | supported | v2.28.2 |
| 1.14.0 | supported | v2.28.3 |
| 1.15.0 | supported | v2.30.1 |
| 1.16.0 | supported | v2.31.0 |
| 1.16.1 | supported | v2.31.2 |
| 1.17.0 | supported | v2.32.0 |
| 1.17.1 | supported | v2.32.1 |
| 1.18.0 | supported | v2.35.1 |
| 1.18.1 | supported | v2.35.2 |
| 1.18.2 | supported | v2.37.7 |
| 1.18.3 | supported | v2.37.12 |
| 1.18.4 | supported | v2.37.14 |
| 1.18.5 | supported | v2.38.1 |

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
