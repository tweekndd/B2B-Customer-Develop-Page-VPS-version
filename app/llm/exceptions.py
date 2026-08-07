"""
LLM 统一异常体系（V5.0 新增）
Router 依据异常类型决定是否 Fallback / 重试：
  - LLMTimeoutError        → 重试后降级
  - LLMRateLimitError      → 立即降级下一个模型
  - LLMModelUnavailableError → 立即降级下一个模型
  - LLMAuthenticationError → 立即失败（Key 无效，降级无意义）
  - LLMConnectionError     → 立即失败
  - LLMContentError        → 内容解析/空内容问题
"""


class LLMError(Exception):
    """LLM 调用基类异常"""


class LLMAuthenticationError(LLMError):
    """API Key 无效或无权限（401/403）"""


class LLMRateLimitError(LLMError):
    """触发限流（429）"""


class LLMTimeoutError(LLMError):
    """请求超时"""


class LLMModelUnavailableError(LLMError):
    """模型不可用 / 服务端错误（500/502/503/504）"""


class LLMConnectionError(LLMError):
    """网络连接失败或非预期 HTTP 错误"""


class LLMContentError(LLMError):
    """响应内容异常（空内容 / 缺少字段 / JSON 解析失败）"""
