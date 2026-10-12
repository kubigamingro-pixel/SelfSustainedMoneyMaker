# Audit: SelfSustainedMoneyMaker paper-trading engine (improvement, security)

- **Date:** 2026-10-12
- **Repo/branch:** kubigamingro-pixel/SelfSustainedMoneyMaker, `main` (local clone at HEAD `d5285723`, tick 2026-10-12T08:33Z)
- **Path:** D:\SelfSustainedMoneyMaker
- **Target:** whole app (engine/, tracks/, scheduler.py, config/tracks.yaml, tick.yml, grid/scoreboard/trade_log, docs)
- **Mode:** read-only. Nothing was edited, pushed or opened. The only file written is this report.

## Summary

The biggest problem is a Polymarket accounting bug. When the tracked market closes, the engine switches to a new market but keeps the open position, so the position gets valued at an unrelated contract's price. That happened during the 2026-10-03/04 incident: a position bought at 0.965 on a market that then settled near 0.9995 (a winner) was marked at 0.725, then at 0.07, on two different markets. Several of the "stopped" outcomes in PERFORMANCE_REVIEW.md sections 8 and 9 are therefore partly artifacts. Together with how markets are picked (highest volume, which often means in-play or nearly resolved markets) and a cost filter that breaks near zero, the Polymarket evidence so far says very little about momentum itself.

On the three pain points: (a) the regime guard is not what is holding back the Polymarket tracks; the cost filter blocked all 7 crossovers since the reset, and the guard blocked only 1 of them. (b) The liquidation/loss-cap coupling should be decoupled, because each permanent stop ends the track's sample and has already forced two manual resets. (c) Backtesting is the highest-value addition, and the strategy code is already pure enough to replay offline.

Security is in good shape. A scan of every object in the git history found no PAT or other credential, and the committed .db files hold only simulated data. The main security gap is supply chain: unpinned pip dependencies are installed on every tick inside a job that has `contents: write`.

## Findings

