"""HTTP Basic auth gate (production readiness #1).

Disabled when BASIC_AUTH_USER/PASSWORD are unset (local/test); enforced for the
whole app when both are set. Uses a static asset so the check needs no DB.
"""

import base64

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app

_PATH = "/static/js/app.js"


def test_auth_disabled_by_default():
    assert settings.auth_enabled is False
    assert TestClient(app).get(_PATH).status_code == 200


def test_auth_enforced_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "BASIC_AUTH_USER", "admin")
    monkeypatch.setattr(settings, "BASIC_AUTH_PASSWORD", "s3cret")
    assert settings.auth_enabled is True
    c = TestClient(app)

    # no credentials -> 401 + challenge
    r = c.get(_PATH)
    assert r.status_code == 401
    assert "Basic" in r.headers.get("WWW-Authenticate", "")

    # wrong credentials -> 401
    bad = base64.b64encode(b"admin:nope").decode()
    assert c.get(_PATH, headers={"Authorization": f"Basic {bad}"}).status_code == 401

    # correct credentials -> 200
    good = base64.b64encode(b"admin:s3cret").decode()
    assert c.get(_PATH, headers={"Authorization": f"Basic {good}"}).status_code == 200
