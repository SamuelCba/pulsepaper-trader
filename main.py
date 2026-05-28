from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone

from engine import load_candles, run_backtest
from kraken_client import fetch_ohlc, save_candles_csv
from sample_data import generate_sample_csv
from strategy import Candle, STRATEGY_MODES, build_indicators, strategy_signal
from webapp import serve_web


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"


def fmt_price(value: float) -> str:
    return f"{value:,.2f}"


def short_ts(timestamp: str) -> str:
    clean = timestamp.replace("T", " ")
    if "+" in clean:
        clean = clean.split("+", 1)[0]
    return clean[-8:-3]


def colorize(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{color}{text}{RESET}"


def pnl_color(value: float, enabled: bool) -> str:
    if value > 0:
        return colorize(f"{value:.2f}", GREEN, enabled)
    if value < 0:
        return colorize(f"{value:.2f}", RED, enabled)
    return colorize(f"{value:.2f}", YELLOW, enabled)


def build_close_chart(candles: list[Candle], width: int = 24) -> list[str]:
    window = candles[-width:]
    closes = [candle.close for candle in window]
    highs = [candle.high for candle in window]
    lows = [candle.low for candle in window]
    max_high = max(highs)
    min_low = min(lows)
    span = max(max_high - min_low, 1.0)
    rows: list[str] = []
    for candle in window:
        body_pos = int(((candle.close - min_low) / span) * 20)
        wick_low = int(((candle.low - min_low) / span) * 20)
        wick_high = int(((candle.high - min_low) / span) * 20)
        chars = []
        for idx in range(21):
            if idx == body_pos:
                chars.append("#")
            elif wick_low <= idx <= wick_high:
                chars.append("|")
            else:
                chars.append(".")
        rows.append("".join(chars))
    return rows


def build_volume_bar(value: float, max_value: float, width: int = 10) -> str:
    if max_value <= 0:
        return "." * width
    fill = max(1, int((value / max_value) * width))
    fill = min(fill, width)
    return "#" * fill + "." * (width - fill)


def render_recent_candles(candles: list[Candle], count: int = 8, color: bool = True) -> list[str]:
    window = candles[-count:]
    max_volume = max((candle.volume for candle in window), default=1.0)
    chart = build_close_chart(candles, width=max(24, count))
    chart_window = chart[-count:]
    rows: list[str] = []
    for candle, mini in zip(window, chart_window):
        direction = "UP" if candle.close >= candle.open else "DN"
        dir_text = colorize(direction, GREEN if direction == "UP" else RED, color)
        vol_bar = build_volume_bar(candle.volume, max_volume)
        rows.append(
            f"{short_ts(candle.timestamp)} {dir_text} "
            f"C:{fmt_price(candle.close):>10} "
            f"R:{fmt_price(candle.high - candle.low):>8} "
            f"V:{candle.volume:>6.2f} {vol_bar} "
            f"{mini}"
        )
    return rows


def render_dashboard(
    pair: str,
    interval: int,
    candles: list[Candle],
    refresh_seconds: int,
    cycle_started_at: float,
    strategy_mode: str,
    starting_balance: float,
    risk_per_trade_pct: float,
    candle_count: int,
    color: bool,
    seconds_until_refresh: int,
) -> str:
    result = run_backtest(
        candles,
        starting_balance=starting_balance,
        risk_per_trade_pct=risk_per_trade_pct,
        strategy_mode=strategy_mode,
    )
    indicators = result.indicators
    last = candles[-1]
    signal = strategy_signal(
        candles,
        len(candles) - 1,
        indicators,
        strategy_mode=strategy_mode,
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    header = "Paper Trader Terminal"
    if color:
        header = f"{BOLD}{CYAN}{header}{RESET}"
    lines = [
        "\033[2J\033[H",
        header,
        "",
        f"Mercado       {pair}",
        f"Intervalo     {interval}m",
        f"Estrategia    {strategy_mode}",
        f"Fuente        Kraken spot publico",
        f"Velas         {len(candles)} descargadas",
        f"Ultima vela   {last.timestamp}",
        f"Precio close  {fmt_price(last.close)}",
        f"Refresh       cada {refresh_seconds}s",
        f"Actualizado   {now}",
        "",
        f"{BOLD if color else ''}Estado de estrategia{RESET if color else ''}",
    ]

    ema_fast = indicators.ema_fast[-1]
    ema_mid = indicators.ema_mid[-1]
    ema_slow = indicators.ema_slow[-1]
    rsi_value = indicators.rsi_values[-1]
    atr_value = indicators.atr_values[-1]
    breakout_level = indicators.breakout_high[-1]
    volume_avg = indicators.volume_avg[-1]

    lines.extend(
        [
            f"EMA20/50/200 {fmt_price(ema_fast or 0)} / {fmt_price(ema_mid or 0)} / {fmt_price(ema_slow or 0)}",
            f"RSI14        {rsi_value:.2f}" if rsi_value is not None else "RSI14        n/a",
            f"ATR14        {atr_value:.2f}" if atr_value is not None else "ATR14        n/a",
            f"Breakout20   {fmt_price(breakout_level or 0)}",
            f"Vol actual    {last.volume:.2f}",
            f"Vol prom20    {volume_avg:.2f}" if volume_avg is not None else "Vol prom20    n/a",
        ]
    )

    if signal is None:
        lines.append(f"Senal actual  {colorize('ninguna', YELLOW, color)}")
    else:
        lines.extend(
            [
                f"Senal actual  {colorize('LONG', GREEN, color)}",
                f"Entry         {fmt_price(signal.entry)}",
                f"Stop loss     {fmt_price(signal.stop_loss)}",
                f"Take profit   {fmt_price(signal.take_profit)}",
                f"Motivo        {signal.reason}",
            ]
        )

    lines.extend(
        [
            "",
            f"{BOLD if color else ''}Simulacion{RESET if color else ''}",
            f"Balance       {summary_fmt(result.summary.ending_balance)}",
            f"PnL total     {pnl_color(result.summary.total_pnl, color)}",
            f"Trades        {result.summary.total_trades}",
            f"Win rate      {result.summary.win_rate:.2f}%",
            f"Max DD        {result.summary.max_drawdown_pct:.2f}%",
            f"Riesgo/trade  {risk_per_trade_pct * 100:.2f}%",
        ]
    )

    if result.open_position is None:
        lines.append(f"Posicion live  {colorize('ninguna abierta', YELLOW, color)}")
    else:
        pos = result.open_position
        pos_color = GREEN if pos.unrealized_pnl_pct >= 0 else RED
        lines.extend(
            [
                f"Posicion live  {colorize('LONG abierta', GREEN, color)}",
                f"Desde         {pos.entry_time}",
                f"Entry         {fmt_price(pos.entry)}",
                f"Precio act    {fmt_price(pos.current_price)}",
                f"PnL no real   {colorize(f'{pos.unrealized_pnl_pct:.2f}%', pos_color, color)}",
                f"SL / TP       {fmt_price(pos.stop_loss)} / {fmt_price(pos.take_profit)}",
            ]
        )

    lines.extend(["", f"{BOLD if color else ''}Ultimas velas{RESET if color else ''}"])
    lines.extend(render_recent_candles(candles, count=candle_count, color=color))

    if result.trades:
        lines.extend(["", f"{BOLD if color else ''}Ultimos trades simulados{RESET if color else ''}"])
        for trade in result.trades[-5:]:
            trade_color = GREEN if trade.pnl > 0 else RED if trade.pnl < 0 else YELLOW
            lines.append(
                f"{short_ts(trade.entry_time)}->{short_ts(trade.exit_time)} "
                f"entry={fmt_price(trade.entry)} "
                f"exit={fmt_price(trade.exit)} "
                f"pnl={colorize(f'{trade.pnl:.2f}', trade_color, color)} "
                f"{DIM + trade.reason + RESET if color else trade.reason}"
            )
    else:
        lines.extend(["", "Ultimos trades simulados", "Sin trades cerrados todavia"])

    lines.extend(
        [
            "",
            f"Balance inicial {starting_balance:.2f}",
            f"Siguiente refresh en {seconds_until_refresh}s",
            "Salir: Ctrl+C",
        ]
    )
    return "\n".join(lines)


def summary_fmt(value: float) -> str:
    return f"{value:.2f}"


def cmd_generate_sample(args: argparse.Namespace) -> int:
    output = args.output
    parent = os.path.dirname(output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    generate_sample_csv(output, rows=args.rows)
    print(f"Datos de ejemplo generados en: {output}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    candles = load_candles(args.csv_path)
    result = run_backtest(
        candles,
        starting_balance=args.starting_balance,
        risk_per_trade_pct=args.risk_per_trade_pct,
        strategy_mode=args.strategy,
    )
    summary = result.summary
    print("Resumen backtest")
    print(f"Estrategia:      {args.strategy}")
    print(f"Balance inicial: {summary.starting_balance:.2f}")
    print(f"Balance final:   {summary.ending_balance:.2f}")
    print(f"PnL total:       {summary.total_pnl:.2f}")
    print(f"Trades:          {summary.total_trades}")
    print(f"Ganados:         {summary.wins}")
    print(f"Perdidos:        {summary.losses}")
    print(f"Win rate:        {summary.win_rate:.2f}%")
    print(f"Max drawdown:    {summary.max_drawdown_pct:.2f}%")
    print("")
    print("Ultimos trades")
    for trade in result.trades[-5:]:
        print(
            f"{trade.entry_time} -> {trade.exit_time} | "
            f"entry={trade.entry:.2f} exit={trade.exit:.2f} pnl={trade.pnl:.2f} "
            f"reason={trade.reason}"
        )
    if result.open_position is not None:
        print("")
        print("Posicion abierta simulada")
        print(
            f"{result.open_position.entry_time} | "
            f"entry={result.open_position.entry:.2f} "
            f"actual={result.open_position.current_price:.2f} "
            f"unrealized={result.open_position.unrealized_pnl_pct:.2f}%"
        )
    return 0


def cmd_fetch_kraken(args: argparse.Namespace) -> int:
    result = fetch_ohlc(pair=args.pair, interval=args.interval)
    save_candles_csv(args.output, result.candles)
    print(
        f"Velas descargadas: {len(result.candles)} | "
        f"pair={result.pair} interval={result.interval}m"
    )
    print(f"CSV guardado en: {args.output}")
    return 0


def cmd_signal(args: argparse.Namespace) -> int:
    candles = load_candles(args.csv_path)
    indicators = build_indicators(candles)
    signal = strategy_signal(
        candles,
        len(candles) - 1,
        indicators,
        strategy_mode=args.strategy,
    )
    last = candles[-1]
    print(f"Ultima vela: {last.timestamp} close={last.close:.2f} volume={last.volume:.2f}")
    if signal is None:
        print("Senal actual: ninguna")
        return 0
    print(f"Estrategia:  {args.strategy}")
    print("Senal actual: long")
    print(f"Entry:       {signal.entry:.2f}")
    print(f"Stop loss:   {signal.stop_loss:.2f}")
    print(f"Take profit: {signal.take_profit:.2f}")
    print(f"Motivo:      {signal.reason}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    try:
        while True:
            cycle_started_at = time.time()
            result = fetch_ohlc(pair=args.pair, interval=args.interval)
            if args.output:
                save_candles_csv(args.output, result.candles)
            while True:
                elapsed = time.time() - cycle_started_at
                remaining = max(0, int(args.refresh_seconds - elapsed))
                print(
                    render_dashboard(
                        pair=result.pair,
                        interval=result.interval,
                        candles=result.candles,
                        refresh_seconds=args.refresh_seconds,
                        cycle_started_at=cycle_started_at,
                        strategy_mode=args.strategy,
                        starting_balance=args.starting_balance,
                        risk_per_trade_pct=args.risk_per_trade_pct,
                        candle_count=args.candle_count,
                        color=not args.no_color,
                        seconds_until_refresh=remaining,
                    ),
                    end="",
                    flush=True,
                )
                if elapsed >= args.refresh_seconds:
                    break
                time.sleep(args.ui_tick_seconds)
    except KeyboardInterrupt:
        print("\nWatch detenido.")
        return 0


def add_strategy_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--strategy",
        choices=STRATEGY_MODES,
        default="hybrid",
        help="Modo de estrategia",
    )


def cmd_serve_web(args: argparse.Namespace) -> int:
    serve_web(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper trader terminal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate-sample", help="Genera CSV de ejemplo")
    generate.add_argument("output", help="Ruta del CSV de salida")
    generate.add_argument("--rows", type=int, default=500, help="Cantidad de filas")
    generate.set_defaults(func=cmd_generate_sample)

    backtest = subparsers.add_parser("backtest", help="Corre backtest sobre CSV")
    backtest.add_argument("csv_path", help="Ruta del archivo CSV")
    backtest.add_argument("--starting-balance", type=float, default=1000.0)
    backtest.add_argument("--risk-per-trade-pct", type=float, default=0.01)
    add_strategy_arg(backtest)
    backtest.set_defaults(func=cmd_backtest)

    fetch = subparsers.add_parser("fetch-kraken", help="Descarga OHLC de Kraken")
    fetch.add_argument("output", help="Ruta del CSV de salida")
    fetch.add_argument("--pair", default="BTC/USDT", help="Par, ejemplo BTC/USDT")
    fetch.add_argument("--interval", type=int, default=15, help="Intervalo en minutos")
    fetch.set_defaults(func=cmd_fetch_kraken)

    signal = subparsers.add_parser("signal", help="Muestra la senal mas reciente")
    signal.add_argument("csv_path", help="Ruta del CSV")
    add_strategy_arg(signal)
    signal.set_defaults(func=cmd_signal)

    watch = subparsers.add_parser("watch", help="Panel de terminal en vivo")
    watch.add_argument("--pair", default="BTC/USDT", help="Par, ejemplo BTC/USDT")
    watch.add_argument("--interval", type=int, default=15, help="Intervalo en minutos")
    watch.add_argument("--refresh-seconds", type=int, default=60, help="Cada cuantos segundos refresca")
    watch.add_argument("--output", default="data/live_kraken.csv", help="CSV opcional donde guarda la ultima descarga")
    watch.add_argument("--starting-balance", type=float, default=1000.0)
    watch.add_argument("--risk-per-trade-pct", type=float, default=0.01)
    watch.add_argument("--candle-count", type=int, default=8, help="Cuantas velas mostrar")
    watch.add_argument("--ui-tick-seconds", type=float, default=1.0, help="Cada cuantos segundos repinta la UI")
    watch.add_argument("--no-color", action="store_true", help="Desactiva colores ANSI")
    add_strategy_arg(watch)
    watch.set_defaults(func=cmd_watch)

    web = subparsers.add_parser("serve-web", help="Levanta la app web local")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8787)
    web.set_defaults(func=cmd_serve_web)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
