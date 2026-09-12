/* eslint-disable @typescript-eslint/no-require-imports */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');

(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1100}});
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    const base=process.env.SOURCE_TOOLS_URL || 'http://127.0.0.1:5089';
    await page.goto(base+'/#tools');
    await page.getByRole('button',{name:'Open tar1090-db',exact:true}).waitFor();
    await page.locator('.gap-table').waitFor();
    assert.equal(await page.getByText('Gap report unavailable:',{exact:false}).count(),0);
    await fs.mkdir('tmp/source-tools-results',{recursive:true});
    await page.screenshot({path:'tmp/source-tools-results/overview-desktop.png',fullPage:true});
    await page.getByLabel('Data purpose').selectOption('IDENTITY');
    await page.getByLabel('Source region').selectOption('EU');
    assert.equal(await page.getByRole('button',{name:'Open United States · FAA',exact:true}).count(),0);
    await page.getByRole('button',{name:'Open tar1090-db',exact:true}).click();
    await page.getByRole('heading',{name:'tar1090-db',exact:true}).waitFor();
    await page.getByLabel('Coverage gaps / next steps').fill('Browser test: validate the CSV columns and retain the upstream version.');
    await page.getByLabel('Review interval (days)').fill('14');
    await page.getByRole('button',{name:'Save source notes',exact:true}).click();
    await page.getByText('Saved by', {exact:false}).waitFor();
    await page.reload();
    await page.getByRole('heading',{name:'tar1090-db',exact:true}).waitFor();
    assert.match(await page.getByLabel('Coverage gaps / next steps').inputValue(),/Browser test/);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:'tmp/source-tools-results/source-mobile.png',fullPage:true});
    await page.getByRole('button',{name:'Sources & coverage',exact:true}).click();
    await page.getByRole('button',{name:'Open HexDB',exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:'tmp/source-tools-results/overview-mobile.png',fullPage:true});
    await page.getByRole('button',{name:'Open HexDB',exact:true}).click();
    await page.getByLabel('Six-character hex').fill('4082a2');
    await page.getByRole('button',{name:'Look up in HexDB',exact:true}).click();
    await page.getByText('Source record found', {exact:false}).waitFor();
    await page.getByText('Cached result', {exact:false}).waitFor();
    await page.screenshot({path:'tmp/source-tools-results/lookup-mobile.png',fullPage:true});
    await page.getByRole('button',{name:'Sources & coverage',exact:true}).click();
    await page.locator('.gap-table').waitFor();
    const globalRow=page.locator('.gap-table tbody tr').first();
    if(!await globalRow.getByRole('button',{name:'Review types'}).isDisabled()) {
      await globalRow.getByRole('button',{name:'Review types'}).click();
      await page.getByRole('heading',{name:'Aircraft identity review',exact:true}).waitFor();
      assert.equal(await page.getByLabel('Identity gap').inputValue(),'TYPE');
      assert.equal(await page.getByRole('combobox',{name:/^Aircraft/}).inputValue(),'ALL');
    }
    await page.getByRole('button',{name:'Sources & coverage',exact:true}).click();
    await page.getByRole('button',{name:'Open LBA preview',exact:true}).click();
    await page.getByRole('heading',{name:'LBA · Fetch and preview',exact:true}).waitFor();
    // The known-missing API state must be distinct from an empty source inventory.
    await page.route('**/api/tools/identity-gaps*',r=>r.fulfill({status:503,json:{error:'Coverage temporarily unavailable'}}));
    await page.getByRole('button',{name:'Back to sources & coverage',exact:true}).click();
    await page.getByText('Gap report unavailable:',{exact:false}).waitFor();
    await page.getByRole('button',{name:'Open tar1090-db',exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    console.log('Source Tools browser checks passed: inventory, source filters, notes persistence, deep links, cached lookup, gap navigation, failure states and mobile layout.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
