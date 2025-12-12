# Executive Summary

- Build a **fully offline, reproducible, multi-phase research + backtesting codebase** using **Python interfaces of `nautilus_trader`** only.
- Use **only public historical files from `binance.vision`** (no API keys, no live connectors, no authenticated providers).
- Support **Binance Spot** and **Binance Futures (UM)** datasets stored locally as daily/monthly CSVs, matching the inventory/layout patterns provided.
- Phase 2 will deliver a deterministic **ETL + normalization pipeline** that converts raw CSVs into a **`ParquetDataCatalog`** (Nautilus-native storage) with schema validation and deduplication.
- Phase 3 will deliver a **feature/signal layer + strategy** (default: a liquidation-pressure style “Phoenix/LPI” strategy consistent with the provided prototype code), implemented as a Nautilus `Strategy`.
- Phase 4 will deliver a **reproducible backtest runner** (config-driven) producing stable metrics artifacts (fills report, positions report, account report, and optional tearsheet if installed).
- Architecture explicitly isolates uncertain Nautilus details behind **adapter boundaries** marked “TO BE CONFIRMED FROM NAUTILUS CONTEXT”, while grounding all known APIs in the provided examples.
- Determinism plan: **canonical timestamp normalization**, strict event ordering, stable serialization, and controlled caching/materialization.
- Deliverables include tests for: schema correctness, timestamp monotonicity, dedup guarantees, and end-to-end backtest reproducibility.

---

# Goals and Non-Goals

## Goals

1. **Offline-only historical research and backtesting** using locally stored `binance.vision` files.
2. **Single source of truth:** raw data on disk, transformed into a Nautilus `ParquetDataCatalog`.
3. **Reproducibility:** deterministic ingestion, deterministic backtest runs, stable artifacts.
4. **Dataset coverage:** support the dataset families shown in the provided inventory:
   - Spot: `aggTrades`, `trades`, `klines` (1m)
   - Futures (UM): `aggTrades`, `trades`, `bookTicker`, `bookDepth` (derived metrics format), `klines`, `markPriceKlines`, `indexPriceKlines`, `premiumIndexKlines`, `metrics`, `fundingRate`
5. **Modularity across phases:** clean boundaries (ETL vs features vs strategy vs runner).

## Non-Goals (explicit exclusions)

- **No live trading**, no live data subscriptions, no use of Binance adapters.
- **No API keys**, no authenticated endpoints, no paid datasets.
- No UI/UX dashboards, web apps, notebooks-as-products (not disallowed, but out of scope).
- No multi-exchange abstractions beyond the local `binance.vision` layouts provided.
- No attempt to “correct” market microstructure beyond what the offline dataset supports (e.g., reconstructing a full order book from incomplete depth metrics unless the data supports it).
- No reliance on unprovided files or hidden repository structure: everything must be derivable from the inputs in this chat.

---

# Data Source and File Layout (binance.vision)

This system assumes all historical inputs are already downloaded from `binance.vision` and placed into a local folder hierarchy.

## Supported on-disk layout patterns

### Binance Futures (UM) — **as observed** (`data/raw/futures/...`)

From the provided listing, daily files exist like:

- `data/raw/futures/daily/aggTrades/BTCUSDT-aggTrades-YYYY-MM-DD.csv`
- `data/raw/futures/daily/bookTicker/BTCUSDT-bookTicker-YYYY-MM-DD.csv`
- `data/raw/futures/daily/bookDepth/BTCUSDT-bookDepth-YYYY-MM-DD.csv`
- `data/raw/futures/daily/klines_1m/BTCUSDT-1m-YYYY-MM-DD.csv`  *(note the directory name `klines_1m`)*
- `data/raw/futures/daily/metrics/BTCUSDT-metrics-YYYY-MM-DD.csv`
- Monthly:
  - `data/raw/futures/monthly/fundingRate/BTCUSDT-fundingRate-YYYY-MM.csv`
  - (and potentially monthly `aggTrades`, `bookTicker`, `klines`, etc., per the inventory examples)

### Binance Spot — **as described** (inventory example)

- `./spot_data/daily/aggTrades/SYMBOL-aggTrades-YYYY-MM-DD.csv`
- `./spot_data/daily/trades/SYMBOL-trades-YYYY-MM-DD.csv`
- `./spot_data/daily/klines/SYMBOL-1m-YYYY-MM-DD.csv`
- Monthly equivalents:
  - `./spot_data/monthly/aggTrades/SYMBOL-aggTrades-YYYY-MM.csv`
  - `./spot_data/monthly/trades/SYMBOL-trades-YYYY-MM.csv`
  - `./spot_data/monthly/klines/SYMBOL-1m-YYYY-MM.csv`

