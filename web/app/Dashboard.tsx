"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Analytics } from "./Analytics";
import { AskData } from "./AskData";
import { CustomerLocations } from "./CustomerLocations";
import { OperatorFleet } from "./OperatorFleet";
import { EuropeHelicopters } from "./EuropeHelicopters";
import { MaintenancePulse } from "./MaintenancePulse";
import { FlightMap } from "./FlightMap";
import { Tools } from "./Tools";
import { CapabilityMappings } from "./CapabilityMappings";

type DatasetStatus =
  | "NOT_DOWNLOADED"
  | "QUEUED"
  | "DOWNLOADING"
  | "DOWNLOADED"
  | "PROCESSING"
  | "PROCESSED"
  | "FAILED_DOWNLOAD"
  | "FAILED_PROCESSING";

type DayRecord = {
  utc_date: string;
  status: DatasetStatus;
  source_release_tag?: string | null;
  raw_bytes?: number | null;
  source_aircraft_count?: number | null;
  source_record_count?: number | null;
  derived_record_count?: number | null;
  airport_presence_record_count?: number | null;
  flight_segment_record_count?: number | null;
  airport_visit_record_count?: number | null;
  derivation_version?: string | null;
  download_duration_ms?: number | null;
  processing_duration_ms?: number | null;
  derived_bytes_estimate?: number | null;
  raw_deleted_at?: string | null;
  raw_deleted_bytes?: number | null;
  error_stage?: string | null;
  error_message?: string | null;
  queue_id?: number | null;
  queue_status?: string | null;
  requested_action?: string | null;
  raw_source?: "DIRECT" | "PI" | null;
  active_stage?: string | null;
  status_message?: string | null;
  heartbeat_at?: string | null;
};

type QueueRecord = {
  id: number;
  utc_date: string;
  requested_action: string;
  raw_source: "DIRECT" | "PI";
  status: string;
  attempts: number;
  requested_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error_message?: string | null;
};

type Overview = {
  month: string;
  first_date: string;
  last_date: string;
  days: DayRecord[];
  summary: {
    processed_days: number;
    active_days: number;
    failed_days: number;
    latest_processed_date?: string | null;
    aircraft_day_rows: number;
  };
  current_queue_item?: (QueueRecord & {
    dataset_status?: DatasetStatus | null;
    active_stage?: string | null;
    status_message?: string | null;
    heartbeat_at?: string | null;
  }) | null;
  recent_queue: QueueRecord[];
  latest_ingestion?: DayRecord | null;
  ingestion_sources: Array<{
    code: "DIRECT" | "PI";
    label: string;
    available: boolean;
  }>;
};

type DateDetail = {
  utc_date: string;
  dataset: (DayRecord & Record<string, unknown>) | null;
  jobs: Array<Record<string, unknown>>;
  queue_history: QueueRecord[];
};

const monthFormatter = new Intl.DateTimeFormat("en-GB", {
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});
const fullDateFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});
const numberFormatter = new Intl.NumberFormat("en-GB");

function isoToday(): string {
  return new Date().toISOString().slice(0, 10);
}

function isoYesterday(): string {
  const value = new Date();
  value.setUTCDate(value.getUTCDate() - 1);
  return value.toISOString().slice(0, 10);
}

function apiBase(): string {
  if (typeof window !== "undefined" && window.location.port === "3000") {
    return "http://127.0.0.1:5080";
  }
  return "";
}

function formatBytes(value?: number | null): string {
  if (value == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1000 && unit < units.length - 1) {
    amount /= 1000;
    unit += 1;
  }
  return `${amount.toFixed(unit >= 2 ? 1 : 0)} ${units[unit]}`;
}

