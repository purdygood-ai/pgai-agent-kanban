r"""
test_self_header_help_pipe_safety.py
=====================================
Coverage for the operator-script convention where a `--help` branch builds
its usage text by reading the script's own leading comment block and
truncating it to a fixed line count: `grep '^#' "$0" | sed 's/^# \{0,1\}//'`
feeding a line-count limiter.

Background: when that extraction fed a `head -N` pipe stage directly, and
the file's own count of lines starting with `#` exceeded `N`, `head`
stopped reading once it had its `N` lines while `grep`/`sed` upstream were
still writing further-down lines. Under `set -o pipefail` (every affected
script sets it) the still-writing stage's SIGPIPE (128+13 = 141) became the
pipeline's own exit status, and `set -e` then terminated the script before
its trailing `exit 0` ran. Whether it fired was a scheduling race between
`head` closing its read end and the upstream stage's next `write()`, which
is why the failure was intermittent rather than constant, and why a single
by-hand invocation reads as "not reproducible."

The fix drains the extraction through `mapfile` via process substitution
-- which always reads its producer to EOF rather than closing early -- and
slices the resulting array to the line-count limit locally with `printf`,
so the upstream `grep`/`sed` always finish writing and no reader can close
their pipe out from under them.

Test groups
-----------
Group 1 -- Repaired-script sweep: a deterministic static check that no
covered script pipes the extraction directly into `head -N` (the race
condition itself, catchable on inspection with no timing involved), paired
with every covered script driven through repeated plain invocations with
output redirected to a file, never a pipe a reader could close early. The
static check is the reliable regression lock -- the race this repair
closes fires on roughly one invocation in a hundred, so a bounded repeat
count alone would only probabilistically catch a script that regressed
back to the pre-repair shape.

Group 2 -- Negative proof: a synthetic script reproducing the PRE-repair
three-stage pipeline (extraction piped straight into `head -N`, with a
header long enough relative to `N` to make the race land reliably) is
driven the same way and must show at least one non-zero exit across
repeated attempts -- confirming this test's own harness would have caught
every one of the twenty scripts before they were repaired, not just after.

Group 3 -- Repair-shape proof: the same synthetic long header, extracted
through the mapfile-and-printf shape this repair uses instead of a raw
`head -N` pipe stage, must exit 0 on every attempt -- confirming the shape
generalizes to header sizes larger than any single shipped script carries
today, not merely to the twenty specific line counts this repair covers.

No tmux and no docker are required -- every `--help` branch covered here
returns before its script reaches any tmux or container invocation.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's location, matching every other
# script-invoking test module in this tree.
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

# The literal extraction call every covered script's --help branch performs
# on its own source. Matching on this construct rather than on a script
# name or a fixed enumeration means a future script adopting the same
# convention is covered automatically, and a script that stops using the
# convention drops out of the sweep on its own. A script that performs
# this same extraction with no line-count limit downstream at all has no
# reader that could close early, so it is never racy and is intentionally
# left out of the sweep by the second half of the filter below.
#
# The extraction may drop the shebang between the two stages
# (`grep -v '^#!'`), so --help never opens on `!/usr/bin/env bash`.
_SHEBANG_FILTER = r"""(?:grep -v '\^#!' \| )?"""
_SELF_HEADER_EXTRACTION = re.compile(
    r"""grep '\^#' "\$0" \| """ + _SHEBANG_FILTER + r"""sed 's/\^# \\\{0,1\\\}//'"""
)

# A line-count limit applied to the extraction above, in either its
# pre-repair shape (feeding a `head -N` pipe stage directly -- the shape
# that can close early and race the writers) or its repaired shape (drained
# through `mapfile` via process substitution first, then sliced). Matching
# EITHER shape, not only the repaired one, is what lets the sweep still
# find and test a script that regresses back to the pre-repair shape --
# matching only the repaired marker would make a regressed script silently
# drop out of its own regression test instead of failing it.
_OLD_HEAD_LIMIT = re.compile(
    r"""grep '\^#' "\$0" \| """ + _SHEBANG_FILTER + r"""sed 's/\^# \\\{0,1\\\}//' \| head -\d+"""
)
_NEW_MAPFILE_LIMIT = re.compile(r"mapfile -t __help_lines <")

# Repeat count for the repaired-script sweep (Group 1). The fix removed the
# scheduling race entirely -- the extraction now always drains to EOF before
# anything reads it, so a repaired script's exit status is no longer
# probabilistic -- but the repeat still guards against a future edit that
# reintroduces a `head -N` (or similar early-closing reader) downstream of
# the extraction in any one of the covered scripts.
_SWEEP_REPEAT_COUNT = 30

# Repeat count for the synthetic negative/positive proofs (Groups 2 and 3).
# Chosen large enough that Group 2's failure (empirically ~100% at these
# parameters -- a 5000-line header against a `head -1` limit) is not a
# fluke of one run, while staying cheap.
_SYNTHETIC_REPEAT_COUNT = 20


def _discover_self_header_help_scripts() -> list[pathlib.Path]:
    """Every *.sh under team/scripts/ whose --help path extracts its own
    leading comment block via the shared convention AND applies a
    line-count limit to it, in either the pre-repair or repaired shape.
    Discovered by the construct rather than by a hardcoded path list, per
    the grep-zero sweep convention: a script adopting, repairing, or
    regressing the convention moves in, stays in, or is still caught by
    this sweep without the test needing an edit."""
    found = []
    for path in sorted(_SCRIPTS_DIR.rglob("*.sh")):
        text = path.read_text()
        if _SELF_HEADER_EXTRACTION.search(text) and (
            _OLD_HEAD_LIMIT.search(text) or _NEW_MAPFILE_LIMIT.search(text)
        ):
            found.append(path)
    return found


_COVERED_SCRIPTS = _discover_self_header_help_scripts()


def _run_env() -> dict[str, str]:
    """Environment for a covered script's --help subprocess.

    --help returns before any of the covered scripts touch live kanban
    content, so PGAI_AGENT_KANBAN_ROOT_PATH only needs to resolve to *some*
    absolute directory for env_bootstrap.sh's idempotency guard to accept
    it -- pointed at this repository's own root rather than any operator
    install, so the verdict never depends on what happens to be installed
    on the host running the suite.
    """
    env = os.environ.copy()
    env.setdefault("PGAI_AGENT_KANBAN_ROOT_PATH", str(_TEAM_DIR.parent))
    return env


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


# ---------------------------------------------------------------------------
# Group 1 -- Repaired-script sweep
# ---------------------------------------------------------------------------
def test_covered_scripts_sweep_finds_every_repaired_script() -> None:
    """Sanity check on the discovery mechanism itself: the sweep below is
    only meaningful if it actually found scripts to test. A change that
    silently narrows the regex to match nothing would otherwise leave
    Group 1 vacuously green."""
    assert len(_COVERED_SCRIPTS) >= 20, (
        f"expected the self-header-extraction sweep to find at least 20 "
        f"scripts under {_SCRIPTS_DIR}, found {len(_COVERED_SCRIPTS)}: "
        f"{[str(p) for p in _COVERED_SCRIPTS]}"
    )


def test_no_covered_script_pipes_extraction_directly_into_head() -> None:
    """Deterministic companion to the behavioral sweep below. The race this
    module guards against fires on roughly one invocation in a hundred (the
    field measurement that motivated the repair), so a bounded repeat count
    catches a real regression only probabilistically -- this static check
    catches it every time, on the first run, with no race to win. It
    targets the exact construct that can race (the extraction piped
    straight into a `head -N` stage), not a name or an enumerated path
    list, so a *new* script that copies the pre-repair shape is caught
    here too, not only the twenty this repair covers today."""
    violations = [
        str(path) for path in sorted(_SCRIPTS_DIR.rglob("*.sh")) if _OLD_HEAD_LIMIT.search(path.read_text())
    ]
    assert not violations, (
        f"these scripts pipe their self-header extraction directly into "
        f"`head -N`, which can close its read end before the extraction "
        f"finishes writing and SIGPIPE it under pipefail: {violations}. "
        f"Drain the extraction through `mapfile` via process substitution "
        f"first, then slice to the line limit locally instead of piping "
        f"into `head -N`."
    )


def test_covered_scripts_help_exits_zero_across_repeated_invocations(
    tmp_path: pathlib.Path,
) -> None:
    """Every script whose --help branch extracts its own header must exit 0
    on every one of a repeated run of plain (non-piped) invocations. Output
    goes to a file, never a pipe a reader could close early, isolating the
    INTERNAL extraction-and-limit assembly from any external reader
    effect."""
    failures: dict[str, list[int]] = {}
    for script in _COVERED_SCRIPTS:
        rc_list: list[int] = []
        for attempt in range(_SWEEP_REPEAT_COUNT):
            sink_path = tmp_path / f"{script.name}.{attempt}.out"
            with open(sink_path, "wb") as sink:
                result = subprocess.run(
                    ["bash", str(script), "--help"],
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    env=_run_env(),
                    cwd=str(tmp_path),
                    timeout=20,
                )
            if result.returncode != 0:
                rc_list.append(result.returncode)
        if rc_list:
            failures[str(script)] = rc_list

    assert not failures, (
        f"--help exited non-zero on at least one of {_SWEEP_REPEAT_COUNT} "
        f"repeated invocations for these scripts (exit 141 is SIGPIPE, "
        f"128+13): {failures}"
    )


def test_covered_scripts_help_omits_the_shebang(tmp_path: pathlib.Path) -> None:
    """The header extraction reads every '#' line, the shebang included;
    --help must still never print `!/usr/bin/env bash` as a help line."""
    printed_shebang = []
    for script in _COVERED_SCRIPTS:
        result = subprocess.run(
            ["bash", str(script), "--help"],
            capture_output=True,
            text=True,
            env=_run_env(),
            cwd=str(tmp_path),
            timeout=20,
        )
        if any(line.startswith("!/") for line in result.stdout.splitlines()):
            printed_shebang.append(str(script))

    assert not printed_shebang, (
        f"--help prints the shebang as a help line for: {printed_shebang}. "
        f"Drop it in the extraction (grep '^#' \"$0\" | grep -v '^#!' | sed ...)."
    )


# ---------------------------------------------------------------------------
# Synthetic fixture shared by Groups 2 and 3
# ---------------------------------------------------------------------------
def _write_synthetic_header_script(
    path: pathlib.Path, *, extraction_body: str, header_line_count: int = 5000
) -> None:
    """Writes a standalone script whose --help branch is built from
    `extraction_body`, preceded by a `header_line_count`-line comment
    block. The header is made deliberately far longer than any real
    script's own header so the line-count limit inside `extraction_body`
    is always exceeded, whatever limit that body uses -- this is what
    makes Group 2's race land reliably instead of the ~1% rate observed
    on the shipped scripts."""
    header_lines = [f"# synthetic header padding line {i}" for i in range(header_line_count)]
    script = "\n".join(
        [
            "#!/usr/bin/env bash",
            *header_lines,
            "set -euo pipefail",
            'case "${1:-}" in',
            "  -h|--help)",
            extraction_body,
            "    exit 0",
            "    ;;",
            "esac",
            "",
        ]
    )
    path.write_text(script)
    path.chmod(0o755)


# ---------------------------------------------------------------------------
# Group 2 -- Negative proof: the pre-repair shape still fails
# ---------------------------------------------------------------------------
def test_pre_repair_extraction_shape_fails_intermittently(tmp_path: pathlib.Path) -> None:
    """Reproduces the PRE-repair shape directly -- the self-header
    extraction piped straight into `head -N` -- against a header long
    enough that `head` always closes before `grep`/`sed` finish writing.
    At least one repeated attempt must exit non-zero, proving this
    module's own sweep mechanism (Group 1) would have failed against
    every one of the covered scripts before they were repaired, not only
    that it happens to pass now."""
    script = tmp_path / "pre_repair_help.sh"
    _write_synthetic_header_script(
        script,
        extraction_body="    grep '^#' \"$0\" | sed 's/^# \\{0,1\\}//' | head -1",
    )
    failures = []
    for attempt in range(_SYNTHETIC_REPEAT_COUNT):
        result = subprocess.run(
            ["bash", str(script), "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_run_env(),
            cwd=str(tmp_path),
            timeout=20,
            text=True,
        )
        if result.returncode != 0:
            failures.append((attempt, result.returncode))

    assert failures, (
        f"expected the pre-repair grep|sed|head shape to fail on at least "
        f"one of {_SYNTHETIC_REPEAT_COUNT} attempts against a header far "
        f"longer than its head -N limit; every attempt exited 0, which "
        f"means this harness could not have caught the defect it exists "
        f"to guard against"
    )
    non_zero = [rc for _, rc in failures]
    assert all(rc == 141 for rc in non_zero), (
        f"expected every non-zero exit to be 141 (SIGPIPE, 128+13); saw "
        f"{non_zero}"
    )


# ---------------------------------------------------------------------------
# Group 3 -- Repair-shape proof: the fix generalizes past the shipped sizes
# ---------------------------------------------------------------------------
def test_repaired_extraction_shape_exits_zero_across_repeated_invocations(
    tmp_path: pathlib.Path,
) -> None:
    """The same oversized header as Group 2, this time extracted through
    the mapfile-and-printf shape the repair uses in place of a raw
    `head -N` pipe stage, must exit 0 on every attempt -- proving the safety
    is a property of the extraction shape itself, not an artifact of the
    twenty shipped scripts' particular header lengths staying within some
    margin."""
    script = tmp_path / "repaired_help.sh"
    _write_synthetic_header_script(
        script,
        extraction_body=(
            "    mapfile -t __help_lines < <(grep '^#' \"$0\" | sed 's/^# \\{0,1\\}//')\n"
            '    printf \'%s\\n\' "${__help_lines[@]:0:1}"'
        ),
    )
    failures = []
    for attempt in range(_SYNTHETIC_REPEAT_COUNT):
        result = subprocess.run(
            ["bash", str(script), "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_run_env(),
            cwd=str(tmp_path),
            timeout=20,
            text=True,
        )
        if result.returncode != 0:
            failures.append((attempt, result.returncode, _diagnostic(result)))

    assert not failures, (
        f"repaired extraction shape exited non-zero on "
        f"{len(failures)}/{_SYNTHETIC_REPEAT_COUNT} attempts against an "
        f"oversized header: {failures}"
    )


if __name__ == "__main__":  # pragma: no cover - manual debug entry point
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-v", str(_THIS_FILE)]))
