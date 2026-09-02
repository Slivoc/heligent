from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from adsb_ingest.webapp import create_app
from adsb_ingest.access import AccessUser
from adsb_ingest.public_access import QueryRateLimiter


class FakeAdminStore:
    dsn = "postgresql://example.invalid/test"

    def __init__(self) -> None:
        self.queued: list[tuple[date, bool, bool, str]] = []
        self.queued_ranges: list[tuple[date, date, bool, bool, str]] = []

    def apply_phase3_migration(self) -> None:
        return None

    def apply_phase13_migration(self) -> None:
        return None

    def month_overview(self, year: int, month: int):
        return {
            "month": f"{year:04d}-{month:02d}",
            "first_date": date(year, month, 1),
            "last_date": date(year, month, 28),
            "days": [
                {
                    "utc_date": date(2026, 8, 20),
                    "status": "PROCESSED",
                    "source_aircraft_count": 83_336,
                }
            ],
            "summary": {
                "processed_days": 1,
                "active_days": 0,
                "failed_days": 0,
                "latest_processed_date": date(2026, 8, 20),
                "aircraft_day_rows": 83_336,
            },
            "current_queue_item": None,
            "recent_queue": [],
            "latest_ingestion": None,
        }

    def date_detail(self, utc_date: date):
        return {"utc_date": utc_date, "dataset": None, "jobs": [], "queue_history": []}

    def enqueue_date(
        self,
        utc_date: date,
        *,
        reprocess: bool = False,
        keep_raw: bool = False,
        raw_source: str = "DIRECT",
    ):
        self.queued.append((utc_date, reprocess, keep_raw, raw_source))
        return {"outcome": "QUEUED", "item": {"id": len(self.queued), "utc_date": utc_date}}

    def enqueue_range(
        self,
        start_date: date,
        end_date: date,
        *,
        reprocess: bool = False,
        keep_raw: bool = False,
        raw_source: str = "DIRECT",
    ):
        self.queued_ranges.append(
            (start_date, end_date, reprocess, keep_raw, raw_source)
        )
        return {"requested_days": (end_date - start_date).days + 1, "queued_days": 2, "skipped_days": 0, "results": []}

    def retry_date(
        self,
        utc_date: date,
        *,
        keep_raw: bool = False,
        raw_source: str = "DIRECT",
    ):
        return self.enqueue_date(
            utc_date, reprocess=True, keep_raw=keep_raw, raw_source=raw_source
        )

    def cancel_queue_item(self, queue_id: int):
        return {"id": queue_id, "utc_date": date(2026, 8, 19), "status": "CANCELLED"}


