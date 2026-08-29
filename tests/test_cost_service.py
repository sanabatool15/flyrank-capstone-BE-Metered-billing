from src.services.cost_service import calc_cost_cents


def test_api_call_pricing():
    assert calc_cost_cents("api_call", quantity=1) == 1
    assert calc_cost_cents("api_call", quantity=250) == 250


def test_api_call_zero_quantity():
    assert calc_cost_cents("api_call", quantity=0) == 0


def test_ai_tokens_input_pricing():
    # 1,000,000 input tokens @ 300 cents/million = 300 cents
    assert calc_cost_cents("ai_tokens", input_tokens=1_000_000) == 300


def test_ai_tokens_cached_input_pricing():
    # 1,000,000 cached input tokens @ 75 cents/million = 75 cents
    assert calc_cost_cents("ai_tokens", cached_input_tokens=1_000_000) == 75


def test_ai_tokens_output_pricing():
    # 1,000,000 output tokens @ 1500 cents/million = 1500 cents
    assert calc_cost_cents("ai_tokens", output_tokens=1_000_000) == 1500


def test_ai_tokens_reasoning_merges_into_output():
    # reasoning tokens priced at the output rate, merged before pricing
    cost_reasoning_only = calc_cost_cents("ai_tokens", reasoning_tokens=1_000_000)
    cost_output_only = calc_cost_cents("ai_tokens", output_tokens=1_000_000)
    assert cost_reasoning_only == cost_output_only == 1500

    # split across output + reasoning sums the same as if all in one bucket
    split = calc_cost_cents("ai_tokens", output_tokens=400_000, reasoning_tokens=600_000)
    assert split == 1500


def test_ai_tokens_combined_categories():
    cost = calc_cost_cents(
        "ai_tokens",
        input_tokens=1_000_000,
        cached_input_tokens=1_000_000,
        output_tokens=500_000,
        reasoning_tokens=500_000,
    )
    # 300 + 75 + 1500 (500k+500k=1M output-equivalent @ 1500/million)
    assert cost == 300 + 75 + 1500


def test_ai_tokens_zero_quantity():
    assert calc_cost_cents("ai_tokens") == 0
    assert calc_cost_cents(
        "ai_tokens",
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
    ) == 0


def test_result_is_always_int_no_floats():
    result = calc_cost_cents("ai_tokens", input_tokens=333_333)
    assert isinstance(result, int)
    # integer floor division: 333333 * 300 // 1_000_000 = 99 (not 99.9999)
    assert result == 99


def test_unknown_usage_type_raises():
    import pytest

    with pytest.raises(ValueError):
        calc_cost_cents("bogus")
