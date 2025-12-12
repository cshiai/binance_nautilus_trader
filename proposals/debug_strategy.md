## Executive Summary (6–10 lines)

- The current LPI concept is directionally sensible, but it is likely **not profitable** because it triggers **exactly when adverse selection is maximal** (catching the falling knife) while assuming fills/fees that are too optimistic for liquidation regimes.  
- The LPI math mixes **incompatible clocks** (100ms `agg_trades` vs slow `open_interest`) and uses an **OI-delta normalization** that can be numerically unstable and regime-skewed.  
- The implementation pseudo-code departs materially from the formal spec (e.g., FSI denominator), which can flip the meaning of “liquidation intensity.”  
- The strategy is under-specified on **horizon alignment**: LPI is ultra-short horizon, while mean reversion may only become positive after a **post-cascade stabilization condition**.  
- A revised approach should split into two implementable variants: (1) **Cascade Momentum / Breakout** (ride the forced-flow regime) and (2) **Post-Exhaustion Mean Reversion** (only after forced flow measurably decays).  
- Key upgrades: (i) replace OI gating with **event-time liquidation proxies** robust at 100ms–1s, (ii) add an explicit **exhaustion detector**, (iii) enforce **execution-aware entry logic** (maker-only but conditional), (iv) volatility-targeted sizing with hard kill-switches.  
- The backtest must be **event-driven**, include latency/queue conservatism, and run ablations to prove each component is necessary (fees, latency, spread, depth thinning, regime filters).  

---

## 1) What I Read (Grounding Summary)

### Minimal outline — only sections relevant to performance & constraints

**GUIDELINE_DOC (“From Noise to Signal…”)**
- **Research Cycle / Microstructure framing**: observation → hypothesis → data philology → formalization → validation (Figure “THE QUANTITATIVE RESEARCH CYCLE”).  
- **Time base guidance**: crypto “day” is arbitrary; prefer **volume time / trade time** for volatility modeling (Figure “SUBORDINATION OF TIME (Clark, 1973)”).  
- **Feature engineering constraints**: align `agg_trades`, `depth`, funding/open interest; microstructure noise (bid-ask bounce), realized variance; stationarity / fractional differentiation.  
- **Execution realism**: backtesting traps include **kline look-ahead**, **latency**, **queue position**, fees; need purged CV + deflated Sharpe logic; “reality gap” figure.

**STRATEGY_DOC (“Quantifying the Liquidity Spiral… LPI”)**
- **Narrative + goal**: detect liquidation cascades and exploit dislocation; LPI to distinguish forced selling vs informed repricing (“Anatomy of a Crash,” “V bottom” diagram).  
- **Math specification**: LPI is composite of **FSI** (forced selling intensity) + **LDR** (liquidity depletion ratio), each z-scored (Section “Mathematical Specification”).  
- **Implementation**: event-driven, 100ms “alpha ticks,” last-known-state book, Welford online z-scores; pseudo-code computes impact volume vs best bid; OI updates slow (Section “Algorithmic Implementation”).  
- **Validation methodology**: maker-only preference due to taker fees; queue simulation; latency penalty; markout; DSR requirement; stress periods May 19, 2021 and Nov 2022 (Section “Validation Methodology”).  

### Hard constraints inferred from GUIDELINE_DOC (explicit)

These are *design constraints/philosophy* rather than hard numeric limits:

- **Data universe**: strategies should be implementable primarily from **Binance public market data** (`agg_trades`, `depth`, tickers; optionally funding/open interest). (GUIDELINE_DOC “The Binance Laboratory”, “Data Philology”).  
- **Clock choice**: avoid arbitrary daily boundaries; prefer **event/volume time** for intraday volatility/liquidity research. (GUIDELINE_DOC “Volume as the Clock”).  
- **Backtest discipline**: must avoid look-ahead (especially with OHLC klines), include **latency + queue position**, and model fees/slippage explicitly. (GUIDELINE_DOC “Chronology Trap”, “Latency and Queue Position”).  
- **Validation discipline**: use **purged CV** for clustered volatility; use **deflated Sharpe** to penalize multiple tries. (GUIDELINE_DOC “Defeating Overfitting”).  
- **Execution reality**: maker/taker economics dominate at high turnover; you can’t assume fills at touch; include adverse selection / markout. (GUIDELINE_DOC “Transaction Cost Modeling”, “Reality Gap”).  

