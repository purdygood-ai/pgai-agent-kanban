#!/usr/bin/env python3
"""
generate_operator_verb_docs.py
===============================
Renders the two census-derived reference sections -- the "Verb Reference
(generated)" section of `docs/operator-commands.md` and the "Census-derived
route reference (generated)" section of `docs/operator-api.md` -- from
`team/config/operator_verbs.manifest` (the operator verb census), read only
through its sanctioned Python reader
(`team/pgai_agent_kanban/lib/operator_verb_manifest.py`). In `--check` mode,
refuses when either rendered section has drifted from what's on disk, so a
census row edited without regenerating fails the doc-truth pre-flight the
gated runners carry.

Sources, per section:

  docs/operator-commands.md  One block per active census row, in census
                             order: the verb's script, mutates/idempotence
                             facts, read|action class and REST twin (all
                             and implementation binding (all from the
                             census row), plus the verb's own
                             `--help` output captured by invoking the
                             script. Usage text comes from `--help`, not
                             the census's `flags` column, so the documented
                             invocation is the invocation the verb itself
                             reports -- there is no second transcription of
                             the flag vocabulary to go stale. The census
                             supplies what `--help` cannot: behavioural
                             classification.

  docs/operator-api.md       One table row per active census row: verb,
                             read|action class, REST method, route, the
                             route's parameters, request body model,
                             response schema reference, and implementation
                             binding. Class, method, route, body model,
                             schema reference and implementation come from
                             the census row. The parameter list is
                             resolved from the checked-in ICD
                             (`docs/api/icd.json`) for that row's route --
                             query/path parameters for a GET route, the
                             body model's fields for a POST route, required
                             ones marked -- so the reference names exactly
                             the HTTP parameters the published contract
                             declares. A census route the ICD does not carry
                             renders an explicit "not in the ICD" cell
                             naming the regeneration command rather than
                             guessing.

Block-boundary rule (read this before editing either document's
neighbourhood):

    Each document carries exactly ONE section-level marker pair --
    `<!-- OPERATOR-VERBS-GENERATED:BEGIN section=verb-reference -->` /
    `... END section=verb-reference -->` in docs/operator-commands.md and
    the same pair with `section=api-reference` in docs/operator-api.md.
    Everything between a pair is generated and overwritten in full on every
    render, including the per-verb `OPERATOR-VERBS-GENERATED:BEGIN verb=...`
    markers inside the verb-reference section (they are emitted so --check
    can name the verb whose block drifted; they are not a hand-maintained
    boundary). Adding a row to the census is therefore the ONLY edit a new
    verb needs to appear in both references -- this script never requires a
    per-verb marker pair to be added by hand. The section's own heading and
    every hand-written paragraph sit OUTSIDE the pair and are never touched,
    which keeps the boundary a WRITER can work around.

This generator does not re-check the census against the tree it describes
(script existence, flag-parity, REST-twin existence) --
`team/scripts/lint_operator_verb_manifest.py` and
`team/scripts/lint_api_parity.py` own those checks and this script defers
to them entirely rather than reporting a second, differently-worded
version of the same finding.

Usage:
    python3 generate_operator_verb_docs.py            # render: write both docs
    python3 generate_operator_verb_docs.py --check     # verify: report drift, don't write

Options:
    --check              Compare both rendered sections against what's on
                          disk; print every disagreement and exit 1 without
                          writing anything. Exit 0 when everything already
                          matches.
    --manifest PATH      Census file. Default: team/config/operator_verbs.manifest.
    --doc PATH           docs/operator-commands.md file to render/check.
    --api-doc PATH       docs/operator-api.md file to render/check.
    --icd PATH           Checked-in ICD artifact the API reference resolves
                          route parameters from. Default: docs/api/icd.json.
    --team-dir PATH      Root of the team/ directory (scripts are invoked
                          relative to this). Default: this file's own team/.
    --timeout SECONDS    Per-verb --help subprocess timeout. Default: 10.
    --help, -h           Show this help and exit.

Exit codes:
    0   Render succeeded, or --check found no drift.
    1   --check found one or more disagreements (CONVERT: documentation
        drift carries zero runtime-failure risk -- see the refusal site
        below for the full classification rationale).
    2   Usage error, missing/malformed census, missing doc or ICD file,
        a document missing its section marker pair (a structural problem
        the render can't repair by itself), or PGAI_AGENT_KANBAN_ROOT_PATH
        unusable -- unset, empty, or naming a directory that does not
        exist (see below).

Environment:
    PGAI_AGENT_KANBAN_ROOT_PATH must name an existing directory: every shell
    verb resolves it before answering --help, and several refuse with
    "kanban root not found" when the directory is missing. When it is unset
    or empty, when it names a path that is not an existing directory, or
    when the first shell verb's --help refuses for want of it, the run
    refuses with exit 2 before reporting any drift or writing anything (in
    --check and render mode alike), names the variable and the path, and
    gives the export to run -- rather than reporting the refusing verbs as
    drifted or overwriting their blocks with the refusal. A relative value
    is resolved against --team-dir, where each verb's --help runs. From a
    checkout the tree's own team/ directory works:
        PGAI_AGENT_KANBAN_ROOT_PATH="$PWD/team" python3 team/scripts/generate_operator_verb_docs.py --check
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Dev-tree root resolution -- each tree finds itself.
# This file lives at team/scripts/generate_operator_verb_docs.py.
# Two parent levels: scripts/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent
_DEV_TREE_ROOT = _TEAM_DIR.parent

_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)

from pgai_agent_kanban.lib import operator_verb_manifest as ovm  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402

_DEFAULT_DOC = _DEV_TREE_ROOT / "docs" / "operator-commands.md"
_DEFAULT_API_DOC = _DEV_TREE_ROOT / "docs" / "operator-api.md"
_DEFAULT_ICD = _DEV_TREE_ROOT / "docs" / "api" / "icd.json"
_DEFAULT_TIMEOUT = 10.0

_MARKER_NAMESPACE = "OPERATOR-VERBS-GENERATED"

# Section names: one marker pair per document.
VERB_REFERENCE_SECTION = "verb-reference"
API_REFERENCE_SECTION = "api-reference"

# The census's own "no value" markers for its REST-describing columns.
_ABSENT = "ABSENT"
_NONE = "NONE"

_REGENERATE_COMMAND = "python3 team/scripts/generate_operator_verb_docs.py"
_ICD_REGENERATE_COMMAND = "bash team/scripts/generate-icd.sh"

# Every shell verb resolves this variable through lib/env_bootstrap.sh before
# it answers --help; without it each one refuses instead of printing usage.
_ROOT_ENV_VAR = "PGAI_AGENT_KANBAN_ROOT_PATH"
# env_bootstrap.sh's refusal, as it reaches a capture's reason (first stderr line).
_SHELL_ENV_REFUSAL_RE = re.compile(
    r"PGAI_AGENT_KANBAN_ROOT_PATH\s+not\s+set|shell-env\s+missing", re.IGNORECASE
)


def _begin_marker(verb_name: str) -> str:
    return f"<!-- {_MARKER_NAMESPACE}:BEGIN verb={verb_name} -->"


def _end_marker(verb_name: str) -> str:
    return f"<!-- {_MARKER_NAMESPACE}:END verb={verb_name} -->"


def section_begin_marker(section: str) -> str:
    """Return the HTML comment that opens the generated block for *section*."""
    return f"<!-- {_MARKER_NAMESPACE}:BEGIN section={section} -->"


def section_end_marker(section: str) -> str:
    """Return the HTML comment that closes the generated block for *section*."""
    return f"<!-- {_MARKER_NAMESPACE}:END section={section} -->"


_VERB_BEGIN_RE = re.compile(
    re.escape(f"<!-- {_MARKER_NAMESPACE}:BEGIN verb=") + r"([^\s>]+)\s*-->"
)


# ---------------------------------------------------------------------------
# --help capture
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HelpCapture:
    """The outcome of invoking one verb's script with --help.

    ok=True carries the captured stdout in `text`. ok=False carries a
    human-readable `reason` and no text -- the render never falls back to
    the census's `flags` column when capture fails; it says so instead.
    """

    ok: bool
    text: Optional[str]
    reason: Optional[str]


def _help_command(verb: ovm.Verb, team_dir: pathlib.Path) -> list[str]:
    script_path = team_dir / verb.script
    if script_path.suffix == ".py":
        return [sys.executable, str(script_path), "--help"]
    return ["bash", str(script_path), "--help"]


def capture_help(
    verb: ovm.Verb, team_dir: pathlib.Path, timeout: float = _DEFAULT_TIMEOUT
) -> HelpCapture:
    """Invoke *verb*'s own script with --help and return its output.

    Never raises: every failure mode (missing script, non-zero exit,
    timeout, unlaunchable interpreter) is folded into a HelpCapture(ok=False,
    reason=...) so the caller can render an honest "unavailable" note
    instead of guessing.
    """
    script_path = team_dir / verb.script
    if not script_path.is_file():
        return HelpCapture(False, None, f"script not found at {script_path}")

    command = _help_command(verb, team_dir)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(team_dir),
        )
    except subprocess.TimeoutExpired:
        return HelpCapture(False, None, f"--help timed out after {timeout}s")
    except OSError as exc:
        return HelpCapture(False, None, f"failed to invoke --help: {exc}")

    if result.returncode != 0:
        stderr_excerpt = result.stderr.strip().splitlines()[:1]
        detail = stderr_excerpt[0] if stderr_excerpt else "(no stderr)"
        return HelpCapture(
            False, None, f"--help exited {result.returncode}: {detail}"
        )

    # Prefer stdout -- the convention every verb but one follows. One verb
    # (set-version-ceiling.sh) writes its usage text to stderr instead of
    # stdout; falling back to stderr when stdout is empty still captures
    # that verb's own --help output verbatim (not a substitute derived from
    # the census), it just reads the fd the script actually used.
    text = result.stdout.strip("\n") or result.stderr.strip("\n")
    if not text:
        return HelpCapture(False, None, "--help printed no output on stdout or stderr")
    return HelpCapture(True, text, None)


def _is_shell_verb(verb: ovm.Verb) -> bool:
    """True when *verb*'s script runs under bash (and so through env_bootstrap.sh)."""
    return pathlib.PurePath(verb.script).suffix != ".py"


