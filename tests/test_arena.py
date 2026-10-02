from ptcgl_catalog import arena as arena_module
from ptcgl_catalog.arena import AI_POLICY_VERSION, ARENA_VERSION, MATCH_CLOCK_SECONDS, ArenaSession, report_arena_rule
from ptcgl_catalog.game_rules import CORE_RULES_VERSION


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


def _finish_setup(session: ArenaSession, *, order: str = "first") -> None:
    session.apply(next(action for action in session.legal_actions() if action["type"] == "call_coin" and action["choice"] == "heads"))
    if session.phase == "choose_turn_order":
        session.apply(next(action for action in session.legal_actions() if action.get("order") == order))
    if session.phase == "mulligan_draw":
        session.apply(max(session.legal_actions(), key=lambda action: action["count"]))
    session.apply(next(action for action in session.legal_actions() if action["type"] == "choose_active"))
    bench = next((action for action in session.legal_actions() if action["type"] == "setup_bench"), None)
    if bench:
        session.apply(bench)
    session.apply(next(action for action in session.legal_actions() if action["type"] == "finish_setup"))


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
    assert state["coreRulesVersion"] == CORE_RULES_VERSION
    assert state["status"] == "setup"
    assert state["phase"] == "coin_call"
    assert state["player"]["active"] is None
    assert state["player"]["deckCount"] == 60
    assert {action["choice"] for action in state["legalActions"]} == {"heads", "tails"}
    assert state["clocks"]["initialMs"] == MATCH_CLOCK_SECONDS * 1000
    assert MATCH_CLOCK_SECONDS * 1000 - 1000 < state["clocks"]["playerMs"] <= MATCH_CLOCK_SECONDS * 1000
    assert state["clocks"]["opponentMs"] == MATCH_CLOCK_SECONDS * 1000
    assert state["clocks"]["active"] == "player"

    _finish_setup(session)
    state = session.public_state()
    assert state["status"] == "playing"
    assert state["player"]["active"] is not None
    assert state["opponent"]["active"] is not None
    assert state["player"]["prizesRemaining"] == 6
    assert state["opponent"]["prizesRemaining"] == 6
    assert not any(action["type"] == "attack" for action in state["legalActions"])


