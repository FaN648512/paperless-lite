/* 管理员端验证：boss 登录后用户管理按钮应可见 */
const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:8765';

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const p = await ctx.newPage();
  await p.goto(BASE + '/login', { waitUntil: 'networkidle' });
  await p.fill('#lg-user', 'boss');
  await p.fill('#lg-pwd', 'demo123456');
  await p.click('#btn-login');
  await p.waitForTimeout(8000);
  console.log('管理员 URL:', p.url());
  console.log('管理员 #btn-users 可见:', await p.locator('#btn-users').isVisible().catch(() => false));
  console.log('管理员 #btn-logout 可见:', await p.locator('#btn-logout').isVisible().catch(() => false));

  // 同事端：退出按钮应可见，用户管理应隐藏
  const ctx2 = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const p2 = await ctx2.newPage();
  await p2.goto(BASE + '/login', { waitUntil: 'networkidle' });
  await p2.fill('#lg-user', 'xiaoli');
  await p2.fill('#lg-pwd', 'demo123456');
  await p2.click('#btn-login');
  await p2.waitForTimeout(3500);
  console.log('同事 #btn-logout 可见:', await p2.locator('#btn-logout').isVisible().catch(() => false));
  console.log('同事 #btn-users 隐藏:', !(await p2.locator('#btn-users').isVisible().catch(() => false)));
  await browser.close();
})();
