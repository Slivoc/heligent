from __future__ import annotations

import argparse
import csv
import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable
from zipfile import BadZipFile, ZipFile

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import requests

from .postgres import PostgresStore, _copy_rows


PROJECT_ROOT = Path(__file__).parents[2]
PHASE11_MIGRATION = PROJECT_ROOT / "schema" / "phase11.sql"
CANADA_SOURCE_CODE = "TC_CCAR"
CANADA_DOWNLOAD_URL = (
    "https://wwwapps.tc.gc.ca/Saf-Sec-Sur/2/CCARCS-RIACC/"
    "DDZip.aspx?lang=eng"
)
CANADA_ATTRIBUTION = (
    "Includes data provided by the Government of Canada; no endorsement is implied."
)
FAA_SOURCE_CODE = "FAA_AIRCRAFT_REGISTRY"
FAA_DOWNLOAD_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
FAA_ATTRIBUTION = "Source: Federal Aviation Administration Civil Aviation Registry."
CASA_SOURCE_CODE = "CASA_AIRCRAFT_REGISTER"
CASA_DOWNLOAD_URL = "https://services.casa.gov.au/CSV/acrftreg.zip"
CASA_ATTRIBUTION = (
    "Source: Civil Aviation Safety Authority (Australia), Australian Civil "
    "Aircraft Register; licensed under CC BY 4.0. Changes: normalized fields "
    "and category mappings."
)
EXPECTED_CANADA_FILES = frozenset({"carscurr.txt", "carsownr.txt", "carslayout.txt"})
EXPECTED_FAA_FILES = frozenset(
    {
        "ardata.pdf",
        "acftref.txt",
        "engine.txt",
        "dealer.txt",
        "master.txt",
        "reserved.txt",
        "dereg.txt",
        "docindex.txt",
    }
)
MAX_CANADA_ZIP_BYTES = 32 * 1024 * 1024
MAX_CANADA_MEMBER_BYTES = 128 * 1024 * 1024
MAX_FAA_ZIP_BYTES = 128 * 1024 * 1024
MAX_FAA_MEMBER_BYTES = 384 * 1024 * 1024
MAX_FAA_EXPANDED_BYTES = 768 * 1024 * 1024
MAX_CASA_ZIP_BYTES = 16 * 1024 * 1024
MAX_CASA_MEMBER_BYTES = 32 * 1024 * 1024
CANADA_CURRENT_COLUMNS = 47
HEX_RE = re.compile(r"^[0-9a-f]{6}$")
FAA_N_NUMBER_RE = re.compile(r"^[1-9][0-9]{0,4}[A-HJ-NP-Z]{0,2}$")
FAA_USABLE_STATUSES = frozenset({"V", "R", "M", "N", "T"})
FAA_MASTER_HEADER = (
    "N-NUMBER", "SERIAL NUMBER", "MFR MDL CODE", "ENG MFR MDL", "YEAR MFR",
    "TYPE REGISTRANT", "NAME", "STREET", "STREET2", "CITY", "STATE",
    "ZIP CODE", "REGION", "COUNTY", "COUNTRY", "LAST ACTION DATE",
    "CERT ISSUE DATE", "CERTIFICATION", "TYPE AIRCRAFT", "TYPE ENGINE",
    "STATUS CODE", "MODE S CODE", "FRACT OWNER", "AIR WORTH DATE",
    "OTHER NAMES(1)", "OTHER NAMES(2)", "OTHER NAMES(3)", "OTHER NAMES(4)",
    "OTHER NAMES(5)", "EXPIRATION DATE", "UNIQUE ID", "KIT MFR", " KIT MODEL",
    "MODE S CODE HEX", "",
)
FAA_REFERENCE_HEADER = (
    "CODE", "MFR", "MODEL", "TYPE-ACFT", "TYPE-ENG", "AC-CAT",
    "BUILD-CERT-IND", "NO-ENG", "NO-SEATS", "AC-WEIGHT", "SPEED",
    "TC-DATA-SHEET", "TC-DATA-HOLDER", "",
)
CASA_HEADER = (
    "Mark", "Manu", "Type", "Model", "Serial", "MTOW", "engnum", "Engmanu",
    "Engtype", "Engmodel", "Fueltype", "regType", "regholdname", "regholdadd1",
    "regholdadd2", "regholdSuburb", "regholdState", "regholdPostcode",
    "regholdCountry", "regholdCommdate", "regopName", "regopadd1", "regopadd2",
    "regopSuburb", "regopState", "regopPostcode", "regopCountry", "regopCommdate",
    "Datefirstreg", "gear", "Airframe", "CoAcata", "CoAcatb", "CoAcatc",
    "Propmanu", "Propmodel", "Typecert", "Countrymanu", "Yearmanu",
    "Regexpirydate", "suspendstatus", "suspenddate", "ICAOtypedesig",
    "IDERA_Authorised_Party",
)


class AircraftRegistryError(RuntimeError):
    """Raised when an official registry cannot be safely parsed or imported."""


class _HiddenInputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        attributes = {key.lower(): value for key, value in attrs}
        if attributes.get("type", "").lower() != "hidden":
            return
        name = attributes.get("name")
        if name:
            self.values[name] = attributes.get("value") or ""


@dataclass(frozen=True)
class CanadaAircraftRecord:
    registration: str
    address: str
    manufacturer: str
    model: str
    serial_number: str | None
    registry_category: str
    official_category: str
    registration_sub_type: str | None
    registration_status: str | None
    issue_date: date | None
    effective_date: date
    ineffective_date: date | None
    modified_date: date | None
    manufacture_date: date | None
    base_country: str | None
    base_region: str | None
    base_location: str | None
    type_certificate_number: str | None
    engine_category: str | None
    number_of_engines: int | None
    number_of_seats: int | None
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class FaaAircraftRecord:
    registration: str
    address: str
    manufacturer: str
    model: str
    serial_number: str | None
    registry_category: str
    official_category: str
    registration_sub_type: str | None
    registration_status: str
    issue_date: date | None
    effective_date: date
    ineffective_date: date | None
    modified_date: date
    manufacture_date: date | None
    base_country: str | None
    base_region: str | None
    base_location: str | None
    type_certificate_number: str | None
    engine_category: str | None
    number_of_engines: int | None
    number_of_seats: int | None
    quality_flags: tuple[str, ...] = ()
    source_fields: tuple[tuple[str, str | int | None], ...] = ()


