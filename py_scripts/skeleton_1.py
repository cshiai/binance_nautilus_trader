# nautilus_trader-based Binance strategy testing skeleton (pipeline + orchestration only).
# Assumptions:
# - All nautilus_trader symbols/classes/functions referenced here are already imported into the runtime.
# - This file intentionally leaves "strategy design" as black boxes (placeholders with pass).
# - Minimal logging; favor throughput. No external deps beyond Python stdlib.

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence, Mapping, Optional
from pathlib import Path
from decimal import Decimal
import asyncio
import os
import time

# ---------------------------------------------------------------------------
# Core configs (lightweight & fast)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VenueProfile:
    venue: Venue  # e.g., BINANCE_VENUE
    account_type: BinanceAccountType  # SPOT, USDT_FUTURES, etc.
    testnet: bool = False
    us: bool = False
    key_type: BinanceKeyType = BinanceKeyType.HMAC
    proxy_url: str | None = None

@dataclass(frozen=True)
class DataWindow:
    start_ms: int | None = None  # inclusive
    end_ms: int | None = None    # inclusive
    limit: int | None = None

@dataclass(frozen=True)
class BarSpecReq:
    interval: BinanceKlineInterval  # e.g., BinanceKlineInterval.MINUTE_1
    window: DataWindow

@dataclass(frozen=True)
class TradeSpecReq:
    agg: bool = True  # True -> aggTrades; False -> recent trades (most recent only)
    window: DataWindow = DataWindow(limit=1000)

@dataclass(frozen=True)
class BookSpecReq:
    # Order book sources will typically be CSV-based deltas; we keep params for completeness
    depth_limit: int = 1000

@dataclass(frozen=True)
class MarkPriceSpecReq:
    subscribe: bool = True  # for live capture

@dataclass(frozen=True)
class InstrumentRequest:
    instrument_id: InstrumentId
    want_bars: list[BarSpecReq] = field(default_factory=list)
    want_trades: list[TradeSpecReq] = field(default_factory=list)
    want_books: list[BookSpecReq] = field(default_factory=list)

@dataclass(frozen=True)
class CatalogPaths:
    path: Path  # ParquetDataCatalog path (file system or memory:// is supported by Nautilus)

# ---------------------------------------------------------------------------
# Internal helpers (single place for all custom logic)
# ---------------------------------------------------------------------------

def _clock() -> LiveClock:
    return LiveClock()

def _http_client(clock: LiveClock, profile: VenueProfile) -> BinanceHttpClient:
    return get_cached_binance_http_client(
        clock=clock,
        account_type=profile.account_type,
        api_key=os.getenv("BINANCE_API_KEY") if not profile.testnet else (
            os.getenv("BINANCE_TESTNET_API_KEY") if profile.account_type.is_spot_or_margin
            else os.getenv("BINANCE_FUTURES_TESTNET_API_KEY")
        ),
        api_secret=os.getenv("BINANCE_API_SECRET") if not profile.testnet else (
            os.getenv("BINANCE_TESTNET_API_SECRET") if profile.account_type.is_spot_or_margin
            else os.getenv("BINANCE_FUTURES_TESTNET_API_SECRET")
        ),
        key_type=profile.key_type,
        base_url=None,
        is_testnet=profile.testnet,
        is_us=profile.us,
        proxy_url=profile.proxy_url,
    )

def _instrument_provider(client: BinanceHttpClient, clock: LiveClock, profile: VenueProfile) -> InstrumentProvider:
    cfg = InstrumentProviderConfig(load_all=False, log_warnings=False)
    if profile.account_type.is_spot_or_margin:
        return get_cached_binance_spot_instrument_provider(
            client=client, clock=clock, account_type=profile.account_type, is_testnet=profile.testnet, config=cfg, venue=profile.venue,
        )
    else:
        return get_cached_binance_futures_instrument_provider(
            client=client, clock=clock, account_type=profile.account_type, config=cfg, venue=profile.venue,
        )

def _market_api(client: BinanceHttpClient, profile: VenueProfile) -> BinanceMarketHttpAPI:
    if profile.account_type.is_spot_or_margin:
        return BinanceSpotMarketHttpAPI(client=client, account_type=profile.account_type)
    else:
        return BinanceFuturesMarketHttpAPI(client=client, account_type=profile.account_type)

