"use client";
import { useEffect, useState } from "react";

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
  const [watches, setWatches] = useState<Row[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
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
  useEffect(() => { api("watches").then(setWatches).catch(e => setError(e.message)); }, []);
  async function open(id: unknown) {
    setBusy(true); setError(""); setDetail(null); setCandidate(null); setEditing(null);
    try { setDetail(await api(`watches/${id}`)); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function add() {
    setBusy(true); setError("");
    try { await api("watches", { registration: tail }); setTail(""); setWatches(await api("watches")); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function save() {
    if (!detail) return;
    setBusy(true); setError("");
    try {
      await api(`watches/${detail.watch.id}/reviews`, { event_id: editing?.id, candidate_key: candidate?.candidate_key, started_at: editing?.started_at || candidate?.candidate_started_at || `${start}T00:00:00Z`, ended_at: editing?.ended_at || candidate?.candidate_ended_at || `${end}T00:00:00Z`, status, maintenance_kind: kind, notes });
      setDetail(await api(`watches/${detail.watch.id}`)); setWatches(await api("watches")); setNotes(""); setCandidate(null); setEditing(null);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <main style={{ maxWidth: 1320, margin: "32px auto", padding: 24 }}>
    <p className="eyebrow">Maintenance Pulse · Team watchlist</p><h1>Understand the next maintenance opportunity</h1>
    <p>Review previous maintenance and the aircraft’s recent movements. A visit to an MRO airport does not establish which facility it entered or prove maintenance.</p>
    {error && <p role="alert" className="alert alert-error">{error}</p>}
    <form onSubmit={e => { e.preventDefault(); void add(); }} style={{ display: "flex", gap: 12, marginBottom: 24 }}>
      <label>Watch a tail <input required value={tail} maxLength={16} onChange={e => setTail(e.target.value)} placeholder="G-XXXX" /></label>
      <button className="button button-primary" disabled={busy}>Add to watchlist</button>
    </form>
    <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 24 }}>
      {watches.map(w => <button className="button button-secondary" disabled={busy} key={text(w.id)} onClick={() => void open(w.id)}>{text(w.registration)} · {w.last_confirmed_maintenance ? `Maintenance reviewed: ${text(w.last_confirmed_maintenance).slice(0,10)}` : "Needs benchmark"}</button>)}
      {!watches.length && <p>Add the first tail to begin collecting reviewed benchmarks.</p>}
    </div>
    {busy && <p role="status">Loading / saving…</p>}
    {detail && <>
      <h2>{text(detail.watch.registration)} · Behaviour and maintenance evidence</h2>
      <button className="button button-ghost" disabled={busy} onClick={async () => { setBusy(true); try { await api(`watches/${detail.watch.id}/archive`, {}); setDetail(null); setWatches(await api("watches")); } catch(e) { setError(String(e)); } finally { setBusy(false); } }}>Remove from active watchlist (retain reviews)</button>
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
    </>}
  </main>;
}
