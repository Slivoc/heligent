"use client";
import { useEffect, useState } from 'react';
import { IdentityAssignment } from './IdentityAssignment';
import { AircraftLookup } from './AircraftLookup';
import type { GapReview } from './SourceHub';
type Result = { from: string; to: string; processed_days: number; expected_days: number; has_more: boolean; rows: { address: string; days: number; first_day: string; last_day: string; positions: number; hours: number; types: string[]; callsigns: string[]; categories?:string[];identity_sources?:string[];resolved_registrations?:string[]; top_visits: {airport_ident:string;name:string;visits:number;ground_hours:number}[]; current_registration: string | null }[] };
export function Unidentified({initialReview}:{initialReview?:GapReview}) {
  const [from,setFrom]=useState(initialReview?.from||'');
  const [to,setTo]=useState(initialReview?.to||'');
  const [category,setCategory]=useState(initialReview?'ALL':'ROTORCRAFT');
  const [includeUnknown,setIncludeUnknown]=useState(true);
  const [search,setSearch]=useState('');
  const [region,setRegion]=useState(initialReview?.region||'ALL');
  const [gap,setGap]=useState(initialReview?.gap||'SOURCE_TAIL');
  const [lookupHex,setLookupHex]=useState('');
  const [request,setRequest]=useState({from:initialReview?.from||'',to:initialReview?.to||'',offset:0,category:initialReview?'ALL':'ROTORCRAFT_UNKNOWN',region:initialReview?.region||'ALL',search:'',gap:initialReview?.gap||'SOURCE_TAIL'});
  const [data,setData]=useState<Result|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(true);
  const [selected,setSelected]=useState('');
  const [notice,setNotice]=useState('');
  const [canEdit,setCanEdit]=useState(false);
  useEffect(()=>{
    const c=new AbortController();
    fetch('/api/auth/session',{signal:c.signal}).then(r=>r.ok?r.json():null).then(v=>{if(!c.signal.aborted)setCanEdit(['ADMIN','ANALYST'].includes(v?.user?.role));}).catch(()=>{});
    return ()=>c.abort();
  },[]);
  useEffect(()=>{
    const c=new AbortController();
    const params=new URLSearchParams({offset:String(request.offset),category:request.category,region:request.region,search:request.search,gap:request.gap});
    if(request.from) params.set('from',request.from);
    if(request.to) params.set('to',request.to);
    fetch(`/api/tools/unidentified?${params}`,{signal:c.signal}).then(async r=>{
      const value=await r.json();
      if(!r.ok) throw new Error(value.error || 'Could not load unidentified activity. Try a shorter range.');
      if(!c.signal.aborted){setData(value);setFrom(value.from);setTo(value.to);}
    }).catch(e=>{if(!c.signal.aborted)setError(e.message);}).finally(()=>{if(!c.signal.aborted)setBusy(false);});
    return ()=>c.abort();
  },[request]);
  function submitRequest(next:typeof request){setBusy(true);setError('');setData(null);setRequest(next);}
  function load(){submitRequest({from,to,offset:0,category:category==='ROTORCRAFT'&&includeUnknown?'ROTORCRAFT_UNKNOWN':category,region,search,gap});}
  function includeUnknownResults(){setCategory('ROTORCRAFT');setIncludeUnknown(true);setRegion(request.region);setSearch(request.search);submitRequest({...request,offset:0,category:'ROTORCRAFT_UNKNOWN'});}
  return <section><h2>Aircraft identity review</h2>
    <p>Review missing tails and types in position-bearing daily records. With helicopters and unknowns selected, classified helicopters come first, then unknowns; each group is ranked by estimated airborne hours. Callsigns are clues, not verified registrations.</p>
    <form onSubmit={e=>{e.preventDefault();load();}}>
      <label>From (UTC)<input type="date" value={from} onChange={e=>setFrom(e.target.value)} disabled={busy}/></label>
      <label>To (UTC)<input type="date" value={to} onChange={e=>setTo(e.target.value)} disabled={busy}/></label>
      <label>Identity gap<select value={gap} onChange={e=>setGap(e.target.value)} disabled={busy}><option value="SOURCE_TAIL">Original ADS-B tail missing</option><option value="TAIL">Tail still unresolved</option><option value="TYPE">ICAO type missing</option><option value="BOTH">Tail and type missing</option></select></label>
      <label>Aircraft<select value={category} onChange={e=>setCategory(e.target.value)} disabled={busy}><option value="ROTORCRAFT">Helicopters</option><option value="UNKNOWN">Unknown types only</option><option value="FIXED_WING">Fixed wing only</option><option value="ALL">All aircraft</option></select></label>
      {category==='ROTORCRAFT'&&<label className="tools-check unidentified-unknown"><input type="checkbox" checked={includeUnknown} onChange={e=>setIncludeUnknown(e.target.checked)} disabled={busy}/><span>Include unknown types<small>Missing tails often also have no aircraft type. Unknowns may include fixed wing.</small></span></label>}
      <label>Observed region<select value={region} onChange={e=>setRegion(e.target.value)} disabled={busy}>{[['ALL','Worldwide'],['EU','Europe'],['GB','United Kingdom'],['NA','North America'],['SA','South America'],['AF','Africa'],['AS','Asia'],['OC','Oceania'],['AN','Antarctica'],['UNLOCATED','No airport region']].map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label>
      <label>Hex or callsign<input type="search" value={search} onChange={e=>setSearch(e.target.value)} maxLength={40} placeholder="e.g. 4082A2 or G-WSAS" disabled={busy}/></label>
      <button disabled={busy}>{busy?'Loading…':'Apply filters'}</button>
    </form>
    <p>Defaults to the latest processed week; maximum 31 days. Analysts and administrators can assign verified identities using a dated preview and confirmation.</p>
    <p>Helicopter filtering uses resolved category, including dated national register evidence even when the original type is missing. Unknown categories stay visible by default. Community lookup candidates appear for review and do not change these filters. Search matches hex addresses or callsign clues across all pages; spaces and hyphens are ignored. Region selects days with an airport visit there; daily hours are not time spent solely in that region.</p>
    {notice&&<p role="status">{notice}</p>}
    {selected&&<IdentityAssignment key={selected} address={selected} onCancel={()=>setSelected('')} onSaved={message=>{setSelected('');setNotice(message);load();}}/>}
    {lookupHex&&<><button onClick={()=>setLookupHex('')}>Close cross-reference</button><AircraftLookup key={lookupHex} initialAddress={lookupHex} canEdit={canEdit}/></>}
    {error && <p role="alert" className="map-warning">{error}</p>}
    {data && <><p>{data.from} to {data.to} · {data.processed_days}/{data.expected_days} dates processed · Page {request.offset/50+1}</p>
      {data.processed_days<data.expected_days && <p className="map-warning">Some dates are not processed. Totals are incomplete; even processed dates can have receiver gaps.</p>}
      <p>Totals cover only days matching the selected identity gap, with recorded positions. Resolved tails show dated registry results; current registration is shown separately. Up to 20 distinct callsigns are shown per hex.</p>
      {request.category==='ROTORCRAFT'&&<p className="map-warning">{!data.rows.length?'No classified helicopters match these filters. ':''}Aircraft with an unknown type are excluded, even if they are helicopters. <button disabled={busy} onClick={includeUnknownResults}>Include unknown types</button></p>}
      {!data.rows.length && request.category!=='ROTORCRAFT' && <p>No unidentified position-bearing aircraft match these filters. Try another callsign, region or date range. This does not establish complete identity coverage.</p>}
      <div className="unidentified-table"><table><thead><tr><th>Hex</th><th>Estimated hours</th><th>Positions / days</th><th>First / last day</th><th>Recorded types / resolved category</th><th>Callsign clues</th><th>Top airport visits</th><th>Current registration</th><th>Review</th></tr></thead><tbody>{data.rows.map(r=><tr key={r.address}><td><strong>{r.address.toUpperCase()}</strong></td><td>{Number(r.hours).toFixed(2)}</td><td>{Number(r.positions).toLocaleString()} / {r.days}</td><td>{r.first_day}<br/>{r.last_day}</td><td>{r.types.join(', ')||'Unknown'}<br/><strong>{r.categories?.join(', ')||'Unknown category'}</strong><small>{r.identity_sources?.join(', ')||'ADS-B type reference'}</small></td><td>{r.callsigns.join(', ')||'None recorded'}</td><td>{r.top_visits?.length ? r.top_visits.map(v=><div key={v.airport_ident}><strong>{v.airport_ident}</strong> · {v.name}<br/>{v.visits} visit records · {Number(v.ground_hours).toFixed(1)}h observed ground</div>) : 'No recorded airport visits'}</td><td>{r.current_registration||'Unresolved'}{!!r.resolved_registrations?.length&&<small>Resolved: {r.resolved_registrations.join(', ')}</small>}</td><td><button onClick={()=>setLookupHex(r.address)}>Cross-reference</button>{canEdit ? <button onClick={()=>{setNotice('');setSelected(r.address);}}>Assign tail number</button> : 'Read-only'}</td></tr>)}</tbody></table></div>
      <nav aria-label="Unidentified aircraft pages"><button disabled={busy||request.offset===0} onClick={()=>submitRequest({...request,offset:Math.max(0,request.offset-50)})}>Previous</button><button disabled={busy||!data.has_more} onClick={()=>submitRequest({...request,offset:request.offset+50})}>Next</button></nav>
    </>}
  </section>;
}
