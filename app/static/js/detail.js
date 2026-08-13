/* ============================================
   AI Trade Customer Analyzer V4.6 - 客户详情页
   ============================================ */

const customerId = window.location.pathname.split('/').pop();
let detailPollTimer = null;

// ── 加载详情 ──
async function loadDetail() {
    try {
        const c = await _fetchWithTimeout(`/api/customers/${customerId}`);
        renderDetail(c);
        if (c.is_analyzing) {
            showAnalyzingBanner();
            if (!detailPollTimer) detailPollTimer = setInterval(pollDetail, 2000);
        }
    } catch (err) {
        document.getElementById('loadingState').innerHTML =
            `<div class="alert alert-danger mb-0"><i class="bi bi-exclamation-triangle me-2"></i>加载失败: ${_esc(err.message)}</div>`;
    }
}

async function pollDetail() {
    try {
        const c = await _fetchWithTimeout(`/api/customers/${customerId}`);
        if (!c.is_analyzing) {
            hideAnalyzingBanner();
            clearInterval(detailPollTimer);
            detailPollTimer = null;
            renderDetail(c);
        }
    } catch (err) { console.error('轮询失败:', err); }
}

function showAnalyzingBanner() {
    document.getElementById('analyzingBanner').classList.remove('d-none');
    document.getElementById('analyzingText').textContent = '正在分析该客户，请稍候...';
    const btn = document.getElementById('btnAnalyze');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>分析中...';
    document.getElementById('btnStopDetail').style.display = 'inline-block';
}

function hideAnalyzingBanner() {
    document.getElementById('analyzingBanner').classList.add('d-none');
    const btn = document.getElementById('btnAnalyze');
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-lightning-fill me-1"></i>分析此客户';
    document.getElementById('btnStopDetail').style.display = 'none';
}

// ── 渲染详情 ──
function renderDetail(c) {
    document.getElementById('loadingState').classList.add('d-none');
    document.getElementById('mainContent').classList.remove('d-none');

    document.getElementById('companyName').textContent = c.company_name;
    document.getElementById('country').textContent = c.country || '-';
    const siteEl = document.getElementById('website');
    if (c.website) {
        siteEl.href = 'https://' + _esc(c.website);
        siteEl.textContent = _esc(c.website);
        const domainEl = document.getElementById('hunterDomain');
        if (domainEl) domainEl.value = _esc(c.website);
        const wfDomainEl = document.getElementById('waterfallDomain');
        if (wfDomainEl) wfDomainEl.value = _esc(c.website);
    } else {
        siteEl.textContent = '-';
    }

    const sourceBadge = document.getElementById('discoverySourceBadge');
    if (c.discovery_source === 'Google') {
        sourceBadge.innerHTML = '<span class="badge badge-source google">Google 发现</span>';
    } else if (c.discovery_source === 'Similar') {
        sourceBadge.innerHTML = '<span class="badge badge-source similar">相似扩展</span>';
    } else {
        sourceBadge.innerHTML = '<span class="badge badge-source excel">手动导入</span>';
    }

    const discInfo = document.getElementById('discoveryInfo');
    let infoParts = [];
    if (c.discovery_keyword) infoParts.push(`关键词: ${_esc(c.discovery_keyword)}`);
    if (c.first_found_at) infoParts.push(`发现时间: ${_fmtDate(c.first_found_at)}`);
    discInfo.textContent = infoParts.length ? infoParts.join(' | ') : '';

    const scoreEl = document.getElementById('scoreDisplay');
    const hasScore = c.total_score !== null && c.total_score !== undefined;
    if (hasScore) {
        const g = _gradeLabel(c.total_score);
        scoreEl.innerHTML = `<div class="score-number ${g.cls}">${c.total_score}</div><div class="score-label">总分</div>`;
    } else {
        scoreEl.innerHTML = '<div class="score-number text-muted">--</div><div class="score-label">未评分</div>';
    }

    const pb = document.getElementById('priorityBadge');
    if (c.priority && c.priority !== '-') {
        const cls = c.priority === 'A' ? 'bg-danger' : c.priority === 'B' ? 'bg-warning text-dark' : c.priority === 'C' ? 'bg-info text-dark' : 'bg-secondary';
        pb.className = `badge rounded-pill ${cls}`;
        pb.textContent = `优先级 ${c.priority}`;
    } else {
        pb.className = 'badge rounded-pill bg-secondary';
        pb.textContent = '未评级';
    }

    const tb = document.getElementById('companyTypeBadge');
    if (c.company_type) {
        tb.className = 'badge badge-status analyzed';
        tb.textContent = _esc(c.company_type);
    } else {
        tb.classList.add('d-none');
    }

    // V4.6：买家意向评分徽章（评分分级移植）
    const bib = document.getElementById('buyerIntentBadge');
    if (c.buyer_intent_score !== null && c.buyer_intent_score !== undefined) {
        const s = c.buyer_intent_score;
        const cls = s >= 8 ? 'bg-success' : s >= 5 ? 'bg-info text-dark' : s >= 1 ? 'bg-warning text-dark' : 'bg-secondary';
        const label = s >= 8 ? '高购买意向' : s >= 5 ? '中购买意向' : s >= 1 ? '低购买意向' : '非采购方';
        bib.className = `badge rounded-pill ${cls}`;
        bib.title = 'AI 买家意向评分（0-10，供应商/制造商通常为低分）';
        bib.innerHTML = `<i class="bi bi-cart-check me-1"></i>${label} ${s}/10`;
    } else {
        bib.className = 'badge rounded-pill d-none';
    }

    // V4.6：价格询盘标记
    const pib = document.getElementById('priceInquiryBadge');
    if (c.is_price_inquiry) {
        pib.className = 'badge rounded-pill bg-danger';
        pib.title = 'AI 识别到客户在询价（RFQ / 报价请求）';
        pib.innerHTML = '<i class="bi bi-fire me-1"></i>价格询盘';
    } else {
        pib.className = 'badge rounded-pill d-none';
    }

    document.getElementById('aiSummary').textContent = c.ai_summary || '暂无 AI 摘要';

    renderScoreDetail(c);

    // V4.6：回显已保存的开发信草稿
    const draft = c.email_draft || {};
    if (draft && draft.subject && draft.body) {
        renderEmailDraft(draft);
        if (!document.getElementById('emailDraftKeywords').value) {
            document.getElementById('emailDraftKeywords').value = c.discovery_keyword || '';
        }
    }

    const records = c.email_records || [];
    const legacyCount = (c.emails || []).length;
    if (records.length === 0 && legacyCount > 0) {
        renderLegacyEmailHint(legacyCount);
    } else {
        renderCustomerEmails(records);
    }

    const kc = document.getElementById('keywordsContainer');
    let kh = '<div class="mb-3"><strong class="text-success small">正向关键词</strong><div class="d-flex flex-wrap gap-1 mt-1">';
    const positiveKws = c.positive_keywords || {};
    const positiveKeys = Object.keys(positiveKws);
    if (positiveKeys.length > 0) {
        positiveKeys.forEach(kw => { kh += `<span class="kw-positive">${_esc(kw)} (${positiveKws[kw]})</span>`; });
    } else {
        kh += '<span class="text-muted small">未命中</span>';
    }
    kh += '</div></div><div><strong class="text-danger small">负向关键词</strong><div class="d-flex flex-wrap gap-1 mt-1">';
    const negativeKws = c.negative_keywords || {};
    const negativeKeys = Object.keys(negativeKws);
    if (negativeKeys.length > 0) {
        negativeKeys.forEach(kw => { kh += `<span class="kw-negative">${_esc(kw)} (${negativeKws[kw]})</span>`; });
    } else {
        kh += '<span class="text-muted small">未命中</span>';
    }
    kh += '</div></div>';
    kc.innerHTML = kh;

    const ac = document.getElementById('aiResultContainer');
    const aiRaw = c.ai_raw || {};
    if (Object.keys(aiRaw).length === 0) {
        ac.innerHTML = '<p class="text-muted small mb-0">尚未进行 AI 分析</p>';
    } else {
        let aiHtml = `
            <div class="mb-3">
                <strong class="small">公司类型</strong>
                <div><span class="badge badge-status analyzed mt-1">${_esc(aiRaw.company_type || '未知')}</span></div>
            </div>`;

        // V4.6：产品匹配关键词
        if (aiRaw.product_match) {
            aiHtml += `<div class="mb-3"><strong class="text-primary small">产品匹配</strong>
                <div class="d-flex flex-wrap gap-1 mt-1"><span class="kw-positive">${_esc(aiRaw.product_match)}</span></div></div>`;
        }

        // V4.6：识别到的客户需求
        const needs = aiRaw.needs_identified || [];
        if (needs.length > 0) {
            aiHtml += `<div class="mb-3"><strong class="text-success small">客户需求</strong><ul class="mb-0 mt-1 small">${needs.map(n => `<li>${_esc(n)}</li>`).join('')}</ul></div>`;
        }

        aiHtml += `
            <div>
                <strong class="small">分析原因</strong>
                <p class="text-muted mt-1 mb-0" style="font-size:0.88rem">${_esc(aiRaw.analysis_reason || '暂无分析')}</p>
            </div>`;
        ac.innerHTML = aiHtml;
    }

    const hc = document.getElementById('salesHookContainer');
    let hh = '';
    if (c.sales_hook) {
        hh += `<div class="mb-3"><strong class="text-success small"><i class="bi bi-bullseye me-1"></i>开发切入点</strong><p class="mt-1">${_esc(c.sales_hook)}</p></div>`;
    }
    if (c.target_position) {
        hh += `<div><strong class="text-primary small"><i class="bi bi-person-badge me-1"></i>推荐联系职位</strong><p class="mt-1">${_esc(c.target_position)}</p></div>`;
    }
    hc.innerHTML = hh || '<p class="text-muted small mb-0">暂无开发建议</p>';

    const pc = document.getElementById('projectContainer');
    pc.innerHTML = c.identified_projects
        ? `<p class="mb-0" style="font-size:0.9rem">${_esc(c.identified_projects)}</p>`
        : '<p class="text-muted small mb-0">暂无项目信息</p>';

    const te = document.getElementById('websiteText');
    if (c.website_text) {
        te.textContent = c.website_text;
        document.getElementById('textLength').textContent = `${c.website_text.length} 字符`;
    } else {
        te.textContent = '暂无官网内容';
        document.getElementById('textLength').textContent = '0 字符';
    }

    document.getElementById('followUpStatus').value = c.status || '待联系';
    document.getElementById('followUpDate').value = c.follow_up_date || '';
    document.getElementById('followUpNotes').value = c.notes || '';
    document.getElementById('starRating').value = c.star_rating || 0;

    let statusIcons = '';
    if (c.scrape_status === 'success') statusIcons += '<span class="badge badge-status analyzed ms-1">已抓取</span>';
    else if (c.scrape_status === 'failed') statusIcons += '<span class="badge badge-status error ms-1">抓取失败</span>';
    if (c.ai_status === 'success') statusIcons += '<span class="badge badge-status analyzed ms-1">已分析</span>';
    else if (c.ai_status === 'failed') statusIcons += '<span class="badge badge-status error ms-1">分析失败</span>';
    if (c.fail_reason) statusIcons += `<span class="badge badge-status stopped ms-1" title="${_esc(c.fail_reason)}">异常</span>`;
    if (statusIcons) {
        const container = document.getElementById('discoverySourceBadge');
        container.insertAdjacentHTML('afterend', statusIcons);
    }
}

