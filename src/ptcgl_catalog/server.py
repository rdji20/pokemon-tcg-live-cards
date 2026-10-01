from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import date
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import psycopg

from .catalog import CatalogError, build_catalog
from .database import (
    catalog_status, database_url, get_card_effects, get_ruleset,
    import_catalog, list_sets, search_cards,
)
from .decks import create_deck, export_deck, get_deck, list_decks, validate_payload
from .optimization import optimize_deck
from .simulation import simulate_match
from .rule_reviews import decide_rule_review, list_rule_reviews, review_coverage


def create_review_token(password: str, reviewer: str, *, now: int | None = None) -> str:
    reviewer = reviewer.replace("|", " ").strip()[:80] or "manual-reviewer"
    expiry = (now if now is not None else int(time.time())) + 8 * 60 * 60
    payload = f"{reviewer}|{expiry}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    signature = hmac.new(password.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_review_token(password: str, token: str, *, now: int | None = None) -> str | None:
    if not password:
        return None
    try:
        encoded_payload, signature = token.split(".", 1)
        padding = "=" * (-len(encoded_payload) % 4)
        payload = base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
        reviewer, expiry_text = payload.rsplit("|", 1)
        expected = hmac.new(password.encode(), payload.encode(), hashlib.sha256).hexdigest()
        current_time = now if now is not None else int(time.time())
        if not hmac.compare_digest(signature, expected) or int(expiry_text) < current_time:
            return None
        return reviewer
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None


class CatalogHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[SimpleHTTPRequestHandler],
        *,
        project_dir: Path,
        output_dir: Path,
        cache_dir: Path,
        ref: str,
        policy_path: Path | None,
        database_url_value: str | None,
        migrations_dir: Path,
    ) -> None:
        super().__init__(server_address, handler)
        self.project_dir = project_dir
        self.output_dir = output_dir
        self.cache_dir = cache_dir
        self.ref = ref
        self.policy_path = policy_path
        self.database_url = database_url(database_url_value)
        self.migrations_dir = migrations_dir
        self.sync_lock = threading.Lock()


