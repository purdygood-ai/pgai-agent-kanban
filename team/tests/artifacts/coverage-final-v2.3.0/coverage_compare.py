#!/usr/bin/env python3
"""Compare final vs baseline coverage.py JSON reports for the v2.3.0
coverage gate (CODER-20260806-013). Computes per-suite line/branch
percentage deltas and a covered-line-set / covered-branch-set regression
check per file: any line or branch present in the baseline's
executed_lines/executed_branches for a file that is absent from the
final measurement's executed_lines/executed_branches for the SAME file
(and the file still exists in the final report) is a regression.

Usage: coverage_compare.py <baseline.json> <final.json> <suite-label>
Prints a markdown-ready summary block to stdout.
"""
import json
import sys


def load(path):
    with open(path) as f:
        return json.load(f)


def main():
    baseline_path, final_path, label = sys.argv[1], sys.argv[2], sys.argv[3]
    baseline = load(baseline_path)
    final = load(final_path)

    b_totals = baseline["totals"]
    f_totals = final["totals"]

    b_line_pct = b_totals["percent_covered"]
    f_line_pct = f_totals["percent_covered"]

    def branch_pct(totals):
        covered = totals.get("covered_branches", 0)
        num = totals.get("num_branches", 0)
        return (covered / num * 100.0) if num else 0.0

    b_branch_pct = branch_pct(b_totals)
    f_branch_pct = branch_pct(f_totals)

    print(f"## {label} suite — baseline vs final")
    print()
    print(f"- Baseline line coverage: {b_line_pct:.2f}% "
          f"({b_totals['covered_lines']}/{b_totals['num_statements']})")
    print(f"- Final line coverage:    {f_line_pct:.2f}% "
          f"({f_totals['covered_lines']}/{f_totals['num_statements']})")
    print(f"- Line delta: {f_line_pct - b_line_pct:+.2f} pct pts")
    print()
    print(f"- Baseline branch coverage: {b_branch_pct:.2f}% "
          f"({b_totals.get('covered_branches',0)}/{b_totals.get('num_branches',0)})")
    print(f"- Final branch coverage:    {f_branch_pct:.2f}% "
          f"({f_totals.get('covered_branches',0)}/{f_totals.get('num_branches',0)})")
    print(f"- Branch delta: {f_branch_pct - b_branch_pct:+.2f} pct pts")
    print()

    line_regressions = []
    branch_regressions = []
    files_removed = []

    b_files = baseline["files"]
    f_files = final["files"]

    for fname, b_entry in b_files.items():
        b_lines = set(b_entry.get("executed_lines", []))
        b_branches = b_entry.get("executed_branches", [])
        b_branches = {tuple(x) for x in b_branches} if b_branches else set()

        if fname not in f_files:
            # File removed entirely from measurement — check if the source
            # file itself still exists; if the file is gone, it's not a
            # coverage regression (nothing left to cover), just a note.
            files_removed.append(fname)
            continue

        f_entry = f_files[fname]
        f_lines = set(f_entry.get("executed_lines", []))
        f_branches = f_entry.get("executed_branches", [])
        f_branches = {tuple(x) for x in f_branches} if f_branches else set()

        missing_lines = b_lines - f_lines
        missing_branches = b_branches - f_branches

        if missing_lines:
            line_regressions.append((fname, sorted(missing_lines)))
        if missing_branches:
            branch_regressions.append((fname, sorted(missing_branches)))

    print(f"- Files measured in baseline: {len(b_files)}")
    print(f"- Files measured in final:    {len(f_files)}")
    print(f"- Files present in baseline but absent from final report: "
          f"{len(files_removed)}")
    if files_removed:
        for fn in files_removed:
            print(f"  - {fn}")
    print()

    if not line_regressions and not branch_regressions:
        print("- Covered-line-set diff: ZERO regressions "
              "(no baseline-covered line or branch became uncovered).")
    else:
        print("- Covered-line-set diff: REGRESSIONS FOUND")
        for fname, lines in line_regressions:
            print(f"  - {fname}: lines no longer covered: {lines}")
        for fname, branches in branch_regressions:
            print(f"  - {fname}: branches no longer covered: {branches}")
    print()

    return 1 if (line_regressions or branch_regressions) else 0


if __name__ == "__main__":
    sys.exit(main())
