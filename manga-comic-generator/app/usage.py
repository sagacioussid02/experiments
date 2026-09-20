from __future__ import annotations

from typing import Any

from app.models import UsageRecord

# USD per 1M tokens. List prices at time of writing -- estimates, not an invoice; check the
# provider dashboards for the real bill and update these when prices change.
ANTHROPIC_PRICES = {  # (input, output)
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.1

OPENAI_IMAGE_PRICES = {  # gpt-image-1, per 1M tokens
    "gpt-image-1": {"text_in": 5.0, "image_in": 10.0, "image_out": 40.0},
}


# USD per 1M tokens (input, output) for OpenAI chat/vision models. Matched exactly or as
# "<name>-<date>" so that e.g. "gpt-5.4" is NOT priced as "gpt-5" (unknown -> flagged unpriced).
OPENAI_CHAT_PRICES = {
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4o": (2.5, 10.0),
}


def openai_chat_record(stage: str, model: str, usage: dict | None, detail: str = "") -> UsageRecord:
    usage = usage or {}
    inp = usage.get("prompt_tokens", 0) or 0
    out = usage.get("completion_tokens", 0) or 0  # includes reasoning tokens, which are billed as output
    price = next((v for k, v in OPENAI_CHAT_PRICES.items() if model == k or model.startswith(k + "-")), None)
    cost = (inp * price[0] + out * price[1]) / 1e6 if price and usage else None
    return UsageRecord(stage=stage, provider="openai", model=model, detail=detail, input_tokens=inp, output_tokens=out, cost_usd=cost)


def _lookup(table: dict, model: str):
    for prefix in sorted(table, key=len, reverse=True):
        if model.startswith(prefix):
            return table[prefix]
    return None


def anthropic_record(stage: str, model: str, usage: Any, detail: str = "") -> UsageRecord:
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    price = _lookup(ANTHROPIC_PRICES, model)
    cost = None
    if price:
        p_in, p_out = price
        cost = (inp * p_in + out * p_out + read * p_in * CACHE_READ_MULTIPLIER + write * p_in * CACHE_WRITE_MULTIPLIER) / 1e6
    return UsageRecord(
        stage=stage, provider="anthropic", model=model, detail=detail, input_tokens=inp,
        output_tokens=out, cache_read_tokens=read, cache_write_tokens=write, cost_usd=cost,
    )


def openai_image_record(stage: str, model: str, usage: dict | None, detail: str = "") -> UsageRecord:
    """usage is the `usage` object of an Images API response (None if the API omitted it)."""
    usage = usage or {}
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    image_in = (usage.get("input_tokens_details") or {}).get("image_tokens", 0) or 0
    price = _lookup(OPENAI_IMAGE_PRICES, model)
    cost = None
    if price and usage:
        cost = ((inp - image_in) * price["text_in"] + image_in * price["image_in"] + out * price["image_out"]) / 1e6
    return UsageRecord(
        stage=stage, provider="openai", model=model, detail=detail, input_tokens=inp,
        output_tokens=out, image_input_tokens=image_in, images=1, cost_usd=cost,
    )


def total_cost(records: list[UsageRecord]) -> float:
    return sum(r.cost_usd or 0.0 for r in records)


def summarize(records: list[UsageRecord]) -> dict:
    by_stage: dict[str, dict] = {}
    for r in records:
        s = by_stage.setdefault(r.stage, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "images": 0, "cost_usd": 0.0})
        s["calls"] += 1
        s["input_tokens"] += r.input_tokens
        s["output_tokens"] += r.output_tokens
        s["images"] += r.images
        s["cost_usd"] = round(s["cost_usd"] + (r.cost_usd or 0.0), 6)
    return {
        "total_cost_usd": round(total_cost(records), 4),
        "calls": len(records),
        "input_tokens": sum(r.input_tokens for r in records),
        "output_tokens": sum(r.output_tokens for r in records),
        "images": sum(r.images for r in records),
        "unpriced_calls": sum(1 for r in records if r.cost_usd is None),
        "by_stage": by_stage,
        "note": "Estimates from list prices in app/usage.py; not an invoice.",
    }
