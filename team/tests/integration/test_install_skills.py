"""
test_install_skills.py — scripts/install-skills.sh, the operator verb that
copies the skills under a source directory into the provider CLI skill homes.

Coverage:
  - Over a sandbox root's default staging directory, an install into two
    named homes copies both shipped skills whole; a second run reports
    ``same`` and exits 0; an edited home copy is left with exit 3 and a
    runnable ``--force`` remedy, which overwrites it. Nothing outside the
    two homes is written.
  - A flat ``--dir`` with ``--provider codex`` installs into the codex home
    only, whole directories and file modes included.
  - With PATH narrowed so one provider's CLI is absent, that provider is
    skipped with one notice line and the exit stays 0; the default homes
    are the sandbox HOME's.
  - ``--dry-run``, ``--list`` and ``--list --json`` write nothing; the two
    list forms are pinned by goldens.
  - ``--help`` lists exactly the declared flags, the census row's flags.
  - Usage errors exit 2 with a dual-channel block (the shared ``unknown
    argument`` wording for an unknown flag) and write nothing.
  - The default homes the script lists equal the shared provider-home
    rule's (skills_install.provider_skills_home over
    pgai_agent_kanban.lib.provider_home).
  - GET /skills calls skills_install.list_skills and the script reaches it
    through pp_run_ops.

Every run builds a throwaway kanban root and HOME under tmp_path, with no
CLAUDE_CONFIG_DIR, PGAI_TASKS_DIR or PGAI_PROJECT_ROOT inherited, and stub
``claude`` / ``codex`` executables on PATH where a provider must count as
installed; nothing reads or writes the real ~/.claude or ~/.codex.
"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import skills_install as si
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest
from tests.fixtures.route_cli_parity import assert_route_cli_parity, build_api_client
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.fixtures.upgrade_sandbox import diagnostic

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_SCRIPT = _TEAM_DIR / "scripts" / "install-skills.sh"
_GOLDEN_DIR = _TEAM_DIR / "tests" / "fixtures" / "golden"
_SHIPPED = "pgai-agent-kanban"
_PROVIDERS = ("claude", "codex")
# System tool directories for a narrowed PATH: python3's own directory is
# added separately, and /usr/local/bin is left out so a host-wide provider
# CLI there cannot count as installed.
_SYSTEM_DIRS = ("/usr/bin", "/bin")


class Sandbox:
    """A throwaway kanban root whose staging directory holds the
    repository's shipped skills, a sandbox HOME, and a stub-binary
    directory with ``claude`` and ``codex``."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "root"
        self.home = tmp_path / "user-home"
        self.bin = tmp_path / "bin"
        for d in (self.root / "tmp", self.home, self.bin):
            d.mkdir(parents=True)
        self.staging = self.root / si.STAGING_DIR
        shutil.copytree(_REPO_ROOT / "skills", self.staging)
        shipped = sorted(p.relative_to(self.root).as_posix() for p in self.staging.glob("*/*/SKILL.md"))
        (self.root / si.SHIPPED_MANIFEST).write_text("".join(f"{s}\n" for s in shipped), encoding="utf-8")
        for name in _PROVIDERS:
            self.add_stub(self.bin, name)

    @staticmethod
    def add_stub(bin_dir: pathlib.Path, name: str) -> None:
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)

    def path(self, bin_dir: pathlib.Path | None = None) -> str:
        dirs = [str(bin_dir or self.bin), str(pathlib.Path(sys.executable).parent), *_SYSTEM_DIRS]
        return os.pathsep.join(dirs)

    def run(
        self, *args: str, bin_dir: pathlib.Path | None = None, extra_env: dict | None = None
    ) -> subprocess.CompletedProcess:
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_env={
                "PATH": self.path(bin_dir),
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
                **(extra_env or {}),
            },
        )
        return subprocess.run(
            [str(_SCRIPT), *args],
            cwd=self.tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> Sandbox:
    return Sandbox(tmp_path)


def _snapshot(root: pathlib.Path, skip: tuple[pathlib.Path, ...] = ()) -> dict:
    """Every path under *root* (outside *skip*) with its bytes, mode and
    mtime, so a run that writes nothing there leaves an equal snapshot."""
    out = {}
    for path in sorted(root.rglob("*")):
        if any(path == s or s in path.parents for s in skip):
            continue
        st = path.lstat()
        out[path.relative_to(root).as_posix()] = (
            path.read_bytes() if path.is_file() else None,
            st.st_mode,
            st.st_mtime_ns,
        )
    return out


def _tree(root: pathlib.Path) -> dict:
    """Relative path -> (bytes, mode) for every file under *root*."""
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mode & 0o777)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _make_skill(parent: pathlib.Path, name: str) -> pathlib.Path:
    skill = parent / name
    (skill / "scripts").mkdir(parents=True)
    (skill / "references").mkdir()
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: An operator's own skill.\n---\n\n# {name}\n", encoding="utf-8"
    )
    (skill / "scripts" / "run.sh").write_text("#!/bin/sh\necho run\n", encoding="utf-8")
    (skill / "scripts" / "run.sh").chmod(0o755)
    (skill / "references" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    return skill


# ---------------------------------------------------------------------------
# Install, same, changed, forced
# ---------------------------------------------------------------------------


def test_install_into_named_homes_then_same_then_changed_left_then_forced(sandbox: Sandbox) -> None:
    t1, t2 = sandbox.tmp / "T1", sandbox.tmp / "T2"
    homes = ["--claude-home", str(t1), "--codex-home", str(t2)]
    outside = _snapshot(sandbox.tmp, skip=(t1, t2))

    first = sandbox.run(*homes)
    assert first.returncode == 0, diagnostic(first)
    for provider, home in (("claude", t1), ("codex", t2)):
        assert _tree(home / _SHIPPED) == _tree(
            sandbox.staging / provider / _SHIPPED
        ), f"{provider} copy is not the whole skill"
    assert _snapshot(sandbox.tmp, skip=(t1, t2)) == outside, "the install wrote outside the two homes it was given"

    installed = _snapshot(sandbox.tmp)
    again = sandbox.run(*homes)
    assert again.returncode == 0, diagnostic(again)
    assert again.stdout.count(f"  {_SHIPPED}: same") == 2, diagnostic(again)
    assert _snapshot(sandbox.tmp) == installed, "a second run wrote something"

    edited = t1 / _SHIPPED / "SKILL.md"
    edited.write_text("an operator edit\n", encoding="utf-8")
    left = sandbox.run(*homes)
    assert left.returncode == 3, diagnostic(left)
    assert edited.read_text(encoding="utf-8") == "an operator edit\n"
    contract = parse_error_contract(left.stderr)
    assert contract is not None and contract["error"] == "skills_install.changed_left", diagnostic(left)

    # The remedy is this verb with --force, runnable as printed.
    remedy = shlex.split(contract["remedy"])
    assert remedy[0] == str(_SCRIPT) and remedy[-1] == "--force", contract["remedy"]
    forced = sandbox.run(*remedy[1:])
    assert forced.returncode == 0, diagnostic(forced)
    assert f"  {_SHIPPED}: overwritten" in forced.stdout, diagnostic(forced)
    assert edited.read_bytes() == (sandbox.staging / "claude" / _SHIPPED / "SKILL.md").read_bytes()


def test_flat_dir_with_codex_provider_installs_into_the_codex_home_only(sandbox: Sandbox) -> None:
    flat = sandbox.tmp / "my-skills"
    skill = _make_skill(flat, "my-skill")
    t1, t2 = sandbox.tmp / "T1", sandbox.tmp / "T2"

    result = sandbox.run("--dir", str(flat), "--provider", "codex", "--claude-home", str(t1), "--codex-home", str(t2))
    assert result.returncode == 0, diagnostic(result)
    assert _tree(t2 / "my-skill") == _tree(skill)
    assert not t1.exists(), "the claude home was touched by a codex-only install"


# ---------------------------------------------------------------------------
# Missing provider CLI, dry run, list
# ---------------------------------------------------------------------------


def test_provider_whose_cli_is_not_on_path_is_skipped_with_one_notice(sandbox: Sandbox) -> None:
    codex_only = sandbox.tmp / "codex-only-bin"
    codex_only.mkdir()
    Sandbox.add_stub(codex_only, "codex")
    assert (
        shutil.which("claude", path=sandbox.path(codex_only)) is None
    ), f"a claude binary is on the narrowed PATH {sandbox.path(codex_only)}; this test needs it absent"

    result = sandbox.run(bin_dir=codex_only)
    assert result.returncode == 0, diagnostic(result)
    notices = [line for line in result.stderr.splitlines() if "skipping" in line]
    assert notices == ["skills: skipping claude: CLI binary not found on PATH"], diagnostic(result)
    assert not (sandbox.home / ".claude").exists()
    assert (sandbox.home / ".codex" / "skills" / _SHIPPED / "SKILL.md").is_file()


@pytest.mark.parametrize(
    "args", [["--dry-run"], ["--list"], ["--list", "--json"]], ids=["dry-run", "list", "list-json"]
)
def test_dry_run_and_list_write_nothing(sandbox: Sandbox, args: list[str]) -> None:
    before = _snapshot(sandbox.tmp)
    result = sandbox.run(*args)
    assert result.returncode == 0, diagnostic(result)
    assert _shipped_line_count(result.stdout, args) == 2, diagnostic(result)
    assert _snapshot(sandbox.tmp) == before, f"{' '.join(args)} wrote something"


def _shipped_line_count(stdout: str, args: list[str]) -> int:
    if "--json" in args:
        return sum(1 for p in json.loads(stdout)["providers"] for s in p["skills"] if s["name"] == _SHIPPED)
    if "--dry-run" in args:
        return stdout.count(f"  {_SHIPPED}: would install")
    return stdout.count(f"new      {_SHIPPED}  (shipped)")


def _golden_state(sandbox: Sandbox) -> list[str]:
    """Claude's shipped copy installed (same), codex's edited (changed), an
    operator-added claude skill (new) and an invalid one; returns the home
    flags."""
    t1, t2 = sandbox.tmp / "homes" / "claude", sandbox.tmp / "homes" / "codex"
    homes = ["--claude-home", str(t1), "--codex-home", str(t2)]
    assert sandbox.run(*homes).returncode == 0
    (t2 / _SHIPPED / "SKILL.md").write_text("an operator edit\n", encoding="utf-8")
    _make_skill(sandbox.staging / "claude", "my-skill")
    broken = sandbox.staging / "claude" / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("---\nname: broken\n---\n", encoding="utf-8")
    return homes


@pytest.mark.parametrize(
    "args,golden",
    [(["--list"], "install_skills_list.txt"), (["--list", "--json"], "install_skills_list.json")],
    ids=["text", "json"],
)
def test_list_output_matches_the_golden(sandbox: Sandbox, args: list[str], golden: str) -> None:
    homes = _golden_state(sandbox)
    result = sandbox.run(*args, *homes)
    assert result.returncode == 0, diagnostic(result)
    rendered = result.stdout.replace(str(sandbox.tmp), "$TMP")
    expected = (_GOLDEN_DIR / golden).read_text(encoding="utf-8")
    assert rendered == expected, f"output differs from {_GOLDEN_DIR / golden}:\n{rendered}"


# ---------------------------------------------------------------------------
# Help and usage errors
# ---------------------------------------------------------------------------


def test_help_lists_exactly_the_declared_flags(sandbox: Sandbox) -> None:
    result = sandbox.run("--help")
    assert result.returncode == 0, diagnostic(result)
    options = result.stdout.split("Options:\n", 1)[1].split("\n\n", 1)[0]
    listed = [line.split()[0].rstrip(",") for line in options.splitlines()]
    expected = [
        "--dir",
        "--provider",
        "--claude-home",
        "--codex-home",
        "--list",
        "--json",
        "--dry-run",
        "--force",
        "--help",
    ]
    assert listed == expected, options
    assert ["--" + f for f in load_manifest()["install-skills"].flags] == expected


@pytest.mark.parametrize(
    "args,error,wording",
    [
        (["--bogus"], "install_skills.unknown_argument", "install-skills.sh: unknown argument: --bogus"),
        (["stray"], "install_skills.unknown_argument", "install-skills.sh: unknown argument: stray"),
        (["--dir"], "install_skills.option_value_required", "--dir needs a value"),
        (["--provider", "gemini"], "install_skills.unknown_provider", "--provider gemini"),
        (["--json"], "install_skills.json_needs_list", "without --list"),
        (["--list", "--force"], "install_skills.list_is_read_only", "--list only reads"),
        (["--dir", "absent"], "skills_install.source_missing", "does not exist"),
    ],
    ids=[
        "unknown-flag",
        "positional",
        "missing-value",
        "unknown-provider",
        "json-without-list",
        "list-with-force",
        "missing-dir",
    ],
)
def test_usage_errors_exit_2_with_a_dual_channel_block(
    sandbox: Sandbox, args: list[str], error: str, wording: str
) -> None:
    before = _snapshot(sandbox.tmp)
    result = sandbox.run(*args)
    assert result.returncode == 2, diagnostic(result)
    contract = parse_error_contract(result.stderr)
    assert contract is not None and contract["error"] == error, diagnostic(result)
    assert wording in contract["message"], diagnostic(result)
    assert _snapshot(sandbox.tmp) == before


# ---------------------------------------------------------------------------
# Default homes and the shared function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("claude_config_dir", [None, "", "claude-config"], ids=["unset", "empty", "set"])
def test_default_homes_equal_the_python_resolver(sandbox: Sandbox, claude_config_dir: str | None) -> None:
    extra = {}
    if claude_config_dir is not None:
        extra["CLAUDE_CONFIG_DIR"] = str(sandbox.tmp / claude_config_dir) if claude_config_dir else ""
    result = sandbox.run("--list", "--json", extra_env=extra)
    assert result.returncode == 0, diagnostic(result)
    env = {"HOME": str(sandbox.home), **extra}
    listed = {p["provider"]: p["home"] for p in json.loads(result.stdout)["providers"]}
    assert listed == {p: str(si.provider_skills_home(p, env)) for p in _PROVIDERS}


def test_route_and_script_reach_the_shared_list_function(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(sandbox.home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    result = assert_route_cli_parity("install-skills", build_api_client(sandbox.root))
    assert result.response.status_code == 200, result.response.text