class CatalogRequestHandler(SimpleHTTPRequestHandler):
    server: CatalogHTTPServer

    def end_headers(self) -> None:
        if self.path.startswith("/data/") or self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json_response(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _manifest(self) -> dict[str, Any]:
        path = self.server.output_dir / "manifest.json"
        if not path.is_file():
            raise CatalogError("No catalog manifest exists. Run a sync first.")
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _request_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            raise ValueError("Request body must be JSON and smaller than 1 MB")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _review_password(self) -> str:
        password = os.environ.get("PTCGL_REVIEW_PASSWORD", "")
        return password if len(password) >= 12 else ""

    def _review_identity(self) -> str | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("ptcgl_review")
        password = self._review_password()
        if morsel is None or not password:
            return None
        return verify_review_token(password, morsel.value)

    def _review_cookie(self, reviewer: str) -> str:
        token = create_review_token(self._review_password(), reviewer)
        return f"ptcgl_review={token}; Path=/; Max-Age=28800; HttpOnly; SameSite=Strict"

    def _require_reviewer(self) -> str | None:
        reviewer = self._review_identity()
        if reviewer is None:
            self._json_response(HTTPStatus.UNAUTHORIZED, {"error": "Manual review login required"})
        return reviewer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            try:
                self._json_response(
                    HTTPStatus.OK,
                    {"manifest": self._manifest(), "database": catalog_status(self.server.database_url)},
                )
            except (CatalogError, psycopg.Error) as exc:
                self._json_response(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        if parsed.path == "/api/sets":
            try:
                self._json_response(HTTPStatus.OK, {"items": list_sets(self.server.database_url)})
            except psycopg.Error as exc:
                self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/cards":
            query = parse_qs(parsed.query)
            value = lambda key, default="": query.get(key, [default])[0]
            try:
                payload = search_cards(
                    q=value("q"),
                    set_id=value("set_id"),
                    supertype=value("supertype"),
                    legality=value("legality"),
                    page=int(value("page", "1")),
                    page_size=int(value("page_size", "48")),
                    sort=value("sort", "newest"),
                    url=self.server.database_url,
                )
                self._json_response(HTTPStatus.OK, payload)
            except (psycopg.Error, ValueError) as exc:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if parsed.path == "/api/rule-coverage":
            try:
                self._json_response(HTTPStatus.OK, review_coverage(self.server.database_url))
            except psycopg.Error as exc:
                self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/review/status":
            identity = self._review_identity()
            self._json_response(HTTPStatus.OK, {
                "configured": bool(self._review_password()),
                "authenticated": identity is not None,
                "reviewer": identity,
            })
            return
        if parsed.path == "/api/reviews":
            if self._require_reviewer() is None:
                return
            query = parse_qs(parsed.query)
            try:
                items = list_rule_reviews(
                    manual_status=query.get("status", ["pending"])[0],
                    limit=int(query.get("limit", ["50"])[0]),
                    url=self.server.database_url,
                )
                self._json_response(HTTPStatus.OK, {"items": items})
            except (ValueError, psycopg.Error) as exc:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if parsed.path == "/api/decks":
            try:
                self._json_response(HTTPStatus.OK, {"items": list_decks(self.server.database_url)})
            except psycopg.Error as exc:
                self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path.startswith("/api/decks/"):
            parts = parsed.path.strip("/").split("/")
            try:
                deck_id = parts[2]
                if len(parts) == 4 and parts[3] == "export":
                    self._json_response(HTTPStatus.OK, {"decklist": export_deck(deck_id, self.server.database_url)})
                else:
                    deck = get_deck(deck_id, self.server.database_url)
                    self._json_response(HTTPStatus.OK if deck else HTTPStatus.NOT_FOUND, {"deck": deck})
            except (ValueError, psycopg.Error) as exc:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if parsed.path == "/api/rules":
            rules = get_ruleset(url=self.server.database_url)
            self._json_response(HTTPStatus.OK if rules else HTTPStatus.NOT_FOUND, {"ruleset": rules})
            return
        if parsed.path.startswith("/api/cards/") and parsed.path.endswith("/effects"):
            card_id = parsed.path.removeprefix("/api/cards/").removesuffix("/effects").strip("/")
            effects = get_card_effects(card_id, self.server.database_url)
            self._json_response(HTTPStatus.OK if effects else HTTPStatus.NOT_FOUND, {"cardEffects": effects})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/review/login":
            try:
                payload = self._request_json()
                configured = self._review_password()
                supplied = str(payload.get("password", ""))
                valid = bool(configured) and hmac.compare_digest(supplied, configured)
                if not valid:
                    self._json_response(
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "Review password is not configured or is incorrect"},
                    )
                    return
                reviewer = str(payload.get("reviewer", "manual-reviewer"))
                self._json_response(
                    HTTPStatus.OK,
                    {"authenticated": True, "reviewer": reviewer.replace("|", " ").strip()[:80]},
                    {"Set-Cookie": self._review_cookie(reviewer)},
                )
            except (ValueError, json.JSONDecodeError) as exc:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path == "/api/review/logout":
            self._json_response(
                HTTPStatus.OK,
                {"authenticated": False},
                {"Set-Cookie": "ptcgl_review=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"},
            )
            return
        if path.startswith("/api/reviews/") and path.endswith("/decision"):
            reviewer = self._require_reviewer()
            if reviewer is None:
                return
            review_id = path.removeprefix("/api/reviews/").removesuffix("/decision").strip("/")
            try:
                payload = self._request_json()
                result = decide_rule_review(
                    review_id,
                    decision=str(payload.get("decision", "")),
                    reviewer=reviewer,
                    note=str(payload.get("note", "")),
                    url=self.server.database_url,
                )
                self._json_response(HTTPStatus.OK, {"review": result})
            except (ValueError, json.JSONDecodeError, psycopg.Error) as exc:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path in {"/api/decks", "/api/decks/validate", "/api/simulations", "/api/optimize"}:
            try:
                payload = self._request_json()
                if path == "/api/decks":
                    result = create_deck(payload, self.server.database_url)
                    status = HTTPStatus.CREATED if result["deck"] else HTTPStatus.UNPROCESSABLE_ENTITY
                elif path == "/api/decks/validate":
                    result = validate_payload(payload, self.server.database_url)
                    status = HTTPStatus.OK if result["valid"] else HTTPStatus.UNPROCESSABLE_ENTITY
                elif path == "/api/simulations":
                    result = simulate_match(payload, self.server.database_url)
                    status = HTTPStatus.CREATED
                else:
                    result = optimize_deck(payload, self.server.database_url)
                    status = HTTPStatus.CREATED
                self._json_response(status, result)
            except (KeyError, ValueError, json.JSONDecodeError, psycopg.Error) as exc:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path != "/api/sync":
            self._json_response(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if self.headers.get("X-PTCGL-Action") != "sync":
            self._json_response(HTTPStatus.FORBIDDEN, {"error": "Missing sync header"})
            return
        if not self.server.sync_lock.acquire(blocking=False):
            self._json_response(HTTPStatus.CONFLICT, {"error": "A sync is already running"})
            return

        try:
            result = build_catalog(
                output_dir=self.server.output_dir,
                cache_dir=self.server.cache_dir,
                ref=self.server.ref,
                as_of=date.today(),
                policy_path=self.server.policy_path,
            )
            imported = import_catalog(
                data_dir=self.server.output_dir,
                migrations_dir=self.server.migrations_dir,
                url=self.server.database_url,
            )
            manifest = self._manifest()
            self._json_response(
                HTTPStatus.OK,
                {
                    "message": "Catalog synchronized",
                    "commit": result.commit,
                    "sets": result.set_count,
                    "cards": result.card_count,
                    "generatedAt": manifest["generatedAt"],
                    "asOf": manifest["asOf"],
                    "warnings": list(result.warnings),
                    "catalogRunId": imported.catalog_run_id,
                },
            )
        except (CatalogError, OSError, ValueError, psycopg.Error) as exc:
            self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        finally:
            self.server.sync_lock.release()


def run_server(
    *,
    project_dir: Path,
    output_dir: Path,
    cache_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    ref: str = "master",
    policy_path: Path | None = None,
    database_url_value: str | None = None,
    migrations_dir: Path = Path("db/migrations"),
) -> None:
    project_dir = project_dir.resolve()
    handler = partial(CatalogRequestHandler, directory=str(project_dir))
    server = CatalogHTTPServer(
        (host, port),
        handler,
        project_dir=project_dir,
        output_dir=(project_dir / output_dir).resolve() if not output_dir.is_absolute() else output_dir,
        cache_dir=(project_dir / cache_dir).resolve() if not cache_dir.is_absolute() else cache_dir,
        ref=ref,
        policy_path=policy_path.resolve() if policy_path else None,
        database_url_value=database_url_value,
        migrations_dir=(project_dir / migrations_dir).resolve() if not migrations_dir.is_absolute() else migrations_dir,
    )
    print(f"TCG Live Card Catalog running at http://{host}:{port}")
    print("Press Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
