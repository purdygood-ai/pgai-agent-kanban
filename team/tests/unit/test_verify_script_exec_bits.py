"""
test_verify_script_exec_bits.py
===============================
Keeps verification/verify_script_exec_bits.sh honest about committed .sh
files whose paths contain whitespace, in both directions, and about committed
.sh entries that are not regular files (symlinks and gitlinks):

  1. A spaced-path .sh file committed at 100644 is listed whole under the
     FAIL line and the script exits 1.
  2. The same file committed at 100755 passes: exit 0 and the OK line.
  3. The error-contract ``remedy:`` line, run through bash from outside the
     repository (it carries ``git -C <repo>``), fixes the spaced path.
  4. Each "Fix with:" hint line, run through bash from the repo root (the
     hints carry no -C), fixes the spaced path.
  5. A plain-path 100644 file is still reported exactly as before, next to a
     spaced one, and both are listed.
  6. The script parses (bash -n).
  7. A committed .sh symlink (index mode 120000) beside a 100644 file is
     skipped: only the regular file is listed, no hint or remedy names the
     symlink, and the printed remedy and hints all run and fix the listing.
  8. A repository whose only non-100755 .sh entry is a symlink passes.
  9. A committed .sh gitlink (index mode 160000, a submodule entry) beside a
     100644 file is skipped: only the regular file is listed, no hint or
     remedy names the gitlink, and the printed remedy and hints all run and
     fix the listing.

Every case builds a throwaway git repository under pytest's tmp_path and runs
the real script from this tree, so it sources the shared error-contract
emitter beside it. The run sets or clears all five live-install names
together (docs/test-authoring-guidelines.md "Live-install safety"):
KANBAN_ROOT, PGAI_AGENT_KANBAN_ROOT_PATH and PGAI_AGENT_KANBAN_TEMP_DIR point
into a sandbox under tmp_path, PGAI_TASKS_DIR and PGAI_PROJECT_ROOT are
unset. PGAI_PROJECT_NAME is unset too, so the emitter's refusal-ledger
recording is skipped and no project directory is written.
"""

from __future__ import annotations

import pathlib
import shlex
import subprocess

from tests.unit.shell_harness import BashResult, run_bash

_TEAM_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _TEAM_ROOT / "verification" / "verify_script_exec_bits.sh"

_OK_LINE = "verify_script_exec_bits: OK — all .sh files have mode 100755."
_FAIL_LINE = (
    "verify_script_exec_bits: FAIL — the following .sh files are missing "
    "the exec bit (mode != 100755):"
)
_SPACED = "team/scripts/has space.sh"
_PLAIN = "team/scripts/a.sh"
_LINK = "team/scripts/link.sh"
_GITLINK = "team/scripts/mod.sh"


