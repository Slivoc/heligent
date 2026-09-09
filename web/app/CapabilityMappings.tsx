"use client";
import { FormEvent, useEffect, useState } from "react";
import "./capability-mappings.css";
import { CapabilityWording } from './CapabilityWording';

type Summary = { id: number; company_name: string; site_name: string | null; approval_number: string; approval_status: string; model: string | null; aircraft_type_code: string | null; limitation: string | null; mapping_count: number; shared_mapping_count: number };
type Mapping = { id: number; aircraft_type_code: string; match_level: string; variant_scope: string; source_url: string; notes: string; revision: number; active: boolean; stale: boolean; reviewed_by: string; reviewed_at: string; evidence_snapshot: Record<string, unknown> };
type Detail = { model_phrase: string; shared_mappings: Mapping[]; shared_history: { snapshot: Mapping; recorded_at: string }[]; shared_history_truncated: boolean; capability: Summary & { active: boolean; company_active: boolean; site_active: boolean | null; is_base_maintenance: boolean; is_line_maintenance: boolean }; source_snapshot: Record<string, unknown>; mappings: Mapping[]; history: { snapshot: Mapping; recorded_at: string }[]; history_truncated: boolean };
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
  const [scope, setScope] = useState<"shared" | "local">("shared");
  const [sharedConfirmed, setSharedConfirmed] = useState(false);
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
    api<Detail>(`/${selection.id}`, controller.signal).then(value => { setDetail(value); setScope("shared"); setSharedConfirmed(false); setForm(blank(value.capability.aircraft_type_code || "")); })
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
      await api(`/${detail.capability.id}${scope === "shared" ? "/shared" : ""}`, undefined, { ...form, shared_confirmed: sharedConfirmed, evidence_snapshot: detail.source_snapshot });
      const updated = await api<Detail>(`/${detail.capability.id}`);
      setDetail(updated); setForm(blank()); setSharedConfirmed(false); setNotice("Mapping saved. Reload the Stops map to use the updated review.");
      setSearch(previous => ({ ...previous }));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  const editable = canEdit && detail?.capability.active && detail.capability.company_active && detail.capability.site_active !== false;
  return <main className="capability-review-page">
    <p className="eyebrow">Maintenance intelligence · Capability review</p><h1>Connect capability wording to aircraft types</h1>
    <p>Map aircraft wording once to reuse it across entries with the same wording. Each site keeps its own approval, maintenance scope and limitations. Site-specific reviews take precedence.</p>
    <form className="capability-search" onSubmit={e => { e.preventDefault(); setResults(null); setError(""); setSearch({ query, offset: 0 }); }}>
      <label>Search company, approval or capability<input value={query} onChange={e => setQuery(e.target.value)} maxLength={200} placeholder="Airbus, BK117, UK.145.00124…" /></label><button disabled={busy}>Search</button>
    </form>
    {error && <p className="map-warning" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="capability-review-layout">
      <section className="capability-results"><h2>Imported aircraft capabilities</h2>
        {!results && <p>Loading capabilities…</p>}{results?.rows.length === 0 && <p>No matching active aircraft capabilities.</p>}
        {results?.rows.map(c => <button type="button" key={c.id} disabled={busy} aria-pressed={selection.id === c.id} onClick={() => open(c.id)}><strong>{c.company_name}</strong><span>{c.site_name || "Company-wide scope"} · {c.approval_number} · {c.approval_status}</span><CapabilityWording capability={c} /><small>{c.mapping_count} site-specific · {c.shared_mapping_count} shared active mappings</small></button>)}
        <div className="capability-pagination"><button disabled={busy || search.offset === 0} onClick={() => { setResults(null); setSearch(s => ({ ...s, offset: Math.max(0, s.offset - 50) })); }}>Previous</button><span>Page {search.offset / 50 + 1}</span><button disabled={busy || !results?.has_more} onClick={() => { setResults(null); setSearch(s => ({ ...s, offset: s.offset + 50 })); }}>Next</button></div>
      </section>
      <section className="capability-editor">
        {!detail ? <p>{selection.id ? "Loading selected capability…" : "Select a capability to review its type mappings."}</p> : <>
          <h2>{detail.capability.company_name}</h2><p>{detail.capability.site_name || "Company-wide scope — not confirmed at individual bases"} · {detail.capability.approval_number} · {detail.capability.approval_status}</p>
          <h3>Original capability wording</h3><CapabilityWording capability={detail.capability} />
          <p>Base maintenance: {detail.capability.is_base_maintenance ? "recorded" : "not recorded"} · Line maintenance: {detail.capability.is_line_maintenance ? "recorded" : "not recorded"}</p>
          <p className="map-note">These flags and limitations remain in force. A mapping is not proof of suitability for a particular check or aircraft variant.</p>
          <button type="button" disabled={busy} onClick={() => open(detail.capability.id)}>Reload capability (discard unsaved edits)</button>
          <h3>Site-specific overrides</h3>{detail.mappings.length === 0 && <p>No interpretations saved yet.</p>}
          {detail.mappings.map(m => <article className="saved-mapping" key={m.id}>
            <strong>{m.aircraft_type_code} · {m.match_level === "POSSIBLE_FAMILY" ? "Possible family match" : "Reviewed type mapping"}{!m.active ? " · WITHDRAWN" : ""}</strong>
            <p>{m.variant_scope ? `Restricted variants: ${m.variant_scope}. The map remains amber until aircraft variant is verified.` : "No variant restriction recorded"}</p>
            {m.stale && <p className="map-warning">Imported scope changed. This mapping needs re-review and cannot produce a confirmed match. Previous wording: {String(m.evidence_snapshot.limitation || m.evidence_snapshot.model || "not recorded")}</p>}
            <p>{m.notes}</p><a href={m.source_url} target="_blank" rel="noreferrer">Evidence source ↗</a><small>Reviewed by {m.reviewed_by} · {m.reviewed_at} · revision {m.revision}</small>
            {editable && <button type="button" disabled={busy} onClick={() => { setScope("local"); setSharedConfirmed(false); setForm({ aircraft_type_code: m.aircraft_type_code, match_level: m.match_level, variant_scope: m.variant_scope, source_url: m.source_url, notes: m.notes, revision: m.revision, active: m.active, type_wide_confirmed: false }); setNotice(""); }}>Edit / re-review / withdraw {m.aircraft_type_code}</button>}
          </article>)}
          <h3>Shared wording mappings</h3>
          <p>Exact phrase (ignoring case and spacing):</p><blockquote>{detail.model_phrase || "No wording available"}</blockquote>
          {detail.shared_mappings.length === 0 && <p>No shared mappings for this phrase yet.</p>}
          {detail.shared_mappings.map(m => <article className="saved-mapping" key={m.id}>
            <strong>{m.aircraft_type_code} · {m.match_level === "POSSIBLE_FAMILY" ? "Possible family match" : "Reviewed type mapping"}{!m.active ? " · WITHDRAWN" : ""}</strong>
            <p>{m.variant_scope ? `Restricted variants: ${m.variant_scope}. Match remains amber.` : "No variant restriction recorded"}</p>
            {detail.mappings.some(local => local.aircraft_type_code === m.aircraft_type_code) && <p className="map-warning">A site-specific review overrides this shared rule here, including if that review is withdrawn or needs re-review.</p>}
            <p>{m.notes}</p><a href={m.source_url} target="_blank" rel="noreferrer">Evidence source ↗</a>
            <small>Reviewed by {m.reviewed_by} · {m.reviewed_at} · revision {m.revision}</small>
            {editable && <button type="button" disabled={busy} onClick={() => { setScope("shared"); setSharedConfirmed(false); setForm({ aircraft_type_code: m.aircraft_type_code, match_level: m.match_level, variant_scope: m.variant_scope, source_url: m.source_url, notes: m.notes, revision: m.revision, active: m.active, type_wide_confirmed: false }); setNotice(""); }}>Edit / withdraw shared {m.aircraft_type_code}</button>}
          </article>)}
          {!editable ? <p>Read-only. An analyst or administrator can review active capabilities.</p> : <form onSubmit={save} className="mapping-form">
            <h3>{form.revision ? `Review ${form.aircraft_type_code}` : "Add a mapping"}</h3>
            <p className="map-note">For broad “MBB-BK117 SERIES” wording and EC45, choose Possible family match unless the precise scope has been verified. Shared rules reuse this exact phrase only; they never widen a facility’s maintenance scope.</p>
            <fieldset disabled={busy}>
              <label>Apply mapping to<select value={scope} onChange={e => { setScope(e.target.value as "shared" | "local"); setForm(blank()); setSharedConfirmed(false); }}><option value="shared">All entries with this exact wording</option><option value="local">This capability only (override)</option></select></label>
              {scope === "shared" && <label className="mapping-check"><input type="checkbox" required checked={sharedConfirmed} onChange={e => setSharedConfirmed(e.target.checked)} /> Apply this review to all current and future entries with the exact phrase shown above.</label>}
              <label>ICAO aircraft type<input required pattern="[A-Za-z0-9]{2,4}" maxLength={4} value={form.aircraft_type_code} disabled={form.revision > 0} onChange={e => setForm(f => ({ ...f, aircraft_type_code: e.target.value.toUpperCase() }))} placeholder="EC45" /></label>
              <label>Match level<select value={form.match_level} onChange={e => setForm(f => ({ ...f, match_level: e.target.value, type_wide_confirmed: false }))}><option value="POSSIBLE_FAMILY">Possible family match — amber</option><option value="REVIEWED_TYPE">Reviewed type / variant mapping</option></select></label>
              <label>Restricted variants (if applicable)<input maxLength={1000} value={form.variant_scope} onChange={e => setForm(f => ({ ...f, variant_scope: e.target.value, type_wide_confirmed: false }))} placeholder="e.g. MBB-BK117 C-2 only" /></label>
              {form.match_level === "REVIEWED_TYPE" && !form.variant_scope.trim() && <label className="mapping-check"><input type="checkbox" required checked={form.type_wide_confirmed} onChange={e => setForm(f => ({ ...f, type_wide_confirmed: e.target.checked }))} /> I verified that this capability covers the whole selected ICAO type, subject to its original maintenance limitations.</label>}
              <label>Evidence URL<input required type="url" maxLength={2000} value={form.source_url} onChange={e => setForm(f => ({ ...f, source_url: e.target.value }))} placeholder="https://…" /></label>
              <label>Evidence and review notes<textarea required maxLength={4000} rows={4} value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} /></label>
              <label className="mapping-check"><input type="checkbox" checked={form.active} onChange={e => setForm(f => ({ ...f, active: e.target.checked }))} /> Active mapping (uncheck to withdraw; history is retained)</label>
              <button type="submit">{busy ? "Saving…" : "Save mapping"}</button> <button type="button" onClick={() => { setForm(blank()); setSharedConfirmed(false); }}>New mapping / clear form</button>
            </fieldset>
          </form>}
          <details><summary>Shared rule history ({detail.shared_history.length}{detail.shared_history_truncated ? "+" : ""})</summary>{detail.shared_history.map((h, i) => <article className="saved-mapping" key={i}><strong>{h.snapshot.aircraft_type_code} · revision {h.snapshot.revision}</strong><p>{h.snapshot.match_level} · {h.snapshot.active ? "active" : "withdrawn"} · {h.snapshot.reviewed_by}</p><p>{h.snapshot.notes}</p><small>Archived {h.recorded_at}</small></article>)}</details>
          <details><summary>Previous review revisions ({detail.history.length}{detail.history_truncated ? "+" : ""})</summary>{detail.history.map((h, i) => <article className="saved-mapping" key={i}><strong>{h.snapshot.aircraft_type_code} · revision {h.snapshot.revision}</strong><p>{h.snapshot.match_level} · {h.snapshot.active ? "active" : "withdrawn"} · {h.snapshot.reviewed_by}</p><p>{h.snapshot.notes}</p><small>Archived {h.recorded_at}</small></article>)}</details>
        </>}
      </section>
    </div>
  </main>;
}
