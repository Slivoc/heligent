/* eslint-disable @typescript-eslint/no-require-imports */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:5089/#tools/source/TAR1090_DB');
    await page.getByRole('button',{name:'Compare observed gaps',exact:true}).waitFor();
    await page.getByLabel('Comparison from (UTC)').fill('2026-08-20');
    await page.getByLabel('Comparison to (UTC)').fill('2026-08-20');
    await page.getByLabel('Missing identity field').selectOption('EITHER');
    await page.getByRole('button',{name:'Compare observed gaps',exact:true}).click();
    const fill=page.getByRole('button',{name:'Fill 1 identity',exact:true});
    await fill.waitFor();
    assert.equal(await page.getByLabel('Add matched helicopters to Maintenance Pulse').isChecked(),true);
    assert.equal(await page.getByLabel('Valid from (UTC)',{exact:true}).count(),0);
    assert.equal(await page.getByRole('button',{name:'Confirm assignment'}).count(),0);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.getByRole('region',{name:'Bulk identity fill'}).scrollIntoViewIfNeeded();
    await fs.mkdir('tmp/map-browser-results',{recursive:true});
    await page.screenshot({path:'tmp/map-browser-results/tar1090-bulk-mobile.png'});
    await fill.click();
    await page.getByRole('status').filter({hasText:'Filled 1 aircraft.'}).waitFor();
    await page.getByText('Filled for testing',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Fill 0 identities',exact:true}).isDisabled(),true);
    await page.setViewportSize({width:1440,height:1000});
    await page.getByRole('link',{name:'Open Maintenance Pulse'}).click();
    await page.getByRole('button',{name:'Open GPUMA details',exact:true}).waitFor();
    await page.getByRole('button',{name:'Open GPUMA details',exact:true}).click();
    const detail=page.getByRole('region',{name:'Aircraft maintenance details'});
    await detail.getByRole('heading',{name:/GPUMA.*Behaviour/}).waitFor();
    await detail.getByRole('cell',{name:'TEST · Test Airport',exact:true}).waitFor();
    assert.equal(await detail.locator('.activity-table tbody tr').count(),2);
    await page.screenshot({path:'tmp/map-browser-results/tar1090-pulse-desktop.png',fullPage:true});
    // Changing groups remains explicit. A plane can be filled but is not watched.
    await page.goto('http://127.0.0.1:5089/#tools/source/TAR1090_DB');
    await page.reload();
    await page.getByRole('button',{name:'Fixed wing 1',exact:true}).click();
    await page.getByRole('button',{name:'Fill 1 identity',exact:true}).click();
    await page.getByRole('status').filter({hasText:'Filled 1 aircraft.'}).waitFor();
    const watches=await (await page.request.get('http://127.0.0.1:5089/api/maintenance/watches')).json();
    assert.equal(watches.some(w=>w.registration==='GFIXED'),false);
    assert.deepEqual(errors,[]);
    console.log('Bulk fill browser checks passed: one click, automatic dates, immediate Pulse evidence, mobile and category scope.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