function formatDuration(value?: number | null): string {
  if (value == null) return "—";
  const totalSeconds = Math.round(value / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  return fullDateFormatter.format(new Date(`${value.slice(0, 10)}T12:00:00Z`));
}

function formatTimestamp(value?: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function statusLabel(status?: string | null): string {
  const labels: Record<string, string> = {
    NOT_DOWNLOADED: "Not downloaded",
    QUEUED: "Queued",
    DOWNLOADING: "Downloading",
    DOWNLOADED: "Download verified",
    PROCESSING: "Processing",
    PROCESSED: "Processed",
    FAILED_DOWNLOAD: "Download failed",
    FAILED_PROCESSING: "Processing failed",
    RUNNING: "Running",
    SUCCEEDED: "Complete",
    FAILED: "Failed",
    CANCELLED: "Cancelled",
  };
  return labels[status ?? ""] ?? status?.replaceAll("_", " ") ?? "Not downloaded";
}

function statusTone(status?: string | null): string {
  if (status === "PROCESSED" || status === "SUCCEEDED") return "success";
  if (status?.startsWith("FAILED") || status === "FAILED") return "danger";
  if (["QUEUED", "DOWNLOADING", "DOWNLOADED", "PROCESSING", "RUNNING"].includes(status ?? "")) return "active";
  return "quiet";
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase()}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "HeligentAdmin",
      ...(options?.headers ?? {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error ?? `Request failed (${response.status})`);
  }
  return payload as T;
}

function monthShift(month: string, delta: number): string {
  const [year, monthNumber] = month.split("-").map(Number);
  const value = new Date(Date.UTC(year, monthNumber - 1 + delta, 1));
  return value.toISOString().slice(0, 7);
}

export function Dashboard() {
  const [yesterday] = useState(isoYesterday);
  const [mappingCapability, setMappingCapability] = useState<number | null>(null);
  const [surface, setSurface] = useState<"ask" | "analytics" | "helicopters" | "locations" | "operators" | "data" | "pulse" | "tracks" | "mappings" | "tools">(
    () => typeof window !== "undefined"
      ? window.location.hash === "#customers"
        ? "locations"
        : window.location.hash === "#helicopters"
          ? "helicopters"
        : window.location.hash === "#operators"
          ? "operators"
        : (window.location.hash === "#tools" || window.location.hash.startsWith("#tools/"))
          ? "tools"
        : window.location.hash === "#capability-mappings"
          ? "mappings"
        : window.location.hash === "#tracks"
          ? "tracks"
        : window.location.hash === "#pulse"
          ? "pulse"
          : "ask"
      : "ask",
  );
  const [month, setMonth] = useState(yesterday.slice(0, 7));
  useEffect(() => {
    const openPulse = () => { if (window.location.hash === '#pulse') setSurface('pulse'); };
    window.addEventListener('hashchange', openPulse);
    return () => window.removeEventListener('hashchange', openPulse);
  }, []);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>(yesterday);
  const [detail, setDetail] = useState<DateDetail | null>(null);
  const [quickDate, setQuickDate] = useState(yesterday);
  const [rangeFrom, setRangeFrom] = useState(`${yesterday.slice(0, 8)}01`);
  const [rangeTo, setRangeTo] = useState(yesterday);
  const [keepRaw, setKeepRaw] = useState(false);
  const [reprocessRange, setReprocessRange] = useState(false);
  const [rawSource, setRawSource] = useState<"DIRECT" | "PI">("DIRECT");
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadOverview = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const result = await api<Overview>(`/api/overview?month=${month}`);
      setOverview(result);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to load ingestion status");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [month]);

  const loadDetail = useCallback(async (value: string) => {
    try {
      const result = await api<DateDetail>(`/api/datasets/${value}`);
      setDetail(result);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to load date detail");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadOverview(), 0);
    return () => window.clearTimeout(timer);
  }, [loadOverview]);
  useEffect(() => {
    const timer = window.setTimeout(() => void loadDetail(selectedDate), 0);
    return () => window.clearTimeout(timer);
  }, [selectedDate, loadDetail]);
  useEffect(() => {
    const active = Boolean(overview?.current_queue_item?.id);
    const timer = window.setInterval(() => {
      void loadOverview(true);
      if (selectedDate) void loadDetail(selectedDate);
    }, active ? 5000 : 30000);
    return () => window.clearInterval(timer);
  }, [overview?.current_queue_item?.id, selectedDate, loadOverview, loadDetail]);

  const mutate = async (action: () => Promise<unknown>, success: string) => {
    setActing(true);
    setNotice(null);
    try {
      await action();
      setNotice(success);
      await loadOverview(true);
      if (selectedDate) await loadDetail(selectedDate);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The action could not be completed");
    } finally {
      setActing(false);
    }
  };

  const queueOne = (event: FormEvent) => {
    event.preventDefault();
    setSelectedDate(quickDate);
    void mutate(
      () => api("/api/queue/date", {
        method: "POST",
        body: JSON.stringify({ date: quickDate, keep_raw: keepRaw, raw_source: rawSource }),
      }),
      `${formatDate(quickDate)} added to the queue.`,
    );
  };

  const queueRange = (event: FormEvent) => {
    event.preventDefault();
    void mutate(
      () => api("/api/queue/range", {
        method: "POST",
        body: JSON.stringify({ from_date: rangeFrom, to_date: rangeTo, reprocess: reprocessRange, keep_raw: keepRaw, raw_source: rawSource }),
      }),
      reprocessRange
        ? "Processed and missing dates in the selected range were queued for rebuilding."
        : "Missing dates in the selected range were queued.",
    );
  };

  const daysByDate = useMemo(
    () => new Map((overview?.days ?? []).map((day) => [day.utc_date, day])),
    [overview?.days],
  );
  const calendarDays = useMemo(() => {
    const [year, monthNumber] = month.split("-").map(Number);
    const count = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
    const firstWeekday = (new Date(Date.UTC(year, monthNumber - 1, 1)).getUTCDay() + 6) % 7;
    return [
      ...Array.from({ length: firstWeekday }, () => null),
      ...Array.from({ length: count }, (_, index) => {
        const value = `${month}-${String(index + 1).padStart(2, "0")}`;
        return { value, day: daysByDate.get(value) };
      }),
    ];
  }, [month, daysByDate]);

  const selected = detail?.dataset;
  const current = overview?.current_queue_item;
  const latest = overview?.latest_ingestion;
  const compressionRatio = latest?.raw_bytes && latest?.derived_bytes_estimate
    ? latest.raw_bytes / latest.derived_bytes_estimate
    : null;

  return (
    <main className="app-shell">
      <header className="topbar" id="top">
        <a className="brand" href="#top" aria-label="Heligent coverage control home">
          <span className="brand-mark">H</span>
          <span><strong>HELIGENT</strong><small>ADS-B HUB INTELLIGENCE</small></span>
        </a>
        <nav aria-label="Primary navigation">
          <button type="button" className={surface === "ask" ? "nav-active" : ""} onClick={() => setSurface("ask")}>Ask data</button>
          <button type="button" className={surface === "analytics" ? "nav-active" : ""} onClick={() => setSurface("analytics")}>Explorer</button>
          <button type="button" className={surface === "helicopters" ? "nav-active" : ""} onClick={() => setSurface("helicopters")}>Europe helis</button>
          <button type="button" className={surface === "locations" ? "nav-active" : ""} onClick={() => setSurface("locations")}>Customers</button>
          <button type="button" className={surface === "operators" ? "nav-active" : ""} onClick={() => setSurface("operators")}>Operators</button>
          <button type="button" className={surface === "pulse" ? "nav-active" : ""} onClick={() => setSurface("pulse")}>Maintenance Pulse</button>
          <button type="button" className={surface === "tracks" ? "nav-active" : ""} onClick={() => setSurface("tracks")}>Stops map</button>
          <button type="button" className={["tools","mappings"].includes(surface) ? "nav-active" : ""} onClick={() => setSurface("tools")}>Tools</button>
          <button type="button" className={surface === "data" ? "nav-active" : ""} onClick={() => setSurface("data")}>Data control</button>
        </nav>
        <div className="system-state"><span className={`state-light ${current ? "state-working" : ""}`} />{current ? "Pipeline active" : "Pipeline ready"}</div>
      </header>

      {surface === "ask" && <AskData onOpenData={() => setSurface("data")} />}
      {surface === "analytics" && <Analytics onOpenData={() => setSurface("data")} />}
      {surface === "helicopters" && <EuropeHelicopters onOpenData={() => setSurface("data")} />}
      {surface === "locations" && <CustomerLocations />}
      {surface === "operators" && <OperatorFleet />}
      {surface === "pulse" && <MaintenancePulse />}
      {surface === "tracks" && <FlightMap onReviewCapability={id => { setMappingCapability(id); setSurface("mappings"); }} />}
      {surface === "tools" && <Tools initialLba={typeof window !== "undefined" && window.location.hash === "#tools/lba"} onMappings={() => {setMappingCapability(null); setSurface("mappings");}} />}
      {surface === "mappings" && <CapabilityMappings key={mappingCapability ?? "search"} initialCapabilityId={mappingCapability} />}
      <div className="management-surface" hidden={surface !== "data"}>
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Historical coverage control</p>
          <h1>Build the local traffic picture, one reliable day at a time.</h1>
          <p className="hero-summary">Queue historical UTC dates, follow the live ingestion state, and verify that compact tail and airport metrics are safely committed before raw data disappears.</p>
        </div>
        <div className={`current-operation tone-${statusTone(current?.dataset_status ?? current?.status)}`}>
          <div className="operation-heading"><span className="operation-kicker">Current operation</span><span className="live-chip">{current ? "LIVE" : "CLEAR"}</span></div>
          {current ? (
            <>
              <div className="operation-date">{formatDate(current.utc_date)}</div>
              <div className="operation-stage">{statusLabel(current.active_stage ?? current.dataset_status ?? current.status)}</div>
              <div className="operation-message">{current.status_message ?? `${statusLabel(current.status)} · attempt ${current.attempts}`}</div>
              <div className="pulse-track"><span /></div>
              <div className="operation-meta"><span>Sequential worker · {current.raw_source === "PI" ? "Pi API" : "Direct"}</span><span>Updated {formatTimestamp(current.heartbeat_at ?? current.requested_at)}</span></div>
            </>
          ) : (
            <>
              <div className="operation-date">Queue clear</div>
              <div className="operation-message">No download or processing job is running. The next queued date will start automatically.</div>
              <div className="ready-line"><span /> Ready for work</div>
            </>
          )}
        </div>
      </section>

      {error && <div className="alert alert-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}
      {notice && <div className="alert alert-success" role="status"><span>{notice}</span><button type="button" onClick={() => setNotice(null)} aria-label="Dismiss notice">×</button></div>}

      <section className="metric-strip" aria-label="Pipeline summary">
        <article><span className="metric-label">Processed through</span><strong>{formatDate(overview?.summary.latest_processed_date)}</strong><small>Latest committed UTC day</small></article>
        <article><span className="metric-label">Available days</span><strong>{numberFormatter.format(overview?.summary.processed_days ?? 0)}</strong><small>{numberFormatter.format(overview?.summary.aircraft_day_rows ?? 0)} aircraft-day rows</small></article>
        <article><span className="metric-label">Attention needed</span><strong>{numberFormatter.format(overview?.summary.failed_days ?? 0)}</strong><small>{overview?.summary.active_days ?? 0} day(s) queued or active</small></article>
        <article><span className="metric-label">Last reduction</span><strong>{compressionRatio ? `${compressionRatio.toFixed(1)}×` : "—"}</strong><small>{formatBytes(latest?.raw_bytes)} raw → {formatBytes(latest?.derived_bytes_estimate)}</small></article>
      </section>

      <section className="control-grid" id="data">
        <article className="panel queue-panel">
          <div className="panel-heading"><div><p className="section-index">01 · Queue work</p><h2>Add historical coverage</h2></div><span className="sequence-badge">1 at a time</span></div>
          <label className="source-select">
            Raw download source
            <select value={rawSource} onChange={(event) => setRawSource(event.target.value as "DIRECT" | "PI")}>
              {(overview?.ingestion_sources ?? [
                { code: "DIRECT" as const, label: "Direct from ADSB.lol", available: true },
                { code: "PI" as const, label: "Raspberry Pi archive API", available: false },
              ]).map((source) => <option key={source.code} value={source.code} disabled={!source.available}>{source.label}{source.available ? "" : " (not configured)"}</option>)}
            </select>
            <small>{rawSource === "PI" ? "Pull the verified release from the private Pi archive." : "Use the existing ADSB.lol release download path."}</small>
          </label>
          <form className="date-form" onSubmit={queueOne}>
            <label>Single UTC date<input type="date" value={quickDate} max={yesterday} onChange={(event) => setQuickDate(event.target.value)} required /></label>
            <button className="button button-primary" type="submit" disabled={acting}>Queue date <span aria-hidden="true">→</span></button>
          </form>
          <div className="form-divider"><span>or queue missing dates</span></div>
          <form className="range-form" onSubmit={queueRange}>
            <label>From<input type="date" value={rangeFrom} max={yesterday} onChange={(event) => setRangeFrom(event.target.value)} required /></label>
            <label>To<input type="date" value={rangeTo} max={yesterday} onChange={(event) => setRangeTo(event.target.value)} required /></label>
            <button className="button button-secondary" type="submit" disabled={acting}>{reprocessRange ? "Reprocess range" : "Queue missing days"}</button>
          </form>
          <label className="check-row">
            <input type="checkbox" checked={reprocessRange} onChange={(event) => setReprocessRange(event.target.checked)} />
            <span><strong>Reprocess completed dates in this range</strong><small>Re-downloads each day and atomically rebuilds its derived flights and visits.</small></span>
          </label>
          <label className="check-row">
            <input type="checkbox" checked={keepRaw} onChange={(event) => setKeepRaw(event.target.checked)} />
            <span><strong>Keep raw files after processing</strong><small>Developer option. Off is safer for disk use.</small></span>
          </label>
          <p className="guardrail-copy">Already processed dates are skipped unless range reprocessing is selected. Ranges are capped at 31 days and run sequentially.</p>
        </article>

        <article className="panel last-run-panel">
          <div className="panel-heading"><div><p className="section-index">02 · Last ingestion</p><h2>{latest ? formatDate(latest.utc_date) : "No completed day"}</h2></div><span className={`status-pill tone-${statusTone(latest?.status)}`}>{statusLabel(latest?.status)}</span></div>
          {latest ? (
            <div className="run-ledger">
              <div><span>Aircraft</span><strong>{numberFormatter.format(latest.source_aircraft_count ?? 0)}</strong></div>
              <div><span>Observations</span><strong>{numberFormatter.format(latest.source_record_count ?? 0)}</strong></div>
              <div><span>Hub activity links</span><strong>{numberFormatter.format(latest.airport_presence_record_count ?? 0)}</strong></div>
              <div><span>Flight episodes</span><strong>{numberFormatter.format(latest.flight_segment_record_count ?? 0)}</strong></div>
              <div><span>Airport visits</span><strong>{numberFormatter.format(latest.airport_visit_record_count ?? 0)}</strong></div>
              <div><span>Download</span><strong>{formatDuration(latest.download_duration_ms)}</strong></div>
              <div><span>Processing</span><strong>{formatDuration(latest.processing_duration_ms)}</strong></div>
              <div><span>Raw cleanup</span><strong>{latest.raw_deleted_at ? "Complete" : "Retained"}</strong></div>
            </div>
          ) : <p className="empty-copy">Queue a historical date to create the first local coverage record.</p>}
          <div className="integrity-note"><span className="integrity-mark">✓</span><p><strong>Commit before cleanup</strong><br />Raw archives are deleted only after the complete date transaction succeeds.</p></div>
        </article>
      </section>

      <section className="coverage-layout" id="coverage">
        <article className="panel calendar-panel">
          <div className="calendar-heading">
            <div><p className="section-index">03 · Local availability</p><h2>{monthFormatter.format(new Date(`${month}-15T12:00:00Z`))}</h2></div>
            <div className="month-nav" aria-label="Calendar month navigation"><button type="button" onClick={() => setMonth(monthShift(month, -1))} aria-label="Previous month">←</button><button type="button" onClick={() => setMonth(yesterday.slice(0, 7))}>Today</button><button type="button" onClick={() => setMonth(monthShift(month, 1))} aria-label="Next month" disabled={month >= isoToday().slice(0, 7)}>→</button></div>
          </div>
          <div className="legend" aria-label="Calendar status legend"><span><i className="dot-success" /> Processed</span><span><i className="dot-active" /> Active / queued</span><span><i className="dot-danger" /> Failed</span><span><i className="dot-quiet" /> Not local</span></div>
          <div className="calendar-grid calendar-weekdays" aria-hidden="true">{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => <span key={day}>{day}</span>)}</div>
          <div className={`calendar-grid calendar-days ${loading ? "calendar-loading" : ""}`}>
            {calendarDays.map((entry, index) => {
              if (!entry) return <span className="calendar-blank" key={`blank-${index}`} />;
              const future = entry.value >= isoToday();
              const status = future ? "FUTURE" : entry.day?.status ?? "NOT_DOWNLOADED";
              const tone = future ? "future" : statusTone(status);
              return (
                <button type="button" key={entry.value} className={`calendar-day tone-${tone} ${selectedDate === entry.value ? "selected" : ""}`} onClick={() => setSelectedDate(entry.value)} disabled={future} aria-label={`${formatDate(entry.value)}: ${future ? "future date" : statusLabel(status)}`}>
                  <span className="day-number">{Number(entry.value.slice(-2))}</span><span className="day-status">{future ? "—" : entry.day ? statusLabel(entry.day.status) : "Not local"}</span>{entry.day?.raw_deleted_at && <span className="cleanup-mark" title="Raw files deleted">✓</span>}
                </button>
              );
            })}
          </div>
        </article>

        <aside className="panel detail-panel" aria-live="polite">
          <p className="section-index">Selected UTC date</p><h2>{formatDate(selectedDate)}</h2><span className={`status-pill tone-${statusTone(selected?.status)}`}>{statusLabel(selected?.status)}</span>
          {selected ? (
            <>
              <dl className="detail-list">
                <div><dt>Release</dt><dd>{String(selected.source_release_tag ?? "Pending discovery")}</dd></div><div><dt>Parser</dt><dd>{String(selected.derivation_version ?? "Legacy daily summaries")}</dd></div><div><dt>Raw source</dt><dd>{formatBytes(selected.raw_bytes as number | null)}</dd></div><div><dt>Aircraft</dt><dd>{numberFormatter.format(Number(selected.source_aircraft_count ?? 0))}</dd></div><div><dt>Observations</dt><dd>{numberFormatter.format(Number(selected.source_record_count ?? 0))}</dd></div><div><dt>Flight episodes</dt><dd>{numberFormatter.format(Number(selected.flight_segment_record_count ?? 0))}</dd></div><div><dt>Airport visits</dt><dd>{numberFormatter.format(Number(selected.airport_visit_record_count ?? 0))}</dd></div><div><dt>Derived storage</dt><dd>{formatBytes(selected.derived_bytes_estimate as number | null)}</dd></div><div><dt>Raw deleted</dt><dd>{selected.raw_deleted_at ? formatTimestamp(selected.raw_deleted_at as string) : "No"}</dd></div>
              </dl>
              {selected.error_message && <div className="date-error"><strong>{String(selected.error_stage ?? "Ingestion")} error</strong>{String(selected.error_message)}</div>}
              <div className="detail-actions">
                {String(selected.status).startsWith("FAILED") && <button className="button button-primary" type="button" disabled={acting} onClick={() => void mutate(() => api(`/api/datasets/${selectedDate}/retry`, { method: "POST", body: JSON.stringify({ keep_raw: keepRaw, raw_source: rawSource }) }), `${formatDate(selectedDate)} queued for retry.`)}>Retry safely</button>}
                {selected.status === "PROCESSED" && <button className="button button-danger" type="button" disabled={acting} onClick={() => { if (window.confirm(`Re-download and replace ${formatDate(selectedDate)}? Existing rows remain available until the replacement commits.`)) { void mutate(() => api(`/api/datasets/${selectedDate}/reprocess`, { method: "POST", body: JSON.stringify({ keep_raw: keepRaw, raw_source: rawSource }) }), `${formatDate(selectedDate)} queued for reprocessing.`); } }}>Reprocess date</button>}
                {selected.queue_id && selected.queue_status === "QUEUED" && <button className="button button-ghost" type="button" disabled={acting} onClick={() => void mutate(() => api(`/api/queue/${selected.queue_id}/cancel`, { method: "POST", body: "{}" }), "Queued work cancelled.")}>Cancel queued work</button>}
              </div>
            </>
          ) : (
            <><p className="empty-copy">This day is not available locally yet.</p><button className="button button-primary detail-queue" type="button" disabled={acting || selectedDate >= isoToday()} onClick={() => void mutate(() => api("/api/queue/date", { method: "POST", body: JSON.stringify({ date: selectedDate, keep_raw: keepRaw, raw_source: rawSource }) }), `${formatDate(selectedDate)} added to the queue.`)}>Queue this day →</button></>
          )}
        </aside>
      </section>

      <section className="panel activity-panel">
        <div className="panel-heading"><div><p className="section-index">04 · Queue history</p><h2>Recent ingestion requests</h2></div><button className="text-button" type="button" onClick={() => void loadOverview()} disabled={loading}>Refresh status</button></div>
        {overview?.recent_queue.length ? (
          <div className="activity-table-wrap"><table><thead><tr><th>Date</th><th>Action</th><th>Source</th><th>Status</th><th>Attempt</th><th>Requested</th><th>Finished</th></tr></thead><tbody>{overview.recent_queue.map((item) => <tr key={item.id}><td><button className="table-date" type="button" onClick={() => setSelectedDate(item.utc_date)}>{formatDate(item.utc_date)}</button></td><td>{item.requested_action === "REPROCESS" ? "Reprocess" : "Ingest"}</td><td>{item.raw_source === "PI" ? "Pi API" : "Direct"}</td><td><span className={`mini-status tone-${statusTone(item.status)}`}>{statusLabel(item.status)}</span></td><td>{item.attempts}</td><td>{formatTimestamp(item.requested_at)}</td><td>{formatTimestamp(item.finished_at)}</td></tr>)}</tbody></table></div>
        ) : <p className="empty-copy">No UI ingestion requests have been made yet.</p>}
      </section>
      </div>

      <footer><span>HELIGENT · PHASE 6</span><p>Natural-language questions · inferred hub movements · aircraft activity · PostgreSQL source of truth</p></footer>
    </main>
  );
}
