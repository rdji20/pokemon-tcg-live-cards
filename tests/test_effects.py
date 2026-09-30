from ptcgl_catalog.effects import draw_value, maximum_attack_damage, parse_card_effects, parse_operations


def test_operations_are_machine_readable():
    operations = parse_operations("Draw 2 cards. Heal 30 damage. Your opponent's Active Pokémon is now Poisoned.")
    assert {item["op"] for item in operations} == {"draw", "heal", "apply_condition"}
    assert next(item for item in operations if item["op"] == "draw")["count"] == 2
    assert parse_operations("Draw a card.") == [{"op": "draw", "count": 1}]


def test_card_effects_include_attack_damage_and_draw_value():
    parsed = parse_card_effects({
        "attacks": [{"name": "Research Blast", "cost": ["Fire"], "damage": "120+", "text": "Draw 2 cards."}],
    })
    assert parsed["status"] == "parsed"
    assert maximum_attack_damage(parsed) == 120
    assert draw_value(parsed) == 2
