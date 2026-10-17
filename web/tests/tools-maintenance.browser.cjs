/* eslint-disable @typescript-eslint/no-require-imports */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

const counts = {
  companies:0,approvals:0,current_approvals:0,sites:0,capabilities:0,aircraft_capabilities:0,
  site_capabilities:0,company_capabilities:0,distinct_type_codes:0,mapped_aircraft_capabilities:0,
  unmapped_aircraft_capabilities:0,coordinate_sites:0,airport_linked_sites:0,
};
const source = (code,name,group) => ({
  code,name,group,method:'Fixture import',regions:['EU'],countries:['GB'],fields:['Fixture fields'],
  match:'Source identifier',readiness:'AVAILABLE',url:'https://example.test/source',cadence:'Not verified',
  refresh_days:30,scope:'Fixture scope',limitations:'Imported evidence only',next_step:'Inspect evidence',
  command:'',family:code,schedule:'No schedule recorded',has_import:true,latest_failed:false,
  freshness:{status:'CURRENT',age_days:1},record_count:1,history:[],lookups:[],
});
const company = {
  ...source('FIXTURE_PART145','Fixture approval source','COMPANY'),
  imported_site_countries:['GB'],imported_company_countries:['FR'],last_verified_at:'2026-08-23T00:00:00Z',
};
const sources = [
  company,source('FIXTURE_REGISTER','Fixture aircraft register','IDENTITY'),
  {...source('ADSB_LOL','Fixture activity archive','ACTIVITY'),activity:{processed_days:1,failed_days:0}},
  source('FIXTURE_AIRPORTS','Fixture airport reference','REFERENCE'),
];

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL?{channel:process.env.PLAYWRIGHT_CHANNEL}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:1000}});
    const errors=[],requests=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/api/**',async route=>{
      const request=route.request(),path=new URL(request.url()).pathname;
      requests.push({path,method:request.method()});
      if(path==='/api/auth/session')return route.fulfill({json:{user:{role:'VIEWER'}}});
      if(path==='/api/tools/maintenance/coverage')return route.fulfill({json:{data:{totals:counts,unlocated:counts,countries:[],sources:[],definitions:{}},meta:{generated_at:'2026-10-17T12:00:00Z'}}});
      if(path==='/api/tools/maintenance/companies')return route.fulfill({json:{data:[],meta:{}}});
      if(path==='/api/tools/sources')return route.fulfill({json:{generated_at:'2026-10-17T12:00:00Z',sources,regions:{EU:'Europe'},history_note:'Fixture evidence'}});
      if(path.startsWith('/api/tools/sources/')){
        const selected=sources.find(value=>value.code===decodeURIComponent(path.split('/').pop()));
        return route.fulfill({status:selected?200:404,json:selected||{error:'Unknown source'}});
      }
      if(path==='/api/tools/identity-gaps')return route.fulfill({json:{from:'2026-10-01',to:'2026-10-07',processed_days:7,expected_days:7,rows:[{region:'GLOBAL',aircraft:1,missing_tail:1,missing_type:1,missing_both:1,unclassified:1,rotorcraft:0,rotorcraft_missing_tail:0,recovered_tail:0}]}});
      if(path==='/api/tools/lba/history')return route.fulfill({json:[]});
      if(path==='/api/capability-mappings')return route.fulfill({json:{rows:[],has_more:false}});
      return route.fulfill({status:503,json:{error:'Unused fixture API'}});
    });
    const base=process.env.TOOLS_MAINTENANCE_URL||'http://127.0.0.1:5089';
    await page.goto(base+'/#tools');
    await page.getByRole('heading',{name:'Maintenance approvals',exact:true}).waitFor();
    await page.getByText('No matching approval evidence is available in this dataset.').waitFor();
    assert.equal(requests.some(request=>request.path==='/api/tools/identity-gaps'),false);
    assert.equal(await page.getByRole('region',{name:'Identity coverage gaps'}).count(),0);
    const nav=page.getByRole('navigation',{name:'Tools pages'});
    await nav.getByRole('button',{name:'Activity ingestion',exact:true}).click();
    await page.getByRole('button',{name:'Open Fixture activity archive',exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Open Fixture aircraft register',exact:true}).count(),0);
    assert.equal(requests.some(request=>request.path==='/api/tools/identity-gaps'),false);
    await nav.getByRole('button',{name:'Reference data',exact:true}).click();
    await page.getByRole('button',{name:'Open Fixture airport reference',exact:true}).waitFor();
    assert.equal(await page.getByLabel('Data purpose').count(),0);
    assert.equal(requests.some(request=>request.path==='/api/tools/identity-gaps'),false);
    await page.reload();
    await page.getByRole('button',{name:'Open Fixture airport reference',exact:true}).waitFor();
    await nav.getByRole('button',{name:'Aircraft identity',exact:true}).click();
    await page.getByRole('button',{name:'Open Fixture aircraft register',exact:true}).waitFor();
    await page.getByRole('region',{name:'Identity coverage gaps'}).waitFor();
    assert.equal(requests.some(request=>request.path==='/api/tools/identity-gaps'),true);
    assert.equal(await page.getByRole('button',{name:'Open Fixture approval source',exact:true}).count(),0);
    const gapRequests=requests.filter(request=>request.path==='/api/tools/identity-gaps').length;
    await page.evaluate(()=>{window.location.hash='#tools/source/FIXTURE_PART145';});
    await page.getByRole('heading',{name:'Fixture approval source',exact:true}).waitFor();
    await page.getByRole('button',{name:'← Maintenance approvals',exact:true}).waitFor();
    assert.equal(await nav.getByRole('button',{name:'Maintenance approvals',exact:true}).getAttribute('aria-current'),'page');
    await page.getByText('Imported facility countries: GB.',{exact:true}).waitFor();
    await page.getByText('Imported company countries: FR.',{exact:false}).waitFor();
    assert.equal(requests.filter(request=>request.path==='/api/tools/identity-gaps').length,gapRequests);
    await page.reload();
    await page.getByRole('button',{name:'← Maintenance approvals',exact:true}).click();
    await page.getByRole('heading',{name:'Maintenance approvals',exact:true}).waitFor();
    await page.goBack();
    await page.getByRole('heading',{name:'Fixture approval source',exact:true}).waitFor();
    await page.getByRole('button',{name:'LBA import preview',exact:true}).click();
    await page.getByRole('heading',{name:'LBA · Fetch and preview',exact:true}).waitFor();
    await page.reload();
    await page.getByRole('heading',{name:'LBA · Fetch and preview',exact:true}).waitFor();
    await page.getByRole('button',{name:'Capability mappings',exact:true}).click();
    await page.getByRole('heading',{name:'Connect capability wording to aircraft types',exact:true}).waitFor();
    await page.goBack();
    await page.getByRole('heading',{name:'LBA · Fetch and preview',exact:true}).waitFor();
    await page.getByRole('button',{name:'Maintenance approvals',exact:true}).click();
    await page.getByRole('heading',{name:'Maintenance approvals',exact:true}).waitFor();
    for(const width of [760,390,320]){
      await page.setViewportSize({width,height:900});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`Overflow at ${width}px`);
    }
    assert.equal(requests.some(request=>request.method!=='GET'),false,'Navigation must not mutate data');
    assert.deepEqual(errors,[]);
    console.log('Tools maintenance navigation passed: separate purposes, identity request isolation, source inference, deep links, browser history, geography labels and mobile.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