### “future_data/…” format — **legacy/alternate** (inventory example)

The inventory includes an alternate naming layout like:

- `./future_data/daily_data/aggTrades/...`
- `./future_data/daily_data/bookDepth/...`
- `./future_data/daily_data/bookTicker/...`
- `./future_data/daily_data/(indexPriceKlines|klines|markPriceKlines|premiumIndexKlines)/...`
- `./future_data/daily_data/metrics/...`
- `./future_data/daily_data/trades/...`
- Monthly equivalents under `./future_data/monthly_data/...`

**Design decision:** Phase 2 will support both:
- `data/raw/futures/{daily,monthly}/...` (observed)
- `future_data/{daily_data,monthly_data}/...` (inventory example)

by treating both as “input roots” conforming to a shared dataset registry.

## Dataset → columns → canonical internal schema (table)

The canonical internal schema is expressed in terms of NautilusTrader event types where possible, and custom `Data` types otherwise.

| Dataset family | Example file | Expected columns (raw) | Canonical internal event | Canonical fields (internal) | Notes |
|---|---|---|---|---|---|
| Spot aggTrades | `.../spot_data/.../SYMBOL-aggTrades-YYYY-MM-DD.csv` | `aggTradeId, price, quantity, firstTradeId, lastTradeId, timestamp, isBuyerMaker, isBestMatch` (headerless or header) | `TradeTick` | `instrument_id, price, size, aggressor_side, trade_id, ts_event, ts_init` | Aggressor side derived from `isBuyerMaker` (see Canonical Data Model). |
| Spot trades | `.../spot_data/.../SYMBOL-trades-YYYY-MM-DD.csv` | `tradeId, price, qty, quoteQty, time, isBuyerMaker, isBestMatch` | `TradeTick` | same as above | Trade-level not aggregated. |
| Spot klines 1m | `.../spot_data/.../SYMBOL-1m-YYYY-MM-DD.csv` | `open_time, open, high, low, close, volume, close_time, quote_asset_volume, number_of_trades, taker_buy_base_asset_volume, taker_buy_quote_asset_volume, ignore` | `Bar` | `bar_type, open, high, low, close, volume, ts_event, ts_init` | `bar_type` must be consistent (`1-MINUTE-...`). |
| Futures aggTrades | `.../futures/.../BTCUSDT-aggTrades-YYYY-MM-DD.csv` | same pattern as aggTrades | `TradeTick` | same as above | Futures-specific instrument id is different from spot (see below). |
| Futures trades | `.../futures/.../BTCUSDT-trades-YYYY-MM-DD.csv` | `id, price, qty, quote_qty, time, is_buyer_maker` | `TradeTick` | same as above | Column names may be `snake_case` + lowercase booleans (`true/false`). |
| Futures bookTicker | `.../futures/.../BTCUSDT-bookTicker-YYYY-MM-DD.csv` | `update_id, best_bid_price, best_bid_qty, best_ask_price, best_ask_qty, transaction_time, event_time` | `QuoteTick` | `instrument_id, bid_price, ask_price, bid_size, ask_size, ts_event, ts_init` | Use `transaction_time` as `ts_event` for determinism (see Canonical Data Model). |
| Futures klines 1m | `.../futures/.../BTCUSDT-1m-YYYY-MM-DD.csv` | `open_time, open, high, low, close, volume, close_time, quote_volume, count, taker_buy_volume, taker_buy_quote_volume, ignore` | `Bar` | as above | Note directory may be `klines_1m` or `klines`. |
| Futures mark/index/premium klines | `.../markPriceKlines/...` etc. | same as klines but often volume=0 | `Bar` (optional) OR custom `Data` | for `Bar`: `bar_type` encodes price type | Often not needed for execution backtests; useful for research/analytics. |
| Futures metrics | `.../metrics/...` | e.g. `create_time, symbol, sum_open_interest, sum_open_interest_value, ... ratios ...` | custom `Data` (e.g., `BinanceOiData`) | `instrument_id, open_interest_qty, open_interest_val, top_trader_ratio, ts_event, ts_init` | Example custom data class appears in provided prototype. |
| Futures fundingRate (monthly) | `.../fundingRate/BTCUSDT-fundingRate-YYYY-MM.csv` | `calc_time, funding_interval_hours, last_funding_rate` (+ symbol may appear) | custom `Data` (e.g., `BinanceFundingRate`) | `instrument_id, rate, interval_hours, ts_event, ts_init` | Timestamp is ms epoch. |
| Futures bookDepth (derived metrics format) | `.../bookDepth/...` | sample shows `timestamp (ISO), percentage, depth, notional` | custom `Data` (e.g., `BinanceDepthData`) | `instrument_id, percentage, depth_qty, notional, ts_event, ts_init` | Not a full L2 book snapshot; treat as a *liquidity metric stream*. |