class FakeAnalyticsStore:
    def __init__(self) -> None:
        self.calls: list[tuple[date | None, date | None, str | None, bool]] = []
        self.operator_updates = []

    def apply_phase4_migration(self) -> None:
        return None

    def apply_phase6_migration(self) -> None:
        return None

    def refresh_type_classification(self) -> None:
        return None

    def availability(self):
        return {
            "earliest_available_date": date(2026, 8, 20),
            "latest_available_date": date(2026, 8, 20),
            "available_days": 1,
            "available_dates": [date(2026, 8, 20)],
        }

    def snapshot(
        self,
        start_date: date | None,
        end_date: date | None,
        *,
        type_code: str | None = None,
        helicopters_only: bool = False,
    ):
        self.calls.append((start_date, end_date, type_code, helicopters_only))
        return {
            "selection": {"from_date": start_date, "to_date": end_date},
            "coverage": {
                "requested_days": 2,
                "available_days": 1,
                "complete": False,
                "message": "Results below are partial.",
            },
            "totals": {"unique_aircraft": 83_336},
            "daily_activity": [],
            "types": [],
            "type_options": [],
            "tails": [],
            "hubs": [],
            "helicopters": {"totals": {}, "types": []},
        }

    def company_sites(self, *, query=None, tracked_only=False, limit=50):
        return [{
            "site_id": 5,
            "company_id": 2,
            "company": "A2B Heli (Maintenance) Limited",
            "is_customer": False,
            "is_mro": True,
            "is_operator": False,
            "site": "Redhill Primary Site",
            "locality": "Redhill",
            "postal_code": "RH1 5JY",
            "country_code": "GB",
            "is_of_interest": False,
            "airport_ident": None,
            "approval_numbers": "UK.145.01318",
            "capability_count": 8,
        }]

    def operator_fleet_summary(self):
        return {
            "current_assignments": 2,
            "operator_assignments": 1,
            "companies_with_assignments": 2,
            "unique_aircraft": 1,
            "conflicting_aircraft": 1,
        }

    def operator_aircraft(
        self, *, query=None, company_query=None, registration_query=None,
        region_query=None, assignment_role="OPERATOR", limit=100
    ):
        return [{
            "company": "Bristow Helicopters Limited",
            "registration": "G-TEST",
            "aircraft_address": "400abc",
            "assignment_role": assignment_role,
            "confidence": 0.7,
            "valid_from": date(2026, 8, 23),
            "valid_to": None,
            "source_code": "CRM_OPERATOR_TAILS",
            "source_assignment_id": "crm-op-test",
        }]

    def operator_suggestions(self, query, *, limit=8):
        return [{"name": "Bristow Helicopters Limited", "source_code": "COMPANY_DIRECTORY"}]

    def set_aircraft_operator(self, address, *, operator, registration=None):
        self.operator_updates.append((address, operator, registration))
        return {
            "address": address.lower(),
            "registration": registration,
            "operator": operator,
            "operator_source_code": "ADMIN_OPERATOR_OVERRIDE",
        }

    def airport_search(self, query, *, limit=20):
        return [{
            "ident": "EGKR",
            "iata_code": "KRH",
            "name": "Redhill Aerodrome",
            "municipality": "Redhill",
            "iso_country": "GB",
        }]

    def update_company_site_tracking(self, site_id, **kwargs):
        return {
            **self.company_sites()[0],
            "site_id": site_id,
            "airport_ident": kwargs["airport_ident"],
            "is_of_interest": kwargs["is_of_interest"],
            "is_customer": kwargs["is_customer"],
            "interest_notes": kwargs["interest_notes"],
        }

    def company_site_activity(self, site_id, utc_date):
        return {
            "utc_date": utc_date,
            "site": {**self.company_sites()[0], "site_id": site_id, "airport_ident": "EGKR"},
            "capabilities": [],
            "airport_metrics": {
                "airport_ident": "EGKR",
                "unique_aircraft": 12,
                "arrival_candidates": 4,
                "departure_candidates": 3,
                "movement_candidates": 7,
            },
            "aircraft": [],
            "types": [],
            "attribution_note": "Airport activity only.",
        }


class FakeNaturalLanguage:
    configured = True
    model = "test-model"

    def __init__(self) -> None:
        self.calls = []

    def config(self):
        return {
            "configured": True,
            "model": self.model,
            "availability": {"latest_available_date": date(2026, 8, 20)},
            "examples": ["Which hubs were busiest?"],
        }

    def ask(self, question, *, context, debug=False):
        self.calls.append((question, context, debug))
        return {
            "question": question,
            "coverage": {"complete": True},
            "answer": "ORD ranks first.",
            "table": {"columns": ["airport"], "rows": [{"airport": "ORD"}]},
            "chart": {"type": "bar", "x": "airport", "y": "unique_aircraft"},
        }


