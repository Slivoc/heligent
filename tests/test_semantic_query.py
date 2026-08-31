from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from adsb_ingest.natural_language import QueryPlannerUnavailable
from adsb_ingest.semantic_query import (
    MAX_RESULT_ROWS,
    OpenAISemanticSQLPlanner,
    SemanticNaturalLanguageAnalytics,
    SemanticQueryPlan,
    SemanticQueryRejected,
    SemanticQueryResult,
    explicit_question_date_range,
    validate_and_bound_sql,
    validate_question_semantics,
    validate_semantic_plan,
)


def plan(**overrides):
    value = {
        "sql": (
            "SELECT operator, COUNT(DISTINCT address) AS unique_aircraft "
            "FROM nl_aircraft_activity "
            "WHERE utc_date BETWEEN DATE '2026-07-22' AND DATE '2026-08-20' "
            "AND operator IS NOT NULL GROUP BY operator "
            "ORDER BY unique_aircraft DESC LIMIT 20"
        ),
        "title": "Known operators by unique aircraft",
        "result_kind": "ranking",
        "from_date": "2026-07-22",
        "to_date": "2026-08-20",
        "chart_type": "bar",
        "chart_x": "operator",
        "chart_y": "unique_aircraft",
        "chart_label": "Unique aircraft",
        "caveat": "operator_attribution",
        "suggestions": ["Show the busiest Bristow tails."],
    }
    value.update(overrides)
    return value


class FakeAnalytics:
    def availability(self):
        return {
            "earliest_available_date": date(2025, 8, 21),
            "latest_available_date": date(2026, 8, 20),
            "available_days": 2,
            "available_dates": [date(2025, 8, 21), date(2026, 8, 20)],
            "company_data": {"companies": 281},
        }


class FakePlanner:
    configured = True
    model = "test-model"

    def __init__(self, plans):
        self.plans = list(plans)
        self.calls = []
        self.repairs = []

    def plan(self, question, *, availability, context):
        self.calls.append((question, availability, context))
        return self.plans.pop(0)

    def repair(self, question, *, availability, context, previous_plan, error):
        self.repairs.append((question, previous_plan, error))
        return self.plans.pop(0)


class FakeExecutor:
    statement_timeout_ms = 20_000

    def __init__(self, *, reject_first=False):
        self.reject_first = reject_first
        self.plans = []

    def execute(self, query_plan):
        self.plans.append(query_plan)
        if self.reject_first and len(self.plans) == 1:
            raise SemanticQueryRejected("synthetic rejection")
        return SemanticQueryResult(
            sql=query_plan.sql,
            columns=("operator", "unique_aircraft"),
            rows=(
                {"operator": "Example Rotor", "unique_aircraft": 12},
                {"operator": "Second Rotor", "unique_aircraft": 8},
            ),
            planned_cost=123.4,
            execution_ms=42,
        )


class RejectingExecutor:
    statement_timeout_ms = 10_000

    def execute(self, query_plan):
        raise SemanticQueryRejected("synthetic permanent rejection")


