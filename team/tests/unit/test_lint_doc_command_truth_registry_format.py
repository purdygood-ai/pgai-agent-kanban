"""
test_lint_doc_command_truth_registry_format.py
==============================================
How team/scripts/lint_doc_command_truth.py's key-parity check reads the Key
Registry and the raw ``.cfg_example`` templates.

Covered:

  1. A registry key line with ONE space between key and status declares the
     key, and lint_config_key_coverage.py's registry-line pattern reads the
     same lines, so the two lints agree on which lines declare a key. Prose
     lines that open with a capitalised word and a status ("Status:
     REQUIRED = ...") declare nothing.
  2. A ``[models.<provider>]`` key-group line naming ``shipped value "X"``
     records X for that group; the other groups keep the section default.
     The real registry records the shipped model of each provider.
  3. In a raw template, a prose comment that opens with a bracket ("# [project]
     dev_tree_path is empty ...") does not re-home the keys below it; only an
     uncommented ``[section]`` line switches section.
  4. In a raw template, a commented ``# key = value`` line documents a
     registered key (no UNDOCUMENTED CONFIG KEY), and is never itself
     reported as an unknown key or a default mismatch.

The real tree's own key-parity count is the lint's to report, not a test's
to gate: the lint is quality-class.

Fixture content lives under pytest's tmp_path; the real-tree checks read
this checkout only.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_TEAM_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_doc_command_truth.py"
_COVERAGE_SCRIPT = _TEAM_DIR / "scripts" / "lint_config_key_coverage.py"
_REAL_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_lint = _load("lint_doc_command_truth_registry_format", _LINT_SCRIPT)
_coverage = _load("lint_config_key_coverage_registry_format", _COVERAGE_SCRIPT)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _registry_file(tmp_path: Path, name: str, lines: list[str]) -> Path:
    body = "#!/usr/bin/env bash\n# Key Registry\n# ------------\n" + "".join(f"{ln}\n" for ln in lines) + "\ntrue\n"
    return _write(tmp_path / name, body)


def _empty_census(tmp_path: Path) -> Path:
    rows = [
        "\t".join(["W", "0", "main", "-", "1"]),
        "\t".join(["P", "0", "-", "0", "single", "header.sh", "header.sh"]),
    ]
    return _write(tmp_path / "fixture.census", "# fixture census\n" + "\n".join(rows) + "\n")


def _key_parity_clean(out: str) -> bool:
    """True when the key-parity summary line reports zero findings.

    The fixture census leaves one window undescribed, so the lint's overall
    exit is non-zero by design; only the key-parity line is asserted on.
    """
    line = next((ln for ln in out.splitlines() if "key-parity:" in ln), "")
    return bool(re.search(r"; 0 finding\(s\)", line))


def _run(tmp_path: Path, doc: Path, config_loader: Path, project_registry: Path) -> int:
    return _lint.main(
        [
            "--repo-root", str(_REPO_ROOT),
            "--doc", str(doc),
            "--config-loader", str(config_loader),
            "--project-key-registry", str(project_registry),
            "--census", str(_empty_census(tmp_path)),
            "--help-timeout", "5",
        ]
    )


# ---------------------------------------------------------------------------
# 1. Registry line format: one space or padding, both lints agree
# ---------------------------------------------------------------------------


def test_single_space_registry_line_declares_its_key_in_both_lints(tmp_path) -> None:
    lines = [
        "# Status: REQUIRED = fail loud if absent.",
        "# [overwatch]",
        "#   block_triage            OPTIONAL  true",
        "#   leaked_listener_min_age_seconds OPTIONAL 3600 (seconds a listener",
        "#                                         must have run first)",
    ]
    registry = _lint._parse_config_loader_registry(_registry_file(tmp_path, "cl.sh", lines))

    assert registry[("overwatch", "leaked_listener_min_age_seconds")].default == "3600"
    assert registry[("overwatch", "block_triage")].default == "true"
    assert set(registry) == {("overwatch", "block_triage"), ("overwatch", "leaked_listener_min_age_seconds")}

    coverage_keys = {m.group(1) for ln in lines if (m := _coverage._REGISTRY_KEY_RE.match(ln))}
    assert coverage_keys == {key for _, key in registry}


def test_real_registry_declares_leaked_listener_min_age_seconds() -> None:
    registry = _lint._parse_config_loader_registry(_REAL_CONFIG_LOADER)
    assert registry[("overwatch", "leaked_listener_min_age_seconds")].default == "3600"
    assert not any(key.endswith(":") or not re.match(r"^[a-z_][a-z0-9_]*$", key) for _, key in registry)


# ---------------------------------------------------------------------------
# 2. [models.<provider>] shipped values
# ---------------------------------------------------------------------------


def test_models_line_shipped_value_overrides_section_default(tmp_path) -> None:
    lines = [
        '# [models.alpha]   — all OPTIONAL, default "" unless a line names a shipped value',
        '#   default                               — shipped value "alpha-model-1";',
        "#                                           fallback model for any unset role.",
        "#   pm, coder                            — per-role overrides",
    ]
    registry = _lint._parse_config_loader_registry(_registry_file(tmp_path, "cl.sh", lines))

    assert registry[("models.alpha", "default")].default == "alpha-model-1"
    assert registry[("models.alpha", "pm")].default == ""
    assert registry[("models.alpha", "coder")].default == ""


def test_real_registry_records_each_provider_shipped_model() -> None:
    registry = _lint._parse_config_loader_registry(_REAL_CONFIG_LOADER)
    template = (_REPO_ROOT / "kanban.cfg_example").read_text(encoding="utf-8")
    for provider in ("claude", "codex"):
        shipped = registry[(f"models.{provider}", "default")].default
        assert shipped, f"[models.{provider}] default records no shipped value"
        section = template.split(f"[models.{provider}]", 1)[1]
        assert re.search(rf"^default = {re.escape(shipped)}$", section, re.MULTILINE), (
            f"kanban.cfg_example [models.{provider}] does not ship default = {shipped}"
        )
        assert registry[(f"models.{provider}", "coder")].default == ""


# ---------------------------------------------------------------------------
# 3 and 4. Raw template: bracket-led prose and commented key lines
# ---------------------------------------------------------------------------


def _template_registries(tmp_path: Path) -> tuple[Path, Path]:
    config_loader = _registry_file(
        tmp_path,
        "cl.sh",
        [
            "# [paths]",
            "#   dev_tree_path           OPTIONAL  \"\"",
            "#   tmp_root                OPTIONAL  /tmp",
        ],
    )
    project_registry = _registry_file(
        tmp_path,
        "pp.sh",
        [
            "# [project]",
            "#   dev_tree_path           OPTIONAL  \"\"",
            "#   temp_dir                OPTIONAL  N/A",
        ],
    )
    return config_loader, project_registry


def test_bracket_led_prose_comment_does_not_switch_template_section(tmp_path, capsys) -> None:
    config_loader, project_registry = _template_registries(tmp_path)
    doc = _write(
        tmp_path / "fixture.cfg_example",
        "[paths]\n"
        "# A project's own project.cfg\n"
        "# [project] dev_tree_path is empty, so this one is used.\n"
        "dev_tree_path =\n"
        "tmp_root = /tmp\n"
        "[project]\n"
        "dev_tree_path =\n"
        "# temp_dir = projects/<project_name>\n",
    )

    _run(tmp_path, doc, config_loader, project_registry)
    out = capsys.readouterr().out

    assert "UNKNOWN CONFIG KEY" not in out, out
    assert _key_parity_clean(out), out


def test_commented_template_line_documents_a_registered_key(tmp_path, capsys) -> None:
    config_loader, project_registry = _template_registries(tmp_path)
    without_mention = _write(
        tmp_path / "bare.cfg_example",
        "[paths]\ndev_tree_path =\ntmp_root = /tmp\n[project]\ndev_tree_path =\n",
    )
    _run(tmp_path, without_mention, config_loader, project_registry)
    assert "UNDOCUMENTED CONFIG KEY: [project] temp_dir" in capsys.readouterr().out

    with_mention = _write(
        tmp_path / "commented.cfg_example",
        "[paths]\ndev_tree_path =\ntmp_root = /tmp\n[project]\ndev_tree_path =\n"
        "# temp_dir = projects/<project_name>\n",
    )
    _run(tmp_path, with_mention, config_loader, project_registry)
    out = capsys.readouterr().out
    assert "UNDOCUMENTED CONFIG KEY" not in out, out
    assert _key_parity_clean(out), out


def test_commented_template_line_is_never_an_unknown_key_or_mismatch(tmp_path, capsys) -> None:
    config_loader, project_registry = _template_registries(tmp_path)
    doc = _write(
        tmp_path / "fixture.cfg_example",
        "[paths]\ndev_tree_path =\n# tmp_root = /var/tmp\n# invented_key = 1\n"
        "tmp_root = /tmp\n[project]\ndev_tree_path =\n# temp_dir = x\n",
    )

    _run(tmp_path, doc, config_loader, project_registry)
    out = capsys.readouterr().out

    assert "invented_key" not in out, out
    assert "CONFIG DEFAULT MISMATCH" not in out, out
    assert _key_parity_clean(out), out

