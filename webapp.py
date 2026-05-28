from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from engine import BacktestSummary, Trade, run_backtest
from kraken_client import fetch_ohlc
from strategy import STRATEGY_MODES, strategy_signal


WEB_DIR = Path(__file__).parent / "web"
DEFAULT_PAIR = "BTC/USDT"
DEFAULT_INTERVAL = 15
DEFAULT_STRATEGY = "trend_breakout"
DEFAULT_STARTING_BALANCE = 10.0
DEFAULT_RISK_PER_TRADE_PCT = 0.10
SCAN_PAIRS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT")
SCAN_INTERVALS = (1, 5, 15, 30, 60)
SESSION_WARMUP_CANDLES = 250
LIVE_SESSIONS: dict[str, "LiveSession"] = {}
DB_PATH = Path(__file__).parent / "pulsepaper.db"


@dataclass
class LiveSession:
    session_id: str
    pair: str
    interval: int
    strategy_mode: str
    starting_balance: float
    risk_per_trade_pct: float
    start_timestamp: str
    start_market_pair: str


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS live_sessions (
                session_id TEXT PRIMARY KEY,
                pair TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL,
                strategy_mode TEXT NOT NULL,
                starting_balance REAL NOT NULL,
                risk_per_trade_pct REAL NOT NULL,
                started_at_candle TEXT NOT NULL,
                market_pair TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS live_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                total_trades INTEGER NOT NULL,
                total_pnl REAL NOT NULL,
                ending_balance REAL NOT NULL,
                open_position INTEGER NOT NULL,
                last_candle_timestamp TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def save_live_session(session: LiveSession) -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO live_sessions (
                session_id, pair, interval_minutes, strategy_mode,
                starting_balance, risk_per_trade_pct, started_at_candle, market_pair
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.session_id,
                session.pair,
                session.interval,
                session.strategy_mode,
                session.starting_balance,
                session.risk_per_trade_pct,
                session.start_timestamp,
                session.start_market_pair,
            ),
        )


def save_live_snapshot(
    session_id: str,
    total_trades: int,
    total_pnl: float,
    ending_balance: float,
    open_position: bool,
    last_candle_timestamp: str,
) -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO live_snapshots (
                session_id, total_trades, total_pnl, ending_balance, open_position, last_candle_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                total_trades,
                total_pnl,
                ending_balance,
                1 if open_position else 0,
                last_candle_timestamp,
            ),
        )


