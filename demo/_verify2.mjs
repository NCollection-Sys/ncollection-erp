import { chromium } from 'playwright';
const BASE = 'http://localhost:4173';
const OUT = '/private/tmp/claude-501/-Users-omaressam-Documents-ERP-Sys-ncollection-erp/a5f857be-2686-4a88-8451-55e59b087c6a/scratchpad';
const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));

// login (localStorage may hold ar from before; normalize to a clean start)
await page.goto(BASE + '/login', { waitUntil: 'networkidle' });
await page.evaluate(() => localStorage.setItem('nc-lang', 'en'));
await page.reload({ waitUntil: 'networkidle' });
await page.click('button[type="submit"]');
await page.waitForSelector('.sidebar__link', { timeout: 5000 });
await page.waitForTimeout(300);

// go Arabic
await page.click('.topbar__lang');
await page.waitForTimeout(400);
// settings
await page.click('.sidebar__link:has-text("إعدادات مساحة العمل")');
await page.waitForTimeout(450);
await page.screenshot({ path: `${OUT}/ar-settings2.png` });

// read the phone input value as rendered
const phoneVal = await page.$$eval('.nc-input', els => {
  const el = els.find(e => e.value && e.value.includes('971'));
  return el ? el.value : 'not found';
});
console.log('Phone field value:', phoneVal);

// no horizontal overflow check in RTL
const scrollW = await page.evaluate(() => document.documentElement.scrollWidth);
const clientW = await page.evaluate(() => document.documentElement.clientWidth);
console.log(`RTL overflow: scrollWidth=${scrollW} clientWidth=${clientW} (equal = no h-scroll)`);

// mobile RTL — sidebar off-screen, opens from right
await page.setViewportSize({ width: 390, height: 800 });
await page.click('.sidebar__link:has-text("لوحة التحكم")');
await page.waitForTimeout(300);
await page.screenshot({ path: `${OUT}/ar-mobile.png` });
const mScroll = await page.evaluate(() => document.documentElement.scrollWidth);
console.log(`RTL mobile scrollWidth at 390px: ${mScroll}`);

console.log(`Console errors: ${errors.length}`);
errors.forEach(e => console.log('  ✗ ' + e));
await browser.close();
process.exit(errors.length ? 1 : 0);
