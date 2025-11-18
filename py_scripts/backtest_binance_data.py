#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
onefile_nautilus_binance_instrumentation.py

End-to-end one-file script to fetch Binance Spot + USDT-M Futures market data
via nautilus_trader adapter HTTP APIs, instrument them into nautilus_trader
Data objects, persist in ParquetDataCatalog, emit auxiliary endpoint payloads
as Parquet tables, and run a minimal backtest to verify catalog usability.

Assumptions:
- All nautilus_trader symbols/classes are already imported and available
  in the execution environment (Strategy, StrategyConfig, BacktestEngine,
  BacktestEngineConfig, BacktestVenueConfig, LoggingConfig, OmsType,
  AccountType, Venue, InstrumentId, Symbol, Money, Currency, Quantity, Price,
  BarType, BarAggregation, BarSpecification, PriceType, AggregationSource,
  QuoteTick, TradeTick, OrderBookDelta, OrderBookDeltas, RecordFlag,
  ClientId, LiveClock, ParquetDataCatalog, InstrumentProviderConfig, Logger,
  BINANCE_VENUE or Venue("BINANCE"), etc.).
- Binance adapter symbols are available (BinanceAccountType, BinanceKeyType,
  BinanceSymbol, BinanceKlineInterval, get_cached_binance_http_client,
  BinanceSpotMarketHttpAPI, BinanceFuturesMarketHttpAPI,
  BinanceSpotInstrumentProvider, BinanceFuturesInstrumentProvider,
  BinanceTicker, BinanceBar).
- Only REST is used (no live trading). API keys used for read-only queries.

Environment variables (optional):
  OUTPUT_DIR                default: ./catalog_binance
  AUX_DIR                   default: ./aux_parquet
  SPOT_SYMBOL               default: BTCUSDT           (HTTP symbol)
  FUTURES_SYMBOL            default: BTCUSDT           (HTTP symbol, PERP is derived)
  FUTURES_PAIR              default: FUTURES_SYMBOL    (pair for continuousKlines/index klines)
  CONTINUOUS_CONTRACT_TYPE  default: PERPETUAL         (or CURRENT_QUARTER, NEXT_QUARTER, etc.)
  LOOKBACK_MINUTES          default: 1440              (history window for trades/klines)
  BARS_INTERVAL             default: 1m                (one of BinanceKlineInterval values)
  TRADE_LIMIT               default: 1000
  KLINE_LIMIT               default: 1000
  METRIC_LIMIT              default: 500
  FUNDING_LIMIT             default: 500
  TESTNET                   default: 0
  US                        default: 0
  API_KEY                   default: unset
  API_SECRET                default: unset
  PROXY_URL                 default: unset
