import { useEffect, useRef, useState } from 'react';
import { IdentityAssignment } from './IdentityAssignment';

export type TarMetadata = {csv_revision:string;source_date:string;source_url:string;types_revision:string;types_date:string;types_url:string;sha256:string;types_sha256:string;type_count:number};
type Candidate = {address:string;hours:number;days:number;first_day:string;last_day:string;registrations:string[];types:string[];identity_sources:string[];category:string;group:string;conflicts:string[];reviewable:boolean;applied?:boolean;applied_mode?:string;claim:null|{registration:string|null;type_code:string|null;description:string|null;flags:string}};
type Preview = {preview_id:string;snapshot_id:number;metadata:TarMetadata;created_at:string;from:string;to:string;region:string;gap:string;processed_days:number;expected_days:number;total:number;matched:number;reviewable:number;counts:Record<string,number>;rows:Candidate[];eligible_addresses:string[];filtered_count:number;offset:number;has_more:boolean};
type FillResult = {address:string;status:string;reason?:string;watch_id?:number;affected_days?:number};
const groups:Record<string,string> = {ROTORCRAFT:'Helicopters / rotorcraft',FIXED_WING:'Fixed wing',GROUND_VEHICLE:'Ground vehicles',OTHER:'Other categories',UNKNOWN:'Unknown category',CONFLICT:'Conflicts',UNMATCHED:'No source match',ALL:'All candidates'};
const regions:Record<string,string> = {EU:'Europe',GB:'United Kingdom',ALL:'Worldwide',UNLOCATED:'No airport region',NA:'North America',SA:'South America',AF:'Africa',AS:'Asia',OC:'Oceania',AN:'Antarctica'};
async function request<T>(url:string, payload?:unknown, signal?:AbortSignal):Promise<T> {
  const r=await fetch(url,payload===undefined?{signal}:{signal,method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'HeligentAdmin'},body:JSON.stringify(payload)});
  const v=await r.json();if(!r.ok)throw new Error(v.error||'Source tool request failed');return v;
}
export function Tar1090Tool({canEdit,metadata,latestPreview,onRefresh}:{canEdit:boolean;metadata?:TarMetadata;latestPreview?:string;onRefresh:()=>void}) {
  const [busy,setBusy]=useState(latestPreview?'Loading comparison':'');
  const [error,setError]=useState('');
  const [message,setMessage]=useState('');
  const [preview,setPreview]=useState<Preview|null>(null);
  const [selection,setSelection]=useState<Candidate|null>(null);
  const [addWatch,setAddWatch]=useState(true);
  const [fillResults,setFillResults]=useState<FillResult[]>([]);
  const [progress,setProgress]=useState<{done:number;total:number}|null>(null);
  const stopFill=useRef(false);
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;stopFill.current=true;};},[]);
  const reviewRef=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(selection)reviewRef.current?.scrollIntoView({block:'start'});},[selection]);
  const [filters,setFilters]=useState({from:'',to:'',region:'EU',gap:'EITHER'});
  const [page,updatePage]=useState({id:latestPreview||'',group:'ROTORCRAFT',search:'',offset:0,revision:0});
  function setPage(next:typeof page){setBusy('Loading comparison');setError('');setSelection(null);updatePage(next);}
  const [search,setSearch]=useState('');
  useEffect(()=>{
    if(!page.id)return;
    const controller=new AbortController();
    const params=new URLSearchParams({group:page.group,search:page.search,offset:String(page.offset)});
    request<Preview>(`/api/tools/tar1090/previews/${page.id}?${params}`,undefined,controller.signal).then(v=>{
      setPreview(v);setFilters({from:v.from,to:v.to,region:v.region,gap:v.gap});
    }).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setBusy('');});
    return()=>controller.abort();
  },[page]);
  async function refresh(){setBusy('Downloading and validating source');setError('');try{
    await request('/api/tools/tar1090/refresh',{});onRefresh();
  }catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy('');}}
  async function compare(){setBusy('Comparing observed hexes');setError('');setSelection(null);setMessage('');try{
    const v=await request<Preview>('/api/tools/tar1090/preview',filters);setPreview(v);setSearch('');
    setPage({id:v.preview_id,group:'ROTORCRAFT',search:'',offset:0,revision:0});
  }catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy('');}}
  async function fill(){
    if(!preview)return;
    const addresses=preview.eligible_addresses;
    stopFill.current=false;setBusy('Filling missing identities');setError('');setMessage('');setSelection(null);setFillResults([]);
    setProgress({done:0,total:addresses.length});const results:FillResult[]=[];
    try {
      for(const address of addresses){
        if(stopFill.current)break;
        const result=await request<FillResult>('/api/tools/tar1090/fill',{preview_id:preview.preview_id,address,add_watch:addWatch});
        results.push(result);
        if(!mounted.current)return;
        setFillResults([...results]);setProgress({done:results.length,total:addresses.length});
      }
      if(!mounted.current)return;
      const filled=results.filter(r=>r.status==='FILLED').length;
      const skipped=results.filter(r=>r.status==='SKIPPED').length;
      const failed=results.filter(r=>r.status==='FAILED').length;
      const watched=results.filter(r=>r.watch_id).length;
      setMessage(`Filled ${filled} aircraft. ${skipped} skipped${failed?`, ${failed} failed`:''}. ${watched?`${watched} aircraft are ready in Maintenance Pulse.`:''}${stopFill.current?' Stopped; completed fills are saved.':''}`);
    }catch(e){if(mounted.current)setError(`${e instanceof Error?e.message:String(e)} Completed fills are saved; retry to continue.`);}
    finally {
      if(mounted.current){setProgress(null);setBusy('Loading comparison');updatePage({...page,revision:page.revision+1});}
    }
  }
  return <section className="tools-card tar1090-tool" aria-label="tar1090 bulk source tool">
    <div className="source-overview"><div><p className="eyebrow">Bulk aircraft reference</p><h3>Fill the missing identities for Maintenance Pulse.</h3><p>Compare your data, then fill matching aircraft together.</p></div><button disabled={!canEdit||!!busy} onClick={()=>void refresh()}>{metadata?'Refresh tar1090 snapshot':'Download tar1090 snapshot'}</button></div>
    <p className="source-caption">Refreshes run on demand and can take about a minute. Source files and changes are retained automatically.</p>
    {metadata&&<details className="tar-evidence"><summary>Current reference revisions and hashes</summary><dl className="source-facts"><div><dt>Aircraft CSV published</dt><dd>{new Date(metadata.source_date).toLocaleString()}<a href={metadata.source_url} target="_blank" rel="noreferrer">Revision {metadata.csv_revision}</a><code>{metadata.sha256}</code></dd></div><div><dt>Type reference published</dt><dd>{new Date(metadata.types_date).toLocaleString()} · {metadata.type_count.toLocaleString()} types<a href={metadata.types_url} target="_blank" rel="noreferrer">Revision {metadata.types_revision}</a><code>{metadata.types_sha256}</code></dd></div></dl><p>Publication dates describe repository snapshots. Individual aircraft revision and effective dates are unknown. Attribution: tar1090-db / wiedehopf, Mictronics, ADSB Exchange. Owner fields are retained only in the original file and are not imported as operators.</p></details>}
    <form onSubmit={e=>{e.preventDefault();void compare();}}><fieldset disabled={!!busy||!canEdit||!metadata}>
      <label>Comparison from (UTC)<input type="date" value={filters.from} onChange={e=>setFilters({...filters,from:e.target.value})}/></label>
      <label>Comparison to (UTC)<input type="date" value={filters.to} onChange={e=>setFilters({...filters,to:e.target.value})}/></label>
      <label>Observed region<select value={filters.region} onChange={e=>setFilters({...filters,region:e.target.value})}>{Object.entries(regions).map(([v,l])=><option value={v} key={v}>{l}</option>)}</select></label>
      <label>Missing identity field<select value={filters.gap} onChange={e=>setFilters({...filters,gap:e.target.value})}><option value="TAIL">Tail number</option><option value="TYPE">ICAO type</option><option value="EITHER">Tail or type</option><option value="BOTH">Both tail and type</option></select></label>
      <button>Compare observed gaps</button>
    </fieldset></form>
    <p className="source-caption">Blank dates use the latest processed week. Maximum 31 days. Region is based on airport visits; once a hex is selected, all its processed observations in the period are checked for conflicts.</p>
    {!canEdit&&<p>Analyst access is required to download, compare or assign. Saved comparisons are available to view.</p>}
    {busy&&<p role="status">{busy}…</p>}{error&&<p role="alert" className="map-warning">{error}</p>}{message&&<p role="status">{message}</p>}
    {preview&&<>
      <h4>Saved comparison · {regions[preview.region]} · {preview.from} to {preview.to}</h4>
      <p>{preview.processed_days}/{preview.expected_days} dates processed · {preview.total.toLocaleString()} hexes with gaps · {preview.matched.toLocaleString()} source matches.</p>
      <p className="source-caption">Compared {new Date(preview.created_at).toLocaleString()} using <a href={preview.metadata.source_url} target="_blank" rel="noreferrer">CSV revision {preview.metadata.csv_revision.slice(0,12)}</a> (published {new Date(preview.metadata.source_date).toLocaleDateString()}). Counts describe this saved comparison; run it again to measure remaining gaps after assignments.</p>
      <nav className="tar-groups" aria-label="Candidate categories">{Object.entries(groups).map(([v,l])=><button key={v} disabled={!!busy} aria-pressed={page.group===v} onClick={()=>{setSelection(null);setPage({...page,group:v,offset:0});}}>{l} <strong>{v==='ALL'?preview.total:preview.counts[v]||0}</strong></button>)}</nav>
      <form onSubmit={e=>{e.preventDefault();setSelection(null);setPage({...page,search,offset:0});}}><label>Find candidate<input type="search" maxLength={40} value={search} onChange={e=>setSearch(e.target.value)} placeholder="Hex, tail or type"/></label><button disabled={!!busy}>Search candidates</button></form>
      <section className="tar-fill-panel" aria-label="Bulk identity fill">
        <h4>Fill {preview.eligible_addresses.length} matching aircraft</h4>
        <p>Applies to {groups[page.group].toLowerCase()}{page.search?` matching “${page.search}”`:''} across all result pages, for {preview.from} to {preview.to}. Dates and source notes are filled in automatically.</p>
        <p className="source-caption">Uses current tar1090 identities provisionally for this test period. Existing identity conflicts are skipped.</p>
        <label className="tools-check"><input type="checkbox" checked={addWatch} disabled={!!busy||!canEdit} onChange={e=>setAddWatch(e.target.checked)}/> Add matched helicopters to Maintenance Pulse</label>
        <button disabled={!canEdit||!!busy||!preview.eligible_addresses.length} onClick={()=>void fill()}>Fill {preview.eligible_addresses.length} {preview.eligible_addresses.length===1?'identity':'identities'}</button>
        {progress&&<><p role="status">{progress.done} of {progress.total} aircraft processed</p><progress max={progress.total} value={progress.done}/><button onClick={()=>{stopFill.current=true;}}>Stop after this aircraft</button></>}
        {fillResults.some(r=>r.reason)&&<details><summary>Skipped / failed aircraft</summary>{fillResults.filter(r=>r.reason).map(r=><p key={r.address}><strong>{r.address.toUpperCase()}</strong>: {r.reason}</p>)}</details>}
        {fillResults.some(r=>r.watch_id)&&<p><a href="#pulse">Open Maintenance Pulse →</a></p>}
      </section>
      <p>{groups[page.group]} · {preview.filtered_count.toLocaleString()} results. Rotorcraft includes helicopters, gyrocopters and tilt-rotors. Sorted by estimated airborne hours within each category.</p>
      <div className="source-table-wrap"><table className="source-table"><thead><tr><th>Observed hex / activity</th><th>Existing resolved identity</th><th>Source claim</th><th>Comparison / review</th></tr></thead><tbody>{preview.rows.map(r=><tr key={r.address}><td><strong>{r.address.toUpperCase()}</strong><small>{Number(r.hours).toFixed(1)} hours · {r.days} days</small><small>{r.first_day} to {r.last_day}</small></td><td>{r.registrations.join(', ')||'No tail'}<small>{r.types.join(', ')||'No ICAO type'}</small><small>{r.identity_sources.join(', ')}</small></td><td>{r.claim?<><strong>{r.claim.registration||'No tail'}</strong> · {r.claim.type_code||'No type'}<small>{r.claim.description}</small><small>{groups[r.category]||r.category} · flags {r.claim.flags||'none'}</small></>:'No exact hex match'}</td><td>{r.conflicts.map(c=><small key={c} className="map-warning">{c}</small>)}{r.reviewable?<button disabled={!canEdit||!!busy} onClick={()=>setSelection(r)}>Review dates for {r.address.toUpperCase()}</button>:<small>{r.applied?(r.applied_mode==='BULK_PROVISIONAL'?'Filled for testing':'Identity already reviewed'):r.group==='UNMATCHED'?'Try another source':r.conflicts.length?'Resolve conflict separately':r.category==='GROUND_VEHICLE'?'Ground identifier; excluded from tail assignment':'Incomplete, unsupported or no useful new identity fields'}</small>}</td></tr>)}</tbody></table>{!preview.rows.length&&<p className="source-empty">No candidates in this group. Check the other groups or change the comparison dates.</p>}</div>
      <div className="tar-paging"><button disabled={!!busy||preview.offset===0} onClick={()=>setPage({...page,offset:Math.max(0,preview.offset-50)})}>Previous candidates</button><span>{preview.filtered_count?`${preview.offset+1}–${Math.min(preview.offset+50,preview.filtered_count)}`:'0'} of {preview.filtered_count}</span><button disabled={!!busy||!preview.has_more} onClick={()=>setPage({...page,offset:preview.offset+50})}>Next candidates</button></div>
      <div ref={reviewRef} style={{scrollMarginTop:100}}>{selection?.claim?.registration&&<IdentityAssignment key={`${preview.preview_id}-${selection.address}`} address={selection.address} candidate={{preview_id:preview.preview_id,registration:selection.claim.registration,type_code:selection.claim.type_code,source_url:preview.metadata.source_url}} onCancel={()=>setSelection(null)} onSaved={m=>{setSelection(null);setMessage(m);setPage({...page,revision:page.revision+1});}}/>}</div>
    </>}
  </section>;
}
