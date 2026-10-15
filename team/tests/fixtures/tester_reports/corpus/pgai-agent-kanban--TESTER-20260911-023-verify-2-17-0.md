# Verification Report: ai_rc/v2.17.0

**Task:** TESTER-20260911-023-verify-2-17-0
**Agent:** tester  **Model:** claude-fable-5-1
**RC branch:** `ai_rc/v2.17.0` @ `824d3c04330ff99a9c0bbcc8bf1f9b29ed4a6c62`
**Baseline:** `ai_main` @ `e8ab7f0b62b4e6192eeccfa43af7f59140407b54`
**Requirements:** `projects/pgai-agent-kanban/requirements/v2.17.0-container-fleet.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260911-023-verify-2-17-0` (detached at the RC commit)

## Summary

Verified the v2.17.0 container-fleet RC against its requirements document. The RC
carries fifteen commits across seven prerequisite tasks and touches seventeen
files: the three flavor Dockerfiles, the new `docker/cli-versions.env` pin file,
`docker/entrypoint.sh`, `team/scripts/container-shell.sh`, a rewritten
`docs/docker.md`, and the fixture/test surface behind all of it. Both gated
runners exited 0 (unit 3823 passed / 3 skipped; integration 442 passed / 1
skipped). Every deliverable in the requirements document is present and was
checked by reading the shipped file, not by inferring from the diff. Three guard
mechanisms were exercised against the failure modes they exist to prevent rather
than inspected: the unpinned-build guard was triggered with a real
argument-free `docker build` and refused naming the missing ARG; all five
entrypoint mount-contract refusals were triggered with synthetic mounts and each
printed the exact two-line message `docs/docker.md` documents for it; the
zero-flag launcher was run against a stub engine and emitted `--rm`, the
corrected default tag, and both matching-path mounts. One quality-class lint
finding surfaced (pre-existing, in already-shipped release notes) and was filed
via Path C-A. No `bug`, `gap`, or `stale-assertion` finding was recorded.

## Recommendation

PASS

## Systemic Risk

low

## Test Suites

| Suite | Command (gated runner — authoritative) | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3823 passed, 3 skipped in 517.34s |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 442 passed, 1 skipped in 414.19s |

Both runners' post-suite cleanliness checks also reported OK
(`bare_tmp_cleanliness: OK`, `listener_cleanliness: OK`). The PASS evidence
cited here is the gated-runner exit code, not a direct-pytest "N passed" line.

Captured output:
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester/unit_output.txt`,
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester/integration_output.txt`.

## Code Coverage

Read from the `totals` object of the runner-written JSON reports (field names
confirmed against the generated files, not assumed).

- Unit:        line 72%  |  branch 62%   (11202/14928 stmts, 2768/4496 branches)
- Integration: line 8%   |  branch 6%    (1176/14919 stmts, 277/4496 branches)

Informational only. Coverage did not enter the PASS precondition.

## Findings

### Finding 1: Pre-flight merge verification

**Category:** pass

Scanned every task folder under `projects/pgai-agent-kanban/tasks/`. Every DONE
task with a non-`none` `## Feature Branch` had its branch already deleted — the
normal post-merge cleanup CODER and WRITER perform — so zero live feature
branches remained to check, and both Check 1 (ancestor of RC) and Check 2
(BUG-92 zero-commits-ahead) had no subjects. All seven prerequisite tasks'
merges are present in the RC log as `--no-ff` merge commits (tasks 015 through
021). Worktree HEAD matches `ai_rc/v2.17.0` exactly, `git status --short` is
empty, and `git stash list` is empty.

### Finding 2: release-state.md sanity (Step 6.1)

**Category:** pass

`## Active RC` = `v2.17.0`, `## RC Opened By Task` =
`CM-20260911-022-open-rc-2-17-0`, `## RC Opened At` = `2026-09-11T06:40:18+00:00`
(the file's mtime matches that timestamp to the minute, consistent with
`cm-open-rc.sh` having written it rather than a hand-edit). `## Last Released`
= `v2.16.2`, well-formed `vX.Y.Z`. `semver_gt v2.17.0 v2.16.2` is true. Highest
tag on the tree is `ai_v2.16.2`, agreeing independently. No drift.

