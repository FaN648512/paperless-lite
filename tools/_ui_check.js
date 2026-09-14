const { chromium } = require('playwright');
const fs = require('fs');

const path = require('path');

const BASE = process.env.PL_BASE || 'http://127.0.0.1:8765';
const OFFICE_DIR = path.join(__dirname, 'officetest');
const EMPTY_DIR = path.join(__dirname, 'scantest_empty');
const JUNK_DIR = path.join(__dirname, 'scantest_junk');

let P = 0, F = 0;
const check = (name, ok, extra = '') => {
    if (ok) { P++; console.log('  [通过] ' + name + ' ' + extra); }
    else { F++; console.log('  [失败] ' + name + ' ' + extra); }
};
const jfetch = async (path, opt) => {
    const r = await fetch(BASE + path, opt);
    const t = await r.text();
    try { return JSON.parse(t); } catch (e) { throw new Error('响应不是 JSON（' + r.status + '）：' + t.slice(0, 120)); }
};

(async () => {
    // 准备：确保库里有 Excel / PPT 文档各若干
    let docs = (await jfetch('/api/documents')).items;
    let office = docs.filter(d => d.format === 'excel' || d.format === 'ppt');
    if (office.length < 2) {
        const paths = fs.readdirSync(OFFICE_DIR).filter(n => !n.startsWith('_t_')).map(n => OFFICE_DIR + '/' + n);
        await jfetch('/api/import-folder', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths })
        });
        await new Promise(r => setTimeout(r, 15000));
        docs = (await jfetch('/api/documents')).items;
        office = docs.filter(d => d.format === 'excel' || d.format === 'ppt');
    }
    const nExcel = docs.filter(d => d.format === 'excel').length;
    const nPpt = docs.filter(d => d.format === 'ppt').length;
    check('准备：库里有 Office 文档（Excel %d / PPT %d）'.replace('%d', nExcel).replace('%d', nPpt),
        nExcel >= 1 && nPpt >= 1);
    const officeIds = office.map(d => d.id);

    const b = await chromium.launch({ headless: true });
    const page = await b.newPage({ viewport: { width: 1440, height: 900 } });
    page.setDefaultTimeout(20000);
    page.on('pageerror', e => console.log('  页面 JS 报错:', e.message));
    await page.goto(BASE, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#format-list .filter-item');

    console.log('=== 1. 左侧栏格式分类 ===');
    const fmtItems = await page.locator('#format-list .filter-item').count();
    check('侧栏出现格式分类（全部 + PDF/图片/Excel/PPT/其他）', fmtItems === 6, '实际 ' + fmtItems + ' 项');
    const labels = (await page.locator('#format-list .filter-item').allInnerTexts())
        .map(s => s.replace(/\s+/g, ' ').trim());
    console.log('     分类项：' + labels.join(' | '));

    console.log('=== 2. 点格式分类能筛选 ===');
    const total = parseInt(await page.locator('#result-count').innerText(), 10);
    await page.locator('#format-list .filter-item', { hasText: 'Excel' }).click();
    await page.waitForTimeout(700);
    const excelN = parseInt(await page.locator('#result-count').innerText(), 10);
    const badge = parseInt(await page.locator('#format-list .filter-item', { hasText: 'Excel' })
        .locator('.count').innerText(), 10);
    check('点 Excel 后结果数 = 侧栏统计数', excelN === badge && excelN === nExcel,
        '结果 ' + excelN + ' / 侧栏 ' + badge);

    await page.locator('#format-list .filter-item', { hasText: 'PPT' }).click();
    await page.waitForTimeout(700);
    const pptN = parseInt(await page.locator('#result-count').innerText(), 10);
    check('点 PPT 后只剩 PPT 文档', pptN === nPpt, '结果 ' + pptN);
    const pptSrc = await page.locator('.doc-card .src-badge').first().innerText();
    check('Office 文档标记来源为“文件解析”', pptSrc.includes('文件解析'), '徽标：' + pptSrc);

    await page.locator('#format-list .filter-item', { hasText: '全部格式' }).click();
    await page.waitForTimeout(700);
    const backN = parseInt(await page.locator('#result-count').innerText(), 10);
    check('点“全部格式”恢复全部文档', backN === total, backN + ' / 原 ' + total);

    console.log('=== 3. 扫描空文件夹 → 弹窗提示 ===');
    fs.mkdirSync(EMPTY_DIR, { recursive: true });
    await page.locator('#btn-folder').click();
    await page.locator('#folder-path').fill(EMPTY_DIR);
    await page.locator('#btn-scan').click();
    await page.waitForTimeout(2500);
    const noticeVisible = await page.locator('#notice-modal').isVisible();
    const noticeText = await page.locator('#notice-msg').innerText();
    check('弹出提示窗口', noticeVisible);
    check('提示文案说明“没有任何文件”', noticeText.includes('没有任何文件'),
        '文案：' + noticeText.split('\n')[0]);
    await page.screenshot({ path: path.join(__dirname, '_shot_notice.png') });
    await page.locator('#notice-modal .btn-primary').click();
    await page.waitForTimeout(300);
    check('点“知道了”关闭弹窗', await page.locator('#notice-modal').isHidden());

    console.log('=== 4. 扫描只有不支持格式的文件夹 → 弹窗提示 ===');
    fs.mkdirSync(JUNK_DIR, { recursive: true });
    fs.writeFileSync(JUNK_DIR + '/说明.txt', '不支持的文件');
    await page.locator('#folder-path').fill(JUNK_DIR);
    await page.locator('#btn-scan').click();
    await page.waitForTimeout(2500);
    check('弹出提示窗口', await page.locator('#notice-modal').isVisible());
    const noticeText2 = await page.locator('#notice-msg').innerText();
    check('提示文案说明“格式不支持”并列出支持格式',
        noticeText2.includes('不支持') && noticeText2.includes('Excel'),
        '文案：' + noticeText2.split('\n')[0]);
    await page.locator('#notice-modal .icon-btn').click();
    await page.waitForTimeout(300);
    check('点 × 也能关闭弹窗', await page.locator('#notice-modal').isHidden());

    console.log('=== 5. 清理 ===');
    for (const id of officeIds) {
        await fetch(BASE + '/api/documents/' + id, { method: 'DELETE' });
    }
    fs.rmSync(EMPTY_DIR, { recursive: true, force: true });
    fs.rmSync(JUNK_DIR, { recursive: true, force: true });
    const rest = (await jfetch('/api/documents')).total;
    check('测试文档已清理', rest === total - officeIds.length,
        '剩余 ' + rest + ' 份（原 ' + total + '，删了 ' + officeIds.length + '）');

    await b.close();
    console.log('\n结果：' + P + ' 通过 / ' + F + ' 失败');
    process.exitCode = F === 0 ? 0 : 1;
})().catch(e => { console.error('出错:', e.message); process.exitCode = 1; });
