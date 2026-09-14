const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const SHOTS = path.join(__dirname, 'shots') + path.sep;
fs.mkdirSync(SHOTS, { recursive: true });
const BASE = 'http://127.0.0.1:8765';

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
  p.on('console', m => { if (m.type() === 'error') console.log('  [页面报错]', m.text()); });

  // 1. 未设置密码时访问首页
  await p.goto(BASE, { waitUntil: 'networkidle' });
  console.log('1) 访问首页 -> 落在：', p.url());
  await p.screenshot({ path: SHOTS + '1-设置密码页.png' });

  // 2. 设置一个账号密码
  await p.fill('#su-user', 'boss');
  await p.fill('#su-pwd', 'demo123456');
  await p.fill('#su-pwd2', 'demo123456');
  await p.click('#btn-setup');
  await p.waitForTimeout(3000);
  console.log('2) 设置完成 -> 落在：', p.url());
  await p.screenshot({ path: SHOTS + '2-文档库首页.png' });

  // 3. 确认文档正常显示 + 退出按钮在不在
  const cards = await p.locator('.doc-card, .card-item, [data-doc-id]').count();
  const bodyTxt = await p.locator('body').innerText();
  console.log('3) 首页文档卡片数：', cards, '｜页面字符数：', bodyTxt.length);
  const logoutVisible = await p.locator('#btn-logout').isVisible().catch(() => false);
  console.log('   退出按钮可见：', logoutVisible);

  // 4. 退出登录
  if (logoutVisible) {
    await p.click('#btn-logout');
    await p.waitForTimeout(2000);
    console.log('4) 退出后 -> 落在：', p.url());
    await p.screenshot({ path: SHOTS + '3-登录页.png' });

    // 5. 用正确密码重新登录
    await p.fill('#lg-user', 'boss');
    await p.fill('#lg-pwd', 'demo123456');
    await p.click('#btn-login');
    await p.waitForTimeout(3000);
    console.log('5) 重新登录 -> 落在：', p.url());
    if (p.url().includes('/login')) {
      const err = await p.locator('#err').innerText().catch(() => '');
      console.log('   登录失败提示：', err);
    }
    await p.screenshot({ path: SHOTS + '4-登录后.png' });
  }

  await b.close();
  console.log('\n截图已保存到：', SHOTS);
})().catch(e => { console.error('出错了：', e.message); process.exit(1); });