### Anchor points to STRATEGY_DOC (where key claims come from)

- LPI math definition (FSI, LDR, z-scoring): **STRATEGY_DOC §“Mathematical Specification”**.  
- 100ms alpha ticks + last-known-state buffer: **STRATEGY_DOC §“Data Synchronization and State Management”**.  
- Pseudo-code specifics (impact threshold, OI weighting, sweep-cost proxy for LDR): **STRATEGY_DOC Figure “Core logic for the Liquidation Pressure Index”**.  
- Maker-only validation + queue model + latency penalty + markout: **STRATEGY_DOC §“Validation Methodology”**.  
- Stress tests and survival constraint: **STRATEGY_DOC §“Stress Testing: The ‘Black Swan’ Protocol”**.  

---

## 2) Diagnosis: Why Profitability Failed (ranked)

Below are **8 plausible root causes** (≥6 required), ranked by expected likelihood × impact, each with a **concrete test**.

### 1) Adverse selection at the signal peak (buying into toxic flow)
**Why likely:** The LPI is designed to spike during the cascade (“MAX PAIN… -> BUY SIGNAL” in STRATEGY_DOC “Signal Visualization”). That is *exactly* when selling is still aggressive, spreads are wide, and fills are most toxic.

**Test / metric**
- Compute **post-fill markout** conditioned on LPI deciles:  
  - Markout at 250ms / 1s / 5s after fill for maker fills.  
  - If markout is negative in high-LPI states, you’re providing liquidity to informed/forced flow (winner’s curse noted in STRATEGY_DOC “Winner’s Curse”).

### 2) Horizon mismatch: LPI is 100ms–1s, mean reversion edge is minutes
**Why likely:** LPI is computed on 100ms buckets (STRATEGY_DOC “Alpha Ticks”), but the “exhaustion / recovery” is described conceptually, not operationally. If you enter too early, you repeatedly catch the knife.

**Test / metric**
- Measure conditional returns **as a function of holding period**: 0.5s, 1s, 5s, 30s, 5m, 30m after a signal.  
- Look for **sign change**: negative at short horizons but positive at longer horizons → you need an explicit “exhaustion confirmation” gate.

### 3) `open_interest` is too slow / asynchronous for the FSI definition
**Why likely:** STRATEGY_DOC acknowledges OI updates are “approx every 1–5 mins or on event.” Using OI deltas to normalize 100ms forced selling intensity can create a noisy, stale, or misleading scaling.

**Test / metric**
- Correlate your “OI delta per 100ms” proxy with:
  - realized liquidation stream (if you have it) or  
  - longer-window OI changes.  
- Evaluate signal performance **with OI removed** (pure trade+book proxy). If performance improves, OI gating is hurting.

### 4) Spec/implementation divergence (FSI denominator & weighting inconsistency)
**Why likely:** In math spec,  
- \(FSI_t\) divides by \(OI_{t-\Delta} - OI_t + \epsilon\).  
But in pseudo-code, it computes `raw_fsi = impact_vol * liquidation_weight` with `liquidation_weight = 1 + (oi_delta / current_oi * 100)`—this is not the same object and can blow up or skew strongly.

**Test / metric**
- Implement **both** versions side-by-side on the same data:
  - Spec-FSI vs Code-FSI.  
- Compare distributions, stability (outlier frequency), and downstream PnL attribution.  
- If results differ materially, you likely debug a “strategy definition drift” problem, not an alpha problem.

### 5) Transaction costs + spread widening overwhelm edge (maker fills not actually “cheap”)
**Why likely:** During cascades, spreads widen and queue priority matters. Maker may still experience:
- missed fills (opportunity loss), or  
- “instant fills” that are adverse selection (toxic).  
GUIDELINE_DOC emphasizes this reality gap.

**Test / metric**
- Build a cost decomposition by regime:
  - effective spread paid/earned,
  - fees,
  - adverse selection (markout),
  - missed-trade rate.  
- Sensitivity: multiply fees and slippage by 1×/2×/3×. If PnL collapses quickly, the “paper edge” is too thin.

### 6) LDR proxy may not match the true “liquidity depletion” you trade against
**Why likely:** Math spec defines depth within 1% and spread, but pseudo-code uses `cost_to_sweep(qty=10.0)` normalized by best bid. Those can disagree across assets and regimes; a fixed “10 BTC” is not cross-sectionally consistent.

