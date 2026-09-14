"use client";

import { useEffect, useState } from "react";
import { elapsed, normalizeTail, readTailSelection, tailHref, utcTime } from "./tailLookup";
import "./aircraft-history.css";

type Visit = { first_evidence_at: string; last_evidence_at: string; ground_observation_count: number; ground_time_seconds: number; proximity_observation_count: number; arrival_evidence: string | null; departure_evidence: string | null };
type Flight = { address: string; segment_sequence: number; dataset_day_id: number; takeoff_at: string; landing_at: string; origin_airport_ident: string | null; destination_airport_ident: string | null; observed_airborne_seconds: number; elapsed_airborne_seconds: number; confidence: string; quality_flags: string[] };
type QuietWindow = { key: string; address: string; airport_ident: string; airport_name: string; started_at: string; ended_at: string; elapsed_seconds: number; open_end: boolean; before: Visit; after: Visit | null; ground_at_both_boundaries: boolean; processed_days: number; expected_days: number; intermediate_days: number; days_with_other_aircraft: number; days_with_other_helicopters: number; min_other_aircraft: number; mro_companies: string[]; previous_flight: Flight | null; next_flight: Flight | null };
type Event = { id: number; started_at: string; ended_at: string; maintenance_kind: string; status: string; notes: string; reviewed_by: string };
type History = {
  registration: string; from: string; to: string; latest_processed: string | null; type_codes: string[]; addresses: string[];
  coverage: { processed_days: number; expected_days: number };
  calendar: { utc_date: string; processed: boolean; observed: boolean }[];
  summary: { observed_days: number; quiet_windows: number; median_quiet_seconds: number | null; observations: number; ground_observations: number; observed_ground_seconds: number };
  bases: { airport_ident: string; airport_name: string; windows: number }[];
  intervals: QuietWindow[]; flights: Flight[]; flights_truncated: boolean; events: Event[]; events_truncated: boolean;
};

function FlightEvidence({ flight }: { flight: Flight | null }) {
  return flight ? <div className="history-flight-evidence">
    <strong>{flight.origin_airport_ident || "Unknown origin"} → {flight.destination_airport_ident || "Unknown destination"}</strong>
    <span>{utcTime(flight.takeoff_at)} → {utcTime(flight.landing_at)}</span>
    <span>{elapsed(flight.elapsed_airborne_seconds)} elapsed · {elapsed(flight.observed_airborne_seconds)} observed airborne</span>
    <small>{flight.confidence} confidence{flight.quality_flags.length ? ` · ${flight.quality_flags.join(", ")}` : ""}</small>
  </div> : <p>No flight episode available on this side of the interval.</p>;
}

