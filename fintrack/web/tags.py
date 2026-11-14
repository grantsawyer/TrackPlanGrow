"""Tags page (Planning section, after Budgets since 2026-11-02; user's request 2026-10-28): a card per tag you've made on the
Transactions page, with what its transactions add up to (tags.summaries): spent, received, how many, their dates, and
spending and income by category, largest first, each with its share. Every figure links to those transactions. You
can rename or delete a tag here; tagging itself happens on Transactions (a row's edit panel, or group edit).

Above the cards, a month calendar (user's requests 2026-10-28; `calendar_month`, tags.calendar_items): a tag with its own
Start and End dates (set on its card) is one bar for them, with a single-day bar for each tagged day outside them; a tag
without is a bar per run of consecutive days with its transactions. Packed into lanes week by week. A ribbon toggle picks 1 month, 3 months or 1 year (`?view=month|quarter|year`, CALENDAR_VIEWS; user's request
2026-10-28): one month grid, the three months ending with the anchor month, or the anchor's calendar year as twelve small
grids; arrows step a month, or a year in the 1-year view (`?month=YYYY-MM` = the anchor; default the month of the latest tagged date). Each tag keeps one colour (TAG_SLOTS by creation order; gray after 8).
The calendar's gear popup (user's request 2026-10-29) can hide the tagged days, leaving only tags' own ranges.

The cards are one list, newest first (`card_order`), in their own scrolling area: tags drawn on the calendar's current view are
outlined, and the area scrolls to the first of them (user's request 2026-11-02). The ribbon's + makes a new, empty tag.
"""

from __future__ import annotations

from calendar import Calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from fintrack import calendar_labels, categories, spending, tags
from fintrack.web.deps import get_conn, get_settings, redirect, templates

router = APIRouter()

def countdown(day: str | None, today: date | None = None) -> str:
    """How far off a tag's Start date is, for its card (user's requests 2026-11-04): "21d away" up to 28 days, then weeks
    to 3 months ("7.5w away"), then months ("3.5m away"), weeks and months to the nearest half. "" once it has come."""
    if not day:
        return ""
    days = (date.fromisoformat(day) - (today or date.today())).days
    if days <= 0:
        return ""
    if days <= 28:
        return f"{days}d away"
    months = days / (365.25 / 12)
    value, unit = (months, "m") if months > 3 else (days / 7, "w")
    half = round(value * 2) / 2
    return f"{half:g}{unit} away"


templates.env.globals["countdown"] = countdown


def transactions_url(summary: tags.TagSummary, exact: str = "", **extra: str) -> str:
    """A tag's transactions on the Transactions page, every date it covers (custom range = its first to last day).
    `exact`: the category without its subcategories (Transactions' `only`; named so templates don't read like an
    inline on…= handler to the template scan)."""
    params = {"tag": summary.tag.id, "range": "custom", "from": summary.first or "", "to": summary.last or "", **extra}
    if exact:
        params["only"] = exact
    return "/transactions?" + urlencode(params)


# ---------------------------------------------------------------- the calendar

TAG_SLOTS = 8          # palette slots for tags, in creation order; later tags are gray (dataviz: never cycle colours)


def tag_slots(summaries: list[tags.TagSummary]) -> dict[int, str]:
    """{tag id: its colour class}: the first TAG_SLOTS tags by creation (id) get series 1..8, the rest gray. A tag keeps
    its colour whatever the month or order on the page."""
    ordered = sorted(s.tag.id for s in summaries)
    return {tag_id: (f"tb-{i + 1}" if i < TAG_SLOTS else "tb-other") for i, tag_id in enumerate(ordered)}


@dataclass
class Bar:
    summary: tags.TagSummary
    run: tags.Run          # the days this bar stands for
    url: str               # those days' transactions under the tag
    slot: str
    start: int             # first column (1 = Sunday) in this week
    end: int               # last column (7 = Saturday)
    starts: bool           # the tag's first day is in this week (rounded left end)
    ends: bool             # its last day is in this week (rounded right end)