---

# Canonical Data Model

This section defines the “single truth” representation for time, instruments, and event semantics used across all phases.

## Time model and timezone

- **Canonical timezone:** UTC.
- **Canonical timestamp unit in-memory:** **nanoseconds since Unix epoch** (`int`), matching typical Nautilus usage (`ts_event`, `ts_init`).
- Input timestamps may appear as:
  - epoch milliseconds (`1711756800000`)
  - ISO timestamps (`2024-03-30 00:00:09`)
- **Normalization rule:**
  - If a field is documented as ms-epoch, convert `ms → ns` by `ns = int(ms) * 1_000_000`.
  - If a field is ISO datetime, parse in UTC unless an explicit timezone is present; convert to `ns`.
- **Event ordering rule:** within each stream, sort by `(ts_event, stable_tiebreaker)` before writing to catalog, where stable tiebreaker is dataset-specific (e.g., `update_id`, `agg_trade_id`, `trade_id`, `open_time`).

## Instrument identifiers and venue namespace

NautilusTrader uses `InstrumentId`. The provided code and examples show patterns like:

- `InstrumentId.from_str("ETHUSDT.BINANCE")`
- `InstrumentId(symbol=Symbol("ETHUSDT-PERP"), venue=BINANCE_VENUE)`
- Backtest examples use `Venue("XCME")`, etc.

**Canonical convention for this codebase (spec default):**

- Define two venues to avoid ambiguity between spot and futures:
  - `BINANCE_SPOT` for spot datasets
  - `BINANCE_FUTURES` for UM futures datasets
- Define instrument IDs as:
  - Spot: `"{SYMBOL}.BINANCE_SPOT"` (e.g., `BTCUSDT.BINANCE_SPOT`)
  - Futures perpetual: `"{SYMBOL}-PERP.BINANCE_FUTURES"` (e.g., `BTCUSDT-PERP.BINANCE_FUTURES`)
    - Rationale: prevents accidental mixing of spot and futures ticks in one instrument stream.
- **Mapping rule from binance.vision symbol strings:**
  - Spot symbol in filenames: `BTCUSDT` → spot instrument symbol: `BTCUSDT`
  - Futures UM symbol in filenames: `BTCUSDT` → futures instrument symbol: `BTCUSDT-PERP`
    - This is a modeling choice; binance.vision futures UM data is typically perpetual-style. If a different contract type is required later, it must be specified explicitly (see Blocking Questions).

## Price/size precision rules

- Raw CSV values may have more decimals than desired for execution realism.
- Canonical rule: **instrument definitions determine quantization**.
  - For example, the provided prototype uses:
    - `price_precision=2`, `price_increment=0.10`
    - `size_precision=3`, `size_increment=0.001`
- **Normalization:** when converting to Nautilus `Price` / `Quantity`, quantize to instrument precision and/or increments using the instrument helpers wherever possible (e.g., `instrument.make_price`, `instrument.make_qty`) to prevent “invalid tick size” artifacts.

## Event types and required/optional streams

### Required for a minimal executable futures backtest

- `TradeTick` from `aggTrades` or `trades`
- `QuoteTick` from `bookTicker` (for best bid/ask; required if strategy uses maker orders or needs spread)
- `Instrument` definition (e.g., `CryptoPerpetual` for futures)
- `Venue` definition in backtest engine setup

### Optional but supported for research/filters

- `Bar` from `klines` (1m)
- Custom `Data`:
  - `FundingRate` (monthly)
  - `OpenInterest/Metrics`
  - `DepthMetric` (derived from bookDepth percentage/depth/notional format)
- Additional bar-like streams:
  - mark/index/premium klines (as `Bar` with explicit price type mapping, or as custom data)

---

# System Architecture (Phases 2–4)

## Component diagram (ASCII)

```text
+-------------------+        +---------------------+        +---------------------+
|  binance.vision    |        |  Phase 2: ETL       |        | Phase 3: Research   |
|  offline files     | -----> |  Ingest + Catalog   | -----> | Features + Strategy |
|  (CSV daily/month) |        |  (ParquetDataCatalog)|       | (Strategy + factors)|
+-------------------+        +---------------------+        +---------------------+
                                                                      |
                                                                      v
                                                         +----------------------+
                                                         | Phase 4: Backtest    |
                                                         | Runner + Reports     |
                                                         | (Engine/Node + CSV)  |
                                                         +----------------------+
```

## Dataflow diagram (ASCII)

