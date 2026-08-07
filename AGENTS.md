# AI Trade Customer Analyzer — Agent Guide

A FastAPI web application for foreign trade customer discovery, AI analysis, scoring, and multi-source email finding. Monolithic Python backend with Jinja2 frontend.

## Quick Start

```bash
pip install -r requirements.txt
set GLM_API_KEY=your-glm-api-key
set SERPAPI_API_KEY=xxx       # or TAVILY_API_KEY
python main.py                # → http://localhost:8000
```

## Essential Commands

| Command | Purpose |
|---------|---------|
| `python main.py` | Run production (autocreate dirs, no reload) |
| `python -m uvicorn main:app --reload` | Dev with hot reload |
| `pytest tests/` | Run all tests (isolated SQLite DB) |
| `pytest tests/test_X.py -v` | Single test file |

**Never** run `python main.py` with `reload=True` on Windows — it causes route registration bugs (`main.py:101-107` comment explains). Use `uvicorn --reload` instead.

## Architecture Overview

```
main.py (FastAPI app)
  ├── app/api/          ← 10 route modules (injected via APIRouter)
  ├── app/services/     ← 29 service modules (business logic)
  ├── app/llm/          ← LLM 统一架构 (manager/router/config/exceptions + providers)
  ├── app/templates/    ← Jinja2 HTML (Chinese UI)
  ├── app/static/       ← 8 JS modules + CSS
  └── app/database.py   ← SQLAlchemy models + auto-migration
```

### API Routing (`app/api/__init__.py`)

All routes mount under `/api` prefix. Sub-modules use `router = APIRouter(tags=[...])` **without** prefix — the prefix is inherited. 10 sub-routers are merged into one:

| Module | Tags | Key endpoints |
|--------|------|--------------|
| `auth.py` | 认证 | Login, logout, current user (`/auth/*`) |
| `users.py` | users | Admin-only user CRUD + per-user permissions |
| `customers.py` | customers | CRUD, import/export, analyze, add-emails |
| `discovery.py` | discovery | Search tasks, SSE stream, keyword expansion |
| `sync.py` | sync | Multi-device data sync (export/import JSON) |
| `config.py` | config | Read/write JSON config files, schema validation |
| `hunter.py` | hunter | Hunter.io email lookup |
| `tomba.py` | tomba | Tomba.io email lookup |
| `waterfall.py` | waterfall | Multi-source cascaded email discovery |
| `geocode.py` | geocode | Batch/single geocoding endpoints |

### Service Layer (`app/services/`)

Key service responsibilities and data flow:

1. **Discovery Flow**: `search_task_service.py` orchestrates the full pipeline:
   - `keyword_expander.py` → AI generates related keywords in target language
   - `google_discovery.py` or `tavily_discovery.py` → search web (runtime-switchable via `set_search_engine()`)
   - `company_filter.py` → filter out social media, news, etc.
   - `url_normalizer.py` → normalize URLs
   - `deduplication.py` → domain + fuzzy name dedup
   - `website_scraper.py` → scrape /about /services etc.
   - `email_extractor.py` → extract target-prefix emails
   - `keyword_analyzer.py` → hit positive/negative keywords
   - `glm_analyzer.py` → AI analysis (company type, hook, etc.)
   - `scoring_engine.py` → 5-dimension rule-based scoring

2. **Email Discovery**: `waterfall_discovery.py` cascades:
   - `hunter_service.py` → `tomba_service.py` → `prospeo_service.py` → scrape mailto:
   - Configurable via `EMAIL_DISCOVERY_MIN_RESULTS` (default 2)
   - Scoring: Tomba(30) > Prospeo(28) > Hunter(25) > scraped(10)

3. **Cache**: 5 cache tables with TTL-based cleanup via `cache_manager.py` + startup cleanup

## Database

**Default**: SQLite at `app/customers.db`  
**Override**: `DATABASE_URL` env var (PostgreSQL supported)

**11 tables**: Customer, User, SearchTask, SearchCache, WebsiteCache, AnalysisCache, HunterCache, TombaCache, ProspeoCache, EmailQuotaLog, GeocodeCache

**Note**: `app/database.py` actually defines 11 models — the above plus `User`, `GeocodeCache`. Auto-migration: `init_db()` in `database.py:283` creates tables + adds missing columns via `ALTER TABLE ADD COLUMN`. Indexes are created with `CREATE INDEX IF NOT EXISTS`. No Alembic or migration tooling.

**Key Customer fields**: `scrape_status` / `ai_status` / `fail_reason` track processing state. `emails` is a JSON string, not a relation. Scores are individual columns (`industry_score`..`total_score`).

## Configuration

Two JSON config files in `app/services/`:

| File | Purpose | Editing |
|------|---------|---------|
| `industry_config.json` | Scoring rules, keywords, priority thresholds | Via `/api/config` UI or direct edit |
| `country_weights.json` | Country → score mapping | Same |