def _git(repo: pathlib.Path, *args: str) -> str:
    """Run git against *repo* with a fixed identity and return its stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed in {repo}:\n{result.stdout}\n{result.stderr}"
    )
    return result.stdout


def _make_repo(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    """Build a git repo with team/scripts/ and team/verification/ holding
    *files* (repo-relative path -> committed mode) and commit them."""
    repo = tmp_path / "repo"
    (repo / "team" / "scripts").mkdir(parents=True)
    (repo / "team" / "verification").mkdir(parents=True)
    _git(repo, "init", "-q")
    for rel, mode in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        target.chmod(0o755 if mode == "100755" else 0o644)
    _git(repo, "add", "-A")
    # The index records the mode named, whatever core.fileMode this host has.
    for rel, mode in files.items():
        _git(repo, "update-index", "--chmod=+x" if mode == "100755" else "--chmod=-x", "--", rel)
    _git(repo, "commit", "-qm", "init")
    return repo


def _commit_symlink(repo: pathlib.Path, rel: str, target: str) -> None:
    """Commit a symlink at *rel* (repo-relative) pointing at *target*, which
    is relative to the link's own directory, and assert the index records it
    as a symlink (mode 120000)."""
    link = repo / rel
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    _git(repo, "add", "--", rel)
    _git(repo, "commit", "-qm", f"add symlink {rel}")
    assert _index_mode(repo, rel) == "120000", f"{rel} is not committed as a symlink"


def _commit_gitlink(repo: pathlib.Path, rel: str) -> None:
    """Commit a gitlink (submodule entry) at *rel* (repo-relative) pointing
    at the repository's own HEAD commit, and assert the index records it as
    a gitlink (mode 160000). No submodule is checked out: git stores only the
    commit pointer, which is all the verifier reads."""
    head = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},{rel}")
    _git(repo, "commit", "-qm", f"add gitlink {rel}")
    assert _index_mode(repo, rel) == "160000", f"{rel} is not committed as a gitlink"


def _sandboxed(tmp_path: pathlib.Path, command: str, cwd: pathlib.Path) -> BashResult:
    """Run *command* from *cwd* with every live-install name set or cleared."""
    sandbox = tmp_path / "sandbox"
    (sandbox / "tmp").mkdir(parents=True, exist_ok=True)
    script = (
        "env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT -u PGAI_PROJECT_NAME "
        f"KANBAN_ROOT={shlex.quote(str(sandbox))} "
        f"PGAI_AGENT_KANBAN_ROOT_PATH={shlex.quote(str(sandbox))} "
        f"PGAI_AGENT_KANBAN_TEMP_DIR={shlex.quote(str(sandbox / 'tmp'))} "
        f"bash -c {shlex.quote(command)}"
    )
    return run_bash(tmp_path, script, cwd=cwd, timeout=60)


def _run_check(tmp_path: pathlib.Path, repo: pathlib.Path) -> BashResult:
    """Run the verifier against *repo* from tmp_path (not the repo itself)."""
    command = f"bash {shlex.quote(str(_SCRIPT))} --repo-root {shlex.quote(str(repo))}"
    return _sandboxed(tmp_path, command, cwd=tmp_path)


def _fail_listing(stdout: str) -> list[str]:
    """Return the entries listed under the FAIL line, stripped of indent."""
    lines = stdout.splitlines()
    assert _FAIL_LINE in lines, f"no FAIL line in output:\n{stdout}"
    entries = []
    for line in lines[lines.index(_FAIL_LINE) + 1 :]:
        if not line.strip():
            break
        entries.append(line.strip())
    return entries


def _fix_with_hints(stdout: str) -> list[str]:
    """Return the command lines printed under "Fix with:"."""
    lines = stdout.splitlines()
    assert "Fix with:" in lines, f"no 'Fix with:' section in output:\n{stdout}"
    return [line.strip() for line in lines[lines.index("Fix with:") + 1 :] if line.strip()]


def _remedy(stderr: str) -> str:
    """Return the error-contract remedy command from stderr."""
    for line in stderr.splitlines():
        if line.startswith("remedy:"):
            return line[len("remedy:") :].strip()
    raise AssertionError(f"no remedy: line on stderr:\n{stderr}")


def _index_mode(repo: pathlib.Path, rel: str) -> str:
    """Return the mode the index records for *rel*."""
    return _git(repo, "ls-files", "--stage", "--", rel).split(" ", 1)[0]


def test_spaced_path_at_100644_is_listed_under_fail(tmp_path):
    repo = _make_repo(tmp_path, {_SPACED: "100644", "team/verification/ok.sh": "100755"})

    result = _run_check(tmp_path, repo)

    assert result.returncode == 1, result.stdout + result.stderr
    assert _fail_listing(result.stdout) == [f"{_SPACED} (mode 100644)"]
    assert "verify_script_exec_bits.exec_bit_missing" in result.stderr


def test_spaced_path_at_100755_passes(tmp_path):
    repo = _make_repo(
        tmp_path,
        {_SPACED: "100755", "team/verification/also spaced.sh": "100755", "team/scripts/notes x.txt": "100644"},
    )

    result = _run_check(tmp_path, repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == _OK_LINE


def test_error_contract_remedy_fixes_spaced_path(tmp_path):
    repo = _make_repo(tmp_path, {_SPACED: "100644"})
    remedy = _remedy(_run_check(tmp_path, repo).stderr)

    # The remedy carries `git -C <repo>`, so run it from outside the repo.
    applied = _sandboxed(tmp_path, remedy, cwd=tmp_path)

    assert applied.returncode == 0, f"remedy {remedy!r} failed:\n{applied.stderr}"
    assert _index_mode(repo, _SPACED) == "100755"
    rerun = _run_check(tmp_path, repo)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert rerun.stdout.strip() == _OK_LINE


def test_fix_with_hints_fix_spaced_path_from_repo_root(tmp_path):
    repo = _make_repo(tmp_path, {_SPACED: "100644", "team/verification/v w.sh": "100644"})
    hints = _fix_with_hints(_run_check(tmp_path, repo).stdout)
    assert len(hints) == 2, hints

    # The hints carry no -C: they are documented to run from the repo root.
    for hint in hints:
        applied = _sandboxed(tmp_path, hint, cwd=repo)
        assert applied.returncode == 0, f"hint {hint!r} failed:\n{applied.stderr}"

    assert _index_mode(repo, _SPACED) == "100755"
    assert _index_mode(repo, "team/verification/v w.sh") == "100755"
    rerun = _run_check(tmp_path, repo)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert rerun.stdout.strip() == _OK_LINE


def test_plain_and_spaced_100644_files_are_both_listed(tmp_path):
    repo = _make_repo(tmp_path, {_PLAIN: "100644", _SPACED: "100644"})

    result = _run_check(tmp_path, repo)

    assert result.returncode == 1, result.stdout + result.stderr
    assert _fail_listing(result.stdout) == [
        f"{_PLAIN} (mode 100644)",
        f"{_SPACED} (mode 100644)",
    ]
    # A plain path's hint reads exactly as it always has.
    assert f"git update-index --chmod=+x {_PLAIN}" in _fix_with_hints(result.stdout)


def test_script_parses():
    result = subprocess.run(["bash", "-n", str(_SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_symlink_beside_100644_file_is_skipped_and_every_remedy_runs(tmp_path):
    repo = _make_repo(tmp_path, {_PLAIN: "100755", _SPACED: "100644"})
    _commit_symlink(repo, _LINK, "a.sh")

    first = _run_check(tmp_path, repo)

    assert first.returncode == 1, first.stdout + first.stderr
    assert _fail_listing(first.stdout) == [f"{_SPACED} (mode 100644)"]
    hints = _fix_with_hints(first.stdout)
    remedy = _remedy(first.stderr)
    assert not [hint for hint in hints if _LINK in hint], hints
    assert _LINK not in remedy, remedy

    # The remedy carries `git -C <repo>`, so run it from outside the repo.
    applied = _sandboxed(tmp_path, remedy, cwd=tmp_path)
    assert applied.returncode == 0, f"remedy {remedy!r} failed:\n{applied.stderr}"
    # The hints carry no -C: they are documented to run from the repo root.
    for hint in hints:
        applied = _sandboxed(tmp_path, hint, cwd=repo)
        assert applied.returncode == 0, f"hint {hint!r} failed:\n{applied.stderr}"

    assert _index_mode(repo, _SPACED) == "100755"
    rerun = _run_check(tmp_path, repo)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert rerun.stdout.strip() == _OK_LINE


def test_repo_whose_only_non_100755_entry_is_a_symlink_passes(tmp_path):
    repo = _make_repo(tmp_path, {_PLAIN: "100755"})
    _commit_symlink(repo, _LINK, "a.sh")

    result = _run_check(tmp_path, repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == _OK_LINE


def test_gitlink_beside_100644_file_is_skipped_and_every_remedy_runs(tmp_path):
    repo = _make_repo(tmp_path, {_SPACED: "100644"})
    _commit_gitlink(repo, _GITLINK)

    first = _run_check(tmp_path, repo)

    assert first.returncode == 1, first.stdout + first.stderr
    assert _fail_listing(first.stdout) == [f"{_SPACED} (mode 100644)"]
    hints = _fix_with_hints(first.stdout)
    remedy = _remedy(first.stderr)
    assert not [hint for hint in hints if "mod.sh" in hint], hints
    assert "mod.sh" not in remedy, remedy

    # The remedy carries `git -C <repo>`, so run it from outside the repo.
    applied = _sandboxed(tmp_path, remedy, cwd=tmp_path)
    assert applied.returncode == 0, f"remedy {remedy!r} failed:\n{applied.stderr}"
    # The hints carry no -C: they are documented to run from the repo root.
    for hint in hints:
        applied = _sandboxed(tmp_path, hint, cwd=repo)
        assert applied.returncode == 0, f"hint {hint!r} failed:\n{applied.stderr}"

    assert _index_mode(repo, _SPACED) == "100755"
    assert _index_mode(repo, _GITLINK) == "160000"
    rerun = _run_check(tmp_path, repo)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert rerun.stdout.strip() == _OK_LINE


# ---------------------------------------------------------------------------
# Outside a checkout: the --repo-root remedy names a resolved dev tree
#
# A copy of the verification tree that is not inside a git checkout (an
# install, say) cannot name its own repository, so the not-in-a-repo remedy
# names PGAI_DEV_TREE_PATH, else the project's dev_tree_path, else a neutral
# example path -- never an operator's home-relative location.
# ---------------------------------------------------------------------------


def _verification_copy(tmp_path: pathlib.Path, with_scripts_lib: bool) -> pathlib.Path:
    """Copy team/verification (and, when asked, team/scripts/lib) to a
    directory outside any checkout; return the copied script's path."""
    import shutil

    install = tmp_path / "install"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(_TEAM_ROOT / "verification", install / "verification", ignore=ignore)
    if with_scripts_lib:
        shutil.copytree(_TEAM_ROOT / "scripts" / "lib", install / "scripts" / "lib", ignore=ignore)
    return install / "verification" / _SCRIPT.name


