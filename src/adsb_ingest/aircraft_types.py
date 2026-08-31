from __future__ import annotations


# User-facing trade names and common model spellings mapped to the ICAO Doc 8643
# designators stored in ADS-B metadata. Keep this deliberately conservative.
AIRCRAFT_TYPE_ALIASES = {
    "AS-350": "AS50",
    "AS350": "AS50",
    "AS350B2": "AS50",
    "AS350B3": "AS50",
    "AS350B3E": "AS50",
    "H-125": "AS50",
    "H125": "AS50",
    "EC-135": "EC35",
    "EC135": "EC35",
    "H-135": "EC35",
    "H135": "EC35",
    "EC-145": "EC45",
    "EC145": "EC45",
    "H-145": "EC45",
    "H145": "EC45",
    "EC-175": "EC75",
    "EC175": "EC75",
    "H-175": "EC75",
    "H175": "EC75",
    "Z-15": "EC75",
    "Z15": "EC75",
}


def resolve_aircraft_type_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper().replace(" ", "")
    if not normalized or normalized == "ALL":
        return None
    return AIRCRAFT_TYPE_ALIASES.get(normalized, normalized)
