#!/usr/bin/env python3
"""
render_costs.py — Standalone cost renderer for the pgai kanban dashboard.

This module is an as-is extraction of the 229-line inline Python heredoc
previously embedded in team/scripts/dashboard/costs.sh as
``_render_cost_block_python``.  The rendering logic is preserved exactly —
do NOT change output format, numeric formatting, or indentation logic without
filing a separate bug.  Any quirks in the original are intentionally kept.

Reads a token-rollup JSON file and (optionally) a token_pricing.json file.
Emits indented per-model cost breakdown lines suitable for dashboard output.

The rollup JSON may be either:
  - A day rollup (contains a ``by_model`` key with per-model aggregates)
  - An RC rollup  (contains a ``tasks`` list; costs are summed per model)

Pre-computed cost fields (``*_cost_usd``) are used when present; pricing-table
fallback is used when all computed costs are zero (legacy rollup with no
pre-computed fields).

Usage (CLI):
    python3 team/pgai_agent_kanban/dashboard/render_costs.py \\
        <rollup_path> <pricing_path> [--indent N]

    rollup_path    — path to day or RC rollup JSON
    pricing_path   — path to token_pricing.json (may be empty string "")
    --indent N     — number of spaces to prefix each output line (default: 4)

Usage (import):
    from pgai_agent_kanban.dashboard.render_costs import render_costs
    render_costs(rollup_path="...", pricing_path="...", indent="    ")

Both forms produce identical output.

Exit codes:
    0 — always (renders a friendly placeholder when data is missing or invalid)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
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
    return f"{n:,}"


def fmt_usd(v: float) -> str:
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
# Main rendering logic
# ---------------------------------------------------------------------------

def render_costs(rollup_path: str, pricing_path: str, indent: str) -> None:
    """Render per-model cost breakdown lines to stdout.

    Produces identical output to the ``_render_cost_block_python`` heredoc
    in team/scripts/dashboard/costs.sh.  Output is plain text formatted for
    indentation.

    Args:
        rollup_path:   Path to day or RC rollup JSON file.
        pricing_path:  Path to token_pricing.json (empty string = no pricing).
        indent:        Indentation prefix string (e.g. "    " for 4 spaces).
    """
    rp = pathlib.Path(rollup_path)
    pricing = load_pricing(pricing_path)
    data = load_rollup(rp)

    if data is None:
        print(f"{indent}(no data yet)")
        return

    by_model = extract_by_model(data)

    if not by_model:
        print(f"{indent}(no data yet)")
        return

    # Separator line (em-dash, matching dashboard-metadata.sh visual style)
    SEPARATOR = "─" * 37

    grand_total: float = 0.0
    found_any = False

    # Sort models deterministically: alphabetically by model name
    for model in sorted(by_model.keys()):
        mv = by_model[model]
        i  = mv["input"]
        cc = mv["cache_create"]
        cr = mv["cache_read"]
        o  = mv["output"]

        # Use pre-computed costs from aggregator when non-zero.
        # Fall back to computing from pricing when all are zero (legacy rollup).
        pre_i  = mv["input_cost"]
        pre_cc = mv["cc_cost"]
        pre_cr = mv["cr_cost"]
        pre_o  = mv["o_cost"]
        cat_sum = pre_i + pre_cc + pre_cr + pre_o

        if cat_sum != 0.0:
            i_cost, cc_cost, cr_cost, o_cost = pre_i, pre_cc, pre_cr, pre_o
        else:
            i_cost, cc_cost, cr_cost, o_cost = model_cost_parts(
                pricing, model, i, o, cc, cr
            )

        subtotal = i_cost + cc_cost + cr_cost + o_cost
        grand_total += subtotal
        found_any = True

        # Provider label: look up in pricing to get the provider name.
        provider_label = "claude"
        for pname, pb in pricing.get("providers", {}).items():
            if model in pb.get("models", {}):
                provider_label = pname
                break

        print(f"{indent}{provider_label} / {model}")

        # Align: label 14 chars, token count 15 chars right-justified, cost aligned
        def _line(lbl: str, tokens: int, cost: float) -> str:
            return f"{indent}  {lbl:<14} {fmt_tokens(tokens):>15} tokens    {fmt_usd(cost)}"

        print(_line("input:",        i,  i_cost))
        print(_line("cache write:",  cc, cc_cost))
        print(_line("cache read:",   cr, cr_cost))
        print(_line("output:",       o,  o_cost))
        print(f"{indent}  {SEPARATOR}")
        print(f"{indent}  subtotal:{' ' * 35}{fmt_usd(subtotal)}")
        print()

    if not found_any:
        print(f"{indent}(no data yet)")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point: render cost block from rollup + pricing files."""
    parser = argparse.ArgumentParser(
        description=(
            "Render a per-model cost breakdown block from a token rollup JSON. "
            "Output is plain text formatted for indentation (suitable for "
            "embedding in the pgai kanban dashboard)."
        ),
    )
    parser.add_argument(
        "rollup_path",
        help="Path to day or RC rollup JSON file.",
    )
    parser.add_argument(
        "pricing_path",
        help=(
            "Path to token_pricing.json. Pass an empty string \"\" to skip "
            "pricing lookup (all costs will show as $0.00 for legacy data)."
        ),
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=4,
        metavar="N",
        help="Number of spaces to prefix each output line (default: 4).",
    )
    args = parser.parse_args()
    render_costs(
        rollup_path=args.rollup_path,
        pricing_path=args.pricing_path,
        indent=" " * args.indent,
    )


if __name__ == "__main__":
    main()
