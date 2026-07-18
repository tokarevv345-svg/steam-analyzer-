from __future__ import annotations

import atexit
import os
import random
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

# Необязательный прокси для запросов к Steam, например "socks5://127.0.0.1:10808".
# Если переменная не задана (или пустая) — запрос идёт напрямую, как раньше.
STEAM_PROXY_URL = os.getenv("STEAM_PROXY_URL") or None

STEAM_MARKET_PRICEOVERVIEW_URL = "https://steamcommunity.com/market/priceoverview/"
STEAM_MARKET_LISTING_URL = "https://steamcommunity.com/market/listings"
STEAM_MARKET_SEARCH_URL = "https://steamcommunity.com/market/search"
STEAM_MARKET_SEARCH_RENDER_URL = "https://steamcommunity.com/market/search/render/"
STEAM_MARKET_ORDER_HISTOGRAM_URL = "https://steamcommunity.com/market/itemordershistogram"
USD_CURRENCY_CODE = 1
RUB_CURRENCY_CODE = 5
REQUEST_TIMEOUT_SECONDS = 10.0

# Несколько реальных строк UA разных браузеров/ОС. Выбираем один случайный
# на весь процесс (см. get_random_user_agent), а не на каждый запрос —
# смена UA посреди keep-alive соединения выглядит подозрительнее, чем один
# стабильный UA, потому что все остальные признаки (TLS, порядок заголовков)
# при этом не меняются.
USER_AGENTS: list[str] = [
    # Старый статический UA, который стоял здесь до перехода на ротацию —
    # оставлен в списке, просто теперь как один из вариантов, а не единственный.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:115.0) Gecko/20100101 Firefox/115.0",
]


def get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)


# Заголовки, маскирующие запрос под обычный браузер, чтобы Steam
# не резал ответы у "голого" HTTP-клиента.
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": get_random_user_agent(),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# Rate limiting: пауза между запросами со случайным разбросом (jitter),
# чтобы не идти к Steam ровными интервалами.
MIN_REQUEST_DELAY_SECONDS = 0.8
MAX_REQUEST_DELAY_SECONDS = 1.5

# Backoff при 429: сколько раз повторить и с каким нарастающим ожиданием.
MAX_RETRIES_ON_RATE_LIMIT = 3
BASE_BACKOFF_SECONDS = 5.0

# Долгоживущий процесс (например, APScheduler-джоб) иначе слал бы все запросы
# за часы работы от одного и того же UA с одними и теми же cookies. Каждые
# SESSION_ROTATION_REQUEST_COUNT запросов "личность" сбрасывается — как будто
# на сайт зашёл новый человек, а не один и тот же браузер держит сессию
# бесконечно.
SESSION_ROTATION_REQUEST_COUNT = 10

_client = httpx.Client(
    timeout=REQUEST_TIMEOUT_SECONDS,
    trust_env=False,
    headers=DEFAULT_HEADERS,
    proxy=STEAM_PROXY_URL,
)
atexit.register(_client.close)

_last_request_at: float = 0.0
_requests_since_session_start: int = 0


def _rotate_session_identity() -> None:
    global _requests_since_session_start
    _client.cookies.clear()
    _client.headers["User-Agent"] = get_random_user_agent()
    _requests_since_session_start = 0


def _wait_for_rate_limit() -> None:
    global _last_request_at
    delay = random.uniform(MIN_REQUEST_DELAY_SECONDS, MAX_REQUEST_DELAY_SECONDS)
    elapsed = time.monotonic() - _last_request_at
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_request_at = time.monotonic()


