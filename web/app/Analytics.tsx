"use client";

import {
  BarController,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
  type ChartOptions,
} from "chart.js";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Bar, Line } from "react-chartjs-2";

ChartJS.register(
  BarController,
  BarElement,
  CategoryScale,
  Filler,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
);

type Coverage = {
  requested_days: number;
  available_days: number;
  complete: boolean;
  available_dates: string[];
  missing_dates: string[];
  message: string;
};

type Totals = {
  unique_aircraft: number;
  aircraft_days: number;
  observations: number;
  active_hours: number;
  airborne_hours: number;
  ground_active_hours: number;
  known_type_codes: number;
  airport_linked_aircraft: number;
};

type DailyMetric = {
  utc_date: string;
  unique_aircraft: number;
  observations: number;
  active_hours: number;
  airborne_hours: number;
  ground_active_hours: number;
  airport_linked_aircraft: number;
};

type TypeMetric = {
  type_code: string;
  description?: string | null;
  category?: string;
  unique_aircraft: number;
  aircraft_days?: number;
  observations: number;
  active_hours: number;
  airborne_hours: number;
  ground_active_hours?: number;
};

type TailMetric = {
  address: string;
  registration?: string | null;
  operator?: string | null;
  operator_source_code?: string | null;
  type_code?: string | null;
  description?: string | null;
  active_days: number;
  observations: number;
  active_hours: number;
  airborne_hours: number;
  ground_active_hours: number;
  primary_airport?: string | null;
  primary_airport_label?: string | null;
};

type HubMetric = {
  airport_ident: string;
  iata_code?: string | null;
  airport_name: string;
  iso_country?: string | null;
  unique_aircraft: number;
  primary_aircraft: number;
  known_type_codes: number;
  ground_observations: number;
  ground_active_hours: number;
  arrival_candidates: number;
  departure_candidates: number;
  movement_candidates: number;
  endpoint_linked_aircraft: number;
  movement_linked_aircraft: number;
  primary_tail_active_hours: number;
  primary_tail_airborne_hours: number;
};

type AnalyticsSnapshot = {
  selection: {
    from_date?: string | null;
    to_date?: string | null;
    type_code?: string | null;
    helicopters_only: boolean;
    latest_available_date?: string | null;
  };
  coverage: Coverage;
  totals: Totals;
  daily_activity: DailyMetric[];
  types: TypeMetric[];
  type_options: Array<{ type_code: string; description?: string | null; unique_aircraft: number }>;
  tails: TailMetric[];
  hubs: HubMetric[];
  helicopters: {
    totals: Pick<Totals, "unique_aircraft" | "observations" | "active_hours" | "airborne_hours" | "known_type_codes">;
    types: TypeMetric[];
    classification_note: string;
  };
};

type AnalyticsProps = {
  onOpenData: () => void;
};

const numberFormatter = new Intl.NumberFormat("en-GB");
const compactFormatter = new Intl.NumberFormat("en-GB", {
  notation: "compact",
  maximumFractionDigits: 1,
});
const dayFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  timeZone: "UTC",
});

function apiBase(): string {
  if (typeof window !== "undefined" && window.location.port === "3000") {
    return "http://127.0.0.1:5080";
  }
  return "";
}

async function fetchSnapshot(query = ""): Promise<AnalyticsSnapshot> {
  const response = await fetch(`${apiBase()}/api/analytics/snapshot${query}`, {
    headers: { "X-Requested-With": "HeligentAdmin" },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error ?? `Analytics request failed (${response.status})`);
  return payload as AnalyticsSnapshot;
}

function shortDate(value: string): string {
  return dayFormatter.format(new Date(`${value}T12:00:00Z`));
}

function shiftDate(value: string, delta: number): string {
  const result = new Date(`${value}T12:00:00Z`);
  result.setUTCDate(result.getUTCDate() + delta);
  return result.toISOString().slice(0, 10);
}

function hours(value?: number | null): string {
  return numberFormatter.format(Math.round(value ?? 0));
}

const chartText = "#62737f";
const chartGrid = "rgba(16, 43, 60, 0.09)";

function commonOptions(label: string): ChartOptions<"line"> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { intersect: false, mode: "index" },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#102b3c",
        titleColor: "#ffffff",
        bodyColor: "#d9ebe9",
        padding: 12,
      },
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: chartText, maxRotation: 0 } },
      y: {
        beginAtZero: true,
        grid: { color: chartGrid },
        ticks: { color: chartText, callback: (value) => compactFormatter.format(Number(value)) },
        title: { display: true, text: label, color: chartText },
      },
    },
  };
}