### Finding 3: Deliverable — `docker/cli-versions.env` as the single source of CLI versions

**Category:** pass

`docker/cli-versions.env` exists and holds exactly the two required
`KEY=value` lines (`CLAUDE_CODE_VERSION=2.1.268`, `CODEX_VERSION=0.154.0`).
All three Dockerfiles declare `ARG CLAUDE_CODE_VERSION` / `ARG CODEX_VERSION`
**with no default** (`docker/rocky9/Dockerfile:45-46`,
`docker/debian/Dockerfile:60-61`,
`docker/claude-abliterated/Dockerfile:48-49`) and each carries a
byte-identical fail-loud `${VAR:?message}` guard in its own `RUN` layer ahead of
the `npm install`. Grepping the tree found no Dockerfile carrying a hardcoded
CLI version of its own.

The single reader, `team/tests/fixtures/docker_cli_pins.py`, resolves the env
file relative to its own module location (not the caller's cwd) and refuses
rather than defaulting on both failure modes — absent file (`FileNotFoundError`
naming the resolved absolute path) and missing key (`ValueError` naming the
key). Its four unit tests cover both refusals.

Both consumers pass the args through that one reader: the cold-build lane
(`team/tests/cold/test_docker_cold_build.py:450`, via
`cli_pins_build_args()`), and every docker-build fixture in the fast suite
(`test_docker_build_fixture.py`, `test_entrypoint_shipped_image.py`,
`test_dashboard_smoke_container.py`, `test_dashboard_operator_window.py`).
`docs/docker.md` passes them as explicit `--build-arg` flags sourced from the
same file via `set -a && . docker/cli-versions.env && set +a`.

The bump ritual is documented at `docs/docker.md` section "Bumping a pinned
version": candidate through the cold-build lane first (`scripts/cold-build-now.sh
--project <name>`), then land as an ordinary RC — in that order, with the
blast-radius reasoning stated.

### Finding 4: Deliverable — the three flavors

**Category:** pass

- **debian** — `FROM python:3.12-bookworm` with Node 20 copied from a
  `node:20-bookworm` stage; `kanban` user at UID/GID 1000 with overridable
  `KANBAN_UID`/`KANBAN_GID` build args.
- **rocky9** — `FROM quay.io/rockylinux/rockylinux:9`. The RC's diff is exactly
  the rebase off the stale Docker Hub `rockylinux:9` tag, as specified. Same
  user model as debian.
- **claude-abliterated** — `USER root` on line 14, immediately after the `ENV`
  block and ahead of every install step, with `USER kanban` restored on line
  112 as the last instruction. The adopt-and-rename block handles all three
  arrival states the requirement names and is idempotent: gid 1000 absent ->
  `groupadd`; gid 1000 held under another name (squatter) -> `groupmod
  --new-name`; already `kanban` -> neither branch taken, no-op. Same three-way
  shape for the user via `useradd` / `usermod --login ... --move-home`. A
  following `RUN` asserts the result is exactly `1000:1000` and fails loud
  naming the ids it actually resolved, citing the bind-mount contract as the
  reason.

All three flavors put the CLI smoke check in its own `RUN` layer, separate
from the install layer (`RUN claude --version && codex --version` at
debian:80, rocky9:65, claude-abliterated:68), with the reasoning — npm's
"added N packages" is not proof the launcher runs — stated at the site.

Supporting runtime evidence: the fast suite's `test_docker_build_fixture.py`
ran 16 tests with zero skips on this host (docker daemon reachable), which
means both the debian and rocky9 flavors were actually built with the pins
supplied and then exercised. `_narrate_skip()` calls `pytest.skip()`, so a
missing daemon would have shown as a skip rather than a false pass — it did
not.

### Finding 5: Deliverable — the launcher (`team/scripts/container-shell.sh`)

**Category:** pass

Behaviorally verified (Step 6.5), not inspected. Ran the launcher with no
flags against a stub `docker` on `PATH`:

```
container-shell.sh: engine=docker image=pgai-agent-kanban-debian:latest kanban_root=... home=...
STUB-ENGINE-ARGS: run --rm -it -v <home>:<home> -v <root>:<root> -e HOME=<home> -e PGAI_AGENT_KANBAN_ROOT_PATH=<root> pgai-agent-kanban-debian:latest
```

- **Zero-flag launch** — resolved both paths from the environment the
  `shell-env` bootstrap populates; no flag and no ad-hoc export needed.
- **Default tag matches the documented build tag** — `pgai-agent-kanban-debian:latest`
  in `_DEFAULT_IMAGE_TAG`, in the usage text, and in the `docker build -t ...`
  command at `docs/docker.md:161`. The stale `pgai-agent-kanban-rocky:latest`
  string appears nowhere in the tree (grepped, zero hits), satisfying the
  ticket's explicit no-residue criterion.
- **`--rm` disposability** — present in the assembled invocation above, and
  asserted by `test_assembled_invocation_carries_the_rm_flag` rather than left
  to inspection.
- **Engine-portable flags only** — `run --rm -it -v -e`, all accepted by both
  docker and podman, as `docs/docker.md` section Podman claims.
- **Narration line** — matches the example in `docs/docker.md:252` verbatim.

### Finding 6: Deliverable — `docs/docker.md`

**Category:** pass

Read end-to-end (438 lines, substantially rewritten — Principle 3). Every
required subject is present and, where checkable, accurate:

- **All three flavors** — a "What ships in `docker/`" table, a "Choosing a
  flavor" comparison table with a stated default, and a separate build command
  per flavor, each passing both `--build-arg` flags.
- **The env file** — its own section, the two keys shown, the no-default/refuse
  contract explained, and the `set -a` load line given.
- **The pins policy** — the bump ritual in the mandated order, with the reason
  for the order.
- **The pseudocron-in-container ritual** — "One heartbeat" section: host crontab
  confirmed off (`crontab -l`, "no crontab for ..." is the wanted answer), exactly
  one scheduler started by hand in the container, and the single-heartbeat
  confirmation via the `fired (minute=NN)` log line, with the two-heartbeats
  failure mode named explicitly.
- **`install --no-system-cron`** — the volume-is-an-install rule, the command,
  and why that flag specifically.

Doc/tree parity checks that passed: `install.sh` does implement
`--no-system-cron` (install.sh:205); `scripts/cold-build-now.sh`,
`scripts/pseudocron.py` and `scripts/dashboard/create.sh` all exist at the
install-layout paths the document tells the operator to run; the default image
tag in the launcher table matches the code; and every one of the four rows in
the mount-contract refusal table reproduces the entrypoint's real message (see
Finding 7). Fenced blocks that cannot run in the harness carry explicit
`doc-lint: skip` comments with stated reasons.

### Finding 7: Acceptance — the entrypoint fails loud on the mount contract

**Category:** pass

Behaviorally verified. Ran `docker/entrypoint.sh` directly against six
synthetic environments:

| Case | Result |
|---|---|
| `PGAI_AGENT_KANBAN_ROOT_PATH` unset | exit 1, names the variable + host remedy |
| kanban root points at an absent directory | exit 1, names the offending path |
| `HOME` unset | exit 1, names the variable + `-v "$HOME:$HOME" -e "HOME=$HOME"` remedy |
| a raw checkout mounted as the kanban root | exit 1, names checkout-vs-install layout + `install.sh --no-system-cron` remedy |
| an empty directory mounted as the kanban root | exit 1, names `scripts/pseudocron.py` missing + same remedy |
| contract satisfied | exit 0 |

Each refusal printed exactly two lines — what is wrong, then a remedy the
reader runs on the host — and each matched the corresponding row of the
`docs/docker.md` refusal table. The happy path produced no error output and
`cd`'d into the kanban root before `exec bash -l`. The script carries
`set -euo pipefail`.

### Finding 8: Acceptance — a build without args fails naming the ARG

**Category:** pass

Behaviorally verified (Step 6.5 — the guard was triggered, not inspected). Ran
a real argument-free build from the RC worktree:

```
docker build -f docker/debian/Dockerfile -t pgai-tester-unpinned-probe:test .
-> EXIT=1
#14 0.210 /bin/sh: 1: CLAUDE_CODE_VERSION: CLAUDE_CODE_VERSION build-arg is required,
  e.g. --build-arg CLAUDE_CODE_VERSION=2.1.268 -- refusing to install an unpinned claude-code
```

The build stopped at the guard layer (step 8 of 14) — before the npm download,
as designed — and the message names the missing argument and a runnable
remedy. The probe image was removed afterward. The guard text is byte-identical
in all three Dockerfiles (one match each, confirmed by grep), so the same
refusal is in place for rocky9 and claude-abliterated.

The RC also ships this as a standing test:
`test_build_without_pinned_versions_fails_naming_the_missing_arg`, parametrized
over debian and rocky9, which ran (not skipped) in the unit suite on this host.
`team/tests/cold/test_docker_cold_build.py` carries the equivalent against a
real shipped flavor for the cold lane.

### Finding 9: Acceptance — the relaunch-same-board fixture

**Category:** pass

`team/tests/unit/test_container_shell_launcher.py:499` —
`test_relaunch_against_same_kanban_root_presents_the_same_board` runs the
launcher twice against the same kanban root through the fake-engine harness and
asserts both invocations carry identical mount and env flags. It passed in the
unit suite. The file's other new coverage (zero-flag default tag, `--rm`
presence) passed alongside it.

### Finding 10: Acceptance — a bad pinned version fails in the smoke layer naming the CLI

**Category:** pass-with-caveat

The mechanism is in place and correctly shaped: because the smoke check lives
in its own `RUN` layer after the install layer, a version that publishes to npm
but does not launch fails at `claude --version` / `codex --version` rather than
being masked by the install layer's success. `test_docker_cold_build.py`
carries the assertion directly — it builds a real shipped flavor with a
deliberately bad `CODEX_VERSION` (lines 645-654 and 803-814) and asserts the
failure names the CLI.

**Caveat:** that assertion lives in the cold lane, which by design is not an RC
step (SOP section "The DEEP-VERIFY tier"), so it was not executed during this
verification. The structural half — smoke check isolated in its own layer,
after install, in all three flavors — was verified directly. This is recorded
as a caveat rather than a gap: the check exists, is wired to the right lane, and
the lane has a pre-release gate in front of the public tag.

### Finding 11: Acceptance — cold build all flavors under docker AND podman from the docs verbatim

**Category:** pass-with-caveat

Not exercised at RC time, and correctly so: `team/roles/TESTER.md` Step 5d
forbids TESTER from running the cold-build lane or starting a container engine
during RC verification, and SOP section "The DEEP-VERIFY tier" moves this
criterion to the DEEP-VERIFY tier with a pre-release freshness check gating the
public tag. `team/scripts/deep-verify.sh` is present, so the tree is not in the
one case that would make this a `gap` (images shipping with no cold-build gate
anywhere in front of them).

What was established instead: `test_docker_cold_build.py` discovers all three
flavors (asserting a minimum set of `{debian, rocky9, claude-abliterated}` at
line 469) and parametrizes over both engines, passing the pins through
`cli_pins_build_args()`; and the fast suite's own build fixture really did build
debian and rocky9 under docker with the pins on this host. Both podman and
docker are installed here, but the two-engine x three-flavor sweep belongs to
the lane.

See `## Deep-Verify Freshness` below for what the lane currently knows about
this tree — the answer is "nothing," and CM should read it before tagging.

### Finding 12: Autonomous operation criterion (Step 6.12)

**Category:** pass

See the `## Autonomous Criterion` section — all four checks are NO.

### Finding 13: Skip-cites-real-bug grep-gate (Step 6.13)

**Category:** pass

No placeholder skip IDs (`BUG-SKIP`, `skip.*BUG-[A-Za-z]`) in any test source.
The only grep hits were the literal string `BUG-NNNN` inside four archived
`team/tests/artifacts/coverage-*-v2.3.0/*-run.log` files, which are captured
runner logs, not skip annotations. Every `BUG-NNNN` cited from a skip
annotation resolves to an existing file in `bugs/` (zero `MISSING:` lines). The
gated runners' own `lint_skip_bug_gate.sh` pre-flight also passed.

### Finding 14: Constraint negative checks and coding-standards spot-check (Steps 6.9, 6.14)

**Category:** pass

- `set -euo pipefail` present in both changed shell files (`docker/entrypoint.sh:20`,
  `team/scripts/container-shell.sh:42`).
- No credential-shaped assignments introduced anywhere in the diff.
- No new script was added under `team/scripts/`; the one modified script still
  exits 0 on `--help` and documents every flag including the corrected default,
  asserted by `test_help_flag_exits_zero_and_documents_every_option`.
- Directive 5 (operational values belong in config): the RC moves the CLI
  versions out of the Dockerfiles into `docker/cli-versions.env` — this change
  is the directive being applied, not violated.
- Directive 8 (no internal IDs in production strings): the hygiene lint found
  zero provenance hits in any file this RC touched. Its single hit is in
  already-shipped release notes (see Lint Findings).
- Directives 1/3 (no duplicated implementation): `docker_cli_pins.py` is a
  single new reader that all five build call sites go through; no second parser
  of the env file exists in the tree.

### Finding 15: Step 6.8 — synthetic release simulation

**Category:** pass (skipped by trigger condition, with note)

The RC's diff touches no release-lifecycle file: no `cm-release.sh`,
`cm-open-rc.sh`, `cm-cancel-rc.sh`, or `lib/project_paths.sh`. The only
`team/scripts/` file in the diff is `container-shell.sh`, which does not
participate in the open/close cycle. Step 6.8 is skipped per its own trigger
condition. `team/tests/integration/test_cm_release_synthetic.py` is present and
passed as part of the green integration suite.

## Lint Findings (non-blocking)

- `hygiene` (class `provenance`) — `release-notes/v2.16.0.md:42` cites the
  internal task ID `TESTER-20260910-070` in a shipped operator-facing document,
  pointing the reader at a task folder that does not ship — **filed BUG-0242
  (Path C-A — filed as BUG — code defect / regression / test failure)**.

One finding, one lint class, one bug — the same single instance appeared in
both the unit and the integration findings file and was aggregated into one
filing. The site is not in this RC's diff (`release-notes/v2.16.0.md` last
changed at `d25e9a6`, before `ai_main`), so it is pre-existing. BUG-0236 is
open for the same lint class but its `## Actual` and `## Findings` sections name
a different instance (`team/tests/unit/test_lint_gate_census_compliance.py:1216`),
so a CODER working from it would never reach this site; a new bug was filed
rather than a duplicate cited. Per Step 5c this finding does not move
`## Recommendation` or `## Systemic Risk`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=4d freshness=stale — stale on version only (4d is inside the 7-day window, but v2.9.20 is not the v2.17.0 under verification); the record predates every container change in this RC and names failures — a claude-abliterated FROM-arity parse error under both engines and a podman/rocky9 `COPY --chmod=+x` step — that the tree no longer contains, cited to BUG-0167, which is now `done`.

