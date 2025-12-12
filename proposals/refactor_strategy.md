## Revision: a **workable** LPI / volatility strategy (that’s not just “buy the bottom”)

Your current LPI idea is directionally correct (liquidations create *mechanical* flow and temporary dislocations), but most unprofitable implementations fail for one of four reasons:

1. **Misclassification:** “high sell volume” ≠ “forced selling.” You need an *identification layer*.
2. **Bad timing:** the max-LPI moment is often *still toxic*; mean reversion starts when **selling exhausts**, not when panic begins.
3. **Wrong instrument/fees:** if you need taker fills during cascades, the edge is usually gone (fees + adverse selection).
4. **No state model:** sometimes it’s *informed repricing* (trend), not liquidation mean reversion (revert).

So the revision below is a **state-based mixture strategy**:

- **Mode A (Forced-flow exhaustion → mean reversion):** provide liquidity *after* liquidation pressure peaks and toxicity decays.
- **Mode B (Informed-flow / structural break → trend continuation or “don’t fade”):** if it’s not forced, do *not* fade.

This is still not a guarantee of profitability, but it is the kind of design that can survive contact with microstructure.

---

# 1) Data upgrades (critical)

## 1.1 Use *explicit liquidation data* if at all possible
Your earlier doc says “Binance does not explicitly flag liquidations in agg_trades” and infers them. In practice, inference is fragile.

**Revision:** treat liquidation inference as a fallback, not primary.

- Prefer a dedicated liquidation feed (Binance Futures exposes liquidation order events via websocket streams; naming varies by API version).  
- If you can’t use it, use a stricter proxy requiring **OI drops + impact trades + spread/depth shock**.

## 1.2 Minimum feature set (per symbol)
At time \(t\), sample on **alpha ticks** (e.g., 100ms or 250ms):

- Trades: \(\{(p_k, q_k, d_k, m_k)\}\) in window \([t-\Delta,t]\) from `agg_trades`
- Book: best bid/ask and near-depth from `depth` / `diff_depth`
- Open interest: \(OI_t\) (`open_interest`)
- Funding: \(F_t\) (`funding_rate`) (slow; treat as regime variable, not tick variable)

---

# 2) A stricter, *microstructure-correct* Liquidity Pressure Index

The goal is to estimate **forced-flow intensity relative to liquidity**.

## 2.1 Definitions

### Mid, spread, microprice
Let:
\[
m_t = \frac{a_t + b_t}{2}, \quad s_t = a_t - b_t
\]

Optional microprice (useful when imbalance matters):
\[
\tilde{m}_t = \frac{a_t Q^{bid}_t + b_t Q^{ask}_t}{Q^{bid}_t + Q^{ask}_t}
\]

### Depth available to absorb sells
Define bid-side depth within a band (e.g., 10 bps or 25 bps):
\[
D^{bid}_t(\eta) = \sum_{j:\; p^{bid}_j \ge b_t(1-\eta)} q^{bid}_j
\]

### Impact-sell volume (to separate “rotation at touch” vs “panic through book”)
For trades in the last \(\Delta\) seconds, define sell aggressor indicator:

- In Binance `agg_trades`, field `m=True` typically indicates **buyer is maker**, so aggressor is **seller**.

Define **impact threshold** \(\theta\) (e.g., 2–8 bps depending on symbol/liquidity):
\[
V^{impact}_t = \sum_{k \in [t-\Delta,t]} (p_k q_k)\,\mathbf{1}[m_k=\text{True}]\,\mathbf{1}[p_k < b_{t^-}(1-\theta)]
\]

> Using \(b_{t^-}\) (best bid just before the trade window) avoids self-referential bias.

### Open interest flush (forced close proxy)
\[
\Delta OI_t = OI_{t-\Delta_{OI}} - OI_t
\]
Use \(\Delta_{OI}\) based on OI update frequency (often seconds to minutes). When OI is sparse, use an *exponentially-smoothed* estimate and measure drops at the update times.

---

## 2.2 Revised sub-factors

### (A) Forced Selling Intensity (FSI)
Instead of dividing by \(\Delta OI_t\) directly (which can explode when \(\Delta OI_t\approx 0\)), use a **bounded, monotone scaling**:

\[
FSI_t
=
\frac{V^{impact}_t}{ADV^{\$}_t \Delta}
\cdot
g(\Delta OI_t)
\]

Where:
- \(ADV^{\$}_t\) is rolling average dollar volume per second (or per \(\Delta\)).
- \(g(\cdot)\) is a smooth “OI flush amplifier”, e.g.
\[
g(\Delta OI_t)= 1 + \kappa \cdot \tanh\!\left(\frac{\Delta OI_t}{\sigma_{\Delta OI}}\right)
\]
This prevents instability and still boosts when OI is flushing.

