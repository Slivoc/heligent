from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import requests

from .aircraft_types import resolve_aircraft_type_code
from .analytics import (
    AnalyticsStore,
    MAX_ANALYTICS_DAYS,
    normalize_world_region_query,
)


DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
MAX_QUESTION_LENGTH = 600
MAX_CONTEXT_QUESTIONS = 4
TYPE_CODE_RE = re.compile(r"^[A-Z0-9-]{1,16}$")
PLURAL_TYPE_IN_QUESTION_RE = re.compile(
    r"\b([A-Z][A-Z0-9-]{1,15})(?:['’]s|s)\b",
    re.IGNORECASE,
)
AIRPORT_CODE_RE = re.compile(r"^[A-Z0-9]{3,4}$")
AIRPORT_IN_QUESTION_RE = re.compile(
    r"\b(?:at|from|to|through|into|out\s+of)\s+([A-Z0-9]{3,4})\b",
    re.IGNORECASE,
)
APPROVAL_NUMBER_RE = re.compile(r"\b(?:UK|EASA)\.145\.[A-Z0-9.-]+\b", re.IGNORECASE)
REGISTRATION_IN_QUESTION_RE = re.compile(
    r"\b(?:[A-Z0-9]{1,3}-[A-Z0-9]{2,6}|N[0-9]{1,5}[A-Z]{0,2})\b",
    re.IGNORECASE,
)
PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_QUERY_DEBUG_DIR = PROJECT_ROOT / "reports" / "query-debug"
ALL_AVAILABLE_RE = re.compile(
    r"\b(?:whole|entire)\s+(?:database|data(?:base)?|history|dataset)\b"
    r"|\b(?:all|every)\s+(?:available\s+)?(?:day|date|history|data)\b",
    re.IGNORECASE,
)
WORLD_REGION_IN_QUESTION_RE = re.compile(
    r"\b(north\s+american?|south\s+american?|europe(?:an)?|african?|asian?|"
    r"oceanian?|oceania|americas)\b",
    re.IGNORECASE,
)

OPERATION_METRICS: dict[str, tuple[str, ...]] = {
    "daily_activity": (
        "unique_aircraft",
        "observations",
        "active_hours",
        "airborne_hours",
        "ground_active_hours",
        "airport_linked_aircraft",
    ),
    "type_breakdown": (
        "unique_aircraft",
        "observations",
        "active_hours",
        "airborne_hours",
        "ground_active_hours",
    ),
    "hub_ranking": (
        "unique_aircraft",
        "primary_aircraft",
        "movement_candidates",
        "arrival_candidates",
        "departure_candidates",
        "ground_active_hours",
        "ground_observations",
        "primary_tail_active_hours",
        "primary_tail_airborne_hours",
    ),
    "tail_ranking": (
        "active_hours",
        "airborne_hours",
        "ground_active_hours",
        "observations",
        "active_days",
    ),
    "operator_tail_activity": (
        "active_hours",
        "airborne_hours",
        "ground_active_hours",
        "observations",
        "active_days",
    ),
    "airport_aircraft": (
        "airport_movement_candidates",
        "airport_arrival_candidates",
        "airport_departure_candidates",
        "airport_ground_observations",
        "airport_ground_active_hours",
        "active_hours",
        "airborne_hours",
        "observations",
    ),
    "airport_types": (
        "unique_aircraft",
        "movement_candidates",
        "arrival_candidates",
        "departure_candidates",
        "ground_observations",
        "ground_active_hours",
        "active_hours",
        "airborne_hours",
        "observations",
    ),
    "helicopter_types": (
        "unique_aircraft",
        "observations",
        "active_hours",
        "airborne_hours",
    ),
    "totals": (
        "unique_aircraft",
        "observations",
        "active_hours",
        "airborne_hours",
        "ground_active_hours",
        "known_type_codes",
        "airport_linked_aircraft",
    ),
    "company_directory": (
        "capability_count",
        "site_count",
        "valid_approval_count",
        "assigned_aircraft_count",
    ),
    "company_approvals": ("capability_count",),
    "company_capabilities": ("matching_capabilities",),
    "operator_aircraft": ("assigned_aircraft_count",),
}

REFERENCE_OPERATIONS = {
    "company_directory",
    "company_approvals",
    "company_capabilities",
    "operator_aircraft",
}

METRIC_LABELS = {
    "unique_aircraft": "Unique aircraft",
    "observations": "Observations",
    "active_hours": "Active hours",
    "airborne_hours": "Airborne hours",
    "ground_active_hours": "Ground-active hours",
    "airport_linked_aircraft": "Hub-linked aircraft",
    "primary_aircraft": "Primary aircraft",
    "movement_candidates": "Movement candidates",
    "arrival_candidates": "Arrival candidates",
    "departure_candidates": "Departure candidates",
    "airport_movement_candidates": "Airport movement candidates",
    "airport_arrival_candidates": "Airport arrival candidates",
    "airport_departure_candidates": "Airport departure candidates",
    "ground_observations": "Ground observations",
    "airport_ground_observations": "Airport ground observations",
    "airport_ground_active_hours": "Airport ground-active hours",
    "primary_tail_active_hours": "Primary-tail active hours",
    "primary_tail_airborne_hours": "Primary-tail airborne hours",
    "active_days": "Active days",
    "known_type_codes": "Known type codes",
    "capability_count": "Approval capabilities",
    "matching_capabilities": "Matching approval scopes",
    "site_count": "Sites",
    "valid_approval_count": "Valid approvals",
    "assigned_aircraft_count": "Assigned aircraft",
}

QUERY_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "operation": {"type": "string", "enum": list(OPERATION_METRICS)},
        "metric": {
            "type": "string",
            "enum": sorted({metric for values in OPERATION_METRICS.values() for metric in values}),
        },
        "date_scope": {"type": "string", "enum": ["range", "all_available", "reference"]},
        "from_date": {"type": ["string", "null"]},
        "to_date": {"type": ["string", "null"]},
        "type_code": {"type": ["string", "null"]},
        "airport_code": {"type": ["string", "null"]},
        "company_query": {"type": ["string", "null"]},
        "capability_query": {"type": ["string", "null"]},
        "location_query": {"type": ["string", "null"]},
        "region_query": {"type": ["string", "null"]},
        "approval_query": {"type": ["string", "null"]},
        "registration_query": {"type": ["string", "null"]},
        "company_role": {"type": "string", "enum": ["operator", "mro", "either"]},
        "assignment_role": {
            "type": "string",
            "enum": ["OPERATOR", "OWNER", "MANAGER", "AOC_AUTHORIZED", "OTHER", "ANY"],
        },
        "approval_status": {
            "type": "string",
            "enum": ["VALID", "PENDING", "SUSPENDED", "REVOKED", "EXPIRED", "UNKNOWN", "ANY"],
        },
        "capability_kind": {
            "type": "string",
            "enum": ["AIRCRAFT", "ENGINE", "COMPONENT", "SPECIALIST", "SERVICE", "OTHER", "ANY"],
        },
        "helicopters_only": {"type": "boolean"},
        "operator_grouping": {"type": "string", "enum": ["tail", "operator"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "chart": {
            "type": "string",
            "enum": ["auto", "line", "bar", "doughnut", "table"],
        },
    },
    "required": [
        "operation",
        "metric",
        "date_scope",
        "from_date",
        "to_date",
        "type_code",
        "airport_code",
        "company_query",
        "capability_query",
        "location_query",
        "region_query",
        "approval_query",
        "registration_query",
        "company_role",
        "assignment_role",
        "approval_status",
        "capability_kind",
        "helicopters_only",
        "operator_grouping",
        "limit",
        "chart",
    ],
}


class QueryPlannerUnavailable(RuntimeError):
    """Raised when the configured model cannot create a query plan."""