// ── 邮箱联系人管理（V5.1） ──
let _customerEmailRecords = [];

const EMAIL_SOURCE_LABELS = {
    website: '官网', hunter: 'Hunter', tomba: 'Tomba', prospeo: 'Prospeo',
    manual: '手动', legacy: '旧数据', migrated: '迁移',
};

function emailSourceLabel(source) {
    return EMAIL_SOURCE_LABELS[source] || source || '未知';
}

function emailAddForm() {
    return `
        <hr class="my-2">
        <form class="row g-2 align-items-center" onsubmit="addCustomerEmail(event)">
            <div class="col-12 col-sm">
                <input type="email" class="form-control form-control-sm" id="newEmailInput" placeholder="输入邮箱地址" required>
            </div>
            <div class="col-12 col-sm">
                <input type="text" class="form-control form-control-sm" id="newEmailNotes" placeholder="备注（可选）">
            </div>
            <div class="col-auto">
                <div class="form-check form-check-inline mb-0">
                    <input class="form-check-input" type="checkbox" id="newEmailPrimary">
                    <label class="form-check-label small" for="newEmailPrimary">设为主要</label>
                </div>
            </div>
            <div class="col-auto">
                <button type="submit" class="btn btn-sm btn-accent"><i class="bi bi-plus-lg me-1"></i>添加邮箱</button>
            </div>
        </form>`;
}

