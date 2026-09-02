from __future__ import annotations

from datetime import date, timedelta
import hashlib
from pathlib import Path
import re
from typing import Any

import pycountry
from psycopg.rows import dict_row

from .aircraft_types import resolve_aircraft_type_code
from .postgres import PostgresStore


PROJECT_ROOT = Path(__file__).parents[2]
PHASE4_MIGRATION = PROJECT_ROOT / "schema" / "phase4.sql"
PHASE6_MIGRATION = PROJECT_ROOT / "schema" / "phase6.sql"
PHASE9_MIGRATION = PROJECT_ROOT / "schema" / "phase9.sql"
PHASE10_MIGRATION = PROJECT_ROOT / "schema" / "phase10.sql"
PHASE11_MIGRATION = PROJECT_ROOT / "schema" / "phase11.sql"
PHASE12_MIGRATION = PROJECT_ROOT / "schema" / "phase12.sql"
PHASE15_MIGRATION = PROJECT_ROOT / "schema" / "phase15.sql"
MAX_ANALYTICS_DAYS = 366
ANALYTICS_STATEMENT_TIMEOUT_MS = 30_000
WORLD_REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "AFRICA": ("AFRICA",),
    "AFRICAN": ("AFRICA",),
    "AMERICAS": ("NORTH_AMERICA", "SOUTH_AMERICA"),
    "ASIA": ("ASIA",),
    "ASIAN": ("ASIA",),
    "EUROPE": ("EUROPE",),
    "EUROPEAN": ("EUROPE",),
    "NORTH AMERICA": ("NORTH_AMERICA",),
    "NORTH AMERICAN": ("NORTH_AMERICA",),
    "OCEANIA": ("OCEANIA",),
    "OCEANIAN": ("OCEANIA",),
    "SOUTH AMERICA": ("SOUTH_AMERICA",),
    "SOUTH AMERICAN": ("SOUTH_AMERICA",),
}


def normalize_world_region_query(value: str | None) -> tuple[str | None, tuple[str, ...]]:
    if value is None:
        return None, ()
    if not isinstance(value, str):
        raise ValueError("Geographic region filters must be text")
    label = " ".join(value.replace("_", " ").replace("-", " ").split()).upper()
    if not label:
        return None, ()
    codes = WORLD_REGION_ALIASES.get(label)
    if codes is None:
        raise ValueError(
            "Geographic region must be Africa, Asia, Europe, North America, "
            "South America, Oceania, or the Americas"
        )
    canonical = "AMERICAS" if len(codes) > 1 else codes[0]
    return canonical, codes


def normalize_activity_location_query(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, str):
        raise ValueError("Activity location filters must be text")
    label = " ".join(value.split())
    if not label:
        return None, None
    if len(label) > 100:
        raise ValueError("Activity location filters are capped at 100 characters")
    try:
        country = pycountry.countries.lookup(label)
    except LookupError:
        country_code = None
    else:
        country_code = country.alpha_2
    return label, country_code

FILTERED_CTE = """
WITH filtered AS (
    SELECT
        ad.*,
        COALESCE(ad.resolved_category::text, 'UNKNOWN') AS aircraft_category
    FROM aircraft_day_identity ad
    WHERE ad.utc_date BETWEEN %(start_date)s AND %(end_date)s
      AND (
          %(type_code)s::text IS NULL
          OR COALESCE(ad.type_code, 'UNKNOWN') = %(type_code)s
      )
      AND (
          %(helicopters_only)s::boolean = false
          OR ad.resolved_category = 'ROTORCRAFT'
      )
      AND COALESCE(ad.resolved_category::text, 'UNKNOWN') <> 'GROUND_VEHICLE'
)
"""

SNAPSHOT_FILTERED_CTE = """
WITH filtered AS (
    SELECT
        ad.*,
        COALESCE(ad.category, 'UNKNOWN') AS aircraft_category,
        ad.observations AS observation_count,
        ad.active_hours * 3600.0 AS active_time_seconds,
        ad.airborne_hours * 3600.0 AS airborne_time_seconds,
        ad.ground_active_hours * 3600.0 AS ground_active_time_seconds,
        ad.time_observed_hours * 3600.0 AS time_observed_seconds
    FROM nl_aircraft_activity_cache ad
    WHERE ad.utc_date BETWEEN %(start_date)s AND %(end_date)s
      AND (
          %(type_code)s::text IS NULL
          OR COALESCE(ad.type_code, 'UNKNOWN') = %(type_code)s
      )
      AND (
          %(helicopters_only)s::boolean = false
          OR ad.category = 'ROTORCRAFT'
      )
      AND (
          %(region_query)s::text IS NULL
          OR EXISTS (
              SELECT 1
              FROM nl_aircraft_area_day_cache region
              WHERE region.dataset_day_id = ad.dataset_day_id
                AND region.address = ad.address
                AND region.area_kind = 'REGION'
                AND region.area_code = ANY(%(region_codes)s::text[])
          )
      )
      AND COALESCE(ad.category, 'UNKNOWN') <> 'GROUND_VEHICLE'
)
"""

HELICOPTER_CTE = """
WITH filtered AS (
    SELECT
        ad.*,
        ad.observations AS observation_count,
        ad.active_hours * 3600.0 AS active_time_seconds,
        ad.airborne_hours * 3600.0 AS airborne_time_seconds
    FROM nl_aircraft_activity_cache ad
    WHERE ad.utc_date BETWEEN %(start_date)s AND %(end_date)s
      AND ad.category = 'ROTORCRAFT'
)
"""


