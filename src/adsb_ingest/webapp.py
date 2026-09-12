from __future__ import annotations

import argparse
import hmac
import logging
import os
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory
from waitress import serve
from werkzeug.middleware.proxy_fix import ProxyFix

from .analytics import AnalyticsStore
from .maintenance import MaintenanceStore
from .capability_mappings import CapabilityMappingStore
from .access import AccessStore, AccessUser, normalize_email, normalize_role
from .companies import PHASE8_MIGRATION
from .natural_language import NaturalLanguageAnalytics, QueryPlannerUnavailable
from .public_access import QueryRateLimiter, RateLimitDecision
from .semantic_query import SemanticNaturalLanguageAnalytics
from .work_queue import AdminStore, SequentialIngestionWorker


LOGGER = logging.getLogger("adsb_web")
STATIC_ROOT = Path(__file__).with_name("web_static")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
LOCAL_DEV_ORIGINS = {
    "http://localhost:3000",
    "http://127.0.0.1:3000",
}
PUBLIC_DEMO_ENDPOINTS = frozenset(
    {
        "health",
        "index",
        "mobile_demo",
        "natural_language_query",
        "query_config",
        "static_asset",
    }
)
MUTATION_MINIMUM_ROLE = {
    "tar1090_refresh": "ANALYST",
    "tar1090_preview": "ANALYST",
    "tar1090_identity": "ANALYST",
    "tools_source_settings": "ANALYST",
    "tools_source_lookup": "ANALYST",
    "identity_preview": "ANALYST",
    "identity_assign": "ANALYST",
    "capability_mapping_save": "ANALYST",
    "capability_phrase_save": "ANALYST",
    "lba_preview": "ANALYST",
    "lba_import": "ANALYST",
    "maintenance_add": "ANALYST",
    "maintenance_review": "ANALYST",
    "maintenance_archive": "ANALYST",
    "natural_language_query": "VIEWER",
    "set_aircraft_operator": "ANALYST",
    "update_company_site_tracking": "ANALYST",
    "queue_date": "ANALYST",
    "queue_range": "ANALYST",
    "retry_date": "ANALYST",
    "reprocess_date": "ANALYST",
    "cancel_queue_item": "ANALYST",
    "upsert_access_user": "ADMIN",
    "deactivate_access_user": "ADMIN",
}