### (B) Liquidity Depletion / Fragility (LDR)
Use spread *and* depth, but do it dimensionlessly:

\[
LDR_t(\eta) = \frac{s_t/m_t}{D^{bid}_t(\eta) / \overline{D^{bid}}_t(\eta)}
\]

Interpretation: wide relative spread + unusually thin depth → fragile book.

### (C) Price impact per dollar (toxicity proxy)
A strong way to detect “toxic” flow is **impact slope**:

\[
\Pi_t = \frac{|r_t|}{V^{\$}_t + \epsilon}
\quad\text{where}\quad
r_t=\ln\left(\frac{m_t}{m_{t-\Delta}}\right)
\]

Large \(\Pi_t\) means small volume moves price a lot → depleted liquidity / adverse selection.

---

## 2.3 Composite LPI (robust normalization)
Use robust z-scores (median/MAD) to reduce fat-tail distortion:

\[
z(x_t)=\frac{x_t-\text{median}(x_{t-W:t})}{1.4826\cdot \text{MAD}(x_{t-W:t})+\epsilon}
\]

Then:
\[
LPI_t = w_1 z(FSI_t) + w_2 z(LDR_t) + w_3 z(\Pi_t)
\]

Typical starting weights:
- \(w_1=0.5\), \(w_2=0.3\), \(w_3=0.2\)

---

# 3) The missing piece: an **Exhaustion Detector** (entry timing)

High LPI tells you “it’s violent.” It does **not** tell you “it’s over.”

Mean reversion becomes positive expectancy when forced selling is **decelerating** and liquidity begins to **reform**.

## 3.1 Core exhaustion conditions (microstructure version)

Define:

- First difference: \(\Delta LPI_t = LPI_t - LPI_{t-\Delta}\)
- Short realized volatility (or RV):  
\[
RV_t = \sum_{i=1}^{n} r_{t,i}^2
\]
computed on sub-samples inside \(\Delta\)

### Exhaustion rule set \(E_t\)
Trigger candidate “exhaustion” when all hold:

1. **Panic occurred:**  
   \[
   LPI_t > LPI^{hi} \quad (\text{e.g., } 3.0)
   \]
2. **Pressure is rolling over:**  
   \[
   \Delta LPI_t < 0 \quad \text{for } K \text{ consecutive ticks}
   \]
3. **OI flush confirms forced nature (if available):**  
   \[
   z(\Delta OI_t) > z_{OI}^{hi}
   \]
4. **Liquidity reformation:** depth stops worsening, spread stops widening:
   \[
   \Delta z(LDR_t) < 0
   \]
5. **Anti-catching-knife filter:** price stops making new lows *while LPI declines* (divergence):
   \[
   m_t < m_{t-\tau} \;\;\text{but}\;\; LPI_t < LPI_{t-\tau}
   \]

This divergence is the closest you get to “capitulation detected” in pure public microstructure data.

---

# 4) Strategy architecture: Mixture-of-States (don’t fade everything)

## 4.1 State classification
Define two states:

- **Forced-flow state** \(S_t=\mathsf{FORCED}\)
- **Informed/trend state** \(S_t=\mathsf{INFORMED}\)

A simple, production-friendly classifier:

\[
S_t=
\begin{cases}
\mathsf{FORCED}, & z(\Delta OI_t) > c_{OI} \;\wedge\; z(FSI_t)>c_{FSI} \\
\mathsf{INFORMED}, & \text{otherwise}
\end{cases}
\]

If OI is too sparse, substitute with:
- liquidation feed volume (preferred), or
- persistent signed order flow imbalance without OI drops (proxy for informed repositioning).

---

## 4.2 Two-mode trading logic

### Mode A: Forced-flow exhaustion → **liquidity-provision mean reversion**
This is your “LPI strategy,” but fixed.

**Entry:** when \(S_t=\mathsf{FORCED}\) and \(E_t=\text{true}\).

**Position direction:** fade the move (buy after sell cascade; sell after buy squeeze).

Let:
\[
dir_t = -\text{sign}(r_t)
\]

**Target position (continuous sizing):**
\[
w_t = dir_t \cdot w_{max}\cdot \sigma^{-1}_{t}\cdot h(LPI_t)
\]
where:
- \(\sigma_t\) is short-horizon realized vol (risk normalization)
- \(h(\cdot)\) is increasing but bounded, e.g.
\[
h(LPI)=\tanh\!\left(\frac{LPI - LPI^{hi}}{2}\right)
\]

**Exit:**
- primary: when \(LPI_t\) decays below \(LPI^{exit}\) (e.g., 1.0–1.5)
- safety: time stop \(T_{max}\) (e.g., 30–180s depending on horizon)
- risk: hard stop if price continues against you by \(X\) sigmas

