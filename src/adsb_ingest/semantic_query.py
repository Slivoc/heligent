from __future__ import annotations

import json
import os
import re
import time
import uuid
import calendar
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import psycopg
import requests
import sqlglot
from psycopg.rows import dict_row
from sqlglot import exp

from .analytics import AnalyticsStore, MAX_ANALYTICS_DAYS
from .aircraft_types import resolve_aircraft_type_code
from .natural_language import (
    DEFAULT_OPENAI_MODEL,
    DEFAULT_QUERY_DEBUG_DIR,
    MAX_CONTEXT_QUESTIONS,
    MAX_QUESTION_LENGTH,
    PlannerDecision,
    QueryPlannerUnavailable,
)


MAX_RESULT_ROWS = 100
PLANNER_TIMEOUT_SECONDS = 12.0
STATEMENT_TIMEOUT_MS = 10_000
LOCK_TIMEOUT_MS = 1_000
MAX_PLANNED_COST = 50_000_000.0
MAX_SQL_LENGTH = 12_000

WORLD_REGION_QUESTION_TERMS = (
    "north american",
    "south american",
    "north america",
    "south america",
    "european",
    "african",
    "asian",
    "oceanian",
    "europe",
    "africa",
    "asia",
    "oceania",
    "americas",
)

MONTH_NUMBERS = {
    name.lower(): number
    for number in range(1, 13)
    for name in (calendar.month_name[number], calendar.month_abbr[number])
}

ALLOWED_RELATIONS = frozenset(
    {
        "nl_aircraft_activity",
        "nl_airport_activity",
        "nl_country_activity",
        "nl_region_activity",
        "nl_operator_claim",
        "nl_aircraft_assignment",
        "nl_company",
        "nl_company_site",
        "nl_approval",
        "nl_capability",
    }
)

ALLOWED_FUNCTIONS = frozenset(
    {
        "AND",
        "AVG",
        "BOOL_AND",
        "BOOL_OR",
        "CASE",
        "CAST",
        "COALESCE",
        "CONCAT",
        "CONCAT_WS",
        "COUNT",
        "DATE_TRUNC",
        "EXTRACT",
        "GREATEST",
        "GROUP_CONCAT",
        "IF",
        "LEAST",
        "LOGICAL_AND",
        "LOGICAL_OR",
        "LOWER",
        "MAX",
        "MIN",
        "NULLIF",
        "OR",
        "REGEXP_REPLACE",
        "ROUND",
        "STRING_AGG",
        "SUM",
        "TIMESTAMP_TRUNC",
        "TIME_TO_STR",
        "TO_CHAR",
        "TRIM",
        "UPPER",
    }
)

SEMANTIC_SCHEMA = """
PostgreSQL read-only semantic schema:

nl_aircraft_activity -- one row per observed aircraft per UTC day
  dataset_day_id bigint (internal join key; do not select), utc_date date,
  address text, registration text, type_code text,
  type_description text, category text, operator text, operator_country text,
  operator_home_region text, operator_source_code text, operator_confidence numeric,
  operator_evidence_kind text, observations bigint, active_hours numeric,
  airborne_hours numeric, ground_active_hours numeric, time_observed_hours numeric,
  distinct_airports integer, estimated_distance_nm numeric, identity_source_code text,
  identity_status text

nl_airport_activity -- one row per aircraft/day/linked airport
  utc_date date, address text, registration text, type_code text,
  type_description text, category text, operator text, operator_country text,
  operator_home_region text, operator_source_code text,
  airport_ident text (ICAO/local identifier), iata_code text,
  airport text (IATA code when available, otherwise airport_ident),
  airport_name text, municipality text,
  activity_country text (ISO alpha-2), iso_region text, activity_region text,
  is_primary_airport boolean, link_method text, presence_count integer,
  airport_ground_observations integer, airport_ground_hours numeric,
  airport_ground_active_hours numeric, arrival_candidates integer,
  departure_candidates integer, movement_candidates integer,
  inferred_endpoint_count integer, aircraft_day_observations bigint,
  aircraft_day_active_hours numeric, aircraft_day_airborne_hours numeric

nl_region_activity -- one row per aircraft/day/world activity region
  utc_date date, address text, registration text, type_code text,
  type_description text, category text, operator text, operator_country text,
  operator_home_region text, operator_source_code text, activity_region text,
  linked_airports bigint, airport_ground_observations bigint,
  airport_ground_hours numeric, airport_ground_active_hours numeric,
  arrival_candidates bigint, departure_candidates bigint,
  movement_candidates bigint, aircraft_day_observations bigint,
  aircraft_day_active_hours numeric, aircraft_day_airborne_hours numeric

nl_country_activity -- one row per aircraft/day/activity country
  Same identity/operator/metric columns as nl_region_activity, with
  activity_country text (ISO alpha-2) and activity_region text.

nl_aircraft_assignment -- current sourced operator claims
  operator text, operator_country text, operator_region text, address text,
  registration text, source_code text, confidence numeric, evidence_kind text

nl_operator_claim -- current claim evidence, potentially multiple rows per aircraft
  operator text, operator_country text, operator_region text,
  aircraft_address text, reported_aircraft_address text, registration text,
  operator_source_code text, confidence numeric, evidence_kind text

nl_company -- one row per active imported company
  company text, legal_name text, trading_name text, is_operator boolean,
  is_mro boolean, country text, geographic_region text, website text,
  site_count bigint, valid_approval_count bigint, capability_count bigint,
  assigned_aircraft_count bigint

nl_company_site -- one row per active company site
  company text, site text, airport_ident text, locality text, region text,
  postal_code text, country text, geographic_region text, is_primary boolean,
  is_base_maintenance boolean, is_line_maintenance boolean

nl_approval -- one row per regulatory approval
  company text, company_country text, geographic_region text, authority_code text,
  approval_type text, approval_number text, status text, valid_from date,
  valid_to date, last_verified_at timestamp, source_url text

nl_capability -- one row per active approval capability
  company text, company_country text, geographic_region text,
  approval_number text, authority_code text, approval_status text,
  capability_kind text, rating_class text, rating_code text, manufacturer text,
  model text, aircraft_type_code text, capability text, site text, locality text,
  site_country text, is_base_maintenance boolean, is_line_maintenance boolean

Semantics:
- Use nl_aircraft_activity for global aircraft, type, and operator activity.
- Use nl_region_activity for activity "in Europe", "in South America", or
  another world region. Use nl_country_activity for activity in a country.
  Use nl_airport_activity for a named airport or city.
- For a three-letter airport code such as ABZ, LHR, or LSI, filter airport
  (or iata_code), not airport_ident. airport_ident contains ICAO/local
  identifiers such as EGPD.
- activity_region/activity_country describe where aircraft activity was linked.
  operator_home_region/operator_country describe the operator and must never be
  used as an activity-location filter unless the user explicitly asks where
  operators are based or registered.
- Count DISTINCT address for unique observed aircraft. Do not count assignments.
- nl_airport_activity can have several airports for one aircraft/day. Use
  COUNT(DISTINCT address), and pre-aggregate aircraft/day before summing whole-day
  aircraft metrics when necessary.
- arrival_candidates, departure_candidates, and movement_candidates are inferred
  ADS-B candidates, not certified movements.
- aircraft_day_active_hours and aircraft_day_airborne_hours are whole-day metrics
  for aircraft linked to that airport, not exact hours inside an airport or region.
- operator is sourced attribution evidence and may be NULL. "Known operators"
  means operator IS NOT NULL in observed activity, not every current assignment.
- "Show the operator if available" means select the nullable operator column;
  never filter on operator, operator_country, or operator_home_region and never
  inner-join an assignment relation for that request.
- Rotorcraft use category = 'ROTORCRAFT'. Ground vehicles are already excluded.
- Common model aliases: H125 and AS350 -> AS50; H135 -> EC35; H145 -> EC45;
  H175 and EC175 -> EC75.
- Country filters use ISO codes: Chile CL, United Kingdom GB, United States US,
  Canada CA, Germany DE, Australia AU, Brazil BR, Argentina AR, Norway NO.
- World regions are EUROPE, NORTH_AMERICA, SOUTH_AMERICA, AFRICA, ASIA, OCEANIA.
""".strip()


