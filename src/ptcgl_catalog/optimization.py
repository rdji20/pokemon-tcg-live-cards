from __future__ import annotations

import random
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .database import GAMEPLAY_FINGERPRINT_SQL, database_url
from .effects import draw_value, maximum_attack_damage


ALGORITHM_VERSION = "heuristic-0.1.0"


def _score(row: dict[str, Any]) -> float:
    effects = row.get("effects") or {"effects": []}
    if row["supertype"] == "Pokémon":
        return maximum_attack_damage(effects) * 1.4 + (row.get("hp") or 0) * .35 + draw_value(effects) * 25
    return draw_value(effects) * 30 + len(row.get("rules_text") or "") * .02


def optimize_deck(payload: dict[str, Any], url: str | None = None) -> dict[str, Any]:
    format_name = payload.get("format", "standard")
    type_name = payload.get("type", "")
    seed = int(payload.get("seed", 1))
    status_column = "standard_status" if format_name == "standard" else "live_expanded_status"
    if format_name not in {"standard", "live-expanded"}:
        raise ValueError("Optimization supports standard or live-expanded")
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        catalog_run = connection.execute(
            "SELECT id FROM catalog_runs ORDER BY imported_at DESC LIMIT 1"
        ).fetchone()
        if catalog_run is None:
            raise ValueError("Catalog must be imported before optimization")
        rows = connection.execute(
            f"""
            SELECT c.id, c.name, c.supertype, c.subtypes, c.types, c.hp,
                   c.rules_text, c.image_small, c.image_large, c.set_id,
                   c.number, ({GAMEPLAY_FINGERPRINT_SQL}) AS print_group,
                   ce.effects
            FROM cards c LEFT JOIN card_effects ce ON ce.card_id = c.id
            WHERE c.active AND c.{status_column} = 'legal'
              AND (%s = '' OR c.supertype <> 'Pokémon' OR %s = ANY(c.types))
            """.format(GAMEPLAY_FINGERPRINT_SQL=GAMEPLAY_FINGERPRINT_SQL),
            (type_name, type_name),
        ).fetchall()
        candidates = [dict(row) for row in rows]
        rng = random.Random(seed)
        rng.shuffle(candidates)
        pokemon = sorted((row for row in candidates if row["supertype"] == "Pokémon" and "Basic" in (row["subtypes"] or [])), key=_score, reverse=True)
        trainers = sorted((row for row in candidates if row["supertype"] == "Trainer"), key=_score, reverse=True)
        energies = [row for row in candidates if row["supertype"] == "Energy" and "Basic" in (row["subtypes"] or [])]
        if type_name:
            energies.sort(key=lambda row: (type_name.lower() not in row["name"].lower(), row["name"]))
        chosen: list[dict[str, Any]] = []
        used_names: set[str] = set()

        def take_unique(pool: list[dict[str, Any]], count: int, quantity: int) -> None:
            for row in pool:
                if row["name"] in used_names:
                    continue
                chosen.append({
                    "card_id": row["id"], "quantity": quantity,
                    "name": row["name"], "supertype": row["supertype"],
                    "subtypes": row["subtypes"] or [],
                    "image_small": row["image_small"], "image_large": row["image_large"],
                    "set_id": row["set_id"], "number": row["number"],
                    "print_group": row["print_group"],
                    "score": round(_score(row), 2),
                })
                used_names.add(row["name"])
                if len([item for item in chosen if item["quantity"] == quantity]) >= count:
                    break

        take_unique(pokemon, 8, 4)
        pokemon_cards = sum(item["quantity"] for item in chosen)
        trainer_target = 12
        for row in trainers:
            if trainer_target <= 0:
                break
            if row["name"] in used_names:
                continue
            quantity = min(4, trainer_target)
            chosen.append({
                "card_id": row["id"], "quantity": quantity,
                "name": row["name"], "supertype": row["supertype"],
                "subtypes": row["subtypes"] or [],
                "image_small": row["image_small"], "image_large": row["image_large"],
                "set_id": row["set_id"], "number": row["number"],
                "print_group": row["print_group"],
                "score": round(_score(row), 2),
            })
            used_names.add(row["name"])
            trainer_target -= quantity
        remaining = 60 - sum(item["quantity"] for item in chosen)
        if energies and remaining > 0:
            energy = energies[0]
            chosen.append({
                "card_id": energy["id"], "quantity": remaining,
                "name": energy["name"], "supertype": energy["supertype"],
                "subtypes": energy["subtypes"] or [],
                "image_small": energy["image_small"], "image_large": energy["image_large"],
                "set_id": energy["set_id"], "number": energy["number"],
                "print_group": energy["print_group"],
                "score": 0,
            })
        total = sum(item["quantity"] for item in chosen)
        result = {
            "algorithmVersion": ALGORITHM_VERSION,
            "catalogRunId": catalog_run["id"],
            "format": format_name,
            "type": type_name or None,
            "seed": seed,
            "cardCount": total,
            "cards": chosen,
            "validShape": total == 60 and pokemon_cards > 0,
            "warning": "Heuristic prototype; evaluate with the simulator and validate before play.",
        }
        row = connection.execute(
            """
            INSERT INTO optimization_runs (
                format, seed, algorithm_version, parameters, result, catalog_run_id
            ) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (format_name, seed, ALGORITHM_VERSION, Jsonb(payload), Jsonb(result), catalog_run["id"]),
        ).fetchone()
        connection.commit()
    result["optimizationId"] = str(row["id"])
    return result
