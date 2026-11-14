"""Category icons (user's request 2026-10-20).

A transaction without a logo shows its category's icon instead of a letter, and the Categories page lets you pick
each category's icon.

You can create any category, so the icons aren't tied to categories. ICONS is a fixed library of general concepts
(food, car, pet, savings, ...), each with a theme group and keywords. Any category, built in or yours, gets an icon
in this order (`resolve`):

    1. your choice                 (categories.icon)
    2. a match on its name         (`guess`: the name's words against each icon's keywords, e.g. "Dog grooming" -> paw)
    3. the built-in default        (BUILT_IN, Plaid's top-level categories, for names that match nothing)
    4. its parent category's icon  (a subcategory)
    5. one for its kind            (KIND_DEFAULT: income, not counted, spending)

The name is tried before the built-in default so a renamed category follows its new name, and so Plaid's ~100
subcategories ("Coffee", "Gas & electricity", "Rental cars") get fitting icons without a table of their own. The
Categories page offers the name's best matches first (`suggestions`), then the whole library by group.

The drawings are fintrack's own: 24x24 strokes in the text color (currentColor), like templates/_icons.html, so they
follow light and dark mode; no icon font or CDN. The markup below is a constant of this module (never user data), and
templates render it inside the <svg> of `_icons.html`'s `category_icon` macro.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Icon:
    name: str
    label: str
    group: str
    keywords: tuple[str, ...]
    svg: str


def _icon(name: str, label: str, group: str, keywords: str, svg: str) -> Icon:
    return Icon(name, label, group, tuple(k.strip() for k in keywords.split(",") if k.strip()), svg)


# Order matters twice: it is the picker's order, and an equal keyword match goes to the icon listed first.
# Keywords avoid words Plaid uses as qualifiers ("Personal loan payment", "Other general services"): those names
# should take their parent's icon, not Treats or Other (user's report 2026-10-20).
ICONS: tuple[Icon, ...] = (
    # ---- Food
    _icon("utensils", "Dining", "Food", "food, dining, restaurant, meal, lunch, dinner, breakfast, takeout, eating, drink",
          '<path d="M7 3v8"/><path d="M4.5 3v5a2.5 2.5 0 0 0 5 0V3"/><path d="M7 11v10"/>'
          '<path d="M17 21V3c-2.2 1.2-3.5 3.6-3.5 7v3H17"/>'),
    _icon("coffee", "Coffee", "Food", "coffee, cafe, café, tea, bakery, coffee shop",
          '<path d="M4 9h13v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M17 10h1.5a2.5 2.5 0 0 1 0 5H17"/>'
          '<path d="M8 3v3"/><path d="M12 3v3"/>'),
    _icon("cart", "Groceries", "Food", "grocery, supermarket, market, farmers market, food store",
          '<circle cx="9" cy="20" r="1.5"/><circle cx="17" cy="20" r="1.5"/>'
          '<path d="M2 3h3l2.6 12.2a1 1 0 0 0 1 .8h9.2a1 1 0 0 0 1-.8L21 7H6"/>'),
    _icon("wine", "Drinks", "Food", "bar, alcohol, wine, beer, liquor, nightlife, pub, brewery",
          '<path d="M8 3h8l-.5 6a3.5 3.5 0 0 1-7 0z"/><path d="M12 12.5V20"/><path d="M8 21h8"/>'),
    # ---- Shopping
    _icon("bag", "Shopping", "Shopping", "shopping, merchandise, store, retail, department store, superstore, mall",
          '<path d="M5 8h14l-1 13H6z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>'),
    _icon("shirt", "Clothing", "Shopping", "clothing, clothes, apparel, fashion, shoes, accessories, laundry, dry cleaning",
          '<path d="M8 3 3 6l2 4 2-1v12h10V9l2 1 2-4-5-3a4 4 0 0 1-8 0z"/>'),
    _icon("box", "Online orders", "Shopping", "online, amazon, package, delivery, shipping, marketplace, storage",
          '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5 12 12l9-4.5"/><path d="M12 12v9"/>'),
    _icon("laptop", "Electronics", "Shopping", "electronics, computer, software, tech, technology, app, gadget, apple store",
          '<rect x="4" y="5" width="16" height="11" rx="1.5"/><path d="M2 19h20"/>'),
    _icon("gift", "Gifts", "Shopping", "gift, present, birthday, holiday, christmas, novelty",
          '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M5 12v9h14v-9"/><path d="M12 8v13"/>'
          '<path d="M12 8C10.5 4 7 4 7 6s2.5 2 5 2 5 0 5-2-3.5-2-5 2"/>'),
    _icon("book", "Books", "Shopping", "book, bookstore, reading, newspaper, newsstand, magazine, news",
          '<path d="M4 4h6a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4z"/><path d="M20 4h-6a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h6z"/>'),
    # ---- Home
    _icon("home", "Home", "Home", "home, rent, mortgage, house, housing, apartment, hoa",
          '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/>'),
    _icon("bulb", "Utilities", "Home", "utilities, utility, electric, electricity, power, energy, gas and electricity",
          '<path d="M9 18h6"/><path d="M10 21h4"/>'
          '<path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>'),
    _icon("droplet", "Water", "Home", "water, sewer, sewage",
          '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>'),
    _icon("wifi", "Internet", "Home", "internet, cable, wifi, broadband, internet and cable",
          '<path d="M2 9a15 15 0 0 1 20 0"/><path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M8.5 16a5 5 0 0 1 7 0"/>'
          '<circle cx="12" cy="19.5" r=".8" fill="currentColor"/>'),
    _icon("phone", "Phone", "Home", "phone, mobile, cell, wireless, telephone, cell phone",
          '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>'),
    _icon("roller", "Home improvement", "Home",
          "home improvement, renovation, remodel, repair, hardware, paint, furniture, decor, maintenance",
          '<rect x="4" y="3" width="14" height="5" rx="1"/><path d="M18 5.5h2v5h-8v3"/>'
          '<rect x="10.5" y="13.5" width="3" height="7.5" rx="1"/>'),
    _icon("leaf", "Garden", "Home", "garden, gardening, lawn, plant, landscaping, nursery, yard",
          '<path d="M5 19c0-8 5-14 15-15-1 10-7 15-15 15z"/><path d="M5 19l8-8"/>'),
    _icon("trash", "Waste", "Home", "trash, garbage, waste, recycling, sanitation, waste management",
          '<path d="M4 7h16"/><path d="M10 3h4"/><path d="M6 7l1 14h10l1-14"/>'),
    # ---- Getting around
    _icon("car", "Car", "Getting around",
          "car, auto, automotive, vehicle, transportation, rideshare, ride share, uber, lyft, taxi, rental car",
          '<path d="M3 13l2-6h14l2 6"/><rect x="3" y="13" width="18" height="5" rx="1.5"/>'
          '<circle cx="7.5" cy="15.5" r=".8" fill="currentColor"/><circle cx="16.5" cy="15.5" r=".8" fill="currentColor"/>'
          '<path d="M5 18v2"/><path d="M19 18v2"/>'),
    _icon("fuel", "Fuel", "Getting around", "gas, fuel, gasoline, petrol, gas station, charging, ev charging",
          '<path d="M4 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16"/><path d="M3 21h12"/><path d="M4 10h10"/>'
          '<path d="M14 8h2a2 2 0 0 1 2 2v6a1.5 1.5 0 0 0 3 0V8l-3-3"/>'),
    _icon("bus", "Transit", "Getting around", "transit, bus, train, subway, metro, rail, public transit, commute",
          '<rect x="5" y="3" width="14" height="15" rx="2"/><path d="M5 11h14"/><path d="M8 21v-3"/><path d="M16 21v-3"/>'
          '<circle cx="8.5" cy="14.5" r=".8" fill="currentColor"/><circle cx="15.5" cy="14.5" r=".8" fill="currentColor"/>'),
    _icon("parking", "Parking & tolls", "Getting around", "parking, toll",
          '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M10 17V7h3.5a3 3 0 0 1 0 6H10"/>'),
    _icon("plane", "Travel", "Getting around", "travel, trip, flight, airline, airfare, vacation",
          '<path d="M2 13l20-8-6 15-4-6z"/><path d="M12 14l10-9"/>'),
    _icon("bed", "Lodging", "Getting around", "hotel, lodging, airbnb, accommodation, motel",
          '<path d="M3 19V6"/><path d="M3 15h18v4"/><path d="M21 15v-3a3 3 0 0 0-3-3h-7v6"/><circle cx="7" cy="11" r="2"/>'),
    # ---- Health & care
    _icon("heart", "Health", "Health & care",
          "medical, health, healthcare, doctor, hospital, clinic, dental, dentist, vision, therapy, care",
          '<path d="M12 20s-8-4.5-8-10.5A4.5 4.5 0 0 1 12 6.5a4.5 4.5 0 0 1 8 3c0 6-8 10.5-8 10.5z"/>'),
    _icon("pill", "Pharmacy", "Health & care", "pharmacy, medicine, medication, prescription, drugstore, supplement",
          '<rect x="3.5" y="9" width="17" height="6" rx="3" transform="rotate(-45 12 12)"/><path d="M9.9 9.9l4.2 4.2"/>'),
    _icon("dumbbell", "Fitness", "Health & care", "gym, fitness, sport, exercise, workout, yoga, athletic, fitness center",
          '<path d="M6 7v10"/><path d="M18 7v10"/><path d="M3 10v4"/><path d="M21 10v4"/><path d="M6 12h12"/>'),
    _icon("scissors", "Personal care", "Health & care",
          "personal care, hair, haircut, salon, barber, beauty, spa, nail, cosmetics",
          '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M8.5 7.5 20 18"/><path d="M8.5 16.5 20 6"/>'),
    _icon("paw", "Pets", "Health & care", "pet, dog, cat, vet, veterinary, grooming, pet supplies",
          '<path d="M8 17.5c0-2.5 2-4.5 4-4.5s4 2 4 4.5c0 1.6-1.4 2.5-4 2.5s-4-.9-4-2.5z"/>'
          '<circle cx="6" cy="11" r="1.6"/><circle cx="9.5" cy="7" r="1.6"/><circle cx="14.5" cy="7" r="1.6"/>'
          '<circle cx="18" cy="11" r="1.6"/>'),
    _icon("people", "Family & kids", "Health & care",
          "family, kid, child, children, childcare, baby, daycare, babysitter, allowance",
          '<circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><circle cx="17" cy="10" r="2.2"/>'
          '<path d="M15.5 15.2A4.5 4.5 0 0 1 21 19.5"/>'),
    # ---- Fun & learning
    _icon("film", "Entertainment", "Fun & learning",
          "entertainment, movie, cinema, film, streaming, tv, television, netflix, tv and movies",
          '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16"/><path d="M17 4v16"/>'
          '<path d="M3 9h4"/><path d="M3 15h4"/><path d="M17 9h4"/><path d="M17 15h4"/>'),
    _icon("music", "Music", "Fun & learning", "music, concert, spotify, audio, music and audio",
          '<path d="M9 18V5l11-2v13"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>'),
    _icon("game", "Games", "Fun & learning", "game, gaming, video game, hobby, toy, casino, gambling, lottery",
          '<path d="M7 7h10a5 5 0 0 1 4.5 7.2l-.6 1.3a2.5 2.5 0 0 1-4.2.5L15 14H9l-1.7 2a2.5 2.5 0 0 1-4.2-.5l-.6-1.3'
          'A5 5 0 0 1 7 7z"/><path d="M8 9.5v3"/><path d="M6.5 11h3"/>'
          '<circle cx="16" cy="10.5" r=".8" fill="currentColor"/><circle cx="17.5" cy="12" r=".8" fill="currentColor"/>'),
    _icon("ticket", "Events", "Fun & learning",
          "event, ticket, theater, theatre, show, sporting event, museum, attraction, amusement park",
          '<path d="M3 7h18v3a2 2 0 0 0 0 4v3H3v-3a2 2 0 0 0 0-4z"/><path d="M15 7v10" stroke-dasharray="2 2"/>'),
    _icon("star", "Treats", "Fun & learning", "fun, treat, splurge, hobby, leisure",
          '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>'),
    _icon("cap", "Education", "Fun & learning",
          "education, school, tuition, college, university, student, class, course, learning",
          '<path d="M2 9l10-5 10 5-10 5z"/><path d="M6 11v5c0 1.5 3 3 6 3s6-1.5 6-3v-5"/><path d="M22 9v5"/>'),
    # ---- Money
    _icon("income", "Income", "Money",
          "income, salary, paycheck, payroll, wage, deposit, earnings, bonus, reimbursement, refund",
          '<path d="M12 3v11"/><path d="M8 10l4 4 4-4"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>'),
    _icon("arrow-in", "Money in", "Money", "transfer in, incoming, money in",
          '<path d="M17 7 7 17"/><path d="M15 17H7V9"/>'),
    _icon("arrow-out", "Money out", "Money", "transfer out, outgoing, sent, money out",
          '<path d="M7 17 17 7"/><path d="M9 7h8v8"/>'),
    _icon("transfer", "Transfers", "Money", "transfer, between accounts, account transfer",
          '<path d="M4 8h14"/><path d="M14 4l4 4-4 4"/><path d="M20 16H6"/><path d="M10 12l-4 4 4 4"/>'),
    _icon("cash", "Cash", "Money", "cash, atm, withdrawal, money, cash and atm",
          '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/>'
          '<path d="M6 9.5v.01"/><path d="M18 14.5v.01"/>'),
    _icon("card", "Cards", "Money", "credit card, card, card payment, debit card",
          '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/><path d="M6 15h4"/>'),
    _icon("bank", "Bank", "Money", "bank, banking, fee, bank fee, overdraft, loan, interest charge, finance, financial",
          '<path d="M3 9.5 12 4l9 5.5"/><path d="M4 21h16"/><path d="M5 10v8"/><path d="M9.5 10v8"/>'
          '<path d="M14.5 10v8"/><path d="M19 10v8"/><path d="M4 18h16"/>'),
    _icon("percent", "Interest", "Money", "interest, dividend, interest earned, rate, apr",
          '<path d="M19 5 5 19"/><circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="17" r="2.5"/>'),
    _icon("jar", "Savings", "Money", "saving, savings, save, emergency fund, goal, rainy day",
          '<path d="M7 3h10"/><path d="M8 3v2a5 5 0 0 0-3 4.6V19a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9.6A5 5 0 0 0 16 5V3"/>'
          '<path d="M12 11v6"/><path d="M9 14h6"/>'),
    _icon("trend", "Investments", "Money",
          "investment, investing, brokerage, stock, retirement, 401k, ira, roth, crypto, pension, retirement fund",
          '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>'),
    _icon("receipt", "Bills", "Money", "bill, invoice, accounting, tax preparation",
          '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6"/><path d="M9 12h6"/><path d="M9 16h3"/>'),
    _icon("repeat", "Subscriptions", "Money", "subscription, membership, recurring, dues",
          '<path d="M17 2l4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="M7 22l-4-4 4-4"/>'
          '<path d="M21 13v1a4 4 0 0 1-4 4H3"/>'),
    _icon("shield", "Insurance", "Money", "insurance, protection, warranty, security",
          '<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/>'),
    # ---- Other
    _icon("landmark", "Government", "Other", "government, tax, irs, dmv, fine, license, permit, public, tax payment",
          '<path d="M12 3v3"/><path d="M5 11a7 5 0 0 1 14 0"/><path d="M4 11h16"/><path d="M6 11v7"/><path d="M10 11v7"/>'
          '<path d="M14 11v7"/><path d="M18 11v7"/><path d="M3 21h18"/><path d="M4 18h16"/>'),
    _icon("hand", "Giving", "Other", "donation, charity, giving, church, tithe, nonprofit, lent, lending",
          '<path d="M12 11.5s-3.5-2-3.5-4.3A2 2 0 0 1 12 6a2 2 0 0 1 3.5 1.2c0 2.3-3.5 4.3-3.5 4.3z"/>'
          '<path d="M2 15h4l4 2h4a1.5 1.5 0 0 0 0-3h-3"/><path d="M14 16.5l5-2.2a1.6 1.6 0 0 1 1.6 2.7L14 21H6l-4-2"/>'),
    _icon("briefcase", "Work", "Other",
          "work, business, office, professional, services, consulting, legal, lawyer, accountant",
          '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>'
          '<path d="M3 13h18"/>'),
    _icon("wrench", "Repairs", "Other", "repair, mechanic, plumber, handyman, auto repair",
          '<path d="M15 4a4 4 0 0 0-3.6 5.7L4 17.1V20h2.9l7.4-7.4A4 4 0 0 0 20 9l-2.5 2.5-3-3L17 6"/>'),
    _icon("envelope", "Mail", "Other", "mail, postage, postal, usps, ups, fedex, postage and shipping",
          '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>'),
    _icon("tag", "Other", "Other", "misc, miscellaneous",
          '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/><circle cx="7.5" cy="7.5" r="1.3"/>'),
    _icon("question", "Uncategorized", "Other", "uncategorized, unknown, unsorted",
          '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.7"/>'
          '<path d="M12 17v.01"/>'),
)

BY_NAME: dict[str, Icon] = {i.name: i for i in ICONS}
GROUPS: tuple[str, ...] = tuple(dict.fromkeys(i.group for i in ICONS))

# Plaid's top-level categories (migration 0005), for when a renamed one's name matches nothing.
BUILT_IN = {
    "INCOME": "income", "FOOD_AND_DRINK": "utensils", "GENERAL_MERCHANDISE": "bag", "RENT_AND_UTILITIES": "home",
    "TRANSPORTATION": "car", "TRAVEL": "plane", "ENTERTAINMENT": "film", "PERSONAL_CARE": "scissors",
    "MEDICAL": "heart", "HOME_IMPROVEMENT": "roller", "GENERAL_SERVICES": "briefcase",
    "GOVERNMENT_AND_NON_PROFIT": "landmark", "BANK_FEES": "bank", "LOAN_PAYMENTS": "bank", "CASH_WITHDRAWAL": "cash",
    "OTHER": "tag", "UNCATEGORIZED": "question", "CREDIT_CARD_PAYMENT": "card", "LOAN_DISBURSEMENTS": "bank",
    "TRANSFER_IN": "arrow-in", "TRANSFER_OUT": "arrow-out",
}
KIND_DEFAULT = {"income": "income", "transfer": "transfer", "expense": "tag"}
SUGGESTIONS = 6


# ---------------------------------------------------------------- matching a name to icons

def _singular(word: str) -> str:
    """A rough singular, applied to names and keywords alike: 'groceries' -> 'grocery', 'taxes' -> 'tax'."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith(("xes", "sses", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _words(text: str) -> str:
    """' grocery and household ' for 'Groceries & household': lower case, & -> and, singular words, padded so a
    keyword phrase is found with ' phrase ' (whole words only)."""
    text = text.lower().replace("&", " and ")
    return " " + " ".join(_singular(w) for w in re.findall(r"[a-z0-9é]+", text)) + " "