@dataclass(frozen=True)
class CasaAircraftRecord:
    registration: str
    address: None
    manufacturer: str
    model: str
    serial_number: str | None
    registry_category: str
    official_category: str
    registration_sub_type: str | None
    registration_status: str | None
    issue_date: date | None
    effective_date: date
    ineffective_date: date | None
    modified_date: date | None
    manufacture_date: date | None
    base_country: str | None
    base_region: str | None
    base_location: str | None
    type_certificate_number: str | None
    engine_category: str | None
    number_of_engines: int | None
    number_of_seats: int | None
    official_type_code: str | None
    registered_operator: str
    operator_effective_date: date
    operator_country: str | None
    operator_geographic_region: str | None
    quality_flags: tuple[str, ...] = ()
    source_fields: tuple[tuple[str, str | int | None], ...] = ()


def _text(value: str) -> str | None:
    cleaned = " ".join(value.split())
    return cleaned or None


def _required_text(value: str, field: str, row_number: int) -> str:
    cleaned = _text(value)
    if not cleaned:
        raise AircraftRegistryError(f"Canada row {row_number}: {field} is blank")
    return cleaned


def _date(value: str, field: str, row_number: int) -> date | None:
    cleaned = _text(value)
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned, "%Y/%m/%d").date()
    except ValueError as exc:
        raise AircraftRegistryError(
            f"Canada row {row_number}: {field} must use YYYY/MM/DD"
        ) from exc


def _integer(value: str, field: str, row_number: int) -> int | None:
    cleaned = _text(value)
    if not cleaned:
        return None
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise AircraftRegistryError(
            f"Canada row {row_number}: {field} must be an integer"
        ) from exc
    if parsed < 0:
        raise AircraftRegistryError(
            f"Canada row {row_number}: {field} cannot be negative"
        )
    return parsed


def _canada_category(value: str, row_number: int) -> str:
    normalized = " ".join(value.split()).lower()
    categories = {
        "aeroplane": "FIXED_WING",
        "helicopter": "ROTORCRAFT",
        "gyroplane": "ROTORCRAFT",
        "glider": "GLIDER",
        "balloon": "BALLOON",
        "airship": "BALLOON",
        "unmanned aircraft": "UAV",
        "ornithopter": "OTHER",
    }
    result = categories.get(normalized)
    if result is None:
        raise AircraftRegistryError(
            f"Canada row {row_number}: unsupported aircraft category {value!r}"
        )
    return result


def _canada_address(binary: str, row_number: int) -> str:
    compact = "".join(binary.split())
    if not re.fullmatch(r"[01]{24}", compact):
        raise AircraftRegistryError(
            f"Canada row {row_number}: Mode-S value must contain 24 binary digits"
        )
    address = f"{int(compact, 2):06x}"
    if not HEX_RE.fullmatch(address):
        raise AssertionError("Converted Canadian Mode-S address is invalid")
    return address


def parse_canada_row(row: list[str], row_number: int) -> CanadaAircraftRecord:
    if len(row) != CANADA_CURRENT_COLUMNS:
        raise AircraftRegistryError(
            f"Canada row {row_number}: expected {CANADA_CURRENT_COLUMNS} columns, "
            f"found {len(row)}"
        )
    mark = _required_text(row[46], "trimmed mark", row_number).upper()
    if not re.fullmatch(r"[A-Z]{3,4}", mark):
        raise AircraftRegistryError(
            f"Canada row {row_number}: invalid registration suffix {mark!r}"
        )
    effective_date = _date(row[22], "effective date", row_number)
    if effective_date is None:
        raise AircraftRegistryError(f"Canada row {row_number}: effective date is blank")
    ineffective_date = _date(row[23], "ineffective date", row_number)
    quality_flags: list[str] = []
    if ineffective_date is not None and ineffective_date < effective_date:
        ineffective_date = None
        quality_flags.append("INEFFECTIVE_DATE_BEFORE_EFFECTIVE_DATE")
    registry_category = _required_text(row[10], "aircraft category", row_number)
    return CanadaAircraftRecord(
        registration=f"C-{mark}",
        address=_canada_address(row[42], row_number),
        manufacturer=_required_text(row[3], "manufacturer", row_number),
        model=_required_text(row[4], "model", row_number),
        serial_number=_text(row[5]),
        registry_category=registry_category,
        official_category=_canada_category(registry_category, row_number),
        registration_sub_type=_text(row[1]),
        registration_status=_text(row[38]),
        issue_date=_date(row[21], "issue date", row_number),
        effective_date=effective_date,
        ineffective_date=ineffective_date,
        modified_date=_date(row[41], "modified date", row_number),
        manufacture_date=_date(row[31], "manufacture date", row_number),
        base_country=_text(row[32]),
        base_region=_text(row[34]),
        base_location=_text(row[36]),
        type_certificate_number=_text(row[37]),
        engine_category=_text(row[15]),
        number_of_engines=_integer(row[17], "number of engines", row_number),
        number_of_seats=_integer(row[18], "number of seats", row_number),
        quality_flags=tuple(quality_flags),
    )


def load_canada_zip(path: Path) -> list[CanadaAircraftRecord]:
    if not path.is_file():
        raise AircraftRegistryError(f"Canada registry ZIP does not exist: {path}")
    if path.stat().st_size > MAX_CANADA_ZIP_BYTES:
        raise AircraftRegistryError("Canada registry ZIP exceeds the 32 MiB safety limit")
    try:
        with ZipFile(path) as archive:
            names = {item.filename.lower() for item in archive.infolist() if not item.is_dir()}
            if names != EXPECTED_CANADA_FILES:
                raise AircraftRegistryError(
                    "Canada registry ZIP must contain only carscurr.txt, carsownr.txt, "
                    "and carslayout.txt"
                )
            for item in archive.infolist():
                if item.file_size > MAX_CANADA_MEMBER_BYTES:
                    raise AircraftRegistryError(
                        f"Canada registry member {item.filename} exceeds the safety limit"
                    )
            with archive.open("carscurr.txt") as binary:
                stream = io.TextIOWrapper(
                    binary, encoding="cp1252", errors="strict", newline=""
                )
                records: list[CanadaAircraftRecord] = []
                registrations: set[str] = set()
                addresses: set[str] = set()
                for row_number, row in enumerate(csv.reader(stream), start=1):
                    if not row or (len(row) == 1 and not row[0].strip("\x1a\ufeff \t\r\n")):
                        continue
                    if len(row) == 1 and re.fullmatch(
                        r"\d+ rows selected\.", row[0].strip()
                    ):
                        expected_rows = int(row[0].split()[0])
                        if expected_rows != len(records):
                            raise AircraftRegistryError(
                                "Canada registry footer row count does not match parsed records"
                            )
                        continue
                    record = parse_canada_row(row, row_number)
                    if record.registration in registrations:
                        raise AircraftRegistryError(
                            f"Canada row {row_number}: duplicate registration "
                            f"{record.registration}"
                        )
                    if record.address in addresses:
                        raise AircraftRegistryError(
                            f"Canada row {row_number}: duplicate Mode-S address {record.address}"
                        )
                    registrations.add(record.registration)
                    addresses.add(record.address)
                    records.append(record)
    except (BadZipFile, UnicodeDecodeError, KeyError) as exc:
        raise AircraftRegistryError(f"Invalid Canada registry ZIP: {exc}") from exc
    if not records:
        raise AircraftRegistryError("Canada registry contains no aircraft records")
    return records


