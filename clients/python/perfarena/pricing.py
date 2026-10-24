"""Cost of one generation.

OpenAI models priced in the local table keep the scripts' rule (fresh prompt,
cached prompt, and completion tokens each at their own rate). Any other paid
model is priced from the server's rates (GET /api/pricing, set by an admin on
/models). Local providers cost nothing. An unknown model costs 0.0: better to
under-report than to invent a number."""
from __future__ import annotations

import re

# Per-1M-token USD, copied from submit_with_local_model.OPENAI_PRICING_USD_PER_1M.
OPENAI_PRICING_USD_PER_1M: dict[str, dict[str, float]] = {
    "gpt-5":             {"in": 1.25, "cached": 0.125, "out": 10.00},
    "gpt-5-mini":        {"in": 0.25, "cached": 0.025, "out": 2.00},
    "gpt-5-nano":        {"in": 0.05, "cached": 0.005, "out": 0.40},
    "gpt-5.4-mini":      {"in": 0.25, "cached": 0.025, "out": 2.00},
    "gpt-5.5":           {"in": 0.25, "cached": 0.025, "out": 2.00},
    "gpt-5-chat":        {"in": 1.25, "cached": 0.125, "out": 10.00},
    "gpt-5-chat-latest": {"in": 1.25, "cached": 0.125, "out": 10.00},
    "gpt-4.1":           {"in": 2.00, "cached": 0.50,  "out": 8.00},
    "gpt-4.1-mini":      {"in": 0.40, "cached": 0.10,  "out": 1.60},
    "gpt-4.1-nano":      {"in": 0.10, "cached": 0.025, "out": 0.40},
    "gpt-4o":            {"in": 2.50, "cached": 1.25,  "out": 10.00},
    "gpt-4o-mini":       {"in": 0.15, "cached": 0.075, "out": 0.60},
    "o4-mini":           {"in": 1.10, "cached": 0.275, "out": 4.40},
    "o3":                {"in": 2.00, "cached": 0.50,  "out": 8.00},
    "o3-mini":           {"in": 1.10, "cached": 0.55,  "out": 4.40},
}
_FREE = {"ollama", "canned-fallback", "fake", None}


def _cached_tokens(usage: dict) -> int:
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    return int(details.get("cached_tokens") or 0)


def openai_cost(trace: dict) -> float:
    model = trace.get("model") or ""
    base = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
    price = OPENAI_PRICING_USD_PER_1M.get(model) or OPENAI_PRICING_USD_PER_1M.get(base)
    if not price:
        return 0.0
    usage = (trace.get("provider_meta") or {}).get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    cached = min(_cached_tokens(usage), pt)
    return ((pt - cached) * price["in"] + cached * price.get("cached", price["in"])
            + ct * price["out"]) / 1_000_000.0


class PriceBook:
    """`server_rates`: {(model_name, model_version): (in, out)} per 1M tokens,
    usually from Leaderboard.pricing()."""

    def __init__(self, server_rates: dict[tuple[str, str], tuple[float, float]] | None = None):
        self.server_rates = server_rates or {}

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "PriceBook":
        return cls({(r["model_name"], r["model_version"]):
                    (r["input_price_per_1m_usd"], r["output_price_per_1m_usd"]) for r in rows})

    def _server_rate(self, model: str, version: str | None):
        if version and (model, version) in self.server_rates:
            return self.server_rates[(model, version)]
        return next((v for (m, _), v in self.server_rates.items() if m == model), None)

    def cost_usd(self, trace: dict, model_version: str | None = None) -> float:
        provider = trace.get("provider")
        if provider in _FREE:
            return 0.0
        if provider == "openai" and openai_cost(trace):
            return openai_cost(trace)
        rate = self._server_rate(trace.get("model") or "", model_version)
        if rate is None:
            return 0.0
        usage = (trace.get("provider_meta") or {}).get("usage") or {}
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        return (pt * rate[0] + ct * rate[1]) / 1_000_000.0


def estimate_code_tokens(code: str) -> int:
    """Token count of the code: tiktoken's cl100k_base when installed, else
    ~4 characters per token (the scripts' rule)."""
    if not code:
        return 0
    try:
        import tiktoken
        return len(tiktoken.get_encoding("cl100k_base").encode(code))
    except Exception:                                            # noqa: BLE001
        return max(1, len(code) // 4)
