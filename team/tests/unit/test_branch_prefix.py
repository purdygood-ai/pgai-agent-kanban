"""
test_branch_prefix.py
=====================
The root-based readers in ``pgai_agent_kanban.lib.branch_prefix``: the ones a
caller holding a project root or a config file uses, which answer ``""`` for
an unset prefix rather than :func:`resolve_branch_prefix`'s ``ai_``.

Covered:

  1. ``branch_prefix_for_project_root`` / ``branch_prefix_from_cfg`` answer
     what ``pp_branch_prefix`` (``team/scripts/lib/project_paths.sh``) prints
     for the same file, value by value: an absent key, a blank value, ``ai_``,
     a quoted ``"ai_"``, a quoted empty ``""``, an inline comment, and a
     legacy sectionless ``PROJECT.cfg`` bare key.
  2. No config file, or ``None`` for the file, reads as ``""``.
  3. Neither validates: a value ``pp_branch_prefix`` would refuse is returned
     as read, so the caller's own refusal text stays the caller's.
  4. Every validated entry point a caller uses -- ``ini_branch_prefix_from_cfg``,
     ``bare_branch_prefix_from_cfg`` and ``ini_branch_prefix_for_project_root``
     -- answers what ``pp_branch_prefix`` prints for the same sandbox file, or
     raises ``InvalidBranchPrefixError`` where it refuses: plain, double-quoted
     and empty values, an inline comment (stripped), a single-quoted or
     slashed value (refused), a key outside ``[project]`` (ignored), a
     duplicate key, a key in the wrong case, a sectionless ``project.cfg``,
     legacy ``PROJECT.cfg`` files and no file at all.
  5. ``validate_branch_prefix`` accepts what bash's ``validate_branch_prefix``
     accepts; ``None`` or ``""`` for the file reads as ``""``.
  6. The name-based readers ``resolve_branch_prefix`` and
     ``prefixed_main_branch`` answer what ``pp_branch_prefix`` prints for
     every ``project.cfg`` row of the table in 4 (inline comment stripped, a
     key outside ``[project]`` ignored), and answer their *default* -- never
     the refused value -- where it refuses, as for an absent key, no file or
     no kanban root.

Every kanban root is a throwaway directory under tmp_path; bash runs get an
explicit, minimal environment (build_scrubbed_env) pointing every root
variable at it.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib import branch_prefix  # noqa: E402
from pgai_agent_kanban.lib.project_cfg import project_cfg_file  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_LIB = _TEAM_DIR / "scripts" / "lib"
_PROJECT = "prefix-rule"

# (file name, file body or None for no file, expected prefix)
_TWIN_CASES = [
    ("project.cfg", "[project]\ndev_tree_path = /srv/dev\n", ""),
    ("project.cfg", "[project]\nbranch_prefix =\n", ""),
    ("project.cfg", "[project]\nbranch_prefix = ai_\n", "ai_"),
    ("project.cfg", '[project]\nbranch_prefix = "ai_"\n', "ai_"),
    ("project.cfg", '[project]\nbranch_prefix = ""\n', ""),
    ("project.cfg", "[project]\nbranch_prefix = team-x_\n", "team-x_"),
    ("project.cfg", "[project]\nbranch_prefix = ai_  # house prefix\n", "ai_"),
    ("PROJECT.cfg", 'dev_tree_path=/srv/dev\nbranch_prefix="lg_"\n', "lg_"),
    ("project.cfg", None, ""),
]
_TWIN_IDS = ["absent", "blank", "ai", "quoted", "quoted-empty", "hyphen", "inline-comment", "legacy-bare", "no-file"]


def _root(tmp_path: pathlib.Path, files: dict) -> pathlib.Path:
    """A throwaway kanban root registering one project holding *files*."""
    root = tmp_path / "kanban"
    project = root / "projects" / _PROJECT
    project.mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    for name, body in files.items():
        if body is not None:
            (project / name).write_text(body, encoding="utf-8")
    return root


def _pp_branch_prefix(tmp_path: pathlib.Path, root: pathlib.Path) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    script = (
        f'source "{_LIB / "ini_parser.sh"}"\nsource "{_LIB / "project_paths.sh"}"\n'
        f'pp_branch_prefix "{_PROJECT}"; echo "rc=$?"\n'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)


@pytest.mark.parametrize(("name", "body", "expected"), _TWIN_CASES, ids=_TWIN_IDS)
def test_project_root_reader_matches_pp_branch_prefix(tmp_path: pathlib.Path, name, body, expected) -> None:
    root = _root(tmp_path, {name: body})
    project_root = root / "projects" / _PROJECT

    answer = branch_prefix.branch_prefix_for_project_root(project_root)
    bash = _pp_branch_prefix(tmp_path, root)

    assert answer == expected
    assert bash.stdout == f"{expected}\nrc=0\n", bash.stderr
    cfg = project_root / name
    assert branch_prefix.branch_prefix_from_cfg(cfg if cfg.is_file() else None) == expected
    assert branch_prefix.branch_prefix_for_project_root(str(project_root)) == expected


def test_no_config_reads_empty_not_the_ai_default(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, {})
    assert branch_prefix.branch_prefix_from_cfg(None) == ""
    assert branch_prefix.branch_prefix_for_project_root(root / "projects" / _PROJECT) == ""
    assert branch_prefix.ini_branch_prefix_for_project_root(root / "projects" / _PROJECT) == ""
    assert branch_prefix.DEFAULT_BRANCH_PREFIX == "ai_"


def test_twin_returns_an_invalid_value_unvalidated(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, {"project.cfg": "[project]\nbranch_prefix = bad/pfx\n"})
    project_root = root / "projects" / _PROJECT

    assert branch_prefix.branch_prefix_for_project_root(project_root) == "bad/pfx"
    bash = _pp_branch_prefix(tmp_path, root)
    assert bash.stdout == "rc=1\n"
    assert "validate_branch_prefix: invalid branch_prefix 'bad/pfx'" in bash.stderr


# Marks a case pp_branch_prefix refuses (prints nothing, returns 1).
_REFUSED = object()

# (case id, {file name: body}, pp_branch_prefix's answer or _REFUSED)
_VALIDATED_CASES = [
    ("plain", {"project.cfg": "[project]\nbranch_prefix = zz_\n"}, "zz_"),
    ("double-quoted", {"project.cfg": '[project]\nbranch_prefix = "zz_"\n'}, "zz_"),
    ("blank", {"project.cfg": "[project]\nbranch_prefix =\n"}, ""),
    ("quoted-empty", {"project.cfg": '[project]\nbranch_prefix = ""\n'}, ""),
    ("absent", {"project.cfg": "[project]\ndev_tree_path = /srv/dev\n"}, ""),
    ("hyphen", {"project.cfg": "[project]\nbranch_prefix = team-x_\n"}, "team-x_"),
    ("inline-comment", {"project.cfg": "[project]\nbranch_prefix = zz_  # note\n"}, "zz_"),
    ("inline-semicolon-comment", {"project.cfg": "[project]\nbranch_prefix = zz_ ; note\n"}, "zz_"),
    ("single-quoted", {"project.cfg": "[project]\nbranch_prefix = 'zz_'\n"}, _REFUSED),
    ("invalid", {"project.cfg": "[project]\nbranch_prefix = zz/\n"}, _REFUSED),
    ("invalid-space", {"project.cfg": "[project]\nbranch_prefix = zz _\n"}, _REFUSED),
    ("wrong-section", {"project.cfg": "[other]\nbranch_prefix = zz_\n[project]\ndev_tree_path = /srv/dev\n"}, ""),
    ("earlier-section-ignored", {"project.cfg": "[other]\nbranch_prefix = zz_\n[project]\nbranch_prefix = ai_\n"},
     "ai_"),
    ("duplicate-key-first-wins", {"project.cfg": "[project]\nbranch_prefix = ai_\nbranch_prefix = bb_\n"}, "ai_"),
    ("commented-and-case", {"project.cfg": "[project]\n# branch_prefix = cm_\nBranch_Prefix = up_\n"}, ""),
    ("sectionless-project-cfg", {"project.cfg": "branch_prefix = ai_\n"}, ""),
    ("legacy-section", {"PROJECT.cfg": "[project]\nbranch_prefix = lg_\n"}, "lg_"),
    ("legacy-bare", {"PROJECT.cfg": 'dev_tree_path=/srv/dev\nbranch_prefix="lg_"\n'}, "lg_"),
    ("legacy-bare-single-quoted", {"PROJECT.cfg": "branch_prefix='lg_'\n"}, "lg_"),
    ("project-cfg-wins-even-blank",
     {"project.cfg": "[project]\nbranch_prefix =\n", "PROJECT.cfg": "[project]\nbranch_prefix = lg_\n"}, ""),
    ("no-file", {}, ""),
]


def _validated_readings(project_root: pathlib.Path) -> dict:
    """Each validated entry point's answer for *project_root*: the prefix, or the refusal it raised."""
    cfg = project_cfg_file(project_root)
    readers = {
        "ini_branch_prefix_from_cfg": lambda: branch_prefix.ini_branch_prefix_from_cfg(cfg),
        "ini_branch_prefix_from_cfg(str)": lambda: branch_prefix.ini_branch_prefix_from_cfg(str(cfg) if cfg else None),
        "bare_branch_prefix_from_cfg": lambda: branch_prefix.bare_branch_prefix_from_cfg(cfg),
        "ini_branch_prefix_for_project_root": lambda: branch_prefix.ini_branch_prefix_for_project_root(project_root),
        "ini_branch_prefix_for_project_root(str)":
            lambda: branch_prefix.ini_branch_prefix_for_project_root(str(project_root)),
    }
    answers = {}
    for name, read in readers.items():
        try:
            answers[name] = read()
        except branch_prefix.InvalidBranchPrefixError as exc:
            answers[name] = exc
    return answers


