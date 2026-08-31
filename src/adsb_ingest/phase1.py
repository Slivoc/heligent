from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .adsblol import (
    TRACE_PATH_RE,
    DayReleases,
    GitHubClient,
    SplitAssetReader,
    TarEntry,
    iter_tar_entries,
)


TRACE_COLUMNS = (
    "seconds_after_timestamp",
    "latitude",
    "longitude",
    "barometric_altitude_ft_or_ground",
    "ground_speed_knots",
    "track_degrees",
    "flags",
    "vertical_rate_fpm",
    "aircraft_detail_object",
    "position_source_type",
    "geometric_altitude_ft",
    "geometric_vertical_rate_fpm",
    "indicated_airspeed_knots",
    "roll_degrees",
)


def _counter_percentages(counter: Counter[str], denominator: int) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "count": count,
            "percent": round(count * 100 / denominator, 2) if denominator else 0.0,
        }
        for key, count in counter.most_common()
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _bootstrap_estimates(
    footprints: list[int],
    records: list[int],
    uncompressed: list[int],
    trace_region_bytes: int,
    repetitions: int = 2_000,
) -> dict[str, dict[str, int]]:
    rng = random.Random(20260820)
    count = len(footprints)
    aircraft_estimates: list[float] = []
    record_estimates: list[float] = []
    json_estimates: list[float] = []
    for _ in range(repetitions):
        indexes = [rng.randrange(count) for _ in range(count)]
        mean_footprint = statistics.fmean(footprints[index] for index in indexes)
        estimated_aircraft = trace_region_bytes / mean_footprint
        aircraft_estimates.append(estimated_aircraft)
        record_estimates.append(
            estimated_aircraft * statistics.fmean(records[index] for index in indexes)
        )
        json_estimates.append(
            estimated_aircraft * statistics.fmean(uncompressed[index] for index in indexes)
        )

    def interval(values: list[float]) -> dict[str, int]:
        return {
            "estimate": round(statistics.fmean(values)),
            "sample_95pct_low": round(_percentile(values, 0.025)),
            "sample_95pct_high": round(_percentile(values, 0.975)),
        }

    return {
        "aircraft_files": interval(aircraft_estimates),
        "trace_records": interval(record_estimates),
        "uncompressed_json_bytes": interval(json_estimates),
    }


def _release_dict(release: Any) -> dict[str, Any]:
    return {
        "tag": release.tag,
        "instance": release.instance,
        "html_url": release.html_url,
        "published_at": release.published_at,
        "total_bytes": release.total_bytes,
        "assets": [
            {
                "name": asset.name,
                "size": asset.size,
                "download_url": asset.download_url,
                "digest": asset.digest,
            }
            for asset in release.assets
        ],
    }


