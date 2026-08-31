"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Bar, Line } from "react-chartjs-2";
import {
  BarController, BarElement, CategoryScale, Chart as ChartJS, Filler, LinearScale,
  LineController, LineElement, PointElement, Tooltip,
} from "chart.js";
import { OperatorPicker } from "./OperatorPicker";

ChartJS.register(BarController, BarElement, CategoryScale, Filler, LinearScale, LineController, LineElement, PointElement, Tooltip);

type Metric = { unique_aircraft: number; observations: number; active_hours: number; airborne_hours: number };
type Snapshot = {
  selection: { from_date: string | null; to_date: string | null; latest_available_date: string | null };
  coverage: { requested_days: number; available_days: number; complete: boolean; message: string; missing_dates: string[] };
  totals: Metric & { aircraft_days: number; known_type_codes: number; airport_linked_aircraft: number };
  daily_activity: Array<Metric & { utc_date: string }>;
  types: Array<Metric & { type_code: string; description?: string | null }>;
  tails: Array<Metric & { address: string; registration?: string | null; operator?: string | null; type_code?: string | null; primary_airport_label?: string | null; active_days: number }>;
  operators: Array<Metric & { operator: string; operator_source_code?: string | null; known_type_codes: number; active_days: number }>;
  hubs: Array<{ airport_ident: string; iata_code?: string | null; airport_name: string; iso_country?: string | null; unique_aircraft: number; endpoint_linked_aircraft: number; movement_linked_aircraft: number; movement_candidates: number }>;
};

const nf = new Intl.NumberFormat("en-GB");
const compact = new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 });
const day = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" });
const hours = (value?: number | null) => nf.format(Math.round(value ?? 0));
const apiBase = () => typeof window !== "undefined" && window.location.port === "3000" ? "http://127.0.0.1:5080" : "";

