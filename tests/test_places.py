from landfall.places import _short


def test_us_places_name_the_state():
    assert _short({"town": "Cedar Key", "state": "Florida", "country": "United States", "country_code": "us"}, "") == "Cedar Key, Florida"


def test_places_abroad_name_the_country():
    assert _short({"city": "Mandalay", "state": "Mandalay Region", "country": "Myanmar", "country_code": "mm"}, "") == "Mandalay, Myanmar"


def test_a_state_or_country_alone():
    assert _short({"state": "Florida", "country": "United States", "country_code": "us"}, "") == "Florida"
    assert _short({"country": "Jamaica", "country_code": "jm"}, "x") == "Jamaica"