export function Analytics({ onOpenData }: AnalyticsProps) {
  const [snapshot, setSnapshot] = useState<AnalyticsSnapshot | null>(null);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [typeCode, setTypeCode] = useState("ALL");
  const [helicoptersOnly, setHelicoptersOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activePreset, setActivePreset] = useState("day");

  const load = useCallback(async (
    selection?: { from: string; to: string; type: string; helicopters: boolean },
  ) => {
    setLoading(true);
    setError(null);
    try {
      const query = selection
        ? `?from=${selection.from}&to=${selection.to}&type=${encodeURIComponent(selection.type)}&helicopters=${selection.helicopters}`
        : "";
      const result = await fetchSnapshot(query);
      setSnapshot(result);
      if (!selection && result.selection.from_date && result.selection.to_date) {
        setFromDate(result.selection.from_date);
        setToDate(result.selection.to_date);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to load analytics");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const applySelection = (
    from = fromDate,
    to = toDate,
    type = typeCode,
    helicopters = helicoptersOnly,
  ) => {
    if (!from || !to) return;
    void load({ from, to, type, helicopters });
  };

  const choosePreset = (days: number) => {
    const anchor = snapshot?.selection.latest_available_date;
    if (!anchor) return;
    const from = shiftDate(anchor, -(days - 1));
    setFromDate(from);
    setToDate(anchor);
    setActivePreset(days === 1 ? "day" : String(days));
    applySelection(from, anchor);
  };

  const submitFilters = (event: FormEvent) => {
    event.preventDefault();
    setActivePreset("custom");
    applySelection();
  };

  const toggleHelicopters = () => {
    const next = !helicoptersOnly;
    setHelicoptersOnly(next);
    applySelection(fromDate, toDate, typeCode, next);
  };

  const dailyLabels = useMemo(
    () => snapshot?.daily_activity.map((row) => shortDate(row.utc_date)) ?? [],
    [snapshot?.daily_activity],
  );
  const aircraftChart = useMemo(() => ({
    labels: dailyLabels,
    datasets: [{
      label: "Unique aircraft",
      data: snapshot?.daily_activity.map((row) => row.unique_aircraft) ?? [],
      borderColor: "#167f75",
      backgroundColor: "rgba(89, 214, 197, 0.18)",
      pointBackgroundColor: "#167f75",
      pointRadius: dailyLabels.length === 1 ? 5 : 3,
      borderWidth: 2,
      fill: true,
      tension: 0.28,
    }],
  }), [dailyLabels, snapshot?.daily_activity]);
  const observationChart = useMemo(() => ({
    labels: dailyLabels,
    datasets: [{
      label: "Observations",
      data: snapshot?.daily_activity.map((row) => row.observations) ?? [],
      backgroundColor: "#315d74",
      borderRadius: 3,
      maxBarThickness: 44,
    }],
  }), [dailyLabels, snapshot?.daily_activity]);
  const typeChart = useMemo(() => {
    const rows = snapshot?.types.slice(0, 10) ?? [];
    return {
      labels: rows.map((row) => row.type_code),
      datasets: [{
        label: "Unique aircraft",
        data: rows.map((row) => row.unique_aircraft),
        backgroundColor: rows.map((_, index) => index === 0 ? "#59d6c5" : "#315d74"),
        borderRadius: 3,
      }],
    };
  }, [snapshot?.types]);
  const typeOptions: ChartOptions<"bar"> = {
    indexAxis: "y",
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { backgroundColor: "#102b3c", padding: 12 } },
    scales: {
      x: { beginAtZero: true, grid: { color: chartGrid }, ticks: { color: chartText } },
      y: { grid: { display: false }, ticks: { color: chartText, font: { family: "monospace", weight: 700 } } },
    },
  };

  const totals = snapshot?.totals;
  const coverage = snapshot?.coverage;
  const hasRows = Boolean(totals?.aircraft_days);

  return (
    <div className="analytics-surface" id="analytics">
      <section className="analytics-hero">
        <div>
          <p className="eyebrow">Phase 4 · local analytics</p>
          <h1>See the traffic shape behind every processed day.</h1>
          <p>Explore hub reach, tail activity hours, observation volume, and aircraft-type mix from compact daily summaries—not reconstructed journeys.</p>
        </div>
        <aside className={`coverage-card ${coverage?.complete ? "coverage-complete" : "coverage-partial"}`}>
          <span className="coverage-kicker">Dataset coverage</span>
          <strong>{coverage ? `${coverage.available_days} / ${coverage.requested_days}` : "—"}</strong>
          <p>{coverage?.message ?? "Reading local availability…"}</p>
          {coverage && !coverage.complete && coverage.missing_dates.length > 0 && (
            <button type="button" onClick={onOpenData}>Review missing days →</button>
          )}
        </aside>
      </section>

      <section className="analytics-controls" aria-label="Analytics filters">
        <div className="preset-group" aria-label="Snapshot period">
          <span>Snapshot</span>
          {[{ label: "Latest day", days: 1, key: "day" }, { label: "7 days", days: 7, key: "7" }, { label: "30 days", days: 30, key: "30" }].map((preset) => (
            <button key={preset.key} type="button" className={activePreset === preset.key ? "active" : ""} onClick={() => choosePreset(preset.days)} disabled={!snapshot?.selection.latest_available_date}>{preset.label}</button>
          ))}
        </div>
        <form onSubmit={submitFilters}>
          <label>From<input type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} required /></label>
          <label>To<input type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} required /></label>
          <label className="type-select">Aircraft type<select value={typeCode} onChange={(event) => setTypeCode(event.target.value)}><option value="ALL">All types</option>{snapshot?.type_options.map((option) => <option value={option.type_code} key={option.type_code}>{option.type_code} · {numberFormatter.format(option.unique_aircraft)}</option>)}</select></label>
          <button className="button button-secondary" type="submit" disabled={loading}>Apply view</button>
        </form>
        <button type="button" className={`heli-toggle ${helicoptersOnly ? "active" : ""}`} onClick={toggleHelicopters} aria-pressed={helicoptersOnly}><span>Rotorcraft only</span><i /></button>
      </section>

      {error && <div className="analytics-alert" role="alert">{error}<button type="button" onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}

      <section className={`analytics-metrics ${loading ? "analytics-loading" : ""}`} aria-label="Selected-period totals">
        <article><span>Unique tails</span><strong>{numberFormatter.format(totals?.unique_aircraft ?? 0)}</strong><small>{numberFormatter.format(totals?.aircraft_days ?? 0)} aircraft-day records</small></article>
        <article><span>Observations</span><strong>{compactFormatter.format(totals?.observations ?? 0)}</strong><small>Retained as daily volume only</small></article>
        <article><span>Estimated active hours</span><strong>{hours(totals?.active_hours)}</strong><small>{hours(totals?.airborne_hours)} airborne hours</small></article>
        <article><span>Hub-linked tails</span><strong>{numberFormatter.format(totals?.airport_linked_aircraft ?? 0)}</strong><small>{numberFormatter.format(totals?.known_type_codes ?? 0)} known type codes</small></article>
      </section>

      {!loading && !hasRows ? (
        <section className="no-analytics-data">
          <span>NO LOCAL ROWS</span><h2>There is no queryable data for this selection.</h2><p>Choose an available date or add the missing coverage from Data control.</p><button className="button button-primary" type="button" onClick={onOpenData}>Open data control</button>
        </section>
      ) : (
        <>
          <section className="chart-grid">
            <article className="analytics-panel chart-panel">
              <div className="analytics-panel-heading"><div><p className="section-index">01 · Daily reach</p><h2>Aircraft observed</h2></div><span>Unique ADS-B addresses</span></div>
              <div className="chart-frame"><Line data={aircraftChart} options={commonOptions("Unique tails")} role="img" aria-label="Daily unique aircraft observed" /></div>
            </article>
            <article className="analytics-panel chart-panel">
              <div className="analytics-panel-heading"><div><p className="section-index">02 · Source intensity</p><h2>Observation volume</h2></div><span>Raw messages summarised</span></div>
              <div className="chart-frame"><Bar data={observationChart} options={commonOptions("Observations") as ChartOptions<"bar">} role="img" aria-label="Daily ADS-B observation volume" /></div>
            </article>
          </section>

          <section className="type-layout">
            <article className="analytics-panel type-chart-panel">
              <div className="analytics-panel-heading"><div><p className="section-index">03 · Fleet shape</p><h2>Leading aircraft types</h2></div><span>By unique tail</span></div>
              <div className="type-chart-frame"><Bar data={typeChart} options={typeOptions} role="img" aria-label="Leading aircraft types by unique aircraft" /></div>
            </article>
            <article className="analytics-panel type-table-panel">
              <div className="analytics-panel-heading"><div><p className="section-index">Type ledger</p><h2>Hours by designator</h2></div></div>
              <div className="analytics-table-wrap"><table><thead><tr><th>Type</th><th>Tails</th><th>Active h</th><th>Airborne h</th></tr></thead><tbody>{snapshot?.types.slice(0, 12).map((row) => <tr key={`${row.type_code}-${row.category}`}><td><strong>{row.type_code}</strong><small>{row.description ?? "Unclassified description"}</small></td><td>{numberFormatter.format(row.unique_aircraft)}</td><td>{hours(row.active_hours)}</td><td>{hours(row.airborne_hours)}</td></tr>)}</tbody></table></div>
            </article>
          </section>

          <section className="rank-grid">
            <article className="analytics-panel rank-panel">
              <div className="analytics-panel-heading"><div><p className="section-index">04 · Hub activity</p><h2>Busiest observed airports</h2></div><span>Ranked by inferred movement candidates</span></div>
              <div className="analytics-table-wrap"><table><thead><tr><th>Hub</th><th>Movement tails</th><th>Movements</th><th>Arrivals</th><th>Departures</th></tr></thead><tbody>{snapshot?.hubs.slice(0, 12).map((row, index) => <tr key={row.airport_ident}><td><span className="rank-number">{String(index + 1).padStart(2, "0")}</span><strong>{row.iata_code ?? row.airport_ident}</strong><small>{row.airport_name}</small></td><td>{numberFormatter.format(row.movement_linked_aircraft)}</td><td>{numberFormatter.format(row.movement_candidates)}</td><td>{numberFormatter.format(row.arrival_candidates)}</td><td>{numberFormatter.format(row.departure_candidates)}</td></tr>)}</tbody></table></div>
            </article>
            <article className="analytics-panel rank-panel">
              <div className="analytics-panel-heading"><div><p className="section-index">05 · Tail activity</p><h2>Most active aircraft</h2></div><span>Observation-derived hours</span></div>
              <div className="analytics-table-wrap"><table><thead><tr><th>Tail</th><th>Operator</th><th>Type</th><th>Hub</th><th>Active h</th><th>Airborne h</th></tr></thead><tbody>{snapshot?.tails.slice(0, 12).map((row, index) => <tr key={row.address}><td><span className="rank-number">{String(index + 1).padStart(2, "0")}</span><strong>{row.registration ?? row.address.toUpperCase()}</strong><small>{row.address}</small></td><td><strong>{row.operator ?? "—"}</strong>{row.operator_source_code && <small>{row.operator_source_code}</small>}</td><td>{row.type_code ?? "—"}</td><td>{row.primary_airport_label ?? "—"}</td><td>{hours(row.active_hours)}</td><td>{hours(row.airborne_hours)}</td></tr>)}</tbody></table></div>
            </article>
          </section>

          <section className="helicopter-panel">
            <div className="helicopter-copy"><p className="section-index">06 · Rotorcraft lens</p><h2>Helicopter activity, separated from the fleet.</h2><p>{snapshot?.helicopters.classification_note}</p><div className="heli-metrics"><div><strong>{numberFormatter.format(snapshot?.helicopters.totals.unique_aircraft ?? 0)}</strong><span>classified tails</span></div><div><strong>{hours(snapshot?.helicopters.totals.active_hours)}</strong><span>active hours</span></div><div><strong>{numberFormatter.format(snapshot?.helicopters.totals.known_type_codes ?? 0)}</strong><span>type codes</span></div></div></div>
            <div className="helicopter-table"><table><thead><tr><th>Type</th><th>Description</th><th>Tails</th><th>Active h</th></tr></thead><tbody>{snapshot?.helicopters.types.slice(0, 10).map((row) => <tr key={row.type_code}><td><strong>{row.type_code}</strong></td><td>{row.description ?? "—"}</td><td>{numberFormatter.format(row.unique_aircraft)}</td><td>{hours(row.active_hours)}</td></tr>)}</tbody></table></div>
          </section>
        </>
      )}

      <section className="query-preview" aria-label="Natural-language analytics">
        <div><span>Phase 6 · Hub intelligence</span><h2>Ask the data</h2><p>Natural-language analytics uses this same coverage-aware movement and activity layer.</p></div>
        <div className="query-shell"><input aria-label="Example future analytics question" value="Which helicopter types were most active last week?" readOnly /><button type="button" disabled>Ask →</button></div>
      </section>

      <p className="analytics-footnote">Hours are estimates from consecutive ADS-B observations, not engine, block, maintenance, or regulatory time. Movement counts are conservative candidates derived from ground evidence and low, slow trace endpoints; they are not certified airport records.</p>
    </div>
  );
}
