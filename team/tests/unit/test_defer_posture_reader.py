"""
test_defer_posture_reader.py — Unit tests for pgai_agent_kanban.lib.defer_posture,
the one Python reader of the deferral posture, and for the bash-rule INI
reader it reads the cfg tiers through (pgai_agent_kanban.lib.ini.read_line_value).

Coverage:

  1. resolve_defer_posture answers exactly as the bash resolver
     (team/scripts/lib/defer_posture.sh) for the same tree: every tier,
     project-over-root campaign precedence, root-only mode, inline comments
     and CRLF in cfg files, and an invalid value at a tier never consulted.
  2. Every refusal carries the same dual-channel block the bash resolver
     prints, and names the file at fault.
  3. read_line_value answers as bash read_ini for the same file.
  4. The campaign-file helpers, posture_mode and the per-bug severity
     decision.
  5. No Python module other than lib/defer_posture.py names the campaign
     file to open it, and the dashboard and API readers take their posture
     vocabulary and answer from it.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib import defer_posture, ini  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_PACKAGE_DIR = _TEAM_DIR / "pgai_agent_kanban"
_BASH_RESOLVER = _TEAM_DIR / "scripts" / "lib" / "defer_posture.sh"
_BASH_INI = _TEAM_DIR / "scripts" / "lib" / "ini_parser.sh"
_PROJECT = "demo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "root"
    (root / "projects" / _PROJECT).mkdir(parents=True)
    return root


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _bash_resolve(tmp_path: pathlib.Path, root: pathlib.Path, project: str) -> subprocess.CompletedProcess:
    """Run the bash resolver for *project* ("" = root-only) on *root*."""
    script = (
        f'source "{_BASH_RESOLVER}"\n'
        'resolve_defer_posture "$1"; rc=$?\n'
        'printf "ERRFILE=%s\\n" "$DEFER_POSTURE_LAST_ERROR_FILE" >&2\n'
        'exit $rc\n'
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
        },
    )
    return subprocess.run(
        ["bash", "-c", script, "resolve", project],
        capture_output=True, text=True, env=env, timeout=30,
    )


def _assert_same_answer(tmp_path: pathlib.Path, root: pathlib.Path, project: str = _PROJECT) -> None:
    """Both resolvers agree: same lines on success, same block and file on refusal."""
    bash = _bash_resolve(tmp_path, root, project)
    bash_stderr, _, errfile_line = bash.stderr.rpartition("ERRFILE=")
    bash_errfile = errfile_line.strip()
    try:
        posture = defer_posture.resolve_defer_posture(root, project or None)
    except defer_posture.DeferPostureError as exc:
        assert bash.returncode == 1, f"bash resolved where Python refused:\n{bash.stdout}"
        assert bash_stderr == f"{exc}\n"
        assert bash_errfile == exc.file
        return
    assert bash.returncode == 0, f"Python resolved where bash refused:\n{bash.stderr}"
    assert posture.resolver_lines() == bash.stdout


# ---------------------------------------------------------------------------
# 1. Same answer as the bash resolver
# ---------------------------------------------------------------------------


def test_defaults_resolve_from_the_default_tier(tmp_path):
    root = _root(tmp_path)
    posture = defer_posture.resolve_defer_posture(root, _PROJECT)
    assert posture.deferred_bugs == defer_posture.PostureValue("false", "default")
    assert posture.deferred_bug_level == defer_posture.PostureValue("low", "default")
    assert posture.defer_piggyback == defer_posture.PostureValue("true", "default")
    assert posture.until == defer_posture.PostureValue("", "none")
    assert posture.campaign_file == defer_posture.PostureValue("", "none")
    _assert_same_answer(tmp_path, root)


def test_project_cfg_outranks_kanban_cfg_key_by_key(tmp_path):
    root = _root(tmp_path)
    _write(root / "kanban.cfg", "[release]\ndeferred_bugs = true\ndeferred_bug_level = medium\n")
    _write(root / "projects" / _PROJECT / "project.cfg", "[release]\ndeferred_bug_level = high\n")
    posture = defer_posture.resolve_defer_posture(root, _PROJECT)
    assert posture.deferred_bugs == defer_posture.PostureValue("true", "kanban.cfg")
    assert posture.deferred_bug_level == defer_posture.PostureValue("high", "project.cfg")
    _assert_same_answer(tmp_path, root)


def test_project_campaign_file_forces_deferral_and_wins_over_the_root_one(tmp_path):
    root = _root(tmp_path)
    _write(root / "projects" / _PROJECT / "project.cfg", "[release]\ndeferred_bugs = false\ndefer_piggyback = false\n")
    _write(root / "DEFER-LOW-BUGS", "until=v1.0.0\n")
    project_campaign = root / "projects" / _PROJECT / "DEFER-LOW-BUGS"
    _write(project_campaign, "# note\nuntil = v2.3.4  \n")
    posture = defer_posture.resolve_defer_posture(root, _PROJECT)
    assert posture.deferred_bugs == defer_posture.PostureValue("true", "campaign file")
    assert posture.deferred_bug_level == defer_posture.PostureValue("low", "campaign file")
    assert posture.defer_piggyback == defer_posture.PostureValue("false", "project.cfg")
    assert posture.until == defer_posture.PostureValue("v2.3.4", "campaign file")
    assert posture.campaign_file.value == str(project_campaign)
    assert posture.deferring
    _assert_same_answer(tmp_path, root)


def test_root_only_mode_reads_the_root_campaign_and_skips_project_cfg(tmp_path):
    root = _root(tmp_path)
    _write(root / "projects" / _PROJECT / "project.cfg", "[release]\ndefer_piggyback = maybe\n")
    _write(root / "DEFER-LOW-BUGS", "until=v1.0.0\n")
    posture = defer_posture.resolve_defer_posture(root, None)
    assert posture.campaign_file.value == str(root / "DEFER-LOW-BUGS")
    assert posture.defer_piggyback == defer_posture.PostureValue("true", "default")
    _assert_same_answer(tmp_path, root, project="")


def test_inline_comments_and_crlf_line_endings_read_as_the_bash_reader_reads_them(tmp_path):
    root = _root(tmp_path)
    _write(root / "kanban.cfg", "[release]\r\ndeferred_bugs = true   # on for now\r\ndeferred_bug_level=medium;x\r\n")
    _assert_same_answer(tmp_path, root)


def test_an_invalid_value_at_a_tier_never_consulted_is_not_read(tmp_path):
    root = _root(tmp_path)
    _write(root / "kanban.cfg", "[release]\ndeferred_bug_level = critical\n")
    _write(root / "projects" / _PROJECT / "DEFER-LOW-BUGS", "until=v3.0.0\n")
    posture = defer_posture.resolve_defer_posture(root, _PROJECT)
    assert posture.deferred_bug_level == defer_posture.PostureValue("low", "campaign file")
    _assert_same_answer(tmp_path, root)


# ---------------------------------------------------------------------------
# 2. Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "where, text, error",
    [
        ("project", "[release]\ndeferred_bugs = on\n", "defer_posture.invalid_deferred_bugs"),
        ("kanban", "[release]\ndefer_piggyback = yes\n", "defer_posture.invalid_defer_piggyback"),
        ("project", "[release]\ndeferred_bug_level = critical\n", "defer_posture.invalid_deferred_bug_level"),
    ],
)
def test_an_unaccepted_cfg_value_refuses_naming_the_file(tmp_path, where, text, error):
    root = _root(tmp_path)
    cfg = root / "projects" / _PROJECT / "project.cfg" if where == "project" else root / "kanban.cfg"
    _write(cfg, text)
    with pytest.raises(defer_posture.DeferPostureError) as caught:
        defer_posture.resolve_defer_posture(root, _PROJECT)
    assert caught.value.error == error
    assert caught.value.file == str(cfg)
    _assert_same_answer(tmp_path, root)


@pytest.mark.parametrize(
    "body, error",
    [
        ("# armed by hand\nno target here\n", "defer_posture.campaign_unparseable"),
        ("until=2.14\n", "defer_posture.campaign_malformed_until"),
        ("until=v1.2.3 # trailing\n", "defer_posture.campaign_malformed_until"),
    ],
)
def test_an_unreadable_campaign_file_refuses_rather_than_reading_as_off(tmp_path, body, error):
    root = _root(tmp_path)
    campaign = root / "projects" / _PROJECT / "DEFER-LOW-BUGS"
    _write(campaign, body)
    with pytest.raises(defer_posture.DeferPostureError) as caught:
        defer_posture.resolve_defer_posture(root, _PROJECT)
    assert caught.value.error == error
    assert caught.value.file == str(campaign)
    assert str(caught.value).startswith(f"error:   {error}\n")
    _assert_same_answer(tmp_path, root)


# ---------------------------------------------------------------------------
# 3. read_line_value answers as bash read_ini
# ---------------------------------------------------------------------------


_INI_TEXT = (
    "top = before any section\n"
    "[release]\n"
    "  # an indented comment\n"
    "deferred_bugs = true  ; inline\n"
    "deferred_bugs = false\n"
    "Level = high\n"
    "glyph = #literal\n"
    "blank =\n"
    "url = a=b=c\r\n"
    "[ other ]\n"
    "key = other-value\n"
    "[release.extra]\n"
    "key = dotted\n"
)


@pytest.mark.parametrize(
    "section, key",
    [
        ("release", "deferred_bugs"),
        ("release", "Level"),
        ("release", "level"),
        ("release", "glyph"),
        ("release", "blank"),
        ("release", "url"),
        ("release", "missing"),
        ("other", "key"),
        ("release.extra", "key"),
        ("", "top"),
    ],
)
def test_read_line_value_answers_as_bash_read_ini(tmp_path, section, key):
    cfg = tmp_path / "x.cfg"
    _write(cfg, _INI_TEXT)
    bash = subprocess.run(
        ["bash", "-c", f'source "{_BASH_INI}"; read_ini "$1" "$2" "$3" "@ABSENT@"', "ri", str(cfg), section, key],
        capture_output=True, text=True, timeout=30,
    )
    python = ini.read_line_value(cfg, section, key)
    assert (python if python is not None else "@ABSENT@") == bash.stdout


def test_read_line_value_reads_a_missing_file_as_absent(tmp_path):
    assert ini.read_line_value(tmp_path / "nope.cfg", "release", "deferred_bugs") is None


# ---------------------------------------------------------------------------
# 4. Helpers
# ---------------------------------------------------------------------------


def test_campaign_until_helpers(tmp_path):
    campaign = defer_posture.campaign_file_path(tmp_path)
    assert campaign == tmp_path / "DEFER-LOW-BUGS"
    assert defer_posture.read_campaign_until(campaign) == ""
    _write(campaign, "# c\n  until =  v9.8.7 \r\nuntil=v0.0.1\n")
    assert defer_posture.read_campaign_until(campaign) == "v9.8.7"
    assert defer_posture.parse_campaign_file(campaign) == "v9.8.7"
    _write(campaign, "until=nine\n")
    assert defer_posture.read_campaign_until(campaign) == "nine"


@pytest.mark.parametrize(
    "value, ok",
    [("v1.2.3", True), ("v10.20.30", True), ("1.2.3", False), ("v1.2", False),
     ("v1.2.3-rc1", False), ("v1.2.3 ", False), ("v١.2.3", False)],
)
def test_is_well_formed_until(value, ok):
    assert defer_posture.is_well_formed_until(value) is ok


def test_posture_mode_words(tmp_path):
    root = _root(tmp_path)
    assert defer_posture.posture_mode(None) == defer_posture.MODE_MALFORMED == "malformed"
    assert defer_posture.posture_mode(defer_posture.resolve_defer_posture(root, _PROJECT)) == "off"
    _write(root / "DEFER-LOW-BUGS", "until=v1.0.0\n")
    assert defer_posture.posture_mode(defer_posture.resolve_defer_posture(root, _PROJECT)) == "on"


@pytest.mark.parametrize(
    "severity, level, expected",
    [("low", "low", "deferred"), (" Medium. ", "high", "deferred"), ("high", "medium", "kept"),
     ("critical", "high", "kept"), ("urgent", "high", "unparseable"), ("low", "bogus", "unparseable")],
)
def test_severity_defer_decision(severity, level, expected):
    assert defer_posture.severity_defer_decision(severity, level) == expected


# ---------------------------------------------------------------------------
# 5. One reader
# ---------------------------------------------------------------------------


def _campaign_name_literals(path: pathlib.Path) -> list[int]:
    """Line numbers of string constants that name the campaign file as a path."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == "DEFER-LOW-BUGS" or node.value.endswith("/DEFER-LOW-BUGS"):
                lines.append(node.lineno)
    return lines


