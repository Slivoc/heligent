from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

from adsb_ingest.companies import (
    CSV_FIELDS,
    CompanyImportError,
    load_company_csv,
    parse_company_row,
    write_company_template,
)


class CompanyImportParsingTests(unittest.TestCase):
    def test_minimal_company_defaults_to_stable_key_and_roles_false(self) -> None:
        parsed = parse_company_row(
            {"company_name": "North Sea Helicopters Ltd", "company_country_code": "GB"},
            2,
        )

        self.assertEqual(parsed.company_key, "north-sea-helicopters-ltd-gb")
        self.assertEqual(parsed.source_company_id, parsed.company_key)
        self.assertFalse(parsed.is_operator)
        self.assertFalse(parsed.is_mro)
        self.assertIsNone(parsed.site_key)

    def test_parses_company_site_approval_capability_and_tail_assignment(self) -> None:
        parsed = parse_company_row(
            {
                "company_name": "Rotor Support Limited",
                "source_company_id": "UK.145.12345",
                "aliases": "Rotor Support|RSL",
                "is_operator": "yes",
                "is_mro": "true",
                "company_country_code": "gb",
                "site_name": "Aberdeen Base",
                "airport_ident": "egpd",
                "is_base_maintenance_site": "1",
                "authority_code": "uk_caa",
                "approval_type": "part_145",
                "approval_number": "UK.145.12345",
                "rating_class": "A3",
                "manufacturer": "Airbus Helicopters",
                "model": "H145",
                "aircraft_type_code": "h145",
                "capability_line_maintenance": "yes",
                "aircraft_registration": "g-test",
                "aircraft_assignment_role": "aoc_authorized",
                "aircraft_assignment_confidence": "0.95",
            },
            7,
        )

        self.assertEqual(parsed.aliases, ("Rotor Support", "RSL"))
        self.assertEqual(parsed.company_country_code, "GB")
        self.assertEqual(parsed.airport_ident, "EGPD")
        self.assertTrue(parsed.is_base_maintenance_site)
        self.assertEqual(parsed.authority_code, "UK_CAA")
        self.assertEqual(parsed.approval_type, "PART_145")
        self.assertEqual(parsed.capability_kind, "AIRCRAFT")
        self.assertIsNotNone(parsed.source_capability_id)
        self.assertEqual(parsed.aircraft_type_code, "H145")
        self.assertEqual(parsed.aircraft_registration, "G-TEST")
        self.assertEqual(parsed.aircraft_assignment_role, "AOC_AUTHORIZED")

    def test_capability_requires_approval(self) -> None:
        with self.assertRaisesRegex(CompanyImportError, "require an approval"):
            parse_company_row(
                {"company_name": "Unsupported MRO", "model": "H145", "is_mro": "yes"},
                2,
            )

    def test_rejects_partial_coordinates_and_invalid_dates(self) -> None:
        with self.assertRaisesRegex(CompanyImportError, "supplied together"):
            parse_company_row(
                {"company_name": "Coordinate Test", "latitude_deg": "51.2"}, 2
            )
        with self.assertRaisesRegex(CompanyImportError, "cannot be after"):
            parse_company_row(
                {
                    "company_name": "Approval Test",
                    "authority_code": "UK_CAA",
                    "approval_type": "PART_145",
                    "approval_number": "TEST",
                    "approval_valid_from": "2026-02-01",
                    "approval_valid_to": "2026-01-01",
                },
                2,
            )

    def test_accepts_excel_and_uk_date_formats(self) -> None:
        uk_dates = parse_company_row(
            {
                "company_name": "Excel Date Test",
                "source_updated_at": "20/08/2026",
                "authority_code": "UK_CAA",
                "approval_type": "PART_145",
                "approval_number": "UK.145.DATE",
                "approval_valid_from": "13/09/2013",
                "approval_valid_to": "20-Aug-26",
                "approval_last_verified_at": "20/08/2026 14:35",
            },
            2,
        )
        self.assertEqual(uk_dates.source_updated_at, datetime(2026, 8, 20, tzinfo=UTC))
        self.assertEqual(uk_dates.approval_valid_from, date(2013, 9, 13))
        self.assertEqual(uk_dates.approval_valid_to, date(2026, 8, 20))
        self.assertEqual(
            uk_dates.approval_last_verified_at,
            datetime(2026, 8, 20, 14, 35, tzinfo=UTC),
        )

        excel_serial = parse_company_row(
            {
                "company_name": "Excel Serial Test",
                "source_updated_at": "46254.5",
                "authority_code": "UK_CAA",
                "approval_type": "PART_145",
                "approval_number": "UK.145.SERIAL",
                "approval_valid_from": "46254",
            },
            3,
        )
        self.assertEqual(
            excel_serial.source_updated_at,
            datetime(2026, 8, 20, 12, tzinfo=UTC),
        )
        self.assertEqual(excel_serial.approval_valid_from, date(2026, 8, 20))

    def test_slash_dates_are_day_first_but_unambiguous_us_dates_are_accepted(self) -> None:
        parsed = parse_company_row(
            {
                "company_name": "Date Order Test",
                "source_updated_at": "09/10/2026",
                "authority_code": "UK_CAA",
                "approval_type": "PART_145",
                "approval_number": "UK.145.ORDER",
                "approval_valid_from": "8/20/2026",
            },
            2,
        )
        self.assertEqual(parsed.source_updated_at, datetime(2026, 10, 9, tzinfo=UTC))
        self.assertEqual(parsed.approval_valid_from, date(2026, 8, 20))

    def test_template_round_trips_and_unknown_columns_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "companies.csv"
            write_company_template(template)
            rows = load_company_csv(template)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].approval_type, "PART_145")

            invalid = Path(directory) / "invalid.csv"
            with invalid.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=("company_name", "typo_field"))
                writer.writeheader()
                writer.writerow({"company_name": "Test", "typo_field": "value"})
            with self.assertRaisesRegex(CompanyImportError, "Unknown CSV column"):
                load_company_csv(invalid)

    def test_repository_template_header_matches_contract(self) -> None:
        path = Path(__file__).parents[1] / "schema" / "company-import-template.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(tuple(reader.fieldnames or ()), CSV_FIELDS)
            rows = list(reader)
        self.assertEqual(len(rows), 1)
        parse_company_row(rows[0], 2)


if __name__ == "__main__":
    unittest.main()
