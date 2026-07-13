from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx
from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore[import-untyped]

from ..collector.steam_market_client import SteamMarketError, fetch_price_overview
from ..storage.database import create_session_factory
from ..storage.repository import get_or_create_item, save_price_snapshot

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
            try:
                overview = fetch_price_overview(APP_ID_CS2, market_hash_name)
            except (
                SteamMarketError,
                httpx.HTTPStatusError,
                httpx.TransportError,
            ) as exc:
                print(f"Пропущен {market_hash_name!r}: {exc}")
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            item = get_or_create_item(session, market_hash_name)
            save_price_snapshot(
                session,
                item,
                overview.price,
                overview.volume,
                datetime.now(UTC).replace(tzinfo=None),
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