def inspect_day(discovery: DayReleases, sample_aircraft: int) -> dict[str, Any]:
    if sample_aircraft < 10:
        raise ValueError("At least 10 aircraft are required for a useful inspection")

    preferred = discovery.preferred
    reader = SplitAssetReader(preferred.assets)
    pre_trace_entries: list[TarEntry] = []
    samples: list[TarEntry] = []
    trace_region_offset: int | None = None

    for entry in iter_tar_entries(reader):
        normalized_name = entry.name.removeprefix("./")
        if normalized_name == "traces/":
            trace_region_offset = entry.header_offset
        match = TRACE_PATH_RE.search(normalized_name)
        if match:
            samples.append(entry)
            if len(samples) >= sample_aircraft:
                break
        elif not samples:
            pre_trace_entries.append(entry)

    if trace_region_offset is None or len(samples) < sample_aircraft:
        raise RuntimeError(
            f"Archive yielded only {len(samples)} aircraft traces; requested {sample_aircraft}"
        )

    span_start = samples[0].data_offset
    span_end = samples[-1].data_offset + samples[-1].size
    sample_span = reader.read_at(span_start, span_end - span_start)

    top_level_presence: Counter[str] = Counter()
    top_level_types: dict[str, Counter[str]] = defaultdict(Counter)
    trace_version_values: Counter[str] = Counter()
    trace_row_lengths: Counter[str] = Counter()
    trace_column_presence: Counter[str] = Counter()
    extra_key_presence: Counter[str] = Counter()
    source_types: Counter[str] = Counter()
    registrations = 0
    type_codes = 0
    non_icao_addresses = 0
    callsigns: set[str] = set()
    ground_rows = 0
    extra_object_rows = 0
    total_records = 0
    sample_payload_bytes = 0
    sample_uncompressed_bytes = 0
    footprints: list[int] = []
    record_counts: list[int] = []
    uncompressed_sizes: list[int] = []
    first_observed: float | None = None
    last_observed: float | None = None
    parse_errors: list[dict[str, str]] = []

    for entry in samples:
        relative_start = entry.data_offset - span_start
        compressed = sample_span[relative_start : relative_start + entry.size]
        sample_payload_bytes += len(compressed)
        try:
            uncompressed = gzip.decompress(compressed)
            payload = json.loads(uncompressed)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            parse_errors.append({"name": entry.name, "error": str(exc)})
            continue

        if not isinstance(payload, dict) or not isinstance(payload.get("trace"), list):
            parse_errors.append({"name": entry.name, "error": "Unexpected JSON shape"})
            continue

        sample_uncompressed_bytes += len(uncompressed)
        uncompressed_sizes.append(len(uncompressed))
        footprints.append(entry.footprint)
        top_level_presence.update(payload.keys())
        for key, value in payload.items():
            top_level_types[key][type(value).__name__] += 1
        if "version" in payload:
            trace_version_values[str(payload["version"])] += 1
        if payload.get("r"):
            registrations += 1
        if payload.get("t"):
            type_codes += 1
        if str(payload.get("icao", "")).startswith("~"):
            non_icao_addresses += 1

        base_timestamp = payload.get("timestamp")
        records = payload["trace"]
        record_counts.append(len(records))
        total_records += len(records)
        for row in records:
            if not isinstance(row, list):
                continue
            trace_row_lengths[str(len(row))] += 1
            for index, name in enumerate(TRACE_COLUMNS):
                if index < len(row) and row[index] is not None:
                    trace_column_presence[name] += 1
            if len(row) > 3 and row[3] == "ground":
                ground_rows += 1
            if len(row) > 9 and row[9] is not None:
                source_types[str(row[9])] += 1
            if len(row) > 8 and isinstance(row[8], dict):
                extra_object_rows += 1
                extra_key_presence.update(row[8].keys())
                flight = row[8].get("flight")
                if isinstance(flight, str) and flight.strip():
                    callsigns.add(flight.strip())
            if isinstance(base_timestamp, (int, float)) and row and isinstance(row[0], (int, float)):
                observed = float(base_timestamp) + float(row[0])
                first_observed = observed if first_observed is None else min(first_observed, observed)
                last_observed = observed if last_observed is None else max(last_observed, observed)

    parsed_files = len(footprints)
    if parsed_files < 10:
        raise RuntimeError("Too few sampled aircraft files parsed successfully")

    trace_region_bytes = reader.size - trace_region_offset
    estimates = _bootstrap_estimates(
        footprints,
        record_counts,
        uncompressed_sizes,
        trace_region_bytes,
    )
    aircraft_estimate = estimates["aircraft_files"]["estimate"]
    record_estimate = estimates["trace_records"]["estimate"]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "utc_date": discovery.utc_date.isoformat(),
        "source": {
            "name": "ADSB.lol historical archive",
            "repository": discovery.repository,
            "selection_policy": "ADSB.lol PREFERRED_RELEASES.txt; variants are not merged",
            "preferred_release": _release_dict(preferred),
            "same_day_variants": [_release_dict(item) for item in discovery.variants],
        },
        "archive_structure": {
            "format": "uncompressed tar split across release assets",
            "aircraft_payload_format": "gzip-compressed JSON stored in .json-named tar entries",
            "trace_region_offset": trace_region_offset,
            "trace_region_bytes_approximate": trace_region_bytes,
            "pre_trace_entries": [
                {"name": item.name, "size": item.size, "typeflag": item.typeflag}
                for item in pre_trace_entries
            ],
            "sample_first_entry": samples[0].name,
            "sample_last_entry": samples[-1].name,
        },
        "sampling": {
            "method": (
                "Sparse tar-header range reads followed by one contiguous byte range for "
                "the first N aircraft in archive order across the leading hash shards"
            ),
            "requested_aircraft_files": sample_aircraft,
            "parsed_aircraft_files": parsed_files,
            "parse_errors": parse_errors,
            "compressed_payload_bytes": sample_payload_bytes,
            "tar_footprint_bytes": sum(footprints),
            "uncompressed_json_bytes": sample_uncompressed_bytes,
            "http_bytes_transferred": reader.bytes_transferred,
            "http_range_requests": reader.range_requests,
            "caveat": (
                "Confidence ranges measure sampling variation only. Consecutive files across "
                "the leading hash shards are expected to be broadly representative but are "
                "not a formal uniform random sample of the complete archive."
            ),
        },
        "observed_shape": {
            "top_level_field_presence": _counter_percentages(
                top_level_presence, parsed_files
            ),
            "top_level_field_types": {
                key: dict(counter.most_common()) for key, counter in sorted(top_level_types.items())
            },
            "trace_format_versions": dict(trace_version_values.most_common()),
            "trace_columns": list(TRACE_COLUMNS),
            "trace_row_lengths": dict(trace_row_lengths.most_common()),
            "trace_column_non_null_presence": _counter_percentages(
                trace_column_presence, total_records
            ),
            "extra_object_rows": extra_object_rows,
            "extra_field_presence_among_extra_rows": _counter_percentages(
                extra_key_presence, extra_object_rows
            ),
            "position_source_types": _counter_percentages(source_types, total_records),
            "sample_aircraft_with_registration": registrations,
            "sample_aircraft_with_type_code": type_codes,
            "sample_non_icao_addresses": non_icao_addresses,
            "distinct_sample_callsigns": len(callsigns),
            "sample_ground_records": ground_rows,
            "sample_trace_records": total_records,
            "records_per_aircraft": {
                "mean": round(statistics.fmean(record_counts), 2),
                "median": round(statistics.median(record_counts), 2),
                "p95": round(_percentile([float(item) for item in record_counts], 0.95), 2),
                "max": max(record_counts),
            },
            "sample_observation_window_utc": {
                "first": datetime.fromtimestamp(first_observed, UTC).isoformat()
                if first_observed is not None
                else None,
                "last": datetime.fromtimestamp(last_observed, UTC).isoformat()
                if last_observed is not None
                else None,
            },
        },
        "full_day_estimates": {
            **estimates,
            "method": (
                "Bootstrap extrapolation from sampled tar footprint and trace count across "
                "the approximate trace-region byte length"
            ),
        },
        "storage_planning": {
            "source_archive_bytes": reader.size,
            "estimated_uncompressed_trace_json_bytes": estimates[
                "uncompressed_json_bytes"
            ]["estimate"],
            "estimated_aircraft_day_table_bytes_at_400_bytes_per_row": aircraft_estimate
            * 400,
            "estimated_full_position_table_bytes_at_80_bytes_per_row": record_estimate * 80,
            "assumptions": [
                "400 bytes per aircraft_day row includes a planning allowance for indexes",
                "80 bytes per normalized position row is a planning estimate, not a measured PostgreSQL size",
                "PostgreSQL storage must be measured after Phase 2 COPY/upsert and VACUUM ANALYZE",
            ],
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect a bounded sample of an ADSB.lol historical day"
    )
    parser.add_argument("--date", required=True, type=date.fromisoformat, dest="utc_date")
    parser.add_argument("--sample-aircraft", type=int, default=500)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    discovery = GitHubClient().discover_day(args.utc_date)
    report = inspect_day(discovery, args.sample_aircraft)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        estimates = report["full_day_estimates"]
        print(f"Wrote {args.output}")
        print(f"Preferred release: {report['source']['preferred_release']['tag']}")
        print(
            "Estimated full day: "
            f"{estimates['aircraft_files']['estimate']:,} aircraft files, "
            f"{estimates['trace_records']['estimate']:,} trace records"
        )
        print(
            f"Transferred {report['sampling']['http_bytes_transferred']:,} of "
            f"{report['storage_planning']['source_archive_bytes']:,} archive bytes"
        )
    else:
        print(rendered)


if __name__ == "__main__":
    main()
