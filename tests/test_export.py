import csv, io, os
import pytest
import cf_export as ex

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "roundups-template.csv")

def regroup(raw: bytes):
    """Шаблон -> структуры подборок по правилам: title на каждой строке, category/intro в первой."""
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8")), delimiter=","))
    out, cur = [], None
    for t, cat, intro, it, desc, img, url, cta in rows[1:]:
        if cur is None or cur.title != t:
            cur = ex.Roundup(t, cat, intro, cta)
            out.append(cur)
            cur.items.append(ex.Item(it, desc, img, url))
        else:
            cur.items.append(ex.Item(it, desc, img, url, cta))
    return rows[0], out

def test_columns_match_template_header_and_order():
    raw = open(FIX, "rb").read()
    assert tuple(csv.reader(io.StringIO(raw.decode()))  .__next__()) == ex.COLUMNS

def test_export_reproduces_template_byte_for_byte():
    raw = open(FIX, "rb").read()
    _, roundups = regroup(raw)
    assert ex.to_csv_bytes(roundups) == raw            # те же байты: UTF-8, LF, кавычки, порядок

def test_structure_rules_multiple_roundups():
    a = ex.Roundup("Title A", "Fonts", "Intro A", "View", [ex.Item("a1", "d", "https://i/1.jpg", "https://www.creativefabrica.com/product/a1/"),
                                                         ex.Item("a2", "d", "https://i/2.jpg", "https://www.creativefabrica.com/product/a2/", "Get it")])
    b = ex.Roundup("Title B", "Graphics", "Intro B", "", [ex.Item("b1", "", "https://i/3.jpg", "https://www.creativefabrica.com/product/b1/")])
    rows = ex.rows([a, b])
    assert [r[0] for r in rows] == ["Title A", "Title A", "Title B"]            # title на каждой строке
    assert [r[1] for r in rows] == ["Fonts", "", "Graphics"]                   # category — только первая
    assert [r[2] for r in rows] == ["Intro A", "", "Intro B"]                  # intro — только первая
    assert [r[7] for r in rows] == ["View", "Get it", ""]                      # cta: первая строка / по строке

def test_csv_escaping_roundtrip_unicode_and_newlines():
    r = ex.Roundup('He said "hi", ok', "Graphics", "Line1\r\nLine2, with comma × 4000", "", [
        ex.Item('T "q", x', 'Desc; with; semis\nand newline', "https://i/1.jpg", "https://www.creativefabrica.com/product/x/")])
    for delim in (",", ";"):
        data = ex.to_csv_bytes([r], delimiter=delim)
        back = list(csv.reader(io.StringIO(data.decode("utf-8")), delimiter=delim))
        assert back[0] == list(ex.COLUMNS)
        assert back[1][0] == 'He said "hi", ok'
        assert back[1][2] == "Line1\nLine2, with comma × 4000"
        assert back[1][3] == 'T "q", x' and back[1][4] == "Desc; with; semis\nand newline"
        assert len(back) == 2 and all(len(row) == 8 for row in back)

def test_bom_and_no_bom():
    r = ex.Roundup("T", "C", "I", "", [ex.Item("t", "", "https://i/1.jpg", "https://www.creativefabrica.com/p/")])
    assert not ex.to_csv_bytes([r]).startswith(b"\xef\xbb\xbf")
    assert ex.to_csv_bytes([r], bom=True).startswith(b"\xef\xbb\xbf")

GOOD = dict(title="t", image_url="https://i/1.jpg", affiliate_url="https://www.creativefabrica.com/product/a/")

def test_validation_blocks_bad_data():
    bad = ex.Roundup("", "", "", "", [
        ex.Item("", image_url="not a url", affiliate_url="https://evil.example.com/x"),
        ex.Item("dup", image_url="https://i/1.jpg", affiliate_url="https://www.creativefabrica.com/product/a/"),
        ex.Item("dup2", image_url="https://i/1.jpg", affiliate_url="https://www.creativefabrica.com/product/a/")])
    errors, _ = ex.validate([bad])
    text = "\n".join(errors)
    for needle in ("название статьи", "категория", "нет названия", "изображение", "товар Creative Fabrica", "дубль"):
        assert needle in text
    assert ex.validate([]) [0]

def test_validation_duplicate_titles_and_warnings():
    mk = lambda: ex.Roundup("Same", "Fonts", "", "", [ex.Item(**GOOD)])
    errors, warns = ex.validate([mk(), mk()])
    assert any("уже есть" in e for e in errors)
    _, warns = ex.validate([ex.Roundup("A", "Fonts", "", "", [ex.Item(**GOOD)]),
                            ex.Roundup("B", "Fonts", "Intro", "", [ex.Item(**GOOD)])])
    joined = "\n".join(warns)
    assert "пустое вступление" in joined and "пустое описание" in joined and "повторяются" in joined

def test_valid_export_has_no_errors():
    _, roundups = regroup(open(FIX, "rb").read())
    errors, warns = ex.validate(roundups)
    assert errors == [] and warns == []