The RC has therefore never been cold-built by the lane. Nothing above is
re-derived: the verdict is the ledger's own word, read through
`deep_verify_ledger_read_latest`. Per Step 5d this record is context the rating
was made with, not a finding, and it does not move the recommendation or the
systemic risk. No bug was filed for it — the deep-verify run that produced it
already filed BUG-0167.

## Autonomous Criterion

| # | Check | Answer |
|---|---|---|
| 1 | Tasks force-promoted from WAITING to BACKLOG by hand? | **NO** |
| 2 | `release-state.md` edited outside the `cm-*` scripts? | **NO** |
| 3 | Scripts patched mid-build? | **NO** |
| 4 | Queue files manually edited? | **NO** |

**Manual interventions required: none.**

Evidence: all nine cycle tasks (CM-022, PM-003, CODER-015 through CODER-020,
WRITER-021) are `DONE` with `Needs Human: no`. The PM wake log shows the normal
discovery -> materialize path, including an idempotent re-materialization no-op
that behaved as designed. `release-state.md`'s mtime matches its own
`## RC Opened At` timestamp. The RC log is fifteen commits that are exactly the
seven tasks' work plus their `--no-ff` merges — no out-of-band commit. The
tester queue entry for this task carries `[A]`, written by the wake script. No
`HALT` or `HALT-AFTER` file exists at either scope. `logs/activity.log` has no
entry for 2026-09-11, so no host-level software change was made during the
build.