class FakeAccessStore:
    def __init__(self) -> None:
        self.users = {
            "viewer@example.com": AccessUser(
                "viewer@example.com", "View User", "VIEWER", True
            ),
            "analyst@example.com": AccessUser(
                "analyst@example.com", "Analysis User", "ANALYST", True
            ),
            "admin@example.com": AccessUser(
                "admin@example.com", "Admin User", "ADMIN", True
            ),
        }
        self.audits = []

    def apply_migration(self):
        return None

    def bootstrap_admins(self, emails):
        return 0

    def get_user(self, email):
        return self.users.get(email)

    def active_admin_count(self):
        return sum(user.role == "ADMIN" and user.active for user in self.users.values())

    def touch_user(self, email, display_name):
        return None

    def list_users(self):
        return [user.public_json() for user in self.users.values()]

    def upsert_user(
        self, email, *, role, display_name=None, active=True, actor_email=None
    ):
        user = AccessUser(email, display_name, role, active)
        self.users[email] = user
        return user.public_json()

    def deactivate_user(self, email, *, actor_email):
        user = self.users.get(email)
        if user is None:
            return None
        updated = AccessUser(user.email, user.display_name, user.role, False)
        self.users[email] = updated
        return updated.public_json()

    def record_audit_event(self, **event):
        self.audits.append(event)

