from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from psycopg import Connection
from psycopg.types.json import Jsonb

from .postgres import PostgresStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHASE8_MIGRATION = PROJECT_ROOT / "schema" / "phase8.sql"
DEFAULT_TEMPLATE = PROJECT_ROOT / "schema" / "company-import-template.csv"

CSV_FIELDS = (
    "company_name",
    "company_key",
    "source_company_id",
    "legal_name",
    "trading_name",
    "aliases",
    "is_operator",
    "is_mro",
    "company_country_code",
    "website",
    "company_active",
    "company_notes",
    "source_updated_at",
    "site_name",
    "site_key",
    "source_site_id",
    "airport_ident",
    "address_line_1",
    "address_line_2",
    "locality",
    "region",
    "postal_code",
    "site_country_code",
    "latitude_deg",
    "longitude_deg",
    "is_primary_site",
    "is_base_maintenance_site",
    "is_line_maintenance_site",
    "site_active",
    "authority_code",
    "approval_type",
    "approval_number",
    "source_approval_id",
    "approval_status",
    "approval_valid_from",
    "approval_valid_to",
    "approval_source_url",
    "approval_last_verified_at",
    "approval_notes",
    "site_is_primary_for_approval",
    "capability_kind",
    "source_capability_id",
    "rating_class",
    "rating_code",
    "manufacturer",
    "model",
    "aircraft_type_code",
    "limitation",
    "capability_base_maintenance",
    "capability_line_maintenance",
    "capability_active",
    "aircraft_registration",
    "aircraft_address",
    "aircraft_assignment_role",
    "source_aircraft_assignment_id",
    "aircraft_assignment_valid_from",
    "aircraft_assignment_valid_to",
    "aircraft_assignment_confidence",
    "aircraft_assignment_active",
)

BOOLEAN_TRUE = frozenset({"1", "true", "t", "yes", "y"})
BOOLEAN_FALSE = frozenset({"0", "false", "f", "no", "n"})
APPROVAL_STATUSES = frozenset(
    {"VALID", "PENDING", "SUSPENDED", "REVOKED", "EXPIRED", "UNKNOWN"}
)
CAPABILITY_KINDS = frozenset(
    {"AIRCRAFT", "ENGINE", "COMPONENT", "SPECIALIST", "SERVICE", "OTHER"}
)
ASSIGNMENT_ROLES = frozenset({"OPERATOR", "OWNER", "MANAGER", "AOC_AUTHORIZED", "OTHER"})
SOURCE_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{1,63}$")
COMPANY_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
AIRCRAFT_ADDRESS_RE = re.compile(r"^(?:~[0-9a-f]{6}|[0-9a-f]{6})$")
EXCEL_1900_EPOCH = datetime(1899, 12, 30)
EXCEL_DATE_FORMATS = (
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%m/%d/%Y",
    "%m/%d/%y",
)
EXCEL_DATETIME_FORMATS = tuple(
    f"{date_format} {time_format}"
    for date_format in EXCEL_DATE_FORMATS
    for time_format in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p")
)


class CompanyImportError(ValueError):
    pass


@dataclass(frozen=True)
class CompanyImportRow:
    row_number: int
    raw: dict[str, str]
    company_name: str
    company_key: str
    source_company_id: str
    legal_name: str | None
    trading_name: str | None
    aliases: tuple[str, ...]
    is_operator: bool
    is_mro: bool
    company_country_code: str | None
    website: str | None
    company_active: bool
    company_notes: str | None
    source_updated_at: datetime | None
    site_name: str | None
    site_key: str | None
    source_site_id: str | None
    airport_ident: str | None
    address_line_1: str | None
    address_line_2: str | None
    locality: str | None
    region: str | None
    postal_code: str | None
    site_country_code: str | None
    latitude_deg: float | None
    longitude_deg: float | None
    is_primary_site: bool
    is_base_maintenance_site: bool
    is_line_maintenance_site: bool
    site_active: bool
    authority_code: str | None
    approval_type: str | None
    approval_number: str | None
    source_approval_id: str | None
    approval_status: str | None
    approval_valid_from: date | None
    approval_valid_to: date | None
    approval_source_url: str | None
    approval_last_verified_at: datetime | None
    approval_notes: str | None
    site_is_primary_for_approval: bool
    capability_kind: str | None
    source_capability_id: str | None
    rating_class: str | None
    rating_code: str | None
    manufacturer: str | None
    model: str | None
    aircraft_type_code: str | None
    limitation: str | None
    capability_base_maintenance: bool
    capability_line_maintenance: bool
    capability_active: bool
    aircraft_registration: str | None
    reported_aircraft_address: str | None
    aircraft_assignment_role: str | None
    source_aircraft_assignment_id: str | None
    aircraft_assignment_valid_from: date | None
    aircraft_assignment_valid_to: date | None
    aircraft_assignment_confidence: float | None
    aircraft_assignment_active: bool


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _normalize_alias(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).split())


def _slug(value: str, *, suffix: str | None = None) -> str:
    normalized = _normalize_alias(value).replace(" ", "-")
    normalized = normalized[:112].strip("-._")
    if not normalized:
        normalized = "company"
    if suffix:
        normalized = f"{normalized}-{suffix.lower()}"
    if len(normalized) < 2:
        normalized = f"{normalized}-company"
    return normalized[:128]


def _boolean(value: Any, field: str, *, default: bool) -> bool:
    normalized = (_text(value) or "").lower()
    if not normalized:
        return default
    if normalized in BOOLEAN_TRUE:
        return True
    if normalized in BOOLEAN_FALSE:
        return False
    raise CompanyImportError(f"{field} must be yes/no or true/false")


