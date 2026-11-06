"""
test_overwatch_config_bash.py
=============================
The OVERWATCH checks read the project registry and branch_prefix through
team/scripts/lib/overwatch_config.sh, never by parsing projects.cfg or
project.cfg themselves:

  overwatch_registered_projects      projects_cfg_list (lib/projects.sh)
  overwatch_branch_prefix <project>  pp_branch_prefix (lib/project_paths.sh)

The helper tests pin what each reader returns, including when the library
was sourced by a relative path and the caller has since changed to an
unrelated directory. The per-check tests run every
converted check's environment resolver both standalone (only the check
sourced, so the helper loads its library itself) and as the sweep runs it
(the libraries already loaded), and pin the messages for an unset
OVERWATCH_PROJECT on a multi-project install and for no resolvable project,
plus the branch_prefix each prefix check resolves.

Every fixture lives under pytest's tmp_path; no live kanban root is read or
written.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib"
_CHECKS = _LIB / "overwatch-checks"
_CONFIG = _LIB / "overwatch_config.sh"

# The checks that resolve a branch_prefix, and the variable each one sets.
_PREFIX_CHECKS = {
    "check-orphan-rc-branches": "_CORB_BRANCH_PREFIX",
    "check-stale-worktrees": "_CSW_BRANCH_PREFIX",
    "check-stale-active-rc": "_CSARC_BRANCH_PREFIX",
    "check-changelog-drift": "_CCD_BRANCH_PREFIX",
    "check-transient-api-error": "_CTAE_BRANCH_PREFIX",
}
_REGISTRY_ONLY_CHECKS = (
    "check-leaked-listeners",
    "check-cache-marker-drift",
    "check-tester-orphan-files",
    "check-version-divergence",
    "check-readme-bundled",
    "check-bare-tmp-litter",
    "check-push-lag",
)
_ALL_CHECKS = tuple(sorted((*_PREFIX_CHECKS, *_REGISTRY_ONLY_CHECKS)))

# What the sweep runner has loaded before it sources any check.
_SWEEP_PRELOAD = "".join(
    f"source '{_LIB / name}'\n"
    for name in ("temp.sh", "project_paths.sh", "projects.sh", "overwatch_lib.sh", "overwatch_protocol.sh")
)
_CONTEXTS = {"standalone": "", "sweep": _SWEEP_PRELOAD}

# Ambient names that would point a resolver away from the fixture.
_UNSET = "unset OVERWATCH_PROJECT PGAI_PROJECT_NAME PGAI_DEV_TREE_PATH PGAI_TASKS_DIR PGAI_PROJECT_ROOT\n"


def _kanban(tmp_path: pathlib.Path, registry: str | None, project_cfg: str | None = None) -> pathlib.Path:
    """A kanban root with *registry* as projects.cfg and *project_cfg* as alpha's project.cfg."""
    root = tmp_path / "kanban"
    (root / "projects" / "alpha").mkdir(parents=True)
    (root / "tmp").mkdir()
    if registry is not None:
        (root / "projects.cfg").write_text(registry, encoding="utf-8")
    if project_cfg is not None:
        dev = tmp_path / "dev"
        dev.mkdir()
        (root / "projects" / "alpha" / "project.cfg").write_text(
            f"[project]\nproject_name=alpha\ndev_tree_path={dev}\n{project_cfg}\n", encoding="utf-8"
        )
    return root


def _run(tmp_path: pathlib.Path, root: pathlib.Path, script: str):
    return run_bash(
        tmp_path,
        _UNSET + script,
        timeout=60,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )


def _resolver(check: str) -> str:
    """The name of *check*'s environment resolver function."""
    text = (_CHECKS / f"{check}.sh").read_text(encoding="utf-8")
    match = re.search(r"^(_\w+_resolve_env)\(\) \{$", text, re.M)
    assert match, f"{check}.sh defines no _<abbr>_resolve_env function"
    return match.group(1)


