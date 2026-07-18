from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Item, PriceSnapshot

UNKNOWN_PLACEHOLDER = "unknown"

# Предохранитель от повторных/случайных записей: если для предмета уже есть
# снепшот в пределах этого окна ДО ИЛИ ПОСЛЕ новой точки — новый не сохраняем.
# Смотрим в обе стороны от времени, а не только "после" — иначе исторические
# точки backfill'а (которые раньше уже существующих) ошибочно посчитались бы
# "слишком свежими" и отбрасывались бы все подряд.
MIN_SNAPSHOT_INTERVAL = timedelta(minutes=1)

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


def save_item_nameid(session: Session, item: Item, item_nameid: int) -> None:
    """Кэширует резолвнутый item_nameid на предмете, чтобы Collector не грузил
    страницу листинга повторно на следующих циклах сбора (см. resolve_item_nameid
    в steam_market_client.py)."""
    item.item_nameid = item_nameid
    session.flush()


def save_price_snapshot(
    session: Session,
    item: Item,
    price: Decimal,
    volume: int | None,
    collected_at: datetime,
    highest_buy_order: Decimal | None = None,
    buy_order_count: int | None = None,
) -> PriceSnapshot | None:
    nearby_snapshot = session.scalar(
        select(PriceSnapshot)
        .where(
            PriceSnapshot.item_id == item.id,
            PriceSnapshot.collected_at >= collected_at - MIN_SNAPSHOT_INTERVAL,
            PriceSnapshot.collected_at <= collected_at + MIN_SNAPSHOT_INTERVAL,
        )
        .limit(1)
    )
    if nearby_snapshot is not None:
        return None

    snapshot = PriceSnapshot(
        item_id=item.id,
        price=price,
        volume=volume,
        highest_buy_order=highest_buy_order,
        buy_order_count=buy_order_count,
        collected_at=collected_at,
    )
    session.add(snapshot)
    session.flush()
    return snapshot
