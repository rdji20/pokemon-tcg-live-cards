from __future__ import annotations

import random
import re
import threading
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


ARENA_VERSION = "arena-0.2.0"
AI_POLICY_VERSION = "simple-ai-0.1.0"
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
    }


def _load_cards(connection: psycopg.Connection[Any], entries: list[dict[str, Any]], owner: str) -> list[dict[str, Any]]:
    ids = [str(item["card_id"]) for item in entries]
    rows = connection.execute(
        """
        SELECT id, name, supertype, subtypes, types, hp, image_small,
               image_large, raw_data
        FROM cards
        WHERE active AND standard_status = 'legal' AND id = ANY(%s)
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
        raise ValueError("Arena 0.2 supports Standard decks only")
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
        "enteredTurn": entered_turn,
    }


def _card_view(instance: dict[str, Any]) -> dict[str, Any]:
    return {"uid": instance["uid"], **instance["card"]}


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
        self.log: list[str] = []
        self.players = [
            self._new_player(player_name, player_deck),
            self._new_player(opponent_name, opponent_deck),
        ]
        self.log.append("Decks are ready. Call heads or tails for the opening coin flip.")

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
        self._set_up_in_play(self.players[1])
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

    def _evolve(self, player: dict[str, Any], card_uid: str, target_uid: str) -> None:
        if player["turnsTaken"] <= 1:
            raise ValueError("Pokémon cannot evolve on a player's first turn")
        item = self._find_hand(player, card_uid)
        target = self._find_pokemon(player, target_uid)
        if target["enteredTurn"] >= player["turnsTaken"]:
            raise ValueError("A Pokémon cannot evolve during the turn it was played")
        if item["card"].get("evolvesFrom") != target["card"]["name"]:
            raise ValueError("That Evolution card does not evolve from the selected Pokémon")
        player["hand"].remove(item)
        target["stack"].append(item)
        target["card"] = item["card"]
        target["uid"] = item["uid"]
        target["enteredTurn"] = player["turnsTaken"]
        self.log.append(f"{player['name']} evolved into {item['card']['name']}.")

    def _retreat(self, player: dict[str, Any], target_uid: str) -> None:
        if player["retreated"]:
            raise ValueError("A player may retreat only once each turn")
        active = player["active"]
        target = next((item for item in player["bench"] if item["uid"] == target_uid), None)
        if active is None or target is None:
            raise ValueError("Choose a Benched Pokémon to retreat into")
        cost = len(active["card"]["retreatCost"])
        if len(active["energy"]) < cost:
            raise ValueError("The Active Pokémon does not have enough Energy to retreat")
        for _ in range(cost):
            player["discard"].append(active["energy"].pop())
        player["bench"].remove(target)
        player["bench"].append(active)
        player["active"] = target
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
        rules = " ".join(knocked_out["card"].get("rules") or [])
        prize_match = re.search(r"takes\s+(\d+)\s+Prize", rules, re.IGNORECASE)
        prize_count = int(prize_match.group(1)) if prize_match else 1
        defender["discard"].extend(knocked_out["stack"])
        defender["discard"].extend(knocked_out["energy"])
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

    def _attack(self, attacker_index: int, attack_index: int) -> None:
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
        if not _can_pay(list(attack.get("cost") or []), active["energy"]):
            raise ValueError("The Active Pokémon does not have the Energy required for that attack")
        damage = _printed_damage(attack)
        text = str(attack.get("text") or "").strip()
        supported_text = not text or bool(
            re.fullmatch(r"Draw (?:(?:a|1) card|\d+ cards?)\.?", text, re.IGNORECASE)
        )
        attack_type = (active["card"]["types"] or [""])[0]
        weakness = next((item for item in defender["active"]["card"]["weaknesses"] if item.get("type") == attack_type), None)
        resistance = next((item for item in defender["active"]["card"]["resistances"] if item.get("type") == attack_type), None)
        if weakness and str(weakness.get("value", "")).startswith("×"):
            damage *= int(str(weakness["value"])[1:] or 1)
        if resistance and str(resistance.get("value", "")).startswith("-"):
            damage = max(0, damage - int(str(resistance["value"])[1:] or 0))
        defender["active"]["damage"] += damage
        coverage_note = "" if supported_text else " Card text was not executed in Arena 0.2."
        self.log.append(f"{active['card']['name']} used {attack['name']} for {damage} damage.{coverage_note}")
        draw_match = re.fullmatch(r"Draw (?:a|1) card\.?", text, re.IGNORECASE)
        draw_many = re.fullmatch(r"Draw (\d+) cards?\.?", text, re.IGNORECASE)
        if draw_match:
            self._draw(attacker)
        elif draw_many:
            self._draw(attacker, int(draw_many.group(1)))
        if defender["active"]["damage"] >= defender["active"]["card"]["hp"]:
            self._knock_out(attacker_index, defender_index)

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
            if card["name"].startswith("Professor's Research"):
                actions.append({"type": "play_trainer", "cardUid": item["uid"], "label": f"Play {card['name']}"})
            elif card["name"] == "Boss's Orders":
                for target in opponent["bench"]:
                    actions.append({"type": "play_trainer", "cardUid": item["uid"], "targetUid": target["uid"], "label": f"Boss's Orders → {target['card']['name']}"})
            else:
                rules = " ".join(card["rules"]).strip()
                if re.fullmatch(r"Draw (?:a|\d+) cards?\.?", rules, re.IGNORECASE):
                    actions.append({"type": "play_trainer", "cardUid": item["uid"], "label": f"Play {card['name']}"})
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
        if card["name"].startswith("Professor's Research"):
            player["discard"].extend(player["hand"])
            player["hand"] = []
            self._draw(player, 7)
        elif card["name"] == "Boss's Orders":
            target = next((candidate for candidate in opponent["bench"] if candidate["uid"] == target_uid), None)
            if target is None:
                raise ValueError("Choose an opposing Benched Pokémon")
            opponent["bench"].remove(target)
            opponent["bench"].append(opponent["active"])
            opponent["active"] = target
        else:
            rules = " ".join(card["rules"]).strip()
            match = re.fullmatch(r"Draw (a|\d+) cards?\.?", rules, re.IGNORECASE)
            if not match:
                raise ValueError("That Trainer's effect is not supported in Arena 0.2")
            self._draw(player, 1 if match.group(1).lower() == "a" else int(match.group(1)))
        player["discard"].append(item)
        self.log.append(f"{player['name']} played {card['name']}.")

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
                    if target and target["card"]["name"] == evolves_from and target["enteredTurn"] < player["turnsTaken"]:
                        actions.append({"type": "evolve", "cardUid": item["uid"], "targetUid": target["uid"], "label": f"Evolve {evolves_from} → {item['card']['name']}"})
        active = player["active"]
        if active and not first_turn_restricted(
            player_index=player_index,
            first_player=self.first_player,
            turns_taken=player["turnsTaken"],
        ):
            for index, attack in enumerate(active["card"]["attacks"]):
                if _can_pay(list(attack.get("cost") or []), active["energy"]):
                    text = str(attack.get("text") or "").strip()
                    supported_text = not text or bool(
                        re.fullmatch(r"Draw (?:(?:a|1) card|\d+ cards?)\.?", text, re.IGNORECASE)
                    )
                    suffix = "" if supported_text else " (base damage only)"
                    actions.append({
                        "type": "attack",
                        "attackIndex": index,
                        "coverage": "complete" if supported_text else "partial",
                        "label": f"Attack: {attack['name']}{suffix}",
                    })
        if active and player["bench"] and not player["retreated"] and len(active["energy"]) >= len(active["card"]["retreatCost"]):
            actions.extend(
                {"type": "retreat", "targetUid": target["uid"], "label": f"Retreat → {target['card']['name']}"}
                for target in player["bench"]
            )
        actions.extend(self._trainer_actions(player_index))
        return actions

    def apply(self, action: dict[str, Any]) -> None:
        if self.winner is not None:
            raise ValueError("This match is already over")
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
        elif kind == "attack":
            self._attack(0, int(action["attackIndex"]))
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
        affordable = [] if active is None else [
            (index, attack) for index, attack in enumerate(active["card"]["attacks"])
            if _can_pay(list(attack.get("cost") or []), active["energy"])
        ]
        restricted = first_turn_restricted(
            player_index=1,
            first_player=self.first_player,
            turns_taken=ai["turnsTaken"],
        )
        if affordable and not restricted:
            attack_index, _ = max(affordable, key=lambda item: (_printed_damage(item[1]), item[1].get("name", "")))
            self._attack(1, attack_index)
        elif restricted:
            self.log.append(f"{ai['name']} cannot attack on the first player's first turn.")
        else:
            self.log.append(f"{ai['name']} could not attack.")
        self._finish_turn()

    def public_state(self) -> dict[str, Any]:
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
            "winner": None if self.winner is None else ("player" if self.winner == 0 else "opponent"),
            "reason": self.reason,
            "player": self._public_player(player, reveal_hand=True),
            "opponent": self._public_player(opponent, reveal_hand=False),
            "legalActions": self.legal_actions(0),
            "log": self.log[-20:],
            "ruleCoverage": list(RULE_COVERAGE),
            "limitations": [
                "Arena 0.2 executes the official pregame sequence plus core turns, Energy, evolution, retreat, attacks, Weakness, Resistance, Knock Outs, Prizes, and win conditions.",
                "The opponent uses a deterministic setup-and-attack policy; it does not search future turns.",
                "Only simple draw Trainers, Professor's Research, Boss's Orders, and exact draw attack text execute. An attack with other text is labeled base damage only; the omitted effect is never presented as executed.",
                "Special Conditions and Pokémon Checkup are not executable yet because no supported card program can currently create those states.",
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