**Test / metric**
- Replace LDR with two alternatives and ablate:
  1) depth-at-1% in notional terms,
  2) depth-at-X bps (e.g., 25 bps) normalized by ADV.  
- Check which variant yields more stable predictive power and better execution compatibility.

### 7) Normalization / z-scoring window is regime-misaligned (leaks slow drift into fast signal)
**Why likely:** STRATEGY_DOC uses Welford with `window_size=86400` (seconds?)—but LPI is computed per 100ms tick. If “window” is misinterpreted, you can end up with either:
- too short window (overreactive z-scores), or  
- too long (z-scores lag, making peaks late).

**Test / metric**
- Audit the exact units of the rolling window.  
- Sweep normalization half-life: 5m, 30m, 2h, 24h.  
- Evaluate calibration: does “3-sigma” occur at reasonable frequency (e.g., not 5% of time)?

### 8) Strategy crowding / reflexivity (signals become self-defeating in the most obvious regimes)
**Why plausible:** LPI-like logic is intuitive; competitors may fade/arb it, especially on majors. Crowd effects can turn mean reversion into continuation.

**Test / metric**
- Compare performance across:
  - major vs mid-tail symbols,
  - periods of high retail participation (proxied by funding extremes),
  - different liquidity tiers.  
- If edge exists only in illiquid tail (but is untradeable), you need capacity-aware constraints.

---

## 3) Revised Strategy Overview (high level)

A single “LPI → buy” rule is too coarse. The revised approach uses **two strategy variants** driven by the same LPI family, chosen by **regime classification**:

1) **Variant A — Cascade Momentum (Trend in liquidation cascades)**  
   - Hypothesis: when forced-flow is active *and not yet exhausting*, price impact persists (liquidity spiral).  
   - Trade: short with the cascade (or long in short-squeeze cascades), small duration, strict risk caps, execution tolerant of taker only when spread is acceptable (otherwise maker).

2) **Variant B — Post-Exhaustion Mean Reversion (after forced flow)**  
   - Hypothesis: once forced selling **decelerates** and liquidity begins to reappear, the remaining move is dominated by dislocation and should mean-revert.  
   - Trade: provide liquidity (maker-first) **only after** an explicit exhaustion condition; enter in tranches; exit quickly if toxicity resumes.

Shared upgrades:
- Replace “OI delta per 100ms” with a **two-layer LPI**:
  - **Fast LPI**: purely from `agg_trades` + `depth` (100ms–1s).  
  - **Slow Leverage Regime**: from funding + OI (minutes–hours) used only as a **regime prior**, not as a denominator in a fast signal.
- Add an explicit **Exhaustion Detector** to separate “cascade active” from “cascade ending.”
- Use **volatility targeting + exposure caps + kill switches** aligned with GUIDELINE_DOC execution realism.

---

## 4) Signal Construction (math + intuition)

### Data inputs (implementable with standard Binance market + derivatives data)
- Trades: `Futures.agg_trades` (direction via `m` flag per STRATEGY_DOC pseudo-code).  
- Book: `depth` or `diff_depth` reconstructed local order book; best bid/ask + depth ladder.  
- Optional slow regime: `funding_rate`, `open_interest` (do **not** require tick alignment).

### Core definitions (≤6 key equations total)

**(Eq 1) Impact Sell Notional (fast, event-time)**
\[
IS_t = \sum_{k\in \mathcal{T}_t} \big(q_k p_k\big)\,\mathbf{1}\{d_k=-1\}\,\mathbf{1}\{p_k < b_{t^-}(1-\eta)\}
\]
- Intuition: count only aggressive sells that *penetrate* below the prior best bid by \(\eta\) (impact threshold).

**(Eq 2) Impact Buy Notional (symmetry for short-squeezes)**
\[
IB_t = \sum_{k\in \mathcal{T}_t} \big(q_k p_k\big)\,\mathbf{1}\{d_k=+1\}\,\mathbf{1}\{p_k > a_{t^-}(1+\eta)\}
\]

**(Eq 3) Liquidity Depletion (depth-normalized)**
\[
LD_t = \frac{s_t}{\text{Depth}^{\$}_{t}(X\text{ bps})+\epsilon},\quad s_t=a_t-b_t
\]
- Use **notional depth** available within \(X\) bps of touch on the side you care about; \(X\) typically 25–100 bps (must be tested).  
- This fixes the cross-sectional inconsistency of “sweep 10 BTC.”

