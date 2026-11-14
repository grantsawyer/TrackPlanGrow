"""Demo data for the Sandbox (user's request 2026-10-09: "add dummy data to the sandbox version with a
similar manual and Plaid account mix but a net worth around 500k").

Plaid's own Sandbox can't do this well: a "custom user" can't set transaction categories, and is capped
at about 250 transactions and ten accounts per login. So the demo data is generated here, with every
name made up, and served to the app in place of Plaid:

* `build_dataset` generates ~21 months of believable activity (paychecks, rent, bills, card spending,
  card payments, investment contributions) plus balances and holdings, all from a fixed random seed.
  It is written as JSON to FINTRACK_HOME/demo-sandbox.json (never into the repo).
* `DemoAwareGateway` answers for Items whose access tokens start with "demo-access-" from that file and
  passes everything else to the real PlaidGateway. `make_gateway` uses it in the Sandbox only; in
  Production it returns the plain PlaidGateway, so nothing here can touch real data or real banks.
* `seed` creates the Items and runs the app's OWN sync over the generated data, so categories,
  transfer matching, recurring detection, holdings, card statements and snapshots all come from the
  real code. It then adds daily net-worth history, manual investment accounts with positions, confirms
  the recurring items and sets budgets.

Money is integer cents until the Plaid-shaped payloads, which carry dollars like Plaid does.
"""

from __future__ import annotations

import calendar
import copy
import json
import logging
import os
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from fintrack.config import Settings
from fintrack.plaid_client import PlaidError, PlaidGateway

log = logging.getLogger(__name__)

DATA_FILE = "demo-sandbox.json"
ACCESS_PREFIX = "demo-access-"            # demo Items' (fake) access tokens: demo-access-<item key>
INSTITUTION_PREFIX = "demo_ins_"
TARGET_NET_WORTH_CENTS = 500_000_00
HISTORY_MONTHS = 21                       # activity starts this many months before the current one
RAMP_START = 0.62                         # investments were this fraction of today's value at the start of the history:
                                          # about what ~$2.3k/month of contributions plus market growth explains
SEED = 20261009

# Prices used to size holdings when Alpaca can't be asked (no keys, offline). Approximate on purpose.
FALLBACK_PRICES = {"VTI": 330.0, "VXUS": 72.0, "BND": 74.0, "VOO": 610.0, "QQQ": 560.0, "VTV": 190.0,
                   "AAPL": 235.0, "MSFT": 520.0, "FXAIX": 225.0}


class DemoError(Exception):
    """The demo data can't be created here; the message says why."""


# ---------------------------------------------------------------- the made-up world

# (key, Plaid account id suffix, name, official name, type, subtype, mask)
CHECKING = ("CHK", "Demo Checking", "DEMO CREDIT UNION CHECKING", "depository", "checking", "0042")
CARDS = [   # key, name, mask, APR %, limit $, statement day
    ("CARD_A", "Everyday Rewards Visa", "1107", 24.99, 15_000, 18),
    ("CARD_B", "Travel Mastercard", "2284", 21.49, 20_000, 25),
    ("CARD_C", "Cash Back Card", "3391", 27.24, 10_000, 4),
    ("CARD_D", "Home Store Card", "4456", 29.99, 5_000, 11),
]
CARD_INSTITUTIONS = {"CARD_A": "cards_1", "CARD_B": "cards_1", "CARD_C": "cards_2", "CARD_D": "cards_2"}
DUE_DAYS = 21                              # payment due this many days after the statement

# Plaid investment accounts: key, name, subtype, mask, target $, {ticker: weight} ("cash" is the cash line)
PLAID_INVESTMENTS = [
    ("BRK", "Demo Brokerage", "brokerage", "5521", 118_000,
     {"VTI": .38, "VXUS": .14, "VOO": .12, "AAPL": .08, "MSFT": .07, "QQQ": .08, "BND": .08, "cash": .05}),
    ("IRA", "Demo Traditional IRA", "ira", "6630", 84_000,
     {"VTI": .45, "VTV": .15, "BND": .22, "FXAIX": .14, "cash": .04}),
    ("ROTH", "Demo Roth IRA", "roth", "7748", 52_000,
     {"QQQ": .40, "VTI": .35, "VXUS": .15, "MSFT": .06, "cash": .04}),
]
# Manual investment accounts (positions fed by prices): name, [(ticker or None, line name, kind, target $)]
MANUAL_ACCOUNTS = [
    ("Demo Workplace 401(k)", [("VTI", "Total US Stock Market ETF", "etf", 73_840), ("VXUS", "Total International ETF", "etf", 28_400),
                               ("BND", "Total Bond Market ETF", "etf", 25_560), (None, "Stable Value Fund", "other", 14_200)]),
    ("Demo Taxable Brokerage", [("VOO", "S&P 500 ETF", "etf", 34_800), ("MSFT", "Software Company Stock", "stock", 11_600),
                                (None, "Cash", "cash", 11_600)]),
    ("Demo HSA", [("VTI", "Total US Stock Market ETF", "etf", 31_500), (None, "HSA Cash", "cash", 4_500)]),
]

SECURITY_NAMES = {   # generic names on purpose (these are real tickers, but no brands)
    "VTI": ("Total US Stock Market ETF", "etf"), "VXUS": ("Total International ETF", "etf"),
    "BND": ("Total Bond Market ETF", "etf"), "VOO": ("S&P 500 ETF", "etf"), "QQQ": ("Large Growth ETF", "etf"),
    "VTV": ("Large Value ETF", "etf"), "AAPL": ("Consumer Devices Company", "equity"),
    "MSFT": ("Software Company", "equity"), "FXAIX": ("Large Cap Index Fund", "mutual fund"),
}

