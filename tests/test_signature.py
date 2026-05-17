"""Tests for ClickUp webhook signature verification."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CLICKUP_TEAM_ID", "x")
os.environ.setdefault("CLICKUP_USER_ID", "1")
os.environ.setdefault("CLICKUP_TOKEN", "x")
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/x")
os.environ.setdefault("DASHBOARD_USER", "u")
os.environ.setdefault("DASHBOARD_PASSWORD", "p")

import hashlib
import hmac

# Import is safe because db._pool is initialised lazily.
import db
db.init_db = lambda: None  # type: ignore[assignment]

from main import verify_signature  # noqa: E402


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    body = b'{"event":"taskAssigneeUpdated"}'
    secret = "s3cret"
    assert verify_signature(body, _sign(body, secret), secret) is True


def test_wrong_signature_rejected():
    body = b'{"event":"taskAssigneeUpdated"}'
    assert verify_signature(body, "0" * 64, "s3cret") is False


def test_tampered_body_rejected():
    secret = "s3cret"
    sig = _sign(b'{"event":"taskAssigneeUpdated"}', secret)
    tampered = b'{"event":"taskAssigneeUpdated","extra":1}'
    assert verify_signature(tampered, sig, secret) is False


def test_wrong_secret_rejected():
    body = b'{"x":1}'
    sig = _sign(body, "real-secret")
    assert verify_signature(body, sig, "guessed-secret") is False


def test_empty_signature_rejected():
    body = b'{"x":1}'
    assert verify_signature(body, "", "s3cret") is False
