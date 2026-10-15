<!--
GUIDE NOT GATE: This template is a guide, not a gate.
Agents must tolerate imperfect or missing sections.
Use the structure when it helps; skip sections that do not apply.
The Recommendation section value MUST be one of: PASS | SHIP-WITH-CONCERNS | SHIP-WITH-SERIOUS-CONCERNS
Findings must use exactly these categories: pass, pass-with-caveat, gap, bug, stale-assertion.
Per-finding fields for bug/gap/stale-assertion findings:
  Category: pass | pass-with-caveat | gap | bug | stale-assertion
  Fix Effort: small | medium | large
  Systemic Risk: low | medium | high
The top-level Systemic Risk is the MAX across all per-finding Systemic Risk values.
-->

# Verification Report: <task-id or release-id>

**Date:** YYYY-MM-DD
**Tester:** <agent or human name>
**Scope:** <what was tested>

---

## Executive Summary

Brief narrative (2–5 sentences) describing what was verified, how, and the overall outcome. State clearly whether the work is ready to ship.

---

## Acceptance Criteria

Checklist derived from the task or requirements doc. For each criterion, record whether it passed, failed, or was skipped.

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| 1 | `<criterion text>` | PASS / FAIL / SKIP | |
| 2 | `<criterion text>` | PASS / FAIL / SKIP | |

---

## Constraint Verification

List the hard constraints from the task and confirm each was met.

| Constraint | Status | Notes |
|------------|--------|-------|
| `<constraint text>` | MET / VIOLATED / UNTESTABLE | |

---

## Findings

Per-step findings. Each finding must use exactly one of the canonical categories: `pass`, `pass-with-caveat`, `gap`, `bug`, `stale-assertion`. See `team/roles/TESTER.md` Step 7 for category definitions and the stale-assertion classification heuristic.

For findings that are `bug`, `gap`, or `stale-assertion`, include the structured fields below (Category, Fix Effort, Systemic Risk, Filed As). Findings that are `pass` or `pass-with-caveat` may use a single-line note instead.

### Finding 1: <short title>

**Category:** pass / pass-with-caveat / gap / bug / stale-assertion
**Fix Effort:** small (1-2 CODER tasks) / medium (3-5 CODER tasks) / large (architectural, may span multiple RCs)
**Systemic Risk:** low (isolated, RC-specific) / medium (could recur in similar RCs) / high (broader framework regression or stuck CODER pattern)
**Filed As:** BUG-NNNN or PRIORITY-NNNN (Path C: autonomous follow-up filing — non-blocking) — omit for pass/pass-with-caveat findings

<detail, steps, evidence, or "N/A" for pass findings>

### Finding 2: <short title>

**Category:** pass / pass-with-caveat / gap / bug / stale-assertion
**Fix Effort:** small / medium / large
**Systemic Risk:** low / medium / high
**Filed As:** <bug or priority ID, or omit for pass findings>

<detail>

---

## Stale Assertions

Test failures classified as `stale-assertion` per TESTER.md Step 7. If none, write "None found." Each entry must trace the stale literal to the RC change that legitimately altered it; without that trace the finding should have been classified as a real failure (`bug`) instead.

### Stale Assertion 1: <short title>
- **Test file / line:** `<path:line>`
- **Failing assertion:** expected `<literal>`, actual `<literal>`
- **Production code change:** `<file:line>` — literal changed from `<old>` to `<new>`
- **RC commit / task ID:** `<sha or task-id that introduced the change>`
- **Why this is stale, not real:** Brief evidence that the production change is consistent with the RC's requirements doc and the test is the artifact that needs updating.
- **Recommended follow-up:** Update `<test path>` to assert on `<new literal>` (file as Path C-A (filed as BUG) — narrowly scoped).

---

## Bugs

Bugs discovered during verification. If none, write "None found."

### Bug 1: <short title>
- **Severity:** CRITICAL / HIGH / MEDIUM / LOW
- **Description:** What is wrong.
- **Steps to reproduce:** How to trigger it.
- **Expected:** What should happen.
- **Actual:** What actually happens.
- **Recommendation:** Fix before ship / Ship with known issue / Defer

---

## Spot Checks Beyond the Spec

Observations, edge cases, or quality signals noticed during verification that were not part of the formal acceptance criteria. These are informational — not blockers unless elevated.

- `<observation>`
- `<observation>`

---

## Code Coverage

