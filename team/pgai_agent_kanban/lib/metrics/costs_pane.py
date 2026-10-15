"""
pgai_agent_kanban.lib.metrics.costs_pane — the dashboard Costs pane's cost blocks.

``render_cost_block()`` turns one token roll-up -- a day roll-up
(``usage/daily/<date>.json``, read through its ``by_model`` table) or an RC
roll-up (``usage/rc/<version>-tokens.json``, read through its ``tasks``
list) -- into the pane's per-model breakdown: a ``<provider> / <model>``
line, input / cache write / cache read / output lines with token counts and
costs, a rule, and the model's subtotal. The aggregator's per-category
cost fields are used when any is non-zero; otherwise the costs are priced
from ``token_pricing.json`` (a model the table does not list costs 0).

``scripts/dashboard/costs.sh`` reaches these functions through
``pp_run_ops pgai_agent_kanban.lib.metrics``: ``cost_block`` prints a
block's total on its first line and the block after it, ``rc_label``
prints an RC's header detail, and ``sum_amounts`` adds the per-project
totals into the day total.

A block's total is the sum of its printed subtotals, read back from the
printed ``subtotal:`` lines, so a subtotal of $1,000 or more (printed
without cents) enters the total as printed.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------

def load_pricing(pricing_path: str) -> dict[str, Any]:
    """Load token_pricing.json.  Returns empty dict on any failure."""
    if not pricing_path:
        return {}
    p = pathlib.Path(pricing_path)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_model_rates(pricing: dict[str, Any], model: str) -> dict[str, float] | None:
    """Return pricing rates for the given model, searching across all providers."""
    for _provider, pb in pricing.get("providers", {}).items():
        rates = pb.get("models", {}).get(model)
        if rates is not None:
            return rates
    return None


def model_cost_parts(
    pricing: dict[str, Any],
    model: str,
    input_tok: int,
    output_tok: int,
    cache_create: int,
    cache_read: int,
) -> tuple[float, float, float, float]:
    """Return (input_cost, cache_create_cost, cache_read_cost, output_cost).
    Returns zeros when model not found or pricing unavailable."""
    rates = get_model_rates(pricing, model)
    if rates is None:
        return (0.0, 0.0, 0.0, 0.0)
    i_cost  = input_tok     * float(rates.get("input_per_1m",            0)) / 1_000_000
    cc_cost = cache_create  * float(rates.get("cache_creation_per_1m",   0)) / 1_000_000
    cr_cost = cache_read    * float(rates.get("cache_read_per_1m",        0)) / 1_000_000
    o_cost  = output_tok    * float(rates.get("output_per_1m",            0)) / 1_000_000
    return (round(i_cost, 6), round(cc_cost, 6), round(cr_cost, 6), round(o_cost, 6))


# ---------------------------------------------------------------------------
# Rollup loading
# ---------------------------------------------------------------------------

def load_rollup(rollup_path: pathlib.Path) -> dict[str, Any] | None:
    """Load a rollup JSON file.  Returns None on any failure."""
    if not rollup_path.is_file():
        return None
    try:
        data = json.loads(rollup_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_tokens(n: int) -> str:
    """Return a token count with thousands separators (``1,234,567``)."""
    return f"{n:,}"


def fmt_usd(v: float) -> str:
    """Return a dollar amount: whole dollars with separators from $1,000 up, else two decimals."""
    if abs(v) >= 1000:
        return f"${v:,.0f}"
    return f"${v:.2f}"


# ---------------------------------------------------------------------------
# Build per-model data from the rollup.
# Returns a dict: model_name -> {input, cache_create, cache_read, output}
# Normalised: for day rollups reads by_model; for RC rollups reads tasks.
# Also reads pre-computed costs from aggregator fields when available.
# ---------------------------------------------------------------------------

def extract_by_model(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Aggregate token and cost data by model from a rollup dict."""
    result: dict[str, dict[str, Any]] = {}

    def _add(model: str, i: int, cc: int, cr: int, o: int,
             i_cost: float, cc_cost: float, cr_cost: float, o_cost: float) -> None:
        if model not in result:
            result[model] = {
                "input": 0, "cache_create": 0, "cache_read": 0, "output": 0,
                "input_cost": 0.0, "cc_cost": 0.0, "cr_cost": 0.0, "o_cost": 0.0,
            }
        result[model]["input"]        += i
        result[model]["cache_create"] += cc
        result[model]["cache_read"]   += cr
        result[model]["output"]       += o
        result[model]["input_cost"]   += i_cost
        result[model]["cc_cost"]      += cc_cost
        result[model]["cr_cost"]      += cr_cost
        result[model]["o_cost"]       += o_cost

    # Day rollup: by_model key
    if "by_model" in data:
        for model, mv in data["by_model"].items():
            i   = int(mv.get("input",  0) or 0)
            cc  = int(mv.get("cache_creation_tokens", 0) or 0)
            cr  = int(mv.get("cache_read_tokens",     0) or 0)
            o   = int(mv.get("output", 0) or 0)

            # Use aggregator-computed costs when present (avoid double-computing)
            i_cost  = float(mv.get("input_cost_usd",          0) or 0)
            cc_cost = float(mv.get("cache_creation_cost_usd",  0) or 0)
            cr_cost = float(mv.get("cache_read_cost_usd",      0) or 0)
            o_cost  = float(mv.get("output_cost_usd",          0) or 0)
            _add(model, i, cc, cr, o, i_cost, cc_cost, cr_cost, o_cost)

    # RC rollup: tasks list (no by_model; aggregate from tasks)
    elif "tasks" in data:
        for task in data.get("tasks", []):
            model = str(task.get("model", "unknown"))
            i   = int(task.get("input",  0) or 0)
            cc  = int(task.get("cache_creation_tokens", 0) or 0)
            cr  = int(task.get("cache_read_tokens",     0) or 0)
            o   = int(task.get("output", 0) or 0)

            i_cost  = float(task.get("input_cost_usd",          0) or 0)
            cc_cost = float(task.get("cache_creation_cost_usd",  0) or 0)
            cr_cost = float(task.get("cache_read_cost_usd",      0) or 0)
            o_cost  = float(task.get("output_cost_usd",          0) or 0)
            _add(model, i, cc, cr, o, i_cost, cc_cost, cr_cost, o_cost)

    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# The rule under each model's category lines.
