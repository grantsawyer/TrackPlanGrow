"""The only module that talks to Plaid.

`PlaidGateway` wraps the official plaid-python SDK in a few small methods that
return plain dicts. Everything else in the app goes through it, which keeps
Plaid usage easy to audit and lets tests substitute a fake (no network).

Item-creating calls: only `create_link_token(..., access_token=None)` can lead
to a new Item (once you finish Link). The service layer gates it behind the
Production consent check. Update mode (`access_token` given) never creates one.

Never log the access tokens passed into these methods.
"""

from __future__ import annotations

import json
from datetime import date
from dataclasses import dataclass

import plaid
import urllib3
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.institutions_get_by_id_request_options import InstitutionsGetByIdRequestOptions
from plaid.model.institutions_search_request import InstitutionsSearchRequest
from plaid.model.institutions_search_request_options import InstitutionsSearchRequestOptions
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.investments_transactions_get_request import InvestmentsTransactionsGetRequest
from plaid.model.investments_transactions_get_request_options import InvestmentsTransactionsGetRequestOptions
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.liabilities_get_request import LiabilitiesGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_update import LinkTokenCreateRequestUpdate
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_transactions import LinkTokenTransactions
from plaid.model.products import Products
from plaid.model.sandbox_item_reset_login_request import SandboxItemResetLoginRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.transactions_sync_request_options import TransactionsSyncRequestOptions

from fintrack.config import Settings

# Plaid needs a stable, non-identifying ID for "the end user". There's only one of us.
CLIENT_USER_ID = "fintrack-local-user"

# Maximum history Plaid allows. It can only be set when an Item is created.
TRANSACTION_HISTORY_DAYS = 730

# Which products to request for each kind of connection. `products` filters
# which institutions Link offers (an institution must support all of them);
# `optional` products are added when the institution supports them.
LINK_KINDS = {
    "banking": {
        "label": "Bank, credit card, or loan",
        "products": ["transactions"],
        "optional": ["liabilities", "investments"],
    },
    "investments": {
        "label": "Brokerage or retirement",
        "products": ["investments"],
        "optional": ["transactions", "liabilities"],
    },
}

# Error codes meaning "you need to log in again". Fixed via Link update mode.
LOGIN_REQUIRED_CODES = {"ITEM_LOGIN_REQUIRED", "PENDING_EXPIRATION", "PENDING_DISCONNECT"}


@dataclass
class PlaidError(Exception):
    code: str
    message: str
    type: str = ""

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    @property
    def needs_login(self) -> bool:
        return self.code in LOGIN_REQUIRED_CODES


def _translate(exc: plaid.ApiException) -> PlaidError:
    """Turn the SDK's exception into a PlaidError carrying Plaid's error_code."""
    try:
        body = json.loads(exc.body or "{}")
    except (TypeError, ValueError):
        body = {}
    return PlaidError(
        code=body.get("error_code", f"HTTP_{exc.status}"),
        message=body.get("display_message") or body.get("error_message") or str(exc.reason),
        type=body.get("error_type", ""),
    )


