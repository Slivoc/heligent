from datetime import date
from unittest.mock import Mock, call

import pytest
import requests

from adsb_ingest import ingest
from adsb_ingest.adsblol import DayReleases, Release
from adsb_ingest.archive_api_client import ArchiveApiError, ArchiveDayUnavailable
from adsb_ingest.downloader import DownloadResult


@pytest.fixture
def ingestion(tmp_path, monkeypatch):
    args = ingest.build_parser().parse_args([
        "--database-url", "test", "ingest-day", "--date", "2026-11-16",
        "--raw-dir", str(tmp_path), "--airport-csv", str(tmp_path / "airports.csv"),
        "--keep-raw", "--archive-api-url", "https://archive.example.com",
        "--archive-api-token", "pi-token",
    ])
    store = Mock()
    store.get_dataset.return_value = None
    store.upsert_dataset.return_value = 42
    store.upsert_airports.return_value = 1
    store.load_summaries.return_value = {}
    monkeypatch.setattr(ingest, "PostgresStore", Mock(return_value=store))
    monkeypatch.setattr(ingest, "load_airport_catalog", Mock())
    monkeypatch.setattr(ingest, "AirportIndex", Mock())
    monkeypatch.setattr("adsb_ingest.analytics.AnalyticsStore", Mock())

    def release(source):
        asset_release = Release(source, "", "", ())
        return DayReleases(date(2026, 11, 16), "adsblol/fixture", asset_release, (asset_release,))

    pi, direct, downloaders = (Mock() for _ in range(3))
    pi.discover_day.return_value = release("pi")
    pi.download_headers = {"Authorization": "Bearer pi-token"}
    direct.discover_day.return_value = release("direct")
    pi_client = Mock()
    pi_client.return_value.__enter__ = Mock(return_value=pi)
    pi_client.return_value.__exit__ = Mock(return_value=False)
    public_client = Mock()
    public_client.return_value.__enter__ = Mock(return_value=direct)
    public_client.return_value.__exit__ = Mock(return_value=False)
    downloaders.return_value.__enter__ = Mock()
    downloaders.return_value.__enter__.return_value.download_release.return_value = DownloadResult((), 0, 0)
    downloaders.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(ingest, "ArchiveApiClient", pi_client)
    monkeypatch.setattr(ingest, "GitHubClient", public_client)
    monkeypatch.setattr(ingest, "AssetDownloader", downloaders)
    return args, store, pi, direct, downloaders


@pytest.mark.parametrize("failure", [requests.ConnectTimeout(), ArchiveDayUnavailable()])
def test_pi_discovery_falls_back_when_offline_or_missing_day(ingestion, failure):
    args, store, pi, direct, downloaders = ingestion
    pi.discover_day.side_effect = failure

    result = ingest.run_ingestion(args)

    assert result["raw_source"] == "DIRECT"
    pi.discover_day.assert_called_once_with(args.utc_date)
    direct.discover_day.assert_called_once_with(args.utc_date)
    downloaders.assert_called_once_with()
    store.upsert_dataset.assert_called_once_with(direct.discover_day.return_value)
    download = downloaders.return_value.__enter__.return_value.download_release
    assert download.call_args.args[1] == args.raw_dir / "adsblol-fallback"


@pytest.mark.parametrize("source", [None, "direct"])
def test_preferred_pi_and_explicit_direct_use_the_expected_source(ingestion, source):
    args, _, pi, direct, downloaders = ingestion
    args.raw_source = source

    result = ingest.run_ingestion(args)

    if source is None:
        assert result["raw_source"] == "PI"
        pi.discover_day.assert_called_once_with(args.utc_date)
        direct.discover_day.assert_not_called()
        downloaders.assert_called_once_with(timeout=ingest.PI_DOWNLOAD_TIMEOUT, headers=pi.download_headers)
    else:
        assert result["raw_source"] == "DIRECT"
        pi.discover_day.assert_not_called()
        direct.discover_day.assert_called_once_with(args.utc_date)
        downloaders.assert_called_once_with()


def test_unavailable_pi_transfer_switches_source_inside_the_same_download_job(ingestion):
    args, store, pi, direct, downloaders = ingestion
    transfer = downloaders.return_value.__enter__.return_value.download_release
    transfer.side_effect = [requests.ConnectionError(), DownloadResult((), 0, 0)]

    result = ingest.run_ingestion(args)

    assert result["raw_source"] == "DIRECT"
    assert store.upsert_dataset.call_args_list == [
        call(pi.discover_day.return_value), call(direct.discover_day.return_value),
    ]
    assert downloaders.call_args_list == [
        call(timeout=ingest.PI_DOWNLOAD_TIMEOUT, headers=pi.download_headers), call(),
    ]
    assert [attempt.args[1] for attempt in transfer.call_args_list] == [
        args.raw_dir, args.raw_dir / "adsblol-fallback",
    ]
    store.reconcile_interrupted.assert_called_once_with(42)
    store.finish_download.assert_called_once()
    store.fail_job.assert_not_called()


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


@pytest.mark.parametrize("failure", [
    ArchiveApiError("bad manifest"), requests.exceptions.SSLError("certificate failed"), http_error(401),
])
def test_pi_discovery_integrity_and_tls_errors_remain_visible(ingestion, failure):
    args, store, pi, direct, _ = ingestion
    pi.discover_day.side_effect = failure

    with pytest.raises(type(failure)):
        ingest.run_ingestion(args)

    direct.discover_day.assert_not_called()
    store.upsert_dataset.assert_not_called()


def test_unconfigured_pi_defaults_to_direct(ingestion, monkeypatch):
    args, _, pi, direct, downloaders = ingestion
    args.archive_api_url = args.archive_api_token = None
    monkeypatch.delenv("ADSB_ARCHIVE_API_URL", raising=False)
    monkeypatch.delenv("ADSB_ARCHIVE_API_TOKEN", raising=False)

    assert ingest.run_ingestion(args)["raw_source"] == "DIRECT"
    pi.discover_day.assert_not_called()
    direct.discover_day.assert_called_once_with(args.utc_date)
    transfer = downloaders.return_value.__enter__.return_value.download_release
    assert transfer.call_args.args[1] == args.raw_dir


def test_failed_fallback_download_marks_job_failed(ingestion):
    args, store, _, _, downloaders = ingestion
    transfer = downloaders.return_value.__enter__.return_value.download_release
    transfer.side_effect = [requests.ConnectionError(), IOError("SHA-256 verification failed")]

    with pytest.raises(IOError, match="SHA-256 verification failed"):
        ingest.run_ingestion(args)

    store.fail_job.assert_called_once()
    store.finish_download.assert_not_called()
    store.load_summaries.assert_not_called()