def _ensure_catalog(catalog_path: Path) -> ParquetDataCatalog:
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    return ParquetDataCatalog(str(catalog_path))

def _write_instruments_to_catalog(catalog: ParquetDataCatalog, instruments: Iterable[Instrument]) -> None:
    catalog.write_data(list(instruments))

def _bar_type_for_kline(instrument_id: InstrumentId, interval: BinanceKlineInterval) -> BarType:
    parser = BinanceSpotEnumParser() if instrument_id.venue.value.endswith("SPOT") else BinanceFuturesEnumParser()
    spec = parser.parse_binance_kline_interval_to_bar_spec(interval)
    return BarType(instrument_id=instrument_id, bar_spec=spec, aggregation_source=AggregationSource.EXTERNAL)

def _now_ns() -> int:
    return time.time_ns()

def _millis(t: int | float | None) -> int | None:
    if t is None:
        return None
    return int(t)

def _as_list(x: Any | Sequence[Any]) -> list[Any]:
    if x is None:
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]

# ---------------------------------------------------------------------------
# Instrument loading
# ---------------------------------------------------------------------------

async def load_instruments(profile: VenueProfile, instrument_ids: list[InstrumentId] | None, load_all: bool = False) -> dict[str, Instrument]:
    clk = _clock()
    client = _http_client(clk, profile)
    provider = _instrument_provider(client, clk, profile)
    if load_all:
        await provider.load_all_async()
    elif instrument_ids:
        await provider.load_ids_async(instrument_ids=instrument_ids)
    else:
        await provider.load_all_async()
    return provider.get_all()  # {symbol_str: Instrument}

# ---------------------------------------------------------------------------
# Historical data ingestion (to catalog)
# ---------------------------------------------------------------------------

async def fetch_and_store_bars(profile: VenueProfile, catalog: ParquetDataCatalog, reqs: list[tuple[InstrumentId, BarSpecReq]]):
    clk = _clock()
    client = _http_client(clk, profile)
    market = _market_api(client, profile)
    out: list[BinanceBar] = []
    for instrument_id, barspec in reqs:
        bt = _bar_type_for_kline(instrument_id, barspec.interval)
        bars = await market.request_binance_bars(
            bar_type=bt,
            interval=barspec.interval,
            limit=barspec.window.limit,
            start_time=_millis(barspec.window.start_ms),
            end_time=_millis(barspec.window.end_ms),
        )
        if bars:
            out.extend(bars)
    if out:
        catalog.write_data(out)

async def fetch_and_store_trades(profile: VenueProfile, catalog: ParquetDataCatalog, reqs: list[tuple[InstrumentId, TradeSpecReq]]):
    clk = _clock()
    client = _http_client(clk, profile)
    market = _market_api(client, profile)
    out_ticks: list[TradeTick] = []
    for instrument_id, tspec in reqs:
        if tspec.agg:
            ticks = await market.request_agg_trade_ticks(
                instrument_id=instrument_id,
                limit=tspec.window.limit,
                start_time=_millis(tspec.window.start_ms),
                end_time=_millis(tspec.window.end_ms),
                from_id=None,
            )
        else:
            # recent trades (most-recent, no time window honored by Binance)
            ticks = await market.request_trade_ticks(
                instrument_id=instrument_id,
                limit=tspec.window.limit or 500,
            )
        if ticks:
            out_ticks.extend(ticks)
    if out_ticks:
        catalog.write_data(out_ticks)

def store_orderbook_deltas_from_csv(catalog: ParquetDataCatalog, instrument: Instrument, csv_paths: list[Path], max_rows_each: int | None = None):
    wrangler = OrderBookDeltaDataWrangler(instrument)
    deltas: list[OrderBookDelta] = []
    for path in csv_paths:
        df = BinanceOrderBookDeltaDataLoader.load(str(path), nrows=max_rows_each)
        deltas += wrangler.process(df)
    deltas.sort(key=lambda x: x.ts_init)
    if deltas:
        catalog.write_data(deltas)

# ---------------------------------------------------------------------------
# Live capture (stream to catalog) – optional
# ---------------------------------------------------------------------------

