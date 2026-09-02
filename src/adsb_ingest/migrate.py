from __future__ import annotations

import argparse
import os
from pathlib import Path

from .postgres import PostgresStore


PROJECT_ROOT = Path(__file__).parents[2]
PHASE2_SCHEMA = PROJECT_ROOT / "schema" / "phase2.sql"
MIGRATIONS = (
    "phase3.sql",
    "phase4.sql",
    "phase6.sql",
    "phase8.sql",
    "phase9.sql",
    "phase10.sql",
    "phase11.sql",
    "phase12.sql",
    "phase13.sql",
    "phase14.sql",
    "phase15.sql",
)


def migrate(database_url: str) -> list[str]:
    store = PostgresStore(database_url)
    applied: list[str] = []
    with store.connect() as connection:
        phase2_exists = connection.execute(
            "SELECT to_regclass('public.dataset_day') IS NOT NULL"
        ).fetchone()[0]
    if not phase2_exists:
        store.apply_schema(PHASE2_SCHEMA)
        applied.append("phase2.sql")

    for name in MIGRATIONS:
        if store.apply_schema_once(PROJECT_ROOT / "schema" / name):
            applied.append(name)
    return applied


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply all Heligent database migrations")
    parser.add_argument("--database-url", help="PostgreSQL connection URL")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    database_url = args.database_url or os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    applied = migrate(database_url)
    if applied:
        print("Applied: " + ", ".join(applied))
    else:
        print("Database schema is already current")


if __name__ == "__main__":
    main()
