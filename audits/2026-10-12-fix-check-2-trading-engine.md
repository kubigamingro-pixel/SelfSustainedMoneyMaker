# Fix check 2: SelfSustainedMoneyMaker trading engine

- **Date:** 2026-10-12
- **Checks:** `audits/2026-10-12-fix-check-response.md` against `audits/2026-10-12-fix-check-trading-engine.md`
- **Code state:** staged index on local `main` (37 files, +1207/-208, identical with and without line-ending differences). Local `main` = `10ad381c` (tick 15:04Z).
- **Mode:** read-only. Tests ran on scratch copies outside the repo using the engine modules; nothing in the repo or `data_store/` was written apart from this file.

## Verdict: not ready to commit yet, because of one blocker

**Blocker:** `requirements.lock`, `backtest.py` and `reset_track.py` are **untracked**, not staged (`git status`: `?? requirements.lock`, `?? backtest.py`, `?? reset_track.py`, `?? audits/`). `git add --renormalize .` only re-stages files git already tracks; it never adds new ones. The staged `tick.yml` now runs `pip install --require-hashes -r requirements.lock` (line 49) and points the pip cache at it (line 41). Committing the index as it stands would make **every CI tick fail at install**, and README would document scripts that aren't in the repo.

**Fix:** `git add requirements.lock backtest.py reset_track.py` (and `audits/` if you want the reports versioned). Then `git pull` again right before committing, because origin keeps ticking every 15 minutes.

With that done, every item this check covers is fixed or acceptable, and it is ready to commit. Two small follow-ups below (A, B) are recommended but don't block the commit.

## Per item

| Item | Verdict | Evidence |
|---|---|---|
| #6 breach-churn loop | **Fixed** | T1: breach closes epoch 0 → epoch 1, cash back to 50.0. T1b: re-entering at the same price on the next tick is **not** force-closed (qty stays 2.5). Small bookkeeping issue A below |
| #1 resolved positions never settle | **Fixed** | T2: `runner.settle_position` sells at 0.9995 with reason `MARKET_RESOLVED` (cash 9.87, qty 0); the leveraged version returns margin + pnl (cash 49.24, margin 0). All 6 wrappers settle when `holding and market_closed` and return; the next flat tick rotates |
| #2 missing `endDate` passed | **Fixed** | `prediction_source.py:82-84` now skips candidates with no `endDate` |
| #2/#3 rules only at selection | **Fixed** | `risk.passes_price_band` is a BUY gate in both runners (`runner.py:120`, `leveraged_runner.py:119`); `[0.10, 0.90]` is set on all 6 Polymarket tracks; `config.py:59-62` validates it. Tested: 0.05 → blocked, 0.5 → allowed, no band set → allowed |
| #3 two-tick spike | **Fixed** | T3 on 0.0025 base: spikes lasting 1, 2 and 3 ticks all read 0.0%; a smooth uptrend still reads 6.19%. A 4-tick spike still reads 540%, but at those prices the new entry band blocks it anyway |
| #11 transitive dependencies | **Fixed in content; file not staged (blocker)** | `requirements.lock` is a `uv --universal --generate-hashes` lock with 2,216 hashes; tick.yml uses `--require-hashes` |
| #12 duplicate-tick liquidation | **Fixed** | Test: liquidation price 92.0; low 91.0 in the entry's own period → kept (qty 2.5); a new period's low of 91.5 → liquidated (qty 0). Edge: two *different* candles with exactly equal lows also skip the intrabar check; the close-price check still applies (acceptable) |
| N1 `reset_track.py --all` half-resets | **Fixed (read, not run)** | Two phases: `plan_reset` for every target, abort if any error, then `apply_reset`. `--final-equity` refused with `--all`. Holding tracks are valued from the last equity row of the current epoch. Not executed, because it writes to the real `data_store/` |
| N2 line endings | **Fixed in the index** | `git ls-files --eol`: `engine/risk.py` is `i/lf`; staged diff is the same size with and without line-ending differences. Working-tree copies are still CRLF on disk (the index is what gets committed, so this is harmless). New issue B below |
| N3 stale local branch | **Fixed at 15:04Z; will go stale again** | Local `main` = origin tick `10ad381c`. No `data_store/` files are staged (verified). Pull again immediately before committing |

## Recommended follow-ups (not blocking)

**A. Epoch summary records stale cash (Low).** `Ledger.reset_epoch()` reads `get_state()` from the database, but `check_account_loss_cap` calls it with the in-memory state after funding and force-close, before `set_state`. Reproduced in T1: the archived `epochs` row shows `final_cash=50.0` while the real post-close cash was 23.32 (`final_equity` is right because it's passed in). In a live tick the row would also record the pre-close `final_position_qty`. Trades and equity rows are correct; only the summary is wrong.
**Fix:** call `ledger.set_state(state)` just before `ledger.reset_epoch(...)` in `leverage_risk.check_account_loss_cap`.

**B. `.gitattributes` order cancels the explicit `binary` rule (Low).** `* text=auto eol=lf` comes after `*.db binary` / `*.docx binary`, and later lines win. `git check-attr` now reports `text: auto, eol: lf` for `data_store/*.db` and the `.docx`. It is safe today, because git detects them as binary (`i/-text`), but it reverts the "explicit, not heuristic" protection the file's own comment asks for.
**Fix:** move the `*.db binary` and `*.docx binary` lines below the `* text=auto eol=lf` block (and add `*.png binary`).

## Commit checklist
1. `git add requirements.lock backtest.py reset_track.py` (and optionally `audits/`).
2. Optionally apply A and B.
3. `git pull --ff-only origin main`, make sure no `data_store/` file is staged, commit, push between ticks.
4. Watch the first CI tick: the install step with `--require-hashes` and one full scheduler run, including the 6 Polymarket tracks, which have never run against live Gamma with these changes.
5. Then `python reset_track.py --all --reason "rules change: 2026-10-12 remediation"`, commit the ledgers, and push, so the new rules start at a clean epoch.

## Not checked
- Anything needing network: live Gamma (`market_closed`, eligibility), the lock installing on `ubuntu-latest`, `pip-audit`.
- `reset_track.py` and `scheduler.py --once` were not executed, because both write to the real `data_store/`; their logic was read and the engine functions were tested on copies.
- Items the response left unchanged (#4 crypto backtest venue, #5 cosmetic caveats) were not re-checked.
