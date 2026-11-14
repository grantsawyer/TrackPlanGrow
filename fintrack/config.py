"""Configuration: where data lives and how the app talks to Plaid.

Everything personal (the .env with Plaid keys, the SQLite database, TLS
certificates, backups, logs, and the Python venv) lives in FINTRACK_HOME,
which defaults to ~/.fintrack and must be OUTSIDE any git repository that
contains this code. `load_settings()` refuses to run otherwise.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

# The server only ever listens on the IPv4 loopback address. This is a
# constant on purpose: there is no setting that could expose it to the network.
HOST = "127.0.0.1"

DEFAULT_HOME = Path.home() / ".fintrack"
VALID_PLAID_ENVS = ("sandbox", "production")


class ConfigError(Exception):
    """Raised when configuration is missing or unsafe."""


@dataclass(frozen=True)
class Settings:
    home: Path
    plaid_env: str
    plaid_client_id: str
    plaid_secret: str  # the secret for the active plaid_env only
    port: int
    production_item_limit: int
    # Optional: stock prices from Alpaca (quotes.py, price_history.py; user's decision 2026-10-07,
    # replacing Finnhub). Both empty = Plaid's prices only.
    alpaca_key_id: str = ""
    alpaca_secret_key: str = ""
    # INVESTMENT_TRANSACTIONS=on in .env: sync investment transactions in Production too (see below).
    investment_transactions_opt_in: bool = False
    # REMOTE_ACCESS=on: phones reach fintrack through the gate (gate.py, remote.py; user's decision 2026-11-05): a
    # sandboxed process on REMOTE_PORT that requires a phone's client certificate, then hands the connection to a
    # second fintrack listener on 127.0.0.1:REMOTE_INNER_PORT. REMOTE_HOST is your home's public IP or hostname, as
    # phones dial it. fintrack itself still listens on loopback only.
    remote_access: bool = False
    remote_host: str = ""
    remote_port: int = 48443
    remote_inner_port: int = 8444
    # PHONE_OFFLINE_COPY=on: phones keep an encrypted read-only copy of the main pages for when the Mac doesn't answer
    # (web/offline.py, docs/phone-offline.md; user's decision 2026-11-11). Off: phones are told to wipe it.
    phone_offline_copy: bool = False

    @property
    def remote_origin(self) -> str:
        """The address phones open: https://<REMOTE_HOST>[:<REMOTE_PORT>] (IPv6 in brackets, :443 left out)."""
        host = f"[{self.remote_host}]" if ":" in self.remote_host else self.remote_host
        return f"https://{host}" + ("" if self.remote_port == 443 else f":{self.remote_port}")

    @property
    def sync_investment_transactions(self) -> bool:
        """Fetch investment transactions (contributions, withdrawals, dividends...) with each sync?

        Always in the Sandbox (free). In Production only if you opt in: Plaid bills investment transactions as their
        own subscription (docs checked 2026-10-09: "Calling /investments/transactions/get on an Item adds both the
        Investments Transactions and Investments Holdings subscriptions"), and no price is published, so it must not
        happen by accident. Check the price in the Plaid dashboard first."""
        return (not self.is_production) or self.investment_transactions_opt_in

    # --- Derived paths, all inside `home` ---
    @property
    def quotes_enabled(self) -> bool:
        """Both Alpaca keys are set, so live prices and price history are on."""
        return bool(self.alpaca_key_id and self.alpaca_secret_key)

    @property
    def secrets(self) -> list[str]:
        """Values the log filter must never write out."""
        return [self.plaid_secret, self.plaid_client_id, self.alpaca_key_id, self.alpaca_secret_key]

    @property
    def env_file(self) -> Path:
        return self.home / ".env"

    @property
    def db_path(self) -> Path:
        # One database per Plaid environment, so fake Sandbox banks and
        # transactions can never mix with real Production data.
        return self.home / f"fintrack-{self.plaid_env}.db"

    @property
    def certs_dir(self) -> Path:
        return self.home / "certs"

    @property
    def cert_file(self) -> Path:
        return self.certs_dir / "localhost.pem"

    @property
    def key_file(self) -> Path:
        return self.certs_dir / "localhost-key.pem"

    @property
    def backups_dir(self) -> Path:
        return self.home / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def base_url(self) -> str:
        # "localhost" (not 127.0.0.1) because that's the name on the mkcert
        # certificate and the redirect URI registered with Plaid.
        return f"https://localhost:{self.port}"

    @property
    def redirect_uri(self) -> str:
        """OAuth redirect URI; must be registered in the Plaid dashboard."""
        return f"{self.base_url}/oauth-return"

    @property
    def is_production(self) -> bool:
        return self.plaid_env == "production"


