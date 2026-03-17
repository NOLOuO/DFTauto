from __future__ import annotations

from copy import deepcopy
from typing import Any


PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "moonshot": {
        "label": "Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "allowed_hosts": ["api.moonshot.cn"],
        "api_key_env": "MOONSHOT_API_KEY",
        "max_tokens": 5000,
        "timeout_seconds": 180,
        "thinking_enabled": True,
        "thinking_instruction": "请先进行充分的内部分析、逐段核对和多步推理，再输出最终 Markdown，但不要展示思考过程。",
        "supports_temperature": True,
        "default_temperature": 0.2,
        "supports_top_p": False,
        "default_top_p": None,
        "models": [
            {
                "id": "kimi2.5",
                "label": "Kimi 2.5",
                "api_model": "kimi-k2.5",
                "note": "Moonshot Kimi 2.5 模型。",
                "extra_body": {},
                "request_overrides": {},
            }
        ],
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "allowed_hosts": ["api.deepseek.com"],
        "api_key_env": "DEEPSEEK_API_KEY",
        "max_tokens": 5000,
        "timeout_seconds": 180,
        "thinking_enabled": True,
        "thinking_instruction": "请先进行充分的内部分析、逐段核对和多步推理，再输出最终 Markdown，但不要展示思考过程。",
        "supports_temperature": False,
        "default_temperature": None,
        "supports_top_p": False,
        "default_top_p": None,
        "models": [
            {
                "id": "deepseek-reasoner",
                "label": "deepseek-reasoner",
                "api_model": "deepseek-reasoner",
                "note": "官方推理模型。",
                "extra_body": {},
                "request_overrides": {},
            }
        ],
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimaxi.com/v1",
        "allowed_hosts": ["api.minimaxi.com"],
        "api_key_env": "MINIMAX_API_KEY",
        "max_tokens": 5000,
        "timeout_seconds": 180,
        "thinking_enabled": True,
        "thinking_instruction": "请先进行充分的内部分析、逐段核对和多步推理，再输出最终 Markdown，但不要展示思考过程。",
        "supports_temperature": True,
        "default_temperature": 1.0,
        "supports_top_p": False,
        "default_top_p": None,
        "models": [
            {
                "id": "minimax2.5",
                "label": "MiniMax 2.5",
                "api_model": "MiniMax-M2.5",
                "note": "官方 M2.5 模型。",
                "extra_body": {"reasoning_split": True},
                "request_overrides": {},
            },
            {
                "id": "minimax2.1",
                "label": "MiniMax 2.1",
                "api_model": "MiniMax-M2.1",
                "note": "官方 M2.1 模型。",
                "extra_body": {"reasoning_split": True},
                "request_overrides": {},
            },
        ],
    },
    "bailian": {
        "label": "阿里百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "allowed_hosts": ["dashscope.aliyuncs.com"],
        "api_key_env": "DASHSCOPE_API_KEY",
        "max_tokens": 5000,
        "timeout_seconds": 180,
        "thinking_enabled": True,
        "thinking_instruction": "请先进行充分的内部分析、逐段核对和多步推理，再输出最终 Markdown，但不要展示思考过程。",
        "supports_temperature": True,
        "default_temperature": 0.2,
        "supports_top_p": True,
        "default_top_p": 0.95,
        "models": [
            {
                "id": "qwen3.5-plus",
                "label": "Qwen 3.5 Plus",
                "api_model": "qwen-plus",
                "note": "界面展示为 Qwen 3.5 Plus，当前内部映射到百炼兼容模式常用模型名 qwen-plus，并开启 thinking。",
                "extra_body": {"enable_thinking": True},
                "request_overrides": {},
            }
        ],
    },
}


def list_provider_ids() -> list[str]:
    return list(PROVIDER_PRESETS.keys())


def get_provider(provider_id: str) -> dict[str, Any]:
    if provider_id not in PROVIDER_PRESETS:
        raise KeyError(f"未知 provider: {provider_id}")
    return deepcopy(PROVIDER_PRESETS[provider_id])


def list_models(provider_id: str) -> list[dict[str, Any]]:
    provider = get_provider(provider_id)
    return provider.get("models", [])


def default_model_id(provider_id: str) -> str:
    models = list_models(provider_id)
    if not models:
        raise KeyError(f"provider={provider_id} 未配置可用模型")
    return str(models[0]["id"])


def build_provider_model_config(provider_id: str, model_id: str | None = None) -> dict[str, Any]:
    provider = get_provider(provider_id)
    models = provider.pop("models", [])
    resolved_model_id = model_id or (models[0]["id"] if models else "")
    selected = None
    for item in models:
        if item["id"] == resolved_model_id:
            selected = deepcopy(item)
            break
    if selected is None:
        raise KeyError(f"provider={provider_id} 下不存在 model={resolved_model_id}")

    config = deepcopy(provider)
    config["provider_id"] = provider_id
    config["model_id"] = selected["id"]
    config["model_label"] = selected["label"]
    config["model"] = selected["api_model"]
    config["model_note"] = selected.get("note", "")
    config["extra_body"] = selected.get("extra_body", {})
    config["request_overrides"] = selected.get("request_overrides", {})
    return config
