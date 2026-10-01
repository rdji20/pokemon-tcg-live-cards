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
    sync.add_argument("--database-url", default=None, help="Import into PostgreSQL after syncing")
    import_db = subparsers.add_parser("import-db", help="Import generated catalog files into PostgreSQL")
    import_db.add_argument("--data", type=Path, default=Path("data"))
    import_db.add_argument("--migrations", type=Path, default=Path("db/migrations"))
    import_db.add_argument("--database-url", default=None)
    report = subparsers.add_parser("report", help="Compare a catalog with the saved baseline")
    report.add_argument("--data", type=Path, default=Path("data"))
    report.add_argument("--baseline", type=Path, default=Path("catalog/baseline.json"))
    report.add_argument("--output", type=Path, default=Path("reports/latest.md"))
    report.add_argument("--update-baseline", action="store_true")
    import_rules = subparsers.add_parser("import-rules", help="Validate and import AI rule drafts")
    import_rules.add_argument("--rules", type=Path, default=Path("rules/generated"))
    import_rules.add_argument("--database-url", default=None)
    ai_rules = subparsers.add_parser("ai-rules", help="Generate executable Standard card-rule drafts")
    ai_rules.add_argument("--output", type=Path, default=Path("rules/generated"))
    ai_rules.add_argument("--limit", type=int, default=25)
    ai_rules.add_argument("--model", default=None)
    ai_rules.add_argument("--database-url", default=None)
    serve = subparsers.add_parser("serve", help="Run the local card browser and sync API")
    serve.add_argument("--directory", type=Path, default=Path("."))
    serve.add_argument("--output", type=Path, default=Path("data"))
    serve.add_argument("--cache", type=Path, default=Path(".cache"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--ref", default="master")
    serve.add_argument("--policy", type=Path, default=None)
    serve.add_argument("--database-url", default=None)
    serve.add_argument("--migrations", type=Path, default=Path("db/migrations"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "serve":
        from .server import run_server

        run_server(
            project_dir=args.directory,
            output_dir=args.output,
            cache_dir=args.cache,
            host=args.host,
            port=args.port,
            ref=args.ref,
            policy_path=args.policy,
            database_url_value=args.database_url,
            migrations_dir=args.migrations,
        )
        return 0
    if args.command == "import-db":
        from .database import import_catalog

        result = import_catalog(
            data_dir=args.data,
            migrations_dir=args.migrations,
            url=args.database_url,
        )
        print(
            f"Imported {result.card_count:,} cards from {result.set_count} sets "
            f"into catalog run {result.catalog_run_id}"
        )
        return 0
    if args.command == "report":
        from .report import write_change_report

        result = write_change_report(
            data_dir=args.data,
            baseline_path=args.baseline,
            output_path=args.output,
            update_baseline=args.update_baseline,
        )
        print(
            f"Catalog report: {len(result['addedCards'])} added, "
            f"{len(result['removedCards'])} removed; "
            f"content changed: {'yes' if result['changed'] else 'no'}"
        )
        return 0
    if args.command == "import-rules":
        from .rule_reviews import import_rule_programs

        result = import_rule_programs(args.rules, args.database_url)
        print(f"Rule drafts: {result['imported']} passed, {result['failed']} failed")
        return 0 if not result["failed"] else 1
    if args.command == "ai-rules":
        from .ai_rules import run_ai_rule_pass

        try:
            result = run_ai_rule_pass(
                output_dir=args.output,
                limit=args.limit,
                url=args.database_url,
                model=args.model,
            )
        except RuntimeError as exc:
            print(f"error: {exc}")
            return 1
        print(
            f"Generated {len(result['generated'])} AI rule drafts with {result['model']}; "
            f"{result['imported']} passed validation"
        )
        return 0
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
        if args.database_url:
            from .database import import_catalog

            imported = import_catalog(
                data_dir=args.output,
                migrations_dir=Path("db/migrations"),
                url=args.database_url,
            )
            print(f"Imported into PostgreSQL catalog run {imported.catalog_run_id}")
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
