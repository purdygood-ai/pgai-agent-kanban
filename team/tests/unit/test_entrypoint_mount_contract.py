"""
test_entrypoint_mount_contract.py
==================================
Behavioral unit tests for docker/entrypoint.sh — the container startup
script that enforces the launcher's mount contract before landing the
operator in a shell.

The launcher (team/scripts/container-shell.sh) promises the entrypoint three
things: PGAI_AGENT_KANBAN_ROOT_PATH names an existing, bind-mounted kanban
root; HOME names an existing, bind-mounted operator home; and the directory
presented as the kanban root is one install.sh has populated, never a raw
repository checkout. This suite drives docker/entrypoint.sh directly as a
shell script against temporary directories — no container engine is started
— and covers each of the three refusals plus the satisfied-contract success
path, asserting on message content (the unmet condition, the offending path,
and a runnable remedy) rather than the exit code alone.

Test naming describes behavior, never bug IDs or scaffolding labels.
"""

from __future__ import annotations

import pathlib

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Path resolution — anchored to the test file location.
# test file:  team/tests/unit/test_entrypoint_mount_contract.py
# team/:      team/tests/unit/../../../ = team/ (3 levels up)
# repo root:  team/../                  = repo root
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_ENTRYPOINT = _REPO_ROOT / "docker" / "entrypoint.sh"


# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------


def _make_install_root(tmp_path: pathlib.Path, name: str = "install_root") -> pathlib.Path:
    """Build a directory shaped like an install.sh-populated kanban root.

    The distinguishing artifact install.sh always produces is
    scripts/pseudocron.py — install.sh copies team/scripts/ (source layout)
    to scripts/ (install layout) at the kanban root, and pseudocron.py sits
    directly under team/scripts/ in the source tree.
    """
    root = tmp_path / name
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "pseudocron.py").write_text("# stub\n", encoding="utf-8")
    return root


def _make_checkout_root(tmp_path: pathlib.Path, name: str = "checkout_root") -> pathlib.Path:
    """Build a directory shaped like a raw repository checkout: helpers live
    at team/scripts/ instead of scripts/, exactly the layout a checkout
    mounted in an install's place produces.
    """
    root = tmp_path / name
    (root / "team" / "scripts").mkdir(parents=True)
    (root / "team" / "scripts" / "pseudocron.py").write_text("# stub\n", encoding="utf-8")
    return root


def _make_home(tmp_path: pathlib.Path, name: str = "home") -> pathlib.Path:
    home = tmp_path / name
    home.mkdir()
    return home


# ---------------------------------------------------------------------------
# Condition 1 — PGAI_AGENT_KANBAN_ROOT_PATH
# ---------------------------------------------------------------------------


def test_missing_kanban_root_env_var_refuses_naming_variable_and_remedy(
    tmp_path: pathlib.Path,
) -> None:
    """No PGAI_AGENT_KANBAN_ROOT_PATH at all -> non-zero exit, variable named,
    a host-runnable remedy printed.
    """
    home = _make_home(tmp_path)

    result = run_bash(
        tmp_path,
        f"unset PGAI_AGENT_KANBAN_ROOT_PATH; bash {_ENTRYPOINT}",
        extra_env={"HOME": str(home)},
    )

    assert result.returncode != 0, (
        f"expected non-zero exit; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "PGAI_AGENT_KANBAN_ROOT_PATH" in result.stderr, (
        f"variable name not named in stderr:\n{result.stderr!r}"
    )
    assert "Remedy" in result.stderr, f"no remedy printed:\n{result.stderr!r}"
    assert "container-shell.sh" in result.stderr or "--kanban-root" in result.stderr, (
        f"remedy does not name a host-runnable fix:\n{result.stderr!r}"
    )


