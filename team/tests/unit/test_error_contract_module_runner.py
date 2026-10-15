"""
test_error_contract_module_runner.py
====================================
Sourcing team/scripts/lib/error_contract.sh also makes the module runner
pp_run_ops available (pp_run_ops.sh), so a lib function lifted out of its
own file still reaches the pgai_agent_kanban modules wherever this
primitive is loaded:

  (A) Sourced alone, from a working directory outside the tree, with no
      PYTHONPATH and KANBAN_ROOT pointing at an empty directory, it defines
      pp_run_ops, which runs a pgai_agent_kanban module.
  (B) A pp_run_ops the caller defined before sourcing is kept, not replaced.
  (C) A wake status writer extracted on its own (set_state, with only
      error_contract.sh and atomic_write.sh sourced) rewrites status.md
      from such a working directory.
  (D) A copy of error_contract.sh with no pp_run_ops.sh beside it still
      loads under ``set -e`` and still emits; it just defines no runner.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import textwrap

_LIB_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
_ERROR_CONTRACT = _LIB_DIR / "error_contract.sh"
_ATOMIC_WRITE = _LIB_DIR / "atomic_write.sh"
_WAKE_TASK_STATE = _LIB_DIR / "wake_task_state.sh"


def _run(tmp_path: pathlib.Path, script: str) -> subprocess.CompletedProcess:
    empty_root = tmp_path / "empty-kanban-root"
    empty_root.mkdir(exist_ok=True)
    temp_dir = tmp_path / "kanban-temp"
    temp_dir.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "KANBAN_ROOT": str(empty_root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
    }
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(tmp_path),
    )


def test_sourcing_error_contract_defines_a_working_module_runner(tmp_path: pathlib.Path) -> None:
    status = tmp_path / "status.md"
    status.write_text("## State\nWORKING\n", encoding="utf-8")
    result = _run(
        tmp_path,
        f'source "{_ERROR_CONTRACT}"\n'
        f'pp_run_ops pgai_agent_kanban.task_status read "{status}" --section State --raw\n',
    )
    assert (result.returncode, result.stdout) == (0, "WORKING\n"), result.stderr


def test_a_caller_defined_runner_is_kept(tmp_path: pathlib.Path) -> None:
    result = _run(
        tmp_path,
        "pp_run_ops() { echo caller-runner; }\n"
        f'source "{_ERROR_CONTRACT}"\n'
        "pp_run_ops anything\n",
    )
    assert (result.returncode, result.stdout) == (0, "caller-runner\n"), result.stderr


def test_an_extracted_wake_writer_reaches_its_module(tmp_path: pathlib.Path) -> None:
    status = tmp_path / "status.md"
    status.write_text("## State\nWORKING\n\n## Summary\nx\n", encoding="utf-8")
    script = textwrap.dedent(f"""\
        source "{_ERROR_CONTRACT}"
        source "{_ATOMIC_WRITE}"
        eval "$(awk '/^set_state\\(\\) {{/{{f=1}} f{{print}} f&&/^}}$/{{exit}}' "{_WAKE_TASK_STATE}")"
        set_state "{status}" BLOCKED
    """)
    result = _run(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert status.read_text(encoding="utf-8") == "## State\nBLOCKED\n\n## Summary\nx\n"


def test_a_copy_without_the_runner_beside_it_still_loads_and_emits(tmp_path: pathlib.Path) -> None:
    lone = tmp_path / "lone-lib"
    lone.mkdir()
    shutil.copy(_ERROR_CONTRACT, lone / "error_contract.sh")
    result = _run(
        tmp_path,
        f'source "{lone / "error_contract.sh"}"\n'
        'declare -F pp_run_ops || echo "no runner"\n'
        'pgai_emit_error_contract demo.key "a message" "echo remedy"\n',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "no runner\n"
    assert "error:   demo.key" in result.stderr
