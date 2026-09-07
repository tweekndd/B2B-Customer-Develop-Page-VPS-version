"""
pytest 共享 fixtures
"""
import sys
import os

# 确保项目路径在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 测试环境关闭 IP 限流（避免整套用例累计触发限流导致误报）
os.environ.setdefault("DISABLE_RATE_LIMIT", "1")


def pytest_sessionfinish(session, exitstatus):
    """整个会话结束后清理共享测试库文件。

    tests/test_api.db 被多个测试模块以各自 engine 共享。删除动作只能放在
    会话末尾执行 —— 若在某个模块 teardown 中途 unlink，其它模块已保活的
    SQLite 连接池会指向已删除的 inode，后续写入报 readonly 错误。
    """
    db_path = os.path.join(os.path.dirname(__file__), "test_api.db")
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            p = db_path + suffix
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass  # 忽略删除失败（如 Windows 文件锁）