FAA_TYPE_CATEGORIES = {
    "1": ("Glider", "GLIDER"),
    "2": ("Balloon", "BALLOON"),
    "3": ("Blimp/Dirigible", "BALLOON"),
    "4": ("Fixed wing single engine", "FIXED_WING"),
    "5": ("Fixed wing multi engine", "FIXED_WING"),
    "6": ("Rotorcraft", "ROTORCRAFT"),
    "7": ("Weight-shift-control", "OTHER"),
    "8": ("Powered Parachute", "OTHER"),
    "9": ("Gyroplane", "ROTORCRAFT"),
    "H": ("Hybrid Lift", "OTHER"),
    "O": ("Other", "OTHER"),
}
FAA_ENGINE_TYPES = {
    "0": "None",
    "1": "Reciprocating",
    "2": "Turbo-prop",
    "3": "Turbo-shaft",
    "4": "Turbo-jet",
    "5": "Turbo-fan",
    "6": "Ramjet",
    "7": "2 Cycle",
    "8": "4 Cycle",
    "9": "Unknown",
    "10": "Electric",
    "11": "Rotary",
}
FAA_STATUS_DESCRIPTIONS = {
    "V": "Valid Registration",
    "R": "Registration Pending",
    "M": "Registered to Manufacturer under Dealer Certificate",
    "N": "Non-citizen Corporation Flight-hour Report Outstanding",
    "T": "Valid Registration from a Trainee",
}
FAA_KNOWN_STATUSES = frozenset(
    {"A", "D", "E", "M", "N", "R", "S", "T", "V", "W", "X", "Z"}
    | {str(value) for value in range(1, 30)}
)


def _faa_date(value: str, field: str, row_number: int) -> date | None:
    cleaned = _text(value)
    if not cleaned:
        return None
    if not re.fullmatch(r"\d{8}", cleaned):
        raise AircraftRegistryError(
            f"FAA row {row_number}: {field} must use YYYYMMDD"
        )
    try:
        return datetime.strptime(cleaned, "%Y%m%d").date()
    except ValueError as exc:
        raise AircraftRegistryError(
            f"FAA row {row_number}: {field} is not a valid date"
        ) from exc


def _faa_integer(value: str, field: str, row_number: int) -> int | None:
    cleaned = _text(value)
    if not cleaned:
        return None
    if not cleaned.isdigit():
        raise AircraftRegistryError(f"FAA row {row_number}: {field} must be numeric")
    return int(cleaned)


def parse_faa_row(
    row: dict[str, str], reference: dict[str, str], row_number: int
) -> FaaAircraftRecord | None:
    status_code = _required_text(row["STATUS CODE"], "status code", row_number)
    if status_code not in FAA_KNOWN_STATUSES:
        raise AircraftRegistryError(
            f"FAA row {row_number}: unsupported status code {status_code!r}"
        )
    if status_code not in FAA_USABLE_STATUSES:
        return None

    n_number = _required_text(row["N-NUMBER"], "N-number", row_number).upper()
    if len(n_number) > 5 or not FAA_N_NUMBER_RE.fullmatch(n_number):
        raise AircraftRegistryError(
            f"FAA row {row_number}: invalid N-number {n_number!r}"
        )
    address = _required_text(
        row["MODE S CODE HEX"], "Mode-S hexadecimal code", row_number
    ).lower()
    if not HEX_RE.fullmatch(address) or not address.startswith("a"):
        raise AircraftRegistryError(
            f"FAA row {row_number}: invalid Mode-S hexadecimal code {address!r}"
        )

    type_code = _required_text(row["TYPE AIRCRAFT"], "aircraft type", row_number)
    category = FAA_TYPE_CATEGORIES.get(type_code)
    if category is None:
        raise AircraftRegistryError(
            f"FAA row {row_number}: unsupported aircraft type code {type_code!r}"
        )
    reference_type = _required_text(
        reference["TYPE-ACFT"], "reference aircraft type", row_number
    )
    if reference_type not in FAA_TYPE_CATEGORIES:
        raise AircraftRegistryError(
            f"FAA row {row_number}: unsupported reference aircraft type "
            f"{reference_type!r}"
        )
    engine_type = _required_text(
        reference["TYPE-ENG"], "reference engine type", row_number
    )
    if engine_type not in FAA_ENGINE_TYPES:
        raise AircraftRegistryError(
            f"FAA row {row_number}: unsupported engine type code {engine_type!r}"
        )

    modified_date = _faa_date(row["LAST ACTION DATE"], "last action date", row_number)
    if modified_date is None:
        raise AircraftRegistryError(f"FAA row {row_number}: last action date is blank")
    issue_date = _faa_date(row["CERT ISSUE DATE"], "certificate issue date", row_number)
    effective_date = issue_date or modified_date
    ineffective_date = _faa_date(
        row["EXPIRATION DATE"], "certificate expiration date", row_number
    )
    quality_flags: list[str] = []
    if issue_date is None:
        quality_flags.append("CERTIFICATE_ISSUE_DATE_MISSING")
    if ineffective_date is not None and ineffective_date < effective_date:
        ineffective_date = None
        quality_flags.append("EXPIRATION_DATE_BEFORE_EFFECTIVE_DATE")
    if type_code != reference_type:
        quality_flags.append("MASTER_REFERENCE_TYPE_MISMATCH")

    manufacture_year = _faa_integer(row["YEAR MFR"], "manufacture year", row_number)
    if manufacture_year == 0:
        manufacture_year = None
        quality_flags.append("MANUFACTURE_YEAR_ZERO_TREATED_AS_MISSING")
    elif manufacture_year is not None and not 1800 <= manufacture_year <= 2200:
        raise AircraftRegistryError(
            f"FAA row {row_number}: manufacture year is outside the safety range"
        )
    source_fields: tuple[tuple[str, str | int | None], ...] = (
        ("faa_status_code", status_code),
        ("faa_model_code", _text(row["MFR MDL CODE"])),
        ("faa_engine_model_code", _text(row["ENG MFR MDL"])),
        ("faa_type_aircraft_code", type_code),
        ("faa_reference_type_aircraft_code", reference_type),
        ("faa_type_engine_code", engine_type),
        ("faa_aircraft_category_code", _text(reference["AC-CAT"])),
        ("faa_builder_certification_code", _text(reference["BUILD-CERT-IND"])),
        ("faa_aircraft_weight", _text(reference["AC-WEIGHT"])),
        ("faa_unique_id", _text(row["UNIQUE ID"])),
        ("manufacture_year", manufacture_year),
        ("airworthiness_date", _text(row["AIR WORTH DATE"])),
        ("certification", _text(row["CERTIFICATION"])),
        ("kit_manufacturer", _text(row["KIT MFR"])),
        ("kit_model", _text(row[" KIT MODEL"])),
    )
    return FaaAircraftRecord(
        registration=f"N{n_number}",
        address=address,
        manufacturer=_required_text(reference["MFR"], "manufacturer", row_number),
        model=_required_text(reference["MODEL"], "model", row_number),
        serial_number=_text(row["SERIAL NUMBER"]),
        registry_category=category[0],
        official_category=category[1],
        registration_sub_type=None,
        registration_status=f"{status_code} - {FAA_STATUS_DESCRIPTIONS.get(status_code, status_code)}",
        issue_date=issue_date,
        effective_date=effective_date,
        ineffective_date=ineffective_date,
        modified_date=modified_date,
        manufacture_date=None,
        base_country=None,
        base_region=None,
        base_location=None,
        type_certificate_number=_text(reference["TC-DATA-SHEET"]),
        engine_category=FAA_ENGINE_TYPES[engine_type],
        number_of_engines=_faa_integer(reference["NO-ENG"], "number of engines", row_number),
        number_of_seats=_faa_integer(reference["NO-SEATS"], "number of seats", row_number),
        quality_flags=tuple(quality_flags),
        source_fields=source_fields,
    )