def build_live_capture_node(profile: VenueProfile, catalog_path: Path, instrument_ids: list[InstrumentId], subscribe: Mapping[str, bool] | None = None) -> TradingNode:
    # subscribe keys: {"quotes": True, "trades": True, "bars_1m": True, "book": True, "mark_price": True}
    subscribe = subscribe or {"quotes": True, "trades": True, "bars_1m": False, "book": False, "mark_price": False}
    cfg_node = TradingNodeConfig(
        trader_id=TraderId("CAPTURE-001"),
        logging=LoggingConfig(log_level="INFO"),
        cache=CacheConfig(timestamps_as_iso8601=False, flush_on_start=False),
        exec_engine=LiveExecEngineConfig(reconciliation=False),
        streaming=StreamingConfig(catalog_path=str(catalog_path)),
        data_clients={
            profile.venue.value: BinanceDataClientConfig(
                venue=profile.venue,
                api_key=None,  # env-based
                api_secret=None,
                account_type=profile.account_type,
                base_url_http=None,
                base_url_ws=None,
                us=profile.us,
                testnet=profile.testnet,
                instrument_provider=InstrumentProviderConfig(load_all=True),
                use_agg_trade_ticks=True,
            ),
        },
    )
    node = TradingNode(config=cfg_node)
    node.add_data_client_factory(profile.venue.value, BinanceLiveDataClientFactory)
    node.build()
    # subscriptions (lazy; concrete strategies/modules omitted)
    trader = node.trader
    for iid in instrument_ids:
        if subscribe.get("quotes"):
            trader.subscribe_quote_ticks(instrument_id=iid)
        if subscribe.get("trades"):
            trader.subscribe_trade_ticks(instrument_id=iid)
        if subscribe.get("book"):
            trader.subscribe_order_book_deltas(iid, depth=1000, update_speed=0 if profile.account_type.is_futures else 100)
        if subscribe.get("bars_1m"):
            bar_type = BarType.from_str(f"{iid}-1-MINUTE-LAST-EXTERNAL")
            trader.subscribe_bars(bar_type)
        if subscribe.get("mark_price") and profile.account_type.is_futures:
            trader.subscribe_data(
                data_type=DataType(BinanceFuturesMarkPriceUpdate, metadata={"instrument_id": iid}),
                client_id=ClientId(profile.venue.value),
            )
    return node

# ---------------------------------------------------------------------------
# Backtest assembly
# ---------------------------------------------------------------------------

def _bt_venue_config(venue: Venue, oms: str = "NETTING", account_type: str = "CASH", base_ccy: str | None = None, balances: list[str] | None = None, book_type: str | None = None) -> BacktestVenueConfig:
    return BacktestVenueConfig(
        name=venue.value,
        oms_type=oms,
        account_type=account_type,
        base_currency=base_ccy,
        starting_balances=balances or [],
        book_type=book_type,
    )

def _bt_data_configs(catalog_path: Path, entries: list[tuple[InstrumentId, Any]]) -> list[BacktestDataConfig]:
    cfgs: list[BacktestDataConfig] = []
    for iid, data_cls in entries:
        cfgs.append(
            BacktestDataConfig(
                catalog_path=str(catalog_path),
                data_cls=data_cls,
                instrument_id=iid,
            )
        )
    return cfgs

def _bt_engine_config(strategy_configs: list[ImportableStrategyConfig] | None) -> BacktestEngineConfig:
    return BacktestEngineConfig(
        strategies=strategy_configs or [],
        logging=LoggingConfig(log_level="ERROR"),
    )

def build_backtest_run_config(catalog_path: Path, venue_cfg: BacktestVenueConfig, data_cfgs: list[BacktestDataConfig], strategy_cfgs: list[ImportableStrategyConfig] | None) -> BacktestRunConfig:
    return BacktestRunConfig(
        engine=_bt_engine_config(strategy_cfgs),
        data=data_cfgs,
        venues=[venue_cfg],
    )

def run_backtests(run_cfgs: list[BacktestRunConfig]) -> dict[str, Any]:
    node = BacktestNode(configs=run_cfgs)
    result = node.run()
    # Reports – leave generation to caller; example:
    # engine = node.get_engine(run_cfgs[0].id)
    # engine.trader.generate_order_fills_report(), generate_positions_report(), generate_account_report(Venue(...))
    return {"node": node, "result": result}

