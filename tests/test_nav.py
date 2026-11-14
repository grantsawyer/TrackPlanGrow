"""Navigation (web/deps.nav_for): the pages beside a tab's page, for a phone's sideways swipe (ui.js, 2026-11-11)."""
from fintrack.web.deps import NAV, nav_for
from tests.test_remote import ready, remote_settings  # noqa: F401 (fixtures)


def test_a_tabs_page_knows_the_pages_beside_it_in_the_bottom_bars_order():
    order = [href for _, tabs in NAV for _, href in tabs]
    tags = nav_for("/tags")
    assert (tags["prev"], tags["next"]) == ("/budgets", "/recurring")          # within its section
    last_of_section = NAV[3][1][-1][1]                                         # Investments' last tab
    assert nav_for(last_of_section)["next"] == NAV[4][1][0][1]                 # on into the next section
    assert nav_for(order[0])["prev"] is None and nav_for(order[-1])["next"] is None


def test_pages_under_a_tab_get_no_swipe():
    """An account's page or a rule's form: a swipe there won't jump to another section."""
    for path in ("/accounts/3", "/rules/new", "/manual/new"):
        nav = nav_for(path)
        assert nav["prev"] is None and nav["next"] is None


def test_the_page_carries_them(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.web.app import create_app
    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway), base_url="https://localhost:8443")
    page = client.get("/tags").text
    assert 'data-swipe-prev="/budgets"' in page and 'data-swipe-next="/recurring"' in page



def test_only_phones_pages_may_frame_the_apps_own_pages(ready, conn, cipher, gateway):
    """A swipe shows the page beside in a frame (ui.js): phones' pages allow frames of this app only; the Mac's none."""
    from tests.test_remote import _client, sign_in
    phone = _client(ready, cipher, gateway, phone=True)
    sign_in(phone)
    csp = phone.get("/tags").headers["content-security-policy"]
    assert "frame-ancestors 'self'" in csp and "frame-src 'self' https://cdn.plaid.com/" in csp
    mac = _client(ready, cipher, gateway, phone=False).get("/tags").headers["content-security-policy"]
    assert "frame-ancestors 'none'" in mac and "frame-src https://cdn.plaid.com/" in mac


def test_a_swipe_into_another_section_knows_its_tabs():
    """The tabs line that slides in with a swipe into another section (ui.js): that section's tabs, the new page marked."""
    within = nav_for("/tags")
    assert within["next_tabs"] is None and within["prev_tabs"] is None           # same section: the mark moves instead
    last = NAV[3][1][-1][1]                                                     # Investments' last tab
    into = nav_for(last)["next_tabs"]
    assert [t["href"] for t in into] == [h for _, h in NAV[4][1]] and into[0]["active"]
    assert sum(t["active"] for t in into) == 1


def test_the_summary_as_a_balance():
    """Transactions' Summary (web/transaction_filters._balance, 2026-11-11): each group's net on its side, two bars on
    one scale, the dashed net completing the shorter one. Made-up figures."""
    from fintrack.web.transaction_filters import _balance

    def g(kind, net, count=1):
        return {"kind": kind, "label": kind.title(), "url": "/x", "count": count, "net_cents": net}
    every = {"count": 6, "net_cents": 1000_00 - 600_00 - 500_00}
    b = _balance([every, g("income", 1000_00), g("expense", -600_00), g("transfer", -500_00)])
    assert (b["in_cents"], b["out_cents"], b["gap_on"]) == (1000_00, 1100_00, "in")      # 100 short: dashed on "in"
    assert [s["kind"] for s in b["came_in"]] == ["income"] and [s["kind"] for s in b["went_out"]] == ["expense", "transfer"]
    assert abs(b["went_out"][0]["share"] - 600 / 1100) < 1e-9 and abs(b["gap_share"] - 100 / 1100) < 1e-9
    key = {k["kind"]: k["of_side"] for k in b["key"]}
    assert key == {"income": None, "expense": 55, "transfer": 45}                         # alone on its side: no 100%
    # more came in than went out: the dashed piece is on "out" (what was kept); a $0 group listed, no share
    b = _balance([{"count": 3, "net_cents": 200_00}, g("income", 500_00), g("expense", -300_00), g("transfer", 0)])
    assert b["gap_on"] == "out" and [k["of_side"] for k in b["key"]] == [None, None, None]
