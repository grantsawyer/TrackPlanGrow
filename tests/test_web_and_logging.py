import logging

import pytest
from fastapi.testclient import TestClient

from fintrack.crypto import TokenCipher
from fintrack.logging_setup import RedactingFilter
from fintrack.money import format_cents, to_cents
from fintrack.web.app import create_app
from tests.fakes import FakeGateway

ORIGIN = {"origin": "https://localhost:8443"}


def make_client(settings, gateway):
    app = create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher)
    return TestClient(app, base_url="https://localhost:8443")


@pytest.fixture
def client(settings, conn, cipher, gateway):
    return make_client(settings, gateway)


@pytest.fixture
def prod_client(prod_settings, prod_conn, cipher, gateway):
    return make_client(prod_settings, gateway)


# ---------------------------------------------------------------- security

def test_setup_page_and_csp(client):
    response = client.get("/setup")
    assert response.status_code == 200
    assert "Up to date" in response.text
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "https://cdn.plaid.com/link/v2/stable/link-initialize.js" in csp
    assert "https://sandbox.plaid.com/" in csp and "production.plaid.com" not in csp
    assert "unsafe-inline" not in csp


def test_production_csp_targets_production_api(prod_client):
    csp = prod_client.get("/setup").headers["content-security-policy"]
    assert "https://production.plaid.com/" in csp and "sandbox.plaid.com" not in csp


def test_foreign_host_header_rejected(client):
    # Defends against DNS rebinding: evil.example resolving to 127.0.0.1.
    assert client.get("/setup", headers={"host": "evil.example"}).status_code == 400


def test_referrer_policy_keeps_origin_on_form_posts(client):
    # Regression: with "no-referrer", browsers send `Origin: null` on plain HTML
    # form POSTs, so the CSRF check refused our own Remove/Simulate buttons.
    policy = client.get("/setup").headers["referrer-policy"]
    assert policy == "same-origin"


def test_origin_null_still_refused(client):
    response = client.post("/items/1/refresh", headers={"origin": "null"})
    assert response.status_code == 403


def test_cross_origin_post_rejected(client, gateway):
    response = client.post("/link/token/create", json={"kind": "banking"}, headers={"origin": "https://evil.example"})
    assert response.status_code == 403
    assert client.post("/link/token/create", json={"kind": "banking"}).status_code == 403  # no Origin
    assert gateway.calls == []


# ---------------------------------------------------------------- Link flow

def test_full_sandbox_connect_flow(client):
    token = client.post("/link/token/create", json={"kind": "banking"}, headers=ORIGIN).json()
    assert token["link_token"].startswith("link-sandbox-fake")

    done = client.post(
        "/link/complete",
        json={"session_id": token["session_id"], "purpose": "create", "public_token": "public-sandbox-x"},
        headers=ORIGIN,
    ).json()
    assert done["redirect"].startswith("/setup?msg=Connected+First+Platypus+Bank")

    page = client.get("/setup").text
    assert "First Platypus Bank" in page and "Configuration" in page    # connections + the checks on one page
    # Settings → Setup lists each connection's account names only (user's request 2026-10-14), linked to their
    # Balances pages; masks and balances are on those pages
    assert 'class="plain-list account-names"' in page and 'href="/accounts/1"' in page
    assert "123456783333" not in page                                     # never a full account number
    assert "····0000" not in page and "$110.00" not in page


def test_duplicate_link_returns_409_without_exchange(client, gateway):
    first = client.post("/link/token/create", json={"kind": "banking"}, headers=ORIGIN).json()
    client.post("/link/complete", json={**first, "purpose": "create", "public_token": "p1"}, headers=ORIGIN)

    second = client.post("/link/token/create", json={"kind": "banking"}, headers=ORIGIN).json()
    response = client.post(
        "/link/complete",
        json={**second, "purpose": "create", "public_token": "p2", "institution_id": "ins_fake_1",
              "accounts": [{"name": "Fake Checking", "mask": "0000"}]},
        headers=ORIGIN,
    )
    assert response.status_code == 409
    assert "already connected" in response.json()["error"]
    assert ("exchange_public_token", "p2") not in gateway.calls


def test_production_token_requires_consent_over_http(prod_client, gateway):
    response = prod_client.post("/link/token/create", json={"kind": "banking"}, headers=ORIGIN)
    assert response.status_code == 403
    assert "CONNECT" in response.json()["error"]
    assert gateway.calls == []

    ok = prod_client.post("/link/token/create", json={"kind": "banking", "consent": "CONNECT"}, headers=ORIGIN)
    assert ok.status_code == 200