export function AircraftHistory() {
  const [initial] = useState(readTailSelection);
  const [tail, setTail] = useState(initial.tail);
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const [query, setQuery] = useState({ ...initial, revision: 0 });
  const [data, setData] = useState<History | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [flightLimit, setFlightLimit] = useState(50);

  useEffect(() => {
    if (!query.tail) return;
    const controller = new AbortController();
    setBusy(true); setError(""); setData(null); setFlightLimit(50);
    let key: string;
    try { key = normalizeTail(query.tail); }
    catch (e) { setError((e as Error).message); setBusy(false); return; }
    const params = new URLSearchParams();
    if (query.from) params.set("from", query.from);
    if (query.to) params.set("to", query.to);
    fetch(`/api/maintenance/aircraft/${encodeURIComponent(key)}/history?${params}`, { signal: controller.signal })
      .then(async response => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Unable to load aircraft history");
        return result as History;
      }).then(result => {
        if (controller.signal.aborted) return;
        setData(result); setTail(result.registration); setFrom(result.from); setTo(result.to);
        const ranked = [...result.intervals].sort((a, b) => Number(a.open_end) - Number(b.open_end) || b.elapsed_seconds - a.elapsed_seconds);
        setSelected(ranked[0]?.key || "");
        window.history.replaceState(null, "", tailHref("aircraft", { tail: result.registration, from: result.from, to: result.to }));
      }).catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [query]);

  const gap = data?.intervals.find(g => g.key === selected);
  const closed = data?.intervals.filter(g => !g.open_end) || [];
  const plotStart = data ? Date.parse(`${data.from}T00:00:00Z`) : 0;
  const plotSpan = data ? Date.parse(`${data.to}T00:00:00Z`) + 86400000 - plotStart : 1;
  const position = (start: string, end: string) => ({ left: `${Math.max(0, (Date.parse(start) - plotStart) / plotSpan * 100)}%`, width: `${Math.max(.15, (Math.min(Date.parse(end), plotStart + plotSpan) - Math.max(Date.parse(start), plotStart)) / plotSpan * 100)}%` });
  const mapSelection = data ? { tail: data.registration, from: new Date(Math.max(Date.parse(`${data.from}T00:00:00Z`), Date.parse(`${data.to}T00:00:00Z`) - 30 * 86400000)).toISOString().slice(0, 10), to: data.to } : null;

  return <main className="aircraft-history">
    <header className="history-header"><p className="eyebrow">Aircraft intelligence</p><h1>Aircraft history</h1><p>Find overnight patterns, longer quiet windows and the evidence around them.</p></header>
    <form className="history-search" onSubmit={e => { e.preventDefault(); try { const key = normalizeTail(tail); setQuery({ tail: key, from, to, revision: query.revision + 1 }); } catch (err) { setError((err as Error).message); } }}>
      <label>Aircraft registration<input required value={tail} maxLength={16} onChange={e => setTail(e.target.value)} placeholder="e.g. G-SNSI" autoCapitalize="characters" spellCheck={false} /></label>
      <label>From (UTC)<input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
      <label>To (UTC)<input type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
      <button className="button button-primary" disabled={busy || !tail.trim()}>{busy ? "Loading history…" : "Look up aircraft"}</button>
      <p>Any registration · defaults to up to 90 days of available history · up to one year per search</p>
    </form>
    {error && <p className="history-warning" role="alert">{error}</p>}
    {busy && <p role="status">Reading aircraft activity and airport evidence…</p>}
    {!data && !busy && !error && <div className="history-empty"><h2>Where has this aircraft been resting?</h2><p>Enter a tail number to see sightings, likely stays and flight activity. No watchlist entry is needed.</p></div>}
    {data && <>
      <div className="history-result-heading"><h2>{data.registration} <span>{data.type_codes.join(" / ") || "Type unknown"}</span></h2>{mapSelection && <a className="button button-ghost" href={tailHref("tracks", mapSelection)}>Open Stops map{data.coverage.expected_days > 31 ? " · last 31 days" : ""}</a>}</div>
      <p className="history-coverage">{data.from} to {data.to} · {data.coverage.processed_days}/{data.coverage.expected_days} source dates processed with visit data. Latest available: {data.latest_processed || "none"}.</p>
      {data.addresses.length > 1 && <p className="history-warning">Multiple addresses recorded: {data.addresses.join(", ")}. Quiet windows remain separate for each address; check the aircraft identity.</p>}
      {data.coverage.processed_days < data.coverage.expected_days && <p className="history-warning">Some source dates are missing or have only older summaries. Quiet periods across them have incomplete coverage.</p>}
      {!data.summary.observed_days ? <div className="history-empty"><h2>No aircraft observations in this range</h2><p>Check the registration or try another period. This lookup uses the registration recorded on each historical date.</p></div> : <>
        <div className="history-stats">
          <div><span>Days with sightings</span><strong>{data.summary.observed_days}</strong></div>
          <div><span>Quiet windows with two endpoints</span><strong>{data.summary.quiet_windows}</strong></div>
          <div><span>Longest bracketed gap</span><strong>{closed.length ? elapsed(Math.max(...closed.map(g => g.elapsed_seconds))) : "Not bounded"}</strong></div>
          <div><span>Explicit ground observations</span><strong>{data.summary.ground_observations.toLocaleString()} <small>/ {data.summary.observations.toLocaleString()}</small></strong></div>
        </div>
        <section className="history-panel"><h2>Activity and likely stays</h2>
          <div className="history-legend"><span><i className="history-seen" />Day with sightings</span><span><i className="history-quiet" />Possible stay</span><span><i className="history-unknown" />Unobserved</span><span><i className="history-missing" />Source date missing</span></div>
          <div className="history-chart" role="img" aria-label={`Timeline of ${data.summary.observed_days} observed days and ${data.intervals.length} possible quiet windows`}>
            <p>Days with sightings</p><div className="history-days">{data.calendar.map(d => <span key={d.utc_date} title={`${d.utc_date}: ${d.observed ? "aircraft observed" : d.processed ? "no aircraft observations" : "source date missing"}`} className={d.observed ? "history-seen" : d.processed ? "history-unknown" : "history-missing"} />)}</div>
            <p>Possible stays between sightings</p><div className="history-window-track">{data.intervals.map(g => <span key={g.key} className={`history-window ${g.open_end ? "history-open" : ""} ${g.key === selected ? "history-selected" : ""}`} style={position(g.started_at, g.ended_at)} />)}</div>
            {data.events.length > 0 && <><p>Recorded maintenance reviews</p><div className="history-window-track">{data.events.map(e => <span key={e.id} className={`history-event history-event-${e.status.toLowerCase()}`} style={position(e.started_at, e.ended_at)} />)}</div></>}
            <div className="history-axis"><span>{data.from}</span><span>{data.to}</span></div>
          </div>
          <p className="history-note">Quiet windows join matching airport sightings across days, starting at six hours. They show possible stays, not continuous ground reception. {data.summary.median_quiet_seconds !== null && `Typical bracketed gap: ${elapsed(data.summary.median_quiet_seconds)}.`}</p>
          {data.bases.length > 0 && <p className="history-bases"><strong>Repeated overnight locations:</strong> {data.bases.slice(0, 5).map(b => `${b.airport_ident} (${b.windows} windows)`).join(" · ")}</p>}
        </section>
        {data.intervals.length > 0 ? <section className="history-panel history-inspector">
          <label>Inspect a quiet window<select value={selected} onChange={e => setSelected(e.target.value)}>{[...data.intervals].sort((a, b) => b.elapsed_seconds - a.elapsed_seconds).map(g => <option key={g.key} value={g.key}>{g.airport_ident} · {g.started_at.slice(0, 10)} · {elapsed(g.elapsed_seconds)}{g.open_end ? " · no later sighting" : ""}</option>)}</select></label>
          {gap && <>
            <div className="history-result-heading"><h2>{gap.airport_name} <span>{gap.airport_ident}</span></h2><strong className="history-duration">{elapsed(gap.elapsed_seconds)}</strong></div>
            <p>{utcTime(gap.started_at)} → {utcTime(gap.ended_at)}</p>
            {gap.open_end ? <p className="history-warning">No later sighting in this range. The interval ends at the end of the available source period; a later departure has not been observed here.</p> : <p className="history-note">Same airport at both endpoints, with no aircraft observations in between. {gap.ground_at_both_boundaries ? "Both boundary visits include explicit ground reports." : "At least one boundary relies on airport proximity evidence."}</p>}
            <dl className="history-evidence">
              <div><dt>Source dates processed</dt><dd>{gap.processed_days}/{gap.expected_days}</dd></div>
              <div><dt>Other aircraft at this airport</dt><dd>{gap.intermediate_days ? `${gap.days_with_other_aircraft}/${gap.intermediate_days} intervening dates (${gap.days_with_other_helicopters} with helicopters)` : "No full intervening dates"}</dd></div>
              <div><dt>Ground reports in boundary visits</dt><dd>{gap.before.ground_observation_count} / {gap.after?.ground_observation_count ?? "No later sighting"}</dd></div>
              <div><dt>Observed ground time in those visits</dt><dd>{elapsed(gap.before.ground_time_seconds)} / {gap.after ? elapsed(gap.after.ground_time_seconds) : "Not bounded"}</dd></div>
              <div><dt>Airport contacts in boundary visits</dt><dd>{gap.before.proximity_observation_count} / {gap.after?.proximity_observation_count ?? "Not bounded"}</dd></div>
              <div><dt>MRO links in the catalogue</dt><dd>{gap.mro_companies.join(", ") || "No airport-linked MRO recorded"}</dd></div>
            </dl>
            <p className="history-note">Other aircraft activity gives reception context. It does not establish this aircraft’s continuous presence. MRO links describe the airport, not a confirmed facility visit.</p>
            <div className="history-flight-pair"><div><h3>Previous observed flight episode</h3><FlightEvidence flight={gap.previous_flight} /></div><div><h3>Next observed flight episode</h3><FlightEvidence flight={gap.next_flight} /></div></div>
          </>}
        </section> : <p className="history-warning">No matching airport endpoints bound an overnight or longer gap in this range. The aircraft may have rested outside reception or away from a recorded airport.</p>}
      </>}
      <section className="history-panel"><h2>Recorded maintenance reviews</h2>
        <p className="history-note">Team evidence recorded in Maintenance Pulse is shown alongside the tracking.</p>
        {!data.events.length && <p>No maintenance reviews recorded for this tail in this range.</p>}
        {data.events.map(e => <article className="history-review" key={e.id}><strong>{e.maintenance_kind} · {e.status}</strong><p>{utcTime(e.started_at)} → {utcTime(e.ended_at)}</p><p>{e.notes}</p><small>Reviewed by {e.reviewed_by}</small></article>)}
        {data.events_truncated && <p>Showing the latest 500 reviews. Narrow the dates to see older reviews.</p>}
      </section>
      <details className="history-panel"><summary>Flight episodes ({data.flights.length}{data.flights_truncated ? "+" : ""})</summary>
        {data.flights_truncated && <p className="history-warning">Only the latest 5,000 episodes are available in this range. Earlier boundary flight evidence may be omitted.</p>}
        <div className="history-table-wrap"><table><thead><tr><th>Takeoff (UTC)</th><th>Route</th><th>Observed / elapsed</th><th>Evidence</th></tr></thead><tbody>{data.flights.slice(0, flightLimit).map(f => <tr key={`${f.dataset_day_id}:${f.address}:${f.segment_sequence}`}><td>{utcTime(f.takeoff_at)}</td><td>{f.origin_airport_ident || "Unknown"} → {f.destination_airport_ident || "Unknown"}</td><td>{elapsed(f.observed_airborne_seconds)} / {elapsed(f.elapsed_airborne_seconds)}</td><td>{f.confidence} · {f.quality_flags.join(", ") || "No flags"}</td></tr>)}</tbody></table></div>
        {flightLimit < data.flights.length && <button className="button button-ghost" onClick={() => setFlightLimit(n => n + 50)}>Show more flights</button>}
      </details>
    </>}
  </main>;
}
