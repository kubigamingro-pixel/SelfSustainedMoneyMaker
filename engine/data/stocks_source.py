"""Stock OHLCV via yfinance. No API key required."""

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import yfinance as yf

NY_TZ = ZoneInfo("America/New_York")

_INTERVAL_RE = re.compile(r"^(\d+)([mhd])$")
_INTERVAL_UNITS = {"m": "minutes", "h": "hours", "d": "days"}

# 2026-10-12 audit finding #8: see crypto_source.py's _cache for the full
# rationale -- one real fetch per symbol per tick, shared by every track
# watching it, cleared at the start of each tick (scheduler.py).
_cache = {}


def clear_cache():
    _cache.clear()


def market_is_open(now=None) -> bool:
    """Rough NYSE regular-hours check: Mon-Fri, 09:30-16:00 America/New_York.
    Does not account for market holidays -- a closed-market tick will simply
    fail to find fresh data and no-op, which is an acceptable approximation."""
    now = now or datetime.now(NY_TZ)
    if now.weekday() >= 5:
        return False
    return time(9, 30) <= now.time() <= time(16, 0)


def _interval_to_timedelta(interval: str) -> timedelta:
    match = _INTERVAL_RE.match(interval)
    if not match:
        raise ValueError(f"unsupported interval format: {interval!r}")
    amount, unit = match.groups()
    return timedelta(**{_INTERVAL_UNITS[unit]: int(amount)})


def fetch_series(symbol: str, period: str = "5d", interval: str = "15m", include_low: bool = False):
    """Returns a list of closing prices, oldest first, and the last price (plus
    the latest closed bar's low when include_low=True -- see crypto_source's
    docstring for why, finding #12).

    2026-10-12 audit finding #7: yfinance's most recent bar is usually still
    in progress during market hours (its close is just the current price), so
    two ticks a few seconds apart could read different "last closed" values.
    Drop it unless a full interval has actually elapsed since it opened."""
    closes, last_price, last_low = _fetch_raw(symbol, period, interval)
    if include_low:
        return closes, last_price, last_low
    return closes, last_price


def _fetch_raw(symbol: str, period: str, interval: str):
    cache_key = (symbol, period, interval)
    if cache_key in _cache:
        return _cache[cache_key]

    hist = yf.Ticker(symbol).history(period=period, interval=interval)
    valid = hist["Close"].notna()
    hist = hist[valid]
    if hist.empty:
        raise RuntimeError(f"No price data returned for {symbol}")

    bar_len = _interval_to_timedelta(interval)
    now = datetime.now(timezone.utc)
    last_ts = hist.index[-1]
    last_ts_utc = last_ts.tz_convert("UTC") if last_ts.tzinfo else last_ts.tz_localize("UTC")
    if last_ts_utc.to_pydatetime() + bar_len > now:
        hist = hist.iloc[:-1]

    closes = hist["Close"].tolist()
    if not closes:
        raise RuntimeError(f"No closed price bars returned for {symbol}")
    result = (closes, closes[-1], float(hist["Low"].iloc[-1]))
    _cache[cache_key] = result
    return result