def test_player_loses_when_their_match_clock_expires(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(arena_module.time, "monotonic", lambda: now[0])
    session = ArenaSession(
        player_name="Player",
        player_deck=_deck("player"),
        opponent_name="Opponent",
        opponent_deck=_deck("opponent"),
        seed=9,
    )
    now[0] += MATCH_CLOCK_SECONDS + 1
    state = session.public_state()
    assert state["status"] == "finished"
    assert state["winner"] == "opponent"
    assert state["reason"] == "time_expired"
    assert state["clocks"]["playerMs"] == 0
    assert state["clocks"]["active"] is None


def test_arena_returns_to_player_after_simple_ai_turn():
    session = ArenaSession(
        player_name="Player",
        player_deck=_deck("player"),
        opponent_name="Opponent",
        opponent_deck=_deck("opponent"),
        seed=4,
    )
    _finish_setup(session)
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
    _finish_setup(session)
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
    _finish_setup(session)
    active = session.players[0]["active"]
    active["card"]["attacks"][0]["text"] = "Flip a coin. If heads, prevent all effects of an attack."
    energy = next(item for item in session.players[0]["hand"] if item["card"]["supertype"] == "Energy")
    session.players[0]["hand"].remove(energy)
    active["energy"].append(energy)
    session.turn_number = 3
    session.players[0]["turnsTaken"] = 2
    attack = next(action for action in session.legal_actions() if action["type"] == "attack")
    assert attack["coverage"] == "partial"
    assert "base damage only" in attack["label"]


def test_player_can_choose_to_go_second_after_winning_coin_flip():
    session = ArenaSession(
        player_name="Player",
        player_deck=_deck("player"),
        opponent_name="Opponent",
        opponent_deck=_deck("opponent"),
        seed=1,
    )
    session.apply({"type": "call_coin", "choice": "heads"})
    assert session.phase == "choose_turn_order"
    session.apply({"type": "choose_turn_order", "order": "second"})
    assert session.first_player == 1
    assert session.phase in {"mulligan_draw", "choose_active"}
    assert session.players[1]["active"] is not None
    assert session.players[1]["prizes"] == []


def test_opening_hand_mulligans_until_it_contains_a_basic():
    found = None
    for seed in range(50):
        session = ArenaSession(
            player_name="Player",
            player_deck=_deck("player"),
            opponent_name="Opponent",
            opponent_deck=_deck("opponent"),
            seed=seed,
        )
        session.apply({"type": "call_coin", "choice": "heads"})
        if session.phase == "choose_turn_order":
            session.apply({"type": "choose_turn_order", "order": "first"})
        if session.mulligans[0] or session.mulligans[1]:
            found = session
            break
    assert found is not None
    assert any("Basic" in item["card"]["subtypes"] for item in found.players[0]["hand"])
    assert any("Basic" in item["card"]["subtypes"] for item in found.players[1]["hand"])


def test_compiled_attack_executes_and_records_exact_rule_version():
    session = ArenaSession(
        player_name="Player",
        player_deck=_deck("player"),
        opponent_name="Opponent",
        opponent_deck=_deck("opponent"),
        seed=12,
    )
    _finish_setup(session)
    active = session.players[0]["active"]
    active["card"]["ruleProgram"] = {
        "rules": [{
            "id": "attack-tackle", "sourceName": "Tackle", "sourceType": "attack",
            "trigger": "attack", "conditions": [],
            "effects": [
                {"op": "deal_damage", "target": "opponent.active", "amount": 30, "value": "", "conditions": []},
                {"op": "draw_cards", "target": "self", "amount": 1, "value": "", "conditions": []},
            ],
        }],
    }
    active["card"]["ruleVersion"] = {
        "id": "00000000-0000-0000-0000-000000000001",
        "programHash": "program-hash", "sourceTextHash": "source-hash",
        "aiStatus": "passed", "manualStatus": "pending", "flagged": False,
    }
    energy = next(item for item in session.players[0]["hand"] if item["card"]["supertype"] == "Energy")
    session.players[0]["hand"].remove(energy)
    active["energy"].append(energy)
    session.turn_number = 3
    session.players[0]["turnsTaken"] = 2
    hand_before = len(session.players[0]["hand"])
    attack = next(action for action in session.legal_actions() if action["type"] == "attack")
    assert attack["coverage"] == "complete"
    session.apply(attack)
    assert session.players[1]["active"]["damage"] == 30
    assert len(session.players[0]["hand"]) >= hand_before + 1
    trace = session.rule_trace[-1]
    assert trace["ruleVersionId"] == "00000000-0000-0000-0000-000000000001"
    assert trace["ruleId"] == "attack-tackle"
    assert trace["ruleKey"] == "player-pokemon:source-hash:program-hash:attack-tackle"
    assert trace["operations"] == ["deal_damage", "draw_cards"]


def test_rule_modifier_can_protect_a_benched_pokemon_from_ex_attack_damage():
    session = ArenaSession(
        player_name="Player",
        player_deck=_deck("player"),
        opponent_name="Opponent",
        opponent_deck=_deck("opponent"),
        seed=13,
    )
    _finish_setup(session)
    if not session.players[0]["bench"]:
        basic = next(item for item in session.players[0]["deck"] if item["card"]["supertype"] == "Pokémon" and "Basic" in item["card"]["subtypes"])
        session.players[0]["deck"].remove(basic)
        session.players[0]["hand"].append(basic)
        session._bench(session.players[0], basic["uid"])
    protected = session.players[0]["bench"][0]
    opponent = session.players[1]["active"]
    opponent["card"]["subtypes"].append("ex")
    source = {"uid": "supporter", "card": {"id": "test", "name": "Protection"}}
    session._store_modifier(0, source, {
        "op": "create_modifier", "target": "chosen_self_pokemon.damage_received",
        "amount": 0, "value": "prevent_all",
        "parameters": [
            {"key": "duration", "value": "during_opponent_next_turn"},
            {"key": "source_filter", "value": "opponent.pokemon_ex.attacks"},
        ],
    }, protected["uid"])
    assert session._modified_attack_damage(1, opponent, protected, 80) == 0


def test_arena_rule_flag_uses_the_exact_execution_trace(monkeypatch):
    session = ArenaSession(
        player_name="Player",
        player_deck=_deck("player"),
        opponent_name="Opponent",
        opponent_deck=_deck("opponent"),
        seed=14,
    )
    session.rule_trace.append({
        "traceId": 7,
        "cardId": "me1-113",
        "ruleVersionId": "00000000-0000-0000-0000-000000000001",
        "ruleId": "supporter-acerolas-mischief",
        "summary": "Prevented attack damage.",
    })
    arena_module._SESSIONS[session.id] = session
    captured = {}

    def fake_report(**kwargs):
        captured.update(kwargs)
        return {"id": "report-id"}

    monkeypatch.setattr("ptcgl_catalog.rule_reviews.report_rule_problem", fake_report)
    try:
        result = report_arena_rule(session.id, {"traceId": 7, "reason": "Bench protection was wrong"})
    finally:
        arena_module._SESSIONS.pop(session.id, None)
    assert result["id"] == "report-id"
    assert captured["rule_id"] == "supporter-acerolas-mischief"
    assert captured["trace"]["traceId"] == 7
    assert captured["reason"] == "Bench protection was wrong"