@pytest.mark.parametrize(("files", "expected"), [c[1:] for c in _VALIDATED_CASES], ids=[c[0] for c in _VALIDATED_CASES])
def test_validated_entry_points_answer_as_pp_branch_prefix(tmp_path: pathlib.Path, files, expected) -> None:
    root = _root(tmp_path, files)
    bash = _pp_branch_prefix(tmp_path, root)
    answers = _validated_readings(root / "projects" / _PROJECT)

    if expected is _REFUSED:
        assert bash.stdout == "rc=1\n", bash.stderr
        refusals = {name: answer for name, answer in answers.items()
                    if isinstance(answer, branch_prefix.InvalidBranchPrefixError)}
        assert refusals.keys() == answers.keys(), answers
        for refusal in refusals.values():
            # The same refusal text bash's validate_branch_prefix prints, naming the same value.
            assert str(refusal).split(" in /")[0] in bash.stderr, (str(refusal), bash.stderr)
            assert refusal.source == str(project_cfg_file(root / "projects" / _PROJECT))
    else:
        assert bash.stdout == f"{expected}\nrc=0\n", bash.stderr
        assert answers == dict.fromkeys(answers, expected)


# The name-based readers read project.cfg alone, so the legacy PROJECT.cfg rows
# of the table above are outside their contract.
_NAME_BASED_CASES = [c for c in _VALIDATED_CASES if "PROJECT.cfg" not in c[1]]