## Caveats

- **The one thing this report cannot speak to is the RC's headline promise.**
  The requirement's intent is "three flavors a stranger can cold-build any
  morning," and its first acceptance line is a two-engine x three-flavor cold
  build. That is a DEEP-VERIFY criterion by construction, and the freshness line
  above says the lane has never seen this tree. Everything verifiable at RC time
  is green, and the partial evidence is genuinely encouraging — debian and
  rocky9 really did build under docker with the pins during the unit suite —
  but claude-abliterated was built by nothing in this run, and podman was
  exercised by nothing in this run. The pre-release freshness check
  (`deep-verify-freshness-check.sh --project pgai-agent-kanban --version
  v2.17.0`) will refuse promotion on the stale record, and the remedy is the
  on-demand verb (`scripts/cold-build-now.sh --project pgai-agent-kanban`).
  This is a note about sequencing for CM, not a finding against the RC.
- `docker/debian/Dockerfile` carries `# syntax=docker/dockerfile:1` on line 2,
  below an ordinary comment. Docker only honours a parser directive that precedes
  every comment and instruction, so as written the directive is inert. It does no
  harm today — BuildKit is the default builder and podman/buildah supports
  `COPY --chmod` natively, and the argument-free build probe in Finding 8 reached
  the guard layer normally — and the line predates this RC (untouched by the
  diff). Recorded as an observation, not a finding; it would only bite a build
  forced through the classic builder with `DOCKER_BUILDKIT=0`.
