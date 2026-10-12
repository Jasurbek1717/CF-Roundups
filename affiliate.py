# -*- coding: utf-8 -*-
"""
affiliate.py — партнёрские ссылки Creative Fabrica.

Формат ссылок CF публично не документирован, поэтому он НЕ угадывается:
  * без образца ссылки товар экспортируется как есть (обычный URL с сайта);
  * если задан образец реальной партнёрской ссылки, формат выводится из него
    и проверяется обратной сборкой (ссылка из обычного URL должна совпасть с образцом).

Распознаются три варианта расположения ID:
  prefix:  https://www.creativefabrica.com/ref/<ID>/product/<slug>/
  suffix:  https://www.creativefabrica.com/product/<slug>/ref/<ID>/?sharedfrom=pdp
  query:   https://www.creativefabrica.com/product/<slug>/?ref=<ID>
"""
from __future__ import annotations

from typing import List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CF_DOMAIN = "creativefabrica.com"
REF_QUERY_KEYS = ("ref", "aff", "affiliate", "affiliate_id", "aff_id", "partner")


def is_cf_url(url: str) -> bool:
    host = (urlsplit(str(url or "").strip()).netloc or "").lower()
    return host == CF_DOMAIN or host.endswith("." + CF_DOMAIN)


def _split_ref(path: str) -> Tuple[str, Optional[str], str]:
    """-> (путь без /ref/<ID>, ID или None, 'prefix'|'suffix'|'')."""
    segs = path.split("/")
    for i, s in enumerate(segs):
        if s == "ref" and i + 1 < len(segs) and segs[i + 1]:
            rest = segs[:i] + segs[i + 2:]
            kind = "prefix" if i == 1 else "suffix"
            clean = "/".join(rest) or "/"
            return (clean if clean.startswith("/") else "/" + clean), segs[i + 1], kind
    return path, None, ""


def plain_url(url: str) -> str:
    """Обычная ссылка без партнёрских меток (для обратной сборки образца)."""
    p = urlsplit(url.strip())
    path, _, _ = _split_ref(p.path or "/")
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in REF_QUERY_KEYS]
    return urlunsplit((p.scheme or "https", p.netloc, path, urlencode(q), ""))


def parse_sample(sample: Optional[str]) -> Optional[dict]:
    """Формат из образца или None, если не распознан / обратная сборка не совпала."""
    if not sample or not is_cf_url(sample):
        return None
    p = urlsplit(sample.strip())
    _, ref_id, kind = _split_ref(p.path or "/")
    pairs = parse_qsl(p.query, keep_blank_values=True)
    if ref_id:
        spec = {"kind": kind, "id": ref_id, "key": "",
                "extra": [(k, v) for k, v in pairs if k.lower() not in REF_QUERY_KEYS]}
    else:
        found = [(k, v) for k, v in pairs if k.lower() in REF_QUERY_KEYS and v]
        if not found:
            return None
        key, val = found[0]
        spec = {"kind": "query", "id": val, "key": key,
                "extra": [(k, v) for k, v in pairs if k.lower() not in REF_QUERY_KEYS]}
    rebuilt = build(plain_url(sample), spec)
    return spec if _same(rebuilt, sample) else None


def _norm(u: str):
    p = urlsplit(u.strip())
    return (p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), sorted(parse_qsl(p.query, keep_blank_values=True)))


def _same(a: str, b: str) -> bool:
    return _norm(a) == _norm(b)


def build(url: str, spec: Optional[dict]) -> str:
    """Партнёрская ссылка по подтверждённому формату; без формата — исходный URL."""
    url = str(url or "").strip()
    if not spec or not url or not is_cf_url(url):
        return url
    p = urlsplit(url)
    path, existing_id, _ = _split_ref(p.path or "/")
    if existing_id:                                  # уже партнёрская — не дублируем
        return url
    query: List[Tuple[str, str]] = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                                    if k.lower() not in REF_QUERY_KEYS]
    kind, rid = spec["kind"], spec["id"]
    if kind == "prefix":
        path = f"/ref/{rid}{path}"
    elif kind == "suffix":
        path = path.rstrip("/") + f"/ref/{rid}/"
    elif kind == "query":
        query.append((spec["key"], rid))
    for k, v in spec.get("extra", []):          # постоянные параметры образца (например, sharedfrom=pdp)
        if (k, v) not in query:
            query.append((k, v))
    return urlunsplit((p.scheme or "https", p.netloc, path, urlencode(query), ""))
