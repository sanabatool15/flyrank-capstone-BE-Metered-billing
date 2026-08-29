"""Cost calculation. Integer cents only — no floats, per CLAUDE.md.

Pricing pinned here per DESIGN.md ("Pricing pinned in config, cents per 1M
tokens"). Reasoning tokens merge into output tokens before pricing.
"""

# Cents per API call (usage_type == "api_call").
API_CALL_PRICE_CENTS = 1

# Cents per 1,000,000 tokens, by category (usage_type == "ai_tokens").
PRICE_CENTS_PER_MILLION = {
    "input": 300,
    "cached_input": 75,
    "output": 1500,
}

_MILLION = 1_000_000


def calc_cost_cents(
    usage_type: str,
    quantity: int = 0,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> int:
    """Returns the cost of one usage event (or a rollup of many, when the
    caller passes summed totals) in integer cents.

    All arithmetic is integer: (tokens * price_cents_per_million) // 1_000_000.
    """
    if usage_type == "api_call":
        return quantity * API_CALL_PRICE_CENTS

    if usage_type == "ai_tokens":
        # Reasoning tokens merge into output before pricing (DESIGN.md).
        merged_output_tokens = output_tokens + reasoning_tokens

        cost = 0
        cost += (input_tokens * PRICE_CENTS_PER_MILLION["input"]) // _MILLION
        cost += (cached_input_tokens * PRICE_CENTS_PER_MILLION["cached_input"]) // _MILLION
        cost += (merged_output_tokens * PRICE_CENTS_PER_MILLION["output"]) // _MILLION
        return cost

    raise ValueError(f"unknown usage_type: {usage_type}")
