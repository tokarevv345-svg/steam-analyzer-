from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx
from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore[import-untyped]

from ..collector.steam_market_client import (
    RUB_CURRENCY_CODE,
    SteamMarketError,
    fetch_order_histogram,
    resolve_item_nameid,
)
from ..storage.database import create_session_factory
from ..storage.repository import get_or_create_item, save_item_nameid, save_price_snapshot

APP_ID_CS2 = 730

TRACKED_ITEMS: list[str] = [
    "AK-47 | Redline (Field-Tested)",
    "AWP | Asiimov (Field-Tested)",
    "Glock-18 | Fade (Factory New)",
]

REQUEST_DELAY_SECONDS = 2.0
DEFAULT_INTERVAL_MINUTES = 60


def collect_once() -> None:
    session_factory = create_session_factory()
    with session_factory() as session:
        for market_hash_name in TRACKED_ITEMS:
            item = get_or_create_item(session, market_hash_name)
            try:
                # item_nameid резолвится один раз на предмет (через сторонний
                # датасет, см. resolve_item_nameid) и кэшируется в БД — на
                # следующих циклах сбора датасет для этого предмета уже не нужен.
                # Локальная переменная nameid — иначе mypy не может проследить,
                # что item.item_nameid уже не None после save_item_nameid()
                # (баг найден и подтверждён Claude Code 17.07.2026).
                if item.item_nameid is None:
                    nameid = resolve_item_nameid(APP_ID_CS2, market_hash_name)
                    save_item_nameid(session, item, nameid)
                    session.commit()
                else:
                    nameid = item.item_nameid

                # itemordershistogram вместо priceoverview: не требует cookie-
                # разогрева и держит заметно больше запросов, не отдавая 429
                # там, где priceoverview резал уже на первом десятке (см. разбор
                # в чате от 17.07.2026). Собираем и храним в рублях — валюте,
                # в которой Steam реально отдаёт данные этому аккаунту (see
                # docs/SCOPE.md, запись от 15.07.2026). Конвертация в доллары —
                # на стороне Analyzer, в момент использования, не здесь.
                histogram = fetch_order_histogram(nameid, currency=RUB_CURRENCY_CODE)
            except (
                SteamMarketError,
                httpx.HTTPStatusError,
                httpx.TransportError,
            ) as exc:
                print(f"Пропущен {market_hash_name!r}: {exc}")
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            save_price_snapshot(
                session,
                item,
                histogram.lowest_sell_order,
                # volume=None: itemordershistogram не даёт торговый объём (сделок/день),
                # только глубину стакана на текущий момент. histogram.sell_order_count —
                # это "сколько лотов сейчас выставлено", а не "сколько продано" —
                # calculate_liquidity_factor() в analyzer/signal_calculator.py трактует
                # volume именно как объём сделок, так что писать сюда глубину стакана
                # значило бы тихо портить liquidity_factor для FLIP/INVESTMENT. Найдено
                # ревью 19.07.2026 перед мержем в master.
                None,
                datetime.now(UTC).replace(tzinfo=None),
                highest_buy_order=histogram.highest_buy_order,
                buy_order_count=histogram.buy_order_count,
            )
            session.commit()
            time.sleep(REQUEST_DELAY_SECONDS)


def build_scheduler(
    interval_minutes: float = DEFAULT_INTERVAL_MINUTES,
) -> BlockingScheduler:
    scheduler = BlockingScheduler()
    scheduler.add_job(
        collect_once, "interval", minutes=interval_minutes, next_run_time=datetime.now()
    )
    return scheduler


if __name__ == "__main__":
    build_scheduler(DEFAULT_INTERVAL_MINUTES).start()
