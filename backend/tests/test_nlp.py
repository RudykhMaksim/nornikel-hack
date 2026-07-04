import pytest

from app import nlp


def test_extract_numeric_constraints_range_list():
    q = ("исходная вода содержит сульфаты, хлориды, Ca, Mg, Na по 200-300 мг/л, "
         "а требуемый сухой остаток - не более 1000 мг/дм3")
    constraints = nlp.extract_numeric_constraints(q)
    params = {c["parameter"] for c in constraints}
    assert {"sulfate_concentration", "chloride_concentration", "calcium_concentration",
            "magnesium_concentration", "sodium_concentration", "dry_residue"} <= params
    dry_residue = next(c for c in constraints if c["parameter"] == "dry_residue")
    assert dry_residue["operator"] == "<="
    assert dry_residue["value"] == 1000.0
    assert dry_residue["unit"] == "mg/dm3"

    sulfate = next(c for c in constraints if c["parameter"] == "sulfate_concentration")
    assert sulfate["operator"] == "range"
    assert sulfate["value"] == 200.0
    assert sulfate["value2"] == 300.0


def test_extract_geography_filters():
    assert nlp.extract_geography_filters("описаны в мировой практике") == ["International"]
    assert nlp.extract_geography_filters("применялись в России") == ["RU"]
    assert nlp.extract_geography_filters("применялись в России и за рубежом (международная практика)") == ["RU", "International"]
    assert nlp.extract_geography_filters("никаких упоминаний географии") == []


def test_extract_year_range_last_n_years():
    from datetime import date
    result = nlp.extract_year_range("за последние 5 лет", today=date(2026, 7, 3))
    assert result == (2021, 2026)


def test_extract_year_range_explicit_span():
    result = nlp.extract_year_range("в период 2018-2020")
    assert result == (2018, 2020)


def test_extract_year_range_none():
    assert nlp.extract_year_range("без указания периода") is None


def test_extract_numeric_constraints_english_range_list():
    q = ("the feed water contains sulfates, chlorides, Ca, Mg, Na at 200-300 mg/l each, "
         "and the required dry residue is not more than 1000 mg/dm3")
    constraints = nlp.extract_numeric_constraints(q)
    params = {c["parameter"] for c in constraints}
    assert {"sulfate_concentration", "chloride_concentration", "calcium_concentration",
            "magnesium_concentration", "sodium_concentration", "dry_residue"} <= params
    dry_residue = next(c for c in constraints if c["parameter"] == "dry_residue")
    assert dry_residue["operator"] == "<="
    assert dry_residue["value"] == 1000.0


@pytest.mark.parametrize("text,parameter,operator,value", [
    ("at least 200 mg/l of chloride", "chloride_concentration", ">=", 200.0),
    ("not less than 100 mg/l sodium", "sodium_concentration", ">=", 100.0),
    ("sulfate concentration up to 500 mg/l", "sulfate_concentration", "<=", 500.0),
    ("temperature not more than 80 °C", "temperature", "<=", 80.0),
    ("current density at least 300 A/m2", "current_density", ">=", 300.0),
])
def test_extract_numeric_constraints_english_phrasings(text, parameter, operator, value):
    constraints = nlp.extract_numeric_constraints(text)
    assert len(constraints) == 1
    assert constraints[0]["parameter"] == parameter
    assert constraints[0]["operator"] == operator
    assert constraints[0]["value"] == value


def test_extract_geography_filters_english():
    assert nlp.extract_geography_filters("described in international practice") == ["International"]
    assert nlp.extract_geography_filters("used in Russia") == ["RU"]
    assert nlp.extract_geography_filters("domestic experience with heap leaching") == ["RU"]
    assert nlp.extract_geography_filters("used abroad and in foreign countries") == ["International"]
    assert nlp.extract_geography_filters("used in Russia and abroad") == ["RU", "International"]


def test_extract_year_range_english_last_n_years():
    from datetime import date
    assert nlp.extract_year_range("over the last 5 years", today=date(2026, 7, 3)) == (2021, 2026)
    assert nlp.extract_year_range("in the past 10 years", today=date(2026, 7, 3)) == (2016, 2026)


def test_extract_year_range_english_span():
    assert nlp.extract_year_range("between 2015 and 2020") == (2015, 2020)
    assert nlp.extract_year_range("in 2019 a pilot was launched") == (2019, 2019)