def _run_outside_checkout(tmp_path: pathlib.Path, script: pathlib.Path, env: str = "") -> BashResult:
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    command = (
        f"export GIT_CEILING_DIRECTORIES={shlex.quote(str(tmp_path))}; {env} "
        f"bash {shlex.quote(str(script))}"
    )
    return _sandboxed(tmp_path, command, cwd=outside)


def test_not_in_checkout_remedy_names_an_example_path_when_no_dev_tree_is_known(tmp_path):
    script = _verification_copy(tmp_path, with_scripts_lib=False)
    result = _run_outside_checkout(tmp_path, script, env="unset PGAI_DEV_TREE_PATH;")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "error:   verify_script_exec_bits.not_in_git_repo" in result.stderr
    remedy = _remedy(result.stderr)
    assert "<" not in remedy and ">" not in remedy, remedy
    assert "develop/pgai-agent-kanban" not in result.stderr
    assert remedy.endswith("--repo-root /srv/develop/my-app"), remedy
    # Runnable as printed: it runs the verifier, which refuses the example
    # path through its own contract rather than failing to parse or start.
    ran = run_bash(tmp_path, remedy, cwd=tmp_path)
    assert "verify_script_exec_bits.repo_root_not_git" in ran.stderr, ran.stdout + ran.stderr


