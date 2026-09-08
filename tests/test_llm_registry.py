import pytest

from app.llm.registry import chat_url, canonical_provider, model_ids, models_url, normalize_base_url


def test_provider_alias_and_unknown_are_safe():
    assert canonical_provider("zhipuai") == "glm"
    assert canonical_provider("mistral") == "mistral"
    assert canonical_provider("not-a-provider") == "custom"


def test_url_normalization_accepts_root_or_endpoint():
    assert normalize_base_url("https://api.example.com/v1/chat/completions") == "https://api.example.com/v1"
    assert chat_url("https://api.example.com/v1/") == "https://api.example.com/v1/chat/completions"
    assert models_url("https://api.example.com/v1/chat/completions") == "https://api.example.com/v1/models"


def test_model_ids_supports_openai_payload_and_deduplicates():
    assert model_ids({"data": [{"id": "a"}, {"id": "b"}, {"id": "a"}]}) == ["a", "b"]
    assert model_ids({"data": [{"object": "model"}]}) == []
    assert model_ids([]) == []


if __name__ == "__main__":
    raise SystemExit("Run with pytest")

