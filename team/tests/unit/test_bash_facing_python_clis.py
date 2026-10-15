"""
test_bash_facing_python_clis.py
===============================
The small command lines bash scripts reach through pp_run_ops / python3 -m
in place of inline Python, each run the way its script runs it:

  * pgai_agent_kanban.lib.json_field -- one field of a JSON object (text or
    file), its null/default/JSON/string-only renderings, and a document's
    length; a bad document exits 1 with nothing on stdout.
  * pgai_agent_kanban.halt_after.describe -- a HALT-AFTER token's operator
    text, as written and parsed from raw sentinel content.
  * pgai_agent_kanban.overwatch.intent declares -- whether an INTENT.md
    entry declares exactly the given text.
  * pgai_agent_kanban.dashboard.cron_firings --count -- the roster size.
  * pgai_agent_kanban.api.config port -- the resolved API port.
  * pgai_agent_kanban.lint.doc_commands -- the fenced commands doc-lint.sh
    runs, and team/scripts/lib/gated_findings.py (run by path, as the
    gated runners run it) -- the findings JSON.
  * pgai_agent_kanban.discovery.items / eligibility -- the discovery
    stages' scans, on the shapes the parity checks used.

Every child runs through team/scripts/lib/pp_run_ops.sh with a scrubbed
environment (this suite's interpreter first on PATH) and a working
directory under tmp_path.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"
_CRON_PARSER = _TEAM_DIR / "pm-agent" / "lib" / "cron_parser.py"


def _run(tmp_path: pathlib.Path, module: str, *args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    # The interpreter running this suite comes first on PATH, so the modules
    # run under the Python version the framework requires.
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env=extra_env or {},
    )
    return subprocess.run(
        ["bash", str(_PP_RUN_OPS), module, *args],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60,
    )


# --- json_field ---------------------------------------------------------------

_DOC = json.dumps({"attempt": 2, "family_key": None, "cargo": ["BUG-9001-a.md"], "captured": False,
                   "workflow_type": "  ", "name": "x"})


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["attempt"], "2"),
        (["family_key"], "None"),
        (["--null", "none", "family_key"], "none"),
        (["--json", "cargo"], '["BUG-9001-a.md"]'),
        (["captured"], "False"),
        (["--default", "true", "missing"], "true"),
        (["--string-only", "--default", "release", "workflow_type"], "release"),
        (["--string-only", "--default", "release", "name"], "x"),
        (["--string-only", "--default", "release", "attempt"], "release"),
    ],
    ids=["int", "null", "null-text", "json", "bool", "default", "blank-string", "string", "non-string"],
)
def test_json_field_prints_the_field_as_documented(tmp_path: pathlib.Path, args: list[str], expected: str) -> None:
    result = _run(tmp_path, "pgai_agent_kanban.lib.json_field", *args[:-1], _DOC, args[-1])
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected + "\n"


def test_json_field_reads_a_file_and_its_length(tmp_path: pathlib.Path) -> None:
    doc = tmp_path / "findings.json"
    doc.write_text('[{"a": 1}, {"b": 2}, {"c": 3}]', encoding="utf-8")
    assert _run(tmp_path, "pgai_agent_kanban.lib.json_field", "--file", "--length", str(doc)).stdout == "3\n"


@pytest.mark.parametrize("source", ["not json", "[1, 2]"], ids=["unparseable", "not-an-object"])
def test_json_field_refuses_a_bad_document(tmp_path: pathlib.Path, source: str) -> None:
    result = _run(tmp_path, "pgai_agent_kanban.lib.json_field", source, "key")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "json_field: cannot read field 'key'" in result.stderr


# --- halt_after.describe --------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["describe", "rc:v1.2.3"], "until v1.2.3"),
        (["describe", "coder"], "coder"),
        (["describe-raw", "  RC:v2.0.0\n"], "until v2.0.0\n"),
        (["describe-raw", " bogus "], "bogus\n"),
    ],
    ids=["pinned", "role", "raw-pinned", "raw-unparsed"],
)
def test_halt_after_describe_renders_the_token(tmp_path: pathlib.Path, args: list[str], expected: str) -> None:
    result = _run(tmp_path, "pgai_agent_kanban.halt_after.describe", *args)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
    assert "RuntimeWarning" not in result.stderr


# --- overwatch.intent declares ----------------------------------------------------


def test_intent_declares_only_an_exact_entry(tmp_path: pathlib.Path) -> None:
    intent = tmp_path / "INTENT.md"
    intent.write_text("# Intent\n\n## Entries\n\n- 2026-10-01: Ship past the HALT for the fixture\n",
                      encoding="utf-8")
    module = "pgai_agent_kanban.overwatch.intent"
    assert _run(tmp_path, module, "declares", str(intent), "Ship past the HALT for the fixture").returncode == 0
    assert _run(tmp_path, module, "declares", str(intent), "Ship past the HALT").returncode == 1
    assert _run(tmp_path, module, "declares", str(tmp_path / "absent.md"), "x").returncode == 1


# --- cron_firings --count ------------------------------------------------------------


def test_cron_firings_count_is_the_roster_size(tmp_path: pathlib.Path) -> None:
    crontab = (
        "*/2 * * * * /k/scripts/wake-batch.sh --agent=pm --sleep=0\n"
        "1-59/2 * * * * /k/scripts/wake-batch.sh --agent=coder --sleep=0\n"
    )
    module = "pgai_agent_kanban.dashboard.cron_firings"
    labels = json.loads(_run(tmp_path, module, crontab, str(_CRON_PARSER)).stdout)
    count = _run(tmp_path, module, "--count", crontab, str(_CRON_PARSER))
    assert count.stdout == f"{len(labels)}\n"
    assert _run(tmp_path, module, "--count", "", str(_CRON_PARSER)).stdout == "0\n"


# --- api.config port -------------------------------------------------------------------


def test_api_config_port_follows_the_loader(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "kanban.cfg").write_text("[api]\nport = 8411\n", encoding="utf-8")
    env = {"PGAI_AGENT_KANBAN_ROOT_PATH": str(root)}
    assert _run(tmp_path, "pgai_agent_kanban.api.config", "port", extra_env=env).stdout == "8411\n"
    env["PGAI_API_PORT"] = "8422"
    assert _run(tmp_path, "pgai_agent_kanban.api.config", "port", extra_env=env).stdout == "8422\n"


# --- discovery.items / eligibility ----------------------------------------------------------


def test_discovery_items_commands(tmp_path: pathlib.Path) -> None:
    bugs = tmp_path / "bugs"
    bugs.mkdir()
    (bugs / "BUG-9001-a.md").write_text("# a\n**Severity:**  high \n## Status\nopen\n", encoding="utf-8")
    (bugs / "BUG-9002-b.md").write_text("# b\n## Status\ndone\n\n## Known Issue Carry Forward\n\nv1\n",
                                        encoding="utf-8")
    (bugs / "BUG-12-bad.md").write_text("x", encoding="utf-8")
    module = "pgai_agent_kanban.discovery.items"
    rejected = tmp_path / "rejected.tsv"

    scan = _run(tmp_path, module, "scan", str(bugs), "BUG-", str(rejected))
    assert scan.stdout.splitlines() == [str((bugs / "BUG-9001-a.md").resolve())]
    assert "skipping BUG-9002-b.md: terminal state 'done'" in scan.stderr
    assert rejected.read_text(encoding="utf-8").startswith("BUG-12-bad.md\t")

    assert _run(tmp_path, module, "severity", str(bugs / "BUG-9001-a.md")).stdout == "high\n"
    lines = f"{bugs / 'BUG-9001-a.md'}\n\n{bugs / 'BUG-9002-b.md'}\tRIDE_ALONG:x\n"
    assert _run(tmp_path, module, "order", lines).stdout.splitlines() == [
        f"{bugs / 'BUG-9002-b.md'}\tRIDE_ALONG:x", str(bugs / "BUG-9001-a.md"),
    ]

    state = tmp_path / "counts.tsv"
    state.write_text("keep.md\t1\t2026\n", encoding="utf-8")
    _run(tmp_path, module, "record-rejection", str(state), "BUG-12-bad.md", "2", "now", "why")
    assert state.read_text(encoding="utf-8") == "keep.md\t1\t2026\nBUG-12-bad.md\t2\tnow\twhy\n"
    _run(tmp_path, module, "forget-rejection", str(state), "BUG-12-bad.md")
    assert state.read_text(encoding="utf-8") == "keep.md\t1\t2026\n"

    status = tmp_path / "status.md"
    status.write_text("## State\ndone\n\n## Summary\nx\n", encoding="utf-8")
    assert _run(tmp_path, module, "task-state", str(status)).stdout == "DONE\n"
    status.write_text("## State\n\n## Summary\nDONE\n", encoding="utf-8")
    assert _run(tmp_path, module, "task-state", str(status)).stdout == "UNPARSEABLE\n"


def test_discovery_eligibility_orders_and_marks_requirements(tmp_path: pathlib.Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    for name, target in [("v1.2.0-a.md", "v1.2.0"), ("v1.10.0-b.md", "v1.10.0"), ("v2.0.0-c.md", "v2.0.0"),
                         ("v1.0.5-d.md", "auto"), ("v1.0.0-e.md", "v1.0.0")]:
        (req / name).write_text(f"# r\n\n## Target Version\n{target}\n", encoding="utf-8")
    result = _run(tmp_path, "pgai_agent_kanban.discovery.eligibility", str(req), "v1.0.0", "", "1", "", "semver")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        str((req / "v1.2.0-a.md").resolve()),
        str((req / "v1.10.0-b.md").resolve()),
        f"{(req / 'v2.0.0-c.md').resolve()}:ceiling-blocked:v2.0.0:major:2:max_major:1",
        f"{(req / 'v1.0.5-d.md').resolve()}:auto",
    ]


# --- lint.doc_commands -----------------------------------------------------------------------


def test_doc_commands_lists_each_fenced_command_with_its_annotations(tmp_path: pathlib.Path) -> None:
    doc = tmp_path / "README.md"
    doc.write_text(
        "# Doc\n\n```bash\ntrue\necho a && \\\n  echo b\n# comment\nfalse  # doc-lint: skip\n```\n\n"
        "<!-- doc-lint: skip needs a service -->\n\n```sh\nservice-cmd\n```\n\n"
        "```\nunlabelled\n```\n\n"
        "<!-- doc-lint: docker -->\n```\ndocker ps\n```\n\n"
        "~~~bash\ncat <YOUR_FILE>\n~~~\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pgai_agent_kanban.lint.doc_commands", str(doc))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "1\tfalse\tfalse\tfalse\ttrue",
        "1\tfalse\tfalse\tfalse\techo a &&    echo b",
        "1\tfalse\tfalse\ttrue\tfalse",
        "2\ttrue\tfalse\tfalse\tservice-cmd",
        "3\tfalse\ttrue\tfalse\tdocker ps",
        "4\tfalse\tfalse\tfalse\tcat <YOUR_FILE>",
    ]
    missing = _run(tmp_path, "pgai_agent_kanban.lint.doc_commands", str(tmp_path / "absent.md"))
    assert missing.returncode == 1
    assert "doc-lint.sh: cannot read" in missing.stderr


# --- lint.gated_findings ----------------------------------------------------------------------


def test_gated_findings_build_record_split_and_write(tmp_path: pathlib.Path) -> None:
    script = _TEAM_DIR / "scripts" / "lib" / "gated_findings.py"
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_path_dirs=[pathlib.Path(sys.executable).parent])

    def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            ["python3", str(script), *args], input=stdin,
            capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60,
        )

    built = run("build", "lint_x", "3", stdin="\n  first line \nmore\n")
    assert json.loads(built.stdout) == {
        "lint": "lint_x", "severity": "quality", "exit_code": 3, "summary": "first line",
        "detail": "\n  first line \nmore\n",
    }
    recorded = json.loads(run("record", "lint_y", "aborted").stdout)
    assert recorded == {"lint": "lint_y", "severity": "quality", "summary": "aborted"}

    array = tmp_path / "array.json"
    array.write_text('[{"lint": "a"}, {"lint": "b", "line": 3}]', encoding="utf-8")
    assert run("split", str(array)).stdout.splitlines() == [
        '{"lint": "a"}', '{"lint": "b", "line": 3}',
    ]

    out = tmp_path / "findings.json"
    written = run("write", str(out), "run-unit-tests", stdin=built.stdout + "\n" + json.dumps(recorded) + "\n")
    assert written.returncode == 0, written.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1 and doc["runner"] == "run-unit-tests"
    assert [f["lint"] for f in doc["findings"]] == ["lint_x", "lint_y"]
    assert out.read_text(encoding="utf-8").endswith("}\n")
