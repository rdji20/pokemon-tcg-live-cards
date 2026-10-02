from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .database import database_url


DECK_SIZE = 60
DECK_LINE = re.compile(r"^(\d+)\s+(.+?)\s+([A-Za-z0-9-]+)\s+([A-Za-z0-9-]+)$")
SECTION_HEADER = re.compile(r"^(pok[eé]mon|trainer|energy)(?:\s*:\s*\d+)?$", re.IGNORECASE)
FORMATS = {"standard", "live-expanded", "unlimited"}


def parse_decklist(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or SECTION_HEADER.match(line):
            continue
        match = DECK_LINE.match(line)
        if not match:
            errors.append({"code": "invalid_line", "line": line_number, "message": f"Could not parse: {line}"})
            continue
        quantity, name, set_code, number = match.groups()
        entries.append({
            "quantity": int(quantity),
            "name": name,
            "set_code": set_code,
            "number": number,
            "line": line_number,
        })
    return entries, errors


def _resolve_text_entries(connection: psycopg.Connection[Any], entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for entry in entries:
        row = connection.execute(
            """
            SELECT c.id
            FROM cards c
            JOIN sets s ON s.id = c.set_id
            WHERE c.active
              AND lower(c.name) = lower(%s)
              AND c.number = %s
              AND (upper(c.set_id) = upper(%s) OR upper(s.raw_data->>'ptcgoCode') = upper(%s))
            ORDER BY c.release_date DESC
            LIMIT 1
            """,
            (entry["name"], entry["number"], entry["set_code"], entry["set_code"]),
        ).fetchone()
        if row is None:
            errors.append({
                "code": "card_not_found",
                "line": entry["line"],
                "message": f"Card not found: {entry['name']} {entry['set_code']} {entry['number']}",
            })
        else:
            resolved.append({"card_id": row[0], "quantity": entry["quantity"]})
    return resolved, errors


def _normalize_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combined: dict[str, int] = defaultdict(int)
    for entry in entries:
        card_id = str(entry.get("card_id", "")).strip()
        quantity = int(entry.get("quantity", 0))
        if card_id:
            combined[card_id] += quantity
    return [{"card_id": card_id, "quantity": quantity} for card_id, quantity in combined.items()]


def validate_deck(
    entries: list[dict[str, Any]],
    *,
    format_name: str,
    connection: psycopg.Connection[Any],
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if format_name not in FORMATS:
        errors.append({"code": "invalid_format", "message": f"Unsupported format: {format_name}"})
    normalized = _normalize_entries(entries)
    total = sum(item["quantity"] for item in normalized)
    if total != DECK_SIZE:
        errors.append({"code": "deck_size", "message": f"Deck has {total} cards; exactly {DECK_SIZE} are required"})
    if any(item["quantity"] <= 0 for item in normalized):
        errors.append({"code": "invalid_quantity", "message": "Every quantity must be positive"})

    ids = [item["card_id"] for item in normalized]
    rows = connection.execute(
        """
        SELECT id, name, supertype, subtypes, standard_status,
               live_expanded_status, live_status, image_small, image_large,
               set_id, number
        FROM cards WHERE active AND id = ANY(%s)
        """,
        (ids,),
    ).fetchall() if ids else []
    cards = {row[0]: row for row in rows}
    missing = sorted(set(ids).difference(cards))
    for card_id in missing:
        errors.append({"code": "card_not_found", "cardId": card_id, "message": f"Unknown card: {card_id}"})

    copies_by_name: dict[str, int] = defaultdict(int)
    has_basic_pokemon = False
    for item in normalized:
        row = cards.get(item["card_id"])
        if row is None:
            continue
        _, name, supertype, subtypes, standard_status, live_expanded_status, live_status, _, _, _, _ = row
        is_basic_energy = supertype == "Energy" and "Basic" in (subtypes or [])
        if not is_basic_energy:
            copies_by_name[name] += item["quantity"]
        if supertype == "Pokémon" and "Basic" in (subtypes or []):
            has_basic_pokemon = True
        if live_status != "playable":
            errors.append({"code": "not_in_live", "cardId": item["card_id"], "message": f"{name} is not playable in TCG Live"})
        if format_name == "standard" and standard_status != "legal":
            errors.append({"code": "not_standard_legal", "cardId": item["card_id"], "message": f"{name} is not Standard legal"})
        if format_name == "live-expanded" and live_expanded_status != "legal":
            errors.append({"code": "not_live_expanded_legal", "cardId": item["card_id"], "message": f"{name} is not Live Expanded legal"})
    for name, quantity in copies_by_name.items():
        if quantity > 4:
            errors.append({"code": "copy_limit", "message": f"{name} has {quantity} copies; maximum is 4"})
    if not has_basic_pokemon:
        errors.append({"code": "no_basic_pokemon", "message": "Deck needs at least one Basic Pokémon"})
    resolved_cards = []
    for item in normalized:
        row = cards.get(item["card_id"])
        if row is None:
            continue
        resolved_cards.append({
            **item,
            "name": row[1],
            "supertype": row[2],
            "subtypes": row[3] or [],
            "image_small": row[7],
            "image_large": row[8],
            "set_id": row[9],
            "number": row[10],
        })
    return {
        "valid": not errors,
        "format": format_name,
        "cardCount": total,
        "uniquePrints": len(normalized),
        "errors": errors,
        "warnings": warnings,
        "cards": resolved_cards,
    }


def validate_payload(payload: dict[str, Any], url: str | None = None) -> dict[str, Any]:
    format_name = payload.get("format", "standard")
    parse_errors: list[dict[str, Any]] = []
    with psycopg.connect(database_url(url)) as connection:
        if payload.get("decklist") is not None:
            parsed, parse_errors = parse_decklist(str(payload["decklist"]))
            entries, resolve_errors = _resolve_text_entries(connection, parsed)
            parse_errors.extend(resolve_errors)
        else:
            entries = list(payload.get("cards", []))
        result = validate_deck(entries, format_name=format_name, connection=connection)
    result["errors"] = [*parse_errors, *result["errors"]]
    result["valid"] = not result["errors"]
    return result


def create_deck(payload: dict[str, Any], url: str | None = None) -> dict[str, Any]:
    validation = validate_payload(payload, url)
    if not validation["valid"]:
        return {"deck": None, "validation": validation}
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        with connection.transaction():
            deck_id = connection.execute(
                "INSERT INTO decks (name, format) VALUES (%s, %s) RETURNING id",
                (payload.get("name", "Untitled deck"), validation["format"]),
            ).fetchone()["id"]
            connection.cursor().executemany(
                "INSERT INTO deck_cards (deck_id, card_id, quantity) VALUES (%s, %s, %s)",
                [(deck_id, item["card_id"], item["quantity"]) for item in validation["cards"]],
            )
        connection.commit()
    return {"deck": get_deck(str(deck_id), url), "validation": validation}


def get_deck(deck_id: str, url: str | None = None) -> dict[str, Any] | None:
    UUID(deck_id)
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        deck = connection.execute("SELECT * FROM decks WHERE id = %s", (deck_id,)).fetchone()
        if deck is None:
            return None
        rows = connection.execute(
            """
            SELECT dc.card_id, dc.quantity, c.name, c.set_id, c.number,
                   c.supertype, c.subtypes, c.image_small, c.image_large,
                   c.raw_data
            FROM deck_cards dc
            JOIN cards c ON c.id = dc.card_id
            WHERE dc.deck_id = %s
            ORDER BY c.supertype, c.name, c.set_id, c.number
            """,
            (deck_id,),
        ).fetchall()
    result = dict(deck)
    result["id"] = str(result["id"])
    result["created_at"] = result["created_at"].isoformat()
    result["updated_at"] = result["updated_at"].isoformat()
    result["cards"] = [{k: v for k, v in dict(row).items() if k != "raw_data"} for row in rows]
    return result


def list_decks(url: str | None = None) -> list[dict[str, Any]]:
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT d.id, d.name, d.format, d.created_at, d.updated_at,
                   coalesce(sum(dc.quantity), 0) AS card_count
            FROM decks d LEFT JOIN deck_cards dc ON dc.deck_id = d.id
            GROUP BY d.id ORDER BY d.updated_at DESC
            """
        ).fetchall()
    return [
        {**dict(row), "id": str(row["id"]), "card_count": int(row["card_count"]), "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat()}
        for row in rows
    ]


def export_deck(deck_id: str, url: str | None = None) -> str:
    UUID(deck_id)
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT dc.quantity, c.name, c.number, c.set_id,
                   c.supertype,
                   coalesce(s.raw_data->>'ptcgoCode', upper(c.set_id)) AS set_code
            FROM deck_cards dc
            JOIN cards c ON c.id = dc.card_id
            JOIN sets s ON s.id = c.set_id
            WHERE dc.deck_id = %s
            ORDER BY c.supertype, c.name, c.number
            """,
            (deck_id,),
        ).fetchall()
    headings = (("Pokémon", "Pokémon"), ("Trainer", "Trainer"), ("Energy", "Energy"))
    sections: list[str] = []
    for supertype, heading in headings:
        items = [row for row in rows if row["supertype"] == supertype]
        if not items:
            continue
        total = sum(row["quantity"] for row in items)
        lines = [f"{row['quantity']} {row['name']} {row['set_code']} {row['number']}" for row in items]
        sections.append("\n".join([f"{heading}: {total}", *lines]))
    return "\n\n".join(sections) + "\n"
