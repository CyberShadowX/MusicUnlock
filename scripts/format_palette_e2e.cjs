'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

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
  const trigger = page.locator('#formatTrigger');
  const menu = page.locator('#formatListbox');
  await trigger.waitFor({state: 'visible'});
  assert.equal(await control.isHidden(), true, 'Native select stays a hidden source of truth');
  const original = await control.inputValue();
  const options = await control.evaluate(select => [...select.options].map(o => ({value: o.value, caption: o.text, disabled: o.disabled})));
  for (const value of ['', 'mp3', 'flac', 'm4a', 'wav', 'ogg']) assert(options.some(o => o.value === value));
  await control.selectOption('mp3', {force: true});
  assert.match(await trigger.innerText(), /MP3/);
  await trigger.focus();
  await page.keyboard.press('Enter');
  await menu.waitFor({state: 'visible'});
  assert.equal(await trigger.getAttribute('aria-expanded'), 'true');
  assert.equal(await menu.locator('[role="option"][aria-selected="true"]').innerText(), 'MP3');
  const data = await menu.evaluate(element => {
    const s = getComputedStyle(element);
    return {background: s.backgroundImage, foreground: s.color, border: s.borderWidth, outline: s.outlineStyle,
      selected: [...element.children].filter(o => o.getAttribute('aria-selected') === 'true').map(o => {
        const c = getComputedStyle(o); return {background: c.backgroundColor, foreground: c.color};
      }),
      active: [...element.children].filter(o => o.dataset.active === 'true').map(o => {
        const c = getComputedStyle(o); return {background: c.backgroundColor, foreground: c.color};
      })};
  });
  assert.match(data.background, /^linear-gradient\(/);
  assert.equal(data.border, '0px', 'App menu must not have the native white outer border');
  assert.equal(data.outline, 'none');
  assert.deepEqual(rgb(data.foreground), [238, 242, 250]);
  const stops = data.background.match(/rgba?\([^)]+\)/g);
  assert(stops?.length >= 2 && new Set(stops).size >= 2, 'A visible gradient needs distinct stops');
  data.gradientContrast = stops.map(stop => contrast(data.foreground, stop));
  assert(data.gradientContrast.every(ratio => ratio >= 4.5));
  for (const item of [...data.selected, ...data.active]) assert(contrast(item.foreground, item.background) >= 4.5);
  if (config.menuScreenshot) await page.screenshot({path: config.menuScreenshot, animations: 'disabled'});
  // Selection follows the native value and the existing conversion state.
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  assert.equal(await control.inputValue(), 'flac');
  assert.equal(await page.evaluate(() => state.format), 'flac');
  assert.equal(await menu.isHidden(), true);
  assert.equal(await trigger.evaluate(e => e === document.activeElement), true);
  await trigger.click();
  await menu.locator('[role="option"]').filter({hasText: /^WAV$/}).click();
  assert.equal(await control.inputValue(), 'wav');
  await trigger.click();
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('Escape');
  assert.equal(await control.inputValue(), 'wav', 'Escape must not commit a highlighted value');
  await trigger.click();
  await page.locator('#health').click();
  assert.equal(await menu.isHidden(), true);
  await trigger.focus();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');
  assert.equal(await menu.isHidden(), true);
  assert.equal(await trigger.evaluate(e => e === document.activeElement), false, 'Tab must leave the control');
  await control.selectOption(original, {force: true});
  return {passed: true, renderedByApp: true, keyboard: true, outsideClick: true, nativeValueRetained: true, options, ...data};
}

async function main() {
  assert.equal(process.argv[2], '--config');
  const config = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
  const root = path.resolve(__dirname, '..');
  const cache = path.join(root, '.cache/npm/_npx');
  const playwright = require(fs.readdirSync(cache).map(n => path.join(cache, n, 'node_modules/playwright')).find(p => fs.existsSync(p)));
  const browser = await playwright.chromium.connectOverCDP(config.cdp, {noDefaults: true});
  let page;
  try {
    page = browser.contexts()[0].pages().find(p => p.url().startsWith('http://127.0.0.1:'));
    assert(page);
    if (config.hostileLayer) await page.evaluate(() => {
      const style = document.createElement('style'); style.id = 'qa-upstream-palette-layer';
      style.textContent = '@layer qa-upstream {select#format,select#format option,#formatTrigger,#formatListbox,#formatListbox [role="option"] {background:white!important;color:white!important;-webkit-text-fill-color:white!important;color-scheme:light!important;border:1px solid white!important}}';
      document.head.appendChild(style);
    });
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
if (require.main === module) main().catch(e => {console.error(e.stack); process.exitCode = 1;});
