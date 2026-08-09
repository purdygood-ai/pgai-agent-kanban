"""
test_wake_model_override_precedence.py
=======================================
Behavioral fixtures for the wake scripts' per-task model resolution.

Both wake providers (scripts/wake/claude.sh, scripts/wake/codex.sh) resolve
which model string to pass to the agent CLI for a given task using a 3-tier
precedence chain (documented in SOP.md "Model Override Mechanism", highest
wins):

  1. The task README's ## Model Override field
  2. The PGAI_<ROLE>_MODEL environment variable
  3. The provider's own CLI default (no override supplied; empty string)

Acceptance criteria exercised:

  (1) Each tier wins when every higher tier is absent — tier 1 alone,
      tier 2 alone, and tier 3 as the empty fallback.

  (2) The task override (tier 1) wins even when a PGAI_<ROLE>_MODEL env
      var (tier 2) is also present — the two tiers do not silently swap
      priority.

  (3) A task README ## Model Override value of "none" is treated as
      absent, so resolution falls through to the env tier exactly as it
      would if the field were omitted entirely.

  (4) Sibling parity: the extracted Model Selection block is byte-identical
      across scripts/wake/claude.sh and scripts/wake/codex.sh, so both
      providers share one resolution order rather than drifting apart.

The Model Selection block is extracted from each provider script by its
anchor comment and exercised in a stubbed bash harness — the same
extraction technique already used for the pre-dispatch assertion block in
test_wake_dispatch_role_assignment.py — so the fixtures run the real
production logic rather than a reimplementation of it.

All tests use synthetic environments and never touch the live kanban root.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see SOP.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to team/, which is the CWD when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------
_WAKE_COMMON_LIB = pathlib.Path("scripts/lib/wake_common.sh")
_CLAUDE_SH = pathlib.Path("scripts/wake/claude.sh")
_CODEX_SH = pathlib.Path("scripts/wake/codex.sh")

_PROVIDER_SCRIPTS = {"claude": _CLAUDE_SH, "codex": _CODEX_SH}

# Anchor comment that begins the Model Selection block in both siblings.
_MODEL_SELECTION_ANCHOR = "# --- Model Selection ---"


def _awk_extract_function(name: str, source: pathlib.Path) -> str:
    """Return an awk program (as a bash command substitution) that extracts
    a single bash function definition by name from *source*, matched by
    brace depth so the extraction stops at the function's own closing brace.

    Mirrors the extraction technique already used elsewhere in this suite
    to source individual wake_common.sh functions without triggering the
    full file's module-level bootstrap side effects.
    """
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^{name}\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{
                        depth--
                        if (depth == 0) {{ found=0; exit }}
                    }}
                }}
            }}
        ' '{source}')"
    """)


