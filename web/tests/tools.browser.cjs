/* eslint-disable @typescript-eslint/no-require-imports */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1400,height:1000}});
    const errors = [];
    page.on('pageerror',e=>errors.push(e.message));
    const preview = {query:'ADAC',fetched_at:'2026-09-07T12:00:00Z',source_sha256:'a'.repeat(64),page:'1-1 / 1',truncated:false,organisations:[{name:'Fixture Heliservice',approval:'DE.145.TEST',sites:[{street:'Base road',locality:'Test city',ratings:[{wording:'A3 (Base- und Line Maintenance)',models:['EC135','MBB-BK117']}]},{street:'Hospital road',locality:'Other city',ratings:[{wording:'A3 (nur Line Maintenance)',models:['EC135']}]}]}]};
    await page.route('**/api/**', async r=>{
      const path=new URL(r.request().url()).pathname;
      if(path==='/api/auth/session') return r.fulfill({json:{user:{role:'ANALYST'}}});
      if(path==='/api/tools/identity/preview') return r.fulfill({json:{token:'fixture-token',affected_days:3,first_day:'2026-09-01',last_day:'2026-09-05'}});
      if(path==='/api/tools/identity/assign') {
        assert.equal(r.request().postDataJSON().token,'fixture-token');
        assert.equal(r.request().postDataJSON().registration,'G-WSAS');
        return r.fulfill({json:{saved:true,message:'Identity saved. Reload the Stops map.'}});
      }
      if(path==='/api/tools/unidentified') return r.fulfill({json:{from:'2026-09-01',to:'2026-09-07',processed_days:6,expected_days:7,has_more:false,rows:[{address:'4082a2',days:3,first_day:'2026-09-01',last_day:'2026-09-05',positions:1200,hours:2.5,types:[],callsigns:['GWSAS'],current_registration:null}]}});
      if(path==='/api/tools/lba/preview') {
        assert.equal(r.request().method(),'POST');
        assert.equal(r.request().postDataJSON().query,'ADAC');
        return r.fulfill({json:preview});
      }
      return r.fulfill({status:503,json:{error:'Unused fixture API'}});
    });
    await page.goto('http://127.0.0.1:5089/#tools');
    await page.getByRole('button',{name:'Open LBA preview'}).click();
    await page.getByRole('button',{name:'Fetch preview',exact:true}).click();
    await page.getByText('Fixture Heliservice',{exact:true}).waitFor();
    assert.equal(await page.locator('.tools-card details').count(),2);
    await page.getByLabel('Show sites with base maintenance wording only').check();
    assert.equal(await page.locator('.tools-card details').count(),1);
    await page.locator('.tools-card summary').click();
    await page.getByText('MBB-BK117',{exact:true}).waitFor();
    const download=page.waitForEvent('download');
    await page.getByRole('button',{name:'Download preview JSON'}).click();
    assert.equal((await download).suggestedFilename(),'lba-preview.json');
    await fs.mkdir('tmp/map-browser-results',{recursive:true});
    await page.screenshot({path:'tmp/map-browser-results/tools-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:'tmp/map-browser-results/tools-mobile.png',fullPage:true});
    await page.getByRole('button',{name:'Hexes without tail numbers',exact:true}).click();
    await page.getByText('4082A2',{exact:true}).waitFor();
    await page.getByText('GWSAS',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Next',exact:true}).isDisabled(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:'tmp/map-browser-results/unidentified-mobile.png',fullPage:true});
    await page.setViewportSize({width:1400,height:1000});
    await page.screenshot({path:'tmp/map-browser-results/unidentified-desktop.png',fullPage:true});
    await page.getByRole('button',{name:'Assign tail number',exact:true}).click();
    await page.getByLabel('Tail number',{exact:true}).fill('G-WSAS');
    await page.getByLabel('Valid from (UTC)',{exact:true}).fill('2026-08-14');
    await page.getByLabel('Source URL (optional)',{exact:true}).fill('https://example.test/identity');
    await page.getByLabel('Notes (optional)',{exact:true}).fill('Verified registration for this period');
    await page.getByRole('button',{name:'Preview affected records'}).click();
    await page.getByRole('button',{name:'Confirm assignment'}).waitFor();
    await page.screenshot({path:'tmp/map-browser-results/identity-desktop.png',fullPage:true});
    await page.getByLabel('ICAO type (optional)',{exact:true}).fill('EC45');
    assert.equal(await page.getByRole('button',{name:'Confirm assignment'}).count(),0);
    await page.getByRole('button',{name:'Preview affected records'}).click();
    await page.getByRole('button',{name:'Confirm assignment'}).click();
    await page.getByRole('status').filter({hasText:'Identity saved'}).waitFor();
    assert.deepEqual(errors,[]);
    console.log('Tools browser checks passed: navigation, preview, scope filter, download, mobile.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
