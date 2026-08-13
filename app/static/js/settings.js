/* ============================================
   AI Trade Customer Analyzer V4.6 - AI 与 API 设置页
   ============================================ */
(function() {
'use strict';

// ── Provider 默认值 ──
const PROVIDER_DEFAULTS = {
    glm: {
        base_url: 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
        model: 'glm-4.7-flash',
        fallbacks: ['glm-4.6v-flash', 'glm-4-flash-250414'],
    },
    deepseek: {
        base_url: 'https://api.deepseek.com/v1/chat/completions',
        model: 'deepseek-chat',
        fallbacks: ['deepseek-reasoner'],
    },
    qwen: {
        base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        model: 'qwen-plus',
        fallbacks: ['qwen-turbo'],
    },
    moonshot: {
        base_url: 'https://api.moonshot.cn/v1',
        model: 'moonshot-v1-8k',
        fallbacks: ['moonshot-v1-32k'],
    },
    openai: {
        base_url: 'https://api.openai.com/v1/chat/completions',
        model: 'gpt-4o-mini',
        fallbacks: ['gpt-4o'],
    },
    custom: { base_url: '', model: '', fallbacks: [] },
};

// ── 邮箱服务字段定义 ──
const EMAIL_SERVICES = [
    {
        service: 'hunter',
        name: 'Hunter.io',
        desc: '域名邮箱搜索',
        fields: [
            { id: 'hunterApiKey', label: 'API Key', type: 'password', key: 'api_key' },
        ],
    },
    {
        service: 'tomba',
        name: 'Tomba.io',
        desc: '更丰富的联系人数据',
        fields: [
            { id: 'tombaApiKey', label: 'API Key', type: 'password', key: 'api_key' },
            { id: 'tombaSecret', label: 'API Secret', type: 'password', key: 'api_secret' },
        ],
    },
    {
        service: 'prospeo',
        name: 'Prospeo.io',
        desc: '搜索 + 数据补全',
        fields: [
            { id: 'prospeoApiKey', label: 'API Key', type: 'password', key: 'api_key' },
        ],
    },
];

// ── 搜索服务字段定义 ──
const SEARCH_SERVICES = [
    {
        service: 'tavily',
        name: 'Tavily',
        desc: '1000次/月免费',
        fields: [
            { id: 'tavilyApiKey', label: 'API Key', type: 'password', key: 'api_key' },
        ],
    },
    {
        service: 'serpapi',
        name: 'SerpAPI',
        desc: '250次/月免费',
        fields: [
            { id: 'serpapiApiKey', label: 'API Key', type: 'password', key: 'api_key' },
        ],
    },
    {
        service: 'searxng',
        name: 'SearXNG',
        desc: '自部署搜索引擎',
        fields: [
            { id: 'searxngUrl', label: 'Base URL', type: 'text', key: 'base_url' },
        ],
    },
];

// ═══════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════

function statusBadge(source, configured) {
    if (!configured) {
        return '<span class="badge rounded-pill bg-secondary">未配置</span>';
    }
    return source === 'user'
        ? '<span class="badge rounded-pill bg-success">已配置（用户）</span>'
        : '<span class="badge rounded-pill bg-info text-dark">服务器默认</span>';
}

function toggleKey(inputId, btn) {
    const input = document.getElementById(inputId);
    if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = '<i class="bi bi-eye-slash"></i>';
    } else {
        input.type = 'password';
        btn.innerHTML = '<i class="bi bi-eye"></i>';
    }
}

function parseModels(value) {
    return value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
}

async function saveService(service, payload) {
    return _fetchWithTimeout(`/api/user-config/${service}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }, 15000);
}

// ═══════════════════════════════════════════
// LLM 配置
// ═══════════════════════════════════════════

function onProviderChange() {
    const provider = document.getElementById('llmProvider').value;
    const def = PROVIDER_DEFAULTS[provider] || PROVIDER_DEFAULTS.custom;
    const baseUrlEl = document.getElementById('llmBaseUrl');
    const modelEl = document.getElementById('llmDefaultModel');
    const fallbackEl = document.getElementById('llmFallbackModels');
    if (!baseUrlEl.value) baseUrlEl.value = def.base_url;
    if (!modelEl.value) modelEl.value = def.model;
    if (!fallbackEl.value) fallbackEl.value = def.fallbacks.join(', ');

    // 更新模型建议
    const dl = document.getElementById('modelSuggest');
    dl.innerHTML = '';
    (def.fallbacks.concat([def.model])).forEach(m => {
        if (!m) return;
        const opt = document.createElement('option');
        opt.value = m;
        dl.appendChild(opt);
    });
}

function renderLlmConfig(config, effective) {
    if (config && config.provider) {
        const sel = document.getElementById('llmProvider');
        if ([...sel.options].some(o => o.value === config.provider)) {
            sel.value = config.provider;
        }
    }
    onProviderChange();
    if (config) {
        if (config.base_url) document.getElementById('llmBaseUrl').value = config.base_url;
        if (config.default_model) document.getElementById('llmDefaultModel').value = config.default_model;
        if (config.fallback_models && config.fallback_models.length) {
            document.getElementById('llmFallbackModels').value = config.fallback_models.join(', ');
        }
        if (config.api_key_set) {
            document.getElementById('llmApiKey').placeholder = config.api_key + '（点击输入框替换）';
        }
    }
    const eff = effective || {};
    document.getElementById('llmStatusBadge').innerHTML = statusBadge(eff.source, eff.configured);
}

async function saveLlmConfig() {
    const payload = {
        provider: document.getElementById('llmProvider').value,
        api_key: document.getElementById('llmApiKey').value.trim() || null,
        base_url: document.getElementById('llmBaseUrl').value.trim() || null,
        default_model: document.getElementById('llmDefaultModel').value.trim() || null,
        fallback_models: parseModels(document.getElementById('llmFallbackModels').value),
    };
    const btn = document.getElementById('btnSaveLlm');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>保存中...';
    try {
        const res = await saveService('llm', payload);
        showToast(res.message || 'LLM 配置已保存', 'success');
        document.getElementById('llmApiKey').value = '';
        document.getElementById('llmApiKey').placeholder = '';
        await loadAll();
    } catch (err) {
        showToast('保存失败: ' + err.message, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-floppy me-1"></i>保存配置';
    }
}

async function testLlmConnection() {
    const payload = {
        provider: document.getElementById('llmProvider').value,
        api_key: document.getElementById('llmApiKey').value.trim() || null,
        base_url: document.getElementById('llmBaseUrl').value.trim() || null,
        model: document.getElementById('llmDefaultModel').value.trim() || null,
        fallback_models: parseModels(document.getElementById('llmFallbackModels').value),
    };
    const btn = document.getElementById('btnTestLlm');
    const resultEl = document.getElementById('llmTestResult');
    btn.disabled = true;
    resultEl.innerHTML = '<span class="text-muted"><span class="spinner-border spinner-border-sm me-1"></span>正在测试连接...</span>';
    try {
        const res = await _fetchWithTimeout('/api/user-config/llm/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        }, 60000);
        if (res.success) {
            resultEl.innerHTML = `<span class="text-success"><i class="bi bi-check-circle me-1"></i>${_esc(res.message || '连接成功')}</span>`;
            showToast('LLM 连接测试成功', 'success');
        } else {
            resultEl.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle me-1"></i>${_esc(res.message || '连接失败')}</span>`;
            showToast('LLM 连接测试失败', 'danger');
        }
    } catch (err) {
        resultEl.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle me-1"></i>${_esc(err.message)}</span>`;
    } finally {
        btn.disabled = false;
    }
}

