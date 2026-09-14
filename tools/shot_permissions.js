const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const SHOTS = path.join(__dirname, 'shots') + path.sep;
fs.mkdirSync(SHOTS, { recursive: true });
const BASE = 'http://127.0.0.1:8765';

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1360, height: 900 } });
  p.on('console', m => { if (m.type() === 'error') console.log('  [页面报错]', m.text()); });

  // 1. 设置管理员账号
  await p.goto(BASE, { waitUntil: 'networkidle' });
  await p.fill('#su-user', 'boss');
  await p.fill('#su-pwd', 'demo123456');
  await p.fill('#su-pwd2', 'demo123456');
  await p.click('#btn-setup');
  await p.waitForTimeout(3500);
  console.log('1) 登录后落在：', p.url());

  // 2. 首页：看卡片上的归属/可见性徽章
  await p.waitForTimeout(1500);
  const badges = await p.locator('.vis-badge').count();
  const owners = await p.locator('.owner-badge').count();
  console.log('2) 卡片徽章：可见性', badges, '个 / 归属', owners, '个');
  await p.screenshot({ path: SHOTS + '5-权限-首页徽章.png' });

  // 3. 打开第一份文档详情
  await p.locator('.doc-card').first().click();
  await p.waitForTimeout(2000);
  const visPicker = await p.locator('.vis-picker').count();
  const visOpts = await p.locator('.vis-opt').count();
  const activeVis = await p.locator('.vis-opt.active').innerText().catch(() => '(无)');
  console.log('3) 详情面板：可见性选择器', visPicker, '个 / 选项', visOpts, '个 / 当前选中：', activeVis.replace(/\s+/g, ' '));
  await p.screenshot({ path: SHOTS + '6-权限-可见性选择器.png' });

  // 4. 点「私密」验证能切换
  await p.locator('.vis-opt[data-vis="private"]').click();
  await p.waitForTimeout(2500);
  const afterPrivate = await p.locator('.vis-opt.active').innerText().catch(() => '(无)');
  const hint = await p.locator('.vis-hint').innerText().catch(() => '');
  console.log('4) 点了「私密」后：当前选中 =', afterPrivate.replace(/\s+/g, ' '), '｜提示 =', hint);
  await p.screenshot({ path: SHOTS + '7-权限-已设为私密.png' });

  // 5. 切回「只读」（不改动老板的真实数据）
  await p.locator('.vis-opt[data-vis="readonly"]').click();
  await p.waitForTimeout(2500);
  const back = await p.locator('.vis-opt.active').innerText().catch(() => '(无)');
  console.log('5) 已切回：', back.replace(/\s+/g, ' '));

  // 6. 用户管理面板
  await p.locator('.detail-close').click();
  await p.waitForTimeout(600);
  const usersBtnVisible = await p.locator('#btn-users').isVisible().catch(() => false);
  console.log('6) 用户管理按钮可见：', usersBtnVisible);
  if (usersBtnVisible) {
    await p.click('#btn-users');
    await p.waitForTimeout(1500);
    // 添加一个同事账号
    await p.fill('#nu-name', 'xiaoli');
    await p.fill('#nu-pass', 'demo123456');
    await p.click('#btn-user-add');
    await p.waitForTimeout(1800);
    const rows = await p.locator('#user-list tr').count();
    console.log('   添加用户后，列表行数：', rows);
    await p.screenshot({ path: SHOTS + '8-权限-用户管理.png' });

    // 7. 用同事账号登录，看只读效果
    const p2 = await b.newPage({ viewport: { width: 1360, height: 900 } });
    await p2.goto(BASE, { waitUntil: 'networkidle' });
    await p2.fill('#lg-user', 'xiaoli');
    await p2.fill('#lg-pwd', 'demo123456');
    await p2.click('#btn-login');
    await p2.waitForTimeout(3500);
    console.log('7) 同事登录后落在：', p2.url());
    const u2Badges = await p2.locator('.vis-badge').count();
    const u2OwnerText = await p2.locator('.owner-badge').first().innerText().catch(() => '');
    console.log('   同事看到的徽章数：', u2Badges, '｜第一个归属：', u2OwnerText.replace(/\s+/g, ''));
    const usersBtnU2 = await p2.locator('#btn-users').isVisible().catch(() => false);
    console.log('   同事看不到「用户管理」按钮：', !usersBtnU2);
    // 打开一份老板的文档，看只读提示
    await p2.locator('.doc-card').first().click();
    await p2.waitForTimeout(2000);
    const roText = await p2.locator('.vis-readonly').innerText().catch(() => '(无只读提示)');
    const saveDisabled = await p2.locator('#d-save').isDisabled().catch(() => null);
    console.log('   只读提示：', roText.replace(/\s+/g, ' '), '｜保存按钮禁用：', saveDisabled);
    await p2.screenshot({ path: SHOTS + '9-权限-同事只读视图.png' });
  }

  await b.close();
  console.log('\n截图目录：', SHOTS);
})().catch(e => { console.error('出错了：', e.message); process.exit(1); });
