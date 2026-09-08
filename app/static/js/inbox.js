/* ============================================
   回复中心（Phase2：回复同步 / AI 分类 / 动作待办）
   ============================================ */
'use strict';

let _msgPage = 1;
let _msgDetail = null;

const INTENT_BADGE = {
    rfq: 'bg-warning text-dark', interest: 'bg-info text-dark', question: 'bg-primary',
    not_interested: 'bg-secondary', unsubscribe: 'bg-dark', out_of_office: 'bg-secondary',
    bounce: 'bg-danger', complaint: 'bg-danger', other: 'bg-secondary', null: 'bg-light text-secondary',
};
const INTENT_LABEL = {
    rfq: '询价', interest: '意向', question: '提问', not_interested: '婉拒',
    unsubscribe: '退订', out_of_office: '自动回复', bounce: '退信', complaint: '投诉', other: '其他', null: '未分类',
};
const TODO_TYPE_LABEL = {
    respond: '需回复', follow_up: '跟进', quote_request: '询价', complaint: '投诉', other: '其他',
};

function _intentBadge(c) {
    return `<span class="badge ${INTENT_BADGE[c] || 'bg-light text-secondary'}">${INTENT_LABEL[c] || _esc(c)}</span>`;
}
function _prioBadge(p) {
    const map = { 1: 'bg-danger', 2: 'bg-warning text-dark', 3: 'bg-secondary' };
    return `<span class="badge ${map[p] || 'bg-secondary'}">${p === 1 ? '高' : p === 2 ? '中' : '低'}</span>`;
}
function _todoStatusBadge(s) {
    const map = { open: 'bg-primary', done: 'bg-success', dismissed: 'bg-secondary' };
    const label = { open: '进行中', done: '已完成', dismissed: '已忽略' };
    return `<span class="badge ${map[s] || 'bg-secondary'}">${label[s] || _esc(s)}</span>`;
}
function _ts(iso) {
    if (!iso) return '-';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* ── 消息列表 ── */
async function loadMessages() {
    const body = document.getElementById('messagesBody');
    const cls = document.getElementById('msgClassFilter').value;
    const handled = document.getElementById('msgHandledFilter').value;
    const matched = document.getElementById('onlyMatched').checked;
    const q = new URLSearchParams();
    q.set('page', _msgPage);
    q.set('page_size', 20);
    if (cls) q.set('classification', cls);
    if (handled) q.set('handled', handled);
    if (matched) q.set('matched_only', '1');
    try {
        const r = await _fetchWithTimeout('/api/inbox/messages?' + q.toString());
        const msgs = r.messages || [];
        document.getElementById('statTotal').textContent = r.total;
        body.innerHTML = msgs.length ? msgs.map(m => {
            const att = m.has_attachments ? '<i class="bi bi-paperclip" style="color:var(--text-secondary);"></i>' : '';
            return `<tr>
                <td>${_intentBadge(m.classification)}</td>
                <td class="small">${_esc(m.customer_name || '—')}<br><span class="text-secondary">${_esc(m.matched_domain || '未匹配')}</span></td>
                <td class="small">${_esc(m.from_address || '')}</td>
                <td class="small" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${_esc(m.subject)}">${_esc(m.subject || '(无主题)')}</td>
                <td class="small text-secondary" style="white-space:nowrap;">${_ts(m.received_at || m.sent_at)}</td>
                <td>${m.is_handled ? '<span class="badge bg-success">已处理</span>' : '<span class="badge bg-warning text-dark">未处理</span>'}</td>
                <td>${att}</td>
                <td class="small" style="white-space:nowrap;">
                    <button class="btn btn-sm btn-outline-primary" onclick="openMessage(${m.id})"><i class="bi bi-eye"></i> 查看</button>
                </td>
            </tr>`;
        }).join('') : `<tr><td colspan="8" class="text-center text-secondary py-5">暂无回复邮件。点右上角「立即同步」拉取收件箱。</td></tr>`;

        // 统计卡片
        const stats = { unhandled: 0, rfq: 0, bounce: 0, matched: 0 };
        msgs.forEach(m => {
            if (!m.is_handled) stats.unhandled++;
            if (m.classification === 'rfq') stats.rfq++;
            if (m.classification === 'bounce' || m.classification === 'unsubscribe') stats.bounce++;
            if (m.customer_id) stats.matched++;
        });
        document.getElementById('statUnhandled').textContent = stats.unhandled;
        document.getElementById('statRfq').textContent = stats.rfq;
        document.getElementById('statBounce').textContent = stats.bounce;
        document.getElementById('statMatched').textContent = stats.matched;

        // 分页
        const pager = document.getElementById('msgPager');
        pager.innerHTML = '';
        if (r.total_pages > 1) {
            const prev = document.createElement('nav');
            prev.innerHTML = `<ul class="pagination pagination-sm mb-0">
                <li class="page-item ${_msgPage <= 1 ? 'disabled' : ''}"><button class="page-link" onclick="goMsgPage(${_msgPage - 1})">‹</button></li>
                <li class="page-item disabled"><span class="page-link">${_msgPage}/${r.total_pages}</span></li>
                <li class="page-item ${_msgPage >= r.total_pages ? 'disabled' : ''}"><button class="page-link" onclick="goMsgPage(${_msgPage + 1})">›</button></li>
            </ul>`;
            pager.appendChild(prev);
        }
    } catch (e) {
        body.innerHTML = `<tr><td colspan="8" class="text-center text-danger py-4">加载失败: ${_esc(e.message)}</td></tr>`;
    }
}

function goMsgPage(p) {
    _msgPage = Math.max(1, p);
    loadMessages();
}

/* ── 邮件详情 ── */
async function openMessage(id) {
    try {
        const r = await _fetchWithTimeout('/api/inbox/messages/' + id, {}, 30000);
        _msgDetail = r;
        document.getElementById('modalTitle').textContent = r.subject || '(无主题)';
        document.getElementById('modalMeta').innerHTML =
            `<i class="bi bi-envelope me-1"></i>${_esc(r.from_address)}` +
            (r.account_email ? ` → <span class="text-secondary">${_esc(r.account_email)}</span>` : '') +
            ` · ${_ts(r.received_at || r.sent_at)}` +
            (r.customer_name ? ` · <a href="/customers/${r.customer_id}" target="_blank">${_esc(r.customer_name)}</a>` : '');
        let clsHtml = `${_intentBadge(r.classification)}`;
        const payload = r.classification_payload;
        if (payload) {
            const bits = [];
            if (payload.quantity) bits.push(`数量: ${_esc(payload.quantity)}`);
            if (payload.product) bits.push(`产品: ${_esc(payload.product)}`);
            if (payload.destination) bits.push(`目的地: ${_esc(payload.destination)}`);
            if (payload.missing_fields && payload.missing_fields.length) bits.push(`待确认: ${_esc(payload.missing_fields.join(', '))}`);
            if (payload.summary) bits.push(`摘要: ${_esc(payload.summary)}`);
            clsHtml += bits.map(b => `<span class="badge bg-light text-dark ms-1 fw-normal">${b}</span>`).join('');
            if (payload.needs_human_review) clsHtml += ' <span class="badge bg-danger">需人工复核</span>';
        }
        document.getElementById('modalClass').innerHTML = clsHtml;
        document.getElementById('modalBody').textContent = r.body || '(正文已外置/为空)';

        const attBox = document.getElementById('modalAttachments');
        attBox.innerHTML = (r.attachments || []).length ? '<h6 class="small fw-bold mt-2">附件</h6>' + r.attachments.map(a =>
            `<a class="btn btn-sm btn-outline-secondary me-1 mb-1" href="/api/inbox/messages/${r.id}/attachments/${a.id}" download>
                <i class="bi bi-paperclip"></i> ${_esc(a.filename || a.id)} (${Math.round((a.size_bytes || 0) / 1024)}KB)</a>`
        ).join('') : '';

        const modal = new bootstrap.Modal(document.getElementById('msgDetailModal'));
        modal.show();
    } catch (e) {
        alert('加载邮件详情失败: ' + e.message);
    }
}

function openCustomerFromModal() {
    if (_msgDetail && _msgDetail.customer_id) {
        window.open('/customers/' + _msgDetail.customer_id, '_blank');
        return;
    }
    alert('该邮件尚未匹配到客户');
}

function makeDraftFromModal() {
    if (!_msgDetail || !_msgDetail.customer_id) {
        alert('该邮件尚未匹配到客户，无法生成跟进草稿');
        return;
    }
    window.open('/customers/' + _msgDetail.customer_id, '_blank');
}

async function ignoreModalMessage() {
    if (!_msgDetail) return;
    try {
        await _fetchWithTimeout(`/api/inbox/messages/${_msgDetail.id}/ignore`, { method: 'POST' });
        bootstrap.Modal.getInstance(document.getElementById('msgDetailModal')).hide();
        loadMessages();
        loadAll();
    } catch (e) { alert('忽略失败: ' + e.message); }
}

/* ── 待办 ── */
async function loadTodos() {
    const box = document.getElementById('todosList');
    const status = document.getElementById('todoStatusFilter').value;
    const type = document.getElementById('todoTypeFilter').value;
    const q = new URLSearchParams();
    q.set('page', 1);
    q.set('page_size', 50);
    if (status) q.set('status', status);
    try {
        const r = await _fetchWithTimeout('/api/inbox/todos?' + q.toString());
        const todos = r.todos || [];
        document.getElementById('statTodos').textContent = todos.filter(t => t.status === 'open').length;
        box.innerHTML = todos.length ? todos.map(t =>
            `<div class="col-md-6 col-xl-4"><div class="card border-0 h-100">
                <div class="card-body p-3">
                    <div class="d-flex justify-content-between mb-1">
                        <span class="small fw-bold">${TODO_TYPE_LABEL[t.todo_type] || _esc(t.todo_type)}</span>
                        <span>${_prioBadge(t.priority)} ${_todoStatusBadge(t.status)}</span>
                    </div>
                    <div class="fw-bold small mb-1 text-truncate" title="${_esc(t.summary)}">${_esc(t.summary || '(无摘要)')}</div>
                    <div class="small text-secondary mb-2">
                        ${_esc(t.customer_name || '客户 #' + t.customer_id)}<br>
                        <span class="text-muted">${_ts(t.created_at)}</span>
                    </div>
                    ${t.details ? (t.details.product ? `<div class="small text-secondary mb-2">产品: ${_esc(t.details.product)}${t.details.quantity ? ' · 数量: ' + _esc(t.details.quantity) : ''}${t.details.destination ? ' · 目的地: ' + _esc(t.details.destination) : ''}</div>` : '') : ''}
                    <div class="d-flex gap-1 flex-wrap">
                        ${t.status === 'open'
                            ? `<button class="btn btn-sm btn-outline-success" onclick="doneTodo(${t.id})"><i class="bi bi-check2"></i> 完成</button>
                               <button class="btn btn-sm btn-outline-danger" onclick="dismissTodo(${t.id})"><i class="bi bi-x"></i> 忽略</button>` 
                            : `<button class="btn btn-sm btn-outline-secondary" onclick="reopenTodo(${t.id})"><i class="bi bi-arrow-counterclockwise"></i> 重开</button>`}
                        ${t.customer_id ? `<a class="btn btn-sm btn-outline-primary" href="/customers/${t.customer_id}" target="_blank"><i class="bi bi-person"></i> 客户</a>` : ''}
                    </div>
                </div>
            </div></div>`
        ).join('') : `<div class="col-12"><div class="card border-0"><div class="card-body text-center text-secondary py-5"><i class="bi bi-check2-all fs-1 d-block mb-2"></i>暂无待办</div></div></div>`;
    } catch (e) {
        box.innerHTML = `<div class="col-12"><div class="card border-0"><div class="card-body text-danger text-center py-4">加载失败: ${_esc(e.message)}</div></div></div>`;
    }
}

async function doneTodo(id) {
    try { await _fetchWithTimeout(`/api/inbox/todos/${id}/done`, { method: 'POST' }); loadTodos(); loadMessages(); }
    catch (e) { alert(e.message); }
}
async function reopenTodo(id) {
    try { await _fetchWithTimeout(`/api/inbox/todos/${id}/reopen`, { method: 'POST' }); loadTodos(); }
    catch (e) { alert(e.message); }
}
async function dismissTodo(id) {
    try { await _fetchWithTimeout(`/api/inbox/todos/${id}/dismiss`, { method: 'POST' }); loadTodos(); }
    catch (e) { alert(e.message); }
}

/* ── 同步 ── */
async function syncInbox(manual) {
    const btn = document.getElementById('btnSync');
    const old = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 同步中…';
    try {
        const r = await _fetchWithTimeout('/api/inbox/sync', { method: 'POST' }, 60000);
        alert(r.message || '同步完成');
        _msgPage = 1;
        loadMessages();
        loadTodos();
        if (manual && typeof _toast === 'function') _toast(r.message, 'success');
    } catch (e) {
        alert('同步失败: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = old;
    }
}

/* ── 汇总刷新 ── */
async function loadAll() {
    _msgPage = 1;
    await Promise.allSettled([loadMessages(), loadTodos()]);
}