_SEPARATOR = "─" * 37

# A printed subtotal line, as the block total reads it back.
_SUBTOTAL_RE = re.compile(r"subtotal:\s+\$([\d,]+\.?\d*)")


def render_cost_block(rollup_path: str, pricing_path: str, indent: str) -> list[str]:
    """The per-model cost breakdown lines for one roll-up file.

    Args:
        rollup_path:  Path to a day or RC roll-up JSON file.
        pricing_path: Path to token_pricing.json ("" for none).
        indent:       Prefix for every line (e.g. four spaces).

    Returns:
        The block's lines, each model followed by an empty line; a single
        ``(no data yet)`` line when the roll-up is missing, unreadable or
        has no models.
    """
    pricing = load_pricing(pricing_path)
    data = load_rollup(pathlib.Path(rollup_path))
    if data is None:
        return [f"{indent}(no data yet)"]
    by_model = extract_by_model(data)
    if not by_model:
        return [f"{indent}(no data yet)"]

    def _line(label: str, tokens: int, cost: float) -> str:
        return f"{indent}  {label:<14} {fmt_tokens(tokens):>15} tokens    {fmt_usd(cost)}"

    lines: list[str] = []
    for model in sorted(by_model):
        mv = by_model[model]
        precomputed = (mv["input_cost"], mv["cc_cost"], mv["cr_cost"], mv["o_cost"])
        if sum(precomputed) != 0.0:
            i_cost, cc_cost, cr_cost, o_cost = precomputed
        else:
            i_cost, cc_cost, cr_cost, o_cost = model_cost_parts(
                pricing, model, mv["input"], mv["output"], mv["cache_create"], mv["cache_read"]
            )
        subtotal = i_cost + cc_cost + cr_cost + o_cost
        provider_label = "claude"
        for pname, pb in pricing.get("providers", {}).items():
            if model in pb.get("models", {}):
                provider_label = pname
                break
        lines.append(f"{indent}{provider_label} / {model}")
        lines.append(_line("input:", mv["input"], i_cost))
        lines.append(_line("cache write:", mv["cache_create"], cc_cost))
        lines.append(_line("cache read:", mv["cache_read"], cr_cost))
        lines.append(_line("output:", mv["output"], o_cost))
        lines.append(f"{indent}  {_SEPARATOR}")
        lines.append(f"{indent}  subtotal:{' ' * 35}{fmt_usd(subtotal)}")
        lines.append("")
    return lines


def block_total(lines: list[str]) -> str:
    """The sum of a block's printed subtotals, as ``%.2f``."""
    total = 0.0
    for line in lines:
        match = _SUBTOTAL_RE.search(line)
        if match:
            total += float(match.group(1).replace(",", ""))
    return f"{total:.2f}"


def cost_block(rollup_path: str, pricing_path: str, indent: str) -> str:
    """The text ``costs.sh`` prints for one roll-up: its total, then the block.

    The first line is :func:`block_total`; the block's lines follow.
    """
    lines = render_cost_block(rollup_path, pricing_path, indent)
    return "\n".join([block_total(lines), *lines]) + "\n"


def rc_label(rc_rollup_path: str) -> str:
    """An RC header's detail: ``started <shipped_at>, <N> tasks`` or ``<N> tasks``.

    ``shipped_at`` (else ``started_at``) is used when non-empty; N counts the
    roll-up's ``tasks`` entries. An unreadable roll-up yields "".
    """
    try:
        with open(rc_rollup_path, encoding="utf-8") as fh:
            data = json.load(fh)
        started = data.get("shipped_at", "") or data.get("started_at", "")
        tasks = len(data.get("tasks", []))
    except Exception:  # noqa: BLE001 -- any unreadable roll-up leaves the header bare
        return ""
    return f"started {started}, {tasks} tasks" if started else f"{tasks} tasks"


def sum_amounts(amounts: list[str]) -> str:
    """Add dollar amounts one at a time, each running sum rounded to cents; "0" for none."""
    if not amounts:
        return "0"
    total = "0"
    for amount in amounts:
        total = f"{float(total) + float(amount):.2f}"
    return total


def render_costs(rollup_path: str, pricing_path: str, indent: str) -> None:
    """Print :func:`render_cost_block`'s lines for one roll-up."""
    for line in render_cost_block(rollup_path, pricing_path, indent):
        print(line)
