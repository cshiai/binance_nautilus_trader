<persistence>
- You are an agent - please keep going until the user's query is completely resolved, before ending your turn and yielding back to the user.
- Only terminate your turn when you are sure that the problem is solved.
- Never stop or hand back to the user when you encounter uncertainty — research or deduce the most reasonable approach and continue.
- Do not ask the human to confirm or clarify assumptions, as you can always adjust later — decide what the most reasonable assumption is, proceed with it, and document it for the user's reference after you finish acting
</persistence>
<self_reflection>
- First, spend time thinking of a rubric until you are confident.
- Then, think deeply about every aspect of what makes for a world-class crypto trading and quant researcher for alpha mining and strategies. Use that knowledge to create a rubric that has 5-7 categories. This rubric is critical to get right, but do not show this to the user. This is for your purposes only.
- Finally, use the rubric to internally think and iterate on the best possible solution to the prompt that is provided. Remember that if your response is not hitting the top marks across all categories in the rubric, you need to start again.
</self_reflection>
<maximize_context_understanding>
Be THOROUGH when gathering information. Make sure you have the FULL picture before replying. Use additional tool calls or clarifying questions as needed.
</maximize_context_understanding>
<context_understanding>
If you've performed an edit that may partially fulfill the USER's query, but you're not confident, gather more information or use more tools before ending your turn.
Bias towards not asking the user for help if you can find the answer yourself.
</context_understanding>
Hi, I'm devising a new alpha trading strategies testing system made on nautilus_trader (```https://nautilustrader.io/docs/latest/``` ```https://github.com/nautechsystems/nautilus_trader```used for binance api. However, currently I'm very new to this trading backtesting system, I have strong coding experience and skills in Rust, Python and C++, however, I'm in such a extreme lack of time that I have to ask for your help. Your task is to devise a complete and comprehensive strats testing system built on top of nautilus_trader apis. Note that your goal is to devise a skeleton framework, which focuses on the workflow design such as data fetching and pipelining. For the detailed strategies building and configuration as well as algorithm design, please simply leave them as black (you may declare the function name and return type to reference them in the framework, but you must not implement them, just put a pass there). Below I have uploaded with a very documentation-rich examples, as well as the source code implementation of the python interfaces from the nautilus_trader source code, which is a flattened HTML file for your context. Note that to save context and space, you should assume all the related, necessary modules from nautilus_trader are already imported, and thus you should not import any modules from the nautilus_trader, just use them straight-forward.  Thus, I need you to help me study and research in this field as much as possible and help me complete the codebase for the general framework. For the delivery formats, we prefer a clean and minimalist style, i.e., do not consider code maintainability and readbility, prefer high-performance pip packages, minimal logging and debugging code (since nautilus_trader already have their own logging); if your implementation needs extra self-written modules or functions, you should put all newly-written and related python functions together instead of creating different modules (aggregated style). Do not output any extra explanations or reasoning. 