SQL_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sql": {"type": "string"},
        "title": {"type": "string"},
        "result_kind": {
            "type": "string",
            "enum": ["ranking", "time_series", "table", "single_value"],
        },
        "from_date": {"type": ["string", "null"]},
        "to_date": {"type": ["string", "null"]},
        "chart_type": {
            "type": "string",
            "enum": ["none", "line", "bar", "doughnut"],
        },
        "chart_x": {"type": ["string", "null"]},
        "chart_y": {"type": ["string", "null"]},
        "chart_label": {"type": ["string", "null"]},
        "caveat": {
            "type": "string",
            "enum": [
                "none",
                "operator_attribution",
                "airport_linked_activity",
                "movement_candidates",
                "whole_day_aircraft_metrics",
            ],
        },
        "suggestions": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
        },
    },
    "required": [
        "sql",
        "title",
        "result_kind",
        "from_date",
        "to_date",
        "chart_type",
        "chart_x",
        "chart_y",
        "chart_label",
        "caveat",
        "suggestions",
    ],
}


class SemanticQueryRejected(ValueError):
    """Raised when generated SQL falls outside the read-only semantic contract."""


class SemanticQueryExecutionError(RuntimeError):
    """Raised when a validated semantic query cannot execute safely."""


@dataclass(frozen=True)
class SemanticQueryPlan:
    sql: str
    title: str
    result_kind: str
    from_date: date | None
    to_date: date | None
    chart_type: str
    chart_x: str | None
    chart_y: str | None
    chart_label: str | None
    caveat: str
    suggestions: tuple[str, ...]

    def public_json(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("sql")
        value["from_date"] = self.from_date.isoformat() if self.from_date else None
        value["to_date"] = self.to_date.isoformat() if self.to_date else None
        value["suggestions"] = list(self.suggestions)
        return value


@dataclass(frozen=True)
class SemanticQueryResult:
    sql: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    planned_cost: float
    execution_ms: int


class SemanticSQLPlanner(Protocol):
    @property
    def configured(self) -> bool: ...

    @property
    def model(self) -> str: ...

    def plan(
        self,
        question: str,
        *,
        availability: dict[str, Any],
        context: list[str],
    ) -> dict[str, Any] | PlannerDecision: ...


class OpenAISemanticSQLPlanner:
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        timeout_seconds: float = PLANNER_TIMEOUT_SECONDS,
        session: Any = requests,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()
        self._model = (model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL).strip()
        self.timeout_seconds = timeout_seconds
        self.session = session

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def model(self) -> str:
        return self._model

    def plan(
        self,
        question: str,
        *,
        availability: dict[str, Any],
        context: list[str],
    ) -> PlannerDecision:
        return self._request(
            question,
            availability=availability,
            context=context,
            correction=None,
        )

    def repair(
        self,
        question: str,
        *,
        availability: dict[str, Any],
        context: list[str],
        previous_plan: dict[str, Any],
        error: str,
    ) -> PlannerDecision:
        correction = (
            "The previous SQL plan was rejected. Return a corrected complete plan.\n"
            f"REJECTED PLAN: {json.dumps(previous_plan, default=str)}\n"
            f"SERVER ERROR: {error[:800]}"
        )
        return self._request(
            question,
            availability=availability,
            context=context,
            correction=correction,
        )

    def _request(
        self,
        question: str,
        *,
        availability: dict[str, Any],
        context: list[str],
        correction: str | None,
    ) -> PlannerDecision:
        if not self.configured:
            raise QueryPlannerUnavailable(
                "Natural-language queries need OPENAI_API_KEY in the server environment."
            )
        instructions = (
            "You write one PostgreSQL SELECT query for a local aviation analytics app. "
            "Use only the supplied nl_* semantic relations. Never use base tables, schema "
            "qualification, DML, DDL, comments, semicolons, recursive CTEs, system functions, "
            "or current_date. Return strict structured output, not prose. Always cap result "
            f"rows at {MAX_RESULT_ROWS}. Resolve relative dates against latest_available_date. "
            "For activity questions set from_date and to_date and filter utc_date in SQL. "
            "Never shrink an explicitly requested calendar period to locally available dates; "
            "the server reports missing coverage separately. Use nl_region_activity for activity "
            "in a world region and nl_country_activity for activity in a country. Do not use "
            "operator home geography as aircraft activity geography. If the user asks to show "
            "an operator if available, select the nullable operator but do not filter or inner "
            "join on operator evidence. "
            "For three-letter IATA airport codes filter nl_airport_activity.airport, not "
            "airport_ident. "
            "For reference-only company/approval questions set both dates null. Select concise, "
            "human-readable output columns. Choose chart columns that are actually selected. "
            "Prefer COUNT(DISTINCT address) for unique aircraft. Distinguish operator home "
            "geography from airport-linked activity geography. Preserve requested filters and "
            "grouping exactly."
        )
        context_lines = "\n".join(f"- {item}" for item in context[-MAX_CONTEXT_QUESTIONS:])
        input_text = (
            f"SEMANTIC SCHEMA\n{SEMANTIC_SCHEMA}\n\n"
            "LOCAL DATA AVAILABILITY\n"
            f"{json.dumps(availability, separators=(',', ':'), default=str)}\n\n"
            f"RECENT USER QUESTIONS\n{context_lines or '- none'}\n\n"
            f"CURRENT QUESTION\n{question}"
        )
        if correction:
            input_text += f"\n\nCORRECTION REQUIRED\n{correction}"
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "heligent_semantic_sql_plan",
                    "strict": True,
                    "schema": SQL_PLAN_SCHEMA,
                }
            },
            "store": False,
            "max_output_tokens": 1_600,
        }
        try:
            response = self.session.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise QueryPlannerUnavailable(
                "The OpenAI SQL planner could not be reached. Try the question again."
            ) from exc
        if not response.ok:
            message = "The OpenAI SQL planner rejected the request."
            try:
                detail = response.json().get("error", {}).get("message")
                if detail:
                    message = f"{message} {detail}"
            except (AttributeError, ValueError):
                pass
            raise QueryPlannerUnavailable(message)
        try:
            payload = response.json()
            output_text = next(
                content["text"]
                for output in payload.get("output", [])
                if output.get("type") == "message"
                for content in output.get("content", [])
                if content.get("type") == "output_text"
            )
            plan = json.loads(output_text)
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            raise QueryPlannerUnavailable(
                "The OpenAI SQL planner returned an unreadable plan."
            ) from exc
        if not isinstance(plan, dict):
            raise QueryPlannerUnavailable("The OpenAI SQL planner returned an invalid plan.")
        headers = getattr(response, "headers", {})
        return PlannerDecision(
            plan=plan,
            trace={
                "response_id": payload.get("id"),
                "request_id": headers.get("x-request-id") if hasattr(headers, "get") else None,
                "model": payload.get("model", self.model),
                "status": payload.get("status"),
                "usage": payload.get("usage"),
                "repair": correction is not None,
            },
        )


