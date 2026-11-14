"""The phone's read-only copy, server side (web/offline.py; docs/phone-offline.md): phones only, switched by
PHONE_OFFLINE_COPY, the list of pages a phone saves, and the browser cache still off."""

import dataclasses
import re
from datetime import date, timedelta

import pytest

from fintrack.web import offline
from tests.test_networth import add_account, add_item
from tests.test_remote import PHONE_ORIGIN, _client, ready, remote_settings, sign_in  # noqa: F401 (fixtures)


@pytest.fixture
def copy_on(ready):
    return dataclasses.replace(ready, phone_offline_copy=True)


def phone_client(settings, cipher, gateway):
    return _client(settings, cipher, gateway, phone=True)


def mac_client(settings, cipher, gateway):
    return _client(settings, cipher, gateway, phone=False)


def test_macs_pages_never_carry_the_copy(copy_on, conn, cipher, gateway):
    mac = mac_client(copy_on, cipher, gateway)
    page = mac.get("/").text
    assert "data-offline-copy" not in page and "offline-copy.js" not in page
    assert mac.get("/offline/pages").status_code == 404


def test_phones_are_told_to_remove_the_copy_when_it_is_off(ready, conn, cipher, gateway):
    phone = phone_client(ready, cipher, gateway)
    sign_in(phone)
    page = phone.get("/").text
    assert 'data-offline-copy="off"' in page and "offline-copy.js" in page
    assert phone.get("/offline/pages").status_code == 404


def test_phones_get_the_worker_and_the_list_when_it_is_on(copy_on, conn, cipher, gateway):
    add_item(conn)
    add_account(conn, 1, "depository", 100000)
    add_account(conn, 2, "credit", 5000, hidden=1)
    conn.commit()
    phone = phone_client(copy_on, cipher, gateway)

    worker = phone.get(offline.WORKER_PATH)          # code, no data: reachable before the passcode
    assert worker.status_code == 200 and worker.headers["content-type"].startswith("text/javascript")
    assert phone.get("/offline/pages").status_code == 303          # the list needs the passcode session

    sign_in(phone)
    assert 'data-offline-copy="on"' in phone.get("/").text
    response = phone.get("/offline/pages")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"         # the browser's own cache stays off
    listing = response.json()
    assert isinstance(listing["revision"], int)
    by_path = {p["path"]: p for p in listing["pages"]}
    for path, default in offline.RANGE_PAGES.items():
        assert by_path[path]["default"] == default
        assert set(by_path[path]["ranges"]) == {"this-month", "last-month", "90", "ytd", "last-year", "365"}
    for path in offline.PLAIN_PAGES:
        assert by_path[path] == {"path": path}
    assert "/accounts/1" in by_path and "/accounts/2" not in by_path   # visible accounts only
    assert not any(p.startswith(("/setup", "/rules", "/categories", "/connect")) for p in by_path)
    assert "/static/app.css" in listing["static"]
    assert "/static/link.js" not in listing["static"] and "/static/offline-sw.js" not in listing["static"]


def test_phone_pages_carry_the_session_their_key_belongs_to(copy_on, ready, conn, cipher, gateway):
    """The copy's key is made per passcode session (offline-store.js): pages say which session, by its expiry (the
    cookie's public half, never its signature). A new login is a new session."""
    phone = phone_client(copy_on, cipher, gateway)
    sign_in(phone)
    cookie = phone.cookies.get("ft_remote")
    expires, signature = cookie.split(".", 1)
    page = phone.get("/").text
    assert f'data-offline-session="{expires}"' in page and signature not in page
    assert "offline-store.js" in page and "offline-copy.js" in page

    # with the copy off, no session on the page (the script only wipes)
    off = phone_client(ready, cipher, gateway)
    sign_in(off)
    assert "data-offline-session" not in off.get("/").text


def test_transactions_pages_follow_the_lists_own_count(conn):
    add_item(conn)
    add_account(conn, 1, "depository", 100000)
    today = date.today()
    for n in range(150):    # 150 fake purchases over the last 75 days
        conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name,"
                     " plaid_category_key, pending) VALUES (?, 1, ?, 500, 'FAKE', 'GENERAL_MERCHANDISE', 0)",
                     (f"offline-{n}", (today - timedelta(days=n // 2)).isoformat()))
    conn.commit()
    ranges = next(p for p in offline.page_list(conn) if p["path"] == "/transactions")["ranges"]
    assert ranges["90"] == 2 and ranges["365"] == 2          # 150 rows = 2 pages of 100
    assert ranges["last-year"] == 1                           # none: still one (empty) page


@pytest.mark.parametrize("path", list(offline.RANGE_PAGES))
def test_each_pages_default_range_is_what_the_page_opens_on(path, settings, conn, cipher, gateway):
    """The copy treats a page opened with no range as its default preset: it must be the page's own default."""
    page = mac_client(settings, cipher, gateway).get(path).text
    picked = re.search(r'name="range"[^>]*value="([^"]+)"', page).group(1)
    assert picked == offline.RANGE_PAGES[path]


def test_pages_are_compressed_and_still_not_cached(copy_on, conn, cipher, gateway):
    """gzip (web/app.py) for phones over mobile data and the copy's refresh; the browser cache stays off."""
    phone = phone_client(copy_on, cipher, gateway)
    sign_in(phone)
    response = phone.get("/", headers={"accept-encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["cache-control"] == "no-store"
    assert "Content-Security-Policy" in response.headers
