# -*- coding: utf-8 -*-
"""
CF Roundups — поиск товаров Creative Fabrica → подборки → CSV для блога.
Поиск → Фильтры → Выбор → Экспорт. Без ИИ, без Google Sheets, без фоновых задач.
"""
from __future__ import annotations

import datetime as dt
import traceback

import pandas as pd
import streamlit as st

import affiliate as af
import cf_export as ex
import cf_search as cs

st.set_page_config(page_title="CF Roundups", page_icon="📦", layout="wide")

DEFAULT_CTA = "View on Creative Fabrica"
FREE_LABELS = {"any": "Любые", "paid": "Только платные", "free": "Только бесплатные"}


# ----------------------------------------------------------------------------
# Вспомогательное
# ----------------------------------------------------------------------------
def secret(name: str, default: str = "") -> str:
    try:
        v = st.secrets.get(name, default)
        return str(v) if v else default
    except Exception:
        return default


def init_state() -> None:
    ss = st.session_state
    ss.setdefault("picked", {})          # id -> товар: выбор не теряется при смене фильтров
    ss.setdefault("result", None)
    ss.setdefault("search_id", 0)
    ss.setdefault("grid_ver", 0)
    ss.setdefault("grid_base", None)
    ss.setdefault("roundups", [])
    ss.setdefault("rid_counter", 0)


def new_roundup(category: str = "") -> None:
    ss = st.session_state
    ss.rid_counter += 1
    ss.roundups.append({"rid": ss.rid_counter, "title": "", "category": category,
                        "intro": "", "cta": DEFAULT_CTA, "items": [], "ver": 0})
    ss["active_sel"] = len(ss.roundups) - 1


def cb_new_roundup() -> None:
    new_roundup(st.session_state.get("last_type", ""))


def cb_delete_roundup() -> None:
    ss = st.session_state
    try:
        i = int(ss.get("active_sel", 0))
    except (ValueError, TypeError):
        i = 0
    if 0 <= i < len(ss.roundups):
        ss.roundups.pop(i)
    ss["active_sel"] = max(0, min(i, len(ss.roundups) - 1))


def ts(d: dt.date, end: bool) -> int:
    t = dt.time.max if end else dt.time.min
    return int(dt.datetime.combine(d, t.replace(microsecond=0), tzinfo=dt.timezone.utc).timestamp())


def fmt_date(v) -> str:
    return dt.datetime.fromtimestamp(v, dt.timezone.utc).strftime("%Y-%m-%d") if v else ""


@st.cache_data(ttl=3600, show_spinner=False)
def cached_search(cfg: cs.Config, sp: cs.SearchParams, fields: tuple):
    return cs.search(cfg, sp, dict(fields))


@st.cache_data(ttl=86400, show_spinner=False)
def cached_categories(cfg: cs.Config, type_: str):
    return cs.list_categories(cfg, type_)


