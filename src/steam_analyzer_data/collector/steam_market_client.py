from __future__ import annotations

import atexit
import random
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx

STEAM_MARKET_PRICEOVERVIEW_URL = "https://steamcommunity.com/market/priceoverview/"
USD_CURRENCY_CODE = 1
REQUEST_TIMEOUT_SECONDS = 10.0

# Rate limiting: пауза между запросами со случайным разбросом (jitter),
# чтобы не идти к Steam ровными интервалами.
MIN_REQUEST_DELAY_SECONDS = 0.8
MAX_REQUEST_DELAY_SECONDS = 1.5

# Backoff при 429: сколько раз повторить и с каким нарастающим ожиданием.
MAX_RETRIES_ON_RATE_LIMIT = 3
BASE_BACKOFF_SECONDS = 5.0

_client = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False)
atexit.register(_client.close)

_last_request_at: float = 0.0


def _wait_for_rate_limit() -> None:
    global _last_request_at
    delay = random.uniform(MIN_REQUEST_DELAY_SECONDS, MAX_REQUEST_DELAY_SECONDS)
    elapsed = time.monotonic() - _last_request_at
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_request_at = time.monotonic()


def _get_with_backoff(url: str, params: dict[str, str | int]) -> httpx.Response:
    for attempt in range(MAX_RETRIES_ON_RATE_LIMIT + 1):
        _wait_for_rate_limit()
        response = _client.get(url, params=params)

        if response.status_code != 429:
            return response

        if attempt == MAX_RETRIES_ON_RATE_LIMIT:
            return response

        backoff = BASE_BACKOFF_SECONDS * (2**attempt)
        print(
            f"429 от Steam, попытка {attempt + 1}/{MAX_RETRIES_ON_RATE_LIMIT}, "
            f"жду {backoff:.0f} сек"
        )
        time.sleep(backoff)

    return response


class SteamMarketError(Exception):
    pass


@dataclass(frozen=True)
class PriceOverview:
    price: Decimal
    volume: int | None


def _parse_price(raw_price: str) -> Decimal:
    cleaned = raw_price.replace("$", "").replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise SteamMarketError(f"Не удалось разобрать цену: {raw_price!r}") from exc


def _parse_volume(raw_volume: str | None) -> int | None:
    if raw_volume is None:
        return None
    return int(raw_volume.replace(",", ""))


def fetch_price_overview(
    app_id: int,
    market_hash_name: str,
    currency: int = USD_CURRENCY_CODE,
) -> PriceOverview:
    params: dict[str, str | int] = {
        "appid": app_id,
        "market_hash_name": market_hash_name,
        "currency": currency,
    }
    response = _get_with_backoff(STEAM_MARKET_PRICEOVERVIEW_URL, params)
    response.raise_for_status()
    data = response.json()

    if not data.get("success"):
        raise SteamMarketError(
            f"Steam не вернул данные по предмету {market_hash_name!r} (appid={app_id})"
        )

    lowest_price = data.get("lowest_price") or data.get("median_price")
    if lowest_price is None:
        raise SteamMarketError(
            f"В ответе Steam нет цены для {market_hash_name!r} (appid={app_id})"
        )

    return PriceOverview(
        price=_parse_price(lowest_price),
        volume=_parse_volume(data.get("volume")),
    )
