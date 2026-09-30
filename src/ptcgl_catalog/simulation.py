from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .database import database_url
from .effects import draw_value, maximum_attack_damage


MODEL_VERSION = "prototype-0.1.0"


@dataclass(frozen=True)
class CardModel:
    id: str
    name: str
    supertype: str
    subtypes: tuple[str, ...]
    hp: int
    damage: int
    draw: int

    @property
    def is_pokemon(self) -> bool:
        return self.supertype == "Pokémon"

    @property
    def is_basic(self) -> bool:
        return self.is_pokemon and "Basic" in self.subtypes


class DecisionPolicy(Protocol):
    name: str

    def choose_active(self, candidates: list[CardModel], rng: random.Random) -> CardModel:
        ...


class GreedyDamagePolicy:
    name = "greedy-damage-v1"

    def choose_active(self, candidates: list[CardModel], rng: random.Random) -> CardModel:
        return max(candidates, key=lambda card: (card.damage, card.hp, card.name))


class DurablePolicy:
    name = "durable-v1"

    def choose_active(self, candidates: list[CardModel], rng: random.Random) -> CardModel:
        return max(candidates, key=lambda card: (card.hp, card.damage, card.name))


class RandomPolicy:
    name = "random-v1"

    def choose_active(self, candidates: list[CardModel], rng: random.Random) -> CardModel:
        return rng.choice(candidates)


POLICIES: dict[str, DecisionPolicy] = {
    "greedy": GreedyDamagePolicy(),
    "durable": DurablePolicy(),
    "random": RandomPolicy(),
}


@dataclass
class PlayerState:
    deck: list[CardModel]
    hand: list[CardModel] = field(default_factory=list)
    active: CardModel | None = None
    active_hp: int = 0
    prizes_taken: int = 0


def _draw(player: PlayerState, count: int = 1) -> bool:
    for _ in range(count):
        if not player.deck:
            return False
        player.hand.append(player.deck.pop())
    return True


def _promote(player: PlayerState, policy: DecisionPolicy, rng: random.Random) -> bool:
    candidates = [card for card in player.hand if card.is_basic]
    while not candidates and player.deck:
        _draw(player)
        candidates = [card for card in player.hand if card.is_basic]
    if not candidates:
        return False
    chosen = policy.choose_active(candidates, rng)
    player.hand.remove(chosen)
    player.active = chosen
    player.active_hp = max(10, chosen.hp)
    return True


def simulate_game(
    deck_a: list[CardModel],
    deck_b: list[CardModel],
    *,
    seed: int,
    policy_a: DecisionPolicy,
    policy_b: DecisionPolicy,
    starting_player: int = 0,
    max_turns: int = 300,
) -> dict[str, Any]:
    rng = random.Random(seed)
    players = [PlayerState(list(deck_a)), PlayerState(list(deck_b))]
    for player in players:
        rng.shuffle(player.deck)
        if not _draw(player, 7):
            return {"winner": None, "reason": "invalid_deck", "turns": 0}
    policies = [policy_a, policy_b]
    for index, player in enumerate(players):
        if not _promote(player, policies[index], rng):
            return {"winner": 1 - index, "reason": "no_basic_pokemon", "turns": 0}

    for turn in range(max_turns):
        attacker_index = (starting_player + turn) % 2
        defender_index = 1 - attacker_index
        attacker = players[attacker_index]
        defender = players[defender_index]
        if not _draw(attacker):
            return {"winner": defender_index, "reason": "deck_out", "turns": turn + 1}
        bonus_draw = min(5, attacker.active.draw if attacker.active else 0)
        if bonus_draw and not _draw(attacker, bonus_draw):
            return {"winner": defender_index, "reason": "deck_out", "turns": turn + 1}
        damage = attacker.active.damage if attacker.active else 0
        defender.active_hp -= max(0, damage)
        if defender.active_hp <= 0:
            attacker.prizes_taken += 1
            defender.active = None
            if attacker.prizes_taken >= 6:
                return {"winner": attacker_index, "reason": "prizes", "turns": turn + 1}
            if not _promote(defender, policies[defender_index], rng):
                return {"winner": attacker_index, "reason": "no_pokemon", "turns": turn + 1}
    return {"winner": None, "reason": "turn_limit", "turns": max_turns}