# Each keyword once after _words ("utility" and "utilities" are the same word), so none counts twice.
_KEYWORDS: dict[str, tuple[str, ...]] = {i.name: tuple(dict.fromkeys(_words(k) for k in i.keywords)) for i in ICONS}


def scores(label: str) -> list[tuple[int, str]]:
    """(score, icon name) for every icon the name matches, best first; a keyword phrase scores its number of words,
    so 'Gas & electricity' goes to Utilities (3) over Fuel ('gas', 1). Ties keep ICONS' order."""
    text = _words(label or "")
    found = []
    for position, icon in enumerate(ICONS):
        score = sum(len(k.split()) for k in _KEYWORDS[icon.name] if k in text)
        if score:
            found.append((-score, position, icon.name))
    return [(-s, name) for s, _, name in sorted(found)]


def guess(label: str) -> str | None:
    """The icon whose keywords the name matches best, if any."""
    found = scores(label)
    return found[0][1] if found else None


def suggestions(label: str, limit: int = SUGGESTIONS) -> list[str]:
    """The picker's first row: the icons the name matches, best first."""
    return [name for _, name in scores(label)[:limit]]


# ---------------------------------------------------------------- each category's icon

def resolve(conn: sqlite3.Connection) -> dict[str, str]:
    """{category key: icon name} for every category (order in the module docstring)."""
    rows = {r["key"]: r for r in conn.execute("SELECT key, label, kind, parent_key, icon FROM categories")}

    def own(row) -> str | None:
        """What a category decides by itself: your choice, its name, or its built-in default."""
        if row["icon"] in BY_NAME:
            return row["icon"]
        return guess(row["label"]) or BUILT_IN.get(row["key"])

    icons = {}
    for key, row in rows.items():
        parent = rows.get(row["parent_key"]) if row["parent_key"] else None
        icons[key] = own(row) or (parent and own(parent)) or KIND_DEFAULT.get(row["kind"], "tag")
    return icons


def set_icon(conn: sqlite3.Connection, key: str, icon: str | None) -> None:
    """Your icon for a category; None or "" = automatic again."""
    icon = icon or None
    if icon is not None and icon not in BY_NAME:
        raise ValueError("Choose an icon from the list.")
    with conn:
        if conn.execute("UPDATE categories SET icon = ? WHERE key = ?", (icon, key)).rowcount == 0:
            raise ValueError("That category no longer exists.")
