from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests

from .adsblol import DayReleases, Release, ReleaseAsset, USER_AGENT


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArchiveApiError(RuntimeError):
    """The configured raw-archive API returned an invalid or unavailable day."""


class ArchiveApiClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 60.0,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Archive API URL must be an absolute HTTP(S) URL")
        if not token.strip():
            raise ValueError("Archive API token is required")
        self.base_url = normalized
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {token.strip()}",
                "User-Agent": USER_AGENT,
            }
        )

    @property
    def download_headers(self) -> dict[str, str]:
        return {"Authorization": self.session.headers["Authorization"]}

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "ArchiveApiClient":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def discover_day(self, utc_date: date) -> DayReleases:
        url = f"{self.base_url}/v1/days/{utc_date.isoformat()}"
        response = self.session.get(url, timeout=self.timeout)
        if response.status_code == 404:
            raise ArchiveApiError(
                f"The Raspberry Pi archive does not have a verified copy of {utc_date}"
            )
        response.raise_for_status()
        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            raise ArchiveApiError("Archive API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ArchiveApiError("Archive API response must be a JSON object")
        if payload.get("utc_date") != utc_date.isoformat():
            raise ArchiveApiError("Archive API returned the wrong UTC date")

        tag = str(payload.get("release_tag", ""))
        if not tag or Path(tag).name != tag:
            raise ArchiveApiError("Archive API returned an unsafe release tag")
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list) or not raw_assets:
            raise ArchiveApiError("Archive API release has no assets")
        assets: list[ReleaseAsset] = []
        seen_names: set[str] = set()
        for item in raw_assets:
            if not isinstance(item, dict):
                raise ArchiveApiError("Archive API asset metadata is invalid")
            name = str(item.get("name", ""))
            if not name or Path(name).name != name or name in seen_names:
                raise ArchiveApiError("Archive API returned an unsafe or duplicate asset")
            seen_names.add(name)
            try:
                size = int(item["size"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ArchiveApiError(f"Archive API asset {name} has an invalid size") from exc
            digest = str(item.get("sha256", "")).lower()
            if size < 0 or not SHA256_RE.fullmatch(digest):
                raise ArchiveApiError(f"Archive API asset {name} failed integrity validation")
            expected_path = (
                f"/v1/releases/{quote(tag, safe='')}/assets/{quote(name, safe='')}"
            )
            if item.get("download_path") != expected_path:
                raise ArchiveApiError(f"Archive API asset {name} has an invalid download path")
            download_url = urljoin(f"{self.base_url}/", expected_path.lstrip("/"))
            if urlparse(download_url).netloc != urlparse(self.base_url).netloc:
                raise ArchiveApiError("Archive API download escaped the configured host")
            assets.append(
                ReleaseAsset(
                    name=name,
                    size=size,
                    download_url=download_url,
                    digest=f"sha256:{digest}",
                )
            )

        release = Release(
            tag=tag,
            html_url=str(payload.get("release_url", "")),
            published_at=str(payload.get("published_at", "")),
            assets=tuple(assets),
        )
        return DayReleases(
            utc_date=utc_date,
            repository=str(payload.get("repository", "adsblol/unknown")),
            preferred=release,
            variants=(release,),
        )
