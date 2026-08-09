# DASHBOARD-GIT-CENSUS.md
# Full Git-Usage Census for Dashboard Scripts

**Purpose:** Enumerate every git usage site across ALL dashboard window
scripts (main, operator, visibility, attention, git, metadata, metrics,
logs, drill windows, and every shared `dashboard/lib/*.sh` helper), classify
each site, and record the missing-tree behavior chosen for it — per
BUG-0112 ("The ENTIRE dashboard is local-only").

**Task:** CODER-20260726-003-dashboard-census-local-only
**Date:** 2026-07-26
**Related:** CODER-20260726-002-discovery-git-local-only-flag (prerequisite;
added the `PGAI_GIT_LOCAL_ONLY` guard to `discovery.sh`'s `_disc_git_tag_exists`)

**Scope note:** this ticket converts every non-`4:git`-window site. The
`4:git` window itself (`git-status.sh`, `git-recent-tags.sh`) is classified
here for completeness; its message-wording follow-up was completed by
`CODER-20260726-004-git-window-local-render` — see Section B below.

---

## Methodology

```bash
# Pass 1 — files containing the literal string "git" anywhere:
grep -rln "git" team/scripts/dashboard/

# Pass 2 — files touching git transitively through a wrapper function name
# (catches sites where "git" itself never appears as a literal token because
# the call goes through pp_last_released_version / get_kanban_version /
# get_latest_released_tag / discovery_scan_stale_requirements):
grep -rln "pp_last_released_version\|get_kanban_version\|get_latest_released_tag\|\.git\b\|git -C\|git tag\|git fetch\|git log\|git status\|git branch\|git rev-parse\|git symbolic-ref\|git rev-list\|git ls-remote\|discovery_scan_stale_requirements\|discovery.sh" \
  team/scripts/dashboard/ team/scripts/dashboard/lib/

# Pass 3 — network-capable git verbs specifically (the ones the bug forbids):
grep -rn "ls-remote\|git fetch\|git pull\|git push\|git clone\| fetch \| clone " \
  team/scripts/dashboard/ team/scripts/lib/project_paths.sh team/scripts/lib/discovery.sh

# Pass 4 — confirmed set of "drill window" scripts (windows created per
# registered project by create.sh's drill-N block) by reading the drill
# creation block in create.sh (~line 1235 onward) rather than grepping,
# since drill windows are composed of ordinary show-*.sh scripts, not a
# separate "drill" script family.
```

Pass 2 is the exhaustive one — it is what the census in this document is
built from. Pass 1 alone would have missed `show-multi.sh`, `column-render.sh`,
and `show-status-window.sh` (none contain the literal substring `git`, but
all three call a git-touching wrapper function).

---

## Summary Table — every dashboard file, one row each

Every `.sh` file under `team/scripts/dashboard/` (33 files, non-recursive)
plus the three shared helpers under `team/scripts/dashboard/lib/` (3 files)
is listed. "Window" names the tmux window(s) that invoke the script, where
applicable (a script may be invoked from more than one window — e.g.
`show-header.sh` is used by the main window AND every drill-N window).

| File | Window(s) | Git site? | Classification | Network-capable before this ticket? | Action taken |
|---|---|---|---|---|---|
| `attach.sh` | (control script) | no | NO-GIT | — | none |
| `attention.sh` | 4-attention | **yes** (transitive, via `discovery.sh`) | RENDER-PATH | yes (ls-remote fallthrough in `_disc_git_tag_exists`; already gated by ticket 2, but the gate was never armed) | **CONVERTED** — exports `PGAI_GIT_LOCAL_ONLY=1` before sourcing `discovery.sh` |
| `column-render.sh` | visibility, operator, drill-N | **yes** (transitive, via `pp_last_released_version`) | RENDER-PATH | yes (`git fetch origin --tags` inside `pp_last_released_version`) | **CONVERTED** — exports `PGAI_GIT_LOCAL_ONLY=1` |
| `costs.sh` | (cost report pane) | no | NO-GIT | — | none |
| `create.sh` | (orchestrator — creates all windows) | no direct git call | ORCHESTRATION-ONLY | n/a (wires up `watch` commands targeting `git-status.sh` / `git-recent-tags.sh` / `metadata.sh`, none of which are invoked directly by `create.sh` itself) | none needed; the invoked scripts are classified separately below |
| `cron-tail.sh` | logs (helper) | no | NO-GIT | — | none |
| `data.sh` | (subprocess of show-header.sh, show-progress.sh, show-active.sh, show-multi.sh, show-queues.sh, show-status-window.sh) | **yes** (`pp_last_released_version`, `get_kanban_version`) | RENDER-PATH | yes | **CONVERTED** — exports `PGAI_GIT_LOCAL_ONLY=1` |
| `debug-logs.sh` | debug-logs | no | NO-GIT | — | none |
| `detach.sh` | (control script) | no | NO-GIT | — | none |
| `git-recent-tags.sh` | 4:git (right pane) | **yes** (local `git -C <dev_tree> tag/log`) | **4:GIT-WINDOW** | no (audited: no fetch/pull/push/clone/ls-remote found; already local-only) | **CONVERTED** (message wording) — see Section B |
| `git-status.sh` | 4:git (left pane) | **yes** (local `git -C <dev_tree> status/branch/rev-list/log/rev-parse`) | **4:GIT-WINDOW** | no (audited: `rev-list origin/<branch>..<branch>` compares against the LOCAL remote-tracking ref only — no fetch is performed to refresh it) | **CONVERTED** (message wording) — see Section B |
| `human-review.sh` | human-review | no | NO-GIT | — | none |
| `kill.sh` | (control script) | no | NO-GIT | — | none |
| `legend-render.sh` | visibility (legend row) | no | NO-GIT | — | none |
| `lib/halt_scope.sh` | (shared helper) | no | NO-GIT | — | none |
| `lib/helper_resolver.sh` | (shared helper) | no | NO-GIT | — | none |
| `lib/version.sh` | (shared helper: `data.sh`, `show-header.sh`, `show-status-window.sh`, `show-multi.sh`, `status-bottom.sh`) | **yes** (`get_latest_released_tag`, `get_kanban_version`) | RENDER-PATH | yes (`git fetch origin --tags --quiet` inside `get_kanban_version` Tier 3, unconditional) | **CONVERTED** — fetch call removed unconditionally (this lib is dashboard-render-only; never used by agent/wake code) |
| `logs.sh` | logs, drill-N (bottom pane) | no | NO-GIT | — | none |
| `metadata.sh` | 5:metadata | **yes** (`pp_last_released_version`) | RENDER-PATH | yes | **CONVERTED** — exports `PGAI_GIT_LOCAL_ONLY=1` |
| `metrics.sh` | 6:metrics | no | NO-GIT | — | none |
| `next-cron-firings.sh` | main, drill-N | no | NO-GIT | — | none |
| `project-toggle.sh` | (keybinding helper — dashboard/overview toggle) | **false positive** | NO-GIT | n/a | none — the only "git" substring hit is inside the word `di`**`git`**`s` in a comment about the `drill-<digits>` filename pattern; no git command anywhere in the file |
| `queue-pane.sh` | visibility, operator (queue columns, via column-render.sh) | no | NO-GIT | — | none |
| `show-active.sh` | (subprocess consumer of data.sh) | no direct git call | NO-GIT (covered transitively) | n/a | none — calls `data.sh` as a subprocess; `data.sh`'s own guard covers it |
| `show-attention.sh` | (wrapper invoking attention.sh output) | no direct git call | NO-GIT (covered transitively) | n/a | none — delegates to `attention.sh`, which is converted |
| `show-document-drill.sh` | drill-N (document-workflow projects) | no | NO-GIT | — | none |
| `show-header.sh` | main, drill-N | **yes** (`pp_last_released_version` directly; also invokes `data.sh` as subprocess) | RENDER-PATH | yes | **CONVERTED** — exports `PGAI_GIT_LOCAL_ONLY=1` |
| `show-metrics.sh` | (metrics history, `--no-tmux` mode) | no | NO-GIT | — | none |
| `show-multi.sh` | main (multi-project stacked overview) | **yes** (`pp_last_released_version`, `get_kanban_version`) | RENDER-PATH | yes | **CONVERTED** — exports `PGAI_GIT_LOCAL_ONLY=1` |
| `show-progress.sh` | (subprocess consumer of data.sh) | no direct git call | NO-GIT (covered transitively) | n/a | none — calls `data.sh` as a subprocess; `data.sh`'s own guard covers it |
| `show-queues.sh` | drill-N | no direct git call | NO-GIT (covered transitively) | n/a | none — calls `data.sh` as a subprocess; `data.sh`'s own guard covers it |
| `show-status-window.sh` | main (Window 0 full-screen status) | **yes** (`get_kanban_version` directly; also invokes `data.sh` as subprocess) | RENDER-PATH | yes | **transitively covered** — `get_kanban_version`'s network call was removed unconditionally in `lib/version.sh`; `data.sh`'s own guard covers the subprocess call. No direct edit needed in this file. |
| `status-bottom.sh` | tmux status-format[1] (bottom status bar) | **yes** (`pp_last_released_version`, `get_kanban_version` x2) | RENDER-PATH | yes | **CONVERTED** — exports `PGAI_GIT_LOCAL_ONLY=1` |
| `status-right.sh` | tmux status-format (right side) | no | NO-GIT | — | none |
| `training-logs.sh` | training-logs | no | NO-GIT | — | none |
| `verify-window0-geometry.sh` | (verification script, not a live pane) | no | NO-GIT | — | none |
| `visibility.sh` | 2:visibility (orchestrates column-render.sh) | no direct git call | NO-GIT (covered transitively) | n/a | none — delegates to `column-render.sh`, which is converted |

---

## Detailed site-by-site breakdown

### A. Sites converted in this ticket

#### A1. `team/scripts/dashboard/attention.sh` (Window 4: Attention)

Sources `team/scripts/lib/discovery.sh`, which defines
`discovery_scan_stale_requirements` (called at attention.sh's "STALE
REQUIREMENTS" section) and `_disc_git_tag_exists` (called transitively via
`discovery_compute_next_patch`, not on this render path, but shares the
same env-guarded helper). `discovery_scan_stale_requirements` calls
`pp_last_released_version`, which performed an unconditional
`git fetch origin --tags --quiet` before this ticket.

**Fix:** `export PGAI_GIT_LOCAL_ONLY=1` added immediately before the
`source .../lib/discovery.sh` line (line ~141), so every git-touching
helper `discovery.sh` defines or calls inherits the flag from process
start.

**Missing-tree behavior:** SUPPRESSION-WITH-NARRATION. When the dev tree
is absent/invalid, `pp_last_released_version` falls back through its
existing tiers to the `v0.0.0` sentinel (see A-shared below); the STALE
REQUIREMENTS section already renders "(no stale requirements)" gracefully
in that case — no hang, no prompt, no stack trace.

#### A2. `team/scripts/dashboard/data.sh` (subprocess of 6 other scripts)

Calls `get_kanban_version` (line 185, via `lib/version.sh`, fixed at the
library level — see A7) and `pp_last_released_version` directly (line
215) for the `LAST_RELEASED` key.

**Fix:** `export PGAI_GIT_LOCAL_ONLY=1` added immediately after sourcing
`../lib/project_paths.sh` (line ~103).

**Missing-tree behavior:** SUPPRESSION-WITH-NARRATION. `LAST_RELEASED`
falls back to the string `none`, which is documented as a valid output
value in the script's own header comment and is rendered as-is by every
downstream consumer (`show-header.sh`, `show-status-window.sh`,
`show-multi.sh`) — a clearly-labeled placeholder, not a crash.

#### A3. `team/scripts/dashboard/metadata.sh` (Window 5: Metadata)

Calls `pp_last_released_version` per registered project (line 261) to
render the "Registered projects" table's Last Released column.

**Fix:** `export PGAI_GIT_LOCAL_ONLY=1` added immediately after sourcing
`../lib/project_paths.sh` (line ~78).

**Missing-tree behavior:** SUPPRESSION-WITH-NARRATION. Falls back to the
`v0.0.0` sentinel per project row; the row still renders with that value
rather than omitting the project or hanging.

#### A4. `team/scripts/dashboard/show-header.sh` (main window header; also
used by every drill-N window)

