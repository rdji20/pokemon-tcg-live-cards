from ptcgl_catalog.database import _hp, _rules_text, _status


def test_legality_status_is_explicit():
    assert _status({"standard": "Legal"}, "standard") == "legal"
    assert _status({"standard": "Banned"}, "standard") == "banned"
    assert _status({}, "standard") == "rotated"


def test_rules_text_collects_abilities_and_attacks():
    card = {
        "abilities": [{"name": "Insight", "text": "Draw a card."}],
        "attacks": [{"name": "Spark", "damage": "20", "text": "Flip a coin."}],
        "rules": ["A deck rule."],
    }
    text = _rules_text(card)
    assert "Insight Draw a card." in text
    assert "Spark 20 Flip a coin." in text
    assert "A deck rule." in text


def test_hp_only_accepts_numeric_values():
    assert _hp("120") == 120
    assert _hp(None) is None
    assert _hp("unknown") is None

