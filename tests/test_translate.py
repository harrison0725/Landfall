from pathlib import Path

from landfall import translate


def test_page_translates_text_but_not_numbers_or_style(tmp_path: Path, monkeypatch):
    sent = []

    def fake(texts, lang):
        sent.extend(texts)
        return {t: f"[{lang}] {t}" for t in texts}

    monkeypatch.setattr(translate, "translate", fake)
    doc = '<!doctype html><html lang="en"><head><title>Evidence packet b023</title><style>h1{color:red}</style></head><body><h1>Damage evidence</h1><div class="draft">Draft</div><td>3.6 ft</td><td>b023</td></body></html>'
    out = translate.page(doc, "es", tmp_path, "/en")
    assert '<html lang="es">' in out
    assert "[es] Damage evidence" in out and "[es] Evidence packet b023" in out
    assert "<td>3.6 ft</td>" in out and "<td>b023</td>" in out and "h1{color:red}" in out
    assert "Traducción automática" in out and 'href="/en"' in out
    assert "3.6 ft" not in sent and "b023" not in sent
    # the second page reuses what the first one translated
    sent.clear()
    translate.page(doc, "es", tmp_path, "/en")
    assert sent == []


def test_english_and_unknown_languages_are_left_alone(tmp_path: Path):
    doc = '<html lang="en"><body><p>Hello</p></body></html>'
    assert translate.page(doc, "fr", tmp_path, "/en") == doc
