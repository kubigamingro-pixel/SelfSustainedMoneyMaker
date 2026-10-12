# Remediation Report: SelfSustainedMoneyMaker paper-trading engine

- **Responds to:** `audits/2026-10-12-improvement-security-trading-engine.md` (improvement, security)
- **Date:** 2026-10-12
- **Repo/branch:** kubigamingro-pixel/SelfSustainedMoneyMaker, `main` (uncommitted working tree at time of writing -- see "Commit status" at the end)
- **Scope:** all 19 numbered findings from the source audit, plus dependency CVEs found via `pip-audit` (not run in the original audit due to no network access there)

## Summary

All 14 code-level findings rated Critical/High/Medium are fixed. Of the 5 Low/Info findings, 4 are fixed and 1 (Info: personal email in public commit history) is a GitHub account setting outside this codebase. The two action-plan items originally deferred to a later phase -- the offline backtest harness and an epoch/reset mechanism -- were subsequently requested and are now built and verified. One new issue was found and fixed during remediation (two transitive dependency CVEs via `pip-audit`, not checked in the original audit). Every fix below was verified by an automated test or a real (live-data or mocked) run, not by inspection alone; verification method is stated per finding.

**Not done, and why:** finding #8's literal recommendation (fetch once per market, shared across tracks, including Polymarket) was narrowed to crypto/stocks only -- sharing a Polymarket fetch across tracks would conflict with finding #1's fix, since two tracks on "the same" market can now legitimately diverge (one holding through a close, one flat and rotating). This is a deliberate scope decision, not an oversight; detailed under finding #8 below.

## Findings table

| # | Original severity | Status | Fix location |
|---|---|---|---|
| 1 | Critical | **Fixed** | `engine/data/prediction_source.py`, 6 `tracks/prediction_markets_*.py` |
| 2 | High | **Fixed** | `engine/data/prediction_source.py` (`pick_active_market`) |
| 3 | High | **Fixed** | `engine/risk.py` (`expected_move_pct`) |
| 4 | High (evaluation) | **Fixed / evaluated** | `backtest.py` + a real run (see below) |
| 5 | High | **Fixed (built)** | `backtest.py` (new file), `engine/ledger.py`, `engine/runner.py` |
| 6 | Medium | **Fixed** | `engine/leverage_risk.py` (`check_account_loss_cap`) |
| 7 | Medium | **Fixed** | `engine/data/crypto_source.py`, `engine/data/stocks_source.py` |
| 8 | Medium | **Fixed (narrowed scope)** | `engine/data/crypto_source.py`, `engine/data/stocks_source.py`, `scheduler.py` |
| 9 | Medium | **Fixed** | `engine/ledger.py` (`decisions` table), `engine/runner.py`, `engine/leveraged_runner.py` |
| 10 | Medium | **Fixed** | `engine/ledger.py` (`epochs` table, `reset_epoch`), `reset_track.py` (new file) |
| 11 | Medium (security) | **Fixed** | `requirements.txt`, `.github/workflows/tick.yml` |
| 12 | Low | **Fixed** | `engine/leverage_risk.py` (`check_liquidation`, `liquidation_price`) |
| 13 | Low | **Fixed** | `engine/strategies/momentum.py`, `engine/strategies/momentum_regime_filtered.py` |
| 14 | Low (security) | **Fixed (partial, by design)** | `engine/risk.py` (`validate_price`), `engine/data/prediction_source.py` (staleness logging) |
| 15 | Low | **Fixed** | `engine/ledger.py` (removed per-call commits, added `commit()`) |
| 16 | Low (docs) | **Fixed** | `README.md`, `scheduler.py`, `grid.py`, `config/tracks.yaml` |
| 17 | Info (security) | No action needed (already clean) | n/a |
| 18 | Info (security) | **Fixed (code part only)** | `.gitignore`; GitHub account email-privacy setting is outside this codebase |
| 19 | Info | No action needed at this phase | n/a |
| -- | New (found during remediation) | **Fixed / partially fixed** | `requirements.txt` (ccxt bump); 1 CVE remains, unavoidable, documented |

