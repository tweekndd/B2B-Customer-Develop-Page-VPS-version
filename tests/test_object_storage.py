"""
对象存储服务测试（Phase0）

验证：
1. put_object 落盘 + storage_objects 索引；同内容幂等去重
2. get_object 读回内容；get_object_meta 返回元数据
3. delete_object 删除文件与索引（幂等）
4. cleanup_expired_objects 清理过期对象
5. StorageObject 注册在 Base.metadata（模型完整性）
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, StorageObject
from app.services import object_storage as ost


_TEST_DB = os.path.join(os.path.dirname(__file__), "test_api.db")
_test_engine = create_engine(f"sqlite:///{_TEST_DB}", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)


@pytest.fixture(autouse=True)
def _setup_db(tmp_path, monkeypatch):
    """每个用例重建表，并将对象目录指向临时目录"""
    Base.metadata.create_all(bind=_test_engine)
    # 隔离对象目录，避免污染真实 data/
    monkeypatch.setattr(ost, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ost, "OBJECTS_DIR", os.path.join(str(tmp_path), "objects"))
    yield
    Base.metadata.drop_all(bind=_test_engine)
    ost.remove_local_objects_dir()


class TestObjectStorage:
    def test_put_and_get_roundtrip(self):
        db = TestSessionLocal()
        content = b"website snapshot content " * 100
        obj = ost.put_object(db, content, content_type="text/plain")
        assert obj.size_bytes == len(content)
        assert obj.storage_provider == "local"
        assert ost.get_object(db, obj.object_key) == content
        meta = ost.get_object_meta(db, obj.object_key)
        assert meta is not None and meta.content_hash == obj.content_hash
        assert db.query(StorageObject).count() == 1
        db.close()

    def test_put_is_idempotent_dedup(self):
        db = TestSessionLocal()
        content = b"same content"
        ost.put_object(db, content)
        ost.put_object(db, content)  # 同内容第二次写入
        assert db.query(StorageObject).count() == 1
        db.close()

    def test_different_content_two_objects(self):
        db = TestSessionLocal()
        ost.put_object(db, b"aaa")
        ost.put_object(db, b"bbb")
        assert db.query(StorageObject).count() == 2
        db.close()

    def test_get_missing_returns_empty(self):
        db = TestSessionLocal()
        assert ost.get_object(db, "not-exist-key") == b""
        db.close()

    def test_delete_idempotent(self):
        db = TestSessionLocal()
        obj = ost.put_object(db, b"to delete")
        key = obj.object_key
        assert ost.delete_object(db, key) is True
        assert db.query(StorageObject).count() == 0
        assert ost.get_object(db, key) == b""
        # 再次删除：返回 False（幂等）
        assert ost.delete_object(db, key) is False
        db.close()

    def test_cleanup_expired(self):
        db = TestSessionLocal()
        ost.put_object(db, b"keep forever")  # retention_until=None
        ost.put_object(
            db,
            b"expired",
            retention_until=datetime.datetime.utcnow() - datetime.timedelta(days=1),
        )
        cleaned = ost.cleanup_expired_objects(db)
        assert cleaned == 1
        assert db.query(StorageObject).count() == 1
        db.close()

    def test_disk_usage(self):
        db = TestSessionLocal()
        ost.put_object(db, b"12345")
        assert ost.disk_usage_bytes() == 5
        db.close()
