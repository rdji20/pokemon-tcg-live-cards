from ptcgl_catalog.decks import parse_decklist


def test_parse_live_decklist_with_counted_sections():
    entries, errors = parse_decklist(
        """Pokémon: 4
4 Charmander OBF 26

Trainer: 4
4 Professor's Research SVI 189

Energy: 52
52 Basic Fire Energy SVE 2
"""
    )
    assert not errors
    assert sum(item["quantity"] for item in entries) == 60
    assert entries[0]["name"] == "Charmander"


def test_parse_decklist_reports_invalid_lines():
    entries, errors = parse_decklist("this is not a deck line")
    assert not entries
    assert errors[0]["code"] == "invalid_line"


def test_parse_live_decklist_accepts_hyphenated_promo_set_codes():
    entries, errors = parse_decklist("4 Infernape V PR-SW SWSH252\n4 Victini ex PR-SV 142")
    assert not errors
    assert entries == [
        {"quantity": 4, "name": "Infernape V", "set_code": "PR-SW", "number": "SWSH252", "line": 1},
        {"quantity": 4, "name": "Victini ex", "set_code": "PR-SV", "number": "142", "line": 2},
    ]