def _company_query_params(
    *,
    company_query: str | None = None,
    capability_query: str | None = None,
    location_query: str | None = None,
    region_query: str | None = None,
    approval_query: str | None = None,
    limit: int,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Company result limits must be between 1 and 100")

    def clean(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Company query filters must be text")
        normalized = " ".join(value.split()).lower()
        if not normalized:
            return None
        if len(normalized) > 160:
            raise ValueError("Company query filters are capped at 160 characters")
        return normalized

    company = clean(company_query)
    capability = clean(capability_query)
    location = clean(location_query)
    approval = clean(approval_query)
    region_label, region_codes = normalize_world_region_query(region_query)
    compact_capability = re.sub(r"[^a-z0-9]+", "", capability or "")
    return {
        "company_query": company,
        "company_like": f"%{company}%" if company else None,
        "capability_query": capability,
        "capability_compact_like": f"%{compact_capability}%" if capability else None,
        "location_query": location,
        "location_like": f"%{location}%" if location else None,
        "region_query": region_label,
        "region_codes": list(region_codes),
        "approval_query": approval,
        "approval_like": f"%{approval}%" if approval else None,
        "limit": limit,
    }


class AnalyticsStore:
    def __init__(self, store: PostgresStore) -> None:
        self.store = store

    def apply_phase4_migration(self, path: Path = PHASE4_MIGRATION) -> None:
        self.store.apply_schema_once(path)

    def apply_phase6_migration(self, path: Path = PHASE6_MIGRATION) -> None:
        self.store.apply_schema_once(path)

    def apply_phase9_migration(self, path: Path = PHASE9_MIGRATION) -> None:
        self.store.apply_schema_once(path)

    def apply_phase10_migration(self, path: Path = PHASE10_MIGRATION) -> None:
        self.store.apply_schema_once(path)

    def apply_phase11_migration(self, path: Path = PHASE11_MIGRATION) -> None:
        self.store.apply_schema_once(path)

    def apply_phase12_migration(self, path: Path = PHASE12_MIGRATION) -> None:
        self.store.apply_schema_once(path)

    def apply_phase15_migration(self, path: Path = PHASE15_MIGRATION) -> None:
        self.store.apply_schema_once(path)

    def refresh_nl_operator_cache(self) -> None:
        with self.store.connect() as connection:
            connection.execute("SELECT heligent_refresh_nl_operator_cache()")
            connection.commit()

    def refresh_nl_activity_cache(self, *, rebuild: bool = False) -> None:
        with self.store.connect() as connection:
            connection.execute(
                "SELECT heligent_refresh_nl_activity_cache(%s)",
                (rebuild,),
            )
            connection.commit()

    def refresh_nl_area_cache(self) -> None:
        with self.store.connect() as connection:
            connection.execute("SELECT heligent_refresh_nl_area_cache()")
            connection.commit()

    def refresh_type_classification(self) -> None:
        self.store.apply_schema(PHASE4_MIGRATION)

    def refresh_after_ingestion(self) -> None:
        self.refresh_type_classification()
        self.refresh_nl_activity_cache()
        self.refresh_nl_area_cache()
        self.refresh_nl_operator_cache()

    def _latest_available_date(self) -> date | None:
        with self.store.connect() as connection:
            row = connection.execute("SELECT max(utc_date) FROM aircraft_day").fetchone()
        return row[0] if row else None

    def availability(self) -> dict[str, Any]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT utc_date FROM aircraft_day ORDER BY utc_date"
            ).fetchall()
            company_row = connection.execute(
                """
                SELECT
                    count(*) FILTER (WHERE active) AS companies,
                    count(*) FILTER (WHERE active AND is_mro) AS mros,
                    count(*) FILTER (WHERE active AND is_operator) AS operators,
                    (SELECT count(*) FROM company_site WHERE active) AS sites,
                    (SELECT count(*) FROM regulatory_approval WHERE status = 'VALID')
                        AS valid_approvals,
                    (SELECT count(*) FROM approval_capability WHERE active) AS capabilities,
                    (SELECT count(*) FROM company_aircraft_assignment WHERE active)
                      + (SELECT count(*) FROM aircraft_registry_resolved_cache
                         WHERE registered_operator IS NOT NULL
                           AND operator_effective_date <= CURRENT_DATE
                           AND (ineffective_date IS NULL
                                OR ineffective_date >= CURRENT_DATE))
                        AS aircraft_assignments
                FROM company
                """
            ).fetchone()
        dates = [row[0] for row in rows]
        return {
            "earliest_available_date": dates[0] if dates else None,
            "latest_available_date": dates[-1] if dates else None,
            "available_days": len(dates),
            "available_dates": dates,
            "company_data": {
                "companies": company_row[0],
                "mros": company_row[1],
                "operators": company_row[2],
                "sites": company_row[3],
                "valid_approvals": company_row[4],
                "capabilities": company_row[5],
                "aircraft_assignments": company_row[6],
            },
        }

    def company_sites(
        self,
        *,
        query: str | None = None,
        tracked_only: bool = False,
        limit: int = 50,
        site_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Company-site result limits must be between 1 and 100")
        normalized = " ".join(query.split()).lower() if isinstance(query, str) else None
        if normalized == "":
            normalized = None
        if normalized and len(normalized) > 160:
            raise ValueError("Company-site searches are capped at 160 characters")
        if not isinstance(tracked_only, bool):
            raise ValueError("tracked_only must be true or false")
        params = {
            "query": normalized,
            "query_like": f"%{normalized}%" if normalized else None,
            "tracked_only": tracked_only,
            "limit": limit,
            "site_id": site_id,
        }
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                SELECT
                    s.id AS site_id,
                    c.id AS company_id,
                    c.name AS company,
                    c.is_customer,
                    c.is_mro,
                    c.is_operator,
                    s.name AS site,
                    s.address_line_1,
                    s.address_line_2,
                    s.locality,
                    s.region,
                    s.postal_code,
                    s.country_code,
                    s.is_of_interest,
                    s.interest_notes,
                    s.airport_ident,
                    s.airport_link_method,
                    s.airport_link_updated_at,
                    a.iata_code AS airport_iata,
                    a.name AS airport_name,
                    a.municipality AS airport_municipality,
                    approvals.approval_numbers,
                    COALESCE(approvals.capability_count, 0) AS capability_count
                FROM company_site s
                JOIN company c ON c.id = s.company_id
                LEFT JOIN airport a ON a.ident = s.airport_ident
                LEFT JOIN LATERAL (
                    SELECT
                        string_agg(DISTINCT ra.approval_number, '; '
                            ORDER BY ra.approval_number) AS approval_numbers,
                        count(DISTINCT ac.id) FILTER (WHERE ac.active)
                            AS capability_count
                    FROM company_site_approval csa
                    JOIN regulatory_approval ra
                      ON ra.id = csa.regulatory_approval_id
                    LEFT JOIN approval_capability ac
                      ON ac.regulatory_approval_id = ra.id
                     AND (ac.company_site_id IS NULL OR ac.company_site_id = s.id)
                    WHERE csa.company_site_id = s.id AND csa.active
                ) approvals ON true
                WHERE s.active AND c.active
                  AND (%(site_id)s::bigint IS NULL OR s.id = %(site_id)s)
                  AND (%(tracked_only)s = false OR s.is_of_interest OR c.is_customer)
                  AND (%(query)s::text IS NULL OR
                       lower(concat_ws(' ', c.name, c.legal_name, c.trading_name,
                           s.name, s.address_line_1, s.address_line_2, s.locality,
                           s.region, s.postal_code, s.country_code, s.airport_ident,
                           a.name, a.iata_code, approvals.approval_numbers))
                           LIKE %(query_like)s)
                ORDER BY
                    s.is_of_interest DESC,
                    c.is_customer DESC,
                    (s.airport_ident IS NOT NULL) DESC,
                    c.name,
                    s.name
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()

    def airport_search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if not isinstance(query, str) or len(" ".join(query.split())) < 2:
            raise ValueError("Enter at least two characters to search airports")
        if isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("Airport result limits must be between 1 and 50")
        normalized = " ".join(query.split()).lower()[:120]
        params = {
            "query": normalized,
            "query_upper": normalized.upper(),
            "query_like": f"%{normalized}%",
            "limit": limit,
        }
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                SELECT ident, iata_code, name, municipality, iso_country,
                       latitude_deg, longitude_deg
                FROM airport
                WHERE upper(ident) = %(query_upper)s
                   OR upper(COALESCE(iata_code, '')) = %(query_upper)s
                   OR lower(concat_ws(' ', name, municipality, ident, iata_code,
                       gps_code, local_code)) LIKE %(query_like)s
                ORDER BY
                    CASE
                        WHEN upper(ident) = %(query_upper)s THEN 0
                        WHEN upper(COALESCE(iata_code, '')) = %(query_upper)s THEN 1
                        WHEN lower(name) LIKE %(query_like)s THEN 2
                        ELSE 3
                    END,
                    name,
                    ident
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()

    def update_company_site_tracking(
        self,
        site_id: int,
        *,
        airport_ident: str | None,
        is_of_interest: bool,
        is_customer: bool,
        interest_notes: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(site_id, bool) or site_id < 1:
            raise ValueError("A valid company site is required")
        if not isinstance(is_of_interest, bool) or not isinstance(is_customer, bool):
            raise ValueError("Customer and address-of-interest flags must be true or false")
        if interest_notes is not None:
            if not isinstance(interest_notes, str):
                raise ValueError("Interest notes must be text")
            interest_notes = " ".join(interest_notes.split()) or None
            if interest_notes and len(interest_notes) > 1000:
                raise ValueError("Interest notes are capped at 1,000 characters")
        normalized_airport = None
        if airport_ident is not None:
            if not isinstance(airport_ident, str):
                raise ValueError("Airport identifier must be text")
            normalized_airport = airport_ident.strip().upper() or None
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            site = connection.execute(
                "SELECT company_id FROM company_site WHERE id = %s AND active FOR UPDATE",
                (site_id,),
            ).fetchone()
            if site is None:
                raise ValueError("Company site was not found")
            if normalized_airport is not None:
                airport = connection.execute(
                    "SELECT 1 FROM airport WHERE ident = %s",
                    (normalized_airport,),
                ).fetchone()
                if airport is None:
                    raise ValueError("The selected airport does not exist")
            connection.execute(
                "UPDATE company SET is_customer = %s, updated_at = clock_timestamp() WHERE id = %s",
                (is_customer, site["company_id"]),
            )
            connection.execute(
                """
                UPDATE company_site
                SET airport_ident = %s,
                    is_of_interest = %s,
                    interest_notes = %s,
                    airport_link_method = CASE WHEN %s::text IS NULL THEN NULL ELSE 'MANUAL' END,
                    airport_link_updated_at = clock_timestamp(),
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (
                    normalized_airport,
                    is_of_interest,
                    interest_notes,
                    normalized_airport,
                    site_id,
                ),
            )
            connection.commit()
        rows = self.company_sites(
            query=None, tracked_only=False, limit=1, site_id=site_id
        )
        for row in rows:
            if row["site_id"] == site_id:
                return row
        raise ValueError("Updated company site could not be reloaded")

    def company_site_capabilities(
        self, site_id: int, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Capability result limits must be between 1 and 100")
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                SELECT DISTINCT
                    ra.approval_number,
                    ra.status AS approval_status,
                    ac.capability_kind,
                    ac.rating_code,
                    ac.manufacturer,
                    ac.model,
                    ac.aircraft_type_code,
                    ac.limitation AS capability
                FROM company_site_approval csa
                JOIN regulatory_approval ra
                  ON ra.id = csa.regulatory_approval_id
                JOIN approval_capability ac
                  ON ac.regulatory_approval_id = ra.id
                 AND (ac.company_site_id IS NULL OR ac.company_site_id = csa.company_site_id)
                WHERE csa.company_site_id = %s
                  AND csa.active AND ac.active
                ORDER BY ra.approval_number, ac.capability_kind,
                         ac.rating_code NULLS LAST, ac.limitation NULLS LAST
                LIMIT %s
                """,
                (site_id, limit),
            ).fetchall()

    def company_site_activity(self, site_id: int, utc_date: date) -> dict[str, Any]:
        matches = self.company_sites(
            query=None, tracked_only=False, limit=1, site_id=site_id
        )
        site = next((row for row in matches if row["site_id"] == site_id), None)
        if site is None:
            raise ValueError("Company site was not found")
        result: dict[str, Any] = {
            "utc_date": utc_date,
            "site": site,
            "capabilities": self.company_site_capabilities(site_id),
            "airport_metrics": None,
            "aircraft": [],
            "types": [],
            "attribution_note": (
                "Activity is measured across the linked airport. It does not prove that "
                "an aircraft visited or used this company site."
            ),
        }
        airport_ident = site.get("airport_ident")
        if not airport_ident:
            return result
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            result["airport_metrics"] = connection.execute(
                """
                SELECT utc_date, airport_ident, airport_name, iata_code,
                       unique_aircraft, movement_candidates, arrival_candidates,
                       departure_candidates, ground_observations, ground_active_hours
                FROM airport_day_metrics
                WHERE utc_date = %s AND airport_ident = %s
                """,
                (utc_date, airport_ident),
            ).fetchone()
        result["aircraft"] = self.airport_aircraft(
            utc_date,
            utc_date,
            airport_code=airport_ident,
            metric="airport_movement_candidates",
            limit=25,
        )
        result["types"] = self.airport_types(
            utc_date,
            utc_date,
            airport_code=airport_ident,
            metric="unique_aircraft",
            limit=25,
        )
        return result

    def company_directory(
        self,
        *,
        company_query: str | None = None,
        location_query: str | None = None,
        region_query: str | None = None,
        company_role: str = "either",
        metric: str = "capability_count",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        order_columns = {
            "capability_count": "capability_count",
            "site_count": "site_count",
            "valid_approval_count": "valid_approval_count",
            "assigned_aircraft_count": "assigned_aircraft_count",
        }
        order_column = order_columns.get(metric)
        if order_column is None:
            raise ValueError("Unsupported company-directory ranking metric")
        params = _company_query_params(
            company_query=company_query,
            location_query=location_query,
            region_query=region_query,
            limit=limit,
        )
        if company_role not in {"operator", "mro", "either"}:
            raise ValueError("Company role must be operator, mro, or either")
        params["company_role"] = company_role
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                f"""
                SELECT
                    c.name AS company,
                    concat_ws(' / ',
                        CASE WHEN c.is_operator THEN 'Operator' END,
                        CASE WHEN c.is_mro THEN 'MRO' END
                    ) AS roles,
                    c.country_code AS country,
                    c.geographic_region,
                    sites.site_names AS sites,
                    sites.locations,
                    approvals.approval_numbers,
                    COALESCE(sites.site_count, 0) AS site_count,
                    COALESCE(approvals.valid_approval_count, 0) AS valid_approval_count,
                    COALESCE(approvals.capability_count, 0) AS capability_count,
                    COALESCE(assignments.assigned_aircraft_count, 0)
                        AS assigned_aircraft_count
                FROM company c
                LEFT JOIN LATERAL (
                    SELECT
                        count(*) AS site_count,
                        string_agg(DISTINCT s.name, '; ' ORDER BY s.name) AS site_names,
                        string_agg(DISTINCT concat_ws(', ', s.locality, s.region,
                            s.postal_code, s.country_code), '; ') AS locations
                    FROM company_site s
                    WHERE s.company_id = c.id AND s.active
                ) sites ON true
                LEFT JOIN LATERAL (
                    SELECT
                        count(DISTINCT ra.id) FILTER (WHERE ra.status = 'VALID')
                            AS valid_approval_count,
                        string_agg(DISTINCT ra.approval_number, '; '
                            ORDER BY ra.approval_number) AS approval_numbers,
                        count(DISTINCT ac.id) FILTER (WHERE ac.active) AS capability_count
                    FROM regulatory_approval ra
                    LEFT JOIN approval_capability ac
                      ON ac.regulatory_approval_id = ra.id
                    WHERE ra.company_id = c.id
                ) approvals ON true
                LEFT JOIN LATERAL (
                    SELECT count(*) AS assigned_aircraft_count
                    FROM company_aircraft_assignment caa
                    WHERE caa.company_id = c.id AND caa.active
                ) assignments ON true
                WHERE c.active
                  AND (%(company_role)s = 'either'
                       OR (%(company_role)s = 'mro' AND c.is_mro)
                       OR (%(company_role)s = 'operator' AND c.is_operator))
                  AND (%(company_query)s::text IS NULL OR
                       lower(concat_ws(' ', c.name, c.legal_name, c.trading_name,
                           approvals.approval_numbers)) LIKE %(company_like)s OR
                       EXISTS (
                           SELECT 1 FROM company_alias ca
                           WHERE ca.company_id = c.id
                             AND lower(ca.alias) LIKE %(company_like)s
                       ))
                  AND (%(location_query)s::text IS NULL OR EXISTS (
                       SELECT 1 FROM company_site ls
                       WHERE ls.company_id = c.id AND ls.active
                         AND lower(concat_ws(' ', ls.name, ls.airport_ident,
                             ls.address_line_1, ls.address_line_2, ls.locality,
                             ls.region, ls.postal_code, ls.country_code))
                             LIKE %(location_like)s
                  ))
                  AND (%(region_query)s::text IS NULL
                       OR c.geographic_region = ANY(%(region_codes)s::text[]))
                ORDER BY {order_column} DESC, c.name
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()

    def operator_fleet_summary(self) -> dict[str, Any]:
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                WITH current_assignments AS (
                    SELECT
                        c.name AS company,
                        caa.assignment_role,
                        upper(COALESCE(NULLIF(caa.registration, ''),
                            NULLIF(a.registration, ''), caa.aircraft_address,
                            caa.reported_aircraft_address)) AS aircraft_identity
                    FROM company_aircraft_assignment caa
                    JOIN company c ON c.id = caa.company_id
                    LEFT JOIN aircraft a ON a.address = caa.aircraft_address
                    WHERE caa.active AND c.active
                      AND (caa.valid_from IS NULL OR caa.valid_from <= CURRENT_DATE)
                      AND (caa.valid_to IS NULL OR caa.valid_to >= CURRENT_DATE)
                    UNION ALL
                    SELECT
                        registered_operator AS company,
                        'OPERATOR' AS assignment_role,
                        upper(registration) AS aircraft_identity
                    FROM aircraft_registry_resolved_cache
                    WHERE registered_operator IS NOT NULL
                      AND operator_effective_date <= CURRENT_DATE
                      AND (ineffective_date IS NULL OR ineffective_date >= CURRENT_DATE)
                ), conflicts AS (
                    SELECT aircraft_identity
                    FROM current_assignments
                    WHERE aircraft_identity IS NOT NULL
                    GROUP BY aircraft_identity
                    HAVING count(DISTINCT company) > 1
                )
                SELECT
                    count(*) AS current_assignments,
                    count(*) FILTER (WHERE assignment_role = 'OPERATOR')
                        AS operator_assignments,
                    count(DISTINCT company) AS companies_with_assignments,
                    count(DISTINCT aircraft_identity) AS unique_aircraft,
                    (SELECT count(*) FROM conflicts) AS conflicting_aircraft
                FROM current_assignments
                """
            ).fetchone()

    def operator_suggestions(
        self, query: str, *, limit: int = 8
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str):
            raise ValueError("Operator search must be text")
        normalized = " ".join(query.split()).lower()
        if len(normalized) < 2:
            return []
        if len(normalized) > 160:
            raise ValueError("Operator search is capped at 160 characters")
        if isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ValueError("Operator suggestion limits must be between 1 and 20")
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                WITH candidates AS (
                    SELECT c.name, 'COMPANY_DIRECTORY'::text AS source_code, 0 AS priority
                    FROM company c
                    WHERE c.active AND c.is_operator
                      AND (
                          lower(c.name) LIKE %s
                          OR EXISTS (
                              SELECT 1 FROM company_alias alias
                              WHERE alias.company_id = c.id
                                AND lower(alias.alias) LIKE %s
                          )
                      )
                    UNION ALL
                    SELECT registered_operator, source_code, 1
                    FROM aircraft_registry_resolved_cache
                    WHERE registered_operator IS NOT NULL
                      AND operator_effective_date <= CURRENT_DATE
                      AND (ineffective_date IS NULL OR ineffective_date >= CURRENT_DATE)
                      AND lower(registered_operator) LIKE %s
                ), deduplicated AS (
                    SELECT DISTINCT ON (lower(name)) name, source_code
                    FROM candidates
                    ORDER BY lower(name), priority, name
                )
                SELECT name, source_code
                FROM deduplicated
                ORDER BY
                    CASE
                        WHEN lower(name) = %s THEN 0
                        WHEN lower(name) LIKE %s THEN 1
                        ELSE 2
                    END,
                    name
                LIMIT %s
                """,
                (
                    f"%{normalized}%",
                    f"%{normalized}%",
                    f"%{normalized}%",
                    normalized,
                    f"{normalized}%",
                    limit,
                ),
            ).fetchall()

    def set_aircraft_operator(
        self,
        address: str,
        *,
        operator: str,
        registration: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(address, str) or not re.fullmatch(r"[0-9a-fA-F]{6}", address.strip()):
            raise ValueError("A six-character hexadecimal aircraft address is required")
        normalized_address = address.strip().lower()
        if not isinstance(operator, str):
            raise ValueError("Operator must be text")
        normalized_operator = " ".join(operator.split())
        if not normalized_operator:
            raise ValueError("Operator is required")
        if len(normalized_operator) > 200:
            raise ValueError("Operator is capped at 200 characters")
        normalized_registration = None
        if registration is not None:
            if not isinstance(registration, str):
                raise ValueError("Registration must be text")
            normalized_registration = " ".join(registration.split()).upper() or None
            if normalized_registration and len(normalized_registration) > 40:
                raise ValueError("Registration is capped at 40 characters")

        company_key = "manual-" + hashlib.sha256(
            normalized_operator.casefold().encode("utf-8")
        ).hexdigest()[:24]
        external_id = f"operator:{normalized_address}"
        source_code = "ADMIN_OPERATOR_OVERRIDE"
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            connection.execute(
                """
                INSERT INTO company_data_source (code, name, source_kind, notes)
                VALUES (%s, 'Heligent manual operator entry', 'CURATED',
                        'Operator assignments entered in the local admin interface')
                ON CONFLICT (code) DO NOTHING
                """,
                (source_code,),
            )
            company = connection.execute(
                """
                SELECT id FROM company
                WHERE lower(name) = lower(%s) AND active
                ORDER BY is_operator DESC, id
                LIMIT 1
                """,
                (normalized_operator,),
            ).fetchone()
            if company is None:
                company = connection.execute(
                    """
                    INSERT INTO company (company_key, name, is_operator)
                    VALUES (%s, %s, true)
                    ON CONFLICT (company_key) DO UPDATE SET
                        name = EXCLUDED.name,
                        is_operator = true,
                        active = true,
                        updated_at = clock_timestamp()
                    RETURNING id
                    """,
                    (company_key, normalized_operator),
                ).fetchone()
            else:
                connection.execute(
                    """
                    UPDATE company SET is_operator = true, updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (company["id"],),
                )
            aircraft = connection.execute(
                "SELECT registration FROM aircraft WHERE address = %s",
                (normalized_address,),
            ).fetchone()
            effective_registration = normalized_registration or (
                aircraft["registration"] if aircraft else None
            )
            row = connection.execute(
                """
                INSERT INTO company_aircraft_assignment (
                    company_id, aircraft_address, reported_aircraft_address,
                    registration, geographic_region, region_basis,
                    assignment_role, source_code, external_id, valid_from,
                    confidence, active, raw_data
                ) VALUES (
                    %s, %s, %s, %s, heligent_registration_region(%s),
                    CASE WHEN heligent_registration_region(%s) IS NULL
                         THEN NULL ELSE 'REGISTRATION_PREFIX' END,
                    'OPERATOR', %s, %s, CURRENT_DATE, 1.000, true,
                    '{"entry_method":"admin_ui"}'::jsonb
                )
                ON CONFLICT (source_code, external_id) DO UPDATE SET
                    company_id = EXCLUDED.company_id,
                    aircraft_address = EXCLUDED.aircraft_address,
                    reported_aircraft_address = EXCLUDED.reported_aircraft_address,
                    registration = EXCLUDED.registration,
                    geographic_region = EXCLUDED.geographic_region,
                    region_basis = EXCLUDED.region_basis,
                    valid_from = CURRENT_DATE,
                    valid_to = NULL,
                    confidence = 1.000,
                    active = true,
                    updated_at = clock_timestamp()
                RETURNING id
                """,
                (
                    company["id"],
                    normalized_address if aircraft else None,
                    normalized_address,
                    effective_registration,
                    effective_registration,
                    effective_registration,
                    source_code,
                    external_id,
                ),
            ).fetchone()
            connection.commit()
        self.refresh_nl_operator_cache()
        return {
            "assignment_id": row["id"],
            "address": normalized_address,
            "registration": effective_registration,
            "operator": normalized_operator,
            "operator_source_code": source_code,
        }

    def operator_aircraft(
        self,
        *,
        query: str | None = None,
        company_query: str | None = None,
        registration_query: str | None = None,
        region_query: str | None = None,
        assignment_role: str = "OPERATOR",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Operator-aircraft result limits must be between 1 and 100")
        allowed_roles = {
            "OPERATOR", "OWNER", "MANAGER", "AOC_AUTHORIZED", "OTHER", "ANY"
        }
        if assignment_role not in allowed_roles:
            raise ValueError("Unsupported aircraft assignment role")

        def clean(value: str | None) -> str | None:
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError("Operator-aircraft filters must be text")
            normalized = " ".join(value.split()).lower()
            if not normalized:
                return None
            if len(normalized) > 160:
                raise ValueError("Operator-aircraft filters are capped at 160 characters")
            return normalized

        generic = clean(query)
        company = clean(company_query)
        registration = clean(registration_query)
        region_label, region_codes = normalize_world_region_query(region_query)
        params = {
            "query": generic,
            "query_like": f"%{generic}%" if generic else None,
            "company_query": company,
            "company_like": f"%{company}%" if company else None,
            "registration_query": registration,
            "registration_like": f"%{registration}%" if registration else None,
            "region_query": region_label,
            "region_codes": list(region_codes),
            "assignment_role": assignment_role,
            "limit": limit,
        }
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                WITH assignments AS (
                    SELECT
                        c.name AS company, c.legal_name, c.trading_name,
                        c.country_code::text AS country, caa.geographic_region,
                        caa.region_basis,
                        COALESCE(NULLIF(caa.registration, ''),
                                 NULLIF(a.registration, '')) AS registration,
                        COALESCE(caa.aircraft_address,
                                 caa.reported_aircraft_address) AS aircraft_address,
                        caa.assignment_role, caa.confidence, caa.valid_from,
                        caa.valid_to, caa.source_code,
                        caa.external_id AS source_assignment_id,
                        lower(concat_ws(' ', c.name, c.legal_name, c.trading_name,
                            (SELECT string_agg(alias.alias, ' ')
                             FROM company_alias alias WHERE alias.company_id = c.id)))
                            AS company_search
                    FROM company_aircraft_assignment caa
                    JOIN company c ON c.id = caa.company_id
                    LEFT JOIN aircraft a ON a.address = caa.aircraft_address
                    WHERE caa.active AND c.active
                      AND (caa.valid_from IS NULL OR caa.valid_from <= CURRENT_DATE)
                      AND (caa.valid_to IS NULL OR caa.valid_to >= CURRENT_DATE)
                    UNION ALL
                    SELECT
                        registered_operator, NULL, NULL, operator_country,
                        operator_geographic_region,
                        'REGISTRY_OPERATOR_COUNTRY',
                        registration, NULL, 'OPERATOR', 1.000,
                        operator_effective_date, ineffective_date, source_code,
                        concat('registry:', registration),
                        lower(registered_operator)
                    FROM aircraft_registry_resolved_cache
                    WHERE registered_operator IS NOT NULL
                      AND operator_effective_date <= CURRENT_DATE
                      AND (ineffective_date IS NULL OR ineffective_date >= CURRENT_DATE)
                )
                SELECT company, legal_name, trading_name, country, geographic_region,
                    region_basis,
                    registration,
                    aircraft_address, assignment_role, confidence, valid_from,
                    valid_to, source_code, source_assignment_id
                FROM assignments
                WHERE (%(assignment_role)s = 'ANY'
                       OR assignment_role = %(assignment_role)s)
                  AND (%(company_query)s::text IS NULL
                       OR company_search LIKE %(company_like)s)
                  AND (%(region_query)s::text IS NULL
                       OR geographic_region = ANY(%(region_codes)s::text[]))
                  AND (%(registration_query)s::text IS NULL OR
                       lower(concat_ws(' ', registration, aircraft_address))
                           LIKE %(registration_like)s)
                  AND (%(query)s::text IS NULL OR
                       lower(concat_ws(' ', company_search, registration,
                           aircraft_address, assignment_role, source_code))
                           LIKE %(query_like)s)
                ORDER BY company, registration NULLS LAST, assignment_role,
                    source_assignment_id
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()

    def operator_tail_activity(
        self,
        start_date: date,
        end_date: date,
        *,
        company_query: str | None = None,
        region_query: str | None = None,
        assignment_role: str = "OPERATOR",
        type_code: str | None = None,
        helicopters_only: bool = False,
        group_by_operator: bool = False,
        limit: int = 25,
    ) -> dict[str, Any]:
        if end_date < start_date:
            raise ValueError("Analytics to date must be on or after the from date")
        requested_days = (end_date - start_date).days + 1
        if requested_days > MAX_ANALYTICS_DAYS:
            raise ValueError(f"Analytics periods are capped at {MAX_ANALYTICS_DAYS} days")
        if assignment_role not in {"OPERATOR", "OWNER", "MANAGER", "AOC_AUTHORIZED", "OTHER"}:
            raise ValueError("Unsupported aircraft assignment role")
        if not isinstance(group_by_operator, bool):
            raise ValueError("group_by_operator must be true or false")
        normalized_type = resolve_aircraft_type_code(type_code)
        if normalized_type is not None and not re.fullmatch(r"[A-Z0-9-]{1,16}", normalized_type):
            raise ValueError("Aircraft type code is invalid")
        query_params = _company_query_params(
            company_query=company_query, region_query=region_query, limit=limit
        )
        params = {
            **query_params,
            "start_date": start_date,
            "end_date": end_date,
            "assignment_role": assignment_role,
            "type_code": normalized_type,
            "helicopters_only": helicopters_only,
        }
        if group_by_operator:
            result_select = """
                SELECT
                    company,
                    NULL::varchar AS address,
                    NULL::text AS registration,
                    NULL::text AS type_code,
                    NULL::text AS description,
                    CASE WHEN count(DISTINCT aircraft_category) = 1
                         THEN max(aircraft_category) ELSE 'MIXED' END AS category,
                    max(geographic_region) AS geographic_region,
                    assignment_role,
                    max(confidence) AS confidence,
                    string_agg(DISTINCT source_code, ', ' ORDER BY source_code)
                        AS source_code,
                    count(DISTINCT address) AS aircraft_count,
                    count(DISTINCT utc_date) AS active_days,
                    sum(observation_count) AS observations,
                    sum(active_time_seconds) / 3600.0 AS active_hours,
                    sum(airborne_time_seconds) / 3600.0 AS airborne_hours,
                    sum(ground_active_time_seconds) / 3600.0 AS ground_active_hours
                FROM matched_activity
                GROUP BY company, assignment_role
                ORDER BY active_hours DESC, observations DESC, company
                LIMIT %(limit)s
            """
        else:
            result_select = """
                SELECT
                    company,
                    address,
                    max(registration) AS registration,
                    max(type_code) AS type_code,
                    max(type_description) AS description,
                    max(aircraft_category) AS category,
                    max(geographic_region) AS geographic_region,
                    assignment_role,
                    max(confidence) AS confidence,
                    string_agg(DISTINCT source_code, ', ' ORDER BY source_code)
                        AS source_code,
                    1::bigint AS aircraft_count,
                    count(DISTINCT utc_date) AS active_days,
                    sum(observation_count) AS observations,
                    sum(active_time_seconds) / 3600.0 AS active_hours,
                    sum(airborne_time_seconds) / 3600.0 AS airborne_hours,
                    sum(ground_active_time_seconds) / 3600.0 AS ground_active_hours
                FROM matched_activity
                GROUP BY company, address, assignment_role
                ORDER BY active_hours DESC, observations DESC, address
                LIMIT %(limit)s
            """
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            connection.execute(
                f"SET LOCAL statement_timeout = '{ANALYTICS_STATEMENT_TIMEOUT_MS}ms'"
            )
            available_rows = connection.execute(
                """
                SELECT DISTINCT utc_date
                FROM aircraft_day
                WHERE utc_date BETWEEN %(start_date)s AND %(end_date)s
                ORDER BY utc_date
                """,
                params,
            ).fetchall()
            rows = connection.execute(
                f"""
                WITH matched_assignments AS (
                    SELECT DISTINCT
                        c.name AS company,
                        caa.aircraft_address,
                        caa.reported_aircraft_address,
                        upper(NULLIF(caa.registration, '')) AS registration,
                        caa.assignment_role,
                        caa.confidence,
                        caa.source_code,
                        caa.external_id,
                        caa.geographic_region
                    FROM company_aircraft_assignment caa
                    JOIN company c ON c.id = caa.company_id
                    WHERE caa.active AND c.active
                      AND caa.assignment_role = %(assignment_role)s
                      AND (caa.valid_from IS NULL OR caa.valid_from <= CURRENT_DATE)
                      AND (caa.valid_to IS NULL OR caa.valid_to >= CURRENT_DATE)
                      AND (%(company_query)s::text IS NULL OR (
                          lower(concat_ws(' ', c.name, c.legal_name, c.trading_name))
                              LIKE %(company_like)s
                          OR EXISTS (
                              SELECT 1 FROM company_alias alias
                              WHERE alias.company_id = c.id
                                AND lower(alias.alias) LIKE %(company_like)s
                          )
                      ))
                      AND (%(region_query)s::text IS NULL
                           OR caa.geographic_region = ANY(%(region_codes)s::text[]))
                    UNION ALL
                    SELECT DISTINCT
                        registered_operator AS company,
                        NULL::varchar AS aircraft_address,
                        NULL::varchar AS reported_aircraft_address,
                        upper(registration),
                        'OPERATOR', 1.000, source_code,
                        concat('registry:', registration),
                        operator_geographic_region
                    FROM aircraft_registry_resolved_cache
                    WHERE %(assignment_role)s = 'OPERATOR'
                      AND registered_operator IS NOT NULL
                      AND operator_effective_date <= %(end_date)s
                      AND (ineffective_date IS NULL
                           OR ineffective_date >= %(start_date)s)
                      AND (%(company_query)s::text IS NULL
                           OR lower(registered_operator) LIKE %(company_like)s)
                      AND (%(region_query)s::text IS NULL OR
                           operator_geographic_region = ANY(%(region_codes)s::text[]))
                ), candidate_activity AS (
                    SELECT ad.*, COALESCE(ad.resolved_category::text, 'UNKNOWN') AS aircraft_category,
                           ma.company, ma.assignment_role, ma.confidence,
                           ma.source_code, ma.external_id, ma.geographic_region
                    FROM aircraft_day_identity ad
                    JOIN matched_assignments ma ON ma.aircraft_address = ad.address
                    WHERE ad.utc_date BETWEEN %(start_date)s AND %(end_date)s
                      AND (%(type_code)s::text IS NULL OR ad.type_code = %(type_code)s)
                      AND COALESCE(ad.resolved_category::text, 'UNKNOWN') <> 'GROUND_VEHICLE'
                      AND (%(helicopters_only)s::boolean = false
                           OR ad.resolved_category = 'ROTORCRAFT')
                    UNION ALL
                    SELECT ad.*, COALESCE(ad.resolved_category::text, 'UNKNOWN') AS aircraft_category,
                           ma.company, ma.assignment_role, ma.confidence,
                           ma.source_code, ma.external_id, ma.geographic_region
                    FROM aircraft_day_identity ad
                    JOIN matched_assignments ma ON ma.reported_aircraft_address = ad.address
                    WHERE ad.utc_date BETWEEN %(start_date)s AND %(end_date)s
                      AND (%(type_code)s::text IS NULL OR ad.type_code = %(type_code)s)
                      AND COALESCE(ad.resolved_category::text, 'UNKNOWN') <> 'GROUND_VEHICLE'
                      AND (%(helicopters_only)s::boolean = false
                           OR ad.resolved_category = 'ROTORCRAFT')
                    UNION ALL
                    SELECT ad.*, COALESCE(ad.resolved_category::text, 'UNKNOWN') AS aircraft_category,
                           ma.company, ma.assignment_role, ma.confidence,
                           ma.source_code, ma.external_id, ma.geographic_region
                    FROM aircraft_day_identity ad
                    JOIN matched_assignments ma
                      ON regexp_replace(ma.registration, '[^A-Z0-9]', '', 'g') =
                         regexp_replace(upper(NULLIF(ad.registration, '')),
                                        '[^A-Z0-9]', '', 'g')
                    WHERE ad.utc_date BETWEEN %(start_date)s AND %(end_date)s
                      AND (%(type_code)s::text IS NULL OR ad.type_code = %(type_code)s)
                      AND COALESCE(ad.resolved_category::text, 'UNKNOWN') <> 'GROUND_VEHICLE'
                      AND (%(helicopters_only)s::boolean = false
                           OR ad.resolved_category = 'ROTORCRAFT')
                ), matched_activity AS (
                    SELECT DISTINCT ON (dataset_day_id, address, company) *
                    FROM candidate_activity
                    ORDER BY dataset_day_id, address, company,
                             confidence DESC NULLS LAST, external_id
                )
                {result_select}
                """,
                params,
            ).fetchall()

        available_dates = [row["utc_date"] for row in available_rows]
        available_set = set(available_dates)
        requested_dates = [start_date + timedelta(days=offset) for offset in range(requested_days)]
        missing_dates = [value for value in requested_dates if value not in available_set]
        available_days = len(available_dates)
        if available_days == 0:
            message = f"None of the {requested_days} requested UTC days are available locally yet."
        elif missing_dates:
            message = (
                f"Only {available_days} of {requested_days} requested UTC days are available "
                "locally. Results below are partial."
            )
        else:
            message = f"All {requested_days} requested UTC days are available locally."
        return {
            "coverage": {
                "requested_days": requested_days,
                "available_days": available_days,
                "complete": not missing_dates,
                "available_dates": available_dates,
                "missing_dates": missing_dates,
                "message": message,
            },
            "operator_tail_activity": rows,
        }

    def hub_ranking(
        self,
        start_date: date,
        end_date: date,
        *,
        metric: str = "unique_aircraft",
        type_code: str | None = None,
        helicopters_only: bool = False,
        region_query: str | None = None,
        location_query: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        if end_date < start_date:
            raise ValueError("Analytics to date must be on or after the from date")
        requested_days = (end_date - start_date).days + 1
        if requested_days > MAX_ANALYTICS_DAYS:
            raise ValueError(f"Analytics periods are capped at {MAX_ANALYTICS_DAYS} days")
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Hub result limits must be between 1 and 100")
        order_columns = {
            "unique_aircraft": "unique_aircraft",
            "primary_aircraft": "primary_aircraft",
            "movement_candidates": "movement_candidates",
            "arrival_candidates": "arrival_candidates",
            "departure_candidates": "departure_candidates",
            "ground_active_hours": "ground_active_hours",
            "ground_observations": "ground_observations",
            "primary_tail_active_hours": "primary_tail_active_hours",
            "primary_tail_airborne_hours": "primary_tail_airborne_hours",
        }
        order_column = order_columns.get(metric)
        if order_column is None:
            raise ValueError("Unsupported hub-ranking metric")
        normalized_type = resolve_aircraft_type_code(type_code)
        region_label, region_codes = normalize_world_region_query(region_query)
        location_label, country_code = normalize_activity_location_query(location_query)
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "type_code": normalized_type,
            "helicopters_only": helicopters_only,
            "region_query": region_label,
            "region_codes": list(region_codes),
            "location_query": location_label,
            "location_like": f"%{location_label.lower()}%" if location_label else None,
            "country_code": country_code,
            "limit": limit,
        }
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            available_rows = connection.execute(
                """
                SELECT DISTINCT utc_date
                FROM aircraft_day
                WHERE utc_date BETWEEN %(start_date)s AND %(end_date)s
                ORDER BY utc_date
                """,
                params,
            ).fetchall()
            rows = connection.execute(
                FILTERED_CTE
                + f"""
                SELECT
                    aad.airport_ident,
                    a.iata_code,
                    a.name AS airport_name,
                    a.iso_country,
                    count(DISTINCT f.address) AS unique_aircraft,
                    count(DISTINCT f.type_code) FILTER (WHERE f.type_code IS NOT NULL)
                        AS known_type_codes,
                    count(DISTINCT f.address) FILTER (WHERE aad.is_primary_airport)
                        AS primary_aircraft,
                    sum(aad.ground_observation_count) AS ground_observations,
                    sum(aad.ground_active_time_seconds) / 3600.0 AS ground_active_hours,
                    sum(aad.arrival_count) AS arrival_candidates,
                    sum(aad.departure_count) AS departure_candidates,
                    sum(aad.arrival_count + aad.departure_count) AS movement_candidates,
                    count(DISTINCT f.address) FILTER (WHERE aad.inferred_endpoint_count > 0)
                        AS endpoint_linked_aircraft,
                    count(DISTINCT f.address) FILTER (
                        WHERE aad.arrival_count + aad.departure_count > 0
                    ) AS movement_linked_aircraft,
                    sum(f.active_time_seconds) FILTER (WHERE aad.is_primary_airport) / 3600.0
                        AS primary_tail_active_hours,
                    sum(f.airborne_time_seconds) FILTER (WHERE aad.is_primary_airport) / 3600.0
                        AS primary_tail_airborne_hours
                FROM filtered f
                JOIN aircraft_airport_day aad
                  ON aad.dataset_day_id = f.dataset_day_id
                 AND aad.address = f.address
                JOIN airport a ON a.ident = aad.airport_ident
                WHERE (%(region_query)s::text IS NULL
                       OR heligent_world_region(a.iso_country) =
                          ANY(%(region_codes)s::text[]))
                  AND (%(location_query)s::text IS NULL
                       OR a.iso_country = %(country_code)s
                       OR lower(concat_ws(' ', a.ident, a.iata_code, a.name,
                           a.municipality, a.iso_region, a.iso_country))
                          LIKE %(location_like)s)
                GROUP BY aad.airport_ident, a.iata_code, a.name, a.iso_country
                ORDER BY {order_column} DESC NULLS LAST, aad.airport_ident
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()

        available_dates = [row["utc_date"] for row in available_rows]
        available_set = set(available_dates)
        requested_dates = [
            start_date + timedelta(days=offset) for offset in range(requested_days)
        ]
        missing_dates = [value for value in requested_dates if value not in available_set]
        if not available_dates:
            message = f"None of the {requested_days} requested UTC days are available locally yet."
        elif missing_dates:
            message = (
                f"Only {len(available_dates)} of {requested_days} requested UTC days are "
                "available locally. Results below are partial."
            )
        else:
            message = f"All {requested_days} requested UTC days are available locally."
        return {
            "coverage": {
                "requested_days": requested_days,
                "available_days": len(available_dates),
                "complete": not missing_dates,
                "available_dates": available_dates,
                "missing_dates": missing_dates,
                "message": message,
            },
            "hubs": rows,
        }

    def tail_ranking(
        self,
        start_date: date,
        end_date: date,
        *,
        metric: str = "active_hours",
        type_code: str | None = None,
        helicopters_only: bool = False,
        region_query: str | None = None,
        location_query: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        if end_date < start_date:
            raise ValueError("Analytics to date must be on or after the from date")
        requested_days = (end_date - start_date).days + 1
        if requested_days > MAX_ANALYTICS_DAYS:
            raise ValueError(f"Analytics periods are capped at {MAX_ANALYTICS_DAYS} days")
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Tail result limits must be between 1 and 100")
        order_columns = {
            "active_hours": "active_hours",
            "airborne_hours": "airborne_hours",
            "ground_active_hours": "ground_active_hours",
            "observations": "observations",
            "active_days": "active_days",
        }
        order_column = order_columns.get(metric)
        if order_column is None:
            raise ValueError("Unsupported tail-ranking metric")
        normalized_type = resolve_aircraft_type_code(type_code)
        region_label, region_codes = normalize_world_region_query(region_query)
        location_label, country_code = normalize_activity_location_query(location_query)
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "type_code": normalized_type,
            "helicopters_only": helicopters_only,
            "region_query": region_label,
            "region_codes": list(region_codes),
            "location_query": location_label,
            "location_like": f"%{location_label.lower()}%" if location_label else None,
            "country_code": country_code,
            "limit": limit,
        }
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            available_rows = connection.execute(
                """
                SELECT DISTINCT utc_date
                FROM aircraft_day
                WHERE utc_date BETWEEN %(start_date)s AND %(end_date)s
                ORDER BY utc_date
                """,
                params,
            ).fetchall()
            rows = connection.execute(
                FILTERED_CTE
                + f"""
                , region_linked AS (
                    SELECT f.*
                    FROM filtered f
                    WHERE (%(region_query)s::text IS NULL
                           AND %(location_query)s::text IS NULL)
                       OR EXISTS (
                            SELECT 1
                            FROM aircraft_airport_day regional_presence
                            JOIN airport regional_airport
                              ON regional_airport.ident = regional_presence.airport_ident
                            WHERE regional_presence.dataset_day_id = f.dataset_day_id
                              AND regional_presence.address = f.address
                              AND (
                                  %(region_query)s::text IS NULL
                                  OR heligent_world_region(
                                      regional_airport.iso_country
                                  ) = ANY(%(region_codes)s::text[])
                              )
                              AND (
                                  %(location_query)s::text IS NULL
                                  OR regional_airport.iso_country = %(country_code)s
                                  OR lower(concat_ws(' ', regional_airport.ident,
                                      regional_airport.iata_code, regional_airport.name,
                                      regional_airport.municipality,
                                      regional_airport.iso_region,
                                      regional_airport.iso_country))
                                     LIKE %(location_like)s
                              )
                       )
                ), tail_metrics AS (
                    SELECT
                        f.address,
                        max(f.registration) AS registration,
                        max(f.type_code) AS type_code,
                        max(f.type_description) AS description,
                        max(f.aircraft_category) AS category,
                        count(DISTINCT f.utc_date) AS active_days,
                        sum(f.observation_count) AS observations,
                        sum(f.active_time_seconds) / 3600.0 AS active_hours,
                        sum(f.airborne_time_seconds) / 3600.0 AS airborne_hours,
                        sum(f.ground_active_time_seconds) / 3600.0 AS ground_active_hours
                    FROM region_linked f
                    GROUP BY f.address
                    ORDER BY {order_column} DESC NULLS LAST, f.address
                    LIMIT %(limit)s
                )
                SELECT
                    tm.*,
                    COALESCE(%(region_query)s::text, %(location_query)s::text)
                        AS activity_area,
                    regional_airport.airport_ident AS primary_airport,
                    regional_airport.airport_label AS primary_airport_label,
                        operator_claim.operator AS resolved_operator,
                        operator_claim.operator_source_code
                FROM tail_metrics tm
                LEFT JOIN LATERAL (
                    SELECT
                        aad.airport_ident,
                        COALESCE(a.iata_code, aad.airport_ident) AS airport_label
                    FROM aircraft_airport_day aad
                    JOIN airport a ON a.ident = aad.airport_ident
                    WHERE aad.address = tm.address
                      AND aad.utc_date BETWEEN %(start_date)s AND %(end_date)s
                      AND (%(region_query)s::text IS NULL
                           OR heligent_world_region(a.iso_country) =
                              ANY(%(region_codes)s::text[]))
                      AND (%(location_query)s::text IS NULL
                           OR a.iso_country = %(country_code)s
                           OR lower(concat_ws(' ', a.ident, a.iata_code, a.name,
                               a.municipality, a.iso_region, a.iso_country))
                              LIKE %(location_like)s)
                    GROUP BY aad.airport_ident, a.iata_code
                    ORDER BY sum(aad.ground_time_seconds) DESC, aad.airport_ident
                    LIMIT 1
                ) regional_airport ON true
                LEFT JOIN LATERAL (
                    SELECT claim.operator, claim.operator_source_code
                    FROM current_aircraft_operator_claim claim
                    WHERE claim.aircraft_address = tm.address
                       OR claim.reported_aircraft_address = tm.address
                       OR (
                           claim.registration_key IS NOT NULL
                           AND claim.registration_key = regexp_replace(
                               upper(tm.registration), '[^A-Z0-9]', '', 'g'
                           )
                       )
                    ORDER BY
                        CASE
                            WHEN claim.aircraft_address = tm.address THEN 0
                            WHEN claim.reported_aircraft_address = tm.address THEN 1
                            ELSE 2
                        END,
                        claim.confidence DESC NULLS LAST,
                        claim.operator_source_code,
                        claim.operator
                    LIMIT 1
                ) operator_claim ON true
                ORDER BY {order_column} DESC NULLS LAST, tm.address
                """,
                params,
            ).fetchall()

        available_dates = [row["utc_date"] for row in available_rows]
        available_set = set(available_dates)
        requested_dates = [
            start_date + timedelta(days=offset) for offset in range(requested_days)
        ]
        missing_dates = [value for value in requested_dates if value not in available_set]
        if not available_dates:
            message = f"None of the {requested_days} requested UTC days are available locally yet."
        elif missing_dates:
            message = (
                f"Only {len(available_dates)} of {requested_days} requested UTC days are "
                "available locally. Results below are partial."
            )
        else:
            message = f"All {requested_days} requested UTC days are available locally."
        return {
            "coverage": {
                "requested_days": requested_days,
                "available_days": len(available_dates),
                "complete": not missing_dates,
                "available_dates": available_dates,
                "missing_dates": missing_dates,
                "message": message,
            },
            "tails": rows,
        }

    def company_approvals(
        self,
        *,
        company_query: str | None = None,
        approval_query: str | None = None,
        location_query: str | None = None,
        region_query: str | None = None,
        approval_status: str = "ANY",
        metric: str = "capability_count",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        if metric != "capability_count":
            raise ValueError("Unsupported company-approval ranking metric")
        allowed_statuses = {"VALID", "PENDING", "SUSPENDED", "REVOKED", "EXPIRED", "UNKNOWN", "ANY"}
        if approval_status not in allowed_statuses:
            raise ValueError("Unsupported regulatory approval status")
        params = _company_query_params(
            company_query=company_query,
            location_query=location_query,
            region_query=region_query,
            approval_query=approval_query,
            limit=limit,
        )
        params["approval_status"] = approval_status
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                SELECT
                    c.name AS company,
                    c.geographic_region,
                    ra.authority_code,
                    ra.approval_type,
                    ra.approval_number,
                    ra.status,
                    ra.valid_from,
                    ra.valid_to,
                    ra.last_verified_at,
                    string_agg(DISTINCT s.name, '; ' ORDER BY s.name) AS sites,
                    string_agg(DISTINCT concat_ws(', ', s.locality, s.region,
                        s.postal_code, s.country_code), '; ') AS locations,
                    count(DISTINCT ac.id) FILTER (WHERE ac.active) AS capability_count
                FROM regulatory_approval ra
                JOIN company c ON c.id = ra.company_id
                LEFT JOIN company_site_approval csa
                  ON csa.regulatory_approval_id = ra.id AND csa.active
                LEFT JOIN company_site s ON s.id = csa.company_site_id AND s.active
                LEFT JOIN approval_capability ac ON ac.regulatory_approval_id = ra.id
                WHERE c.active
                  AND (%(approval_status)s = 'ANY' OR ra.status = %(approval_status)s)
                  AND (%(company_query)s::text IS NULL OR
                       lower(concat_ws(' ', c.name, c.legal_name, c.trading_name))
                           LIKE %(company_like)s)
                  AND (%(approval_query)s::text IS NULL OR
                       lower(concat_ws(' ', ra.authority_code, ra.approval_type,
                           ra.approval_number)) LIKE %(approval_like)s)
                  AND (%(location_query)s::text IS NULL OR EXISTS (
                       SELECT 1
                       FROM company_site_approval lcsa
                       JOIN company_site ls ON ls.id = lcsa.company_site_id
                       WHERE lcsa.regulatory_approval_id = ra.id
                         AND lcsa.active AND ls.active
                         AND lower(concat_ws(' ', ls.name, ls.airport_ident,
                             ls.address_line_1, ls.address_line_2, ls.locality,
                             ls.region, ls.postal_code, ls.country_code))
                             LIKE %(location_like)s
                  ))
                  AND (%(region_query)s::text IS NULL
                       OR c.geographic_region = ANY(%(region_codes)s::text[]))
                GROUP BY c.name, c.geographic_region, ra.id
                ORDER BY capability_count DESC, c.name, ra.approval_number
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()

    def company_capabilities(
        self,
        *,
        company_query: str | None = None,
        capability_query: str | None = None,
        location_query: str | None = None,
        region_query: str | None = None,
        capability_kind: str = "ANY",
        approval_status: str = "VALID",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        allowed_kinds = {"AIRCRAFT", "ENGINE", "COMPONENT", "SPECIALIST", "SERVICE", "OTHER", "ANY"}
        if capability_kind not in allowed_kinds:
            raise ValueError("Unsupported approval capability kind")
        allowed_statuses = {"VALID", "PENDING", "SUSPENDED", "REVOKED", "EXPIRED", "UNKNOWN", "ANY"}
        if approval_status not in allowed_statuses:
            raise ValueError("Unsupported regulatory approval status")
        if not any((company_query, capability_query, location_query, region_query)):
            raise ValueError("Capability searches require a company, capability, or location")
        params = _company_query_params(
            company_query=company_query,
            capability_query=capability_query,
            location_query=location_query,
            region_query=region_query,
            limit=limit,
        )
        params["capability_kind"] = capability_kind
        params["approval_status"] = approval_status
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                SELECT
                    c.name AS company,
                    c.geographic_region,
                    ra.approval_number,
                    ra.status AS approval_status,
                    ac.capability_kind,
                    ac.rating_class,
                    ac.rating_code,
                    ac.manufacturer,
                    ac.model,
                    ac.aircraft_type_code,
                    ac.limitation AS capability,
                    sites.site_names AS sites,
                    sites.locations,
                    1 AS matching_capabilities
                FROM approval_capability ac
                JOIN regulatory_approval ra ON ra.id = ac.regulatory_approval_id
                JOIN company c ON c.id = ra.company_id
                LEFT JOIN LATERAL (
                    SELECT
                        string_agg(DISTINCT s.name, '; ' ORDER BY s.name) AS site_names,
                        string_agg(DISTINCT concat_ws(', ', s.locality, s.region,
                            s.postal_code, s.country_code), '; ') AS locations
                    FROM company_site s
                    WHERE s.active AND (
                        s.id = ac.company_site_id OR EXISTS (
                            SELECT 1 FROM company_site_approval csa
                            WHERE csa.company_site_id = s.id
                              AND csa.regulatory_approval_id = ra.id AND csa.active
                        )
                    )
                ) sites ON true
                WHERE c.active AND ac.active
                  AND (%(approval_status)s = 'ANY' OR ra.status = %(approval_status)s)
                  AND (%(capability_kind)s = 'ANY'
                       OR ac.capability_kind = %(capability_kind)s)
                  AND (%(company_query)s::text IS NULL OR
                       lower(concat_ws(' ', c.name, c.legal_name, c.trading_name,
                           ra.approval_number)) LIKE %(company_like)s)
                  AND (%(capability_query)s::text IS NULL OR
                       regexp_replace(lower(concat_ws(' ', ac.capability_kind,
                           ac.rating_class, ac.rating_code, ac.manufacturer, ac.model,
                           ac.aircraft_type_code, ac.limitation)), '[^a-z0-9]+', '', 'g')
                           LIKE %(capability_compact_like)s)
                  AND (%(location_query)s::text IS NULL OR
                       lower(concat_ws(' ', sites.site_names, sites.locations))
                           LIKE %(location_like)s)
                  AND (%(region_query)s::text IS NULL
                       OR c.geographic_region = ANY(%(region_codes)s::text[]))
                ORDER BY c.name, ra.approval_number, ac.capability_kind,
                    ac.rating_code NULLS LAST, ac.capability_key
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()

    def airport_daily_activity(
        self,
        start_date: date,
        end_date: date,
        *,
        airport_code: str,
        type_code: str | None = None,
        helicopters_only: bool = False,
    ) -> dict[str, Any]:
        if end_date < start_date:
            raise ValueError("Analytics to date must be on or after the from date")
        requested_days = (end_date - start_date).days + 1
        if requested_days > MAX_ANALYTICS_DAYS:
            raise ValueError(f"Analytics periods are capped at {MAX_ANALYTICS_DAYS} days")
        normalized_airport = airport_code.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,11}", normalized_airport):
            raise ValueError("Airport code must be a valid stored airport identifier")
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "type_code": resolve_aircraft_type_code(type_code),
            "helicopters_only": helicopters_only,
            "airport_code": normalized_airport,
        }
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            available_rows = connection.execute(
                """
                SELECT DISTINCT utc_date
                FROM aircraft_day
                WHERE utc_date BETWEEN %(start_date)s AND %(end_date)s
                ORDER BY utc_date
                """,
                params,
            ).fetchall()
            rows = connection.execute(
                FILTERED_CTE
                + """
                SELECT
                    f.utc_date,
                    max(COALESCE(a.iata_code, a.ident)) AS airport,
                    count(DISTINCT f.address) AS unique_aircraft,
                    sum(f.observation_count) AS observations,
                    sum(f.active_time_seconds) / 3600.0 AS active_hours,
                    sum(f.airborne_time_seconds) / 3600.0 AS airborne_hours,
                    sum(aad.ground_active_time_seconds) / 3600.0 AS ground_active_hours,
                    count(DISTINCT f.address) AS airport_linked_aircraft
                FROM filtered f
                JOIN aircraft_airport_day aad
                  ON aad.dataset_day_id = f.dataset_day_id
                 AND aad.address = f.address
                JOIN airport a ON a.ident = aad.airport_ident
                WHERE upper(a.ident) = %(airport_code)s
                   OR upper(COALESCE(a.iata_code, '')) = %(airport_code)s
                GROUP BY f.utc_date
                ORDER BY f.utc_date
                """,
                params,
            ).fetchall()

        available_dates = [row["utc_date"] for row in available_rows]
        rows_by_date = {row["utc_date"]: row for row in rows}
        rows = [
            rows_by_date.get(
                available_date,
                {
                    "utc_date": available_date,
                    "airport": normalized_airport,
                    "unique_aircraft": 0,
                    "observations": 0,
                    "active_hours": 0,
                    "airborne_hours": 0,
                    "ground_active_hours": 0,
                    "airport_linked_aircraft": 0,
                },
            )
            for available_date in available_dates
        ]
        available_set = set(available_dates)
        requested_dates = [
            start_date + timedelta(days=offset) for offset in range(requested_days)
        ]
        missing_dates = [value for value in requested_dates if value not in available_set]
        if not available_dates:
            message = f"None of the {requested_days} requested UTC days are available locally yet."
        elif missing_dates:
            message = (
                f"Only {len(available_dates)} of {requested_days} requested UTC days are "
                "available locally. Results below are partial."
            )
        else:
            message = f"All {requested_days} requested UTC days are available locally."
        return {
            "coverage": {
                "requested_days": requested_days,
                "available_days": len(available_dates),
                "complete": not missing_dates,
                "available_dates": available_dates,
                "missing_dates": missing_dates,
                "message": message,
            },
            "daily_activity": rows,
        }

    def airport_aircraft(
        self,
        start_date: date,
        end_date: date,
        *,
        airport_code: str,
        type_code: str | None = None,
        helicopters_only: bool = False,
        metric: str = "airport_ground_observations",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        normalized_airport = airport_code.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,11}", normalized_airport):
            raise ValueError("Airport code must be a valid stored airport identifier")
        order_columns = {
            "airport_movement_candidates": "airport_movement_candidates",
            "airport_arrival_candidates": "airport_arrival_candidates",
            "airport_departure_candidates": "airport_departure_candidates",
            "airport_ground_observations": "airport_ground_observations",
            "airport_ground_active_hours": "airport_ground_active_hours",
            "active_hours": "active_hours",
            "airborne_hours": "airborne_hours",
            "observations": "observations",
        }
        order_column = order_columns.get(metric)
        if order_column is None:
            raise ValueError("Unsupported airport-aircraft ranking metric")
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Airport-aircraft result limits must be between 1 and 100")

        normalized_type = resolve_aircraft_type_code(type_code)
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "type_code": normalized_type,
            "helicopters_only": helicopters_only,
            "airport_code": normalized_airport,
            "limit": limit,
        }
        movement_having = {
            "airport_movement_candidates": (
                "HAVING sum(aad.arrival_count + aad.departure_count) > 0"
            ),
            "airport_arrival_candidates": "HAVING sum(aad.arrival_count) > 0",
            "airport_departure_candidates": "HAVING sum(aad.departure_count) > 0",
        }.get(metric, "")
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            rows = connection.execute(
                FILTERED_CTE
                + f"""
                , tail_metrics AS (
                SELECT
                    f.address,
                    max(f.registration) AS registration,
                    max(f.type_code) AS type_code,
                    max(f.type_description) AS description,
                    max(f.aircraft_category) AS category,
                    max(f.registry_operator) AS registry_operator,
                    max(f.identity_source_code) AS registry_operator_source_code,
                    count(DISTINCT f.utc_date) AS active_days,
                    sum(f.observation_count) AS observations,
                    sum(f.active_time_seconds) / 3600.0 AS active_hours,
                    sum(f.airborne_time_seconds) / 3600.0 AS airborne_hours,
                    sum(f.ground_active_time_seconds) / 3600.0 AS ground_active_hours,
                    max(a.ident) AS airport_ident,
                    max(COALESCE(a.iata_code, a.ident)) AS airport,
                    max(a.name) AS airport_name,
                    sum(aad.ground_observation_count) AS airport_ground_observations,
                    sum(aad.ground_active_time_seconds) / 3600.0
                        AS airport_ground_active_hours,
                    sum(aad.arrival_count) AS airport_arrival_candidates,
                    sum(aad.departure_count) AS airport_departure_candidates,
                    sum(aad.arrival_count + aad.departure_count)
                        AS airport_movement_candidates,
                    sum(aad.inferred_endpoint_count) AS inferred_endpoint_count,
                    CASE
                        WHEN bool_or(aad.ground_observation_count > 0)
                         AND bool_or(aad.inferred_endpoint_count > 0)
                            THEN 'Ground + endpoint'
                        WHEN bool_or(aad.ground_observation_count > 0) THEN 'Ground'
                        ELSE 'Inferred endpoint'
                    END AS activity_evidence,
                    count(DISTINCT f.utc_date) FILTER (WHERE aad.is_primary_airport)
                        AS primary_airport_days
                FROM filtered f
                JOIN aircraft_airport_day aad
                  ON aad.dataset_day_id = f.dataset_day_id
                 AND aad.address = f.address
                JOIN airport a ON a.ident = aad.airport_ident
                WHERE upper(a.ident) = %(airport_code)s
                   OR upper(COALESCE(a.iata_code, '')) = %(airport_code)s
                GROUP BY f.address
                {movement_having}
                ORDER BY {order_column} DESC, observations DESC, f.address
                LIMIT %(limit)s
                )
                SELECT
                    tm.*,
                    operator_claim.operator,
                    operator_claim.operator_source_code
                FROM tail_metrics tm
                LEFT JOIN LATERAL (
                    SELECT claim.operator, claim.operator_source_code
                    FROM current_aircraft_operator_claim claim
                    WHERE claim.aircraft_address = tm.address
                       OR claim.reported_aircraft_address = tm.address
                       OR (
                           claim.registration_key IS NOT NULL
                           AND claim.registration_key = regexp_replace(
                               upper(tm.registration), '[^A-Z0-9]', '', 'g'
                           )
                       )
                    ORDER BY
                        CASE
                            WHEN claim.aircraft_address = tm.address THEN 0
                            WHEN claim.reported_aircraft_address = tm.address THEN 1
                            ELSE 2
                        END,
                        claim.confidence DESC NULLS LAST,
                        claim.operator_source_code,
                        claim.operator
                    LIMIT 1
                ) operator_claim ON true
                ORDER BY {order_column} DESC, observations DESC, tm.address
                """,
                params,
            ).fetchall()
        return rows

    def airport_types(
        self,
        start_date: date,
        end_date: date,
        *,
        airport_code: str,
        type_code: str | None = None,
        helicopters_only: bool = False,
        metric: str = "unique_aircraft",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        normalized_airport = airport_code.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,11}", normalized_airport):
            raise ValueError("Airport code must be a valid stored airport identifier")
        order_columns = {
            "unique_aircraft": "unique_aircraft",
            "movement_candidates": "movement_candidates",
            "arrival_candidates": "arrival_candidates",
            "departure_candidates": "departure_candidates",
            "ground_observations": "ground_observations",
            "ground_active_hours": "ground_active_hours",
            "active_hours": "active_hours",
            "airborne_hours": "airborne_hours",
            "observations": "observations",
        }
        order_column = order_columns.get(metric)
        if order_column is None:
            raise ValueError("Unsupported airport-type ranking metric")
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Airport-type result limits must be between 1 and 100")

        params = {
            "start_date": start_date,
            "end_date": end_date,
            "type_code": resolve_aircraft_type_code(type_code),
            "helicopters_only": helicopters_only,
            "airport_code": normalized_airport,
            "limit": limit,
        }
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                FILTERED_CTE
                + f"""
                SELECT
                    COALESCE(f.type_code, 'UNKNOWN') AS type_code,
                    max(f.type_description) AS description,
                    count(DISTINCT f.address) AS unique_aircraft,
                    sum(f.observation_count) AS observations,
                    sum(f.active_time_seconds) / 3600.0 AS active_hours,
                    sum(f.airborne_time_seconds) / 3600.0 AS airborne_hours,
                    sum(aad.ground_observation_count) AS ground_observations,
                    sum(aad.ground_active_time_seconds) / 3600.0 AS ground_active_hours,
                    sum(aad.arrival_count) AS arrival_candidates,
                    sum(aad.departure_count) AS departure_candidates,
                    sum(aad.arrival_count + aad.departure_count) AS movement_candidates,
                    max(COALESCE(a.iata_code, a.ident)) AS airport,
                    max(a.name) AS airport_name
                FROM filtered f
                JOIN aircraft_airport_day aad
                  ON aad.dataset_day_id = f.dataset_day_id
                 AND aad.address = f.address
                JOIN airport a ON a.ident = aad.airport_ident
                WHERE upper(a.ident) = %(airport_code)s
                   OR upper(COALESCE(a.iata_code, '')) = %(airport_code)s
                GROUP BY COALESCE(f.type_code, 'UNKNOWN')
                ORDER BY {order_column} DESC, type_code
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()

    def snapshot(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        *,
        type_code: str | None = None,
        helicopters_only: bool = False,
        region_query: str | None = None,
    ) -> dict[str, Any]:
        latest = self._latest_available_date()
        if start_date is None and end_date is None:
            start_date = latest
            end_date = latest
        elif start_date is None or end_date is None:
            raise ValueError("Analytics requires both from and to dates")

        if start_date is None or end_date is None:
            return self._empty_snapshot(type_code=type_code, helicopters_only=helicopters_only)
        if end_date < start_date:
            raise ValueError("Analytics to date must be on or after the from date")
        requested_days = (end_date - start_date).days + 1
        if requested_days > MAX_ANALYTICS_DAYS:
            raise ValueError(f"Analytics periods are capped at {MAX_ANALYTICS_DAYS} days")

        normalized_type = resolve_aircraft_type_code(type_code)
        if normalized_type is not None and len(normalized_type) > 16:
            raise ValueError("Aircraft type code is too long")
        region_label, region_codes = normalize_world_region_query(region_query)

        params = {
            "start_date": start_date,
            "end_date": end_date,
            "type_code": normalized_type,
            "helicopters_only": helicopters_only,
            "region_query": region_label,
            "region_codes": list(region_codes),
        }
        heli_params = {"start_date": start_date, "end_date": end_date}

        with self.store.connect() as connection:
            connection.row_factory = dict_row
            connection.execute(
                f"SET LOCAL statement_timeout = '{ANALYTICS_STATEMENT_TIMEOUT_MS}ms'"
            )
            available_rows = connection.execute(
                """
                SELECT DISTINCT utc_date
                FROM nl_aircraft_activity_cache ad
                WHERE utc_date BETWEEN %(start_date)s AND %(end_date)s
                ORDER BY utc_date
                """,
                params,
            ).fetchall()
            totals = connection.execute(
                SNAPSHOT_FILTERED_CTE
                + """
                SELECT
                    count(DISTINCT address) AS unique_aircraft,
                    count(*) AS aircraft_days,
                    COALESCE(sum(observation_count), 0) AS observations,
                    COALESCE(sum(active_time_seconds), 0) / 3600.0 AS active_hours,
                    COALESCE(sum(airborne_time_seconds), 0) / 3600.0 AS airborne_hours,
                    COALESCE(sum(ground_active_time_seconds), 0) / 3600.0 AS ground_active_hours,
                    count(DISTINCT type_code) FILTER (WHERE type_code IS NOT NULL)
                        AS known_type_codes,
                    count(DISTINCT address) FILTER (WHERE distinct_airports > 0)
                        AS airport_linked_aircraft
                FROM filtered
                """,
                params,
            ).fetchone()
            daily = connection.execute(
                SNAPSHOT_FILTERED_CTE
                + """
                SELECT
                    utc_date,
                    count(DISTINCT address) AS unique_aircraft,
                    sum(observation_count) AS observations,
                    sum(active_time_seconds) / 3600.0 AS active_hours,
                    sum(airborne_time_seconds) / 3600.0 AS airborne_hours,
                    sum(ground_active_time_seconds) / 3600.0 AS ground_active_hours,
                    count(DISTINCT address) FILTER (WHERE distinct_airports > 0)
                        AS airport_linked_aircraft
                FROM filtered
                GROUP BY utc_date
                ORDER BY utc_date
                """,
                params,
            ).fetchall()
            types = connection.execute(
                SNAPSHOT_FILTERED_CTE
                + """
                SELECT
                    COALESCE(type_code, 'UNKNOWN') AS type_code,
                    max(type_description) AS description,
                    aircraft_category AS category,
                    count(DISTINCT address) AS unique_aircraft,
                    count(*) AS aircraft_days,
                    sum(observation_count) AS observations,
                    sum(active_time_seconds) / 3600.0 AS active_hours,
                    sum(airborne_time_seconds) / 3600.0 AS airborne_hours,
                    sum(ground_active_time_seconds) / 3600.0 AS ground_active_hours
                FROM filtered
                GROUP BY COALESCE(type_code, 'UNKNOWN'), aircraft_category
                ORDER BY unique_aircraft DESC, active_hours DESC
                LIMIT 30
                """,
                params,
            ).fetchall()
            type_options = connection.execute(
                """
                SELECT
                    COALESCE(ad.type_code, 'UNKNOWN') AS type_code,
                    max(ad.type_description) AS description,
                    count(DISTINCT ad.address) AS unique_aircraft
                FROM nl_aircraft_activity_cache ad
                WHERE ad.utc_date BETWEEN %(start_date)s AND %(end_date)s
                GROUP BY COALESCE(ad.type_code, 'UNKNOWN')
                ORDER BY unique_aircraft DESC, type_code
                """,
                params,
            ).fetchall()
            tails = connection.execute(
                SNAPSHOT_FILTERED_CTE
                + """
                , tail_metrics AS (
                SELECT
                    f.address,
                    max(f.registration) AS registration,
                    max(f.type_code) AS type_code,
                    max(f.type_description) AS description,
                    max(f.aircraft_category) AS category,
                    count(DISTINCT f.utc_date) AS active_days,
                    sum(f.observation_count) AS observations,
                    sum(f.active_time_seconds) / 3600.0 AS active_hours,
                    sum(f.airborne_time_seconds) / 3600.0 AS airborne_hours,
                    sum(f.ground_active_time_seconds) / 3600.0 AS ground_active_hours,
                    (
                        array_agg(p.airport_ident ORDER BY p.ground_time_seconds DESC, p.airport_ident)
                        FILTER (WHERE p.airport_ident IS NOT NULL)
                    )[1] AS primary_airport,
                    (
                        array_agg(COALESCE(a.iata_code, p.airport_ident)
                                  ORDER BY p.ground_time_seconds DESC, p.airport_ident)
                        FILTER (WHERE p.airport_ident IS NOT NULL)
                    )[1] AS primary_airport_label
                FROM filtered f
                LEFT JOIN aircraft_airport_day p
                  ON p.dataset_day_id = f.dataset_day_id
                 AND p.address = f.address
                 AND p.is_primary_airport
                LEFT JOIN airport a ON a.ident = p.airport_ident
                GROUP BY f.address
                ORDER BY active_hours DESC, observations DESC
                LIMIT 25
                )
                SELECT
                    tm.*,
                    operator_claim.operator,
                    operator_claim.operator_source_code
                FROM tail_metrics tm
                LEFT JOIN nl_best_aircraft_operator operator_claim
                  ON operator_claim.address = tm.address
                ORDER BY active_hours DESC, observations DESC
                """,
                params,
            ).fetchall()
            operators = connection.execute(
                SNAPSHOT_FILTERED_CTE
                + """
                , aircraft_metrics AS (
                    SELECT
                        address,
                        max(type_code) AS type_code,
                        count(DISTINCT utc_date) AS active_days,
                        sum(observation_count) AS observations,
                        sum(active_time_seconds) AS active_time_seconds,
                        sum(airborne_time_seconds) AS airborne_time_seconds
                    FROM filtered
                    GROUP BY address
                ), attributed AS (
                    SELECT
                        metrics.*,
                        operator_claim.operator,
                        operator_claim.operator_source_code
                    FROM aircraft_metrics metrics
                    LEFT JOIN nl_best_aircraft_operator operator_claim
                      ON operator_claim.address = metrics.address
                )
                SELECT
                    operator,
                    max(operator_source_code) AS operator_source_code,
                    count(DISTINCT address) AS unique_aircraft,
                    count(DISTINCT type_code) FILTER (WHERE type_code IS NOT NULL)
                        AS known_type_codes,
                    sum(active_days) AS active_days,
                    sum(observations) AS observations,
                    sum(active_time_seconds) / 3600.0 AS active_hours,
                    sum(airborne_time_seconds) / 3600.0 AS airborne_hours
                FROM attributed
                WHERE operator IS NOT NULL
                GROUP BY operator
                ORDER BY unique_aircraft DESC, active_hours DESC, operator
                LIMIT 25
                """,
                params,
            ).fetchall()
            hubs = connection.execute(
                SNAPSHOT_FILTERED_CTE
                + """
                SELECT
                    aad.airport_ident,
                    a.iata_code,
                    a.name AS airport_name,
                    a.iso_country,
                    count(DISTINCT f.address) AS unique_aircraft,
                    count(DISTINCT f.type_code) FILTER (WHERE f.type_code IS NOT NULL)
                        AS known_type_codes,
                    count(DISTINCT f.address) FILTER (WHERE aad.is_primary_airport)
                        AS primary_aircraft,
                    sum(aad.ground_observation_count) AS ground_observations,
                    sum(aad.ground_active_time_seconds) / 3600.0 AS ground_active_hours,
                    sum(aad.arrival_count) AS arrival_candidates,
                    sum(aad.departure_count) AS departure_candidates,
                    sum(aad.arrival_count + aad.departure_count) AS movement_candidates,
                    count(DISTINCT f.address) FILTER (WHERE aad.inferred_endpoint_count > 0)
                        AS endpoint_linked_aircraft,
                    count(DISTINCT f.address) FILTER (
                        WHERE aad.arrival_count + aad.departure_count > 0
                    ) AS movement_linked_aircraft,
                    sum(f.active_time_seconds) FILTER (WHERE aad.is_primary_airport) / 3600.0
                        AS primary_tail_active_hours,
                    sum(f.airborne_time_seconds) FILTER (WHERE aad.is_primary_airport) / 3600.0
                        AS primary_tail_airborne_hours
                FROM filtered f
                JOIN aircraft_airport_day aad
                  ON aad.dataset_day_id = f.dataset_day_id
                 AND aad.address = f.address
                JOIN airport a ON a.ident = aad.airport_ident
                WHERE (%(region_query)s::text IS NULL
                       OR heligent_world_region(a.iso_country) =
                          ANY(%(region_codes)s::text[]))
                GROUP BY aad.airport_ident, a.iata_code, a.name, a.iso_country
                ORDER BY movement_candidates DESC, movement_linked_aircraft DESC,
                    unique_aircraft DESC
                LIMIT 25
                """,
                params,
            ).fetchall()
            helicopter_totals = connection.execute(
                HELICOPTER_CTE
                + """
                SELECT
                    count(DISTINCT address) AS unique_aircraft,
                    count(DISTINCT type_code) AS known_type_codes,
                    COALESCE(sum(observation_count), 0) AS observations,
                    COALESCE(sum(active_time_seconds), 0) / 3600.0 AS active_hours,
                    COALESCE(sum(airborne_time_seconds), 0) / 3600.0 AS airborne_hours
                FROM filtered
                """,
                heli_params,
            ).fetchone()
            helicopter_types = connection.execute(
                HELICOPTER_CTE
                + """
                SELECT
                    type_code,
                    max(type_description) AS description,
                    count(DISTINCT address) AS unique_aircraft,
                    sum(observation_count) AS observations,
                    sum(active_time_seconds) / 3600.0 AS active_hours,
                    sum(airborne_time_seconds) / 3600.0 AS airborne_hours
                FROM filtered
                GROUP BY type_code
                ORDER BY unique_aircraft DESC, active_hours DESC
                LIMIT 20
                """,
                heli_params,
            ).fetchall()

        available_dates = [row["utc_date"] for row in available_rows]
        available_set = set(available_dates)
        requested_dates = [start_date + timedelta(days=offset) for offset in range(requested_days)]
        missing_dates = [value for value in requested_dates if value not in available_set]
        available_days = len(available_dates)
        if available_days == 0:
            coverage_message = (
                f"None of the {requested_days} requested UTC days are available locally yet."
            )
        elif missing_dates:
            coverage_message = (
                f"Only {available_days} of {requested_days} requested UTC days are available "
                "locally. Results below are partial."
            )
        else:
            coverage_message = f"All {requested_days} requested UTC days are available locally."

        return {
            "selection": {
                "from_date": start_date,
                "to_date": end_date,
                "type_code": normalized_type,
                "helicopters_only": helicopters_only,
                "latest_available_date": latest,
            },
            "coverage": {
                "requested_days": requested_days,
                "available_days": available_days,
                "complete": not missing_dates,
                "available_dates": available_dates,
                "missing_dates": missing_dates,
                "message": coverage_message,
            },
            "totals": totals,
            "daily_activity": daily,
            "types": types,
            "type_options": type_options,
            "tails": tails,
            "operators": operators,
            "hubs": hubs,
            "helicopters": {
                "totals": helicopter_totals,
                "types": helicopter_types,
                "classification_note": (
                    "Rotorcraft totals include type codes classified by the explicit local "
                    "classification table. Unknown and unclassified types are excluded."
                ),
            },
        }

    @staticmethod
    def _empty_snapshot(*, type_code: str | None, helicopters_only: bool) -> dict[str, Any]:
        return {
            "selection": {
                "from_date": None,
                "to_date": None,
                "type_code": type_code,
                "helicopters_only": helicopters_only,
                "latest_available_date": None,
            },
            "coverage": {
                "requested_days": 0,
                "available_days": 0,
                "complete": False,
                "available_dates": [],
                "missing_dates": [],
                "message": "No processed ADS-B days are available locally yet.",
            },
            "totals": {
                "unique_aircraft": 0,
                "aircraft_days": 0,
                "observations": 0,
                "active_hours": 0,
                "airborne_hours": 0,
                "ground_active_hours": 0,
                "known_type_codes": 0,
                "airport_linked_aircraft": 0,
            },
            "daily_activity": [],
            "types": [],
            "type_options": [],
            "tails": [],
            "operators": [],
            "hubs": [],
            "helicopters": {
                "totals": {
                    "unique_aircraft": 0,
                    "known_type_codes": 0,
                    "observations": 0,
                    "active_hours": 0,
                    "airborne_hours": 0,
                },
                "types": [],
                "classification_note": (
                    "Rotorcraft totals include only explicitly classified type codes."
                ),
            },
        }