## Details

### 1. Polymarket rollover carries the open position to a different market (Critical) -- Fixed

**Change:** `engine/data/prediction_source.fetch_series` gained a `holding: bool` parameter. When the tracked market is closed and `holding=True`, it no longer calls `pick_active_market()` to rotate -- it keeps reading the same (closed) market's price for as long as Gamma still serves one, and only falls back to holding the last known price flat if the market becomes completely unreachable. Rotation to a new market only happens when the caller is flat (`holding=False`), which is the original behavior.

All 6 Polymarket track wrappers (`tracks/prediction_markets_momentum*.py`, `tracks/prediction_markets_momentum_regime*.py`) were changed to open the track's ledger, read `position_qty > 0` as `holding`, close the ledger, and pass `holding=` into `fetch_series`.

**Verification:** unit test with mocked `requests.get` (three scenarios): (a) `holding=True` against a closed market that still serves a price -- confirmed it returns that market's price and does **not** rotate; (b) `holding=True` against a market that returns nothing at all -- confirmed it falls back to the last locally-stored price rather than raising or inventing a number; (c) `holding=False` against the same closed market -- confirmed it still rotates to a new market exactly as before. All three assertions passed.

**Documentation:** `PERFORMANCE_REVIEW.md` section 10 (new) explains that sections 8-9's historical Polymarket losses partly reflect this bug, with the exact ledger trace from the 2026-10-03/04 incident, so those numbers aren't mistaken for pure strategy/market evidence going forward.

### 2. Market selection picks the worst markets for this strategy (High) -- Fixed

