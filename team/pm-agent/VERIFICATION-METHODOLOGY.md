# Unified Verification Methodology

**Purpose:** define the canonical "how Claude verifies an RC against its requirements doc" workflow. This is the human-proxy pattern a TESTER subagent follows. It is the source of truth for what a TESTER subagent should do — comparing this document against the TESTER role file reveals any gaps.

**What this document is NOT:** an enforcement policy or pass/fail rubric. It's a *workflow*. The agent applies judgment within the workflow. Some of the most valuable findings come from the tester *reading* the written material carefully — bugs in PO, CODER, and WRITER subagent paths that don't fit any structural or executable check. Judgment is part of the methodology, not an escape from it.

---

## Core Principles

These five principles anchor every verification:

**1. Cheapest checks first, most expensive last.**
Cost gradient: structural (`ls`, `find`) → grep-and-read → executable runtime → synthetic-input edge cases → judgment-based document review. Run them in that order so failures surface fast and you don't burn time on semantic analysis of obviously broken work.

**2. Every check must trace back to the requirements doc.**
Deliverables, acceptance criteria, constraints. If a check doesn't trace back to one of those, it's scope creep — drop it. The exception: **spot-checks outside the spec for state drift, untested new mechanisms, regressions of prior gaps.** These are explicitly judgment-based and recurringly valuable.

**3. New files = read fully. Modified files = grep to changed region.**
Cost gradient applied to the deliverables list. New files are a single coherent change to be understood; modified files are diffs to be located. The autonomous tester branches its strategy based on which kind it's looking at.

**4. Verify behavior, not just presence.**
Structural checks ("does the file exist?") miss runtime bugs. When a fix is supposed to *prevent* something, *exercise the prevention* — set up the failure scenario, run the protected operation, verify the protection fired correctly without false positives. This principle catches false-positive guards — for example a PID lock that refuses on every first invocation — which would ship if structural checks alone were used.

**5. Recommendation is unambiguous.**
SHIP, WAIT, or BLOCK. If "ship with follow-up," enumerate the follow-up tickets explicitly. The tester's job is to make a clean call; the human's job is to act on it.

---

## The 12-Step Workflow

This is the canonical order. Steps may take seconds or minutes depending on RC scope; the order is what's invariant.

### Step 0 — Ingest

- Receive the RC as a zip / git checkout / branch reference
- Locate the requirements doc that drove the RC
- If a previous TESTER report exists in the working tree (e.g., `team/templates/agent/REPORT-TEMPLATE.md`-shaped artifact), note its existence but do not yet read it — read fresh first

**Output:** working tree extracted, requirements doc identified.

### Step 1 — Read the requirements doc carefully

Build a verification checklist from:
- **Deliverables** — what files should exist or have changed
- **Acceptance criteria** — testable conditions, often expressed as commands
- **Constraints** — rules that must have been followed even if deliverables look right (often negative: "must NOT contain X" or "must preserve Y")

The checklist is your scope. Everything you check should trace back to one of those three sources.

**Output:** verification checklist (mental or written) with each item tagged Deliverable / Criterion / Constraint.

### Step 2 — Sanity check release-state.md and Last Released

Independent of the requirements doc. Cheap drift check.

The schema for `release-state.md` holds only three fields: `Active RC`, `RC Opened At`, `RC Opened By Task`. Last Released is derived from git tags via `pp_last_released_version` — there is no `Last Released` field stored in any file, so the "release-state vs. git tag drift" failure mode is gone by construction.

What's still worth checking:

- Resolve canonical Last Released via `pp_last_released_version "<project-name>"`. By definition this matches a git tag on `origin/main`, or returns `v0.0.0` for fresh systems — no extra verification step is needed.
- Inspect the project's `release-state.md` at `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<project-name>/release-state.md`. Confirm:
  - It contains `Active RC`, `RC Opened At`, `RC Opened By Task` and only those fields.
  - Stale `Last Released*` fields from an older install were migrated cleanly. If they remain, the install.sh migration did not fire — flag as drift.
  - If `Active RC` is set, the RC branch actually exists. Mismatch is drift (e.g., `Active RC: vX.Y.Z` set but no `rc/vX.Y.Z` branch).

