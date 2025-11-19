#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nautilus_binance_instrumentation.py

Complete, single-file framework for:
1. Fetching historical market data (Spot & Futures) using NautilusTrader's adapter components.
2. Instrumenting data into NautilusTrader compatible objects (Bars, Ticks, Book Deltas).
3. Persisting core data to a Nautilus ParquetDataCatalog.
4. Persisting auxiliary data (OI, Funding, etc.) to standard Parquet files.
5. Running a minimal BacktestEngine verification using the instrumented data.

Usage:
    export BINANCE_API_KEY="your_key"
    export BINANCE_API_SECRET="your_secret"
    python nautilus_binance_instrumentation.py
"""

import asyncio
import shutil
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, List, Dict, Optional
import pandas as pd
import msgspec

# NautilusTrader Imports
from nautilus_trader.adapters.binance.config import BinanceDataClientConfig
from nautilus_trader.adapters.binance.factories import get_cached_binance_http_client
from nautilus_trader.adapters.binance.http.client import BinanceHttpClient
from nautilus_trader.adapters.binance.spot.http.market import BinanceSpotMarketHttpAPI
from nautilus_trader.adapters.binance.futures.http.market import BinanceFuturesMarketHttpAPI
from nautilus_trader.adapters.binance.spot.providers import BinanceSpotInstrumentProvider
from nautilus_trader.adapters.binance.futures.providers import BinanceFuturesInstrumentProvider
from nautilus_trader.adapters.binance.common.enums import (
    BinanceAccountType, 
    BinanceKeyType, 
    BinanceKlineInterval,
    BinanceOrderSide,
    BinanceOrderType,
    BinanceTimeInForce
)
from nautilus_trader.adapters.binance.common.types import (
    BinanceBar, 
    BinanceTicker, 
    BinanceFuturesMarkPriceUpdate
)
from nautilus_trader.adapters.binance.common.symbol import BinanceSymbol
from nautilus_trader.adapters.binance.spot.enums import BinanceSpotEnumParser
from nautilus_trader.adapters.binance.futures.enums import BinanceFuturesEnumParser
from nautilus_trader.adapters.binance.spot.schemas.market import BinanceSpotExchangeInfo
from nautilus_trader.adapters.binance.futures.schemas.market import BinanceFuturesExchangeInfo

from nautilus_trader.model.data import (
    Bar, BarType, BarSpecification, QuoteTick, TradeTick, 
    OrderBookDelta, OrderBookDeltas, BookAction
)
from nautilus_trader.model.enums import (
    BarAggregation, PriceType, AggregationSource, 
    OrderSide, AggressorSide, BookType, OmsType, AccountType, TimeInForce, TriggerType
)
from nautilus_trader.model.identifiers import (
    InstrumentId, Venue, Symbol, TraderId, StrategyId, ClientId
)
from nautilus_trader.model.objects import (
    Price, Quantity, Money, Currency
)
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.time.core import LiveClock
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from nautilus_trader.core.datetime import dt_to_unix_nanos, unix_nanos_to_dt

# -------------------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------------------
CATALOG_PATH = Path(os.getcwd()) / "catalog"
AUX_OUTPUT_PATH = Path(os.getcwd()) / "outputs_aux"
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TESTNET = os.getenv("TESTNET", "False").lower() in ("true", "1", "yes")

# Symbols to process
SPOT_SYMBOL_STR = "BTCUSDT"
FUTURES_SYMBOL_STR = "BTCUSDT" 
FUTURES_PAIR_STR = "BTCUSDT"

# Limits for fetching
LIMIT_TRADES = 500
LIMIT_KLINES = 500

# -------------------------------------------------------------------------------------
# Strategy for Verification
# -------------------------------------------------------------------------------------
class MinimalTestStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_qty: Decimal

class MinimalTestStrategy(Strategy):
    """
    A minimal strategy to verify data is flowing in the backtest engine.
    Buys on the first bar, closes on the last.
    """
    def __init__(self, config: MinimalTestStrategyConfig):
        super().__init__(config)
        self.instrument_id = config.instrument_id
        self.bar_type = config.bar_type
        self.trade_qty = config.trade_qty
        self.invested = False

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        # Simple logic: Buy if not invested, Sell if invested (flip-flop for testing)
        if not self.invested:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.instrument.make_qty(self.trade_qty)
            )
            self.submit_order(order)
            self.invested = True
        else:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.instrument.make_qty(self.trade_qty)
            )
            self.submit_order(order)
            self.invested = False

# -------------------------------------------------------------------------------------
# Data Fetcher & Instrumenter
# -------------------------------------------------------------------------------------
class BinanceDataInstrumenter:
    def __init__(self, clock: LiveClock, catalog: ParquetDataCatalog):
        self.clock = clock
        self.catalog = catalog
        
        # Clients
        self.spot_client = get_cached_binance_http_client(
            clock=clock,
            account_type=BinanceAccountType.SPOT,
            api_key=API_KEY,
            api_secret=API_SECRET,
            is_testnet=TESTNET
        )
        self.fut_client = get_cached_binance_http_client(
            clock=clock,
            account_type=BinanceAccountType.USDT_FUTURES,
            api_key=API_KEY,
            api_secret=API_SECRET,
            is_testnet=TESTNET
        )
        
        # APIs
        self.spot_market = BinanceSpotMarketHttpAPI(self.spot_client, account_type=BinanceAccountType.SPOT)
        self.fut_market = BinanceFuturesMarketHttpAPI(self.fut_client, account_type=BinanceAccountType.USDT_FUTURES)
        
        # Providers (for parsing Instruments)
        self.spot_provider = BinanceSpotInstrumentProvider(self.spot_client, clock, is_testnet=TESTNET)
        self.fut_provider = BinanceFuturesInstrumentProvider(self.fut_client, clock, account_type=BinanceAccountType.USDT_FUTURES)

        # Enum Parsers
        self.spot_parser = BinanceSpotEnumParser()
        self.fut_parser = BinanceFuturesEnumParser()

    async def prepare_instruments(self) -> List[Instrument]:
        """Load instruments from exchange and write to catalog."""
        print("[INFO] Loading Instruments...")
        
        # Load specific IDs to avoid rate limits on full load
        spot_id = InstrumentId(Symbol(SPOT_SYMBOL_STR), Venue("BINANCE"))
        fut_id = InstrumentId(Symbol(f"{FUTURES_SYMBOL_STR}-PERP"), Venue("BINANCE"))
        
        await self.spot_provider.load_ids_async([spot_id])
        await self.fut_provider.load_ids_async([fut_id])
        
        instruments = []
        if spot_inst := self.spot_provider.find(spot_id):
            instruments.append(spot_inst)
        if fut_inst := self.fut_provider.find(fut_id):
            instruments.append(fut_inst)
            
        if instruments:
            self.catalog.write_data(instruments)
            print(f"[SUCCESS] Wrote {len(instruments)} instruments to catalog.")
        else:
            print("[ERROR] No instruments found.")
            
        return instruments

    async def fetch_and_store_spot_data(self, instrument: Instrument):
        """Fetch Spot Klines, AggTrades, Depth and store in Catalog."""
        print(f"[INFO] Processing Spot Data for {instrument.id}...")
        symbol = BinanceSymbol(instrument.id.symbol.value)
        
        # 1. Klines -> BinanceBar
        # Note: Using 1m interval
        klines = await self.spot_market.query_klines(
            symbol=symbol,
            interval=BinanceKlineInterval.MINUTE_1,
            limit=LIMIT_KLINES
        )
        
        bar_type = BarType(
            instrument_id=instrument.id,
            bar_spec=BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
            aggregation_source=AggregationSource.EXTERNAL
        )
        
        bars = [k.parse_to_binance_bar(bar_type) for k in klines]
        # Filter incomplete bars (where close time > now)
        now_ns = self.clock.timestamp_ns()
        bars = [b for b in bars if b.ts_event < now_ns]
        
        if bars:
            self.catalog.write_data(bars)
            print(f"   -> Wrote {len(bars)} Spot Bars")

        # 2. AggTrades -> TradeTick
        agg_trades = await self.spot_market.query_agg_trades(
            symbol=symbol,
            limit=LIMIT_TRADES
        )
        ticks = [t.parse_to_trade_tick(instrument.id) for t in agg_trades]
        if ticks:
            self.catalog.write_data(ticks)
            print(f"   -> Wrote {len(ticks)} Spot TradeTicks")

        # 3. Depth -> OrderBookDeltas (Snapshot)
        depth = await self.spot_market.query_depth(symbol=symbol, limit=100)
        snapshot = depth.parse_to_order_book_snapshot(
            instrument_id=instrument.id,
            ts_init=self.clock.timestamp_ns()
        )
        self.catalog.write_data([snapshot])
        print(f"   -> Wrote Spot OrderBook Snapshot")

    async def fetch_and_store_futures_data(self, instrument: Instrument):
        """Fetch Futures Klines, Trades, Depth, MarkPrice and store in Catalog."""
        print(f"[INFO] Processing Futures Data for {instrument.id}...")
        # Remove -PERP suffix for raw API calls
        raw_symbol_str = instrument.id.symbol.value.replace("-PERP", "")
        symbol = BinanceSymbol(raw_symbol_str)

        # 1. Klines -> BinanceBar
        klines = await self.fut_market.query_klines(
            symbol=symbol,
            interval=BinanceKlineInterval.MINUTE_1,
            limit=LIMIT_KLINES
        )
        
        bar_type = BarType(
            instrument_id=instrument.id,
            bar_spec=BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
            aggregation_source=AggregationSource.EXTERNAL
        )
        
        bars = [k.parse_to_binance_bar(bar_type) for k in klines]
        now_ns = self.clock.timestamp_ns()
        bars = [b for b in bars if b.ts_event < now_ns]
        
        if bars:
            self.catalog.write_data(bars)
            print(f"   -> Wrote {len(bars)} Futures Bars")

        # 2. AggTrades -> TradeTick
        agg_trades = await self.fut_market.query_agg_trades(
            symbol=symbol,
            limit=LIMIT_TRADES
        )
        ticks = [t.parse_to_trade_tick(instrument.id, ts_init=now_ns) for t in agg_trades]
        if ticks:
            self.catalog.write_data(ticks)
            print(f"   -> Wrote {len(ticks)} Futures TradeTicks")

        # 3. Depth -> OrderBookDeltas
        depth = await self.fut_market.query_depth(symbol=symbol, limit=100)
        snapshot = depth.parse_to_order_book_snapshot(
            instrument_id=instrument.id,
            ts_init=now_ns
        )
        self.catalog.write_data([snapshot])
        print(f"   -> Wrote Futures OrderBook Snapshot")

        # 4. Premium Index -> BinanceFuturesMarkPriceUpdate
        # We need to fetch this as a raw dict first or use the typed endpoint if available
        # The adapter has `query_futures_exchange_info` but premiumIndex is usually separate.
        # We'll use the raw client to fetch premium index to demonstrate custom data instrumentation.
        try:
            raw_pi = await self.fut_client.send_request(
                method="GET", 
                url_path="/fapi/v1/premiumIndex", 
                payload={"symbol": str(symbol)},
                ratelimiter_keys=None
            )
            # Parse manually since we are bypassing the specific API wrapper method for demonstration
            data = msgspec.json.decode(raw_pi)
            # Map to Nautilus Object
            mp_update = BinanceFuturesMarkPriceUpdate(
                instrument_id=instrument.id,
                mark=Price.from_str(data["markPrice"]),
                index=Price.from_str(data["indexPrice"]),
                estimated_settle=Price.from_str(data["estimatedSettlePrice"]),
                funding_rate=Decimal(data["lastFundingRate"]),
                next_funding_ns=int(data["nextFundingTime"]) * 1_000_000,
                ts_event=int(data["time"]) * 1_000_000,
                ts_init=now_ns
            )
            # Write to catalog (CustomData)
            # Note: ParquetDataCatalog supports CustomData if registered. 
            # BinanceFuturesMarkPriceUpdate is registered in the adapter __init__.
            self.catalog.write_data([mp_update])
            print(f"   -> Wrote Futures MarkPriceUpdate")
        except Exception as e:
            print(f"   [WARN] Failed to fetch/write MarkPrice: {e}")

    async def fetch_auxiliary_data(self, symbol_str: str):
        """Fetch auxiliary data (OI, Ratios) and save as standard Parquet (Pandas)."""
        print(f"[INFO] Fetching Auxiliary Data for {symbol_str}...")
        
        # Open Interest
        try:
            raw_oi = await self.fut_client.send_request(
                method="GET",
                url_path="/fapi/v1/openInterest",
                payload={"symbol": symbol_str},
                ratelimiter_keys=None
            )
            oi_data = msgspec.json.decode(raw_oi)
            df_oi = pd.DataFrame([oi_data])
            df_oi.to_parquet(AUX_OUTPUT_PATH / f"{symbol_str}_open_interest.parquet")
            print(f"   -> Saved Open Interest Parquet")
        except Exception as e:
            print(f"   [WARN] Failed OI fetch: {e}")

        # Long/Short Ratio
        try:
            raw_ls = await self.fut_client.send_request(
                method="GET",
                url_path="/futures/data/globalLongShortAccountRatio",
                payload={"symbol": symbol_str, "period": "5m", "limit": "10"},
                ratelimiter_keys=None
            )
            ls_data = msgspec.json.decode(raw_ls)
            df_ls = pd.DataFrame(ls_data)
            df_ls.to_parquet(AUX_OUTPUT_PATH / f"{symbol_str}_ls_ratio.parquet")
            print(f"   -> Saved Long/Short Ratio Parquet")
        except Exception as e:
            print(f"   [WARN] Failed LS Ratio fetch: {e}")

# -------------------------------------------------------------------------------------
# Main Workflow
# -------------------------------------------------------------------------------------
async def main():
    # 1. Setup Directories
    if CATALOG_PATH.exists():
        shutil.rmtree(CATALOG_PATH)
    CATALOG_PATH.mkdir(parents=True)
    
    if AUX_OUTPUT_PATH.exists():
        shutil.rmtree(AUX_OUTPUT_PATH)
    AUX_OUTPUT_PATH.mkdir(parents=True)

    # 2. Initialize Catalog & Fetcher
    catalog = ParquetDataCatalog(str(CATALOG_PATH))
    clock = LiveClock()
    instrumenter = BinanceDataInstrumenter(clock, catalog)

    # 3. Fetch Instruments
    instruments = await instrumenter.prepare_instruments()
    
    # 4. Fetch & Instrument Data
    for inst in instruments:
        if inst.id.symbol.value == SPOT_SYMBOL_STR:
            await instrumenter.fetch_and_store_spot_data(inst)
        elif inst.id.symbol.value == f"{FUTURES_SYMBOL_STR}-PERP":
            await instrumenter.fetch_and_store_futures_data(inst)
            await instrumenter.fetch_auxiliary_data(FUTURES_SYMBOL_STR)

    # 5. Run Verification Backtest
    print("\n[INFO] Starting Verification Backtest...")
    
    # Find the Futures Instrument for backtest
    backtest_inst = next((i for i in instruments if "PERP" in i.id.symbol.value), None)
    if not backtest_inst:
        print("[ERROR] Futures instrument not found for backtest.")
        return

    # Configure Engine
    engine_config = BacktestEngineConfig(
        trader_id=TraderId("VERIFIER"),
        logging=LoggingConfig(log_level="ERROR") # Minimal logging
    )
    engine = BacktestEngine(config=engine_config)

    # Add Venue
    engine.add_venue(
        venue=Venue("BINANCE"),
        oms_type=OmsType.NETTING, # Simple netting for test
        account_type=AccountType.MARGIN,
        base_currency=None,
        starting_balances=[Money(100_000, Currency.from_str("USDT"))]
    )

    # Add Data from Catalog
    # We load all bars for the instrument
    bars = catalog.bars(instrument_ids=[backtest_inst.id])
    if not bars:
        print("[ERROR] No bars found in catalog for backtest.")
        return
    
    engine.add_instrument(backtest_inst)
    engine.add_data(bars)

    # Configure & Add Strategy
    strat_config = MinimalTestStrategyConfig(
        instrument_id=backtest_inst.id,
        bar_type=bars[0].bar_type,
        trade_qty=Decimal("0.01")
    )
    strategy = MinimalTestStrategy(config=strat_config)
    engine.add_strategy(strategy)

    # Run
    engine.run()
    
    # Report
    print("[SUCCESS] Backtest Completed.")
    print("Positions:")
    print(engine.trader.generate_positions_report())
    print("Orders:")
    print(engine.trader.generate_order_fills_report())
    
    engine.dispose()

if __name__ == "__main__":
    # Ensure we have an event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())