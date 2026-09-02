from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg

from adsb_ingest.adsblol import DayReleases, Release, ReleaseAsset
from adsb_ingest.access import AccessStore, AccessUser
from adsb_ingest.analytics import AnalyticsStore
from adsb_ingest.companies import CompanyStore, parse_company_row
from adsb_ingest.intelligence_api import IntelligenceService
from adsb_ingest.aircraft_registry import AircraftRegistryStore, CasaAircraftRecord
from adsb_ingest.airports import Airport, AirportCatalog, AirportIndex
from adsb_ingest.archive import TracePayload
from adsb_ingest.summarize import summarize_trace
from adsb_ingest.work_queue import AdminStore, SequentialIngestionWorker
from test_phase2 import trace_row


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is not configured")
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_DATABASE_URL is not None
        cls.store = AdminStore(TEST_DATABASE_URL)
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as connection:
            connection.execute("DROP SCHEMA public CASCADE")
            connection.execute("CREATE SCHEMA public")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase2.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase8.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase9.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase10.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase11.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase12.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase15.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase14.sql")
        # Application startup replays every idempotent migration. Phase 9 adds
        # company columns, so Phase 8 views must remain stable on the next run.
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase8.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase9.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase10.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase11.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase12.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase15.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase14.sql")

    def fixture(self):
        airport = Airport(
            ident="TEST",
            airport_type="small_airport",
            name="Test Airport",
            latitude_deg=51.0,
            longitude_deg=-1.0,
            elevation_ft=100,
            continent="EU",
            iso_country="GB",
            iso_region="GB-ENG",
            municipality="Test",
            scheduled_service=False,
            gps_code="EGTT",
            iata_code=None,
            local_code=None,
        )
        catalog = AirportCatalog(
            airports=(airport,),
            source_url="https://example.test/airports.csv",
            sha256="0" * 64,
            downloaded_at=datetime.now(UTC),
        )
        release = Release(
            tag="v2026.08.20-planes-readsb-test-0",
            html_url="https://example.test/release",
            published_at="2026-08-21T00:00:00Z",
            assets=(ReleaseAsset("test.tar.aa", 100, "https://example.test/test.tar.aa"),),
        )
        discovery = DayReleases(
            utc_date=date(2026, 8, 20),
            repository="example/test",
            preferred=release,
            variants=(release,),
        )
        payload = {
            "icao": "abcdef",
            "r": "G-TEST",
            "t": "H145",
            "desc": "AIRBUS HELICOPTERS H145",
            "version": "readsb test",
            "timestamp": 1787184000.0,
            "trace": [
                trace_row(0, 51.0, -1.0, "ground", 0, flight="TEST1"),
                trace_row(30, 51.001, -1.0, "ground", 10, flight="TEST1"),
                trace_row(60, 51.01, -1.0, 500, 100, flight="TEST1"),
                trace_row(90, 51.1, -1.0, 1_500, 120, flight="TEST1"),
            ],
        }
        trace = TracePayload("trace.json", "abcdef", 100, 500, payload)
        summary = summarize_trace(trace, discovery.utc_date, AirportIndex([airport]))
        return catalog, discovery, summary

    def test_company_import_is_idempotent_and_preserves_roles_and_scope(self) -> None:
        assert TEST_DATABASE_URL is not None
        parsed = parse_company_row(
            {
                "company_name": "Integration Rotor Services Ltd",
                "company_key": "integration-rotor-services-gb",
                "source_company_id": "UK.145.INTEGRATION",
                "is_operator": "yes",
                "is_mro": "yes",
                "company_country_code": "GB",
                "site_name": "Integration Base",
                "source_site_id": "UK.145.INTEGRATION:BASE",
                "is_base_maintenance_site": "yes",
                "authority_code": "UK_CAA",
                "approval_type": "PART_145",
                "approval_number": "UK.145.INTEGRATION",
                "approval_status": "VALID",
                "capability_kind": "AIRCRAFT",
                "source_capability_id": "UK.145.INTEGRATION:H145",
                "manufacturer": "Airbus Helicopters",
                "model": "H145",
                "aircraft_type_code": "H145",
                "aircraft_registration": "G-INTE",
                "aircraft_assignment_role": "OPERATOR",
            },
            2,
        )
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as handle:
            handle.write(b"integration fixture")
            fixture_path = Path(handle.name)
        self.addCleanup(fixture_path.unlink, missing_ok=True)
        companies = CompanyStore(TEST_DATABASE_URL)
        arguments = {
            "source_code": "TEST_COMPANIES",
            "source_name": "Integration test companies",
            "source_kind": "CURATED",
            "authority_code": "UK_CAA",
            "source_url": "https://example.test/companies",
            "data_license": "Test fixture",
            "notes": None,
            "file_path": fixture_path,
        }
        first = companies.import_rows((parsed,), **arguments)
        second = companies.import_rows((parsed,), **arguments)
        self.assertEqual(first["companies"], 1)
        self.assertEqual(second["capabilities"], 1)

        with self.store.connect() as connection:
            company_row = connection.execute(
                """
                SELECT is_operator, is_mro, site_count, valid_approval_count,
                       capability_count, assigned_aircraft_count
                FROM company_directory
                WHERE company_key = 'integration-rotor-services-gb'
                """
            ).fetchone()
            self.assertEqual(company_row, (True, True, 1, 1, 1, 1))
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM company_import_batch WHERE source_code = 'TEST_COMPANIES'"
                ).fetchone()[0],
                2,
            )

        analytics = AnalyticsStore(self.store)
        directory = analytics.company_directory(
            company_query="Integration Rotor", company_role="mro"
        )
        approvals = analytics.company_approvals(
            approval_query="UK.145.INTEGRATION", approval_status="VALID"
        )
        capabilities = analytics.company_capabilities(
            capability_query="H-145", capability_kind="AIRCRAFT", approval_status="VALID"
        )
        operator_aircraft = analytics.operator_aircraft(
            company_query="Integration Rotor", assignment_role="OPERATOR"
        )
        fleet_summary = analytics.operator_fleet_summary()
        self.assertEqual(directory[0]["valid_approval_count"], 1)
        self.assertEqual(directory[0]["assigned_aircraft_count"], 1)
        self.assertEqual(approvals[0]["approval_number"], "UK.145.INTEGRATION")
        self.assertEqual(capabilities[0]["aircraft_type_code"], "H145")
        self.assertEqual(operator_aircraft[0]["registration"], "G-INTE")
        self.assertEqual(operator_aircraft[0]["assignment_role"], "OPERATOR")
        self.assertEqual(operator_aircraft[0]["geographic_region"], "EUROPE")
        self.assertEqual(operator_aircraft[0]["region_basis"], "COMPANY_COUNTRY")
        self.assertGreaterEqual(fleet_summary["operator_assignments"], 1)

    def test_zz_casa_registration_match_resolves_identity_and_operator(self) -> None:
        assert TEST_DATABASE_URL is not None
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE aircraft_day SET registration = 'VH-TST' WHERE address = 'abcdef'"
            )
            connection.execute(
                """
                INSERT INTO aircraft_type_classification (
                    type_code, category, classification_source, confidence
                ) VALUES ('H145', 'ROTORCRAFT', 'TEST_FIXTURE', 1.000)
                ON CONFLICT (type_code) DO UPDATE SET category = EXCLUDED.category
                """
            )
        record = CasaAircraftRecord(
            registration="VH-TST",
            address=None,
            manufacturer="TEXTRON AVIATION INC.",
            model="208B",
            serial_number="TEST-SERIAL-001",
            registry_category="Power Driven Aeroplane",
            official_category="FIXED_WING",
            registration_sub_type="Full Registration",
            registration_status="Full Registration",
            issue_date=None,
            effective_date=date(2026, 1, 1),
            ineffective_date=None,
            modified_date=None,
            manufacture_date=None,
            base_country="Australia",
            base_region=None,
            base_location=None,
            type_certificate_number="A37CE",
            engine_category="Turbo-prop",
            number_of_engines=1,
            number_of_seats=None,
            official_type_code="C208",
            registered_operator="INTEGRATION AIR SERVICES PTY LTD",
            operator_effective_date=date(2026, 1, 2),
            operator_country="Australia",
            operator_geographic_region="OCEANIA",
        )
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
            handle.write(b"CASA integration fixture")
            fixture_path = Path(handle.name)
        self.addCleanup(fixture_path.unlink, missing_ok=True)
        registry = AircraftRegistryStore(TEST_DATABASE_URL)
        registry.import_casa(
            (record,),
            snapshot_date=date(2026, 8, 24),
            downloaded_at=datetime.now(UTC),
            source_path=fixture_path,
        )
        with self.store.connect() as connection:
            identity = connection.execute(
                """
                SELECT type_code, resolved_category::text, identity_source_code,
                       registry_operator, source_type_code, identity_status
                FROM aircraft_day_identity
                WHERE address = 'abcdef'
                """
            ).fetchone()
        self.assertEqual(
            identity,
            (
                "C208", "FIXED_WING", "CASA_AIRCRAFT_REGISTER",
                "INTEGRATION AIR SERVICES PTY LTD", "H145", "CATEGORY_CONFLICT",
            ),
        )
        assignments = AnalyticsStore(self.store).operator_aircraft(
            registration_query="VH-TST"
        )
        self.assertEqual(assignments[0]["company"], "INTEGRATION AIR SERVICES PTY LTD")
        self.assertEqual(assignments[0]["source_code"], "CASA_AIRCRAFT_REGISTER")
        self.assertEqual(assignments[0]["geographic_region"], "OCEANIA")
        airport_tails = AnalyticsStore(self.store).airport_aircraft(
            date(2026, 8, 20),
            date(2026, 8, 20),
            airport_code="TEST",
            metric="airport_ground_observations",
        )
        self.assertEqual(airport_tails[0]["operator"], "INTEGRATION AIR SERVICES PTY LTD")
        self.assertEqual(
            airport_tails[0]["operator_source_code"], "CASA_AIRCRAFT_REGISTER"
        )
        with self.store.connect() as connection:
            semantic_identity = connection.execute(
                """
                SELECT registration, type_code, category, operator
                FROM nl_aircraft_activity
                WHERE utc_date = %s AND address = 'abcdef'
                """,
                (date(2026, 8, 20),),
            ).fetchone()
        self.assertEqual(
            semantic_identity,
            (
                "VH-TST", "C208", "FIXED_WING",
                "INTEGRATION AIR SERVICES PTY LTD",
            ),
        )

    def test_load_replace_and_failure_rollback(self) -> None:
        catalog, discovery, summary = self.fixture()
        self.store.upsert_airports(catalog)
        dataset_id = self.store.upsert_dataset(discovery)

        download_job = self.store.start_job(dataset_id, "DOWNLOAD")
        self.store.set_downloading(dataset_id)
        self.store.finish_download(
            dataset_id,
            download_job,
            raw_bytes=100,
            raw_file_count=1,
            downloaded_bytes_this_run=100,
            duration_ms=1,
        )

        process_job = self.store.start_job(dataset_id, "PROCESS")
        self.store.set_processing(dataset_id)
        result = self.store.load_summaries(dataset_id, process_job, iter([summary]))
        self.assertEqual(result["aircraft_count"], 1)
        self.assertEqual(result["airport_presence_count"], 1)
        self.assertEqual(result["flight_segment_count"], 1)
        self.assertEqual(result["airport_visit_count"], 1)
        with self.store.connect() as connection:
            initial_derived_bytes = connection.execute(
                "SELECT derived_bytes_estimate FROM dataset_day WHERE id = %s",
                (dataset_id,),
            ).fetchone()[0]
            episode_metadata = connection.execute(
                """
                SELECT flight_segment_record_count, airport_visit_record_count,
                       derivation_version, derivation_config
                FROM dataset_day
                WHERE id = %s
                """,
                (dataset_id,),
            ).fetchone()
        self.assertIsNotNone(initial_derived_bytes)
        self.assertEqual(episode_metadata[:3], (1, 1, "flight-visits-v1"))
        self.assertEqual(episode_metadata[3], {})
        already_processed = self.store.enqueue_date(discovery.utc_date)
        self.assertEqual(already_processed["outcome"], "SKIPPED_ALREADY_PROCESSED")

        self.store.mark_raw_deleted(dataset_id, 100)
        redownload_job = self.store.start_job(dataset_id, "DOWNLOAD")
        self.store.set_downloading(dataset_id)
        self.store.finish_download(
            dataset_id,
            redownload_job,
            raw_bytes=100,
            raw_file_count=1,
            downloaded_bytes_this_run=100,
            duration_ms=2,
        )
        with self.store.connect() as connection:
            raw_state = connection.execute(
                "SELECT raw_deleted_at, raw_deleted_bytes FROM dataset_day WHERE id = %s",
                (dataset_id,),
            ).fetchone()
            self.assertEqual(raw_state, (None, None))

        reprocess_job = self.store.start_job(dataset_id, "REPROCESS")
        self.store.set_processing(dataset_id)
        self.store.load_summaries(dataset_id, reprocess_job, iter([summary]))

        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM aircraft_day").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM aircraft_airport_day").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM aircraft_flight_segment").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM aircraft_airport_visit").fetchone()[0],
                1,
            )
            metrics = connection.execute(
                "SELECT unique_aircraft, type_code FROM airport_day_type_metrics"
            ).fetchone()
            self.assertEqual(metrics, (1, "H145"))
            hub_metrics = connection.execute(
                """
                SELECT unique_aircraft, primary_aircraft, known_type_codes
                FROM airport_day_metrics
                """
            ).fetchone()
            self.assertEqual(hub_metrics, (1, 1, 1))
            current_derived_bytes = connection.execute(
                "SELECT derived_bytes_estimate FROM dataset_day WHERE id = %s",
                (dataset_id,),
            ).fetchone()[0]
            self.assertEqual(current_derived_bytes, initial_derived_bytes)

        failed_job = self.store.start_job(dataset_id, "REPROCESS")
        self.store.set_processing(dataset_id)

        def failing_generator():
            yield summary
            raise RuntimeError("synthetic parser failure")

        with self.assertRaisesRegex(RuntimeError, "synthetic parser failure"):
            self.store.load_summaries(dataset_id, failed_job, failing_generator())
        self.store.fail_job(
            dataset_id,
            failed_job,
            stage="PROCESSING",
            message="synthetic parser failure",
        )
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM aircraft_day").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT observation_count FROM aircraft_day").fetchone()[0],
                summary[0].observation_count,
            )

    def test_sequential_queue_claim_duplicate_guard_and_cancel(self) -> None:
        queued = self.store.enqueue_date(date(2026, 8, 19))
        self.assertEqual(queued["outcome"], "QUEUED")
        duplicate = self.store.enqueue_date(date(2026, 8, 19))
        self.assertEqual(duplicate["outcome"], "ALREADY_QUEUED")

        item = self.store.claim_next("integration-test-worker")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.utc_date, date(2026, 8, 19))
        self.store.finish_queue_item(item, {"status": "fixture complete"})

        cancellable = self.store.enqueue_date(date(2026, 8, 18))
        cancelled = self.store.cancel_queue_item(cancellable["item"]["id"])
        self.assertIsNotNone(cancelled)
        with self.store.connect() as connection:
            statuses = connection.execute(
                "SELECT status, count(*) FROM ingestion_queue GROUP BY status ORDER BY status"
            ).fetchall()
        self.assertIn(("SUCCEEDED", 1), statuses)
        self.assertIn(("CANCELLED", 1), statuses)

    def test_worker_processes_queue_without_parallel_stage(self) -> None:
        seen_dates: list[date] = []

        def fixture_runner(args):
            seen_dates.append(args.utc_date)
            return {"status": "PROCESSED", "utc_date": args.utc_date.isoformat()}

        queued = self.store.enqueue_date(date(2026, 8, 17))
        self.assertEqual(queued["outcome"], "QUEUED")
        worker = SequentialIngestionWorker(
            self.store,
            runner=fixture_runner,
            poll_seconds=0.05,
        )
        worker.start()
        worker.notify()
        deadline = time.monotonic() + 5
        final_status = None
        while time.monotonic() < deadline:
            with self.store.connect() as connection:
                final_status = connection.execute(
                    "SELECT status FROM ingestion_queue WHERE id = %s",
                    (queued["item"]["id"],),
                ).fetchone()[0]
            if str(final_status) == "SUCCEEDED":
                break
            time.sleep(0.05)
        worker.stop()
        self.assertEqual(str(final_status), "SUCCEEDED")
        self.assertEqual(seen_dates, [date(2026, 8, 17)])

    def test_z_coverage_aware_analytics_snapshot(self) -> None:
        analytics = AnalyticsStore(self.store)
        analytics.refresh_after_ingestion()

        snapshot = analytics.snapshot(date(2026, 8, 20), date(2026, 8, 20))
        self.assertTrue(snapshot["coverage"]["complete"])
        self.assertEqual(snapshot["totals"]["unique_aircraft"], 1)
        self.assertEqual(snapshot["types"][0]["type_code"], "H145")
        self.assertEqual(snapshot["hubs"][0]["airport_ident"], "TEST")
        self.assertLessEqual(
            snapshot["hubs"][0]["movement_linked_aircraft"],
            snapshot["hubs"][0]["movement_candidates"],
        )
        self.assertEqual(snapshot["helicopters"]["totals"]["unique_aircraft"], 1)

        suggestions = analytics.operator_suggestions("integration rotor")
        self.assertEqual(suggestions[0]["name"], "Integration Rotor Services Ltd")

        partial = analytics.snapshot(date(2026, 8, 19), date(2026, 8, 20))
        self.assertFalse(partial["coverage"]["complete"])
        self.assertEqual(partial["coverage"]["available_days"], 1)
        self.assertEqual(partial["coverage"]["requested_days"], 2)

        site = analytics.company_sites(query="Integration Rotor")[0]
        updated = analytics.update_company_site_tracking(
            site["site_id"],
            airport_ident="TEST",
            is_of_interest=True,
            is_customer=True,
            interest_notes="Integration customer base",
        )
        self.assertTrue(updated["is_customer"])
        self.assertTrue(updated["is_of_interest"])
        self.assertEqual(updated["airport_ident"], "TEST")

        activity = analytics.company_site_activity(
            site["site_id"], date(2026, 8, 20)
        )
        self.assertEqual(activity["airport_metrics"]["unique_aircraft"], 1)
        self.assertEqual(activity["types"][0]["type_code"], "H145")
        self.assertIn("does not prove", activity["attribution_note"])

        with self.store.connect() as connection:
            semantic_row = connection.execute(
                """
                SELECT registration, type_code, category, airport
                FROM nl_airport_activity
                WHERE utc_date = %s AND address = 'abcdef'
                """,
                (date(2026, 8, 20),),
            ).fetchone()
        self.assertEqual(semantic_row, ("G-TEST", "H145", "ROTORCRAFT", "TEST"))
        with self.store.connect() as connection:
            region_row = connection.execute(
                """
                SELECT registration, type_code, activity_region, linked_airports
                FROM nl_region_activity
                WHERE utc_date = %s AND address = 'abcdef'
                """,
                (date(2026, 8, 20),),
            ).fetchone()
        self.assertEqual(region_row, ("G-TEST", "H145", "EUROPE", 1))

    def test_zy_intelligence_api_queries_match_materialized_views(self) -> None:
        service = IntelligenceService(self.store)
        coverage = service.coverage(date(2026, 8, 20), date(2026, 8, 20))
        self.assertTrue(coverage["complete"])

        activity = service.helicopter_activity(
            date(2026, 8, 20),
            date(2026, 8, 20),
            registrations=["G-TEST"],
            operator=None,
            airport="TEST",
            limit=10,
        )
        self.assertEqual(activity[0]["registration"], "G-TEST")
        self.assertEqual(activity[0]["type_code"], "H145")

        days = service.helicopter_days(
            "G-TEST", date(2026, 8, 20), date(2026, 8, 20)
        )
        self.assertEqual(days[0]["airports"][0]["ident"], "TEST")

        airport = service.airport_activity(
            "TEST", date(2026, 8, 20), date(2026, 8, 20), limit=10
        )
        self.assertEqual(airport["helicopters"][0]["registration"], "G-TEST")
        self.assertEqual(airport["types"][0]["type_code"], "H145")

        daily = service.aircraft_daily_activity(
            date(2026, 8, 20),
            date(2026, 8, 20),
            registrations=["G-TEST"],
            category="ALL",
        )
        self.assertEqual(daily[0]["registration"], "G-TEST")
        self.assertEqual(daily[0]["airports"][0]["ident"], "TEST")
        self.assertEqual(daily[0]["flight_segment_count"], 1)
        self.assertEqual(daily[0]["airport_visit_count"], 1)
        self.assertEqual(daily[0]["derivation_version"], "flight-visits-v1")
        self.assertGreaterEqual(daily[0]["elapsed_flight_hours"], 0)
        self.assertIsNotNone(daily[0]["data_revision"])

        ranked_aircraft = service.region_aircraft_rankings(
            ["EUROPE"],
            date(2026, 8, 20),
            date(2026, 8, 20),
            category="ROTORCRAFT",
            operator_status="ALL",
            type_status="KNOWN",
            metric="active_hours",
            limit=10,
        )
        self.assertEqual(ranked_aircraft[0]["registration"], "G-TEST")

        type_breakdown = service.region_type_breakdown(
            ["EUROPE"],
            date(2026, 8, 20),
            date(2026, 8, 20),
            category="ROTORCRAFT",
            limit=10,
        )
        self.assertEqual(type_breakdown[0]["type_code"], "H145")

        ranked_airports = service.region_airport_rankings(
            ["EUROPE"],
            date(2026, 8, 20),
            date(2026, 8, 20),
            category="ROTORCRAFT",
            metric="movement_candidates",
            limit=10,
            compare_previous=True,
        )
        self.assertEqual(ranked_airports[0]["airport_ident"], "TEST")
        self.assertEqual(ranked_airports[0]["leading_types"][0]["type_code"], "H145")

    def test_zz_external_access_users_and_audit_log(self) -> None:
        assert TEST_DATABASE_URL is not None
        access = AccessStore(TEST_DATABASE_URL)
        access.apply_migration()
        self.assertEqual(access.bootstrap_admins(["Admin@Example.com"]), 1)
        admin = access.get_user("admin@example.com")
        assert admin is not None
        self.assertTrue(admin.permits("ADMIN"))
        analyst = access.upsert_user(
            "analyst@example.com", role="ANALYST", display_name="Test Analyst"
        )
        self.assertEqual(analyst["role"], "ANALYST")
        access.record_audit_event(
            actor=AccessUser(
                email="admin@example.com",
                display_name=None,
                role="ADMIN",
                active=True,
            ),
            action="upsert_access_user",
            method="POST",
            path="/api/admin/users",
            request_id="integration-test",
            remote_address="127.0.0.1",
            response_status=200,
            target="email=analyst@example.com",
        )
        with self.store.connect() as connection:
            audit = connection.execute(
                """
                SELECT actor_email, action, remote_address::text
                FROM heligent_audit_event
                WHERE request_id = 'integration-test'
                """
            ).fetchone()
        self.assertEqual(
            audit, ("admin@example.com", "upsert_access_user", "127.0.0.1/32")
        )
        with self.assertRaisesRegex(ValueError, "own access"):
            access.upsert_user(
                "admin@example.com",
                role="VIEWER",
                actor_email="admin@example.com",
            )
        deactivated = access.deactivate_user(
            "analyst@example.com", actor_email="admin@example.com"
        )
        assert deactivated is not None
        self.assertFalse(deactivated["active"])
        access.upsert_user("spare-admin@example.com", role="ADMIN")
        deactivated_admin = access.deactivate_user(
            "admin@example.com", actor_email="spare-admin@example.com"
        )
        assert deactivated_admin is not None
        self.assertFalse(deactivated_admin["active"])
        with self.assertRaisesRegex(ValueError, "own account"):
            access.deactivate_user("admin@example.com", actor_email="admin@example.com")


if __name__ == "__main__":
    unittest.main()
