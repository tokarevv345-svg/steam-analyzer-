from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class SignalType(str, enum.Enum):
    FLIP = "flip"
    INVESTMENT = "investment"


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("market_hash_name", name="uq_items_market_hash_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    market_hash_name: Mapped[str] = mapped_column(nullable=False)
    item_type: Mapped[str] = mapped_column(nullable=False)
    exterior: Mapped[str | None] = mapped_column(nullable=True)
    stattrak: Mapped[bool] = mapped_column(default=False, nullable=False)
    rarity: Mapped[str] = mapped_column(nullable=False)
    image_url: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    price_snapshots: Mapped[list[PriceSnapshot]] = relationship(back_populates="item")
    signals: Mapped[list[Signal]] = relationship(back_populates="item")


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    __table_args__ = (
        Index("ix_price_snapshots_item_collected", "item_id", "collected_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    volume: Mapped[int | None] = mapped_column(nullable=True)
    collected_at: Mapped[datetime] = mapped_column(nullable=False)

    item: Mapped[Item] = relationship(back_populates="price_snapshots")


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    signal_type: Mapped[SignalType] = mapped_column(
        SqlEnum(SignalType, name="signal_type"), nullable=False
    )
    buy_price_suggested: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    sell_price_suggested: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    expected_profit_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    spread_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    trend_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    liquidity_factor: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True
    )
    volatility_penalty: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True
    )
    sent_to_telegram: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    item: Mapped[Item] = relationship(back_populates="signals")
