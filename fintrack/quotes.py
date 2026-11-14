"""Stock prices from Alpaca: the only module that calls Alpaca (user's decision 2026-10-07,
replacing Finnhub, which was added 2026-10-04).

What Alpaca sees: the ticker symbols looked up and your API keys, nothing else (no amounts,
quantities, accounts, or names). The keys travel in request headers, never in a URL.

Verified against Alpaca's docs and a live test with a free "Paper Only" account, 2026-10-07
(docs.alpaca.markets: stock snapshots, historical bars; alpaca.markets/data):

* GET https://data.alpaca.markets/v2/stocks/snapshots?symbols=A,B,...&feed=...
  One call prices many tickers: latestTrade, minuteBar, dailyBar, prevDailyBar per symbol.
  Free plan: feed=iex is real time (one exchange); feed=delayed_sip is every US exchange,
  15 minutes delayed. In the test, delayed_sip's daily closes matched the official closes
  exactly; IEX prices were within ~0.02% (median). Symbols it can't price (mutual funds) are
  simply missing from the answer.
* GET https://data.alpaca.markets/v2/stocks/bars?symbols=...&timeframe=1Day&feed=sip
  Daily prices back to 2016, many symbols per call (up to 10,000 bars a page, then
  page_token). The free plan refuses SIP data from the latest 15 minutes, so `end` is always
  at least 16 minutes ago.
* The same bars endpoint with timeframe=1Min gives minute prices for the Investments chart's "1 day"
  range (user's request 2026-10-27; `intraday`): the all-exchange feed up to 16 minutes ago, then the
  real-time IEX feed for the latest minutes. A minute without trades has no bar.
* GET https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity (user's decision 2026-11-04, for
  the watchlist's company search; docs.alpaca.markets/reference/get-v2-assets-1, checked with the user's paper keys the
  same day: 0.7 s, 6.5 MB, 14,395 assets with symbol, name, exchange, tradable): every active US stock and ETF, fetched
  about weekly (`assets`) and searched locally, so typing sends nothing. The trading API's paper host, which serves
  "Paper Only" keys; the same headers.
* Free plan: 200 calls a minute. fintrack uses 2 per quote refresh and 2 per minute-price refresh
  (each cached 5 minutes).

Live price rule: whichever is newest of the IEX last trade (real time) and the all-exchange
daily close so far (15 minutes behind). After the close that's the official close; during the
day usually IEX; for a thinly traded stock the all-exchange price. Change = vs the official
previous close.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, time as clock, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

DATA_URL = "https://data.alpaca.markets/v2/stocks"
ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets"   # the company list (watchlist search, 2026-11-04)
ASSETS_TIMEOUT_SECONDS = 30  # one ~6.5 MB answer, about weekly
CACHE_SECONDS = 300          # a quote is reused for 5 minutes
TIMEOUT_SECONDS = 6          # one request
TOTAL_SECONDS = 6            # a refresh's two calls run side by side: a page never waits longer
SYMBOLS_PER_CALL = 100       # keeps URLs short; fintrack holds far fewer
SIP_DELAY = timedelta(minutes=16)   # free plan: no all-exchange data from the latest 15 minutes
MARKET_ZONE = ZoneInfo("America/New_York")
SESSION = (clock(9, 30), clock(16, 0))   # regular trading hours (an early close just has no later bars)


@dataclass(frozen=True)
class Quote:
    price: float
    change_pct: float | None       # 0.012 = +1.2% vs the previous close
    previous_close: float | None
    as_of: int | None              # unix seconds of the price used
    fetched_at: float | None = None   # unix seconds when fintrack asked Alpaca (kept through the cache)


def _http_get(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers)
    timeout = ASSETS_TIMEOUT_SECONDS if url.startswith(ASSETS_URL) else TIMEOUT_SECONDS
    with urllib.request.urlopen(request, timeout=timeout) as response:   # noqa: S310 (fixed https URL)
        return json.loads(response.read().decode("utf-8"))


def _when(stamp: str | None) -> datetime | None:
    """Alpaca's RFC-3339 times carry nanoseconds; Python keeps microseconds."""
    if not stamp:
        return None
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$", stamp)
    if not m:
        return None
    fraction = "." + m.group(2)[:6] if m.group(2) else ""
    zone = "+00:00" if m.group(3) == "Z" else m.group(3)
    return datetime.fromisoformat(m.group(1) + fraction + zone)


