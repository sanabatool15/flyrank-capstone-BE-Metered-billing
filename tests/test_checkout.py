from unittest.mock import MagicMock, patch

from tests.conftest import (
    auth_header,
    make_member_with_permissions,
    make_role,
    make_tenant,
    make_user,
    make_membership,
)


def test_non_admin_without_billing_manage_returns_403(app_client, db_session):
    tenant = make_tenant(db_session, plan="free")
    user = make_user(db_session)
    role = make_role(db_session, "Viewer")
    make_membership(db_session, user, tenant, role)

    resp = app_client.post(
        f"/tenants/{tenant.id}/checkout",
        json={"target_plan": "pro"},
        headers=auth_header(user),
    )
    assert resp.status_code == 403


def test_admin_gets_checkout_url(app_client, db_session):
    tenant = make_tenant(db_session, plan="free")
    admin, _m, _r = make_member_with_permissions(db_session, tenant, ["billing.manage"])

    fake_customer = {"id": "cus_new_123"}
    fake_session = {"url": "https://checkout.stripe.com/test-session"}

    with patch(
        "src.services.stripe_service.stripe.Customer.create", return_value=fake_customer
    ), patch(
        "src.services.stripe_service.stripe.checkout.Session.create",
        return_value=fake_session,
    ) as mock_session_create:
        resp = app_client.post(
            f"/tenants/{tenant.id}/checkout",
            json={"target_plan": "pro"},
            headers=auth_header(admin),
        )

    assert resp.status_code == 200
    assert resp.json()["checkout_url"] == "https://checkout.stripe.com/test-session"
    mock_session_create.assert_called_once()