| # | Severity/Impact | Effort | Type | Area | Finding | Evidence |
|---|---|---|---|---|---|---|
| 1 | Critical | M | improvement | Polymarket data / ledger | Market rollover keeps the open position and marks it at the new market's price | `engine/data/prediction_source.py:55-63`; trades 2026-10-03/04 (below) |
| 2 | High | S | improvement | Polymarket market selection | "Highest-volume active market" picks in-play / near-resolution markets: 14 rollovers in 20 days, prices pinned at 0.0005 or 0.9995 | condition-file git history; equity_curve prices 2026-10-11 |
| 3 | High | S | improvement | `engine/risk.py` cost filter | Range is divided by `tail[0]`, so near-zero prices produce huge "expected moves" and let the worst entries through | `risk.py:21-28`; 2026-10-02 BUY at 0.016 after 0.0025 (range 540%) |
| 4 | High (pain a) | S | improvement | Regime guard | The guard is not the binding filter on Polymarket; the cost filter is. On crypto it blocked half the crossovers | replay below |
| 5 | High (pain c) | M-L | improvement | Evaluation | No offline backtest; forward test produces about 1 round trip per day per track | ledgers: 0-18 trades per track |
| 6 | Medium (pain b) | S-M | improvement | Leverage risk | At 50% sizing and a 50% floor, one gapped liquidation permanently ends the track. Decouple it | `leverage_risk.py:56-95`, `leveraged_runner.py:91-104` |
| 7 | Medium | S | improvement | Data sources | Signals use the in-progress candle, so the same strategy diverges between tracks seconds apart | `crypto_source.py:12`, `stocks_source.py:24`; crypto_momentum vs _2x exits 10-06 |
| 8 | Medium | S-M | improvement | Experiment design | Every track fetches its own data and picks its own Polymarket market, so "head-to-head" tracks see different inputs | tracks/*.py; plain vs regime on different contracts 10-02 to 10-03 |
| 9 | Medium | S | improvement | Observability | Per-tick decisions (HOLD, guard failed, cost filtered) are not persisted; `logs/` is gitignored, so CI decisions are lost | `.gitignore:3`, `runner.py:64-69` |
| 10 | Medium | M | improvement | Ledger | Manual resets overwrite live ledgers; the scoreboard and grid no longer show earlier losses | commits `ab7b3037`, `2260457f` |
| 11 | Medium | S | security | CI / supply chain | `pip install` of unpinned `>=` deps on every tick, under `contents: write`; actions pinned by tag, not SHA | `requirements.txt`, `tick.yml:14-15,31-37` |
| 12 | Low | S-M | improvement | Leverage realism | Liquidation and loss cap only look at the 15-minute tick price, so losses run to -147% or -728% of margin before closing | `leverage_risk.py:63-66`; PERFORMANCE_REVIEW s.8 |
| 13 | Low | S | improvement | Strategy robustness | Momentum exits only on the crossover event; one missed tick at the crossover leaves a position open until the next full cycle | `momentum.py:45-51`; 5 tick gaps >20 min since 10-02 |
| 14 | Low | S | security | API handling | No range, finiteness or staleness checks on prices (Polymarket held 0.535 for 109 ticks, about 27 h) | `prediction_source.py:70`, `crypto_source.py:12` |
| 15 | Low | S | improvement | Ledger | Writes are not atomic per tick (`record_trade` commits before `set_state`) | `ledger.py:96-109`, `risk.py:66-75` |
| 16 | Low | S | docs | Docs | README, scheduler and grid comments are out of date (four tracks, "schedule", "regime 1x only") | `README.md`, `scheduler.py:4`, `grid.py:152-155`, `tracks.yaml:226-227` |
| 17 | Info | - | security | Secrets | Full git-history scan is clean: no PAT, no keys, no credentialed remote URL | scan below |
| 18 | Info | S | security | Repo hygiene | `.claude/settings.local.json` is untracked but not ignored; owner's personal email is in 36 public commits | `git status`; `git log --format=%ae` |
| 19 | Info | - | security | Committed DBs | DBs contain only `state`, `trades`, `equity_curve` and `sqlite_sequence` with simulated values. Fine for paper trading; not fine once real money is involved | schema dump below |

## Details

### 1. Polymarket rollover carries the open position to a different market (Critical)

**What:** `fetch_series()` swaps to a newly picked market when the current one is `closed` (`prediction_source.py:55-63`) and returns the new market's price. `runner.run_tick` / `leveraged_runner.run_tick` do not know a swap happened, so `position_qty` from market A is valued, liquidated or sold at market B's price. The ledger never stores which market a position belongs to.

**Evidence** (ledger from commit `7e484bbf`, just before the 10-05 reset, `prediction_markets_momentum`):

```
2026-10-03T16:15  BUY at 0.9698 on market 0xe46219...   (price then 0.9995 for 2 h: effectively resolved YES)
2026-10-03T18:15  rollover -> 0x16c502...  price 0.725 -> equity marked 10.05 -> 8.66
2026-10-03T23:01  rollover -> 0x206475...  price 0.10  -> equity 5.50
2026-10-04T04:31  SELL at 0.0697 (on market 0x206475...)
2026-10-04T07:15  LOSS_CAP_TRIGGERED
```
`prediction_markets_momentum_10x`: BUY 0.9698 at 16:16, then "LIQUIDATED remaining_margin_pct=-147.5%" at 18:15, which is exactly the rollover tick. `_2x` was "LIQUIDATED" at 19:46 at 0.4975 on the second market. All three were winning positions on their real market.

**Why it matters:** this alone invalidates part of the Polymarket conclusions in PERFORMANCE_REVIEW.md section 9 ("regime guard did not protect these tracks", leverage liquidations). Any future rollover with an open position will do the same.

**Recommended change:**
- Store `condition_id` (and token id) with the position in the ledger `state`.
- When a rollover is detected while holding, settle the position first at the old market's final `outcomePrices` (1/0 on resolution), recorded as `SELL reason=MARKET_RESOLVED`. Only then switch markets and reset history.
- Add an assertion in the runner that the position's market equals the market being priced.
- Annotate PERFORMANCE_REVIEW s.8/s.9 so nobody draws strategy conclusions from those numbers.

### 2. Market selection picks the worst markets for this strategy (High)

**What:** `pick_active_market()` takes the top `order=volume` active market (`prediction_source.py:21-32`). The condition file changed 14 times between 09-22 and 10-12, so markets typically resolve within 1-3 days. Price runs on 2026-10-11 look like a live event: 0.565 → 0.495 → 0.405 → 0.26 → 0.105 → 0.026 → 0.0005 in eight 15-minute ticks. The next market then swung 0.05 to 0.50 tick to tick before also going to 0.0005. "Active, not closed" still includes markets sitting at 0.0005 or 0.9995 for hours (`closed` lags resolution).

**Why it matters:** a 5/20 SMA on 15-minute samples of an in-play binary market measures news jumps, not momentum, and the price is bounded: a BUY at 0.97 has at most 3% upside against a modelled 5% round-trip cost.

**Recommended change:** select markets by rules fixed in advance: end date at least N days out, price inside [0.10, 0.90], minimum liquidity and order-book depth (the CLOB book logger already exists). Keep the market pinned until it fails those rules, and close any position before switching. Skip ticks where the price is outside the band.

### 3. Cost filter breaks near zero (High)

**What:** `expected_move_pct = (max - min) / tail[0] * 100` (`risk.py:28`). With a series flat at 0.0025 and one tick at 0.016, the "expected move" is about 540%, so the filter passes. That is exactly the 2026-10-02 entry: BUY at 0.0161, back to 0.0025 one tick later, loss cap. On a contract near 0, one price increment is already larger than the 10% threshold.

**Recommended change:** for Polymarket, measure the move in absolute probability points (or divide by the mean or median of the window, not the first value), and combine it with the price band from #2. A robust range (for example the 10th to 90th percentile) avoids passing on single-tick spikes. This is a mechanical fix of a units bug, not a retuning of the 2x multiplier.

### 4. Pain point (a): is the regime guard helping? (High)

The Polymarket and crypto results below come from replaying the real strategy classes over each track's stored tick prices (`equity_curve.price`, history resets at the recorded rollover times). For crypto this is an approximation, because live signals use Kraken 15-minute candles including the in-progress one (inferred to be close, not identical).

| Track family (since regime tracks started) | Crossovers | Blocked by regime guard | Blocked by cost filter | Entries |
|---|---|---|---|---|
| Polymarket plain (since 10-05 reset) | 7 | - | 7 | 0 |
| Polymarket regime | 7 | 1 | 6 | 0 |
| BTC plain (approx.) | 30 | - | 25 | 5 |
| BTC regime (approx.) | 30 | 15 | 15 | 0 |
| SPY plain | 2 | - | 0 | 2 |
| SPY regime | 2 | 1 | 0 | 1 |

**Verdict:**
- On Polymarket the guard is not what is idle; the cost filter blocks everything at the current ~0.54 price, because a 20-tick range of 1-2% cannot clear 10%. Before the reset, the guard passed 5 of 5 regime entries and they all lost (10-02/03 trades). So it never addressed the real Polymarket failure modes, which are #1-#3: rollover accounting, near-resolved markets, and spike entries.
- On BTC the guard is very selective. It blocked all three entries the plain track took since 10-02 (10-05, 10-07, 10-08), which netted -0.6% on crypto_momentum. That is weakly favourable, but n=3.
- What it does mechanically: at a crossover, "slow SMA rising" is the same as "price now > price 20 bars ago". So it rejects V-bottom reversals and keeps continuation crossovers.
- Forward testing at this rate cannot settle whether it helps. Evaluate it on replayed history (#5) and judge it on crypto and SPY only, until #1-#3 are fixed for Polymarket.

### 5. Pain point (c): no backtesting (High)

**What:** all evidence is forward-only. Since 10-02 the 21 tracks produced 0-12 trades each, and the 2x/10x columns are the same signal as 1x (see #8), so there are effectively 6 independent experiments.

**Why it matters:** PERFORMANCE_REVIEW itself sets 50-100+ closed trades per track before judging. At about 0.5-1 round trip per day, that is months per question, and every rule change restarts the clock.

**Recommended change:** the code is already close to backtestable. Strategies are pure (`decide(series, holding)`), and the fills and risk checks are plain functions.
1. Make `Ledger` accept a path or `:memory:`, and inject a clock instead of `datetime.now()` (`ledger.py:107,114`, `leveraged_runner.py:62`).
2. Write a `backtest.py` that walks closed historical candles through the same `STRATEGIES`, `passes_cost_filter`, `simulate_fill`, `check_loss_cap` / `check_liquidation` / `apply_funding_cost` functions, so live and backtest share one code path.
3. Data (verify the limits before building; this audit had no network access to test them): BTC/ETH 15-minute history via ccxt `fetch_ohlcv(since=...)` pagination. Kraken's public OHLC returns only the latest 720 candles, so use another USD venue or Kraken's downloadable CSVs. SPY 15-minute bars from yfinance cover only about 60 days, and 1-hour bars about 2 years. For Polymarket, use the CLOB `prices-history` endpoint per token.
4. Discipline: fix the parameters before running; split in-sample and out-of-sample by time; report net-of-cost return, trade count, bootstrap confidence intervals, and buy-and-hold alpha; run the same rules on several assets (BTC, ETH, SOL; SPY, QQQ) to separate "strategy" from "market".
5. Keep forward testing as the out-of-sample check that live data matches the backtest.

### 6. Pain point (b): should liquidation and the account stop be decoupled? (Medium)

**What:** a 10x BTC entry commits 25 margin and pays about 0.65 fee, leaving 24.35 cash. The floor is 25. Liquidation fires at 80% margin loss, so if it fills exactly at the threshold the track survives (about 24.35 + 4.4 ≈ 28.7). In practice it is checked only at the tick price (#12), so a gap past the threshold leaves `recovered = 0` and equity at about 24.35, under the floor, and the track is permanently stopped. On Polymarket the 2% entry fee makes it worse: even "LIQUIDATED remaining_margin_pct=5.0%" on _2x stopped the track. With 2x, liquidation needs a 40% adverse move, so the stop is almost never reached any other way. In effect the account stop is not a separate safeguard; it is "first gapped liquidation = end of track".

**Verdict: decouple it.** The project's goal is measuring edge. A rule that ends a track on its first bad event turns each leveraged track into a single sample, and it has already led to two manual resets (#10), which put human judgement into the dataset. A real trader would want the stop; the research harness wants the data.

**Recommended change (pick one):**
- Make the account stop non-terminal in research mode: record a `LOSS_CAP_BREACHED` event (with "would have stopped here" in the scoreboard) and keep trading. Reporting stays honest without losing samples. This is the preferred option.
- Or keep it terminal but size leveraged margin so that two or more full margin losses fit above the floor (for example `position_size_pct: 20` for the 2x/10x tracks). This changes comparability with the 1x tracks, so it would be a new epoch (#10).

Do not change the 1x tracks for this; the issue only applies to leveraged tracks.

### 7. Signals are computed on the in-progress candle (Medium)

**What:** Kraken (via ccxt) and yfinance both return the current, unfinished 15-minute bar as the last element, and it is used as the latest close. Identical strategies a few seconds apart then disagree. Example: `crypto_momentum` and `crypto_momentum_2x` both sold the same 10-05 position, at 00:15 and at 01:45 on 10-06 respectively (different reason values, same strategy and params).

**Recommended change:** drop the last candle unless its open time + 15 minutes ≤ now, and price fills at the latest trade or ticker price. That also makes live results comparable to a backtest on closed bars.

### 8. Tracks that should be head-to-head see different inputs (Medium)

**What:** each of the 21 track modules fetches separately (for example, BTC is fetched 6 times per tick), and each Polymarket track keeps its own condition file. From 10-02 23:01 to 10-03 23:01 the plain Polymarket tracks were on markets `0x2598…`/`0xe462…`/`0x16c5…` while all regime tracks were on `0x2064…`. A stopped track also stops rolling over, so the tracks drift further apart.

**Recommended change:** fetch once per market per tick in `scheduler.py` and pass the same series to every track on it; keep one shared "current Polymarket market" state. Consider calculating 2x/10x outcomes from the 1x trade stream (with funding and liquidation applied) instead of separate fetch paths. They carry no new information about signal edge.

### 9. Decisions are not persisted (Medium)

**What:** "BUY signal filtered", "regime guard failed", HOLD reasons and tick failures go only to `logs/scheduler.log` (gitignored) and the GitHub Actions run log (expires). The replay in #4 was needed just to count them.

**Recommended change:** add a `decisions` table per ledger (ts, price, signal, reason, `cost_filter_pass`, `guard_pass`, `market_id`). It is small and makes questions like (a) answerable with one query.

### 10. Resets overwrite history (Medium)

Resets (`ab7b3037`, `2260457f`) replaced the ledgers and deleted condition and price history. The losses survive only in git and in PERFORMANCE_REVIEW prose; `scoreboard.py` and `grid.py` now show those tracks as fresh. Add an `epoch` column (or an `epochs` table with a rules/config hash) and a `reset_track.py` that starts a new epoch instead of rewriting the file, and have the scoreboard report per epoch and across all epochs.

### 11. Supply chain in the tick job (Medium, security)

`pip install -r requirements.txt` resolves `ccxt>=4.3.0`, `yfinance>=0.2.40` and others fresh on every tick (about 96 times a day), inside a job with `permissions: contents: write`. A compromised or broken new release (ccxt publishes very often) would run with a token that can push to `main`. `actions/checkout@v4` and `actions/setup-python@v5` are pinned by moving tag.

**Recommended change:** commit a lock file with hashes (`pip-compile --generate-hashes` → `requirements.lock`, and `pip install --require-hashes -r requirements.lock` in CI). Pin both actions to commit SHAs. Optionally split the job: run the tick with `contents: read`, upload `data_store/` as an artifact, and commit it in a second job that has write access but runs no third-party code. Update the lock deliberately (for example monthly, or with Dependabot).

### 12. Liquidation only at tick prices (Low)

The documented -728% and -147.5% "remaining margin" liquidations come from checking only the tick close (the -147.5% one is actually the rollover from #1). For crypto and SPY the OHLCV `low` since the last tick is available. Trigger liquidation when the low crosses the liquidation price, and fill at the liquidation price minus slippage, which is closer to an exchange. Funding is already time-weighted correctly.

### 13. Event-only momentum exit (Low)

`crossed_down` requires the crossover to land exactly on the latest bar (`momentum.py:46`, `momentum_regime_filtered.py:51`). Ticks are reliable (median gap 15.0 min, but 5 gaps >20 min and a max of 147 min since 10-02, from `crypto_momentum` equity_curve), and a missed or deduplicated bar at the crossover keeps the position open through the whole next cycle. Exiting on state (`holding and fast_now < slow_now`) behaves the same in normal operation and is robust to gaps. This changes behaviour, so treat it as an engine fix applied to all momentum tracks at an epoch boundary.

### 14. API response validation (Low, security)

There is no check that prices are finite, positive, inside (0, 1) for Polymarket, or fresh. Examples: `float(prices[0])` (`prediction_source.py:70`); ccxt closes only filter `None` (`crypto_source.py:12`). Polymarket showed 0.535 unchanged for 109 ticks, which is probably no trades rather than a real price, yet each repeat feeds the SMA as a new sample. Add a `validate_price()` helper (finite, in range, timestamp not older than N minutes), and skip the tick on failure with a recorded decision (#9). Parsing is otherwise safe: `yaml.safe_load`, parameterized SQL everywhere, and no `eval` or shell calls.

### 15. Non-atomic ledger writes (Low)

`record_trade` and `record_equity` each `commit()` before `set_state` does. An exception between them leaves a trade with no matching state. Wrap each tick in one transaction (`with conn:`) and commit once at the end.

### 16. Documentation drift (Low)

README still describes four tracks and a "schedule" trigger with a "known issue" that no longer applies. `scheduler.py:4` says "on a schedule". `grid.py:152-155` and `tracks.yaml:226-229` say the regime variants are "1x only" although 2x/10x now exist. PERFORMANCE_REVIEW is "as of 2026-09-28" in its header. Update them, keeping the intentional workflow_dispatch from cron-job.org as the documented design.

### 17. Secret scan (Info, clean)

- Scanned every object in the local repository, including unreachable ones: 19,758 blobs, 3,728 trees, 1,857 commits (`git cat-file --batch-all-objects`), plus all commit messages. Patterns: `github_pat_…`, `gh[pousr]_…`, `AKIA…`, `sk-…`, `xox?-…`, `AIza…`, `BEGIN … PRIVATE KEY`, and `scheme://user:pass@`.
- **No PAT or real credential was ever committed.** The only hits are example strings in third-party persona docs (`.claude/agents/security-senior-secops.md:40-48`, `security-secrets-credential-engineer.md:92`). `git log -S github_pat_` and `-G 'ghp_|github_pat'` return nothing.
- `origin` remote URL has no embedded token; no credential helper is configured in `.git/config`; stash is empty.
- `.claude/settings.local.json` (untracked) contains no token patterns, and `logs/` contains none.
- Limits: only the local clone was scanned. GitHub-side refs that are not fetched (other branches, PR refs, Actions logs) were not checked; enable GitHub secret scanning and push protection in repo settings to cover those.

### 18. Repo hygiene (Info)

- Add `.claude/settings.local.json` to `.gitignore`. It is per-machine and currently one `git add -A` away from being published.
- 36 commits carry the owner's personal Gmail address in a public repo. Turn on "Keep my email addresses private" and use the noreply address from now on. Rewriting history is not recommended.
- The cron-job.org trigger necessarily stores a GitHub token on a third-party service. Make sure it is fine-grained, limited to this one repo, has only Actions: read/write (no Contents), and has an expiry. This could not be verified from here.

### 19. Committed SQLite files (Info)

The schema (checked in all 21 DBs) is `state`, `trades`, `equity_curve`, `sqlite_sequence`, holding simulated cash, quantities, prices and reasons. `*_orderbook_log.jsonl`, `*_condition.json` and `*_price_history.json` hold only public market data. Nothing sensitive is exposed today. Before any real-money phase, move live ledgers out of the public repo: real positions plus a linked identity are sensitive, and strategy timing becomes visible.

## Recommended action plan

Each step is a separate change the owner approves. None of them re-enables the GitHub `schedule` trigger.

**Must fix**
1. (#1) Store the market id with the position, and settle on resolution before any rollover; add the runner assertion. Add a note to PERFORMANCE_REVIEW s.8/s.9 that those Polymarket numbers include cross-market marks.
2. (#2, #3) Polymarket market eligibility rules (end date, [0.10, 0.90] band, liquidity), and a cost filter in absolute probability points or relative to the window median.
3. (#11) Hash-locked requirements and SHA-pinned actions in `tick.yml`.

**Quick wins**
4. (#9) `decisions` table.
5. (#7) Drop in-progress candles.
6. (#8) One fetch per market per tick, shared by all its tracks, plus a shared Polymarket market state.
7. (#14, #15) Price validation helper and one transaction per tick.
8. (#18, #16) `.gitignore` the local settings file; refresh the docs.

**Next**
9. (#5) Backtest harness on the shared engine functions (in-memory ledger, injected clock, historical loaders), then evaluate plain vs regime on BTC/ETH/SPY with out-of-sample splits.
10. (#6, #10) Epochs plus a non-terminal research-mode loss cap for leveraged tracks.

**Later**
11. (#12) Intrabar (candle-low) liquidation.
12. (#13) State-based momentum exit, at an epoch boundary.
13. Before real money: private ledger storage, a GitHub secret-scanning/push-protection check, and an audit of the trading-key handling.

## Not checked

- No network from the audit environment, so live API behaviour, historical-data limits (#5) and current dependency CVEs (`pip-audit`) were not run.
- The crypto part of the replay in #4 uses stored tick prices, not the exact Kraken candles (approximation, inferred to be close).
- GitHub-side settings (repo visibility, secret scanning, Actions logs, the cron-job.org token's scopes) and refs not present in the local clone.
- `quant_strategy_audit.docx` was not re-read; its conclusions were taken from PERFORMANCE_REVIEW.md.
- `.claude/agents/` persona content (third-party docs, not runtime code), apart from the secret scan.
- No tests exist in the repo, so none were run; the code was not executed apart from read-only replays against copies of the ledgers.
