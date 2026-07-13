from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Item, PriceSnapshot

UNKNOWN_PLACEHOLDER = "unknown"

_EXTERIOR_VALUES = {
    "Factory New",
    "Minimal Wear",
    "Field-Tested",
    "Well-Worn",
    "Battle-Scarred",
}
_STATTRAK_PREFIX = "StatTrak™ "


def _parse_stattrak(market_hash_name: str) -> bool:
    return market_hash_name.startswith(_STATTRAK_PREFIX)


def _parse_exterior(market_hash_name: str) -> str | None:
    if market_hash_name.endswith(")") and "(" in market_hash_name:
        candidate = market_hash_name.rsplit("(", 1)[1][:-1]
        if candidate in _EXTERIOR_VALUES:
            return candidate
    return None


def get_or_create_item(session: Session, market_hash_name: str) -> Item:
    existing = session.scalar(
        select(Item).where(Item.market_hash_name == market_hash_name)
    )
    if existing is not None:
        return existing

    item = Item(
        market_hash_name=market_hash_name,
        item_type=UNKNOWN_PLACEHOLDER,
        exterior=_parse_exterior(market_hash_name),
        stattrak=_parse_stattrak(market_hash_name),
        rarity=UNKNOWN_PLACEHOLDER,
    )
    session.add(item)
    session.flush()
    return item


def save_price_snapshot(
    session: Session,
    item: Item,
    price: Decimal,
    volume: int | None,
    collected_at: datetime,
) -> PriceSnapshot:
    snapshot = PriceSnapshot(
        item_id=item.id,
        price=price,
        volume=volume,
        collected_at=collected_at,
    )
    session.add(snapshot)
    session.flush()
    return snapshot