def test_production_connect_page_shows_budget_and_warning(prod_client):
    page = prod_client.get("/connect").text
    assert "0 of 3 Production Items used, 3 remaining" in page
    assert "does <strong>not</strong> give the Item back" in page


def test_reconnect_and_remove_via_forms(client, gateway):
    token = client.post("/link/token/create", json={"kind": "banking"}, headers=ORIGIN).json()
    client.post("/link/complete", json={**token, "purpose": "create", "public_token": "p"}, headers=ORIGIN)

    client.post("/items/1/simulate-login-required", headers=ORIGIN)
    assert "Login required" in client.get("/setup").text

    update = client.post("/items/1/link-token", headers=ORIGIN).json()
    gateway.accounts_error = None
    client.post("/link/complete", json={"session_id": update["session_id"], "purpose": "update"}, headers=ORIGIN)
    assert "Login required" not in client.get("/setup").text

    response = client.post("/items/1/remove", data={"typed_name": "First Platypus Bank"},
                           headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303
    assert "Removed connections" in client.get("/setup").text


def test_sync_now_and_transactions_page(client, gateway):
    from tests.fakes import page, txn

    token = client.post("/link/token/create", json={"kind": "banking"}, headers=ORIGIN).json()
    client.post("/link/complete", json={**token, "purpose": "create", "public_token": "p"}, headers=ORIGIN)
    gateway.sync_queue = [page(added=[txn("t1", 4.5), txn("t2", -1200.0, name="Payroll", merchant=None,
                                                         primary="INCOME", pending=True)])]

    response = client.post("/sync", headers=ORIGIN, follow_redirects=False)
    assert "2+new" in response.headers["location"]

    page_html = client.get("/transactions").text
    assert "Fake Coffee Co" in page_html and "$4.50" in page_html
    assert "+$1,200.00" in page_html and "pending" in page_html
    assert ">Food &amp; drink</option>" in page_html and "(Plaid)" not in page_html   # Plaid = default, unlabeled
    assert "history complete" not in client.get("/setup").text        # status line removed (2026-10-14)


def test_networth_page(client, gateway):
    token = client.post("/link/token/create", json={"kind": "banking"}, headers=ORIGIN).json()
    client.post("/link/complete", json={**token, "purpose": "create", "public_token": "p"}, headers=ORIGIN)

    empty = client.get("/networth").text
    assert "History builds from here on" in empty

    client.post("/sync", headers=ORIGIN)
    html = client.get("/networth").text
    assert '<div class="hero-value">-$300</div>' in html   # 110.00 checking - 410.01 card, whole dollars
    assert "Credit cards" in html and "Cash" in html
    # Over time follows the ribbon (2026-10-13): $ mode by default on Net worth = net worth in dollars; % mode = change
    # since the range began (dollars ride along in "net" for the tooltip)
    assert 'id="nw-trend-data"' in html and '"v": -300.01' in html and 'data-unit="value"' in html
    assert 'title="Total value" aria-current="page">$</a>' in html
    pct = client.get("/networth?show=pct").text
    assert '"net": -300.01' in pct and '"v": 0.0' in pct and 'data-unit="pct"' in pct
    assert "Change since last month appears once" not in html     # replaced by the range's % change (2026-10-13)
    assert '<div class="kpi-sub nw-range"><span class="delta-up">▲ 0.0%</span> <span class="muted"><span class="w-long">90 days</span><span class="w-short">90D</span></span></div>' in html
    assert "style=" not in html.split("<main>")[1]  # no inline styles (CSP)


def _seed_two_transactions(client, gateway):
    from tests.fakes import page, txn

    token = client.post("/link/token/create", json={"kind": "banking"}, headers=ORIGIN).json()
    client.post("/link/complete", json={**token, "purpose": "create", "public_token": "p"}, headers=ORIGIN)
    gateway.sync_queue = [page(added=[
        txn("t1", 4.5, day="2026-09-10"),
        txn("t2", 80.0, day="2026-09-12", name="FAKE AIRLINE 123", merchant="Fake Air", primary="TRAVEL"),
    ])]
    client.post("/sync", headers=ORIGIN)


def test_transaction_filters(client, gateway):
    _seed_two_transactions(client, gateway)
    assert 'data-bulk-total="2"' in client.get("/transactions").text
    by_search = client.get("/transactions", params={"q": "airline"}).text
    assert 'data-bulk-total="1"' in by_search
    assert "Fake Air" in by_search and "Fake Coffee Co" not in by_search
    by_category = client.get("/transactions", params={"category": "FOOD_AND_DRINK", "month": "2026-09"}).text
    assert "Fake Coffee Co" in by_category and "Fake Air" not in by_category
    assert "No transactions match." in client.get("/transactions", params={"kind": "income"}).text


def test_recategorize_row_via_htmx_and_spending_page(client, gateway, conn):
    _seed_two_transactions(client, gateway)
    tid = conn.execute("SELECT id FROM transactions WHERE plaid_transaction_id = 't1'").fetchone()[0]
    row = client.post(f"/transactions/{tid}/category", data={"category": "ENTERTAINMENT"}, headers=ORIGIN)
    assert row.status_code == 200 and row.text.strip().startswith(f'<tr id="txn-{tid}"')
    assert "Entertainment" in row.text and "<select" not in row.text      # the row shows text; edits are in its panel

    report = client.get("/spending", params={"month": "2026-09"}).text
    assert '<div class="hero-value">$85</div>' in report               # headline: whole dollars ($84.50)
    assert "Entertainment" in report and "Travel" in report


def test_rule_and_category_forms(client, gateway, conn):
    _seed_two_transactions(client, gateway)
    response = client.post("/rules", data={"cond-0-field": "merchant", "cond-0-op": "contains", "cond-0-value": "fake air",
                                           "act-0-kind": "set_category", "act-0-value": "GENERAL_SERVICES"},
                           headers=ORIGIN, follow_redirects=False)
    assert "It+matches+1+transaction" in response.headers["location"]
    assert conn.execute("SELECT category FROM transactions_effective WHERE plaid_transaction_id = 't2'").fetchone()[0] == "GENERAL_SERVICES"

    client.post("/categories", data={"label": "Fake Hobby", "kind": "expense"}, headers=ORIGIN)
    client.post("/categories/FAKE_HOBBY/kind", data={"kind": "transfer"}, headers=ORIGIN)
    assert conn.execute("SELECT kind FROM categories WHERE key = 'FAKE_HOBBY'").fetchone()[0] == "transfer"
    page_html = client.get("/categories", params={"pattern": "Fake Coffee Co", "category": "FOOD_AND_DRINK"}).text
    assert ">Fake Coffee Co</textarea>" in page_html          # prefilled from a transaction's "rule…" link


def test_revision_rises_on_every_kind_of_change(client, gateway, conn):
    from fintrack import db

    def rev():
        return int(client.get("/revision").text)

    start = rev()
    _seed_two_transactions(client, gateway)                       # connect + sync
    after_sync = rev()
    assert after_sync > start

    client.post("/rules", data={"cond-0-field": "description", "cond-0-op": "contains", "cond-0-value": "fake", "act-0-kind": "set_category", "act-0-value": "TRAVEL"}, headers=ORIGIN)
    after_rule = rev()
    assert after_rule > after_sync

    client.post("/categories/TRANSFER_OUT/kind", data={"kind": "expense"}, headers=ORIGIN)
    after_kind = rev()
    assert after_kind > after_rule

    tid = conn.execute("SELECT id FROM transactions LIMIT 1").fetchone()[0]
    client.post(f"/transactions/{tid}/category", data={"category": "MEDICAL"}, headers=ORIGIN)
    assert rev() > after_kind
    assert rev() == db.revision(conn)


def test_pages_embed_the_revision_they_were_rendered_from(client):
    current = client.get("/revision").text
    for path in ("/spending", "/transactions", "/categories", "/networth", "/investments", "/portfolio", "/setup"):
        # the <body> tag carries the revision (and, on a tab's page, the swipe neighbours: ui.js, 2026-11-11)
        assert f'<body data-revision="{current}"' in client.get(path).text, path


def test_sandbox_only_action_blocked_in_production(prod_client, gateway):
    response = prod_client.post("/items/1/simulate-login-required", headers=ORIGIN, follow_redirects=False)
    assert "Sandbox-only" in response.headers["location"].replace("+", " ")
    assert gateway.calls == []


# ---------------------------------------------------------------- helpers

def test_money_helpers():
    assert to_cents(1.005) == 101
    assert to_cents(-12.34) == -1234
    assert to_cents(None) is None
    assert format_cents(1234567) == "$12,345.67"
    assert format_cents(-500) == "-$5.00"


def test_log_redaction():
    redactor = RedactingFilter(secrets=["super-secret-value"])
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1,
        "token=%s secret=%s public=%s",
        ("access-production-abc123-def", "super-secret-value", "public-sandbox-9f9f"),
        None,
    )
    redactor.filter(record)
    message = record.getMessage()
    assert "abc123" not in message and "super-secret-value" not in message and "9f9f" not in message
    assert "access-production-[REDACTED]" in message