def test_no_python_module_but_the_posture_reader_names_the_campaign_file():
    offenders = {}
    for path in sorted(_PACKAGE_DIR.rglob("*.py")):
        if "tests" in path.relative_to(_PACKAGE_DIR).parts:
            continue
        if path == _PACKAGE_DIR / "lib" / "defer_posture.py":
            continue
        lines = _campaign_name_literals(path)
        if lines:
            offenders[str(path.relative_to(_TEAM_DIR))] = lines
    assert offenders == {}, (
        "only pgai_agent_kanban/lib/defer_posture.py may name the DEFER-LOW-BUGS "
        f"file; import CAMPAIGN_FILE_NAME / campaign_file_path from it instead: {offenders}"
    )


def test_dashboard_readers_take_their_posture_vocabulary_from_the_reader():
    from pgai_agent_kanban.dashboard import defer_card_glyph, status_line

    assert status_line.severity_defer_decision is defer_posture.severity_defer_decision
    assert status_line.SEVERITY_VOCABULARY is defer_posture.SEVERITY_VOCABULARY
    assert defer_card_glyph.MODE_MALFORMED is defer_posture.MODE_MALFORMED
    assert defer_card_glyph.MODE_ON is defer_posture.MODE_ON
    modes, _ = defer_card_glyph.parse_proj_defer_info([], "\x1f", 1)
    assert modes == [defer_posture.MODE_OFF]


def test_verdict_posture_fields_come_from_the_reader(tmp_path, monkeypatch):
    from pgai_agent_kanban.ops import verdict

    root = _root(tmp_path)
    _write(root / "projects" / _PROJECT / "DEFER-LOW-BUGS", "until=v4.5.6\n")
    calls = []
    real = defer_posture.resolve_defer_posture

    def spy(kanban_root, project=None):
        calls.append((str(kanban_root), project))
        return real(kanban_root, project)

    monkeypatch.setattr(defer_posture, "resolve_defer_posture", spy)

    def posture() -> dict:
        report = verdict.resolve_verdicts(root, [_PROJECT], details=True)
        return verdict.verdicts_json(report)["posture"]

    first = posture()
    assert calls == [(str(root), _PROJECT)]
    assert first["deferred_bugs"] == {"value": "true", "tier": "campaign file"}
    assert first["until"] == {
        "value": "v4.5.6", "tier": "campaign file",
        "campaign_file": str(root / "projects" / _PROJECT / "DEFER-LOW-BUGS"),
    }
    assert first["pending_bundles"] == []

    (root / "projects" / _PROJECT / "DEFER-LOW-BUGS").unlink()
    assert posture()["until"] == {"value": "(none)", "tier": "none"}
