/* eslint-disable @typescript-eslint/no-require-imports */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const url=process.env.MAP_TEST_URL || 'http://127.0.0.1:5089';
const flight={address:'abcdef',dataset_day_id:2,segment_sequence:1,takeoff_at:'2026-09-09T11:28:00Z',landing_at:'2026-09-09T11:34:00Z',origin_airport_ident:'EGSH',destination_airport_ident:'EGSH',observed_airborne_seconds:359,elapsed_airborne_seconds:360,confidence:'MEDIUM',quality_flags:['OPEN_START']};
const boundary={first_evidence_at:'2026-08-20T12:34:00Z',last_evidence_at:'2026-08-20T12:36:00Z',ground_observation_count:43,ground_time_seconds:40,proximity_observation_count:140,arrival_evidence:'TRACK_ENTRY_LOW_SLOW_PROXIMITY',departure_evidence:null};
const gap={key:'closed',address:'abcdef',airport_ident:'EGSH',airport_name:'Norwich Airport',started_at:'2026-08-20T12:36:00Z',ended_at:'2026-09-09T11:27:00Z',elapsed_seconds:1723860,open_end:false,before:boundary,after:{...boundary,ground_observation_count:21,ground_time_seconds:32},ground_at_both_boundaries:true,processed_days:21,expected_days:21,intermediate_days:19,days_with_other_aircraft:19,days_with_other_helicopters:19,min_other_aircraft:3,mro_companies:[],previous_flight:null,next_flight:flight};
const data={registration:'GTEST',from:'2026-08-01',to:'2026-09-09',latest_processed:'2026-09-09',type_codes:['A139'],addresses:['abcdef'],coverage:{processed_days:40,expected_days:40},calendar:Array.from({length:40},(_,i)=>({utc_date:new Date(Date.UTC(2026,7,1+i)).toISOString().slice(0,10),processed:true,observed:i<20 || i===39})),summary:{observed_days:21,quiet_windows:1,median_quiet_seconds:1723860,observations:1000,ground_observations:64,observed_ground_seconds:72},bases:[{airport_ident:'EGSH',airport_name:'Norwich Airport',windows:1}],intervals:[gap,{...gap,key:'open',started_at:'2026-09-09T11:37:00Z',ended_at:'2026-09-10T00:00:00Z',elapsed_seconds:44580,open_end:true,after:null,next_flight:null,ground_at_both_boundaries:false,expected_days:1,processed_days:1,intermediate_days:0}],flights:[flight],flights_truncated:false,events:[],events_truncated:false};

(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async r=>{
   const req=r.request(),path=new URL(req.url()).pathname;requests.push({path,method:req.method()});
   if(path==='/api/maintenance/watches')return r.fulfill({json:[]});
   if(path.endsWith('/map-config'))return r.fulfill({json:{carto_key:''}});
   if(path==='/api/maintenance/aircraft/GTEST/history')return r.fulfill({json:data});
   if(path==='/api/maintenance/aircraft/GEMPTY/history')return r.fulfill({json:{...data,registration:'GEMPTY',summary:{...data.summary,observed_days:0},intervals:[],flights:[],bases:[],addresses:[]}});
   if(path==='/api/maintenance/aircraft/GERROR/history')return r.fulfill({status:400,json:{error:'Choose an ordered UTC date range of at most 366 days'}});
   if(path==='/api/maintenance/aircraft/GTEST/map')return r.fulfill({json:{registration:'GTEST',watch:null,from:'2026-08-10',to:'2026-09-09',latest_processed:'2026-09-09',type_code:'A139',type_codes:['A139'],addresses:['abcdef'],processed_days:[],expected_days:31,stops:[],sites:[],stops_truncated:false,sites_truncated:false,capabilities_truncated:false,approval_as_of:'2026-09-13'}});
   return r.fulfill({status:503,json:{error:'Unrelated fixture API'}});
  });
  await page.goto(url+'/#aircraft');
  await page.getByLabel('Aircraft registration',{exact:true}).fill('g-test');
  await page.getByRole('button',{name:'Look up aircraft',exact:true}).click();
  await page.getByRole('heading',{name:'GTEST A139',exact:true}).waitFor();
  assert.equal(await page.getByLabel('Inspect a quiet window').inputValue(),'closed');
  await page.getByText('19/19 intervening dates (19 with helicopters)',{exact:true}).waitFor();
  await page.getByText('40 sec / 32 sec',{exact:true}).waitFor();
  await page.getByText('EGSH → EGSH',{exact:true}).first().waitFor();
  await fs.mkdir('tmp/history-browser-results',{recursive:true});
  await page.screenshot({path:'tmp/history-browser-results/desktop.png',fullPage:true});
  await page.getByLabel('Inspect a quiet window').selectOption('open');
  await page.getByText('No later sighting in this range.',{exact:false}).waitFor();
  await page.getByLabel('Inspect a quiet window').selectOption('closed');
  await page.getByRole('link',{name:'Open Stops map · last 31 days',exact:true}).click();
  await page.getByText('GTEST · A139',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('Watched aircraft').count(),0,'No watchlist required');
  await page.getByRole('link',{name:'View overnight and longer stays',exact:true}).click();
  await page.getByRole('heading',{name:'GTEST A139',exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'tmp/history-browser-results/mobile.png',fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'No mobile overflow');
  await page.getByLabel('Aircraft registration',{exact:true}).fill('g-empty');
  await page.getByRole('button',{name:'Look up aircraft',exact:true}).click();
  await page.getByRole('heading',{name:'No aircraft observations in this range',exact:true}).waitFor();
  assert.equal(await page.getByLabel('Inspect a quiet window').count(),0,'No stale evidence after a new lookup');
  await page.getByLabel('Aircraft registration',{exact:true}).fill('g-error');
  await page.getByRole('button',{name:'Look up aircraft',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'at most 366 days'}).waitFor();
  assert(requests.every(r=>r.method==='GET'),'Tail lookups are read-only');
  assert.deepEqual(errors,[]);
  console.log('Aircraft history checks passed: arbitrary registration, evidence, open interval, empty/error states, map links, no watch writes, mobile.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