Python code coverage, when collected. Coverage is opt-in: the Step 5a certification runs do not use `--coverage`, and a separate run with it is optional. This section is informational only — it does not feed the Recommendation, the Systemic Risk rating, or the PASS / SHIP-WITH-CONCERNS decision. `coverage unavailable` and `not collected` are both valid values: when coverage was not run, `pytest-cov` is absent, or the measurement failed, record that and move on. It is never a finding or a blocker.

- **Line coverage:** `<NN%>` | `coverage unavailable` | `not collected`
- **Branch coverage:** `<NN%>` | `coverage unavailable` | `not collected`
- **Lowest-covered modules (optional):** `<module — NN%>`, `<module — NN%>`

---

## Slowest tests

Where the suite time goes. **This section is informational only**, like Code Coverage. It does not feed the Recommendation, the Systemic Risk rating, or the PASS / SHIP-WITH-CONCERNS decision. A slow test, or a total slower than last time, is an observation for the reader and not a finding on its own.

The source is the durations record each certification run writes: `$PGAI_AGENT_KANBAN_TEMP_DIR/lint/durations-unit.json` and `lint/durations-integration.json`. Each lists up to 25 tests, slowest first, each taking at least 1.0 s, plus `total_seconds` and `test_count`. Copy the top 10 entries per suite. Fewer than 10 is normal, because only tests of 1.0 s or more are listed. When a suite left no file, write `not recorded`.

**Change since the previous RC.** Compare this run's `total_seconds` with the previous RC's. Use the copy of its durations file saved in that RC's TESTER task `artifacts/` when one exists. Otherwise use the total its report recorded here. When there is neither, write `no previous record`. If `[testing] workers` differed between the two runs, say so beside the change, because the worker count moves the total.

The next certification run overwrites both files. Save copies of this run's two files in this task's `artifacts/`, so the next RC has something to compare against.

### Unit

| # | Test | Seconds |
|---|------|---------|
| 1 | `<test id>` | `<N.NN>` |

- **Total:** `<total_seconds>` s over `<test_count>` tests | `not recorded`
- **Change since previous RC:** `<+/-N.N s (+/-N%)> vs <vX.Y.Z>` | `no previous record`

### Integration

| # | Test | Seconds |
|---|------|---------|
| 1 | `<test id>` | `<N.NN>` |

- **Total:** `<total_seconds>` s over `<test_count>` tests | `not recorded`
- **Change since previous RC:** `<+/-N.N s (+/-N%)> vs <vX.Y.Z>` | `no previous record`

---

## Deep-Verify Freshness

One line is the entire body of this section — no bullet, no sub-heading beneath it. It is a **fact the report states, never a verdict the report produces**: it is not a `## Findings` entry, and it never changes the `## Recommendation` or the `## Systemic Risk` rating. Write it on **every** report for a tree that ships Dockerfiles, including a tree never deep-verified and a tree whose last result was red. **A tree that ships no Dockerfiles omits this section.**

Form A is the placeholder below — the record-exists form. Two other forms replace it: **Form B**, never run (`found=false`), and **Form C**, the ledger is unreadable. `team/roles/TESTER.md` Step 5d is the canon for choosing among the three, for the token rules, and for the one sentence each form owes; do not guess a form from this template alone.

```
deep-verify: found=true verdict=<built | failed-to-build | could-not-attempt> version=<vX.Y.Z> commit=<sha> timestamp=<ISO 8601 UTC> age=<N>d freshness=<fresh|stale> — <one sentence>
```

`verdict` is the ledger's own word, copied verbatim: `built`, `failed-to-build`, or `could-not-attempt`. Never PASS / FAIL.

---

## Recommendation

<!-- Allowed values: PASS | SHIP-WITH-CONCERNS | SHIP-WITH-SERIOUS-CONCERNS
PASS                       — All acceptance criteria pass. No bugs filed. Clean release.
SHIP-WITH-CONCERNS         — Bugs filed but issues are minor; release is usable.
SHIP-WITH-SERIOUS-CONCERNS — Bugs filed and issues are serious enough the release may be unusable.
There is no BLOCK recommendation. BLOCKED is a state (verification couldn't complete), not a recommendation.
-->

**Decision: PASS | SHIP-WITH-CONCERNS | SHIP-WITH-SERIOUS-CONCERNS**

**Rationale:** Explain why this decision was reached. Reference specific criteria, bugs, or constraints as evidence.

---

## Systemic Risk

<!-- The report-level Systemic Risk is the MAX across all per-finding Systemic Risk values.
If any finding has Systemic Risk: high, this field must be high.
If no finding has high but at least one has medium, this field is medium.
If all findings are low (or no bugs/gaps were filed), this field is low.
Allowed values: low | medium | high
Default: low (when no bugs or gaps were filed).
-->

low
