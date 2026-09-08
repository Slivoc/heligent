"use client";
import { useEffect, useState } from 'react';
import { IdentityAssignment } from './IdentityAssignment';
type Result = { from: string; to: string; processed_days: number; expected_days: number; has_more: boolean; rows: { address: string; days: number; first_day: string; last_day: string; positions: number; hours: number; types: string[]; callsigns: string[]; current_registration: string | null }[] };
export function Unidentified() {
  const [from,setFrom]=useState('');
  const [to,setTo]=useState('');
  const [request,setRequest]=useState({from:'',to:'',offset:0});
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
    const params=new URLSearchParams({offset:String(request.offset)});
    if(request.from) params.set('from',request.from);
    if(request.to) params.set('to',request.to);
    fetch(`/api/tools/unidentified?${params}`,{signal:c.signal}).then(async r=>{
      const value=await r.json();
      if(!r.ok) throw new Error(value.error || 'Could not load unidentified activity. Try a shorter range.');
      if(!c.signal.aborted){setData(value);setFrom(value.from);setTo(value.to);}
    }).catch(e=>{if(!c.signal.aborted)setError(e.message);}).finally(()=>{if(!c.signal.aborted)setBusy(false);});
    return ()=>c.abort();
  },[request]);
  function load(offset=0){setBusy(true);setError('');setData(null);setRequest({from,to,offset});}
  return <section><h2>Hex addresses without tail numbers</h2>
    <p>Aircraft with positions but a blank registration in their historical daily records. Ranked by estimated airborne hours, highest first. Callsigns are clues, not verified registrations.</p>
    <form onSubmit={e=>{e.preventDefault();load();}}><label>From (UTC)<input type="date" value={from} onChange={e=>setFrom(e.target.value)} disabled={busy}/></label><label>To (UTC)<input type="date" value={to} onChange={e=>setTo(e.target.value)} disabled={busy}/></label><button disabled={busy}>{busy?'Loading…':'Load dates'}</button></form>
    <p>Defaults to the latest processed week; maximum 31 days. Analysts and administrators can assign verified identities using a dated preview and confirmation.</p>
    {notice&&<p role="status">{notice}</p>}
    {selected&&<IdentityAssignment key={selected} address={selected} onCancel={()=>setSelected('')} onSaved={message=>{setSelected('');setNotice(message);load();}}/>}
    {error && <p role="alert" className="map-warning">{error}</p>}
    {data && <><p>{data.from} to {data.to} · {data.processed_days}/{data.expected_days} dates processed · Page {request.offset/50+1}</p>
      {data.processed_days<data.expected_days && <p className="map-warning">Some dates are not processed. Totals are incomplete; even processed dates can have receiver gaps.</p>}
      <p>Totals cover only days with missing registration and recorded positions. A current registration below means historical records still need review. Up to 20 distinct callsigns are shown per hex.</p>
      {!data.rows.length && <p>No unidentified position-bearing aircraft found in this range. This does not establish complete identity coverage.</p>}
      <div className="unidentified-table"><table><thead><tr><th>Hex</th><th>Estimated hours</th><th>Positions / days</th><th>First / last day</th><th>Recorded types</th><th>Callsign clues</th><th>Current registration</th><th>Review</th></tr></thead><tbody>{data.rows.map(r=><tr key={r.address}><td><strong>{r.address.toUpperCase()}</strong></td><td>{Number(r.hours).toFixed(2)}</td><td>{Number(r.positions).toLocaleString()} / {r.days}</td><td>{r.first_day}<br/>{r.last_day}</td><td>{r.types.join(', ')||'Unknown'}</td><td>{r.callsigns.join(', ')||'None recorded'}</td><td>{r.current_registration||'Unresolved'}</td><td>{canEdit ? <button onClick={()=>{setNotice('');setSelected(r.address);}}>Assign tail number</button> : 'Read-only'}</td></tr>)}</tbody></table></div>
      <nav aria-label="Unidentified aircraft pages"><button disabled={busy||request.offset===0} onClick={()=>load(Math.max(0,request.offset-50))}>Previous</button><button disabled={busy||!data.has_more} onClick={()=>load(request.offset+50)}>Next</button></nav>
    </>}
  </section>;
}
