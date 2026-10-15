"""
classify.py — map a run-cold-build-tests.sh invocation onto the three
SOP verdict classes.

team/SOP-core.md ("Gates and Refusals" -- "Unusable build blocks; an
unavailable build environment does not") separates a verification
lane's outcome into three cases that must never collapse into one
another:

  1. built            — the artefact built.
  2. failed-to-build   — the artefact failed to build (a tree defect).
  3. could-not-attempt — the build could not be attempted at all (an
                          environment gap: no engine, unreachable registry,
                          missing toolchain) -- nothing was learned about
                          whether the images build. On exit 1 the record
                          can still name a failing engine-free cold test,
                          whose bug the run files.

run-cold-build-tests.sh already discriminates cases 1-3 internally (see its
own header comment and team/roles/TESTER.md "What distinguishes red from
skipped") but folds cases 1 and 3 onto the same exit code (0) and reports
case 3's pytest-missing precondition as exit 2 -- a code this module's
caller (team/scripts/deep-verify.sh) must not confuse with "built" or
"failed-to-build" either. This module is the one place that reads the
lane's exit code plus its captured stdout+stderr and returns which of the
three verdicts actually happened, plus a human-readable detail string
naming the evidence (the failing engine/flavor/step, the unavailable
resource, or the built pairs).

The lane runs every module under team/tests/cold/, not only the image
builds, so a non-zero lane exit does not by itself mean an image failed to
build. The verdict is about the builds: when pytest's short test summary
names the failing tests and none of them belongs to an image-build module
(_BUILD_TEST_MODULES), the run records built, and the detail names the
failing non-build tests so the caller can file them as a test failure.

built always needs evidence that a build happened (shows_build_evidence):
at least one per-pair "Image built successfully" line, or no narrated
environmental skip at all. On a host with no usable engine the build
tests narrate-skip every pair while the engine-free cold modules still
run, so the lane exits 0 (they passed) or 1 (one failed) without the
all-skipped marker. When no pair built and pairs were narrate-skipped,
both shapes record could-not-attempt -- nothing was learned about whether
the images build -- and on exit 1 the detail still names the failing
non-build tests alongside the skip reasons.

This module does not run the lane and does not touch the filesystem beyond
what its caller hands it as plain text -- it is a pure classifier, kept
unit-testable against synthetic captured-output fixtures without spawning a
real container build.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# The three verdict classes. Any code that writes a deep-verify ledger
# record must use one of exactly these three literal strings -- see
# pgai_agent_kanban.deep_verify.ledger.VALID_VERDICTS, which enforces the
# same vocabulary at write time.
# ---------------------------------------------------------------------------
VERDICT_BUILT = "built"
VERDICT_FAILED_TO_BUILD = "failed-to-build"
VERDICT_COULD_NOT_ATTEMPT = "could-not-attempt"

VALID_VERDICTS = (VERDICT_BUILT, VERDICT_FAILED_TO_BUILD, VERDICT_COULD_NOT_ATTEMPT)

# ---------------------------------------------------------------------------
# Text markers run-cold-build-tests.sh itself prints (its own narration,
# never re-derived or duplicated here as a second source of truth for what
# the lane "means" -- these are the exact strings the shipped script emits).
# ---------------------------------------------------------------------------
_ALL_SKIPPED_MARKER = "Cold-build lane: all (engine, flavor) pairs skipped"
_CLI_VERSIONS_MISSING_KEY = "run-cold-build-tests.cli-versions-env-missing"
_CLI_VERSIONS_EMPTY_KEY = "run-cold-build-tests.cli-versions-empty"

# Per-pair success line printed by team/tests/cold/test_docker_cold_build.py's
# _build_flavor_image() on a successful build.
_BUILT_PAIR_RE = re.compile(
    r"\[cold-build\]\s+\[(?P<engine>\S+)\]\s+\[(?P<flavor>\S+)\]\s+"
    r"Image built successfully"
)

# Narrated-skip line printed by the same module's _narrate_skip(), one line
# per (engine, flavor) pair skipped for an environmental reason (engine
# absent, registry unreachable).
_SKIP_REASON_RE = re.compile(r"^\[cold-build\]\s+SKIP\s+—\s+(?P<reason>.+)$", re.MULTILINE)

# pytest.fail() message from _build_flavor_image() on a genuine Dockerfile
# defect -- names the engine, the flavor, and the failing Dockerfile step.
_FAILING_PAIR_RE = re.compile(
    r"(?P<engine>docker|podman) build of docker/(?P<flavor>[\w.-]+)/Dockerfile "
    r"failed \(exit (?P<code>\d+)\) at step: (?P<step>.+?);"
)

# pytest's short test summary line for a failed or errored test, printed by
# default (-r fE) after the run: "FAILED tests/cold/test_x.py::test_y - msg"
# or "ERROR tests/cold/test_x.py::test_y - msg"; a collection error names the
# file alone ("ERROR tests/cold/test_x.py - msg"). Node ids are relative to
# team/, the directory run-cold-build-tests.sh runs pytest from.
_FAILED_TEST_RE = re.compile(
    r"^(?P<outcome>FAILED|ERROR)[ \t]+(?P<file>[^\s:]+\.py)"
    r"(?:::(?P<test>\S+))?(?:[ \t]+-[ \t]+(?P<reason>.*?))?[ \t]*$",
    re.MULTILINE,
)

# The cold-lane modules whose tests build the declared images
# (docker/<flavor>/Dockerfile). A failing test in one of these is a build
# failure; a failing test anywhere else under team/tests/cold/ is not.
_BUILD_TEST_MODULES = frozenset(
    {
        "test_docker_cold_build.py",
        "test_docker_build_fixture.py",
    }
)


@dataclass(frozen=True)
class Classification:
    """One classifier result: a verdict (one of VALID_VERDICTS) plus a
    single-line, human-readable detail naming the evidence behind it."""

    verdict: str
    detail: str


@dataclass(frozen=True)
class FailingPair:
    """One (engine, flavor) pair that failed to build cold, plus the
    Dockerfile step it failed at. Structured counterpart to the formatted
    "engine/flavor: failed at step: ..." strings _extract_failing_pairs()
    joins into Classification.detail -- team/scripts/deep-verify.sh's
    failure-path bug filer (pgai_agent_kanban.deep_verify.bug_filer) needs
    the engine/flavor/step fields separately, not pre-joined into prose, so
    it can name each field in its own bug-report field and key duplicate
    detection off the (engine, flavor) pair alone."""

    engine: str
    flavor: str
    step: str


def extract_failing_pairs(output_text: str) -> list:
    """Return every (engine, flavor, step) triple a failed cold-build run's
    captured output names, as a list of FailingPair. Empty when the output
    carries no structured "docker/podman build of docker/<flavor>/Dockerfile
    failed ... at step: ..." line (the exit-1 case classify_cold_build_result
    falls back to an unstructured detail for -- see its own docstring)."""
    return [
        FailingPair(
            engine=m.group("engine"),
            flavor=m.group("flavor"),
            step=m.group("step").strip(),
        )
        for m in _FAILING_PAIR_RE.finditer(output_text)
    ]


@dataclass(frozen=True)
class FailingTest:
    """One test pytest's short test summary names as failed or errored.

    *file* is the test module path as pytest printed it (relative to team/,
    e.g. "tests/cold/test_x.py"); *test_id* is the part after "::" (the
    test function, with any parametrize id), or "" for a module-level
    collection error; *reason* is pytest's one-line message, or "".
    """

    file: str
    test_id: str
    outcome: str
    reason: str = ""

    @property
    def nodeid(self) -> str:
        """The pytest node id, "<file>::<test_id>", or the file alone."""
        return f"{self.file}::{self.test_id}" if self.test_id else self.file

    @property
    def is_build_test(self) -> bool:
        """True when the test belongs to an image-build module."""
        return self.file.replace("\\", "/").rsplit("/", 1)[-1] in _BUILD_TEST_MODULES


def extract_failing_tests(output_text: str) -> list:
    """Return every test pytest's short test summary in *output_text* names
    as FAILED or ERROR, as a list of FailingTest in first-seen order, one
    entry per node id (a test reported both FAILED and ERROR appears once).
    Empty when the output carries no summary line."""
    seen: dict = {}
    for m in _FAILED_TEST_RE.finditer(output_text):
        test = FailingTest(
            file=m.group("file"),
            test_id=m.group("test") or "",
            outcome=m.group("outcome"),
            reason=(m.group("reason") or "").strip(),
        )
        seen.setdefault(test.nodeid, test)
    return list(seen.values())


def _extract_built_pairs(output_text: str) -> list:
    return [
        f"{m.group('engine')}/{m.group('flavor')}: built"
        for m in _BUILT_PAIR_RE.finditer(output_text)
    ]


def _extract_skip_reasons(output_text: str) -> list:
    return [m.group("reason").strip() for m in _SKIP_REASON_RE.finditer(output_text)]


def shows_build_evidence(output_text: str) -> bool:
    """Return True when *output_text* is evidence that the run exercised an
    image build: it carries at least one per-pair "Image built successfully"
    line, or it narrates no environmental skip at all (a run that skipped
    nothing leaves the lane's own exit status to speak for the builds).

    False when no pair built and at least one pair was narrate-skipped
    ("[cold-build] SKIP — <reason>"): such a run learned nothing about
    whether the images build, so it must not be recorded as built.
    """
    return bool(_extract_built_pairs(output_text)) or not _extract_skip_reasons(output_text)


def _no_pair_built_detail(skip_reasons: list) -> str:
    """The detail prefix for a run in which no pair built and every pair
    the run reached was narrate-skipped, naming each skip reason."""
    return (
        "no (engine, flavor) pair built; every pair the run reached was "
        "narrate-skipped: " + "; ".join(skip_reasons)
    )


def _extract_failing_pairs(output_text: str) -> list:
    return [
        f"{pair.engine}/{pair.flavor}: failed at step: {pair.step}"
        for pair in extract_failing_pairs(output_text)
    ]


def _single_line(detail: str) -> str:
    """Collapse any stray newline in *detail* -- the ledger's field-parse
    format is one value per line (see pgai_agent_kanban.deep_verify.ledger),
    so a multi-line detail would corrupt the record's shape."""
    return " ".join(detail.split("\n"))


def classify_cold_build_result(exit_code: int, output_text: str) -> Classification:
    """Classify one run-cold-build-tests.sh invocation.

    *exit_code* is the lane's own process exit code (0/1/2 per its header
    comment). *output_text* is its captured stdout+stderr, unmodified.

    Exit 1 is failed-to-build when the output carries a cli-versions error
    key, a structured Dockerfile-step failure, or a failing test in an
    image-build module, and when it names no failing test at all. When
    every failing test pytest names is outside the image-build modules, no
    image failed to build: the verdict is built when the output shows
    build evidence (shows_build_evidence), and could-not-attempt when no
    pair built and pairs were narrate-skipped. Either way the detail names
    the failing tests; the could-not-attempt detail names the skip reasons
    too.

    Exit 0 is could-not-attempt on the lane's all-skipped marker, and also
    when no pair built and pairs were narrate-skipped without that marker
    (the engine-free cold tests ran and passed). Otherwise it is built.

    Never raises. An exit code or output shape this classifier has no
    discriminator for is recorded honestly as could-not-attempt with a
    detail that says so plainly -- SOP-core.md's own words: 'a run that cannot
    tell which case it was records that ambiguity rather than guessing'.
    Guessing 'built' or 'failed-to-build' without evidence would be worse
    than admitting the ambiguity, because could-not-attempt is the one
    verdict that already means 'nothing was learned about whether the
    images build'.
    """
    if exit_code == 2:
        return Classification(
            VERDICT_COULD_NOT_ATTEMPT,
            "pytest is not installed on the verifying host "
            "(run-cold-build-tests.sh exited 2: the lane could not even start)",
        )

    if exit_code == 1:
        if _CLI_VERSIONS_MISSING_KEY in output_text:
            return Classification(
                VERDICT_FAILED_TO_BUILD,
                "docker/cli-versions.env is missing; the cold-build lane has no "
                "CLAUDE_CODE_VERSION/CODEX_VERSION pins to build with "
                f"({_CLI_VERSIONS_MISSING_KEY})",
            )
        if _CLI_VERSIONS_EMPTY_KEY in output_text:
            return Classification(
                VERDICT_FAILED_TO_BUILD,
                "docker/cli-versions.env resolved an empty CLAUDE_CODE_VERSION or "
                f"CODEX_VERSION pin ({_CLI_VERSIONS_EMPTY_KEY})",
            )
        pairs = _extract_failing_pairs(output_text)
        if pairs:
            return Classification(VERDICT_FAILED_TO_BUILD, _single_line("; ".join(pairs)))
        failing_tests = extract_failing_tests(output_text)
        build_failures = [t.nodeid for t in failing_tests if t.is_build_test]
        if build_failures:
            return Classification(
                VERDICT_FAILED_TO_BUILD,
                _single_line(
                    "image-build test(s) failed without a structured "
                    "engine/flavor/step line: " + "; ".join(build_failures)
                ),
            )
        if failing_tests:
            # Every failure pytest named is outside the image-build modules:
            # no build failed. The failing tests are named so the caller
            # files them as a test failure rather than a build failure.
            failing_nodeids = "; ".join(t.nodeid for t in failing_tests)
            if not shows_build_evidence(output_text):
                # No pair built and pairs were narrate-skipped: nothing was
                # learned about the builds, so the run is not built.
                return Classification(
                    VERDICT_COULD_NOT_ATTEMPT,
                    _single_line(
                        _no_pair_built_detail(_extract_skip_reasons(output_text))
                        + "; non-build cold test(s) failed: "
                        + failing_nodeids
                    ),
                )
            # The builds that ran are recorded as built.
            detail_parts = [
                "no image-build test failed; non-build cold test(s) failed: "
                + failing_nodeids
            ]
            built_pairs = _extract_built_pairs(output_text)
            if built_pairs:
                detail_parts.append("; ".join(built_pairs))
            return Classification(VERDICT_BUILT, _single_line("; ".join(detail_parts)))
        return Classification(
            VERDICT_FAILED_TO_BUILD,
            "a Dockerfile failed to build cold (exit 1) but no structured "
            "engine/flavor/step detail could be extracted from captured output "
            "-- see the captured log for the failure",
        )

    if exit_code == 0:
        if _ALL_SKIPPED_MARKER in output_text:
            reasons = _extract_skip_reasons(output_text)
            if reasons:
                detail = _single_line("; ".join(reasons))
            else:
                detail = (
                    "every (engine, flavor) pair was narrate-skipped for an "
                    "unavailable environmental resource, but no individual skip "
                    "reason could be extracted from captured output"
                )
            return Classification(VERDICT_COULD_NOT_ATTEMPT, detail)

        built_pairs = _extract_built_pairs(output_text)
        skip_reasons = _extract_skip_reasons(output_text)
        if built_pairs:
            detail_parts = list(built_pairs)
            if skip_reasons:
                detail_parts.append(
                    f"(also narrate-skipped: {'; '.join(skip_reasons)})"
                )
            return Classification(VERDICT_BUILT, _single_line("; ".join(detail_parts)))
        if skip_reasons:
            # No pair built, pairs were narrate-skipped, and the lane still
            # exited 0 because the engine-free cold tests passed -- the
            # all-skipped marker prints only when pytest collected nothing.
            return Classification(
                VERDICT_COULD_NOT_ATTEMPT,
                _single_line(
                    _no_pair_built_detail(skip_reasons)
                    + " (the lane exited 0: the other cold tests passed)"
                ),
            )
        return Classification(
            VERDICT_BUILT,
            "run-cold-build-tests.sh reported PASSED (exit 0) but no per-pair "
            "'Image built successfully' line was captured -- see the captured "
            "log for the pairs actually exercised",
        )

    # An exit code with no documented meaning in run-cold-build-tests.sh's
    # own contract (0/1/2). Recording this as could-not-attempt (rather than
    # guessing built or failed-to-build) is the honest choice: could-not-
    # attempt already means "nothing was learned about whether the images
    # build", which is exactly true here -- this classifier does not know
    # what happened.
    return Classification(
        VERDICT_COULD_NOT_ATTEMPT,
        f"run-cold-build-tests.sh exited {exit_code}, a code this classifier has "
        "no documented mapping for (expected 0, 1, or 2); recording the ambiguity "
        "rather than guessing built or failed-to-build",
    )


def main(argv: list | None = None) -> int:
    """CLI: classify.py <exit_code> <captured_output_file>

    Prints exactly two lines to stdout: the verdict, then the detail. Both
    lines are single-line (no embedded newlines). Used by
    team/scripts/deep-verify.sh, which cannot import Python directly from
    bash.
    """
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: classify.py <exit_code> <captured_output_file>", file=sys.stderr)
        return 2

    try:
        exit_code = int(argv[0])
    except ValueError:
        print(f"classify.py: exit_code must be an integer, got {argv[0]!r}", file=sys.stderr)
        return 2

    try:
        with open(argv[1], "r", encoding="utf-8", errors="replace") as handle:
            output_text = handle.read()
    except OSError as exc:
        print(f"classify.py: could not read {argv[1]}: {exc}", file=sys.stderr)
        return 2

    result = classify_cold_build_result(exit_code, output_text)
    print(result.verdict)
    print(result.detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
