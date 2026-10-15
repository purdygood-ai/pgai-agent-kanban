#!/usr/bin/env python3
"""
gated_runner_durations.py -- record a certification run's slowest tests.

Every certification run of a gated runner (run-unit-tests.sh,
run-integration-tests.sh -- any run without --files or --quarantined)
passes each pytest lane ``--durations=25 --durations-min=1.0``, so the log
shows the slowest tests, and ``--junitxml=<lane xml>``, so the per-test
times survive the run. After the last lane, ``record`` merges the lanes'
XML reports into one JSON file beside the run's findings file
(``lint/durations-<suite>.json`` under the temp root):

    {
      "suite": "unit",
      "limit": 25,
      "min_seconds": 1.0,
      "tests": [{"test": "tests.unit.test_x::test_slow", "seconds": 1.204}, ...],
      "total_seconds": 1731.75,
      "test_count": 7908
    }

``tests`` holds at most ``limit`` entries, slowest first, each at least
``min_seconds`` (a test's seconds are its setup, call and teardown
together, as pytest's JUnit report times it); ``total_seconds`` sums the
lanes' own run times and ``test_count`` the tests they reported.

This is informational only: recording never changes a runner's exit code,
never gates anything and never files a bug. A lane that wrote no report
(it crashed, or collected nothing) is skipped; with no readable report at
all, ``record`` writes nothing and says so.

The planner (gated_runner_lanes.py) imports :func:`lane_args` so the
options and the thresholds are stated once, here.

CLI
---
    python3 gated_runner_durations.py record --suite unit|integration \\
        --out FILE [LANE_XML ...]

Prints one narration line. Exits 0 whether or not a file was written;
2 on a usage error (argparse).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import xml.etree.ElementTree as ET
from typing import Iterable, Sequence

#: How many of the slowest tests are listed (pytest --durations).
DURATIONS_LIMIT = 25
#: The shortest duration, in seconds, worth listing (pytest --durations-min).
DURATIONS_MIN_SECONDS = 1.0


def lane_args(xml_path: str) -> list[str]:
    """The pytest options a certification lane carries to be recorded."""
    return [
        f"--durations={DURATIONS_LIMIT}",
        f"--durations-min={DURATIONS_MIN_SECONDS}",
        f"--junitxml={xml_path}",
    ]


def _float(text: "str | None") -> float:
    try:
        return float(text or 0)
    except ValueError:
        return 0.0


def _int(text: "str | None") -> int:
    try:
        return int(text or 0)
    except ValueError:
        return 0


def _suites(root: ET.Element) -> list[ET.Element]:
    """The <testsuite> elements of a JUnit report, whatever its root."""
    if root.tag == "testsuite":
        return [root]
    return list(root.iter("testsuite"))


def summarize(xml_paths: Iterable["str | pathlib.Path"], suite: str) -> "dict | None":
    """Merge the lanes' JUnit reports into the durations record.

    Returns None when no report could be read.
    """
    entries: list[tuple[float, str]] = []
    total_seconds = 0.0
    test_count = 0
    read_any = False
    for path in xml_paths:
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        read_any = True
        for testsuite in _suites(root):
            total_seconds += _float(testsuite.get("time"))
            test_count += _int(testsuite.get("tests"))
            for case in testsuite.iter("testcase"):
                seconds = _float(case.get("time"))
                if seconds < DURATIONS_MIN_SECONDS:
                    continue
                classname = case.get("classname", "")
                name = case.get("name", "")
                entries.append((seconds, f"{classname}::{name}" if classname else name))
    if not read_any:
        return None
    entries.sort(key=lambda entry: (-entry[0], entry[1]))
    return {
        "suite": suite,
        "limit": DURATIONS_LIMIT,
        "min_seconds": DURATIONS_MIN_SECONDS,
        "tests": [
            {"test": test, "seconds": round(seconds, 3)}
            for seconds, test in entries[:DURATIONS_LIMIT]
        ],
        "total_seconds": round(total_seconds, 3),
        "test_count": test_count,
    }


def record(suite: str, out: "str | pathlib.Path", xml_paths: Sequence["str | pathlib.Path"]) -> str:
    """Write the durations record for *suite* to *out*; return a narration line."""
    summary = summarize(xml_paths, suite)
    if summary is None:
        return (
            f"[durations] {suite}: no lane wrote a readable timing report; "
            "no durations file this run (informational only)."
        )
    out_path = pathlib.Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    staging = out_path.with_name(f".{out_path.name}.{os.getpid()}.tmp")
    staging.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    os.replace(staging, out_path)
    return (
        f"[durations] {suite}: {len(summary['tests'])} test(s) took at least "
        f"{DURATIONS_MIN_SECONDS}s of {summary['test_count']} in {summary['total_seconds']}s; "
        f"slowest list written to {out_path} (informational only)."
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gated_runner_durations.py",
        description="Record a certification run's slowest tests from its lanes' JUnit reports.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    rec = sub.add_parser(
        "record",
        help="merge lane reports into durations-<suite>.json",
        description=(
            "Merge the lanes' JUnit XML reports into one JSON record of the "
            f"slowest tests (at most {DURATIONS_LIMIT}, each at least "
            f"{DURATIONS_MIN_SECONDS}s), the total run time and the test count."
        ),
    )
    rec.add_argument("--suite", required=True, choices=("unit", "integration"),
                     help="the suite the run certified")
    rec.add_argument("--out", required=True, metavar="FILE",
                     help="the JSON file to write (lint/durations-<suite>.json)")
    rec.add_argument("lane_xml", nargs="*", metavar="LANE_XML",
                     help="a lane's --junitxml report; unreadable ones are skipped")
    return parser


def main(argv: "Sequence[str] | None" = None) -> int:
    """Run the durations CLI: ``record --suite SUITE --out FILE [LANE_XML ...]``.

    Prints one narration line. Exits 0 whether or not a file was written;
    2 on a usage error (argparse).
    """
    args = _build_parser().parse_args(argv)
    print(record(args.suite, args.out, args.lane_xml))
    return 0


if __name__ == "__main__":
    sys.exit(main())