@dataclass
class Week:
    days: list[dict]                              # {"date", "day", "in_month", "today", "labels", "hidden"}
    lanes: list[list[Bar]] = field(default_factory=list)

    @property
    def blank_lead(self) -> int:
        """How many hidden days start the week (another month's, shown in their own month)."""
        return next((i for i, d in enumerate(self.days) if not d["hidden"]), len(self.days))

    @property
    def blank_tail(self) -> int:
        """How many hidden days end it."""
        return next((i for i, d in enumerate(reversed(self.days)) if not d["hidden"]), len(self.days))


def month_rows(months: list[str], today: date | None = None) -> dict[str, list[list[date]]]:
    """Each month's weeks so the months shown have the same number of rows (user's request 2026-11-02). A month needing 6 drops
    the end row with fewer of its own days (on a tie, the last), e.g. August 2026 drops the row with only Aug 1, as Aug 30-31
    are on the last. A row holding the range's first or last day, or today, is never dropped: if both ends hold one, the month
    keeps 6. February can need only 4 and keeps 4: it isn't filled out with March (user's rule 2026-11-02)."""
    today = today or date.today()
    first = date.fromisoformat(f"{months[0]}-01")
    last = date.fromisoformat(spending.month_bounds(months[-1])[1]) - timedelta(days=1)
    keep = {first, last, today}
    plans = {}
    for m in months:
        year, mon = (int(p) for p in m.split("-"))
        weeks = Calendar(firstweekday=6).monthdatescalendar(year, mon)       # 6 = Sunday
        if len(weeks) == 6:
            own = lambda week: [d for d in week if d.month == mon]          # noqa: E731
            ends = [i for i in (0, 5) if not keep & set(own(weeks[i]))]
            if ends:
                weeks.pop(min(ends, key=lambda i: (len(own(weeks[i])), i == 0)))
        plans[m] = weeks
    return plans


def calendar_month(summaries: list[tags.TagSummary], tag_runs: dict[int, list[tags.Run]], month: str,
                   today: date | None = None, labels: dict | None = None, rows: list[list[date]] | None = None,
                   hide: set[date] | None = None) -> list[Week]:
    """The month's weeks (Sunday first), each with the tags' bars (tags.calendar_items) packed into lanes: a bar goes in
    the first lane where it doesn't overlap one already there (earlier starts first, then longer). `hide`: other months'
    days shown in their own month too (user's request 2026-11-02): blank here, with no bar, label or today on them."""
    hide = hide or set()
    today = today or date.today()
    year, mon = (int(p) for p in month.split("-"))
    slots = tag_slots(summaries)
    spans = sorted(((date.fromisoformat(r.first), date.fromisoformat(r.last), s, r)
                    for s in summaries for r in tag_runs.get(s.tag.id, [])),
                   key=lambda x: (x[0], -(x[1] - x[0]).days, x[2].tag.name.lower()))
    weeks = []
    for days in rows or Calendar(firstweekday=6).monthdatescalendar(year, mon):       # 6 = Sunday; `rows`: month_rows
        week = Week([{"date": d.isoformat(), "day": d.day, "in_month": d.month == mon, "today": d == today and d not in hide,
                      "labels": (labels or {}).get(d.isoformat(), []), "hidden": d in hide}
                     for d in days])
        # hidden days are only ever at a week's ends (another month's), so the days shown are one run
        shown = [d for d in days if d not in hide]
        lo, hi = shown[0], shown[-1]
        lane_ends: list[date] = []
        for first, last, s, run in spans:
            if last < lo or first > hi:
                continue
            start, end = max(first, lo), min(last, hi)
            url = "/transactions?" + urlencode({"tag": s.tag.id, "range": "custom", "from": run.first, "to": run.last})
            bar = Bar(s, run, url, slots[s.tag.id], (start - days[0]).days + 1, (end - days[0]).days + 1,
                      first >= lo, last <= hi)
            lane = next((i for i, e in enumerate(lane_ends) if e < start), None)
            if lane is None:
                lane_ends.append(end)
                week.lanes.append([bar])
            else:
                lane_ends[lane] = end
                week.lanes[lane].append(bar)
        weeks.append(week)
    return weeks