function renderCustomerEmails(records) {
    _customerEmailRecords = Array.isArray(records) ? records : [];
    const ec = document.getElementById('emailsContainer');
    const mergeBtn = document.getElementById('btnMergeLegacy');

    if (_customerEmailRecords.length === 0) {
        let html = '<p class="text-muted small mb-2">暂未提取到邮箱</p>';
        const website = (document.getElementById('website').textContent || '').trim();
        if (website && website !== '-') {
            html += `<button class="btn btn-sm btn-outline-info mb-2" onclick="document.getElementById('hunterTab').click()"><i class="bi bi-envelope-at me-1"></i>查找邮箱</button>`;
        }
        ec.innerHTML = html + emailAddForm();
        if (mergeBtn) mergeBtn.style.display = 'none';
        return;
    }

    const rows = _customerEmailRecords.map(r => {
        const vBadge = r.verification === 'valid' ? 'success' : r.verification === 'invalid' ? 'danger' : r.verification === 'risky' ? 'warning text-dark' : 'secondary';
        const vLabel = r.verification === 'valid' ? '已验证' : r.verification === 'invalid' ? '无效' : r.verification === 'risky' ? '风险' : '未知';
        const primaryStar = r.is_primary
            ? '<span class="text-warning" title="主要邮箱"><i class="bi bi-star-fill"></i></span>'
            : `<a href="#" class="text-muted text-decoration-none" title="设为主要邮箱" onclick="event.preventDefault(); setPrimaryEmail(${r.id})"><i class="bi bi-star"></i></a>`;
        const contactInfo = [r.first_name, r.last_name].filter(Boolean).join(' ')
            + (r.position ? (r.position ? ' · ' + r.position : '') : '')
            + (r.department ? ' · ' + r.department : '');
        return `
        <div class="email-record ${r.is_primary ? 'email-primary' : ''}">
            <div class="d-flex justify-content-between align-items-start gap-2 flex-wrap">
                <div class="flex-grow-1">
                    <div class="d-flex align-items-center gap-2 flex-wrap">
                        <span class="email-badge"><i class="bi bi-envelope-fill"></i> ${_esc(r.email)}</span>
                        ${primaryStar}
                    </div>
                    <div class="small text-muted mt-1">
                        <span class="badge badge-source ${r.source}" style="font-size:0.7rem">${emailSourceLabel(r.source)}</span>
                        <span class="badge bg-${vBadge}" style="font-size:0.7rem">${vLabel}</span>
                        ${r.score ? `<span class="badge bg-secondary" style="font-size:0.7rem">${r.score}分</span>` : ''}
                        ${contactInfo ? `<span class="ms-1">${_esc(contactInfo)}</span>` : ''}
                        ${r.notes ? `<span class="ms-1"><i class="bi bi-chat-left-text"></i> ${_esc(r.notes)}</span>` : ''}
                    </div>
                </div>
                <div class="d-flex gap-1 flex-shrink-0">
                    <button class="btn btn-sm btn-outline-secondary" onclick="_copyText('${_esc(r.email)}')" title="复制邮箱"><i class="bi bi-clipboard"></i></button>
                    <button class="btn btn-sm btn-outline-primary" onclick="editCustomerEmail(${r.id})" title="编辑"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteCustomerEmail(${r.id})" title="删除"><i class="bi bi-trash"></i></button>
                </div>
            </div>
        </div>`;
    }).join('');

    ec.innerHTML = `
        <div class="mb-3">
            ${rows}
        </div>
        ${emailAddForm()}`;

    const website = (document.getElementById('website').textContent || '').trim();
    if (mergeBtn) {
        mergeBtn.style.display = website && website !== '-' ? 'inline-block' : 'none';
    }
}

function renderLegacyEmailHint(count) {
    _customerEmailRecords = [];
    const ec = document.getElementById('emailsContainer');
    const mergeBtn = document.getElementById('btnMergeLegacy');
    ec.innerHTML = `
        <div class="alert alert-warning py-2 px-3 mb-2 small">
            <i class="bi bi-exclamation-triangle me-1"></i>检测到 <strong>${count}</strong> 个旧版邮箱尚未合并到新邮箱表。
            <button class="btn btn-sm btn-warning ms-2" onclick="mergeLegacyEmails()"><i class="bi bi-arrow-repeat me-1"></i>立即合并</button>
        </div>
        ${emailAddForm()}`;
    if (mergeBtn) mergeBtn.style.display = 'inline-block';
}

async function loadCustomerEmails() {
    try {
        const data = await _fetchWithTimeout(`/api/customers/${customerId}/emails`);
        renderCustomerEmails(data.emails || []);
    } catch (err) {
        console.error('加载邮箱失败:', err);
    }
}

async function addCustomerEmail(e) {
    e.preventDefault();
    const email = document.getElementById('newEmailInput').value.trim();
    const notes = document.getElementById('newEmailNotes').value.trim();
    const is_primary = document.getElementById('newEmailPrimary').checked;
    if (!email) return;
    try {
        await _fetchWithTimeout(`/api/customers/${customerId}/emails`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, notes, is_primary }),
        });
        document.getElementById('newEmailInput').value = '';
        document.getElementById('newEmailNotes').value = '';
        document.getElementById('newEmailPrimary').checked = false;
        showToast('邮箱已添加', 'success');
        await loadCustomerEmails();
    } catch (err) {
        showToast('添加失败: ' + err.message, 'danger');
    }
}

