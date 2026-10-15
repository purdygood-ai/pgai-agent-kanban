"""
test_git_remote_name_resolver.py
================================
One rule names a project's git remote, in both languages:
``pp_git_remote_name`` / ``validate_git_remote_name``
(``team/scripts/lib/project_paths.sh``) and
``pgai_agent_kanban.lib.git_remote``.

Covered:

  1. Parity, value by value: for every ``[project] git_remote_name`` spelling
     in the table (absent, blank, quoted, valid names, and values that are an
     option, contain a slash or whitespace, are a URL, or end in ``.lock``)
     the bash and Python resolvers give the same answer -- the same name, or
     a refusal whose dual-channel block is byte-identical, naming the key,
     the value and the file, with a remedy free of placeholders.
  2. Absent or blank means ``origin``; so does a project with no config file
     at all, and a legacy ``PROJECT.cfg`` bare key is read.
  3. ``--quiet`` refuses with exit 1 and prints nothing.
  4. ``pp_load_config`` exports the resolver's answer as ``PP_git_remote_name``.
  5. The three Python CM verbs (ship-rc, cancel-rc, unwind-rc) refuse an
     invalid value with ``git_remote_name.invalid`` before running any git
     command.

Every kanban root is a throwaway directory under tmp_path; bash runs get an
explicit, minimal environment (build_scrubbed_env) pointing every root
variable at it.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.cm import cancel, ship, unwind  # noqa: E402
from pgai_agent_kanban.lib import git_remote  # noqa: E402
from pgai_agent_kanban.lib.error_contract import parse_error_contract  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_LIB = _TEAM_DIR / "scripts" / "lib"
_PROJECT = "remote-rule"

# (config line or None for "no line", expected answer or None for "refused")
_CASES = [
    (None, "origin"),
    ("git_remote_name =", "origin"),
    ("git_remote_name =    ", "origin"),
    ("git_remote_name = origin", "origin"),
    ("git_remote_name = upstream", "upstream"),
    ('git_remote_name = "upstream"', "upstream"),
    ("git_remote_name = my-fork", "my-fork"),
    ("git_remote_name = gh.mirror_2", "gh.mirror_2"),
    ("git_remote_name = 2nd", "2nd"),
    ("git_remote_name = -x", None),
    ("git_remote_name = --upload-pack=touch", None),
    ("git_remote_name = up stream", None),
    ("git_remote_name = a/b", None),
    ("git_remote_name = ..x", None),
    ("git_remote_name = x..y", None),
    ("git_remote_name = x.", None),
    ("git_remote_name = x.lock", None),
    ("git_remote_name = git@example.invalid:org/repo.git", None),
    ("git_remote_name = https://example.invalid/repo.git", None),
    ("git_remote_name = _hidden", None),
]


def _root(tmp_path: pathlib.Path, line, *, dev_tree: str = "/srv/dev/remote-rule", legacy: bool = False) -> pathlib.Path:
    root = tmp_path / "kanban"
    project = root / "projects" / _PROJECT
    project.mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    if legacy:
        body = f"dev_tree_path={dev_tree}\n" + (f"{line.replace(' = ', '=')}\n" if line else "")
        (project / "PROJECT.cfg").write_text(body, encoding="utf-8")
    else:
        body = f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {dev_tree}\n"
        if line is not None:
            body += line + "\n"
        (project / "project.cfg").write_text(body, encoding="utf-8")
    return root


def _bash(tmp_path: pathlib.Path, root: pathlib.Path, body: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    script = f'source "{_LIB / "ini_parser.sh"}"\nsource "{_LIB / "project_paths.sh"}"\n{body}\n'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)


def _python(root: pathlib.Path):
    try:
        return git_remote.resolve_git_remote_name(root / "projects" / _PROJECT, _PROJECT), None
    except git_remote.InvalidRemoteName as exc:
        return None, exc


@pytest.mark.parametrize(("line", "expected"), _CASES, ids=[str(c[0]) for c in _CASES])
def test_bash_and_python_resolve_every_value_alike(tmp_path: pathlib.Path, line, expected) -> None:
    root = _root(tmp_path, line)
    bash = _bash(tmp_path, root, f'pp_git_remote_name "{_PROJECT}"; echo "rc=$?"')
    answer, refusal = _python(root)

    if expected is not None:
        assert bash.stdout == f"{expected}\nrc=0\n", bash.stderr
        assert answer == expected and refusal is None
        return

    assert bash.stdout == "rc=1\n", bash.stdout
    assert answer is None and refusal is not None
    block = parse_error_contract(bash.stderr)
    assert block is not None, bash.stderr
    assert block == {"error": refusal.error, "message": refusal.message, "remedy": refusal.remedy}
    assert bash.stderr.endswith(refusal.text), "the bash block is byte-identical to the Python one"
    assert refusal.error == "git_remote_name.invalid"
    cfg = root / "projects" / _PROJECT / "project.cfg"
    assert "[project] git_remote_name" in refusal.message and str(cfg) in refusal.message
    assert f"'{refusal.value}'" in refusal.message
    assert "<" not in refusal.remedy and ">" not in refusal.remedy, refusal.remedy
    assert "git_remote_name = origin" in refusal.remedy
    assert "git -C /srv/dev/remote-rule remote" in refusal.remedy


def test_validators_agree_on_the_rule() -> None:
    values = ["origin", "upstream", "a.b", "A_1-x", "", "-x", "a/b", "a b", "..", "a..b", "a.", "a.lock", ".a", "_a"]
    script = 'for v in "$@"; do if validate_git_remote_name "$v"; then echo 1; else echo 0; fi; done'
    bash = subprocess.run(
        ["bash", "-c", f'source "{_LIB / "ini_parser.sh"}"; source "{_LIB / "project_paths.sh"}"; {script}', "_", *values],
        capture_output=True, text=True, timeout=60,
    )
    assert bash.stdout.split() == ["1" if git_remote.is_valid_remote_name(v) else "0" for v in values]
    assert [git_remote.is_valid_remote_name(v) for v in values[:4]] == [True] * 4


def test_no_config_file_means_origin(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, None)
    (root / "projects" / _PROJECT / "project.cfg").unlink()
    assert _bash(tmp_path, root, f'pp_git_remote_name "{_PROJECT}"').stdout == "origin\n"
    assert _python(root) == ("origin", None)
    assert git_remote.remote_name_from_cfg(None) == git_remote.DEFAULT_REMOTE_NAME == "origin"


def test_legacy_project_cfg_bare_key_is_read(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "git_remote_name = upstream", legacy=True)
    assert _bash(tmp_path, root, f'pp_git_remote_name "{_PROJECT}"').stdout == "upstream\n"
    assert _python(root) == ("upstream", None)


def test_quiet_refusal_prints_nothing(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "git_remote_name = -x")
    quiet = _bash(tmp_path, root, f'pp_git_remote_name --quiet "{_PROJECT}"; echo "rc=$?"')
    assert quiet.stdout == "rc=1\n"
    assert quiet.stderr == ""


@pytest.mark.parametrize(("line", "exported"), [("git_remote_name = upstream", "upstream"), (None, "origin"), ("git_remote_name = -x", "")])
def test_load_config_exports_the_resolved_remote(tmp_path: pathlib.Path, line, exported) -> None:
    root = _root(tmp_path, line)
    loaded = _bash(tmp_path, root, f'pp_load_config "{_PROJECT}" && printf "[%s]\\n" "$PP_git_remote_name"')
    assert loaded.stdout == f"[{exported}]\n", loaded.stderr
    assert "git_remote_name.invalid" not in loaded.stderr, "pp_load_config never refuses over the remote name"


# ---------------------------------------------------------------------------
# The Python CM verbs refuse before any git command
# ---------------------------------------------------------------------------


@pytest.fixture
def _invalid_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A registered, pushing project whose git_remote_name is an option; no git is reachable."""
    root = _root(tmp_path, "git_remote_name = --upload-pack=touch", dev_tree=str(tmp_path / "no-such-dev-tree"))
    (root / "projects" / _PROJECT / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nv1.2.3\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    for name in ("PGAI_PROJECT_NAME", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR", "PGAI_DEV_TREE_PATH", "REPO_ROOT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
    }.items():
        monkeypatch.setenv(name, value)

    def _no_git(*args, **kwargs):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError(f"a git command ran before the remote name was validated: {args!r}")

    monkeypatch.setattr(subprocess, "run", _no_git)
    return root


def _assert_remote_refusal(exc) -> None:
    assert exc.error == "git_remote_name.invalid"
    block = parse_error_contract(exc.text)
    assert block is not None and block["error"] == "git_remote_name.invalid", exc.text
    assert "[project] git_remote_name is '--upload-pack=touch'" in block["message"]


def test_cancel_rc_refuses_an_invalid_remote_name(_invalid_root: pathlib.Path) -> None:
    with pytest.raises(cancel.CancelRcError) as caught:
        cancel.cancel_rc(OpsContext(kanban_root=_invalid_root), _PROJECT, "v1.2.3", yes=True, out=lambda _t: None)
    _assert_remote_refusal(caught.value)


def test_ship_rc_refuses_an_invalid_remote_name(_invalid_root: pathlib.Path) -> None:
    with pytest.raises(ship.ShipRcError) as caught:
        ship.ship_rc(OpsContext(kanban_root=_invalid_root), _PROJECT, "v1.2.3", out=lambda _t: None)
    _assert_remote_refusal(caught.value)


def test_unwind_rc_refuses_an_invalid_remote_name(_invalid_root: pathlib.Path) -> None:
    with pytest.raises(unwind.UnwindRcError) as caught:
        unwind.unwind_rc(
            OpsContext(kanban_root=_invalid_root), _PROJECT, "v1.2.3", dry_run=True, out=lambda _t: None
        )
    _assert_remote_refusal(caught.value)