# The calendar's views (user's request 2026-10-28): key -> (toggle label, months the arrows step; the 3-month view moves one
# month at a time, user's request 2026-11-02).
CALENDAR_VIEWS = {"month": ("1 month", 1), "quarter": ("3 months", 1), "year": ("1 year", 12)}


def view_months(view: str, anchor: str) -> list[str]:
    """The months a view shows for an anchor month: itself; the three ending with it; or its calendar year."""
    if view == "quarter":
        return [spending.shift_month(anchor, -2), spending.shift_month(anchor, -1), anchor]
    if view == "year":
        return [f"{anchor[:4]}-{m:02d}" for m in range(1, 13)]
    return [anchor]


def view_title(view: str, months: list[str]) -> str:
    """ "October 2026", "Aug – Oct 2026" (or "Nov 2025 – Jan 2026"), "2026"."""
    from fintrack.web.deps import month_label
    if view == "year":
        return months[0][:4]
    if view == "quarter":
        first, last = months[0], months[-1]
        if first[:4] == last[:4]:
            return f"{month_label(first, 'short')[:3]} – {month_label(last, 'short')}"
        return f"{month_label(first, 'short')} – {month_label(last, 'short')}"
    return month_label(months[0])


def card_order(summaries: list[tags.TagSummary]) -> list[tags.TagSummary]:
    """The cards, newest first (user's request 2026-11-02; replaced Upcoming / Visible / Past): by a tag's End date, else its
    latest tagged date, else its Start. A tag with none of them comes first, so one just made with + is easy to find."""
    def when(s: tags.TagSummary) -> str:
        return s.tag.end_date or s.last or s.tag.start_date or "9999"
    return sorted(summaries, key=when, reverse=True)


def _labels_for(conn, first_month: str, last_month: str) -> dict:
    """Holidays and your labels through the days the first and last grids show of other months."""
    return calendar_labels.day_labels(conn, date.fromisoformat(f"{first_month}-01") - timedelta(days=7),
                                      date.fromisoformat(spending.month_bounds(last_month)[1]) + timedelta(days=7))


COUNTDOWN_GROUPS = ("Events", "Holidays", "Your dates")


def _coming_up(conn, today: date) -> list[dict]:
    """Tags by their Start date from today on, holidays and your labels at their next date, soonest first."""
    coming = [{"key": f"tag:{t.id}", "name": t.name, "date": t.start_date, "group": "Events"}
              for t in tags.all_tags(conn) if t.start_date and t.start_date >= today.isoformat()]
    coming += calendar_labels.upcoming(conn, today)
    order = {group: n for n, group in enumerate(("Events", "Your dates", "Holidays"))}
    coming.sort(key=lambda item: (item["date"], order[item["group"]], item["name"].lower()))
    for item in coming:
        item["days"] = (date.fromisoformat(item["date"]) - today).days
    return coming


def choose_countdown(conn, slot: int, key: str, today: date | None = None) -> None:
    """Point a timer at something coming up, remembering its date; blank = automatic. Something no longer coming up is ignored."""
    if not key:
        tags.set_countdown_choice(conn, slot, "")
        return
    item = next((c for c in _coming_up(conn, today or date.today()) if c["key"] == key), None)
    if item:
        tags.set_countdown_choice(conn, slot, key, item["date"])


