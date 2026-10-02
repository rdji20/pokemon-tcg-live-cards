from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any


SCHEMA_VERSION = 1
ALLOWED_TRIGGERS = {
    "activate_ability", "attack", "continuous", "on_knockout", "play_card",
    "would_be_knocked_out",
}
ALLOWED_OPERATIONS = {
    "choose_cards", "clear_special_conditions", "confuse", "create_modifier", "deal_damage",
    "discard_cards", "discard_energy", "draw_cards", "flip_coin",
    "heal_damage", "inspect_top_deck", "knockout", "move_cards", "move_energy",
    "request_choice", "set_prize_value", "shuffle_cards", "shuffle_zone_into_deck",
    "swap_cards", "switch_active",
}
ALLOWED_OPERATORS = {"equals", "not_equals", "at_least", "at_most", "exists", "contains", "not_contains"}


def program_hash(program: dict[str, Any]) -> str:
    """Return the immutable identity of executable semantics, excluding provenance."""
    executable = {
        "schemaVersion": program.get("schemaVersion"),
        "cardId": program.get("cardId"),
        "sourceTextHash": program.get("sourceTextHash"),
        "rules": program.get("rules", []),
        "unsupportedText": program.get("unsupportedText", []),
    }
    canonical = json.dumps(executable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def matching_rule(
    program: dict[str, Any] | None,
    *,
    trigger: str,
    source_name: str | None = None,
    source_type: str | None = None,
) -> dict[str, Any] | None:
    if not program:
        return None
    for rule in program.get("rules", []):
        if rule.get("trigger") != trigger:
            continue
        if source_name is not None and rule.get("sourceName") != source_name:
            continue
        if source_type is not None and rule.get("sourceType") != source_type:
            continue
        return rule
    return None


def card_source_text(card: dict[str, Any]) -> str:
    payload = {
        "id": card["id"],
        "abilities": card.get("abilities", []),
        "attacks": card.get("attacks", []),
        "rules": card.get("rules", []),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_text_hash(card: dict[str, Any]) -> str:
    return hashlib.sha256(card_source_text(card).encode("utf-8")).hexdigest()


def validate_program(program: dict[str, Any], card: dict[str, Any] | None = None) -> dict[str, Any]:
    errors: list[str] = []
    if program.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"schemaVersion must be {SCHEMA_VERSION}")
    if card and program.get("cardId") != card.get("id"):
        errors.append("cardId does not match source card")
    if card and program.get("sourceTextHash") != source_text_hash(card):
        errors.append("sourceTextHash does not match source card text")
    rules = program.get("rules")
    if not isinstance(rules, list):
        errors.append("rules must be an array")
        rules = []
    seen_ids: set[str] = set()
    for index, rule in enumerate(rules):
        prefix = f"rules[{index}]"
        rule_id = rule.get("id")
        if not rule_id or rule_id in seen_ids:
            errors.append(f"{prefix}.id must be present and unique")
        seen_ids.add(rule_id)
        if rule.get("trigger") not in ALLOWED_TRIGGERS:
            errors.append(f"{prefix}.trigger is unsupported")
        for condition in rule.get("conditions", []):
            if condition.get("operator") not in ALLOWED_OPERATORS:
                errors.append(f"{prefix} contains an unsupported condition operator")
        for effect in rule.get("effects", []):
            if effect.get("op") not in ALLOWED_OPERATIONS:
                errors.append(f"{prefix} contains unsupported operation {effect.get('op')}")
            for condition in effect.get("conditions", []):
                if condition.get("operator") not in ALLOWED_OPERATORS:
                    errors.append(f"{prefix} effect contains an unsupported condition operator")
    unsupported = program.get("unsupportedText", [])
    if unsupported:
        errors.append("program still contains unsupported source text")
    has_source_rules = bool(card and any(card.get(key) for key in ("abilities", "attacks", "rules")))
    if not rules and (has_source_rules or card is None):
        errors.append("card has rule text but program contains no rules")
    if card:
        for ability in card.get("abilities", []):
            if not any(
                rule.get("sourceType") == "ability" and rule.get("sourceName") == ability.get("name")
                for rule in rules
            ):
                errors.append(f"ability is not represented: {ability.get('name')}")
        for attack in card.get("attacks", []):
            matching = [
                rule for rule in rules
                if rule.get("sourceType") == "attack" and rule.get("sourceName") == attack.get("name")
            ]
            if not matching:
                errors.append(f"attack is not represented: {attack.get('name')}")
                continue
            if matching[0].get("cost", []) != attack.get("cost", []):
                errors.append(f"attack cost does not match source: {attack.get('name')}")
            damage_match = re.search(r"\d+", attack.get("damage", ""))
            if damage_match:
                damage = int(damage_match.group())
                if not any(
                    effect.get("op") == "deal_damage" and effect.get("amount") == damage
                    for effect in matching[0].get("effects", [])
                ):
                    errors.append(f"printed damage is not represented: {attack.get('name')}")
        expected_rule_boxes = len(card.get("rules", []))
        represented_rule_boxes = sum(
            rule.get("sourceType") in {"rule_box", "trainer_rule"}
            for rule in rules
        )
        if represented_rule_boxes < expected_rule_boxes:
            errors.append("one or more printed card rules are not represented")
    return {
        "passed": not errors,
        "errors": errors,
        "ruleCount": len(rules),
        "operationCount": sum(len(rule.get("effects", [])) for rule in rules),
    }


def _lookup(context: dict[str, Any], field: str) -> Any:
    value: Any = context
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _condition_matches(condition: dict[str, Any], context: dict[str, Any]) -> bool:
    actual = _lookup(context, condition["field"])
    expected = condition.get("value", "")
    operator = condition["operator"]
    if operator == "exists":
        if isinstance(actual, (str, list, tuple, set, dict)):
            return bool(actual)
        return actual is not None
    if operator == "contains":
        return expected in (actual or [])
    if operator == "not_contains":
        return expected not in (actual or [])
    if operator == "at_least":
        return float(actual or 0) >= float(expected)
    if operator == "at_most":
        return float(actual or 0) <= float(expected)
    if operator == "not_equals":
        return str(actual).lower() != str(expected).lower()
    return str(actual).lower() == str(expected).lower()


def executable_effects(
    program: dict[str, Any],
    rule_id: str,
    context: dict[str, Any],
    *,
    seed: int = 1,
) -> list[dict[str, Any]]:
    """Resolve a validated rule into deterministic primitive effects.

    Choice operations remain explicit actions for the game policy to resolve.
    """
    rule = next(rule for rule in program["rules"] if rule["id"] == rule_id)
    if not all(_condition_matches(item, context) for item in rule.get("conditions", [])):
        return []
    resolved_context = dict(context)
    result: list[dict[str, Any]] = []
    rng = random.Random(seed)
    for effect in rule.get("effects", []):
        if not all(_condition_matches(item, resolved_context) for item in effect.get("conditions", [])):
            continue
        item = dict(effect)
        if effect["op"] == "flip_coin":
            target = effect.get("target") or "coin"
            amount = max(1, int(effect.get("amount") or 1))
            if effect.get("value") == "until_tails":
                outcomes = []
                while not outcomes or outcomes[-1] != "tails":
                    outcomes.append("heads" if rng.randrange(2) == 0 else "tails")
            elif amount > 1:
                outcomes = ["heads" if rng.randrange(2) == 0 else "tails" for _ in range(amount)]
            else:
                outcome = "heads" if rng.randrange(2) == 0 else "tails"
                resolved_context[target] = outcome
                item["result"] = outcome
                result.append(item)
                continue
            heads = outcomes.count("heads")
            resolved_context[target] = {"results": outcomes, "heads": heads}
            item["results"] = outcomes
            item["heads"] = heads
        result.append(item)
    return result
