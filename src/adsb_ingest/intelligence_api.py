from __future__ import annotations

import argparse
import hmac
import logging
import os
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping, Sequence
from uuid import uuid4

import psycopg
from flask import Flask, Response, jsonify, request
from psycopg.rows import dict_row
from waitress import serve
from werkzeug.exceptions import BadRequest, HTTPException, NotFound
from werkzeug.middleware.proxy_fix import ProxyFix

from .analytics import AnalyticsStore, MAX_ANALYTICS_DAYS, normalize_world_region_query
from .postgres import PostgresStore


LOGGER = logging.getLogger("heligent_intelligence_api")
REGISTRATION_RE = re.compile(r"[A-Z0-9][A-Z0-9-]{1,39}")
AIRPORT_RE = re.compile(r"[A-Z0-9][A-Z0-9-]{1,11}")
CATEGORIES = {
    "ALL",
    "UNKNOWN",
    "FIXED_WING",
    "ROTORCRAFT",
    "GLIDER",
    "BALLOON",
    "UAV",
    "OTHER",
}
AIRCRAFT_RANKING_METRICS = {
    "active_hours": "active_hours",
    "airborne_hours": "airborne_hours",
    "observations": "observations",
    "movement_candidates": "movement_candidates",
    "active_days": "active_days",
}
AIRPORT_RANKING_METRICS = {
    "movement_candidates": "movement_candidates",
    "arrival_candidates": "arrival_candidates",
    "departure_candidates": "departure_candidates",
    "ground_observations": "ground_observations",
    "unique_aircraft": "unique_aircraft",
}


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _normalize_registration(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Aircraft registrations must be text")
    normalized = value.strip().upper()
    if not REGISTRATION_RE.fullmatch(normalized):
        raise ValueError(f"Invalid aircraft registration: {normalized or value}")
    return normalized


def _normalize_registrations(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, list):
        raise ValueError("registrations must be an array or comma-separated text")
    registrations: list[str] = []
    for value in values:
        normalized = _normalize_registration(value)
        if normalized not in registrations:
            registrations.append(normalized)
    if len(registrations) > 100:
        raise ValueError("A request can contain at most 100 registrations")
    return registrations


def _normalize_airport(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("airport must be text")
    normalized = value.strip().upper()
    if not AIRPORT_RE.fullmatch(normalized):
        raise ValueError("airport must be a valid ICAO, IATA, or stored airport code")
    return normalized


def _normalize_operator(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("operator must be text")
    normalized = " ".join(value.split())
    if len(normalized) > 160:
        raise ValueError("operator is capped at 160 characters")
    return normalized or None


def _normalize_limit(value: Any, *, default: int = 100) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ValueError("limit must be an integer")
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    return limit


def _normalize_category(value: Any, *, default: str = "ALL") -> str:
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        raise ValueError("category must be text")
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    if normalized not in CATEGORIES:
        raise ValueError(
            "category must be ALL, ROTORCRAFT, FIXED_WING, GLIDER, BALLOON, "
            "UAV, OTHER, or UNKNOWN"
        )
    return normalized


def _normalize_status(value: Any, *, field: str) -> str:
    if value in (None, ""):
        return "ALL"
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = value.strip().upper()
    if normalized not in {"ALL", "KNOWN", "UNKNOWN", "MATCHED", "UNMATCHED"}:
        raise ValueError(f"Invalid {field}")
    if field == "operator_status" and normalized in {"KNOWN", "UNKNOWN"}:
        raise ValueError("operator_status must be ALL, MATCHED, or UNMATCHED")
    if field == "type_status" and normalized in {"MATCHED", "UNMATCHED"}:
        raise ValueError("type_status must be ALL, KNOWN, or UNKNOWN")
    return normalized


def _normalize_metric(value: Any, allowed: Mapping[str, str], *, default: str) -> str:
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        raise ValueError("metric must be text")
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise ValueError(f"metric must be one of: {', '.join(sorted(allowed))}")
    return normalized


def _normalize_boolean(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError("compare_previous must be true or false")


def _normalize_region(value: Any) -> tuple[str, tuple[str, ...]]:
    if not isinstance(value, str):
        raise ValueError("region must be text")
    canonical, codes = normalize_world_region_query(value)
    if canonical is None or not codes:
        raise ValueError("region is required")
    return canonical, codes


class IntelligenceService:
    def __init__(self, store: PostgresStore) -> None:
        self.store = store
        self.analytics = AnalyticsStore(store)

    def availability(self) -> dict[str, Any]:
        return self.analytics.availability()

    def coverage(self, start_date: date, end_date: date) -> dict[str, Any]:
        requested = [
            start_date + timedelta(days=offset)
            for offset in range((end_date - start_date).days + 1)
        ]
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT utc_date
                FROM nl_aircraft_activity_cache
                WHERE utc_date BETWEEN %s AND %s
                ORDER BY utc_date
                """,
                (start_date, end_date),
            ).fetchall()
        available = [row[0] for row in rows]
        available_set = set(available)
        missing = [item for item in requested if item not in available_set]
        return {
            "requested_days": len(requested),
            "available_days": len(available),
            "complete": not missing,
            "available_dates": available,
            "missing_dates": missing,
        }

    def helicopter_activity(
        self,
        start_date: date,
        end_date: date,
        *,
        registrations: Sequence[str] = (),
        operator: str | None = None,
        airport: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        registration_keys = [re.sub(r"[^A-Z0-9]", "", value) for value in registrations]
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "registration_keys": registration_keys,
            "operator": operator,
            "operator_like": f"%{operator}%" if operator else None,
            "airport": airport,
            "limit": limit,
        }
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                SELECT
                    activity.address,
                    max(activity.registration) AS registration,
                    max(activity.type_code) AS type_code,
                    max(activity.type_description) AS type_description,
                    max(activity.operator) AS operator,
                    max(activity.operator_country) AS operator_country,
                    max(activity.operator_home_region) AS operator_region,
                    max(activity.operator_source_code) AS operator_source_code,
                    min(activity.utc_date) AS first_activity_date,
                    max(activity.utc_date) AS last_activity_date,
                    count(DISTINCT activity.utc_date) AS active_days,
                    sum(activity.observations) AS observations,
                    sum(activity.active_hours) AS active_hours,
                    sum(activity.airborne_hours) AS airborne_hours,
                    sum(activity.ground_active_hours) AS ground_active_hours,
                    sum(activity.estimated_distance_nm) AS estimated_distance_nm,
                    sum(activity.distinct_airports) AS airport_links
                FROM nl_aircraft_activity activity
                WHERE activity.utc_date BETWEEN %(start_date)s AND %(end_date)s
                  AND activity.category = 'ROTORCRAFT'
                  AND (
                      cardinality(%(registration_keys)s::text[]) = 0
                      OR regexp_replace(upper(activity.registration), '[^A-Z0-9]', '', 'g')
                         = ANY(%(registration_keys)s::text[])
                  )
                  AND (
                      %(operator)s::text IS NULL
                      OR activity.operator ILIKE %(operator_like)s
                  )
                  AND (
                      %(airport)s::text IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM nl_airport_activity airport_activity
                          WHERE airport_activity.utc_date = activity.utc_date
                            AND airport_activity.address = activity.address
                            AND (
                                upper(airport_activity.airport_ident) = %(airport)s
                                OR upper(COALESCE(airport_activity.iata_code, '')) = %(airport)s
                            )
                      )
                  )
                GROUP BY activity.address
                ORDER BY active_hours DESC, observations DESC, activity.address
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()

    def helicopter_days(
        self, registration: str, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        registration_key = re.sub(r"[^A-Z0-9]", "", registration)
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                SELECT
                    activity.utc_date,
                    activity.address,
                    activity.registration,
                    activity.type_code,
                    activity.type_description,
                    activity.operator,
                    activity.operator_country,
                    activity.operator_home_region AS operator_region,
                    activity.operator_source_code,
                    activity.observations,
                    activity.active_hours,
                    activity.airborne_hours,
                    activity.ground_active_hours,
                    activity.time_observed_hours,
                    activity.estimated_distance_nm,
                    activity.distinct_airports AS airport_links,
                    COALESCE(airport_totals.arrival_candidates, 0) AS arrival_candidates,
                    COALESCE(airport_totals.departure_candidates, 0) AS departure_candidates,
                    COALESCE(airport_totals.movement_candidates, 0) AS movement_candidates,
                    COALESCE(airport_totals.airports, '[]'::jsonb) AS airports
                FROM nl_aircraft_activity activity
                LEFT JOIN LATERAL (
                    SELECT
                        sum(a.arrival_candidates) AS arrival_candidates,
                        sum(a.departure_candidates) AS departure_candidates,
                        sum(a.movement_candidates) AS movement_candidates,
                        jsonb_agg(
                            jsonb_build_object(
                                'ident', a.airport_ident,
                                'iata', a.iata_code,
                                'name', a.airport_name,
                                'country', a.airport_country,
                                'is_primary', a.is_primary_airport,
                                'evidence', a.link_method,
                                'arrival_candidates', a.arrival_candidates,
                                'departure_candidates', a.departure_candidates,
                                'movement_candidates', a.movement_candidates
                            ) ORDER BY a.is_primary_airport DESC, a.airport_ident
                        ) AS airports
                    FROM nl_airport_activity a
                    WHERE a.utc_date = activity.utc_date
                      AND a.address = activity.address
                ) airport_totals ON true
                WHERE activity.utc_date BETWEEN %s AND %s
                  AND activity.category = 'ROTORCRAFT'
                  AND regexp_replace(upper(activity.registration), '[^A-Z0-9]', '', 'g') = %s
                ORDER BY activity.utc_date
                """,
                (start_date, end_date, registration_key),
            ).fetchall()

    def airport_activity(
        self, airport: str, start_date: date, end_date: date, *, limit: int
    ) -> dict[str, Any]:
        daily = self.analytics.airport_daily_activity(
            start_date, end_date, airport_code=airport, helicopters_only=True
        )
        aircraft = self.analytics.airport_aircraft(
            start_date,
            end_date,
            airport_code=airport,
            helicopters_only=True,
            metric="airport_ground_observations",
            limit=min(limit, 100),
        )
        types = self.analytics.airport_types(
            start_date,
            end_date,
            airport_code=airport,
            helicopters_only=True,
            metric="unique_aircraft",
            limit=min(limit, 100),
        )
        return {
            "coverage": daily["coverage"],
            "daily_activity": daily["daily_activity"],
            "helicopters": aircraft,
            "types": types,
        }

    def aircraft_daily_activity(
        self,
        start_date: date,
        end_date: date,
        *,
        registrations: Sequence[str],
        category: str = "ALL",
    ) -> list[dict[str, Any]]:
        registration_keys = [re.sub(r"[^A-Z0-9]", "", value) for value in registrations]
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "registration_keys": registration_keys,
            "category": None if category == "ALL" else category,
        }
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                SELECT
                    activity.utc_date,
                    activity.address,
                    activity.registration,
                    activity.type_code,
                    activity.type_description,
                    activity.category,
                    activity.operator,
                    activity.operator_country,
                    activity.operator_home_region AS operator_region,
                    activity.operator_source_code,
                    activity.identity_source_code,
                    activity.identity_status,
                    activity.observations,
                    activity.active_hours,
                    activity.airborne_hours,
                    activity.ground_active_hours,
                    activity.time_observed_hours,
                    activity.estimated_distance_nm,
                    activity.distinct_airports AS airport_links,
                    GREATEST(
                        dataset.updated_at,
                        activity_cache.refreshed_at,
                        operator_cache.refreshed_at
                    ) AS data_revision,
                    COALESCE(airport_totals.arrival_candidates, 0) AS arrival_candidates,
                    COALESCE(airport_totals.departure_candidates, 0) AS departure_candidates,
                    COALESCE(airport_totals.movement_candidates, 0) AS movement_candidates,
                    COALESCE(airport_totals.airports, '[]'::jsonb) AS airports
                FROM nl_aircraft_activity activity
                JOIN dataset_day dataset ON dataset.id = activity.dataset_day_id
                JOIN nl_aircraft_activity_cache activity_cache
                  ON activity_cache.dataset_day_id = activity.dataset_day_id
                 AND activity_cache.address = activity.address
                LEFT JOIN nl_best_aircraft_operator operator_cache
                  ON operator_cache.address = activity.address
                LEFT JOIN LATERAL (
                    SELECT
                        sum(a.arrival_candidates) AS arrival_candidates,
                        sum(a.departure_candidates) AS departure_candidates,
                        sum(a.movement_candidates) AS movement_candidates,
                        jsonb_agg(
                            jsonb_build_object(
                                'ident', a.airport_ident,
                                'iata', a.iata_code,
                                'name', a.airport_name,
                                'country', a.airport_country,
                                'region', a.activity_region,
                                'is_primary', a.is_primary_airport,
                                'evidence', a.link_method,
                                'arrival_candidates', a.arrival_candidates,
                                'departure_candidates', a.departure_candidates,
                                'movement_candidates', a.movement_candidates
                            ) ORDER BY a.is_primary_airport DESC, a.airport_ident
                        ) AS airports
                    FROM nl_airport_activity a
                    WHERE a.utc_date = activity.utc_date
                      AND a.address = activity.address
                ) airport_totals ON true
                WHERE activity.utc_date BETWEEN %(start_date)s AND %(end_date)s
                  AND regexp_replace(upper(activity.registration), '[^A-Z0-9]', '', 'g')
                      = ANY(%(registration_keys)s::text[])
                  AND (%(category)s::text IS NULL OR activity.category = %(category)s)
                ORDER BY activity.utc_date, activity.registration, activity.address
                """,
                params,
            ).fetchall()

    def region_aircraft_rankings(
        self,
        region_codes: Sequence[str],
        start_date: date,
        end_date: date,
        *,
        category: str,
        operator_status: str,
        type_status: str,
        metric: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        order_column = AIRCRAFT_RANKING_METRICS[metric]
        params = {
            "region_codes": list(region_codes),
            "start_date": start_date,
            "end_date": end_date,
            "category": None if category == "ALL" else category,
            "operator_status": operator_status,
            "type_status": type_status,
            "limit": limit,
        }
        query = f"""
            WITH regional_day AS (
                SELECT
                    activity.address,
                    activity.utc_date,
                    max(activity.registration) AS registration,
                    max(activity.type_code) AS type_code,
                    max(activity.type_description) AS type_description,
                    max(activity.category) AS category,
                    max(activity.operator) AS operator,
                    max(activity.operator_country) AS operator_country,
                    max(activity.operator_home_region) AS operator_home_region,
                    max(activity.operator_source_code) AS operator_source_code,
                    max(activity.aircraft_day_observations) AS observations,
                    max(activity.aircraft_day_active_hours) AS active_hours,
                    max(activity.aircraft_day_airborne_hours) AS airborne_hours,
                    sum(activity.linked_airports) AS linked_airports,
                    sum(activity.arrival_candidates) AS arrival_candidates,
                    sum(activity.departure_candidates) AS departure_candidates,
                    sum(activity.movement_candidates) AS movement_candidates
                FROM nl_region_activity activity
                WHERE activity.utc_date BETWEEN %(start_date)s AND %(end_date)s
                  AND activity.activity_region = ANY(%(region_codes)s::text[])
                  AND (%(category)s::text IS NULL OR activity.category = %(category)s)
                  AND (
                      %(operator_status)s = 'ALL'
                      OR (%(operator_status)s = 'MATCHED' AND nullif(activity.operator, '') IS NOT NULL)
                      OR (%(operator_status)s = 'UNMATCHED' AND nullif(activity.operator, '') IS NULL)
                  )
                  AND (
                      %(type_status)s = 'ALL'
                      OR (%(type_status)s = 'KNOWN' AND nullif(activity.type_code, '') IS NOT NULL)
                      OR (%(type_status)s = 'UNKNOWN' AND nullif(activity.type_code, '') IS NULL)
                  )
                GROUP BY activity.address, activity.utc_date
            )
            SELECT
                regional_day.address,
                max(regional_day.registration) AS registration,
                max(regional_day.type_code) AS type_code,
                max(regional_day.type_description) AS type_description,
                max(regional_day.category) AS category,
                max(regional_day.operator) AS operator,
                max(regional_day.operator_country) AS operator_country,
                max(regional_day.operator_home_region) AS operator_region,
                max(regional_day.operator_source_code) AS operator_source_code,
                min(regional_day.utc_date) AS first_activity_date,
                max(regional_day.utc_date) AS last_activity_date,
                count(DISTINCT regional_day.utc_date) AS active_days,
                sum(regional_day.observations) AS observations,
                sum(regional_day.active_hours) AS active_hours,
                sum(regional_day.airborne_hours) AS airborne_hours,
                sum(regional_day.linked_airports) AS airport_links,
                sum(regional_day.arrival_candidates) AS arrival_candidates,
                sum(regional_day.departure_candidates) AS departure_candidates,
                sum(regional_day.movement_candidates) AS movement_candidates
            FROM regional_day
            GROUP BY regional_day.address
            ORDER BY {order_column} DESC NULLS LAST, active_days DESC, regional_day.address
            LIMIT %(limit)s
        """
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(query, params).fetchall()

    def region_type_breakdown(
        self,
        region_codes: Sequence[str],
        start_date: date,
        end_date: date,
        *,
        category: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        params = {
            "region_codes": list(region_codes),
            "start_date": start_date,
            "end_date": end_date,
            "category": None if category == "ALL" else category,
            "limit": limit,
        }
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                WITH regional_day AS (
                    SELECT
                        activity.utc_date,
                        activity.address,
                        COALESCE(nullif(activity.type_code, ''), 'UNKNOWN') AS type_code,
                        max(activity.type_description) AS type_description,
                        max(activity.category) AS category,
                        max(activity.aircraft_day_observations) AS observations,
                        max(activity.aircraft_day_active_hours) AS active_hours,
                        max(activity.aircraft_day_airborne_hours) AS airborne_hours,
                        sum(activity.movement_candidates) AS movement_candidates
                    FROM nl_region_activity activity
                    WHERE activity.utc_date BETWEEN %(start_date)s AND %(end_date)s
                      AND activity.activity_region = ANY(%(region_codes)s::text[])
                      AND (%(category)s::text IS NULL OR activity.category = %(category)s)
                    GROUP BY activity.utc_date, activity.address,
                             COALESCE(nullif(activity.type_code, ''), 'UNKNOWN')
                )
                SELECT
                    regional_day.type_code,
                    max(regional_day.type_description) AS type_description,
                    max(regional_day.category) AS category,
                    count(DISTINCT regional_day.address) AS unique_aircraft,
                    count(DISTINCT regional_day.utc_date) AS active_days,
                    sum(regional_day.observations) AS observations,
                    sum(regional_day.active_hours) AS active_hours,
                    sum(regional_day.airborne_hours) AS airborne_hours,
                    sum(regional_day.movement_candidates) AS movement_candidates
                FROM regional_day
                GROUP BY regional_day.type_code
                ORDER BY unique_aircraft DESC, active_hours DESC NULLS LAST, type_code
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()

    def region_airport_rankings(
        self,
        region_codes: Sequence[str],
        start_date: date,
        end_date: date,
        *,
        category: str,
        metric: str,
        limit: int,
        compare_previous: bool,
    ) -> list[dict[str, Any]]:
        order_column = AIRPORT_RANKING_METRICS[metric]
        period_days = (end_date - start_date).days + 1
        previous_end = start_date - timedelta(days=1)
        previous_start = previous_end - timedelta(days=period_days - 1)
        params = {
            "region_codes": list(region_codes),
            "start_date": start_date,
            "end_date": end_date,
            "previous_start": previous_start,
            "previous_end": previous_end,
            "category": None if category == "ALL" else category,
            "compare_previous": compare_previous,
            "limit": limit,
        }
        query = f"""
            WITH current_period AS (
                SELECT
                    activity.airport_ident,
                    max(activity.iata_code) AS iata_code,
                    max(activity.airport_name) AS airport_name,
                    max(activity.municipality) AS municipality,
                    max(activity.airport_country) AS airport_country,
                    max(activity.iso_region) AS iso_region,
                    count(DISTINCT activity.address) AS unique_aircraft,
                    count(DISTINCT activity.address)
                        FILTER (WHERE nullif(activity.type_code, '') IS NOT NULL)
                        AS known_type_aircraft,
                    count(DISTINCT activity.address)
                        FILTER (WHERE nullif(activity.type_code, '') IS NULL)
                        AS unknown_type_aircraft,
                    count(DISTINCT activity.utc_date) AS active_days,
                    sum(activity.airport_ground_observations) AS ground_observations,
                    sum(activity.airport_ground_active_hours) AS ground_active_hours,
                    sum(activity.arrival_candidates) AS arrival_candidates,
                    sum(activity.departure_candidates) AS departure_candidates,
                    sum(activity.movement_candidates) AS movement_candidates
                FROM nl_airport_activity activity
                WHERE activity.utc_date BETWEEN %(start_date)s AND %(end_date)s
                  AND activity.activity_region = ANY(%(region_codes)s::text[])
                  AND (%(category)s::text IS NULL OR activity.category = %(category)s)
                GROUP BY activity.airport_ident
            ),
            ranked_current AS (
                SELECT *
                FROM current_period
                ORDER BY {order_column} DESC NULLS LAST,
                         unique_aircraft DESC,
                         airport_ident
                LIMIT %(limit)s
            ),
            previous_period AS (
                SELECT
                    activity.airport_ident,
                    count(DISTINCT activity.address) AS unique_aircraft,
                    sum(activity.airport_ground_observations) AS ground_observations,
                    sum(activity.arrival_candidates) AS arrival_candidates,
                    sum(activity.departure_candidates) AS departure_candidates,
                    sum(activity.movement_candidates) AS movement_candidates
                FROM nl_airport_activity activity
                WHERE %(compare_previous)s
                  AND activity.utc_date BETWEEN %(previous_start)s AND %(previous_end)s
                  AND activity.activity_region = ANY(%(region_codes)s::text[])
                  AND (%(category)s::text IS NULL OR activity.category = %(category)s)
                GROUP BY activity.airport_ident
            )
            SELECT
                ranked_current.*,
                COALESCE(leading_types.items, '[]'::jsonb) AS leading_types,
                previous_period.{order_column} AS previous_metric_value,
                ranked_current.{order_column} - previous_period.{order_column}
                    AS metric_change
            FROM ranked_current
            LEFT JOIN previous_period USING (airport_ident)
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'type_code', ranked_type.type_code,
                        'type_description', ranked_type.type_description,
                        'unique_aircraft', ranked_type.unique_aircraft
                    ) ORDER BY ranked_type.unique_aircraft DESC, ranked_type.type_code
                ) AS items
                FROM (
                    SELECT
                        COALESCE(nullif(activity.type_code, ''), 'UNKNOWN') AS type_code,
                        max(activity.type_description) AS type_description,
                        count(DISTINCT activity.address) AS unique_aircraft
                    FROM nl_airport_activity activity
                    WHERE activity.utc_date BETWEEN %(start_date)s AND %(end_date)s
                      AND activity.airport_ident = ranked_current.airport_ident
                      AND (%(category)s::text IS NULL OR activity.category = %(category)s)
                    GROUP BY COALESCE(nullif(activity.type_code, ''), 'UNKNOWN')
                    ORDER BY unique_aircraft DESC, type_code
                    LIMIT 5
                ) ranked_type
            ) leading_types ON true
            ORDER BY ranked_current.{order_column} DESC NULLS LAST,
                     ranked_current.unique_aircraft DESC,
                     ranked_current.airport_ident
        """
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(query, params).fetchall()


def _parse_date_range(
    from_value: Any, to_value: Any, *, availability: Mapping[str, Any]
) -> tuple[date, date]:
    if from_value in (None, "") and to_value in (None, ""):
        latest = availability.get("latest_available_date")
        if not isinstance(latest, date):
            raise LookupError("No processed Heligent dates are available")
        return latest, latest
    if from_value in (None, "") or to_value in (None, ""):
        raise ValueError("Both from and to dates are required")
    try:
        start_date = date.fromisoformat(str(from_value))
        end_date = date.fromisoformat(str(to_value))
    except ValueError as exc:
        raise ValueError("Dates must use YYYY-MM-DD format") from exc
    if end_date < start_date:
        raise ValueError("to must be on or after from")
    if (end_date - start_date).days + 1 > MAX_ANALYTICS_DAYS:
        raise ValueError(f"Date ranges are capped at {MAX_ANALYTICS_DAYS} days")
    if end_date >= datetime.now(UTC).date():
        raise ValueError("Only completed historical UTC dates can be queried")
    return start_date, end_date


def create_intelligence_api(
    database_url: str | None = None,
    *,
    token: str | None = None,
    service: IntelligenceService | Any | None = None,
    trusted_proxy_count: int = 0,
) -> Flask:
    token_from_environment = token is None
    expected_token = (token if token is not None else os.getenv("HELIGENT_API_TOKEN", "")).strip()
    if not expected_token:
        raise RuntimeError("HELIGENT_API_TOKEN is required")
    if token_from_environment and (
        len(expected_token) < 32 or expected_token.lower().startswith("replace-")
    ):
        raise RuntimeError(
            "HELIGENT_API_TOKEN must contain at least 32 non-placeholder characters"
        )
    if isinstance(trusted_proxy_count, bool) or trusted_proxy_count < 0:
        raise ValueError("trusted_proxy_count must be zero or greater")
    if service is None:
        dsn = database_url or os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL is required")
        service = IntelligenceService(PostgresStore(dsn))

    app = Flask(__name__, static_folder=None)
    app.config.update(JSON_SORT_KEYS=False, MAX_CONTENT_LENGTH=64 * 1024)
    if trusted_proxy_count:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=trusted_proxy_count,
            x_proto=trusted_proxy_count,
            x_host=trusted_proxy_count,
        )

    @app.before_request
    def assign_request_id() -> None:
        supplied = request.headers.get("X-Request-ID", "").strip()
        request.environ["heligent.request_id"] = supplied[:128] or str(uuid4())

    @app.before_request
    def require_token() -> Response | None:
        if request.endpoint == "health":
            return None
        supplied = request.headers.get("Authorization", "")
        candidate = supplied[7:] if supplied.startswith("Bearer ") else ""
        if not hmac.compare_digest(candidate, expected_token):
            response = jsonify(
                {"error": {"code": "UNAUTHORIZED", "message": "Valid bearer token required"}}
            )
            response.status_code = 401
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        return None

    @app.after_request
    def response_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Request-ID"] = request.environ.get("heligent.request_id", "")
        return response

    @app.errorhandler(ValueError)
    def invalid_request(exc: ValueError) -> tuple[dict[str, Any], int]:
        return {"error": {"code": "INVALID_REQUEST", "message": str(exc)}}, 400

    @app.errorhandler(BadRequest)
    def malformed_request(exc: BadRequest) -> tuple[dict[str, Any], int]:
        return {
            "error": {"code": "INVALID_REQUEST", "message": "Malformed request body"}
        }, 400

    @app.errorhandler(NotFound)
    def unknown_route(exc: NotFound) -> tuple[dict[str, Any], int]:
        return {
            "error": {"code": "NOT_FOUND", "message": "API endpoint not found"}
        }, 404

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException) -> tuple[dict[str, Any], int]:
        return {
            "error": {
                "code": exc.name.upper().replace(" ", "_"),
                "message": exc.description,
            }
        }, int(exc.code or 500)

    @app.errorhandler(LookupError)
    def not_found(exc: LookupError) -> tuple[dict[str, Any], int]:
        return {"error": {"code": "NOT_FOUND", "message": str(exc)}}, 404

    @app.errorhandler(psycopg.errors.QueryCanceled)
    def query_timeout(exc: Exception) -> tuple[dict[str, Any], int]:
        return {
            "error": {
                "code": "QUERY_TIMEOUT",
                "message": "The intelligence query exceeded the database time limit",
            }
        }, 503

    @app.errorhandler(Exception)
    def internal_error(exc: Exception) -> tuple[dict[str, Any], int]:
        LOGGER.exception(
            "Intelligence API request failed (request_id=%s)",
            request.environ.get("heligent.request_id"),
        )
        return {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "The intelligence service could not complete the request",
            }
        }, 500

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "heligent-intelligence-api", "version": "v1"}

    @app.get("/api/v1/coverage")
    def coverage() -> dict[str, Any]:
        return {"data": _json_ready(service.availability())}

    def activity_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        availability = service.availability()
        start_date, end_date = _parse_date_range(
            payload.get("from"), payload.get("to"), availability=availability
        )
        registrations = _normalize_registrations(payload.get("registrations"))
        operator = _normalize_operator(payload.get("operator"))
        airport = _normalize_airport(payload.get("airport"))
        limit = _normalize_limit(payload.get("limit"))
        rows = service.helicopter_activity(
            start_date,
            end_date,
            registrations=registrations,
            operator=operator,
            airport=airport,
            limit=limit,
        )
        return {
            "data": _json_ready(rows),
            "meta": _json_ready(
                {
                    "from": start_date,
                    "to": end_date,
                    "coverage": service.coverage(start_date, end_date),
                    "count": len(rows),
                    "limit": limit,
                    "filters": {
                        "registrations": registrations,
                        "operator": operator,
                        "airport": airport,
                    },
                }
            ),
        }

    @app.get("/api/v1/helicopters/activity")
    def helicopter_activity_get() -> dict[str, Any]:
        return activity_payload(
            {
                "from": request.args.get("from"),
                "to": request.args.get("to"),
                "registrations": request.args.get("registrations"),
                "operator": request.args.get("operator"),
                "airport": request.args.get("airport"),
                "limit": request.args.get("limit"),
            }
        )

    @app.post("/api/v1/helicopters/activity")
    def helicopter_activity_post() -> dict[str, Any]:
        payload = request.get_json(silent=False)
        if not isinstance(payload, dict):
            raise ValueError("A JSON object is required")
        return activity_payload(payload)

    @app.get("/api/v1/helicopters/<registration>/days")
    def helicopter_days(registration: str) -> dict[str, Any]:
        normalized = _normalize_registration(registration)
        availability = service.availability()
        start_date, end_date = _parse_date_range(
            request.args.get("from"), request.args.get("to"), availability=availability
        )
        rows = service.helicopter_days(normalized, start_date, end_date)
        if not rows:
            raise LookupError(f"No helicopter activity found for {normalized} in that date range")
        return {
            "data": _json_ready(rows),
            "meta": _json_ready(
                {
                    "registration": normalized,
                    "from": start_date,
                    "to": end_date,
                    "coverage": service.coverage(start_date, end_date),
                    "count": len(rows),
                }
            ),
        }

    @app.get("/api/v1/airports/<airport>/activity")
    def airport_activity(airport: str) -> dict[str, Any]:
        normalized = _normalize_airport(airport)
        assert normalized is not None
        availability = service.availability()
        start_date, end_date = _parse_date_range(
            request.args.get("from"), request.args.get("to"), availability=availability
        )
        limit = _normalize_limit(request.args.get("limit"), default=25)
        result = service.airport_activity(
            normalized, start_date, end_date, limit=limit
        )
        return {
            "data": _json_ready(result),
            "meta": _json_ready(
                {"airport": normalized, "from": start_date, "to": end_date}
            ),
        }

    @app.post("/api/v1/aircraft/daily-activity")
    def aircraft_daily_activity() -> dict[str, Any]:
        payload = request.get_json(silent=False)
        if not isinstance(payload, dict):
            raise ValueError("A JSON object is required")
        registrations = _normalize_registrations(payload.get("registrations"))
        if not registrations:
            raise ValueError("At least one registration is required")
        availability = service.availability()
        start_date, end_date = _parse_date_range(
            payload.get("from"), payload.get("to"), availability=availability
        )
        category = _normalize_category(payload.get("category"))
        rows = service.aircraft_daily_activity(
            start_date,
            end_date,
            registrations=registrations,
            category=category,
        )
        returned_keys = {
            re.sub(r"[^A-Z0-9]", "", str(row.get("registration") or "").upper())
            for row in rows
        }
        without_activity = [
            registration
            for registration in registrations
            if re.sub(r"[^A-Z0-9]", "", registration) not in returned_keys
        ]
        return {
            "data": _json_ready(rows),
            "meta": _json_ready(
                {
                    "from": start_date,
                    "to": end_date,
                    "category": category,
                    "coverage": service.coverage(start_date, end_date),
                    "requested_registrations": registrations,
                    "registrations_without_activity": without_activity,
                    "count": len(rows),
                }
            ),
        }

    def region_request(region: str) -> tuple[str, tuple[str, ...], date, date, str]:
        canonical, region_codes = _normalize_region(region)
        availability = service.availability()
        start_date, end_date = _parse_date_range(
            request.args.get("from"), request.args.get("to"), availability=availability
        )
        category = _normalize_category(request.args.get("category"), default="ROTORCRAFT")
        return canonical, region_codes, start_date, end_date, category

    @app.get("/api/v1/regions/<region>/aircraft/rankings")
    def region_aircraft_rankings(region: str) -> dict[str, Any]:
        canonical, region_codes, start_date, end_date, category = region_request(region)
        metric = _normalize_metric(
            request.args.get("metric"), AIRCRAFT_RANKING_METRICS, default="active_hours"
        )
        operator_status = _normalize_status(
            request.args.get("operator_status"), field="operator_status"
        )
        type_status = _normalize_status(
            request.args.get("type_status"), field="type_status"
        )
        limit = _normalize_limit(request.args.get("limit"), default=100)
        rows = service.region_aircraft_rankings(
            region_codes,
            start_date,
            end_date,
            category=category,
            operator_status=operator_status,
            type_status=type_status,
            metric=metric,
            limit=limit,
        )
        return {
            "data": _json_ready(rows),
            "meta": _json_ready(
                {
                    "region": canonical,
                    "from": start_date,
                    "to": end_date,
                    "category": category,
                    "metric": metric,
                    "operator_status": operator_status,
                    "type_status": type_status,
                    "coverage": service.coverage(start_date, end_date),
                    "count": len(rows),
                    "limit": limit,
                }
            ),
        }

    @app.get("/api/v1/regions/<region>/types")
    def region_type_breakdown(region: str) -> dict[str, Any]:
        canonical, region_codes, start_date, end_date, category = region_request(region)
        limit = _normalize_limit(request.args.get("limit"), default=100)
        rows = service.region_type_breakdown(
            region_codes,
            start_date,
            end_date,
            category=category,
            limit=limit,
        )
        return {
            "data": _json_ready(rows),
            "meta": _json_ready(
                {
                    "region": canonical,
                    "from": start_date,
                    "to": end_date,
                    "category": category,
                    "coverage": service.coverage(start_date, end_date),
                    "count": len(rows),
                    "limit": limit,
                }
            ),
        }

    @app.get("/api/v1/regions/<region>/airports/rankings")
    def region_airport_rankings(region: str) -> dict[str, Any]:
        canonical, region_codes, start_date, end_date, category = region_request(region)
        metric = _normalize_metric(
            request.args.get("metric"), AIRPORT_RANKING_METRICS,
            default="movement_candidates",
        )
        compare_previous = _normalize_boolean(request.args.get("compare_previous"))
        limit = _normalize_limit(request.args.get("limit"), default=100)
        rows = service.region_airport_rankings(
            region_codes,
            start_date,
            end_date,
            category=category,
            metric=metric,
            limit=limit,
            compare_previous=compare_previous,
        )
        previous_period = None
        if compare_previous:
            period_days = (end_date - start_date).days + 1
            previous_to = start_date - timedelta(days=1)
            previous_period = {
                "from": previous_to - timedelta(days=period_days - 1),
                "to": previous_to,
            }
        return {
            "data": _json_ready(rows),
            "meta": _json_ready(
                {
                    "region": canonical,
                    "from": start_date,
                    "to": end_date,
                    "category": category,
                    "metric": metric,
                    "compare_previous": compare_previous,
                    "previous_period": previous_period,
                    "coverage": service.coverage(start_date, end_date),
                    "count": len(rows),
                    "limit": limit,
                    "methodology": (
                        "Hub ranking uses ADS-B-derived airport links and candidate "
                        "arrivals/departures; it is not an audited movement count."
                    ),
                }
            ),
        }

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Heligent service-to-service intelligence API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5100)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--trusted-proxy-count", type=int, default=0)
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_intelligence_api(trusted_proxy_count=args.trusted_proxy_count)
    LOGGER.info("Intelligence API listening on http://%s:%s", args.host, args.port)
    serve(app, host=args.host, port=args.port, threads=args.threads)


if __name__ == "__main__":
    main()