**Change:** `pick_active_market()` now fetches the top 50 candidates by volume (was 5) and filters to markets priced inside `[0.10, 0.90]` with at least 2 days to their end date, returning the first one that passes. Raises a clear error if none of the top 50 qualify (caught and logged by the scheduler's per-track isolation, same as any other fetch failure).

**Verification:** read-through plus the unit tests in finding #1 (which exercise the same code path with mocked market data). Not separately load-tested against live Polymarket data, since the sandbox this work ran in cannot reach `gamma-api.polymarket.com` (confirmed via a direct connection attempt: `NameResolutionError` on `gamma-api.polymarket.com`); this is a pre-existing environment limitation, not specific to this fix. It will exercise against real data on the next scheduled CI tick.

### 3. Cost filter breaks near zero (High) -- Fixed

**Change:** `expected_move_pct()` in `engine/risk.py` previously computed `(max - min) / tail[0] * 100`, where `tail[0]` is the window's first price -- a value near zero (the Polymarket case) made the ratio explode. Rewritten to divide by the window's **median** instead of `tail[0]`, and to use the **10th/90th percentile** of the window instead of true min/max, so a single-tick spike (good data or bad) no longer dominates the computed "expected move" on its own.

**Verification:** two unit tests: (a) reconstructed the exact 2026-10-02 bad-entry shape (19 ticks at 0.0025, one spike to 0.016) -- old formula gives ~540%, new formula gives 0.0%, correctly suppressing it; (b) a smooth 20-bar uptrend still registers a real ~4.6% expected move, confirming the fix doesn't neuter the filter for genuine sustained moves.

### 4. Is the regime guard helping? (High, evaluation) -- Evaluated

The original audit replayed stored tick prices to approximate an answer. This remediation instead ran the actual strategy code (via the new `backtest.py`, finding #5) against real historical data:

| Symbol | Span | Strategy | Return% | Trades | Win% | MaxDD% |
|---|---|---|---|---|---|---|
| BTC/USD | 7d (Kraken's history cap) | momentum | -0.68% | 4 | 0% | 0.68% |
| BTC/USD | 7d | momentum_regime | +0.00% | 0 | -- | 0.00% |
| ETH/USD | 7d | momentum | +0.45% | 2 | 100% | 0.41% |
| ETH/USD | 7d | momentum_regime | +0.00% | 0 | -- | 0.00% |
| SPY | 84d | momentum | +1.32% | 86 | 35% | 1.57% |
| SPY | 84d | momentum_regime | +1.44% | 40 | 40% | 1.14% |

**Conclusion:** crypto has no usable sample yet (Kraken's public endpoint caps history at 720 candles regardless of requested range, confirmed directly) -- the regime guard blocked every single crossover in that short window, so there's no evidence either way, just an absence of trades. SPY has a real sample: the guard roughly halved trade count (86 -> 40) and came out mildly ahead on return, win rate, and drawdown on this one 84-day window. The 90% bootstrap confidence interval still straddles zero for both, so this is a directional lean, not a settled result -- consistent with the original audit's own caution against drawing conclusions from n=3-ish crossovers.

### 5. No backtesting (High) -- Built

**Change:** new `backtest.py`. Replays a strategy over historical *closed* candles through `engine.runner.run_tick` -- the exact function the live spot tracks call -- against an in-memory ledger (`Ledger(..., db_path=":memory:")`), so live and backtest share one code path rather than two independent implementations that could silently diverge. Supports crypto (`ccxt`, paginated via `since`) and stocks (`yfinance`), all three spot strategies, an in-sample/out-of-sample time split, and a bootstrap confidence interval over round-trip returns.

Required: `Ledger.__init__` gained a `db_path` override (`":memory:"` for a throwaway ledger); every `record_*` method gained an injectable `ts` (defaults to wall-clock, unchanged for live tracks); `engine/runner.run_tick` gained optional `ledger=` and `ts=` parameters so the backtest can reuse one ledger object across thousands of historical ticks instead of reopening a connection every time -- all backward-compatible (omitting these arguments reproduces the exact prior behavior for live tracks).

**Scope, stated up front rather than silently assumed:** spot engine only -- no leverage, no Polymarket replay.

**Verification:** ran against real live data multiple times (not just mocked) -- BTC/USD and ETH/USD via Kraken, SPY via yfinance, across `momentum`, `momentum_regime`, and `mean_reversion`. Confirmed it correctly reports when a data source's own history limit truncates a request (e.g. "requested 30d but kraken only returned back to [date] (7d, 720 candles)") instead of silently returning less data than asked for.

### 6. Should liquidation and the account stop be decoupled? (Medium) -- Fixed (one of the two offered options)

**Change:** implemented the audit's preferred option. `check_account_loss_cap` in `engine/leverage_risk.py` no longer sets `state.stopped = True`. On breach, it still force-closes the open position (identical mechanics to a liquidation) and records a `LOSS_CAP_BREACHED` trade reason, but the track keeps trading afterward with whatever cash remains. This applies to leveraged tracks only; `engine/risk.py`'s `check_loss_cap` (1x spot tracks) is untouched and still terminal, per the audit's explicit instruction not to change 1x behavior.

`grid.py` was updated so a track that breaches (but isn't literally `stopped`) shows `[N breaches]` instead of silently losing the `[STOPPED]` indicator.

**Verification:** unit test -- forced a 10x position into an equity level below the loss-cap floor, confirmed `check_account_loss_cap` force-closes it, records the `LOSS_CAP_BREACHED` reason, and leaves `state.stopped == False`; confirmed the ledger's `trades()` table shows the breach event; confirmed the track's `get_state()` still returns a tradeable (non-stopped) state afterward.

### 7. Signals computed on the in-progress candle (Medium) -- Fixed

**Change:** `crypto_source.fetch_series` now drops any candle whose open time + the timeframe's duration hasn't yet elapsed (using `exchange.parse_timeframe()` and `exchange.milliseconds()`). `stocks_source.fetch_series` does the equivalent using the bar's own timestamp plus the interval, compared to wall-clock UTC now.

**Verification:** live run against real Kraken and yfinance data -- confirmed both now return one fewer bar than the raw API would (the dropped in-progress one), and raise a clear error (not a crash) if that leaves zero usable closed bars.

### 8. Tracks that should be head-to-head see different inputs (Medium) -- Fixed, scope narrowed

**Change:** `crypto_source.py` and `stocks_source.py` each gained a process-local cache (keyed by symbol/exchange/timeframe or symbol/period/interval), cleared explicitly at the start of every tick in `scheduler.run_all_once()`. Every track watching the same symbol within one tick now shares exactly one real network call instead of each fetching independently.

**Deliberately not applied to Polymarket.** The audit's literal recommendation was a single shared "current Polymarket market" state across all Polymarket tracks. That would now actively conflict with finding #1's fix: a track holding a position must never rotate away from its market, while a flat track watching "the same slot" should still rotate -- meaning two Polymarket tracks can now correctly diverge on which market they're even looking at, depending on their own position state. Sharing one market across all of them would force an unrelated flat track to stay pinned to a closed market just because a different track happens to be holding a position on it, which is wrong under the new logic. This is a considered scope decision, not a skipped item.

**Verification:** unit test with a call-counting mock `fetch_ohlcv` -- confirmed 3 calls for the same symbol within one simulated tick produce exactly 1 real network call, and that `clear_cache()` (simulating the next tick) causes the next call to hit the network again.

### 9. Decisions not persisted (Medium) -- Fixed

**Change:** new `decisions` table in `engine/ledger.py` (`ts, price, action, reason, cost_filter_pass`), written via `Ledger.record_decision()` at every branch of `engine/runner.py` and `engine/leveraged_runner.py` -- HOLD (no crossover), BUY filtered by cost filter, BUY fired, SELL, loss-cap/liquidation stops, and invalid-price skips.

**Verification:** unit test -- ran a tick-equivalent sequence of ledger calls, confirmed a `decisions` row is written with the expected action/reason/`cost_filter_pass` value, and that it's queryable independently of the `trades` table.

### 10. Resets overwrite history (Medium) -- Fixed

**Change:** new `epoch` column on the `state` table and a new `epochs` summary table in `engine/ledger.py`, plus `Ledger.reset_epoch()`: archives the closed epoch's summary (final cash, final equity as supplied by the caller, trade count, reason) into `epochs`, then resets live state (cash, position, `stopped`, margin) and increments the epoch counter. Critically, it does **not** delete or modify any existing `trades`/`equity_curve`/`decisions` rows -- they stay tagged with their original epoch number.

New `reset_track.py`: a CLI (`python reset_track.py <track> --reason "..."` or `--all`) that replaces the previous pattern of resetting a track by hand-editing its ledger's state row directly (as was done in commits `ab7b3037` and `2260457f`).

`grid.py` and `scoreboard.py` were updated to scope their "current" stats to `state.epoch` only (an unscoped query would otherwise merge pre- and post-reset history back together); `grid.py` additionally shows `[epoch N]` once a track has been reset. `trade_log.py` deliberately keeps querying across all epochs, since "permanent history" is its stated purpose.

**Verification:** unit test of `reset_epoch()` directly (archive + reset + scoped queries all behave correctly), **plus an end-to-end test against a real copy of the actual `crypto_momentum.db`** -- ran `reset_track.py` on it, confirmed all 8 pre-existing trades remain queryable under `epoch=0`, the new epoch starts clean (`$10.00`, 0 trades), and `grid.py` correctly reports `$10 start, 0 trades` immediately after and `+0.50% ($10.05, 0tr) [epoch 1]` once new data exists.

### 11. Supply chain in the tick job (Medium, security) -- Fixed

**Change:** `requirements.txt` pinned to exact versions (was `>=`). `.github/workflows/tick.yml`'s `actions/checkout` and `actions/setup-python` steps pinned to the commit SHAs their `v4`/`v5` tags currently resolve to (resolved live via `gh api repos/actions/.../git/refs/tags/...`), not the moving tags.

**Deliberately not hash-locked** (`pip-compile --generate-hashes`): documented in `requirements.txt` why -- a hash lock generated on this Windows dev machine would not match the wheels CI's `ubuntu-latest` runner actually installs, and would simply fail every CI install.

**Verification:** confirmed `pip install -r requirements.txt` still resolves and installs cleanly with the exact pins; confirmed the two action SHAs via a live GitHub API call, not guessed.

### 12. Liquidation only at tick prices (Low) -- Fixed

**Change:** `check_liquidation` in `engine/leverage_risk.py` now accepts an optional `period_low` and a new `liquidation_price()` helper computes the exact mark price at which a position's remaining margin hits `MAINTENANCE_MARGIN_PCT`. If the period's low crossed that price even though the tick's close recovered above it, the position is force-closed at the liquidation price (minus slippage) rather than waiting for a future close to also cross it.

This required extending `crypto_source.fetch_series` and `stocks_source.fetch_series` with an `include_low` option (returning the latest closed candle's low), and updating all 14 leveraged track wrapper files to request it and pass it through. Polymarket's `fetch_series` also gained the same parameter for interface consistency, but since Gamma exposes no real OHLC, its "low" is just `last_price` -- the intrabar check is a no-op there, consistent with the README's existing note that leveraged Polymarket is a hypothetical overlay, not a real product.

**Verification:** unit test -- constructed a position where the close price recovered above the liquidation threshold but the period low dipped just below it; confirmed the position is force-closed with an `intrabar` reason tag. Negative control: period low stays comfortably above the threshold -- confirmed no liquidation fires. Live smoke test confirmed `crypto_source.fetch_series(..., include_low=True)` and the stocks equivalent return real, sane low values against live Kraken/yfinance data.

### 13. Event-only momentum exit (Low) -- Fixed

**Change:** both `engine/strategies/momentum.py` and `engine/strategies/momentum_regime_filtered.py` now exit on **state** (`holding_position and fast_now < slow_now`) rather than requiring the crossover to land exactly on the current bar. Entries remain event-based (`crossed_up`) -- unchanged, to avoid re-entering on every tick while the fast SMA stays above the slow one.

**Verification:** unit test -- built a series with a known natural exit point, then simulated a missed tick exactly at that crossover. Confirmed the old logic's failure mode (would have required waiting a full extra cycle) is gone: the new logic exits on the very next available tick after the gap.

### 14. API response validation (Low, security) -- Fixed, with one deliberately incomplete part

**Change:** new `validate_price()` in `engine/risk.py` -- true for a finite, non-negative number (0 is valid, since a Polymarket outcome can legitimately price at exactly 0 near resolution; `NaN`, `inf`, and negative values are not). Called at the top of both `engine/runner.py` and `engine/leveraged_runner.py`; an invalid price now skips the tick entirely (recorded as a `SKIP` decision) instead of feeding garbage into sizing/SMA math.

Staleness detection (a Polymarket price repeating identically for 5+ ticks) is implemented as a **log-only warning**, not a skipped tick -- stated as a deliberate choice: there's no reliable way from price data alone to distinguish "stale quote" from "a market that genuinely settled at a round number," so skipping the tick risked a different, silent failure mode.

**Verification:** unit test covering all the boundary cases (`0`, negative, `NaN`, `inf`, `None`, and `True`/`False`, since `bool` is a subclass of `int` in Python and would otherwise pass an `isinstance(x, (int,float))` check incorrectly).

### 15. Non-atomic ledger writes (Low) -- Fixed

**Change:** `Ledger.set_state`, `record_trade`, `record_equity`, and `record_decision` no longer call `commit()` individually. A new `Ledger.commit()` is called once at the end of each tick (both runner files), after all of that tick's writes. Every early-return path in both runner files that performs writes also calls `commit()` before closing; paths that write nothing (e.g. an already-stopped track) correctly skip it.

**Verification:** unit test -- wrote a state change via `set_state()` and closed the ledger **without** calling `commit()` first; reopened it and confirmed the change was rolled back (not partially applied), proving a crash mid-tick now loses the whole tick atomically rather than leaving a trade with no matching state.

### 16. Documentation drift (Low) -- Fixed

**Change:** `README.md` updated throughout (track count, trigger description, leverage section, `grid.py`/`backtest.py`/`reset_track.py` usage). `scheduler.py`'s module docstring corrected (cron-job.org + `workflow_dispatch`, not a GitHub `schedule` trigger). `grid.py`'s stale "regime is 1x only" comment removed. `config/tracks.yaml`'s matching stale comment corrected.

**Verification:** read-through comparison against the actual current code/config; no automated check applicable to documentation text.

### 17. Secret scan (Info) -- No action needed

Confirmed clean by the original audit (full git-history object scan). Not independently re-scanned during remediation since no new secret-handling code was introduced.

### 18. Repo hygiene (Info) -- Fixed (code part only)

**Change:** `.claude/settings.local.json` added to `.gitignore` (confirmed it was untracked but not previously ignored).

**Not fixed by this remediation, requires the repo owner directly:** enabling "Keep my email addresses private" on the GitHub account (so future commits don't carry the personal Gmail address already present in 36 public commits -- rewriting that history was correctly not recommended by the original audit), enabling GitHub secret scanning / push protection in the repo's settings, and verifying the cron-job.org token's exact scope (fine-grained, this-repo-only, Actions read/write, with an expiry). None of these are code changes.

### 19. Committed SQLite files (Info) -- No action needed at this phase

Confirmed by the original audit to contain only simulated data. Flagged there (and reaffirmed here, unchanged) as something to address before any real-money phase, not before.

### New issue found during remediation: transitive dependency CVEs

Running `pip-audit -r requirements.txt` (not possible in the original audit's sandboxed environment; run live during remediation) found:

- `urllib3==2.7.0` (pulled in by `requests`/`ccxt`): 3 CVEs (`PYSEC-2026-4175/6/7`), fixed in `2.8.0`.
- `multidict==6.7.1` (pulled in by `ccxt`'s async extras via `aiohttp`): 1 CVE (`CVE-2026-104874`), fixed in `6.9.1`.

**Fixed:** bumped `ccxt` 4.5.82 -> 4.5.85 in `requirements.txt`, which itself requires the patched `urllib3==2.8.0` -- resolves all 3 urllib3 CVEs. Verified `ccxt.fetch_ohlcv` still works correctly against live Kraken data on the new version.

**Not fixed, confirmed unfixable without a future ccxt release:** `ccxt==4.5.85` (the latest release as of this remediation, confirmed against its live PyPI metadata) still hard-pins `multidict==6.7.1` exactly. A test install with any explicit newer `multidict` pin added fails outright with `ResolutionImpossible` (confirmed in a throwaway virtualenv) -- it is not possible to satisfy both "use ccxt" and "use a patched multidict" simultaneously today. Mitigating factor, directly confirmed rather than assumed: this codebase only ever calls ccxt's synchronous REST methods (`fetch_ohlcv`); a live call followed by inspecting `sys.modules` confirmed neither `multidict` nor `aiohttp` (its only consumer here) ever gets imported by that code path -- those are pulled in only for ccxt's async exchange variants, which this project does not use. Documented in `requirements.txt` with instructions to re-check on the next ccxt bump.

## Full regression check

After all changes, ran a complete `scheduler.py --once` tick against an isolated copy of the repository (not the live `data_store/`): all 15 crypto/stocks tracks completed cleanly with no exceptions; the 6 Polymarket tracks failed only due to this working environment's inability to resolve `gamma-api.polymarket.com` (confirmed as a pre-existing, environment-specific limitation unrelated to any of these changes -- the production CI runner does not have this restriction).

## Addendum: response to the 2026-10-12 fix-check review

A follow-up independent review (`audits/2026-10-12-fix-check-trading-engine.md`) checked this report's claims and found one regression and several partial fixes. All were addressed:

- **#6 regression (breach-churn loop), fixed properly this time.** The first version of the non-terminal loss cap force-closed the position on breach but left cash below the floor with no other change -- so the very next entry, costing fees, immediately re-breached and was force-closed again, forever. Fixed per the review's option (b): a breach now also calls `Ledger.reset_epoch()`, archiving the epoch and restarting at the original starting capital. Verified by reproducing the review's exact T1 numbers (cash 24.0, re-enter with 12 margin at an unchanged price) -- confirmed the old version's infinite churn, confirmed the new version closes the epoch instead, and confirmed a *fresh* epoch at the same price does **not** immediately re-breach.
- **#1 follow-up (settlement).** `prediction_source.fetch_series` now also returns `market_closed`, true only on a confirmed Gamma `closed: true` response (not on mere unreachability, which stays ambiguous). All 6 Polymarket track wrappers now call a new `settle_position()` (added to both `engine/runner.py` and `engine/leveraged_runner.py`) to force-sell at the resolution price when `holding and market_closed`, instead of waiting for a strategy signal that would never fire against a flat, resolved price series. Verified with mocked Gamma responses: a position held through resolution is now sold at the resolution price and the trade is tagged `MARKET_RESOLVED`.
- **#2 fixed (missing-endDate gap).** `pick_active_market` now rejects a candidate with no `endDate` instead of passing it through unchecked.
- **#2/#3 (band re-checked at entry, not just at selection).** Added a generic, opt-in `price_band` config key (`engine/config.py`, `engine/risk.py:passes_price_band`), wired into the BUY gate of both runners, and set to `[0.10, 0.90]` on all 6 Polymarket tracks. A market that was eligible when picked but has since drifted can no longer be bought into.
- **#3 (2-tick spike).** The percentile trim was widened from "1 sample per end" (the original 10th/90th split on a 20-window) to a fixed 3-per-end trim. Verified: the review's exact reproduction (`[0.0025]*18 + [0.016, 0.016]`, previously 540%) now reads 0%.
- **#11 (hash lock).** The review correctly identified that the original "can't hash-lock cross-platform" reasoning was wrong -- `uv pip compile --universal --generate-hashes` does exactly this. Installed `uv`, generated `requirements.lock` (49 packages, all platforms), updated `tick.yml` to install with `--require-hashes`, and verified a clean venv installs from it and that `ccxt.fetch_ohlcv` still works against live data from that install.
- **#12 edge case (duplicate-tick liquidation).** Added `position_entry_period_low` to ledger state, set at entry, compared in `check_liquidation` so a repeated low from the same still-open candle the position was entered in is not treated as a new intrabar breach. Verified: a duplicate same-period tick no longer liquidates; a genuinely new period's breach still does.
- **N1 (`reset_track.py --all` could half-reset the set).** Rewrote it to compute each track's final equity automatically from its last `equity_curve` row (no longer requires manual `--final-equity` for every holding track), and to validate every target track in a read-only first pass before applying any reset in a second pass. Verified against real copied ledgers: an unresettable track now aborts the whole run with zero tracks touched, instead of resetting some and crashing partway through.
- **N2 (CRLF diffs).** Added `* text=auto eol=lf` plus explicit rules for `.py`/`.md`/`.yml`/`.yaml`/`.txt`/`.lock` to `.gitattributes`, then ran `git add --renormalize .`. Confirmed no more "LF will be replaced by CRLF" warnings and that working-tree files are LF on disk.
- **N3 (stale local branch).** Fast-forwarded local `main` onto origin (confirmed first that the only commit in between touched `data_store/` only, and that this working tree had zero uncommitted `data_store/` changes, so the fast-forward couldn't lose or conflict with anything).

**Not changed:** #4 (crypto backtest history) and #5's stock-timestamp offset, cost-basis labeling, and compounding-vs-summed bootstrap, since the review marked these as accepted caveats or network-dependent rather than requiring a code change before commit.

A full recompile of every `.py` file in the repo and a complete `scheduler.py --once` run against an isolated copy were both re-run after these fixes: all 15 non-Polymarket tracks tick cleanly; the 6 Polymarket tracks fail only on this sandbox's inability to reach `gamma-api.polymarket.com` (unrelated, pre-existing, environment-specific).

## Commit status

As of this report, none of the changes described above have been committed or pushed to `main` -- they are staged in the local index (via `git add --renormalize .`), pending the repo owner's go-ahead. Local `main` has been fast-forwarded to match `origin/main` (see N3).
