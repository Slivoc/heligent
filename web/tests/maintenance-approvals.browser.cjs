/* eslint-disable @typescript-eslint/no-require-imports */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');

const counts={companies:2,approvals:3,current_approvals:3,sites:2,capabilities:4,aircraft_capabilities:3,site_capabilities:2,company_capabilities:2,distinct_type_codes:0,mapped_aircraft_capabilities:0,unmapped_aircraft_capabilities:3,coordinate_sites:0,airport_linked_sites:0};
const source={source_code:'UK_CAA_PART145',source_name:'UK CAA fixture',authority_codes:['UK_CAA'],...counts,last_imported_at:'2026-10-10',last_verified_at:'2026-08-20',snapshot_date:null};
const company={id:42,name:'Fixture MRO Ltd',country_code:'GB',approval_count:2};
const capability={capability_id:1,approval_id:1,authority_code:'UK_CAA',approval_number:'UK.145.TEST',approval_status:'VALID',approval_current:true,capability_kind:'AIRCRAFT',model:'EC135 limited variants <script>',limitation:'Original engine and variant limitations retained',is_base_maintenance:false,is_line_maintenance:true,scope_level:'COMPANY',site:null,source_url:'javascript:alert(1)',source_code:'UK_CAA_PART145',last_verified_at:'2026-08-20',mappings:[]};

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL?{channel:process.env.PLAYWRIGHT_CHANNEL}:{})});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:1000}});
    const requests=[],errors=[];
    let failCoverage=false,changedRevision=false;
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/api/**',async route=>{
      const req=route.request(),url=new URL(req.url()),p=url.pathname;
      requests.push({path:p,params:Object.fromEntries(url.searchParams),method:req.method()});
      if(p==='/api/auth/session')return route.fulfill({json:{user:{role:'VIEWER'}}});
      if(p==='/api/tools/maintenance/coverage'){
        if(failCoverage)return route.fulfill({status:503,json:{error:'Fixture coverage unavailable'}});
        const companyGeography=url.searchParams.get('geography')==='company_country';
        return route.fulfill({json:{data:{totals:counts,unlocated:{...counts,companies:1,approvals:1,sites:0,capabilities:2,aircraft_capabilities:1},countries:[{...counts,country_code:'GB',sites:2,capabilities:2,aircraft_capabilities:2,authority_codes:['UK_CAA'],source_codes:['UK_CAA_PART145']}],sources:[source],definitions:{geography:companyGeography?'company_country':'site_country'}},meta:{dataset_revision:'one',generated_at:'2026-10-17'}}});
      }
      if(p==='/api/tools/maintenance/companies')return route.fulfill({json:{data:[company],meta:{next_cursor:null}}});
      if(p==='/api/tools/maintenance/companies/42/capabilities'){
        const engine=url.searchParams.get('kind')==='ENGINE',next=url.searchParams.get('cursor');
        const row=engine?{...capability,capability_id:3,capability_kind:'ENGINE',model:'Engine-only scope'}:{...capability,capability_id:next?2:1,model:next?'Second aircraft scope':capability.model};
        return route.fulfill({json:{data:{company,capabilities:[row],kind_counts:{AIRCRAFT:2,ENGINE:1,COMPONENT:0,SPECIALIST:0,SERVICE:0,OTHER:0}},meta:{dataset_revision:changedRevision?'two':'one',next_cursor:!engine&&!next?'2':null}}});
      }
      return route.fulfill({status:503,json:{error:'Unused fixture API'}});
    });
    await page.goto((process.env.TOOLS_MAINTENANCE_URL||'http://127.0.0.1:5089')+'/#tools');
    await page.getByRole('heading',{name:'Maintenance approvals',exact:true}).waitFor();
    await page.getByRole('button',{name:/United Kingdom.*2 sites/}).click();
    await page.getByRole('button',{name:/Fixture MRO Ltd/}).waitFor();
    assert.ok(requests.some(r=>r.params.site_country==='GB'&&!r.params.country));
    await page.getByRole('button',{name:/Fixture MRO Ltd/}).click();
    await page.getByRole('heading',{name:capability.model,exact:true}).waitFor();
    assert.equal(await page.locator('.maintenance-scope-list script').count(),0);
    assert.equal(await page.getByRole('link',{name:'Original approval source ↗'}).count(),0);
    assert.match(await page.locator('.maintenance-company-scope').innerText(),/Company-wide; no facility established/);
    changedRevision=true;
    await page.getByRole('button',{name:'Load more scope rows'}).click();
    await page.getByText('Approval evidence changed while paging.',{exact:false}).waitFor();
    assert.equal(await page.getByRole('heading',{name:'Second aircraft scope',exact:true}).count(),0);
    await page.getByRole('button',{name:'Retry scope'}).click();
    await page.getByRole('button',{name:'Load more scope rows'}).waitFor();
    await page.getByRole('button',{name:'Load more scope rows'}).click();
    await page.getByRole('heading',{name:'Second aircraft scope',exact:true}).waitFor();
    await page.getByRole('button',{name:'Engines (1)',exact:true}).click();
    await page.getByRole('heading',{name:'Engine-only scope',exact:true}).waitFor();
    assert.equal(await page.getByRole('heading',{name:'Second aircraft scope',exact:true}).count(),0);
    await page.getByLabel('Geographic evidence').selectOption('company_country');
    await page.getByLabel('Country',{exact:true}).selectOption('GB');
    await page.getByText('Company domicile only',{exact:true}).waitFor();
    assert.ok(requests.some(r=>r.params.country==='GB'&&!r.params.site_country));
    await page.getByText('Sources, freshness and original evidence').click();
    await page.getByText('Source snapshot',{exact:true}).waitFor();
    assert.match(await page.locator('.maintenance-source-grid').innerText(),/Not recorded/);
    await fs.mkdir('tmp/maintenance-approval-results',{recursive:true});
    await page.screenshot({path:'tmp/maintenance-approval-results/desktop.png',fullPage:true});
    for(const width of [760,390,320]){
      await page.setViewportSize({width,height:1100});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`Overflow at ${width}`);
    }
    await page.screenshot({path:'tmp/maintenance-approval-results/mobile.png',fullPage:true});
    failCoverage=true;
    await page.getByRole('button',{name:'Refresh evidence',exact:true}).click();
    await page.getByText('Fixture coverage unavailable',{exact:false}).waitFor();
    failCoverage=false;
    await page.getByRole('button',{name:'Retry coverage'}).click();
    await page.getByRole('heading',{name:'Company countries',exact:true}).waitFor();
    assert.ok(requests.every(r=>r.method==='GET'));
    assert.deepEqual(errors,[]);
    console.log('Maintenance approvals browser checks passed: geography, grouping, pagination/revision safety, source dates, escaping, failures and mobile.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
