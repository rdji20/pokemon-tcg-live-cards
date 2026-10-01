from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .rule_engine import (
    ALLOWED_OPERATIONS, ALLOWED_OPERATORS, ALLOWED_TRIGGERS,
    SCHEMA_VERSION, source_text_hash, validate_program,
)
from .rule_reviews import import_rule_programs


def _condition_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "field": {"type": "string"},
            "operator": {"type": "string", "enum": sorted(ALLOWED_OPERATORS)},
            "value": {"type": "string"},
        },
        "required": ["field", "operator", "value"],
        "additionalProperties": False,
    }


def _effect_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": sorted(ALLOWED_OPERATIONS)},
            "target": {"type": "string"},
            "amount": {"type": "integer"},
            "value": {"type": "string"},
            "conditions": {"type": "array", "items": _condition_schema()},
            "parameters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                    "required": ["key", "value"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["op", "target", "amount", "value", "conditions", "parameters"],
        "additionalProperties": False,
    }


AI_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "sourceType": {"type": "string", "enum": ["ability", "attack", "rule_box", "trainer_rule"]},
                    "sourceName": {"type": "string"},
                    "trigger": {"type": "string", "enum": sorted(ALLOWED_TRIGGERS)},
                    "cost": {"type": "array", "items": {"type": "string"}},
                    "conditions": {"type": "array", "items": _condition_schema()},
                    "effects": {"type": "array", "items": _effect_schema()},
                },
                "required": ["id", "sourceType", "sourceName", "trigger", "cost", "conditions", "effects"],
                "additionalProperties": False,
            },
        },
        "unsupportedText": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["confidence", "rules", "unsupportedText", "notes"],
    "additionalProperties": False,
}


def _prompt(card: dict[str, Any]) -> str:
    source = {
        key: card.get(key)
        for key in ("id", "name", "supertype", "subtypes", "hp", "abilities", "attacks", "rules")
    }
    return (
        "Convert this Pokemon TCG card into executable rule primitives. Preserve every "
        "condition, timing restriction, target, choice, coin branch, damage modifier, "
        "self effect, and prize rule. Include printed attack damage as deal_damage. "
        "Use unsupportedText for anything that cannot be represented exactly with the "
        "allowed operations. Do not infer effects that are absent from the source. "
        "Use dotted context fields in conditions and string values. Empty numeric fields "
        "must be zero and unused strings/arrays must be empty. Return only the schema.\n\n"
        + json.dumps(source, ensure_ascii=False, indent=2)
    )


def _output_text(response: dict[str, Any]) -> str:
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content["text"]
    raise RuntimeError("OpenAI response did not contain structured output text")


def generate_rule_program(
    card: dict[str, Any],
    *,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    request_body = {
        "model": model,
        "input": _prompt(card),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "pokemon_card_rule_program",
                "strict": True,
                "schema": AI_OUTPUT_SCHEMA,
            }
        },
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(request_body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"OpenAI rule pass failed ({exc.code}): {detail}") from exc
    generated = json.loads(_output_text(payload))
    program = {
        "schemaVersion": SCHEMA_VERSION,
        "cardId": card["id"],
        "sourceTextHash": source_text_hash(card),
        "generatedBy": {
            "provider": "openai",
            "model": payload.get("model", model),
            "responseId": payload.get("id", ""),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        },
        **generated,
    }
    checks = validate_program(program, card)
    if not checks["passed"]:
        raise RuntimeError("AI rule program failed validation: " + "; ".join(checks["errors"]))
    return program


def run_ai_rule_pass(
    *,
    output_dir: Path,
    limit: int = 25,
    url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    from .database import database_url

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for the AI rule pass")
    model = model or os.environ.get("OPENAI_RULE_MODEL", "gpt-5")
    with psycopg.connect(database_url(url), row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT c.raw_data
            FROM cards c
            WHERE c.active AND c.standard_status = 'legal'
            ORDER BY c.release_date DESC, c.id
            """
        ).fetchall()
        completed = {
            (row["card_id"], row["source_text_hash"])
            for row in connection.execute(
                "SELECT card_id, source_text_hash FROM card_rule_versions WHERE ai_status = 'passed'"
            ).fetchall()
        }
    cards = [dict(row["raw_data"]) for row in rows]
    cards = [card for card in cards if (card["id"], source_text_hash(card)) not in completed]
    cards = cards[:min(200, max(1, limit))]
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_ids: list[str] = []
    for card in cards:
        program = generate_rule_program(card, api_key=api_key, model=model)
        path = output_dir / f"{card['id']}.json"
        path.write_text(json.dumps(program, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        generated_ids.append(card["id"])
    imported = import_rule_programs(output_dir, url)
    return {"generated": generated_ids, **imported, "model": model}
