"use client";

import { FormEvent, useEffect, useRef, useState } from 'react';
import './maintenance-approvals.css';

type Counts = {
  companies:number; approvals:number; current_approvals:number; sites:number; capabilities:number;
  aircraft_capabilities:number; site_capabilities:number; company_capabilities:number;
  distinct_type_codes:number; mapped_aircraft_capabilities:number; unmapped_aircraft_capabilities:number;
  coordinate_sites?:number; airport_linked_sites?:number;
};
type Country = Counts & {country_code:string; sources?:string[]; authority_codes?:string[]};
type Source = Counts & {source_code:string;source_name:string;authority_codes?:string[];last_imported_at?:string|null;last_fetched_at?:string|null;snapshot_date?:string|null;last_verified_at?:string|null};
type Coverage = {totals:Counts;countries:Country[];sources:Source[];unlocated:Counts;definitions:Record<string,unknown>};
type Company = {id:number;name:string;legal_name?:string;trading_name?:string;country_code?:string|null;approval_count?:number};
type Mapping = {aircraft_type_code:string;match_level:string;variant_scope?:string;stale:boolean;active:boolean;origin?:string};
type Capability = {
  capability_id:number;approval_id:number;authority_code:string;approval_number:string;approval_status:string;
  approval_current?:boolean;valid_from?:string|null;valid_to?:string|null;last_verified_at?:string|null;
  source_url?:string|null;source_code:string;capability_kind:string;model?:string|null;manufacturer?:string|null;
  limitation?:string|null;aircraft_type_code?:string|null;is_base_maintenance:boolean;is_line_maintenance:boolean;
  scope_level:string;site?:{id:number;name?:string;country_code?:string|null;locality?:string|null}|null;
  mappings:Mapping[];type_interpretation?:string;
};
type Envelope<T> = {data:T;meta:{next_cursor?:string|null;generated_at?:string;dataset_revision?:string}};
const KINDS:Record<string,string> = {AIRCRAFT:'Aircraft',ENGINE:'Engines',COMPONENT:'Components',SPECIALIST:'Specialist services',SERVICE:'Services',OTHER:'Other'};
const fmt = (n?:number|null) => n == null ? '—' : n.toLocaleString('en-GB');
const date = (s?:string|null) => {const value=s?new Date(s.slice(0,10)+'T12:00:00Z'):null;return value&&Number.isFinite(value.getTime())?new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}).format(value):'Not recorded';};
const countryName = (code:string) => {try{return new Intl.DisplayNames(['en-GB'],{type:'region'}).of(code)||code;}catch{return code;}};
const safeURL = (value?:string|null) => {try{const u=new URL(value||'');return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:undefined;}catch{return undefined;}};
async function read<T>(path:string,signal?:AbortSignal):Promise<Envelope<T>> {
  const r=await fetch(path,{signal});
  const value=await r.json();
  if(!r.ok)throw new Error(typeof value.error==='string'?value.error:value.error?.message||'Approval evidence could not load.');
  return value;
}

