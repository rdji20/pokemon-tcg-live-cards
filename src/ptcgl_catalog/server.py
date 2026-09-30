from __future__ import annotations

import json
import threading
from datetime import date
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .catalog import CatalogError, build_catalog


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
    ) -> None:
        super().__init__(server_address, handler)
        self.project_dir = project_dir
        self.output_dir = output_dir
        self.cache_dir = cache_dir
        self.ref = ref
        self.policy_path = policy_path
        self.sync_lock = threading.Lock()


class CatalogRequestHandler(SimpleHTTPRequestHandler):
    server: CatalogHTTPServer

    def end_headers(self) -> None:
        if self.path.startswith("/data/") or self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json_response(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _manifest(self) -> dict[str, Any]:
        path = self.server.output_dir / "manifest.json"
        if not path.is_file():
            raise CatalogError("No catalog manifest exists. Run a sync first.")
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/api/status":
            try:
                self._json_response(HTTPStatus.OK, self._manifest())
            except CatalogError as exc:
                self._json_response(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/sync":
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
                },
            )
        except (CatalogError, OSError, ValueError) as exc:
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
    )
    print(f"TCG Live Card Catalog running at http://{host}:{port}")
    print("Press Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