# ----------------------------------------------------------------------------
# Боковая панель
# ----------------------------------------------------------------------------
def sidebar():
    with st.sidebar:
        st.header("Настройки")
        app_id = st.text_input("Algolia App ID", value=secret("ALGOLIA_APP_ID"))
        api_key = st.text_input("Algolia Search key", value=secret("ALGOLIA_API_KEY"), type="password")
        with st.expander("Где взять актуальный ключ"):
            st.markdown(
                "1. Откройте creativefabrica.com в Chrome и нажмите **F12** → вкладка **Network**.\n"
                "2. Включите фильтр **Fetch/XHR**, введите в поиск сайта любое слово.\n"
                "3. Найдите запрос, в адресе которого есть `queries`, и откройте его → **Headers**.\n"
                "4. Скопируйте значения `x-algolia-application-id` и `x-algolia-api-key` "
                "(они в адресе запроса или в заголовках).\n\n"
                "Ключ не публикуйте. Если с новым ключом снова 403, значит ключ ограничен сайтом: "
                "приложение это не обходит.")
        with st.expander("Дополнительно"):
            index = st.text_input("Индекс", value=secret("ALGOLIA_INDEX", cs.DEFAULT_INDEX))
            pop_index = st.text_input("Индекс сортировки по популярности (необязательно)",
                                      value=secret("ALGOLIA_POPULARITY_INDEX"))
            st.caption("Поля с рейтингом, отзывами и описанием появляются, только если вы укажете "
                       "их точные имена из вкладки «Диагностика». Сами имена не угадываются.")
            f_rating = st.text_input("Поле рейтинга", value=secret("FIELD_RATING"))
            f_reviews = st.text_input("Поле числа отзывов", value=secret("FIELD_REVIEWS"))
            f_desc = st.text_input("Поле описания", value=secret("FIELD_DESCRIPTION"))
            sample = st.text_input("Образец партнёрской ссылки (необязательно)",
                                   value=secret("CF_AFFILIATE_LINK_EXAMPLE"))
        spec = af.parse_sample(sample)
        if sample.strip() and not spec:
            st.warning("Формат образца не распознан: ссылки будут без партнёрского ID.")
        elif spec:
            st.success(f"Партнёрский формат подтверждён образцом ({spec['kind']}).")
        else:
            st.caption("Партнёрская ссылка не задана: товары экспортируются обычными ссылками CF.")
    cfg = cs.Config(app_id.strip(), api_key.strip(), index.strip() or cs.DEFAULT_INDEX, pop_index.strip())
    fields = tuple((k, v.strip()) for k, v in
                   (("rating", f_rating), ("reviews", f_reviews), ("description", f_desc)) if v.strip())
    return cfg, fields, spec


# ----------------------------------------------------------------------------
# Вкладка «Поиск»
# ----------------------------------------------------------------------------
def build_grid_base(add_to: dict | None) -> None:
    ss = st.session_state
    res = ss.result
    in_rt = {it["id"] for it in add_to["items"]} if add_to else set()
    rows = []
    for p in res.products:
        rows.append({
            "Выбрано": p["id"] in ss.picked,
            "В подборке": p["id"] in in_rt,
            "Фото": p["image"],
            "Название": p["title"],
            "Категории": ", ".join(p["categories"]),
            "Цена": p["price"],
            "Бесплатно": bool(p["is_free"]),
            "Популярность": p["popularity"],
            "Дата": fmt_date(p["date"]),
            "Рейтинг": p["rating"],
            "Отзывы": p["reviews"],
            "Ссылка": p["url"],
        })
    df = pd.DataFrame(rows)
    for col in ("Рейтинг", "Отзывы"):          # только если поле реально настроено и заполнено
        if df[col].isna().all():
            df = df.drop(columns=[col])
    ss.grid_base = df


def active_roundup():
    ss = st.session_state
    if not ss.roundups:
        return None
    try:
        idx = int(ss.get("active_sel", 0))
    except (ValueError, TypeError):
        idx = 0
    i = max(0, min(idx, len(ss.roundups) - 1))
    return ss.roundups[i]


