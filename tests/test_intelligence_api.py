from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from adsb_ingest.intelligence_api import create_intelligence_api


class FakeIntelligenceService:
    def __init__(self) -> None:
        self.activity_calls = []
        self.daily_calls = []
        self.region_calls = []

    def availability(self):
        return {
            "earliest_available_date": date(2026, 8, 20),
            "latest_available_date": date(2026, 8, 22),
            "available_days": 3,
            "available_dates": [date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 22)],
        }

    def coverage(self, start_date, end_date):
        return {
            "requested_days": (end_date - start_date).days + 1,
            "available_days": 2,
            "complete": True,
            "available_dates": [start_date, end_date],
            "missing_dates": [],
        }

    def helicopter_activity(
        self, start_date, end_date, *, registrations, operator, airport, limit
    ):
        self.activity_calls.append(
            (start_date, end_date, registrations, operator, airport, limit)
        )
        return [{"registration": "G-TEST", "active_hours": 3.5}]

    def helicopter_days(self, registration, start_date, end_date):
        if registration == "G-MISS":
            return []
        return [{"utc_date": start_date, "registration": registration, "airports": []}]

    def airport_activity(self, airport, start_date, end_date, *, limit):
        return {
            "coverage": {"complete": True},
            "daily_activity": [{"utc_date": start_date, "unique_aircraft": 4}],
            "helicopters": [{"registration": "G-TEST"}],
            "types": [{"type_code": "EC35"}],
        }

    def aircraft_daily_activity(
        self, start_date, end_date, *, registrations, category
    ):
        self.daily_calls.append((start_date, end_date, registrations, category))
        return [
            {
                "utc_date": start_date,
                "registration": registrations[0],
                "category": "ROTORCRAFT",
                "active_hours": 2.5,
                "airports": [],
            }
        ]

    def region_aircraft_rankings(
        self,
        region_codes,
        start_date,
        end_date,
        *,
        category,
        operator_status,
        type_status,
        metric,
        limit,
    ):
        self.region_calls.append(
            (
                "aircraft",
                region_codes,
                category,
                operator_status,
                type_status,
                metric,
                limit,
            )
        )
        return [{"registration": "G-TEST", "active_hours": 12.5}]

    def region_type_breakdown(
        self, region_codes, start_date, end_date, *, category, limit
    ):
        self.region_calls.append(("types", region_codes, category, limit))
        return [{"type_code": "H145", "unique_aircraft": 8}]

    def region_airport_rankings(
        self,
        region_codes,
        start_date,
        end_date,
        *,
        category,
        metric,
        limit,
        compare_previous,
    ):
        self.region_calls.append(
            ("airports", region_codes, category, metric, limit, compare_previous)
        )
        return [
            {
                "airport_ident": "EGPD",
                "unique_aircraft": 20,
                "movement_candidates": 45,
                "previous_metric_value": 40 if compare_previous else None,
            }
        ]


class IntelligenceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeIntelligenceService()
        app = create_intelligence_api(token="service-secret", service=self.service)
        app.testing = True
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer service-secret"}

    def test_health_is_public_but_data_requires_bearer(self) -> None:
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json["service"], "heligent-intelligence-api")
        denied = self.client.get("/api/v1/coverage")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json["error"]["code"], "UNAUTHORIZED")
        self.assertTrue(denied.headers["X-Request-ID"])

    def test_environment_token_rejects_deployment_placeholders(self) -> None:
        with patch.dict(
            "os.environ",
            {"HELIGENT_API_TOKEN": "replace-with-a-long-random-service-token"},
        ):
            with self.assertRaisesRegex(RuntimeError, "non-placeholder"):
                create_intelligence_api(service=self.service)

    def test_coverage_serializes_dates(self) -> None:
        response = self.client.get("/api/v1/coverage", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["latest_available_date"], "2026-08-22")

    def test_batch_activity_normalizes_filters(self) -> None:
        response = self.client.post(
            "/api/v1/helicopters/activity",
            headers=self.headers,
            json={
                "from": "2026-08-20",
                "to": "2026-08-21",
                "registrations": ["g-test", "G-TEST"],
                "operator": "  Bristow   Helicopters ",
                "airport": "egpd",
                "limit": 25,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"][0]["registration"], "G-TEST")
        self.assertEqual(
            self.service.activity_calls[0],
            (
                date(2026, 8, 20),
                date(2026, 8, 21),
                ["G-TEST"],
                "Bristow Helicopters",
                "EGPD",
                25,
            ),
        )
        self.assertTrue(response.json["meta"]["coverage"]["complete"])

    def test_get_activity_defaults_to_latest_available_day(self) -> None:
        response = self.client.get(
            "/api/v1/helicopters/activity?registrations=G-TEST", headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["meta"]["from"], "2026-08-22")
        self.assertEqual(response.json["meta"]["to"], "2026-08-22")

    def test_tail_days_and_not_found(self) -> None:
        response = self.client.get(
            "/api/v1/helicopters/G-TEST/days?from=2026-08-20&to=2026-08-21",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"][0]["registration"], "G-TEST")
        missing = self.client.get(
            "/api/v1/helicopters/G-MISS/days?from=2026-08-20&to=2026-08-21",
            headers=self.headers,
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json["error"]["code"], "NOT_FOUND")

    def test_airport_activity_is_helicopter_specific(self) -> None:
        response = self.client.get(
            "/api/v1/airports/egpd/activity?from=2026-08-20&to=2026-08-21&limit=10",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["meta"]["airport"], "EGPD")
        self.assertEqual(response.json["data"]["types"][0]["type_code"], "EC35")

    def test_known_fleet_daily_activity_is_batched_and_reports_missing_tails(self) -> None:
        response = self.client.post(
            "/api/v1/aircraft/daily-activity",
            headers=self.headers,
            json={
                "from": "2026-08-20",
                "to": "2026-08-21",
                "registrations": ["g-test", "G-MISS"],
                "category": "all",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"][0]["registration"], "G-TEST")
        self.assertEqual(response.json["meta"]["registrations_without_activity"], ["G-MISS"])
        self.assertEqual(
            self.service.daily_calls[0],
            (date(2026, 8, 20), date(2026, 8, 21), ["G-TEST", "G-MISS"], "ALL"),
        )

    def test_region_endpoints_expose_rankings_types_and_observed_hubs(self) -> None:
        aircraft = self.client.get(
            "/api/v1/regions/europe/aircraft/rankings"
            "?from=2026-08-20&to=2026-08-21&category=fixed-wing"
            "&operator_status=unmatched&type_status=known&metric=observations&limit=25",
            headers=self.headers,
        )
        self.assertEqual(aircraft.status_code, 200)
        self.assertEqual(aircraft.json["meta"]["region"], "EUROPE")
        self.assertEqual(aircraft.json["data"][0]["registration"], "G-TEST")
        self.assertEqual(
            self.service.region_calls[0],
            (
                "aircraft",
                ("EUROPE",),
                "FIXED_WING",
                "UNMATCHED",
                "KNOWN",
                "observations",
                25,
            ),
        )

        types = self.client.get(
            "/api/v1/regions/europe/types?from=2026-08-20&to=2026-08-21",
            headers=self.headers,
        )
        self.assertEqual(types.status_code, 200)
        self.assertEqual(types.json["data"][0]["type_code"], "H145")

        hubs = self.client.get(
            "/api/v1/regions/europe/airports/rankings"
            "?from=2026-08-20&to=2026-08-21&compare_previous=true",
            headers=self.headers,
        )
        self.assertEqual(hubs.status_code, 200)
        self.assertEqual(hubs.json["data"][0]["airport_ident"], "EGPD")
        self.assertEqual(hubs.json["meta"]["previous_period"]["from"], "2026-08-18")
        self.assertIn("not an audited movement count", hubs.json["meta"]["methodology"])

    def test_region_and_daily_validation(self) -> None:
        empty = self.client.post(
            "/api/v1/aircraft/daily-activity",
            headers=self.headers,
            json={"from": "2026-08-20", "to": "2026-08-21", "registrations": []},
        )
        self.assertEqual(empty.status_code, 400)
        bad_region = self.client.get(
            "/api/v1/regions/moon/types?from=2026-08-20&to=2026-08-21",
            headers=self.headers,
        )
        self.assertEqual(bad_region.status_code, 400)
        bad_metric = self.client.get(
            "/api/v1/regions/europe/airports/rankings"
            "?from=2026-08-20&to=2026-08-21&metric=made_up",
            headers=self.headers,
        )
        self.assertEqual(bad_metric.status_code, 400)

    def test_rejects_partial_future_and_oversized_requests(self) -> None:
        partial = self.client.get(
            "/api/v1/helicopters/activity?from=2026-08-20", headers=self.headers
        )
        self.assertEqual(partial.status_code, 400)
        too_many = [f"G-{index:03d}" for index in range(101)]
        oversized = self.client.post(
            "/api/v1/helicopters/activity",
            headers=self.headers,
            json={"from": "2026-08-20", "to": "2026-08-21", "registrations": too_many},
        )
        self.assertEqual(oversized.status_code, 400)

    def test_malformed_json_and_unknown_routes_use_error_contract(self) -> None:
        malformed = self.client.post(
            "/api/v1/helicopters/activity",
            headers={**self.headers, "Content-Type": "application/json"},
            data="{broken",
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(malformed.json["error"]["code"], "INVALID_REQUEST")
        missing = self.client.get("/api/v1/missing", headers=self.headers)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json["error"]["code"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
