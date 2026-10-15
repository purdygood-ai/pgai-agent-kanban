"""
test_version_ceiling_defaults.py
================================
The version-ceiling contract every reader shares:

- an absent ``max_patch`` / ``max_minor`` / ``max_major`` key, an empty
  value, or no project.cfg at all reads as the documented default
  21 / 13 / 0, held once in pgai_agent_kanban.lib.version_ceiling;
- ``none`` (any case) is the only spelling of "no ceiling";
- a malformed value is never "no ceiling": the reader prints exactly one
  line naming the file, the key and the value, and the gate treats the
  project as ceiling-blocked.

Covered here: the shared module and its CLI; the bash accessors
(pp_max_*, pp_version_within_ceiling) on a throwaway root; the bash
discovery path and eligibility.py on a v1.0.0 requirement with the major
ceiling absent, ``none`` and malformed (``O``, the letter); and a structural
check that no ceiling caller discards the accessors' stderr.
pm-agent's check, set-version-ceiling and the REST view have their own tests
(test_pm_agent_ceiling_semantics_bash.py,
test_ops_version_ceiling_switch_provider.py, api/tests/test_projects.py).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import version_ceiling as vc
from pgai_agent_kanban.discovery.eligibility import list_eligible_requirements
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PROJECT = "project_a"
_V1_BLOCKED_LOG = "PM not queued for v1.0.0: major version 1 exceeds max_major=0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT


def _set_versioning(root: pathlib.Path, body: "str | None") -> pathlib.Path:
    """Rewrite project_a's project.cfg with *body* as its [versioning] lines
    (None drops the section entirely). Returns the cfg path."""
    cfg = _project(root) / "project.cfg"
    text = cfg.read_text(encoding="utf-8")
    text = text[: text.index("[versioning]")].rstrip("\n") + "\n"
    if body is not None:
        text += f"\n[versioning]\n{body}"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def _add_v1_requirement(root: pathlib.Path) -> pathlib.Path:
    req_dir = _project(root) / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    path = req_dir / "v1.0.0-first-major.md"
    path.write_text(
        "# v1.0.0 first major\n\n## Target Version\nv1.0.0\n\n## Status\nopen\n\n## Summary\nA major.\n",
        encoding="utf-8",
    )
    return path


def _run(root: pathlib.Path, tmp_path: pathlib.Path, body: str):
    """Source the discovery libs against *root* and run *body*."""
    script = (
        "set -euo pipefail\n"
        "source scripts/lib/ini_parser.sh\n"
        "source scripts/lib/temp.sh\n"
        "source scripts/lib/semver.sh\n"
        "source scripts/lib/project_paths.sh\n"
        "source scripts/lib/discovery.sh\n"
        + body
    )
    env = build_scrubbed_env(
        home_dir=tmp_path,
        extra_env={
            "KANBAN_ROOT": str(root),
            "TEAM_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
            "PGAI_PROJECT_NAME": _PROJECT,
        },
    )
    return run_bash(tmp_path, script, base_env=env, timeout=120)


def _lines_naming(text: str, key: str, value: str) -> list[str]:
    return [line for line in text.splitlines() if key in line and f"'{value}'" in line]


# ---------------------------------------------------------------------------
# The shared module
# ---------------------------------------------------------------------------


def test_the_documented_defaults_live_in_one_mapping() -> None:
    assert dict(vc.CEILING_DEFAULTS) == {"max_patch": 21, "max_minor": 13, "max_major": 0}
    assert vc.CEILING_KEYS == ("max_patch", "max_minor", "max_major")


@pytest.mark.parametrize(
    "raw, limit, defaulted",
    [
        (None, 0, True),
        ("", 0, True),
        ("   ", 0, True),
        ("none", None, False),
        ("NONE", None, False),
        ('"none"', None, False),
        ("0", 0, False),
        ("'3'", 3, False),
        ("07", 7, False),
    ],
)
def test_parse_ceiling_reads_absent_none_and_numbers(raw, limit, defaulted) -> None:
    ceiling = vc.parse_ceiling("max_major", raw)
    assert (ceiling.limit, ceiling.defaulted) == (limit, defaulted)


@pytest.mark.parametrize("raw", ["O", "-1", "1.5", "unlimited", "0x1"])
def test_parse_ceiling_refuses_a_malformed_value_naming_file_key_and_value(raw) -> None:
    with pytest.raises(vc.CeilingValueError) as caught:
        vc.parse_ceiling("max_major", raw, source="/srv/p/project.cfg")
    text = str(caught.value)
    assert "\n" not in text
    assert f"/srv/p/project.cfg [versioning] max_major = '{raw}'" in text
    assert "ceiling-blocked" in text


def test_display_shows_the_value_in_effect() -> None:
    assert vc.parse_ceiling("max_minor", "").display == "13 (default)"
    assert vc.parse_ceiling("max_minor", "None").display == "none"
    assert vc.parse_ceiling("max_minor", "07").display == "07"


def test_read_ceilings_without_a_config_file_are_the_defaults() -> None:
    resolved = vc.read_ceilings(None)
    assert {key: c.limit for key, c in resolved.items()} == dict(vc.CEILING_DEFAULTS)
    assert all(c.defaulted for c in resolved.values())


def test_read_ceiling_reads_a_legacy_bare_key_file(tmp_path: pathlib.Path) -> None:
    legacy = tmp_path / "PROJECT.cfg"
    legacy.write_text('max_major="none"\nmax_minor=4\n', encoding="utf-8")
    assert vc.read_ceiling(legacy, "max_major").limit is None
    assert vc.read_ceiling(legacy, "max_minor").limit == 4
    assert vc.read_ceiling(legacy, "max_patch").limit == 21


def test_cli_prints_a_default_and_refuses_an_unknown_key(tmp_path: pathlib.Path) -> None:
    env = build_scrubbed_env(home_dir=tmp_path, extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    argv = [sys.executable, "-m", "pgai_agent_kanban.lib.version_ceiling"]
    good = subprocess.run(argv + ["default", "max_minor"], capture_output=True, text=True,
                          env=env, cwd=tmp_path, timeout=60)
    bad = subprocess.run(argv + ["default", "max_build"], capture_output=True, text=True,
                         env=env, cwd=tmp_path, timeout=60)
    assert (good.returncode, good.stdout) == (0, "13\n")
    assert bad.returncode == 2 and "max_build" in bad.stderr and "max_major" in bad.stderr


# ---------------------------------------------------------------------------
# The bash accessors
# ---------------------------------------------------------------------------

_ACCESSORS = (
    'for f in pp_max_patch pp_max_minor pp_max_major; do\n'
    '  if v="$($f project_a)"; then echo "$f=[$v]"; else echo "$f FAILED"; fi\n'
    'done\n'
)


def test_no_project_cfg_reads_major_0_minor_13_patch_21(two_project_root, tmp_path) -> None:
    (_project(two_project_root) / "project.cfg").unlink()
    result = _run(two_project_root, tmp_path, _ACCESSORS)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["pp_max_patch=[21]", "pp_max_minor=[13]", "pp_max_major=[0]"]
    assert result.stderr == ""


def test_absent_and_empty_keys_read_as_the_defaults(two_project_root, tmp_path) -> None:
    _set_versioning(two_project_root, "max_patch = 4\nmax_minor =\n")
    result = _run(two_project_root, tmp_path, _ACCESSORS)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["pp_max_patch=[4]", "pp_max_minor=[13]", "pp_max_major=[0]"]


def test_none_reads_as_no_ceiling_and_quotes_are_stripped(two_project_root, tmp_path) -> None:
    _set_versioning(two_project_root, 'max_patch = "5"\nmax_minor = None\nmax_major = none\n')
    result = _run(
        two_project_root, tmp_path,
        _ACCESSORS + 'pp_version_within_ceiling project_a v9.99.5 && echo WITHIN\n',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "pp_max_patch=[5]", "pp_max_minor=[]", "pp_max_major=[]", "WITHIN",
    ]


def test_version_within_ceiling_applies_the_default_major_zero(two_project_root, tmp_path) -> None:
    _set_versioning(two_project_root, "max_patch = 21\nmax_minor = 13\n")
    result = _run(
        two_project_root, tmp_path,
        'pp_version_within_ceiling project_a v0.13.21 && echo V0-WITHIN\n'
        'pp_version_within_ceiling project_a v1.0.0 || echo V1-EXCEEDS\n',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["V0-WITHIN", "V1-EXCEEDS"]


def test_malformed_value_fails_with_one_line_naming_file_key_and_value(two_project_root, tmp_path) -> None:
    cfg = _set_versioning(two_project_root, "max_major = O\n")
    result = _run(two_project_root, tmp_path, _ACCESSORS)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["pp_max_patch=[21]", "pp_max_minor=[13]", "pp_max_major FAILED"]
    assert len(result.stderr.splitlines()) == 1, result.stderr
    assert f"{cfg} [versioning] max_major = 'O'" in result.stderr


# ---------------------------------------------------------------------------
# Discovery (bash path) and eligibility.py
# ---------------------------------------------------------------------------


def test_absent_max_major_blocks_v1_in_the_bash_discovery_path(two_project_root, tmp_path) -> None:
    _set_versioning(two_project_root, "max_patch = 21\nmax_minor = 13\n")
    req = _add_v1_requirement(two_project_root)

    result = _run(
        two_project_root, tmp_path,
        'discovery_run_pipeline project_a\necho "DISCOVERY_STATUS=${DISCOVERY_LAST_STATUS}"\n',
    )

    assert result.returncode == 0, result.stderr
    assert _V1_BLOCKED_LOG in result.stderr
    assert "active ceilings for project_a: max_major=0, max_minor=13, max_patch=21" in result.stderr
    assert "DISCOVERY_STATUS=idle" in result.stdout
    assert "## Status\nopen" in req.read_text(encoding="utf-8")


def test_absent_max_major_blocks_v1_in_eligibility_py(tmp_path: pathlib.Path) -> None:
    req_dir = tmp_path / "requirements"
    req_dir.mkdir()
    (req_dir / "v1.0.0-first-major.md").write_text("# r\n\n## Target Version\nv1.0.0\n", encoding="utf-8")
    blocked = f"{(req_dir / 'v1.0.0-first-major.md').resolve()}:ceiling-blocked:v1.0.0:major:1:max_major:0"

    assert list_eligible_requirements(req_dir, "v0.0.0") == [blocked]

    env = build_scrubbed_env(home_dir=tmp_path, extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    for args in ([str(req_dir), "v0.0.0"], [str(req_dir), "v0.0.0", "", "", "", "semver"]):
        cli = subprocess.run([sys.executable, "-m", "pgai_agent_kanban.discovery.eligibility", *args],
                             capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
        assert (cli.returncode, cli.stdout) == (0, blocked + "\n"), cli.stderr


def test_eligibility_py_refuses_a_malformed_ceiling_argument(tmp_path: pathlib.Path) -> None:
    req_dir = tmp_path / "requirements"
    req_dir.mkdir()
    env = build_scrubbed_env(home_dir=tmp_path, extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    cli = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.discovery.eligibility", str(req_dir), "v0.0.0", "", "O"],
        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60,
    )
    assert cli.returncode == 2
    assert len(_lines_naming(cli.stderr, "max_major", "O")) == 1, cli.stderr


def test_max_major_none_makes_v1_eligible(two_project_root, tmp_path) -> None:
    _set_versioning(two_project_root, "max_major = none\n")
    req = _add_v1_requirement(two_project_root)

    result = _run(
        two_project_root, tmp_path,
        'req_dir="$(pp_requirements_dir project_a)"\n'
        '_disc_list_all_eligible_requirements "$req_dir" v0.0.0 '
        '"$(pp_max_minor project_a)" "$(pp_max_major project_a)" "$(pp_max_patch project_a)" semver\n',
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(req.resolve())]
    assert list_eligible_requirements(req.parent, "v0.0.0", max_major=None) == [str(req.resolve())]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param('discovery_run_pipeline project_a\necho "DISCOVERY_STATUS=${DISCOVERY_LAST_STATUS}"\n',
                     id="pipeline"),
        pytest.param('discovery_step_requirements project_a || echo "STEP_RC=$?"\n', id="step-3-direct"),
    ],
)
def test_malformed_max_major_queues_nothing_and_narrates_one_line(two_project_root, tmp_path, body) -> None:
    cfg = _set_versioning(two_project_root, "max_patch = 21\nmax_minor = 13\nmax_major = O\n")
    req = _add_v1_requirement(two_project_root)
    pm_backlog = _project(two_project_root) / "tasks" / "queues" / "pm_backlog.md"
    backlog_before = pm_backlog.read_text(encoding="utf-8")

    result = _run(two_project_root, tmp_path, body)

    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    naming = _lines_naming(output, "max_major", "O")
    assert len(naming) == 1, output
    assert str(cfg) in naming[0]
    assert "ceiling-blocked" in result.stderr
    assert "error:   discovery.ceiling-unreadable" in result.stderr
    if "pipeline" in body:
        assert "DISCOVERY_STATUS=blocked" in result.stdout
    else:
        assert "STEP_RC=1" in result.stdout
    assert pm_backlog.read_text(encoding="utf-8") == backlog_before
    assert "## Status\nopen" in req.read_text(encoding="utf-8")


def test_malformed_ceiling_blocks_the_auto_version_check(two_project_root, tmp_path) -> None:
    _set_versioning(two_project_root, "max_patch = x\n")
    result = _run(
        two_project_root, tmp_path,
        '_disc_check_ceiling_and_log project_a v0.0.1 test || echo "CHECK_RC=$?"\n',
    )
    assert result.returncode == 0, result.stderr
    assert "CHECK_RC=1" in result.stdout
    assert len(_lines_naming(result.stderr, "max_patch", "x")) == 1, result.stderr
    assert "PM not queued for v0.0.1: a version ceiling is unreadable" in result.stderr


# ---------------------------------------------------------------------------
# Structural: no ceiling caller throws the accessors' stderr away
# ---------------------------------------------------------------------------

_CALLERS = [
    "scripts/lib/discovery.sh",
    "scripts/lib/discovery_step_requirements.sh",
    "scripts/lib/discovery_guards.sh",
    "scripts/lib/discovery_defer.sh",
    "scripts/pm-agent.sh",
    "scripts/dashboard/metadata.sh",
]
_DISCARD_RE = re.compile(r"(pp_max_(?:patch|minor|major)|pp_version_within_ceiling)\b[^\n]*2>\s*/dev/null")


@pytest.mark.parametrize("relpath", _CALLERS)
def test_ceiling_callers_keep_the_accessors_stderr(relpath: str) -> None:
    text = (_TEAM_DIR / relpath).read_text(encoding="utf-8")
    assert "pp_max_" in text or "pp_version_within_ceiling" in text
    assert _DISCARD_RE.findall(text) == [], f"{relpath} discards a ceiling accessor's stderr"
