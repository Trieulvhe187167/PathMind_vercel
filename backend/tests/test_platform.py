"""Foundation: secrets, storage (disk and S3), text extraction, tasks/cron, AI providers, billing."""

from datetime import datetime
import io
import json
import zipfile

import httpx
import pytest

from conftest import make_client, register
from app.services import extract, llm, secretbox, storage, tasks, vnpay


def _admin(app):
    c = make_client(app)
    register(c, "alice")
    return c


def mock_ai(admin, embedding=False):
    p = admin.post("/api/admin/ai/providers", json={"name": "Mock", "kind": "mock"}).json()["provider"]
    m = admin.post("/api/admin/ai/models", json={"provider_id": p["id"], "model_name": "mock-chat"}).json()["model"]
    if embedding:
        admin.post("/api/admin/ai/models", json={"provider_id": p["id"], "model_name": "mock-embed", "purpose": "embedding"})
    return p, m


# ── Secrets ─────────────────────────────────────────────────────────────────


def test_secretbox_roundtrip_and_tamper(app):
    token = secretbox.encrypt("sk-test-123 ✓")
    assert token.startswith("enc:v1:") and "sk-test" not in token
    assert secretbox.decrypt(token) == "sk-test-123 ✓"
    assert secretbox.encrypt("x") != secretbox.encrypt("x")  # random nonce
    bad = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(secretbox.SecretError):
        secretbox.decrypt(bad)
    assert secretbox.try_decrypt(bad) == ""


def test_settings_store_secrets_encrypted(app):
    admin = _admin(app)
    r = admin.put("/api/admin/settings/smtp", json={"host": "smtp.example.com", "password": "hunter2", "port": "2525"})
    s = r.json()["settings"]
    assert s["password_set"] is True and "password_enc" not in s and s["port"] == 2525
    from app.models import AppSetting

    with app.state.session_factory() as db:
        raw = db.get(AppSetting, "smtp").value
        assert raw["password_enc"].startswith("enc:v1:")
    s = admin.put("/api/admin/settings/smtp", json={"password": ""}).json()["settings"]
    assert s["password_set"] is False
    assert admin.put("/api/admin/settings/general", json={"public_url": "ftp://x"}).status_code == 422
    assert admin.get("/api/admin/settings/nope").status_code == 404


# ── Files and storage ──────────────────────────────────────────────────────


def test_upload_download_on_disk(app):
    admin = _admin(app)
    r = admin.post("/api/files", files={"file": ("notes.txt", b"hello world", "text/plain")})
    assert r.status_code == 201, r.text
    f = r.json()["file"]
    assert f["size_bytes"] == 11 and f["in_object_storage"] is False
    d = admin.get(f"/api/files/{f['id']}/download")
    assert d.status_code == 200 and d.content == b"hello world"
    other = make_client(app)
    register(other, "bob")
    assert other.get(f"/api/files/{f['id']}/download").status_code == 404
    assert admin.delete(f"/api/files/{f['id']}").json() == {"ok": True}
    assert admin.get(f"/api/files/{f['id']}").status_code == 404


class FakeS3:
    def __init__(self):
        self.objects = {}

    def handler(self, request: httpx.Request):
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AK/")
        key = request.url.path
        if request.method == "PUT":
            self.objects[key] = request.content
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
        if request.method == "DELETE":
            self.objects.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(405)