def tab_search(cfg: cs.Config, fields: tuple) -> None:
    ss = st.session_state
    c1, c2 = st.columns([3, 2])
    q_text = c1.text_area("Запросы (по одному в строке; фраза в кавычках — точное совпадение)",
                          height=110, key="q_text", placeholder='christmas svg\n"cricut" mug')
    type_ = c2.selectbox("Тип товаров (категории не смешиваются)", cs.MAIN_TYPES, key="type_sel")
    cats: list = []
    if cfg.ready:
        try:
            cat_list = cached_categories(cfg, type_)
        except cs.CFError as e:
            cat_list = []
            c2.caption(f"Список категорий недоступен: {e}")
        label = {n: f"{n} ({c})" for n, c in cat_list}
        cats = c2.multiselect("Категории внутри типа (необязательно)", [n for n, _ in cat_list],
                              format_func=lambda n: label[n], key=f"cats_{type_}")

    c3, c4, c5 = st.columns(3)
    free = c3.selectbox("Цена", list(FREE_LABELS), format_func=FREE_LABELS.get, key="free_sel")
    target = int(c4.number_input("Сколько товаров нужно", 1, 300, 10, key="target"))
    sort = c5.selectbox("Сортировка", list(cs.SORTS), format_func=cs.SORTS.get, key="sort_sel")

    with st.expander("Дополнительные фильтры"):
        d1, d2, d3 = st.columns(3)
        min_pop = d1.number_input("Популярность не ниже (0 — выкл.)", 0, 10**9, 0, key="min_pop")
        pmin = d2.number_input("Цена от (0 — выкл.)", 0.0, 1_000_000.0, 0.0, key="pmin")
        pmax = d3.number_input("Цена до (0 — выкл.)", 0.0, 1_000_000.0, 0.0, key="pmax")
        use_date = st.checkbox("Ограничить по дате публикации", key="use_date")
        dates = None
        if use_date:
            today = dt.date.today()
            dates = st.date_input("Период", (today - dt.timedelta(days=90), today), key="dates")
        strict = st.checkbox("Строгий поиск (без исправления опечаток)", value=True, key="strict")
        fresh = st.checkbox("Без кеша (запросить заново)", key="fresh")

    if st.button("🔎 Найти", type="primary"):
        d_from = d_to = None
        if use_date and isinstance(dates, (tuple, list)) and len(dates) == 2:
            d_from, d_to = ts(dates[0], False), ts(dates[1], True)
        sp = cs.SearchParams(
            queries=tuple(l for l in q_text.splitlines() if l.strip()), type_=type_,
            categories=tuple(cats), free=free, date_from=d_from, date_to=d_to, strict=strict,
            target=target, sort=sort, min_popularity=float(min_pop) or None,
            price_min=float(pmin) or None, price_max=float(pmax) or None)
        try:
            with st.spinner("Ищу…"):
                res = cs.search(cfg, sp, dict(fields)) if fresh else cached_search(cfg, sp, fields)
        except cs.CFError as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"Непредвиденная ошибка: {type(e).__name__}: {e}")
            st.code(traceback.format_exc())   # покажет ПОЛНЫЙ traceback как текст
            return
        ss.result, ss.last_type = res, type_
        ss.search_id += 1
        ss.grid_ver = 0
        build_grid_base(active_roundup())

    res = ss.result
    if res is None:
        st.info("Введите запрос и нажмите «Найти».")
        return
    st.caption(f"Найдено: {len(res.products)} · запросов к API: {res.requests_made} · "
               f"убрано дублей: {res.duplicates} · отсеяно локальными фильтрами: {res.filtered_out} · "
               f"всего совпадений по запросам: {sum(res.nb_hits.values())}")
    for n in res.notes:
        st.warning(n) if not res.exact_sort else st.info(n)
    if not res.products:
        return

    a, b, c = st.columns([1, 1, 3])
    if a.button("Выбрать все"):
        for p in res.products:
            ss.picked[p["id"]] = p
        ss.grid_ver += 1
        build_grid_base(active_roundup())
        st.rerun()
    if b.button("Снять все"):
        for p in res.products:
            ss.picked.pop(p["id"], None)
        ss.grid_ver += 1
        build_grid_base(active_roundup())
        st.rerun()

    df = ss.grid_base
    cfgcols = {
        "Выбрано": st.column_config.CheckboxColumn(width="small"),
        "В подборке": st.column_config.CheckboxColumn(width="small"),
        "Фото": st.column_config.ImageColumn(width="small"),
        "Бесплатно": st.column_config.CheckboxColumn(width="small"),
        "Ссылка": st.column_config.LinkColumn(display_text="Открыть"),
    }
    edited = st.data_editor(df, key=f"grid_{ss.search_id}_{ss.grid_ver}", hide_index=True,
                            column_config=cfgcols, disabled=[x for x in df.columns if x != "Выбрано"],
                            row_height=64, height=min(64 * len(df) + 45, 760))
    for i, flag in edited["Выбрано"].items():
        p = res.products[i]
        if flag:
            ss.picked[p["id"]] = p
        else:
            ss.picked.pop(p["id"], None)

    rt = active_roundup()
    n_here = sum(1 for p in res.products if p["id"] in ss.picked)
    n_all = len(ss.picked)
    c.write(f"Выбрано всего: **{n_all}** (в этом списке: {n_here}). Выбор сохраняется при новых поисках."
            + (f" Подборка: **{rt['title'] or 'без названия'}**" if rt else ""))
    if st.button(f"➕ Добавить выбранные ({n_all}) в подборку", disabled=n_all == 0):
        if rt is None:
            new_roundup(ss.get("last_type", ""))
            rt = active_roundup()
        have = {it["id"] for it in rt["items"]}
        for pid, p in list(ss.picked.items()):
            if pid not in have:
                rt["items"].append({"id": pid, "title": p["title"], "description": p["description"],
                                    "image": p["image"], "url": p["url"], "cta": ""})
            ss.picked.pop(pid, None)
        rt["ver"] += 1
        ss.grid_ver += 1
        build_grid_base(rt)
        st.rerun()


