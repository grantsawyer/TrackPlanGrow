"""Shared test fixtures.

Every test runs against a throwaway data folder and an in-memory keyring,
so tests never touch ~/.fintrack or the real macOS Keychain.
"""

from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend

from fintrack import db
from fintrack.config import load_settings


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture(autouse=True)
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated FINTRACK_HOME with no Plaid env vars leaking in from the shell."""
    home = tmp_path / "fintrack-home"
    home.mkdir()
    monkeypatch.setenv("FINTRACK_HOME", str(home))
    for var in ("PLAID_ENV", "PLAID_CLIENT_ID", "PLAID_SECRET_SANDBOX",
                "PLAID_SECRET_PRODUCTION", "FINTRACK_PORT", "PLAID_PRODUCTION_ITEM_LIMIT"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def settings(home):
    (home / ".env").write_text(
        "PLAID_CLIENT_ID=test-client-id-123\nPLAID_SECRET_SANDBOX=test-sandbox-secret-456\n"
    )
    return load_settings()


@pytest.fixture
def prod_settings(home):
    (home / ".env").write_text(
        "PLAID_ENV=production\nPLAID_CLIENT_ID=test-client-id-123\n"
        "PLAID_SECRET_PRODUCTION=test-production-secret-789\nPLAID_PRODUCTION_ITEM_LIMIT=3\n"
    )
    return load_settings()


def _open(settings):
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    return conn


@pytest.fixture
def conn(settings):
    conn = _open(settings)
    yield conn
    conn.close()


@pytest.fixture
def prod_conn(prod_settings):
    conn = _open(prod_settings)
    yield conn
    conn.close()


@pytest.fixture
def cipher(home):
    from fintrack.crypto import TokenCipher, ensure_key

    ensure_key(home)
    return TokenCipher(home)


@pytest.fixture
def gateway():
    from tests.fakes import FakeGateway

    return FakeGateway()


@pytest.fixture(autouse=True)
def fresh_price_history_timer():
    """price_history.refresh_if_due remembers its last run per process; tests start fresh."""
    from fintrack import price_history
    price_history.reset_refresh_timer()
    yield
    price_history.reset_refresh_timer()