class QueryPlanner(Protocol):
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


@dataclass(frozen=True)
class PlannerDecision:
    plan: dict[str, Any]
    trace: dict[str, Any]


@dataclass(frozen=True)
class QueryPlan:
    operation: str
    metric: str
    date_scope: str
    from_date: date | None
    to_date: date | None
    type_code: str | None
    airport_code: str | None
    company_query: str | None
    capability_query: str | None
    location_query: str | None
    region_query: str | None
    approval_query: str | None
    registration_query: str | None
    company_role: str
    assignment_role: str
    approval_status: str
    capability_kind: str
    helicopters_only: bool
    operator_grouping: str
    limit: int
    chart: str

    def as_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["from_date"] = self.from_date.isoformat() if self.from_date else None
        value["to_date"] = self.to_date.isoformat() if self.to_date else None
        return value


class OpenAIQueryPlanner:
    """Translate a question into a closed, server-validated analytics plan."""

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        timeout_seconds: float = 35,
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
        if not self.configured:
            raise QueryPlannerUnavailable(
                "Natural-language queries need OPENAI_API_KEY in the server environment."
            )

        instructions = (
            "You are the query planner for a local ADS-B analytics application. Convert the "
            "user's aviation question into exactly one JSON query plan. You do not write SQL. "
            "Choose only the supplied operation and metric enums. Dates are UTC. Resolve relative "
            "Company, site, approval, and maintenance-capability operations query imported "
            "regulatory reference records, not ADS-B traffic. For those operations set "
            "date_scope=reference and both dates to null. Use company_directory to list or search "
            "operators/MROs and their locations; operator_aircraft for imported operator-to-tail "
            "assignments and authoritative registry Registered Operator claims, an operator's "
            "fleet, or who operates/owns/manages a registration; "
            "company_approvals for Part 145 certificates, "
            "approval numbers, or status; and company_capabilities for what a company may maintain "
            "or which MROs have an aircraft, engine, component, or specialist approval scope. Put "
            "company/trading names in company_query, registrations or tail numbers in "
            "registration_query, model or maintenance-scope terms such as EC135 in capability_query, "
            "place names in location_query, world regions such as Europe, Asia, Africa, North "
            "America, South America, Oceania, or the Americas in region_query, and approval "
            "identifiers in approval_query. Geographic region means the operator/company's "
            "country-derived region, not where ADS-B activity was observed. For "
            "operator_aircraft use assignment_role=OPERATOR unless the question explicitly asks "
            "for an owner, manager, or aircraft authorised on an AOC. "
            "Use operator_tail_activity when the user ranks activity for tails with imported or "
            "registry-backed operator assignments over an ADS-B date range. Put a named operator "
            "in company_query, "
            "or leave company_query null when the user asks across all registered operators. "
            "Set operator_grouping=operator when ranking operators and tail when ranking tails. "
            "Use approval_status=VALID for current 'can maintain' questions and "
            "ANY only when the user asks for all or historical statuses. Never put a maintenance "
            "capability model in traffic type_code. "
            "dates against latest_available_date, not today's date. Set date_scope=all_available "
            "when the user says whole database, entire history, all data, or all available dates; "
            "then set both dates to null. Otherwise use date_scope=range; if no period is requested, "
            "use the latest available day. Use type_code only for an explicit ICAO aircraft type code. "
            "Set helicopters_only for rotorcraft questions; helicopter_types is specifically for a "
            "ranking of classified helicopter type codes. Use airport_types when the user asks "
            "for aircraft or helicopter types at one named airport. Use airport_aircraft when the user asks "
            "which individual aircraft were seen at, arrived at, or departed from one named airport; "
            "put its IATA or ICAO code in airport_code. Airport arrivals and departures are conservative "
            "movement candidates inferred during ingestion from explicit ground observations and low, "
            "slow trace endpoints that travel outside the airport zone; they are not certified movement "
            "records. Prefer hub_ranking for comparisons between airports/hubs, "
            "tail_ranking for registrations or individual aircraft, type_breakdown for aircraft types, "
            "daily_activity for trends over time, and totals for a single aggregate. For generic hub "
            "traffic, busiest-hub, or 'seen the most traffic' questions use unique_aircraft. Use "
            "primary_aircraft only when the user explicitly asks where aircraft are primarily based; "
            "ground observations only when explicitly requested because they are sampling-dependent. "
            "Never infer that "
            "missing days are available. Prefer chart=auto unless the user explicitly requests a table "
            "or a supported chart. Return only the strict structured result."
        )
        context_lines = "\n".join(f"- {item}" for item in context[-MAX_CONTEXT_QUESTIONS:])
        input_text = (
            "LOCAL DATA AVAILABILITY\n"
            f"{json.dumps(availability, separators=(',', ':'), default=str)}\n\n"
            "ALLOWED OPERATIONS AND METRICS\n"
            f"{json.dumps(OPERATION_METRICS, separators=(',', ':'))}\n\n"
            f"RECENT USER QUESTIONS\n{context_lines or '- none'}\n\n"
            f"CURRENT QUESTION\n{question}"
        )
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "heligent_query_plan",
                    "strict": True,
                    "schema": QUERY_PLAN_SCHEMA,
                }
            },
            "store": False,
            "max_output_tokens": 650,
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
                "The OpenAI query planner could not be reached. Try the question again."
            ) from exc

        if not response.ok:
            message = "The OpenAI query planner rejected the request."
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
                "The OpenAI query planner returned an unreadable plan. Try rephrasing the question."
            ) from exc
        if not isinstance(plan, dict):
            raise QueryPlannerUnavailable("The OpenAI query planner returned an invalid plan.")
        headers = getattr(response, "headers", {})
        return PlannerDecision(
            plan=plan,
            trace={
                "response_id": payload.get("id"),
                "request_id": headers.get("x-request-id") if hasattr(headers, "get") else None,
                "model": payload.get("model", self.model),
                "status": payload.get("status"),
                "usage": payload.get("usage"),
            },
        )


