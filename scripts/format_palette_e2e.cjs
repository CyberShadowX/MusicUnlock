'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function rgb(text) {
  const numbers = text.match(/[\d.]+/g)?.map(Number);
  assert(numbers && numbers.length >= 3 && (numbers.length === 3 || numbers[3] === 1), `Opaque color required: ${text}`);
  return numbers.slice(0, 3);
}
function luminance(color) {
  const linear = rgb(color).map(value => value / 255).map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}
function contrast(a, b) {
  const values = [luminance(a), luminance(b)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

async function verifyPalette(page, config = {}) {
  assert.match(await page.title(), /^MusicUnlock/);
  const control = page.locator('select#format');
  const original = await control.inputValue();
  const data = await control.evaluate(select => {
    const style = getComputedStyle(select);
    return {value: select.value, background: style.backgroundColor, foreground: style.color, scheme: style.colorScheme,
      options: [...select.options].map(option => {
        const value = getComputedStyle(option);
        return {value: option.value, caption: option.text, selected: option.selected,
          background: value.backgroundColor, foreground: value.color};
      })};
  });
  assert.deepEqual(rgb(data.background), [16, 20, 31]);
  assert.deepEqual(rgb(data.foreground), [238, 242, 250]);
  assert(data.scheme.split(/\s+/).includes('dark'));
  for (const value of ['', 'mp3', 'flac', 'm4a', 'wav', 'ogg']) {
    assert(data.options.some(option => option.value === value), `Missing existing format: ${value}`);
  }
  data.contrast = contrast(data.foreground, data.background);
  assert(data.contrast >= 4.5);
  for (const option of data.options) {
    assert.deepEqual(rgb(option.background), option.selected ? [38, 53, 82] : [16, 20, 31]);
    assert.deepEqual(rgb(option.foreground), [238, 242, 250]);
    option.contrast = contrast(option.foreground, option.background);
    assert(option.contrast >= 4.5, `Unreadable option ${option.caption}`);
  }
  await control.selectOption('mp3');
  assert.equal(await control.inputValue(), 'mp3');
  await control.selectOption(original);
  if (config.popupReady && config.popupCaptured) {
    await control.scrollIntoViewIfNeeded();
    await control.click();
    await sleep(150);
    data.nativeOpen = await control.evaluate(select => CSS.supports('selector(select:open)') ? select.matches(':open') : null);
    if (data.nativeOpen !== null) assert.equal(data.nativeOpen, true, 'Native menu did not open');
    fs.writeFileSync(config.popupReady, JSON.stringify(data));
    try {
      const deadline = Date.now() + 15000;
      let captured;
      while (Date.now() < deadline) {
        try { captured = JSON.parse(fs.readFileSync(config.popupCaptured, 'utf8')); } catch {}
        if (captured) break;
        await sleep(50);
      }
      assert(captured, 'Native popup capture timed out');
      assert(!captured.error, captured.error);
      data.capture = captured;
    } finally {
      await page.keyboard.press('Escape');
    }
  }
  return {passed: true, ...data};
}

async function main() {
  assert.equal(process.argv[2], '--config');
  const config = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
  const root = path.resolve(__dirname, '..');
  const cache = path.join(root, '.cache/npm/_npx');
  const playwright = require(fs.readdirSync(cache).map(name => path.join(cache, name, 'node_modules/playwright')).find(file => fs.existsSync(file)));
  const browser = await playwright.chromium.connectOverCDP(config.cdp, {noDefaults: true});
  let page;
  try {
    page = browser.contexts()[0].pages().find(page => page.url().startsWith('http://127.0.0.1:'));
    assert(page);
    if (config.hostileLayer) {
      await page.evaluate(() => {
        const style = document.createElement('style');
        style.id = 'qa-upstream-palette-layer';
        style.textContent = '@layer qa-upstream {select#format,select#format option {background:white!important;color:white!important;-webkit-text-fill-color:white!important;color-scheme:light!important}}';
        document.head.appendChild(style);
      });
    }
    const report = await verifyPalette(page, config);
    report.hostileLayer = !!config.hostileLayer;
    fs.writeFileSync(config.report, JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally {
    if (config.hostileLayer && page) await page.evaluate(() => document.getElementById('qa-upstream-palette-layer')?.remove()).catch(() => {});
    await browser.close();
  }
}
module.exports = {verifyPalette};
if (require.main === module) main().catch(error => { console.error(error.stack); process.exitCode = 1; });