// ═══════════════════════════════════════════
// 通用服务行渲染
// ═══════════════════════════════════════════

function buildServiceRow(def, savedConfig, effective) {
    const eff = effective || {};
    const isUser = eff.source === 'user';
    const id = `row_${def.service}`;
    const fieldsHtml = def.fields.map(f => {
        const placeholder = savedConfig && savedConfig[f.key + '_set']
            ? (f.key === 'api_key' ? savedConfig.api_key : '已设置（留空保留）')
            : `留空则使用服务器环境变量`;
        return `
            <div class="col-md-${12 / Math.min(def.fields.length, 4)}">
                <label class="form-label small mb-1">${f.label}</label>
                <div class="input-group">
                    <input type="${f.type}" class="form-control form-control-sm" id="${f.id}" placeholder="${placeholder}" autocomplete="off">
                    ${f.type === 'password' ? `<button class="btn btn-outline-secondary btn-sm" type="button" onclick="toggleKey('${f.id}', this)"><i class="bi bi-eye"></i></button>` : ''}
                </div>
            </div>`;
    }).join('');

    return `
        <div class="border rounded p-3 mb-3" id="${id}">
            <div class="d-flex align-items-center mb-2">
                <strong>${def.name}</strong>
                <span class="text-muted small ms-2">${def.desc}</span>
                <span class="ms-auto">${statusBadge(eff.source, eff.configured)}</span>
            </div>
            <div class="row g-2 align-items-end">
                ${fieldsHtml}
                <div class="col-md-2 d-flex gap-1 justify-content-end">
                    <button class="btn btn-accent btn-sm" onclick="saveGenericService('${def.service}', this)">
                        <i class="bi bi-floppy"></i> 保存
                    </button>
                    ${isUser ? `<button class="btn btn-outline-danger btn-sm" onclick="deleteService('${def.service}')">
                        <i class="bi bi-trash"></i>
                    </button>` : ''}
                </div>
            </div>
        </div>`;
}

