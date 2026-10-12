# -*- coding: utf-8 -*-
"""
cf_export.py — экспорт подборок в CSV строго по шаблону roundups-template.csv.

Правила, выведенные из шаблона (последняя версия файла):
  * 8 столбцов в фиксированном порядке (COLUMNS);
  * одна строка = один товар, несколько подборок в одном файле подряд;
  * article_title повторяется в КАЖДОЙ строке подборки;
  * article_category и article_intro заполнены только в первой строке подборки;
  * cta_text необязателен и привязан к строке (по умолчанию — первая строка подборки);
  * запятая-разделитель, UTF-8 без BOM, переводы строк LF; в кавычках всегда article_intro и
    item_description (если не пусты), остальные поля — только когда это необходимо.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple
from urllib.parse import urlsplit

COLUMNS = ("article_title", "article_category", "article_intro", "item_title",
           "item_description", "item_image_url", "item_affiliate_url", "cta_text")


@dataclass
class Item:
    title: str
    description: str = ""
    image_url: str = ""
    affiliate_url: str = ""
    cta: str = ""            # необязательный CTA именно для этой строки


@dataclass
class Roundup:
    title: str
    category: str
    intro: str = ""
    cta: str = ""            # CTA по умолчанию для первой строки подборки
    items: List[Item] = field(default_factory=list)


def _clean(s) -> str:
    return str(s or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def rows(roundups: List[Roundup]) -> List[List[str]]:
    out = []
    for r in roundups:
        for i, it in enumerate(r.items):
            first = i == 0
            out.append([
                _clean(r.title),
                _clean(r.category) if first else "",
                _clean(r.intro) if first else "",
                _clean(it.title),
                _clean(it.description),
                _clean(it.image_url),
                _clean(it.affiliate_url),
                _clean(it.cta) or (_clean(r.cta) if first else ""),
            ])
    return out


_ALWAYS_QUOTED = (COLUMNS.index("article_intro"), COLUMNS.index("item_description"))


def _fmt(value: str, force: bool, delimiter: str) -> str:
    """Поле CSV: кавычки по необходимости; для intro/description — всегда, если поле не пустое."""
    need = (force and value != "") or any(c in value for c in (delimiter, '"', "\n", "\r"))
    return '"' + value.replace('"', '""') + '"' if need else value


def to_csv_bytes(roundups: List[Roundup], delimiter: str = ",", bom: bool = False) -> bytes:
    """UTF-8, переводы строк LF; как в шаблоне, article_intro и item_description всегда в кавычках."""
    lines = [delimiter.join(COLUMNS)]
    for row in rows(roundups):
        lines.append(delimiter.join(_fmt(v, i in _ALWAYS_QUOTED, delimiter) for i, v in enumerate(row)))
    data = ("\n".join(lines) + "\n").encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def is_http_url(u: str, host_suffix: str = "") -> bool:
    p = urlsplit(str(u or "").strip())
    if p.scheme not in ("http", "https") or not p.netloc or " " in str(u).strip():
        return False
    return (p.netloc.lower().endswith(host_suffix) if host_suffix else True)


def validate(roundups: List[Roundup]) -> Tuple[List[str], List[str]]:
    """-> (ошибки, предупреждения). Ошибки блокируют скачивание."""
    errors: List[str] = []
    warns: List[str] = []
    if not roundups or not any(r.items for r in roundups):
        return ["Нет ни одной подборки с товарами."], warns
    titles = {}
    all_urls = {}
    for n, r in enumerate(roundups, 1):
        tag = f"Подборка {n} «{_clean(r.title) or 'без названия'}»"
        if not r.items:
            warns.append(f"{tag}: пустая, в файл не попадёт.")
            continue
        if not _clean(r.title):
            errors.append(f"{tag}: не заполнено название статьи.")
        elif _clean(r.title).lower() in titles:
            errors.append(f"{tag}: такое название уже есть у подборки {titles[_clean(r.title).lower()]} "
                          "(при импорте строки склеятся).")
        else:
            titles[_clean(r.title).lower()] = n
        if not _clean(r.category):
            errors.append(f"{tag}: не заполнена категория.")
        if not _clean(r.intro):
            warns.append(f"{tag}: пустое вступление.")
        seen = set()
        no_desc = 0
        for k, it in enumerate(r.items, 1):
            where = f"{tag}, товар {k}"
            if not _clean(it.title):
                errors.append(f"{where}: нет названия.")
            if not is_http_url(it.image_url):
                errors.append(f"{where}: некорректная ссылка на изображение.")
            if not is_http_url(it.affiliate_url, "creativefabrica.com"):
                errors.append(f"{where}: некорректная ссылка на товар Creative Fabrica.")
            key = _clean(it.affiliate_url)
            if key in seen:
                errors.append(f"{where}: дубль товара внутри подборки.")
            seen.add(key)
            all_urls.setdefault(key, set()).add(n)
            if not _clean(it.description):
                no_desc += 1
        if no_desc:
            warns.append(f"{tag}: у {no_desc} из {len(r.items)} товаров пустое описание.")
    shared = sum(1 for v in all_urls.values() if len(v) > 1)
    if shared:
        warns.append(f"{shared} товар(ов) повторяются в разных подборках.")
    return errors, warns
