/* eslint-disable @typescript-eslint/no-require-imports */
// Run against the built SPA served locally; all data and tiles are synthetic.
// NODE_PATH may point to a temporary Playwright install outside production dependencies.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const url = process.env.MAP_TEST_URL || 'http://127.0.0.1:5089';
const stamp = Date.parse('2026-08-20T10:00:00Z') / 1000;
const approval = { id: 1, approval_number: 'TEST.145.01', approval_status: 'VALID', valid_from: '2020-01-01', valid_to: null, linked_site_ids: [1], source_url: 'https://example.test/approval', last_verified_at: null };
const capability = { ...approval, company_site_id: 1, capability_kind: 'AIRCRAFT', aircraft_type_code: 'EC45', model: 'H145', manufacturer: 'Airbus', limitation: 'Review the scope before booking maintenance.', rating_code: 'A3', is_base_maintenance: true, is_line_maintenance: false, match: 'SITE_MATCH' };
const base = { id: 1, company_name: 'Fixture Rotor Engineering', name: 'Prestwick base', airport_ident: 'EGPK', latitude_deg: 55.51, longitude_deg: -4.59, location_precision: 'AIRPORT_CENTROID', capabilities: [capability], approvals: [approval], match: 'SITE_MATCH' };
const flight = { dataset_day_id: 1, address: 'abcdef', segment_sequence: 1, takeoff_at: '2026-08-20T10:00:00Z', landing_at: '2026-08-20T11:00:00Z', origin_airport_ident: 'EGPF', destination_airport_ident: 'EGPK', origin_lat: 55.87, origin_lon: -4.43, destination_lat: 55.51, destination_lon: -4.59, confidence: 'MEDIUM', quality_flags: ['coverage_gap'], track_omitted: false, track: { segments: [[[stamp,55.87,-4.43,1000],[stamp+60,55.84,-4.46,1500]],[[stamp+500,55.65,-4.52,1500],[stamp+600,55.51,-4.59,600]]], retained_count: 4, input_count: 40, truncated: false } };
const data = { watch: { id:1,registration:'G-TEST' }, from:'2026-08-20',to:'2026-08-26',latest_processed:'2026-08-26',type_code:'EC45',type_codes:['EC45'],addresses:['abcdef'],processed_days:[{utc_date:'2026-08-20',derivation_version:'flight-visits-v2-tracks'}],expected_days:7,flights:[flight,{...flight,segment_sequence:2,track:null}],visits:[{airport_ident:'EGPK',airport_name:'Prestwick',latitude_deg:55.51,longitude_deg:-4.59,first_evidence_at:'2026-08-20T11:00:00Z',last_evidence_at:'2026-08-20T11:10:00Z',confidence:'MEDIUM',ground_observation_count:20}],sites:[base,{...base,id:2,name:'Company scope only',match:'COMPANY_MATCH',capabilities:[{...capability,company_site_id:null,match:'COMPANY_MATCH'}]}],flights_truncated:false,visits_truncated:false,sites_truncated:false,capabilities_truncated:false,approval_as_of:'2026-09-06' };

(async () => {
  const browser = await chromium.launch({ headless:true });
  try {
    const page = await browser.newPage({ viewport:{width:1440,height:1100} });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('https://basemaps.cartocdn.com/**', r => r.fulfill({ contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#e8edf0"/><path d="M0 0H256V256H0Z" fill="none" stroke="#ced8df"/></svg>' }));
    await page.route('**/api/**', async r => {
      const path = new URL(r.request().url()).pathname;
      let body;
      if (path === '/api/maintenance/watches') body=[data.watch,{id:2,registration:'G-EMPTY'}];
      else if (path.endsWith('map-config')) body={carto_key:'synthetic-browser-test'};
      else if (path === '/api/maintenance/watches/1/map') body=data;
      else if (path === '/api/maintenance/watches/2/map') body={...data,watch:{id:2,registration:'G-EMPTY'},flights:[],visits:[],type_code:null,type_codes:[],addresses:[],sites:data.sites.map(s=>({...s,match:'NO_RECORDED_MATCH',capabilities:[]}))};
      else return r.fulfill({status:503,contentType:'application/json',body:'{"error":"Other page APIs not used in this fixture"}'});
      return r.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    });
    await page.goto(url+'/#tracks');
    await page.getByText('G-TEST · EC45',{exact:true}).waitFor();
    await page.locator('.leaflet-container').waitFor();
    await page.evaluate(() => {
      const original = window.L.Map.prototype.setView;
      window.L.Map.prototype.setView = function (...args) {
        window.__fixtureMap = this;
        return original.apply(this,args);
      };
    });
    await page.getByRole('button', {name:/Fixture Rotor Engineering.*Prestwick base/}).click();
    await page.getByText('Marker is the airport centre, not the hangar.').waitFor();
    await page.getByLabel('Flight episode').selectOption('1:abcdef:1');
    await page.getByRole('button',{name:'Play',exact:true}).click();
    await page.getByRole('button',{name:'Pause',exact:true}).waitFor();
    await page.getByRole('button',{name:'Pause',exact:true}).click();
    await page.getByLabel('Flight playback position').fill('30');
    // Let Leaflet's zoom transition and React's effects finish before visual QA.
    await page.waitForTimeout(400);
    assert.equal(await page.evaluate(() => window.__fixtureMap.getBounds().contains([55.87,-4.43])),true,'Selecting a flight fits its route after selecting a base');
    assert.match(await page.locator('.map-playback').innerText(),/Last observed fix:/);
    await fs.mkdir('tmp/map-browser-results',{recursive:true});
    await page.evaluate(() => { document.documentElement.style.scrollBehavior='auto'; window.scrollTo(0,0); });
    await page.waitForFunction(() => window.scrollY === 0);
    await page.waitForTimeout(200);
    await page.screenshot({path:'tmp/map-browser-results/desktop.png',fullPage:true});
    await page.getByLabel('Type matches only (site or company)').check();
    await page.getByLabel('Find a base').fill('Company scope');
    assert.equal(await page.locator('.map-base-list button').count(),1);
    await page.getByLabel('Watched aircraft').selectOption('2');
    await page.getByText('G-EMPTY · Type unknown',{exact:true}).waitFor();
    await page.getByText('No flight episodes for this tail in the selected dates.',{exact:false}).waitFor();
    await page.getByLabel('Find a base').fill('');
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:'tmp/map-browser-results/mobile.png',fullPage:true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),false,'No horizontal overflow on mobile');
    assert.deepEqual(errors,[]);
    console.log('Flight map browser checks passed: tracks, playback, scopes, filtering, empty tail, mobile.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
