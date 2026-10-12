"""Crypto OHLCV via ccxt public endpoints. No API key required for market data."""

import ccxt

# 2026-10-12 audit finding #8: every track watching the same symbol (e.g. 9
# different BTC tracks: momentum/regime x 1x/2x/10x) called fetch_series
# independently, each its own network round trip, each a few seconds apart from
# the others -- wasteful, and (before finding #7's in-progress-candle fix) a real
# source of ticks disagreeing on what "the latest price" even was. This cache is
# scoped to one process run (one `scheduler.py --once` tick): cleared explicitly
# via clear_cache() at the start of each tick (see scheduler.py), so every track
# sharing a symbol within that tick gets the exact same fetch, and the next tick
# always fetches fresh data again. NOT applied to Polymarket (prediction_source.py)
# -- there, which market a track is even looking at can legitimately differ
# per-track now (finding #1: a track holding a position must not rotate away from
# its market even while a flat track on the "same" slot rotates to a new one), so
# a shared cache would be actively wrong there, not just unnecessary.
_cache = {}


def clear_cache():
    _cache.clear()


def fetch_series(symbol: str, exchange_id: str = "kraken", timeframe: str = "15m", limit: int = 60, include_low: bool = False):
    """Returns a list of closing prices, oldest first, and the last price (plus
    the latest closed candle's low when include_low=True -- see below).

    2026-10-12 audit finding #7: the exchange's most recent candle is usually
    still in progress (its close is just "whatever the price is right now"),
    so two ticks of the same strategy a few seconds apart could read different
    "last closed" values and disagree. Dropping any candle that hasn't finished
    its interval yet also makes live results comparable to a backtest, which can
    only ever see closed bars.

    include_low=True (used by leveraged tracks, finding #12) also returns the
    latest closed candle's low -- the worst price reached since the previous
    tick -- so a liquidation check can catch an intrabar breach that recovered
    by the close instead of only ever checking the close price."""
    closes, last_price, last_low = _fetch_raw(symbol, exchange_id, timeframe, limit)
    if include_low:
        return closes, last_price, last_low
    return closes, last_price


def _fetch_raw(symbol: str, exchange_id: str, timeframe: str, limit: int):
    cache_key = (exchange_id, symbol, timeframe, limit)
    if cache_key in _cache:
        return _cache[cache_key]

    exchange_cls = getattr(ccxt, exchange_id)
    exchange = exchange_cls({"enableRateLimit": True})
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    duration_ms = exchange.parse_timeframe(timeframe) * 1000
    now_ms = exchange.milliseconds()
    # an in-progress candle can come back with a null close on some exchanges;
    # candle[0] is the candle's open time in ms, so it's only closed once a full
    # interval has elapsed since then.
    closed_candles = [
        candle
        for candle in ohlcv
        if candle[4] is not None and candle[0] + duration_ms <= now_ms
    ]
    if not closed_candles:
        raise RuntimeError(f"No usable closed candles returned for {symbol} on {exchange_id}")
    closes = [candle[4] for candle in closed_candles]
    result = (closes, closes[-1], closed_candles[-1][3])
    _cache[cache_key] = result
    return result
