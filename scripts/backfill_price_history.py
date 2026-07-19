"""Разовый ручной backfill истории цен через pricehistory (требует STEAM_SESSION_COOKIE в .env).

Не часть автоматического Collector'а. Запускать вручную, изредка, для теста Analyzer
на реальных исторических данных — см. docs/SCOPE.md.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

from src.steam_analyzer_data.collector.steam_market_client import (
    RUB_CURRENCY_CODE,
    STEAM_MARKET_LISTING_URL,
    STEAM_MARKET_SEARCH_URL,
    SteamMarketError,
    _client,
    _get_with_backoff,
)
from src.steam_analyzer_data.scheduler.collection_job import APP_ID_CS2, TRACKED_ITEMS
from src.steam_analyzer_data.storage.database import create_session_factory
from src.steam_analyzer_data.storage.repository import (
    get_or_create_item,
    save_price_snapshot,
)

PRICEHISTORY_URL = "https://steamcommunity.com/market/pricehistory/"
REQUEST_DELAY_SECONDS = 8.0
DATE_FORMAT = "%b %d %Y %H: +0"


def _parse_history_point(row: list[object]) -> tuple[datetime, Decimal, int]:
    try:
        date_str, price, volume_str = row
        collected_at = datetime.strptime(str(date_str), DATE_FORMAT)
        return collected_at, Decimal(str(price)), int(str(volume_str))
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise SteamMarketError(f"Не удалось разобрать точку истории цен: {row!r}") from exc


def _ensure_sessionid() -> None:
    # Раньше здесь было написано, что sessionid заставляет pricehistory уважать
    # параметр currency. Проверено вживую 19.07.2026 (A/B на реальном
    # pricehistory, со свежим steamLoginSecure): цена ОДИНАКОВАЯ что с
    # sessionid, что без него — в обоих случаях в рублях, хотя currency=1
    # (USD) запрошен явно. То есть заявление про sessionid было неверным —
    # настоящая причина, почему pricehistory всегда отдаёт рубли для этого
    # аккаунта, не установлена (возможно, валюта Steam-кошелька на стороне
    # аккаунта просто переопределяет любой запрошенный currency). Сама
    # функция оставлена — дешёвая (один анонимный запрос) и, возможно,
    # нужна для чего-то другого на pricehistory, что не проверялось, — но
    # её оригинальное обоснование недостоверно, не доверять ему.
    if "sessionid" in _client.cookies:
        return
    _get_with_backoff(STEAM_MARKET_SEARCH_URL, {})


def fetch_price_history(
    app_id: int, market_hash_name: str, cookie: str
) -> list[tuple[datetime, Decimal, int]]:
    _ensure_sessionid()

    listing_url = f"{STEAM_MARKET_LISTING_URL}/{app_id}/{market_hash_name}"
    params: dict[str, str | int] = {
        "appid": app_id,
        "market_hash_name": market_hash_name,
        # currency=1 (USD) запрашивался раньше, но реально Steam всегда
        # отдаёт рубли для этого аккаунта независимо от параметра (см.
        # _ensure_sessionid) — запрашиваем RUB_CURRENCY_CODE явно, чтобы
        # код совпадал с тем, что реально происходит, и с тем, как хранит
        # цены collect_once() (см. docs/SCOPE.md, запись от 15.07.2026).
        "currency": RUB_CURRENCY_CODE,
    }
    headers = {"Referer": listing_url}
    cookies = {"steamLoginSecure": cookie}

    # Через общий _get_with_backoff — тот же джиттер, backoff при 429 и
    # ротация UA/cookies каждые 10 запросов, что и у автоматического
    # Collector'а, вместо отдельной копии retry-цикла с теми же константами.
    response = _get_with_backoff(
        PRICEHISTORY_URL, params, extra_headers=headers, extra_cookies=cookies
    )
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
