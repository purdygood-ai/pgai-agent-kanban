"""
test_harness_repo_lock_provenance.py
======================================
Behavioral unit test: team/scripts/wake/harness.sh's
per-project repo lock now acquires and releases through the shared
pgai_lock_acquire/pgai_lock_release helpers (team/scripts/lib/
lock_provenance.sh) instead of a raw `exec {fd}>PATH` + bare `flock -n`.

The guarded defect: a raw `exec {_lock_fd}>"$_lock_file"` truncates the lock
file on open, unconditionally, before flock ever decides whether this
process actually won the lock -- so a harness invocation that loses a
contended acquisition first silently wipes whatever provenance the
incumbent holder had written, then declines. This test proves the opposite
property now holds: a contended second acquisition leaves the first
holder's four provenance lines byte-identical.

The test exercises the REAL acquisition and release blocks from
harness.sh's run_project_chain override, extracted verbatim (never
reimplemented) via marker-based line slicing, so a regression back to a
raw exec/flock pair in that file is caught here even if lock_provenance.sh
itself is untouched.

Acceptance criteria exercised:
  - grep -nE 'exec \\{[A-Za-z_]+\\}>' team/scripts/wake/harness.sh finds no
    lock-file acquisition (asserted directly here as a grep-zero check).
  - harness.sh calls pgai_lock_acquire with a non-blank holder/pid/op/task
    and pgai_lock_release to drop it (asserted by extracting and running
    the real snippet).
  - A second, contended acquisition leaves the first holder's four
    provenance lines byte-identical.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap
import time

_HARNESS_SH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "wake" / "harness.sh"
_LOCK_PROVENANCE_SH = "scripts/lib/lock_provenance.sh"


def _acquisition_snippet() -> str:
    """Extract the real acquisition block from harness.sh's run_project_chain
    override: from its first local-variable declaration through the closing
    ``fi`` of the contention-refusal branch (two lines after the narration
    line naming "-- skipping"). Fails loud (AssertionError) if either
    marker is not found -- a silent empty extraction would let this test
    pass against a file that no longer has the shape it claims to verify.
    """
    lines = _HARNESS_SH.read_text(encoding="utf-8").splitlines()
    start_idx = next(i for i, line in enumerate(lines) if "local _lock_file" in line)
    skip_idx = next(
        i for i in range(start_idx, len(lines)) if "-- skipping\"" in lines[i]
    )
    # skip_idx+1 is "return 0", skip_idx+2 is the closing "fi".
    return "\n".join(lines[start_idx : skip_idx + 3])


def _release_snippet() -> str:
    lines = _HARNESS_SH.read_text(encoding="utf-8").splitlines()
    release_line = next(
        line for line in lines if 'pgai_lock_release "$_lock_file" "$_lock_fd"' in line
    )
    return release_line.strip()


def test_no_raw_exec_fd_lock_acquisition_remains() -> None:
    """grep -nE 'exec \\{[A-Za-z_]+\\}>' harness.sh finds nothing -- the raw
    truncating redirection that used to acquire the repo lock is gone."""
    text = _HARNESS_SH.read_text(encoding="utf-8")
    hits = [line for line in text.splitlines() if re.search(r"exec \{[A-Za-z_]+\}>", line)]
    assert not hits, f"raw exec-fd lock acquisition still present:\n" + "\n".join(hits)


def test_harness_calls_shared_lock_helpers() -> None:
    text = _HARNESS_SH.read_text(encoding="utf-8")
    assert "pgai_lock_acquire " in text
    assert "pgai_lock_release " in text
    assert re.search(r'source ".*lock_provenance\.sh"', text), (
        "harness.sh must source lock_provenance.sh to reach pgai_lock_acquire/release"
    )


def test_contended_second_acquisition_preserves_first_holders_provenance(
    tmp_path: pathlib.Path,
) -> None:
    """Run the REAL acquisition/release snippet from harness.sh as one
    process (the incumbent holder), then attempt the SAME snippet from a
    second, concurrent process against the same lock file. The second
    attempt must decline, and the first holder's four provenance lines
    must be byte-identical before and after the contended attempt."""
    lock_file = tmp_path / "locks" / "repo-wake-demo-project.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    acquisition = _acquisition_snippet()
    release = _release_snippet()

    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"

    # Wrapped in a function (mirroring the real run_project_chain the
    # snippet is extracted from): `local` and the if-block's `return 0`
    # are only valid inside a function, and `return` must skip the rest
    # of THIS function's own body exactly as it skips the rest of
    # run_project_chain in production.
    holder_script = textwrap.dedent(f"""\
        set -uo pipefail
        source {_LOCK_PROVENANCE_SH}
        pp_repo_lock_path() {{ printf '%s\\n' "{lock_file}"; }}
        log() {{ printf '%s\\n' "$*" >&2; }}
        run_project_chain_stub() {{
          local AGENT="coder"
          local project_name="demo-project"
          {acquisition}
          touch "{ready_file}"
          while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
          {release}
        }}
        run_project_chain_stub
    """)
    holder = subprocess.Popen(
        ["bash", "-c", holder_script],
        cwd=str(pathlib.Path(__file__).resolve().parents[2]),
    )
    try:
        deadline = time.time() + 5.0
        while not ready_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready_file.exists(), "holder process did not signal readiness"

        provenance_before = lock_file.read_text(encoding="utf-8")
        assert "holder=coder" in provenance_before
        assert "op=wake-chain:demo-project" in provenance_before
        assert "task=none" in provenance_before

        contender_script = textwrap.dedent(f"""\
            set -uo pipefail
            source {_LOCK_PROVENANCE_SH}
            pp_repo_lock_path() {{ printf '%s\\n' "{lock_file}"; }}
            log() {{ printf '%s\\n' "$*" >&2; }}
            run_project_chain_stub() {{
              local AGENT="writer"
              local project_name="demo-project"
              {acquisition}
              echo "CONTENDER_REACHED_TASK_LOOP"
            }}
            run_project_chain_stub
        """)
        contender = subprocess.run(
            ["bash", "-c", contender_script],
            capture_output=True,
            text=True,
            cwd=str(pathlib.Path(__file__).resolve().parents[2]),
            timeout=10,
        )
        assert "CONTENDER_REACHED_TASK_LOOP" not in contender.stdout, (
            "a contended acquisition must return before reaching the task "
            f"loop\nstdout={contender.stdout}\nstderr={contender.stderr}"
        )
        assert "repo lock held by" in contender.stderr, (
            f"contended run must narrate who holds the lock\nstderr={contender.stderr}"
        )

        provenance_after = lock_file.read_text(encoding="utf-8")
        assert provenance_after == provenance_before, (
            "the incumbent holder's provenance must be byte-identical after "
            f"a declined contended attempt.\nbefore:\n{provenance_before}\n"
            f"after:\n{provenance_after}"
        )
    finally:
        done_file.touch()
        holder.wait(timeout=5)

    # After release, the lock file is gone entirely -- not just emptied
    # (mirrors pgai_lock_release's atomic-release contract: a lock file
    # without provenance must be impossible to leave behind, so a clean
    # release removes the file itself, not merely its content). Exercised
    # end-to-end through harness.sh's own snippet.
    assert not lock_file.exists(), (
        f"expected the lock file to be removed after release, but it still "
        f"exists with content: {lock_file.read_text(encoding='utf-8')!r}"
    )
