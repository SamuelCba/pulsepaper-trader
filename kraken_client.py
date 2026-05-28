from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlencode
from urllib.request import urlopen

from strategy import Candle


BASE_URL = "https://api.kraken.com/0/public/OHLC"


@dataclass
class KrakenFetchResult:
    pair: str
    interval: int
    candles: List[Candle]


def normalize_pair(pair: str) -> str:
    raw = pair.strip().upper().replace("-", "/")
    aliases = {
        "BTC/USDT": "XBTUSDT",
        "BTC/USD": "XBTUSD",
        "ETH/USDT": "ETHUSDT",
        "ETH/USD": "ETHUSD",
        "SOL/USDT": "SOLUSDT",
        "SOL/USD": "SOLUSD",
        "XRP/USDT": "XRPUSDT",
        "XRP/USD": "XRPUSD",
        "ADA/USDT": "ADAUSDT",
        "ADA/USD": "ADAUSD",
        "XBT/USDT": "XBTUSDT",
        "XBT/USD": "XBTUSD",
    }
    return aliases.get(raw, raw.replace("/", ""))


def fetch_ohlc(pair: str = "BTC/USDT", interval: int = 15) -> KrakenFetchResult:
    query = urlencode({"pair": normalize_pair(pair), "interval": interval})
    url = f"{BASE_URL}?{query}"
    with urlopen(url, timeout=20) as response:
        payload = json.load(response)

    errors = payload.get("error") or []
    if errors:
        raise RuntimeError(f"Kraken devolvio errores: {errors}")

    result = payload.get("result", {})
    result_keys = [key for key in result.keys() if key != "last"]
    if not result_keys:
        raise RuntimeError("Kraken no devolvio velas")

    pair_key = result_keys[0]
    candles: List[Candle] = []
    for item in result[pair_key]:
        timestamp = datetime.fromtimestamp(int(float(item[0])), tz=timezone.utc).isoformat()
        candles.append(
            Candle(
                timestamp=timestamp,
                open=float(item[1]),
                high=float(item[2]),
                low=float(item[3]),
                close=float(item[4]),
                volume=float(item[6]),
            )
        )
    return KrakenFetchResult(pair=pair_key, interval=interval, candles=candles)


def save_candles_csv(path: str, candles: List[Candle]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for candle in candles:
            writer.writerow(
                [
                    candle.timestamp,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                ]
            )