"""

from __future__ import annotations
import os, sys, json, time, math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import msgspec


# ------------------------------- Utilities -------------------------------

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip().lower()
    if v in ("1", "true", "yes", "y", "on"): return True
    if v in ("0", "false", "no", "n", "off"): return False
    return default

def _now_ns() -> int:
    return time.time_ns()

def _ms_to_ns(ms: int | float | None) -> int:
    if ms is None: return _now_ns()
    return int(int(ms) * 1_000_000)

def _to_ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def _safe_decimal(x: Any, default: str = "0") -> Decimal:
    try:
        if x is None: return Decimal(default)
        return Decimal(str(x))
    except Exception:
        return Decimal(default)

def _df_to_parquet(df: pd.DataFrame, path: Path) -> None:
    if df.empty:
        # write empty table with no rows but with schema to keep artifacts consistent
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, path)
        return
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, path)

def _flatten(obj: Any) -> pd.DataFrame:
    try:
        if isinstance(obj, list):
            return pd.json_normalize(obj)
        return pd.json_normalize(obj)
    except Exception:
        return pd.DataFrame({"payload":[json.dumps(obj)]})

def _bt_minutes_ago(minutes: int) -> Tuple[int, int]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    return _to_ms(start), _to_ms(end)

def _mk_bar_type(instrument_id: InstrumentId, step: int, agg: BarAggregation, price_type: PriceType, source: AggregationSource) -> BarType:
    return BarType(instrument_id=instrument_id, bar_spec=BarSpecification(step, agg, price_type), aggregation_source=source)

def _parse_interval(s: str) -> BinanceKlineInterval:
    s = s.strip()
    # Allowed: "1s","1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"
    return BinanceKlineInterval(s)

def _venue() -> Venue:
    try:
        return BINANCE_VENUE  # provided by adapter
    except NameError:
        return Venue("BINANCE")


# --------------------------- Converters to Data ---------------------------

def _ticker24hr_to_binance_ticker(t: Any, instrument_id: InstrumentId, ts_init_ns: int) -> BinanceTicker:
    price_change = _safe_decimal(getattr(t, "priceChange", None))
    price_change_percent = _safe_decimal(getattr(t, "priceChangePercent", None))
    weighted_avg_price = _safe_decimal(getattr(t, "weightedAvgPrice", None))
    last_price = _safe_decimal(getattr(t, "lastPrice", None))
    last_qty = _safe_decimal(getattr(t, "lastQty", None))
    open_price = _safe_decimal(getattr(t, "openPrice", None))
    high_price = _safe_decimal(getattr(t, "highPrice", None))
    low_price = _safe_decimal(getattr(t, "lowPrice", None))
    volume = _safe_decimal(getattr(t, "volume", None))
    quote_volume = _safe_decimal(getattr(t, "quoteVolume", None))
    open_time_ms = int(getattr(t, "openTime", 0) or 0)
    close_time_ms = int(getattr(t, "closeTime", 0) or 0)
    first_id = int(getattr(t, "firstId", 0) or 0)
    last_id = int(getattr(t, "lastId", 0) or 0)
    count = int(getattr(t, "count", 0) or 0)
    prev_close_price = None
    if hasattr(t, "prevClosePrice"):
        try:
            prev_close_price = Decimal(str(t.prevClosePrice))
        except Exception:
            prev_close_price = None
    # Optional bid/ask fields present for SPOT 24hr FULL, if missing set None
    bid_price = None
    bid_qty = None
    ask_price = None
    ask_qty = None
    if hasattr(t, "bidPrice") and t.bidPrice is not None: bid_price = Decimal(str(t.bidPrice))
    if hasattr(t, "bidQty") and t.bidQty is not None: bid_qty = Decimal(str(t.bidQty))
    if hasattr(t, "askPrice") and t.askPrice is not None: ask_price = Decimal(str(t.askPrice))
    if hasattr(t, "askQty") and t.askQty is not None: ask_qty = Decimal(str(t.askQty))
    ts_event_ns = _ms_to_ns(close_time_ms) if close_time_ms else ts_init_ns
    return BinanceTicker(
        instrument_id=instrument_id,
        price_change=price_change,
        price_change_percent=price_change_percent,
        weighted_avg_price=weighted_avg_price,
        last_price=last_price,
        last_qty=last_qty,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        volume=volume,
        quote_volume=quote_volume,
        open_time_ms=open_time_ms,
        close_time_ms=close_time_ms,
        first_id=first_id,
        last_id=last_id,
        count=count,
        ts_event=ts_event_ns,
        ts_init=ts_init_ns,
        prev_close_price=prev_close_price,
        bid_price=bid_price,
        bid_qty=bid_qty,
        ask_price=ask_price,
        ask_qty=ask_qty,
    )

def _ticker_book_to_quote_tick(obj: Any, instrument_id: InstrumentId, ts_init_ns: int) -> QuoteTick:
    # SPOT: no 'time'; FUTURES: has 'time'
    ts_event_ns = _ms_to_ns(getattr(obj, "time", None))
    return QuoteTick(
        instrument_id=instrument_id,
        bid_price=Price.from_str(getattr(obj, "bidPrice", "0")),
        ask_price=Price.from_str(getattr(obj, "askPrice", "0")),
        bid_size=Quantity.from_str(getattr(obj, "bidQty", "0")),
        ask_size=Quantity.from_str(getattr(obj, "askQty", "0")),
        ts_event=ts_event_ns,
        ts_init=ts_init_ns,
    )

def _depth_to_deltas(depth: Any, instrument_id: InstrumentId, ts_init_ns: int) -> List[OrderBookDelta]:
    # depth is BinanceDepth msgspec Struct with .parse_to_order_book_snapshot
    snap: OrderBookDeltas = depth.parse_to_order_book_snapshot(instrument_id=instrument_id, ts_init=ts_init_ns)
    # Flatten the deltas list for catalog write
    return list(snap.deltas or [])

def _klines_to_binance_bars(klines: List[Any], bar_type: BarType, ts_init_ns: int) -> List[BinanceBar]:
    bars: List[BinanceBar] = []
    # HTTP Klines are BinanceKline (array-like) with .parse_to_binance_bar(bar_type, ts_init)
    for k in klines:
        bars.append(k.parse_to_binance_bar(bar_type=bar_type, ts_init=ts_init_ns))
    return bars


# -------------------------- Data Fetch + Instrument --------------------------

class BinanceBacktestInstrumenter:
    def __init__(
        self,
        output_dir: Path,
        aux_dir: Path,
        spot_symbol: str,
        fut_symbol: str,
        fut_pair: str,
        cont_type: str,
        bars_interval: str,
        lookback_minutes: int,
        trade_limit: int,
        kline_limit: int,
        metric_limit: int,
        funding_limit: int,
        is_testnet: bool,
        is_us: bool,
        api_key: Optional[str],
        api_secret: Optional[str],
        proxy_url: Optional[str],
    ) -> None:
        self.out_dir = output_dir
        self.aux_dir = aux_dir
        _ensure_dir(self.out_dir)
        _ensure_dir(self.aux_dir)
        self.venue = _venue()
        self.client_clock = LiveClock()
        self.spot_symbol = spot_symbol.upper()
        self.fut_symbol = fut_symbol.upper()
        self.fut_pair = fut_pair.upper()
        self.cont_type = cont_type.upper()
        self.interval = _parse_interval(bars_interval)
        self.lookback_mins = lookback_minutes
        self.trade_limit = max(1, trade_limit)
        self.kline_limit = max(1, kline_limit)
        self.metric_limit = max(1, metric_limit)
        self.funding_limit = max(1, funding_limit)
        self.is_testnet = is_testnet
        self.is_us = is_us
        self.api_key = api_key or None
        self.api_secret = api_secret or None
        self.proxy_url = proxy_url or None

        # HTTP Clients (singleton cache)
        self.spot_client: BinanceHttpClient = get_cached_binance_http_client(
            clock=self.client_clock,
            account_type=BinanceAccountType.SPOT,
            api_key=self.api_key,
            api_secret=self.api_secret,
            key_type=BinanceKeyType.HMAC,
            base_url=None,
            is_testnet=self.is_testnet,
            is_us=self.is_us,
            proxy_url=self.proxy_url,
        )
        self.fut_client: BinanceHttpClient = get_cached_binance_http_client(
            clock=self.client_clock,
            account_type=BinanceAccountType.USDT_FUTURES,
            api_key=self.api_key,
            api_secret=self.api_secret,
            key_type=BinanceKeyType.HMAC,
            base_url=None,
            is_testnet=self.is_testnet,
            is_us=self.is_us,
            proxy_url=self.proxy_url,
        )

        # Typed REST APIs
        self.spot_market = BinanceSpotMarketHttpAPI(self.spot_client, account_type=BinanceAccountType.SPOT)
        self.fut_market = BinanceFuturesMarketHttpAPI(self.fut_client, account_type=BinanceAccountType.USDT_FUTURES)

        # Instrument Providers (to produce Instrument objects)
        self.spot_provider = BinanceSpotInstrumentProvider(
            client=self.spot_client,
            clock=self.client_clock,
            account_type=BinanceAccountType.SPOT,
            is_testnet=self.is_testnet,
            config=InstrumentProviderConfig(load_all=False, log_warnings=False),
            venue=self.venue,
        )
        self.fut_provider = BinanceFuturesInstrumentProvider(
            client=self.fut_client,
            clock=self.client_clock,
            account_type=BinanceAccountType.USDT_FUTURES,
            config=InstrumentProviderConfig(load_all=False, log_warnings=False),
            venue=self.venue,
        )

        # IDs
        self.spot_instrument_id = InstrumentId.from_str(f"{self.spot_symbol}.{self.venue.value}")
        self.fut_perp_instrument_id = InstrumentId.from_str(f"{self.fut_symbol}-PERP.{self.venue.value}")

        self.catalog = ParquetDataCatalog(str(self.out_dir))

    async def _raw_get(self, client: BinanceHttpClient, url_path: str, params: Dict[str, str] | None = None) -> Any:
        raw = await client.send_request(HttpMethod.GET, url_path=url_path, payload=params or {}, ratelimiter_keys=None)
        try:
            return msgspec.json.decode(raw)
        except Exception:
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {"raw": raw.decode("utf-8", errors="ignore")}

    async def load_instruments(self) -> Tuple[Instrument, Instrument]:
        # Spot
        await self.spot_provider.load_ids_async([self.spot_instrument_id])
        spot_inst = self.spot_provider.find(self.spot_instrument_id)
        if spot_inst is None:
            raise RuntimeError(f"Cannot load spot instrument {self.spot_instrument_id}")
        # Futures perp
        await self.fut_provider.load_ids_async([self.fut_perp_instrument_id])
        fut_inst = self.fut_provider.find(self.fut_perp_instrument_id)
        if fut_inst is None:
            raise RuntimeError(f"Cannot load futures instrument {self.fut_perp_instrument_id}")
        # Persist instruments
        self.catalog.write_data([spot_inst, fut_inst])
        return spot_inst, fut_inst

    async def instrument_spot(self, instrument: Instrument) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        ts_init_ns = self.client_clock.timestamp_ns()
        start_ms, end_ms = _bt_minutes_ago(self.lookback_mins)

        # Typed REST -> Data
        # Bars (external aggregation)
        bar_type_ext_1m = _mk_bar_type(instrument.id, 1, BarAggregation.MINUTE, PriceType.LAST, AggregationSource.EXTERNAL)
        klines = await self.spot_market.query_klines(
            symbol=self.spot_symbol,
            interval=self.interval,
            limit=self.kline_limit,
            start_time=start_ms,
            end_time=end_ms,
        )
        bars = _klines_to_binance_bars(klines, bar_type_ext_1m, ts_init_ns)
        out["bars"] = bars

        # Trades (aggregated trades over time window)
        trade_ticks = await self.spot_market.request_agg_trade_ticks(
            instrument_id=instrument.id,
            start_time=start_ms,
            end_time=end_ms,
            limit=self.trade_limit,
        )
        out["trade_ticks"] = trade_ticks

        # Order book snapshot -> OrderBookDelta[]
        depth = await self.spot_market.query_depth(symbol=self.spot_symbol, limit=min(1000, max(100, self.trade_limit)))
        deltas = _depth_to_deltas(depth, instrument.id, ts_init_ns)
        out["order_book_deltas"] = deltas

        # Ticker 24hr -> BinanceTicker
        tickers24 = await self.spot_market._endpoint_ticker_24hr._get(
            params=self.spot_market._endpoint_ticker_24hr.GetParameters(symbol=BinanceSymbol(self.spot_symbol), symbols=None, type=None)
        )
        spot_tickers: List[BinanceTicker] = []
        for t in tickers24:
            spot_tickers.append(_ticker24hr_to_binance_ticker(t, instrument.id, ts_init_ns))
        out["tickers_24hr"] = spot_tickers

        # bookTicker -> QuoteTick
        tick_book = await self.spot_market._endpoint_ticker_book._get(
            params=self.spot_market._endpoint_ticker_book.GetParameters(symbol=BinanceSymbol(self.spot_symbol), symbols=None)
        )
        quotes: List[QuoteTick] = []
        for tb in tick_book:
            quotes.append(_ticker_book_to_quote_tick(tb, instrument.id, ts_init_ns))
        out["quote_ticks"] = quotes

        # Persist to catalog
        # Write instruments previously; here write data
        # Write in small batches to reduce memory overhead
        if bars: self.catalog.write_data(bars)
        if trade_ticks: self.catalog.write_data(trade_ticks)
        if deltas: self.catalog.write_data(deltas)
        if spot_tickers: self.catalog.write_data(spot_tickers)
        if quotes: self.catalog.write_data(quotes)

        # AUX dumps (raw endpoints to Parquet)
        # time
        spot_time = await self.spot_market._endpoint_time.get()
        _df_to_parquet(_flatten({"serverTime": spot_time.serverTime, "retrieved_at": _iso_now()}), self.aux_dir / "spot_time.parquet")
        # exchangeInfo
        ex_info = await BinanceSpotMarketHttpAPI(self.spot_client).query_spot_exchange_info(symbol=self.spot_symbol)
        _df_to_parquet(_flatten(msgspec.structs.asdict(ex_info)), self.aux_dir / "spot_exchange_info.parquet")
        # depth raw
        _df_to_parquet(pd.DataFrame(depth.bids, columns=["price","qty"]).assign(side="bid"), self.aux_dir / "spot_depth_bids.parquet")
        _df_to_parquet(pd.DataFrame(depth.asks, columns=["price","qty"]).assign(side="ask"), self.aux_dir / "spot_depth_asks.parquet")
        # trades (recent)
        trades_recent = await self.spot_market._endpoint_trades.get(self.spot_market._endpoint_trades.GetParameters(symbol=BinanceSymbol(self.spot_symbol), limit=min(1000, self.trade_limit)))
        _df_to_parquet(_flatten([msgspec.structs.asdict(x) for x in trades_recent]), self.aux_dir / "spot_trades.parquet")
        # historicalTrades
        trades_hist = await self.spot_market._endpoint_historical_trades.get(self.spot_market._endpoint_historical_trades.GetParameters(symbol=BinanceSymbol(self.spot_symbol), limit=min(1000, self.trade_limit), fromId=None))
        _df_to_parquet(_flatten([msgspec.structs.asdict(x) for x in trades_hist]), self.aux_dir / "spot_historical_trades.parquet")
        # aggTrades
        agg_trades = await self.spot_market._endpoint_agg_trades.get(self.spot_market._endpoint_agg_trades.GetParameters(symbol=BinanceSymbol(self.spot_symbol), limit=min(1000, self.trade_limit), fromId=None, startTime=None, endTime=None))
        _df_to_parquet(_flatten([msgspec.structs.asdict(x) for x in agg_trades]), self.aux_dir / "spot_agg_trades.parquet")
        # klines (HTTP)
        _df_to_parquet(_flatten([msgspec.structs.asdict(k) for k in klines]), self.aux_dir / "spot_klines.parquet")
        # uiKlines (raw)
        ui_klines = await self._raw_get(self.spot_client, "/api/v3/uiKlines", {"symbol": self.spot_symbol, "interval": self.interval.value, "limit": str(self.kline_limit)})
        _df_to_parquet(_flatten(ui_klines), self.aux_dir / "spot_ui_klines.parquet")
        # avgPrice
        avg_px = await BinanceSpotMarketHttpAPI(self.spot_client)._endpoint_spot_average_price.get(BinanceSpotMarketHttpAPI(self.spot_client)._endpoint_spot_average_price.GetParameters(symbol=BinanceSymbol(self.spot_symbol)))
        _df_to_parquet(_flatten(msgspec.structs.asdict(avg_px)), self.aux_dir / "spot_avg_price.parquet")
        # ticker 24hr raw
        _df_to_parquet(_flatten([msgspec.structs.asdict(x) for x in tickers24]), self.aux_dir / "spot_ticker_24hr.parquet")
        # trading day ticker (raw)
        trading_day = await self._raw_get(self.spot_client, "/api/v3/ticker/tradingDay", {"symbol": self.spot_symbol})
        _df_to_parquet(_flatten(trading_day), self.aux_dir / "spot_trading_day_ticker.parquet")
        # ticker price raw
        spot_price_arr = await self.spot_market._endpoint_ticker_price._get(self.spot_market._endpoint_ticker_price.GetParameters(symbol=BinanceSymbol(self.spot_symbol), symbols=None))
        _df_to_parquet(_flatten([msgspec.structs.asdict(x) for x in spot_price_arr]), self.aux_dir / "spot_ticker_price.parquet")
        # bookTicker raw
        _df_to_parquet(_flatten([msgspec.structs.asdict(x) for x in tick_book]), self.aux_dir / "spot_book_ticker.parquet")

        return out

    async def instrument_futures(self, instrument: Instrument) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        ts_init_ns = self.client_clock.timestamp_ns()
        start_ms, end_ms = _bt_minutes_ago(self.lookback_mins)

        # Bars (external aggregation from klines)
        bar_type_ext_1m = _mk_bar_type(instrument.id, 1, BarAggregation.MINUTE, PriceType.LAST, AggregationSource.EXTERNAL)
        f_klines = await self.fut_market.query_klines(
            symbol=self.fut_symbol,
            interval=self.interval,
            limit=self.kline_limit,
            start_time=start_ms,
            end_time=end_ms,
        )
        fut_bars = _klines_to_binance_bars(f_klines, bar_type_ext_1m, ts_init_ns)
        out["bars"] = fut_bars

        # Trades (aggregated)
        f_trades = await self.fut_market.request_agg_trade_ticks(
            instrument_id=instrument.id,
            start_time=start_ms,
            end_time=end_ms,
            limit=self.trade_limit,
        )
        out["trade_ticks"] = f_trades

        # Order book snapshot -> OrderBookDelta[]
        f_depth = await self.fut_market.query_depth(symbol=self.fut_symbol, limit=min(1000, max(100, self.trade_limit)))
        f_deltas = _depth_to_deltas(f_depth, instrument.id, ts_init_ns)
        out["order_book_deltas"] = f_deltas

        # Ticker 24hr -> BinanceTicker
        f_tickers24 = await self.fut_market._endpoint_ticker_24hr._get(
            params=self.fut_market._endpoint_ticker_24hr.GetParameters(symbol=BinanceSymbol(self.fut_symbol), symbols=None, type=None)
        )
        fut_tickers: List[BinanceTicker] = []
        for t in f_tickers24:
            fut_tickers.append(_ticker24hr_to_binance_ticker(t, instrument.id, ts_init_ns))
        out["tickers_24hr"] = fut_tickers

        # bookTicker -> QuoteTick
        f_tb = await self.fut_market._endpoint_ticker_book._get(
            params=self.fut_market._endpoint_ticker_book.GetParameters(symbol=BinanceSymbol(self.fut_symbol), symbols=None)
        )
        fut_quotes: List[QuoteTick] = []
        for tb in f_tb:
            fut_quotes.append(_ticker_book_to_quote_tick(tb, instrument.id, ts_init_ns))
        out["quote_ticks"] = fut_quotes

        # Persist catalog
        if fut_bars: self.catalog.write_data(fut_bars)
        if f_trades: self.catalog.write_data(f_trades)
        if f_deltas: self.catalog.write_data(f_deltas)
        if fut_tickers: self.catalog.write_data(fut_tickers)
        if fut_quotes: self.catalog.write_data(fut_quotes)

        # AUX futures raw endpoints
        # time
        fut_time = await self.fut_market._endpoint_time.get()
        _df_to_parquet(_flatten({"serverTime": fut_time.serverTime, "retrieved_at": _iso_now()}), self.aux_dir / "fut_time.parquet")
        # exchangeInfo
        fut_ex = await self.fut_market._endpoint_futures_exchange_info.get()
        _df_to_parquet(_flatten(msgspec.structs.asdict(fut_ex)), self.aux_dir / "fut_exchange_info.parquet")
        # depth raw
        _df_to_parquet(pd.DataFrame(f_depth.bids, columns=["price","qty"]).assign(side="bid"), self.aux_dir / "fut_depth_bids.parquet")
        _df_to_parquet(pd.DataFrame(f_depth.asks, columns=["price","qty"]).assign(side="ask"), self.aux_dir / "fut_depth_asks.parquet")
        # trades recent
        f_recent = await self.fut_market._endpoint_trades.get(self.fut_market._endpoint_trades.GetParameters(symbol=BinanceSymbol(self.fut_symbol), limit=min(1000, self.trade_limit)))
        _df_to_parquet(_flatten([msgspec.structs.asdict(x) for x in f_recent]), self.aux_dir / "fut_trades.parquet")
        # aggTrades
        f_agg = await self.fut_market._endpoint_agg_trades.get(self.fut_market._endpoint_agg_trades.GetParameters(symbol=BinanceSymbol(self.fut_symbol), limit=min(1000, self.trade_limit), fromId=None, startTime=None, endTime=None))
        _df_to_parquet(_flatten([msgspec.structs.asdict(x) for x in f_agg]), self.aux_dir / "fut_agg_trades.parquet")
        # klines raw
        _df_to_parquet(_flatten([msgspec.structs.asdict(k) for k in f_klines]), self.aux_dir / "fut_klines.parquet")
        # continuousKlines
        cont = await self._raw_get(self.fut_client, "/fapi/v1/continuousKlines", {"pair": self.fut_pair, "contractType": self.cont_type, "interval": self.interval.value, "limit": str(self.kline_limit)})
        _df_to_parquet(_flatten(cont), self.aux_dir / "fut_continuous_klines.parquet")
        # indexPriceKlines
        idxk = await self._raw_get(self.fut_client, "/fapi/v1/indexPriceKlines", {"pair": self.fut_pair, "interval": self.interval.value, "limit": str(self.kline_limit)})
        _df_to_parquet(_flatten(idxk), self.aux_dir / "fut_index_price_klines.parquet")
        # markPriceKlines
        markk = await self._raw_get(self.fut_client, "/fapi/v1/markPriceKlines", {"symbol": self.fut_symbol, "interval": self.interval.value, "limit": str(self.kline_limit)})
        _df_to_parquet(_flatten(markk), self.aux_dir / "fut_mark_price_klines.parquet")
        # premiumIndex
        prem = await self._raw_get(self.fut_client, "/fapi/v1/premiumIndex", {"symbol": self.fut_symbol})
        _df_to_parquet(_flatten(prem), self.aux_dir / "fut_premium_index.parquet")
        # fundingRate history
        f_hist = await self._raw_get(self.fut_client, "/fapi/v1/fundingRate", {"symbol": self.fut_symbol, "limit": str(self.funding_limit)})
        _df_to_parquet(_flatten(f_hist), self.aux_dir / "fut_funding_rate_history.parquet")
        # fundingInfo
        f_info = await self._raw_get(self.fut_client, "/fapi/v1/fundingInfo", {"symbol": self.fut_symbol})
        _df_to_parquet(_flatten(f_info), self.aux_dir / "fut_funding_info.parquet")
        # ticker 24hr raw
        _df_to_parquet(_flatten([msgspec.structs.asdict(x) for x in f_tickers24]), self.aux_dir / "fut_ticker_24hr.parquet")
        # v2 ticker price
        v2tp = await self.fut_market._endpoint_ticker_price._get(self.fut_market._endpoint_ticker_price.GetParameters(symbol=BinanceSymbol(self.fut_symbol), symbols=None))
        _df_to_parquet(_flatten([msgspec.structs.asdict(x) for x in v2tp]), self.aux_dir / "fut_ticker_price_v2.parquet")
        # bookTicker raw
        _df_to_parquet(_flatten([msgspec.structs.asdict(x) for x in f_tb]), self.aux_dir / "fut_book_ticker.parquet")
        # openInterest
        oi = await self._raw_get(self.fut_client, "/fapi/v1/openInterest", {"symbol": self.fut_symbol})
        _df_to_parquet(_flatten(oi), self.aux_dir / "fut_open_interest.parquet")
        # openInterestHist
        oih = await self._raw_get(self.fut_client, "/futures/data/openInterestHist", {"symbol": self.fut_symbol, "period": "5m", "limit": str(self.metric_limit)})
        _df_to_parquet(_flatten(oih), self.aux_dir / "fut_open_interest_hist.parquet")
        # topLongShortPositionRatio
        tlspr = await self._raw_get(self.fut_client, "/futures/data/topLongShortPositionRatio", {"symbol": self.fut_symbol, "period": "5m", "limit": str(self.metric_limit)})
        _df_to_parquet(_flatten(tlspr), self.aux_dir / "fut_top_long_short_position_ratio.parquet")
        # globalLongShortAccountRatio
        glsar = await self._raw_get(self.fut_client, "/futures/data/globalLongShortAccountRatio", {"symbol": self.fut_symbol, "period": "5m", "limit": str(self.metric_limit)})
        _df_to_parquet(_flatten(glsar), self.aux_dir / "fut_global_long_short_account_ratio.parquet")
        # topLongShortAccountRatio
        tlsar = await self._raw_get(self.fut_client, "/futures/data/topLongShortAccountRatio", {"symbol": self.fut_symbol, "period": "5m", "limit": str(self.metric_limit)})
        _df_to_parquet(_flatten(tlsar), self.aux_dir / "fut_top_long_short_account_ratio.parquet")
        # taker long short ratio
        tlsr = await self._raw_get(self.fut_client, "/futures/data/takerlongshortRatio", {"symbol": self.fut_symbol, "period": "5m", "limit": str(self.metric_limit)})
        _df_to_parquet(_flatten(tlsr), self.aux_dir / "fut_taker_long_short_ratio.parquet")
        # assetIndex
        aidx = await self._raw_get(self.fut_client, "/fapi/v1/assetIndex", {})
        _df_to_parquet(_flatten(aidx), self.aux_dir / "fut_asset_index.parquet")
        # indexInfo
        iinfo = await self._raw_get(self.fut_client, "/fapi/v1/indexInfo", {})
        _df_to_parquet(_flatten(iinfo), self.aux_dir / "fut_index_info.parquet")
        # indexPriceConstituents
        cons = await self._raw_get(self.fut_client, "/fapi/v1/indexPriceConstituents", {"symbol": self.fut_symbol})
        _df_to_parquet(_flatten(cons), self.aux_dir / "fut_index_price_constituents.parquet")
        # quarterly contract settlement approx
        qsettle = await self._raw_get(self.fut_client, "/fapi/v1/continuousKlines", {"pair": self.fut_pair, "contractType": "CURRENT_QUARTER", "interval": "1d", "limit": "10"})
        _df_to_parquet(_flatten(qsettle), self.aux_dir / "fut_quarterly_settlement_approx.parquet")

        return out


# ------------------------------- Simple Strategy -------------------------------

class SimpleEMACrossConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    fast: int = 8
    slow: int = 21
    trade_size: Decimal = Decimal("0.01")

class SimpleEMACross(Strategy):
    def __init__(self, config: SimpleEMACrossConfig) -> None:
        super().__init__(config)
        self._fast = config.fast
        self._slow = config.slow
        self._has_position = False
        self._ema_fast = None
        self._ema_slow = None

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        px = Decimal(str(bar.close))
        alpha_f = Decimal("2") / Decimal(str(self._fast + 1))
        alpha_s = Decimal("2") / Decimal(str(self._slow + 1))
        self._ema_fast = px if self._ema_fast is None else (alpha_f * px + (Decimal("1") - alpha_f) * self._ema_fast)
        self._ema_slow = px if self._ema_slow is None else (alpha_s * px + (Decimal("1") - alpha_s) * self._ema_slow)

        if self._ema_fast is None or self._ema_slow is None:
            return

        if (not self._has_position) and self._ema_fast > self._ema_slow:
            order = self.order_factory.market(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.cache.instrument(self.config.instrument_id).make_qty(self.config.trade_size),
            )
            self.submit_order(order)
            self._has_position = True

        elif self._has_position and self._ema_fast < self._ema_slow:
            order = self.order_factory.market(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.cache.instrument(self.config.instrument_id).make_qty(self.config.trade_size),
            )
            self.submit_order(order)
            self._has_position = False


# -------------------------------------- main --------------------------------------

def main() -> None:
    output_dir = Path(os.getenv("OUTPUT_DIR", "catalog_binance"))
    aux_dir = Path(os.getenv("AUX_DIR", "aux_parquet"))
    spot_symbol = os.getenv("SPOT_SYMBOL", "BTCUSDT").strip().upper()
    futures_symbol = os.getenv("FUTURES_SYMBOL", spot_symbol).strip().upper()
    futures_pair = os.getenv("FUTURES_PAIR", futures_symbol).strip().upper()
    cont_type = os.getenv("CONTINUOUS_CONTRACT_TYPE", "PERPETUAL").strip().upper()
    bars_interval = os.getenv("BARS_INTERVAL", "1m").strip()
    lookback_minutes = int(os.getenv("LOOKBACK_MINUTES", "1440"))
    trade_limit = int(os.getenv("TRADE_LIMIT", "1000"))
    kline_limit = int(os.getenv("KLINE_LIMIT", "1000"))
    metric_limit = int(os.getenv("METRIC_LIMIT", "500"))
    funding_limit = int(os.getenv("FUNDING_LIMIT", "500"))
    is_testnet = _env_bool("TESTNET", False)
    is_us = _env_bool("US", False)
    api_key = os.getenv("API_KEY", None)
    api_secret = os.getenv("API_SECRET", None)
    proxy_url = os.getenv("PROXY_URL", None)

    inst = BinanceBacktestInstrumenter(
        output_dir=output_dir,
        aux_dir=aux_dir,
        spot_symbol=spot_symbol,
        fut_symbol=futures_symbol,
        fut_pair=futures_pair,
        cont_type=cont_type,
        bars_interval=bars_interval,
        lookback_minutes=lookback_minutes,
        trade_limit=trade_limit,
        kline_limit=kline_limit,
        metric_limit=metric_limit,
        funding_limit=funding_limit,
        is_testnet=is_testnet,
        is_us=is_us,
        api_key=api_key,
        api_secret=api_secret,
        proxy_url=proxy_url,
    )

    # Fetch instruments and market data, write Parquet catalog + AUX parquet
    loop = asyncio.get_event_loop()
    spot_inst, fut_inst = loop.run_until_complete(inst.load_instruments())
    loop.run_until_complete(inst.instrument_spot(spot_inst))
    loop.run_until_complete(inst.instrument_futures(fut_inst))

    # Minimal backtest using SPOT bars from the catalog to verify instrumentation
    # Reload bars from catalog to ensure on-disk compatibility
    bars_all: List[BinanceBar] = inst.catalog.custom_data(cls=BinanceBar)
    spot_bars = [b for b in bars_all if b.bar_type.instrument_id == spot_inst.id]
    if not spot_bars:
        raise RuntimeError("No spot bars found in catalog for backtest verification.")

    # Build and run BacktestEngine quickly
    engine = BacktestEngine(config=BacktestEngineConfig(trader_id=TraderId("BT-TEST"), logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=inst.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(100_000, Currency.from_str("USDT"))],
    )
    engine.add_instrument(spot_inst)

    # Ensure bar_type for subscription matches our bar data type
    bar_type = _mk_bar_type(spot_inst.id, 1, BarAggregation.MINUTE, PriceType.LAST, AggregationSource.EXTERNAL)
    # Add a small slice for quick run
    spot_bars_sorted = sorted(spot_bars, key=lambda x: x.ts_event)[: min(2000, len(spot_bars))]
    engine.add_data(spot_bars_sorted)

    strat_cfg = SimpleEMACrossConfig(
        instrument_id=spot_inst.id,
        bar_type=bar_type,
        fast=8,
        slow=21,
        trade_size=Decimal("0.01"),
    )
    engine.add_strategy(SimpleEMACross(config=strat_cfg))
    engine.run()

    # Emit quick reports to stdout (minimal)
    print(engine.trader.generate_account_report(inst.venue))
    print(engine.trader.generate_order_fills_report())
    print(engine.trader.generate_positions_report())

    # Dispose
    engine.reset()
    engine.dispose()


if __name__ == "__main__":
    import asyncio
    main()
