import json
from pathlib import Path

from ptcgl_catalog.arena import ARENA_SUPPORTED_OPERATIONS
from ptcgl_catalog.rule_engine import executable_effects, matching_rule, program_hash, validate_program


PROGRAMS = Path("rules/generated/standard-first-10.json")


def test_bootstrap_rule_programs_are_executable():
    programs = json.loads(PROGRAMS.read_text(encoding="utf-8"))
    assert len(programs) == 10
    for program in programs:
        checks = validate_program(program)
        assert checks["passed"], (program["cardId"], checks["errors"])
        assert checks["ruleCount"] >= 1
        assert checks["operationCount"] >= 1


def test_every_generated_rule_program_uses_the_supported_schema():
    for path in sorted(Path("rules/generated").glob("*.json")):
        for program in json.loads(path.read_text(encoding="utf-8")):
            checks = validate_program(program)
            assert checks["passed"], (path.name, program["cardId"], checks["errors"])
            operations = {
                effect["op"]
                for rule in program["rules"]
                for effect in rule.get("effects", [])
            }
            assert operations <= ARENA_SUPPORTED_OPERATIONS, (path.name, program["cardId"], operations - ARENA_SUPPORTED_OPERATIONS)


def test_coin_effect_resolution_is_seeded_and_conditional():
    program = next(
        item for item in json.loads(PROGRAMS.read_text(encoding="utf-8"))
        if item["cardId"] == "me5-105"
    )
    first = executable_effects(program, "item-crushing-hammer", {}, seed=1)
    second = executable_effects(program, "item-crushing-hammer", {}, seed=1)
    assert first == second
    assert first[0]["op"] == "flip_coin"
    if first[0]["result"] == "heads":
        assert first[1]["op"] == "discard_energy"


def test_multiple_and_until_tails_coin_flips_are_deterministic():
    fixed = {
        "rules": [{
            "id": "fixed", "conditions": [],
            "effects": [{"op": "flip_coin", "target": "coin", "amount": 5, "value": "", "conditions": []}],
        }],
    }
    repeated = executable_effects(fixed, "fixed", {}, seed=7)[0]
    assert len(repeated["results"]) == 5
    assert repeated["heads"] == repeated["results"].count("heads")

    until_tails = {
        "rules": [{
            "id": "until-tails", "conditions": [],
            "effects": [{"op": "flip_coin", "target": "coin", "amount": 1, "value": "until_tails", "conditions": []}],
        }],
    }
    result = executable_effects(until_tails, "until-tails", {}, seed=7)[0]
    assert result["results"][-1] == "tails"
    assert result["heads"] == len(result["results"]) - 1


def test_at_most_condition_enforces_a_prize_threshold():
    program = {
        "rules": [{
            "id": "prize-threshold",
            "conditions": [{"field": "opponent.prizes", "operator": "at_most", "value": 2}],
            "effects": [{"op": "create_modifier", "target": "self", "amount": 0, "value": "protected", "conditions": []}],
        }],
    }
    assert executable_effects(program, "prize-threshold", {"opponent": {"prizes": 2}})
    assert executable_effects(program, "prize-threshold", {"opponent": {"prizes": 3}}) == []


def test_program_identity_is_canonical_and_rules_are_found_by_event_source():
    program = {
        "rules": [{
            "sourceName": "Tackle", "sourceType": "attack", "trigger": "attack",
            "id": "attack-tackle", "effects": [],
        }],
        "schemaVersion": 1,
    }
    reordered = {"schemaVersion": 1, "rules": program["rules"]}
    assert program_hash(program) == program_hash(reordered)
    with_provenance = {**program, "generatedBy": {"generatedAt": "2099-01-01T00:00:00Z"}, "notes": "Editorial only"}
    assert program_hash(program) == program_hash(with_provenance)
    assert matching_rule(program, trigger="attack", source_name="Tackle")["id"] == "attack-tackle"
    assert matching_rule(program, trigger="play_card", source_name="Tackle") is None