class ShellEnvUnavailableError(RuntimeError):
    """A shell verb's --help refused because the kanban root variable is missing.

    Every other shell verb would refuse the same way, so rendering on would
    replace each real block with an "unavailable" stub.
    """

    def __init__(self, verb_name: str, reason: str) -> None:
        super().__init__(f"verb={verb_name!r}: {reason}")
        self.verb_name = verb_name
        self.reason = reason


# ---------------------------------------------------------------------------
# Rendering: the verb reference (docs/operator-commands.md)
# ---------------------------------------------------------------------------


def _mutates_label(mutates: Optional[bool]) -> str:
    if mutates is True:
        return "yes"
    if mutates is False:
        return "no"
    return "-"


def _rest_twin_label(verb: ovm.Verb) -> str:
    if verb.rest_route == _ABSENT or verb.rest_method == _ABSENT:
        return "none"
    return f"`{verb.rest_method} {verb.rest_route}`"


def _impl_label(verb: ovm.Verb) -> str:
    """Render the census row's ``impl`` value verbatim, or "-" when absent."""
    if not verb.impl:
        return "-"
    return f"`{verb.impl}`"


def render_verb_block(verb: ovm.Verb, capture: HelpCapture) -> str:
    """Render one verb's full BEGIN/END marker block (both inclusive).

    Everything in the returned string is generated: the heading, the
    script/mutates/idempotence/class/REST-twin/implementation facts
    (sourced from the census row), and either the captured --help text or an explicit
    unavailable notice.
    """
    lines = [_begin_marker(verb.name)]
    lines.append(f"#### `{verb.name}`")
    lines.append("")
    lines.append(
        f"Generated from `{verb.script} --help` and this verb's census row. "
        f"Do not hand-edit between these markers; run `{_REGENERATE_COMMAND}` "
        "(from the repo root) and commit the result instead."
    )
    lines.append("")
    lines.append(f"- **Script:** `{verb.script}`")
    lines.append(f"- **Mutates state:** {_mutates_label(verb.mutates)}")
    lines.append(f"- **Idempotence:** {verb.idempotence}")
    lines.append(f"- **Class:** {verb.verb_class}")
    lines.append(f"- **REST twin:** {_rest_twin_label(verb)}")
    lines.append(f"- **Implementation:** {_impl_label(verb)}")
    lines.append("")
    if capture.ok:
        lines.append("```text")
        lines.append(capture.text)
        lines.append("```")
    else:
        lines.append(
            f"`--help` could not be captured for this verb ({capture.reason}). "
            "No flag vocabulary is shown here as a substitute -- the "
            "census's `flags` column is not a source for this section by "
            "design; see the script itself."
        )
    lines.append(_end_marker(verb.name))
    return "\n".join(lines)


