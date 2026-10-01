from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .catalog import CatalogError
from .effects import PARSER_VERSION, parse_card_effects


DEFAULT_DATABASE_URL = "postgresql://ptcgl:ptcgl_local@localhost:5432/ptcgl"


@dataclass(frozen=True)
class ImportResult:
    catalog_run_id: int
    set_count: int
    card_count: int
    source_commit: str


def database_url(value: str | None = None) -> str:
    return value or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def apply_migrations(connection: psycopg.Connection[Any], migrations_dir: Path) -> list[str]:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename text PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    applied = {
        row[0]
        for row in connection.execute("SELECT filename FROM schema_migrations").fetchall()
    }
    completed: list[str] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in applied:
            continue
        connection.execute(path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migrations (filename) VALUES (%s)",
            (path.name,),
        )
        completed.append(path.name)
    connection.commit()
    return completed


def _status(legalities: dict[str, Any], key: str) -> str:
    value = str(legalities.get(key, "")).lower()
    if value == "legal":
        return "legal"
    if value == "banned":
        return "banned"
    return "rotated"


def _rules_text(card: dict[str, Any]) -> str:
    parts: list[str] = []
    for ability in card.get("abilities", []):
        parts.extend((ability.get("name", ""), ability.get("text", "")))
    for attack in card.get("attacks", []):
        parts.extend((attack.get("name", ""), attack.get("damage", ""), attack.get("text", "")))
    for key in ("rules", "flavorText"):
        value = card.get(key, [])
        parts.extend(value if isinstance(value, list) else [value])
    return " ".join(str(part) for part in parts if part).strip()