**(Eq 4) Fast LPI (signed)**
\[
LPI^{fast}_t = Z(IS_t - IB_t) \;+\; \lambda_{LD}\, Z(LD_t)
\]
- Signed pressure: positive means net forced selling pressure; negative means net forced buying pressure.  
- Z-scoring is over a *short* rolling window (e.g., 10–60 minutes in event-time buckets), not 24h by default.

**(Eq 5) Exhaustion Score (detect deceleration of pressure)**
\[
EXH_t = Z\!\Big(-\Delta LPI^{fast}_t\Big) \;+\; \lambda_{liq}\, Z\!\Big(\Delta \text{Depth}^{\$}_{t}(X\text{ bps})\Big)
\]
- Meaning: exhaustion rises when pressure stops increasing (or starts falling) *and* depth starts to recover.

**(Eq 6) Slow Leverage Regime (prior, not tick-synced)**
\[
LEV_t = Z(funding_t) \;+\; \lambda_{OI}\, Z(\Delta OI_t)
\]
- Use as a regime filter: e.g., mean reversion after long-liquidations is more plausible when leverage was crowded-long beforehand.

### Signal processing rules (practical, implementable)

- **Clocking**
  - Compute everything on **alpha ticks** (e.g., 250ms or 1s), not necessarily 100ms, to reduce microstructure noise unless you truly need 100ms.  
  - Maintain last-known book state as in STRATEGY_DOC “Last-Known-State buffer.”

- **Z-scoring**
  - Use robust z-score: median/MAD or winsorized mean/std.  
  - Winsorize raw inputs (IS, IB, LD) at e.g. 99.5th percentile per symbol.

- **Gating conditions (must pass to trade)**
  - Spread gate: \(s_t / mid_t < s_{max}\) (symbol-specific).  
  - Depth gate: \(\text{Depth}^{\$}_{t}(X\text{ bps}) > D_{min}\).  
  - Latency gate (GUIDELINE_DOC): measured live latency below threshold; in backtest simulate with penalty.

### Two tradable variants from the same signals

**Variant A: Cascade Momentum (continuation)**
- Condition: \(|LPI^{fast}_t|\) high **and** exhaustion low.  
- Direction: trade **with** the sign of pressure:
  - If \(LPI^{fast}_t\) is strongly positive → short (forced sell cascade).  
  - If strongly negative → long (short squeeze cascade).  
- Exit quickly on:
  - time stop (seconds to a few minutes), or  
  - \(EXH_t\) crossing up (signaling exhaustion), or  
  - spread exploding / depth collapsing beyond thresholds.

**Variant B: Post-Exhaustion Mean Reversion**
- Condition: \(|LPI^{fast}_t|\) high **then** \(EXH_t\) high (pressure decelerates + depth recovers).  
- Direction: trade **against** the prior pressure:
  - After sell cascade exhaustion → long mean reversion.  
  - After buy squeeze exhaustion → short mean reversion.  
- Stronger when slow leverage prior \(LEV_t\) indicates crowded positioning consistent with forced unwind.

---

## 5) Portfolio Construction & Risk Model

### Universe
- Binance USD-margined perpetuals (consistent with STRATEGY_DOC focus on Binance Futures).  
- Start with top-liquidity symbols; expand only after execution quality is proven.

### Position sizing (vol-targeted, capped)
- Compute per-symbol realized vol on alpha ticks (event-time RV proxy per GUIDELINE_DOC realized variance discussion).  
- Size by:
  - **risk parity-ish**: target equal contribution to risk,  
  - **hard caps**: max notional per symbol, max gross exposure, max leverage.

### Constraints (recommended defaults; must be tuned)
- **Gross exposure cap**: prevent portfolio blow-up during market-wide cascades.  
- **Concentration**: max weight per symbol.  
- **Correlation control**: reduce when BTC beta dominates (GUIDELINE_DOC suggests orthogonalization; at minimum enforce BTC exposure cap).  
- **Turnover control**: max trades per minute per symbol; avoid flip-flopping.

### Risk model (microstructure-aware)
- Regime-based risk scaling:
  - In extreme LPI regimes, reduce base risk unless you are explicitly running Variant A with short holding periods.  
