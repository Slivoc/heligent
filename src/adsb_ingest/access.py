from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .postgres import PostgresStore


PROJECT_ROOT = Path(__file__).parents[2]
PHASE14_MIGRATION = PROJECT_ROOT / "schema" / "phase14.sql"
ROLES = ("VIEWER", "ANALYST", "ADMIN")
ROLE_LEVEL = {role: index for index, role in enumerate(ROLES)}
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def normalize_email(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("User email must be text")
    email = value.strip().lower()
    if len(email) > 320 or EMAIL_RE.fullmatch(email) is None:
        raise ValueError("A valid user email is required")
    return email


def normalize_role(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("User role must be text")
    role = value.strip().upper()
    if role not in ROLES:
        raise ValueError("User role must be VIEWER, ANALYST, or ADMIN")
    return role


def normalize_display_name(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("Display name must be text")
    result = " ".join(value.split())
    if len(result) > 200:
        raise ValueError("Display name is capped at 200 characters")
    return result or None


@dataclass(frozen=True)
class AccessUser:
    email: str
    display_name: str | None
    role: str
    active: bool

    def permits(self, required_role: str) -> bool:
        return self.active and ROLE_LEVEL[self.role] >= ROLE_LEVEL[required_role]

    def public_json(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
        }


class AccessStore(PostgresStore):
    def apply_migration(self, path: Path = PHASE14_MIGRATION) -> None:
        self.apply_schema_once(path)

    def bootstrap_admins(self, emails: list[str]) -> int:
        normalized = [normalize_email(item) for item in emails]
        if not normalized:
            return 0
        with self.connect() as connection:
            before = connection.execute(
                "SELECT count(*) FROM heligent_user WHERE email = ANY(%s)",
                (normalized,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO heligent_user (email, role)
                SELECT email, 'ADMIN'
                FROM unnest(%s::text[]) AS email
                ON CONFLICT (email) DO NOTHING
                """,
                (normalized,),
            )
            connection.commit()
            after = connection.execute(
                "SELECT count(*) FROM heligent_user WHERE email = ANY(%s)",
                (normalized,),
            ).fetchone()[0]
        return int(after - before)

    def get_user(self, email: str) -> AccessUser | None:
        normalized = normalize_email(email)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT email, display_name, role, active
                FROM heligent_user
                WHERE email = %s
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return AccessUser(str(row[0]), row[1], str(row[2]), bool(row[3]))

    def active_admin_count(self) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM heligent_user WHERE role = 'ADMIN' AND active"
                ).fetchone()[0]
            )

    def touch_user(self, email: str, display_name: str | None) -> None:
        normalized = normalize_email(email)
        name = normalize_display_name(display_name)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE heligent_user
                SET display_name = COALESCE(%s, display_name),
                    last_seen_at = clock_timestamp(),
                    updated_at = CASE
                        WHEN %s IS NOT NULL AND display_name IS DISTINCT FROM %s
                        THEN clock_timestamp()
                        ELSE updated_at
                    END
                WHERE email = %s
                  AND (
                      last_seen_at IS NULL
                      OR last_seen_at < clock_timestamp() - interval '5 minutes'
                      OR (%s IS NOT NULL AND display_name IS DISTINCT FROM %s)
                  )
                """,
                (name, name, name, normalized, name, name),
            )
            connection.commit()

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                SELECT email, display_name, role, active, created_at, updated_at,
                       last_seen_at
                FROM heligent_user
                ORDER BY active DESC, role DESC, email
                """
            ).fetchall()

    def upsert_user(
        self,
        email: str,
        *,
        role: str,
        display_name: str | None = None,
        active: bool = True,
        actor_email: str | None = None,
    ) -> dict[str, Any]:
        normalized_email = normalize_email(email)
        normalized_role = normalize_role(role)
        normalized_name = normalize_display_name(display_name)
        normalized_actor = normalize_email(actor_email) if actor_email else None
        if not isinstance(active, bool):
            raise ValueError("active must be true or false")
        if (
            normalized_email == normalized_actor
            and (normalized_role != "ADMIN" or not active)
        ):
            raise ValueError("Administrators cannot reduce their own access")
        with self.connect() as connection:
            connection.row_factory = dict_row
            connection.execute(
                "LOCK TABLE heligent_user IN SHARE ROW EXCLUSIVE MODE"
            )
            existing = connection.execute(
                "SELECT role, active FROM heligent_user WHERE email = %s FOR UPDATE",
                (normalized_email,),
            ).fetchone()
            if (
                existing is not None
                and existing["role"] == "ADMIN"
                and existing["active"]
                and (normalized_role != "ADMIN" or not active)
            ):
                active_admins = connection.execute(
                    """
                    SELECT count(*) AS active_admins
                    FROM heligent_user
                    WHERE role = 'ADMIN' AND active
                    """
                ).fetchone()["active_admins"]
                if active_admins <= 1:
                    raise ValueError("The final active administrator cannot be removed")
            row = connection.execute(
                """
                INSERT INTO heligent_user (email, display_name, role, active)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    display_name = COALESCE(EXCLUDED.display_name,
                                            heligent_user.display_name),
                    role = EXCLUDED.role,
                    active = EXCLUDED.active,
                    updated_at = clock_timestamp()
                RETURNING email, display_name, role, active, created_at,
                          updated_at, last_seen_at
                """,
                (normalized_email, normalized_name, normalized_role, active),
            ).fetchone()
            connection.commit()
        return row

    def deactivate_user(
        self, email: str, *, actor_email: str | None = None
    ) -> dict[str, Any] | None:
        normalized_email = normalize_email(email)
        normalized_actor = normalize_email(actor_email) if actor_email else None
        if normalized_email == normalized_actor:
            raise ValueError("Administrators cannot deactivate their own account")
        with self.connect() as connection:
            connection.row_factory = dict_row
            connection.execute(
                "LOCK TABLE heligent_user IN SHARE ROW EXCLUSIVE MODE"
            )
            existing = connection.execute(
                "SELECT role, active FROM heligent_user WHERE email = %s FOR UPDATE",
                (normalized_email,),
            ).fetchone()
            if existing is None or not existing["active"]:
                return None
            if existing["role"] == "ADMIN":
                active_admins = connection.execute(
                    """
                    SELECT count(*) AS active_admins
                    FROM heligent_user
                    WHERE role = 'ADMIN' AND active
                    """
                ).fetchone()["active_admins"]
                if active_admins <= 1:
                    raise ValueError("The final active administrator cannot be removed")
            row = connection.execute(
                """
                UPDATE heligent_user
                SET active = false, updated_at = clock_timestamp()
                WHERE email = %s AND active
                RETURNING email, display_name, role, active, created_at,
                          updated_at, last_seen_at
                """,
                (normalized_email,),
            ).fetchone()
            connection.commit()
        return row

    def record_audit_event(
        self,
        *,
        actor: AccessUser,
        action: str,
        method: str,
        path: str,
        request_id: str,
        remote_address: str | None,
        response_status: int,
        target: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO heligent_audit_event (
                    actor_email, actor_role, action, request_method, request_path,
                    request_id, remote_address, response_status, target, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::inet, %s, %s, %s)
                """,
                (
                    actor.email,
                    actor.role,
                    action[:160],
                    method,
                    path[:1000],
                    request_id[:200],
                    remote_address,
                    response_status,
                    target[:500] if target else None,
                    Jsonb(metadata or {}),
                ),
            )
            connection.commit()


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Heligent maintenance users")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add = subparsers.add_parser("add", help="Add or update an externally authenticated user")
    add.add_argument("email")
    add.add_argument("--role", required=True, choices=ROLES)
    add.add_argument("--name")
    subparsers.add_parser("list", help="List configured users")
    deactivate = subparsers.add_parser("deactivate", help="Disable a user")
    deactivate.add_argument("email")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    store = AccessStore(_database_url())
    store.apply_migration()
    if args.command == "add":
        row = store.upsert_user(args.email, role=args.role, display_name=args.name)
        print(f"{row['email']}\t{row['role']}\tactive={row['active']}")
    elif args.command == "list":
        for row in store.list_users():
            print(
                f"{row['email']}\t{row['role']}\tactive={row['active']}"
                f"\tlast_seen={row['last_seen_at'] or '-'}"
            )
    else:
        normalized = normalize_email(args.email)
        row = store.deactivate_user(normalized)
        if row is None:
            raise SystemExit(f"User not found: {normalized}")
        print(f"{row['email']}\t{row['role']}\tactive={row['active']}")


if __name__ == "__main__":
    main()
