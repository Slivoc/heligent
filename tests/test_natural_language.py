from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from adsb_ingest.natural_language import (
    NaturalLanguageAnalytics,
    OpenAIQueryPlanner,
    QueryPlannerUnavailable,
    normalize_plan_for_question,
    validate_query_plan,
)


class FakePlanner:
    configured = True
    model = "test-model"

    def __init__(self, plan):
        self.next_plan = plan
        self.calls = []

    def plan(self, question, *, availability, context):
        self.calls.append((question, availability, context))
        return self.next_plan


class FakeAnalytics:
    def __init__(self):
        self.calls = []
        self.airport_calls = []
        self.airport_type_calls = []
        self.company_directory_calls = []
        self.company_approval_calls = []
        self.company_capability_calls = []
        self.operator_aircraft_calls = []
        self.operator_tail_activity_calls = []
        self.snapshot_region_calls = []
        self.hub_ranking_calls = []
        self.tail_ranking_calls = []
        self.airport_daily_activity_calls = []

    def availability(self):
        return {
            "earliest_available_date": date(2025, 8, 21),
            "latest_available_date": date(2026, 8, 20),
            "available_days": 2,
            "available_dates": [date(2025, 8, 21), date(2026, 8, 20)],
            "company_data": {
                "companies": 281,
                "mros": 281,
                "operators": 1,
                "sites": 281,
                "valid_approvals": 284,
                "capabilities": 2329,
                "aircraft_assignments": 1,
            },
        }

    def snapshot(
        self,
        start_date,
        end_date,
        *,
        type_code=None,
        helicopters_only=False,
        region_query=None,
    ):
        self.calls.append((start_date, end_date, type_code, helicopters_only))
        self.snapshot_region_calls.append(region_query)
        return {
            "coverage": {
                "requested_days": 1,
                "available_days": 1,
                "complete": True,
                "available_dates": [date(2026, 8, 20)],
                "missing_dates": [],
                "message": "All 1 requested UTC days are available locally.",
            },
            "totals": {
                "unique_aircraft": 83_036,
                "observations": 110_486_395,
                "active_hours": 206_031.5,
                "airborne_hours": 197_601.99,
                "ground_active_hours": 8_429.51,
                "known_type_codes": 885,
                "airport_linked_aircraft": 36_382,
            },
            "daily_activity": [
                {"utc_date": date(2026, 8, 20), "unique_aircraft": 83_036}
            ],
            "types": [
                {"type_code": "B738", "description": "Boeing 737-800", "unique_aircraft": 2_100},
                {"type_code": "A320", "description": "Airbus A320", "unique_aircraft": 1_900},
            ],
            "tails": [
                {
                    "address": "abc123",
                    "registration": "N123AB",
                    "operator": "Example Air",
                    "operator_source_code": "CRM_OPERATOR_TAILS",
                    "type_code": "B738",
                    "primary_airport_label": "ORD",
                    "airborne_hours": 22.5,
                }
            ],
            "hubs": [
                {
                    "airport_ident": "KORD",
                    "iata_code": "ORD",
                    "airport_name": "Chicago O'Hare International Airport",
                    "unique_aircraft": 1_024,
                },
                {
                    "airport_ident": "KDEN",
                    "iata_code": "DEN",
                    "airport_name": "Denver International Airport",
                    "unique_aircraft": 824,
                },
            ],
            "helicopters": {
                "types": [
                    {"type_code": "H145", "description": "Airbus H145", "unique_aircraft": 210}
                ]
            },
        }

    def hub_ranking(self, start_date, end_date, **kwargs):
        self.hub_ranking_calls.append((start_date, end_date, kwargs))
        return self.snapshot(
            start_date,
            end_date,
            type_code=kwargs.get("type_code"),
            helicopters_only=kwargs.get("helicopters_only", False),
            region_query=kwargs.get("region_query"),
        )

    def tail_ranking(self, start_date, end_date, **kwargs):
        self.tail_ranking_calls.append((start_date, end_date, kwargs))
        result = self.snapshot(
            start_date,
            end_date,
            type_code=kwargs.get("type_code"),
            helicopters_only=kwargs.get("helicopters_only", False),
            region_query=kwargs.get("region_query"),
        )
        for row in result["tails"]:
            row["activity_area"] = (
                kwargs.get("region_query") or kwargs.get("location_query")
            )
        return result

    def airport_daily_activity(self, start_date, end_date, **kwargs):
        self.airport_daily_activity_calls.append((start_date, end_date, kwargs))
        return self.snapshot(
            start_date,
            end_date,
            type_code=kwargs.get("type_code"),
            helicopters_only=kwargs.get("helicopters_only", False),
        )

    def airport_aircraft(
        self,
        start_date,
        end_date,
        *,
        airport_code,
        type_code=None,
        helicopters_only=False,
        metric="airport_ground_observations",
        limit=25,
    ):
        self.airport_calls.append(
            (
                start_date,
                end_date,
                airport_code,
                type_code,
                helicopters_only,
                metric,
                limit,
            )
        )
        return [
            {
                "address": "400abc",
                "registration": "G-TEST",
                "operator": "Bristow Helicopters Limited",
                "operator_source_code": "CRM_OPERATOR_TAILS",
                "type_code": "A320",
                "airport": "ABZ",
                "airport_arrival_candidates": 1,
                "airport_departure_candidates": 1,
                "airport_movement_candidates": 2,
                "activity_evidence": "Inferred endpoint",
                "airport_ground_observations": 14,
                "airport_ground_active_hours": 0.2,
                "active_hours": 8.5,
                "airborne_hours": 7.9,
            }
        ]

    def airport_types(
        self,
        start_date,
        end_date,
        *,
        airport_code,
        type_code=None,
        helicopters_only=False,
        metric="unique_aircraft",
        limit=25,
    ):
        self.airport_type_calls.append(
            (
                start_date,
                end_date,
                airport_code,
                type_code,
                helicopters_only,
                metric,
                limit,
            )
        )
        return [
            {
                "type_code": "EC75",
                "description": "AIRBUS HELICOPTERS EC-175",
                "unique_aircraft": 14,
                "observations": 8_000,
                "active_hours": 24.0,
                "airborne_hours": 23.5,
                "ground_observations": 0,
                "ground_active_hours": 0.0,
                "arrival_candidates": 14,
                "departure_candidates": 13,
                "movement_candidates": 27,
                "airport": "ABZ",
                "airport_name": "Aberdeen International Airport",
            },
            {
                "type_code": "S92",
                "description": "SIKORSKY S-92 Helibus",
                "unique_aircraft": 9,
                "observations": 5_000,
                "active_hours": 12.0,
                "airborne_hours": 11.8,
                "ground_observations": 0,
                "ground_active_hours": 0.0,
                "arrival_candidates": 9,
                "departure_candidates": 9,
                "movement_candidates": 18,
                "airport": "ABZ",
                "airport_name": "Aberdeen International Airport",
            },
        ]

    def company_directory(self, **kwargs):
        self.company_directory_calls.append(kwargs)
        return [{
            "company": "A2B Heli (Maintenance) Limited",
            "roles": "MRO",
            "sites": "Redhill Primary Site",
            "locations": "Redhill, Surrey, RH1 5JY, GB",
            "approval_numbers": "UK.145.01318",
            "site_count": 1,
            "valid_approval_count": 1,
            "capability_count": 8,
            "assigned_aircraft_count": 0,
        }]

    def company_approvals(self, **kwargs):
        self.company_approval_calls.append(kwargs)
        return [{
            "company": "A2B Heli (Maintenance) Limited",
            "authority_code": "UK_CAA",
            "approval_type": "PART_145",
            "approval_number": "UK.145.01318",
            "status": "VALID",
            "sites": "Redhill Primary Site",
            "locations": "Redhill, Surrey, RH1 5JY, GB",
            "capability_count": 8,
        }]

    def company_capabilities(self, **kwargs):
        self.company_capability_calls.append(kwargs)
        return [{
            "company": "A2B Heli (Maintenance) Limited",
            "approval_number": "UK.145.01318",
            "approval_status": "VALID",
            "capability_kind": "AIRCRAFT",
            "rating_code": "A3",
            "manufacturer": None,
            "model": None,
            "aircraft_type_code": None,
            "capability": "Aircraft\\A3 HELICOPTERS AIRBUS HELICOPTERS EC135",
            "sites": "Redhill Primary Site",
            "locations": "Redhill, Surrey, RH1 5JY, GB",
            "matching_capabilities": 1,
        }]

    def operator_aircraft(self, **kwargs):
        self.operator_aircraft_calls.append(kwargs)
        return [{
            "company": "Bristow Helicopters Limited",
            "legal_name": "Bristow Helicopters Limited",
            "trading_name": None,
            "country": "GB",
            "registration": "G-TEST",
            "aircraft_address": "400abc",
            "assignment_role": "OPERATOR",
            "confidence": 0.7,
            "valid_from": date(2026, 8, 23),
            "valid_to": None,
            "source_code": "CRM_OPERATOR_TAILS",
            "source_assignment_id": "crm-op-test",
        }]

    def operator_tail_activity(self, start_date, end_date, **kwargs):
        self.operator_tail_activity_calls.append((start_date, end_date, kwargs))
        return {
            "coverage": {
                "requested_days": 7,
                "available_days": 2,
                "complete": False,
                "available_dates": [date(2026, 8, 19), date(2026, 8, 20)],
                "missing_dates": [],
                "message": "Only 2 of 7 requested UTC days are available locally.",
            },
            "operator_tail_activity": [{
                "company": "Bristow Helicopters Limited",
                "geographic_region": "EUROPE",
                "aircraft_count": 1,
                "address": "400abc",
                "registration": "G-TEST",
                "type_code": "H145",
                "category": "ROTORCRAFT",
                "active_days": 2,
                "active_hours": 4.5,
                "airborne_hours": 3.2,
                "ground_active_hours": 0.4,
                "observations": 1200,
                "source_code": "CRM_OPERATOR_TAILS",
            }],
        }


