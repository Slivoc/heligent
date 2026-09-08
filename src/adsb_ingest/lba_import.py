"""First-pass additive import of one server-staged LBA organisation."""
from datetime import datetime
from hashlib import sha256
import json
import re
from uuid import uuid4, UUID
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .lba import SOURCE

SOURCE_CODE = 'LBA_DIRECTORY'


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def stage_preview(store, snapshot, actor):
    preview_id = str(uuid4())
    with store.connect() as c:
        c.execute('INSERT INTO tool_import_preview(id,adapter,snapshot,created_by) VALUES (%s,%s,%s,%s)',
                  (preview_id,SOURCE_CODE,Jsonb(snapshot),actor))
    return {**snapshot,'preview_id':preview_id,'mode':'STAGED_PREVIEW'}


def import_history(store):
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute("SET LOCAL statement_timeout='5s'")
        return c.execute('''SELECT id,finished_at,company_count,site_count,capability_count,metadata
            FROM company_import_batch WHERE source_code=%s AND status='SUCCEEDED'
            ORDER BY id DESC LIMIT 25''',(SOURCE_CODE,)).fetchall()


def organisation_scope(org):
    approval = re.fullmatch(r'(DE\.145\.[A-Za-z0-9]+)\s*-\s*gültig seit (\d{2}\.\d{2}\.\d{4})',org.get('approval',''))
    if not approval:
        raise ValueError('This first importer supports recognised DE.145 approvals only')
    if not org.get('name') or not 1 <= len(org.get('sites',[])) <= 250:
        raise ValueError('Organisation needs a name and 1–250 complete sites')
    count = 0
    for site in org['sites']:
        if not site.get('street') or not site.get('locality') or not site.get('ratings'):
            raise ValueError('A site is missing its address or ratings; import stopped')
        for rating in site['ratings']:
            if not re.match(r'^[ABCD]\d+\s*-',rating['wording']) or not rating['models']:
                raise ValueError('Unrecognised or incomplete rating; import stopped')
            count += len(rating['models'])
    if count > 2000:
        raise ValueError('Organisation exceeds the 2000 capability pilot limit')
    return approval[1],datetime.strptime(approval[2],'%d.%m.%Y').date()


