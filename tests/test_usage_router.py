from tests.conftest import auth_header, make_member_with_permissions, make_tenant


def test_generate_missing_idempotency_key_returns_400(app_client, db_session):
    tenant = make_tenant(db_session, plan="free")
    user, _m, _r = make_member_with_permissions(db_session, tenant, ["api.use"])

    resp = app_client.post(
        f"/tenants/{tenant.id}/generate",
        json={"usage_type": "api_call"},
        headers=auth_header(user),
    )
    assert resp.status_code == 400


def test_generate_happy_path_200(app_client, db_session):
    tenant = make_tenant(db_session, plan="free")
    user, _m, _r = make_member_with_permissions(db_session, tenant, ["api.use"])

    resp = app_client.post(
        f"/tenants/{tenant.id}/generate",
        json={"usage_type": "api_call"},
        headers={**auth_header(user), "Idempotency-Key": "req-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "usage_event_id" in data
    assert data["cost_cents"] == 1
    assert data["remaining_quota"]["api_calls"] == 999
    assert data["remaining_quota"]["ai_tokens"] == 100_000


def test_usage_rollup_shape(app_client, db_session):
    tenant = make_tenant(db_session, plan="free")
    user, _m, _r = make_member_with_permissions(
        db_session, tenant, ["api.use", "usage.read"]
    )

    app_client.post(
        f"/tenants/{tenant.id}/generate",
        json={"usage_type": "api_call"},
        headers={**auth_header(user), "Idempotency-Key": "req-1"},
    )

    resp = app_client.get(f"/tenants/{tenant.id}/usage", headers=auth_header(user))
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {
        "period_start",
        "period_end",
        "api_calls",
        "ai_tokens",
        "cost_cents",
    }
    assert data["api_calls"]["used"] == 1
    assert data["api_calls"]["limit"] == 1_000


def test_usage_rollup_cost_from_summed_totals_not_summed_per_event_costs(
    app_client, db_session
):
    """Construct events whose per-event costs, if summed individually with
    integer floor division, would differ from the cost computed on the
    summed token totals. Assert the rollup uses the correct (summed-totals)
    method.
    """
    tenant = make_tenant(db_session, plan="free")
    user, _m, _r = make_member_with_permissions(
        db_session, tenant, ["api.use", "usage.read"]
    )

    # Each event has 1 output token individually: 1 * 1500 // 1_000_000 == 0 cents.
    # Summed per-event cost would be 0 + 0 + ... = 0.
    # But summed as totals: 3 tokens * 1500 // 1_000_000 == 0 too for small N,
    # so use a case where floor division rounding differs: use inputs that
    # sum across the boundary of the divisor.
    # input price = 300 cents / 1_000_000. Use 3 events of 400,000 input tokens
    # each: per-event cost = 400000*300//1000000 = 120 each -> summed = 360.
    # Summed totals = 1,200,000 * 300 // 1,000,000 = 360. Same here, so use a
    # genuinely rounding-losing split instead: 3 events of 333,334 input tokens.
    # per event: 333334*300//1000000 = 100 (100.0002 floored) -> 3*100=300
    # summed total: 1,000,002 * 300 // 1,000,000 = 300 (300.0006 floored) = 300
    # Still equal. Use output tokens with values that individually floor to 0
    # but summed cross the threshold: output price 1500/1_000_000.
    # 3 events of 400 output tokens each: per event 400*1500//1000000 = 0 -> summed=0
    # summed totals: 1200 * 1500 // 1_000_000 = 1 (1.8 floored to 1).
    for i in range(3):
        app_client.post(
            f"/tenants/{tenant.id}/generate",
            json={"usage_type": "ai_tokens", "output_tokens": 400},
            headers={**auth_header(user), "Idempotency-Key": f"req-{i}"},
        )

    resp = app_client.get(f"/tenants/{tenant.id}/usage", headers=auth_header(user))
    data = resp.json()

    # Sum of per-event costs would be 0 (each event floors to 0 cents individually).
    sum_of_per_event_costs = 0
    # Correct: cost computed on summed token totals (1200 output tokens).
    correct_cost = (1200 * 1500) // 1_000_000
    assert correct_cost == 1
    assert data["cost_cents"] == correct_cost
    assert data["cost_cents"] != sum_of_per_event_costs
