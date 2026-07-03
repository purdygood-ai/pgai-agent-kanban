#!/usr/bin/env python3
"""
pm_status.py — Quick dashboard of all tasks and their states.

Usage:
    python3 pm_status.py [--team-root /path/to/kanban] [--filter STATE] [--verbose]
    python3 pm_status.py --queues  (show per-agent queue backlogs)
    python3 pm_status.py --check-desync  (detect queue marker vs status.md state mismatches)
"""

import argparse
import os
import re
import sys
from pathlib import Path
from collections import Counter

# lib.config provides get_config() for config-driven directory defaults.
sys.path.insert(0, str(Path(__file__).parent))
from lib.config import get_config


STATE_EMOJI = {
    "BACKLOG": "⬜",
    "WAITING": "⏸️ ",
    "WORKING": "🔵",
    "BLOCKED": "🔴",
    "REVIEW": "🟡",
    "DONE": "✅",
    "WONT-DO": "⛔",
}


def get_status_fields(status_file):
    """Parse a status.md file into a dict of heading -> body."""
    text = status_file.read_text()
    fields = {}
    current_heading = None
    current_body = []

    for line in text.splitlines():
        m = re.match(r'^## (.+)$', line)
        if m:
            if current_heading:
                fields[current_heading] = "\n".join(current_body).strip()
            current_heading = m.group(1).strip()
            current_body = []
        elif current_heading:
            current_body.append(line)

    if current_heading:
        fields[current_heading] = "\n".join(current_body).strip()

    return fields


def is_queue_entry(line):
    """Return True if line looks like a task queue entry (e.g. '- [ ] TASK-ID')."""
    # Matches: - [ ] TASK-ID  or  - [x] TASK-ID  or  * [ ] TASK-ID
    return bool(re.match(r'^\s*[-*]\s*\[.?\]\s*\S+', line))


def scan_queue_files(tasks_dir):
    """Scan per-agent queue backlog files under tasks/queues/.

    Reads flat layout tasks/queues/<agent>_backlog.md files.

    Returns a list of dicts with keys: queue, task_line.
    """
    queues_dir = tasks_dir / "queues"
    entries = []

    # Flat layout tasks/queues/<agent>_backlog.md
    if queues_dir.is_dir():
        for backlog_file in sorted(queues_dir.glob("*_backlog.md")):
            queue_name = backlog_file.stem  # e.g. "coder_backlog"
            try:
                text = backlog_file.read_text()
            except OSError:
                continue
            for line in text.splitlines():
                if is_queue_entry(line):
                    entries.append({"queue": queue_name, "task_line": line.strip()})

    return entries


def extract_task_id_from_queue_line(line):
    """Extract the task ID and marker from a queue entry line.

    Supports formats like:
      - [ ] TASK-ID
      - [x] TASK-ID   (completed)
      * [ ] TASK-ID
      - [ ] TASK-ID  optional trailing text

    Returns a tuple (task_id, is_done) where is_done is True when the
    marker is [x] (case-insensitive), or None if the line is not a valid
    queue entry.
    """
    m = re.match(r'^\s*[-*]\s*\[(.?)\]\s*(\S+)', line)
    if not m:
        return None
    marker = m.group(1).strip().lower()
    task_id = m.group(2).strip()
    is_done = marker == "x"
    return task_id, is_done


# States that correspond to the [x] (completed) queue marker
DONE_STATES = {"DONE", "WONT-DO"}


def check_desync(tasks_dir, team_root):
    """Scan queue files vs status.md files and report mismatches.

    A desync is any case where the queue marker and the task state disagree:
      - Marker is [x] but status.md state is not DONE or WONT-DO
      - Marker is [ ] but status.md state is DONE or WONT-DO

    Reports each desync as:
      <queue> <task_id> [<marker>] vs <state>

    Returns the number of desyncs found.

    Reads flat layout tasks/queues/<agent>_backlog.md files.
    """
    queues_dir = tasks_dir / "queues"
    desyncs = []

    # Flat layout tasks/queues/<agent>_backlog.md
    queue_files = []
    if queues_dir.is_dir():
        for f in sorted(queues_dir.glob("*_backlog.md")):
            queue_files.append((f, f.stem))

    for queue_file, queue_name in queue_files:
        try:
            text = queue_file.read_text()
        except OSError:
            continue

        for line in text.splitlines():
            parsed = extract_task_id_from_queue_line(line)
            if parsed is None:
                continue
            task_id, marker_done = parsed

            # Look up status.md for this task
            task_dir = tasks_dir / task_id
            status_file = task_dir / "status.md"
            if not status_file.is_file():
                # Cannot validate — skip silently
                continue

            fields = get_status_fields(status_file)
            state = fields.get("State", "UNKNOWN").strip()

            state_done = state in DONE_STATES

            if marker_done and not state_done:
                marker_str = "[x]"
                desyncs.append((queue_name, task_id, marker_str, state))
            elif not marker_done and state_done:
                marker_str = "[ ]"
                desyncs.append((queue_name, task_id, marker_str, state))

    return desyncs


