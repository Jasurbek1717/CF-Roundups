"""Прогон интерфейса целиком на мок-Algolia: поиск -> выбор -> подборка -> экспорт."""
import os
import streamlit as st
from streamlit.testing.v1 import AppTest
import cf_search as cs
from fakes import FakeAlgolia

APP = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")

def start(monkeypatch):
    st.cache_data.clear()                     # кеш Streamlit не должен «протекать» между тестами
    f = FakeAlgolia()
    monkeypatch.setattr(cs.requests, "post", f)
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    return at, f

def by_label(items, text):
    return next(i for i in items if text in i.label)

def configure(at):
    by_label(at.sidebar.text_input, "App ID").set_value("APPID123")
    by_label(at.sidebar.text_input, "Search key").set_value("SEARCHKEY")
    at.run()
    assert not at.exception

def test_first_screen_without_keys_does_not_crash(monkeypatch):
    at, f = start(monkeypatch)
    assert any("App ID" in i.value for i in at.info)
    assert f.calls == []                       # без ключей — ни одного запроса

def test_full_flow(monkeypatch):
    at, f = start(monkeypatch)
    configure(at)
    at.text_area(key="q_text").set_value("cat")
    at.run()
    by_label(at.button, "Найти").click().run()
    assert not at.exception and not at.error
    res = at.session_state["result"]
    assert len(res.products) == 10 and all(p["type"] == "Graphics" for p in res.products)

    # имитация отметки трёх товаров в таблице
    df = at.session_state["grid_base"].copy()
    df.loc[[0, 1, 2], "Выбрано"] = True
    at.session_state["grid_base"] = df
    at.run()
    assert len(at.session_state["picked"]) == 3

    # смена фильтра и повторный поиск не теряет выбор
    at.selectbox(key="sort_sel").set_value("date")
    by_label(at.button, "Найти").click().run()
    assert len(at.session_state["picked"]) == 3

    by_label(at.button, "Добавить выбранные").click().run()
    rts = at.session_state["roundups"]
    assert len(rts) == 1 and len(rts[0]["items"]) == 3 and not at.session_state["picked"]

    # заполняем статью и проверяем экспорт
    rid = rts[0]["rid"]
    at.text_input(key=f"t_{rid}").set_value("Best Cat SVG Files")
    at.text_input(key=f"c_{rid}").set_value("Graphics")
    at.text_area(key=f"i_{rid}").set_value("Short intro, with comma.")
    at.run()
    assert not at.exception and not at.error
    out = at.get("download_button")
    assert out, "кнопка скачивания должна быть доступна при отсутствии ошибок"

def test_export_blocked_until_required_fields(monkeypatch):
    at, _ = start(monkeypatch)
    configure(at)
    at.session_state["roundups"] = [{"rid": 1, "title": "", "category": "", "intro": "", "cta": "",
        "items": [{"id": "x", "title": "T", "description": "", "image": "https://i/1.jpg",
                   "url": "https://www.creativefabrica.com/product/t/", "cta": ""}], "ver": 0}]
    at.session_state["rid_counter"] = 1
    at.run()
    assert not at.exception
    assert any("название статьи" in e.value for e in at.error)
    assert any(getattr(b, "disabled", False) for b in at.get("download_button")) or not at.get("download_button")

def test_access_denied_shows_message_not_traceback(monkeypatch):
    at, _ = start(monkeypatch)
    monkeypatch.setattr(cs.requests, "post", FakeAlgolia(status=403))
    configure(at)
    at.text_area(key="q_text").set_value("cat")
    at.run()
    by_label(at.button, "Найти").click().run()
    assert not at.exception
    assert any("отклонил" in e.value for e in at.error)
