from __future__ import annotations

import csv
from datetime import date
import io
from pathlib import Path
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from adsb_ingest.aircraft_registry import (
    AircraftRegistryError,
    CASA_HEADER,
    FAA_MASTER_HEADER,
    FAA_REFERENCE_HEADER,
    load_canada_zip,
    load_casa_zip,
    load_faa_zip,
    parse_canada_row,
    parse_casa_row,
    parse_faa_row,
)


def casa_row(*, mark: str = "22B", airframe: str = "Rotorcraft") -> dict[str, str]:
    row = {field: "" for field in CASA_HEADER}
    row.update(
        {
            "Mark": mark,
            "Manu": "ROBINSON HELICOPTER CO",
            "Model": "R22 BETA",
            "Serial": "1234",
            "MTOW": "622",
            "engnum": "1",
            "Engmanu": "LYCOMING",
            "Engtype": "Piston",
            "regType": "Full Registration",
            "regopName": "SOUTH TIPPERARY INVESTMENTS PTY LTD",
            "regopCountry": "Australia",
            "regopCommdate": "30/04/2026",
            "Datefirstreg": "01/05/2026",
            "Airframe": airframe,
            "Typecert": "H10WE",
            "Countrymanu": "United States of America",
            "Yearmanu": "2025",
            "ICAOtypedesig": "R22",
        }
    )
    return row


def write_casa_zip(path: Path, rows: list[dict[str, str]]) -> None:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CASA_HEADER, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("acrftreg.csv", stream.getvalue().encode("utf-8-sig"))


def canada_row(
    *,
    mark: str = "GIIZ",
    binary_address: str = "110000000101101010110010",
    manufacturer: str = "Boeing",
    model: str = "737-8",
    category: str = "Aeroplane",
    effective_date: str = "2026/07/21",
    ineffective_date: str = "",
) -> list[str]:
    row = [""] * 47
    row[0] = mark
    row[1] = "Continuing Registration"
    row[3] = manufacturer
    row[4] = model
    row[5] = "68409"
    row[10] = category
    row[15] = "Turbo Fan"
    row[17] = "2"
    row[18] = "189"
    row[21] = effective_date
    row[22] = effective_date
    row[23] = ineffective_date
    row[31] = "2026/01/01"
    row[32] = "CANADA"
    row[34] = "Alberta"
    row[36] = "YYC"
    row[37] = "A146"
    row[38] = "Registered"
    row[41] = effective_date
    row[42] = binary_address
    row[46] = mark
    return row


def write_canada_zip(path: Path, rows: list[list[str]]) -> None:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerows(rows)
    stream.write(f"\r\n{len(rows)} rows selected.\r\n")
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("carscurr.txt", stream.getvalue().encode("cp1252"))
        archive.writestr("carsownr.txt", b"")
        archive.writestr("carslayout.txt", b"fixture")


def faa_reference(*, type_aircraft: str = "5") -> dict[str, str]:
    return {
        "CODE": "1234567",
        "MFR": "BOEING",
        "MODEL": "737-8",
        "TYPE-ACFT": type_aircraft,
        "TYPE-ENG": "5",
        "AC-CAT": "1",
        "BUILD-CERT-IND": "0",
        "NO-ENG": "02",
        "NO-SEATS": "189",
        "AC-WEIGHT": "CLASS 3",
        "SPEED": "0500",
        "TC-DATA-SHEET": "A16WE",
        "TC-DATA-HOLDER": "BOEING",
        "": "",
    }


def faa_master(
    *, status: str = "V", type_aircraft: str = "5", address: str = "A12345"
) -> dict[str, str]:
    row = {field: "" for field in FAA_MASTER_HEADER}
    row.update(
        {
            "N-NUMBER": "123AB",
            "SERIAL NUMBER": "12345",
            "MFR MDL CODE": "1234567",
            "ENG MFR MDL": "12345",
            "YEAR MFR": "2024",
            "LAST ACTION DATE": "20260721",
            "CERT ISSUE DATE": "20260721",
            "CERTIFICATION": "1",
            "TYPE AIRCRAFT": type_aircraft,
            "TYPE ENGINE": "5",
            "STATUS CODE": status,
            "MODE S CODE HEX": address,
            "AIR WORTH DATE": "20260721",
            "EXPIRATION DATE": "20330731",
            "UNIQUE ID": "00000001",
        }
    )
    return row


def write_faa_zip(path: Path, rows: list[dict[str, str]]) -> None:
    master_stream = io.StringIO(newline="")
    master_writer = csv.DictWriter(
        master_stream, fieldnames=FAA_MASTER_HEADER, lineterminator="\r\n"
    )
    master_writer.writeheader()
    master_writer.writerows(rows)
    reference_stream = io.StringIO(newline="")
    reference_writer = csv.DictWriter(
        reference_stream, fieldnames=FAA_REFERENCE_HEADER, lineterminator="\r\n"
    )
    reference_writer.writeheader()
    reference_writer.writerow(faa_reference())
    members = {
        "ardata.pdf": b"fixture",
        "ACFTREF.txt": reference_stream.getvalue().encode("utf-8-sig"),
        "ENGINE.txt": b"",
        "DEALER.txt": b"",
        "MASTER.txt": master_stream.getvalue().encode("utf-8-sig"),
        "RESERVED.txt": b"",
        "DEREG.txt": b"",
        "DOCINDEX.txt": b"",
    }
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