def resolve_home() -> Path:
    return Path(os.environ.get("FINTRACK_HOME", DEFAULT_HOME)).expanduser().resolve()


def find_code_repo_root() -> Path | None:
    """Return the git working tree that contains this code, if any."""
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def check_home_outside_repo(home: Path) -> None:
    """Refuse a data directory that would put personal data under git."""
    repo = find_code_repo_root()
    if repo is not None and home.is_relative_to(repo):
        raise ConfigError(
            f"FINTRACK_HOME ({home}) is inside the code repository ({repo}). "
            "Financial data and secrets must live outside the repo."
        )


def load_settings(require_plaid: bool = True) -> Settings:
    """Read settings from FINTRACK_HOME/.env, with real env vars taking priority.

    `require_plaid=False` lets commands like `init` and `backup` run before
    Plaid keys have been filled in.
    """
    home = resolve_home()
    check_home_outside_repo(home)

    file_values = dotenv_values(home / ".env") if (home / ".env").exists() else {}

    def get(name: str, default: str = "") -> str:
        value = os.environ.get(name)
        if value is None:
            value = file_values.get(name) or default
        return value.strip()

    plaid_env = get("PLAID_ENV", "sandbox").lower()
    if plaid_env not in VALID_PLAID_ENVS:
        raise ConfigError(f"PLAID_ENV must be one of {VALID_PLAID_ENVS}, got {plaid_env!r}")

    try:
        port = int(get("FINTRACK_PORT", "8443"))
        item_limit = int(get("PLAID_PRODUCTION_ITEM_LIMIT", "10"))
        remote_port = int(get("REMOTE_PORT", "48443"))
        remote_inner_port = int(get("REMOTE_INNER_PORT", "8444"))
    except ValueError as exc:
        raise ConfigError(f"Invalid number in .env: {exc}") from None

    client_id = get("PLAID_CLIENT_ID")
    secret = get(f"PLAID_SECRET_{plaid_env.upper()}")
    if require_plaid and not (client_id and secret):
        raise ConfigError(
            f"PLAID_CLIENT_ID and PLAID_SECRET_{plaid_env.upper()} must be set in {home / '.env'}"
        )

    return Settings(
        home=home,
        plaid_env=plaid_env,
        plaid_client_id=client_id,
        plaid_secret=secret,
        port=port,
        production_item_limit=item_limit,
        alpaca_key_id=get("ALPACA_API_KEY_ID"),
        alpaca_secret_key=get("ALPACA_API_SECRET_KEY"),
        investment_transactions_opt_in=get("INVESTMENT_TRANSACTIONS").lower() in ("1", "on", "true", "yes"),
        remote_access=get("REMOTE_ACCESS").lower() in ("1", "on", "true", "yes"),
        remote_host=_phone_host(get("REMOTE_HOST")),
        remote_port=remote_port,
        remote_inner_port=remote_inner_port,
        phone_offline_copy=get("PHONE_OFFLINE_COPY").lower() in ("1", "on", "true", "yes"),
    )


def _phone_host(text: str) -> str:
    """REMOTE_HOST: an IP address or a hostname (no scheme, port or path); never a loopback address."""
    import ipaddress
    import re

    text = text.strip().strip("[]")
    if not text:
        return ""
    try:
        if ipaddress.ip_address(text).is_loopback:
            raise ConfigError("REMOTE_HOST must be your home's public address, not a loopback address")
        return text
    except ValueError:
        pass
    if not re.fullmatch(r"(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*", text):
        raise ConfigError(f"REMOTE_HOST must be an IP address or hostname, got {text!r}")
    return text.lower()
