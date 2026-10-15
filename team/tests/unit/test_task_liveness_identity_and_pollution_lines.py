"""
test_task_liveness_identity_and_pollution_lines.py
==================================================
Two wake-path comparisons that used to accept a near match:

  (A) Task liveness names one process, not one pid number. A task-liveness
      record carries ``pid_start`` (the pid's start time) and ``boot_id``
      beside the pid; a pid that answers ``kill -0`` but was started at a
      different time, or a record written under another boot, reads
      ``stale`` -- in the bash classifier (lock_provenance.sh,
      ``pgai_lock_pid_liveness_verdict`` through ``task_liveness_verdict``)
      and in its Python form (``ops.locks.pid_liveness_verdict``). A record
      without those fields reads exactly as before.
  (B) The post-task pollution sweep compares porcelain lines whole
      (``wd_pollution_new_lines``): a new ``?? a/b.md`` is reported even
      when ``?? a/b.md.bak`` was already in the pre-task snapshot.

The functions under test are the real bodies, sourced or extracted from
their stage files; KANBAN_ROOT is a throwaway directory under tmp_path. The
live process is a ``sleep`` this test starts and kills.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap
from collections.abc import Iterator

import pytest

from pgai_agent_kanban.ops import locks as ops_locks

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
_LOCK_PROVENANCE = _SCRIPTS / "lib" / "lock_provenance.sh"
_WAKE_TASK_STATE = _SCRIPTS / "lib" / "wake_task_state.sh"
_WAKE_DRIVER_FINALIZE = _SCRIPTS / "lib" / "wake_driver_finalize.sh"
_TASK = "CODER-20260101-001-liveness"


def _extract(path: pathlib.Path, names: list[str]) -> str:
    """Bash that defines the named top-level functions from *path* (brace-matched)."""
    alternation = "|".join(re.escape(name) for name in names)
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^({alternation})\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{ depth--; if (depth == 0) found=0 }}
                }}
            }}
        ' '{path}')"
    """)


def _bash(tmp_path: pathlib.Path, body: str) -> str:
    script = tmp_path / "run.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'export KANBAN_ROOT="{tmp_path / "root"}"\n'
        f'source "{_LOCK_PROVENANCE}"\n'
        + _extract(_WAKE_TASK_STATE, ["task_liveness_record_path", "task_liveness_record", "task_liveness_verdict"])
        + _extract(_WAKE_DRIVER_FINALIZE, ["wd_pollution_new_lines"])
        + body,
        encoding="utf-8",
    )
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def live_pid() -> Iterator[int]:
    proc = subprocess.Popen(["sleep", "300"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


def _start_time(pid: int) -> str:
    stat = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return stat.rpartition(")")[2].split()[19]


def _boot_id() -> str:
    return pathlib.Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()


pytestmark = pytest.mark.skipif(
    not pathlib.Path("/proc/sys/kernel/random/boot_id").exists(), reason="needs Linux /proc"
)


# ---------------------------------------------------------------------------
# (A) liveness identity
# ---------------------------------------------------------------------------


def test_liveness_record_carries_the_start_time_and_boot_id_and_reads_held(
    tmp_path: pathlib.Path, live_pid: int
) -> None:
    out = _bash(tmp_path, f'task_liveness_record "{_TASK}" claude {live_pid} dispatch\ntask_liveness_verdict "{_TASK}"\n')

    record = (tmp_path / "root" / "locks" / "task-liveness" / f"{_TASK}.record").read_text(encoding="utf-8")
    assert f"pid_start={_start_time(live_pid)}\n" in record, record
    assert f"boot_id={_boot_id()}\n" in record, record
    assert out.strip() == "held"


def _verdict_for_record(tmp_path: pathlib.Path, record: str) -> str:
    path = tmp_path / "root" / "locks" / "task-liveness" / f"{_TASK}.record"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record, encoding="utf-8")
    return _bash(tmp_path, f'task_liveness_verdict "{_TASK}"\n').strip()


def _record(pid: int, start: str, boot: str) -> str:
    return f"holder=claude\npid={pid}\nop=dispatch\ntask={_TASK}\npid_start={start}\nboot_id={boot}\n"


def test_a_live_pid_with_a_different_start_time_reads_stale(tmp_path: pathlib.Path, live_pid: int) -> None:
    other_start = str(int(_start_time(live_pid)) + 1)
    record = _record(live_pid, other_start, _boot_id())

    assert _verdict_for_record(tmp_path, record) == "stale"
    assert ops_locks.pid_liveness_verdict(record) == "stale"


def test_a_record_from_another_boot_reads_stale(tmp_path: pathlib.Path, live_pid: int) -> None:
    record = _record(live_pid, _start_time(live_pid), "00000000-0000-0000-0000-000000000000")

    assert _verdict_for_record(tmp_path, record) == "stale"
    assert ops_locks.pid_liveness_verdict(record) == "stale"


def test_a_matching_identity_reads_held(tmp_path: pathlib.Path, live_pid: int) -> None:
    record = _record(live_pid, _start_time(live_pid), _boot_id())

    assert _verdict_for_record(tmp_path, record) == "held"
    assert ops_locks.pid_liveness_verdict(record) == "held"


def test_a_record_without_identity_fields_reads_as_before(tmp_path: pathlib.Path, live_pid: int) -> None:
    record = f"holder=claude\npid={live_pid}\nop=dispatch\ntask={_TASK}\n"

    assert _verdict_for_record(tmp_path, record) == "held"
    assert ops_locks.pid_liveness_verdict(record) == "held"
    blank = _record(live_pid, "", "")
    assert _verdict_for_record(tmp_path, blank) == "held"
    assert ops_locks.pid_liveness_verdict(blank) == "held"


def test_proc_start_time_reads_past_a_command_name_with_spaces_and_parens(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = tmp_path / "proc" / "4242"
    proc.mkdir(parents=True)
    fields = ["S"] + [str(n) for n in range(4, 22)] + ["777"] + ["0"] * 10
    (proc / "stat").write_text("4242 (a (b) c) " + " ".join(fields) + "\n", encoding="utf-8")
    monkeypatch.setattr(ops_locks, "_PROC_ROOT", tmp_path / "proc")

    assert ops_locks.proc_start_time("4242") == "777"
    assert ops_locks.proc_start_time("nope") == ""


# ---------------------------------------------------------------------------
# (B) pollution sweep: whole-line compare
# ---------------------------------------------------------------------------


def test_new_file_whose_path_prefixes_an_existing_one_is_reported(tmp_path: pathlib.Path) -> None:
    out = _bash(
        tmp_path,
        "pre=$'?? a/b.md.bak\\n M docs/notes.md.orig'\n"
        "post=$'?? a/b.md.bak\\n?? a/b.md\\n M docs/notes.md.orig\\n M docs/notes.md'\n"
        'wd_pollution_new_lines "$pre" "$post"\n',
    )

    assert out.splitlines() == ["?? a/b.md", " M docs/notes.md"]


def test_unchanged_lines_are_not_reported(tmp_path: pathlib.Path) -> None:
    out = _bash(
        tmp_path,
        "pre=$'?? a/b.md\\n M c.txt'\n"
        'wd_pollution_new_lines "$pre" "$pre"\n'
        "wd_pollution_new_lines '' $'?? fresh.md'\n",
    )

    assert out.splitlines() == ["?? fresh.md"]
