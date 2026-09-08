"""LLM provider 元数据、默认 URL 和模型发现辅助工具。"""
from typing import Any, Dict, List
from urllib.parse import urlparse, urlunparse


PROVIDER_CATALOG: Dict[str, Dict[str, Any]] = {
    "glm": {"name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.7-flash", "models": ["glm-4.7-flash", "glm-4.6v-flash", "glm-4-flash-250414"]},
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "models": ["deepseek-chat", "deepseek-reasoner"]},
    "qwen": {"name": "通义千问 Qwen", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "models": ["qwen-plus", "qwen-turbo", "qwen-max", "qwen-long"]},
    "moonshot": {"name": "Moonshot Kimi", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"]},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"]},
    "mistral": {"name": "Mistral AI", "base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest", "models": ["mistral-small-latest", "mistral-large-latest", "codestral-latest"]},
    "groq": {"name": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "openai/gpt-oss-120b"]},
    "together": {"name": "Together AI", "base_url": "https://api.together.xyz/v1", "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "models": []},
    "fireworks": {"name": "Fireworks AI", "base_url": "https://api.fireworks.ai/inference/v1", "model": "accounts/fireworks/models/llama-v3p1-70b-instruct", "models": []},
    "xai": {"name": "xAI Grok", "base_url": "https://api.x.ai/v1", "model": "grok-3-mini", "models": ["grok-3-mini", "grok-3"]},
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini", "models": []},
    "siliconflow": {"name": "硅基流动 SiliconFlow", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3", "models": []},
    "ollama": {"name": "Ollama（本地）", "base_url": "http://localhost:11434/v1", "model": "llama3.2", "models": []},
    "custom": {"name": "OpenAI 兼容 / 自定义", "base_url": "", "model": "", "models": []},
}

ALIASES = {"zhipu": "glm", "zhipuai": "glm", "bigmodel": "glm", "openai-compatible": "custom", "compatible": "custom"}


def canonical_provider(provider: str) -> str:
    value = (provider or "custom").strip().lower()
    return ALIASES.get(value, value if value in PROVIDER_CATALOG else "custom")


def provider_options() -> List[Dict[str, Any]]:
    return [{"id": key, "name": value["name"], "base_url": value["base_url"], "default_model": value["model"], "models": value["models"]} for key, value in PROVIDER_CATALOG.items()]


def normalize_base_url(base_url: str, provider: str = "custom") -> str:
    """将用户输入规范化为 API 根地址，兼容填入完整 chat/completions URL。"""
    value = (base_url or "").strip().rstrip("/")
    if not value:
        value = PROVIDER_CATALOG.get(canonical_provider(provider), {}).get("base_url", "")
    for suffix in ("/chat/completions", "/completions"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    return value.rstrip("/")


def models_url(base_url: str, provider: str = "custom") -> str:
    root = normalize_base_url(base_url, provider)
    if root.endswith("/v4") and canonical_provider(provider) == "glm":
        return f"{root}/models"
    return f"{root}/models"


def chat_url(base_url: str, provider: str = "custom") -> str:
    root = normalize_base_url(base_url, provider)
    return f"{root}/chat/completions"


def model_ids(payload: Any) -> List[str]:
    """解析 OpenAI / vLLM / 部分厂商返回的模型列表。"""
    values = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        return []
    result: List[str] = []
    for item in values:
        value = item.get("id") if isinstance(item, dict) else item
        if isinstance(value, str) and value.strip() and value.strip() not in result:
            result.append(value.strip())
    return result


def safe_url_display(url: str) -> str:
    try:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
    except Exception:
        return url


__all__ = ["PROVIDER_CATALOG", "canonical_provider", "provider_options", "normalize_base_url", "models_url", "chat_url", "model_ids"]


if __name__ == "__main__":
    raise SystemExit("This module is imported by the application; do not execute it directly.")