Calls `pp_last_released_version` directly (line 180, `DISPLAY_VERSION`)
and invokes `data.sh` as a subprocess (line ~127, after the edit).

**Fix:** `export PGAI_GIT_LOCAL_ONLY=1` added immediately after sourcing
`../lib/project_paths.sh` (line ~45). Because this export happens before
the `data.sh` subprocess invocation later in the file, the exported flag
is also inherited by that child process, reinforcing (not depended upon,
since `data.sh` sets its own copy of the same guard) `data.sh`'s own
guard.

**Missing-tree behavior:** SUPPRESSION-WITH-NARRATION. `DISPLAY_VERSION`
falls back to `v0.0.0`, rendered inline as `pgai-agent-kanban v0.0.0` —
self-evidently a placeholder value, not a crash.

#### A5. `team/scripts/dashboard/status-bottom.sh` (tmux status-format[1],
the bottom status bar line — refreshes on every tmux status-interval tick)

Calls `get_kanban_version` twice (`FRAMEWORK_VERSION` line 144-ish and
`VERSION` in the per-project branch) and `pp_last_released_version` once
(`PROJECT_VERSION`, line ~168).

**Fix:** `export PGAI_GIT_LOCAL_ONLY=1` added immediately after sourcing
`../lib/project_paths.sh`, `../lib/projects.sh` (line ~105). This is the
highest-frequency render path in the whole dashboard (tmux re-invokes
status-format scripts on its configured `status-interval`), so this was
the single most latency-sensitive site to convert.