def print_desync_report(desyncs, team_root):
    """Print the desync report to stdout."""
    print("=" * 70)
    print("  DESYNC CHECK REPORT")
    print(f"  Root: {team_root}")
    print("=" * 70)
    print()

    if not desyncs:
        print("  No desyncs found. Queue markers and status.md states are in sync.")
        print()
        return

    print(f"  {len(desyncs)} desync(s) found:")
    print()
    for queue_name, task_id, marker_str, state in desyncs:
        print(f"  {queue_name}  {task_id}  {marker_str} vs {state}")
    print()


def print_queue_dashboard(tasks_dir, team_root):
    """Print the per-agent queue backlog dashboard.

    Reads flat layout tasks/queues/<agent>_backlog.md files.
    """
    queues_dir = tasks_dir / "queues"

    print("=" * 70)
    print("  QUEUE BACKLOG DASHBOARD")
    print(f"  Root: {team_root}")
    print("=" * 70)
    print()

    found_any = False
    seen_queue_names: set[str] = set()

    # Flat layout tasks/queues/<agent>_backlog.md
    if queues_dir.is_dir():
        backlog_files = sorted(queues_dir.glob("*_backlog.md"))
        if backlog_files:
            print(f"  Per-agent queues: tasks/queues/")
            print()
            for backlog_file in backlog_files:
                queue_name = backlog_file.stem
                seen_queue_names.add(queue_name)
                try:
                    text = backlog_file.read_text()
                except OSError:
                    print(f"  [{queue_name}] (unreadable)")
                    continue
                task_lines = [
                    l.strip() for l in text.splitlines()
                    if is_queue_entry(l)
                ]
                count = len(task_lines)
                print(f"  [{queue_name}] — {count} item(s)")
                for tl in task_lines:
                    print(f"      {tl}")
                if not task_lines:
                    print(f"      (empty)")
                print()
                found_any = True

    if not found_any:
        print("  No queue files found.")
        print()