class SemanticSQLValidationTests(unittest.TestCase):
    def test_allows_ctes_and_caps_the_outer_result(self):
        sql = validate_and_bound_sql(
            "WITH daily AS ("
            "SELECT utc_date, address, MAX(active_hours) AS hours "
            "FROM nl_aircraft_activity "
            "WHERE utc_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-20' "
            "GROUP BY utc_date, address) "
            "SELECT address, SUM(hours) AS active_hours FROM daily "
            "GROUP BY address ORDER BY active_hours DESC LIMIT 500",
            activity_dates_required=True,
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 20),
        )

        self.assertIn(f"LIMIT {MAX_RESULT_ROWS}", sql)
        self.assertIn("nl_aircraft_activity", sql)

    def test_rejects_base_tables_schema_qualification_and_multiple_statements(self):
        bad_sql = (
            "SELECT * FROM aircraft_day",
            "SELECT * FROM public.nl_company",
            "SELECT * FROM nl_company; SELECT * FROM nl_company",
        )
        for sql in bad_sql:
            with self.subTest(sql=sql), self.assertRaises(SemanticQueryRejected):
                validate_and_bound_sql(sql, activity_dates_required=False)

    def test_rejects_unsafe_function_locking_and_offset(self):
        bad_sql = (
            "SELECT pg_sleep(1), company FROM nl_company",
            "SELECT company FROM nl_company FOR UPDATE",
            "SELECT company FROM nl_company OFFSET 100000",
        )
        for sql in bad_sql:
            with self.subTest(sql=sql), self.assertRaises(SemanticQueryRejected):
                validate_and_bound_sql(sql, activity_dates_required=False)

    def test_requires_matching_dates_in_an_activity_where_clause(self):
        with self.assertRaisesRegex(SemanticQueryRejected, "WHERE"):
            validate_and_bound_sql(
                "SELECT utc_date, COUNT(*) FROM nl_aircraft_activity GROUP BY utc_date",
                activity_dates_required=True,
                from_date=date(2026, 8, 1),
                to_date=date(2026, 8, 20),
            )
        with self.assertRaisesRegex(SemanticQueryRejected, "match"):
            validate_and_bound_sql(
                "SELECT COUNT(*) FROM nl_aircraft_activity "
                "WHERE utc_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-19'",
                activity_dates_required=True,
                from_date=date(2026, 8, 1),
                to_date=date(2026, 8, 20),
            )
        with self.assertRaisesRegex(SemanticQueryRejected, "explicit bounded"):
            validate_and_bound_sql(
                "SELECT COUNT(*) FROM nl_aircraft_activity WHERE utc_date IS NOT NULL",
                activity_dates_required=False,
            )

    def test_canonicalizes_aircraft_model_aliases_in_type_filters(self):
        sql = validate_and_bound_sql(
            "SELECT address FROM nl_region_activity "
            "WHERE utc_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-20' "
            "AND type_code = 'H175' AND operator <> 'H175'",
            activity_dates_required=True,
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 20),
        )
        reverse_sql = validate_and_bound_sql(
            "SELECT address FROM nl_region_activity "
            "WHERE utc_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-20' "
            "AND 'H-175' = type_code",
            activity_dates_required=True,
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 20),
        )
        in_sql = validate_and_bound_sql(
            "SELECT address FROM nl_region_activity "
            "WHERE utc_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-20' "
            "AND type_code IN ('H175', 'EC175', 'S92')",
            activity_dates_required=True,
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 20),
        )

        self.assertIn("type_code = 'EC75'", sql)
        self.assertIn("operator <> 'H175'", sql)
        self.assertIn("'EC75' = type_code", reverse_sql)
        self.assertIn("type_code IN ('EC75', 'EC75', 'S92')", in_sql)

    def test_canonicalizes_three_letter_airport_ident_filters(self):
        iata_sql = validate_and_bound_sql(
            "SELECT type_code FROM nl_airport_activity "
            "WHERE utc_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-20' "
            "AND airport_ident = 'abz'",
            activity_dates_required=True,
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 20),
        )
        reverse_sql = validate_and_bound_sql(
            "SELECT type_code FROM nl_airport_activity "
            "WHERE utc_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-20' "
            "AND 'LSI' = airport_ident",
            activity_dates_required=True,
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 20),
        )
        icao_sql = validate_and_bound_sql(
            "SELECT type_code FROM nl_airport_activity "
            "WHERE utc_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-20' "
            "AND airport_ident = 'EGPD'",
            activity_dates_required=True,
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 20),
        )
        reference_sql = validate_and_bound_sql(
            "SELECT company FROM nl_company_site WHERE airport_ident = 'ABZ'",
            activity_dates_required=False,
        )

        self.assertIn("airport = 'ABZ'", iata_sql)
        self.assertIn("'LSI' = airport", reverse_sql)
        self.assertIn("airport_ident = 'EGPD'", icao_sql)
        self.assertIn("airport_ident = 'ABZ'", reference_sql)