export function MaintenanceApprovals({onMappings,onSource}:{onMappings:()=>void;onSource:(code:string)=>void}) {
  const [source,setSource]=useState('');
  const [authority,setAuthority]=useState('');
  const [kind,setKind]=useState('ALL');
  const [geography,setGeography]=useState('site_country');
  const [selectedCountry,setSelectedCountry]=useState('');
  const [coverage,setCoverage]=useState<Coverage|null>(null);
  const [catalog,setCatalog]=useState<Source[]>([]);
  const [generated,setGenerated]=useState<string>();
  const [busy,setBusy]=useState(true);
  const [error,setError]=useState('');
  const [revision,setRevision]=useState(0);
  const [query,setQuery]=useState('');
  const [search,setSearch]=useState('');
  const [companies,setCompanies]=useState<Company[]>([]);
  const [companyCursor,setCompanyCursor]=useState<string|null>(null);
  const [nextCompanyCursor,setNextCompanyCursor]=useState<string|null>(null);
  const [companyError,setCompanyError]=useState('');
  const [companyBusy,setCompanyBusy]=useState(true);
  const [searchRevision,setSearchRevision]=useState(0);
  const [chosen,setChosen]=useState<Company|null>(null);
  useEffect(()=>{
    const c=new AbortController();
    const params=new URLSearchParams({kind,geography});
    if(source)params.set('source',source);if(authority)params.set('authority',authority);
    read<Coverage>('/api/tools/maintenance/coverage?'+params,c.signal).then(v=>{
      setCoverage(v.data);setGenerated(v.meta.generated_at);
      setCatalog(previous=>Array.from(new Map([...previous,...v.data.sources].map(row=>[row.source_code,row])).values()));
    }).catch(e=>{if(!c.signal.aborted)setError(e.message);}).finally(()=>{if(!c.signal.aborted)setBusy(false);});
    return()=>c.abort();
  },[source,authority,kind,geography,revision]);
  useEffect(()=>{
    const c=new AbortController();
    const params=new URLSearchParams({query:search,role:'MRO',kind,limit:'20'});
    if(source)params.set('source',source);if(authority)params.set('authority',authority);
    if(selectedCountry)params.set(geography==='site_country'?'site_country':'country',selectedCountry);
    if(companyCursor)params.set('cursor',companyCursor);
    read<Company[]>('/api/tools/maintenance/companies?'+params,c.signal).then(v=>{
      setCompanies(v.data);setNextCompanyCursor(v.meta.next_cursor||null);
    }).catch(e=>{if(!c.signal.aborted){setCompanyError(e.message);setCompanies([]);setNextCompanyCursor(null);}}).finally(()=>{if(!c.signal.aborted)setCompanyBusy(false);});
    return()=>c.abort();
  },[search,source,authority,kind,geography,selectedCountry,companyCursor,revision,searchRevision]);
  function directoryPage(cursor:string|null){setCompanyBusy(true);setCompanyError('');setCompanies([]);setCompanyCursor(cursor);setChosen(null);}
  function filters(action:()=>void,refreshCoverage=true){
    action();directoryPage(null);
    if(refreshCoverage){setBusy(true);setError('');setCoverage(null);setSelectedCountry('');}
  }
  function reload(){setBusy(true);setError('');setCoverage(null);setCompanyBusy(true);setCompanyError('');setChosen(null);setRevision(n=>n+1);}
  const selected=coverage?.countries.find(row=>row.country_code===selectedCountry);
  const sorted=[...(coverage?.countries||[])].sort((a,b)=>(geography==='site_country'?b.sites-a.sites:b.companies-a.companies)||a.country_code.localeCompare(b.country_code));
  let visible=sorted.slice(0,10);
  if(selected&&!visible.some(row=>row.country_code===selected.country_code))visible=[...visible.slice(0,9),selected];
  const max=Math.max(1,...sorted.map(row=>geography==='site_country'?row.sites:row.companies));
  const authorities=[...new Set(catalog.flatMap(row=>row.authority_codes||[]))].sort();
  return <section className="maintenance-approvals" aria-label="Maintenance approval evidence">
    <header className="maintenance-heading"><div><p className="eyebrow">Regulatory scope · Imported evidence</p><h1>Maintenance approvals</h1><p>Explore aircraft and other maintenance scope by source, authority and location.</p></div><button onClick={reload} disabled={busy}>Refresh evidence</button></header>
    <form className="maintenance-filters" onSubmit={e=>e.preventDefault()}>
      <label>Approval source<select value={source} onChange={e=>filters(()=>setSource(e.target.value))}><option value="">All imported sources</option>{catalog.map(s=><option key={s.source_code} value={s.source_code}>{s.source_name}</option>)}</select></label>
      <label>Approving authority<select value={authority} onChange={e=>filters(()=>setAuthority(e.target.value))}><option value="">All authorities</option>{authorities.map(a=><option key={a}>{a}</option>)}</select></label>
      <label>Capability kind<select value={kind} onChange={e=>filters(()=>setKind(e.target.value))}><option value="ALL">All capability kinds</option>{Object.entries(KINDS).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
      <label>Geographic evidence<select value={geography} onChange={e=>filters(()=>{setGeography(e.target.value);setSelectedCountry('');})}><option value="site_country">Facility country</option><option value="company_country">Company country</option></select></label>
    </form>
    {busy&&<p role="status">Loading approval coverage…</p>}
    {error&&<p role="alert" className="map-warning">{error} <button onClick={reload}>Retry coverage</button></p>}
    {coverage&&<>
      <div className="maintenance-metrics" aria-label="Filtered approval evidence totals"><article><strong>{fmt(coverage.totals.approvals)}</strong><span>Approval records</span></article><article><strong>{fmt(coverage.totals.aircraft_capabilities)}</strong><span>Aircraft scope rows</span></article><article><strong>{fmt(geography==='site_country'?coverage.totals.sites-coverage.unlocated.sites:coverage.totals.companies-coverage.unlocated.companies)}</strong><span>{geography==='site_country'?'Sites with country':'Companies with country'}</span></article><article><strong>{fmt(coverage.unlocated.capabilities)}</strong><span>Scope rows without this geography</span></article></div>
      <div className="maintenance-geography">
        <section className="maintenance-country-panel" aria-label="Approval geography"><header><h3>{geography==='site_country'?'Imported facility locations':'Company countries'}</h3><span>{fmt(coverage.countries.length)} countries</span></header>
          <label>Country<select aria-label="Country" value={selectedCountry} onChange={e=>filters(()=>setSelectedCountry(e.target.value),false)}><option value="">All countries</option>{[...sorted].sort((a,b)=>countryName(a.country_code).localeCompare(countryName(b.country_code))).map(row=><option key={row.country_code} value={row.country_code}>{countryName(row.country_code)}</option>)}</select></label>
          <div className="maintenance-country-list">{visible.map(row=><button type="button" className="maintenance-country" key={row.country_code} aria-pressed={selectedCountry===row.country_code} onClick={()=>{if(selectedCountry!==row.country_code)filters(()=>setSelectedCountry(row.country_code),false);}}><span>{countryName(row.country_code)}</span><strong>{fmt(geography==='site_country'?row.sites:row.companies)} {geography==='site_country'?'sites':'companies'}</strong><span className="maintenance-track" aria-hidden="true"><span style={{width:`${(geography==='site_country'?row.sites:row.companies)/max*100}%`}}/></span></button>)}</div>
          {!sorted.length&&<p>No country is recorded for this selection. Unlocated evidence remains included in the totals.</p>}
          <p className="source-caption">Showing {visible.length} of {sorted.length} countries. Imported presence does not establish complete national coverage.</p>
        </section>
        <section className="maintenance-country-detail" aria-live="polite"><p className="eyebrow">{selected?'Selected country':'Geography not recorded'}</p><h3>{selected?countryName(selected.country_code):'Keep unknown locations visible'}</h3>
          {selected?<><span className="maintenance-evidence-label">{geography==='site_country'?'Facility country recorded':'Company domicile only'}</span><dl><div><dt>Companies</dt><dd>{fmt(selected.companies)}</dd></div><div><dt>Approval records</dt><dd>{fmt(selected.approvals)}</dd></div><div><dt>Aircraft scope rows</dt><dd>{fmt(selected.aircraft_capabilities)}</dd></div><div><dt>All selected scope rows</dt><dd>{fmt(selected.capabilities)}</dd></div>{selected.authority_codes&&<div><dt>Approving authorities</dt><dd>{selected.authority_codes.join(' · ')||'Not recorded'}</dd></div>}</dl></>:<dl><div><dt>Scope rows</dt><dd>{fmt(coverage.unlocated.capabilities)}</dd></div><div><dt>Sites missing country</dt><dd>{fmt(coverage.unlocated.sites)}</dd></div><div><dt>Company-wide scope</dt><dd>{fmt(coverage.totals.company_capabilities)}</dd></div></dl>}
          <p className="source-caption">{geography==='site_country'?'Company-wide scope and sites without country remain unlocated. An authority’s country does not establish a facility location.':'Company country does not establish the location of a maintenance facility.'}</p>
          <p className="source-caption">{fmt(coverage.totals.mapped_aircraft_capabilities)} aircraft rows have recorded type mappings; review labels describe interpretations, not verified certification.</p>
        </section>
      </div>
      <details className="maintenance-sources"><summary>Sources, freshness and original evidence</summary><div className="maintenance-source-grid">{coverage.sources.map(s=><article key={s.source_code}><h3><button onClick={()=>onSource(s.source_code)}>{s.source_name}</button></h3><p>{s.authority_codes?.join(' · ')||'Authority not recorded'} · {fmt(s.approvals)} approvals · {fmt(s.aircraft_capabilities)} aircraft rows</p><dl><div><dt>Source snapshot</dt><dd>{date(s.snapshot_date)}</dd></div><div><dt>Latest recorded verification</dt><dd>{date(s.last_verified_at)}</dd></div><div><dt>Last fetched</dt><dd>{date(s.last_fetched_at)}</dd></div><div><dt>Last imported</dt><dd>{date(s.last_imported_at)}</dd></div></dl></article>)}</div><p className="source-caption">Verification dates are the latest recorded for each source, not a guarantee for every approval. Fetch and import dates do not replace an unknown source snapshot date.</p></details>
      <p className="source-caption">Scope rows can repeat across sites and approvals; they are not distinct aircraft types. Counts of companies, sites and approvals overlap between sources and countries. Evidence read {date(generated)}.</p>
    </>}
    <section className="maintenance-directory" aria-label="MRO capability directory"><header><h3>MRO capability directory</h3><button onClick={onMappings}>Review aircraft type mappings</button></header><p>{selectedCountry?`${countryName(selectedCountry)} · ${geography==='site_country'?'recorded facility country':'company country'}`:'Search imported MRO organisations and inspect their recorded approval scope.'}</p>
      <form onSubmit={(e:FormEvent)=>{e.preventDefault();directoryPage(null);setSearch(query);setSearchRevision(n=>n+1);}}><label>Find an MRO<input type="search" value={query} onChange={e=>setQuery(e.target.value)} maxLength={200} placeholder="Company or approval number"/></label><button disabled={companyBusy}>Search MROs</button></form>
      {companyError&&<p role="alert" className="map-warning">{companyError} <button onClick={reload}>Retry directory</button></p>}
      {companyBusy?<p role="status">Loading MROs…</p>:<><div className="maintenance-company-list">{companies.map(company=><button type="button" key={company.id} aria-pressed={chosen?.id===company.id} onClick={()=>setChosen(company)}><strong>{company.name}</strong><span>{company.country_code?countryName(company.country_code):'Company country unknown'} · {fmt(company.approval_count)} approvals</span></button>)}</div>{!companies.length&&!companyError&&<p>No matching approval evidence is available in this dataset.</p>}<div className="maintenance-paging">{companyCursor&&<button onClick={()=>directoryPage(null)}>Back to first MROs</button>}{nextCompanyCursor&&<button onClick={()=>directoryPage(nextCompanyCursor)}>Next MROs</button>}</div></>}
      {chosen&&<CompanyCapabilities key={`${chosen.id}-${kind}`} company={chosen} initialKind={kind==='ALL'?'AIRCRAFT':kind}/>}
    </section>
  </section>;
}

function CompanyCapabilities({company,initialKind}:{company:Company;initialKind:string}) {
  const [kind,setKind]=useState(initialKind);
  const [rows,setRows]=useState<Capability[]>([]);
  const [counts,setCounts]=useState<Record<string,number>>({});
  const [cursor,setCursor]=useState<string|null>(null);
  const [next,setNext]=useState<string|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(true);
  const [revision,setRevision]=useState(0);
  const datasetRevision=useRef<string|null>(null);
  useEffect(()=>{
    const c=new AbortController();
    const params=new URLSearchParams({kind,limit:'100'});if(cursor)params.set('cursor',cursor);
    read<{company:Company;capabilities:Capability[];kind_counts:Record<string,number>}>(`/api/tools/maintenance/companies/${company.id}/capabilities?${params}`,c.signal).then(v=>{
      if(cursor&&datasetRevision.current!==v.meta.dataset_revision)throw new Error('Approval evidence changed while paging. Reload scope to read a consistent version.');
      datasetRevision.current=v.meta.dataset_revision||null;
      setRows(previous=>cursor?[...previous,...v.data.capabilities]:v.data.capabilities);setCounts(v.data.kind_counts);setNext(v.meta.next_cursor||null);
    }).catch(e=>{if(!c.signal.aborted){setError(e.message);setNext(null);}}).finally(()=>{if(!c.signal.aborted)setBusy(false);});
    return()=>c.abort();
  },[company.id,kind,cursor,revision]);
  function reload(){setBusy(true);setError('');setCursor(null);setRows([]);setNext(null);datasetRevision.current=null;setRevision(n=>n+1);}
  return <section className="maintenance-company-scope" aria-label={`${company.name} approval scope`}><h3>{company.name}</h3><p>Recorded scope across this company’s authorities. Company-wide scope does not establish approval at an individual facility.</p><nav aria-label="Maintenance capability kinds">{Object.entries(KINDS).map(([key,label])=><button key={key} aria-pressed={kind===key} onClick={()=>{if(kind===key)return;setBusy(true);setError('');setKind(key);setCursor(null);setRows([]);setNext(null);datasetRevision.current=null;}}>{label}{counts[key]!=null?` (${fmt(counts[key])})`:''}</button>)}</nav>
    {error&&<p role="alert" className="map-warning">{error} <button onClick={reload}>Retry scope</button></p>}
    <div className="maintenance-scope-list">{rows.map(row=><article key={row.capability_id}><header><h4>{row.model||row.limitation||row.aircraft_type_code||'Scope wording not recorded'}</h4><span className="maintenance-evidence-label">{row.scope_level==='SITE'?'Site-specific scope':'Company-wide scope'}</span></header>{row.model&&row.limitation&&<p className="maintenance-raw-scope">{row.limitation}</p>}<dl><div><dt>Approval</dt><dd>{row.authority_code} · {row.approval_number}</dd></div><div><dt>Recorded status</dt><dd>{row.approval_status}{row.approval_current===false?' · outside recorded current validity':''}</dd></div><div><dt>Facility</dt><dd>{row.site?[row.site.name,row.site.locality,row.site.country_code?countryName(row.site.country_code):'Country unknown'].filter(Boolean).join(' · '):'Company-wide; no facility established'}</dd></div><div><dt>Privileges</dt><dd>{[row.is_base_maintenance?'Base maintenance':null,row.is_line_maintenance?'Line maintenance':null].filter(Boolean).join(' · ')||'Not specified in imported scope'}</dd></div><div><dt>Last recorded verification</dt><dd>{date(row.last_verified_at)}</dd></div>{(row.valid_from||row.valid_to)&&<div><dt>Recorded validity</dt><dd>{date(row.valid_from)} → {date(row.valid_to)}</dd></div>}</dl>
      {row.aircraft_type_code&&<p>Source aircraft code: <strong>{row.aircraft_type_code}</strong></p>}
      {row.mappings?.filter(m=>m.active).map((mapping,i)=><p className="maintenance-mapping" key={`${mapping.aircraft_type_code}-${i}`}>{mapping.aircraft_type_code} · {mapping.stale?'Mapping needs renewed review':mapping.match_level==='REVIEWED_TYPE'?'Reviewed type interpretation':'Possible aircraft family'}{mapping.variant_scope?` · Restricted variants: ${mapping.variant_scope}`:''}</p>)}
      {row.capability_kind==='AIRCRAFT'&&!row.aircraft_type_code&&!row.mappings?.some(m=>m.active)&&<p className="source-caption">Aircraft type normalization needs review; original scope is retained.</p>}
      {safeURL(row.source_url)&&<a href={safeURL(row.source_url)} target="_blank" rel="noreferrer">Original approval source ↗</a>}
    </article>)}</div>
    {busy&&<p role="status">Loading approval scope…</p>}{!busy&&!error&&!rows.length&&<p>No {KINDS[kind]?.toLowerCase()} scope is recorded here. This does not establish absence of approval.</p>}{next&&<button onClick={()=>{setBusy(true);setError('');setCursor(next);}} disabled={busy}>Load more scope rows</button>}
  </section>;
}
