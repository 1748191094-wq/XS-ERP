// Open the actual admin quote page with isolated network fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const staticRoot = path.resolve(__dirname, '../app/static');

async function check(browser, width) {
  const page = await browser.newPage({ viewport: { width, height: 850 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let attempts = 0, releaseRetry;
  const gate = new Promise(resolve => { releaseRetry = resolve; });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== 'erp.test') return route.abort();
    if (!url.pathname.startsWith('/api/')) {
      const file = { '/': 'index.html', '/static/app.js': 'app.js', '/static/workflow.js': 'workflow.js', '/static/styles.css': 'styles.css' }[url.pathname];
      if (!file) return route.fulfill({ status: 404, body: '' });
      return route.fulfill({ path: path.join(staticRoot, file) });
    }
    let data = [];
    if (url.pathname.endsWith('/auth/status')) data = { initialized: true };
    if (url.pathname.endsWith('/auth/me')) data = { user: { role: 'admin', display_name: '验收员' } };
    if (url.pathname === '/api/customers') data = [{ id: 7, name: '测试客户' }];
    if (url.pathname === '/api/orders') data = [{ id: 1, order_no: 'WX-TEST-01', customer_id: 7 }];
    if (url.pathname.endsWith('/assessment-summary')) {
      if (++attempts === 1) return route.fulfill({ status: 503, json: { error: { message: '读取失败' } } });
      await gate;
      data = { assessment: { id: 2, assessment_no: 'DS-TEST-02', conclusion: '待更换部件。'.repeat(30) }, suggested_item: { item_name: '测试部件', unit_price: '12345.67' } };
    }
    return route.fulfill({ json: { success: true, data } });
  });
  try {
    await page.goto('http://erp.test/#quotes');
    await page.getByRole('button', { name: '新建报价版本' }).click();
    await page.locator('[name=repair_order_id]').selectOption('1');
    const retry = page.getByRole('button', { name: '重试读取定损' });
    const source = page.locator('#quote-assessment-source');
    await retry.waitFor();
    const note = page.locator('[name=repair_recommendation]');
    await note.fill('保留这条人工维修建议');
    await retry.focus();
    if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SCREENSHOT_DIR, 'failure-' + width + '.png') });
    await retry.press('Enter');
    assert(await retry.isDisabled());
    assert(await source.evaluate(el => el === document.activeElement));
    releaseRetry();
    await source.filter({ hasText: 'DS-TEST-02' }).waitFor();
    assert(await page.locator('#quote-assessment-retry').isHidden());
    assert.equal(await note.inputValue(), '保留这条人工维修建议');
    assert.equal(await page.locator('[name=unit_price]').last().inputValue(), '12345.67');
    assert(await source.evaluate(el => el === document.activeElement));
    assert(await page.locator('.modal-card').evaluate(el => el.scrollWidth <= el.clientWidth + 1));
    if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SCREENSHOT_DIR, 'success-' + width + '.png') });
    await page.getByRole('button', { name: '关闭对话框' }).click();
    await page.locator('#modal').waitFor({ state: 'hidden' });
    assert.deepEqual(errors, []);
  } finally { releaseRetry(); await page.close(); }
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox', '--disable-gpu'] });
  try {
    if (process.env.SCREENSHOT_DIR) await fs.mkdir(process.env.SCREENSHOT_DIR, { recursive: true });
    for (const width of [320, 390, 1440]) await check(browser, width);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
