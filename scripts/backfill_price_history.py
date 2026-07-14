"""Разовый ручной backfill истории цен через pricehistory (требует STEAM_SESSION_COOKIE в .env).

Не часть автоматического Collector'а. Запускать вручную, изредка, для теста Analyzer
на реальных исторических данных — см. docs/SCOPE.md.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from decimal import Decimal

import httpx
from dotenv import load_dotenv

from src.steam_analyzer_data.scheduler.collection_job import APP_ID_CS2, TRACKED_ITEMS
from src.steam_analyzer_data.storage.database import create_session_factory
from src.steam_analyzer_data.storage.repository import (
    get_or_create_item,
    save_price_snapshot,
)

load_dotenv()

PRICEHISTORY_URL = "https://steamcommunity.com/market/pricehistory/"
REQUEST_DELAY_SECONDS = 3.0
DATE_FORMAT = "%b %d %Y %H: +0"


def _parse_history_point(row: list[object]) -> tuple[datetime, Decimal, int]:
    date_str, price, volume_str = row
    collected_at = datetime.strptime(str(date_str), DATE_FORMAT)
    return collected_at, Decimal(str(price)), int(str(volume_str))


def fetch_price_history(
    app_id: int, market_hash_name: str, cookie: str
) -> list[tuple[datetime, Decimal, int]]:
    response = httpx.get(
        PRICEHISTORY_URL,
        params={"appid": app_id, "market_hash_name": market_hash_name, "currency": 1},
        cookies={"steamLoginSecure": cookie},
        timeout=15.0,
    )
    response.raise_for_status()
    data = response.json()

    if not data.get("success") or "prices" not in data:
        raise RuntimeError(f"Steam не вернул историю для {market_hash_name!r}: {data}")

    return [_parse_history_point(row) for row in data["prices"]]


def main() -> None:
    cookie = os.environ["STEAM_SESSION_COOKIE"]
    session_factory = create_session_factory()

    with session_factory() as session:
        for market_hash_name in TRACKED_ITEMS:
            print(f"Backfill: {market_hash_name}")
            history = fetch_price_history(APP_ID_CS2, market_hash_name, cookie)
            item = get_or_create_item(session, market_hash_name)

            for collected_at, price, volume in history:
                save_price_snapshot(session, item, price, volume, collected_at)

            session.commit()
            print(f"  -> добавлено {len(history)} точек")
            time.sleep(REQUEST_DELAY_SECONDS)


if __name__ == "__main__":
    main()