**Output:** drift status (clean / drift detected with notes).

### Step 3 — Cheap structural checks

Confirm catastrophic-failure scenarios didn't happen. Use `ls`, `find`, `wc -l`, `cat | head`. No code inspection yet.

- Does the expected directory layout exist?
- Are new files present at expected paths with non-trivial content (not zero-byte stubs)?
- Are migrations actually migrated (old path gone, new path present)?
- Are scripts executable when they need to be?

If structural checks fail catastrophically, you can often skip semantic checks because the work is obviously incomplete. **Fail fast.**

**Output:** pass/fail per deliverable's structural existence.

### Step 4 — Deep file inspection

For **new files:** read top to bottom. There's no diff to look at — the whole file is the change. Check:
- Does the structure match what the spec asked for?
- Are there obvious bugs (typos, wrong variable names, missing quoting)?
- Are bash safety conventions honored (strict mode, cleanup trap, source order)?
- Does the script's actual logic match the spec's *behavior* description, not just match it word-for-word?
- **Do file paths, queue locations, task ID formats match the rest of the system's conventions?** (Wrong-queue-path bugs in subagent prompts are a recurring class this check catches.)

For **modified files:** grep to the changed region per the requirements doc and view the relevant range. Don't read the whole file unless the change is invasive.

**Particular care for documentation-layer files** (subagent prompts, role files, templates): READ THE PROMPT FULLY. Errors in these files don't trigger executable tests but cause runtime failures the first time the agent uses them. Failure shapes that escape structural checks alone:
- A subagent prompt referencing the wrong queue path
- A subagent prompt with a wrong path AND a broken sed substitution

**Output:** findings per file with line references for any concerns.

### Step 5 — Run executable acceptance criteria

If the requirements doc has acceptance criteria expressed as commands, run them. Set up a throwaway test directory, set the env var, execute the commands, observe the output.

Common patterns:
- Script with no arg → usage error, exit non-zero
- Script with malformed arg → format error, exit non-zero
- Script with valid arg in synthetic env → expected behavior

For Python files: `python3 -m py_compile <file>`.
For Bash files: `bash -n <file>`.

When tests are present (`team/scripts/run-unit-tests.sh`, `team/scripts/run-integration-tests.sh` if they exist):
- Install pytest if missing (`pip install pytest --break-system-packages`)
- Run unit tests, capture exit code
- Run integration tests, capture exit code
- Note in report: count passed, count failed

**Output:** pass/fail per executable criterion, with output captured for failures.

### Step 6 — Trigger failure modes (for new behavioral logic)

When the RC adds logic that's supposed to *prevent* something, actively try to trigger the prevention:

- Concurrency lock supposed to prevent double-run? Run two instances and verify second exits cleanly.
- Stale-state cleanup supposed to recover gracefully? Create the stale state and verify cleanup fires.
- Refusal-to-proceed supposed to fire on bad input? Construct the bad input and verify refusal.

Verify both:
- The protection fires when it should
- The protection does NOT fire when it shouldn't (no false positives)

This is the step that catches false-positive guards — for example a PID lock where `pgrep -f` matches the parent shell's argv, causing the script to refuse on every first invocation.

**When verifying a bug fix specifically:** synthesize the original failure mode AND the new normal case. Don't just verify the fix is present in the code — verify it works AND that it didn't break the case that was working.

**Output:** behavioral-correctness findings.

### Step 7 — Spot-test with synthetic inputs

Feed scripts inputs that DON'T match production-shape data:

- Materializer fed plan JSON missing optional fields
- Wake script fed task README missing optional fields
- Cleanup script fed nearly-empty filesystem

This finds latent bugs in how scripts handle the unexpected — for example a `topological_order` `KeyError: 'sequence'` that never fires in production (PM always includes sequence numbers) but crashes on synthetic input.

**Output:** latent-fragility findings.

### Step 8 — Constraint negative checks