**Missing-tree behavior:** SUPPRESSION-WITH-NARRATION. All three values
fall back to sentinels (`unknown` / `v0.0.0`) that render inline in the
status bar without altering its layout or blocking the bar's refresh
cycle.

#### A6. `team/scripts/dashboard/show-multi.sh` (multi-project stacked
overview header, used when more than one project is registered)

Calls `get_kanban_version` (line 180, framework version) and
`pp_last_released_version` per registered project (line 231, per-project
"Last Released" row).

**Fix:** `export PGAI_GIT_LOCAL_ONLY=1` added immediately after sourcing
`../lib/project_paths.sh` (line ~58).

**Missing-tree behavior:** SUPPRESSION-WITH-NARRATION. Falls back to
`v0.0.0` per project row, consistent with `metadata.sh` and
`show-header.sh`.

#### A7. `team/scripts/dashboard/lib/version.sh` (shared helper — no
window of its own; sourced by `data.sh`, `show-header.sh`,
`show-status-window.sh`, `show-multi.sh`, `status-bottom.sh`)

`get_kanban_version`'s Tier 3 branch performed an UNCONDITIONAL
`git -C "$repo_root" fetch origin --tags --quiet` before calling
`get_latest_released_tag` — this is the network call ("lib/version.sh
no longer performs the `git fetch origin --tags` network call on the
render path").

**Fix:** the fetch line is REMOVED, not flag-gated — this library is
dashboard-render-only (never sourced by agent/wake-context code), so
there is no legitimate caller that needs the network fetch preserved.
`get_latest_released_tag` itself already only reads
`git tag --merged origin/main`, a purely local ref comparison (no
network) against whatever `origin/main` already resolves to on disk.

**Missing-tree behavior:** SUPPRESSION-WITH-NARRATION (unchanged from
before this ticket — Tier 3 already returned empty string on any
failure, falling through to Tier 4/Tier 5 = `"unknown"`).

#### A8. `team/scripts/dashboard/column-render.sh` (visibility window,
operator window, and every drill-N window's requirements column)

Calls `pp_last_released_version` in two places: the `--all-projects`
requirements-column branch (line ~460) and the single-project
requirements-column branch (line ~1383).

**Fix:** `export PGAI_GIT_LOCAL_ONLY=1` added immediately after sourcing
`../lib/project_paths.sh` (line ~81).

**Missing-tree behavior:** SUPPRESSION-WITH-NARRATION. Both call sites
already strip the `v0.0.0` / `none` sentinel before handing the value to
the Python renderer (`is_shipped()` treats an empty string as "not
shipped"), so a missing tree degrades to "this requirement has not
shipped yet" — a pre-existing, correct rendering, not a new behavior
introduced by this ticket.

#### A9. `team/scripts/lib/project_paths.sh` — `pp_last_released_version`
(shared framework library, NOT dashboard-only — also used by
agent/wake-context code such as `discovery.sh`'s
`discovery_compute_next_patch` and `discovery_step_requirements`, which
legitimately need network reachability to origin)

This is the single function all eight render-path sites above (A1-A6,
A8) ultimately call. It performed an unconditional
`git -C "$dev_tree" fetch origin --tags --quiet` before falling through
its tiered tag-resolution logic.

**Fix:** the fetch call is now gated behind `PGAI_GIT_LOCAL_ONLY`
(checked immediately before the fetch, mirroring the pattern already
established in `discovery.sh`'s `_disc_git_tag_exists` from ticket 2):
when the flag is unset (the default — every agent/wake-context caller),
behavior is byte-for-byte unchanged. When a render-path caller has
exported the flag, the fetch is skipped and resolution proceeds directly
to the existing local-tag tiers (origin/`<main_branch>` local
remote-tracking ref → local `<main_branch>` branch → all local tags →
`v0.0.0` sentinel).

This is the correct scope for the guard: it is a SHARED library function,
so gating it centrally (rather than duplicating the fetch-skip logic in
every one of the eight call sites) is both DRY and matches the existing
precedent set by ticket 2's `_disc_git_tag_exists` guard.

---

### B. Sites classified as 4:git window (conversion follow-up, CODER-20260726-004)

#### B1. `team/scripts/dashboard/git-status.sh`

Renders per-project git status (branch, ahead/behind, working-tree
status, RC branches, recent commits, recent tags) for every
release-workflow project. Audited line-by-line for network-capable verbs
(`fetch`, `pull`, `push`, `clone`, `ls-remote`) — **zero hits**. All git
invocations are `git -C "$dev_tree" <local-subcommand>`:
`rev-parse --git-dir`, `symbolic-ref`, `rev-parse --verify`, `rev-list`
(comparing two already-resolved local refs, including the
`origin/<branch>` remote-tracking ref — reading a cached ref is not a
network operation), `status --porcelain`, `branch --list`, `log`,
`tag --list`.

**Missing-tree behavior:** CLEAR-ERROR RENDER, unified to the design-law
wording: `"git tree not available: <path>"` (all three degrade
branches — unconfigured, missing directory, not a git repository — now
render this string family).

**Classification:** 4:GIT-WINDOW. No network-related code change was
needed (this script was already local-only).

#### B2. `team/scripts/dashboard/git-recent-tags.sh`

Renders the 10 most recent tags for each release-workflow project (the
`4:git` window's right pane). Audited identically — **zero
network-capable hits**. Same local-only `git -C "$dev_tree" tag --list`
/ `git -C "$dev_tree" log -1` pattern as B1.

**Missing-tree behavior:** CLEAR-ERROR RENDER, unified to the design-law
wording: `"git tree not available: <path>"` (same treatment as B1).

**Classification:** 4:GIT-WINDOW. No network-related code change was
needed (this script was already local-only).

---

### C. Sites that were false positives or already zero-hit

- `project-toggle.sh` — the only `grep "git"` hit is the substring `git`
  inside the English word "digits" in a comment describing the
  `drill-<digits>` filename pattern. No git command exists in the file.
- All other 22 files listed as NO-GIT in the summary table above returned
  zero hits across every grep pass (Pass 1 through Pass 3) and were
  additionally read in full or in relevant part to confirm no git
  invocation is constructed dynamically (e.g. via `eval`, string
  concatenation into a command, or an indirect function call) anywhere
  in those files.

---

## Missing-tree behavior policy (applies to all A-series sites)

Every A-series site (A1-A9) relies on the SAME underlying resolution
chain: `pp_last_released_version` / `get_kanban_version` /
`get_latest_released_tag`. All three already contained defensive checks
(`[[ -d "${repo_root}/.git" ]]`, `git -C "$dev_tree" rev-parse --git-dir`)
BEFORE this ticket that make them return a safe sentinel
(`v0.0.0` / `unknown` / `none`) rather than hang, prompt, or crash when
the dev tree is missing or invalid. This ticket's job was to make sure
those functions never perform a NETWORK operation on the render path —
it did not need to (and does not) change what happens when the tree is
simply absent, because that path was already safe.

**Design choice, stated explicitly (not blocking DONE):** the chosen
missing-tree behavior for every A-series site is
SUPPRESSION-WITH-NARRATION via a labeled sentinel value (`v0.0.0`,
`unknown`, `none`) rather than a dedicated "git tree not available: X"
banner per site. This was a deliberate choice to avoid rewriting eight
render functions' existing, already-tested fallback plumbing beyond what
was require (remove the network call). Every rendered sentinel is
self-evidently a placeholder to an operator reading the pane (e.g.
"Last Released: none", "v0.0.0", "vunknown"), satisfies the acceptance
criterion ("clear error render OR suppression with narration — never
prompt, hang, or stack-trace"), and was verified behaviorally (see
Verification below) to not hang or error when the dev tree path is
invalid. If a future iteration wants per-site "git tree not available:
&lt;path&gt;" banners instead of sentinel values, that is a cosmetic
follow-up, not a functional gap.

---

## Verification performed

All verification commands were run inside the task worktree,
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260726-003-dashboard-census-local-only`,
with a `git` PATH-shadowing stub script that logs any invocation of
`fetch`/`ls-remote`/`pull`/`push`/`clone` before delegating to the real
`git` binary (network-stub fixture).

1. **`bash -n` on every modified file** — all 9 files pass with exit 0.

2. **Guard-fire test on `pp_last_released_version`** — called once with
   `PGAI_GIT_LOCAL_ONLY` unset (baseline) and once with it set to `1`.
   Baseline recorded one `NETWORK-CALL: git ... fetch origin --tags
   --quiet` line; guarded run recorded zero network calls, and both runs
   returned the identical correct version (`v1.26.24`) from local tags.

3. **Guard-fire test on `get_kanban_version`** (`dashboard/lib/version.sh`)
   — zero network calls recorded (fetch line removed unconditionally).

4. **End-to-end run of `attention.sh --kanban-root
   /home/rocky/pgai_agent_kanban`** under the network-stub PATH — exited
   0, rendered the full BLOCKED/TRANSIENT/STALE/QUARANTINE/HALTED/STALE
   REQUIREMENTS/PENDING APPROVALS/OVERWATCH sections, zero network calls
   recorded.

5. **End-to-end runs of `data.sh --project pgai-agent-kanban`,
   `show-header.sh --kanban-root ...`, and `status-bottom.sh
   /home/rocky/pgai_agent_kanban pgai-agent-kanban`** under the
   network-stub PATH — all exited 0, rendered correct version/status
   output (`v1.26.24`, `pgai-agent-kanban v1.26.24`, the tmux status-bar
   line), zero network calls recorded.

6. **Missing-tree fixture** — `pp_last_released_version` called with a
   nonexistent project name and `get_kanban_version` called with
   nonexistent kanban-root/repo-root paths, both wrapped in `timeout 10`
   as a hang-detection safety net — both returned instantly (`v0.0.0`
   and `unknown` respectively) with exit 0, no hang, no prompt.
   `data.sh` and `attention.sh` were additionally re-run with
   `PGAI_DEV_TREE_PATH` pointed at a nonexistent directory — both exited
   0 with zero network calls.

No test run in this verification pass triggered a network call, a hang,
a prompt, or a non-zero exit code.