def countdowns(conn, today: date) -> tuple[list[dict], dict[str, list[dict]]]:
    """Overview's two timers and what can be chosen for them (user's requests 2026-11-02). A timer follows your choice until its
    date passes, then goes back to automatic, the next thing the other timer isn't showing; so by default they show the next
    two. A holiday or yearly label has passed once its next date is later than the one chosen; a moved tag stays chosen while
    it's still ahead."""
    coming = _coming_up(conn, today)
    by_key = {item["key"]: item for item in coming}

    def chosen_item(slot: int) -> dict | None:
        choice = tags.countdown_choice(conn, slot)
        item = by_key.get(choice[1]) if choice else None
        if item and not item["key"].startswith("tag:") and item["date"] > choice[0]:
            return None
        return item

    chosen = {slot: chosen_item(slot) for slot in tags.COUNTDOWN_SLOTS}
    # a choice for both: the first keeps it, the second goes back to automatic
    if chosen[1] and chosen[2] and chosen[1]["key"] == chosen[2]["key"]:
        chosen[2] = None
    taken = {item["key"] for item in chosen.values() if item}
    timers = []
    for slot in tags.COUNTDOWN_SLOTS:
        item = chosen[slot]
        if item is None:
            item = next((c for c in coming if c["key"] not in taken), None)
            if item:
                taken.add(item["key"])
        if item:
            other = chosen[3 - slot]
            timers.append({"slot": slot, "item": item, "pinned": chosen[slot] is not None,
                           "blocked": other["key"] if other else None})
    return timers, {group: [c for c in coming if c["group"] == group] for group in COUNTDOWN_GROUPS}


def month_card(conn, month: str | None = None, today: date | None = None) -> dict:
    """Overview's calendar card (user's request 2026-11-02): one month, this month by default, and its countdowns."""
    today = today or date.today()
    current = f"{today:%Y-%m}"
    month = spending.parse_month(month) if month else current
    summaries = tags.summaries(conn)
    items = tags.calendar_items(conn, ranges_only=not tags.calendar_shows_days(conn))
    weeks = calendar_month(summaries, items, month, today, labels=_labels_for(conn, month, month),
                           rows=month_rows([month], today)[month])
    timers, choices = countdowns(conn, today)
    return {"month": month, "current": current, "calendar": {"month": month, "weeks": weeks},
            "prev": spending.shift_month(month, -1), "next": spending.shift_month(month, 1),
            "timers": timers, "choices": choices}


def _back(view: str, month: str, edit: str = "") -> dict[str, str]:
    """The query that returns to the same calendar view and period after a POST, and to the card's edit mode when the
    change came from it (user's request 2026-11-04)."""
    return {k: v for k, v in (("view", view if view in CALENDAR_VIEWS and view != "month" else ""), ("month", month),
                              ("edit", edit if edit.isdigit() else "")) if v}


@router.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    summaries = tags.summaries(conn)
    view = request.query_params.get("view") if request.query_params.get("view") in CALENDAR_VIEWS else "month"
    # The calendar's anchor month: ?month=, else the month of the latest tagged date, else this month.
    latest = max((s.last for s in summaries if s.last), default=None)
    month = spending.parse_month(request.query_params.get("month") or (latest[:7] if latest else None))
    show_days = tags.calendar_shows_days(conn)
    items = tags.calendar_items(conn, ranges_only=not show_days)
    months = view_months(view, month)
    # holidays and your labels (user's requests 2026-11-02), through the days the first and last grids show of other months
    labels = _labels_for(conn, months[0], months[-1])
    rows = month_rows(months)
    # each day once (user's request 2026-11-02): another month's day is blank where its own month shows it
    own = {d for m in months for week in rows[m] for d in week if f"{d:%Y-%m}" == m}
    calendars = [{"month": m, "weeks": calendar_month(summaries, items, m, labels=labels, rows=rows[m],
                                                      hide={d for week in rows[m] for d in week if f"{d:%Y-%m}" != m and d in own})}
                 for m in months]
    # the tags drawn on any of these grids: the Visible section
    shown = {b.summary.tag.id for c in calendars for w in c["weeks"] for lane in w.lanes for b in lane}
    slots = tag_slots(summaries)
    step = CALENDAR_VIEWS[view][1]
    return templates.TemplateResponse(request, "tags.html", {
        "settings": settings,
        "summaries": summaries,
        "month": month,
        "view": view,
        "views": CALENDAR_VIEWS,
        "calendars": calendars,
        "calendar_title": view_title(view, months),
        "show_days": show_days,
        "cards": card_order(summaries),
        "on_calendar": shown,        # outlined; ui.js scrolls the list to the first
        # each card in its tag's colour: the swatch before its name and its share bars (user's request 2026-10-28)
        "slots": slots,
        "prev_month": spending.shift_month(month, -step),
        # Today (user's request 2026-11-02): this month; this month and the next 2 (the 3-month view ends with its anchor); this year
        "today_month": (today_anchor := spending.shift_month(f"{date.today():%Y-%m}", 2 if view == "quarter" else 0)),
        "at_today": month == today_anchor if view != "year" else month[:4] == today_anchor[:4],
        "next_month": spending.shift_month(month, step),
        "transactions_url": transactions_url,
        "budget_status": tags.budget_status,
        "color_palette": tags.COLOR_PALETTE,
        "color_shades": tags.SHADES,
        "custom_colors": tags.custom_colors(conn),
        "spending_categories": [c for c in categories.list_categories(conn) if c["group_kind"] == "expense"],
        "msg": request.query_params.get("msg"),
        "error": request.query_params.get("error"),
        # the card in edit mode (its pencil; user's request 2026-11-04)
        "edit_id": int(request.query_params["edit"]) if request.query_params.get("edit", "").isdigit() else None,
        # a category budget waiting for your yes to raise the tag's total (user's request 2026-11-04)
        "raise_prompt": _raise_prompt(request.query_params, categories.list_categories(conn)),
    })