class SemanticPlanTests(unittest.TestCase):
    def test_extracts_full_explicit_calendar_month_without_treating_a_day_as_month(self):
        self.assertEqual(
            explicit_question_date_range("Show H125s in Europe in August 2026"),
            (date(2026, 8, 1), date(2026, 8, 31)),
        )
        self.assertEqual(
            explicit_question_date_range("Show August 2024 traffic"),
            (date(2024, 8, 1), date(2024, 8, 31)),
        )
        self.assertIsNone(explicit_question_date_range("Show traffic on 2026-08-20"))

    def test_validates_plan_metadata(self):
        result = validate_semantic_plan(
            plan(), latest_available=date(2026, 8, 20)
        )

        self.assertEqual(result.from_date, date(2026, 7, 22))
        self.assertEqual(result.chart_x, "operator")
        self.assertNotIn("sql", result.public_json())

    def test_rejects_future_and_overlong_ranges(self):
        with self.assertRaises(SemanticQueryRejected):
            validate_semantic_plan(
                plan(from_date="2026-08-21", to_date="2026-08-22"),
                latest_available=date(2026, 8, 20),
            )

    def test_region_activity_and_optional_operator_are_semantic_invariants(self):
        question = (
            "Show H125s that did the most hours in Europe in August 2026 "
            "and show the operator if available"
        )
        wrong = validate_semantic_plan(
            plan(
                sql=(
                    "SELECT address, registration, operator, SUM(active_hours) AS hours "
                    "FROM nl_aircraft_activity "
                    "WHERE utc_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-31' "
                    "AND operator_region = 'EUROPE' GROUP BY address, registration, operator"
                ),
                from_date="2026-08-01",
                to_date="2026-08-31",
            ),
            latest_available=date(2026, 8, 20),
        )
        with self.assertRaisesRegex(SemanticQueryRejected, "Operator is optional"):
            validate_question_semantics(
                wrong,
                question,
                required_date_range=(date(2026, 8, 1), date(2026, 8, 31)),
            )

        correct = validate_semantic_plan(
            plan(
                sql=(
                    "SELECT address, MAX(registration) AS registration, "
                    "MAX(operator) AS operator, "
                    "SUM(aircraft_day_active_hours) AS hours "
                    "FROM nl_region_activity "
                    "WHERE utc_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-31' "
                    "AND activity_region = 'EUROPE' AND type_code = 'AS50' "
                    "GROUP BY address ORDER BY hours DESC"
                ),
                from_date="2026-08-01",
                to_date="2026-08-31",
            ),
            latest_available=date(2026, 8, 20),
        )
        validate_question_semantics(
            correct,
            question,
            required_date_range=(date(2026, 8, 1), date(2026, 8, 31)),
        )

        narrowed = validate_semantic_plan(
            plan(from_date="2026-08-02", to_date="2026-08-20"),
            latest_available=date(2026, 8, 20),
        )
        with self.assertRaisesRegex(SemanticQueryRejected, "full range|explicit requested"):
            validate_question_semantics(
                narrowed,
                "Show traffic in August 2026",
                required_date_range=(date(2026, 8, 1), date(2026, 8, 31)),
            )
        with self.assertRaises(SemanticQueryRejected):
            validate_semantic_plan(
                plan(from_date="2025-01-01", to_date="2026-08-20"),
                latest_available=date(2026, 8, 20),
            )


