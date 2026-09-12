/* eslint-disable @typescript-eslint/no-require-imports */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1100}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    // Only the external download is stubbed; preview, pagination and dated review
    // use the isolated PostgreSQL fixture through the real Flask API.
    await page.route('**/api/tools/tar1090/refresh',r=>r.fulfill({json:{status:'UNCHANGED'}}));
    await page.goto('http://127.0.0.1:5089/#tools/source/TAR1090_DB');
    await page.getByRole('button',{name:'Refresh tar1090 snapshot',exact:true}).click();
    await page.getByRole('button',{name:'Compare observed gaps',exact:true}).waitFor();
    await page.getByLabel('Comparison from (UTC)').fill('2026-08-20');
    await page.getByLabel('Comparison to (UTC)').fill('2026-08-20');
    await page.getByLabel('Missing identity field').selectOption('EITHER');
    await page.getByRole('button',{name:'Compare observed gaps',exact:true}).click();
    await page.getByRole('button',{name:'Review F00001',exact:true}).waitFor();
    await page.getByText('5 hexes with gaps',{exact:false}).waitFor();
    await page.getByRole('button',{name:'Ground vehicles 1',exact:true}).click();
    await page.getByText('Ground identifier; excluded from tail assignment').waitFor();
    assert.equal(await page.getByRole('button',{name:'Review F00002',exact:true}).count(),0);
    await page.getByRole('button',{name:'Conflicts 1',exact:true}).click();
    await page.getByText('Existing resolved tail differs',{exact:true}).waitFor();
    await page.getByRole('button',{name:'No source match 1',exact:true}).click();
    await page.getByText('F00005',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Helicopters / rotorcraft 1',exact:true}).click();
    await page.getByRole('button',{name:'Review F00001',exact:true}).click();
    const form=page.getByRole('region',{name:'Assign aircraft identity'});
    assert.equal(await form.getByLabel('Tail number',{exact:true}).inputValue(),'G-PUMA');
    assert.equal(await form.getByLabel('Tail number',{exact:true}).getAttribute('readonly'),'');
    assert.equal(await form.getByLabel('Valid from (UTC)',{exact:true}).inputValue(),'');
    await form.getByLabel('Valid from (UTC)',{exact:true}).fill('2026-08-20');
    await form.getByLabel('Valid to (UTC, inclusive)',{exact:true}).fill('2026-08-20');
    await form.getByLabel('Dated evidence / review notes').fill('Browser fixture: dated operator confirmation checked.');
    await form.getByRole('button',{name:'Preview affected records'}).click();
    await form.getByRole('button',{name:'Confirm assignment'}).waitFor();
    await fs.mkdir('tmp/map-browser-results',{recursive:true});
    await page.screenshot({path:'tmp/map-browser-results/tar1090-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:'tmp/map-browser-results/tar1090-mobile.png',fullPage:true});
    await form.getByRole('button',{name:'Confirm assignment'}).click();
    await page.getByRole('status').filter({hasText:'Identity saved'}).waitFor();
    await page.getByText('Identity already reviewed',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Compare observed gaps',exact:true}).click();
    await page.getByText('4 hexes with gaps',{exact:false}).waitFor();
    await page.reload();
    await page.getByText('4 hexes with gaps',{exact:false}).waitFor();
    assert.deepEqual(errors,[]);
    console.log('tar1090 browser checks passed: source refresh, real comparison/review, categories, reload and mobile.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