# A default no case configures, so an answer equal to it can only be the fallback.
_FALLBACK = "fb_"


@pytest.mark.parametrize(
    ("files", "expected"), [c[1:] for c in _NAME_BASED_CASES], ids=[c[0] for c in _NAME_BASED_CASES]
)
def test_name_based_readers_answer_as_pp_branch_prefix(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, files, expected
) -> None:
    """resolve_branch_prefix / prefixed_main_branch agree with bash, a refusal yielding *default*.

    With ``default=""`` (bash's own answer for an unset key) every accepted
    value reads exactly as ``pp_branch_prefix`` prints it. A value bash
    refuses is never returned, nor glued in front of ``main``: the caller's
    *default* stands in for it.
    """
    root = _root(tmp_path, files)
    bash = _pp_branch_prefix(tmp_path, root)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))

    prefix = branch_prefix.resolve_branch_prefix(_PROJECT, default="")
    main = branch_prefix.prefixed_main_branch(_PROJECT, default="")

    if expected is _REFUSED:
        assert bash.stdout == "rc=1\n", bash.stderr
        assert (prefix, main) == ("", "main")
        assert branch_prefix.resolve_branch_prefix(_PROJECT, default=_FALLBACK) == _FALLBACK
        assert branch_prefix.prefixed_main_branch(_PROJECT, default=_FALLBACK) == f"{_FALLBACK}main"
        refused = files["project.cfg"].split("branch_prefix = ", 1)[1].rstrip("\n")
        for answer in (prefix, main):
            assert refused not in answer
    else:
        assert bash.stdout == f"{expected}\nrc=0\n", bash.stderr
        assert (prefix, main) == (expected, f"{expected}main")