def render_verb_reference_section(
    active_verbs: list[ovm.Verb], captures: dict[str, HelpCapture]
) -> str:
    """Render the whole verb-reference section (section markers inclusive).

    One block per active verb in census order, blank-line separated.
    """
    blocks = [render_verb_block(v, captures[v.name]) for v in active_verbs]
    return "\n".join(
        [section_begin_marker(VERB_REFERENCE_SECTION), "\n\n".join(blocks),
         section_end_marker(VERB_REFERENCE_SECTION)]
    )


# ---------------------------------------------------------------------------
# Rendering: the API reference (docs/operator-api.md)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteParameters:
    """The HTTP parameters the ICD declares for one census row's route.

    ok=False means the ICD does not carry the route (or its body model):
    `reason` says which, and the table cell names the regeneration command
    instead of a guessed parameter list.
    """

    ok: bool
    names: tuple[str, ...] = ()
    required: frozenset[str] = field(default_factory=frozenset)
    reason: Optional[str] = None


def load_icd(icd_path: pathlib.Path) -> dict:
    """Load the checked-in ICD artifact.

    Raises:
        FileNotFoundError: the artifact does not exist at *icd_path*.
        ValueError: the file is not a JSON object (a corrupt artifact).
    """
    if not icd_path.is_file():
        raise FileNotFoundError(f"ICD artifact not found: {icd_path}")
    document = json.loads(icd_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{icd_path} does not contain a JSON object")
    return document


def _schema_ref_name(schema: dict) -> Optional[str]:
    ref = schema.get("$ref", "")
    return ref.rsplit("/", 1)[-1] if ref else None


def resolve_route_parameters(icd: dict, method: str, route: str) -> RouteParameters:
    """Return the parameter names the ICD declares for ``METHOD route``.

    A GET route's parameters are its declared query/path parameters; a POST
    route's are its JSON body model's fields, looked up through the
    ``requestBody`` schema reference into ``components.schemas``. Required
    names are carried separately so the renderer can mark them.
    """
    operation = icd.get("paths", {}).get(route, {}).get(method.lower())
    if operation is None:
        return RouteParameters(False, reason=f"{method} {route} is not in the ICD")

    names: list[str] = []
    required: set[str] = set()
    for parameter in operation.get("parameters", []):
        name = parameter.get("name")
        if not name:
            continue
        names.append(name)
        if parameter.get("required"):
            required.add(name)

    body_schema = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    model_name = _schema_ref_name(body_schema)
    if model_name is not None:
        model = icd.get("components", {}).get("schemas", {}).get(model_name)
        if model is None:
            return RouteParameters(
                False, reason=f"body model {model_name} is not in the ICD's components"
            )
        properties = model.get("properties", {})
        model_required = set(model.get("required", []))
        # Required fields first, then optional, each in the ICD's own
        # (sorted) key order -- a stable layout a reader can scan.
        for name in sorted(properties):
            if name in model_required:
                names.append(name)
                required.add(name)
        for name in sorted(properties):
            if name not in model_required:
                names.append(name)

    return RouteParameters(True, tuple(names), frozenset(required))


def _parameters_cell(parameters: RouteParameters) -> str:
    if not parameters.ok:
        return f"not in `docs/api/icd.json` ({parameters.reason}; run `{_ICD_REGENERATE_COMMAND}`)"
    if not parameters.names:
        return "none"
    rendered = []
    for name in parameters.names:
        suffix = " (required)" if name in parameters.required else ""
        rendered.append(f"`{name}`{suffix}")
    return ", ".join(rendered)


def _body_model_cell(verb: ovm.Verb) -> str:
    if verb.rest_body_model in (_ABSENT, _NONE):
        return "none"
    return f"`{verb.rest_body_model}`"


def render_api_row(verb: ovm.Verb, parameters: RouteParameters) -> str:
    """Render one census row as one markdown table row."""
    return " | ".join(
        [
            f"| `{verb.name}`",
            verb.verb_class,
            f"`{verb.rest_method}`",
            f"`{verb.rest_route}`",
            _parameters_cell(parameters),
            _body_model_cell(verb),
            f"`{verb.schema_ref}`",
            f"`{verb.script}`",
            f"{_impl_label(verb)} |",
        ]
    )


def render_api_reference_section(active_verbs: list[ovm.Verb], icd: dict) -> str:
    """Render the whole API-reference section (section markers inclusive).

    Every active census row appears exactly once: rows with a REST twin as
    a table row, rows without one in the trailing list, so a census row
    can never be silently absent from the reference.
    """
    lines = [section_begin_marker(API_REFERENCE_SECTION)]
    lines.append(
        f"Generated from every active row of `team/config/operator_verbs.manifest` "
        f"(parameters resolved from `docs/api/icd.json`). Do not hand-edit "
        f"between these markers; run `{_REGENERATE_COMMAND}` (from the repo "
        "root) and commit the result instead."
    )
    lines.append("")
    lines.append(
        "| Verb | Class | Method | Route | Parameters | Body model | Response schema | Script | Implementation |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    without_twin: list[ovm.Verb] = []
    for verb in active_verbs:
        if verb.rest_route == _ABSENT or verb.rest_method == _ABSENT:
            without_twin.append(verb)
            continue
        parameters = resolve_route_parameters(icd, verb.rest_method, verb.rest_route)
        lines.append(render_api_row(verb, parameters))
    lines.append("")
    if without_twin:
        lines.append("Active census verbs with no REST twin yet:")
        lines.append("")
        for verb in without_twin:
            lines.append(f"- `{verb.name}` (`{verb.script}`, class {verb.verb_class})")
    else:
        lines.append("Every active census verb has a REST twin.")
    lines.append(section_end_marker(API_REFERENCE_SECTION))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Marker-pair replace/extract
# ---------------------------------------------------------------------------


class MarkerNotFoundError(ValueError):
    """A required BEGIN/END marker pair is missing from the target doc."""


def _replace_between_markers(
    content: str, begin_marker: str, end_marker: str, new_inner: str
) -> str:
    begin_idx = content.find(begin_marker)
    if begin_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {begin_marker!r}")
    end_idx = content.find(end_marker, begin_idx)
    if end_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {end_marker!r}")
    end_idx += len(end_marker)
    return content[:begin_idx] + new_inner + content[end_idx:]


def _extract_between_markers(content: str, begin_marker: str, end_marker: str) -> str:
    begin_idx = content.find(begin_marker)
    if begin_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {begin_marker!r}")
    end_idx = content.find(end_marker, begin_idx)
    if end_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {end_marker!r}")
    end_idx += len(end_marker)
    return content[begin_idx:end_idx]


def _replace_section(doc_path: pathlib.Path, content: str, section: str, rendered: str) -> str:
    begin = section_begin_marker(section)
    end = section_end_marker(section)
    try:
        return _replace_between_markers(content, begin, end, rendered)
    except MarkerNotFoundError:
        raise MarkerNotFoundError(
            f"{doc_path} is missing the section marker pair {begin!r} / {end!r}; "
            "add both markers on their own lines where the generated section "
            "belongs and re-run."
        ) from None


# ---------------------------------------------------------------------------
# Render / check over both docs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocRender:
    """One document's on-disk content and its freshly rendered counterpart."""

    path: pathlib.Path
    section: str
    current: str
    rendered: str


@dataclass(frozen=True)
class RenderResult:
    """Everything one render pass produced.

    The verb reference and API reference documents (each on-disk and freshly
    rendered), the manifest verbs that are active, and each verb's ``--help``
    capture keyed by verb name.
    """
    verb_doc: DocRender
    api_doc: DocRender
    active_verbs: list[ovm.Verb]
    captures: dict[str, HelpCapture]


def render_docs(
    verbs: dict[str, ovm.Verb],
    team_dir: pathlib.Path,
    doc_path: pathlib.Path,
    api_doc_path: pathlib.Path,
    icd: dict,
    timeout: float,
) -> RenderResult:
    """Return the current and rendered content of both documents, plus the
    per-verb --help captures used to build the verb reference.

    Raises:
        FileNotFoundError: a document does not exist.
        MarkerNotFoundError: a document has no section marker pair.
        ShellEnvUnavailableError: the first shell verb's --help refused for
            want of the kanban root variable; nothing further is captured.
    """
    active_verbs = [v for v in verbs.values() if v.status == "active"]

    captures: dict[str, HelpCapture] = {}
    first_shell_checked = False
    for verb in active_verbs:
        capture = capture_help(verb, team_dir, timeout)
        if _is_shell_verb(verb) and not first_shell_checked:
            first_shell_checked = True
            if not capture.ok and _SHELL_ENV_REFUSAL_RE.search(capture.reason or ""):
                raise ShellEnvUnavailableError(verb.name, capture.reason or "")
        captures[verb.name] = capture

    verb_current = doc_path.read_text(encoding="utf-8")
    verb_rendered = _replace_section(
        doc_path, verb_current, VERB_REFERENCE_SECTION,
        render_verb_reference_section(active_verbs, captures),
    )

    api_current = api_doc_path.read_text(encoding="utf-8")
    api_rendered = _replace_section(
        api_doc_path, api_current, API_REFERENCE_SECTION,
        render_api_reference_section(active_verbs, icd),
    )

    return RenderResult(
        DocRender(doc_path, VERB_REFERENCE_SECTION, verb_current, verb_rendered),
        DocRender(api_doc_path, API_REFERENCE_SECTION, api_current, api_rendered),
        active_verbs,
        captures,
    )


def _verb_reference_disagreements(doc: DocRender, active_verbs: list[ovm.Verb]) -> list[str]:
    """Name every way the on-disk verb-reference section differs from the
    rendered one: per-verb block drift, a census row with no block on disk,
    a block on disk for a verb the census no longer lists, and -- when the
    blocks all agree but the section still differs -- text between blocks.
    """
    current_section = _extract_between_markers(
        doc.current, section_begin_marker(doc.section), section_end_marker(doc.section)
    )
    rendered_section = _extract_between_markers(
        doc.rendered, section_begin_marker(doc.section), section_end_marker(doc.section)
    )
    if current_section == rendered_section:
        return []

    disagreements: list[str] = []
    active_names = {v.name for v in active_verbs}
    for verb in active_verbs:
        begin = _begin_marker(verb.name)
        end = _end_marker(verb.name)
        rendered_block = _extract_between_markers(rendered_section, begin, end)
        try:
            current_block = _extract_between_markers(current_section, begin, end)
        except MarkerNotFoundError:
            disagreements.append(
                f"verb={verb.name!r} has no block in {doc.path} (census row "
                "present, section not regenerated)"
            )
            continue
        if current_block != rendered_block:
            disagreements.append(
                f"verb={verb.name!r} block in {doc.path} disagrees with its census "
                f"row and/or its --help output (marker pair {begin!r} / {end!r})"
            )
    for stale in _VERB_BEGIN_RE.findall(current_section):
        if stale not in active_names:
            disagreements.append(
                f"verb={stale!r} has a block in {doc.path} but no active census row"
            )
    if not disagreements:
        disagreements.append(
            f"section={doc.section!r} in {doc.path} differs outside the per-verb blocks"
        )
    return disagreements


def _api_reference_disagreements(doc: DocRender) -> list[str]:
    current_section = _extract_between_markers(
        doc.current, section_begin_marker(doc.section), section_end_marker(doc.section)
    )
    rendered_section = _extract_between_markers(
        doc.rendered, section_begin_marker(doc.section), section_end_marker(doc.section)
    )
    if current_section == rendered_section:
        return []
    current_rows = {line for line in current_section.splitlines() if line.startswith("| `")}
    rendered_rows = {line for line in rendered_section.splitlines() if line.startswith("| `")}
    changed = sorted(
        line.split("|")[1].strip().strip("`")
        for line in current_rows.symmetric_difference(rendered_rows)
    )
    detail = (
        f" (rows differing: {', '.join(dict.fromkeys(changed))})" if changed else ""
    )
    return [
        f"section={doc.section!r} in {doc.path} disagrees with the census and "
        f"the ICD{detail}"
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="generate_operator_verb_docs.py",
        description=(
            "Render (or, with --check, verify) the census-derived reference "
            "sections of docs/operator-commands.md and docs/operator-api.md "
            "from team/config/operator_verbs.manifest, each active verb's own "
            "--help output, and docs/api/icd.json."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify only -- report drift and exit non-zero, write nothing.",
    )
    parser.add_argument("--manifest", metavar="PATH", help="Census (manifest) file path.")
    parser.add_argument("--doc", metavar="PATH", help="docs/operator-commands.md path.")
    parser.add_argument("--api-doc", metavar="PATH", help="docs/operator-api.md path.")
    parser.add_argument(
        "--icd", metavar="PATH",
        help="Checked-in ICD artifact (docs/api/icd.json) route parameters are resolved from.",
    )
    parser.add_argument(
        "--team-dir", metavar="PATH", help="Root of the team/ directory."
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"Per-verb --help subprocess timeout. Default: {_DEFAULT_TIMEOUT}.",
    )
    return parser.parse_args(argv)


def _failed_captures(captures: dict[str, HelpCapture]) -> list[tuple[str, str]]:
    return [
        (name, capture.reason)
        for name, capture in captures.items()
        if not capture.ok
    ]


def _write_if_changed(doc: DocRender, verb_names: list[str]) -> None:
    if doc.rendered != doc.current:
        doc.path.write_text(doc.rendered, encoding="utf-8")
        print(
            f"generate_operator_verb_docs: wrote {doc.path} "
            f"({len(verb_names)} verb(s) rendered: {', '.join(verb_names)})"
        )
    else:
        print(
            f"generate_operator_verb_docs: {doc.path} already up to date "
            f"({len(verb_names)} verb(s): {', '.join(verb_names)})"
        )


def _missing_root_cause(root_value: str, team_dir: pathlib.Path) -> Optional[str]:
    """Describe why *root_value* is not a usable kanban root, or None if it is.

    The value is resolved the way each verb's --help sees it: verbs run with
    *team_dir* as their working directory, so a relative value is joined to
    it (an absolute value is used as is).
    """
    resolved = team_dir / root_value
    if resolved.is_dir():
        return None
    where = "" if pathlib.PurePath(root_value).is_absolute() else f", resolved to {resolved}"
    return f"it names {root_value}{where}, which is not an existing directory"


def _refuse_shell_env(
    team_dir: pathlib.Path,
    argv: list[str],
    cause: str,
    error_key: str = "operator-verb-docs.root-path-unset",
) -> int:
    """Refuse the whole run because shell verbs cannot answer --help here.

    Printed instead of any drift report or write: one dual-channel refusal
    naming the variable, why it is unusable (*cause*, which names the path
    when one was given), and a command that re-runs this invocation with it
    exported to this tree's own team/ directory. *error_key* separates an
    unset variable (the default) from one naming a missing directory.
    """
    rerun = shlex.join([sys.executable, str(_THIS_FILE), *argv])
    # BLOCK-WORTHY: with the kanban root variable unset, or naming a
    # directory that does not exist, shell verbs' --help refuses, so --check
    # cannot verify anything (verification unable to run) and a render would
    # overwrite real blocks with a stub or the refusal text.
    emit_error_contract(
        error_key,
        (
            f"{_ROOT_ENV_VAR} is not usable in this shell ({cause}), so shell "
            "verbs cannot print their --help; no drift was checked and nothing "
            f"was written. Export {_ROOT_ENV_VAR} as an existing kanban root "
            "and re-run."
        ),
        f"export {_ROOT_ENV_VAR}={shlex.quote(str(team_dir))} && {rerun}",
    )
    # CENSUS-ANCHOR: generate-operator-verb-docs-root-path-unset-01
    return 2


def main(argv: list[str] | None = None) -> int:
    """Run the operator-verb docs generator: render both references, or with ``--check`` compare.

    Exit codes: 0 rendered, or ``--check`` found no drift; 1 ``--check`` found
    one or more disagreements (printed; nothing written); 2 usage error, a
    missing or malformed manifest, a missing document or ICD file, or
    PGAI_AGENT_KANBAN_ROOT_PATH unusable (unset, empty, or naming a directory
    that does not exist) so shell verbs cannot print --help.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parse_args(argv)

    team_dir = pathlib.Path(args.team_dir).resolve() if args.team_dir else _TEAM_DIR
    manifest_path = pathlib.Path(args.manifest) if args.manifest else ovm.default_manifest_path()
    doc_path = pathlib.Path(args.doc) if args.doc else _DEFAULT_DOC
    api_doc_path = pathlib.Path(args.api_doc) if args.api_doc else _DEFAULT_API_DOC
    icd_path = pathlib.Path(args.icd) if args.icd else _DEFAULT_ICD

    try:
        verbs = ovm.load_manifest(manifest_path)
    except FileNotFoundError:
        print(
            f"generate_operator_verb_docs: manifest file not found: {manifest_path}",
            file=sys.stderr,
        )
        return 2
    except ovm.ManifestFormatError as exc:
        print(f"generate_operator_verb_docs: malformed manifest: {exc}", file=sys.stderr)
        return 2

    for path in (doc_path, api_doc_path):
        if not path.is_file():
            print(f"generate_operator_verb_docs: doc file not found: {path}", file=sys.stderr)
            return 2

    try:
        icd = load_icd(icd_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"generate_operator_verb_docs: cannot load ICD artifact: {exc}; "
            f"run '{_ICD_REGENERATE_COMMAND}' from the repo root and re-run.",
            file=sys.stderr,
        )
        return 2

    has_shell_verb = any(_is_shell_verb(v) for v in verbs.values() if v.status == "active")
    root_value = os.environ.get(_ROOT_ENV_VAR, "")
    if has_shell_verb and not root_value:
        return _refuse_shell_env(team_dir, argv, "it is unset or empty")
    # A set root that is not a directory makes the verbs that check it refuse
    # their --help; checked before any capture so neither --check nor a
    # render ever compares against or writes that refusal.
    missing_cause = _missing_root_cause(root_value, team_dir) if has_shell_verb else None
    if missing_cause is not None:
        return _refuse_shell_env(
            team_dir, argv, missing_cause, error_key="operator-verb-docs.root-path-missing"
        )

    try:
        result = render_docs(verbs, team_dir, doc_path, api_doc_path, icd, args.timeout)
    except MarkerNotFoundError as exc:
        print(f"generate_operator_verb_docs: {exc}", file=sys.stderr)
        return 2
    except ShellEnvUnavailableError as exc:
        return _refuse_shell_env(team_dir, argv, f"the first shell verb's --help refused: {exc}")

    verb_names = [v.name for v in result.active_verbs]
    failed = _failed_captures(result.captures)

    if not args.check:
        _write_if_changed(result.verb_doc, verb_names)
        _write_if_changed(result.api_doc, verb_names)
        if failed:
            print(
                f"generate_operator_verb_docs: {len(failed)} verb(s) could not "
                "be invoked for --help:"
            )
            for name, reason in failed:
                print(f"  - {name}: {reason}")
        else:
            print("generate_operator_verb_docs: 0 verb(s) could not be invoked for --help.")
        return 0

    # --check mode: report every disagreement, write nothing.
    disagreements = _verb_reference_disagreements(result.verb_doc, result.active_verbs)
    disagreements += _api_reference_disagreements(result.api_doc)

    if not disagreements:
        print(
            f"generate_operator_verb_docs --check: OK -- all "
            f"{len(verb_names)} verb block(s) in {doc_path} and the API "
            f"reference in {api_doc_path} match the census, the ICD, and "
            "each verb's own --help output."
        )
        return 0

    for d in disagreements:
        print(f"generate_operator_verb_docs --check: DRIFT -- {d}", file=sys.stderr)

    # CONVERT: documentation-generation drift between the census / ICD /
    # --help output and the rendered reference sections carries zero
    # runtime-failure risk -- no operator script's or route's actual
    # behaviour is affected either way. This refusal only ever stops a
    # quality-class pre-flight lint, never the runner itself; see
    # team/SOP-core.md "Gates and Refusals".
    emit_error_contract(
        "operator-verb-docs.drift",
        (
            f"{len(disagreements)} generated reference section(s)/block(s) "
            f"disagree with team/config/operator_verbs.manifest, docs/api/icd.json "
            f"and/or their own --help output: " + "; ".join(disagreements)
        ),
        f"run '{_REGENERATE_COMMAND}' from the repo root and commit the result.",
    )
    # CENSUS-ANCHOR: generate-operator-verb-docs-check-drift-01
    return 1


if __name__ == "__main__":
    sys.exit(main())
