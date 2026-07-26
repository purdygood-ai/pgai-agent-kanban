"""
test_overwatch_lock_halt_protocol.py
=====================================
Behavioral unit tests for BUG-0105: OVERWATCH inspects wrong lock and can
clobber operator HALT.

Fixtures exercised (mirrors the BUG-0105 acceptance criteria):

  (A) A project named other-than-"pgai-kanban": OVERWATCH's
      overwatch_halt_first_fix resolves the SAME lock path
      (pp_repo_lock_path) that wake dispatch would hold for that project.
  (B) A pre-existing HALT plus an OVERWATCH repair (overwatch_halt_first_fix)
      leaves the HALT present afterward, byte-for-byte unchanged — an
      operator-placed HALT is never touched or removed by OVERWATCH.
  (C) The wrapped mutation function runs while the resolved project lock is
      held: a concurrent non-blocking flock attempt on the same lock file
      from inside the wrapped function is contended, and the lock is fully
      released once overwatch_halt_first_fix returns.
  (D) Grep-zero: no executable (non-comment) occurrence of the hardcoded
      literal "repo-wake-pgai-kanban" remains anywhere in team/scripts/.

All tests run against synthetic environments (tmp_path) and never touch the
live kanban root.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths to the libraries under test.
# When pytest is run from team/ (the standard invocation via
# run-unit-tests.sh), paths are relative to team/.
# ---------------------------------------------------------------------------
_PROJECT_PATHS_SH = "scripts/lib/project_paths.sh"
_OVERWATCH_LIB_SH = "scripts/lib/overwatch_lib.sh"
_PROTOCOL_SH = "scripts/lib/overwatch_protocol.sh"


def _repo_root() -> pathlib.Path:
    """Return the absolute path to the repository root, resolved via git."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return pathlib.Path(result.stdout.strip())


def _build_kanban(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    """Build a minimal synthetic kanban root with one registered project."""
    proj_dir = kanban_root / "projects" / project_name
    (proj_dir / "overwatch" / "backups").mkdir(parents=True)
    (proj_dir / "overwatch" / "actions.log").write_text("", encoding="utf-8")
    (kanban_root / "locks").mkdir(parents=True, exist_ok=True)
    return proj_dir


# ---------------------------------------------------------------------------
# (A) Non-"pgai-kanban" project: OVERWATCH resolves the SAME lock path a wake
#     dispatcher would hold.
# ---------------------------------------------------------------------------


def test_repo_lock_path_matches_for_project_other_than_pgai_kanban(
    tmp_path: pathlib.Path,
) -> None:
    """pp_repo_lock_path resolves the SAME path OVERWATCH inspects.

    Regression coverage for BUG-0105: overwatch_protocol.sh used to hardcode
    "repo-wake-pgai-kanban.lock", which only matched a wake-held lock by
    coincidence for a project literally named "pgai-kanban". This test uses
    a project name that is deliberately NOT "pgai-kanban" and asserts that
    (1) the shared helper resolves the expected path, and (2) sourcing
    overwatch_protocol.sh and invoking overwatch_halt_first_fix against a
    lock held at that exact path produces sentinel 4 (contended) — proving
    OVERWATCH is inspecting the same lock file the helper computes, not a
    different, hardcoded one.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "other-than-pgai-kanban"
    _build_kanban(kanban_root, project_name)

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_PROJECT_PATHS_SH}
        export KANBAN_ROOT="{kanban_root}"
        pp_repo_lock_path "{project_name}"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"pp_repo_lock_path failed:\n{result.stderr}"

    resolved = result.stdout.strip()
    expected = str(kanban_root / "locks" / f"repo-wake-{project_name}.lock")
    assert resolved == expected, (
        f"pp_repo_lock_path resolved '{resolved}', expected '{expected}' "
        f"(string-compare-equal to what wake dispatch would hold)."
    )

    # Now hold a flock on the resolved path and confirm overwatch_protocol's
    # overwatch_halt_first_fix reports contention against THAT SAME path —
    # not a different hardcoded name.
    lock_file = pathlib.Path(resolved)
    holder_script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        READY_FILE="{kanban_root}/locks/holder-ready"
        DONE_FILE="{kanban_root}/locks/holder-done"
        exec 9>"{lock_file}"
        flock -x 9
        touch "$READY_FILE"
        while [[ ! -f "$DONE_FILE" ]]; do sleep 0.05; done
        exec 9>&-
    """)
    holder = subprocess.Popen(["bash", "-c", holder_script])
    try:
        import time

        ready_file = kanban_root / "locks" / "holder-ready"
        deadline = time.time() + 5.0
        while not ready_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready_file.exists(), "holder subprocess did not signal readiness"

        protocol_script = textwrap.dedent(f"""\
            set +e
            source {_PROJECT_PATHS_SH}
            source {_OVERWATCH_LIB_SH}
            source {_PROTOCOL_SH}
            export KANBAN_ROOT="{kanban_root}"
            export OVERWATCH_PROJECT="{project_name}"
            _noop_fn() {{ echo "noop ran"; }}
            overwatch_halt_first_fix _noop_fn
            echo "EXIT_CODE=$?"
        """)
        protocol_result = run_bash(tmp_path, protocol_script)
        assert "EXIT_CODE=4" in protocol_result.stdout, (
            f"Expected sentinel 4 (contended) against the resolved lock path.\n"
            f"stdout: {protocol_result.stdout}\nstderr: {protocol_result.stderr}"
        )
    finally:
        (kanban_root / "locks" / "holder-done").touch()
        holder.wait(timeout=5)


