"""
test_intake_filename_guard.py — the intake.sh path (ops.write.deposit_intake)
refuses the same names as POST /operations/intake, through the one shared
check (lib.filename_contract.intake_destination), and writes nothing.

What is proven here:

  1. deposit_intake refuses a source whose deposited name would not be a
     bare intake filename -- a traversal path such as '<dir>/sub/../x.md'
     (its name 'x.md' routes nowhere), and names that keep a routing prefix
     but are not bare ('..' inside the name, a backslash, an encoded
     slash) -- and nothing lands in
     bugs/, priority/ or requirements/.
  2. The refusal reaches the shared helper: a spy on the name ops.write
     binds sees the basename, and a not-bare name is refused dual-channel
     under the helper's own machine key.
  3. A source path that climbs ('<dir>/sub/../' before a bug-prefixed
     name) is still accepted: the
     staged file may live anywhere, but what is deposited is the bare name,
     inside the project's bugs/ directory.
  4. The real intake.sh, run against a throwaway kanban root, exits 2 with
     the dual-channel block for a not-bare name and deposits nothing.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.lib import filename_contract
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import Refused

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_INTAKE_SH = _TEAM_DIR / "scripts" / "intake.sh"
_PROJECT = "proj"


def _make_board(base: pathlib.Path) -> tuple[pathlib.Path, OpsContext]:
    """A throwaway kanban root with one registered project and its intake dirs."""
    root = base / "kanban"
    project = root / "projects" / _PROJECT
    (project / "tasks" / "queues").mkdir(parents=True)
    for sub in ("bugs", "priority", "requirements"):
        (project / sub).mkdir()
    (root / "logs").mkdir()
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    return project, OpsContext(kanban_root=root)


def _deposited(project: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for sub in ("bugs", "priority", "requirements") for p in (project / sub).iterdir())


def _stage(path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# staged\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1 and 2. deposit_intake refuses through the shared helper, writes nothing
# ---------------------------------------------------------------------------


def test_traversal_source_path_is_refused_through_the_shared_helper(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, ctx = _make_board(tmp_path)
    staging = tmp_path / "staging"
    _stage(staging / "x.md")
    (staging / "sub").mkdir()
    seen: list[str] = []

    def _spy(name: str) -> str:
        seen.append(name)
        return filename_contract.intake_destination(name)

    monkeypatch.setattr(ops_write, "intake_destination", _spy)

    with pytest.raises(Refused, match="cannot route 'x.md'"):
        ops_write.deposit_intake(ctx, _PROJECT, staging / "sub" / ".." / "x.md")

    assert seen == ["x.md"], "deposit_intake did not route through intake_destination"
    assert _deposited(project) == []


@pytest.mark.parametrize("name", ["BUG-0001..x.md", "BUG-0001\\x.md", "BUG-0001%2Fx.md"])
def test_not_bare_name_is_refused_dual_channel_even_with_a_routing_prefix(
    name: str, tmp_path: pathlib.Path
) -> None:
    project, ctx = _make_board(tmp_path)
    source = _stage(tmp_path / "staging" / name)

    with pytest.raises(Refused) as caught:
        ops_write.deposit_intake(ctx, _PROJECT, source)

    triple = parse_error_contract(str(caught.value))
    assert triple is not None, f"not a dual-channel refusal: {caught.value}"
    assert triple["error"] == filename_contract.INTAKE_FILENAME_NOT_BARE
    assert triple["message"].strip()
    assert "BUG-0001-example.md" in triple["remedy"] and "<" not in triple["remedy"], triple["remedy"]
    assert _deposited(project) == []
    assert source.read_text(encoding="utf-8") == "# staged\n", "the staged source was changed"


# ---------------------------------------------------------------------------
# 3. The source may live anywhere; only the bare name is deposited
# ---------------------------------------------------------------------------


def test_climbing_source_path_deposits_the_bare_name_inside_the_project(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path)
    _stage(tmp_path / "staging" / "BUG-0001-x.md")
    (tmp_path / "staging" / "sub").mkdir()

    dest = ops_write.deposit_intake(ctx, _PROJECT, tmp_path / "staging" / "sub" / ".." / "BUG-0001-x.md")

    assert dest == project / "bugs" / "BUG-0001-x.md"
    assert _deposited(project) == [project / "bugs" / "BUG-0001-x.md"]


# ---------------------------------------------------------------------------
# 4. The real intake.sh refuses the same name and deposits nothing
# ---------------------------------------------------------------------------


def test_intake_sh_refuses_a_not_bare_name_and_deposits_nothing(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path)
    root = ctx.kanban_root
    source = _stage(tmp_path / "staging" / "BUG-0001..x.md")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        "LANG": "C.UTF-8",
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp"),
    }
    for name in ("PYTHONPATH", "VIRTUAL_ENV"):
        if name in os.environ:
            env[name] = os.environ[name]

    result = subprocess.run(
        ["bash", str(_INTAKE_SH), "--project", _PROJECT, str(source)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_TEAM_DIR),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )

    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    triple = parse_error_contract(result.stderr)
    assert triple is not None and triple["error"] == "intake.filename_not_bare", result.stderr
    assert _deposited(project) == []