### Mode B: Informed state → **do not fade** (or trend-follow small)
If \(S_t=\mathsf{INFORMED}\), then either:
- stay flat, or
- run a separate trend / breakout strategy with its own cost model.

This prevents the classic failure: fading a real repricing.

---

# 5) Execution model that can survive fees + adverse selection

## 5.1 Maker-first execution with toxicity-aware quoting
During cascades, crossing the spread is usually paying to lose.

### Quote placement (buy example)
When Mode A wants to buy, place layered maker orders:

- Level 1: best bid
- Level 2: best bid \(-1\) to \(-3\) ticks
- Level 3: deeper but small (optional)

But **only** if you estimate toxicity has decreased.

A minimal toxicity gate:
\[
z(\Pi_t) < z_\Pi^{max}
\]
because if price impact per dollar is still extreme, fills are likely “winner’s curse.”

## 5.2 Queue / fill realism (must-have)
In backtest or sim, require either:

- price trades *through* your limit, or
- printed volume at that level exceeds your queue estimate.

A conservative fill condition for a bid at \(P_{lim}\):

\[
Fill_t =
\mathbf{1}\Big[\min(p_{trades \in [t,t+\delta]}) < P_{lim}\Big]
\;\;\vee\;\;
\mathbf{1}\Big[V_{at\;P_{lim}} > Q_{ahead}\Big]
\]

## 5.3 Post-trade markout as the objective (not just PnL)
You should optimize:
\[
Markout(\tau) = \mathbb{E}[dir\cdot (m_{t+\tau}-P_{fill})]
\]
If markout is negative at \(\tau=0.5\)–2s, you are providing liquidity to toxic flow.

---

# 6) Proof-of-concept reasoning (why this can have edge)

A workable microstructure argument:

1. **Forced sellers are inelastic:** liquidation engine sells regardless of price → creates temporary overshoot.
2. **Market makers withdraw during uncertainty:** spread widens + depth thins → overshoot amplifies.
3. **Once forced selling exhausts (OI flush),** the inelastic supply disappears; remaining flow is more elastic → price mean-reverts toward fair/microprice.

We can express this as a conditional drift model.

Let mid returns satisfy:
\[
r_{t+1} = \mu(S_t, E_t) + \beta r_t + \epsilon_{t+1}
\]

Hypothesis:
\[
\mu(\mathsf{FORCED}, E_t=\text{true}) \cdot dir_t > 0
\]
i.e., after exhaustion in forced state, expected return in the fade direction is positive.

But:
\[
\mu(\mathsf{INFORMED}, E_t=\text{true}) \cdot dir_t \not> 0
\]
which is exactly why you need the state split.

**Empirical validation path:** event study on \(E_t\) triggers:
- compute conditional average markout and conditional distribution
- check robustness across symbols, regimes, and time

---

# 7) ASCII diagrams (system + timeline)

## 7.1 Strategy state machine

```text
+-------------------------------------------------------------------+
|                    LPI / VOLATILITY ALPHA ENGINE                  |
+-------------------------------------------------------------------+
| Inputs: agg_trades, depth/diff_depth, open_interest, funding      |
+-------------------------------------------------------------------+
| 1) Feature Layer                                                  |
|    - impact sell $V_impact$                                       |
|    - depth band D_bid(eta), spread s                              |
|    - impact slope Pi = |r|/V                                      |
|    - OI flush DeltaOI                                             |
|                |                                                  |
|                v                                                  |
| 2) LPI Computation (robust z-scores)                              |
|    LPI = w1 z(FSI) + w2 z(LDR) + w3 z(Pi)                         |
|                |                                                  |
|                v                                                  |
| 3) State Classifier S_t                                           |
|    IF (FSI high AND DeltaOI high) -> FORCED                       |
|    ELSE -> INFORMED                                               |
|                |                                                  |
|                v                                                  |
| 4) Exhaustion Detector E_t                                        |
|    - LPI high then rolling over                                   |
|    - LDR improving                                                |
|    - divergence: price down, LPI down                             |
|                |                                                  |
|                v                                                  |
| 5) Decision                                                       |
|    FORCED & E_t -> Mode A: maker-fade mean reversion              |
|    INFORMED     -> Mode B: flat or separate trend logic           |
|                |                                                  |
|                v                                                  |
| 6) Execution + Risk                                               |
|    - maker-first quoting, toxicity gate                           |
|    - queue-aware fills                                            |
|    - time stop, sigma stop, inventory caps                        |
+-------------------------------------------------------------------+
```

## 7.2 Why your previous approach likely lost money (timing)

```text
Price:         \_____V_____/ 
LPI:            /\   /\ 
               /  \_/  \__
                 ^   ^
                 |   |
         "panic begins"  "exhaustion begins"

Bad strategy: BUY at first spike  -> filled while toxicity rising
Revised:      BUY only after rollover + liquidity reformation
```

