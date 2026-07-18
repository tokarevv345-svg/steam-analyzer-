from __future__ import annotations

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
