from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from src.steam_analyzer_data.collector import steam_market_client as smc

ITEM_NAMEID = 7178002


def _fake_histogram_response(payload: dict[str, object]) -> httpx.Response:
    request = httpx.Request("GET", smc.STEAM_MARKET_ORDER_HISTOGRAM_URL)
    return httpx.Response(200, json=payload, request=request)


def test_fetch_order_histogram_reads_counts_from_order_graphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Реальный ответ Steam (проверено вживую 18.07.2026) не содержит полей
    # "sell_order_count"/"buy_order_count" вообще — только *_order_graph.
    payload = {
        "success": 1,
        "lowest_sell_order": "4228",
        "highest_buy_order": "4131",
        "sell_order_graph": [
            [42.28, 1, "1 sell order at $42.28 or lower"],
            [42.47, 2, "2 sell orders at $42.47 or lower"],
            [42.66, 4, "4 sell orders at $42.66 or lower"],
        ],
        "buy_order_graph": [
            [41.31, 14, "14 buy orders at $41.31 or higher"],
            [41.29, 39, "39 buy orders at $41.29 or higher"],
            [41.26, 91, "91 buy orders at $41.26 or higher"],
        ],
    }
    monkeypatch.setattr(
        smc, "_get_with_backoff", lambda *a, **kw: _fake_histogram_response(payload)
    )

    result = smc.fetch_order_histogram(ITEM_NAMEID)

    assert result.sell_order_count == 4
    assert result.buy_order_count == 91


def test_fetch_order_histogram_empty_graph_is_zero_not_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "success": 1,
        "lowest_sell_order": "4228",
        "highest_buy_order": "4131",
        "sell_order_graph": [],
        "buy_order_graph": [],
    }
    monkeypatch.setattr(
        smc, "_get_with_backoff", lambda *a, **kw: _fake_histogram_response(payload)
    )

    result = smc.fetch_order_histogram(ITEM_NAMEID)

    assert result.sell_order_count == 0
    assert result.buy_order_count == 0


def test_fetch_order_histogram_missing_buy_side_returns_none_not_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Реальный случай, пойманный вживую 18.07.2026 на 'Glock-18 | Fade (FN)':
    # sell_order_summary = "There are no active listings for this item." —
    # это не пропадает highest_buy_order, а лишает sell-стороны. Здесь —
    # обратная, но аналогичная ситуация: нет открытых ордеров на покупку.
    # В обоих случаях это легитимное состояние рынка, а не ошибка ответа.
    payload = {
        "success": 1,
        "lowest_sell_order": "4228",
        "highest_buy_order": None,
        "sell_order_graph": [[42.28, 1, "1 sell order at $42.28 or lower"]],
        "buy_order_graph": [],
    }
    monkeypatch.setattr(
        smc, "_get_with_backoff", lambda *a, **kw: _fake_histogram_response(payload)
    )

    result = smc.fetch_order_histogram(ITEM_NAMEID)

    assert result.lowest_sell_order == Decimal("42.28")
    assert result.highest_buy_order is None


def test_fetch_order_histogram_missing_sell_side_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Без lowest_sell_order нет вообще никакой цены, которую можно записать —
    # это остаётся жёстким требованием (как раньше было у priceoverview).
    payload: dict[str, object] = {
        "success": 1,
        "lowest_sell_order": None,
        "highest_buy_order": "4131",
        "sell_order_graph": [],
        "buy_order_graph": [[41.31, 14, "14 buy orders at $41.31 or higher"]],
    }
    monkeypatch.setattr(
        smc, "_get_with_backoff", lambda *a, **kw: _fake_histogram_response(payload)
    )

    with pytest.raises(smc.SteamMarketError):
        smc.fetch_order_histogram(ITEM_NAMEID)


def test_parse_order_count_from_graph_raises_steam_market_error_on_malformed_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ряд короче ожидаемого [цена, количество, описание] — раньше падало бы
    # необработанным IndexError и роняло весь цикл сбора в collect_once().
    payload = {
        "success": 1,
        "lowest_sell_order": "4228",
        "highest_buy_order": "4131",
        "sell_order_graph": [[42.28]],
        "buy_order_graph": [],
    }
    monkeypatch.setattr(
        smc, "_get_with_backoff", lambda *a, **kw: _fake_histogram_response(payload)
    )

    with pytest.raises(smc.SteamMarketError):
        smc.fetch_order_histogram(ITEM_NAMEID)