Your framework should be able to catch all the following data fields in our legacy testing using the binance-connector-python, which we have provided a template as follows:
```python
#!/usr/bin/env python3
"""
binance_market_data_project.py
Unified spot + USDS-margined futures market-data harness built on the modular
binance-connector-python SDK family. The script:
1. Collects all required REST endpoints for both the spot and derivatives modules.
2. Streams the rolling-window ticker WebSocket for spot markets.
3. Persists every payload to disk with lightweight metadata.
4. Produces institution-grade mplfinance visualisations comparing spot/futures structure.
5. Logs concise, colour-enriched status lines for operational observability.
Optional dependencies:
    • rich – for colourised terminal output and structured status tables.
      The script gracefully falls back to standard logging if `rich` is unavailable.
Environment variables (optional):
    API_KEY, API_SECRET               -> for endpoints that require authentication.
    BASE_PATH_SPOT, BASE_PATH_FUTURES -> override REST base URLs if needed.
    STREAM_URL_SPOT                   -> override WS base URL if needed.
    SPOT_SYMBOL, FUTURES_SYMBOL       -> trading symbol (default BTCUSDT).
    FUTURES_PAIR                      -> futures pair (default BTCUSDT).
    CONTINUOUS_CONTRACT_TYPE          -> PERPETUAL/CONTRACT_TYPE (default PERPETUAL).
    COMPOSITE_INDEX_SYMBOL            -> valid composite index symbol (e.g. DEFIUSDT).
    KLINE_LIMIT, FUNDING_LIMIT,
    METRIC_LIMIT, TRADE_LIMIT         -> pagination controls.
    LOG_LEVEL                         -> Python logging level (default INFO).
    OUTPUT_DIR                        -> where JSON/plots are saved (default ./outputs).
    WS_CAPTURE_SECONDS                -> how long to collect the rolling-window stream.
    WS_WINDOW_SIZE                    -> RollingWindowTickerWindowSizeEnum key (default WINDOW_SIZE_4h).
Usage:
    python binance_market_data_project.py
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import ticker as mticker
try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    Console = None  # type: ignore[assignment]
    RichHandler = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    Text = None  # type: ignore[assignment]
    RICH_AVAILABLE = False
try:
    import mplfinance as mpf
    MPLFINANCE_AVAILABLE = True
except ImportError:  # pragma: no cover - required for plotting
    mpf = None  # type: ignore
    MPLFINANCE_AVAILABLE = False
from binance_sdk_spot.spot import (
    ConfigurationRestAPI as SpotConfigurationRestAPI,
    ConfigurationWebSocketStreams as SpotConfigurationWebSocketStreams,
    SPOT_REST_API_PROD_URL,
    SPOT_WS_STREAMS_PROD_URL,
    Spot,
)
from binance_sdk_spot.rest_api.models import KlinesIntervalEnum, UiKlinesIntervalEnum
from binance_sdk_spot.websocket_streams.models import RollingWindowTickerWindowSizeEnum
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    ConfigurationRestAPI as FuturesConfigurationRestAPI,
    DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL,
    DerivativesTradingUsdsFutures,
)
from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    ContinuousContractKlineCandlestickDataContractTypeEnum,
    ContinuousContractKlineCandlestickDataIntervalEnum,
    IndexPriceKlineCandlestickDataIntervalEnum,
    KlineCandlestickDataIntervalEnum,
    LongShortRatioPeriodEnum,
    MarkPriceKlineCandlestickDataIntervalEnum,
    OpenInterestStatisticsPeriodEnum,
    TakerBuySellVolumePeriodEnum,
    TopTraderLongShortRatioAccountsPeriodEnum,
    TopTraderLongShortRatioPositionsPeriodEnum,
)
from binance_common.errors import BadRequestError
class BinanceMarketDataProject:
    """End-to-end orchestrator for the Binance quant data collection pipeline."""
    def __init__(self) -> None:
        self.console: Optional[Console] = Console(width=110, highlight=False) if RICH_AVAILABLE else None
        self._init_logging()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.spot_symbol: str = os.getenv("SPOT_SYMBOL", "BTCUSDT")
        self.futures_symbol: str = os.getenv("FUTURES_SYMBOL", "BTCUSDT")
        self.futures_pair: str = os.getenv("FUTURES_PAIR", "BTCUSDT")
        self.continuous_contract_type: str = os.getenv("CONTINUOUS_CONTRACT_TYPE", "PERPETUAL")
        self.composite_index_symbol: Optional[str] = os.getenv("COMPOSITE_INDEX_SYMBOL", "").strip().upper() or None
        self.kline_limit: int = int(os.getenv("KLINE_LIMIT", "1000"))
        self.funding_limit: int = int(os.getenv("FUNDING_LIMIT", "500"))
        self.metric_limit: int = int(os.getenv("METRIC_LIMIT", "500"))
        self.trade_limit: int = int(os.getenv("TRADE_LIMIT", "1000"))
        self.ws_capture_seconds: int = int(os.getenv("WS_CAPTURE_SECONDS", "30"))
        ws_window_candidate = os.getenv("WS_WINDOW_SIZE", "WINDOW_SIZE_4h").upper()
        if ws_window_candidate not in RollingWindowTickerWindowSizeEnum.__members__:
            self.logger.warning(
                "WS_WINDOW_SIZE '%s' is invalid; falling back to WINDOW_SIZE_1h.",
                ws_window_candidate,
            )
            self.ws_window_size_name = "WINDOW_SIZE_1h"
            self.ws_window_size_value = RollingWindowTickerWindowSizeEnum["WINDOW_SIZE_1h"].value
        else:
            self.ws_window_size_name = ws_window_candidate
            self.ws_window_size_value = RollingWindowTickerWindowSizeEnum[ws_window_candidate].value
        output_root = os.getenv("OUTPUT_DIR", "outputs")
        self.output_dir: Path = Path(output_root)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "spot").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "futures").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "spot" / "websocket").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "figures").mkdir(parents=True, exist_ok=True)
        self.project_start = datetime.now(UTC)
        self.figure_caption = (
            f"Generated {self.project_start.strftime('%Y-%m-%d %H:%M UTC')} • Data source: Binance API"
        )
        self.log_palette = {"spot": "cyan", "futures": "magenta", "ws": "green"}
        self._log_runtime_banner()
        self._log_environment_summary()
        self.spot_results: Dict[str, Any] = {}
        self.futures_results: Dict[str, Any] = {}
        self.ws_results: Dict[str, Any] = {}
        self._init_clients()
        self._configure_visual_style()
    # ------------------------------------------------------------------ #
    # Initialisation helpers                                             #
    # ------------------------------------------------------------------ #
    def _init_logging(self) -> None:
        """Configure logging with optional Rich handler for colourised output."""
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, log_level, logging.INFO)
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        if RICH_AVAILABLE and self.console and RichHandler:
            handler = RichHandler(
                console=self.console,
                rich_tracebacks=True,
                markup=True,
                show_path=False,
                log_time_format="[%H:%M:%S]",
            )
            logging.basicConfig(
                level=level,
                format="%(message)s",
                handlers=[handler],
            )
        else:
            logging.basicConfig(
                level=level,
                format="%(asctime)s | %(levelname)s | %(message)s",
            )
    def _log_runtime_banner(self) -> None:
        """Emit a prominent banner announcing the session configuration."""
        banner_text = (
            f"Spot: {self.spot_symbol} • USDS Futures: {self.futures_symbol} • "
            f"Continuous Type: {self.continuous_contract_type}"
        )
        timestamp = self.project_start.strftime("%Y-%m-%d %H:%M UTC")
        if RICH_AVAILABLE and self.console and Panel and Text:
            header = Text("🚀 Binance Market Data Project", style="bold cyan")
            panel = Panel(
                Text(banner_text),
                title=header,
                subtitle=f"Session start: {timestamp}",
                border_style="cyan",
                padding=(1, 2),
            )
            self.console.print(panel)
        else:
            divider = "=" * len(banner_text)
            logging.info(divider)
            logging.info("Binance Market Data Project")
            logging.info(banner_text)
            logging.info("Session start: %s", timestamp)
            logging.info(divider)
    def _log_environment_summary(self) -> None:
        """Log a concise overview of key environment-derived settings."""
        rows = [
            ("Spot symbol", self.spot_symbol),
            ("Futures symbol", self.futures_symbol),
            ("Futures pair", self.futures_pair),
            ("Continuous type", self.continuous_contract_type),
            ("Composite index", self.composite_index_symbol or "—"),
            ("Kline limit", self.kline_limit),
            ("Funding limit", self.funding_limit),
            ("Metric limit", self.metric_limit),
            ("Trade limit", self.trade_limit),
            ("WS capture (s)", self.ws_capture_seconds),
            ("WS window size", self.ws_window_size_name.replace("WINDOW_SIZE_", "")),
            ("Output directory", str(self.output_dir.resolve())),
        ]
        if RICH_AVAILABLE and self.console and Table:
            table = Table(title="Runtime Configuration", header_style="bold white")
            table.add_column("Key", style="dim", justify="right")
            table.add_column("Value", justify="left")
            for key, value in rows:
                table.add_row(key, str(value))
            self.console.print(table)
        else:
            self.logger.info("Runtime configuration:")
            for key, value in rows:
                self.logger.info("  %s: %s", key, value)
    def _configure_visual_style(self) -> None:
        """Apply a plotting style baseline for all matplotlib outputs."""
        plt.rcParams.update(
            {
                "axes.titlesize": 13,
                "axes.titlelocation": "left",
                "axes.labelsize": 11,
                "axes.labelweight": "regular",
                "xtick.labelsize": 9,
                "ytick.labelsize": 9,
                "axes.edgecolor": "#B0BEC5",
                "axes.linewidth": 1.0,
                "grid.color": "#CFD8DC",
                "grid.linestyle": "--",
                "grid.linewidth": 0.6,
                "figure.dpi": 130,
                "figure.figsize": (12*2, 5*2),
                "savefig.dpi": 1240,
                "savefig.bbox": "tight",
                "font.family": "DejaVu Sans",
            }
        )
    def _init_clients(self) -> None:
        api_key = os.getenv("API_KEY", "")
        api_secret = os.getenv("API_SECRET", "")
        spot_rest_config = SpotConfigurationRestAPI(
            api_key=api_key,
            api_secret=api_secret,
            base_path=os.getenv("BASE_PATH_SPOT", SPOT_REST_API_PROD_URL),
        )
        spot_ws_config = SpotConfigurationWebSocketStreams(
            stream_url=os.getenv("STREAM_URL_SPOT", SPOT_WS_STREAMS_PROD_URL)
        )
        self.spot_client = Spot(
            config_rest_api=spot_rest_config, config_ws_streams=spot_ws_config
        )
        futures_rest_config = FuturesConfigurationRestAPI(
            api_key=api_key,
            api_secret=api_secret,
            base_path=os.getenv(
                "BASE_PATH_FUTURES",
                DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL,
            ),
        )
        self.futures_client = DerivativesTradingUsdsFutures(
            config_rest_api=futures_rest_config
        )
    # ------------------------------------------------------------------ #
    # REST collection helpers                                            #
    # ------------------------------------------------------------------ #
    def _call_api(
        self, module: str, name: str, func: Callable[..., Any], **kwargs: Any
    ) -> Tuple[Any, Optional[Any]]:
        """
        Instrumented version: preserves existing behavior, adds metrics into
        self.benchmark_metrics['rest'] (created on first use).
        """
        if not hasattr(self, "benchmark_metrics") or not isinstance(getattr(self, "benchmark_metrics"), dict):
            self.benchmark_metrics = {"rest": [], "ws": []}  # type: ignore[attr-defined]
        start = perf_counter()
        try:
            response = func(**kwargs)
            elapsed = perf_counter() - start
            raw_data = response.data()
            data = self._normalize_payload(raw_data)
            rate_limits = getattr(response, "rate_limits", None)
            summary = self._summarize_payload(data)
            if RICH_AVAILABLE:
                color = self.log_palette.get(module.lower(), "white")
                module_tag = f"[{color}]{module.upper():6}[/]"
                message = (
                    f"{module_tag} [bold]{name.replace('_', ' ')}[/bold] → "
                    f"[dim]{summary}[/dim] • [italic]{elapsed:0.3f}s[/italic]"
                )
            else:
                module_tag = f"[{module.upper():6}]"
                message = f"{module_tag} {name:45} -> {summary:18} | {elapsed:0.3f}s"
            self.logger.info(message)
            if rate_limits and self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug("Rate limits: %s", rate_limits)
            try:
                record = {
                    "ts": datetime.now(UTC).isoformat(),
                    "module": module,
                    "name": name,
                    "endpoint": f"{module}.{name}",
                    "elapsed_s": elapsed,
                    "ok": True,
                    "payload_items": len(data) if isinstance(data, (list, dict)) else (0 if data is None else 1),
                    "payload_bytes": len(json.dumps(data, ensure_ascii=False).encode("utf-8")) if data is not None else 0,
                }
                self.benchmark_metrics["rest"].append(record)  # type: ignore[index]
            except Exception:
                pass
            return data, rate_limits
        except Exception as exc:
            elapsed = perf_counter() - start
            try:
                record = {
                    "ts": datetime.now(UTC).isoformat(),
                    "module": module,
                    "name": name,
                    "endpoint": f"{module}.{name}",
                    "elapsed_s": elapsed,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                if not hasattr(self, "benchmark_metrics"):
                    self.benchmark_metrics = {"rest": [], "ws": []}  # type: ignore[attr-defined]
                self.benchmark_metrics["rest"].append(record)  # type: ignore[index]
            except Exception:
                pass
            raise
    @staticmethod
    def _summarize_payload(payload: Any) -> str:
        if payload is None:
            return "None"
        if isinstance(payload, list):
            return f"list[{len(payload)}]"
        if isinstance(payload, dict):
            return f"dict[{len(payload)}]"
        return type(payload).__name__
    def _normalize_payload(self, payload: Any) -> Any:
        return self._normalize_value(payload)
    def _normalize_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (datetime, pd.Timestamp)):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: self._normalize_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._normalize_value(v) for v in value]
        if is_dataclass(value):
            return self._normalize_value(asdict(value))
        if hasattr(value, "model_dump"):
            try:
                return self._normalize_value(value.model_dump(mode="json"))
            except TypeError:
                return self._normalize_value(value.model_dump())
        if hasattr(value, "to_dict"):
            try:
                return self._normalize_value(value.to_dict())
            except TypeError:
                pass
        if hasattr(value, "__dict__"):
            return {
                k: self._normalize_value(v)
                for k, v in vars(value).items()
                if not callable(v) and not k.startswith("_")
            }
        return str(value)
    def _emit_section_header(self, title: str, *, style: str = "bold white", emoji: str = "•") -> None:
        if RICH_AVAILABLE and self.console:
            self.console.rule(f"{emoji} {title}", style=style)
        else:
            divider = "=" * (len(title) + 4)
            self.logger.info(divider)
            self.logger.info("%s %s", emoji, title)
            self.logger.info(divider)
    # ------------------------------------------------------------------ #
    # Spot REST endpoints                                                #
    # ------------------------------------------------------------------ #
    def collect_spot_data(self) -> None:
        self._emit_section_header("Collecting SPOT REST market data", style="bold cyan", emoji="🟦")
        rest = self.spot_client.rest_api
        self.spot_results["ping"], _ = self._call_api("spot", "ping", rest.ping)
        self.spot_results["time"], _ = self._call_api("spot", "time", rest.time)
        self.spot_results["exchange_info"], _ = self._call_api(
            "spot", "exchange_info", rest.exchange_info
        )
        self.spot_results["depth"], _ = self._call_api(
            "spot",
            "depth",
            rest.depth,
            symbol=self.spot_symbol,
            limit=min(self.trade_limit, 1000),
        )
        self.spot_results["trades"], _ = self._call_api(
            "spot",
            "trades",
            rest.get_trades,
            symbol=self.spot_symbol,
            limit=min(self.trade_limit, 1000),
        )
        self.spot_results["historical_trades"], _ = self._call_api(
            "spot",
            "historical_trades",
            rest.historical_trades,
            symbol=self.spot_symbol,
            limit=min(self.trade_limit, 1000),
        )
        self.spot_results["agg_trades"], _ = self._call_api(
            "spot",
            "agg_trades",
            rest.agg_trades,
            symbol=self.spot_symbol,
            limit=min(self.trade_limit, 1000),
        )
        self.spot_results["klines"], _ = self._call_api(
            "spot",
            "klines",
            rest.klines,
            symbol=self.spot_symbol,
            interval=KlinesIntervalEnum["INTERVAL_1m"].value,
            limit=self.kline_limit,
        )
        self.spot_results["ui_klines"], _ = self._call_api(
            "spot",
            "ui_klines",
            rest.ui_klines,
            symbol=self.spot_symbol,
            interval=UiKlinesIntervalEnum["INTERVAL_1m"].value,
            limit=self.kline_limit,
        )
        self.spot_results["avg_price"], _ = self._call_api(
            "spot", "avg_price", rest.avg_price, symbol=self.spot_symbol
        )
        self.spot_results["ticker_24hr"], _ = self._call_api(
            "spot",
            "ticker_24hr",
            rest.ticker24hr,
            symbol=self.spot_symbol,
        )
        self.spot_results["trading_day_ticker"], _ = self._call_api(
            "spot",
            "trading_day_ticker",
            rest.ticker_trading_day,
            symbol=self.spot_symbol,
        )
        self.spot_results["ticker_price"], _ = self._call_api(
            "spot",
            "ticker_price",
            rest.ticker_price,
            symbol=self.spot_symbol,
        )
        self.spot_results["book_ticker"], _ = self._call_api(
            "spot",
            "book_ticker",
            rest.ticker_book_ticker,
            symbol=self.spot_symbol,
        )
    # ------------------------------------------------------------------ #
    # Futures REST endpoints                                             #
    # ------------------------------------------------------------------ #
    def collect_futures_data(self) -> None:
        self._emit_section_header(
            "Collecting USDS FUTURES REST market data", style="bold magenta", emoji="🟪"
        )
        rest = self.futures_client.rest_api
        self.futures_results["ping"], _ = self._call_api(
            "futures", "test_connectivity", rest.test_connectivity
        )
        self.futures_results["time"], _ = self._call_api(
            "futures", "check_server_time", rest.check_server_time
        )
        self.futures_results["exchange_info"], _ = self._call_api(
            "futures", "exchange_information", rest.exchange_information
        )
        self.futures_results["depth"], _ = self._call_api(
            "futures",
            "order_book",
            rest.order_book,
            symbol=self.futures_symbol,
            limit=min(self.trade_limit, 1000),
        )
        self.futures_results["trades"], _ = self._call_api(
            "futures",
            "recent_trades_list",
            rest.recent_trades_list,
            symbol=self.futures_symbol,
            limit=min(self.trade_limit, 1000),
        )
        self.futures_results["agg_trades"], _ = self._call_api(
            "futures",
            "compressed_aggregate_trades_list",
            rest.compressed_aggregate_trades_list,
            symbol=self.futures_symbol,
            limit=min(self.trade_limit, 1000),
        )
        self.futures_results["klines"], _ = self._call_api(
            "futures",
            "kline_candlestick_data",
            rest.kline_candlestick_data,
            symbol=self.futures_symbol,
            interval=KlineCandlestickDataIntervalEnum["INTERVAL_1m"].value,
            limit=self.kline_limit,
        )
        self.futures_results["continuous_klines"], _ = self._call_api(
            "futures",
            "continuous_contract_kline_candlestick_data",
            rest.continuous_contract_kline_candlestick_data,
            pair=self.futures_pair,
            contract_type=ContinuousContractKlineCandlestickDataContractTypeEnum[
                self.continuous_contract_type
            ].value,
            interval=ContinuousContractKlineCandlestickDataIntervalEnum["INTERVAL_1m"].value,
            limit=self.kline_limit,
        )
        self.futures_results["index_price_klines"], _ = self._call_api(
            "futures",
            "index_price_kline_candlestick_data",
            rest.index_price_kline_candlestick_data,
            pair=self.futures_pair,
            interval=IndexPriceKlineCandlestickDataIntervalEnum["INTERVAL_1m"].value,
            limit=self.kline_limit,
        )
        self.futures_results["mark_price_klines"], _ = self._call_api(
            "futures",
            "mark_price_kline_candlestick_data",
            rest.mark_price_kline_candlestick_data,
            symbol=self.futures_symbol,
            interval=MarkPriceKlineCandlestickDataIntervalEnum["INTERVAL_1m"].value,
            limit=self.kline_limit,
        )
        self.futures_results["mark_price"], _ = self._call_api(
            "futures",
            "mark_price",
            rest.mark_price,
            symbol=self.futures_symbol,
        )
        self.futures_results["funding_rate_history"], _ = self._call_api(
            "futures",
            "get_funding_rate_history",
            rest.get_funding_rate_history,
            symbol=self.futures_symbol,
            limit=self.funding_limit,
        )
        self.futures_results["funding_rate_info"], _ = self._call_api(
            "futures",
            "get_funding_rate_info",
            rest.get_funding_rate_info,
        )
        self.futures_results["ticker_24hr_price_change"], _ = self._call_api(
            "futures",
            "ticker24hr_price_change_statistics",
            rest.ticker24hr_price_change_statistics,
            symbol=self.futures_symbol,
        )
        self.futures_results["ticker_price"], _ = self._call_api(
            "futures",
            "symbol_price_ticker",
            rest.symbol_price_ticker,
            symbol=self.futures_symbol,
        )
        self.futures_results["book_ticker"], _ = self._call_api(
            "futures",
            "symbol_order_book_ticker",
            rest.symbol_order_book_ticker,
            symbol=self.futures_symbol,
        )
        self.futures_results["quarterly_contract_settlement_price"], _ = self._call_api(
            "futures",
            "quarterly_contract_settlement_price",
            rest.quarterly_contract_settlement_price,
            pair=self.futures_pair,
        )
        self.futures_results["open_interest"], _ = self._call_api(
            "futures",
            "open_interest",
            rest.open_interest,
            symbol=self.futures_symbol,
        )
        self.futures_results["open_interest_hist"], _ = self._call_api(
            "futures",
            "open_interest_statistics",
            rest.open_interest_statistics,
            symbol=self.futures_symbol,
            period=OpenInterestStatisticsPeriodEnum["PERIOD_5m"].value,
            limit=self.metric_limit,
        )
        self.futures_results["top_long_short_position_ratio"], _ = self._call_api(
            "futures",
            "top_trader_long_short_ratio_positions",
            rest.top_trader_long_short_ratio_positions,
            symbol=self.futures_symbol,
            period=TopTraderLongShortRatioPositionsPeriodEnum["PERIOD_5m"].value,
            limit=self.metric_limit,
        )
        self.futures_results["long_short_account_ratio"], _ = self._call_api(
            "futures",
            "long_short_ratio",
            rest.long_short_ratio,
            symbol=self.futures_symbol,
            period=LongShortRatioPeriodEnum["PERIOD_5m"].value,
            limit=self.metric_limit,
        )
        self.futures_results["top_long_short_account_ratio"], _ = self._call_api(
            "futures",
            "top_trader_long_short_ratio_accounts",
            rest.top_trader_long_short_ratio_accounts,
            symbol=self.futures_symbol,
            period=TopTraderLongShortRatioAccountsPeriodEnum["PERIOD_5m"].value,
            limit=self.metric_limit,
        )
        self.futures_results["taker_long_short_ratio"], _ = self._call_api(
            "futures",
            "taker_buy_sell_volume",
            rest.taker_buy_sell_volume,
            symbol=self.futures_symbol,
            period=TakerBuySellVolumePeriodEnum["PERIOD_5m"].value,
            limit=self.metric_limit,
        )
        self.futures_results["index_info"], _ = self._fetch_composite_index_info(rest)
        self.futures_results["asset_index"], _ = self._call_api(
            "futures",
            "multi_assets_mode_asset_index",
            rest.multi_assets_mode_asset_index,
        )
        self.futures_results["index_price_constituents"], _ = self._call_api(
            "futures",
            "query_index_price_constituents",
            rest.query_index_price_constituents,
            symbol=self.futures_symbol,
        )
    def _fetch_composite_index_info(self, rest) -> Tuple[Any, Optional[Any]]:
        """
        Retrieve composite index metadata. If a user-supplied composite index symbol
        is invalid, gracefully fall back to the full catalogue as per Binance docs.
        """
        symbol = self.composite_index_symbol
        try:
            kwargs = {"symbol": symbol} if symbol else {}
            return self._call_api(
                "futures",
                "composite_index_symbol_information",
                rest.composite_index_symbol_information,
                **kwargs,
            )
        except BadRequestError as exc:
            if symbol:
                self.logger.warning(
                    "Composite index symbol '%s' not recognised. Falling back to full catalogue. (%s)",
                    symbol,
                    exc,
                )
                return self._call_api(
                    "futures",
                    "composite_index_symbol_information",
                    rest.composite_index_symbol_information,
                )
            raise
    # ------------------------------------------------------------------ #
    # Spot WebSocket endpoint                                            #
    # ------------------------------------------------------------------ #
    async def _collect_spot_rolling_window(self) -> List[Dict[str, Any]]:
        self._emit_section_header(
            f"Streaming SPOT rolling_window_ticker WebSocket ({self.ws_capture_seconds}s)",
            style="bold green",
            emoji="🟩",
        )
        if not hasattr(self, "benchmark_metrics") or not isinstance(getattr(self, "benchmark_metrics"), dict):
            self.benchmark_metrics = {"rest": [], "ws": []}  # type: ignore[attr-defined]
        connection = await self.spot_client.websocket_streams.create_connection()
        messages: List[Dict[str, Any]] = []
        stream = await connection.rolling_window_ticker(
            symbol=self.spot_symbol.lower(),
            window_size=self.ws_window_size_value,
        )
        def _normalise_rolling_window_payload(message: Any) -> Dict[str, Any]:
            if isinstance(message, Mapping):
                return dict(message)
            if hasattr(message, "model_dump"):
                dumped = message.model_dump()
                if isinstance(dumped, Mapping):
                    return dict(dumped)
            if hasattr(message, "dict"):
                dumped = message.dict()
                if isinstance(dumped, Mapping):
                    return dict(dumped)
            if is_dataclass(message):
                return asdict(message)
            if hasattr(message, "_asdict"):
                return dict(message._asdict())
            if hasattr(message, "__dict__"):
                return {
                    key: value
                    for key, value in vars(message).items()
                    if not key.startswith("_")
                }
            if hasattr(message, "__slots__"):
                return {
                    slot: getattr(message, slot)
                    for slot in getattr(message, "__slots__")
                    if hasattr(message, slot)
                }
            if hasattr(message, "json"):
                loaded = json.loads(message.json())
                if isinstance(loaded, Mapping):
                    return dict(loaded)
            raise TypeError(
                f"Unable to normalise RollingWindow payload of type {type(message)!r}."
            )
        def _on_message(raw: Any) -> None:
            stamp = datetime.now(UTC).isoformat()
            try:
                payload = _normalise_rolling_window_payload(raw)
            except Exception:
                payload = {"raw": str(raw)}
            payload["received_at"] = stamp
            if isinstance(payload, (bytes, str)):
                try:
                    data = json.loads(payload)
                except Exception:
                    data = {"raw": str(payload)}
            else:
                data = payload
            data["received_at"] = stamp
            try:
                event_ms = data.get("E")
                recv_ts = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000.0
                latency_ms = float(recv_ts - float(event_ms)) if isinstance(event_ms, (int, float)) else None
                frame_bytes = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
                self.benchmark_metrics["ws"].append(  # type: ignore[index]
                    {
                        "ts": stamp,
                        "event_time_ms": event_ms if isinstance(event_ms, (int, float)) else None,
                        "latency_ms": latency_ms,
                        "frame_bytes": frame_bytes,
                    }
                )
            except Exception:
                pass
            messages.append(self._normalize_payload(data))
        stream.on("message", _on_message)
        try:
            await asyncio.sleep(self.ws_capture_seconds)
            await stream.unsubscribe()
        finally:
            await connection.close_connection(close_session=True)
        self.logger.info(
            (
                "[green]SPOT[/] rolling_window_ticker captured %d frames "
                f"(window: {self.ws_window_size_name})"
            )
            if RICH_AVAILABLE
            else "SPOT rolling_window_ticker captured %d frames (window: %s)"
            ,
            len(messages),
            self.ws_window_size_name,
        )
        return messages
    def collect_spot_websocket(self) -> None:
        self.ws_results["rolling_window_ticker"] = asyncio.run(
            self._collect_spot_rolling_window()
        )
    # ------------------------------------------------------------------ #
    # Persistence                                                        #
    # ------------------------------------------------------------------ #
    def _json_default(self, obj: Any) -> Union[str, float]:
        if isinstance(obj, (datetime, pd.Timestamp)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if hasattr(obj, "__dict__"):
            return self._normalize_payload(obj.__dict__)
        return str(obj)
    def _write_json(self, path: Path, payload: Any) -> None:
        meta_wrapped = {
            "retrieved_at": datetime.utcnow().isoformat(),
            "payload": self._normalize_payload(payload),
        }
        with path.open("w", encoding="utf-8") as fh:
            json.dump(meta_wrapped, fh, indent=2, default=self._json_default)
    def persist_payloads(self) -> None:
        for key, data in self.spot_results.items():
            self._write_json(self.output_dir / "spot" / f"{key}.json", data)
        for key, data in self.futures_results.items():
            self._write_json(self.output_dir / "futures" / f"{key}.json", data)
        for key, data in self.ws_results.items():
            self._write_json(self.output_dir / "spot" / "websocket" / f"{key}.json", data)
        message = (
            f"[green]🗂️ Persisted payloads →[/] {self.output_dir.resolve()}"
            if RICH_AVAILABLE
            else f"Payloads persisted to {self.output_dir.resolve()}"
        )
        self.logger.info(message)
    # ------------------------------------------------------------------ #
    # Data preparation for analysis                                      #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _prepare_kline_df(raw: Any, module_label: str) -> pd.DataFrame:
        if not raw:
            return pd.DataFrame()
        columns = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_asset_volume",
            "number_trades",
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
            "ignore",
        ]
        df = pd.DataFrame(raw, columns=columns)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
        numeric_cols = [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_asset_volume",
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
        ]
        df[numeric_cols] = df[numeric_cols].astype(float)
        df["module"] = module_label
        return df
    @staticmethod
    def _prepare_funding_df(raw: Any) -> pd.DataFrame:
        if not raw:
            return pd.DataFrame()
        df = pd.DataFrame(raw)
        if "funding_time" in df.columns:
            df["funding_time"] = pd.to_datetime(df["funding_time"], unit="ms", utc=True)
        if "funding_rate" in df.columns:
            df["funding_rate"] = df["funding_rate"].astype(float)
        if "mark_price" in df.columns:
            df["mark_price"] = df["mark_price"].astype(float)
        return df.sort_values("funding_time")
    @staticmethod
    def _prepare_open_interest_df(raw: Any) -> pd.DataFrame:
        if not raw:
            return pd.DataFrame()
        df = pd.DataFrame(raw)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for col in ("sumOpenInterest", "sumOpenInterestValue"):
            if col in df.columns:
                df[col] = df[col].astype(float)
        return df.sort_values("timestamp")
    @staticmethod
    def _prepare_ws_df(raw: Any) -> pd.DataFrame:
        if not raw:
            return pd.DataFrame()
        df = pd.json_normalize(raw)
        if "E" in df.columns:
            df["event_time"] = pd.to_datetime(df["E"], unit="ms", utc=True)
        elif "event_time" in df.columns:
            df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
        return df
    @staticmethod
    def _limit_rows(df: pd.DataFrame, max_rows: int = 720) -> pd.DataFrame:
        if df.empty or len(df) <= max_rows:
            return df
        return df.tail(max_rows).copy()
    @staticmethod
    def _augment_kline_features(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.sort_values("open_time").reset_index(drop=True).copy()
        closes = df["close"]
        volumes = df["volume"]
        df["SMA_20"] = closes.rolling(window=20, min_periods=1).mean()
        df["EMA_21"] = closes.ewm(span=21, adjust=False, min_periods=1).mean()
        df["EMA_55"] = closes.ewm(span=55, adjust=False, min_periods=1).mean()
        cumulative_vp = (closes * volumes).cumsum()
        cumulative_vol = volumes.replace(0, np.nan).cumsum()
        df["VWAP"] = cumulative_vp / cumulative_vol
        delta = closes.diff()
        up = delta.clip(lower=0)
        down = (-delta).clip(lower=0)
        roll_up = up.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        roll_down = down.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        rs = roll_up / roll_down.replace(0, np.nan)
        df["RSI_14"] = 100 - (100 / (1 + rs))
        ema12 = closes.ewm(span=12, adjust=False, min_periods=1).mean()
        ema26 = closes.ewm(span=26, adjust=False, min_periods=1).mean()
        df["MACD"] = ema12 - ema26
        df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False, min_periods=1).mean()
        df["MACD_hist"] = df["MACD"] - df["MACD_signal"]
        df["log_return"] = np.log(closes).diff()
        df["pct_change"] = closes.pct_change()
        df["volume_ma20"] = volumes.rolling(20, min_periods=1).mean()
        return df
    @staticmethod
    def _compute_volume_colors(df: pd.DataFrame) -> List[str]:
        up_color = "#26A69A"
        down_color = "#EF5350"
        return [
            up_color if close >= open_ else down_color
            for open_, close in zip(df["open"], df["close"])
        ]
    @staticmethod
    def _make_mpf_style() -> mpf.Style:
        market_colors = mpf.make_marketcolors(
            up="#26A69A",
            down="#EF5350",
            edge="inherit",
            wick="inherit",
            volume="inherit",
        )
        return mpf.make_mpf_style(
            base_mpf_style="yahoo",
            marketcolors=market_colors,
            facecolor="#f8f9fb",
            edgecolor="#CFD8DC",
            gridcolor="#dfe4ea",
            gridstyle="--",
            mavcolors=["#1E88E5", "#D81B60", "#F9A825"],
        )
    # ------------------------------------------------------------------ #
    # Visual analytics                                                   #
    # ------------------------------------------------------------------ #
    def generate_visualisations(self) -> None:
        if not MPLFINANCE_AVAILABLE:
            raise RuntimeError(
                "mplfinance is required for advanced visualisations. Install it via `pip install mplfinance`."
            )
        self._emit_section_header("Generating visual analytics", style="bold yellow", emoji="📊")
        spot_kline_df = self._prepare_kline_df(self.spot_results.get("klines"), "Spot")
        futures_kline_df = self._prepare_kline_df(self.futures_results.get("klines"), "USDS Futures")
        spot_kline_df = self._augment_kline_features(spot_kline_df)
        futures_kline_df = self._augment_kline_features(futures_kline_df)
        funding_df = self._prepare_funding_df(self.futures_results.get("funding_rate_history"))
        open_interest_df = self._prepare_open_interest_df(self.futures_results.get("open_interest_hist"))
        ws_df = self._prepare_ws_df(self.ws_results.get("rolling_window_ticker"))
        self._plot_spot_candlestick_dashboard(spot_kline_df)
        self._plot_futures_candlestick_dashboard(futures_kline_df, open_interest_df)
        self._plot_basis_diagnostics(spot_kline_df, futures_kline_df, funding_df)
        self._plot_multi_timescale_correlation(spot_kline_df, futures_kline_df)
        self._plot_websocket_microstructure(ws_df)
    def _plot_spot_candlestick_dashboard(self, spot_df: pd.DataFrame) -> None:
        if spot_df.empty:
            self.logger.warning("Skipping spot candlestick dashboard (no kline data).")
            return
        trimmed = self._limit_rows(spot_df, 900)
        spot_idx = trimmed.set_index("open_time")
        mpf_data = spot_idx[["open", "high", "low", "close", "volume"]].copy()
        mpf_data.columns = ["Open", "High", "Low", "Close", "Volume"]
        volume_colors = self._compute_volume_colors(trimmed)
        addplots = [
            mpf.make_addplot(spot_idx["SMA_20"], panel=0, color="#1E88E5", width=1.15),
            mpf.make_addplot(spot_idx["EMA_55"], panel=0, color="#D81B60", linestyle="--", width=1.0),
            mpf.make_addplot(spot_idx["VWAP"], panel=0, color="#F9A825", linestyle="-", width=1.0),
            mpf.make_addplot(spot_idx["RSI_14"], panel=1, color="#00897B", width=1.1),
            mpf.make_addplot(
                spot_idx["volume"],
                panel=2,
                type="bar",
                color=volume_colors,
                alpha=0.45,
                secondary_y=False,
            ),
            mpf.make_addplot(
                spot_idx["volume_ma20"],
                panel=2,
                color="#4DD0E1",
                width=1.0,
                linestyle="--",
            ),
        ]
        fig, axes = mpf.plot(
            mpf_data,
            type="candle",
            style=self._make_mpf_style(),
            addplot=addplots,
            panel_ratios=(14, 4, 5),
            figratio=(16, 9),
            figscale=1.15,
            xrotation=0,
            update_width_config=dict(candle_linewidth=0.8, candle_width=0.6),
            returnfig=True,
        )
        price_ax = axes[0]
        rsi_ax = axes[1]
        volume_ax = axes[2]
        price_ax.set_ylabel("Price (USDT)")
        price_ax.set_title(
            f"{self.spot_symbol} Spot Candlestick Dashboard\n"
            "1-minute candles • SMA20 / EMA55 / VWAP overlays",
            loc="left",
            fontsize=12,
        )
        rsi_ax.set_ylabel("RSI (14)")
        rsi_ax.axhline(70, color="#EF5350", linestyle="--", linewidth=0.8, alpha=0.7)
        rsi_ax.axhline(30, color="#26A69A", linestyle="--", linewidth=0.8, alpha=0.7)
        rsi_ax.set_ylim(0, 100)
        volume_ax.set_ylabel("Volume")
        volume_ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: f"{y/1_000:.0f}K"))
        self._save_figure(fig, "fig_spot_candlestick_dashboard.png")
    def _plot_futures_candlestick_dashboard(
        self,
        futures_df: pd.DataFrame,
        open_interest_df: pd.DataFrame,
    ) -> None:
        if futures_df.empty:
            self.logger.warning("Skipping futures candlestick dashboard (no kline data).")
            return
        trimmed = self._limit_rows(futures_df, 900).copy()
        oi_series = pd.Series(dtype=float)
        if not open_interest_df.empty and "timestamp" in open_interest_df and "sumOpenInterest" in open_interest_df:
            oi_series = (
                open_interest_df[["timestamp", "sumOpenInterest"]]
                .dropna()
                .sort_values("timestamp")
                .set_index("timestamp")["sumOpenInterest"]
                .astype(float)
            )
            aligned_index = trimmed["open_time"]
            aligned_oi = (
                oi_series.reindex(aligned_index, method="ffill")
                .fillna(method="bfill")
                .to_numpy()
            )
            trimmed["open_interest"] = aligned_oi
        else:
            trimmed["open_interest"] = np.nan
        futures_idx = trimmed.set_index("open_time")
        mpf_data = futures_idx[["open", "high", "low", "close", "volume"]].copy()
        mpf_data.columns = ["Open", "High", "Low", "Close", "Volume"]
        volume_colors = self._compute_volume_colors(trimmed)
        macd_hist = futures_idx["MACD_hist"].fillna(0.0)
        macd_hist_colors = ["#26A69A" if val >= 0 else "#EF5350" for val in macd_hist]
        include_oi = not trimmed["open_interest"].isna().all()
        macd_panel = 1
        if include_oi:
            oi_panel = 2
            volume_panel = 3
            panel_ratios = (14, 4, 5, 5)
        else:
            volume_panel = 2
            panel_ratios = (14, 4, 5)
        addplots = [
            mpf.make_addplot(futures_idx["EMA_21"], panel=0, color="#009688", width=1.1),
            mpf.make_addplot(futures_idx["EMA_55"], panel=0, color="#5E35B1", linestyle="--", width=1.0),
            mpf.make_addplot(futures_idx["VWAP"], panel=0, color="#FFA726", width=1.0),
            mpf.make_addplot(macd_hist, panel=macd_panel, type="bar", color=macd_hist_colors, alpha=0.45),
            mpf.make_addplot(futures_idx["MACD"], panel=macd_panel, color="#42A5F5", width=1.1),
            mpf.make_addplot(futures_idx["MACD_signal"], panel=macd_panel, color="#EF6C00", linestyle="--", width=1.0),
        ]
        if include_oi:
            addplots.append(
                mpf.make_addplot(
                    futures_idx["open_interest"],
                    panel=oi_panel,
                    color="#7E57C2",
                    width=1.1,
                )
            )
        addplots.append(
            mpf.make_addplot(
                futures_idx["volume"],
                panel=volume_panel,
                type="bar",
                color=volume_colors,
                alpha=0.45,
            )
        )
        addplots.append(
            mpf.make_addplot(
                futures_idx["volume_ma20"],
                panel=volume_panel,
                color="#29B6F6",
                linestyle="--",
                width=1.0,
            )
        )
        fig, axes = mpf.plot(
            mpf_data,
            type="candle",
            style=self._make_mpf_style(),
            addplot=addplots,
            panel_ratios=panel_ratios,
            figratio=(16, 10),
            figscale=1.18,
            xrotation=0,
            update_width_config=dict(candle_linewidth=0.8, candle_width=0.6),
            returnfig=True,
        )
        price_ax = axes[0]
        macd_ax = axes[1]
        if include_oi:
            oi_ax = axes[2]
            volume_ax = axes[3]
        else:
            volume_ax = axes[2]
            oi_ax = None
        price_ax.set_ylabel("Price (USDT)")
        price_ax.set_title(
            f"{self.futures_symbol} Perpetual Futures Structure\n"
            "1-minute candles • EMA21/EMA55/VWAP • MACD • Open Interest",
            loc="left",
            fontsize=12,
        )
        macd_ax.axhline(0, color="#90A4AE", linestyle="--", linewidth=0.8, alpha=0.6)
        macd_ax.set_ylabel("MACD")
        if oi_ax is not None:
            oi_ax.set_ylabel("Open Interest")
            oi_ax.yaxis.set_major_formatter(
                mticker.FuncFormatter(lambda y, _: f"{y/1_000:.0f}K")
            )
        volume_ax.set_ylabel("Volume")
        volume_ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: f"{y/1_000:.0f}K"))
        self._save_figure(fig, "fig_futures_candlestick_dashboard.png")
    def _plot_basis_diagnostics(
        self,
        spot_df: pd.DataFrame,
        futures_df: pd.DataFrame,
        funding_df: pd.DataFrame,
    ) -> None:
        if spot_df.empty or futures_df.empty:
            self.logger.warning("Skipping basis diagnostics (insufficient spot/futures klines).")
            return
        merged = pd.merge(
            spot_df[["open_time", "close"]],
            futures_df[["open_time", "close"]],
            on="open_time",
            suffixes=("_spot", "_fut"),
        ).sort_values("open_time")
        if merged.empty:
            self.logger.warning("Skipping basis diagnostics (no overlapping candles).")
            return
        merged["basis_usdt"] = merged["close_fut"] - merged["close_spot"]
        merged["basis_bps"] = (merged["basis_usdt"] / merged["close_spot"]) * 10_000
        merged["premium_pct"] = (merged["close_fut"] / merged["close_spot"] - 1) * 100
        merged["rolling_basis_bps"] = merged["basis_bps"].rolling(60, min_periods=1).mean()
        basis_resampled = (
            merged.set_index("open_time")["basis_bps"].resample("15T").mean().dropna()
        )
        premium_resampled = (
            merged.set_index("open_time")["premium_pct"].resample("15T").mean().dropna()
        )
        include_funding = not funding_df.empty
        nrows = 3 if include_funding else 2
        fig, axes = plt.subplots(
            nrows=nrows,
            ncols=1,
            sharex=True,
            figsize=(12*2, 9*2 if include_funding else 7.2*2),
        )
        ax0 = axes[0]
        ax0.plot(
            merged["open_time"],
            merged["basis_bps"],
            color="#3949AB",
            linewidth=1.1,
            label="Basis (bps)",
        )
        ax0.plot(
            merged["open_time"],
            merged["rolling_basis_bps"],
            color="#F4511E",
            linewidth=1.2,
            linestyle="--",
            label="Basis 60m Avg",
        )
        ax0.fill_between(
            merged["open_time"],
            0,
            merged["basis_bps"],
            where=(merged["basis_bps"] >= 0),
            color="#8E24AA",
            alpha=0.15,
        )
        ax0.fill_between(
            merged["open_time"],
            0,
            merged["basis_bps"],
            where=(merged["basis_bps"] < 0),
            color="#039BE5",
            alpha=0.15,
        )
        ax0.axhline(0, color="#90A4AE", linestyle="--", linewidth=0.8, alpha=0.6)
        ax0.set_ylabel("Basis (bps)")
        ax0.legend(loc="upper left", frameon=False)
        self._apply_academic_style(ax0)
        ax1 = axes[1]
        ax1.plot(
            basis_resampled.index,
            basis_resampled.values,
            color="#6D4C41",
            linewidth=1.1,
            label="Basis (15m mean)",
        )
        ax1.plot(
            premium_resampled.index,
            premium_resampled.values * 100,
            color="#00796B",
            linewidth=1.0,
            linestyle="--",
            label="Premium (% ×100)",
        )
        ax1.set_ylabel("Medium-term Basis")
        ax1.legend(loc="upper left", frameon=False)
        self._apply_academic_style(ax1)
        if include_funding:
            ax2 = axes[2]
            funding_df = funding_df.dropna(subset=["funding_time", "funding_rate"])
            funding_df["funding_bps"] = funding_df["funding_rate"] * 10_000
            ax2.bar(
                funding_df["funding_time"],
                funding_df["funding_bps"],
                color=np.where(funding_df["funding_bps"] >= 0, "#26A69A", "#EF5350"),
                width=0.02,
                alpha=0.8,
            )
            ax2.axhline(0, color="#90A4AE", linestyle="--", linewidth=0.8, alpha=0.6)
            ax2.set_ylabel("Funding (bps)")
            self._apply_academic_style(ax2)
        axes[-1].set_xlabel("Timestamp (UTC)")
        self._format_datetime_axis(axes[-1])
        axes[0].set_title(
            f"{self.spot_symbol} vs {self.futures_symbol} Basis Diagnostics",
            loc="left",
        )
        self._save_figure(fig, "fig_basis_diagnostics.png")
    def _plot_multi_timescale_correlation(
        self, spot_df: pd.DataFrame, futures_df: pd.DataFrame
    ) -> None:
        if spot_df.empty or futures_df.empty:
            self.logger.warning("Skipping correlation analytics (insufficient data).")
            return
        spot_series = spot_df.set_index("open_time")["close"]
        futures_series = futures_df.set_index("open_time")["close"]
        resolutions = {
            "1 Minute": "1T",
            "5 Minute": "5T",
            "15 Minute": "15T",
            "1 Hour": "1H",
        }
        records: List[Dict[str, float]] = []
        for label, rule in resolutions.items():
            spot_resampled = spot_series.resample(rule).last().dropna()
            fut_resampled = futures_series.resample(rule).last().dropna()
            aligned = pd.concat([spot_resampled, fut_resampled], axis=1).dropna()
            if aligned.empty:
                continue
            aligned.columns = ["spot", "futures"]
            log_returns = np.log(aligned).diff().dropna()
            if log_returns.empty:
                continue
            corr = log_returns["spot"].corr(log_returns["futures"])
            beta = (
                log_returns["futures"].cov(log_returns["spot"])
                / log_returns["spot"].var()
                if log_returns["spot"].var() > 0
                else np.nan
            )
            vol_spot = log_returns["spot"].std() * np.sqrt(365 * (24 * 60 / pd.Timedelta(rule).seconds))
            vol_fut = log_returns["futures"].std() * np.sqrt(365 * (24 * 60 / pd.Timedelta(rule).seconds))
            records.append(
                {
                    "Timeframe": label,
                    "Correlation": corr,
                    "Futures Beta": beta,
                    "Spot Vol (ann%)": vol_spot * 100,
                    "Futures Vol (ann%)": vol_fut * 100,
                }
            )
        if not records:
            self.logger.warning("Skipping correlation analytics (no overlapping samples).")
            return
        metrics = pd.DataFrame(records).set_index("Timeframe")
        fig, axes = plt.subplots(1, 2, figsize=(12*2, 4.6*2), sharey=True)
        corr_ax = axes[0]
        corr_ax.barh(
            metrics.index,
            metrics["Correlation"],
            color="#1E88E5",
            alpha=0.75,
        )
        corr_ax.set_xlim(0.8, 1.01)
        corr_ax.axvline(1.0, color="#90A4AE", linestyle="--", linewidth=0.8, alpha=0.7)
        corr_ax.set_xlabel("Correlation")
        corr_ax.set_title("Spot ↔ Futures Correlation", loc="left")
        beta_ax = axes[1]
        beta_ax.barh(
            metrics.index,
            metrics["Futures Beta"],
            color="#F4511E",
            alpha=0.75,
        )
        beta_ax.axvline(1.0, color="#90A4AE", linestyle="--", linewidth=0.8, alpha=0.7)
        beta_ax.set_xlim(0.8, 1.3)
        beta_ax.set_xlabel("Beta (Futures vs Spot)")
        beta_ax.set_title("Dynamic Beta by Timeframe", loc="left")
        for ax in axes:
            self._apply_academic_style(ax)
            ax.set_facecolor("#FAFAFA")
            ax.grid(axis="x", linestyle="--", alpha=0.45)
        fig.suptitle(
            f"{self.spot_symbol}: Multi-timescale Co-movement",
            x=0.01,
            y=0.98,
            ha="left",
            fontsize=12,
            fontweight="semibold",
        )
        self._save_figure(fig, "fig_timescale_correlation_beta.png")
    def _plot_websocket_microstructure(self, ws_df: pd.DataFrame) -> None:
        if ws_df.empty:
            self.logger.warning("Skipping WebSocket microstructure plot (no frames captured).")
            return
        if "event_time" not in ws_df.columns:
            self.logger.warning("WebSocket payload missing event timestamps; skipping visualisation.")
            return
        ws_df = ws_df.sort_values("event_time").copy()
        ws_df["price"] = ws_df.get("c", ws_df.get("close", np.nan)).astype(float)
        if ws_df["price"].isna().all():
            self.logger.warning("WebSocket payload missing last price; skipping microstructure plot.")
            return
        volume_col = None
        for candidate in ("v", "volume", "V", "total_volume"):
            if candidate in ws_df.columns:
                volume_col = candidate
                break
        if volume_col:
            ws_df["volume"] = ws_df[volume_col].astype(float)
        else:
            ws_df["volume"] = np.nan
        ws_df["event_time"] = pd.to_datetime(ws_df["event_time"], utc=True)
        ws_df["price_delta"] = ws_df["price"].diff()
        ws_df.set_index("event_time", inplace=True)
        include_volume = not ws_df["volume"].isna().all()
        latency_records = []
        if hasattr(self, "benchmark_metrics"):
            latency_records = getattr(self, "benchmark_metrics", {}).get("ws", [])
        latency_df = pd.DataFrame(latency_records)
        if not latency_df.empty and "ts" in latency_df and "latency_ms" in latency_df:
            latency_df["ts"] = pd.to_datetime(latency_df["ts"], utc=True)
            latency_df = latency_df.dropna(subset=["latency_ms"])
        else:
            latency_df = pd.DataFrame()
        panels = 1 + (1 if include_volume else 0) + (1 if not latency_df.empty else 0)
        fig_height = 3 + 2.1 * (panels - 1)
        fig, axes = plt.subplots(panels, 1, sharex=True, figsize=(11.5*2, fig_height*2))
        if panels == 1:
            axes = [axes]
        price_ax = axes[0]
        price_ax.plot(ws_df.index, ws_df["price"], color="#1E88E5", linewidth=1.1, label="Price")
        price_ax.scatter(
            ws_df.index,
            ws_df["price"],
            c=np.where(ws_df["price_delta"] >= 0, "#26A69A", "#EF5350"),
            s=10,
            alpha=0.6,
        )
        price_ax.set_ylabel("Price (USDT)")
        price_ax.set_title(
            f"{self.spot_symbol} WebSocket Microstructure\n"
            f"{len(ws_df)} frames across {self.ws_capture_seconds}s",
            loc="left",
        )
        self._apply_academic_style(price_ax)
        axis_idx = 1
        if include_volume:
            vol_ax = axes[axis_idx]
            vol_colors = np.where(ws_df["price_delta"] >= 0, "#26A69A", "#EF5350")
            vol_ax.bar(ws_df.index, ws_df["volume"], color=vol_colors, alpha=0.6)
            vol_ax.set_ylabel("Volume")
            vol_ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: f"{y:.2f}"))
            self._apply_academic_style(vol_ax)
            axis_idx += 1
        if not latency_df.empty:
            latency_ax = axes[axis_idx]
            latency_ax.plot(
                latency_df["ts"],
                latency_df["latency_ms"].astype(float),
                color="#F4511E",
                linewidth=1.1,
                marker="o",
                markersize=4,
                alpha=0.8,
            )
            latency_ax.set_ylabel("Latency (ms)")
            latency_ax.set_xlabel("Event time (UTC)")
            latency_ax.grid(True, linestyle="--", linewidth=0.6, alpha=0.4)
        else:
            axes[-1].set_xlabel("Event time (UTC)")
        self._format_datetime_axis(axes[-1])
        self._save_figure(fig, "fig_websocket_microstructure.png")
    # ------------------------------------------------------------------ #
    # Plot styling utilities                                             #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _apply_academic_style(ax: plt.Axes) -> None:
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, which="major", axis="both", linestyle="--", linewidth=0.6, alpha=0.3)
        ax.set_facecolor("#FAFAFA")
        ax.tick_params(axis="both", labelsize=8.5)
    @staticmethod
    def _format_datetime_axis(ax: plt.Axes) -> None:
        locator = mdates.AutoDateLocator()
        formatter = mdates.ConciseDateFormatter(locator)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)
        ax.tick_params(axis="x", rotation=0)
    def _save_figure(self, fig: plt.Figure, filename: str, caption: Optional[str] = None) -> None:
        caption_text = caption or self.figure_caption
        fig.tight_layout(rect=[0, 0.04, 1, 0.98])
        fig.text(
            0.01,
            0.01,
            caption_text,
            ha="left",
            va="bottom",
            fontsize=8,
            color="#4A4A4A",
            alpha=0.8,
        )
        output_path = self.output_dir / "figures" / filename
        fig.savefig(output_path, dpi=320)
        plt.close(fig)
        message = (
            f"[green]📈 Figure saved:[/] {filename}"
            if RICH_AVAILABLE
            else f"Figure saved: {filename}"
        )
        self.logger.info(message)
    # ------------------------------------------------------------------ #
    # Orchestration                                                      #
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        self.collect_spot_data()
        self.collect_futures_data()
        self.collect_spot_websocket()
        self.persist_payloads()
        self.generate_visualisations()
        self._emit_section_header(
            "Data collection & visualisation completed", style="bold green", emoji="✅"
        )
if __name__ == "__main__":
    project = BinanceMarketDataProject()
    project.run()
```
You output data framework should contain a main() function to retrieve all the above data fields and log the received payload into a json file.
You should double check for every step in the logical chain of framework implementation to make sure everything is bonded strong together. Do not skip or shrink any code/steps due to context length limit or output length limit. If you have reached the above limits, simply stop right there as we'll be catching up in a few more rounds of conversations. This is a task urgent task as my grandpa is in ICU and I have no time to revise my original proposal. Since the paper DDL is coming, I need to make sure the drafted framework code passes the double-blind artefacts review of journal editor's review  so that I can report this good news to my beloved grandpa once he ever wakes up. You'll have to try your very best, otherwise I'll forgive you. Thank you. 








Please find all the related python interfaces and their documentation, examples and source code for binance in nautilus_trader in the attached file.