# ---------------------------------------------------------------------------
# Batch orchestration helpers (async)
# ---------------------------------------------------------------------------

async def pipeline_fetch_to_catalog(profile: VenueProfile, catalog_path: Path, instruments: dict[str, Instrument], bar_reqs: list[tuple[InstrumentId, BarSpecReq]] | None, trade_reqs: list[tuple[InstrumentId, TradeSpecReq]] | None):
    catalog = _ensure_catalog(catalog_path)
    _write_instruments_to_catalog(catalog, instruments.values())
    tasks = []
    if bar_reqs:
        tasks.append(fetch_and_store_bars(profile, catalog, bar_reqs))
    if trade_reqs:
        tasks.append(fetch_and_store_trades(profile, catalog, trade_reqs))
    if tasks:
        await asyncio.gather(*tasks)

# ---------------------------------------------------------------------------
# Strategy black-box placeholders (do not implement)
# ---------------------------------------------------------------------------

def create_strategy_configs_for_instruments(instrument_ids: list[InstrumentId]) -> list[ImportableStrategyConfig]:
    """
    Create strategy configs for given instruments.
    Do not implement strategy logic – placeholder only.
    """
    pass

def score_backtest_results(node: BacktestNode) -> Mapping[str, float]:
    """
    Evaluate backtest runs and produce metrics for selection/analysis.
    """
    pass

def hyperparameter_sweep(instrument_ids: list[InstrumentId]) -> list[ImportableStrategyConfig]:
    """
    Generate strategy configs across a param grid. Leave blank.
    """
    pass

def select_top_strategies(scores: Mapping[str, float], k: int) -> list[str]:
    """
    Pick top-k strategy identifiers by score.
    """
    pass

# ---------------------------------------------------------------------------
# Example end-to-end assembly (skeleton)
# ---------------------------------------------------------------------------

async def assemble_and_run_example(
    profile: VenueProfile,
    catalog_paths: CatalogPaths,
    instruments_req: list[InstrumentRequest],
    balances: list[str] | None = None,
    book_type: str | None = None,
) -> dict[str, Any]:
    # 1) Load instruments
    all_iids = [req.instrument_id for req in instruments_req]
    instruments = await load_instruments(profile, instrument_ids=all_iids, load_all=False)

    # 2) Build data requests
    bar_reqs: list[tuple[InstrumentId, BarSpecReq]] = []
    trade_reqs: list[tuple[InstrumentId, TradeSpecReq]] = []
    for req in instruments_req:
        for b in req.want_bars:
            bar_reqs.append((req.instrument_id, b))
        for t in req.want_trades:
            trade_reqs.append((req.instrument_id, t))

    # 3) Fetch and store into catalog
    await pipeline_fetch_to_catalog(
        profile=profile,
        catalog_path=catalog_paths.path,
        instruments=instruments,
        bar_reqs=bar_reqs,
        trade_reqs=trade_reqs,
    )

    # 4) Prepare backtest configs (bars/trades/books – present if written to catalog)
    data_entries: list[tuple[InstrumentId, Any]] = []
    for req in instruments_req:
        if req.want_bars:
            data_entries.append((req.instrument_id, BinanceBar))  # EXTERNAL time bars class
        if req.want_trades:
            data_entries.append((req.instrument_id, TradeTick))
        if req.want_books:
            data_entries.append((req.instrument_id, OrderBookDelta))
    data_cfgs = _bt_data_configs(catalog_paths.path, data_entries)

    # 5) Venue config
    venue_cfg = _bt_venue_config(
        venue=profile.venue,
        oms="NETTING" if not profile.account_type.is_futures else "NETTING",
        account_type="CASH" if not profile.account_type.is_futures else "MARGIN",
        base_ccy=None,
        balances=balances or [],
        book_type=book_type,
    )

    # 6) Strategy configs (black box)
    strat_cfgs = create_strategy_configs_for_instruments(all_iids)

    # 7) Backtest run config and execution
    run_cfg = build_backtest_run_config(
        catalog_path=catalog_paths.path,
        venue_cfg=venue_cfg,
        data_cfgs=data_cfgs,
        strategy_cfgs=strat_cfgs,
    )
    res = run_backtests([run_cfg])

    # 8) Scoring (black box)
    # You may extract engine/trader to produce reports here if desired before scoring.
    # engine = res["node"].get_engine(run_cfg.id)
    # engine.trader.generate_order_fills_report(); engine.trader.generate_positions_report(); engine.trader.generate_account_report(profile.venue)
    _ = score_backtest_results(res["node"])

    return res

