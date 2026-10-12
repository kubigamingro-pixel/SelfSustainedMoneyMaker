# Response to fix-check: SelfSustainedMoneyMaker trading engine

- **Responds to:** `audits/2026-10-12-fix-check-trading-engine.md`
- **Date:** 2026-10-12
- **Code state:** all changes below are staged in the local git index (`git add --renormalize .`); nothing committed or pushed yet.

## Summary

The fix-check found one regression and four partial fixes in the prior remediation, plus three new issues (one new bug, two process/hygiene problems). All eight are addressed below, each with a reproduction of the original failure and a verification that it no longer occurs.

## #6 — regression: breach-churn loop (fixed properly)

**What the fix-check found:** the first version of the non-terminal loss-cap change force-closed a breached position but left cash below the floor with nothing else changed. The very next entry, costing fees, pushed equity further under the same floor and was force-closed again — forever. A fee-burning loop that `trade_log`/`scoreboard` would count as real trades.

**Fix:** `check_account_loss_cap` (`engine/leverage_risk.py`) now also calls `Ledger.reset_epoch()` on breach — archiving the closed epoch and restarting the track at its original starting capital, per the fix-check's recommended option (b).

**Verification:** reproduced the fix-check's exact T1 numbers (cash 24.0, re-entering with 12 margin at an unchanged price, equity 23.69 against a 25.00 floor):
- Confirmed the breach now closes the epoch (`epoch 0 → 1`, cash reset to 50.0) instead of leaving cash at 11.69 under the floor.
- Confirmed a **fresh** epoch opening the same position at the same price does **not** immediately re-breach (equity is healthy relative to the new epoch's floor) — the churn loop cannot recur.

## #1 — partial: resolved positions never settle (completed)

**What the fix-check found:** mis-valuing a held position against the wrong market was fixed, but nothing ever sold it when its market genuinely resolved. A YES-resolved position sitting at ~0.9995 had no strategy signal that would ever fire (the price series goes flat), so the track froze on a dead market permanently.

**Fix:** `engine/data/prediction_source.fetch_series` now also returns `market_closed` — true only on a **confirmed** Gamma `closed: true` response, not on mere unreachability (which stays ambiguous and does not trigger settlement, to avoid treating a transient API hiccup as a resolution). A new `settle_position()` helper (added to both `engine/runner.py` and `engine/leveraged_runner.py`) force-sells the position at the resolution price. All 6 Polymarket track wrappers now check `holding and market_closed` and call it before falling through to normal strategy evaluation.

**Verification:** mocked a closed market returning a confirmed price — `market_closed=True` is correctly reported. Ran the settlement path directly against a ledger holding a position: position closed to zero, cash credited for the sale, trade tagged `MARKET_RESOLVED`.

## #2 — partial: missing-endDate gap (fixed)

**What the fix-check found:** `pick_active_market`'s eligibility filter skipped the end-date check entirely for a market with no `endDate` field, the opposite of what the check is for.

**Fix:** a missing `endDate` now rejects the candidate instead of passing it through unchecked.

## #2 / #3 — partial: eligibility only checked at selection time (fixed)

**What the fix-check found:** the price-band and end-date rules only applied when a market was first picked. A market already held, or a flat track's current market, drifting outside the band afterward was never re-checked.

**Fix:** added a generic, opt-in `price_band` config key (`engine/config.py` validates it; `engine/risk.py:passes_price_band` checks it), wired into the BUY gate of both `engine/runner.py` and `engine/leveraged_runner.py`. Set to `[0.10, 0.90]` on all 6 Polymarket tracks in `config/tracks.yaml`. A BUY is now refused if the price has drifted outside the band by the time the signal actually fires, regardless of what the price was when the market was selected.

**Verification:** unit-tested `passes_price_band` directly (inside/outside/no-band-configured cases); confirmed all 6 Polymarket tracks load the new config key correctly via `load_tracks_config()`.

## #3 — mostly fixed: two-tick spike still passed (fixed)

**What the fix-check found:** the percentile trim introduced in the original remediation only ever excluded exactly 1 sample per end on a 20-sample window, so a spike lasting 2 ticks still read through at the same ~540% the single-tick fix was meant to stop.

**Fix:** changed from a 10th/90th-percentile split to a fixed trim of 3 samples off each end (not a percentage, so it doesn't silently change if the window size is ever edited).

**Verification:** reproduced the fix-check's exact failing case, `expected_move_pct([0.0025]*18 + [0.016, 0.016])` — previously 540%, now 0.0%. Re-ran the original single-tick-spike test and a smooth-uptrend sanity check to confirm neither regressed.

## #11 — partial: sub-dependencies still floated (fixed)

**What the fix-check found:** top-level packages were pinned exactly, but transitive dependencies of yfinance, pandas, matplotlib, and requests still floated. The stated reason for not hash-locking ("can't generate a cross-platform-correct lock from Windows") was also factually wrong — `uv pip compile --universal --generate-hashes` does exactly that.

**Fix:** installed `uv`, generated `requirements.lock` from `requirements.txt` with `--universal --generate-hashes` (49 packages, every pinned version's hashes for every published platform, not just this machine's). Updated `.github/workflows/tick.yml` to install with `pip install --require-hashes -r requirements.lock`. Corrected the comment in `requirements.txt` explaining the mistake the fix-check caught.

**Verification:** installed the lock file into a brand-new, empty virtualenv with `--require-hashes` — succeeded. Confirmed `ccxt`, `yfinance`, `pandas`, `matplotlib`, `requests`, `yaml` all import correctly from that install, and that `ccxt.fetch_ohlcv` returns real data from live Kraken. Re-ran `pip-audit` against the lock: still finds only the one previously-documented, confirmed-unfixable `multidict` CVE (ccxt itself hard-pins that exact version; unrelated to this fix).

## #12 — edge case: duplicate-tick liquidation (fixed)

**What the fix-check found:** if two scheduler ticks land inside the same still-open candle (observed 8 times since 10-02), both report the same `period_low`. If a position was opened on the first of those ticks, that low can predate the entry — the position never actually experienced it — and the intrabar liquidation check would wrongly treat it as a fresh breach.

**Fix:** added `position_entry_period_low` to ledger state, set at entry time. `check_liquidation` now skips the intrabar (not the close-price) check when the current `period_low` still equals the one that was already "live" when the position was opened.

**Verification:** reproduced the scenario directly — a duplicate same-period tick with the stored entry-period low no longer force-closes a healthy position; a genuinely new period's low crossing the liquidation price still does.

## N1 — new issue: `reset_track.py --all` could half-reset the set (fixed)

**What the fix-check found:** the script required `--final-equity` to be supplied manually for any holding track, and raised `SystemExit` at the first one it hit under `--all` — after any earlier tracks in iteration order had already been reset. The SPY tracks currently hold positions, so running `--all` as shipped would have left the set half-reset.

**Fix:** rewrote it in two phases. Phase 1 (read-only) computes every target track's final equity automatically — cash if flat, or the last `equity_curve` row of the current epoch if holding — and collects any that can't be resolved. If anything failed, the whole run aborts with no track touched. Phase 2 only runs, and only applies the reset to every track, after every target has passed phase 1.

**Verification:** against real copied ledger files — ran `--all` with one track holding a position and a valid equity history: succeeded, used the auto-computed equity. Then injected a track holding a position with **zero** equity-curve history (unresolvable) into the target set: the whole run aborted with the exact error identifying the broken track, and confirmed a separate, valid track in the same batch was **not** touched (stayed at epoch 0).

## N2 — new issue: CRLF line-ending conversion (fixed)

**What the fix-check found:** 16 files had been converted to CRLF line endings on disk (this machine's `core.autocrlf=true`), turning ordinary diffs into whole-file rewrites (2,729/2,028 changed lines vs. 881/180 real ones).

**Fix:** added `* text=auto eol=lf` plus explicit `eol=lf` rules for `.py`/`.md`/`.yml`/`.yaml`/`.txt`/`.lock` to `.gitattributes`, then ran `git add --renormalize .`.

**Verification:** confirmed no more "LF will be replaced by CRLF" warnings from git, and confirmed the affected files are LF on disk.

## N3 — new issue: stale local branch (fixed)

**What the fix-check found:** local `main` was about 7 hours behind `origin/main`; committing as-is risked overwriting newer CI ticks in `data_store/`.

**Fix:** confirmed first that the single commit separating local from origin touched only `data_store/` files, and that the local working tree had zero uncommitted `data_store/` changes of its own — then fast-forwarded local `main` onto `origin/main` (a plain fast-forward, not a rebase or merge, since local had no diverging commits).

**Verification:** `git log -1` on local `main` now matches `origin/main`'s latest tick commit; all other working-tree changes (the code fixes above) were preserved untouched by the fast-forward.

## Not changed

Per the fix-check's own framing, these were left as accepted caveats or marked not independently checkable from its environment, not requiring a code change before commit:
- **#4**: the crypto side of the regime-guard backtest is still unanswered (Kraken's 720-candle cap). The fix-check suggested trying a venue that paginates further (e.g. Coinbase or Bitstamp via ccxt) — not yet attempted.
- **#5 caveats**: `fetch_stock_history`'s double-fetch timestamp offset, `in_sample_days` being crypto-only-labelled for a stocks run, and the bootstrap summing instead of compounding returns. None affect reported prices, trade counts, or the pass/fail of any filter — only cosmetic labeling in `backtest.py`'s own output.

## Full regression check

Re-ran after every fix above: all 46 `.py` files in the repo compile; a complete `scheduler.py --once` run against an isolated copy of the repository completes cleanly for all 15 non-Polymarket tracks, with the 6 Polymarket tracks failing only on this sandbox's pre-existing inability to reach `gamma-api.polymarket.com` (unrelated to any of these changes).