def _environment_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def _environment_int(name: str, default: int, *, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        result = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if result < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return result


def _rate_limit_response(decision: RateLimitDecision) -> Response:
    if decision.reason == "busy":
        message = "The demo is answering other questions. Please try again shortly."
    elif decision.reason == "global_rate_limit":
        message = "The public demo has reached its query budget. Please try again later."
    else:
        message = "Too many questions from this visitor. Please wait before trying again."
    response = jsonify({"error": message, "code": decision.reason})
    response.status_code = 429
    response.headers["Retry-After"] = str(decision.retry_after_seconds)
    response.headers["X-RateLimit-Limit"] = str(decision.client_limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.client_remaining)
    return response


def _database_url(value: str | None) -> str:
    result = value or os.getenv("DATABASE_URL")
    if not result:
        raise RuntimeError("DATABASE_URL is required for the data-management application")
    return result


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _parse_date(value: Any, *, historical_only: bool = True) -> date:
    if not isinstance(value, str):
        raise ValueError("A date in YYYY-MM-DD format is required")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Date must use YYYY-MM-DD format") from exc
    if historical_only and result >= datetime.now(UTC).date():
        raise ValueError("Only completed historical UTC dates can be queued")
    return result


def _parse_month(value: str | None) -> tuple[int, int]:
    if value is None:
        today = datetime.now(UTC).date()
        return today.year, today.month
    match = MONTH_RE.fullmatch(value)
    if not match:
        raise ValueError("Month must use YYYY-MM format")
    year, month = map(int, match.groups())
    if not 1 <= month <= 12:
        raise ValueError("Month must be between 01 and 12")
    return year, month


def _parse_raw_source(value: Any, *, pi_configured: bool) -> str:
    source = "DIRECT" if value is None else str(value).strip().upper()
    if source not in {"DIRECT", "PI"}:
        raise ValueError("raw_source must be DIRECT or PI")
    if source == "PI" and not pi_configured:
        raise ValueError(
            "Raspberry Pi source is not configured on the server; set "
            "ADSB_ARCHIVE_API_URL and ADSB_ARCHIVE_API_TOKEN"
        )
    return source


def create_app(
    database_url: str | None = None,
    *,
    start_worker: bool = True,
    store: AdminStore | None = None,
    analytics_store: AnalyticsStore | None = None,
    natural_language: NaturalLanguageAnalytics | SemanticNaturalLanguageAnalytics | None = None,
    public_demo: bool | None = None,
    trusted_proxy_count: int | None = None,
    query_rate_limiter: QueryRateLimiter | None = None,
    access_store: AccessStore | Any | None = None,
    auth_mode: str | None = None,
) -> Flask:
    public_demo_enabled = (
        _environment_bool("HELIGENT_PUBLIC_DEMO") if public_demo is None else public_demo
    )
    selected_auth_mode = (
        "PUBLIC"
        if public_demo_enabled
        else (auth_mode or os.getenv("HELIGENT_AUTH_MODE", "LOCAL")).strip().upper()
    )
    if selected_auth_mode not in {"LOCAL", "NGROK", "PUBLIC"}:
        raise ValueError("HELIGENT_AUTH_MODE must be LOCAL or NGROK")
    proxy_count = (
        _environment_int("HELIGENT_TRUSTED_PROXY_COUNT", 0, minimum=0)
        if trusted_proxy_count is None
        else trusted_proxy_count
    )
    if isinstance(proxy_count, bool) or proxy_count < 0:
        raise ValueError("Trusted proxy count must be zero or greater")
    app = Flask(__name__, static_folder=None)
    app.config.update(
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=64 * 1024,
        PUBLIC_DEMO=public_demo_enabled,
        AUTH_MODE=selected_auth_mode,
    )
    if proxy_count:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_count,
            x_proto=proxy_count,
            x_host=proxy_count,
        )
    admin_store = store or AdminStore(_database_url(database_url))
    admin_store.apply_phase3_migration()
    admin_store.apply_phase13_migration()
    access: AccessStore | Any | None = access_store
    proxy_secret = ""
    if selected_auth_mode == "NGROK":
        proxy_secret = os.getenv("HELIGENT_AUTH_PROXY_SECRET", "").strip()
        if len(proxy_secret) < 32 or proxy_secret.lower().startswith("replace-"):
            raise RuntimeError(
                "HELIGENT_AUTH_PROXY_SECRET must contain at least 32 characters"
            )
        access = access or AccessStore(admin_store.dsn)
        access.apply_migration()
        bootstrap_emails = [
            item.strip()
            for item in os.getenv("HELIGENT_BOOTSTRAP_ADMIN_EMAILS", "").split(",")
            if item.strip()
        ]
        if bootstrap_emails:
            access.bootstrap_admins(bootstrap_emails)
        if access.active_admin_count() < 1:
            raise RuntimeError(
                "At least one active maintenance ADMIN is required; set "
                "HELIGENT_BOOTSTRAP_ADMIN_EMAILS for the first startup"
            )
    archive_api_url = os.getenv("ADSB_ARCHIVE_API_URL", "").strip()
    archive_api_token = os.getenv("ADSB_ARCHIVE_API_TOKEN", "").strip()
    pi_source_configured = bool(archive_api_url and archive_api_token)
    analytics = analytics_store or AnalyticsStore(admin_store)
    analytics.apply_phase4_migration()
    analytics.apply_phase6_migration()
    if analytics_store is None:
        admin_store.apply_schema_once(PHASE8_MIGRATION)
        analytics.apply_phase9_migration()
        analytics.apply_phase10_migration()
        analytics.apply_phase11_migration()
        analytics.apply_phase12_migration()
        analytics.apply_phase15_migration()
        admin_store.apply_schema_once(Path(__file__).resolve().parents[2] / 'schema' / 'phase16.sql')
        admin_store.apply_schema_once(Path(__file__).resolve().parents[2] / 'schema' / 'phase17.sql')
        admin_store.apply_schema_once(Path(__file__).resolve().parents[2] / 'schema' / 'phase18.sql')
        admin_store.apply_schema_once(Path(__file__).resolve().parents[2] / 'schema' / 'phase19.sql')
        admin_store.apply_schema_once(Path(__file__).resolve().parents[2] / 'schema' / 'phase20.sql')
        admin_store.apply_schema_once(Path(__file__).resolve().parents[2] / 'schema' / 'phase21.sql')
        admin_store.apply_schema_once(Path(__file__).resolve().parents[2] / 'schema' / 'phase22.sql')
        admin_store.apply_schema_once(Path(__file__).resolve().parents[2] / 'schema' / 'phase23.sql')
    query_service = natural_language or SemanticNaturalLanguageAnalytics(analytics)
    worker = SequentialIngestionWorker(
        admin_store,
    )
    app.extensions["admin_store"] = admin_store
    app.extensions["analytics_store"] = analytics
    app.extensions["natural_language_analytics"] = query_service
    app.extensions["ingestion_worker"] = worker
    app.extensions["access_store"] = access
    limiter = query_rate_limiter
    if public_demo_enabled and limiter is None:
        limiter = QueryRateLimiter(
            per_client_limit=_environment_int("HELIGENT_QUERY_PER_MINUTE", 10),
            global_limit=_environment_int("HELIGENT_QUERY_PER_DAY", 250),
            max_concurrent=_environment_int("HELIGENT_QUERY_MAX_CONCURRENT", 2),
        )
    app.extensions["query_rate_limiter"] = limiter
    if start_worker:
        worker.start()

    @app.before_request
    def authenticate_maintenance_user() -> Response | None:
        request_id = request.headers.get("X-Request-ID", "").strip()[:200]
        request.environ["heligent.request_id"] = request_id or str(uuid4())
        if selected_auth_mode == "PUBLIC" or request.endpoint == "health":
            return None
        if selected_auth_mode == "LOCAL":
            request.environ["heligent.user"] = AccessUser(
                email="local-admin@heligent.invalid",
                display_name="Local administrator",
                role="ADMIN",
                active=True,
            )
            return None
        supplied_secret = request.headers.get("X-Heligent-Proxy-Secret", "")
        if not hmac.compare_digest(supplied_secret, proxy_secret):
            return jsonify(
                {"error": "Authenticated ngrok access is required", "code": "AUTH_REQUIRED"}
            ), 401
        try:
            email = normalize_email(request.headers.get("X-Heligent-Auth-Email"))
        except ValueError:
            return jsonify(
                {"error": "ngrok did not supply a valid user identity", "code": "AUTH_REQUIRED"}
            ), 401
        assert access is not None
        user = access.get_user(email)
        if user is None or not user.active:
            return jsonify(
                {
                    "error": "This identity has not been granted Heligent access",
                    "code": "ACCESS_NOT_GRANTED",
                }
            ), 403
        display_name = request.headers.get("X-Heligent-Auth-Name")
        request.environ["heligent.user"] = user
        try:
            access.touch_user(email, display_name)
        except Exception:
            LOGGER.exception("Unable to update maintenance-user last-seen timestamp")
        return None

    @app.after_request
    def security_and_audit(response: Response) -> Response:
        origin = request.headers.get("Origin")
        if origin in LOCAL_DEV_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, X-Requested-With"
            )
            response.headers["Vary"] = "Origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if public_demo_enabled or selected_auth_mode == "NGROK":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'none'; object-src 'none'; "
                "frame-ancestors 'none'; form-action 'self'; img-src 'self' data: https://basemaps.cartocdn.com; "
                "script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'"
            )
            if request.is_secure:
                response.headers["Strict-Transport-Security"] = (
                    "max-age=31536000; includeSubDomains"
                )
        response.headers["Cache-Control"] = (
            "no-store" if request.path.startswith("/api/") else "no-cache"
        )
        response.headers["X-Request-ID"] = request.environ.get(
            "heligent.request_id", ""
        )
        user = request.environ.get("heligent.user")
        if (
            access is not None
            and isinstance(user, AccessUser)
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        ):
            try:
                access.record_audit_event(
                    actor=user,
                    action=request.endpoint or "unknown",
                    method=request.method,
                    path=request.path,
                    request_id=request.environ.get("heligent.request_id", ""),
                    remote_address=request.remote_addr,
                    response_status=response.status_code,
                    target=(
                        ",".join(
                            f"{key}={value}"
                            for key, value in request.view_args.items()
                        )
                        if request.view_args
                        else None
                    ),
                )
            except Exception:
                LOGGER.exception("Unable to record maintenance audit event")
        return response

    @app.before_request
    def protect_mutations() -> None:
        if public_demo_enabled and request.endpoint not in PUBLIC_DEMO_ENDPOINTS:
            abort(404)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if selected_auth_mode != "PUBLIC":
                user = request.environ.get("heligent.user")
                required_role = MUTATION_MINIMUM_ROLE.get(
                    request.endpoint or "", "ADMIN"
                )
                if not isinstance(user, AccessUser) or not user.permits(required_role):
                    abort(403, description=f"{required_role} role required")
            expected_header = (
                "HeligentQuery"
                if request.endpoint == "natural_language_query"
                else "HeligentAdmin"
            )
            if request.headers.get("X-Requested-With") not in {
                expected_header,
                "HeligentAdmin",
            }:
                abort(403, description="Missing local admin request header")

    @app.errorhandler(ValueError)
    def invalid_request(exc: ValueError) -> tuple[dict[str, Any], int]:
        return {"error": str(exc)}, 400

    @app.errorhandler(403)
    def forbidden(exc: Exception) -> tuple[dict[str, Any], int]:
        description = getattr(exc, "description", None)
        return {"error": description or "This identity cannot perform that action"}, 403

    @app.errorhandler(QueryPlannerUnavailable)
    def query_planner_unavailable(exc: QueryPlannerUnavailable) -> tuple[dict[str, Any], int]:
        return {
            "error": str(exc),
            "code": "OPENAI_NOT_CONFIGURED" if not query_service.configured else "QUERY_PLANNER_UNAVAILABLE",
        }, 503

    @app.errorhandler(psycopg.errors.QueryCanceled)
    def analytics_query_timed_out(exc: psycopg.errors.QueryCanceled) -> tuple[dict[str, Any], int]:
        return {
            "error": (
                "The Explorer query exceeded the 30-second database limit. "
                "Try a shorter date range or a more specific filter."
            ),
            "code": "ANALYTICS_QUERY_TIMEOUT",
        }, 503

    @app.get("/health")
    def health() -> dict[str, Any]:
        result: dict[str, Any] = {"status": "ok"}
        if not public_demo_enabled:
            result["worker_enabled"] = start_worker
        return result

    def require_access_role(role: str) -> AccessUser:
        user = request.environ.get("heligent.user")
        if not isinstance(user, AccessUser) or not user.permits(role):
            abort(403, description=f"{role} role required")
        return user

    @app.get("/api/auth/session")
    def auth_session() -> dict[str, Any]:
        user = require_access_role("VIEWER")
        return {"auth_mode": selected_auth_mode, "user": user.public_json()}

    @app.get("/api/admin/users")
    def list_access_users() -> dict[str, Any]:
        require_access_role("ADMIN")
        if access is None:
            return {"users": []}
        return {"users": _json_ready(access.list_users())}

    @app.post("/api/admin/users")
    def upsert_access_user() -> dict[str, Any]:
        actor = require_access_role("ADMIN")
        if access is None:
            raise ValueError("External user management is unavailable in local mode")
        payload = request.get_json(silent=True) or {}
        row = access.upsert_user(
            normalize_email(payload.get("email")),
            role=normalize_role(payload.get("role")),
            display_name=payload.get("display_name"),
            active=payload.get("active", True),
            actor_email=actor.email,
        )
        return {"user": _json_ready(row)}

    @app.delete("/api/admin/users/<path:email>")
    def deactivate_access_user(email: str) -> dict[str, Any]:
        actor = require_access_role("ADMIN")
        if access is None:
            raise ValueError("External user management is unavailable in local mode")
        row = access.deactivate_user(email, actor_email=actor.email)
        if row is None:
            abort(404)
        return {"user": _json_ready(row)}

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        year, month = _parse_month(request.args.get("month"))
        result = admin_store.month_overview(year, month)
        result["ingestion_sources"] = [
            {
                "code": "DIRECT",
                "label": "Direct from ADSB.lol",
                "available": True,
            },
            {
                "code": "PI",
                "label": "Raspberry Pi archive API",
                "available": pi_source_configured,
            },
        ]
        return _json_ready(result)

    @app.get("/api/datasets/<utc_date>")
    def dataset_detail(utc_date: str) -> dict[str, Any]:
        requested_date = _parse_date(utc_date, historical_only=False)
        return _json_ready(admin_store.date_detail(requested_date))

    @app.get("/api/analytics/snapshot")
    def analytics_snapshot() -> dict[str, Any]:
        from_value = request.args.get("from")
        to_value = request.args.get("to")
        if (from_value is None) != (to_value is None):
            raise ValueError("Analytics requires both from and to dates")
        start_date = _parse_date(from_value, historical_only=False) if from_value else None
        end_date = _parse_date(to_value, historical_only=False) if to_value else None
        helicopter_value = request.args.get("helicopters", "false").lower()
        if helicopter_value not in {"true", "false"}:
            raise ValueError("helicopters must be true or false")
        snapshot_options: dict[str, Any] = {
            "type_code": request.args.get("type"),
            "helicopters_only": helicopter_value == "true",
        }
        if request.args.get("region") is not None:
            snapshot_options["region_query"] = request.args.get("region")
        result = analytics.snapshot(start_date, end_date, **snapshot_options)
        return _json_ready(result)

    @app.get("/api/company-sites")
    def company_sites() -> dict[str, Any]:
        tracked_value = request.args.get("tracked", "false").lower()
        if tracked_value not in {"true", "false"}:
            raise ValueError("tracked must be true or false")
        return {
            "sites": _json_ready(
                analytics.company_sites(
                    query=request.args.get("q"),
                    tracked_only=tracked_value == "true",
                    limit=100,
                )
            )
        }

    @app.get("/api/operators")
    def operators() -> dict[str, Any]:
        role = request.args.get("role", "OPERATOR").strip().upper()
        try:
            limit = int(request.args.get("limit", "100"))
        except ValueError as exc:
            raise ValueError("Operator result limit must be an integer") from exc
        return {
            "summary": _json_ready(analytics.operator_fleet_summary()),
            "assignments": _json_ready(
                analytics.operator_aircraft(
                    query=request.args.get("q"),
                    assignment_role=role,
                    limit=limit,
                )
            ),
        }

    @app.get("/api/operators/search")
    def operator_search() -> dict[str, Any]:
        return {
            "operators": _json_ready(
                analytics.operator_suggestions(request.args.get("q", ""), limit=8)
            )
        }

    @app.patch("/api/aircraft/<address>/operator")
    def set_aircraft_operator(address: str) -> dict[str, Any]:
        payload = request.get_json(silent=True) or {}
        result = analytics.set_aircraft_operator(
            address,
            operator=payload.get("operator"),
            registration=payload.get("registration"),
        )
        return {"assignment": _json_ready(result)}

    @app.get("/api/airports/search")
    def airport_search() -> dict[str, Any]:
        return {
            "airports": _json_ready(
                analytics.airport_search(request.args.get("q", ""), limit=25)
            )
        }

    @app.post("/api/company-sites/<int:site_id>/tracking")
    def update_company_site_tracking(site_id: int) -> dict[str, Any]:
        payload = request.get_json(silent=True) or {}
        result = analytics.update_company_site_tracking(
            site_id,
            airport_ident=payload.get("airport_ident"),
            is_of_interest=payload.get("is_of_interest", False),
            is_customer=payload.get("is_customer", False),
            interest_notes=payload.get("interest_notes"),
        )
        return {"site": _json_ready(result)}

    @app.get("/api/company-sites/<int:site_id>/activity")
    def company_site_activity(site_id: int) -> dict[str, Any]:
        requested_date = request.args.get("date")
        if requested_date:
            activity_date = _parse_date(requested_date, historical_only=False)
        else:
            activity_date = analytics.availability().get("latest_available_date")
            if activity_date is None:
                raise ValueError("No processed ADS-B date is available")
        return _json_ready(analytics.company_site_activity(site_id, activity_date))

    @app.get("/api/query/config")
    def query_config() -> dict[str, Any]:
        result = query_service.config()
        if public_demo_enabled and limiter is not None:
            result = {
                **result,
                "public_demo": True,
                "query_limit_per_minute": limiter.per_client_limit,
            }
        return _json_ready(result)

    @app.post("/api/query")
    def natural_language_query() -> dict[str, Any] | Response:
        payload = request.get_json(silent=True) or {}
        context = payload.get("context", [])
        if not isinstance(context, list):
            raise ValueError("Query context must be a list of recent questions")
        debug = payload.get("debug", False)
        if not isinstance(debug, bool):
            raise ValueError("Query debug must be true or false")
        if public_demo_enabled and debug:
            raise ValueError("Query debug is disabled on the public demo")

        decision: RateLimitDecision | None = None
        if public_demo_enabled and limiter is not None:
            decision = limiter.acquire(request.remote_addr or "unknown")
            if not decision.allowed:
                return _rate_limit_response(decision)
        try:
            result = query_service.ask(
                payload.get("question", ""),
                context=context,
                debug=debug,
            )
        finally:
            if decision is not None and limiter is not None:
                limiter.release()

        if decision is None:
            return _json_ready(result)
        response = jsonify(_json_ready(result))
        response.headers["X-RateLimit-Limit"] = str(decision.client_limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.client_remaining)
        return response

    @app.get('/api/maintenance/watches')
    def maintenance_watches():
        return jsonify(_json_ready(MaintenanceStore(admin_store).watches()))

    @app.get('/api/tools/sources')
    def tools_sources():
        require_access_role('VIEWER')
        from .source_tools import source_inventory
        return jsonify(_json_ready(source_inventory(admin_store)))

    @app.get('/api/tools/sources/<code>')
    def tools_source_detail(code):
        require_access_role('VIEWER')
        from .source_tools import source_detail
        return jsonify(_json_ready(source_detail(admin_store, code)))

    @app.post('/api/tools/sources/<code>/settings')
    def tools_source_settings(code):
        actor = require_access_role('ANALYST')
        from .source_tools import save_source_settings
        return jsonify(save_source_settings(admin_store, code, request.get_json(), actor.email))

    @app.post('/api/tools/sources/<code>/lookup')
    def tools_source_lookup(code):
        actor = require_access_role('ANALYST')
        from .aircraft_lookup import lookup_aircraft
        payload = request.get_json()
        if not isinstance(payload, dict):
            raise ValueError('Expected a hex lookup')
        return jsonify(_json_ready(lookup_aircraft(admin_store, code, payload.get('address'), actor.email)))

    @app.get('/api/tools/identity-gaps')
    def tools_identity_gaps():
        require_access_role('VIEWER')
        from .source_tools import identity_gaps
        return jsonify(_json_ready(identity_gaps(admin_store, request.args.get('from'), request.args.get('to'))))

    @app.post('/api/tools/tar1090/refresh')
    def tar1090_refresh():
        actor = require_access_role('ANALYST')
        from .tar1090 import refresh_snapshot
        return jsonify(_json_ready(refresh_snapshot(admin_store, actor.email)))

    @app.post('/api/tools/tar1090/preview')
    def tar1090_preview():
        actor = require_access_role('ANALYST')
        from .tar1090 import build_preview
        return jsonify(_json_ready(build_preview(admin_store, request.get_json(), actor.email)))

    @app.get('/api/tools/tar1090/previews/<preview_id>')
    def tar1090_preview_page(preview_id):
        require_access_role('VIEWER')
        from .tar1090 import preview_page
        return jsonify(_json_ready(preview_page(admin_store, preview_id, request.args)))

    @app.post('/api/tools/tar1090/identity/<action>')
    def tar1090_identity(action):
        actor = require_access_role('ANALYST')
        from .tar1090 import review_identity
        if action not in ('preview', 'assign'):
            abort(404)
        return jsonify(_json_ready(review_identity(admin_store, request.get_json(), actor.email, save=action == 'assign')))

    @app.get('/api/tools/unidentified')
    def unidentified_hexes():
        require_access_role('VIEWER')
        from .unidentified import unidentified_activity
        return jsonify(_json_ready(unidentified_activity(admin_store,
            request.args.get('from'), request.args.get('to'), int(request.args.get('offset','0')),
            request.args.get('category','ROTORCRAFT_UNKNOWN'), request.args.get('region','ALL'),
            request.args.get('search',''), gap=request.args.get('gap','SOURCE_TAIL'))))

    @app.post('/api/tools/identity/preview')
    def identity_preview():
        from .identity import assign_identity
        actor = require_access_role('ANALYST')
        return jsonify(_json_ready(assign_identity(admin_store, request.get_json(), actor.email)))

    @app.post('/api/tools/identity/assign')
    def identity_assign():
        from .identity import assign_identity
        actor = require_access_role('ANALYST')
        return jsonify(_json_ready(assign_identity(admin_store, request.get_json(), actor.email, save=True)))

    @app.post('/api/tools/lba/preview')
    def lba_preview():
        actor = require_access_role('ANALYST')
        from .lba import fetch_preview
        from .lba_import import stage_preview
        payload = request.get_json() or {}
        if not isinstance(payload, dict):
            raise ValueError('Expected a JSON object')
        return jsonify(stage_preview(admin_store, fetch_preview(payload.get('query')), actor.email))

    @app.post('/api/tools/lba/import')
    def lba_import():
        from .lba_import import import_selection
        actor = require_access_role('ANALYST')
        return jsonify(import_selection(admin_store, request.get_json(), actor.email))

    @app.get('/api/tools/lba/history')
    def lba_history():
        from .lba_import import import_history
        require_access_role('VIEWER')
        return jsonify(_json_ready(import_history(admin_store)))

    @app.get('/api/capability-mappings')
    def capability_mapping_search():
        return jsonify(_json_ready(CapabilityMappingStore(admin_store).search(
            request.args.get('q',''), int(request.args.get('offset','0')))))

    @app.get('/api/capability-mappings/<int:capability_id>')
    def capability_mapping_detail(capability_id):
        return jsonify(_json_ready(CapabilityMappingStore(admin_store).detail(capability_id)))

    @app.post('/api/capability-mappings/<int:capability_id>')
    def capability_mapping_save(capability_id):
        actor = require_access_role('ANALYST')
        return jsonify(_json_ready(CapabilityMappingStore(admin_store).save(
            capability_id, request.get_json() or {}, actor.email)))

    @app.post('/api/capability-mappings/<int:capability_id>/shared')
    def capability_phrase_save(capability_id):
        actor = require_access_role('ANALYST')
        return jsonify(_json_ready(CapabilityMappingStore(admin_store).save_shared(
            capability_id, request.get_json() or {}, actor.email)))

    @app.post('/api/maintenance/watches')
    def maintenance_add():
        user = request.environ['heligent.user']
        return jsonify(_json_ready(MaintenanceStore(admin_store).add(request.get_json() or {}, user.email))), 201

    @app.get('/api/maintenance/watches/<int:watch_id>')
    def maintenance_detail(watch_id):
        return jsonify(_json_ready(MaintenanceStore(admin_store).detail(watch_id)))

    @app.get('/api/maintenance/map-config')
    def maintenance_map_config():
        # CARTO basemap keys are browser-visible tile credentials, never server API tokens.
        return jsonify({'carto_key': os.getenv('HELIGENT_CARTO_BASEMAP_KEY', '')})

    @app.get('/api/maintenance/watches/<int:watch_id>/map')
    def maintenance_map(watch_id):
        from .maintenance_map import map_data
        return jsonify(_json_ready(map_data(admin_store, watch_id,
            request.args.get('from'), request.args.get('to'))))

    @app.post('/api/maintenance/watches/<int:watch_id>/reviews')
    def maintenance_review(watch_id):
        user = request.environ['heligent.user']
        return jsonify(_json_ready(MaintenanceStore(admin_store).review(watch_id, request.get_json() or {}, user.email))), 201

    @app.post('/api/maintenance/watches/<int:watch_id>/archive')
    def maintenance_archive(watch_id):
        MaintenanceStore(admin_store).archive(watch_id)
        return jsonify({'status': 'ARCHIVED'})

    @app.post("/api/queue/date")
    def queue_date() -> tuple[dict[str, Any], int]:
        payload = request.get_json(silent=True) or {}
        requested_date = _parse_date(payload.get("date"))
        raw_source = _parse_raw_source(
            payload.get("raw_source"), pi_configured=pi_source_configured
        )
        result = admin_store.enqueue_date(
            requested_date,
            reprocess=bool(payload.get("reprocess", False)),
            keep_raw=bool(payload.get("keep_raw", False)),
            raw_source=raw_source,
        )
        worker.notify()
        return _json_ready(result), 202 if result["outcome"] == "QUEUED" else 200

    @app.post("/api/queue/range")
    def queue_range() -> tuple[dict[str, Any], int]:
        payload = request.get_json(silent=True) or {}
        start_date = _parse_date(payload.get("from_date"))
        end_date = _parse_date(payload.get("to_date"))
        raw_source = _parse_raw_source(
            payload.get("raw_source"), pi_configured=pi_source_configured
        )
        result = admin_store.enqueue_range(
            start_date,
            end_date,
            reprocess=bool(payload.get("reprocess", False)),
            keep_raw=bool(payload.get("keep_raw", False)),
            raw_source=raw_source,
        )
        worker.notify()
        return _json_ready(result), 202

    @app.post("/api/datasets/<utc_date>/retry")
    def retry_date(utc_date: str) -> tuple[dict[str, Any], int]:
        payload = request.get_json(silent=True) or {}
        requested_date = _parse_date(utc_date)
        raw_source = _parse_raw_source(
            payload.get("raw_source"), pi_configured=pi_source_configured
        )
        result = admin_store.retry_date(
            requested_date,
            keep_raw=bool(payload.get("keep_raw", False)),
            raw_source=raw_source,
        )
        worker.notify()
        return _json_ready(result), 202

    @app.post("/api/datasets/<utc_date>/reprocess")
    def reprocess_date(utc_date: str) -> tuple[dict[str, Any], int]:
        payload = request.get_json(silent=True) or {}
        requested_date = _parse_date(utc_date)
        raw_source = _parse_raw_source(
            payload.get("raw_source"), pi_configured=pi_source_configured
        )
        result = admin_store.enqueue_date(
            requested_date,
            reprocess=True,
            keep_raw=bool(payload.get("keep_raw", False)),
            raw_source=raw_source,
        )
        worker.notify()
        return _json_ready(result), 202 if result["outcome"] == "QUEUED" else 200

    @app.post("/api/queue/<int:queue_id>/cancel")
    def cancel_queue_item(queue_id: int) -> tuple[dict[str, Any], int]:
        result = admin_store.cancel_queue_item(queue_id)
        if result is None:
            return {"error": "Only queued work can be cancelled"}, 409
        return _json_ready(result), 200

    @app.get("/")
    def index() -> Response:
        if public_demo_enabled:
            return redirect("/demo", code=302)
        if not (STATIC_ROOT / "index.html").is_file():
            return Response(
                "The Phase 5 frontend has not been built yet.",
                status=503,
                content_type="text/plain; charset=utf-8",
            )
        return send_from_directory(STATIC_ROOT, "index.html")

    @app.get("/demo")
    @app.get("/demo/")
    def mobile_demo() -> Response:
        if not (STATIC_ROOT / "index.html").is_file():
            return Response(
                "The Heligent frontend has not been built yet.",
                status=503,
                content_type="text/plain; charset=utf-8",
            )
        return send_from_directory(STATIC_ROOT, "index.html")

    @app.get("/<path:asset_path>")
    def static_asset(asset_path: str) -> Response:
        if public_demo_enabled and asset_path == "index.html":
            return redirect("/demo", code=302)
        return send_from_directory(STATIC_ROOT, asset_path)

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Heligent ADS-B analytics web application")
    parser.add_argument("--database-url", help="PostgreSQL connection URL")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5080)
    parser.add_argument("--no-worker", action="store_true")
    parser.add_argument(
        "--public-demo",
        action="store_true",
        help="Expose only the mobile demo and its read/query endpoints",
    )
    parser.add_argument(
        "--trusted-proxy-count",
        type=int,
        help="Number of trusted reverse proxies that set forwarded client headers",
    )
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
    app = create_app(
        args.database_url,
        start_worker=not args.no_worker,
        public_demo=True if args.public_demo else None,
        trusted_proxy_count=args.trusted_proxy_count,
    )
    LOGGER.info("Data-management application listening on http://%s:%s", args.host, args.port)
    serve(app, host=args.host, port=args.port, threads=6)


if __name__ == "__main__":
    main()
