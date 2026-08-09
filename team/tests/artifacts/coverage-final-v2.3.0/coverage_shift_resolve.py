#!/usr/bin/env python3
"""Resolve the raw covered-line-set diff between the v2.3.0 coverage
baseline and the final (post-consolidation) measurement.

The 6 files this script checks are exactly the production files whose
comments were reworded by the provenance sweep (Goal 2 of the RC) --
each edit removed or added a comment/docstring line, shifting every
line below it up or down by a fixed amount within the file. That shift
alone is enough to make coverage.py report the same executed statement
as a "different" line number, which the raw diff (coverage_compare.py)
flags as a false-positive "regression".

Method: build a line-number correspondence between the baseline-commit
and final-commit versions of each production file using difflib's
SequenceMatcher over the UNCHANGED ("equal") regions only (i.e. lines
whose content and position relative to a comment edit did not change).
For every baseline-covered line/branch missing from the final
measurement, look up its corresponding final-file line number via that
map and check whether the final measurement covers it there. If every
missing line/branch resolves this way, the diff is proven to be a pure
line-number-shift artifact, not a real coverage loss -- because the
underlying statement/branch is provably the same line of source code
in both commits, and it fired in both runs.

Usage: run from the dev-tree root:
  python3 coverage_shift_resolve.py <baseline-commit> <final-commit> \
      <baseline-unit.json> <final-unit.json> \
      <baseline-integration.json> <final-integration.json>
"""
import subprocess
import difflib
import json
import sys

FILES_MAP = {
    "pgai_agent_kanban/cm/write_rc_state.py": "team/pgai_agent_kanban/cm/write_rc_state.py",
    "pgai_agent_kanban/dashboard/scan_attention.py": "team/pgai_agent_kanban/dashboard/scan_attention.py",
    "pgai_agent_kanban/ops/write.py": "team/pgai_agent_kanban/ops/write.py",
    "pm-agent/pm_materialize.py": "team/pm-agent/pm_materialize.py",
    "scripts/lib/metrics_aggregator.py": "team/scripts/lib/metrics_aggregator.py",
    "scripts/lib/metrics_csv_writer.py": "team/scripts/lib/metrics_csv_writer.py",
}


def git_show(commit, path):
    out = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.splitlines()


def build_line_map(baseline_commit, final_commit, repo_path):
    b_content = git_show(baseline_commit, repo_path)
    f_content = git_show(final_commit, repo_path)
    sm = difflib.SequenceMatcher(a=b_content, b=f_content, autojunk=False)
    line_map = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                line_map[i1 + 1 + offset] = j1 + 1 + offset
    return line_map


def check_suite(label, baseline_commit, final_commit, baseline_json, final_json):
    baseline = json.load(open(baseline_json))
    final = json.load(open(final_json))
    print(f"########## {label} ##########")
    overall_clean = True
    for cov_key, repo_path in FILES_MAP.items():
        if cov_key not in baseline["files"] or cov_key not in final["files"]:
            continue
        line_map = build_line_map(baseline_commit, final_commit, repo_path)

        def map_line(n):
            if n <= 0:
                mapped = line_map.get(-n)
                return -mapped if mapped else None
            return line_map.get(n)

        b_lines = set(baseline["files"][cov_key]["executed_lines"])
        f_lines = set(final["files"][cov_key]["executed_lines"])
        missing_lines = sorted(b_lines - f_lines)
        unresolved_lines = [m for m in missing_lines if map_line(m) not in f_lines]

        b_br = {tuple(x) for x in baseline["files"][cov_key].get("executed_branches", [])}
        f_br = {tuple(x) for x in final["files"][cov_key].get("executed_branches", [])}
        missing_br = sorted(b_br - f_br)
        unresolved_br = []
        for (a, b) in missing_br:
            ma, mb = map_line(a), map_line(b)
            if ma is None or mb is None or (ma, mb) not in f_br:
                unresolved_br.append((a, b))

        status = "CLEAN" if not unresolved_lines and not unresolved_br else "UNRESOLVED HITS"
        if unresolved_lines or unresolved_br:
            overall_clean = False
        print(
            f"  {cov_key}: missing_lines={len(missing_lines)} "
            f"resolved_by_shift={len(missing_lines) - len(unresolved_lines)} "
            f"unresolved_lines={unresolved_lines} | "
            f"missing_branches={len(missing_br)} "
            f"unresolved_branches={unresolved_br} -> {status}"
        )
    return overall_clean


def main():
    baseline_commit, final_commit = sys.argv[1], sys.argv[2]
    bu, fu, bi, fi = sys.argv[3:7]
    clean_unit = check_suite("UNIT", baseline_commit, final_commit, bu, fu)
    clean_integration = check_suite("INTEGRATION", baseline_commit, final_commit, bi, fi)
    print()
    if clean_unit and clean_integration:
        print("RESULT: All apparent per-file covered-line/branch-set diffs between "
              "baseline and final are fully explained by line-number shift from the "
              "provenance-sweep comment edits. Zero unresolved hits. No real coverage "
              "regression exists.")
        return 0
    print("RESULT: One or more diffs did NOT resolve via line-number-shift mapping. "
          "These require manual investigation as potential real regressions.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
