"""A fake PlaidGateway: records calls and returns obviously fake Sandbox-shaped data."""

from __future__ import annotations

from fintrack.plaid_client import PlaidError

FAKE_ACCOUNTS = [
    {
        "account_id": "acc-checking",
        "name": "Fake Checking",
        "official_name": None,
        "mask": "0000",
        "type": "depository",
        "subtype": "checking",
        "balances": {"current": 110.0, "available": 100.0, "iso_currency_code": "USD"},
    },
    {
        "account_id": "acc-card",
        "name": "Fake Credit Card",
        "official_name": None,
        "mask": "123456783333",  # longer than 4: must be truncated
        "type": "credit",
        "subtype": "credit card",
        "balances": {"current": 410.005, "available": None, "iso_currency_code": "USD"},
    },
]


def txn(transaction_id, amount, *, account_id="acc-checking", day="2026-09-15", name="Fake Coffee",
        merchant="Fake Coffee Co", primary="FOOD_AND_DRINK", pending=False, pending_transaction_id=None,
        location=None, account_owner=None):
    """A /transactions/sync transaction with only the fields fintrack reads."""
    from datetime import date

    return {
        "transaction_id": transaction_id,
        "account_id": account_id,
        "amount": amount,
        "date": date.fromisoformat(day),  # the SDK returns date objects
        "authorized_date": None,
        "iso_currency_code": "USD",
        "name": name,
        "merchant_name": merchant,
        "pending": pending,
        "pending_transaction_id": pending_transaction_id,
        "personal_finance_category": {"primary": primary, "detailed": f"{primary}_OTHER", "confidence_level": "HIGH"},
        # Plaid's location object (migration 0049): every field null for an online purchase
        "location": location or {k: None for k in ("address", "city", "region", "postal_code", "country", "lat", "lon",
                                                   "store_number")},
        "account_owner": account_owner,      # a sub-account's card holder (migration 0051); usually null
    }


def page(added=(), modified=(), removed=(), next_cursor="c1", has_more=False, status="HISTORICAL_UPDATE_COMPLETE"):
    return {
        "added": list(added),
        "modified": list(modified),
        "removed": [{"transaction_id": r, "account_id": "acc-checking"} for r in removed],
        "next_cursor": next_cursor,
        "has_more": has_more,
        "transactions_update_status": status,
    }


class FakeGateway:
    def __init__(self, settings=None):
        self.calls: list[tuple] = []
        self.counter = 0
        self.accounts_error: PlaidError | None = None
        self.remove_error: PlaidError | None = None
        self.institution_id = "ins_fake_1"
        self.products = ["transactions"]
        # Queue of sync responses: each entry is a page dict or a PlaidError to raise.
        self.sync_queue: list = []

    def create_link_token(self, kind=None, access_token=None, account_selection=False):
        self.calls.append(("create_link_token", kind, access_token) + (("account_selection",) if account_selection else ()))
        return f"link-sandbox-fake-{len(self.calls)}"

    def exchange_public_token(self, public_token):
        self.calls.append(("exchange_public_token", public_token))
        self.counter += 1
        return {
            "access_token": f"access-sandbox-fake-{self.counter:04d}",
            "item_id": f"item-fake-{self.counter}",
        }

    def get_item(self, access_token):
        self.calls.append(("get_item",))
        return {"institution_id": self.institution_id, "products": self.products, "billed_products": self.products}

    def get_liabilities(self, access_token):
        self.calls.append(("get_liabilities",))
        return getattr(self, "liabilities_payload", {"credit": [], "mortgage": [], "student": []})

    def sync_transactions(self, access_token, cursor, count=500):
        self.calls.append(("sync_transactions", cursor))
        response = self.sync_queue.pop(0) if self.sync_queue else page(next_cursor=cursor or "c0")
        if isinstance(response, Exception):
            raise response
        return response

    def get_investment_transactions(self, access_token, start, end):
        self.calls.append(("get_investment_transactions", start, end))
        if isinstance(getattr(self, "investment_transactions_error", None), Exception):
            raise self.investment_transactions_error
        return getattr(self, "investment_transactions_payload", [])

    def get_institution_name(self, institution_id):
        return "First Platypus Bank"

    # A 1x1 PNG, base64 (fake): what /institutions/get_by_id returns as `logo`; set institution_logo = None for "no logo"
    institution_logo = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5E"
                        "rkJggg==")

    def get_institution_logo(self, institution_id):
        self.calls.append(("get_institution_logo", institution_id))
        return self.institution_logo

    # /institutions/search: made-up results; set institution_search_results to change them
    institution_search_results = None

    def search_institutions(self, query):
        self.calls.append(("search_institutions", query))
        if self.institution_search_results is not None:
            return self.institution_search_results
        return [{"institution_id": "ins_fake_9", "name": "Fake Fund Co", "logo": self.institution_logo},
                {"institution_id": "ins_fake_8", "name": query.title(), "logo": self.institution_logo}]

    def get_accounts(self, access_token):
        self.calls.append(("get_accounts", access_token))
        if self.accounts_error:
            raise self.accounts_error
        return FAKE_ACCOUNTS

    def remove_item(self, access_token):
        self.calls.append(("remove_item", access_token))
        if self.remove_error:
            raise self.remove_error

    def sandbox_reset_login(self, access_token):
        self.calls.append(("sandbox_reset_login",))
        self.accounts_error = PlaidError("ITEM_LOGIN_REQUIRED", "the login details of this item have changed")


def alpaca_fetch(prices: dict, previous: dict | None = None, bars: dict | None = None,
                 when: str = "2026-10-07T20:00:00Z", calls: list | None = None, minutes: dict | None = None):
    """A fake Alpaca for QuoteGateway(fetch=...): snapshots for both feeds from `prices`
    ({ticker: price}, read at call time so tests can change it), previous closes from
    `previous` (default: the same price), daily bars from `bars` ({ticker: [(date, close)]}).
    Tickers not in `prices` are missing from answers, like a mutual fund at Alpaca. Every
    (url, headers) is appended to `calls`."""
    from urllib.parse import parse_qs, urlparse

    def fetch(url, headers):
        if calls is not None:
            calls.append((url, headers))
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if url.split("?")[0].endswith("/v2/assets"):
            return []                                  # the watchlist's stock list (2026-11-04): none, unless a test gives one
        symbols = query["symbols"].split(",")
        if url.split("?")[0].endswith("/bars") and query["timeframe"] == "1Min":
            return {"bars": {s: [{"t": t, "c": c} for t, c in (minutes or {}).get(s, [])
                                 if query["start"] <= t < query["end"]] for s in symbols}, "next_page_token": None}
        if url.split("?")[0].endswith("/bars"):
            return {"bars": {s: [{"t": f"{d}T04:00:00Z", "c": c} for d, c in (bars or {}).get(s, [])]
                             for s in symbols if (bars or {}).get(s)}, "next_page_token": None}
        answer = {}
        for s in symbols:
            if s not in prices:
                continue
            price, prev = prices[s], (previous or {}).get(s, prices[s])
            answer[s] = {"latestTrade": {"p": price, "t": when},
                         "dailyBar": {"c": price, "t": when[:10] + "T04:00:00Z"},
                         "minuteBar": {"t": when[:17] + "00Z"},
                         "prevDailyBar": {"c": prev}}
        return answer
    return fetch