```text
(raw disk)                       (normalized)                 (simulation)
┌───────────────┐                ┌───────────────────┐        ┌───────────────────┐
│ CSV files      │                │ Nautilus objects  │        │ BacktestEngine     │
│ daily/monthly  │                │ TradeTick/QuoteTick│       │ or BacktestNode    │
└──────┬────────┘                │ Bar/custom Data   │        └──────┬────────────┘
       │                         └─────────┬─────────┘               │
       v                                   │                         v
┌───────────────┐      validate+dedup      │            ┌────────────────────────┐
│ Parsing        │ ---------------------->  │ write      │ Metrics + Artifacts     │
│ header/noheader│                         v            │ fills/positions/account  │
└──────┬────────┘                ┌───────────────────┐  │ (deterministic outputs)  │
       │                         │ ParquetDataCatalog│  └────────────────────────┘
       v                         └───────────────────┘
┌───────────────┐
│ Normalization  │
│ time, types,   │
│ instrument_id  │
└───────────────┘
```

## Module boundaries and phase ownership

### Phase 2 owns (ETL + catalog)

- Dataset registry (spot vs futures; daily vs monthly; mapping file path → parser).
- Robust CSV reading (headerless/headered).
- Canonical timestamp parsing and normalization.
- Deduplication policy per dataset.
- Validation: schema, monotonic timestamps, numeric sanity checks.
- Writing outputs into `ParquetDataCatalog`.

**Phase 2 explicitly does NOT own:**
- Strategy logic, indicators, portfolio/risk, backtest orchestration.

### Phase 3 owns (features + strategy)

- Strategy configuration objects (`StrategyConfig` subclasses).
- Feature calculators / signal generators:
  - default: liquidation-pressure style factor engine consistent with provided prototype.
- Strategy implementation as Nautilus `Strategy`:
  - subscribes to `TradeTick`, `QuoteTick`, and optional custom `Data` types.
- Risk model and position sizing model.

**Phase 3 explicitly does NOT own:**
- Raw parsing, catalog layout decisions, or the backtest runner CLI.

### Phase 4 owns (reproducible runner)

- Configuration for:
  - backtest date range
  - instruments
  - venues
  - which catalog path(s) to use
- Running the backtest deterministically.
- Exporting artifacts:
  - fills report CSV
  - positions report CSV
  - account report CSV / JSON (format to be decided)
  - metadata manifest describing inputs and versions

**Phase 4 explicitly does NOT own:**
- Dataset parsing logic, feature math definitions.

---

# Mathematical Formulation

This specification defines a default “Phoenix/LPI” research strategy because the provided prototype code and datasets include:
- `aggTrades` / `trades` (trade flow)
- `bookTicker` (top-of-book)
- `metrics` (open interest + ratios)
- `bookDepth` (liquidity metric snapshots)
- `fundingRate` (context)

If the final intended strategy differs, swap the signal definition while keeping the pipeline unchanged.

## Notation

- Let \( t \) denote event time in **nanoseconds UTC**.
- Let \( p_t \) be trade price at time \( t \).
- Let \( q_t \) be trade quantity (base units) at time \( t \).
- Let \( n_t = p_t \cdot q_t \) be notional (quote currency, e.g., USDT) for a trade at time \( t \).
- Let \( \Delta \log p_t = \log(p_t) - \log(p_{t^-}) \) be log-return from previous trade tick.
- Let \( \sigma_t \) denote realized volatility estimate (defined below).

### Aggressor side mapping

Using Binance semantics present in the files:
- `is_buyer_maker = True` implies the buyer was the maker → the taker was selling.
- Therefore define:
  - if `is_buyer_maker` is True → aggressor side = SELLER
  - else → aggressor side = BUYER

## Realized volatility (for sizing)

Define a rolling-window realized variance estimator over a window length \( W \) (in seconds or nanoseconds) on trade ticks:

\[
RV_t = \sqrt{\sum_{i: t-W \le t_i \le t} (\Delta \log p_{t_i})^2}
\]

Use \( \sigma_t = \max(RV_t, \sigma_{\min}) \) to avoid division by near-zero.

## Volume-clock buckets and forced selling intensity (FSI)

Define a **volume bucket size** \( B \) in notional units (e.g., \(1{,}000{,}000\) USDT).

Maintain an accumulator over trades:
- Total bucket notional \( V = \sum n_t \)
- Aggressive sell notional \( S = \sum n_t \cdot \mathbf{1}\{\text{aggressor}=\text{SELLER}\} \)
- Aggressive buy notional \( U = \sum n_t \cdot \mathbf{1}\{\text{aggressor}=\text{BUYER}\} \)

When \( V \ge B \), finalize the bucket and compute:

\[
FSI = \frac{S}{V}
\]

Optionally normalize via rolling z-score (see below), but the bucket-level raw \(FSI\) is the primary observable.

## VPIN-like imbalance within the bucket