def _resolve(tmp_path: pathlib.Path, root: pathlib.Path, check: str, context: str, var: str = ""):
    """Source *check* in *context*, run its resolver, print rc, project and *var*."""
    show = f'printf "prefix=[%s]\\n" "${{{var}-unset}}"\n' if var else ""
    return _run(
        tmp_path,
        root,
        f"{_CONTEXTS[context]}source '{_CHECKS / check}.sh'\n"
        f"{_resolver(check)}; rc=$?\n"
        'printf "rc=%s project=%s\\n" "$rc" "${OVERWATCH_PROJECT:-}"\n' + show,
    )


# --- overwatch_branch_prefix ----------------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ("", ""),
        ("branch_prefix=ai_", "ai_"),
        ('branch_prefix = ""', ""),
        ("branch_prefix =", ""),
        ('branch_prefix = "ai_"', "ai_"),
        ("branch_prefix = xy-", "xy-"),
    ],
    ids=["absent", "ai_", "quoted-empty", "blank", "double-quoted", "dash"],
)
def test_branch_prefix_reads_the_configured_value(tmp_path: pathlib.Path, line: str, expected: str) -> None:
    root = _kanban(tmp_path, "[project:alpha]\npriority=1\n", line)
    result = _run(tmp_path, root, f"source '{_CONFIG}'\nprintf '[%s]' \"$(overwatch_branch_prefix alpha)\"\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"[{expected}]"


def test_branch_prefix_is_empty_without_a_project_cfg(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path, "[project:alpha]\npriority=1\n")
    result = _run(tmp_path, root, f"source '{_CONFIG}'\nprintf '[%s]' \"$(overwatch_branch_prefix alpha)\"\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "[]"


def test_branch_prefix_refuses_an_invalid_value_naming_the_file(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path, "[project:alpha]\npriority=1\n", "branch_prefix = ai/")
    result = _run(tmp_path, root, f"source '{_CONFIG}'\noverwatch_branch_prefix alpha\n")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "invalid branch_prefix 'ai/'" in result.stderr
    assert f"set [project] branch_prefix in {root}/projects/alpha/project.cfg" in result.stderr


def test_branch_prefix_requires_a_project_name(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path, "[project:alpha]\npriority=1\n", "branch_prefix=ai_")
    result = _run(tmp_path, root, f"source '{_CONFIG}'\noverwatch_branch_prefix ''\n")
    assert result.returncode == 1
    assert "a project name is required" in result.stderr


# --- overwatch_registered_projects ----------------------------------------------


@pytest.mark.parametrize(
    "registry, expected",
    [
        ("[project:zeta]\npriority=2\n\n[project:alpha]\npriority=1\n\n[project:beta]\n", "alpha\nzeta\nbeta\n"),
        ("zeta:2\nalpha:1\n", "alpha\nzeta\n"),
        ("# no projects yet\n", ""),
        (None, ""),
    ],
    ids=["ini-priority-order", "colon-legacy", "empty", "missing"],
)
@pytest.mark.parametrize("sourcing", ["absolute", "relative", "relative-then-cd"])
def test_registered_projects_lists_in_priority_order(
    tmp_path: pathlib.Path, registry: str | None, expected: str, sourcing: str
) -> None:
    root = _kanban(tmp_path, registry)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    load = {
        "absolute": f"source '{_CONFIG}'\n",
        "relative": f"cd '{_LIB.parent}'\nsource lib/overwatch_config.sh\n",
        # The library resolves its own directory when sourced, so a relative
        # name still finds lib/projects.sh after the caller leaves the
        # directory it was named from.
        "relative-then-cd": f"cd '{_LIB.parent}'\nsource lib/overwatch_config.sh\ncd '{elsewhere}'\n",
    }[sourcing]
    result = _run(tmp_path, root, f'{load}overwatch_registered_projects; echo "rc=$?"\n')
    assert result.stdout == f"{expected}rc=0\n"


def test_sourcing_the_lib_twice_is_silent_and_harmless(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path, "[project:alpha]\npriority=1\n")
    result = _run(
        tmp_path,
        root,
        f"set -euo pipefail\n{_SWEEP_PRELOAD}source '{_CONFIG}'\nsource '{_CONFIG}'\noverwatch_registered_projects\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "alpha\n"


# --- the converted checks -------------------------------------------------------


@pytest.mark.parametrize("context", sorted(_CONTEXTS))
@pytest.mark.parametrize("check", _ALL_CHECKS)
def test_unset_project_on_a_multi_project_install_refuses(tmp_path: pathlib.Path, check: str, context: str) -> None:
    root = _kanban(tmp_path, "[project:zeta]\npriority=2\n\n[project:alpha]\npriority=1\n")
    result = _resolve(tmp_path, root, check, context)
    assert "rc=1 project=\n" in result.stdout
    assert re.search(
        rf"^{check}: ERROR: OVERWATCH_PROJECT (is )?not set and multiple projects (are )?registered\.$",
        result.stderr,
        re.M,
    ), result.stderr
    if "Registered projects:" in result.stderr:
        assert "  Registered projects: alpha zeta \n" in result.stderr


@pytest.mark.parametrize("context", sorted(_CONTEXTS))
@pytest.mark.parametrize("check", _ALL_CHECKS)
def test_no_resolvable_project_refuses(tmp_path: pathlib.Path, check: str, context: str) -> None:
    root = _kanban(tmp_path, None)
    result = _resolve(tmp_path, root, check, context)
    assert "rc=1 project=\n" in result.stdout
    assert f"{check}: ERROR: no project specified and none resolvable from projects.cfg\n" in result.stderr


@pytest.mark.parametrize("context", sorted(_CONTEXTS))
@pytest.mark.parametrize("check", _ALL_CHECKS)
def test_empty_registry_counts_no_projects_and_refuses_cleanly(
    tmp_path: pathlib.Path, check: str, context: str
) -> None:
    """An empty projects.cfg counts as 0 projects: the refusal is the only stderr text."""
    root = _kanban(tmp_path, "")
    result = _resolve(tmp_path, root, check, context)
    assert "rc=1 project=\n" in result.stdout
    assert "syntax error" not in result.stderr, result.stderr
    assert f"{check}: ERROR: no project specified and none resolvable from projects.cfg\n" in result.stderr


@pytest.mark.parametrize("context", sorted(_CONTEXTS))
@pytest.mark.parametrize("check", _ALL_CHECKS)
def test_single_registered_project_is_resolved(tmp_path: pathlib.Path, check: str, context: str) -> None:
    root = _kanban(tmp_path, "[project:alpha]\npriority=1\n", "")
    result = _resolve(tmp_path, root, check, context)
    assert "rc=0 project=alpha\n" in result.stdout, result.stderr


@pytest.mark.parametrize(
    "line, expected",
    [("", ""), ("branch_prefix=ai_", "ai_"), ('branch_prefix = ""', "")],
    ids=["absent", "ai_", "quoted-empty"],
)
@pytest.mark.parametrize("context", sorted(_CONTEXTS))
@pytest.mark.parametrize("check", sorted(_PREFIX_CHECKS))
def test_prefix_checks_resolve_the_branch_prefix(
    tmp_path: pathlib.Path, check: str, context: str, line: str, expected: str
) -> None:
    root = _kanban(tmp_path, "[project:alpha]\npriority=1\n", line)
    result = _resolve(tmp_path, root, check, context, _PREFIX_CHECKS[check])
    assert result.stdout == f"rc=0 project=alpha\nprefix=[{expected}]\n", result.stderr


@pytest.mark.parametrize("check", sorted(_PREFIX_CHECKS))
def test_prefix_checks_refuse_an_invalid_branch_prefix(tmp_path: pathlib.Path, check: str) -> None:
    root = _kanban(tmp_path, "[project:alpha]\npriority=1\n", "branch_prefix = ai/")
    result = _resolve(tmp_path, root, check, "standalone")
    assert "rc=1 project=alpha\n" in result.stdout
    assert "invalid branch_prefix 'ai/'" in result.stderr


@pytest.mark.parametrize("check", _ALL_CHECKS)
def test_checks_parse_neither_config_themselves(check: str) -> None:
    text = (_CHECKS / f"{check}.sh").read_text(encoding="utf-8")
    assert "branch_prefix[[:space:]]*=" not in text
    assert "/^\\[project:" not in text
    assert 'source "$(dirname "${BASH_SOURCE[0]}")/../overwatch_config.sh"' in text
