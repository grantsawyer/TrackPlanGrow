"""Category icons (category_icons.py, the Categories page's picker). Fake data only."""

import re

import pytest

from fintrack import categories, category_icons

ORIGIN = {"origin": "https://localhost:8443"}


def test_the_library_is_well_formed():
    names = [i.name for i in category_icons.ICONS]
    assert len(names) == len(set(names)) and len(names) >= 40
    assert set(category_icons.BUILT_IN.values()) <= set(names)
    assert set(category_icons.KIND_DEFAULT.values()) <= set(names)
    for icon in category_icons.ICONS:
        assert icon.keywords and icon.group in category_icons.GROUPS
        assert "<script" not in icon.svg and "style=" not in icon.svg and re.fullmatch(r"(<[a-z]+ [^<>]*/>)+", icon.svg)


@pytest.mark.parametrize("label, icon", [
    ("Groceries", "cart"),                    # plural names match singular keywords
    ("Gas & electricity", "bulb"),            # a two-word phrase beats "gas" alone
    ("Gas", "fuel"),
    ("Rent & utilities", "home"),             # a tie goes to the icon listed first
    ("Dog grooming", "paw"),                  # a custom name
    ("Kids", "people"),
    ("Scarves", None),                        # whole words only: no "car" inside "scarves"
    ("Unemployment", None),
    # Plaid's qualifiers ("Personal", "Other", "General") match nothing (user's report 2026-10-20)
    ("Personal loan payment", "bank"),
    ("Other payment", None),
    ("Other general merchandise", "bag"),
    ("Other general services", "briefcase"),
])
def test_a_name_finds_its_icon(label, icon):
    assert category_icons.guess(label) == icon


def test_suggestions_are_the_best_matches_first():
    assert category_icons.suggestions("Coffee & tea")[0] == "coffee"
    assert category_icons.suggestions("Zzz fake") == []


@pytest.fixture
def db(conn):
    return conn


def test_your_choice_then_the_name_then_built_in_then_parent_then_kind(db):
    icons = category_icons.resolve(db)
    assert icons["FOOD_AND_DRINK"] == "utensils" and icons["UNCATEGORIZED"] == "question"
    assert icons["OTHER"] == "tag"                                      # Plaid's Other: its built-in default
    categories.rename_category(db, "OTHER", "Kids")                     # a renamed category follows its name
    categories.rename_category(db, "TRAVEL", "Zzz fake")                # ... unless the name matches nothing
    sub = categories.add_subcategory(db, "TRANSPORTATION", "Bikes & scooters")
    mine = categories.add_category(db, "Fake stuff", "income")
    icons = category_icons.resolve(db)
    assert (icons["OTHER"], icons["TRAVEL"], icons[sub], icons[mine]) == ("people", "plane", "car", "income")
    category_icons.set_icon(db, sub, "star")
    assert category_icons.resolve(db)[sub] == "star"
    category_icons.set_icon(db, sub, "")                                # automatic again
    assert category_icons.resolve(db)[sub] == "car"
    with pytest.raises(ValueError):
        category_icons.set_icon(db, sub, "not-an-icon")
    with pytest.raises(ValueError):
        category_icons.set_icon(db, "NO_SUCH_CATEGORY", "star")


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_the_categories_page_picks_icons(client, db):
    page = client.get("/categories").text
    assert page.count('id="icon-picker"') == 1                                     # one shared picker
    assert page.count('class="icon-choice"') == len(category_icons.ICONS)
    button = page.split('data-icon-picker="/categories/FOOD_AND_DRINK/icon"')[1].split("</button>")[0]
    assert 'data-icon-current=""' in button and 'data-icon-suggest="utensils' in button and "(automatic)" in button
    r = client.post("/categories/FOOD_AND_DRINK/icon", data={"icon": "coffee"}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    assert category_icons.resolve(db)["FOOD_AND_DRINK"] == "coffee"
    assert 'data-icon-current="coffee"' in client.get("/categories").text
    bad = client.post("/categories/FOOD_AND_DRINK/icon", data={"icon": "<svg>"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"] and category_icons.resolve(db)["FOOD_AND_DRINK"] == "coffee"
