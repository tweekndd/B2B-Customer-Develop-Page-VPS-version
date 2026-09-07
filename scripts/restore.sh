#!/usr/bin/env bash
# =============================================================================
# B2B Customer Develop Platform — 数据库恢复脚本（Phase0）
#
# 从 backups/ 中最新（或指定）备份恢复数据库。恢复前会自动备份当前库。
# 仅支持 .env 未启用加密 或 提供 BACKUP_ENCRYPT_KEY 的 .gpg 备份。
#
# 用法:
#   bash scripts/restore.sh                      # 恢复最新备份
#   bash scripts/restore.sh backups/postgres_xxxx_daily.sql   # 恢复指定文件
#   BACKUP_ENCRYPT_KEY=xxx bash scripts/restore.sh            # 解密 .gpg 备份
#
# 危险操作：会清空并重建目标数据库。请确认已理解后果。
# =============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
ENCRYPT_KEY="${BACKUP_ENCRYPT_KEY:-}"
CONFIRM="${CONFIRM:-yes}"   # 交互确认，脚本自动化请设 CONFIRM=yes

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
die()  { log "ERROR: $*"; exit 1; }

_load_env() {
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

# ─── 选择备份文件 ─────────────────────────────────────────────────────────────
SRC="${1:-}"
if [ -z "$SRC" ]; then
    if _is_postgres; then
        SRC="$(ls -1t "${BACKUP_DIR}"/postgres_*.sql* 2>/dev/null | head -1 || true)"
    else
        SRC="$(ls -1t "${BACKUP_DIR}"/sqlite_*.db* 2>/dev/null | head -1 || true)"
    fi
fi

[ -n "$SRC" ] || die "未找到可用备份文件（备份目录: ${BACKUP_DIR}）"
[ -f "$SRC" ] || die "备份文件不存在: ${SRC}"

# 若为 .gpg 需先解密到临时文件
WORK=""
if [[ "$SRC" == *.gpg ]]; then
    [ -n "$ENCRYPT_KEY" ] || die "备份已加密，请提供 BACKUP_ENCRYPT_KEY"
    log "解密备份: ${SRC}"
    WORK="$(mktemp /tmp/b2b_restore_XXXXXX)"
    gpg --batch --yes --passphrase "$ENCRYPT_KEY" -d -o "$WORK" "$SRC" 2>/dev/null \
        || die "解密失败（密钥错误或文件损坏）"
    SRC="$WORK"
fi

log "恢复源文件: ${SRC}"

if [ "$CONFIRM" != "yes" ]; then
    read -r -p "即将清空并恢复数据库，继续？[y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || die "已取消"
fi

# ─── 恢复前备份当前库 ─────────────────────────────────────────────────────────
STAMP=$(date '+%Y%m%d_%H%M%S')
log "恢复前备份当前数据库 → ${BACKUP_DIR}/before_restore_${STAMP}.bak"
if _is_postgres; then
    DB_USER="$(_load_env DB_USER)"; DB_USER="${DB_USER:-b2b}"
    DB_NAME="$(_load_env DB_NAME)"; DB_NAME="${DB_NAME:-b2b_customers}"
    CONTAINER="${DB_CONTAINER:-b2b-db}"
    docker exec "$CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" \
        > "${BACKUP_DIR}/before_restore_${STAMP}.sql" 2>/dev/null \
        || log "警告：恢复前备份失败（忽略）"
else
    if docker ps --format '{{.Names}}' | grep -q '^b2b-app$'; then
        docker cp b2b-app:/app/app/customers.db "${BACKUP_DIR}/before_restore_${STAMP}.db" 2>/dev/null \
            || log "警告：恢复前备份失败（忽略）"
    elif [ -f "app/customers.db" ]; then
        cp "app/customers.db" "${BACKUP_DIR}/before_restore_${STAMP}.db"
    fi
fi

# ─── 执行恢复 ─────────────────────────────────────────────────────────────────
if _is_postgres; then
    DB_USER="$(_load_env DB_USER)"; DB_USER="${DB_USER:-b2b}"
    DB_NAME="$(_load_env DB_NAME)"; DB_NAME="${DB_NAME:-b2b_customers}"
    CONTAINER="${DB_CONTAINER:-b2b-db}"

    log "重建 PostgreSQL 库 ${DB_NAME} ..."
    # 先停应用避免写入冲突（提示）
    log "提示：恢复期间请暂停应用写入（docker compose stop app）"
    docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
    log "导入备份..."
    docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" < "$SRC"
    log "PostgreSQL 恢复完成。请执行版本化迁移补丁：alembic upgrade head"
else
    log "复制 SQLite 备份..."
    if docker ps --format '{{.Names}}' | grep -q '^b2b-app$'; then
        # 容器运行中：需停止应用再替换，避免文件锁/脏写
        log "提示：建议先 docker compose stop app 再恢复"
        docker cp "$SRC" b2b-app:/app/app/customers.db
    else
        cp "$SRC" "app/customers.db"
    fi
    log "SQLite 恢复完成，请重启应用"
fi

# 清理解密临时文件
if [ -n "$WORK" ]; then rm -f "$WORK"; fi
log "恢复完成"
exit 0