def _faa_reader(binary: Any, expected_header: tuple[str, ...], filename: str) -> Any:
    stream = io.TextIOWrapper(binary, encoding="utf-8-sig", errors="strict", newline="")
    reader = csv.DictReader(stream)
    if tuple(reader.fieldnames or ()) != expected_header:
        raise AircraftRegistryError(f"FAA {filename} header does not match its specification")
    return reader


def load_faa_zip(path: Path) -> list[FaaAircraftRecord]:
    if not path.is_file():
        raise AircraftRegistryError(f"FAA registry ZIP does not exist: {path}")
    if path.stat().st_size > MAX_FAA_ZIP_BYTES:
        raise AircraftRegistryError("FAA registry ZIP exceeds the 128 MiB safety limit")
    try:
        with ZipFile(path) as archive:
            file_items = [item for item in archive.infolist() if not item.is_dir()]
            names = {item.filename.lower() for item in file_items}
            if names != EXPECTED_FAA_FILES:
                raise AircraftRegistryError(
                    "FAA registry ZIP members do not match the documented official archive"
                )
            if any(item.file_size > MAX_FAA_MEMBER_BYTES for item in file_items):
                raise AircraftRegistryError("FAA registry member exceeds the safety limit")
            if sum(item.file_size for item in file_items) > MAX_FAA_EXPANDED_BYTES:
                raise AircraftRegistryError("FAA registry expanded size exceeds the safety limit")
            actual_names = {item.filename.lower(): item.filename for item in file_items}

            references: dict[str, dict[str, str]] = {}
            with archive.open(actual_names["acftref.txt"]) as binary:
                for row_number, row in enumerate(
                    _faa_reader(binary, FAA_REFERENCE_HEADER, "ACFTREF.txt"), start=2
                ):
                    if None in row or row.get("") not in {"", None}:
                        raise AircraftRegistryError(
                            f"FAA ACFTREF row {row_number} has an unexpected column"
                        )
                    code = _required_text(row["CODE"], "reference code", row_number)
                    if code in references:
                        raise AircraftRegistryError(
                            f"FAA ACFTREF row {row_number}: duplicate code {code}"
                        )
                    references[code] = row

            records: list[FaaAircraftRecord] = []
            registrations: set[str] = set()
            addresses: set[str] = set()
            with archive.open(actual_names["master.txt"]) as binary:
                for row_number, row in enumerate(
                    _faa_reader(binary, FAA_MASTER_HEADER, "MASTER.txt"), start=2
                ):
                    if None in row or row.get("") not in {"", None}:
                        raise AircraftRegistryError(
                            f"FAA MASTER row {row_number} has an unexpected column"
                        )
                    model_code = _required_text(
                        row["MFR MDL CODE"], "manufacturer/model code", row_number
                    )
                    reference = references.get(model_code)
                    if reference is None:
                        raise AircraftRegistryError(
                            f"FAA row {row_number}: unknown manufacturer/model code "
                            f"{model_code}"
                        )
                    record = parse_faa_row(row, reference, row_number)
                    if record is None:
                        continue
                    if record.registration in registrations:
                        raise AircraftRegistryError(
                            f"FAA row {row_number}: duplicate registration "
                            f"{record.registration}"
                        )
                    if record.address in addresses:
                        raise AircraftRegistryError(
                            f"FAA row {row_number}: duplicate Mode-S address {record.address}"
                        )
                    registrations.add(record.registration)
                    addresses.add(record.address)
                    records.append(record)
    except (BadZipFile, UnicodeDecodeError, KeyError) as exc:
        raise AircraftRegistryError(f"Invalid FAA registry ZIP: {exc}") from exc
    if not records:
        raise AircraftRegistryError("FAA registry contains no usable aircraft records")
    return records


CASA_AIRFRAME_CATEGORIES = {
    "power driven aeroplane": "FIXED_WING",
    "rotorcraft": "ROTORCRAFT",
    "glider": "GLIDER",
    "motor-glider": "GLIDER",
    "manned free balloon": "BALLOON",
    "captive balloon": "BALLOON",
    "airship": "BALLOON",
    "airship (gas)": "BALLOON",
    "ornithopter": "OTHER",
    "unclassified": "OTHER",
    "rpa - powered lift": "UAV",
    "rpa - rotorcraft": "UAV",
    "rpa - power driven aeroplane": "UAV",
    "rpa - airship": "UAV",
}


def _casa_required(value: str, field: str, row_number: int) -> str:
    cleaned = _text(value)
    if not cleaned:
        raise AircraftRegistryError(f"CASA row {row_number}: {field} is blank")
    return cleaned


def _casa_date(value: str, field: str, row_number: int) -> date | None:
    cleaned = _text(value)
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned, "%d/%m/%Y").date()
    except ValueError as exc:
        raise AircraftRegistryError(
            f"CASA row {row_number}: {field} must use DD/MM/YYYY"
        ) from exc


def _casa_integer(value: str, field: str, row_number: int) -> int | None:
    cleaned = _text(value)
    if not cleaned:
        return None
    if not cleaned.isdigit():
        raise AircraftRegistryError(f"CASA row {row_number}: {field} must be numeric")
    return int(cleaned)


