from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .rule_engine import validate_program


def _programs_in_file(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else [payload]


def import_program_directory(
    connection: psycopg.Connection[Any],
    directory: Path,
) -> dict[str, int]:
    imported = 0
    failed = 0
    if not directory.is_dir():
        return {"imported": 0, "failed": 0}
    for path in sorted(directory.glob("*.json")):
        for program in _programs_in_file(path):
            card = connection.execute(
                "SELECT raw_data FROM cards WHERE id = %s",
                (program.get("cardId"),),
            ).fetchone()
            checks = validate_program(program, card[0] if card else None)
            status = "passed" if checks["passed"] else "failed"
            generated = program.get("generatedBy", {})
            connection.execute(
                """
                INSERT INTO card_rule_versions (
                    card_id, source_text_hash, schema_version, ai_provider,
                    ai_model, ai_response_id, program, automated_checks, ai_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (
                    card_id, source_text_hash, schema_version, ai_provider, ai_model
                ) DO UPDATE SET
                    ai_response_id = EXCLUDED.ai_response_id,
                    program = EXCLUDED.program,
                    automated_checks = EXCLUDED.automated_checks,
                    ai_status = EXCLUDED.ai_status,
                    updated_at = now()
                """,
                (
                    program["cardId"], program["sourceTextHash"],
                    program["schemaVersion"], generated.get("provider", "unknown"),
                    generated.get("model", "unknown"), generated.get("responseId"),
                    Jsonb(program), Jsonb(checks), status,
                ),
            )
            imported += int(checks["passed"])
            failed += int(not checks["passed"])
    return {"imported": imported, "failed": failed}


def import_rule_programs(directory: Path, url: str | None = None) -> dict[str, int]:
    from .database import database_url

    with psycopg.connect(database_url(url)) as connection:
        result = import_program_directory(connection, directory)
        connection.commit()
    return result


def review_coverage(url: str | None = None) -> dict[str, Any]:
    from .database import database_url

    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            """
            SELECT
                count(DISTINCT c.id) FILTER (WHERE c.active AND c.standard_status = 'legal') AS standard_cards,
                count(DISTINCT rv.card_id) FILTER (
                    WHERE c.active AND c.standard_status = 'legal' AND rv.ai_status = 'passed'
                ) AS ai_passed,
                count(DISTINCT rv.card_id) FILTER (
                    WHERE c.active AND c.standard_status = 'legal'
                      AND rv.ai_status = 'passed' AND rv.manual_status = 'approved'
                ) AS manually_approved,
                count(DISTINCT rv.id) FILTER (
                    WHERE rv.ai_status = 'passed' AND rv.manual_status = 'pending'
                ) AS pending_reviews,
                count(DISTINCT rv.id) FILTER (WHERE rv.manual_status = 'rejected') AS rejected_versions
            FROM cards c
            LEFT JOIN card_rule_versions rv ON rv.card_id = c.id
            """
        ).fetchone()
    return {key: int(value or 0) for key, value in dict(row).items()}


def list_rule_reviews(
    *,
    manual_status: str = "pending",
    limit: int = 50,
    url: str | None = None,
) -> list[dict[str, Any]]:
    from .database import database_url

    if manual_status not in {"pending", "approved", "rejected", "all"}:
        raise ValueError("Invalid manual review status")
    where = "" if manual_status == "all" else "AND rv.manual_status = %s"
    parameters: list[Any] = [] if manual_status == "all" else [manual_status]
    parameters.append(min(200, max(1, limit)))
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        rows = connection.execute(
            f"""
            SELECT rv.id, rv.card_id, rv.schema_version, rv.ai_provider,
                   rv.ai_model, rv.ai_status, rv.manual_status, rv.program,
                   rv.automated_checks, rv.reviewer, rv.review_note,
                   rv.reviewed_at, rv.created_at,
                   c.name, c.set_name, c.number, c.supertype, c.image_small,
                   c.standard_status, c.raw_data
            FROM card_rule_versions rv
            JOIN cards c ON c.id = rv.card_id
            WHERE rv.ai_status = 'passed' {where}
            ORDER BY rv.created_at, rv.card_id
            LIMIT %s
            """,
            parameters,
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        for key in ("reviewed_at", "created_at"):
            item[key] = item[key].isoformat() if item[key] else None
        result.append(item)
    return result


def decide_rule_review(
    review_id: str,
    *,
    decision: str,
    reviewer: str,
    note: str = "",
    url: str | None = None,
) -> dict[str, Any]:
    from .database import database_url

    UUID(review_id)
    if decision not in {"approved", "rejected"}:
        raise ValueError("Decision must be approved or rejected")
    reviewer = reviewer.strip()[:80] or "manual-reviewer"
    note = note.strip()[:2000]
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        with connection.transaction():
            row = connection.execute(
                """
                UPDATE card_rule_versions
                SET manual_status = %s, reviewer = %s, review_note = %s,
                    reviewed_at = now(), updated_at = now()
                WHERE id = %s AND ai_status = 'passed'
                RETURNING id, card_id, manual_status, reviewer, review_note, reviewed_at
                """,
                (decision, reviewer, note, review_id),
            ).fetchone()
            if row is None:
                raise ValueError("Review version not found")
            connection.execute(
                """
                INSERT INTO card_rule_review_audit (rule_version_id, action, actor, metadata)
                VALUES (%s, %s, %s, %s)
                """,
                (review_id, decision, reviewer, Jsonb({"note": note})),
            )
        connection.commit()
    result = dict(row)
    result["id"] = str(result["id"])
    result["reviewed_at"] = result["reviewed_at"].isoformat()
    return result