class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeAdminStore()
        self.analytics = FakeAnalyticsStore()
        self.natural_language = FakeNaturalLanguage()
        self.app = create_app(
            start_worker=False,
            store=self.store,  # type: ignore[arg-type]
            analytics_store=self.analytics,  # type: ignore[arg-type]
            natural_language=self.natural_language,  # type: ignore[arg-type]
        )
        self.client = self.app.test_client()
        self.headers = {"X-Requested-With": "HeligentAdmin"}

    def test_overview_and_date_detail_are_json_safe(self) -> None:
        response = self.client.get("/api/overview?month=2026-08")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["summary"]["processed_days"], 1)
        self.assertEqual(response.json["days"][0]["utc_date"], "2026-08-20")
        self.assertEqual(
            response.json["ingestion_sources"],
            [
                {
                    "available": True,
                    "code": "DIRECT",
                    "label": "Direct from ADSB.lol",
                },
                {
                    "available": False,
                    "code": "PI",
                    "label": "Raspberry Pi archive API",
                },
            ],
        )

        detail = self.client.get("/api/datasets/2026-08-20")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json["utc_date"], "2026-08-20")

    def test_mutations_require_local_admin_header(self) -> None:
        response = self.client.post("/api/queue/date", json={"date": "2026-08-19"})
        self.assertEqual(response.status_code, 403)

    def test_ngrok_identity_authentication_roles_and_audit(self) -> None:
        access = FakeAccessStore()
        with patch.dict(
            "os.environ",
            {
                "HELIGENT_AUTH_PROXY_SECRET": "s" * 40,
                "HELIGENT_BOOTSTRAP_ADMIN_EMAILS": "",
            },
            clear=False,
        ):
            app = create_app(
                start_worker=False,
                store=self.store,  # type: ignore[arg-type]
                analytics_store=self.analytics,  # type: ignore[arg-type]
                natural_language=self.natural_language,  # type: ignore[arg-type]
                access_store=access,
                auth_mode="NGROK",
            )
        client = app.test_client()
        proxy = {"X-Heligent-Proxy-Secret": "s" * 40}

        self.assertEqual(client.get("/api/overview").status_code, 401)
        unknown = client.get(
            "/api/overview",
            headers={**proxy, "X-Heligent-Auth-Email": "unknown@example.com"},
        )
        self.assertEqual(unknown.status_code, 403)

        viewer = {
            **proxy,
            "X-Heligent-Auth-Email": "viewer@example.com",
            "X-Heligent-Auth-Name": "View User",
        }
        self.assertEqual(client.get("/api/overview", headers=viewer).status_code, 200)
        denied = client.post(
            "/api/queue/date",
            json={"date": "2026-08-19"},
            headers={**viewer, **self.headers},
        )
        self.assertEqual(denied.status_code, 403)

        analyst = {
            **proxy,
            "X-Heligent-Auth-Email": "analyst@example.com",
            **self.headers,
        }
        queued = client.post(
            "/api/queue/date", json={"date": "2026-08-19"}, headers=analyst
        )
        self.assertEqual(queued.status_code, 202)
        self.assertEqual(access.audits[-1]["actor"].email, "analyst@example.com")
        self.assertEqual(access.audits[-1]["action"], "queue_date")

        admin = {
            **proxy,
            "X-Heligent-Auth-Email": "admin@example.com",
            **self.headers,
        }
        created = client.post(
            "/api/admin/users",
            json={"email": "colleague@example.com", "role": "VIEWER"},
            headers=admin,
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json["user"]["role"], "VIEWER")
        self.assertEqual(
            client.get("/api/admin/users", headers=viewer).status_code, 403
        )

    def test_analytics_snapshot_validates_and_forwards_filters(self) -> None:
        response = self.client.get(
            "/api/analytics/snapshot?from=2026-08-19&to=2026-08-20"
            "&type=H145&helicopters=true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["coverage"]["available_days"], 1)
        self.assertEqual(
            self.analytics.calls[-1],
            (date(2026, 8, 19), date(2026, 8, 20), "H145", True),
        )

        missing_to = self.client.get("/api/analytics/snapshot?from=2026-08-19")
        self.assertEqual(missing_to.status_code, 400)
        invalid_toggle = self.client.get("/api/analytics/snapshot?helicopters=maybe")
        self.assertEqual(invalid_toggle.status_code, 400)

    def test_queue_date_validates_and_forwards_options(self) -> None:
        response = self.client.post(
            "/api/queue/date",
            json={"date": "2026-08-19", "keep_raw": True},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["outcome"], "QUEUED")
        self.assertEqual(
            self.store.queued,
            [(date(2026, 8, 19), False, True, "DIRECT")],
        )

        future = self.client.post(
            "/api/queue/date",
            json={"date": "2999-01-01"},
            headers=self.headers,
        )
        self.assertEqual(future.status_code, 400)

    def test_queue_range_can_explicitly_reprocess_completed_dates(self) -> None:
        response = self.client.post(
            "/api/queue/range",
            json={
                "from_date": "2026-08-01",
                "to_date": "2026-08-07",
                "reprocess": True,
                "keep_raw": False,
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            self.store.queued_ranges,
            [(date(2026, 8, 1), date(2026, 8, 7), True, False, "DIRECT")],
        )

    def test_pi_raw_source_requires_server_config_and_is_forwarded(self) -> None:
        unavailable = self.client.post(
            "/api/queue/date",
            json={"date": "2026-08-19", "raw_source": "PI"},
            headers=self.headers,
        )
        self.assertEqual(unavailable.status_code, 400)
        self.assertIn("not configured", unavailable.json["error"])

        configured_store = FakeAdminStore()
        with patch.dict(
            "os.environ",
            {
                "ADSB_ARCHIVE_API_URL": "https://adsb-archive.example.ts.net",
                "ADSB_ARCHIVE_API_TOKEN": "fixture-token",
            },
        ):
            app = create_app(
                start_worker=False,
                store=configured_store,  # type: ignore[arg-type]
                analytics_store=FakeAnalyticsStore(),  # type: ignore[arg-type]
                natural_language=FakeNaturalLanguage(),  # type: ignore[arg-type]
            )
        client = app.test_client()
        response = client.post(
            "/api/queue/date",
            json={"date": "2026-08-19", "raw_source": "PI"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            configured_store.queued,
            [(date(2026, 8, 19), False, False, "PI")],
        )

    def test_manual_customer_site_mapping_and_activity_endpoints(self) -> None:
        sites = self.client.get("/api/company-sites?q=A2B&tracked=false")
        self.assertEqual(sites.status_code, 200)
        self.assertEqual(sites.json["sites"][0]["approval_numbers"], "UK.145.01318")

        airports = self.client.get("/api/airports/search?q=Redhill")
        self.assertEqual(airports.status_code, 200)
        self.assertEqual(airports.json["airports"][0]["ident"], "EGKR")

        missing_header = self.client.post(
            "/api/company-sites/5/tracking",
            json={"airport_ident": "EGKR", "is_customer": True, "is_of_interest": True},
        )
        self.assertEqual(missing_header.status_code, 403)

        saved = self.client.post(
            "/api/company-sites/5/tracking",
            json={
                "airport_ident": "EGKR",
                "is_customer": True,
                "is_of_interest": True,
                "interest_notes": "Priority customer",
            },
            headers=self.headers,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json["site"]["is_customer"])
        self.assertEqual(saved.json["site"]["airport_ident"], "EGKR")

        activity = self.client.get("/api/company-sites/5/activity?date=2026-08-20")
        self.assertEqual(activity.status_code, 200)
        self.assertEqual(activity.json["airport_metrics"]["arrival_candidates"], 4)

    def test_operator_fleet_endpoint_returns_current_assignment_evidence(self) -> None:
        response = self.client.get("/api/operators?q=G-TEST&role=OPERATOR")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["summary"]["unique_aircraft"], 1)
        self.assertEqual(response.json["summary"]["conflicting_aircraft"], 1)
        self.assertEqual(response.json["assignments"][0]["registration"], "G-TEST")
        self.assertEqual(
            response.json["assignments"][0]["source_code"], "CRM_OPERATOR_TAILS"
        )

        suggestions = self.client.get("/api/operators/search?q=bris")
        self.assertEqual(suggestions.status_code, 200)
        self.assertEqual(
            suggestions.json["operators"][0]["name"],
            "Bristow Helicopters Limited",
        )

    def test_manual_operator_entry_requires_admin_header_and_forwards_tail(self) -> None:
        payload = {"operator": "Bristow Helicopters", "registration": "G-TEST"}
        forbidden = self.client.patch("/api/aircraft/400abc/operator", json=payload)
        self.assertEqual(forbidden.status_code, 403)

        response = self.client.patch(
            "/api/aircraft/400abc/operator", json=payload, headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["assignment"]["operator"], "Bristow Helicopters")
        self.assertEqual(
            self.analytics.operator_updates[-1],
            ("400abc", "Bristow Helicopters", "G-TEST"),
        )

    def test_natural_language_config_and_query(self) -> None:
        config = self.client.get("/api/query/config")
        self.assertEqual(config.status_code, 200)
        self.assertTrue(config.json["configured"])
        self.assertEqual(config.json["availability"]["latest_available_date"], "2026-08-20")

        missing_header = self.client.post("/api/query", json={"question": "Which hubs?"})
        self.assertEqual(missing_header.status_code, 403)

        response = self.client.post(
            "/api/query",
            json={
                "question": "Which hubs?",
                "context": ["Show the latest day"],
                "debug": True,
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["table"]["rows"][0]["airport"], "ORD")
        self.assertEqual(
            self.natural_language.calls[-1],
            ("Which hubs?", ["Show the latest day"], True),
        )

    def test_mobile_demo_route_serves_the_spa(self) -> None:
        response = self.client.get("/demo")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/html", response.content_type)
            self.assertIn(b'<div id="root"></div>', response.data)
        finally:
            response.close()

    def test_reprocess_is_explicit(self) -> None:
        response = self.client.post(
            "/api/datasets/2026-08-20/reprocess",
            json={},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            self.store.queued[-1],
            (date(2026, 8, 20), True, False, "DIRECT"),
        )

    def test_public_demo_blocks_admin_routes_and_rate_limits_queries(self) -> None:
        limiter = QueryRateLimiter(
            per_client_limit=2,
            global_limit=10,
            max_concurrent=1,
        )
        public_app = create_app(
            start_worker=False,
            store=self.store,  # type: ignore[arg-type]
            analytics_store=self.analytics,  # type: ignore[arg-type]
            natural_language=self.natural_language,  # type: ignore[arg-type]
            public_demo=True,
            query_rate_limiter=limiter,
        )
        client = public_app.test_client()

        root = client.get("/")
        self.assertEqual(root.status_code, 302)
        self.assertEqual(root.headers["Location"], "/demo")
        self.assertEqual(client.get("/index.html").headers["Location"], "/demo")
        self.assertIn("default-src 'self'", root.headers["Content-Security-Policy"])
        self.assertEqual(client.get("/api/overview").status_code, 404)
        self.assertEqual(
            client.post(
                "/api/queue/date",
                json={"date": "2026-08-19"},
                headers=self.headers,
            ).status_code,
            404,
        )

        config = client.get("/api/query/config")
        self.assertTrue(config.json["public_demo"])
        self.assertEqual(config.json["query_limit_per_minute"], 2)

        query_headers = {"X-Requested-With": "HeligentQuery"}
        first = client.post(
            "/api/query",
            json={"question": "Which hubs?", "context": [], "debug": False},
            headers=query_headers,
        )
        second = client.post(
            "/api/query",
            json={"question": "Which types?", "context": [], "debug": False},
            headers=query_headers,
        )
        limited = client.post(
            "/api/query",
            json={"question": "Which tails?", "context": [], "debug": False},
            headers=query_headers,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["X-RateLimit-Remaining"], "1")
        self.assertEqual(second.headers["X-RateLimit-Remaining"], "0")
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json["code"], "client_rate_limit")
        self.assertIn("Retry-After", limited.headers)

    def test_public_demo_rejects_debug_and_requires_query_header(self) -> None:
        public_app = create_app(
            start_worker=False,
            store=self.store,  # type: ignore[arg-type]
            analytics_store=self.analytics,  # type: ignore[arg-type]
            natural_language=self.natural_language,  # type: ignore[arg-type]
            public_demo=True,
            query_rate_limiter=QueryRateLimiter(),
        )
        client = public_app.test_client()

        missing_header = client.post(
            "/api/query",
            json={"question": "Which hubs?", "context": [], "debug": False},
        )
        self.assertEqual(missing_header.status_code, 403)
        debug = client.post(
            "/api/query",
            json={"question": "Which hubs?", "context": [], "debug": True},
            headers={"X-Requested-With": "HeligentQuery"},
        )
        self.assertEqual(debug.status_code, 400)
        self.assertIn("disabled", debug.json["error"])

    def test_forwarded_client_ip_is_used_only_for_an_explicitly_trusted_proxy(self) -> None:
        query_headers = {"X-Requested-With": "HeligentQuery"}

        def public_client(*, trusted_proxy_count: int):
            limiter = QueryRateLimiter(per_client_limit=1, global_limit=10)
            app = create_app(
                start_worker=False,
                store=self.store,  # type: ignore[arg-type]
                analytics_store=self.analytics,  # type: ignore[arg-type]
                natural_language=self.natural_language,  # type: ignore[arg-type]
                public_demo=True,
                trusted_proxy_count=trusted_proxy_count,
                query_rate_limiter=limiter,
            )
            return app.test_client()

        direct = public_client(trusted_proxy_count=0)
        first = direct.post(
            "/api/query",
            json={"question": "Which hubs?"},
            headers={**query_headers, "X-Forwarded-For": "203.0.113.10"},
        )
        spoofed = direct.post(
            "/api/query",
            json={"question": "Which types?"},
            headers={**query_headers, "X-Forwarded-For": "203.0.113.11"},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(spoofed.status_code, 429)

        proxied = public_client(trusted_proxy_count=1)
        visitor_one = proxied.post(
            "/api/query",
            json={"question": "Which hubs?"},
            headers={**query_headers, "X-Forwarded-For": "203.0.113.10"},
        )
        visitor_two = proxied.post(
            "/api/query",
            json={"question": "Which types?"},
            headers={**query_headers, "X-Forwarded-For": "203.0.113.11"},
        )
        self.assertEqual(visitor_one.status_code, 200)
        self.assertEqual(visitor_two.status_code, 200)


if __name__ == "__main__":
    unittest.main()