---

# 8) Concrete, complete strategy spec (rules you can implement)

Below is a full spec you can translate into code.

## 8.1 Parameters (starting points; must be fit per symbol bucket)
- Alpha tick \(\Delta = 250\)ms (or 100ms for top pairs)
- Depth band \(\eta = 10\) bps
- Impact threshold \(\theta = 3\)–8 bps
- Robust window \(W = 2\)–24 hours depending on stationarity
- \(LPI^{hi}=3.0\), \(LPI^{exit}=1.2\)
- Rollover requirement \(K=3\) ticks (e.g., 0.75s at 250ms)
- Time stop \(T_{max}=120s\)
- Max adverse move stop: \(2.5\sigma_t\)
- Inventory cap per symbol (risk-based)

## 8.2 Signal computation (each alpha tick)
1. Update local book (diff_depth)
2. Accumulate trades in \([t-\Delta,t]\)
3. Compute \(V^{impact}_t\), \(FSI_t\), \(LDR_t\), \(\Pi_t\), \(\Delta OI_t\)
4. Compute robust z-scores and \(LPI_t\)

## 8.3 State classifier
- \(S_t=\mathsf{FORCED}\) if:
  - \(z(FSI_t)>2.0\) AND \(z(\Delta OI_t)>1.5\) (or liquidation feed spike)
- else \(S_t=\mathsf{INFORMED}\)

## 8.4 Exhaustion detector
\(E_t=\text{true}\) if:
- \(LPI_t > 3.0\)
- \(\Delta LPI_t < 0\) for last \(K\) ticks
- \(\Delta z(LDR_t) < 0\) (liquidity not worsening)
- divergence test over \(\tau\in[1s,3s]\): price makes new low but LPI does not

## 8.5 Orders (Mode A only)
If \(S_t=\mathsf{FORCED}\) and \(E_t=\text{true}\):
- Determine direction \(dir_t=-\text{sign}(r_t)\)
- Compute risk-scaled size \(w_t\)
- Place maker limits:
  - \(P_1=b_t\)
  - \(P_2=b_t - 1\) tick
  - \(P_3=b_t - 2\) ticks (small)
- Cancel/replace if:
  - toxicity gate fails: \(z(\Pi_t)\) spikes again
  - spread explodes beyond max threshold
  - time-in-state exceeds a small limit (avoid stale quotes)

## 8.6 Exits
- Take profit: when \(LPI_t < 1.2\) OR markout target hit
- Stop: if adverse move > \(2.5\sigma_t\)
- Time stop: \(T_{max}\)
- Also reduce exposure if global market is in correlated crash (BTC dominance filter)

---

# 9) Validation protocol (so you don’t “optimize optimism”)

## 9.1 Event study first (before full backtest)
Collect all events where \(E_t=\text{true}\) for each symbol.

Compute:
- mean/median markout at \(\tau = 0.5s, 1s, 2s, 5s, 30s, 120s\)
- distribution tails (5th percentile)
- conditional on funding regime (high positive, neutral, negative)

If markout is not robustly positive after costs, stop here.

## 9.2 Event-driven simulation (required)
Vectorized OHLC backtests will lie to you.

Sim must include:
- latency shift (50–150ms)
- maker queue model
- cancel/replace limits
- fee tier realistic (maker/taker)
- slippage if you ever cross

## 9.3 Overfitting control
- Purged / embargoed CV across time blocks
- Deflated Sharpe Ratio (DSR) or at least multiple-testing adjustment
- Parameter stability: performance should not vanish if \(\theta\) changes by ±2 bps

---

# 10) What I need from you to tailor this into a “drop-in” rewrite for your paper/proposal

If you answer these, I can rewrite your **alpha_proposal2** into a revised, consistent IEEE-style strategy section *and* provide implementation-ready pseudo-code:

1. Are you trading **BTCUSDT/ETHUSDT only**, or a top-\(N\) universe?
2. Do you have access to a **liquidation websocket stream**, or only `agg_trades` + `open_interest`?
3. Your target holding time: **seconds**, **minutes**, or **hours**?
4. Maker fill reality: do you have any estimate of **queue position** (e.g., from depth snapshots + trade prints), or do you assume conservative “trade-through” only?
5. Your current failure mode:  
   - too many false positives (catching knives),  
   - fills are toxic (negative markout),  
   - fees/latency kill, or  
   - signal decays before you can enter?

---

## If you want, I can also:
- rewrite the LPI math in your proposal to use **robust normalization + bounded OI scaling** (fixing the instability),
- add the **exhaustion detector** section and diagrams,
- and provide a full “Strategy Rules + Backtest Protocol + Risk Model” section that is internally consistent with your existing narrative style.