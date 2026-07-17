from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage.models import Item, PriceSnapshot, Signal, SignalType

# Комиссия Steam Market — единственная не-гипотетическая цифра в дизайне
# (docs/SIGNALS_ARCHITECTURE.md, раздел 2): 5% Steam + 10% издатель.
STEAM_FEE_RATE = Decimal("0.15")

SPREAD_WINDOW_DAYS = 7
TREND_WINDOW_DAYS = 30

# Веса final_score — ЗАГЛУШКИ, не откалиброваны бэктестом. Этап 9, часть 2
# (docs/SIGNALS_ARCHITECTURE.md, раздел 11, п.3) должен пересчитать их по
# фактическим результатам, а не оставлять равными 1.
WEIGHT_SPREAD = Decimal("1")
WEIGHT_LIQUIDITY = Decimal("1")
WEIGHT_VOLATILITY = Decimal("1")
WEIGHT_TREND = Decimal("1")

# Порог отклонения для flip-кандидата — тоже заглушка, не откалиброван.
FLIP_DEVIATION_THRESHOLD = Decimal("0.05")

SCORE_DECIMAL_PLACES = Decimal("0.0001")


def _snapshots_since(session: Session, item_id: int, days: int) -> list[PriceSnapshot]:
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    return list(
        session.scalars(
            select(PriceSnapshot)
            .where(PriceSnapshot.item_id == item_id, PriceSnapshot.collected_at >= since)
            .order_by(PriceSnapshot.collected_at)
        )
    )


def _latest_snapshots(session: Session, item_id: int, limit: int) -> list[PriceSnapshot]:
    return list(
        session.scalars(
            select(PriceSnapshot)
            .where(PriceSnapshot.item_id == item_id)
            .order_by(PriceSnapshot.collected_at.desc())
            .limit(limit)
        )
    )


def calculate_spread_score(
    median_price: Decimal, current_price: Decimal
) -> Decimal | None:
    """(median_sell_price_7d − current_buy_price) / current_buy_price — Приложение А."""
    if current_price == 0:
        return None
    return (median_price - current_price) / current_price


def calculate_liquidity_factor(snapshots: list[PriceSnapshot]) -> Decimal | None:
    """log10(средний объём продаж/день + 1) — сжимаем, чтобы уместиться в
    Numeric(6,4) и не дать самым ликвидным предметам задавить остальные
    компоненты final_score линейно. Не из Приложения А — моё решение,
    само число "средний объём/день" там не было формализовано."""
    volumes = [s.volume for s in snapshots if s.volume is not None]
    if not volumes:
        return None
    avg_volume = sum(volumes) / len(volumes)
    return Decimal(str(math.log10(avg_volume + 1))).quantize(SCORE_DECIMAL_PLACES)


def calculate_volatility_penalty(snapshots: list[PriceSnapshot]) -> Decimal | None:
    """std_dev/mean цены за окно — Приложение А."""
    prices = [s.price for s in snapshots]
    if len(prices) < 2:
        return None
    mean_price = statistics.mean(prices)
    if mean_price == 0:
        return None
    std_price = statistics.stdev(prices)
    return (std_price / mean_price).quantize(SCORE_DECIMAL_PLACES)


def calculate_trend_score(snapshots: list[PriceSnapshot]) -> Decimal | None:
    """Наклон линейной регрессии цены за окно, нормированный на текущую
    цену (доля от цены в день) — Приложение А ("наклон линейной регрессии
    цены за N дней (trend_score)")."""
    if len(snapshots) < 2:
        return None
    first_ts = snapshots[0].collected_at
    days_offset = [(s.collected_at - first_ts).total_seconds() / 86400 for s in snapshots]
    if len(set(days_offset)) < 2:
        return None
    prices = [float(s.price) for s in snapshots]
    slope_per_day, _ = statistics.linear_regression(days_offset, prices)
    current_price = prices[-1]
    if current_price == 0:
        return None
    return Decimal(str(slope_per_day / current_price)).quantize(SCORE_DECIMAL_PLACES)