@pytest.mark.parametrize(
    ("body", "prefix"),
    [
        ("[project]\nbranch_prefix = zz_\n", "zz_"),
        ("[project]\nbranch_prefix = zz_  # note\n", "zz_"),
        ('[project]\nbranch_prefix = ""\n', ""),
        ("[project]\nbranch_prefix =\n", ""),
        ("[project]\nbranch_prefix = 'zz_'\n", _FALLBACK),
        ("[project]\nbranch_prefix = zz/\n", _FALLBACK),
        ("[other]\nbranch_prefix = zz_\n[project]\ndev_tree_path = /srv/dev\n", _FALLBACK),
        (None, _FALLBACK),
    ],
    ids=["plain", "inline-comment", "quoted-empty", "blank", "single-quoted", "invalid", "wrong-section", "no-file"],
)
def test_name_based_readers_fall_back_only_when_unset_or_refused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, body, prefix
) -> None:
    """A configured value wins even when empty; an absent key, no file or a refused value yields *default*."""
    root = _root(tmp_path, {"project.cfg": body})
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))

    assert branch_prefix.resolve_branch_prefix(_PROJECT, default=_FALLBACK) == prefix
    assert branch_prefix.prefixed_main_branch(_PROJECT, default=_FALLBACK) == f"{prefix}main"


def test_name_based_readers_default_without_a_kanban_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PGAI_AGENT_KANBAN_ROOT_PATH", raising=False)
    assert branch_prefix.resolve_branch_prefix(_PROJECT) == branch_prefix.DEFAULT_BRANCH_PREFIX
    assert branch_prefix.prefixed_main_branch(_PROJECT, default=_FALLBACK) == f"{_FALLBACK}main"


@pytest.mark.parametrize(
    ("value", "accepted"),
    [("", True), ("ai_", True), ("Team-9_", True), ("zz/", False), ("'zz_'", False), ("zz_  # note", False),
     ("a.b", False), ("zz_\n", False)],
    ids=["empty", "ai", "mixed", "slash", "single-quoted", "inline-comment", "dot", "trailing-newline"],
)
def test_validate_branch_prefix_accepts_what_bash_accepts(value: str, accepted: bool) -> None:
    if accepted:
        assert branch_prefix.validate_branch_prefix(value) == value
    else:
        with pytest.raises(branch_prefix.InvalidBranchPrefixError) as raised:
            branch_prefix.validate_branch_prefix(value, "/srv/p/project.cfg")
        assert raised.value.value == value
        assert str(raised.value).startswith(f"invalid branch_prefix '{value}' (allowed characters: a-z A-Z 0-9 _ -;")
        assert str(raised.value).endswith(" in /srv/p/project.cfg")


def test_validated_one_file_readers_unset_file_reads_empty() -> None:
    for reader in (branch_prefix.ini_branch_prefix_from_cfg, branch_prefix.bare_branch_prefix_from_cfg):
        assert reader(None) == ""
        assert reader("") == ""
