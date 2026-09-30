from __future__ import annotations

import re
from typing import Any


PARSER_VERSION = "0.2.0"


def _number(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group()) if match else None


def parse_operations(text: str) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    lowered = text.lower()
    patterns = (
        (r"draw (\d+) cards?", "draw", "count"),
        (r"draw (?:a|one) card", "draw", "count"),
        (r"heal (\d+) damage", "heal", "amount"),
        (r"discard (\d+) energ", "discard_energy", "count"),
        (r"discard (?:an|one) energ", "discard_energy", "count"),
        (r"attach (?:up to )?(\d+) .*energy", "attach_energy", "count"),
        (r"attach (?:a|an|one) .*energy", "attach_energy", "count"),
        (r"search your deck for (?:up to )?(\d+)", "search_deck", "count"),
        (r"search your deck for (?:a|an|one) ", "search_deck", "count"),
    )
    for pattern, kind, field in patterns:
        for match in re.finditer(pattern, lowered):
            operations.append({"op": kind, field: int(match.group(1)) if match.lastindex else 1})
    if "flip a coin" in lowered:
        operations.append({"op": "coin_flip", "count": 1})
    if "switch" in lowered and "active" in lowered:
        operations.append({"op": "switch_active"})
    if "paralyzed" in lowered:
        operations.append({"op": "apply_condition", "condition": "paralyzed"})
    if "poisoned" in lowered:
        operations.append({"op": "apply_condition", "condition": "poisoned"})
    if "asleep" in lowered:
        operations.append({"op": "apply_condition", "condition": "asleep"})
    return operations


def parse_card_effects(card: dict[str, Any]) -> dict[str, Any]:
    effects: list[dict[str, Any]] = []
    recognized = 0
    sources = 0
    for source_name, entries in (("ability", card.get("abilities", [])), ("attack", card.get("attacks", []))):
        for entry in entries:
            sources += 1
            text = entry.get("text", "")
            operations = parse_operations(text)
            damage = _number(entry.get("damage", "")) if source_name == "attack" else None
            if operations or damage is not None:
                recognized += 1
            effects.append({
                "source": source_name,
                "name": entry.get("name"),
                "cost": entry.get("cost", []),
                "damage": {
                    "printed": entry.get("damage", ""),
                    "base": damage,
                } if source_name == "attack" else None,
                "operations": operations,
                "rawText": text,
            })
    if sources == 0:
        status = "parsed"
    elif recognized == sources:
        status = "parsed"
    elif recognized:
        status = "partial"
    else:
        status = "unparsed"
    return {
        "schemaVersion": 1,
        "parserVersion": PARSER_VERSION,
        "status": status,
        "effects": effects,
    }


def maximum_attack_damage(parsed: dict[str, Any]) -> int:
    damages = [
        effect.get("damage", {}).get("base") or 0
        for effect in parsed.get("effects", [])
        if effect.get("source") == "attack" and effect.get("damage")
    ]
    return max(damages, default=0)


def draw_value(parsed: dict[str, Any]) -> int:
    return sum(
        operation.get("count", 0)
        for effect in parsed.get("effects", [])
        for operation in effect.get("operations", [])
        if operation.get("op") == "draw"
    )
