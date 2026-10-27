"""initData HMAC validation tests.  Run:  pytest tests/"""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.auth import validate_init_data

TOKEN = "123456:TEST-TOKEN"


def sign(fields: dict, token: str = TOKEN) -> str:
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


def fields(age: int = 0, uid: int = 42) -> dict:
    return {"auth_date": str(int(time.time()) - age), "query_id": "AAH",
            "user": json.dumps({"id": uid, "username": "alex"}), "signature": "x"}


def test_valid_signature_returns_user():
    assert validate_init_data(sign(fields()), TOKEN)["id"] == 42


def test_tampered_user_is_rejected():
    forged = sign(fields(uid=42)).replace("%22id%22%3A+42", "%22id%22%3A+99")
    with pytest.raises(ValueError):
        validate_init_data(forged, TOKEN)


def test_wrong_bot_token_is_rejected():
    with pytest.raises(ValueError):
        validate_init_data(sign(fields()), "999:OTHER")


def test_expired_init_data_is_rejected():
    with pytest.raises(ValueError):
        validate_init_data(sign(fields(age=3 * 24 * 3600)), TOKEN)


def test_missing_hash_is_rejected():
    with pytest.raises(ValueError):
        validate_init_data(urlencode(fields()), TOKEN)
