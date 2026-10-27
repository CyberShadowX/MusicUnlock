/* Drive the application's own WebView2 over CDP. Never launches a browser. */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function loadPlaywright() {
  const root = path.resolve(__dirname, '..');
  const candidates = [process.env.MUSICUNLOCK_PLAYWRIGHT_MODULE, 'playwright'];
  const cache = path.join(root, '.cache', 'npm', '_npx');
  if (fs.existsSync(cache)) {
    for (const directory of fs.readdirSync(cache).sort()) {
      candidates.push(path.join(cache, directory, 'node_modules', 'playwright'));
    }
  }
  for (const candidate of candidates.filter(Boolean)) {
    let resolved;
    try {
      resolved = require.resolve(candidate);
    } catch (error) {
      if (error.code === 'MODULE_NOT_FOUND') continue;
      throw error;
    }
    const module = require(resolved);
    assert(module.chromium, `No Chromium CDP client in ${resolved}`);
    return {chromium: module.chromium, module: resolved};
  }
  throw new Error('Playwright is unavailable. Set MUSICUNLOCK_PLAYWRIGHT_MODULE to an installed module; this verifier does not install packages.');
}

const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function waitForFile(filename) {
  const deadline = Date.now() + 30000;
  let lastSize = -1;
  while (Date.now() < deadline) {
    const size = fs.existsSync(filename) ? fs.statSync(filename).size : 0;
    if (size > 0 && size === lastSize) return size;
    lastSize = size;
    await sleep(200);
  }
  throw new Error(`Native Save dialog did not produce ${filename}`);
}

async function main() {
  const playwright = loadPlaywright();
  if (process.argv[2] === '--check') {
    console.log(JSON.stringify({playwright: playwright.module, browserLaunch: false}));
    return;
  }
  assert.equal(process.argv[2], '--config', 'Usage: node desktop_e2e.cjs --config <json>');
  const config = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
  assert.match(config.cdp, /^http:\/\/127\.0\.0\.1:\d+$/);
  assert.match(config.base, /^http:\/\/127\.0\.0\.1:\d+$/);
  // Preserve WebView2's host-owned download policy and its real save dialogs.
  const browser = await playwright.chromium.connectOverCDP(config.cdp, {noDefaults: true});
  let page;
  try {
    const contexts = browser.contexts();
    assert.equal(contexts.length, 1, 'Expected the existing WebView2 context');
    page = contexts[0].pages().find(candidate => candidate.url().startsWith(config.base + '/'));
    assert(page, 'Embedded application page is absent; no substitute browser will be opened');
    page.setDefaultTimeout(20000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.locator('#health.on').waitFor();
    assert.equal(await page.locator('#health').innerText(), '服务正常');
    const settings = await require('./settings_e2e.cjs').runSettingsAcceptance(page, {
      checkRealUpdates: true, expectedRealPhase: 'up_to_date',
      realScreenshot: path.join(path.dirname(config.domScreenshot), 'settings-real.png'),
    });
    await page.locator('#format').selectOption('flac');
    assert.equal(await page.locator('#format').inputValue(), 'flac');
    assert.equal(await page.locator('#embedCover').isChecked(), true);
    await page.locator('#picker').setInputFiles(config.fixtures.map(item => item.path));
    await page.waitForFunction(() => document.querySelectorAll('.badge.ok').length === 2 && document.querySelectorAll('.badge.fail').length === 1);
    const rows = page.locator('#list .item');
    assert.equal(await rows.count(), 3);
    const singleRow = rows.filter({hasText: config.fixtures[0].name});
    await singleRow.locator('.badge.ok').waitFor();
    assert.match(await singleRow.locator('.finfo').innerText(), /FLAC/);
    assert((await singleRow.locator('.finfo').innerText()).includes(config.fixtures[0].title));
    await singleRow.locator('.thumb img').waitFor();
    const failedRow = rows.filter({hasText: config.fixtures[2].name});
    assert((await failedRow.locator('.err-text').innerText()).trim(), 'Invalid input needs a readable UI error');

    // Native dialogs must handle saving; never redirect downloads through CDP.
    await singleRow.locator('a.dl').click();
    const singleBytes = await waitForFile(config.single);
    assert.equal(page.url(), config.base + '/', 'A single download replaced the embedded interface');
    await page.locator('#zipAll').click();
    const zipBytes = await waitForFile(config.zip);
    assert.equal(page.url(), config.base + '/', 'ZIP download replaced the embedded interface');
    await page.screenshot({path: config.domScreenshot, fullPage: true, animations: 'disabled'});
    assert.deepEqual(errors, [], 'The embedded UI emitted JavaScript errors');
    const report = {
      passed: true,
      transport: 'connectOverCDP to the application-owned WebView2; no browser launch',
      playwright: playwright.module,
      pageUrl: page.url(),
      title: await page.title(),
      outputFormat: await page.locator('#format').inputValue(),
      completed: 2,
      readableFailures: 1,
      nativeSavedBytes: {single: singleBytes, zip: zipBytes},
      pageErrors: errors,
      domScreenshot: config.domScreenshot,
      settings,
    };
    fs.writeFileSync(config.report, JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } catch (error) {
    if (page) await page.screenshot({path: config.failureScreenshot, fullPage: true}).catch(() => {});
    throw error;
  } finally {
    // A connected CDP client disconnects; the native window owns app lifetime.
    await browser.close();
  }
}

main().catch(error => {
  console.error(error.stack || String(error));
  process.exitCode = 1;
});
