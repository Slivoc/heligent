"use client";
import { FormEvent, useEffect, useState } from "react";
import "./capability-mappings.css";

type Summary = { id: number; company_name: string; site_name: string | null; approval_number: string; approval_status: string; model: string | null; aircraft_type_code: string | null; limitation: string | null; mapping_count: number };
type Mapping = { id: number; aircraft_type_code: string; match_level: string; variant_scope: string; source_url: string; notes: string; revision: number; active: boolean; stale: boolean; reviewed_by: string; reviewed_at: string; evidence_snapshot: Record<string, unknown> };
type Detail = { capability: Summary & { active: boolean; company_active: boolean; site_active: boolean | null; is_base_maintenance: boolean; is_line_maintenance: boolean }; source_snapshot: Record<string, unknown>; mappings: Mapping[]; history: { snapshot: Mapping; recorded_at: string }[]; history_truncated: boolean };
const blank = (code = "") => ({ aircraft_type_code: code, match_level: "POSSIBLE_FAMILY", variant_scope: "", source_url: "", notes: "", revision: 0, active: true, type_wide_confirmed: false });
async function api<T>(path: string, signal?: AbortSignal, body?: unknown): Promise<T> {
  const r = await fetch(`/api/capability-mappings${path}`, { signal, ...(body ? { method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "HeligentAdmin" }, body: JSON.stringify(body) } : {}) });
  const value = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(value.error || `Request failed (${r.status})`);
  return value;
}

