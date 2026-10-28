"""
provider_usage.py — turn a provider CLI's captured output into a usage record.

The wake captures each provider CLI's machine-readable output to a file and
needs two things back from it: the agent's text answer, written to a
result-text file, and a flat usage record on stdout that the token-capture
merge writes into the task's ``tokens.json``. This module holds those
parsers, one per provider output format, plus the helpers that adjust a
usage record after parsing.

The flat usage record has exactly these keys::

    {"input_tokens": N, "output_tokens": N,
     "cache_read_input_tokens": N, "cache_creation_input_tokens": N}

and may carry an optional ``canonical_model_id`` naming the model the
tokens were spent on, when the output stream itself does not say.

Claude
------
``claude -p --output-format json`` writes one JSON object whose ``result``
field holds the agent's text and whose ``usage`` block holds aggregate token
counts. Newer CLI builds add ``total_cost_usd`` and a per-model
``modelUsage`` map (camelCase keys); the claude usage record carries those
too, as ``total_cost_usd`` and a snake_case ``model_usage`` (both ``null``
when the CLI omits them), plus ``canonical_model_id``. The bash caller is
``team/scripts/lib/token_capture.sh``, which reaches the parser through
``python3 -m pgai_agent_kanban.lib.token_capture``.

Codex
-----
``codex exec --json`` writes a JSONL event stream. The agent's text is in
``item.completed`` events whose ``item.type`` is ``agent_message``; the
token counts are in the ``usage`` block of the ``turn.completed`` event.
Codex reports cache reads as ``cached_input_tokens`` (mapped to
``cache_read_input_tokens``), includes them in ``input_tokens``, and never
reports cache creation, so ``cache_creation_input_tokens`` is always 0.

The bash caller is ``team/scripts/lib/provider_codex.sh``, which reaches
these functions through ``python3 -m pgai_agent_kanban.lib.codex_provider``.

Public API:
    WARNING_PREFIX
    CLAUDE_WARNING_PREFIX
    UnparseableOutputError
    compact_json(record) -> str
    parse_claude_json(json_path, result_path, requested_model="", warn=None) -> dict
    parse_codex_jsonl(jsonl_path, result_path, warn=None) -> dict
    stamp_canonical_model_id(record, model) -> dict
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Callable, Optional, Union

__all__ = [
    "CLAUDE_WARNING_PREFIX",
    "UnparseableOutputError",
    "WARNING_PREFIX",
    "compact_json",
    "parse_claude_json",
    "parse_codex_jsonl",
    "stamp_canonical_model_id",
]

#: Prefix of every warning the codex parser writes to stderr.
WARNING_PREFIX = "provider_codex"

#: Prefix of every warning the claude parser writes to stderr.
CLAUDE_WARNING_PREFIX = "token_capture"


class UnparseableOutputError(ValueError):
    """The captured provider output holds no JSON document at all."""

PathLike = Union[str, "os.PathLike[str]"]


def _stderr_warning(text: str) -> None:
    """Write one warning line to stderr."""
    print(text, file=sys.stderr)


def compact_json(record: dict) -> str:
    """Return *record* as the one-line JSON the wake reads: no spaces after
    ``,`` or ``:``, non-ASCII escaped."""
    return json.dumps(record, separators=(",", ":"))


def _claude_result_text(result: object) -> str:
    """Return the agent's text from a claude ``result`` field: a string as
    is, a content-block list as its ``text`` blocks joined by newlines, and
    anything else as ``str(result)``."""
    if isinstance(result, list):
        return "\n".join(
            block.get("text", "") for block in result
            if isinstance(block, dict) and block.get("type") == "text"
        )
    if not isinstance(result, str):
        return str(result)
    return result


def _select_canonical_model(model_usage: dict, requested_model: str) -> str:
    """Return the primary model of a snake_case *model_usage* map.

    *requested_model* wins when it is a key of the map; otherwise the key
    with the greatest ``input_tokens + output_tokens`` (the first such key,
    in map order, on a tie); ``""`` for an empty map.
    """
    if requested_model and requested_model in model_usage:
        return requested_model
    if not model_usage:
        return ""
    return max(
        model_usage,
        key=lambda name: model_usage[name].get("input_tokens", 0) + model_usage[name].get("output_tokens", 0),
    )


def parse_claude_json(
    json_path: PathLike,
    result_path: PathLike,
    requested_model: str = "",
    warn: Optional[Callable[[str], None]] = None,
) -> dict:
    """Parse a captured ``claude -p --output-format json`` response.

    The file is read whole and stripped. When it is not one JSON document,
    its last non-blank line that is JSON is used instead (the stream-json
    shape). The agent's text (the ``result`` field; see
    ``_claude_result_text``) is written to *result_path*, even when empty.

    Returns the claude usage record, in this key order::

        {"input_tokens": N, "output_tokens": N,
         "cache_creation_input_tokens": N, "cache_read_input_tokens": N,
         "canonical_model_id": "<model or empty>",
         "total_cost_usd": <float or None>,
         "model_usage": {<model>: {"input_tokens": N, "output_tokens": N,
                                   "cache_creation_input_tokens": N,
                                   "cache_read_input_tokens": N,
                                   "cost_usd": <float>}, ...} or None}

    The four totals come from ``usage`` (0 when a key or the block is
    missing). ``total_cost_usd`` is the response's number as a float, or
    None when absent or not a number. ``model_usage`` maps each
    ``modelUsage`` entry that is an object from camelCase to snake_case,
    missing counts as 0; it is None when ``modelUsage`` is absent, not an
    object, or empty. ``canonical_model_id`` is *requested_model* when it
    is a ``model_usage`` key, else the key with the greatest input+output
    tokens, else ``""``.

    Args:
        json_path:       The captured response.
        result_path:     Where the agent's text is written.
        requested_model: The model the wake asked the CLI for (may be empty).
        warn:            Called with a warning line when *result_path*
                         cannot be written (parsing still returns the
                         record); default writes it to stderr.

    Raises:
        OSError: *json_path* cannot be read. *result_path* has been emptied
            first, so a caller never reads a stale answer.
        UnparseableOutputError: No line of the file is JSON. The raw
            (stripped) text has been written to *result_path*.
        ValueError: The file is not UTF-8 (nothing written), or a count is
            not a number (the result text has been written).
        TypeError: A count is ``null`` or a container (result text written).
        AttributeError: The JSON document is not an object (nothing written).
    """
    warn = warn or _stderr_warning
    result_file = pathlib.Path(result_path)

    try:
        raw = pathlib.Path(json_path).read_text(encoding="utf-8").strip()
    except OSError:
        result_file.write_text("", encoding="utf-8")
        raise

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                pass
        else:
            result_file.write_text(raw, encoding="utf-8")
            raise UnparseableOutputError("cannot parse JSON from claude output") from None

    result_text = _claude_result_text(data.get("result", ""))
    try:
        result_file.write_text(result_text, encoding="utf-8")
    except OSError as exc:
        warn(f"{CLAUDE_WARNING_PREFIX}: cannot write result_text_file: {exc}")

    usage = data.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}

    record = {
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens", 0)),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0)),
        "canonical_model_id": "",
        "total_cost_usd": None,
        "model_usage": None,
    }

    raw_cost = data.get("total_cost_usd")
    if isinstance(raw_cost, (int, float)):
        record["total_cost_usd"] = float(raw_cost)

    raw_model_usage = data.get("modelUsage")
    if isinstance(raw_model_usage, dict) and raw_model_usage:
        model_usage = {}
        for model_name, entry in raw_model_usage.items():
            if not isinstance(entry, dict):
                continue
            model_usage[model_name] = {
                "input_tokens": int(entry.get("inputTokens", 0)),
                "output_tokens": int(entry.get("outputTokens", 0)),
                "cache_creation_input_tokens": int(entry.get("cacheCreationInputTokens", 0)),
                "cache_read_input_tokens": int(entry.get("cacheReadInputTokens", 0)),
                "cost_usd": float(entry.get("costUSD", 0)),
            }
        record["model_usage"] = model_usage
        record["canonical_model_id"] = _select_canonical_model(model_usage, requested_model)

    return record


def parse_codex_jsonl(
    jsonl_path: PathLike,
    result_path: PathLike,
    warn: Optional[Callable[[str], None]] = None,
) -> dict:
    """Parse a captured ``codex exec --json`` event stream.

    Writes the agent's text -- every non-empty ``agent_message`` text, in
    stream order, joined by newlines -- to *result_path*, and returns the
    flat usage record from the stream's ``turn.completed`` event (the last
    one, when there are several). Blank lines and lines that are not JSON
    are skipped.

    Args:
        jsonl_path:  The captured JSONL stream.
        result_path: Where the agent's text is written (an empty file when
                     the stream holds no agent message).
        warn:        Called with each warning line; default writes it to
                     stderr. Warnings: the result file cannot be written
                     (parsing still returns the record), and no
                     ``turn.completed`` event was found (the record is all
                     zeros).

    Returns:
        The usage record (see the module docstring).

    Raises:
        OSError:    *jsonl_path* cannot be read. *result_path* has been
                    emptied first, so a caller never reads a stale answer.
        ValueError: A line is undecodable as UTF-8, or a usage count is not
                    an integer.
        TypeError:  A usage count is ``null`` or a container.
        AttributeError: A JSON line is not an object.
        Nothing is written to *result_path* for the last three.
    """
    warn = warn or _stderr_warning
    jsonl_file = pathlib.Path(jsonl_path)
    result_file = pathlib.Path(result_path)

    try:
        lines = jsonl_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        result_file.write_text("", encoding="utf-8")
        raise

    agent_text_parts = []
    record = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    usage_found = False

    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text", "")
                if isinstance(text, str) and text:
                    agent_text_parts.append(text)

        elif event_type == "turn.completed":
            usage = event.get("usage", {})
            if isinstance(usage, dict):
                usage_found = True
                record["input_tokens"] = int(usage.get("input_tokens", 0))
                record["output_tokens"] = int(usage.get("output_tokens", 0))
                record["cache_read_input_tokens"] = int(usage.get("cached_input_tokens", 0))
                record["cache_creation_input_tokens"] = 0

    try:
        result_file.write_text("\n".join(agent_text_parts), encoding="utf-8")
    except OSError as exc:
        warn(f"{WARNING_PREFIX}: cannot write result_text_file: {exc}")

    if not usage_found:
        warn(f"{WARNING_PREFIX}: WARNING: no turn.completed event found in JSONL — usage will be 0")

    return record


def stamp_canonical_model_id(record: dict, model: str) -> dict:
    """Return a copy of usage *record* with ``canonical_model_id`` set to *model*.

    Used when the provider's output stream does not name the model, so the
    token-capture merge prices the tokens against the configured model
    instead of ``unknown``. An existing ``canonical_model_id`` is replaced;
    a new key is added last, so the compact JSON keeps every other key in
    its original order.
    """
    stamped = dict(record)
    stamped["canonical_model_id"] = model
    return stamped
