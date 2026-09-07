#!/usr/bin/env bash
# =============================================================================
# B2B Customer Develop Platform — 数据库每日备份脚本（Phase0）
#
# 同时支持两种数据库：
#   - PostgreSQL（生产推荐）：docker exec b2b-db pg_dump 逻辑备份
#   - SQLite（开发/单机）：    docker cp / 直接复制 .db 文件
#
# 保留策略：
#   - 每日备份保留 30 天
#   - 每周备份（周日的 full 标记）保留 12 周
#   - 每月备份（每月 1 日）保留 12 个月
#
# 用法:
#   bash scripts/backup.sh                 # 立即备份（默认路径 ./backups）
#   BACKUP_DIR=/path bash scripts/backup.sh
#   BACKUP_ENCRYPT_KEY=xxx bash scripts/backup.sh   # 启用 gpg 对称加密备份
#
# 部署建议：加入 crontab
#   0 3 * * * cd /opt/b2b && bash scripts/backup.sh >> logs/backup.log 2>&1
# =============================================================================
set -euo pipefail

# ─── 配置 ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
ENCRYPT_KEY="${BACKUP_ENCRYPT_KEY:-}"            # 非空则用 gpg -c --no-symmetric 加密
KEEP_DAILY="${KEEP_DAILY:-30}"
KEEP_WEEKLY="${KEEP_WEEKLY:-12}"
KEEP_MONTHLY="${KEEP_MONTHLY:-12}"

# ─── 工具函数 ─────────────────────────────────────────────────────────────────
log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
die()  { log "ERROR: $*"; exit 1; }

_load_env() {
    # 读取 .env 中指定变量（安全解析，忽略注释）
    local key="$1"
    if [ -f .env ]; then
        grep -E "^${key}=" .env | tail -1 | cut -d= -f2- | tr -d '"' || true
    fi
}

_is_postgres() {
    local url
    url="$(_load_env DATABASE_URL)"
    case "$url" in
        postgresql://*|postgres://*) return 0 ;;
        *) return 1 ;;
    esac
}

_now()  { date '+%Y%m%d_%H%M%S'; }
_dow()  { date '+%u'; }          # 1=周一 ... 7=周日
_day()  { date '+%d'; }          # 每月第几天

# ─── 备份执行 ─────────────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

STAMP="$(_now)"
DOW="$(_dow)"
DOM="$(_day)"

if _is_postgres; then
    # ── PostgreSQL 逻辑备份 ──
    DB_USER="$(_load_env DB_USER)";   DB_USER="${DB_USER:-b2b}"
    DB_NAME="$(_load_env DB_NAME)";   DB_NAME="${DB_NAME:-b2b_customers}"
    CONTAINER="${DB_CONTAINER:-b2b-db}"

    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
        die "PostgreSQL 容器 ${CONTAINER} 未运行，无法备份"
    fi

    SUFFIX="daily"
    [ "$DOW" = "7" ]  && SUFFIX="weekly"
    [ "$DOM" = "01" ] && SUFFIX="monthly"

    OUT="${BACKUP_DIR}/postgres_${STAMP}_${SUFFIX}.sql"
    log "备份 PostgreSQL → ${OUT}"
    docker exec "${CONTAINER}" pg_dump -U "${DB_USER}" "${DB_NAME}" > "${OUT}"
    test -s "${OUT}" || die "pg_dump 输出为空，备份失败"
    log "pg_dump 完成（$(du -h "${OUT}" | cut -f1)）"

    # 简单校验：SQL 备份应包含 create table 语句
    if ! grep -qiE "CREATE TABLE" "${OUT}"; then
        die "备份文件缺少 CREATE TABLE，疑似备份失败"
    fi
else
    # ── SQLite 备份（优先容器，其次宿主机） ──
    OUT="${BACKUP_DIR}/sqlite_${STAMP}.db"

    if docker ps --format '{{.Names}}' | grep -q '^b2b-app$'; then
        log "从容器 b2b-app 备份 SQLite → ${OUT}"
        docker exec b2b-app sh -c 'cd /app && python - <<PY
import sqlite3, shutil, os
src="app/customers.db"
tmp="/tmp/customers_bk.db"
con=sqlite3.connect(src)
dst=sqlite3.connect(tmp)
with dst:
    con.backup(dst)
dst.close(); con.close()
shutil.copy(tmp, src+".prebackup")
PY' || true
        docker cp b2b-app:/app/app/customers.db "${OUT}"
    elif [ -f "app/customers.db" ]; then
        log "备份宿主机 SQLite → ${OUT}"
        python -c "import sqlite3,shutil,sys; con=sqlite3.connect('app/customers.db'); dst=sqlite3.connect(sys.argv[1]); con.backup(dst); dst.close(); con.close()" "${OUT}"
    else
        die "未找到可备份的 SQLite 数据库"
    fi
    test -s "${OUT}" || die "SQLite 备份为空，失败"
fi

# ─── 可选加密 ─────────────────────────────────────────────────────────────────
if [ -n "$ENCRYPT_KEY" ]; then
    log "启用 gpg 对称加密备份..."
    OUT_ENC="${OUT}.gpg"
    gpg --batch --yes --passphrase "$ENCRYPT_KEY" -c --cipher-algo AES256 -o "${OUT_ENC}" "${OUT}"
    rm -f "${OUT}"
    OUT="${OUT_ENC}"
fi

log "备份成功: ${OUT}"

# ─── 保留策略清理 ─────────────────────────────────────────────────────────────
_cleanup() {
    local pattern="$1" keep="$2" suffix="$3"
    # suffix 非空时仅删除该类别；保留最近 keep 个
    local files
    if [ -n "$suffix" ]; then
        files=$(ls -1t "${BACKUP_DIR}"/${pattern}_*_${suffix}.sql* 2>/dev/null | tail -n +"$((keep + 1))" || true)
    else
        files=$(ls -1t "${BACKUP_DIR}"/${pattern}_*.db* 2>/dev/null | tail -n +"$((keep + 1))" || true)
    fi
    for f in $files; do
        [ -n "$f" ] && rm -f "$f" && log "清理旧备份: $(basename "$f")"
    done
}

if _is_postgres; then
    _cleanup "postgres" "$KEEP_MONTHLY" "monthly"
    _cleanup "postgres" "$KEEP_WEEKLY"  "weekly"
    _cleanup "postgres" "$KEEP_DAILY"   "daily"
else
    _cleanup "sqlite" "$KEEP_DAILY" ""
fi

log "备份完成。备份目录: ${BACKUP_DIR}"
exit 0