def _hp(value: Any) -> int | None:
    text = str(value or "")
    return int(text) if text.isdigit() else None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def import_catalog(
    *,
    data_dir: Path,
    migrations_dir: Path,
    url: str | None = None,
) -> ImportResult:
    manifest_path = data_dir / "manifest.json"
    sets_path = data_dir / "sets.json"
    cards_path = data_dir / "cards.jsonl"
    if not all(path.is_file() for path in (manifest_path, sets_path, cards_path)):
        raise CatalogError("Catalog files are missing; run sync before importing")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sets = json.loads(sets_path.read_text(encoding="utf-8"))
    cards = _load_jsonl(cards_path)
    source = manifest["source"]
    policy = manifest["policy"]
    generated_at = datetime.fromisoformat(manifest["generatedAt"])

    with psycopg.connect(database_url(url)) as connection:
        apply_migrations(connection, migrations_dir.parent / "init")
        apply_migrations(connection, migrations_dir)
        with connection.transaction():
            run = connection.execute(
                """
                INSERT INTO catalog_runs (
                    source_repository, source_commit, policy, as_of, imported_at,
                    set_count, card_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_repository, source_commit, as_of) DO UPDATE SET
                    policy = EXCLUDED.policy,
                    imported_at = EXCLUDED.imported_at,
                    set_count = EXCLUDED.set_count,
                    card_count = EXCLUDED.card_count
                RETURNING id
                """,
                (
                    source["repository"], source["commit"], Jsonb(policy),
                    manifest["asOf"], generated_at, len(sets), len(cards),
                ),
            ).fetchone()
            if run is None:
                raise CatalogError("Could not create catalog run")
            run_id = int(run[0])

            set_rows = [
                (
                    item["id"], item["name"], item["series"],
                    item["releaseDate"].replace("/", "-"), item.get("printedTotal"),
                    item.get("total"), Jsonb(item),
                )
                for item in sets
            ]
            connection.cursor().executemany(
                """
                INSERT INTO sets (id, name, series, release_date, printed_total, total, raw_data)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    series = EXCLUDED.series,
                    release_date = EXCLUDED.release_date,
                    printed_total = EXCLUDED.printed_total,
                    total = EXCLUDED.total,
                    raw_data = EXCLUDED.raw_data
                """,
                set_rows,
            )

            connection.execute("UPDATE cards SET active = false")
            evidence = {
                "policyAsOf": policy.get("policy_as_of"),
                "sources": policy.get("evidence", []),
                "sourceRepository": source["repository"],
                "sourceCommit": source["commit"],
            }
            card_rows = []
            for card in cards:
                legalities = card.get("legalities", {})
                catalog = card.get("catalog", {})
                standard_status = _status(legalities, "standard")
                expanded_status = _status(legalities, "expanded")
                live_status = "playable" if catalog.get("livePlayable") else "unknown"
                live_expanded_status = "legal" if live_status == "playable" else "not_implemented"
                card_rows.append((
                    card["id"], card["set"]["id"], card["set"]["name"], card["name"],
                    card.get("supertype"), card.get("subtypes", []), card.get("types", []),
                    _hp(card.get("hp")), card.get("number"), card.get("rarity"),
                    card.get("artist"), card.get("regulationMark"),
                    catalog["setReleaseDate"].replace("/", "-"),
                    standard_status == "legal", expanded_status == "legal",
                    card.get("images", {}).get("small"), card.get("images", {}).get("large"),
                    _rules_text(card), Jsonb(card), True, run_id, live_status,
                    standard_status, expanded_status, live_expanded_status,
                    Jsonb(evidence), generated_at,
                ))
            connection.cursor().executemany(
                """
                INSERT INTO cards (
                    id, set_id, set_name, name, supertype, subtypes, types, hp,
                    number, rarity, artist, regulation_mark, release_date,
                    standard_legal, expanded_legal, image_small, image_large,
                    rules_text, raw_data, active, catalog_run_id, live_status,
                    standard_status, expanded_status, live_expanded_status,
                    legality_evidence, verified_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    set_id = EXCLUDED.set_id,
                    set_name = EXCLUDED.set_name,
                    name = EXCLUDED.name,
                    supertype = EXCLUDED.supertype,
                    subtypes = EXCLUDED.subtypes,
                    types = EXCLUDED.types,
                    hp = EXCLUDED.hp,
                    number = EXCLUDED.number,
                    rarity = EXCLUDED.rarity,
                    artist = EXCLUDED.artist,
                    regulation_mark = EXCLUDED.regulation_mark,
                    release_date = EXCLUDED.release_date,
                    standard_legal = EXCLUDED.standard_legal,
                    expanded_legal = EXCLUDED.expanded_legal,
                    image_small = EXCLUDED.image_small,
                    image_large = EXCLUDED.image_large,
                    rules_text = EXCLUDED.rules_text,
                    raw_data = EXCLUDED.raw_data,
                    active = true,
                    catalog_run_id = EXCLUDED.catalog_run_id,
                    live_status = EXCLUDED.live_status,
                    standard_status = EXCLUDED.standard_status,
                    expanded_status = EXCLUDED.expanded_status,
                    live_expanded_status = EXCLUDED.live_expanded_status,
                    legality_evidence = EXCLUDED.legality_evidence,
                    verified_at = EXCLUDED.verified_at
                """,
                card_rows,
            )
            effect_rows = []
            for card in cards:
                parsed = parse_card_effects(card)
                effect_rows.append((
                    card["id"], PARSER_VERSION, parsed["status"], Jsonb(parsed),
                ))
            connection.cursor().executemany(
                """
                INSERT INTO card_effects (card_id, parser_version, status, effects)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (card_id) DO UPDATE SET
                    parser_version = EXCLUDED.parser_version,
                    status = EXCLUDED.status,
                    effects = EXCLUDED.effects,
                    updated_at = now()
                """,
                effect_rows,
            )
            rules_path = migrations_dir.parent.parent / "rules" / "standard-v1.json"
            if rules_path.is_file():
                rules = json.loads(rules_path.read_text(encoding="utf-8"))
                connection.execute(
                    """
                    INSERT INTO rulesets (id, version, name, rules)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        version = EXCLUDED.version,
                        name = EXCLUDED.name,
                        rules = EXCLUDED.rules
                    """,
                    (rules["id"], rules["version"], rules["name"], Jsonb(rules)),
                )
            from .rule_reviews import import_program_directory

            import_program_directory(connection, rules_path.parent / "generated")
        connection.commit()
    return ImportResult(run_id, len(sets), len(cards), source["commit"])


