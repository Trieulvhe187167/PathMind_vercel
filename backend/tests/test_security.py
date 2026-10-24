import time

from app.security import create_token, decode_token, hash_password, verify_password


def test_password_hash_roundtrip_and_salt():
    h1, h2 = hash_password("abc12345"), hash_password("abc12345")
    assert h1 != h2 and h1.startswith("scrypt$")
    assert verify_password("abc12345", h1)
    assert not verify_password("abc12346", h1)
    assert not verify_password("x", "garbage")


def test_token_signature_expiry_and_tamper():
    t = create_token("k", "u1", 0, 60)
    assert decode_token("k", t)["sub"] == "u1"
    assert decode_token("other", t) is None
    body, sig = t.split(".")
    assert decode_token("k", body + "x." + sig) is None
    assert decode_token("k", "nonsense") is None
    expired = create_token("k", "u1", 0, -1)
    time.sleep(0.01)
    assert decode_token("k", expired) is None


def test_throttle_reads_do_not_grow_memory_and_window_expires():
    from app.ratelimit import LoginThrottle

    now = [0.0]
    t = LoginThrottle(2, 60, clock=lambda: now[0])
    for i in range(100):
        assert t.retry_after("1.1.1.1", f"nobody{i}") == 0
    assert t._fails == {}
    t.failure("ip", "Bob")
    t.failure("ip", " bob ")
    assert t.retry_after("ip", "BOB") > 0
    assert t.retry_after("other-ip", "bob") == 0  # account-wide limit is 4x
    now[0] = 61
    assert t.retry_after("ip", "bob") == 0
