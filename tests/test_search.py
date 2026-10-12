import pytest
import cf_search as cs
from fakes import FakeAlgolia, FakeResp

CFG = cs.Config("APPID123", "SEARCHKEY", "idx_main", "")

@pytest.fixture
def fake(monkeypatch):
    f = FakeAlgolia()
    monkeypatch.setattr(cs.requests, "post", f)
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    return f

def sp(**kw):
    base = dict(queries=("cat",), type_="Graphics", target=10)
    base.update(kw)
    return cs.SearchParams(**base)

# --- параметры -------------------------------------------------------------
def test_filters_quote_and_escape():
    assert cs.build_filters("Graphics", ("Cricut", 'A "B"'), "paid") == \
        'type:"Graphics" AND (category:"Cricut" OR category:"A \\"B\\"") AND isFree:false'
    assert cs.build_filters("Fonts", (), "free").endswith("isFree:true")
    assert cs.build_filters("Fonts") == 'type:"Fonts"'

def test_numeric_and_params():
    assert cs.build_numeric(10, 20) == ["date>=10", "date<=20"]
    p = cs.build_params('"exact phrase"', "", [], 0, 20, strict=True)
    assert p["advancedSyntax"] is True and p["typoTolerance"] is False and p["analytics"] is False
    assert "advancedSyntax" not in cs.build_params("plain", "", [], 0, 20, strict=False)

def test_requests_are_honest_no_spoofed_headers(fake):
    cs.search(CFG, sp())
    h = {k.lower(): v for k, v in fake.calls[0]["headers"].items()}
    assert "referer" not in h and "origin" not in h
    assert h["user-agent"].startswith("CFRoundupExporter")
    assert fake.calls[0]["url"].startswith("https://appid123-dsn.algolia.net")

# --- нормализация: ничего не выдумывается ------------------------------------
def test_normalize_no_invented_fields():
    h = {"objectID": "a", "name_en": " T ", "url": "/product/t/", "image": "//c.x/i.jpg", "price": 3,
         "rating": 4.5, "description": "Real text"}
    p = cs.normalize_hit(h)
    assert p["url"] == "https://www.creativefabrica.com/product/t/" and p["image"] == "https://c.x/i.jpg"
    assert p["rating"] is None and p["reviews"] is None and p["description"] == ""   # без явного маппинга
    p2 = cs.normalize_hit(h, {"rating": "rating", "description": "description"})
    assert p2["rating"] == 4.5 and p2["description"] == "Real text"

# --- поиск -------------------------------------------------------------------
def test_target_pagination_and_no_duplicates(fake):
    r = cs.search(CFG, sp(target=150))
    ids = [p["id"] for p in r.products]
    assert len(ids) == 150 and len(set(ids)) == 150
    assert r.requests_made == 2                       # 2 страницы по 100, без лишних запросов

def test_dedupe_across_queries(fake):
    r = cs.search(CFG, sp(queries=("cat", "dog"), target=230))
    ids = [p["id"] for p in r.products]
    assert len(ids) == len(set(ids)) and len(ids) == 230
    assert r.duplicates > 0

def test_same_query_twice_is_collapsed(fake):
    r = cs.search(CFG, sp(queries=("cat", " CAT ")))
    assert len(fake.calls[0]["json"]["requests"]) == 1

def test_popularity_sort_local_and_exact_when_complete(fake):
    r = cs.search(CFG, sp(queries=("dog",), sort="popularity", target=5))
    pops = [p["popularity"] for p in r.products]
    assert pops == sorted(pops, reverse=True) and r.exact_sort is True      # весь каталог (30) получен

def test_popularity_sort_is_marked_approximate_when_catalog_larger_than_pool(fake, monkeypatch):
    monkeypatch.setattr(cs, "MAX_PAGES", 1)
    r = cs.search(CFG, sp(sort="date", target=5))
    assert r.exact_sort is False and any("приближение" in n for n in r.notes)

def test_replica_index_used_for_popularity(fake):
    cfg = cs.Config("APPID123", "K", "idx_main", "idx_pop")
    r = cs.search(cfg, sp(sort="popularity", target=5))
    assert fake.calls[0]["json"]["requests"][0]["indexName"] == "idx_pop" and r.exact_sort

def test_local_filters(fake):
    r = cs.search(CFG, sp(queries=("dog",), min_popularity=800, target=50))
    assert all(p["popularity"] >= 800 for p in r.products) and r.filtered_out > 0

def test_validation_errors(fake):
    with pytest.raises(cs.CFError):
        cs.search(CFG, sp(queries=("  ",)))
    with pytest.raises(cs.CFError):
        cs.search(CFG, sp(type_=""))
    with pytest.raises(cs.CFError):
        cs.search(cs.Config("", ""), sp())

# --- ошибки и ограничения доступа ------------------------------------------------
def test_403_stops_immediately_no_workarounds(monkeypatch):
    f = FakeAlgolia(status=403)
    monkeypatch.setattr(cs.requests, "post", f)
    with pytest.raises(cs.AccessDenied):
        cs.search(CFG, sp())
    assert len(f.calls) == 1                                   # без повторов и других хостов

def test_429_retries_then_succeeds(monkeypatch):
    seq = [FakeResp({}, 429), FakeAlgolia()]
    inner = FakeAlgolia()
    calls = {"n": 0}
    def post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResp({}, 429) if calls["n"] == 1 else inner(url, json, headers, timeout)
    monkeypatch.setattr(cs.requests, "post", post)
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    assert cs.search(CFG, sp()).products and calls["n"] >= 2

def test_5xx_falls_back_to_next_host(monkeypatch):
    f = FakeAlgolia(fail_first_hosts=1)
    monkeypatch.setattr(cs.requests, "post", f)
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    assert cs.search(CFG, sp()).products
    assert f.calls[0]["url"] != f.calls[1]["url"]

def test_categories_and_diagnose(fake):
    assert cs.list_categories(CFG, "Graphics") == [("Cricut", 90), ("SVG", 55)]
    checks, sample = cs.diagnose(CFG, "Graphics")
    assert checks[0]["статус"] == "ok" and sample["objectID"] == "p1"
    keys = {r["поле"] for r in cs.describe_keys(sample)}
    assert {"name_en", "popularity", "category"} <= keys