def catalog_status(url: str | None = None) -> dict[str, Any]:
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            """
            SELECT
                count(*) FILTER (WHERE active) AS cards,
                count(*) FILTER (WHERE active AND standard_status = 'legal') AS standard_cards,
                count(DISTINCT set_id) FILTER (WHERE active) AS sets,
                max(release_date) FILTER (WHERE active) AS latest_release
            FROM cards
            """
        ).fetchone()
        latest = connection.execute(
            """
            SELECT set_name FROM cards
            WHERE active
            ORDER BY release_date DESC, set_name
            LIMIT 1
            """
        ).fetchone()
    result = dict(row or {})
    result["latest_set"] = latest["set_name"] if latest else None
    for key, value in list(result.items()):
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    return result


def list_sets(url: str | None = None) -> list[dict[str, Any]]:
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.name, s.series, s.release_date, count(c.id) AS card_count
            FROM sets s
            JOIN cards c ON c.set_id = s.id AND c.active
            GROUP BY s.id
            ORDER BY s.release_date DESC, s.name
            """
        ).fetchall()
    return [
        {**dict(row), "release_date": row["release_date"].isoformat()}
        for row in rows
    ]


def _card_payload(row: dict[str, Any]) -> dict[str, Any]:
    card = dict(row["raw_data"])
    catalog = dict(card.get("catalog", {}))
    catalog.update({
        "liveStatus": row["live_status"],
        "standardStatus": row["standard_status"],
        "expandedStatus": row["expanded_status"],
        "liveExpandedStatus": row["live_expanded_status"],
        "legalityEvidence": row["legality_evidence"],
        "verifiedAt": row["verified_at"].isoformat() if row["verified_at"] else None,
        "standardLegal": row["standard_status"] == "legal",
        "expandedLegal": row["expanded_status"] == "legal",
    })
    card["catalog"] = catalog
    return card


def search_cards(
    *,
    q: str = "",
    set_id: str = "",
    supertype: str = "",
    legality: str = "",
    page: int = 1,
    page_size: int = 48,
    sort: str = "newest",
    url: str | None = None,
) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    where = ["active"]
    params: list[Any] = []
    q = q.strip()
    if q:
        where.append("(search_document @@ websearch_to_tsquery('english', %s) OR similarity(name, %s) >= 0.18)")
        params.extend((q, q))
    if set_id:
        where.append("set_id = %s")
        params.append(set_id)
    if supertype:
        where.append("supertype = %s")
        params.append(supertype)
    status_column = {
        "standard": "standard_status",
        "expanded": "expanded_status",
        "live-expanded": "live_expanded_status",
    }.get(legality)
    if status_column:
        where.append(f"{status_column} = 'legal'")
    where_sql = " AND ".join(where)

    if q:
        rank_sql = "ts_rank_cd(search_document, websearch_to_tsquery('english', %s)) + similarity(name, %s) * 2"
        rank_params: list[Any] = [q, q]
    else:
        rank_sql = "0"
        rank_params = []
    order_sql = {
        "newest": "release_date DESC, set_id, name, id",
        "oldest": "release_date ASC, set_id, name, id",
        "name": "name, release_date DESC, id",
        "set": "set_name, number, id",
        "relevance": "rank DESC, release_date DESC, name",
    }.get(sort, "release_date DESC, set_id, name, id")
    if q and sort == "newest":
        order_sql = "rank DESC, release_date DESC, name"

    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        total = connection.execute(
            f"SELECT count(*) AS total FROM cards WHERE {where_sql}",
            params,
        ).fetchone()["total"]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            f"""
            SELECT raw_data, live_status, standard_status, expanded_status,
                   live_expanded_status, legality_evidence, verified_at,
                   {rank_sql} AS rank
            FROM cards
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT %s OFFSET %s
            """,
            [*rank_params, *params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [_card_payload(dict(row)) for row in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "pages": pages,
    }


def get_ruleset(ruleset_id: str = "pokemon-tcg-standard", url: str | None = None) -> dict[str, Any] | None:
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT id, version, name, rules FROM rulesets WHERE id = %s",
            (ruleset_id,),
        ).fetchone()
    return dict(row) if row else None


def get_card_effects(card_id: str, url: str | None = None) -> dict[str, Any] | None:
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT card_id, parser_version, status, effects, updated_at FROM card_effects WHERE card_id = %s",
            (card_id,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["updated_at"] = result["updated_at"].isoformat()
    return result
