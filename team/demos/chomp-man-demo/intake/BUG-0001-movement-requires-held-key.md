# BUG-0001-movement-requires-held-key

**Bug ID:** BUG-0001-movement-requires-held-key
**Filed By:** operator (Rocky)
**Severity:** medium

## Status
open

## Symptom
Player movement is one step per keypress: to keep moving you must repeatedly press and release a
direction key. Holding a direction key down does NOT keep the player moving — the character takes a
single step and stops until the key is pressed again. This makes the game feel broken / unresponsive
compared to a normal arcade maze game.

## Expected
While a direction key is HELD DOWN, the player moves continuously in that direction, one step/tile per
movement tick, and keeps moving as long as the key remains held AND the path ahead is clear. Movement
stops immediately when (a) the player hits a wall, or (b) the key is released. (This is "hold-to-move",
not Pac-Man-style "tap once and persist until the next turn" — releasing the key stops the player.)

## Actual
Input is handled as discrete keypress EVENTS (one move per key-down event), so continuous holding does
not produce continuous motion. The player only advances on each fresh key press, so sustained movement
requires rapid repeated tapping. The held state of the key is not polled each frame/tick.

## Reproduction
1. Start the game.
2. Press and HOLD a direction key (e.g. Right) with a clear path ahead.
3. Observe: the player moves one tile and stops, despite the key still being held.
4. Tap the key repeatedly — the player advances one tile per tap, confirming it is event-driven, not
   held-state-driven.

## Files Involved
- The input/keyboard handling and the main game/update loop (wherever key events are read and player
  position is advanced). Likely an event handler that moves on key-down only, rather than a per-frame
  check of "is this direction key currently held?".

## Hypothesis (optional)
Fix direction (do not implement here): switch from event-driven single-step movement to per-tick
held-key polling. Maintain a set/flags of currently-held direction keys (set on key-down, clear on
key-up). Each movement tick, if a direction key is held and the next tile in that direction is not a
wall, advance one tile; if a wall is ahead, hold position (do not error/jitter); if no direction key is
held, the player is stationary. Releasing the key clears the flag so motion stops on the next tick.
Keep the movement cadence tied to the game's tick/frame rate (a fixed step interval) so speed is
consistent regardless of OS key-repeat settings.

## Acceptance
1. Holding a direction key with a clear path moves the player continuously (multiple tiles) without any
   additional key presses.
2. The player stops immediately when it hits a wall (continues to hold position while the key stays
   held against the wall — no jitter, no error).
3. The player stops moving immediately when the direction key is released.
4. Movement speed does not depend on the OS keyboard key-repeat rate (it is driven by the game tick).
5. Changing direction works by holding a different key (and behaves correctly when two keys are held —
   define and document the resolution, e.g. last-pressed wins).

## Severity
medium

Rationale: not a crash, but it makes core gameplay feel broken — continuous movement is fundamental to
a maze game, and tap-to-step is unplayable in practice. High impact on the basic play experience.
