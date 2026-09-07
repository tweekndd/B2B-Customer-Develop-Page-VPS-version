/* ============================================
   Prompt 管理页（Phase1 外联模板）
   ============================================ */
'use strict';

let _currentTemplateId = null;
let _varDocs = [];

async function loadTemplates() {
    const status = document.getElementById('templateStatusFilter')?.value || '';
    const url = status ? `/api/prompts?status=${encodeURIComponent(status)}` : '/api/prompts';
    const box = document.getElementById('templateList');
    try {
        const r = await _fetchWithTimeout(url);
        const templates = r.templates || [];
        if (!templates.length) {
            box.innerHTML = `<div class="text-center text-secondary py-5">
                <i class="bi bi-file-earmark-plus fs-1 d-block mb-2"></i>暂无模板，点击右上角「新建模板」</div>`;
            return;
        }
        box.innerHTML = templates.map(t => {
            const st = t.status === 'active' ? 'bg-success' : t.status === 'draft' ? 'bg-warning text-dark' : 'bg-secondary';
            return `<div class="list-group-item list-group-item-action d-flex justify-content-between align-items-center gap-2 py-2 ${t.id === _currentTemplateId ? 'active' : ''}" style="cursor:pointer;" onclick="openTemplate(${t.id})">
                <div style="min-width:0;">
                    <div class="d-flex align-items-center gap-2">
                        <span class="fw-bold text-truncate" style="max-width:180px;">${_esc(t.name)}</span>
                        <span class="badge ${st}">${_esc(t.status)}</span>
                        <span class="badge bg-light text-dark">${_esc(t.freedom_level)}</span>
                    </div>
                    <div class="small text-secondary text-truncate mt-1">
                        v${t.current_version} · ${_esc(t.purpose)} · ${_esc(t.language)}${t.product_name ? ' · ' + _esc(t.product_name) : ''}
                    </div>
                </div>
                <i class="bi bi-chevron-right"></i>
            </div>`;
        }).join('');
    } catch (e) { showToast('加载模板失败：' + e.message, 'danger'); }
}

async function openTemplate(id) {
    try {
        const r = await _fetchWithTimeout(`/api/prompts/${id}`);
        const t = r.template;
        _currentTemplateId = t.id;
        loadTemplates();
        // 填充表单
        document.getElementById('editorTitle').innerHTML = `<i class="bi bi-pencil-square me-2 text-accent"></i>${_esc(t.name)} <span class="text-secondary small fw-normal">#${t.id} · v${t.current_version}</span>`;
        document.getElementById('fName').value = t.name;
        document.getElementById('fPurpose').value = t.purpose;
        document.getElementById('fLanguage').value = t.language;
        document.getElementById('fFreedom').value = t.freedom_level;
        document.getElementById('fProduct').value = t.product_name || '';
        document.getElementById('fSegment').value = t.customer_segment || '';
        document.getElementById('fSystem').value = t.system_prompt || '';
        document.getElementById('fUser').value = t.user_prompt_template || '';
        document.getElementById('fSchema').value = t.variables_schema ? JSON.stringify(t.variables_schema, null, 2) : '';
        document.getElementById('fModelCfg').value = t.model_config ? JSON.stringify(t.model_config) : '{}';
        const actions = document.getElementById('editorActions');
        actions.classList.remove('d-none');
        actions.classList.add('d-flex');
        document.getElementById('btnDeleteTemplate').classList.toggle('d-none', t.status === 'active' || !!r.versions.length);
        document.getElementById('editorStatus').textContent = t.status === 'active' ? '该模板已发布，改动需另存为新草稿后发布。' : '';
        // 只读约束：active 模板禁止直接编辑
        const readOnly = t.status === 'active';
        ['fName','fPurpose','fLanguage','fFreedom','fProduct','fSegment','fSystem','fUser','fSchema','fModelCfg']
            .forEach(idName => { document.getElementById(idName).readOnly = readOnly; });
        renderVersions(r.versions || []);
    } catch (e) { showToast('加载模板失败：' + e.message, 'danger'); }
}