def parse_casa_row(row: dict[str, str], row_number: int) -> CasaAircraftRecord:
    mark = _casa_required(row["Mark"], "registration mark", row_number).upper()
    if not re.fullmatch(r"[A-Z0-9]{1,5}", mark):
        raise AircraftRegistryError(
            f"CASA row {row_number}: invalid registration mark {mark!r}"
        )
    airframe = _casa_required(row["Airframe"], "airframe category", row_number)
    official_category = CASA_AIRFRAME_CATEGORIES.get(airframe.lower())
    if official_category is None:
        raise AircraftRegistryError(
            f"CASA row {row_number}: unsupported airframe category {airframe!r}"
        )
    effective_date = _casa_date(row["Datefirstreg"], "first registration date", row_number)
    operator_effective_date = _casa_date(
        row["regopCommdate"], "operator commencement date", row_number
    )
    if effective_date is None or operator_effective_date is None:
        raise AircraftRegistryError(
            f"CASA row {row_number}: registration and operator dates are required"
        )
    ineffective_date = _casa_date(
        row["Regexpirydate"], "registration expiry date", row_number
    )
    quality_flags: list[str] = []
    if ineffective_date is not None and ineffective_date < effective_date:
        ineffective_date = None
        quality_flags.append("EXPIRY_DATE_BEFORE_FIRST_REGISTRATION")
    type_code = _casa_required(
        row["ICAOtypedesig"], "ICAO type designator", row_number
    ).upper()
    if not re.fullmatch(r"[A-Z0-9-]{2,16}", type_code):
        raise AircraftRegistryError(
            f"CASA row {row_number}: invalid ICAO type designator {type_code!r}"
        )
    source_fields: tuple[tuple[str, str | int | None], ...] = (
        # Preserve CASA's published decimal text exactly; do not let spreadsheet
        # or binary floating-point coercion alter the value.
        ("maximum_takeoff_weight", _text(row["MTOW"])),
        ("engine_manufacturer", _text(row["Engmanu"])),
        ("engine_type", _text(row["Engtype"])),
        ("engine_model", _text(row["Engmodel"])),
        ("fuel_type", _text(row["Fueltype"])),
        ("landing_gear", _text(row["gear"])),
        ("certificate_category_a", _text(row["CoAcata"])),
        ("certificate_category_b", _text(row["CoAcatb"])),
        ("certificate_category_c", _text(row["CoAcatc"])),
        ("propeller_manufacturer", _text(row["Propmanu"])),
        ("propeller_model", _text(row["Propmodel"])),
        ("country_of_manufacture", _text(row["Countrymanu"])),
        ("year_of_manufacture", _casa_integer(row["Yearmanu"], "year", row_number)),
        ("suspension_status", _text(row["suspendstatus"])),
        ("suspension_date", _text(row["suspenddate"])),
    )
    operator_country = _text(row["regopCountry"])
    operator_regions = {
        "Australia": "OCEANIA",
        "Fiji": "OCEANIA",
        "Hong Kong": "ASIA",
        "United Arab Emirates": "ASIA",
        "United Kingdom": "EUROPE",
        "United States of America": "NORTH_AMERICA",
    }
    return CasaAircraftRecord(
        registration=f"VH-{mark}",
        address=None,
        manufacturer=_casa_required(row["Manu"], "manufacturer", row_number),
        model=_casa_required(row["Model"], "model", row_number),
        serial_number=_text(row["Serial"]),
        registry_category=airframe,
        official_category=official_category,
        registration_sub_type=_text(row["regType"]),
        registration_status=_text(row["suspendstatus"]) or _text(row["regType"]),
        issue_date=None,
        effective_date=effective_date,
        ineffective_date=ineffective_date,
        modified_date=None,
        manufacture_date=None,
        base_country="Australia",
        base_region=None,
        base_location=None,
        type_certificate_number=_text(row["Typecert"]),
        engine_category=_text(row["Engtype"]),
        number_of_engines=_casa_integer(row["engnum"], "number of engines", row_number),
        number_of_seats=None,
        official_type_code=type_code,
        registered_operator=_casa_required(
            row["regopName"], "registered operator", row_number
        ),
        operator_effective_date=operator_effective_date,
        operator_country=operator_country,
        operator_geographic_region=operator_regions.get(operator_country or ""),
        quality_flags=tuple(quality_flags),
        source_fields=source_fields,
    )


def load_casa_zip(path: Path) -> list[CasaAircraftRecord]:
    if not path.is_file():
        raise AircraftRegistryError(f"CASA registry ZIP does not exist: {path}")
    if path.stat().st_size > MAX_CASA_ZIP_BYTES:
        raise AircraftRegistryError("CASA registry ZIP exceeds the 16 MiB safety limit")
    try:
        with ZipFile(path) as archive:
            items = [item for item in archive.infolist() if not item.is_dir()]
            if len(items) != 1 or items[0].filename.lower() != "acrftreg.csv":
                raise AircraftRegistryError(
                    "CASA registry ZIP must contain only acrftreg.csv"
                )
            if items[0].file_size > MAX_CASA_MEMBER_BYTES:
                raise AircraftRegistryError("CASA registry CSV exceeds the safety limit")
            with archive.open(items[0]) as binary:
                stream = io.TextIOWrapper(
                    binary, encoding="utf-8-sig", errors="strict", newline=""
                )
                reader = csv.DictReader(stream)
                if tuple(reader.fieldnames or ()) != CASA_HEADER:
                    raise AircraftRegistryError(
                        "CASA acrftreg.csv header does not match its specification"
                    )
                records: list[CasaAircraftRecord] = []
                registrations: set[str] = set()
                for row_number, row in enumerate(reader, start=2):
                    if None in row:
                        raise AircraftRegistryError(
                            f"CASA row {row_number} has an unexpected column"
                        )
                    record = parse_casa_row(row, row_number)
                    if record.registration in registrations:
                        raise AircraftRegistryError(
                            f"CASA row {row_number}: duplicate registration "
                            f"{record.registration}"
                        )
                    registrations.add(record.registration)
                    records.append(record)
    except (BadZipFile, UnicodeDecodeError, KeyError) as exc:
        raise AircraftRegistryError(f"Invalid CASA registry ZIP: {exc}") from exc
    if not records:
        raise AircraftRegistryError("CASA registry contains no aircraft records")
    return records