\[
VPIN = \frac{|U - S|}{V}
\]

This is a bounded imbalance measure in \([0,1]\).

## Liquidity depletion ratio (LDR) from depth metric stream

Given the `bookDepth` derived metric provides notional depth \( D_t \) (e.g., at some percentage band), define:

\[
LDR_t = \begin{cases}
\frac{D_t}{D_{t^-}} & \text{if } D_{t^-} > 0\\
1 & \text{otherwise}
\end{cases}
\]

Define liquidity stress as:

\[
LS_t = \max(0, 1 - LDR_t)
\]

so liquidity stress increases when depth decreases.

## Open interest regime z-score

Let \( O_t \) be open interest quantity from `metrics` at time \( t \).
Maintain a rolling window of length \( N_{OI} \) (e.g., 30 days worth of 5-min updates).

Define:

\[
Z_{OI}(t) = \frac{O_t - \mu_O}{\sigma_O}
\]

with the convention that if \(\sigma_O \approx 0\) or too few samples exist, the z-score is treated as undefined and the strategy blocks new entries.

## Composite Liquidation Pressure Index (LPI)

Define a bounded composite score:

- Let \( \tilde{FSI} \) be a nonlinearly scaled version of FSI (e.g., via tanh of positive z-scored FSI shocks):

\[
\tilde{FSI} = \tanh(\max(0, Z_{FSI}))
\]

- Combine:

\[
LPI = w_1 \tilde{FSI} + w_2 LS + w_3 VPIN
\]

with default weights:
- \(w_1 = 0.6\)
- \(w_2 = 0.3\)
- \(w_3 = 0.1\)

## Entry/exit rules (default)

- Regime filter: only consider trading when \( Z_{OI}(t) \ge z_{\text{thr}} \).
- Entry trigger: if \( LPI \ge \ell_{\text{entry}} \), enter **long** (mean-reversion to post-liquidation bounce) using maker logic if quotes available.
- Exit trigger: if \( LPI \le \ell_{\text{exit}} \), close position.

(If short-selling is desired, it must be explicitly specified later; this spec defaults to the single-direction version reflected in the prototype.)

## Position sizing (Kelly-style)

Given equity \( E_t \), expected edge \( \mu \), and volatility proxy \( \sigma_t \), define:

\[
f^* = \kappa \cdot \frac{\mu}{\sigma_t^2 + \nu}
\]

Where:
- \( \kappa \in (0,1] \) is a risk fraction (half-Kelly typical)
- \( \nu \) is jump-risk variance add-on

Cap leverage:

\[
f = \min(f^*, f_{\max})
\]

Target notional:

\[
N_t = E_t \cdot f
\]

Quantity:

\[
Q_t = \frac{N_t}{P_{\text{ref}}}
\]

where \(P_{\text{ref}}\) is typically best bid or mid at decision time, quantized to instrument rules.

## Walk-forward / rolling window backtest (optional extension)

If walk-forward evaluation is required:

- Choose:
  - training window length \(T_{\text{train}}\)
  - test window length \(T_{\text{test}}\)
  - step size \(T_{\text{step}}\)

For each fold \(k\):
- Train interval: \([t_0 + kT_{\text{step}},\ t_0 + kT_{\text{step}} + T_{\text{train}})\)
- Test interval: \([t_0 + kT_{\text{step}} + T_{\text{train}},\ t_0 + kT_{\text{step}} + T_{\text{train}} + T_{\text{test}})\)

This spec defaults to **single-run backtests** unless explicitly requested, because walk-forward implies parameter fitting workflows not yet specified.

---

# Algorithmic Specifications

## ETL ingestion (Phase 2): streaming, deduplication, validation

### 1) Dataset registry and file discovery

- Input: one or more **root paths** (e.g., `data/raw/futures`, `spot_data`, `future_data`, etc.).
- Discover files by glob patterns:
  - daily: `**/daily/**/**/*.csv`
  - monthly: `**/monthly/**/**/*.csv`
- Infer dataset type from parent directory name (e.g., `aggTrades`, `bookTicker`, `metrics`, `klines_1m`, `fundingRate`).
- Extract date tokens via regex:
  - daily: `YYYY-MM-DD`
  - monthly: `YYYY-MM`
- Group daily files by date for deterministic processing order.

### 2) Robust CSV reading

Given files may be headerless or headered (as shown in the inventory), apply:

- Read first row with pandas to infer if header exists:
  - If first column name looks numeric → treat as headerless.
  - Else treat as headered.
- Normalize column names into canonical raw column sets per dataset family.
- Reject empty files early.

### 3) Normalization to canonical fields

For each dataset, map to canonical event objects:

