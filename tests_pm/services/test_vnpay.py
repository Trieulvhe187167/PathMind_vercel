"""VNPAY: signed payment URL, IPN + return confirmation; VNPAY is the only gateway."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qsl, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pathmind.database.connection import Base
from pathmind.database.init_db import DEFAULT_MODEL_PRICES, DEFAULT_PLANS
from pathmind.database.models import ModelPrice, Payment, SubscriptionPlan
from pathmind.services import vnpay

SECRET = "UNITTESTHASHSECRET"


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("VNPAY_TMN_CODE", "TESTTMN1")
    monkeypatch.setenv("VNPAY_HASH_SECRET", SECRET)
    monkeypatch.setenv("PATHMIND_PUBLIC_URL", "https://pathmind.example")
    monkeypatch.delenv("VNPAY_ENV", raising=False)
    monkeypatch.delenv("BILLING_DEMO_MODE", raising=False)


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for p in DEFAULT_PLANS:
        session.add(SubscriptionPlan(**p))
    for price in DEFAULT_MODEL_PRICES:
        session.add(ModelPrice(is_active=True, **price))
    session.commit()
    from pathmind.services import quota_service

    monkeypatch.setattr(quota_service, "_auth_store_lookup", lambda uid: None)
    quota_service.ensure_db_user(session, "u1", "learner", "user")
    quota_service.ensure_db_user(session, "u2", "other", "user")
    yield session
    session.close()


class _Me:
    user_id = "u1"
    username = "learner"
    role = "user"


def _client(db):
    from pathmind.api.routers import billing
    from pathmind.api.routers.auth import require_auth
    from pathmind.database.connection import get_db

    app = FastAPI()
    app.include_router(billing.router, prefix="/api/billing")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: _Me()
    return TestClient(app)


def _payment(db, code="DT0123456789", user="u1", status="pending", amount_vnd=253_746):
    payment = Payment(
        user_id=user,
        plan_id="pro",
        amount=9.99,
        currency="USD",
        amount_vnd=amount_vnd,
        billing_interval="monthly",
        payment_method="vnpay",
        transaction_id=code,
        status=status,
    )
    db.add(payment)
    db.commit()
    return payment


def _result(code="DT0123456789", amount_vnd=253_746, response="00", status="00", **extra):
    """What VNPAY sends back (IPN query / return URL), signed with our secret."""
    params = {
        "vnp_Amount": str(amount_vnd * 100),
        "vnp_BankCode": "NCB",
        "vnp_BankTranNo": "VNP14000000",
        "vnp_CardType": "ATM",
        "vnp_OrderInfo": "Thanh toan goi Pro Learner ma " + code,
        "vnp_PayDate": "20260928003500",
        "vnp_ResponseCode": response,
        "vnp_TmnCode": "TESTTMN1",
        "vnp_TransactionNo": "14000000",
        "vnp_TransactionStatus": status,
        "vnp_TxnRef": code,
        **extra,
    }
    params["vnp_SecureHash"] = vnpay.sign(params, SECRET)
    return params


# ---------------------------------------------------------------------------


def test_payment_url_is_signed_and_well_formed(env):
    url = vnpay.build_payment_url(
        transaction_id="DT0123456789",
        amount_vnd=253_746,
        order_info="Thanh toán gói Pro: DT0123456789!",
        client_ip="1.2.3.4",
        now=datetime(2026, 9, 28, 0, 30, tzinfo=vnpay.VN_TZ),
    )
    assert url.startswith(vnpay.SANDBOX_PAYMENT_URL + "?")
    query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    assert query["vnp_Amount"] == "25374600"
    assert query["vnp_CurrCode"] == "VND" and query["vnp_Version"] == "2.1.0"
    assert query["vnp_CreateDate"] == "20260928003000"
    assert query["vnp_ExpireDate"] == "20260928010000"
    assert query["vnp_OrderInfo"] == "Thanh toan goi Pro DT0123456789"
    assert query["vnp_ReturnUrl"] == "https://pathmind.example/pricing"
    assert vnpay.verify(query)


def test_signature_rejects_tampering_and_ignores_hash_type(env):
    params = _result()
    assert vnpay.verify(params)
    assert vnpay.verify({**params, "vnp_SecureHashType": "HmacSHA512"})
    assert not vnpay.verify({**params, "vnp_Amount": "100"})
    assert not vnpay.verify({k: v for k, v in params.items() if k != "vnp_SecureHash"})
    # Empty values VNPAY sends still count toward the hash.
    with_empty = _result(vnp_BankTranNo="")
    assert vnpay.verify(with_empty)


def test_ipn_confirms_once_and_answers_vnpay_codes(env, db):
    _payment(db)
    client = _client(db)
    ok = client.get("/api/billing/vnpay/ipn", params=_result())
    assert ok.json() == {"RspCode": "00", "Message": "Confirm Success"}
    payment = db.query(Payment).one()
    assert payment.status == "completed" and payment.confirmed_by == "vnpay"
    assert payment.gateway_reference == "vnpay:14000000"
    from pathmind.services.quota_service import get_active_subscription

    assert get_active_subscription("u1", db).plan_id == "pro"

    assert client.get("/api/billing/vnpay/ipn", params=_result()).json()["RspCode"] == "02"
    assert client.get("/api/billing/vnpay/ipn", params=_result(code="DTFFFFFFFFFF")).json()["RspCode"] == "01"
    bad = {**_result(), "vnp_SecureHash": "00" * 64}
    assert client.get("/api/billing/vnpay/ipn", params=bad).json()["RspCode"] == "97"


def test_ipn_wrong_amount_and_failed_payment(env, db):
    _payment(db)
    client = _client(db)
    assert client.get("/api/billing/vnpay/ipn", params=_result(amount_vnd=1000)).json()["RspCode"] == "04"
    assert db.query(Payment).one().status == "pending"

    canceled = client.get("/api/billing/vnpay/ipn", params=_result(response="24", status="02"))
    assert canceled.json()["RspCode"] == "00"
    payment = db.query(Payment).one()
    assert payment.status == "failed" and "24" in payment.note


def test_return_page_confirms_for_the_owner_only(env, db):
    _payment(db)
    _payment(db, code="DT9999999999", user="u2")
    client = _client(db)
    res = client.post("/api/billing/payments/vnpay-return", json={"params": _result()})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed" and res.json()["vnpay_code"] == "00"

    other = client.post(
        "/api/billing/payments/vnpay-return", json={"params": _result(code="DT9999999999")}
    )
    assert other.status_code == 404
    assert db.query(Payment).filter_by(transaction_id="DT9999999999").one().status == "pending"

    _payment(db, code="DT1111111111")
    tampered = {**_result(code="DT1111111111"), "vnp_ResponseCode": "00", "vnp_Amount": "100"}
    assert client.post("/api/billing/payments/vnpay-return", json={"params": tampered}).status_code == 400


def test_checkout_returns_vnpay_url_and_only_vnpay_exists(env, db):
    client = _client(db)
    config = client.get("/api/billing/config").json()
    assert [g["id"] for g in config["gateways"]] == ["vnpay"]
    assert config["gateways"][0]["enabled"] and config["gateways"][0]["sandbox"]

    res = client.post("/api/billing/checkout", json={"plan_id": "pro"})
    assert res.status_code == 200, res.text
    body = res.json()
    url = body["checkout_url"]
    query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    assert query["vnp_TxnRef"] == body["transaction_id"]
    assert int(query["vnp_Amount"]) == body["amount_vnd"] * 100
    assert vnpay.verify(query)
    for gone in ("bank_transfer", "stripe", "crypto"):
        assert client.post("/api/billing/checkout", json={"plan_id": "pro", "gateway": gone}).status_code == 422


def test_vnpay_off_without_credentials(monkeypatch):
    monkeypatch.delenv("VNPAY_TMN_CODE", raising=False)
    monkeypatch.delenv("VNPAY_HASH_SECRET", raising=False)
    monkeypatch.delenv("BILLING_DEMO_MODE", raising=False)
    from pathmind.api.routers.billing import _gateways

    assert _gateways()[0]["enabled"] is False
    assert vnpay.status_summary()["configured"] is False
    assert not vnpay.verify({"vnp_TxnRef": "x", "vnp_SecureHash": "y"})