FOOD, MERCH, HOME, ENT, CARE, MED, TRANS, TRAVEL, UTIL, SERV = (
    "FOOD_AND_DRINK", "GENERAL_MERCHANDISE", "HOME_IMPROVEMENT", "ENTERTAINMENT", "PERSONAL_CARE", "MEDICAL",
    "TRANSPORTATION", "TRAVEL", "RENT_AND_UTILITIES", "GENERAL_SERVICES")

# Everyday spending: card, times a month, category, detailed category, [(bank text, merchant)], low $, high $
PATTERNS = [
    ("CARD_A", 9.0, FOOD, "FOOD_AND_DRINK_GROCERIES", [("RIVERSIDE MARKET #0412", "Riverside Market"), ("GREENLEAF GROCERS", "Greenleaf Grocers"),
                                                       ("CORNER PANTRY", "Corner Pantry"), ("HARVEST FOODS #218", "Harvest Foods")], 28, 135),
    ("CARD_A", 5.0, FOOD, "FOOD_AND_DRINK_RESTAURANT", [("BLUE HERON BISTRO", "Blue Heron Bistro"), ("MAPLE STREET DINER", "Maple Street Diner"),
                                                        ("NOODLE GARDEN", "Noodle Garden")], 18, 85),
    ("CARD_B", 5.0, FOOD, "FOOD_AND_DRINK_RESTAURANT", [("TAVERNA OLIVE", "Taverna Olive"), ("HARBOR GRILL", "Harbor Grill"),
                                                        ("BLUE HERON BISTRO", "Blue Heron Bistro")], 25, 120),
    ("CARD_A", 9.0, FOOD, "FOOD_AND_DRINK_COFFEE", [("BEAN THERE COFFEE", "Bean There Coffee"), ("SUNRISE CAFE", "Sunrise Cafe")], 3.5, 9),
    ("CARD_A", 3.0, FOOD, "FOOD_AND_DRINK_FAST_FOOD", [("BURGER BARN #77", "Burger Barn"), ("TACO LANTERN", "Taco Lantern")], 8, 19),
    ("CARD_A", 4.0, TRANS, "TRANSPORTATION_GAS", [("NORTHWIND FUEL 0931", "Northwind Fuel"), ("QUICKFILL GAS", "QuickFill Gas")], 34, 62),
    ("CARD_B", 3.0, TRANS, "TRANSPORTATION_TAXIS_AND_RIDE_SHARES", [("HOPPER RIDES", "Hopper Rides")], 11, 34),
    ("CARD_C", 8.0, MERCH, "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES", [("EVERYTHING ONLINE MARKETPLACE", "Everything Online Marketplace")], 12, 140),
    ("CARD_C", 2.0, MERCH, "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES", [("THREAD & NEEDLE APPAREL", "Thread & Needle Apparel")], 30, 160),
    ("CARD_C", 0.5, MERCH, "GENERAL_MERCHANDISE_ELECTRONICS", [("GADGET HUB ELECTRONICS", "Gadget Hub Electronics")], 25, 300),
    ("CARD_C", 1.0, MERCH, "GENERAL_MERCHANDISE_PET_SUPPLIES", [("PAWSOME PET SUPPLY", "Pawsome Pet Supply")], 25, 70),
    ("CARD_D", 2.0, HOME, "HOME_IMPROVEMENT_HARDWARE", [("HAMMER & NAIL HARDWARE", "Hammer & Nail Hardware")], 14, 110),
    ("CARD_D", 0.5, HOME, "HOME_IMPROVEMENT_FURNITURE", [("CASA FURNISHINGS", "Casa Furnishings")], 80, 420),
    ("CARD_B", 2.0, ENT, "ENTERTAINMENT_TV_AND_MOVIES", [("STARLIGHT CINEMAS", "Starlight Cinemas")], 12, 40),
    ("CARD_B", 0.5, ENT, "ENTERTAINMENT_SPORTING_EVENTS_AMUSEMENT_PARKS_AND_MUSEUMS", [("RIVERFRONT AMPHITHEATER", "Riverfront Amphitheater")], 40, 140),
    ("CARD_A", 1.0, MED, "MEDICAL_PHARMACIES_AND_SUPPLEMENTS", [("CORNER PHARMACY", "Corner Pharmacy")], 8, 45),
    ("CARD_A", 0.25, MED, "MEDICAL_PRIMARY_CARE", [("LAKESIDE FAMILY MEDICINE", "Lakeside Family Medicine")], 20, 180),
    ("CARD_C", 1.0, CARE, "PERSONAL_CARE_HAIR_AND_BEAUTY", [("GLOW SALON", "Glow Salon")], 40, 95),
]
# Subscriptions: card, day of month, bank text, merchant, category, detailed, [(from month, cents)]
SUBSCRIPTIONS = [
    ("CARD_C", 5, "STREAMLY", "Streamly", ENT, "ENTERTAINMENT_TV_AND_MOVIES", [("2000-01", 1549), ("2026-03", 1799)]),
    ("CARD_C", 9, "TUNEBOX MUSIC", "Tunebox", ENT, "ENTERTAINMENT_MUSIC_AND_AUDIO", [("2000-01", 1099)]),
    ("CARD_C", 14, "CLOUDVAULT STORAGE", "CloudVault", SERV, "GENERAL_SERVICES_OTHER_GENERAL_SERVICES", [("2000-01", 299)]),
    ("CARD_C", 20, "DAILY LEDGER NEWS", "Daily Ledger News", ENT, "ENTERTAINMENT_OTHER_ENTERTAINMENT", [("2000-01", 999)]),
    ("CARD_C", 23, "PIXELFORGE SOFTWARE", "Pixelforge", SERV, "GENERAL_SERVICES_OTHER_GENERAL_SERVICES", [("2000-01", 499)]),
    ("CARD_A", 3, "IRON WORKS GYM", "Iron Works Gym", CARE, "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS", [("2000-01", 4999)]),
]
# One-off bills that make a month overspend: (month, day, card, bank text, merchant, category, detailed, cents).
# August 2026: the air conditioner failed and the car needed work, so that month spends more than it earns.
ONE_OFFS = [
    ("2026-08", 11, "CARD_A", "COMFORTAIR HEATING & COOLING", "ComfortAir Heating & Cooling", HOME,
     "HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE", 264_000),
    ("2026-08", 19, "CARD_A", "MERIDIAN AUTO SERVICE", "Meridian Auto Service", SERV, "GENERAL_SERVICES_AUTOMOTIVE", 78_500),
]
# Withdrawals from the investment accounts into checking: (month, day, cents, bank text). August 2026's pays for the
# air-conditioner bill above; December 2025's is for the holidays.
WITHDRAWALS = [("2025-12", 12, 120_000, "TRANSFER FROM DEMO BROKERAGE"), ("2026-08", 12, 300_000, "TRANSFER FROM DEMO BROKERAGE")]
TRIP_MONTHS = ("2025-03", "2025-06", "2025-09", "2025-12", "2026-05", "2026-08")

