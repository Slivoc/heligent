from __future__ import annotations

import base64
import binascii
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator
from urllib.parse import quote

import requests


GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
USER_AGENT = "heligent-adsb-inspector/0.1"
PREFERRED_URL_RE = re.compile(
    r"/releases/download/(?P<tag>v\d{4}\.\d{2}\.\d{2}-planes-readsb-[^/]+)/"
)
PREFERRED_DATE_RE = re.compile(
    r"/releases/download/v(?P<date>\d{4}\.\d{2}\.\d{2})-planes-readsb-"
)
TRACE_PATH_RE = re.compile(r"(?:^|/)traces/[0-9a-f]{2}/trace_full_(?P<address>[^/]+)\.json$")


class DiscoveryError(RuntimeError):
    """Raised when ADSB.lol release metadata is absent or inconsistent."""


class ArchiveError(RuntimeError):
    """Raised when a split archive cannot be read as a tar stream."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    size: int
    download_url: str
    digest: str | None = None


@dataclass(frozen=True)
class Release:
    tag: str
    html_url: str
    published_at: str
    assets: tuple[ReleaseAsset, ...]

    @property
    def total_bytes(self) -> int:
        return sum(asset.size for asset in self.assets)

    @property
    def instance(self) -> str:
        marker = "-planes-readsb-"
        return self.tag.split(marker, 1)[1] if marker in self.tag else "unknown"


@dataclass(frozen=True)
class DayReleases:
    utc_date: date
    repository: str
    preferred: Release
    variants: tuple[Release, ...]


@dataclass(frozen=True)
class TarEntry:
    name: str
    size: int
    typeflag: str
    header_offset: int
    data_offset: int

    @property
    def next_header_offset(self) -> int:
        padded_size = ((self.size + 511) // 512) * 512
        return self.data_offset + padded_size

    @property
    def footprint(self) -> int:
        return self.next_header_offset - self.header_offset


class GitHubClient:
    def __init__(self, token: str | None = None, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        token = token or os.getenv("GITHUB_TOKEN")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _json(self, url: str) -> Any:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _text(self, url: str) -> str:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def get_release(self, repository: str, tag: str) -> Release:
        payload = self._json(
            f"{GITHUB_API}/repos/{repository}/releases/tags/{quote(tag, safe='')}"
        )
        assets = tuple(
            sorted(
                (
                    ReleaseAsset(
                        name=item["name"],
                        size=int(item["size"]),
                        download_url=item["browser_download_url"],
                        digest=item.get("digest"),
                    )
                    for item in payload.get("assets", [])
                ),
                key=lambda asset: asset.name,
            )
        )
        if not assets:
            raise DiscoveryError(f"Release {tag} has no downloadable assets")
        return Release(
            tag=payload["tag_name"],
            html_url=payload["html_url"],
            published_at=payload["published_at"],
            assets=assets,
        )

    def preferred_dates(self, year: int) -> tuple[date, ...]:
        """Return the UTC dates listed by ADSB.lol as preferred for a year."""
        repository = f"adsblol/globe_history_{year}"
        preferred_text = self._text(
            f"{GITHUB_RAW}/{repository}/main/PREFERRED_RELEASES.txt"
        )
        result: set[date] = set()
        for match in PREFERRED_DATE_RE.finditer(preferred_text):
            parsed = date.fromisoformat(match.group("date").replace(".", "-"))
            if parsed.year == year:
                result.add(parsed)
        return tuple(sorted(result))

    def latest_preferred_date(self, *, before: date | None = None) -> date:
        """Find the newest completed UTC day in the preferred-release indexes.

        ``before`` is exclusive. By default the current UTC day is excluded so
        a daily archiver never selects an in-progress day.
        """
        current_utc_date = datetime.now(timezone.utc).date()
        upper_bound = (before or current_utc_date) - timedelta(days=1)
        for year in (upper_bound.year, upper_bound.year - 1):
            try:
                candidates = [
                    item for item in self.preferred_dates(year) if item <= upper_bound
                ]
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    continue
                raise
            if candidates:
                return max(candidates)
        raise DiscoveryError(
            f"No preferred release found before {upper_bound + timedelta(days=1)}"
        )

    def discover_day(self, utc_date: date) -> DayReleases:
        repository = f"adsblol/globe_history_{utc_date.year}"
        preferred_text = self._text(
            f"{GITHUB_RAW}/{repository}/main/PREFERRED_RELEASES.txt"
        )
        date_token = utc_date.strftime("v%Y.%m.%d-planes-readsb-")
        preferred_lines = [line for line in preferred_text.splitlines() if date_token in line]
        if len(preferred_lines) != 1:
            raise DiscoveryError(
                f"Expected one preferred release for {utc_date}, found {len(preferred_lines)}"
            )
        match = PREFERRED_URL_RE.search(preferred_lines[0])
        if not match:
            raise DiscoveryError(f"Could not parse preferred release line for {utc_date}")
        preferred_tag = match.group("tag")

        refs = self._json(
            f"{GITHUB_API}/repos/{repository}/git/matching-refs/tags/{date_token}"
        )
        tags = sorted(item["ref"].removeprefix("refs/tags/") for item in refs)
        if preferred_tag not in tags:
            tags.append(preferred_tag)
            tags.sort()
        variants = tuple(self.get_release(repository, tag) for tag in tags)
        preferred = next(item for item in variants if item.tag == preferred_tag)
        return DayReleases(
            utc_date=utc_date,
            repository=repository,
            preferred=preferred,
            variants=variants,
        )


class SplitAssetReader:
    """Random-access reader over ordered split release assets.

    Only requested byte ranges are transferred. Redirect targets are resolved
    once per asset because GitHub's public release URLs add significant latency
    to every range request.
    """

    def __init__(
        self,
        assets: tuple[ReleaseAsset, ...],
        timeout: float = 60.0,
    ) -> None:
        if not assets:
            raise ValueError("At least one release asset is required")
        self.assets = assets
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._resolved_urls: list[str | None] = [None] * len(assets)
        self.bytes_transferred = 0
        self.range_requests = 0

    @property
    def size(self) -> int:
        return sum(asset.size for asset in self.assets)

    def _resolve(self, index: int) -> str:
        cached = self._resolved_urls[index]
        if cached:
            return cached
        response = self.session.get(
            self.assets[index].download_url,
            headers={"Range": "bytes=0-0"},
            stream=True,
            timeout=self.timeout,
        )
        response.raise_for_status()
        if response.status_code != 206:
            response.close()
            raise ArchiveError("Release asset server did not honor an HTTP range request")
        self._resolved_urls[index] = response.url
        self.bytes_transferred += 1
        self.range_requests += 1
        response.close()
        return response.url

    def _read_asset(self, index: int, offset: int, length: int) -> bytes:
        url = self._resolve(index)
        response = self.session.get(
            url,
            headers={"Range": f"bytes={offset}-{offset + length - 1}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        if response.status_code != 206:
            raise ArchiveError("Release asset server stopped honoring range requests")
        content = response.content
        if len(content) != length:
            raise ArchiveError(f"Expected {length} range bytes, received {len(content)}")
        self.bytes_transferred += len(content)
        self.range_requests += 1
        return content

    def read_at(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > self.size:
            raise ValueError("Requested range lies outside the split archive")
        if length == 0:
            return b""

        remaining = length
        cursor = offset
        chunks: list[bytes] = []
        asset_start = 0
        for index, asset in enumerate(self.assets):
            asset_end = asset_start + asset.size
            if cursor >= asset_end:
                asset_start = asset_end
                continue
            local_offset = max(0, cursor - asset_start)
            take = min(remaining, asset.size - local_offset)
            chunks.append(self._read_asset(index, local_offset, take))
            cursor += take
            remaining -= take
            if remaining == 0:
                break
            asset_start = asset_end
        if remaining:
            raise ArchiveError("Split archive ended before the requested range")
        return b"".join(chunks)


def _parse_tar_number(raw: bytes) -> int:
    if raw and raw[0] & 0x80:
        value = int.from_bytes(raw, "big", signed=True)
        return value & ((1 << (len(raw) * 8 - 1)) - 1)
    clean = raw.rstrip(b"\0 ").lstrip(b" ") or b"0"
    return int(clean, 8)


def parse_tar_header(block: bytes, offset: int) -> TarEntry | None:
    if len(block) != 512:
        raise ArchiveError("Tar header must be exactly 512 bytes")
    if block == b"\0" * 512:
        return None

    stored_checksum = _parse_tar_number(block[148:156])
    checksum_block = block[:148] + (b" " * 8) + block[156:]
    computed_checksum = sum(checksum_block)
    if stored_checksum != computed_checksum:
        raise ArchiveError(
            f"Invalid tar checksum at byte {offset}: {stored_checksum} != {computed_checksum}"
        )

    name = block[:100].split(b"\0", 1)[0].decode("utf-8", "replace")
    prefix = block[345:500].split(b"\0", 1)[0].decode("utf-8", "replace")
    if prefix:
        name = f"{prefix}/{name}"
    return TarEntry(
        name=name,
        size=_parse_tar_number(block[124:136]),
        typeflag=(block[156:157] or b"0").decode("ascii", "replace") or "0",
        header_offset=offset,
        data_offset=offset + 512,
    )


def iter_tar_entries(reader: SplitAssetReader) -> Iterator[TarEntry]:
    offset = 0
    while offset + 512 <= reader.size:
        entry = parse_tar_header(reader.read_at(offset, 512), offset)
        if entry is None:
            return
        yield entry
        if entry.next_header_offset <= offset:
            raise ArchiveError(f"Tar entry at {offset} did not advance the stream")
        offset = entry.next_header_offset


def decode_content_encoding(value: str) -> bytes:
    """Small test helper for fixture payloads stored as base64 strings."""
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid base64 fixture") from exc
