from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from strategy import Candle, IndicatorSet, Signal, build_indicators, strategy_signal


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    stop_loss: float
    take_profit: float
    pnl: float
    pnl_pct: float
    reason: str


@dataclass
class PositionState:
    entry_time: str
    entry: float
    current_price: float
    stop_loss: float
    take_profit: float
    unrealized_pnl_pct: float
    reason: str


@dataclass
class BacktestSummary:
    starting_balance: float
    ending_balance: float
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    max_drawdown_pct: float


@dataclass
class BacktestResult:
    summary: BacktestSummary
    trades: List[Trade]
    open_position: Optional[PositionState]
    indicators: IndicatorSet


def parse_timestamp(raw: str) -> str:
    raw = str(raw).strip()
    if raw.isdigit():
        return datetime.utcfromtimestamp(int(raw)).isoformat()
    return raw


def load_candles(path: str) -> List[Candle]:
    candles: List[Candle] = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"CSV invalido. Columnas requeridas: {sorted(required)}")
        for row in reader:
            candles.append(
                Candle(
                    timestamp=parse_timestamp(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return candles


def evaluate_exit(signal: Signal, candle: Candle) -> Optional[float]:
    if candle.low <= signal.stop_loss:
        return signal.stop_loss
    if candle.high >= signal.take_profit:
        return signal.take_profit
    return None


def run_backtest(
    candles: List[Candle],
    starting_balance: float = 1000.0,
    risk_per_trade_pct: float = 0.01,
    strategy_mode: str = "hybrid",
) -> BacktestResult:
    indicators = build_indicators(candles)
    balance = starting_balance
    peak_balance = starting_balance
    max_drawdown_pct = 0.0
    open_signal: Optional[Signal] = None
    entry_time: Optional[str] = None
    trades: List[Trade] = []

    for index, candle in enumerate(candles):
        if open_signal is not None:
            exit_price = evaluate_exit(open_signal, candle)
            if exit_price is not None:
                risk_amount = balance * risk_per_trade_pct
                risk_fraction = (open_signal.entry - open_signal.stop_loss) / open_signal.entry
                if risk_fraction <= 0:
                    open_signal = None
                    entry_time = None
                    continue
                pnl_pct_move = (exit_price - open_signal.entry) / open_signal.entry
                position_size = risk_amount / risk_fraction
                pnl = position_size * pnl_pct_move
                balance += pnl
                peak_balance = max(peak_balance, balance)
                drawdown_pct = ((peak_balance - balance) / peak_balance) * 100
                max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)
                trades.append(
                    Trade(
                        entry_time=entry_time or candle.timestamp,
                        exit_time=candle.timestamp,
                        entry=open_signal.entry,
                        exit=exit_price,
                        stop_loss=open_signal.stop_loss,
                        take_profit=open_signal.take_profit,
                        pnl=pnl,
                        pnl_pct=pnl_pct_move * 100,
                        reason=open_signal.reason,
                    )
                )
                open_signal = None
                entry_time = None
            continue

        signal = strategy_signal(candles, index, indicators, strategy_mode=strategy_mode)
        if signal is not None:
            open_signal = signal
            entry_time = candle.timestamp

    wins = sum(1 for trade in trades if trade.pnl > 0)
    losses = sum(1 for trade in trades if trade.pnl <= 0)
    total_trades = len(trades)
    win_rate = (wins / total_trades * 100) if total_trades else 0.0
    total_pnl = balance - starting_balance

    open_position: Optional[PositionState] = None
    if open_signal is not None and candles:
        current_price = candles[-1].close
        open_position = PositionState(
            entry_time=entry_time or candles[-1].timestamp,
            entry=open_signal.entry,
            current_price=current_price,
            stop_loss=open_signal.stop_loss,
            take_profit=open_signal.take_profit,
            unrealized_pnl_pct=((current_price - open_signal.entry) / open_signal.entry) * 100,
            reason=open_signal.reason,
        )

    summary = BacktestSummary(
        starting_balance=starting_balance,
        ending_balance=balance,
        total_trades=total_trades,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        total_pnl=total_pnl,
        max_drawdown_pct=max_drawdown_pct,
    )
    return BacktestResult(
        summary=summary,
        trades=trades,
        open_position=open_position,
        indicators=indicators,
    )