def _quote(iex: dict | None, sip: dict | None) -> Quote | None:
    """One symbol's Quote from its IEX and delayed all-exchange snapshots (see module doc)."""
    candidates = []                                   # (time, price)
    trade = (iex or {}).get("latestTrade") or {}
    if trade.get("p"):
        candidates.append((_when(trade.get("t")), float(trade["p"])))
    daily, minute = (sip or {}).get("dailyBar") or {}, (sip or {}).get("minuteBar") or {}
    if daily.get("c"):
        # The daily bar's close is current as of the latest minute bar's end.
        stamp = _when(minute.get("t")) or _when(daily.get("t"))
        candidates.append(((stamp + timedelta(minutes=1)) if stamp else None, float(daily["c"])))
    candidates = [c for c in candidates if c[0] is not None]
    if not candidates:
        return None
    # Newest wins; a tie (after the close) goes to the all-exchange official close.
    when, price = max(candidates, key=lambda c: (c[0], candidates.index(c)))
    previous = ((sip or {}).get("prevDailyBar") or {}).get("c") or ((iex or {}).get("prevDailyBar") or {}).get("c")
    previous = float(previous) if previous else None
    return Quote(price=price, change_pct=price / previous - 1 if previous else None,
                 previous_close=previous, as_of=int(when.timestamp()))