- “Survival constraint” consistent with STRATEGY_DOC stress concept:
  - daily loss limit, weekly loss limit, and event-day kill switch.

---

## 6) Execution Model & Microstructure Considerations

### Core principle
Your alpha is strongest when microstructure is worst—so execution must decide **when not to trade**.

### Order types & logic (two-mode execution)

**For Variant A (Momentum)**
- You may need **taker** sometimes, but only when spread is below a threshold and book is not hollow.
- Execution choice:
  - If spread tight + depth adequate → taker for immediacy (small size).  
  - Else → do not trade (or place maker “chase” only if you accept missed trades).

**For Variant B (Mean Reversion)**
- Maker-first, *but not naïve maker*:
  - Place passive orders **behind** the touch (price improvement), tranche entries.  
  - Cancel if toxicity resumes (LPI re-accelerates) to avoid being run over.

### Queue simulation (backtest realism)
Use STRATEGY_DOC’s conservative volume-depletion idea, but make it continuous:
- Estimate \(Q_{ahead}\) from depth at your price level.  
- Fill only if traded volume through that level exceeds \(Q_{ahead}\), not just touch.

### Latency penalty & markout (mandatory)
- Apply a fixed + stochastic latency shift (50–150ms per STRATEGY_DOC; GUIDELINE_DOC notes 50–200ms).  
- Track markout to detect adverse selection:
  - If immediate fills have negative markout, tighten gates or shift to “post-exhaustion only.”

### Cost model
- Include:
  - maker/taker fees (STRATEGY_DOC notes taker 4 bps; GUIDELINE_DOC mentions maker 2 bps / taker 4 bps),  
  - spread costs,  
  - slippage (modeled from next-tick trade-through),  
  - cancel/replace impact if relevant.

---

## 7) Backtest & Validation Plan (incl. ablations)

### Event-driven simulation (EDS)
Follow STRATEGY_DOC validation stance: do not use vectorized candle backtests for 100ms–1s strategies.

**Core requirements**
- Reconstruct local order book from depth updates (or snapshots at a fixed cadence).  
- Consume `agg_trades` in time buckets (250ms/1s) and apply latency shifts.  
- Simulate order placement, queue position, partial fills, cancels.

### Splits / walk-forward
- Use walk-forward by calendar time (because regimes cluster; GUIDELINE_DOC).  
- Purged splits around high-vol events (avoid leakage from overlapping labels).

### Metrics (beyond Sharpe)
- Fill rate, cancel rate, average queue time.  
- Markout at 250ms/1s/5s.  
- Tail risk: worst 1% trade, worst day, drawdown, exposure during market crashes.  
- Capacity proxies: PnL per unit notional vs size (slippage curve).

### Ablations (to locate the “why”)
At minimum:
1) **No LDR term**: set \(\lambda_{LD}=0\).  
2) **No exhaustion detector**: trade directly on \(LPI^{fast}\).  
3) **No slow leverage prior**: remove \(LEV_t\) gating.  
4) **Maker-only vs mixed** (Variant A likely needs mixed; Variant B maker-only).  
5) Latency sensitivity: 0ms vs 50ms vs 150ms.  
6) Cost sensitivity: 1×/2×/3× fees+slippage.

### Falsifiable predictions (what “should” happen if logic is right)
- Variant A: conditional returns should be positive **immediately** (seconds) but degrade with wider spreads.  
- Variant B: returns may be negative immediately, then turn positive after exhaustion; markout should improve materially once \(EXH_t\) is required.

---

## 8) Failure Modes, Guardrails, and Monitoring

### Failure modes
- **Falling knife**: exhaustion false-positive; cascade continues.  
- **Whale rotation**: large trades without forced liquidation (STRATEGY_DOC “Fake-Out”).  
- **Spread regime breaks**: book disappears; maker fills become toxic.  
- **Exchange microstructure changes**: fee tiers, matching engine behavior, feed quality.  
- **Crowding / reflexivity**: signal becomes widely exploited and decays.

### Guardrails (hard rules)
- Kill switch if:
  - spread > threshold,  
  - depth < threshold,  
  - markout over last N trades < negative threshold,  
  - realized slippage > cap,  
  - portfolio drawdown > limit.
- Position caps during market-wide stress:
  - if BTC LPI extreme, reduce alt exposure (correlation spike).