# ---------------------------------------------------------------------------
# (B) Pre-existing HALT + OVERWATCH repair → HALT still present afterward.
# ---------------------------------------------------------------------------


def test_pre_existing_halt_survives_an_overwatch_repair(tmp_path: pathlib.Path) -> None:
    """A HALT that existed before the call is never touched or removed.

    Regression coverage for BUG-0105 symptom 2: the halt-first wrapper used
    to touch-HALT -> fix -> rm-HALT without recording whether HALT
    pre-existed, so an OVERWATCH repair could remove an operator-placed
    HALT and restart a deliberately frozen board. This test places a HALT
    with distinctive content before calling overwatch_halt_first_fix and
    asserts the file is untouched (same content, same mtime-independent
    presence) after the wrapped function runs and returns successfully.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "project-with-operator-halt"
    _build_kanban(kanban_root, project_name)

    halt_flag = kanban_root / "HALT"
    sentinel_content = "operator halt: governance review in progress\n"
    halt_flag.write_text(sentinel_content, encoding="utf-8")

    script = textwrap.dedent(f"""\
        set +e
        source {_PROJECT_PATHS_SH}
        source {_OVERWATCH_LIB_SH}
        source {_PROTOCOL_SH}
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        _repair_fn() {{ echo "repair ran"; return 0; }}
        overwatch_halt_first_fix _repair_fn
        echo "EXIT_CODE=$?"
    """)
    result = run_bash(tmp_path, script)

    assert "EXIT_CODE=0" in result.stdout, (
        f"Expected overwatch_halt_first_fix to succeed (exit 0) with an "
        f"unheld lock and a pre-existing HALT.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "repair ran" in result.stdout, (
        f"Expected the wrapped function to actually run.\nstdout: {result.stdout}"
    )
    assert halt_flag.exists(), (
        "Pre-existing HALT was removed by overwatch_halt_first_fix — an "
        "operator-placed HALT must be inviolate."
    )
    assert halt_flag.read_text(encoding="utf-8") == sentinel_content, (
        "Pre-existing HALT content was modified by overwatch_halt_first_fix."
    )


def test_halt_created_by_this_invocation_is_removed_after_repair(
    tmp_path: pathlib.Path,
) -> None:
    """When no HALT pre-exists, OVERWATCH creates then removes its own HALT.

    Complements the pre-existing-HALT test above: confirms the baseline
    HALT-first behavior (stop chain agents during the mutation, restore
    after) still works when OVERWATCH is the one creating the HALT.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "project-without-halt"
    _build_kanban(kanban_root, project_name)

    halt_flag = kanban_root / "HALT"
    assert not halt_flag.exists()

    script = textwrap.dedent(f"""\
        set +e
        source {_PROJECT_PATHS_SH}
        source {_OVERWATCH_LIB_SH}
        source {_PROTOCOL_SH}
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        _repair_fn() {{
            if [[ -f "{halt_flag}" ]]; then
                echo "HALT_PRESENT_DURING_FN"
            else
                echo "HALT_ABSENT_DURING_FN"
            fi
        }}
        overwatch_halt_first_fix _repair_fn
        echo "EXIT_CODE=$?"
    """)
    result = run_bash(tmp_path, script)

    assert "EXIT_CODE=0" in result.stdout
    assert "HALT_PRESENT_DURING_FN" in result.stdout, (
        f"Expected HALT to be present while the wrapped function ran.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert not halt_flag.exists(), (
        "OVERWATCH-created HALT was not removed after the repair completed."
    )