class PlaidGateway:
    def __init__(self, settings: Settings):
        host = plaid.Environment.Production if settings.is_production else plaid.Environment.Sandbox
        configuration = plaid.Configuration(
            host=host,
            api_key={"clientId": settings.plaid_client_id, "secret": settings.plaid_secret},
        )
        self._api = plaid_api.PlaidApi(plaid.ApiClient(configuration))
        self._settings = settings

    def _call(self, method, request) -> dict:
        """One Plaid call. Network failures become a PlaidError too, so a sync records them as that Item's error and
        carries on: a raw urllib3 error used to escape sync_all and skip rules, transfers, recurring and the snapshot
        (user's report 2026-11-03). A connection dropped before any response (usually a stale pooled connection) is
        retried once."""
        for attempt in (1, 2):
            try:
                return method(request).to_dict()
            except plaid.ApiException as exc:
                raise _translate(exc) from None
            except urllib3.exceptions.ProtocolError as exc:
                if attempt == 1:
                    continue
                raise PlaidError("NETWORK_ERROR", f"connection to Plaid dropped ({exc.__class__.__name__})") from None
            except (urllib3.exceptions.HTTPError, OSError) as exc:
                raise PlaidError("NETWORK_ERROR", f"couldn't reach Plaid ({exc.__class__.__name__})") from None

    # --- Link ---

    def create_link_token(self, kind: str | None = None, access_token: str | None = None,
                          account_selection: bool = False) -> str:
        """New-Item token for `kind`, or an update-mode token when `access_token` is given.

        `account_selection` (update mode only): let you add or remove which accounts
        the existing Item shares, instead of creating a new Item. OAuth banks with
        their own account picker always allow this in update mode; other
        institutions need the flag.
        """
        args = dict(
            user=LinkTokenCreateRequestUser(client_user_id=CLIENT_USER_ID),
            client_name="fintrack",
            country_codes=[CountryCode("US")],
            language="en",
            redirect_uri=self._settings.redirect_uri,
        )
        if access_token:
            # Update mode: repairs an existing Item's login. Products must be omitted.
            args["access_token"] = access_token
            if account_selection:
                args["update"] = LinkTokenCreateRequestUpdate(account_selection_enabled=True)
        else:
            spec = LINK_KINDS[kind]
            args["products"] = [Products(p) for p in spec["products"]]
            args["optional_products"] = [Products(p) for p in spec["optional"]]
            args["transactions"] = LinkTokenTransactions(days_requested=TRANSACTION_HISTORY_DAYS)
        return self._call(self._api.link_token_create, LinkTokenCreateRequest(**args))["link_token"]

    def exchange_public_token(self, public_token: str) -> dict:
        """Returns {'access_token', 'item_id'}."""
        resp = self._call(
            self._api.item_public_token_exchange,
            ItemPublicTokenExchangeRequest(public_token=public_token),
        )
        return {"access_token": resp["access_token"], "item_id": resp["item_id"]}

    # --- Items and accounts (all free endpoints) ---

    def get_item(self, access_token: str) -> dict:
        return self._call(self._api.item_get, ItemGetRequest(access_token=access_token))["item"]

    def get_institution_name(self, institution_id: str) -> str:
        resp = self._call(
            self._api.institutions_get_by_id,
            InstitutionsGetByIdRequest(institution_id=institution_id, country_codes=[CountryCode("US")]),
        )
        return resp["institution"]["name"]

    def get_institution_logo(self, institution_id: str) -> str | None:
        """The institution's logo, a base64 152x152 PNG, or None when Plaid has none ("Not all institutions' logos are
        available"). /institutions/get_by_id is free and involves no Item (checked 2026-10-13,
        plaid.com/docs/account/billing: "free-to-use endpoints include Institutions endpoints"). The SDK's
        `include_optional_metadata` is the older name of the docs' `include_logo` (logo, brand color, URL)."""
        resp = self._call(
            self._api.institutions_get_by_id,
            InstitutionsGetByIdRequest(institution_id=institution_id, country_codes=[CountryCode("US")],
                                       options=InstitutionsGetByIdRequestOptions(include_optional_metadata=True)),
        )
        return resp["institution"].get("logo")

    def search_institutions(self, query: str) -> list[dict]:
        """/institutions/search by name (free, no Item; "up to a maximum of ten institutions per query"; checked
        2026-10-13): [{institution_id, name, logo (base64 PNG or None)}], for a manual account's institution logo."""
        resp = self._call(
            self._api.institutions_search,
            InstitutionsSearchRequest(query=query, country_codes=[CountryCode("US")],
                                      options=InstitutionsSearchRequestOptions(include_optional_metadata=True)),
        )
        return [{"institution_id": i["institution_id"], "name": i["name"], "logo": i.get("logo")}
                for i in resp.get("institutions", [])]

    def get_accounts(self, access_token: str) -> list[dict]:
        """/accounts/get: free; balances are Plaid's cached values, not a live check."""
        return self._call(self._api.accounts_get, AccountsGetRequest(access_token=access_token))["accounts"]

    def sync_transactions(self, access_token: str, cursor: str | None, count: int = 500) -> dict:
        """One page of /transactions/sync. Paging and restarts live in sync.py.

        Only call this for Items that already have the Transactions product:
        on any other Item, Plaid would add Transactions to it.

        Returns the raw page: added, modified, removed, next_cursor, has_more,
        transactions_update_status (dates are datetime.date objects).
        """
        return self._call(
            self._api.transactions_sync,
            # The raw bank text too (destination rules match it; user's request 2026-10-12).
            TransactionsSyncRequest(access_token=access_token, cursor=cursor or "", count=count,
                                    options=TransactionsSyncRequestOptions(include_original_description=True)),
        )

    def get_transactions(self, access_token: str, start: date, end: date, logos: bool = True,
                         original_description: bool = False) -> list[dict]:
        """Every transaction between two dates via /transactions/get (read-only; used once to fill in
        merchant logos, and the raw bank text, for history synced before fintrack kept them). Paged by
        offset, 500 at a time. Same caution as sync: only for Items that already have Transactions."""
        found: list[dict] = []
        while True:
            options = TransactionsGetRequestOptions(count=500, offset=len(found),
                                                    include_logo_and_counterparty_beta=logos,
                                                    include_original_description=original_description)
            page = self._call(self._api.transactions_get, TransactionsGetRequest(
                access_token=access_token, start_date=start, end_date=end, options=options))
            found.extend(page["transactions"])
            if not page["transactions"] or len(found) >= page["total_transactions"]:
                return found

    def get_holdings(self, access_token: str) -> dict:
        """/investments/holdings/get: {'holdings': [...], 'securities': [...]}.

        Only call for Items that already have the Investments product (same
        caution as transactions: don't risk adding a product by calling it).
        """
        resp = self._call(self._api.investments_holdings_get, InvestmentsHoldingsGetRequest(access_token=access_token))
        return {"holdings": resp["holdings"], "securities": resp["securities"]}

    def get_investment_transactions(self, access_token: str, start: date, end: date) -> list[dict]:
        """/investments/transactions/get: every investment transaction between two dates (inclusive), all pages.

        BILLING: the first call on an Item adds Plaid's "Investments Transactions" subscription (and Holdings), see
        Settings.sync_investment_transactions, which decides whether syncs may call this. Up to 24 months back."""
        found: list[dict] = []
        while True:
            resp = self._call(self._api.investments_transactions_get, InvestmentsTransactionsGetRequest(
                access_token=access_token, start_date=start, end_date=end,
                options=InvestmentsTransactionsGetRequestOptions(count=500, offset=len(found))))
            page = resp["investment_transactions"] or []
            found += page
            if not page or len(found) >= resp["total_investment_transactions"]:
                return found

    def get_liabilities(self, access_token: str) -> dict:
        """/liabilities/get: {'credit': [...], 'mortgage': [...], 'student': [...]}.

        Covers credit cards, mortgages, and student loans only (not auto loans or
        HELOCs). Only call for Items with the Liabilities product.
        """
        resp = self._call(self._api.liabilities_get, LiabilitiesGetRequest(access_token=access_token))
        return {k: v or [] for k, v in (resp["liabilities"] or {}).items()}

    def remove_item(self, access_token: str) -> None:
        """/item/remove: revokes the token. On the Trial plan the slot is NOT refunded."""
        self._call(self._api.item_remove, ItemRemoveRequest(access_token=access_token))

    # --- Sandbox-only helpers ---

    def sandbox_reset_login(self, access_token: str) -> None:
        """Forces a Sandbox Item into ITEM_LOGIN_REQUIRED to test update mode."""
        if self._settings.is_production:
            raise RuntimeError("sandbox_reset_login is Sandbox-only")
        self._call(self._api.sandbox_item_reset_login, SandboxItemResetLoginRequest(access_token=access_token))
