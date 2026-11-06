"""
test_skills_install.py
======================
Interface tests for pgai_agent_kanban.lib.skills_install: the one module that
parses, compares, installs and lists provider CLI skills for the
install-skills verb, the upgrade's skill refresh and the operator API.

Coverage:
  - Each shipped skill in the repository passes the shipped-skill rules, and
    a fixture skill breaking each rule is refused naming that rule.
  - The claude and codex copies of the shipped skill are byte-identical and
    each parses; a one-byte difference in a sandbox copy fails that check.
  - Comparison reports new, same and changed over the whole skill directory.
  - Install copies whole directories, writes nothing on a second run, leaves a
    changed copy unless forced, and never removes what exists only in a home.
  - A flat source is accepted for exactly one provider and installs there only.
  - The JSON and text lists are pinned by goldens, in process and through the
    command line run by file path.
  - The skill-home resolver for claude (with and without CLAUDE_CONFIG_DIR)
    and codex.
  - The command line's exit codes, its changed-skill remedy, and the
    missing-CLI skip.

Every test builds its sources and provider homes under tmp_path; nothing
reads or writes a real ~/.claude, ~/.codex or kanban root.
"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import stat
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import skills_install as si
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.fixtures.upgrade_sandbox import diagnostic

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_MODULE_FILE = _TEAM_DIR / "pgai_agent_kanban" / "lib" / "skills_install.py"
_GOLDEN_DIR = _TEAM_DIR / "tests" / "fixtures" / "golden"
_SHIPPED_SKILLS = ("claude/pgai-agent-kanban", "codex/pgai-agent-kanban")


def _skill_md(name: str, description: str = "Does one thing well.", body: str = "# Body\n") -> str:
    return f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\n\n{body}"


def _make_skill(parent: pathlib.Path, name: str, *, text: str | None = None, extra: dict | None = None) -> pathlib.Path:
    """Create ``parent/name`` with a SKILL.md and optional extra files
    (``{relative path: content}``); ``scripts/`` files are made executable."""
    skill = parent / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(_skill_md(name) if text is None else text, encoding="utf-8")
    for rel, content in (extra or {}).items():
        path = skill / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if rel.startswith("scripts/"):
            path.chmod(0o755)
    return skill


_FULL_SKILL_FILES = {
    "scripts/run.sh": "#!/bin/sh\necho run\n",
    "references/guide.md": "# Guide\n",
    "assets/templates/note.txt": "note\n",
}


def _snapshot(root: pathlib.Path) -> dict:
    """Every path under *root* with its bytes, mode, inode and mtime, so a
    run that writes nothing leaves an equal snapshot."""
    out = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        data = path.read_bytes() if path.is_file() else None
        out[path.relative_to(root).as_posix()] = (data, st.st_mode, st.st_ino, st.st_mtime_ns)
    return out


def _homes(tmp_path: pathlib.Path) -> dict:
    return {"claude": tmp_path / "homes" / "claude", "codex": tmp_path / "homes" / "codex"}


# ---------------------------------------------------------------------------
# Parsing and the shipped-skill rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel", _SHIPPED_SKILLS)
def test_shipped_skill_passes_every_shipped_skill_rule(rel):
    skill_dir = _REPO_ROOT / "skills" / rel
    skill = si.validate_shipped_skill(skill_dir)
    assert skill.name == skill_dir.name
    assert skill.description.strip()
    assert len(skill.description) < 500


def _assert_skill_twins(skills_root: pathlib.Path, name: str) -> None:
    """Every provider's copy of skill *name* under *skills_root* (provider
    layout) has a SKILL.md byte-identical to the others, and each parses:
    its front-matter name equals its directory name and its description is
    non-empty and under 500 characters."""
    copies = [skills_root / provider / name / "SKILL.md" for provider in si.PROVIDERS]
    reference = copies[0].read_bytes()
    for copy in copies[1:]:
        assert copy.read_bytes() == reference, (
            f"{copy} is not byte-identical to {copies[0]}; the shipped twins are edited by hand "
            f"and must be kept the same: cp {copies[0]} {copy} (or the reverse)"
        )
    for copy in copies:
        front = si.parse_front_matter(copy.read_text(encoding="utf-8"))
        assert front.get("name") == copy.parent.name, f"{copy}: name {front.get('name')!r}"
        description = front.get("description")
        assert isinstance(description, str) and description.strip(), f"{copy}: empty description"
        assert len(description) < si.DESCRIPTION_LIMIT, f"{copy}: description is {len(description)} characters"


def test_shipped_skill_twins_are_byte_identical_and_each_parses():
    names = {rel.split("/", 1)[1] for rel in _SHIPPED_SKILLS}
    assert names == {"pgai-agent-kanban"}, names
    _assert_skill_twins(_REPO_ROOT / "skills", "pgai-agent-kanban")


def test_skill_twin_check_fails_on_a_one_byte_difference(tmp_path):
    name = "pgai-agent-kanban"
    for provider in si.PROVIDERS:
        copy = tmp_path / provider / name / "SKILL.md"
        copy.parent.mkdir(parents=True)
        copy.write_bytes((_REPO_ROOT / "skills" / provider / name / "SKILL.md").read_bytes())
    _assert_skill_twins(tmp_path, name)

    edited = tmp_path / si.PROVIDERS[-1] / name / "SKILL.md"
    data = bytearray(edited.read_bytes())
    data[-2] = ord("X") if data[-2] != ord("X") else ord("Y")
    edited.write_bytes(bytes(data))

    with pytest.raises(AssertionError, match="not byte-identical"):
        _assert_skill_twins(tmp_path, name)


_LONG = "x" * 500

_RULE_CASES = [
    (si.RULE_FRONT_MATTER_MISSING, "# A skill with no front matter\n", {}),
    (si.RULE_FRONT_MATTER_MISSING, "---\nname: demo\ndescription: unclosed\n# never closed\n", {}),
    (si.RULE_FRONT_MATTER_INVALID, "---\nname: [demo\n---\n", {}),
    (si.RULE_FRONT_MATTER_INVALID, "---\n- a list\n- not a mapping\n---\n", {}),
    (si.RULE_NAME_MISSING, "---\ndescription: Has no name.\n---\n", {}),
    (si.RULE_NAME_MISMATCH, _skill_md("another-name"), {}),
    (si.RULE_DESCRIPTION_MISSING, "---\nname: demo\n---\n", {}),
    (si.RULE_DESCRIPTION_MISSING, '---\nname: demo\ndescription: "  "\n---\n', {}),
    (si.RULE_DESCRIPTION_TOO_LONG, _skill_md("demo", _LONG), {}),
    (si.RULE_OPERATOR_LITERAL, _skill_md("demo", body="Run /home/alice/kanban/scripts/why.sh\n"), {}),
    (si.RULE_OPERATOR_LITERAL, _skill_md("demo", body="git clone git@github-alice:org/repo.git\n"), {}),
    (si.RULE_OPERATOR_LITERAL, _skill_md("demo"), {"references/setup.md": "cd /home/alice/develop\n"}),
]


@pytest.mark.parametrize(
    "rule,text,extra",
    _RULE_CASES,
    ids=[
        "no-front-matter",
        "unclosed-front-matter",
        "bad-yaml",
        "not-a-mapping",
        "no-name",
        "name-not-dir",
        "no-description",
        "blank-description",
        "description-500",
        "home-path",
        "ssh-host-alias",
        "home-path-in-references",
    ],
)
def test_shipped_skill_rule_violation_is_refused_naming_the_rule(tmp_path, rule, text, extra):
    skill = _make_skill(tmp_path, "demo", text=text, extra=extra)
    with pytest.raises(si.SkillInvalid) as caught:
        si.validate_shipped_skill(skill)
    assert caught.value.rule == rule
    assert rule in str(caught.value)
    assert caught.value.path == skill


def test_description_of_499_characters_is_accepted(tmp_path):
    skill = _make_skill(tmp_path, "demo", text=_skill_md("demo", "y" * 499))
    assert len(si.validate_shipped_skill(skill).description) == 499


def test_neutral_home_and_remote_forms_pass_the_hygiene_rule(tmp_path):
    body = (
        'source "$HOME/pgai_agent_kanban/shell-env"\n'
        "ls ~/pgai_agent_kanban /home/<user>/x /home/$USER/y\n"
        "git clone git@github.com:org/repo.git\n"
    )
    skill = _make_skill(tmp_path, "demo", text=_skill_md("demo", body=body))
    assert si.validate_shipped_skill(skill).name == "demo"


def test_operator_skill_is_not_held_to_the_hygiene_rule(tmp_path):
    skill = _make_skill(tmp_path, "mine", text=_skill_md("mine", body="cd /home/alice/work\n"))
    assert si.load_skill(skill).name == "mine"


def test_front_matter_accepts_block_scalars_and_trailing_whitespace():
    text = "﻿---  \nname: demo\ndescription: >\n  Folded\n  text.\n...\t\nbody\n"
    assert si.parse_front_matter(text) == {"name": "demo", "description": "Folded text."}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def test_compare_absent_copy_is_new(tmp_path):
    source = _make_skill(tmp_path / "src", "demo", extra=_FULL_SKILL_FILES)
    assert si.compare_skill_dir(source, tmp_path / "home" / "demo") == si.STATE_NEW


def test_compare_byte_identical_whole_directory_is_same(tmp_path):
    source = _make_skill(tmp_path / "src", "demo", extra=_FULL_SKILL_FILES)
    installed = _make_skill(tmp_path / "home", "demo", extra=_FULL_SKILL_FILES)
    assert si.compare_skill_dir(source, installed) == si.STATE_SAME


@pytest.mark.parametrize(
    "rel,edit",
    [
        ("SKILL.md", "change"),
        ("scripts/run.sh", "change"),
        ("references/guide.md", "change"),
        ("assets/templates/note.txt", "change"),
        ("references/guide.md", "remove"),
    ],
)
def test_compare_any_differing_or_missing_file_is_changed(tmp_path, rel, edit):
    source = _make_skill(tmp_path / "src", "demo", extra=_FULL_SKILL_FILES)
    installed = _make_skill(tmp_path / "home", "demo", extra=_FULL_SKILL_FILES)
    if edit == "change":
        (installed / rel).write_text("edited by the operator\n", encoding="utf-8")
    else:
        (installed / rel).unlink()
    assert si.compare_skill_dir(source, installed) == si.STATE_CHANGED


def test_compare_file_only_in_the_home_copy_is_not_a_difference(tmp_path):
    source = _make_skill(tmp_path / "src", "demo", extra=_FULL_SKILL_FILES)
    installed = _make_skill(tmp_path / "home", "demo", extra={**_FULL_SKILL_FILES, "notes/mine.md": "mine\n"})
    assert si.compare_skill_dir(source, installed) == si.STATE_SAME


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def _staging(tmp_path: pathlib.Path) -> pathlib.Path:
    staging = tmp_path / "staging"
    for prov in si.PROVIDERS:
        _make_skill(staging / prov, "pgai-agent-kanban", extra=_FULL_SKILL_FILES)
    return staging


def _install(staging, homes, **kwargs):
    force = kwargs.pop("force", False)
    dry_run = kwargs.pop("dry_run", False)
    return si.install_skills(si.list_skills(staging, homes=homes, **kwargs), force=force, dry_run=dry_run)


def test_install_copies_the_whole_skill_directory_into_each_home(tmp_path):
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    report = _install(staging, homes)
    assert [o.action for o in report.outcomes] == [si.ACTION_INSTALLED, si.ACTION_INSTALLED]
    assert report.exit_code == si.EXIT_OK
    for prov in si.PROVIDERS:
        source = staging / prov / "pgai-agent-kanban"
        installed = homes[prov] / "pgai-agent-kanban"
        for rel in ["SKILL.md", *_FULL_SKILL_FILES]:
            assert (installed / rel).read_bytes() == (source / rel).read_bytes(), rel
        assert (installed / "scripts" / "run.sh").stat().st_mode & stat.S_IXUSR
        assert si.compare_skill_dir(source, installed) == si.STATE_SAME


def test_second_install_reports_same_for_every_skill_and_writes_nothing(tmp_path):
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    _install(staging, homes)
    before = _snapshot(tmp_path / "homes")
    listing = si.list_skills(staging, homes=homes)
    assert {e.state for p in listing.providers for e in p.entries} == {si.STATE_SAME}
    report = si.install_skills(listing, force=True)
    assert {o.action for o in report.outcomes} == {si.ACTION_UNCHANGED}
    assert _snapshot(tmp_path / "homes") == before


def test_changed_copy_is_left_and_reported_unless_forced(tmp_path):
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    _install(staging, homes)
    edited = homes["claude"] / "pgai-agent-kanban" / "scripts" / "run.sh"
    edited.write_text("#!/bin/sh\necho operator edit\n", encoding="utf-8")
    before = _snapshot(tmp_path / "homes")

    report = _install(staging, homes)
    assert [(o.entry.provider, o.action) for o in report.left_changed] == [("claude", si.ACTION_LEFT_CHANGED)]
    assert report.left_changed[0].written == ("scripts/run.sh",)
    assert report.exit_code == si.EXIT_CHANGED_LEFT
    assert _snapshot(tmp_path / "homes") == before

    report = _install(staging, homes, force=True)
    assert [(o.entry.provider, o.action) for o in report.outcomes] == [
        ("claude", si.ACTION_OVERWRITTEN),
        ("codex", si.ACTION_UNCHANGED),
    ]
    assert report.exit_code == si.EXIT_OK
    assert edited.read_bytes() == (staging / "claude" / "pgai-agent-kanban" / "scripts" / "run.sh").read_bytes()


def test_install_never_removes_what_exists_only_in_a_home(tmp_path):
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    own_skill = _make_skill(homes["claude"], "operator-own", extra={"scripts/x.sh": "x\n"})
    _install(staging, homes)
    own_note = homes["claude"] / "pgai-agent-kanban" / "notes" / "mine.md"
    own_note.parent.mkdir()
    own_note.write_text("mine\n", encoding="utf-8")
    (homes["claude"] / "pgai-agent-kanban" / "SKILL.md").write_text("edited\n", encoding="utf-8")
    for force in (False, True):
        _install(staging, homes, force=force)
        assert own_note.read_text(encoding="utf-8") == "mine\n"
        assert (own_skill / "SKILL.md").is_file() and (own_skill / "scripts" / "x.sh").is_file()


def test_dry_run_reports_the_plan_and_writes_nothing(tmp_path):
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    report = _install(staging, homes, dry_run=True)
    assert [o.action for o in report.outcomes] == [si.ACTION_INSTALLED, si.ACTION_INSTALLED]
    assert report.outcomes[0].written == (
        "SKILL.md",
        "assets/templates/note.txt",
        "references/guide.md",
        "scripts/run.sh",
    )
    assert not (tmp_path / "homes").exists()


def test_invalid_source_skill_is_listed_with_its_rule_and_never_installed(tmp_path):
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    _make_skill(staging / "claude", "broken", text="no front matter\n")
    report = _install(staging, homes, provider="claude")
    broken = [o for o in report.outcomes if o.entry.name == "broken"]
    assert [(o.action, o.entry.rule) for o in broken] == [(si.ACTION_SKIPPED_INVALID, si.RULE_FRONT_MATTER_MISSING)]
    assert not (homes["claude"] / "broken").exists()
    assert report.exit_code == si.EXIT_OK


def test_provider_layout_with_one_provider_installs_into_that_home_only(tmp_path):
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    _install(staging, homes, provider="claude")
    assert (homes["claude"] / "pgai-agent-kanban" / "SKILL.md").is_file()
    assert not homes["codex"].exists()


# ---------------------------------------------------------------------------
# Flat source
# ---------------------------------------------------------------------------


def test_flat_source_with_one_provider_installs_into_that_home_only(tmp_path):
    flat, homes = tmp_path / "flat", _homes(tmp_path)
    _make_skill(flat, "alpha", extra=_FULL_SKILL_FILES)
    _make_skill(flat, "beta")
    report = _install(flat, homes, provider="codex")
    assert [(o.entry.provider, o.entry.name, o.action) for o in report.outcomes] == [
        ("codex", "alpha", si.ACTION_INSTALLED),
        ("codex", "beta", si.ACTION_INSTALLED),
    ]
    assert (homes["codex"] / "alpha" / "scripts" / "run.sh").is_file()
    assert (homes["codex"] / "beta" / "SKILL.md").is_file()
    assert not homes["claude"].exists()


def test_flat_source_without_exactly_one_provider_is_a_usage_error(tmp_path):
    flat = tmp_path / "flat"
    _make_skill(flat, "alpha")
    with pytest.raises(si.SkillsUsageError) as caught:
        si.list_skills(flat, provider="all", homes=_homes(tmp_path))
    assert caught.value.error == "skills_install.flat_needs_one_provider"
    assert "--provider claude" in caught.value.remedy


def test_missing_source_directory_is_a_usage_error(tmp_path):
    with pytest.raises(si.SkillsUsageError) as caught:
        si.list_skills(tmp_path / "absent", homes=_homes(tmp_path))
    assert caught.value.error == "skills_install.source_missing"


def test_missing_source_with_missing_ok_lists_every_provider_empty_and_creates_nothing(tmp_path):
    homes = _homes(tmp_path)
    before = _snapshot(tmp_path)
    listing = si.list_skills(tmp_path / "absent", homes=homes, missing_ok=True)
    assert [(p.provider, p.home, p.entries) for p in listing.providers] == [
        ("claude", homes["claude"], ()),
        ("codex", homes["codex"], ()),
    ]
    assert _snapshot(tmp_path) == before


def test_missing_ok_still_refuses_a_source_that_is_not_a_directory(tmp_path):
    (tmp_path / "file").write_text("x\n", encoding="utf-8")
    with pytest.raises(si.SkillsUsageError) as caught:
        si.list_skills(tmp_path / "file", homes=_homes(tmp_path), missing_ok=True)
    assert caught.value.error == "skills_install.source_missing"


# ---------------------------------------------------------------------------
# Shipped set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        ("claude/pgai-agent-kanban", {"claude": {"pgai-agent-kanban"}}),
        ("skills/codex/pgai-agent-kanban/SKILL.md", {"codex": {"pgai-agent-kanban"}}),
        ("0123abcd  skills/claude/pgai-agent-kanban/SKILL.md", {"claude": {"pgai-agent-kanban"}}),
        ("pgai-agent-kanban", {"*": {"pgai-agent-kanban"}}),
        ("  # a comment", {}),
    ],
)
def test_shipped_manifest_reads_each_line_shape(tmp_path, line, expected):
    manifest = tmp_path / "shipped.txt"
    manifest.write_text(f"\n{line}\n", encoding="utf-8")
    assert si.read_shipped_manifest(manifest) == {k: frozenset(v) for k, v in expected.items()}


def test_absent_shipped_manifest_is_an_empty_set_only_with_missing_ok(tmp_path):
    assert si.read_shipped_manifest(tmp_path / si.SHIPPED_MANIFEST, missing_ok=True) == {}
    with pytest.raises(si.SkillsUsageError) as caught:
        si.read_shipped_manifest(tmp_path / si.SHIPPED_MANIFEST)
    assert caught.value.error == "skills_install.shipped_manifest_unreadable"


# ---------------------------------------------------------------------------
# List output goldens
# ---------------------------------------------------------------------------


def _golden_tree(tmp_path: pathlib.Path) -> tuple[pathlib.Path, dict, pathlib.Path]:
    """Staging with one skill in each state per provider, plus a shipped
    manifest naming the kanban skill."""
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    _install(staging, homes)
    (homes["codex"] / "pgai-agent-kanban" / "references" / "guide.md").write_text("edited\n", encoding="utf-8")
    _make_skill(staging / "claude", "my-skill")
    _make_skill(staging / "claude", "broken", text="---\nname: broken\n---\n")
    manifest = tmp_path / "shipped.txt"
    manifest.write_text("claude/pgai-agent-kanban\ncodex/pgai-agent-kanban\n", encoding="utf-8")
    return staging, homes, manifest


def _golden(name: str, tmp_path: pathlib.Path, text: str) -> None:
    rendered = text.replace(str(tmp_path), "$TMP")
    expected = (_GOLDEN_DIR / name).read_text(encoding="utf-8")
    assert rendered == expected, f"rendered list differs from {_GOLDEN_DIR / name}:\n{rendered}"


def test_list_json_matches_the_golden_and_is_key_ordered(tmp_path):
    staging, homes, manifest = _golden_tree(tmp_path)
    listing = si.list_skills(staging, homes=homes, shipped=si.read_shipped_manifest(manifest))
    rendered = si.render_list_json(listing)
    _golden("skills_list.json", tmp_path, rendered)
    assert rendered == json.dumps(json.loads(rendered), indent=2, sort_keys=True) + "\n"
    assert rendered == si.render_list_json(
        si.list_skills(staging, homes=homes, shipped=si.read_shipped_manifest(manifest))
    )


def test_list_text_matches_the_golden(tmp_path):
    staging, homes, manifest = _golden_tree(tmp_path)
    listing = si.list_skills(staging, homes=homes, shipped=si.read_shipped_manifest(manifest))
    _golden("skills_list.txt", tmp_path, "\n".join(si.render_list_text(listing)) + "\n")


def test_list_without_a_shipped_set_carries_no_origin(tmp_path):
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    data = json.loads(si.render_list_json(si.list_skills(staging, homes=homes)))
    assert [sorted(s) for p in data["providers"] for s in p["skills"]] == [["name", "state"], ["name", "state"]]


# ---------------------------------------------------------------------------
# Skill-home resolver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,env,expected",
    [
        ("claude", {"HOME": "/h", "CLAUDE_CONFIG_DIR": "/cfg/claude"}, "/cfg/claude/skills"),
        ("claude", {"HOME": "/h"}, "/h/.claude/skills"),
        ("claude", {"HOME": "/h", "CLAUDE_CONFIG_DIR": ""}, "/h/.claude/skills"),
        ("codex", {"HOME": "/h", "CLAUDE_CONFIG_DIR": "/cfg/claude"}, "/h/.codex/skills"),
    ],
    ids=["claude-config-dir", "claude-default", "claude-config-dir-empty", "codex"],
)
def test_skill_home_resolver(provider, env, expected):
    assert si.provider_skills_home(provider, env) == pathlib.Path(expected)


def test_skill_home_resolver_reads_the_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert si.provider_skills_home("claude") == tmp_path / ".claude" / "skills"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert si.provider_skills_home("claude") == tmp_path / "cfg" / "skills"
    assert si.provider_skills_home("codex") == tmp_path / ".codex" / "skills"


# ---------------------------------------------------------------------------
# Command line (run by file path, scrubbed environment)
# ---------------------------------------------------------------------------


def _run(tmp_path: pathlib.Path, *args: str, path_dirs=None) -> subprocess.CompletedProcess:
    home = tmp_path / "user-home"
    home.mkdir(exist_ok=True)
    extra_env = {"PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path / "kanban")}
    if path_dirs is not None:
        extra_env["PATH"] = os.pathsep.join(str(d) for d in path_dirs)
    env = build_scrubbed_env(home_dir=home, extra_env=extra_env)
    return subprocess.run(
        [sys.executable, str(_MODULE_FILE), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _home_args(homes: dict) -> list[str]:
    return ["--claude-home", str(homes["claude"]), "--codex-home", str(homes["codex"])]


def test_cli_list_json_matches_the_golden(tmp_path):
    staging, homes, manifest = _golden_tree(tmp_path)
    result = _run(
        tmp_path, "list", "--json", "--dir", str(staging), *_home_args(homes), "--shipped-manifest", str(manifest)
    )
    assert result.returncode == 0, diagnostic(result)
    _golden("skills_list.json", tmp_path, result.stdout)


def test_cli_install_exit_codes_and_changed_skill_remedy(tmp_path):
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    args = ["install", "--dir", str(staging), *_home_args(homes)]
    first = _run(tmp_path, *args)
    assert first.returncode == 0, diagnostic(first)
    again = _run(tmp_path, *args)
    assert again.returncode == 0, diagnostic(again)
    assert "pgai-agent-kanban: same" in again.stdout, diagnostic(again)

    edited = homes["claude"] / "pgai-agent-kanban" / "SKILL.md"
    edited.write_text("operator edit\n", encoding="utf-8")
    left = _run(tmp_path, *args)
    assert left.returncode == 3, diagnostic(left)
    assert edited.read_text(encoding="utf-8") == "operator edit\n"
    contract = parse_error_contract(left.stderr)
    assert contract is not None and contract["error"] == "skills_install.changed_left", diagnostic(left)

    # The remedy is runnable as printed and clears the refusal.
    remedy = shlex.split(contract["remedy"])
    assert remedy[0] == "python3" and remedy[-1] == "--force", contract["remedy"]
    forced = _run(tmp_path, *remedy[2:])
    assert forced.returncode == 0, diagnostic(forced)
    assert edited.read_bytes() == (staging / "claude" / "pgai-agent-kanban" / "SKILL.md").read_bytes()


def test_cli_rerun_command_names_the_callers_remedy(tmp_path):
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    _install(staging, homes)
    (homes["codex"] / "pgai-agent-kanban" / "SKILL.md").write_text("edit\n", encoding="utf-8")
    result = _run(
        tmp_path, "install", "--dir", str(staging), *_home_args(homes), "--rerun-command", "scripts/install-skills.sh"
    )
    assert result.returncode == 3, diagnostic(result)
    assert parse_error_contract(result.stderr)["remedy"] == "scripts/install-skills.sh --force", diagnostic(result)


@pytest.mark.parametrize(
    "args",
    [
        ["install", "--dir", "absent"],
        ["list", "--unknown-flag"],
        ["install", "--provider", "gemini"],
        [],
    ],
    ids=["missing-source", "unknown-flag", "unknown-provider", "no-verb"],
)
def test_cli_usage_errors_exit_2(tmp_path, args):
    result = _run(tmp_path, *args)
    assert result.returncode == 2, diagnostic(result)


def test_cli_function_named_verbs_are_list_and_install(tmp_path):
    staging, homes, manifest = _golden_tree(tmp_path)
    source = ["--dir", str(staging), *_home_args(homes), "--shipped-manifest", str(manifest)]
    by_alias = _run(tmp_path, "list_skills", "--json", *source)
    assert by_alias.returncode == 0, diagnostic(by_alias)
    assert by_alias.stdout == _run(tmp_path, "list", "--json", *source).stdout

    fresh = tmp_path / "fresh-homes"
    installed = _run(
        tmp_path, "install_skills", "--dir", str(staging), "--provider", "codex", "--codex-home", str(fresh)
    )
    assert installed.returncode == 0, diagnostic(installed)
    assert (fresh / "pgai-agent-kanban" / "references" / "guide.md").is_file()


def test_cli_missing_ok_reads_absent_source_and_manifest_as_empty(tmp_path):
    absent = ["--dir", str(tmp_path / "no-skills"), "--shipped-manifest", str(tmp_path / "no-record")]
    listed = _run(tmp_path, "list", "--json", *absent, *_home_args(_homes(tmp_path)), "--missing-ok")
    assert listed.returncode == 0, diagnostic(listed)
    assert [(p["provider"], p["skills"]) for p in json.loads(listed.stdout)["providers"]] == [
        ("claude", []),
        ("codex", []),
    ]
    assert not (tmp_path / "no-skills").exists() and not (tmp_path / "homes").exists()

    staging = _staging(tmp_path)
    no_record = _run(tmp_path, "list", "--json", "--dir", str(staging), absent[2], absent[3], "--missing-ok")
    assert no_record.returncode == 0, diagnostic(no_record)
    assert {s["origin"] for p in json.loads(no_record.stdout)["providers"] for s in p["skills"]} == {si.ORIGIN_OPERATOR}

    refused = _run(tmp_path, "list", "--dir", str(staging), absent[2], absent[3])
    assert refused.returncode == 2, diagnostic(refused)
    assert parse_error_contract(refused.stderr)["error"] == "skills_install.shipped_manifest_unreadable"


def test_cli_default_source_is_the_kanban_root_skills_directory(tmp_path):
    root_skills = tmp_path / "kanban" / "skills"
    _make_skill(root_skills / "codex", "rooted")
    result = _run(tmp_path, "list", "--json", "--provider", "codex", "--codex-home", str(tmp_path / "h"))
    assert result.returncode == 0, diagnostic(result)
    assert json.loads(result.stdout)["source"] == str(root_skills)


def test_cli_skips_a_provider_whose_binary_is_not_on_path(tmp_path):
    staging, homes = _staging(tmp_path), _homes(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "codex").chmod(0o755)
    result = _run(
        tmp_path, "install", "--dir", str(staging), *_home_args(homes), "--skip-missing-cli", path_dirs=[bin_dir]
    )
    assert result.returncode == 0, diagnostic(result)
    assert result.stderr.count("CLI binary not found on PATH") == 1, diagnostic(result)
    assert "skipping claude" in result.stderr, diagnostic(result)
    assert not homes["claude"].exists()
    assert (homes["codex"] / "pgai-agent-kanban" / "SKILL.md").is_file()


def test_cli_home_prints_the_resolved_skill_home(tmp_path):
    result = _run(tmp_path, "home", "codex")
    assert result.returncode == 0, diagnostic(result)
    assert result.stdout.strip() == str(tmp_path / "user-home" / ".codex" / "skills")
