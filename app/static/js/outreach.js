/* ============================================
   邮件审核中心（Phase1 外联）
   ============================================ */
'use strict';

let _senderAccounts = [];
const STATUS_BADGE = {
    draft: 'bg-secondary', pending: 'bg-warning text-dark', approved: 'bg-info text-dark',
    sending: 'bg-primary', sent: 'bg-success', rejected: 'bg-danger',
    failed: 'bg-danger', cancelled: 'bg-dark',
};

function _statusBadge(s) {
    return `<span class="badge ${STATUS_BADGE[s] || 'bg-secondary'}">${_esc(s)}</span>`;
}

async function loadSenderAccounts() {
    try {
        const r = await _fetchWithTimeout('/api/mail-sender-accounts');
        _senderAccounts = r.accounts || [];
        const sel = document.getElementById('rmSenderAccount');
        if (!sel) return;
        sel.innerHTML = '';
        if (!_senderAccounts.length) {
            sel.innerHTML = '<option value="">（未配置发件账户，请到 AI 设置页添加）</option>';
        } else {
            _senderAccounts.forEach(a => {
                const opt = document.createElement('option');
                opt.value = a.id;
                opt.textContent = `${a.email_address}${a.enabled ? '' : '（已停用）'}`;
                sel.appendChild(opt);
            });
        }
    } catch (e) { console.warn('加载发件账户失败', e); }
}

