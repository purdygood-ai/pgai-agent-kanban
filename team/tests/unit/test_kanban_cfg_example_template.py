"""
test_kanban_cfg_example_template.py
===================================
Checks that kanban.cfg_example -- the file install.sh copies verbatim as a
fresh install's kanban.cfg -- states what the code actually does.

Covered:
  - Keys with no in-code fallback ([governance] budget_bytes_*,
    [release] max_rc_retries) state a "Shipped value:", never a bare
    "Default:" the code does not apply.
  - The comments that name a consequence an operator needs: max_project_seconds
    defaults to max_runtime_seconds, an invalid llm_thinking_enabled fails,
    [paths] dev_tree_path is the last-resort dev tree after project.cfg and
    PGAI_DEV_TREE_PATH, and the ai_auth_mode block has no orphaned line.
  - The commented ship_posture_confirm_remote line shows its default (false).
  - No line carries a release-version tag.
  - [release] carries a commented deep_verify_offhours_window at its default.
  - [models.claude] / [models.codex] ship a provider default with every
    per-role key empty, and the comments give the resolution order and a
    per-role example.
  - A fresh root whose kanban.cfg is the template resolves the provider
    default for every role through config_loader.sh's load_config (the
    resolver the wake uses), for both providers.

All fixture roots live under tmp_path; the live kanban root is never read
or written.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_TEMPLATE = _REPO_ROOT / "kanban.cfg_example"
_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"

_ROLES = ("pm", "coder", "writer", "tester", "cm", "po")
_PROVIDER_DEFAULTS = {"claude": "claude-sonnet-5-5", "codex": "gpt-6.1-sol"}

_SECTION_RE = re.compile(r"^\[\s*([^\]]+?)\s*\]")
# An active "key = value" line or a commented "# key = value" example line.
_KEY_RE = re.compile(r"^(?P<commented>#\s*)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*$")
_VERSION_TAG_RE = re.compile(r"\bv[0-9]+\.[0-9]+(\.[0-9]+)?\b")


def _template_lines() -> list[str]:
    return _TEMPLATE.read_text(encoding="utf-8").splitlines()


def _find_key(section: str, key: str, *, commented: bool | None = None) -> tuple[int, str]:
    """Return (line index, value) of *key* under [*section*].

    *commented* None accepts either form; True/False require that form.
    """
    current = ""
    for index, line in enumerate(_template_lines()):
        header = _SECTION_RE.match(line)
        if header:
            current = header.group(1)
            continue
        if current != section:
            continue
        match = _KEY_RE.match(line)
        if not match or match.group("key") != key:
            continue
        is_commented = match.group("commented") is not None
        if commented is None or commented == is_commented:
            return index, match.group("value")
    raise AssertionError(f"[{section}] {key} not found in {_TEMPLATE}")


def _comment_block_above(index: int) -> list[str]:
    """The contiguous '#' comment lines directly above line *index*."""
    lines = _template_lines()
    block: list[str] = []
    cursor = index - 1
    while cursor >= 0 and lines[cursor].lstrip().startswith("#"):
        block.insert(0, lines[cursor])
        cursor -= 1
    return block


def _section_lines(section: str) -> list[str]:
    lines = _template_lines()
    out: list[str] = []
    inside = False
    for line in lines:
        header = _SECTION_RE.match(line)
        if header:
            inside = header.group(1) == section
            continue
        if inside:
            out.append(line)
    return out


# ---------------------------------------------------------------------------
# Stated defaults match the code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "section, key",
    [("governance", f"budget_bytes_{role}") for role in ("coder", "tester", "po", "pm", "writer", "cm", "overwatch")]
    + [("release", "max_rc_retries")],
)
def test_key_without_in_code_fallback_states_a_shipped_value_not_a_default(section: str, key: str) -> None:
    index, _ = _find_key(section, key, commented=False)
    block = _comment_block_above(index)
    assert not any(re.match(r"^#\s*Default:", line) for line in block), (
        f"[{section}] {key} has no in-code fallback, so its comment must not carry a bare "
        f"'Default:' line:\n" + "\n".join(block)
    )
    assert any("Shipped value:" in line for line in block), "\n".join(block)


def test_max_project_seconds_default_names_max_runtime_seconds() -> None:
    index, _ = _find_key("wake", "max_project_seconds", commented=False)
    default_lines = [line for line in _comment_block_above(index) if re.match(r"^#\s*Default:", line)]
    assert default_lines and "max_runtime_seconds" in default_lines[-1], default_lines


def test_llm_thinking_enabled_comment_states_an_invalid_value_fails() -> None:
    index, _ = _find_key("providers", "llm_thinking_enabled", commented=False)
    text = " ".join(_comment_block_above(index))
    assert re.search(r"any other value fails", text), text
    assert "Claude provider only" in text, text


def test_dev_tree_path_comment_names_its_fallback_role() -> None:
    index, _ = _find_key("paths", "dev_tree_path", commented=False)
    text = "\n".join(_comment_block_above(index))
    assert "PGAI_DEV_TREE_PATH" in text, text
    assert "project.cfg" in text, text


def test_ai_auth_mode_comment_block_has_no_orphaned_continuation_line() -> None:
    """Every unindented comment line ('# Word') in the block starts a
    sentence or a 'Label:' -- a line opening on a lowercase word continues
    a sentence that is not there."""
    index, value = _find_key("providers", "ai_auth_mode", commented=False)
    assert value == "oauth"
    block = _comment_block_above(index)
    for line in block:
        text = line[1:]
        if text.startswith(" ") and not text.startswith("  ") and text.strip():
            assert text.strip()[0].isupper(), f"orphaned line in the ai_auth_mode block: {line!r}"
    assert any("Accepted: oauth|apikey" in line for line in block), block


def test_ship_posture_confirm_remote_example_line_shows_its_default() -> None:
    _, value = _find_key("release", "ship_posture_confirm_remote", commented=True)
    assert value == "false"


def test_no_line_carries_a_release_version_tag() -> None:
    offenders = [
        f"{number}: {line}"
        for number, line in enumerate(_template_lines(), start=1)
        if _VERSION_TAG_RE.search(line)
    ]
    assert not offenders, "version tags describe history, not behaviour:\n" + "\n".join(offenders)


def test_release_section_carries_a_commented_offhours_window_at_its_default() -> None:
    index, value = _find_key("release", "deep_verify_offhours_window", commented=True)
    assert _template_lines()[index] == "# deep_verify_offhours_window = 03:00-03:59"
    assert value == "03:00-03:59"
    text = " ".join(_comment_block_above(index))
    assert "project.cfg" in text and "03:00-03:59" in text, text


def test_release_deep_verify_comment_states_the_kanban_wide_precedence() -> None:
    index, value = _find_key("release", "deep_verify", commented=False)
    assert value == "weekly"
    text = " ".join(_comment_block_above(index))
    assert "project.cfg" in text and "Set off here" in text, text


# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider, model", sorted(_PROVIDER_DEFAULTS.items()))
def test_models_section_ships_a_default_and_empty_per_role_keys(provider: str, model: str) -> None:
    section = f"models.{provider}"
    _, default = _find_key(section, "default", commented=False)
    assert default == model
    for role in _ROLES:
        _, value = _find_key(section, role, commented=False)
        assert value == "", f"[{section}] {role} must ship empty, found {value!r}"
    comments = "\n".join(line for line in _section_lines(section) if line.startswith("#"))
    assert "Default: empty (provider default applies)" in comments, comments
    assert re.search(r"Example \(per role\): \w+ = \S+", comments), comments


def test_models_header_gives_the_resolution_order_and_per_role_examples() -> None:
    text = _TEMPLATE.read_text(encoding="utf-8")
    header_start = text.index("# [models.<provider>]")
    header = text[header_start : text.index("[models.claude]", header_start)]
    order = [header.index(marker) for marker in ("Model Override", "role's own key", "default key", "provider CLI's own default")]
    assert order == sorted(order), header
    assert "All keys ship empty" not in text
    claude_comments = "\n".join(_section_lines("models.claude"))
    codex_comments = "\n".join(_section_lines("models.codex"))
    assert "claude-opus-5-5" in claude_comments
    assert "gpt-6.1-sol" in codex_comments


def _fresh_root_with_template(tmp_path: pathlib.Path, provider: str) -> pathlib.Path:
    """A throwaway kanban root whose kanban.cfg is the template, as a fresh
    install lays it down, with [providers] active set to *provider*."""
    root = tmp_path / f"root-{provider}"
    root.mkdir()
    text = _TEMPLATE.read_text(encoding="utf-8")
    text, count = re.subn(r"(?m)^active = \w+$", f"active = {provider}", text, count=1)
    assert count == 1, "the template's [providers] active line moved"
    (root / "kanban.cfg").write_text(text, encoding="utf-8")
    return root


@pytest.mark.parametrize("provider", sorted(_PROVIDER_DEFAULTS))
def test_fresh_install_resolves_the_provider_default_model_for_every_role(
    tmp_path: pathlib.Path, provider: str
) -> None:
    root = _fresh_root_with_template(tmp_path, provider)
    env = {k: v for k, v in os.environ.items() if not re.fullmatch(r"PGAI_[A-Z]+_MODEL", k)}
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT"):
        env.pop(name, None)
    scratch = root / "tmp"
    scratch.mkdir()
    env.update(
        KANBAN_ROOT=str(root),
        PGAI_AGENT_KANBAN_ROOT_PATH=str(root),
        PGAI_AGENT_KANBAN_TEMP_DIR=str(scratch),
    )
    echoes = " && ".join(f'echo "{role}=${{PGAI_{role.upper()}_MODEL:-}}"' for role in _ROLES)
    script = f"source {str(_CONFIG_LOADER)!r} && load_config {str(root / 'kanban.cfg')!r} && {echoes}"

    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)

    assert result.returncode == 0, f"load_config failed on the template: {result.stderr!r}"
    for role in _ROLES:
        assert f"{role}={_PROVIDER_DEFAULTS[provider]}" in result.stdout.splitlines(), (
            f"{provider}: {role} did not resolve to the provider default; stdout: {result.stdout!r}"
        )
