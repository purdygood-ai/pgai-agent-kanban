#!/usr/bin/env python3
"""pseudocron.py — Foreground cron-like scheduler for sandboxed environments.

Reads pseudocron.cfg (schedule) and pseudocron.env (environment) from the
kanban root directory, then loops forever firing jobs whose minute value
matches the current minute.

Usage:
    python3 pseudocron.py

Environment:
    PGAI_AGENT_KANBAN_ROOT_PATH          Root directory containing
                                         pseudocron.cfg and pseudocron.env
                                         (canonical var).

Output:
    stdout — one line per fired job:
        <ISO timestamp> fired (minute=<NN>): <command>
    stderr — startup info, parse errors (prefixed with ERROR), shutdown notice.

Stop:
    SIGINT (Ctrl-C) or SIGTERM — clean exit with status 0.
    Already-running child processes are not killed.
"""

import os
import pathlib
import re
import signal
import subprocess
import sys
import time


# ---------------------------------------------------------------------------
# Parsers (importable by unit tests)
# ---------------------------------------------------------------------------

def parse_config(text, source="pseudocron.cfg"):
    """Parse pseudocron config text into a list of (minute, command) tuples.

    Skips blank lines and lines whose first non-whitespace character is '#'.
    Logs parse errors to stderr and skips the offending line; does not raise.

    Args:
        text (str): Contents of the config file.
        source (str): Display name used in error messages.

    Returns:
        list of (int, str): Valid (minute, command) pairs.
    """
    jobs = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Split on whitespace: first token is minute, rest is command.
        parts = re.split(r"\s+", line, maxsplit=1)
        minute_str = parts[0]
        command = parts[1].strip() if len(parts) > 1 else ""

        # Validate minute field.
        try:
            minute = int(minute_str)
        except ValueError:
            print(
                f"ERROR {_ts()} parse error {source} line {lineno}:"
                f' invalid minute "{minute_str}"',
                file=sys.stderr,
            )
            continue
        if not 0 <= minute <= 59:
            print(
                f"ERROR {_ts()} parse error {source} line {lineno}:"
                f" minute {minute} out of range 0-59",
                file=sys.stderr,
            )
            continue

        # Validate command field.
        if not command:
            print(
                f"ERROR {_ts()} parse error {source} line {lineno}: empty command",
                file=sys.stderr,
            )
            continue

        jobs.append((minute, command))
    return jobs


def parse_env(text, source="pseudocron.env"):
    """Parse pseudocron env text into a dict of NAME -> VALUE pairs.

    Accepts:
        export NAME=VALUE
        NAME=VALUE
        # comment lines
        blank lines

    Quoted values (single or double) have their outer quotes stripped so
    that spaces inside are preserved.  No shell expansion is performed.

    Args:
        text (str): Contents of the env file.
        source (str): Display name used in error messages (unused currently).

    Returns:
        dict: {name: value}
    """
    env = {}
    # Pattern matches optional 'export ', NAME=VALUE with optional quoting.
    pattern = re.compile(
        r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
    )
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = pattern.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        # Strip matching outer quotes (single or double).
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ('"', "'")
        ):
            value = value[1:-1]
        env[name] = value
    return env


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ts():
    """Return current UTC time as an ISO 8601 string (seconds precision)."""
    t = time.gmtime()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", t)


def _shutdown(signum, frame):  # noqa: ARG001
    print(f"pseudocron shutting down (signal {signum})", file=sys.stderr)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    # Resolve root directory — canonical var first, new-path default.
    root = (
        os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH", "").strip()
        or str(pathlib.Path.home() / "pgai_agent_kanban")
    )
    if not root:
        print(
            "ERROR pseudocron: PGAI_AGENT_KANBAN_ROOT_PATH is not set."
            " Set PGAI_AGENT_KANBAN_ROOT_PATH to the kanban root directory and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg_path = os.path.join(root, "pseudocron.cfg")
    env_path = os.path.join(root, "pseudocron.env")

    # Load config (required).
    try:
        with open(cfg_path) as fh:
            cfg_text = fh.read()
    except FileNotFoundError:
        print(
            f"ERROR pseudocron: config file not found: {cfg_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    jobs = parse_config(cfg_text, source=cfg_path)
    print(
        f"pseudocron starting: {len(jobs)} jobs loaded from {cfg_path}",
        file=sys.stderr,
    )

    # Load env (optional).
    child_env = os.environ.copy()
    try:
        with open(env_path) as fh:
            env_text = fh.read()
        extra_env = parse_env(env_text, source=env_path)
        child_env.update(extra_env)
        print(
            f"pseudocron starting: {len(extra_env)} vars loaded from {env_path}",
            file=sys.stderr,
        )
    except FileNotFoundError:
        print(
            f"pseudocron starting: env file not found ({env_path});"
            " continuing with bare environment",
            file=sys.stderr,
        )

    # Register signal handlers.
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Main loop.
    while True:
        now = time.time()
        target = (int(now / 60) + 1) * 60
        sleep_seconds = target - now
        # Positive-sleep guard: never pass a non-positive value to sleep().
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        current_minute = time.localtime().tm_min
        ts = _ts()

        for minute, command in jobs:
            if minute != current_minute:
                continue
            print(f"{ts} fired (minute={minute:02d}): {command}", file=sys.stderr)
            sys.stdout.flush()
            try:
                subprocess.Popen(["bash", "-c", command], env=child_env)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"ERROR {ts} spawn failed (minute={minute:02d}):"
                    f" {command} ({type(exc).__name__}: {exc})",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    main()
