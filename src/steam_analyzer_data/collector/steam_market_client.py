from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx

STEAM_MARKET_PRICEOVERVIEW_URL = "https://steamcommunity.com/market/priceoverview/"
USD_CURRENCY_CODE = 1
REQUEST_TIMEOUT_SECONDS = 10.0


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
    response = httpx.get(
        STEAM_MARKET_PRICEOVERVIEW_URL,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
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