def test_add_or_remove_accounts_button_and_route(client, gateway):
    token = client.post("/link/token/create", json={"kind": "banking"}, headers=ORIGIN).json()
    client.post("/link/complete", json={**token, "purpose": "create", "public_token": "p"}, headers=ORIGIN)
    # the "Add or remove accounts" button: removed 2026-10-14, back on Settings → Setup at the user's request 2026-10-15
    assert 'data-account-select="1" data-item-id="1"' in client.get("/setup").text
    gateway.calls.clear()
    assert client.post("/items/1/link-token?accounts=1", headers=ORIGIN).status_code == 200
    assert gateway.calls[-1][-1] == "account_selection"


def test_rail_icons_and_collapsed_cookie(client):
    expanded = client.get("/setup").text
    assert '<svg class="icon"' in expanded and 'class="shell"' in expanded
    assert 'aria-expanded="true"' in expanded and "Collapse sidebar" in expanded
    client.cookies.set("ft_rail", "collapsed")
    collapsed = client.get("/setup").text
    assert 'class="shell rail-collapsed"' in collapsed and "Expand sidebar" in collapsed
    assert "style=" not in collapsed.split("<body")[1]          # no inline styles (CSP)


def test_sync_button_lives_at_the_bottom_of_the_rail(client):
    page = client.get("/").text
    rail = page.split('<aside class="rail"')[1].split("</aside>")[0]
    assert 'action="/sync"' in rail and "Not synced yet" in rail and 'class="icon"' in rail
    foot = rail.split('class="rail-foot"')[1]
    assert foot.index('action="/sync"') < foot.index("data-rail-toggle")          # sync, then collapse
    tabbar = page.split('<header class="tabbar">')[1].split("</header>")[0]
    assert 'action="/sync"' not in tabbar