def _raise_prompt(q, cats) -> dict | None:
    """The card's question from the query set_tag_category_budget sends back, or None."""
    if not (q.get("raise_tag", "").isdigit() and q.get("raise_needed", "").isdigit() and q.get("raise_total", "").isdigit()):
        return None
    labels = {c["key"]: c["label"] for c in cats}
    return {"tag_id": int(q["raise_tag"]), "category": q.get("raise_category", ""), "amount": q.get("raise_amount", ""),
            "label": labels.get(q.get("raise_category", ""), q.get("raise_category", "")),
            "needed": int(q["raise_needed"]), "total": int(q["raise_total"])}


@router.post("/tags/new")
def new_tag(name: str = Form(""), month: str = Form(""), view: str = Form(""), conn=Depends(get_conn)):
    """A new tag from the ribbon's + (user's request 2026-10-29): no transactions yet; set its dates on its card, tag
    transactions on Transactions."""
    try:
        tag = tags.create(conn, name)
    except tags.TagError as exc:
        return redirect("/tags", **_back(view, month), error=str(exc))
    return redirect("/tags", **_back(view, month), msg=f"Added {tag.name}.")


@router.post("/tags/settings")
def calendar_settings(show_days: str = Form("0"), month: str = Form(""), view: str = Form(""), conn=Depends(get_conn)):
    """The calendar's gear popup: show the days with tagged transactions, or only tags' own ranges. A hidden 0 comes
    before the checkbox's 1, so unticking sends 0 (the last value wins)."""
    tags.set_calendar_shows_days(conn, show_days == "1")
    return redirect("/tags", **_back(view, month))


@router.post("/tags/{tag_id}/rename")
def rename_tag(tag_id: int, name: str = Form(""), month: str = Form(""), view: str = Form(""), edit: str = Form(""),
               conn=Depends(get_conn)):
    """The name, from its card's edit mode (saved on leaving the box)."""
    keep = _back(view, month, edit)
    try:
        tags.rename(conn, tag_id, name)
    except tags.TagError as exc:
        return redirect("/tags", **keep, error=str(exc))
    return redirect("/tags", **keep)


@router.post("/tags/{tag_id}/dates")
def set_tag_dates(tag_id: int, start: str = Form(""), end: str = Form(""), month: str = Form(""), view: str = Form(""),
                  edit: str = Form(""), conn=Depends(get_conn)):
    """A tag's Start and End dates, from its card; back to the same calendar view and period."""
    keep = _back(view, month, edit)
    try:
        tags.set_dates(conn, tag_id, start, end)
    except tags.TagError as exc:
        return redirect("/tags", **keep, error=str(exc))
    return redirect("/tags", **keep)


