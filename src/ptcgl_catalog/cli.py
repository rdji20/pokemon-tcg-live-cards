from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .catalog import CatalogError, build_catalog


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ptcgl-catalog",
        description="Build a reproducible catalog of cards playable in Pokemon TCG Live.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync = subparsers.add_parser("sync", help="Download and build the current catalog")
    sync.add_argument("--output", type=Path, default=Path("data"))
    sync.add_argument("--cache", type=Path, default=Path(".cache"))
    sync.add_argument("--ref", default="master", help="Git ref or 40-character commit")
    sync.add_argument("--as-of", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD")
    sync.add_argument("--policy", type=Path, default=None)
    sync.add_argument("--archive", type=Path, default=None, help="Use a local source tar.gz")
    sync.add_argument("--commit", default=None, help="Commit represented by --archive")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = build_catalog(
            output_dir=args.output,
            cache_dir=args.cache,
            ref=args.ref,
            as_of=args.as_of,
            policy_path=args.policy,
            archive_path=args.archive,
            commit=args.commit,
        )
    except CatalogError as exc:
        print(f"error: {exc}")
        return 1
    print(
        f"Built {result.card_count:,} cards from {result.set_count} sets "
        f"at {result.commit[:12]} in {result.output_dir}"
    )
    for warning in result.warnings:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