def test_projection_controls_live_in_the_ribbon(client):
    page = client.get("/projection").text
    ribbon = page.split('<header class="tabbar">')[1].split("</header>")[0]
    assert "Compare with" in ribbon and 'href="/projection/scenarios/new"' in ribbon
    assert 'class="page-head"' not in page


def test_check_again_lives_in_the_ribbon(client):
    page = client.get("/recurring").text
    ribbon = page.split('<header class="tabbar">')[1].split("</header>")[0]
    assert 'action="/recurring/refresh"' in ribbon and page.count('action="/recurring/refresh"') == 1


def test_the_page_count_is_in_the_pager(client, gateway):
    """User's request 2026-10-31: beside the page links at the bottom, not above the charts."""
    from fintrack.web import transactions as page
    _seed_two_transactions(client, gateway)
    original, page.PAGE_SIZE = page.PAGE_SIZE, 1
    try:
        first = client.get("/transactions").text
        second = client.get("/transactions", params={"page": 2}).text
    finally:
        page.PAGE_SIZE = original
    pager = first.split('<nav class="pager">')[1].split("</nav>")[0]
    assert "Page 1 of 2" in pager and "Older →" in pager and first.count("Page 1 of 2") == 1
    assert "Page 2 of 2" in second.split('<nav class="pager">')[1] and "← Newer" in second


def test_log_files_are_private(tmp_path):
    """fintrack.log and its rotated copies are mode 600; older looser files in the folder are tightened (CLAUDE.md:
    files in FINTRACK_HOME are 600)."""
    import logging
    import stat

    from fintrack.logging_setup import configure_logging

    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "sync-scheduled.log"
    old.write_text("old")
    old.chmod(0o644)
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        configure_logging(logs)
        handler = root.handlers[0]
        logging.getLogger("test").info("hello")
        handler.doRollover()                                    # a rotation opens a new file
        logging.getLogger("test").info("after rotation")
        for path in (logs / "fintrack.log", logs / "fintrack.log.1", old):
            assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers, root.level = saved[0], saved[1]