def calculate_final_score(
    spread_score: Decimal | None,
    liquidity_factor: Decimal | None,
    volatility_penalty: Decimal | None,
    trend_score: Decimal | None,
) -> Decimal:
    """final_score = w1*spread_score + w2*liquidity_factor − w3*volatility_penalty
    + w4*trend_score — Приложение А. Недостающие компоненты считаются нулём."""
    return (
        WEIGHT_SPREAD * (spread_score or Decimal("0"))
        + WEIGHT_LIQUIDITY * (liquidity_factor or Decimal("0"))
        - WEIGHT_VOLATILITY * (volatility_penalty or Decimal("0"))
        + WEIGHT_TREND * (trend_score or Decimal("0"))
    ).quantize(SCORE_DECIMAL_PLACES)


def _is_flip_deviation_confirmed(
    median_price: Decimal, latest_two: list[PriceSnapshot]
) -> bool:
    """Подтверждение отклонения на двух подряд опросах Collector'а — защита
    от разового глюка/устаревшей цены (SIGNALS_ARCHITECTURE.md, раздел 7)."""
    if len(latest_two) < 2:
        return False
    for snapshot in latest_two:
        spread = calculate_spread_score(median_price, snapshot.price)
        if spread is None or spread < FLIP_DEVIATION_THRESHOLD:
            return False
    return True


def analyze_item(session: Session, item: Item, usd_rub_rate: Decimal) -> Signal | None:
    """Считает spread_score, liquidity_factor, volatility_penalty, trend_score
    и final_score по данным price_snapshots. Пишет строку в signals, только
    если flip-отклонение подтвердилось на двух последних опросах подряд —
    иначе возвращает None, ничего не сохраняя.

    price_snapshots хранится в рублях (валюта, в которой Steam реально отдаёт
    данные аккаунту — см. docs/SCOPE.md, запись от 15.07.2026). Коэффициенты
    (spread/volatility/trend) от валюты не зависят — считаются как есть. Курс
    нужен только в самом конце, чтобы перевести buy/sell_price_suggested
    в доллары для читаемого результата. Курс — забота вызывающего кода
    (Collector, fetch_usd_rub_rate), не этой функции: Analyzer работает
    только с данными из Storage, за сеть не отвечает.

    DoD этого этапа: score считается и пишется в signals, не более. Веса и
    порог — заглушки, бэктест и калибровка — Этап 9, часть 2.
    """
    latest_two = _latest_snapshots(session, item.id, limit=2)
    if not latest_two:
        return None
    current_price_rub = latest_two[0].price

    spread_window = _snapshots_since(session, item.id, SPREAD_WINDOW_DAYS)
    trend_window = _snapshots_since(session, item.id, TREND_WINDOW_DAYS)

    median_price_rub = (
        statistics.median([s.price for s in spread_window]) if spread_window else None
    )
    spread = (
        calculate_spread_score(median_price_rub, current_price_rub)
        if median_price_rub is not None
        else None
    )
    liquidity = calculate_liquidity_factor(spread_window)
    volatility = calculate_volatility_penalty(spread_window)
    trend = calculate_trend_score(trend_window)

    if median_price_rub is None or not _is_flip_deviation_confirmed(
        median_price_rub, latest_two
    ):
        return None

    score = calculate_final_score(spread, liquidity, volatility, trend)

    buy_price_usd = (current_price_rub / usd_rub_rate).quantize(Decimal("0.01"))
    sell_price_usd = (median_price_rub / usd_rub_rate).quantize(Decimal("0.01"))
    net_profit = sell_price_usd * (1 - STEAM_FEE_RATE) - buy_price_usd
    expected_profit_pct = (
        (net_profit / buy_price_usd * 100) if buy_price_usd != 0 else Decimal("0")
    )

    signal = Signal(
        item_id=item.id,
        signal_type=SignalType.FLIP,
        buy_price_suggested=buy_price_usd,
        sell_price_suggested=sell_price_usd,
        expected_profit_pct=expected_profit_pct.quantize(Decimal("0.01")),
        score=score,
        spread_score=spread,
        trend_score=trend,
        liquidity_factor=liquidity,
        volatility_penalty=volatility,
    )
    session.add(signal)
    session.flush()
    return signal