- `bookTicker` → `QuoteTick`
  - Use `transact_time` (ms) → `ts_event` in ns
  - Set `ts_init = ts_event` for deterministic ingestion
- `aggTrades` / `trades` → `TradeTick`
  - price/qty as floats/strings → `Price` / `Quantity` objects
  - `trade_id` = agg trade id or trade id
  - aggressor side derived from `is_buyer_maker`
- `klines` (1m) → `Bar`
  - `ts_event = open_time` in ns
- `metrics`, `fundingRate`, `bookDepth` metric format → custom `Data` types
  - Use `@customdataclass` pattern as shown in the provided prototype

**Important:** the internal representation must not depend on fragile float formatting. Prefer instrument-based conversion helpers (where possible) so the same raw file always yields identical serialized values.

### 4) Deduplication rules

Dedup must be deterministic and dataset-specific:

- `TradeTick`:
  - primary key: `(instrument_id, trade_id)`
  - fallback if IDs unreliable: `(instrument_id, ts_event, price, size, aggressor_side)` (only as last resort)
- `QuoteTick` (bookTicker):
  - primary key: `(instrument_id, update_id)` if present
  - otherwise: `(instrument_id, ts_event, bid_price, ask_price, bid_size, ask_size)`
- `Bar`:
  - primary key: `(instrument_id, bar_type, ts_event)` where `ts_event` is open time

Dedup should occur:
- within-file first (fast)
- optionally across-day boundaries when writing to catalog (to handle overlaps), depending on memory constraints

### 5) Validation checks (fail-fast vs warn)

For each file:
- Timestamp parse success rate must exceed threshold (e.g., 99.9% for large files; configurable).
- Non-negativity:
  - price > 0
  - size ≥ 0 (and typically > 0 for trades)
  - quote bid/ask > 0 and bid < ask (warn if violated; drop rows)
- Monotonicity:
  - Ensure sorted order by `(ts_event, tiebreaker)` before writing.
- Coverage sanity:
  - For daily data: confirm `ts_event` spans the named date (UTC day) within tolerance; warn if not.

### 6) Catalog writing

- Output: `ParquetDataCatalog` at a configured path (e.g., `data/catalog`).
- Write:
  - instruments first (definitions)
  - then events by type
- Enforce deterministic chunking:
  - fixed batch sizes per dataset type
  - stable ordering

---

## Feature/stat computation pipeline (Phase 3)

### Event-driven feature engine

Maintain stateful feature calculators that update on events:

- On each `TradeTick`:
  - update realized variance estimator
  - update volume-bucket accumulators (FSI/VPIN)
  - when a bucket completes, emit a `PhoenixSignal` object (internal-only data class)
- On each `BinanceDepthData`:
  - update depth notional and LDR
- On each `BinanceOiData`:
  - update OI rolling z-score

Validity conditions:
- Do not emit tradable signals until required buffers initialized:
  - depth has at least 2 points
  - OI z-score window has minimum samples
  - volatility has at least 2 trades

---

## Strategy decision loop (Phase 3)

### Inputs

- Trade stream: `TradeTick`
- Quote stream: `QuoteTick` (required for maker-style execution)
- Optional custom streams:
  - `BinanceOiData`, `BinanceDepthData`, `BinanceFundingRate`

### Outputs

- Orders submitted through Nautilus `order_factory`:
  - maker-biased limit entry (post-only) if quotes available
  - market exit (or close positions) when exit conditions met

### State machine

Maintain:
- `last_best_bid`, `last_best_ask`
- `current_volatility`
- `is_position_open`
- `active_order_id` (or equivalent handle)

Rules:
- Only one active entry order at a time.
- Cancel working orders before exiting.
- Prevent division-by-zero sizing via volatility floor.

---

## Backtest orchestration (Phase 4)

### Engine setup (grounded in provided examples)

Use the `BacktestEngine` flow as shown in the examples:

1. Create `BacktestEngine(config=BacktestEngineConfig(...))`
2. `engine.add_venue(...)`
3. `engine.add_instrument(...)`
4. Load data from `ParquetDataCatalog`:
   - `catalog.query(...)` for `TradeTick`, `QuoteTick`
   - `catalog.custom_data(...)` for custom `Data` types
5. `engine.add_strategy(strategy)`
6. `engine.run()`
7. Export reports: `engine.trader.generate_*_report()`

### Rolling windows (if enabled)

If walk-forward is enabled (optional):
- Iterate folds, reinitialize engine each fold, run, export fold-specific artifacts with fold metadata.

---

# Performance, Disk Footprint, and Determinism Plan

## Complexity and I/O considerations

### ETL

- Parsing is I/O-bound for large CSVs.
- Complexity:
  - reading: \(O(N)\) rows
  - sorting: \(O(N \log N)\) per file/day if required (often can avoid if already sorted, but must verify)
