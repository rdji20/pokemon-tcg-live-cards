from __future__ import annotations

import random
import re
import threading
import time
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .database import database_url
from .game_rules import (
    CORE_RULES_VERSION,
    MAX_BENCH,
    OPENING_HAND_SIZE,
    OFFICIAL_RULEBOOK_URL,
    PRIZE_COUNT,
    RULE_COVERAGE,
    SETUP_PHASES,
    first_turn_restricted,
    setup_prompt,
)
from .optimization import optimize_deck
from .rule_engine import executable_effects, matching_rule


ARENA_VERSION = "arena-0.4.0"
AI_POLICY_VERSION = "simple-ai-0.1.0"
MATCH_CLOCK_SECONDS = 20 * 60
ARENA_SUPPORTED_OPERATIONS = {
    "choose_cards", "clear_special_conditions", "confuse", "create_modifier",
    "deal_damage", "discard_cards", "discard_energy", "draw_cards", "flip_coin",
    "heal_damage", "inspect_top_deck", "knockout", "move_cards", "move_energy",
    "request_choice", "set_prize_value", "shuffle_cards", "shuffle_zone_into_deck",
    "swap_cards", "switch_active",
}
_SESSIONS: dict[str, "ArenaSession"] = {}
_SESSION_LOCK = threading.RLock()


