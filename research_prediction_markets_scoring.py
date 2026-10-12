"""Extends this project's rigorous strategy-search methodology to prediction
markets (Polymarket), the 3rd and last of the 7 live track families (crypto
and stocks already tested -- see
audits/2026-10-12-full-strategy-search-audit-dossier.md,
audits/2026-10-12-sol-xrp-ada-1h-scoring-report.md, and
audits/2026-10-12-stocks-scoring-report.md).

Why this needs a different design than crypto/stocks: the live
prediction_markets_momentum track's own data source
(engine/data/prediction_source.py) documents that Polymarket's Gamma API
"only exposes the current price, not a rolling history" -- the live system
builds its own history by polling and persisting prices tick by tick. That
means the only "real" history available locally is as long as the live
track has been running (currently ~1 week after a recent full reset -- see
audits/2026-10-12-sol-xrp-ada-1h-scoring-report.md's discussion of why that
wasn't enough for a meaningful test).

This script instead pulls directly from Polymarket's CLOB `prices-history`
endpoint, which DOES return a real tick-level history -- but only for
markets that are still active; a resolved/closed market's CLOB history
returns empty (confirmed by direct testing: multiple high-volume historical
election/COVID markets from 2020-2021 and 2024-2025 all returned `{"history":
[]}`, while a currently-active NBA spread market returned 4,317 points
spanning its full ~1-month lifetime so far). So unlike crypto (years of one
continuous asset) or stocks (a decade of one continuous asset), prediction
markets here means: many short-lived, mostly-unrelated, currently-active
markets, each contributing its own available history to date -- an episode
design, not one continuous series.

Methodology, decided before running:
- Pull the ~20 highest-24h-volume currently active markets from Gamma.
- Fetch each market's full available CLOB price history (interval=max).
- Keep only episodes with at least 100 ticks (the minimum needed for the
  slowest frozen strategy, macd_crossover, to produce any signal at all).
- Invents NO new strategies: runs research_harness.ALL_STRATEGIES exactly
  as already frozen in PREREGISTRATION.md, same pattern as the SOL/XRP/ADA
  and stocks rounds.
- Each (strategy, episode) cell is run independently via run_cell (markets
  are different topics/timeframes, so they are NOT concatenated into one
  series -- that would create fake continuity at episode boundaries).
- Per strategy, the per-episode p-values are combined into ONE overall
  p-value via Fisher's method (same method already used in this project to
  combine the SOL/XRP/ADA held-out p-values) -- episodes here are much more
  independent of each other than crypto assets are, since they're mostly
  unrelated real-world events, so Fisher's method is on firmer ground here
  than it was for SOL/XRP/ADA.
- The pass gate, decided before running: a strategy passes if its
  Fisher-combined p-value < 0.10 AND its total summed round-trip return,
  pooled across all episodes, is positive.
- Correction across strategies: Benjamini-Hochberg at q=0.10 across the 6
  frozen strategies' combined p-values (not across individual episodes,
  since those are already pooled per strategy above).

Does NOT modify research_harness.py, backtest_all.py, or PREREGISTRATION.md.
Uses _local_dns_workaround.py to route around a local DNS resolver issue on
this machine (gamma-api.polymarket.com / clob.polymarket.com fail to
resolve via the default resolver but resolve fine via public DNS -- see that
file's docstring; this does not weaken TLS certificate validation, it only
substitutes a working DNS answer for a locally broken one, and is not used
by any live engine code, which runs on GitHub Actions where this isn't an
issue).
"""

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _local_dns_workaround
_local_dns_workaround.apply()

import requests

from research_harness import ALL_STRATEGIES, run_cell, passes, PASS_P_THRESHOLD
from engine.config import load_tracks_config

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
CLOB_PRICES_HISTORY_URL = "https://clob.polymarket.com/prices-history"
DATASET_DIR = Path(__file__).resolve().parent / "audits" / "2026-10-12-prediction-markets-research-datasets"
DATASET_DIR.mkdir(parents=True, exist_ok=True)