export function EuropeHelicopters({ onOpenData }: { onOpenData: () => void }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (from?: string, to?: string) => {
    setLoading(true); setError(null);
    try {
      const range = from && to ? `&from=${from}&to=${to}` : "";
      let response = await fetch(`${apiBase()}/api/analytics/snapshot?helicopters=true&region=Europe${range}`, { headers: { "X-Requested-With": "HeligentAdmin" } });
      let result = await response.json();
      if (!response.ok) throw new Error(result.error ?? "Unable to load helicopter activity");
      if (!from && result.selection.latest_available_date) {
        const end = result.selection.latest_available_date;
        const start = `${end.slice(0, 8)}01`;
        setFromDate(start); setToDate(end);
        response = await fetch(`${apiBase()}/api/analytics/snapshot?helicopters=true&region=Europe&from=${start}&to=${end}`, { headers: { "X-Requested-With": "HeligentAdmin" } });
        result = await response.json();
        if (!response.ok) throw new Error(result.error ?? "Unable to load helicopter activity");
      }
      setSnapshot(result);
    } catch (exc) { setError(exc instanceof Error ? exc.message : "Unable to load helicopter activity"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);
  const submit = (event: FormEvent) => { event.preventDefault(); void load(fromDate, toDate); };
  const trend = useMemo(() => ({ labels: snapshot?.daily_activity.map(row => day.format(new Date(`${row.utc_date}T12:00:00Z`))) ?? [], datasets: [{ data: snapshot?.daily_activity.map(row => row.unique_aircraft) ?? [], borderColor: "#59d6c5", backgroundColor: "rgba(89,214,197,.16)", fill: true, tension: .3, pointRadius: 2 }] }), [snapshot]);
  const types = useMemo(() => { const rows = snapshot?.types.slice(0, 10) ?? []; return { labels: rows.map(row => row.type_code), datasets: [{ data: rows.map(row => row.unique_aircraft), backgroundColor: rows.map((_, i) => i === 0 ? "#59d6c5" : "#315d74"), borderRadius: 3 }] }; }, [snapshot]);
  const chartOptions = { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { backgroundColor: "#102b3c" } }, scales: { x: { grid: { display: false }, ticks: { color: "#71828a" } }, y: { beginAtZero: true, grid: { color: "rgba(16,43,60,.08)" }, ticks: { color: "#71828a" } } } } as const;
  const hasRows = Boolean(snapshot?.totals.aircraft_days);

  return <div className="euro-heli-surface" id="helicopters">
    <section className="euro-heli-hero"><div><p className="eyebrow">European rotorcraft intelligence</p><h1>Helicopter activity across Europe.</h1><p>See the tails, operators, aircraft types and hubs shaping the latest month of observed rotorcraft activity.</p></div><aside><span>DEFAULT VIEW</span><strong>Latest 30 days</strong><p>Europe-linked aircraft · UTC coverage</p></aside></section>
    <section className="euro-heli-controls"><div><span className="scope-dot" /> Rotorcraft only <b>·</b> Europe</div><form onSubmit={submit}><label>From<input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)} required /></label><label>To<input type="date" value={toDate} onChange={e => setToDate(e.target.value)} required /></label><button className="button button-secondary" disabled={loading}>Update</button></form></section>
    {error && <div className="analytics-alert" role="alert">{error}</div>}
    <section className={`euro-heli-metrics ${loading ? "analytics-loading" : ""}`}><article><span>Unique tails</span><strong>{nf.format(snapshot?.totals.unique_aircraft ?? 0)}</strong><small>{nf.format(snapshot?.totals.aircraft_days ?? 0)} aircraft-days</small></article><article><span>Active hours</span><strong>{hours(snapshot?.totals.active_hours)}</strong><small>{hours(snapshot?.totals.airborne_hours)} airborne</small></article><article><span>Observations</span><strong>{compact.format(snapshot?.totals.observations ?? 0)}</strong><small>ADS-B messages summarised</small></article><article><span>Coverage</span><strong>{snapshot ? `${snapshot.coverage.available_days}/${snapshot.coverage.requested_days}` : "—"}</strong><small>{snapshot?.coverage.complete ? "Complete requested period" : "Partial local coverage"}</small></article></section>
    {!loading && !hasRows ? <section className="no-analytics-data"><span>NO MATCHING ROWS</span><h2>No European helicopter activity is available for this period.</h2><p>{snapshot?.coverage.message}</p><button className="button button-primary" onClick={onOpenData}>Open data control</button></section> : <>
      <section className="euro-heli-charts"><article className="analytics-panel"><div className="analytics-panel-heading"><div><p className="section-index">01 · Activity trend</p><h2>Daily helicopter reach</h2></div><span>Unique tails per day</span></div><div className="chart-frame"><Line data={trend} options={chartOptions} /></div></article><article className="analytics-panel"><div className="analytics-panel-heading"><div><p className="section-index">02 · Fleet mix</p><h2>Top aircraft types</h2></div><span>By unique tail</span></div><div className="chart-frame"><Bar data={types} options={{...chartOptions, indexAxis: "y" as const}} /></div></article></section>
      <section className="euro-heli-ranks"><Rank title="Top tails" kicker="03 · Aircraft" headers={["Tail", "Operator", "Type", "Hub", "Active h"]} rows={(snapshot?.tails ?? []).slice(0, 12).map((r, i) => [rank(i, r.registration ?? r.address.toUpperCase()), <OperatorPicker key={r.address} address={r.address} registration={r.registration} operator={r.operator} onSaved={() => void load(fromDate, toDate)} />, r.type_code ?? "—", r.primary_airport_label ?? "—", hours(r.active_hours)])} /><Rank title="Top operators" kicker="04 · Operators" headers={["Operator", "Tails", "Types", "Active h", "Airborne h"]} rows={(snapshot?.operators ?? []).slice(0, 12).map((r, i) => [rank(i, r.operator), nf.format(r.unique_aircraft), nf.format(r.known_type_codes), hours(r.active_hours), hours(r.airborne_hours)])} /><Rank title="Top types" kicker="05 · Types" headers={["Type", "Description", "Tails", "Active h", "Airborne h"]} rows={(snapshot?.types ?? []).slice(0, 12).map((r, i) => [rank(i, r.type_code), r.description ?? "—", nf.format(r.unique_aircraft), hours(r.active_hours), hours(r.airborne_hours)])} /><Rank title="Busiest hubs" kicker="06 · Geography" note="Ranked by inferred arrival and departure candidates" headers={["Hub", "Airport", "Country", "Movement tails", "Movements"]} rows={(snapshot?.hubs ?? []).slice(0, 12).map((r, i) => [rank(i, r.iata_code ?? r.airport_ident), r.airport_name, r.iso_country ?? "—", nf.format(r.movement_linked_aircraft), nf.format(r.movement_candidates)])} /></section>
    </>}
    <p className="analytics-footnote">Europe means aircraft whose strongest observed airport link was in the European region during the selected period. Hours and movements are observation-derived estimates, not certified flight or maintenance records.</p>
  </div>;
}

function rank(index: number, value: string) { return <><span className="rank-number">{String(index + 1).padStart(2, "0")}</span><strong>{value}</strong></>; }
function Rank({ title, kicker, note = "Latest selection", headers, rows }: { title: string; kicker: string; note?: string; headers: string[]; rows: React.ReactNode[][] }) { return <article className="analytics-panel rank-panel"><div className="analytics-panel-heading"><div><p className="section-index">{kicker}</p><h2>{title}</h2></div><span>{note}</span></div><div className="analytics-table-wrap"><table><thead><tr>{headers.map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody></table></div></article>; }
