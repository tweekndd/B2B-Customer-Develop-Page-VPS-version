"""
LLM 统一架构包（V5.0 新增）

业务代码统一通过 get_llm_manager().chat() 调用 AI，禁止直接调用具体模型 API。

结构：
  ├── config.py      配置解析（环境变量 → Round 3 用户配置）
  ├── exceptions.py  统一异常体系
  ├── utils.py       JSON 解析等工具
  ├── router.py      自动 Fallback + 重试
  ├── manager.py     统一入口（Provider 工厂 + 路由）
  └── providers/     Provider 实现（base / glm / openai_compatible）
"""
from app.llm.manager import LLMManager, get_llm_manager
from app.llm.providers.base import LLMChatResult
from app.llm.config import LLMConfig, resolve_config

__all__ = [
    "LLMManager",
    "get_llm_manager",
    "LLMChatResult",
    "LLMConfig",
    "resolve_config",
]
