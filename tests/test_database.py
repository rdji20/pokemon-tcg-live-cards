from ptcgl_catalog.database import _card_payload, _hp, _rules_text, _status


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


def test_card_payload_exposes_all_four_rule_validation_states():
    base = {
        "raw_data": {"id": "x", "catalog": {}},
        "live_status": "playable",
        "standard_status": "legal",
        "expanded_status": "legal",
        "live_expanded_status": "legal",
        "legality_evidence": {},
        "verified_at": None,
    }
    cases = {
        (False, False): "not_validated",
        (True, False): "ai_validated",
        (False, True): "human_validated",
        (True, True): "validated",
    }
    for (ai_validated, human_validated), expected in cases.items():
        payload = _card_payload({
            **base,
            "ai_validated": ai_validated,
            "human_validated": human_validated,
        })
        assert payload["ruleValidation"]["state"] == expected
        assert payload["ruleValidation"]["testingAllowed"] is True
