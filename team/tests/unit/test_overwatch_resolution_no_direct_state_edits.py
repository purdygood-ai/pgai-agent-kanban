"""
test_overwatch_resolution_no_direct_state_edits.py
=====================================================
Enforced grep-zero check: pgai_agent_kanban.overwatch.resolution_stage —
the whole module, every lane (the pre-existing CANCEL-AND-REBUNDLE path
and the v2.23.0 Lane A/B/C paths alike) — never opens a real kanban state
file (a task's status.md, a queue file, a bug file, release-state.md,
project.cfg, a requirements bundle) directly in write mode. Every
mutation this module makes must go through one of three channels:

  1. A subprocess call to an existing operator verb script (cancel-rc.sh,
     unwind-rc.sh, cm/release.sh) — the scripts own their own writes.
  2. The ops write layer (pgai_agent_kanban.ops.write's wontdo_item /
     close_item / reset_item / delete_item) — that module owns its own
     writes and is out of this file's scope to re-implement.
  3. This module's own small set of purpose-built, atomic (tempfile +
     os.replace) append-only writers: the release-state.md "## OVERWATCH
     Decision" / "## Operator Override" event writer
     (_atomic_append_release_state_event, via its local `tmp_path`
     variable), the in-progress resolution marker
     (write_resolving_marker, also via `tmp_path`), and the OVERWATCH
     action log (log_action, via its local `log_file` variable — an
     OVERWATCH-owned audit trail, not kanban process state).

This is the acceptance criterion "All actions via existing operator
verbs (grep-zero for direct state edits in the OVERWATCH resolution
path)" (requirements/v2.23.0-overwatch-block-resolution.md, Acceptance)
made an enforced, regression-catching test instead of a one-time manual
grep. It supersedes the narrower, Lane-A-function-scoped proxy in
test_overwatch_lane_a_drill.py's TestGoldenPathExecutesTheDrill (renamed
TestLaneAGoldenPath there) — that test stays as further behavioral
evidence that the drill's operator verbs actually ran; this test is the
structural half, over the WHOLE file rather than one lane's slice of it.

Design: a source scan, not a runtime fixture — deliberately fast and
with no git/subprocess dependency, so it runs in every suite invocation
and cannot be skipped for lack of a binary. Every `open(...)` call in the
target file is inspected; a write-mode call whose target expression is
not one of the two allowlisted local variable names names a violation.
"""

from __future__ import annotations

import pathlib
import re

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_RESOLUTION_STAGE_PATH = (
    _TEAM_DIR / "pgai_agent_kanban" / "overwatch" / "resolution_stage.py"
)

# The only two local-variable targets a write-mode open() may legitimately
# name in this file: `tmp_path` (the atomic tempfile-then-os.replace
# pattern every append-only writer here uses) and `log_file` (the
# OVERWATCH action log path, an audit trail rather than kanban process
# state). Any other target is, by construction, either a real kanban state
# path passed straight into open() (the violation this check exists to
# catch) or a new pattern that has not been reviewed against this
# allowlist yet — both cases must fail loud rather than pass silently.
_ALLOWED_WRITE_TARGETS = frozenset({"tmp_path", "log_file"})

# Matches open(<target>, "<mode>", ...) or open(<target>, mode="<mode>", ...)
# for the first two positional-ish arguments. Deliberately loose on
# whitespace (SOP: keep regex loose for whitespace that gets discarded).
_OPEN_CALL_RE = re.compile(
    r"open\(\s*([^,()]+?)\s*,\s*(?:mode\s*=\s*)?[\"']([rwaxb+]+)[\"']"
)


def _write_mode_open_calls(source: str) -> list:
    """Return (target_expr, mode) for every open() call in *source* whose
    mode string is a write form (contains 'w', 'a', or 'x')."""
    violations = []
    for match in _OPEN_CALL_RE.finditer(source):
        target, mode = match.group(1).strip(), match.group(2)
        if any(flag in mode for flag in ("w", "a", "x")):
            violations.append((target, mode))
    return violations


def test_source_file_exists() -> None:
    """Sanity precondition: the module this check scans must exist at the
    expected path — a silent 0-byte scan (file missing) would otherwise
    look identical to a clean pass."""
    assert _RESOLUTION_STAGE_PATH.is_file(), (
        f"expected the OVERWATCH resolution stage module at {_RESOLUTION_STAGE_PATH}"
    )