class QuoteGateway:
    """Fetch quotes and daily price history. `fetch` is injectable so tests never touch the network."""

    _cache: dict[str, tuple[float, Quote | None]] = {}
    _minutes: dict[tuple, tuple[float, dict]] = {}    # (day, tickers) -> (fetched, intraday answer)
    _lock = threading.Lock()

    def __init__(self, key_id: str, secret_key: str,
                 fetch: Callable[[str, dict[str, str]], dict] = _http_get, total_seconds: float = TOTAL_SECONDS):
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self._fetch = fetch
        self._total_seconds = total_seconds

    def _get(self, path: str, params: dict) -> dict | None:
        url = f"{DATA_URL}/{path}?" + urllib.parse.urlencode(params)
        try:
            return self._fetch(url, self._headers)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            # The keys are in headers, not the URL; the error type is enough either way.
            log.warning("Alpaca %s unavailable (%s)", path, type(exc).__name__)
            return None

    def assets(self) -> list[tuple[str, str, str]] | None:
        """Every active, tradable US stock and ETF as (symbol, name, exchange), for the watchlist's search (see module
        doc). None when Alpaca can't be reached."""
        url = f"{ASSETS_URL}?" + urllib.parse.urlencode({"status": "active", "asset_class": "us_equity"})
        try:
            answer = self._fetch(url, self._headers)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            log.warning("Alpaca assets unavailable (%s)", type(exc).__name__)
            return None
        if not isinstance(answer, list):
            return None
        return [(a["symbol"], a.get("name") or "", a.get("exchange") or "") for a in answer
                if a.get("symbol") and a.get("tradable") and a.get("status", "active") == "active"]

    def _snapshots(self, symbols: list[str], feed: str) -> dict:
        found: dict = {}
        for i in range(0, len(symbols), SYMBOLS_PER_CALL):
            answer = self._get("snapshots", {"symbols": ",".join(symbols[i:i + SYMBOLS_PER_CALL]), "feed": feed})
            found.update({s: v for s, v in (answer or {}).items() if v})
        return found

    def quotes(self, tickers: list[str]) -> dict[str, Quote]:
        """{ticker: Quote} for the tickers Alpaca could price; cached for CACHE_SECONDS."""
        now = time.time()
        wanted = sorted({t for t in tickers if t})
        with self._lock:
            fresh = {t: self._cache[t][1] for t in wanted
                     if t in self._cache and now - self._cache[t][0] < CACHE_SECONDS}
        missing = [t for t in wanted if t not in fresh]
        if missing:
            # Both feeds at once, waiting at most TOTAL_SECONDS. A feed that hasn't answered by
            # then counts as no data; its symbols are cached as "no quote" like any failure, so a
            # hung Alpaca costs one slow page per CACHE_SECONDS. The thread ends on its own timeout.
            pool = ThreadPoolExecutor(max_workers=2)
            try:
                futures = {pool.submit(self._snapshots, missing, feed): feed for feed in ("iex", "delayed_sip")}
                done, late = wait(futures, timeout=self._total_seconds)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            if late:
                log.warning("Alpaca: %s took too long; using Plaid prices where needed",
                            ", ".join(futures[f] for f in late))
            answers = {futures[f]: f.result() for f in done}
            iex, sip = answers.get("iex", {}), answers.get("delayed_sip", {})
            fetched = {}
            for t in missing:
                q = _quote(iex.get(t), sip.get(t))
                fetched[t] = Quote(q.price, q.change_pct, q.previous_close, q.as_of, fetched_at=now) if q else None
            with self._lock:
                for t, q in fetched.items():
                    self._cache[t] = (now, q)     # failures too: retried after CACHE_SECONDS
            fresh.update(fetched)
        return {t: q for t, q in fresh.items() if q is not None}

    def history(self, tickers: list[str], start: date) -> dict[str, list[tuple[str, float]]]:
        """Daily closes since `start` (every US exchange, split-adjusted so a split doesn't look
        like a crash; not dividend-adjusted, so they match the prices Plaid reports):
        {ticker: [("2026-10-06", 101.5), ...]} oldest first. Missing tickers are left out."""
        wanted = sorted({t for t in tickers if t})
        end = (datetime.now(timezone.utc) - SIP_DELAY).strftime("%Y-%m-%dT%H:%M:%SZ")
        bars = self._bars(wanted, {"timeframe": "1Day", "start": start.isoformat(), "end": end,
                                   "adjustment": "split", "feed": "sip"})
        return {symbol: [(b["t"][:10], float(b["c"])) for b in rows] for symbol, rows in bars.items()}

    def _bars(self, symbols: list[str], params: dict) -> dict[str, list[dict]]:
        """{symbol: [bar, ...]} oldest first from /bars, every page, bars without a close left out."""
        found: dict[str, list[dict]] = {}
        for i in range(0, len(symbols), SYMBOLS_PER_CALL):
            page = {**params, "symbols": ",".join(symbols[i:i + SYMBOLS_PER_CALL]), "limit": 10000}
            while True:
                answer = self._get("bars", page)
                if not answer:
                    break
                for symbol, bars in (answer.get("bars") or {}).items():
                    found.setdefault(symbol, []).extend(b for b in bars if b.get("c"))
                if not answer.get("next_page_token"):
                    break
                page = {**page, "page_token": answer["next_page_token"]}
        return found

    def intraday(self, tickers: list[str], day: date, now: datetime | None = None) -> dict[str, list[tuple[int, float]]]:
        """Minute closes during `day`'s regular session (the Investments chart's "1 day"):
        {ticker: [(unix seconds of the minute, close), ...]} oldest first. All exchanges up to
        SIP_DELAY ago, then IEX's real-time bars after the last of those. Cached CACHE_SECONDS."""
        now = now or datetime.now(timezone.utc)
        wanted = tuple(sorted({t for t in tickers if t}))
        key = (day.isoformat(), wanted)
        with self._lock:
            hit = self._minutes.get(key)
            if hit and time.time() - hit[0] < CACHE_SECONDS:
                return hit[1]
        opens, closes = (datetime.combine(day, t, MARKET_ZONE).astimezone(timezone.utc) for t in SESSION)
        stamp = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")     # noqa: E731
        sip_end, end = min(closes, now - SIP_DELAY), min(closes, now)
        windows = {}                                            # feed -> (start, end)
        if wanted and sip_end > opens:
            windows["sip"] = (opens, sip_end)
        if wanted and end > max(opens, sip_end):
            windows["iex"] = (max(opens, sip_end), end)
        answers = {}
        if windows:
            # Both feeds side by side, at most TOTAL_SECONDS (as in `quotes`).
            pool = ThreadPoolExecutor(max_workers=2)
            try:
                futures = {pool.submit(self._bars, list(wanted), {"timeframe": "1Min", "start": stamp(a), "end": stamp(b),
                                                                  "feed": feed}): feed
                           for feed, (a, b) in windows.items()}
                done, late = wait(futures, timeout=self._total_seconds)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            if late:
                log.warning("Alpaca: minute prices (%s) took too long", ", ".join(futures[f] for f in late))
            answers = {futures[f]: f.result() for f in done}
        minutes: dict[str, list[tuple[int, float]]] = {}
        for feed in ("sip", "iex"):                             # IEX only adds minutes after the last SIP one
            for symbol, bars in answers.get(feed, {}).items():
                rows = minutes.setdefault(symbol, [])
                for b in bars:
                    when = _when(b.get("t"))
                    if when is not None and (not rows or int(when.timestamp()) > rows[-1][0]):
                        rows.append((int(when.timestamp()), float(b["c"])))
        minutes = {symbol: rows for symbol, rows in minutes.items() if rows}
        with self._lock:
            self._minutes[key] = (time.time(), minutes)
        return minutes

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._cache.clear()
            cls._minutes.clear()
