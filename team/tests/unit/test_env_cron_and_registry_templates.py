"""
test_env_cron_and_registry_templates.py
=======================================
The install templates document what the code does, and the two readers of a
project's priority and row limit agree with each other.

  (1) Every variable or field the projects.cfg, shell-env, claude-env,
      secrets, pseudocron and crontab templates set has a comment block that
      states its default ("Default:" or "No default").
  (2) No template other than secrets_example carries an ANTHROPIC_API_KEY=
      or OPENAI_API_KEY= assignment, commented or not.
  (3) No commented assignment in those templates uses an angle-bracket
      placeholder as its value (uncommenting it as-is must never install a
      literal "<...>").
  (4) projects.cfg_example registers no project when copied: it has no
      active [project:...] section.
  (5) A projects.cfg priority that is not a plain integer (priority=abc)
      orders that project last, identically in the wake loop's bash list
      (projects_cfg_list) and the Python registry (ini.projects_registry).
  (6) The REST board's per-project row limit is the tmux dashboard's: the
      projects.cfg value clamped to 5..100, else kanban.cfg [dashboard]
      max_rows.
  (7) cron-suggested.template.txt, rendered with the recipe its own header
      documents, leaves no __PLACEHOLDER__, calls wake-batch.sh only in its
      --agent= form, and schedules the OVERWATCH sweep; every pseudocron
      tier template schedules the OVERWATCH sweep and the OVERWATCH wake.

Every fixture lives under tmp_path; the templates are read, never installed.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

from pgai_agent_kanban.lib import ini

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO = _TEAM_DIR.parent
_INSTALL = _TEAM_DIR / "templates" / "install"
_TIERS = ("small", "medium", "large")

#: template -> the variables / fields whose comment block must state a default.
_DOCUMENTED: dict[pathlib.Path, tuple[str, ...]] = {
    _REPO / "projects.cfg_example": ("priority", "description", "dashboard_color", "dashboard_max_rows"),
    _REPO / "shell-env_example": ("PGAI_AGENT_KANBAN_ROOT_PATH", "KANBAN_ROOT", "PATH"),
    _REPO / "claude-env_example": ("BASH_MAX_TIMEOUT_MS",),
    _REPO / "secrets_example": ("AI_AUTH_MODE", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"),
    _TEAM_DIR / "scripts" / "pseudocron.env.example": ("PGAI_AGENT_KANBAN_ROOT_PATH", "PATH", "HOME", "PGAI_CODER_MODEL"),
    _TEAM_DIR / "scripts" / "cron-suggested.template.txt": ("PGAI_AGENT_KANBAN_ROOT_PATH", "PATH"),
    **{_INSTALL / f"pseudocron-{t}.cfg.example": ("PATH", "HOME", "PGAI_CODER_MODEL") for t in _TIERS},
    **{_INSTALL / f"crontab-{t}.example": ("PGAI_AGENT_KANBAN_ROOT_PATH", "PATH") for t in _TIERS},
    _INSTALL / "crontab.example": ("PGAI_AGENT_KANBAN_ROOT_PATH", "PATH"),
}
_TEMPLATES = (*_DOCUMENTED, _TEAM_DIR / "scripts" / "pseudocron.cfg.example")
_SECRETS = _REPO / "secrets_example"


def _comment_blocks(path: pathlib.Path) -> list[str]:
    """The file's comment paragraphs: runs of comment lines split at an empty
    comment line (``#``) or a non-comment line."""
    blocks: list[list[str]] = [[]]
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#") and line.lstrip("#").strip():
            blocks[-1].append(line)
        elif blocks[-1]:
            blocks.append([])
    return ["\n".join(block) for block in blocks if block]


@pytest.mark.parametrize(
    ("path", "name"),
    [(path, name) for path, names in _DOCUMENTED.items() for name in names],
    ids=[f"{path.name}:{name}" for path, names in _DOCUMENTED.items() for name in names],
)
def test_each_template_variable_states_its_default(path: pathlib.Path, name: str) -> None:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    blocks = [block for block in _comment_blocks(path) if pattern.search(block)]
    assert blocks, f"{path.name} has no comment describing {name}"
    assert any("Default:" in block or "No default" in block for block in blocks), (
        f"no comment block naming {name} in {path.name} states its default:\n\n" + "\n\n".join(blocks)
    )


_KEY_ASSIGNMENT = re.compile(r"^\s*#?\s*(?:export\s+)?(?:ANTHROPIC|OPENAI)_API_KEY=", re.M)


@pytest.mark.parametrize("path", [p for p in _TEMPLATES if p != _SECRETS], ids=lambda p: p.name)
def test_only_secrets_example_assigns_an_api_key(path: pathlib.Path) -> None:
    hits = _KEY_ASSIGNMENT.findall(path.read_text(encoding="utf-8"))
    assert not hits, f"{path.name} carries a credential assignment; credentials belong in secrets_example only"


_PLACEHOLDER_ASSIGNMENT = re.compile(r"^\s*#\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=[\"']?<[^>\n]*>", re.M)


@pytest.mark.parametrize("path", _TEMPLATES, ids=lambda p: p.name)
def test_no_commented_assignment_uses_an_angle_bracket_placeholder(path: pathlib.Path) -> None:
    hits = [m.group(0).strip() for m in _PLACEHOLDER_ASSIGNMENT.finditer(path.read_text(encoding="utf-8"))]
    assert not hits, f"{path.name}: uncommenting these installs a literal placeholder: {hits}"


def test_projects_cfg_example_registers_no_project() -> None:
    text = (_REPO / "projects.cfg_example").read_text(encoding="utf-8")
    assert not re.search(r"^\s*\[project:", text, re.M), "an active [project:...] section would be registered"


# ---------------------------------------------------------------------------
# (5) priority ordering
# ---------------------------------------------------------------------------


def _bash_project_order(root: pathlib.Path) -> list[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR")}
    env.update(KANBAN_ROOT=str(root), PGAI_AGENT_KANBAN_ROOT_PATH=str(root))
    done = subprocess.run(
        ["bash", "-c", f'source "{_TEAM_DIR}/scripts/lib/projects.sh"; projects_cfg_list'],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


def test_a_non_integer_priority_orders_last_in_bash_and_python_alike(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects.cfg").write_text(
        "[project:bad-prio]\npriority=abc\n\n"
        "[project:first]\npriority=1\n\n"
        "[project:no-prio]\n\n"
        "[project:second]\npriority = 2\n",
        encoding="utf-8",
    )
    python_order = [entry.name for entry in ini.projects_registry(tmp_path)]
    bash_order = _bash_project_order(tmp_path)

    assert python_order == ["first", "second", "bad-prio", "no-prio"], python_order
    assert bash_order == python_order


# ---------------------------------------------------------------------------
# (6) REST board row limit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("field", "cfg", "expected"), [
    ("dashboard_max_rows=2\n", "", 5),
    ("dashboard_max_rows=500\n", "", 100),
    ("dashboard_max_rows=25\n", "", 25),
    ("", "[dashboard]\nmax_rows = 30\n", 30),
    ("", "", 20),
], ids=["below-min", "above-max", "in-range", "kanban-cfg-fallback", "built-in-default"])
def test_rest_board_row_limit_matches_the_tmux_dashboard(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, field: str, cfg: str, expected: int
) -> None:
    from pgai_agent_kanban.api.routers import board

    monkeypatch.delenv("DASHBOARD_MAX_ROWS", raising=False)
    (tmp_path / "projects.cfg").write_text(f"[project:alpha]\npriority=1\n{field}", encoding="utf-8")
    if cfg:
        (tmp_path / "kanban.cfg").write_text(cfg, encoding="utf-8")

    rows = board._read_projects_registry(tmp_path)

    assert [row["max_rows"] for row in rows] == [expected]


# ---------------------------------------------------------------------------
# (7) cron-suggested and the pseudocron tiers
# ---------------------------------------------------------------------------

_CRON_SUGGESTED = _TEAM_DIR / "scripts" / "cron-suggested.template.txt"


def _render_with_documented_recipe(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    header = "\n".join(line for line in text.splitlines() if line.startswith("#"))
    substitutions = re.findall(r"s\|(__[A-Z_]+__)\|([^|]*)\|g", header)
    assert substitutions, f"{path.name} documents no sed substitution recipe"
    for placeholder, value in substitutions:
        text = text.replace(placeholder, value)
    return text


def test_cron_suggested_renders_completely_with_its_own_recipe() -> None:
    rendered = _render_with_documented_recipe(_CRON_SUGGESTED)
    assert not re.findall(r"__[A-Z_]+__", rendered), "the documented recipe leaves a placeholder behind"


def test_cron_suggested_uses_the_agent_flag_and_schedules_overwatch() -> None:
    jobs = [line for line in _CRON_SUGGESTED.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
    positional = [line for line in jobs if re.search(r"wake-batch\.sh\s+(?!-)\w", line)]
    assert not positional, f"deprecated positional wake-batch.sh calls: {positional}"
    assert any("overwatch-sweep.sh" in line for line in jobs)
    assert any("--agent=overwatch" in line for line in jobs)


@pytest.mark.parametrize("tier", _TIERS)
def test_each_pseudocron_tier_schedules_both_overwatch_jobs(tier: str) -> None:
    path = _INSTALL / f"pseudocron-{tier}.cfg.example"
    jobs = [line for line in path.read_text(encoding="utf-8").splitlines()
            if re.match(r"^\s*[0-9]+\s+\S", line)]
    assert any("scripts/overwatch-sweep.sh" in line for line in jobs), path.name
    assert any("--agent=overwatch" in line for line in jobs), path.name