def _get_with_backoff(
    url: str,
    params: dict[str, str | int],
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    global _requests_since_session_start

    if _requests_since_session_start >= SESSION_ROTATION_REQUEST_COUNT:
        _rotate_session_identity()

    for attempt in range(MAX_RETRIES_ON_RATE_LIMIT + 1):
        _wait_for_rate_limit()
        response = _client.get(url, params=params, headers=extra_headers)
        _requests_since_session_start += 1

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


@dataclass(frozen=True)
class OrderHistogram:
    lowest_sell_order: Decimal
    highest_buy_order: Decimal
    sell_order_count: int | None
    buy_order_count: int | None


@dataclass(frozen=True)
class MarketSearchItem:
    hash_name: str
    sell_listings: int
    sell_price: Decimal


# Символы/суффиксы валют, которые Steam добавляет к цене — по мере надобности
# дополнять сюда, а не переписывать саму логику разбора.
_CURRENCY_MARKERS = ("$", "руб.")


def _parse_price(raw_price: str) -> Decimal:
    cleaned = raw_price
    for marker in _CURRENCY_MARKERS:
        cleaned = cleaned.replace(marker, "")
    cleaned = cleaned.replace("\xa0", "").replace(" ", "").strip()

    # "," и "." по-разному значат разделитель тысяч/десятичных в разных
    # валютах Steam (доллар: "1,234.56" — запятая тысячи, точка дробная;
    # рубль: "3302,17" — запятая дробная, тысячи через пробел). Последний
    # из двух символов в строке — всегда десятичный разделитель, остальные
    # вхождения — разделители тысяч, их просто убираем.
    last_comma = cleaned.rfind(",")
    last_dot = cleaned.rfind(".")
    decimal_sep_pos = max(last_comma, last_dot)

    if decimal_sep_pos == -1:
        normalized = cleaned
    else:
        integer_part = cleaned[:decimal_sep_pos].replace(",", "").replace(".", "")
        fractional_part = cleaned[decimal_sep_pos + 1 :]
        normalized = f"{integer_part}.{fractional_part}"

    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise SteamMarketError(f"Не удалось разобрать цену: {raw_price!r}") from exc


def _parse_volume(raw_volume: str | int | None) -> int | None:
    if raw_volume is None:
        return None
    if isinstance(raw_volume, int):
        return raw_volume
    return int(raw_volume.replace(",", ""))


def _ensure_session_warmed_up(app_id: int, market_hash_name: str) -> None:
    # Живой тест (17.07.2026) подтвердил: без cookies priceoverview режет
    # запрос 429 на первой же попытке — маскировки через один UA/Referer для
    # него недостаточно, cookies-разогрев обязателен. Мисматча с UA теперь
    # нет: DEFAULT_HEADERS выбирает случайный UA один раз при импорте модуля
    # и дальше не меняется, так что cookies, полученные при разогреве,
    # весь процесс идут с тем же самым UA, каким были получены.
    #
    # Cookies не привязаны к конкретному предмету — разогреваем сессию, только
    # если её ещё не было или она протухла, а не перед каждым запросом цены.
    _client.cookies.jar.clear_expired_cookies()
    if len(_client.cookies) > 0:
        return

    listing_url = f"{STEAM_MARKET_LISTING_URL}/{app_id}/{market_hash_name}"
    response = _get_with_backoff(listing_url, {})
    response.raise_for_status()


def fetch_price_overview(
    app_id: int,
    market_hash_name: str,
    currency: int = USD_CURRENCY_CODE,
) -> PriceOverview:
    _ensure_session_warmed_up(app_id, market_hash_name)

    listing_url = f"{STEAM_MARKET_LISTING_URL}/{app_id}/{market_hash_name}"
    referer_headers = {"Referer": listing_url}
    params: dict[str, str | int] = {
        "appid": app_id,
        "market_hash_name": market_hash_name,
        "currency": currency,
    }
    response = _get_with_backoff(
        STEAM_MARKET_PRICEOVERVIEW_URL, params, extra_headers=referer_headers
    )

    if response.status_code == 429:
        # Cookies формально не протухли (иначе _ensure_session_warmed_up уже бы
        # их обновила), но Steam всё равно режет — считаем сессию недействительной,
        # принудительно обновляем её и пробуем ровно один раз ещё.
        _client.cookies.clear()
        _ensure_session_warmed_up(app_id, market_hash_name)
        response = _get_with_backoff(
            STEAM_MARKET_PRICEOVERVIEW_URL, params, extra_headers=referer_headers
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


def fetch_usd_rub_rate(app_id: int, market_hash_name: str) -> Decimal:
    """Текущий курс USD/RUB (сколько рублей за доллар), вычисленный по цене
    одного и того же предмета в обеих валютах через priceoverview — анонимно,
    без cookie. Используется, чтобы конвертировать рублёвые price_snapshots
    в доллары в момент анализа, а не при сборе (собираем и храним всё
    в рублях — валюте, в которой Steam реально отдаёт данные аккаунту)."""
    usd_overview = fetch_price_overview(app_id, market_hash_name, currency=USD_CURRENCY_CODE)
    rub_overview = fetch_price_overview(app_id, market_hash_name, currency=RUB_CURRENCY_CODE)
    if usd_overview.price == 0:
        raise SteamMarketError(
            f"Не удалось вычислить курс USD/RUB по {market_hash_name!r}: цена в USD равна нулю"
        )
    return rub_overview.price / usd_overview.price


MAX_RETRIES_ON_STALE_BATCH = 3


def _parse_market_search_item(raw_item: dict[str, Any]) -> MarketSearchItem:
    try:
        return MarketSearchItem(
            hash_name=str(raw_item["hash_name"]),
            sell_listings=int(raw_item.get("sell_listings", 0)),
            sell_price=Decimal(raw_item["sell_price"]) / 100,
        )
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise SteamMarketError(
            f"Не удалось разобрать предмет из результатов поиска: {raw_item!r}"
        ) from exc


def fetch_market_items(
    app_id: int,
    total: int,
    request_delay_seconds: float = 8.0,
    sort_column: str = "quantity",
    sort_dir: str = "desc",
) -> list[MarketSearchItem]:
    search_page_url = f"{STEAM_MARKET_SEARCH_URL}?appid={app_id}"
    referer_headers = {
        "Referer": search_page_url,
        "X-Requested-With": "XMLHttpRequest",
    }

    # Разогрев: обычный заход на страницу поиска даёт анонимные cookies,
    # без которых Steam режет запросы к search/render как подозрительные.
    warmup_response = _get_with_backoff(STEAM_MARKET_SEARCH_URL, {"appid": app_id})
    warmup_response.raise_for_status()

    items: list[MarketSearchItem] = []
    seen_hash_names: set[str] = set()
    start = 0
    stale_retries = 0

    while len(items) < total:
        # count — сколько предметов мы просим. Steam его не соблюдает: проверено
        # живьём на count=1 и count=100 — оба раза в ответе pagesize=10, то есть
        # реальный размер страницы Steam выбирает сам. Поэтому ниже используется
        # не count, а len(results) — то, что реально пришло.
        response = _get_with_backoff(
            STEAM_MARKET_SEARCH_RENDER_URL,
            {
                "query": "",
                "appid": app_id,
                "norender": 1,
                "count": total - len(items),
                "start": start,
                "sort_column": sort_column,
                "sort_dir": sort_dir,
            },
            extra_headers=referer_headers,
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            raise SteamMarketError(f"Steam не вернул результаты поиска (appid={app_id})")

        results = data.get("results", [])
        if not results:
            break

        try:
            new_results = [r for r in results if r["hash_name"] not in seen_hash_names]
        except KeyError as exc:
            raise SteamMarketError(
                f"В результатах поиска нет поля {exc} (appid={app_id})"
            ) from exc

        # Steam иногда отдаёт ту же страницу повторно (внутреннее кэширование
        # на его стороне) даже при другом start. Если вся пачка уже видена —
        # это не новые данные, а "залипшая" страница: ждём и пробуем тот же start снова.
        if not new_results:
            stale_retries += 1
            if stale_retries > MAX_RETRIES_ON_STALE_BATCH:
                raise SteamMarketError(
                    f"Steam повторяет одну и ту же страницу поиска (start={start}) "
                    f"после {MAX_RETRIES_ON_STALE_BATCH} повторных попыток"
                )
            time.sleep(request_delay_seconds)
            continue

        stale_retries = 0
        for raw_item in new_results:
            if len(items) >= total:
                break
            item = _parse_market_search_item(raw_item)
            seen_hash_names.add(item.hash_name)
            items.append(item)

        # Продвигаем позицию по реально полученному количеству, а не по count —
        # иначе следующий запрос частично пересечётся с уже обработанной страницей.
        start += len(results)
        if len(items) < total:
            time.sleep(request_delay_seconds)

    return items


# item_nameid Steam нигде не публикует официально. До 2026 года его можно
# было вытащить регуляркой из HTML страницы листинга (Market_LoadOrderSpread),
# но с переходом Steam на клиентский рендеринг (React/Next.js SSR) эта
# разметка из ответа сервера пропала — проверено вживую 17.07.2026, ни в HTML,
# ни в статических JS-чанках страницы nameid больше нет. Единственный
# оставшийся способ без полноценного управляемого браузера с логином в
# аккаунт (см. обсуждение в чате, вариант через Puppeteer — тяжелее и больше
# похоже на "агрессивный скрапер", чем хотелось бы) — сторонний, вручную
# поддерживаемый датасет market_hash_name→item_nameid. Тем же самым
# датасетом, как выяснилось, пользуется и автор референс-репозитория,
# который мы разбирали 17.07.2026.
STEAM_ITEM_NAME_IDS_BASE_URL = (
    "https://raw.githubusercontent.com/somespecialone/steam-item-name-ids/master/data"
)

# appid -> папка датасета. Пока только CS2 — единственная игра, которую
# отслеживает проект (docs/SCOPE.md). Добавлять новые игры сюда по мере
# необходимости, не заранее.
_APP_ID_TO_DATASET_FOLDER: dict[int, str] = {730: "CS2"}

# Кэш в памяти процесса: датасет — несколько МБ, скачивать его на каждый
# резолвинг смысла нет. Уже резолвленные item_nameid и так кэшируются в БД
# (Item.item_nameid) вызывающим кодом — сюда попадаем только за НОВЫМ
# предметом, который встретился впервые, и то один раз за время жизни
# процесса, а не на каждый такой случай.
_item_nameid_dataset_cache: dict[int, dict[str, int]] = {}


def _load_item_nameid_dataset(app_id: int) -> dict[str, int]:
    if app_id in _item_nameid_dataset_cache:
        return _item_nameid_dataset_cache[app_id]

    folder = _APP_ID_TO_DATASET_FOLDER.get(app_id)
    if folder is None:
        raise SteamMarketError(
            f"Нет датасета item_nameid для appid={app_id} — поддерживается только CS2 (730)"
        )

    url = f"{STEAM_ITEM_NAME_IDS_BASE_URL}/{folder}/item_names.json"
    # trust_env=False — ОБЯЗАТЕЛЬНО, ровно по той же причине, по которой он
    # стоит у модульного _client: на Windows httpx через trust_env читает
    # системный прокси из реестра, а он отдаётся под некорректной схемой
    # (socks4://...), которую httpx не принимает и падает с ValueError
    # "Unknown scheme for proxy URL" ещё до сети. Баг найден Claude Code
    # при повторной проверке 17.07.2026: одноразовый httpx.get здесь
    # обходил _client и вместе с ним терял этот флаг. Датасет живёт на
    # GitHub, не на Steam, и в STEAM_PROXY_URL не нуждается — поэтому просто
    # ходим напрямую, без всякого прокси.
    response = httpx.get(url, timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False)
    response.raise_for_status()
    dataset: dict[str, int] = response.json()

    _item_nameid_dataset_cache[app_id] = dataset
    return dataset


def resolve_item_nameid(app_id: int, market_hash_name: str) -> int:
    """Резолвинг item_nameid через сторонний датасет (см. комментарий выше).
    Результат кэшируется вызывающим кодом (Item.item_nameid в БД), чтобы
    датасет не перечитывался на каждый цикл сбора — только на первую
    встречу нового предмета.

    ВАЖНО: датасет неполный (его же README прямо предупреждает "Does not
    contain all items") — если предмета там нет, кидаем понятную ошибку,
    а не падаем необъяснимо. Это НЕ запрос к steamcommunity.com — не идёт
    через _get_with_backoff и не расходует лимит запросов к Steam."""
    dataset = _load_item_nameid_dataset(app_id)
    nameid = dataset.get(market_hash_name)
    if nameid is None:
        raise SteamMarketError(
            f"item_nameid для {market_hash_name!r} не найден в датасете "
            f"(appid={app_id}) — предмета там может не быть, датасет неполный"
        )
    return nameid


def fetch_order_histogram(
    item_nameid: int,
    currency: int = USD_CURRENCY_CODE,
) -> OrderHistogram:
    """itemordershistogram — в отличие от priceoverview, не требует
    предварительного разогрева анонимной сессии (cookies/Referer) и
    переносит заметно бОльшую частоту запросов, не отдавая 429 там, где
    priceoverview уже режет (см. разбор в чате от 17.07.2026). Отдаёт сразу
    обе стороны книги ордеров одним запросом — реальную текущую цену продажи
    и реальную текущую цену покупки, а не приближение через историческую
    медиану, как раньше."""
    params: dict[str, str | int] = {
        "country": "US",
        "language": "english",
        "currency": currency,
        "item_nameid": item_nameid,
    }
    response = _get_with_backoff(STEAM_MARKET_ORDER_HISTOGRAM_URL, params)
    response.raise_for_status()
    data = response.json()

    if (
        not data.get("success")
        or not data.get("lowest_sell_order")
        or not data.get("highest_buy_order")
    ):
        raise SteamMarketError(
            f"Steam не вернул order histogram для item_nameid={item_nameid}"
        )

    return OrderHistogram(
        lowest_sell_order=Decimal(str(data["lowest_sell_order"])) / 100,
        highest_buy_order=Decimal(str(data["highest_buy_order"])) / 100,
        sell_order_count=_parse_volume(data.get("sell_order_count")),
        buy_order_count=_parse_volume(data.get("buy_order_count")),
    )
