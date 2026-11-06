"""
test_import_registration_block.py
=================================
``import-project.sh`` reads the archive MANIFEST's ``## registration``
block and treats it the same way with or without ``--register``: the
block's ``[project:NAME]`` header is renamed to the final project name
when NAME passes the shared project-name rule (``project_header`` in
``scripts/lib/projects_cfg.sh``), a block with no such header is never
used, and a section whose header fails the rule is left out of the block
that is offered or appended.

Covered, against the real script run as a subprocess on a hand-built
archive whose top-level directory is ``alpha/``, imported with
``--name beta``:

  * without ``--register``, a ``[project:My_App]`` header (a name the rule
    rejects) exits 0, prints no "add the following section" block and never
    shows ``[project:My_App]``; a warning says the block has no header the
    registry accepts, and projects.cfg is untouched;
  * without ``--register``, a ``[project:alpha]`` header is offered for
    pasting as ``[project:beta]``;
  * with ``--register``, the ``[project:My_App]`` block is refused with
    exit 5 and the "does not contain a valid [project:NAME] header" error,
    and projects.cfg gains no section;
  * with ``--register``, the ``[project:alpha]`` block is appended to
    projects.cfg as ``[project:beta]``;
  * for a mixed block -- a ``[project:My_App]`` section with ``key = v``
    beside an accepted ``[project:other]`` section, in either order -- the
    print path exits 0 and offers only ``[project:beta]`` / ``priority = 1``,
    ``--register`` exits 0 and appends only that section, and both warn that
    ``[project:My_App]`` was left out;
  * for a block holding more than the one section to register, against a
    projects.cfg that ends in ``[project:gamma]`` / ``priority = 7`` -- a
    stray ``priority = 99`` key line before the first header, or a second
    accepted ``[project:gamma]`` section with ``priority = 99`` after it --
    ``--register`` exits 0, appends only ``[project:beta]`` /
    ``status = active`` and leaves gamma's priority reading 7 through
    ``_projects_cfg_ini_value``; the print path exits 0, leaves projects.cfg
    unchanged and offers only that section; and both paths name the
    left-out content (the stray line, or ``[project:gamma]``) on stderr.

  * a legacy ``alpha:3:#378ADD`` registration line is offered and appended
    as ``beta:3:#378ADD`` when projects.cfg is itself legacy, and converted
    to ``[project:beta]`` / ``priority=3`` / ``dashboard_color=#378ADD``
    when it is INI;
  * a ``#`` comment above the first header draws no warning;
  * a ``[release]`` header inside the registered section is removed with a
    warning naming it, and the key after it is registered as beta's.

Every run uses a sandbox kanban root under ``tmp_path`` and an environment
built by ``build_scrubbed_env``; nothing reads or writes the live install.
"""

from __future__ import annotations

import io
import pathlib
import subprocess
import sys
import tarfile

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_IMPORT_SH = _TEAM_DIR / "scripts" / "import-project.sh"

_NOTICE = "add the following section to:"
_REJECTED_HEADER = "[project:My_App]"
_ACCEPTED_HEADER = "[project:alpha]"
_REGISTRY_BEFORE = "# sandbox registry\n"
_REJECTED_KEY = "key = v"
_LEFT_OUT_WARNING = "sections the registry does not accept"

# A registration block holding a section the registry rejects and one it
# accepts, in both orders; the rejected section carries a key of its own.
_MIXED_BLOCKS = {
    "rejected_first": "[project:My_App]\nkey = v\n[project:other]\npriority = 1\n",
    "accepted_first": "[project:other]\npriority = 1\n[project:My_App]\nkey = v\n",
}

# A registry whose last section belongs to another project, gamma: a line
# appended after it with no header of its own would be read as gamma's.
_NEIGHBOUR_REGISTRY = "# sandbox registry\n[project:gamma]\npriority = 7\n"
_PROJECTS_CFG_SH = _TEAM_DIR / "scripts" / "lib" / "projects_cfg.sh"

# Registration blocks holding more than the one section to register, each
# mapped to the content that must be left out and named in the warning: a
# key line before the first header, and a second accepted section that
# names an already-registered project.
_EXTRA_CONTENT_BLOCKS = {
    "stray_key_before_header": (
        "priority = 99\n[project:other]\nstatus = active\n",
        "priority = 99",
    ),
    "second_accepted_section": (
        "[project:other]\nstatus = active\n[project:gamma]\npriority = 99\n",
        "[project:gamma]",
    ),
}
_ONE_SECTION = ["[project:beta]", "status = active"]


