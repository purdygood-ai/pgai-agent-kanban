# Verification Report: rc/v9.9.9

## Summary

The comment reader now treats a quoted `<!--` as text, and a prose mention of "<!--" too.

## Findings

### Finding 1: a quoted opener above the fields
A reader must not treat `<!--` in inline code as a comment opener.
Nor the bare "<!--" quoted in this sentence.

**Category:** bug
**Fix Effort:** small
**Systemic Risk:** high
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0001

<!-- A real comment hides the line below it.
**Severity:** critical
-->

### Finding 2: a finding after the quoted openers
**Category:** gap
**Systemic Risk:** low
**Filed As:** BUG-0002

## Lint Findings (non-blocking)
A later section that happens to carry a closer --> on its line.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

high
