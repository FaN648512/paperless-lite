/* ============================================================
   票据合同 OCR 文档库 —— 前端交互
   原生 JS，无构建链，低内存占用
   ============================================================ */

const state = {
    docs: [],
    tags: [],
    rules: [],
    meta: {},
    query: '',
    filter: { status: '', tag: null, correspondent: null },
    selectedId: null,
    view: 'grid',
    pollTimer: null,
    batch: { mode: false, selected: new Set() },   // 批量标签模式
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

/* ---------------- 网络 ---------------- */
async function api(path, options = {}) {
    const res = await fetch(path, options);
    if (res.status === 401) {
        // 未登录或登录过期：直接跳登录页，不要弹一堆报错吓人
        if (!location.pathname.startsWith('/login')) location.href = '/login';
        throw new Error('未登录或登录已过期');
    }
    if (!res.ok) {
        let msg = '请求失败（' + res.status + '）';
        try { const j = await res.json(); if (j.error) msg = j.error; } catch (e) {}
        throw new Error(msg);
    }
    return res.json();
}

let toastTimer = null;
function toast(msg, kind = '') {
    const el = $('#toast');
    el.textContent = msg;
    el.className = 'toast ' + kind;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

/* 通用提示弹窗：需要老板明确确认的场景（如扫描无结果）用它，比一闪而过的 toast 稳 */
function showNotice(title, msgHtml, okText = '知道了') {
    $('#notice-title').textContent = title;
    $('#notice-msg').innerHTML = msgHtml;
    const btn = $('#notice-modal .btn-primary');
    if (btn) btn.textContent = okText;
    $('#notice-modal').hidden = false;
}

/* ---------------- 渲染：侧栏标签 ---------------- */
function renderTags() {
    const box = $('#tag-list');
    if (!state.tags.length) {
        box.innerHTML = '<div style="padding:4px 8px;color:var(--faint);font-size:12px">暂无标签</div>';
        return;
    }
    box.innerHTML = state.tags.map(t => `
        <div class="filter-item tag-chip ${state.filter.tag === t.id ? 'active' : ''}"
             data-tag="${t.id}" style="color:${esc(t.color)}">
            <span class="dot" style="background:${esc(t.color)}"></span>
            <span class="name" style="color:var(--txt)">${esc(t.name)}</span>
            <span class="count">${t.doc_count}</span>
            <span class="tag-del" data-del="${t.id}" title="删除标签">×</span>
        </div>`).join('');

    $$('#tag-list .tag-chip').forEach(el => {
        el.onclick = () => {
            const id = parseInt(el.dataset.tag, 10);
            state.filter.tag = (state.filter.tag === id) ? null : id;
            loadDocuments();
        };
    });

    $$('#tag-list .tag-del').forEach(el => {
        el.onclick = async (e) => {
            e.stopPropagation();
            const id = parseInt(el.dataset.del, 10);
            const tag = state.tags.find(t => t.id === id);
            if (!tag) return;
            const msg = `删除标签「${tag.name}」？\n将从 ${tag.doc_count} 份文档移除该标签，其自动匹配规则也会一并删除。`;
            if (!confirm(msg)) return;
            await api('/api/tags/' + id, { method: 'DELETE' });
            if (state.filter.tag === id) state.filter.tag = null;
            await refreshAll();
            toast('标签已删除', 'ok');
        };
    });
}

function renderCorrespondents() {
    const box = $('#corr-list');
    const list = state.meta.correspondents || [];
    if (!list.length) {
        box.innerHTML = '<div style="padding:4px 8px;color:var(--faint);font-size:12px">暂无</div>';
        return;
    }
    box.innerHTML = list.map(c => `
        <div class="filter-item ${state.filter.correspondent === c.id ? 'active' : ''}"
             data-corr="${c.id}">
            <span class="name" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.name)}</span>
        </div>`).join('');

    $$('#corr-list .filter-item').forEach(el => {
        el.onclick = () => {
            const id = parseInt(el.dataset.corr, 10);
            state.filter.correspondent = (state.filter.correspondent === id) ? null : id;
            loadDocuments();
        };
    });
}

/* ---------------- 渲染：文档列表 ---------------- */
function tagHtml(t) {
    return `<span class="tag ${t.source === 'auto' ? 'auto' : ''}" style="color:${esc(t.color)}">
        <span class="dot" style="background:${esc(t.color)}"></span>${esc(t.name)}</span>`;
}

function fmtSize(n) {
    if (!n) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
}

function statusLabel(d) {
    return { pending: '排队中', processing: '识别中', done: '已完成', failed: '失败' }[d.status] || d.status;
}

/* 卡片上的归属 / 可见性小徽章 */
const VIS_BADGE = {
    private: { icon: '🔒', text: '私密', cls: 'vis-private' },
    readonly: { icon: '👁', text: '只读', cls: 'vis-readonly-badge' },
    public_edit: { icon: '✏️', text: '公开编辑', cls: 'vis-public' },
};
function visBadge(d) {
    const me = state.meta.me || {};
    const mine = d.owner && d.owner === me.username;
    const b = VIS_BADGE[d.visibility] || VIS_BADGE.readonly;
    const who = mine ? '我' : esc(d.owner || '—');
    return `<span class="vis-badge ${b.cls}" title="${b.text}：${b.icon}">
              <span>${b.icon}</span>${b.text}
            </span>
            <span class="owner-badge" title="由 ${who} 上传">👤 ${who}</span>`;
}

function cardHtml(d) {
    const cls = (d.status === 'pending' || d.status === 'processing') ? 'processing' : '';
    const srcCls = d.content_source === 'ocr' ? 'ocr' : (d.content_source === 'text-layer' ? 'text-layer' : '');
    const srcText = { 'text-layer': '文本层', 'ocr': 'OCR', 'mixed': '混合', 'office': '文件解析' }[d.content_source] || '';
    const tags = (d.tags || []).slice(0, 3).map(tagHtml).join('');
    const preview = highlight(d.preview || '', state.query);

    return `<div class="doc-card ${cls} ${state.selectedId === d.id ? 'selected' : ''}" data-id="${d.id}">
        <div class="card-thumb">
            ${d.status === 'done'
                ? `<img src="${d.thumb_url}" alt="" loading="lazy" onerror="this.parentNode.innerHTML='<span class=\\'no-thumb\\'>📄</span>'">`
                : `<span class="no-thumb">⏳</span>`}
        </div>
        <div class="card-body">
            <div class="card-title">${highlight(d.title || d.original_name, state.query)}</div>
            ${d.original_name && d.original_name !== d.title
                ? `<div class="card-file" title="${esc(d.original_name)}">${highlight(d.original_name, state.query)}</div>`
                : ''}
            <div class="card-vis">
                ${visBadge(d)}
            </div>
            <div class="card-preview">${preview}</div>
            <div class="card-tags">${tags}</div>
            <div class="card-meta">
                <span class="status-dot status-${d.status}"></span>
                <span>${statusLabel(d)}</span>
                ${srcText ? `<span class="src-badge ${srcCls}">${srcText}</span>` : ''}
                <span>${d.page_count || 1} 页</span>
                ${d.ocr_chars ? `<span>${d.ocr_chars} 字</span>` : ''}
            </div>
        </div>
    </div>`;
}

function renderDocs() {
    const grid = $('#doc-grid');
    const empty = $('#empty-state');

    if (!state.docs.length) {
        grid.innerHTML = '';
        empty.classList.add('show');
    } else {
        empty.classList.remove('show');
        grid.innerHTML = state.docs.map(cardHtml).join('');
        $$('#doc-grid .doc-card').forEach(el => {
            const id = parseInt(el.dataset.id, 10);
            if (state.batch.mode) {
                el.classList.toggle('batch-selected', state.batch.selected.has(id));
                el.onclick = () => toggleBatch(id);
            } else {
                el.onclick = () => openDetail(id);
            }
        });
    }
    grid.className = 'doc-grid' + (state.view === 'list' ? ' list-view' : '');

    $('#result-count').textContent = state.docs.length;
    const q = state.query;
    $('#result-query').textContent = q ? `· 匹配「${q}」` : '';
    if (state.batch.mode) {
        $$('#doc-grid .doc-card').forEach(el => {
            el.classList.toggle('batch-selected', state.batch.selected.has(parseInt(el.dataset.id, 10)));
        });
        updateBatchBar();
    }
}

/* ---------------- 搜索高亮 ---------------- */
function highlight(text, query) {
    const raw = String(text == null ? '' : text);
    const q = (query || '').trim();
    if (!q) return esc(raw);
    const terms = q.split(/\s+/).filter(Boolean);
    if (!terms.length) return esc(raw);

    let html = esc(raw);
    for (const t of terms) {
        const safe = esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        try {
            html = html.replace(new RegExp('(' + safe + ')', 'gi'), '<span class="hl">$1</span>');
        } catch (e) { /* 忽略非法正则 */ }
    }
    return html;
}

/* ---------------- 详情面板 ---------------- */
function closeDetail() {
    state.selectedId = null;
    $('.layout').classList.remove('detail-open');
    $('#detail-panel').innerHTML = '<div class="detail-empty">选择一份文档查看识别结果</div>';
    renderDocs();
}

async function openDetail(id) {
    state.selectedId = id;
    $('.layout').classList.add('detail-open');
    renderDocs();

    const d = await api('/api/documents/' + id);
    const panel = $('#detail-panel');

    // 有没有编辑权限（别人的「只读」文档 -> false）
    const editable = d.can_edit !== false;
    const canRemoveTag = editable ? '<span class="x" data-tag="${t.id}">×</span>' : '';
    const tagsHtml = (d.tags || []).map(t => `
        <span class="tag-removable" style="color:${esc(t.color)}">
            <span class="dot" style="background:${esc(t.color)};width:6px;height:6px;border-radius:50%;display:inline-block"></span>
            ${esc(t.name)}
            ${t.source === 'auto' ? '<span style="color:var(--faint);font-size:10.5px">自动</span>' : ''}
            ${canRemoveTag}
        </span>`).join('');

    const logs = (d.logs || []).slice(-8).map(l =>
        `<div>[${esc(l.t)}] ${esc(l.m)}</div>`).join('');

    // ---- 多用户权限：私密 / 只读 / 公开编辑 ----
    const me = state.meta.me || {};
    const canManage = d.is_owner || me.is_admin;      // 本人或管理员才能改这个设置
    const VIS_OPT = [
        { v: 'private', icon: '🔒', name: '私密', desc: '只有你自己看得见' },
        { v: 'readonly', icon: '👁', name: '只读', desc: '别人能看，不能改' },
        { v: 'public_edit', icon: '✏️', name: '公开编辑', desc: '别人也能改' },
    ];
    const visHtml = canManage
        ? `<div class="vis-picker">
               ${VIS_OPT.map(o => `
                 <button class="vis-opt ${d.visibility === o.v ? 'active' : ''}"
                         data-vis="${o.v}" title="${o.desc}">
                    <span class="vis-icon">${o.icon}</span>${o.name}
                 </button>`).join('')}
             </div>
             <div class="vis-hint">${VIS_OPT.find(o => o.v === d.visibility)?.desc || ''}</div>`
        : `<div class="vis-readonly">
               ${d.visibility === 'private'
                   ? '🔒 私密文档（只有上传者能看）'
                   : `👁 由 <b>${esc(d.owner || '他人')}</b> 上传，设为只读，你只能查看`}
           </div>`;

    // 没有编辑权限时，把改动类按钮全部禁用
    const dis = editable ? '' : 'disabled';
    const disStyle = editable ? '' : 'style="opacity:.45;cursor:not-allowed"';

    panel.innerHTML = `
    <div class="detail-topbar">
        <div class="detail-topbar-main">
            <textarea class="detail-title" id="d-title" rows="1" ${editable ? '' : 'readonly'}>${esc(d.title || '')}</textarea>
            <div class="detail-actions">
                <button class="btn btn-sm" id="d-save" ${dis} ${disStyle}>保存修改</button>
                <button class="btn btn-sm" id="d-reprocess" ${dis} ${disStyle}>重新识别</button>
                <a class="btn btn-sm" href="${d.file_url}" target="_blank">打开原文件</a>
                <button class="btn btn-sm" id="d-delete" ${dis} ${disStyle}
                        style="color:var(--bad);border-color:rgba(248,81,73,.35)">删除</button>
            </div>
        </div>
        <button class="detail-close" id="d-close" title="关闭详情（Esc）">×</button>
    </div>
    <div class="detail-inner">

        ${d.status === 'done'
            ? `<img class="detail-preview" src="${d.thumb_url}" alt="" onerror="this.style.display='none'">`
            : `<div class="detail-preview" style="height:180px;display:flex;align-items:center;justify-content:center;color:var(--faint)">
                 ${d.status === 'failed' ? '处理失败' : '识别中…'}
               </div>`}

        <div class="field-row">
            <div class="field-label">谁能改这份</div>
            <div class="field-value">${visHtml}</div>
        </div>

        <div class="field-row">
            <div class="field-label">标签</div>
            <div class="tags-editor">
                ${tagsHtml}
                ${editable ? '<input class="tag-add-input" id="d-tag-add" placeholder="+ 标签">'
                           : '<span style="color:var(--faint);font-size:12px">（只读，不能加标签）</span>'}
            </div>
        </div>

        <div class="field-row">
            <div class="field-label">原始文件</div>
            <div class="field-value" style="word-break:break-all">
                ${esc(d.original_name)} <span style="color:var(--faint)">· ${fmtSize(d.file_size)}</span>
            </div>
        </div>

        <div class="field-row">
            <div class="field-label">往来单位</div>
            <div class="field-value"><input id="d-corr" value="${esc(d.correspondent || '')}" placeholder="留空则自动识别"></div>
        </div>

        <div class="field-row">
            <div class="field-label">
                识别正文
                <span style="text-transform:none;letter-spacing:0">
                    · ${d.ocr_chars || 0} 字 ·
                    ${ { 'text-layer': '文本层抽取', 'ocr': 'OCR 识别', 'mixed': '混合抽取', 'office': '文件解析' }[d.content_source] || '—' }
                    · ${d.page_count || 1} 页
                </span>
            </div>
            <div class="content-box">${highlight(d.content || '（暂无内容）', state.query)}</div>
        </div>

        ${d.error ? `<div class="field-row" style="margin-top:14px">
            <div class="field-label" style="color:var(--bad)">错误信息</div>
            <div class="log-box" style="border-color:rgba(248,81,73,.3);color:var(--bad)">${esc(d.error)}</div>
        </div>` : ''}

        ${logs ? `<div class="field-row" style="margin-top:14px">
            <div class="field-label">处理日志</div>
            <div class="log-box">${logs}</div>
        </div>` : ''}
    </div>`;

    // 事件绑定
    $('#d-close').onclick = closeDetail;
    const titleEl = $('#d-title');
    titleEl.style.height = 'auto';
    titleEl.style.height = titleEl.scrollHeight + 'px';
    titleEl.oninput = () => {
        titleEl.style.height = 'auto';
        titleEl.style.height = titleEl.scrollHeight + 'px';
    };

    $$('#detail-panel .tag-removable .x').forEach(el => {
        el.onclick = async (e) => {
            e.stopPropagation();
            await api(`/api/documents/${id}/tags/${el.dataset.tag}`, { method: 'DELETE' });
            await refreshAll();
            openDetail(id);
        };
    });

    const tagInput = $('#d-tag-add');
    tagInput.onkeydown = async (e) => {
        if (e.key === 'Enter' && tagInput.value.trim()) {
            const res = await api(`/api/documents/${id}/tags`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: tagInput.value.trim() })
            });
            if (res.auto) toast(`新标签已自动匹配 ${res.auto.tagged} 份文档`, 'ok');
            await refreshAll();
            openDetail(id);
        }
    };

    // 切换可见性（私密 / 只读 / 公开编辑）
    $$('#detail-panel .vis-opt').forEach(el => {
        el.onclick = async () => {
            const vis = el.dataset.vis;
            try {
                const r = await api(`/api/documents/${id}/visibility`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ visibility: vis })
                });
                toast('已设为「' + r.visibility_label + '」', 'ok');
                await refreshAll();
                openDetail(id);
            } catch (e) { toast(e.message, 'warn'); }
        };
    });

    $('#d-save').onclick = async () => {
        await api(`/api/documents/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: $('#d-title').value.trim(),
                correspondent: $('#d-corr').value.trim()
            })
        });
        toast('已保存', 'ok');
        await refreshAll();
    };

    $('#d-reprocess').onclick = async () => {
        await api(`/api/documents/${id}/reprocess`, { method: 'POST' });
        toast('已加入处理队列', 'ok');
        startPolling();
    };

    $('#d-delete').onclick = async () => {
        if (!confirm('确定删除这份文档？此操作不可撤销。')) return;
        await api(`/api/documents/${id}`, { method: 'DELETE' });
        closeDetail();
        await refreshAll();
        toast('已删除', 'ok');
    };
}

/* ---------------- 渲染：侧栏格式分类 ---------------- */
const FORMAT_ICON = { pdf: '📕', image: '🌄', excel: '📗', ppt: '📙', other: '📄' };

function renderFormats() {
    const box = $('#format-list');
    const list = state.meta.formats || [];
    const cur = state.filter.format;

    const total = list.reduce((s, f) => s + (f.count || 0), 0);
    box.innerHTML = `<a class="filter-item ${cur === '' ? 'active' : ''}" data-filter="format" data-value="">
            <span>全部格式</span><span class="count">${total}</span>
        </a>` +
        list.map(f => `
        <a class="filter-item ${cur === f.key ? 'active' : ''}" data-filter="format" data-value="${f.key}">
            <span>${FORMAT_ICON[f.key] || '📄'} ${esc(f.label)}</span>
            <span class="count">${f.count}</span>
        </a>`).join('');

    $$('#format-list [data-filter="format"]').forEach(el => {
        el.onclick = () => {
            state.filter.format = el.dataset.value;
            renderFormats();
            loadDocuments();
        };
    });
}

/* ---------------- 数据加载 ---------------- */
let _loadSeq = 0;   // 请求序号：防止慢的旧响应覆盖新筛选结果
async function loadDocuments() {
    const seq = ++_loadSeq;
    const params = new URLSearchParams();
    if (state.query) params.set('q', state.query);
    if (state.filter.tag) params.set('tag', state.filter.tag);
    if (state.filter.correspondent) params.set('correspondent', state.filter.correspondent);

    const data = await api('/api/documents?' + params.toString());
    if (seq !== _loadSeq) return;   // 已有更新的请求，本次结果作废

    let docs = data.items;

    // 格式筛选在前端做（后端没有对应字段索引，且需与状态筛选叠加）
    if (state.filter.format) {
        docs = docs.filter(d => (d.format || 'other') === state.filter.format);
    }

    // 状态筛选在前端做（后端按 status 过滤与服务中状态会冲突）
    if (state.filter.status === 'working') {
        docs = docs.filter(d => d.status === 'pending' || d.status === 'processing');
    } else if (state.filter.status) {
        docs = docs.filter(d => d.status === state.filter.status);
    }

    state.docs = docs;
    renderDocs();
    schedulePolling();
}

async function loadTags() {
    const data = await api('/api/tags');
    state.tags = data.items;
    renderTags();
}

async function loadRules() {
    const data = await api('/api/rules');
    state.rules = data.items;
    $('#rules-badge').textContent = data.items.length;
    renderRules();
}

async function loadMeta() {
    state.meta = await api('/api/meta');
    renderCorrespondents();
    renderFormats();
    const s = state.meta.stats || {};
    $('#stats').innerHTML = `
        <div>文档 <b>${s.total || 0}</b> 份 · 已处理 <b>${s.done || 0}</b></div>
        <div>OCR 识别 <b>${s.ocred || 0}</b> · 文本层 <b>${s.text_layer || 0}</b></div>
        <div>累计文字 <b>${(s.chars || 0).toLocaleString()}</b> 字</div>`;
}

async function refreshAll() {
    await Promise.all([loadDocuments(), loadTags(), loadMeta()]);
}

/* ---------------- 轮询（处理中的文档） ---------------- */
function schedulePolling() {
    const busy = state.docs.some(d => d.status === 'pending' || d.status === 'processing');
    if (busy) startPolling(); else stopPolling();
}

function startPolling() {
    if (state.pollTimer) return;
    const startedAt = Date.now();
    const MAX_MS = 10 * 60 * 1000;   // 兜底：最多轮询 10 分钟，避免个别文档卡住时无限刷
    state.pollTimer = setInterval(async () => {
        try {
            await loadDocuments();
            await loadTags();
            await loadMeta();
            const stillBusy = state.docs.some(d => d.status === 'pending' || d.status === 'processing');
            if (!stillBusy || Date.now() - startedAt > MAX_MS) {
                stopPolling();
                if (state.selectedId) openDetail(state.selectedId);
            }
        } catch (e) { stopPolling(); }
    }, 2000);
}

function stopPolling() {
    if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}

/* ---------------- 上传 ---------------- */
function uploadFiles(files) {
    if (!files || !files.length) return;
    const form = new FormData();
    for (const f of files) form.append('files', f);

    const list = $('#upload-list');
    list.innerHTML = Array.from(files).map(f =>
        `<div class="upload-item">
            <span class="status-dot status-processing"></span>
            <span class="name">${esc(f.name)}</span>
            <span class="st">上传中…</span>
         </div>`).join('');
    $('#upload-modal').hidden = false;

    fetch('/api/documents', { method: 'POST', body: form })
        .then(r => r.json())
        .then(j => {
            if (j.rejected && j.rejected.length) {
                toast(j.rejected.map(x => x.name + '：' + x.reason).join('；'), 'err');
            }
            if (j.created && j.created.length) {
                list.innerHTML = j.created.map(id =>
                    `<div class="upload-item" data-upload-id="${id}">
                        <span class="status-dot status-processing"></span>
                        <span class="name">已接收，正在识别…</span>
                        <span class="st">文档 #${id}</span>
                     </div>`).join('');
                startPolling();
                watchUpload(j.created);
            } else {
                $('#upload-modal').hidden = true;
            }
            refreshAll();
        })
        .catch(e => {
            $('#upload-modal').hidden = true;
            toast('上传失败：' + e.message, 'err');
        });
}

/* 跟踪上传文档的进度，全部完成后自动关闭弹窗。
   注意：每次 tick 只拉一次文档列表再本地过滤，避免逐个请求造成请求风暴。 */
function watchUpload(ids) {
    const idset = new Set(ids);
    const startedAt = Date.now();
    const timer = setInterval(async () => {
        try {
            const data = await api('/api/documents');
            const tracked = (data.items || []).filter(d => idset.has(d.id));
            if (!tracked.length) { clearInterval(timer); $('#upload-modal').hidden = true; return; }
            let allDone = true;
            const items = tracked.map(d => {
                if (d.status === 'pending' || d.status === 'processing') allDone = false;
                return `<div class="upload-item">
                    <span class="status-dot status-${d.status}"></span>
                    <span class="name">${esc(d.title || d.original_name)}</span>
                    <span class="st">${statusLabel(d)}${(d.tags || []).length ? ' · ' + d.tags.map(t => t.name).join(',') : ''}</span>
                </div>`;
            });
            $('#upload-list').innerHTML = items.join('');
            // 兜底：最多盯 10 分钟，防止状态异常导致永远轮询
            if (allDone || Date.now() - startedAt > 10 * 60 * 1000) {
                clearInterval(timer);
                if (allDone) {
                    setTimeout(() => { $('#upload-modal').hidden = true; }, 1500);
                    refreshAll();
                }
            }
        } catch (e) { clearInterval(timer); }
    }, 2500);
}

/* ---------------- 规则管理 ---------------- */
function renderRules() {
    const body = $('#rules-body');
    if (!state.rules.length) {
        body.innerHTML = '<tr><td colspan="6" style="color:var(--faint);text-align:center;padding:18px">还没有规则</td></tr>';
        return;
    }
    const meta = { fields: {}, match_types: {} };
    body.innerHTML = state.rules.map(r => `
        <tr class="${r.enabled ? '' : 'rule-off'}">
            <td><span class="tag" style="color:${esc(r.tag_color)}">
                <span class="dot" style="background:${esc(r.tag_color)}"></span>${esc(r.tag_name)}</span></td>
            <td style="color:var(--dim);font-size:12.5px">${esc(FLABEL[r.field] || r.field)}</td>
            <td style="color:var(--dim);font-size:12.5px">${esc(MLABEL[r.match_type] || r.match_type)}</td>
            <td class="rule-pattern">${esc(r.pattern)}</td>
            <td style="color:var(--faint)">${r.hit_count || 0}</td>
            <td>
                <button class="mini-btn" data-toggle="${r.id}" title="${r.enabled ? '停用' : '启用'}">${r.enabled ? '✓' : '○'}</button>
                <button class="mini-btn" data-del="${r.id}" title="删除" style="color:var(--bad)">×</button>
            </td>
        </tr>`).join('');

    $$('#rules-body [data-toggle]').forEach(b => {
        b.onclick = async () => {
            const id = parseInt(b.dataset.toggle, 10);
            const r = state.rules.find(x => x.id === id);
            await api('/api/rules/' + id, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: r.enabled ? 0 : 1 })
            });
            await loadRules();
        };
    });
    $$('#rules-body [data-del]').forEach(b => {
        b.onclick = async () => {
            if (!confirm('删除这条规则？')) return;
            await api('/api/rules/' + b.dataset.del, { method: 'DELETE' });
            await loadRules();
        };
    });
}

const MLABEL = { any: '任一关键词', all: '全部关键词', regex: '正则', exact: '完全相等', fulltext: '全文子串' };
const FLABEL = { any: '标题+正文', title: '标题', content: '正文', correspondent: '往来单位' };

/* ---------------- 事件绑定 ---------------- */
function bindEvents() {
    // 搜索
    let searchTimer = null;
    let suggestDelay = null;
    $('#search').oninput = (e) => {
        const v = e.target.value;
        $('.searchbox').classList.toggle('has-value', !!v.trim());
        clearTimeout(searchTimer);
        // 自动补全：更短的防抖，输入即提示
        clearTimeout(suggestDelay);
        if (v.trim().length >= 1) {
            suggestDelay = setTimeout(() => fetchSuggest(v.trim()), 180);
        } else {
            hideSuggest();
        }
        searchTimer = setTimeout(() => {
            state.query = v.trim();
            loadDocuments();
            if (state.selectedId) openDetail(state.selectedId);
        }, 280);
    };
    $('#search').onkeydown = (e) => {
        if (e.key === 'Escape') hideSuggest();
        if (e.key === 'Enter') hideSuggest();
    };
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.searchbox')) hideSuggest();
    });
    $('#search-clear').onclick = () => {
        $('#search').value = '';
        $('.searchbox').classList.remove('has-value');
        state.query = '';
        loadDocuments();
    };

    // 状态筛选
    $$('[data-filter="status"]').forEach(el => {
        el.onclick = () => {
            $$('[data-filter="status"]').forEach(x => x.classList.remove('active'));
            el.classList.add('active');
            state.filter.status = el.dataset.value;
            loadDocuments();
        };
    });

    // 视图切换
    $$('[data-view]').forEach(el => {
        el.onclick = () => {
            $$('[data-view]').forEach(x => x.classList.remove('active'));
            el.classList.add('active');
            state.view = el.dataset.view;
            renderDocs();
        };
    });

    // 上传
    const dz = $('#dropzone'), input = $('#file-input');
    dz.onclick = () => input.click();
    input.onchange = () => { uploadFiles(input.files); input.value = ''; };
    $('#btn-upload').onclick = () => input.click();

    ['dragenter', 'dragover'].forEach(ev => {
        dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('dragover'); });
    });
    ['dragleave', 'drop'].forEach(ev => {
        dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('dragover'); });
    });
    dz.addEventListener('drop', (e) => {
        if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
    });
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => {
        e.preventDefault();
        if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
    });

    // 弹窗
    $$('[data-close]').forEach(el => {
        el.onclick = () => { $('#' + el.dataset.close).hidden = true; };
    });
    $$('.modal').forEach(m => {
        m.addEventListener('click', (e) => { if (e.target === m) m.hidden = true; });
    });
    $('#btn-rules').onclick = () => { loadRules(); $('#rules-modal').hidden = false; };

    // 新建规则
    $('#btn-rule-save').onclick = async () => {
        const name = $('#rule-tag').value.trim();
        const pattern = $('#rule-pattern').value.trim();
        if (!name || !pattern) { toast('标签名和匹配内容都要填', 'err'); return; }
        try {
            await api('/api/rules', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name, pattern,
                    field: $('#rule-field').value,
                    match_type: $('#rule-match').value
                })
            });
            $('#rule-tag').value = '';
            $('#rule-pattern').value = '';
            toast('规则已添加，正在对所有文档重跑', 'ok');
            await api('/api/rules/apply', { method: 'POST' });
            await loadRules();
            await refreshAll();
        } catch (e) { toast(e.message, 'err'); }
    };

    $('#btn-rules-apply').onclick = async () => {
        const j = await api('/api/rules/apply', { method: 'POST' });
        toast('已对 ' + j.applied + ' 份文档重跑规则', 'ok');
        await refreshAll();
    };

    // 新建标签
    $('#btn-add-tag').onclick = async () => {
        const name = prompt('新标签名称：');
        if (!name || !name.trim()) return;
        const res = await api('/api/tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name.trim() })
        });
        await loadTags();
        if (res.auto) {
            toast(`标签已创建，自动匹配到 ${res.auto.tagged} 份文档`, 'ok');
        } else {
            toast('标签已创建', 'ok');
        }
    };

    // ESC 关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            $$('.modal').forEach(m => { m.hidden = true; });
            if (state.selectedId) closeDetail();
        }
    });
}

/* ---------------- 文件夹导入 ---------------- */
const folderState = { items: [] };   // {path, name, rel, size, status, detail, checked}

const FOLDER_STATUS = {
    'new':       { label: '新文件', cls: 'new' },
    'dup-db':    { label: '重复',   cls: 'dup' },
    'dup-batch': { label: '批内重复', cls: 'dup' },
};

function renderFolderList() {
    const box = $('#folder-list');
    const summary = $('#folder-scan-summary');
    const importBtn = $('#btn-folder-import');

    if (!folderState.items.length) {
        box.innerHTML = '<div class="folder-empty">还没有文件，先输入文件夹路径点「扫描」</div>';
        summary.hidden = true;
        importBtn.disabled = true;
        $('#folder-count').textContent = '';
        return;
    }

    const newN = folderState.items.filter(i => i.status === 'new').length;
    const dupN = folderState.items.length - newN;
    summary.hidden = false;
    summary.innerHTML = `共扫描到 <b>${folderState.items.length}</b> 个可上传文件：` +
        `新文件 <b>${newN}</b> 个` +
        (dupN ? `，<span class="warn-text">重复 ${dupN} 个（已默认不勾选，重复内容只保留一个）</span>` : '');

    box.innerHTML = folderState.items.map((it, idx) => {
        const st = FOLDER_STATUS[it.status] || FOLDER_STATUS['new'];
        return `
        <div class="folder-item ${it.status === 'new' ? '' : 'duplicate'}">
            <div class="fi-main">
                <label class="fi-check">
                    <input type="checkbox" data-idx="${idx}" ${it.checked ? 'checked' : ''}>
                </label>
                <div class="fi-info">
                    <div class="fi-name">${esc(it.name)}<span class="fi-badge ${st.cls}">${st.label}</span></div>
                    <div class="fi-sub">${esc(it.rel)} · ${fmtSize(it.size)}${it.detail ? ' · ' + esc(it.detail) : ''}</div>
                </div>
            </div>
            <div class="fi-foot"><button class="fi-del" data-del="${idx}">删除</button></div>
        </div>`;
    }).join('');

    const selN = folderState.items.filter(i => i.checked).length;
    $('#folder-count').textContent = `已选 ${selN} 个文件`;
    importBtn.disabled = selN === 0;
    importBtn.textContent = `导入所选（${selN}）`;

    $$('#folder-list input[type=checkbox]').forEach(el => {
        el.onchange = () => {
            folderState.items[parseInt(el.dataset.idx, 10)].checked = el.checked;
            const n = folderState.items.filter(i => i.checked).length;
            $('#folder-count').textContent = `已选 ${n} 个文件`;
            importBtn.disabled = n === 0;
            importBtn.textContent = `导入所选（${n}）`;
        };
    });
    $$('#folder-list .fi-del').forEach(el => {
        el.onclick = () => {
            folderState.items.splice(parseInt(el.dataset.del, 10), 1);
            renderFolderList();
        };
    });
}

async function scanFolder() {
    const path = $('#folder-path').value.trim();
    if (!path) { toast('请先输入文件夹路径', 'warn'); return; }
    const btn = $('#btn-scan');
    btn.disabled = true; btn.textContent = '扫描中…';
    try {
        const res = await api('/api/scan-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
        });
        if (res.error) { showNotice('扫描失败', esc(res.error)); return; }

        folderState.items = (res.items || []).map(it => ({ ...it, checked: it.status === 'new' }));

        // 关键：一个可导入文件都没扫到时，必须弹窗明确告知原因，不能静默
        if (!folderState.items.length) {
            renderFolderList();
            const total = res.total_files || 0;
            const skipped = res.skipped_others || 0;
            const support = 'PDF、图片（PNG / JPG / BMP / TIFF / WebP）、Excel（.xlsx / .xlsm / .xls）、PPT（.pptx / .ppt）';
            const body = total === 0
                ? `这个文件夹（含子文件夹）里<b>没有任何文件</b>，所以没有可导入的内容。`
                : `这个文件夹里共 <b>${total}</b> 个文件，但没有一个是可导入的格式` +
                  `（已跳过 <b>${skipped}</b> 个不支持的文件）。`;
            showNotice('没有扫描到可导入的文件',
                `${body}<div class="notice-path">${esc(path)}</div>` +
                `<div class="notice-tip">当前支持的格式：${support}<br>` +
                `请确认路径是否填对，或把文件另存为上述格式后再扫描。</div>`);
            return;
        }

        if (res.skipped_others > 0) {
            toast(`已扫描，另有 ${res.skipped_others} 个不支持的文件未列出`, 'warn');
        }
        renderFolderList();
    } catch (e) {
        showNotice('扫描失败',
            `没能读取这个文件夹：<div class="notice-path">${esc(path)}</div>` +
            `<div class="notice-tip">${esc(e.message || '未知错误')}<br>` +
            `常见原因：路径不存在、拼写错误、或该文件夹没有读取权限。</div>`);
    } finally {
        btn.disabled = false; btn.textContent = '扫描';
    }
}

async function importFolder() {
    const paths = folderState.items.filter(i => i.checked).map(i => i.path);
    if (!paths.length) return;
    const btn = $('#btn-folder-import');
    btn.disabled = true; btn.textContent = '导入中…';
    try {
        const res = await api('/api/import-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths })
        });
        if (res.error) { toast(res.error, 'warn'); return; }
        const n = (res.created || []).length;
        const s = (res.skipped || []);
        if (s.length) {
            toast(`已导入 ${n} 个文件；跳过重复/无效 ${s.length} 个（重复内容只保留一个）`, 'warn');
            console.log('跳过明细：', s);
        } else {
            toast(`已导入 ${n} 个文件`, 'ok');
        }
        $('#folder-modal').hidden = true;
        folderState.items = [];
        $('#folder-path').value = '';
        startPolling();
        await refreshAll();
    } finally {
        btn.disabled = false; btn.textContent = '导入所选';
    }
}

$('#btn-folder').onclick = () => {
    folderState.items = [];
    $('#folder-path').value = '';
    renderFolderList();
    $('#folder-modal').hidden = false;
};
$('#btn-scan').onclick = scanFolder;
$('#btn-folder-import').onclick = importFolder;
$('#folder-path').addEventListener('keydown', e => { if (e.key === 'Enter') scanFolder(); });

// 保存的视图
$('#btn-save-view').onclick = () => {
    if (!state.query && !state.filter.tag && !state.filter.status) {
        toast('先设好搜索词或筛选条件，再保存视图');
        return;
    }
    const name = prompt('给这个视图起个名字（如：本月发票）：', state.query || '我的视图');
    if (!name || !name.trim()) return;
    const tag = state.tags.find(t => t.id === state.filter.tag);
    const views = loadViews();
    views.push({
        name: name.trim(),
        query: state.query,
        tag: state.filter.tag,
        tag_name: tag ? tag.name : '',
        tag_color: tag ? tag.color : '',
        status: state.filter.status,
    });
    saveViews(views);
    renderViews();
    toast('视图已保存，点左侧即可一键应用');
};

// 批量标签
$('#btn-batch').onclick = () => state.batch.mode ? exitBatch() : enterBatch();
$('#batch-exit').onclick = exitBatch;
$('#batch-tag').onchange = updateBatchBar;
$('#batch-add').onclick = () => doBatch('add');
$('#batch-remove').onclick = () => doBatch('remove');

renderViews();

/* ---------------- 搜索自动补全（借鉴 paperless-ngx） ---------------- */
let suggestTimer = null;
let suggestSeq = 0;
async function fetchSuggest(q) {
    const seq = ++suggestSeq;
    try {
        const j = await api('/api/suggest?q=' + encodeURIComponent(q));
        if (seq !== suggestSeq) return;          // 已有更新的请求，作废
        renderSuggest(j.titles || [], j.tags || []);
    } catch (e) { hideSuggest(); }
}

function renderSuggest(titles, tags) {
    let box = $('#suggest-box');
    if (!titles.length && !tags.length) { hideSuggest(); return; }
    const itemHtml = (icon, label, cls, data) =>
        `<div class="suggest-item ${cls}" ${data}><span class="s-icon">${icon}</span>${esc(label)}</div>`;
    box.innerHTML =
        tags.map(t => itemHtml('🏷', t.name, 's-tag', `data-tagid="${t.id}" data-tagname="${esc(t.name)}"`)).join('') +
        titles.map(t => itemHtml('📄', t.name, 's-title', `data-title="${esc(t.name)}"`)).join('');
    box.hidden = false;
    $$('#suggest-box .suggest-item').forEach(el => {
        el.onclick = () => {
            const input = $('#search');
            if (el.dataset.tagid) {
                // 点标签候选：直接按该标签筛选（比当关键词搜更准）
                state.filter.tag = parseInt(el.dataset.tagid, 10);
                input.value = el.dataset.tagname;
                state.query = '';
                $('.searchbox').classList.remove('has-value');
                loadDocuments(); loadMeta();
            } else {
                input.value = el.dataset.title;
                state.query = el.dataset.title;
                $('.searchbox').classList.add('has-value');
                loadDocuments();
            }
            hideSuggest();
        };
    });
}

function hideSuggest() {
    const box = $('#suggest-box');
    if (box) box.hidden = true;
}

/* ---------------- 保存的视图（借鉴 paperless-ngx / Docspell 书签） ---------------- */
const VIEWS_KEY = 'pl_saved_views';
function loadViews() {
    try { return JSON.parse(localStorage.getItem(VIEWS_KEY)) || []; }
    catch (e) { return []; }
}
function saveViews(views) {
    localStorage.setItem(VIEWS_KEY, JSON.stringify(views));
}
function renderViews() {
    const box = $('#view-list');
    const views = loadViews();
    if (!views.length) {
        box.innerHTML = '<div style="padding:4px 8px;color:var(--faint);font-size:12px">暂无视图。设好搜索/筛选后点右上「＋」保存</div>';
        return;
    }
    box.innerHTML = views.map((v, i) => {
        const parts = [];
        if (v.query) parts.push(`「${esc(v.query)}」`);
        if (v.tag_name) parts.push(`<span style="color:${esc(v.tag_color || 'var(--accent)')}">🏷 ${esc(v.tag_name)}</span>`);
        if (v.status) parts.push(esc(v.status_label || v.status));
        return `<div class="filter-item view-item" data-view="${i}">
            <span class="name" title="${esc(parts.join(' + '))}">📌 ${esc(v.name)}</span>
            <span class="tag-del" data-delview="${i}" title="删除此视图">×</span>
        </div>`;
    }).join('');
    $$('#view-list .view-item').forEach(el => {
        el.onclick = (ev) => {
            if (ev.target.dataset.delview !== undefined) return;   // 删除按钮单独处理
            applyView(loadViews()[parseInt(el.dataset.view, 10)]);
        };
    });
    $$('#view-list [data-delview]').forEach(el => {
        el.onclick = (ev) => {
            ev.stopPropagation();
            const views = loadViews();
            views.splice(parseInt(el.dataset.delview, 10), 1);
            saveViews(views);
            renderViews();
            toast('视图已删除');
        };
    });
}
async function applyView(v) {
    if (!v) return;
    // 退出批量模式，避免视图切换后选中态错乱
    if (state.batch.mode) exitBatch();
    state.query = v.query || '';
    $('#search').value = state.query;
    $('.searchbox').classList.toggle('has-value', !!state.query);
    state.filter.tag = v.tag || null;
    state.filter.status = v.status || '';
    $$('.filter-item[data-filter="status"]').forEach(el =>
        el.classList.toggle('active', el.dataset.value === state.filter.status));
    renderTags();          // 让标签高亮跟随
    renderViews();
    await loadDocuments();
    if (state.selectedId) closeDetail();
}

/* ---------------- 批量标签模式 ---------------- */
function enterBatch() {
    state.batch.mode = true;
    state.batch.selected.clear();
    $('#btn-batch').classList.add('active');
    $('#batch-bar').hidden = false;
    const sel = $('#batch-tag');
    sel.innerHTML = '<option value="">— 选择标签 —</option>' +
        state.tags.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('');
    updateBatchBar();
    renderDocs();
    toast('批量模式：点选文档，再统一加/删标签');
}
function exitBatch() {
    state.batch.mode = false;
    state.batch.selected.clear();
    $('#btn-batch').classList.remove('active');
    $('#batch-bar').hidden = true;
    renderDocs();
}
function toggleBatch(id) {
    if (state.batch.selected.has(id)) state.batch.selected.delete(id);
    else state.batch.selected.add(id);
    $$('#doc-grid .doc-card').forEach(el => {
        el.classList.toggle('batch-selected', state.batch.selected.has(parseInt(el.dataset.id, 10)));
    });
    updateBatchBar();
}
function updateBatchBar() {
    $('#batch-n').textContent = state.batch.selected.size;
    $('#batch-add').disabled = $('#batch-remove').disabled = !state.batch.selected.size;
    $('#batch-add').disabled = $('#batch-remove').disabled =
        (!state.batch.selected.size || !$('#batch-tag').value);
}
async function doBatch(action) {
    const tagId = parseInt($('#batch-tag').value, 10);
    const ids = Array.from(state.batch.selected);
    if (!tagId || !ids.length) return;
    const j = await api('/api/documents/batch-tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids, tag_id: tagId, action }),
    });
    toast(`已${action === 'add' ? '添加' : '移除'} ${j.updated} 份` +
          (j.skipped ? `，跳过 ${j.skipped} 份（无编辑权限）` : ''));
    await Promise.all([loadDocuments(), loadMeta()]);
    if (state.selectedId) openDetail(state.selectedId);
}


(async function init() {
    bindEvents();
    // 先处理身份（退出/用户管理按钮），再加载文档——2 核机上 refreshAll 较慢，别让按钮等文档
    try {
        const s = await (await fetch('/api/auth/status')).json();
        if (s && (s.logged_in || s.need_setup)) {
            const b = $('#btn-logout');
            if (b) b.hidden = false;
        }
        if (s && s.is_admin) {
            const ub = $('#btn-users');
            if (ub) ub.hidden = false;   // 只有管理员看得到「用户管理」
        }
    } catch (e) {}
    await refreshAll();
    await loadRules();
})();

/* ---------------- 用户管理（仅管理员） ---------------- */
async function loadUsers() {
    try {
        const j = await api('/api/users');
        state.users = j.users || [];
    } catch (e) {
        state.users = [];
        return;
    }
    const box = $('#user-list');
    if (!state.users.length) {
        box.innerHTML = '<tr><td colspan="5" style="color:var(--faint);text-align:center;padding:14px">还没有用户</td></tr>';
        return;
    }
    box.innerHTML = state.users.map(u => `
        <tr>
            <td><b>${esc(u.username)}</b>${u.is_me ? ' <span style="color:var(--faint)">（我）</span>' : ''}</td>
            <td>${u.is_admin ? '<span class="role-admin">管理员</span>' : '<span class="role-user">普通用户</span>'}</td>
            <td>${u.doc_count || 0} 份</td>
            <td style="color:var(--faint);font-size:12px">${esc(u.created_at || '')}</td>
            <td class="user-ops">
                <button class="btn btn-xs" data-reset="${esc(u.username)}">重置密码</button>
                ${u.is_me ? '' : `<button class="btn btn-xs btn-danger" data-del="${esc(u.username)}">删除</button>`}
            </td>
        </tr>`).join('');

    $$('#user-list [data-reset]').forEach(el => {
        el.onclick = async () => {
            const np = prompt(`给「${el.dataset.reset}」设置新密码（至少 6 位）：`);
            if (!np) return;
            if (np.length < 6) { toast('密码至少 6 位', 'warn'); return; }
            try {
                await api(`/api/users/${encodeURIComponent(el.dataset.reset)}/password`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: np })
                });
                toast('密码已重置', 'ok');
            } catch (e) { toast(e.message, 'warn'); }
        };
    });
    $$('#user-list [data-del]').forEach(el => {
        el.onclick = async () => {
            if (!confirm(`确定删除用户「${el.dataset.del}」？\nTA 上传的文档会保留在库里（管理员仍可管理）。`)) return;
            try {
                await api(`/api/users/${encodeURIComponent(el.dataset.del)}`, { method: 'DELETE' });
                toast('已删除用户', 'ok');
                await loadUsers();
            } catch (e) { toast(e.message, 'warn'); }
        };
    });
}

$('#btn-users').onclick = async () => {
    $('#users-modal').hidden = false;
    $('#nu-hint').textContent = '';
    $('#nu-name').value = '';
    $('#nu-pass').value = '';
    $('#nu-admin').checked = false;
    await loadUsers();
};

$('#btn-user-add').onclick = async () => {
    const name = $('#nu-name').value.trim();
    const pass = $('#nu-pass').value;
    const isAdmin = $('#nu-admin').checked ? 1 : 0;
    const hint = $('#nu-hint');
    if (name.length < 2) { hint.textContent = '账号至少 2 个字符'; return; }
    if (pass.length < 6) { hint.textContent = '密码至少 6 位'; return; }
    try {
        await api('/api/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: name, password: pass, is_admin: isAdmin })
        });
        hint.textContent = '';
        $('#nu-name').value = '';
        $('#nu-pass').value = '';
        $('#nu-admin').checked = false;
        toast(`已添加用户「${name}」`, 'ok');
        await loadUsers();
    } catch (e) { hint.textContent = e.message; }
};

/* 退出登录：同事共用的机器上换人时用 */
$('#btn-logout').onclick = async () => {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
    } catch (e) {}
    location.href = '/login';
};