class SemanticServiceTests(unittest.TestCase):
    def test_returns_model_selected_rows_without_operation_routing(self):
        planner = FakePlanner([plan()])
        executor = FakeExecutor()
        service = SemanticNaturalLanguageAnalytics(
            FakeAnalytics(), planner, executor=executor  # type: ignore[arg-type]
        )

        result = service.ask("Rank known operators by unique aircraft seen in the last 30 days")

        self.assertEqual(result["table"]["rows"][0]["operator"], "Example Rotor")
        self.assertEqual(result["chart"]["x"], "operator")
        self.assertEqual(result["meta"]["execution"], "validated read-only semantic SQL")
        self.assertIn("ranks first", result["answer"])
        self.assertEqual(len(planner.calls), 1)

    def test_repairs_one_rejected_plan(self):
        first = plan(sql="SELECT pg_sleep(1), company FROM nl_company")
        planner = FakePlanner([first, plan()])
        executor = FakeExecutor(reject_first=True)
        service = SemanticNaturalLanguageAnalytics(
            FakeAnalytics(), planner, executor=executor  # type: ignore[arg-type]
        )

        result = service.ask("Rank known operators by unique aircraft seen recently")

        self.assertEqual(result["table"]["rows"][0]["unique_aircraft"], 12)
        self.assertEqual(len(planner.repairs), 1)
        self.assertIn("synthetic rejection", planner.repairs[0][2])

    def test_repairs_operator_home_geography_and_preserves_optional_operator_rows(self):
        wrong = plan(
            sql=(
                "SELECT address, registration, operator, SUM(active_hours) AS hours "
                "FROM nl_aircraft_activity "
                "WHERE utc_date BETWEEN DATE '2026-08-02' AND DATE '2026-08-20' "
                "AND operator_region = 'EUROPE' GROUP BY address, registration, operator"
            ),
            from_date="2026-08-02",
            to_date="2026-08-20",
        )
        corrected = plan(
            sql=(
                "SELECT address, MAX(registration) AS registration, "
                "MAX(operator) AS operator, "
                "SUM(aircraft_day_active_hours) AS unique_aircraft "
                "FROM nl_region_activity "
                "WHERE utc_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-31' "
                "AND activity_region = 'EUROPE' AND type_code = 'AS50' "
                "GROUP BY address ORDER BY unique_aircraft DESC"
            ),
            from_date="2026-08-01",
            to_date="2026-08-31",
            chart_x="operator",
        )
        planner = FakePlanner([wrong, corrected])
        executor = FakeExecutor()
        service = SemanticNaturalLanguageAnalytics(
            FakeAnalytics(), planner, executor=executor  # type: ignore[arg-type]
        )

        result = service.ask(
            "Show H125s that did the most hours in Europe in August 2026, "
            "show the operator if available"
        )

        self.assertEqual(len(planner.repairs), 1)
        self.assertIn("do not shrink", planner.repairs[0][2])
        self.assertEqual(result["plan"]["from_date"], "2026-08-01")
        self.assertEqual(result["plan"]["to_date"], "2026-08-31")

    def test_writes_version_two_debug_document(self):
        with TemporaryDirectory() as directory:
            service = SemanticNaturalLanguageAnalytics(
                FakeAnalytics(),
                FakePlanner([plan()]),
                executor=FakeExecutor(),  # type: ignore[arg-type]
                debug_dir=Path(directory),
            )
            result = service.ask("Rank known operators by unique aircraft", debug=True)
            document = json.loads(Path(result["debug_dump"]["path"]).read_text("utf-8"))

        self.assertEqual(document["debug_version"], 2)
        self.assertIn("validated_sql", document)
        self.assertNotIn("sql", document["validated_plan"])
        self.assertTrue(document["query_guard"]["read_only_transaction"])

    def test_writes_rejected_debug_document_after_both_plans_fail(self):
        with TemporaryDirectory() as directory:
            service = SemanticNaturalLanguageAnalytics(
                FakeAnalytics(),
                FakePlanner([plan(), plan()]),
                executor=RejectingExecutor(),  # type: ignore[arg-type]
                debug_dir=Path(directory),
            )
            with self.assertRaisesRegex(QueryPlannerUnavailable, "Debug saved"):
                service.ask("Rank known operators by unique aircraft", debug=True)
            files = list(Path(directory).glob("query-*.json"))
            self.assertEqual(len(files), 1)
            document = json.loads(files[0].read_text("utf-8"))

        self.assertEqual(document["status"], "REJECTED")
        self.assertIn("synthetic permanent rejection", document["rejection"]["message"])


class FakeResponse:
    ok = True
    headers = {"x-request-id": "req_semantic_test"}

    def json(self):
        return {
            "id": "resp_semantic_test",
            "model": "test-model",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": json.dumps(plan())}
                    ],
                }
            ],
        }


class FakeSession:
    def __init__(self):
        self.request = None

    def post(self, endpoint, **kwargs):
        self.request = (endpoint, kwargs)
        return FakeResponse()


class OpenAISemanticPlannerTests(unittest.TestCase):
    def test_uses_strict_responses_output_and_supplies_semantic_schema(self):
        session = FakeSession()
        planner = OpenAISemanticSQLPlanner(
            api_key="test-secret", model="test-model", session=session
        )

        decision = planner.plan(
            "Which operators were busiest?",
            availability={"latest_available_date": "2026-08-20"},
            context=[],
        )

        self.assertEqual(decision.plan["result_kind"], "ranking")
        self.assertEqual(decision.trace["request_id"], "req_semantic_test")
        _, request = session.request
        self.assertFalse(request["json"]["store"])
        self.assertTrue(request["json"]["text"]["format"]["strict"])
        self.assertIn("nl_aircraft_activity", request["json"]["input"])
        self.assertNotIn("test-secret", str(request["json"]))

    def test_requires_server_side_api_key(self):
        planner = OpenAISemanticSQLPlanner(api_key="")
        with self.assertRaises(QueryPlannerUnavailable):
            planner.plan("Which operators were busiest?", availability={}, context=[])


if __name__ == "__main__":
    unittest.main()
