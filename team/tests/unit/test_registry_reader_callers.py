"""
test_registry_reader_callers.py
===============================
The bash scripts that read the projects.cfg registry do it through
``scripts/lib/projects.sh`` rather than their own greps and awk:

  * ``projects_cfg_list --registration-order`` lists names in the order
    projects.cfg holds them (INI and colon-legacy), while the plain call
    keeps its priority order;
  * ``upgrade.sh``'s ``_upg_resolve_branch_prefix`` takes the prefix of the
    first ``[project:NAME]`` section in file order -- not the highest
    priority -- and reads a colon-legacy registry as unprefixed;
  * ``export-project.sh`` refuses an unregistered name, listing the
    registered ones in file order, and records a project's registration
    text -- its whole INI section, or its colon-legacy line -- verbatim,
    through ``projects_cfg_registration``;
  * ``install.sh`` and ``upgrade.sh`` seed ``projects/<name>/logs/debug/archive``
    and ``logs/training`` for every name ``projects_cfg_list`` reads, from an
    INI or a colon-legacy registry alike;
  * ``import-project.sh --register`` skips a name the registry already
    holds, a colon-legacy entry included, leaving projects.cfg unchanged.

Every script runs as a subprocess against a sandbox kanban root under
``tmp_path`` with an environment built by ``build_scrubbed_env``; nothing
reads or writes the live install.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tarfile

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_UPGRADE_SH = _TEAM_DIR / "scripts" / "upgrade.sh"
_EXPORT_SH = _TEAM_DIR / "scripts" / "export-project.sh"
_IMPORT_SH = _TEAM_DIR / "scripts" / "import-project.sh"
_INSTALL_SH = _TEAM_DIR.parent / "install.sh"

# upgrade.sh's resolver definition, from its opening line to the first
# lone closing brace. Liberal about spacing around the parentheses.
_RESOLVER_RE = re.compile(
    r"^_upg_resolve_branch_prefix\s*\(\s*\)\s*\{.*?^\}\s*$", re.M | re.S
)

# Priority order and file order disagree: zeta is first in the file,
# alpha has the best priority.
_INI_REGISTRY = (
    "# registry\n"
    "[project:zeta]\n"
    "priority = 5\n"
    "\n"
    "[project:alpha]\n"
    "priority = 1\n"
    "[project:mid]\n"
    "priority = 3\n"
)
_COLON_REGISTRY = "# registry\nzeta:5\nalpha:1:#378ADD\nmid:3\n"


def _env(tmp_path: pathlib.Path, root: pathlib.Path) -> dict:
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
        },
    )


def _root(tmp_path: pathlib.Path, name: str, registry: str) -> pathlib.Path:
    """A sandbox kanban root holding *registry* as projects.cfg and a
    project directory (with project.cfg) for every name in it."""
    root = tmp_path / name
    for project, prefix in (("zeta", "z_"), ("alpha", "a_"), ("mid", "")):
        proj = root / "projects" / project
        proj.mkdir(parents=True)
        lines = ["[project]", f"project_name = {project}"]
        if prefix:
            lines.append(f"branch_prefix = {prefix}")
        (proj / "project.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "projects.cfg").write_text(registry, encoding="utf-8")
    return root


def _say(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _bash(tmp_path: pathlib.Path, root: pathlib.Path, script: str, **extra: str) -> subprocess.CompletedProcess:
    env = _env(tmp_path, root)
    env.update(extra)
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env,
        cwd=str(cwd), timeout=60, stdin=subprocess.DEVNULL, check=False,
    )


def _run(tmp_path: pathlib.Path, root: pathlib.Path, script: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        [str(script), *args], capture_output=True, text=True, env=_env(tmp_path, root),
        cwd=str(cwd), timeout=120, stdin=subprocess.DEVNULL, check=False,
    )


_LIST_DRIVER = r"""
set -euo pipefail
source "$LIB_DIR/project_paths.sh"
source "$LIB_DIR/projects.sh"
echo "priority: $(projects_cfg_list | paste -sd' ')"
echo "registration: $(projects_cfg_list --registration-order | paste -sd' ')"
"""


def test_registration_order_lists_ini_names_in_file_order(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "kanban", _INI_REGISTRY)
    result = _bash(tmp_path, root, _LIST_DRIVER, LIB_DIR=str(_LIB_DIR))
    assert result.returncode == 0, _say(result)
    assert "priority: alpha mid zeta" in result.stdout, _say(result)
    assert "registration: zeta alpha mid" in result.stdout, _say(result)


def test_registration_order_lists_colon_legacy_names_in_file_order(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "kanban", _COLON_REGISTRY)
    result = _bash(tmp_path, root, _LIST_DRIVER, LIB_DIR=str(_LIB_DIR))
    assert result.returncode == 0, _say(result)
    assert "priority: alpha mid zeta" in result.stdout, _say(result)
    assert "registration: zeta alpha mid" in result.stdout, _say(result)


_RESOLVER_DRIVER = r"""
set -euo pipefail
source "$LIB_DIR/project_paths.sh"
source "$LIB_DIR/projects.sh"
eval "$RESOLVER_SOURCE"
printf 'prefix=[%s]\n' "$(_upg_resolve_branch_prefix)"
"""


def _resolver_source() -> str:
    match = _RESOLVER_RE.search(_UPGRADE_SH.read_text(encoding="utf-8"))
    assert match is not None, f"_upg_resolve_branch_prefix() not found in {_UPGRADE_SH}"
    return match.group(0)


def test_upgrade_prefix_comes_from_the_first_project_in_file_order(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "kanban", _INI_REGISTRY)
    result = _bash(
        tmp_path, root, _RESOLVER_DRIVER,
        LIB_DIR=str(_LIB_DIR), RESOLVER_SOURCE=_resolver_source(),
    )
    assert result.returncode == 0, _say(result)
    assert "prefix=[z_]" in result.stdout, _say(result)


def test_upgrade_prefix_reads_a_colon_legacy_registry_as_unprefixed(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "kanban", _COLON_REGISTRY)
    result = _bash(
        tmp_path, root, _RESOLVER_DRIVER,
        LIB_DIR=str(_LIB_DIR), RESOLVER_SOURCE=_resolver_source(),
    )
    assert result.returncode == 0, _say(result)
    assert "prefix=[]" in result.stdout, _say(result)


def test_export_refuses_an_unregistered_name_listing_projects_in_file_order(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "kanban", _INI_REGISTRY)
    result = _run(tmp_path, root, _EXPORT_SH, "--project", "gamma", "--out", str(tmp_path / "x.tar.gz"))
    assert result.returncode == 2, _say(result)
    assert "project 'gamma' is not registered in projects.cfg" in result.stderr, _say(result)
    assert "  Registered projects:\n    zeta\n    alpha\n    mid\n" in result.stderr, _say(result)


def _manifest(archive: pathlib.Path) -> str:
    with tarfile.open(archive, "r:gz") as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith("MANIFEST"))
        handle = tar.extractfile(member)
        assert handle is not None, f"MANIFEST in {archive} is not a regular file"
        return handle.read().decode("utf-8")


def test_export_accepts_a_colon_legacy_entry(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "kanban", _COLON_REGISTRY)
    out = tmp_path / "alpha.tar.gz"
    result = _run(tmp_path, root, _EXPORT_SH, "--project", "alpha", "--out", str(out))
    assert result.returncode == 0, _say(result)
    assert "## registration\nalpha:1:#378ADD\n" in _manifest(out), _say(result)


def test_import_register_skips_a_name_a_colon_legacy_registry_holds(tmp_path: pathlib.Path) -> None:
    source = _root(tmp_path, "source", _INI_REGISTRY)
    out = tmp_path / "alpha.tar.gz"
    exported = _run(tmp_path, source, _EXPORT_SH, "--project", "alpha", "--out", str(out))
    assert exported.returncode == 0, _say(exported)

    target = tmp_path / "target"
    (target / "projects").mkdir(parents=True)
    (target / "projects.cfg").write_text("alpha:1\n", encoding="utf-8")
    result = _run(tmp_path, target, _IMPORT_SH, "--archive", str(out), "--register")

    assert result.returncode == 0, _say(result)
    assert "project 'alpha' is already registered" in result.stderr, _say(result)
    assert (target / "projects.cfg").read_text(encoding="utf-8") == "alpha:1\n"


# An INI registry whose middle section carries comments, blank lines and
# fields; the recorded registration is that section, untouched, ending at
# the last non-blank line before the next header.
_INI_WITH_DETAIL = (
    "# registry\n"
    "[project:zeta]\n"
    "priority = 5\n"
    "\n"
    "[project:alpha]\n"
    "# the demo project\n"
    "priority=1\n"
    "description=Alpha project\n"
    "dashboard_color=#378ADD\n"
    "\n"
    "[project:mid]\n"
    "priority = 3\n"
)


def test_export_records_an_ini_section_verbatim(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "kanban", _INI_WITH_DETAIL)
    out = tmp_path / "alpha.tar.gz"
    result = _run(tmp_path, root, _EXPORT_SH, "--project", "alpha", "--out", str(out))
    assert result.returncode == 0, _say(result)
    expected = (
        "## registration\n"
        "[project:alpha]\n"
        "# the demo project\n"
        "priority=1\n"
        "description=Alpha project\n"
        "dashboard_color=#378ADD\n"
    )
    assert _manifest(out).endswith(expected), _manifest(out)


def test_export_records_an_indented_colon_legacy_line_verbatim(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "kanban", "zeta:5\n  alpha : 1\nmid:3\n")
    out = tmp_path / "alpha.tar.gz"
    result = _run(tmp_path, root, _EXPORT_SH, "--project", "alpha", "--out", str(out))
    assert result.returncode == 0, _say(result)
    assert "## registration\n  alpha : 1\n" in _manifest(out), _say(result)


_REGISTRATION_DRIVER = r"""
set -uo pipefail
source "$LIB_DIR/project_paths.sh"
source "$LIB_DIR/projects.sh"
projects_cfg_registration "$NAME"
echo "rc=$?"
"""


@pytest.mark.parametrize(
    ("registry", "name", "expected"),
    [
        pytest.param(
            "[project:alpha-two]\npriority=1\n[project:alpha]\npriority=2\n",
            "alpha", "[project:alpha]\npriority=2\nrc=0\n", id="ini-name-is-not-a-prefix-match",
        ),
        pytest.param(
            "[project:alpha]\npriority=1\n[project:zeta]\npriority=2\n[project:alpha]\npriority=9\n",
            "alpha", "[project:alpha]\npriority=1\nrc=0\n", id="ini-first-section-of-a-duplicate",
        ),
        pytest.param(
            "[project:alpha]  \npriority=1\n",
            "alpha", "[project:alpha]  \npriority=1\nrc=0\n", id="ini-header-with-trailing-spaces",
        ),
        pytest.param(
            "# alpha:0 is a comment\nalpha-two:1\nalpha:2:#378ADD\n",
            "alpha", "alpha:2:#378ADD\nrc=0\n", id="colon-legacy-whole-name-field",
        ),
        pytest.param("[project:zeta]\npriority=1\n", "alpha", "rc=1\n", id="ini-unregistered"),
        pytest.param("zeta:1\n", "alpha", "rc=1\n", id="colon-legacy-unregistered"),
    ],
)
def test_registration_reader_prints_the_entry_unaltered(
    tmp_path: pathlib.Path, registry: str, name: str, expected: str
) -> None:
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "projects.cfg").write_text(registry, encoding="utf-8")
    result = _bash(tmp_path, root, _REGISTRATION_DRIVER, LIB_DIR=str(_LIB_DIR), NAME=name)
    assert result.stdout == expected, _say(result)


# ---------------------------------------------------------------------------
# install.sh and upgrade.sh: per-project log-directory seeding
# ---------------------------------------------------------------------------

_SEED_REGISTRIES = [
    pytest.param("[project:p1]\npriority=1\n", id="ini"),
    pytest.param("p1:1\n", id="colon-legacy"),
]


def _seed_root(tmp_path: pathlib.Path, registry: str) -> pathlib.Path:
    """A sandbox root registering p1, with a project directory but no log subtrees."""
    root = tmp_path / "kanban"
    (root / "projects" / "p1").mkdir(parents=True)
    (root / "projects.cfg").write_text(registry, encoding="utf-8")
    (tmp_path / "home").mkdir(exist_ok=True)
    return root


def _assert_seeded(root: pathlib.Path, result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, _say(result)
    for sub in ("logs/debug/archive", "logs/training"):
        assert (root / "projects" / "p1" / sub).is_dir(), f"projects/p1/{sub} not created\n{_say(result)}"


@pytest.mark.skipif(not _INSTALL_SH.exists(), reason=f"install.sh not found at {_INSTALL_SH}")
@pytest.mark.parametrize("registry", _SEED_REGISTRIES)
def test_install_seeds_project_log_dirs_from_the_registry_reader(tmp_path: pathlib.Path, registry: str) -> None:
    root = _seed_root(tmp_path, registry)
    result = _run(
        tmp_path, root, _INSTALL_SH,
        "--force", "--wake-tier", "none", "--stamp-version", "v0.0.0-sandbox-test",
    )
    _assert_seeded(root, result)
    assert (
        "Ensured per-project log subdirs: projects/p1/logs/debug/archive/ and logs/training/"
        in result.stdout
    ), _say(result)


@pytest.mark.parametrize("registry", _SEED_REGISTRIES)
def test_upgrade_seeds_project_log_dirs_from_the_registry_reader(tmp_path: pathlib.Path, registry: str) -> None:
    root = _seed_root(tmp_path, registry)
    result = _run(
        tmp_path, root, _UPGRADE_SH,
        "--phase2", "--phase2-protocol", "1", "--kanban-root", str(root),
        "--force", "--stamp-version", "v0.0.1-sandbox-test",
    )
    _assert_seeded(root, result)
