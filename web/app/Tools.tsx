"use client";
import { useEffect, useState } from 'react';
import './tools.css';

type Preview = { query: string; fetched_at: string; source_url: string; source_sha256: string; page: string; truncated: boolean; organisations: { name: string; approval: string; sites: { street: string; locality: string; ratings: { wording: string; models: string[] }[] }[] }[] };

export function Tools({ onMappings, initialLba = false }: { onMappings: () => void; initialLba?: boolean }) {
  const [tab, setTab] = useState(initialLba ? 'lba' : 'imports');
  const [query, setQuery] = useState('ADAC');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [canEdit, setCanEdit] = useState(false);
  const [baseOnly, setBaseOnly] = useState(false);
  useEffect(() => {
    const c = new AbortController();
    fetch('/api/auth/session', {signal:c.signal}).then(r => r.ok ? r.json() : null)
      .then(v => { if (!c.signal.aborted) setCanEdit(['ADMIN','ANALYST'].includes(v?.user?.role)); }).catch(() => {});
    return () => c.abort();
  }, []);
  async function fetchPreview() {
    setBusy(true); setError(''); setPreview(null);
    try {
      const r = await fetch('/api/tools/lba/preview', {method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'HeligentAdmin'},body:JSON.stringify({query})});
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || 'Preview failed');
      setPreview(data);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(preview,null,2)],{type:'application/json'}));
    const link = document.createElement('a'); link.href=url; link.download='lba-preview.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  return <main className="tools-page">
    <p className="eyebrow">Tools · Data stewardship</p><h1>Data imports & review</h1>
    <p>A shared home for source adapters, repeatable updates and data-quality tools.</p>
    <nav aria-label="Tools sections"><button onClick={() => setTab('imports')} aria-pressed={tab === 'imports'}>Data imports</button><button onClick={onMappings}>Capability mappings</button></nav>
    {tab === 'imports' ? <>
      <section className="tools-card"><span className="eyebrow">First adapter · Preview available</span><h2>LBA technical organisations</h2><p>Inspect published approvals, operating sites, ratings and model wording. Start with ADAC Heliservice.</p><button onClick={() => setTab('lba')}>Open LBA preview</button></section>
      <section className="tools-card"><h2>Import history</h2><p>No persistent import-run history is implemented in this first pass. LBA previews do not write to the catalogue. Download a preview to retain it.</p><p>Next: saved staging runs, duplicate/conflict review, approved imports and repeat-import comparisons.</p></section>
    </> : <section>
      <button onClick={() => setTab('imports')}>Back to data imports</button><h2>LBA · Fetch and preview</h2>
      <p><a href="https://iauskunft.lba.de/tb/" target="_blank" rel="noreferrer">Open the LBA source directory</a></p>
      <p className="map-warning">Preview only: no companies, sites or capabilities will be created or overwritten. Publication is consent-based; absent results do not establish absence of approval.</p>
      <form onSubmit={e => {e.preventDefault(); void fetchPreview();}}><label>Organisation name<input value={query} onChange={e => setQuery(e.target.value)} minLength={3} maxLength={100} required disabled={busy} /></label><button disabled={busy || !canEdit}>{busy ? 'Fetching LBA…' : 'Fetch preview'}</button></form>
      {!canEdit && <p>An analyst or administrator account is required to fetch a preview.</p>}
      <p>One result page per request, at most once every 30 seconds per app process. Nothing runs on a schedule.</p>
      {error && <p role="alert" className="map-warning">{error}</p>}
      {preview && <>
        <section className="tools-card"><h3>Fetched preview: {preview.query}</h3><p>{preview.organisations.length} organisations · {preview.organisations.reduce((n,o)=>n+o.sites.length,0)} sites · source page {preview.page}</p><p>Fetched {preview.fetched_at}</p><small>Response SHA-256: {preview.source_sha256}</small><button onClick={download}>Download preview JSON</button></section>
        {preview.truncated && <p role="alert" className="map-warning">More results exist. This is not the full directory: narrow the organisation name before proceeding.</p>}
        <label className="tools-check"><input type="checkbox" checked={baseOnly} onChange={e => setBaseOnly(e.target.checked)} /> Show sites with base maintenance wording only</label>
        <p>Original rating and model wording is retained. No ICAO type mapping or airport coordinates are inferred.</p>
        {preview.organisations.map((o,i) => <section className="tools-card" key={i}><h3>{o.name}</h3><p>{o.approval}</p>{o.sites.filter(s => !baseOnly || s.ratings.some(r => /Base/i.test(r.wording))).map((s,j)=><details key={j}><summary>{s.street} · {s.locality} ({s.ratings.length} ratings)</summary>{s.ratings.map((r,k)=><article key={k}><h4>{r.wording}</h4><ul>{r.models.map((m,l)=><li key={l}>{m}</li>)}</ul></article>)}</details>)}</section>)}
      </>}
    </section>}
  </main>;
}