# Budgets: category -> [(from month or None = first month of history, $ a month)]
BUDGETS = {"INCOME": [(None, 8300), ("2026-01", 8550)], "RENT_AND_UTILITIES": [(None, 2850), ("2026-01", 2950)],
           "GENERAL_SERVICES_INSURANCE": [(None, 160)], "FOOD_AND_DRINK": [(None, 1300)], "TRANSPORTATION": [(None, 320)],
           "GENERAL_MERCHANDISE": [(None, 650)], "HOME_IMPROVEMENT": [(None, 250)], "ENTERTAINMENT": [(None, 220)],
           "PERSONAL_CARE": [(None, 140)], "MEDICAL": [(None, 150)], "TRAVEL": [(None, 600)]}


# ---------------------------------------------------------------- small helpers

def _first_of_month(day: date, months: int = 0) -> date:
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _month_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _dollars(cents: int) -> float:
    return round(cents / 100, 2)


def _between(rng: random.Random, low: float, high: float) -> int:
    """A price in cents between two dollar amounts, skewed toward the low end like real purchases."""
    return int(round((low + (high - low) * rng.random() ** 1.7) * 100))


# ---------------------------------------------------------------- the generated dataset

@dataclass
class Dataset:
    anchor: date
    start: date                                    # first day of activity
    server: dict                                   # what the demo gateway serves (JSON-able)
    histories: dict[str, list[tuple[str, int]]]    # Plaid account id -> [(day, balance cents)] up to the day before anchor
    manual: list[dict]                             # manual investment accounts and their positions
    expected_net_worth_cents: int = 0


class _Ledger:
    """Transactions per account key, in cents with Plaid's sign (positive = money out)."""

    def __init__(self, anchor: date):
        self.anchor = anchor
        self.rows: dict[str, list[dict]] = {}

    def add(self, account: str, day: date, cents: int, name: str, merchant: str | None, primary: str, detailed: str) -> None:
        if day > self.anchor:
            return
        self.rows.setdefault(account, []).append(
            {"day": day, "cents": cents, "name": name, "merchant": merchant, "primary": primary, "detailed": detailed})

    def total(self, account: str) -> int:
        return sum(r["cents"] for r in self.rows.get(account, []))


def _plaid_id(key: str) -> str:
    return f"demo-acc-{key.lower().replace('_', '-')}"


