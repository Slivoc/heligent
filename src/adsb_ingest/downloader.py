from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import Callable

import requests

from .adsblol import Release, ReleaseAsset, USER_AGENT


LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[ReleaseAsset, int, int], None]


@dataclass(frozen=True)
class DownloadResult:
    paths: tuple[Path, ...]
    bytes_downloaded: int
    bytes_reused: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_digest(path: Path, asset: ReleaseAsset) -> bool:
    if path.stat().st_size != asset.size:
        return False
    if not asset.digest:
        return True
    algorithm, separator, expected = asset.digest.partition(":")
    if separator != ":" or algorithm.lower() != "sha256":
        raise ValueError(f"Unsupported release digest: {asset.digest}")
    return sha256_file(path) == expected.lower()


class AssetDownloader:
    def __init__(
        self,
        timeout: float = 120.0,
        chunk_bytes: int = 8 * 1024 * 1024,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.timeout = timeout
        self.chunk_bytes = chunk_bytes
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        if headers:
            self.session.headers.update(headers)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "AssetDownloader":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def download_release(
        self,
        release: Release,
        raw_root: Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        release_dir = raw_root / release.tag
        release_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        downloaded = 0
        reused = 0
        for asset in release.assets:
            if Path(asset.name).name != asset.name:
                raise ValueError(f"Unsafe release asset name: {asset.name}")
            path = release_dir / asset.name
            result = self.download_asset(asset, path, progress=progress)
            paths.append(path)
            downloaded += result[0]
            reused += result[1]
        return DownloadResult(tuple(paths), downloaded, reused)

    def download_asset(
        self,
        asset: ReleaseAsset,
        target: Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> tuple[int, int]:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and _valid_digest(target, asset):
            LOGGER.info("Reusing verified asset %s", target.name)
            if progress:
                progress(asset, asset.size, asset.size)
            return 0, asset.size

        part = target.with_name(target.name + ".part")
        if target.exists():
            target.unlink()
        offset = part.stat().st_size if part.exists() else 0
        if offset > asset.size:
            part.unlink()
            offset = 0
        elif offset == asset.size:
            if _valid_digest(part, asset):
                os.replace(part, target)
                if progress:
                    progress(asset, asset.size, asset.size)
                return 0, asset.size
            part.unlink()
            offset = 0

        headers = {"Range": f"bytes={offset}-"}
        LOGGER.info("Downloading %s from byte %d", asset.name, offset)
        with self.session.get(
            asset.download_url,
            headers=headers,
            stream=True,
            timeout=self.timeout,
        ) as response:
            response.raise_for_status()
            if offset and response.status_code != 206:
                LOGGER.warning("Server refused resume for %s; restarting", asset.name)
                offset = 0
                mode = "wb"
            else:
                mode = "ab" if offset else "wb"
            written = offset
            with part.open(mode) as handle:
                for chunk in response.iter_content(chunk_size=self.chunk_bytes):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if progress:
                        progress(asset, written, asset.size)
                handle.flush()
                os.fsync(handle.fileno())

        if part.stat().st_size != asset.size:
            raise IOError(
                f"Asset {asset.name} has {part.stat().st_size} bytes; expected {asset.size}"
            )
        if not _valid_digest(part, asset):
            raise IOError(f"SHA-256 verification failed for {asset.name}")
        os.replace(part, target)
        if progress:
            progress(asset, asset.size, asset.size)
        return asset.size - offset, offset
