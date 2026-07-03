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

Python code coverage captured from the gated runners (run with `--coverage` in Step 5a). This section is informational only — it does not feed the Recommendation, the Systemic Risk rating, or the PASS / SHIP-WITH-CONCERNS decision. `coverage unavailable` is a valid value: when `pytest-cov` is absent or the measurement failed, record that and move on. It is never a finding or a blocker.

- **Line coverage:** `<NN%>` | `coverage unavailable`
- **Branch coverage:** `<NN%>` | `coverage unavailable`
- **Lowest-covered modules (optional):** `<module — NN%>`, `<module — NN%>`

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