def test_s3_storage_gateway(app, monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(storage, "S3_TRANSPORT", httpx.MockTransport(fake.handler))
    admin = _admin(app)
    body = {"name": "MinIO", "endpoint_url": "http://minio.local:9000", "bucket": "pm", "access_key_id": "AK",
            "secret_key": "SK", "path_style": True, "key_prefix": "pathmind"}
    b = admin.post("/api/admin/storage", json=body).json()["backend"]
    assert b["secret_key_set"] and b["key_prefix"] == "pathmind/" and b["status"] == "untested"
    # Cannot become default before a successful test.
    assert admin.put("/api/admin/storage-default", json={"backend_id": b["id"]}).status_code == 409
    t = admin.post(f"/api/admin/storage/{b['id']}/test").json()["backend"]
    assert t["status"] == "ok", t
    assert admin.put("/api/admin/storage-default", json={"backend_id": b["id"]}).json()["default"] == b["id"]

    f = admin.post("/api/files", files={"file": ("a.txt", b"in the bucket", "text/plain")}).json()["file"]
    assert f["in_object_storage"] is True
    assert any(k.startswith("/pm/pathmind/upload/") for k in fake.objects)
    r = admin.get(f"/api/files/{f['id']}/download", follow_redirects=False)
    assert r.status_code == 307 and "X-Amz-Signature=" in r.headers["location"]
    assert admin.get(f"/api/files/{f['id']}/download?inline=true").content == b"in the bucket"
    # Files are there: deleting the storage or moving its bucket is refused.
    assert admin.delete(f"/api/admin/storage/{b['id']}").status_code == 409
    assert admin.put(f"/api/admin/storage/{b['id']}", json={**body, "bucket": "other"}).status_code == 409
    # Back to disk for new files; old ones stay readable.
    admin.put("/api/admin/storage-default", json={"backend_id": None})
    g = admin.post("/api/files", files={"file": ("b.txt", b"on disk", "text/plain")}).json()["file"]
    assert g["in_object_storage"] is False
    assert admin.get(f"/api/files/{f['id']}/download?inline=true").content == b"in the bucket"


def test_s3_presign_and_aws_urls():
    from app.services.s3 import S3Client, S3Config

    c = S3Client(S3Config(bucket="b", access_key="AK", secret_key="SK", region="ap-southeast-1"))
    url = c.presign_get("folder/file name.pdf", 60, "file name.pdf")
    assert url.startswith("https://b.s3.ap-southeast-1.amazonaws.com/folder/file%20name.pdf?")
    assert "X-Amz-Expires=60" in url and "X-Amz-Signature=" in url


# ── Text extraction ─────────────────────────────────────────────────────────


def _docx(paragraphs):
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    parts = []
    for style, text in paragraphs:
        ppr = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else ""
        parts.append(f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>")
    body = "".join(parts)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document {ns}><w:body>{body}</w:body></w:document>")
    return buf.getvalue()


def test_extract_docx_html_and_passages():
    data = _docx([("Heading1", "Safety"), ("", "Wear gloves."), ("Heading2", "Chemicals"), ("", "Store acids low.")])
    ex = extract.extract("manual.docx", data)
    assert ex.text.splitlines()[0] == "# Safety" and "## Chemicals" in ex.text
    html = extract.extract("p.html", b"<h1>Title</h1><p>One</p><script>x()</script><h2>Sub</h2><p>Two</p>")
    assert "# Title" in html.text and "x()" not in html.text
    ps = extract.split_passages(ex)
    assert [p.heading_path for p in ps] == ["Safety", "Safety > Chemicals"]
    long = extract.Extracted("# A\n" + ("word " * 600))
    assert len(extract.split_passages(long, max_chars=500)) > 2
    with pytest.raises(extract.ExtractError):
        extract.extract("x.exe", b"MZ")
    assert extract.headings("# A\nbody a\n## B\nbody b")[1] == (2, "B", "body b")


# ── Tasks and cron ───────────────────────────────────────────────────────────


def test_cron_next_run():
    t = datetime(2026, 10, 13, 7, 59)
    assert tasks.next_run("0 8 * * *", t) == datetime(2026, 10, 13, 8, 0)
    assert tasks.next_run("0 8 * * *", datetime(2026, 10, 13, 8, 0)) == datetime(2026, 10, 14, 8, 0)
    assert tasks.next_run("*/30 * * * *", datetime(2026, 10, 13, 8, 5)) == datetime(2026, 10, 13, 8, 30)
    assert tasks.next_run("5 0 1 * *", t) == datetime(2026, 11, 1, 0, 5)
    with pytest.raises(ValueError):
        tasks.next_run("bad", t)


def test_builtin_jobs_and_admin_controls(app):
    admin = _admin(app)
    jobs = admin.get("/api/admin/jobs").json()["jobs"]
    assert {j["action"]["type"] for j in jobs} >= {"study_reminder", "review_due", "billing_housekeeping"}
    job = next(j for j in jobs if j["action"]["type"] == "billing_housekeeping")
    assert admin.patch(f"/api/admin/jobs/{job['id']}", json={"cron": "nope"}).status_code == 422
    assert admin.patch(f"/api/admin/jobs/{job['id']}", json={"is_enabled": False}).json()["job"]["is_enabled"] is False
    r = admin.post(f"/api/admin/jobs/{job['id']}/run").json()
    assert "expired" in r["message"]


def test_task_runner_records_progress_and_failure(app):
    runner = app.state.tasks

    def ok(db, task, report):
        report(0.5)
        return {"n": 3}

    def boom(db, task, report):
        raise RuntimeError("nope")

    from app.models import BackgroundTask

    a, b = runner.submit("test", None, {}, ok), runner.submit("test", None, {}, boom)
    with app.state.session_factory() as db:
        ta, tb = db.get(BackgroundTask, a), db.get(BackgroundTask, b)
        assert (ta.status, ta.progress, ta.result) == ("done", 1.0, {"n": 3})
        assert tb.status == "failed" and "nope" in tb.error


# ── AI providers ─────────────────────────────────────────────────────────────


def test_ai_admin_and_metering(app):
    admin = _admin(app)
    user = make_client(app)
    register(user, "bob")
    assert user.get("/api/ai/models").json() == {"models": [], "default": None}
    p, m = mock_ai(admin, embedding=True)
    assert m["is_default"] is True
    listed = user.get("/api/ai/models").json()
    assert listed["default"] == m["id"] and listed["models"][0]["model"] == "mock-chat"
    assert admin.post(f"/api/admin/ai/providers/{p['id']}/test").json()["ok"] is True
    r = admin.post("/api/admin/ai/try", json={"model_id": m["id"], "prompt": "ping"}).json()
    assert r["ok"] and "ping" in r["message"]
    with app.state.session_factory() as db:
        from app.models import LLMCall, UsageDaily

        call = db.query(LLMCall).one()
        assert call.model == "mock-chat" and call.prompt_tokens > 0 and call.purpose == "admin_test"
        assert db.query(UsageDaily).count() == 1
        vecs, model = llm.embed(db, ["alpha beta", "alpha beta"])
        assert model == "mock-embed" and llm.cosine(vecs[0], vecs[1]) > 0.99
    # Provider keys are stored encrypted and never returned.
    q = admin.post("/api/admin/ai/providers", json={"name": "OpenAI", "kind": "openai", "api_key": "sk-abc"}).json()["provider"]
    assert q["api_key_set"] and "sk-abc" not in json.dumps(admin.get("/api/admin/ai/providers").json())
    assert admin.post("/api/admin/ai/providers", json={"name": "X", "kind": "openai_compatible"}).status_code == 422
    # Changing the host drops the key unless a new one is given.
    q2 = admin.put(f"/api/admin/ai/providers/{q['id']}", json={"name": "OpenAI", "kind": "openai",
                                                               "base_url": "https://evil.example.com/v1"}).json()["provider"]
    assert q2["api_key_set"] is False
    assert user.get("/api/admin/ai/providers").status_code == 403


def test_openai_stream_parsing(app, monkeypatch):
    def handler(request):
        assert request.headers["authorization"] == "Bearer sk-1"
        body = (
            'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":2}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(llm, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    admin = _admin(app)
    p = admin.post("/api/admin/ai/providers", json={"name": "OAI", "kind": "openai", "api_key": "sk-1"}).json()["provider"]
    m = admin.post("/api/admin/ai/models", json={"provider_id": p["id"], "model_name": "gpt-4o-mini"}).json()["model"]
    r = admin.post("/api/admin/ai/try", json={"model_id": m["id"]}).json()
    assert r == {"ok": True, "message": "Hello"}
    with app.state.session_factory() as db:
        from app.models import LLMCall

        call = db.query(LLMCall).one()
        assert (call.prompt_tokens, call.completion_tokens) == (12, 2)
        assert float(call.cost_usd) == pytest.approx((12 * 0.15 + 2 * 0.6) / 1e6, abs=1e-6)


def test_parse_json_tolerates_fences():
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json('Sure! [1, 2] done') == [1, 2]
    assert llm.parse_json("nothing", {"x": 0}) == {"x": 0}


# ── Billing ──────────────────────────────────────────────────────────────────


def test_plans_summary_and_unlimited_roles(app):
    admin = _admin(app)
    user = make_client(app)
    register(user, "bob")
    plans = user.get("/api/billing/plans").json()["plans"]
    assert [p["id"] for p in plans] == ["free", "plus", "pro", "enterprise"]
    s = user.get("/api/billing/summary").json()
    assert s["plan"]["id"] == "free" and s["unlimited"] is False and s["entitlements"]["courses"] is False
    a = admin.get("/api/billing/summary").json()
    assert a["unlimited"] is True and a["entitlements"]["courses"] is True


def test_demo_checkout_and_admin_payment_actions(app):
    admin = _admin(app)
    user = make_client(app)
    bob = register(user, "bob").json()["user"]
    assert user.post("/api/billing/checkout", json={"plan_id": "plus", "gateway": "demo"}).status_code == 409
    admin.put("/api/admin/settings/billing", json={"demo_mode": True})
    r = user.post("/api/billing/checkout", json={"plan_id": "plus", "gateway": "demo"}).json()
    txn = r["payment"]["transaction_id"]
    assert r["payment"]["status"] == "pending" and r["payment"]["amount_vnd"] == round(4.99 * 25400)
    done = user.post(f"/api/billing/payments/{txn}/simulate").json()
    assert done["payment"]["status"] == "completed" and done["summary"]["plan"]["id"] == "plus"
    assert done["summary"]["entitlements"]["courses"] is True
    # Paying again for the same plan extends it instead of starting over.
    txn2 = user.post("/api/billing/checkout", json={"plan_id": "plus", "gateway": "demo"}).json()["payment"]["transaction_id"]
    user.post(f"/api/billing/payments/{txn2}/simulate")
    end = user.get("/api/billing/summary").json()["subscription"]["period_end"]
    assert end > "2026-12"
    rep = admin.post(f"/api/admin/billing/payments/{txn2}/refund", json={"note": "test"}).json()
    assert rep["payment"]["status"] == "refunded"
    assert user.get("/api/billing/summary").json()["plan"]["id"] == "free"
    report = admin.get("/api/admin/billing/report").json()
    assert report["revenue_usd"] == pytest.approx(4.99) and report["by_plan"][0]["plan_id"] == "plus"
    # Admin grant and revoke.
    g = admin.post(f"/api/admin/billing/users/{bob['id']}/plan", json={"plan_id": "pro", "days": 0}).json()
    assert g["subscription"]["period_end"] is None and g["summary"]["plan"]["id"] == "pro"
    me = admin.get("/api/auth/me").json()["user"]
    assert admin.post(f"/api/admin/billing/users/{me['id']}/plan", json={"plan_id": "pro"}).status_code == 409
    assert admin.post(f"/api/admin/billing/users/{bob['id']}/plan/cancel").json() == {"ok": True}
    assert user.get("/api/billing/summary").json()["plan"]["id"] == "free"


def test_vnpay_signature_and_ipn(app):
    admin = _admin(app)
    user = make_client(app)
    register(user, "bob")
    admin.put("/api/admin/settings/vnpay", json={"tmn_code": "TMN1", "hash_secret": "SECRET"})
    r = user.post("/api/billing/checkout", json={"plan_id": "pro", "interval": "yearly"}).json()
    assert r["pay_url"].startswith("https://sandbox.vnpayment.vn/") and "vnp_SecureHash=" in r["pay_url"]
    p = r["payment"]
    params = {"vnp_TxnRef": p["transaction_id"], "vnp_Amount": str(p["amount_vnd"] * 100), "vnp_ResponseCode": "00",
              "vnp_TransactionStatus": "00", "vnp_TransactionNo": "999"}
    params["vnp_SecureHash"] = vnpay.sign(params, "SECRET")
    bad = dict(params, vnp_SecureHash="0" * 128)
    anon = make_client(app)
    assert anon.get("/api/billing/vnpay/ipn", params=bad).json()["RspCode"] == "97"
    assert anon.get("/api/billing/vnpay/ipn", params=params).json()["RspCode"] == "00"
    assert anon.get("/api/billing/vnpay/ipn", params=params).json()["RspCode"] == "02"
    assert user.get("/api/billing/summary").json()["plan"]["id"] == "pro"
    wrong = {**params, "vnp_Amount": "100"}
    wrong["vnp_SecureHash"] = vnpay.sign({k: v for k, v in wrong.items() if k != "vnp_SecureHash"}, "SECRET")
    assert anon.get("/api/billing/vnpay/ipn", params=wrong).json()["RspCode"] == "04"


def test_credit_limit_blocks_ai_calls(app):
    admin = _admin(app)
    user = make_client(app)
    register(user, "bob")
    mock_ai(admin)
    admin.put("/api/admin/ai/prices", json={"model_key": "mock", "input_per_m": 100, "output_per_m": 100,
                                            "aliases": ["mock-chat"]})
    plan = next(p for p in admin.get("/api/admin/billing/plans").json()["plans"] if p["id"] == "free")
    admin.put("/api/admin/billing/plans", json={**plan, "credits_per_day": 10})
    from app.models import User

    with app.state.session_factory() as db:
        bob = db.query(User).filter_by(username="bob").one()
        llm.complete(db, user=bob, messages=[{"role": "user", "content": "first question " * 20}])
        db.commit()
        with pytest.raises(Exception) as exc:
            llm.complete(db, user=bob, messages=[{"role": "user", "content": "again"}])
        assert getattr(exc.value, "status_code", None) == 402


def test_feature_reservation_lifetime_trial(app):
    from app.models import User
    from app.services import billing

    _admin(app)
    user = make_client(app)
    register(user, "bob")
    with app.state.session_factory() as db:
        bob = db.query(User).filter_by(username="bob").one()
        billing.reserve(db, bob, "books")
        db.commit()
        with pytest.raises(Exception) as exc:
            billing.reserve(db, bob, "books")
        assert exc.value.status_code == 402 and exc.value.detail["upgrade_to"]["id"] == "plus"
        billing.release(db, bob, "books")
        billing.reserve(db, bob, "books")