**Gotcha**: Both are `lru_cache`'d in `scoring_engine.py`. After writing, call `invalidate_config_cache()` + `invalidate_keyword_cache()`. The `/api/config` PUT endpoint handles this. Direct file edits require server restart.

## External APIs (all via env vars)

| API | Env var | Free tier | Notes |
|-----|---------|-----------|-------|
| 智谱 GLM | `GLM_API_KEY` | Free | AI analysis + keyword expansion. Model: `glm-4.7-flash` (free flagship text model). Compatible with old `DEEPSEEK_API_KEY` |
| SerpAPI | `SERPAPI_API_KEY` | 250/mo | Google search |
| Tavily | `TAVILY_API_KEY` | 1000/mo | Web search (preferred if both configured) |
| Hunter.io | `HUNTER_API_KEY` | 25/mo | Email domain search |
| Tomba.io | `TOMBA_API_KEY` + `SECRET` | 25/mo | Richer email data (LinkedIn, phone, score) |
| Prospeo.io | `PROSPEO_API_KEY` | Paid | Search+Enrich, 1 credit/email |

**Search engine auto-detection**: If `SEARXNG_URL` set → SearXNG; else if `TAVILY_API_KEY` set → Tavily; else if `SERPAPI_API_KEY` set → SerpAPI. Override with `SEARCH_ENGINE=searxng|tavily|serpapi`.

## LLM 统一架构（V5.0 新增）

**核心原则：业务代码禁止直接调用具体模型 API，一律通过 `get_llm_manager().chat(...)`。**

```
app/llm/
  ├── manager.py   统一入口（Provider 工厂 + 配置解析）
  ├── router.py    自动 Fallback + 重试（模型链去重、限流/超时/模型不可用降级）
  ├── config.py    配置解析：优先读用户配置（user_api_config 表），再回退环境变量
  ├── exceptions.py 统一异常体系（Authentication/RateLimit/Timeout/ModelUnavailable/Connection/Content）
  ├── utils.py     extract_json() 处理 markdown 代码块/前后缀文字/数组
  └── providers/
      ├── base.py               BaseLLMProvider 抽象（chat / test_connection / get_models）
      ├── glm.py                GLMProvider（默认 URL + 模型 glm-4.7-flash/glm-4.6v-flash/glm-4-flash-250414）
      └── openai_compatible.py  OpenAICompatibleProvider（api_key+base_url+model，DeepSeek/Qwen/Moonshot/GLM/Custom）
```

- **调用链**：`业务代码 → LLMManager.chat() → resolve_config() → get_provider() → LLMRouter.chat() → Provider.chat() → HTTP API`
- **4 个 LLM 调用点**（已重构走统一接口）：
  1. `glm_analyzer.py:analyze_company()` — 客户官网文本分析
  2. `keyword_expander.py:expand_keywords()` — 关键词扩展/翻译
  3. `similar_company_finder.py:_translate_to_local_language()` — 相似客户本地化翻译
  4. `similar_company_finder.py:extract_business_info()` — 业务信息提取
- **兼容性**：所有入口函数签名不变（新增可选 `user_id` 参数，Round 3 用于按用户取 Key），`retry_async(analyze_company, ...)` 等调用无需改动。
- **Router 策略**：`LLMTimeoutError`→当前模型重试2次后降级；`LLMRateLimitError`/`LLMModelUnavailableError`→立即降级；`LLMAuthenticationError`/`LLMConnectionError`→立即失败；空内容按 `finish_reason=="length"` 降级否则重试。
- **Model fallback 链**：主模型 + `GLM_FALLBACK_MODELS`（默认 `glm-4.7-flash,glm-4.6v-flash,glm-4-flash-250414`）自动去重。
- **Round 3（已完成）**：`user_api_config` 表（Fernet 加密存储）+ `/api/user-config` CRUD 接口 + `SearchTask.user_id` 支持后台任务按用户取 Key。LLM 与所有外部 API（Hunter/Tomba/Prospeo/Tavily/SerpAPI/SearXNG）均支持「用户配置优先，环境变量回退」。详见下方「Round 3 用户级 API Key」。

## Round 3 用户级 API Key（Multi-user SaaS）

每个用户可保存自己的外部服务 Key，未配置时自动回退服务器环境变量（向后兼容）。

- **表**：`user_api_config`（user_id + service 唯一，API Key 用 Fernet 加密）
- **加密密钥**：`API_CONFIG_ENCRYPTION_KEY` 环境变量，否则自动生成并持久化到 `app/.config_encryption_key`（已 gitignore，勿提交）
- **服务层**：`app/services/user_config.py` — `get_effective_api_key/secret/base_url`、`resolve_service_config`、`resolve_search_config`、CRUD
- **API 层**：`app/api/user_config.py` — `GET/POST/DELETE /api/user-config/{service}`、列表、`POST /api/user-config/llm/test`
- **搜索引擎偏好**：`POST /api/discovery/search-engine` 按用户持久化；后台搜索任务通过 `SearchTask.user_id` 解析用户 Key
- **打通点**：`glm_analyzer` / `keyword_expander` / `similar_company_finder`（LLM）、`google_discovery`/`tavily`/`searxng`（搜索）、`hunter`/`tomba`/`prospeo`/`waterfall`（邮箱）