# ----------------------------------------------------------------------------
# Вкладка «Подборки»
# ----------------------------------------------------------------------------
def tab_roundups() -> None:
    ss = st.session_state
    if not ss.roundups:
        st.info("Подборок пока нет.")
        if st.button("➕ Создать подборку"):
            new_roundup(ss.get("last_type", ""))
            st.rerun()
        return
    rts = ss.roundups
    try:
        active_idx = int(ss.get("active_sel", 0))
    except (ValueError, TypeError):
        active_idx = 0
    if active_idx >= len(rts):
        ss["active_sel"] = len(rts) - 1
    names = [f"{i + 1}. {r['title'] or 'без названия'} ({len(r['items'])})" for i, r in enumerate(rts)]
    c1, c2, c3 = st.columns([3, 1, 1])
    idx = c1.selectbox("Подборка", range(len(rts)), format_func=lambda i: names[i], key="active_sel")
    c2.button("➕ Новая", on_click=cb_new_roundup)
    c3.button("🗑 Удалить", on_click=cb_delete_roundup)
    r = rts[idx]
    rid = r["rid"]
    r["title"] = st.text_input("article_title — название статьи", r["title"], key=f"t_{rid}")
    k1, k2 = st.columns(2)
    r["category"] = k1.text_input("article_category", r["category"], key=f"c_{rid}")
    r["cta"] = k2.text_input("cta_text (для первой строки)", r["cta"], key=f"cta_{rid}")
    r["intro"] = st.text_area("article_intro — вступление", r["intro"], height=140, key=f"i_{rid}")

    if not r["items"]:
        st.info("В подборке нет товаров: отметьте их на вкладке «Поиск».")
        return
    df = pd.DataFrame([{"Порядок": i + 1, "Удалить": False, "Фото": it["image"], "Название": it["title"],
                        "Описание": it["description"], "CTA строки": it["cta"], "Ссылка": it["url"]}
                       for i, it in enumerate(r["items"])])
    st.caption("Меняйте номера в «Порядок», отмечайте «Удалить», правьте название и описание, "
               "затем нажмите «Применить».")
    edited = st.data_editor(
        df, key=f"items_{rid}_{r['ver']}", hide_index=True, row_height=64,
        column_config={"Порядок": st.column_config.NumberColumn(min_value=1, step=1, width="small"),
                       "Удалить": st.column_config.CheckboxColumn(width="small"),
                       "Фото": st.column_config.ImageColumn(width="small"),
                       "Ссылка": st.column_config.LinkColumn(display_text="Открыть")},
        disabled=["Фото", "Ссылка"])
    b1, b2 = st.columns(2)
    if b1.button("✅ Применить", type="primary"):
        keep = []
        for pos, row in edited.iterrows():
            if row["Удалить"]:
                continue
            it = dict(r["items"][pos])
            it["title"] = str(row["Название"] or "")
            it["description"] = str(row["Описание"] or "")
            it["cta"] = str(row["CTA строки"] or "")
            order = float(row["Порядок"]) if pd.notna(row["Порядок"]) else 10**6
            keep.append((order, pos, it))
        keep.sort(key=lambda t: (t[0], t[1]))
        r["items"] = [t[2] for t in keep]
        r["ver"] += 1
        st.rerun()
    if b2.button("Очистить подборку"):
        r["items"] = []
        r["ver"] += 1
        st.rerun()