# ---------------------------------------------------------------------------
# (C) Mutation occurs while the correct project lock is held (ordering).
# ---------------------------------------------------------------------------


def test_mutation_runs_while_project_lock_is_held_and_lock_releases_after(
    tmp_path: pathlib.Path,
) -> None:
    """The wrapped function executes under the held project lock; lock frees after.

    Regression coverage for BUG-0105 symptom 3: the contention lock used to
    be released BEFORE the mutation (TOCTOU) rather than held across it.
    This test has the wrapped function itself attempt a non-blocking flock
    on the SAME resolved project lock file from a fresh subprocess — if the
    wrapper is correctly holding the lock during the mutation, that attempt
    must be contended. After overwatch_halt_first_fix returns, a subsequent
    non-blocking flock attempt on the same file must succeed, proving the
    lock is fully released once the transaction completes.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "project-lock-ordering"
    _build_kanban(kanban_root, project_name)

    lock_file = kanban_root / "locks" / f"repo-wake-{project_name}.lock"
    contention_probe = kanban_root / "locks" / "contention-probe-result"

    script = textwrap.dedent(f"""\
        set +e
        source {_PROJECT_PATHS_SH}
        source {_OVERWATCH_LIB_SH}
        source {_PROTOCOL_SH}
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"

        _mutating_fn() {{
            # From inside the mutation, attempt a non-blocking flock on the
            # SAME lock file from a separate subprocess. This must be
            # contended if overwatch_halt_first_fix is correctly holding the
            # lock across the mutation (not just checking then releasing).
            bash -c '
                exec 8>"{lock_file}"
                if flock -n 8; then
                    echo "ACQUIRED" > "{contention_probe}"
                else
                    echo "CONTENDED" > "{contention_probe}"
                fi
                exec 8>&-
            '
        }}

        overwatch_halt_first_fix _mutating_fn
        echo "EXIT_CODE=$?"

        # After overwatch_halt_first_fix returns, the lock must be free.
        exec 8>"{lock_file}"
        if flock -n 8; then
            echo "POST_RELEASE=ACQUIRED"
        else
            echo "POST_RELEASE=CONTENDED"
        fi
        exec 8>&-
    """)
    result = run_bash(tmp_path, script)

    assert "EXIT_CODE=0" in result.stdout, (
        f"Expected the wrapped mutation to succeed.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert contention_probe.exists(), (
        f"Contention probe never ran.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert contention_probe.read_text(encoding="utf-8").strip() == "CONTENDED", (
        "The lock was NOT held while the wrapped mutation function ran — "
        "this is the TOCTOU BUG-0105 describes: a wake could start "
        "processing the project between OVERWATCH's contention check and "
        "its first state write."
    )
    assert "POST_RELEASE=ACQUIRED" in result.stdout, (
        f"Expected the project lock to be fully released after "
        f"overwatch_halt_first_fix returned.\nstdout: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# (D) Grep-zero: no hardcoded "repo-wake-pgai-kanban" outside comments.
# ---------------------------------------------------------------------------

_HARDCODED_LOCK_LITERAL = "repo-wake-pgai-kanban"


def _find_hardcoded_lock_literal_violations() -> list[str]:
    """Return '<file>:<lineno>:<content>' for each executable occurrence.

    Scans team/scripts/ (*.sh) for the literal "repo-wake-pgai-kanban".  A
    line is considered a comment (and therefore allowed) when its stripped
    form starts with '#'.  Any other occurrence is an executable hardcode of
    the old, buggy lock name and is a violation.
    """
    root = _repo_root()
    scripts_dir = root / "team" / "scripts"

    violations: list[str] = []
    for sh_file in scripts_dir.rglob("*.sh"):
        try:
            rel_path = sh_file.relative_to(root)
        except ValueError:
            rel_path = sh_file
        rel_str = str(rel_path).replace("\\", "/")

        try:
            lines = sh_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for lineno, line in enumerate(lines, start=1):
            if _HARDCODED_LOCK_LITERAL not in line:
                continue
            if line.strip().startswith("#"):
                continue
            violations.append(f"{rel_str}:{lineno}:{line.rstrip()}")

    return violations


def test_no_hardcoded_repo_wake_pgai_kanban_lock_name_outside_comments() -> None:
    """No executable occurrence of the old hardcoded lock literal remains.

    BUG-0105 required removing the hardcoded "repo-wake-pgai-kanban.lock"
    from overwatch_protocol.sh in favor of pp_repo_lock_path. This is the
    mechanical completeness signal: any future reintroduction of the literal
    outside a comment (documentation/history) fails here.
    """
    violations = _find_hardcoded_lock_literal_violations()
    assert not violations, (
        f"grep-zero invariant violated — {len(violations)} executable "
        f"occurrence(s) of '{_HARDCODED_LOCK_LITERAL}' found outside comments:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nUse pp_repo_lock_path (team/scripts/lib/project_paths.sh) instead."
    )


def test_grep_zero_pattern_detects_synthetic_violation(tmp_path: pathlib.Path) -> None:
    """Negative proof: the grep-zero scan mechanism catches a real violation.

    Confirms the pattern used above actually flags an executable hardcode,
    rather than the gate always passing regardless of content.
    """
    bad_script = tmp_path / "bad_overwatch.sh"
    bad_script.write_text(
        "#!/usr/bin/env bash\n"
        "# repo-wake-pgai-kanban.lock is mentioned here as documentation only.\n"
        'repo_lock_file="${KANBAN_ROOT}/locks/repo-wake-pgai-kanban.lock"\n',
        encoding="utf-8",
    )
    lines = bad_script.read_text(encoding="utf-8").splitlines()
    executable_hits = [
        line for line in lines
        if _HARDCODED_LOCK_LITERAL in line and not line.strip().startswith("#")
    ]
    assert len(executable_hits) == 1, (
        f"Expected exactly 1 executable hit in the synthetic bad script; "
        f"got {len(executable_hits)}: {executable_hits!r}."
    )


# ---------------------------------------------------------------------------
# Structural check: wake dispatchers and OVERWATCH share one helper.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative_path",
    [
        "scripts/wake/claude.sh",
        "scripts/wake/codex.sh",
        "scripts/wake/harness.sh",
        "scripts/lib/overwatch_protocol.sh",
    ],
)
def test_repo_lock_resolution_goes_through_shared_helper(relative_path: str) -> None:
    """Every per-project repo lock resolution site calls pp_repo_lock_path.

    Single-source-of-truth check: wake dispatch (claude.sh, codex.sh,
    harness.sh) and OVERWATCH (overwatch_protocol.sh) must all resolve the
    per-project repo lock path through pp_repo_lock_path rather than
    re-deriving the "repo-wake-<id>.lock" naming/sanitization formula
    independently — a second implementation is exactly how BUG-0105
    happened (the two silently diverged).
    """
    root = _repo_root()
    target = root / "team" / relative_path
    assert target.is_file(), f"Expected file at {target}"

    content = target.read_text(encoding="utf-8")
    assert "pp_repo_lock_path" in content, (
        f"{relative_path} does not call pp_repo_lock_path — it may be "
        f"re-deriving the per-project repo lock path independently, which "
        f"is exactly the single-source-of-truth violation BUG-0105 fixed."
    )

    # No file should still hand-roll the sanitization formula
    # (tr '/' '_' | tr -cd '[:alnum:]_-') for a repo-wake-*.lock path —
    # that logic now lives exclusively inside pp_repo_lock_path.
    hand_rolled = re.search(
        r"tr\s+-cd\s+'\[:alnum:\]_-'.*repo-wake|repo-wake.*tr\s+-cd\s+'\[:alnum:\]_-'",
        content,
    )
    assert hand_rolled is None, (
        f"{relative_path} appears to hand-roll the repo-lock sanitization "
        f"formula alongside calling pp_repo_lock_path — remove the "
        f"duplicate derivation."
    )
