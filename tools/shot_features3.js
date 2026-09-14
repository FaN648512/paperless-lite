/* 浏览器端到端验证三大新功能：
   1) 搜索自动补全  2) 保存的视图  3) 批量标签 */
const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:8765';
const path = require('path');
const fs = require('fs');
const SHOTS = path.join(__dirname, 'shots') + path.sep;
fs.mkdirSync(SHOTS, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const p = await ctx.newPage();
  const errors = [];
  p.on('pageerror', e => errors.push(String(e)));

  // 登录（boss/demo123456 已由 setup 建立；如需 setup 则自动兜底）
  await p.goto(BASE + '/login', { waitUntil: 'networkidle' });
  await p.waitForTimeout(1200);
  const st = await p.evaluate(() => fetch('/api/auth/status').then(r => r.json()));
  if (st.need_setup) {
    await p.fill('#su-user', 'boss');
    await p.fill('#su-pwd', 'demo123456');
    await p.fill('#su-pwd2', 'demo123456');
    await p.click('#btn-setup');
  } else {
    await p.fill('#lg-user', 'boss');
    await p.fill('#lg-pwd', 'demo123456');
    await p.click('#btn-login');
  }
  await p.waitForURL(u => !String(u).includes('/login'), { timeout: 20000 }).catch(() => {});
  // 若 20 秒后仍在登录页，重试一次
  if (p.url().includes('/login')) {
    await p.fill('#lg-user', 'boss');
    await p.fill('#lg-pwd', 'demo123456');
    await p.click('#btn-login');
    await p.waitForURL(u => !String(u).includes('/login'), { timeout: 20000 }).catch(() => {});
  }
  await p.waitForTimeout(2500);
  console.log('登录后 URL:', p.url());
  if (p.url().includes('/login')) { console.log('!! 无法登录（账号不是 boss/demo123456），终止'); await browser.close(); return; }

  // ============ 1. 搜索自动补全 ============
  await p.fill('#search', '检', { timeout: 5000 }).catch(async () => {
    // fill 可能因浮层出现而重试失败，先点聚焦
    await p.click('#search'); await p.fill('#search', '检');
  });
  await p.waitForTimeout(900);
  const sugVisible = await p.locator('#suggest-box').isVisible().catch(() => false);
  const sugCount = await p.locator('#suggest-box .suggest-item').count();
  console.log('1) 补全浮层出现:', sugVisible, '｜候选条数:', sugCount);
  if (sugCount) {
    const first = await p.locator('#suggest-box .suggest-item').first().innerText();
    console.log('   第一个候选:', first.replace(/\s+/g, ' '));
  }
  await p.screenshot({ path: SHOTS + '10-搜索自动补全.png' });
  // 点第一个候选
  if (sugCount) {
    await p.locator('#suggest-box .suggest-item').first().click();
    await p.waitForTimeout(1200);
    const applied = await p.locator('#search').inputValue();
    console.log('   点击候选后搜索框 =', applied);
    // 清理：取消标签筛选 + 清空搜索词（触发 oninput 让 has-value 状态复位）
    const activeTag = await p.locator('#tag-list .tag-chip.active').count();
    if (activeTag) { await p.locator('#tag-list .tag-chip.active').first().click(); }
    await p.evaluate(() => {
        const s = document.querySelector('#search');
        s.value = '';
        s.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await p.waitForTimeout(800);
  } else {
    await p.evaluate(() => {
        const s = document.querySelector('#search');
        s.value = '';
        s.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await p.waitForTimeout(500);
  }

  // ============ 2. 保存的视图 ============
  // 先设一个筛选：点第一个标签
  const tagCount = await p.locator('#tag-list .tag-chip').count();
  console.log('2) 标签数:', tagCount);
  if (tagCount) {
    const wasActive = await p.locator('#tag-list .tag-chip.active').count();
    if (!wasActive) {
      await p.locator('#tag-list .tag-chip').first().click();
      await p.waitForFunction(
        () => document.querySelectorAll('#tag-list .tag-chip.active').length > 0,
        null, { timeout: 8000 }).catch(() => {});
    }
    await p.waitForTimeout(600);
  }
  // 页面对话框（prompt）自动输入名称
  p.once('dialog', d => d.accept('测试视图-标签'));
  await p.click('#btn-save-view');
  await p.waitForTimeout(600);
  const viewCount = await p.locator('#view-list .view-item').count();
  console.log('   保存后视图数:', viewCount);
  // 清掉筛选（防御式：有 active 才点），再一键应用视图
  if (tagCount) {
    const act = await p.locator('#tag-list .tag-chip.active').count();
    if (act) {
      await p.locator('#tag-list .tag-chip.active').first().click();
      await p.waitForFunction(
        () => document.querySelectorAll('#tag-list .tag-chip.active').length === 0,
        null, { timeout: 8000 }).catch(() => {});
    }
    await p.waitForTimeout(600);
  }
  await p.locator('#view-list .view-item').first().click();
  await p.waitForTimeout(1200);
  const tagActive = await p.locator('#tag-list .tag-chip.active').count();
  console.log('   一键应用后标签筛选恢复:', tagActive > 0);
  await p.screenshot({ path: SHOTS + '11-保存的视图.png' });
  // 删除测试视图（收尾）
  await p.locator('#view-list [data-delview]').first().click().catch(() => {});
  await p.waitForTimeout(500);

  // ============ 3. 批量标签 ============
  await p.click('#btn-batch');
  await p.waitForTimeout(600);
  const barVisible = await p.locator('#batch-bar').isVisible();
  console.log('3) 批量操作条出现:', barVisible);
  await p.locator('#doc-grid .doc-card').nth(0).click();
  await p.locator('#doc-grid .doc-card').nth(1).click();
  await p.waitForTimeout(400);
  const n = await p.locator('#batch-n').innerText();
  console.log('   已选份数:', n);
  await p.screenshot({ path: SHOTS + '12-批量标签模式.png' });
  // 选第一个标签执行添加（打印受影响 doc/tag，供收尾清理）
  const optCount = await p.locator('#batch-tag option').count();
  if (optCount > 1) {
    const chosen = await p.locator('#batch-tag option').nth(1).innerText();
    await p.selectOption('#batch-tag', { index: 1 });
    const chosenId = await p.locator('#batch-tag').inputValue();
    const ids = [];
    for (const el of await p.locator('.doc-card.batch-selected').all()) ids.push(await el.getAttribute('data-id'));
    console.log('   批量目标 → 标签:', chosen.trim(), '(id=' + chosenId + ')｜文档ids:', ids.join(','));
    await p.waitForTimeout(200);
    await p.click('#batch-add');
    await p.waitForTimeout(1500);
    const toast = await p.locator('#toast').innerText().catch(() => '');
    console.log('   批量添加结果 toast:', toast.trim());
  } else {
    console.log('   （库里没有可选标签，跳过添加动作）');
  }
  await p.click('#batch-exit');
  await p.waitForTimeout(800);
  const barHidden = !(await p.locator('#batch-bar').isVisible());
  console.log('   退出后操作条隐藏:', barHidden);

  console.log('页面 JS 报错数:', errors.length, errors.slice(0, 3));
  await browser.close();
})();