class NaturalLanguageAnalytics:
    def __init__(
        self,
        analytics: AnalyticsStore,
        planner: QueryPlanner | None = None,
        *,
        debug_dir: Path = DEFAULT_QUERY_DEBUG_DIR,
    ) -> None:
        self.analytics = analytics
        self.planner = planner or OpenAIQueryPlanner()
        self.debug_dir = debug_dir

    @property
    def configured(self) -> bool:
        return self.planner.configured

    @property
    def model(self) -> str:
        return self.planner.model

    def config(self) -> dict[str, Any]:
        availability = self.analytics.availability()
        latest = availability.get("latest_available_date")
        examples = [
            f"Which hubs saw the most unique aircraft on {latest}?" if latest else "Which hubs are busiest?",
            f"Which tails logged the most airborne hours on {latest}?" if latest else "Which tails flew most?",
            "Which aircraft are assigned to Bristow as operator?",
            "Which UK Part 145 MROs are approved for EC135 helicopters?",
            "Show the imported maintenance organisations in Surrey.",
        ]
        return {
            "configured": self.configured,
            "model": self.model,
            "availability": availability,
            "examples": examples,
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

        clean_context = []
        for item in context or []:
            if isinstance(item, str) and item.strip():
                clean_context.append(" ".join(item.split())[:MAX_QUESTION_LENGTH])
        clean_context = clean_context[-MAX_CONTEXT_QUESTIONS:]

        availability = self.analytics.availability()
        latest = availability.get("latest_available_date")
        company_data = availability.get("company_data") or {}
        if latest is None and not company_data.get("companies"):
            return self._no_data_answer(normalized_question, availability)

        planner_output = self.planner.plan(
            normalized_question,
            availability=availability,
            context=clean_context,
        )
        if isinstance(planner_output, PlannerDecision):
            raw_plan = planner_output.plan
            planner_trace = planner_output.trace
        else:
            raw_plan = planner_output
            planner_trace = {"model": self.model, "source": "injected planner"}
        normalized_plan, adjustments = normalize_plan_for_question(
            raw_plan,
            normalized_question,
        )
        validation_latest = latest or date.today()
        earliest = availability.get("earliest_available_date") or validation_latest
        plan = validate_query_plan(
            normalized_plan,
            latest_available=validation_latest,
            earliest_available=earliest,
        )
        if plan.operation == "daily_activity" and plan.airport_code:
            assert plan.from_date is not None and plan.to_date is not None
            snapshot = self.analytics.airport_daily_activity(
                plan.from_date,
                plan.to_date,
                airport_code=plan.airport_code,
                type_code=plan.type_code,
                helicopters_only=plan.helicopters_only,
            )
        elif plan.operation == "operator_tail_activity":
            assert plan.from_date is not None and plan.to_date is not None
            snapshot = self.analytics.operator_tail_activity(
                plan.from_date,
                plan.to_date,
                company_query=plan.company_query,
                region_query=plan.region_query,
                assignment_role=plan.assignment_role,
                type_code=plan.type_code,
                helicopters_only=plan.helicopters_only,
                group_by_operator=plan.operator_grouping == "operator",
                limit=plan.limit,
            )
        elif plan.operation == "tail_ranking":
            assert plan.from_date is not None and plan.to_date is not None
            snapshot = self.analytics.tail_ranking(
                plan.from_date,
                plan.to_date,
                metric=plan.metric,
                type_code=plan.type_code,
                helicopters_only=plan.helicopters_only,
                region_query=plan.region_query,
                location_query=plan.location_query,
                limit=plan.limit,
            )
        elif plan.operation == "hub_ranking":
            assert plan.from_date is not None and plan.to_date is not None
            snapshot = self.analytics.hub_ranking(
                plan.from_date,
                plan.to_date,
                metric=plan.metric,
                type_code=plan.type_code,
                helicopters_only=plan.helicopters_only,
                region_query=plan.region_query,
                location_query=plan.location_query,
                limit=plan.limit,
            )
        elif plan.operation in REFERENCE_OPERATIONS:
            snapshot = {"coverage": _reference_coverage(company_data)}
            if plan.operation == "company_directory":
                snapshot["company_directory"] = self.analytics.company_directory(
                    company_query=plan.company_query,
                    location_query=plan.location_query,
                    region_query=plan.region_query,
                    company_role=plan.company_role,
                    metric=plan.metric,
                    limit=plan.limit,
                )
            elif plan.operation == "company_approvals":
                snapshot["company_approvals"] = self.analytics.company_approvals(
                    company_query=plan.company_query,
                    approval_query=plan.approval_query,
                    location_query=plan.location_query,
                    region_query=plan.region_query,
                    approval_status=plan.approval_status,
                    metric=plan.metric,
                    limit=plan.limit,
                )
            elif plan.operation == "operator_aircraft":
                snapshot["operator_aircraft"] = self.analytics.operator_aircraft(
                    company_query=plan.company_query,
                    registration_query=plan.registration_query,
                    region_query=plan.region_query,
                    assignment_role=plan.assignment_role,
                    limit=plan.limit,
                )
            else:
                snapshot["company_capabilities"] = self.analytics.company_capabilities(
                    company_query=plan.company_query,
                    capability_query=plan.capability_query,
                    location_query=plan.location_query,
                    region_query=plan.region_query,
                    capability_kind=plan.capability_kind,
                    approval_status=plan.approval_status,
                    limit=plan.limit,
                )
        else:
            if latest is None:
                return self._no_data_answer(normalized_question, availability)
            assert plan.from_date is not None and plan.to_date is not None
            snapshot = self.analytics.snapshot(
                plan.from_date,
                plan.to_date,
                type_code=plan.type_code,
                helicopters_only=plan.helicopters_only,
                region_query=plan.region_query,
            )
        if plan.operation == "airport_aircraft":
            assert plan.from_date is not None and plan.to_date is not None
            snapshot["airport_aircraft"] = self.analytics.airport_aircraft(
                plan.from_date,
                plan.to_date,
                airport_code=plan.airport_code or "",
                type_code=plan.type_code,
                helicopters_only=plan.helicopters_only,
                metric=plan.metric,
                limit=plan.limit,
            )
        if plan.operation == "airport_types":
            assert plan.from_date is not None and plan.to_date is not None
            snapshot["airport_types"] = self.analytics.airport_types(
                plan.from_date,
                plan.to_date,
                airport_code=plan.airport_code or "",
                type_code=plan.type_code,
                helicopters_only=plan.helicopters_only,
                metric=plan.metric,
                limit=plan.limit,
            )
        if plan.date_scope == "all_available":
            snapshot["coverage"] = _all_available_coverage(availability)
        table, chart = build_result(snapshot, plan)
        result = {
            "question": normalized_question,
            "plan": plan.as_json(),
            "coverage": snapshot["coverage"],
            "answer": build_summary(snapshot, plan, table["rows"]),
            "table": table,
            "chart": chart,
            "suggestions": suggestions_for(plan),
            "meta": {
                "planner": "OpenAI Responses API",
                "model": self.model,
                "execution": "server-validated curated analytics",
            },
        }
        if debug:
            result["debug_dump"] = self._write_debug_dump(
                question=normalized_question,
                context=clean_context,
                availability=availability,
                raw_plan=raw_plan,
                adjustments=adjustments,
                plan=plan,
                planner_trace=planner_trace,
                snapshot=snapshot,
                result=result,
            )
        return result

    def _write_debug_dump(
        self,
        *,
        question: str,
        context: list[str],
        availability: dict[str, Any],
        raw_plan: dict[str, Any],
        adjustments: list[str],
        plan: QueryPlan,
        planner_trace: dict[str, Any],
        snapshot: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, str]:
        debug_id = (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        path = self.debug_dir / f"query-{debug_id}.json"
        document = {
            "debug_version": 1,
            "debug_id": debug_id,
            "created_at": datetime.now(UTC),
            "privacy_note": (
                "Contains the question and derived local analytics, but no API key, "
                "database URL, raw model prompt, or raw ADS-B positions."
            ),
            "question": question,
            "context": context,
            "availability": availability,
            "planner_trace": planner_trace,
            "raw_model_plan": raw_plan,
            "server_adjustments": adjustments,
            "validated_plan": plan.as_json(),
            "coverage": snapshot["coverage"],
            "candidate_rows": _debug_candidates(snapshot, plan),
            "final_response": {
                key: value for key, value in result.items() if key != "debug_dump"
            },
        }
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_debug_ready(document), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            display_path = path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            display_path = str(path)
        return {"id": debug_id, "path": display_path}

    @staticmethod
    def _no_data_answer(question: str, availability: dict[str, Any]) -> dict[str, Any]:
        return {
            "question": question,
            "plan": None,
            "coverage": {
                "requested_days": 0,
                "available_days": 0,
                "complete": False,
                "available_dates": [],
                "missing_dates": [],
                "message": "No processed ADS-B days are available locally yet.",
            },
            "answer": "There is no local ADS-B history to answer from yet. Add a day in Data control first.",
            "table": {"columns": [], "rows": []},
            "chart": None,
            "suggestions": [],
            "meta": {"execution": "no local data", "availability": availability},
        }


def normalize_plan_for_question(
    raw: dict[str, Any], question: str
) -> tuple[dict[str, Any], list[str]]:
    normalized = dict(raw)
    adjustments: list[str] = []
    defaults = {
        "company_query": None,
        "capability_query": None,
        "location_query": None,
        "region_query": None,
        "approval_query": None,
        "registration_query": None,
        "company_role": "either",
        "assignment_role": "OPERATOR",
        "approval_status": "ANY",
        "capability_kind": "ANY",
        "operator_grouping": "tail",
    }
    for field, value in defaults.items():
        normalized.setdefault(field, value)

    question_lower = question.lower()
    activity_question = bool(
        re.search(
            r"\b(?:busy|busiest|logged|hours?|activity|active|airborne|observations?)\b",
            question_lower,
        )
    )
    if (
        normalized.get("operation") == "daily_activity"
        and not normalized.get("airport_code")
        and re.search(r"\b(?:by\s+day|daily|each\s+day)\b", question_lower)
    ):
        explicit_airport = re.search(r"\b([A-Z]{3,4})\b", question)
        if explicit_airport:
            normalized["airport_code"] = explicit_airport.group(1)
            adjustments.append(
                f"Applied {explicit_airport.group(1)} as the airport for the daily series."
            )
    region_match = WORLD_REGION_IN_QUESTION_RE.search(question)
    if region_match:
        region_label, _ = normalize_world_region_query(region_match.group(1))
        normalized["region_query"] = region_label
        for misplaced_field in ("company_query", "location_query"):
            misplaced = normalized.get(misplaced_field)
            if isinstance(misplaced, str):
                try:
                    misplaced_region, _ = normalize_world_region_query(misplaced)
                except ValueError:
                    misplaced_region = None
                if misplaced_region == region_label:
                    normalized[misplaced_field] = None
        region_subject = (
            "operator/company"
            if normalized.get("operation") in REFERENCE_OPERATIONS
            or re.search(r"\boperators?\b", question_lower)
            else "airport-linked activity"
        )
        adjustments.append(
            f"Applied {region_label.replace('_', ' ').title()} as an "
            f"{region_subject} geographic-region filter."
        )

    # A plural aircraft designator means individual airframes, not one row that
    # aggregates the entire type. Requiring a digit avoids treating ordinary
    # plural words (for example, "operators") as type codes.
    plural_type_code = None
    if activity_question:
        for type_match in PLURAL_TYPE_IN_QUESTION_RE.finditer(question):
            requested_type = type_match.group(1).upper()
            if any(character.isdigit() for character in requested_type):
                plural_type_code = resolve_aircraft_type_code(requested_type)
                normalized["type_code"] = plural_type_code
                if plural_type_code != requested_type:
                    adjustments.append(
                        f"Resolved aircraft model alias {requested_type} to ICAO type "
                        f"designator {plural_type_code}."
                    )
                break

    operator_activity_question = bool(
        activity_question
        and (
            normalized.get("operation") == "operator_tail_activity"
            or (
                re.search(r"\b(?:tails?|aircraft|fleet)\b", question_lower)
                and (
                    normalized.get("company_query")
                    or re.search(r"\boperators?\b", question_lower)
                )
            )
            or re.search(r"\boperators?\b", question_lower)
        )
    )
    if operator_activity_question:
        normalized["operation"] = "operator_tail_activity"
        if normalized.get("date_scope") == "reference":
            normalized["date_scope"] = "range"
        activity_type = normalized.get("type_code")
        if isinstance(activity_type, str):
            resolved_type = resolve_aircraft_type_code(activity_type)
            if resolved_type != activity_type.strip().upper():
                normalized["type_code"] = resolved_type
                adjustments.append(
                    f"Resolved aircraft model alias {activity_type.strip().upper()} to ICAO type "
                    f"designator {resolved_type}."
                )
        if normalized.get("metric") not in OPERATION_METRICS["operator_tail_activity"]:
            normalized["metric"] = "active_hours"
        normalized["assignment_role"] = "OPERATOR"
        normalized["company_role"] = "operator"
        normalized["operator_grouping"] = (
            "operator"
            if re.search(r"\boperators?\b", question_lower)
            and not re.search(r"\b(?:tails?|registrations?)\b", question_lower)
            else "tail"
        )
        if re.search(r"\b(?:helicopters?|rotorcraft)\b", question_lower):
            normalized["helicopters_only"] = True
        capability_as_type = normalized.get("capability_query")
        if (
            not normalized.get("type_code")
            and isinstance(capability_as_type, str)
            and TYPE_CODE_RE.fullmatch(capability_as_type.strip().upper())
        ):
            requested_type = capability_as_type.strip().upper()
            normalized["type_code"] = resolve_aircraft_type_code(requested_type)
            normalized["capability_query"] = None
            adjustments.append(
                "Used the explicit aircraft code as an ADS-B type filter, not a maintenance capability."
            )
            if normalized["type_code"] != requested_type:
                adjustments.append(
                    f"Resolved aircraft model alias {requested_type} to ICAO type designator "
                    f"{normalized['type_code']}."
                )
        adjustments.append(
            "Restricted the dated activity ranking to current imported operator assignments."
        )
        return normalized, adjustments

    if plural_type_code:
        normalized["operation"] = "tail_ranking"
        if normalized.get("date_scope") == "reference":
            normalized["date_scope"] = "range"
        if normalized.get("metric") not in OPERATION_METRICS["tail_ranking"]:
            normalized["metric"] = "active_hours"
        normalized["operator_grouping"] = "tail"
        adjustments.append(
            f"Interpreted plural model {plural_type_code}s as individual aircraft and "
            "ranked one row per tail."
        )
        return normalized, adjustments

    operator_assignment_question = bool(
        re.search(
            r"\b(?:who\s+(?:operates|owns|manages)|operated\s+by|owned\s+by|"
            r"managed\s+by|assigned\s+to|operator(?:'s)?\s+fleet|fleet\s+(?:of|for)|"
            r"(?:operator|owner|manager).{0,32}(?:aircraft|tails?|registrations?)|"
            r"(?:aircraft|tails?|registrations?).{0,32}(?:operator|owner|manager))\b",
            question_lower,
        )
    )
    if operator_assignment_question:
        normalized["operation"] = "operator_aircraft"
        normalized["metric"] = "assigned_aircraft_count"
        normalized["date_scope"] = "reference"
        normalized["from_date"] = None
        normalized["to_date"] = None
        normalized["type_code"] = None
        normalized["airport_code"] = None
        normalized["capability_query"] = None
        normalized["location_query"] = None
        normalized["approval_query"] = None
        normalized["company_role"] = "operator"
        normalized["helicopters_only"] = False
        normalized["operator_grouping"] = "tail"
        normalized["chart"] = "table"
        normalized["limit"] = 100
        if re.search(r"\b(?:owns?|owned|owner)\b", question_lower):
            normalized["assignment_role"] = "OWNER"
        elif re.search(r"\b(?:manages?|managed|manager)\b", question_lower):
            normalized["assignment_role"] = "MANAGER"
        elif re.search(r"\b(?:aoc|authori[sz]ed)\b", question_lower):
            normalized["assignment_role"] = "AOC_AUTHORIZED"
        else:
            normalized["assignment_role"] = "OPERATOR"
        registration_match = REGISTRATION_IN_QUESTION_RE.search(question)
        if registration_match:
            registration = registration_match.group(0).upper()
            normalized["registration_query"] = registration
            if str(normalized.get("company_query") or "").upper() == registration:
                normalized["company_query"] = None
        adjustments.append(
            "Routed the operator/fleet question to current imported aircraft assignments."
        )
        return normalized, adjustments

    if (
        normalized.get("operation") == "tail_ranking"
        and normalized.get("company_query")
        and re.search(
            r"\b(?:busy|busiest|most\s+active|most\s+airborne|tails?|fleet)\b",
            question_lower,
        )
    ):
        normalized["operation"] = "operator_tail_activity"
        normalized["assignment_role"] = "OPERATOR"
        adjustments.append(
            "Restricted the tail activity ranking to the named operator's current assignments."
        )

    company_reference_question = bool(
        re.search(
            r"\b(?:mros?|operators?|maintenance (?:organi[sz]ations?|companies?)|part\s*145|approved "
            r"organi[sz]ations?|approval|certificate|rating|capabilit(?:y|ies)|"
            r"approved\s+(?:for|to)|maintain|maintenance scope)\b",
            question_lower,
        )
    )
    if company_reference_question:
        asks_capabilities = bool(
            re.search(
                r"\b(?:maintain|maintenance scope|capabilit(?:y|ies)|rating|"
                r"approved\s+(?:for|to)|aircraft|engine|component|specialist)\b",
                question_lower,
            )
        )
        asks_approval = bool(
            re.search(r"\b(?:approval number|certificate|status|valid|expired|suspended)\b", question_lower)
            or APPROVAL_NUMBER_RE.search(question)
        )
        operation = "company_capabilities" if asks_capabilities else (
            "company_approvals" if asks_approval else "company_directory"
        )
        if normalized.get("operation") != operation:
            adjustments.append(
                "Routed the maintenance-organisation question to imported company "
                "reference records rather than ADS-B activity."
            )
        normalized["operation"] = operation
        normalized["metric"] = (
            "matching_capabilities" if operation == "company_capabilities" else "capability_count"
        )
        normalized["date_scope"] = "reference"
        normalized["from_date"] = None
        normalized["to_date"] = None
        normalized["airport_code"] = None
        normalized["helicopters_only"] = False
        normalized["chart"] = "table"
        if re.search(r"\bmros?|maintenance (?:organi[sz]ations?|companies?)\b", question_lower):
            normalized["company_role"] = "mro"
        elif re.search(r"\boperators?\b", question_lower):
            normalized["company_role"] = "operator"
        approval_match = APPROVAL_NUMBER_RE.search(question)
        if approval_match and not normalized.get("approval_query"):
            normalized["approval_query"] = approval_match.group(0).upper()
        if operation == "company_capabilities":
            if normalized.get("approval_status") == "ANY":
                normalized["approval_status"] = "VALID"
            if normalized.get("type_code") and not normalized.get("capability_query"):
                normalized["capability_query"] = normalized["type_code"]
                adjustments.append(
                    "Used the aircraft/model term as a maintenance-capability search, "
                    "not an ADS-B type-code filter."
                )
            normalized["type_code"] = None
            if normalized.get("capability_kind") == "ANY" and re.search(
                r"\b(?:aircraft|helicopters?|rotorcraft)\b", question_lower
            ):
                normalized["capability_kind"] = "AIRCRAFT"
            elif normalized.get("capability_kind") == "ANY" and re.search(
                r"\bengines?\b", question_lower
            ):
                normalized["capability_kind"] = "ENGINE"
            elif normalized.get("capability_kind") == "ANY" and re.search(
                r"\bcomponents?\b", question_lower
            ):
                normalized["capability_kind"] = "COMPONENT"
            elif normalized.get("capability_kind") == "ANY" and re.search(
                r"\bspecialist\b", question_lower
            ):
                normalized["capability_kind"] = "SPECIALIST"
        else:
            normalized["type_code"] = None
        return normalized, adjustments

    requested_type = normalized.get("type_code")
    if isinstance(requested_type, str):
        resolved_type = resolve_aircraft_type_code(requested_type)
        if resolved_type != requested_type.strip().upper():
            normalized["type_code"] = resolved_type
            adjustments.append(
                f"Resolved aircraft model alias {requested_type.strip().upper()} to ICAO type "
                f"designator {resolved_type}."
            )

    if ALL_AVAILABLE_RE.search(question):
        if normalized.get("date_scope") != "all_available":
            adjustments.append(
                "Interpreted whole-database wording as all locally available UTC days."
            )
        normalized["date_scope"] = "all_available"
        normalized["from_date"] = None
        normalized["to_date"] = None

    generic_hub_traffic = (
        normalized.get("operation") == "hub_ranking"
        and re.search(r"\b(?:traffic|busiest|most\s+activity)\b", question_lower)
        and not re.search(
            r"\b(?:primary|primarily|based|base|observation|sample|hour|time)\b",
            question_lower,
        )
    )
    if generic_hub_traffic and normalized.get("metric") != "unique_aircraft":
        adjustments.append(
            "Mapped generic hub traffic to unique aircraft; observation counts are "
            "sampling-dependent and primary-aircraft counts mean something narrower."
        )
        normalized["metric"] = "unique_aircraft"

    airport_match = AIRPORT_IN_QUESTION_RE.search(question)
    if airport_match is None and re.search(
        r"\b(?:arriv(?:e|ed|al)|depart(?:ed|ure)?|airport|airfield|hub)\b",
        question,
        re.IGNORECASE,
    ):
        airport_match = re.search(r"\b([A-Z0-9]{3,4})\b", question)
    asks_for_types = bool(
        re.search(
            r"\b(?:aircraft|helicopter|rotorcraft)\s+types\b|\btypes\b"
            r"|\b(?:group(?:ed|ing)?|breakdown|break\s+down)\s+by\s+"
            r"(?:aircraft\s+)?type\b",
            question,
            re.IGNORECASE,
        )
    )
    if airport_match and asks_for_types:
        airport_code = airport_match.group(1).upper()
        if normalized.get("operation") != "airport_types":
            adjustments.append(
                "Routed an aircraft-type breakdown for one named airport to the "
                "airport-type aggregation."
            )
        normalized["operation"] = "airport_types"
        normalized["airport_code"] = airport_code
        normalized["helicopters_only"] = bool(
            re.search(r"\b(?:helicopters?|rotorcraft)\b", question, re.IGNORECASE)
        )
        if normalized.get("metric") not in OPERATION_METRICS["airport_types"]:
            normalized["metric"] = "unique_aircraft"
        normalized["limit"] = 100

    asks_for_individual_aircraft = not asks_for_types and bool(
        re.search(
            r"\b(?:which|what|show|list)\b.{0,45}"
            r"\b(?:aircraft|helicopters?|rotorcraft|tails?)\b"
            r"|\b(?:aircraft|helicopters?|rotorcraft|tails?)\b.{0,45}"
            r"\b(?:arrived|departed|seen|visited|used)\b",
            question,
            re.IGNORECASE,
        )
    ) and not bool(re.search(r"\baircraft\s+types?\b", question, re.IGNORECASE))
    if airport_match and asks_for_individual_aircraft:
        airport_code = airport_match.group(1).upper()
        if normalized.get("operation") != "airport_aircraft":
            adjustments.append(
                "Routed an individual-aircraft question for one named airport to "
                "the airport-aircraft view instead of a global hub ranking."
            )
        normalized["operation"] = "airport_aircraft"
        normalized["airport_code"] = airport_code
        if re.search(r"\b(?:helicopters?|rotorcraft)\b", question, re.IGNORECASE):
            normalized["helicopters_only"] = True
        asks_arrivals = bool(re.search(r"\barriv(?:e|ed|al|als)\b", question, re.IGNORECASE))
        asks_departures = bool(
            re.search(r"\bdepart(?:ed|ure|ures)?\b", question, re.IGNORECASE)
        )
        if asks_arrivals and asks_departures:
            normalized["metric"] = "airport_movement_candidates"
        elif asks_arrivals:
            normalized["metric"] = "airport_arrival_candidates"
        elif asks_departures:
            normalized["metric"] = "airport_departure_candidates"
        elif normalized.get("metric") not in OPERATION_METRICS["airport_aircraft"]:
            normalized["metric"] = "observations"
        normalized["limit"] = 100

    if (
        normalized.get("operation") == "airport_aircraft"
        and normalized.get("metric") not in OPERATION_METRICS["airport_aircraft"]
    ):
        # The structured schema constrains operations and metrics separately, so a
        # model can still produce a valid enum pair that is invalid in combination.
        # In particular, ``unique_aircraft`` is a natural choice for a view whose
        # rows are already unique aircraft. Use a supported ordering metric and let
        # the airport-aircraft summary report the number of returned tails.
        replacement_metric = "observations"
        if re.search(r"\barriv(?:e|ed|al|als)\b", question, re.IGNORECASE):
            replacement_metric = "airport_arrival_candidates"
        if re.search(r"\bdepart(?:ed|ure|ures)?\b", question, re.IGNORECASE):
            replacement_metric = (
                "airport_movement_candidates"
                if replacement_metric == "airport_arrival_candidates"
                else "airport_departure_candidates"
            )
        if re.search(r"\bground(?:ed)?\b", question, re.IGNORECASE):
            replacement_metric = "airport_ground_observations"
        normalized["metric"] = replacement_metric
        normalized["limit"] = 100
        adjustments.append(
            "Mapped the airport-aircraft metric to a compatible per-aircraft metric; "
            "each result row already represents one unique aircraft."
        )
    return normalized, adjustments


def validate_query_plan(
    raw: dict[str, Any],
    *,
    latest_available: date | str,
    earliest_available: date | str | None = None,
) -> QueryPlan:
    if not isinstance(raw, dict):
        raise ValueError("The query plan must be an object")
    allowed_fields = set(QUERY_PLAN_SCHEMA["properties"])
    unexpected_fields = set(raw) - allowed_fields
    if unexpected_fields:
        raise ValueError("The query planner returned unsupported plan fields")
    operation = raw.get("operation")
    metric = raw.get("metric")
    if operation not in OPERATION_METRICS:
        raise ValueError("The query planner selected an unsupported operation")
    if metric not in OPERATION_METRICS[operation]:
        raise ValueError(f"{metric!r} is not available for {operation}")

    latest = date.fromisoformat(latest_available) if isinstance(latest_available, str) else latest_available
    earliest_value = earliest_available or latest
    earliest = date.fromisoformat(earliest_value) if isinstance(earliest_value, str) else earliest_value
    date_scope = raw.get("date_scope", "range")
    if date_scope not in {"range", "all_available", "reference"}:
        raise ValueError("The query planner selected an unsupported date scope")
    if operation in REFERENCE_OPERATIONS:
        date_scope = "reference"
        from_date = None
        to_date = None
    elif date_scope == "reference":
        raise ValueError("Reference date scope is only available for company queries")
    elif date_scope == "all_available":
        from_date = earliest
        to_date = latest
    else:
        from_date = _plan_date(raw.get("from_date"), fallback=latest)
        to_date = _plan_date(raw.get("to_date"), fallback=latest)
    if from_date is not None and to_date is not None and to_date < from_date:
        raise ValueError("The query plan's to date must be on or after its from date")
    if from_date is not None and to_date is not None and (to_date - from_date).days + 1 > MAX_ANALYTICS_DAYS:
        raise ValueError(f"Natural-language periods are capped at {MAX_ANALYTICS_DAYS} days")

    type_code = raw.get("type_code")
    if type_code is not None:
        if not isinstance(type_code, str):
            raise ValueError("Aircraft type code must be text")
        type_code = resolve_aircraft_type_code(type_code)
        if type_code is not None and not TYPE_CODE_RE.fullmatch(type_code):
            raise ValueError("The query planner selected an invalid aircraft type code")

    airport_code = raw.get("airport_code")
    if airport_code is not None:
        if not isinstance(airport_code, str):
            raise ValueError("Airport code must be text")
        airport_code = airport_code.strip().upper()
        if not AIRPORT_CODE_RE.fullmatch(airport_code):
            raise ValueError("The query planner selected an invalid airport code")
    if operation in {"airport_aircraft", "airport_types"} and airport_code is None:
        raise ValueError(f"An {operation.replace('_', '-')} query requires an IATA or ICAO airport code")

    limit = raw.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Natural-language result limits must be between 1 and 100")
    chart = raw.get("chart", "auto")
    if chart not in {"auto", "line", "bar", "doughnut", "table"}:
        raise ValueError("The query planner selected an unsupported chart")

    helicopters_only = raw.get("helicopters_only", False)
    if not isinstance(helicopters_only, bool):
        raise ValueError("The helicopter filter must be true or false")
    if operation == "helicopter_types":
        helicopters_only = True

    def query_text(field: str) -> str | None:
        value = raw.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field.replace('_', ' ').title()} must be text")
        value = " ".join(value.split())
        if not value:
            return None
        if len(value) > 160:
            raise ValueError("Company query filters are capped at 160 characters")
        return value

    company_role = raw.get("company_role", "either")
    if company_role not in {"operator", "mro", "either"}:
        raise ValueError("The query planner selected an unsupported company role")
    assignment_role = raw.get("assignment_role", "OPERATOR")
    if assignment_role not in {
        "OPERATOR", "OWNER", "MANAGER", "AOC_AUTHORIZED", "OTHER", "ANY"
    }:
        raise ValueError("The query planner selected an unsupported aircraft assignment role")
    if operation == "operator_tail_activity" and assignment_role == "ANY":
        assignment_role = "OPERATOR"
    operator_grouping = raw.get("operator_grouping", "tail")
    if operator_grouping not in {"tail", "operator"}:
        raise ValueError("The query planner selected an unsupported operator grouping")
    approval_status = raw.get("approval_status", "ANY")
    if approval_status not in {"VALID", "PENDING", "SUSPENDED", "REVOKED", "EXPIRED", "UNKNOWN", "ANY"}:
        raise ValueError("The query planner selected an unsupported approval status")
    capability_kind = raw.get("capability_kind", "ANY")
    if capability_kind not in {"AIRCRAFT", "ENGINE", "COMPONENT", "SPECIALIST", "SERVICE", "OTHER", "ANY"}:
        raise ValueError("The query planner selected an unsupported capability kind")
    company_query = query_text("company_query")
    capability_query = query_text("capability_query")
    location_query = query_text("location_query")
    region_query_value = query_text("region_query")
    region_query, _ = normalize_world_region_query(region_query_value)
    approval_query = query_text("approval_query")
    registration_query = query_text("registration_query")
    if operation == "company_capabilities" and not any(
        (company_query, capability_query, location_query, region_query)
    ):
        raise ValueError("Capability searches require a company, capability, or location")
    return QueryPlan(
        operation=operation,
        metric=metric,
        date_scope=date_scope,
        from_date=from_date,
        to_date=to_date,
        type_code=type_code,
        airport_code=airport_code,
        company_query=company_query,
        capability_query=capability_query,
        location_query=location_query,
        region_query=region_query,
        approval_query=approval_query,
        registration_query=registration_query,
        company_role=company_role,
        assignment_role=assignment_role,
        approval_status=approval_status,
        capability_kind=capability_kind,
        helicopters_only=helicopters_only,
        operator_grouping=operator_grouping,
        limit=limit,
        chart=chart,
    )


def _all_available_coverage(availability: dict[str, Any]) -> dict[str, Any]:
    available_dates = list(availability.get("available_dates") or [])
    count = len(available_dates)
    return {
        "requested_days": count,
        "available_days": count,
        "complete": True,
        "available_dates": available_dates,
        "missing_dates": [],
        "message": (
            f"All {count} locally available UTC day{'s are' if count != 1 else ' is'} "
            "included. Gaps between those dates were not requested."
        ),
    }


def _reference_coverage(company_data: dict[str, Any]) -> dict[str, Any]:
    companies = int(company_data.get("companies") or 0)
    capabilities = int(company_data.get("capabilities") or 0)
    assignments = int(company_data.get("aircraft_assignments") or 0)
    return {
        "requested_days": 0,
        "available_days": 0,
        "complete": True,
        "available_dates": [],
        "missing_dates": [],
        "message": (
            f"Searched imported company reference data covering {companies:,} companies "
            f"and {capabilities:,} approval-capability rows, with {assignments:,} "
            "aircraft assignments. These records are not "
            "date-bounded ADS-B activity."
        ),
    }


def _debug_candidates(snapshot: dict[str, Any], plan: QueryPlan) -> dict[str, Any]:
    if plan.operation == "hub_ranking":
        rows = snapshot["hubs"]
    elif plan.operation == "tail_ranking":
        rows = snapshot["tails"]
    elif plan.operation == "operator_tail_activity":
        rows = snapshot["operator_tail_activity"]
    elif plan.operation == "airport_aircraft":
        rows = snapshot["airport_aircraft"]
    elif plan.operation == "airport_types":
        rows = snapshot["airport_types"]
    elif plan.operation == "helicopter_types":
        rows = snapshot["helicopters"]["types"]
    elif plan.operation == "type_breakdown":
        rows = snapshot["types"]
    elif plan.operation == "daily_activity":
        rows = snapshot["daily_activity"]
    elif plan.operation in REFERENCE_OPERATIONS:
        rows = snapshot[plan.operation]
    else:
        rows = [snapshot["totals"]]
    return {
        "operation": plan.operation,
        "metric": plan.metric,
        "source_row_count": len(rows),
        "rows": rows,
    }


def _debug_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _debug_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_debug_ready(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _plan_date(value: Any, *, fallback: date) -> date:
    if value is None:
        return fallback
    if not isinstance(value, str):
        raise ValueError("Query-plan dates must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Query-plan dates must use YYYY-MM-DD") from exc


def build_result(
    snapshot: dict[str, Any], plan: QueryPlan
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    operation = plan.operation
    metric = plan.metric
    if operation == "daily_activity":
        rows = list(snapshot["daily_activity"])
        columns = ["utc_date", metric]
        x_key = "utc_date"
    elif operation in {"type_breakdown", "helicopter_types", "airport_types"}:
        source = (
            snapshot["helicopters"]["types"]
            if operation == "helicopter_types"
            else snapshot["airport_types"]
            if operation == "airport_types"
            else snapshot["types"]
        )
        if plan.type_code:
            source = [row for row in source if row.get("type_code") == plan.type_code]
        rows = _rank(source, metric, plan.limit)
        columns = ["type_code", "description", metric]
        x_key = "type_code"
    elif operation == "hub_ranking":
        rows = _rank(snapshot["hubs"], metric, plan.limit)
        rows = [{**row, "airport": row.get("iata_code") or row.get("airport_ident")} for row in rows]
        columns = ["airport", "airport_name", metric]
        x_key = "airport"
    elif operation == "tail_ranking":
        rows = _rank(snapshot["tails"], metric, plan.limit)
        rows = [{**row, "tail": row.get("registration") or row.get("address")} for row in rows]
        columns = [
            "tail", "operator", "activity_area", "type_code", "description", "category",
            "primary_airport_label", metric,
            "operator_source_code",
        ]
        x_key = "tail"
    elif operation == "operator_tail_activity":
        rows = _rank(snapshot["operator_tail_activity"], metric, plan.limit)
        rows = [{**row, "tail": row.get("registration") or row.get("address")} for row in rows]
        if plan.operator_grouping == "operator":
            columns = [
                "company", "geographic_region", "aircraft_count", "active_days",
                metric, "source_code",
            ]
            x_key = "company"
        else:
            columns = [
                "tail", "company", "geographic_region", "type_code", "category",
                "active_days", metric, "source_code",
            ]
            x_key = "tail"
    elif operation == "airport_aircraft":
        rows = list(snapshot["airport_aircraft"])
        rows = [{**row, "tail": row.get("registration") or row.get("address")} for row in rows]
        columns = [
            "tail",
            "operator",
            "type_code",
            "airport",
            "airport_arrival_candidates",
            "airport_departure_candidates",
            "activity_evidence",
            "airport_ground_observations",
            "active_hours",
            "airborne_hours",
            "operator_source_code",
        ]
        x_key = "tail"
    elif operation == "company_directory":
        rows = list(snapshot["company_directory"])
        columns = [
            "company", "roles", "geographic_region", "sites", "locations", "approval_numbers",
            "site_count", "valid_approval_count", "capability_count",
            "assigned_aircraft_count",
        ]
        x_key = "company"
    elif operation == "operator_aircraft":
        rows = list(snapshot["operator_aircraft"])
        columns = [
            "company", "geographic_region", "registration", "aircraft_address", "assignment_role",
            "confidence", "valid_from", "valid_to", "source_code",
        ]
        x_key = "company"
    elif operation == "company_approvals":
        rows = list(snapshot["company_approvals"])
        columns = [
            "company", "geographic_region", "approval_number", "authority_code", "approval_type",
            "status", "sites", "locations", "capability_count", "last_verified_at",
        ]
        x_key = "company"
    elif operation == "company_capabilities":
        rows = list(snapshot["company_capabilities"])
        columns = [
            "company", "geographic_region", "approval_number", "approval_status", "capability_kind",
            "rating_code", "manufacturer", "model", "aircraft_type_code",
            "capability", "sites", "locations",
        ]
        x_key = "company"
    else:
        totals = snapshot["totals"]
        rows = [{"metric": METRIC_LABELS[metric], "value": totals.get(metric, 0)}]
        columns = ["metric", "value"]
        x_key = "metric"
        metric = "value"

    rows = [{key: _simple_value(value) for key, value in row.items()} for row in rows]
    table = {"columns": columns, "rows": rows}
    if not rows or plan.chart == "table" or operation in REFERENCE_OPERATIONS:
        return table, None

    chart_type = _chart_type(plan, row_count=len(rows))
    return table, {
        "type": chart_type,
        "x": x_key,
        "y": metric,
        "label": METRIC_LABELS.get(plan.metric, plan.metric.replace("_", " ").title()),
    }


def _rank(rows: list[dict[str, Any]], metric: str, limit: int) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: _numeric(row.get(metric)), reverse=True)[:limit]


def _numeric(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _simple_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _chart_type(plan: QueryPlan, *, row_count: int) -> str:
    if plan.operation == "daily_activity":
        return plan.chart if plan.chart in {"line", "bar"} else "line"
    if plan.operation in {"type_breakdown", "helicopter_types", "airport_types"}:
        if plan.chart == "doughnut" and row_count <= 8:
            return "doughnut"
        return "bar"
    return "bar"


def build_summary(snapshot: dict[str, Any], plan: QueryPlan, rows: list[dict[str, Any]]) -> str:
    coverage = snapshot["coverage"]
    if plan.operation in REFERENCE_OPERATIONS:
        if not rows:
            return (
                "No imported company reference records matched those filters. "
                "Try a company name, registration, approval number, location, or a broader capability term."
            )
        companies = len({row.get("company") for row in rows if row.get("company")})
        if plan.operation == "operator_aircraft":
            return (
                f"I found {len(rows):,} current imported aircraft assignment"
                f"{'s' if len(rows) != 1 else ''} across {companies:,} compan"
                f"{'y' if companies == 1 else 'ies'}. These are sourced operator/owner/manager "
                "claims, not proof of operational control on a particular flight."
            )
        if plan.operation == "company_directory":
            return (
                f"I found {companies:,} matching compan{'y' if companies == 1 else 'ies'} "
                "in the imported directory. Counts describe the loaded regulatory and "
                "company records, not observed ADS-B traffic."
            )
        if plan.operation == "company_approvals":
            return (
                f"I found {len(rows):,} matching approval"
                f"{'s' if len(rows) != 1 else ''} across {companies:,} compan"
                f"{'y' if companies == 1 else 'ies'} in the imported reference data."
            )
        return (
            f"I found {len(rows):,} matching approval scope"
            f"{'s' if len(rows) != 1 else ''} across {companies:,} MRO"
            f"{'s' if companies != 1 else ''}. These are regulator-published approval "
            "scopes, not evidence of work performed, current capacity, or a link to ADS-B traffic."
        )
    period = (
        f"all {coverage['available_days']} locally available UTC "
        f"day{'s' if coverage['available_days'] != 1 else ''}"
        if plan.date_scope == "all_available"
        else _period_label(plan.from_date, plan.to_date)
    )
    label = METRIC_LABELS[plan.metric].lower()
    if not rows:
        answer = f"No matching activity was found for {period}."
    elif plan.operation == "daily_activity":
        values = [_numeric(row.get(plan.metric)) for row in rows]
        airport_scope = f" linked to {plan.airport_code}" if plan.airport_code else ""
        answer = (
            f"Across {len(rows)} available day{'s' if len(rows) != 1 else ''} in {period}, "
            f"{label}{airport_scope} ranged from {_format_metric(min(values), plan.metric)} to "
            f"{_format_metric(max(values), plan.metric)}."
        )
    elif plan.operation == "totals":
        formatted = _format_metric(rows[0]["value"], plan.metric)
        labelled_value = formatted if plan.metric.endswith("hours") else f"{formatted} {label}"
        answer = f"The local data records {labelled_value} for {period}."
    elif plan.operation == "operator_tail_activity":
        top = rows[0]
        region_scope = (
            f" in {plan.region_query.replace('_', ' ').title()}"
            if plan.region_query else ""
        )
        if plan.operator_grouping == "operator":
            answer = (
                f"Using current imported assignment evidence, {top.get('company') or 'the leading operator'} "
                f"ranks first among operators{region_scope} for {label} in {period}, with "
                f"{_format_metric(top.get(plan.metric), plan.metric)} across "
                f"{int(top.get('aircraft_count') or 0):,} observed assigned aircraft. "
                "Assignments are attribution evidence, not proof of operational control on every flight."
            )
        else:
            operator_scope = plan.company_query or "all operators with imported assignments"
            answer = (
                f"Using the current imported assignment snapshot for {operator_scope}{region_scope}, "
                f"{top.get('tail') or 'the leading tail'} ranks first for {label} in {period}, "
                f"with {_format_metric(top.get(plan.metric), plan.metric)}. Current assignment "
                "data is attribution evidence, not proof that the same operator controlled every "
                "historical flight."
            )
    elif plan.operation == "airport_aircraft":
        airport = plan.airport_code or "the selected airport"
        arrivals = sum(_numeric(row.get("airport_arrival_candidates")) for row in rows)
        departures = sum(_numeric(row.get("airport_departure_candidates")) for row in rows)
        answer = (
            f"I found {len(rows):,} aircraft with matching airport-activity evidence at {airport} "
            f"in {period}, including {_format_metric(arrivals, 'arrival_candidates')} arrival "
            f"and {_format_metric(departures, 'departure_candidates')} departure candidates. "
            "These are conservative ADS-B-derived candidates, not certified movement records."
        )
    elif plan.operation == "airport_types":
        airport = plan.airport_code or "the selected airport"
        top = rows[0]
        answer = (
            f"I found {len(rows):,} matching aircraft type"
            f"{'s' if len(rows) != 1 else ''} at {airport} in {period}. "
            f"{top.get('type_code') or 'UNKNOWN'} ranks first for {label}, with "
            f"{_format_metric(top.get(plan.metric), plan.metric)}."
        )
    else:
        top = rows[0]
        name_key = {
            "hub_ranking": "airport",
            "tail_ranking": "tail",
            "operator_tail_activity": "tail",
            "airport_aircraft": "tail",
            "type_breakdown": "type_code",
            "helicopter_types": "type_code",
            "airport_types": "type_code",
        }[plan.operation]
        activity_area = plan.region_query or plan.location_query
        if plan.operation == "tail_ranking" and activity_area:
            answer = (
                f"{top.get(name_key, 'The leading result')} ranks first for {label} among "
                f"aircraft linked to airports in "
                f"{activity_area.replace('_', ' ').title()} in {period}, with "
                f"{_format_metric(top.get(plan.metric), plan.metric)}. Regional attribution "
                "uses airport-link evidence; the hours are whole-day aircraft activity, not "
                "exact time inside the regional boundary."
            )
        else:
            answer = (
                f"{top.get(name_key, 'The leading result')} ranks first for {label} in {period}, "
                f"with {_format_metric(top.get(plan.metric), plan.metric)}."
            )
    if not coverage.get("complete"):
        answer += " This is a partial result because some requested days are not local."
    return answer


def _period_label(start: date | None, end: date | None) -> str:
    if start is None or end is None:
        return "the imported reference data"
    if start == end:
        return start.strftime("%d %B %Y")
    return f"{start.strftime('%d %B %Y')} to {end.strftime('%d %B %Y')}"


def _format_metric(value: Any, metric: str) -> str:
    number = _numeric(value)
    if metric.endswith("hours"):
        return f"{number:,.1f} hours"
    return f"{number:,.0f}"


def suggestions_for(plan: QueryPlan) -> list[str]:
    if plan.operation == "operator_aircraft":
        return [
            "Who operates one of these registrations?",
            "Show all current aircraft assigned to this operator.",
        ]
    if plan.operation == "operator_tail_activity":
        return [
            "Show the current assignment evidence for these tails.",
            "Rank this operator's tails by airborne hours instead.",
            "Which of these tails were active on the latest available day?",
        ]
    if plan.operation == "company_directory":
        return [
            "Which imported Part 145 MROs are in Surrey?",
            "Show the approval details for one of these companies.",
        ]
    if plan.operation == "company_approvals":
        return [
            "What aircraft capabilities are attached to this approval?",
            "Show the sites for this maintenance organisation.",
        ]
    if plan.operation == "company_capabilities":
        return [
            "Show all aircraft approval scopes for one of these MROs.",
            "Which imported MROs have EC135 approval scope?",
        ]
    period = (
        "the whole local database"
        if plan.date_scope == "all_available"
        else plan.to_date.isoformat() if plan.to_date else "the imported reference data"
    )
    suggestions = {
        "hub_ranking": [
            f"Which tails were most active on {period}?",
            "Break the same day down by aircraft type.",
        ],
        "tail_ranking": [
            f"Which hubs saw the most aircraft on {period}?",
            f"Show classified helicopter types on {period}.",
        ],
        "airport_aircraft": [
            f"Break aircraft at {plan.airport_code or 'that airport'} down by type.",
            f"Which aircraft logged the most airborne hours in {period}?",
        ],
        "airport_types": [
            f"Show the individual aircraft at {plan.airport_code or 'that airport'}.",
            f"Compare helicopter movement candidates at that airport on {period}.",
        ],
        "type_breakdown": [
            f"Which hubs saw the most unique aircraft on {period}?",
            f"Show the most active tails on {period}.",
        ],
        "helicopter_types": [
            f"Which hubs had the most helicopter activity on {period}?",
            f"Rank helicopter types by airborne hours on {period}.",
        ],
        "daily_activity": [
            "Which aircraft types drove that activity?",
            "Rank the busiest hubs for the latest available day.",
        ],
        "totals": [
            f"Break that down by aircraft type on {period}.",
            f"Which hubs led on {period}?",
        ],
    }
    return suggestions[plan.operation]