def main():
    parser = argparse.ArgumentParser(description="Kanban status dashboard")
    parser.add_argument("--team-root", default=None, help="Kanban root path")
    parser.add_argument("--filter", default=None, help="Filter by state (e.g., WORKING, BLOCKED)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show full status details")
    parser.add_argument("--owner", default=None, help="Filter by owner prefix (e.g., CLAUDE)")
    parser.add_argument(
        "--queues",
        action="store_true",
        help="Show per-agent queue backlogs (tasks/queues/*_backlog.md)",
    )
    parser.add_argument(
        "--check-desync",
        action="store_true",
        help=(
            "Detect mismatches between queue markers ([x]/[ ]) and status.md states. "
            "Exit 0 if no desyncs, exit 1 if any found."
        ),
    )
    args = parser.parse_args()

    if args.team_root is not None:
        # --team-root explicitly provided: all path lookups must derive from it
        # only.  Do NOT fall back to PGAI_PROJECT_ROOT, PGAI_TASKS_DIR, or any
        # other env var so that test subprocesses using a synthetic kanban tree
        # do not accidentally see the operator-live queues.
        team_root = str(Path(args.team_root).resolve())
        tasks_dir = Path(team_root) / "tasks"
    else:
        # No explicit --team-root: use config with standard env-var precedence.
        # PGAI_PROJECT_ROOT takes priority over PGAI_AGENT_KANBAN_ROOT_PATH
        # for per-project path resolution (tasks, queues, release-state, bugs).
        cli_root = os.environ.get("PGAI_PROJECT_ROOT") or None
        cfg = get_config(kanban_root=cli_root)
        team_root = cfg["KANBAN_ROOT"]
        tasks_dir = Path(cfg["PGAI_TASKS_DIR"])
    if not tasks_dir.is_dir():
        print(f"ERROR: Tasks directory not found: {tasks_dir}", file=sys.stderr)
        sys.exit(1)

    if args.queues:
        print_queue_dashboard(tasks_dir, team_root)
        return

    if args.check_desync:
        desyncs = check_desync(tasks_dir, team_root)
        print_desync_report(desyncs, team_root)
        sys.exit(1 if desyncs else 0)

    tasks = []
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        if task_dir.name.startswith("TASK-") or task_dir.name == "queues":
            continue
        if args.owner and not task_dir.name.startswith(args.owner):
            continue

        status_file = task_dir / "status.md"
        if not status_file.is_file():
            tasks.append({
                "id": task_dir.name,
                "state": "NO-STATUS",
                "summary": "status.md missing",
                "blockers": "",
                "needs_human": "",
                "artifacts": "",
            })
            continue

        fields = get_status_fields(status_file)
        state = fields.get("State", "UNKNOWN")
        if args.filter and state != args.filter.upper():
            continue

        tasks.append({
            "id": task_dir.name,
            "state": state,
            "summary": fields.get("Summary", ""),
            "blockers": fields.get("Blockers", "none"),
            "needs_human": fields.get("Needs Human", ""),
            "artifacts": fields.get("Artifacts", "none"),
            "next_step": fields.get("Next Recommended Step", ""),
        })

    if not tasks:
        print("No tasks found matching criteria.")
        return

    state_counts = Counter(t["state"] for t in tasks)

    print("=" * 70)
    print("  KANBAN STATUS DASHBOARD")
    print(f"  Root: {team_root}")
    print("=" * 70)
    print()

    total = len(tasks)
    parts = []
    for state in ["DONE", "REVIEW", "WORKING", "BACKLOG", "WAITING", "BLOCKED", "WONT-DO"]:
        count = state_counts.get(state, 0)
        if count > 0:
            emoji = STATE_EMOJI.get(state, "?")
            parts.append(f"{emoji} {state}: {count}")
    print(f"  Total: {total}  |  " + "  |  ".join(parts))
    print()

    done_count = state_counts.get("DONE", 0) + state_counts.get("WONT-DO", 0)
    review_count = state_counts.get("REVIEW", 0)
    working_count = state_counts.get("WORKING", 0)
    if total > 0:
        bar_width = 40
        done_bar = int(bar_width * done_count / total)
        review_bar = int(bar_width * review_count / total)
        working_bar = int(bar_width * working_count / total)
        remaining_bar = bar_width - done_bar - review_bar - working_bar
        bar = "█" * done_bar + "▓" * review_bar + "▒" * working_bar + "░" * remaining_bar
        pct = int(100 * done_count / total)
        print(f"  Progress: [{bar}] {pct}% complete")
        print()

    print("-" * 70)
    for t in tasks:
        emoji = STATE_EMOJI.get(t["state"], "?")
        human_flag = " 👤" if t.get("needs_human", "").lower() == "yes" else ""
        print(f"  {emoji} {t['id']}{human_flag}")

        if args.verbose:
            print(f"     State: {t['state']}")
            if t["summary"]:
                summary = t["summary"][:120] + "..." if len(t["summary"]) > 120 else t["summary"]
                print(f"     Summary: {summary}")
            if t["blockers"] and t["blockers"] != "none":
                print(f"     Blockers: {t['blockers'][:120]}")
            if t.get("next_step"):
                print(f"     Next: {t['next_step'][:120]}")
            print()

    blocked = [t for t in tasks if t["state"] == "BLOCKED"]
    waiting = [t for t in tasks if t["state"] == "WAITING"]
    needs_human = [t for t in tasks if t.get("needs_human", "").lower() == "yes"]
    in_review = [t for t in tasks if t["state"] == "REVIEW"]

    if blocked or needs_human or in_review or waiting:
        print()
        print("=" * 70)
        print("  ACTION NEEDED")
        print("=" * 70)

        if needs_human:
            print(f"\n  👤 Needs your attention ({len(needs_human)}):")
            for t in needs_human:
                print(f"     - {t['id']}: {t.get('blockers', 'check status')[:80]}")

        if in_review:
            print(f"\n  🟡 Ready for review ({len(in_review)}):")
            for t in in_review:
                summary = t["summary"][:80] + "..." if len(t["summary"]) > 80 else t["summary"]
                print(f"     - {t['id']}: {summary}")

        if blocked:
            print(f"\n  🔴 Blocked — needs manual unblock ({len(blocked)}):")
            for t in blocked:
                print(f"     - {t['id']}: {t.get('blockers', 'unknown')[:80]}")

        if waiting:
            print(f"\n  ⏸️  Waiting on prerequisites ({len(waiting)}) — auto-resolves:")
            for t in waiting:
                print(f"     - {t['id']}: {t.get('blockers', 'check status')[:80]}")

    print()


if __name__ == "__main__":
    main()
