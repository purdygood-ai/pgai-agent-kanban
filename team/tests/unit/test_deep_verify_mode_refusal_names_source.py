"""
test_deep_verify_mode_refusal_names_source.py
=============================================
deep-verify.sh's ``--scheduled`` mode read refuses dual-channel
(``deep_verify.invalid_schedule_mode``) when the [release] deep_verify value
in force is unrecognized. That value resolves from project.cfg, then
kanban.cfg, then the default, so the refusal's message and remedy must name
the file the resolver reported -- kanban.cfg when the bad value came from
there -- not always project.cfg.

The refusal branch is extracted verbatim from team/scripts/deep-verify.sh at
test time (from its mode-read scratch file through the ``fi`` closing the
``deep-verify-02`` refusal) and run under bash with the resolver
``pp_deep_verify_mode`` stubbed. deep-verify.sh itself is never invoked, so
nothing here reaches the container tier. The emitter is the real
error-contract formatter, printed to stderr without the refusal-ledger
write.

Covered:
  - a resolver failing on a kanban.cfg value: exit 1, and the message and
    remedy both name that kanban.cfg path;
  - a resolver failing on a project.cfg value: the message and remedy name
    project.cfg, not kanban.cfg;
  - a resolver that succeeds: no refusal, the canonical mode is kept;
  - the resolver's own diagnostic still carries the "for project 'P' in FILE
    [release] deep_verify" shape the refusal reads the file from.
"""

from __future__ import annotations

import pathlib
import shlex

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_DEEP_VERIFY_SH = _TEAM_DIR / "scripts" / "deep-verify.sh"
_PROJECT_PATHS_SH = _TEAM_DIR / "scripts" / "lib" / "project_paths.sh"
_ERROR_CONTRACT_SH = _TEAM_DIR / "scripts" / "lib" / "error_contract.sh"

_START_MARKER = "_dv_mode_stderr_file=\"$(pgai_mktemp deep_verify_mode_stderr)\""
_ANCHOR_MARKER = "# CENSUS-ANCHOR: deep-verify-02"


def _extract_mode_refusal_branch() -> str:
    """The mode read and its refusal, verbatim from deep-verify.sh."""
    lines = _DEEP_VERIFY_SH.read_text(encoding="utf-8").splitlines()
    starts = [i for i, ln in enumerate(lines) if _START_MARKER in ln]
    anchors = [i for i, ln in enumerate(lines) if ln.strip() == _ANCHOR_MARKER]
    assert len(starts) == 1, f"expected one mode-read start line in {_DEEP_VERIFY_SH}"
    assert len(anchors) == 1, f"expected one {_ANCHOR_MARKER!r} line in {_DEEP_VERIFY_SH}"
    end = next(i for i in range(anchors[0], len(lines)) if lines[i].strip() == "fi")
    return "\n".join(lines[starts[0] : end + 1])


def _resolver_diagnostic(source_file: str) -> str:
    """The resolver's refusal line, in the shape pp_deep_verify_mode prints."""
    return (
        "project_paths.sh: pp_deep_verify_mode: unrecognized value 'bogus' for project "
        f"'demo' in {source_file} [release] deep_verify (expected 'weekly' or 'off', "
        "aliases 'true'/'false' also accepted; blank/absent in project.cfg and kanban.cfg "
        "defaults to 'weekly')"
    )


def _run_branch(tmp_path: pathlib.Path, resolver_body: str):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    script = "\n".join(
        [
            "set -uo pipefail",
            f"source {shlex.quote(str(_ERROR_CONTRACT_SH))}",
            'pgai_emit_error_contract() { pgai_format_error_contract "$1" "$2" "$3" >&2; }',
            f"pgai_mktemp() {{ mktemp -p {shlex.quote(str(scratch))}; }}",
            f"pp_deep_verify_mode() {{ {resolver_body}; }}",
            "_dv_project=demo",
            _extract_mode_refusal_branch(),
            'echo "MODE=${_dv_mode}"',
        ]
    )
    return run_bash(tmp_path, script)


def _contract_fields(stderr: str) -> dict[str, str]:
    fields = {}
    for line in stderr.splitlines():
        for key in ("error", "message", "remedy"):
            if line.startswith(f"{key}:"):
                fields[key] = line.split(":", 1)[1].strip()
    return fields


def test_kanban_cfg_value_refusal_names_kanban_cfg(tmp_path):
    kanban_cfg = str(tmp_path / "root" / "kanban.cfg")
    diag = _resolver_diagnostic(kanban_cfg)
    result = _run_branch(tmp_path, f"echo {shlex.quote(diag)} >&2; return 1")

    assert result.returncode == 1, result.stderr
    assert "MODE=" not in result.stdout
    fields = _contract_fields(result.stderr)
    assert fields.get("error") == "deep_verify.invalid_schedule_mode", result.stderr
    assert f"in {kanban_cfg}:" in fields["message"], fields["message"]
    assert f"Set [release] deep_verify in {kanban_cfg} " in fields["remedy"], fields["remedy"]
    assert "project.cfg [release] deep_verify" not in fields["message"]
    assert "Set project.cfg" not in fields["remedy"]


def test_project_cfg_value_refusal_names_project_cfg(tmp_path):
    project_cfg = str(tmp_path / "root" / "projects" / "demo" / "project.cfg")
    diag = _resolver_diagnostic(project_cfg)
    result = _run_branch(tmp_path, f"echo {shlex.quote(diag)} >&2; return 1")

    assert result.returncode == 1, result.stderr
    fields = _contract_fields(result.stderr)
    assert f"in {project_cfg}:" in fields["message"], fields["message"]
    assert f"Set [release] deep_verify in {project_cfg} " in fields["remedy"], fields["remedy"]
    assert "kanban.cfg [release]" not in fields["remedy"]


def test_recognized_mode_passes_through_without_refusal(tmp_path):
    result = _run_branch(tmp_path, "echo weekly")

    assert result.returncode == 0, result.stderr
    assert "MODE=weekly" in result.stdout
    assert "deep_verify.invalid_schedule_mode" not in result.stderr


def test_resolver_diagnostic_carries_the_file_the_refusal_reads():
    source = _PROJECT_PATHS_SH.read_text(encoding="utf-8")
    body = source.split("pp_deep_verify_mode() {", 1)[1].split("\n}\n", 1)[0]
    assert "for project '${name}' in ${source_file} [release] deep_verify" in body, (
        "pp_deep_verify_mode's refusal no longer prints \"for project '<name>' in <file> "
        "[release] deep_verify\"; deep-verify.sh reads the file from that shape"
    )