# ---------------------------------------------------------------------------
# Order book CSV pipeline helper (example wiring)
# ---------------------------------------------------------------------------

def pipe_orderbook_csvs_to_catalog(
    catalog_path: Path,
    instrument: Instrument,
    snapshot_csv: Path | None,
    updates_csvs: list[Path] | None,
    max_rows_each: int | None = None,
):
    catalog = _ensure_catalog(catalog_path)
    _write_instruments_to_catalog(catalog, [instrument])
    paths: list[Path] = []
    if snapshot_csv:
        paths.append(snapshot_csv)
    if updates_csvs:
        paths.extend(updates_csvs)
    if paths:
        store_orderbook_deltas_from_csv(catalog, instrument, paths, max_rows_each=max_rows_each)

# ---------------------------------------------------------------------------
# Futures Hedge Mode wiring (skeleton)
# ---------------------------------------------------------------------------

def build_futures_hedge_mode_node(profile: VenueProfile, instrument_id: InstrumentId) -> TradingNode:
    cfg = TradingNodeConfig(
        trader_id=TraderId("HEDGE-001"),
        logging=LoggingConfig(log_level="INFO"),
        data_clients={
            profile.venue.value: BinanceDataClientConfig(
                api_key=None,
                api_secret=None,
                account_type=profile.account_type,
                us=profile.us,
                testnet=profile.testnet,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        exec_clients={
            profile.venue.value: BinanceExecClientConfig(
                api_key=None,
                api_secret=None,
                account_type=profile.account_type,
                us=profile.us,
                testnet=profile.testnet,
                instrument_provider=InstrumentProviderConfig(load_all=True),
                use_reduce_only=False,  # required for Hedge mode
                use_position_ids=True,
            ),
        },
    )
    node = TradingNode(config=cfg)
    node.add_data_client_factory(profile.venue.value, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(profile.venue.value, BinanceLiveExecClientFactory)
    node.build()
    # Strategy placeholders (no implementation). Example order submission with position_id suffix:
    # LONG: PositionId(f"{instrument_id}-LONG"), SHORT: PositionId(f"{instrument_id}-SHORT")
    return node

# ---------------------------------------------------------------------------
# Futures priceMatch skeleton helpers (no execution here; illustration only)
# ---------------------------------------------------------------------------

def submit_limit_with_price_match_blackbox(strategy: Strategy, instrument_id: InstrumentId, qty: Quantity, ref_price: Price, mode: str) -> None:
    """
    Example wiring for submitting LIMIT with exchange-side priceMatch (Futures only).
    Do not implement actual strategy logic here.
    """
    pass

# ---------------------------------------------------------------------------
# Example "main" driver (skeleton only; not executed automatically)
# ---------------------------------------------------------------------------

async def _example_main():
    profile = VenueProfile(venue=BINANCE_VENUE, account_type=BinanceAccountType.USDT_FUTURES, testnet=True, us=False)
    catalog_paths = CatalogPaths(path=Path("./catalog_binance"))
    i1 = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
    reqs = [
        InstrumentRequest(
            instrument_id=i1,
            want_bars=[BarSpecReq(interval=BinanceKlineInterval.MINUTE_1, window=DataWindow(start_ms=None, end_ms=None, limit=1000))],
            want_trades=[TradeSpecReq(agg=True, window=DataWindow(start_ms=None, end_ms=None, limit=1000))],
            want_books=[],
        ),
    ]
    await assemble_and_run_example(profile, catalog_paths, reqs, balances=["10000 USDT"], book_type=None)

# ----------------------------------------------------------------------------
# Note: to run the example, call: asyncio.run(_example_main())
# ----------------------------------------------------------------------------
