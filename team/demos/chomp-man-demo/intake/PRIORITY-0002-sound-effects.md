# PRIORITY-0002-sound-effects

## Status
open

## Priority ID
PRIORITY-0002

## Target Version
(patch — next available)

## Workflow Type
release

## Test Required
true

## Source Branch
ai_develop

## Human Approval Required
auto

---

## Overview
Add sound to Chomp Man. The game currently plays silent; this priority adds an audio layer —
sound effects for the core actions — to make the game feel alive. Pygame's mixer provides the
audio playback.

## Goals
1. Sound effects for the core game events that exist so far:
   - Chomp Man eating a pellet (the signature "chomp"/"waka" style loop or per-eat blip).
   - Eating a power-up.
   - A dragon catching Chomp Man (life lost).
   - Game over.
   - (If present at the time this ships) eating a vulnerable dragon, and eating bonus fruit.
2. A simple, central sound manager so future events can register sounds easily.
3. Audio must degrade gracefully: if no audio device is available (e.g. a headless host), the
   game still runs without crashing — sound is best-effort, never fatal.
4. A mute toggle (suggested key: `M`) so the player can silence the game. (`M` is free; do NOT
   use `K`, which is reserved for a future special-effect feature.)

## Design / Theming
- Use pygame's mixer for playback. Sounds can be short generated tones or small bundled .wav
  files (keep any assets small and in an `assets/` or `sounds/` dir; document their origin).
- A central `SoundManager` (or similar) that loads sounds once and plays them by name keeps
  the wiring clean and lets later releases add sounds without touching event logic everywhere.
- Keep the audio tasteful and not grating — short, distinct cues per event.

## Deliverables
- A sound manager module + loaded sound effects for the core events.
- Sound playback wired into the existing event points (pellet eat, power-up, life loss,
  game over, and — if those mechanics exist when this ships — dragon-eat and fruit).
- Graceful no-audio fallback (try/except around mixer init; game runs silently if audio is
  unavailable).
- A mute toggle (M) that silences/unsilences all sound.

## Acceptance criteria
1. `python3 -m py_compile` passes for all changed/added .py files.
2. A central sound manager exists (sounds loaded once, played by name) — verify the module
   and its play-by-name interface.
3. Core events trigger sound playback (verify the play calls are wired at the pellet-eat,
   power-up, life-loss, and game-over points; and at dragon-eat/fruit if those exist).
4. Mute toggle on `M` silences/unsilences (verify the toggle; confirm `K` is still unbound).
5. Graceful fallback: with no audio device, the game still runs and does not crash (verify the
   mixer init is guarded; simulate by forcing mixer init to fail and confirm the game loop
   still runs). This is critical for headless CI/test environments.
6. Any bundled audio assets are small and live in a dedicated dir; their origin is documented.

## Suggested Decomposition
- CODER-1: SoundManager module (guarded mixer init, load-by-name, play-by-name, graceful
  no-audio fallback).
- CODER-2: wire sounds into existing event points + the M mute toggle.

## Notes for CODER
Wrap mixer initialization in a try/except so a host with no audio device runs silently rather
than crashing — this is essential, since the chain's test/verification environment is
headless and must not fail just because there's no sound card. Route all playback through one
sound manager (play-by-name) so later releases add sounds without scattering mixer calls. Use
`M` for mute; leave `K` unbound (reserved). Keep assets tiny and documented.

## Notes for TESTER
The most important check is criterion 5 — graceful no-audio fallback — because your
verification environment is almost certainly headless (no audio device). Confirm: mixer init
is guarded; with audio unavailable the game still imports, compiles, and the loop runs without
crashing. Also verify the central sound manager (play-by-name), that core events have playback
wired in, the M mute toggle works, and K remains unbound. Actual audio output needs a machine
with a sound device (manual). Headless: rely on the guarded-init + wiring inspection; force a
mixer-init failure and confirm the game still runs silently.

## Notes
Dropped as a standalone priority (not tied to a specific feature requirement). It targets the
next available patch version and adds an audio layer over whatever mechanics exist at ship
time. Demonstrates using a priority to inject a cross-cutting concern (sound) mid-stream,
landing as a patch rather than a planned minor.