def _country(value: Any, field: str) -> str | None:
    result = (_text(value) or "").upper()
    if not result:
        return None
    if not re.fullmatch(r"[A-Z]{2}", result):
        raise CompanyImportError(f"{field} must be a two-letter ISO country code")
    return result


def _choice(value: Any, field: str, choices: frozenset[str], *, default: str) -> str:
    result = (_text(value) or default).upper()
    if result not in choices:
        raise CompanyImportError(f"{field} must be one of {', '.join(sorted(choices))}")
    return result


def _excel_datetime(value: Any, field: str) -> datetime | None:
    result = _text(value)
    if not result:
        return None
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except ValueError:
        pass

    if re.fullmatch(r"\d+(?:\.\d+)?", result):
        serial = float(result)
        if math.isfinite(serial):
            try:
                parsed = EXCEL_1900_EPOCH + timedelta(days=serial)
            except OverflowError:
                parsed = None
            if parsed is not None and 1900 <= parsed.year <= 2200:
                return parsed.replace(tzinfo=UTC)

    normalized = re.sub(r"\s+", " ", result).strip()
    for date_format in (*EXCEL_DATETIME_FORMATS, *EXCEL_DATE_FORMATS):
        try:
            return datetime.strptime(normalized, date_format).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise CompanyImportError(
        f"{field} must be ISO, a day-first Excel date, a recognizable month-name date, "
        "or a Windows Excel serial number"
    )


def _date(value: Any, field: str) -> date | None:
    parsed = _excel_datetime(value, field)
    return parsed.date() if parsed else None


def _datetime(value: Any, field: str) -> datetime | None:
    return _excel_datetime(value, field)


def _float(value: Any, field: str) -> float | None:
    result = _text(value)
    if not result:
        return None
    try:
        return float(result)
    except ValueError as exc:
        raise CompanyImportError(f"{field} must be numeric") from exc