function renderEmailServices(savedMap, effectiveMap) {
    const container = document.getElementById('emailServiceRows');
    container.innerHTML = EMAIL_SERVICES.map(def => {
        const saved = savedMap[def.service] || null;
        const eff = (effectiveMap && effectiveMap[def.service]) || {};
        return buildServiceRow(def, saved, eff);
    }).join('');
}

function renderSearchServices(savedMap, effectiveMap) {
    const container = document.getElementById('searchServiceRows');
    container.innerHTML = SEARCH_SERVICES.map(def => {
        const saved = savedMap[def.service] || null;
        const eff = (effectiveMap && effectiveMap[def.service]) || {};
        return buildServiceRow(def, saved, eff);
    }).join('');
}

async function saveGenericService(service, btn) {
    const def = [...EMAIL_SERVICES, ...SEARCH_SERVICES].find(d => d.service === service);
    if (!def) return;
    const payload = {};
    def.fields.forEach(f => {
        const val = document.getElementById(f.id).value.trim();
        payload[f.key] = val || null;
    });
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    try {
        const res = await saveService(service, payload);
        showToast(res.message || `「${def.name}」配置已保存`, 'success');
        await loadAll();
    } catch (err) {
        showToast('保存失败: ' + err.message, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-floppy"></i> 保存';
    }
}

async function deleteService(service) {
    if (!confirm(`确定删除「${service}」的用户配置吗？删除后将回退使用服务器环境变量。`)) return;
    try {
        const res = await _fetchWithTimeout(`/api/user-config/${service}`, { method: 'DELETE' }, 15000);
        showToast(res.message || '已删除', 'success');
        await loadAll();
    } catch (err) {
        showToast('删除失败: ' + err.message, 'danger');
    }
}

// ═══════════════════════════════════════════
// 搜索引擎偏好
// ═══════════════════════════════════════════

function renderSearchEngine(info) {
    if (!info) return;
    const current = info.current || 'none';
    const engineNames = { tavily: 'Tavily', serpapi: 'SerpAPI', searxng: 'SearXNG', none: '无' };
    document.getElementById('currentEngineText').textContent = engineNames[current] || current;

    const badge = document.getElementById('searchEngineBadge');
    if (info.source === 'user') {
        badge.innerHTML = '<span class="badge rounded-pill bg-success">用户偏好</span>';
    } else if (info.source === 'global') {
        badge.innerHTML = '<span class="badge rounded-pill bg-info text-dark">全局默认</span>';
    } else {
        badge.innerHTML = '<span class="badge rounded-pill bg-secondary">未配置</span>';
    }

    const sel = document.getElementById('preferredEngine');
    sel.innerHTML = '';
    ['tavily', 'serpapi', 'searxng'].forEach(e => {
        const opt = document.createElement('option');
        opt.value = e;
        opt.textContent = `${engineNames[e]}${info.available && info.available[e] ? ' ✓' : '（未配置）'}`;
        if (e === (info.preferred || info.current)) opt.selected = true;
        sel.appendChild(opt);
    });
}

async function applyPreferredEngine() {
    const engine = document.getElementById('preferredEngine').value;
    try {
        const res = await _fetchWithTimeout(`/api/discovery/search-engine?engine=${encodeURIComponent(engine)}`, {
            method: 'POST',
        }, 15000);
        showToast(res.message || `已切换到 ${engine}`, 'success');
        await loadSearchEngine();
    } catch (err) {
        showToast('切换失败: ' + err.message, 'danger');
    }
}

async function loadSearchEngine() {
    try {
        const info = await _fetchWithTimeout('/api/discovery/search-engine');
        renderSearchEngine(info);
    } catch (err) {
        document.getElementById('currentEngineText').textContent = '获取失败';
    }
}

// ═══════════════════════════════════════════
// LinkedIn OAuth（V5.1）
// ═══════════════════════════════════════════

function renderLinkedinConfig(savedConfig, effective, oauthStatus) {
    const badge = document.getElementById('linkedinStatusBadge');
    if (!badge) return;

    // 凭据状态
    const credBadge = effective && effective.configured
        ? (effective.source === 'user'
            ? '<span class="badge rounded-pill bg-success">已配置（用户）</span>'
            : '<span class="badge rounded-pill bg-info text-dark">服务器默认</span>')
        : '<span class="badge rounded-pill bg-secondary">未配置</span>';
    // 授权状态
    const auth = oauthStatus || {};
    let authBadge = '<span class="badge rounded-pill bg-secondary">未授权</span>';
    if (auth.authorized) {
        const exp = auth.expires_at ? new Date(auth.expires_at).toLocaleString() : '';
        authBadge = `<span class="badge rounded-pill bg-success" title="过期时间: ${_esc(exp)}">已授权</span>`;
    }
    badge.innerHTML = credBadge + ' ' + authBadge;

    // 已配置的凭据回显（仅后4位）
    if (savedConfig) {
        if (savedConfig.api_key_set) {
            document.getElementById('linkedinClientId').placeholder = savedConfig.api_key + '（点击输入框替换）';
        }
        if (savedConfig.api_secret_set) {
            document.getElementById('linkedinClientSecret').placeholder = savedConfig.api_secret + '（点击输入框替换）';
        }
    }

    // 授权按钮状态
    const btnOauth = document.getElementById('btnLinkedinOauth');
    const btnDisc = document.getElementById('btnLinkedinDisconnect');
    const hint = document.getElementById('linkedinOauthHint');
    if (!auth.client_configured) {
        btnOauth.disabled = true;
        btnOauth.innerHTML = '<i class="bi bi-box-arrow-up-right me-1"></i>先保存配置';
        btnDisc.style.display = 'none';
        if (hint) hint.textContent = '请先填写并保存 Client ID / Secret';
    } else {
        btnOauth.disabled = false;
        btnOauth.innerHTML = '<i class="bi bi-box-arrow-up-right me-1"></i>开始授权';
        btnDisc.style.display = auth.authorized ? 'inline-block' : 'none';
        if (hint) hint.textContent = auth.authorized ? '已授权，可在客户详情页用官方 API 刷新组织详情' : '';
    }
}

async function saveLinkedinConfig() {
    const clientId = document.getElementById('linkedinClientId').value.trim();
    const clientSecret = document.getElementById('linkedinClientSecret').value.trim();
    const payload = {
        api_key: clientId || null,
        api_secret: clientSecret || null,
        enabled: true,
    };
    if (!clientId && !clientSecret) {
        showToast('请至少填写 Client ID 或 Client Secret', 'warning');
        return;
    }
    const btn = document.getElementById('btnSaveLinkedin');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>保存中...';
    try {
        const res = await saveService('linkedin', payload);
        showToast(res.message || 'LinkedIn 配置已保存', 'success');
        document.getElementById('linkedinClientId').value = '';
        document.getElementById('linkedinClientSecret').value = '';
        document.getElementById('linkedinClientId').placeholder = '';
        document.getElementById('linkedinClientSecret').placeholder = '';
        await loadAll();
    } catch (err) {
        showToast('保存失败: ' + err.message, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-floppy me-1"></i>保存配置';
    }
}

async function startLinkedinOauth() {
    try {
        const status = await _fetchWithTimeout('/api/linkedin/oauth/status');
        if (!status.client_configured) {
            showToast('请先保存 Client ID / Secret', 'warning');
            return;
        }
        // 跳转到授权页（完成授权后回到设置页）
        window.location.href = '/api/linkedin/oauth/start?next=/settings';
    } catch (err) {
        showToast('启动授权失败: ' + err.message, 'danger');
    }
}

async function disconnectLinkedinOauth() {
    if (!confirm('确定断开 LinkedIn 授权吗？断开后将无法使用官方 API 刷新组织详情。')) return;
    try {
        const res = await _fetchWithTimeout('/api/linkedin/oauth/disconnect', { method: 'POST' });
        showToast(res.message || '已断开授权', 'success');
        await loadAll();
    } catch (err) {
        showToast('操作失败: ' + err.message, 'danger');
    }
}

async function loadLinkedinOauthStatus() {
    try {
        const status = await _fetchWithTimeout('/api/linkedin/oauth/status');
        return status;
    } catch (err) {
        return {};
    }
}

// ═══════════════════════════════════════════
// 整体加载
// ═══════════════════════════════════════════

async function loadAll() {
    try {
        const data = await _fetchWithTimeout('/api/user-config/');
        const savedMap = {};
        (data.saved_configs || []).forEach(c => { savedMap[c.service] = c; });
        const effMap = data.effective || {};

        renderLlmConfig(savedMap.llm || null, effMap.llm || {});
        renderEmailServices(savedMap, effMap);
        renderSearchServices(savedMap, effMap);

        const oauthStatus = await loadLinkedinOauthStatus();
        renderLinkedinConfig(savedMap.linkedin || null, effMap.linkedin || {}, oauthStatus);
    } catch (err) {
        showToast('加载配置失败: ' + err.message, 'danger');
    }
    await loadSearchEngine();
}

document.addEventListener('DOMContentLoaded', loadAll);

// 暴露需要从 HTML 调用的函数到全局
window.onProviderChange = onProviderChange;
window.saveLlmConfig = saveLlmConfig;
window.testLlmConnection = testLlmConnection;
window.saveGenericService = saveGenericService;
window.deleteService = deleteService;
window.applyPreferredEngine = applyPreferredEngine;
window.toggleKey = toggleKey;
window.saveLinkedinConfig = saveLinkedinConfig;
window.startLinkedinOauth = startLinkedinOauth;
window.disconnectLinkedinOauth = disconnectLinkedinOauth;

})();
