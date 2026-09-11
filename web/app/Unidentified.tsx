"use client";
import { useEffect, useState } from 'react';
import { IdentityAssignment } from './IdentityAssignment';
type Result = { from: string; to: string; processed_days: number; expected_days: number; has_more: boolean; rows: { address: string; days: number; first_day: string; last_day: string; positions: number; hours: number; types: string[]; callsigns: string[]; top_visits: {airport_ident:string;name:string;visits:number;ground_hours:number}[]; current_registration: string | null }[] };
export function Unidentified() {
  const [from,setFrom]=useState('');
  const [to,setTo]=useState('');
  const [category,setCategory]=useState('ROTORCRAFT');
  const [includeUnknown,setIncludeUnknown]=useState(true);
  const [search,setSearch]=useState('');
  const [region,setRegion]=useState('ALL');
  const [request,setRequest]=useState({from:'',to:'',offset:0,category:'ROTORCRAFT_UNKNOWN',region:'ALL',search:''});
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
    const params=new URLSearchParams({offset:String(request.offset),category:request.category,region:request.region,search:request.search});
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
  function load(){submitRequest({from,to,offset:0,category:category==='ROTORCRAFT'&&includeUnknown?'ROTORCRAFT_UNKNOWN':category,region,search});}
  function includeUnknownResults(){setCategory('ROTORCRAFT');setIncludeUnknown(true);setRegion(request.region);setSearch(request.search);submitRequest({...request,offset:0,category:'ROTORCRAFT_UNKNOWN'});}
  return <section><h2>Hex addresses without tail numbers</h2>
    <p>Aircraft with positions but a blank registration in their historical daily records. Ranked by estimated airborne hours, highest first. Callsigns are clues, not verified registrations.</p>
    <form onSubmit={e=>{e.preventDefault();load();}}>
      <label>From (UTC)<input type="date" value={from} onChange={e=>setFrom(e.target.value)} disabled={busy}/></label>
      <label>To (UTC)<input type="date" value={to} onChange={e=>setTo(e.target.value)} disabled={busy}/></label>
      <label>Aircraft<select value={category} onChange={e=>setCategory(e.target.value)} disabled={busy}><option value="ROTORCRAFT">Helicopters</option><option value="UNKNOWN">Unknown types only</option><option value="FIXED_WING">Fixed wing only</option><option value="ALL">All aircraft</option></select></label>
      {category==='ROTORCRAFT'&&<label className="tools-check unidentified-unknown"><input type="checkbox" checked={includeUnknown} onChange={e=>setIncludeUnknown(e.target.checked)} disabled={busy}/><span>Include unknown types<small>Missing tails often also have no aircraft type. Unknowns may include fixed wing.</small></span></label>}
      <label>Observed region<select value={region} onChange={e=>setRegion(e.target.value)} disabled={busy}>{[['ALL','Worldwide'],['EU','Europe'],['GB','United Kingdom'],['NA','North America'],['SA','South America'],['AF','Africa'],['AS','Asia'],['OC','Oceania'],['AN','Antarctica']].map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label>
      <label>Hex or callsign<input type="search" value={search} onChange={e=>setSearch(e.target.value)} maxLength={40} placeholder="e.g. 4082A2 or G-WSAS" disabled={busy}/></label>
      <button disabled={busy}>{busy?'Loading…':'Apply filters'}</button>
    </form>
    <p>Defaults to the latest processed week; maximum 31 days. Analysts and administrators can assign verified identities using a dated preview and confirmation.</p>
    <p>Helicopter filtering uses recorded type classification, not a guess from callsigns. Unknown types are included by default so unclassified helicopters remain discoverable. Search matches hex addresses or callsign clues across all pages; spaces and hyphens are ignored. Region selects days with an airport visit there; daily hours are not time spent solely in that region. Top visits are airport sightings, not confirmed maintenance-base visits.</p>
    {notice&&<p role="status">{notice}</p>}
    {selected&&<IdentityAssignment key={selected} address={selected} onCancel={()=>setSelected('')} onSaved={message=>{setSelected('');setNotice(message);load();}}/>}
    {error && <p role="alert" className="map-warning">{error}</p>}
    {data && <><p>{data.from} to {data.to} · {data.processed_days}/{data.expected_days} dates processed · Page {request.offset/50+1}</p>
      {data.processed_days<data.expected_days && <p className="map-warning">Some dates are not processed. Totals are incomplete; even processed dates can have receiver gaps.</p>}
      <p>Totals cover only days with missing registration and recorded positions. A current registration below means historical records still need review. Up to 20 distinct callsigns are shown per hex.</p>
      {request.category==='ROTORCRAFT'&&<p className="map-warning">{!data.rows.length?'No classified helicopters match these filters. ':''}Aircraft with an unknown type are excluded, even if they are helicopters. <button disabled={busy} onClick={includeUnknownResults}>Include unknown types</button></p>}
      {!data.rows.length && request.category!=='ROTORCRAFT' && <p>No unidentified position-bearing aircraft match these filters. Try another callsign, region or date range. This does not establish complete identity coverage.</p>}
      <div className="unidentified-table"><table><thead><tr><th>Hex</th><th>Estimated hours</th><th>Positions / days</th><th>First / last day</th><th>Recorded types</th><th>Callsign clues</th><th>Top airport visits</th><th>Current registration</th><th>Review</th></tr></thead><tbody>{data.rows.map(r=><tr key={r.address}><td><strong>{r.address.toUpperCase()}</strong></td><td>{Number(r.hours).toFixed(2)}</td><td>{Number(r.positions).toLocaleString()} / {r.days}</td><td>{r.first_day}<br/>{r.last_day}</td><td>{r.types.join(', ')||'Unknown'}</td><td>{r.callsigns.join(', ')||'None recorded'}</td><td>{r.top_visits?.length ? r.top_visits.map(v=><div key={v.airport_ident}><strong>{v.airport_ident}</strong> · {v.name}<br/>{v.visits} visit records · {Number(v.ground_hours).toFixed(1)}h observed ground</div>) : 'No recorded airport visits'}</td><td>{r.current_registration||'Unresolved'}</td><td>{canEdit ? <button onClick={()=>{setNotice('');setSelected(r.address);}}>Assign tail number</button> : 'Read-only'}</td></tr>)}</tbody></table></div>
      <nav aria-label="Unidentified aircraft pages"><button disabled={busy||request.offset===0} onClick={()=>submitRequest({...request,offset:Math.max(0,request.offset-50)})}>Previous</button><button disabled={busy||!data.has_more} onClick={()=>submitRequest({...request,offset:request.offset+50})}>Next</button></nav>
    </>}
  </section>;
}