def _stable_key(parts: Iterable[Any]) -> str:
    payload = "\x1f".join(str(item or "").strip().lower() for item in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _has_any(row: dict[str, str], fields: Iterable[str]) -> bool:
    return any(_text(row.get(field)) for field in fields)


def parse_company_row(raw: dict[str, str], row_number: int) -> CompanyImportRow:
    try:
        company_name = _text(raw.get("company_name"))
        if not company_name:
            raise CompanyImportError("company_name is required")

        company_country = _country(raw.get("company_country_code"), "company_country_code")
        company_key = (_text(raw.get("company_key")) or _slug(company_name, suffix=company_country)).lower()
        if not COMPANY_KEY_RE.fullmatch(company_key):
            raise CompanyImportError(
                "company_key must be 2-128 lowercase letters, numbers, dots, dashes or underscores"
            )
        source_company_id = _text(raw.get("source_company_id")) or company_key

        aliases = tuple(
            alias.strip() for alias in (_text(raw.get("aliases")) or "").split("|") if alias.strip()
        )

        site_fields = (
            "site_name",
            "site_key",
            "source_site_id",
            "airport_ident",
            "address_line_1",
            "address_line_2",
            "locality",
            "region",
            "postal_code",
            "site_country_code",
            "latitude_deg",
            "longitude_deg",
        )
        has_site = _has_any(raw, site_fields)
        site_name = _text(raw.get("site_name")) if has_site else None
        if has_site and not site_name:
            site_name = _text(raw.get("airport_ident")) or company_name
        source_site_id = _text(raw.get("source_site_id")) if has_site else None
        site_key = _text(raw.get("site_key")) if has_site else None
        if has_site and not site_key:
            site_key = _slug(source_site_id or site_name or "site")

        latitude = _float(raw.get("latitude_deg"), "latitude_deg")
        longitude = _float(raw.get("longitude_deg"), "longitude_deg")
        if (latitude is None) != (longitude is None):
            raise CompanyImportError("latitude_deg and longitude_deg must be supplied together")
        if latitude is not None and not -90 <= latitude <= 90:
            raise CompanyImportError("latitude_deg must be between -90 and 90")
        if longitude is not None and not -180 <= longitude <= 180:
            raise CompanyImportError("longitude_deg must be between -180 and 180")

        approval_fields = (
            "authority_code",
            "approval_type",
            "approval_number",
            "source_approval_id",
            "approval_status",
            "approval_valid_from",
            "approval_valid_to",
        )
        has_approval = _has_any(raw, approval_fields)
        authority_code = (_text(raw.get("authority_code")) or "").upper() or None
        approval_type = (_text(raw.get("approval_type")) or "").upper() or None
        approval_number = _text(raw.get("approval_number"))
        if has_approval and not all((authority_code, approval_type, approval_number)):
            raise CompanyImportError(
                "authority_code, approval_type and approval_number are all required for an approval"
            )
        source_approval_id = _text(raw.get("source_approval_id")) if has_approval else None
        if has_approval and not source_approval_id:
            source_approval_id = f"{authority_code}:{approval_type}:{approval_number}"
        approval_status = (
            _choice(raw.get("approval_status"), "approval_status", APPROVAL_STATUSES, default="VALID")
            if has_approval
            else None
        )
        approval_valid_from = _date(raw.get("approval_valid_from"), "approval_valid_from")
        approval_valid_to = _date(raw.get("approval_valid_to"), "approval_valid_to")
        if approval_valid_from and approval_valid_to and approval_valid_from > approval_valid_to:
            raise CompanyImportError("approval_valid_from cannot be after approval_valid_to")

        capability_fields = (
            "capability_kind",
            "source_capability_id",
            "rating_class",
            "rating_code",
            "manufacturer",
            "model",
            "aircraft_type_code",
            "limitation",
            "capability_base_maintenance",
            "capability_line_maintenance",
        )
        has_capability = _has_any(raw, capability_fields)
        if has_capability and not has_approval:
            raise CompanyImportError("capability fields require an approval on the same row")
        default_capability_kind = (
            "AIRCRAFT"
            if _has_any(raw, ("manufacturer", "model", "aircraft_type_code"))
            else "OTHER"
        )
        capability_kind = (
            _choice(
                raw.get("capability_kind"),
                "capability_kind",
                CAPABILITY_KINDS,
                default=default_capability_kind,
            )
            if has_capability
            else None
        )
        source_capability_id = _text(raw.get("source_capability_id")) if has_capability else None
        if has_capability and not source_capability_id:
            source_capability_id = _stable_key(
                (
                    source_approval_id,
                    source_site_id or site_key,
                    capability_kind,
                    raw.get("rating_class"),
                    raw.get("rating_code"),
                    raw.get("manufacturer"),
                    raw.get("model"),
                    raw.get("aircraft_type_code"),
                    raw.get("limitation"),
                )
            )

        registration = (_text(raw.get("aircraft_registration")) or "").upper() or None
        reported_address = (_text(raw.get("aircraft_address")) or "").lower() or None
        if reported_address and not AIRCRAFT_ADDRESS_RE.fullmatch(reported_address):
            raise CompanyImportError("aircraft_address must be six hexadecimal digits")
        has_assignment = bool(registration or reported_address)
        assignment_role = (
            _choice(
                raw.get("aircraft_assignment_role"),
                "aircraft_assignment_role",
                ASSIGNMENT_ROLES,
                default="OPERATOR",
            )
            if has_assignment
            else None
        )
        assignment_id = _text(raw.get("source_aircraft_assignment_id")) if has_assignment else None
        if has_assignment and not assignment_id:
            assignment_id = _stable_key(
                (source_company_id, source_approval_id, registration, reported_address, assignment_role)
            )
        assignment_from = _date(
            raw.get("aircraft_assignment_valid_from"), "aircraft_assignment_valid_from"
        )
        assignment_to = _date(
            raw.get("aircraft_assignment_valid_to"), "aircraft_assignment_valid_to"
        )
        if assignment_from and assignment_to and assignment_from > assignment_to:
            raise CompanyImportError(
                "aircraft_assignment_valid_from cannot be after aircraft_assignment_valid_to"
            )
        confidence = _float(
            raw.get("aircraft_assignment_confidence"), "aircraft_assignment_confidence"
        )
        if confidence is not None and not 0 <= confidence <= 1:
            raise CompanyImportError("aircraft_assignment_confidence must be between 0 and 1")

        aircraft_type_code = (_text(raw.get("aircraft_type_code")) or "").upper() or None
        airport_ident = (_text(raw.get("airport_ident")) or "").upper() or None

        return CompanyImportRow(
            row_number=row_number,
            raw={key: value or "" for key, value in raw.items()},
            company_name=company_name,
            company_key=company_key,
            source_company_id=source_company_id,
            legal_name=_text(raw.get("legal_name")),
            trading_name=_text(raw.get("trading_name")),
            aliases=aliases,
            is_operator=_boolean(raw.get("is_operator"), "is_operator", default=False),
            is_mro=_boolean(raw.get("is_mro"), "is_mro", default=False),
            company_country_code=company_country,
            website=_text(raw.get("website")),
            company_active=_boolean(raw.get("company_active"), "company_active", default=True),
            company_notes=_text(raw.get("company_notes")),
            source_updated_at=_datetime(raw.get("source_updated_at"), "source_updated_at"),
            site_name=site_name,
            site_key=site_key,
            source_site_id=source_site_id,
            airport_ident=airport_ident,
            address_line_1=_text(raw.get("address_line_1")),
            address_line_2=_text(raw.get("address_line_2")),
            locality=_text(raw.get("locality")),
            region=_text(raw.get("region")),
            postal_code=_text(raw.get("postal_code")),
            site_country_code=_country(raw.get("site_country_code"), "site_country_code"),
            latitude_deg=latitude,
            longitude_deg=longitude,
            is_primary_site=_boolean(
                raw.get("is_primary_site"), "is_primary_site", default=False
            ),
            is_base_maintenance_site=_boolean(
                raw.get("is_base_maintenance_site"),
                "is_base_maintenance_site",
                default=False,
            ),
            is_line_maintenance_site=_boolean(
                raw.get("is_line_maintenance_site"),
                "is_line_maintenance_site",
                default=False,
            ),
            site_active=_boolean(raw.get("site_active"), "site_active", default=True),
            authority_code=authority_code,
            approval_type=approval_type,
            approval_number=approval_number,
            source_approval_id=source_approval_id,
            approval_status=approval_status,
            approval_valid_from=approval_valid_from,
            approval_valid_to=approval_valid_to,
            approval_source_url=_text(raw.get("approval_source_url")),
            approval_last_verified_at=_datetime(
                raw.get("approval_last_verified_at"), "approval_last_verified_at"
            ),
            approval_notes=_text(raw.get("approval_notes")),
            site_is_primary_for_approval=_boolean(
                raw.get("site_is_primary_for_approval"),
                "site_is_primary_for_approval",
                default=False,
            ),
            capability_kind=capability_kind,
            source_capability_id=source_capability_id,
            rating_class=_text(raw.get("rating_class")),
            rating_code=_text(raw.get("rating_code")),
            manufacturer=_text(raw.get("manufacturer")),
            model=_text(raw.get("model")),
            aircraft_type_code=aircraft_type_code,
            limitation=_text(raw.get("limitation")),
            capability_base_maintenance=_boolean(
                raw.get("capability_base_maintenance"),
                "capability_base_maintenance",
                default=False,
            ),
            capability_line_maintenance=_boolean(
                raw.get("capability_line_maintenance"),
                "capability_line_maintenance",
                default=False,
            ),
            capability_active=_boolean(
                raw.get("capability_active"), "capability_active", default=True
            ),
            aircraft_registration=registration,
            reported_aircraft_address=reported_address,
            aircraft_assignment_role=assignment_role,
            source_aircraft_assignment_id=assignment_id,
            aircraft_assignment_valid_from=assignment_from,
            aircraft_assignment_valid_to=assignment_to,
            aircraft_assignment_confidence=confidence,
            aircraft_assignment_active=_boolean(
                raw.get("aircraft_assignment_active"),
                "aircraft_assignment_active",
                default=True,
            ),
        )
    except CompanyImportError as exc:
        raise CompanyImportError(f"CSV row {row_number}: {exc}") from exc


def load_company_csv(path: Path) -> tuple[CompanyImportRow, ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise CompanyImportError("CSV has no header row")
        headers = tuple(field.strip() for field in reader.fieldnames)
        unknown = sorted(set(headers) - set(CSV_FIELDS))
        if unknown:
            raise CompanyImportError(f"Unknown CSV column(s): {', '.join(unknown)}")
        if "company_name" not in headers:
            raise CompanyImportError("CSV must include the company_name column")
        rows = tuple(parse_company_row(dict(row), index) for index, row in enumerate(reader, 2))
    if not rows:
        raise CompanyImportError("CSV contains no data rows")
    return rows


def write_company_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        sample = {field: "" for field in CSV_FIELDS}
        sample.update(
            {
                "company_name": "Example Aviation Ltd",
                "company_key": "example-aviation-gb",
                "source_company_id": "UK.145.99999",
                "legal_name": "Example Aviation Limited",
                "trading_name": "Example Aviation",
                "aliases": "Example Air|Example Engineering",
                "is_operator": "yes",
                "is_mro": "yes",
                "company_country_code": "GB",
                "website": "https://example.invalid",
                "company_active": "yes",
                "site_name": "Example Airport Base",
                "source_site_id": "UK.145.99999:BASE",
                "airport_ident": "EGPD",
                "site_country_code": "GB",
                "is_primary_site": "yes",
                "is_base_maintenance_site": "yes",
                "authority_code": "UK_CAA",
                "approval_type": "PART_145",
                "approval_number": "UK.145.99999",
                "approval_status": "VALID",
                "capability_kind": "AIRCRAFT",
                "source_capability_id": "UK.145.99999:A1:H145",
                "rating_class": "A1",
                "manufacturer": "Airbus Helicopters",
                "model": "H145",
                "aircraft_type_code": "H145",
                "capability_base_maintenance": "yes",
            }
        )
        writer.writerow(sample)


class CompanyStore:
    def __init__(self, database_url: str) -> None:
        self.store = PostgresStore(database_url)

    def apply_migration(self, path: Path = PHASE8_MIGRATION) -> None:
        self.store.apply_schema(path)

    @staticmethod
    def _airport_ident(connection: Connection, code: str | None, row_number: int) -> str | None:
        if not code:
            return None
        rows = connection.execute(
            """
            SELECT ident
            FROM airport
            WHERE upper(ident) = %s
               OR upper(COALESCE(gps_code, '')) = %s
               OR upper(COALESCE(iata_code, '')) = %s
            ORDER BY CASE WHEN upper(ident) = %s THEN 0 ELSE 1 END, ident
            LIMIT 2
            """,
            (code, code, code, code),
        ).fetchall()
        if not rows:
            raise CompanyImportError(
                f"CSV row {row_number}: airport_ident {code!r} is not in the airport reference table"
            )
        if len(rows) > 1 and code not in {str(item[0]).upper() for item in rows}:
            raise CompanyImportError(
                f"CSV row {row_number}: airport code {code!r} matches more than one airport"
            )
        return str(rows[0][0])

    @staticmethod
    def _upsert_company(
        connection: Connection, row: CompanyImportRow, source_code: str, batch_id: int
    ) -> int:
        mapped = connection.execute(
            """
            SELECT company_id FROM company_external_identifier
            WHERE source_code = %s AND external_id = %s
            """,
            (source_code, row.source_company_id),
        ).fetchone()
        if mapped:
            company_id = int(mapped[0])
            connection.execute(
                """
                UPDATE company SET
                    name = %s,
                    legal_name = COALESCE(%s, legal_name),
                    trading_name = COALESCE(%s, trading_name),
                    is_operator = is_operator OR %s,
                    is_mro = is_mro OR %s,
                    country_code = COALESCE(%s, country_code),
                    geographic_region = COALESCE(
                        heligent_world_region(%s), geographic_region
                    ),
                    website = COALESCE(%s, website),
                    active = %s,
                    notes = COALESCE(%s, notes),
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (
                    row.company_name,
                    row.legal_name,
                    row.trading_name,
                    row.is_operator,
                    row.is_mro,
                    row.company_country_code,
                    row.company_country_code,
                    row.website,
                    row.company_active,
                    row.company_notes,
                    company_id,
                ),
            )
        else:
            result = connection.execute(
                """
                INSERT INTO company (
                    company_key, name, legal_name, trading_name, is_operator, is_mro,
                    country_code, geographic_region, website, active, notes
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    heligent_world_region(%s), %s, %s, %s
                )
                ON CONFLICT (company_key) DO UPDATE SET
                    name = EXCLUDED.name,
                    legal_name = COALESCE(EXCLUDED.legal_name, company.legal_name),
                    trading_name = COALESCE(EXCLUDED.trading_name, company.trading_name),
                    is_operator = company.is_operator OR EXCLUDED.is_operator,
                    is_mro = company.is_mro OR EXCLUDED.is_mro,
                    country_code = COALESCE(EXCLUDED.country_code, company.country_code),
                    geographic_region = COALESCE(
                        EXCLUDED.geographic_region, company.geographic_region
                    ),
                    website = COALESCE(EXCLUDED.website, company.website),
                    active = EXCLUDED.active,
                    notes = COALESCE(EXCLUDED.notes, company.notes),
                    updated_at = clock_timestamp()
                RETURNING id
                """,
                (
                    row.company_key,
                    row.company_name,
                    row.legal_name,
                    row.trading_name,
                    row.is_operator,
                    row.is_mro,
                    row.company_country_code,
                    row.company_country_code,
                    row.website,
                    row.company_active,
                    row.company_notes,
                ),
            ).fetchone()
            assert result is not None
            company_id = int(result[0])

        connection.execute(
            """
            INSERT INTO company_external_identifier (
                source_code, external_id, company_id, source_updated_at,
                last_seen_batch_id, active, raw_data
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_code, external_id) DO UPDATE SET
                company_id = EXCLUDED.company_id,
                source_updated_at = EXCLUDED.source_updated_at,
                last_seen_batch_id = EXCLUDED.last_seen_batch_id,
                active = EXCLUDED.active,
                raw_data = EXCLUDED.raw_data,
                imported_at = clock_timestamp()
            """,
            (
                source_code,
                row.source_company_id,
                company_id,
                row.source_updated_at,
                batch_id,
                row.company_active,
                Jsonb(row.raw),
            ),
        )

        aliases = (
            (row.company_name, "SOURCE"),
            (row.legal_name, "LEGAL"),
            (row.trading_name, "TRADING_AS"),
            *((alias, "OTHER") for alias in row.aliases),
        )
        for alias, alias_type in aliases:
            if not alias:
                continue
            normalized = _normalize_alias(alias)
            if not normalized:
                continue
            connection.execute(
                """
                INSERT INTO company_alias (
                    company_id, alias, normalized_alias, alias_type, source_code
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (company_id, normalized_alias) DO UPDATE SET
                    alias = EXCLUDED.alias,
                    alias_type = CASE
                        WHEN company_alias.alias_type = 'OTHER' THEN EXCLUDED.alias_type
                        ELSE company_alias.alias_type
                    END,
                    source_code = COALESCE(company_alias.source_code, EXCLUDED.source_code)
                """,
                (company_id, alias, normalized, alias_type, source_code),
            )
        return company_id

    def _upsert_site(
        self,
        connection: Connection,
        row: CompanyImportRow,
        source_code: str,
        batch_id: int,
        company_id: int,
    ) -> int | None:
        if not row.site_key or not row.site_name:
            return None
        airport_ident = self._airport_ident(connection, row.airport_ident, row.row_number)
        mapped = None
        if row.source_site_id:
            mapped = connection.execute(
                """
                SELECT company_site_id FROM company_site_external_identifier
                WHERE source_code = %s AND external_id = %s
                """,
                (source_code, row.source_site_id),
            ).fetchone()
        if mapped:
            site_id = int(mapped[0])
            connection.execute(
                """
                UPDATE company_site SET
                    company_id = %s, site_key = %s, name = %s, airport_ident = %s,
                    address_line_1 = COALESCE(%s, address_line_1),
                    address_line_2 = COALESCE(%s, address_line_2),
                    locality = COALESCE(%s, locality), region = COALESCE(%s, region),
                    postal_code = COALESCE(%s, postal_code),
                    country_code = COALESCE(%s, country_code),
                    geographic_region = COALESCE(
                        heligent_world_region(%s), geographic_region
                    ),
                    latitude_deg = COALESCE(%s, latitude_deg),
                    longitude_deg = COALESCE(%s, longitude_deg),
                    is_primary = %s,
                    is_base_maintenance = is_base_maintenance OR %s,
                    is_line_maintenance = is_line_maintenance OR %s,
                    active = %s, updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (
                    company_id,
                    row.site_key,
                    row.site_name,
                    airport_ident,
                    row.address_line_1,
                    row.address_line_2,
                    row.locality,
                    row.region,
                    row.postal_code,
                    row.site_country_code,
                    row.site_country_code,
                    row.latitude_deg,
                    row.longitude_deg,
                    row.is_primary_site,
                    row.is_base_maintenance_site,
                    row.is_line_maintenance_site,
                    row.site_active,
                    site_id,
                ),
            )
        else:
            result = connection.execute(
                """
                INSERT INTO company_site (
                    company_id, site_key, name, airport_ident, address_line_1,
                    address_line_2, locality, region, postal_code, country_code,
                    geographic_region, latitude_deg, longitude_deg, is_primary, is_base_maintenance,
                    is_line_maintenance, active
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    heligent_world_region(%s), %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (company_id, site_key) DO UPDATE SET
                    name = EXCLUDED.name,
                    airport_ident = COALESCE(EXCLUDED.airport_ident, company_site.airport_ident),
                    address_line_1 = COALESCE(EXCLUDED.address_line_1, company_site.address_line_1),
                    address_line_2 = COALESCE(EXCLUDED.address_line_2, company_site.address_line_2),
                    locality = COALESCE(EXCLUDED.locality, company_site.locality),
                    region = COALESCE(EXCLUDED.region, company_site.region),
                    postal_code = COALESCE(EXCLUDED.postal_code, company_site.postal_code),
                    country_code = COALESCE(EXCLUDED.country_code, company_site.country_code),
                    geographic_region = COALESCE(
                        EXCLUDED.geographic_region, company_site.geographic_region
                    ),
                    latitude_deg = COALESCE(EXCLUDED.latitude_deg, company_site.latitude_deg),
                    longitude_deg = COALESCE(EXCLUDED.longitude_deg, company_site.longitude_deg),
                    is_primary = EXCLUDED.is_primary,
                    is_base_maintenance = company_site.is_base_maintenance
                        OR EXCLUDED.is_base_maintenance,
                    is_line_maintenance = company_site.is_line_maintenance
                        OR EXCLUDED.is_line_maintenance,
                    active = EXCLUDED.active,
                    updated_at = clock_timestamp()
                RETURNING id
                """,
                (
                    company_id,
                    row.site_key,
                    row.site_name,
                    airport_ident,
                    row.address_line_1,
                    row.address_line_2,
                    row.locality,
                    row.region,
                    row.postal_code,
                    row.site_country_code,
                    row.site_country_code,
                    row.latitude_deg,
                    row.longitude_deg,
                    row.is_primary_site,
                    row.is_base_maintenance_site,
                    row.is_line_maintenance_site,
                    row.site_active,
                ),
            ).fetchone()
            assert result is not None
            site_id = int(result[0])
        if row.source_site_id:
            connection.execute(
                """
                INSERT INTO company_site_external_identifier (
                    source_code, external_id, company_site_id, last_seen_batch_id,
                    active, raw_data
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_code, external_id) DO UPDATE SET
                    company_site_id = EXCLUDED.company_site_id,
                    last_seen_batch_id = EXCLUDED.last_seen_batch_id,
                    active = EXCLUDED.active,
                    raw_data = EXCLUDED.raw_data,
                    imported_at = clock_timestamp()
                """,
                (
                    source_code,
                    row.source_site_id,
                    site_id,
                    batch_id,
                    row.site_active,
                    Jsonb(row.raw),
                ),
            )
        return site_id

    @staticmethod
    def _upsert_approval(
        connection: Connection,
        row: CompanyImportRow,
        source_code: str,
        batch_id: int,
        company_id: int,
        site_id: int | None,
    ) -> int | None:
        if not row.approval_number or not row.authority_code or not row.approval_type:
            return None
        result = connection.execute(
            """
            INSERT INTO regulatory_approval (
                company_id, authority_code, approval_type, approval_number, status,
                valid_from, valid_to, source_url, last_verified_at, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (authority_code, approval_type, approval_number) DO UPDATE SET
                company_id = EXCLUDED.company_id,
                status = EXCLUDED.status,
                valid_from = COALESCE(EXCLUDED.valid_from, regulatory_approval.valid_from),
                valid_to = COALESCE(EXCLUDED.valid_to, regulatory_approval.valid_to),
                source_url = COALESCE(EXCLUDED.source_url, regulatory_approval.source_url),
                last_verified_at = COALESCE(
                    EXCLUDED.last_verified_at, regulatory_approval.last_verified_at
                ),
                notes = COALESCE(EXCLUDED.notes, regulatory_approval.notes),
                updated_at = clock_timestamp()
            RETURNING id
            """,
            (
                company_id,
                row.authority_code,
                row.approval_type,
                row.approval_number,
                row.approval_status,
                row.approval_valid_from,
                row.approval_valid_to,
                row.approval_source_url,
                row.approval_last_verified_at,
                row.approval_notes,
            ),
        ).fetchone()
        assert result is not None
        approval_id = int(result[0])
        assert row.source_approval_id is not None
        connection.execute(
            """
            INSERT INTO regulatory_approval_external_identifier (
                source_code, external_id, regulatory_approval_id, last_seen_batch_id,
                active, raw_data
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_code, external_id) DO UPDATE SET
                regulatory_approval_id = EXCLUDED.regulatory_approval_id,
                last_seen_batch_id = EXCLUDED.last_seen_batch_id,
                active = EXCLUDED.active,
                raw_data = EXCLUDED.raw_data,
                imported_at = clock_timestamp()
            """,
            (
                source_code,
                row.source_approval_id,
                approval_id,
                batch_id,
                row.approval_status == "VALID",
                Jsonb(row.raw),
            ),
        )
        if site_id is not None:
            connection.execute(
                """
                INSERT INTO company_site_approval (
                    company_site_id, regulatory_approval_id, source_code, is_primary, active
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (company_site_id, regulatory_approval_id) DO UPDATE SET
                    source_code = EXCLUDED.source_code,
                    is_primary = EXCLUDED.is_primary,
                    active = EXCLUDED.active,
                    updated_at = clock_timestamp()
                """,
                (
                    site_id,
                    approval_id,
                    source_code,
                    row.site_is_primary_for_approval,
                    row.approval_status == "VALID",
                ),
            )
        return approval_id

    @staticmethod
    def _upsert_capability(
        connection: Connection,
        row: CompanyImportRow,
        source_code: str,
        batch_id: int,
        approval_id: int | None,
        site_id: int | None,
    ) -> bool:
        if not row.capability_kind or not row.source_capability_id or approval_id is None:
            return False
        connection.execute(
            """
            INSERT INTO approval_capability (
                regulatory_approval_id, company_site_id, source_code, capability_key,
                capability_kind, rating_class, rating_code, manufacturer, model,
                aircraft_type_code, limitation, is_base_maintenance,
                is_line_maintenance, active, last_seen_batch_id, raw_data
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (regulatory_approval_id, source_code, capability_key) DO UPDATE SET
                company_site_id = EXCLUDED.company_site_id,
                capability_kind = EXCLUDED.capability_kind,
                rating_class = EXCLUDED.rating_class,
                rating_code = EXCLUDED.rating_code,
                manufacturer = EXCLUDED.manufacturer,
                model = EXCLUDED.model,
                aircraft_type_code = EXCLUDED.aircraft_type_code,
                limitation = EXCLUDED.limitation,
                is_base_maintenance = EXCLUDED.is_base_maintenance,
                is_line_maintenance = EXCLUDED.is_line_maintenance,
                active = EXCLUDED.active,
                last_seen_batch_id = EXCLUDED.last_seen_batch_id,
                raw_data = EXCLUDED.raw_data,
                updated_at = clock_timestamp()
            """,
            (
                approval_id,
                site_id,
                source_code,
                row.source_capability_id,
                row.capability_kind,
                row.rating_class,
                row.rating_code,
                row.manufacturer,
                row.model,
                row.aircraft_type_code,
                row.limitation,
                row.capability_base_maintenance,
                row.capability_line_maintenance,
                row.capability_active,
                batch_id,
                Jsonb(row.raw),
            ),
        )
        return True

    @staticmethod
    def _upsert_aircraft_assignment(
        connection: Connection,
        row: CompanyImportRow,
        source_code: str,
        batch_id: int,
        company_id: int,
        approval_id: int | None,
    ) -> bool:
        if not row.source_aircraft_assignment_id or not row.aircraft_assignment_role:
            return False
        aircraft_address = None
        if row.reported_aircraft_address:
            found = connection.execute(
                "SELECT address FROM aircraft WHERE address = %s",
                (row.reported_aircraft_address,),
            ).fetchone()
            aircraft_address = str(found[0]) if found else None
        if aircraft_address is None and row.aircraft_registration:
            found = connection.execute(
                """
                SELECT address
                FROM aircraft
                WHERE upper(registration) = %s
                ORDER BY last_seen_date DESC
                LIMIT 2
                """,
                (row.aircraft_registration,),
            ).fetchall()
            aircraft_address = str(found[0][0]) if len(found) == 1 else None
        connection.execute(
            """
            INSERT INTO company_aircraft_assignment (
                company_id, regulatory_approval_id, aircraft_address,
                reported_aircraft_address, registration, assignment_role, source_code,
                external_id, valid_from, valid_to, confidence, active,
                last_seen_batch_id, raw_data
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_code, external_id) DO UPDATE SET
                company_id = EXCLUDED.company_id,
                regulatory_approval_id = EXCLUDED.regulatory_approval_id,
                aircraft_address = COALESCE(
                    EXCLUDED.aircraft_address, company_aircraft_assignment.aircraft_address
                ),
                reported_aircraft_address = COALESCE(
                    EXCLUDED.reported_aircraft_address,
                    company_aircraft_assignment.reported_aircraft_address
                ),
                registration = COALESCE(
                    EXCLUDED.registration, company_aircraft_assignment.registration
                ),
                assignment_role = EXCLUDED.assignment_role,
                valid_from = EXCLUDED.valid_from,
                valid_to = EXCLUDED.valid_to,
                confidence = EXCLUDED.confidence,
                active = EXCLUDED.active,
                last_seen_batch_id = EXCLUDED.last_seen_batch_id,
                raw_data = EXCLUDED.raw_data,
                updated_at = clock_timestamp()
            """,
            (
                company_id,
                approval_id,
                aircraft_address,
                row.reported_aircraft_address,
                row.aircraft_registration,
                row.aircraft_assignment_role,
                source_code,
                row.source_aircraft_assignment_id,
                row.aircraft_assignment_valid_from,
                row.aircraft_assignment_valid_to,
                row.aircraft_assignment_confidence,
                row.aircraft_assignment_active,
                batch_id,
                Jsonb(row.raw),
            ),
        )
        connection.execute(
            """
            UPDATE company_aircraft_assignment caa
            SET
                geographic_region = COALESCE(
                    c.geographic_region,
                    heligent_registration_region(caa.registration)
                ),
                region_basis = CASE
                    WHEN c.geographic_region IS NOT NULL THEN 'COMPANY_COUNTRY'
                    WHEN heligent_registration_region(caa.registration) IS NOT NULL
                        THEN 'REGISTRATION_PREFIX'
                    ELSE NULL
                END,
                updated_at = clock_timestamp()
            FROM company c
            WHERE c.id = caa.company_id
              AND caa.source_code = %s
              AND caa.external_id = %s
            """,
            (source_code, row.source_aircraft_assignment_id),
        )
        return True

    def import_rows(
        self,
        rows: tuple[CompanyImportRow, ...],
        *,
        source_code: str,
        source_name: str,
        source_kind: str,
        authority_code: str | None,
        source_url: str | None,
        data_license: str | None,
        notes: str | None,
        file_path: Path,
    ) -> dict[str, Any]:
        normalized_source = source_code.strip().upper()
        if not SOURCE_CODE_RE.fullmatch(normalized_source):
            raise CompanyImportError(
                "source_code must be 2-64 uppercase letters, numbers, dots, dashes or underscores"
            )
        normalized_kind = source_kind.strip().upper()
        if normalized_kind not in {"REGULATOR", "REGISTRY", "CURATED", "COMMERCIAL", "OTHER"}:
            raise CompanyImportError("Invalid source kind")
        file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        metadata = {"format": "heligent-company-csv-v1", "columns": list(CSV_FIELDS)}

        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO company_data_source (
                    code, name, source_kind, authority_code, source_url,
                    data_license, notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    source_kind = EXCLUDED.source_kind,
                    authority_code = COALESCE(
                        EXCLUDED.authority_code, company_data_source.authority_code
                    ),
                    source_url = COALESCE(EXCLUDED.source_url, company_data_source.source_url),
                    data_license = COALESCE(
                        EXCLUDED.data_license, company_data_source.data_license
                    ),
                    notes = COALESCE(EXCLUDED.notes, company_data_source.notes),
                    updated_at = clock_timestamp()
                """,
                (
                    normalized_source,
                    source_name,
                    normalized_kind,
                    authority_code,
                    source_url,
                    data_license,
                    notes,
                ),
            )
            batch = connection.execute(
                """
                INSERT INTO company_import_batch (
                    source_code, file_name, sha256, input_rows, metadata
                ) VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (normalized_source, file_path.name, file_hash, len(rows), Jsonb(metadata)),
            ).fetchone()
            assert batch is not None
            batch_id = int(batch[0])
            connection.commit()

        company_ids: set[int] = set()
        site_ids: set[int] = set()
        approval_ids: set[int] = set()
        capability_keys: set[tuple[int, str]] = set()
        assignment_ids: set[str] = set()
        try:
            with self.store.connect() as connection:
                for row in rows:
                    company_id = self._upsert_company(
                        connection, row, normalized_source, batch_id
                    )
                    company_ids.add(company_id)
                    site_id = self._upsert_site(
                        connection, row, normalized_source, batch_id, company_id
                    )
                    if site_id is not None:
                        site_ids.add(site_id)
                    approval_id = self._upsert_approval(
                        connection,
                        row,
                        normalized_source,
                        batch_id,
                        company_id,
                        site_id,
                    )
                    if approval_id is not None:
                        approval_ids.add(approval_id)
                    if self._upsert_capability(
                        connection,
                        row,
                        normalized_source,
                        batch_id,
                        approval_id,
                        site_id,
                    ):
                        assert approval_id is not None and row.source_capability_id is not None
                        capability_keys.add((approval_id, row.source_capability_id))
                    if self._upsert_aircraft_assignment(
                        connection,
                        row,
                        normalized_source,
                        batch_id,
                        company_id,
                        approval_id,
                    ):
                        assert row.source_aircraft_assignment_id is not None
                        assignment_ids.add(row.source_aircraft_assignment_id)
                connection.execute(
                    """
                    UPDATE company_import_batch SET
                        status = 'SUCCEEDED', finished_at = clock_timestamp(),
                        imported_rows = %s, company_count = %s, site_count = %s,
                        approval_count = %s, capability_count = %s,
                        aircraft_assignment_count = %s
                    WHERE id = %s
                    """,
                    (
                        len(rows),
                        len(company_ids),
                        len(site_ids),
                        len(approval_ids),
                        len(capability_keys),
                        len(assignment_ids),
                        batch_id,
                    ),
                )
                semantic_refresh_available = connection.execute(
                    "SELECT to_regprocedure('heligent_refresh_nl_operator_cache()') IS NOT NULL"
                ).fetchone()
                if semantic_refresh_available and semantic_refresh_available[0]:
                    connection.execute("SELECT heligent_refresh_nl_operator_cache()")
                connection.commit()
        except Exception as exc:
            with self.store.connect() as connection:
                connection.execute(
                    """
                    UPDATE company_import_batch SET
                        status = 'FAILED', finished_at = clock_timestamp(), error_message = %s
                    WHERE id = %s
                    """,
                    (f"{type(exc).__name__}: {exc}"[:10_000], batch_id),
                )
                connection.commit()
            raise

        return {
            "status": "SUCCEEDED",
            "batch_id": batch_id,
            "source_code": normalized_source,
            "sha256": file_hash,
            "input_rows": len(rows),
            "companies": len(company_ids),
            "sites": len(site_ids),
            "approvals": len(approval_ids),
            "capabilities": len(capability_keys),
            "aircraft_assignments": len(assignment_ids),
        }


def _database_url(value: str | None) -> str:
    result = value or os.getenv("DATABASE_URL")
    if not result:
        raise SystemExit("A PostgreSQL URL is required via --database-url or DATABASE_URL")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and import Heligent operator/MRO company reference data"
    )
    parser.add_argument("--database-url", help="PostgreSQL connection URL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    schema = subparsers.add_parser("apply-schema", help="Apply the Phase 8 company schema")
    schema.add_argument("--schema", type=Path, default=PHASE8_MIGRATION)

    validate = subparsers.add_parser("validate-csv", help="Validate without changing PostgreSQL")
    validate.add_argument("--file", required=True, type=Path)

    template = subparsers.add_parser("write-template", help="Write a documented starter CSV")
    template.add_argument("--output", type=Path, default=DEFAULT_TEMPLATE)

    importer = subparsers.add_parser("import-csv", help="Validate and atomically upsert a CSV")
    importer.add_argument("--file", required=True, type=Path)
    importer.add_argument("--source-code", required=True)
    importer.add_argument("--source-name")
    importer.add_argument(
        "--source-kind",
        default="CURATED",
        choices=("REGULATOR", "REGISTRY", "CURATED", "COMMERCIAL", "OTHER"),
    )
    importer.add_argument("--authority-code")
    importer.add_argument("--source-url")
    importer.add_argument("--license", dest="data_license")
    importer.add_argument("--notes")
    importer.add_argument(
        "--dry-run", action="store_true", help="Validate only; do not connect to PostgreSQL"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "validate-csv":
            rows = load_company_csv(args.file)
            print(json.dumps({"status": "VALID", "rows": len(rows), "file": str(args.file)}))
            return
        if args.command == "write-template":
            write_company_template(args.output)
            print(json.dumps({"status": "WRITTEN", "file": str(args.output)}))
            return
        if args.command == "apply-schema":
            store = CompanyStore(_database_url(args.database_url))
            store.apply_migration(args.schema)
            print(json.dumps({"status": "SCHEMA_APPLIED", "schema": str(args.schema)}))
            return
        if args.command == "import-csv":
            rows = load_company_csv(args.file)
            if args.dry_run:
                print(
                    json.dumps(
                        {"status": "VALID", "rows": len(rows), "file": str(args.file)},
                        indent=2,
                    )
                )
                return
            store = CompanyStore(_database_url(args.database_url))
            store.apply_migration()
            result = store.import_rows(
                rows,
                source_code=args.source_code,
                source_name=args.source_name or args.source_code,
                source_kind=args.source_kind,
                authority_code=args.authority_code,
                source_url=args.source_url,
                data_license=args.data_license,
                notes=args.notes,
                file_path=args.file,
            )
            print(json.dumps(result, indent=2))
            return
        raise AssertionError(f"Unhandled command {args.command}")
    except CompanyImportError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
