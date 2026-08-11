"""LLM 适配器单元测试：环境变量解析 + 兼容别名（不联网）。"""

import pytest

from src.utils import llm as llm_module


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "LLM_EXTRA_BODY",
        "LLM_TIMEOUT",
        "LLM_MAX_RETRIES",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_default_falls_back_to_deepseek_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    cfg = llm_module.get_llm_config()
    assert cfg["provider"] == "deepseek"
    assert cfg["base_url"] == "https://api.deepseek.com/v1"
    assert cfg["model"] == "deepseek-v4-pro"
    assert cfg["api_key"] == "sk-test"


def test_iflytek_preset(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "iflytek")
    monkeypatch.setenv("LLM_API_KEY", "ak:sk")
    cfg = llm_module.get_llm_config()
    assert cfg["base_url"] == "https://spark-api-open.xf-yun.com/v1"
    assert cfg["model"] == "4.0Ultra"
    assert cfg["label"] == "讯飞星火"


def test_custom_requires_base_url(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        llm_module.get_llm_config()


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(ValueError, match="未知 LLM_PROVIDER"):
        llm_module.get_llm_config()


def test_extra_body_must_be_valid_json(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "iflytek")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_EXTRA_BODY", "{bad")
    with pytest.raises(ValueError, match="LLM_EXTRA_BODY"):
        llm_module.get_llm_config()


def test_extra_body_parsed(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "iflytek")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"thinking": {"type": "enabled"}}')
    cfg = llm_module.get_llm_config()
    assert cfg["extra_body"] == {"thinking": {"type": "enabled"}}


def test_call_deepseek_json_alias(monkeypatch):
    sentinel = {"ok": True}

    def fake(prompt, temperature=0.0):
        return sentinel

    monkeypatch.setattr(llm_module, "call_llm_json", fake)
    assert llm_module.call_deepseek_json("p") is sentinel
