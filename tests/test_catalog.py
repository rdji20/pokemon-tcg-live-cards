from datetime import date

from ptcgl_catalog.catalog import enrich_card, select_live_sets, validate


POLICY = {
    "supported_series": ["Sun & Moon", "Sword & Shield"],
    "included_set_ids": ["special"],
    "excluded_set_ids": ["cel25c"],
}


def test_select_live_sets_applies_policy_and_release_date():
    sets = [
        {"id": "sm1", "series": "Sun & Moon", "releaseDate": "2017/02/03"},
        {"id": "cel25c", "series": "Sword & Shield", "releaseDate": "2021/10/08"},
        {"id": "future", "series": "Sword & Shield", "releaseDate": "2030/01/01"},
        {"id": "special", "series": "Other", "releaseDate": "2020/01/01"},
        {"id": "base1", "series": "Base", "releaseDate": "1999/01/09"},
    ]
    result = select_live_sets(sets, POLICY, date(2026, 9, 30))
    assert [item["id"] for item in result] == ["sm1", "special"]


def test_enrich_card_keeps_raw_fields_and_adds_live_metadata():
    card = {
        "id": "sm1-1",
        "name": "Caterpie",
        "set": {"id": "sm1", "name": "Sun & Moon"},
        "legalities": {"expanded": "Legal"},
    }
    result = enrich_card(card, {"releaseDate": "2017/02/03"}, "abc")
    assert result["name"] == "Caterpie"
    assert result["catalog"]["livePlayable"] is True
    assert result["catalog"]["standardLegal"] is False
    assert result["catalog"]["expandedLegal"] is True


def test_validate_rejects_duplicate_ids():
    card = {"id": "x", "name": "X", "set": {"id": "s"}}
    try:
        validate([card, card])
    except Exception as exc:
        assert "Duplicate" in str(exc)
    else:
        raise AssertionError("expected duplicate validation failure")