def _load_deck(connection: psycopg.Connection[Any], deck_id: str) -> list[CardModel]:
    rows = connection.execute(
        """
        SELECT dc.quantity, c.id, c.name, c.supertype, c.subtypes, c.hp,
               ce.effects
        FROM deck_cards dc
        JOIN cards c ON c.id = dc.card_id
        LEFT JOIN card_effects ce ON ce.card_id = c.id
        WHERE dc.deck_id = %s
        """,
        (deck_id,),
    ).fetchall()
    result: list[CardModel] = []
    for row in rows:
        parsed = row["effects"] or {"effects": []}
        model = CardModel(
            id=row["id"],
            name=row["name"],
            supertype=row["supertype"] or "",
            subtypes=tuple(row["subtypes"] or []),
            hp=row["hp"] or 0,
            damage=maximum_attack_damage(parsed),
            draw=draw_value(parsed),
        )
        result.extend([model] * row["quantity"])
    return result


def simulate_match(payload: dict[str, Any], url: str | None = None) -> dict[str, Any]:
    deck_a_id = str(payload["deckA"])
    deck_b_id = str(payload["deckB"])
    seed = int(payload.get("seed", 1))
    games = min(1000, max(1, int(payload.get("games", 100))))
    policy_a = POLICIES.get(payload.get("policyA", "greedy"), POLICIES["greedy"])
    policy_b = POLICIES.get(payload.get("policyB", "greedy"), POLICIES["greedy"])
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        deck_a = _load_deck(connection, deck_a_id)
        deck_b = _load_deck(connection, deck_b_id)
        if len(deck_a) != 60 or len(deck_b) != 60:
            raise ValueError("Both saved decks must contain exactly 60 cards")
        provenance = connection.execute(
            """
            SELECT cr.id AS catalog_run_id, r.id AS ruleset_id, r.version AS ruleset_version
            FROM catalog_runs cr CROSS JOIN rulesets r
            WHERE r.id = 'pokemon-tcg-standard'
            ORDER BY cr.imported_at DESC LIMIT 1
            """
        ).fetchone()
        if provenance is None:
            raise ValueError("Catalog and ruleset must be imported before simulation")
        wins = [0, 0]
        draws = 0
        reasons: dict[str, int] = {}
        turns: list[int] = []
        for game_index in range(games):
            result = simulate_game(
                deck_a, deck_b,
                seed=seed + game_index,
                policy_a=policy_a,
                policy_b=policy_b,
                starting_player=game_index % 2,
            )
            if result["winner"] is None:
                draws += 1
            else:
                wins[result["winner"]] += 1
            reasons[result["reason"]] = reasons.get(result["reason"], 0) + 1
            turns.append(result["turns"])
        summary = {
            "modelVersion": MODEL_VERSION,
            "catalogRunId": provenance["catalog_run_id"],
            "ruleset": {"id": provenance["ruleset_id"], "version": provenance["ruleset_version"]},
            "games": games,
            "seed": seed,
            "deckAWins": wins[0],
            "deckBWins": wins[1],
            "draws": draws,
            "averageTurns": sum(turns) / len(turns),
            "reasons": reasons,
            "limitations": [
                "Prototype effect subset only",
                "Energy attachment and attack-cost enforcement are not implemented",
                "Weakness, resistance, retreat, evolution, and bench timing are not implemented"
            ],
        }
        row = connection.execute(
            """
            INSERT INTO simulation_runs (
                deck_a_id, deck_b_id, seed, games, model_version,
                policy_a, policy_b, result, catalog_run_id, ruleset_id,
                ruleset_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                deck_a_id, deck_b_id, seed, games, MODEL_VERSION, policy_a.name,
                policy_b.name, Jsonb(summary), provenance["catalog_run_id"],
                provenance["ruleset_id"], provenance["ruleset_version"],
            ),
        ).fetchone()
        connection.commit()
    summary["simulationId"] = str(row["id"])
    return summary