def _safe_float(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _trade_to_dict(trade: Trade) -> dict[str, Any]:
    return {
        "entry_time": trade.entry_time,
        "exit_time": trade.exit_time,
        "entry": round(trade.entry, 2),
        "exit": round(trade.exit, 2),
        "stop_loss": round(trade.stop_loss, 2),
        "take_profit": round(trade.take_profit, 2),
        "pnl": round(trade.pnl, 2),
        "pnl_pct": round(trade.pnl_pct, 4),
        "reason": trade.reason,
    }


def _summary_to_dict(summary: BacktestSummary, risk_per_trade_pct: float) -> dict[str, Any]:
    return {
        "starting_balance": round(summary.starting_balance, 2),
        "ending_balance": round(summary.ending_balance, 2),
        "total_trades": summary.total_trades,
        "wins": summary.wins,
        "losses": summary.losses,
        "win_rate": round(summary.win_rate, 2),
        "total_pnl": round(summary.total_pnl, 2),
        "max_drawdown_pct": round(summary.max_drawdown_pct, 2),
        "risk_per_trade_pct": round(risk_per_trade_pct * 100, 4),
        "risk_amount_estimate": round(summary.starting_balance * risk_per_trade_pct, 4),
    }


def _find_start_index(candles: list[dict[str, Any]] | list[Any], start_timestamp: str) -> int:
    for index, candle in enumerate(candles):
        if candle.timestamp == start_timestamp:
            return index
    return max(0, len(candles) - 1)


def _build_live_session_id(
    pair: str,
    interval: int,
    strategy_mode: str,
    starting_balance: float,
    risk_per_trade_pct: float,
) -> str:
    return f"{pair}|{interval}|{strategy_mode}|{starting_balance:.8f}|{risk_per_trade_pct:.8f}"


def _get_or_create_live_session(
    pair: str,
    interval: int,
    strategy_mode: str,
    starting_balance: float,
    risk_per_trade_pct: float,
    reset: bool = False,
) -> LiveSession:
    session_id = _build_live_session_id(
        pair=pair,
        interval=interval,
        strategy_mode=strategy_mode,
        starting_balance=starting_balance,
        risk_per_trade_pct=risk_per_trade_pct,
    )
    if reset or session_id not in LIVE_SESSIONS:
        result = fetch_ohlc(pair=pair, interval=interval)
        session = LiveSession(
            session_id=session_id,
            pair=pair,
            interval=interval,
            strategy_mode=strategy_mode,
            starting_balance=starting_balance,
            risk_per_trade_pct=risk_per_trade_pct,
            start_timestamp=result.candles[-1].timestamp,
            start_market_pair=result.pair,
        )
        LIVE_SESSIONS[session_id] = session
        save_live_session(session)
    return LIVE_SESSIONS[session_id]


def build_status_payload(
    pair: str = DEFAULT_PAIR,
    interval: int = DEFAULT_INTERVAL,
    strategy_mode: str = DEFAULT_STRATEGY,
    starting_balance: float = DEFAULT_STARTING_BALANCE,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
) -> dict[str, Any]:
    result = fetch_ohlc(pair=pair, interval=interval)
    candles = result.candles
    backtest = run_backtest(
        candles,
        starting_balance=starting_balance,
        risk_per_trade_pct=risk_per_trade_pct,
        strategy_mode=strategy_mode,
    )
    indicators = backtest.indicators
    signal = strategy_signal(
        candles,
        len(candles) - 1,
        indicators,
        strategy_mode=strategy_mode,
    )
    last = candles[-1]
    last_candles = candles[-40:]

    return {
        "app_name": "PulsePaper",
        "market": {
            "pair": result.pair,
            "requested_pair": pair,
            "interval": interval,
            "source": "Kraken spot public",
            "candle_count": len(candles),
        },
        "strategy": {
            "mode": strategy_mode,
            "modes": list(STRATEGY_MODES),
            "signal": None
            if signal is None
            else {
                "side": signal.side,
                "entry": round(signal.entry, 2),
                "stop_loss": round(signal.stop_loss, 2),
                "take_profit": round(signal.take_profit, 2),
                "reason": signal.reason,
            },
        },
        "summary": {
            **_summary_to_dict(backtest.summary, risk_per_trade_pct),
        },
        "position": None
        if backtest.open_position is None
        else {
            "entry_time": backtest.open_position.entry_time,
            "entry": round(backtest.open_position.entry, 2),
            "current_price": round(backtest.open_position.current_price, 2),
            "stop_loss": round(backtest.open_position.stop_loss, 2),
            "take_profit": round(backtest.open_position.take_profit, 2),
            "unrealized_pnl_pct": round(backtest.open_position.unrealized_pnl_pct, 4),
            "reason": backtest.open_position.reason,
        },
        "last_candle": {
            "timestamp": last.timestamp,
            "open": round(last.open, 2),
            "high": round(last.high, 2),
            "low": round(last.low, 2),
            "close": round(last.close, 2),
            "volume": round(last.volume, 6),
        },
        "indicators": {
            "ema20": _safe_float(indicators.ema_fast[-1]),
            "ema50": _safe_float(indicators.ema_mid[-1]),
            "ema200": _safe_float(indicators.ema_slow[-1]),
            "rsi14": _safe_float(indicators.rsi_values[-1]),
            "atr14": _safe_float(indicators.atr_values[-1]),
            "breakout20": _safe_float(indicators.breakout_high[-1]),
            "volume_avg20": _safe_float(indicators.volume_avg[-1]),
        },
        "candles": [
            {
                "timestamp": candle.timestamp,
                "open": round(candle.open, 2),
                "high": round(candle.high, 2),
                "low": round(candle.low, 2),
                "close": round(candle.close, 2),
                "volume": round(candle.volume, 6),
            }
            for candle in last_candles
        ],
        "all_candles": [
            {
                "timestamp": candle.timestamp,
                "open": round(candle.open, 2),
                "high": round(candle.high, 2),
                "low": round(candle.low, 2),
                "close": round(candle.close, 2),
                "volume": round(candle.volume, 6),
            }
            for candle in candles
        ],
        "recent_trades": [_trade_to_dict(trade) for trade in backtest.trades[-12:]],
    }


def build_live_payload(
    pair: str = DEFAULT_PAIR,
    interval: int = DEFAULT_INTERVAL,
    strategy_mode: str = DEFAULT_STRATEGY,
    starting_balance: float = DEFAULT_STARTING_BALANCE,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    reset: bool = False,
) -> dict[str, Any]:
    session = _get_or_create_live_session(
        pair=pair,
        interval=interval,
        strategy_mode=strategy_mode,
        starting_balance=starting_balance,
        risk_per_trade_pct=risk_per_trade_pct,
        reset=reset,
    )
    result = fetch_ohlc(pair=pair, interval=interval)
    candles = result.candles
    start_index = _find_start_index(candles, session.start_timestamp)
    slice_start = max(0, start_index - SESSION_WARMUP_CANDLES)
    live_candles = candles[slice_start:]
    live_backtest = run_backtest(
        live_candles,
        starting_balance=starting_balance,
        risk_per_trade_pct=risk_per_trade_pct,
        strategy_mode=strategy_mode,
    )
    live_trades = [trade for trade in live_backtest.trades if trade.entry_time >= session.start_timestamp]
    open_position = live_backtest.open_position
    if open_position is not None and open_position.entry_time < session.start_timestamp:
        open_position = None

    wins = sum(1 for trade in live_trades if trade.pnl > 0)
    losses = sum(1 for trade in live_trades if trade.pnl <= 0)
    total_trades = len(live_trades)
    win_rate = (wins / total_trades * 100) if total_trades else 0.0
    ending_balance = starting_balance + sum(trade.pnl for trade in live_trades)
    max_drawdown_pct = 0.0
    peak = starting_balance
    balance = starting_balance
    for trade in live_trades:
        balance += trade.pnl
        peak = max(peak, balance)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, ((peak - balance) / peak) * 100)
    summary = BacktestSummary(
        starting_balance=starting_balance,
        ending_balance=ending_balance,
        total_trades=total_trades,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        total_pnl=ending_balance - starting_balance,
        max_drawdown_pct=max_drawdown_pct,
    )
    save_live_snapshot(
        session_id=session.session_id,
        total_trades=summary.total_trades,
        total_pnl=summary.total_pnl,
        ending_balance=summary.ending_balance,
        open_position=open_position is not None,
        last_candle_timestamp=candles[-1].timestamp,
    )
    signal = strategy_signal(
        live_candles,
        len(live_candles) - 1,
        live_backtest.indicators,
        strategy_mode=strategy_mode,
    )
    last = candles[-1]
    recent_live_candles = live_candles[-40:]
    return {
        "session": {
            "id": session.session_id,
            "started_at_candle": session.start_timestamp,
            "mode": "live_from_now",
            "market_pair": session.start_market_pair,
        },
        "market": {
            "pair": result.pair,
            "requested_pair": pair,
            "interval": interval,
            "source": "Kraken spot public",
            "candle_count": len(live_candles),
        },
        "summary": _summary_to_dict(summary, risk_per_trade_pct),
        "position": None
        if open_position is None
        else {
            "entry_time": open_position.entry_time,
            "entry": round(open_position.entry, 2),
            "current_price": round(open_position.current_price, 2),
            "stop_loss": round(open_position.stop_loss, 2),
            "take_profit": round(open_position.take_profit, 2),
            "unrealized_pnl_pct": round(open_position.unrealized_pnl_pct, 4),
            "reason": open_position.reason,
        },
        "last_candle": {
            "timestamp": last.timestamp,
            "open": round(last.open, 2),
            "high": round(last.high, 2),
            "low": round(last.low, 2),
            "close": round(last.close, 2),
            "volume": round(last.volume, 6),
        },
        "indicators": {
            "ema20": _safe_float(live_backtest.indicators.ema_fast[-1]),
            "ema50": _safe_float(live_backtest.indicators.ema_mid[-1]),
            "ema200": _safe_float(live_backtest.indicators.ema_slow[-1]),
            "rsi14": _safe_float(live_backtest.indicators.rsi_values[-1]),
            "atr14": _safe_float(live_backtest.indicators.atr_values[-1]),
            "breakout20": _safe_float(live_backtest.indicators.breakout_high[-1]),
            "volume_avg20": _safe_float(live_backtest.indicators.volume_avg[-1]),
        },
        "candles": [
            {
                "timestamp": candle.timestamp,
                "open": round(candle.open, 2),
                "high": round(candle.high, 2),
                "low": round(candle.low, 2),
                "close": round(candle.close, 2),
                "volume": round(candle.volume, 6),
            }
            for candle in recent_live_candles
        ],
        "all_candles": [
            {
                "timestamp": candle.timestamp,
                "open": round(candle.open, 2),
                "high": round(candle.high, 2),
                "low": round(candle.low, 2),
                "close": round(candle.close, 2),
                "volume": round(candle.volume, 6),
            }
            for candle in live_candles
        ],
        "recent_trades": [_trade_to_dict(trade) for trade in live_trades[-12:]],
        "signal": None
        if signal is None
        else {
            "side": signal.side,
            "entry": round(signal.entry, 2),
            "stop_loss": round(signal.stop_loss, 2),
            "take_profit": round(signal.take_profit, 2),
            "reason": signal.reason,
        },
    }