function openEditor() {
    _currentTemplateId = null;
    document.getElementById('editorTitle').innerHTML = '<i class="bi bi-plus-circle me-2 text-accent"></i>新建模板';
    ['fName','fPurpose','fLanguage','fFreedom','fProduct','fSegment','fSystem','fUser','fSchema','fModelCfg']
        .forEach(idName => { document.getElementById(idName).readOnly = false; });
    document.getElementById('fName').value = '';
    document.getElementById('fPurpose').value = 'first_contact';
    document.getElementById('fLanguage').value = 'auto';
    document.getElementById('fFreedom').value = 'L1';
    document.getElementById('fProduct').value = '';
    document.getElementById('fSegment').value = '';
    document.getElementById('fSystem').value = '';
    document.getElementById('fUser').value = '';
    document.getElementById('fSchema').value = '';
    document.getElementById('fModelCfg').value = '{"temperature": 0.7, "max_tokens": 2048}';
    document.getElementById('editorActions').classList.add('d-none');
    document.getElementById('btnDeleteTemplate').classList.add('d-none');
    document.getElementById('editorStatus').textContent = '';
    document.getElementById('versionHistory').classList.add('d-none');
    document.getElementById('previewBox').classList.add('d-none');
    loadTemplates();
}

function collectPayload() {
    let schema = document.getElementById('fSchema').value.trim();
    let schemaParsed = null;
    if (schema) {
        try { schemaParsed = JSON.parse(schema); }
        catch (e) { throw new Error('variables_schema 不是合法 JSON：' + e.message); }
    }
    let modelCfg = document.getElementById('fModelCfg').value.trim();
    let modelParsed = null;
    if (modelCfg) {
        try { modelParsed = JSON.parse(modelCfg); }
        catch (e) { throw new Error('model_config 不是合法 JSON：' + e.message); }
    }
    return {
        name: document.getElementById('fName').value.trim(),
        purpose: document.getElementById('fPurpose').value,
        language: document.getElementById('fLanguage').value.trim() || 'auto',
        freedom_level: document.getElementById('fFreedom').value,
        product_name: document.getElementById('fProduct').value.trim() || null,
        customer_segment: document.getElementById('fSegment').value.trim() || null,
        system_prompt: document.getElementById('fSystem').value,
        user_prompt_template: document.getElementById('fUser').value,
        variables_schema: schemaParsed,
        model_config: modelParsed,
    };
}

