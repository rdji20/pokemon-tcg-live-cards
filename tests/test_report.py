from ptcgl_catalog.report import compare_snapshots, render_report


def test_change_report_finds_added_and_removed_cards():
    previous = {
        "cardIds": ["a", "b"],
        "cardsSha256": "old",
        "sets": {"s1": {"name": "One"}},
        "source": {"commit": "before"},
    }
    current = {
        "generatedAt": "2026-09-30T00:00:00+00:00",
        "asOf": "2026-09-30",
        "cardIds": ["b", "c"],
        "cardsSha256": "new",
        "sets": {"s1": {"name": "One"}, "s2": {"name": "Two"}},
        "source": {"commit": "after"},
    }
    change = compare_snapshots(previous, current)
    assert change["addedCards"] == ["c"]
    assert change["removedCards"] == ["a"]
    assert change["addedSets"] == ["s2"]
    assert "Cards: 2 → 2" in render_report(previous, current)
