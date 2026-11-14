"""Production pre-flight check: is everything ready to connect real banks?

Checks the PRODUCTION configuration even while PLAID_ENV is still sandbox, and
creates no Item: a link token only permits opening Link. An Item is created only
when you finish a bank login in Link AND the app exchanges the public token,
which this never does. Creating one link token proves Plaid accepts the
Production credentials and the registered redirect URI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography import x509

from fintrack import db
from fintrack.config import Settings
from fintrack.crypto import has_key
from fintrack.item_budget import production_item_budget
from fintrack.plaid_client import PlaidError

CERT_WARN_DAYS = 30


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def _cert_days_left(path) -> int | None:
    try:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
    except (OSError, ValueError):
        return None
    return (cert.not_valid_after_utc - datetime.now(timezone.utc)).days


def run(settings: Settings, gateway_factory) -> list[Check]:
    """`settings` must be the production settings. `gateway_factory(settings)` makes the Plaid client."""
    checks: list[Check] = []

    keys_ok = bool(settings.plaid_client_id and settings.plaid_secret)
    checks.append(Check(
        "Plaid Production keys", keys_ok,
        "client ID and Production secret found in .env" if keys_ok else "missing",
        "" if keys_ok else f"Add PLAID_CLIENT_ID and PLAID_SECRET_PRODUCTION to {settings.env_file} "
                           "(Plaid dashboard → Developers → Keys).",
    ))

    key_ok = has_key(settings.home)
    checks.append(Check("Token encryption key", key_ok, "in the macOS Keychain" if key_ok else "missing",
                        "" if key_ok else "Run `fintrack init`."))

    days = _cert_days_left(settings.cert_file) if settings.cert_file.exists() else None
    cert_ok = days is not None and days > CERT_WARN_DAYS
    checks.append(Check(
        "HTTPS certificate", cert_ok,
        f"valid for {days} more days" if days is not None else "not found",
        "" if cert_ok else "Run `fintrack certs`.",
    ))

    conn = db.connect(settings.db_path)
    try:
        db.migrate(conn)
        budget = production_item_budget(conn, settings.production_item_limit)
    finally:
        conn.close()
    checks.append(Check(
        "Production Item budget", not budget.exhausted,
        f"{budget.used} of {budget.limit} used, {budget.remaining} remaining (database {settings.db_path.name})",
        "" if not budget.exhausted else "All Trial Items are used; Plaid requires a paid plan for more.",
    ))

    if keys_ok:
        try:
            gateway_factory(settings).create_link_token(kind="banking")
            checks.append(Check(
                "Plaid accepts credentials + redirect URI", True,
                f"created a Production link token for {settings.redirect_uri} (no Item created)",
            ))
        except PlaidError as err:
            fix = "Check the Production secret in .env."
            if "redirect" in err.message.lower() or err.code in ("INVALID_FIELD", "INVALID_REDIRECT_URI"):
                fix = f"Register {settings.redirect_uri} under Developers → API → Allowed redirect URIs."
            elif err.code in ("INVALID_API_KEYS", "INVALID_SECRET"):
                fix = "The Production secret or client ID is wrong, or Production access isn't enabled yet."
            checks.append(Check("Plaid accepts credentials + redirect URI", False, f"{err.code}: {err.message}", fix))
    return checks