def download_canada_zip(output: Path, *, timeout: float = 90.0) -> Path:
    session = requests.Session()
    session.headers.update({"User-Agent": "heligent-aircraft-registry/0.1"})
    page = session.get(CANADA_DOWNLOAD_URL, timeout=timeout)
    page.raise_for_status()
    parser = _HiddenInputParser()
    parser.feed(page.text)
    if "__VIEWSTATE" not in parser.values:
        raise AircraftRegistryError("Transport Canada download page omitted VIEWSTATE")
    payload = dict(parser.values)
    payload["ctl00$ContentPlaceHolder1$btnDownload"] = "Download"
    response = session.post(CANADA_DOWNLOAD_URL, data=payload, timeout=timeout)
    response.raise_for_status()
    if len(response.content) > MAX_CANADA_ZIP_BYTES:
        raise AircraftRegistryError("Transport Canada response exceeds the safety limit")
    if not response.content.startswith(b"PK\x03\x04"):
        raise AircraftRegistryError("Transport Canada did not return a ZIP archive")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(response.content)
    return output


def download_faa_zip(output: Path, *, timeout: float = 300.0) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.part")
    try:
        with requests.get(
            FAA_DOWNLOAD_URL,
            headers={"User-Agent": "heligent-aircraft-registry/0.1"},
            stream=True,
            timeout=(15.0, timeout),
        ) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_FAA_ZIP_BYTES:
                raise AircraftRegistryError("FAA response exceeds the safety limit")
            written = 0
            with temporary.open("wb") as handle:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if not block:
                        continue
                    written += len(block)
                    if written > MAX_FAA_ZIP_BYTES:
                        raise AircraftRegistryError("FAA response exceeds the safety limit")
                    handle.write(block)
        with temporary.open("rb") as handle:
            if handle.read(4) != b"PK\x03\x04":
                raise AircraftRegistryError("FAA did not return a ZIP archive")
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output


def download_casa_zip(output: Path, *, timeout: float = 90.0) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.part")
    try:
        with requests.get(
            CASA_DOWNLOAD_URL,
            headers={"User-Agent": "heligent-aircraft-registry/0.1"},
            stream=True,
            timeout=(15.0, timeout),
        ) as response:
            response.raise_for_status()
            written = 0
            with temporary.open("wb") as handle:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if not block:
                        continue
                    written += len(block)
                    if written > MAX_CASA_ZIP_BYTES:
                        raise AircraftRegistryError("CASA response exceeds the safety limit")
                    handle.write(block)
        with temporary.open("rb") as handle:
            if handle.read(4) != b"PK\x03\x04":
                raise AircraftRegistryError("CASA did not return a ZIP archive")
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _record_row(
    batch_id: int,
    record: CanadaAircraftRecord | FaaAircraftRecord | CasaAircraftRecord,
) -> tuple[object, ...]:
    source_data = dict(getattr(record, "source_fields", ()))
    source_data["quality_flags"] = list(record.quality_flags)
    return (
        batch_id,
        record.registration,
        record.address,
        record.manufacturer,
        record.model,
        record.serial_number,
        record.registry_category,
        record.official_category,
        record.registration_sub_type,
        record.registration_status,
        record.issue_date,
        record.effective_date,
        record.ineffective_date,
        record.modified_date,
        record.manufacture_date,
        record.base_country,
        record.base_region,
        record.base_location,
        record.type_certificate_number,
        record.engine_category,
        record.number_of_engines,
        record.number_of_seats,
        getattr(record, "official_type_code", None),
        getattr(record, "registered_operator", None),
        getattr(record, "operator_effective_date", None),
        getattr(record, "operator_country", None),
        getattr(record, "operator_geographic_region", None),
        Jsonb(source_data),
    )


REGISTRY_RECORD_COLUMNS = (
    "import_batch_id",
    "registration",
    "address",
    "manufacturer",
    "model",
    "serial_number",
    "registry_category",
    "official_category",
    "registration_sub_type",
    "registration_status",
    "issue_date",
    "effective_date",
    "ineffective_date",
    "modified_date",
    "manufacture_date",
    "base_country",
    "base_region",
    "base_location",
    "type_certificate_number",
    "engine_category",
    "number_of_engines",
    "number_of_seats",
    "official_type_code",
    "registered_operator",
    "operator_effective_date",
    "operator_country",
    "operator_geographic_region",
    "source_data",
)