### Live monitoring (minimal, strategy-specific)
- Real-time: LPI, EXH, spread, depth, fill rate, markout, latency.  
- Daily: performance attribution by regime and by component (IS vs LD vs EXH).

---

## 9) Implementation Notes (data, pseudocode, edge cases)

### Data handling (practical)
- Use WebSocket for:
  - `aggTrade` stream (or equivalent),  
  - `diffDepth` for order book maintenance.  
- Use REST occasionally for:
  - snapshot initialization,  
  - funding/OI updates (slow regime).

### Pseudocode sketch (core loop; implementation-oriented)

- Maintain:
  - rolling bucket of trades for the last \(\Delta\) (e.g., 1s),
  - local order book with best bid/ask and depth-within-X-bps in notional,
  - robust rolling stats objects for Z-scores (short-window), separately for IS-IB and LD,
  - exhaustion stats.

- Per alpha tick:
  1) compute IS, IB using last-known best bid/ask before each trade batch,  
  2) compute LD from current spread and depth-within-X-bps,  
  3) compute \(LPI^{fast}\) and \(EXH\),  
  4) decide regime:
     - if \(|LPI^{fast}|\) high & \(EXH\) low → Variant A,  
     - if \(|LPI^{fast}|\) high & \(EXH\) high → Variant B,  
     - else no trade,  
  5) apply execution gates (spread/depth/latency),  
  6) place/cancel/adjust orders; log all states for attribution.

### Edge cases to explicitly handle
- **Book drift / desync**: periodic snapshot resync; drop trades while book invalid.  
- **Trade direction inference**: Binance `m` flag semantics must be consistent (buyer is maker implies seller aggressor); validate.  
- **Outliers**: clamp IS/IB and LD to avoid z-score explosions.  
- **Cross-symbol normalization**: define depth in **notional** terms and normalize by ADV or median depth to compare signals across symbols.  
- **OI update gaps**: treat \(LEV_t\) as “last known” and do not use it in fast denominators.

### One ASCII diagram (pipeline; kept compact)

```text
[agg_trades] + [local book depth]  --->  IS/IB + LD  --->  LPI_fast  --->  EXH
       |                                |                 |             |
       |                                |                 |             |
       +--> (slow) funding/OI ---> LEV prior              +--> Regime select:
                                                     (A) cascade momentum
                                                     (B) post-exhaust mean rev
```

(Using only 1 diagram total; within the “≤2” limit.)

---

## 10) Open Assumptions (explicit list)

I will not guess these—each needs confirmation or a controlled design choice:

1) **What exactly is “not profitable”?**  
   - Net of fees? net of slippage? live vs backtest? timeframe? universe?  

2) **Current trading rule mapping from LPI to positions**  
   - STRATEGY_DOC defines LPI and discusses “BUY SIGNAL,” but does not fully specify the production decision rule, holding period, exits, and sizing.

3) **Actual execution mode in production**  
   - Maker-only as intended (STRATEGY_DOC) or mixed?  
   - Actual observed fill rates and cancel/replace constraints?

4) **Fee tier and rebates**  
   - Maker/taker fees vary by VIP level; need the *actual* applied fees (GUIDELINE_DOC warns about assuming VIP tiers).

5) **Order book data source**  
   - Are you reconstructing full book from `diff_depth` or polling snapshots? Depth quality drives LDR/queue simulation validity.

6) **Alpha tick resolution**  
   - Are you truly running 100ms decisions, or could you move to 250ms/1s without losing edge (often improves noise/cost balance)?

7) **Availability of liquidation-specific streams**  
   - STRATEGY_DOC assumes inferring from raw trades “for robustness.” If liquidation streams are available, we can use them for *validation labels* even if not used in the signal.

8) **Universe & leverage constraints**  
   - Max leverage, per-symbol caps, and whether shorting is always allowed across chosen perps.

9) **Risk objective**  
   - Is the goal absolute return, market-neutral, or BTC-beta-contained? This impacts hedging/constraints.

---

If you paste (or summarize) the **current exact entry/exit rules and sizing**, plus **a single week of logs** (timestamps, LPI, orders, fills, markouts), I can turn the diagnosis above into a precise “this failed because X” ranking backed by measured attribution, and parameterize Variant A/B into a concrete spec ready to implement.