def validate_semantic_plan(
    raw: dict[str, Any],
    *,
    latest_available: date | str | None,
) -> SemanticQueryPlan:
    if not isinstance(raw, dict):
        raise SemanticQueryRejected("The SQL plan must be an object")
    allowed = set(SQL_PLAN_SCHEMA["properties"])
    if set(raw) - allowed:
        raise SemanticQueryRejected("The SQL planner returned unsupported fields")
    sql = raw.get("sql")
    title = raw.get("title")
    if not isinstance(sql, str) or not sql.strip():
        raise SemanticQueryRejected("The SQL planner did not return a query")
    if not isinstance(title, str) or not title.strip() or len(title) > 180:
        raise SemanticQueryRejected("The SQL result title is invalid")
    result_kind = raw.get("result_kind")
    if result_kind not in {"ranking", "time_series", "table", "single_value"}:
        raise SemanticQueryRejected("The SQL result kind is invalid")

    def parse_plan_date(field: str) -> date | None:
        value = raw.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise SemanticQueryRejected(f"{field} must use YYYY-MM-DD")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise SemanticQueryRejected(f"{field} must use YYYY-MM-DD") from exc

    from_date = parse_plan_date("from_date")
    to_date = parse_plan_date("to_date")
    if (from_date is None) != (to_date is None):
        raise SemanticQueryRejected("Activity plans require both from_date and to_date")
    if from_date and to_date:
        if to_date < from_date:
            raise SemanticQueryRejected("The query date range is reversed")
        if (to_date - from_date).days + 1 > MAX_ANALYTICS_DAYS:
            raise SemanticQueryRejected(
                f"Natural-language periods are capped at {MAX_ANALYTICS_DAYS} days"
            )
        latest = (
            date.fromisoformat(latest_available)
            if isinstance(latest_available, str)
            else latest_available
        )
        if latest and from_date > latest:
            raise SemanticQueryRejected("The requested period begins after local data availability")
    chart_type = raw.get("chart_type")
    if chart_type not in {"none", "line", "bar", "doughnut"}:
        raise SemanticQueryRejected("The SQL chart type is invalid")
    chart_x = _nullable_identifier(raw.get("chart_x"), "chart_x")
    chart_y = _nullable_identifier(raw.get("chart_y"), "chart_y")
    chart_label = raw.get("chart_label")
    if chart_label is not None and (not isinstance(chart_label, str) or len(chart_label) > 100):
        raise SemanticQueryRejected("The SQL chart label is invalid")
    if chart_type != "none" and (chart_x is None or chart_y is None):
        raise SemanticQueryRejected("Charts require selected x and y columns")
    caveat = raw.get("caveat")
    if caveat not in {
        "none",
        "operator_attribution",
        "airport_linked_activity",
        "movement_candidates",
        "whole_day_aircraft_metrics",
    }:
        raise SemanticQueryRejected("The SQL caveat is invalid")
    suggestions_value = raw.get("suggestions")
    if not isinstance(suggestions_value, list) or len(suggestions_value) > 3:
        raise SemanticQueryRejected("SQL suggestions must be a short list")
    suggestions = tuple(
        " ".join(item.split())[:180]
        for item in suggestions_value
        if isinstance(item, str) and item.strip()
    )
    return SemanticQueryPlan(
        sql=sql.strip(),
        title=" ".join(title.split()),
        result_kind=result_kind,
        from_date=from_date,
        to_date=to_date,
        chart_type=chart_type,
        chart_x=chart_x,
        chart_y=chart_y,
        chart_label=chart_label,
        caveat=caveat,
        suggestions=suggestions,
    )


