from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass
class Candle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    reason: str


@dataclass
class IndicatorSet:
    ema_fast: List[Optional[float]]
    ema_mid: List[Optional[float]]
    ema_slow: List[Optional[float]]
    rsi_values: List[Optional[float]]
    volume_avg: List[Optional[float]]
    atr_values: List[Optional[float]]
    breakout_high: List[Optional[float]]
    breakout_low: List[Optional[float]]


STRATEGY_MODES = (
    "trend_breakout",
    "ema_pullback",
    "range_reclaim",
    "momentum_scalp",
    "hybrid",
)


def ema(values: Iterable[float], period: int) -> List[Optional[float]]:
    values = list(values)
    result: List[Optional[float]] = [None] * len(values)
    if not values or period <= 0 or len(values) < period:
        return result

    multiplier = 2 / (period + 1)
    current = sum(values[:period]) / period
    result[period - 1] = current
    for index in range(period, len(values)):
        current = ((values[index] - current) * multiplier) + current
        result[index] = current
    return result


def rsi(values: Iterable[float], period: int = 14) -> List[Optional[float]]:
    values = list(values)
    result: List[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return result

    gains = []
    losses = []
    for index in range(1, period + 1):
        delta = values[index] - values[index - 1]
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    result[period] = 100.0 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))

    for index in range(period + 1, len(values)):
        delta = values[index] - values[index - 1]
        avg_gain = ((avg_gain * (period - 1)) + max(delta, 0.0)) / period
        avg_loss = ((avg_loss * (period - 1)) + abs(min(delta, 0.0))) / period
        result[index] = 100.0 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))
    return result


def volume_sma(candles: List[Candle], period: int = 20) -> List[Optional[float]]:
    result: List[Optional[float]] = [None] * len(candles)
    if len(candles) < period:
        return result

    rolling_sum = sum(candle.volume for candle in candles[:period])
    result[period - 1] = rolling_sum / period
    for index in range(period, len(candles)):
        rolling_sum += candles[index].volume - candles[index - period].volume
        result[index] = rolling_sum / period
    return result


def atr(candles: List[Candle], period: int = 14) -> List[Optional[float]]:
    result: List[Optional[float]] = [None] * len(candles)
    if len(candles) <= period:
        return result

    tr_values: List[float] = [0.0]
    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        tr_values.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )

    current_atr = sum(tr_values[1 : period + 1]) / period
    result[period] = current_atr
    for index in range(period + 1, len(candles)):
        current_atr = ((current_atr * (period - 1)) + tr_values[index]) / period
        result[index] = current_atr
    return result


def rolling_breakout_high(candles: List[Candle], period: int = 20) -> List[Optional[float]]:
    result: List[Optional[float]] = [None] * len(candles)
    if len(candles) <= period:
        return result
    for index in range(period, len(candles)):
        result[index] = max(candle.high for candle in candles[index - period : index])
    return result


def rolling_breakout_low(candles: List[Candle], period: int = 20) -> List[Optional[float]]:
    result: List[Optional[float]] = [None] * len(candles)
    if len(candles) <= period:
        return result
    for index in range(period, len(candles)):
        result[index] = min(candle.low for candle in candles[index - period : index])
    return result


def build_indicators(candles: List[Candle]) -> IndicatorSet:
    closes = [candle.close for candle in candles]
    return IndicatorSet(
        ema_fast=ema(closes, 20),
        ema_mid=ema(closes, 50),
        ema_slow=ema(closes, 200),
        rsi_values=rsi(closes, 14),
        volume_avg=volume_sma(candles, 20),
        atr_values=atr(candles, 14),
        breakout_high=rolling_breakout_high(candles, 20),
        breakout_low=rolling_breakout_low(candles, 20),
    )


def trend_breakout_signal(
    candles: List[Candle], index: int, indicators: IndicatorSet
) -> Optional[Signal]:
    current = candles[index]
    fast = indicators.ema_fast[index]
    mid = indicators.ema_mid[index]
    slow = indicators.ema_slow[index]
    rsi_value = indicators.rsi_values[index]
    avg_volume = indicators.volume_avg[index]
    atr_value = indicators.atr_values[index]
    breakout_level = indicators.breakout_high[index]

    if None in (fast, mid, slow, rsi_value, avg_volume, atr_value, breakout_level):
        return None
    assert fast is not None and mid is not None and slow is not None
    assert rsi_value is not None and avg_volume is not None and atr_value is not None
    assert breakout_level is not None

    trend_ok = current.close > fast > mid > slow
    momentum_ok = 52 <= rsi_value <= 68
    breakout_ok = current.close > breakout_level
    volume_ok = current.volume >= avg_volume * 1.05
    if not (trend_ok and momentum_ok and breakout_ok and volume_ok):
        return None

    stop_loss = current.close - (atr_value * 1.4)
    take_profit = current.close + (atr_value * 2.4)
    if stop_loss >= current.close:
        return None
    return Signal(
        side="long",
        entry=current.close,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reason="trend_breakout_atr",
    )


