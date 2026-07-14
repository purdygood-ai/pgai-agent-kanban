# Public README Cold-Walk

**Cites:** [../../README.md](../../README.md) (the v1.8.0 public README)

## Purpose

Prove that the README a stranger meets — the five-minute demo spine and
the API quickstart — executes verbatim on a fresh clone with no
additional instructions. This checkpoint is the "cold reader" gate: if
these commands do not run as written, the README is wrong.

The verifier follows the checklist top-to-bottom, in order, without
skipping steps and without editing commands. If any step deviates from
what the README says, that is a finding against the README, not against
this checkpoint.

## Scope

- The five-minute demo spine (README section: **Five-minute demo spine**),
  steps 1 through 6, run against a fresh clone on a scratch host.
- The API quickstart (README section: **API quickstart**), three commands,
  run against the server started in the demo spine step chain.
- The envelope shape returned by the operate endpoint matches what the
  README documents.

Not in scope: the full requirements-to-tag release cycle, the tmux
dashboard visual, cost accrual over a full RC, the demo projects'
narrative outputs. Those are separate checkpoints.

## Prerequisites

Before you start:

- A fresh scratch directory the verifier can write to (nothing under it).
  Suggested: `/tmp/pgai-readme-walk/`.
- Git installed and able to reach the repository URL the README's step 1
  substitutes for `<this-repo>`.
- Python 3 with `pip` available.
- No pre-existing install at `$HOME/pgai_agent_kanban/` — the demo spine
  writes there. If one exists, back it up before starting and restore it
  after; the checkpoint is not responsible for save/restore of live
  installs.
- `curl` on PATH.
- Port 8300 free on loopback.

Record the host, the date, and the commit SHA of the repo you cloned in
the outcome section.

## Procedure

Execute each numbered step verbatim. The command text in each `bash`
block matches the README's command text character-for-character; if you
find a divergence, that is a finding.

### 1. Prepare the scratch host

Confirm the prerequisites resolve cleanly:

```bash
mkdir -p /tmp/pgai-readme-walk
cd /tmp/pgai-readme-walk

command -v git   >/dev/null || { echo "git missing";   exit 1; }
command -v python3 >/dev/null || { echo "python3 missing"; exit 1; }
command -v pip   >/dev/null || { echo "pip missing";   exit 1; }
command -v curl  >/dev/null || { echo "curl missing";  exit 1; }

[ -e "$HOME/pgai_agent_kanban" ] && \
    { echo "WARN: $HOME/pgai_agent_kanban exists — back it up before proceeding"; exit 1; }
```

Expected: no output from the missing-command checks; the pre-existing
install check does not warn. Any WARN or exit 1 is a stop condition.

### 2. Demo spine step 1 — clone and install

Verbatim from the README five-minute demo spine, step 1. Substitute the
concrete repository URL for `<this-repo>` (record the URL in the outcome
section).

```bash
git clone <this-repo> ~/develop/pgai-agent-kanban
cd ~/develop/pgai-agent-kanban
./install.sh
```

Expected: the install completes without error. The install root
`$HOME/pgai_agent_kanban/` now exists and contains the framework tree.

Verify:

```bash
[ -d "$HOME/pgai_agent_kanban" ] || { echo "install root missing"; exit 1; }
[ -x "$HOME/pgai_agent_kanban/scripts/create-project.sh" ] || \
    { echo "create-project.sh missing or not executable"; exit 1; }
```

### 3. Demo spine step 2 — install Python deps

Verbatim from the README five-minute demo spine, step 2. Drop the
`--break-system-packages` flag if the target Python is a venv.

```bash
cd ~/develop/pgai-agent-kanban
pip install -r requirements.txt --break-system-packages
```

Expected: `PyYAML` installs. The test toolchain adds `pytest` and
`pytest-cov`; presence of those is fine.

### 4. Demo spine step 3 — register a project

Verbatim from the README five-minute demo spine, step 3. Substitute a
concrete `<repo-url>` for the demo project (record it in the outcome
section).

```bash
~/pgai_agent_kanban/scripts/create-project.sh --project my-app \
    --workflow-type release \
    --dev-tree ~/develop/my-app --git-repo <repo-url>
```

Expected: the command succeeds. Verify:

```bash
[ -d "$HOME/pgai_agent_kanban/projects/my-app" ] || \
    { echo "project my-app not registered"; exit 1; }
[ -f "$HOME/pgai_agent_kanban/projects/my-app/project.cfg" ] || \
    { echo "project.cfg not created"; exit 1; }
grep -q "workflow_type[[:space:]]*=[[:space:]]*release" \
    "$HOME/pgai_agent_kanban/projects/my-app/project.cfg" || \
    { echo "workflow_type not 'release'"; exit 1; }
```

### 5. Demo spine step 4 — one-time git setup

Verbatim from the README five-minute demo spine, step 4.

```bash
~/pgai_agent_kanban/scripts/init-project-git-repo.sh --project my-app
```

Expected: the initial base branches are pushed to the project's origin.
Record the command's exit code in the outcome section.

### 6. Demo spine step 5 — drop a requirements document

Verbatim from the README five-minute demo spine, step 5. Substitute a
concrete requirements filename (any valid `vX.Y.Z-*.md` shape) for
`v1.0.0-my-feature.md`.

```bash
~/pgai_agent_kanban/scripts/intake.sh --project my-app v1.0.0-my-feature.md
```

