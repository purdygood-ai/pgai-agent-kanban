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
  5. The not-bare remedy renames the staged file to a bare name that keeps
     its own routing prefix (BUG-, PRIORITY-, vX.Y.Z-), and running the
     remedy exactly as printed deposits it in the directory that prefix
     routes to; a name with no routing prefix is offered BUG-0001-example.md.
  6. Both intake rename remedies -- the not-bare name and the BUG-* name off
     the ledger filename contract -- re-run the intake.sh installed under the
     kanban root, shell-quoted: on a root whose path holds a space, the real
     intake.sh, run from outside any scripts/ directory, prints a remedy
     whose re-run names <root>/scripts/intake.sh, and that remedy run as
     printed from the same directory deposits the file.
"""

from __future__ import annotations

import os
import pathlib
import shlex
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


def _install_intake_sh(root: pathlib.Path) -> pathlib.Path:
    """Give the throwaway *root* an installed scripts/intake.sh that runs this tree's copy."""
    installed = root / "scripts" / "intake.sh"
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_text(f'#!/usr/bin/env bash\nexec bash {shlex.quote(str(_INTAKE_SH))} "$@"\n', encoding="utf-8")
    installed.chmod(0o755)
    return installed


def _deposited(project: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for sub in ("bugs", "priority", "requirements") for p in (project / sub).iterdir())


def _stage(path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# staged\n", encoding="utf-8")
    return path


def _intake_env(tmp_path: pathlib.Path, root: pathlib.Path) -> dict[str, str]:
    """An explicit environment for intake.sh pointed at the throwaway *root*."""
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
    return env


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
    assert _deposited(project) == [project / "bugs" / ".bug-claims.log", project / "bugs" / "BUG-0001-x.md"]


# ---------------------------------------------------------------------------
# 4. The real intake.sh refuses the same name and deposits nothing
# ---------------------------------------------------------------------------


def test_intake_sh_refuses_a_not_bare_name_and_deposits_nothing(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path)
    root = ctx.kanban_root
    source = _stage(tmp_path / "staging" / "BUG-0001..x.md")
    env = _intake_env(tmp_path, root)

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


# ---------------------------------------------------------------------------
# 5. The not-bare remedy keeps the staged name's own routing prefix
# ---------------------------------------------------------------------------


def _not_bare_remedy(project: pathlib.Path, ctx: OpsContext, source: pathlib.Path) -> str:
    """The remedy deposit_intake prints for *source*, after checking it refused."""
    with pytest.raises(Refused) as caught:
        ops_write.deposit_intake(ctx, _PROJECT, source)
    triple = parse_error_contract(str(caught.value))
    assert triple is not None, f"not a dual-channel refusal: {caught.value}"
    assert triple["error"] == filename_contract.INTAKE_FILENAME_NOT_BARE
    assert "<" not in triple["remedy"], triple["remedy"]
    assert _deposited(project) == []
    return triple["remedy"]


@pytest.mark.parametrize(
    "name, prefix, subdir",
    [
        pytest.param("PRIORITY-0050-a%2Fb.md", "PRIORITY-", "priority", id="priority"),
        pytest.param("v2.42.1..x.md", "v", "requirements", id="requirements"),
        pytest.param("BUG-0007\\x.md", "BUG-", "bugs", id="bug"),
    ],
)
def test_not_bare_remedy_keeps_the_routing_prefix_and_deposits_where_it_routes(
    name: str, prefix: str, subdir: str, tmp_path: pathlib.Path
) -> None:
    project, ctx = _make_board(tmp_path)
    source = _stage(tmp_path / "staging" / name)

    remedy = _not_bare_remedy(project, ctx, source)

    # The runnable part is the shell command after the prose lead-in.
    command = remedy[remedy.index("mv "):]
    words = shlex.split(command)
    assert words[:2] == ["mv", str(source)], remedy
    target = pathlib.Path(words[2])
    installed = _install_intake_sh(ctx.kanban_root)
    assert words[3:] == ["&&", str(installed), "--project", _PROJECT, str(target)], remedy
    assert target.parent == source.parent, remedy
    assert target.name.startswith(prefix), f"suggested {target.name!r} lost the {prefix!r} prefix"
    assert filename_contract.intake_destination(target.name) == subdir

    # Run the remedy exactly as printed, from a directory with no intake.sh in it.
    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        env=_intake_env(tmp_path, ctx.kanban_root),
        cwd=str(tmp_path),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )

    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    landed = [p for p in _deposited(project) if p.suffix == ".md"]
    assert len(landed) == 1, landed
    assert landed[0].parent == project / subdir, f"remedy deposited into {landed[0].parent.name}/, not {subdir}/"
    assert landed[0].name.startswith(prefix), landed[0].name
    assert landed[0].read_text(encoding="utf-8") == "# staged\n"


def test_not_bare_name_without_a_routing_prefix_is_offered_a_bug_example(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path)
    source = _stage(tmp_path / "staging" / "notes%2Fdraft.md")

    remedy = _not_bare_remedy(project, ctx, source)

    expected = source.with_name("BUG-0001-example.md")
    assert shlex.join(["mv", str(source), str(expected)]) in remedy, remedy


# ---------------------------------------------------------------------------
# 6. Both rename remedies re-run the installed intake.sh on a spaced root
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, error, subdir",
    [
        pytest.param("BUG-0001..x.md", filename_contract.INTAKE_FILENAME_NOT_BARE, "bugs", id="not-bare"),
        pytest.param("BUG-0001-Upper.Case.md", ops_write.INTAKE_BUG_NAME_MALFORMED, "bugs", id="bug-name-malformed"),
    ],
)
def test_rename_remedy_reruns_the_installed_intake_sh_on_a_spaced_root(
    name: str, error: str, subdir: str, tmp_path: pathlib.Path
) -> None:
    project, ctx = _make_board(tmp_path / "kanban parent")
    root = ctx.kanban_root
    assert " " in str(root)
    installed = _install_intake_sh(root)
    source = _stage(tmp_path / "staging" / name)
    workdir = tmp_path / "elsewhere"
    workdir.mkdir()
    env = _intake_env(tmp_path, root)

    refused = subprocess.run(
        [str(installed), "--project", _PROJECT, str(source)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(workdir),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )

    assert refused.returncode != 0, (refused.stdout, refused.stderr)
    triple = parse_error_contract(refused.stderr)
    assert triple is not None and triple["error"] == error, refused.stderr
    remedy = triple["remedy"]
    assert "<" not in remedy, remedy
    rerun = shlex.split(remedy[remedy.index("&&") + len("&&"):])
    assert rerun[0] == str(root / "scripts" / "intake.sh"), remedy
    assert rerun[1:3] == ["--project", _PROJECT], remedy
    assert _deposited(project) == []

    # The whole remedy, run as printed from the same directory, deposits the file.
    command = remedy[remedy.index("mv "):]
    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(workdir),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )

    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    landed = [p for p in _deposited(project) if p.suffix == ".md"]
    assert [p.parent for p in landed] == [project / subdir], landed
