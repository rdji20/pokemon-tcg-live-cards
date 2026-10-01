from __future__ import annotations

from typing import Final


CORE_RULES_VERSION: Final = "pokemon-tcg-core-0.2.1"
OFFICIAL_RULEBOOK_URL: Final = (
    "https://tcg.pokemon.com/assets/img/global/tcg-rulebook/"
    "ME02_Web_Rulebook_en-us_HiRes.pdf"
)

DECK_SIZE: Final = 60
OPENING_HAND_SIZE: Final = 7
PRIZE_COUNT: Final = 6
MAX_BENCH: Final = 5
MAX_MANUAL_ENERGY_PER_TURN: Final = 1
MAX_RETREATS_PER_TURN: Final = 1

SETUP_PHASES: Final = {
    "coin_call",
    "choose_turn_order",
    "mulligan_draw",
    "choose_active",
    "choose_bench",
}

RULE_COVERAGE: Final = (
    {
        "rule": "Pregame sequence",
        "status": "implemented",
        "detail": "Coin call, winner's turn-order choice, opening hands, mulligans, optional bonus draw, Active, Bench, and Prizes.",
    },
    {
        "rule": "Opening hand and mulligans",
        "status": "implemented",
        "detail": "Seven cards, reveal-and-reshuffle until a Basic exists, and zero-to-N optional bonus cards for opponent mulligans.",
    },
    {
        "rule": "Board setup",
        "status": "implemented",
        "detail": "One Basic Active, up to five Basic Benched Pokémon, then six face-down Prize cards.",
    },
    {
        "rule": "Turn structure",
        "status": "implemented",
        "detail": "Draw at turn start, legal actions in any permitted order, one manual Energy attachment, one retreat, and attack ending the turn.",
    },
    {
        "rule": "First-turn restrictions",
        "status": "implemented",
        "detail": "The player going first cannot attack or play a Supporter on their first turn.",
    },
    {
        "rule": "Win conditions",
        "status": "implemented",
        "detail": "Prize cards, no Pokémon in play, and failure to draw at the start of a turn.",
    },
    {
        "rule": "Evolution, Energy, Retreat, Weakness, and Resistance",
        "status": "partial",
        "detail": "Universal timing and calculations execute; special Energy, Tools, effects that change costs, and Special Conditions still require card programs.",
    },
    {
        "rule": "Trainer cards, Abilities, and attack effects",
        "status": "partial",
        "detail": "Only the explicitly listed effect subset executes; unimplemented card text remains visibly partial.",
    },
    {
        "rule": "Special Conditions and Pokémon Checkup",
        "status": "pending",
        "detail": "Poisoned, Burned, Asleep, Paralyzed, and Confused state transitions are not executable yet.",
    },
)


def first_turn_restricted(*, player_index: int, first_player: int | None, turns_taken: int) -> bool:
    return first_player == player_index and turns_taken == 1


def setup_prompt(phase: str, *, mulligan_draws: int = 0) -> dict[str, str]:
    prompts = {
        "coin_call": {
            "title": "Call the opening coin",
            "text": "Pick a side. The winner chooses who goes first.",
        },
        "choose_turn_order": {
            "title": "You won the coin flip",
            "text": "Choose whether you want to go first or second.",
        },
        "mulligan_draw": {
            "title": "Opponent took a mulligan",
            "text": f"You may draw {mulligan_draws} extra card{'s' if mulligan_draws != 1 else ''} before choosing your Active Pokémon.",
        },
        "choose_active": {
            "title": "Choose your Active Pokémon",
            "text": "Click a highlighted Basic Pokémon in your hand to place it face down in the Active Spot.",
        },
        "choose_bench": {
            "title": "Set up your Bench",
            "text": "Click highlighted Basic Pokémon to place them face down. Choose Ready when your board is set.",
        },
    }
    return prompts.get(phase, {"title": "Main phase", "text": "Choose one legal action."})