def ema_pullback_signal(
    candles: List[Candle], index: int, indicators: IndicatorSet
) -> Optional[Signal]:
    if index < 2:
        return None
    current = candles[index]
    previous = candles[index - 1]
    prior = candles[index - 2]
    fast = indicators.ema_fast[index]
    mid = indicators.ema_mid[index]
    slow = indicators.ema_slow[index]
    rsi_value = indicators.rsi_values[index]
    avg_volume = indicators.volume_avg[index]
    atr_value = indicators.atr_values[index]

    if None in (fast, mid, slow, rsi_value, avg_volume, atr_value):
        return None
    assert fast is not None and mid is not None and slow is not None
    assert rsi_value is not None and avg_volume is not None and atr_value is not None

    trend_ok = fast > mid > slow and current.close > slow
    pullback_ok = prior.low > fast and previous.low <= fast and current.close > previous.high
    rsi_ok = 46 <= rsi_value <= 62
    volume_ok = current.volume >= avg_volume * 0.95
    if not (trend_ok and pullback_ok and rsi_ok and volume_ok):
        return None

    stop_loss = min(previous.low, current.low) - (atr_value * 0.7)
    take_profit = current.close + ((current.close - stop_loss) * 1.8)
    if stop_loss >= current.close:
        return None
    return Signal(
        side="long",
        entry=current.close,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reason="ema_pullback_reclaim",
    )


def range_reclaim_signal(
    candles: List[Candle], index: int, indicators: IndicatorSet
) -> Optional[Signal]:
    if index < 3:
        return None
    current = candles[index]
    previous = candles[index - 1]
    fast = indicators.ema_fast[index]
    slow = indicators.ema_slow[index]
    rsi_value = indicators.rsi_values[index]
    atr_value = indicators.atr_values[index]
    breakout_low = indicators.breakout_low[index]
    avg_volume = indicators.volume_avg[index]

    if None in (fast, slow, rsi_value, atr_value, breakout_low, avg_volume):
        return None
    assert fast is not None and slow is not None
    assert rsi_value is not None and atr_value is not None
    assert breakout_low is not None and avg_volume is not None

    down_move_exhausted = previous.low <= breakout_low and current.close > previous.high
    trend_not_bearish = current.close >= slow * 0.985
    reclaim_ok = current.close > fast * 0.995
    momentum_ok = 43 <= rsi_value <= 58
    volume_ok = current.volume >= avg_volume * 0.85
    if not (down_move_exhausted and trend_not_bearish and reclaim_ok and momentum_ok and volume_ok):
        return None

    stop_loss = min(previous.low, current.low) - (atr_value * 0.55)
    take_profit = current.close + ((current.close - stop_loss) * 1.5)
    if stop_loss >= current.close:
        return None
    return Signal(
        side="long",
        entry=current.close,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reason="range_reclaim_bounce",
    )


def momentum_scalp_signal(
    candles: List[Candle], index: int, indicators: IndicatorSet
) -> Optional[Signal]:
    if index < 1:
        return None
    current = candles[index]
    previous = candles[index - 1]
    fast = indicators.ema_fast[index]
    mid = indicators.ema_mid[index]
    rsi_value = indicators.rsi_values[index]
    atr_value = indicators.atr_values[index]
    avg_volume = indicators.volume_avg[index]

    if None in (fast, mid, rsi_value, atr_value, avg_volume):
        return None
    assert fast is not None and mid is not None
    assert rsi_value is not None and atr_value is not None and avg_volume is not None

    impulse_ok = current.close > previous.high and current.close > fast >= mid
    rsi_ok = 55 <= rsi_value <= 75
    volume_ok = current.volume >= avg_volume * 1.1
    candle_ok = (current.close - current.open) > (atr_value * 0.2)
    if not (impulse_ok and rsi_ok and volume_ok and candle_ok):
        return None

    stop_loss = current.close - (atr_value * 0.9)
    take_profit = current.close + (atr_value * 1.4)
    if stop_loss >= current.close:
        return None
    return Signal(
        side="long",
        entry=current.close,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reason="momentum_scalp_push",
    )


def strategy_signal(
    candles: List[Candle],
    index: int,
    indicators: Optional[IndicatorSet] = None,
    strategy_mode: str = "hybrid",
) -> Optional[Signal]:
    if indicators is None:
        indicators = build_indicators(candles)
    if index < 200:
        return None
    if strategy_mode not in STRATEGY_MODES:
        raise ValueError(f"Modo de estrategia invalido: {strategy_mode}")

    trend_signal = trend_breakout_signal(candles, index, indicators)
    pullback_signal = ema_pullback_signal(candles, index, indicators)
    range_signal = range_reclaim_signal(candles, index, indicators)
    scalp_signal = momentum_scalp_signal(candles, index, indicators)

    if strategy_mode == "trend_breakout":
        return trend_signal
    if strategy_mode == "ema_pullback":
        return pullback_signal
    if strategy_mode == "range_reclaim":
        return range_signal
    if strategy_mode == "momentum_scalp":
        return scalp_signal
    return scalp_signal or pullback_signal or trend_signal or range_signal