def build_scan_payload(
    strategy_mode: str = DEFAULT_STRATEGY,
    starting_balance: float = DEFAULT_STARTING_BALANCE,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for pair in SCAN_PAIRS:
        for interval in SCAN_INTERVALS:
            result = fetch_ohlc(pair=pair, interval=interval)
            backtest = run_backtest(
                result.candles,
                starting_balance=starting_balance,
                risk_per_trade_pct=risk_per_trade_pct,
                strategy_mode=strategy_mode,
            )
            signal = strategy_signal(
                result.candles,
                len(result.candles) - 1,
                backtest.indicators,
                strategy_mode=strategy_mode,
            )
            rows.append(
                {
                    "pair": pair,
                    "interval": interval,
                    "market_pair": result.pair,
                    "last_close": round(result.candles[-1].close, 2),
                    "total_pnl": round(backtest.summary.total_pnl, 2),
                    "win_rate": round(backtest.summary.win_rate, 2),
                    "trades": backtest.summary.total_trades,
                    "max_drawdown_pct": round(backtest.summary.max_drawdown_pct, 2),
                    "signal": None if signal is None else signal.side,
                }
            )
    return {
        "strategy_mode": strategy_mode,
        "mode": "historical_compare",
        "pairs": list(SCAN_PAIRS),
        "intervals": list(SCAN_INTERVALS),
        "rows": rows,
    }


def build_live_scan_payload(
    strategy_mode: str = DEFAULT_STRATEGY,
    starting_balance: float = DEFAULT_STARTING_BALANCE,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    reset: bool = False,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for pair in SCAN_PAIRS:
        for interval in SCAN_INTERVALS:
            payload = build_live_payload(
                pair=pair,
                interval=interval,
                strategy_mode=strategy_mode,
                starting_balance=starting_balance,
                risk_per_trade_pct=risk_per_trade_pct,
                reset=reset,
            )
            rows.append(
                {
                    "pair": pair,
                    "interval": interval,
                    "market_pair": payload["market"]["pair"],
                    "last_close": payload["last_candle"]["close"],
                    "total_pnl": payload["summary"]["total_pnl"],
                    "win_rate": payload["summary"]["win_rate"],
                    "trades": payload["summary"]["total_trades"],
                    "max_drawdown_pct": payload["summary"]["max_drawdown_pct"],
                    "signal": None if payload["signal"] is None else payload["signal"]["side"],
                    "started_at_candle": payload["session"]["started_at_candle"],
                }
            )
    return {
        "strategy_mode": strategy_mode,
        "mode": "live_compare",
        "pairs": list(SCAN_PAIRS),
        "intervals": list(SCAN_INTERVALS),
        "rows": rows,
    }


class PulsePaperHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            params = parse_qs(parsed.query)
            pair = params.get("pair", [DEFAULT_PAIR])[0]
            interval = int(params.get("interval", [str(DEFAULT_INTERVAL)])[0])
            strategy_mode = params.get("strategy", [DEFAULT_STRATEGY])[0]
            starting_balance = float(params.get("starting_balance", ["1000"])[0])
            risk_per_trade_pct = float(params.get("risk_per_trade_pct", ["0.01"])[0])
            try:
                payload = build_status_payload(
                    pair=pair,
                    interval=interval,
                    strategy_mode=strategy_mode,
                    starting_balance=starting_balance,
                    risk_per_trade_pct=risk_per_trade_pct,
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
                return
            self._send_json(payload)
            return
        if parsed.path == "/api/live-status":
            params = parse_qs(parsed.query)
            pair = params.get("pair", [DEFAULT_PAIR])[0]
            interval = int(params.get("interval", [str(DEFAULT_INTERVAL)])[0])
            strategy_mode = params.get("strategy", [DEFAULT_STRATEGY])[0]
            starting_balance = float(params.get("starting_balance", [str(DEFAULT_STARTING_BALANCE)])[0])
            risk_per_trade_pct = float(params.get("risk_per_trade_pct", [str(DEFAULT_RISK_PER_TRADE_PCT)])[0])
            reset = params.get("reset", ["0"])[0] == "1"
            try:
                payload = build_live_payload(
                    pair=pair,
                    interval=interval,
                    strategy_mode=strategy_mode,
                    starting_balance=starting_balance,
                    risk_per_trade_pct=risk_per_trade_pct,
                    reset=reset,
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
                return
            self._send_json(payload)
            return
        if parsed.path == "/api/scan":
            params = parse_qs(parsed.query)
            strategy_mode = params.get("strategy", [DEFAULT_STRATEGY])[0]
            starting_balance = float(params.get("starting_balance", [str(DEFAULT_STARTING_BALANCE)])[0])
            risk_per_trade_pct = float(params.get("risk_per_trade_pct", [str(DEFAULT_RISK_PER_TRADE_PCT)])[0])
            try:
                payload = build_scan_payload(
                    strategy_mode=strategy_mode,
                    starting_balance=starting_balance,
                    risk_per_trade_pct=risk_per_trade_pct,
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
                return
            self._send_json(payload)
            return
        if parsed.path == "/api/live-scan":
            params = parse_qs(parsed.query)
            strategy_mode = params.get("strategy", [DEFAULT_STRATEGY])[0]
            starting_balance = float(params.get("starting_balance", [str(DEFAULT_STARTING_BALANCE)])[0])
            risk_per_trade_pct = float(params.get("risk_per_trade_pct", [str(DEFAULT_RISK_PER_TRADE_PCT)])[0])
            reset = params.get("reset", ["0"])[0] == "1"
            try:
                payload = build_live_scan_payload(
                    strategy_mode=strategy_mode,
                    starting_balance=starting_balance,
                    risk_per_trade_pct=risk_per_trade_pct,
                    reset=reset,
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
                return
            self._send_json(payload)
            return

        if parsed.path in ("/", "/index.html"):
            self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self._send_file(WEB_DIR / "styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._send_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve_web(host: str = "127.0.0.1", port: int = 8787) -> None:
    server = ThreadingHTTPServer((host, port), PulsePaperHandler)
    print(f"PulsePaper running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPulsePaper detenido.")
    finally:
        server.server_close()
