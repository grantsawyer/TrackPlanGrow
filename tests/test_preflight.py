"""Production pre-flight: reports readiness and never creates an Item."""

import subprocess

import pytest

from fintrack import preflight
from fintrack.plaid_client import PlaidError
from tests.fakes import FakeGateway


@pytest.fixture
def ready(prod_settings, cipher):
    """Production settings with keys, Keychain key, and a fresh self-signed cert."""
    prod_settings.certs_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365", "-subj", "/CN=localhost",
         "-keyout", str(prod_settings.key_file), "-out", str(prod_settings.cert_file)],
        check=True, capture_output=True,
    )
    return prod_settings


def by_name(checks):
    return {c.name: c for c in checks}


def test_all_ready_and_no_item_created(ready):
    gateway = FakeGateway()
    checks = by_name(preflight.run(ready, lambda s: gateway))
    assert all(c.ok for c in checks.values()), {k: c.detail for k, c in checks.items()}
    assert "0 of 3 used" in checks["Production Item budget"].detail
    # Only a link token was requested: no exchange, nothing that creates an Item.
    assert gateway.calls == [("create_link_token", "banking", None)]


def test_bad_redirect_uri_explained(ready):
    gateway = FakeGateway()

    def refuse(**kwargs):
        raise PlaidError("INVALID_FIELD", "redirect_uri must be configured in the developer dashboard")

    gateway.create_link_token = refuse
    check = by_name(preflight.run(ready, lambda s: gateway))["Plaid accepts credentials + redirect URI"]
    assert not check.ok and "Allowed redirect URIs" in check.fix


def test_missing_keys_and_cert(home, cipher):
    (home / ".env").write_text("PLAID_ENV=production\n")
    from fintrack.config import load_settings

    settings = load_settings(require_plaid=False)
    gateway = FakeGateway()
    checks = by_name(preflight.run(settings, lambda s: gateway))
    assert not checks["Plaid Production keys"].ok and "PLAID_SECRET_PRODUCTION" in checks["Plaid Production keys"].fix
    assert not checks["HTTPS certificate"].ok
    assert "Plaid accepts credentials + redirect URI" not in checks   # not attempted without keys
    assert gateway.calls == []
