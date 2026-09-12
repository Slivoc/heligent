"use client";
import { useState } from 'react';

export type Lookup = { id:number; address:string; source_code:string; status:string; fetched_at:string; source_url:string; cached:boolean; result:{registration?:string;type_code?:string;model?:string;manufacturer?:string;category?:string;error?:string} };

export function AircraftLookup({ provider: fixedProvider, initialAddress = '', canEdit }: { provider?:string; initialAddress?:string; canEdit:boolean }) {
  const [provider,setProvider] = useState(fixedProvider || 'ADSBDB');
  const [address,setAddress] = useState(initialAddress);
  const [result,setResult] = useState<Lookup|null>(null);
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  async function lookup() {
    setBusy(true);setError('');setResult(null);
    try {
      const r = await fetch(`/api/tools/sources/${provider}/lookup`, {method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'HeligentAdmin'},body:JSON.stringify({address:address.trim()})});
      const value = await r.json();
      if(!r.ok)throw new Error(value.error || 'Lookup failed');
      setResult(value);
    } catch(e) {setError(e instanceof Error ? e.message : String(e));} finally {setBusy(false);}
  }
  return <section className="tools-card aircraft-lookup" aria-label="Aircraft cross-reference">
    <h3>Cross-reference one hex</h3>
    <p>Current source claims for review. A lookup does not change aircraft identity or establish an assignment for an earlier flight.</p>
    <form onSubmit={e=>{e.preventDefault();void lookup();}}>
      {!fixedProvider && <label>Lookup source<select value={provider} disabled={busy} onChange={e=>{setProvider(e.target.value);setResult(null);setError('');}}><option value="ADSBDB">ADSBdb</option><option value="HEXDB">HexDB</option></select></label>}
      <label>Six-character hex<input value={address} pattern="[0-9a-fA-F]{6}" maxLength={6} required disabled={busy} onChange={e=>{setAddress(e.target.value);setResult(null);}} placeholder="4082A2" /></label>
      <button disabled={busy || !canEdit}>{busy?'Looking up…':`Look up in ${provider==='ADSBDB'?'ADSBdb':'HexDB'}`}</button>
    </form>
    {!canEdit && <p>An analyst or administrator can request lookups.</p>}
    <small>One request at a time. Results and misses cached for 30 days; errors for 5 minutes. Up to 60 new lookups per source per day, five seconds apart. No background scan.</small>
    {error && <p role="alert" className="map-warning">{error}</p>}
    {result && <div role="status" className={`lookup-result ${result.status==='FAILED'?'map-warning':''}`}>
      <strong>{result.status==='FOUND'?'Source record found':result.status==='NOT_FOUND'?'No record found': 'Lookup failed'} · {result.address.toUpperCase()}</strong>
      {result.status==='FOUND' && <dl className="source-facts"><div><dt>Tail</dt><dd>{result.result.registration || 'Missing'}</dd></div><div><dt>ICAO type</dt><dd>{result.result.type_code || 'Missing'}</dd></div><div><dt>Category from Heligent type reference</dt><dd>{result.result.category || 'Unknown'}</dd></div><div><dt>Model</dt><dd>{result.result.model || 'Missing'}</dd></div></dl>}
      {result.status==='NOT_FOUND' && <p>This source has no record for the hex. That does not identify its aircraft category.</p>}
      {result.result.error && <p>{result.result.error}</p>}
      <p><a href={result.source_url} target="_blank" rel="noreferrer">Source response</a> · fetched {new Date(result.fetched_at).toLocaleString()} · {result.cached?'Cached result':'New lookup'} · evidence #{result.id}</p>
      <small>Per-record source revision date: unknown. Compare with dated register evidence before assigning a tail or type.</small>
    </div>}
  </section>;
}