class CanadaRegistryTests(unittest.TestCase):
    def test_parses_identity_category_and_effective_date(self) -> None:
        record = parse_canada_row(canada_row(), 1)
        self.assertEqual(record.registration, "C-GIIZ")
        self.assertEqual(record.address, "c05ab2")
        self.assertEqual(record.manufacturer, "Boeing")
        self.assertEqual(record.model, "737-8")
        self.assertEqual(record.official_category, "FIXED_WING")
        self.assertEqual(record.effective_date, date(2026, 7, 21))
        self.assertEqual(record.number_of_engines, 2)

    def test_maps_helicopter_to_rotorcraft(self) -> None:
        record = parse_canada_row(
            canada_row(
                mark="AAAA",
                binary_address="110000000000000000000000",
                manufacturer="Bell",
                model="206B",
                category="Helicopter",
            ),
            1,
        )
        self.assertEqual(record.official_category, "ROTORCRAFT")

    def test_flags_impossible_ineffective_date(self) -> None:
        record = parse_canada_row(
            canada_row(ineffective_date="2026/01/01"), 1
        )
        self.assertIsNone(record.ineffective_date)
        self.assertEqual(
            record.quality_flags, ("INEFFECTIVE_DATE_BEFORE_EFFECTIVE_DATE",)
        )

    def test_loads_expected_zip_and_checks_footer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ccar.zip"
            write_canada_zip(path, [canada_row()])
            records = load_canada_zip(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].address, "c05ab2")

    def test_rejects_unexpected_zip_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ccar.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr("carscurr.txt", b"")
                archive.writestr("carsownr.txt", b"")
                archive.writestr("carslayout.txt", b"")
                archive.writestr("unexpected.txt", b"")
            with self.assertRaisesRegex(AircraftRegistryError, "must contain only"):
                load_canada_zip(path)


class FaaRegistryTests(unittest.TestCase):
    def test_parses_current_aircraft_identity_and_category(self) -> None:
        record = parse_faa_row(faa_master(), faa_reference(), 2)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.registration, "N123AB")
        self.assertEqual(record.address, "a12345")
        self.assertEqual(record.manufacturer, "BOEING")
        self.assertEqual(record.model, "737-8")
        self.assertEqual(record.official_category, "FIXED_WING")
        self.assertEqual(record.effective_date, date(2026, 7, 21))
        self.assertEqual(record.number_of_engines, 2)

    def test_maps_rotorcraft_and_flags_reference_disagreement(self) -> None:
        record = parse_faa_row(
            faa_master(type_aircraft="6"), faa_reference(type_aircraft="5"), 2
        )
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.official_category, "ROTORCRAFT")
        self.assertIn("MASTER_REFERENCE_TYPE_MISMATCH", record.quality_flags)

    def test_excludes_noncurrent_status(self) -> None:
        self.assertIsNone(parse_faa_row(faa_master(status="7"), faa_reference(), 2))

    def test_loads_documented_archive_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "faa.zip"
            write_faa_zip(path, [faa_master()])
            records = load_faa_zip(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].registration, "N123AB")

    def test_rejects_unexpected_archive_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "faa.zip"
            write_faa_zip(path, [faa_master()])
            with ZipFile(path, "a") as archive:
                archive.writestr("unexpected.txt", b"")
            with self.assertRaisesRegex(AircraftRegistryError, "members do not match"):
                load_faa_zip(path)


class CasaRegistryTests(unittest.TestCase):
    def test_parses_registration_category_type_and_operator(self) -> None:
        record = parse_casa_row(casa_row(), 2)
        self.assertEqual(record.registration, "VH-22B")
        self.assertIsNone(record.address)
        self.assertEqual(record.official_category, "ROTORCRAFT")
        self.assertEqual(record.official_type_code, "R22")
        self.assertEqual(
            record.registered_operator, "SOUTH TIPPERARY INVESTMENTS PTY LTD"
        )
        self.assertEqual(record.operator_effective_date, date(2026, 4, 30))

    def test_maps_current_rpa_categories_to_uav(self) -> None:
        record = parse_casa_row(
            casa_row(mark="ABC", airframe="RPA - Rotorcraft"), 2
        )
        self.assertEqual(record.official_category, "UAV")

    def test_loads_only_documented_csv_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "casa.zip"
            write_casa_zip(path, [casa_row()])
            records = load_casa_zip(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].registration, "VH-22B")

    def test_rejects_unexpected_archive_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "casa.zip"
            write_casa_zip(path, [casa_row()])
            with ZipFile(path, "a") as archive:
                archive.writestr("unexpected.txt", b"")
            with self.assertRaisesRegex(AircraftRegistryError, "only acrftreg.csv"):
                load_casa_zip(path)


if __name__ == "__main__":
    unittest.main()