/* ── 待审批卡片 ── */
async function loadPending() {
    const box = document.getElementById('pendingList');
    try {
        const r = await _fetchWithTimeout('/api/outreach/drafts?status=pending&page_size=50');
        document.getElementById('statPending').textContent = r.total;
        const drafts = r.drafts || [];
        if (!drafts.length) {
            box.innerHTML = `<div class="col-12"><div class="card border-0"><div class="card-body text-center text-secondary py-5">
                <i class="bi bi-check2-all fs-1 d-block mb-2"></i>暂无待审批草稿。<br>
                <span class="small">请先在客户详情页用「AI 开发信生成」创建草稿，生成后可在此审批发送。</span></div></div></div>`;
            return;
        }
        box.innerHTML = drafts.map(d => {
            const risks = (d.risk_flags || []).map(x => `<span class="badge bg-danger me-1">${_esc(x)}</span>`).join('');
            return `<div class="col-md-6 col-xl-4">
                <div class="card border-0 h-100">
                    <div class="card-body p-3">
                        <div class="d-flex justify-content-between mb-1">
                            <span class="fw-bold small text-truncate" style="max-width:70%;" title="${_esc(d.subject)}">${_esc(d.subject)}</span>
                            ${_statusBadge(d.status)}
                        </div>
                        <div class="text-secondary small mb-2">
                            <i class="bi bi-building me-1"></i>客户 #${d.customer_id}
                            ${d.model ? `<span class="badge bg-light text-dark ms-1">${_esc(d.model)}</span>` : ''}
                            ${d.language ? `<span class="badge bg-light text-dark">${_esc(d.language)}</span>` : ''}
                        </div>
                        <div class="mb-2">${risks || '<span class="text-success small"><i class="bi bi-shield-check"></i> 无风险标记</span>'}</div>
                        <div class="text-secondary small border rounded p-2 mb-2" style="max-height:120px; overflow:auto; white-space:pre-wrap;">${_esc(d.body.slice(0, 300))}${d.body.length > 300 ? '…' : ''}</div>
                        <button class="btn btn-sm btn-outline-primary w-100" onclick="openReview(${d.id})"><i class="bi bi-eye"></i> 查看 / 审批</button>
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch (e) { box.innerHTML = `<div class="text-danger">加载失败：${_esc(e.message)}</div>`; }
}

/* ── 全部草稿表 ── */
async function loadAllDrafts() {
    const status = document.getElementById('draftStatusFilter')?.value || '';
    const customerId = document.getElementById('draftCustomerId')?.value?.trim() || '';
    let url = '/api/outreach/drafts?page_size=100';
    if (status) url += '&status=' + encodeURIComponent(status);
    if (customerId) url += '&customer_id=' + encodeURIComponent(customerId);
    try {
        const r = await _fetchWithTimeout(url);
        const body = document.getElementById('allDraftsBody');
        const drafts = r.drafts || [];
        if (!drafts.length) {
            body.innerHTML = `<tr><td colspan="7" class="text-center text-secondary py-4">暂无草稿</td></tr>`;
            return;
        }
        body.innerHTML = drafts.map(d => `<tr>
            <td>${d.id}</td>
            <td><a href="/customer/${d.customer_id}" onclick="navigate(event,'/customer/${d.customer_id}')">#${d.customer_id}</a></td>
            <td class="text-truncate" style="max-width:260px;" title="${_esc(d.subject)}">${_esc(d.subject)}</td>
            <td>${_statusBadge(d.status)}</td>
            <td class="small text-secondary">${_esc(d.model || '')}${d.prompt_version_id ? ` · v${d.prompt_version_id}` : ''}</td>
            <td class="small text-secondary">${_esc(_fmtDateTime(d.created_at))}</td>
            <td>${d.status === 'pending' ? `<button class="btn btn-sm btn-outline-primary" onclick="openReview(${d.id})">审批</button>` : `<button class="btn btn-sm btn-outline-secondary" onclick="openDraftDetail(${d.id})">查看</button>`}</td>
        </tr>`).join('');
    } catch (e) { showToast('加载草稿失败：' + e.message, 'danger'); }
}

/* ── 发送记录 ── */
async function loadSendLogs() {
    try {
        const r = await _fetchWithTimeout('/api/outreach/send-logs?page_size=100');
        const body = document.getElementById('sendLogsBody');
        const logs = r.logs || [];
        if (!logs.length) {
            body.innerHTML = `<tr><td colspan="8" class="text-center text-secondary py-4">暂无发送记录</td></tr>`;
            return;
        }
        body.innerHTML = logs.map(l => `<tr>
            <td>${l.id}</td>
            <td>${l.draft_id || '-'}</td>
            <td>${_esc(l.to_address)}</td>
            <td class="text-truncate" style="max-width:220px;" title="${_esc(l.subject)}">${_esc(l.subject)}</td>
            <td>${_statusBadge(l.status)}</td>
            <td class="small text-secondary text-truncate" style="max-width:160px;" title="${_esc(l.provider_message_id || l.internet_message_id || '')}">${_esc((l.provider_message_id || l.internet_message_id || '').slice(0, 60))}</td>
            <td class="small text-secondary">${_esc(_fmtDateTime(l.sent_at || l.created_at))}</td>
            <td class="small text-danger text-truncate" style="max-width:180px;" title="${_esc(l.error_message || '')}">${_esc(l.error_message || '')}</td>
        </tr>`).join('');
    } catch (e) { showToast('加载发送记录失败：' + e.message, 'danger'); }
}

/* ── 任务中心 ── */
async function loadTasks() {
    try {
        const r = await _fetchWithTimeout('/api/outreach/tasks?page_size=100');
        const body = document.getElementById('tasksBody');
        const tasks = r.tasks || [];
        if (!tasks.length) {
            body.innerHTML = `<tr><td colspan="7" class="text-center text-secondary py-4">暂无任务</td></tr>`;
            return;
        }
        body.innerHTML = tasks.map(t => `<tr>
            <td>${t.id}</td>
            <td>${_esc(t.task_type)}</td>
            <td>${_statusBadge(t.status)}</td>
            <td>${t.attempts}/${t.max_attempts}</td>
            <td class="small text-danger text-truncate" style="max-width:240px;" title="${_esc(t.error_message || '')}">${_esc(t.error_message || '')}</td>
            <td class="small text-secondary">${_esc(_fmtDateTime(t.created_at))}</td>
            <td>${['queued', 'retry_wait'].includes(t.status) ? `<button class="btn btn-sm btn-outline-danger" onclick="cancelTask(${t.id})">取消</button>` : ''}</td>
        </tr>`).join('');
    } catch (e) { showToast('加载任务失败：' + e.message, 'danger'); }
}

/* ── 黑名单 ── */
async function loadBlacklist() {
    try {
        const r = await _fetchWithTimeout('/api/outreach/blacklist?page_size=100');
        const body = document.getElementById('blacklistBody');
        const entries = r.entries || [];
        if (!entries.length) {
            body.innerHTML = `<tr><td colspan="7" class="text-center text-secondary py-4">名单为空</td></tr>`;
            return;
        }
        body.innerHTML = entries.map(e => `<tr>
            <td>${e.id}</td>
            <td>${_esc(e.email || '-')}</td>
            <td>${_esc(e.domain || '-')}</td>
            <td><span class="badge bg-danger">${_esc(e.reason)}</span></td>
            <td class="small text-secondary">${_esc(e.note || '')}</td>
            <td class="small text-secondary">${_esc(_fmtDateTime(e.created_at))}</td>
            <td><button class="btn btn-sm btn-outline-secondary" onclick="removeBlacklist(${e.id})"><i class="bi bi-trash"></i></button></td>
        </tr>`).join('');
    } catch (e) { showToast('加载名单失败：' + e.message, 'danger'); }
}

async function addBlacklist() {
    const payload = {
        email: document.getElementById('blEmail').value.trim(),
        domain: document.getElementById('blDomain').value.trim(),
        reason: document.getElementById('blReason').value,
        note: document.getElementById('blNote').value.trim(),
    };
    if (!payload.email && !payload.domain) { showToast('请填写邮箱或域名', 'warning'); return; }
    try {
        await _fetchWithTimeout('/api/outreach/blacklist', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        showToast('已加入禁止联系名单', 'success');
        document.getElementById('blEmail').value = '';
        document.getElementById('blDomain').value = '';
        document.getElementById('blNote').value = '';
        loadBlacklist();
    } catch (e) { showToast(e.message, 'danger'); }
}

async function removeBlacklist(id) {
    if (!confirm('确认移出名单？')) return;
    try {
        await _fetchWithTimeout(`/api/outreach/blacklist/${id}`, { method: 'DELETE' });
        showToast('已移出名单', 'success');
        loadBlacklist();
    } catch (e) { showToast(e.message, 'danger'); }
}

async function cancelTask(id) {
    if (!confirm('确认取消该任务？')) return;
    try {
        await _fetchWithTimeout(`/api/outreach/tasks/${id}/cancel`, { method: 'POST' });
        showToast('任务已取消', 'success');
        loadTasks();
    } catch (e) { showToast(e.message, 'danger'); }
}

/* ── 审批弹窗 ── */
let _currentDraft = null;

async function openReview(id) {
    try {
        const r = await _fetchWithTimeout(`/api/outreach/drafts/${id}`);
        const d = r.draft;
        _currentDraft = d;
        document.getElementById('rmDraftId').textContent = d.id;
        document.getElementById('rmCustomer').textContent = d.customer_id;
        document.getElementById('rmModel').textContent = d.model || '模型未知';
        document.getElementById('rmLang').textContent = d.language || '';
        document.getElementById('rmRecipient').value = d.recipient_email || '';
        document.getElementById('rmSubject').value = d.subject;
        document.getElementById('rmBody').value = d.body;
        const facts = (d.facts_used || []).join('；');
        document.getElementById('rmFacts').textContent = facts || '（无）';
        const riskBox = document.getElementById('rmRiskBox');
        if (d.risk_flags && d.risk_flags.length) {
            riskBox.style.display = '';
            riskBox.innerHTML = '<i class="bi bi-exclamation-triangle"></i> ' + d.risk_flags.map(_esc).join('；');
        } else {
            riskBox.style.display = 'none';
        }
        document.getElementById('rmNote').value = '';
        // 溯源详情
        if (r.generation_run && r.generation_run.input_snapshot) {
            const snap = r.generation_run.input_snapshot || {};
            const extra = document.getElementById('rmFacts');
            if (typeof snap === 'object') {
                extra.textContent = (extra.textContent || '') + '；事实快照：' + JSON.stringify(snap).slice(0, 400);
            }
        }
        await loadSenderAccounts();
        // 默认选中该草稿已绑账户
        if (d.sender_account_id) {
            const sel = document.getElementById('rmSenderAccount');
            for (const o of sel.options) if (Number(o.value) === d.sender_account_id) { sel.value = o.value; break; }
        }
        const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('reviewModal'));
        modal.show();
    } catch (e) { showToast('加载草稿失败：' + e.message, 'danger'); }
}

async function openDraftDetail(id) {
    // 复用审批弹窗（只读意图），非 pending 时隐藏发送操作由状态判断
    await openReview(id);
    const sendBtns = document.querySelectorAll('#reviewModal .btn-accent, #reviewModal .btn-outline-danger');
    // 简单处理：全部草稿查看仅看内容与状态
    const d = _currentDraft;
    if (d.status !== 'pending') {
        document.getElementById('rmNote').placeholder = '（状态：' + d.status + '）';
    }
}

async function saveDraftEdit() {
    const d = _currentDraft;
    if (!d) return;
    try {
        await _fetchWithTimeout(`/api/outreach/drafts/${d.id}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                subject: document.getElementById('rmSubject').value,
                body: document.getElementById('rmBody').value,
                recipient_email: document.getElementById('rmRecipient').value,
            }),
        });
        showToast('草稿已保存（可再次提交审批）', 'success');
        loadPending(); loadAllDrafts();
    } catch (e) { showToast(e.message, 'danger'); }
}