async function setPrimaryEmail(id) {
    try {
        await _fetchWithTimeout(`/api/customer-emails/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_primary: true }),
        });
        showToast('已设为主要邮箱', 'success');
        await loadCustomerEmails();
    } catch (err) {
        showToast('操作失败: ' + err.message, 'danger');
    }
}

async function editCustomerEmail(id) {
    const record = _customerEmailRecords.find(r => r.id === id);
    if (!record) return;
    const newEmail = prompt('邮箱地址', record.email);
    if (newEmail === null) return;
    const newNotes = prompt('备注（留空保持不变）', record.notes || '');
    if (newNotes === null) return;
    const body = { email: newEmail.trim(), notes: newNotes };
    try {
        await _fetchWithTimeout(`/api/customer-emails/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        showToast('邮箱已更新', 'success');
        await loadCustomerEmails();
    } catch (err) {
        showToast('更新失败: ' + err.message, 'danger');
    }
}

async function deleteCustomerEmail(id) {
    const record = _customerEmailRecords.find(r => r.id === id);
    if (!record) return;
    if (!confirm(`确定删除邮箱 ${record.email} 吗？`)) return;
    try {
        await _fetchWithTimeout(`/api/customer-emails/${id}`, { method: 'DELETE' });
        showToast('邮箱已删除', 'success');
        await loadCustomerEmails();
    } catch (err) {
        showToast('删除失败: ' + err.message, 'danger');
    }
}

async function mergeLegacyEmails() {
    if (!confirm('把旧版 JSON 邮箱字段合并到新邮箱表？此操作幂等，可重复执行。')) return;
    try {
        const data = await _fetchWithTimeout(`/api/customers/${customerId}/emails/merge-legacy`, { method: 'POST' });
        showToast(`已合并 ${data.merged} 个旧邮箱`, 'success');
        await loadCustomerEmails();
    } catch (err) {
        showToast('合并失败: ' + err.message, 'danger');
    }
}

// ── LinkedIn 公司主页（V5.1） ──
let _linkedinProfiles = [];
let _linkedinOauthAuthorized = false;

const LINKEDIN_SOURCE_LABELS = { search: '搜索发现', manual: '手动添加', official_api: '官方 API' };

function linkedinSourceLabel(source) {
    return LINKEDIN_SOURCE_LABELS[source] || source || '未知';
}

async function loadLinkedinProfiles() {
    try {
        const data = await _fetchWithTimeout(`/api/customers/${customerId}/social-profiles`);
        renderLinkedinProfiles(data.profiles || []);
    } catch (err) {
        console.error('加载 LinkedIn 失败:', err);
    }
    try {
        const status = await _fetchWithTimeout('/api/linkedin/oauth/status');
        _linkedinOauthAuthorized = !!status.authorized;
        // 已授权时重新渲染以显示「官方 API 刷新」按钮
        if (_linkedinOauthAuthorized && document.getElementById('linkedinContainer')) {
            renderLinkedinProfiles(_linkedinProfiles);
        }
    } catch (err) {
        _linkedinOauthAuthorized = false;
    }
}

function renderLinkedinProfiles(profiles) {
    _linkedinProfiles = Array.isArray(profiles) ? profiles : [];
    const lc = document.getElementById('linkedinContainer');
    if (!lc) return;

    if (_linkedinProfiles.length === 0) {
        lc.innerHTML = `
            <p class="text-muted small mb-2">暂未保存 LinkedIn 公司主页</p>
            <button class="btn btn-sm btn-outline-primary mb-2" onclick="discoverLinkedin()"><i class="bi bi-search me-1"></i>通过搜索引擎查找</button>
            <form class="row g-2 align-items-center mt-1" onsubmit="addLinkedinProfile(event)">
                <div class="col-12 col-sm">
                    <input type="url" class="form-control form-control-sm" id="newLinkedinUrl" placeholder="粘贴 LinkedIn 公司页 URL" required>
                </div>
                <div class="col-auto">
                    <button type="submit" class="btn btn-sm btn-accent"><i class="bi bi-plus-lg me-1"></i>手动添加</button>
                </div>
            </form>`;
        return;
    }

    const rows = _linkedinProfiles.map(p => {
        const verifiedBadge = p.is_verified
            ? '<span class="badge bg-success" style="font-size:0.7rem"><i class="bi bi-check-circle me-1"></i>已确认</span>'
            : `<button class="btn btn-sm btn-outline-success" onclick="verifyLinkedinProfile(${p.id}, true)" title="确认此主页为公司主页"><i class="bi bi-check-lg me-1"></i>确认</button>`;
        const confPct = Math.round(p.confidence || 0);
        return `
        <div class="email-record ${p.is_verified ? 'email-primary' : ''}">
            <div class="d-flex justify-content-between align-items-start gap-2 flex-wrap">
                <div class="flex-grow-1">
                    <div class="d-flex align-items-center gap-2 flex-wrap">
                        <a href="${_esc(p.profile_url)}" target="_blank" rel="noopener" class="text-decoration-none">
                            <span class="email-badge"><i class="bi bi-linkedin"></i> ${_esc(p.display_name || p.vanity_name || p.profile_url)}</span>
                        </a>
                        ${verifiedBadge}
                    </div>
                    <div class="small text-muted mt-1">
                        <span class="badge bg-secondary" style="font-size:0.7rem">${linkedinSourceLabel(p.source)}</span>
                        ${confPct > 0 ? `<span class="badge bg-info text-dark" style="font-size:0.7rem">置信度 ${confPct}%</span>` : ''}
                        ${p.website_url ? `<span class="ms-1">官网: ${_esc(p.website_url)}</span>` : ''}
                    </div>
                </div>
                <div class="d-flex gap-1 flex-shrink-0">
                    <button class="btn btn-sm btn-outline-secondary" onclick="window.open('${_esc(p.profile_url)}','_blank')" title="打开主页"><i class="bi bi-box-arrow-up-right"></i></button>
                    ${_linkedinOauthAuthorized ? `<button class="btn btn-sm btn-outline-primary" onclick="resolveLinkedinProfile(${p.id})" title="用官方 API 刷新组织详情（名称/Logo/地点/员工规模/官网）"><i class="bi bi-arrow-repeat"></i></button>` : ''}
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteLinkedinProfile(${p.id})" title="删除"><i class="bi bi-trash"></i></button>
                </div>
            </div>
        </div>`;
    }).join('');

    lc.innerHTML = `
        <div class="mb-3">${rows}</div>
        <hr class="my-2">
        <form class="row g-2 align-items-center" onsubmit="addLinkedinProfile(event)">
            <div class="col-12 col-sm">
                <input type="url" class="form-control form-control-sm" id="newLinkedinUrl" placeholder="粘贴 LinkedIn 公司页 URL" required>
            </div>
            <div class="col-auto">
                <button type="submit" class="btn btn-sm btn-accent"><i class="bi bi-plus-lg me-1"></i>手动添加</button>
            </div>
        </form>`;
}

async function discoverLinkedin() {
    const btn = document.getElementById('btnDiscoverLinkedin') || document.getElementById('linkedinContainer');
    if (btn.tagName === 'BUTTON') {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>查找中...';
    }
    try {
        const data = await _fetchWithTimeout(`/api/customers/${customerId}/linkedin/discover`, { method: 'POST' }, 60000);
        if (data.total === 0) {
            showToast('未找到候选主页，可尝试手动粘贴 URL', 'info');
        } else {
            showToast(`找到 ${data.total} 个候选主页`, 'success');
        }
        await loadLinkedinProfiles();
    } catch (err) {
        showToast('查找失败: ' + err.message, 'danger');
    } finally {
        if (btn.tagName === 'BUTTON') {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-search me-1"></i>查找候选';
        }
    }
}

async function addLinkedinProfile(e) {
    e.preventDefault();
    const url = document.getElementById('newLinkedinUrl').value.trim();
    if (!url) return;
    try {
        await _fetchWithTimeout(`/api/customers/${customerId}/social-profiles`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_url: url }),
        });
        document.getElementById('newLinkedinUrl').value = '';
        showToast('LinkedIn 主页已保存', 'success');
        await loadLinkedinProfiles();
    } catch (err) {
        showToast('保存失败: ' + err.message, 'danger');
    }
}

