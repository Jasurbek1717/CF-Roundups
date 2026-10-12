"""Мок Algolia: каталог товаров и фальшивый requests.post (сеть не используется)."""
import re

def _to_unix(s):
    """'YYYY-MM-DD' -> unix (UTC); чистые числа проходят как есть."""
    if isinstance(s, str) and not s.isdigit():
        import datetime as _dt
        return int(_dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc).timestamp())
    return int(s)

def make_hit(i, **kw):
    h = {"objectID": f"p{i}", "name_en": f"Cat Design {i}", "url": f"/product/cat-design-{i}/",
         "image": f"https://cdn.example.com/img/{i}.jpg", "popularity": float(1000 - i),
         "date": 1_700_000_000 + i * 86400, "isFree": i % 5 == 0, "price": float(i % 7),
         "type": "Graphics", "category": ["Cricut", "SVG"], "tags": ["cat", "svg"],
         "designer": {"designerName": "Studio", "designerUrl": "/designer/x/"}}
    h.update(kw)
    return h

CATALOG = {
    "cat": [make_hit(i) for i in range(1, 251)],                       # 250 товаров
    "dog": [make_hit(i, name_en=f"Dog Design {i}") for i in range(200, 230)],  # 200..229 пересекаются с cat
}

class FakeResp:
    def __init__(self, payload, status=200):
        self.status_code, self._p, self.text = status, payload, str(payload)[:200]
    def json(self):
        return self._p

class FakeAlgolia:
    def __init__(self, status=200, fail_first_hosts=0):
        self.calls, self.status, self.fail_first_hosts = [], status, fail_first_hosts
    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.status != 200:
            return FakeResp({}, self.status)
        if len(self.calls) <= self.fail_first_hosts:
            return FakeResp({}, 503)
        out = []
        for rq in json["requests"]:
            if "facets" in rq:
                out.append({"hits": [], "nbHits": 0, "facets": {"category": {"Cricut": 90, "SVG": 55}}})
                continue
            items = CATALOG.get((rq.get("query") or "").strip('"').lower(), [])
            if rq.get("query", "") == "":
                items = CATALOG["cat"]
            # строковая фильтрация как у Algolia: type/category/isFree + числовые date>=/date<=
            f = (rq.get("filters") or "").replace('""', "\x00")
            m = re.search(r'type:"([^"]+)"', f)
            if m:
                items = [h for h in items if h["type"] == m.group(1).replace("\x00", '"')]
            m = re.search(r'isFree:(true|false)', f)
            if m:
                items = [h for h in items if h["isFree"] == (m.group(1) == "true")]
            m = re.search(r'date<=(\d+)', f)
            if m:
                hi = _to_unix(m.group(1))
                items = [h for h in items if h["date"] <= hi]
            m = re.search(r'date>=(\d+)', f)
            if m:
                lo = _to_unix(m.group(1))
                items = [h for h in items if h["date"] >= lo]
            n = rq.get("hitsPerPage", 20); pg = rq.get("page", 0)
            chunk = items[pg * n:(pg + 1) * n]
            nb_pages = max(1, -(-len(items) // n)) if n else 1
            out.append({"hits": chunk, "nbHits": len(items), "page": pg, "nbPages": nb_pages})
        return FakeResp({"results": out})
