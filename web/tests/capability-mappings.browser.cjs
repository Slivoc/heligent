/* eslint-disable @typescript-eslint/no-require-imports */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');

(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    let role = 'ANALYST';
    const cap = {id:1,company_name:'Example Helicopter Maintenance',site_name:'Example airport base',approval_number:'TEST.145.01',approval_status:'VALID',limitation:'Aircraft A3 HELICOPTERS MBB-BK117 SERIES',active:true,company_active:true,site_active:true,is_base_maintenance:true,is_line_maintenance:true,mapping_count:0};
    const detail = {capability:cap,source_snapshot:{limitation:cap.limitation},mappings:[],history:[],history_truncated:false};
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      let body;
      if (path === '/api/auth/session') body={user:{role}};
      else if (path === '/api/capability-mappings') body={rows:[cap],has_more:false};
      else if (path === '/api/capability-mappings/1') {
        if (route.request().method() === 'POST') {
          const payload = route.request().postDataJSON();
          assert.equal(route.request().headers()['x-requested-with'],'HeligentAdmin');
          assert.deepEqual(payload.evidence_snapshot,detail.source_snapshot);
          detail.mappings=[{...payload,id:1,revision:payload.revision+1,reviewed_by:'analyst@example.test',reviewed_at:'2026-09-07T12:00:00Z',stale:false}];
          cap.mapping_count=1;
        }
        body=detail;
      } else return route.fulfill({status:503,contentType:'application/json',body:'{"error":"Unused fixture API"}'});
      return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    });
    await page.goto((process.env.MAP_TEST_URL || 'http://127.0.0.1:5089')+'/#capability-mappings');
    await page.getByRole('button',{name:/Example Helicopter Maintenance/}).click();
    await page.getByLabel('ICAO aircraft type').fill('EC45');
    await page.getByLabel('Evidence URL').fill('https://example.test/approval');
    await page.getByLabel('Evidence and review notes').fill('Family only; exact variant not yet established.');
    await page.getByRole('button',{name:'Save mapping',exact:true}).click();
    await page.getByText('Mapping saved.',{exact:false}).waitFor();
    assert.equal(detail.mappings[0].match_level,'POSSIBLE_FAMILY');
    await page.getByRole('button',{name:'Edit / re-review / withdraw EC45'}).click();
    await page.getByLabel('Match level').selectOption('REVIEWED_TYPE');
    assert.equal(await page.getByRole('checkbox',{name:/I verified/}).isChecked(),false);
    await page.getByLabel('Restricted variants').fill('C-2 only');
    await page.getByRole('button',{name:'Save mapping',exact:true}).click();
    await page.getByText('Restricted variants: C-2 only.',{exact:false}).waitFor();
    assert.equal(detail.mappings[0].revision,2);
    await fs.mkdir('tmp/map-browser-results',{recursive:true});
    await page.screenshot({path:'tmp/map-browser-results/mappings-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),true);
    await page.screenshot({path:'tmp/map-browser-results/mappings-mobile.png',fullPage:true});
    role='VIEWER';
    await page.reload();
    await page.getByRole('button',{name:/Example Helicopter Maintenance/}).click();
    await page.getByText('Read-only. An analyst or administrator',{exact:false}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Save mapping',exact:true}).count(),0);
    assert.deepEqual(errors,[]);
    console.log('Capability mapping browser checks passed (save, revision, restricted scope, responsive layout, viewer).');
  } finally { await browser.close(); }
})().catch(e => { console.error(e);process.exitCode=1; });
