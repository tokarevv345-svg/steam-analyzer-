"""Разовый ручной backfill истории цен через pricehistory (требует STEAM_SESSION_COOKIE в .env).

Не часть автоматического Collector'а. Запускать вручную, изредка, для теста Analyzer
на реальных исторических данных — см. docs/SCOPE.md.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx
from dotenv import load_dotenv

from src.steam_analyzer_data.collector.steam_market_client import (
    DEFAULT_HEADERS,
    STEAM_MARKET_LISTING_URL,
    STEAM_PROXY_URL,
    SteamMarketError,
)
from src.steam_analyzer_data.scheduler.collection_job import APP_ID_CS2, TRACKED_ITEMS
from src.steam_analyzer_data.storage.database import create_session_factory
from src.steam_analyzer_data.storage.repository import (
    get_or_create_item,
    save_price_snapshot,
)

load_dotenv()

PRICEHISTORY_URL = "https://steamcommunity.com/market/pricehistory/"
REQUEST_TIMEOUT_SECONDS = 15.0
REQUEST_DELAY_SECONDS = 8.0
DATE_FORMAT = "%b %d %Y %H: +0"

# Backoff при 429 — то же самое поведение, что уже проверено в steam_market_client.py.
MAX_RETRIES_ON_RATE_LIMIT = 3
BASE_BACKOFF_SECONDS = 5.0

# Свой клиент, а не голый httpx.get(): без системного прокси из реестра Windows
# (trust_env=False), с браузерными заголовками и явным proxy= из .env.
_client = httpx.Client(
    timeout=REQUEST_TIMEOUT_SECONDS,
    trust_env=False,
    headers=DEFAULT_HEADERS,
    proxy=STEAM_PROXY_URL,
)


def _parse_history_point(row: list[object]) -> tuple[datetime, Decimal, int]:
    try:
        date_str, price, volume_str = row
        collected_at = datetime.strptime(str(date_str), DATE_FORMAT)
        return collected_at, Decimal(str(price)), int(str(volume_str))
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise SteamMarketError(f"Не удалось разобрать точку истории цен: {row!r}") from exc


def fetch_price_history(
    app_id: int, market_hash_name: str, cookie: str
) -> list[tuple[datetime, Decimal, int]]:
    listing_url = f"{STEAM_MARKET_LISTING_URL}/{app_id}/{market_hash_name}"
    params: dict[str, str | int] = {
        "appid": app_id,
        "market_hash_name": market_hash_name,
        "currency": 1,
    }
    headers = {"Referer": listing_url}
    cookies = {"steamLoginSecure": cookie}

    response = _client.get(PRICEHISTORY_URL, params=params, headers=headers, cookies=cookies)
    for attempt in range(MAX_RETRIES_ON_RATE_LIMIT):
        if response.status_code != 429:
            break
        backoff = BASE_BACKOFF_SECONDS * (2**attempt)
        print(f"429 от Steam, попытка {attempt + 1}/{MAX_RETRIES_ON_RATE_LIMIT}, жду {backoff:.0f} сек")
        time.sleep(backoff)
        response = _client.get(PRICEHISTORY_URL, params=params, headers=headers, cookies=cookies)

    response.raise_for_status()
    data = response.json()

    if not data.get("success") or "prices" not in data:
        raise SteamMarketError(f"Steam не вернул историю для {market_hash_name!r}: {data}")

    return [_parse_history_point(row) for row in data["prices"]]


def main() -> None:
    cookie = os.environ["STEAM_SESSION_COOKIE"]
    if not cookie:
        raise RuntimeError(
            "STEAM_SESSION_COOKIE в .env пуст. Залогиньтесь на steamcommunity.com, "
            "возьмите cookie steamLoginSecure из DevTools и впишите его в .env, "
            "прежде чем запускать backfill."
        )
    session_factory = create_session_factory()

    with session_factory() as session:
        for market_hash_name in TRACKED_ITEMS:
            print(f"Backfill: {market_hash_name}")
            history = fetch_price_history(APP_ID_CS2, market_hash_name, cookie)
            item = get_or_create_item(session, market_hash_name)

            saved_count = 0
            for collected_at, price, volume in history:
                if save_price_snapshot(session, item, price, volume, collected_at) is not None:
                    saved_count += 1

            session.commit()
            print(f"  -> получено {len(history)} точек, сохранено новых: {saved_count}")
            time.sleep(REQUEST_DELAY_SECONDS)


if __name__ == "__main__":
    main()