def _nullable_identifier(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", value):
        raise SemanticQueryRejected(f"{field} must name one selected column")
    return value


def explicit_question_date_range(question: str) -> tuple[date, date] | None:
    normalized = " ".join(question.lower().split())
    month_terms = "|".join(
        re.escape(value) for value in sorted(MONTH_NUMBERS, key=len, reverse=True)
    )
    match = re.search(
        rf"\b(?P<month>{month_terms})\s+(?P<year>20\d{{2}})\b",
        normalized,
    ) or re.search(
        rf"\b(?P<year>20\d{{2}})\s+(?P<month>{month_terms})\b",
        normalized,
    )
    if match:
        year = int(match.group("year"))
        month = MONTH_NUMBERS[match.group("month")]
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    iso_month = re.search(
        r"\b(?P<year>20\d{2})-(?P<month>0[1-9]|1[0-2])(?!-\d{2})\b",
        normalized,
    )
    if iso_month:
        year = int(iso_month.group("year"))
        month = int(iso_month.group("month"))
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    return None


def validate_question_semantics(
    plan: SemanticQueryPlan,
    question: str,
    *,
    required_date_range: tuple[date, date] | None,
) -> None:
    try:
        expression = sqlglot.parse_one(plan.sql, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise SemanticQueryRejected("Generated SQL could not be parsed") from exc
    relations = {
        table.name.lower()
        for table in expression.find_all(exp.Table)
    }
    activity_relations = relations.intersection(
        {
            "nl_aircraft_activity",
            "nl_airport_activity",
            "nl_country_activity",
            "nl_region_activity",
        }
    )
    issues: list[str] = []
    if activity_relations and required_date_range is not None:
        if (plan.from_date, plan.to_date) != required_date_range:
            required_from, required_to = required_date_range
            issues.append(
                "The explicit requested period must remain "
                f"{required_from.isoformat()} through {required_to.isoformat()}; "
                "do not shrink it to locally available dates"
            )

    normalized_question = " ".join(question.lower().split())
    where_columns = {
        column.name.lower()
        for where in expression.find_all(exp.Where)
        for column in where.find_all(exp.Column)
    }
    optional_operator = bool(
        re.search(
            r"\boperator\b.{0,40}\bif\s+(?:it\s+is\s+)?(?:available|known)\b"
            r"|\bif\s+(?:it\s+is\s+)?(?:available|known)\b.{0,40}\boperator\b",
            normalized_question,
        )
    )
    if optional_operator and activity_relations:
        selected_columns = {
            item.alias_or_name.lower()
            for item in expression.selects
            if item.alias_or_name
        }
        if "operator" not in selected_columns:
            issues.append(
                "The user requested operator in the result; select it as operator"
            )
        if where_columns.intersection(
            {"operator", "operator_country", "operator_region", "operator_home_region"}
        ):
            issues.append(
                "Operator is optional display data here; do not filter by operator evidence"
            )
        if relations.intersection({"nl_aircraft_assignment", "nl_operator_claim"}):
            issues.append(
                "Use the nullable operator on the activity relation; do not join assignments"
            )

    has_world_region = any(term in normalized_question for term in WORLD_REGION_QUESTION_TERMS)
    explicit_operator_home = bool(
        re.search(
            r"\b(?:european|african|asian|oceanian|north american|south american)"
            r"\b.{0,32}\boperators?\b"
            r"|\boperators?\b.{0,24}\b(?:based|headquartered|registered|domiciled)\b",
            normalized_question,
        )
    )
    if activity_relations and has_world_region and not explicit_operator_home:
        if "nl_region_activity" not in relations or "activity_region" not in where_columns:
            issues.append(
                "Activity in a world region must use nl_region_activity and filter "
                "activity_region; operator home region is not activity geography"
            )
    if issues:
        raise SemanticQueryRejected("; ".join(issues))


def validate_and_bound_sql(
    sql: str,
    *,
    activity_dates_required: bool,
    from_date: date | None = None,
    to_date: date | None = None,
) -> str:
    if len(sql) > MAX_SQL_LENGTH:
        raise SemanticQueryRejected("Generated SQL is too long")
    normalized = sql.strip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    if ";" in normalized or "--" in normalized or "/*" in normalized:
        raise SemanticQueryRejected("SQL comments and multiple statements are not allowed")
    try:
        statements = sqlglot.parse(normalized, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise SemanticQueryRejected("Generated SQL could not be parsed") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise SemanticQueryRejected("Only one SELECT query is allowed")
    expression = statements[0]
    forbidden_nodes = tuple(
        node_type
        for node_type in (
            getattr(exp, "Alter", None),
            getattr(exp, "Command", None),
            getattr(exp, "Copy", None),
            getattr(exp, "Create", None),
            getattr(exp, "Delete", None),
            getattr(exp, "Drop", None),
            getattr(exp, "Insert", None),
            getattr(exp, "Into", None),
            getattr(exp, "Lock", None),
            getattr(exp, "Merge", None),
            getattr(exp, "Transaction", None),
            getattr(exp, "Update", None),
        )
        if node_type is not None
    )
    if any(isinstance(node, forbidden_nodes) for node in expression.walk()):
        raise SemanticQueryRejected("Only non-locking SELECT queries are allowed")
    with_clause = expression.args.get("with_")
    if with_clause is not None and with_clause.args.get("recursive"):
        raise SemanticQueryRejected("Recursive queries are not allowed")
    cte_names = {
        cte.alias_or_name.lower()
        for cte in expression.find_all(exp.CTE)
        if cte.alias_or_name
    }
    semantic_relations: set[str] = set()
    for table in expression.find_all(exp.Table):
        if table.catalog or table.db:
            raise SemanticQueryRejected("Schema-qualified relations are not allowed")
        name = table.name.lower()
        if name in cte_names:
            continue
        if name not in ALLOWED_RELATIONS:
            raise SemanticQueryRejected(f"Relation {table.name!r} is not available to NL queries")
        semantic_relations.add(name)
    if not semantic_relations:
        raise SemanticQueryRejected("The query must use the NL semantic schema")
    for function in expression.find_all(exp.Func):
        name = function.sql_name().upper()
        if name not in ALLOWED_FUNCTIONS:
            raise SemanticQueryRejected(f"SQL function {name!r} is not allowed")
    activity_relations = semantic_relations.intersection(
        {
            "nl_aircraft_activity",
            "nl_airport_activity",
            "nl_country_activity",
            "nl_region_activity",
        }
    )
    if activity_relations:
        if not activity_dates_required or from_date is None or to_date is None:
            raise SemanticQueryRejected("Activity SQL requires an explicit bounded date range")
        has_date_filter = any(
            column.name.lower() == "utc_date"
            for where in expression.find_all(exp.Where)
            for column in where.find_all(exp.Column)
        )
        if not has_date_filter:
            raise SemanticQueryRejected("Activity SQL must filter utc_date in a WHERE clause")
        if from_date.isoformat() not in normalized or to_date.isoformat() not in normalized:
            raise SemanticQueryRejected("SQL dates must match the validated plan dates")
    elif activity_dates_required:
        raise SemanticQueryRejected("Dated plans must query an activity relation")
    if expression.args.get("offset") is not None:
        raise SemanticQueryRejected("Result offsets are not allowed")
    _canonicalize_airport_code_filters(expression, semantic_relations)
    _canonicalize_aircraft_type_literals(expression)
    limit = expression.args.get("limit")
    if limit is None:
        expression = expression.limit(MAX_RESULT_ROWS)
    else:
        limit_expression = limit.expression
        if not isinstance(limit_expression, exp.Literal) or not limit_expression.is_int:
            raise SemanticQueryRejected("The result limit must be a literal integer")
        if int(limit_expression.this) < 1:
            raise SemanticQueryRejected("The result limit must be positive")
        if int(limit_expression.this) > MAX_RESULT_ROWS:
            expression.set("limit", exp.Limit(expression=exp.Literal.number(MAX_RESULT_ROWS)))
    return expression.sql(dialect="postgres")


def _canonicalize_aircraft_type_literals(expression: exp.Expression) -> None:
    """Rewrite common model names to the ICAO designators stored by ADS-B."""

    aircraft_type_columns = {"type_code", "aircraft_type_code"}

    def references_aircraft_type(node: exp.Expression) -> bool:
        return any(
            column.name.lower() in aircraft_type_columns
            for column in node.find_all(exp.Column)
        ) or (
            isinstance(node, exp.Column)
            and node.name.lower() in aircraft_type_columns
        )

    def canonical_literal(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Literal) or not node.is_string:
            return node
        canonical = resolve_aircraft_type_code(node.this)
        if canonical is None or canonical == node.this:
            return node
        return exp.Literal.string(canonical)

    for comparison in expression.find_all(exp.EQ):
        left = comparison.this
        right = comparison.expression
        if references_aircraft_type(left):
            comparison.set("expression", canonical_literal(right))
        elif references_aircraft_type(right):
            comparison.set("this", canonical_literal(left))

    for predicate in expression.find_all(exp.In):
        if not references_aircraft_type(predicate.this):
            continue
        predicate.set(
            "expressions",
            [canonical_literal(item) for item in predicate.expressions],
        )


def _canonicalize_airport_code_filters(
    expression: exp.Expression,
    semantic_relations: set[str],
) -> None:
    """Treat three-letter airport-ident filters as user-facing airport codes."""

    if "nl_airport_activity" not in semantic_relations:
        return

    def three_letter_code(node: exp.Expression) -> str | None:
        if not isinstance(node, exp.Literal) or not node.is_string:
            return None
        value = node.this.strip().upper()
        return value if re.fullmatch(r"[A-Z0-9]{3}", value) else None

    def airport_ident_columns(node: exp.Expression) -> list[exp.Column]:
        columns = list(node.find_all(exp.Column))
        if isinstance(node, exp.Column):
            columns.append(node)
        return [column for column in columns if column.name.lower() == "airport_ident"]

    def rename_to_airport(columns: list[exp.Column]) -> None:
        for column in columns:
            column.set("this", exp.Identifier(this="airport", quoted=False))

    for comparison in expression.find_all(exp.EQ):
        left = comparison.this
        right = comparison.expression
        left_columns = airport_ident_columns(left)
        right_columns = airport_ident_columns(right)
        right_code = three_letter_code(right)
        left_code = three_letter_code(left)
        if left_columns and right_code is not None:
            rename_to_airport(left_columns)
            comparison.set("expression", exp.Literal.string(right_code))
        elif right_columns and left_code is not None:
            rename_to_airport(right_columns)
            comparison.set("this", exp.Literal.string(left_code))

    for predicate in expression.find_all(exp.In):
        columns = airport_ident_columns(predicate.this)
        codes = [three_letter_code(item) for item in predicate.expressions]
        if not columns or not codes or any(code is None for code in codes):
            continue
        rename_to_airport(columns)
        predicate.set(
            "expressions",
            [exp.Literal.string(code) for code in codes if code is not None],
        )


class SemanticSQLExecutor:
    def __init__(
        self,
        analytics: AnalyticsStore,
        *,
        statement_timeout_ms: int = STATEMENT_TIMEOUT_MS,
        max_planned_cost: float = MAX_PLANNED_COST,
    ) -> None:
        self.analytics = analytics
        self.statement_timeout_ms = statement_timeout_ms
        self.max_planned_cost = max_planned_cost

    def execute(self, plan: SemanticQueryPlan) -> SemanticQueryResult:
        sql = validate_and_bound_sql(
            plan.sql,
            activity_dates_required=plan.from_date is not None,
            from_date=plan.from_date,
            to_date=plan.to_date,
        )
        started = time.monotonic()
        try:
            with self.analytics.store.connect() as connection:
                connection.row_factory = dict_row
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute(
                    f"SET LOCAL statement_timeout = '{int(self.statement_timeout_ms)}ms'"
                )
                connection.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'")
                explain_row = connection.execute(
                    f"EXPLAIN (FORMAT JSON) {sql}"
                ).fetchone()
                if explain_row is None:
                    raise SemanticQueryExecutionError("PostgreSQL did not return a query plan")
                explain_value = next(iter(explain_row.values()))
                planned_cost = float(explain_value[0]["Plan"]["Total Cost"])
                if planned_cost > self.max_planned_cost:
                    raise SemanticQueryRejected(
                        "The generated query is too expensive for an interactive request"
                    )
                cursor = connection.execute(sql)
                columns = tuple(item.name for item in cursor.description or ())
                rows = tuple(cursor.fetchall())
                connection.rollback()
        except SemanticQueryRejected:
            raise
        except psycopg.errors.QueryCanceled as exc:
            raise SemanticQueryExecutionError(
                "The generated query exceeded the safe database time limit"
            ) from exc
        except psycopg.Error as exc:
            raise SemanticQueryExecutionError(
                f"PostgreSQL rejected the generated query: {exc.diag.message_primary or type(exc).__name__}"
            ) from exc
        if len(rows) > MAX_RESULT_ROWS:
            raise SemanticQueryExecutionError("The generated query exceeded the row limit")
        return SemanticQueryResult(
            sql=sql,
            columns=columns,
            rows=rows,
            planned_cost=planned_cost,
            execution_ms=round((time.monotonic() - started) * 1000),
        )


class SemanticNaturalLanguageAnalytics:
    def __init__(
        self,
        analytics: AnalyticsStore,
        planner: SemanticSQLPlanner | None = None,
        *,
        debug_dir: Path = DEFAULT_QUERY_DEBUG_DIR,
        executor: SemanticSQLExecutor | None = None,
    ) -> None:
        self.analytics = analytics
        self.planner = planner or OpenAISemanticSQLPlanner()
        self.executor = executor or SemanticSQLExecutor(analytics)
        self.debug_dir = debug_dir

    @property
    def configured(self) -> bool:
        return self.planner.configured

    @property
    def model(self) -> str:
        return self.planner.model

    def config(self) -> dict[str, Any]:
        availability = self.analytics.availability()
        return {
            "configured": self.configured,
            "model": self.model,
            "availability": availability,
            "examples": [
                "Rank known operators by unique aircraft seen in the last 30 days.",
                "Show ABZ helicopter traffic by day for August 2026.",
                "Which S92s were busiest at European airports in August 2026?",
                "Which UK Part 145 MROs are approved for EC135 helicopters?",
                "Compare helicopter movements at ABZ and LSI by day.",
            ],
        }

    def ask(
        self,
        question: str,
        *,
        context: list[str] | None = None,
        debug: bool = False,
    ) -> dict[str, Any]:
        normalized_question = " ".join(question.split()) if isinstance(question, str) else ""
        if len(normalized_question) < 4:
            raise ValueError("Ask a slightly more specific aviation question")
        if len(normalized_question) > MAX_QUESTION_LENGTH:
            raise ValueError(f"Questions are capped at {MAX_QUESTION_LENGTH} characters")
        clean_context = [
            " ".join(item.split())[:MAX_QUESTION_LENGTH]
            for item in (context or [])
            if isinstance(item, str) and item.strip()
        ][-MAX_CONTEXT_QUESTIONS:]
        availability = self.analytics.availability()
        required_date_range = explicit_question_date_range(normalized_question)
        planner_availability = dict(availability)
        if required_date_range is not None:
            planner_availability["explicit_question_date_range"] = {
                "from_date": required_date_range[0].isoformat(),
                "to_date": required_date_range[1].isoformat(),
                "instruction": "Preserve this full range even when local coverage is partial",
            }
        if not availability.get("latest_available_date") and not (
            availability.get("company_data") or {}
        ).get("companies"):
            return {
                "question": normalized_question,
                "coverage": _coverage(availability, None, None),
                "answer": "No local ADS-B or company reference data is available yet.",
                "table": {"columns": [], "rows": []},
                "chart": None,
                "suggestions": [],
                "meta": {"planner": "semantic SQL", "model": self.model},
            }

        planner_traces: list[dict[str, Any]] = []
        raw_plan: dict[str, Any] | None = None
        plan: SemanticQueryPlan | None = None
        execution: SemanticQueryResult | None = None
        failure: Exception | None = None
        for attempt in range(2):
            if attempt == 0:
                planner_output = self.planner.plan(
                    normalized_question,
                    availability=planner_availability,
                    context=clean_context,
                )
            else:
                repair = getattr(self.planner, "repair", None)
                if repair is None or raw_plan is None or failure is None:
                    break
                planner_output = repair(
                    normalized_question,
                    availability=planner_availability,
                    context=clean_context,
                    previous_plan=raw_plan,
                    error=str(failure),
                )
            if isinstance(planner_output, PlannerDecision):
                raw_plan = planner_output.plan
                planner_traces.append(planner_output.trace)
            else:
                raw_plan = planner_output
                planner_traces.append({"model": self.model, "source": "injected planner"})
            try:
                plan = validate_semantic_plan(
                    raw_plan,
                    latest_available=availability.get("latest_available_date"),
                )
                validate_question_semantics(
                    plan,
                    normalized_question,
                    required_date_range=required_date_range,
                )
                execution = self.executor.execute(plan)
                failure = None
                break
            except (SemanticQueryRejected, SemanticQueryExecutionError) as exc:
                failure = exc
        if plan is None or execution is None:
            message = (
                "The generated analytics query could not be validated safely. "
                "Try rephrasing it."
            )
            if debug and raw_plan is not None and failure is not None:
                failure_dump = self._write_failure_debug_dump(
                    question=normalized_question,
                    context=clean_context,
                    availability=planner_availability,
                    raw_plan=raw_plan,
                    plan=plan,
                    planner_traces=planner_traces,
                    failure=failure,
                )
                message += f" Debug saved to {failure_dump['path']}."
            raise QueryPlannerUnavailable(message) from failure

        rows = [_json_ready(dict(row)) for row in execution.rows]
        columns = list(execution.columns)
        chart = _build_chart(plan, columns, rows)
        coverage = _coverage(availability, plan.from_date, plan.to_date)
        answer = _build_answer(plan, rows, coverage)
        result = {
            "question": normalized_question,
            "plan": plan.public_json(),
            "coverage": coverage,
            "answer": answer,
            "table": {"columns": columns, "rows": rows},
            "chart": chart,
            "suggestions": list(plan.suggestions),
            "meta": {
                "planner": "OpenAI schema-aware SQL",
                "model": self.model,
                "execution": "validated read-only semantic SQL",
                "execution_ms": execution.execution_ms,
            },
        }
        if debug:
            result["debug_dump"] = self._write_debug_dump(
                question=normalized_question,
                context=clean_context,
                availability=availability,
                raw_plan=raw_plan,
                plan=plan,
                planner_traces=planner_traces,
                execution=execution,
                result=result,
            )
        return result

    def _write_failure_debug_dump(
        self,
        *,
        question: str,
        context: list[str],
        availability: dict[str, Any],
        raw_plan: dict[str, Any],
        plan: SemanticQueryPlan | None,
        planner_traces: list[dict[str, Any]],
        failure: Exception,
    ) -> dict[str, str]:
        debug_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        path = self.debug_dir / f"query-{debug_id}.json"
        document = {
            "debug_version": 2,
            "debug_id": debug_id,
            "created_at": datetime.now(UTC),
            "status": "REJECTED",
            "privacy_note": (
                "Contains the question and rejected semantic SQL plan, but no API key, "
                "database URL, raw prompt, raw ADS-B positions, or database result rows."
            ),
            "question": question,
            "context": context,
            "availability": availability,
            "planner_traces": planner_traces,
            "raw_model_plan": raw_plan,
            "validated_plan": plan.public_json() if plan is not None else None,
            "rejection": {"type": type(failure).__name__, "message": str(failure)},
            "query_guard": {
                "allowed_relations": sorted(ALLOWED_RELATIONS),
                "read_only_transaction": True,
                "row_limit": MAX_RESULT_ROWS,
                "statement_timeout_ms": self.executor.statement_timeout_ms,
            },
        }
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_json_ready(document), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"id": debug_id, "path": _display_debug_path(path)}

    def _write_debug_dump(
        self,
        *,
        question: str,
        context: list[str],
        availability: dict[str, Any],
        raw_plan: dict[str, Any],
        plan: SemanticQueryPlan,
        planner_traces: list[dict[str, Any]],
        execution: SemanticQueryResult,
        result: dict[str, Any],
    ) -> dict[str, str]:
        debug_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        path = self.debug_dir / f"query-{debug_id}.json"
        document = {
            "debug_version": 2,
            "debug_id": debug_id,
            "created_at": datetime.now(UTC),
            "privacy_note": (
                "Contains the question, generated semantic SQL, and derived local results, "
                "but no API key, database URL, raw prompt, or raw ADS-B positions."
            ),
            "question": question,
            "context": context,
            "availability": availability,
            "planner_traces": planner_traces,
            "raw_model_plan": raw_plan,
            "validated_plan": plan.public_json(),
            "validated_sql": execution.sql,
            "query_guard": {
                "allowed_relations": sorted(ALLOWED_RELATIONS),
                "read_only_transaction": True,
                "row_limit": MAX_RESULT_ROWS,
                "statement_timeout_ms": self.executor.statement_timeout_ms,
                "planned_cost": execution.planned_cost,
                "execution_ms": execution.execution_ms,
            },
            "candidate_rows": {"source_row_count": len(execution.rows), "rows": execution.rows},
            "final_response": {key: value for key, value in result.items() if key != "debug_dump"},
        }
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_json_ready(document), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"id": debug_id, "path": _display_debug_path(path)}


def _coverage(
    availability: dict[str, Any],
    from_date: date | None,
    to_date: date | None,
) -> dict[str, Any]:
    if from_date is None or to_date is None:
        company_data = availability.get("company_data") or {}
        return {
            "requested_days": 0,
            "available_days": 0,
            "complete": True,
            "available_dates": [],
            "missing_dates": [],
            "message": (
                "Searched the imported reference schema covering "
                f"{int(company_data.get('companies') or 0):,} companies."
            ),
        }
    available_dates = [
        value if isinstance(value, date) else date.fromisoformat(value)
        for value in availability.get("available_dates") or []
        if from_date <= (value if isinstance(value, date) else date.fromisoformat(value)) <= to_date
    ]
    available_set = set(available_dates)
    requested_days = (to_date - from_date).days + 1
    requested_dates = [
        from_date.fromordinal(from_date.toordinal() + offset)
        for offset in range(requested_days)
    ]
    missing_dates = [value for value in requested_dates if value not in available_set]
    return {
        "requested_days": requested_days,
        "available_days": len(available_dates),
        "complete": not missing_dates,
        "available_dates": available_dates,
        "missing_dates": missing_dates,
        "message": (
            f"All {requested_days} requested UTC days are available locally."
            if not missing_dates
            else f"Only {len(available_dates)} of {requested_days} requested UTC days are available locally."
        ),
    }


def _build_chart(
    plan: SemanticQueryPlan,
    columns: list[str],
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if plan.chart_type == "none" or not rows:
        return None
    if plan.chart_x not in columns or plan.chart_y not in columns:
        return None
    return {
        "type": plan.chart_type,
        "x": plan.chart_x,
        "y": plan.chart_y,
        "label": plan.chart_label or plan.chart_y.replace("_", " ").title(),
    }


def _build_answer(
    plan: SemanticQueryPlan,
    rows: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> str:
    if not rows:
        answer = f"No matching results were found for {plan.title.lower()}."
    elif plan.result_kind == "ranking":
        top = rows[0]
        label_column = plan.chart_x or next(iter(top))
        value_column = plan.chart_y or next(
            (key for key in top if key != label_column),
            label_column,
        )
        answer = (
            f"{plan.title}. {top.get(label_column)} ranks first with "
            f"{_format_value(top.get(value_column))}."
        )
    elif plan.result_kind == "time_series" and plan.chart_y:
        values = [
            float(row[plan.chart_y])
            for row in rows
            if isinstance(row.get(plan.chart_y), (int, float, Decimal))
        ]
        answer = (
            f"{plan.title}. Returned {len(rows)} daily rows"
            + (
                f", ranging from {_format_value(min(values))} to {_format_value(max(values))}."
                if values
                else "."
            )
        )
    elif plan.result_kind == "single_value":
        answer = f"{plan.title}: " + ", ".join(
            f"{key.replace('_', ' ')} {_format_value(value)}"
            for key, value in rows[0].items()
        ) + "."
    else:
        answer = f"{plan.title}. Found {len(rows):,} matching row{'s' if len(rows) != 1 else ''}."
    caveats = {
        "operator_attribution": (
            " Operator names are sourced attribution evidence, not proof of operational "
            "control on every flight."
        ),
        "airport_linked_activity": (
            " Geography is based on linked airport evidence rather than exact airspace boundaries."
        ),
        "movement_candidates": (
            " Movements are conservative ADS-B-derived candidates, not certified movement records."
        ),
        "whole_day_aircraft_metrics": (
            " Aircraft-day hours are whole-day activity for airport-linked aircraft, not exact "
            "hours inside the selected airport or region."
        ),
    }
    answer += caveats.get(plan.caveat, "")
    if not coverage.get("complete"):
        answer += " This is a partial result because some requested days are not local."
    return answer


def _format_value(value: Any) -> str:
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        return f"{value:,.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _display_debug_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)