def test_every_write_mode_open_call_targets_an_allowlisted_local_variable() -> None:
    """The grep-zero assertion: every write-mode open() call in
    resolution_stage.py must target `tmp_path` or `log_file` — never a
    real kanban state path (release-state.md, a task's status.md, a queue
    file, a bug file, project.cfg, a requirements bundle) opened directly.
    """
    source = _RESOLUTION_STAGE_PATH.read_text(encoding="utf-8")
    calls = _write_mode_open_calls(source)

    assert calls, (
        "expected at least the known write-mode open() calls (log_action's "
        "log_file append, the two atomic writers' tmp_path writes) — found "
        "none; either the file changed shape or this check's regex stopped "
        "matching, and either way the check is not exercising anything"
    )

    offenders = [
        (target, mode) for target, mode in calls if target not in _ALLOWED_WRITE_TARGETS
    ]
    assert not offenders, (
        "direct write-mode open() call(s) on a non-allowlisted target found in "
        f"{_RESOLUTION_STAGE_PATH} — every kanban state mutation in this module "
        "must go through a subprocess call to an operator verb script, the "
        "pgai_agent_kanban.ops.write layer, or one of this module's own "
        f"tmp_path/log_file atomic writers. Offending call(s): {offenders}. "
        "If this is a new, reviewed atomic-writer pattern, add its local "
        "variable name to _ALLOWED_WRITE_TARGETS in this test alongside the "
        "code change that introduces it — never widen the allowlist to paper "
        "over a call that targets a real state path."
    )


def test_no_write_mode_open_call_targets_a_known_state_path_literal() -> None:
    """Belt-and-suspenders: independent of the open()-call scan above,
    confirm none of the well-known real state path variables this module
    carries (release_state_md, ship_halt_path, project_halt, global_halt,
    status_file, readme, bundle_key-derived paths) appear as the direct
    target of a write-mode open() anywhere in the file. Written as a
    second, differently-shaped check so a single blind spot in the regex
    above cannot silently pass a real regression through both checks at
    once.
    """
    source = _RESOLUTION_STAGE_PATH.read_text(encoding="utf-8")
    known_state_path_names = (
        "release_state_md",
        "release_state_path",
        "ship_halt_path",
        "project_halt",
        "global_halt",
        "status_file",
        "readme",
        "bundle_key",
        "bug_path",
        "cfg_path",
    )
    for name in known_state_path_names:
        pattern = re.compile(
            rf"open\(\s*{re.escape(name)}\s*,\s*(?:mode\s*=\s*)?[\"'][wax]"
        )
        assert not pattern.search(source), (
            f"found a write-mode open() call directly targeting '{name}' in "
            f"{_RESOLUTION_STAGE_PATH} — a real kanban state path must never "
            "be opened for write directly; route the mutation through an "
            "operator verb subprocess, the ops write layer, or an atomic "
            "tmp_path writer instead"
        )


# ---------------------------------------------------------------------------
# Regression-detection self-proof: the same check, run against a synthetic
# copy of the source with a deliberate direct-state-edit violation spliced
# in, must fail. This is the automated form of "proven by a deliberate
# temporary regression during development" — it is exercised on every test
# run (not just once by hand at authoring time), so a future edit that
# weakens the check's own detection logic is itself caught by the suite.
# ---------------------------------------------------------------------------


def test_check_itself_fails_on_a_spliced_in_direct_state_edit() -> None:
    """Splice a synthetic direct write of release_state_md into a copy of
    the real source text and confirm the same detection logic used by the
    two tests above flags it. Proves this grep-zero check is load-bearing
    (would actually catch the regression it exists to catch), not merely
    a check that always passes regardless of content.
    """
    source = _RESOLUTION_STAGE_PATH.read_text(encoding="utf-8")
    regressed = source + (
        "\n\n"
        "def _regression_direct_write_probe(release_state_md: str) -> None:\n"
        "    with open(release_state_md, 'w', encoding='utf-8') as handle:\n"
        "        handle.write('direct edit — should never ship')\n"
    )

    calls = _write_mode_open_calls(regressed)
    offenders = [
        (target, mode) for target, mode in calls if target not in _ALLOWED_WRITE_TARGETS
    ]
    assert offenders, (
        "the spliced-in direct write of release_state_md was not detected — "
        "the grep-zero check's own regression-detection logic is broken"
    )
    assert ("release_state_md", "w") in offenders
