"use client";
import { useEffect, useRef, useState } from "react";
import { emptyWatchFilters, filterWatches, unknownValue, watchOptions, type Watch } from "./maintenance-watchlist";
import "./maintenance-pulse.css";

type Row = Record<string, unknown>;
type Detail = { watch: Row; as_of: string; since_maintenance: Row | null; flights: Row[]; visits: Row[]; candidates: Row[]; events: Row[]; flights_truncated: boolean; visits_truncated: boolean; candidates_truncated: boolean };
const text = (v: unknown) => v == null ? "—" : Array.isArray(v) ? v.join(", ") : String(v);
async function api(path: string, body?: Row) {
  const response = await fetch(`/api/maintenance/${path}`, body ? { method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "HeligentAdmin" }, body: JSON.stringify(body) } : undefined);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}
export function MaintenancePulse() {
  const [watches, setWatches] = useState<Watch[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [filters, setFilters] = useState(emptyWatchFilters);
  const [removed, setRemoved] = useState<Watch | null>(null);
  const [notice, setNotice] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const openedWatchId = detail?.watch.id;
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [tail, setTail] = useState("");
  const [candidate, setCandidate] = useState<Row | null>(null);
  const [editing, setEditing] = useState<Row | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [kind, setKind] = useState("");
  const [notes, setNotes] = useState("");
  const [status, setStatus] = useState("UNCERTAIN");
  useEffect(() => { let active = true;
    api("watches").then(data => { if (active) setWatches(data); })
      .catch(e => { if (active) { setError(e.message); setLoadFailed(true); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (openedWatchId) detailHeading.current?.focus();
  }, [openedWatchId]);
  async function reload() {
    setLoading(true); setError("");
    try { setWatches(await api("watches")); setLoadFailed(false); }
    catch (e) { setError(String(e)); setLoadFailed(true); }
    finally { setLoading(false); }
  }
  async function open(id: unknown) {
    setBusy(true); setError(""); setDetail(null); setCandidate(null); setEditing(null);
    try { setDetail(await api(`watches/${id}`)); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function add() {
    setBusy(true); setError("");
    try { const added = await api("watches", { registration: tail }); setTail(""); setNotice(`${added.registration} is on your watchlist.`); setRemoved(null); setFilters(emptyWatchFilters); setWatches(await api("watches")); setLoadFailed(false); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function remove(watch: Watch) {
    setBusy(true); setError("");
    try {
      await api(`watches/${watch.id}/archive`, {});
      setWatches(current => current.filter(w => w.id !== watch.id));
      if (detail?.watch.id === watch.id) { setDetail(null); setCandidate(null); setEditing(null); }
      setRemoved(watch); setNotice("");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function undoRemove() {
    if (!removed) return;
    setBusy(true); setError("");
    try {
      await api("watches", { registration: removed.registration, notes: removed.notes || "" });
      setWatches(current => [...current.filter(w => w.id !== removed.id), removed].sort((a, b) => a.registration.localeCompare(b.registration)));
      setNotice(`${removed.registration} restored to your watchlist.`); setRemoved(null);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function save() {
    if (!detail) return;
    setBusy(true); setError("");
    try {
      await api(`watches/${detail.watch.id}/reviews`, { event_id: editing?.id, candidate_key: candidate?.candidate_key, started_at: editing?.started_at || candidate?.candidate_started_at || `${start}T00:00:00Z`, ended_at: editing?.ended_at || candidate?.candidate_ended_at || `${end}T00:00:00Z`, status, maintenance_kind: kind, notes });
      setDetail(await api(`watches/${detail.watch.id}`)); setWatches(await api("watches")); setNotes(""); setCandidate(null); setEditing(null);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  const visibleWatches = filterWatches(watches, filters);
  const operators = watchOptions(watches, "operator");
  const types = watchOptions(watches, "type_code");
  const hasFilters = Object.values(filters).some(Boolean);
  const disabled = busy || loading;
  return <main className="maintenance-pulse">
    <header className="pulse-header">
      <div><p className="eyebrow">Maintenance Pulse</p><h1>Team watchlist</h1>
        <p>Keep an eye on your aircraft and build a clearer maintenance history.</p></div>
      <form onSubmit={e => { e.preventDefault(); void add(); }} className="pulse-add">
        <label htmlFor="pulse-tail">Watch an aircraft</label>
        <div><input id="pulse-tail" required value={tail} maxLength={16} onChange={e => setTail(e.target.value)} placeholder="e.g. G-XXXX" autoCapitalize="characters" spellCheck={false} />
          <button className="button button-primary" disabled={disabled || !tail.trim()}>Add to watchlist</button></div>
      </form>
    </header>
    {error && <p role="alert" className="alert alert-error">{error}</p>}
    {(removed || notice) && <div className="pulse-feedback"><p role="status">{removed ? `${removed.registration} removed. Its reviews are saved.` : notice}</p>
      {removed && <button className="button button-ghost" disabled={busy} onClick={() => void undoRemove()}>Undo removal</button>}
      <button className="pulse-text-button" onClick={() => { setRemoved(null); setNotice(""); }} disabled={busy} aria-label="Dismiss notification">Dismiss</button>
    </div>}
    <section className="pulse-watchlist" aria-label="Watched aircraft">
      <div className="pulse-filters">
        <label className="pulse-search">Search watchlist<input type="search" value={filters.search} onChange={e => setFilters({ ...filters, search: e.target.value })} placeholder="Registration, operator or type" /></label>
        <label>Operator<select value={filters.operator} onChange={e => setFilters({ ...filters, operator: e.target.value })}>
          <option value="">All operators</option>{operators.map(operator => <option key={operator} value={operator}>{operator}</option>)}
          {filters.operator && filters.operator !== unknownValue && !operators.includes(filters.operator) && <option value={filters.operator}>{filters.operator}</option>}
          <option value={unknownValue}>Unknown operator</option>
        </select></label>
        <label>Aircraft type<select value={filters.type} onChange={e => setFilters({ ...filters, type: e.target.value })}>
          <option value="">All types</option>{types.map(type => <option key={type} value={type}>{type}</option>)}
          {filters.type && filters.type !== unknownValue && !types.includes(filters.type) && <option value={filters.type}>{filters.type}</option>}
          <option value={unknownValue}>Unknown type</option>
        </select></label>
        <label>Review status<select value={filters.review} onChange={e => setFilters({ ...filters, review: e.target.value })}>
          <option value="">All statuses</option><option value="needs-benchmark">Needs benchmark</option><option value="reviewed">Has confirmed maintenance</option>
        </select></label>
      </div>
      <div className="pulse-list-summary">
        <p role="status">{loading ? "Loading watchlist…" : loadFailed ? "Watchlist unavailable" : `${visibleWatches.length} of ${watches.length} aircraft`}</p>
        {hasFilters && <button className="pulse-text-button" onClick={() => setFilters(emptyWatchFilters)}>Clear filters</button>}
        <span>Removing an aircraft keeps its reviews.</span>
      </div>
      {!loading && !loadFailed && visibleWatches.length > 0 && <table className="pulse-table">
        <caption className="pulse-sr-only">Team maintenance watchlist. Open aircraft details or remove an aircraft directly.</caption>
        <thead><tr><th scope="col">Aircraft</th><th scope="col">Operator</th><th scope="col">Type</th><th scope="col">Maintenance benchmark</th><th scope="col">Last observed</th><th scope="col"><span className="pulse-sr-only">Actions</span></th></tr></thead>
        <tbody>{visibleWatches.map(w => <tr key={w.id} className={detail?.watch.id === w.id ? "pulse-selected" : undefined}>
          <td className="pulse-aircraft"><button className="pulse-tail-link" disabled={disabled} onClick={() => void open(w.id)} aria-label={`Open ${w.registration} details`} aria-expanded={detail?.watch.id === w.id} aria-controls="pulse-detail">{w.registration}</button></td>
          <td data-label="Operator" className={!w.operator ? "pulse-muted" : undefined}>{w.operator || "Unknown operator"}</td>
          <td data-label="Type" className={!w.type_code ? "pulse-muted" : undefined}>{w.type_code || "Unknown type"}</td>
          <td data-label="Benchmark"><span className={`pulse-badge ${w.last_confirmed_maintenance ? "pulse-reviewed" : "pulse-needs-review"}`}>{w.last_confirmed_maintenance ? "Confirmed maintenance" : "Needs benchmark"}</span>
            {w.last_confirmed_maintenance && <time className="pulse-review-date" dateTime={w.last_confirmed_maintenance}>{w.last_confirmed_maintenance.slice(0, 10)}</time>}</td>
          <td data-label="Last observed" className="pulse-muted">{w.last_seen_date ? w.last_seen_date.slice(0, 10) : "No observations"}</td>
          <td className="pulse-row-actions"><button className="button pulse-remove" disabled={disabled} onClick={() => void remove(w)} aria-label={`Remove ${w.registration} from watchlist`} title="Remove from watchlist; reviews are retained">Remove</button></td>
        </tr>)}</tbody>
      </table>}
      {!loading && loadFailed && <div className="pulse-empty"><h2>We couldn’t load your watchlist</h2><button className="button button-ghost" onClick={() => void reload()}>Try again</button></div>}
      {!loading && !loadFailed && !visibleWatches.length && <div className="pulse-empty">
        <h2>{watches.length ? "No aircraft match these filters" : "Your watchlist is ready for its first aircraft"}</h2>
        <p>{watches.length ? "Try another operator, type or registration, or clear your filters." : "Add a registration above to start reviewing its maintenance history."}</p>
        {hasFilters && <button className="button button-ghost" onClick={() => setFilters(emptyWatchFilters)}>Show all aircraft</button>}
      </div>}
      <p className="pulse-data-note">Operators use current recorded assignments. Type and last observed date use the latest matching processed observations.</p>
    </section>
    <p className="pulse-evidence-note">An MRO airport visit is a lead to review; it does not confirm a facility visit or maintenance.</p>
    {busy && <p role="status">Loading / saving…</p>}
    <section id="pulse-detail" aria-label="Aircraft maintenance details">{detail && <>
      <h2 ref={detailHeading} tabIndex={-1} className="pulse-detail-heading">{text(detail.watch.registration)} · Behaviour and maintenance evidence</h2>
      <button className="button button-ghost" disabled={busy} onClick={() => { setDetail(null); setCandidate(null); setEditing(null); }}>Close aircraft details</button>
      {detail.since_maintenance && <p>Since last confirmed event ({text(detail.since_maintenance.baseline)}): {Number(detail.since_maintenance.observed_hours).toFixed(1)} observed / {Number(detail.since_maintenance.elapsed_hours).toFixed(1)} elapsed airborne hours; {text(detail.since_maintenance.episodes)} inferred episodes. Processed-day coverage: {text(detail.since_maintenance.processed_days)}/{text(detail.since_maintenance.expected_days)}. These are estimates, not maintenance counters.</p>}
      <p>Latest episode-capable processed date: {text(detail.as_of)}. Timeline covers the latest 90 days. Missing observations are not proof the aircraft stayed on the ground.</p>
      {(detail.flights_truncated || detail.visits_truncated || detail.candidates_truncated) && <p role="status">This view is limited to 500 entries per section; some older entries are omitted.</p>}
      <section className="panel" style={{ padding: 24, marginBottom: 24 }}><h2>Suspected maintenance stays</h2>
        {!detail.candidates.length && <p>No candidates in this review window. Check processed coverage and MRO airport links, or add a known event below.</p>}
        {detail.candidates.map(c => <article key={text(c.candidate_key)} style={{ borderBottom: "1px solid #ddd", padding: "16px 0" }}>
          <strong>{text(c.airport)} · {text(c.airport_name)}</strong>
          <p>{text(c.candidate_started_at)} → {text(c.candidate_ended_at)} · {text(c.elapsed_days)} days · {text(c.confidence)} inference confidence</p>
          <p>MROs at airport: {text(c.mro_companies)}. Processed dates: {text(c.processed_coverage_days)}/{text(c.expected_coverage_days)}. Intervening flight hours: {text(c.intermediate_flight_hours)}.</p>
          <p>{text(c.evidence_flags)}</p>
          <button className="button button-secondary" onClick={() => { setCandidate(c); setEditing(null); setStatus("UNCERTAIN"); setNotes(""); setKind(""); document.getElementById("maintenance-review")?.scrollIntoView({ behavior: "smooth" }); }}>Review this stay</button>
        </article>)}
      </section>
      <section className="panel" id="maintenance-review" style={{ padding: 24, marginBottom: 24 }}><h2>{editing ? "Revise maintenance decision" : candidate ? `Review stay at ${text(candidate.airport)}` : "Record known maintenance"}</h2>
        <form onSubmit={e => { e.preventDefault(); void save(); }} style={{ display: "grid", gap: 16 }}>
          {candidate || editing ? <><p>{text(editing?.started_at || candidate?.candidate_started_at)} → {text(editing?.ended_at || candidate?.candidate_ended_at)}</p><button type="button" onClick={() => { setCandidate(null); setEditing(null); }}>Switch to manual event</button></> : <><label>Started (UTC date)<input required type="date" value={start} onChange={e => setStart(e.target.value)} /></label><label>Completed (UTC date)<input required type="date" value={end} onChange={e => setEnd(e.target.value)} /></label></>}
          <label>Maintenance type / check (use unknown if unsure)<input required maxLength={100} value={kind} onChange={e => setKind(e.target.value)} /></label>
          <label>Decision <select value={status} onChange={e => setStatus(e.target.value)}><option>UNCERTAIN</option><option>CONFIRMED</option><option>REJECTED</option></select></label>
          <label>Evidence / source / reasoning<textarea required maxLength={4000} value={notes} onChange={e => setNotes(e.target.value)} style={{ width: "100%", minHeight: 90 }} /></label>
          <p>Confirm only with supporting evidence. These reviews inform commercial planning, not airworthiness or approved maintenance schedules.</p>
          <button className="button button-primary" disabled={busy}>Save review</button>
        </form>
      </section>
      <h2>Reviewed events</h2>{detail.events.map(e => <p key={text(e.id)}>{text(e.started_at)} → {text(e.ended_at)} · {text(e.maintenance_kind)} · <strong>{text(e.status)}</strong> · {text(e.notes)} — {text(e.reviewed_by)} <button onClick={() => { setEditing(e); setCandidate(null); setKind(text(e.maintenance_kind)); setNotes(text(e.notes)); setStatus(text(e.status)); document.getElementById("maintenance-review")?.scrollIntoView(); }}>Revise review</button></p>)}
      <h2>Flight timeline</h2><div style={{ overflowX: "auto" }}><table className="activity-table"><thead><tr><th>Takeoff → landing (UTC)</th><th>Route</th><th>Observed / elapsed hours</th><th>Evidence</th></tr></thead><tbody>{detail.flights.map((f,i) => <tr key={i}><td>{text(f.takeoff_at)} → {text(f.landing_at)}</td><td>{text(f.origin_airport)} → {text(f.destination_airport)}</td><td>{Number(f.observed_airborne_hours).toFixed(2)} / {Number(f.elapsed_airborne_hours).toFixed(2)}</td><td>{text(f.confidence)} · {text(f.quality_flags)}</td></tr>)}</tbody></table></div>
      <h2>Airport visits</h2><div style={{ overflowX: "auto" }}><table className="activity-table"><thead><tr><th>First → last evidence (UTC)</th><th>Airport</th><th>Ground observations</th><th>Confidence / gaps</th></tr></thead><tbody>{detail.visits.map((v,i) => <tr key={i}><td>{text(v.first_evidence_at)} → {text(v.last_evidence_at)}</td><td>{text(v.airport)} · {text(v.airport_name)}</td><td>{text(v.ground_observation_count)}</td><td>{text(v.confidence)} · {text(v.quality_flags)}</td></tr>)}</tbody></table></div>
    </>}</section>
  </main>;
}