async function approveDraft() {
    const d = _currentDraft;
    const sel = document.getElementById('rmSenderAccount');
    const accountId = sel.value;
    if (!accountId) { showToast('请先选择发件账户（AI 设置页可添加）', 'warning'); return; }
    const note = document.getElementById('rmNote').value.trim();
    try {
        await _fetchWithTimeout(`/api/outreach/drafts/${d.id}/approve`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sender_account_id: Number(accountId), note, recipient_email: document.getElementById('rmRecipient').value }),
        });
        showToast('审批通过，正在发送…', 'success');
        await _fetchWithTimeout(`/api/outreach/drafts/${d.id}/send`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ immediate: true }),
        });
        showToast('发送任务已处理', 'success');
        bootstrap.Modal.getInstance(document.getElementById('reviewModal'))?.hide();
        loadPending(); loadAllDrafts(); loadSendLogs(); loadTasks();
    } catch (e) { showToast(e.message, 'danger'); }
}

async function rejectDraft() {
    const d = _currentDraft;
    const reason = document.getElementById('rmNote').value.trim();
    if (!reason) { showToast('拒绝时必须填写原因', 'warning'); return; }
    try {
        await _fetchWithTimeout(`/api/outreach/drafts/${d.id}/reject`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason }),
        });
        showToast('草稿已拒绝', 'success');
        bootstrap.Modal.getInstance(document.getElementById('reviewModal'))?.hide();
        loadPending(); loadAllDrafts();
    } catch (e) { showToast(e.message, 'danger'); }
}