def plan(**overrides):
    value = {
        "operation": "hub_ranking",
        "metric": "unique_aircraft",
        "date_scope": "range",
        "from_date": "2026-08-20",
        "to_date": "2026-08-20",
        "type_code": None,
        "airport_code": None,
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
        "helicopters_only": False,
        "operator_grouping": "tail",
        "limit": 10,
        "chart": "auto",
    }
    value.update(overrides)
    return value


class QueryPlanTests(unittest.TestCase):
    def test_normalizes_unique_aircraft_for_airport_aircraft_before_validation(self):
        raw = plan(
            operation="airport_aircraft",
            metric="unique_aircraft",
            airport_code="ABZ",
        )

        normalized, adjustments = normalize_plan_for_question(
            raw, "Give me an ABZ traffic overview"
        )
        result = validate_query_plan(
            normalized,
            latest_available=date(2026, 8, 20),
            earliest_available=date(2025, 8, 21),
        )

        self.assertEqual(result.operation, "airport_aircraft")
        self.assertEqual(result.metric, "observations")
        self.assertEqual(result.limit, 100)
        self.assertTrue(adjustments)

    def test_validates_closed_operation_metric_and_defaults_dates(self):
        result = validate_query_plan(
            plan(from_date=None, to_date=None), latest_available=date(2026, 8, 20)
        )
        self.assertEqual(result.from_date, date(2026, 8, 20))
        self.assertEqual(result.to_date, date(2026, 8, 20))

        with self.assertRaisesRegex(ValueError, "not available"):
            validate_query_plan(
                plan(metric="active_days"), latest_available=date(2026, 8, 20)
            )

    def test_rejects_unsafe_filters_and_unbounded_ranges(self):
        with self.assertRaisesRegex(ValueError, "invalid aircraft type"):
            validate_query_plan(
                plan(type_code="B738'; DROP TABLE aircraft_day;--"),
                latest_available=date(2026, 8, 20),
            )
        with self.assertRaisesRegex(ValueError, "capped"):
            validate_query_plan(
                plan(from_date="2024-01-01", to_date="2026-08-20"),
                latest_available=date(2026, 8, 20),
            )
        with self.assertRaisesRegex(ValueError, "true or false"):
            validate_query_plan(
                plan(helicopters_only="false"), latest_available=date(2026, 8, 20)
            )
        with self.assertRaisesRegex(ValueError, "unsupported plan fields"):
            validate_query_plan(
                {**plan(), "sql": "SELECT * FROM aircraft_day"},
                latest_available=date(2026, 8, 20),
            )

    def test_service_executes_curated_hub_result_with_coverage(self):
        planner = FakePlanner(plan())
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask(
            "Which hubs saw the most unique aircraft on 20 August 2026?",
            context=["Show the latest day"],
        )

        self.assertEqual(analytics.calls[-1], (date(2026, 8, 20), date(2026, 8, 20), None, False))
        self.assertEqual(result["table"]["columns"], ["airport", "airport_name", "unique_aircraft"])
        self.assertEqual(result["table"]["rows"][0]["airport"], "ORD")
        self.assertEqual(result["chart"], {
            "type": "bar",
            "x": "airport",
            "y": "unique_aircraft",
            "label": "Unique aircraft",
        })
        self.assertIn("ORD ranks first", result["answer"])
        self.assertEqual(result["meta"]["model"], "test-model")

    def test_airport_daily_traffic_uses_focused_airport_series(self):
        planner = FakePlanner(plan(
            operation="daily_activity",
            metric="unique_aircraft",
            from_date="2026-08-01",
            to_date="2026-08-31",
            airport_code=None,
            helicopters_only=True,
            limit=100,
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("show ABZ helicopter traffic by day for August 2026")

        self.assertEqual(result["plan"]["operation"], "daily_activity")
        self.assertEqual(result["plan"]["airport_code"], "ABZ")
        _, _, call = analytics.airport_daily_activity_calls[-1]
        self.assertEqual(call["airport_code"], "ABZ")
        self.assertTrue(call["helicopters_only"])
        self.assertIn("linked to ABZ", result["answer"])

    def test_whole_database_traffic_is_corrected_and_writes_redacted_debug(self):
        planner = FakePlanner(
            plan(
                metric="primary_aircraft",
                type_code="EC45",
                helicopters_only=True,
            )
        )
        analytics = FakeAnalytics()
        with TemporaryDirectory() as temp_dir:
            service = NaturalLanguageAnalytics(
                analytics,  # type: ignore[arg-type]
                planner,
                debug_dir=Path(temp_dir),
            )
            result = service.ask(
                "From the whole database, which hub has seen the most traffic "
                "from EC45 type helicopters?",
                debug=True,
            )
            dump_path = Path(temp_dir) / f"query-{result['debug_dump']['id']}.json"
            dump = dump_path.read_text(encoding="utf-8")

        self.assertEqual(result["plan"]["date_scope"], "all_available")
        self.assertEqual(result["plan"]["metric"], "unique_aircraft")
        self.assertEqual(
            analytics.calls[-1],
            (date(2025, 8, 21), date(2026, 8, 20), "EC45", True),
        )
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["requested_days"], 2)
        self.assertIn("server_adjustments", dump)
        self.assertNotIn("OPENAI_API_KEY", dump)
        self.assertNotIn("postgresql://", dump)

    def test_aircraft_at_named_airport_is_not_answered_with_global_hubs(self):
        planner = FakePlanner(plan(operation="hub_ranking", metric="unique_aircraft"))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask(
            "show me which helicopters arrived and departed ABZ on the latest available day"
        )

        self.assertEqual(result["plan"]["operation"], "airport_aircraft")
        self.assertEqual(result["plan"]["airport_code"], "ABZ")
        self.assertEqual(result["plan"]["metric"], "airport_movement_candidates")
        self.assertEqual(result["table"]["rows"][0]["tail"], "G-TEST")
        self.assertEqual(
            result["table"]["rows"][0]["operator"],
            "Bristow Helicopters Limited",
        )
        self.assertEqual(
            analytics.airport_calls[-1],
            (
                date(2026, 8, 20),
                date(2026, 8, 20),
                "ABZ",
                None,
                True,
                "airport_movement_candidates",
                100,
            ),
        )
        self.assertIn("conservative ADS-B-derived candidates", result["answer"])
        self.assertNotIn("airport_name", result["table"]["columns"])

    def test_airport_helicopter_types_are_grouped_by_type_not_listed_as_tails(self):
        planner = FakePlanner(
            plan(
                operation="helicopter_types",
                metric="unique_aircraft",
                airport_code="ABZ",
                helicopters_only=True,
                limit=100,
                chart="table",
            )
        )
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask(
            "Show the helicopter types seen at ABZ on the latest available day grouped by TYPE"
        )

        self.assertEqual(result["plan"]["operation"], "airport_types")
        self.assertEqual(result["plan"]["metric"], "unique_aircraft")
        self.assertTrue(result["plan"]["helicopters_only"])
        self.assertEqual(result["table"]["columns"], ["type_code", "description", "unique_aircraft"])
        self.assertEqual([row["type_code"] for row in result["table"]["rows"]], ["EC75", "S92"])
        self.assertEqual(
            analytics.airport_type_calls[-1],
            (
                date(2026, 8, 20),
                date(2026, 8, 20),
                "ABZ",
                None,
                True,
                "unique_aircraft",
                100,
            ),
        )
        self.assertIn("2 matching aircraft types at ABZ", result["answer"])

    def test_mro_aircraft_model_question_uses_capability_records_not_traffic(self):
        planner = FakePlanner(plan(type_code="EC135", helicopters_only=True))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("Which UK Part 145 MROs can maintain EC135 helicopters?")

        self.assertEqual(result["plan"]["operation"], "company_capabilities")
        self.assertEqual(result["plan"]["date_scope"], "reference")
        self.assertIsNone(result["plan"]["type_code"])
        self.assertEqual(result["plan"]["capability_query"], "EC135")
        self.assertEqual(result["plan"]["capability_kind"], "AIRCRAFT")
        self.assertEqual(analytics.calls, [])
        self.assertEqual(
            analytics.company_capability_calls[-1]["capability_query"], "EC135"
        )
        self.assertEqual(result["table"]["rows"][0]["approval_number"], "UK.145.01318")
        self.assertIn("regulator-published approval scopes", result["answer"])
        self.assertIsNone(result["chart"])

    def test_company_directory_location_query_is_not_date_bound(self):
        planner = FakePlanner(plan(
            operation="company_directory",
            metric="site_count",
            date_scope="reference",
            from_date=None,
            to_date=None,
            location_query="Surrey",
            company_role="mro",
            chart="table",
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("Show imported maintenance organisations in Surrey")

        self.assertEqual(result["plan"]["operation"], "company_directory")
        self.assertEqual(analytics.company_directory_calls[-1]["location_query"], "Surrey")
        self.assertEqual(result["coverage"]["requested_days"], 0)
        self.assertIn("imported directory", result["answer"])

    def test_approval_number_query_routes_to_approvals(self):
        planner = FakePlanner(plan(
            operation="company_approvals",
            metric="capability_count",
            date_scope="reference",
            from_date=None,
            to_date=None,
            approval_query="UK.145.01318",
            approval_status="VALID",
            chart="table",
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("Show the status of approval UK.145.01318")

        self.assertEqual(result["plan"]["operation"], "company_approvals")
        self.assertEqual(
            analytics.company_approval_calls[-1]["approval_query"], "UK.145.01318"
        )
        self.assertEqual(result["table"]["rows"][0]["status"], "VALID")

    def test_operator_tail_question_uses_imported_assignment_records(self):
        planner = FakePlanner(plan(company_query="G-TEST"))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("Who operates G-TEST?")

        self.assertEqual(result["plan"]["operation"], "operator_aircraft")
        self.assertEqual(result["plan"]["date_scope"], "reference")
        self.assertEqual(result["plan"]["registration_query"], "G-TEST")
        self.assertIsNone(result["plan"]["company_query"])
        self.assertEqual(result["plan"]["assignment_role"], "OPERATOR")
        self.assertEqual(
            analytics.operator_aircraft_calls[-1]["registration_query"], "G-TEST"
        )
        self.assertEqual(result["table"]["rows"][0]["company"], "Bristow Helicopters Limited")
        self.assertIn("sourced operator/owner/manager claims", result["answer"])
        self.assertIsNone(result["chart"])

    def test_operator_fleet_question_preserves_company_filter(self):
        planner = FakePlanner(plan(company_query="Bristow"))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("Which aircraft are assigned to Bristow as operator?")

        self.assertEqual(result["plan"]["operation"], "operator_aircraft")
        self.assertEqual(result["plan"]["company_query"], "Bristow")
        self.assertEqual(
            analytics.operator_aircraft_calls[-1]["assignment_role"], "OPERATOR"
        )

    def test_busiest_operator_tails_join_assignments_to_dated_activity(self):
        planner = FakePlanner(plan(
            operation="tail_ranking",
            metric="active_hours",
            from_date="2026-08-14",
            to_date="2026-08-20",
            company_query="Bristow",
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("Which Bristow tails have been the busiest over the past week?")

        self.assertEqual(result["plan"]["operation"], "operator_tail_activity")
        self.assertEqual(result["plan"]["company_query"], "Bristow")
        start_date, end_date, call = analytics.operator_tail_activity_calls[-1]
        self.assertEqual((start_date, end_date), (date(2026, 8, 14), date(2026, 8, 20)))
        self.assertEqual(call["assignment_role"], "OPERATOR")
        self.assertEqual(result["table"]["rows"][0]["tail"], "G-TEST")
        self.assertIn("ranks first", result["answer"])

    def test_dated_activity_for_all_registered_operators_is_not_company_directory(self):
        planner = FakePlanner(plan(
            operation="tail_ranking",
            metric="active_hours",
            from_date="2026-08-01",
            to_date="2026-08-20",
            assignment_role="ANY",
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask(
            "Which tails with registered operators logged the most hours so far in August?"
        )

        self.assertEqual(result["plan"]["operation"], "operator_tail_activity")
        self.assertEqual(result["plan"]["assignment_role"], "OPERATOR")
        self.assertIsNone(result["plan"]["company_query"])
        _, _, call = analytics.operator_tail_activity_calls[-1]
        self.assertIsNone(call["company_query"])
        self.assertEqual(result["table"]["rows"][0]["company"], "Bristow Helicopters Limited")

    def test_busiest_helicopter_operators_overrides_directory_plan_and_groups_operators(self):
        planner = FakePlanner(plan(
            operation="company_directory",
            metric="assigned_aircraft_count",
            date_scope="range",
            from_date="2026-08-01",
            to_date="2026-08-20",
            company_role="operator",
            helicopters_only=False,
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("Which helicopter operators were busiest in August 2026?")

        self.assertEqual(result["plan"]["operation"], "operator_tail_activity")
        self.assertEqual(result["plan"]["operator_grouping"], "operator")
        self.assertTrue(result["plan"]["helicopters_only"])
        _, _, call = analytics.operator_tail_activity_calls[-1]
        self.assertTrue(call["group_by_operator"])
        self.assertEqual(result["table"]["columns"][0], "company")
        self.assertIn("ranks first among operators", result["answer"])

    def test_operator_region_is_extracted_even_when_planner_misplaces_it(self):
        planner = FakePlanner(plan(
            operation="company_directory",
            metric="assigned_aircraft_count",
            from_date="2026-08-01",
            to_date="2026-08-20",
            company_query="Europe",
            location_query="Europe",
            company_role="operator",
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask(
            "Which European helicopter operators were busiest in August 2026?"
        )

        self.assertEqual(result["plan"]["region_query"], "EUROPE")
        self.assertIsNone(result["plan"]["company_query"])
        self.assertIsNone(result["plan"]["location_query"])
        _, _, call = analytics.operator_tail_activity_calls[-1]
        self.assertEqual(call["region_query"], "EUROPE")

    def test_busiest_plural_type_in_region_ranks_individual_tails_with_operator_enrichment(self):
        planner = FakePlanner(plan(
            operation="type_breakdown",
            metric="active_hours",
            from_date="2026-08-01",
            to_date="2026-08-20",
            type_code="S92S",
            location_query="Europe",
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("Which S92s were busiest in Europe in August 2026?")

        self.assertEqual(result["plan"]["operation"], "tail_ranking")
        self.assertEqual(result["plan"]["type_code"], "S92")
        self.assertEqual(result["plan"]["region_query"], "EUROPE")
        _, _, call = analytics.tail_ranking_calls[-1]
        self.assertEqual(call["type_code"], "S92")
        self.assertEqual(call["region_query"], "EUROPE")
        self.assertEqual(result["table"]["rows"][0]["tail"], "N123AB")
        self.assertEqual(
            result["table"]["rows"][0]["operator"],
            "Example Air",
        )

    def test_apostrophe_plural_type_routes_to_individual_operator_tails(self):
        planner = FakePlanner(plan(
            operation="type_breakdown",
            metric="active_hours",
            from_date="2026-08-01",
            to_date="2026-08-20",
            type_code="S92",
            region_query="Europe",
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("Which were the busiest S92's in Europe in August 2026?")

        self.assertEqual(result["plan"]["operation"], "tail_ranking")
        self.assertEqual(result["plan"]["type_code"], "S92")
        self.assertEqual(result["table"]["rows"][0]["tail"], "N123AB")

    def test_country_location_is_applied_to_plural_type_activity(self):
        planner = FakePlanner(plan(
            operation="type_breakdown",
            metric="active_hours",
            from_date="2026-08-01",
            to_date="2026-08-31",
            type_code="H125",
            location_query="Chile",
            helicopters_only=True,
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("Which were the most active H125's in Chile in August 2026?")

        self.assertEqual(result["plan"]["operation"], "tail_ranking")
        self.assertEqual(result["plan"]["type_code"], "AS50")
        _, _, call = analytics.tail_ranking_calls[-1]
        self.assertEqual(call["location_query"], "Chile")
        self.assertEqual(result["table"]["rows"][0]["activity_area"], "Chile")

    def test_hub_region_is_forwarded_as_an_airport_location_filter(self):
        planner = FakePlanner(plan(
            operation="hub_ranking",
            metric="unique_aircraft",
            from_date="2026-08-20",
            to_date="2026-08-20",
            region_query="Europe",
            helicopters_only=True,
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask(
            "Which hubs saw the most unique helicopter aircraft in Europe on 2026-08-20?"
        )

        self.assertEqual(result["plan"]["region_query"], "EUROPE")
        self.assertEqual(analytics.snapshot_region_calls[-1], "EUROPE")

    def test_known_operator_helicopter_ranking_applies_rotorcraft_filter(self):
        planner = FakePlanner(plan(
            operation="operator_tail_activity",
            metric="active_hours",
            from_date="2026-08-01",
            to_date="2026-08-20",
            assignment_role="OPERATOR",
            helicopters_only=True,
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask(
            "Which helicopter tails were busiest so far in August, which have a known operator?"
        )

        self.assertEqual(result["plan"]["operation"], "operator_tail_activity")
        _, _, call = analytics.operator_tail_activity_calls[-1]
        self.assertTrue(call["helicopters_only"])
        self.assertEqual(result["table"]["rows"][0]["category"], "ROTORCRAFT")

    def test_operator_activity_promotes_explicit_type_out_of_capability_filter(self):
        planner = FakePlanner(plan(
            operation="operator_tail_activity",
            metric="active_hours",
            from_date="2026-08-17",
            to_date="2026-08-20",
            capability_query="EC45",
            company_role="operator",
            assignment_role="OPERATOR",
            helicopters_only=True,
        ))
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(analytics, planner)  # type: ignore[arg-type]

        result = service.ask("Which EC45 operators have been most active this week?")

        self.assertEqual(result["plan"]["operation"], "operator_tail_activity")
        self.assertEqual(result["plan"]["type_code"], "EC45")
        self.assertIsNone(result["plan"]["capability_query"])
        _, _, call = analytics.operator_tail_activity_calls[-1]
        self.assertEqual(call["type_code"], "EC45")
        self.assertTrue(call["helicopters_only"])

    def test_as350_and_h125_aliases_resolve_to_stored_icao_designator(self):
        analytics = FakeAnalytics()
        service = NaturalLanguageAnalytics(
            analytics,
            FakePlanner(plan(
                operation="tail_ranking",
                metric="active_hours",
                from_date="2026-01-01",
                to_date="2026-08-20",
                type_code="AS350",
                helicopters_only=True,
            )),
        )  # type: ignore[arg-type]

        result = service.ask("Show me the most active AS350 helicopter tails in 2026")

        self.assertEqual(result["plan"]["type_code"], "AS50")
        self.assertEqual(analytics.calls[-1][2], "AS50")
        normalized, adjustments = normalize_plan_for_question(
            plan(type_code="H125", helicopters_only=True),
            "Which H125 helicopters were active?",
        )
        self.assertEqual(normalized["type_code"], "AS50")
        self.assertTrue(any("H125" in adjustment and "AS50" in adjustment for adjustment in adjustments))


class FakeResponse:
    ok = True
    headers = {"x-request-id": "req_test_123"}

    def json(self):
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": __import__("json").dumps(plan())}],
                }
            ]
        }


class FakeSession:
    def __init__(self):
        self.request = None

    def post(self, endpoint, **kwargs):
        self.request = (endpoint, kwargs)
        return FakeResponse()


class OpenAIPlannerTests(unittest.TestCase):
    def test_uses_responses_structured_output_without_storage(self):
        session = FakeSession()
        planner = OpenAIQueryPlanner(api_key="test-secret", model="test-model", session=session)
        decision = planner.plan(
            "Which hubs were busiest?",
            availability={"latest_available_date": "2026-08-20"},
            context=[],
        )

        self.assertEqual(decision.plan["operation"], "hub_ranking")
        self.assertEqual(decision.trace["request_id"], "req_test_123")
        _, request = session.request
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-secret")
        self.assertFalse(request["json"]["store"])
        self.assertTrue(request["json"]["text"]["format"]["strict"])
        self.assertNotIn("test-secret", str(request["json"]))

    def test_requires_server_side_api_key(self):
        planner = OpenAIQueryPlanner(api_key="")
        with self.assertRaises(QueryPlannerUnavailable):
            planner.plan("Which hubs were busiest?", availability={}, context=[])


if __name__ == "__main__":
    unittest.main()