def _generate_activity(rng: random.Random, anchor: date, start: date) -> tuple[_Ledger, dict]:
    """All checking and card activity from `start` to `anchor`. Returns the ledger and the cards' statements."""
    ledger = _Ledger(anchor)
    months = []
    month = start
    while month <= anchor:
        months.append(month)
        month = _first_of_month(month, 1)

    for first in months:
        y, m = first.year, first.month
        tag = f"{y}-{m:02d}"
        raise_applies = tag >= "2026-01"
        # Checking: pay, housing, bills.
        for day in (15, 31):
            ledger.add("CHK", _month_day(y, m, day), -(427_500 if raise_applies else 415_000), "DEMO CORP PAYROLL", "Demo Corp",
                       "INCOME", "INCOME_WAGES")
        ledger.add("CHK", _month_day(y, m, 1), 245_000 if raise_applies else 235_000, "RIVERSIDE PROPERTY MGMT RENT",
                   "Riverside Property Management", UTIL, "RENT_AND_UTILITIES_RENT")
        summer = m in (6, 7, 8)
        ledger.add("CHK", _month_day(y, m, 8), _between(rng, 105 if summer else 85, 175 if summer else 130),
                   "CITYWIDE ELECTRIC", "Citywide Electric", UTIL, "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY")
        ledger.add("CHK", _month_day(y, m, 12), _between(rng, 38, 52), "AQUA MUNICIPAL WATER", "Aqua Municipal Water",
                   UTIL, "RENT_AND_UTILITIES_WATER")
        ledger.add("CHK", _month_day(y, m, 20), 6_500, "FIBERNET INTERNET", "FiberNet Internet", UTIL, "RENT_AND_UTILITIES_INTERNET_AND_CABLE")
        ledger.add("CHK", _month_day(y, m, 22), 8_500, "MOBILINK WIRELESS", "Mobilink Wireless", UTIL, "RENT_AND_UTILITIES_TELEPHONE")
        ledger.add("CHK", _month_day(y, m, 3), 2_200, "HEARTH RENTERS INSURANCE", "Hearth Renters Insurance", SERV, "GENERAL_SERVICES_INSURANCE")
        ledger.add("CHK", _month_day(y, m, 17), 13_800, "SHIELD AUTO INSURANCE", "Shield Auto Insurance", SERV, "GENERAL_SERVICES_INSURANCE")
        # Contributions and withdrawals: Plaid's "investment and retirement funds" transfer category (contributions.py).
        ledger.add("CHK", _month_day(y, m, 16), 185_000, "TRANSFER TO DEMO BROKERAGE", None, "TRANSFER_OUT",
                   "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS")
        if m == 4:                                               # the yearly Roth IRA contribution
            ledger.add("CHK", _month_day(y, m, 9), 700_000, "TRANSFER TO DEMO ROTH IRA", None, "TRANSFER_OUT",
                       "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS")
        for withdrawal_month, day, cents, text in WITHDRAWALS:
            if withdrawal_month == tag:
                ledger.add("CHK", _month_day(y, m, day), -cents, text, None, "TRANSFER_IN",
                           "TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS")
        ledger.add("CHK", _month_day(y, m, rng.randint(2, 27)), rng.choice([6_000, 8_000, 10_000]),
                   "ATM WITHDRAWAL DEMO CU", None, "TRANSFER_OUT", "TRANSFER_OUT_WITHDRAWAL")

        # Everyday card spending.
        for card, per_month, primary, detailed, merchants, low, high in PATTERNS:
            count = int(per_month) + (1 if rng.random() < per_month - int(per_month) else 0)
            if tag in ("2025-11", "2025-12") and primary == MERCH:
                count += 3                                           # holiday shopping
            for _ in range(count):
                text, merchant = rng.choice(merchants)
                ledger.add(card, _month_day(y, m, rng.randint(1, 28)), _between(rng, low, high), text, merchant, primary, detailed)
        for card, day, text, merchant, primary, detailed, prices in SUBSCRIPTIONS:
            cents = [c for from_month, c in prices if from_month <= tag][-1]
            ledger.add(card, _month_day(y, m, day), cents, text, merchant, primary, detailed)
        for one_off_month, day, card, text, merchant, primary, detailed, cents in ONE_OFFS:
            if one_off_month == tag:
                ledger.add(card, _month_day(y, m, day), cents, text, merchant, primary, detailed)
        if tag in TRIP_MONTHS:
            ledger.add("CARD_B", _month_day(y, m, rng.randint(2, 8)), _between(rng, 290, 540), "SKYLINE AIRWAYS", "Skyline Airways", TRAVEL, "TRAVEL_FLIGHTS")
            ledger.add("CARD_B", _month_day(y, m, rng.randint(9, 14)), _between(rng, 480, 1100), "SEASIDE RESORT", "Seaside Resort", TRAVEL, "TRAVEL_LODGING")
            ledger.add("CARD_B", _month_day(y, m, rng.randint(9, 14)), _between(rng, 160, 380), "ROADRUNNER CAR RENTAL", "RoadRunner Car Rental", TRAVEL, "TRAVEL_RENTAL_CARS")
            ledger.add("CARD_B", _month_day(y, m, rng.randint(15, 20)), _between(rng, 290, 540), "SKYLINE AIRWAYS", "Skyline Airways", TRAVEL, "TRAVEL_FLIGHTS")

    # Card statements, and the payments from checking that clear them.
    statements: dict[str, list[dict]] = {}
    for key, _name, _mask, _apr, _limit, statement_day in CARDS:
        previous = start - timedelta(days=1)
        statements[key] = []
        for first in months + [_first_of_month(months[-1], 1)]:
            issued = _month_day(first.year, first.month, statement_day)
            if issued > anchor:
                statements[key].append({"issued": issued, "balance": None, "due": issued + timedelta(days=DUE_DAYS)})
                break
            balance = sum(r["cents"] for r in ledger.rows.get(key, []) if r["cents"] > 0 and previous < r["day"] <= issued)
            due = issued + timedelta(days=DUE_DAYS)
            statements[key].append({"issued": issued, "balance": balance, "due": due})
            previous = issued
            if balance > 0 and due <= anchor:
                label = key.replace("CARD_", "")
                ledger.add("CHK", due, balance, f"ONLINE PAYMENT TO DEMO CARD {label}", None, "LOAN_PAYMENTS",
                           "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
                ledger.add(key, due, -balance, "PAYMENT THANK YOU", None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    return ledger, statements


def _holdings_for(rng: random.Random, anchor: date, key: str, account_id: str, target_cents: int, weights: dict,
                  prices: dict) -> tuple[list[dict], list[dict], int]:
    """Holdings sized to `target_cents`; returns (holdings, securities, total value cents)."""
    holdings, securities, total = [], [], 0
    for ticker, weight in weights.items():
        target = int(target_cents * weight)
        if ticker == "cash":
            holdings.append({"account_id": account_id, "security_id": "demo-sec-cash", "quantity": _dollars(target),
                             "institution_price": 1.0, "institution_value": _dollars(target), "cost_basis": _dollars(target),
                             "institution_price_as_of": anchor.isoformat(), "iso_currency_code": "USD"})
            securities.append({"security_id": "demo-sec-cash", "name": "U.S. Dollar", "ticker_symbol": None, "type": "cash",
                               "is_cash_equivalent": True})
            total += target
            continue
        price = prices[ticker]
        quantity = round(target / 100 / price, 3)
        value = int(round(quantity * price * 100))
        basis = int(value * (0.62 + 0.28 * rng.random()))
        security_id = f"demo-sec-{ticker.lower()}"
        holdings.append({"account_id": account_id, "security_id": security_id, "quantity": quantity, "institution_price": price,
                         "institution_value": _dollars(value), "cost_basis": _dollars(basis),
                         "institution_price_as_of": anchor.isoformat(), "iso_currency_code": "USD"})
        name, kind = SECURITY_NAMES[ticker]
        securities.append({"security_id": security_id, "name": name, "ticker_symbol": ticker, "type": kind, "is_cash_equivalent": False})
        total += value
    return holdings, securities, total


def _investment_activity(ledger: "_Ledger", anchor: date, start: date, prices: dict) -> list[dict]:
    """Activity inside the investment accounts, Plaid-shaped (amount > 0 = cash debited, < 0 = cash credited):
    each bank transfer into an account shows up there as a contribution two days later (so the Transfers chart must
    count it once), withdrawals leave two days before they reach checking, the traditional IRA gets a payroll-style
    employer contribution every month that never touches the bank, plus buys and quarterly dividends."""
    rows: list[dict] = []

    def add(account: str, day: date, name: str, kind: str, subtype: str, cents: int, ticker: str | None = None) -> None:
        if day > anchor or day < start:
            return
        price = prices[ticker] if ticker else 1.0
        rows.append({"investment_transaction_id": "", "account_id": _plaid_id(account), "security_id": f"demo-sec-{ticker.lower()}" if ticker else "demo-sec-cash",
                     "date": day.isoformat(), "name": name, "quantity": round(cents / 100 / price, 3) if ticker else _dollars(cents),
                     "price": price, "fees": 0.0, "amount": _dollars(cents), "type": kind, "subtype": subtype, "iso_currency_code": "USD"})

    for r in ledger.rows.get("CHK", []):
        if r["detailed"] == "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS" and r["cents"] > 0:
            target = "ROTH" if "ROTH" in r["name"] else "BRK"
            arrives = r["day"] + timedelta(days=2)
            add(target, arrives, "ELECTRONIC DEPOSIT CONTRIBUTION", "cash", "contribution", -r["cents"])
            add(target, arrives + timedelta(days=1), "BUY TOTAL MARKET FUND", "buy", "buy", r["cents"], "VTI")
        elif r["detailed"] == "TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS" and r["cents"] < 0:
            add("BRK", r["day"] - timedelta(days=3), "SELL TOTAL MARKET FUND", "sell", "sell", r["cents"], "VTI")
            add("BRK", r["day"] - timedelta(days=2), "ELECTRONIC WITHDRAWAL", "cash", "withdrawal", -r["cents"])
    month = start
    while month <= anchor:
        add("IRA", _month_day(month.year, month.month, 15), "PAYROLL CONTRIBUTION EMPLOYER MATCH", "cash", "contribution", -40_000)
        if month.month in (3, 6, 9, 12):
            add("BRK", _month_day(month.year, month.month, 28), "DIVIDEND RECEIVED", "cash", "dividend", -41_000)
            add("IRA", _month_day(month.year, month.month, 28), "DIVIDEND RECEIVED", "cash", "dividend", -26_000)
        month = _first_of_month(month, 1)
    rows.sort(key=lambda t: (t["date"], t["account_id"], t["name"]))
    for i, row in enumerate(rows):
        row["investment_transaction_id"] = f"demo-inv-{i:05d}"
    return rows


def build_dataset(anchor: date, prices: dict[str, float] | None = None) -> Dataset:
    """Generate the whole demo world as of `anchor` (normally today). Deterministic for a given anchor and prices."""
    prices = {**FALLBACK_PRICES, **(prices or {})}
    rng = random.Random(SEED)
    start = _first_of_month(anchor, -HISTORY_MONTHS)
    ledger, statements = _generate_activity(rng, anchor, start)

    # Investments first: they fix the net worth, and checking's balance absorbs what's left of the target.
    items: dict[str, dict] = {}
    invest_total, invest_accounts, holdings_all, securities_all = 0, [], [], {}
    for key, name, subtype, mask, target, weights in PLAID_INVESTMENTS:
        account_id = _plaid_id(key)
        holdings, securities, total = _holdings_for(rng, anchor, key, account_id, target * 100, weights, prices)
        holdings_all += holdings
        securities_all.update({s["security_id"]: s for s in securities})
        invest_total += total
        invest_accounts.append({"key": key, "account_id": account_id, "name": name, "official_name": name.upper(), "mask": mask,
                                "type": "investment", "subtype": subtype, "current": total})
    manual_total = sum(t for _n, lines in MANUAL_ACCOUNTS for *_x, t in lines) * 100
    card_owed = {key: ledger.total(key) for key, *_r in CARDS}
    checking_current = TARGET_NET_WORTH_CENTS - invest_total - manual_total + sum(card_owed.values())
    checking_current = max(8_000_00, min(40_000_00, checking_current))

    def account(key, name, official, kind, subtype, mask, current, available):
        return {"account_id": _plaid_id(key), "name": name, "official_name": official, "mask": mask, "type": kind,
                "subtype": subtype, "balances": {"current": _dollars(current), "available": None if available is None else _dollars(available),
                                                 "iso_currency_code": "USD"}}

    def txns(item_key: str, account_keys: list[str]) -> list[dict]:
        rows = [(r["day"], key, r) for key in account_keys for r in ledger.rows.get(key, [])]
        rows.sort(key=lambda t: (t[0], t[1], t[2]["cents"]))
        out = []
        for i, (day, key, r) in enumerate(rows):
            out.append({"transaction_id": f"demo-txn-{item_key}-{i:05d}", "account_id": _plaid_id(key),
                        "amount": _dollars(r["cents"]), "date": day.isoformat(), "authorized_date": None, "iso_currency_code": "USD",
                        "name": r["name"], "merchant_name": r["merchant"],
                        # Card purchases from the last day or two haven't posted yet.
                        "pending": key.startswith("CARD_") and r["cents"] > 0 and (anchor - day).days <= 1,
                        "pending_transaction_id": None,
                        "personal_finance_category": {"primary": r["primary"], "detailed": r["detailed"], "confidence_level": "HIGH"}})
        return out

    items["credit_union"] = {
        "institution_id": INSTITUTION_PREFIX + "credit_union", "institution_name": "Demo Credit Union", "products": ["transactions"],
        "accounts": [account("CHK", CHECKING[1], CHECKING[2], CHECKING[3], CHECKING[4], CHECKING[5], checking_current, checking_current)],
        "transactions": txns("credit_union", ["CHK"]), "holdings": None, "liabilities": None}

    for item_key, institution in (("cards_1", "Demo Rewards Bank"), ("cards_2", "Demo Retail Bank")):
        keys = [c[0] for c in CARDS if CARD_INSTITUTIONS[c[0]] == item_key]
        credit, accounts = [], []
        for key, name, mask, apr, limit, _day in CARDS:
            if key not in keys:
                continue
            owed = card_owed[key]
            accounts.append(account(key, name, name.upper(), "credit", "credit card", mask, owed, limit * 100 - owed))
            done = [s for s in statements[key] if s["balance"] is not None]
            last = done[-1] if done else None
            payments = [r for r in ledger.rows.get(key, []) if r["cents"] < 0]
            upcoming = statements[key][-1]
            due = last["due"] if last and last["due"] >= anchor else upcoming["due"]
            credit.append({"account_id": _plaid_id(key), "aprs": [{"apr_type": "purchase_apr", "apr_percentage": apr}],
                           "is_overdue": False,
                           "last_payment_amount": _dollars(-payments[-1]["cents"]) if payments else None,
                           "last_payment_date": payments[-1]["day"].isoformat() if payments else None,
                           "last_statement_balance": _dollars(last["balance"]) if last else None,
                           "last_statement_issue_date": last["issued"].isoformat() if last else None,
                           "minimum_payment_amount": _dollars(max(3_500, int((last["balance"] if last else 0) * 0.02))),
                           "next_payment_due_date": due.isoformat()})
        items[item_key] = {"institution_id": INSTITUTION_PREFIX + item_key, "institution_name": institution,
                           "products": ["liabilities", "transactions"], "accounts": accounts, "transactions": txns(item_key, keys),
                           "holdings": None, "liabilities": {"credit": credit, "mortgage": [], "student": []}}

    items["brokerage"] = {
        "institution_id": INSTITUTION_PREFIX + "brokerage", "institution_name": "Demo Investments", "products": ["investments"],
        "accounts": [account(a["key"], a["name"], a["official_name"], "investment", a["subtype"], a["mask"], a["current"], None)
                     for a in invest_accounts],
        "transactions": [], "holdings": {"holdings": holdings_all, "securities": list(securities_all.values())}, "liabilities": None,
        "investment_transactions": _investment_activity(ledger, anchor, start, prices)}

    # Daily balance history for the Plaid accounts (everything before today; the sync records today's).
    days = [start + timedelta(days=i) for i in range((anchor - start).days)]
    histories: dict[str, list[tuple[str, int]]] = {}
    for rows_key in ["CHK"]:
        opening = checking_current + ledger.total("CHK")          # balance before the first transaction
        running, series = opening, []
        by_day: dict[date, int] = {}
        for r in ledger.rows.get("CHK", []):
            by_day[r["day"]] = by_day.get(r["day"], 0) + r["cents"]
        for day in days:
            running -= by_day.get(day, 0)
            series.append((day.isoformat(), running))
        histories[_plaid_id(rows_key)] = series
    for key, *_rest in CARDS:
        running, series, by_day = 0, [], {}
        for r in ledger.rows.get(key, []):
            by_day[r["day"]] = by_day.get(r["day"], 0) + r["cents"]
        for day in days:
            running += by_day.get(day, 0)
            series.append((day.isoformat(), running))
        histories[_plaid_id(key)] = series
    for a in invest_accounts:
        market, level = [], 1.0
        for _ in days:
            level *= 1 + 0.0004 + rng.gauss(0, 0.009)
            market.append(level)
        n = max(len(days) - 1, 1)
        raw = [(RAMP_START + (1 - RAMP_START) * i / n) * market[i] for i in range(len(days))]     # saving x market
        shape = [x / raw[-1] for x in raw] if raw else []                                           # ends at 1.0
        # Rescale so the history starts at exactly RAMP_START of today's value (the market's ups and downs stay).
        low = shape[0] if shape else RAMP_START
        scaled = [RAMP_START + (x - low) * (1 - RAMP_START) / (1 - low) for x in shape] if low < 1 else shape
        histories[a["account_id"]] = [(day.isoformat(), int(a["current"] * scaled[i])) for i, day in enumerate(days)]

    manual = []
    for name, lines in MANUAL_ACCOUNTS:
        manual.append({"name": name, "lines": [
            {"ticker": ticker, "name": line_name, "kind": kind, "target_cents": target * 100,
             "quantity": round(target / prices[ticker], 3) if ticker else None}
            for ticker, line_name, kind, target in lines]})
    return Dataset(anchor=anchor, start=start, server={"anchor": anchor.isoformat(), "items": items}, histories=histories,
                   manual=manual, expected_net_worth_cents=checking_current + invest_total + manual_total - sum(card_owed.values()))


# ---------------------------------------------------------------- serving it in place of Plaid

_cache: dict[str, tuple[float, dict]] = {}


def data_path(home: Path) -> Path:
    return home / DATA_FILE


def load_server_data(home: Path) -> dict:
    """The generated dataset (cached until the file changes)."""
    path = data_path(home)
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        raise PlaidError("DEMO_DATA_MISSING", "The demo data file is missing. Run `fintrack demo --replace` to create it.") from None
    cached = _cache.get(str(path))
    if cached is None or cached[0] != mtime:
        _cache[str(path)] = (mtime, json.loads(path.read_text()))
    return _cache[str(path)][1]


def is_demo_token(access_token: str | None) -> bool:
    return bool(access_token) and str(access_token).startswith(ACCESS_PREFIX)


class DemoServer:
    """Plaid-shaped answers for demo Items, from the generated dataset."""

    def __init__(self, data: dict):
        self._items = {ACCESS_PREFIX + key: item for key, item in data["items"].items()}
        self._by_institution = {item["institution_id"]: item for item in data["items"].values()}

    def _item(self, access_token: str) -> dict:
        try:
            return self._items[access_token]
        except KeyError:
            raise PlaidError("INVALID_ACCESS_TOKEN", "That demo connection no longer exists.") from None

    def get_item(self, access_token: str) -> dict:
        item = self._item(access_token)
        return {"institution_id": item["institution_id"], "products": item["products"], "billed_products": item["products"]}

    def get_institution_name(self, institution_id: str) -> str:
        return self._by_institution[institution_id]["institution_name"]

    def get_institution_logo(self, institution_id: str) -> None:
        return None                                       # made-up institutions have no logo (a letter is shown)

    def search_institutions(self, query: str) -> list[dict]:
        return []                                         # not used: DemoAwareGateway asks Plaid (Sandbox)

    def get_accounts(self, access_token: str) -> list[dict]:
        return copy.deepcopy(self._item(access_token)["accounts"])

    def sync_transactions(self, access_token: str, cursor: str | None, count: int = 500) -> dict:
        item = self._item(access_token)
        everything = not cursor                 # the first call returns all of it; later calls have nothing new
        return {"added": copy.deepcopy(item["transactions"]) if everything else [], "modified": [], "removed": [],
                "next_cursor": "demo-all", "has_more": False, "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE"}

    def get_transactions(self, access_token: str, start: date, end: date, logos: bool = True,
                         original_description: bool = False) -> list[dict]:
        return []                               # demo transactions have no logos or raw text to back-fill

    def get_holdings(self, access_token: str) -> dict:
        return copy.deepcopy(self._item(access_token)["holdings"] or {"holdings": [], "securities": []})

    def get_liabilities(self, access_token: str) -> dict:
        return copy.deepcopy(self._item(access_token)["liabilities"] or {"credit": [], "mortgage": [], "student": []})

    def get_investment_transactions(self, access_token: str, start: date, end: date) -> list[dict]:
        everything = self._item(access_token).get("investment_transactions") or []
        return [copy.deepcopy(t) for t in everything if start.isoformat() <= t["date"] <= end.isoformat()]

    def remove_item(self, access_token: str) -> None:
        self._item(access_token)


class DemoAwareGateway:
    """The gateway the app uses in the Sandbox: demo Items are answered from the generated dataset (no
    network), everything else goes to the real PlaidGateway. See make_gateway."""

    def __init__(self, settings: Settings, real: PlaidGateway | None = None, server: DemoServer | None = None):
        self._settings = settings
        self._real_gateway = real
        self._server = server

    @property
    def _real(self) -> PlaidGateway:
        if self._real_gateway is None:
            self._real_gateway = PlaidGateway(self._settings)
        return self._real_gateway

    def _demo(self) -> DemoServer:
        if self._server is None:
            self._server = DemoServer(load_server_data(self._settings.home))
        return self._server

    # -- calls about one connection: demo tokens are answered here
    def get_item(self, access_token):
        return self._demo().get_item(access_token) if is_demo_token(access_token) else self._real.get_item(access_token)

    def get_accounts(self, access_token):
        return self._demo().get_accounts(access_token) if is_demo_token(access_token) else self._real.get_accounts(access_token)

    def sync_transactions(self, access_token, cursor, count=500):
        if is_demo_token(access_token):
            return self._demo().sync_transactions(access_token, cursor, count)
        return self._real.sync_transactions(access_token, cursor, count)

    def get_transactions(self, access_token, start, end, logos=True, original_description=False):
        if is_demo_token(access_token):
            return self._demo().get_transactions(access_token, start, end, logos, original_description)
        return self._real.get_transactions(access_token, start, end, logos, original_description)

    def get_holdings(self, access_token):
        return self._demo().get_holdings(access_token) if is_demo_token(access_token) else self._real.get_holdings(access_token)

    def get_liabilities(self, access_token):
        return self._demo().get_liabilities(access_token) if is_demo_token(access_token) else self._real.get_liabilities(access_token)

    def get_investment_transactions(self, access_token, start, end):
        if is_demo_token(access_token):
            return self._demo().get_investment_transactions(access_token, start, end)
        return self._real.get_investment_transactions(access_token, start, end)

    def remove_item(self, access_token):
        if is_demo_token(access_token):
            return self._demo().remove_item(access_token)
        return self._real.remove_item(access_token)

    def get_institution_name(self, institution_id):
        if str(institution_id).startswith(INSTITUTION_PREFIX):
            return self._demo().get_institution_name(institution_id)
        return self._real.get_institution_name(institution_id)

    def get_institution_logo(self, institution_id):
        if str(institution_id).startswith(INSTITUTION_PREFIX):
            return self._demo().get_institution_logo(institution_id)
        return self._real.get_institution_logo(institution_id)

    def search_institutions(self, query):
        return self._real.search_institutions(query)      # a name search involves no Item (Sandbox's own list)

    def sandbox_reset_login(self, access_token):
        if is_demo_token(access_token):
            raise PlaidError("DEMO_ITEM", "Demo connections can't simulate a login problem.")
        return self._real.sandbox_reset_login(access_token)

    def create_link_token(self, kind=None, access_token=None, account_selection=False):
        if is_demo_token(access_token):
            raise PlaidError("DEMO_ITEM", "Demo connections can't be re-linked.")
        return self._real.create_link_token(kind, access_token, account_selection)

    def exchange_public_token(self, public_token):
        return self._real.exchange_public_token(public_token)


def make_gateway(settings: Settings):
    """The Plaid gateway for this environment. Production gets the plain PlaidGateway, untouched; the
    Sandbox gets one that also serves the demo data."""
    if settings.is_production:
        return PlaidGateway(settings)
    return DemoAwareGateway(settings)


# ---------------------------------------------------------------- seeding the database

def resolve_prices(settings: Settings, quotes=None, given: dict | None = None) -> dict[str, float]:
    """Prices to size holdings with: `given`, else live Alpaca quotes where available, else the fallbacks."""
    if given is not None:
        return {**FALLBACK_PRICES, **given}
    prices = dict(FALLBACK_PRICES)
    if quotes is not None:
        try:
            prices.update({t: q.price for t, q in quotes.quotes(sorted(FALLBACK_PRICES)).items() if q and q.price > 0})
        except Exception as exc:                  # noqa: BLE001 - prices are a nicety, never a reason to fail
            log.warning("Live prices unavailable for the demo data (%s); using approximate prices", type(exc).__name__)
    return prices


def seed(conn: sqlite3.Connection, settings: Settings, cipher, *, today: date | None = None, prices: dict | None = None,
         quotes=None) -> dict:
    """Fill an EMPTY Sandbox database with the demo world. Returns a summary."""
    from fintrack import (budgets, manual, manual_positions, networth, price_history, recurring, sync)

    if settings.is_production:
        raise DemoError("Demo data is for the Sandbox only. This is a Production setup.")
    if conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] or conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
        raise DemoError("This database already has connections or accounts. Use --replace to back it up and start the demo fresh.")

    today = today or date.today()
    prices = resolve_prices(settings, quotes, prices)
    dataset = build_dataset(today, prices)
    path = data_path(settings.home)
    path.write_text(json.dumps(dataset.server))
    os.chmod(path, 0o600)
    _cache.pop(str(path), None)

    # 1. The Items, then the app's real sync over the generated data.
    for key, item in dataset.server["items"].items():
        conn.execute(
            "INSERT INTO items (plaid_item_id, plaid_env, institution_id, institution_name, access_token_enc, products)"
            " VALUES (?, 'sandbox', ?, ?, ?, ?)",
            (f"demo-item-{key}", item["institution_id"], item["institution_name"], cipher.encrypt(ACCESS_PREFIX + key),
             json.dumps(sorted(item["products"]))))
    conn.commit()
    gateway = DemoAwareGateway(settings, server=DemoServer(dataset.server))
    summary = sync.sync_all(conn, gateway, cipher, settings.home, settings.sync_investment_transactions)
    if summary.errors:
        raise DemoError("The demo sync failed: " + summary.describe())

    # 2. Daily net-worth history for the Plaid accounts (sync recorded today's).
    ids = {r["plaid_account_id"]: r for r in conn.execute("SELECT * FROM accounts WHERE is_manual = 0")}
    with conn:
        for account_id, series in dataset.histories.items():
            row = ids[account_id]
            conn.executemany(
                "INSERT OR REPLACE INTO balance_snapshots (date, account_id, balance_cents, classification, account_type)"
                " VALUES (?, ?, ?, ?, ?)",
                [(day, row["id"], cents, row["classification"], row["type"]) for day, cents in series])

    # 3. Manual investment accounts: lines whose amounts grow month by month (contributions), priced by `prices`.
    live = {}
    if quotes is not None:
        try:
            live = quotes.quotes(sorted({line["ticker"] for a in dataset.manual for line in a["lines"] if line["ticker"]}))
        except Exception:                          # noqa: BLE001
            live = {}
    months = []
    month = dataset.start
    while month <= today:
        months.append(month)
        month = _first_of_month(month, 1)
    last = max(len(months) - 1, 1)
    for spec in dataset.manual:
        account_id = conn.execute(
            "INSERT INTO accounts (name, type, classification, is_manual, iso_currency_code)"
            " VALUES (?, 'investment', 'asset', 1, 'USD')", (spec["name"],)).lastrowid
        for line in spec["lines"]:
            position_id = conn.execute("INSERT INTO manual_positions (account_id, ticker, name, kind) VALUES (?, ?, ?, ?)",
                                       (account_id, line["ticker"], line["name"], line["kind"])).lastrowid
            for i, first in enumerate(months):
                share = RAMP_START + (1 - RAMP_START) * i / last
                if line["ticker"]:
                    conn.execute("INSERT INTO manual_position_changes (position_id, date, quantity) VALUES (?, ?, ?)",
                                 (position_id, first.isoformat(), round(line["quantity"] * share, 3)))
                else:
                    conn.execute("INSERT INTO manual_position_changes (position_id, date, value_cents) VALUES (?, ?, ?)",
                                 (position_id, first.isoformat(), int(line["target_cents"] * share)))
            if line["ticker"]:
                manual_positions._save_price(conn, line["ticker"], today.isoformat(), prices[line["ticker"]], "you")
        conn.commit()
        manual_positions.revalue(conn, account_id, live, since=dataset.start.isoformat(), today=today)
    # Real daily closes (when Alpaca is reachable) turn the flat history into the market's.
    if quotes is not None:
        try:
            price_history.backfill(conn, quotes, today)
        except Exception as exc:                   # noqa: BLE001
            log.warning("Price history unavailable for the demo data (%s)", type(exc).__name__)
    networth.take_snapshot(conn)

    # 4. Recurring items confirmed (so the forecast has something), and budgets.
    for row in recurring.list_items(conn, "detected"):
        recurring.set_status(conn, row["id"], "confirmed")
    first_month = f"{dataset.start:%Y-%m}"
    for category, steps in BUDGETS.items():
        for from_month, amount in steps:
            try:
                budgets.set_budget(conn, category, from_month or first_month, str(amount), False)
            except budgets.BudgetError as exc:
                log.warning("Demo budget for %s skipped: %s", category, exc)

    nw = networth.current_net_worth(conn)
    return {"items": len(dataset.server["items"]), "plaid_accounts": len(ids), "manual_accounts": len(dataset.manual),
            "transactions": conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
            "net_worth_cents": nw.net_cents, "since": dataset.start.isoformat()}