/* ── 汇总 ── */
function _fmtDateTime(s) {
    if (!s) return '';
    const d = new Date(s);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

async function loadCounts() {
    try {
        const [p, s, all, tasks] = await Promise.all([
            _fetchWithTimeout('/api/outreach/drafts?status=pending&page_size=1'),
            _fetchWithTimeout('/api/outreach/send-logs?status=sent&page_size=1'),
            _fetchWithTimeout('/api/outreach/drafts?status=draft&page_size=1'),
            _fetchWithTimeout('/api/outreach/tasks?page_size=1'),
        ]);
        document.getElementById('statPending').textContent = p.total;
        document.getElementById('statSent').textContent = s.total;
        document.getElementById('statDrafts').textContent = all.total;
        document.getElementById('statFailed').textContent = tasks.total;
    } catch (e) { /* 忽略 */ }
}

function loadAll() {
    loadCounts();
    loadPending();
    loadAllDrafts();
    loadSendLogs();
    loadTasks();
    loadBlacklist();
    loadSenderAccounts();
}

document.addEventListener('DOMContentLoaded', () => {
    loadAll();
    // Tab 切换时懒刷新
    document.getElementById('outreachTabs')?.addEventListener('shown.bs.tab', (e) => {
        const target = e.target.getAttribute('data-bs-target');
        if (target === '#tabLogs') loadSendLogs();
        if (target === '#tabTasks') loadTasks();
        if (target === '#tabBlacklist') loadBlacklist();
    });
});
