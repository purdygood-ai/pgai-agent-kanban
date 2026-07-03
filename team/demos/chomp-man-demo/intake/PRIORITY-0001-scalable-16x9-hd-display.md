# PRIORITY-0001-scalable-16x9-hd-display

## Status
open

## Title
Render the game in a resizable 16:9 window at HD scale (floor 1280x720), crisp pixel scaling

## Description
The game currently renders in a small fixed-size window that looks tiny and dated on a modern monitor.
It should present at HD scale and be comfortably sized on current displays. Rather than hardcode one or
two fixed resolutions, the window should be a resizable 16:9 surface: the playfield scales to fill the
window while preserving aspect ratio, with a minimum (floor) of 1280x720 and a sensible default of
1920x1080. The pixel art must scale crisply (nearest-neighbor / integer scaling) so it stays sharp and
retro rather than blurry.

Goal: looks good on a modern monitor, sized like a real game, and the player can resize the window to
taste without distorting the art.

## Suggested Decomposition
1. Replace the fixed window dimensions with a resizable 16:9 window. Default size 1920x1080; enforce a
   minimum size of 1280x720 (do not allow the window to shrink below the floor).
2. Letterbox/pillarbox or constrain resizing so the rendered playfield always keeps its aspect ratio
   (no horizontal/vertical stretch distortion) as the window is resized.
3. Scale the playfield/sprites to fill the window using nearest-neighbor (or integer) scaling so pixels
   stay sharp — explicitly avoid smoothing/bilinear blur on the pixel art.
4. Keep the game logic resolution-independent: the maze grid and movement operate in game/tile units;
   only the render layer scales. (Movement, collisions, timing must not change with window size.)
5. (Optional) Expose the default/initial size via a simple setting if trivial; not required.

## Acceptance Criteria
1. On launch the window is 16:9 and at least 1280x720 (default 1920x1080); it cannot be resized smaller
   than 1280x720.
2. Resizing the window keeps the playfield's aspect ratio — no stretching/squashing of the art.
3. Pixel art renders crisply at scale (nearest-neighbor / integer scaling; no blur).
4. Gameplay (movement speed, collisions, maze layout) is identical regardless of window size — only the
   visual scale changes.
5. The game runs and is playable at both 1280x720 and 1920x1080.

## Notes
- This is a render-layer change (window size + scaling), not an art redraw — reuse the existing pixel
  art, just scale it up crisply. Higher-fidelity sprite art is explicitly OUT of scope.
- Pairs naturally with the movement fix (BUG-0001): a bigger window plus continuous hold-to-move makes
  the game feel like a real arcade title.
- Severity/priority: this is polish/usability — the game is functional at the current size, just small
  and dated-looking. Not urgent, but high visual payoff for "looks okay on a modern monitor."
