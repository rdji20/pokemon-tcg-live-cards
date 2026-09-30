from ptcgl_catalog.simulation import CardModel, GreedyDamagePolicy, simulate_game


def _deck(damage: int) -> list[CardModel]:
    pokemon = CardModel("p", "Test Pokémon", "Pokémon", ("Basic",), 100, damage, 0)
    energy = CardModel("e", "Test Energy", "Energy", ("Basic",), 0, 0, 0)
    return [pokemon] * 12 + [energy] * 48


def test_simulation_is_deterministic_for_same_seed():
    policy = GreedyDamagePolicy()
    first = simulate_game(_deck(50), _deck(40), seed=42, policy_a=policy, policy_b=policy)
    second = simulate_game(_deck(50), _deck(40), seed=42, policy_a=policy, policy_b=policy)
    assert first == second


def test_simulation_can_finish_by_prizes():
    result = simulate_game(_deck(100), _deck(10), seed=5, policy_a=GreedyDamagePolicy(), policy_b=GreedyDamagePolicy())
    assert result["winner"] == 0
    assert result["reason"] == "prizes"

