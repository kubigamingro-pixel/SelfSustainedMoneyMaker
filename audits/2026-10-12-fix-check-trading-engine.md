# Fix check: SelfSustainedMoneyMaker trading engine (improvement, security)

- **Date:** 2026-10-12
- **Checks:** `audits/2026-10-12-remediation-report.md` against `audits/2026-10-12-improvement-security-trading-engine.md`
- **Code state:** uncommitted working tree on local `main` (35 modified files, plus new `backtest.py` and `reset_track.py`). Local `main` is at tick `d5285723` (08:33Z); CI has kept ticking on origin since then.
- **Mode:** read-only. Tests ran against copies in a scratch folder outside the repo; nothing in `data_store/` or the code was touched. This file is the only write.

## Verdict

Most fixes are real and well done (#7, #9, #10, #13, #14, #15 and #16 verified in code, several with my own tests). Three are only partly done (#1, #2, #3). One fix introduced a regression (#6), and that should be fixed before this is committed:

- **#6:** after a leveraged track breaches its loss cap, every new position it opens is force-closed on the very next tick, even if the price hasn't moved. The track turns into a fee-burning loop.
- **#1:** a position held through a market that resolves to a flat price (for example YES at 0.9995) never exits. The track stays frozen on that market.

Separately, deploying these rule changes mid-epoch would mix old-rule and new-rule data in every track's epoch 0. The new `reset_track.py --all` cannot do that reset safely while the SPY tracks hold positions.

## Per-finding results

| # | Claimed | Verdict | Evidence |
|---|---|---|---|
| 1 | Fixed | **Partly fixed**: mis-marking is gone; resolved positions never settle | `prediction_source.py:164-181`; test T2 |
| 2 | Fixed | **Partly fixed**: rules apply only when picking; markets with no `endDate` pass | `prediction_source.py:58-90` |
| 3 | Fixed | **Mostly fixed**: one-tick spike suppressed; a two-tick spike still reads 540% | `risk.py:43-52`; test T3 |
| 4 | Evaluated | **Not reproduced** (no network here); the crypto question is still open | remediation report table |
| 5 | Built | **Fixed, with caveats**: shares `run_tick`; spot only; Kraken history cap; small stock-timestamp offset | `backtest.py` |
| 6 | Fixed | **Regression**: after a breach, every new entry is force-closed the next tick | `leverage_risk.py:128-135`; test T1 |
| 7 | Fixed | **Fixed** | `crypto_source.py:58-66`, `stocks_source.py:69-73` |
| 8 | Fixed (narrowed) | **Partly fixed, reasonably**: crypto/stocks share one fetch per tick; Polymarket does not | `scheduler.py:87-91`, `_cache` in both sources |
| 9 | Fixed | **Fixed** | `ledger.py:213-223`; every branch in both runners records a decision |
| 10 | Fixed | **Fixed**, but `reset_track.py --all` has a gap (see N1) | `ledger.py:319-351`; test T4 on a copy of the real DB |
| 11 | Fixed | **Partly fixed**: top-level pins and action SHAs only; the reason given for skipping hashes is not right | `requirements.txt`, `tick.yml:36-37` |
| 12 | Fixed | **Fixed** (minor edge case) | `leverage_risk.py:95-101`; all 14 leveraged wrappers pass `include_low=True` |
| 13 | Fixed | **Fixed** | `momentum.py:55-56`, `momentum_regime_filtered.py:68-69` |
| 14 | Fixed (partial by design) | **Fixed**; logging staleness only is a fair choice | `risk.py:55-60`, runners check at the top |
| 15 | Fixed | **Fixed** | test T5: an uncommitted write is rolled back on close |
| 16 | Fixed | **Fixed** (spot-checked: README, scheduler docstring, grid, tracks.yaml, PERFORMANCE_REVIEW s.10) | grep for the old phrases finds nothing |
| 17 | n/a | n/a | — |
| 18 | Fixed (code part) | **Fixed** | `.gitignore:9` |
| 19 | n/a | n/a | — |
| New | ccxt bump; multidict left | **Confirmed**: ccxt 4.5.85 hard-pins `multidict==6.7.1` and `urllib3==2.8.0` | `.venv/Lib/site-packages/ccxt-4.5.85.dist-info/METADATA` |

## Tests run (scratch copies, system Python 3.10, pure-engine modules)

- **T1 (#6):** a breached 10x track with cash 24.0 re-enters with 12 margin at price 100. Equity right after entry is 23.69, below the floor of 25. On the next tick, at the **same** price, `check_account_loss_cap` force-closes it ("LOSS_CAP_BREACHED equity=23.6880 floor=25.0000") and charges exit fees. A breached track cannot hold any position until equity gets back above the floor, and it can't get there without holding one.
- **T2 (#1):** `MomentumStrategy.decide(series, holding=True)` on a series that rises and then sits flat at 0.9995 for 40 ticks returns HOLD indefinitely, because the exit needs fast < slow and both SMAs end up equal. The holding branch then keeps re-reading the closed market and never rotates.
- **T3 (#3):** `expected_move_pct([0.0025]*19 + [0.016])` returns 0.0 (fixed). `[0.0025]*18 + [0.016, 0.016]` returns 540.0, which passes. A 5¢ contract moving one cent reads 16.7%, which passes the 10% threshold.
- **T4 (#10):** opening a copy of the real `crypto_momentum_2x.db` with the new `Ledger` migrates it cleanly: `epoch=0`, all 8 trades and 999 equity rows kept, and `trades(0)` returns 8.
- **T5 (#15):** `set_state` + `record_trade`, then `close()` without `commit()`, and reopen: cash and trades are unchanged, so the tick rolled back as a whole.
- Every `.py` file in the repo parses (AST check).

## Details for anything not fully fixed

### #6 regression: breach-churn loop (High)
`check_account_loss_cap` now force-closes whenever `equity < floor and position_qty > 0`, with no memory that the track already breached. After the first breach, equity is below the floor by definition, so any new entry (which costs fees and puts equity even further under) is force-closed on the next tick. The result is a stream of fee-only round trips that `trade_log`/`scoreboard` will count as strategy trades.

**Change (pick one):**
- (a) Trigger the breach only on the crossing: record the floor breach once, then re-arm the floor at a level relative to post-breach equity.
- (b) Make a breach close the epoch automatically (`reset_epoch(reason="LOSS_CAP_BREACHED")`), so each epoch has a clean floor.
- (c) After a breach, stop applying the account check for the rest of the epoch and keep only per-position liquidation.

Option (b) fits the new epoch model best.

### #1 partial: held positions on resolved markets never settle (High)
Mis-marking against a different market is fixed: a holding track no longer rotates. But there is still no settlement step. When the market closes while the track holds a position, nothing sells it at the resolution price, so a YES-resolved position stays open at about 1.0 forever, and the track never rotates to a new market. Leveraged tracks also keep paying funding on it. My original recommendation also included storing the market id with the position and asserting on it; that was not added.

**Change:** in the `holding and closed` branch, return a "resolved" signal that the runner turns into `SELL reason=MARKET_RESOLVED` at the final price. Then let the next tick rotate as a flat track.

### #2 partial (Medium)
`pick_active_market` filters by price band and end date, but only when picking. A market that is already held, or a flat track's current market, is never re-checked, so a market that drifts to 0.02 or goes in-play stays tracked until `closed`. The current market (`0x5262…`) was picked under the old rules. Markets with no `endDate` skip the date check entirely.

**Change:** also require the price to be inside the band at entry (a BUY gate in the Polymarket wrappers or the runner), and rotate a *flat* track whose market leaves the rules.

### #3 mostly fixed (Medium)
The 10th/90th percentile trim over a 20-sample window removes only one sample at the top, so a spike lasting two ticks still passes. The % range is also still relative, so cheap contracts pass on a single price increment.

**Change:** gating entries by price band (see #2) covers most of this. Otherwise use absolute probability points for Polymarket, or a wider trim.

### #4 not reproduced (Info)
The SPY result (86 → 40 trades, regime slightly ahead, confidence interval straddling 0) looks plausible, but I couldn't re-run it without network. One thing to check: yfinance serves 15-minute bars for only about 60 days, so an "84d" span suggests a different interval was used; state which one. The crypto question remains unanswered: with Kraken's 720-candle cap, the backtest window is 7 days. Use `--exchange` with a venue that paginates history (for example Coinbase or Bitstamp via ccxt) to get months of BTC/ETH.

### #5 caveats (Low)
- `fetch_stock_history` fetches twice. The first call drops the in-progress bar; the second does not. Pairing `timestamps[-len(closes):]` with `closes` then labels each close with the *next* bar's timestamp during market hours. Prices are unaffected; only the timestamps and the out-of-sample split shift by one bar.
- `in_sample_days` uses `args.days`, which is crypto-only (default 180), so it is mislabelled for stocks.
- The bootstrap sums % returns instead of compounding them. That's fine as a noise check.

### #8 narrowed scope (accepted)
Not sharing Polymarket fetches is a sound call given the per-track holding rule. Plain and regime Polymarket tracks can therefore still end up on different markets, so keep reading Polymarket plain-vs-regime comparisons with that in mind.

### #11 partial (Low)
- Top-level packages are pinned exactly. ccxt itself pins its whole tree exactly (verified in the installed metadata), but transitive dependencies of yfinance, pandas, matplotlib and requests still float.
- The reason given for skipping hashes is not right. `pip-compile --generate-hashes` records hashes for every published file of a pinned version, not only this machine's wheel. The real cross-platform issue is platform-specific dependencies (the `tzdata` win32 marker), which `uv pip compile --universal --generate-hashes`, or compiling inside a Linux container, handles.
- The two action SHAs look well-formed, but I could not resolve them against GitHub from here.

### #12 edge case (Low)
If two ticks fall within the same 15-minute candle (8 such gaps were seen since 10-02), the second tick reuses the same closed candle's low. That low can predate an entry made on the first tick and liquidate the position on a price it never experienced. Skip the intrabar check when the low's candle closed before the position's entry time.

## New issues found in this check

| # | Severity | Issue | Evidence | Change |
|---|---|---|---|---|
| N1 | Medium | Deploying mid-epoch mixes rule sets: closed candles, state exits, the new cost-filter formula and the non-terminal cap would all apply partway through every track's epoch 0. `reset_track.py --all` can't do the reset safely: one `--final-equity` is applied to every track, and without it the script raises `SystemExit` at the first holding track (SPY tracks currently hold positions) **after earlier tracks are already reset**, leaving the set half reset | `reset_track.py:33-40,63-65`; `stocks_momentum*` state `position_qty>0` | Compute each track's final equity from its last `equity_curve` row, check every track before resetting any, then run one `--all` reset tagged "rules change: 2026-10-12 remediation" in the same commit/deploy |
| N2 | Low | 16 files were converted to CRLF line endings, so the diff shows whole-file rewrites (2,729/2,028 lines vs 881/180 when line endings are ignored) | `file …` reports CRLF; `git diff --stat` vs `--ignore-cr-at-eol` | Add `*.py text eol=lf` (and `*.md`, `*.yml`) to `.gitattributes`, renormalize, commit |
| N3 | Info | Local `main` is about 7h behind origin and every change is uncommitted. Committing the local `data_store/` would overwrite newer CI ticks | `git log -1` = 08:33Z tick | `git pull --rebase` first, commit code only (not `data_store/`), push between ticks |

## Recommended next steps (for the VS Code session, after owner approval)

1. Fix the #6 churn loop (option b: a breach closes the epoch).
2. Add `MARKET_RESOLVED` settlement for #1, and the entry-time price-band gate for #2/#3.
3. Fix `reset_track.py` (per-track final equity, check everything before resetting any track), add `.gitattributes` for line endings.
4. Pull, commit the code without `data_store/`, then run `reset_track.py --all --reason "rules change: 2026-10-12 remediation"` once and push.
5. Re-run the regime backtest on a crypto venue with long history.

## Not checked
- Anything needing network: the backtest numbers in #4, live Polymarket eligibility, the action SHAs, `pip-audit`.
- The full `scheduler.py --once` run. It writes to the real `data_store/` paths, so I tested the engine functions directly on scratch copies instead.
- `backtest.py`'s command-line run and printed output, which need ccxt/yfinance and network.
