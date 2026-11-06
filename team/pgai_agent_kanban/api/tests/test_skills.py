"""
test_skills.py — Tests for GET /skills.

Coverage:
  - A sandbox root staging the two shipped skills and one operator-added
    skill answers 200 with one entry per (provider, skill), each naming
    shipped or operator-added and its state (new, same, changed) in that
    provider's home.
  - The body, minus ``warnings``, is the skills module's own JSON list for
    the same staging directory and homes -- in process and from the
    module's ``list --json`` command line run by file path.
  - The route writes nothing in the staging directory or any home.
  - No staging directory: 200, every provider with no skills, nothing created.
  - No shipped-skills record: every skill is operator-added.
  - An invalid skill is listed with the rule it broke.
  - A staging directory the module refuses (skills directly under skills/)
    answers 422 with the module's dual-channel triple.
  - An unknown query parameter is a warning, not a failure.

Each test builds its kanban root under tmp_path and points HOME and
CLAUDE_CONFIG_DIR at temp directories, so the provider homes the route
resolves are never the real ~/.claude or ~/.codex.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from pgai_agent_kanban.lib import skills_install as si
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[3]
_REPO_ROOT = _TEAM_DIR.parent
_MODULE_FILE = _TEAM_DIR / "pgai_agent_kanban" / "lib" / "skills_install.py"
_SHIPPED = "pgai-agent-kanban"
_OPERATOR_SKILL = "my-skill"


def _skill_md(name: str, description: str = "An operator's own skill.") -> str:
    return f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\n\n# {name}\n"


def _snapshot(root: pathlib.Path) -> dict:
    """Every path under *root* with its bytes and mtime."""
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None, p.lstat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
    }


@pytest.fixture
def homes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Point the server's environment at temp provider homes."""
    user_home = tmp_path / "user-home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    return {
        "claude": tmp_path / "claude-config" / "skills",
        "codex": user_home / ".codex" / "skills",
    }


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A kanban root with no staging directory."""
    kanban_root = tmp_path / "kanban"
    kanban_root.mkdir()
    return kanban_root


def _stage(root: pathlib.Path) -> pathlib.Path:
    """Stage both shipped skills (copied from the repository) and one
    operator-added claude skill, and record the shipped set as install does."""
    staging = root / si.STAGING_DIR
    for prov in si.PROVIDERS:
        shutil.copytree(_REPO_ROOT / "skills" / prov / _SHIPPED, staging / prov / _SHIPPED)
    mine = staging / "claude" / _OPERATOR_SKILL
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text(_skill_md(_OPERATOR_SKILL), encoding="utf-8")
    (root / si.SHIPPED_MANIFEST).write_text(
        "".join(f"0000  skills/{prov}/{_SHIPPED}/SKILL.md\n" for prov in si.PROVIDERS),
        encoding="utf-8",
    )
    return staging


def _client(root: pathlib.Path) -> TestClient:
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    return TestClient(create_app(ApiConfig(host="127.0.0.1", port=0, kanban_root=root)))


def _get(root: pathlib.Path, url: str = "/skills"):
    with _client(root) as client:
        return client.get(url)


def _entries(body: dict) -> dict:
    """``{(provider, name): entry}`` over the whole body."""
    return {(p["provider"], s["name"]): s for p in body["providers"] for s in p["skills"]}


def _without_warnings(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "warnings"}


def test_staged_skills_list_per_provider_with_origin_and_home_state(root, homes):
    staging = _stage(root)
    # claude's shipped skill installed unchanged (same); codex's installed then
    # edited in the home (changed); the operator's skill not installed (new).
    si.install_skills(si.list_skills(staging, homes=homes, provider="all"))
    shutil.rmtree(homes["claude"] / _OPERATOR_SKILL)
    (homes["codex"] / _SHIPPED / "SKILL.md").write_text("edited in the home\n", encoding="utf-8")

    response = _get(root)

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == str(staging)
    assert [(p["provider"], p["home"]) for p in body["providers"]] == [
        ("claude", str(homes["claude"])),
        ("codex", str(homes["codex"])),
    ]
    assert {key: (e["origin"], e["state"]) for key, e in _entries(body).items()} == {
        ("claude", _OPERATOR_SKILL): ("operator-added", "new"),
        ("claude", _SHIPPED): ("shipped", "same"),
        ("codex", _SHIPPED): ("shipped", "changed"),
    }
    assert body["warnings"] == []


def test_body_is_the_skills_module_list_in_process_and_from_its_command_line(root, homes, tmp_path):
    staging = _stage(root)
    body = _get(root).json()

    in_process = si.list_skills(staging, homes=homes, shipped=si.read_shipped_manifest(root / si.SHIPPED_MANIFEST))
    assert _without_warnings(body) == json.loads(si.render_list_json(in_process))

    env = build_scrubbed_env(
        home_dir=homes["codex"].parent.parent,
        extra_env={"CLAUDE_CONFIG_DIR": str(homes["claude"].parent), "PGAI_AGENT_KANBAN_ROOT_PATH": str(root)},
    )
    cli = subprocess.run(
        [sys.executable, str(_MODULE_FILE), "list", "--json", "--shipped-manifest", str(root / si.SHIPPED_MANIFEST)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cli.returncode == 0, cli.stderr
    assert _without_warnings(body) == json.loads(cli.stdout)


def test_listing_writes_nothing_in_staging_or_homes(root, homes):
    staging = _stage(root)
    si.install_skills(si.list_skills(staging, homes=homes))
    before = (_snapshot(root), _snapshot(homes["claude"].parent), _snapshot(homes["codex"].parent.parent))

    assert _get(root).status_code == 200

    assert (_snapshot(root), _snapshot(homes["claude"].parent), _snapshot(homes["codex"].parent.parent)) == before


def test_no_staging_directory_is_an_empty_list_and_creates_nothing(root, homes, tmp_path):
    before = _snapshot(tmp_path)

    response = _get(root)

    assert response.status_code == 200
    body = response.json()
    assert [(p["provider"], p["home"], p["skills"]) for p in body["providers"]] == [
        ("claude", str(homes["claude"]), []),
        ("codex", str(homes["codex"]), []),
    ]
    assert body["warnings"] == []
    assert not (root / si.STAGING_DIR).exists()
    assert not homes["claude"].exists() and not homes["codex"].exists()
    assert _snapshot(tmp_path) == before


def test_without_a_shipped_record_every_skill_is_operator_added(root, homes):
    _stage(root)
    (root / si.SHIPPED_MANIFEST).unlink()

    body = _get(root).json()

    assert {e["origin"] for e in _entries(body).values()} == {"operator-added"}


def test_invalid_skill_is_listed_with_the_rule_it_broke(root, homes):
    broken = root / si.STAGING_DIR / "codex" / "broken"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("---\nname: broken\n---\n", encoding="utf-8")

    response = _get(root)

    assert response.status_code == 200
    entry = _entries(response.json())[("codex", "broken")]
    assert (entry["state"], entry["origin"], entry["error"]) == (
        "invalid",
        "operator-added",
        si.RULE_DESCRIPTION_MISSING,
    )
    assert entry["message"]


def test_skills_directly_under_staging_are_refused_with_the_module_triple(root, homes):
    flat = root / si.STAGING_DIR / _OPERATOR_SKILL
    flat.mkdir(parents=True)
    (flat / "SKILL.md").write_text(_skill_md(_OPERATOR_SKILL), encoding="utf-8")

    response = _get(root)

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "skills_install.flat_needs_one_provider"
    assert _OPERATOR_SKILL in body["message"]
    assert body["remedy"].strip()
    assert body["warnings"] == []


def test_unknown_query_parameter_is_a_warning_and_the_list_still_returns(root, homes):
    _stage(root)

    response = _get(root, "/skills?provider=codex")

    assert response.status_code == 200
    body = response.json()
    assert body["warnings"] == ["unknown parameter: provider"]
    assert [p["provider"] for p in body["providers"]] == ["claude", "codex"]
