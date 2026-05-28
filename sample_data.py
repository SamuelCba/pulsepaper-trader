from __future__ import annotations

import csv
import math
import random
from datetime import datetime, timedelta


def generate_sample_csv(path: str, rows: int = 500) -> None:
    random.seed(7)
    start = datetime(2026, 1, 1, 0, 0, 0)
    price = 42000.0

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])

        for index in range(rows):
            timestamp = start + timedelta(minutes=15 * index)
            drift = 18 * math.sin(index / 16) + 7 * math.sin(index / 5)
            noise = random.uniform(-45, 45)
            open_price = price
            close_price = max(1000.0, open_price + drift + noise)
            high_price = max(open_price, close_price) + random.uniform(12, 55)
            low_price = min(open_price, close_price) - random.uniform(12, 55)
            volume = 100 + abs(drift) * 1.2 + random.uniform(0, 80)

            writer.writerow(
                [
                    timestamp.isoformat(),
                    round(open_price, 2),
                    round(high_price, 2),
                    round(low_price, 2),
                    round(close_price, 2),
                    round(volume, 2),
                ]
            )
            price = close_price