## Key Patterns & Gotchas

### Testing
- Tests use **separate SQLite file** (`test_api.db`), not production DB
- `conftest.py` adds project root to `sys.path`
- Config files are **backed up** at session start and **restored** after each test (protects production config)
- Each test function drops and recreates all tables

### Async Architecture
- `main.py` uses `lifespan` context manager (not deprecated `@app.on_event`)
- Search tasks run via `asyncio.get_event_loop().create_task(...)` — fire-and-forget
- SSE streaming (`/discovery/task-stream/{id}`) pushes real-time progress to frontend
- All services that call external APIs are `async def`
- Some services (Hunter, Tomba, Prospeo clients) remain synchronous with `httpx` in sync mode

### Caching
- 5 cache tables, each with different TTL:
  - `search_cache`: 30 days
  - `website_cache`: 7 days
  - `analysis_cache`: content hash-based (permanent if content unchanged)
  - `hunter_cache` / `tomba_cache` / `prospeo_cache`: env configurable (default 7 days)
- Manual cache cleanup endpoint: `POST /admin/cleanup-cache`
- Cache hits don't consume API quotas

### Deduplication
- `deduplication.py` uses domain matching (primary) + fuzzy company name matching (fallback)
- Company name normalization removes legal suffixes (`Inc`, `S.A. de C.V.`, `GmbH`, etc.), punctuation, and stop words
- **Order matters**: complex suffix patterns must precede simple ones (e.g., `S.A. de C.V.` before `S.A.`)

### Multi-language Search
- `country_language_map.py` maps 130+ countries to Google hl/lr/cr parameters
- `keyword_expander.py` uses this to generate keywords in the target language
- `google_discovery.py` passes these params to SerpAPI

### Email Extraction
- `email_extractor.py` targets specific prefixes: `info`, `sales`, `contact`, `procurement`, `project`, `marketing`
- `waterfall_discovery.py` filters out generic blacklist prefixes (`noreply`, `support`, `postmaster`, etc.)

### Scoring Engine
5 dimensions (configurable via JSON):
- Industry match: 30pts (keyword weight * frequency, capped at weight max 5)
- Project match: 25pts (has projects page + industry content relevance)
- Company type: 20pts (EPC=20, Contractor=18, Distributor=18, Dealer=17, Importer=17, Trader=16, End User=16, Mining Company=16, Manufacturer=8, etc.)
- Country priority: 15pts (from country_weights.json)
- Contact completeness: 10pts (tiered by email count)
- **V4.6 价格询盘加成**: +5pts if `is_price_inquiry` (configurable `scoring.price_inquiry`), total capped at 100

Priority: A(≥80) > B(≥60) > C(≥40) > D

### 买家/供应商评分分级 + 开发信生成（V4.6）
- **买家意向评分**：`glm_analyzer.analyze_company()` 输出 `buyer_intent_score`(0-10)，供应商/制造商/电商页=低分，矿场/EPC/政府招标=高分，**经销商/分销商/贸易商=高价值采购方（7-9），不得因非终端用户降分**。取分用 `get_buyer_intent_score()`，价格询盘用 `get_price_inquiry()`（→ `is_price_inquiry` 列）
- **开发信生成**：`app/services/email_composer.py` → `generate_email_draft()` 产品关键词驱动 + `detect_email_language(country)` 多语种自动检测（country_language_map 130+ 国家）。API：`POST /api/customers/{id}/email-draft`，存 `customers.email_draft`(JSON)
- **数据库新列**：`customers.buyer_intent_score`(INTEGER)、`is_price_inquiry`(INTEGER)、`email_draft`(TEXT) — 均通过 `_migrate_add_column` 自动迁移，sync.py 导出/导入已含
- **前端**：详情页「AI 开发信生成」卡片 + 买家意向徽章；列表页 `8/10 高购买意向` 徽章与「🔥 询价」标记

### Frontend
- Pure Jinja2 templates (no SPA framework)
- Shared JS in `app/static/js/app.js` with IntersectionObserver animations, number counting, fetch timeout helper
- SSE for real-time task progress (replaced polling in V3.1.1)
- Chinese language UI throughout

### Sync Script
- `sync.sh` supports `export|import|status` for multi-device data sharing
- Works via REST API endpoints (`/api/sync/export`, `/api/sync/import`)
- Designed for iCloud/Dropbox/USB workflows