N_MARKETS = 20
N_CANDIDATES = 150
MIN_TICKS = 100


def fetch_active_markets(limit=N_CANDIDATES):
    """Candidate pool: the Gamma API's default (unsorted) `closed=false`
    listing is dominated by long-horizon political/macro futures markets
    (confirmed directly: the first 1,000 unsorted active markets contained
    zero markets matching a sports-game text pattern). Explicitly sorting by
    `order=volume24hr&ascending=false` surfaces real, currently-live sports
    games and other short-horizon markets with genuine price movement
    (confirmed directly: this ordering's top 30 included NFL spreads, MLB
    games, European football). volume24hr is used here only to get a
    reasonable, real-activity candidate POOL -- final episode selection
    still uses realized volatility (see main()), not raw volume, since the
    two aren't the same thing for this asset class (a first attempt sorting
    by raw volume24hr without this ordering fix picked up a cluster of
    dormant "2028 election longshot" markets with real dollar volume but
    almost no price movement at all -- zero trades for any strategy)."""
    markets = []
    for offset in range(0, limit, 100):
        r = requests.get(GAMMA_MARKETS_URL, params={
            "closed": "false", "limit": min(100, limit - offset), "offset": offset,
            "order": "volume24hr", "ascending": "false",
        }, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        markets.extend(batch)
    out = []
    for m in markets:
        token_ids = m.get("clobTokenIds")
        if isinstance(token_ids, str):
            try:
                token_ids = json.loads(token_ids)
            except (json.JSONDecodeError, TypeError):
                token_ids = None
        if not token_ids:
            continue
        try:
            vol24 = float(m.get("volume24hr") or 0)
        except (TypeError, ValueError):
            vol24 = 0.0
        out.append({"question": m.get("question"), "token_id": token_ids[0], "volume24hr": vol24})
    return out


def fetch_market_history(token_id):
    r = requests.get(CLOB_PRICES_HISTORY_URL, params={"market": token_id, "interval": "max"}, timeout=20)
    r.raise_for_status()
    points = r.json().get("history", [])
    return [(datetime.fromtimestamp(pt["t"], tz=timezone.utc), float(pt["p"]), float(pt["p"])) for pt in points]


def _slugify(text, max_len=40):
    safe = "".join(c if c.isalnum() else "_" for c in text)
    return safe[:max_len].strip("_")


def fisher_combine(p_values):
    p_values = [max(p, 1e-12) for p in p_values]
    chi2_stat = -2 * sum(math.log(p) for p in p_values)
    df = 2 * len(p_values)
    # survival function of chi-square via regularized upper incomplete gamma;
    # use a simple series-free approximation good enough for this purpose:
    # for integer df, chi2 CDF has a closed form sum.
    k = df // 2
    term = math.exp(-chi2_stat / 2)
    total = term
    for i in range(1, k):
        term *= (chi2_stat / 2) / i
        total += term
    return min(1.0, total)


def benjamini_hochberg(p_values, q=0.10):
    n = len(p_values)
    ranked = sorted(range(n), key=lambda i: p_values[i])
    bh_rank1_threshold = q * 1 / n
    largest_passing_rank = 0
    for rank, idx in enumerate(ranked, start=1):
        if p_values[idx] <= q * rank / n:
            largest_passing_rank = rank
    passing = set()
    if largest_passing_rank > 0:
        for rank, idx in enumerate(ranked, start=1):
            if rank <= largest_passing_rank:
                passing.add(idx)
    return passing, bh_rank1_threshold


def main():
    config = load_tracks_config()
    base_config = config["prediction_markets_momentum"]

    print(f"=== Fetching {N_CANDIDATES} candidate active markets (volume floor only) ===")
    markets = fetch_active_markets()
    candidates = []
    for m in markets:
        path = DATASET_DIR / f"{_slugify(m['question'])}_{m['token_id'][:10]}.csv"
        if path.exists():
            history = []
            with open(path) as f:
                next(f)
                for line in f:
                    ts_str, price_str, low_str = line.strip().split(",")
                    history.append((datetime.fromisoformat(ts_str), float(price_str), float(low_str)))
        else:
            history = fetch_market_history(m["token_id"])
            time.sleep(0.3)
            with open(path, "w") as f:
                f.write("timestamp,price,low\n")
                for ts, price, low in history:
                    f.write(f"{ts.isoformat()},{price},{low}\n")
        if len(history) < MIN_TICKS:
            continue
        prices = [h[1] for h in history]
        returns = [(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
        volatility = (sum(r * r for r in returns) / len(returns)) ** 0.5 if returns else 0.0
        candidates.append({"question": m["question"], "history": history, "volatility": volatility, "volume24hr": m["volume24hr"]})

    candidates.sort(key=lambda c: c["volatility"], reverse=True)
    episodes = candidates[:N_MARKETS]
    for c in episodes:
        print(f"  {c['question'][:60]!r}: {len(c['history'])} ticks, volatility={c['volatility']:.5f}, 24h vol ${c['volume24hr']:,.0f}")

    print(f"\n=== Selected the {len(episodes)} most-volatile of {len(candidates)} qualifying markets (>= {MIN_TICKS} ticks) ===")

    strategy_results = {}
    for strategy_name, params in ALL_STRATEGIES.items():
        per_episode = []
        for ep in episodes:
            r = run_cell(strategy_name, params, ep["history"], base_config)
            per_episode.append({"question": ep["question"], "result": r})
            print(f"  {strategy_name} / {ep['question'][:50]!r}: equity_return={r['return_pct']:.2f}% "
                  f"summed_rt={r['summed_rt_return_pct']:.2f}% n_rt={r['n_round_trips']} p={r['p_value']}")

        p_values = [e["result"]["p_value"] for e in per_episode if e["result"]["p_value"] is not None]
        total_summed_return = sum(e["result"]["summed_rt_return_pct"] for e in per_episode)
        combined_p = fisher_combine(p_values) if p_values else 1.0
        strategy_results[strategy_name] = {
            "per_episode": per_episode, "combined_p": combined_p,
            "total_summed_return": total_summed_return, "n_episodes_with_trades": len(p_values),
        }
        print(f"  -> {strategy_name}: Fisher-combined p={combined_p:.5f} across {len(p_values)} episodes with trades, "
              f"pooled summed return={total_summed_return:.2f}%\n")

    print("=== Final gate: Fisher-combined p<0.10 AND positive pooled summed return ===")
    combined_p_values = []
    strategy_names = list(ALL_STRATEGIES.keys())
    gate_passes = {}
    for strategy_name in strategy_names:
        sr = strategy_results[strategy_name]
        gate_pass = sr["combined_p"] < PASS_P_THRESHOLD and sr["total_summed_return"] > 0
        gate_passes[strategy_name] = gate_pass
        combined_p_values.append(sr["combined_p"])
        print(f"  {strategy_name}: combined_p={sr['combined_p']:.5f}, pooled_return={sr['total_summed_return']:.2f}% "
              f"-> {'PASS' if gate_pass else 'fail'}")

    bh_passing_idx, bh_rank1_threshold = benjamini_hochberg(combined_p_values, q=0.10)
    print(f"\n=== Benjamini-Hochberg at q=0.10 across 6 strategies: rank-1 threshold = {bh_rank1_threshold:.5f} ===")
    if not bh_passing_idx:
        print("  0 strategies survive BH correction.")
    else:
        for idx in sorted(bh_passing_idx):
            print(f"  SURVIVES BH: {strategy_names[idx]} p={combined_p_values[idx]:.5f}")

    return strategy_results


if __name__ == "__main__":
    main()
