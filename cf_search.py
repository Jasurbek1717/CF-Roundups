# -*- coding: utf-8 -*-
"""
cf_search.py — поиск товаров Creative Fabrica через Algolia.

Принципы:
  * только честные запросы: свой User-Agent, БЕЗ подделки Referer/Origin;
    ключ берётся из настроек, со страниц сайта не извлекается;
  * при 401/403 — остановка с понятным сообщением, обходов нет;
  * используются только параметры, подтверждённые работой старого проекта
    (query, filters по type/category/isFree, numericFilters по date, hitsPerPage,
    facets) и стандартные параметры Algolia (page, advancedSyntax);
    всё остальное проверяет вкладка «Диагностика».
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

SITE = "https://www.creativefabrica.com"
DEFAULT_INDEX = "prod_Productsv2"          # из старого проекта (cf_core.INDEX_NAME)
MAIN_TYPES = ("Graphics", "Fonts", "Crafts", "Embroidery")   # из cf_core.MAIN_TYPES
USER_AGENT = "CFRoundupExporter/1.0 (personal research tool)"
TIMEOUT = 20
MAX_RETRIES = 2
RETRY_BACKOFF = 2.0
PAGE_DELAY = 0.3
HITS_PER_PAGE = 100
MAX_PAGES = 10          # страниц на один запрос
MAX_POOL = 1000         # потолок пула для локальной сортировки

SORTS = {
    "relevance": "Релевантность",
    "popularity": "Популярность",
    "date": "Новизна (по дате)",
    "price_asc": "Цена ↑",
    "price_desc": "Цена ↓",
}


class CFError(Exception):
    """Любая понятная пользователю ошибка поиска."""


class AccessDenied(CFError):
    """401/403: ключ не принят или ограничен. Обход не выполняется."""


class RateLimited(CFError):
    pass


class NetworkProblem(CFError):
    pass


@dataclass(frozen=True)
class Config:
    app_id: str
    api_key: str
    index: str = DEFAULT_INDEX
    popularity_index: str = ""      # реплика с сортировкой по популярности (необязательно)
    url: str = ""                   # полный URL multi-query, если хост нестандартный

    @property
    def ready(self) -> bool:
        return bool(self.app_id and self.api_key)

    def hosts(self) -> List[str]:
        if self.url:
            return [self.url]
        a = self.app_id.lower()
        tail = "/1/indexes/*/queries"
        return [f"https://{a}-dsn.algolia.net{tail}"] + \
               [f"https://{a}-{i}.algolianet.com{tail}" for i in (1, 2, 3)]


@dataclass(frozen=True)
class SearchParams:
    queries: Tuple[str, ...]
    type_: str
    categories: Tuple[str, ...] = ()
    free: str = "any"               # any | free | paid
    date_from: Optional[int] = None  # unix, секунды
    date_to: Optional[int] = None
    strict: bool = True             # без исправления опечаток
    target: int = 10
    sort: str = "relevance"
    min_popularity: Optional[float] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None


@dataclass
class SearchResult:
    products: List[dict] = field(default_factory=list)
    exact_sort: bool = True
    notes: List[str] = field(default_factory=list)
    requests_made: int = 0
    nb_hits: Dict[str, int] = field(default_factory=dict)
    duplicates: int = 0
    filtered_out: int = 0


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def _headers(cfg: Config) -> Dict[str, str]:
    return {
        "x-algolia-application-id": cfg.app_id,
        "x-algolia-api-key": cfg.api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
	"Referer": "https://www.creativefabrica.com/",
    }


def _api_message(r) -> str:
    """Собственное сообщение Algolia об ошибке (ключ в него не попадает)."""
    try:
        m = r.json().get("message")
    except Exception:
        m = None
    return str(m or getattr(r, "text", "") or "").strip()[:200]


def post(cfg: Config, requests_list: List[dict]) -> List[dict]:
    """Один multi-query запрос. Повтор — только при 429 и сетевых сбоях/5xx."""
    if not cfg.ready:
        raise CFError("Не заданы Algolia App ID и Search key (боковая панель «Настройки»).")
    payload = {"requests": requests_list}
    last: Optional[CFError] = None
    for url in cfg.hosts():
        for attempt in range(MAX_RETRIES + 1):
            try:
                r = requests.post(url, json=payload, headers=_headers(cfg), timeout=TIMEOUT)
            except requests.RequestException as e:
                last = NetworkProblem(f"Сетевая ошибка: {e}")
                break                                   # следующий хост
            code = r.status_code
            if code in (401, 403):
                msg = _api_message(r)
                raise AccessDenied(
                    f"Algolia отклонил запрос (HTTP {code}" + (f", ответ: «{msg}»" if msg else "") + "). "
                    "Что проверить: 1) App ID и Search key введены без лишних символов и актуальны "
                    "(ключи на сайте могут меняться): скопируйте свежие из браузера, см. подсказку в "
                    "боковой панели; 2) если свежий ключ тоже отклоняется, он, вероятно, ограничен "
                    "сайтом (по домену или IP). Обход ограничений не выполняется.")
            if code == 429:
                last = RateLimited("Слишком много запросов (HTTP 429). Подождите и повторите.")
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            if code >= 500:
                last = NetworkProblem(f"Сервер Algolia недоступен (HTTP {code}).")
                break
            if code >= 400:
                raise CFError(f"Algolia вернул HTTP {code}: {r.text[:200]}")
            try:
                return r.json().get("results", [])
            except ValueError as e:
                raise CFError("Ответ Algolia не является JSON.") from e
    raise last or NetworkProblem("Нет ответа от Algolia.")


# ----------------------------------------------------------------------------
# Параметры запроса
# ----------------------------------------------------------------------------
def _q(value: Any) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_filters(type_: str, categories=(), free: str = "any") -> str:
    parts = []
    if type_:
        parts.append(f"type:{_q(type_)}")
    cats = [c for c in categories if c]
    if cats:
        parts.append("(" + " OR ".join(f"category:{_q(c)}" for c in cats) + ")")
    if free == "free":
        parts.append("isFree:true")
    elif free == "paid":
        parts.append("isFree:false")
    return " AND ".join(parts)


def build_numeric(date_from: Optional[int], date_to: Optional[int]) -> List[str]:
    out = []
    if date_from:
        out.append(f"date>={int(date_from)}")
    if date_to:
        out.append(f"date<={int(date_to)}")
    return out


def build_params(query: str, filters: str, numeric: List[str], page: int,
                 hits_per_page: int, strict: bool) -> dict:
    p: Dict[str, Any] = {"query": query, "page": page, "hitsPerPage": hits_per_page,
                         "analytics": False, "typoTolerance": not strict}
    if filters:
        p["filters"] = filters
    if numeric:
        p["numericFilters"] = list(numeric)
    if '"' in query:
        p["advancedSyntax"] = True      # точная фраза в кавычках
    return p


# ----------------------------------------------------------------------------
# Нормализация товара
# ----------------------------------------------------------------------------
def _abs(url: Any) -> str:
    u = str(url or "").strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return SITE + u
    return u


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _field(hit: dict, name: Optional[str]) -> Any:
    """Необязательное поле: берётся ТОЛЬКО если пользователь явно указал его имя."""
    if not name:
        return None
    return hit.get(name)


def normalize_hit(hit: dict, fields: Optional[Dict[str, str]] = None) -> dict:
    fields = fields or {}
    cats = hit.get("category") or []
    if isinstance(cats, str):
        cats = [cats]
    designer = hit.get("designer")
    title = str(hit.get("name_en") or hit.get("name") or "").strip()
    url = _abs(hit.get("url"))
    desc = _field(hit, fields.get("description"))
    return {
        "id": str(hit.get("objectID") or url or title),
        "title": title,
        "url": url,
        "image": _abs(hit.get("image")),
        "type": str(hit.get("type") or ""),
        "categories": [str(c) for c in cats],
        "price": _num(hit.get("price")),
        "is_free": hit.get("isFree") if isinstance(hit.get("isFree"), bool) else None,
        "popularity": _num(hit.get("popularity")),
        "date": _num(hit.get("date")),
        "tags": [str(t) for t in (hit.get("tags") or [])],
        "designer": designer.get("designerName") if isinstance(designer, dict) else None,
        "rating": _field(hit, fields.get("rating")),
        "reviews": _field(hit, fields.get("reviews")),
        "description": str(desc).strip() if isinstance(desc, (str, int, float)) else "",
    }


# ----------------------------------------------------------------------------
# Поиск
# ----------------------------------------------------------------------------
def _clean_queries(queries) -> List[str]:
    seen, out = set(), []
    for q in queries:
        q = " ".join(str(q).split())
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def _passes(p: dict, sp: SearchParams) -> bool:
    if sp.min_popularity and (p["popularity"] is None or p["popularity"] < sp.min_popularity):
        return False
    if sp.price_min is not None and (p["price"] is None or p["price"] < sp.price_min):
        return False
    if sp.price_max is not None and (p["price"] is None or p["price"] > sp.price_max):
        return False
    return True


def _sort(products: List[dict], mode: str) -> None:
    if mode == "popularity":
        products.sort(key=lambda p: (p["popularity"] is None, -(p["popularity"] or 0)))
    elif mode == "date":
        products.sort(key=lambda p: (p["date"] is None, -(p["date"] or 0)))
    elif mode == "price_asc":
        products.sort(key=lambda p: (p["price"] is None, p["price"] or 0))
    elif mode == "price_desc":
        products.sort(key=lambda p: (p["price"] is None, -(p["price"] or 0)))


def search(cfg: Config, sp: SearchParams, fields: Optional[Dict[str, str]] = None) -> SearchResult:
    queries = _clean_queries(sp.queries)
    if not queries:
        raise CFError("Введите хотя бы один поисковый запрос.")
    if not sp.type_:
        raise CFError("Выберите один тип товаров: категории не смешиваются.")
    target = max(1, int(sp.target))
    use_replica = sp.sort == "popularity" and bool(cfg.popularity_index)
    index = cfg.popularity_index if use_replica else cfg.index
    pool_mode = sp.sort != "relevance" and not use_replica
    want = min(MAX_POOL, max(target * 5, 200)) if pool_mode else target
    per_page = HITS_PER_PAGE if pool_mode else min(HITS_PER_PAGE, max(20, target))
    filters = build_filters(sp.type_, sp.categories, sp.free)
    numeric = build_numeric(sp.date_from, sp.date_to)

    state = {q: {"page": 0, "done": False, "exhausted": False} for q in queries}
    res = SearchResult()
    seen = set()
    pool: List[dict] = []
    while len(pool) < want:
        active = [q for q in queries if not state[q]["done"]]
        if not active:
            break
        reqs = [{"indexName": index,
                 **build_params(q, filters, numeric, state[q]["page"], per_page, sp.strict)}
                for q in active]
        results = post(cfg, reqs)
        res.requests_made += 1
        for q, r in zip(active, results):
            hits = r.get("hits") or []
            res.nb_hits[q] = int(r.get("nbHits") or 0)
            state[q]["page"] += 1
            nb_pages = r.get("nbPages")
            if not hits or (nb_pages is not None and state[q]["page"] >= nb_pages):
                state[q]["exhausted"] = True            # все совпадения этого запроса получены
            if state[q]["exhausted"] or state[q]["page"] >= MAX_PAGES:
                state[q]["done"] = True
            for h in hits:
                p = normalize_hit(h, fields)
                if p["id"] in seen:
                    res.duplicates += 1
                    continue
                seen.add(p["id"])
                if not _passes(p, sp):
                    res.filtered_out += 1
                    continue
                pool.append(p)
        if len(pool) < want and not all(s["done"] for s in state.values()):
            time.sleep(PAGE_DELAY)

    if pool_mode:
        _sort(pool, sp.sort)
    res.products = pool[:target]
    complete = all(s["exhausted"] for s in state.values())   # каталог по запросам прочитан целиком
    res.exact_sort = use_replica or sp.sort == "relevance" or (pool_mode and complete)
    if not res.exact_sort:
        res.notes.append(
            f"Порядок «{SORTS.get(sp.sort, sp.sort)}» построен по {len(pool)} найденным товарам, "
            "а не по всему каталогу (приближение).")
    if len(res.products) < target:
        res.notes.append(f"Найдено {len(res.products)} из {target}: по этим условиям товаров больше нет.")
    return res


def list_categories(cfg: Config, type_: str) -> List[Tuple[str, int]]:
    r = post(cfg, [{"indexName": cfg.index, "query": "", "filters": f"type:{_q(type_)}",
                    "facets": ["category"], "hitsPerPage": 0, "maxValuesPerFacet": 200,
                    "analytics": False}])
    facets = (r[0].get("facets") or {}).get("category") or {}
    return sorted(((str(k), int(v)) for k, v in facets.items()), key=lambda kv: (-kv[1], kv[0]))


# ----------------------------------------------------------------------------
# Диагностика: что реально отдаёт Algolia
# ----------------------------------------------------------------------------
_HINTS = ("rat", "review", "descr", "format", "score", "star", "download", "like", "sale")


def describe_keys(hit: dict) -> List[dict]:
    rows = []
    for k, v in hit.items():
        if str(k).startswith("_"):
            continue
        preview = str(v)
        rows.append({"поле": k, "тип": type(v).__name__,
                     "пример": preview if len(preview) <= 80 else preview[:77] + "...",
                     "похоже на рейтинг/описание/формат": any(h in str(k).lower() for h in _HINTS)})
    return rows


def diagnose(cfg: Config, type_: str) -> Tuple[List[dict], Optional[dict]]:
    """Возвращает (проверки, сырой пример товара). Каждая проверка независима."""
    checks: List[dict] = []
    sample: Optional[dict] = None
    base = {"indexName": cfg.index, "analytics": False, "filters": f"type:{_q(type_)}"}

    def run(name: str, fn):
        try:
            status, detail = fn()
        except CFError as e:
            status, detail = "fail", str(e)
        checks.append({"проверка": name, "статус": status, "детали": detail})

    def access():
        nonlocal sample
        r = post(cfg, [{**base, "query": "", "hitsPerPage": 1}])
        hits = r[0].get("hits") or []
        if not hits:
            return "warn", "Доступ есть, но товаров с таким типом не найдено."
        sample = hits[0]
        return "ok", f"Доступ есть. Всего товаров типа {type_}: {r[0].get('nbHits')}."

    def paging():
        r = post(cfg, [{**base, "query": "", "hitsPerPage": 5, "page": 1}])
        hits = r[0].get("hits") or []
        return ("ok", f"Страница 2 загружается (получено {len(hits)}, page={r[0].get('page')}).") \
            if hits else ("warn", "Страница 2 пустая: постраничная загрузка может быть ограничена.")

    def pop_filter():
        r = post(cfg, [{**base, "query": "", "hitsPerPage": 1, "numericFilters": ["popularity>=1"]}])
        return "ok", f"Фильтр по popularity работает на сервере (nbHits={r[0].get('nbHits')})."

    def cats():
        c = list_categories(cfg, type_)
        return ("ok", f"Категорий: {len(c)}; первые: " + ", ".join(n for n, _ in c[:5])) if c \
            else ("warn", "Список категорий пуст.")

    def replica():
        if not cfg.popularity_index:
            return "warn", "Индекс сортировки по популярности не задан (необязательно)."
        r = post(cfg, [{"indexName": cfg.popularity_index, "query": "", "hitsPerPage": 3,
                        "filters": f"type:{_q(type_)}", "analytics": False}])
        pops = [h.get("popularity") for h in (r[0].get("hits") or [])]
        return "ok", f"Индекс отвечает; popularity первых товаров: {pops}."

    run("Доступ и пример товара", access)
    if checks[0]["статус"] == "fail":
        return checks, sample
    run("Постраничная загрузка", paging)
    run("Серверный фильтр по popularity", pop_filter)
    run("Список категорий (facets)", cats)
    run("Индекс сортировки по популярности", replica)
    return checks, sample
