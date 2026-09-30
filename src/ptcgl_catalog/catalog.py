from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import ssl
import sqlite3
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable


REPOSITORY = "PokemonTCG/pokemon-tcg-data"
GITHUB_API = f"https://api.github.com/repos/{REPOSITORY}"
USER_AGENT = "ptcgl-catalog/0.1 (+personal research project)"


class CatalogError(RuntimeError):
    """A controlled catalog build failure."""


@dataclass(frozen=True)
class BuildResult:
    commit: str
    set_count: int
    card_count: int
    output_dir: Path
    warnings: tuple[str, ...]


def load_policy(path: Path | None = None) -> dict[str, Any]:
    policy_path = path or Path(str(files("ptcgl_catalog").joinpath("live_policy.json")))
    with policy_path.open("r", encoding="utf-8") as handle:
        policy = json.load(handle)
    required = {"supported_series", "included_set_ids", "excluded_set_ids"}
    missing = required.difference(policy)
    if missing:
        raise CatalogError(f"Policy is missing required keys: {sorted(missing)}")
    return policy


def _request(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    context = ssl.create_default_context()
    default_paths = ssl.get_default_verify_paths()
    if default_paths.cafile is None and default_paths.capath is None:
        for candidate in (Path("/etc/ssl/cert.pem"), Path("/opt/homebrew/etc/openssl@3/cert.pem")):
            if candidate.is_file():
                context = ssl.create_default_context(cafile=str(candidate))
                break
    try:
        with urllib.request.urlopen(request, timeout=60, context=context) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise CatalogError(f"HTTP {exc.code} while requesting {url}") from exc
    except urllib.error.URLError as exc:
        raise CatalogError(f"Could not request {url}: {exc.reason}") from exc


def resolve_commit(ref: str) -> str:
    payload = json.loads(_request(f"{GITHUB_API}/commits/{ref}"))
    sha = payload.get("sha", "")
    if len(sha) != 40:
        raise CatalogError(f"GitHub returned an invalid commit for ref {ref!r}")
    return sha


def download_archive(commit: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / f"pokemon-tcg-data-{commit}.tar.gz"
    if destination.exists() and destination.stat().st_size > 0:
        return destination

    payload = _request(f"https://codeload.github.com/{REPOSITORY}/tar.gz/{commit}")
    with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, destination)
    return destination


class Snapshot:
    def __init__(self, archive: Path):
        self._tar = tarfile.open(archive, "r:gz")
        self._members = {member.name.split("/", 1)[-1]: member for member in self._tar.getmembers() if member.isfile()}

    def close(self) -> None:
        self._tar.close()

    def __enter__(self) -> "Snapshot":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def json(self, relative_path: str) -> Any:
        member = self._members.get(relative_path)
        if member is None:
            raise CatalogError(f"Snapshot is missing {relative_path}")
        extracted = self._tar.extractfile(member)
        if extracted is None:
            raise CatalogError(f"Could not read {relative_path}")
        return json.load(io.TextIOWrapper(extracted, encoding="utf-8"))


def _parse_release_date(value: str) -> date:
    return date.fromisoformat(value.replace("/", "-"))


def select_live_sets(
    sets: Iterable[dict[str, Any]], policy: dict[str, Any], as_of: date
) -> list[dict[str, Any]]:
    supported = set(policy["supported_series"])
    included = set(policy["included_set_ids"])
    excluded = set(policy["excluded_set_ids"])
    selected = []
    for item in sets:
        set_id = item["id"]
        series_match = item.get("series") in supported
        if (series_match or set_id in included) and set_id not in excluded:
            release_date = item.get("releaseDate")
            if release_date and _parse_release_date(release_date) <= as_of:
                selected.append(item)
    return sorted(selected, key=lambda item: (_parse_release_date(item["releaseDate"]), item["id"]))


def enrich_card(card: dict[str, Any], set_data: dict[str, Any], commit: str) -> dict[str, Any]:
    result = dict(card)
    # The repository stores cards in one file per set and omits the embedded
    # set object used by the API. Reattach it so each exported record is whole.
    result["set"] = dict(set_data)
    result["catalog"] = {
        "livePlayable": True,
        "standardLegal": card.get("legalities", {}).get("standard") == "Legal",
        "expandedLegal": card.get("legalities", {}).get("expanded") == "Legal",
        "setReleaseDate": set_data.get("releaseDate"),
        "sourceCommit": commit,
    }
    return result


def validate(cards: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for card in cards:
        card_id = card.get("id")
        if not isinstance(card_id, str) or not card_id:
            raise CatalogError("Every card must have a non-empty string id")
        if card_id in seen:
            duplicates.add(card_id)
        seen.add(card_id)
        if not card.get("name") or not card.get("set", {}).get("id"):
            raise CatalogError(f"Card {card_id} is missing its name or set id")
    if duplicates:
        raise CatalogError(f"Duplicate card ids: {sorted(duplicates)[:10]}")


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    return text.encode("utf-8")


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_jsonl(path: Path, cards: list[dict[str, Any]]) -> None:
    payload = b"".join(_json_bytes(card) for card in cards)
    _atomic_bytes(path, payload)


def _write_csv(path: Path, cards: list[dict[str, Any]]) -> None:
    output = io.StringIO(newline="")
    fields = [
        "id", "name", "set_id", "set_name", "number", "supertype", "subtypes",
        "types", "hp", "rarity", "artist", "regulation_mark", "release_date",
        "standard_legal", "expanded_legal", "image_small", "image_large",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for card in cards:
        writer.writerow({
            "id": card["id"],
            "name": card.get("name"),
            "set_id": card.get("set", {}).get("id"),
            "set_name": card.get("set", {}).get("name"),
            "number": card.get("number"),
            "supertype": card.get("supertype"),
            "subtypes": "|".join(card.get("subtypes", [])),
            "types": "|".join(card.get("types", [])),
            "hp": card.get("hp"),
            "rarity": card.get("rarity"),
            "artist": card.get("artist"),
            "regulation_mark": card.get("regulationMark"),
            "release_date": card["catalog"]["setReleaseDate"],
            "standard_legal": card["catalog"]["standardLegal"],
            "expanded_legal": card["catalog"]["expandedLegal"],
            "image_small": card.get("images", {}).get("small"),
            "image_large": card.get("images", {}).get("large"),
        })
    _atomic_bytes(path, output.getvalue().encode("utf-8"))


def _write_sqlite(path: Path, cards: list[dict[str, Any]], commit: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            CREATE TABLE cards (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                set_id TEXT NOT NULL,
                set_name TEXT NOT NULL,
                number TEXT,
                supertype TEXT,
                hp TEXT,
                rarity TEXT,
                regulation_mark TEXT,
                release_date TEXT NOT NULL,
                standard_legal INTEGER NOT NULL,
                expanded_legal INTEGER NOT NULL,
                image_small TEXT,
                image_large TEXT,
                raw_json TEXT NOT NULL
            );
            CREATE INDEX cards_name_idx ON cards(name);
            CREATE INDEX cards_set_idx ON cards(set_id, number);
            CREATE INDEX cards_standard_idx ON cards(standard_legal);
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        rows = []
        for card in cards:
            rows.append((
                card["id"], card["name"], card["set"]["id"], card["set"]["name"],
                card.get("number"), card.get("supertype"), card.get("hp"), card.get("rarity"),
                card.get("regulationMark"), card["catalog"]["setReleaseDate"],
                int(card["catalog"]["standardLegal"]), int(card["catalog"]["expandedLegal"]),
                card.get("images", {}).get("small"), card.get("images", {}).get("large"),
                json.dumps(card, ensure_ascii=False, separators=(",", ":")),
            ))
        connection.executemany("INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [("source_commit", commit), ("card_count", str(len(cards)))],
        )
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_catalog(
    *,
    output_dir: Path,
    cache_dir: Path,
    ref: str = "master",
    as_of: date | None = None,
    policy_path: Path | None = None,
    archive_path: Path | None = None,
    commit: str | None = None,
) -> BuildResult:
    as_of = as_of or datetime.now(timezone.utc).date()
    policy = load_policy(policy_path)
    if archive_path is None:
        commit = commit or resolve_commit(ref)
        archive_path = download_archive(commit, cache_dir)
    elif commit is None:
        commit = "offline-snapshot"

    warnings: list[str] = []
    with Snapshot(archive_path) as snapshot:
        all_sets = snapshot.json("sets/en.json")
        selected_sets = select_live_sets(all_sets, policy, as_of)
        cards: list[dict[str, Any]] = []
        for set_data in selected_sets:
            set_cards = snapshot.json(f"cards/en/{set_data['id']}.json")
            declared = set_data.get("total")
            if isinstance(declared, int) and declared != len(set_cards):
                warnings.append(
                    f"{set_data['id']}: metadata total={declared}, source rows={len(set_cards)}"
                )
            cards.extend(enrich_card(card, set_data, commit) for card in set_cards)

    cards.sort(key=lambda card: (card["catalog"]["setReleaseDate"], card["set"]["id"], card["id"]))
    validate(cards)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "cards.jsonl"
    csv_path = output_dir / "cards.csv"
    sqlite_path = output_dir / "cards.sqlite3"
    sets_path = output_dir / "sets.json"
    _write_jsonl(jsonl_path, cards)
    _write_csv(csv_path, cards)
    _write_sqlite(sqlite_path, cards, commit)
    _atomic_bytes(sets_path, _json_bytes(selected_sets, pretty=True))

    files_manifest = {}
    for path in (jsonl_path, csv_path, sqlite_path, sets_path):
        files_manifest[path.name] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
    manifest = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "asOf": as_of.isoformat(),
        "source": {"repository": REPOSITORY, "commit": commit},
        "policy": policy,
        "counts": {"sets": len(selected_sets), "cards": len(cards)},
        "warnings": warnings,
        "files": files_manifest,
    }
    _atomic_bytes(output_dir / "manifest.json", _json_bytes(manifest, pretty=True))
    return BuildResult(commit, len(selected_sets), len(cards), output_dir, tuple(warnings))