def import_selection(store, payload, actor):
    if not isinstance(payload,dict) or payload.get('confirmed') is not True:
        raise ValueError('Confirm importing the selected organisation first')
    try:
        preview_id = str(UUID(str(payload.get('preview_id'))))
    except (ValueError,TypeError) as exc:
        raise ValueError('Fetch a fresh LBA preview first') from exc
    ordinal = payload.get('organisation_index')
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError('Choose an organisation from the preview')
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute("SET LOCAL statement_timeout='15s'")
        c.execute("SET LOCAL lock_timeout='3s'")
        c.execute('SELECT pg_advisory_xact_lock(714520)')
        preview = c.execute('''SELECT snapshot FROM tool_import_preview WHERE id=%s AND adapter=%s
            AND created_at>now()-interval '24 hours' ''',(preview_id,SOURCE_CODE)).fetchone()
        if not preview or ordinal >= len(preview['snapshot']['organisations']):
            raise ValueError('Preview missing or expired; fetch again')
        snapshot = preview['snapshot']
        if snapshot.get('truncated'):
            raise ValueError('Narrow the search to a complete result page before importing')
        org = snapshot['organisations'][ordinal]
        number,valid_from = organisation_scope(org)
        org_hash = digest(org)
        existing = c.execute('''SELECT raw_data,active FROM regulatory_approval_external_identifier
            WHERE source_code=%s AND external_id=%s''',(SOURCE_CODE,number)).fetchone()
        if existing:
            if not existing['active'] or existing['raw_data'].get('organisation_sha256') != org_hash:
                raise ValueError('This approval was imported previously and its source has changed or been disabled. Update/merge review is not yet supported; existing records were left untouched.')
            return {**existing['raw_data']['result'],'already_imported':True}
        # Match exact approval first, then exact company name. Never fuzzy-merge ADAC entities.
        approvals = c.execute('SELECT * FROM regulatory_approval WHERE upper(approval_number)=%s FOR UPDATE',(number.upper(),)).fetchall()
        if len(approvals)>1:
            raise ValueError('Multiple approvals share this number; resolve the duplicate before importing')
        if approvals:
            approval = approvals[0]
            company = c.execute('SELECT * FROM company WHERE id=%s FOR UPDATE',(approval['company_id'],)).fetchone()
            if approval['status']!='VALID' or (approval['valid_to'] and approval['valid_to']<datetime.now().date()):
                raise ValueError('Existing approval is not current; review it before importing')
        else:
            matches = c.execute('SELECT * FROM company WHERE lower(btrim(name))=lower(%s) FOR UPDATE',(org['name'],)).fetchall()
            if len(matches)>1:
                raise ValueError('Multiple companies have this name; resolve the duplicate before importing')
            company = matches[0] if matches else c.execute('''INSERT INTO company(company_key,name,is_mro)
                VALUES (%s,%s,true) RETURNING *''',('lba-'+digest(org['name'])[:24],org['name'])).fetchone()
            approval = c.execute('''INSERT INTO regulatory_approval(company_id,authority_code,approval_type,approval_number,
                valid_from,source_url,last_verified_at) VALUES (%s,'DE_LBA','PART_145',%s,%s,%s,%s) RETURNING *''',
                (company['id'],number,valid_from,SOURCE,snapshot['fetched_at'])).fetchone()
        if not company['active']:
            raise ValueError('Existing company is inactive; review it before importing')
        c.execute('''INSERT INTO company_data_source(code,name,source_kind,authority_code,source_url)
            VALUES (%s,'LBA technical organisations','REGULATOR','DE_LBA',%s) ON CONFLICT(code) DO NOTHING''',(SOURCE_CODE,SOURCE))
        metadata = {'organisation':org['name'],'approval':number,'organisation_sha256':org_hash,
                    'source_url':SOURCE,'source_sha256':snapshot['source_sha256'],'fetched_at':snapshot['fetched_at'],
                    'preview_id':preview_id,'reviewed_by':actor}
        batch = c.execute('''INSERT INTO company_import_batch(source_code,file_name,sha256,metadata)
            VALUES (%s,%s,%s,%s) RETURNING id''',(SOURCE_CODE,number+'.json',org_hash,Jsonb(metadata))).fetchone()['id']
        site_ids,cap_ids = set(),set()
        for site in org['sites']:
            site_key = 'lba-'+digest([site['street'].casefold(),site['locality'].casefold()])[:32]
            candidates = c.execute('''SELECT * FROM company_site WHERE company_id=%s AND
                (site_key=%s OR (lower(btrim(address_line_1))=lower(%s) AND lower(btrim(locality))=lower(%s))) FOR UPDATE''',
                (company['id'],site_key,site['street'],site['locality'])).fetchall()
            if len(candidates)>1 or (candidates and not candidates[0]['active']):
                raise ValueError('Ambiguous or inactive existing site; import stopped without changes')
            site_row = candidates[0] if candidates else c.execute('''INSERT INTO company_site(company_id,site_key,name,
                address_line_1,locality,is_base_maintenance,is_line_maintenance) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (company['id'],site_key,site['street']+' · '+site['locality'],site['street'],site['locality'],
                 any('Base' in r['wording'] for r in site['ratings']),any('Line' in r['wording'] for r in site['ratings']))).fetchone()
            sid = site_row['id']
            site_ids.add(sid)
            link = c.execute('SELECT active FROM company_site_approval WHERE company_site_id=%s AND regulatory_approval_id=%s',(sid,approval['id'])).fetchone()
            if link and not link['active']:
                raise ValueError('Site approval link is inactive; review before importing')
            c.execute('''INSERT INTO company_site_approval(company_site_id,regulatory_approval_id,source_code)
                VALUES (%s,%s,%s) ON CONFLICT DO NOTHING''',(sid,approval['id'],SOURCE_CODE))
            for rating in site['ratings']:
                wording = rating['wording']
                code = re.match(r'^([ABCD]\d+)',wording)[1]
                kind = {'A':'AIRCRAFT','B':'ENGINE','C':'COMPONENT','D':'SPECIALIST'}[code[0]]
                for model in rating['models']:
                    key = digest([sid,wording,model])
                    cap = c.execute('''INSERT INTO approval_capability(regulatory_approval_id,company_site_id,source_code,
                        capability_key,capability_kind,rating_code,model,limitation,is_base_maintenance,is_line_maintenance,last_seen_batch_id,raw_data)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(regulatory_approval_id,source_code,capability_key) DO NOTHING RETURNING id''',
                        (approval['id'],sid,SOURCE_CODE,key,kind,code,model,wording,'Base' in wording,'Line' in wording,batch,
                         Jsonb({'rating':wording,'model':model,'site':site['street'],'source_url':SOURCE}))).fetchone()
                    if cap:
                        cap_ids.add(cap['id'])
        result = {'company_id':company['id'],'company_name':company['name'],'approval_id':approval['id'],
                  'batch_id':batch,'sites':len(site_ids),'capabilities_added':len(cap_ids),'already_imported':False}
        c.execute('''INSERT INTO regulatory_approval_external_identifier(source_code,external_id,regulatory_approval_id,
            last_seen_batch_id,raw_data) VALUES (%s,%s,%s,%s,%s)''',
            (SOURCE_CODE,number,approval['id'],batch,Jsonb({'organisation_sha256':org_hash,'organisation':org,'result':result})))
        c.execute('''UPDATE company_import_batch SET status='SUCCEEDED',finished_at=now(),company_count=1,
            site_count=%s,approval_count=1,capability_count=%s WHERE id=%s''',(len(site_ids),len(cap_ids),batch))
    return result
