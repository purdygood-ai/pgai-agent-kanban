# BUG-0002-no-color-change-power-pellet

## Status
open

## Severity
low

## Component
pgai-chomp-man.py (Chomp-Man sprite rendering during the power-pellet / edible-dragon
state)

## Symptom
When Chomp-Man eats a power pellet and gains the ability to eat dragons (the
edible-dragon timer is active), the dragons change to their fleeing/edible
appearance, but Chomp-Man's own sprite color does not change to signal the
powered-up state. The player has no visual cue on the Chomp-Man character itself
that the dragon-eating window is active.

## Expected
While the power-pellet (dragon-edible) timer is active, Chomp-Man's sprite should
render in a distinct color (a powered-up hue) so the player can tell at a glance
that dragons are currently edible. When the timer expires, Chomp-Man returns to
its normal color.

## Reproduction
1. Run pgai-chomp-man.py.
2. Eat a power pellet (dragons enter their edible/fleeing state).
3. Observe Chomp-Man: its color stays the same as normal play, with no powered-up
   visual indication.

## Fix
In the Chomp-Man render path, when the power-pellet / edible-dragon timer is
active, draw Chomp-Man in a distinct powered-up color; revert to the normal color
when the timer is inactive. Reuse the existing timer/state that already drives the
dragons' edible appearance — do not introduce a separate timer.

## Acceptance Criteria
1. While the dragon-edible timer is active, Chomp-Man renders in a distinct color
   (different from normal play).
2. When the timer expires (or is not active), Chomp-Man renders in its normal
   color.
3. The color change is driven by the SAME state/timer that makes dragons edible
   (no new/parallel timer).
4. `python3 -m py_compile` on the changed file(s) passes.
5. No regression: power-pellet timing, dragon edible appearance, dragon-eating
   scoring, and the board-clearable invariant are unchanged.

## Notes for TESTER
The color change itself is manual visual confirmation. Programmatically: confirm
the render path branches on the existing edible/power-pellet timer state for
Chomp-Man's color (not a new timer), and that the file compiles. Confirm normal
play (timer inactive) still renders the normal color.