- Recommended approach:
  - **process day-by-day** to limit peak memory
  - write in batches to the catalog

### Backtest

- Event replay is typically \(O(N)\) where \(N\) is total events.
- If both quote and trade ticks are used, event counts can be large. Control via:
  - date range selection
  - optional downsampling (NOT in scope unless specified; downsampling changes research results and must be explicit)

## Caching and materialization rules

Allowed disk materializations:
- `ParquetDataCatalog` as the canonical normalized store
- Run artifacts:
  - fills report CSV
  - positions report CSV
  - account report CSV/JSON
  - manifest JSON capturing:
    - data catalog path and hash (if available)
    - input roots
    - date range
    - strategy config

Not allowed:
- any cache that depends on live endpoints
- any dynamic downloads during runs

## Determinism plan

Determinism requires controlling:

1. **Event timestamps and ordering**
   - normalize time to UTC ns
   - sort with stable tie-breakers
2. **Numeric normalization**
   - quantize prices/sizes to instrument precision
3. **Stable serialization**
   - always write events in the same order
   - avoid float formatting differences by using consistent conversion paths
4. **Randomness**
   - if any stochastic models are used (fill models, slippage), set explicit seeds in config
   - default: no randomness unless explicitly configured

---

# Interfaces & Contracts Between Phases

This section defines the artifacts and APIs that each phase must produce/consume.

## Phase 2 outputs (ETL artifacts + APIs)

### Artifact: normalized catalog

- A `ParquetDataCatalog` directory (e.g., `data/catalog/`)
- Contains:
  - instrument definitions (spot and/or futures)
  - `TradeTick`, `QuoteTick`, `Bar`
  - custom `Data` types (`BinanceOiData`, `BinanceDepthData`, `BinanceFundingRate`, etc.)

### Phase 2 module API (proposal)

- `DatasetRegistry` (pure Python): maps dataset names to:
  - file glob patterns
  - parser function
  - canonical schema definition
- `Ingestor`:
  - `ingest(root_paths: list[pathlib.Path], catalog_path: pathlib.Path, universe: list[str], start: date, end: date, mode: {"spot","futures","both"}) -> IngestReport`

**TO BE CONFIRMED FROM NAUTILUS CONTEXT**
- Whether `ParquetDataCatalog.write_data(...)` accepts iterables of different types in one call and how it shards data internally across partitions. The design assumes it can write per-type batches, as shown in examples.

## Phase 3 outputs (strategy + feature APIs)

### Strategy package

- `strategies/phoenix_lpi.py`:
  - `PhoenixStrategyConfig(StrategyConfig, frozen=True)`
  - `PhoenixStrategy(Strategy)`

### Feature package

- `features/lpi.py`:
  - `LiquidationAlphaFactor` (stateful calculator)
  - `RealizedVarianceCalculator`
  - `PhoenixSignal` dataclass (internal)

**TO BE CONFIRMED FROM NAUTILUS CONTEXT**
- Exact typing/constructor requirements for `TradeTick`, `QuoteTick`, `Bar`, and `customdataclass` behavior, beyond what is shown in the provided code. The design isolates these in “adapter constructors” so signature changes do not leak into higher layers.

## Phase 4 outputs (runner + metrics artifacts)

### Runner interface

- A config-driven entrypoint (CLI or script) that:
  - opens a `ParquetDataCatalog`
  - loads data in `[start, end]`
  - runs strategy
  - exports artifacts

### Artifacts

- `artifacts/{run_id}/`
  - `config.json`
  - `fills.csv`
  - `positions.csv`
  - `account.csv` (or `account.json`)
  - `manifest.json` (hashes, counts, start/end timestamps, catalog path)

**Grounded in examples:** `engine.trader.generate_order_fills_report()` and related report generators exist in the examples; exports should use those interfaces.

---

# Validation & Testing Strategy

## Unit tests (Phase 2)

1. **Schema detection tests**
   - headerless vs headered inference produces correct column mapping for each dataset type.
2. **Timestamp conversion tests**
   - ms epoch → ns conversion exactness
   - ISO parse correctness and UTC normalization
3. **Aggressor side mapping**
   - `is_buyer_maker=True` → SELLER, else BUYER
4. **Dedup tests**
   - duplicates within a file removed deterministically
   - duplicates across files (e.g., day boundary overlaps) handled as specified

## Integration tests (Phase 2 → Phase 4)

1. **Catalog roundtrip**
   - ingest a small known set of files
   - query back the catalog
   - verify counts and ordering invariants
2. **Backtest smoke test**
   - run on a small date range (e.g., 1–2 days)
   - assert engine completes and artifacts created

## Strategy invariants (Phase 3)