def test_not_in_checkout_remedy_names_pgai_dev_tree_path(tmp_path):
    repo = _make_repo(tmp_path, {"team/scripts/ok.sh": "100755"})
    script = _verification_copy(tmp_path, with_scripts_lib=False)
    result = _run_outside_checkout(
        tmp_path, script, env=f"export PGAI_DEV_TREE_PATH={shlex.quote(str(repo))};"
    )

    assert result.returncode == 1
    remedy = _remedy(result.stderr)
    assert remedy.endswith(f"--repo-root {shlex.quote(str(repo))}"), remedy
    ran = _sandboxed(tmp_path, remedy, cwd=tmp_path)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert _OK_LINE in ran.stdout


def test_not_in_checkout_remedy_names_the_projects_dev_tree_path(tmp_path):
    repo = _make_repo(tmp_path, {"team/scripts/ok.sh": "100755"})
    project_dir = tmp_path / "sandbox" / "projects" / "pgai-agent-kanban"
    project_dir.mkdir(parents=True)
    (project_dir / "project.cfg").write_text(
        f"[project]\nproject_name = pgai-agent-kanban\ndev_tree_path = {repo}\n", encoding="utf-8"
    )
    script = _verification_copy(tmp_path, with_scripts_lib=True)
    result = _run_outside_checkout(tmp_path, script, env="unset PGAI_DEV_TREE_PATH;")

    assert result.returncode == 1, result.stdout + result.stderr
    remedy = _remedy(result.stderr)
    assert remedy.endswith(f"--repo-root {shlex.quote(str(repo))}"), remedy
    ran = _sandboxed(tmp_path, remedy, cwd=tmp_path)
    assert ran.returncode == 0, ran.stdout + ran.stderr
