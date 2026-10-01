import json
from pathlib import Path

from ptcgl_catalog.rule_engine import executable_effects, validate_program


PROGRAMS = Path("rules/generated/standard-first-10.json")


def test_bootstrap_rule_programs_are_executable():
    programs = json.loads(PROGRAMS.read_text(encoding="utf-8"))
    assert len(programs) == 10
    for program in programs:
        checks = validate_program(program)
        assert checks["passed"], (program["cardId"], checks["errors"])
        assert checks["ruleCount"] >= 1
        assert checks["operationCount"] >= 1


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