def test_kanban_root_pointing_at_absent_directory_refuses_naming_path(
    tmp_path: pathlib.Path,
) -> None:
    """PGAI_AGENT_KANBAN_ROOT_PATH set to a nonexistent directory -> non-zero
    exit, the offending path named.
    """
    home = _make_home(tmp_path)
    missing_root = tmp_path / "does-not-exist"

    result = run_bash(
        tmp_path,
        f"bash {_ENTRYPOINT}",
        extra_env={"HOME": str(home), "PGAI_AGENT_KANBAN_ROOT_PATH": str(missing_root)},
    )

    assert result.returncode != 0, (
        f"expected non-zero exit; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert str(missing_root) in result.stderr, (
        f"missing root path not named in stderr:\n{result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Condition 2 — HOME
# ---------------------------------------------------------------------------


def test_missing_home_env_var_refuses_naming_variable_and_remedy(
    tmp_path: pathlib.Path,
) -> None:
    """A valid, install-shaped kanban root but no HOME at all -> non-zero
    exit, HOME named, a host-runnable remedy printed.
    """
    root = _make_install_root(tmp_path)

    result = run_bash(
        tmp_path,
        f"unset HOME; bash {_ENTRYPOINT}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(root)},
    )

    assert result.returncode != 0, (
        f"expected non-zero exit; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "HOME" in result.stderr, f"HOME not named in stderr:\n{result.stderr!r}"
    assert "Remedy" in result.stderr, f"no remedy printed:\n{result.stderr!r}"
    assert "container-shell.sh" in result.stderr, (
        f"remedy does not point back at the launcher:\n{result.stderr!r}"
    )


def test_home_pointing_at_absent_directory_refuses_naming_path(
    tmp_path: pathlib.Path,
) -> None:
    """HOME set to a nonexistent directory -> non-zero exit, the offending
    path named.
    """
    root = _make_install_root(tmp_path)
    missing_home = tmp_path / "no-such-home"

    result = run_bash(
        tmp_path,
        f"bash {_ENTRYPOINT}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(root), "HOME": str(missing_home)},
    )

    assert result.returncode != 0, (
        f"expected non-zero exit; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert str(missing_home) in result.stderr, (
        f"missing HOME path not named in stderr:\n{result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Condition 3 — install, not a raw repository checkout
# ---------------------------------------------------------------------------


def test_checkout_mounted_as_kanban_root_refuses_naming_checkout_and_install_remedy(
    tmp_path: pathlib.Path,
) -> None:
    """A raw repository checkout (helpers at team/scripts/, none at scripts/)
    mounted in the install's place -> non-zero exit, the checkout condition
    named, and a remedy naming the install.sh invocation that populates a
    volume.
    """
    root = _make_checkout_root(tmp_path)
    home = _make_home(tmp_path)

    result = run_bash(
        tmp_path,
        f"bash {_ENTRYPOINT}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(root), "HOME": str(home)},
    )

    assert result.returncode != 0, (
        f"expected non-zero exit; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert str(root) in result.stderr, (
        f"offending kanban-root path not named in stderr:\n{result.stderr!r}"
    )
    assert "checkout" in result.stderr, (
        f"error does not name the repository-checkout condition:\n{result.stderr!r}"
    )
    assert "team/scripts" in result.stderr, (
        f"error does not explain the checkout-vs-install layout difference:\n{result.stderr!r}"
    )
    assert "install.sh" in result.stderr, (
        f"remedy does not name the install.sh invocation:\n{result.stderr!r}"
    )


def test_kanban_root_missing_install_artifact_refuses_even_without_checkout_layout(
    tmp_path: pathlib.Path,
) -> None:
    """A kanban root that is neither install-shaped nor checkout-shaped (e.g.
    an empty directory) still refuses -- the contract requires a positive
    install signal, not merely the absence of a checkout signal.
    """
    root = tmp_path / "empty_root"
    root.mkdir()
    home = _make_home(tmp_path)

    result = run_bash(
        tmp_path,
        f"bash {_ENTRYPOINT}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(root), "HOME": str(home)},
    )

    assert result.returncode != 0, (
        f"expected non-zero exit; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert str(root) in result.stderr, (
        f"offending kanban-root path not named in stderr:\n{result.stderr!r}"
    )
    assert "install.sh" in result.stderr, (
        f"remedy does not name the install.sh invocation:\n{result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Satisfied contract — unchanged success path
# ---------------------------------------------------------------------------


def test_satisfied_contract_lands_in_kanban_root_via_login_shell(
    tmp_path: pathlib.Path,
) -> None:
    """All three conditions satisfied -> the script cd's into the kanban root
    and execs a login bash exactly as before.

    bash -l with no -c and non-interactive stdin still reads and runs
    commands from stdin, so feeding `pwd` proves both that the exec
    happened and that the working directory is the kanban root.
    """
    root = _make_install_root(tmp_path)
    home = _make_home(tmp_path)

    result = run_bash(
        tmp_path,
        f"bash {_ENTRYPOINT}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(root), "HOME": str(home)},
        stdin="pwd\n",
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert result.stdout.strip() == str(root), (
        f"login shell did not land in the kanban root:\n{result.stdout!r}"
    )


def test_satisfied_contract_produces_no_error_output(tmp_path: pathlib.Path) -> None:
    """The satisfied-contract path prints no refusal text -- a clean landing,
    not a warning-then-continue.
    """
    root = _make_install_root(tmp_path)
    home = _make_home(tmp_path)

    result = run_bash(
        tmp_path,
        f"bash {_ENTRYPOINT}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(root), "HOME": str(home)},
        stdin="exit 0\n",
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "ERROR" not in result.stderr, f"unexpected error output:\n{result.stderr!r}"