For every constraint expressed as "must NOT contain X," issue a `grep -rn` across the working tree.
For every constraint expressed as "must preserve Y," issue a `grep -n` against the modified files.

Don't trust that the agent honored constraints; verify them. Examples that catch real issues:
- `grep -rn "<retired-name>"` to confirm a renamed component leaves no stale references
- Queue-path constraint check on subagent prompts (catches the recurring wrong-queue-path bug class)
- Bash strict mode + cleanup trap presence in every modified script

**Standard regression checks** to run on every RC:
- Previously-closed gaps still closed (no stale references to retired names)
- All bash scripts have `set -euo pipefail` and `cleanup_on_exit` trap
- All Python files compile with `python3 -m py_compile`
- All bash files pass `bash -n`
- Any previously-fixed bugs still fixed

**Output:** constraint-pass/fail list, with regression-check status.

### Step 9 — Prereq dependency graph verification

For RCs that materialize a multi-task pipeline:
- No cycles in the dependency graph
- All referenced task IDs exist
- Lifecycle bookends correctly linked (CM-open at start, TESTER + optional HUMAN-APPROVE + CM-release at end)
- Sequence numbers consistent and non-colliding

**Output:** graph soundness verdict.

### Step 10 — Spot-check outside the spec

Apply judgment. This is where the most consequential findings land — the ones a checklist-only tester would miss.

Categories that recurringly produce real findings:
- **State drift between files** (e.g., `Active RC` in `release-state.md` vs the actual rc branch state, queue files vs task folders)
- **Untested new mechanisms** — when an RC adds a mechanism but doesn't exercise it (the mechanism may have a latent bug that won't surface until later)
- **Regressions of prior gaps** — known issues from prior RCs creeping back
- **Documentation-layer drift** — comments and docstrings still describing old behavior
- **Convention violations** — file paths, naming patterns, ID formats inconsistent with rest of system

A "scope-creep" finding is one that doesn't trace to deliverables/criteria/constraints AND doesn't surface from the categories above. Drop those. Findings from the listed categories are not scope creep — they're the value-add the tester provides over a checklist runner.

**Output:** judgment-based findings list.

### Step 11 — Categorize findings

Sort all findings into:

- **Pass** — requirement fully met
- **Pass with caveat** — met, but worth noting (implementation detail differs from spec, etc.)
- **Gap** — requirement not fully met, can ship with follow-up
- **Bug** — something that wasn't asked for got introduced (or pre-existing bug surfaced)
- **Block** — broken in a way that prevents the next RC from proceeding

Each finding should reference the specific deliverable, acceptance criterion, or constraint it relates to (back-link to the requirements doc). Findings without back-links are scope creep UNLESS they came from Step 10's listed categories.

**Output:** categorized findings list.

### Step 12 — Write the report; make a recommendation

Use REPORT-TEMPLATE.md if present. Required sections:
- Executive Summary (2-5 sentences, plain language)
- Acceptance Criteria (table form, pass/fail per item)
- Constraint Verification (table form, pass/fail per constraint)
- Bugs (with file path + line numbers + suggested fix)
- Spot Checks Beyond the Spec (the Step 10 findings)
- **Recommendation: SHIP | WAIT | BLOCK** (the parseable decision line)

The recommendation is unambiguous:
- **SHIP** — pass, or pass-with-known-bugs that go in release notes' "Bugs Skipped" section
- **WAIT** — bugs serious enough that shipping creates worse problems than waiting
- **BLOCK** — build is broken in a way that prevents release (catastrophic only)