export function CapabilityMappings({ initialCapabilityId = null }: { initialCapabilityId?: number | null }) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState({ query: "", offset: 0 });
  const [results, setResults] = useState<{ rows: Summary[]; has_more: boolean } | null>(null);
  const [selection, setSelection] = useState({ id: initialCapabilityId, reload: 0 });
  const [detail, setDetail] = useState<Detail | null>(null);
  const [form, setForm] = useState(blank());
  const [canEdit, setCanEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/auth/session', { signal: controller.signal }).then(r => r.ok ? r.json() : null)
      .then(v => setCanEdit(["ADMIN", "ANALYST"].includes(v?.user?.role))).catch(() => {});
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    api<{ rows: Summary[]; has_more: boolean }>(`?q=${encodeURIComponent(search.query)}&offset=${search.offset}`, controller.signal)
      .then(setResults).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [search]);
  useEffect(() => {
    if (selection.id == null) return;
    const controller = new AbortController();
    api<Detail>(`/${selection.id}`, controller.signal).then(value => { setDetail(value); setForm(blank(value.capability.aircraft_type_code || "")); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [selection]);
  function open(id: number) {
    setDetail(null); setError(""); setNotice("");
    setSelection(previous => ({ id, reload: previous.reload + 1 }));
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!detail) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api(`/${detail.capability.id}`, undefined, { ...form, evidence_snapshot: detail.source_snapshot });
      const updated = await api<Detail>(`/${detail.capability.id}`);
      setDetail(updated); setForm(blank()); setNotice("Mapping saved. Reload the Stops map to use the updated review.");
      setSearch(previous => ({ ...previous }));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  const editable = canEdit && detail?.capability.active && detail.capability.company_active && detail.capability.site_active !== false;
  return <main className="capability-review-page">
    <p className="eyebrow">Maintenance intelligence · Capability review</p><h1>Connect capability wording to aircraft types</h1>
    <p>Mappings belong to this specific approval capability. They do not change the uploaded wording or grant capability to other sites.</p>
    <form className="capability-search" onSubmit={e => { e.preventDefault(); setResults(null); setError(""); setSearch({ query, offset: 0 }); }}>
      <label>Search company, approval or capability<input value={query} onChange={e => setQuery(e.target.value)} maxLength={200} placeholder="Airbus, BK117, UK.145.00124…" /></label><button disabled={busy}>Search</button>
    </form>
    {error && <p className="map-warning" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="capability-review-layout">
      <section className="capability-results"><h2>Imported aircraft capabilities</h2>
        {!results && <p>Loading capabilities…</p>}{results?.rows.length === 0 && <p>No matching active aircraft capabilities.</p>}
        {results?.rows.map(c => <button type="button" key={c.id} disabled={busy} aria-pressed={selection.id === c.id} onClick={() => open(c.id)}><strong>{c.company_name}</strong><span>{c.site_name || "Company-wide scope"} · {c.approval_number} · {c.approval_status}</span><p>{c.limitation || c.model || c.aircraft_type_code || "No model text"}</p><small>{c.mapping_count} active mappings</small></button>)}
        <div className="capability-pagination"><button disabled={busy || search.offset === 0} onClick={() => { setResults(null); setSearch(s => ({ ...s, offset: Math.max(0, s.offset - 50) })); }}>Previous</button><span>Page {search.offset / 50 + 1}</span><button disabled={busy || !results?.has_more} onClick={() => { setResults(null); setSearch(s => ({ ...s, offset: s.offset + 50 })); }}>Next</button></div>
      </section>
      <section className="capability-editor">
        {!detail ? <p>{selection.id ? "Loading selected capability…" : "Select a capability to review its type mappings."}</p> : <>
          <h2>{detail.capability.company_name}</h2><p>{detail.capability.site_name || "Company-wide scope — not confirmed at individual bases"} · {detail.capability.approval_number} · {detail.capability.approval_status}</p>
          <h3>Original capability wording</h3><blockquote>{detail.capability.limitation || detail.capability.model || "No descriptive wording supplied"}</blockquote>
          <p>Base maintenance: {detail.capability.is_base_maintenance ? "recorded" : "not recorded"} · Line maintenance: {detail.capability.is_line_maintenance ? "recorded" : "not recorded"}</p>
          <p className="map-note">These flags and limitations remain in force. A mapping is not proof of suitability for a particular check or aircraft variant.</p>
          <button type="button" disabled={busy} onClick={() => open(detail.capability.id)}>Reload capability (discard unsaved edits)</button>
          <h3>Saved mappings</h3>{detail.mappings.length === 0 && <p>No interpretations saved yet.</p>}
          {detail.mappings.map(m => <article className="saved-mapping" key={m.id}>
            <strong>{m.aircraft_type_code} · {m.match_level === "POSSIBLE_FAMILY" ? "Possible family match" : "Reviewed type mapping"}{!m.active ? " · WITHDRAWN" : ""}</strong>
            <p>{m.variant_scope ? `Restricted variants: ${m.variant_scope}. The map remains amber until aircraft variant is verified.` : "No variant restriction recorded"}</p>
            {m.stale && <p className="map-warning">Imported scope changed. This mapping needs re-review and cannot produce a confirmed match. Previous wording: {String(m.evidence_snapshot.limitation || m.evidence_snapshot.model || "not recorded")}</p>}
            <p>{m.notes}</p><a href={m.source_url} target="_blank" rel="noreferrer">Evidence source ↗</a><small>Reviewed by {m.reviewed_by} · {m.reviewed_at} · revision {m.revision}</small>
            {editable && <button type="button" disabled={busy} onClick={() => { setForm({ aircraft_type_code: m.aircraft_type_code, match_level: m.match_level, variant_scope: m.variant_scope, source_url: m.source_url, notes: m.notes, revision: m.revision, active: m.active, type_wide_confirmed: false }); setNotice(""); }}>Edit / re-review / withdraw {m.aircraft_type_code}</button>}
          </article>)}
          {!editable ? <p>Read-only. An analyst or administrator can review active capabilities.</p> : <form onSubmit={save} className="mapping-form">
            <h3>{form.revision ? `Review ${form.aircraft_type_code}` : "Add a mapping"}</h3>
            <p className="map-note">For broad “MBB-BK117 SERIES” wording and EC45, choose Possible family match unless the precise scope has been verified. No global alias is created.</p>
            <fieldset disabled={busy}>
              <label>ICAO aircraft type<input required pattern="[A-Za-z0-9]{2,4}" maxLength={4} value={form.aircraft_type_code} disabled={form.revision > 0} onChange={e => setForm(f => ({ ...f, aircraft_type_code: e.target.value.toUpperCase() }))} placeholder="EC45" /></label>
              <label>Match level<select value={form.match_level} onChange={e => setForm(f => ({ ...f, match_level: e.target.value, type_wide_confirmed: false }))}><option value="POSSIBLE_FAMILY">Possible family match — amber</option><option value="REVIEWED_TYPE">Reviewed type / variant mapping</option></select></label>
              <label>Restricted variants (if applicable)<input maxLength={1000} value={form.variant_scope} onChange={e => setForm(f => ({ ...f, variant_scope: e.target.value, type_wide_confirmed: false }))} placeholder="e.g. MBB-BK117 C-2 only" /></label>
              {form.match_level === "REVIEWED_TYPE" && !form.variant_scope.trim() && <label className="mapping-check"><input type="checkbox" required checked={form.type_wide_confirmed} onChange={e => setForm(f => ({ ...f, type_wide_confirmed: e.target.checked }))} /> I verified that this capability covers the whole selected ICAO type, subject to its original maintenance limitations.</label>}
              <label>Evidence URL<input required type="url" maxLength={2000} value={form.source_url} onChange={e => setForm(f => ({ ...f, source_url: e.target.value }))} placeholder="https://…" /></label>
              <label>Evidence and review notes<textarea required maxLength={4000} rows={4} value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} /></label>
              <label className="mapping-check"><input type="checkbox" checked={form.active} onChange={e => setForm(f => ({ ...f, active: e.target.checked }))} /> Active mapping (uncheck to withdraw; history is retained)</label>
              <button type="submit">{busy ? "Saving…" : "Save mapping"}</button> <button type="button" onClick={() => setForm(blank())}>New mapping / clear form</button>
            </fieldset>
          </form>}
          <details><summary>Previous review revisions ({detail.history.length}{detail.history_truncated ? "+" : ""})</summary>{detail.history.map((h, i) => <article className="saved-mapping" key={i}><strong>{h.snapshot.aircraft_type_code} · revision {h.snapshot.revision}</strong><p>{h.snapshot.match_level} · {h.snapshot.active ? "active" : "withdrawn"} · {h.snapshot.reviewed_by}</p><p>{h.snapshot.notes}</p><small>Archived {h.recorded_at}</small></article>)}</details>
        </>}
      </section>
    </div>
  </main>;
}