- No entry orders submitted without valid quotes if maker mode is enabled.
- Position sizing never divides by ~0 volatility.
- At most one active entry order at a time.
- Exit cancels outstanding orders before closing positions.

## Acceptance criteria per phase

### Phase 2
- Ingests the provided futures folder structure (including `klines_1m`) without manual edits.
- Produces a non-empty `ParquetDataCatalog` with queryable `TradeTick` and `QuoteTick` streams.
- Deterministic: two ingestions on the same inputs produce identical event counts and stable ordering.

### Phase 3
- Strategy receives all subscribed data streams when replayed from catalog.
- Signal generation is stable (same inputs → same signals).
- At least one backtest run completes end-to-end.

### Phase 4
- Runner exports the full artifact set with a manifest sufficient to reproduce the run.
- Re-running with the same config produces identical artifacts (modulo timestamps inside logs).

---

# Risks and Mitigations

1. **Schema drift / mixed headers**
   - Risk: daily/monthly files may differ in header presence and column naming (`snake_case` vs `camelCase`, `true/false` vs `True/False`).
   - Mitigation: robust schema normalization layer per dataset type + strict column mapping tests.

2. **Timestamp inconsistencies (event_time vs transaction_time)**
   - Risk: `bookTicker` provides both `transaction_time` and `event_time`; choosing the wrong one may break determinism or align poorly with trades.
   - Mitigation: define a single canonical choice (default: `transaction_time`) and document it; optionally store the alternate field as metadata (only if supported without API fabrication).

3. **Volume/price precision mismatch with instrument spec**
   - Risk: quantization differences can create unrealistic fills or invalid tick sizes.
   - Mitigation: define instrument precision explicitly and normalize all parsed values through instrument helpers.

4. **Data volume (memory blowups)**
   - Risk: quote ticks are high frequency and can be huge.
   - Mitigation: day-by-day ingestion, query-by-date range, and avoid holding full-year tick data in memory unless explicitly requested.

5. **BookDepth misunderstanding**
   - Risk: `bookDepth` in the provided futures inventory appears to be *derived metrics*, not raw order book snapshots.
   - Mitigation: treat as a custom liquidity metric stream; do not attempt to construct L2 books from it.

6. **Survivorship / contract definition ambiguity**
   - Risk: futures symbol `BTCUSDT` may represent perpetual UM; naming and funding may differ across eras.
   - Mitigation: explicit instrument naming convention (`-PERP`) and require user confirmation for contract type (see Blocking Questions).

---

# Deliverables Checklist (Phase 2–4)

## Phase 2 (ETL + Catalog)
- [ ] Dataset registry supporting both observed futures layout and the alternate `future_data/*` layout.
- [ ] Parsers for: futures `aggTrades`, `trades`, `bookTicker`, `klines_1m`, `metrics`, `fundingRate`, `bookDepth` metrics format; and spot `aggTrades`, `trades`, `klines`.
- [ ] Deterministic timestamp normalization and event ordering.
- [ ] Dedup + validation framework.
- [ ] Output `ParquetDataCatalog` written to a configurable path.
- [ ] Unit tests for parsing, time conversion, dedup, and invariants.

## Phase 3 (Features + Strategy)
- [ ] Feature engine implementing: realized variance, volume-bucket FSI/VPIN, LDR from depth metric, OI z-score.
- [ ] Strategy using Nautilus `Strategy` + `StrategyConfig` patterns grounded in the provided examples.
- [ ] Execution logic (maker-biased entry using `QuoteTick`, exit on signal normalization).
- [ ] Risk and sizing per the mathematical specification (Kelly-style with caps).
- [ ] Strategy tests for invariants and deterministic signal generation on fixed data.

## Phase 4 (Runner + Artifacts)
- [ ] Config-driven runner that:
  - loads catalog data by date range
  - sets up venues/instruments
  - runs the strategy
- [ ] Artifact exporter producing:
  - fills/positions/account reports
  - manifest describing all inputs/configs
- [ ] Reproducibility checks: same config + same catalog → identical outputs.

---

# Blocking Questions (Must Answer Before Phase 2)

1. **Universe + market type:** Are we targeting **Futures UM perpetuals only**, **Spot only**, or **both**? If both, do you want **separate backtests** per venue (recommended) or a combined multi-venue setup?
2. **Strategy objective:** Should the default Phase 3 strategy be the **Phoenix/LPI liquidation-pressure strategy** implied by the provided prototype (FSI/VPIN/LDR/OI + Kelly sizing), or do you want a different baseline (e.g., EMA cross)?
3. **Backtest date range and instruments:** What are the exact **start/end timestamps (UTC)** and the exact **symbols** (e.g., `BTCUSDT` only, or multiple)?