# ----------------------------------------------------------------------------
# Вкладка «Экспорт»
# ----------------------------------------------------------------------------
def tab_export(spec) -> None:
    ss = st.session_state
    if not any(r["items"] for r in ss.roundups):
        st.info("Нет подборок с товарами: найдите товары на вкладке «Поиск» и добавьте их в подборку.")
        return
    roundups = [ex.Roundup(
        title=r["title"], category=r["category"], intro=r["intro"], cta=r["cta"],
        items=[ex.Item(title=it["title"], description=it["description"], image_url=it["image"],
                       affiliate_url=af.build(it["url"], spec), cta=it["cta"]) for it in r["items"]])
        for r in ss.roundups]
    errors, warns = ex.validate(roundups)
    if spec is None:
        st.info("Партнёрская ссылка не задана: в CSV попадут обычные ссылки на товары CF. "
                "Партнёрский ID должен добавляться вашим импортом или образцом в настройках.")
    for e in errors:
        st.error(e)
    for w in warns:
        st.warning(w)
    o1, o2 = st.columns(2)
    delim = o1.selectbox("Разделитель", [",", ";"], format_func=lambda d: "запятая (как в шаблоне)" if d == "," else "точка с запятой")
    bom = o2.checkbox("UTF-8 с BOM (только если откроете в Excel)")
    data = ex.to_csv_bytes(roundups, delimiter=delim, bom=bom)
    rows = ex.rows(roundups)
    if rows:
        st.dataframe(pd.DataFrame(rows, columns=ex.COLUMNS), hide_index=True)
    st.download_button("⬇️ Скачать CSV", data, f"roundups_{dt.date.today():%Y%m%d}.csv", "text/csv",
                       type="primary", disabled=bool(errors))


# ----------------------------------------------------------------------------
# Вкладка «Диагностика»
# ----------------------------------------------------------------------------
def tab_diag(cfg: cs.Config) -> None:
    st.write("Проверка делает несколько лёгких запросов и показывает, какие поля и возможности "
             "реально отдаёт Algolia. Запускайте при первом использовании и если что-то перестало работать.")
    type_ = st.selectbox("Тип для проверки", cs.MAIN_TYPES, key="diag_type")
    if st.button("🛠 Проверить доступ и поля"):
        try:
            checks, sample = cs.diagnose(cfg, type_)
        except cs.CFError as e:
            st.error(str(e))
            return
        icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
        for c in checks:
            st.write(f"{icon[c['статус']]} **{c['проверка']}**: {c['детали']}")
        if sample:
            st.subheader("Поля товара")
            st.dataframe(pd.DataFrame(cs.describe_keys(sample)), hide_index=True)
            with st.expander("Сырой пример товара (JSON)"):
                st.json(sample)
            st.caption("Если среди полей есть рейтинг, число отзывов или описание — впишите точные имена "
                       "в «Настройки → Дополнительно», и они появятся в таблицах.")


# ----------------------------------------------------------------------------
def main() -> None:
    init_state()
    cfg, fields, spec = sidebar()
    st.title("📦 CF Roundups")
    st.caption("Поиск → Фильтры → Выбор → Экспорт CSV")
    if not cfg.ready:
        st.info("Укажите Algolia App ID и Search key в боковой панели (или в secrets).")
    t1, t2, t3, t4 = st.tabs(["🔎 Поиск", "📚 Подборки", "⬇️ Экспорт", "🛠 Диагностика"])
    with t1:
        tab_search(cfg, fields)
    with t2:
        tab_roundups()
    with t3:
        tab_export(spec)
    with t4:
        tab_diag(cfg)


main()