class AircraftRegistryStore:
    def __init__(self, dsn: str) -> None:
        self.store = PostgresStore(dsn)

    def apply_migration(self, path: Path = PHASE11_MIGRATION) -> None:
        self.store.apply_schema(path)

    def import_canada(
        self,
        records: Iterable[CanadaAircraftRecord],
        *,
        snapshot_date: date,
        downloaded_at: datetime,
        source_path: Path,
    ) -> dict[str, Any]:
        return self._import_records(
            records,
            source_code=CANADA_SOURCE_CODE,
            attribution=CANADA_ATTRIBUTION,
            snapshot_date=snapshot_date,
            downloaded_at=downloaded_at,
            source_path=source_path,
        )

    def import_faa(
        self,
        records: Iterable[FaaAircraftRecord],
        *,
        snapshot_date: date,
        downloaded_at: datetime,
        source_path: Path,
    ) -> dict[str, Any]:
        return self._import_records(
            records,
            source_code=FAA_SOURCE_CODE,
            attribution=FAA_ATTRIBUTION,
            snapshot_date=snapshot_date,
            downloaded_at=downloaded_at,
            source_path=source_path,
            extra_metadata={"usable_status_codes": sorted(FAA_USABLE_STATUSES)},
        )

    def import_casa(
        self,
        records: Iterable[CasaAircraftRecord],
        *,
        snapshot_date: date,
        downloaded_at: datetime,
        source_path: Path,
    ) -> dict[str, Any]:
        return self._import_records(
            records,
            source_code=CASA_SOURCE_CODE,
            attribution=CASA_ATTRIBUTION,
            snapshot_date=snapshot_date,
            downloaded_at=downloaded_at,
            source_path=source_path,
            extra_metadata={
                "operator_claim": "CASA Registered Operator",
                "operator_addresses_imported": False,
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
            },
        )

    def _import_records(
        self,
        records: Iterable[CanadaAircraftRecord | FaaAircraftRecord | CasaAircraftRecord],
        *,
        source_code: str,
        attribution: str,
        snapshot_date: date,
        downloaded_at: datetime,
        source_path: Path,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record_list = list(records)
        digest = _sha256(source_path)
        with self.store.connect() as connection:
            existing = connection.execute(
                """
                SELECT id, row_count
                FROM aircraft_registry_import_batch
                WHERE source_code = %s AND source_sha256 = %s
                """,
                (source_code, digest),
            ).fetchone()
            if existing:
                batch_id, row_count = existing
                return {
                    "status": "ALREADY_IMPORTED",
                    "batch_id": batch_id,
                    "rows": row_count,
                    "sha256": digest,
                }
            batch_id = connection.execute(
                """
                INSERT INTO aircraft_registry_import_batch (
                    source_code, snapshot_date, downloaded_at, source_file_name,
                    source_sha256, source_bytes, row_count, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    source_code,
                    snapshot_date,
                    downloaded_at,
                    source_path.name,
                    digest,
                    source_path.stat().st_size,
                    len(record_list),
                    Jsonb({"attribution": attribution, **(extra_metadata or {})}),
                ),
            ).fetchone()[0]
            _copy_rows(
                connection,
                "aircraft_registry_record",
                REGISTRY_RECORD_COLUMNS,
                (_record_row(batch_id, item) for item in record_list),
            )
            connection.execute(
                "REFRESH MATERIALIZED VIEW aircraft_registry_resolved_cache"
            )
            connection.execute("ANALYZE aircraft_registry_resolved_cache")
            semantic_refresh_available = connection.execute(
                """
                SELECT
                    to_regprocedure(
                        'heligent_refresh_nl_activity_cache_for_registry_batch(bigint)'
                    )
                        IS NOT NULL,
                    to_regprocedure('heligent_refresh_nl_operator_cache()') IS NOT NULL
                """
            ).fetchone()
            if semantic_refresh_available and semantic_refresh_available[0]:
                connection.execute(
                    "SELECT heligent_refresh_nl_activity_cache_for_registry_batch(%s)",
                    (batch_id,),
                )
            if semantic_refresh_available and semantic_refresh_available[1]:
                connection.execute("SELECT heligent_refresh_nl_operator_cache()")
        return {
            "status": "IMPORTED",
            "batch_id": batch_id,
            "rows": len(record_list),
            "sha256": digest,
        }

    def audit_registry(self, source_code: str, *, limit: int = 50) -> dict[str, Any]:
        with self.store.connect() as connection:
            connection.row_factory = dict_row
            summary = connection.execute(
                """
                SELECT
                    count(*) FILTER (WHERE identity_source_code = %s)
                        AS matched_aircraft_days,
                    count(DISTINCT address) FILTER (
                        WHERE identity_source_code = %s
                    ) AS matched_aircraft,
                    count(*) FILTER (
                        WHERE identity_source_code = %s
                          AND identity_status = 'REGISTRATION_CONFLICT'
                    ) AS registration_conflict_days,
                    count(*) FILTER (
                        WHERE identity_source_code = %s
                          AND identity_status = 'CATEGORY_CONFLICT'
                    ) AS category_conflict_days,
                    count(*) FILTER (
                        WHERE identity_source_code = %s
                          AND identity_status = 'REGISTRATION_AND_CATEGORY_CONFLICT'
                    ) AS registration_and_category_conflict_days,
                    count(DISTINCT address) FILTER (
                        WHERE identity_source_code = %s
                          AND identity_status IN (
                            'REGISTRATION_CONFLICT', 'CATEGORY_CONFLICT',
                            'REGISTRATION_AND_CATEGORY_CONFLICT'
                        )
                    ) AS conflicted_aircraft
                FROM aircraft_day_identity
                """,
                (source_code, source_code, source_code, source_code, source_code, source_code),
            ).fetchone()
            conflicts = connection.execute(
                """
                SELECT
                    address,
                    max(source_registration) AS source_registration,
                    max(registration) AS official_registration,
                    max(source_type_code) AS source_type_code,
                    max(source_type_description) AS source_type_description,
                    max(type_description) AS official_description,
                    max(resolved_category::text) AS official_category,
                    max(identity_status) AS identity_status,
                    min(utc_date) AS first_conflict_date,
                    max(utc_date) AS last_conflict_date,
                    count(*) AS affected_days,
                    sum(active_time_seconds) / 3600.0 AS active_hours
                FROM aircraft_identity_conflict
                WHERE identity_source_code = %s
                GROUP BY address
                ORDER BY active_hours DESC, address
                LIMIT %s
                """,
                (source_code, limit),
            ).fetchall()
        return {"summary": dict(summary), "conflicts": [dict(row) for row in conflicts]}

    def audit_canada(self, *, limit: int = 50) -> dict[str, Any]:
        return self.audit_registry(CANADA_SOURCE_CODE, limit=limit)

    def audit_faa(self, *, limit: int = 50) -> dict[str, Any]:
        return self.audit_registry(FAA_SOURCE_CODE, limit=limit)

    def audit_casa(self, *, limit: int = 50) -> dict[str, Any]:
        return self.audit_registry(CASA_SOURCE_CODE, limit=limit)


def _database_url(value: str | None) -> str:
    result = value or os.getenv("DATABASE_URL")
    if not result:
        raise AircraftRegistryError(
            "A PostgreSQL URL is required via --database-url or DATABASE_URL"
        )
    return result


def _default_canada_path(snapshot_date: date) -> Path:
    return (
        PROJECT_ROOT
        / "data"
        / "reference"
        / "transport-canada"
        / f"ccar-{snapshot_date.isoformat()}.zip"
    )


def _default_faa_path(snapshot_date: date) -> Path:
    return (
        PROJECT_ROOT
        / "data"
        / "reference"
        / "faa"
        / f"faa-aircraft-registry-{snapshot_date.isoformat()}.zip"
    )


def _default_casa_path(snapshot_date: date) -> Path:
    return (
        PROJECT_ROOT
        / "data"
        / "reference"
        / "casa"
        / f"casa-aircraft-register-{snapshot_date.isoformat()}.zip"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import and audit authoritative national aircraft registries"
    )
    parser.add_argument("--database-url", help="PostgreSQL connection URL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    schema = subparsers.add_parser("apply-schema", help="Apply the registry schema")
    schema.add_argument("--schema", type=Path, default=PHASE11_MIGRATION)

    validate = subparsers.add_parser(
        "validate-canada", help="Validate a Transport Canada CCAR ZIP"
    )
    validate.add_argument("--file", required=True, type=Path)

    validate_faa = subparsers.add_parser(
        "validate-faa", help="Validate an FAA Releasable Aircraft Database ZIP"
    )
    validate_faa.add_argument("--file", required=True, type=Path)

    validate_casa = subparsers.add_parser(
        "validate-casa", help="Validate a CASA Australian aircraft register ZIP"
    )
    validate_casa.add_argument("--file", required=True, type=Path)

    sync = subparsers.add_parser(
        "sync-canada", help="Download or import the current Transport Canada CCAR"
    )
    sync.add_argument("--file", type=Path, help="Use an existing official CCAR ZIP")
    sync.add_argument("--output", type=Path, help="Path for a newly downloaded ZIP")
    sync.add_argument("--snapshot-date", type=date.fromisoformat)
    sync.add_argument(
        "--dry-run", action="store_true", help="Download and validate without changing PostgreSQL"
    )

    sync_faa = subparsers.add_parser(
        "sync-faa", help="Download or import the current FAA aircraft registry"
    )
    sync_faa.add_argument("--file", type=Path, help="Use an existing official FAA ZIP")
    sync_faa.add_argument("--output", type=Path, help="Path for a newly downloaded ZIP")
    sync_faa.add_argument("--snapshot-date", type=date.fromisoformat)
    sync_faa.add_argument(
        "--dry-run", action="store_true", help="Download and validate without changing PostgreSQL"
    )

    sync_casa = subparsers.add_parser(
        "sync-casa", help="Download or import the current CASA aircraft register"
    )
    sync_casa.add_argument("--file", type=Path, help="Use an existing official CASA ZIP")
    sync_casa.add_argument("--output", type=Path, help="Path for a newly downloaded ZIP")
    sync_casa.add_argument("--snapshot-date", type=date.fromisoformat)
    sync_casa.add_argument(
        "--dry-run", action="store_true", help="Download and validate without changing PostgreSQL"
    )

    audit = subparsers.add_parser(
        "audit-canada", help="Report current Canada-to-ADSB identity conflicts"
    )
    audit.add_argument("--limit", type=int, default=50)
    audit_faa = subparsers.add_parser(
        "audit-faa", help="Report current FAA-to-ADSB identity conflicts"
    )
    audit_faa.add_argument("--limit", type=int, default=50)
    audit_casa = subparsers.add_parser(
        "audit-casa", help="Report current CASA-to-ADSB identity conflicts"
    )
    audit_casa.add_argument("--limit", type=int, default=50)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "apply-schema":
            store = AircraftRegistryStore(_database_url(args.database_url))
            store.apply_migration(args.schema)
            print(json.dumps({"status": "SCHEMA_APPLIED", "schema": str(args.schema)}))
            return
        if args.command == "validate-canada":
            records = load_canada_zip(args.file)
            print(
                json.dumps(
                    {
                        "status": "VALID",
                        "source": CANADA_SOURCE_CODE,
                        "rows": len(records),
                        "file": str(args.file),
                        "sha256": _sha256(args.file),
                    },
                    indent=2,
                )
            )
            return
        if args.command == "validate-faa":
            records = load_faa_zip(args.file)
            print(
                json.dumps(
                    {
                        "status": "VALID",
                        "source": FAA_SOURCE_CODE,
                        "usable_rows": len(records),
                        "file": str(args.file),
                        "sha256": _sha256(args.file),
                    },
                    indent=2,
                )
            )
            return
        if args.command == "validate-casa":
            records = load_casa_zip(args.file)
            print(
                json.dumps(
                    {
                        "status": "VALID",
                        "source": CASA_SOURCE_CODE,
                        "rows": len(records),
                        "registered_operators": len(
                            {record.registered_operator for record in records}
                        ),
                        "file": str(args.file),
                        "sha256": _sha256(args.file),
                    },
                    indent=2,
                )
            )
            return
        if args.command == "sync-canada":
            snapshot_date = args.snapshot_date or datetime.now(UTC).date()
            if args.file and args.output:
                raise AircraftRegistryError("Use --file or --output, not both")
            source_path = args.file or args.output or _default_canada_path(snapshot_date)
            if args.file is None:
                download_canada_zip(source_path)
            records = load_canada_zip(source_path)
            validation = {
                "status": "VALID" if args.dry_run else "READY",
                "source": CANADA_SOURCE_CODE,
                "snapshot_date": snapshot_date,
                "rows": len(records),
                "file": str(source_path),
                "sha256": _sha256(source_path),
                "attribution": CANADA_ATTRIBUTION,
            }
            if args.dry_run:
                print(json.dumps(validation, indent=2, default=str))
                return
            store = AircraftRegistryStore(_database_url(args.database_url))
            store.apply_migration()
            imported = store.import_canada(
                records,
                snapshot_date=snapshot_date,
                downloaded_at=datetime.now(UTC),
                source_path=source_path,
            )
            imported["audit"] = store.audit_canada()
            print(json.dumps(imported, indent=2, default=str))
            return
        if args.command == "sync-faa":
            snapshot_date = args.snapshot_date or datetime.now(UTC).date()
            if args.file and args.output:
                raise AircraftRegistryError("Use --file or --output, not both")
            source_path = args.file or args.output or _default_faa_path(snapshot_date)
            if args.file is None:
                download_faa_zip(source_path)
            records = load_faa_zip(source_path)
            validation = {
                "status": "VALID" if args.dry_run else "READY",
                "source": FAA_SOURCE_CODE,
                "snapshot_date": snapshot_date,
                "usable_rows": len(records),
                "file": str(source_path),
                "sha256": _sha256(source_path),
                "attribution": FAA_ATTRIBUTION,
            }
            if args.dry_run:
                print(json.dumps(validation, indent=2, default=str))
                return
            store = AircraftRegistryStore(_database_url(args.database_url))
            store.apply_migration()
            imported = store.import_faa(
                records,
                snapshot_date=snapshot_date,
                downloaded_at=datetime.now(UTC),
                source_path=source_path,
            )
            imported["audit"] = store.audit_faa()
            print(json.dumps(imported, indent=2, default=str))
            return
        if args.command == "sync-casa":
            snapshot_date = args.snapshot_date or datetime.now(UTC).date()
            if args.file and args.output:
                raise AircraftRegistryError("Use --file or --output, not both")
            source_path = args.file or args.output or _default_casa_path(snapshot_date)
            if args.file is None:
                download_casa_zip(source_path)
            records = load_casa_zip(source_path)
            validation = {
                "status": "VALID" if args.dry_run else "READY",
                "source": CASA_SOURCE_CODE,
                "snapshot_date": snapshot_date,
                "rows": len(records),
                "registered_operators": len(
                    {record.registered_operator for record in records}
                ),
                "file": str(source_path),
                "sha256": _sha256(source_path),
                "attribution": CASA_ATTRIBUTION,
            }
            if args.dry_run:
                print(json.dumps(validation, indent=2, default=str))
                return
            store = AircraftRegistryStore(_database_url(args.database_url))
            store.apply_migration()
            imported = store.import_casa(
                records,
                snapshot_date=snapshot_date,
                downloaded_at=datetime.now(UTC),
                source_path=source_path,
            )
            imported["audit"] = store.audit_casa()
            print(json.dumps(imported, indent=2, default=str))
            return
        if args.command in {"audit-canada", "audit-faa", "audit-casa"}:
            if not 1 <= args.limit <= 500:
                raise AircraftRegistryError("--limit must be between 1 and 500")
            store = AircraftRegistryStore(_database_url(args.database_url))
            audits = {
                "audit-canada": store.audit_canada,
                "audit-faa": store.audit_faa,
                "audit-casa": store.audit_casa,
            }
            audit_result = audits[args.command](limit=args.limit)
            print(json.dumps(audit_result, indent=2, default=str))
            return
        raise AssertionError(f"Unhandled command {args.command}")
    except (AircraftRegistryError, requests.RequestException, psycopg.Error) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