def _card_record(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw_data") or {}
    return {
        "id": row["id"],
        "name": row["name"],
        "supertype": row.get("supertype") or "",
        "subtypes": list(row.get("subtypes") or []),
        "types": list(row.get("types") or []),
        "hp": int(row.get("hp") or 0),
        "image": row.get("image_small") or row.get("image_large"),
        "attacks": list(raw.get("attacks") or []),
        "rules": list(raw.get("rules") or []),
        "evolvesFrom": raw.get("evolvesFrom"),
        "retreatCost": list(raw.get("retreatCost") or []),
        "weaknesses": list(raw.get("weaknesses") or []),
        "resistances": list(raw.get("resistances") or []),
        "ruleProgram": row.get("rule_program"),
        "ruleVersion": None if not row.get("rule_version_id") else {
            "id": str(row["rule_version_id"]),
            "programHash": row.get("program_hash"),
            "sourceTextHash": row.get("rule_source_text_hash"),
            "aiStatus": row.get("ai_status"),
            "manualStatus": row.get("manual_status"),
            "flagged": bool(row.get("rule_flagged")),
        },
    }


def _load_cards(connection: psycopg.Connection[Any], entries: list[dict[str, Any]], owner: str) -> list[dict[str, Any]]:
    ids = [str(item["card_id"]) for item in entries]
    rows = connection.execute(
        """
        SELECT c.id, c.name, c.supertype, c.subtypes, c.types, c.hp,
               c.image_small, c.image_large, c.raw_data,
               rv.id AS rule_version_id, rv.source_text_hash AS rule_source_text_hash,
               rv.program_hash, rv.program AS rule_program,
               rv.ai_status, rv.manual_status,
               EXISTS (
                   SELECT 1 FROM card_rule_reports report
                   WHERE report.rule_version_id = rv.id AND report.status = 'open'
               ) AS rule_flagged
        FROM cards c
        LEFT JOIN LATERAL (
            SELECT candidate.*
            FROM card_rule_versions candidate
            WHERE candidate.card_id = c.id
              AND candidate.source_text_hash = c.source_text_hash
              AND candidate.ai_status = 'passed'
            ORDER BY candidate.created_at DESC, candidate.id DESC
            LIMIT 1
        ) rv ON true
        WHERE c.active AND c.standard_status = 'legal' AND c.id = ANY(%s)
        """,
        (ids,),
    ).fetchall()
    cards = {row["id"]: _card_record(dict(row)) for row in rows}
    missing = sorted(set(ids).difference(cards))
    if missing:
        raise ValueError(f"Arena deck contains unavailable Standard cards: {', '.join(missing)}")
    result: list[dict[str, Any]] = []
    serial = 0
    for entry in entries:
        for _ in range(int(entry["quantity"])):
            serial += 1
            result.append({"uid": f"{owner}-{serial}", "card": cards[str(entry["card_id"])]})
    if len(result) != 60:
        raise ValueError("Arena decks must contain exactly 60 cards")
    return result


def _load_saved_deck(connection: psycopg.Connection[Any], deck_id: str) -> tuple[str, list[dict[str, Any]]]:
    deck = connection.execute(
        "SELECT id, name, format FROM decks WHERE id = %s",
        (deck_id,),
    ).fetchone()
    if deck is None:
        raise ValueError("Saved deck was not found")
    if deck["format"] != "standard":
        raise ValueError("Arena 0.4 supports Standard decks only")
    entries = connection.execute(
        "SELECT card_id, quantity FROM deck_cards WHERE deck_id = %s ORDER BY card_id",
        (deck_id,),
    ).fetchall()
    return deck["name"], _load_cards(connection, [dict(row) for row in entries], "player")


def _is_basic(instance: dict[str, Any]) -> bool:
    card = instance["card"]
    return card["supertype"] == "Pokémon" and "Basic" in card["subtypes"]


def _is_energy(instance: dict[str, Any]) -> bool:
    return instance["card"]["supertype"] == "Energy"


def _energy_type(instance: dict[str, Any]) -> str:
    card = instance["card"]
    if card["types"]:
        return card["types"][0]
    match = re.search(r"Basic\s+([A-Za-z]+)\s+Energy", card["name"], re.IGNORECASE)
    return match.group(1).title() if match else "Colorless"


def _printed_damage(attack: dict[str, Any]) -> int:
    match = re.search(r"\d+", str(attack.get("damage") or ""))
    return int(match.group()) if match else 0


def _can_pay(cost: list[str], energy: list[dict[str, Any]]) -> bool:
    available = [_energy_type(item) for item in energy]
    colored = [symbol for symbol in cost if symbol != "Colorless"]
    for symbol in colored:
        if symbol not in available:
            return False
        available.remove(symbol)
    return len(available) >= sum(symbol == "Colorless" for symbol in cost)


def _pokemon(instance: dict[str, Any], *, entered_turn: int) -> dict[str, Any]:
    return {
        "uid": instance["uid"],
        "card": instance["card"],
        "stack": [instance],
        "damage": 0,
        "energy": [],
        "tools": [],
        "specialConditions": [],
        "enteredTurn": entered_turn,
    }


def _card_view(instance: dict[str, Any]) -> dict[str, Any]:
    card = instance["card"]
    return {
        "uid": instance["uid"],
        **{key: value for key, value in card.items() if key != "ruleProgram"},
    }


def _pokemon_view(pokemon: dict[str, Any] | None) -> dict[str, Any] | None:
    if pokemon is None:
        return None
    card = pokemon["card"]
    return {
        "uid": pokemon["uid"],
        **card,
        "damage": pokemon["damage"],
        "remainingHp": max(0, card["hp"] - pokemon["damage"]),
        "energy": [_card_view(item) for item in pokemon["energy"]],
        "energyCount": len(pokemon["energy"]),
        "tools": [_card_view(item) for item in pokemon.get("tools", [])],
        "specialConditions": list(pokemon.get("specialConditions") or []),
    }


class ArenaSession:
    def __init__(
        self,
        *,
        player_name: str,
        player_deck: list[dict[str, Any]],
        opponent_name: str,
        opponent_deck: list[dict[str, Any]],
        seed: int,
    ) -> None:
        self.id = str(uuid4())
        self.seed = seed
        self.rng = random.Random(seed)
        self.turn_number = 0
        self.current_player = 0
        self.first_player: int | None = None
        self.phase = "coin_call"
        self.coin_call: str | None = None
        self.coin_result: str | None = None
        self.coin_winner: int | None = None
        self.mulligans = [0, 0]
        self.mulligan_draws_available = 0
        self.winner: int | None = None
        self.reason: str | None = None
        self.pending_promotion: int | None = None
        self.stadium: dict[str, Any] | None = None
        self.modifiers: list[dict[str, Any]] = []
        self.pending_events: dict[str, dict[str, Any]] = {}
        self.rule_trace: list[dict[str, Any]] = []
        self.rule_trace_sequence = 0
        self.clock_seconds = [float(MATCH_CLOCK_SECONDS), float(MATCH_CLOCK_SECONDS)]
        self.clock_owner: int | None = 0
        self.clock_started_at = time.monotonic()
        self.log: list[str] = []
        self.players = [
            self._new_player(player_name, player_deck),
            self._new_player(opponent_name, opponent_deck),
        ]
        self.log.append("Decks are ready. Call heads or tails for the opening coin flip.")

    def _commit_clock(self) -> None:
        if self.clock_owner is None or self.winner is not None:
            return
        now = time.monotonic()
        owner = self.clock_owner
        self.clock_seconds[owner] = max(0.0, self.clock_seconds[owner] - max(0.0, now - self.clock_started_at))
        self.clock_started_at = now
        if self.clock_seconds[owner] <= 0:
            self.winner = 1 - owner
            self.reason = "time_expired"
            self.phase = "finished"
            self.log.append(f"{self.players[owner]['name']} ran out of time and loses.")
            self.clock_owner = None

    def _switch_clock(self, owner: int | None) -> None:
        self._commit_clock()
        if self.winner is not None:
            return
        self.clock_owner = owner
        self.clock_started_at = time.monotonic()

    @staticmethod
    def _new_player(name: str, deck: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "name": name,
            "deck": list(deck),
            "hand": [],
            "prizes": [],
            "discard": [],
            "active": None,
            "bench": [],
            "energyAttached": False,
            "retreated": False,
            "supporterPlayed": False,
            "stadiumPlayed": False,
            "abilitiesUsed": set(),
            "turnsTaken": 0,
        }

    def _draw(self, player: dict[str, Any], count: int = 1) -> bool:
        for _ in range(count):
            if not player["deck"]:
                return False
            player["hand"].append(player["deck"].pop())
        return True

    def _opening_hand(self, player: dict[str, Any]) -> int:
        mulligans = 0
        self.rng.shuffle(player["deck"])
        while True:
            player["hand"] = []
            if not self._draw(player, OPENING_HAND_SIZE):
                raise ValueError("Deck cannot produce a seven-card opening hand")
            if any(_is_basic(item) for item in player["hand"]):
                return mulligans
            player["deck"].extend(player["hand"])
            player["hand"] = []
            self.rng.shuffle(player["deck"])
            mulligans += 1
            if mulligans > 100:
                raise ValueError("Deck could not produce a Basic Pokémon after 100 mulligans")

    def _call_coin(self, choice: str) -> None:
        if choice not in {"heads", "tails"}:
            raise ValueError("Coin call must be heads or tails")
        self.coin_call = choice
        self.coin_result = self.rng.choice(("heads", "tails"))
        self.coin_winner = 0 if self.coin_call == self.coin_result else 1
        self.log.append(f"The coin landed {self.coin_result}. {'You won' if self.coin_winner == 0 else 'The opponent won'} the flip.")
        if self.coin_winner == 0:
            self.phase = "choose_turn_order"
            return
        self.first_player = 1
        self.log.append(f"{self.players[1]['name']} chose to go first.")
        self._prepare_opening_hands()

    def _choose_turn_order(self, order: str) -> None:
        if order not in {"first", "second"}:
            raise ValueError("Choose first or second")
        self.first_player = 0 if order == "first" else 1
        self.log.append(f"{self.players[0]['name']} chose to go {order}.")
        self._prepare_opening_hands()

    def _prepare_opening_hands(self) -> None:
        self.mulligans = [self._opening_hand(player) for player in self.players]
        self.log.append(
            f"Opening hands dealt. Mulligans: {self.players[0]['name']} {self.mulligans[0]}, "
            f"{self.players[1]['name']} {self.mulligans[1]}."
        )
        for _ in range(self.mulligans[0]):
            self._draw(self.players[1])
        if self.mulligans[0]:
            self.log.append(f"{self.players[1]['name']} drew {self.mulligans[0]} mulligan bonus card{'s' if self.mulligans[0] != 1 else ''}.")
        self.mulligan_draws_available = self.mulligans[1]
        self._set_up_in_play(self.players[1])
        self.phase = "mulligan_draw" if self.mulligan_draws_available else "choose_active"

    def _resolve_mulligan_draw(self, count: int) -> None:
        if count < 0 or count > self.mulligan_draws_available:
            raise ValueError("Mulligan bonus draw must be between zero and the available card count")
        if count:
            self._draw(self.players[0], count)
            self.log.append(f"{self.players[0]['name']} drew {count} mulligan bonus card{'s' if count != 1 else ''}.")
        else:
            self.log.append(f"{self.players[0]['name']} declined the mulligan bonus draw.")
        self.mulligan_draws_available = 0
        self.phase = "choose_active"

    def _choose_active(self, card_uid: str) -> None:
        player = self.players[0]
        item = self._find_hand(player, card_uid)
        if not _is_basic(item):
            raise ValueError("The Active Pokémon chosen during setup must be Basic")
        player["hand"].remove(item)
        player["active"] = _pokemon(item, entered_turn=0)
        self.phase = "choose_bench"
        self.log.append(f"{player['name']} chose {item['card']['name']} as the opening Active Pokémon.")

    def _finish_setup(self) -> None:
        for player in self.players:
            for _ in range(PRIZE_COUNT):
                if not player["deck"]:
                    raise ValueError("Deck does not contain enough cards to place six Prize cards")
                player["prizes"].append(player["deck"].pop())
        self.phase = "playing"
        self.current_player = int(self.first_player or 0)
        self.turn_number = 1
        self.log.append("Both players revealed their setup Pokémon and placed six Prize cards.")
        self.log.append(f"{self.players[self.current_player]['name']} goes first.")
        self._switch_clock(self.current_player)
        self._begin_turn()
        if self.current_player == 1 and self.winner is None:
            self._run_ai_turn()

    def _set_up_in_play(self, player: dict[str, Any]) -> None:
        basics = sorted(
            (item for item in player["hand"] if _is_basic(item)),
            key=lambda item: (item["card"]["hp"], item["card"]["name"]),
            reverse=True,
        )
        chosen = basics[0]
        player["hand"].remove(chosen)
        player["active"] = _pokemon(chosen, entered_turn=0)
        for item in basics[1:MAX_BENCH + 1]:
            player["hand"].remove(item)
            player["bench"].append(_pokemon(item, entered_turn=0))

    def _begin_turn(self) -> None:
        player = self.players[self.current_player]
        player["turnsTaken"] += 1
        player["energyAttached"] = False
        player["retreated"] = False
        player["supporterPlayed"] = False
        player["stadiumPlayed"] = False
        player["abilitiesUsed"] = set()
        self.pending_events = {
            uid: event for uid, event in self.pending_events.items()
            if event.get("playerIndex") != self.current_player
        }
        if not self._draw(player):
            self.winner = 1 - self.current_player
            self.reason = "deck_out"
            self.log.append(f"{player['name']} cannot draw a card and loses.")
            return
        self.log.append(f"Turn {self.turn_number}: {player['name']} drew a card.")

    def _finish_turn(self) -> None:
        if self.winner is not None or self.pending_promotion is not None:
            return
        self.current_player = 1 - self.current_player
        self.turn_number += 1
        self.modifiers = [
            item for item in self.modifiers
            if item["expiresAfter"] is None or self.turn_number <= item["expiresAfter"]
        ]
        self._switch_clock(self.current_player)
        self._begin_turn()
        if self.current_player == 1 and self.winner is None:
            self._run_ai_turn()

    def _find_hand(self, player: dict[str, Any], uid: str) -> dict[str, Any]:
        item = next((card for card in player["hand"] if card["uid"] == uid), None)
        if item is None:
            raise ValueError("That card is not in your hand")
        return item

    @staticmethod
    def _find_pokemon(player: dict[str, Any], uid: str) -> dict[str, Any]:
        candidates = [player["active"], *player["bench"]]
        pokemon = next((item for item in candidates if item and item["uid"] == uid), None)
        if pokemon is None:
            raise ValueError("That Pokémon is not in play")
        return pokemon

    @staticmethod
    def _parameters(effect: dict[str, Any]) -> dict[str, str]:
        return {str(item.get("key")): str(item.get("value", "")) for item in effect.get("parameters", [])}

    @staticmethod
    def _in_play(player: dict[str, Any]) -> list[dict[str, Any]]:
        return [item for item in [player.get("active"), *player.get("bench", [])] if item]

    def _rule_context(
        self,
        player_index: int,
        *,
        source: dict[str, Any] | None = None,
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        player = self.players[player_index]
        opponent = self.players[1 - player_index]
        source = source or player.get("active")
        active = player.get("active")
        opposing_active = opponent.get("active")
        attached_types = [] if source is None else [_energy_type(item) for item in source.get("energy", [])]
        location = None
        if source is not None:
            location = "active" if active and active["uid"] == source["uid"] else "bench"
        return {
            "self": {
                "location": location,
                "prizes": len(player["prizes"]),
                "prize_cards_remaining": len(player["prizes"]),
                "damage_counters": 0 if source is None else int(source.get("damage", 0)) // 10,
                "extra_energy_count": 0,
                "attached_energy": {"types": attached_types},
                "bench": {"has_damage_counters": any(item["damage"] > 0 for item in player["bench"])},
                "active": {} if active is None else {
                    "types": active["card"]["types"],
                    "subtypes": active["card"]["subtypes"],
                    "special_condition": active.get("specialConditions", []),
                },
            },
            "opponent": {
                "prizes": len(opponent["prizes"]),
                "prize_cards_remaining": len(opponent["prizes"]),
                "response": "yes",
                "active": {} if opposing_active is None else {
                    "types": opposing_active["card"]["types"],
                    "subtypes": opposing_active["card"]["subtypes"],
                    "special_condition": opposing_active.get("specialConditions", []),
                },
            },
            "turn": {
                "is_self": self.current_player == player_index,
                "ability_used": {rule_id: True for rule_id in player["abilitiesUsed"]},
            },
            "event": event or {},
        }

    @staticmethod
    def _program_rule(card: dict[str, Any], trigger: str, source_name: str | None = None) -> dict[str, Any] | None:
        return matching_rule(card.get("ruleProgram"), trigger=trigger, source_name=source_name)

    def _trace_rule(
        self,
        *,
        player_index: int,
        source: dict[str, Any],
        rule: dict[str, Any],
        effects: list[dict[str, Any]],
        summary: str,
    ) -> None:
        version = source["card"].get("ruleVersion")
        if not version:
            return
        self.rule_trace_sequence += 1
        rule_key = ":".join((
            source["card"]["id"], version["sourceTextHash"],
            version["programHash"], rule["id"],
        ))
        self.rule_trace.append({
            "traceId": self.rule_trace_sequence,
            "turn": self.turn_number,
            "player": "player" if player_index == 0 else "opponent",
            "cardId": source["card"]["id"],
            "cardName": source["card"]["name"],
            "ruleVersionId": version["id"],
            "programHash": version["programHash"],
            "ruleId": rule["id"],
            "ruleKey": rule_key,
            "trigger": rule["trigger"],
            "review": {
                "ai": version["aiStatus"],
                "manual": version["manualStatus"],
                "flagged": version["flagged"],
            },
            "operations": [effect["op"] for effect in effects],
            "summary": summary,
        })
        self.rule_trace = self.rule_trace[-100:]

    def _chosen_self(self, player_index: int, requested_uid: str | None = None) -> dict[str, Any] | None:
        player = self.players[player_index]
        if requested_uid:
            return next((item for item in self._in_play(player) if item["uid"] == requested_uid), None)
        return max(self._in_play(player), key=lambda item: (item["damage"], item["card"]["hp"]), default=None)

    def _resolve_moved_to_active(self, player_index: int, pokemon: dict[str, Any]) -> None:
        event = {
            "playerIndex": player_index,
            "moved_to_active": {
                "name": pokemon["card"]["name"],
                "previous_location": "self.bench",
            },
        }
        self.pending_events[pokemon["uid"]] = event
        player = self.players[player_index]
        for rule in (pokemon["card"].get("ruleProgram") or {}).get("rules", []):
            if rule.get("trigger") != "activate_ability":
                continue
            if not any(str(condition.get("field", "")).startswith("event.") for condition in rule.get("conditions", [])):
                continue
            effects = executable_effects(
                pokemon["card"]["ruleProgram"], rule["id"],
                self._rule_context(player_index, source=pokemon, event=event),
                seed=self.seed + self.turn_number,
            )
            if not effects:
                continue
            resolved, _ = self._execute_rule(player_index, pokemon, rule, event=event)
            player["abilitiesUsed"].add(rule["id"])
            self._trace_rule(
                player_index=player_index, source=pokemon, rule=rule,
                effects=resolved, summary=f"Entry Ability {rule['sourceName']} resolved.",
            )

    def _choose_deck_cards(self, player: dict[str, Any], effect: dict[str, Any], runtime: dict[str, Any]) -> None:
        value = str(effect.get("value") or "")
        amount = int(effect.get("amount") or 0)
        candidates = list(player["deck"])
        if effect.get("target") == "inspected_cards":
            candidates = list(runtime.get("inspected", []))
        if value == "basic_fighting_energy_or_basic_fighting_pokemon":
            candidates = [item for item in candidates if (
                (_is_energy(item) and _energy_type(item) == "Fighting")
                or (_is_basic(item) and "Fighting" in item["card"]["types"])
            )]
        elif value == "up_to_grass_pokemon_or_stadium":
            candidates = [item for item in candidates if (
                (item["card"]["supertype"] == "Pokémon" and "Grass" in item["card"]["types"])
                or "Stadium" in item["card"]["subtypes"]
            )]
        elif value == "mega_evolution_pokemon_ex":
            candidates = [item for item in candidates if (
                any(subtype in {"MEGA", "Mega Evolution"} for subtype in item["card"]["subtypes"])
                and "ex" in item["card"]["subtypes"]
            )]
        elif effect.get("target") == "inspected_cards":
            parameters = self._parameters(effect)
            if parameters.get("filter.supertype"):
                candidates = [item for item in candidates if item["card"]["supertype"] == parameters["filter.supertype"]]
        selected = candidates[:amount]
        parameters = self._parameters(effect)
        if parameters.get("requires_cost_paid") == "true" and not runtime.get("cost_paid"):
            return
        selection_id = parameters.get("selection_id", "selected")
        runtime.setdefault("selections", {})[selection_id] = selected
        if parameters.get("destination") == "self.hand":
            for item in selected:
                if item in player["deck"]:
                    player["deck"].remove(item)
                if item in runtime.get("inspected", []):
                    runtime["inspected"].remove(item)
                player["hand"].append(item)

    def _store_modifier(
        self,
        player_index: int,
        source: dict[str, Any],
        effect: dict[str, Any],
        requested_uid: str | None,
    ) -> None:
        target_uid = None
        effect_target = str(effect.get("target", ""))
        if effect_target.startswith("chosen_self_pokemon"):
            chosen = self._chosen_self(player_index, requested_uid)
            target_uid = chosen["uid"] if chosen else None
        elif effect_target.startswith("opponent.active"):
            opposing_active = self.players[1 - player_index].get("active")
            target_uid = opposing_active["uid"] if opposing_active else None
        parameters = self._parameters(effect)
        duration = parameters.get("duration", "")
        expires_after = None
        if duration == "during_opponent_next_turn":
            expires_after = self.turn_number + 1
        elif duration == "during_self_next_turn":
            expires_after = self.turn_number + 2
        self.modifiers.append({
            "owner": player_index,
            "sourceUid": source["uid"],
            "targetUid": target_uid,
            "createdTurn": self.turn_number,
            "expiresAfter": expires_after,
            "effect": effect,
        })

    def _apply_non_damage_effect(
        self,
        player_index: int,
        source: dict[str, Any],
        effect: dict[str, Any],
        runtime: dict[str, Any],
        *,
        target_uid: str | None = None,
        switch_target_uid: str | None = None,
    ) -> None:
        player = self.players[player_index]
        opponent = self.players[1 - player_index]
        op = effect["op"]
        target = str(effect.get("target") or "")
        amount = int(effect.get("amount") or 0)
        value = str(effect.get("value") or "")
        parameters = self._parameters(effect)
        if "damage" in source and "ex" in source["card"]["subtypes"] and target.startswith("opponent"):
            protected_target = opponent.get("active")
            if target == "opponent.in_play.chosen_pokemon" and target_uid:
                protected_target = next((item for item in self._in_play(opponent) if item["uid"] == target_uid), protected_target)
            if protected_target and any(
                modifier["effect"].get("target") == "chosen_self_pokemon.attack_effects_received"
                and modifier["owner"] == 1 - player_index
                and modifier["targetUid"] == protected_target["uid"]
                and (modifier["expiresAfter"] is None or self.turn_number <= modifier["expiresAfter"])
                for modifier in self.modifiers
            ):
                return
        if op == "draw_cards":
            if value == "until_hand_size_equals_self_psychic_pokemon_in_play":
                goal = sum("Psychic" in item["card"]["types"] for item in self._in_play(player))
                amount = max(0, goal - len(player["hand"]))
            self._draw(player, amount)
        elif op == "discard_cards":
            if target == "self.hand":
                player["discard"].extend(player["hand"])
                player["hand"] = []
            elif target == "opponent.deck.top":
                for _ in range(min(amount, len(opponent["deck"]))):
                    opponent["discard"].append(opponent["deck"].pop())
            elif target == "opponent.active.attached_pokemon_tools" and opponent.get("active"):
                opponent["discard"].extend(opponent["active"].get("tools", []))
                opponent["active"]["tools"] = []
        elif op == "discard_energy":
            owner = opponent if target.startswith("opponent") else player
            if target == "self.hand":
                energy = next((item for item in owner["hand"] if _is_energy(item)), None)
                if energy:
                    owner["hand"].remove(energy)
                    owner["discard"].append(energy)
                    runtime["cost_paid"] = True
                return
            pokemon = owner.get("active")
            if target == "opponent.in_play.chosen_pokemon":
                pokemon = next((item for item in self._in_play(owner) if item["energy"]), pokemon)
            if pokemon:
                count = len(pokemon["energy"]) if value == "all" else min(amount, len(pokemon["energy"]))
                for _ in range(count):
                    owner["discard"].append(pokemon["energy"].pop())
        elif op == "shuffle_zone_into_deck" and target == "self.hand":
            player["deck"].extend(player["hand"])
            player["hand"] = []
            self.rng.shuffle(player["deck"])
        elif op == "inspect_top_deck":
            runtime["inspected"] = [player["deck"].pop() for _ in range(min(amount, len(player["deck"])))]
        elif op == "choose_cards":
            self._choose_deck_cards(player, effect, runtime)
        elif op == "move_cards":
            destination = player if target == "self.hand" else opponent
            if value == "take_prize":
                if destination["prizes"]:
                    destination["hand"].append(destination["prizes"].pop())
            else:
                selection_id = parameters.get("selection_id", "selected")
                selected = runtime.get("selections", {}).get(selection_id, [])
                for item in selected[:amount]:
                    if item in player["deck"]:
                        player["deck"].remove(item)
                    if item in runtime.get("inspected", []):
                        runtime["inspected"].remove(item)
                    destination["hand"].append(item)
        elif op == "shuffle_cards":
            if target == "unchosen_inspected_cards":
                player["deck"].extend(runtime.get("inspected", []))
                runtime["inspected"] = []
            self.rng.shuffle(player["deck"])
        elif op == "heal_damage":
            chosen = self._chosen_self(player_index, target_uid)
            if chosen:
                chosen["damage"] = 0 if value == "all" else max(0, chosen["damage"] - amount)
                runtime.setdefault("selections", {})[parameters.get("selection_id", "chosen_self_pokemon")] = chosen
        elif op == "clear_special_conditions":
            selection = runtime.get("selections", {}).get(parameters.get("selection_id", "chosen_self_pokemon"))
            chosen = selection if isinstance(selection, dict) else self._chosen_self(player_index, target_uid)
            if chosen:
                chosen["specialConditions"] = []
        elif op == "confuse":
            chosen = player.get("active") if target == "self.active" else opponent.get("active")
            if chosen and "confused" not in chosen["specialConditions"]:
                chosen["specialConditions"].append("confused")
        elif op == "switch_active":
            owner = player if target == "self" else opponent
            if value == "optional" and not switch_target_uid:
                return
            requested = switch_target_uid or target_uid
            chosen = next((item for item in owner["bench"] if item["uid"] == requested), None)
            chosen = chosen or (owner["bench"][0] if owner["bench"] else None)
            if chosen:
                owner["bench"].remove(chosen)
                if owner["active"]:
                    owner["active"]["specialConditions"] = []
                    owner["bench"].append(owner["active"])
                owner["active"] = chosen
                self._resolve_moved_to_active(self.players.index(owner), chosen)
        elif op == "move_energy":
            in_play = self._in_play(player)
            source_candidates = player["bench"] if parameters.get("source") == "self.benched_pokemon" else in_play
            destination = player.get("active") if target == "self.active" else self._chosen_self(player_index, target_uid)
            source_pokemon = next((
                item for item in source_candidates
                if item["energy"] and destination and item["uid"] != destination["uid"]
            ), None)
            if source_pokemon and destination and source_pokemon["uid"] != destination["uid"]:
                count = len(source_pokemon["energy"]) if value == "any_amount" else min(amount, len(source_pokemon["energy"]))
                for _ in range(count):
                    destination["energy"].append(source_pokemon["energy"].pop())
        elif op == "swap_cards":
            if player["hand"] and player["deck"]:
                hand_card = player["hand"].pop(0)
                deck_card = player["deck"].pop()
                player["hand"].append(deck_card)
                player["deck"].append(hand_card)
        elif op == "create_modifier":
            if target not in {
                "self.turn.supporter_plays", "self.turn.item_plays", "self.turn.stadium_plays",
                "game.stadium_slot", "self.play_stadium.same_name_allowed",
                "current_attack.damage", "current_attack.usable_on_first_turn_when_going_first",
            }:
                self._store_modifier(player_index, source, effect, target_uid)

    def _execute_rule(
        self,
        player_index: int,
        source: dict[str, Any],
        rule: dict[str, Any],
        *,
        target_uid: str | None = None,
        switch_target_uid: str | None = None,
        event: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        context = self._rule_context(player_index, source=source, event=event)
        if source.get("card", {}).get("attacks"):
            attack = next((item for item in source["card"]["attacks"] if item.get("name") == rule.get("sourceName")), None)
            if attack:
                context["self"]["extra_energy_count"] = max(0, len(source["energy"]) - len(attack.get("cost") or []))
        effects = executable_effects(source["card"]["ruleProgram"], rule["id"], context, seed=self.seed + self.turn_number + self.rule_trace_sequence)
        runtime: dict[str, Any] = {"selections": {}, "cost_paid": False}
        for effect in effects:
            if effect["op"] not in {"deal_damage", "knockout", "set_prize_value", "flip_coin", "request_choice"}:
                self._apply_non_damage_effect(
                    player_index, source, effect, runtime,
                    target_uid=target_uid, switch_target_uid=switch_target_uid,
                )
        if any(effect.get("op") == "move_cards" and effect.get("value") == "take_prize" for effect in effects):
            players_without_prizes = [index for index, player in enumerate(self.players) if not player["prizes"]]
            if len(players_without_prizes) == 1:
                self.winner = players_without_prizes[0]
                self.reason = "prizes"
            elif len(players_without_prizes) == 2:
                self.winner = -1
                self.reason = "sudden_death_required"
            if self.winner is not None:
                self.phase = "finished"
                self.clock_owner = None
        return effects, runtime

    def _bench(self, player: dict[str, Any], card_uid: str) -> None:
        if len(player["bench"]) >= MAX_BENCH:
            raise ValueError("The Bench is full")
        item = self._find_hand(player, card_uid)
        if not _is_basic(item):
            raise ValueError("Only a Basic Pokémon can be placed directly on the Bench")
        player["hand"].remove(item)
        player["bench"].append(_pokemon(item, entered_turn=player["turnsTaken"]))
        self.log.append(f"{player['name']} benched {item['card']['name']}.")

    def _attach(self, player: dict[str, Any], card_uid: str, target_uid: str) -> None:
        if player["energyAttached"]:
            raise ValueError("Only one manual Energy attachment is allowed each turn")
        item = self._find_hand(player, card_uid)
        if not _is_energy(item):
            raise ValueError("That card is not an Energy card")
        target = self._find_pokemon(player, target_uid)
        player["hand"].remove(item)
        target["energy"].append(item)
        player["energyAttached"] = True
        self.log.append(f"{player['name']} attached {item['card']['name']} to {target['card']['name']}.")

    def _same_turn_evolution_allowed(self, target: dict[str, Any], evolution: dict[str, Any]) -> bool:
        if not self.stadium:
            return False
        rule = self._program_rule(self.stadium["card"], "continuous")
        if not rule or not any(
            effect.get("op") == "create_modifier"
            and effect.get("target") == "all_players.in_play.grass_pokemon.evolution_timing"
            and effect.get("value") == "allow_same_turn"
            for effect in rule.get("effects", [])
        ):
            return False
        return "Grass" in target["card"]["types"] and "Grass" in evolution["card"]["types"]

    def _evolve(self, player: dict[str, Any], card_uid: str, target_uid: str) -> None:
        if player["turnsTaken"] <= 1:
            raise ValueError("Pokémon cannot evolve on a player's first turn")
        item = self._find_hand(player, card_uid)
        target = self._find_pokemon(player, target_uid)
        used_stadium_exception = target["enteredTurn"] >= player["turnsTaken"] and self._same_turn_evolution_allowed(target, item)
        if target["enteredTurn"] >= player["turnsTaken"] and not used_stadium_exception:
            raise ValueError("A Pokémon cannot evolve during the turn it was played")
        if item["card"].get("evolvesFrom") != target["card"]["name"]:
            raise ValueError("That Evolution card does not evolve from the selected Pokémon")
        player["hand"].remove(item)
        target["stack"].append(item)
        target["card"] = item["card"]
        target["uid"] = item["uid"]
        target["specialConditions"] = []
        target["enteredTurn"] = player["turnsTaken"]
        self.log.append(f"{player['name']} evolved into {item['card']['name']}.")
        if used_stadium_exception and self.stadium:
            rule = self._program_rule(self.stadium["card"], "continuous")
            if rule:
                self._trace_rule(
                    player_index=self.players.index(player), source=self.stadium, rule=rule,
                    effects=rule.get("effects", []),
                    summary=f"{self.stadium['card']['name']} allowed same-turn Grass evolution.",
                )

    def _retreat(self, player: dict[str, Any], target_uid: str) -> None:
        if player["retreated"]:
            raise ValueError("A player may retreat only once each turn")
        active = player["active"]
        target = next((item for item in player["bench"] if item["uid"] == target_uid), None)
        if active is None or target is None:
            raise ValueError("Choose a Benched Pokémon to retreat into")
        player_index = self.players.index(player)
        if any(
            modifier["effect"].get("target") == "opponent.active.retreat_allowed"
            and modifier["owner"] != player_index
            and modifier["targetUid"] == active["uid"]
            and modifier["effect"].get("value") == "false"
            and (modifier["expiresAfter"] is None or self.turn_number <= modifier["expiresAfter"])
            for modifier in self.modifiers
        ):
            raise ValueError("An attack effect prevents this Active Pokémon from retreating")
        cost = len(active["card"]["retreatCost"])
        if len(active["energy"]) < cost:
            raise ValueError("The Active Pokémon does not have enough Energy to retreat")
        for _ in range(cost):
            player["discard"].append(active["energy"].pop())
        player["bench"].remove(target)
        active["specialConditions"] = []
        player["bench"].append(active)
        player["active"] = target
        self._resolve_moved_to_active(player_index, target)
        player["retreated"] = True
        self.log.append(f"{player['name']} retreated into {target['card']['name']}.")

    def _take_prizes(self, player: dict[str, Any], count: int) -> None:
        for _ in range(min(count, len(player["prizes"]))):
            player["hand"].append(player["prizes"].pop())
        if not player["prizes"]:
            self.winner = self.players.index(player)
            self.reason = "prizes"

    def _knock_out(self, attacker_index: int, defender_index: int) -> None:
        attacker = self.players[attacker_index]
        defender = self.players[defender_index]
        knocked_out = defender["active"]
        prize_count = 1
        prize_rule = self._program_rule(knocked_out["card"], "on_knockout")
        if prize_rule:
            effects, _ = self._execute_rule(defender_index, knocked_out, prize_rule)
            prize_effect = next((effect for effect in effects if effect["op"] == "set_prize_value"), None)
            if prize_effect:
                prize_count = int(prize_effect.get("amount") or 1)
            self._trace_rule(
                player_index=defender_index, source=knocked_out, rule=prize_rule,
                effects=effects, summary=f"Knock Out prize value set to {prize_count}.",
            )
        else:
            rules = " ".join(knocked_out["card"].get("rules") or [])
            prize_match = re.search(r"takes\s+(\d+)\s+Prize", rules, re.IGNORECASE)
            prize_count = int(prize_match.group(1)) if prize_match else 1
        defender["discard"].extend(knocked_out["stack"])
        defender["discard"].extend(knocked_out["energy"])
        defender["discard"].extend(knocked_out.get("tools", []))
        defender["active"] = None
        self.log.append(f"{knocked_out['card']['name']} was Knocked Out. {attacker['name']} takes {prize_count} Prize card{'s' if prize_count != 1 else ''}.")
        self._take_prizes(attacker, prize_count)
        if self.winner is not None:
            return
        if not defender["bench"]:
            self.winner = attacker_index
            self.reason = "no_pokemon"
            return
        if defender_index == 0:
            self.pending_promotion = defender_index
            self.log.append(f"{defender['name']} must choose a new Active Pokémon.")
            return
        promoted = max(defender["bench"], key=lambda item: (item["card"]["hp"], item["card"]["name"]))
        defender["bench"].remove(promoted)
        defender["active"] = promoted
        self.log.append(f"{defender['name']} promoted {promoted['card']['name']}.")

    def _knock_out_benched(self, attacker_index: int, defender_index: int, knocked_out: dict[str, Any]) -> None:
        attacker = self.players[attacker_index]
        defender = self.players[defender_index]
        if knocked_out not in defender["bench"]:
            return
        prize_count = 1
        prize_rule = self._program_rule(knocked_out["card"], "on_knockout")
        if prize_rule:
            effects, _ = self._execute_rule(defender_index, knocked_out, prize_rule)
            prize_effect = next((effect for effect in effects if effect["op"] == "set_prize_value"), None)
            if prize_effect:
                prize_count = int(prize_effect.get("amount") or 1)
            self._trace_rule(
                player_index=defender_index, source=knocked_out, rule=prize_rule,
                effects=effects, summary=f"Knock Out prize value set to {prize_count}.",
            )
        defender["bench"].remove(knocked_out)
        defender["discard"].extend(knocked_out["stack"])
        defender["discard"].extend(knocked_out["energy"])
        defender["discard"].extend(knocked_out.get("tools", []))
        self.log.append(f"Benched {knocked_out['card']['name']} was Knocked Out. {attacker['name']} takes {prize_count} Prize card{'s' if prize_count != 1 else ''}.")
        self._take_prizes(attacker, prize_count)

    def _promote(self, player_index: int, target_uid: str) -> None:
        if self.pending_promotion != player_index:
            raise ValueError("No replacement Active Pokémon is required")
        player = self.players[player_index]
        target = next((item for item in player["bench"] if item["uid"] == target_uid), None)
        if target is None:
            raise ValueError("Choose one of your Benched Pokémon")
        player["bench"].remove(target)
        player["active"] = target
        self.pending_promotion = None
        self.log.append(f"{player['name']} promoted {target['card']['name']}.")

    def _can_pay_attack(self, player_index: int, pokemon: dict[str, Any], cost: list[str]) -> bool:
        energy = list(pokemon["energy"])
        player = self.players[player_index]
        doubles_grass = any(
            any(
                rule.get("trigger") == "continuous"
                and any(
                    effect.get("op") == "create_modifier"
                    and effect.get("target") == "self.in_play.basic_grass_energy.energy_provided"
                    for effect in rule.get("effects", [])
                )
                for rule in (item["card"].get("ruleProgram") or {}).get("rules", [])
            )
            for item in self._in_play(player)
        )
        if doubles_grass:
            energy.extend(item for item in pokemon["energy"] if _energy_type(item) == "Grass")
        return _can_pay(cost, energy)

    def _modified_attack_damage(
        self,
        attacker_index: int,
        source: dict[str, Any],
        target: dict[str, Any],
        amount: int,
    ) -> int:
        damage = amount
        target_index = 1 - attacker_index
        source_is_ex = "ex" in source["card"]["subtypes"]
        source_has_rule_box = bool(source["card"].get("rules")) or any(
            subtype in {"ex", "V", "VMAX", "VSTAR", "GX"}
            for subtype in source["card"]["subtypes"]
        )
        target_is_ex = target is self.players[target_index].get("active") and "ex" in target["card"]["subtypes"]
        for tool in source.get("tools", []):
            rule = self._program_rule(tool["card"], "continuous")
            if not rule or source_has_rule_box or not target_is_ex:
                continue
            for effect in rule.get("effects", []):
                if effect.get("op") == "create_modifier" and effect.get("target") == "attached_pokemon.attacks.damage":
                    damage += int(effect.get("amount") or 0)
                    self._trace_rule(
                        player_index=attacker_index, source=tool, rule=rule,
                        effects=[effect], summary=f"{tool['card']['name']} added {int(effect.get('amount') or 0)} attack damage.",
                    )
        for modifier in self.modifiers:
            effect = modifier["effect"]
            field = str(effect.get("target") or "")
            if modifier["expiresAfter"] is not None and self.turn_number > modifier["expiresAfter"]:
                continue
            if field == "self.attacks.damage" and modifier["owner"] == attacker_index and modifier["sourceUid"] == source["uid"] and self.turn_number > modifier["createdTurn"]:
                damage += int(effect.get("amount") or 0)
            elif field == "opponent.active.attacks.damage" and modifier["owner"] == target_index and modifier["targetUid"] == source["uid"]:
                damage += int(effect.get("amount") or 0)
            elif field == "chosen_self_pokemon.damage_received" and modifier["owner"] == target_index and modifier["targetUid"] == target["uid"]:
                if effect.get("value") == "prevent_all" and source_is_ex:
                    damage = 0
        return max(0, damage)

    def _post_weakness_damage(self, target_index: int, target: dict[str, Any], damage: int) -> int:
        for modifier in self.modifiers:
            effect = modifier["effect"]
            if modifier["expiresAfter"] is not None and self.turn_number > modifier["expiresAfter"]:
                continue
            if (
                effect.get("target") == "self.in_play.metal_pokemon.damage_taken_from_opponent_attacks"
                and modifier["owner"] == target_index
                and "Metal" in target["card"]["types"]
            ):
                damage += int(effect.get("amount") or 0)
        return max(0, damage)

    def _attack(self, attacker_index: int, action: dict[str, Any]) -> None:
        attack_index = int(action["attackIndex"])
        defender_index = 1 - attacker_index
        attacker = self.players[attacker_index]
        defender = self.players[defender_index]
        active = attacker["active"]
        if active is None or defender["active"] is None:
            raise ValueError("Both players need an Active Pokémon")
        attacks = active["card"]["attacks"]
        if attack_index < 0 or attack_index >= len(attacks):
            raise ValueError("Unknown attack")
        attack = attacks[attack_index]
        if not self._can_pay_attack(attacker_index, active, list(attack.get("cost") or [])):
            raise ValueError("The Active Pokémon does not have the Energy required for that attack")
        if "confused" in active.get("specialConditions", []):
            confusion_result = self.rng.choice(("heads", "tails"))
            self.log.append(f"Confusion flip: {confusion_result}.")
            if confusion_result == "tails":
                active["damage"] += 30
                self.log.append(f"{active['card']['name']}'s attack failed and it took 30 damage from Confusion.")
                if active["damage"] >= active["card"]["hp"]:
                    self._knock_out(defender_index, attacker_index)
                return
        rule = self._program_rule(active["card"], "attack", str(attack.get("name")))
        if rule:
            effects, _ = self._execute_rule(
                attacker_index, active, rule,
                target_uid=action.get("targetUid"),
                switch_target_uid=action.get("switchTargetUid"),
            )
            active_damage = 0
            other_damage: list[tuple[dict[str, Any], int]] = []
            context = self._rule_context(attacker_index, source=active)
            coin = next((effect for effect in effects if effect["op"] == "flip_coin"), None)
            heads = int(coin.get("heads", int(coin.get("result") == "heads"))) if coin else 0
            for effect in effects:
                if effect["op"] == "create_modifier" and effect.get("target") == "current_attack.damage":
                    multiplier = int(context["self"].get("damage_counters", 0))
                    if effect.get("value") == "multiply":
                        active_damage += int(effect.get("amount") or 0) * multiplier
                if effect["op"] != "deal_damage":
                    continue
                amount = int(effect.get("amount") or 0)
                if effect.get("value") in {"multiply", "bonus_multiply"}:
                    amount *= heads
                target = str(effect.get("target") or "")
                if target == "opponent.active":
                    active_damage += amount
                elif target == "self":
                    other_damage.append((active, amount))
                elif target == "chosen_opponent_benched_pokemon":
                    chosen = next((item for item in defender["bench"] if item["uid"] == action.get("targetUid")), None)
                    if chosen:
                        other_damage.append((chosen, amount))
            active_damage = self._modified_attack_damage(attacker_index, active, defender["active"], active_damage)
            attack_type = (active["card"]["types"] or [""])[0]
            weakness = next((item for item in defender["active"]["card"]["weaknesses"] if item.get("type") == attack_type), None)
            resistance = next((item for item in defender["active"]["card"]["resistances"] if item.get("type") == attack_type), None)
            if weakness and str(weakness.get("value", "")).startswith("×"):
                active_damage *= int(str(weakness["value"])[1:] or 1)
            if resistance and str(resistance.get("value", "")).startswith("-"):
                active_damage = max(0, active_damage - int(str(resistance["value"])[1:] or 0))
            active_damage = self._post_weakness_damage(defender_index, defender["active"], active_damage)
            defender["active"]["damage"] += active_damage
            for target, amount in other_damage:
                if target is active:
                    target["damage"] += max(0, amount)
                else:
                    bench_damage = self._modified_attack_damage(attacker_index, active, target, amount)
                    target["damage"] += self._post_weakness_damage(defender_index, target, bench_damage)
                    if target in defender["bench"] and target["damage"] >= target["card"]["hp"]:
                        self._knock_out_benched(attacker_index, defender_index, target)
            if any(effect["op"] == "knockout" for effect in effects):
                self._knock_out(attacker_index, defender_index)
            self._trace_rule(
                player_index=attacker_index, source=active, rule=rule, effects=effects,
                summary=f"{attack['name']} resolved for {active_damage} Active damage.",
            )
            self.log.append(f"{active['card']['name']} used {attack['name']} for {active_damage} damage. Rule {rule['id']} executed.")
        else:
            damage = _printed_damage(attack)
            defender["active"]["damage"] += damage
            self.log.append(f"{active['card']['name']} used {attack['name']} for {damage} damage. No compiled rule was available; printed damage only.")
        if defender.get("active") and defender["active"]["damage"] >= defender["active"]["card"]["hp"]:
            self._knock_out(attacker_index, defender_index)
        if active["damage"] >= active["card"]["hp"] and attacker.get("active") is active:
            self._knock_out(defender_index, attacker_index)

    def _trainer_actions(self, player_index: int) -> list[dict[str, Any]]:
        player = self.players[player_index]
        opponent = self.players[1 - player_index]
        actions: list[dict[str, Any]] = []
        for item in player["hand"]:
            card = item["card"]
            if card["supertype"] != "Trainer":
                continue
            is_supporter = "Supporter" in card["subtypes"]
            if is_supporter and (
                player["supporterPlayed"]
                or first_turn_restricted(
                    player_index=player_index,
                    first_player=self.first_player,
                    turns_taken=player["turnsTaken"],
                )
            ):
                continue
            rule = self._program_rule(card, "play_card")
            if rule:
                if not executable_effects(card["ruleProgram"], rule["id"], self._rule_context(player_index, source=item), seed=self.seed):
                    continue
                targets_self = any(str(effect.get("target", "")).startswith("chosen_self_pokemon") for effect in rule.get("effects", []))
                switches_opponent = any(effect.get("op") == "switch_active" and effect.get("target") == "opponent" for effect in rule.get("effects", []))
                moves_energy = any(effect.get("op") == "move_energy" and effect.get("target") == "self.in_play" for effect in rule.get("effects", []))
                if switches_opponent:
                    for target in opponent["bench"]:
                        actions.append({"type": "play_trainer", "cardUid": item["uid"], "targetUid": target["uid"], "ruleId": rule["id"], "label": f"Play {card['name']} → {target['card']['name']}"})
                elif targets_self or moves_energy:
                    for target in self._in_play(player):
                        actions.append({"type": "play_trainer", "cardUid": item["uid"], "targetUid": target["uid"], "ruleId": rule["id"], "label": f"Play {card['name']} → {target['card']['name']}"})
                else:
                    actions.append({"type": "play_trainer", "cardUid": item["uid"], "ruleId": rule["id"], "label": f"Play {card['name']}"})
                continue
            continuous = self._program_rule(card, "continuous")
            if not continuous:
                continue
            if "Stadium" in card["subtypes"] and not player["stadiumPlayed"]:
                if self.stadium is None or self.stadium["card"]["name"] != card["name"]:
                    actions.append({
                        "type": "play_stadium", "cardUid": item["uid"],
                        "ruleId": continuous["id"], "label": f"Play {card['name']}",
                    })
            elif "Pokémon Tool" in card["subtypes"] or "Tool" in card["subtypes"]:
                for target in self._in_play(player):
                    if not target.get("tools"):
                        actions.append({
                            "type": "attach_tool", "cardUid": item["uid"],
                            "targetUid": target["uid"], "ruleId": continuous["id"],
                            "label": f"Attach {card['name']} → {target['card']['name']}",
                        })
        return actions

    def _play_trainer(self, player_index: int, card_uid: str, target_uid: str | None = None) -> None:
        player = self.players[player_index]
        opponent = self.players[1 - player_index]
        item = self._find_hand(player, card_uid)
        card = item["card"]
        if card["supertype"] != "Trainer":
            raise ValueError("That card is not a Trainer")
        if "Supporter" in card["subtypes"]:
            if player["supporterPlayed"] or first_turn_restricted(
                player_index=player_index,
                first_player=self.first_player,
                turns_taken=player["turnsTaken"],
            ):
                raise ValueError("That Supporter cannot be played now")
            player["supporterPlayed"] = True
        player["hand"].remove(item)
        rule = self._program_rule(card, "play_card")
        if rule is None:
            raise ValueError("That Trainer does not have a compiled rule program")
        effects, _ = self._execute_rule(player_index, item, rule, target_uid=target_uid)
        player["discard"].append(item)
        self._trace_rule(
            player_index=player_index, source=item, rule=rule, effects=effects,
            summary=f"{card['name']} resolved from the hand.",
        )
        self.log.append(f"{player['name']} played {card['name']}. Rule {rule['id']} executed.")

    def _activate_ability(self, player_index: int, source_uid: str, rule_id: str, target_uid: str | None = None) -> None:
        player = self.players[player_index]
        if self.stadium and self.stadium["uid"] == source_uid:
            source = self.stadium
        else:
            source = self._find_pokemon(player, source_uid)
        rule = next((
            item for item in (source["card"].get("ruleProgram") or {}).get("rules", [])
            if item.get("id") == rule_id and item.get("trigger") == "activate_ability"
        ), None)
        if rule is None:
            raise ValueError("That ability does not have a compiled rule")
        pending_event = self.pending_events.get(source_uid, {})
        effects, _ = self._execute_rule(
            player_index, source, rule, target_uid=target_uid,
            event=pending_event,
        )
        player["abilitiesUsed"].add(rule_id)
        self._trace_rule(
            player_index=player_index, source=source, rule=rule, effects=effects,
            summary=f"Ability {rule['sourceName']} resolved.",
        )
        self.log.append(f"{source['card']['name']} used {rule['sourceName']}. Rule {rule_id} executed.")

    def _play_stadium(self, player_index: int, card_uid: str, rule_id: str) -> None:
        player = self.players[player_index]
        item = self._find_hand(player, card_uid)
        if "Stadium" not in item["card"]["subtypes"]:
            raise ValueError("That card is not a Stadium")
        if player["stadiumPlayed"]:
            raise ValueError("Only one Stadium can be played each turn")
        if self.stadium and self.stadium["card"]["name"] == item["card"]["name"]:
            raise ValueError("A Stadium with the same name is already in play")
        player["hand"].remove(item)
        if self.stadium:
            previous_owner = int(self.stadium.get("owner", 0))
            self.players[previous_owner]["discard"].append(self.stadium)
        item["owner"] = player_index
        self.stadium = item
        player["stadiumPlayed"] = True
        rule = next((rule for rule in item["card"]["ruleProgram"]["rules"] if rule["id"] == rule_id), None)
        if rule:
            self._trace_rule(
                player_index=player_index, source=item, rule=rule, effects=rule.get("effects", []),
                summary=f"{item['card']['name']} entered the Stadium zone.",
            )
        self.log.append(f"{player['name']} played {item['card']['name']} into the Stadium zone.")

    def _attach_tool(self, player_index: int, card_uid: str, target_uid: str, rule_id: str) -> None:
        player = self.players[player_index]
        item = self._find_hand(player, card_uid)
        target = self._find_pokemon(player, target_uid)
        if target.get("tools"):
            raise ValueError("That Pokémon already has a Pokémon Tool")
        player["hand"].remove(item)
        target["tools"].append(item)
        rule = next((rule for rule in item["card"]["ruleProgram"]["rules"] if rule["id"] == rule_id), None)
        if rule:
            self._trace_rule(
                player_index=player_index, source=item, rule=rule, effects=rule.get("effects", []),
                summary=f"{item['card']['name']} was attached to {target['card']['name']}.",
            )
        self.log.append(f"{player['name']} attached {item['card']['name']} to {target['card']['name']}.")

    def legal_actions(self, player_index: int = 0) -> list[dict[str, Any]]:
        if self.winner is not None:
            return []
        if self.phase == "coin_call":
            return [
                {"type": "call_coin", "choice": "heads", "label": "Call heads"},
                {"type": "call_coin", "choice": "tails", "label": "Call tails"},
            ]
        if self.phase == "choose_turn_order":
            return [
                {"type": "choose_turn_order", "order": "first", "label": "Go first"},
                {"type": "choose_turn_order", "order": "second", "label": "Go second"},
            ]
        if self.phase == "mulligan_draw":
            return [
                {
                    "type": "mulligan_draw",
                    "count": count,
                    "label": "Do not draw" if count == 0 else f"Draw {count} extra",
                }
                for count in range(self.mulligan_draws_available + 1)
            ]
        if self.phase == "choose_active":
            return [
                {"type": "choose_active", "cardUid": item["uid"], "label": f"Active: {item['card']['name']}"}
                for item in self.players[0]["hand"] if _is_basic(item)
            ]
        if self.phase == "choose_bench":
            actions: list[dict[str, Any]] = []
            if len(self.players[0]["bench"]) < MAX_BENCH:
                actions.extend(
                    {"type": "setup_bench", "cardUid": item["uid"], "label": f"Bench {item['card']['name']}"}
                    for item in self.players[0]["hand"] if _is_basic(item)
                )
            actions.append({"type": "finish_setup", "label": "Finish setup"})
            return actions
        if self.phase != "playing":
            return []
        if self.pending_promotion == player_index:
            return [
                {"type": "promote", "targetUid": target["uid"], "label": f"Promote {target['card']['name']}"}
                for target in self.players[player_index]["bench"]
            ]
        if self.current_player != player_index or self.pending_promotion is not None:
            return []
        player = self.players[player_index]
        actions: list[dict[str, Any]] = [{"type": "end_turn", "label": "End turn"}]
        if len(player["bench"]) < MAX_BENCH:
            actions.extend(
                {"type": "bench", "cardUid": item["uid"], "label": f"Bench {item['card']['name']}"}
                for item in player["hand"] if _is_basic(item)
            )
        if not player["energyAttached"]:
            for item in (card for card in player["hand"] if _is_energy(card)):
                for target in [player["active"], *player["bench"]]:
                    if target:
                        actions.append({"type": "attach", "cardUid": item["uid"], "targetUid": target["uid"], "label": f"Attach {item['card']['name']} → {target['card']['name']}"})
        if player["turnsTaken"] > 1:
            for item in player["hand"]:
                evolves_from = item["card"].get("evolvesFrom")
                if not evolves_from:
                    continue
                for target in [player["active"], *player["bench"]]:
                    if target and target["card"]["name"] == evolves_from and (
                        target["enteredTurn"] < player["turnsTaken"] or self._same_turn_evolution_allowed(target, item)
                    ):
                        actions.append({"type": "evolve", "cardUid": item["uid"], "targetUid": target["uid"], "label": f"Evolve {evolves_from} → {item['card']['name']}"})
        active = player["active"]
        if active:
            for index, attack in enumerate(active["card"]["attacks"]):
                rule = self._program_rule(active["card"], "attack", str(attack.get("name")))
                restricted = first_turn_restricted(
                    player_index=player_index,
                    first_player=self.first_player,
                    turns_taken=player["turnsTaken"],
                )
                first_turn_override = bool(rule and any(
                    effect.get("op") == "create_modifier"
                    and effect.get("target") == "current_attack.usable_on_first_turn_when_going_first"
                    for effect in rule.get("effects", [])
                ))
                if not self._can_pay_attack(player_index, active, list(attack.get("cost") or [])) or (restricted and not first_turn_override):
                    continue
                base = {
                    "type": "attack",
                    "attackIndex": index,
                    "coverage": "complete" if rule else "partial",
                    "ruleId": rule["id"] if rule else None,
                }
                bench_target = bool(rule and any(effect.get("target") == "chosen_opponent_benched_pokemon" for effect in rule.get("effects", [])))
                optional_switch = bool(rule and any(effect.get("op") == "switch_active" and effect.get("target") == "self" for effect in rule.get("effects", [])))
                target_options = self.players[1 - player_index]["bench"] if bench_target else [None]
                switch_options = [None, *player["bench"]] if optional_switch else [None]
                for target in target_options:
                    for switch_target in switch_options:
                        action = dict(base)
                        if target:
                            action["targetUid"] = target["uid"]
                        if switch_target:
                            action["switchTargetUid"] = switch_target["uid"]
                        qualifiers = []
                        if target:
                            qualifiers.append(target["card"]["name"])
                        if switch_target:
                            qualifiers.append(f"switch to {switch_target['card']['name']}")
                        suffix = f" → {', '.join(qualifiers)}" if qualifiers else ""
                        fallback = " (base damage only)" if not rule else ""
                        action["label"] = f"Attack: {attack['name']}{suffix}{fallback}"
                        actions.append(action)
        retreat_locked = bool(active and any(
            modifier["effect"].get("target") == "opponent.active.retreat_allowed"
            and modifier["owner"] != player_index
            and modifier["targetUid"] == active["uid"]
            and modifier["effect"].get("value") == "false"
            and (modifier["expiresAfter"] is None or self.turn_number <= modifier["expiresAfter"])
            for modifier in self.modifiers
        ))
        if active and player["bench"] and not player["retreated"] and not retreat_locked and len(active["energy"]) >= len(active["card"]["retreatCost"]):
            actions.extend(
                {"type": "retreat", "targetUid": target["uid"], "label": f"Retreat → {target['card']['name']}"}
                for target in player["bench"]
            )
        actions.extend(self._trainer_actions(player_index))
        for pokemon in self._in_play(player):
            for rule in (pokemon["card"].get("ruleProgram") or {}).get("rules", []):
                if rule.get("trigger") != "activate_ability" or rule["id"] in player["abilitiesUsed"]:
                    continue
                if not executable_effects(
                    pokemon["card"]["ruleProgram"], rule["id"],
                    self._rule_context(
                        player_index, source=pokemon,
                        event=self.pending_events.get(pokemon["uid"], {}),
                    ), seed=self.seed,
                ):
                    continue
                targets_self = any(str(effect.get("target", "")).startswith("chosen_self_pokemon") for effect in rule.get("effects", []))
                candidates = self._in_play(player) if targets_self else [None]
                for target in candidates:
                    action = {
                        "type": "activate_ability", "sourceUid": pokemon["uid"],
                        "ruleId": rule["id"], "coverage": "complete",
                        "label": f"Ability: {rule['sourceName']}",
                    }
                    if target:
                        action["targetUid"] = target["uid"]
                        action["label"] += f" → {target['card']['name']}"
                    actions.append(action)
        if self.stadium:
            for rule in (self.stadium["card"].get("ruleProgram") or {}).get("rules", []):
                if rule.get("trigger") != "activate_ability" or rule["id"] in player["abilitiesUsed"]:
                    continue
                requires_hand_energy = any(
                    effect.get("op") == "discard_energy" and effect.get("target") == "self.hand"
                    for effect in rule.get("effects", [])
                )
                if requires_hand_energy and not any(_is_energy(item) for item in player["hand"]):
                    continue
                if executable_effects(
                    self.stadium["card"]["ruleProgram"], rule["id"],
                    self._rule_context(player_index, source=self.stadium), seed=self.seed,
                ):
                    actions.append({
                        "type": "activate_ability", "sourceUid": self.stadium["uid"],
                        "ruleId": rule["id"], "coverage": "complete",
                        "label": f"Stadium: {rule['sourceName']}",
                    })
        return actions

    def apply(self, action: dict[str, Any]) -> None:
        self._commit_clock()
        if self.winner is not None:
            return
        if action.get("type") == "timeout":
            self.clock_seconds[0] = 0
            self.winner = 1
            self.reason = "time_expired"
            self.phase = "finished"
            self.clock_owner = None
            self.log.append(f"{self.players[0]['name']} ran out of time and loses.")
            return
        if self.phase == "playing" and self.current_player != 0 and self.pending_promotion != 0:
            raise ValueError("Wait for the opponent's turn to finish")
        allowed = self.legal_actions(0)
        signature = {key: value for key, value in action.items() if key != "label"}
        if not any(
            {key: value for key, value in candidate.items() if key != "label"} == signature
            for candidate in allowed
        ):
            raise ValueError("That action is not legal in the current state")
        player = self.players[0]
        kind = str(action.get("type"))
        if kind == "call_coin":
            self._call_coin(str(action["choice"]))
        elif kind == "choose_turn_order":
            self._choose_turn_order(str(action["order"]))
        elif kind == "mulligan_draw":
            self._resolve_mulligan_draw(int(action["count"]))
        elif kind == "choose_active":
            self._choose_active(str(action["cardUid"]))
        elif kind == "setup_bench":
            self._bench(player, str(action["cardUid"]))
        elif kind == "finish_setup":
            self._finish_setup()
        elif kind == "bench":
            self._bench(player, str(action["cardUid"]))
        elif kind == "attach":
            self._attach(player, str(action["cardUid"]), str(action["targetUid"]))
        elif kind == "evolve":
            self._evolve(player, str(action["cardUid"]), str(action["targetUid"]))
        elif kind == "retreat":
            self._retreat(player, str(action["targetUid"]))
        elif kind == "play_trainer":
            self._play_trainer(0, str(action["cardUid"]), action.get("targetUid"))
        elif kind == "play_stadium":
            self._play_stadium(0, str(action["cardUid"]), str(action["ruleId"]))
        elif kind == "attach_tool":
            self._attach_tool(0, str(action["cardUid"]), str(action["targetUid"]), str(action["ruleId"]))
        elif kind == "activate_ability":
            self._activate_ability(0, str(action["sourceUid"]), str(action["ruleId"]), action.get("targetUid"))
        elif kind == "attack":
            self._attack(0, action)
            self._finish_turn()
        elif kind == "promote":
            self._promote(0, str(action["targetUid"]))
            self._finish_turn()
        elif kind == "end_turn":
            self.log.append(f"{player['name']} ended the turn.")
            self._finish_turn()

    def _run_ai_turn(self) -> None:
        ai = self.players[1]
        if self.winner is not None:
            return
        for item in list(ai["hand"]):
            if _is_basic(item) and len(ai["bench"]) < MAX_BENCH:
                self._bench(ai, item["uid"])
        if ai["turnsTaken"] > 1:
            for item in list(ai["hand"]):
                evolves_from = item["card"].get("evolvesFrom")
                if not evolves_from:
                    continue
                target = next((pokemon for pokemon in [ai["active"], *ai["bench"]] if pokemon and pokemon["card"]["name"] == evolves_from and pokemon["enteredTurn"] < ai["turnsTaken"]), None)
                if target:
                    self._evolve(ai, item["uid"], target["uid"])
        if not ai["energyAttached"]:
            energy = next((item for item in ai["hand"] if _is_energy(item)), None)
            if energy and ai["active"]:
                self._attach(ai, energy["uid"], ai["active"]["uid"])
        active = ai["active"]
        attack_actions = [action for action in self.legal_actions(1) if action["type"] == "attack"]
        restricted = first_turn_restricted(
            player_index=1,
            first_player=self.first_player,
            turns_taken=ai["turnsTaken"],
        )
        if attack_actions:
            attack_action = max(
                attack_actions,
                key=lambda item: (
                    _printed_damage(active["card"]["attacks"][int(item["attackIndex"])]),
                    active["card"]["attacks"][int(item["attackIndex"])].get("name", ""),
                ),
            )
            self._attack(1, attack_action)
        elif restricted:
            self.log.append(f"{ai['name']} cannot attack on the first player's first turn.")
        else:
            self.log.append(f"{ai['name']} could not attack.")
        self._finish_turn()
        if self.pending_promotion == 0:
            self._switch_clock(0)

    def public_state(self) -> dict[str, Any]:
        self._commit_clock()
        player, opponent = self.players
        status = "finished" if self.winner is not None else ("setup" if self.phase in SETUP_PHASES else "playing")
        prompt = setup_prompt(self.phase, mulligan_draws=self.mulligan_draws_available)
        return {
            "sessionId": self.id,
            "arenaVersion": ARENA_VERSION,
            "aiPolicyVersion": AI_POLICY_VERSION,
            "coreRulesVersion": CORE_RULES_VERSION,
            "officialRulebook": OFFICIAL_RULEBOOK_URL,
            "seed": self.seed,
            "status": status,
            "phase": self.phase,
            "prompt": prompt,
            "setup": {
                "coinCall": self.coin_call,
                "coinResult": self.coin_result,
                "coinWinner": None if self.coin_winner is None else ("player" if self.coin_winner == 0 else "opponent"),
                "firstPlayer": None if self.first_player is None else ("player" if self.first_player == 0 else "opponent"),
                "playerMulligans": self.mulligans[0],
                "opponentMulligans": self.mulligans[1],
                "bonusDrawsAvailable": self.mulligan_draws_available,
            },
            "turn": self.turn_number,
            "isPlayerTurn": (self.phase in SETUP_PHASES or self.current_player == 0 or self.pending_promotion == 0) and self.winner is None,
            "winner": None if self.winner is None else ("player" if self.winner == 0 else ("opponent" if self.winner == 1 else "tie")),
            "reason": self.reason,
            "clocks": {
                "initialMs": MATCH_CLOCK_SECONDS * 1000,
                "playerMs": round(self.clock_seconds[0] * 1000),
                "opponentMs": round(self.clock_seconds[1] * 1000),
                "active": None if self.winner is not None or self.clock_owner is None else ("player" if self.clock_owner == 0 else "opponent"),
            },
            "player": self._public_player(player, reveal_hand=True),
            "opponent": self._public_player(opponent, reveal_hand=False),
            "stadium": None if self.stadium is None else _card_view(self.stadium),
            "legalActions": self.legal_actions(0),
            "log": self.log[-20:],
            "ruleTrace": self.rule_trace[-30:],
            "ruleCoverage": list(RULE_COVERAGE),
            "limitations": [
                "Arena 0.4 executes the official pregame sequence and loads the latest current-source AI-passed rule program for every processed card.",
                "The opponent uses a deterministic setup-and-attack policy; it does not search future turns.",
                "Every executed compiled rule records its immutable version, rule ID, operations, review status, and outcome in the match trace. Unprocessed cards explicitly fall back to printed attack damage only.",
                "Compiled choices currently use deterministic Arena policy unless the legal action contains an explicit target. The choice protocol will become fully interactive without changing rule identities.",
                "Special Conditions can be created and cleared by card programs; the between-turn Pokémon Checkup sequence is the remaining universal-rules gap.",
                "A simultaneous final-Prize result is identified as requiring Sudden Death; automatically starting the one-Prize rematch is not implemented yet.",
                "Sessions are local memory and end when the application server restarts.",
            ],
        }

    @staticmethod
    def _public_player(player: dict[str, Any], *, reveal_hand: bool) -> dict[str, Any]:
        result = {
            "name": player["name"],
            "deckCount": len(player["deck"]),
            "handCount": len(player["hand"]),
            "prizesRemaining": len(player["prizes"]),
            "discardCount": len(player["discard"]),
            "active": _pokemon_view(player["active"]),
            "bench": [_pokemon_view(item) for item in player["bench"]],
        }
        if reveal_hand:
            result["hand"] = [_card_view(item) for item in player["hand"]]
        return result


def start_arena_session(payload: dict[str, Any], url: str | None = None) -> dict[str, Any]:
    deck_id = str(payload["deckId"])
    opponent_type = str(payload.get("opponentType") or "Fire")
    seed = int(payload.get("seed", 1))
    optimized = optimize_deck({"format": "standard", "type": opponent_type, "seed": seed}, url)
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        player_name, player_deck = _load_saved_deck(connection, deck_id)
        opponent_deck = _load_cards(connection, optimized["cards"], "opponent")
    session = ArenaSession(
        player_name=player_name,
        player_deck=player_deck,
        opponent_name=f"{opponent_type} sparring deck",
        opponent_deck=opponent_deck,
        seed=seed,
    )
    with _SESSION_LOCK:
        if len(_SESSIONS) >= 100:
            _SESSIONS.pop(next(iter(_SESSIONS)))
        _SESSIONS[session.id] = session
    return session.public_state()


def get_arena_session(session_id: str) -> dict[str, Any]:
    with _SESSION_LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            raise ValueError("Arena session was not found or the server restarted")
        return session.public_state()


def apply_arena_action(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with _SESSION_LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            raise ValueError("Arena session was not found or the server restarted")
        session.apply(payload)
        return session.public_state()


def report_arena_rule(session_id: str, payload: dict[str, Any], url: str | None = None) -> dict[str, Any]:
    from .rule_reviews import report_rule_problem

    trace_id = int(payload["traceId"])
    with _SESSION_LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            raise ValueError("Arena session was not found or the server restarted")
        trace = next((item for item in session.rule_trace if item["traceId"] == trace_id), None)
        if trace is None:
            raise ValueError("That rule execution is no longer in the match trace")
        return report_rule_problem(
            rule_version_id=trace["ruleVersionId"],
            card_id=trace["cardId"],
            rule_id=trace["ruleId"],
            arena_version=ARENA_VERSION,
            session_id=session_id,
            trace=trace,
            reason=str(payload.get("reason") or "Incorrect card behavior"),
            detail=str(payload.get("detail") or ""),
            reporter=str(payload.get("reporter") or "arena-player"),
            url=url,
        )