async function verifyLinkedinProfile(id, verified) {
    try {
        await _fetchWithTimeout(`/api/social-profiles/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_verified: verified }),
        });
        showToast(verified ? '已确认该主页' : '已取消确认', 'success');
        await loadLinkedinProfiles();
    } catch (err) {
        showToast('操作失败: ' + err.message, 'danger');
    }
}

async function deleteLinkedinProfile(id) {
    const profile = _linkedinProfiles.find(p => p.id === id);
    if (!profile) return;
    if (!confirm(`确定删除 LinkedIn 主页 ${profile.display_name || profile.profile_url} 吗？`)) return;
    try {
        await _fetchWithTimeout(`/api/social-profiles/${id}`, { method: 'DELETE' });
        showToast('已删除', 'success');
        await loadLinkedinProfiles();
    } catch (err) {
        showToast('删除失败: ' + err.message, 'danger');
    }
}

async function resolveLinkedinProfile(id) {
    try {
        const data = await _fetchWithTimeout(`/api/social-profiles/${id}/resolve`, { method: 'POST' }, 30000);
        showToast('已用官方 API 刷新组织详情', 'success');
        await loadLinkedinProfiles();
    } catch (err) {
        if (err.message.includes('尚未完成') || err.message.includes('授权')) {
            showToast('请先在设置页完成 LinkedIn 授权: ' + err.message, 'warning');
        } else {
            showToast('刷新失败: ' + err.message, 'danger');
        }
    }
}

// ── 评分明细 ──
function renderScoreDetail(c) {
    const container = document.getElementById('scoreDetailContainer');
    if (c.industry_score === null || c.industry_score === undefined) {
        container.innerHTML = '<p class="text-muted small mb-0">尚未评分（分析完成后由规则引擎自动评分）</p>';
        return;
    }

    const items = [
        { label: '行业匹配度', score: c.industry_score, max: 30, color: 'bg-teal' },
        { label: '项目匹配度', score: c.project_score, max: 25, color: 'bg-cyan' },
        { label: '公司类型', score: c.company_type_score, max: 20, color: 'bg-emerald' },
        { label: '国家优先级', score: c.country_score, max: 15, color: 'bg-amber' },
        { label: '联系方式', score: c.contact_score, max: 10, color: 'bg-rose' },
    ];

    const g = _gradeLabel(c.total_score);
    const priorityColors = { A: 'bg-danger', B: 'bg-warning text-dark', C: 'bg-info text-dark', D: 'bg-secondary' };

    let html = '';
    items.forEach(item => {
        const pct = Math.min(100, (item.score / item.max) * 100);
        html += `
            <div class="score-detail-item">
                <div class="score-detail-label">${item.label}</div>
                <div class="score-detail-bar">
                    <div class="progress">
                        <div class="progress-bar ${item.color}" style="width:${pct.toFixed(0)}%"></div>
                    </div>
                </div>
                <div class="score-detail-value ${pct >= 60 ? 'text-success' : pct >= 30 ? 'text-warning' : 'text-muted'}">${item.score}</div>
                <div class="score-detail-max">/${item.max}</div>
            </div>`;
    });

    html += `
        <hr class="my-2">
        <div class="d-flex justify-content-between align-items-center pt-1">
            <strong>总分</strong>
            <span class="fs-4 fw-bold ${g.cls}">${c.total_score}</span>
        </div>
        <div class="d-flex justify-content-between align-items-center mt-1">
            <span class="text-muted small">优先级</span>
            <span class="badge rounded-pill ${priorityColors[c.priority] || 'bg-secondary'}">${c.priority || '-'}</span>
        </div>`;

    // V4.6：买家意向分 + 价格询盘加成（评分分级展示）
    if (c.buyer_intent_score !== null && c.buyer_intent_score !== undefined) {
        const s = c.buyer_intent_score;
        const intentCls = s >= 8 ? 'text-success' : s >= 5 ? 'text-info' : s >= 1 ? 'text-warning' : 'text-muted';
        const intentLabel = s >= 8 ? '高购买意向' : s >= 5 ? '中购买意向' : s >= 1 ? '低购买意向' : '非采购方';
        html += `<div class="d-flex justify-content-between align-items-center mt-1">
            <span class="text-muted small"><i class="bi bi-cart-check me-1"></i>AI 买家意向</span>
            <span class="fw-bold ${intentCls}">${intentLabel} ${s}/10</span>
        </div>`;
    }
    if (c.is_price_inquiry) {
        html += `<div class="d-flex justify-content-between align-items-center mt-1">
            <span class="text-muted small"><i class="bi bi-fire me-1"></i>价格询盘加成</span>
            <span class="fw-bold text-danger">+5</span>
        </div>`;
    }

    container.innerHTML = html;
}

// ── 分析 ──
async function analyzeCustomer() {
    if (!confirm('确定要分析该客户吗？')) return;
    showAnalyzingBanner();
    try {
        await _fetchWithTimeout(`/api/analyze/${customerId}`, { method: 'POST' });
        showToast('分析已完成', 'success');
        setTimeout(async () => {
            await loadDetail();
            hideAnalyzingBanner();
        }, 1000);
    } catch (err) {
        hideAnalyzingBanner();
        showToast('分析失败: ' + err.message, 'danger');
    }
}

async function stopAndBack() {
    try {
        await _fetchWithTimeout('/api/stop-analysis', { method: 'POST' });
    } catch (err) { /* ignore */ }
    window.location.href = '/';
}

// ── 保存跟进 ──
async function saveFollowUp() {
    const status = document.getElementById('followUpStatus').value;
    const date = document.getElementById('followUpDate').value;
    const notes = document.getElementById('followUpNotes').value;
    const star = document.getElementById('starRating').value;
    try {
        let url = `/api/customers/${customerId}/follow-up?status=${encodeURIComponent(status)}`;
        if (date) url += `&follow_up_date=${encodeURIComponent(date)}`;
        if (notes) url += `&notes=${encodeURIComponent(notes)}`;
        url += `&star_rating=${encodeURIComponent(star)}`;
        const data = await _fetchWithTimeout(url, { method: 'POST' });
        const saved = document.getElementById('followUpSaved');
        saved.classList.remove('d-none');
        showToast('跟进记录已保存', 'success');
        setTimeout(() => saved.classList.add('d-none'), 2000);
    } catch (err) {
        showToast('保存失败: ' + err.message, 'danger');
    }
}

// ── 重新抓取 ──
async function rescrapeCustomer() {
    if (!confirm('确定要重新抓取该客户的官网吗？')) return;
    try {
        await _fetchWithTimeout(`/api/customers/${customerId}/re-scrape`, { method: 'POST' });
        await loadDetail();
        showToast('重新抓取完成', 'success');
    } catch (err) {
        showToast('重新抓取失败: ' + err.message, 'danger');
    }
}

// ── 重新 AI 分析 ──
async function reanalyzeCustomer() {
    if (!confirm('确定要重新 AI 分析该客户吗？')) return;
    try {
        await _fetchWithTimeout(`/api/customers/${customerId}/re-analyze`, { method: 'POST' });
        await loadDetail();
        showToast('重新分析完成', 'success');
    } catch (err) {
        showToast('重新分析失败: ' + err.message, 'danger');
    }
}

// ── Hunter 配置状态 ──
let _lastHunterEmails = null;

async function checkHunterConfigDetail() {
    try {
        const status = await _fetchWithTimeout('/api/hunter/status');
        const badge = document.getElementById('hunterConfigBadgeDetail');
        if (status.configured) {
            if (status.test_mode) {
                badge.className = 'badge bg-warning text-dark';
                badge.textContent = '测试模式'; // intentionally not using emojis for consistency
            } else {
                badge.className = 'badge bg-success';
                badge.textContent = 'API 已配置';
            }
        } else {
            badge.className = 'badge bg-danger';
            badge.textContent = '未配置 API Key';
        }
    } catch (err) { /* ignore */ }
}

// ── Hunter 邮箱查找 ──
async function hunterLookup() {
    const websiteLink = document.getElementById('website');
    const website = websiteLink ? (websiteLink.textContent || '').trim() : '';
    if (!website || website === '-') {
        showToast('该客户没有官网地址，无法使用 Hunter 查找邮箱', 'warning');
        return;
    }

    const loadingEl = document.getElementById('hunterLoading');
    const resultEl = document.getElementById('hunterResult');
    const btn = document.getElementById('btnHunterLookup');
    loadingEl.classList.remove('d-none');
    resultEl.classList.add('d-none');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>查找中...';

    try {
        const firstName = document.getElementById('hunterFirstName').value.trim();
        const lastName = document.getElementById('hunterLastName').value.trim();
        const dept = document.getElementById('hunterDepartment').value;

        let url = `/api/hunter/find-emails?website=${encodeURIComponent(website)}`;
        if (firstName) url += `&first_name=${encodeURIComponent(firstName)}`;
        if (lastName) url += `&last_name=${encodeURIComponent(lastName)}`;
        if (dept) url += `&department=${encodeURIComponent(dept)}`;

        const data = await _fetchWithTimeout(url, {}, 20000);
        _lastHunterEmails = data;
        renderHunterResult(data, resultEl);
    } catch (err) {
        resultEl.classList.remove('d-none');
        resultEl.innerHTML = `<div class="alert alert-danger mb-0"><i class="bi bi-exclamation-triangle me-1"></i>查询失败: ${_esc(err.message)}</div>`;
    } finally {
        loadingEl.classList.add('d-none');
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-search me-1"></i>查找邮箱';
    }
}

function renderHunterResult(data, container) {
    container.classList.remove('d-none');

    if (data.total_available === 0) {
        container.innerHTML = `
            <div class="alert alert-info mb-0">
                <i class="bi bi-info-circle me-1"></i>
                Hunter 数据库中未找到该公司 (<strong>${_esc(data.domain)}</strong>) 的邮箱数据。
                <br><small class="text-muted">可能原因：公司规模较小、Hunter 尚未抓取到该公司的公开邮箱。</small>
            </div>`;
        return;
    }

    const emails = data.emails || [];
    if (emails.length === 0) {
        container.innerHTML = `
            <div class="alert alert-warning mb-0">
                <i class="bi bi-search me-1"></i>
                该公司共有 ${data.total_available} 个邮箱，但未匹配到符合条件的联系人。
                <br><small class="text-muted">尝试不填姓名或更换部门筛选条件。</small>
            </div>`;
        return;
    }

    const sourceLabels = {
        'cache': '本地缓存',
        'domain_search': 'Domain Search',
        'email_finder': 'Email Finder',
    };
    const source = sourceLabels[data.source] || data.source || '未知';
    const quotaInfo = data.quota_used || {};
    const searchUsed = (quotaInfo.domain_search || 0) + (quotaInfo.email_finder || 0);

    let html = `
        <div class="d-flex justify-content-between align-items-center mb-2 flex-wrap gap-1">
            <span class="text-muted small">来源: <strong>${source}</strong> · 共 <strong>${data.total_available}</strong> 个可用</span>
            <span class="text-muted small">本次消耗 ${searchUsed} 次搜索额度</span>
        </div>
        <div class="table-responsive">
            <table class="table table-sm table-hover mb-0 align-middle">
                <thead class="table-light">
                    <tr>
                        <th>邮箱</th>
                        <th>姓名</th>
                        <th>职位</th>
                        <th>置信度</th>
                        <th>验证</th>
                        <th class="text-center">操作</th>
                    </tr>
                </thead>
                <tbody>`;

    emails.forEach(e => {
        const email = e.value || e.email || '';
        const name = [e.first_name, e.last_name].filter(Boolean).join(' ') || '-';
        const position = e.position || '-';
        const confidence = e.confidence || 0;
        const verification = e.verification || {};
        const vStatus = verification.status || 'unknown';
        const vBadge = vStatus === 'valid' ? 'success' : vStatus === 'accept_all' ? 'warning text-dark' : 'secondary';
        const vLabel = vStatus === 'valid' ? '有效' : vStatus === 'accept_all' ? '待验证' : '未知';
        const confBadge = confidence >= 90 ? 'success' : confidence >= 70 ? 'warning text-dark' : 'secondary';

        html += `<tr>
            <td><code style="font-size:0.85rem">${_esc(email)}</code></td>
            <td>${_esc(name)}</td>
            <td><small>${_esc(position)}</small></td>
            <td><span class="badge bg-${confBadge}">${confidence}%</span></td>
            <td><span class="badge bg-${vBadge}" style="font-size:0.75rem">${vLabel}</span></td>
            <td class="text-center">
                <button class="btn btn-sm btn-outline-secondary me-1" onclick="_copyText('${_esc(email)}')" title="复制邮箱">
                    <i class="bi bi-clipboard"></i>
                </button>
                <button class="btn btn-sm btn-outline-success" onclick="saveSingleHunterEmail('${_esc(email)}')" title="保存该邮箱到客户">
                    <i class="bi bi-save"></i>
                </button>
            </td>
        </tr>`;
    });

    html += `</tbody></table></div>`;

    html += `
        <div class="d-flex justify-content-between align-items-center mt-3 pt-2 border-top">
            <span class="small text-muted">
                <i class="bi bi-info-circle me-1"></i>将 ${emails.length} 个邮箱保存到客户记录
            </span>
            <div class="d-flex gap-2">
                <button class="btn btn-sm btn-outline-success" onclick="saveHunterEmails(null)">
                    <i class="bi bi-save me-1"></i>仅保存邮箱
                </button>
                <button class="btn btn-sm btn-accent" onclick="saveHunterEmails('已发邮件')">
                    <i class="bi bi-envelope-paper me-1"></i>保存并标记已联系
                </button>
            </div>
        </div>`;

    container.innerHTML = html;

    const quickArea = document.getElementById('quickImportArea');
    if (quickArea) {
        quickArea.classList.remove('d-none');
    }
}

// ── 瀑布式多源邮箱查找 ──
let _lastWaterfallEmails = null;

async function waterfallLookup() {
    const websiteLink = document.getElementById('website');
    const website = websiteLink ? (websiteLink.textContent || '').trim() : '';
    if (!website || website === '-') {
        showToast('该客户没有官网地址', 'warning');
        return;
    }

    const loadingEl = document.getElementById('waterfallLoading');
    const resultEl = document.getElementById('waterfallResult');
    const statusEl = document.getElementById('waterfallStatus');
    const btn = document.getElementById('btnWaterfallLookup');
    const loadingText = document.getElementById('waterfallLoadingText');

    loadingEl.classList.remove('d-none');
    resultEl.classList.add('d-none');
    statusEl.innerHTML = '';
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>查找中...';

    loadingText.textContent = '第1级: 正在查询 Hunter...';
    setTimeout(() => { loadingText.textContent = '第2级: Hunter 结果不足，正在查询 Tomba...'; }, 3000);
    setTimeout(() => { loadingText.textContent = '第3级: 尝试从官网抓取...'; }, 6000);

    try {
        const data = await _fetchWithTimeout(`/api/waterfall/email-discovery?website=${encodeURIComponent(website)}`, {}, 30000);
        _lastWaterfallEmails = data;
        renderWaterfallResult(data, resultEl, statusEl);
    } catch (err) {
        resultEl.classList.remove('d-none');
        resultEl.innerHTML = `<div class="alert alert-danger mb-0"><i class="bi bi-exclamation-triangle me-1"></i>查询失败: ${_esc(err.message)}</div>`;
    } finally {
        loadingEl.classList.add('d-none');
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-search me-1"></i>瀑布式查找';
    }
}

function renderWaterfallResult(data, container, statusEl) {
    container.classList.remove('d-none');

    const sourceLabels = { hunter: 'Hunter', tomba: 'Tomba', scraped: '网页抓取' };
    const sourceInfo = document.getElementById('waterfallSourceInfo');
    if (data.sources_used && data.sources_used.length > 0) {
        sourceInfo.innerHTML = data.sources_used.map(s => sourceLabels[s] || s).join(' → ');
    }

    let statusHtml = '';
    if (data.waterfall_log) {
        data.waterfall_log.forEach(log => {
            const srcLabel = sourceLabels[log.source] || log.source;
            if (log.skipped) {
                statusHtml += `<span class="badge bg-secondary">${srcLabel} 跳过（已有足够结果）</span>`;
            } else if (log.found > 0) {
                statusHtml += `<span class="badge bg-success">${srcLabel} ${log.found} 个</span>`;
            } else {
                statusHtml += `<span class="badge bg-info text-dark">${srcLabel} 无结果</span>`;
            }
        });
    }
    statusEl.innerHTML = statusHtml;

    if (data.total === 0) {
        container.innerHTML = `
            <div class="alert alert-info mb-0">
                <i class="bi bi-info-circle me-1"></i>
                所有数据源均未找到 <strong>${_esc(data.domain)}</strong> 的邮箱。
                <br><small class="text-muted">建议：尝试手动在官网查找联系方式，或使用 Hunter 精确查找（按姓名）。</small>
            </div>`;
        return;
    }

    const emails = data.emails || [];
    let html = `
        <div class="d-flex justify-content-between align-items-center mb-2 flex-wrap gap-1">
            <span class="text-muted small">共找到 <strong>${data.total}</strong> 个邮箱 · 综合排序</span>
        </div>
        <div class="table-responsive">
            <table class="table table-sm table-hover mb-0 align-middle">
                <thead class="table-light">
                    <tr>
                        <th>邮箱</th>
                        <th>姓名</th>
                        <th>职位</th>
                        <th>部门</th>
                        <th>来源</th>
                        <th>评分</th>
                        <th>领英</th>
                        <th class="text-center">操作</th>
                    </tr>
                </thead>
                <tbody>`;

    emails.forEach(e => {
        const email = e.email || '';
        const name = e.full_name || [e.first_name, e.last_name].filter(Boolean).join(' ') || '-';
        const position = e.position || '-';
        const dept = e.department || '-';
        const source = e.source || '?';
        const score = e.score || 0;

        const sourceBadge = source === 'tomba' ? 'primary' : source === 'hunter' ? 'success' : 'warning text-dark';
        const sourceLabel = sourceLabels[source] || source;
        const linkedin = e.linkedin || '';

        const verification = e.verification || '';
        const vIcon = verification === 'valid' ? '' : verification === 'unknown' ? '' : '';

        html += `<tr>
            <td><code style="font-size:0.85rem">${_esc(email)}</code></td>
            <td>${_esc(name)}</td>
            <td><small>${_esc(position)}</small></td>
            <td><small>${_esc(dept)}</small></td>
            <td><span class="badge bg-${sourceBadge}" style="font-size:0.7rem">${sourceLabel}</span></td>
            <td><span class="badge bg-${score >= 70 ? 'success' : score >= 40 ? 'warning text-dark' : 'secondary'}">${score}</span></td>
            <td>${linkedin ? `<a href="${_esc(linkedin)}" target="_blank" class="text-decoration-none text-primary"><i class="bi bi-linkedin"></i></a>` : '<span class="text-muted">—</span>'}</td>
            <td class="text-center">
                <button class="btn btn-sm btn-outline-secondary me-1" onclick="_copyText('${_esc(email)}')" title="复制邮箱">
                    <i class="bi bi-clipboard"></i>
                </button>
                <button class="btn btn-sm btn-outline-success" onclick="saveSingleEmail('${_esc(email)}', '${source}')" title="保存该邮箱到客户">
                    <i class="bi bi-save"></i>
                </button>
            </td>
        </tr>`;
    });

    html += `</tbody></table></div>`;

    html += `
        <div class="d-flex justify-content-between align-items-center mt-3 pt-2 border-top">
            <span class="small text-muted">
                <i class="bi bi-info-circle me-1"></i>将 ${emails.length} 个邮箱保存到客户记录
            </span>
            <div class="d-flex gap-2">
                <button class="btn btn-sm btn-outline-success" onclick="saveWaterfallEmails(null)">
                    <i class="bi bi-save me-1"></i>仅保存邮箱
                </button>
                <button class="btn btn-sm btn-accent" onclick="saveWaterfallEmails('已发邮件')">
                    <i class="bi bi-envelope-paper me-1"></i>保存并标记已联系
                </button>
            </div>
        </div>`;

    container.innerHTML = html;
}

// ── 保存瀑布式查到的邮箱 ──
async function saveWaterfallEmails(setStatus) {
    if (!_lastWaterfallEmails || !_lastWaterfallEmails.emails || _lastWaterfallEmails.emails.length === 0) {
        showToast('没有可保存的邮箱，请先执行查找', 'warning');
        return;
    }

    // V5.1：按来源分组保存，保留来源信息
    const groups = {};
    _lastWaterfallEmails.emails.forEach(e => {
        if (!e.email) return;
        const src = e.source || 'scraped';
        if (!groups[src]) groups[src] = [];
        groups[src].push(e.email);
    });
    const entries = Object.entries(groups);
    if (entries.length === 0) {
        showToast('没有有效的邮箱地址可保存', 'warning');
        return;
    }

    let totalAdded = 0;
    try {
        for (const [src, emails] of entries) {
            let url = `/api/customers/${customerId}/add-emails?emails=${encodeURIComponent(JSON.stringify(emails))}&source=${encodeURIComponent(src)}`;
            if (setStatus) url += `&set_status=${encodeURIComponent(setStatus)}`;
            const result = await _fetchWithTimeout(url, { method: 'POST' });
            totalAdded += result.added;
        }
        showToast(`已保存 ${totalAdded} 个邮箱`, 'success');
        if (setStatus) {
            document.getElementById('followUpStatus').value = setStatus;
        }
        await loadCustomerEmails();
    } catch (err) {
        showToast('保存失败: ' + err.message, 'danger');
    }
}

async function saveSingleEmail(email, source) {
    try {
        let url = `/api/customers/${customerId}/add-emails?emails=${encodeURIComponent(JSON.stringify([email]))}`;
        if (source) url += `&source=${encodeURIComponent(source)}`;
        const result = await _fetchWithTimeout(url, { method: 'POST' });
        showToast(`已保存 1 个邮箱`, 'success');
        await loadCustomerEmails();
    } catch (err) {
        showToast('保存失败: ' + err.message, 'danger');
    }
}

// ── 保存 Hunter 邮箱到客户 ──
async function saveHunterEmails(setStatus) {
    if (!_lastHunterEmails || !_lastHunterEmails.emails || _lastHunterEmails.emails.length === 0) {
        showToast('没有可保存的邮箱，请先执行查找', 'warning');
        return;
    }

    const emails = _lastHunterEmails.emails.map(e => e.value || e.email || '').filter(Boolean);
    if (emails.length === 0) {
        showToast('没有有效的邮箱地址可保存', 'warning');
        return;
    }

    try {
        let url = `/api/customers/${customerId}/add-emails?emails=${encodeURIComponent(JSON.stringify(emails))}&source=hunter`;
        if (setStatus) url += `&set_status=${encodeURIComponent(setStatus)}`;

        const result = await _fetchWithTimeout(url, { method: 'POST' });

        const quickArea = document.getElementById('quickImportArea');
        if (quickArea) quickArea.classList.add('d-none');

        showToast('✅ ' + result.message, 'success');

        const c = await _fetchWithTimeout(`/api/customers/${customerId}`);
        renderDetail(c);
        await loadCustomerEmails();

        const followUpTab = document.getElementById('followUpTab');
        if (followUpTab) followUpTab.click();
    } catch (err) {
        showToast('保存失败: ' + err.message, 'danger');
    }
}

async function saveSingleHunterEmail(email) {
    try {
        const url = `/api/customers/${customerId}/add-emails?emails=${encodeURIComponent(JSON.stringify([email]))}&source=hunter`;
        const result = await _fetchWithTimeout(url, { method: 'POST' });
        showToast('✅ ' + result.message, 'success');
        const c = await _fetchWithTimeout(`/api/customers/${customerId}`);
        renderDetail(c);
        await loadCustomerEmails();
    } catch (err) {
        showToast('保存失败: ' + err.message, 'danger');
    }
}

// ── 一键查找+保存+标记已联系 ──
async function hunterLookupAndSetStatus(setStatus) {
    const websiteLink = document.getElementById('website');
    const website = websiteLink ? (websiteLink.textContent || '').trim() : '';
    if (!website || website === '-') {
        showToast('该客户没有官网地址', 'warning');
        return;
    }

    const btn = document.getElementById('btnFindAndEmail');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>查找并保存...';

    try {
        const url = `/api/hunter/find-emails?website=${encodeURIComponent(website)}`;
        const data = await _fetchWithTimeout(url, {}, 20000);
        _lastHunterEmails = data;

        const emails = (data.emails || []).map(e => e.value || e.email || '').filter(Boolean);

        if (emails.length === 0) {
            if (data.total_available > 0) {
                showToast(`该公司有 ${data.total_available} 个邮箱，但无匹配结果，请到精确查找中调整条件`, 'warning');
            } else {
                showToast(`Hunter 未找到该公司的邮箱`, 'info');
            }
            const resultEl = document.getElementById('hunterResult');
            renderHunterResult(data, resultEl);
            document.getElementById('hunterTab').click();
            return;
        }

        const saveUrl = `/api/customers/${customerId}/add-emails?emails=${encodeURIComponent(JSON.stringify(emails))}&source=hunter&set_status=${encodeURIComponent(setStatus)}`;
        const saveResult = await _fetchWithTimeout(saveUrl, { method: 'POST' });

        showToast(`已保存 ${emails.length} 个邮箱并标记为「${setStatus}」`, 'success');

        const c = await _fetchWithTimeout(`/api/customers/${customerId}`);
        renderDetail(c);
        await loadCustomerEmails();

        const followUpTab = document.getElementById('followUpTab');
        if (followUpTab) followUpTab.click();
    } catch (err) {
        showToast('操作失败: ' + err.message, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-envelope-paper me-1"></i>查找邮箱 + 标记已联系';
    }
}

// ── AI 开发信生成（V4.6） ──
let _lastEmailDraft = null;

async function generateEmailDraft() {
    const lang = document.getElementById('emailDraftLanguage').value;
    const keywords = document.getElementById('emailDraftKeywords').value.trim();
    const btn = document.getElementById('btnEmailDraft');
    const loading = document.getElementById('emailDraftLoading');
    const result = document.getElementById('emailDraftResult');

    btn.disabled = true;
    loading.classList.remove('d-none');
    result.classList.add('d-none');

    let url = `/api/customers/${customerId}/email-draft`;
    const params = new URLSearchParams();
    if (keywords) params.set('product_keywords', keywords);
    if (lang && lang !== 'auto') params.set('language', lang);
    const qs = params.toString();
    if (qs) url += '?' + qs;

    try {
        const data = await _fetchWithTimeout(url, { method: 'POST' }, 60000);
        _lastEmailDraft = data;
        renderEmailDraft(data);
        showToast('开发信已生成', 'success');
    } catch (err) {
        result.classList.remove('d-none');
        result.innerHTML = `<div class="alert alert-danger mb-0"><i class="bi bi-exclamation-triangle me-1"></i>生成失败: ${_esc(err.message)}</div>`;
    } finally {
        btn.disabled = false;
        loading.classList.add('d-none');
    }
}

function renderEmailDraft(d) {
    const result = document.getElementById('emailDraftResult');
    const langInfo = document.getElementById('emailDraftLangInfo');
    if (langInfo && d.language) {
        langInfo.textContent = `语言: ${_esc(d.language)}`;
    }
    result.classList.remove('d-none');
    result.innerHTML = `
        <div class="mb-2">
            <label class="form-label small">主题 (Subject)</label>
            <div class="input-group input-group-sm">
                <input type="text" class="form-control" id="emailDraftSubject" value="${_esc(d.subject || '')}" readonly>
                <button class="btn btn-outline-secondary" onclick="_copyText(this.parentElement.querySelector('input').value)" title="复制主题">
                    <i class="bi bi-clipboard"></i>
                </button>
            </div>
        </div>
        <label class="form-label small">正文 (Body)</label>
        <textarea class="form-control form-control-sm" id="emailDraftBody" rows="10" readonly>${_esc(d.body || '')}</textarea>
        <div class="mt-2 d-flex align-items-center gap-2 flex-wrap">
            <button class="btn btn-sm btn-outline-success" onclick="_copyText(document.getElementById('emailDraftBody').value)">
                <i class="bi bi-clipboard-check me-1"></i>复制正文
            </button>
            <button class="btn btn-sm btn-outline-primary" onclick="openMailClient()">
                <i class="bi bi-envelope-at me-1"></i>打开邮件客户端
            </button>
        </div>`;
}

function openMailClient() {
    const subjectEl = document.getElementById('emailDraftSubject');
    const bodyEl = document.getElementById('emailDraftBody');
    const subject = subjectEl ? subjectEl.value : '';
    const body = bodyEl ? bodyEl.value : '';
    let to = '';
    const primary = _customerEmailRecords.find(r => r.is_primary);
    if (primary) {
        to = primary.email;
    } else if (_customerEmailRecords.length > 0) {
        to = _customerEmailRecords[0].email;
    }
    window.location.href = 'mailto:' + encodeURIComponent(to)
        + '?subject=' + encodeURIComponent(subject)
        + '&body=' + encodeURIComponent(body);
}

// ── 初始化 ──
document.addEventListener('DOMContentLoaded', () => {
    loadDetail();
    checkHunterConfigDetail();
    loadLinkedinProfiles();
});
