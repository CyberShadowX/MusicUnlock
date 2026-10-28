/* Actual embedded WebView2 acceptance; dialogs are driven by the owned Win32 observer. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function runSavingAcceptance(page, config) {
  const base = page.url();
  const errors = [];
  const onError = error => errors.push(error.message);
  page.on('pageerror', onError);
  const report = {passed: false, scope: 'real native bridge and actual Save dialogs', saved: []};
  async function settled(phase = 'completed') {
    await page.waitForFunction(expected => document.querySelector('#saveStatus')?.dataset.phase === expected,
      phase, {timeout: 90000});
    const state = await page.evaluate(() => window.pywebview.api.get_save_state());
    assert.equal(state.phase, phase, state.message);
    return state;
  }
  async function waitFile(filename) {
    const end = Date.now() + 30000;
    while (Date.now() < end) {
      if (fs.existsSync(filename) && fs.statSync(filename).size > 0) return fs.statSync(filename).size;
      await sleep(100);
    }
    throw new Error(`Native save did not produce the expected extended filename: ${filename}`);
  }
  async function upload(files) {
    // CDP has message-size limits that do not apply to the real native file picker.
    for (let i = 0; i < files.length; i += 4) await page.locator('#picker').setInputFiles(files.slice(i, i + 4));
    await page.waitForFunction(count => document.querySelectorAll('.badge.ok,.badge.fail').length === count,
      files.length, {timeout: 180000});
    assert.equal(await page.locator('.badge.ok').count(), files.length, (await page.locator('.err-text').allTextContents()).join('\n'));
  }
  try {
    await page.locator('#format').selectOption('flac', {force: true});
    await upload(config.batch);
    assert.equal(await page.locator('.badge.fail').count(), 0);
    assert.equal(await page.locator('.badge.ok').count(), 34);
    await page.locator('#zipAll').click();
    report.batch = await settled();
    report.batch.bytes = await waitFile(config.zip);
    assert.equal(page.url(), base, 'Batch saving must preserve the main page');
    await page.locator('.dl').first().click();
    await settled('cancelled');
    assert.equal(await page.locator('#zipAll').isEnabled(), true, 'Cancellation must permit retry');
    report.cancelled = true;
    await page.locator('.dl').first().click();
    await settled();
    report.saved.push({format: 'flac', bytes: await waitFile(config.outputs.flac)});
    // Same 34 IDs survive repeated saves; every archive must stay complete.
    await page.locator('#zipAll').click();
    await settled();
    report.retryZipBytes = await waitFile(config.zipAgain);
    for (const format of ['mp3', 'm4a', 'wav', 'ogg']) {
      await page.locator('#clear').click();
      await page.waitForFunction(() => document.querySelectorAll('#list .item').length === 0);
      await page.locator('#format').selectOption(format, {force: true});
      await upload([config.matrix]);
      assert.match(await page.locator('.finfo').innerText(), new RegExp(format.toUpperCase()));
      await page.locator('.dl').first().click();
      await settled();
      report.saved.push({format, bytes: await waitFile(config.outputs[format])});
    }
    // The page sends IDs, never a filesystem destination. Expired/invalid input must be visible.
    const missing = await page.evaluate(() => window.pywebview.api.save_results(['000000000000'], false));
    assert.equal(missing.phase, 'failed');
    assert(missing.message, 'A missing result needs an actionable error');
    report.missingResult = missing;
    assert.equal(page.url(), base);
    assert.deepEqual(errors, []);
    report.passed = true;
    report.pageErrors = errors;
    await page.screenshot({path: config.screenshot, fullPage: true, animations: 'disabled'});
    return report;
  } finally {
    page.off('pageerror', onError);
    if (config.report) fs.writeFileSync(config.report, JSON.stringify(report, null, 2));
  }
}

async function main() {
  assert.equal(process.argv[2], '--config');
  const config = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
  const root = path.resolve(__dirname, '..');
  const cache = path.join(root, '.cache/npm/_npx');
  const playwright = require(fs.readdirSync(cache).map(n => path.join(cache, n, 'node_modules/playwright')).find(p => fs.existsSync(p)));
  const browser = await playwright.chromium.connectOverCDP(config.cdp, {noDefaults: true});
  try {
    const page = browser.contexts()[0].pages().find(p => p.url().startsWith(config.base + '/'));
    assert(page, 'The owned embedded page is missing');
    page.setDefaultTimeout(30000);
    if (config.menuScreenshot) {
      config.menu = await require('./format_palette_e2e.cjs').verifyPalette(page, config);
    }
    console.log(JSON.stringify(await runSavingAcceptance(page, config)));
  } finally { await browser.close(); }
}
module.exports = {runSavingAcceptance};
if (require.main === module) main().catch(e => {console.error(e.stack); process.exitCode = 1;});
