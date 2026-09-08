"use client";
import { useEffect, useState } from 'react';
import './tools.css';
import { Unidentified } from './Unidentified';

type Preview = { preview_id:string; query: string; fetched_at: string; source_url: string; source_sha256: string; page: string; truncated: boolean; organisations: { name: string; approval: string; sites: { street: string; locality: string; ratings: { wording: string; models: string[] }[] }[] }[] };
type ImportRecord = {id:number;finished_at:string;site_count:number;capability_count:number;metadata:{organisation:string;reviewed_by:string}};

export function Tools({ onMappings, initialLba = false }: { onMappings: () => void; initialLba?: boolean }) {
  const [tab, setTab] = useState(initialLba ? 'lba' : 'imports');
  const [query, setQuery] = useState('ADAC');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [canEdit, setCanEdit] = useState(false);
  const [baseOnly, setBaseOnly] = useState(false);
  const [chosen,setChosen] = useState<number|null>(null);
  const [notice,setNotice] = useState('');
  const [history,setHistory] = useState<ImportRecord[]>([]);
  const [historyError,setHistoryError] = useState('');
  const [historyRevision,setHistoryRevision] = useState(0);
  useEffect(()=>{
    const c=new AbortController();
    fetch('/api/tools/lba/history',{signal:c.signal}).then(async r=>{if(!r.ok)throw new Error('Import history could not load');return r.json();})
      .then(v=>{if(!c.signal.aborted){setHistory(v);setHistoryError('');}}).catch(e=>{if(!c.signal.aborted)setHistoryError(e.message);});
    return ()=>c.abort();
  },[historyRevision]);
  useEffect(() => {
    const c = new AbortController();
    fetch('/api/auth/session', {signal:c.signal}).then(r => r.ok ? r.json() : null)
      .then(v => { if (!c.signal.aborted) setCanEdit(['ADMIN','ANALYST'].includes(v?.user?.role)); }).catch(() => {});
    return () => c.abort();
  }, []);
  async function fetchPreview() {
    setBusy(true); setError(''); setPreview(null); setChosen(null); setNotice('');
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
  async function importChosen() {
    if(chosen===null || !preview)return;
    setBusy(true);setError('');setNotice('');
    try {
      const r=await fetch('/api/tools/lba/import',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'HeligentAdmin'},body:JSON.stringify({preview_id:preview.preview_id,organisation_index:chosen,confirmed:true})});
      const result=await r.json();
      if(!r.ok)throw new Error(result.error || 'Import failed; no partial import should be assumed. Check history before retrying.');
      setNotice(result.already_imported ? `${result.company_name}: this exact organisation snapshot was already imported (batch ${result.batch_id}).` : `${result.company_name} imported: ${result.sites} sites, ${result.capabilities_added} capabilities added (batch ${result.batch_id}). Link new sites to airports and review type mappings to use them on the Stops map.`);
      setChosen(null);setHistoryRevision(n=>n+1);
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  return <main className="tools-page">
    <p className="eyebrow">Tools · Data stewardship</p><h1>Data imports & review</h1>
    <p>A shared home for source adapters, repeatable updates and data-quality tools.</p>
    <nav aria-label="Tools sections"><button onClick={() => setTab('imports')} aria-pressed={tab === 'imports'}>Data imports</button><button onClick={onMappings}>Capability mappings</button></nav>
    <button onClick={() => setTab('unidentified')} aria-pressed={tab === 'unidentified'}>Hexes without tail numbers</button>
    {tab === 'unidentified' ? <Unidentified /> : tab === 'imports' ? <>
      <section className="tools-card"><span className="eyebrow">First adapter · Selected organisation import</span><h2>LBA technical organisations</h2><p>Inspect published approvals, operating sites, ratings and model wording. Start with ADAC Heliservice.</p><button onClick={() => setTab('lba')}>Open LBA preview</button></section>
      <section className="tools-card"><h2>Recent LBA imports</h2>{historyError && <p role="alert">{historyError}</p>}{!history.length && !historyError && <p>No completed LBA imports.</p>}{history.map(h=><p key={h.id}><strong>{h.metadata.organisation}</strong> · batch {h.id} · {h.site_count} sites · {h.capability_count} capabilities added<br/>{h.finished_at} · {h.metadata.reviewed_by}</p>)}<p>Latest 25 completed imports. Changed repeat imports need a later update/merge workflow; existing records are not overwritten.</p></section>
    </> : <section>
      <button onClick={() => setTab('imports')}>Back to data imports</button><h2>LBA · Fetch and preview</h2>
      <p><a href="https://iauskunft.lba.de/tb/" target="_blank" rel="noreferrer">Open the LBA source directory</a></p>
      <p className="map-warning">Fetching saves a preview, not catalogue changes. Import only the organisation you choose and confirm. Existing fields are preserved; exact approval numbers or company names are used for matching. Publication is consent-based; absent results do not establish absence of approval.</p>
      <form onSubmit={e => {e.preventDefault(); void fetchPreview();}}><label>Organisation name<input value={query} onChange={e => setQuery(e.target.value)} minLength={3} maxLength={100} required disabled={busy} /></label><button disabled={busy || !canEdit}>{busy ? 'Fetching LBA…' : 'Fetch preview'}</button></form>
      {!canEdit && <p>An analyst or administrator account is required to fetch a preview.</p>}
      <p>One result page per request, at most once every 30 seconds per app process. Nothing runs on a schedule.</p>
      {error && <p role="alert" className="map-warning">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {chosen!==null && preview && <section className="tools-card" aria-label="Confirm LBA import"><h3>Import {preview.organisations[chosen].name}?</h3><p>This adds the approval and all {preview.organisations[chosen].sites.length} sites with their published capabilities, including line-only sites hidden by the display filter. It does not infer aircraft codes, coordinates or airport links. Existing fields will not be overwritten.</p><button disabled={busy} onClick={()=>void importChosen()}>{busy?'Importing…':'Confirm organisation import'}</button> <button disabled={busy} onClick={()=>setChosen(null)}>Cancel import</button></section>}
      {preview && <>
        <section className="tools-card"><h3>Fetched preview: {preview.query}</h3><p>{preview.organisations.length} organisations · {preview.organisations.reduce((n,o)=>n+o.sites.length,0)} sites · source page {preview.page}</p><p>Fetched {preview.fetched_at}</p><small>Response SHA-256: {preview.source_sha256}</small><button onClick={download}>Download preview JSON</button></section>
        {preview.truncated && <p role="alert" className="map-warning">More results exist. This is not the full directory: narrow the organisation name before proceeding.</p>}
        <label className="tools-check"><input type="checkbox" checked={baseOnly} onChange={e => setBaseOnly(e.target.checked)} /> Show sites with base maintenance wording only</label>
        <p>Original rating and model wording is retained. No ICAO type mapping or airport coordinates are inferred.</p>
        {preview.organisations.map((o,i) => <section className="tools-card" key={i}><h3>{o.name}</h3><p>{o.approval}</p><button disabled={busy || !canEdit || preview.truncated} onClick={()=>{setChosen(i);setError('');setNotice('');}}>Import this organisation</button>{o.sites.filter(s => !baseOnly || s.ratings.some(r => /Base/i.test(r.wording))).map((s,j)=><details key={j}><summary>{s.street} · {s.locality} ({s.ratings.length} ratings)</summary>{s.ratings.map((r,k)=><article key={k}><h4>{r.wording}</h4><ul>{r.models.map((m,l)=><li key={l}>{m}</li>)}</ul></article>)}</details>)}</section>)}
      </>}
    </section>}
  </main>;
}
