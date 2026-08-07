"""
pytest 共享 fixtures
"""
import sys
import os

# 确保项目路径在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 测试环境关闭 IP 限流（避免整套用例累计触发限流导致误报）
os.environ.setdefault("DISABLE_RATE_LIMIT", "1")
