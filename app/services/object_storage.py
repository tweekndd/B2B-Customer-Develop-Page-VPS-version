"""
对象存储服务（Phase0）

数据分层策略：数据库保存业务事实，大体积原始内容（官网快照全文、邮件 MIME、
附件、导出文件）外置到对象存储，数据库 storage_objects 表仅保存对象索引。

默认 Provider：本地文件系统（DATA_DIR/objects）。
Provider 抽象：storage provider 只需实现 put_bytes / get_bytes / delete / exists，
后续可增加 S3 兼容 Provider（R2/MinIO/OSS）而无侵入。

用法：
    from app.services.object_storage import put_object, get_object
    ref = put_object(db, b"...", content_type="text/plain")   # 返回 StorageObject
    data = get_object(db, ref.object_key) or b""
"""
import datetime
import hashlib
import os
import shutil

from sqlalchemy.orm import Session

from app.database import StorageObject

# 对象根目录（可被 DATA_DIR 覆盖；默认项目 data/objects，已 gitignore）
DATA_DIR = os.environ.get("DATA_DIR", "").strip() or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
)
OBJECTS_DIR = os.path.join(DATA_DIR, "objects")

# 对象键前缀目录层数（分散文件，避免单目录文件过多）
_PREFIX_DEPTH = 2


def _objects_dir() -> str:
    os.makedirs(OBJECTS_DIR, exist_ok=True)
    return OBJECTS_DIR


def _key_to_path(object_key: str) -> str:
    """将对象键映射到文件路径（按哈希前缀分层存储）"""
    if len(object_key) >= _PREFIX_DEPTH * 2:
        prefix = os.path.join(*[object_key[i:i + 2] for i in range(0, _PREFIX_DEPTH * 2, 2)])
    else:
        prefix = object_key
    return os.path.join(_objects_dir(), prefix, object_key)


# ═══════════════════════════════════════════════════════════════════════
# Provider: local
# ═══════════════════════════════════════════════════════════════════════

def _local_put(object_key: str, data: bytes) -> None:
    path = _key_to_path(object_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _local_get(object_key: str) -> bytes:
    path = _key_to_path(object_key)
    with open(path, "rb") as f:
        return f.read()


def _local_delete(object_key: str) -> None:
    path = _key_to_path(object_key)
    if os.path.exists(path):
        os.remove(path)


def _local_exists(object_key: str) -> bool:
    return os.path.exists(_key_to_path(object_key))


# Provider 分发表（后续增加 s3 等在此注册）
_PROVIDERS = {
    "local": {
        "put": _local_put,
        "get": _local_get,
        "delete": _local_delete,
        "exists": _local_exists,
    },
}


def _provider(provider: str = "local") -> dict:
    if provider not in _PROVIDERS:
        raise ValueError(f"未知对象存储 Provider: {provider}")
    return _PROVIDERS[provider]


# ═══════════════════════════════════════════════════════════════════════
# 对外接口
# ═══════════════════════════════════════════════════════════════════════

def put_object(
    db: Session,
    data: bytes,
    *,
    content_type: str = None,
    provider: str = "local",
    retention_until: datetime.datetime = None,
) -> StorageObject:
    """写入对象并记录索引。内容寻址：同内容自动去重（幂等）。

    同一内容重复写入只保留一条 storage_objects 索引（对象键相同）。
    """
    content_hash = hashlib.sha256(data).hexdigest()
    object_key = content_hash[:32]  # 内容寻址键

    existing = db.query(StorageObject).filter(StorageObject.object_key == object_key).first()
    if existing:
        return existing

    _provider(provider)["put"](object_key, data)

    obj = StorageObject(
        object_key=object_key,
        content_hash=content_hash,
        content_type=content_type,
        size_bytes=len(data),
        storage_provider=provider,
        retention_until=retention_until,
        created_at=datetime.datetime.utcnow(),
    )
    db.add(obj)
    db.flush()
    return obj


def get_object(db: Session, object_key: str) -> bytes:
    """按对象键读取内容；索引不存在则返回空字节（不抛异常，便于按需容错）"""
    obj = db.query(StorageObject).filter(StorageObject.object_key == object_key).first()
    if obj is None:
        return b""
    return _provider(obj.storage_provider)["get"](obj.object_key)


def get_object_meta(db: Session, object_key: str):
    """读取对象索引元数据（不加载内容）"""
    return db.query(StorageObject).filter(StorageObject.object_key == object_key).first()


def delete_object(db: Session, object_key: str) -> bool:
    """删除对象文件与索引。物理文件不存在时视为已删除（幂等）。"""
    obj = db.query(StorageObject).filter(StorageObject.object_key == object_key).first()
    if obj is None:
        return False
    try:
        _provider(obj.storage_provider)["delete"](obj.object_key)
    except FileNotFoundError:
        pass
    db.delete(obj)
    db.flush()
    return True


def exists_object(db: Session, object_key: str) -> bool:
    obj = db.query(StorageObject).filter(StorageObject.object_key == object_key).first()
    if obj is None:
        return False
    try:
        return _provider(obj.storage_provider)["exists"](obj.object_key)
    except Exception:
        return False


def cleanup_expired_objects(db: Session, now: datetime.datetime = None) -> int:
    """删除超过 retention_until 的对象（归档清理任务调用）"""
    now = now or datetime.datetime.utcnow()
    expired = db.query(StorageObject).filter(
        StorageObject.retention_until.isnot(None),
        StorageObject.retention_until < now,
    ).all()
    count = 0
    for obj in expired:
        if delete_object(db, obj.object_key):
            count += 1
    if count:
        db.commit()
    return count


def get_data_dir() -> str:
    """返回数据根目录（对象、后续导出文件等共用）"""
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR


def disk_usage_bytes() -> int:
    """估算对象目录占用（运维告警用）"""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(OBJECTS_DIR):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                continue
    return total


def remove_local_objects_dir() -> None:
    """测试辅助：清空本地对象目录"""
    if os.path.exists(OBJECTS_DIR):
        shutil.rmtree(OBJECTS_DIR, ignore_errors=True)