def _extract_model_selection_block(path: pathlib.Path) -> list[str]:
    """Extract the Model Selection block from a wake provider script.

    The block starts at the anchor comment and ends at the first
    top-level ``fi`` (two-space indent, matching process_one_task's body
    indentation) that closes the tier-resolution if/elif/else.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    capturing = False
    block: list[str] = []
    for line in lines:
        if _MODEL_SELECTION_ANCHOR in line:
            capturing = True
        if capturing:
            block.append(line)
            if line.strip() == "fi" and len(block) > 5:
                break
    assert block, f"Model Selection anchor not found in {path}"
    return block


def _write_readme(tmp_path: pathlib.Path, *, override: str | None) -> pathlib.Path:
    """Write a minimal task README, optionally carrying ## Model Override.

    override=None omits the section entirely (distinct from an explicit
    "none" value, which is exercised separately).
    """
    lines = ["# Task: synthetic fixture", "", "## Task ID", "SYNTH-000-fixture", ""]
    if override is not None:
        lines += ["## Model Override", override, ""]
    readme = tmp_path / "README.md"
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return readme


def _run_model_selection(
    tmp_path: pathlib.Path,
    provider: str,
    *,
    override: str | None,
    env_model: str | None,
    agent: str = "CODER",
    subagent: str = "coder",
    active_provider: str = "claude",
) -> dict[str, str]:
    """Run the extracted Model Selection block from *provider*'s script.

    Returns a dict with the resolved 'selected_model' and 'model_source'
    outputs of the block.
    """
    script_path = _PROVIDER_SCRIPTS[provider]
    block_lines = _extract_model_selection_block(script_path)
    block_text = "\n".join(block_lines)

    readme = _write_readme(tmp_path, override=override)

    env_var_name = f"PGAI_{agent.upper()}_MODEL"
    if env_model is not None:
        env_line = f'export {env_var_name}="{env_model}"'
    else:
        env_line = f"unset {env_var_name} 2>/dev/null || true"

    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        {_awk_extract_function("get_model_override_from_readme", _WAKE_COMMON_LIB)}

        log() {{ :; }}

        task_readme="{readme}"
        AGENT="{agent}"
        ACTIVE_PROVIDER="{active_provider}"
        SUBAGENT="{subagent}"
        {env_line}

        _run_selection() {{
{block_text}
          echo "SELECTED_MODEL=[${{selected_model:-}}]"
          echo "MODEL_SOURCE=[${{model_source:-}}]"
        }}
        _run_selection
    """)

    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, (
        f"Model Selection block exited non-zero for provider={provider!r}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if line.startswith("SELECTED_MODEL=[") and line.endswith("]"):
            parsed["selected_model"] = line[len("SELECTED_MODEL=[") : -1]
        elif line.startswith("MODEL_SOURCE=[") and line.endswith("]"):
            parsed["model_source"] = line[len("MODEL_SOURCE=[") : -1]
    assert "selected_model" in parsed and "model_source" in parsed, (
        f"Could not parse block output for provider={provider!r}.\n"
        f"stdout: {result.stdout}"
    )
    return parsed


# ---------------------------------------------------------------------------
# (1), (2) & (3) Tier precedence: each tier wins when higher tiers are
#     absent; the task override wins even when the env var is also present;
#     an explicit "none" override value falls through like an absent field.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize(
    "override, env_model, expected_selected, expected_source",
    [
        pytest.param(
            "claude-opus-4-7", "claude-haiku-4-0", "claude-opus-4-7", "task force override",
            id="task_override_wins_when_env_var_also_present",
        ),
        pytest.param(
            "opus", None, "opus", "task force override",
            id="task_override_wins_when_env_var_absent",
        ),
        pytest.param(
            None, "claude-sonnet-4-6", "claude-sonnet-4-6", "PGAI_CODER_MODEL env",
            id="env_var_wins_when_task_override_absent",
        ),
        pytest.param(
            None, None, "", "subagent default for coder",
            id="provider_default_wins_when_no_override_or_env",
        ),
        pytest.param(
            "none", "claude-sonnet-4-6", "claude-sonnet-4-6", "PGAI_CODER_MODEL env",
            id="task_override_none_value_falls_through_to_env",
        ),
    ],
)
def test_model_selection_tier_precedence(
    tmp_path: pathlib.Path,
    provider: str,
    override: str | None,
    env_model: str | None,
    expected_selected: str,
    expected_source: str,
) -> None:
    """Model resolution honors the 3-tier precedence (task override > env var
    > provider default), with an explicit 'none' override value treated as
    absent rather than as a literal model name."""
    result = _run_model_selection(
        tmp_path, provider, override=override, env_model=env_model
    )
    assert result["selected_model"] == expected_selected, result
    assert result["model_source"] == expected_source, result


# ---------------------------------------------------------------------------
# (4) Sibling parity: the Model Selection block is byte-identical across
#     both providers.
# ---------------------------------------------------------------------------


def test_sibling_model_selection_blocks_are_byte_identical() -> None:
    """The Model Selection block must be byte-identical across siblings.

    Extraction: locate _MODEL_SELECTION_ANCHOR in each file and capture
    through the closing 'fi'. The two blocks must match byte-for-byte so
    both providers share one resolution order.
    """
    claude_block = _extract_model_selection_block(_CLAUDE_SH)
    codex_block = _extract_model_selection_block(_CODEX_SH)

    assert claude_block == codex_block, (
        "Model Selection blocks differ between siblings.\n"
        "claude.sh block:\n" + "\n".join(claude_block) + "\n\n"
        "codex.sh block:\n" + "\n".join(codex_block)
    )
