# INTENT.md — Declared Operator Intent

## What this file is for

This is this project's ledger of deliberate, operator-authored decisions
about its state. OVERWATCH's Lane B reads it when a CM release is
HALTed: if the HALT's cause is fully explained by an entry below,
OVERWATCH may ship past the HALT with that entry's text as the release's
`--reason`, instead of escalating to Needs Human — see
`team/roles/OVERWATCH.md`, "Autonomous Block Resolution".

An entry here authorises an automated action. Write it as a statement
you are prepared to stand behind, not a passing comment.

No entry, no force — EVER, from OVERWATCH's own judgment. This file
ships with zero live entries below, and OVERWATCH takes no autonomous
action from an empty ledger. Autonomous resolution is also gated on
this project's `overwatch_resolve` key in `project.cfg` — see that
file's `[release]` section — so an empty ledger AND a still-commented
`overwatch_resolve` key both have to change before Lane B can act.

## Entry format

Each live entry is its own line under "## Entries" below, in the form:

    - YYYY-MM-DD: <declaration text>

- `YYYY-MM-DD` is the date the operator made the decision (not
  necessarily the date this file was edited).
- `<declaration text>` names the state a verifier would otherwise flag
  and explains why it is deliberate, in enough detail that the match
  test — "the HALT's evidence is fully explained by the declaration" —
  can be judged from this line alone.
- One entry per line. No continuation lines, no nested bullets.

## Worked example (NOT a live entry)

The block below is fenced as a code sample specifically so it is never
read as a live entry — a live entry only counts when it is an
unfenced bullet line under "## Entries" below. Copy the shape, not the
fence, when adding a real entry:

```
- 2026-01-01: EXAMPLE ONLY — ai_main rebased to v2.4.0 — hindsight
  experiment; lineage preserved on last_run_ai_main.
```

## Entries

<!-- Add real, dated entries below this line — one "- YYYY-MM-DD: ..."
     bullet per decision, outside of any code fence. Nothing below this
     line yet: zero live entries is the shipped state. -->