def _say(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _root(tmp_path: pathlib.Path, registry: str = _REGISTRY_BEFORE) -> pathlib.Path:
    """A sandbox kanban root: projects/, a VERSION and a projects.cfg holding *registry*.

    The default *registry* has no sections.
    """
    root = tmp_path / "kanban"
    (root / "projects").mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "VERSION").write_text("v9.9.9\n", encoding="utf-8")
    (root / "projects.cfg").write_text(registry, encoding="utf-8")
    return root


def _archive(tmp_path: pathlib.Path, header: str = "", *, registration: str | None = None) -> pathlib.Path:
    """An export-shaped archive of ``alpha/``.

    Its MANIFEST registration block is *registration* when given, else
    *header* followed by ``priority = 1``.
    """
    if registration is None:
        registration = f"{header}\npriority = 1\n"
    manifest = "kanban_version: v9.9.9\n" "project_name: alpha\n" "\n" "## registration\n" f"{registration}"
    files = {
        "alpha/project.cfg": b"[project]\nworkflow_type = document\n",
        "alpha/MANIFEST": manifest.encode("utf-8"),
    }
    out = tmp_path / "exports" / "alpha.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        info = tarfile.TarInfo("alpha")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tar.addfile(info)
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return out


def _import(
    tmp_path: pathlib.Path, root: pathlib.Path, archive: pathlib.Path, *args: str
) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME"):
        env.pop(name, None)
    return subprocess.run(
        [str(_IMPORT_SH), "--archive", str(archive), "--name", "beta", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _registry_value(tmp_path: pathlib.Path, registry: pathlib.Path, name: str, key: str) -> str:
    """Read *key* of project *name* from *registry* through the shared bash reader."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(home_dir=home)
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1" && _projects_cfg_ini_value "$2" "$3" "$4"',
            "_",
            str(_PROJECTS_CFG_SH),
            str(registry),
            name,
            key,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert result.returncode == 0, f"registry read of {name}.{key} failed\n" + _say(result)
    return result.stdout.strip()


def _offered_block(stdout: str) -> list[str]:
    """The non-blank lines printed between the registration notice and its re-run hint."""
    lines = stdout.splitlines()
    start = next((i for i, line in enumerate(lines) if _NOTICE in line), None)
    if start is None:
        return []
    block = []
    for line in lines[start + 1 :]:
        if "re-run with --register" in line:
            break
        if line.strip():
            block.append(line.strip())
    return block


def test_print_path_offers_no_block_for_a_header_the_registry_rejects(tmp_path):
    root = _root(tmp_path)

    result = _import(tmp_path, root, _archive(tmp_path, _REJECTED_HEADER))

    assert result.returncode == 0, _say(result)
    assert (root / "projects" / "beta" / "project.cfg").is_file(), _say(result)
    assert (
        _NOTICE not in result.stdout + result.stderr
    ), "a block was offered for pasting though its header fails the project-name rule\n" + _say(result)
    assert _REJECTED_HEADER not in result.stdout + result.stderr, _say(result)
    assert (
        "registration block has no [project:NAME] header or NAME:PRIORITY line the registry accepts" in result.stderr
    ), _say(result)
    assert (root / "projects.cfg").read_text(encoding="utf-8") == _REGISTRY_BEFORE, _say(result)


def test_print_path_offers_the_block_renamed_to_the_final_name(tmp_path):
    root = _root(tmp_path)

    result = _import(tmp_path, root, _archive(tmp_path, _ACCEPTED_HEADER))

    assert result.returncode == 0, _say(result)
    assert _offered_block(result.stdout) == ["[project:beta]", "priority = 1"], _say(result)
    assert (root / "projects.cfg").read_text(encoding="utf-8") == _REGISTRY_BEFORE, _say(result)


def test_register_refuses_a_header_the_registry_rejects_and_appends_nothing(tmp_path):
    root = _root(tmp_path)

    result = _import(tmp_path, root, _archive(tmp_path, _REJECTED_HEADER), "--register")

    assert result.returncode == 5, _say(result)
    assert "does not contain a valid [project:NAME] header" in result.stderr, _say(result)
    assert (root / "projects.cfg").read_text(
        encoding="utf-8"
    ) == _REGISTRY_BEFORE, "a refused registration changed projects.cfg\n" + _say(result)


def test_register_appends_the_block_renamed_to_the_final_name(tmp_path):
    root = _root(tmp_path)

    result = _import(tmp_path, root, _archive(tmp_path, _ACCEPTED_HEADER), "--register")

    assert result.returncode == 0, _say(result)
    registry = (root / "projects.cfg").read_text(encoding="utf-8")
    assert registry.startswith(_REGISTRY_BEFORE), _say(result)
    assert registry[len(_REGISTRY_BEFORE) :].split() == [
        "[project:beta]",
        "priority",
        "=",
        "1",
    ], f"projects.cfg after --register:\n{registry}\n" + _say(result)


@pytest.mark.parametrize("order", sorted(_MIXED_BLOCKS))
def test_print_path_offers_only_the_accepted_section_of_a_mixed_block(tmp_path, order):
    root = _root(tmp_path)

    result = _import(tmp_path, root, _archive(tmp_path, registration=_MIXED_BLOCKS[order]))

    assert result.returncode == 0, _say(result)
    assert (root / "projects" / "beta" / "project.cfg").is_file(), _say(result)
    offered = _offered_block(result.stdout)
    assert (
        _REJECTED_HEADER not in offered and _REJECTED_KEY not in offered
    ), "the offered block keeps a section whose header fails the project-name rule\n" + _say(result)
    assert offered == ["[project:beta]", "priority = 1"], _say(result)
    assert _LEFT_OUT_WARNING in result.stderr and _REJECTED_HEADER in result.stderr, _say(result)
    assert (root / "projects.cfg").read_text(encoding="utf-8") == _REGISTRY_BEFORE, _say(result)


@pytest.mark.parametrize("order", sorted(_MIXED_BLOCKS))
def test_register_appends_only_the_accepted_section_of_a_mixed_block(tmp_path, order):
    root = _root(tmp_path)

    result = _import(tmp_path, root, _archive(tmp_path, registration=_MIXED_BLOCKS[order]), "--register")

    assert result.returncode == 0, _say(result)
    registry = (root / "projects.cfg").read_text(encoding="utf-8")
    appended = [line.strip() for line in registry.splitlines()]
    assert (
        _REJECTED_HEADER not in appended and _REJECTED_KEY not in appended
    ), f"projects.cfg gained a section whose header fails the project-name rule:\n{registry}\n" + _say(result)
    assert registry.startswith(_REGISTRY_BEFORE), _say(result)
    assert registry[len(_REGISTRY_BEFORE) :].split() == [
        "[project:beta]",
        "priority",
        "=",
        "1",
    ], f"projects.cfg after --register:\n{registry}\n" + _say(result)
    assert _LEFT_OUT_WARNING in result.stderr and _REJECTED_HEADER in result.stderr, _say(result)


@pytest.mark.parametrize("shape", sorted(_EXTRA_CONTENT_BLOCKS))
def test_register_appends_only_the_registered_section_and_leaves_the_neighbour_alone(tmp_path, shape):
    block, left_out = _EXTRA_CONTENT_BLOCKS[shape]
    root = _root(tmp_path, _NEIGHBOUR_REGISTRY)
    registry_path = root / "projects.cfg"

    result = _import(tmp_path, root, _archive(tmp_path, registration=block), "--register")

    assert result.returncode == 0, _say(result)
    registry = registry_path.read_text(encoding="utf-8")
    assert registry.startswith(_NEIGHBOUR_REGISTRY), _say(result)
    appended = [line.strip() for line in registry[len(_NEIGHBOUR_REGISTRY) :].splitlines() if line.strip()]
    assert (
        appended == _ONE_SECTION
    ), f"projects.cfg gained more than the section registered for beta:\n{registry}\n" + _say(result)
    assert (
        _registry_value(tmp_path, registry_path, "gamma", "priority") == "7"
    ), f"registering beta changed another project's key:\n{registry}\n" + _say(result)
    assert _registry_value(tmp_path, registry_path, "beta", "status") == "active", _say(result)
    assert (
        left_out in result.stderr and "left out" in result.stderr
    ), f"no warning names the left-out {left_out!r}\n" + _say(result)


@pytest.mark.parametrize("shape", sorted(_EXTRA_CONTENT_BLOCKS))
def test_print_path_offers_only_the_registered_section_and_names_what_it_left_out(tmp_path, shape):
    block, left_out = _EXTRA_CONTENT_BLOCKS[shape]
    root = _root(tmp_path, _NEIGHBOUR_REGISTRY)

    result = _import(tmp_path, root, _archive(tmp_path, registration=block))

    assert result.returncode == 0, _say(result)
    assert (root / "projects" / "beta" / "project.cfg").is_file(), _say(result)
    assert (
        _offered_block(result.stdout) == _ONE_SECTION
    ), "the block offered for pasting holds more than the section registered for beta\n" + _say(result)
    assert (root / "projects.cfg").read_text(encoding="utf-8") == _NEIGHBOUR_REGISTRY, _say(result)
    assert (
        left_out in result.stderr and "left out" in result.stderr
    ), f"no warning names the left-out {left_out!r}\n" + _say(result)


_LEGACY_BLOCK = "alpha:3:#378ADD\n"
_LEGACY_REGISTRY = "# legacy registry\ngamma:7\n"
_LINE_NOTICE = "add the following line to:"


def _offered_line(stdout: str) -> list[str]:
    lines = stdout.splitlines()
    start = next((i for i, line in enumerate(lines) if _LINE_NOTICE in line), None)
    if start is None:
        return []
    return [line.strip() for line in lines[start + 1 :] if line.strip() and "re-run with --register" not in line][:1]


def test_a_legacy_line_is_offered_as_a_legacy_line_to_a_legacy_registry(tmp_path):
    root = _root(tmp_path, _LEGACY_REGISTRY)

    result = _import(tmp_path, root, _archive(tmp_path, registration=_LEGACY_BLOCK))

    assert result.returncode == 0, _say(result)
    assert _offered_line(result.stdout) == ["beta:3:#378ADD"], _say(result)
    assert _NOTICE not in result.stdout, _say(result)
    assert (root / "projects.cfg").read_text(encoding="utf-8") == _LEGACY_REGISTRY, _say(result)


def test_register_appends_a_legacy_line_to_a_legacy_registry(tmp_path):
    root = _root(tmp_path, _LEGACY_REGISTRY)

    result = _import(tmp_path, root, _archive(tmp_path, registration=_LEGACY_BLOCK), "--register")

    assert result.returncode == 0, _say(result)
    registry = (root / "projects.cfg").read_text(encoding="utf-8")
    assert registry.startswith(_LEGACY_REGISTRY), _say(result)
    assert registry[len(_LEGACY_REGISTRY) :].split() == ["beta:3:#378ADD"], registry


def test_a_legacy_line_is_converted_for_an_ini_registry(tmp_path):
    root = _root(tmp_path, _NEIGHBOUR_REGISTRY)

    offered = _import(tmp_path, root, _archive(tmp_path, registration=_LEGACY_BLOCK))
    registered = _import(
        tmp_path / "second",
        _root(tmp_path / "second", _NEIGHBOUR_REGISTRY),
        _archive(tmp_path / "second", registration=_LEGACY_BLOCK),
        "--register",
    )

    assert offered.returncode == 0, _say(offered)
    assert _offered_block(offered.stdout) == ["[project:beta]", "priority=3", "dashboard_color=#378ADD"], _say(offered)
    assert registered.returncode == 0, _say(registered)
    registry_path = tmp_path / "second" / "kanban" / "projects.cfg"
    assert _registry_value(tmp_path, registry_path, "beta", "priority") == "3", _say(registered)
    assert _registry_value(tmp_path, registry_path, "beta", "dashboard_color") == "#378ADD", _say(registered)
    assert _registry_value(tmp_path, registry_path, "gamma", "priority") == "7", _say(registered)


def test_a_comment_above_the_first_header_draws_no_warning(tmp_path):
    root = _root(tmp_path)

    result = _import(
        tmp_path, root, _archive(tmp_path, registration="# exported by alpha\n[project:alpha]\npriority = 1\n")
    )

    assert result.returncode == 0, _say(result)
    assert _offered_block(result.stdout) == ["[project:beta]", "priority = 1"], _say(result)
    assert "MANIFEST registration block" not in result.stderr, _say(result)


@pytest.mark.parametrize("register", [False, True], ids=["print", "register"])
def test_a_foreign_header_inside_the_section_is_removed_and_named(tmp_path, register):
    root = _root(tmp_path, _NEIGHBOUR_REGISTRY)
    block = "[project:alpha]\npriority = 1\n[release]\nstatus = active\n"

    result = _import(tmp_path, root, _archive(tmp_path, registration=block), *(["--register"] if register else []))

    assert result.returncode == 0, _say(result)
    assert "[release]" in result.stderr and "is removed" in result.stderr, _say(result)
    expected = ["[project:beta]", "priority = 1", "status = active"]
    if register:
        registry_path = root / "projects.cfg"
        appended = [
            line.strip()
            for line in registry_path.read_text(encoding="utf-8")[len(_NEIGHBOUR_REGISTRY) :].splitlines()
            if line.strip()
        ]
        assert appended == expected, _say(result)
        assert _registry_value(tmp_path, registry_path, "beta", "status") == "active", _say(result)
    else:
        assert _offered_block(result.stdout) == expected, _say(result)
