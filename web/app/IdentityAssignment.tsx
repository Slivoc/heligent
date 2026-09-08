import { useState } from 'react';
export function IdentityAssignment({address,onSaved,onCancel}:{address:string;onSaved:(message:string)=>void;onCancel:()=>void}) {
  const [form,setForm]=useState({address,registration:'',type_code:'',valid_from:'',valid_to:'',source_url:'',notes:''});
  const [preview,setPreview]=useState<{token:string;affected_days:number;first_day:string;last_day:string}|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  function change(key:string,value:string){setForm(f=>({...f,[key]:value}));setPreview(null);}
  async function submit(save=false){
    setBusy(true);setError('');
    try {
      const r=await fetch(`/api/tools/identity/${save?'assign':'preview'}`,{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'HeligentAdmin'},body:JSON.stringify({...form,token:preview?.token})});
      const value=await r.json();
      if(!r.ok)throw new Error(value.error||'Assignment failed; reload and check before retrying.');
      if(save)onSaved(value.message);else setPreview(value);
    }catch(e){setError(String(e));setPreview(null);}finally{setBusy(false);}
  }
  return <section className="tools-card" aria-label="Assign aircraft identity"><h3>Assign a tail number to {address.toUpperCase()}</h3>
    <p>Choose the verified effective dates, not just the displayed week. A blank end date also covers future ingestion. Callsigns alone are not proof of identity.</p>
    <form onSubmit={e=>{e.preventDefault();void submit();}}><fieldset disabled={busy}>
      <label>Tail number<input required maxLength={10} value={form.registration} onChange={e=>change('registration',e.target.value.toUpperCase())} placeholder="G-WSAS"/></label>
      <label>ICAO type (optional)<input maxLength={4} value={form.type_code} onChange={e=>change('type_code',e.target.value.toUpperCase())} placeholder="EC45"/></label>
      <label>Valid from (UTC)<input required type="date" value={form.valid_from} onChange={e=>change('valid_from',e.target.value)}/></label>
      <label>Valid to (optional, inclusive)<input type="date" value={form.valid_to} onChange={e=>change('valid_to',e.target.value)}/></label>
      <label>Source URL (optional)<input type="url" maxLength={2000} value={form.source_url} onChange={e=>change('source_url',e.target.value)}/></label>
      <label>Notes (optional)<textarea maxLength={4000} value={form.notes} onChange={e=>change('notes',e.target.value)}/></label>
      <button>Preview affected records</button>
    </fieldset></form>
    {error&&<p role="alert" className="map-warning">{error}</p>}
    {preview&&<div><p>This will assign {form.registration} to {preview.affected_days} daily records, {preview.first_day}–{preview.last_day}, and save a dated override. Flights and stops are not reprocessed. Previous identity values and your review are retained for audit.</p><button disabled={busy} onClick={()=>void submit(true)}>Confirm assignment</button></div>}
    <button disabled={busy} onClick={onCancel}>Cancel</button>
  </section>;
}
