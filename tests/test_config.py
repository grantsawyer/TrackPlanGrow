import pytest

from fintrack.config import HOST, ConfigError, find_code_repo_root, load_settings


def test_host_is_loopback_constant():
    assert HOST == "127.0.0.1"


def test_defaults_to_sandbox(settings):
    assert settings.plaid_env == "sandbox"
    assert settings.plaid_secret == "test-sandbox-secret-456"
    assert settings.redirect_uri == "https://localhost:8443/oauth-return"
    assert settings.production_item_limit == 10


def test_production_uses_production_secret(home):
    (home / ".env").write_text(
        "PLAID_ENV=production\nPLAID_CLIENT_ID=cid\n"
        "PLAID_SECRET_SANDBOX=sandbox-secret\nPLAID_SECRET_PRODUCTION=prod-secret\n"
    )
    settings = load_settings()
    assert settings.is_production
    assert settings.plaid_secret == "prod-secret"


def test_missing_keys_rejected_when_required(home):
    with pytest.raises(ConfigError):
        load_settings()
    assert load_settings(require_plaid=False).plaid_client_id == ""


def test_invalid_env_rejected(home):
    (home / ".env").write_text("PLAID_ENV=development\n")
    with pytest.raises(ConfigError):
        load_settings(require_plaid=False)


def test_home_inside_code_repo_rejected(monkeypatch):
    repo = find_code_repo_root()
    if repo is None:
        pytest.skip("not running from a git checkout")
    monkeypatch.setenv("FINTRACK_HOME", str(repo / "data"))
    with pytest.raises(ConfigError, match="inside the code repository"):
        load_settings(require_plaid=False)