Expected: the intake script accepts the file (or refuses it with a
loud, named reason). Verify the file landed under the project's
`requirements/` directory OR the operator sees a clean refusal:

```bash
ls "$HOME/pgai_agent_kanban/projects/my-app/requirements/" 2>&1 | head
```

Record whichever outcome the intake produced.

### 7. Demo spine step 6 — start the dashboard

Verbatim from the README five-minute demo spine, step 6.

```bash
~/pgai_agent_kanban/scripts/dashboard/create.sh
```

Expected: the tmux dashboard session is created. Detach without killing
the session (default binding: `Ctrl-b d`) before continuing to the API
quickstart section. Record the tmux session name in the outcome section.

### 8. API quickstart step 1 — start the server

Verbatim from the README API quickstart, command 1.

```bash
~/pgai_agent_kanban/scripts/api-server.sh start
```

Expected: the server starts and binds `127.0.0.1:8300` only. Verify:

```bash
sleep 2
ss -ltn | grep -E '127\.0\.0\.1:8300' || { echo "server not bound to loopback:8300"; exit 1; }
ss -ltn | grep -E '0\.0\.0\.0:8300|\[::\]:8300' && \
    { echo "SECURITY: server bound to routable address"; exit 1; }
```

Any bind to `0.0.0.0`, `::`, or a routable IP is a hard finding — file
it and stop.

### 9. API quickstart step 2 — read the board

Verbatim from the README API quickstart, command 2.

```bash
curl -sS http://127.0.0.1:8300/board
```

Expected: a JSON document describing every project and every column. The
response is not the operate envelope — this is a JSON-native structured
read. Verify shape:

```bash
curl -sS http://127.0.0.1:8300/board | python3 -c 'import json,sys; json.load(sys.stdin)' \
    && echo "board: valid JSON" \
    || { echo "board: NOT valid JSON"; exit 1; }
```

### 10. API quickstart step 3 — halt a project

Verbatim from the README API quickstart, command 3.

```bash
curl -sS -X POST http://127.0.0.1:8300/operations/halt \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app"}'
```

Expected: the response is the operate envelope documented immediately
after this command block in the README:

```json
{
  "exit_code": 0,
  "stdout": "halt: HALT signal set for project my-app (…/projects/my-app/HALT)\n",
  "stderr": ""
}
```

Verify the envelope keys are exactly `exit_code`, `stdout`, `stderr`:

```bash
curl -sS -X POST http://127.0.0.1:8300/operations/halt \
    -H 'Content-Type: application/json' \
    -d '{"project": "my-app"}' \
  | python3 -c 'import json,sys; e=json.load(sys.stdin); assert set(e.keys())=={"exit_code","stdout","stderr"}, e.keys(); print("envelope: ok")'
```

Also verify the loopback trust model in prose after the block: the
server refuses to bind to a routable address, the Swagger UI answers at
`http://127.0.0.1:8300/docs`, and HTTP status derives from `exit_code`
(`200` on `0`, `500` on non-zero, `422` on missing/invalid params).
Sanity-check `/docs`:

```bash
curl -sSI http://127.0.0.1:8300/docs | head -1
```

Expected: HTTP 200.

### 11. Tear down

Reverse the setup so the host returns to the pre-checkpoint state:

```bash
~/pgai_agent_kanban/scripts/api-server.sh stop
tmux kill-session -t pgai-dashboard 2>/dev/null || true
```

If the operator installed into `$HOME/pgai_agent_kanban/` for the
checkpoint, remove or restore per the pre-flight decision:

```bash
# Only if the checkpoint created this install and no backup exists to restore.
rm -rf "$HOME/pgai_agent_kanban" ~/develop/pgai-agent-kanban ~/develop/my-app
rm -rf /tmp/pgai-readme-walk
```

## Outcome (fill in after running)

Record on the checkpoint output. One row per numbered step above.

| Step | Command block | Result (pass / fail / deviation) | Notes |
|---|---|---|---|
| 1 | Prep the scratch host | | |
| 2 | Demo spine step 1 (clone + install.sh) | | |
| 3 | Demo spine step 2 (pip install) | | |
| 4 | Demo spine step 3 (create-project.sh) | | |
| 5 | Demo spine step 4 (init-project-git-repo.sh) | | |
| 6 | Demo spine step 5 (intake.sh) | | |
| 7 | Demo spine step 6 (dashboard) | | |
| 8 | API quickstart 1 (server start, loopback-only) | | |
| 9 | API quickstart 2 (/board, JSON) | | |
| 10 | API quickstart 3 (/operations/halt, envelope) | | |
| 11 | Tear down | | |

Record additionally:

- Host, OS, and Python version.
- Repository URL used for `<this-repo>` (step 2) and demo `<repo-url>` (step 4).
- Commit SHA the fresh clone landed on.
- Any command whose text on the console differed from the command text
  in the README. Any non-empty answer here is a README finding.
- The observed operate envelope's actual `stdout` string (the README's
  example uses `…` for the path — the real path is a finding only if the
  KEY set differs).

## Findings

If any step fails, note it here with the exact console output. A finding
is not a blocker for other steps in this checkpoint — continue and
record everything.

- (empty)

## Sign-off

- Operator: (name)
- Date: (YYYY-MM-DD)
- Result: (pass / fail / partial)
