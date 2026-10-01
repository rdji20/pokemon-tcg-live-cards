from ptcgl_catalog.arena import AI_POLICY_VERSION, ARENA_VERSION, ArenaSession


def _card(
    card_id: str,
    name: str,
    supertype: str,
    *,
    subtypes: list[str] | None = None,
    hp: int = 0,
    attacks: list[dict] | None = None,
) -> dict:
    return {
        "id": card_id,
        "name": name,
        "supertype": supertype,
        "subtypes": subtypes or [],
        "types": ["Colorless"] if supertype == "Pokémon" else [],
        "hp": hp,
        "image": None,
        "attacks": attacks or [],
        "rules": [],
        "evolvesFrom": None,
        "retreatCost": ["Colorless"] if supertype == "Pokémon" else [],
        "weaknesses": [],
        "resistances": [],
    }


def _deck(owner: str) -> list[dict]:
    basic = _card(
        f"{owner}-pokemon",
        f"{owner.title()} Pokémon",
        "Pokémon",
        subtypes=["Basic"],
        hp=100,
        attacks=[{"name": "Tackle", "cost": ["Colorless"], "damage": "30", "text": ""}],
    )
    energy = _card(f"{owner}-energy", "Basic Colorless Energy", "Energy", subtypes=["Basic"])
    cards = [basic] * 12 + [energy] * 48
    return [{"uid": f"{owner}-{index}", "card": card} for index, card in enumerate(cards, 1)]


def test_arena_starts_with_core_setup_and_versioned_policy():
    session = ArenaSession(
        player_name="Player",
        player_deck=_deck("player"),
        opponent_name="Opponent",
        opponent_deck=_deck("opponent"),
        seed=9,
    )
    state = session.public_state()
    assert state["arenaVersion"] == ARENA_VERSION
    assert state["aiPolicyVersion"] == AI_POLICY_VERSION
    assert state["player"]["active"] is not None
    assert state["opponent"]["active"] is not None
    assert state["player"]["prizesRemaining"] == 6
    assert state["opponent"]["prizesRemaining"] == 6
    assert not any(action["type"] == "attack" for action in state["legalActions"])


def test_arena_returns_to_player_after_simple_ai_turn():
    session = ArenaSession(
        player_name="Player",
        player_deck=_deck("player"),
        opponent_name="Opponent",
        opponent_deck=_deck("opponent"),
        seed=4,
    )
    attach = next(action for action in session.legal_actions() if action["type"] == "attach")
    session.apply(attach)
    session.apply({"type": "end_turn"})
    state = session.public_state()
    assert state["isPlayerTurn"]
    assert state["turn"] == 3
    assert any(action["type"] == "attack" for action in state["legalActions"])


def test_player_chooses_replacement_after_opponent_knockout():
    session = ArenaSession(
        player_name="Player",
        player_deck=_deck("player"),
        opponent_name="Opponent",
        opponent_deck=_deck("opponent"),
        seed=5,
    )
    session.current_player = 1
    session._knock_out(1, 0)
    actions = session.legal_actions()
    assert actions
    assert {action["type"] for action in actions} == {"promote"}
    session.apply(actions[0])
    assert session.players[0]["active"] is not None
    assert session.current_player == 0
    assert session.pending_promotion is None


def test_unsupported_attack_text_is_visibly_partial():
    session = ArenaSession(
        player_name="Player",
        player_deck=_deck("player"),
        opponent_name="Opponent",
        opponent_deck=_deck("opponent"),
        seed=6,
    )
    active = session.players[0]["active"]
    active["card"]["attacks"][0]["text"] = "Flip a coin. If heads, prevent all effects of an attack."
    energy = next(item for item in session.players[0]["hand"] if item["card"]["supertype"] == "Energy")
    session.players[0]["hand"].remove(energy)
    active["energy"].append(energy)
    session.turn_number = 3
    attack = next(action for action in session.legal_actions() if action["type"] == "attack")
    assert attack["coverage"] == "partial"
    assert "base damage only" in attack["label"]