- `docs/docker.md` says the launcher "resolves its paths from your environment
  (via `shell-env`)". The launcher reads the variables from the environment; it
  does not source `shell-env` itself, and `shell-env` ships with its
  `PGAI_AGENT_KANBAN_ROOT_PATH` export commented out because `install.sh`
  deliberately does not auto-seed that file. The document's own code block says
  "with `PGAI_AGENT_KANBAN_ROOT_PATH` set", so a reader following it in order is
  not misled, and the launcher exits 1 naming the variable rather than failing
  obscurely. Observation only — the ticket's criterion was "no ad-hoc export
  beyond the existing shell-env bootstrap", and that holds.
- The claude-abliterated adopt-and-rename block is exercised by no fast test;
  its coverage is the cold lane. The idempotence and three-state logic were
  read closely and are correct, and the block is followed by a hard assertion
  that the result is `1000:1000`. One narrow edge the block does not handle
  gracefully: a group literally named `kanban` already existing at a gid other
  than 1000 while gid 1000 is held by a third name would make `groupmod
  --new-name kanban` fail on the duplicate name. The build would stop loudly at
  that point rather than produce a wrong image, so the failure mode is safe.
  Not filed — it is speculative about a base image the project does not control.
- No `artifacts/gaps.md` was written: Step 10 requires it only for
  SHIP-WITH-CONCERNS or SHIP-WITH-SERIOUS-CONCERNS, and this report is PASS with
  zero `bug`/`gap`/`stale-assertion` findings. The single Path C filing
  (BUG-0242) tracks a non-blocking quality-class lint finding.

## Path C Filings

| ID | Path | Class | Path C route |
|---|---|---|---|
| BUG-0242 | `projects/pgai-agent-kanban/bugs/BUG-0242-release-notes-cite-task-id.md` | quality-class lint (`hygiene`/`provenance`) | C-A (filed as BUG — code defect / regression / test failure) |