async function saveTemplate(publish) {
    try {
        const payload = collectPayload();
        if (!payload.name) { showToast('请填写模板名称', 'warning'); return; }
        if (_currentTemplateId) {
            // active 模板不能直接 PUT（服务端会拒绝），提示先复制
            await _fetchWithTimeout(`/api/prompts/${_currentTemplateId}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
            });
        } else {
            const r = await _fetchWithTimeout('/api/prompts', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
            });
            _currentTemplateId = r.template.id;
        }
        if (publish && _currentTemplateId) {
            await _fetchWithTimeout(`/api/prompts/${_currentTemplateId}/publish`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ change_summary: payload.name }),
            });
        }
        showToast(publish ? '已保存并发布为 active 版本' : '已保存', 'success');
        loadTemplates();
        if (_currentTemplateId) openTemplate(_currentTemplateId);
    } catch (e) {
        showToast('保存失败：' + e.message, 'danger');
    }
}

async function publishTemplate() {
    try {
        const payload = collectPayload();
        // 若为新建（未保存）先保存
        if (!_currentTemplateId) {
            const r = await _fetchWithTimeout('/api/prompts', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
            });
            _currentTemplateId = r.template.id;
        }
        const msg = document.getElementById('fName').value.trim();
        await _fetchWithTimeout(`/api/prompts/${_currentTemplateId}/publish`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ change_summary: `发布：${msg}` }),
        });
        showToast('已发布为 active 版本', 'success');
        loadTemplates();
        openTemplate(_currentTemplateId);
    } catch (e) { showToast('发布失败：' + e.message, 'danger'); }
}

async function previewTemplate() {
    try {
        const payload = collectPayload();
        // 示例变量求值：占位展示（白名单校验）
        const vars = {};
        const schema = payload.variables_schema || {};
        Object.keys(schema).forEach(v => { vars[v] = `示例:${v.split('.').pop()}`; });
        vars['system.sender_company'] = '我方公司名';
        vars['system.now_utc'] = new Date().toISOString().slice(0, 10);
        const r = await _fetchWithTimeout('/api/prompts/preview', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                system_prompt: payload.system_prompt,
                user_prompt_template: payload.user_prompt_template,
                variables_schema: payload.variables_schema,
                variables: vars,
            }),
        });
        document.getElementById('previewContent').textContent =
            '【系统 Prompt】\n' + (r.system_prompt || '') + '\n\n【用户提示词（渲染后）】\n' + r.rendered_user_prompt;
        document.getElementById('previewBox').classList.remove('d-none');
        if (r.undeclared_variables && r.undeclared_variables.length) {
            document.getElementById('schemaStatus').textContent = '⚠ 未声明变量：' + r.undeclared_variables.join(', ');
            document.getElementById('schemaStatus').style.color = 'var(--danger)';
        } else {
            document.getElementById('schemaStatus').textContent = '✓ 变量全部在白名单内';
            document.getElementById('schemaStatus').style.color = 'var(--success)';
        }
    } catch (e) { showToast('预览失败：' + e.message, 'danger'); }
}

function renderVersions(versions) {
    _currentVersions = versions || [];
    const box = document.getElementById('versionList');
    document.getElementById('versionHistory').classList.remove('d-none');
    if (!_currentVersions.length) {
        box.innerHTML = '<div class="text-center text-secondary py-3 small">暂无版本历史，发布后自动生成不可变快照</div>';
        return;
    }
    box.innerHTML = _currentVersions.map(v => `<div class="list-group-item d-flex justify-content-between align-items-center py-2">
        <div class="small">
            <span class="badge bg-primary me-1">v${v.version}</span>
            <span class="text-secondary">${_esc(v.change_summary || '')}</span>
            <span class="text-muted">${_esc(_fmtDateTime(v.created_at))}${v.freedom_level ? ' · ' + v.freedom_level : ''}</span>
        </div>
        <div class="d-flex gap-1">
            <button class="btn btn-sm btn-outline-secondary" onclick="viewVersion(${v.id})">查看</button>
            <button class="btn btn-sm btn-outline-primary" onclick="forkVersion(${v.id})">复制为草稿</button>
        </div>
    </div>`).join('');
}

let _currentVersions = [];

async function viewVersion(id) {
    const v = _currentVersions.find(x => x.id === id);
    if (!v) { showToast('版本信息未加载，请刷新后重试', 'warning'); return; }
    const vars = v.variables_schema || {};
    const exampleVars = {};
    Object.keys(vars).forEach(k => { exampleVars[k] = '示例值'; });
    const r = await _fetchWithTimeout('/api/prompts/preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            system_prompt: v.system_prompt,
            user_prompt_template: v.user_prompt_template,
            variables_schema: v.variables_schema,
            variables: exampleVars,
        }),
    });
    document.getElementById('editorTitle').innerHTML =
        `<i class="bi bi-clock-history me-2 text-accent"></i>版本 v${v.version} 快照（只读，可复制为草稿）`;
    document.getElementById('previewContent').textContent =
        '【系统 Prompt】\n' + (v.system_prompt || '') + '\n\n【用户模板（示例渲染）】\n' + (r.rendered_user_prompt || v.user_prompt_template);
    document.getElementById('previewBox').classList.remove('d-none');
}

async function forkVersion(versionId) {
    if (!_currentTemplateId) return;
    const newName = prompt('新模板名称（基于该版本复制）：', (document.getElementById('fName').value || '模板') + '-副本');
    if (!newName) return;
    try {
        const r = await _fetchWithTimeout(`/api/prompts/${_currentTemplateId}/fork/${versionId}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_name: newName }),
        });
        showToast('已复制为新草稿模板', 'success');
        loadTemplates();
        openTemplate(r.template.id);
    } catch (e) { showToast('复制失败：' + e.message, 'danger'); }
}

async function deleteTemplate() {
    if (!_currentTemplateId) return;
    if (!confirm('确认删除该草稿模板？（已发布/有历史的模板需归档而非删除）')) return;
    try {
        await _fetchWithTimeout(`/api/prompts/${_currentTemplateId}`, { method: 'DELETE' });
        showToast('已删除', 'success');
        _currentTemplateId = null;
        loadTemplates();
        openEditor();
    } catch (e) { showToast('删除失败：' + e.message, 'danger'); }
}

async function showVarHelp() {
    const body = document.getElementById('varHelpBody');
    try {
        const r = await _fetchWithTimeout('/api/prompts/meta/variables');
        _varDocs = r.variables || [];
        body.innerHTML = _varDocs.map(v => `<tr style="cursor:pointer;" onclick="insertVar('{{${v.var}}}')" title="点击插入">
            <td><code>{{${v.var}}}</code></td>
            <td class="small text-secondary">${_esc(v.desc)}</td>
        </tr>`).join('');
        bootstrap.Modal.getOrCreateInstance(document.getElementById('varHelpModal')).show();
    } catch (e) { showToast(e.message, 'danger'); }
}

function insertVar(varStr) {
    const ta = document.getElementById('fUser');
    if (varStr === 'system') { /* noop */ }
    const start = ta.selectionStart || ta.value.length;
    const end = ta.selectionEnd || ta.value.length;
    ta.value = ta.value.slice(0, start) + varStr + ta.value.slice(end);
    ta.focus();
    ta.setSelectionRange(start + varStr.length, start + varStr.length);
}

function _fmtDateTime(s) {
    if (!s) return '';
    return new Date(s).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

document.addEventListener('DOMContentLoaded', () => {
    loadTemplates();
    openEditor();
});