def _after_label(back: str, view: str, month: str, **extra):
    """Back to where the day was clicked: Overview's card in the same month, or the Tags page."""
    if back == "overview":
        return redirect("/", cal=month, **extra)
    return redirect("/tags", **_back(view, month), **extra)


@router.post("/tags/labels")
def add_day_label(date_: str = Form("", alias="date"), label: str = Form(""), yearly: str = Form(""), month: str = Form(""),
                  view: str = Form(""), back: str = Form(""), conn=Depends(get_conn)):
    """Your own label on a calendar day (user's request 2026-11-02), one day or every year."""
    try:
        calendar_labels.add(conn, date_, label, yearly == "1")
    except calendar_labels.LabelError as exc:
        return _after_label(back, view, month, error=str(exc))
    return _after_label(back, view, month)


@router.post("/tags/labels/{label_id}/delete")
def delete_day_label(label_id: int, month: str = Form(""), view: str = Form(""), back: str = Form(""), conn=Depends(get_conn)):
    calendar_labels.remove(conn, label_id)
    return _after_label(back, view, month)


@router.post("/tags/colors/remove")
def remove_custom_color(remove: str = Form(""), month: str = Form(""), view: str = Form(""), conn=Depends(get_conn)):
    """Take one of your colors off the list (its × in the picker). Tags using it keep it."""
    tags.remove_custom_color(conn, remove)
    return redirect("/tags", **_back(view, month))


@router.post("/tags/{tag_id}/color")
def set_tag_color(tag_id: int, color: str = Form(""), custom: str = Form(""), month: str = Form(""), view: str = Form(""),
                  conn=Depends(get_conn)):
    """A tag's color, from the picker on its card (user's request 2026-11-02): a listed one, "" = automatic, or `custom`:
    a new one, which joins the list."""
    keep = _back(view, month)
    try:
        if custom:
            tags.add_custom_color(conn, tag_id, custom)
        else:
            tags.set_color(conn, tag_id, color)
    except tags.TagError as exc:
        return redirect("/tags", **keep, error=str(exc))
    return redirect("/tags", **keep)


@router.post("/tags/{tag_id}/budget")
def set_tag_budget(tag_id: int, amount: str = Form(""), month: str = Form(""), view: str = Form(""), edit: str = Form(""),
                   conn=Depends(get_conn)):
    """A tag's total budget, from its card (user's request 2026-11-02); blank = none."""
    keep = _back(view, month, edit)
    try:
        tags.set_budget(conn, tag_id, amount)
    except tags.TagError as exc:
        return redirect("/tags", **keep, error=str(exc))
    return redirect("/tags", **keep)


@router.post("/tags/{tag_id}/category-budget")
def set_tag_category_budget(tag_id: int, category: str = Form(""), amount: str = Form(""), month: str = Form(""),
                            view: str = Form(""), raise_total: str = Form("", alias="raise"), edit: str = Form(""),
                            conn=Depends(get_conn)):
    """A tag's budget for one category, from its card; blank removes it. One that takes the category budgets past the
    total comes back to the card to ask first (user's request 2026-11-04): `raise`=1 is your yes, raising the total."""
    keep = _back(view, month, edit)
    try:
        tags.set_category_budget(conn, tag_id, category, amount, raise_total=raise_total == "1")
    except tags.BudgetTooSmall as exc:
        return redirect("/tags", **keep, raise_tag=str(tag_id), raise_category=category, raise_amount=amount,
                        raise_needed=str(exc.needed), raise_total=str(exc.total))
    except tags.TagError as exc:
        return redirect("/tags", **keep, error=str(exc))
    return redirect("/tags", **keep)


@router.post("/tags/{tag_id}/delete")
def delete_tag(tag_id: int, conn=Depends(get_conn)):
    """Delete a tag: its transactions lose it, nothing else changes."""
    tags.delete(conn, tag_id)
    return redirect("/tags", msg="Tag deleted.")
