/* Settings acceptance on a supplied page. Native mutation methods are always faked. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function runSettingsAcceptance(page, config = {}) {
  assert.match(await page.title(), /^MusicUnlock/);
  const toggle = page.locator('#settingsToggle');
  const panel = page.locator('#updatePanel');
  if (await panel.isVisible()) await toggle.click();
  await toggle.focus();
  await page.keyboard.press('Enter');
  await panel.waitFor({state: 'visible'});
  assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(await page.locator('#updateDetails').getAttribute('open'), null);
  let realCheck;
  let realState;
  if (config.checkRealUpdates) {
    await page.waitForFunction(() => !document.querySelector('#checkUpdates').disabled);
    await page.locator('#checkUpdates').click();
    await page.waitForFunction(() => ['up_to_date', 'available', 'failed'].includes(document.querySelector('#updatePanel').dataset.phase), null, {timeout: 60000});
    realCheck = await page.locator('#updateStatus').innerText();
    if (config.expectedRealPhase) assert.equal(await panel.getAttribute('data-phase'), config.expectedRealPhase);
    realState = await page.evaluate(() => window.pywebview.api.get_update_state());
    if (config.realScreenshot) await page.screenshot({path: config.realScreenshot, fullPage: true, animations: 'disabled'});
  }
  const samples = [];
  // This is a test double at the native boundary, never a production test flag.
  await page.evaluate(() => {
    window.__settingsAcceptance = {bridge: window.pywebview, items: state.items, busy: state.busy,
      saves: window.MusicUnlockSaves, saving: false, calls: []};
    window.MusicUnlockSaves = {isBusy: () => window.__settingsAcceptance.saving};
    const fixture = {
      app_name: 'MusicUnlock', phase: 'idle', message: '尚未检测更新',
      current_revision: 'a'.repeat(40), latest_revision: '', current_build: '2026-10-27 local',
      latest_summary: '', checked_at: '', build_ready: false, source_workspace: 'F:\\AI\\AIProjects\\Music Unlock',
      can_update: false, can_restart: false, log_path: '',
    };
    window.__settingsAcceptance.fixture = fixture;
    window.pywebview = {api: Object.fromEntries(['get_update_state', 'check_updates', 'start_update', 'choose_source_workspace', 'restart_after_update'].map(name => [name, async () => {
      window.__settingsAcceptance.calls.push(name);
      return {...window.__settingsAcceptance.fixture};
    }]))};
    window.dispatchEvent(new Event('pywebviewready'));
  });
  async function show(phase, extra = {}) {
    await page.evaluate(({phase, extra}) => {
      Object.assign(window.__settingsAcceptance.fixture, {phase, can_update: false, can_restart: false, build_ready: false, ...extra});
    }, {phase, extra});
    // Reopening asks the real UI for a fresh native snapshot.
    await toggle.click();
    await toggle.click();
    await page.waitForFunction(expected => document.querySelector('#updatePanel').dataset.phase === expected, phase);
    samples.push(phase);
  }
  try {
    await show('idle');
    assert.equal(await page.locator('#updateSoftware').isDisabled(), true);
    assert.equal(await page.evaluate(() => window.__settingsAcceptance.calls.includes('check_updates')), false, 'Opening Settings must not contact GitHub');
    await page.locator('#checkUpdates').click();
    await page.waitForFunction(() => window.__settingsAcceptance.calls.includes('check_updates'));
    await page.evaluate(() => {
      const api = window.pywebview.api;
      window.__settingsAcceptance.check = api.check_updates;
      api.check_updates = async () => { throw new Error('测试：连接暂时中断'); };
    });
    await page.locator('#checkUpdates').click();
    await page.waitForFunction(() => document.querySelector('#updateStatus').textContent.includes('连接暂时中断'));
    assert.equal(await page.locator('#checkUpdates').isEnabled(), true, 'A rejected bridge promise must allow retry');
    await page.evaluate(() => { window.pywebview.api.check_updates = window.__settingsAcceptance.check; });
    await show('up_to_date', {message: '已是最新版本', latest_revision: 'a'.repeat(40), checked_at: '2026-10-27T12:00:00Z'});
    assert.match(await page.locator('#currentRevision').innerText(), /aaaaaaaaaaaa/);
    await show('available', {message: '发现可用更新', latest_revision: 'b'.repeat(40), latest_summary: '<img src=x onerror="window.__injected=true">', can_update: true});
    assert.equal(await page.locator('#updateSoftware').isEnabled(), true);
    assert.equal(await page.locator('#latestSummary img').count(), 0);
    await page.locator('#updateSoftware').click();
    await page.waitForFunction(() => window.__settingsAcceptance.calls.includes('start_update'));
    for (const phase of ['checking', 'preparing', 'validating', 'building', 'installing']) {
      await show(phase);
      assert.equal(await page.locator('#checkUpdates').isDisabled(), true);
      assert.equal(await page.locator('#updateSoftware').isDisabled(), true);
      assert.equal(await page.locator('#restartUpdate').isDisabled(), true);
    }
    const installCount = await page.evaluate(() => {
      const before = state.items.length;
      addFiles([new File(['test'], 'blocked.ncm')]);
      return state.items.length - before;
    });
    assert.equal(installCount, 0, 'Installing must prevent new conversion work');
    await show('failed', {message: '检测失败：网络不可用'});
    assert.match(await page.locator('#updateStatus').innerText(), /网络/);
    assert.equal(await page.locator('#checkUpdates').isEnabled(), true);
    await show('ready', {message: '更新已构建完成', build_ready: true, can_restart: true});
    for (const phase of ['available', 'up_to_date', 'failed']) {
      await show(phase, {build_ready: true, can_restart: true,
        ready_revision: 'b'.repeat(40), latest_revision: 'c'.repeat(40)});
      assert.equal(await page.locator('#restartUpdate').isVisible(), true, 'A verified candidate must remain restartable after rechecking');
      assert.equal(await page.locator('#restartUpdate').isEnabled(), true);
      assert.match(await page.locator('#restartHint').innerText(), /bbbbbbbbbbbb/, 'Show the built revision separately from the newer checked tip');
    }
    await show('ready', {build_ready: true, can_restart: true, ready_revision: 'b'.repeat(40)});
    await page.evaluate(() => { state.busy = 1; render(); });
    assert.equal(await page.locator('#restartUpdate').isDisabled(), true);
    await page.evaluate(() => { state.busy = 0; state.items = [{file: new File(['x'], 'waiting.ncm'), status: 'wait', tags: {}, note: '', plat: platOf('waiting.ncm')}]; render(); });
    assert.equal(await page.locator('#restartUpdate').isDisabled(), true);
    await page.evaluate(() => { state.items = []; render(); });
    assert.equal(await page.locator('#restartUpdate').isEnabled(), true);
    await page.evaluate(() => {
      window.__settingsAcceptance.saving = true;
      window.dispatchEvent(new Event('musicunlock:savestate'));
    });
    assert.equal(await page.locator('#restartUpdate').isDisabled(), true, 'Active saving must prevent update restart');
    assert.match(await page.locator('#restartHint').innerText(), /保存/);
    await page.evaluate(() => {
      window.__settingsAcceptance.saving = false;
      window.dispatchEvent(new Event('musicunlock:savestate'));
    });
    assert.equal(await page.locator('#restartUpdate').isEnabled(), true, 'Completed saving must release restart gate');
    await page.locator('#restartUpdate').click();
    await page.waitForFunction(() => window.__settingsAcceptance.calls.includes('restart_after_update'));
    await show('idle');
    await page.locator('#updateDetails summary').click();
    await page.locator('#chooseWorkspace').click();
    await page.waitForFunction(() => window.__settingsAcceptance.calls.includes('choose_source_workspace'));
    await page.evaluate(() => {
      state.items = [{file: new File(['x'], 'literal.ncm'), status: 'ok', tags: {title: '<img src=x onerror="window.__injected=true">'}, note: '<svg onload="window.__injected=true">', plat: platOf('literal.ncm')}];
      render();
    });
    assert.equal(await page.locator('.finfo img, .finfo svg').count(), 0);
    assert.match(await page.locator('.finfo').innerText(), /<svg/);
    assert.equal(await page.evaluate(() => !!window.__injected), false);
    if (config.screenshot) await page.screenshot({path: config.screenshot, fullPage: true, animations: 'disabled'});
    await page.locator('#closeSettings').focus();
    await page.keyboard.press('Escape');
    assert.equal(await panel.isVisible(), false);
    assert.equal(await toggle.evaluate(element => element === document.activeElement), true);
    const callsBefore = await page.evaluate(() => window.__settingsAcceptance.calls.length);
    await page.waitForTimeout(1700);
    assert.equal(await page.evaluate(() => window.__settingsAcceptance.calls.length), callsBefore, 'Idle closed settings must not poll');
    return {passed: true, realCheck, realState, phases: samples, nativeMutations: 'fake bridge only', queueRestartGate: true, metadataEscaped: true};
  } finally {
    await page.evaluate(() => {
      const saved = window.__settingsAcceptance;
      window.pywebview = saved.bridge;
      window.MusicUnlockSaves = saved.saves;
      state.items = saved.items;
      state.busy = saved.busy;
      render();
      window.dispatchEvent(new Event('pywebviewready'));
      delete window.__settingsAcceptance;
    });
    if (await panel.isVisible()) await toggle.click();
  }
}

async function unit() {
  const root = path.resolve(__dirname, '..');
  const cache = path.join(root, '.cache/npm/_npx');
  const modules = fs.readdirSync(cache).map(item => path.join(cache, item, 'node_modules/playwright'));
  const playwright = require(modules.find(item => fs.existsSync(item)));
  const executablePath = [process.env.MUSICUNLOCK_UI_BROWSER, 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(item => item && fs.existsSync(item));
  assert(executablePath, 'An installed Edge executable is required; no browser downloads');
  const profile = fs.mkdtempSync(path.join(root, '.cache', 'ui-profile-'));
  const context = await playwright.chromium.launchPersistentContext(profile, {executablePath, headless: true, viewport: {width: 1100, height: 850}});
  try {
    const page = context.pages()[0];
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/health') return route.fulfill({json: {ok: true}});
      if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync(path.join(root, 'web/static/index.html'))});
      const file = {'/static/settings.js': 'settings.js', '/static/settings.css': 'settings.css',
        '/static/format_picker.js': 'format_picker.js', '/static/downloads.js': 'downloads.js'}[url.pathname];
      if (file) return route.fulfill({contentType: file.endsWith('.js') ? 'text/javascript' : 'text/css', body: fs.readFileSync(path.join(root, 'web/static', file))});
      return route.abort();
    });
    await page.goto('http://musicunlock-ui.invalid/');
    page.setDefaultTimeout(4000);
    await page.locator('#settingsToggle').click();
    assert.equal(await page.locator('#checkUpdates').isDisabled(), true, 'Source-browser actions unavailable before native readiness');
    await page.locator('#settingsToggle').click();
    const result = await runSettingsAcceptance(page);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({...result, pageErrors: errors, scope: 'headless DOM with fake routes/native bridge; no app launched'}));
  } finally {
    await context.close();
    fs.rmSync(profile, {recursive: true, force: true});
  }
}

module.exports = {runSettingsAcceptance};
if (require.main === module) unit().catch(error => { console.error(error.stack); process.exitCode = 1; });
