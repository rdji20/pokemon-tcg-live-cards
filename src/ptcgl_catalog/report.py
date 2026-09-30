from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import CatalogError


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_snapshot(data_dir: Path) -> dict[str, Any]:
    manifest_path = data_dir / "manifest.json"
    sets_path = data_dir / "sets.json"
    cards_path = data_dir / "cards.jsonl"
    if not all(path.is_file() for path in (manifest_path, sets_path, cards_path)):
        raise CatalogError("Catalog files are missing; run sync before building a change report")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sets = json.loads(sets_path.read_text(encoding="utf-8"))
    card_ids: list[str] = []
    with cards_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                card_ids.append(json.loads(line)["id"])
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "catalogGeneratedAt": manifest.get("generatedAt"),
        "asOf": manifest.get("asOf"),
        "source": manifest.get("source", {}),
        "policyVersion": manifest.get("policy", {}).get("version"),
        "cardsSha256": _sha256(cards_path),
        "cardIds": sorted(card_ids),
        "sets": {
            item["id"]: {
                "name": item["name"],
                "releaseDate": item["releaseDate"],
                "total": item.get("total"),
            }
            for item in sets
        },
    }


def compare_snapshots(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    previous = previous or {}
    previous_cards = set(previous.get("cardIds", []))
    current_cards = set(current.get("cardIds", []))
    previous_sets = set(previous.get("sets", {}))
    current_sets = set(current.get("sets", {}))
    changed_sets = sorted(
        set(previous.get("sets", {})).intersection(current.get("sets", {})),
        key=str.lower,
    )
    changed_sets = [
        set_id for set_id in changed_sets
        if previous["sets"][set_id] != current["sets"][set_id]
    ]
    card_content_changed = previous.get("cardsSha256") != current.get("cardsSha256")
    policy_changed = previous.get("policyVersion") != current.get("policyVersion")
    return {
        "initial": not bool(previous),
        "changed": not previous or card_content_changed or bool(changed_sets) or previous_sets != current_sets or policy_changed,
        "cardContentChanged": card_content_changed,
        "sourceChanged": previous.get("source", {}).get("commit") != current.get("source", {}).get("commit"),
        "previousCardCount": len(previous_cards),
        "currentCardCount": len(current_cards),
        "addedCards": sorted(current_cards - previous_cards),
        "removedCards": sorted(previous_cards - current_cards),
        "addedSets": sorted(current_sets - previous_sets),
        "removedSets": sorted(previous_sets - current_sets),
        "changedSets": changed_sets,
    }


def _sample(values: list[str], limit: int = 40) -> str:
    if not values:
        return "None"
    visible = values[:limit]
    suffix = f" (and {len(values) - limit} more)" if len(values) > limit else ""
    return ", ".join(f"`{value}`" for value in visible) + suffix


def render_report(previous: dict[str, Any] | None, current: dict[str, Any]) -> str:
    change = compare_snapshots(previous, current)
    source = current.get("source", {})
    commit = source.get("commit", "unknown")
    lines = [
        "# Catalog change report",
        "",
        f"Generated: {current['generatedAt']}",
        f"Catalog date: {current.get('asOf', 'unknown')}",
        f"Source commit: `{commit}`",
        "",
        "## Summary",
        "",
        f"- Cards: {change['previousCardCount']:,} → {change['currentCardCount']:,}",
        f"- Added cards: {len(change['addedCards']):,}",
        f"- Removed cards: {len(change['removedCards']):,}",
        f"- Added sets: {len(change['addedSets']):,}",
        f"- Removed sets: {len(change['removedSets']):,}",
        f"- Changed set metadata: {len(change['changedSets']):,}",
        f"- Card content changed: {'yes' if change['cardContentChanged'] else 'no'}",
        "",
        "## Details",
        "",
        f"- Added cards: {_sample(change['addedCards'])}",
        f"- Removed cards: {_sample(change['removedCards'])}",
        f"- Added sets: {_sample(change['addedSets'])}",
        f"- Removed sets: {_sample(change['removedSets'])}",
        f"- Changed sets: {_sample(change['changedSets'])}",
        "",
        "The complete machine-readable before/after comparison is reproducible from "
        "`catalog/baseline.json` and the commit-pinned catalog manifest.",
        "",
    ]
    return "\n".join(lines)


def write_change_report(
    *,
    data_dir: Path,
    baseline_path: Path,
    output_path: Path,
    update_baseline: bool = False,
) -> dict[str, Any]:
    current = build_snapshot(data_dir)
    previous = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path.is_file()
        else None
    )
    change = compare_snapshots(previous, current)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report(previous, current), encoding="utf-8")
    if update_baseline and (change["changed"] or change["sourceChanged"]):
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return change
