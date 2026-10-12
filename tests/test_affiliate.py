import affiliate as af

PLAIN = "https://www.creativefabrica.com/product/cute-cat-svg/"

def test_no_sample_keeps_plain_urls():
    assert af.parse_sample("") is None and af.parse_sample(None) is None
    assert af.build(PLAIN, None) == PLAIN

def test_suffix_format_with_constant_query():
    sample = "https://www.creativefabrica.com/product/some-other/ref/12345/?sharedfrom=pdp"
    spec = af.parse_sample(sample)
    assert spec and spec["kind"] == "suffix" and spec["id"] == "12345"
    assert af.build(PLAIN, spec) == "https://www.creativefabrica.com/product/cute-cat-svg/ref/12345/?sharedfrom=pdp"
    assert af.build("https://www.creativefabrica.com/product/some-other/", spec) == sample   # обратная сборка образца

def test_prefix_format():
    spec = af.parse_sample("https://www.creativefabrica.com/ref/777/product/x/")
    assert spec["kind"] == "prefix"
    assert af.build(PLAIN, spec) == "https://www.creativefabrica.com/ref/777/product/cute-cat-svg/"

def test_query_format():
    spec = af.parse_sample("https://www.creativefabrica.com/product/x/?ref=999")
    assert spec["kind"] == "query"
    assert af.build(PLAIN, spec) == PLAIN + "?ref=999"

def test_not_recognized_and_foreign_domains():
    assert af.parse_sample("https://www.creativefabrica.com/product/x/") is None      # нет ID
    assert af.parse_sample("https://example.com/ref/1/product/x/") is None            # чужой домен
    spec = af.parse_sample("https://www.creativefabrica.com/ref/1/product/x/")
    assert af.build("https://example.com/p/", spec) == "https://example.com/p/"       # чужие ссылки не трогаем

def test_already_affiliate_not_duplicated():
    spec = af.parse_sample("https://www.creativefabrica.com/ref/1/product/x/")
    done = "https://www.creativefabrica.com/ref/1/product/y/"
    assert af.build(done, spec) == done