If gaps were found:
- Write `gaps.md` artifact with unmet requirements + diff references
- Write a priority requirements doc to `${PGAI_AGENT_KANBAN_ROOT_PATH}/projects/<project_name>/requirements/priority/v<X.Y.Z+1>-bugfix-<short>.md` (the runtime location discovery's Step 2 scans)
- Recommendation may still be SHIP — bugs in release notes, picked up next cycle

Mark task DONE.

---

## Cost Profile

Time spent in each step varies with RC scope:

| Step | Typical Time | Range |
|---|---|---|
| 0 — Ingest | 1-2 min | always |
| 1 — Read requirements | 3-5 min | always |
| 2 — Drift check | 1-2 min | always |
| 3 — Structural | 2-5 min | scales with deliverable count |
| 4 — Deep inspection | 5-15 min | dominated by new-file count and document-review depth |
| 5 — Executable acceptance | 5-15 min | scales with criterion count |
| 6 — Failure-mode triggers | 0-15 min | only when RC adds preventive behavior |
| 7 — Synthetic input | 0-10 min | only when scripts have user-input contracts |
| 8 — Constraint checks | 3-5 min | always |
| 9 — Dependency graph | 2-5 min | only for multi-task pipelines |
| 10 — Spot-check | 5-10 min | always — high-value time |
| 11 — Categorize | 3-5 min | always |
| 12 — Write report | 10-15 min | always |

**Total:** 40-60 minutes per RC verification. Smaller, documentation-only RCs trend toward the lower end; behaviorally substantive RCs that add or change runtime logic trend toward the higher end.

---

## Tool Requirements

The autonomous TESTER subagent needs:
- **Read, Write, Edit** — for inspecting and creating artifact files
- **Bash** — for git, find, grep, running scripts, py_compile, bash -n
- **Glob, Grep** — fast pattern matching across the tree
- **Python via Bash** — `python3 -m py_compile`, running pytest, importing materializer modules for unit-style tests

What the TESTER probably can't have:
- **The Claude CLI itself** — circular invocation. If a check requires actually invoking another subagent, accept the limit and document it as "structural check only — runtime not exercised."

---

## Anti-Patterns to Avoid

These are mistakes that ship bad RCs:

1. **Trusting structural checks for behavioral logic.** "The fix is in the code" is not the same as "the fix works." Always exercise behavior for new preventive logic.

2. **Skipping close reading of new subagent prompts.** The wrong-queue-path bug family escapes structural and executable checks entirely. Only careful reading of the prompts catches it.

3. **Assuming synthetic input will match production input.** Materializer plans without `sequence` field; wake-script invocations with empty queue; cleanup scripts run on near-empty filesystems. Production-shape inputs hide bugs that synthetic inputs reveal.

4. **Letting "scope creep" exclude valuable judgment-based findings.** Spot-checks outside the spec consistently surface real issues (state drift, untested mechanisms, regressions). The principle is "trace back to requirements OR Step-10 categories" — not "trace back to requirements only."

5. **Ambiguous recommendations.** "Mostly passes, mostly ships" is not a decision. SHIP / WAIT / BLOCK. Bugs go in release notes or in priority requirements docs for next cycle.

6. **Verifying only the new normal case after a bug fix.** Always synthesize the original failure mode AND the new normal case, then verify both.

---

## What This Methodology Doesn't Cover

Honestly out of scope:
- **Performance / load testing.** The kanban runs at 1 task per ~5-10 minutes; performance is not a concern.
- **Security audits.** No untrusted input boundary in the system; the human is the only input source.
- **Cross-platform compatibility.** Linux + bash 4 is the only target.
- **Requirements quality review.** TESTER verifies the RC against its requirements doc; whether the requirements doc was wise is a PO/human question, not a TESTER question.

If any of these become real concerns later, the methodology grows to include them. None of them are currently blocking.

---

## Keeping the TESTER Role File Aligned

This document is the source of truth for the verification workflow. The TESTER role file (`roles/TESTER.md`) and the TESTER subagent prompt must encode the same workflow. When the two drift, this document wins. Points that are easy to under-weight in the role file and must stay explicit there:

- **Steps 6 (failure-mode triggers) and 7 (synthetic input)** are high-value and must not be reduced to a structural presence check.
- **Step 10 (spot-check outside spec)** gives the autonomous tester explicit license to apply judgment on state drift, untested mechanisms, and convention violations.
- **Close reading of new subagent prompts** is required — the wrong-queue-path bug family is invisible to structural and executable checks.
- **The "trigger failure mode AND normal case" pattern** for bug-fix verification must be encoded, not assumed.
