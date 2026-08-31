#!/usr/bin/env python3
"""Convert EASA's Part-145 scope workbook to Heligent's import CSV.

This is intentionally a dependency-free, source-specific converter.  It reads
the XLSX package directly, validates the expected EASA export layout, maps the
source fields to ``schema/company-import-template.csv``, and writes the result
atomically.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import posixpath
import re
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    PROJECT_ROOT
    / "data"
    / "reference"
    / "Scope of approval of EASA Part-145 organisations.xlsx"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "reference" / "easa-part145-companies-import.csv"
DEFAULT_TEMPLATE = PROJECT_ROOT / "schema" / "company-import-template.csv"

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

SOURCE_HEADERS = (
    "EASA approval number",
    "Full Organisation Name",
    "Country",
    "Rating",
    "Part-145 Limitations",
    "Aircraft Line",
    "Aircraft Base",
    "NDT Method",
)

REQUIRED_IMPORT_FIELDS = frozenset(
    {
        "company_name",
        "company_key",
        "source_company_id",
        "legal_name",
        "is_operator",
        "is_mro",
        "company_country_code",
        "company_active",
        "company_notes",
        "authority_code",
        "approval_type",
        "approval_number",
        "source_approval_id",
        "approval_status",
        "approval_source_url",
        "approval_last_verified_at",
        "approval_notes",
        "capability_kind",
        "source_capability_id",
        "rating_class",
        "rating_code",
        "limitation",
        "capability_base_maintenance",
        "capability_line_maintenance",
        "capability_active",
    }
)

COUNTRY_CODES = {
    "ALGERIA": "DZ",
    "AUSTRALIA": "AU",
    "AZERBAIJAN": "AZ",
    "BAHRAIN": "BH",
    "CHILE": "CL",
    "CHINA": "CN",
    "COSTA RICA": "CR",
    "CUBA": "CU",
    "DOMINICAN REPUBLIC": "DO",
    "EGYPT": "EG",
    "EL SALVADOR": "SV",
    "ETHIOPIA": "ET",
    "HONG KONG": "HK",
    "INDIA": "IN",
    "INDONESIA": "ID",
    "ISRAEL": "IL",
    "JAPAN": "JP",
    "JORDAN": "JO",
    "KAZAKHSTAN": "KZ",
    "KENYA": "KE",
    "KUWAIT": "KW",
    "MADAGASCAR": "MG",
    "MALAYSIA": "MY",
    "MALDIVES": "MV",
    "MAURITIUS": "MU",
    "MEXICO": "MX",
    "MONGOLIA": "MN",
    "MOROCCO": "MA",
    "MYANMAR BURMA": "MM",
    "NEW ZEALAND": "NZ",
    "OMAN": "OM",
    "PAPUA NEW GUINEA": "PG",
    "PERU": "PE",
    "PHILIPPINES": "PH",
    "QATAR": "QA",
    "SAUDI ARABIA": "SA",
    "SENEGAL": "SN",
    "SERBIA": "RS",
    "SEYCHELLES": "SC",
    "SINGAPORE": "SG",
    "SOUTH AFRICA": "ZA",
    "SOUTH KOREA": "KR",
    "SRI LANKA": "LK",
    "TAIWAN": "TW",
    "THAILAND": "TH",
    "TUNISIA": "TN",
    "TURKIYE": "TR",
    # The supplied export contains a Unicode replacement character in Türkiye.
    "TRKIYE": "TR",
    "UNITED ARAB EMIRATES": "AE",
    "UNITED KINGDOM": "GB",
    "UZBEKISTAN": "UZ",
    "VIETNAM": "VN",
}

CAPABILITY_KINDS = {
    "A": "AIRCRAFT",
    "B": "ENGINE",
    "C": "COMPONENT",
    "D": "SPECIALIST",
}


class ConversionError(ValueError):
    """Raised when the workbook does not match the expected EASA export."""


@dataclass(frozen=True)
class SourceRow:
    worksheet_row: int
    approval_number: str
    organisation_name: str
    country: str
    rating: str
    limitation: str
    aircraft_line: str
    aircraft_base: str
    ndt_method: str

    @property
    def values(self) -> tuple[str, ...]:
        return (
            self.approval_number,
            self.organisation_name,
            self.country,
            self.rating,
            self.limitation,
            self.aircraft_line,
            self.aircraft_base,
            self.ndt_method,
        )


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").replace("\xa0", " ").split())


def _ascii_words(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).split())


def _company_key(name: str, country_code: str) -> str:
    normalized = _ascii_words(name).replace(" ", "-")[:112].strip("-._") or "company"
    result = f"{normalized}-{country_code.lower()}"
    if len(result) < 2:
        result = f"{result}-company"
    return result[:128]


def _country_code(country: str, worksheet_row: int) -> str:
    normalized = unicodedata.normalize("NFKD", country).encode("ascii", "ignore").decode()
    normalized = " ".join(re.sub(r"[^A-Z0-9]+", " ", normalized.upper()).split())
    try:
        return COUNTRY_CODES[normalized]
    except KeyError as exc:
        raise ConversionError(
            f"worksheet row {worksheet_row}: unknown country {country!r}; "
            "add an explicit ISO-3166 alpha-2 mapping before importing"
        ) from exc


def _yes_no(value: str, field: str, worksheet_row: int) -> str:
    normalized = value.casefold()
    if normalized in {"", "no"}:
        return "no"
    if normalized == "yes":
        return "yes"
    raise ConversionError(
        f"worksheet row {worksheet_row}: {field} must be Yes, No, or blank; got {value!r}"
    )


def _column_index(cell_reference: str) -> int:
    match = re.fullmatch(r"([A-Za-z]+)[1-9][0-9]*", cell_reference)
    if not match:
        raise ConversionError(f"invalid XLSX cell reference {cell_reference!r}")
    result = 0
    for char in match.group(1).upper():
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def _shared_strings(archive: zipfile.ZipFile) -> tuple[str, ...]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return ()
    return tuple(
        "".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t"))
        for item in root.findall(f"{{{MAIN_NS}}}si")
    )


def _worksheet_path(archive: zipfile.ZipFile, requested_sheet: str | None) -> tuple[str, str]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    sheets = workbook.findall(f".//{{{MAIN_NS}}}sheet")
    if not sheets:
        raise ConversionError("workbook contains no worksheets")

    if requested_sheet is None:
        if len(sheets) != 1:
            names = ", ".join(repr(sheet.attrib.get("name", "")) for sheet in sheets)
            raise ConversionError(f"workbook has multiple sheets ({names}); pass --sheet")
        selected = sheets[0]
    else:
        selected = next((sheet for sheet in sheets if sheet.attrib.get("name") == requested_sheet), None)
        if selected is None:
            names = ", ".join(repr(sheet.attrib.get("name", "")) for sheet in sheets)
            raise ConversionError(f"worksheet {requested_sheet!r} not found; available: {names}")

    relationship_id = selected.attrib.get(f"{{{REL_NS}}}id")
    if not relationship_id:
        raise ConversionError("selected worksheet has no workbook relationship")
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relationship = next(
        (
            item
            for item in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
            if item.attrib.get("Id") == relationship_id
        ),
        None,
    )
    if relationship is None or not relationship.attrib.get("Target"):
        raise ConversionError("selected worksheet relationship target is missing")

    target = relationship.attrib["Target"].replace("\\", "/")
    if target.startswith("/"):
        worksheet_path = target.lstrip("/")
    else:
        worksheet_path = posixpath.normpath(str(PurePosixPath("xl") / target))
    return selected.attrib.get("name", ""), worksheet_path


def _cell_text(cell: ElementTree.Element, shared_strings: tuple[str, ...]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))

    value_node = cell.find(f"{{{MAIN_NS}}}v")
    raw_value = "" if value_node is None else value_node.text or ""
    if cell_type == "s" and raw_value:
        try:
            return shared_strings[int(raw_value)]
        except (IndexError, ValueError) as exc:
            raise ConversionError(f"invalid shared-string index {raw_value!r}") from exc
    if cell_type == "b":
        return "TRUE" if raw_value == "1" else "FALSE"
    return raw_value


def _read_source_rows(path: Path, sheet: str | None) -> tuple[str, tuple[SourceRow, ...]]:
    if not path.is_file():
        raise ConversionError(f"input workbook not found: {path}")

    try:
        with zipfile.ZipFile(path) as archive:
            shared_strings = _shared_strings(archive)
            sheet_name, worksheet_path = _worksheet_path(archive, sheet)
            worksheet = ElementTree.fromstring(archive.read(worksheet_path))
    except (KeyError, ElementTree.ParseError, zipfile.BadZipFile) as exc:
        raise ConversionError(f"cannot read {path} as an XLSX workbook: {exc}") from exc

    rows: list[tuple[int, dict[int, str]]] = []
    for row in worksheet.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
        worksheet_row = int(row.attrib.get("r", "0"))
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            if cell.find(f"{{{MAIN_NS}}}f") is not None:
                raise ConversionError(
                    f"worksheet row {worksheet_row}: formulas are not accepted in the source export"
                )
            reference = cell.attrib.get("r")
            if not reference:
                raise ConversionError(f"worksheet row {worksheet_row}: cell has no reference")
            value = _clean_text(_cell_text(cell, shared_strings))
            if value:
                values[_column_index(reference)] = value
        rows.append((worksheet_row, values))

    header_position = next(
        (
            index
            for index, (_, values) in enumerate(rows)
            if tuple(values.get(column, "") for column in range(len(SOURCE_HEADERS)))
            == SOURCE_HEADERS
        ),
        None,
    )
    if header_position is None:
        raise ConversionError(
            "could not find the expected EASA header row: " + ", ".join(SOURCE_HEADERS)
        )

    source_rows: list[SourceRow] = []
    seen_approvals: dict[str, tuple[str, str]] = {}
    for worksheet_row, values in rows[header_position + 1 :]:
        unexpected = {column: value for column, value in values.items() if column >= 8 and value}
        if unexpected:
            raise ConversionError(
                f"worksheet row {worksheet_row}: unexpected populated columns after H"
            )
        fields = tuple(values.get(column, "") for column in range(8))
        if not any(fields):
            continue
        if any(not fields[index] for index in range(4)):
            missing = [SOURCE_HEADERS[index] for index in range(4) if not fields[index]]
            raise ConversionError(
                f"worksheet row {worksheet_row}: missing required value(s): {', '.join(missing)}"
            )
        if not re.fullmatch(
            r"EASA(?:\.[A-Z]{2})?\.145\.[A-Z0-9.-]+", fields[0], flags=re.IGNORECASE
        ):
            raise ConversionError(
                f"worksheet row {worksheet_row}: invalid EASA approval number {fields[0]!r}"
            )

        row = SourceRow(worksheet_row, *fields)
        identity = (row.organisation_name, row.country)
        previous = seen_approvals.setdefault(row.approval_number.upper(), identity)
        if previous != identity:
            raise ConversionError(
                f"worksheet row {worksheet_row}: approval {row.approval_number!r} has "
                "inconsistent organisation or country values"
            )
        source_rows.append(row)

    if not source_rows:
        raise ConversionError("workbook contains no source data rows")
    return sheet_name, tuple(source_rows)


def _import_fields(template: Path) -> tuple[str, ...]:
    if not template.is_file():
        raise ConversionError(f"canonical import template not found: {template}")
    with template.open("r", encoding="utf-8-sig", newline="") as handle:
        fields = tuple(next(csv.reader(handle), ()))
    if not fields:
        raise ConversionError(f"canonical import template has no header: {template}")
    missing = sorted(REQUIRED_IMPORT_FIELDS - set(fields))
    if missing:
        raise ConversionError(
            f"canonical import template is missing required field(s): {', '.join(missing)}"
        )
    return fields


def _capability_kind(rating: str, worksheet_row: int) -> str:
    try:
        return CAPABILITY_KINDS[rating[0].upper()]
    except (IndexError, KeyError) as exc:
        raise ConversionError(
            f"worksheet row {worksheet_row}: unsupported EASA rating {rating!r}"
        ) from exc


def _capability_id(row: SourceRow) -> str:
    # Keep the identifier stable if EASA later corrects the organisation name or
    # country spelling without changing the approval capability itself.
    payload = "\x1f".join(
        value.casefold()
        for value in (
            row.approval_number,
            row.rating,
            row.limitation,
            row.aircraft_line,
            row.aircraft_base,
            row.ndt_method,
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{row.approval_number.upper()}:{digest}"


def _import_row(
    row: SourceRow,
    import_fields: tuple[str, ...],
    *,
    source_name: str,
    sheet_name: str,
    approval_source_url: str,
    verified_at: str,
) -> dict[str, str]:
    country_code = _country_code(row.country, row.worksheet_row)
    limitation = row.limitation
    if row.ndt_method:
        limitation = f"{limitation}; NDT method: {row.ndt_method}" if limitation else f"NDT method: {row.ndt_method}"

    result = {field: "" for field in import_fields}
    result.update(
        {
            "company_name": row.organisation_name,
            "company_key": _company_key(row.organisation_name, country_code),
            "source_company_id": row.approval_number.upper(),
            "legal_name": row.organisation_name,
            "is_operator": "no",
            "is_mro": "yes",
            "company_country_code": country_code,
            "company_active": "yes",
            "company_notes": "EASA Part-145 approved organisation",
            "authority_code": "EASA",
            "approval_type": "PART_145",
            "approval_number": row.approval_number.upper(),
            "source_approval_id": row.approval_number.upper(),
            "approval_status": "VALID",
            "approval_source_url": approval_source_url,
            "approval_last_verified_at": verified_at,
            "approval_notes": f"Extracted from {source_name}; worksheet: {sheet_name}",
            "capability_kind": _capability_kind(row.rating, row.worksheet_row),
            "source_capability_id": _capability_id(row),
            "rating_class": row.rating.upper(),
            "rating_code": row.rating.upper(),
            "limitation": limitation,
            "capability_base_maintenance": _yes_no(
                row.aircraft_base, "Aircraft Base", row.worksheet_row
            ),
            "capability_line_maintenance": _yes_no(
                row.aircraft_line, "Aircraft Line", row.worksheet_row
            ),
            "capability_active": "yes",
        }
    )
    return result


def convert(
    input_path: Path,
    output_path: Path,
    template_path: Path,
    *,
    sheet: str | None,
    approval_source_url: str,
    verified_at: str,
    force: bool,
) -> tuple[int, int, int, int]:
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    template_path = template_path.resolve()
    if output_path.exists() and not force:
        raise ConversionError(f"output already exists: {output_path}; pass --force to replace it")
    if verified_at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", verified_at):
        raise ConversionError("--verified-at must be an ISO date in YYYY-MM-DD form")

    import_fields = _import_fields(template_path)
    sheet_name, source_rows = _read_source_rows(input_path, sheet)

    unique_rows: list[SourceRow] = []
    seen: set[tuple[str, ...]] = set()
    for row in source_rows:
        if row.values in seen:
            continue
        seen.add(row.values)
        unique_rows.append(row)

    output_rows = [
        _import_row(
            row,
            import_fields,
            source_name=input_path.name,
            sheet_name=sheet_name,
            approval_source_url=approval_source_url,
            verified_at=verified_at,
        )
        for row in unique_rows
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8-sig",
            newline="",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=import_fields, extrasaction="raise")
            writer.writeheader()
            writer.writerows(output_rows)
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return (
        len(source_rows),
        len(output_rows),
        len({row.approval_number.upper() for row in unique_rows}),
        len({row.organisation_name for row in unique_rows}),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE,
        help="canonical Heligent CSV template used for column order",
    )
    parser.add_argument("--sheet", help="worksheet name (required only for multi-sheet workbooks)")
    parser.add_argument(
        "--approval-source-url",
        default="",
        help="optional source URL to place on every approval row",
    )
    parser.add_argument(
        "--verified-at",
        default="",
        help="optional YYYY-MM-DD date to place in approval_last_verified_at",
    )
    parser.add_argument("--force", action="store_true", help="replace an existing output file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        source_count, output_count, approval_count, organisation_count = convert(
            args.input,
            args.output,
            args.template,
            sheet=args.sheet,
            approval_source_url=args.approval_source_url.strip(),
            verified_at=args.verified_at.strip(),
            force=args.force,
        )
    except (ConversionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote {output_count:,} import rows to {args.output.resolve()}")
    print(
        f"Source rows: {source_count:,}; exact duplicates removed: "
        f"{source_count - output_count:,}; approvals: {approval_count:,}; "
        f"organisation names: {organisation_count:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
